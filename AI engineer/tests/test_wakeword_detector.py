"""Tests for the wake-word abstraction (wakeword/detector.py).

No real engine is involved: the detectors here are fakes with fully
scripted, deterministic behaviour, which is what lets us verify the
shared threshold/cooldown logic and the WAV -> Reframer -> detector path.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from audio.file_source import WavFileSource
from wakeword.detector import Detection, WakeWordDetector, run_on_source

ROOT = Path(__file__).resolve().parents[1]


class ScriptedDetector(WakeWordDetector):
    """Returns scripted scores, one dict per chunk (silence once exhausted)."""

    def __init__(self, script, chunk_samples=1600, **kwargs):
        super().__init__(**kwargs)
        self._script = list(script)
        self._chunk_samples = chunk_samples
        self.backend_resets = 0

    @property
    def sample_rate(self):
        return 16000

    @property
    def chunk_samples(self):
        return self._chunk_samples

    def _score_chunk(self, chunk):
        i = self.chunks_processed  # index of the chunk being scored
        return self._script[i] if i < len(self._script) else {"fake": 0.0}

    def _reset_backend(self):
        self.backend_resets += 1


class PeakDetector(WakeWordDetector):
    """Score 1.0 if the chunk contains loud samples, else 0.0; records input."""

    def __init__(self, chunk_samples=1280, **kwargs):
        super().__init__(**kwargs)
        self._chunk_samples = chunk_samples
        self.seen = []

    @property
    def sample_rate(self):
        return 16000

    @property
    def chunk_samples(self):
        return self._chunk_samples

    def _score_chunk(self, chunk):
        self.seen.append(chunk.copy())
        return {"loud": 1.0 if np.abs(chunk.astype(np.int32)).max() > 10000 else 0.0}

    def _reset_backend(self):
        self.seen.clear()


def _chunk(detector):
    return np.zeros(detector.chunk_samples, dtype=np.int16)


def _feed(detector, n):
    return [detector.process(_chunk(detector)) for _ in range(n)]


# -- interface ------------------------------------------------------------

def test_interface_is_abstract():
    with pytest.raises(TypeError):
        WakeWordDetector()


def test_detector_module_imports_no_engine():
    """The abstraction must not depend on openWakeWord or Porcupine.

    Runs in a fresh interpreter so other tests cannot pollute sys.modules.
    """
    code = (
        "import sys, wakeword.detector;"
        "bad = [m for m in ('openwakeword', 'pvporcupine') if m in sys.modules];"
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT)
    assert result.returncode == 0


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_invalid_threshold_rejected(bad):
    with pytest.raises(ValueError):
        ScriptedDetector([], threshold=bad)


def test_invalid_cooldown_rejected():
    with pytest.raises(ValueError):
        ScriptedDetector([], cooldown_s=-1)


@pytest.mark.parametrize(
    "bad",
    [
        np.zeros(1600, dtype=np.float32),      # wrong dtype
        np.zeros((1600, 1), dtype=np.int16),   # not 1-D
        np.zeros(1599, dtype=np.int16),        # wrong length
        [0] * 1600,                            # not an array
    ],
)
def test_process_validates_chunk(bad):
    with pytest.raises(ValueError):
        ScriptedDetector([]).process(bad)


# -- threshold ------------------------------------------------------------

def test_threshold_is_inclusive_and_reports_the_score():
    d = ScriptedDetector([{"hey_jarvis": 0.49}, {"hey_jarvis": 0.5}], threshold=0.5)
    first, second = _feed(d, 2)
    assert first is None
    assert isinstance(second, Detection)
    assert second.keyword == "hey_jarvis"
    assert second.score == 0.5
    assert second.threshold == 0.5
    assert d.last_scores == {"hey_jarvis": 0.5}


def test_highest_scoring_keyword_wins():
    d = ScriptedDetector([{"a": 0.6, "b": 0.9, "c": 0.1}])
    (det,) = _feed(d, 1)
    assert det.keyword == "b"
    assert det.score == 0.9


def test_empty_scores_never_detect():
    d = ScriptedDetector([{}, {}])
    assert _feed(d, 2) == [None, None]


# -- cooldown -------------------------------------------------------------

def test_cooldown_suppresses_repeats_and_is_not_extended_by_them():
    # 1600-sample chunks = 0.1 s; cooldown 1.0 s = exactly 10 chunks.
    hits = {0, 3, 9, 10, 11, 20}
    script = [{"kw": 0.9} if i in hits else {"kw": 0.0} for i in range(25)]
    d = ScriptedDetector(script, threshold=0.5, cooldown_s=1.0)

    accepted = [i for i, det in enumerate(_feed(d, 25)) if det is not None]

    # 3, 9 and 11 fall inside a cooldown. 10 is accepted exactly 1.0 s after
    # chunk 0 even though 9 was suppressed just before it (suppressed
    # crossings do not restart the cooldown).
    assert accepted == [0, 10, 20]
    assert d.suppressed_detections == 3


def test_zero_cooldown_accepts_every_crossing():
    d = ScriptedDetector([{"kw": 1.0}] * 5, cooldown_s=0.0)
    assert all(det is not None for det in _feed(d, 5))
    assert d.suppressed_detections == 0


def test_detection_timing_comes_from_sample_counter():
    d = ScriptedDetector([{"kw": 0.0}, {"kw": 0.0}, {"kw": 1.0}])  # 1600-sample chunks
    det = _feed(d, 3)[2]
    assert det.chunk_index == 2
    assert det.end_sample == 3 * 1600
    assert det.stream_time_s == pytest.approx(0.3)
    assert det.processing_ms >= 0
    assert det.wall_time_s > 0
    assert d.samples_processed == 4800
    assert d.chunks_processed == 3


def test_reset_starts_a_new_stream():
    d = ScriptedDetector([{"kw": 1.0}] * 4, cooldown_s=5.0)
    assert _feed(d, 1)[0] is not None
    d.reset()

    assert d.backend_resets == 1
    assert d.samples_processed == 0
    assert d.chunks_processed == 0
    assert d.suppressed_detections == 0
    # Cooldown from the previous stream is gone; the clock restarts at 0.
    det = d.process(_chunk(d))
    assert det is not None
    assert det.chunk_index == 0 and det.end_sample == 1600


# -- WAV replay -> Reframer -> detector ------------------------------------

def _burst_wav(write_wav, extra_samples=0):
    """10 s of silence with loud bursts at 2.0-2.5 s, 2.7-3.0 s and 7.0-7.5 s."""
    audio = np.zeros(16000 * 10 + extra_samples, dtype=np.int16)
    for start, stop in [(32000, 40000), (43200, 48000), (112000, 120000)]:
        audio[start:stop] = 20000
    return audio, write_wav(audio)


def test_wav_to_reframer_to_detector_end_to_end(write_wav):
    audio, path = _burst_wav(write_wav)
    detector = PeakDetector(chunk_samples=1280, threshold=0.5, cooldown_s=2.0)

    detections = run_on_source(WavFileSource(path), detector)

    # Burst 1 starts at sample 32000 = the start of chunk 25 -> fires when
    # that chunk ends (26 * 1280 = 33280 samples = 2.08 s). Burst 2 (2.7 s)
    # is inside the 2 s cooldown. Burst 3 starts in chunk 87 -> 88 * 1280 =
    # 112640 samples = 7.04 s.
    assert [d.chunk_index for d in detections] == [25, 87]
    assert [d.end_sample for d in detections] == [33280, 112640]
    assert [d.stream_time_s for d in detections] == pytest.approx([2.08, 7.04])
    assert all(d.keyword == "loud" and d.score == 1.0 for d in detections)
    # Chunks 26-31, 33-37 and 88-93 also crossed the threshold during cooldown.
    assert detector.suppressed_detections == 17
    assert detector.chunks_processed == 125


def test_detector_sees_every_sample_exactly_once(write_wav):
    """WAV replay -> reframer -> detector loses/duplicates nothing, even when
    the file length is not a multiple of the WP1 block or detector chunk size."""
    extra = 357
    audio, path = _burst_wav(write_wav, extra_samples=extra)
    detector = PeakDetector(chunk_samples=1280)

    run_on_source(WavFileSource(path), detector)

    assert all(c.shape == (1280,) and c.dtype == np.int16 for c in detector.seen)
    received = np.concatenate(detector.seen)
    assert np.array_equal(received[:len(audio)], audio)   # identical, same order
    assert not received[len(audio):].any()                # only trailing padding
    assert len(received) - len(audio) < 1280              # less than one chunk of it


def test_replay_is_deterministic_and_independent_of_pacing(write_wav):
    _, path = _burst_wav(write_wav)

    def run(realtime):
        clock_t = [0.0]

        def sleep(s):
            clock_t[0] += s

        source = WavFileSource(path, realtime=realtime, clock=lambda: clock_t[0], sleep=sleep)
        detector = PeakDetector(chunk_samples=1280)
        return [(d.chunk_index, d.end_sample) for d in run_on_source(source, detector)]

    assert run(realtime=False) == run(realtime=True)


def test_run_on_source_rejects_sample_rate_mismatch(write_wav):
    _, path = _burst_wav(write_wav)

    class OtherRate(PeakDetector):
        @property
        def sample_rate(self):
            return 8000

    with pytest.raises(ValueError, match="8000"):
        run_on_source(WavFileSource(path), OtherRate())
