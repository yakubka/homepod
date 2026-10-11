"""Team Leader AI-2 software dependency: the faster-whisper backend (speech/faster_whisper_backend.py).

Two tiers, kept apart on purpose:
  * Tier 1 - a scripted fake WhisperModel and temporary snapshot directories. SOFTWARE-VALIDATED. These prove
    how OUR adapter behaves (decoder arguments, PCM scaling, lazy consumption, timing, failure channel,
    provenance, privacy). They run anywhere and need no model.
  * Tier 2 - the real tiny.en snapshot in models/ (git-ignored; skipped when it is absent or faster-whisper is not
    importable). They show only that the approved snapshot verifies, loads and executes, and that repeated calls
    behave. They say NOTHING about recognition accuracy: synthetic audio proves no such thing.
"""

import ast
import dataclasses
import hashlib
import logging
import math
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from speech import faster_whisper_backend as fb
from speech.faster_whisper_backend import (
    DECODER_OPTIONS, DEFAULT_MAX_AUDIO_S, FASTER_WHISPER_TINY_EN, UNVERIFIED_MODEL_ID, FasterWhisperEngine, ModelFile,
    SttModelHashMismatch, resolve_model_dir, verify_model_snapshot,
)
from speech.stt import (
    SttContractError, SttInferenceFailed, SttInputError, SttModelLoadFailed, SttModelNotFound, SttRuntimeUnavailable,
    SttStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RATE = 16000


def pcm(n=1600, value=100):
    return np.full(n, value, dtype=np.int16)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _LogCapture(logging.Handler):
    """The homepod_ai logger has propagate=False, so caplog cannot see it."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    fb.logger.addHandler(handler)
    yield handler.lines
    fb.logger.removeHandler(handler)


class FakeWhisper:
    """Scripted stand-in for faster_whisper.WhisperModel (TEST DOUBLE: never counts as a verified model)."""

    def __init__(self, texts=(" hello",), rate=16000, multilingual=False, clock=None, tick=0.0,
                 call_error=None, iter_error=None, result="pair"):
        self.feature_extractor = SimpleNamespace(sampling_rate=rate)
        self.model = SimpleNamespace(is_multilingual=multilingual)
        self.texts, self.clock, self.tick = list(texts), clock, tick
        self.call_error, self.iter_error, self.result = call_error, iter_error, result
        self.calls = []             # (audio copy, kwargs)
        self.yielded = 0
        self.exhausted = False

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio.copy(), kwargs, audio.flags.writeable))
        if self.call_error:
            raise self.call_error

        def segments():
            for text in self.texts:
                if self.clock is not None:
                    self.clock.now += self.tick           # decoding cost is paid while iterating
                if self.iter_error:
                    raise self.iter_error
                self.yielded += 1
                yield SimpleNamespace(text=text)
            self.exhausted = True

        if self.result == "pair":
            return segments(), SimpleNamespace(language="en")
        return self.result


def engine(**kw):
    model = kw.pop("model", None) or FakeWhisper()
    return FasterWhisperEngine(model=model, **kw)


# -- decoder call --------------------------------------------------------------------------------------------

def test_decoder_options_are_pinned():
    assert DECODER_OPTIONS == {
        "language": "en", "condition_on_previous_text": False, "vad_filter": False, "temperature": 0.0, "beam_size": 5,
    }
    assert isinstance(DECODER_OPTIONS["temperature"], float)      # a single value, never the fallback list


def test_transcribe_is_called_with_exactly_the_pinned_options():
    model = FakeWhisper()
    engine(model=model).transcribe(pcm(), RATE)
    assert len(model.calls) == 1
    _audio, kwargs, _w = model.calls[0]
    assert kwargs == DECODER_OPTIONS
    assert kwargs["temperature"] == 0.0 and not isinstance(kwargs["temperature"], (list, tuple))
    assert kwargs["language"] == "en" and kwargs["condition_on_previous_text"] is False and kwargs["vad_filter"] is False
    assert kwargs["beam_size"] == 5


def test_engine_identity_and_constants():
    e = engine()
    assert (e.sample_rate, e.engine_id) == (16000, "faster-whisper")
    assert (fb.DEVICE, fb.COMPUTE_TYPE) == ("cpu", "int8")
    assert e.max_audio_s == DEFAULT_MAX_AUDIO_S == 30.0


# -- PCM conversion ------------------------------------------------------------------------------------------

def test_pcm_scaling_is_exact_int16_over_32768_in_float32():
    model = FakeWhisper()
    samples = np.array([-32768, -1, 0, 1, 32767], dtype=np.int16)
    engine(model=model).transcribe(samples, RATE)
    audio, _kw, _writeable = model.calls[0]
    assert audio.dtype == np.float32 and audio.shape == (5,)
    assert audio.tolist() == [-1.0, -1 / 32768, 0.0, 1 / 32768, 32767 / 32768]
    assert audio[0] == -1.0 and audio[-1] == np.float32(0.999969482421875)


def test_caller_pcm_is_unchanged_by_conversion():
    samples = np.array([-32768, -1, 0, 1, 32767] * 320, dtype=np.int16)
    before = samples.copy()
    engine().transcribe(samples, RATE)
    assert samples.dtype == np.int16 and np.array_equal(samples, before) and samples.flags.writeable


def test_audio_given_to_the_model_is_a_new_array_with_no_gain_or_resampling():
    model = FakeWhisper()
    samples = pcm(RATE, value=16384)
    engine(model=model).transcribe(samples, RATE)
    audio, _kw, _w = model.calls[0]
    assert audio.size == samples.size                       # no resampling, no padding
    assert np.all(audio == 0.5)                             # 16384 / 32768, no gain


# -- lazy segment consumption and text assembly -----------------------------------------------------------------

def test_every_segment_is_consumed_and_joined_in_order():
    model = FakeWhisper(texts=(" What is", " the temperature", "?"))
    r = engine(model=model).transcribe(pcm(), RATE)
    assert model.yielded == 3 and model.exhausted
    assert r.status is SttStatus.OK and r.text == "What is the temperature ?"


def test_segment_boundaries_never_glue_words_together():
    r = engine(model=FakeWhisper(texts=("how many", "people"))).transcribe(pcm(), RATE)
    assert r.text == "how many people"


def test_an_empty_segment_iterable_is_empty_text():
    model = FakeWhisper(texts=())
    r = engine(model=model).transcribe(pcm(), RATE)
    assert r.status is SttStatus.EMPTY_TEXT and r.text == "" and model.exhausted


def test_whitespace_only_segments_are_empty_text():
    r = engine(model=FakeWhisper(texts=(" ", "\n", "  \t"))).transcribe(pcm(), RATE)
    assert r.status is SttStatus.EMPTY_TEXT and r.text == ""


def test_blank_segments_between_real_ones_are_dropped():
    r = engine(model=FakeWhisper(texts=(" a", "  ", " b"))).transcribe(pcm(), RATE)
    assert r.text == "a b"


def test_inference_time_includes_consuming_the_lazy_segments():
    clock = FakeClock()
    model = FakeWhisper(texts=("a", "b", "c"), clock=clock, tick=0.1)
    r = engine(model=model, clock=clock).transcribe(pcm(), RATE)
    assert r.inference_ms == pytest.approx(300.0)           # all three segments, none before transcribe() returned
    assert model.exhausted


# -- failure channel ----------------------------------------------------------------------------------------------

def test_exception_when_calling_the_decoder_is_inference_failed():
    boom = RuntimeError("ctranslate2 exploded")
    with pytest.raises(SttInferenceFailed) as info:
        engine(model=FakeWhisper(call_error=boom)).transcribe(pcm(), RATE)
    assert info.value.__cause__ is boom


def test_exception_while_iterating_segments_is_inference_failed_not_empty_text():
    boom = ValueError("decode failed mid-way")
    with pytest.raises(SttInferenceFailed) as info:
        engine(model=FakeWhisper(texts=("a", "b"), iter_error=boom)).transcribe(pcm(), RATE)
    assert info.value.__cause__ is boom


@pytest.mark.parametrize("result", [None, [], ["seg"], ("only-one",), ("a", "b", "c"), "text", 7])
def test_unexpected_transcribe_result_structure_is_a_contract_error(result):
    with pytest.raises(SttContractError):
        engine(model=FakeWhisper(result=result)).transcribe(pcm(), RATE)


def test_a_segment_without_str_text_is_a_contract_error():
    class BadModel(FakeWhisper):
        def transcribe(self, audio, **kw):
            return iter([SimpleNamespace(text=None)]), SimpleNamespace()

    with pytest.raises(SttContractError):
        engine(model=BadModel()).transcribe(pcm(), RATE)

    class NoText(FakeWhisper):
        def transcribe(self, audio, **kw):
            return iter([object()]), SimpleNamespace()

    with pytest.raises(SttContractError):
        engine(model=NoText()).transcribe(pcm(), RATE)


# -- model contract at construction ------------------------------------------------------------------------------------

@pytest.mark.parametrize("kw", [dict(rate=8000), dict(rate=44100), dict(multilingual=True), dict(multilingual=None)])
def test_a_model_that_breaks_the_contract_is_rejected(kw):
    with pytest.raises(SttContractError):
        FasterWhisperEngine(model=FakeWhisper(**kw))


def test_a_model_missing_the_expected_attributes_is_rejected():
    with pytest.raises(SttContractError):
        FasterWhisperEngine(model=SimpleNamespace(model=SimpleNamespace(is_multilingual=False)))      # no feature_extractor
    with pytest.raises(SttContractError):
        FasterWhisperEngine(model=SimpleNamespace(feature_extractor=SimpleNamespace(sampling_rate=16000)))  # no .model


# -- provenance honesty ----------------------------------------------------------------------------------------------------

def test_an_injected_model_never_claims_to_be_verified(logs):
    e = engine()
    assert e.snapshot_verified is False
    assert e.model_id == UNVERIFIED_MODEL_ID and e.load_ms is None
    line = next(l for l in logs if "event=stt_engine_ready" in l)
    assert "snapshot_verified=false" in line and "injected_model" in line
    for secret in (FASTER_WHISPER_TINY_EN.revision, FASTER_WHISPER_TINY_EN.repo, FASTER_WHISPER_TINY_EN.files[1].sha256, "total_bytes"):
        assert secret not in line


def test_model_dir_and_model_cannot_both_be_given():
    with pytest.raises(ValueError):
        FasterWhisperEngine(model_dir="x", model=FakeWhisper())


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "5", None])
def test_invalid_max_audio_s_is_rejected(bad):
    with pytest.raises(ValueError):
        engine(max_audio_s=bad)


def test_max_audio_s_is_enforced_by_this_backend_only():
    e = engine(max_audio_s=2.0)
    assert e.transcribe(pcm(2 * RATE), RATE).status is SttStatus.OK
    with pytest.raises(SttInputError):
        e.transcribe(pcm(2 * RATE + 1), RATE)
    default = engine()
    with pytest.raises(SttInputError):
        default.transcribe(pcm(31 * RATE), RATE)
    assert default.transcribe(pcm(30 * RATE), RATE).status is SttStatus.OK


# -- the snapshot manifest ---------------------------------------------------------------------------------------------------

def make_snapshot(tmp_path, files=None):
    files = files or {"config.json": b'{"a": 1}', "model.bin": b"\x00\x01" * 100, "tokenizer.json": b"{}", "vocabulary.txt": b"hi\nthere\n"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    entries = tuple(ModelFile(n, len(d), hashlib.sha256(d).hexdigest()) for n, d in sorted(files.items()))
    spec = dataclasses.replace(FASTER_WHISPER_TINY_EN, files=entries)
    return tmp_path, spec


def test_a_matching_snapshot_verifies_and_extra_files_are_ignored(tmp_path):
    d, spec = make_snapshot(tmp_path)
    (d / "README.md").write_text("not part of the contract")
    (d / ".cache").mkdir()
    assert verify_model_snapshot(d, spec) == spec.total_size_bytes


def test_missing_directory_and_missing_file_are_model_not_found(tmp_path):
    d, spec = make_snapshot(tmp_path)
    with pytest.raises(SttModelNotFound, match="directory not found"):
        verify_model_snapshot(tmp_path / "nope", spec)
    (d / "tokenizer.json").unlink()
    with pytest.raises(SttModelNotFound, match="tokenizer.json"):
        verify_model_snapshot(d, spec)


def test_wrong_size_and_same_size_wrong_content_are_hash_mismatches(tmp_path):
    d, spec = make_snapshot(tmp_path)
    (d / "vocabulary.txt").write_bytes(b"hi\nthere\n!!")                      # different size
    with pytest.raises(SttModelHashMismatch, match="size"):          # rejected on size alone, before any hashing
        verify_model_snapshot(d, spec)
    (tmp_path / "second").mkdir()
    d2, spec2 = make_snapshot(tmp_path / "second")
    (d2 / "model.bin").write_bytes(b"\x01\x00" * 100)                         # same size, different bytes
    with pytest.raises(SttModelHashMismatch, match="SHA-256"):
        verify_model_snapshot(d2, spec2)
    assert issubclass(SttModelHashMismatch, SttModelLoadFailed)


def test_a_verified_directory_builds_the_model_locally_and_never_online(tmp_path, logs):
    d, spec = make_snapshot(tmp_path)
    calls = []

    def factory(path, **kwargs):
        calls.append((path, kwargs))
        return FakeWhisper()

    e = FasterWhisperEngine(d, spec=spec, model_factory=factory)
    assert calls == [(str(d), {"device": "cpu", "compute_type": "int8", "local_files_only": True})]
    assert e.snapshot_verified is True and e.model_id == spec.model_id and e.load_ms >= 0
    line = next(l for l in logs if "event=stt_engine_ready" in l)
    assert "snapshot_verified=true" in line and spec.revision in line


def test_the_snapshot_is_verified_before_the_model_is_built(tmp_path):
    d, spec = make_snapshot(tmp_path)
    (d / "config.json").write_bytes(b'{"a": 2}')
    calls = []
    with pytest.raises(SttModelHashMismatch):
        FasterWhisperEngine(d, spec=spec, model_factory=lambda p, **k: calls.append(p) or FakeWhisper())
    assert calls == []                                                          # nothing was loaded from an unverified directory
    with pytest.raises(SttModelNotFound):
        FasterWhisperEngine(tmp_path / "missing", spec=spec, model_factory=lambda p, **k: calls.append(p) or FakeWhisper())
    assert calls == []


def test_a_model_that_fails_to_load_is_model_load_failed(tmp_path):
    d, spec = make_snapshot(tmp_path)
    boom = OSError("corrupt model.bin")

    def factory(path, **kwargs):
        raise boom

    with pytest.raises(SttModelLoadFailed) as info:
        FasterWhisperEngine(d, spec=spec, model_factory=factory)
    assert info.value.__cause__ is boom


def test_runtime_unavailable_passes_through_and_is_not_a_load_failure(tmp_path):
    d, spec = make_snapshot(tmp_path)

    def factory(path, **kwargs):
        raise SttRuntimeUnavailable("blocked")

    with pytest.raises(SttRuntimeUnavailable):
        FasterWhisperEngine(d, spec=spec, model_factory=factory)


def test_default_factory_reports_a_missing_or_blocked_runtime(tmp_path, monkeypatch):
    d, spec = make_snapshot(tmp_path)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)                    # makes `import faster_whisper` raise ImportError
    with pytest.raises(SttRuntimeUnavailable):
        FasterWhisperEngine(d, spec=spec)


def test_default_factory_builds_whisper_from_the_local_directory_never_a_model_name(tmp_path, monkeypatch):
    d, spec = make_snapshot(tmp_path)
    seen = []

    class RecordingWhisperModel(FakeWhisper):
        def __init__(self, path, **kwargs):
            super().__init__()
            seen.append((path, kwargs))

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=RecordingWhisperModel))
    e = FasterWhisperEngine(d, spec=spec)
    assert seen == [(str(d), {"device": "cpu", "compute_type": "int8", "local_files_only": True})]
    assert e.snapshot_verified is True


def test_default_model_directory_is_under_models():
    p = resolve_model_dir()
    assert p == REPO_ROOT / "models" / FASTER_WHISPER_TINY_EN.directory_name
    assert resolve_model_dir("models/x") == REPO_ROOT / "models" / "x"
    assert resolve_model_dir(str(REPO_ROOT)) == REPO_ROOT


def test_the_approved_manifest_record():
    s = FASTER_WHISPER_TINY_EN
    assert (s.repo, s.revision, s.license) == ("Systran/faster-whisper-tiny.en", "0d3d19a32d3338f10357c0889762bd8d64bbdeba", "MIT")
    assert s.model_id == "Systran/faster-whisper-tiny.en@0d3d19a32d3338f10357c0889762bd8d64bbdeba"
    assert s.directory_name == "faster-whisper-tiny.en-" + s.revision[:8]
    assert [f.path for f in s.files] == ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
    assert s.total_size_bytes == 78090594 == sum(f.size_bytes for f in s.files)
    assert all(len(f.sha256) == 64 and set(f.sha256) <= set("0123456789abcdef") for f in s.files)
    assert len({f.sha256 for f in s.files}) == 4


# -- privacy, network and imports ------------------------------------------------------------------------------------------------

def test_transcripts_never_reach_the_logs_through_the_backend(logs):
    engine(model=FakeWhisper(texts=(" the secret exam answers",))).transcribe(pcm(), RATE)
    with pytest.raises(SttInferenceFailed):
        engine(model=FakeWhisper(call_error=RuntimeError("bad secret exam answers"))).transcribe(pcm(), RATE)
    joined = "\n".join(logs)
    assert "secret" not in joined and "exam" not in joined and "event=stt_done" in joined


def test_backend_source_has_no_network_or_download_imports():
    tree = ast.parse((REPO_ROOT / "speech" / "faster_whisper_backend.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"hashlib", "math", "time", "dataclasses", "pathlib", "typing", "numpy", "config", "speech", "faster_whisper"}
    called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not called & {"snapshot_download", "hf_hub_download", "download_model", "urlopen", "requests", "socket"}


def test_importing_the_package_loads_no_engine_and_no_model_even_when_an_engine_is_built():
    code = (
        "import sys\n"
        "import speech, speech.stt, speech.faster_whisper_backend as fb\n"
        "bad = [m for m in ('faster_whisper', 'ctranslate2', 'av', 'huggingface_hub', 'tokenizers', 'onnxruntime') if m in sys.modules]\n"
        "assert not bad, ('after import', bad)\n"
        "from types import SimpleNamespace as N\n"
        "model = N(feature_extractor=N(sampling_rate=16000), model=N(is_multilingual=False), transcribe=lambda a, **k: (iter([]), N()))\n"
        "fb.FasterWhisperEngine(model=model)\n"
        "bad = [m for m in ('faster_whisper', 'ctranslate2', 'av', 'huggingface_hub') if m in sys.modules]\n"
        "assert not bad, ('after injected construction', bad)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr


# =============================================================================================================
# Tier 2 - the real approved tiny.en snapshot (skipped when absent). Proves it verifies, loads and executes.
# Says nothing about recognition accuracy.
# =============================================================================================================

def _real_engine():
    try:
        return FasterWhisperEngine()
    except SttModelNotFound:
        pytest.skip("models/faster-whisper-tiny.en-0d3d19a3 is not present (one-time acquisition not done on this machine)")
    except SttRuntimeUnavailable as exc:
        pytest.skip(f"faster-whisper cannot be imported on this machine: {exc}")


@pytest.fixture(scope="module")
def real():
    return _real_engine()


def test_real_snapshot_verifies_against_the_manifest():
    d = resolve_model_dir()
    if not d.is_dir():
        pytest.skip("approved snapshot not present")
    assert verify_model_snapshot(d) == 78090594


def test_real_engine_is_verified_and_english_only(real):
    assert real.snapshot_verified is True and real.model_id == FASTER_WHISPER_TINY_EN.model_id
    assert real._model.model.is_multilingual is False and real._model.feature_extractor.sampling_rate == 16000
    assert real.load_ms is not None and math.isfinite(real.load_ms)


def test_real_engine_executes_on_synthetic_input_and_repeats(real):
    zeros = np.zeros(RATE, dtype=np.int16)
    before = zeros.copy()
    results = [real.transcribe(zeros, RATE) for _ in range(3)]
    for r in results:
        assert r.status in (SttStatus.OK, SttStatus.EMPTY_TEXT)      # consumable structure; NOT an accuracy claim
        assert math.isfinite(r.inference_ms) and r.inference_ms > 0 and r.audio_duration_s == 1.0
        assert r.engine_id == "faster-whisper" and r.model_id == FASTER_WHISPER_TINY_EN.model_id
    assert np.array_equal(zeros, before)


def test_real_engine_enforces_its_development_length_limit(real):
    with pytest.raises(SttInputError):
        real.transcribe(np.zeros(31 * RATE, dtype=np.int16), RATE)


def test_real_engine_construction_and_inference_need_no_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    offline = _real_engine()
    r = offline.transcribe(np.zeros(RATE // 2, dtype=np.int16), RATE)
    assert r.status in (SttStatus.OK, SttStatus.EMPTY_TEXT)
