"""Week 9 Task 1A: the interface decision draft stays a draft, keeps every requirement and decision, and its VERIFIED FACTS match the source.

Documentation-only check (SOFTWARE-VALIDATED). Nothing here touches hardware or any production module.
"""

import re
from pathlib import Path

from intents.contracts import CommandAction, CommandTarget, ExecutionStatus, IntentCategory, SUPPORTED_COMMANDS

REPO = Path(__file__).resolve().parents[1]
DOC = (REPO / "docs" / "contracts" / "WEEK9_INTERFACE_DECISIONS_DRAFT.md").read_text(encoding="utf-8")


def test_marked_as_unapproved_draft():
    assert "**DRAFT — NOT TEAM-APPROVED.**" in DOC.splitlines()[2]
    assert "No decision in this document has been made by the team." in DOC


def test_every_requirement_and_decision_present():
    for n in range(1, 15):
        assert re.search(rf"^\| R{n} \|", DOC, re.M), f"R{n} missing"
    for n in range(1, 13):
        assert f"### D{n} —" in DOC, f"D{n} missing"


def test_no_decision_recorded_as_made():
    register = DOC.split("## 12. Team decisions")[1]
    rows = re.findall(r"^\| (D\d+) \| (\w+) \|", register, re.M)
    assert [r[0] for r in rows] == [f"D{n}" for n in range(1, 13)]
    assert all(status == "OPEN" for _, status in rows)


def test_verified_facts_match_source():
    assert list(ExecutionStatus) == [ExecutionStatus.NOT_EXECUTED]
    assert {t.value for t in CommandTarget} == {"SPEAKER_OUTPUT", "CLASSROOM_LIGHTS", "SMART_BULB"}
    lights = SUPPORTED_COMMANDS[(IntentCategory.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS)]
    assert dict(lights) == {CommandAction.TURN_ON: None, CommandAction.TURN_OFF: None}
    runtime = (REPO / "assistant" / "runtime.py").read_text(encoding="utf-8")
    # section 6.1 audited the Task 1A per-instance counter; Week 9 Task 1C replaced it, and the draft carries a dated superseded note
    assert "itertools.count(1)" not in runtime and "self._request_ids.next_id()" in runtime
    assert "Superseded 2026-10-17 (Week 9 Task 1C)" in DOC.split("### 6.1")[1].split("### 6.2")[0]
