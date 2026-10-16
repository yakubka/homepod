"""Structure checks for the Team-Leader AI-1 question test set (evaluation/ai_question_test_set_v1.json).

Data-artifact validation only: no answer engine exists. The primary question texts are pinned here so a
silent edit of the frozen v1 set fails a test (changes must be published as a new version).
"""

import dataclasses
import json
import re
from pathlib import Path

import pytest

from fusion.fuse import EnvironmentState, OccupancyState

PATH = Path(__file__).resolve().parents[1] / "evaluation" / "ai_question_test_set_v1.json"

FROZEN_V1 = [
    ("Q1", "ROOM", "How many people are in the room?"),
    ("Q2", "ROOM", "What is the temperature in the room?"),
    ("Q3", "ROOM", "What is the humidity in the room?"),
    ("Q4", "ROOM", "How bright is the room?"),
    ("Q5", "LECTURE", "What lecture is scheduled now?"),
    ("Q6", "LECTURE", "How many students are expected for this lecture?"),
    ("Q7", "LECTURE", "Has the professor arrived yet?"),
    ("Q8", "GENERAL", "What can you do?"),
    ("Q9", "GENERAL", "What time is the next class?"),
    ("Q10", "GENERAL", "Can you make an announcement?"),
]
STATE_DEPENDENT = {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q9"}
NO_SOURCE_YET = {"Q5", "Q6", "Q7", "Q9"}


@pytest.fixture(scope="module")
def doc():
    return json.loads(PATH.read_text(encoding="utf-8"))


def by_id(doc):
    return {q["id"]: q for q in doc["questions"]}


def test_exactly_ten_unique_questions_with_frozen_text(doc):
    qs = doc["questions"]
    assert len(qs) == 10
    assert len({q["id"] for q in qs}) == 10
    assert [(q["id"], q["category"], q["question"]) for q in qs] == FROZEN_V1


def test_version_metadata_and_category_counts(doc):
    meta = doc["test_set"]
    assert meta["version"] == "v1" and meta["question_count"] == 10
    assert meta["source"] == "TEAM LEADER AI-1 MILESTONE"
    assert meta["is_accuracy_benchmark"] is False and meta["frozen"] is True
    assert meta["category_counts"] == {"ROOM": 4, "LECTURE": 3, "GENERAL": 3}
    for cat, n in meta["category_counts"].items():
        assert sum(q["category"] == cat for q in doc["questions"]) == n


def test_every_question_defines_expected_behaviour_and_status(doc):
    classes = set(doc["answer_source_classes"])
    for q in doc["questions"]:
        assert q["expected_semantics"].strip(), q["id"]
        assert q["expected_key_facts"], q["id"]
        assert q["oracle_mode"] in doc["oracle_modes"], q["id"]
        assert q["current_implementation_status"]["classification"] in classes, q["id"]
        assert q["current_implementation_status"]["detail"].strip(), q["id"]
        assert q["dependency"], q["id"]
        assert q["evaluation_notes"].strip(), q["id"]


def test_state_dependent_questions_define_missing_data_behaviour(doc):
    for qid in STATE_DEPENDENT:
        m = by_id(doc)[qid]["missing_data_behavior"]
        assert m["condition"].strip() and m["expected_semantics"].strip(), qid
        assert m["example_wording"], qid


def test_no_numbers_are_hard_coded_outside_fixtures(doc):
    for q in doc["questions"]:
        text = " ".join([q["expected_semantics"], *q["expected_key_facts"], *q["forbidden_claims"]])
        assert not re.search(r"\d", text), f"{q['id']}: use a {{fixture}} placeholder, not a literal number"


def test_placeholders_resolve_to_a_simulated_fixture(doc):
    for name, fx in doc["fixtures"].items():
        assert fx["origin"] == "SIMULATED", name
    for q in doc["questions"]:
        text = " ".join([*q["expected_key_facts"], *q["forbidden_claims"]])
        used = set(re.findall(r"\{(\w+)\}", text))
        if used:
            fx = doc["fixtures"][q["fixture_context"]["fixture"]]
            assert used <= set(fx["values"]) and fx["values"].keys() >= set(q["fixture_context"]["uses"]), q["id"]
        else:
            assert q["oracle_mode"] != "fixture", q["id"]


def test_missing_fixture_has_the_same_keys_and_no_values(doc):
    present, missing = doc["fixtures"]["room_state_present"]["values"], doc["fixtures"]["room_state_missing"]["values"]
    assert present.keys() == missing.keys()
    assert all(v is None for v in missing.values())
    assert all(v is not None for v in present.values())


def test_fixture_mappings_name_real_classroom_state_fields(doc):
    occ = {f.name for f in dataclasses.fields(OccupancyState)}
    env = {f.name for f in dataclasses.fields(EnvironmentState)}
    assert "vision_count" in occ and {"lux", "temperatures", "humidity"} <= env
    maps = doc["fixtures"]["room_state_present"]["maps_to"]
    assert maps.keys() == doc["fixtures"]["room_state_present"]["values"].keys()
    assert "occupancy.vision_count" in maps["people_count"]
    assert "environment.temperatures" in maps["temperature_c"]
    assert "environment.humidity" in maps["humidity_pct"]
    assert "environment.lux" in maps["lux"]


def test_schedule_and_professor_questions_do_not_claim_a_source(doc):
    qs = by_id(doc)
    for qid in NO_SOURCE_YET:
        q = qs[qid]
        assert q["oracle_mode"] == "unavailable_in_v1", qid
        assert q["fixture_context"] is None, qid
        assert q["current_implementation_status"]["classification"] in ("PLANNED BUT NOT IMPLEMENTED", "UNAVAILABLE"), qid
        assert "unavailable" in " ".join(q["expected_key_facts"]).lower(), qid
    assert qs["Q7"]["current_implementation_status"]["classification"] == "UNAVAILABLE"
    assert "yes" in qs["Q7"]["forbidden_claims"] and "no" in qs["Q7"]["forbidden_claims"]


def test_announcement_is_not_marked_implemented(doc):
    q = by_id(doc)["Q10"]
    assert q["oracle_mode"] == "capability_declaration"
    assert q["current_implementation_status"]["classification"] == "PLANNED BUT NOT IMPLEMENTED"
    assert q["final_milestone_behavior"].strip()
