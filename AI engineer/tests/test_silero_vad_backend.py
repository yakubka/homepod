"""Tests for audio/silero_vad.py: the Silero VAD ONNX adapter (Week 7 Task 2).

WHAT THESE TESTS ESTABLISH: the approved artifact is the one that is loaded (SHA-256), the adapter's view of
the model interface matches the inspected file, the adapter feeds that interface correctly (int16 scaling,
64-sample context, recurrent state, reset), failures surface as errors and never as a silent score, and the
engine plugs into the Task 1 segmenter unchanged.

WHAT THEY DO NOT ESTABLISH: any VAD accuracy. The fake session returns scripted numbers and the real-model
smoke tests only show that the model loads, matches its contract, runs, and gives finite scores in [0, 1].
Nothing here used speech, a microphone, a ReSpeaker or a Raspberry Pi.

Groups: A provenance/hash, B metadata contract, C PCM conversion, D context, E recurrent state, F reset,
G score validation, H error handling, I fake-session inference, J real-model smoke, K Task 1 integration,
L import / no-network behaviour. The real-model tests skip (with a reason) when models/silero_vad_v6.2.3.onnx
is not present (it is git-ignored) or onnxruntime cannot be imported on this machine.
"""

import ast
import dataclasses
import hashlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from audio import silero_vad as sv
from audio.silero_vad import (
    PCM_FULL_SCALE,
    SILERO_MODEL,
    SileroInferenceFailed,
    SileroModelContractMismatch,
    SileroModelHashMismatch,
    SileroModelLoadFailed,
    SileroModelNotFound,
    SileroOnnxVadEngine,
    SileroRuntimeUnavailable,
    SileroVadError,
    resolve_model_path,
    validate_session_contract,
)
from audio.utterance import SegmentOutcome, SegmenterConfig, UtteranceSegmenter
from audio.vad import VadEngine, VadFrameError, VadScoreError

SPEC = SILERO_MODEL
REPO_ROOT = Path(__file__).resolve().parent.parent
NETWORK_MODULES = ("urllib.request", "http.client", "requests", "huggingface_hub", "torch", "torchaudio",
                   "silero_vad", "onnx")


# ---------------------------------------------------------------------------------------
# test doubles and helpers (TEST / SYNTHETIC)
# ---------------------------------------------------------------------------------------

class FakeNode:
    """Mimics onnxruntime's NodeArg: name, type string, shape list (None = symbolic dimension)."""

    def __init__(self, name, type, shape):
        self.name, self.type, self.shape = name, type, shape


def nodes_from(tensors):
    return [FakeNode(t.name, t.elem_type, list(t.shape)) for t in tensors]


class OrtLikeError(Exception):
    """Like onnxruntime's own exceptions (InvalidArgument, Fail, ...): a plain Exception subclass, NOT a RuntimeError."""


_UNSET = object()


def ort_like_check(feed):
    """What the REAL graph rejects, as probed on the real file: wrong element types and wrong literal state
    dimensions. (It does NOT check the audio length, the batch or the rank of sr, so neither does this.)"""
    audio, state, sr = feed["input"], feed["state"], feed["sr"]
    if audio.dtype != np.float32 or audio.ndim != 2:
        raise OrtLikeError(f"Unexpected input data type or rank for 'input': {audio.dtype} {audio.shape}")
    if state.dtype != np.float32 or state.ndim != 3 or state.shape[0] != 2 or state.shape[2] != 128:
        raise OrtLikeError(f"Got invalid dimensions or type for input 'state': {state.dtype} {state.shape}")
    if sr.dtype != np.int64:
        raise OrtLikeError(f"Unexpected input data type for 'sr': {sr.dtype}")


class FakeSession:
    """onnxruntime-like session. Metadata defaults to the contract inspected on the real artifact
    (SILERO_MODEL.inputs/outputs; a real-model test proves they are equal). run() records COPIES of what it
    was fed and returns a score plus a state filled with the call number, so state chaining is visible."""

    def __init__(self, scores=None, score_fn=None, inputs=None, outputs=None, raises=None, out_override=None,
                 providers=("CPUExecutionProvider",), raw_return=_UNSET):
        self.inputs = inputs if inputs is not None else nodes_from(SPEC.inputs)
        self.outputs = outputs if outputs is not None else nodes_from(SPEC.outputs)
        self.scores, self.score_fn, self.raises = scores, score_fn, raises
        self.out_override, self.providers = out_override or {}, providers
        self.raw_return = raw_return          # if set, run() returns exactly this (to model a malformed result)
        self.calls, self.returned_states, self.metadata_reads = [], [], 0

    def get_inputs(self):
        self.metadata_reads += 1
        return list(self.inputs)

    def get_outputs(self):
        self.metadata_reads += 1
        return list(self.outputs)

    def get_providers(self):
        return list(self.providers)

    def run(self, output_names, feed):
        n = len(self.calls) + 1
        self.calls.append(dict(
            output_names=list(output_names), keys=sorted(feed),
            input=feed["input"].copy(), state=feed["state"].copy(), sr=feed["sr"].copy(),
            input_dtype=feed["input"].dtype, state_dtype=feed["state"].dtype, sr_dtype=feed["sr"].dtype,
            state_id=id(feed["state"]),
        ))
        ort_like_check(feed)                  # like the real graph: wrong dtypes / state literals are rejected
        if self.raises is not None:
            raise self.raises
        if self.raw_return is not _UNSET:
            return self.raw_return
        if self.score_fn is not None:
            score = self.score_fn(feed["input"][0])
        elif self.scores is not None:
            score = self.scores[n - 1]
        else:
            score = 0.25
        state = np.full(SPEC.state_shape, float(n), dtype=np.float32)
        self.returned_states.append(state)
        result = {"output": np.array([[score]], dtype=np.float32), "stateN": state}
        result.update(self.out_override)
        return [result[name] for name in output_names]


def frame_of(value, n=512):
    return np.full(n, value, dtype=np.int16)


def counter_frame(k, n=512):
    """512 distinct int16 values, different for every k."""
    return ((np.arange(n, dtype=np.int64) + k * n) % 20000 - 10000).astype(np.int16)


def spec_for(data: bytes):
    return dataclasses.replace(SPEC, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


@pytest.fixture
def real_model_path():
    path = resolve_model_path()
    if not path.is_file():
        pytest.skip(f"{SPEC.filename} is not in models/ (git-ignored; see WEEK7_REPORT.md for the one-time acquisition)")
    return path


@pytest.fixture
def real_engine(real_model_path):
    try:
        return SileroOnnxVadEngine(real_model_path)
    except SileroRuntimeUnavailable as exc:
        pytest.skip(f"onnxruntime cannot be imported on this machine: {exc}")


@pytest.fixture
def real_session(real_model_path):
    data = sv._verified_model_bytes(real_model_path, SPEC)
    try:
        return sv._create_cpu_session(data)
    except SileroRuntimeUnavailable as exc:
        pytest.skip(f"onnxruntime cannot be imported on this machine: {exc}")


# =======================================================================================
# A. Provenance / hash
# =======================================================================================

def test_the_spec_records_the_approved_artifact():
    assert SPEC.release_tag == "v6.2.3" and SPEC.license == "MIT"
    assert len(SPEC.commit) == 40 and set(SPEC.commit) <= set("0123456789abcdef")
    assert len(SPEC.sha256) == 64 and set(SPEC.sha256) <= set("0123456789abcdef")
    assert len(SPEC.git_blob_sha1) == 40
    assert SPEC.size_bytes == 2327524 and SPEC.filename == "silero_vad_v6.2.3.onnx"
    assert SPEC.commit in SPEC.source_url and SPEC.source_url.startswith("https://raw.githubusercontent.com/snakers4/silero-vad/")
    assert (SPEC.sample_rate, SPEC.frame_samples, SPEC.context_samples, SPEC.model_input_samples) == (16000, 512, 64, 576)


def test_the_local_artifact_matches_the_recorded_size_hash_and_git_blob(real_model_path):
    data = real_model_path.read_bytes()
    assert len(data) == SPEC.size_bytes
    assert hashlib.sha256(data).hexdigest() == SPEC.sha256
    # GitHub's own object id for the file at the pinned commit: evidence independent of our SHA-256
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == SPEC.git_blob_sha1


def test_default_path_is_the_versioned_file_under_models():
    p = resolve_model_path()
    assert p.name == SPEC.filename and p.parent.name == "models" and p.is_absolute()
    assert resolve_model_path("models/x.onnx") == REPO_ROOT / "models" / "x.onnx"
    assert resolve_model_path(Path(REPO_ROOT) / "a.onnx") == REPO_ROOT / "a.onnx"


def test_a_hash_mismatch_is_rejected_before_any_session_is_created(tmp_path, monkeypatch):
    bad = tmp_path / "silero.onnx"
    bad.write_bytes(b"this is not the approved model")

    def must_not_run(*a, **k):
        raise AssertionError("a session was created for an unverified artifact")

    monkeypatch.setattr(sv, "_create_cpu_session", must_not_run)
    with pytest.raises(SileroModelHashMismatch) as err:
        SileroOnnxVadEngine(bad)
    assert err.value.code == "MODEL_HASH_MISMATCH"
    assert SPEC.sha256 in str(err.value) and "re-approval" in str(err.value)


def test_a_corrupted_copy_of_the_real_artifact_is_rejected_and_the_original_is_untouched(tmp_path, real_model_path):
    before = hashlib.sha256(real_model_path.read_bytes()).hexdigest()
    corrupted = bytearray(real_model_path.read_bytes())
    corrupted[len(corrupted) // 2] ^= 0x01                         # one flipped bit in the middle
    copy = tmp_path / "corrupted.onnx"
    copy.write_bytes(bytes(corrupted))
    with pytest.raises(SileroModelHashMismatch):
        SileroOnnxVadEngine(copy)
    assert hashlib.sha256(real_model_path.read_bytes()).hexdigest() == before == SPEC.sha256


def test_a_truncated_or_wrong_file_is_a_clear_failure(tmp_path):
    for name, data in (("empty.onnx", b""), ("text.onnx", b"hello"), ("short.onnx", b"\x08\x08\x12\x04spox2")):
        p = tmp_path / name
        p.write_bytes(data)
        with pytest.raises(SileroModelHashMismatch, match="not the approved artifact"):
            SileroOnnxVadEngine(p)


def test_a_missing_model_is_model_not_found_and_nothing_is_downloaded(tmp_path):
    with pytest.raises(SileroModelNotFound) as err:
        SileroOnnxVadEngine(tmp_path / "nope.onnx")
    assert err.value.code == "MODEL_NOT_FOUND" and "never downloads" in str(err.value)
    with pytest.raises(SileroModelNotFound):
        SileroOnnxVadEngine(tmp_path)                              # a directory is not a model file


def test_the_exact_verified_bytes_are_what_gets_loaded(tmp_path, monkeypatch):
    data = b"stand-in bytes for the approved artifact"
    p = tmp_path / "m.onnx"
    p.write_bytes(data)
    seen = []
    monkeypatch.setattr(sv, "_create_cpu_session", lambda b: seen.append(b) or FakeSession())
    engine = SileroOnnxVadEngine(p, spec=spec_for(data))            # accepted: the bytes match the (test) spec
    assert seen == [data] and isinstance(engine, VadEngine)


def test_model_path_and_session_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError):
        SileroOnnxVadEngine(tmp_path / "x.onnx", session=FakeSession())


def test_every_adapter_error_is_a_silero_error_with_a_code():
    codes = {SileroModelNotFound: "MODEL_NOT_FOUND", SileroModelHashMismatch: "MODEL_HASH_MISMATCH",
             SileroModelLoadFailed: "MODEL_LOAD_FAILED", SileroRuntimeUnavailable: "MODEL_LOAD_FAILED",
             SileroModelContractMismatch: "MODEL_CONTRACT_MISMATCH", SileroInferenceFailed: "INFERENCE_FAILED"}
    for cls, code in codes.items():
        assert issubclass(cls, SileroVadError) and cls.code == code
    assert issubclass(SileroRuntimeUnavailable, SileroModelLoadFailed)


# =======================================================================================
# B. Model metadata contract
# =======================================================================================

def test_default_fake_metadata_is_accepted():
    validate_session_contract(FakeSession())
    SileroOnnxVadEngine(session=FakeSession())


def test_the_fake_metadata_equals_the_real_artifacts_metadata(real_session):
    """Fake-session realism: what the fakes claim is exactly what ONNX Runtime reports for the real file."""
    def view(nodes):
        return [(n.name, n.type, [d if isinstance(d, int) else None for d in n.shape]) for n in nodes]

    assert view(real_session.get_inputs()) == view(nodes_from(SPEC.inputs))
    assert view(real_session.get_outputs()) == view(nodes_from(SPEC.outputs))


def test_symbolic_dimensions_are_accepted_where_the_artifact_uses_them():
    inputs = [FakeNode("input", "tensor(float)", ["batch", "samples"]),
              FakeNode("state", "tensor(float)", [2, "batch", 128]), FakeNode("sr", "tensor(int64)", [])]
    outputs = [FakeNode("output", "tensor(float)", ["batch", 1]), FakeNode("stateN", "tensor(float)", ["a", "b", "c"])]
    validate_session_contract(FakeSession(inputs=inputs, outputs=outputs))
    # a literal that is right is also fine: batch 1, and audio length 576
    inputs[0] = FakeNode("input", "tensor(float)", [1, 576])
    validate_session_contract(FakeSession(inputs=inputs, outputs=outputs))


def _mutated(inputs=None, outputs=None):
    i, o = nodes_from(SPEC.inputs), nodes_from(SPEC.outputs)
    if inputs:
        inputs(i)
    if outputs:
        outputs(o)
    return FakeSession(inputs=i, outputs=o)


def _set(nodes, target, **kw):
    for n in nodes:
        if n.name == target:
            for k, v in kw.items():
                setattr(n, k, v)


CONTRACT_BREAKS = {
    "wrong audio input name": (lambda: _mutated(inputs=lambda n: _set(n, "input", name="audio")), "input names"),
    "state input missing": (lambda: _mutated(inputs=lambda n: n.__delitem__(1)), "input names"),
    "sr input missing": (lambda: _mutated(inputs=lambda n: n.__delitem__(2)), "input names"),
    "unexpected extra input": (lambda: _mutated(inputs=lambda n: n.append(FakeNode("extra", "tensor(float)", [1]))), "input names"),
    "state rank 2": (lambda: _mutated(inputs=lambda n: _set(n, "state", shape=[2, None])), "rank"),
    "state last dim 64": (lambda: _mutated(inputs=lambda n: _set(n, "state", shape=[2, None, 64])), "literal 128"),
    "state first dim 1": (lambda: _mutated(inputs=lambda n: _set(n, "state", shape=[1, None, 128])), "literal 2"),
    "state batch literal 2": (lambda: _mutated(inputs=lambda n: _set(n, "state", shape=[2, 2, 128])), "batch"),
    "audio batch literal 2": (lambda: _mutated(inputs=lambda n: _set(n, "input", shape=[2, None])), "batch"),
    "audio rank 3": (lambda: _mutated(inputs=lambda n: _set(n, "input", shape=[None, None, 1])), "rank"),
    "audio length fixed at 512 (not context + frame)": (lambda: _mutated(inputs=lambda n: _set(n, "input", shape=[1, 512])), "576"),
    "audio length fixed at 640": (lambda: _mutated(inputs=lambda n: _set(n, "input", shape=[1, 640])), "576"),
    "sr is float": (lambda: _mutated(inputs=lambda n: _set(n, "sr", type="tensor(float)")), "sr"),
    "sr is int32": (lambda: _mutated(inputs=lambda n: _set(n, "sr", type="tensor(int32)")), "sr"),
    "sr has a dimension": (lambda: _mutated(inputs=lambda n: _set(n, "sr", shape=[1])), "rank"),
    "audio is double": (lambda: _mutated(inputs=lambda n: _set(n, "input", type="tensor(double)")), "type"),
    "state is double": (lambda: _mutated(inputs=lambda n: _set(n, "state", type="tensor(double)")), "type"),
    "score output missing": (lambda: _mutated(outputs=lambda n: _set(n, "output", name="prob")), "output names"),
    "state output missing": (lambda: _mutated(outputs=lambda n: n.__delitem__(1)), "output names"),
    "score output has 2 columns": (lambda: _mutated(outputs=lambda n: _set(n, "output", shape=[None, 2])), "literal 1"),
    "score output rank 1": (lambda: _mutated(outputs=lambda n: _set(n, "output", shape=[1])), "rank"),
    "score output is double": (lambda: _mutated(outputs=lambda n: _set(n, "output", type="tensor(double)")), "type"),
    "state output is double": (lambda: _mutated(outputs=lambda n: _set(n, "stateN", type="tensor(double)")), "type"),
    "state output rank 2": (lambda: _mutated(outputs=lambda n: _set(n, "stateN", shape=[None, None])), "rank"),
}


@pytest.mark.parametrize("label", sorted(CONTRACT_BREAKS))
def test_a_contract_mismatch_fails_at_load_never_at_inference(label):
    build, fragment = CONTRACT_BREAKS[label]
    session = build()
    with pytest.raises(SileroModelContractMismatch) as err:
        SileroOnnxVadEngine(session=session)
    assert err.value.code == "MODEL_CONTRACT_MISMATCH" and fragment in str(err.value), str(err.value)
    assert session.calls == []                                   # nothing was run


def test_all_differences_are_reported_together():
    session = _mutated(inputs=lambda n: (_set(n, "sr", type="tensor(float)"), _set(n, "state", shape=[2, None, 64])))
    with pytest.raises(SileroModelContractMismatch) as err:
        validate_session_contract(session)
    assert "sr" in str(err.value) and "state" in str(err.value) and "128" in str(err.value)


def test_unreadable_metadata_is_a_contract_failure():
    class Broken(FakeSession):
        def get_inputs(self):
            raise RuntimeError("no metadata")

    with pytest.raises(SileroModelContractMismatch, match="metadata"):
        SileroOnnxVadEngine(session=Broken())


# =======================================================================================
# C. PCM conversion
# =======================================================================================

@pytest.mark.parametrize("value,expected", [
    (-32768, -1.0), (-1, -2.0 ** -15), (0, 0.0), (1, 2.0 ** -15), (32767, 32767 / 32768),
])
def test_int16_to_float32_scaling_is_pinned(value, expected):
    session = FakeSession()
    SileroOnnxVadEngine(session=session).process_frame(frame_of(value))
    audio = session.calls[0]["input"][0, SPEC.context_samples:]
    assert audio.dtype == np.float32 and audio.shape == (512,)
    assert (audio == np.float32(expected)).all()
    assert PCM_FULL_SCALE == 32768.0


def test_extreme_values_match_the_documented_float32_numbers():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    engine.process_frame(frame_of(32767))
    assert float(session.calls[0]["input"][0, -1]) == 0.999969482421875      # 32767 / 32768, exact in float32


def test_model_input_has_the_exact_dtype_shape_and_sample_rate_array():
    session = FakeSession()
    SileroOnnxVadEngine(session=session).process_frame(counter_frame(0))
    call = session.calls[0]
    assert call["output_names"] == ["output", "stateN"] and call["keys"] == ["input", "sr", "state"]
    assert call["input_dtype"] == np.float32 and call["input"].shape == (1, 576)
    assert call["state_dtype"] == np.float32 and call["state"].shape == (2, 1, 128)
    assert call["sr_dtype"] == np.int64 and call["sr"].shape == () and int(call["sr"]) == 16000


def test_no_gain_or_renormalisation_is_applied():
    session = FakeSession()
    SileroOnnxVadEngine(session=session).process_frame(frame_of(100))
    audio = session.calls[0]["input"][0, 64:]
    assert audio.max() == np.float32(100 / 32768)            # a quiet frame is NOT scaled up to full range


def test_the_callers_frame_is_never_modified():
    frame = counter_frame(3)
    before = frame.copy()
    frame.setflags(write=False)                              # the segmenter hands read-only frames
    engine = SileroOnnxVadEngine(session=FakeSession())
    engine.process_frame(frame)
    engine.process_frame(frame)
    assert np.array_equal(frame, before) and frame.dtype == np.int16


def test_wrong_frames_are_rejected_by_the_vad_contract_before_the_model_is_touched():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    for bad in (np.zeros(511, np.int16), np.zeros(576, np.int16), np.zeros(512, np.float32), np.zeros((512, 1), np.int16)):
        with pytest.raises(VadFrameError):
            engine.process_frame(bad)
    assert session.calls == []


# =======================================================================================
# D. Context
# =======================================================================================

def test_the_first_call_after_reset_uses_64_zeros_plus_the_new_frame():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    f0 = counter_frame(0)
    engine.process_frame(f0)
    row = session.calls[0]["input"][0]
    assert (row[:64] == 0).all()
    assert np.array_equal(row[64:], f0.astype(np.float32) / np.float32(32768))


def test_later_calls_chain_the_last_64_samples_of_the_previous_new_frame():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    frames = [counter_frame(k) for k in range(4)]
    for f in frames:
        engine.process_frame(f)
    scale = lambda f: f.astype(np.float32) / np.float32(32768)
    for k in range(1, 4):
        row = session.calls[k]["input"][0]
        assert np.array_equal(row[:64], scale(frames[k - 1])[-64:]), k       # B and C
        assert np.array_equal(row[64:], scale(frames[k])), k
        assert row.shape == (576,)


def test_context_belongs_to_the_adapter_and_never_changes_the_exposed_frame_size():
    engine = SileroOnnxVadEngine(session=FakeSession())
    assert engine.frame_samples == 512 and engine.context_samples == 64 and engine.sample_rate == 16000
    assert engine.frame_samples + engine.context_samples == 576


def test_reset_returns_the_context_to_zeros():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    engine.process_frame(counter_frame(0))
    engine.process_frame(counter_frame(1))
    engine.reset()
    engine.process_frame(counter_frame(2))
    assert (session.calls[2]["input"][0, :64] == 0).all()


def test_the_context_is_a_copy_not_a_view_of_anything_the_caller_holds():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    f0 = counter_frame(0)
    engine.process_frame(f0)
    f0[:] = 0                                               # caller reuses/clobbers its buffer
    engine.process_frame(counter_frame(1))
    expected = (counter_frame(0).astype(np.float32) / np.float32(32768))[-64:]
    assert np.array_equal(session.calls[1]["input"][0, :64], expected)


# =======================================================================================
# E. Recurrent state
# =======================================================================================

def test_state_starts_at_zero_and_each_call_receives_the_previous_calls_returned_state():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    for k in range(3):
        engine.process_frame(counter_frame(k))
    assert (session.calls[0]["state"] == 0).all()            # initial zero state
    assert (session.calls[1]["state"] == 1.0).all()          # the state returned by call 1
    assert (session.calls[2]["state"] == 2.0).all()          # the state returned by call 2


def test_reset_restores_the_zero_state():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    engine.process_frame(counter_frame(0))
    engine.process_frame(counter_frame(1))
    engine.reset()
    engine.process_frame(counter_frame(2))
    assert (session.calls[2]["state"] == 0).all()


def test_state_arrays_are_not_aliased_with_what_the_session_returned_or_with_other_engines():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    engine.process_frame(counter_frame(0))
    session.returned_states[0][:] = 99.0                     # something outside the engine scribbles on the array it returned
    engine.process_frame(counter_frame(1))
    assert (session.calls[1]["state"] == 1.0).all()          # the engine kept its own copy
    other = SileroOnnxVadEngine(session=FakeSession())
    assert other._state is not engine._state and not np.shares_memory(other._state, engine._state)


# =======================================================================================
# F. Reset
# =======================================================================================

def test_reset_makes_the_next_call_identical_to_a_fresh_engines_first_call():
    s1, s2 = FakeSession(), FakeSession()
    used, fresh = SileroOnnxVadEngine(session=s1), SileroOnnxVadEngine(session=s2)
    for k in range(3):
        used.process_frame(counter_frame(k))
    used.reset()
    used.process_frame(counter_frame(7))
    fresh.process_frame(counter_frame(7))
    a, b = s1.calls[-1], s2.calls[0]
    assert np.array_equal(a["input"], b["input"]) and np.array_equal(a["state"], b["state"])


def test_reset_does_not_reload_rehash_or_revalidate(tmp_path, monkeypatch):
    data = b"stand-in bytes"
    p = tmp_path / "m.onnx"
    p.write_bytes(data)
    session = FakeSession()
    created, hashed = [], []
    monkeypatch.setattr(sv, "_create_cpu_session", lambda b: created.append(1) or session)
    real_verify = sv._verified_model_bytes
    monkeypatch.setattr(sv, "_verified_model_bytes", lambda path, spec: hashed.append(1) or real_verify(path, spec))
    engine = SileroOnnxVadEngine(p, spec=spec_for(data))
    reads = session.metadata_reads
    engine.process_frame(counter_frame(0))
    engine.reset()
    engine.reset()
    assert created == [1] and hashed == [1] and session.metadata_reads == reads
    assert engine._session is session


def test_double_reset_is_harmless():
    engine = SileroOnnxVadEngine(session=FakeSession())
    engine.reset()
    engine.reset()
    assert (engine._state == 0).all() and (engine._context == 0).all()


# =======================================================================================
# G. Score validation
# =======================================================================================

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -0.001, 1.001, 7.0])
def test_a_bad_score_raises_and_the_engine_state_is_not_advanced(bad):
    session = FakeSession(scores=[0.2, bad, 0.3])
    engine = SileroOnnxVadEngine(session=session)
    f = [counter_frame(k) for k in range(3)]
    assert engine.process_frame(f[0]) == pytest.approx(0.2)
    with pytest.raises(VadScoreError):
        engine.process_frame(f[1])
    engine.process_frame(f[2])
    # the failed call changed nothing: call 3 sees the state and context left by call 1
    assert (session.calls[2]["state"] == 1.0).all()
    assert np.array_equal(session.calls[2]["input"][0, :64], (f[0].astype(np.float32) / np.float32(32768))[-64:])


@pytest.mark.parametrize("score", [0.0, 1.0, 0.5])
def test_boundary_scores_are_accepted_and_returned_as_a_python_float(score):
    out = SileroOnnxVadEngine(session=FakeSession(scores=[score])).process_frame(counter_frame(0))
    assert out == score and type(out) is float


@pytest.mark.parametrize("override", [
    {"output": np.zeros((1, 2), np.float32)}, {"output": np.zeros((2, 1), np.float32)}, {"output": np.zeros((1,), np.float32)},
    {"output": np.zeros((1, 1), np.float64)}, {"output": np.zeros((1, 1, 1), np.float32)},
    {"stateN": np.zeros((2, 1, 64), np.float32)}, {"stateN": np.zeros((2, 1, 128), np.float64)}, {"stateN": np.zeros((1, 128), np.float32)},
])
def test_unexpected_output_layout_is_a_contract_error_and_not_a_guess(override):
    session = FakeSession(out_override=override)
    engine = SileroOnnxVadEngine(session=session)
    with pytest.raises(SileroModelContractMismatch):
        engine.process_frame(counter_frame(0))
    assert (engine._state == 0).all() and (engine._context == 0).all()       # nothing advanced


# =======================================================================================
# H. Error handling
# =======================================================================================

def test_an_inference_exception_is_not_a_silent_zero_and_leaves_the_state_alone():
    session = FakeSession(raises=RuntimeError("kernel exploded"))
    engine = SileroOnnxVadEngine(session=session)
    with pytest.raises(SileroInferenceFailed) as err:
        engine.process_frame(counter_frame(0))
    assert err.value.code == "INFERENCE_FAILED" and isinstance(err.value.__cause__, RuntimeError)
    assert "kernel exploded" in str(err.value)
    assert (engine._state == 0).all() and (engine._context == 0).all()


def test_a_missing_runtime_is_reported_as_such(tmp_path, monkeypatch):
    data = b"bytes with a matching hash"
    p = tmp_path / "m.onnx"
    p.write_bytes(data)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)        # makes `import onnxruntime` raise ImportError
    with pytest.raises(SileroRuntimeUnavailable) as err:
        SileroOnnxVadEngine(p, spec=spec_for(data))
    assert err.value.code == "MODEL_LOAD_FAILED"


def test_verified_bytes_that_onnxruntime_cannot_load_are_a_load_failure(tmp_path):
    data = b"hash matches but this is not an ONNX graph"
    p = tmp_path / "m.onnx"
    p.write_bytes(data)
    try:
        SileroOnnxVadEngine(p, spec=spec_for(data))
    except SileroRuntimeUnavailable:
        pytest.skip("onnxruntime cannot be imported on this machine")
    except SileroModelLoadFailed as exc:
        assert exc.code == "MODEL_LOAD_FAILED"
    else:
        pytest.fail("a non-ONNX file was accepted")


def test_the_session_is_built_from_the_verified_bytes_on_the_cpu_provider_with_single_threads(tmp_path, monkeypatch):
    """Spies on the onnxruntime calls with a stand-in module: explicit CPU provider, 1 + 1 threads, verified bytes."""
    import types

    data = b"stand-in bytes for the approved artifact"
    p = tmp_path / "m.onnx"
    p.write_bytes(data)
    seen = {}

    class Options:
        intra_op_num_threads = None
        inter_op_num_threads = None

    def inference_session(model, sess_options=None, providers=None):
        seen.update(model=model, providers=providers, intra=sess_options.intra_op_num_threads,
                    inter=sess_options.inter_op_num_threads)
        return FakeSession()

    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace(SessionOptions=Options, InferenceSession=inference_session))
    SileroOnnxVadEngine(p, spec=spec_for(data))
    assert seen == {"model": data, "providers": ["CPUExecutionProvider"], "intra": 1, "inter": 1}


def test_an_os_level_import_failure_is_also_a_missing_runtime(tmp_path, monkeypatch):
    import builtins

    data = b"bytes with a matching hash"
    p = tmp_path / "m.onnx"
    p.write_bytes(data)
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "onnxruntime":
            raise OSError("blocked by an application control policy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(SileroRuntimeUnavailable, match="blocked"):
        SileroOnnxVadEngine(p, spec=spec_for(data))


def test_an_unreadable_model_file_is_a_load_failure_not_a_crash(tmp_path, monkeypatch):
    p = tmp_path / "m.onnx"
    p.write_bytes(b"x")

    def refuse(self):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with pytest.raises(SileroModelLoadFailed, match="could not read") as err:
        SileroOnnxVadEngine(p)
    assert err.value.code == "MODEL_LOAD_FAILED"


# =======================================================================================
# I. Fake-session inference through the public API
# =======================================================================================

def test_scores_come_from_the_session_in_order():
    engine = SileroOnnxVadEngine(session=FakeSession(scores=[0.1, 0.9, 0.4, 0.0]))
    assert [engine.process_frame(counter_frame(k)) for k in range(4)] == pytest.approx([0.1, 0.9, 0.4, 0.0])


def test_providers_are_reported_from_the_session():
    assert SileroOnnxVadEngine(session=FakeSession(providers=("CPUExecutionProvider",))).providers == ("CPUExecutionProvider",)


# =======================================================================================
# J. Real-model smoke tests (plumbing only: NOT VAD accuracy)
# =======================================================================================

def test_real_model_loads_on_the_cpu_provider_and_matches_its_contract(real_engine):
    assert real_engine.providers == ("CPUExecutionProvider",)
    assert (real_engine.sample_rate, real_engine.frame_samples, real_engine.context_samples) == (16000, 512, 64)


def test_real_model_executes_on_a_zero_frame(real_engine):
    score = real_engine.process_frame(np.zeros(512, np.int16))
    assert isinstance(score, float) and np.isfinite(score) and 0.0 <= score <= 1.0   # plumbing only: no value is asserted


def test_real_model_scores_are_finite_and_in_range_for_deterministic_noise(real_engine):
    rng = np.random.default_rng(1234)
    pcm = (rng.standard_normal(512 * 8) * 3000).astype(np.int16)
    for i in range(8):
        s = real_engine.process_frame(pcm[i * 512:(i + 1) * 512])
        assert np.isfinite(s) and 0.0 <= s <= 1.0


def test_real_model_reset_is_deterministic(real_engine, real_model_path):
    rng = np.random.default_rng(99)
    pcm = (rng.standard_normal(512 * 6) * 3000).astype(np.int16)
    run = lambda e: [e.process_frame(pcm[i * 512:(i + 1) * 512]) for i in range(6)]
    first = run(real_engine)
    real_engine.reset()
    again = run(real_engine)
    fresh = run(SileroOnnxVadEngine(real_model_path))
    np.testing.assert_allclose(again, first, rtol=0, atol=1e-6)
    np.testing.assert_allclose(fresh, first, rtol=0, atol=1e-6)


def test_real_model_state_carries_between_calls_and_reset_clears_it(real_engine, real_model_path):
    rng = np.random.default_rng(5)
    loud = (rng.standard_normal(512) * 8000).astype(np.int16)
    zero = np.zeros(512, np.int16)
    fresh_zero = SileroOnnxVadEngine(real_model_path).process_frame(zero)
    for _ in range(5):
        real_engine.process_frame(loud)
    assert np.any(real_engine._state != 0) and np.any(real_engine._context != 0)
    real_engine.reset()
    assert real_engine.process_frame(zero) == pytest.approx(fresh_zero, abs=1e-6)   # no leak from the loud history


def test_real_model_does_not_modify_the_pcm_it_is_given(real_engine):
    pcm = counter_frame(1)
    before = pcm.copy()
    real_engine.process_frame(pcm)
    assert np.array_equal(pcm, before)


def test_the_canonical_artifact_is_still_the_approved_one(real_model_path):
    assert hashlib.sha256(real_model_path.read_bytes()).hexdigest() == SPEC.sha256


# =======================================================================================
# K. Task 1 interface integration
# =======================================================================================

def _task1_config():
    return SegmenterConfig(sample_rate=16000, vad_frame_samples=512, start_threshold=0.6, end_threshold=0.4,
                           start_confirmation_s=0.05, end_silence_s=0.1, onset_padding_s=0.03, pre_roll_s=0.2,
                           no_speech_timeout_s=1.0, min_utterance_s=0.06, max_utterance_s=5.0)


def _loud_when_audible(row):
    """Scripted stand-in for the model: 'speech' when the NEW part of the input is loud. TEST / SYNTHETIC."""
    return 0.9 if np.abs(row[SPEC.context_samples:]).max() > 0.1 else 0.0


def test_the_engine_is_a_vad_engine_with_the_exact_task1_shape():
    engine = SileroOnnxVadEngine(session=FakeSession())
    assert isinstance(engine, VadEngine)
    assert (engine.sample_rate, engine.frame_samples) == (16000, 512)
    UtteranceSegmenter(_task1_config(), engine)                  # accepted without any change to the segmenter


def test_the_segmenter_runs_on_the_adapter_and_never_sees_the_model_context():
    session = FakeSession(score_fn=_loud_when_audible)
    seg = UtteranceSegmenter(_task1_config(), SileroOnnxVadEngine(session=session))
    pcm = np.zeros(40 * 512, np.int16)
    pcm[5 * 512:15 * 512] = 8000                                  # frames 5..14: synthetic loud block
    seg.activate(0)
    result, pos = None, 0
    while result is None and pos < len(pcm):
        result = seg.push(pcm[pos:pos + 1000], pos)               # blocks that do not match the frame size
        pos += 1000
    assert result.outcome is SegmentOutcome.COMPLETE
    s = result.segment
    # indices are in NEW stream samples only: onset at frame 5, padding 480 back, ends at the end of frame 14
    assert (s.onset_sample, s.start_sample, s.end_sample) == (5 * 512, 5 * 512 - 480, 15 * 512)
    assert np.array_equal(s.samples, pcm[s.start_sample:s.end_sample])      # the stream audio itself, no context, no float
    assert s.samples.dtype == np.int16 and s.duration_samples == s.end_sample - s.start_sample
    assert result.decided_at_sample == 19 * 512                   # 4 silent frames after the last loud one
    # the model was fed 576 samples per call although the stream advanced by 512 per call
    assert len(session.calls) == 19 and all(c["input"].shape == (1, 576) for c in session.calls)


def test_activation_resets_the_adapter_so_no_context_or_state_survives_into_a_new_capture():
    session = FakeSession(score_fn=lambda row: 0.0)
    engine = SileroOnnxVadEngine(session=session)
    seg = UtteranceSegmenter(_task1_config(), engine)
    seg.activate(0)
    seg.push(np.full(2048, 7000, np.int16), 0)                   # leaves non-zero context and state behind
    assert np.any(engine._context != 0) and np.any(engine._state != 0)
    seg.reset()                                                  # the segmenter's reset does not touch the engine
    assert np.any(engine._context != 0)
    before = len(session.calls)
    seg.activate(10_000)                                         # activation resets the engine exactly once
    seg.push(np.full(512, 7000, np.int16), 10_000)
    first = session.calls[before]
    assert (first["input"][0, :64] == 0).all() and (first["state"] == 0).all()


def test_the_real_adapter_plugs_into_the_segmenter(real_engine):
    """Plumbing only: no outcome is asserted, because this is not a speech test."""
    seg = UtteranceSegmenter(_task1_config(), real_engine)
    seg.activate(0)
    rng = np.random.default_rng(7)
    noise = (rng.standard_normal(16000) * 200).astype(np.int16)
    result, pos = None, 0
    while result is None and pos < len(noise):
        result = seg.push(noise[pos:pos + 700], pos)
        pos += 700
    result = result or seg.finish()
    assert result.outcome in set(SegmentOutcome) and result.activation_sample == 0


# =======================================================================================
# L. Import / no-network behaviour
# =======================================================================================

def _run_fresh(code):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))


def test_importing_the_module_loads_no_runtime_and_no_downloader():
    code = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import audio.silero_vad\n"
        f"bad = [m for m in ('onnxruntime',) + {NETWORK_MODULES!r} if m in sys.modules and m not in before]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = _run_fresh(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_building_and_running_the_real_engine_imports_no_network_module(real_model_path):
    code = (
        "import sys\n"
        "try:\n"
        "    import onnxruntime\n"
        "except Exception:\n"
        "    print('SKIP'); sys.exit(0)\n"
        "baseline = set(sys.modules)\n"
        "import numpy as np\n"
        "from audio.silero_vad import SileroOnnxVadEngine\n"
        "e = SileroOnnxVadEngine()\n"
        "for _ in range(3): e.process_frame(np.zeros(512, np.int16))\n"
        f"bad = [m for m in {NETWORK_MODULES!r} if m in sys.modules and m not in baseline]\n"
        "print('BAD', bad) if bad else print('OK')\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = _run_fresh(code)
    if "SKIP" in proc.stdout:
        pytest.skip("onnxruntime cannot be imported on this machine")
    assert proc.returncode == 0 and "OK" in proc.stdout, proc.stdout + proc.stderr


def test_the_module_source_imports_nothing_that_can_download():
    tree = ast.parse((REPO_ROOT / "audio" / "silero_vad.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"urllib", "http", "requests", "socket", "ssl", "huggingface_hub", "torch", "torchaudio",
                                "silero_vad", "onnx", "ftplib", "wget"})
    assert "onnxruntime" in imported            # present, but only as a lazy import inside a function:
    top_level = {n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)}
    assert "onnxruntime" not in top_level


# =======================================================================================
# M. Post-adversarial hardening (Task 2 fresh-session review). Test/evidence closure plus small contract-precision
#    fixes; no change to the Silero recurrence, scaling, 512/64 contract or the approved artifact.
# =======================================================================================

def test_production_load_reads_the_file_once_and_hands_exactly_those_bytes_to_the_session(tmp_path, monkeypatch):
    """Closes the 're-read after hashing' (TOCTOU) mutant: any second read returns DIFFERENT bytes."""
    approved = b"stand-in for the approved artifact bytes"
    p = tmp_path / "m.onnx"
    p.write_bytes(approved)
    original = Path.read_bytes
    reads = []

    def read_bytes(self):
        if self == p:
            reads.append(1)
            return approved if len(reads) == 1 else b"SWAPPED AFTER THE HASH CHECK"
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    captured = []
    monkeypatch.setattr(sv, "_create_cpu_session", lambda data: captured.append(data) or FakeSession())
    engine = SileroOnnxVadEngine(p, spec=spec_for(approved))
    assert len(reads) == 1                                        # the file is read exactly once
    assert len(captured) == 1 and captured[0] is approved         # the very bytes that were hashed reach the session
    assert engine.artifact_verified is True


def test_default_model_path_is_models_under_the_repo_root_even_without_the_model():
    assert resolve_model_path() == REPO_ROOT / "models" / SPEC.filename


def test_the_spec_filename_drives_the_default_path_and_the_not_found_message():
    custom = dataclasses.replace(SPEC, filename="some_other_name.onnx")
    assert resolve_model_path(None, custom) == REPO_ROOT / "models" / "some_other_name.onnx"
    with pytest.raises(SileroModelNotFound) as err:
        SileroOnnxVadEngine(spec=custom)                          # that file does not exist, nothing is downloaded
    # the PATH that was looked up (not just the filename quoted in the hint) comes from the spec
    assert repr(str(REPO_ROOT / "models" / "some_other_name.onnx")) in str(err.value)


def test_contract_validation_uses_the_spec_it_is_given():
    renamed = tuple(dataclasses.replace(t, name="audio") if t.name == "input" else t for t in SPEC.inputs)
    custom = dataclasses.replace(SPEC, inputs=renamed, audio_name="audio")
    SileroOnnxVadEngine(session=FakeSession(inputs=nodes_from(renamed)), spec=custom)       # right under the custom spec
    with pytest.raises(SileroModelContractMismatch):
        SileroOnnxVadEngine(session=FakeSession(inputs=nodes_from(renamed)))                # wrong under the default spec


class _OtherError(Exception):
    pass


@pytest.mark.parametrize("exc", [OrtLikeError("InvalidArgument-like"), ValueError("v"), KeyError("k"), OSError("os"),
                                 TypeError("t"), _OtherError("x")], ids=lambda e: type(e).__name__)
def test_an_inference_exception_of_any_type_is_inference_failed_and_does_not_advance_state(exc):
    """Real onnxruntime exceptions are plain Exception subclasses, not RuntimeError."""
    assert not isinstance(exc, RuntimeError)
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    f = [counter_frame(k) for k in range(3)]
    engine.process_frame(f[0])
    context_before, state_before = engine._context.copy(), engine._state.copy()
    session.raises = exc
    with pytest.raises(SileroInferenceFailed) as err:
        engine.process_frame(f[1])
    assert err.value.code == "INFERENCE_FAILED" and err.value.__cause__ is exc
    assert np.array_equal(engine._context, context_before) and np.array_equal(engine._state, state_before)
    session.raises = None
    engine.process_frame(f[2])                                    # the next call sees the state left by call 1
    assert (session.calls[-1]["state"] == 1.0).all()
    assert np.array_equal(session.calls[-1]["input"][0, :64], (f[0].astype(np.float32) / np.float32(32768))[-64:])


def test_the_fake_session_rejects_what_the_real_graph_rejects():
    """Self-test of the double: it must be strict about element types and the literal state dimensions."""
    good = dict(input=np.zeros((1, 576), np.float32), state=np.zeros((2, 1, 128), np.float32), sr=np.array(16000, np.int64))
    for key, bad in (("input", np.zeros((1, 576), np.float64)), ("state", np.zeros((2, 1, 128), np.float64)),
                     ("state", np.zeros((2, 1, 64), np.float32)), ("sr", np.array(16000, np.float32))):
        with pytest.raises(OrtLikeError):
            FakeSession().run(["output", "stateN"], {**good, key: bad})
    FakeSession().run(["output", "stateN"], good)                 # a good feed is accepted


def test_a_float64_context_would_now_be_caught_by_the_fake_like_by_the_real_graph():
    engine = SileroOnnxVadEngine(session=FakeSession())
    engine._context = np.zeros(64)                                # float64: what a broken reset would leave behind
    with pytest.raises(SileroInferenceFailed):
        engine.process_frame(counter_frame(0))


def test_after_a_reset_the_next_feed_is_still_exactly_float32_float32_int64_with_the_runtime_shapes():
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    for k in range(3):
        engine.process_frame(counter_frame(k))
    engine.reset()
    engine.reset()
    engine.process_frame(counter_frame(3))
    call = session.calls[-1]
    assert call["input_dtype"] == np.float32 and call["input"].shape == (1, 576)
    assert call["state_dtype"] == np.float32 and call["state"].shape == (2, 1, 128)
    assert call["sr_dtype"] == np.int64 and call["sr"].shape == () and int(call["sr"]) == 16000
    assert engine._context.dtype == np.float32 and engine._state.dtype == np.float32


@pytest.mark.parametrize("shape", [(1, 2, 128), (2, 128, 1), (256, 1, 1), (128, 2, 1), (1, 256, 1), (2, 1, 128, 1), (256,)])
def test_a_state_with_the_right_element_count_but_the_wrong_shape_is_rejected(shape):
    assert int(np.prod(shape)) == 2 * 1 * 128                     # same total size as the real state
    session = FakeSession(out_override={"stateN": np.zeros(shape, np.float32)})
    engine = SileroOnnxVadEngine(session=session)
    with pytest.raises(SileroModelContractMismatch) as err:
        engine.process_frame(counter_frame(0))
    assert err.value.code == "MODEL_CONTRACT_MISMATCH"
    assert (engine._state == 0).all() and (engine._context == 0).all()


@pytest.mark.parametrize("which,name", [("in", "input"), ("in", "state"), ("in", "sr"), ("out", "output"), ("out", "stateN")])
def test_duplicate_metadata_names_are_a_contract_mismatch(which, name):
    ins, outs = nodes_from(SPEC.inputs), nodes_from(SPEC.outputs)
    target = ins if which == "in" else outs
    original = next(n for n in target if n.name == name)
    target.append(FakeNode(original.name, original.type, list(original.shape)))
    session = FakeSession(inputs=ins, outputs=outs)
    with pytest.raises(SileroModelContractMismatch, match="duplicated") as err:
        SileroOnnxVadEngine(session=session)
    assert repr(name) in str(err.value) and session.calls == []


@pytest.mark.parametrize("which,name,how", [
    ("in", "sr", "none"), ("in", "sr", "missing"), ("in", "input", "none"), ("in", "state", "none"),
    ("out", "output", "none"), ("out", "stateN", "none"), ("out", "stateN", "missing"),
])
def test_unknown_rank_is_unknown_and_never_taken_for_a_scalar(which, name, how):
    ins, outs = nodes_from(SPEC.inputs), nodes_from(SPEC.outputs)
    node = next(n for n in (ins if which == "in" else outs) if n.name == name)
    if how == "none":
        node.shape = None
    else:
        del node.shape
    session = FakeSession(inputs=ins, outputs=outs)
    with pytest.raises(SileroModelContractMismatch, match="unknown rank"):
        SileroOnnxVadEngine(session=session)
    assert session.calls == []


def test_a_known_scalar_sr_is_still_accepted():
    validate_session_contract(FakeSession())                      # sr reported as [] (a positively known scalar)
    ins = nodes_from(SPEC.inputs)
    assert next(n for n in ins if n.name == "sr").shape == []
    validate_session_contract(FakeSession(inputs=ins))


def _good_outputs():
    return [np.array([[0.25]], np.float32), np.zeros((2, 1, 128), np.float32)]


@pytest.mark.parametrize("raw", [None, [], [_good_outputs()[0]], _good_outputs() + [_good_outputs()[0]], "ab", 5,
                                 {"output": 1, "stateN": 2}], ids=["None", "empty", "one", "three", "str", "int", "dict"])
def test_a_structurally_invalid_run_result_is_a_contract_mismatch_not_an_inference_failure(raw):
    session = FakeSession()
    engine = SileroOnnxVadEngine(session=session)
    engine.process_frame(counter_frame(0))
    context_before, state_before = engine._context.copy(), engine._state.copy()
    session.raw_return = raw
    with pytest.raises(SileroModelContractMismatch) as err:
        engine.process_frame(counter_frame(1))
    assert err.value.code == "MODEL_CONTRACT_MISMATCH" and not isinstance(err.value, SileroInferenceFailed)
    assert np.array_equal(engine._context, context_before) and np.array_equal(engine._state, state_before)


def test_a_two_item_tuple_result_is_accepted_and_an_exception_is_still_an_inference_failure():
    session = FakeSession(raw_return=tuple(_good_outputs()))
    assert SileroOnnxVadEngine(session=session).process_frame(counter_frame(0)) == pytest.approx(0.25)
    failing = FakeSession(raises=OrtLikeError("boom"))
    with pytest.raises(SileroInferenceFailed):
        SileroOnnxVadEngine(session=failing).process_frame(counter_frame(0))


class _LogRecorder:
    def __init__(self):
        self.lines = []

    def _rec(self, msg, *args):
        self.lines.append(msg % args if args else msg)

    info = warning = error = debug = _rec


def test_only_a_verified_file_load_is_marked_verified_and_the_log_never_implies_it_otherwise(tmp_path, monkeypatch):
    rec = _LogRecorder()
    monkeypatch.setattr(sv, "logger", rec)
    data = b"approved stand-in bytes"
    p = tmp_path / "m.onnx"
    p.write_bytes(data)
    spec = spec_for(data)
    monkeypatch.setattr(sv, "_create_cpu_session", lambda b: FakeSession())

    verified = SileroOnnxVadEngine(p, spec=spec)                   # the production path: file -> hash -> session
    assert verified.artifact_verified is True and verified.verified_sha256 == spec.sha256
    line = rec.lines[-1]
    assert "artifact_verified=true" in line and spec.sha256[:12] in line

    for injected in (SileroOnnxVadEngine(session=FakeSession(), spec=spec), SileroOnnxVadEngine(session=FakeSession())):
        assert injected.artifact_verified is False and injected.verified_sha256 is None
        line = rec.lines[-1]
        assert "artifact_verified=false" in line and "injected_session" in line
        for approved in (spec.sha256, SPEC.sha256):
            assert approved[:12] not in line                       # no hash is quoted for bytes that were never verified
        assert "sha256" not in line
    with pytest.raises(AttributeError):
        verified.artifact_verified = False                         # read-only


def test_the_real_model_engine_is_marked_verified(real_engine):
    assert real_engine.artifact_verified is True and real_engine.verified_sha256 == SPEC.sha256
