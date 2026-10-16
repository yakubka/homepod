"""Engine-neutral text-to-speech contract (VESTA, Team Leader spoken-output foundation).

A TtsEngine turns ONE piece of text into ONE SpeechAudio: mono int16 PCM at the voice's native sample rate. That is all it
does. It knows nothing about speakers, devices or playback (audio/playback.py owns those), and nothing about what the text
means. The base class owns validation, timing, the typed-error contract and logging; a backend supplies only _synthesize(),
the same split speech/stt.py and audio/vad.py use.

Contract, enforced by synthesize() for every engine:
  * the text is a str that is not empty, not whitespace-only and contains at least one letter or digit. Punctuation-only
    text is refused here because a backend may happily turn it into a burst of noise or silence;
  * a result is real speech audio: 1-D int16, non-empty, at the engine's sample rate, not all zeros. Every failure raises a
    typed TtsError; no failure ever becomes empty or silent audio;
  * nothing is resampled. The audio keeps the voice's native rate; a playback stage decides what to do about devices;
  * synthesis is synchronous and has no timeout or cancellation (nothing here can stop a running synthesis);
  * synthesis_ms covers the whole _synthesize() call, so a backend that streams must finish inside it.

SpeechAudio carries audio and provenance only: no text, no intent, no answer.

PRIVACY: nothing here logs the text or the audio. Only the text LENGTH, the audio duration, the engine and voice ids, the
synthesis time and the error class are logged.
"""

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from config.logging_config import setup_logging

logger = setup_logging()


# ---------------------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------------------

class TtsError(Exception):
    """Base class for every TTS failure. A failure never becomes empty or silent audio."""


class TtsRuntimeUnavailable(TtsError):
    """The synthesis runtime library cannot be imported (missing, or a native library is blocked)."""


class TtsModelNotFound(TtsError):
    """A required local voice file or directory does not exist."""


class TtsModelLoadFailed(TtsError):
    """The voice files exist but could not be verified or loaded."""


class TtsModelHashMismatch(TtsModelLoadFailed):
    """A local voice file has the wrong size or SHA-256: it is not the approved voice package."""


class TtsInputError(TtsError, ValueError):
    """The text handed to synthesize() breaks the input contract."""


class TtsSynthesisFailed(TtsError):
    """The backend raised while synthesizing."""


class TtsContractError(TtsError):
    """The voice or the backend did not behave the way this contract requires."""


# ---------------------------------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------------------------------

def _is_real(value) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True, eq=False)
class SpeechAudio:
    """One synthesized utterance: owned, read-only, mono int16 PCM at the voice's native sample rate. No text, no intent."""

    samples: np.ndarray
    sample_rate: int
    channels: int
    duration_s: float = field(init=False)       # derived: samples / sample_rate, never supplied
    tts_engine_id: str
    voice_id: str
    synthesis_ms: float

    def __post_init__(self) -> None:
        s = self.samples
        if not isinstance(s, np.ndarray):
            raise TtsContractError(f"SpeechAudio.samples must be a numpy array, got {type(s).__name__}")
        if s.dtype != np.int16:
            raise TtsContractError(f"SpeechAudio.samples must be int16, got {s.dtype}")
        if s.ndim != 1:
            raise TtsContractError(f"SpeechAudio.samples must be 1-D (mono), got shape {s.shape}")
        if s.size == 0:
            raise TtsContractError("SpeechAudio.samples must not be empty")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, (int, np.integer)) or self.sample_rate <= 0:
            raise TtsContractError(f"SpeechAudio.sample_rate must be a positive integer, got {self.sample_rate!r}")
        if isinstance(self.channels, bool) or self.channels != 1:
            raise TtsContractError(f"SpeechAudio.channels must be 1 (mono), got {self.channels!r}")
        for name in ("tts_engine_id", "voice_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise TtsContractError(f"SpeechAudio.{name} must be a non-empty str, got {value!r}")
        if not _is_real(self.synthesis_ms) or self.synthesis_ms < 0:
            raise TtsContractError(f"SpeechAudio.synthesis_ms must be a finite number >= 0, got {self.synthesis_ms!r}")
        owned = np.array(s, dtype=np.int16, copy=True)          # never alias the caller's array
        owned.setflags(write=False)
        object.__setattr__(self, "samples", owned)
        object.__setattr__(self, "sample_rate", int(self.sample_rate))
        object.__setattr__(self, "duration_s", owned.size / int(self.sample_rate))


# ---------------------------------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------------------------------

def check_text(text) -> None:
    """Raise TtsInputError unless `text` can be synthesized: a str with at least one letter or digit."""
    if not isinstance(text, str):
        raise TtsInputError(f"text must be a str, got {type(text).__name__}")
    if not text.strip():
        raise TtsInputError("text must not be empty or whitespace-only")
    if not any(ch.isalnum() for ch in text):
        raise TtsInputError("text must contain at least one letter or digit (punctuation-only text is refused)")


class TtsEngine(ABC):
    """Synthesize text into SpeechAudio."""

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter) -> None:
        """clock is injectable so timing can be tested without waiting; leave the default in production."""
        self._clock = clock

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Short stable name of the engine implementation."""

    @property
    @abstractmethod
    def voice_id(self) -> str:
        """Identity of the voice (never claims more verification than was done)."""

    @property
    @abstractmethod
    def language(self) -> str:
        """Language tag of the voice, for example en-US."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """The voice's native sample rate (Hz). Audio is returned at exactly this rate."""

    @abstractmethod
    def _synthesize(self, text: str) -> np.ndarray:
        """Synthesize the whole text and return 1-D int16 mono PCM at `sample_rate`. Raise a TtsError subclass on failure;
        any other exception is wrapped as TtsSynthesisFailed."""

    def synthesize(self, text: str) -> SpeechAudio:
        """Validate the text, synthesize it synchronously and return the SpeechAudio."""
        check_text(text)
        start = self._clock()
        try:
            pcm = self._synthesize(text)
        except TtsError as exc:
            logger.error("event=tts_failed engine=%s voice=%s error=%s", self.engine_id, self.voice_id, type(exc).__name__)
            raise
        except Exception as exc:
            logger.error("event=tts_failed engine=%s voice=%s error=%s", self.engine_id, self.voice_id, type(exc).__name__)
            raise TtsSynthesisFailed(f"{self.engine_id} failed while synthesizing: {type(exc).__name__}") from exc
        synthesis_ms = (self._clock() - start) * 1000.0

        if not isinstance(pcm, np.ndarray) or pcm.dtype != np.int16 or pcm.ndim != 1 or pcm.size == 0:
            logger.error("event=tts_failed engine=%s voice=%s error=TtsContractError", self.engine_id, self.voice_id)
            raise TtsContractError(f"{self.engine_id} must return non-empty 1-D int16 PCM")
        if not pcm.any():
            logger.error("event=tts_failed engine=%s voice=%s error=TtsContractError", self.engine_id, self.voice_id)
            raise TtsContractError(f"{self.engine_id} returned silent audio for speakable text; refusing to pass it on")
        audio = SpeechAudio(samples=pcm, sample_rate=self.sample_rate, channels=1, tts_engine_id=self.engine_id,
                            voice_id=self.voice_id, synthesis_ms=synthesis_ms)
        # structural fields only: never the text, never the audio
        logger.info("event=tts_done engine=%s voice=%s text_chars=%d audio_duration_s=%.3f synthesis_ms=%.1f",
                    audio.tts_engine_id, audio.voice_id, len(text), audio.duration_s, audio.synthesis_ms)
        return audio
