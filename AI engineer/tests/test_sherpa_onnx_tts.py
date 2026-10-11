"""Team Leader spoken-output foundation: the sherpa-onnx TTS backend (speech/sherpa_onnx_tts.py).

Two tiers, kept apart on purpose:
  * Tier 1 - fake OfflineTts objects, temporary voice directories and a recording fake `sherpa_onnx` module. SOFTWARE-VALIDATED.
    They prove how OUR adapter behaves (verification before loading, load once, policy values, conversion, failure channel,
    provenance, privacy) and need no voice.
  * Tier 2 - the real en_US-lessac-medium package in models/ (git-ignored; skipped when absent or sherpa-onnx is not importable).
    They show only that the approved voice verifies, loads and synthesizes offline. They say NOTHING about voice quality,
    naturalness, speaker output or the Raspberry Pi.
ACADEMIC / NON-COMMERCIAL DEVELOPMENT ASSET: the voice files are never committed.
"""

import ast
import copy
import dataclasses
import hashlib
import json
import logging
import math
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from speech import sherpa_onnx_tts as st
from speech.sherpa_onnx_tts import (
    MANIFEST_PATH, NOISE_SCALE, NOISE_SCALE_W, UNVERIFIED_VOICE_ID, SherpaOnnxTtsEngine, VoiceFile, VoiceManifest, load_manifest,
    resolve_voice_dir, verify_voice_directory,
)
from speech.tts import (
    SpeechAudio, TtsContractError, TtsModelHashMismatch, TtsModelLoadFailed, TtsModelNotFound, TtsRuntimeUnavailable, TtsSynthesisFailed,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RATE = 22050
FALLBACK_SENTENCE = "Sorry, I can't answer that right now."


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    st.logger.addHandler(handler)
    yield handler.lines
    st.logger.removeHandler(handler)


class FakeOfflineTts:
    """Scripted stand-in for sherpa_onnx.OfflineTts (TEST DOUBLE: never counts as a verified voice)."""

    def __init__(self, samples=(0.1, -0.1, 0.25, -0.25), rate=RATE, speakers=1, error=None):
        self.sample_rate, self.num_speakers = rate, speakers
        self.samples, self.error, self.calls, self._rate_out = list(samples), error, [], rate

    def generate(self, text, sid=0, speed=1.0, callback=None):
        self.calls.append((text, sid, speed))
        if self.error:
            raise self.error
        return SimpleNamespace(samples=self.samples, sample_rate=self._rate_out)


def make_voice(tmp_path):
    files = {"m.onnx": b"\x01\x02" * 50, "tokens.txt": b"a 1\nb 2\n", "espeak-ng-data/x.bin": b"data-x", "espeak-ng-data/sub/y.bin": b"data-y"}
    for rel, data in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    entries = tuple(VoiceFile(rel, len(d), hashlib.sha256(d).hexdigest()) for rel, d in sorted(files.items()))
    manifest = VoiceManifest(voice_id="test-voice", language="en-US", sample_rate=RATE, num_speakers=1, model_file="m.onnx",
                             tokens_file="tokens.txt", espeak_data_dir="espeak-ng-data", directory_name="test-voice-dir", files=entries,
                             total_size_bytes=sum(e.size_bytes for e in entries), release_tag="tts-models", asset_sha256="0" * 64,
                             licence_status="TEST")
    return tmp_path, manifest


def engine(**kw):
    kw.setdefault("manifest", make_voice(Path(kw.pop("_tmp")))[1] if "_tmp" in kw else load_manifest())
    return SherpaOnnxTtsEngine(tts=kw.pop("tts", FakeOfflineTts()), **kw)


# ---- the approved manifest --------------------------------------------------------------------------------------------------------

def test_the_shipped_manifest_describes_the_approved_voice():
    m = load_manifest()
    assert (m.voice_id, m.language, m.sample_rate, m.num_speakers) == ("en_US-lessac-medium", "en-US", 22050, 1)
    assert (m.model_file, m.tokens_file, m.espeak_data_dir) == ("en_US-lessac-medium.onnx", "tokens.txt", "espeak-ng-data")
    assert m.directory_name == "vits-piper-en_US-lessac-medium-9e3febfa"
    assert m.release_tag == "tts-models" and m.asset_sha256 == "9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e"
    assert len(m.files) == 359 and m.total_size_bytes == 81147006 == sum(f.size_bytes for f in m.files)
    assert all(len(f.sha256) == 64 and set(f.sha256) <= set("0123456789abcdef") for f in m.files)
    assert len({f.path for f in m.files}) == len(m.files) and sum(f.path.startswith("espeak-ng-data/") for f in m.files) == 355
    by_path = {f.path: f for f in m.files}
    assert by_path["en_US-lessac-medium.onnx"].sha256 == "4ba07d8549906668ee855fd9abf9faf66c5db74742712ff026a159f7277fca9f"
    assert by_path["tokens.txt"].sha256 == "87c8ef66eae5473ed0cc0366b3964c736ca6c5f676c979522ea31234e47430b9"


def test_the_manifest_records_source_revision_and_licence_restrictions():
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert raw["upstream_voice"]["revision"] == "c10ece1aade47bb51c153c893d14e5bf8e5b7117" and raw["upstream_voice"]["repo"] == "rhasspy/piper-voices"
    assert raw["source"]["packager"] == "k2-fsa/sherpa-onnx" and raw["source"]["asset_size_bytes"] == 67230653
    lic = raw["licence"]
    assert "NON-COMMERCIAL" in lic["status"].upper() and "not redistributed" in lic["status"]
    assert "non-commercial" in lic["voice_dataset"] and "Apache-2.0" in lic["sherpa_onnx_runtime"] and "GPL" in lic["espeak_ng_data"]
    assert raw["acquisition_date"] == "2026-10-11" and raw["file_count"] == len(raw["files"])


def _write_manifest(tmp_path, mutate):
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def test_malformed_or_missing_manifests_are_explicit_errors(tmp_path):
    with pytest.raises(TtsModelNotFound):
        load_manifest(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(TtsContractError):
        load_manifest(tmp_path / "bad.json")
    mutations = {
        "missing key": lambda r: r.pop("sample_rate"),
        "wrong total": lambda r: r.update(total_size_bytes=1),
        "duplicate path": lambda r: r["files"].append(dict(r["files"][0])),
        "empty files": lambda r: r.update(files=[], total_size_bytes=0),
        "traversal": lambda r: r["files"][-1].update(path="../escape.bin"),
        "absolute": lambda r: r["files"][-1].update(path="/abs.bin"),
        "short hash": lambda r: r["files"][-1].update(sha256="abc"),
        "model not listed": lambda r: r.update(model_file="other.onnx"),
        "no espeak data": lambda r: r.update(espeak_data_dir="nowhere"),
    }
    for label, mutate in mutations.items():
        with pytest.raises(TtsContractError):
            load_manifest(_write_manifest(tmp_path, mutate))
    for label in ("traversal", "absolute", "short hash"):                              # an entry that is not essential: only the path / hash check can refuse it
        with pytest.raises(TtsContractError, match="invalid hash or path"):
            load_manifest(_write_manifest(tmp_path, mutations[label]))


def test_default_voice_directory_is_under_models():
    m = load_manifest()
    assert resolve_voice_dir(m) == REPO_ROOT / "models" / m.directory_name
    assert resolve_voice_dir(m, "models/x") == REPO_ROOT / "models" / "x"
    assert resolve_voice_dir(m, str(REPO_ROOT)) == REPO_ROOT


# ---- verification ---------------------------------------------------------------------------------------------------------------------

def test_a_matching_voice_directory_verifies_and_extra_files_are_ignored(tmp_path):
    d, m = make_voice(tmp_path)
    (d / "README.txt").write_text("not part of the manifest")
    (d / ".cache").mkdir()
    assert verify_voice_directory(d, m) == m.total_size_bytes


def test_missing_directory_missing_files_size_and_hash_mismatches(tmp_path):
    d, m = make_voice(tmp_path)
    with pytest.raises(TtsModelNotFound, match="directory not found"):
        verify_voice_directory(tmp_path / "nope", m)
    (d / "tokens.txt").unlink()
    with pytest.raises(TtsModelNotFound, match="tokens.txt"):
        verify_voice_directory(d, m)
    d2, m2 = make_voice(tmp_path / "two")
    (d2 / "espeak-ng-data" / "x.bin").write_bytes(b"data-x!!")
    with pytest.raises(TtsModelHashMismatch, match="size"):
        verify_voice_directory(d2, m2)
    d3, m3 = make_voice(tmp_path / "three")
    (d3 / "m.onnx").write_bytes(b"\x02\x01" * 50)                                  # same size, different bytes
    with pytest.raises(TtsModelHashMismatch, match="SHA-256"):
        verify_voice_directory(d3, m3)
    d4, m4 = make_voice(tmp_path / "four")
    (d4 / "espeak-ng-data" / "sub" / "y.bin").write_bytes(b"data-Z")               # a single espeak data file is checked too
    with pytest.raises(TtsModelHashMismatch):
        verify_voice_directory(d4, m4)
    assert issubclass(TtsModelHashMismatch, TtsModelLoadFailed)


# ---- conversion and the synthesis call ----------------------------------------------------------------------------------------------------

def test_float_to_int16_conversion_is_exact_rounded_and_clipped():
    fake = FakeOfflineTts(samples=[0.0, 0.5, -0.5, 1.0, -1.0, 2.0, -2.0, 1e-6, 0.25])      # a plain Python list, as sherpa returns
    audio = engine(tts=fake).synthesize("hello 1")
    assert audio.samples.dtype == np.int16 and audio.samples.tolist() == [0, 16384, -16384, 32767, -32767, 32767, -32768, 0, 8192]


def test_generate_is_called_once_with_the_whole_text_speaker_zero_and_normal_speed():
    fake = FakeOfflineTts()
    e = engine(tts=fake)
    e.synthesize("First sentence. Second sentence!")
    assert fake.calls == [("First sentence. Second sentence!", 0, 1.0)]


def test_the_voice_is_used_as_loaded_and_never_reloaded_per_synthesis(tmp_path):
    d, m = make_voice(tmp_path)
    built = []

    def factory(model, tokens, data_dir):
        built.append((model, tokens, data_dir))
        return FakeOfflineTts()

    e = SherpaOnnxTtsEngine(d, manifest=m, tts_factory=factory)
    for text in ("one", "two", "three"):
        e.synthesize(text)
    assert len(built) == 1
    assert built[0] == (str(d / "m.onnx"), str(d / "tokens.txt"), str(d / "espeak-ng-data"))
    assert e.voice_verified is True and e.voice_id == "test-voice" and e.language == "en-US" and e.sample_rate == RATE
    assert e.verify_ms >= 0 and e.load_ms >= 0 and e.engine_id == "sherpa-onnx"


def test_the_audio_has_the_voice_native_rate_and_is_never_resampled():
    audio = engine().synthesize("hello")
    assert isinstance(audio, SpeechAudio) and audio.sample_rate == RATE and audio.channels == 1
    assert audio.duration_s == audio.samples.size / RATE


@pytest.mark.parametrize("bad", [
    dict(samples=[], rate=RATE), dict(samples=[float("nan"), 0.1], rate=RATE), dict(samples=[float("inf")], rate=RATE),
    dict(samples=[[0.1, 0.2]], rate=RATE), dict(samples=[0.1, 0.2], rate=16000), dict(samples=[0.0, 0.0, 0.0], rate=RATE),
])
def test_unusable_backend_output_is_a_contract_error(bad):
    fake = FakeOfflineTts(samples=bad["samples"])
    fake._rate_out = bad["rate"]
    with pytest.raises(TtsContractError):
        engine(tts=fake).synthesize("hello")


def test_backend_exception_is_synthesis_failed_never_audio():
    boom = RuntimeError("onnx runtime exploded")
    with pytest.raises(TtsSynthesisFailed) as info:
        engine(tts=FakeOfflineTts(error=boom)).synthesize("hello")
    assert info.value.__cause__ is boom


@pytest.mark.parametrize("kw", [dict(rate=16000), dict(rate=44100), dict(speakers=2), dict(speakers=0)])
def test_a_voice_that_breaks_the_manifest_contract_is_rejected(kw):
    with pytest.raises(TtsContractError):
        SherpaOnnxTtsEngine(tts=FakeOfflineTts(**kw), manifest=load_manifest())


def test_a_voice_without_the_expected_attributes_is_rejected():
    with pytest.raises(TtsContractError):
        SherpaOnnxTtsEngine(tts=SimpleNamespace(sample_rate=RATE), manifest=load_manifest())


# ---- verification before loading, provenance honesty ---------------------------------------------------------------------------------------------

def test_the_voice_is_verified_before_anything_is_loaded(tmp_path):
    d, m = make_voice(tmp_path)
    (d / "m.onnx").write_bytes(b"\x09" * 100)
    calls = []
    with pytest.raises(TtsModelHashMismatch):
        SherpaOnnxTtsEngine(d, manifest=m, tts_factory=lambda *a: calls.append(a) or FakeOfflineTts())
    with pytest.raises(TtsModelNotFound):
        SherpaOnnxTtsEngine(tmp_path / "missing", manifest=m, tts_factory=lambda *a: calls.append(a) or FakeOfflineTts())
    assert calls == []


def test_load_failure_and_runtime_unavailable_are_distinct_typed_errors(tmp_path):
    d, m = make_voice(tmp_path)
    boom = OSError("corrupt model")

    def failing(*a):
        raise boom

    with pytest.raises(TtsModelLoadFailed) as info:
        SherpaOnnxTtsEngine(d, manifest=m, tts_factory=failing)
    assert info.value.__cause__ is boom and not isinstance(info.value, TtsModelHashMismatch)

    def blocked(*a):
        raise TtsRuntimeUnavailable("blocked")

    with pytest.raises(TtsRuntimeUnavailable):
        SherpaOnnxTtsEngine(d, manifest=m, tts_factory=blocked)


def test_default_factory_reports_a_missing_or_blocked_runtime(tmp_path, monkeypatch):
    d, m = make_voice(tmp_path)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)                           # `import sherpa_onnx` now raises ImportError
    with pytest.raises(TtsRuntimeUnavailable):
        SherpaOnnxTtsEngine(d, manifest=m)


def test_default_factory_builds_the_reproducible_cpu_configuration(tmp_path, monkeypatch):
    d, m = make_voice(tmp_path)
    seen = {}

    def record(name):
        def make(**kw):
            seen[name] = kw
            return SimpleNamespace(kind=name, **kw)
        return make

    class FakeTts(FakeOfflineTts):
        def __init__(self, config):
            super().__init__()
            seen["config"] = config

    fake_module = SimpleNamespace(OfflineTtsVitsModelConfig=record("vits"), OfflineTtsModelConfig=record("model"),
                                  OfflineTtsConfig=lambda model: SimpleNamespace(model=model), OfflineTts=FakeTts)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)
    e = SherpaOnnxTtsEngine(d, manifest=m)
    assert seen["vits"] == {"model": str(d / "m.onnx"), "tokens": str(d / "tokens.txt"), "data_dir": str(d / "espeak-ng-data"),
                            "noise_scale": 0.0, "noise_scale_w": 0.0}
    assert seen["model"]["provider"] == "cpu" and seen["model"]["num_threads"] == 2 and seen["model"]["vits"].kind == "vits"
    assert e.voice_verified is True


def test_development_policy_constants():
    assert (NOISE_SCALE, NOISE_SCALE_W) == (0.0, 0.0)                 # DEVELOPMENT REPRODUCIBILITY POLICY, not a quality setting
    assert (st.PROVIDER, st.NUM_THREADS, st.SPEAKER_ID, st.SPEED) == ("cpu", 2, 0, 1.0)


def test_an_injected_voice_never_claims_to_be_verified(logs):
    e = engine()
    assert e.voice_verified is False and e.voice_id == UNVERIFIED_VOICE_ID and e.load_ms is None and e.verify_ms is None
    line = next(l for l in logs if "event=tts_engine_ready" in l)
    assert "voice_verified=false" in line and "injected_tts" in line
    for fragment in ("en_US-lessac-medium", "total_bytes", "files="):
        assert fragment not in line


def test_voice_dir_and_tts_cannot_both_be_given():
    with pytest.raises(ValueError):
        SherpaOnnxTtsEngine("x", tts=FakeOfflineTts(), manifest=load_manifest())


def test_a_verified_engine_logs_its_provenance(tmp_path, logs):
    d, m = make_voice(tmp_path)
    SherpaOnnxTtsEngine(d, manifest=m, tts_factory=lambda *a: FakeOfflineTts())
    line = next(l for l in logs if "event=tts_engine_ready" in l)
    for field in ("voice_verified=true", "voice=test-voice", "files=4", "noise_scale=0", "noise_scale_w=0", "provider=cpu"):
        assert field in line


# ---- privacy, network, imports ---------------------------------------------------------------------------------------------------------------------

def test_text_and_audio_values_never_reach_the_logs(logs):
    secret = "the secret exam answer is forty two"
    e = engine(tts=FakeOfflineTts(samples=[0.123456, -0.2]))
    e.synthesize(secret)
    with pytest.raises(TtsSynthesisFailed):
        engine(tts=FakeOfflineTts(error=RuntimeError(f"cannot say {secret}"))).synthesize(secret)
    joined = "\n".join(logs)
    assert "event=tts_done" in joined and "event=tts_failed" in joined
    for fragment in ("secret", "exam", "forty", "0.123456", "4045"):
        assert fragment not in joined, fragment


def test_backend_source_has_no_network_download_or_playback_imports():
    tree = ast.parse((REPO_ROOT / "speech" / "sherpa_onnx_tts.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"hashlib", "json", "time", "dataclasses", "pathlib", "typing", "numpy", "config", "speech", "sherpa_onnx"}
    called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not called & {"snapshot_download", "hf_hub_download", "urlopen", "requests", "socket", "OutputStream", "sounddevice", "play"}


def test_importing_the_backend_loads_no_runtime_and_no_device_library():
    code = (
        "import sys\n"
        "import speech.tts, speech.sherpa_onnx_tts as s\n"
        "bad = [m for m in ('sherpa_onnx', 'sounddevice', 'faster_whisper', 'ctranslate2', 'huggingface_hub') if m in sys.modules]\n"
        "assert not bad, ('after import', bad)\n"
        "from types import SimpleNamespace as N\n"
        "s.SherpaOnnxTtsEngine(tts=N(sample_rate=22050, num_speakers=1))\n"
        "bad = [m for m in ('sherpa_onnx', 'sounddevice') if m in sys.modules]\n"
        "assert not bad, ('after injected construction', bad)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr


# =============================================================================================================
# Tier 2 - the real approved voice (skipped when absent). Proves it verifies, loads and synthesizes offline.
# Says nothing about voice quality, naturalness, speakers or the Raspberry Pi.
# =============================================================================================================

SHORT = "There are 17 people in the room."
LONG = "I'm VESTA, a classroom assistant. I can tell you the number of people, the temperature, the humidity and the light level in the room."


def _real():
    try:
        return SherpaOnnxTtsEngine()
    except TtsModelNotFound:
        pytest.skip("approved voice package not present (one-time acquisition not done on this machine)")
    except TtsRuntimeUnavailable as exc:
        pytest.skip(f"sherpa-onnx cannot be imported on this machine: {exc}")


@pytest.fixture(scope="module")
def real():
    return _real()


def test_real_voice_package_verifies_against_the_manifest():
    m = load_manifest()
    d = resolve_voice_dir(m)
    if not d.is_dir():
        pytest.skip("approved voice package not present")
    assert verify_voice_directory(d, m) == 81147006


def test_real_engine_is_verified_and_reports_the_voice(real):
    assert real.voice_verified is True and real.voice_id == "en_US-lessac-medium" and real.sample_rate == 22050 and real.language == "en-US"
    assert real.verify_ms > 0 and real.load_ms > 0


def test_real_short_and_longer_sentences_give_mono_int16_audio_at_the_native_rate(real):
    short, long_ = real.synthesize(SHORT), real.synthesize(LONG)
    for a in (short, long_):
        assert a.samples.dtype == np.int16 and a.samples.ndim == 1 and a.channels == 1 and a.sample_rate == 22050
        assert a.samples.size > 0 and a.samples.any() and math.isfinite(a.duration_s) and 0.3 < a.duration_s < 30
        assert math.isfinite(a.synthesis_ms) and a.synthesis_ms > 0 and not a.samples.flags.writeable
        assert (a.tts_engine_id, a.voice_id) == ("sherpa-onnx", "en_US-lessac-medium")
    assert long_.duration_s > 2 * short.duration_s                       # the whole longer text was synthesized, not just a sentence


def test_real_synthesis_is_repeatable_under_the_deterministic_policy(real):
    first, second, third = (real.synthesize(SHORT).samples for _ in range(3))
    assert np.array_equal(first, second) and np.array_equal(second, third)


def test_real_engine_builds_and_synthesizes_with_the_network_disabled(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    offline = _real()                                                     # construction under a closed network
    audio = offline.synthesize(FALLBACK_SENTENCE)                         # AI-9 preparation: a fixed fallback sentence, fully offline
    assert audio.samples.any() and audio.duration_s > 0.3


def test_real_engine_refuses_unspeakable_text(real):
    from speech.tts import TtsInputError
    for bad in ("", "   ", "?!", None):
        with pytest.raises(TtsInputError):
            real.synthesize(bad)
