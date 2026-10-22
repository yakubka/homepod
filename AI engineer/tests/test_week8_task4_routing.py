"""Week 8 Task 4: controlled intent coverage hardening.

SOFTWARE-VALIDATED. These tests pin (a) the reviewed phrase additions and the owner-approved policy decisions A-E, (b) that the historical v1 evaluation is
reproducible byte-for-byte with the frozen original router and the pinned Task 1 snapshot, (c) the safety invariants, and (d) the v2 development corpus.
The v2 corpus is DEVELOPMENT / REGRESSION evidence written by the router's author: it says nothing about real-world accuracy or speech recognition.
"""

import hashlib
import json
from pathlib import Path

import pytest

from assistant.answering import AnswerStatus
from evaluation import week8_intent_eval as ev
from evaluation import week8_router_history as history
from intents import router as rt
from intents.contracts import ExecutionStatus, Handling, IntentCategory, IntentRequest, RoutingStatus

REPO = Path(__file__).resolve().parent.parent
V1, V2 = ev.DEFAULT_MANIFEST, ev.V2_MANIFEST
HASH = {
    "evaluation/week8_intent_manifest_v1.json": "a03f494c5f8d9a0abc3a786879a4e424e259e63e75baa012853868f25572c8b0",
    "evaluation/week8_intent_manifest_v2.json": "2488542b9cf91040129ed11ec6ece4df8ca62b2c79c0d1e7dea15bc5caa00ca0",
    "evaluation/week8_task1_router_frozen.py": "e592240c3c9a0babc4b4e299ab9bbb45e70fb164d20918d7867b54bbed47e62f",
    "evaluation/week8_phrase_table_task1_snapshot.json": "c6476795d372ccf2bf721a32480c5bddc415f08d8a4f1a87bb9e051e683197e2",
    "evaluation/week8_phrase_table_task4_snapshot.json": "75067435afa17b467cfa0f70c977e49c9a315282687eee77154310a03ded1763",
}


def r(text):
    return rt.route_intent(IntentRequest(text, "t4"))


def outcome(text):
    x = r(text)
    return x.routing_status, (x.category.value if x.category else None), x.query_family, x.handling, x.reason_code


@pytest.fixture(scope="module")
def v1():
    return ev.load_manifest(V1)


@pytest.fixture(scope="module")
def v2():
    return ev.load_manifest(V2)


@pytest.fixture(scope="module")
def v1_original(v1):
    return ev.evaluate(v1, route=ev.load_router("task1-frozen"), router_name="task1-frozen")


@pytest.fixture(scope="module")
def v1_new(v1):
    return ev.evaluate(v1)


@pytest.fixture(scope="module")
def v2_report(v2):
    # The Task 4 results are the TASK 4 router's. Since Week 8 Task 5 added the class-event guard, the Task 4 router is rebuilt from the edit script
    # (byte-verified) so that these pins stay historical evidence; the Task 5 results are pinned in tests/test_week8_task5_audit.py.
    return ev.evaluate(v2, route=history.load_task4_router(), router_name="task4-baseline")


# =========================================================================================
# A. frozen artifacts and historical reproducibility
# =========================================================================================

@pytest.mark.parametrize("rel", sorted(HASH))
def test_the_frozen_artifacts_are_byte_identical_to_their_recorded_hashes(rel):
    assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == HASH[rel], rel


def test_the_frozen_router_copy_is_the_original_task_1_router_and_the_task1_snapshot_is_its_table():
    from evaluation import week8_task1_router_frozen as frozen
    exact, command, ambiguous = ev.load_phrase_snapshot("task1")
    assert exact == frozenset(frozen._EXACT) and command == frozenset(frozen._COMMAND_EXACT) and ambiguous == frozenset()
    assert len(exact) == 55 and len(command) == 16
    assert json.loads((REPO / "evaluation" / "week8_phrase_table_task1_snapshot.json").read_text(encoding="utf-8"))["source_sha256"] == HASH["evaluation/week8_task1_router_frozen.py"]


def test_the_task4_snapshot_is_the_live_router_table():
    exact, command, ambiguous = ev.load_phrase_snapshot("task4")
    assert exact == frozenset(rt._EXACT) and command == frozenset(rt._COMMAND_EXACT) and ambiguous == frozenset(rt._AMBIGUOUS_EXACT)
    assert (len(exact), len(command), len(ambiguous)) == (121, 20, 10)


def test_v1_provenance_stays_reproducible_after_the_router_grew(v1):
    # the live router now contains phrases that the v1 corpus declares as authored; v1 is still judged against the pinned Task 1 snapshot
    grown = [e for e in v1.entries if e.source_type != "ROUTER_PHRASE_TABLE" and ev.is_live_table_phrase(e.text)]
    assert len(grown) >= 2                                                  # e.g. "what is humidity" became a live table member
    for e in v1.entries:
        assert ev.is_table_phrase(e.text) == (e.source_type == "ROUTER_PHRASE_TABLE"), e.id
    assert v1.snapshot == "task1"


def test_the_original_v1_baseline_is_reproduced_with_the_frozen_router(v1_original):
    s = v1_original["strata"]
    assert v1_original["router"] == "task1-frozen" and v1_original["phrase_table_snapshot"] == "task1"
    assert (s["SPEC_TABLE"]["n_items"], s["SPEC_AUTHORED"]["n_items"], s["DEV_AUTHORED"]["n_items"]) == (112, 133, 35)
    for name, cat, outc in (("SPEC_TABLE", (112, 112), (112, 112)), ("SPEC_AUTHORED", (41, 41), (133, 133)), ("DEV_AUTHORED", (5, 30), (10, 35))):
        assert (s[name]["category_accuracy"]["numerator"], s[name]["category_accuracy"]["denominator"]) == cat, name
        assert (s[name]["routing_outcome_agreement"]["numerator"], s[name]["routing_outcome_agreement"]["denominator"]) == outc, name
    assert (s["ALL"]["category_accuracy"]["numerator"], s["ALL"]["category_accuracy"]["denominator"]) == (158, 183)


def test_the_new_router_on_the_unchanged_v1_corpus_improves_only_the_development_probe(v1_new):
    s = v1_new["strata"]
    assert v1_new["router"] == "current"
    for name in ("SPEC_TABLE", "SPEC_AUTHORED"):                              # the specification strata are unchanged: nothing regressed
        assert s[name]["mismatches"] == [], name
    d = s["DEV_AUTHORED"]
    assert (d["category_accuracy"]["numerator"], d["category_accuracy"]["denominator"]) == (21, 30)
    assert (d["routing_outcome_agreement"]["numerator"], d["routing_outcome_agreement"]["denominator"]) == (26, 35)
    assert [m["id"] for m in d["mismatches"]] == ["D-SQ-006", "D-OQ-001", "D-OQ-002", "D-GA-001", "D-GA-002", "D-GA-004", "D-GA-005", "D-GA-010", "D-GA-013"]
    assert (s["ALL"]["category_accuracy"]["numerator"], s["ALL"]["category_accuracy"]["denominator"]) == (174, 183)


def test_every_v1_mismatch_is_explained_by_a_v2_correction_or_a_named_known_gap(v1_new, v2):
    explained = {c["v1_id"] for c in v2.corrections}
    unexplained = [m["id"] for m in v1_new["strata"]["DEV_AUTHORED"]["mismatches"] if m["id"] not in explained]
    assert unexplained == ["D-GA-013"]                                          # "what is the dark side of the moon": a plain sensor-word false rejection, left as a known gap


def test_the_v1_gold_labels_are_not_modified_by_the_v2_corrections(v1, v2):
    v1_by = {e.id: e for e in v1.entries}
    v2_by = {e.id: e for e in v2.entries}
    assert len(v2.corrections) == 8
    for c in v2.corrections:
        old, new = v1_by[c["v1_id"]], v2_by[c["v2_id"]]
        assert rt.normalize(old.text) == rt.normalize(new.text)
        assert (old.expected_outcome, old.expected_category) != (new.expected_outcome, new.expected_category), c      # the label really differs
        assert len(c["reason"]) > 20 and c["change"]


# =========================================================================================
# B. the reviewed additions, one by one
# =========================================================================================

NEW_EXACT = sorted(ev.load_phrase_snapshot("task4")[0] - ev.load_phrase_snapshot("task1")[0])
FAMILY_CAT = {"ROOM_TEMPERATURE": "SENSOR_QUERY", "ROOM_HUMIDITY": "SENSOR_QUERY", "ROOM_BRIGHTNESS": "SENSOR_QUERY", "ROOM_PEOPLE_COUNT": "OCCUPANCY_QUERY",
              "NEXT_CLASS": "GENERAL_AI"}


def test_exactly_66_exact_and_4_command_phrases_and_10_ambiguous_definitions_were_added():
    assert len(NEW_EXACT) == 66
    assert sorted(ev.load_phrase_snapshot("task4")[1] - ev.load_phrase_snapshot("task1")[1]) == ["turn down the sound", "turn the sound down", "turn the sound up", "turn up the sound"]
    assert len(ev.load_phrase_snapshot("task4")[2]) == 10


@pytest.mark.parametrize("phrase", NEW_EXACT)
def test_every_newly_accepted_query_phrase_routes_to_its_family(phrase):
    x = r(phrase)
    fam = x.query_family
    assert x.routing_status is RoutingStatus.ROUTED and x.handling is Handling.ANSWERING_FOUNDATION and fam in FAMILY_CAT
    assert x.category.value == FAMILY_CAT[fam] and x.command is None


@pytest.mark.parametrize("phrase,action", [("turn the sound up", "VOLUME_UP"), ("turn up the sound", "VOLUME_UP"), ("turn the sound down", "VOLUME_DOWN"),
                                           ("turn down the sound", "VOLUME_DOWN"), ("please turn the sound up", "VOLUME_UP"), ("Turn the sound down.", "VOLUME_DOWN")])
def test_the_new_sound_commands_are_validated_not_executed(phrase, action):
    x = r(phrase)
    assert x.category is IntentCategory.LOCAL_COMMAND and x.command.action.value == action and x.command.target.value == "SPEAKER_OUTPUT"
    assert x.handling is Handling.EXECUTION_NOT_IMPLEMENTED and x.execution_status is ExecutionStatus.NOT_EXECUTED and x.spoken_confirmation_allowed is False


@pytest.mark.parametrize("text,value", [("set the speaker volume to 40 percent", 40), ("set speaker volume to 0 percent", 0), ("set the speaker volume to 100%", 100)])
def test_speaker_volume_is_an_explicit_object(text, value):
    x = r(text)
    assert x.command.action.value == "SET_VOLUME" and x.command.value == value and x.command.unit.value == "PERCENT"
    assert x.execution_status is ExecutionStatus.NOT_EXECUTED and x.spoken_confirmation_allowed is False


@pytest.mark.parametrize("text,reason", [("set the speaker volume to 150 percent", "VALUE_OUT_OF_RANGE"), ("set the speaker volume to -5 percent", "INVALID_VALUE"),
                                         ("set the speaker volume to 12.5 percent", "INVALID_VALUE"), ("set the speaker volume", "MISSING_VALUE"),
                                         ("set the speaker volume to 50", "MISSING_UNIT"), ("set the volume to -1%", "INVALID_VALUE")])
def test_negative_numbers_and_invalid_percentages_are_rejected_without_a_command(text, reason):
    x = r(text)
    assert x.routing_status is RoutingStatus.INVALID_COMMAND and x.reason_code == reason and x.command is None


@pytest.mark.parametrize("text", ["set the speaker to 50 percent", "turn the sound off", "turn the sound up a lot", "turn the sound up and turn the sound down",
                                  "turn the sound up turn the sound up", "set the fan to 50 percent", "turn on the fan", "set the projector to 50 percent"])
def test_unsupported_device_targets_and_multiple_commands_are_rejected(text):
    x = r(text)
    assert x.routing_status in (RoutingStatus.UNSUPPORTED, RoutingStatus.AMBIGUOUS, RoutingStatus.INVALID_COMMAND) and x.command is None and not x.routed


# =========================================================================================
# C. owner-approved policy decisions A-E
# =========================================================================================

@pytest.mark.parametrize("text", ["what is humidity", "what's humidity", "what is relative humidity", "what is temperature", "what is brightness", "what is light level"])
def test_decision_a_a_bare_sensor_definition_is_ambiguous(text):
    assert outcome(text)[0] is RoutingStatus.AMBIGUOUS and r(text).category is None and r(text).command is None


@pytest.mark.parametrize("text", ["what is the lux", "how many people are there", "what class is on today"])
def test_decision_b_the_three_phrases_stay_unsupported(text):
    assert r(text).routing_status is RoutingStatus.UNSUPPORTED and r(text).category is None


@pytest.mark.parametrize("text", ["how many students are present", "how many students are present today", "how many students are present in the lecture hall",
                                  "how many people are there in the world", "how many people are there"])
def test_decision_c_present_needs_an_explicit_physical_location(text):
    assert r(text).routing_status is RoutingStatus.UNSUPPORTED


@pytest.mark.parametrize("text", ["how many students are present in the room", "how many students are present in this room", "how many people are physically in the room"])
def test_decision_c_with_an_explicit_location_the_count_is_the_detected_count(text):
    x = r(text)
    assert x.category is IntentCategory.OCCUPANCY_QUERY and x.query_family == "ROOM_PEOPLE_COUNT"


def test_decision_c_expected_attendance_is_never_the_detected_count():
    from evaluation.week8_intent_eval import simulated_answer_fixtures
    ctx = simulated_answer_fixtures()
    detected = rt.answer_query(r("how many students are in class"), ctx["SCHEDULE_NO_STATE"])         # schedule says 30 expected, no classroom state
    assert detected.status is AnswerStatus.INFORMATION_UNAVAILABLE and "30" not in detected.text
    expected = rt.answer_query(r("how many students are expected"), ctx["FRESH"])                     # classroom state says 17 detected, no schedule
    assert expected.status is AnswerStatus.INFORMATION_UNAVAILABLE and "17" not in expected.text
    assert r("how many students are expected").category is IntentCategory.GENERAL_AI and r("how many students are expected").query_family == "LECTURE_EXPECTED_ATTENDANCE"


@pytest.mark.parametrize("text", ["is the professor in the room", "is the professor present", "is the professor in the building", "is the lecturer in the classroom"])
def test_decision_d_professor_presence_is_not_professor_arrival(text):
    x = r(text)
    assert x.query_family != "PROFESSOR_ARRIVAL" and x.routing_status is RoutingStatus.UNSUPPORTED


def test_decision_d_the_arrival_question_is_still_answered_only_from_an_arrival_source():
    from evaluation.week8_intent_eval import simulated_answer_fixtures
    ctx = simulated_answer_fixtures()
    x = r("has the professor arrived yet")
    assert x.query_family == "PROFESSOR_ARRIVAL"
    a = rt.answer_query(x, ctx["FRESH"])                                                              # someone IS present in the state; that is not arrival
    assert a.status is AnswerStatus.INFORMATION_UNAVAILABLE


@pytest.mark.parametrize("text", ["what class is on today", "what time does the next class start tomorrow", "what class is on now today"])
def test_today_is_not_now(text):
    assert r(text).routing_status is RoutingStatus.UNSUPPORTED
    assert r("what lecture is scheduled now").query_family == "LECTURE_CURRENT"


def test_decision_e_the_v1_gold_labels_were_not_edited_in_place(v1):
    by = {e.id: e for e in v1.entries}
    assert (by["D-OQ-001"].expected_outcome, by["D-OQ-001"].expected_category) == ("ROUTED", "OCCUPANCY_QUERY")
    assert (by["D-GA-004"].expected_outcome, by["D-GA-004"].expected_category) == ("ROUTED", "GENERAL_AI")


# =========================================================================================
# D. measurement questions about THIS room stay sensor queries; educational questions are classified but not answered
# =========================================================================================

@pytest.mark.parametrize("text,fam", [("what is the current humidity", "ROOM_HUMIDITY"), ("how hot is it in the room", "ROOM_TEMPERATURE"),
                                      ("how bright is it in the room", "ROOM_BRIGHTNESS"), ("what is the temperature right now", "ROOM_TEMPERATURE"),
                                      ("what is the light level in this room", "ROOM_BRIGHTNESS"), ("tell me the room humidity", "ROOM_HUMIDITY")])
def test_measurement_questions_about_this_room_remain_sensor_queries(text, fam):
    x = r(text)
    assert x.category is IntentCategory.SENSOR_QUERY and x.query_family == fam and x.handling is Handling.ANSWERING_FOUNDATION


@pytest.mark.parametrize("text", ["explain how humidity works", "explain what temperature means in physics", "what is a lux in photography", "why does humidity affect plants",
                                  "how does temperature affect learning", "what does humidity mean", "explain how a thermostat works", "define relative humidity"])
def test_educational_sensor_topic_questions_are_general_ai_without_a_backend(text):
    x = r(text)
    assert x.category is IntentCategory.GENERAL_AI and x.handling is Handling.NO_BACKEND_APPROVED and x.query_family is None and x.command is None
    with pytest.raises(Exception):
        rt.answer_query(x, None)                                                                     # never reaches the answering engine: nothing is generated


@pytest.mark.parametrize("text", ["explain the temperature in the room", "explain the temperature", "describe the humidity here", "explain how many people are in the room",
                                  "how does the room temperature get controlled", "what does the measured light level mean", "explain the current humidity",
                                  "describe how many students are present", "explain how humid it is right now", "why does the humidity in here feel high"])
def test_the_educational_frame_never_reinterprets_a_measurement_attendance_or_arrival_context(text):
    x = r(text)
    assert x.category is not IntentCategory.GENERAL_AI or x.handling is not Handling.NO_BACKEND_APPROVED, text
    assert x.routing_status is RoutingStatus.UNSUPPORTED


@pytest.mark.parametrize("text", ["what is the temperature of the sun", "how hot is it in paris", "how cold is it outside", "how bright is it outside",
                                  "what is the current humidity outside", "tell me the temperature in london", "what is the temperature right now in tokyo",
                                  "what is the humidity of mars", "what is the temperature in the corridor"])
def test_other_places_are_never_a_room_reading(text):
    x = r(text)
    assert x.routing_status is RoutingStatus.UNSUPPORTED and x.query_family is None


def test_no_general_question_is_ever_sent_to_an_unapproved_backend():
    for text in ("explain gravity", "tell me a joke", "explain how humidity works", "what is a lux in photography"):
        x = r(text)
        assert x.category is IntentCategory.GENERAL_AI and x.handling is Handling.NO_BACKEND_APPROVED
    for e in ev.load_manifest(V2).entries:
        x = r(e.text)
        assert x.handling is not Handling.NONE or not x.routed


# =========================================================================================
# E. AI-1 questions and collisions
# =========================================================================================

def test_all_ai_1_questions_and_their_variants_keep_the_approved_routing():
    oracle = json.loads((REPO / "evaluation" / "ai_question_test_set_v1.json").read_text(encoding="utf-8"))
    expected = {"Q1": "OCCUPANCY_QUERY", "Q2": "SENSOR_QUERY", "Q3": "SENSOR_QUERY", "Q4": "SENSOR_QUERY", **{f"Q{n}": "GENERAL_AI" for n in range(5, 11)}}
    count = 0
    for q in oracle["questions"]:
        for phrase in [q["question"]] + list(q["accepted_variants"]):
            x = r(phrase)
            assert x.routing_status is RoutingStatus.ROUTED and x.category.value == expected[q["id"]] and x.handling is Handling.ANSWERING_FOUNDATION, (q["id"], phrase)
            count += 1
    assert len(oracle["questions"]) == 10 and count >= 10


def test_no_phrase_belongs_to_two_tables_or_two_families():
    assert not set(rt._EXACT) & set(rt._COMMAND_EXACT)
    assert not set(rt._EXACT) & set(rt._AMBIGUOUS_EXACT)
    assert not set(rt._COMMAND_EXACT) & set(rt._AMBIGUOUS_EXACT)
    for phrase in list(rt._EXACT) + list(rt._COMMAND_EXACT) + list(rt._AMBIGUOUS_EXACT):
        assert rt.normalize(phrase) == phrase, phrase
        assert len(rt._matches(phrase)) == 1, phrase                          # exactly one rule claims a table phrase: no family collision


def test_every_table_phrase_still_routes_to_exactly_one_outcome_after_politeness():
    for phrase in list(rt._EXACT):
        assert r(phrase).routing_status is RoutingStatus.ROUTED
    for phrase in list(rt._COMMAND_EXACT):
        assert r("please " + phrase).routing_status is RoutingStatus.ROUTED and r(phrase.upper() + "!").routing_status is RoutingStatus.ROUTED


# =========================================================================================
# F. the v2 corpus and its evaluator support
# =========================================================================================

def test_the_v2_corpus_is_labelled_as_development_regression_evidence_not_heldout(v2):
    data = json.loads(V2.read_text(encoding="utf-8"))
    assert data["schema_version"] == ev.MANIFEST_SCHEMA_V2 and data["phrase_table_snapshot"] == "task4" and v2.snapshot == "task4"
    assert "NOT independent" in data["banner"] and "NOT held out" in data["banner"] and "NOT MEASURED" in data["banner"]
    assert "HELDOUT" not in json.dumps(data) and all(e["source_type"] != "REAL_STT_REVIEWED" for e in data["entries"])
    assert len(v2.entries) == 173 and not v2.has_real and len(v2.corrections) == 8


def test_the_v2_results_are_pinned_including_the_two_known_gaps(v2_report):
    s = v2_report["strata"]
    assert v2_report["strata_sizes"] == {"SPEC_TABLE": 108, "SPEC_AUTHORED": 0, "DEV_AUTHORED": 65, "REAL_STT_REVIEWED": 0}
    assert "SPEC_AUTHORED" not in s and "REAL_STT_REVIEWED" not in s                                      # an empty stratum is absent, never reported as 0 % or 100 %
    a = s["ALL"]
    assert (a["category_accuracy"]["numerator"], a["category_accuracy"]["denominator"]) == (109, 109)
    assert (a["routing_outcome_agreement"]["numerator"], a["routing_outcome_agreement"]["denominator"]) == (171, 173)
    assert [m["id"] for m in a["mismatches"]] == ["K-001", "K-002"]                                   # the two KNOWN GAPS (pre-existing Task 1 behaviour), reported, not hidden
    assert a["command_safety"]["false_command_acceptance"]["numerator"] == 0 and a["command_safety"]["false_command_acceptance"]["denominator"] == 164
    assert a["command_safety"]["invariant_violations"]["count"] == 0 and a["answer_check"]["violations"]["count"] == 0 and ev.safety_violations(v2_report) == 0
    assert v2_report["real_world_intent_accuracy"] == "NOT MEASURED" and v2_report["router"] == "task4-baseline"


def test_the_two_known_gaps_were_the_same_in_the_original_router_and_the_task4_router():
    fr, task4 = ev.load_router("task1-frozen"), history.load_task4_router()
    for text in ("explain why the professor has not arrived", "why does the classroom attendance look low"):
        old, new = fr(IntentRequest(text, "g")), task4(IntentRequest(text, "g"))
        assert (old.routing_status, old.reason_code) == (new.routing_status, new.reason_code) == (RoutingStatus.ROUTED, "GENERAL_AI_NO_BACKEND_APPROVED")
        assert new.handling is Handling.NO_BACKEND_APPROVED                                           # not answered, nothing fabricated


def test_the_v2_corpus_covers_every_new_family_with_answer_fixtures(v2):
    used = {e.answer_fixture for e in v2.entries if e.answer_fixture}
    assert {"FRESH", "NONE", "STALE", "UNCONFIGURED", "NO_HUMIDITY", "NO_TEMPERATURE", "NO_LUX", "NO_VISION", "ZERO_PEOPLE", "SCHEDULE_NO_STATE"} <= used
    texts = {rt.normalize(e.text) for e in v2.entries}
    exact, command, ambiguous = ev.load_phrase_snapshot("task4")
    t1 = ev.load_phrase_snapshot("task1")
    assert all(p in texts for p in (exact - t1[0]) | (command - t1[1]) | ambiguous)                    # every newly accepted phrase is in the corpus


def test_the_report_holds_no_request_text(v2, v2_report):
    blob = json.dumps(v2_report)
    for e in v2.entries:
        if len(e.text) > 14:
            assert e.text not in blob, e.id
    assert v2_report["privacy"] == {"request_text_in_report": False, "answer_text_in_report": False, "pcm_in_report": False}


# ----- evaluator: snapshot provenance and corrections -------------------------------------------------

def _doc(**over):
    base = {"schema_version": ev.MANIFEST_SCHEMA_V2, "corpus_id": "T", "phrase_table_snapshot": "task4", "banner": "b", "notes": "n", "entries": []}
    base.update(over)
    return base


def _entry(eid, text, source):
    return {"id": eid, "text": text, "source_type": source, "split_or_purpose": "SPECIFICATION_CONFORMANCE" if source == "ROUTER_PHRASE_TABLE" else "DEVELOPMENT_AUTHORED",
            "expected_outcome": "UNSUPPORTED", "expected_category": None, "expected_handling": "NONE", "expected_action": None, "expected_target": None,
            "expected_value": None, "expected_unit": None}


def test_provenance_is_judged_against_the_snapshot_the_corpus_names():
    new_phrase = "how hot is it in the room"
    assert ev.is_table_phrase(new_phrase, "task4") and not ev.is_table_phrase(new_phrase, "task1")
    assert ev.is_table_phrase("what is humidity", "task4") and not ev.is_table_phrase("what is humidity", "task1")


def test_a_v2_manifest_needs_a_known_snapshot_and_a_v1_manifest_is_always_task1():
    for bad in ({"phrase_table_snapshot": "task9"}, {"phrase_table_snapshot": None}):
        with pytest.raises(ev.ManifestError):
            ev.parse_manifest(_doc(**bad))
    d = json.loads(V1.read_text(encoding="utf-8"))
    with pytest.raises(ev.ManifestError):
        ev.parse_manifest({**d, "phrase_table_snapshot": "task4"})
    with pytest.raises(ev.ManifestError):
        ev.parse_manifest({**d, "corrections": []})


def test_a_table_member_must_be_declared_a_table_member_under_the_named_snapshot():
    with pytest.raises(ev.ManifestError, match="phrase-table member"):
        ev.parse_manifest(_doc(entries=[_entry("a", "how hot is it in the room", "AUTHORED_DEVELOPMENT")]))
    ev.parse_manifest(_doc(phrase_table_snapshot="task1", entries=[_entry("a", "how hot is it in the room", "AUTHORED_DEVELOPMENT")]))      # under task1 it is authored


def test_corrections_are_validated():
    ok = {"v1_id": "D-1", "v2_id": "a", "change": "x", "reason": "y"}
    ev.parse_manifest(_doc(corrections=[ok], entries=[_entry("a", "what is the lux", "AUTHORED_DEVELOPMENT")]))
    for bad in ([{**ok, "v2_id": "zzz"}], [{**ok, "reason": ""}], [{"v1_id": "D-1"}], "nope"):
        with pytest.raises(ev.ManifestError):
            ev.parse_manifest(_doc(corrections=bad, entries=[_entry("a", "what is the lux", "AUTHORED_DEVELOPMENT")]))


def test_the_cli_router_option_is_limited_to_the_two_known_routers():
    assert ev.ROUTERS == ("current", "task1-frozen")
    with pytest.raises(ev.EvalError):
        ev.load_router("other")
    assert ev.load_router("current") is rt.route_intent
