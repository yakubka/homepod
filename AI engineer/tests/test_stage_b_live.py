"""Team Leader AI-2 / Stage B: the live-capture harness (scripts/ai2_stage_b_live.py).

SOFTWARE-VALIDATED WITH FAKE INPUT. A scripted block source replaces the microphone, a scripted wake detector, an
energy VAD and a fake STT replace the models. These tests prove what the harness does with the blocks, the positions,
the queue and the pipeline. They prove nothing about a microphone, PortAudio, the ReSpeaker XVF3800, wake / VAD /
STT accuracy or a Raspberry Pi. LIVE MICROPHONE: NOT VALIDATED.

Synchronisation uses Events and a script that the capture worker walks through; the only waits are failure guards.
"""

import json
import logging
import queue
import threading
from pathlib import Path

import numpy as np
import pytest

from audio import microphone as mic_mod
from audio.file_source import EndOfAudio
from audio.microphone import MicBlock, ReSpeakerMicrophone
from config import settings
from scripts import ai2_stage_b_live as sb
from scripts.ai2_stage_b_live import (
    Engines, LiveConfig, ManualActivationDetector, MicrophoneStall, StageBEvidenceError, StageBInputError, StageBLiveHarness,
    StageBModelError, StageBReportError, StageBWorkerError, load_engines, main, validate_report, write_evidence,
)
from speech.pipeline import PipelineStatus as S
from tests.stage_b_support import (
    BLOCK, GUARD_S, Boom, EnergyVad, EnergyVad16, FakeBlockSource, FakeStt, FakeStt16, Hold, ScriptedWake, ScriptedWake16, Timeout,
    engines, make_blocks, make_config, stream,
)
from tests.voice_synthetic import SR

# Every test that EXPECTS a failure runs with this bounded wait (run(duration_s)). A legitimate failure is reported within milliseconds; if
# the failure path ever deadlocks (a worker dies silently, a stalled source is never detected) the wait expires after HANG_S and the
# assertion on the failure report fails, instead of the test waiting for an outer timeout.
HANG_S = 3.0
SECRET = "zebra quartz marmalade"
REPLY = "hey jarvis " + SECRET


class _Logs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    h = _Logs()
    sb.logger.addHandler(h)
    yield h.lines
    sb.logger.removeHandler(h)


@pytest.fixture(autouse=True)
def no_leaked_workers():
    yield
    leaked = [t.name for t in threading.enumerate() if t.name.startswith("stageb-") and t.is_alive()]
    assert not leaked, f"worker threads left running: {leaked}"


def harness(source, eng=None, *, queue_blocks=400, stall=10**6, **kw):
    cfg = LiveConfig(read_timeout_s=0.01, max_consecutive_timeouts=stall, processing_queue_blocks=queue_blocks, join_timeout_s=GUARD_S)
    return StageBLiveHarness(source, eng or engines(), config=cfg, segmenter_config=make_config(), sample_rate=SR, **kw)


X = stream(1000, [(300, 600)])                       # one utterance; the wake fires at sample 360, the segment is [280, 600)
X_BLOCKS = 25
DECISION_BLOCK = 15                                  # block 15 ([600, 640)) completes the end-of-speech decision


def run(blocks_or_script, eng=None, *, source_kw=None, **kw):
    src = FakeBlockSource(blocks_or_script, **(source_kw or {}))
    h = harness(src, eng, **kw)
    return h, src, h.run()


# =========================================================================================
# A. continuous input, ordered positions, wake activation, utterance -> STT
# =========================================================================================

def test_continuous_input_is_processed_in_order_with_exact_continuity():
    eng = engines()
    h, src, rep = run(make_blocks(X), eng)
    c = rep["continuity"]
    assert (c["blocks_captured"], c["blocks_processed"], c["samples_processed"]) == (X_BLOCKS, X_BLOCKS, 1000)
    assert c["exact_continuity"] is True and c["discontinuity_count"] == 0 and c["harness_blocks_dropped"] == 0
    assert c["runs"] == [{"run_index": 0, "first_sample": 0, "end_sample": 1000}]
    assert (rep["completed"], rep["end_reason"], rep["run_error"]) == (True, "SOURCE_ENDED", None)
    assert (src.started, src.stopped) == (1, 1)
    assert h.pipeline.master_samples == 1000


def test_a_wake_activated_utterance_reaches_stt_with_exactly_its_samples():
    eng = engines()
    h, src, rep = run(make_blocks(X), eng)
    (i,) = rep["interactions"]
    assert (i["status"], i["segment_outcome"], i["activation_mode"], i["wake_detected"]) == ("TRANSCRIBED", "COMPLETE", "WAKE", True)
    assert (i["segment_start_sample"], i["segment_end_sample"], i["activation_sample"]) == (280, 600, 360)
    (pcm,) = eng.stt.calls
    assert np.array_equal(pcm, X[280:600])
    assert i["stt_engine"] == "fake-stt" and i["stt_inference_ms"] is not None


def test_speech_without_a_wake_detection_is_never_transcribed():
    eng = engines(fire=())
    h, src, rep = run(make_blocks(X), eng)
    assert rep["interaction_count"] == 0 and eng.stt.calls == []         # the wake path is not bypassed
    assert rep["activation_mode"] == "WAKE"


def test_blocks_are_delivered_to_the_pipeline_in_capture_order():
    seen = []
    blocks = make_blocks(X)
    src = FakeBlockSource(blocks)
    h = harness(src)
    real_feed = h.pipeline.feed

    def spy(samples, *a, **k):
        seen.append(int(samples[0, 0]) if samples.ndim == 2 else int(samples[0]))
        return real_feed(samples, *a, **k)

    h.pipeline.feed = spy
    h.run()
    assert seen == [int(b.samples[0, 0]) for b in blocks]


def test_a_short_final_block_is_counted_exactly():
    x = stream(1020, [(300, 600)])
    h, src, rep = run(make_blocks(x))
    assert rep["continuity"]["samples_processed"] == 1020 and rep["continuity"]["exact_continuity"] is True


# =========================================================================================
# B. STT never blocks capture; bounded queue; harness-side drops are observable and exact
# =========================================================================================

Q = 20                                               # processing queue bound used by the overflow scenarios


def _held_stt_scenario(n_after):
    """16 head blocks complete the utterance (the last one starts the STT call, which then waits for `release`); n_after
    more blocks follow while the processing worker is stuck inside STT."""
    release = threading.Event()
    eng = engines(release=release)
    blocks = make_blocks(stream(1000 + n_after * BLOCK, [(300, 600)]))
    return release, eng, blocks[:DECISION_BLOCK + 1], blocks[DECISION_BLOCK + 1:]


def test_slow_stt_does_not_stop_the_capture_worker_from_reading():
    release, eng, head, tail = _held_stt_scenario(30)
    src = FakeBlockSource(head + [Hold(lambda: eng.stt.entered.wait(GUARD_S))] + tail)
    h = harness(src, eng, queue_blocks=Q)
    h.start()
    try:
        assert eng.stt.entered.wait(GUARD_S)
        assert src.all_read.wait(GUARD_S), "the capture worker stopped reading while STT was busy"
        assert not h.is_finished                                              # STT is still running ...
        s = h.stats()
        assert s["blocks_captured"] == len(head) + len(tail)                  # ... and every block was already read
        assert s["harness_blocks_dropped"] == len(tail) - Q and s["max_queue_depth"] <= Q
    finally:
        release.set()
    h.stop()


def test_overflow_of_the_processing_queue_is_observable_exact_and_bounded():
    release, eng, head, tail = _held_stt_scenario(30)
    src = FakeBlockSource(head + [Hold(lambda: eng.stt.entered.wait(GUARD_S))] + tail)
    h = harness(src, eng, queue_blocks=Q)
    h.start()
    try:
        assert src.all_read.wait(GUARD_S)
    finally:
        release.set()
    rep = h.stop()
    c = rep["continuity"]
    dropped = len(tail) - Q
    assert c["harness_blocks_dropped"] == dropped and c["harness_samples_dropped"] == dropped * BLOCK
    assert c["blocks_captured"] == c["blocks_processed"] + c["harness_blocks_dropped"]            # nothing vanishes unaccounted
    assert c["max_processing_queue_depth"] == Q                                                   # it filled to its bound and never beyond
    (d,) = c["discontinuities"]
    assert d["reasons"] == ["TRAILING_LOSS"] and d["callback_domain_gap_samples"] == dropped * BLOCK
    assert d["open_capture_discarded"] is False and c["exact_continuity"] is False
    assert [i["status"] for i in rep["interactions"]] == ["TRANSCRIBED"]                         # the finished utterance is kept


def test_a_large_enough_queue_loses_nothing_while_stt_is_slow():
    release, eng, head, tail = _held_stt_scenario(30)
    src = FakeBlockSource(head + [Hold(lambda: eng.stt.entered.wait(GUARD_S))] + tail)
    h = harness(src, eng, queue_blocks=400)
    h.start()
    try:
        assert src.all_read.wait(GUARD_S)
    finally:
        release.set()
    rep = h.stop()
    c = rep["continuity"]
    assert (c["harness_blocks_dropped"], c["blocks_processed"], c["exact_continuity"]) == (0, len(head) + len(tail), True)


def test_harness_drops_in_the_middle_show_up_as_one_exact_position_jump():
    release, eng, head, tail = _held_stt_scenario(35)
    queued_done = threading.Event()
    src = FakeBlockSource([])
    h = harness(src, eng, queue_blocks=Q)
    fed = []
    real_feed = h.pipeline.feed

    def spy(*a, **k):
        out = real_feed(*a, **k)
        fed.append(1)
        if len(fed) == len(head) + Q:                       # the head and the Q queued tail blocks are all processed
            queued_done.set()
        return out

    h.pipeline.feed = spy

    def release_and_wait():
        release.set()
        assert queued_done.wait(GUARD_S)

    src.load(head + [Hold(lambda: eng.stt.entered.wait(GUARD_S))] + tail[:30] + [Hold(release_and_wait)] + tail[30:])
    rep = h.run()
    c = rep["continuity"]
    assert c["harness_blocks_dropped"] == 30 - Q
    (d,) = c["discontinuities"]
    assert d["reasons"] == ["SEQUENCE_GAP"] and d["callback_domain_gap_samples"] == (30 - Q) * BLOCK
    assert c["blocks_captured"] == c["blocks_processed"] + c["harness_blocks_dropped"]


def test_the_processing_queue_bound_must_be_a_positive_integer():
    for bad in (0, -1, None, 1.5, True, "4"):
        with pytest.raises(StageBInputError):
            LiveConfig(processing_queue_blocks=bad)
    for bad in (0, -1, None, True, float("inf"), float("nan")):
        with pytest.raises(StageBInputError):
            LiveConfig(read_timeout_s=bad)
    with pytest.raises(StageBInputError):
        LiveConfig(max_consecutive_timeouts=0)


def test_the_default_queue_is_bounded_and_small_enough_for_a_pi():
    cfg = LiveConfig()
    assert cfg.processing_queue_blocks * 1024 * 2 < 2_000_000            # < 2 MB of int16 PCM


def test_processing_queue_is_really_bounded_in_the_harness():
    src = FakeBlockSource([], end="eof")
    h = harness(src, queue_blocks=7)
    h.start()
    try:
        assert h._queue.maxsize == 7
    finally:
        h.stop()


# =========================================================================================
# C. discontinuities: detected, logged, partial utterance discarded, pipeline restarted, never merged
# =========================================================================================

Y = stream(1400, [(300, 600), (780, 1080)])          # the first utterance has a hole in it; the second is clean


def _expect_second_utterance_only(eng, rep, gap_block_samples):
    assert len(eng.stt.calls) == 1                                       # the damaged first utterance was NOT transcribed
    assert np.array_equal(eng.stt.calls[0], Y[760:1080])                 # and the one that was contains no pre-gap audio
    (i,) = rep["interactions"]
    assert i["status"] == "TRANSCRIBED" and i["run_index"] == 1


def test_a_dropped_block_inside_an_open_capture_discards_it_and_restarts_the_stream(logs):
    eng = engines()
    src = FakeBlockSource([], mic_blocks_dropped=0)
    src.load(make_blocks(Y, drop={11}) [:11] + [Hold(lambda: setattr(src, "mic_blocks_dropped", 1))] + make_blocks(Y, drop={11})[11:])
    h = harness(src, eng)
    rep = h.run()
    c = rep["continuity"]
    (d,) = c["discontinuities"]
    assert d["reasons"] == ["SEQUENCE_GAP"] and d["callback_domain_gap_samples"] == BLOCK
    assert d["at_stream_sample"] == 480 and d["open_capture_discarded"] is True and d["device_overflow_flag"] is False
    assert c["discarded_partial_utterances"] == 1 and c["exact_continuity"] is False
    assert [r["run_index"] for r in c["runs"]] == [0, 1] and c["runs"][1]["first_sample"] == 480
    assert c["mic_telemetry_delta"]["blocks_dropped"] == 1 and c["mic_telemetry_delta"]["samples_dropped"] == BLOCK
    _expect_second_utterance_only(eng, rep, BLOCK)
    assert any("event=stage_b_discontinuity" in l and "SEQUENCE_GAP" in l and "open_capture_discarded=True" in l for l in logs)


def test_the_pipeline_clock_restarts_at_the_gap_so_indices_never_span_it():
    eng = engines()
    h, src, rep = run(make_blocks(Y, drop={11}), eng)
    (i,) = rep["interactions"]
    # master indices are relative to the NEW stream (which began at absolute 480): 760 - 480 = 280, 1080 - 480 = 600
    assert (i["segment_start_sample"], i["segment_end_sample"]) == (280, 600)
    assert h.pipeline.master_samples == 1400 - 480


def test_a_device_overflow_flag_is_a_discontinuity_of_unknown_size():
    z = stream(1400, [(300, 600), (740, 1040)])
    eng = engines()
    h, src, rep = run(make_blocks(z, overflow={11}), eng)
    (d,) = rep["continuity"]["discontinuities"]
    assert d["reasons"] == ["DEVICE_OVERFLOW"] and d["device_overflow_flag"] is True and d["open_capture_discarded"] is True
    assert d["callback_domain_gap_samples"] == 0                         # the callback-domain count is exact; the device loss is not guessed
    assert len(eng.stt.calls) == 1 and np.array_equal(eng.stt.calls[0], z[720:1040])


def test_an_epoch_change_restarts_the_stream_and_discards_the_open_capture():
    a = stream(500, [(300, 500)])                                        # ends in the middle of speech, wake already fired
    b = stream(1000, [(300, 600)])
    eng = engines()
    h, src, rep = run(make_blocks(a, epoch=1) + make_blocks(b, epoch=2), eng)
    (d,) = rep["continuity"]["discontinuities"]
    assert d["reasons"] == ["EPOCH_CHANGE"] and d["callback_domain_gap_samples"] is None and d["open_capture_discarded"] is True
    assert len(eng.stt.calls) == 1 and np.array_equal(eng.stt.calls[0], b[280:600])


def test_audio_that_starts_late_is_reported_as_leading_loss():
    h, src, rep = run(make_blocks(X, start_at=80))
    (d,) = rep["continuity"]["discontinuities"]
    assert d["reasons"] == ["LEADING_LOSS"] and d["callback_domain_gap_samples"] == 80 and d["open_capture_discarded"] is False
    assert rep["continuity"]["exact_continuity"] is False and rep["interaction_count"] == 1


def test_a_position_that_goes_backwards_is_an_overlap_discontinuity():
    blocks = make_blocks(X)
    blocks = blocks[:6] + [blocks[4]] + blocks[6:]                      # block 4 delivered again
    h, src, rep = run(blocks)
    first = rep["continuity"]["discontinuities"][0]
    assert first["reasons"] == ["SEQUENCE_OVERLAP"] and first["callback_domain_gap_samples"] == -80


def test_a_stream_that_ends_while_the_user_is_speaking_is_finished_not_lost():
    x = stream(500, [(300, 500)])                                       # still speaking when the source ends
    eng = engines()
    h, src, rep = run(make_blocks(x), eng)
    (i,) = rep["interactions"]
    assert i["status"] == "STREAM_ENDED" and rep["continuity"]["exact_continuity"] is True
    assert len(eng.stt.calls) == 1


def test_mic_reported_drops_clear_exact_continuity_even_when_no_gap_is_visible():
    """A block dropped at the very end leaves no later block to show the jump; the microphone's own counter still says so."""
    src = FakeBlockSource([])
    blocks = make_blocks(X)
    src.load(blocks + [Hold(lambda: setattr(src, "mic_blocks_dropped", 2))])
    h = harness(src)
    rep = h.run()
    c = rep["continuity"]
    assert c["discontinuity_count"] == 0 and c["mic_telemetry_delta"]["blocks_dropped"] == 2
    assert c["exact_continuity"] is False


def test_a_discontinuity_before_any_speech_still_restarts_cleanly_and_later_speech_works():
    z = stream(1400, [(460, 760)])                       # the new stream starts at 160, so the wake (chunk 8) is at 520
    eng = engines()
    h, src, rep = run(make_blocks(z, drop={3}), eng)
    (d,) = rep["continuity"]["discontinuities"]
    assert d["open_capture_discarded"] is False
    assert len(eng.stt.calls) == 1 and np.array_equal(eng.stt.calls[0], z[440:760])


def test_each_of_several_gaps_restarts_the_stream_and_never_joins_utterances():
    z = stream(1700, [(300, 600), (780, 1200), (1340, 1640)])
    eng = engines()
    h, src, rep = run(make_blocks(z, drop={11, 25}), eng)
    c = rep["continuity"]
    assert [d["callback_domain_gap_samples"] for d in c["discontinuities"]] == [BLOCK, BLOCK]
    assert [d["open_capture_discarded"] for d in c["discontinuities"]] == [True, True]       # both gaps hit an open capture
    assert c["discarded_partial_utterances"] == 2 and [r["run_index"] for r in c["runs"]] == [0, 1, 2]
    assert len(eng.stt.calls) == 1 and np.array_equal(eng.stt.calls[0], z[1320:1640])      # only the clean third utterance was transcribed


# =========================================================================================
# D. real microphone telemetry through the harness (PortAudio replaced)
# =========================================================================================

class _GatedMic:
    """The real ReSpeakerMicrophone behind a gate, so the test can fill its queue before the capture worker reads."""

    channels = 1

    def __init__(self, mic, gate, finish):
        self._mic, self._gate, self._finish = mic, gate, finish
        self.sample_rate = mic.sample_rate

    def start(self):
        self._mic.start()

    def stop(self):
        self._mic.stop()

    def telemetry(self):
        return self._mic.telemetry()

    def read_block(self, timeout):
        assert self._gate.wait(GUARD_S)
        try:
            return self._mic.read_block(timeout)
        except queue.Empty:
            if self._finish.is_set():
                raise EndOfAudio("done")
            raise


def test_mic_queue_drops_reach_the_evidence_with_exact_counts(monkeypatch):
    monkeypatch.setattr(mic_mod.sd, "query_devices", lambda: [{"name": "ReSpeaker XVF3800", "max_input_channels": 1}])

    class Stream:
        def __init__(self, **kw): pass
        def start(self): pass
        def stop(self): pass
        def close(self): pass

    monkeypatch.setattr(mic_mod.sd, "InputStream", Stream)
    mic = ReSpeakerMicrophone(sample_rate=SR, queue_max_blocks=3)
    gate, finish = threading.Event(), threading.Event()
    h = harness(_GatedMic(mic, gate, finish))
    h.start()
    for i in range(8):                                   # 8 callbacks while nobody reads: 5 are dropped by the microphone queue
        mic._callback(np.full((BLOCK, 1), 1, np.int16), BLOCK, None, None)
    finish.set()
    gate.set()
    assert h.wait(GUARD_S)                               # the source ends once the 3 surviving blocks are read
    rep = h.stop()
    c = rep["continuity"]
    assert c["mic_telemetry_delta"]["blocks_dropped"] == 5 and c["mic_telemetry_delta"]["samples_dropped"] == 5 * BLOCK
    (d,) = c["discontinuities"]
    assert d["reasons"] == ["LEADING_LOSS"] and d["callback_domain_gap_samples"] == 5 * BLOCK   # the position jump equals the drop counter
    assert c["blocks_processed"] == 3 and c["exact_continuity"] is False


# =========================================================================================
# E. microphone stall, worker failures, propagation, shutdown
# =========================================================================================

def test_a_stalled_microphone_fails_deterministically(logs):
    src = FakeBlockSource([], end="stall")
    h = harness(src, stall=3)
    with pytest.raises(StageBWorkerError) as info:
        h.run(HANG_S)
    rep = info.value.report
    assert rep["run_error"] == {"stage": "capture", "type": "MicrophoneStall"}
    assert (rep["completed"], rep["end_reason"]) == (False, "FAILED")
    assert rep["continuity"]["mic_read_timeouts"] == 3 and src.stopped == 1
    assert isinstance(info.value.__cause__, MicrophoneStall)
    assert any("event=stage_b_failure" in l and "stage=capture" in l for l in logs)


def test_isolated_read_timeouts_are_tolerated_and_counted():
    blocks = make_blocks(X)
    script = [Timeout(), Timeout()] + blocks[:10] + [Timeout(), Timeout()] + blocks[10:]
    h, src, rep = run(script, stall=3)
    assert rep["completed"] is True and rep["continuity"]["mic_read_timeouts"] == 4 and rep["continuity"]["exact_continuity"] is True


def test_a_capture_exception_is_propagated_and_stops_the_processing_worker():
    script = make_blocks(X)[:5] + [Boom(RuntimeError("device vanished"))]
    src = FakeBlockSource(script)
    h = harness(src)
    with pytest.raises(StageBWorkerError) as info:
        h.run(HANG_S)
    assert info.value.report["run_error"] == {"stage": "capture", "type": "RuntimeError"}
    assert isinstance(info.value.__cause__, RuntimeError)
    assert src.stopped == 1 and h.is_finished


def test_a_pipeline_exception_is_propagated_not_swallowed():
    eng = engines(vad=EnergyVad(fail_after=3))
    src = FakeBlockSource(make_blocks(X))
    h = harness(src, eng)
    with pytest.raises(StageBWorkerError) as info:
        h.run(HANG_S)
    assert info.value.report["run_error"] == {"stage": "processing", "type": "RuntimeError"}
    assert info.value.report["completed"] is False and src.stopped == 1


def test_after_a_failure_the_processing_worker_stops_and_the_backlog_is_accounted():
    release, eng, head, tail = _held_stt_scenario(5)
    src = FakeBlockSource(head + [Hold(lambda: eng.stt.entered.wait(GUARD_S))] + tail + [Boom(RuntimeError("device vanished"))])
    h = harness(src, eng)
    h.start()
    assert h._abort.wait(GUARD_S)                        # the capture worker failed while the processing worker is inside STT
    release.set()
    with pytest.raises(StageBWorkerError) as info:
        h.stop()
    c = info.value.report["continuity"]
    assert c["blocks_processed"] == len(head) and c["blocks_discarded_on_abort"] == len(tail)
    assert c["blocks_captured"] == c["blocks_processed"] + c["blocks_discarded_on_abort"] + c["harness_blocks_dropped"]


def test_a_result_sink_failure_is_a_processing_failure():
    def sink(result, entry):
        raise ValueError("sink broke")

    src = FakeBlockSource(make_blocks(X))
    h = harness(src, on_result=sink)
    with pytest.raises(StageBWorkerError) as info:
        h.run(HANG_S)
    assert info.value.report["run_error"] == {"stage": "processing", "type": "ValueError"}


def test_a_source_that_cannot_start_fails_before_any_worker_exists():
    src = FakeBlockSource([], fail_start=OSError("no device"))
    h = harness(src)
    with pytest.raises(StageBWorkerError) as info:
        h.start()
    rep = info.value.report
    assert rep["capture_started"] is False and rep["run_error"] == {"stage": "source_start", "type": "OSError"}
    assert not h.is_running and [t for t in threading.enumerate() if t.name.startswith("stageb-")] == []


def test_stop_is_graceful_idempotent_and_releases_the_source_once():
    src = FakeBlockSource(make_blocks(X), end="idle")
    h = harness(src)
    h.start()
    assert src.all_read.wait(GUARD_S)
    rep = h.stop()
    assert rep["end_reason"] == "STOP_REQUESTED" and rep["completed"] is True
    assert [i["status"] for i in rep["interactions"]] == ["TRANSCRIBED"]
    assert h.stop() is rep and src.stopped == 1 and not h.is_running


def test_start_while_running_is_refused_and_stop_before_start_is_refused():
    h = harness(FakeBlockSource([], end="idle"))
    with pytest.raises(StageBInputError):
        h.stop()
    h.start()
    try:
        with pytest.raises(StageBInputError):
            h.start()
    finally:
        h.stop()


def test_repeated_start_stop_cycles_are_independent():
    src = FakeBlockSource([], end="idle")
    eng = engines()
    h = harness(src, eng)
    ids, counts = set(), []
    for _ in range(3):
        src.load(make_blocks(X))
        h.start()
        assert src.all_read.wait(GUARD_S)
        rep = h.stop()
        ids.add(rep["session_id"])
        counts.append((rep["interaction_count"], rep["continuity"]["blocks_processed"], rep["continuity"]["discontinuity_count"]))
    assert counts == [(1, X_BLOCKS, 0)] * 3 and len(ids) == 3
    assert (src.started, src.stopped) == (3, 3) and len(eng.stt.calls) == 3


def test_the_harness_can_restart_after_a_failed_session():
    src = FakeBlockSource([Boom(RuntimeError("x"))])
    h = harness(src)
    with pytest.raises(StageBWorkerError):
        h.run(HANG_S)
    src.load(make_blocks(X))
    rep = h.run()
    assert rep["completed"] is True and rep["interaction_count"] == 1


# =========================================================================================
# F. manual (development) activation is explicit and distinguishable
# =========================================================================================

def test_manual_activation_is_labelled_and_never_looks_like_a_wake_detection():
    manual = ManualActivationDetector(sample_rate=SR, chunk_samples=BLOCK)
    eng = Engines(manual, EnergyVad(), FakeStt("turn on the lights"))
    blocks = make_blocks(X)
    src = FakeBlockSource([])
    h = harness(src, eng)
    eight_fed, fed = threading.Event(), []
    real_feed = h.pipeline.feed

    def spy(*a, **k):
        out = real_feed(*a, **k)
        fed.append(1)
        if len(fed) == 8:
            eight_fed.set()
        return out

    h.pipeline.feed = spy
    # the key press is synchronised with the processing worker: blocks 0-7 are fed before arm() takes effect
    src.load(blocks[:8] + [Hold(lambda: (eight_fed.wait(GUARD_S), manual.arm()))] + blocks[8:])
    rep = h.run()
    (i,) = rep["interactions"]
    assert rep["activation_mode"] == "MANUAL_DEVELOPMENT" and i["activation_mode"] == "MANUAL_DEVELOPMENT"
    assert "MANUAL DEVELOPMENT ACTIVATION" in rep["validation_label"] and "NOT WAKE-ACTIVATED" in rep["validation_label"]
    assert i["wake_keyword"] == "manual" and i["status"] == "TRANSCRIBED"
    assert np.array_equal(eng.stt.calls[0], X[280:600])


def test_without_arming_the_manual_detector_never_activates():
    manual = ManualActivationDetector(sample_rate=SR, chunk_samples=BLOCK)
    eng = Engines(manual, EnergyVad(), FakeStt())
    h, src, rep = run(make_blocks(X), eng)
    assert rep["interaction_count"] == 0 and eng.stt.calls == []


def test_a_wake_run_is_never_labelled_manual():
    h, src, rep = run(make_blocks(X))
    assert rep["activation_mode"] == "WAKE" and "MANUAL" not in rep["validation_label"]


# =========================================================================================
# G. privacy: no transcript and no PCM in logs or evidence by default
# =========================================================================================

def test_transcripts_reach_only_the_explicit_sink(logs, tmp_path):
    got = []
    eng = engines(reply=REPLY)
    h, src, rep = run(make_blocks(X), eng, on_result=lambda result, entry: got.append(result.normalized_text))
    assert got == [SECRET]                                              # available to a caller that asked for it
    blob = json.dumps(rep)
    assert SECRET not in blob and "marmalade" not in blob
    assert not any(SECRET in l or "marmalade" in l for l in logs)
    path = write_evidence(rep, tmp_path)
    assert SECRET not in path.read_text(encoding="utf-8")
    assert rep["interactions"][0]["has_text"] is True and rep["privacy"] == {"transcripts_in_report": False, "pcm_in_report": False}


def test_the_report_holds_no_audio_or_text_keys_anywhere():
    h, src, rep = run(make_blocks(X))
    forbidden = {"text", "transcript", "normalized_text", "actual_transcript", "pcm", "samples", "audio", "wav"}

    def keys(n):
        if isinstance(n, dict):
            for k, v in n.items():
                yield k
                yield from keys(v)
        elif isinstance(n, list):
            for v in n:
                yield from keys(v)

    assert forbidden.isdisjoint(set(keys(rep)))


def test_validate_report_rejects_a_report_that_carries_a_transcript():
    h, src, rep = run(make_blocks(X))
    rep["interactions"][0]["text"] = SECRET
    with pytest.raises(StageBReportError):
        validate_report(rep)


# =========================================================================================
# H. validation labels: nothing here can claim hardware validation
# =========================================================================================

def test_a_fake_input_run_is_labelled_software_validated_with_fake_input():
    h, src, rep = run(make_blocks(X))
    assert rep["source_type"] == "FAKE_INPUT" and rep["validation_label"].startswith("SOFTWARE-VALIDATED")
    assert "FAKE INPUT" in rep["validation_label"] and "HARDWARE-VALIDATED" not in rep["validation_label"]
    assert all(i["validation_label"] == rep["validation_label"] for i in rep["interactions"])


def test_a_live_label_is_unreviewed_and_explicitly_not_hardware_validated():
    label = sb.validation_label(sb.SOURCE_LIVE, "WAKE")
    assert "UNREVIEWED" in label and "NOT HARDWARE-VALIDATED" in label and not label.startswith("HARDWARE-VALIDATED")


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(validation_label="HARDWARE-VALIDATED"),
    lambda r: r.update(source_type="RESPEAKER_LIVE_MICROPHONE"),                      # a fake run relabelled as a live one
    lambda r: r.update(activation_mode="MANUAL_DEVELOPMENT"),                          # mode and label disagree
    lambda r: r["privacy"].update(transcripts_in_report=True),
    lambda r: r.update(completed=False),
    lambda r: r.update(interaction_count=5),
])
def test_validate_report_rejects_dishonest_records(mutate):
    h, src, rep = run(make_blocks(X))
    mutate(rep)
    with pytest.raises(StageBReportError):
        validate_report(rep)


def test_the_module_never_produces_a_bare_hardware_validated_label():
    text = Path(sb.__file__).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "HARDWARE-VALIDATED" in line:
            assert "NOT HARDWARE-VALIDATED" in line or line.lstrip().startswith(("#", '"""')) or "must" in line or "never" in line.lower() \
                or "LIVE-MICROPHONE" in line or "label" in line.lower() or "labelled" in line.lower(), line


# =========================================================================================
# I. models are loaded and verified before capture; no downloads
# =========================================================================================

def _good_factories():
    return dict(vad_factory=EnergyVad, stt_factory=FakeStt, wake_factory=ScriptedWake)


def test_load_engines_returns_verified_engines():
    e = load_engines("WAKE", **_good_factories())
    assert isinstance(e.vad, EnergyVad) and isinstance(e.detector, ScriptedWake) and e.activation_mode == "WAKE"


def test_an_unverified_vad_or_stt_model_is_refused():
    class Unverified(EnergyVad):
        artifact_verified = False

    class UnverifiedStt(FakeStt):
        snapshot_verified = False

    with pytest.raises(StageBModelError) as a:
        load_engines("WAKE", **{**_good_factories(), "vad_factory": Unverified})
    assert a.value.stage == "vad" and a.value.cause_type == "ArtifactNotVerified"
    with pytest.raises(StageBModelError) as b:
        load_engines("WAKE", **{**_good_factories(), "stt_factory": UnverifiedStt})
    assert b.value.stage == "stt"


def test_a_model_that_fails_to_load_is_reported_by_stage_and_class():
    def boom():
        raise FileNotFoundError("model dir")

    with pytest.raises(StageBModelError) as info:
        load_engines("WAKE", **{**_good_factories(), "stt_factory": boom})
    assert (info.value.stage, info.value.cause_type) == ("stt", "FileNotFoundError")
    assert isinstance(info.value.__cause__, FileNotFoundError)


def test_manual_activation_loads_no_wake_model():
    called = []
    e = load_engines("MANUAL_DEVELOPMENT", **{**_good_factories(), "wake_factory": lambda: called.append(1)})
    assert called == [] and isinstance(e.detector, ManualActivationDetector)


def test_an_unknown_activation_mode_is_refused():
    with pytest.raises(StageBInputError):
        load_engines("both", **_good_factories())


def test_the_harness_module_contains_no_network_or_download_code():
    text = Path(sb.__file__).read_text(encoding="utf-8").lower()
    for word in ("urllib", "requests", "http://", "https://", "huggingface", "snapshot_download", "download_models", "socket"):
        assert word not in text, word


# =========================================================================================
# J. settings: explicit, local, uncalibrated; production values untouched
# =========================================================================================

def test_the_live_segmenter_values_are_explicit_development_values_and_settings_stay_unconfigured():
    cfg = sb.build_live_segmenter_config(EnergyVad16())
    for k, v in sb.DEV_LIVE_SEGMENTER_VALUES.items():
        assert getattr(cfg, k) == v
    assert cfg.pre_roll_s == 2.0 and "UNCALIBRATED" in sb.SEGMENTER_STATUS
    for name in ("VAD_START_THRESHOLD", "VAD_END_THRESHOLD", "VAD_END_SILENCE_S", "VOICE_PRE_ROLL_S", "VOICE_MAX_UTTERANCE_S"):
        assert getattr(settings, name) is None


def test_the_report_records_the_segmenter_status_and_models():
    h, src, rep = run(make_blocks(X))
    assert rep["segmenter_config"]["status"].startswith("CALLER-SUPPLIED")
    assert rep["models"]["stt"]["snapshot_verified"] is True and rep["models"]["wake"]["model_hash_verified"] is False


def test_a_source_with_the_wrong_rate_or_channels_is_refused():
    with pytest.raises(StageBInputError):
        StageBLiveHarness(FakeBlockSource([], sample_rate=8000), engines(), segmenter_config=make_config(), sample_rate=SR)
    src = FakeBlockSource([])
    src.channels = 2
    with pytest.raises(StageBInputError):
        StageBLiveHarness(src, engines(), segmenter_config=make_config(), sample_rate=SR)


# =========================================================================================
# K. evidence files and the command line
# =========================================================================================

def _cli_script(total=64000):
    x = np.zeros(total, np.int16)
    x[16000:40000] = (2000 + np.arange(16000, 40000) % 1000).astype(np.int16)
    return make_blocks(x, block=1024)


def _cli_engines(reply=REPLY):
    return lambda activation: Engines(ScriptedWake16(fire=(15,)), EnergyVad16(), FakeStt16(reply))


def _cli(tmp_path, *extra, script=None, source=None, factory=None, stdin=None, **kw):
    import io
    out, err = io.StringIO(), io.StringIO()
    src = source or FakeBlockSource(script if script is not None else _cli_script(), sample_rate=16000)
    code = main(["--out-dir", str(tmp_path / "ev"), *extra], engines_factory=factory or _cli_engines(), source=src,
                stdin=stdin, out=out, err=err, **kw)
    return code, out.getvalue(), err.getvalue(), src


def _only_record(tmp_path):
    files = list((tmp_path / "ev").glob("*.stage_b.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8")), files[0]


def test_cli_end_to_end_with_fake_input_writes_one_record_and_no_transcript(tmp_path):
    code, out, err, src = _cli(tmp_path)
    assert code == 0, err
    rec, path = _only_record(tmp_path)
    assert rec["validation_label"].startswith("SOFTWARE-VALIDATED") and rec["interaction_count"] == 1
    assert rec["interactions"][0]["status"] == "TRANSCRIBED" and rec["continuity"]["exact_continuity"] is True
    assert SECRET not in out and SECRET not in err and SECRET not in path.read_text(encoding="utf-8")
    assert str(path) in out


def test_cli_show_transcript_prints_it_and_only_then(tmp_path):
    code, out, err, _ = _cli(tmp_path, "--show-transcript")
    assert code == 0 and f"transcript[0] (WAKE): {SECRET}" in out


def test_cli_transcripts_out_is_explicit_external_and_exclusive(tmp_path):
    target = tmp_path / "transcripts.jsonl"
    code, out, err, _ = _cli(tmp_path, "--transcripts-out", str(target))
    assert code == 0
    (line,) = target.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["text"] == SECRET
    code2, out2, err2, _ = _cli(tmp_path, "--transcripts-out", str(target))
    assert code2 == 2 and "already exists" in err2 and target.read_text(encoding="utf-8").count("\n") == 1


def test_cli_refuses_an_evidence_directory_inside_the_repository(tmp_path):
    inside = Path(__file__).resolve().parent / "_stage_b_inside_repo"
    called = []
    import io
    err = io.StringIO()
    src = FakeBlockSource(_cli_script(), sample_rate=16000)
    code = main(["--out-dir", str(inside)], engines_factory=lambda a: called.append(1), source=src, out=io.StringIO(), err=err)
    assert code == 2 and "inside the repository" in err.getvalue()
    assert not inside.exists() and called == [] and src.started == 0


def test_cli_refuses_a_transcripts_file_inside_the_repository(tmp_path):
    inside = Path(__file__).resolve().parent / "_stage_b_transcripts.jsonl"
    code, out, err, src = _cli(tmp_path, "--transcripts-out", str(inside))
    assert code == 2 and not inside.exists() and src.started == 0


def test_evidence_is_never_overwritten(tmp_path):
    h, src, rep = run(make_blocks(X))
    first = write_evidence(rep, tmp_path)
    before = first.read_bytes()
    with pytest.raises(StageBEvidenceError):
        write_evidence(rep, tmp_path)
    assert first.read_bytes() == before and len(list(tmp_path.glob("*.stage_b.json"))) == 1


def test_cli_model_failure_writes_a_failure_record_and_never_starts_capture(tmp_path):
    def factory(activation):
        raise StageBModelError("vad", "ArtifactNotVerified", "bad hash")

    code, out, err, src = _cli(tmp_path, factory=factory)
    assert code == 3 and "model loading failed" in err
    rec, _ = _only_record(tmp_path)
    assert rec["run_error"] == {"stage": "model_load:vad", "type": "ArtifactNotVerified"}
    assert rec["capture_started"] is False and rec["completed"] is False and src.started == 0


def test_cli_source_start_failure_exits_3_with_a_record(tmp_path):
    src = FakeBlockSource([], sample_rate=16000, fail_start=OSError("no device"))
    code, out, err, _ = _cli(tmp_path, source=src)
    assert code == 3
    rec, _ = _only_record(tmp_path)
    assert rec["run_error"] == {"stage": "source_start", "type": "OSError"} and rec["capture_started"] is False


def test_cli_worker_failure_exits_3_with_a_record_and_no_transcript(tmp_path):
    script = _cli_script()[:30] + [Boom(RuntimeError("x"))]
    code, out, err, _ = _cli(tmp_path, script=script)
    assert code == 3
    rec, _ = _only_record(tmp_path)
    assert rec["run_error"]["type"] == "RuntimeError" and rec["completed"] is False and rec["end_reason"] == "FAILED"


def test_cli_manual_mode_is_announced_and_labelled(tmp_path):
    import io
    src = FakeBlockSource([], sample_rate=16000, end="idle")
    code, out, err, _ = _cli(tmp_path, "--activation", "manual", source=src, stdin=io.StringIO("q\n"),
                             factory=lambda a: Engines(ManualActivationDetector(), EnergyVad16(), FakeStt16()))
    assert code == 0 and "MANUAL DEVELOPMENT ACTIVATION" in out
    rec, _ = _only_record(tmp_path)
    assert rec["activation_mode"] == "MANUAL_DEVELOPMENT" and "NOT WAKE-ACTIVATED" in rec["validation_label"]


def test_cli_live_source_is_labelled_unreviewed_never_hardware_validated(tmp_path, monkeypatch):
    """Wiring only: the microphone class is replaced by a fake, so this proves the label the CLI attaches to a live run."""
    class FakeMic(FakeBlockSource):
        def __init__(self, device_name_hint=None):
            super().__init__(_cli_script(), sample_rate=16000)

    monkeypatch.setattr(mic_mod, "ReSpeakerMicrophone", FakeMic)
    import io
    out, err = io.StringIO(), io.StringIO()
    code = main(["--out-dir", str(tmp_path / "ev")], engines_factory=_cli_engines(), out=out, err=err)
    assert code == 0, err.getvalue()
    rec, _ = _only_record(tmp_path)
    assert rec["source_type"] == "RESPEAKER_LIVE_MICROPHONE"
    assert "UNREVIEWED" in rec["validation_label"] and "NOT HARDWARE-VALIDATED" in rec["validation_label"]


def test_cli_rejects_a_non_positive_duration(tmp_path):
    code, out, err, src = _cli(tmp_path, "--duration-s", "0")
    assert code == 2 and src.started == 0


# =========================================================================================
# L. real VAD + STT plumbing through the harness (synthetic voiced signal, scripted wake, fake source)
# =========================================================================================

def test_real_vad_and_stt_engines_run_through_the_harness_plumbing():
    """PLUMBING ONLY: shows the harness works with the real engines' shapes (512-sample VAD frames, 1280-sample wake chunks,
    1024-sample blocks). The signal is synthetic and says nothing about accuracy. Skips when a model is not on this machine."""
    from tests.test_voice_pipeline_real import LEAD_S, RATE, TAIL_S, VOICED_S, synthetic_voiced
    from tests.stage_b_support import ScriptedWake16
    try:
        real = load_engines("WAKE", wake_factory=lambda: ScriptedWake16(fire=(12,)))
    except StageBModelError as exc:
        pytest.skip(f"model not available on this machine ({exc.stage}: {exc.cause_type})")
    voiced = synthetic_voiced(VOICED_S)
    x = np.concatenate([np.zeros(int(LEAD_S * RATE), np.int16), voiced.astype(np.int16), np.zeros(int(TAIL_S * RATE), np.int16)])
    src = FakeBlockSource(make_blocks(x, block=1024), sample_rate=16000)
    rep = StageBLiveHarness(src, real, source_kind="FAKE_INPUT").run()
    assert rep["completed"] and rep["continuity"]["exact_continuity"] and rep["interaction_count"] == 1
    assert rep["interactions"][0]["segment_outcome"] == "COMPLETE" and rep["models"]["vad"]["artifact_verified"] is True
    assert rep["validation_label"].startswith("SOFTWARE-VALIDATED")


# =========================================================================================
# M. optional hooks used by assistant.runtime: a prebuilt pipeline and a discontinuity callback
# =========================================================================================

def test_a_prebuilt_pipeline_is_used_instead_of_building_one():
    from audio.utterance import UtteranceSegmenter
    from speech.pipeline import VoicePipeline
    eng = engines()
    mine = VoicePipeline(eng.detector, UtteranceSegmenter(make_config(), eng.vad), eng.stt)
    src = FakeBlockSource(make_blocks(X))
    h = StageBLiveHarness(src, eng, sample_rate=SR, pipeline=mine,
                          config=LiveConfig(read_timeout_s=0.01, max_consecutive_timeouts=10 ** 6, join_timeout_s=GUARD_S))
    rep = h.run()
    assert h.pipeline is mine and rep["interaction_count"] == 1 and rep["segmenter_config"]["status"].startswith("PIPELINE SUPPLIED")


def test_the_discontinuity_callback_receives_each_entry_after_the_restart():
    seen = []
    h, src, rep = run(make_blocks(Y, drop={11}), engines(), on_discontinuity=seen.append)
    assert [e["reasons"] for e in seen] == [["SEQUENCE_GAP"]] and seen[0]["index"] == 0


def test_a_failing_discontinuity_callback_is_a_processing_failure():
    def boom(entry):
        raise ValueError("hook broke")

    src = FakeBlockSource(make_blocks(Y, drop={11}))
    h = harness(src, on_discontinuity=boom)
    with pytest.raises(StageBWorkerError) as info:
        h.run(GUARD_S)
    assert info.value.report["run_error"] == {"stage": "processing", "type": "ValueError"}


def test_the_discontinuity_callback_runs_after_the_pipeline_was_restarted():
    z = stream(1400, [(300, 600)])
    holder, seen = {}, []
    src = FakeBlockSource(make_blocks(z, drop={11}))                    # the gap hits the open capture
    h = harness(src, engines())
    h._on_discontinuity = lambda entry: seen.append((h.pipeline.is_capturing, h.pipeline.master_samples))
    h.run()
    assert seen == [(False, 0)]                                         # already restarted: nothing capturing, clock back at 0
