"""TEST / SYNTHETIC helpers for the Week 7 Task 1 tests.

Everything here is a test double or a deterministic fixture. NOTHING in this module is a
production VAD, and nothing here says anything about real VAD accuracy: the scripted engine
returns whatever score list it is given, and the "audio" is a deterministic counter pattern
whose sample values only exist so a segment can be compared sample-for-sample with the
stream range it should cover.

Conventions used by the tests: 1000 Hz sample rate and 10-sample VAD frames, so a duration in
seconds x 1000 is its sample count and a frame is 10 ms.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from audio.utterance import (
    PcmSnapshot,
    RollingPcmBuffer,
    SegmenterConfig,
    SegmenterResult,
    UtteranceSegmenter,
)
from audio.vad import VadEngine
from wakeword.detector import Detection

SR = 1000
FRAME = 10


def index_pcm(start: int, n: int) -> np.ndarray:
    """int16 samples whose value is a deterministic function of the ABSOLUTE stream index."""
    idx = np.arange(start, start + n, dtype=np.int64)
    return ((idx % 60000) - 30000).astype(np.int16)


def make_config(**overrides) -> SegmenterConfig:
    """An explicit TEST config (sample counts: confirm 30, end silence 40, padding 20, pre-roll 100,
    timeout 200, min 50, max 500). Not a production calibration."""
    values = dict(
        sample_rate=SR,
        vad_frame_samples=FRAME,
        start_threshold=0.6,
        end_threshold=0.4,
        start_confirmation_s=0.03,
        end_silence_s=0.04,
        onset_padding_s=0.02,
        pre_roll_s=0.1,
        no_speech_timeout_s=0.2,
        min_utterance_s=0.05,
        max_utterance_s=0.5,
    )
    values.update(overrides)
    return SegmenterConfig(**values)


class ScriptedVadExhausted(RuntimeError):
    """The scripted engine was asked for more frames than its script holds."""


class ScriptedVadEngine(VadEngine):
    """TEST / SYNTHETIC: returns a supplied score sequence, one score per frame.

    reset() rewinds the script to its start (and counts the call). Running past the end of the
    script raises ScriptedVadExhausted, so a test that scores more frames than it planned fails
    loudly instead of silently reading zeros. Frames are recorded as copies; the engine never
    modifies the PCM it is given.
    """

    def __init__(self, scores: Sequence[float], frame_samples: int = FRAME, sample_rate: int = SR) -> None:
        self._scores = list(scores)
        self._frame_samples = frame_samples
        self._sample_rate = sample_rate
        self._index = 0
        self.reset_calls = 0
        self.seen_frames: List[np.ndarray] = []
        self.frames_were_read_only: List[bool] = []

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def frame_samples(self) -> int:
        return self._frame_samples

    @property
    def position(self) -> int:
        """How many scores have been consumed since the last reset."""
        return self._index

    def set_scores(self, scores: Sequence[float]) -> None:
        """Replace the script (does not count as a reset)."""
        self._scores = list(scores)
        self._index = 0

    def _score_frame(self, frame: np.ndarray) -> float:
        self.seen_frames.append(frame.copy())
        self.frames_were_read_only.append(not frame.flags.writeable)
        if self._index >= len(self._scores):
            raise ScriptedVadExhausted(f"script of {len(self._scores)} scores exhausted")
        score = self._scores[self._index]
        self._index += 1
        return score

    def reset(self) -> None:
        self.reset_calls += 1
        self._index = 0


def runs(*pairs: Tuple[float, int]) -> List[float]:
    """runs((0.0, 3), (0.9, 5)) -> three 0.0 scores then five 0.9 scores."""
    out: List[float] = []
    for score, count in pairs:
        out.extend([score] * count)
    return out


def region_scores(origin: int, n_frames: int, speech: Sequence[Tuple[int, int]],
                  frame: int = FRAME, speech_score: float = 0.9, silence_score: float = 0.0) -> List[float]:
    """Score script for frames starting at `origin`: speech_score where a frame lies fully inside any
    (start, end) stream range in `speech`, else silence_score."""
    out = []
    for i in range(n_frames):
        a, b = origin + i * frame, origin + (i + 1) * frame
        out.append(speech_score if any(a >= s and b <= e for s, e in speech) else silence_score)
    return out


def make_detection(end_sample: int) -> Detection:
    """A wake Detection whose end_sample is the end of the triggering detector chunk."""
    return Detection(
        keyword="hey_jarvis", score=0.9, threshold=0.5, chunk_index=0,
        end_sample=end_sample, stream_time_s=end_sample / SR, wall_time_s=0.0, processing_ms=0.0,
    )


def feed(seg: UtteranceSegmenter, start: int, total: int,
         block_sizes: Sequence[int] = (FRAME,)) -> Tuple[Optional[SegmenterResult], int]:
    """Push index_pcm(start .. start+total) in cycling block sizes. Returns (result or None, next start)."""
    pos, i, stop = start, 0, start + total
    while pos < stop:
        size = min(block_sizes[i % len(block_sizes)], stop - pos)
        result = seg.push(index_pcm(pos, size), pos)
        pos += size
        i += 1
        if result is not None:
            return result, pos
    return None, pos


def run_plain(config: SegmenterConfig, scores: Sequence[float], total: int,
              block_sizes: Sequence[int] = (FRAME,), activation: int = 0, finish: bool = True):
    """No-replay run: activate at `activation`, feed `total` samples from there, optionally finish().
    Returns (segmenter, engine, result)."""
    engine = ScriptedVadEngine(scores)
    seg = UtteranceSegmenter(config, engine)
    seg.activate(activation)
    result, _ = feed(seg, activation, total, block_sizes)
    if result is None and finish:
        result = seg.finish()
    return seg, engine, result


@dataclass
class WakeRun:
    seg: UtteranceSegmenter
    engine: ScriptedVadEngine
    buffer: RollingPcmBuffer
    snapshot: PcmSnapshot
    origin: int
    result: Optional[SegmenterResult]
    next_sample: int


def run_wake(config: SegmenterConfig, *, speech: Sequence[Tuple[int, int]], activation: int,
             buffered_until: int, buffer_capacity: int, stream_end: int,
             block: int = 7, live_block: int = FRAME, finish: bool = True,
             second_activation_at: Optional[int] = None) -> WakeRun:
    """The wake handoff, end to end, on scripted scores.

    The stream is index_pcm(0..); `speech` lists the stream ranges the scripted VAD scores as speech.
    The buffer is filled in blocks of `block` up to `buffered_until`, the segmenter is activated at
    `activation` with the buffer's snapshot, then live blocks run to `stream_end`. If
    `second_activation_at` is given, a second activate() with a fresh snapshot is attempted when the
    live stream reaches that sample.
    """
    buf = RollingPcmBuffer(buffer_capacity)
    pos = 0
    while pos < buffered_until:
        n = min(block, buffered_until - pos)
        buf.push(index_pcm(pos, n), pos)
        pos += n
    snap = buf.snapshot()
    origin = max(snap.start_sample, activation - config.pre_roll_samples)
    n_frames = (stream_end - origin) // FRAME + 20
    engine = ScriptedVadEngine(region_scores(origin, n_frames, speech))
    seg = UtteranceSegmenter(config, engine)
    seg.activate(activation, snap)
    result = seg.result
    nxt = snap.end_sample
    while result is None and nxt < stream_end:
        size = min(live_block, stream_end - nxt)
        result = seg.push(index_pcm(nxt, size), nxt)
        nxt += size
        buf.push(index_pcm(nxt - size, size), nxt - size)
        if result is None and second_activation_at is not None and nxt >= second_activation_at:
            second_activation_at = None
            seg.activate(nxt, buf.snapshot())
    if result is None and finish:
        result = seg.finish()
    return WakeRun(seg, engine, buf, snap, origin, result, nxt)
