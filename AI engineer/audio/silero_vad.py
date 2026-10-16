"""Silero VAD ONNX adapter behind the Week 7 VadEngine contract (VESTA, Week 7 Task 2).

SileroOnnxVadEngine turns ONE frame of 512 NEW int16 samples (16 kHz, mono) into ONE speech
score in [0, 1]. It owns only what is specific to this model: the verified local artifact, the
ONNX session, the rolling 64-sample model context and the recurrent state. Segmentation policy
(thresholds, hysteresis, durations, pre-roll, timeouts) stays in audio/utterance.py; this module
has no thresholds and no defaults for them.

STATUS: SOFTWARE-VALIDATED ADAPTER / MODEL CONTRACT. It says nothing about how well the model
detects classroom speech: VAD ACCURACY is NOT MEASURED, nothing here ran on real speech, a
microphone, a ReSpeaker or a Raspberry Pi.

THE ARTIFACT (single authoritative record: SILERO_MODEL below; evidence in WEEK7_REPORT.md)
  upstream  https://github.com/snakers4/silero-vad, release v6.2.3, commit 5cd79456...
  file      src/silero_vad/data/silero_vad.onnx (the multi-rate model; the project uses 16 kHz only)
  license   MIT
  The local copy lives in models/ (git-ignored, like the YOLOX model) and is fetched ONCE by an
  explicit development step. This module NEVER downloads anything: no network, no downloader.

CONTRACT, read from the local file with ONNX Runtime metadata (not from memory):
  inputs   input  tensor(float) [dynamic, dynamic]      sr  tensor(int64) [] (a scalar)
           state  tensor(float) [2, dynamic, 128]
  outputs  output tensor(float) [dynamic, 1]            stateN tensor(float) [dynamic x 3]
  The graph leaves the audio length and the batch symbolic, so it does NOT enforce the 576-sample
  input of the upstream algorithm (a 512-sample input is also accepted by the graph). THE ADAPTER
  guarantees it: every call feeds exactly [1, 64 context + 512 new] float32 samples. Static
  dimensions and types are checked once at load; the symbolic ones are checked on every call.

UPSTREAM ALGORITHM (utils_vad.py OnnxWrapper at the pinned commit), followed exactly:
  * first call after a reset: context = 64 zeros, state = zeros (2, 1, 128);
  * model input = concatenate(context, new 512 samples); sr = int64 scalar 16000;
  * the run returns (score, new state); the new state is kept for the next call;
  * the next context is the LAST 64 samples of the model input just used.
The context is internal to the model. It is NOT stream audio: VadEngine.frame_samples is 512, the
segmenter indexes only the 512 new samples, and the context never reaches a SpeechSegment.

PCM: float32 = int16 / 32768.0, as in the upstream examples (examples/onnx_sequence/run.py and the
pyaudio streaming notebook). Dividing by a power of two is exact, so -32768 maps to -1.0 and 32767
to 0.999969482421875. No resampling, no channel mixing, no gain, no second normalisation.

LIFECYCLE. Construction verifies the artifact (existence, SHA-256 of the exact bytes that are then
loaded), creates one CPU session and validates the contract. reset() restores the initial context
and state only; it never reloads, re-hashes or recreates the session. A failure inside a call never
becomes a silent score of 0: it raises, and the engine state is left as it was before the call.

Importing this module imports no inference runtime. onnxruntime is imported when an engine is built
from a file; a ready-made session can be injected (test hook, like the YOLOX detector).
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

import numpy as np

from audio.vad import VadEngine, check_score
from config.logging_config import setup_logging

logger = setup_logging()


# ---------------------------------------------------------------------------------------
# errors (code = the MODEL_* name used in reports)
# ---------------------------------------------------------------------------------------

class SileroVadError(RuntimeError):
    """Base class for adapter problems that are about the model, not the audio frame."""

    code = "SILERO_VAD_ERROR"


class SileroModelNotFound(SileroVadError):
    code = "MODEL_NOT_FOUND"


class SileroModelHashMismatch(SileroVadError):
    code = "MODEL_HASH_MISMATCH"


class SileroModelLoadFailed(SileroVadError):
    code = "MODEL_LOAD_FAILED"


class SileroRuntimeUnavailable(SileroModelLoadFailed):
    """onnxruntime could not be imported (not installed, or blocked by the operating system)."""


class SileroModelContractMismatch(SileroVadError):
    code = "MODEL_CONTRACT_MISMATCH"


class SileroInferenceFailed(SileroVadError):
    code = "INFERENCE_FAILED"


# ---------------------------------------------------------------------------------------
# the approved artifact: ONE place for filename, revision, hash and expected contract
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TensorSpec:
    """Expected metadata of one model tensor. A None dimension is symbolic in the artifact."""

    name: str
    elem_type: str                      # ONNX Runtime type string, e.g. "tensor(float)"
    shape: Tuple[Optional[int], ...]    # None = dynamic/symbolic in the artifact
    batch_axis: Optional[int] = None    # if that axis is a literal, it must be 1 (the adapter uses batch 1)


@dataclass(frozen=True)
class SileroModelSpec:
    name: str
    upstream: str
    release_tag: str
    commit: str
    source_path: str
    git_blob_sha1: str          # GitHub's own object id of the file at that commit (independent integrity evidence)
    license: str
    filename: str               # local file under models/
    size_bytes: int
    sha256: str                 # SHA-256 of the exact bytes; verified before any session is created
    sample_rate: int            # the only rate the project uses
    frame_samples: int          # NEW stream samples per call
    context_samples: int        # model context, internal to the adapter
    inputs: Tuple[TensorSpec, ...]
    outputs: Tuple[TensorSpec, ...]
    sr_name: str
    audio_name: str
    state_name: str
    score_name: str
    state_out_name: str
    state_shape: Tuple[int, ...]

    @property
    def model_input_samples(self) -> int:
        return self.context_samples + self.frame_samples

    @property
    def source_url(self) -> str:
        return f"https://raw.githubusercontent.com/snakers4/silero-vad/{self.commit}/{self.source_path}"


SILERO_MODEL = SileroModelSpec(
    name="Silero VAD ONNX (silero_vad.onnx, multi-rate model, used at 16 kHz)",
    upstream="https://github.com/snakers4/silero-vad",
    release_tag="v6.2.3",
    commit="5cd7945676eb32225748052e2e6a0580e4686a08",
    source_path="src/silero_vad/data/silero_vad.onnx",
    git_blob_sha1="80c5592ef1f4c9ede3e357bbd02eb863358a6a9d",
    license="MIT",
    filename="silero_vad_v6.2.3.onnx",
    size_bytes=2327524,
    sha256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
    sample_rate=16000,
    frame_samples=512,
    context_samples=64,
    inputs=(
        TensorSpec("input", "tensor(float)", (None, None), batch_axis=0),
        TensorSpec("state", "tensor(float)", (2, None, 128), batch_axis=1),
        TensorSpec("sr", "tensor(int64)", ()),
    ),
    outputs=(
        TensorSpec("output", "tensor(float)", (None, 1), batch_axis=0),
        TensorSpec("stateN", "tensor(float)", (None, None, None)),
    ),
    sr_name="sr",
    audio_name="input",
    state_name="state",
    score_name="output",
    state_out_name="stateN",
    state_shape=(2, 1, 128),
)

# int16 full scale. The upstream examples normalise with 1/32768 (a power of two, so exact in float32).
PCM_FULL_SCALE = 32768.0


def resolve_model_path(path=None, spec: SileroModelSpec = SILERO_MODEL) -> Path:
    """Default: models/<spec.filename> under the project root (the YOLOX convention)."""
    if path is None:
        return Path(__file__).resolve().parent.parent / "models" / spec.filename
    p = Path(path)
    return p if p.is_absolute() else Path(__file__).resolve().parent.parent / p


# ---------------------------------------------------------------------------------------
# contract validation (metadata only; no inference)
# ---------------------------------------------------------------------------------------

def _dim_text(shape) -> str:
    return "[" + ", ".join("dynamic" if not isinstance(d, int) or isinstance(d, bool) else str(d) for d in shape) + "]"


def _compare_tensors(kind: str, actual_nodes, expected: Sequence[TensorSpec], problems: list) -> None:
    actual = {}
    seen = []
    for node in actual_nodes:
        try:
            name = node.name
        except AttributeError:
            problems.append(f"{kind} entry without a name: {node!r}")
            continue
        seen.append(name)
        actual[name] = node
    duplicated = sorted({n for n in seen if seen.count(n) > 1})
    if duplicated:                      # a dict would silently collapse these
        problems.append(f"{kind} names are duplicated: {duplicated}")
    want = {t.name: t for t in expected}
    if sorted(actual) != sorted(want):
        problems.append(f"{kind} names are {sorted(actual)}, expected {sorted(want)}")
    for name, spec in want.items():
        node = actual.get(name)
        if node is None:
            continue
        label = f"{kind} {name!r}"
        if getattr(node, "type", None) != spec.elem_type:
            problems.append(f"{label} has type {getattr(node, 'type', None)!r}, expected {spec.elem_type!r}")
        raw_shape = getattr(node, "shape", None)
        if raw_shape is None:           # unknown rank is UNKNOWN, never "scalar" (an sr of shape None is not a known scalar)
            problems.append(f"{label} reports no shape (unknown rank), expected rank {len(spec.shape)} {_dim_text(spec.shape)}")
            continue
        shape = list(raw_shape)
        if len(shape) != len(spec.shape):
            problems.append(f"{label} has rank {len(shape)} {_dim_text(shape)}, expected rank {len(spec.shape)} {_dim_text(spec.shape)}")
            continue
        for axis, (want_dim, got_dim) in enumerate(zip(spec.shape, shape)):
            is_literal = isinstance(got_dim, int) and not isinstance(got_dim, bool)
            if want_dim is not None and got_dim != want_dim:
                problems.append(f"{label} axis {axis} is {got_dim!r}, expected the literal {want_dim}")
            elif want_dim is None and is_literal and axis == spec.batch_axis and got_dim != 1:
                problems.append(f"{label} batch axis {axis} is {got_dim}, the adapter uses batch 1")


def validate_session_contract(session, spec: SileroModelSpec = SILERO_MODEL) -> None:
    """Check input/output names, element types, ranks and literal dimensions against `spec`.

    Raises SileroModelContractMismatch listing every difference. Symbolic dimensions in the artifact
    are accepted here (and re-checked on every call). One derived rule: if the audio input's length axis
    is a literal, it must equal context + frame (576), the relationship the adapter relies on.
    """
    problems: list = []
    try:
        inputs = list(session.get_inputs())
        outputs = list(session.get_outputs())
    except Exception as exc:  # noqa: BLE001 - any failure to read metadata is a contract failure
        raise SileroModelContractMismatch(f"could not read the session's input/output metadata: {exc}") from exc

    _compare_tensors("input", inputs, spec.inputs, problems)
    _compare_tensors("output", outputs, spec.outputs, problems)

    for node in inputs:
        if getattr(node, "name", None) == spec.audio_name:
            shape = list(getattr(node, "shape", None) or [])
            if len(shape) == 2 and isinstance(shape[1], int) and not isinstance(shape[1], bool) \
                    and shape[1] != spec.model_input_samples:
                problems.append(
                    f"input {spec.audio_name!r} fixes the length at {shape[1]}, but context {spec.context_samples} + "
                    f"frame {spec.frame_samples} = {spec.model_input_samples}"
                )
    if problems:
        raise SileroModelContractMismatch("Silero model contract mismatch: " + "; ".join(problems))


# ---------------------------------------------------------------------------------------
# loading a verified local file
# ---------------------------------------------------------------------------------------

def _verified_model_bytes(path: Path, spec: SileroModelSpec) -> bytes:
    if not path.is_file():
        raise SileroModelNotFound(
            f"Silero VAD model not found at {str(path)!r}. Place {spec.filename} there (one-time development step; "
            f"source {spec.source_url}, see WEEK7_REPORT.md). The runtime never downloads it."
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SileroModelLoadFailed(f"could not read {str(path)!r}: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    if digest != spec.sha256:
        raise SileroModelHashMismatch(
            f"{str(path)!r} is not the approved artifact: SHA-256 {digest} ({len(data)} bytes), expected "
            f"{spec.sha256} ({spec.size_bytes} bytes, {spec.release_tag}). A changed artifact needs explicit re-approval."
        )
    return data


def _create_cpu_session(model_bytes: bytes):
    try:
        import onnxruntime as ort  # lazy: see the module docstring
    except (ImportError, OSError) as exc:
        logger.error("event=silero_vad_runtime_unavailable error=%s", exc)
        raise SileroRuntimeUnavailable(f"onnxruntime could not be imported ({exc})") from exc
    options = ort.SessionOptions()
    # DEVELOPMENT EXECUTION POLICY: 1 + 1 threads, copied from / aligned with upstream's reference wrapper
    # (OnnxWrapper). NOT PI-CALIBRATED; no performance claim is made or implied.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    try:
        # the verified BYTES are loaded, so the file cannot change between the hash check and the load
        return ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001
        raise SileroModelLoadFailed(f"onnxruntime could not create a session from the verified model: {exc}") from exc


# ---------------------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------------------

class SileroOnnxVadEngine(VadEngine):
    """Silero VAD v6.2.3 (ONNX) at 16 kHz: 512 new int16 samples in, one speech score out."""

    def __init__(self, model_path=None, *, spec: SileroModelSpec = SILERO_MODEL, session=None) -> None:
        """model_path: the local file (default models/<spec.filename>). session: a ready-made
        onnxruntime-like object (get_inputs/get_outputs/run) - TEST HOOK that skips the file, the hash and
        onnxruntime; its metadata is still validated. Leave None in production.

        artifact_verified is True ONLY when the engine was built from a file whose bytes matched
        spec.sha256 (the production path). An injected session is never verified: artifact_verified is
        False, verified_sha256 is None, and the log line says so without quoting any hash."""
        if session is not None and model_path is not None:
            raise ValueError("pass either model_path or session, not both")
        self._spec = spec
        self._model_path: Optional[Path] = None
        self._artifact_verified = False
        self._verified_sha256: Optional[str] = None
        if session is None:
            self._model_path = resolve_model_path(model_path, spec)
            session = _create_cpu_session(_verified_model_bytes(self._model_path, spec))
            # _verified_model_bytes returns only bytes whose SHA-256 equals spec.sha256, and the session was
            # built from exactly those bytes
            self._artifact_verified = True
            self._verified_sha256 = spec.sha256
        validate_session_contract(session, spec)
        self._session = session
        self._sr = np.array(spec.sample_rate, dtype=np.int64)       # 0-d int64, as upstream
        self._context = np.zeros(spec.context_samples, dtype=np.float32)
        self._state = np.zeros(spec.state_shape, dtype=np.float32)
        providers = tuple(getattr(session, "get_providers", lambda: ("unknown",))())
        self._providers = providers
        if self._artifact_verified:
            logger.info(
                "event=silero_vad_ready artifact_verified=true model=%s sha256=%s providers=%s frame_samples=%d context_samples=%d",
                spec.filename, spec.sha256[:12], ",".join(providers), spec.frame_samples, spec.context_samples,
            )
        else:
            # no file, no hash check: nothing here may suggest the approved artifact was verified
            logger.info(
                "event=silero_vad_ready artifact_verified=false source=injected_session providers=%s frame_samples=%d context_samples=%d",
                ",".join(providers), spec.frame_samples, spec.context_samples,
            )

    # -- VadEngine interface ---------------------------------------------

    @property
    def sample_rate(self) -> int:
        return self._spec.sample_rate

    @property
    def frame_samples(self) -> int:
        """NEW stream samples per call (512). The 64-sample model context is not counted here."""
        return self._spec.frame_samples

    def _score_frame(self, frame: np.ndarray) -> float:
        spec = self._spec
        # float32 copy of the caller's frame (the int16 array is never modified), exact power-of-two scaling
        new_audio = frame.astype(np.float32) / np.float32(PCM_FULL_SCALE)
        model_input = np.concatenate((self._context, new_audio)).reshape(1, spec.model_input_samples)
        feed = {spec.audio_name: model_input, spec.state_name: self._state, spec.sr_name: self._sr}
        try:
            outputs = self._session.run([spec.score_name, spec.state_out_name], feed)
        except Exception as exc:  # noqa: BLE001 - never turned into a score of 0
            raise SileroInferenceFailed(f"Silero inference failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(outputs, (list, tuple)) or len(outputs) != 2:
            count = len(outputs) if isinstance(outputs, (list, tuple)) else "no list of"
            raise SileroModelContractMismatch(
                f"session.run returned {type(outputs).__name__} with {count} outputs, expected exactly 2 "
                f"({spec.score_name!r}, {spec.state_out_name!r})"
            )
        score_out, state_out = outputs

        score_out = np.asarray(score_out)
        state_out = np.asarray(state_out)
        if score_out.dtype != np.float32 or score_out.shape != (1, 1):
            raise SileroModelContractMismatch(
                f"score output {spec.score_name!r} is {score_out.dtype} {score_out.shape}, expected float32 (1, 1)"
            )
        if state_out.dtype != np.float32 or state_out.shape != tuple(spec.state_shape):
            raise SileroModelContractMismatch(
                f"state output {spec.state_out_name!r} is {state_out.dtype} {state_out.shape}, "
                f"expected float32 {tuple(spec.state_shape)}"
            )
        score = check_score(float(score_out[0, 0]))   # raises VadScoreError for NaN/inf/out of range; state not advanced yet

        # commit only after every check passed, so a failed call leaves the engine as it was
        self._state = np.array(state_out, dtype=np.float32, copy=True)
        self._context = model_input[0, -spec.context_samples:].copy()
        return score

    def reset(self) -> None:
        """Back to the state of a fresh engine on the same model: zero context, zero recurrent state.
        Does not reload, re-hash or recreate the session."""
        self._context = np.zeros(self._spec.context_samples, dtype=np.float32)
        self._state = np.zeros(self._spec.state_shape, dtype=np.float32)

    # -- extras -------------------------------------------------------------

    @property
    def model_spec(self) -> SileroModelSpec:
        """The specification this engine is CHECKED AGAINST. It is not evidence that the bytes were
        verified: see artifact_verified."""
        return self._spec

    @property
    def artifact_verified(self) -> bool:
        """True only if the session was built from a file whose SHA-256 matched the approved value."""
        return self._artifact_verified

    @property
    def verified_sha256(self) -> Optional[str]:
        """The SHA-256 the loaded bytes were verified to have, or None for an injected session."""
        return self._verified_sha256

    @property
    def providers(self) -> Tuple[str, ...]:
        return self._providers

    @property
    def context_samples(self) -> int:
        return self._spec.context_samples
