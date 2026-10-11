"""Team Leader spoken-output foundation: the engine-neutral TTS contract (speech/tts.py).

SOFTWARE-VALIDATED with a fake engine. No real voice is used here and nothing says anything about voice quality.
"""

import dataclasses
import logging
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from speech import tts
from speech.tts import (
    SpeechAudio, TtsContractError, TtsError, TtsInputError, TtsModelHashMismatch, TtsModelLoadFailed, TtsModelNotFound,
    TtsRuntimeUnavailable, TtsSynthesisFailed, check_text,
)
from tests.spoken_output_fakes import FakeTts, speech_audio, tone

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    tts.logger.addHandler(handler)
    yield handler.lines
    tts.logger.removeHandler(handler)


# ---- text validation ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Hello", "17", "a", "There are 17 people in the room.", "  padded  ", "été", "x!"])
def test_speakable_text_is_accepted(text):
    check_text(text)
    assert isinstance(FakeTts().synthesize(text), SpeechAudio)


@pytest.mark.parametrize("text", ["", " ", "   \n\t ", "?", "...", "?!", "- - -", "—", "()", "’"])
def test_empty_whitespace_and_punctuation_only_text_is_refused(text):
    engine = FakeTts()
    with pytest.raises(TtsInputError) as info:
        engine.synthesize(text)
    assert engine.calls == 0                                  # the backend never ran
    expected = "empty or whitespace-only" if not text.strip() else "at least one letter or digit"
    assert expected in str(info.value)                        # each rule refuses on its own, with its own reason


@pytest.mark.parametrize("bad", [None, 5, 3.2, b"hello", ["hello"], {"text": "hello"}])
def test_non_string_text_is_refused(bad):
    with pytest.raises(TtsInputError):
        FakeTts().synthesize(bad)


# ---- SpeechAudio ------------------------------------------------------------------------------------------------------------

def test_speech_audio_is_mono_int16_owned_read_only_and_derives_its_duration():
    source = tone(4410)
    a = speech_audio(samples=source, rate=22050)
    assert a.samples.dtype == np.int16 and a.samples.ndim == 1 and a.channels == 1
    assert a.duration_s == 4410 / 22050 == pytest.approx(0.2)
    assert not a.samples.flags.writeable and a.samples is not source and not np.shares_memory(a.samples, source)
    source[0] = 123                                           # later changes to the caller's array never reach the audio
    assert a.samples[0] != 123
    with pytest.raises(ValueError):
        a.samples[0] = 1


def test_speech_audio_is_frozen_and_has_exactly_the_agreed_fields_and_no_text():
    a = speech_audio()
    for name in ("samples", "sample_rate", "channels", "duration_s", "tts_engine_id", "voice_id", "synthesis_ms"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(a, name, 1)
    assert [f.name for f in dataclasses.fields(SpeechAudio)] == ["samples", "sample_rate", "channels", "duration_s", "tts_engine_id", "voice_id", "synthesis_ms"]
    assert not hasattr(a, "text") and not hasattr(a, "intent")


def test_duration_cannot_be_supplied():
    with pytest.raises(TypeError):
        SpeechAudio(samples=tone(), sample_rate=22050, channels=1, duration_s=99.0, tts_engine_id="e", voice_id="v", synthesis_ms=1.0)


@pytest.mark.parametrize("kw", [
    dict(samples=np.zeros(100, dtype=np.float32)), dict(samples=np.zeros(100, dtype=np.int32)), dict(samples=np.zeros(100, dtype=np.uint8)),
    dict(samples=np.zeros((100, 1), dtype=np.int16)), dict(samples=np.zeros((2, 50), dtype=np.int16)), dict(samples=np.empty(0, dtype=np.int16)),
    dict(samples=[1, 2, 3]), dict(samples=None), dict(sample_rate=0), dict(sample_rate=-22050), dict(sample_rate=True), dict(sample_rate=22050.0),
    dict(channels=2), dict(channels=0), dict(channels=True), dict(tts_engine_id=""), dict(voice_id="  "), dict(tts_engine_id=None),
    dict(synthesis_ms=-1.0), dict(synthesis_ms=float("nan")), dict(synthesis_ms=float("inf")), dict(synthesis_ms=True),
])
def test_invalid_speech_audio_is_refused(kw):
    with pytest.raises(TtsContractError):
        speech_audio(**kw)


# ---- the engine ------------------------------------------------------------------------------------------------------------------

def test_synthesize_returns_audio_with_engine_voice_and_timing():
    clock = FakeClock()

    class Timed(FakeTts):
        def _synthesize(self, text):
            clock.now += 0.25
            return super()._synthesize(text)

    a = Timed(clock=clock).synthesize("Hello there")
    assert (a.tts_engine_id, a.voice_id, a.channels, a.sample_rate) == ("fake-tts", "fake-voice", 1, 22050)
    assert a.synthesis_ms == pytest.approx(250.0) and math.isfinite(a.synthesis_ms)
    assert a.duration_s > 0


def test_engine_properties_are_part_of_the_contract():
    e = FakeTts()
    assert (e.engine_id, e.voice_id, e.language, e.sample_rate) == ("fake-tts", "fake-voice", "en-US", 22050)


def test_backend_exceptions_become_typed_errors_never_audio():
    boom = RuntimeError("engine blew up")
    with pytest.raises(TtsSynthesisFailed) as info:
        FakeTts(error=boom).synthesize("hello")
    assert info.value.__cause__ is boom
    for exc in (TtsModelLoadFailed("x"), TtsContractError("y"), TtsRuntimeUnavailable("z")):
        with pytest.raises(type(exc)) as got:
            FakeTts(error=exc).synthesize("hello")
        assert got.value is exc
    with pytest.raises(KeyboardInterrupt):
        FakeTts(error=KeyboardInterrupt()).synthesize("hello")


@pytest.mark.parametrize("pcm", [
    np.zeros(1000, dtype=np.int16),                          # silence for speakable text
    np.empty(0, dtype=np.int16), np.ones(100, dtype=np.float32), np.ones((100, 1), dtype=np.int16), [1, 2, 3], None, b"abc",
])
def test_silent_empty_or_wrongly_typed_backend_output_is_a_contract_error(pcm):
    with pytest.raises(TtsContractError) as info:
        FakeTts(pcm=pcm).synthesize("hello")
    assert "must return non-empty 1-D int16 PCM" in str(info.value) or "silent audio" in str(info.value)    # refused by the engine itself


def test_error_hierarchy():
    for cls in (TtsRuntimeUnavailable, TtsModelNotFound, TtsModelLoadFailed, TtsModelHashMismatch, TtsInputError, TtsSynthesisFailed, TtsContractError):
        assert issubclass(cls, TtsError)
    assert issubclass(TtsModelHashMismatch, TtsModelLoadFailed) and issubclass(TtsInputError, ValueError)


def test_the_returned_audio_does_not_alias_the_backend_buffer():
    pcm = tone(3000)
    a = FakeTts(pcm=pcm).synthesize("hello")
    pcm[:] = 0
    assert a.samples.any()


# ---- privacy -------------------------------------------------------------------------------------------------------------------------

def test_text_and_audio_never_reach_the_logs(logs):
    secret = "my secret exam answer is forty two"
    FakeTts().synthesize(secret)
    with pytest.raises(TtsSynthesisFailed):
        FakeTts(error=RuntimeError(f"cannot say {secret}")).synthesize(secret)
    joined = "\n".join(logs)
    assert "event=tts_done" in joined and "event=tts_failed" in joined
    for fragment in ("secret", "exam", "forty", "1000"):
        assert fragment not in joined, fragment
    line = next(l for l in logs if "event=tts_done" in l)
    for field in (f"text_chars={len(secret)}", "engine=fake-tts", "voice=fake-voice", "audio_duration_s=", "synthesis_ms="):
        assert field in line


def test_the_contract_module_imports_no_engine_or_device_library():
    code = (
        "import sys\n"
        "import speech.tts\n"
        "bad = [m for m in ('sherpa_onnx', 'sounddevice', 'faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'huggingface_hub') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
