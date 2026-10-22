"""Week 8 Task 2: the text-level intent EVALUATION harness (evaluation/week8_intent_eval.py) and its versioned corpus.

SOFTWARE-VALIDATED. These tests check the harness arithmetic against hand-computed values, the manifest contract, the provenance rules and the
safety checks. They say nothing about speech recognition or real-world intent accuracy: the versioned corpus is text written by hand, and the
phrases taken from the router's own tables measure SPECIFICATION CONFORMANCE only.
"""

import ast
import copy
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant.answering import AnswerFact, AnswerResult, AnswerSource, AnswerStatus, Intent as AnsweringIntent
from evaluation import week8_intent_eval as ev
from evaluation.week8_intent_eval import (
    DEFAULT_MANIFEST, Entry, EvalError, ItemResult, Manifest, ManifestError, build_report, compute_metrics, evaluate, evaluate_entry, is_table_phrase,
    load_manifest, parse_manifest, ratio, safety_violations, write_report,
)
from intents import router as rt
from intents.contracts import IntentCategory, IntentRequest

REPO = Path(__file__).resolve().parent.parent
SECRET = "zebra quartz marmalade"
TEMPLATE = REPO / "evaluation" / "week8_intent_real_stt_manifest.template.json"
CC_ON = ("CLASSROOM_CONTROL", "TURN_ON", "CLASSROOM_LIGHTS", None, None)
CC_OFF = ("CLASSROOM_CONTROL", "TURN_OFF", "CLASSROOM_LIGHTS", None, None)


@pytest.fixture(scope="module")
def manifest_data():
    return json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(DEFAULT_MANIFEST)


@pytest.fixture(scope="module")
def report(manifest):
    # The Task 2 numbers pinned below are the ORIGINAL Task 1 router's. They are reproduced with its byte-identical frozen copy so that they stay true
    # after Week 8 Task 4 enlarged the live router (the live router's results on this same corpus are pinned in tests/test_week8_task4_routing.py).
    return evaluate(manifest, route=ev.load_router("task1-frozen"), router_name="task1-frozen")


class _Logs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    h = _Logs()
    ev.logger.addHandler(h)
    yield h.lines
    ev.logger.removeHandler(h)


# =========================================================================================
# A. the shipped corpus
# =========================================================================================

def test_the_versioned_manifest_is_valid_and_has_the_frozen_size(manifest):
    assert manifest.corpus_id == "W8-INTENT-V1" and not manifest.template and not manifest.has_real
    sizes = {s: sum(1 for e in manifest.entries if e.stratum == s) for s in ev.STRATA}
    assert sizes == {"SPEC_TABLE": 112, "SPEC_AUTHORED": 133, "DEV_AUTHORED": 35, "REAL_STT_REVIEWED": 0} and len(manifest.entries) == 280


def test_the_corpus_has_no_real_data_and_no_held_out_label(manifest_data):
    assert all(e["source_type"] != "REAL_STT_REVIEWED" for e in manifest_data["entries"])
    assert {e["split_or_purpose"] for e in manifest_data["entries"]} == {"SPECIFICATION_CONFORMANCE", "DEVELOPMENT_AUTHORED"}
    assert "HELDOUT" not in json.dumps(manifest_data["entries"]) and "real_stt" not in json.dumps(manifest_data["entries"])
    assert "NOT MEASURED" in manifest_data["banner"] and "NOT independent" in manifest_data["banner"]


def test_provenance_is_mechanically_true_for_every_entry(manifest):
    for e in manifest.entries:
        assert is_table_phrase(e.text) == (e.source_type == "ROUTER_PHRASE_TABLE"), e.id


def test_every_phrase_of_the_router_tables_is_in_the_corpus(manifest):
    # The v1 corpus is frozen and covers the ORIGINAL Task 1 tables (the pinned "task1" snapshot), not the tables enlarged by Week 8 Task 4.
    texts = {rt.normalize(e.text) for e in manifest.entries}
    exact, command, _ = ev.load_phrase_snapshot("task1")
    assert [p for p in sorted(exact) + sorted(command) if p not in texts] == []


def test_all_six_categories_and_all_four_rejection_outcomes_are_covered(manifest):
    assert {e.expected_category for e in manifest.entries if e.expected_outcome == "ROUTED"} == {c.value for c in IntentCategory}
    assert {e.expected_outcome for e in manifest.entries} == {"ROUTED", "INVALID_COMMAND", "UNSUPPORTED", "AMBIGUOUS", "INVALID_INPUT"}
    assert all(e.expected_category is None for e in manifest.entries if e.expected_outcome in ("UNSUPPORTED", "AMBIGUOUS", "INVALID_INPUT"))
    assert "NO_CATEGORY" not in {e.expected_category for e in manifest.entries} and len(ev.CATEGORY_LABELS) == 6


def test_the_adversarial_families_the_task_asks_for_are_present(manifest):
    def tagged(tag):
        return [e for e in manifest.entries if tag in e.tags]
    assert len(tagged("NEGATIVE_NUMBER")) >= 3 and all(e.expected_outcome == "INVALID_COMMAND" for e in tagged("NEGATIVE_NUMBER"))
    assert len(tagged("DECIMAL")) >= 3 and all(e.expected_outcome == "INVALID_COMMAND" for e in tagged("DECIMAL"))
    assert len(tagged("PERCENT_SIGN")) >= 3 and len(tagged("POLITE")) >= 6 and len(tagged("PUNCTUATION")) >= 4
    assert len(tagged("REPEATED")) >= 4 and len(tagged("CONFLICTING")) >= 4 and len(tagged("COMPOUND")) >= 4
    assert len(tagged("EXPECTED_VS_DETECTED")) >= 4 and len(tagged("PROFESSOR_VS_PRESENCE")) >= 3
    assert len(tagged("ZONE")) >= 5 and len(tagged("UNSAFE_HARDWARE")) >= 10 and len(tagged("NEAR_MISS")) >= 10
    assert len(tagged("SAFE_EDUCATIONAL_SENSOR_WORD")) == 10


def test_the_answer_fixture_cases_cover_missing_stale_zero_and_unconfigured(manifest):
    used = {e.answer_fixture for e in manifest.entries if e.answer_fixture}
    assert {"NONE", "STALE", "UNCONFIGURED", "ZERO_PEOPLE", "NO_TEMPERATURE", "NO_HUMIDITY", "NO_LUX", "NO_VISION", "SCHEDULE_NO_STATE", "FRESH"} <= used


def test_the_approved_q5_to_q10_mapping_is_what_the_corpus_and_the_router_do(manifest):
    oracle = json.loads((REPO / "evaluation" / "ai_question_test_set_v1.json").read_text(encoding="utf-8"))
    expected = {"Q1": "OCCUPANCY_QUERY", "Q2": "SENSOR_QUERY", "Q3": "SENSOR_QUERY", "Q4": "SENSOR_QUERY", **{f"Q{n}": "GENERAL_AI" for n in range(5, 11)}}
    by_text = {rt.normalize(e.text): e for e in manifest.entries if e.expected_outcome == "ROUTED"}
    for q in oracle["questions"]:
        for phrase in [q["question"]] + list(q["accepted_variants"]):
            entry = by_text[rt.normalize(phrase)]
            assert entry.expected_category == expected[q["id"]] and entry.expected_handling == "ANSWERING_FOUNDATION", (q["id"], phrase)
            actual = rt.route_intent(IntentRequest(phrase, "map"))
            assert actual.category.value == expected[q["id"]] and actual.handling.value == "ANSWERING_FOUNDATION" and actual.query_family is not None


def test_expected_attendance_is_never_occupancy_in_the_gold_or_in_the_result(manifest, report):
    entries = [e for e in manifest.entries if e.expected_query_family == "LECTURE_EXPECTED_ATTENDANCE"]
    assert entries and all(e.expected_category == "GENERAL_AI" for e in entries)
    for it in report["items"]:
        if it["gold_family"] == "LECTURE_EXPECTED_ATTENDANCE":
            assert it["actual_category"] == "GENERAL_AI"


# =========================================================================================
# B. manifest validation
# =========================================================================================

def rejection_entry():
    return {"id": "X-1", "text": "open the garage", "source_type": "AUTHORED_SPECIFICATION", "split_or_purpose": "SPECIFICATION_CONFORMANCE",
            "expected_outcome": "UNSUPPORTED", "expected_category": None, "expected_handling": "NONE", "expected_query_family": None,
            "expected_reason_code": "NO_MATCHING_RULE", "expected_action": None, "expected_target": None, "expected_value": None, "expected_unit": None,
            "answer_fixture": None, "expected_answer_status": None, "expected_answer_contains": [], "expected_answer_excludes": [], "tags": [], "notes": ""}


def command_entry():
    e = rejection_entry()
    e.update(id="X-2", text="turn on the lights", expected_outcome="ROUTED", expected_category="CLASSROOM_CONTROL", expected_handling="EXECUTION_NOT_IMPLEMENTED",
             expected_reason_code=None, expected_action="TURN_ON", expected_target="CLASSROOM_LIGHTS")
    return e


def doc(*entries):
    return {"schema_version": ev.MANIFEST_SCHEMA, "corpus_id": "T", "entries": list(entries)}


def test_a_minimal_valid_manifest_parses():
    m = parse_manifest(doc(rejection_entry(), command_entry()))
    assert len(m.entries) == 2 and m.entries[1].expected_command == CC_ON


def mutate(base, **changes):
    e = base()
    e.update(changes)
    return e


BAD = [
    ("unknown key", mutate(rejection_entry, surprise=1)),
    ("duplicate id handled below", None),
    ("bad id", mutate(rejection_entry, id="bad id!")),
    ("non-string text", mutate(rejection_entry, text=5)),
    ("text too long", mutate(rejection_entry, text="a" * 1001)),
    ("unknown source type", mutate(rejection_entry, source_type="MADE_UP")),
    ("purpose does not match source", mutate(rejection_entry, split_or_purpose="DEVELOPMENT_AUTHORED")),
    ("heldout in the versioned corpus", mutate(rejection_entry, split_or_purpose="HELDOUT")),
    ("claims the phrase table but is not in it", mutate(rejection_entry, source_type="ROUTER_PHRASE_TABLE")),
    ("is a table phrase but claims to be authored", mutate(rejection_entry, text="mute")),
    ("is a table phrase but claims to be development", mutate(rejection_entry, text="what is the temperature", source_type="AUTHORED_DEVELOPMENT", split_or_purpose="DEVELOPMENT_AUTHORED")),
    ("a rejected request with a category", mutate(rejection_entry, expected_category="SENSOR_QUERY")),
    ("UNSUPPORTED used as a category", mutate(rejection_entry, expected_category="UNSUPPORTED")),
    ("NO_CATEGORY used as a category", mutate(rejection_entry, expected_category="NO_CATEGORY")),
    ("a rejected request with a handling", mutate(rejection_entry, expected_handling="ANSWERING_FOUNDATION")),
    ("an unknown outcome", mutate(rejection_entry, expected_outcome="MAYBE")),
    ("a routed entry without a category", mutate(command_entry, expected_category=None)),
    ("a routed entry without a handling", mutate(command_entry, expected_handling="NONE")),
    ("a routed command with the wrong handling", mutate(command_entry, expected_handling="ANSWERING_FOUNDATION")),
    ("a gold command that is itself invalid", mutate(command_entry, expected_value=500, expected_unit="PERCENT")),
    ("a gold command with an unknown action", mutate(command_entry, expected_action="SELF_DESTRUCT")),
    ("a gold command missing its target", mutate(command_entry, expected_target=None)),
    ("a command field on a rejection", mutate(rejection_entry, expected_action="TURN_ON")),
    ("an invalid command without a command category", mutate(rejection_entry, expected_outcome="INVALID_COMMAND")),
    ("a query family on a command", mutate(command_entry, expected_query_family="ROOM_TEMPERATURE")),
    ("an unknown tag", mutate(rejection_entry, tags=["WISHFUL"])),
    ("a fixture without a status", mutate(rejection_entry, answer_fixture="NONE")),
    ("an unknown fixture", mutate(command_entry, answer_fixture="MARS", expected_answer_status="ANSWERED")),
    ("an answer check on a command", mutate(command_entry, answer_fixture="NONE", expected_answer_status="ANSWERED")),
    ("answer text checks without a fixture", mutate(rejection_entry, expected_answer_contains=["x"])),
    ("tags that are not a list", mutate(rejection_entry, tags="ADVERSARIAL")),
]


@pytest.mark.parametrize("name,entry", [b for b in BAD if b[1] is not None], ids=[b[0] for b in BAD if b[1] is not None])
def test_an_invalid_entry_is_rejected_before_anything_is_evaluated(name, entry):
    with pytest.raises(ManifestError):
        parse_manifest(doc(entry))


def test_a_missing_key_a_duplicate_id_and_a_bad_envelope_are_rejected():
    e = rejection_entry()
    del e["expected_outcome"]
    for bad in (doc(e), doc(rejection_entry(), rejection_entry()), {"schema_version": "other", "entries": []}, doc() | {"corpus_id": ""}, {**doc(), "entries": "x"}, "text", None):
        with pytest.raises(ManifestError):
            parse_manifest(bad)


def test_the_error_message_names_the_entry_but_never_echoes_the_text():
    bad = mutate(rejection_entry, text=SECRET, expected_category="SENSOR_QUERY")
    with pytest.raises(ManifestError) as info:
        parse_manifest(doc(bad))
    assert "X-1" in str(info.value) and SECRET not in str(info.value)


def test_an_unreadable_manifest_file_is_a_clean_error(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(p)
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "missing.json")


# =========================================================================================
# C. real STT entries and the template (external data only)
# =========================================================================================

def real_entry(**real_changes):
    e = command_entry()
    e.update(id="REAL-1", source_type="REAL_STT_REVIEWED", split_or_purpose="DEV",
             real_stt={"consent_reference": "CONSENT-R17", "reviewed_by_human": True, "stt_engine_id": "test-engine", "recording_ref": "REC-0001"})
    e["real_stt"].update(real_changes)
    return e


def test_a_reviewed_real_entry_is_accepted_only_outside_the_repository(tmp_path):
    data = doc(real_entry())
    assert parse_manifest(data, where_real_ok=True).has_real
    with pytest.raises(ManifestError):
        parse_manifest(data)                                           # the default is the repository: refused
    p = tmp_path / "real.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert load_manifest(p).has_real                                    # a path outside the repository is allowed
    assert ev._inside_repo(REPO / "evaluation" / "x.json") and not ev._inside_repo(p)


@pytest.mark.parametrize("changes", [dict(reviewed_by_human=False), dict(reviewed_by_human="yes"), dict(consent_reference="UNKNOWN"), dict(consent_reference="Alice"),
                                     dict(consent_reference="CONSENT-PLACEHOLDER"), dict(consent_reference=None), dict(stt_engine_id=""), dict(recording_ref=7)])
def test_a_real_entry_needs_human_review_and_a_consent_reference(changes):
    with pytest.raises(ManifestError):
        parse_manifest(doc(real_entry(**changes)), where_real_ok=True)


def test_real_entries_need_their_extra_block_and_only_they_may_have_it():
    no_block = real_entry()
    del no_block["real_stt"]
    with pytest.raises(ManifestError):
        parse_manifest(doc(no_block), where_real_ok=True)
    with pytest.raises(ManifestError):
        parse_manifest(doc(mutate(rejection_entry, real_stt={"x": 1})))
    held = real_entry()
    held["split_or_purpose"] = "HELDOUT"
    assert parse_manifest(doc(held), where_real_ok=True).entries[0].split_or_purpose == "HELDOUT"
    with pytest.raises(ManifestError):
        parse_manifest(doc(mutate(real_entry, split_or_purpose="SPECIFICATION_CONFORMANCE")), where_real_ok=True)


def test_a_real_transcript_may_equal_a_table_phrase_and_stays_real():
    entry = mutate(real_entry, text="mute", expected_category="LOCAL_COMMAND", expected_action="MUTE", expected_target="SPEAKER_OUTPUT")
    assert is_table_phrase("mute")
    m = parse_manifest(doc(entry), where_real_ok=True)
    assert m.entries[0].stratum == "REAL_STT_REVIEWED" and m.entries[0].expected_command[1] == "MUTE"


def test_the_template_is_a_template_without_data_and_cannot_be_evaluated():
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert data["template"] is True and data["corpus_id"].startswith("TEMPLATE-") and "NOT MEASURED" in data["banner"]
    assert all(e["id"].startswith("TEMPLATE-") for e in data["entries"])
    with pytest.raises(ManifestError):
        load_manifest(TEMPLATE)
    m = load_manifest(TEMPLATE, allow_template=True)
    assert m.template and m.entries == ()
    with pytest.raises(EvalError):
        evaluate(m)
    assert "<" in json.dumps(data["entries"])                           # placeholders only: no invented transcript
    assert not (set(data["entries"][0]) - ev.BASE_KEYS - ev.REAL_KEYS)


def test_real_entries_form_their_own_stratum_and_the_accuracy_claim_changes_only_then():
    m = parse_manifest(doc(real_entry(), mutate(real_entry, id="REAL-2", text="open the garage", expected_outcome="UNSUPPORTED", expected_category=None,
                                                  expected_handling="NONE", expected_action=None, expected_target=None, expected_reason_code=None)), where_real_ok=True)
    r = evaluate(m)
    assert r["strata_sizes"]["REAL_STT_REVIEWED"] == 2 and r["independent_examples"] == 2
    assert r["real_world_intent_accuracy"].startswith("MEASURED ON 2 HUMAN-REVIEWED REAL STT TRANSCRIPTS")
    assert r["strata"]["REAL_STT_REVIEWED"]["category_accuracy"]["denominator"] == 1 and r["specification_conformance"]["n_items"] == 0


def test_the_shipped_corpus_reports_that_real_world_accuracy_is_not_measured(report):
    assert report["real_world_intent_accuracy"] == "NOT MEASURED" and report["independent_examples"] == 0
    assert "REAL_STT_REVIEWED" not in report["strata"] and report["strata_sizes"]["REAL_STT_REVIEWED"] == 0
    assert "NOT independent" in report["development_probe"]["label"] and "NOT independent classification accuracy" in report["specification_conformance"]["note"]


# =========================================================================================
# D. the arithmetic, against hand-computed values
# =========================================================================================

def IR(id, g_out, g_cat, g_hand, a_out, a_cat, a_hand, g_fam=None, a_fam=None, g_cmd=None, a_cmd=None, a_reason="X", tags=(), stratum="SPEC_AUTHORED", inv=()):
    return ItemResult(id=id, stratum=stratum, gold_outcome=g_out, gold_category=g_cat, gold_handling=g_hand, gold_family=g_fam, gold_command=g_cmd,
                      actual_outcome=a_out, actual_category=a_cat, actual_handling=a_hand, actual_family=a_fam, actual_reason=a_reason, actual_command=a_cmd,
                      invariant_violations=inv, tags=tags)


ANS, EXEC, NB = "ANSWERING_FOUNDATION", "EXECUTION_NOT_IMPLEMENTED", "NO_BACKEND_APPROVED"
HAND = [
    IR("1", "ROUTED", "SENSOR_QUERY", ANS, "ROUTED", "SENSOR_QUERY", ANS, "ROOM_TEMPERATURE", "ROOM_TEMPERATURE"),
    IR("2", "ROUTED", "SENSOR_QUERY", ANS, "ROUTED", "OCCUPANCY_QUERY", ANS, "ROOM_TEMPERATURE", "ROOM_PEOPLE_COUNT"),
    IR("3", "ROUTED", "OCCUPANCY_QUERY", ANS, "ROUTED", "OCCUPANCY_QUERY", ANS, "ROOM_PEOPLE_COUNT", "ROOM_PEOPLE_COUNT"),
    IR("4", "ROUTED", "CLASSROOM_CONTROL", EXEC, "ROUTED", "CLASSROOM_CONTROL", EXEC, g_cmd=CC_ON, a_cmd=CC_ON),
    IR("5", "ROUTED", "CLASSROOM_CONTROL", EXEC, "ROUTED", "CLASSROOM_CONTROL", EXEC, g_cmd=CC_ON, a_cmd=CC_OFF),
    IR("6", "UNSUPPORTED", None, "NONE", "UNSUPPORTED", None, "NONE"),
    IR("7", "UNSUPPORTED", None, "NONE", "ROUTED", "CLASSROOM_CONTROL", EXEC, a_cmd=CC_ON),
    IR("8", "AMBIGUOUS", None, "NONE", "UNSUPPORTED", None, "NONE"),
    IR("9", "ROUTED", "GENERAL_AI", NB, "UNSUPPORTED", None, "NONE", a_reason=ev.GUARD_REASON, tags=("SAFE_EDUCATIONAL_SENSOR_WORD",)),
    IR("10", "INVALID_COMMAND", "LOCAL_COMMAND", "NONE", "INVALID_COMMAND", "LOCAL_COMMAND", "NONE"),
]


def approx(x, y):
    return x == pytest.approx(y, abs=1e-9)


def test_category_accuracy_precision_recall_f1_and_macro_f1_match_the_hand_computation():
    m = compute_metrics(HAND)
    assert m["n_items"] == 10
    ca = m["category_accuracy"]                                          # eligible = gold ROUTED (items 1,2,3,4,5,9); correct = 1,3,4,5
    assert (ca["numerator"], ca["denominator"]) == (4, 6) and approx(ca["value"], 4 / 6)
    pc = m["per_category"]
    assert (pc["SENSOR_QUERY"]["true_positives"], pc["SENSOR_QUERY"]["predicted"], pc["SENSOR_QUERY"]["support"]) == (1, 1, 2)
    assert approx(pc["SENSOR_QUERY"]["precision"]["value"], 1.0) and approx(pc["SENSOR_QUERY"]["recall"]["value"], 0.5) and approx(pc["SENSOR_QUERY"]["f1"]["value"], 2 / 3)
    assert approx(pc["OCCUPANCY_QUERY"]["precision"]["value"], 0.5) and approx(pc["OCCUPANCY_QUERY"]["recall"]["value"], 1.0) and approx(pc["OCCUPANCY_QUERY"]["f1"]["value"], 2 / 3)
    assert (pc["CLASSROOM_CONTROL"]["true_positives"], pc["CLASSROOM_CONTROL"]["predicted"], pc["CLASSROOM_CONTROL"]["support"]) == (2, 3, 2)
    assert approx(pc["CLASSROOM_CONTROL"]["precision"]["value"], 2 / 3) and approx(pc["CLASSROOM_CONTROL"]["recall"]["value"], 1.0) and approx(pc["CLASSROOM_CONTROL"]["f1"]["value"], 0.8)
    gai = pc["GENERAL_AI"]                                               # support 1, never predicted: precision undefined, recall 0, F1 undefined
    assert gai["precision"]["value"] is None and gai["precision"]["denominator"] == 0 and approx(gai["recall"]["value"], 0.0) and gai["f1"]["value"] is None
    for untouched in ("LOCAL_COMMAND", "SMART_HOME"):                    # no gold, no prediction: everything undefined, nothing is 0 or 1
        assert pc[untouched]["precision"]["value"] is None and pc[untouched]["recall"]["value"] is None and pc[untouched]["f1"]["value"] is None
    assert m["macro_f1"]["categories_included"] == 3 and m["macro_f1"]["categories_total"] == 6
    assert approx(m["macro_f1"]["value"], (2 / 3 + 2 / 3 + 0.8) / 3)


def test_the_confusion_matrices_match_the_hand_computation():
    m = compute_metrics(HAND)["confusion_matrix"]["counts"]
    assert m["SENSOR_QUERY"]["SENSOR_QUERY"] == 1 and m["SENSOR_QUERY"]["OCCUPANCY_QUERY"] == 1
    assert m["OCCUPANCY_QUERY"]["OCCUPANCY_QUERY"] == 1 and m["CLASSROOM_CONTROL"]["CLASSROOM_CONTROL"] == 2
    assert m["GENERAL_AI"]["NO_CATEGORY"] == 1 and m["NO_CATEGORY"]["CLASSROOM_CONTROL"] == 1
    assert m["NO_CATEGORY"]["NO_CATEGORY"] == 3                          # items 6, 8, 10 (INVALID_COMMAND counts as not routed)
    assert sum(sum(row.values()) for row in m.values()) == 10
    o = compute_metrics(HAND)["outcome_confusion_matrix"]["counts"]
    assert o["UNSUPPORTED"] == {"ROUTED": 1, "INVALID_COMMAND": 0, "UNSUPPORTED": 1, "AMBIGUOUS": 0, "INVALID_INPUT": 0}
    assert o["AMBIGUOUS"]["UNSUPPORTED"] == 1 and o["INVALID_COMMAND"]["INVALID_COMMAND"] == 1 and o["ROUTED"]["UNSUPPORTED"] == 1


def test_rejection_outcome_handling_family_and_invalid_command_figures_match():
    m = compute_metrics(HAND)
    rj = m["rejection"]                                                  # gold rejected: 6,7,8,10; actual rejected: 6,8,9,10; both: 6,8,10
    assert (rj["precision"]["numerator"], rj["precision"]["denominator"]) == (3, 4) and (rj["recall"]["numerator"], rj["recall"]["denominator"]) == (3, 4)
    assert (rj["exact_outcome_agreement"]["numerator"], rj["exact_outcome_agreement"]["denominator"]) == (2, 4)
    assert (m["routing_outcome_agreement"]["numerator"], m["routing_outcome_agreement"]["denominator"]) == (7, 10)
    assert (m["handling_agreement"]["numerator"], m["handling_agreement"]["denominator"]) == (8, 10)
    assert (m["query_family_agreement"]["numerator"], m["query_family_agreement"]["denominator"]) == (2, 3)
    assert (m["invalid_command_category_agreement"]["numerator"], m["invalid_command_category_agreement"]["denominator"]) == (1, 1)
    assert {x["id"] for x in m["mismatches"]} == {"2", "5", "7", "8", "9"}
    c = m["counts"]
    assert (c["gold_unsupported"], c["actual_unsupported"], c["gold_ambiguous"], c["actual_ambiguous"]) == (2, 3, 1, 0)
    assert c["gold_by_category"] == {"LOCAL_COMMAND": 0, "SENSOR_QUERY": 2, "OCCUPANCY_QUERY": 1, "CLASSROOM_CONTROL": 2, "SMART_HOME": 0, "GENERAL_AI": 1}


def test_command_safety_figures_match_and_false_acceptance_is_its_own_metric():
    cs = compute_metrics(HAND)["command_safety"]
    fa = cs["false_command_acceptance"]                                  # items without a gold command: 1,2,3,6,7,8,9,10 (8); accepted a command: 7
    assert (fa["numerator"], fa["denominator"], fa["ids"]) == (1, 8, ["7"])
    assert (cs["wrong_command_accepted"]["numerator"], cs["wrong_command_accepted"]["denominator"], cs["wrong_command_accepted"]["ids"]) == (1, 2, ["5"])
    assert cs["gold_command_not_produced"]["numerator"] == 0
    assert (cs["exact_command_agreement_of_gold_commands"]["numerator"], cs["exact_command_agreement_of_gold_commands"]["denominator"]) == (1, 2)
    assert (cs["exact_command_agreement_of_produced_commands"]["numerator"], cs["exact_command_agreement_of_produced_commands"]["denominator"]) == (1, 2)
    pf = cs["per_field_agreement_where_both_exist"]
    assert [(pf[k]["numerator"], pf[k]["denominator"]) for k in ("category", "action", "target", "value", "unit")] == [(2, 2), (1, 2), (2, 2), (2, 2), (2, 2)]
    assert (cs["rejected_requests_that_produced_a_command"]["numerator"], cs["rejected_requests_that_produced_a_command"]["denominator"]) == (0, 4)


def test_false_rejection_figures_match():
    fr = compute_metrics(HAND)["false_rejection"]
    assert (fr["overall"]["numerator"], fr["overall"]["denominator"], fr["overall"]["ids"]) == (1, 6, ["9"])
    assert fr["by_actual_reason_code"] == {ev.GUARD_REASON: 1} and fr["sensor_topic_guard"]["numerator"] == 1
    assert (fr["safe_educational_sensor_word_questions_rejected"]["numerator"], fr["safe_educational_sensor_word_questions_rejected"]["denominator"]) == (1, 1)


def test_a_missed_command_is_counted_and_a_perfect_run_is_perfect():
    miss = [IR("m", "ROUTED", "CLASSROOM_CONTROL", EXEC, "UNSUPPORTED", None, "NONE", g_cmd=CC_ON)]
    cs = compute_metrics(miss)["command_safety"]
    assert cs["gold_command_not_produced"]["numerator"] == 1 and cs["exact_command_agreement_of_gold_commands"]["value"] == 0.0
    assert cs["exact_command_agreement_of_produced_commands"]["value"] is None            # nothing was produced: undefined, not 0 and not 1
    perfect = [h for h in HAND if h.id in ("1", "3", "4", "6", "10")]
    m = compute_metrics(perfect)
    assert m["category_accuracy"]["value"] == 1.0 and m["routing_outcome_agreement"]["value"] == 1.0 and m["mismatches"] == []
    assert m["command_safety"]["false_command_acceptance"]["value"] == 0.0 and m["command_safety"]["false_command_acceptance"]["denominator"] == 4


def test_zero_denominators_are_undefined_never_zero_or_one():
    r = ratio(0, 0, "things")
    assert r["value"] is None and r["denominator"] == 0 and r["undefined_reason"] == "no things"
    assert ratio(3, 4, "x")["value"] == 0.75 and ratio(0, 5, "x")["value"] == 0.0 and ratio(5, 5, "x")["value"] == 1.0


def test_an_empty_evaluation_is_all_undefined():
    m = compute_metrics([])
    assert m["n_items"] == 0 and m["category_accuracy"]["value"] is None and m["routing_outcome_agreement"]["value"] is None
    assert m["macro_f1"]["value"] is None and m["macro_f1"]["categories_included"] == 0
    assert all(v["precision"]["value"] is None and v["f1"]["value"] is None for v in m["per_category"].values())
    assert m["command_safety"]["false_command_acceptance"]["value"] is None and m["rejection"]["precision"]["value"] is None
    assert m["mismatches"] == [] and sum(sum(r.values()) for r in m["confusion_matrix"]["counts"].values()) == 0
    rep = build_report(Manifest("E", False, (), False), [])
    assert rep["strata"]["ALL"]["n_items"] == 0 and "SPEC_TABLE" not in rep["strata"] and rep["real_world_intent_accuracy"] == "NOT MEASURED"


def test_a_single_category_evaluation_has_one_defined_f1():
    one = [IR(str(i), "ROUTED", "SENSOR_QUERY", ANS, "ROUTED", "SENSOR_QUERY", ANS, "ROOM_TEMPERATURE", "ROOM_TEMPERATURE") for i in range(3)]
    m = compute_metrics(one)
    assert m["macro_f1"]["categories_included"] == 1 and m["macro_f1"]["value"] == 1.0 and m["category_accuracy"]["denominator"] == 3
    assert m["per_category"]["GENERAL_AI"]["recall"]["value"] is None and m["rejection"]["recall"]["value"] is None


def test_only_rejections_gives_no_category_accuracy_but_a_rejection_precision():
    rejs = [IR("a", "UNSUPPORTED", None, "NONE", "UNSUPPORTED", None, "NONE"), IR("b", "AMBIGUOUS", None, "NONE", "AMBIGUOUS", None, "NONE")]
    m = compute_metrics(rejs)
    assert m["category_accuracy"]["value"] is None and m["category_accuracy"]["denominator"] == 0
    assert m["rejection"]["precision"]["value"] == 1.0 and m["rejection"]["exact_outcome_agreement"]["value"] == 1.0 and m["macro_f1"]["value"] is None


def test_precision_and_recall_are_not_swapped():
    items = [IR("1", "ROUTED", "SENSOR_QUERY", ANS, "ROUTED", "SENSOR_QUERY", ANS, "ROOM_TEMPERATURE", "ROOM_TEMPERATURE"),
             IR("2", "UNSUPPORTED", None, "NONE", "ROUTED", "SENSOR_QUERY", ANS, None, "ROOM_TEMPERATURE")]       # one false prediction, no miss
    pc = compute_metrics(items)["per_category"]["SENSOR_QUERY"]
    assert approx(pc["precision"]["value"], 0.5) and approx(pc["recall"]["value"], 1.0)


def test_strata_are_reported_separately_and_specification_conformance_excludes_the_development_probe():
    items = [HAND[0], HAND[2], IR("d1", "ROUTED", "SENSOR_QUERY", ANS, "UNSUPPORTED", None, "NONE", "ROOM_TEMPERATURE", None, stratum="DEV_AUTHORED"),
             IR("t1", "ROUTED", "OCCUPANCY_QUERY", ANS, "ROUTED", "OCCUPANCY_QUERY", ANS, "ROOM_PEOPLE_COUNT", "ROOM_PEOPLE_COUNT", stratum="SPEC_TABLE")]
    r = build_report(Manifest("S", False, (), False), items)
    assert r["strata_sizes"] == {"SPEC_TABLE": 1, "SPEC_AUTHORED": 2, "DEV_AUTHORED": 1, "REAL_STT_REVIEWED": 0}
    assert r["strata"]["SPECIFICATION_CONFORMANCE"]["n_items"] == 3 and r["strata"]["SPECIFICATION_CONFORMANCE"]["category_accuracy"]["value"] == 1.0
    assert r["strata"]["DEV_AUTHORED"]["category_accuracy"]["value"] == 0.0 and r["strata"]["ALL"]["category_accuracy"]["denominator"] == 4
    assert r["specification_conformance"]["n_items"] == 3 and r["development_probe"]["n_items"] == 1


# =========================================================================================
# E. the real run on the versioned corpus
# =========================================================================================

def test_specification_conformance_is_complete_on_the_versioned_corpus(report):
    spec = report["strata"]["SPECIFICATION_CONFORMANCE"]
    assert spec["n_items"] == 245
    assert spec["category_accuracy"]["numerator"] == spec["category_accuracy"]["denominator"] == 153
    assert spec["routing_outcome_agreement"]["value"] == 1.0 and spec["handling_agreement"]["value"] == 1.0 and spec["mismatches"] == []
    assert report["specification_conformance"]["label"].endswith("(MEASURED ON LABELED TEXT FIXTURES)")


def test_the_safety_metrics_on_the_versioned_corpus(report):
    for name in ("ALL", "SPEC_TABLE", "SPEC_AUTHORED", "DEV_AUTHORED"):
        cs = report["strata"][name]["command_safety"]
        assert cs["false_command_acceptance"]["numerator"] == 0 and cs["false_command_acceptance"]["denominator"] > 0, name
        assert cs["wrong_command_accepted"]["numerator"] == 0 and cs["invariant_violations"]["count"] == 0, name
        assert cs["rejected_requests_that_produced_a_command"]["numerator"] == 0
    a = report["strata"]["ALL"]
    assert a["answer_check"]["checked"] == 35 and a["answer_check"]["violations"]["count"] == 0 and a["answer_check"]["status_agreement"]["value"] == 1.0
    assert a["command_safety"]["false_command_acceptance"]["denominator"] == 224 and safety_violations(report) == 0
    assert a["command_safety"]["exact_command_agreement_of_gold_commands"]["numerator"] == 54                # 56 gold commands; the two misses are development coverage gaps
    assert a["command_safety"]["exact_command_agreement_of_produced_commands"]["value"] == 1.0


def test_the_development_probe_exposes_coverage_gaps_and_the_guard_false_rejections_instead_of_hiding_them(report):
    d = report["strata"]["DEV_AUTHORED"]
    assert d["n_items"] == 35 and d["category_accuracy"]["denominator"] == 30 and d["category_accuracy"]["numerator"] == 5
    assert d["false_rejection"]["overall"]["numerator"] == 25
    assert d["false_rejection"]["sensor_topic_guard"]["numerator"] == 12
    assert d["false_rejection"]["safe_educational_sensor_word_questions_rejected"]["numerator"] == 10
    assert d["false_rejection"]["safe_educational_sensor_word_questions_rejected"]["denominator"] == 10
    assert d["false_rejection"]["by_actual_reason_code"] == {"NO_MATCHING_RULE": 12, ev.GUARD_REASON: 12, "UNSUPPORTED_TARGET": 1}
    assert report["strata"]["SPEC_AUTHORED"]["false_rejection"]["overall"]["numerator"] == 0
    assert all(i["actual_category"] != "GENERAL_AI" or i["gold_category"] == "GENERAL_AI" for i in report["items"])    # nothing was wrongly sent to GENERAL_AI


def test_no_category_is_ever_predicted_wrongly_precision_is_one_where_defined(report):
    a = report["strata"]["ALL"]
    for c, v in a["per_category"].items():
        assert v["precision"]["value"] == 1.0 and v["precision"]["denominator"] > 0, c
    assert a["macro_f1"]["categories_included"] == 6 and 0.9 < a["macro_f1"]["value"] < 1.0


def test_the_evaluation_is_deterministic(manifest):
    a, b = evaluate(manifest), evaluate(manifest)
    for r in (a, b):
        r.pop("run_timestamp_utc")
    assert a == b


def test_evidence_holds_no_request_text_no_answer_text_and_no_pcm(manifest, manifest_data, report):
    blob = json.dumps(report)
    for e in manifest_data["entries"]:
        if len(e["text"].strip()) > 12:
            assert e["text"] not in blob, e["id"]
    assert "22.5" not in blob and "Signals" not in blob and "I'm VESTA" not in blob
    assert report["privacy"] == {"request_text_in_report": False, "answer_text_in_report": False, "pcm_in_report": False}
    assert all(set(i) .isdisjoint({"text", "answer_text", "transcript"}) for i in report["items"])


def test_a_sentinel_text_reaches_neither_the_evidence_nor_the_logs_nor_the_summary(tmp_path, logs):
    entry = rejection_entry()
    entry["text"] = f"open the {SECRET}"
    path = tmp_path / "m.json"
    path.write_text(json.dumps(doc(entry)), encoding="utf-8")
    out = io.StringIO()
    assert ev.main(["run", "--manifest", str(path), "--output", str(tmp_path / "out")], out=out, err=io.StringIO()) == 0
    files = list((tmp_path / "out").glob("*.json"))
    assert len(files) == 1
    blob = files[0].read_text(encoding="utf-8") + out.getvalue() + "\n".join(logs)
    assert SECRET not in blob and "zebra" not in blob and "marmalade" not in blob


def test_the_evaluation_module_imports_no_device_network_or_model_library():
    tree = ast.parse((REPO / "evaluation" / "week8_intent_eval.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    forbidden = {"sounddevice", "serial", "socket", "urllib", "requests", "http", "openai", "onnxruntime", "faster_whisper", "audio.microphone", "audio.playback",
                 "assistant.runtime", "assistant.output_arbiter", "speech.pipeline", "scripts.ai2_stage_b_live"}
    assert not (names & forbidden), names & forbidden


# =========================================================================================
# F. the safety checks really catch violations
# =========================================================================================

def test_a_router_that_accepts_everything_as_a_command_is_caught(monkeypatch, manifest):
    monkeypatch.setattr(ev, "route_intent", lambda req: rt._command("X", rt.IC.CLASSROOM_CONTROL, rt.CommandAction.TURN_ON, rt.CommandTarget.CLASSROOM_LIGHTS)(req.request_id))
    r = evaluate(manifest)
    a = r["strata"]["ALL"]["command_safety"]
    assert a["false_command_acceptance"]["numerator"] == 224 and a["false_command_acceptance"]["value"] == 1.0
    assert a["wrong_command_accepted"]["numerator"] > 0 and safety_violations(r) > 0
    assert r["strata"]["SPEC_TABLE"]["category_accuracy"]["value"] < 1.0


def test_a_router_that_rejects_everything_has_no_false_acceptance_but_a_high_false_rejection_rate(monkeypatch, manifest):
    monkeypatch.setattr(ev, "route_intent", lambda req: ev.IntentResult(req.request_id, ev.RoutingStatus.UNSUPPORTED, "NO_MATCHING_RULE", "NONE"))
    a = evaluate(manifest)["strata"]["ALL"]
    assert a["command_safety"]["false_command_acceptance"]["numerator"] == 0 and a["false_rejection"]["overall"]["value"] == 1.0
    assert a["category_accuracy"]["value"] == 0.0 and a["macro_f1"]["value"] is None


def test_hardware_success_or_an_executed_status_is_flagged_by_the_invariant_checks():
    ok = SimpleNamespace(execution_status=ev.ExecutionStatus.NOT_EXECUTED, spoken_confirmation_allowed=False, command=None, routed=True,
                         actionable_command=None, requires_acknowledgement=False, handling=ev.Handling.NONE)
    assert ev._invariants(ok) == ()
    assert "EXECUTION_STATUS_NOT_NOT_EXECUTED" in ev._invariants(SimpleNamespace(**{**ok.__dict__, "execution_status": "EXECUTED"}))
    assert "SPOKEN_CONFIRMATION_ALLOWED" in ev._invariants(SimpleNamespace(**{**ok.__dict__, "spoken_confirmation_allowed": True}))
    command = rt.route_intent(IntentRequest("turn on the lights", "c1")).command
    assert "COMMAND_ON_A_NON_ROUTED_RESULT" in ev._invariants(SimpleNamespace(**{**ok.__dict__, "command": command, "routed": False}))
    assert "ACTIONABLE_COMMAND_WITHOUT_ACKNOWLEDGEMENT_OR_PENDING_EXECUTION" in ev._invariants(
        SimpleNamespace(**{**ok.__dict__, "command": command, "actionable_command": command, "requires_acknowledgement": False}))
    negative = SimpleNamespace(value=-5, intent_category=command.intent_category, action=command.action, target=command.target, unit=None, request_id="c1")
    flagged = ev._invariants(SimpleNamespace(**{**ok.__dict__, "command": negative}))
    assert "NEGATIVE_OR_NON_INTEGER_COMMAND_VALUE" in flagged and "COMMAND_FAILS_REVALIDATION" in flagged


def test_a_real_router_result_never_trips_an_invariant_for_any_corpus_text(manifest):
    for k, e in enumerate(manifest.entries):
        r = rt.route_intent(IntentRequest(e.text, f"v{k}"))
        assert ev._invariants(r) == (), e.id


def entry_for(text, fixture, status, contains=(), excludes=()):
    return Entry("E", text, "AUTHORED_SPECIFICATION", "SPECIFICATION_CONFORMANCE", "ROUTED", "OCCUPANCY_QUERY", "ANSWERING_FOUNDATION", "ROOM_PEOPLE_COUNT", None,
                 None, fixture, status, tuple(contains), tuple(excludes), ())


def fake_answer(status, text):
    unavailable = status is AnswerStatus.INFORMATION_UNAVAILABLE
    return AnswerResult(status, text, AnsweringIntent.ROOM_PEOPLE_COUNT, "Q1", AnswerSource.UNAVAILABLE if unavailable else AnswerSource.SIMULATED, "R" if unavailable else None,
                        () if unavailable else (AnswerFact("people_count", 1, "people"),))


def test_the_answer_check_flags_a_substituted_number_and_digits_in_a_non_answer(monkeypatch):
    fixtures = {"NONE": object()}
    e = entry_for("how many students are here", "NONE", "INFORMATION_UNAVAILABLE", excludes=("30",))
    monkeypatch.setattr(ev, "answer_query", lambda result, ctx: fake_answer(AnswerStatus.INFORMATION_UNAVAILABLE, "There are 30 people."))
    bad = evaluate_entry(e, 0, fixtures)
    assert bad.answer_status_ok is True and bad.answer_violation == "FORBIDDEN_TEXT_PRESENT"
    e2 = entry_for("how many students are here", "NONE", "INFORMATION_UNAVAILABLE")
    digits = evaluate_entry(e2, 0, fixtures)
    assert digits.answer_violation == "DIGITS_IN_A_NON_ANSWER"
    monkeypatch.setattr(ev, "answer_query", lambda result, ctx: fake_answer(AnswerStatus.INFORMATION_UNAVAILABLE, "I don't have a current people count."))
    clean = evaluate_entry(e2, 0, fixtures)
    assert clean.answer_violation is None and clean.answer_status_ok is True
    e3 = entry_for("how many students are here", "NONE", "ANSWERED", contains=("17",))
    assert evaluate_entry(e3, 0, fixtures).answer_violation == "EXPECTED_TEXT_MISSING"
    assert evaluate_entry(entry_for("how many students are here", "NONE", "ANSWERED"), 0, fixtures).answer_status_ok is False


def test_an_answer_check_on_a_request_the_router_does_not_route_is_a_failed_check_not_a_pass():
    e = entry_for("open the garage", "NONE", "INFORMATION_UNAVAILABLE")
    item = evaluate_entry(e, 0, {"NONE": object()})
    assert item.answer_checked and item.answer_status_ok is False


def test_a_missing_answer_fixture_is_an_error_not_a_silent_skip():
    with pytest.raises(EvalError):
        evaluate_entry(entry_for("how many students are here", "NONE", "ANSWERED"), 0, {})
    with pytest.raises(EvalError):
        evaluate_entry(entry_for("how many students are here", "NONE", "ANSWERED"), 0, None)


def test_answer_statuses_from_the_simulated_fixtures_are_what_the_gold_says(report):
    answered = [i for i in report["items"] if i["answer_checked"]]
    assert len(answered) == 35 and all(i["answer_status_ok"] and i["answer_violation"] is None for i in answered)


# =========================================================================================
# G. evidence files and the command line
# =========================================================================================

def test_evidence_is_written_outside_the_repository_and_never_overwritten(tmp_path, report):
    path = write_report(report, tmp_path)
    before = path.read_bytes()
    assert path.name.startswith("W8-INTENT-V1_") and path.name.endswith(".week8_intent_eval.json")
    with pytest.raises(EvalError):
        write_report(report, tmp_path)
    assert path.read_bytes() == before
    inside = REPO / "_w8_eval_inside_repo"
    with pytest.raises(EvalError):
        write_report(report, inside)
    assert not inside.exists()


def test_the_command_line_validate_run_and_failure_codes(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    assert ev.main(["validate"], out=out, err=err) == 0 and "280 entries" in out.getvalue()
    out = io.StringIO()
    assert ev.main(["run", "--output", str(tmp_path / "e")], out=out, err=err) == 0
    text = out.getvalue()
    assert "SPECIFICATION CONFORMANCE: 245 items" in text and "REAL-WORLD INTENT ACCURACY: NOT MEASURED" in text and "FALSE COMMAND ACCEPTANCE: 0/224" in text
    assert "DEVELOPMENT PROBE (35 items, same author)" in text
    assert len(list((tmp_path / "e").glob("*.json"))) == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    err = io.StringIO()
    assert ev.main(["validate", "--manifest", str(bad)], out=io.StringIO(), err=err) == 2 and "ERROR" in err.getvalue()
    assert ev.main(["run", "--manifest", str(TEMPLATE), "--output", str(tmp_path / "t")], out=io.StringIO(), err=io.StringIO()) == 2
    assert ev.main(["validate", "--manifest", str(TEMPLATE), "--allow-template"], out=io.StringIO(), err=io.StringIO()) == 0
    inside = REPO / "_w8_cli_inside_repo"
    assert ev.main(["run", "--output", str(inside)], out=io.StringIO(), err=io.StringIO()) == 2 and not inside.exists()


def test_fail_on_safety_violation_exits_1_only_when_something_unsafe_was_accepted(tmp_path, monkeypatch):
    assert ev.main(["run", "--output", str(tmp_path / "a"), "--fail-on-safety-violation"], out=io.StringIO(), err=io.StringIO()) == 0
    monkeypatch.setattr(ev, "route_intent", lambda req: rt._command("X", rt.IC.CLASSROOM_CONTROL, rt.CommandAction.TURN_ON, rt.CommandTarget.CLASSROOM_LIGHTS)(req.request_id))
    assert ev.main(["run", "--output", str(tmp_path / "b"), "--fail-on-safety-violation"], out=io.StringIO(), err=io.StringIO()) == 1
    assert ev.main(["run", "--output", str(tmp_path / "c")], out=io.StringIO(), err=io.StringIO()) == 0           # without the flag the run is an evaluation, not a gate


def test_the_frozen_task_1_layers_do_not_import_the_evaluation_module():
    for rel in ("intents/router.py", "intents/contracts.py", "assistant/runtime.py", "speech/pipeline.py", "scripts/ai2_stage_b_live.py"):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any("week8_intent_eval" in x for x in names), rel


def test_a_no_backend_general_ai_is_never_counted_as_an_answered_one():
    wrong = IR("g1", "ROUTED", "GENERAL_AI", ANS, "ROUTED", "GENERAL_AI", NB, "CAPABILITIES", None)         # the gold says the frozen engine answers; the router says no backend
    m = compute_metrics([wrong])
    assert m["handling_agreement"]["value"] == 0.0 and m["query_family_agreement"]["value"] == 0.0 and m["category_accuracy"]["value"] == 1.0
    assert m["mismatches"] == [{"id": "g1", "differs_in": ["handling", "query_family"]}]
    reverse = IR("g2", "ROUTED", "GENERAL_AI", NB, "ROUTED", "GENERAL_AI", ANS, None, "CAPABILITIES")
    assert compute_metrics([reverse])["handling_agreement"]["value"] == 0.0


def test_an_invalid_command_is_not_counted_as_a_routed_category_in_the_confusion_matrix():
    items = [IR("i", "INVALID_COMMAND", "LOCAL_COMMAND", "NONE", "INVALID_COMMAND", "LOCAL_COMMAND", "NONE")]
    m = compute_metrics(items)
    assert m["confusion_matrix"]["counts"]["NO_CATEGORY"]["NO_CATEGORY"] == 1 and m["confusion_matrix"]["counts"]["LOCAL_COMMAND"]["LOCAL_COMMAND"] == 0
    assert m["per_category"]["LOCAL_COMMAND"]["support"] == 0 and m["per_category"]["LOCAL_COMMAND"]["predicted"] == 0


def test_a_wrong_command_value_is_a_wrong_command_even_when_category_action_and_target_match():
    gold = ("LOCAL_COMMAND", "SET_VOLUME", "SPEAKER_OUTPUT", 50, "PERCENT")
    near = ("LOCAL_COMMAND", "SET_VOLUME", "SPEAKER_OUTPUT", 60, "PERCENT")
    item = IR("v", "ROUTED", "LOCAL_COMMAND", EXEC, "ROUTED", "LOCAL_COMMAND", EXEC, g_cmd=gold, a_cmd=near)
    cs = compute_metrics([item])["command_safety"]
    assert cs["wrong_command_accepted"]["numerator"] == 1 and cs["wrong_command_accepted"]["ids"] == ["v"]
    assert cs["exact_command_agreement_of_gold_commands"]["numerator"] == 0 and cs["exact_command_agreement_of_produced_commands"]["numerator"] == 0
    pf = cs["per_field_agreement_where_both_exist"]
    assert [pf[k]["numerator"] for k in ("category", "action", "target", "value", "unit")] == [1, 1, 1, 0, 1]
    unit_off = ("LOCAL_COMMAND", "SET_VOLUME", "SPEAKER_OUTPUT", 50, None)
    assert compute_metrics([IR("u", "ROUTED", "LOCAL_COMMAND", EXEC, "ROUTED", "LOCAL_COMMAND", EXEC, g_cmd=gold, a_cmd=unit_off)])["command_safety"]["wrong_command_accepted"]["numerator"] == 1


@pytest.mark.parametrize("items,expected", [
    ([HAND[0]], 0),                                                                                     # clean
    ([HAND[6]], 1),                                                                                     # a false command acceptance alone
    ([HAND[4]], 1),                                                                                     # a wrong command alone
    ([IR("i", "UNSUPPORTED", None, "NONE", "UNSUPPORTED", None, "NONE", inv=("SPOKEN_CONFIRMATION_ALLOWED",))], 1),    # an invariant violation alone
    ([HAND[0], HAND[2], HAND[3], HAND[5], HAND[9]], 0),
])
def test_the_safety_gate_counts_each_kind_of_violation(items, expected):
    assert safety_violations(build_report(Manifest("G", False, (), False), items)) == expected


def test_the_safety_gate_counts_an_answer_violation():
    bad = ItemResult(id="a", stratum="SPEC_TABLE", gold_outcome="ROUTED", gold_category="OCCUPANCY_QUERY", gold_handling=ANS, gold_family="ROOM_PEOPLE_COUNT", gold_command=None,
                     actual_outcome="ROUTED", actual_category="OCCUPANCY_QUERY", actual_handling=ANS, actual_family="ROOM_PEOPLE_COUNT", actual_reason="X", actual_command=None,
                     answer_checked=True, answer_status_ok=True, answer_violation="FORBIDDEN_TEXT_PRESENT")
    assert safety_violations(build_report(Manifest("G", False, (), False), [bad])) == 1
