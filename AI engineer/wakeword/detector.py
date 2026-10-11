"""Engine-independent wake-word detector interface (VESTA, WP2.2).

The rest of the pipeline depends only on WakeWordDetector. An engine
(openWakeWord now, possibly Porcupine later) is added by subclassing it
and implementing two small hooks - _score_chunk() and _reset_backend().
This module must never import an engine library.

Threshold and cooldown live HERE, not in the backends, so every engine
is judged by exactly the same decision rule. That matters for the Week 2
benchmark: differences between engines should come from the models, not
from each backend re-implementing debouncing slightly differently.

All timing is derived from the count of samples processed (deterministic
and identical for live and WAV replay); wall-clock time is recorded
separately in Detection.wall_time_s for live latency measurement.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Mapping, Optional

import numpy as np

from audio.file_source import EndOfAudio
from audio.reframer import Reframer


@dataclass(frozen=True)
class Detection:
    """One accepted wake-word event, carrying what evaluation needs."""

    keyword: str
    score: float           # engine confidence of the accepted keyword
    threshold: float       # threshold in force when it fired
    chunk_index: int       # 0-based index of the chunk that triggered
    end_sample: int        # samples processed up to the END of that chunk
    stream_time_s: float   # end_sample / sample_rate (deterministic)
    wall_time_s: float     # time.monotonic() at detection (live latency)
    processing_ms: float   # compute time spent on the triggering chunk


class WakeWordDetector(ABC):
    """Consume fixed-size audio chunks; report accepted wake-word events."""

    def __init__(self, threshold: float = 0.5, cooldown_s: float = 2.0) -> None:
        """
        threshold:  minimum score (0..1) for a detection.
        cooldown_s: after an accepted detection, further ones are ignored
                    until this much audio has been processed, so one spoken
                    wake word cannot fire several times. 0 disables it.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be within [0, 1], got {threshold}")
        if cooldown_s < 0:
            raise ValueError(f"cooldown_s must be >= 0, got {cooldown_s}")
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clear_stream_state()

    def _clear_stream_state(self) -> None:
        self._samples_processed = 0
        self._chunks_processed = 0
        self._last_detection_sample: Optional[int] = None
        self._suppressed = 0
        self._last_scores: Mapping[str, float] = {}

    # -- what a backend must provide ----------------------------------

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Sample rate (Hz) the engine expects."""

    @property
    @abstractmethod
    def chunk_samples(self) -> int:
        """Exact number of int16 samples per process() call."""

    @abstractmethod
    def _score_chunk(self, chunk: np.ndarray) -> Mapping[str, float]:
        """Run the engine on one validated chunk; return {keyword: score}."""

    @abstractmethod
    def _reset_backend(self) -> None:
        """Clear the engine's internal audio/feature history."""

    # -- shared behaviour ---------------------------------------------

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def cooldown_s(self) -> float:
        return self._cooldown_s

    @property
    def samples_processed(self) -> int:
        return self._samples_processed

    @property
    def chunks_processed(self) -> int:
        return self._chunks_processed

    @property
    def suppressed_detections(self) -> int:
        """Threshold crossings ignored because of the cooldown."""
        return self._suppressed

    @property
    def last_scores(self) -> Mapping[str, float]:
        """Raw per-keyword scores of the most recent chunk. Exposed so the
        benchmark can sweep thresholds offline without re-running inference."""
        return self._last_scores

    def process(self, chunk: np.ndarray) -> Optional[Detection]:
        """Feed one chunk (1-D int16, exactly chunk_samples long).

        Returns a Detection if the wake word fired on this chunk, else None.
        """
        if not isinstance(chunk, np.ndarray) or chunk.ndim != 1 or chunk.dtype != np.int16:
            raise ValueError("chunk must be a 1-D int16 numpy array")
        if chunk.size != self.chunk_samples:
            raise ValueError(f"chunk has {chunk.size} samples, expected {self.chunk_samples}")

        start = time.perf_counter()
        scores = dict(self._score_chunk(chunk))
        processing_ms = (time.perf_counter() - start) * 1000.0

        chunk_index = self._chunks_processed
        self._chunks_processed += 1
        self._samples_processed += chunk.size
        self._last_scores = scores

        if not scores:
            return None
        keyword, score = max(scores.items(), key=lambda item: item[1])
        if score < self._threshold:
            return None

        # Cooldown is measured from the last ACCEPTED detection, in stream
        # samples. Suppressed crossings do not extend it.
        if self._last_detection_sample is not None:
            cooldown_samples = round(self._cooldown_s * self.sample_rate)
            if self._samples_processed - self._last_detection_sample < cooldown_samples:
                self._suppressed += 1
                return None

        self._last_detection_sample = self._samples_processed
        return Detection(
            keyword=keyword,
            score=float(score),
            threshold=self._threshold,
            chunk_index=chunk_index,
            end_sample=self._samples_processed,
            stream_time_s=self._samples_processed / self.sample_rate,
            wall_time_s=time.monotonic(),
            processing_ms=processing_ms,
        )

    def reset(self) -> None:
        """Start a NEW stream: clears the engine history, cooldown and the
        sample clock. Live code should not call this after each detection -
        cooldown is already handled internally and reset() restarts time at 0.
        """
        self._reset_backend()
        self._clear_stream_state()


def run_on_source(source, detector: WakeWordDetector) -> List[Detection]:
    """Replay a finite AudioSource through Reframer -> detector.

    This is the glue the Week 2 benchmark and the demo will reuse:

        WavFileSource -> Reframer(detector.chunk_samples) -> detector.process

    It starts and stops the source itself and resets the detector first.
    The trailing partial chunk is zero-padded so the last few ms of the
    file still reach the detector. Only for sources that end (EndOfAudio);
    a live microphone never does.
    """
    if source.sample_rate != detector.sample_rate:
        raise ValueError(
            f"source is {source.sample_rate} Hz but detector expects {detector.sample_rate} Hz"
        )
    if source.channels != 1:
        raise ValueError(f"source must be mono, got {source.channels} channels")

    reframer = Reframer(detector.chunk_samples)
    detections: List[Detection] = []
    detector.reset()

    source.start()
    try:
        while True:
            try:
                block = source.read_frame()
            except EndOfAudio:
                break
            for chunk in reframer.push(block):
                detection = detector.process(chunk)
                if detection is not None:
                    detections.append(detection)

        tail = reframer.flush(pad=True)
        if tail is not None:
            detection = detector.process(tail)
            if detection is not None:
                detections.append(detection)
    finally:
        source.stop()

    return detections
