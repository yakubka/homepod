"""TEST DOUBLES for the voice-assistant runtime tests (assistant/runtime.py).

Fake TTS and playback that can be held on Events, a scripted STT, and a builder for PipelineResult values. Nothing here is a real
voice, a real device or a real recognition. The audio scale of the pipeline tests applies: 1000 Hz, 40-sample wake chunks, the
make_config() segmenter; speech samples carry their stream index.
"""

import threading

import numpy as np

from assistant.answering import AnswerEngine, DeterministicAnswerEngine
from assistant.runtime import VoiceAssistantRuntime
from assistant.state import AssistantStateManager
from audio.playback import PlaybackEngine, PlaybackResult, PlaybackStatus
from audio.utterance import SegmentOutcome, UtteranceSegmenter
from speech.pipeline import PIPELINE_VALIDATION_LABEL, PipelineResult, PipelineStatus, PipelineTimings
from speech.stt import SttEngine, SttStatus
from speech.tts import TtsEngine
from tests.spoken_output_fakes import tone
from tests.stage_b_support import EnergyVad, GUARD_S, ScriptedWake, make_config, stream
from tests.voice_synthetic import SR

RATE = 22050
S = PipelineStatus


class RuntimeTts(TtsEngine):
    """Records the spoken texts (so a test can check what was synthesized), can be gated inside synthesis or made to fail."""

    sample_rate = RATE
    engine_id = "fake-tts"
    voice_id = "fake-voice"
    language = "en-US"

    def __init__(self, gate=None, error=None):
        super().__init__()
        self.gate, self.error = gate, error
        self.texts = []
        self.entered = threading.Event()

    def _synthesize(self, text):
        self.texts.append(text)
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(GUARD_S), "TTS gate never released"
        if self.error is not None:
            raise self.error
        return tone(RATE // 10 + 40 * len(text))


class RuntimePlayback(PlaybackEngine):
    """A fake playback engine WITH the first-buffer signal, reported where a real engine would: once the stream is open and the first
    samples are in the device buffer, never after a stop or a failure that came first. `before_first` holds it BEFORE that point,
    `hold` keeps it playing afterwards until `release`."""

    def __init__(self, hold=False, before_first=None, fail_before=None, bug=None):
        self.hold, self.before_first, self.fail_before, self.bug = hold, before_first, fail_before, bug
        self.calls = self.active = self.max_concurrent = 0
        self.lock = threading.Lock()
        self.called, self.first_sent, self.release = threading.Event(), threading.Event(), threading.Event()
        self.stopped = False
        self._listener = None
        self._playing = False

    @property
    def supports_first_buffer_listener(self):
        return True

    def set_first_buffer_listener(self, listener):
        self._listener = listener

    def play(self, audio):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
        self._playing = True
        self.stopped = False
        try:
            self.called.set()
            if self.before_first is not None:
                assert self.before_first.wait(GUARD_S), "before_first gate never released"
            if self.bug:
                raise self.bug
            if self.stopped:
                return PlaybackResult(PlaybackStatus.STOPPED, audio.duration_s, 10.0, None, None, 10.001, None, None, None, 0)
            if self.fail_before:
                raise self.fail_before
            if self._listener is not None:
                self._listener()
            self.first_sent.set()
            if self.hold:
                assert self.release.wait(GUARD_S), "playback never released"
            status = PlaybackStatus.STOPPED if self.stopped else PlaybackStatus.COMPLETED
            return PlaybackResult(status, audio.duration_s, 10.0, 10.01, 10.02, 10.5, "fake-device", "fake-api", 0.01, 0)
        finally:
            self._playing = False
            with self.lock:
                self.active -= 1

    def stop(self):
        self.stopped = True
        self.release.set()
        if self.before_first is not None:
            self.before_first.set()
        return True

    @property
    def is_playing(self):
        return self._playing


class ScriptedStt(SttEngine):
    """Returns the scripted replies in order (the last one repeats); an Exception instance in the script is raised."""

    sample_rate = SR
    engine_id = "fake-stt"
    model_id = "fake-model"

    def __init__(self, *replies):
        super().__init__()
        self.replies = list(replies) or ["hey jarvis what can you do"]
        self.calls = []

    def _transcribe(self, samples):
        self.calls.append(np.array(samples))
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


class SpyEngine(AnswerEngine):
    """The real deterministic engine behind a call counter; `error` (an Exception instance) is raised instead of answering."""

    def __init__(self, error=None):
        self.error, self.calls, self.texts, self._inner = error, 0, [], DeterministicAnswerEngine()

    def _answer(self, text, context):
        self.calls += 1
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return self._inner._answer(text, context)


def make_result(status, text="", outcome=SegmentOutcome.COMPLETE):
    """A well-formed PipelineResult for `status` (built directly, so a runtime test can hand it to handle_result())."""
    has_segment = status in (S.TRANSCRIBED, S.WAKE_ONLY, S.MAX_DURATION, S.STREAM_ENDED, S.STT_EMPTY, S.STT_FAILED)
    raw = None
    if status in (S.TRANSCRIBED, S.MAX_DURATION, S.STREAM_ENDED, S.WAKE_ONLY):
        raw = SttStatus.OK
    elif status is S.STT_EMPTY:
        raw = SttStatus.EMPTY_TEXT
    return PipelineResult(
        status=status, raw_stt_status=raw, normalized_text=text, segment_outcome=outcome if has_segment else None,
        activation_sample=360, detector_end_sample=360, activation_clamped=False,
        segment_start_sample=280 if has_segment else None, segment_end_sample=600 if has_segment else None,
        audio_duration_s=0.32 if has_segment else None, stt_engine_id="fake-stt" if has_segment else None,
        model_id="fake-model" if has_segment else None, wake_detected=True, wake_keyword="hey_jarvis", wake_prefix_removed=bool(text),
        decided_at_sample=640, unscored_samples=0, ignored_reactivation_count=0,
        error_class={S.STT_FAILED: "SttInferenceFailed", S.INVALID_ACTIVATION: "ActivationOutsideDeliveredPcm"}.get(status),
        timings=PipelineTimings(0.28, 1.0 if raw else None, 1.0), validation_label=PIPELINE_VALIDATION_LABEL)


class Rig:
    """A runtime built from fakes: scripted wake (fires at chunk 8 = sample 360), energy VAD, scripted STT, fake TTS and playback."""

    def __init__(self, replies=("hey jarvis what can you do",), fire=(8,), tts=None, playback=None, **runtime_kw):
        self.detector = ScriptedWake(fire)
        self.vad = EnergyVad()
        self.stt = ScriptedStt(*replies)
        self.tts = tts or RuntimeTts()
        self.playback = playback or RuntimePlayback()
        self.state = AssistantStateManager()
        self.events = []
        self.state.subscribe(self.events.append, replay_current=True)
        self.segmenter = UtteranceSegmenter(make_config(), self.vad)
        self.runtime = VoiceAssistantRuntime(detector=self.detector, segmenter=self.segmenter, stt=self.stt, tts=self.tts,
                                             playback=self.playback, state=self.state, **runtime_kw)

    def drive(self, x, block=40, finish=True):
        outcomes = []
        for lo in range(0, x.size, block):
            outcomes += self.runtime.feed(x[lo:lo + block])
        if finish:
            outcomes += self.runtime.finish()
        return outcomes

    def states(self):
        return [(e.state.value, e.reason_code.value) for e in self.events]

    def colors(self):
        return [e.color_semantic.value for e in self.events]


UTTERANCE = stream(1000, [(300, 600)])          # wake at 360, one complete utterance, decided at 640
SILENCE = stream(1000)


__all__ = ["GUARD_S", "RATE", "Rig", "RuntimePlayback", "RuntimeTts", "S", "SILENCE", "ScriptedStt", "SpyEngine", "UTTERANCE",
           "make_result", "stream"]
