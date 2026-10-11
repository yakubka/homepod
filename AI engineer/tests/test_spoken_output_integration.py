"""Team Leader spoken-output foundation: composition of answer text -> TTS -> SpeechAudio -> playback -> PlaybackResult.

This proves that the components COMPOSE. It is SOFTWARE-VALIDATED / SIMULATED: fake TTS, fake playback or a fake sounddevice.
The one real-voice test needs no playback. Nothing here proves a voice quality, a speaker, audibility, the Pi, or that AI-4,
AI-6 or AI-9 are done. DeterministicAnswerEngine is used unchanged and knows nothing about speech.
"""

import socket

import numpy as np
import pytest

from assistant.answering import (
    AnswerContext, AnswerEngine, AnswerRequest, AnswerStatus, Capability, CapabilityDeclaration, DeterministicAnswerEngine,
)
from audio.playback import PlaybackEngine, PlaybackResult, PlaybackStatus, SoundDevicePlaybackEngine
from speech.tts import SpeechAudio, TtsEngine
from tests import fusion_synthetic as fs
from tests.spoken_output_fakes import FakePlayback, FakeSd, FakeTts

ROOM = frozenset({Capability.PEOPLE_COUNT, Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS})
FALLBACK_SENTENCE = "Sorry, I can't answer that right now."
ANNOUNCEMENT = "The professor has not arrived yet. Please wait for further instructions."


def answer_context():
    state = fs.fuse(fs.inputs(v=fs.vis(17), p=fs.pres(True), l=fs.lux(320.0), temps=[fs.temp("temp_1", 22.5)], h=fs.hum(45.0)))
    return AnswerContext(classroom_state=state, capabilities=CapabilityDeclaration(ROOM))


def speak(text, tts: TtsEngine, playback: PlaybackEngine):
    """The composition under test: exactly the three contracts, nothing else."""
    audio = tts.synthesize(text)
    return audio, playback.play(audio)


def test_answer_text_flows_through_fake_tts_and_fake_playback_to_a_playback_result():
    answer = DeterministicAnswerEngine().answer(AnswerRequest("How many people are in the room?", answer_context()))
    assert answer.status is AnswerStatus.ANSWERED and answer.text == "There are 17 people in the room."
    tts, playback = FakeTts(), FakePlayback()
    audio, result = speak(answer.text, tts, playback)
    assert isinstance(audio, SpeechAudio) and isinstance(result, PlaybackResult)
    assert playback.played == [audio] and tts.calls == 1
    assert result.status is PlaybackStatus.COMPLETED and result.audio_duration_s == audio.duration_s
    assert not hasattr(audio, "text") and not hasattr(audio, "intent")           # no answer semantics travel with the audio


def test_every_ai1_answer_text_is_speakable_by_the_same_path():
    engine, context = DeterministicAnswerEngine(), answer_context()
    from tests.test_answering import Q
    tts, playback = FakeTts(), FakePlayback()
    for qid, q in Q.items():
        answer = engine.answer(AnswerRequest(q["question"], context))
        audio, result = speak(answer.text, tts, playback)
        assert result.status is PlaybackStatus.COMPLETED, qid
    assert len(playback.played) == 10


def test_the_answer_engine_was_not_touched_and_has_no_speech_api():
    for name in ("synthesize", "play", "speak", "tts", "playback"):
        assert not hasattr(DeterministicAnswerEngine, name) and not hasattr(AnswerEngine, name)


def test_announcement_and_fallback_texts_use_the_same_tts_and_playback_stack():
    tts, playback = FakeTts(), FakePlayback()
    for text in (ANNOUNCEMENT, FALLBACK_SENTENCE):                                  # AI-6 / AI-9 preparation: arbitrary approved text
        audio, result = speak(text, tts, playback)
        assert result.status is PlaybackStatus.COMPLETED and audio.tts_engine_id == "fake-tts"
    assert tts.calls == 2 and len(playback.played) == 2                             # one TtsEngine, one PlaybackEngine, no second stack


def test_the_playback_result_exposes_the_software_proxy_for_the_start_of_the_spoken_answer():
    _audio, result = speak("There are 17 people in the room.", FakeTts(), FakePlayback())
    assert result.t_first_buffer_submitted is not None
    assert result.t_play_called <= result.t_stream_ready <= result.t_first_buffer_submitted <= result.t_finished
    # AI-4 SOFTWARE PROXY FOR SPOKEN START = t_first_buffer_submitted (not physical acoustic onset)


def test_the_real_playback_engine_class_composes_with_tts_over_a_fake_device():
    sd = FakeSd()
    playback = SoundDevicePlaybackEngine(sd_module=sd)
    audio, result = speak(ANNOUNCEMENT, FakeTts(), playback)
    stream = sd.streams[0]
    assert result.status is PlaybackStatus.COMPLETED and stream.samplerate == audio.sample_rate == 22050
    assert np.array_equal(stream.pcm()[:audio.samples.size], audio.samples)


def test_a_tts_failure_never_reaches_playback():
    from speech.tts import TtsInputError
    playback = FakePlayback()
    with pytest.raises(TtsInputError):
        speak("?!", FakeTts(), playback)
    assert playback.played == []


# ---- real offline TTS, no playback ----------------------------------------------------------------------------------------------------------

def _real_tts():
    from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
    from speech.tts import TtsModelNotFound, TtsRuntimeUnavailable
    try:
        return SherpaOnnxTtsEngine()
    except TtsModelNotFound:
        pytest.skip("approved voice package not present")
    except TtsRuntimeUnavailable as exc:
        pytest.skip(f"sherpa-onnx not importable: {exc}")


@pytest.fixture(scope="module")
def real_tts():
    return _real_tts()


def test_real_offline_tts_speaks_a_real_answer_text_without_any_playback(real_tts):
    answer = DeterministicAnswerEngine().answer(AnswerRequest("What is the temperature in the room?", answer_context()))
    audio = real_tts.synthesize(answer.text)
    assert audio.samples.dtype == np.int16 and audio.samples.any() and audio.sample_rate == 22050 and audio.duration_s > 0.5


def test_real_tts_audio_reaches_a_fake_device_unchanged(real_tts):
    sd = FakeSd(block=1024)
    audio, result = speak(FALLBACK_SENTENCE, real_tts, SoundDevicePlaybackEngine(sd_module=sd))
    assert result.status is PlaybackStatus.COMPLETED and sd.streams[0].samplerate == 22050
    assert np.array_equal(sd.streams[0].pcm()[:audio.samples.size], audio.samples)


def test_the_fixed_fallback_sentence_synthesizes_with_the_network_disabled(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    offline = _real_tts()
    audio = offline.synthesize(FALLBACK_SENTENCE)
    assert audio.samples.any() and audio.duration_s > 0.5
