"""Tests for audio/vad.py: the engine-independent VadEngine contract.

SOFTWARE / SYNTHETIC CONTRACT TESTS. They use the scripted test double and small hand-made engines;
no VAD model is involved and nothing here measures VAD accuracy.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from audio.vad import VadContractError, VadEngine, VadFrameError, VadScoreError, check_score
from tests.voice_synthetic import FRAME, SR, ScriptedVadEngine, ScriptedVadExhausted, index_pcm


class _FixedScoreEngine(VadEngine):
    """Returns whatever it is told to, so score validation can be tested with bad values."""

    def __init__(self, score):
        self.score = score
        self.resets = 0

    sample_rate = SR
    frame_samples = FRAME

    def _score_frame(self, frame):
        return self.score

    def reset(self):
        self.resets += 1


def _frame(n=FRAME, dtype=np.int16):
    return np.arange(n).astype(dtype)


# 13 -----------------------------------------------------------------------------------

def test_correct_frame_is_accepted_and_scored():
    engine = ScriptedVadEngine([0.25])
    assert engine.process_frame(index_pcm(0, FRAME)) == 0.25


def test_base_class_is_abstract():
    with pytest.raises(TypeError):
        VadEngine()


# 14 -----------------------------------------------------------------------------------

@pytest.mark.parametrize("dtype", [np.float32, np.float64, np.int32, np.uint8, np.int64])
def test_wrong_dtype_rejected(dtype):
    engine = ScriptedVadEngine([0.5])
    with pytest.raises(VadFrameError):
        engine.process_frame(_frame(dtype=dtype))
    assert engine.position == 0  # rejected before scoring


# 15 -----------------------------------------------------------------------------------

@pytest.mark.parametrize("n", [0, FRAME - 1, FRAME + 1, FRAME * 2])
def test_wrong_length_rejected(n):
    engine = ScriptedVadEngine([0.5])
    with pytest.raises(VadFrameError):
        engine.process_frame(_frame(n))


# 16 -----------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    np.zeros((FRAME, 1), dtype=np.int16),
    np.zeros((1, FRAME), dtype=np.int16),
    np.zeros((2, FRAME // 2), dtype=np.int16),
    list(range(FRAME)),
    bytes(2 * FRAME),
    None,
])
def test_malformed_frame_rejected(bad):
    engine = ScriptedVadEngine([0.5])
    with pytest.raises(VadFrameError):
        engine.process_frame(bad)


def test_frame_errors_are_contract_errors():
    assert issubclass(VadFrameError, VadContractError)
    assert issubclass(VadScoreError, VadContractError)
    assert issubclass(VadContractError, ValueError)


# 17, 18 -------------------------------------------------------------------------------

def test_valid_zero_score():
    assert ScriptedVadEngine([0.0]).process_frame(_frame()) == 0.0


def test_valid_one_score():
    assert ScriptedVadEngine([1.0]).process_frame(_frame()) == 1.0


def test_numpy_and_int_scores_are_normalised_to_float():
    assert isinstance(_FixedScoreEngine(np.float32(0.5)).process_frame(_frame()), float)
    out = _FixedScoreEngine(1).process_frame(_frame())
    assert out == 1.0 and isinstance(out, float)


# 19, 20 -------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [-0.0001, -1.0, -1e9])
def test_below_zero_rejected(bad):
    with pytest.raises(VadScoreError):
        _FixedScoreEngine(bad).process_frame(_frame())


@pytest.mark.parametrize("bad", [1.0001, 2.0, 1e9])
def test_above_one_rejected(bad):
    with pytest.raises(VadScoreError):
        _FixedScoreEngine(bad).process_frame(_frame())


# 21, 22 -------------------------------------------------------------------------------

def test_nan_rejected():
    with pytest.raises(VadScoreError):
        _FixedScoreEngine(float("nan")).process_frame(_frame())


@pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
def test_inf_rejected(bad):
    with pytest.raises(VadScoreError):
        _FixedScoreEngine(bad).process_frame(_frame())


@pytest.mark.parametrize("bad", [None, "0.5", [0.5], True, False, complex(0.5, 0)])
def test_non_numbers_rejected(bad):
    with pytest.raises(VadScoreError):
        _FixedScoreEngine(bad).process_frame(_frame())


def test_check_score_helper_directly():
    assert check_score(0.5) == 0.5
    with pytest.raises(VadScoreError):
        check_score(float("nan"))


# 23 -----------------------------------------------------------------------------------

def test_reset_rewinds_the_scripted_engine_and_is_counted():
    engine = ScriptedVadEngine([0.1, 0.2, 0.3])
    assert [engine.process_frame(_frame()) for _ in range(2)] == [0.1, 0.2]
    engine.reset()
    assert engine.reset_calls == 1
    assert engine.process_frame(_frame()) == 0.1  # utterance state cleared


def test_scripted_exhaustion_is_loud_and_deterministic():
    engine = ScriptedVadEngine([0.5])
    engine.process_frame(_frame())
    with pytest.raises(ScriptedVadExhausted):
        engine.process_frame(_frame())
    engine.reset()
    assert engine.process_frame(_frame()) == 0.5


def test_engine_never_modifies_its_input_frame():
    engine = ScriptedVadEngine([0.5])
    frame = index_pcm(5, FRAME)
    before = frame.copy()
    engine.process_frame(frame)
    assert np.array_equal(frame, before)
    assert np.array_equal(engine.seen_frames[0], before)
    assert engine.seen_frames[0] is not frame


def test_contract_module_imports_no_engine_library():
    """audio.vad and audio.utterance must stay engine-free (checked in a fresh interpreter)."""
    code = (
        "import sys\n"
        "import audio.vad, audio.utterance\n"
        "bad = [m for m in ('onnxruntime', 'torch', 'openwakeword', 'faster_whisper', 'ctranslate2', 'silero_vad') "
        "if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=str(Path(__file__).resolve().parent.parent))
    assert proc.returncode == 0, proc.stderr
