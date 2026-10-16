"""Engine-independent VAD contract (VESTA, Week 7 Task 1).

A VadEngine turns ONE fixed-size frame of int16 PCM into ONE speech score in
[0, 1]. That is all the utterance segmenter (audio/utterance.py) needs. All
segmentation policy - thresholds, hysteresis, durations, pre-roll - lives in
the segmenter, never in an engine, the same split Week 2 uses for wake words
(the base class owns the decision rule, a backend only scores).

This module imports no model library and knows nothing about any particular
engine. An engine's private details (context buffers, recurrent state, tensor
names) belong in that engine's own adapter module, not in this contract.

Contract, enforced by process_frame() for every engine:
  * the frame is a 1-D int16 numpy array of exactly `frame_samples` samples;
  * the score is a finite number within [0.0, 1.0];
  * reset() clears whatever the engine remembers between frames, so the next
    frame is scored as the first frame of a new utterance.

Nothing here is validated against real speech or a real engine. The only
engine in the repository at this point is the scripted test double in
tests/voice_synthetic.py (TEST / SYNTHETIC).
"""

import math
from abc import ABC, abstractmethod

import numpy as np


class VadContractError(ValueError):
    """An engine call or an engine result broke the VadEngine contract."""


class VadFrameError(VadContractError):
    """The frame handed to an engine is not what the engine requires."""


class VadScoreError(VadContractError):
    """An engine returned a score that is not a finite number in [0, 1]."""


class VadEngine(ABC):
    """Score fixed-size int16 frames for the presence of speech."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Sample rate (Hz) the engine expects."""

    @property
    @abstractmethod
    def frame_samples(self) -> int:
        """Exact number of NEW stream samples per process_frame() call.

        Engines that need extra history (a rolling context, for example) keep
        it internally; it is never counted as stream audio here.
        """

    @abstractmethod
    def _score_frame(self, frame: np.ndarray) -> float:
        """Score one validated frame. Return a number in [0, 1]."""

    @abstractmethod
    def reset(self) -> None:
        """Forget everything remembered between frames."""

    def process_frame(self, frame: np.ndarray) -> float:
        """Validate `frame`, score it, validate and return the score."""
        expected = self.frame_samples
        if not isinstance(frame, np.ndarray):
            raise VadFrameError(f"frame must be a numpy array, got {type(frame).__name__}")
        if frame.dtype != np.int16:
            raise VadFrameError(f"frame must be int16, got {frame.dtype}")
        if frame.ndim != 1:
            raise VadFrameError(f"frame must be 1-D, got shape {frame.shape}")
        if frame.size != expected:
            raise VadFrameError(f"frame has {frame.size} samples, engine requires {expected}")

        score = self._score_frame(frame)
        return check_score(score)


def check_score(score) -> float:
    """Return `score` as a float, or raise VadScoreError if it is not finite and within [0, 1]."""
    if isinstance(score, bool) or not isinstance(score, (int, float, np.integer, np.floating)):
        raise VadScoreError(f"score must be a real number, got {type(score).__name__}")
    value = float(score)
    if not math.isfinite(value):
        raise VadScoreError(f"score must be finite, got {value!r}")
    if not 0.0 <= value <= 1.0:
        raise VadScoreError(f"score must be within [0, 1], got {value!r}")
    return value
