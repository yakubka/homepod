"""Week 7 Task 4: the wake -> VAD -> STT pipeline (speech/pipeline.py).

SOFTWARE-VALIDATED with a scripted wake detector (a real WakeWordDetector subclass, so the real cooldown and
end_sample semantics apply), an energy-based fake VAD tied to the actual audio, and a fake STT. Nothing here says
anything about wake, VAD or recognition accuracy, a microphone or a Raspberry Pi.

Conventions (same as the Task 1 tests): 1000 Hz, 10-sample VAD frames, the make_config() test configuration
(confirm 30, end silence 40, onset padding 20, pre-roll 100, no-speech timeout 200, min 50, max 500). The scripted
wake detector works on 40-sample chunks: chunk i covers [40 i, 40 i + 40) since the detector's last reset.
"""

import dataclasses
import logging
import re

import numpy as np
import pytest

from audio.base import AudioSource
from audio.file_source import EndOfAudio
from audio.utterance import AudioContinuityError, SegmentOutcome, UtteranceSegmenter
from audio.vad import VadEngine
from speech import pipeline as pl
from speech.pipeline import (
    PIPELINE_VALIDATION_LABEL, PipelineClockError, PipelineConfigError, PipelineInputError, PipelineResult,
    PipelineStateError, PipelineStatus, VoicePipeline, strip_wake_prefix,
)
from speech.stt import SttEngine, SttInferenceFailed, SttInputError, SttStatus
from tests.voice_synthetic import FRAME, SR, make_config
from wakeword.detector import WakeWordDetector

CHUNK = 40
S = PipelineStatus


# -- doubles ---------------------------------------------------------------------------------------

class ScriptedWake(WakeWordDetector):
    """A real WakeWordDetector (real threshold / cooldown / end_sample logic) with scripted scores:
    it fires on the chunks whose index since the last reset is in `fire`."""

    def __init__(self, fire=(8,), cooldown_s=0.0):
        self.fire = set(fire)
        self.backend_resets = 0
        super().__init__(threshold=0.5, cooldown_s=cooldown_s)

    sample_rate = SR
    chunk_samples = CHUNK

    def _score_chunk(self, chunk):
        return {"hey_jarvis": 0.9 if self._chunks_processed in self.fire else 0.0}

    def _reset_backend(self):
        self.backend_resets += 1


class LyingWake(ScriptedWake):
    """A broken detector: it reports end_sample shifted by `shift` (the processed-sample count stays honest)."""

    def __init__(self, shift, **kw):
        self.shift = shift
        super().__init__(**kw)

    def process(self, chunk):
        d = super().process(chunk)
        return None if d is None else dataclasses.replace(d, end_sample=d.end_sample + self.shift)


class EnergyVad(VadEngine):
    """Speech score tied to the audio itself: 0.9 when every sample of the frame is loud, else 0.0."""

    sample_rate = SR
    frame_samples = FRAME

    def __init__(self):
        self.reset_calls = 0
        self.fail_after = None
        self.frames = 0

    def _score_frame(self, frame):
        self.frames += 1
        if self.fail_after is not None and self.frames > self.fail_after:
            raise RuntimeError("scripted VAD failure")
        return 0.9 if int(frame.min()) >= 1000 else 0.0

    def reset(self):
        self.reset_calls += 1


class FakeStt(SttEngine):
    """Counts calls; `reply` is a str, or a callable(call_number, samples) -> str; `error` is raised inside."""

    sample_rate = SR
    engine_id = "fake-stt"
    model_id = "fake-model"

    def __init__(self, reply="hey jarvis turn on the lights", error=None):
        super().__init__()
        self.reply, self.error = reply, error
        self.calls = []

    def _transcribe(self, samples):
        self.calls.append(np.array(samples))
        if self.error is not None:
            raise self.error
        return self.reply(len(self.calls), samples) if callable(self.reply) else self.reply


class BlockSource(AudioSource):
    """A finite source of (n, 1) int16 blocks, like WavFileSource."""

    def __init__(self, x, block=64, rate=SR, channels=1):
        self._x, self._block, self._rate, self._channels = x, block, rate, channels
        self.pos, self.started, self.stopped = 0, False, False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def read_frame(self, timeout=1.0):
        if self.pos >= self._x.size:
            raise EndOfAudio("end")
        out = self._x[self.pos:self.pos + self._block].reshape(-1, 1).copy()
        self.pos += out.shape[0]
        return out

    @property
    def sample_rate(self):
        return self._rate

    @property
    def channels(self):
        return self._channels


class _Logs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _Logs()
    pl.logger.addHandler(handler)          # the shared homepod_ai logger: speech.stt logs through it as well
    yield handler.lines
    pl.logger.removeHandler(handler)


def stream(total, speech=()):
    """Silence with loud, index-coded regions: sample value 2000 + (index % 1000) inside a region."""
    x = np.zeros(total, np.int16)
    for a, b in speech:
        x[a:b] = (2000 + np.arange(a, b) % 1000).astype(np.int16)
    return x


class Rig:
    def __init__(self, fire=(8,), reply="hey jarvis turn on the lights", error=None, cooldown_s=0.0, detector=None, **cfg):
        self.detector = detector or ScriptedWake(fire, cooldown_s)
        self.vad = EnergyVad()
        self.stt = FakeStt(reply, error)
        self.segmenter = UtteranceSegmenter(make_config(**cfg), self.vad)
        self.pipeline = VoicePipeline(self.detector, self.segmenter, self.stt)

    def run(self, x, block=40, finish=True):
        results = []
        for lo in range(0, x.size, block):
            results += self.pipeline.feed(x[lo:lo + block])
        if finish:
            results += self.pipeline.finish()
        return results


def one(rig, x, **kw):
    results = rig.run(x, **kw)
    assert len(results) == 1, [r.status for r in results]
    return results[0]


# =========================================================================================
# A. the happy path, pre-roll replay and continuous speech
# =========================================================================================

def test_wake_inside_a_continuous_utterance_replays_the_pre_roll_and_returns_the_whole_command():
    """Speech starts BEFORE the wake detection (continuous 'hey jarvis turn on the lights', no pause)."""
    x = stream(1000, [(300, 600)])
    rig = Rig(fire=(8,))                                   # chunk 8 ends at sample 360
    r = one(rig, x)
    assert r.status is S.TRANSCRIBED and r.segment_outcome is SegmentOutcome.COMPLETE
    assert r.normalized_text == "turn on the lights" and r.wake_prefix_removed is True
    assert (r.activation_sample, r.detector_end_sample, r.activation_clamped) == (360, 360, False)
    assert (r.segment_start_sample, r.segment_end_sample) == (280, 600)       # onset 300 - padding 20 .. last speech frame
    assert r.segment_start_sample < r.activation_sample                        # the pre-roll was replayed
    (seg_pcm,) = rig.stt.calls
    assert np.array_equal(seg_pcm, x[280:600])                                 # master indices == stream indices
    assert r.audio_duration_s == pytest.approx(0.32) and len(rig.stt.calls) == 1
    assert rig.vad.reset_calls == 1                                            # engine reset once per activation


@pytest.mark.parametrize("block", [1, 7, 30, 40, 64, 333, 1000])
def test_the_result_does_not_depend_on_how_the_caller_chops_the_stream(block):
    x = stream(1000, [(300, 600)])
    r = one(Rig(fire=(8,)), x, block=block)
    assert (r.status, r.activation_sample, r.segment_start_sample, r.segment_end_sample) == (S.TRANSCRIBED, 360, 280, 600)


def test_the_n_by_1_audio_source_layout_is_accepted():
    rig = Rig()
    rig.pipeline.feed(np.zeros((80, 1), np.int16))
    assert rig.pipeline.master_samples == 80


def test_speech_that_begins_after_the_wake_is_captured_without_any_discard_or_silence_hunt():
    x = stream(1000, [(420, 700)])
    r = one(Rig(fire=(8,)), x)                             # wake at 360, speech starts 60 samples later
    assert r.status is S.TRANSCRIBED and (r.segment_start_sample, r.segment_end_sample) == (400, 700)


# =========================================================================================
# B. old speech and the activation boundary
# =========================================================================================

def test_old_speech_inside_the_pre_roll_is_discarded_and_the_later_command_is_returned():
    x = stream(1200, [(380, 440), (560, 800)])            # old words, a pause, then the command
    rig = Rig(fire=(11,))                                  # wake at 480
    r = one(rig, x)
    assert r.status is S.TRANSCRIBED
    assert (r.segment_start_sample, r.segment_end_sample) == (540, 800)       # the old region is not in the segment
    assert np.array_equal(rig.stt.calls[0], x[540:800])


@pytest.mark.parametrize("speech_end,kept", [(470, False), (480, False), (490, True), (500, True)])
def test_the_activation_boundary_is_exact(speech_end, kept):
    """A region ending AT the activation sample is old; one ending a frame later is still being spoken."""
    x = stream(1000, [(400, speech_end)])
    rig = Rig(fire=(11,))                                  # wake at 480
    r = one(rig, x)
    if kept:
        assert r.status is S.TRANSCRIBED and r.segment_end_sample == speech_end
    else:
        assert r.status is S.NO_SPEECH and r.segment_outcome is SegmentOutcome.NO_SPEECH_TIMEOUT
        assert rig.stt.calls == []


# =========================================================================================
# C. the master sample clock and the wake detector's own clock
# =========================================================================================

def test_a_detector_created_at_master_zero_maps_end_sample_to_itself():
    rig = Rig(fire=(8,))
    assert rig.pipeline.detector_origin == 0
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.activation_sample == r.detector_end_sample == 360


@pytest.mark.parametrize("reset_at", [40, 100, 110])
def test_a_detector_reset_at_a_non_zero_master_index_is_mapped_by_its_origin(reset_at):
    """The detector restarts its clock at the reset; the pipeline must add the reset position back."""
    x = stream(1200, [(300, 600)])
    rig = Rig(fire=(3,))                                   # chunk 3 ends at ITS sample 160 (never reached before the reset)
    results = []
    results += rig.pipeline.feed(x[:reset_at])
    rig.pipeline.reset_wake_detector()
    assert rig.pipeline.detector_origin == reset_at and rig.pipeline.master_samples == reset_at
    results += rig.pipeline.feed(x[reset_at:])
    results += rig.pipeline.finish()
    (r,) = results
    assert r.detector_end_sample == 160 and r.activation_sample == reset_at + 160
    assert r.status is S.TRANSCRIBED and np.array_equal(rig.stt.calls[0], x[r.segment_start_sample:r.segment_end_sample])


def test_several_detector_sessions_and_several_wakes_in_one_stream_each_map_correctly():
    x = stream(3000, [(300, 500), (1500, 1700), (2450, 2650)])
    rig = Rig(fire=(8,), cooldown_s=0.0)                   # chunk 8 of every detector session
    results = []
    results += rig.pipeline.feed(x[:1000])                 # session 1 origin 0: wake at 360
    rig.pipeline.reset_wake_detector()                     # origin 1000: wake at 1000 + 360
    results += rig.pipeline.feed(x[1000:2000])
    rig.pipeline.reset_wake_detector()                     # origin 2000: wake at 2000 + 360
    results += rig.pipeline.feed(x[2000:])
    results += rig.pipeline.finish()
    assert [r.activation_sample for r in results] == [360, 1360, 2360]
    assert [r.status for r in results] == [S.TRANSCRIBED] * 3 and rig.pipeline.detector_origin == 2000
    assert rig.vad.reset_calls == 3 and len(rig.stt.calls) == 3
    for r, (a, b) in zip(results, [(300, 500), (1500, 1700), (2450, 2650)]):
        assert r.segment_start_sample == a - 20 and r.segment_end_sample == b


def test_restart_stream_puts_everything_back_to_master_zero_and_repeats_identically():
    x = stream(1000, [(300, 600)])
    rig = Rig(fire=(8,))
    first = one(rig, x)
    rig.pipeline.restart_stream()
    assert rig.pipeline.master_samples == 0 and rig.pipeline.detector_origin == 0 and rig.pipeline.interactions == 0
    second = one(rig, x)
    assert (first.activation_sample, first.segment_start_sample, first.segment_end_sample) == \
           (second.activation_sample, second.segment_start_sample, second.segment_end_sample)


def test_the_detector_is_reset_at_construction_even_if_it_was_used_before():
    det = ScriptedWake()
    det.process(np.zeros(CHUNK, np.int16))
    assert det.samples_processed == CHUNK
    Rig(detector=det)
    assert det.samples_processed == 0


def test_the_detector_being_touched_behind_the_pipelines_back_is_a_clock_error():
    rig = Rig()
    rig.pipeline.feed(stream(80))
    rig.detector.process(np.zeros(CHUNK, np.int16))        # someone else feeds the detector
    with pytest.raises(PipelineClockError):
        rig.pipeline.feed(stream(80))
    rig2 = Rig()
    rig2.pipeline.feed(stream(80))
    rig2.detector.reset()                                  # ... or resets it without telling the pipeline
    with pytest.raises(PipelineClockError):
        rig2.pipeline.feed(stream(80))


def test_the_pre_roll_buffer_holds_pre_roll_plus_one_chunk():
    rig = Rig()
    assert rig.pipeline._buffer.capacity == 100 + CHUNK


# =========================================================================================
# D. finite files: padded EOF detections
# =========================================================================================

@pytest.mark.parametrize("tail", [1, 2, 20, 38, 39])
def test_a_wake_on_the_zero_padded_tail_chunk_is_clamped_to_the_real_pcm(tail):
    total = 25 * CHUNK + tail                              # 25 full chunks and a short tail
    x = stream(total, [(800, 25 * CHUNK)])
    rig = Rig(fire=(25,))                                  # only the padded tail chunk fires
    r = one(rig, x)
    assert r.detector_end_sample == 26 * CHUNK > total     # the detector counted the padding
    assert r.activation_clamped is True and r.activation_sample == total
    assert rig.pipeline.master_samples == total            # the pad was never added to the master clock
    # frozen policy: speech that ends at or before the activation is old, and nothing can follow the last real sample
    assert r.status is S.NO_SPEECH and r.segment_outcome is SegmentOutcome.NO_SPEECH_STREAM_ENDED
    assert rig.stt.calls == [] and rig.vad.frames * FRAME <= total      # no padded frame was ever scored


@pytest.mark.parametrize("tail", [1, 20, 39])
def test_the_padded_tail_after_an_early_wake_is_never_given_to_the_segmenter(tail):
    total = 25 * CHUNK + tail
    x = stream(total, [(900, 25 * CHUNK)])
    rig = Rig(fire=(22,))                                  # wake at 920, speech runs on to the last full chunk
    r = one(rig, x)
    assert r.activation_clamped is False and r.activation_sample == 920
    assert r.status is S.STREAM_ENDED and r.segment_end_sample <= total
    assert np.array_equal(rig.stt.calls[0], x[r.segment_start_sample:r.segment_end_sample])
    assert rig.vad.frames * FRAME <= total                 # equality with the real stream above also proves no pad zeros


def test_a_wake_on_the_last_full_chunk_needs_no_clamp():
    x = stream(1000, [(900, 1000)])
    r = one(Rig(fire=(24,)), x)                            # the chunk ending exactly at EOF: nothing can follow it
    assert r.activation_clamped is False and r.activation_sample == r.detector_end_sample == 1000
    assert r.status is S.NO_SPEECH and r.segment_outcome is SegmentOutcome.NO_SPEECH_STREAM_ENDED


def test_a_mapped_activation_beyond_the_delivered_pcm_outside_the_padding_path_is_invalid_not_clamped():
    rig = Rig(detector=LyingWake(shift=500, fire=(8,)))
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.status is S.INVALID_ACTIVATION and r.error_class == "ActivationOutsideDeliveredPcm"
    assert r.activation_sample == 860 and r.detector_end_sample == 860 and r.activation_clamped is False
    assert rig.stt.calls == [] and r.segment_start_sample is None and r.normalized_text == ""


def test_a_mapped_activation_older_than_the_pre_roll_buffer_is_invalid():
    rig = Rig(detector=LyingWake(shift=-300, fire=(20,)))  # real end 840, reported 540; the buffer only holds 700..840
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.status is S.INVALID_ACTIVATION and rig.stt.calls == []


@pytest.mark.parametrize("shift,valid", [(0, True), (1, False), (-140, True), (-141, False)])
def test_the_delivered_pcm_boundaries_of_a_valid_activation_are_exact(shift, valid):
    """The buffer holds [860, 1000): an activation AT either end is covered by the replay, one sample beyond is not."""
    rig = Rig(detector=LyingWake(shift=shift, fire=(24,)))
    r = one(rig, stream(1000))
    assert r.activation_sample == 1000 + shift
    assert (r.status is S.INVALID_ACTIVATION) is (not valid)
    assert (r.status is S.NO_SPEECH) is valid and rig.stt.calls == []


@pytest.mark.parametrize("block", [40, 7, 333, 1000])
def test_speech_at_the_edge_of_the_pre_roll_window_is_clipped_to_exactly_the_pre_roll_for_any_block_size(block):
    """Speech starting 90 samples before the wake: padding would reach 110 back, the pre-roll allows 100."""
    x = stream(1000, [(270, 600)])
    rig = Rig(fire=(8,))                                   # wake at 360, pre-roll window starts at 260
    r = one(rig, x, block=block)
    assert (r.segment_start_sample, r.segment_end_sample) == (260, 600)
    assert np.array_equal(rig.stt.calls[0], x[260:600])


def test_resetting_the_detector_also_drops_the_samples_it_had_not_been_given_yet():
    rig = Rig(fire=())
    rig.pipeline.feed(stream(110))                         # 2 full chunks fed, 30 samples pending
    assert rig.detector.chunks_processed == 2
    rig.pipeline.reset_wake_detector()
    rig.pipeline.feed(stream(39))                          # 39 < 40: must NOT complete a chunk with the old 30 samples
    assert rig.detector.chunks_processed == 0 and rig.pipeline.detector_origin == 110
    rig.pipeline.feed(stream(1))
    assert rig.detector.chunks_processed == 1


def test_the_pipeline_recovers_after_an_invalid_activation():
    rig = Rig(detector=LyingWake(shift=500, fire=(8, 20)))
    first = rig.run(stream(2000, [(300, 600)]), finish=False)
    assert [r.status for r in first] == [S.INVALID_ACTIVATION, S.INVALID_ACTIVATION] and not rig.pipeline.is_capturing


# =========================================================================================
# E. segmentation outcomes, STT call rule
# =========================================================================================

def test_no_speech_after_the_wake_times_out_and_stt_is_not_called():
    rig = Rig()
    r = one(rig, stream(1000))
    assert r.status is S.NO_SPEECH and r.segment_outcome is SegmentOutcome.NO_SPEECH_TIMEOUT
    assert rig.stt.calls == [] and r.stt_engine_id is None and r.raw_stt_status is None and r.audio_duration_s is None


def test_the_stream_ending_before_any_speech_is_no_speech_and_stt_is_not_called():
    rig = Rig(fire=(8,))
    r = one(rig, stream(400))                              # wake at 360, only 40 more samples
    assert r.status is S.NO_SPEECH and r.segment_outcome is SegmentOutcome.NO_SPEECH_STREAM_ENDED and rig.stt.calls == []


def test_a_confirmed_burst_shorter_than_the_minimum_is_too_short_and_the_pipeline_goes_back_to_idle():
    rig = Rig()
    x = stream(1500, [(420, 460), (900, 1200)])            # a 40-sample burst after the wake, a real command later
    r = one(rig, x)
    assert r.status is S.TOO_SHORT and r.segment_outcome is SegmentOutcome.TOO_SHORT
    assert rig.stt.calls == [] and r.segment_start_sample is None and not rig.pipeline.is_capturing
    # documented UX limitation: the later command was NOT captured (no automatic re-listening)


def test_a_segment_cut_at_the_maximum_is_transcribed_but_flagged_max_duration():
    rig = Rig()
    r = one(rig, stream(1500, [(300, 1200)]))
    assert r.status is S.MAX_DURATION and r.segment_outcome is SegmentOutcome.MAX_DURATION
    assert r.normalized_text == "turn on the lights" and r.segment_end_sample - r.segment_start_sample <= 500
    assert not S.MAX_DURATION is S.TRANSCRIBED and S.MAX_DURATION.carries_text and len(rig.stt.calls) == 1


def test_the_stream_ending_mid_speech_is_transcribed_but_flagged_stream_ended():
    rig = Rig()
    r = one(rig, stream(600, [(300, 600)]))
    assert r.status is S.STREAM_ENDED and r.normalized_text == "turn on the lights" and len(rig.stt.calls) == 1


def test_stt_is_called_exactly_once_per_completed_segment():
    rig = Rig(fire=(8, 40))
    results = rig.run(stream(3000, [(300, 500), (1700, 1900)]))
    assert [r.status for r in results] == [S.TRANSCRIBED, S.TRANSCRIBED] and len(rig.stt.calls) == 2


def test_a_second_wake_during_a_capture_is_ignored_and_counted_and_the_vad_is_reset_once():
    rig = Rig(fire=(8, 9))                                 # wake, then 40 samples later another
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.status is S.TRANSCRIBED and r.ignored_reactivation_count == 1
    assert (r.activation_sample, r.detector_end_sample) == (360, 360)      # the FIRST wake owns the capture
    assert rig.vad.reset_calls == 1 and len(rig.stt.calls) == 1


def test_the_interaction_counter_counts_every_result():
    rig = Rig(fire=(8, 36))
    results = rig.run(stream(3000, [(300, 500), (1500, 1800)]))
    assert len(results) == 2 and rig.pipeline.interactions == 2
    invalid = Rig(detector=LyingWake(shift=500, fire=(8,)))
    assert len(invalid.run(stream(1000))) == 1 and invalid.pipeline.interactions == 1


def test_a_wake_detected_while_nothing_follows_does_not_produce_a_result_without_a_wake():
    rig = Rig(fire=())
    assert rig.run(stream(2000, [(300, 600)])) == []
    assert rig.stt.calls == [] and rig.pipeline.interactions == 0


# =========================================================================================
# F. STT outcomes
# =========================================================================================

def test_an_empty_decode_is_stt_empty():
    rig = Rig(reply="  ")
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.status is S.STT_EMPTY and r.raw_stt_status is SttStatus.EMPTY_TEXT and r.normalized_text == ""
    assert r.segment_start_sample is not None and r.stt_engine_id == "fake-stt" and len(rig.stt.calls) == 1


def test_a_typed_stt_error_is_stt_failed_with_only_the_class_name_recorded():
    rig = Rig(error=SttInferenceFailed("secret words that were said"))
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.status is S.STT_FAILED and r.error_class == "SttInferenceFailed" and r.raw_stt_status is None
    assert r.normalized_text == "" and "secret" not in repr(r)
    assert len(rig.stt.calls) == 1                         # called once, never retried


def test_a_segment_the_stt_backend_refuses_is_stt_failed_not_a_crash():
    class Short(FakeStt):
        def _check_extra_input(self, samples):
            raise SttInputError("too long for this backend")

    rig = Rig()
    rig.stt = Short()
    rig.pipeline = VoicePipeline(rig.detector, rig.segmenter, rig.stt)
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.status is S.STT_FAILED and r.error_class == "SttInputError"


def test_a_programming_error_in_the_stt_layer_is_not_swallowed_as_a_status():
    class Boom(FakeStt):
        def transcribe_segment(self, segment):
            raise KeyError("bug")

    rig = Rig()
    rig.stt = Boom()
    rig.pipeline = VoicePipeline(rig.detector, rig.segmenter, rig.stt)
    with pytest.raises(KeyError):
        rig.run(stream(1000, [(300, 600)]))
    assert not rig.pipeline.is_capturing                   # and the capture was abandoned, not left half-open


# =========================================================================================
# G. wake-prefix normalisation
# =========================================================================================

@pytest.mark.parametrize("text,expected", [
    ("hey jarvis turn on the lights", ("turn on the lights", True)),
    ("Hey Jarvis turn on the lights", ("turn on the lights", True)),
    ("HEY JARVIS turn on the lights", ("turn on the lights", True)),
    ("Hey, Jarvis, what is the temperature?", ("what is the temperature?", True)),
    ("Hey Jarvis. What is the temperature?", ("What is the temperature?", True)),
    ("hey jarvis: lights on", ("lights on", True)),
    ("hey-jarvis lights on", ("lights on", True)),
    ("...Hey Jarvis! Lights on", ("Lights on", True)),
    ('"Hey Jarvis," lights on', ("lights on", True)),
    ("hey   jarvis   lights", ("lights", True)),
    ("Hey Jarvis", ("", True)),
    ("Hey, Jarvis.", ("", True)),
    ("hey jarvis?!", ("", True)),
    # NOT the wake phrase / not at the leading edge: untouched
    ("tell jarvis to open the door", ("tell jarvis to open the door", False)),
    ("turn on the lights hey jarvis", ("turn on the lights hey jarvis", False)),
    ("what did hey jarvis say", ("what did hey jarvis say", False)),
    ("jarvis hey lights", ("jarvis hey lights", False)),
    ("hey there jarvis lights", ("hey there jarvis lights", False)),
    ("heyjarvis lights", ("heyjarvis lights", False)),
    ("hey jarvisian lights", ("hey jarvisian lights", False)),
    ("hey jarvis's lights", ("hey jarvis's lights", False)),
    ("they jarvis lights", ("they jarvis lights", False)),
    ("hey", ("hey", False)),
    ("jarvis", ("jarvis", False)),
    ("", ("", False)),
])
def test_wake_prefix_removal_is_narrow_and_leading_only(text, expected):
    assert strip_wake_prefix(text) == expected


def test_only_one_leading_phrase_is_removed_and_an_internal_repeat_survives():
    assert strip_wake_prefix("hey jarvis hey jarvis lights") == ("hey jarvis lights", True)


def test_the_remaining_text_is_not_rewritten():
    assert strip_wake_prefix("Hey Jarvis, Turn  ON the LiGhTs!") == ("Turn  ON the LiGhTs!", True)


def test_configured_phrases_are_used_longest_first():
    phrases = ("jarvis", "hey jarvis")
    assert strip_wake_prefix("hey jarvis lights", phrases) == ("lights", True)
    assert strip_wake_prefix("jarvis lights", phrases) == ("lights", True)
    assert strip_wake_prefix("okay jarvis lights", phrases) == ("okay jarvis lights", False)


@pytest.mark.parametrize("bad", [(), "hey jarvis", ("",), ("hey jarvis!",), (None,), ("hey_jarvis",)])
def test_bad_wake_phrases_are_rejected(bad):
    with pytest.raises(PipelineConfigError):
        strip_wake_prefix("hey jarvis lights", bad)


def test_wake_only_is_an_explicit_status_and_invents_no_command():
    rig = Rig(reply="Hey, Jarvis.")
    r = one(rig, stream(1000, [(300, 600)]))
    assert r.status is S.WAKE_ONLY and r.normalized_text == "" and r.wake_prefix_removed is True
    assert r.raw_stt_status is SttStatus.OK and not r.status.carries_text and len(rig.stt.calls) == 1


def test_a_transcript_without_the_wake_phrase_is_passed_through_with_prefix_removed_false():
    r = one(Rig(reply="turn on the lights"), stream(1000, [(300, 600)]))
    assert r.status is S.TRANSCRIBED and r.normalized_text == "turn on the lights" and r.wake_prefix_removed is False


def test_an_internal_jarvis_survives_the_pipeline():
    r = one(Rig(reply="Hey Jarvis tell Jarvis to wait"), stream(1000, [(300, 600)]))
    assert r.normalized_text == "tell Jarvis to wait"


def test_the_pipeline_uses_its_configured_wake_phrases():
    rig = Rig(reply="okay vesta lights on")
    rig.pipeline = VoicePipeline(rig.detector, rig.segmenter, rig.stt, wake_phrases=("okay vesta",))
    assert one(rig, stream(1000, [(300, 600)])).normalized_text == "lights on"


# =========================================================================================
# H. contract: gaps, input errors, wiring, lifecycle
# =========================================================================================

def test_a_gap_or_an_overlap_in_the_supplied_start_index_is_refused_not_joined():
    rig = Rig()
    rig.pipeline.feed(stream(80), 0)
    for bad, word in ((100, "gap"), (60, "overlap")):
        with pytest.raises(AudioContinuityError, match=word):
            rig.pipeline.feed(stream(40), bad)
    assert rig.pipeline.master_samples == 80                # the refused block changed nothing
    rig.pipeline.feed(stream(40), 80)                       # the right index is still accepted
    assert rig.pipeline.master_samples == 120


@pytest.mark.parametrize("bad", [np.zeros(10, np.float32), np.zeros(10, np.int32), np.zeros((2, 5), np.int16),
                                 np.zeros((10, 2), np.int16), np.zeros(0, np.int16), [1, 2, 3], None])
def test_bad_blocks_are_rejected(bad):
    with pytest.raises(PipelineInputError):
        Rig().pipeline.feed(bad)


@pytest.mark.parametrize("start", [-1, 1.5, True, "0"])
def test_a_bad_start_sample_is_rejected(start):
    with pytest.raises(PipelineInputError):
        Rig().pipeline.feed(stream(40), start)


def test_the_callers_block_is_never_modified_or_aliased():
    rig = Rig(fire=(8,))
    x = stream(1000, [(300, 600)])
    before = x.copy()
    block = x[:40].copy()
    rig.pipeline.feed(block)
    block[:] = 7
    rig.run(x[40:], finish=True)
    assert np.array_equal(x, before)


def test_feeding_after_finish_needs_a_restart():
    rig = Rig()
    rig.run(stream(100))
    with pytest.raises(PipelineStateError):
        rig.pipeline.feed(stream(40))
    with pytest.raises(PipelineStateError):
        rig.pipeline.finish()
    with pytest.raises(PipelineStateError):
        rig.pipeline.reset_wake_detector()
    rig.pipeline.restart_stream()
    rig.pipeline.feed(stream(40))


def test_finishing_without_any_wake_returns_nothing():
    assert Rig(fire=()).run(stream(500, [(100, 300)])) == []


def test_wiring_mistakes_are_refused():
    det, vad, stt = ScriptedWake(), EnergyVad(), FakeStt()
    seg = UtteranceSegmenter(make_config(), vad)
    for args in ((None, seg, stt), (det, None, stt), (det, seg, None), (seg, det, stt)):
        with pytest.raises(PipelineConfigError):
            VoicePipeline(*args)

    class OtherRate(FakeStt):
        sample_rate = 16000

    with pytest.raises(PipelineConfigError, match="sample rates differ"):
        VoicePipeline(det, seg, OtherRate())
    with pytest.raises(PipelineConfigError):
        VoicePipeline(det, seg, stt, wake_phrases=())


def test_a_vad_failure_abandons_the_capture_and_the_pipeline_recovers():
    rig = Rig(fire=(8, 36))
    rig.vad.fail_after = 20
    x = stream(3000, [(300, 600), (1500, 1800)])
    with pytest.raises(RuntimeError, match="scripted VAD failure"):
        for lo in range(0, 1000, 40):
            rig.pipeline.feed(x[lo:lo + 40])
    assert not rig.pipeline.is_capturing and 360 <= rig.pipeline.master_samples < 1000
    rig.vad.fail_after = None
    resume = rig.pipeline.master_samples                    # carry on from exactly where the master clock stands
    results = rig.pipeline.feed(x[resume:], resume) + rig.pipeline.finish()
    assert [r.status for r in results] == [S.TRANSCRIBED] and results[0].activation_sample == 36 * CHUNK + CHUNK


# -- run_source ------------------------------------------------------------------------------------

def test_run_source_replays_a_finite_source_and_stops_it():
    rig = Rig(fire=(8,))
    src = BlockSource(stream(1000, [(300, 600)]))
    (r,) = rig.pipeline.run_source(src)
    assert r.status is S.TRANSCRIBED and src.started and src.stopped


def test_run_source_stops_the_source_even_when_the_pipeline_raises():
    rig = Rig(fire=(8,))
    rig.vad.fail_after = 5
    src = BlockSource(stream(1000, [(300, 600)]))
    with pytest.raises(RuntimeError):
        rig.pipeline.run_source(src)
    assert src.stopped


def test_run_source_refuses_a_source_that_does_not_fit_and_a_second_run_restarts_the_clock():
    rig = Rig(fire=(8,))
    with pytest.raises(PipelineConfigError):
        rig.pipeline.run_source(BlockSource(stream(100), rate=16000))
    with pytest.raises(PipelineConfigError):
        rig.pipeline.run_source(BlockSource(stream(100), channels=2))
    x = stream(1000, [(300, 600)])
    a = rig.pipeline.run_source(BlockSource(x))
    b = rig.pipeline.run_source(BlockSource(x))
    assert [r.activation_sample for r in a] == [r.activation_sample for r in b] == [360]


# =========================================================================================
# I. the result contract, validation label, privacy
# =========================================================================================

def test_the_result_is_frozen_labelled_software_validated_and_holds_no_audio():
    r = one(Rig(), stream(1000, [(300, 600)]))
    assert r.validation_label == PIPELINE_VALIDATION_LABEL == "SOFTWARE-VALIDATED" and r.wake_detected is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = S.WAKE_ONLY
    for f in dataclasses.fields(r):
        assert not isinstance(getattr(r, f.name), (np.ndarray, bytes, bytearray, list)), f.name


def test_a_result_can_never_claim_hardware_validation():
    r = one(Rig(), stream(1000, [(300, 600)]))
    for label in ("HARDWARE-VALIDATED", "SIMULATED", "", None):
        with pytest.raises(PipelineInputError):
            dataclasses.replace(r, validation_label=label)


def test_result_invariants_are_enforced():
    r = one(Rig(), stream(1000, [(300, 600)]))
    for change in (dict(normalized_text=""), dict(status=S.NO_SPEECH), dict(segment_end_sample=None), dict(stt_engine_id=None),
                   dict(error_class="X"), dict(status=S.STT_FAILED, normalized_text=""), dict(status=S.WAKE_ONLY)):
        with pytest.raises(PipelineInputError):
            dataclasses.replace(r, **change)


def test_result_fields_cover_the_requested_contract():
    r = one(Rig(), stream(1000, [(300, 600)]))
    names = {f.name for f in dataclasses.fields(r)}
    assert {"status", "raw_stt_status", "normalized_text", "segment_outcome", "activation_sample", "segment_start_sample",
            "segment_end_sample", "audio_duration_s", "stt_engine_id", "model_id", "wake_detected", "wake_prefix_removed",
            "timings", "validation_label"} <= names
    assert r.raw_stt_status is SttStatus.OK and r.stt_engine_id == "fake-stt" and r.model_id == "fake-model"
    assert r.timings.decision_latency_s == pytest.approx((r.decided_at_sample - r.activation_sample) / SR)
    assert r.timings.stt_inference_ms is not None and r.timings.stt_inference_ms >= 0 and r.timings.post_decision_ms >= 0


def test_the_transcript_is_hidden_from_the_result_repr():
    r = one(Rig(reply="hey jarvis zebracrossing"), stream(1000, [(300, 600)]))
    assert r.normalized_text == "zebracrossing" and "zebracrossing" not in repr(r) and "zebracrossing" not in str(r)


def test_logs_carry_structure_only_never_the_transcript_or_pcm(logs):
    secret = "zebracrossing"
    x = stream(1000, [(300, 600)])
    rig = Rig(reply=f"hey jarvis {secret}")
    one(rig, x)
    failing = Rig(error=SttInferenceFailed(f"decoder said {secret}"))
    one(failing, x)
    joined = "\n".join(logs)
    assert "event=pipeline_wake" in joined and "event=pipeline_result" in joined and "status=TRANSCRIBED" in joined
    assert "status=STT_FAILED" in joined and "error=SttInferenceFailed" in joined and "prefix_removed=True" in joined
    assert secret not in joined and "hey jarvis" not in joined.lower().replace("keyword=hey_jarvis", "")
    numbers = set(re.findall(r"(?<![\w.])\d+(?![\w.])", joined))
    assert not numbers & {str(v) for v in np.unique(x[300:600])} and "[" not in joined     # no PCM sample value in any log


# =========================================================================================
# J. the segmenter / wake integration stays inside the frozen contracts
# =========================================================================================

def test_the_pipeline_is_the_only_caller_that_resets_the_vad_once_per_activation():
    rig = Rig(fire=(8, 30))
    rig.run(stream(2500, [(300, 500), (1300, 1500)]))
    assert rig.vad.reset_calls == 2


def test_the_pipeline_module_calls_no_intent_answering_tts_or_playback_code():
    import re
    from pathlib import Path

    source = Path(pl.__file__).read_text(encoding="utf-8")
    imports = "\n".join(re.findall(r"^(?:from|import) .*$", source, re.M))
    for banned in ("assistant", "playback", "tts", "intent", "answering", "output_arbiter", "sounddevice", "requests", "socket"):
        assert banned not in imports, banned
