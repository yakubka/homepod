"""Team Leader AI-2 software dependency: the engine-neutral SttEngine / SttResult contract (speech/stt.py).

SOFTWARE-VALIDATED with a scripted fake engine. Nothing here runs a real STT model, and nothing says
anything about recognition accuracy.
"""

import dataclasses
import logging
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from speech import stt
from speech.stt import (
    SttContractError, SttEngine, SttError, SttInferenceFailed, SttInputError, SttModelLoadFailed, SttResult, SttStatus,
    normalize_text,
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


class FakeEngine(SttEngine):
    """Scripted backend: returns `reply`, or raises `error`; `action(samples)` runs inside the timed call."""

    def __init__(self, reply="hello world", error=None, action=None, max_s=None, clock=None):
        super().__init__(clock=clock) if clock else super().__init__()
        self.reply, self.error, self.action, self.max_s = reply, error, action, max_s
        self.seen = []

    sample_rate = RATE
    engine_id = "fake-engine"
    model_id = "fake-model"

    def _check_extra_input(self, samples):
        if self.max_s is not None and samples.size / RATE > self.max_s:
            raise SttInputError("too long for this fake backend")

    def _transcribe(self, samples):
        self.seen.append(samples)
        if self.action:
            self.action(samples)
        if self.error:
            raise self.error
        return self.reply


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
    stt.logger.addHandler(handler)
    yield handler.lines
    stt.logger.removeHandler(handler)


# -- valid input and the OK / EMPTY_TEXT results ----------------------------------------------------

def test_valid_input_gives_an_ok_result_with_identity_and_duration():
    r = FakeEngine().transcribe(pcm(8000), RATE)
    assert r.status is SttStatus.OK and r.text == "hello world"
    assert (r.engine_id, r.model_id) == ("fake-engine", "fake-model")
    assert r.audio_duration_s == 0.5
    assert math.isfinite(r.inference_ms) and r.inference_ms >= 0


@pytest.mark.parametrize("reply", ["", "   ", "\n\t \r\n"])
def test_blank_text_is_empty_text(reply):
    r = FakeEngine(reply=reply).transcribe(pcm(), RATE)
    assert r.status is SttStatus.EMPTY_TEXT and r.text == ""


def test_whitespace_normalisation_is_narrow():
    assert normalize_text("  Hello \n  world\t!  ") == "Hello world !"
    assert normalize_text("Hey Jarvis, turn on THE lights.") == "Hey Jarvis, turn on THE lights."   # no case/wake/semantic change
    assert FakeEngine(reply="  What   is the\ntemperature? ").transcribe(pcm(), RATE).text == "What is the temperature?"


def test_numpy_integer_sample_rate_is_accepted():
    assert FakeEngine().transcribe(pcm(), np.int64(RATE)).status is SttStatus.OK


# -- input contract -----------------------------------------------------------------------------------

@pytest.mark.parametrize("rate", [8000, 16001, 44100, True, 16000.0, "16000", None])
def test_wrong_sample_rate_is_rejected_without_resampling(rate):
    engine = FakeEngine()
    with pytest.raises(SttInputError):
        engine.transcribe(pcm(), rate)
    assert engine.seen == []               # the backend never ran


@pytest.mark.parametrize("dtype", [np.float32, np.float64, np.int32, np.uint8, np.uint16, np.int8])
def test_wrong_dtype_is_rejected(dtype):
    with pytest.raises(SttInputError):
        FakeEngine().transcribe(np.ones(1600, dtype=dtype), RATE)


@pytest.mark.parametrize("shape", [(1600, 1), (2, 800), (1, 1600), (4, 2, 200)])
def test_non_1d_input_is_rejected(shape):
    with pytest.raises(SttInputError):
        FakeEngine().transcribe(np.zeros(shape, dtype=np.int16), RATE)


def test_zero_dimensional_and_non_array_input_is_rejected():
    for bad in (np.int16(5), np.array(5, dtype=np.int16), [1, 2, 3], b"\x00\x00", "abc", None):
        with pytest.raises(SttInputError):
            FakeEngine().transcribe(bad, RATE)


def test_empty_input_is_rejected():
    with pytest.raises(SttInputError):
        FakeEngine().transcribe(np.empty(0, dtype=np.int16), RATE)


def test_backend_specific_over_limit_input_is_rejected_by_the_hook_only():
    engine = FakeEngine(max_s=1.0)
    assert engine.transcribe(pcm(RATE), RATE).status is SttStatus.OK             # exactly at the limit passes
    with pytest.raises(SttInputError):
        engine.transcribe(pcm(RATE + 1), RATE)
    assert FakeEngine().transcribe(pcm(RATE * 120), RATE).status is SttStatus.OK  # the generic contract has no length limit


def test_input_is_not_mutated_and_the_backend_gets_a_read_only_view():
    samples = np.arange(1600, dtype=np.int16)
    before = samples.copy()
    engine = FakeEngine()
    engine.transcribe(samples, RATE)
    assert np.array_equal(samples, before) and samples.flags.writeable
    seen = engine.seen[0]
    assert np.array_equal(seen, before) and not seen.flags.writeable
    assert seen.base is samples or np.shares_memory(seen, samples)              # a view, not a hidden copy


def test_a_backend_that_tries_to_write_the_input_fails_and_the_caller_array_survives():
    samples = np.arange(1600, dtype=np.int16)
    before = samples.copy()

    def scribble(view):
        view[0] = 7

    with pytest.raises(SttInferenceFailed):
        FakeEngine(action=scribble).transcribe(samples, RATE)
    assert np.array_equal(samples, before)


def test_transcribe_segment_passes_samples_and_rate():
    class Seg:
        samples, sample_rate = pcm(3200), RATE

    r = FakeEngine().transcribe_segment(Seg())
    assert r.audio_duration_s == 0.2


# -- failure channel: a failure is never an empty transcript --------------------------------------------

def test_backend_exception_becomes_inference_failed_with_cause_and_never_a_result():
    boom = RuntimeError("decoder exploded")
    with pytest.raises(SttInferenceFailed) as info:
        FakeEngine(error=boom).transcribe(pcm(), RATE)
    assert info.value.__cause__ is boom


def test_typed_backend_errors_pass_through_unchanged():
    for exc in (SttModelLoadFailed("x"), SttContractError("y")):
        with pytest.raises(type(exc)) as info:
            FakeEngine(error=exc).transcribe(pcm(), RATE)
        assert info.value is exc


def test_keyboard_interrupt_is_not_swallowed():
    with pytest.raises(KeyboardInterrupt):
        FakeEngine(error=KeyboardInterrupt()).transcribe(pcm(), RATE)


@pytest.mark.parametrize("reply", [None, b"text", 42, ["a"]])
def test_non_string_backend_output_is_a_contract_error(reply):
    with pytest.raises(SttContractError):
        FakeEngine(reply=reply).transcribe(pcm(), RATE)


def test_every_error_is_an_stt_error_and_input_errors_are_value_errors():
    for cls in (stt.SttRuntimeUnavailable, stt.SttModelNotFound, stt.SttModelLoadFailed, stt.SttInputError,
                stt.SttInferenceFailed, stt.SttContractError):
        assert issubclass(cls, SttError)
    assert issubclass(SttInputError, ValueError)


# -- timing ----------------------------------------------------------------------------------------------

def test_inference_ms_covers_exactly_the_backend_call():
    clock = FakeClock()

    def spend(_):
        clock.now += 0.25

    engine = FakeEngine(action=spend, clock=clock)
    clock.now = 10.0
    assert engine.transcribe(pcm(), RATE).inference_ms == pytest.approx(250.0)


def test_validation_time_is_not_counted_in_inference_ms():
    clock = FakeClock()
    engine = FakeEngine(clock=clock)
    assert engine.transcribe(pcm(), RATE).inference_ms == 0.0


# -- result type -----------------------------------------------------------------------------------------

def _result(**kw):
    base = dict(status=SttStatus.OK, text="hi", engine_id="e", model_id="m", audio_duration_s=1.0, inference_ms=5.0)
    base.update(kw)
    return SttResult(**base)


def test_result_is_frozen():
    r = _result()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.text = "changed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = SttStatus.EMPTY_TEXT


def test_result_has_exactly_the_minimal_fields_and_two_statuses():
    assert [f.name for f in dataclasses.fields(SttResult)] == [
        "status", "text", "engine_id", "model_id", "audio_duration_s", "inference_ms"]
    assert {s.name for s in SttStatus} == {"OK", "EMPTY_TEXT"}      # no FAILED, no TIMEOUT


@pytest.mark.parametrize("kw", [
    dict(status=SttStatus.OK, text=""),                     # OK needs text
    dict(status=SttStatus.EMPTY_TEXT, text="hi"),           # EMPTY_TEXT needs ""
    dict(status="OK"),                                      # not an enum
    dict(text=None),
    dict(text=" padded "),                                  # not normalised
    dict(text="two  spaces"),
    dict(engine_id=""), dict(model_id=""), dict(engine_id=None),
    dict(audio_duration_s=0.0), dict(audio_duration_s=-1.0), dict(audio_duration_s=float("nan")),
    dict(audio_duration_s=float("inf")), dict(audio_duration_s=True),
    dict(inference_ms=-0.1), dict(inference_ms=float("nan")), dict(inference_ms=float("inf")), dict(inference_ms=False),
])
def test_result_invariants_are_enforced(kw):
    with pytest.raises(SttContractError):
        _result(**kw)


def test_valid_empty_result_constructs():
    assert _result(status=SttStatus.EMPTY_TEXT, text="").status is SttStatus.EMPTY_TEXT


# -- privacy -----------------------------------------------------------------------------------------------

def test_transcript_text_never_appears_in_default_logs(logs):
    secret = "my secret transcript about the exam"
    FakeEngine(reply=secret).transcribe(pcm(), RATE)
    with pytest.raises(SttInferenceFailed):
        FakeEngine(error=RuntimeError(f"cannot decode {secret}")).transcribe(pcm(), RATE)
    joined = "\n".join(logs)
    assert "event=stt_done" in joined and "event=stt_failed" in joined
    assert "secret" not in joined and "exam" not in joined


def test_ok_log_line_carries_only_structural_fields(logs):
    FakeEngine(reply="abcde").transcribe(pcm(8000), RATE)
    line = next(l for l in logs if "event=stt_done" in l)
    for field in ("status=OK", "engine=fake-engine", "model=fake-model", "audio_duration_s=0.500", "inference_ms=", "text_chars=5"):
        assert field in line
    assert "abcde" not in line


# -- imports ---------------------------------------------------------------------------------------------

def test_contract_module_imports_no_engine_library():
    code = (
        "import sys\n"
        "import speech, speech.stt\n"
        "bad = [m for m in ('faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'huggingface_hub', 'tokenizers') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
