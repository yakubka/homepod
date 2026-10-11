"""Engine-neutral speech-to-text contract (VESTA, Team Leader AI-2 software dependency).

An SttEngine turns ONE completed utterance (1-D int16 mono PCM at the engine's sample rate, normally
SpeechSegment.samples from audio/utterance.py) into ONE SttResult. That is all it does: no microphone, no
VAD, no wake word, no intent, no answer, no TTS. The base class owns validation, timing, text
normalisation, logging and the error contract; a backend only supplies _transcribe(), the same split the
VAD (audio/vad.py) and wake-word (wakeword/detector.py) layers use.

Contract, enforced by transcribe() for every engine:
  * samples is a 1-D, non-empty, int16 numpy array and sample_rate equals the engine's. Nothing is
    resampled, down-mixed or converted for the caller, and the caller's array is never modified (a backend
    receives a read-only view);
  * a result means inference COMPLETED. EMPTY_TEXT means "completed, nothing usable remained". Every
    failure raises a typed SttError and never becomes an empty transcript;
  * inference is synchronous on one bounded segment. There is no timeout and no cancellation: nothing here
    can terminate a running decode, so none is offered. The upstream segmenter bounds the utterance;
  * inference_ms covers the whole _transcribe() call, so a backend that decodes lazily must finish its
    decoding inside it.

Text normalisation at this layer is deliberately narrow: runs of whitespace collapse to one space and the
ends are stripped. No wake-prefix removal, spelling or semantic correction happens here.

PRIVACY: nothing in this module logs a transcript or audio. Only structural fields are logged.

Importing this module imports numpy and the project logger only: no STT engine, no model.
"""

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np

from config.logging_config import setup_logging

logger = setup_logging()


# ---------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------

class SttError(Exception):
    """Base class for every STT failure. A failure is never reported as an empty transcript."""


class SttRuntimeUnavailable(SttError):
    """The STT runtime library cannot be imported (missing, or a native library is blocked)."""


class SttModelNotFound(SttError):
    """A required local model file or directory does not exist."""


class SttModelLoadFailed(SttError):
    """The model files exist but could not be verified or loaded."""


class SttInputError(SttError, ValueError):
    """The audio handed to transcribe() breaks the input contract."""


class SttInferenceFailed(SttError):
    """The backend raised while decoding."""


class SttContractError(SttError):
    """The model or the decoder did not behave the way this adapter requires."""


# ---------------------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------------------

class SttStatus(Enum):
    OK = "OK"                      # inference completed and produced usable text
    EMPTY_TEXT = "EMPTY_TEXT"      # inference completed; no usable text remained


def normalize_text(raw: str) -> str:
    """Collapse every run of whitespace to one space and strip the ends. Nothing else."""
    return " ".join(raw.split())


def _is_real(value) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class SttResult:
    """What one completed transcription produced. Failures are exceptions, not results."""

    status: SttStatus
    text: str                    # normalised (see normalize_text); "" exactly when status is EMPTY_TEXT
    engine_id: str
    model_id: str
    audio_duration_s: float      # input length: samples / sample_rate
    inference_ms: float          # wall time of the complete decode, including lazy consumption

    def __post_init__(self) -> None:
        if not isinstance(self.status, SttStatus):
            raise SttContractError(f"SttResult.status must be an SttStatus, got {self.status!r}")
        if not isinstance(self.text, str):
            raise SttContractError(f"SttResult.text must be a str, got {type(self.text).__name__}")
        for name in ("engine_id", "model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise SttContractError(f"SttResult.{name} must be a non-empty str, got {value!r}")
        if not _is_real(self.audio_duration_s) or self.audio_duration_s <= 0:
            raise SttContractError(f"SttResult.audio_duration_s must be a finite number > 0, got {self.audio_duration_s!r}")
        if not _is_real(self.inference_ms) or self.inference_ms < 0:
            raise SttContractError(f"SttResult.inference_ms must be a finite number >= 0, got {self.inference_ms!r}")
        if self.text != normalize_text(self.text):
            raise SttContractError("SttResult.text must already be normalised")
        if (self.status is SttStatus.OK) != bool(self.text):
            raise SttContractError(f"status {self.status.value} and text presence disagree")


# ---------------------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------------------

class SttEngine(ABC):
    """Transcribe one completed utterance of int16 mono PCM."""

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter) -> None:
        """clock is injectable so timing can be tested without waiting; leave the default in production."""
        self._clock = clock

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Sample rate (Hz) the engine expects."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Short stable name of the engine implementation."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Identity of the model that decodes (never claims more verification than was done)."""

    @abstractmethod
    def _transcribe(self, samples: np.ndarray) -> str:
        """Decode one validated, read-only utterance COMPLETELY and return the raw text.

        Raise an SttError subclass on failure; any other exception is wrapped as SttInferenceFailed.
        """

    def _check_extra_input(self, samples: np.ndarray) -> None:
        """Backend-specific input limits (for example a maximum length). Raise SttInputError. Default: none."""

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> SttResult:
        """Validate the audio, decode it synchronously and return the SttResult."""
        self._validate(samples, sample_rate)
        self._check_extra_input(samples)

        view = samples.view()
        view.flags.writeable = False           # a backend can read the caller's PCM but never change it
        duration_s = samples.size / sample_rate

        start = self._clock()
        try:
            raw = self._transcribe(view)
        except SttError as exc:
            logger.error("event=stt_failed engine=%s model=%s error=%s", self.engine_id, self.model_id, type(exc).__name__)
            raise
        except Exception as exc:
            logger.error("event=stt_failed engine=%s model=%s error=%s", self.engine_id, self.model_id, type(exc).__name__)
            raise SttInferenceFailed(f"{self.engine_id} failed while decoding: {type(exc).__name__}") from exc
        inference_ms = (self._clock() - start) * 1000.0

        if not isinstance(raw, str):
            logger.error("event=stt_failed engine=%s model=%s error=SttContractError", self.engine_id, self.model_id)
            raise SttContractError(f"{self.engine_id} returned {type(raw).__name__}, not a str")

        text = normalize_text(raw)
        result = SttResult(
            status=SttStatus.OK if text else SttStatus.EMPTY_TEXT,
            text=text,
            engine_id=self.engine_id,
            model_id=self.model_id,
            audio_duration_s=duration_s,
            inference_ms=inference_ms,
        )
        # structural fields only: never the transcript, never audio
        logger.info(
            "event=stt_done status=%s engine=%s model=%s audio_duration_s=%.3f inference_ms=%.1f text_chars=%d",
            result.status.value, result.engine_id, result.model_id, result.audio_duration_s, result.inference_ms, len(text),
        )
        return result

    def transcribe_segment(self, segment) -> SttResult:
        """Convenience for audio.utterance.SpeechSegment (duck-typed: .samples and .sample_rate)."""
        return self.transcribe(segment.samples, segment.sample_rate)

    def _validate(self, samples, sample_rate) -> None:
        if not isinstance(samples, np.ndarray):
            raise SttInputError(f"samples must be a numpy array, got {type(samples).__name__}")
        if samples.dtype != np.int16:
            raise SttInputError(f"samples must be int16, got {samples.dtype}")
        if samples.ndim != 1:
            raise SttInputError(f"samples must be 1-D, got shape {samples.shape}")
        if samples.size == 0:
            raise SttInputError("samples must not be empty")
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, np.integer)):
            raise SttInputError(f"sample_rate must be an integer, got {sample_rate!r}")
        if sample_rate != self.sample_rate:
            raise SttInputError(f"sample_rate is {sample_rate} Hz but the engine requires {self.sample_rate} Hz (no resampling is done)")
