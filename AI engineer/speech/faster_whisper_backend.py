"""faster-whisper (CTranslate2) backend behind the SttEngine contract (VESTA, Team Leader AI-2 software dependency).

FasterWhisperEngine turns ONE completed utterance (int16, mono, 16 kHz) into text with the English-only
Whisper tiny.en model. It owns only what is specific to this engine: local snapshot verification, model
construction, PCM conversion, the decoder call, consuming the lazy segment iterable and joining the text.
Validation, timing, normalisation and logging live in speech/stt.py. It does no VAD, wake word,
segmentation, answering or TTS.

STATUS: SOFTWARE-VALIDATED ADAPTER. It says nothing about recognition accuracy: STT ACCURACY NOT MEASURED,
HALLUCINATION MITIGATION NOT YET VALIDATED, nothing here ran on real speech, a microphone, a ReSpeaker or a
Raspberry Pi. Pi 5 / aarch64 behaviour is PENDING TARGET-HARDWARE VERIFICATION.

THE MODEL (single authoritative record: FASTER_WHISPER_TINY_EN below)
  repo      Systran/faster-whisper-tiny.en (CTranslate2 conversion of OpenAI Whisper tiny.en)
  revision  0d3d19a32d3338f10357c0889762bd8d64bbdeba (pinned commit, never a moving branch)
  license   MIT (Hub model card at that revision)
  directory models/faster-whisper-tiny.en-0d3d19a3 (git-ignored), fetched ONCE by an explicit development
            step (commands in TEAM_LEADER_AI_REPORT.md). This module NEVER downloads anything: it receives a LOCAL
            DIRECTORY, verifies every runtime file against the manifest (size and SHA-256) BEFORE loading,
            and builds the model with local_files_only=True. A directory path means faster-whisper itself
            performs no Hub lookup either (checked in the pinned 1.2.1 source).

IMPORTS ARE LAZY. Importing this module imports numpy and speech.stt only. faster_whisper, ctranslate2 and
av are imported when an engine is built from files, never earlier (the dependency gate saw a first import of
av take ~54 s on the Windows development PC; the cause is NOT known).

DECODER POLICY (faster-whisper 1.2.1; the signature and the fallback loop were read from the installed source)
  language="en"                       the model is English-only
  condition_on_previous_text=False    nothing is carried between decoding windows
  vad_filter=False                    VESTA owns VAD upstream (audio/utterance.py)
  temperature=0.0                     ONE temperature: faster-whisper then never re-decodes
  beam_size=5                         UPSTREAM DEFAULT, NOT CALIBRATED (stated explicitly so it is not an accident)
Everything else keeps the upstream default. Consequences that are NOT hallucination filters: with a single
temperature, compression_ratio_threshold and log_prob_threshold cannot trigger a second decode, because no
further temperature exists. faster-whisper's built-in no-speech skip (no_speech_threshold with
log_prob_threshold) is still active and can drop a window, which then reads as EMPTY_TEXT. No custom
quality threshold is added here. HALLUCINATION MITIGATION NOT YET VALIDATED: Stage A recordings must show
what the decoder really does.

PCM: float32 = int16 / 32768.0, the same scaling faster_whisper.audio.decode_audio applies. A power-of-two
divisor is exact, so -32768 maps to -1.0 and 32767 to 0.999969482421875. No resampling, no channel mixing, no
gain, no second normalisation. The caller's array is only read.

LAZY DECODING. WhisperModel.transcribe() returns (segments, info) where `segments` is a GENERATOR: no decoding
happens until it is iterated. _transcribe() therefore consumes every segment before it returns, and the base
class times that whole call.

EXECUTION POLICY: device="cpu", compute_type="int8" (DEVELOPMENT EXECUTION POLICY for the Windows AI-2
integration backend, not a Pi optimisation). max_audio_s=30 s is a DEVELOPMENT POLICY, NOT CALIBRATED: it is
Whisper's decoding window and keeps decoding to one window. It is a property of this backend, not of the
STT contract; the segmenter remains responsible for bounding utterances.

PROVENANCE. snapshot_verified is True ONLY when the engine was built from files whose size and SHA-256 matched
the manifest. An injected model (the test hook) is never verified: snapshot_verified is False, model_id says
UNVERIFIED-INJECTED-MODEL and the log line quotes no revision or hash.
"""

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np

from config.logging_config import setup_logging
from speech.stt import (
    SttContractError, SttEngine, SttInputError, SttModelLoadFailed, SttModelNotFound, SttRuntimeUnavailable,
)

logger = setup_logging()


# ---------------------------------------------------------------------------------------
# errors specific to this backend
# ---------------------------------------------------------------------------------------

class SttModelHashMismatch(SttModelLoadFailed):
    """A local model file has the wrong size or the wrong SHA-256: it is not the approved snapshot."""


# ---------------------------------------------------------------------------------------
# the approved model snapshot
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelFile:
    path: str            # relative to the model directory, forward slashes
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class FasterWhisperModelSpec:
    """Everything the runtime needs to know about the approved snapshot. Only inference-relevant files are listed."""

    name: str
    repo: str
    revision: str
    license: str
    directory_name: str
    acquisition_date: str
    files: Tuple[ModelFile, ...]

    @property
    def model_id(self) -> str:
        return f"{self.repo}@{self.revision}"

    @property
    def total_size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


FASTER_WHISPER_TINY_EN = FasterWhisperModelSpec(
    name="Whisper tiny.en (CTranslate2 conversion, English only)",
    repo="Systran/faster-whisper-tiny.en",
    revision="0d3d19a32d3338f10357c0889762bd8d64bbdeba",
    license="MIT",
    directory_name="faster-whisper-tiny.en-0d3d19a3",
    acquisition_date="2026-10-11",
    files=(
        ModelFile("config.json", 2317, "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5"),
        ModelFile("model.bin", 75537502, "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a"),
        ModelFile("tokenizer.json", 2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
        ModelFile("vocabulary.txt", 422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
    ),
)

SAMPLE_RATE = 16000
PCM_FULL_SCALE = 32768.0        # int16 full scale; the divisor faster-whisper's own decode_audio uses
DEFAULT_MAX_AUDIO_S = 30.0      # DEVELOPMENT POLICY, NOT CALIBRATED (see the module docstring)
ENGINE_ID = "faster-whisper"
UNVERIFIED_MODEL_ID = "UNVERIFIED-INJECTED-MODEL"

# DEVELOPMENT EXECUTION POLICY (Windows AI-2 backend), not a Pi optimisation.
DEVICE = "cpu"
COMPUTE_TYPE = "int8"

# Passed to WhisperModel.transcribe() exactly as written; pinned by tests. beam_size 5 is the UPSTREAM DEFAULT,
# written out so it is explicit, and NOT CALIBRATED.
DECODER_OPTIONS = {
    "language": "en",
    "condition_on_previous_text": False,
    "vad_filter": False,
    "temperature": 0.0,
    "beam_size": 5,
}

_HASH_CHUNK = 1 << 20


def resolve_model_dir(path=None, spec: FasterWhisperModelSpec = FASTER_WHISPER_TINY_EN) -> Path:
    """Default: models/<spec.directory_name> under the project root (the Silero / YOLOX convention)."""
    root = Path(__file__).resolve().parent.parent
    if path is None:
        return root / "models" / spec.directory_name
    p = Path(path)
    return p if p.is_absolute() else root / p


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(_HASH_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_model_snapshot(model_dir: Path, spec: FasterWhisperModelSpec = FASTER_WHISPER_TINY_EN) -> int:
    """Check every manifest file (existence, size, SHA-256). Return the verified byte total.

    Extra files in the directory (for example the Hub download cache) are ignored: they are not part of the
    inference contract. Raises SttModelNotFound or SttModelHashMismatch.
    """
    if not model_dir.is_dir():
        raise SttModelNotFound(f"model directory not found: {model_dir}")
    for entry in spec.files:
        path = model_dir / entry.path
        if not path.is_file():
            raise SttModelNotFound(f"model file missing: {entry.path}")
        actual_size = path.stat().st_size
        if actual_size != entry.size_bytes:
            raise SttModelHashMismatch(f"{entry.path}: size {actual_size} != expected {entry.size_bytes}")
        if _sha256_of(path) != entry.sha256:
            raise SttModelHashMismatch(f"{entry.path}: SHA-256 does not match the approved snapshot")
    return spec.total_size_bytes


def _default_model_factory(path: str, **kwargs):
    """Import faster-whisper NOW (never earlier) and build a WhisperModel."""
    try:
        from faster_whisper import WhisperModel
    except (ImportError, OSError) as exc:        # includes a native DLL blocked by Windows Application Control
        raise SttRuntimeUnavailable(f"faster-whisper cannot be imported: {type(exc).__name__}: {exc}") from exc
    return WhisperModel(path, **kwargs)


# ---------------------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------------------

class FasterWhisperEngine(SttEngine):
    """Whisper tiny.en via faster-whisper: one int16 utterance in, one SttResult out."""

    def __init__(
        self,
        model_dir=None,
        *,
        spec: FasterWhisperModelSpec = FASTER_WHISPER_TINY_EN,
        max_audio_s: float = DEFAULT_MAX_AUDIO_S,
        model=None,
        model_factory: Optional[Callable] = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """model_dir: local snapshot directory (default models/<spec.directory_name>).
        max_audio_s: longest accepted input (DEVELOPMENT POLICY, NOT CALIBRATED).
        model: a ready-made WhisperModel-like object - TEST HOOK that skips the directory, the hashes and the
        runtime; it is still contract-checked but never counts as verified. Leave None in production.
        model_factory: replaces the lazy faster_whisper import (test hook for the construction path)."""
        super().__init__(clock=clock)
        if model is not None and model_dir is not None:
            raise ValueError("pass either model_dir or model, not both")
        if isinstance(max_audio_s, bool) or not isinstance(max_audio_s, (int, float)) or not math.isfinite(max_audio_s) or max_audio_s <= 0:
            raise ValueError(f"max_audio_s must be a finite number > 0, got {max_audio_s!r}")
        self._spec = spec
        self._max_audio_s = float(max_audio_s)
        self._model_dir: Optional[Path] = None
        self._snapshot_verified = False
        self._load_ms: Optional[float] = None

        if model is None:
            self._model_dir = resolve_model_dir(model_dir, spec)
            verified_bytes = verify_model_snapshot(self._model_dir, spec)        # BEFORE anything is loaded
            factory = model_factory or _default_model_factory
            start = clock()
            try:
                model = factory(str(self._model_dir), device=DEVICE, compute_type=COMPUTE_TYPE, local_files_only=True)
            except SttRuntimeUnavailable:
                raise
            except Exception as exc:
                logger.error("event=stt_model_load_failed model=%s error=%s", spec.model_id, type(exc).__name__)
                raise SttModelLoadFailed(f"could not load {spec.model_id}: {type(exc).__name__}: {exc}") from exc
            self._load_ms = (clock() - start) * 1000.0
            self._snapshot_verified = True      # the manifest check above passed, and the model was built from that directory

        self._check_model_contract(model)
        self._model = model

        if self._snapshot_verified:
            logger.info(
                "event=stt_engine_ready snapshot_verified=true model=%s files=%d total_bytes=%d device=%s compute_type=%s max_audio_s=%.1f load_ms=%.0f",
                spec.model_id, len(spec.files), spec.total_size_bytes, DEVICE, COMPUTE_TYPE, self._max_audio_s, self._load_ms,
            )
        else:
            # no directory, no hash check: nothing here may suggest the approved snapshot was verified
            logger.info("event=stt_engine_ready snapshot_verified=false source=injected_model max_audio_s=%.1f", self._max_audio_s)

    @staticmethod
    def _check_model_contract(model) -> None:
        """The model must decode at 16 kHz and be English-only (the .en model); anything else is a contract error."""
        try:
            rate = model.feature_extractor.sampling_rate
            multilingual = model.model.is_multilingual
        except AttributeError as exc:
            raise SttContractError(f"model does not expose the expected attributes: {exc}") from exc
        if rate != SAMPLE_RATE:
            raise SttContractError(f"model feature extractor expects {rate} Hz, this adapter requires {SAMPLE_RATE} Hz")
        if multilingual is not False:
            raise SttContractError("the loaded model is multilingual (or its language support is unknown); an English-only .en model is required")

    # -- SttEngine interface ---------------------------------------------

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    @property
    def engine_id(self) -> str:
        return ENGINE_ID

    @property
    def model_id(self) -> str:
        return self._spec.model_id if self._snapshot_verified else UNVERIFIED_MODEL_ID

    @property
    def snapshot_verified(self) -> bool:
        """True only when built from a directory whose every manifest file matched (never for an injected model)."""
        return self._snapshot_verified

    @property
    def max_audio_s(self) -> float:
        return self._max_audio_s

    @property
    def load_ms(self) -> Optional[float]:
        """Time spent constructing the model (not hashing); None for an injected model."""
        return self._load_ms

    def _check_extra_input(self, samples: np.ndarray) -> None:
        duration_s = samples.size / self.sample_rate
        if duration_s > self._max_audio_s:
            raise SttInputError(f"audio is {duration_s:.1f} s, longer than this backend's max_audio_s of {self._max_audio_s:.1f} s")

    def _transcribe(self, samples: np.ndarray) -> str:
        audio = samples.astype(np.float32) / np.float32(PCM_FULL_SCALE)       # a new array; the caller's PCM is untouched
        output = self._model.transcribe(audio, **DECODER_OPTIONS)
        if not isinstance(output, tuple) or len(output) != 2:
            raise SttContractError("WhisperModel.transcribe() did not return a (segments, info) pair")
        segments, _info = output
        pieces = []
        for segment in segments:           # the iterable is lazy: decoding happens HERE, inside the timed call
            text = getattr(segment, "text", None)
            if not isinstance(text, str):
                raise SttContractError(f"a decoded segment has no str text (got {type(text).__name__})")
            pieces.append(text.strip())
        return " ".join(p for p in pieces if p)
