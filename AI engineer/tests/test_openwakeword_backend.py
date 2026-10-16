"""Tests for the openWakeWord backend (wakeword/openwakeword_backend.py).

Two tiers:

  Tier 1 - ALWAYS RUN, no openWakeWord needed. The adapter is exercised with
  a fake Model and (for the loading logic) a fake `openwakeword` package.
  These prove the backend's own logic. They do NOT prove that real
  openWakeWord inference works.

  Tier 2 - REAL ENGINE. Skipped automatically when openWakeWord cannot be
  imported (e.g. the Windows Application Control block) or when its model
  files have not been downloaded. Only these tests validate real inference.
"""

import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from audio.file_source import WavFileSource
from wakeword.detector import WakeWordDetector, run_on_source
from wakeword.openwakeword_backend import (
    ModelFilesMissing,
    OpenWakeWordDetector,
    OpenWakeWordUnavailable,
    WakeWordBackendError,
    main,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeModel:
    """Stands in for openwakeword.model.Model: scripted scores by call index."""

    def __init__(self, script=None, name="hey_jarvis"):
        self.script = script or {}
        self.name = name
        self.calls = []
        self.resets = 0

    def predict(self, x):
        self.calls.append((x.dtype, x.shape))
        return {self.name: np.float32(self.script.get(len(self.calls) - 1, 0.0))}

    def reset(self):
        self.resets += 1


def _silence_wav(write_wav, seconds=4):
    return write_wav(np.zeros(16000 * seconds, dtype=np.int16))


# ===========================================================================
# Tier 1: backend logic, no openWakeWord required
# ===========================================================================

def test_importing_the_backend_module_does_not_import_openwakeword():
    """Lazy import: the module must load on machines where openWakeWord can't."""
    code = (
        "import sys, wakeword.openwakeword_backend;"
        "sys.exit(1 if 'openwakeword' in sys.modules else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0


def test_backend_fits_the_detector_interface(monkeypatch):
    # openwakeword=None makes any import attempt fail: proves an injected
    # model never triggers one.
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    d = OpenWakeWordDetector(model=FakeModel())
    assert isinstance(d, WakeWordDetector)
    assert d.sample_rate == 16000
    assert d.chunk_samples == 1280
    assert d.model_names == ["hey_jarvis"]
    assert d.inference_framework == "onnx"
    assert d.warmup_s == pytest.approx(0.4)


def test_chunk_is_passed_to_the_model_as_1d_int16():
    model = FakeModel()
    d = OpenWakeWordDetector(model=model)
    d.process(np.zeros(1280, dtype=np.int16))
    assert model.calls == [(np.dtype(np.int16), (1280,))]


def test_scores_become_plain_python_floats_keyed_by_model_name():
    d = OpenWakeWordDetector(model=FakeModel({0: 0.25}))
    d.process(np.zeros(1280, dtype=np.int16))
    (name, score), = d.last_scores.items()
    assert name == "hey_jarvis"
    assert type(score) is float
    assert score == pytest.approx(0.25)


def test_detection_uses_base_class_threshold_and_cooldown(write_wav):
    # 4 s = 50 chunks of 80 ms. Scores cross 0.5 at chunks 30, 31 and 45.
    # Cooldown 2 s = 25 chunks: 31 is suppressed, 45 (15 chunks later) too.
    model = FakeModel({30: 0.9, 31: 0.95, 45: 0.8})
    d = OpenWakeWordDetector(model=model, threshold=0.5, cooldown_s=2.0)

    detections = run_on_source(WavFileSource(_silence_wav(write_wav)), d)

    assert [x.chunk_index for x in detections] == [30]
    assert detections[0].keyword == "hey_jarvis"
    assert detections[0].score == pytest.approx(0.9)
    assert detections[0].stream_time_s == pytest.approx(31 * 1280 / 16000)
    assert d.suppressed_detections == 2
    assert len(model.calls) == 50


def test_below_threshold_scores_do_not_fire(write_wav):
    d = OpenWakeWordDetector(model=FakeModel({30: 0.49}), threshold=0.5)
    assert run_on_source(WavFileSource(_silence_wav(write_wav)), d) == []


def test_peak_score_tracking_and_reset():
    model = FakeModel({1: 0.4, 3: 0.8, 4: 0.6})
    d = OpenWakeWordDetector(model=model, threshold=0.99)
    for _ in range(5):
        d.process(np.zeros(1280, dtype=np.int16))
    assert d.peak_score == pytest.approx(0.8)
    assert d.peak_time_s == pytest.approx(4 * 1280 / 16000)  # end of chunk index 3

    d.reset()
    assert model.resets == 1
    assert d.peak_score == 0.0
    assert d.samples_processed == 0


def test_run_on_source_resets_the_model_before_each_run(write_wav):
    model = FakeModel()
    d = OpenWakeWordDetector(model=model)
    path = _silence_wav(write_wav, seconds=1)
    run_on_source(WavFileSource(path), d)
    run_on_source(WavFileSource(path), d)
    assert model.resets == 2  # engine history never leaks from one clip into the next


def test_invalid_arguments_rejected():
    with pytest.raises(ValueError):
        OpenWakeWordDetector(model=FakeModel(), inference_framework="pytorch")
    with pytest.raises(ValueError):
        OpenWakeWordDetector(model_names=[], model=FakeModel())


# -- loading logic against a FAKE openwakeword package -----------------------

def _install_fake_openwakeword(monkeypatch, tmp_path, *, files_present):
    """Register a stand-in `openwakeword` exposing only what the backend reads
    (MODELS, FEATURE_MODELS, model.Model). Returns the kwargs Model received."""
    models_dir = tmp_path / "resources" / "models"
    models_dir.mkdir(parents=True)

    pkg = types.ModuleType("openwakeword")
    pkg.FEATURE_MODELS = {
        "embedding": {"model_path": str(models_dir / "embedding_model.tflite")},
        "melspectrogram": {"model_path": str(models_dir / "melspectrogram.tflite")},
    }
    pkg.MODELS = {"hey_jarvis": {"model_path": str(models_dir / "hey_jarvis_v0.1.tflite")}}

    received = {}

    class Model(FakeModel):
        def __init__(self, **kwargs):
            super().__init__()
            received.update(kwargs)

    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = Model
    pkg.model = model_module

    if files_present:
        for name in ("embedding_model.onnx", "melspectrogram.onnx", "hey_jarvis_v0.1.onnx"):
            (models_dir / name).write_bytes(b"")

    monkeypatch.setitem(sys.modules, "openwakeword", pkg)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)
    return received


def test_missing_model_files_give_an_actionable_error(monkeypatch, tmp_path):
    _install_fake_openwakeword(monkeypatch, tmp_path, files_present=False)
    with pytest.raises(ModelFilesMissing) as info:
        OpenWakeWordDetector()
    message = str(info.value)
    assert "hey_jarvis_v0.1.onnx" in message
    assert "download_models" in message
    assert isinstance(info.value, WakeWordBackendError)


def test_model_is_constructed_with_the_documented_arguments(monkeypatch, tmp_path):
    received = _install_fake_openwakeword(monkeypatch, tmp_path, files_present=True)
    d = OpenWakeWordDetector(inference_framework="onnx")
    assert received == {"wakeword_models": ["hey_jarvis"], "inference_framework": "onnx"}
    assert d.model_names == ["hey_jarvis"]


def test_unknown_pretrained_model_name_rejected(monkeypatch, tmp_path):
    _install_fake_openwakeword(monkeypatch, tmp_path, files_present=True)
    with pytest.raises(ValueError, match="unknown pre-trained model"):
        OpenWakeWordDetector(model_names=["definitely_not_a_model"])


def test_unavailable_openwakeword_gives_a_clear_error(monkeypatch):
    """Simulates the Windows DLL block (or a missing install) via ImportError."""
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    with pytest.raises(OpenWakeWordUnavailable) as info:
        OpenWakeWordDetector()
    assert isinstance(info.value.__cause__, ImportError)
    assert "pip install openwakeword" in str(info.value)


# -- command-line entry point ---------------------------------------------

def test_cli_reports_score_and_detection(write_wav, capsys):
    detector = OpenWakeWordDetector(model=FakeModel({30: 0.9}))
    code = main([str(_silence_wav(write_wav))], detector=detector)
    out = capsys.readouterr().out

    assert code == 0
    assert "DETECTION keyword=hey_jarvis score=0.900 stream_time=2.480s chunk=30" in out
    assert "peak_score=0.900 at 2.480s" in out
    assert "RESULT: 1 detection(s)" in out
    assert "warm-up" in out


def test_cli_with_no_detection(write_wav, capsys):
    code = main([str(_silence_wav(write_wav))], detector=OpenWakeWordDetector(model=FakeModel()))
    out = capsys.readouterr().out
    assert code == 0
    assert "RESULT: 0 detection(s)" in out


def test_cli_exits_2_when_openwakeword_unavailable(write_wav, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    assert main([str(_silence_wav(write_wav))]) == 2
    assert "could not be imported" in capsys.readouterr().err


def test_cli_exits_2_for_unsupported_or_missing_wav(write_wav, tmp_path, capsys):
    detector = OpenWakeWordDetector(model=FakeModel())
    assert main([str(write_wav(np.zeros(800, dtype=np.int16), rate=8000))], detector=detector) == 2
    assert main([str(tmp_path / "missing.wav")], detector=detector) == 2
    assert "ERROR" in capsys.readouterr().err


# ===========================================================================
# Tier 2: REAL openWakeWord. Skipped when it cannot be imported / no models.
# ===========================================================================

@pytest.fixture
def real_detector():
    # exc_type=ImportError: on Windows the failure is a blocked DLL, which
    # raises ImportError rather than ModuleNotFoundError.
    pytest.importorskip(
        "openwakeword", exc_type=ImportError,
        reason="openWakeWord cannot be imported on this machine",
    )
    try:
        return OpenWakeWordDetector(threshold=0.5, cooldown_s=2.0)
    except ModelFilesMissing as exc:
        pytest.skip(str(exc))


def test_real_engine_contract(real_detector):
    assert isinstance(real_detector, WakeWordDetector)
    assert (real_detector.sample_rate, real_detector.chunk_samples) == (16000, 1280)


def test_real_engine_ignores_silence(real_detector, write_wav):
    detections = run_on_source(
        WavFileSource(write_wav(np.zeros(16000 * 5, dtype=np.int16))), real_detector
    )
    assert detections == []
    assert set(real_detector.last_scores) == {"hey_jarvis"}
    assert 0.0 <= real_detector.peak_score < real_detector.threshold


def test_real_engine_ignores_low_level_noise(real_detector, write_wav):
    noise = np.random.default_rng(0).integers(-500, 500, size=16000 * 5, dtype=np.int16)
    detections = run_on_source(WavFileSource(write_wav(noise)), real_detector)
    assert detections == []


def test_real_engine_detects_hey_jarvis_in_a_recorded_clip(real_detector):
    """Needs a real recording: set VESTA_HEY_JARVIS_WAV to a 16 kHz mono 16-bit
    WAV in which someone says "hey jarvis" at least ~0.5 s after the start
    (the engine cannot fire during its 0.4 s warm-up)."""
    clip = os.environ.get("VESTA_HEY_JARVIS_WAV")
    if not clip:
        pytest.skip("set VESTA_HEY_JARVIS_WAV to a real 'hey jarvis' recording to run this test")

    detections = run_on_source(WavFileSource(clip), real_detector)

    assert detections, f"no detection; peak score was {real_detector.peak_score:.3f}"
    assert detections[0].keyword == "hey_jarvis"
    print(f"\nscore={detections[0].score:.3f} at {detections[0].stream_time_s:.3f}s "
          f"(peak {real_detector.peak_score:.3f})")
