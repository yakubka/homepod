"""Week 8 Task 3: the intent router inside the voice-assistant runtime (assistant/runtime.py).

SOFTWARE-VALIDATED WITH FAKE INPUT / OUTPUT. The real router, the real answering engine, the real arbiter and the real state manager run
behind a scripted wake detector, an energy VAD, a scripted STT and fake TTS / playback. NOTHING IS EXECUTED anywhere in these tests: a command
comes back as a StructuredCommand with execution_status NOT_EXECUTED. They say nothing about speech recognition, a classroom, a device, an
ESP32, latency or a live voice assistant.
"""

import ast
import hashlib
import json
import re
import threading
from pathlib import Path

import pytest

from assistant import runtime as rtm
from assistant.answering import AnswerContext, AnswerStatus, Capability, CapabilityDeclaration, DeterministicAnswerEngine
from assistant.output_arbiter import Decision
from assistant.runtime import (
    AMBIGUOUS_TEXT, COMMAND_NOT_EXECUTED_TEXT, FIXED_REPLIES, INVALID_COMMAND_TEXT, INVALID_INPUT_TEXT, NOT_SUPPORTED_TEXT, ExpectedServiceFailure,
    OutcomeKind, RuntimeConfigError,
)
from assistant.spoken_output import FallbackReason, OutputStatus
from assistant.state import AssistantState
from evaluation import week8_intent_eval as w8
from intents.contracts import (
    COMMAND_CATEGORIES, CommandAction, CommandTarget, ExecutionStatus, Handling, IntentCategory, IntentRequest, IntentResult, RoutingStatus,
)
from intents.router import route_intent
from tests.runtime_support import (
    GUARD_S, SILENCE, UTTERANCE, Rig, RuntimePlayback, RuntimeTts, S, SpyEngine, make_result, stream,
)

REPO = Path(__file__).resolve().parent.parent
K = OutcomeKind
SECRET = "zebra quartz marmalade"
FIXTURES = w8.simulated_answer_fixtures()            # SIMULATED classroom states from the project's synthetic fusion builders


def say(text, **kw):
    """A rig whose scripted STT hears `text` (the wake phrase is added and removed by the real pipeline)."""
    return Rig(replies=(f"hey jarvis {text}",), **kw)


def run(text, fixture="NONE", **kw):
    rig = say(text, context_provider=lambda: FIXTURES[fixture], **kw)
    outcomes = rig.drive(UTTERANCE)
    assert len(outcomes) == 1, [o.kind for o in outcomes]
    return rig, outcomes[0]


class CountingRouter:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return route_intent(request)


# =========================================================================================
# A. information queries: routed, then answered by the FROZEN engine with its own rules
# =========================================================================================

@pytest.mark.parametrize("text,category,family,fragment", [
    ("how many students are here", IntentCategory.OCCUPANCY_QUERY, "ROOM_PEOPLE_COUNT", "17 people"),
    ("what is the temperature", IntentCategory.SENSOR_QUERY, "ROOM_TEMPERATURE", "22.5"),
    ("what is the humidity", IntentCategory.SENSOR_QUERY, "ROOM_HUMIDITY", "45"),
    ("how bright is the room", IntentCategory.SENSOR_QUERY, "ROOM_BRIGHTNESS", "320"),
])
def test_q1_to_q4_are_routed_then_answered_from_the_simulated_state_with_provenance(text, category, family, fragment):
    rig, o = run(text, "FRESH")
    assert (o.kind, o.intent_category, o.routing_status, o.query_family, o.handling) == (K.ANSWER_OUTPUT, category.value, "ROUTED", family, "ANSWERING_FOUNDATION")
    assert o.answer_status == "ANSWERED" and o.answer_source == "SIMULATED" and o.command is None
    assert fragment in rig.tts.texts[0] and o.output.status is OutputStatus.COMPLETED


def test_zero_detected_people_is_a_real_answer_not_a_missing_one():
    rig, o = run("how many students are here", "ZERO_PEOPLE")
    assert o.answer_status == "ANSWERED" and "0 people" in rig.tts.texts[0]


@pytest.mark.parametrize("fixture,status", [("NONE", "INFORMATION_UNAVAILABLE"), ("STALE", "INFORMATION_UNAVAILABLE"), ("NO_TEMPERATURE", "INFORMATION_UNAVAILABLE"),
                                            ("UNCONFIGURED", "UNSUPPORTED_QUESTION")])
def test_missing_stale_and_unconfigured_data_is_never_turned_into_a_value(fixture, status):
    rig, o = run("what is the temperature", fixture)
    assert o.kind is K.ANSWER_OUTPUT and o.answer_status == status
    assert not any(ch.isdigit() for ch in rig.tts.texts[0])
    assert "22" not in rig.tts.texts[0]


def test_the_default_context_declares_nothing_so_a_routed_room_query_is_not_answered():
    rig = say("what is the temperature")
    (o,) = rig.drive(UTTERANCE)
    assert o.routing_status == "ROUTED" and o.answer_status == "UNSUPPORTED_QUESTION" and o.answer_reason_code == "CAPABILITY_NOT_DECLARED"


@pytest.mark.parametrize("text,fixture,family,status,fragment", [
    ("what lecture is scheduled now", "SCHEDULE_NO_STATE", "LECTURE_CURRENT", "ANSWERED", "Signals"),
    ("what lecture is scheduled now", "NONE", "LECTURE_CURRENT", "INFORMATION_UNAVAILABLE", None),
    ("how many students are expected", "SCHEDULE_NO_STATE", "LECTURE_EXPECTED_ATTENDANCE", "ANSWERED", "30"),
    ("has the professor arrived yet", "SCHEDULE_AND_FRESH", "PROFESSOR_ARRIVAL", "INFORMATION_UNAVAILABLE", None),
    ("what can you do", "NONE", "CAPABILITIES", "ANSWERED", "VESTA"),
    ("when is the next class", "SCHEDULE_NO_STATE", "NEXT_CLASS", "ANSWERED", "14:00"),
    ("can you make an announcement", "NONE", "ANNOUNCEMENT_CAPABILITY", "ANSWERED", "Yes"),
])
def test_q5_to_q10_are_general_ai_and_use_the_deterministic_answering_path_never_a_model(text, fixture, family, status, fragment):
    engine = SpyEngine()
    rig, o = run(text, fixture, answer_engine=engine)
    assert (o.intent_category, o.handling, o.query_family, o.answer_status) == ("GENERAL_AI", "ANSWERING_FOUNDATION", family, status)
    assert engine.calls == 1 and engine.texts == [rtm.answer_query.__globals__["CANONICAL_PHRASE"][family]]      # the canonical phrase of that family, once
    if fragment:
        assert fragment in rig.tts.texts[0]


def test_expected_attendance_never_becomes_the_detected_count_through_the_runtime():
    rig, people = run("how many students are here", "SCHEDULE_NO_STATE")
    assert people.intent_category == "OCCUPANCY_QUERY" and people.answer_status == "INFORMATION_UNAVAILABLE" and "30" not in rig.tts.texts[0]
    rig2, expected = run("how many students are expected", "SCHEDULE_AND_FRESH")
    assert expected.intent_category == "GENERAL_AI" and "30" in rig2.tts.texts[0] and "17" not in rig2.tts.texts[0]


def test_open_ended_general_ai_has_no_backend_and_the_answering_engine_is_never_asked():
    engine = SpyEngine()
    rig, o = run("explain machine learning", answer_engine=engine)
    assert (o.kind, o.intent_category, o.handling, o.answer_status) == (K.NOT_ANSWERABLE, "GENERAL_AI", "NO_BACKEND_APPROVED", None)
    assert engine.calls == 0 and rig.tts.texts == [NOT_SUPPORTED_TEXT] and o.command is None


@pytest.mark.parametrize("text,category", [("is the room dark", "SENSOR_QUERY"), ("is anyone in the room", "OCCUPANCY_QUERY")])
def test_a_classified_query_without_answer_logic_says_so_and_invents_nothing(text, category):
    engine = SpyEngine()
    rig, o = run(text, "FRESH", answer_engine=engine)
    assert (o.kind, o.intent_category, o.handling) == (K.NOT_ANSWERABLE, category, "ANSWER_NOT_IMPLEMENTED")
    assert engine.calls == 0 and rig.tts.texts == [NOT_SUPPORTED_TEXT]


def test_an_explicit_service_failure_in_the_answering_path_still_uses_the_offline_fallback():
    rig, o = run("what is the temperature", "FRESH", answer_engine=SpyEngine(error=ExpectedServiceFailure(FallbackReason.UPSTREAM_TIMEOUT)))
    assert o.kind is K.FALLBACK_OUTPUT and o.fallback_reason is FallbackReason.UPSTREAM_TIMEOUT


# =========================================================================================
# B. action requests: a structured command comes back, NOTHING is executed or confirmed
# =========================================================================================

COMMANDS = [
    ("volume up", IntentCategory.LOCAL_COMMAND, CommandAction.VOLUME_UP, CommandTarget.SPEAKER_OUTPUT, None),
    ("mute", IntentCategory.LOCAL_COMMAND, CommandAction.MUTE, CommandTarget.SPEAKER_OUTPUT, None),
    ("set the volume to 40 percent", IntentCategory.LOCAL_COMMAND, CommandAction.SET_VOLUME, CommandTarget.SPEAKER_OUTPUT, 40),
    ("turn on the lights", IntentCategory.CLASSROOM_CONTROL, CommandAction.TURN_ON, CommandTarget.CLASSROOM_LIGHTS, None),
    ("turn off the classroom lights", IntentCategory.CLASSROOM_CONTROL, CommandAction.TURN_OFF, CommandTarget.CLASSROOM_LIGHTS, None),
    ("turn off the smart bulb", IntentCategory.SMART_HOME, CommandAction.TURN_OFF, CommandTarget.SMART_BULB, None),
    ("set the smart bulb brightness to 30 percent", IntentCategory.SMART_HOME, CommandAction.SET_BRIGHTNESS, CommandTarget.SMART_BULB, 30),
]


@pytest.mark.parametrize("text,category,action,target,value", COMMANDS)
def test_a_validated_command_is_returned_not_executed_and_never_confirmed(text, category, action, target, value):
    rig, o = run(text)
    c = o.command
    assert (o.kind, o.intent_category, o.routing_status, o.handling) == (K.COMMAND_NOT_EXECUTED, category.value, "ROUTED", "EXECUTION_NOT_IMPLEMENTED")
    assert (c.intent_category, c.action, c.target, c.value) == (category, action, target, value) and c.requires_acknowledgement is True
    assert o.execution_status == "NOT_EXECUTED" and o.spoken_confirmation_allowed is False
    assert rig.tts.texts == [COMMAND_NOT_EXECUTED_TEXT] and o.answer_status is None
    assert o.output.status is OutputStatus.COMPLETED                              # the NOTICE was spoken; the command was not carried out


def test_the_answering_engine_is_never_asked_about_a_command():
    engine = SpyEngine()
    for text, *_ in COMMANDS:
        run(text, answer_engine=engine)
    assert engine.calls == 0


def test_no_spoken_reply_to_any_request_claims_that_something_was_done():
    words = ("done", "okay", "ok,", "turned", "turning", "has been", "have been", "success", "executed", "completed", "muted", "sure", "all set", "now on", "now off")
    for reply in FIXED_REPLIES:
        assert not any(w in reply.lower() for w in words), reply
    spoken = set()
    for text, *_ in COMMANDS:
        rig, o = run(text)
        spoken.update(rig.tts.texts)
    assert spoken == {COMMAND_NOT_EXECUTED_TEXT}


def test_a_command_outcome_carries_no_execution_result_of_any_kind():
    _, o = run("turn on the lights")
    assert o.execution_status == ExecutionStatus.NOT_EXECUTED.value
    assert not hasattr(o, "executed") and not hasattr(o, "execution_result") and "success" not in repr(o).lower()


@pytest.mark.parametrize("text,reason", [("set the volume to -5 percent", "INVALID_VALUE"), ("set the volume to 150 percent", "VALUE_OUT_OF_RANGE"),
                                         ("set the volume to 50 degrees", "INCORRECT_UNIT"), ("set the volume", "MISSING_VALUE"),
                                         ("set the smart bulb brightness to 12.5 percent", "INVALID_VALUE")])
def test_an_invalid_numeric_command_is_rejected_with_no_command(text, reason):
    rig, o = run(text)
    assert (o.kind, o.routing_status, o.command) == (K.REQUEST_REJECTED, "INVALID_COMMAND", None)
    assert o.intent_category in {c.value for c in COMMAND_CATEGORIES} and rig.tts.texts == [INVALID_COMMAND_TEXT]
    assert o.handling == "NONE" and o.execution_status == "NOT_EXECUTED"


@pytest.mark.parametrize("text", ["turn on the light", "turn it on", "turn on", "set the light to 50 percent"])
def test_an_ambiguous_request_is_rejected_not_guessed(text):
    rig, o = run(text)
    assert (o.kind, o.routing_status, o.command, o.intent_category) == (K.REQUEST_REJECTED, "AMBIGUOUS", None, None)
    assert rig.tts.texts == [AMBIGUOUS_TEXT]


@pytest.mark.parametrize("text", ["turn on the projector", "turn off the fan", "turn on the bedroom light", "turn on the left lights", "open the door",
                                  "unlock the door", "turn on the lights and mute"])
def test_an_unsupported_target_or_utterance_is_rejected_and_never_reinterpreted_as_general_ai(text):
    engine = SpyEngine()
    rig, o = run(text, answer_engine=engine)
    assert (o.kind, o.routing_status, o.command, o.intent_category, o.handling) == (K.REQUEST_REJECTED, "UNSUPPORTED", None, None, "NONE")
    assert rig.tts.texts == [NOT_SUPPORTED_TEXT] and engine.calls == 0


def test_a_transcript_that_is_only_punctuation_is_an_invalid_input_rejection():
    """The real pipeline already turns '<wake phrase> !!!' into WAKE_ONLY, so this reaches the router through handle_result()."""
    rig = Rig()
    o = rig.runtime.handle_result(make_result(S.TRANSCRIBED, "!!!"))
    assert (o.kind, o.routing_status, o.command) == (K.REQUEST_REJECTED, "INVALID_INPUT", None) and rig.tts.texts == [INVALID_INPUT_TEXT]
    wake_only = say("!!!")
    assert wake_only.drive(UTTERANCE)[0].kind is K.WAKE_ONLY and wake_only.tts.texts == []


def test_a_sensor_topic_question_is_never_sent_to_a_general_backend():
    engine = SpyEngine()
    rig, o = run("what is the temperature in the corridor", "FRESH", answer_engine=engine)
    assert o.routing_status == "UNSUPPORTED" and o.query_family is None and engine.calls == 0 and "22" not in "".join(rig.tts.texts)


# =========================================================================================
# C. exactly one router, exactly where it should be
# =========================================================================================

def test_every_complete_transcript_goes_through_the_one_router_exactly_once():
    router = CountingRouter()
    rig = say("turn on the lights", route=router)
    rig.drive(UTTERANCE)
    assert len(router.requests) == 1 and re.fullmatch(r"c-[0-9a-f]{12}-1", router.requests[0].request_id)    # Week 9 Task 1C id format
    rig.runtime.restart_stream()
    rig.stt.replies = ["hey jarvis what can you do"]
    rig.stt.calls.clear()
    rig.drive(UTTERANCE)
    first, second = (r.request_id for r in router.requests)
    assert second == first[:-1] + "2"                                    # same session, next counter: no hidden second counter


def test_the_router_receives_the_normalised_transcript_and_the_runtime_has_no_second_parser():
    seen = []

    def spy(request):
        seen.append(request.text)
        return route_intent(request)

    rig = say("turn on the lights", route=spy)
    rig.drive(UTTERANCE)
    assert seen == ["turn on the lights"]                       # the wake prefix was removed by the pipeline, nothing else was altered
    tree = ast.parse((REPO / "assistant" / "runtime.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "re" not in {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}      # no regex / keyword parsing in the runtime
    assert {"intents.router", "intents.contracts"} <= imported


@pytest.mark.parametrize("status", [S.WAKE_ONLY, S.STT_EMPTY, S.NO_SPEECH, S.TOO_SHORT, S.INVALID_ACTIVATION, S.MAX_DURATION, S.STREAM_ENDED, S.STT_FAILED])
def test_only_a_complete_transcribed_result_reaches_the_router(status):
    router = CountingRouter()
    rig = Rig(route=router)
    text = "turn on the lights" if status in (S.MAX_DURATION, S.STREAM_ENDED) else ""
    o = rig.runtime.handle_result(make_result(status, text))
    assert router.requests == [] and o.command is None and o.intent_category is None and o.routing_status is None


def test_a_possibly_truncated_utterance_never_becomes_a_device_command():
    for x in (stream(1400, [(300, 1300)]), stream(500, [(300, 500)])):          # MAX_DURATION, STREAM_ENDED
        router = CountingRouter()
        rig = say("turn on the lights", route=router)
        (o,) = rig.drive(x)
        assert o.kind is K.INCOMPLETE_UTTERANCE and o.command is None and router.requests == [] and rig.tts.texts == []


def test_a_wake_only_utterance_and_silence_do_not_reach_the_router():
    router = CountingRouter()
    rig = Rig(replies=("hey jarvis",), route=router)
    assert rig.drive(UTTERANCE)[0].kind is K.WAKE_ONLY
    rig2 = Rig(route=router)
    assert rig2.drive(SILENCE)[0].kind is K.NO_SPEECH and router.requests == []


def test_the_router_is_the_only_gate_the_runtime_cannot_be_given_a_non_router_result():
    rig = say("turn on the lights", route=lambda request: "COMMAND")
    with pytest.raises(RuntimeConfigError):
        rig.drive(UTTERANCE)
    assert rig.tts.texts == [] and rig.state.current_state is AssistantState.IDLE
    with pytest.raises(RuntimeConfigError):
        Rig(route=5)


def test_the_runtime_is_the_only_production_consumer_of_the_intent_package_besides_the_evaluator():
    importers = set()
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("tests/", ".venv/", "intents/")) or "site-packages" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any((isinstance(n, ast.ImportFrom) and n.module and (n.module == "intents" or n.module.startswith("intents."))) for n in ast.walk(tree)):
            importers.add(rel)
    # the frozen copy of the original Task 1 router (Week 8 Task 4) is an evaluation artifact that reproduces the v1 baseline; it is never imported by production code
    # Week 8 Task 5: the history module rebuilds the Task 4 router for before/after evidence only; production code never imports it
    # Week 9 Task 1B (reviewed addition): the SIMULATED execution slice imports the frozen command vocabulary only; the runtime does not import it
    # Midterm readiness (reviewed addition): the mobile-app contract validates a light request with intents.contracts.validate_command (no router)
    assert importers == {"assistant/runtime.py", "evaluation/week8_intent_eval.py", "evaluation/week8_task1_router_frozen.py", "evaluation/week8_router_history.py",
                         "actions/contracts.py", "actions/simulated_adapter.py", "actions/loop.py", "actions/response_policy.py",
                         "scripts/week9_simulated_action_demo.py", "assistant/app_interface.py"}


def test_the_runtime_code_names_no_device_protocol_or_hardware_interface():
    tree = ast.parse((REPO / "assistant" / "runtime.py").read_text(encoding="utf-8"))
    docstrings = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    tokens = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            tokens.append(n.id)
        elif isinstance(n, ast.Attribute):
            tokens.append(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            tokens.append(n.value)
    # Week 9 Task 1C (reviewed, approved runtime edit): exactly ONE call `self._action_loop.execute(...)` may exist, on the injected SIMULATED loop
    loop_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "execute" and isinstance(n.value, ast.Attribute)
                  and n.value.attr == "_action_loop" and isinstance(n.value.value, ast.Name) and n.value.value.id == "self"]
    assert len(loop_calls) == 1 and tokens.count("execute") == 1
    tokens.remove("execute")
    joined = " ".join(tokens).lower().replace("not_executed", "")          # the only other allowed use of the word is the explicit NOT_EXECUTED
    for word in ("gpio", "uart", "esp32", "serial", "relay", "execute", "volume_up", "turn_on"):
        assert word not in joined, word


# =========================================================================================
# D. state, arbiter and playback
# =========================================================================================

@pytest.mark.parametrize("text", ["what can you do", "turn on the lights", "turn on the projector", "explain machine learning", "set the volume to 150 percent"])
def test_every_kind_of_request_follows_the_red_blue_green_lifecycle_and_ends_idle(text):
    rig, o = run(text)
    assert [s for s, _ in rig.states()] == ["IDLE", "LISTENING", "CHECKING", "SPEAKING", "IDLE"]
    assert rig.colors() == ["NO_ACTIVE_COLOR", "RED", "BLUE", "GREEN", "NO_ACTIVE_COLOR"]
    assert rig.states()[-1] == ("IDLE", "PLAYBACK_COMPLETED") and rig.runtime.input_state_error_count == 0


def test_routing_happens_inside_checking_and_speaking_still_waits_for_the_first_buffer():
    hold = threading.Event()
    rig = say("turn on the lights", playback=RuntimePlayback(before_first=hold))
    t = threading.Thread(target=lambda: rig.drive(UTTERANCE), daemon=True)
    t.start()
    assert rig.playback.called.wait(GUARD_S)
    assert rig.tts.texts == [COMMAND_NOT_EXECUTED_TEXT] and rig.state.current_state is AssistantState.CHECKING
    hold.set()
    t.join(GUARD_S)
    assert [s for s, _ in rig.states()][-2:] == ["SPEAKING", "IDLE"]


def test_the_command_provenance_links_the_request_id_the_command_and_the_state_events():
    rig, o = run("turn on the lights")
    rid = o.command.request_id
    assert re.fullmatch(r"c-[0-9a-f]{12}-1", rid)
    assert [e.request_id for e in rig.events if e.state is AssistantState.SPEAKING] == [rid]
    assert o.routing_rule_id == "CC.TURN_ON" and o.arbitration.request_id == rid and o.arbitration.decision is Decision.GRANTED


def test_a_busy_arbiter_rejects_the_notice_but_the_command_is_still_returned_and_never_executed():
    gate = threading.Event()
    rig = say("what can you do", tts=RuntimeTts(gate=gate))
    box = {}
    first = threading.Thread(target=lambda: box.update(v=rig.runtime.handle_result(make_result(S.TRANSCRIBED, "what can you do"))), daemon=True)
    first.start()
    assert rig.tts.entered.wait(GUARD_S)
    before = rig.state.current_event
    o = rig.runtime.handle_result(make_result(S.TRANSCRIBED, "turn on the lights"))
    assert o.kind is K.COMMAND_NOT_EXECUTED and o.command is not None and o.output is None
    assert o.arbitration.decision is Decision.REJECTED_ACTIVE_OUTPUT and o.execution_status == "NOT_EXECUTED"
    assert len(rig.tts.texts) == 1 and rig.playback.calls == 0 and rig.state.current_event == before
    gate.set()
    first.join(GUARD_S)
    assert box["v"].output.status is OutputStatus.COMPLETED and rig.playback.max_concurrent == 1


def test_a_rejected_request_during_speech_and_a_command_cannot_play_concurrently():
    gate = threading.Event()
    rig = say("what can you do", tts=RuntimeTts(gate=gate))
    barrier = threading.Barrier(2)
    results, back = [], threading.Event()

    def ask(text):
        barrier.wait(GUARD_S)
        results.append(rig.runtime.handle_result(make_result(S.TRANSCRIBED, text)))
        back.set()

    threads = [threading.Thread(target=ask, args=(t,), daemon=True) for t in ("turn on the lights", "turn on the projector")]
    for t in threads:
        t.start()
    assert rig.tts.entered.wait(GUARD_S) and back.wait(GUARD_S)
    gate.set()
    for t in threads:
        t.join(GUARD_S)
    assert len(results) == 2 and sorted(o.arbitration.decision.value for o in results) == ["GRANTED", "REJECTED_ACTIVE_OUTPUT"]
    assert rig.playback.max_concurrent == 1 and rig.playback.calls == 1 and len(rig.tts.texts) == 1


def test_stopping_the_notice_does_not_change_what_happened_to_the_command():
    rig = say("turn on the lights", playback=RuntimePlayback(hold=True))
    box = {}
    t = threading.Thread(target=lambda: box.update(v=rig.drive(UTTERANCE)), daemon=True)
    t.start()
    assert rig.playback.first_sent.wait(GUARD_S)
    assert rig.runtime.stop_output() is True
    t.join(GUARD_S)
    (o,) = box["v"]
    assert o.kind is K.COMMAND_NOT_EXECUTED and o.output.status is OutputStatus.STOPPED and o.execution_status == "NOT_EXECUTED" and o.command is not None


# =========================================================================================
# E. errors, recovery, privacy, determinism
# =========================================================================================

def test_a_router_bug_propagates_is_not_spoken_about_and_the_runtime_recovers():
    state = {"fail": True}

    def flaky(request):
        if state["fail"]:
            raise RuntimeError("router bug")
        return route_intent(request)

    rig = say("turn on the lights", route=flaky)
    with pytest.raises(RuntimeError):
        rig.drive(UTTERANCE)
    assert rig.tts.texts == [] and rig.state.current_state is AssistantState.IDLE and rig.states()[-1] == ("IDLE", "OUTPUT_ERROR")
    state["fail"] = False
    rig.runtime.restart_stream()
    (o,) = rig.drive(UTTERANCE)
    assert o.kind is K.COMMAND_NOT_EXECUTED and rig.states()[-1] == ("IDLE", "PLAYBACK_COMPLETED")


def test_an_answering_bug_after_routing_still_propagates_with_no_fallback_speech():
    rig = say("what can you do", answer_engine=SpyEngine(error=TypeError("bug")))
    with pytest.raises(TypeError):
        rig.drive(UTTERANCE)
    assert rig.tts.texts == [] and rig.state.current_state is AssistantState.IDLE


def test_a_tts_failure_on_the_command_notice_is_reported_and_still_not_an_execution():
    from speech.tts import TtsSynthesisFailed
    rig, o = run("turn on the lights", tts=RuntimeTts(error=TtsSynthesisFailed("voice broke")))
    assert o.kind is K.COMMAND_NOT_EXECUTED and o.output.status is OutputStatus.TTS_FAILED and o.command is not None and o.execution_status == "NOT_EXECUTED"


def test_the_sentinel_request_text_reaches_no_log_no_outcome_and_no_state_event():
    import logging

    class Lines(logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []

        def emit(self, record):
            self.lines.append(record.getMessage())

    handler = Lines()
    rtm.logger.addHandler(handler)
    try:
        blobs = []
        for text in (f"turn on the {SECRET}", f"set the volume to {SECRET} percent", f"explain {SECRET}", f"how many {SECRET} are here", "turn on the lights"):
            rig = say(text, context_provider=lambda: FIXTURES["FRESH"])
            outcomes = rig.drive(UTTERANCE)
            blobs.append(repr(outcomes) + repr(rig.events))
    finally:
        rtm.logger.removeHandler(handler)
    blob = "\n".join(blobs + handler.lines)
    assert SECRET not in blob and "zebra" not in blob and "marmalade" not in blob
    assert any("event=intent_routed" in l for l in handler.lines) and any("event=runtime_outcome" in l for l in handler.lines)
    assert "turn on the lights" not in "\n".join(handler.lines)            # not even a valid command is logged as text


def test_the_text_observer_is_the_only_way_text_leaves_and_it_shows_what_is_spoken():
    seen = []
    rig = say("turn on the lights", text_observer=lambda kind, text: seen.append((kind, text)))
    rig.drive(UTTERANCE)
    assert seen == [("TRANSCRIPT", "turn on the lights"), ("ANSWER", COMMAND_NOT_EXECUTED_TEXT)]


def test_no_outcome_holds_pcm():
    import dataclasses
    import numpy as np
    _, o = run("turn on the lights")
    for obj in (o, o.command, o.arbitration, o.output):
        for f in dataclasses.fields(obj):
            assert not isinstance(getattr(obj, f.name), np.ndarray)


def test_repeating_the_same_fake_input_gives_identical_outcomes_and_state_sequences():
    def once():
        keys, states, spoken = [], [], []
        for text, fixture in (("what is the temperature", "FRESH"), ("turn on the lights", "NONE"), ("turn on the projector", "NONE"), ("explain gravity", "NONE")):
            rig, o = run(text, fixture)
            keys.append((o.kind, o.intent_category, o.routing_status, o.handling, o.routing_rule_id, o.answer_status,
                         o.command and (o.command.action, o.command.target, o.command.value)))
            states.append(rig.states())
            spoken.append(rig.tts.texts)
        return keys, states, spoken

    assert once() == once()


# =========================================================================================
# F. the Task 2 corpus is unchanged and its results are still what they were
# =========================================================================================

def test_the_frozen_intent_corpus_and_the_task_1_files_are_byte_identical_to_their_recorded_hashes():
    expected = {
        "evaluation/week8_intent_manifest_v1.json": "a03f494c5f8d9a0abc3a786879a4e424e259e63e75baa012853868f25572c8b0",
    }
    for rel, digest in expected.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == digest, rel


def test_the_task_2_results_are_unchanged_and_the_weak_generalization_stays_visible():
    # the ORIGINAL router (frozen copy): these are the recorded Task 2 baseline numbers; the new router's numbers are pinned in tests/test_week8_task4_routing.py
    r = w8.evaluate(w8.load_manifest(w8.DEFAULT_MANIFEST), route=w8.load_router("task1-frozen"), router_name="task1-frozen")
    s = r["strata"]
    spec = s["SPECIFICATION_CONFORMANCE"]
    assert (spec["category_accuracy"]["numerator"], spec["category_accuracy"]["denominator"]) == (153, 153)
    assert (spec["routing_outcome_agreement"]["numerator"], spec["routing_outcome_agreement"]["denominator"]) == (245, 245)
    assert (s["SPEC_TABLE"]["n_items"], s["SPEC_AUTHORED"]["n_items"], s["DEV_AUTHORED"]["n_items"]) == (112, 133, 35)
    d = s["DEV_AUTHORED"]
    assert (d["category_accuracy"]["numerator"], d["category_accuracy"]["denominator"]) == (5, 30)
    assert (d["routing_outcome_agreement"]["numerator"], d["routing_outcome_agreement"]["denominator"]) == (10, 35)
    assert s["ALL"]["command_safety"]["false_command_acceptance"]["numerator"] == 0 and w8.safety_violations(r) == 0
    assert r["real_world_intent_accuracy"] == "NOT MEASURED"


# =========================================================================================
# G. real local TTS with fake playback (justified: the fixed replies must be speakable by the real voice)
# =========================================================================================

def test_every_fixed_reply_is_speakable_by_the_real_local_voice():
    from speech.tts import TtsError
    try:
        from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
        voice = SherpaOnnxTtsEngine()
    except (TtsError, OSError) as exc:
        pytest.skip(f"local voice not available ({type(exc).__name__})")
    for reply in FIXED_REPLIES:
        audio = voice.synthesize(reply)
        assert audio.samples.size > 0 and audio.duration_s > 0.3
    rig = say("turn on the lights", tts=voice)
    (o,) = rig.drive(UTTERANCE)
    assert o.kind is K.COMMAND_NOT_EXECUTED and o.output.status is OutputStatus.COMPLETED and rig.playback.calls == 1


def test_the_classroom_context_is_read_afresh_for_every_request_so_stale_data_is_never_reused():
    contexts = iter([FIXTURES["FRESH"], FIXTURES["STALE"]])
    rig = say("what is the temperature", context_provider=lambda: next(contexts))
    first = rig.drive(UTTERANCE)
    rig.runtime.restart_stream()
    rig.stt.calls.clear()
    second = rig.drive(UTTERANCE)
    assert first[0].answer_status == "ANSWERED" and "22.5" in rig.tts.texts[0]
    assert second[0].answer_status == "INFORMATION_UNAVAILABLE" and "22.5" not in rig.tts.texts[1]
