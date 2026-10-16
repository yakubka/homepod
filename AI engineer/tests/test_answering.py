"""Team Leader midterm: the deterministic ANSWERING FOUNDATION (assistant/answering.py).

SOFTWARE-VALIDATED on SIMULATED fixtures. Every classroom value here comes from fusion's synthetic builders (origin
SIMULATED) or from the AI-1 simulated fixture. Nothing in this file is a live sensor, a classroom result, the Team
Leader AI-3 8-of-10 measurement, or evidence about speech recognition.
"""

import ast
import dataclasses
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest

from assistant import answering as a
from assistant.answering import (
    AnswerContext, AnswerContextError, AnswerContractError, AnswerEngine, AnswerFact, AnswerRequest, AnswerResult, AnswerSource,
    AnswerStatus, Capability, CapabilityDeclaration, DeterministicAnswerEngine, Intent, SimulatedScheduleContext, match_intent,
    normalize_question,
)
from fusion.contracts import (
    FusionFreshnessConfig, HumidityReading, LuxReading, Origin, SourceStatus, TemperatureReading, Unit, Usability,
)
from tests import fusion_synthetic as fs

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = json.loads((REPO_ROOT / "evaluation" / "ai_question_test_set_v1.json").read_text(encoding="utf-8"))
Q = {q["id"]: q for q in DOC["questions"]}
FIX = DOC["fixtures"]["room_state_present"]["values"]          # SIMULATED TEST FIXTURE: 17 people, 22.5 C, 45 %RH, 320 lux
ENGINE = DeterministicAnswerEngine()
ROOM = frozenset({Capability.PEOPLE_COUNT, Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS})


def state(people=FIX["people_count"], lux=FIX["lux"], temps=(("temp_1", FIX["temperature_c"]),), humidity=FIX["humidity_pct"],
          origin=Origin.SIMULATED, t=99.0, cfg=fs.CFG):
    return fs.fuse(fs.inputs(
        v=None if people is None else fs.vis(people, t, origin),
        p=fs.pres(True, t, origin),
        l=None if lux is None else fs.lux(lux, t, origin),
        temps=[fs.temp(sid, val, t, origin) for sid, val in temps],
        h=None if humidity is None else fs.hum(humidity, t, origin),
        cfg=cfg))


def ctx(st="fixture", caps=ROOM, schedule=None):
    return AnswerContext(classroom_state=state() if st == "fixture" else st, capabilities=CapabilityDeclaration(caps), schedule=schedule)


def ask(text, context=None):
    return ENGINE.answer(AnswerRequest(text, context or ctx()))


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    a.logger.addHandler(handler)
    yield handler.lines
    a.logger.removeHandler(handler)


# ---- the simulated fixture itself is what AI-1 says it is ---------------------------------------------------------------

def test_the_simulated_fixture_reaches_the_answer_layer_unchanged():
    st = state()
    assert st.occupancy.vision_count == FIX["people_count"] == 17
    assert [(v.value, v.unit) for v in st.environment.temperatures] == [(22.5, Unit.CELSIUS)]
    assert st.environment.humidity.value == 45.0 and st.environment.humidity.unit is Unit.PERCENT_RH
    assert st.environment.lux.value == 320.0 and st.environment.lux.unit is Unit.LUX


# ---- the phrase table is the AI-1 oracle -----------------------------------------------------------------------------------------

def test_phrase_table_equals_the_frozen_ai1_questions_and_variants_exactly():
    by_qid = {a.QUESTION_ID[intent]: set(phrases) for intent, phrases in a.PHRASES.items()}
    assert set(by_qid) == set(Q)
    for qid, q in Q.items():
        expected = {normalize_question(q["question"])} | {normalize_question(v) for v in q["accepted_variants"]}
        assert by_qid[qid] == expected, qid
    assert [a.QUESTION_ID[i] for i in Intent] == [f"Q{n}" for n in range(1, 11)]


def test_missing_data_wording_equals_the_ai1_examples():
    for intent, text in a.UNAVAILABLE_TEXT.items():
        assert text == Q[a.QUESTION_ID[intent]]["missing_data_behavior"]["example_wording"], intent
    assert set(a.UNAVAILABLE_TEXT) == {i for i in Intent if Q[a.QUESTION_ID[i]]["missing_data_behavior"]["example_wording"]}


# ---- AI-1: all ten questions, simulated -------------------------------------------------------------------------------------------

DIGITS = re.compile(r"\d+(?:\.\d+)?")


def judge(qid, r, declared=ROOM):
    """(expected description, passed) for one answer against the AI-1 oracle. SIMULATED fixture; software check only."""
    t = r.text
    if qid == "Q1":
        return f"reports {FIX['people_count']} people", r.status is AnswerStatus.ANSWERED and DIGITS.findall(t) == ["17"] and "people" in t
    if qid == "Q2":
        return f"reports {FIX['temperature_c']} degrees Celsius", r.status is AnswerStatus.ANSWERED and DIGITS.findall(t) == ["22.5"] and "°C" in t
    if qid == "Q3":
        return f"reports {FIX['humidity_pct']:g} percent relative humidity", r.status is AnswerStatus.ANSWERED and DIGITS.findall(t) == ["45"] and "%" in t and "humidity" in t
    if qid == "Q4":
        return f"reports {FIX['lux']:g} lux", r.status is AnswerStatus.ANSWERED and DIGITS.findall(t) == ["320"] and "lux" in t
    if qid in ("Q5", "Q6", "Q9"):
        return "states the information is unavailable, no lecture / number / time", (
            r.status is AnswerStatus.INFORMATION_UNAVAILABLE and not DIGITS.findall(t) and t == Q[qid]["missing_data_behavior"]["example_wording"])
    if qid == "Q7":
        words = re.findall(r"[a-z']+", t.lower())
        return "unavailable; neither yes nor no", (
            r.status is AnswerStatus.INFORMATION_UNAVAILABLE and t == Q[qid]["missing_data_behavior"]["example_wording"] and "yes" not in words and "no" not in words)
    if qid == "Q8":
        labels = {Capability.PEOPLE_COUNT: "number of people", Capability.TEMPERATURE: "temperature", Capability.HUMIDITY: "humidity",
                  Capability.BRIGHTNESS: "light level", Capability.LECTURE_INFORMATION: "lecture", Capability.ANNOUNCEMENTS: "announcement"}
        low = t.lower()
        ok = "classroom assistant" in low and all((labels[c] in low) == (c in declared) for c in labels)
        ok = ok and not any(w in low for w in ("medical", "internet", "smart-home", "smart home"))
        return "classroom assistant naming only the declared capabilities", r.status is AnswerStatus.ANSWERED and ok
    if qid == "Q10":
        return "announcements not available yet (announcement_available = false)", (
            r.status is AnswerStatus.ANSWERED and "not available" in t.lower() and "yes" not in t.lower())
    raise AssertionError(qid)


def evaluate_ai1(context):
    rows = []
    for qid, q in Q.items():
        r = ENGINE.answer(AnswerRequest(q["question"], context))
        expected, ok = judge(qid, r, context.capabilities.capabilities)
        rows.append((qid, expected, r.text, "PASS" if ok else "FAIL", r))
    return rows


def test_all_ten_frozen_ai1_questions_pass_on_the_simulated_fixture():
    rows = evaluate_ai1(ctx())
    assert [(r[0], r[3]) for r in rows] == [(f"Q{n}", "PASS") for n in range(1, 11)], rows
    sources = {r[0]: r[4].source for r in rows}
    assert {sources[q] for q in ("Q1", "Q2", "Q3", "Q4")} == {AnswerSource.SIMULATED}        # never presented as live
    assert {sources[q] for q in ("Q5", "Q6", "Q7", "Q9")} == {AnswerSource.UNAVAILABLE}
    assert {sources[q] for q in ("Q8", "Q10")} == {AnswerSource.DECLARATION}


def test_each_ai1_question_carries_its_intent_and_question_id():
    for qid, q in Q.items():
        r = ask(q["question"])
        assert r.question_id == qid and a.QUESTION_ID[r.intent] == qid


# ---- AI-1 missing-data behaviour --------------------------------------------------------------------------------------------------

def _unavailable_contexts():
    old = state(t=50.0)                                                                       # every reading 50 s old: STALE
    unconfigured = state(cfg=FusionFreshnessConfig())                                      # no max ages configured: UNCONFIGURED
    failed = fs.fuse(fs.inputs(v=fs.vis(status=SourceStatus.FAILED), p=fs.pres(True), l=fs.lux(), h=fs.hum(), temps=[fs.temp("t1", 22.5)]))
    failed_env = fs.fuse(fs.inputs(v=fs.vis(17), p=fs.pres(True),
                                   l=LuxReading(fs.env("e", status=SourceStatus.FAILED), None),
                                   h=HumidityReading(fs.env("e", status=SourceStatus.UNAVAILABLE), None),
                                   temps=[TemperatureReading(fs.env("e", status=SourceStatus.FAILED), "t1", None)]))
    return {
        "no state at all": ctx(st=None),
        "nothing received (MISSING)": ctx(st=fs.fuse(fs.inputs())),
        "all stale": ctx(st=old),
        "unconfigured freshness": ctx(st=unconfigured),
        "camera failed": ctx(st=failed),
        "environment sensors failed": ctx(st=failed_env),
    }


@pytest.mark.parametrize("name", list(_unavailable_contexts()))
def test_room_questions_are_unavailable_and_fabricate_nothing(name):
    context = _unavailable_contexts()[name]
    for qid, intent_text in (("Q1", None), ("Q2", None), ("Q3", None), ("Q4", None)):
        r = ask(Q[qid]["question"], context)
        if name == "camera failed" and qid != "Q1":
            continue                                                                          # only the camera is down in that context
        if name == "environment sensors failed" and qid == "Q1":
            continue                                                                          # only the environment sensors are down
        assert r.status is AnswerStatus.INFORMATION_UNAVAILABLE, (name, qid, r.text)
        assert r.text == Q[qid]["missing_data_behavior"]["example_wording"]
        assert not DIGITS.findall(r.text) and r.facts == () and r.source is AnswerSource.UNAVAILABLE and r.reason_code


def test_missing_state_never_changes_the_lecture_professor_and_next_class_answers():
    for qid in ("Q5", "Q6", "Q7", "Q9"):
        r = ask(Q[qid]["question"], ctx(st=None))
        assert (r.status, r.text) == (AnswerStatus.INFORMATION_UNAVAILABLE, Q[qid]["missing_data_behavior"]["example_wording"])
        assert not DIGITS.findall(r.text)


def test_stale_readings_are_not_used_even_though_the_diagnostic_value_exists():
    st = state(t=50.0)
    assert st.occupancy.raw_vision_count == 17 and st.occupancy.vision_count is None            # the stale count survives only as a diagnostic
    r = ask(Q["Q1"]["question"], ctx(st=st))
    assert r.status is AnswerStatus.INFORMATION_UNAVAILABLE and "17" not in r.text


def test_a_fresh_count_is_not_used_if_the_state_says_vision_did_not_participate():
    st = state()
    forged = dataclasses.replace(st, occupancy=dataclasses.replace(st.occupancy, vision_used=False))
    assert ask(Q["Q1"]["question"], ctx(st=forged)).status is AnswerStatus.INFORMATION_UNAVAILABLE
    stale_vision = dataclasses.replace(st, vision_state=dataclasses.replace(st.vision_state, usability=Usability.STALE))
    assert ask(Q["Q1"]["question"], ctx(st=stale_vision)).status is AnswerStatus.INFORMATION_UNAVAILABLE


def test_a_wrong_unit_is_unavailable_never_converted():
    st = state()
    env = st.environment
    for field, bad_unit, qid in (("humidity", Unit.CELSIUS, "Q3"), ("lux", Unit.PERCENT_RH, "Q4")):
        value = dataclasses.replace(getattr(env, field), unit=bad_unit)
        forged = dataclasses.replace(st, environment=dataclasses.replace(env, **{field: value}))
        assert ask(Q[qid]["question"], ctx(st=forged)).status is AnswerStatus.INFORMATION_UNAVAILABLE, field
    temp = dataclasses.replace(env.temperatures[0], unit=Unit.LUX)
    forged = dataclasses.replace(st, environment=dataclasses.replace(env, temperatures=(temp,)))
    assert ask(Q["Q2"]["question"], ctx(st=forged)).status is AnswerStatus.INFORMATION_UNAVAILABLE


def test_a_stale_environment_value_is_not_used_even_if_it_still_carries_a_number():
    st = state()
    env = st.environment

    def stale(v):
        return dataclasses.replace(v, state=dataclasses.replace(v.state, usability=Usability.STALE))

    forged = dataclasses.replace(st, environment=dataclasses.replace(
        env, lux=stale(env.lux), humidity=stale(env.humidity), temperatures=tuple(stale(t) for t in env.temperatures)))
    assert forged.environment.lux.value == 320.0                                   # the number is still there; only the state says STALE
    for qid in ("Q2", "Q3", "Q4"):
        assert ask(Q[qid]["question"], ctx(st=forged)).status is AnswerStatus.INFORMATION_UNAVAILABLE, qid


def test_each_missing_source_only_affects_its_own_question():
    r = {qid: ask(Q[qid]["question"], ctx(st=state(people=None, humidity=None))).status for qid in ("Q1", "Q2", "Q3", "Q4")}
    assert r == {"Q1": AnswerStatus.INFORMATION_UNAVAILABLE, "Q2": AnswerStatus.ANSWERED,
                 "Q3": AnswerStatus.INFORMATION_UNAVAILABLE, "Q4": AnswerStatus.ANSWERED}


# ---- accepted variants -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("context_name", ["fixture", "missing"])
def test_every_accepted_variant_gets_the_same_answer_as_its_primary_question(context_name):
    context = ctx() if context_name == "fixture" else ctx(st=None)
    for qid, q in Q.items():
        primary = ask(q["question"], context)
        assert q["accepted_variants"], qid
        for variant in q["accepted_variants"]:
            r = ask(variant, context)
            assert (r.status, r.intent, r.question_id, r.text, r.facts, r.source) == (
                primary.status, primary.intent, primary.question_id, primary.text, primary.facts, primary.source), (qid, variant)


# ---- normalisation and false matches -----------------------------------------------------------------------------------------------

def test_normalisation_is_narrow():
    assert normalize_question("  HOW   many\tpeople are in the room??? ") == "how many people are in the room"
    assert normalize_question("What’s that?") == "what's that"
    assert match_intent("  How many PEOPLE are in the room!  ") is Intent.ROOM_PEOPLE_COUNT
    assert match_intent("How many people are in the room, please") is None             # no semantic guessing
    assert match_intent("Hey Jarvis, how many people are in the room?") is None          # no prefix stripping at this layer
    assert match_intent("how many people are in the rooms") is None


FALSE_MATCHES = [
    "How many students are here?", "How many people are expected?", "How many people are expected in the room?",
    "How many students are in the room?", "How many people were in the room yesterday?", "How many people are in the building?",
    "What lecture is next?", "What lecture was scheduled earlier?", "What is the next lecture's room temperature?",
    "What time is the lecture?", "What time is the current class?", "When did the last class start?",
    "What is the temperature outside?", "What is the temperature?", "How humid is it outside?", "Is the room bright enough?",
    "Make an announcement.", "Please make an announcement now.", "Announce that the professor is late.", "Can you announce the next class?",
    "Can you play music?", "What can you do for me tomorrow?", "Is the professor in the room?", "Is the room occupied?",
    "Who is the professor?", "Has the lecture started yet?", "Has the professor left?",
    "Tell me a joke.", "Explain machine learning.", "Turn on the lights.",
]


@pytest.mark.parametrize("text", FALSE_MATCHES)
def test_nearby_but_unsupported_questions_are_not_answered(text):
    r = ask(text, ctx(schedule=SimulatedScheduleContext("Maths", 30, "10:00")))
    assert r.status is AnswerStatus.UNSUPPORTED_QUESTION and r.text == "I can't answer that yet." and r.facts == ()
    assert r.intent is None and r.source is AnswerSource.NONE and r.reason_code == a.REASON_NO_MATCH


def test_overlapping_questions_stay_distinct():
    c = ctx(schedule=SimulatedScheduleContext("Maths", 30, "10:00"))
    people, expected = ask("How many people are in the room?", c), ask("How many students are expected?", c)
    assert (people.intent, expected.intent) == (Intent.ROOM_PEOPLE_COUNT, Intent.LECTURE_EXPECTED_ATTENDANCE)
    assert people.facts[0].value == 17 and expected.facts[0].value == 30
    current, nxt = ask("What lecture is scheduled now?", c), ask("When is the next class?", c)
    assert (current.intent, nxt.intent) == (Intent.LECTURE_CURRENT, Intent.NEXT_CLASS) and "Maths" in current.text and "10:00" in nxt.text and "Maths" not in nxt.text
    can, do = ask("Can you make an announcement?", c), ask("Make an announcement", c)
    assert can.status is AnswerStatus.ANSWERED and do.status is AnswerStatus.UNSUPPORTED_QUESTION


@pytest.mark.parametrize("bad", [None, 5, 3.2, b"how many people", ["how many people are in the room"], "", "   ", "\n\t", "?!...", "’"])
def test_invalid_input_is_reported_not_raised(bad):
    r = ask(bad)
    assert r.status is AnswerStatus.INVALID_INPUT and r.text == a.INVALID_TEXT and r.intent is None and r.source is AnswerSource.NONE


def test_bad_requests_and_contexts_raise_typed_errors():
    for bad in ("how many people", None, {"text": "x"}):
        with pytest.raises(AnswerContextError):
            ENGINE.answer(bad)
    with pytest.raises(AnswerContextError):
        AnswerRequest("hi", "not a context")
    with pytest.raises(AnswerContextError):
        AnswerContext(classroom_state="a state")
    with pytest.raises(AnswerContextError):
        AnswerContext(schedule={"current_lecture": "Maths"})
    with pytest.raises(AnswerContextError):
        AnswerContext(capabilities=["PEOPLE_COUNT"])
    with pytest.raises(AnswerContextError):
        CapabilityDeclaration({"PEOPLE_COUNT"})


# ---- room answers: values and units ----------------------------------------------------------------------------------------------------

def test_room_answers_state_the_fixture_values_with_units_and_metadata():
    people, temp, hum, lux = (ask(Q[q]["question"]) for q in ("Q1", "Q2", "Q3", "Q4"))
    assert people.text == "There are 17 people in the room." and people.facts == (AnswerFact("people_count", 17, "people"),)
    assert temp.text == "The temperature in the room is 22.5 °C." and temp.facts == (AnswerFact("temperature", 22.5, "degC"),)
    assert hum.text == "The relative humidity in the room is 45%." and hum.facts == (AnswerFact("humidity", 45.0, "%RH"),)
    assert lux.text == "The light level in the room is 320 lux." and lux.facts == (AnswerFact("lux", 320.0, "lx"),)
    for r in (people, temp, hum, lux):
        assert (r.status, r.source, r.reason_code) == (AnswerStatus.ANSWERED, AnswerSource.SIMULATED, None)


def test_counts_of_one_and_zero_and_number_formatting():
    assert ask("how many people are here", ctx(st=state(people=1))).text == "There is 1 person in the room."
    assert ask("how many people are here", ctx(st=state(people=0))).text == "There are 0 people in the room."
    assert ask("what is the room temperature", ctx(st=state(temps=(("t", 21.46),)))).text == "The temperature in the room is 21.5 °C."
    assert ask("what is the light level", ctx(st=state(lux=0.0))).text == "The light level in the room is 0 lux."


def test_several_temperature_sensors_are_listed_never_averaged():
    r = ask("what is the room temperature", ctx(st=state(temps=(("a", 20.0), ("b", 24.0)))))
    assert r.text == "The temperature sensors read 20 °C and 24 °C." and [f.value for f in r.facts] == [20.0, 24.0]
    assert "22" not in r.text
    one_stale = fs.fuse(fs.inputs(v=fs.vis(17), p=fs.pres(True), temps=[fs.temp("a", 20.0), fs.temp("b", 24.0, t=10.0)]))
    assert ask("what is the room temperature", ctx(st=one_stale)).text == "The temperature in the room is 20 °C."     # the stale one is dropped


def test_expected_attendance_never_substitutes_for_the_people_count_or_the_reverse():
    c = ctx(st=state(people=None), schedule=SimulatedScheduleContext(expected_attendance=30))
    assert ask("How many people are in the room?", c).status is AnswerStatus.INFORMATION_UNAVAILABLE
    assert "30" not in ask("How many people are in the room?", c).text
    assert ask("How many students are expected?", ctx()).status is AnswerStatus.INFORMATION_UNAVAILABLE     # 17 people present, nothing expected
    assert "17" not in ask("How many students are expected?", ctx()).text


# ---- lecture, schedule, professor ---------------------------------------------------------------------------------------------------------

def test_without_a_schedule_the_three_schedule_questions_are_unavailable():
    for qid, reason in (("Q5", a.REASON_NO_SCHEDULE), ("Q6", a.REASON_NO_SCHEDULE), ("Q9", a.REASON_NO_SCHEDULE)):
        r = ask(Q[qid]["question"])
        assert r.status is AnswerStatus.INFORMATION_UNAVAILABLE and r.reason_code == reason and r.source is AnswerSource.UNAVAILABLE


def test_an_empty_schedule_placeholder_is_not_a_schedule():
    for empty in (SimulatedScheduleContext(), SimulatedScheduleContext(current_lecture=None, expected_attendance=None, next_class_time=None)):
        for qid in ("Q5", "Q6", "Q9"):
            r = ask(Q[qid]["question"], ctx(schedule=empty))
            assert r.status is AnswerStatus.INFORMATION_UNAVAILABLE and "None" not in r.text


def test_an_explicit_simulated_schedule_answers_and_is_labelled_simulated():
    c = ctx(schedule=SimulatedScheduleContext("Signals and Systems", 1, "14:00"))
    r5, r6, r9 = (ask(Q[q]["question"], c) for q in ("Q5", "Q6", "Q9"))
    assert r5.text == "The lecture scheduled now is Signals and Systems."
    assert r6.text == "1 student is expected for this lecture." and r6.facts[0].unit == "students"
    assert r9.text == "The next class is at 14:00."
    assert {r.source for r in (r5, r6, r9)} == {AnswerSource.SIMULATED}
    partial = ctx(schedule=SimulatedScheduleContext(current_lecture="Maths"))
    assert ask(Q["Q5"]["question"], partial).status is AnswerStatus.ANSWERED
    assert ask(Q["Q6"]["question"], partial).status is AnswerStatus.INFORMATION_UNAVAILABLE


@pytest.mark.parametrize("kw", [dict(current_lecture=""), dict(current_lecture=5), dict(next_class_time="  "), dict(expected_attendance=-1),
                                dict(expected_attendance=True), dict(expected_attendance=2.5), dict(expected_attendance="30")])
def test_invalid_schedule_fixtures_are_rejected(kw):
    with pytest.raises(AnswerContextError):
        SimulatedScheduleContext(**kw)


def test_reason_codes_are_stable_literals_and_distinct():
    assert ask("Has the professor arrived yet?").reason_code == "NO_AUTHORITATIVE_ARRIVAL_STATE"
    assert ask("What lecture is scheduled now?").reason_code == "NO_SCHEDULE_SOURCE"
    assert ask("How many people are here?", ctx(st=None)).reason_code == "NO_CLASSROOM_STATE"
    assert ask("How many people are here?", ctx(st=state(people=None))).reason_code == "NO_FRESH_VALID_VALUE"
    assert ask("how many people are here", ctx(caps=frozenset())).reason_code == "CAPABILITY_NOT_DECLARED"
    assert ask("tell me a joke").reason_code == "NO_MATCHING_QUESTION"
    assert ask("   ").reason_code == "EMPTY_OR_NON_TEXT_INPUT"


def test_professor_arrival_is_always_unavailable_and_never_guessed():
    full = ctx(schedule=SimulatedScheduleContext("Maths", 30, "10:00"))
    crowded = AnswerContext(classroom_state=state(people=22), capabilities=CapabilityDeclaration(frozenset(Capability)), schedule=full.schedule)
    for c in (ctx(), full, crowded, ctx(st=None), ctx(st=state(people=0))):
        for text in ("Has the professor arrived yet?", "Is the professor here yet?", "Has the lecturer arrived?"):
            r = ask(text, c)
            assert r.status is AnswerStatus.INFORMATION_UNAVAILABLE and r.text == "I don't have professor arrival information."
            assert r.reason_code == a.REASON_NO_ARRIVAL and r.facts == ()
            assert not {"yes", "no"} & set(re.findall(r"[a-z']+", r.text.lower()))


# ---- capabilities and announcements ------------------------------------------------------------------------------------------------------------

def test_what_can_you_do_names_only_the_declared_capabilities():
    full = ask("What can you do?", ctx())
    assert full.text == "I'm VESTA, a classroom assistant. I can tell you the number of people, the temperature, the humidity and the light level in the room."
    one = ask("What can you do?", ctx(caps=frozenset({Capability.TEMPERATURE})))
    assert one.text == "I'm VESTA, a classroom assistant. I can tell you the temperature in the room."
    nothing = ask("What can you do?", ctx(caps=frozenset()))
    assert "no functions are enabled" in nothing.text and "temperature" not in nothing.text
    both = ask("What can you do?", ctx(caps=ROOM | {Capability.ANNOUNCEMENTS, Capability.LECTURE_INFORMATION}))
    assert "announcements" in both.text and "lecture schedule" in both.text
    for r in (full, one, nothing, both):
        assert r.source is AnswerSource.DECLARATION and r.status is AnswerStatus.ANSWERED
    for text in (full.text, one.text, nothing.text):
        assert not any(w in text.lower() for w in ("announcement", "lecture", "medical", "internet", "smart"))


def test_announcement_answer_follows_the_declaration_only():
    off = ask("Can you make an announcement?", ctx())
    on = ask("Can you make an announcement?", ctx(caps=ROOM | {Capability.ANNOUNCEMENTS}))
    assert off.text == "Announcements are not available yet." and off.facts == (AnswerFact("announcement_available", False),)
    assert on.text == "Yes, I can play predefined announcements." and on.facts == (AnswerFact("announcement_available", True),)
    assert ask("Can you make an announcement?", ctx(caps=frozenset())).text == off.text
    assert not CapabilityDeclaration.none().announcement_available and CapabilityDeclaration(frozenset({Capability.ANNOUNCEMENTS})).announcement_available


def test_the_default_context_declares_nothing_and_answers_no_room_question():
    default = AnswerContext()
    assert default.capabilities.capabilities == frozenset() and default.classroom_state is None
    for qid in ("Q1", "Q2", "Q3", "Q4"):
        r = ask(Q[qid]["question"], default)
        assert r.status is AnswerStatus.UNSUPPORTED_QUESTION and r.reason_code == a.REASON_NOT_DECLARED and r.intent is not None


def test_a_room_question_without_its_capability_is_unsupported_even_with_data():
    r = ask("What is the room temperature?", ctx(caps=ROOM - {Capability.TEMPERATURE}))
    assert r.status is AnswerStatus.UNSUPPORTED_QUESTION and r.text == "I can't answer that yet." and "22" not in r.text
    assert ask("What is the room humidity?", ctx(caps=ROOM - {Capability.TEMPERATURE})).status is AnswerStatus.ANSWERED
    for qid in ("Q5", "Q6", "Q7", "Q9"):                                                      # lecture-type questions are not capability gated
        assert ask(Q[qid]["question"], ctx(caps=frozenset())).status is AnswerStatus.INFORMATION_UNAVAILABLE


# ---- provenance -----------------------------------------------------------------------------------------------------------------------------------

def test_provenance_is_passed_through_and_never_upgraded():
    expected = {Origin.SIMULATED: AnswerSource.SIMULATED, Origin.REAL: AnswerSource.REAL, Origin.UNKNOWN: AnswerSource.UNKNOWN_ORIGIN}
    for origin, source in expected.items():
        c = ctx(st=state(origin=origin))
        assert {ask(Q[q]["question"], c).source for q in ("Q1", "Q2", "Q3", "Q4")} == {source}, origin


def test_mixed_temperature_origins_take_the_weakest_label():
    mixed = fs.fuse(fs.inputs(v=fs.vis(17), p=fs.pres(True),
                              temps=[fs.temp("a", 20.0, origin=Origin.REAL), fs.temp("b", 22.0, origin=Origin.SIMULATED)]))
    assert ask("what is the room temperature", ctx(st=mixed)).source is AnswerSource.SIMULATED
    unknown = fs.fuse(fs.inputs(v=fs.vis(17), p=fs.pres(True),
                                temps=[fs.temp("a", 20.0, origin=Origin.REAL), fs.temp("b", 22.0, origin=Origin.UNKNOWN)]))
    assert ask("what is the room temperature", ctx(st=unknown)).source is AnswerSource.UNKNOWN_ORIGIN


def test_unavailable_and_unsupported_answers_carry_no_data_source():
    assert ask("how many people are here", ctx(st=None)).source is AnswerSource.UNAVAILABLE
    assert ask("tell me a joke").source is AnswerSource.NONE


# ---- result contract ---------------------------------------------------------------------------------------------------------------------------------

def _result(**kw):
    base = dict(status=AnswerStatus.ANSWERED, text="x", intent=Intent.ROOM_TEMPERATURE, question_id="Q2", source=AnswerSource.SIMULATED,
                reason_code=None, facts=(AnswerFact("t", 1.0, "degC"),))
    base.update(kw)
    return AnswerResult(**base)


def test_valid_result_is_frozen():
    r = _result()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.text = "changed"


@pytest.mark.parametrize("kw", [
    dict(text=""), dict(text="  "), dict(text=None), dict(status="ANSWERED"), dict(source="SIMULATED"),
    dict(question_id="Q3"), dict(question_id=None), dict(intent=None),
    dict(facts=()), dict(source=AnswerSource.UNAVAILABLE), dict(source=AnswerSource.NONE),
    dict(status=AnswerStatus.INFORMATION_UNAVAILABLE),                                             # facts present / source not UNAVAILABLE
    dict(status=AnswerStatus.INFORMATION_UNAVAILABLE, facts=(), source=AnswerSource.SIMULATED),
    dict(status=AnswerStatus.INFORMATION_UNAVAILABLE, facts=(), source=AnswerSource.UNAVAILABLE, intent=None, question_id=None),
    dict(status=AnswerStatus.UNSUPPORTED_QUESTION),                                                # facts present
    dict(status=AnswerStatus.UNSUPPORTED_QUESTION, facts=(), source=AnswerSource.SIMULATED),
    dict(status=AnswerStatus.INVALID_INPUT, facts=(), source=AnswerSource.NONE),                    # INVALID has no intent
])
def test_result_invariants_are_enforced(kw):
    with pytest.raises(AnswerContractError):
        _result(**kw)


def test_an_engine_that_returns_a_non_result_is_a_contract_error():
    class Bad(AnswerEngine):
        def _answer(self, text, context):
            return "just a string"

    with pytest.raises(AnswerContractError):
        Bad().answer(AnswerRequest("anything", AnswerContext()))


# ---- privacy, scope and imports ------------------------------------------------------------------------------------------------------------------------

def test_neither_the_question_nor_the_answer_text_reaches_the_logs(logs):
    secret_question = "my secret question about the exam"
    ask(secret_question)
    ask("How many people are in the room?")
    ask("What is the temperature in the room?")
    ask("   ")
    joined = "\n".join(logs)
    assert logs and "event=answer_done" in joined
    for fragment in ("secret", "exam", "How many", "how many", "people are in", "22.5", "17", "temperature in the room"):
        assert fragment not in joined, fragment
    assert "status=ANSWERED" in joined and "status=UNSUPPORTED_QUESTION" in joined and "status=INVALID_INPUT" in joined


def test_answering_has_no_llm_network_audio_or_stt_dependency():
    tree = ast.parse((REPO_ROOT / "assistant" / "answering.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(n.name.split(".")[0] for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"math", "re", "abc", "dataclasses", "enum", "typing", "config", "fusion"}


def test_importing_the_answering_layer_loads_no_stt_audio_or_model_library():
    code = (
        "import sys\n"
        "import assistant.answering\n"
        "bad = [m for m in ('faster_whisper', 'ctranslate2', 'av', 'sounddevice', 'openwakeword', 'speech', 'huggingface_hub', 'openai') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
