"""sherpa-onnx backend behind the TtsEngine contract (VESTA, Team Leader spoken-output foundation).

SherpaOnnxTtsEngine turns text into SpeechAudio with the English Piper-format voice en_US-lessac-medium, run by the
sherpa-onnx runtime (an ONNX runtime wrapper; no Piper Python package, no network). It owns only what is specific to this
engine: local voice-package verification, model construction, voice-config checks, the generate() call and the float ->
int16 conversion. Validation, timing and logging live in speech/tts.py. It plays nothing: playback is audio/playback.py.

STATUS: SOFTWARE-VALIDATED ADAPTER on the Windows development PC. It says nothing about voice quality or naturalness
(nobody has judged it), nothing about Raspberry Pi speed, and nothing about any speaker.

THE VOICE (single authoritative record: speech/voices/vits-piper-en_US-lessac-medium.manifest.json)
  packaged by  k2-fsa/sherpa-onnx release tag "tts-models", asset vits-piper-en_US-lessac-medium.tar.bz2, pinned by its
               SHA-256 (the release tag is mutable)
  upstream     rhasspy/piper-voices commit c10ece1a..., en/en_US/lessac/medium (sherpa repackages the model, so its .onnx
               is not byte-identical to Piper's original)
  directory    models/vits-piper-en_US-lessac-medium-9e3febfa (git-ignored), fetched ONCE by an explicit development step
               (commands in TEAM_LEADER_AI_REPORT.md). This module NEVER downloads anything: it receives a LOCAL DIRECTORY
               and verifies EVERY file of the manifest (existence, size, SHA-256) BEFORE the model is loaded.
  LICENCE      ACADEMIC / NON-COMMERCIAL DEVELOPMENT ASSET. The Lessac dataset is licensed for non-commercial use only; the
               bundled espeak-ng data is GPL-family; the sherpa-onnx runtime is Apache-2.0. The voice files are not committed
               and are not redistributed from this repository. No legal claim is made beyond the source licence statements.

IMPORTS ARE LAZY. Importing this module imports numpy, speech.tts and the standard library only. sherpa_onnx is imported
when an engine is built from files, never earlier.

DEVELOPMENT REPRODUCIBILITY POLICY (not a quality setting): noise_scale = 0 and noise_scale_w = 0. In the gate, the default
(stochastic) VITS sampling gave a different length on nearly every repeat, while these two zero values gave bit-identical
audio. Prosody has NOT been tuned and the effect on naturalness has NOT been judged.
DEVELOPMENT EXECUTION POLICY (not calibrated): CPU provider, 2 threads, speaker id 0, speed 1.0.

float -> int16: samples * 32767, rounded to nearest, clipped to [-32768, 32767]. Nothing is resampled; the audio keeps the
voice's native 22050 Hz.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np

from config.logging_config import setup_logging
from speech.tts import (
    TtsContractError, TtsEngine, TtsModelHashMismatch, TtsModelLoadFailed, TtsModelNotFound, TtsRuntimeUnavailable,
)

logger = setup_logging()

ENGINE_ID = "sherpa-onnx"
UNVERIFIED_VOICE_ID = "UNVERIFIED-INJECTED-VOICE"
MANIFEST_PATH = Path(__file__).resolve().parent / "voices" / "vits-piper-en_US-lessac-medium.manifest.json"

# DEVELOPMENT REPRODUCIBILITY POLICY: bit-reproducible synthesis. NOT a quality-optimised setting.
NOISE_SCALE = 0.0
NOISE_SCALE_W = 0.0
# DEVELOPMENT EXECUTION POLICY (CPU, not calibrated, not a Pi setting)
PROVIDER = "cpu"
NUM_THREADS = 2
SPEAKER_ID = 0
SPEED = 1.0
PCM_FULL_SCALE = 32767.0

_HASH_CHUNK = 1 << 20


# ---------------------------------------------------------------------------------------------------
# the approved voice package
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class VoiceFile:
    path: str            # relative to the voice directory, forward slashes
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class VoiceManifest:
    """Everything the runtime needs to know about the approved voice package."""

    voice_id: str
    language: str
    sample_rate: int
    num_speakers: int
    model_file: str
    tokens_file: str
    espeak_data_dir: str
    directory_name: str
    files: Tuple[VoiceFile, ...]
    total_size_bytes: int
    release_tag: str
    asset_sha256: str
    licence_status: str


def load_manifest(path=MANIFEST_PATH) -> VoiceManifest:
    """Read and validate a manifest file. Raises TtsModelNotFound (missing) or TtsContractError (malformed)."""
    p = Path(path)
    if not p.is_file():
        raise TtsModelNotFound(f"voice manifest not found: {p}")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        files = tuple(VoiceFile(f["path"], int(f["size"]), f["sha256"]) for f in d["files"])
        m = VoiceManifest(
            voice_id=d["voice_id"], language=d["language"], sample_rate=int(d["sample_rate"]), num_speakers=int(d["num_speakers"]),
            model_file=d["model_file"], tokens_file=d["tokens_file"], espeak_data_dir=d["espeak_data_dir"],
            directory_name=d["directory_name"], files=files, total_size_bytes=int(d["total_size_bytes"]),
            release_tag=d["source"]["release_tag"], asset_sha256=d["source"]["asset_sha256"], licence_status=d["licence"]["status"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TtsContractError(f"voice manifest is malformed: {type(exc).__name__}: {exc}") from exc
    paths = [f.path for f in m.files]
    if not paths or len(set(paths)) != len(paths) or sum(f.size_bytes for f in m.files) != m.total_size_bytes:
        raise TtsContractError("voice manifest is inconsistent (empty, duplicate paths or wrong total size)")
    if any(len(f.sha256) != 64 or f.path.startswith("/") or ".." in Path(f.path).parts for f in m.files):
        raise TtsContractError("voice manifest has an invalid hash or path")
    if m.model_file not in paths or m.tokens_file not in paths or not any(p.startswith(m.espeak_data_dir + "/") for p in paths):
        raise TtsContractError("voice manifest does not list the model, the tokens file and the espeak data")
    return m


def resolve_voice_dir(manifest: VoiceManifest, path=None) -> Path:
    """Default: models/<manifest.directory_name> under the project root (the Silero / STT convention)."""
    root = Path(__file__).resolve().parent.parent
    if path is None:
        return root / "models" / manifest.directory_name
    p = Path(path)
    return p if p.is_absolute() else root / p


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_voice_directory(voice_dir: Path, manifest: VoiceManifest) -> int:
    """Check every manifest file (existence, size, SHA-256). Return the verified byte total.

    Extra files are ignored. Raises TtsModelNotFound or TtsModelHashMismatch."""
    if not voice_dir.is_dir():
        raise TtsModelNotFound(f"voice directory not found: {voice_dir}")
    for entry in manifest.files:
        path = voice_dir / entry.path
        if not path.is_file():
            raise TtsModelNotFound(f"voice file missing: {entry.path}")
        actual = path.stat().st_size
        if actual != entry.size_bytes:
            raise TtsModelHashMismatch(f"{entry.path}: size {actual} != expected {entry.size_bytes}")
        if _sha256_of(path) != entry.sha256:
            raise TtsModelHashMismatch(f"{entry.path}: SHA-256 does not match the approved voice package")
    return manifest.total_size_bytes


def _default_tts_factory(model: str, tokens: str, data_dir: str):
    """Import sherpa_onnx NOW (never earlier) and build an OfflineTts with the development policy."""
    try:
        import sherpa_onnx as so
    except (ImportError, OSError) as exc:          # includes a native DLL blocked by Windows Application Control
        raise TtsRuntimeUnavailable(f"sherpa-onnx cannot be imported: {type(exc).__name__}: {exc}") from exc
    vits = so.OfflineTtsVitsModelConfig(model=model, tokens=tokens, data_dir=data_dir, noise_scale=NOISE_SCALE, noise_scale_w=NOISE_SCALE_W)
    config = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(vits=vits, num_threads=NUM_THREADS, provider=PROVIDER))
    return so.OfflineTts(config)


# ---------------------------------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------------------------------

class SherpaOnnxTtsEngine(TtsEngine):
    """en_US-lessac-medium via sherpa-onnx: text in, mono int16 SpeechAudio out."""

    def __init__(
        self,
        voice_dir=None,
        *,
        manifest: Optional[VoiceManifest] = None,
        tts=None,
        tts_factory: Optional[Callable] = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """voice_dir: local voice directory (default models/<manifest.directory_name>).
        tts: a ready-made OfflineTts-like object - TEST HOOK that skips the directory, the hashes and the runtime; it is still
        contract-checked but never counts as verified. Leave None in production.
        tts_factory: replaces the lazy sherpa_onnx import (test hook for the construction path)."""
        super().__init__(clock=clock)
        if tts is not None and voice_dir is not None:
            raise ValueError("pass either voice_dir or tts, not both")
        self._manifest = manifest if manifest is not None else load_manifest()
        self._voice_dir: Optional[Path] = None
        self._verified = False
        self._verify_ms: Optional[float] = None
        self._load_ms: Optional[float] = None

        if tts is None:
            m = self._manifest
            self._voice_dir = resolve_voice_dir(m, voice_dir)
            t0 = clock()
            verify_voice_directory(self._voice_dir, m)                              # BEFORE anything is loaded
            self._verify_ms = (clock() - t0) * 1000.0
            factory = tts_factory or _default_tts_factory
            t0 = clock()
            try:
                tts = factory(str(self._voice_dir / m.model_file), str(self._voice_dir / m.tokens_file),
                              str(self._voice_dir / m.espeak_data_dir))
            except TtsRuntimeUnavailable:
                raise
            except Exception as exc:
                logger.error("event=tts_model_load_failed voice=%s error=%s", m.voice_id, type(exc).__name__)
                raise TtsModelLoadFailed(f"could not load {m.voice_id}: {type(exc).__name__}: {exc}") from exc
            self._load_ms = (clock() - t0) * 1000.0
            self._verified = True               # every manifest file matched, and the model was built from that directory

        self._check_voice_contract(tts)
        self._tts = tts
        if self._verified:
            logger.info("event=tts_engine_ready voice_verified=true voice=%s files=%d total_bytes=%d sample_rate=%d provider=%s "
                        "threads=%d noise_scale=%g noise_scale_w=%g verify_ms=%.0f load_ms=%.0f",
                        self._manifest.voice_id, len(self._manifest.files), self._manifest.total_size_bytes, self.sample_rate,
                        PROVIDER, NUM_THREADS, NOISE_SCALE, NOISE_SCALE_W, self._verify_ms, self._load_ms)
        else:
            logger.info("event=tts_engine_ready voice_verified=false source=injected_tts sample_rate=%d", self.sample_rate)

    def _check_voice_contract(self, tts) -> None:
        try:
            rate, speakers = tts.sample_rate, tts.num_speakers
        except AttributeError as exc:
            raise TtsContractError(f"voice does not expose the expected attributes: {exc}") from exc
        if rate != self._manifest.sample_rate:
            raise TtsContractError(f"voice runs at {rate} Hz, the approved voice at {self._manifest.sample_rate} Hz")
        if speakers != self._manifest.num_speakers:
            raise TtsContractError(f"voice has {speakers} speakers, expected {self._manifest.num_speakers}")

    # -- TtsEngine interface ---------------------------------------------------

    @property
    def engine_id(self) -> str:
        return ENGINE_ID

    @property
    def voice_id(self) -> str:
        return self._manifest.voice_id if self._verified else UNVERIFIED_VOICE_ID

    @property
    def language(self) -> str:
        return self._manifest.language

    @property
    def sample_rate(self) -> int:
        return self._manifest.sample_rate

    @property
    def voice_verified(self) -> bool:
        """True only when built from a directory whose every manifest file matched (never for an injected voice)."""
        return self._verified

    @property
    def verify_ms(self) -> Optional[float]:
        return self._verify_ms

    @property
    def load_ms(self) -> Optional[float]:
        return self._load_ms

    def _synthesize(self, text: str) -> np.ndarray:
        audio = self._tts.generate(text, sid=SPEAKER_ID, speed=SPEED)       # complete audio (no callback): all sentences
        rate = getattr(audio, "sample_rate", None)
        if rate != self.sample_rate:
            raise TtsContractError(f"backend returned audio at {rate} Hz, expected {self.sample_rate} Hz")
        floats = np.asarray(getattr(audio, "samples", None), dtype=np.float64)
        if floats.ndim != 1 or floats.size == 0:
            raise TtsContractError("backend returned no samples or a non-mono result")
        if not np.isfinite(floats).all():
            raise TtsContractError("backend returned non-finite samples")
        return np.clip(np.rint(floats * PCM_FULL_SCALE), -32768, 32767).astype(np.int16)
