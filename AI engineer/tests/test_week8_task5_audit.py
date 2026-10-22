"""Week 8 Task 5: structured command semantics, the final router safety audit and the software closeout checks.

SOFTWARE-VALIDATED. Nothing here touches hardware: no ESP32, relay, speaker, bulb, camera or microphone is used, and nothing is executed. The
documentation checks compare docs/contracts/WEEK8_COMMAND_SEMANTICS_V1.md with the real source so the contract cannot drift. The proposed future
interface (stages D-F, requirements R1-R14) has NO executable behaviour; it is tested only as documentation consistency.
"""

import ast
import dataclasses
import hashlib
import json
import logging
import re
from pathlib import Path

import numpy as np
import pytest

from assistant import runtime as rtm
from assistant.answering import AnswerStatus
from assistant.runtime import COMMAND_NOT_EXECUTED_TEXT, NOT_SUPPORTED_TEXT, OutcomeKind
from evaluation import week8_intent_eval as ev
from evaluation import week8_router_history as history
from intents import contracts as ct
from intents import router as rt
from intents.contracts import (
    COMMAND_CATEGORIES, CommandAction, CommandTarget, CommandUnit, CommandValidationStatus, ExecutionStatus, Handling, IntentCategory, IntentContractError,
    IntentRequest, IntentResult, RoutingStatus, StructuredCommand, validate_command,
)
from tests.runtime_support import UTTERANCE, Rig, SpyEngine

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "contracts" / "WEEK8_COMMAND_SEMANTICS_V1.md"
FIXTURES = ev.simulated_answer_fixtures()
SECRET = "zebra quartz marmalade"


def r(text, rid="t5"):
    return rt.route_intent(IntentRequest(text, rid))


def sig(x):
    return (x.routing_status.value, x.category.value if x.category else None, x.reason_code, x.query_family, x.handling.value,
            (x.command.action.value, x.command.target.value, x.command.value, x.command.unit) if x.command else None)


# =========================================================================================
# A. the class-event guard (the two known gaps) and its limits
# =========================================================================================

UNSAFE_CLASS_EVENTS = [
    "explain why the professor has not arrived", "why does the classroom attendance look low", "why is the professor late", "why hasn't the professor arrived",
    "explain why the lecturer is absent today", "why is the professor not here", "why was the professor late this morning", "explain why the teacher has not arrived",
    "why hasn't the instructor shown up", "why is attendance low today", "why is attendance low in this class", "why is our attendance dropping",
    "why is the attendance so low in the classroom", "explain why the attendance is low", "why are there so few students in class today",
    "why is the class empty", "explain why the classroom is empty", "why are students absent today", "describe why my professor is late",
]


@pytest.mark.parametrize("text", UNSAFE_CLASS_EVENTS)
def test_a_causal_question_about_the_actual_professor_or_this_class_is_refused_not_classified_as_general_ai(text):
    x = r(text)
    assert x.routing_status is RoutingStatus.UNSUPPORTED and x.reason_code == "CLASS_EVENT_NOT_ROUTABLE_TO_GENERAL_AI" and x.rule_id == "GEN.CLASS_EVENT_GUARD"
    assert x.category is None and x.command is None and x.query_family is None and x.handling is Handling.NONE and not x.routed
    assert x.execution_status is ExecutionStatus.NOT_EXECUTED and x.spoken_confirmation_allowed is False
    with pytest.raises(IntentContractError):
        rt.answer_query(x, FIXTURES["SCHEDULE_AND_FRESH"])                       # nothing is answered, nothing is explained


BENIGN_EDUCATIONAL = [
    "Explain why university professors can be late.", "Explain the difference between attendance and occupancy.", "why do professors arrive late",
    "explain why class attendance matters", "explain what attendance means in a university", "what is attendance", "explain how attendance is recorded",
    "explain the attendance policy at universities", "explain why the professor is important", "why do students skip lectures", "explain why attendance is low",
    "explain why professors relate to students", "explain how a professor translates a lecture", "explain what a lecturer does", "describe the role of a teacher",
    "why do students need professors", "who was the first professor of physics", "explain how many students attend university", "explain why students are absent from school",
    "tell me about the professor role", "explain gravity", "what is machine learning", "tell me a joke", "help me study",
    "explain voter turnout", "why is voter turnout low", "what is the turnout rate in elections", "explain why few people attend lectures in general",
]


@pytest.mark.parametrize("text", BENIGN_EDUCATIONAL)
def test_ordinary_educational_questions_about_professors_and_attendance_stay_general_ai_without_a_backend(text):
    x = r(text)
    assert x.routing_status is RoutingStatus.ROUTED and x.category is IntentCategory.GENERAL_AI and x.handling is Handling.NO_BACKEND_APPROVED
    assert x.query_family is None and x.command is None
    with pytest.raises(IntentContractError):
        rt.answer_query(x, FIXTURES["FRESH"])                                    # classified, never answered


def test_the_known_conservative_false_rejections_are_documented_and_pinned():
    # A closed word-list guard cannot tell a generic use of "the professor" from a specific one; it refuses (safe) rather than guesses.
    assert r("explain why the professor can be late").reason_code == "CLASS_EVENT_NOT_ROUTABLE_TO_GENERAL_AI"
    # "what is the ..." is deliberately not an educational frame, so the older sensor-word guard still refuses this one (occupancy is a sensor word).
    assert r("what is the difference between attendance and occupancy").reason_code == "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI"


def test_the_guard_only_acts_where_nothing_more_specific_matched():
    for text, family in (("has the professor arrived yet", "PROFESSOR_ARRIVAL"), ("is the professor here yet", "PROFESSOR_ARRIVAL"), ("has the lecturer arrived", "PROFESSOR_ARRIVAL"),
                         ("how many students are expected", "LECTURE_EXPECTED_ATTENDANCE"), ("how many students should be here", "LECTURE_EXPECTED_ATTENDANCE")):
        x = r(text)
        assert x.routed and x.query_family == family and x.handling is Handling.ANSWERING_FOUNDATION, text
    for text in ("turn on the lights", "mute", "set the volume to 40 percent", "what is the temperature right now"):
        assert r(text).reason_code != "CLASS_EVENT_NOT_ROUTABLE_TO_GENERAL_AI"


def test_the_guard_never_changes_a_corpus_item_whose_gold_is_routed():
    for path in (ev.DEFAULT_MANIFEST, ev.V2_MANIFEST):
        for e in ev.load_manifest(path).entries:
            x = r(e.text)
            if x.reason_code == "CLASS_EVENT_NOT_ROUTABLE_TO_GENERAL_AI":
                assert e.expected_outcome == "UNSUPPORTED", e.id


def test_the_guard_is_deterministic_and_stateless():
    a = [sig(r(t)) for t in UNSAFE_CLASS_EVENTS + BENIGN_EDUCATIONAL]
    assert a == [sig(r(t)) for t in UNSAFE_CLASS_EVENTS + BENIGN_EDUCATIONAL]


# =========================================================================================
# B. before / after: the Task 4 router is rebuilt (byte-verified) and compared on every frozen item
# =========================================================================================

def test_the_task4_router_is_rebuilt_byte_identically_from_the_current_router():
    src = history.task4_router_source()
    assert hashlib.sha256(src.encode("utf-8")).hexdigest() == history.TASK4_ROUTER_SHA256 == "6d9c5d30c9cd470f22062bb04bb02f8b95382390fae83712d251392b3b2485ca"
    doc = json.loads(history.EDIT_SCRIPT.read_text(encoding="utf-8"))
    assert doc["task5_sha256"] == hashlib.sha256((REPO / "intents" / "router.py").read_bytes()).hexdigest() and len(doc["edits"]) == 3


def test_the_rebuild_is_refused_if_the_edit_script_or_the_router_do_not_match(tmp_path, monkeypatch):
    doc = json.loads(history.EDIT_SCRIPT.read_text(encoding="utf-8"))
    bad_hash = tmp_path / "a.json"
    bad_hash.write_text(json.dumps({**doc, "task5_sha256": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(history, "EDIT_SCRIPT", bad_hash)
    with pytest.raises(history.RouterHistoryError):
        history.task4_router_source()
    bad_edit = tmp_path / "b.json"
    edits = [dict(e) for e in doc["edits"]]
    edits[1] = {"old": edits[1]["old"] + " ", "new": edits[1]["new"]}                       # an edit whose 'old' text is altered
    bad_edit.write_text(json.dumps({**doc, "edits": edits}), encoding="utf-8")
    monkeypatch.setattr(history, "EDIT_SCRIPT", bad_edit)
    with pytest.raises(history.RouterHistoryError, match="byte-identical"):
        history.task4_router_source()
    bad_task4 = tmp_path / "c.json"
    bad_task4.write_text(json.dumps({**doc, "task4_sha256": "1" * 64}), encoding="utf-8")
    monkeypatch.setattr(history, "EDIT_SCRIPT", bad_task4)
    with pytest.raises(history.RouterHistoryError):
        history.task4_router_source()


def test_the_task4_baseline_still_has_the_two_known_gaps_and_the_current_router_does_not():
    old = history.load_task4_router()
    for text in ("explain why the professor has not arrived", "why does the classroom attendance look low"):
        before, after = old(IntentRequest(text, "b")), r(text)
        assert (before.routing_status, before.handling) == (RoutingStatus.ROUTED, Handling.NO_BACKEND_APPROVED)
        assert (after.routing_status, after.reason_code) == (RoutingStatus.UNSUPPORTED, "CLASS_EVENT_NOT_ROUTABLE_TO_GENERAL_AI")
    assert history.load_task4_router_module() is not rt and history.load_task4_router_module().__file__.startswith("<")      # in memory, never the production module


def test_exactly_the_two_known_gap_items_changed_across_all_453_frozen_items_and_the_30_ai1_phrases():
    old = history.load_task4_router()
    changed, total = [], 0
    for path in (ev.DEFAULT_MANIFEST, ev.V2_MANIFEST):
        for e in ev.load_manifest(path).entries:
            total += 1
            if sig(old(IntentRequest(e.text, "c"))) != sig(r(e.text)):
                changed.append(e.id)
    assert total == 453 and changed == ["K-001", "K-002"]
    oracle = json.loads((REPO / "evaluation" / "ai_question_test_set_v1.json").read_text(encoding="utf-8"))
    phrases = [p for q in oracle["questions"] for p in [q["question"]] + list(q["accepted_variants"])]
    assert len(phrases) == 30 and all(sig(old(IntentRequest(p, "c"))) == sig(r(p)) for p in phrases)


def test_the_three_routers_on_the_two_frozen_corpora_have_the_documented_numbers():
    rts = history.routers()
    v1, v2 = ev.load_manifest(ev.DEFAULT_MANIFEST), ev.load_manifest(ev.V2_MANIFEST)

    def run(manifest, name):
        return ev.evaluate(manifest, route=rts[name], router_name=name)["strata"]["ALL"]

    def pair(a, key):
        return (a[key]["numerator"], a[key]["denominator"])

    expected = {("v1", "task1-frozen"): ((158, 183), (255, 280)), ("v1", "task4-baseline"): ((174, 183), (271, 280)), ("v1", "task5-current"): ((174, 183), (271, 280)),
                ("v2", "task4-baseline"): ((109, 109), (171, 173)), ("v2", "task5-current"): ((109, 109), (173, 173))}
    for (corpus, name), (cat, out) in expected.items():
        a = run(v1 if corpus == "v1" else v2, name)
        assert (pair(a, "category_accuracy"), pair(a, "routing_outcome_agreement")) == (cat, out), (corpus, name)
        cs = a["command_safety"]
        assert cs["false_command_acceptance"]["numerator"] == 0 and cs["wrong_command_accepted"]["numerator"] == 0 and cs["invariant_violations"]["count"] == 0
        assert a["answer_check"]["violations"]["count"] == 0
    cur = run(v2, "task5-current")
    assert cur["mismatches"] == [] and cur["per_category"]["GENERAL_AI"]["precision"]["numerator"] == cur["per_category"]["GENERAL_AI"]["precision"]["denominator"] == 20
    assert run(v1, "task4-baseline")["command_safety"]["exact_command_agreement_of_gold_commands"]["numerator"] == 56


# =========================================================================================
# C. command family matrix, argument validity, targets, ambiguity, multiple commands
# =========================================================================================

LC, CC, SH = IntentCategory.LOCAL_COMMAND, IntentCategory.CLASSROOM_CONTROL, IntentCategory.SMART_HOME
SP, CL, SB = CommandTarget.SPEAKER_OUTPUT, CommandTarget.CLASSROOM_LIGHTS, CommandTarget.SMART_BULB
A = CommandAction
MATRIX = [
    ("volume up", LC, A.VOLUME_UP, SP, None), ("turn the sound up", LC, A.VOLUME_UP, SP, None), ("please raise the volume", LC, A.VOLUME_UP, SP, None),
    ("volume down", LC, A.VOLUME_DOWN, SP, None), ("lower the volume", LC, A.VOLUME_DOWN, SP, None), ("could you turn down the sound", LC, A.VOLUME_DOWN, SP, None),
    ("mute", LC, A.MUTE, SP, None), ("mute the speaker", LC, A.MUTE, SP, None), ("unmute", LC, A.UNMUTE, SP, None), ("Unmute the speaker.", LC, A.UNMUTE, SP, None),
    ("set the volume to 0 percent", LC, A.SET_VOLUME, SP, 0), ("set the volume to 100 percent", LC, A.SET_VOLUME, SP, 100),
    ("set the speaker volume to 35%", LC, A.SET_VOLUME, SP, 35), ("set volume to 60 percent", LC, A.SET_VOLUME, SP, 60),
    ("turn on the lights", CC, A.TURN_ON, CL, None), ("turn off the classroom lights", CC, A.TURN_OFF, CL, None), ("turn the lights on", CC, A.TURN_ON, CL, None),
    ("switch the lights off", CC, A.TURN_OFF, CL, None), ("lights on", CC, A.TURN_ON, CL, None),
    ("turn on the smart bulb", SH, A.TURN_ON, SB, None), ("turn off the smart light", SH, A.TURN_OFF, SB, None), ("turn the smart bulb on", SH, A.TURN_ON, SB, None),
    ("set the smart bulb to 0 percent", SH, A.SET_BRIGHTNESS, SB, 0), ("set the smart bulb brightness to 30 percent", SH, A.SET_BRIGHTNESS, SB, 30),
    ("set the smart light brightness to 100%", SH, A.SET_BRIGHTNESS, SB, 100),
]


@pytest.mark.parametrize("text,cat,action,target,value", MATRIX)
def test_every_supported_command_family_yields_the_exact_validated_command_and_is_never_executed(text, cat, action, target, value):
    x = r(text)
    c = x.command
    assert x.routed and x.category is cat and x.handling is Handling.EXECUTION_NOT_IMPLEMENTED and x.query_family is None
    assert (c.intent_category, c.action, c.target, c.value) == (cat, action, target, value)
    assert c.unit is (CommandUnit.PERCENT if value is not None else None) and not c.parameters and c.validation_status is CommandValidationStatus.VALID
    assert c.request_id == x.request_id == "t5" and c.requires_acknowledgement is True
    assert x.execution_status is ExecutionStatus.NOT_EXECUTED and x.spoken_confirmation_allowed is False and x.actionable_command is c
    assert validate_command(c).valid


def test_the_matrix_reaches_every_entry_of_the_supported_command_table_and_nothing_else():
    reached = {(r(t).command.intent_category, r(t).command.target, r(t).command.action) for t, *_ in MATRIX}
    table = {(cat, target, action) for (cat, target), actions in ct.SUPPORTED_COMMANDS.items() for action in actions}
    assert reached == table and len(table) == 10
    for phrase in rt._COMMAND_EXACT:                                       # every table phrase builds a command that is in the table
        c = r(phrase).command
        assert c.action in ct.SUPPORTED_COMMANDS[(c.intent_category, c.target)]


@pytest.mark.parametrize("text,reason", [
    ("set the volume to 101 percent", "VALUE_OUT_OF_RANGE"), ("set the speaker volume to 150 percent", "VALUE_OUT_OF_RANGE"), ("set the volume to -5 percent", "INVALID_VALUE"),
    ("set the volume to -1%", "INVALID_VALUE"), ("set the volume to 12.5 percent", "INVALID_VALUE"), ("set the volume to fifty percent", "INVALID_VALUE"),
    ("set the volume to 99999 percent", "INVALID_VALUE"), ("set the volume to 1,000 percent", "INVALID_VALUE"), ("set the volume to 50", "MISSING_UNIT"),
    ("set the volume to 50 degrees", "INCORRECT_UNIT"), ("set the volume", "MISSING_VALUE"), ("set the smart bulb to 101 percent", "VALUE_OUT_OF_RANGE"),
    ("set the smart bulb brightness to -20 percent", "INVALID_VALUE"), ("set the smart bulb brightness to 12.5 percent", "INVALID_VALUE"), ("set the smart bulb to 50", "MISSING_UNIT"),
])
def test_invalid_numeric_values_are_rejected_with_no_command(text, reason):
    x = r(text)
    assert x.routing_status is RoutingStatus.INVALID_COMMAND and x.reason_code == reason and x.command is None and x.category in COMMAND_CATEGORIES
    assert x.handling is Handling.NONE and not x.routed and x.actionable_command is None


@pytest.mark.parametrize("text", ["turn on the projector", "turn off the fan", "set the fan to 50 percent", "set the projector to 50 percent", "set the speaker to 50 percent",
                                  "turn on the bedroom light", "turn on the left lights", "open the door", "turn the sound off", "set the lights to 50 percent",
                                  "set the classroom lights brightness to 50 percent", "turn on the air conditioner"])
def test_unsupported_device_targets_and_actions_are_rejected(text):
    x = r(text)
    assert x.routing_status is RoutingStatus.UNSUPPORTED and x.command is None and x.category is None and not x.routed


@pytest.mark.parametrize("text", ["turn on the light", "turn it on", "turn on", "turn off that", "set the light to 50 percent", "set brightness to 50 percent"])
def test_ambiguous_commands_are_rejected_not_guessed(text):
    x = r(text)
    assert x.routing_status is RoutingStatus.AMBIGUOUS and x.command is None and x.category is None


@pytest.mark.parametrize("text", ["turn on the lights and the smart bulb", "turn on the lights and mute", "mute and turn on the lights", "volume up and down", "mute and unmute",
                                  "turn the lights on and off", "volume up volume up", "mute mute", "turn on the lights turn on the lights",
                                  "turn the sound up and turn the sound down", "set the volume to 50 percent and mute", "turn on the lights and turn off the smart bulb"])
def test_multiple_or_conflicting_actions_never_produce_a_command(text):
    x = r(text)
    assert not x.routed and x.command is None and x.actionable_command is None and x.routing_status is not RoutingStatus.ROUTED


def test_the_command_validator_refuses_everything_outside_the_table():
    base = {"intent_category": "CLASSROOM_CONTROL", "action": "TURN_ON", "target": "CLASSROOM_LIGHTS", "request_id": "c1"}
    assert validate_command(base).valid
    bad = [
        ({**base, "action": "SET_BRIGHTNESS", "value": 50, "unit": "PERCENT"}, CommandValidationStatus.UNSUPPORTED_ACTION),       # classroom lights have no brightness
        ({**base, "value": 5}, CommandValidationStatus.UNEXPECTED_VALUE), ({**base, "parameters": {"zone": "LEFT"}}, CommandValidationStatus.UNSUPPORTED_PARAMETER),
        ({**base, "target": "SMART_BULB"}, CommandValidationStatus.UNSUPPORTED_TARGET), ({**base, "intent_category": "GENERAL_AI"}, CommandValidationStatus.CATEGORY_MISMATCH),
        ({**base, "request_id": "has space"}, CommandValidationStatus.INVALID_REQUEST_ID),
        ({"intent_category": "LOCAL_COMMAND", "action": "SET_VOLUME", "target": "SPEAKER_OUTPUT", "request_id": "c1", "value": True, "unit": "PERCENT"}, CommandValidationStatus.INVALID_VALUE),
        ({"intent_category": "LOCAL_COMMAND", "action": "SET_VOLUME", "target": "SPEAKER_OUTPUT", "request_id": "c1", "value": 12.5, "unit": "PERCENT"}, CommandValidationStatus.INVALID_VALUE),
        ({"intent_category": "LOCAL_COMMAND", "action": "SET_VOLUME", "target": "SPEAKER_OUTPUT", "request_id": "c1", "value": -1, "unit": "PERCENT"}, CommandValidationStatus.VALUE_OUT_OF_RANGE),
        ({"intent_category": "LOCAL_COMMAND", "action": "SET_VOLUME", "target": "SPEAKER_OUTPUT", "request_id": "c1", "value": 5}, CommandValidationStatus.MISSING_UNIT),
    ]
    for candidate, status in bad:
        res = validate_command(candidate)
        assert res.status is status and res.command is None, candidate
    assert validate_command({**base, "extra": 1}).status is CommandValidationStatus.MALFORMED


# =========================================================================================
# D. no false hardware success, no executor, duplicate semantics
# =========================================================================================

def test_a_hardware_status_cannot_be_promoted_to_success_in_the_contract():
    assert {m.value for m in ExecutionStatus} == {"NOT_EXECUTED"}
    for attempt in ("EXECUTED", "SUCCESS", "ACKNOWLEDGED", "APPLIED"):
        with pytest.raises((IntentContractError, TypeError, ValueError, AttributeError)):
            IntentResult("x1", RoutingStatus.UNSUPPORTED, "NO_MATCHING_RULE", "NONE", execution_status=attempt)
    c = r("turn on the lights").command
    with pytest.raises(IntentContractError):
        StructuredCommand(c.intent_category, c.action, c.target, c.request_id, validation_status=CommandValidationStatus.MALFORMED)
    with pytest.raises(AttributeError):
        r("turn on the lights").spoken_confirmation_allowed = True                          # frozen result, read-only property
    assert not any(hasattr(StructuredCommand, n) for n in ("execute", "execution_result", "acknowledged", "succeeded"))


def test_every_command_produced_anywhere_in_the_frozen_corpora_is_not_executed_and_never_confirmed():
    n = 0
    for path in (ev.DEFAULT_MANIFEST, ev.V2_MANIFEST):
        for e in ev.load_manifest(path).entries:
            x = r(e.text)
            assert x.execution_status is ExecutionStatus.NOT_EXECUTED and x.spoken_confirmation_allowed is False, e.id
            n += x.command is not None
    assert n > 60


def test_no_production_intent_or_runtime_module_imports_any_executor_transport_or_hardware_library():
    banned = {"serial", "socket", "socketserver", "http", "urllib", "requests", "httpx", "aiohttp", "websockets", "smbus", "smbus2", "gpiozero", "RPi", "spidev",
              "pyserial", "bluetooth", "paho", "asyncio", "subprocess", "usb"}
    for path in [*sorted((REPO / "intents").glob("*.py")), REPO / "assistant" / "runtime.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert name.split(".")[0] not in banned, (path.name, name)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                assert not re.search(r"(?i)^(execute|send_command|dispatch|actuate)", node.name), (path.name, node.name)


def test_duplicate_requests_are_independent_pure_classifications_with_no_hidden_deduplication():
    a, b = r("turn on the lights", "dup-1"), r("turn on the lights", "dup-2")
    assert sig(a) == sig(b) and a.command is not b.command and (a.request_id, b.request_id) == ("dup-1", "dup-2")
    assert r("turn on the lights", "same") == r("turn on the lights", "same")                # same id twice: identical, and nothing remembers the first
    assert not [n for n in vars(rt) if re.search(r"(?i)(seen|cache|dedup|pending|queue)", n) and not n.startswith("__")]


# =========================================================================================
# E. query data safety (the ten answering families)
# =========================================================================================

def ans(text, fixture):
    return rt.answer_query(r(text), FIXTURES[fixture])


@pytest.mark.parametrize("text,fixture", [
    ("what is the temperature", "STALE"), ("what is the temperature", "NONE"), ("what is the temperature", "NO_TEMPERATURE"),
    ("what is the humidity", "STALE"), ("what is the humidity", "NO_HUMIDITY"), ("how bright is the room", "STALE"), ("how bright is the room", "NO_LUX"),
    ("how many students are here", "STALE"), ("how many students are here", "NO_VISION"), ("how many students are here", "NONE"),
    ("what is the current humidity", "NONE"), ("how hot is it in the room", "STALE"), ("how many students are in class", "NO_VISION"),
])
def test_missing_or_stale_sensor_data_is_unavailable_and_contains_no_number(text, fixture):
    a = ans(text, fixture)
    assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE and a.facts == () and not any(ch.isdigit() for ch in a.text)


def test_undeclared_capabilities_are_unsupported_not_answered():
    for text in ("what is the temperature", "what is the humidity", "how bright is the room", "how many students are here"):
        assert ans(text, "UNCONFIGURED").status is AnswerStatus.UNSUPPORTED_QUESTION


def test_expected_attendance_is_never_the_observed_count_and_the_observed_count_is_never_the_expectation():
    seen = ans("how many students are in class", "SCHEDULE_NO_STATE")
    assert seen.status is AnswerStatus.INFORMATION_UNAVAILABLE and "30" not in seen.text
    expected = ans("how many students are expected", "FRESH")
    assert expected.status is AnswerStatus.INFORMATION_UNAVAILABLE and "17" not in expected.text
    both_seen, both_exp = ans("how many students are in class", "SCHEDULE_AND_FRESH"), ans("how many students are expected", "SCHEDULE_AND_FRESH")
    assert "17" in both_seen.text and "30" not in both_seen.text and "30" in both_exp.text and "17" not in both_exp.text
    assert {f.name for f in both_seen.facts} == {"people_count"} and {f.name for f in both_exp.facts} == {"expected_attendance"}


def test_professor_presence_never_implies_arrival_in_any_fixture():
    for fixture in FIXTURES:
        a = ans("has the professor arrived yet", fixture)
        assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE and a.reason_code == "NO_AUTHORITATIVE_ARRIVAL_STATE" and a.facts == (), fixture
    for text in ("is the professor in the room", "is the professor present"):
        assert r(text).query_family != "PROFESSOR_ARRIVAL" and not r(text).routed


def test_no_schedule_data_is_invented_without_a_schedule_source():
    for text in ("what lecture is scheduled now", "how many students are expected", "when is the next class", "when does the next class start"):
        for fixture in ("NONE", "FRESH", "STALE"):
            a = ans(text, fixture)
            assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE and a.reason_code == "NO_SCHEDULE_SOURCE" and not any(ch.isdigit() for ch in a.text), (text, fixture)
    assert ans("when does the next class start", "SCHEDULE_NO_STATE").source.value == "SIMULATED"      # a fixture is always labelled SIMULATED


def test_capability_answers_follow_the_declaration_and_invent_no_speaker_or_camera():
    empty = ans("what can you do", "UNCONFIGURED").text
    assert "no functions are enabled" in empty
    everything = ans("what can you do", "NONE").text
    for word in ("camera", "speaker", "microphone", "projector", "fan", "lights", "bulb", "volume"):
        assert word not in everything.lower() and word not in empty.lower(), word
    assert "not available" in ans("can you make an announcement", "UNCONFIGURED").text and "Yes" in ans("can you make an announcement", "NONE").text


# =========================================================================================
# F. runtime behaviour with fake input / output
# =========================================================================================

def say(text, **kw):
    return Rig(replies=(f"hey jarvis {text}",), **kw)


def run(text, fixture="NONE", **kw):
    rig = say(text, context_provider=lambda: FIXTURES[fixture], **kw)
    (outcome,) = rig.drive(UTTERANCE)
    return rig, outcome


@pytest.mark.parametrize("text", UNSAFE_CLASS_EVENTS[:6])
def test_the_runtime_refuses_a_class_event_explanation_with_a_fixed_reply_and_asks_nobody(text):
    engine = SpyEngine()
    rig, o = run(text, "SCHEDULE_AND_FRESH", answer_engine=engine)
    assert (o.kind, o.routing_status, o.command, o.intent_category, o.handling) == (OutcomeKind.REQUEST_REJECTED, "UNSUPPORTED", None, None, "NONE")
    assert rig.tts.texts == [NOT_SUPPORTED_TEXT] and engine.calls == 0 and o.execution_status == "NOT_EXECUTED" and o.spoken_confirmation_allowed is False


def test_the_runtime_returns_every_command_family_not_executed_with_only_the_not_done_notice():
    spoken = set()
    for text, cat, action, target, value in MATRIX[::3]:
        rig, o = run(text)
        assert o.kind is OutcomeKind.COMMAND_NOT_EXECUTED and o.command.action is action and o.command.target is target and o.command.value == value
        assert o.execution_status == "NOT_EXECUTED" and o.spoken_confirmation_allowed is False
        spoken.update(rig.tts.texts)
    assert spoken == {COMMAND_NOT_EXECUTED_TEXT}
    for word in ("done", "turned", "set to", "muted", "success", "completed", "okay"):
        assert word not in COMMAND_NOT_EXECUTED_TEXT.lower()


def test_neither_the_transcript_nor_audio_leaks_through_a_refused_class_event_request():
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
        for text in (f"explain why the professor {SECRET} has not arrived", f"why does the classroom {SECRET} attendance look low", f"set the volume to {SECRET} percent"):
            rig = say(text, context_provider=lambda: FIXTURES["FRESH"])
            outcomes = rig.drive(UTTERANCE)
            blobs.append(repr(outcomes) + repr(rig.events))
            for o in outcomes:                                                                  # no outcome field holds audio samples
                for obj in (o, o.command, o.arbitration, o.output):
                    for f in (dataclasses.fields(obj) if obj is not None else ()):
                        assert not isinstance(getattr(obj, f.name), np.ndarray), f.name
    finally:
        rtm.logger.removeHandler(handler)
    blob = "\n".join(blobs + handler.lines)
    assert "zebra" not in blob and "marmalade" not in blob and "professor" not in "\n".join(handler.lines)
    assert any("rule=GEN.CLASS_EVENT_GUARD" in line for line in handler.lines)


# =========================================================================================
# G. documentation consistency: docs/contracts/WEEK8_COMMAND_SEMANTICS_V1.md against the source
# =========================================================================================

DOC_TEXT = DOC.read_text(encoding="utf-8")


def doc_enum(name):
    m = re.search(rf"^ENUM {name} = (.+)$", DOC_TEXT, re.M)
    assert m, name
    return [v.strip() for v in m.group(1).split("|")]


@pytest.mark.parametrize("name,enum", [("IntentCategory", ct.IntentCategory), ("RoutingStatus", ct.RoutingStatus), ("Handling", ct.Handling), ("ExecutionStatus", ct.ExecutionStatus),
                                       ("CommandAction", ct.CommandAction), ("CommandTarget", ct.CommandTarget), ("CommandUnit", ct.CommandUnit),
                                       ("CommandValidationStatus", ct.CommandValidationStatus)])
def test_the_contract_document_lists_exactly_the_real_enumeration_values(name, enum):
    assert doc_enum(name) == [m.value for m in enum]


def test_the_contract_document_states_the_real_command_table_and_every_accepted_phrase():
    for (cat, target), actions in ct.SUPPORTED_COMMANDS.items():
        for action, rule in actions.items():
            row = re.search(rf"^\| {cat.value} \| {target.value} \| (.*\b{action.value}\b.*) \| (.+) \|$", DOC_TEXT, re.M)
            assert row, (cat, target, action)
            assert ("integer 0–100 inclusive, unit PERCENT" in row.group(2)) == (rule is not None), (cat, target, action)
    lowered = DOC_TEXT.lower()
    for phrase in rt._COMMAND_EXACT:
        assert phrase in lowered, phrase
    for f in ("intent_category", "action", "target", "request_id", "value", "unit", "parameters", "validation_status"):
        assert f"| `{f}` |" in DOC_TEXT and f in {x.name for x in dataclasses.fields(StructuredCommand)}, f
    assert "exactly **six** official intent categories" in DOC_TEXT and "NOT_EXECUTED" in DOC_TEXT


def test_the_contract_document_separates_implemented_proposed_and_awaiting_approval_content():
    for tag in ("IMPLEMENTED CONTRACT", "PROPOSED FUTURE INTERFACE", "AWAITING TEAM APPROVAL"):
        assert tag in DOC_TEXT
    stages = dict(re.findall(r"^\| ([A-F])\. [^|]+\|[^|]+\| (\*\*[A-Z ]+\*\*)", DOC_TEXT, re.M))
    assert set(stages) == set("ABCDEF")
    assert all("NOT IMPLEMENTED" in stages[s] for s in "DEF") and all("IMPLEMENTED" in stages[s] and "NOT" not in stages[s] for s in "ABC")
    assert "No fake execution-result object" in DOC_TEXT and "ExecutionStatus` has exactly one member" in DOC_TEXT


def test_the_contract_document_lists_the_hardware_requirements_and_assigns_no_transport_detail():
    for n in range(1, 15):
        assert re.search(rf"^\| R{n} \| \*\*", DOC_TEXT, re.M), n
    for topic in ("Command identity", "Duplicate handling", "Bounded timeout", "Target identification", "Capability discovery", "Invalid-target rejection",
                  "Actual execution acknowledgment", "Failure and unavailable states", "Uncertainty after timeout", "Asynchronous completion", "Recovery after connection loss",
                  "Prevention of false success"):
        assert topic in DOC_TEXT, topic
    assert "OPEN" in DOC_TEXT and "No pin, baud rate, GPIO command, address or network endpoint" in DOC_TEXT
    for pattern in (r"GPIO\s*\d", r"\bpin\s*\d", r"\b\d{3,6}\s*baud", r"/dev/tty", r"\bCOM\d", r"https?://", r"\b\d{1,3}(\.\d{1,3}){3}\b", r"0x[0-9a-fA-F]{2}", r"\bI2C\b.*0x"):
        assert not re.search(pattern, DOC_TEXT), pattern


def test_the_contract_document_records_the_zone_gap_and_labels_the_verification_level():
    assert "SET_LIGHT_ZONE" in DOC_TEXT and "no zone" in DOC_TEXT and "SOFTWARE-VALIDATED" in DOC_TEXT and "NOT VALIDATED" in DOC_TEXT
    assert "does **not** imply a paired, discovered or reachable device" in DOC_TEXT
