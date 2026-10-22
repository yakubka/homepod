"""Week 8 Task 1: the intent contract, the structured-command schema and the deterministic router (intents/).

SOFTWARE-VALIDATED. Nothing here executes a command, touches a device, a microphone or the network, or says anything about speech
recognition. Sensor values come from fusion's SIMULATED synthetic builders (the same ones the answering tests use). "Hardware success"
cannot exist at this stage: the only execution status is NOT_EXECUTED.
"""

import ast
import dataclasses
import logging
import re
from pathlib import Path

import pytest

from assistant.answering import (
    PHRASES as ANSWER_PHRASES, AnswerContext, AnswerEngine, AnswerStatus, Capability, CapabilityDeclaration, DeterministicAnswerEngine,
    Intent as AnsweringIntent,
)
from fusion.contracts import Origin
from intents import router as rt
from intents.contracts import (
    COMMAND_CATEGORIES, SUPPORTED_COMMANDS, CommandAction, CommandTarget, CommandUnit, CommandValidationResult, CommandValidationStatus as CV,
    ExecutionStatus, Handling, IntentCategory, IntentContractError, IntentRequest, IntentResult, RoutingStatus, StructuredCommand,
    validate_command,
)
from intents.router import CANONICAL_PHRASE, answer_query, route_intent
from tests import fusion_synthetic as fs

REPO = Path(__file__).resolve().parent.parent
IC, RS, H = IntentCategory, RoutingStatus, Handling
ROOM = frozenset({Capability.PEOPLE_COUNT, Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS})
SECRET = "zebra quartz marmalade"


def route(text, rid="t1"):
    return route_intent(IntentRequest(text, rid))


def room_state(people=17, lux=320.0, temp=22.5, humidity=45.0, t=99.0):
    return fs.fuse(fs.inputs(v=None if people is None else fs.vis(people, t, Origin.SIMULATED), p=fs.pres(True, t, Origin.SIMULATED),
                             l=None if lux is None else fs.lux(lux, t, Origin.SIMULATED),
                             temps=[] if temp is None else [fs.temp("temp_1", temp, t, Origin.SIMULATED)],
                             h=None if humidity is None else fs.hum(humidity, t, Origin.SIMULATED), cfg=fs.CFG))


def context(state="fixture", caps=ROOM):
    return AnswerContext(classroom_state=room_state() if state == "fixture" else state, capabilities=CapabilityDeclaration(frozenset(caps)))


class _Logs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    h = _Logs()
    rt.logger.addHandler(h)
    yield h.lines
    rt.logger.removeHandler(h)


# =========================================================================================
# A. the six official categories and the routing outcomes
# =========================================================================================

def test_the_six_official_categories_have_exactly_the_official_names():
    assert [c.value for c in IntentCategory] == ["LOCAL_COMMAND", "SENSOR_QUERY", "OCCUPANCY_QUERY", "CLASSROOM_CONTROL", "SMART_HOME", "GENERAL_AI"]
    assert [c.name for c in IntentCategory] == [c.value for c in IntentCategory]


def test_unsupported_ambiguous_and_the_other_outcomes_are_not_categories():
    names = {c.name for c in IntentCategory}
    assert not names & {"UNKNOWN", "UNSUPPORTED", "AMBIGUOUS", "INVALID_COMMAND", "INVALID_INPUT", "ROUTED"}
    assert {s.name for s in RoutingStatus} == {"ROUTED", "INVALID_COMMAND", "UNSUPPORTED", "AMBIGUOUS", "INVALID_INPUT"}


ROUTED_CASES = [
    # (text, category, rule id, handling)
    ("volume up", IC.LOCAL_COMMAND, "LC.VOLUME_UP", H.EXECUTION_NOT_IMPLEMENTED),
    ("Turn the volume down.", IC.LOCAL_COMMAND, "LC.VOLUME_DOWN", H.EXECUTION_NOT_IMPLEMENTED),
    ("mute", IC.LOCAL_COMMAND, "LC.MUTE", H.EXECUTION_NOT_IMPLEMENTED),
    ("unmute the speaker", IC.LOCAL_COMMAND, "LC.UNMUTE", H.EXECUTION_NOT_IMPLEMENTED),
    ("set the volume to 40 percent", IC.LOCAL_COMMAND, "LC.SET_VOLUME", H.EXECUTION_NOT_IMPLEMENTED),
    ("What is the temperature?", IC.SENSOR_QUERY, "Q.ROOM_TEMPERATURE", H.ANSWERING_FOUNDATION),
    ("what is the humidity in the room", IC.SENSOR_QUERY, "Q.ROOM_HUMIDITY", H.ANSWERING_FOUNDATION),
    ("how bright is the room", IC.SENSOR_QUERY, "Q.ROOM_BRIGHTNESS", H.ANSWERING_FOUNDATION),
    ("is the room dark", IC.SENSOR_QUERY, "Q.ROOM_DARK", H.ANSWER_NOT_IMPLEMENTED),
    ("How many students are here?", IC.OCCUPANCY_QUERY, "Q.ROOM_PEOPLE_COUNT", H.ANSWERING_FOUNDATION),
    ("how many people are in the room", IC.OCCUPANCY_QUERY, "Q.ROOM_PEOPLE_COUNT", H.ANSWERING_FOUNDATION),
    ("is anyone in the room", IC.OCCUPANCY_QUERY, "Q.ROOM_PRESENCE", H.ANSWER_NOT_IMPLEMENTED),
    ("Turn on the lights.", IC.CLASSROOM_CONTROL, "CC.TURN_ON", H.EXECUTION_NOT_IMPLEMENTED),
    ("turn off the classroom lights", IC.CLASSROOM_CONTROL, "CC.TURN_OFF", H.EXECUTION_NOT_IMPLEMENTED),
    ("lights off", IC.CLASSROOM_CONTROL, "CC.TURN_OFF", H.EXECUTION_NOT_IMPLEMENTED),
    ("turn the lights on", IC.CLASSROOM_CONTROL, "CC.TURN_ON", H.EXECUTION_NOT_IMPLEMENTED),
    ("turn on the smart bulb", IC.SMART_HOME, "SH.TURN_ON", H.EXECUTION_NOT_IMPLEMENTED),
    ("switch the smart light off", IC.SMART_HOME, "SH.TURN_OFF", H.EXECUTION_NOT_IMPLEMENTED),
    ("set the smart bulb brightness to 50 percent", IC.SMART_HOME, "SH.SET_BRIGHTNESS", H.EXECUTION_NOT_IMPLEMENTED),
    ("Explain machine learning.", IC.GENERAL_AI, "GEN.OPEN_ENDED", H.NO_BACKEND_APPROVED),
    ("tell me a joke", IC.GENERAL_AI, "GEN.OPEN_ENDED", H.NO_BACKEND_APPROVED),
    ("help me study for my exam", IC.GENERAL_AI, "GEN.OPEN_ENDED", H.NO_BACKEND_APPROVED),
    ("what can you do", IC.GENERAL_AI, "G.CAPABILITIES", H.ANSWERING_FOUNDATION),
    ("has the professor arrived yet", IC.GENERAL_AI, "G.PROFESSOR_ARRIVAL", H.ANSWERING_FOUNDATION),
]


@pytest.mark.parametrize("text,category,rule,handling", ROUTED_CASES)
def test_supported_requests_are_routed_to_the_right_official_category(text, category, rule, handling):
    r = route(text)
    assert (r.routing_status, r.category, r.rule_id, r.handling) == (RS.ROUTED, category, rule, handling)


def test_every_one_of_the_six_categories_is_reachable():
    assert {route(text).category for text, *_ in ROUTED_CASES} == set(IntentCategory)


@pytest.mark.parametrize("text", ["TURN ON THE LIGHTS", "turn on the lights!!!", "  Turn   on  the lights  ", "please turn on the lights", "can you turn on the lights",
                                  "could you please turn on the lights", "would you turn on the lights please", "please could you turn on the lights please"])
def test_case_punctuation_and_politeness_do_not_change_a_command(text):
    r = route(text)
    assert (r.category, r.rule_id) == (IC.CLASSROOM_CONTROL, "CC.TURN_ON")


# =========================================================================================
# B. the command schema: what exists, what is rejected
# =========================================================================================

VALID_COMMANDS = [
    (IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.VOLUME_UP, None, None),
    (IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.VOLUME_DOWN, None, None),
    (IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE, None, None),
    (IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.UNMUTE, None, None),
    (IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, 0, CommandUnit.PERCENT),
    (IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, 100, CommandUnit.PERCENT),
    (IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON, None, None),
    (IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_OFF, None, None),
    (IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.TURN_ON, None, None),
    (IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.TURN_OFF, None, None),
    (IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.SET_BRIGHTNESS, 50, CommandUnit.PERCENT),
]


def cmd(category, target, action, value=None, unit=None, rid="c1", **kw):
    return {"intent_category": category, "action": action, "target": target, "value": value, "unit": unit, "request_id": rid, **kw}


@pytest.mark.parametrize("category,target,action,value,unit", VALID_COMMANDS)
def test_every_supported_command_validates_and_round_trips(category, target, action, value, unit):
    result = validate_command(cmd(category, target, action, value, unit))
    assert result.valid and result.status is CV.VALID
    c = result.command
    assert (c.intent_category, c.target, c.action, c.value, c.unit, c.request_id) == (category, target, action, value, unit, "c1")
    assert c.validation_status is CV.VALID and c.requires_acknowledgement is True
    assert validate_command(c).command == c                                  # a command object re-validates to itself


def test_the_supported_command_table_is_exactly_the_documented_set():
    assert {(c.value, t.value, a.value) for (c, t), actions in SUPPORTED_COMMANDS.items() for a in actions} == {
        (c.value, t.value, a.value) for c, t, a, _, _ in VALID_COMMANDS}
    assert set(COMMAND_CATEGORIES) == {IC.LOCAL_COMMAND, IC.CLASSROOM_CONTROL, IC.SMART_HOME}


INVALID_COMMANDS = [
    ("sensor query is not a command category", cmd(IC.SENSOR_QUERY, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE), CV.CATEGORY_MISMATCH),
    ("general ai is not a command category", cmd(IC.GENERAL_AI, CommandTarget.SMART_BULB, CommandAction.TURN_ON), CV.CATEGORY_MISMATCH),
    ("a target of another category", cmd(IC.LOCAL_COMMAND, CommandTarget.CLASSROOM_LIGHTS, CommandAction.MUTE), CV.UNSUPPORTED_TARGET),
    ("smart home on the classroom lights", cmd(IC.SMART_HOME, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON), CV.UNSUPPORTED_TARGET),
    ("classroom control on the smart bulb", cmd(IC.CLASSROOM_CONTROL, CommandTarget.SMART_BULB, CommandAction.TURN_ON), CV.UNSUPPORTED_TARGET),
    ("an action the target does not have", cmd(IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.SET_BRIGHTNESS, 50, CommandUnit.PERCENT), CV.UNSUPPORTED_ACTION),
    ("volume on the lights", cmd(IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.VOLUME_UP), CV.UNSUPPORTED_ACTION),
    ("mute on the smart bulb", cmd(IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.MUTE), CV.UNSUPPORTED_ACTION),
    ("a value where none is allowed", cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE, 5, CommandUnit.PERCENT), CV.UNEXPECTED_VALUE),
    ("a unit where none is allowed", cmd(IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON, None, CommandUnit.PERCENT), CV.UNEXPECTED_VALUE),
    ("a missing value", cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, None, CommandUnit.PERCENT), CV.MISSING_VALUE),
    ("a missing unit", cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, 50, None), CV.MISSING_UNIT),
    ("above the range", cmd(IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.SET_BRIGHTNESS, 101, CommandUnit.PERCENT), CV.VALUE_OUT_OF_RANGE),
    ("below the range", cmd(IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.SET_BRIGHTNESS, -1, CommandUnit.PERCENT), CV.VALUE_OUT_OF_RANGE),
    ("a float value", cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, 50.5, CommandUnit.PERCENT), CV.INVALID_VALUE),
    ("a string value", cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, "50", CommandUnit.PERCENT), CV.INVALID_VALUE),
    ("a boolean value", cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, True, CommandUnit.PERCENT), CV.INVALID_VALUE),
    ("a parameter nobody approved", cmd(IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON, parameters={"zone": "LEFT"}), CV.UNSUPPORTED_PARAMETER),
    ("parameters that are not a mapping", cmd(IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON, parameters=["zone"]), CV.MALFORMED),
    ("an invalid request id", cmd(IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON, rid="bad id!"), CV.INVALID_REQUEST_ID),
    ("a request id that is not a string", cmd(IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON, rid=7), CV.INVALID_REQUEST_ID),
    ("an unknown action name", {**cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE), "action": "SELF_DESTRUCT"}, CV.MALFORMED),
    ("an unknown target name", {**cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE), "target": "PROJECTOR"}, CV.MALFORMED),
    ("an unknown category name", {**cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE), "intent_category": "UNKNOWN"}, CV.MALFORMED),
    ("an unknown unit name", {**cmd(IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.SET_BRIGHTNESS, 50), "unit": "DEGREES"}, CV.INCORRECT_UNIT),
    ("an extra key (a wire-format field)", {**cmd(IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE), "gpio_pin": 4}, CV.MALFORMED),
    ("a missing required key", {"intent_category": IC.LOCAL_COMMAND, "action": CommandAction.MUTE, "request_id": "c1"}, CV.MALFORMED),
    ("not a mapping", "MUTE", CV.MALFORMED),
    ("None", None, CV.MALFORMED),
]


@pytest.mark.parametrize("name,candidate,status", INVALID_COMMANDS, ids=[c[0] for c in INVALID_COMMANDS])
def test_a_malformed_or_unsupported_command_is_rejected_with_its_reason_and_no_command(name, candidate, status):
    result = validate_command(candidate)
    assert (result.status, result.command, result.valid) == (status, None, False)


def test_names_are_accepted_as_well_as_members_in_untrusted_input():
    r = validate_command({"intent_category": "SMART_HOME", "action": "SET_BRIGHTNESS", "target": "SMART_BULB", "value": 30, "unit": "PERCENT", "request_id": "c1"})
    assert r.valid and r.command.target is CommandTarget.SMART_BULB


def test_an_invalid_structured_command_cannot_be_constructed():
    with pytest.raises(IntentContractError):
        StructuredCommand(IC.CLASSROOM_CONTROL, CommandAction.TURN_ON, CommandTarget.SMART_BULB, "c1")
    with pytest.raises(IntentContractError):
        StructuredCommand(IC.SMART_HOME, CommandAction.SET_BRIGHTNESS, CommandTarget.SMART_BULB, "c1", 500, CommandUnit.PERCENT)
    with pytest.raises(IntentContractError):
        StructuredCommand(IC.LOCAL_COMMAND, CommandAction.MUTE, CommandTarget.SPEAKER_OUTPUT, "c1", validation_status=CV.MALFORMED)
    good = StructuredCommand(IC.LOCAL_COMMAND, CommandAction.MUTE, CommandTarget.SPEAKER_OUTPUT, "c1")
    with pytest.raises(IntentContractError):
        dataclasses.replace(good, value=3)                                  # replace() re-validates: no way to edit a command into an invalid one


def test_a_command_is_immutable_and_its_parameters_are_read_only():
    c = StructuredCommand(IC.LOCAL_COMMAND, CommandAction.MUTE, CommandTarget.SPEAKER_OUTPUT, "c1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.action = CommandAction.VOLUME_UP
    with pytest.raises(TypeError):
        c.parameters["x"] = "y"


def test_a_validation_result_is_consistent():
    c = StructuredCommand(IC.LOCAL_COMMAND, CommandAction.MUTE, CommandTarget.SPEAKER_OUTPUT, "c1")
    with pytest.raises(IntentContractError):
        CommandValidationResult(CV.VALID)
    with pytest.raises(IntentContractError):
        CommandValidationResult(CV.MALFORMED, c)


def test_the_schema_contains_no_wire_protocol_field_or_hardware_detail():
    fields = {f.name for f in dataclasses.fields(StructuredCommand)}
    assert fields == {"intent_category", "action", "target", "request_id", "value", "unit", "parameters", "validation_status"}
    # the CODE (names and string values, not the docstrings that say what is deliberately absent) mentions no wire-level detail
    tokens = []
    for name in ("contracts.py", "router.py"):
        tree = ast.parse((REPO / "intents" / name).read_text(encoding="utf-8"))
        docstrings = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                tokens.append(n.id)
            elif isinstance(n, ast.Attribute):
                tokens.append(n.attr)
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                tokens.append(n.name)
            elif isinstance(n, ast.arg):
                tokens.append(n.arg)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
                tokens.append(n.value)
    joined = " ".join(tokens).lower()
    for word in ("gpio", "baud", "uart", "i2c", "pwm", "relay", "serial", "packet", "esp32"):
        assert word not in joined, word


# =========================================================================================
# C. IntentResult invariants
# =========================================================================================

def good_command(rid="r1"):
    return StructuredCommand(IC.CLASSROOM_CONTROL, CommandAction.TURN_ON, CommandTarget.CLASSROOM_LIGHTS, rid)


@pytest.mark.parametrize("kwargs", [
    dict(routing_status=RS.ROUTED, category=None, handling=H.ANSWERING_FOUNDATION),
    dict(routing_status=RS.ROUTED, category=IC.CLASSROOM_CONTROL, handling=H.EXECUTION_NOT_IMPLEMENTED),                       # no command
    dict(routing_status=RS.ROUTED, category=IC.SMART_HOME, command="good", handling=H.EXECUTION_NOT_IMPLEMENTED),               # wrong command category
    dict(routing_status=RS.ROUTED, category=IC.CLASSROOM_CONTROL, command="good", handling=H.ANSWERING_FOUNDATION),             # a command answered?
    dict(routing_status=RS.ROUTED, category=IC.SENSOR_QUERY, command="good", query_family="ROOM_TEMPERATURE", handling=H.ANSWERING_FOUNDATION),
    dict(routing_status=RS.ROUTED, category=IC.SENSOR_QUERY, handling=H.ANSWERING_FOUNDATION),                                 # no family
    dict(routing_status=RS.ROUTED, category=IC.SENSOR_QUERY, query_family="ROOM_TEMPERATURE", handling=H.NO_BACKEND_APPROVED),
    dict(routing_status=RS.ROUTED, category=IC.GENERAL_AI, handling=H.ANSWERING_FOUNDATION),
    dict(routing_status=RS.ROUTED, category=IC.GENERAL_AI, query_family="CAPABILITIES", handling=H.NO_BACKEND_APPROVED),
    dict(routing_status=RS.UNSUPPORTED, category=IC.SENSOR_QUERY),                                                              # a rejection with a category
    dict(routing_status=RS.AMBIGUOUS, command="good"),
    dict(routing_status=RS.UNSUPPORTED, handling=H.EXECUTION_NOT_IMPLEMENTED),
    dict(routing_status=RS.INVALID_COMMAND, category=IC.SENSOR_QUERY),
    dict(routing_status=RS.INVALID_COMMAND, category=IC.LOCAL_COMMAND, command="good"),
    dict(routing_status=RS.INVALID_INPUT, query_family="ROOM_TEMPERATURE"),
])
def test_an_inconsistent_intent_result_cannot_exist(kwargs):
    kwargs = dict(kwargs)
    if kwargs.get("command") == "good":
        kwargs["command"] = good_command()
    with pytest.raises(IntentContractError):
        IntentResult("r1", reason_code="x", **kwargs)


def test_nothing_in_task_1_can_claim_an_execution_or_a_hardware_label():
    assert [e.value for e in ExecutionStatus] == ["NOT_EXECUTED"]
    with pytest.raises(IntentContractError):
        IntentResult("r1", RS.UNSUPPORTED, "x", execution_status="EXECUTED")
    with pytest.raises(IntentContractError):
        IntentResult("r1", RS.UNSUPPORTED, "x", validation_label="HARDWARE-VALIDATED")
    assert route("turn on the lights").validation_label == "SOFTWARE-VALIDATED"


def test_a_request_needs_a_valid_id_and_hides_its_text_in_repr():
    with pytest.raises(IntentContractError):
        IntentRequest("hello", "bad id")
    assert SECRET not in repr(IntentRequest(SECRET, "r1"))


# =========================================================================================
# D. unsupported, ambiguous, invalid-argument and invalid-input requests: NO execution, NO answer
# =========================================================================================

UNSUPPORTED = [
    ("turn on the projector", "UNSUPPORTED_TARGET"), ("turn off the fan", "UNSUPPORTED_TARGET"), ("turn on the bedroom light", "UNSUPPORTED_TARGET"),
    ("turn on the left lights", "UNSUPPORTED_TARGET"), ("turn off the air conditioner", "UNSUPPORTED_TARGET"), ("turn on the door", "UNSUPPORTED_TARGET"),
    ("turn on the smart bulbs", "UNSUPPORTED_TARGET"), ("turn on the lights and the smart bulb", "UNSUPPORTED_TARGET"),
    ("set the classroom lights to 50 percent", "UNSUPPORTED_ACTION"), ("set the lights to 20 percent", "UNSUPPORTED_ACTION"),
    ("set the projector to 50 percent", "UNSUPPORTED_TARGET"),
    ("open the door", "NO_MATCHING_RULE"), ("play music", "NO_MATCHING_RULE"), ("pause", "NO_MATCHING_RULE"), ("stop", "NO_MATCHING_RULE"),
    ("make it brighter", "NO_MATCHING_RULE"), ("dim the lights", "NO_MATCHING_RULE"), ("volume", "NO_MATCHING_RULE"), ("lights", "NO_MATCHING_RULE"),
    ("hello", "NO_MATCHING_RULE"), ("how many people are in the corridor", "NO_MATCHING_RULE"), ("what is the temperature outside", "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI"),
    ("what is the temperature in the corridor", "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI"), ("why is it so humid", "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI"),
    ("explain how many people are in the room", "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI"), ("what is the light level in the lab", "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI"),
]
AMBIGUOUS = ["turn on the light", "turn off the light", "turn it on", "switch that off", "turn on", "switch off", "set the light to 50 percent",
             "set the light brightness to 50 percent", "set the brightness to 40 percent", "turn them off"]
INVALID_COMMANDS_TEXT = [
    ("set the volume", "MISSING_VALUE"), ("set the volume to", "MISSING_VALUE"), ("set the volume to 50", "MISSING_UNIT"),
    ("set the volume to 50 degrees", "INCORRECT_UNIT"), ("set the volume to 50 lux", "INCORRECT_UNIT"), ("set the volume to loud", "INVALID_VALUE"),
    ("set the volume to loud percent", "INVALID_VALUE"), ("set the volume to fifty percent", "INVALID_VALUE"), ("set the volume to 150 percent", "VALUE_OUT_OF_RANGE"),
    ("set the volume to 50 5 percent", "INVALID_VALUE"), ("set the volume to -5 percent", "INVALID_VALUE"), ("set the volume to 22125 percent", "INVALID_VALUE"), ("set the volume to 99999 percent", "INVALID_VALUE"),
    ("set the smart bulb brightness to 101 percent", "VALUE_OUT_OF_RANGE"), ("set the smart bulb brightness", "MISSING_VALUE"),
    ("set the smart bulb brightness to 50 degrees", "INCORRECT_UNIT"),
]


@pytest.mark.parametrize("text,reason", UNSUPPORTED)
def test_unsupported_requests_are_rejected(text, reason):
    r = route(text)
    assert (r.routing_status, r.reason_code) == (RS.UNSUPPORTED, reason)
    assert r.category is None and r.actionable_command is None and r.handling is H.NONE


@pytest.mark.parametrize("text", AMBIGUOUS)
def test_ambiguous_requests_are_rejected_not_guessed(text):
    r = route(text)
    assert r.routing_status is RS.AMBIGUOUS and r.category is None and r.command is None and r.handling is H.NONE


@pytest.mark.parametrize("text,reason", INVALID_COMMANDS_TEXT)
def test_a_recognised_command_with_a_bad_argument_is_rejected_and_names_the_reason(text, reason):
    r = route(text)
    assert (r.routing_status, r.reason_code) == (RS.INVALID_COMMAND, reason)
    assert r.category in COMMAND_CATEGORIES and r.command is None and r.actionable_command is None and r.requires_acknowledgement is False


@pytest.mark.parametrize("text", [None, 5, 3.5, b"mute", ["mute"], "", "   ", "!!!", "\n\t", "a" * 201])
def test_invalid_input_is_rejected_without_raising(text):
    r = route(text)
    assert r.routing_status is RS.INVALID_INPUT and r.category is None and r.command is None


def test_the_boundary_of_the_text_length_is_exact():
    ok = "mute " + "x" * 0
    assert route(ok).routed
    assert route("a" * 200).routing_status is RS.UNSUPPORTED and route("a" * 201).routing_status is RS.INVALID_INPUT


def test_the_volume_range_boundaries_are_inclusive():
    assert route("set the volume to 0 percent").command.value == 0
    assert route("set the volume to 100 percent").command.value == 100
    assert route("set the volume to 101 percent").reason_code == "VALUE_OUT_OF_RANGE"


def test_the_percent_sign_is_read_as_the_unit_percent():
    c = route("set the volume to 35%").command
    assert (c.value, c.unit) == (35, CommandUnit.PERCENT)


def test_a_routing_conflict_is_ambiguous_not_resolved_by_order():
    # force two different rules to match one text
    original = dict(rt._EXACT)
    try:
        rt._EXACT["mute"] = ("X.FAKE", rt._query("X.FAKE", IC.SENSOR_QUERY, "ROOM_TEMPERATURE", H.ANSWERING_FOUNDATION))
        r = route("mute")
        assert (r.routing_status, r.reason_code) == (RS.AMBIGUOUS, "CONFLICTING_RULES") and r.category is None
    finally:
        rt._EXACT.clear()
        rt._EXACT.update(original)


# =========================================================================================
# E. commands carry the right structure and are never executed or confirmed
# =========================================================================================

COMMAND_TEXTS = [
    ("volume up", IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.VOLUME_UP, None),
    ("lower the volume", IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.VOLUME_DOWN, None),
    ("mute", IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.MUTE, None),
    ("unmute", IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.UNMUTE, None),
    ("set volume to 70 percent", IC.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT, CommandAction.SET_VOLUME, 70),
    ("turn off the lights", IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_OFF, None),
    ("turn the classroom lights on", IC.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON, None),
    ("turn off the smart bulb", IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.TURN_OFF, None),
    ("set the smart light brightness to 10 percent", IC.SMART_HOME, CommandTarget.SMART_BULB, CommandAction.SET_BRIGHTNESS, 10),
]


@pytest.mark.parametrize("text,category,target,action,value", COMMAND_TEXTS)
def test_a_routed_command_has_the_right_target_action_and_value(text, category, target, action, value):
    r = route(text, "req-9")
    c = r.actionable_command
    assert (r.category, c.intent_category, c.target, c.action, c.value) == (category, category, target, action, value)
    assert c.request_id == "req-9" == r.request_id and c.unit is (CommandUnit.PERCENT if value is not None else None)
    assert r.handling is H.EXECUTION_NOT_IMPLEMENTED and r.execution_status is ExecutionStatus.NOT_EXECUTED
    assert r.requires_acknowledgement is True and r.spoken_confirmation_allowed is False
    assert validate_command(c).command == c


def test_the_classroom_lights_and_the_smart_bulb_are_never_confused():
    assert route("turn on the lights").command.target is CommandTarget.CLASSROOM_LIGHTS
    assert route("turn on the smart bulb").command.target is CommandTarget.SMART_BULB
    assert route("turn on the light").routing_status is RS.AMBIGUOUS


def test_no_command_result_ever_allows_a_spoken_success():
    for text, *_ in COMMAND_TEXTS:
        assert route(text).spoken_confirmation_allowed is False


def test_every_phrase_in_the_tables_routes_consistently_and_nothing_is_executed():
    phrases = list(rt._EXACT) + list(rt._COMMAND_EXACT)
    assert len(phrases) > 60
    for p in phrases:
        r = route(p)
        assert r.routed, p
        assert r.execution_status is ExecutionStatus.NOT_EXECUTED and r.spoken_confirmation_allowed is False
        assert (r.actionable_command is not None) == (r.category in COMMAND_CATEGORIES)
        if r.command:
            assert validate_command(r.command).valid


def test_only_a_routed_command_category_is_actionable():
    for text in [t for t, _ in UNSUPPORTED] + AMBIGUOUS + [t for t, _ in INVALID_COMMANDS_TEXT] + ["what is the temperature", "explain gravity"]:
        r = route(text)
        assert r.actionable_command is None and r.requires_acknowledgement is False, text


# =========================================================================================
# F. sensor and occupancy queries reuse the frozen answering engine
# =========================================================================================

def test_temperature_is_a_sensor_query_and_students_here_is_an_occupancy_query():
    assert route("What is the temperature?").category is IC.SENSOR_QUERY
    assert route("How many students are here?").category is IC.OCCUPANCY_QUERY


def test_expected_attendance_is_never_an_occupancy_query():
    for p in ANSWER_PHRASES[AnsweringIntent.LECTURE_EXPECTED_ATTENDANCE]:
        r = route(p)
        assert r.category is not IC.OCCUPANCY_QUERY and r.query_family == "LECTURE_EXPECTED_ATTENDANCE", p


def test_every_frozen_answering_phrase_is_routed_to_its_own_family():
    for intent, phrases in ANSWER_PHRASES.items():
        for p in phrases:
            r = route(p)
            assert r.routed and r.query_family == intent.name and r.handling is H.ANSWERING_FOUNDATION, (intent, p)


def test_the_canonical_phrases_make_the_frozen_engine_answer_the_same_family():
    engine = DeterministicAnswerEngine()
    ctx = context(caps=ROOM | {Capability.LECTURE_INFORMATION, Capability.ANNOUNCEMENTS})
    for family, phrase in CANONICAL_PHRASE.items():
        assert engine.answer(__import__("assistant.answering", fromlist=["AnswerRequest"]).AnswerRequest(phrase, ctx)).intent.name == family


@pytest.mark.parametrize("text,fragment", [
    ("What is the temperature?", "22.5"), ("what is the humidity", "45"), ("how bright is it", "320"), ("How many students are here?", "17"),
    ("how many students are in the room", "17"), ("what's the temperature", "22.5"),
])
def test_routed_queries_are_answered_from_the_simulated_state_by_the_frozen_engine(text, fragment):
    a = answer_query(route(text), context())
    assert a.status is AnswerStatus.ANSWERED and fragment in a.text and a.source.value == "SIMULATED"


@pytest.mark.parametrize("text", ["What is the temperature?", "what is the humidity", "how bright is it", "How many students are here?"])
def test_a_missing_classroom_state_is_unavailable_and_nothing_is_invented(text):
    a = answer_query(route(text), context(state=None))
    assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE and a.reason_code == "NO_CLASSROOM_STATE" and not any(ch.isdigit() for ch in a.text)


@pytest.mark.parametrize("text,kw", [
    ("What is the temperature?", dict(temp=None)), ("what is the humidity", dict(humidity=None)), ("how bright is it", dict(lux=None)),
    ("How many students are here?", dict(people=None)),
])
def test_a_missing_sensor_is_unavailable(text, kw):
    a = answer_query(route(text), context(state=room_state(**kw)))
    assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE and a.reason_code == "NO_FRESH_VALID_VALUE"


@pytest.mark.parametrize("text", ["What is the temperature?", "what is the humidity", "how bright is it", "How many students are here?"])
def test_a_stale_reading_is_unavailable_even_though_the_number_exists(text):
    a = answer_query(route(text), context(state=room_state(t=50.0)))            # every reading 50 s old against a 10 s limit
    assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE and not any(ch.isdigit() for ch in a.text)


@pytest.mark.parametrize("text", ["What is the temperature?", "how many students are here"])
def test_an_unconfigured_capability_is_not_answered(text):
    a = answer_query(route(text), context(caps=()))
    assert a.status is AnswerStatus.UNSUPPORTED_QUESTION and a.reason_code == "CAPABILITY_NOT_DECLARED"


def test_the_detected_count_is_never_replaced_by_the_expected_attendance():
    from assistant.answering import SimulatedScheduleContext
    ctx = AnswerContext(classroom_state=None, capabilities=CapabilityDeclaration(frozenset(ROOM | {Capability.LECTURE_INFORMATION})),
                        schedule=SimulatedScheduleContext(expected_attendance=30))
    people = answer_query(route("how many students are here"), ctx)
    expected = answer_query(route("how many students are expected"), ctx)
    assert people.status is AnswerStatus.INFORMATION_UNAVAILABLE and "30" not in people.text
    assert expected.status is AnswerStatus.ANSWERED and "30" in expected.text


def test_professor_arrival_stays_unavailable():
    a = answer_query(route("has the professor arrived yet"), context(caps=ROOM | {Capability.LECTURE_INFORMATION}))
    assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE and a.reason_code == "NO_AUTHORITATIVE_ARRIVAL_STATE"


class _SpyEngine(AnswerEngine):
    def __init__(self):
        self.seen = []

    def _answer(self, text, context):
        self.seen.append((text, context))
        return DeterministicAnswerEngine()._answer(text, context)


def test_answer_query_delegates_the_canonical_phrase_and_the_unchanged_context():
    spy, ctx = _SpyEngine(), context()
    answer_query(route("What is the temperature?"), ctx, spy)
    assert spy.seen == [(CANONICAL_PHRASE["ROOM_TEMPERATURE"], ctx)] and ctx.classroom_state is not None


@pytest.mark.parametrize("text", ["turn on the lights", "mute", "set the smart bulb brightness to 50 percent", "is the room dark", "is anyone in the room",
                                  "explain machine learning", "turn on the projector", "turn on the light", "set the volume to 50", ""])
def test_nothing_but_an_answerable_query_can_reach_the_answering_engine(text):
    spy = _SpyEngine()
    with pytest.raises(IntentContractError):
        answer_query(route(text), context(), spy)
    assert spy.seen == []


def test_a_non_result_cannot_be_answered():
    with pytest.raises(IntentContractError):
        answer_query("what is the temperature", context())


# =========================================================================================
# G. GENERAL_AI: classified, never answered; availability reported separately
# =========================================================================================

def test_general_ai_classifies_successfully_but_reports_that_no_backend_is_approved():
    for text in ("explain machine learning", "what is machine learning", "tell me a joke", "help me study", "who was Alan Turing", "why is the sky blue", "describe a neural network"):
        r = route(text)
        assert r.category is IC.GENERAL_AI and r.routed and r.handling is H.NO_BACKEND_APPROVED and r.query_family is None, text
        with pytest.raises(IntentContractError):
            answer_query(r, context())                                   # no answer is fabricated


def test_general_ai_never_swallows_a_sensor_or_occupancy_question():
    for text in ("what is the temperature in the corridor", "what is the humidity in here", "how many students are in the lab", "what is the brightness of the sun",
                 "why is it dark in here", "why is the room so warm"):
        assert route(text).category is not IC.GENERAL_AI, text


def test_general_ai_with_a_deterministic_family_is_answered_by_the_frozen_engine_only():
    r = route("what can you do")
    assert (r.category, r.handling, r.query_family) == (IC.GENERAL_AI, H.ANSWERING_FOUNDATION, "CAPABILITIES")
    a = answer_query(r, context(caps=ROOM | {Capability.ANNOUNCEMENTS}))
    assert a.status is AnswerStatus.ANSWERED and a.source.value == "DECLARATION"


def test_the_not_yet_answerable_queries_are_classified_but_not_answered():
    for text in ("is the room dark", "is anyone in the room"):
        r = route(text)
        assert r.routed and r.handling is H.ANSWER_NOT_IMPLEMENTED
        with pytest.raises(IntentContractError):
            answer_query(r, context())


# =========================================================================================
# H. privacy, determinism, boundaries
# =========================================================================================

def test_no_request_text_reaches_a_log_or_a_result(logs):
    results = [route(f"turn on the {SECRET}"), route(f"explain {SECRET}"), route(f"set the volume to {SECRET} percent"), route(SECRET)]
    blob = repr(results) + "\n".join(logs)
    assert SECRET not in blob and "marmalade" not in blob and "zebra" not in blob
    assert any("event=intent_routed" in l for l in logs) and all("input_chars=" in l for l in logs)


def test_routing_is_deterministic_and_does_not_depend_on_the_request_id():
    texts = [t for t, *_ in ROUTED_CASES] + [t for t, _ in UNSUPPORTED] + AMBIGUOUS
    first = [(r.routing_status, r.category, r.reason_code, r.rule_id, r.handling) for r in (route(t, "a1") for t in texts)]
    second = [(r.routing_status, r.category, r.reason_code, r.rule_id, r.handling) for r in (route(t, "zz9") for t in texts)]
    assert first == second


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_intent_package_imports_no_device_network_or_model_library():
    forbidden = {"sounddevice", "serial", "socket", "urllib", "requests", "http", "openai", "onnxruntime", "faster_whisper", "sherpa_onnx", "openwakeword",
                 "audio.microphone", "audio.playback", "speech.pipeline", "assistant.runtime", "assistant.output_arbiter", "assistant.state"}
    for path in (REPO / "intents").glob("*.py"):
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_the_frozen_layers_do_not_depend_on_the_new_package():
    """The pipeline, TTS, playback, the arbiter, the state manager and the answering engine never import the intent package. (Week 8 Task 3
    deliberately made assistant/runtime.py its ONE consumer; tests/test_runtime_routing.py pins that, so it is no longer in this list.)"""
    for rel in ("assistant/answering.py", "assistant/output_arbiter.py", "assistant/state.py", "assistant/spoken_output.py",
                "speech/pipeline.py", "audio/playback.py", "speech/tts.py"):
        assert not any(n == "intents" or n.startswith("intents.") for n in _imports(REPO / rel)), rel


def test_the_handling_gate_alone_keeps_an_unanswerable_query_away_from_the_engine(monkeypatch):
    """Defence in depth: even if a canonical phrase existed for a not-yet-answerable family, answer_query() would still refuse it."""
    monkeypatch.setitem(CANONICAL_PHRASE, "ROOM_DARK", CANONICAL_PHRASE["ROOM_TEMPERATURE"])
    monkeypatch.setitem(CANONICAL_PHRASE, "ROOM_PRESENCE", CANONICAL_PHRASE["ROOM_PEOPLE_COUNT"])
    spy = _SpyEngine()
    for text in ("is the room dark", "is anyone in the room"):
        with pytest.raises(IntentContractError):
            answer_query(route(text), context(), spy)
    assert spy.seen == []
