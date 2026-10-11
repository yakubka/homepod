"""Team Leader AI-6 (announcement) + AI-9 (offline fallback): the spoken-output orchestration (assistant/spoken_output.py).

SOFTWARE-VALIDATED with fake TTS / fake playback, plus one real-voice tier that needs no playback. Nothing here plays sound or
proves a speaker, an app/schedule trigger, audibility or that a message "plays clearly". COMPLETED means only that the playback
engine reported completion. AI-6 and AI-9 remain NOT COMPLETE (project speaker and trigger/failure-path validation pending).
"""

import ast
import dataclasses
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from assistant import spoken_output as so
from assistant.answering import (
    AnswerContext, AnswerRequest, Capability, CapabilityDeclaration, DeterministicAnswerEngine,
)
from assistant.spoken_output import (
    AnnouncementRequest, AnnouncementSource, FallbackReason, OutputStatus, RequestType, SpokenOutputContractError, SpokenOutputController,
    SpokenOutputRequestError, SpokenOutputResult, demo_announcement_request,
)
from audio.playback import (
    PlaybackBusy, PlaybackDeviceNotFound, PlaybackFailed, PlaybackFormatError, PlaybackOpenFailed, PlaybackResult, PlaybackStatus,
)
from speech.tts import (
    TtsContractError, TtsInputError, TtsModelLoadFailed, TtsRuntimeUnavailable, TtsSynthesisFailed,
)
from tests import fusion_synthetic as fs
from tests.spoken_output_fakes import FakePlayback, FakeTts

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET = "my secret exam answer is forty two"


class SpyTts(FakeTts):
    def __init__(self, delay=0.0, **kw):
        super().__init__(**kw)
        self.texts, self.delay = [], delay

    def _synthesize(self, text):
        self.texts.append(text)
        if self.delay:
            time.sleep(self.delay)
        return super()._synthesize(text)


class SpyPlayback(FakePlayback):
    """Records calls and concurrency; can fail, report STOPPED, or hold the 'speaker' until released."""

    def __init__(self, error=None, status=PlaybackStatus.COMPLETED, hold=False):
        super().__init__()
        self.error, self.status, self.hold = error, status, hold
        self.entered, self.release = threading.Event(), threading.Event()
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()
        self.stop_calls = 0

    def play(self, audio):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.played.append(audio)
        self._playing = True
        try:
            self.entered.set()
            if self.hold:
                assert self.release.wait(5)
            if self.error:
                raise self.error
            return PlaybackResult(self.status, audio.duration_s, 10.0, 10.01, 10.02, 10.5, "fake-device", "fake-api", 0.01, 0)
        finally:
            self._playing = False
            with self._guard:
                self.active -= 1

    def stop(self):
        self.stop_calls += 1
        self.release.set()
        return True


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    so.logger.addHandler(handler)
    yield handler.lines
    so.logger.removeHandler(handler)


def controller(tts=None, playback=None, **kw):
    tts, playback = tts or SpyTts(), playback or SpyPlayback()
    return SpokenOutputController(tts, playback, **kw), tts, playback


def announcement(**kw):
    base = dict(announcement_id="ann-1", text="Lunch is served in the main hall.", source=AnnouncementSource.APP, trigger_id="trig-7")
    base.update(kw)
    return AnnouncementRequest(**base)


# ---- request types are explicit ------------------------------------------------------------------------------------------------------------

def test_the_request_type_comes_from_the_method_called_never_from_the_text():
    c, tts, _ = controller()
    assert c.speak_answer("There are 17 people in the room.").request_type is RequestType.ANSWER
    assert c.speak_answer(so.FALLBACK_TEXT).request_type is RequestType.ANSWER                     # fallback wording, still an ANSWER
    assert c.speak_answer(so.DEMO_ANNOUNCEMENT_TEXT).request_type is RequestType.ANSWER           # announcement wording, still an ANSWER
    assert c.speak_announcement(announcement(text=so.FALLBACK_TEXT)).request_type is RequestType.ANNOUNCEMENT
    assert c.speak_fallback(FallbackReason.UPSTREAM_TIMEOUT).request_type is RequestType.FALLBACK


# ---- announcements (AI-6 software path) -----------------------------------------------------------------------------------------------------

def test_an_announcement_is_spoken_exactly_as_supplied_and_reported_completed():
    c, tts, playback = controller()
    req = announcement()
    r = c.speak_announcement(req)
    assert tts.texts == [req.text]                                    # unedited
    assert (r.status, r.request_type, r.message_id, r.origin, r.trigger_id) == (OutputStatus.COMPLETED, RequestType.ANNOUNCEMENT, "ann-1", "APP", "trig-7")
    assert r.text_chars == len(req.text) and r.playback_status is PlaybackStatus.COMPLETED and r.error_stage is None and r.error_type is None
    assert (r.tts_engine_id, r.voice_id) == ("fake-tts", "fake-voice") and r.audio_duration_s > 0 and r.synthesis_ms >= 0
    assert r.t_request_accepted <= r.t_tts_started <= r.t_tts_finished
    assert r.t_play_called is not None and r.t_first_buffer_submitted is not None and r.t_finished is not None
    assert len(playback.played) == 1 and c.is_active is False


@pytest.mark.parametrize("source", list(AnnouncementSource))
def test_every_declared_source_is_accepted_but_none_is_connected(source):
    c, _, _ = controller()
    r = c.speak_announcement(announcement(source=source))
    assert r.origin == source.value and r.status is OutputStatus.COMPLETED
    # a test request labelled APP / SCHEDULE proves nothing about a real app or schedule trigger (PENDING TEAM DEPENDENCY)


@pytest.mark.parametrize("kw", [
    dict(announcement_id=""), dict(announcement_id="has space"), dict(announcement_id="-bad"), dict(announcement_id="x" * 65), dict(announcement_id=None),
    dict(text=""), dict(text="   "), dict(text=None), dict(text=b"bytes"), dict(text=5),
    dict(source="APP"), dict(source=None), dict(source=AnnouncementSource), dict(source=1),
    dict(trigger_id=""), dict(trigger_id="bad id"), dict(trigger_id=7),
])
def test_invalid_announcement_requests_are_refused_at_construction(kw):
    with pytest.raises(SpokenOutputRequestError):
        announcement(**kw)


def test_the_announcement_request_has_no_invented_policy_fields():
    assert [f.name for f in dataclasses.fields(AnnouncementRequest)] == ["announcement_id", "text", "source", "trigger_id"]   # no priority until AI-7 defines one
    c, _, _ = controller()
    with pytest.raises(SpokenOutputRequestError):
        c.speak_announcement("just a string")
    with pytest.raises(SpokenOutputRequestError):
        c.speak_announcement(None)


def test_the_fixed_demo_announcement_is_neutral_and_labelled_manual_test():
    req = demo_announcement_request()
    assert req.text == "Attention please. This is a VESTA classroom announcement." and req.source is AnnouncementSource.MANUAL_TEST
    words = set(re.findall(r"[a-z']+", req.text.lower()))
    assert not words & {"professor", "lecturer", "late", "absent", "arrived", "cancelled", "canceled", "room", "people", "temperature"}
    c, tts, _ = controller()
    r = c.speak_announcement(req)
    assert tts.texts == [so.DEMO_ANNOUNCEMENT_TEXT] and r.message_id == so.DEMO_ANNOUNCEMENT_ID and r.origin == "MANUAL_TEST"


def test_the_module_never_writes_or_infers_a_professor_announcement():
    source = (REPO_ROOT / "assistant" / "spoken_output.py").read_text(encoding="utf-8").lower()
    for word in ("professor", "lecturer", "arrival", "occupancy", "classroomstate", "schedule_engine", "calendar"):
        assert word not in source, word


# ---- fallback (AI-9 software path) ------------------------------------------------------------------------------------------------------------

def test_the_fallback_sentence_is_fixed_short_and_synthesizable():
    from speech.tts import check_text
    assert so.FALLBACK_TEXT == "I'm sorry, I can't complete that request right now."
    check_text(so.FALLBACK_TEXT)
    assert 0 < len(so.FALLBACK_TEXT) < 80 and "network" not in so.FALLBACK_TEXT.lower() and "internet" not in so.FALLBACK_TEXT.lower()


@pytest.mark.parametrize("reason", list(FallbackReason))
def test_each_classified_failure_speaks_the_same_fixed_sentence(reason):
    c, tts, playback = controller()
    r = c.speak_fallback(reason)
    assert tts.texts == [so.FALLBACK_TEXT]
    assert (r.status, r.request_type, r.origin) == (OutputStatus.COMPLETED, RequestType.FALLBACK, reason.value)
    assert r.message_id == "fallback-" + reason.value.lower().replace("_", "-") and r.text_chars == len(so.FALLBACK_TEXT)
    assert len(playback.played) == 1


@pytest.mark.parametrize("bad", ["NETWORK_UNAVAILABLE", None, 5, FallbackReason, Exception("x"), "UPSTREAM_TIMEOUT"])
def test_an_unclassified_or_unsupported_failure_trigger_is_refused_not_spoken(bad):
    c, tts, playback = controller()
    with pytest.raises(SpokenOutputRequestError):
        c.speak_fallback(bad)
    assert tts.texts == [] and playback.played == []


def test_fallback_needs_no_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    c, _, _ = controller()
    assert c.speak_fallback(FallbackReason.NETWORK_UNAVAILABLE).status is OutputStatus.COMPLETED


# ---- answers ---------------------------------------------------------------------------------------------------------------------------------------

def test_an_answer_result_text_is_spoken_without_touching_the_answer_engine():
    state = fs.fuse(fs.inputs(v=fs.vis(17), p=fs.pres(True), l=fs.lux(320.0), temps=[fs.temp("t1", 22.5)], h=fs.hum(45.0)))
    ctx = AnswerContext(state, CapabilityDeclaration(frozenset({Capability.PEOPLE_COUNT})))
    answer = DeterministicAnswerEngine().answer(AnswerRequest("How many people are in the room?", ctx))
    c, tts, _ = controller()
    r = c.speak_answer(answer.text, message_id="q1")
    assert tts.texts == ["There are 17 people in the room."] and r.status is OutputStatus.COMPLETED and r.message_id == "q1" and r.origin is None
    for name in ("speak", "speak_answer", "synthesize", "play"):
        assert not hasattr(DeterministicAnswerEngine, name)


def test_a_bad_answer_message_id_is_refused():
    c, tts, _ = controller()
    with pytest.raises(SpokenOutputRequestError):
        c.speak_answer("hello", message_id="has space")
    assert tts.texts == []


# ---- one stack for everything ---------------------------------------------------------------------------------------------------------------------------

def test_answer_announcement_and_fallback_all_use_the_same_tts_and_playback_objects():
    c, tts, playback = controller()
    results = [c.speak_answer("Temperature information is not available."), c.speak_announcement(announcement()), c.speak_fallback(FallbackReason.ANSWER_SERVICE_UNAVAILABLE)]
    assert [r.request_type for r in results] == [RequestType.ANSWER, RequestType.ANNOUNCEMENT, RequestType.FALLBACK]
    assert len(tts.texts) == 3 and len(playback.played) == 3
    assert {type(a).__name__ for a in playback.played} == {"SpeechAudio"}
    assert {a.tts_engine_id for a in playback.played} == {"fake-tts"} and {r.voice_id for r in results} == {"fake-voice"}
    assert all(r.status is OutputStatus.COMPLETED for r in results)


def test_the_controller_needs_exactly_one_tts_and_one_playback_engine():
    for bad in ((None, SpyPlayback()), (SpyTts(), None), ("tts", "playback"), (SpyPlayback(), SpyTts())):
        with pytest.raises(SpokenOutputRequestError):
            SpokenOutputController(*bad)


def test_the_module_imports_no_second_tts_or_playback_stack_and_no_network():
    tree = ast.parse((REPO_ROOT / "assistant" / "spoken_output.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"re", "threading", "time", "dataclasses", "enum", "typing", "audio", "config", "speech"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"SherpaOnnxTtsEngine", "SoundDevicePlaybackEngine", "sounddevice", "sherpa_onnx", "urlopen", "socket", "requests", "np"}


# ---- TTS failure ---------------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [TtsSynthesisFailed("x"), TtsModelLoadFailed("x"), TtsRuntimeUnavailable("x"), TtsContractError("x")])
def test_a_tts_failure_is_reported_as_such_and_playback_is_never_called(error, logs):
    c, tts, playback = controller(tts=SpyTts(error=error))
    r = c.speak_announcement(announcement(text=SECRET))
    assert r.status is OutputStatus.TTS_FAILED and (r.error_stage, r.error_type) == ("TTS", type(error).__name__)
    assert playback.played == [] and playback.stop_calls == 0
    assert r.playback_status is None and r.tts_engine_id is None and r.audio_duration_s is None and r.t_play_called is None
    assert c.is_active is False
    assert "secret" not in "\n".join(logs)


def test_text_the_tts_refuses_is_rejected_at_the_input_stage_not_called_a_tts_failure():
    c, tts, playback = controller()
    for text in ("?!", "...", "   "):
        r = c.speak_answer(text)
        assert (r.status, r.error_stage, r.error_type) == (OutputStatus.REJECTED, "INPUT", "TtsInputError")
    assert playback.played == [] and c.speak_answer("fine").status is OutputStatus.COMPLETED


def test_software_bugs_propagate_and_release_the_controller():
    class Buggy(SpyTts):
        def synthesize(self, text):
            raise ValueError("a bug in the engine")

    c, _, playback = controller(tts=Buggy())
    with pytest.raises(ValueError):
        c.speak_answer("hello")
    assert c.is_active is False and playback.played == []

    class WrongType(SpyTts):
        def synthesize(self, text):
            return "not audio"

    c2, _, p2 = controller(tts=WrongType())
    with pytest.raises(SpokenOutputContractError):
        c2.speak_answer("hello")
    assert c2.is_active is False and p2.played == []

    class WrongPlayback(SpyPlayback):
        def play(self, audio):
            return "not a result"

    c3, _, _ = controller(playback=WrongPlayback())
    with pytest.raises(SpokenOutputContractError):
        c3.speak_answer("hello")
    assert c3.is_active is False

    class PlaybackBug(SpyPlayback):
        def play(self, audio):
            raise KeyError("bug")

    c4, _, _ = controller(playback=PlaybackBug())
    with pytest.raises(KeyError):
        c4.speak_answer("hello")
    assert c4.is_active is False


# ---- playback failure -------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [PlaybackFailed("x"), PlaybackOpenFailed("x"), PlaybackDeviceNotFound("x"), PlaybackFormatError("x")])
def test_a_playback_failure_is_reported_after_a_successful_synthesis_and_never_as_success(error, logs):
    tts, playback = SpyTts(), SpyPlayback(error=error)
    c, _, _ = controller(tts=tts, playback=playback)
    r = c.speak_fallback(FallbackReason.INTERNAL_SERVICE_FAILURE)
    assert len(tts.texts) == 1 and len(playback.played) == 1                       # each stage ran exactly once
    assert r.status is OutputStatus.PLAYBACK_FAILED and (r.error_stage, r.error_type) == ("PLAYBACK", type(error).__name__)
    assert r.status is not OutputStatus.COMPLETED and r.playback_status is None and r.t_play_called is None and r.t_finished is None
    assert r.tts_engine_id == "fake-tts" and r.audio_duration_s > 0                # the synthesis did succeed
    assert c.is_active is False
    assert "status=COMPLETED" not in "\n".join(logs)


def test_a_playback_engine_that_is_busy_elsewhere_is_a_rejection():
    c, _, _ = controller(playback=SpyPlayback(error=PlaybackBusy("shared engine in use")))
    r = c.speak_answer("hello")
    assert (r.status, r.error_stage, r.error_type) == (OutputStatus.REJECTED, "PLAYBACK", "PlaybackBusy")


# ---- stop -----------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_stopped_playback_is_reported_as_stopped_never_completed():
    c, _, _ = controller(playback=SpyPlayback(status=PlaybackStatus.STOPPED))
    r = c.speak_answer("hello")
    assert r.status is OutputStatus.STOPPED and r.playback_status is PlaybackStatus.STOPPED and r.error_stage is None


def test_stop_is_delegated_to_the_playback_engine_and_is_safe_when_idle():
    c, _, playback = controller(playback=SpyPlayback(hold=True))
    assert c.stop() is False and playback.stop_calls == 0
    out = []
    t = threading.Thread(target=lambda: out.append(c.speak_answer("hello")))
    t.start()
    assert playback.entered.wait(5) and c.is_active is True
    assert c.stop() is True and playback.stop_calls == 1
    t.join(5)
    assert not t.is_alive() and c.is_active is False and out[0].status is OutputStatus.COMPLETED     # this fake finished normally after release
    assert c.stop() is False


def test_stop_during_synthesis_skips_playback_and_reports_stopped():
    tts, playback = SpyTts(delay=0.3), SpyPlayback()
    c, _, _ = controller(tts=tts, playback=playback)
    out = []
    t = threading.Thread(target=lambda: out.append(c.speak_answer("hello there")))
    t.start()
    deadline = time.time() + 5
    while not tts.texts and time.time() < deadline:
        time.sleep(0.001)
    assert c.stop() is True
    t.join(5)
    assert out[0].status is OutputStatus.STOPPED and out[0].playback_status is None and out[0].audio_duration_s > 0
    assert playback.played == [] and c.is_active is False


# ---- one output at a time (AI-7 preparation, not arbitration) ---------------------------------------------------------------------------------

def test_a_second_request_while_playing_is_rejected_busy_and_never_plays_concurrently():
    c, tts, playback = controller(playback=SpyPlayback(hold=True))
    first = []
    t = threading.Thread(target=lambda: first.append(c.speak_announcement(announcement())))
    t.start()
    assert playback.entered.wait(5)
    texts_before = list(tts.texts)
    for second in (lambda: c.speak_answer("another"), lambda: c.speak_announcement(announcement(announcement_id="ann-2")), lambda: c.speak_fallback(FallbackReason.UPSTREAM_TIMEOUT)):
        r = second()
        assert (r.status, r.error_stage, r.error_type) == (OutputStatus.REJECTED, "BUSY", "OutputBusy")
        assert r.tts_engine_id is None and r.playback_status is None
    assert tts.texts == texts_before and len(playback.played) == 1                # no synthesis, no second play
    playback.release.set()
    t.join(5)
    assert first[0].status is OutputStatus.COMPLETED and playback.max_active == 1
    assert len(playback.played) == 1                                              # nothing was queued behind it
    assert c.speak_answer("now it is free").status is OutputStatus.COMPLETED


def test_a_request_during_synthesis_is_also_rejected():
    tts = SpyTts(delay=0.3)
    c, _, playback = controller(tts=tts)
    t = threading.Thread(target=lambda: c.speak_answer("slow one"))
    t.start()
    while not tts.texts:
        time.sleep(0.001)
    assert c.is_active is True
    assert c.speak_answer("second").error_stage == "BUSY"
    t.join(5)
    assert len(tts.texts) == 1 and len(playback.played) == 1


def test_a_rejected_request_does_not_poison_the_next_one():
    c, _, _ = controller()
    c._active = True                                                              # simulate an active output
    assert c.speak_answer("x").status is OutputStatus.REJECTED
    c._active = False
    assert c.speak_answer("x").status is OutputStatus.COMPLETED


# ---- the result contract ------------------------------------------------------------------------------------------------------------------------------------

def _completed(**kw):
    base = dict(request_type=RequestType.ANSWER, status=OutputStatus.COMPLETED, message_id=None, origin=None, text_chars=5,
                tts_engine_id="e", voice_id="v", playback_status=PlaybackStatus.COMPLETED, audio_duration_s=1.0, synthesis_ms=5.0,
                t_request_accepted=1.0, t_tts_started=1.1, t_tts_finished=1.2, t_play_called=10.0, t_first_buffer_submitted=10.1, t_finished=11.0)
    base.update(kw)
    return SpokenOutputResult(**base)


def test_result_is_frozen_has_the_agreed_fields_and_carries_no_text_or_audio():
    r = _completed()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = OutputStatus.STOPPED
    names = [f.name for f in dataclasses.fields(SpokenOutputResult)]
    for needed in ("request_type", "status", "message_id", "tts_engine_id", "voice_id", "playback_status", "audio_duration_s", "synthesis_ms",
                   "t_play_called", "t_first_buffer_submitted", "t_finished", "error_stage", "error_type"):
        assert needed in names
    assert not {"text", "samples", "pcm", "audio", "message"} & set(names)
    assert {s.name for s in OutputStatus} == {"COMPLETED", "STOPPED", "TTS_FAILED", "PLAYBACK_FAILED", "REJECTED"}      # no HARDWARE_VALIDATED / AUDIBLE


@pytest.mark.parametrize("kw", [
    dict(status="COMPLETED"), dict(request_type="ANSWER"), dict(text_chars=-1), dict(text_chars=True),
    dict(playback_status=PlaybackStatus.STOPPED), dict(playback_status=None), dict(t_play_called=None), dict(t_first_buffer_submitted=None), dict(t_finished=None),
    dict(tts_engine_id=None), dict(audio_duration_s=None), dict(synthesis_ms=None), dict(error_stage="TTS"), dict(error_type="X"),
    dict(t_tts_started=0.5), dict(t_tts_finished=1.05),
])
def test_invalid_completed_results_are_refused(kw):
    with pytest.raises(SpokenOutputContractError):
        _completed(**kw)


@pytest.mark.parametrize("kw", [
    dict(status=OutputStatus.TTS_FAILED, playback_status=None, error_stage="TTS", error_type="E"),                                       # still carries tts ids
    dict(status=OutputStatus.TTS_FAILED, tts_engine_id=None, voice_id=None, audio_duration_s=None, synthesis_ms=None, error_stage="PLAYBACK", error_type="E", playback_status=None),
    dict(status=OutputStatus.TTS_FAILED, tts_engine_id=None, voice_id=None, audio_duration_s=None, synthesis_ms=None, error_stage="TTS", error_type=None, playback_status=None),
    dict(status=OutputStatus.PLAYBACK_FAILED, error_stage="TTS", error_type="E", playback_status=None),
    dict(status=OutputStatus.PLAYBACK_FAILED, error_stage="PLAYBACK", error_type="E"),                                                    # playback status must be None
    dict(status=OutputStatus.PLAYBACK_FAILED, tts_engine_id=None, error_stage="PLAYBACK", error_type="E", playback_status=None),
    dict(status=OutputStatus.REJECTED, error_stage="TTS", error_type="E", playback_status=None),
    dict(status=OutputStatus.REJECTED, error_stage=None, error_type="E", playback_status=None),
    dict(status=OutputStatus.STOPPED, playback_status=PlaybackStatus.COMPLETED),
    dict(status=OutputStatus.STOPPED, error_stage="X", error_type="Y", playback_status=None),
])
def test_invalid_failure_and_stop_results_are_refused(kw):
    with pytest.raises(SpokenOutputContractError):
        _completed(**kw)


def test_the_controller_clock_is_injectable():
    ticks = iter(float(i) for i in range(500, 600))
    c, _, _ = controller(clock=lambda: next(ticks))
    r = c.speak_answer("hello")
    assert (r.t_request_accepted, r.t_tts_started, r.t_tts_finished) == (500.0, 501.0, 502.0)


# ---- privacy ---------------------------------------------------------------------------------------------------------------------------------------------------------

def test_no_text_or_audio_reaches_the_logs_for_any_request_type_or_failure(logs):
    c, _, _ = controller()
    c.speak_answer(SECRET)
    c.speak_announcement(announcement(text=SECRET + " announced"))
    c.speak_fallback(FallbackReason.NETWORK_UNAVAILABLE)
    controller(tts=SpyTts(error=TtsSynthesisFailed(SECRET)))[0].speak_answer(SECRET)
    controller(playback=SpyPlayback(error=PlaybackFailed(SECRET)))[0].speak_answer(SECRET)
    joined = "\n".join(logs)
    assert joined.count("event=spoken_output_done") == 5
    for fragment in ("secret", "exam", "forty", "announced", "sorry", "right now", "Lunch", "Attention", str(so.FALLBACK_TEXT)):
        assert fragment not in joined, fragment
    assert "[" not in joined and "array(" not in joined
    line = next(l for l in logs if "request_type=ANNOUNCEMENT" in l)
    for field in ("status=COMPLETED", "message_id=ann-1", "origin=APP", f"text_chars={len(SECRET + ' announced')}", "tts=fake-tts", "voice=fake-voice", "error_stage=-"):
        assert field in line


def test_the_result_repr_contains_neither_text_nor_samples():
    c, _, _ = controller()
    r = c.speak_answer(SECRET)
    text = repr(r)
    assert "secret" not in text and "array" not in text and "samples" not in text


# ---- imports ---------------------------------------------------------------------------------------------------------------------------------------------------------

def test_importing_the_controller_loads_no_engine_runtime():
    code = (
        "import sys\n"
        "import assistant.spoken_output\n"
        "bad = [m for m in ('sherpa_onnx', 'faster_whisper', 'ctranslate2', 'huggingface_hub') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr


# =============================================================================================================
# Real offline voice + FAKE playback. Nothing is played. Proves only that the software stack can synthesize the fixed
# fallback sentence and the demo announcement offline and report a software result. No audibility, no speaker.
# =============================================================================================================

def _real_tts():
    from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
    from speech.tts import TtsModelNotFound
    try:
        return SherpaOnnxTtsEngine()
    except TtsModelNotFound:
        pytest.skip("approved voice package not present")
    except TtsRuntimeUnavailable as exc:
        pytest.skip(f"sherpa-onnx not importable: {exc}")


@pytest.fixture(scope="module")
def real_tts():
    return _real_tts()


def test_real_offline_fallback_sentence_through_the_controller_with_the_network_disabled(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    tts = _real_tts()                                                              # construction under a closed network
    playback = SpyPlayback()
    r = SpokenOutputController(tts, playback).speak_fallback(FallbackReason.NETWORK_UNAVAILABLE)
    assert tts.voice_verified is True                                              # the manifest-verified voice package
    assert r.status is OutputStatus.COMPLETED and (r.tts_engine_id, r.voice_id) == ("sherpa-onnx", "en_US-lessac-medium")
    audio = playback.played[0]
    assert audio.sample_rate == 22050 and audio.samples.dtype == np.int16 and audio.samples.any() and 0.5 < audio.duration_s < 10
    assert np.isfinite(r.synthesis_ms) and r.synthesis_ms > 0


def test_real_voice_speaks_the_demo_announcement_into_a_fake_playback(real_tts):
    playback = SpyPlayback()
    r = SpokenOutputController(real_tts, playback).speak_announcement(demo_announcement_request())
    assert r.status is OutputStatus.COMPLETED and r.origin == "MANUAL_TEST" and playback.played[0].samples.any()
