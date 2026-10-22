"""Week 8 Task 2: text-level intent EVALUATION harness (SOFTWARE ONLY).

    labeled text request -> intents.router.route_intent() -> IntentResult -> gold-label comparison -> per-item evidence -> aggregate metrics

This evaluates ROUTING of text. It does not evaluate speech recognition, and it measures nothing about a microphone, a classroom, a live command
or a device. The router (intents/router.py) and the contract (intents/contracts.py) are used exactly as they are; this module only compares.

WHAT A NUMBER MEANS (read before quoting any result)
  * Every entry has a source_type, and every metric is reported per STRATUM:
      SPEC_TABLE      the text is a member of the router's own canonical phrase tables (or is that phrase after politeness is removed).
                      A result here is SPECIFICATION CONFORMANCE: the router was built from these phrases, so agreement is expected and is
                      NOT a classification accuracy.
      SPEC_AUTHORED   hand-written cases that the Task 1 specification and tests define (grammar forms, rejections, adversarial inputs).
                      Also SPECIFICATION CONFORMANCE.
      DEV_AUTHORED    cases written by the same author that the specification does NOT assert (near variants, coverage probes). Their gold
                      labels are the DESIRED behaviour, so a mismatch is a finding, not an error. They are a development probe, NOT
                      independent data and NOT held out.
      REAL_STT_REVIEWED   human-reviewed real STT outputs from an EXTERNAL manifest (none exist in the versioned corpus).
  * The corpus is checked mechanically: an entry that claims to come from the phrase table must be in it, and an entry that claims not to
    must not be (so a specification example cannot be passed off as development or real data).
  * REAL-WORLD INTENT ACCURACY is reported as NOT MEASURED unless a REAL_STT_REVIEWED stratum with at least one entry exists.

GOLD LABELS. Category and outcome agreement are compared with the router's IntentResult. The expected category of an UNSUPPORTED / AMBIGUOUS /
INVALID_INPUT request is null. INVALID_COMMAND carries the recognised COMMAND category (that is the Task 1 contract); for the confusion matrix
and the precision / recall figures a request that was not ROUTED has the label NO_CATEGORY, whatever its outcome.

DENOMINATORS are returned next to every ratio. A ratio with a zero denominator is None with an explicit reason; it is never 0 or 100 %.

PRIVACY. Per-item evidence holds ids, labels and flags - never the request text, never an answer text. A manifest with real STT entries must
be stored OUTSIDE the repository; so must the evidence.

    python -m evaluation.week8_intent_eval validate [--manifest FILE]
    python -m evaluation.week8_intent_eval run [--manifest FILE] --output DIR [--fail-on-safety-violation]
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.answering import (
    AnswerContext, AnswerResult, AnswerStatus, Capability, CapabilityDeclaration, SimulatedScheduleContext,
)
from config.logging_config import setup_logging
from evaluation.week7_voice_eval import consent_reference_problem
from intents import router as _router
from intents.contracts import (
    COMMAND_CATEGORIES, ExecutionStatus, Handling, IntentCategory, IntentContractError, IntentRequest, IntentResult, RoutingStatus, validate_command,
)
from intents.router import answer_query, normalize, route_intent

logger = setup_logging()

MANIFEST_SCHEMA = "week8-intent-eval-manifest-v1"
MANIFEST_SCHEMA_V2 = "week8-intent-eval-manifest-v2"
REPORT_SCHEMA = "week8-intent-eval-report-v1"
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "week8_intent_manifest_v1.json"
V2_MANIFEST = Path(__file__).resolve().parent / "week8_intent_manifest_v2.json"
# PINNED phrase-table snapshots. A corpus's provenance ("is this text a member of the router's own phrase tables?") is always judged against the snapshot
# the corpus names, never against the live, growing router tables. "task1" is the table of the ORIGINAL Task 1 router (extracted from its byte-identical
# frozen copy, evaluation/week8_task1_router_frozen.py); "task4" is the table at the end of Week 8 Task 4. The v1 manifest names no snapshot: it is "task1".
SNAPSHOT_FILES = {"task1": "week8_phrase_table_task1_snapshot.json", "task4": "week8_phrase_table_task4_snapshot.json"}
ROUTERS = ("current", "task1-frozen")
REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_TYPES = ("ROUTER_PHRASE_TABLE", "AUTHORED_SPECIFICATION", "AUTHORED_DEVELOPMENT", "REAL_STT_REVIEWED")
STRATUM_OF = {"ROUTER_PHRASE_TABLE": "SPEC_TABLE", "AUTHORED_SPECIFICATION": "SPEC_AUTHORED", "AUTHORED_DEVELOPMENT": "DEV_AUTHORED",
              "REAL_STT_REVIEWED": "REAL_STT_REVIEWED"}
PURPOSE_OF = {"ROUTER_PHRASE_TABLE": ("SPECIFICATION_CONFORMANCE",), "AUTHORED_SPECIFICATION": ("SPECIFICATION_CONFORMANCE",),
              "AUTHORED_DEVELOPMENT": ("DEVELOPMENT_AUTHORED",), "REAL_STT_REVIEWED": ("DEV", "HELDOUT")}
STRATA = ("SPEC_TABLE", "SPEC_AUTHORED", "DEV_AUTHORED", "REAL_STT_REVIEWED")
NO_CATEGORY = "NO_CATEGORY"
CATEGORY_LABELS = tuple(c.value for c in IntentCategory)
LABELS = CATEGORY_LABELS + (NO_CATEGORY,)
OUTCOMES = tuple(s.value for s in RoutingStatus)
TAGS = frozenset({"SAFE_EDUCATIONAL_SENSOR_WORD", "ADVERSARIAL", "NEGATIVE_NUMBER", "DECIMAL", "PERCENT_SIGN", "POLITE", "REPEATED", "CONFLICTING",
                  "EXPECTED_VS_DETECTED", "PROFESSOR_VS_PRESENCE", "UNSAFE_HARDWARE", "ZONE", "NEAR_MISS", "COMPOUND", "PUNCTUATION"})
GUARD_REASON = "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI"
MAX_TEXT = 1000
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

BASE_KEYS = {"id", "text", "source_type", "split_or_purpose", "expected_outcome", "expected_category", "expected_handling", "expected_query_family",
             "expected_reason_code", "expected_action", "expected_target", "expected_value", "expected_unit", "answer_fixture", "expected_answer_status",
             "expected_answer_contains", "expected_answer_excludes", "tags", "notes"}
REAL_KEYS = {"real_stt"}
REAL_SUBKEYS = {"consent_reference", "reviewed_by_human", "stt_engine_id", "recording_ref"}
REQUIRED = BASE_KEYS - {"expected_reason_code", "expected_query_family", "answer_fixture", "expected_answer_status", "expected_answer_contains",
                        "expected_answer_excludes", "tags", "notes"}


class EvalError(Exception):
    """Base class for evaluation problems."""


class ManifestError(EvalError, ValueError):
    """The manifest breaks the contract. Nothing was evaluated. Messages name the entry id and the rule, never the text."""


# ---------------------------------------------------------------------------------------------------
# answer fixtures (SIMULATED)
# ---------------------------------------------------------------------------------------------------

def simulated_answer_fixtures() -> Dict[str, AnswerContext]:
    """SIMULATED contexts for the entries that also check the delegated answer. The classroom states come from the project's synthetic fusion
    builders (Origin.SIMULATED, test freshness values); nothing here is a sensor. Imported lazily because the builders live with the tests."""
    from tests import fusion_synthetic as fs
    from fusion.contracts import Origin

    def state(people=17, lux=320.0, temp=22.5, humidity=45.0, t=99.0):
        return fs.fuse(fs.inputs(v=None if people is None else fs.vis(people, t, Origin.SIMULATED), p=fs.pres(True, t, Origin.SIMULATED),
                                 l=None if lux is None else fs.lux(lux, t, Origin.SIMULATED),
                                 temps=[] if temp is None else [fs.temp("temp_1", temp, t, Origin.SIMULATED)],
                                 h=None if humidity is None else fs.hum(humidity, t, Origin.SIMULATED), cfg=fs.CFG))

    room = {Capability.PEOPLE_COUNT, Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS}
    everything = frozenset(room | {Capability.LECTURE_INFORMATION, Capability.ANNOUNCEMENTS})
    decl = CapabilityDeclaration(everything)
    schedule = SimulatedScheduleContext(current_lecture="Signals and Systems", expected_attendance=30, next_class_time="14:00")
    return {
        "NONE": AnswerContext(None, decl),
        "FRESH": AnswerContext(state(), decl),
        "ZERO_PEOPLE": AnswerContext(state(people=0), decl),
        "STALE": AnswerContext(state(t=50.0), decl),
        "NO_TEMPERATURE": AnswerContext(state(temp=None), decl),
        "NO_HUMIDITY": AnswerContext(state(humidity=None), decl),
        "NO_LUX": AnswerContext(state(lux=None), decl),
        "NO_VISION": AnswerContext(state(people=None), decl),
        "UNCONFIGURED": AnswerContext(state(), CapabilityDeclaration.none()),
        "SCHEDULE_NO_STATE": AnswerContext(None, decl, schedule),
        "SCHEDULE_AND_FRESH": AnswerContext(state(), decl, schedule),
    }


FIXTURE_IDS = ("NONE", "FRESH", "ZERO_PEOPLE", "STALE", "NO_TEMPERATURE", "NO_HUMIDITY", "NO_LUX", "NO_VISION", "UNCONFIGURED", "SCHEDULE_NO_STATE",
               "SCHEDULE_AND_FRESH")


# ---------------------------------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    id: str
    text: str = field(repr=False)
    source_type: str
    split_or_purpose: str
    expected_outcome: str
    expected_category: Optional[str]
    expected_handling: str
    expected_query_family: Optional[str]
    expected_reason_code: Optional[str]
    expected_command: Optional[Tuple]            # (category, action, target, value, unit) or None
    answer_fixture: Optional[str]
    expected_answer_status: Optional[str]
    expected_answer_contains: Tuple[str, ...]
    expected_answer_excludes: Tuple[str, ...]
    tags: Tuple[str, ...]

    @property
    def stratum(self) -> str:
        return STRATUM_OF[self.source_type]


@dataclass(frozen=True)
class Manifest:
    corpus_id: str
    template: bool
    entries: Tuple[Entry, ...]
    has_real: bool
    snapshot: str = "task1"                       # the pinned phrase-table snapshot the provenance is judged against
    corrections: Tuple[Dict[str, str], ...] = ()  # v2 only: which v1 gold labels were corrected, and why


_SNAPSHOT_CACHE: Dict[str, Any] = {}


def load_phrase_snapshot(snapshot_id: str):
    """(exact, command, ambiguous) frozensets of normalised phrases of a pinned snapshot."""
    if snapshot_id not in SNAPSHOT_FILES:
        raise ManifestError(f"phrase_table_snapshot must be one of {sorted(SNAPSHOT_FILES)}")
    if snapshot_id not in _SNAPSHOT_CACHE:
        data = json.loads((Path(__file__).resolve().parent / SNAPSHOT_FILES[snapshot_id]).read_text(encoding="utf-8"))
        _SNAPSHOT_CACHE[snapshot_id] = (frozenset(data["exact_phrases"]), frozenset(data["command_phrases"]), frozenset(data["ambiguous_phrases"]))
    return _SNAPSHOT_CACHE[snapshot_id]


def is_table_phrase(text: str, snapshot: str = "task1") -> bool:
    """True if the pinned snapshot's phrase tables contain this text (after the router's normalisation, and after politeness for commands)."""
    t = normalize(text)
    exact, command, ambiguous = load_phrase_snapshot(snapshot)
    return t in exact or t in ambiguous or _router._strip_polite(t) in command


def is_live_table_phrase(text: str) -> bool:
    """Membership in the CURRENT router's tables (for tests; a corpus is never judged against this)."""
    t = normalize(text)
    return t in _router._EXACT or t in _router._AMBIGUOUS_EXACT or _router._strip_polite(t) in _router._COMMAND_EXACT


def _bad(entry_id, rule):
    raise ManifestError(f"entry {entry_id!r}: {rule}")


def _validate_entry(raw: Mapping[str, Any], seen_ids: set, where_real_ok: bool, snapshot: str = "task1") -> Entry:
    eid = raw.get("id") if isinstance(raw, Mapping) else None
    if not isinstance(raw, Mapping) or not isinstance(eid, str) or not _ID.match(eid):
        raise ManifestError("an entry has no valid id")
    if eid in seen_ids:
        _bad(eid, "duplicate id")
    seen_ids.add(eid)
    source = raw.get("source_type")
    real = source == "REAL_STT_REVIEWED"
    allowed = BASE_KEYS | (REAL_KEYS if real else set())
    unknown = set(raw) - allowed
    if unknown:
        _bad(eid, f"unknown keys {sorted(unknown)}")
    missing = REQUIRED - set(raw)
    if missing or (real and "real_stt" not in raw):
        _bad(eid, f"missing keys {sorted(missing | ({'real_stt'} if real and 'real_stt' not in raw else set()))}")
    if source not in SOURCE_TYPES:
        _bad(eid, f"source_type must be one of {SOURCE_TYPES}")
    if raw["split_or_purpose"] not in PURPOSE_OF[source]:
        _bad(eid, f"split_or_purpose for {source} must be one of {PURPOSE_OF[source]}")
    if real and not where_real_ok:
        _bad(eid, "real STT entries are only allowed in a manifest stored outside the repository")
    text = raw["text"]
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        _bad(eid, f"text must be a string of at most {MAX_TEXT} characters")
    # --- provenance is checked mechanically ---------------------------------------------------------
    if not real:
        in_table = is_table_phrase(text, snapshot)
        if source == "ROUTER_PHRASE_TABLE" and not in_table:
            _bad(eid, "claims ROUTER_PHRASE_TABLE but the text is not in the router's phrase tables")
        if source != "ROUTER_PHRASE_TABLE" and in_table:
            _bad(eid, f"is a router phrase-table member and must be declared ROUTER_PHRASE_TABLE, not {source}")
    else:
        sub = raw["real_stt"]
        if not isinstance(sub, Mapping) or set(sub) != REAL_SUBKEYS:
            _bad(eid, f"real_stt needs exactly {sorted(REAL_SUBKEYS)}")
        if consent_reference_problem(sub["consent_reference"]) is not None:
            _bad(eid, "real_stt.consent_reference is not a valid consent / approval reference")
        if sub["reviewed_by_human"] is not True:
            _bad(eid, "real_stt.reviewed_by_human must be true (no un-reviewed transcript is evaluated)")
        for k in ("stt_engine_id", "recording_ref"):
            if not isinstance(sub[k], str) or not sub[k].strip():
                _bad(eid, f"real_stt.{k} must be a non-empty string")
    # --- gold labels ----------------------------------------------------------------------------------
    outcome, cat, handling = raw["expected_outcome"], raw["expected_category"], raw["expected_handling"]
    if outcome not in OUTCOMES:
        _bad(eid, f"expected_outcome must be one of {OUTCOMES}")
    if cat is not None and cat not in CATEGORY_LABELS:
        _bad(eid, "expected_category must be one of the six official categories or null (NO_CATEGORY / UNSUPPORTED are not categories)")
    if handling not in [h.value for h in Handling]:
        _bad(eid, "expected_handling is not a Handling value")
    family = raw.get("expected_query_family")
    if outcome == "ROUTED":
        if cat is None:
            _bad(eid, "a ROUTED entry needs an expected_category")
        if handling == "NONE":
            _bad(eid, "a ROUTED entry needs an expected_handling other than NONE")
    elif outcome == "INVALID_COMMAND":
        if cat not in {c.value for c in COMMAND_CATEGORIES}:
            _bad(eid, "an INVALID_COMMAND entry names the recognised command category")
    else:
        if cat is not None:
            _bad(eid, f"a {outcome} entry has expected_category null")
    if outcome != "ROUTED" and handling != "NONE":
        _bad(eid, "only a ROUTED entry has a handling other than NONE")
    is_query = outcome == "ROUTED" and handling in ("ANSWERING_FOUNDATION", "ANSWER_NOT_IMPLEMENTED")
    if is_query != (family is not None):
        _bad(eid, "expected_query_family is required for a routed query and null otherwise")
    # command
    action, target, value, unit = (raw["expected_action"], raw["expected_target"], raw["expected_value"], raw["expected_unit"])
    command = None
    if outcome == "ROUTED" and cat in {c.value for c in COMMAND_CATEGORIES}:
        if handling != "EXECUTION_NOT_IMPLEMENTED":
            _bad(eid, "a routed command has handling EXECUTION_NOT_IMPLEMENTED")
        check = validate_command({"intent_category": cat, "action": action, "target": target, "value": value, "unit": unit, "request_id": "gold"})
        if not check.valid:
            _bad(eid, f"the gold command is not itself a valid command ({check.status.value})")
        command = (cat, action, target, value, unit)
    elif any(v is not None for v in (action, target, value, unit)):
        _bad(eid, "expected_action / target / value / unit are null unless the entry is a routed command")
    elif outcome == "ROUTED" and handling == "EXECUTION_NOT_IMPLEMENTED":
        _bad(eid, "EXECUTION_NOT_IMPLEMENTED is for command categories only")
    # answer checks
    fixture, ans_status = raw.get("answer_fixture"), raw.get("expected_answer_status")
    if fixture is not None and fixture not in FIXTURE_IDS:
        _bad(eid, f"answer_fixture must be one of {FIXTURE_IDS} or null")
    if (fixture is None) != (ans_status is None):
        _bad(eid, "answer_fixture and expected_answer_status go together")
    if ans_status is not None and ans_status not in [s.value for s in AnswerStatus]:
        _bad(eid, "expected_answer_status is not an AnswerStatus value")
    if fixture is not None and not is_query:
        _bad(eid, "an answer check needs a routed query with handling ANSWERING_FOUNDATION")
    if fixture is not None and handling != "ANSWERING_FOUNDATION":
        _bad(eid, "an answer check needs handling ANSWERING_FOUNDATION")
    lists = []
    for k in ("expected_answer_contains", "expected_answer_excludes", "tags"):
        v = raw.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            _bad(eid, f"{k} must be a list of non-empty strings")
        lists.append(tuple(v))
    bad_tags = set(lists[2]) - TAGS
    if bad_tags:
        _bad(eid, f"unknown tags {sorted(bad_tags)}")
    if (lists[0] or lists[1]) and fixture is None:
        _bad(eid, "answer contains / excludes need an answer_fixture")
    if not isinstance(raw.get("notes", ""), str):
        _bad(eid, "notes must be a string")
    reason = raw.get("expected_reason_code")
    if reason is not None and not isinstance(reason, str):
        _bad(eid, "expected_reason_code must be a string or null")
    return Entry(eid, text, source, raw["split_or_purpose"], outcome, cat, handling, family, reason, command, fixture, ans_status, lists[0], lists[1], lists[2])


def parse_manifest(data: Mapping[str, Any], *, where_real_ok: bool = False, allow_template: bool = False) -> Manifest:
    if not isinstance(data, Mapping) or data.get("schema_version") not in (MANIFEST_SCHEMA, MANIFEST_SCHEMA_V2):
        raise ManifestError(f"schema_version must be {MANIFEST_SCHEMA!r} or {MANIFEST_SCHEMA_V2!r}")
    v2 = data["schema_version"] == MANIFEST_SCHEMA_V2
    snapshot = data.get("phrase_table_snapshot", None if v2 else "task1")
    if snapshot not in SNAPSHOT_FILES or (not v2 and snapshot != "task1"):
        raise ManifestError("phrase_table_snapshot is required for v2 and must name a pinned snapshot (a v1 manifest is always 'task1')")
    corrections = data.get("corrections", [])
    if not v2 and "corrections" in data:
        raise ManifestError("only a v2 manifest may carry corrections")
    if not isinstance(corrections, list) or not all(isinstance(c, Mapping) and set(c) == {"v1_id", "v2_id", "change", "reason"}
                                                    and all(isinstance(v, str) and v for v in c.values()) for c in corrections):
        raise ManifestError("corrections must be a list of {v1_id, v2_id, change, reason} with non-empty strings")
    if not isinstance(data.get("corpus_id"), str) or not data["corpus_id"].strip():
        raise ManifestError("corpus_id is required")
    template = data.get("template", False)
    if template is True:
        if not allow_template:
            raise ManifestError("this is a TEMPLATE (no real data): copy it outside the repository and fill it in; it cannot be evaluated")
        return Manifest(data["corpus_id"], True, (), False, snapshot)
    entries_raw = data.get("entries")
    if not isinstance(entries_raw, list):
        raise ManifestError("entries must be a list")
    seen: set = set()
    entries = tuple(_validate_entry(raw, seen, where_real_ok, snapshot) for raw in entries_raw)
    ids = {e.id for e in entries}
    if any(c["v2_id"] not in ids for c in corrections):
        raise ManifestError("a correction names a v2_id that is not an entry of this manifest")
    return Manifest(data["corpus_id"], False, entries, any(e.source_type == "REAL_STT_REVIEWED" for e in entries), snapshot,
                    tuple(dict(c) for c in corrections))


def _inside_repo(path: Path) -> bool:
    p = Path(path).expanduser().resolve()
    return p == REPO_ROOT or REPO_ROOT in p.parents


def load_manifest(path, *, allow_template: bool = False) -> Manifest:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read the manifest ({type(exc).__name__})") from exc
    return parse_manifest(data, where_real_ok=not _inside_repo(p), allow_template=allow_template)


# ---------------------------------------------------------------------------------------------------
# per-item evaluation
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ItemResult:
    """One evaluated entry, flat and text-free. Built by evaluate_entry(); built by hand in the arithmetic tests."""

    id: str
    stratum: str
    gold_outcome: str
    gold_category: Optional[str]
    gold_handling: str
    gold_family: Optional[str]
    gold_command: Optional[Tuple]
    actual_outcome: str
    actual_category: Optional[str]
    actual_handling: str
    actual_family: Optional[str]
    actual_reason: str
    actual_command: Optional[Tuple]
    gold_reason: Optional[str] = None
    invariant_violations: Tuple[str, ...] = ()
    answer_checked: bool = False
    answer_status_ok: Optional[bool] = None
    answer_violation: Optional[str] = None
    tags: Tuple[str, ...] = ()


def _label(outcome: str, category: Optional[str]) -> str:
    return category if outcome == "ROUTED" and category else NO_CATEGORY


def _command_tuple(result: IntentResult) -> Optional[Tuple]:
    c = result.command
    return None if c is None else (c.intent_category.value, c.action.value, c.target.value, c.value, c.unit.value if c.unit else None)


def _invariants(result: IntentResult) -> Tuple[str, ...]:
    """Checks on the ACTUAL result (not on the gold): nothing may look executed, confirmed or executable when it was not routed."""
    v = []
    if result.execution_status is not ExecutionStatus.NOT_EXECUTED:
        v.append("EXECUTION_STATUS_NOT_NOT_EXECUTED")
    if result.spoken_confirmation_allowed is not False:
        v.append("SPOKEN_CONFIRMATION_ALLOWED")
    if result.command is not None and not result.routed:
        v.append("COMMAND_ON_A_NON_ROUTED_RESULT")
    if result.actionable_command is not None and (not result.requires_acknowledgement or result.handling is not Handling.EXECUTION_NOT_IMPLEMENTED):
        v.append("ACTIONABLE_COMMAND_WITHOUT_ACKNOWLEDGEMENT_OR_PENDING_EXECUTION")
    if result.command is not None:
        c = result.command
        if c.value is not None and (isinstance(c.value, bool) or not isinstance(c.value, int) or c.value < 0):
            v.append("NEGATIVE_OR_NON_INTEGER_COMMAND_VALUE")
        if not validate_command(c).valid:
            v.append("COMMAND_FAILS_REVALIDATION")
    return tuple(v)


def evaluate_entry(entry: Entry, index: int, fixtures: Optional[Mapping[str, AnswerContext]] = None, route=None) -> ItemResult:
    result = (route or route_intent)(IntentRequest(entry.text, f"eval-{index}"))
    answered_ok: Optional[bool] = None
    violation: Optional[str] = None
    checked = entry.answer_fixture is not None
    if checked:
        if fixtures is None or entry.answer_fixture not in fixtures:
            raise EvalError(f"entry {entry.id!r} needs the answer fixture {entry.answer_fixture!r}, which was not supplied")
        if result.routed and result.handling is Handling.ANSWERING_FOUNDATION:
            answer: Optional[AnswerResult] = answer_query(result, fixtures[entry.answer_fixture])
            low = answer.text.lower()
            answered_ok = answer.status.value == entry.expected_answer_status
            if any(s.lower() not in low for s in entry.expected_answer_contains):
                violation = "EXPECTED_TEXT_MISSING"
            elif any(s.lower() in low for s in entry.expected_answer_excludes):
                violation = "FORBIDDEN_TEXT_PRESENT"          # for example the expected attendance used as the detected count
            elif answer.status is not AnswerStatus.ANSWERED and any(ch.isdigit() for ch in answer.text):
                violation = "DIGITS_IN_A_NON_ANSWER"           # an unavailable / unsupported answer must not state a number
        else:
            answered_ok = False                                # the router did not deliver an answerable query
    return ItemResult(
        id=entry.id, stratum=entry.stratum, gold_outcome=entry.expected_outcome, gold_category=entry.expected_category,
        gold_handling=entry.expected_handling, gold_family=entry.expected_query_family, gold_command=entry.expected_command,
        actual_outcome=result.routing_status.value, actual_category=result.category.value if result.category else None,
        actual_handling=result.handling.value, actual_family=result.query_family, actual_reason=result.reason_code,
        actual_command=_command_tuple(result), gold_reason=entry.expected_reason_code, invariant_violations=_invariants(result),
        answer_checked=checked, answer_status_ok=answered_ok, answer_violation=violation, tags=entry.tags)


# ---------------------------------------------------------------------------------------------------
# metrics (pure functions of a list of ItemResult)
# ---------------------------------------------------------------------------------------------------

def ratio(numerator: int, denominator: int, what: str) -> Dict[str, Any]:
    """A ratio WITH its denominator; None (never 0 or 1) when the denominator is zero."""
    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "value": None, "undefined_reason": f"no {what}"}
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator, "undefined_reason": None}


def _f1(p: Optional[float], r: Optional[float]) -> Optional[float]:
    if p is None or r is None:
        return None
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def compute_metrics(items: Sequence[ItemResult]) -> Dict[str, Any]:
    n = len(items)
    gl = [_label(i.gold_outcome, i.gold_category) for i in items]
    al = [_label(i.actual_outcome, i.actual_category) for i in items]
    matrix = {g: {a: 0 for a in LABELS} for g in LABELS}
    for g, a in zip(gl, al):
        matrix[g][a] += 1
    outcome_matrix = {g: {a: 0 for a in OUTCOMES} for g in OUTCOMES}
    for i in items:
        outcome_matrix[i.gold_outcome][i.actual_outcome] += 1

    routed_gold = [i for i in items if i.gold_outcome == "ROUTED"]
    category_correct = sum(1 for i in routed_gold if i.actual_outcome == "ROUTED" and i.actual_category == i.gold_category)
    per_category = {}
    f1s = []
    for c in CATEGORY_LABELS:
        tp = matrix[c][c]
        predicted = sum(matrix[g][c] for g in LABELS)
        support = sum(matrix[c].values())
        p, r = ratio(tp, predicted, f"{c} predictions"), ratio(tp, support, f"{c} gold items")
        f1 = _f1(p["value"], r["value"])
        if f1 is not None:
            f1s.append(f1)
        per_category[c] = {"support": support, "predicted": predicted, "true_positives": tp, "precision": p, "recall": r,
                           "f1": {"value": f1, "undefined_reason": None if f1 is not None else "precision or recall is undefined"}}
    macro = {"value": sum(f1s) / len(f1s) if f1s else None, "categories_included": len(f1s), "categories_total": len(CATEGORY_LABELS),
             "undefined_reason": None if f1s else "no category has a defined F1"}

    gold_rej = [i for i in items if i.gold_outcome != "ROUTED"]
    actual_rej = [i for i in items if i.actual_outcome != "ROUTED"]
    both_rej = sum(1 for i in gold_rej if i.actual_outcome != "ROUTED")
    rejection = {
        "precision": ratio(both_rej, len(actual_rej), "rejected results"), "recall": ratio(both_rej, len(gold_rej), "gold-rejected items"),
        "exact_outcome_agreement": ratio(sum(1 for i in gold_rej if i.actual_outcome == i.gold_outcome), len(gold_rej), "gold-rejected items"),
    }

    # --- command safety ------------------------------------------------------------------------------
    no_gold_cmd = [i for i in items if i.gold_command is None]
    false_accept = [i for i in no_gold_cmd if i.actual_command is not None and i.actual_outcome == "ROUTED" and i.actual_category in {c.value for c in COMMAND_CATEGORIES}]
    gold_cmd = [i for i in items if i.gold_command is not None]
    both = [i for i in gold_cmd if i.actual_command is not None and i.actual_outcome == "ROUTED"]
    exact = [i for i in both if i.actual_command == i.gold_command]
    wrong = [i for i in both if i.actual_command != i.gold_command]
    missed = [i for i in gold_cmd if i not in both]
    field_names = ("category", "action", "target", "value", "unit")
    per_field = {name: ratio(sum(1 for i in both if i.actual_command[k] == i.gold_command[k]), len(both), "items with both a gold and an actual command")
                 for k, name in enumerate(field_names)}
    invariant_items = [i for i in items if i.invariant_violations]
    command_safety = {
        "false_command_acceptance": {**ratio(len(false_accept), len(no_gold_cmd), "items without a gold command"), "ids": [i.id for i in false_accept]},
        "wrong_command_accepted": {**ratio(len(wrong), len(gold_cmd), "items with a gold command"), "ids": [i.id for i in wrong]},
        "gold_command_not_produced": {**ratio(len(missed), len(gold_cmd), "items with a gold command"), "ids": [i.id for i in missed]},
        "exact_command_agreement_of_gold_commands": ratio(len(exact), len(gold_cmd), "items with a gold command"),
        "exact_command_agreement_of_produced_commands": ratio(len(exact), len(both), "items with both a gold and an actual command"),
        "per_field_agreement_where_both_exist": per_field,
        "invariant_violations": {"count": len(invariant_items), "items": [{"id": i.id, "violations": list(i.invariant_violations)} for i in invariant_items]},
        "rejected_requests_that_produced_a_command": ratio(
            sum(1 for i in items if i.actual_outcome != "ROUTED" and i.actual_command is not None), sum(1 for i in items if i.actual_outcome != "ROUTED"),
            "non-routed results"),
    }

    # --- false rejection (a gold-routed request that the router rejected) -------------------------------
    false_rej = [i for i in routed_gold if i.actual_outcome != "ROUTED"]
    reasons: Dict[str, int] = {}
    for i in false_rej:
        reasons[i.actual_reason] = reasons.get(i.actual_reason, 0) + 1
    safe_edu = [i for i in items if "SAFE_EDUCATIONAL_SENSOR_WORD" in i.tags]
    safe_edu_rej = [i for i in safe_edu if i.actual_outcome != "ROUTED"]
    guard_rej = [i for i in false_rej if i.actual_reason == GUARD_REASON]
    false_rejection = {
        "overall": {**ratio(len(false_rej), len(routed_gold), "gold-routed items"), "ids": [i.id for i in false_rej]},
        "by_actual_reason_code": dict(sorted(reasons.items())),
        "sensor_topic_guard": {**ratio(len(guard_rej), len(routed_gold), "gold-routed items"), "ids": [i.id for i in guard_rej]},
        "safe_educational_sensor_word_questions_rejected": ratio(len(safe_edu_rej), len(safe_edu), "items tagged SAFE_EDUCATIONAL_SENSOR_WORD"),
    }

    answers = [i for i in items if i.answer_checked]
    answer_check = {
        "checked": len(answers),
        "status_agreement": ratio(sum(1 for i in answers if i.answer_status_ok), len(answers), "items with an answer fixture"),
        "violations": {"count": sum(1 for i in answers if i.answer_violation),
                       "items": [{"id": i.id, "violation": i.answer_violation} for i in answers if i.answer_violation]},
    }
    gold_fam = [i for i in items if i.gold_family is not None]
    mismatches = []
    for i in items:
        diff = [name for name, a, g in (("outcome", i.actual_outcome, i.gold_outcome), ("category", i.actual_category, i.gold_category),
                                         ("handling", i.actual_handling, i.gold_handling), ("query_family", i.actual_family, i.gold_family),
                                         ("command", i.actual_command, i.gold_command)) if a != g]
        if i.gold_reason is not None and i.actual_reason != i.gold_reason:
            diff.append("reason_code")
        if i.answer_checked and (i.answer_status_ok is not True or i.answer_violation):
            diff.append("answer")
        if diff:
            mismatches.append({"id": i.id, "differs_in": diff})
    inv_cmd_items = [i for i in items if i.gold_outcome == "INVALID_COMMAND"]
    return {
        "n_items": n,
        "category_accuracy": {**ratio(category_correct, len(routed_gold), "gold-routed items (the eligible set)"),
                              "definition": "gold ROUTED items whose actual result is ROUTED with the same category"},
        "per_category": per_category,
        "macro_f1": macro,
        "confusion_matrix": {"labels": list(LABELS), "rows_are": "gold", "columns_are": "actual (NO_CATEGORY = not ROUTED, whatever the outcome)", "counts": matrix},
        "outcome_confusion_matrix": {"labels": list(OUTCOMES), "rows_are": "gold", "counts": outcome_matrix},
        "routing_outcome_agreement": ratio(sum(1 for i in items if i.actual_outcome == i.gold_outcome), n, "items"),
        "handling_agreement": ratio(sum(1 for i in items if i.actual_handling == i.gold_handling), n, "items"),
        "query_family_agreement": ratio(sum(1 for i in gold_fam if i.actual_family == i.gold_family), len(gold_fam), "items with a gold query family"),
        "invalid_command_category_agreement": ratio(sum(1 for i in inv_cmd_items if i.actual_outcome == "INVALID_COMMAND" and i.actual_category == i.gold_category),
                                                    len(inv_cmd_items), "gold INVALID_COMMAND items"),
        "rejection": rejection,
        "command_safety": command_safety,
        "false_rejection": false_rejection,
        "answer_check": answer_check,
        "counts": {
            "gold_by_outcome": {o: sum(1 for i in items if i.gold_outcome == o) for o in OUTCOMES},
            "actual_by_outcome": {o: sum(1 for i in items if i.actual_outcome == o) for o in OUTCOMES},
            "gold_unsupported": sum(1 for i in items if i.gold_outcome == "UNSUPPORTED"), "actual_unsupported": sum(1 for i in items if i.actual_outcome == "UNSUPPORTED"),
            "gold_ambiguous": sum(1 for i in items if i.gold_outcome == "AMBIGUOUS"), "actual_ambiguous": sum(1 for i in items if i.actual_outcome == "AMBIGUOUS"),
            "gold_by_category": {c: sum(1 for i in items if i.gold_outcome == "ROUTED" and i.gold_category == c) for c in CATEGORY_LABELS},
        },
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------

NOT_MEASURED = "NOT MEASURED"


def build_report(manifest: Manifest, items: Sequence[ItemResult], now: Optional[datetime] = None, router_name: str = "current") -> Dict[str, Any]:
    by_stratum = {s: [i for i in items if i.stratum == s] for s in STRATA}
    strata = {"ALL": compute_metrics(items)}
    for s in STRATA:
        if by_stratum[s]:
            strata[s] = compute_metrics(by_stratum[s])
    spec = by_stratum["SPEC_TABLE"] + by_stratum["SPEC_AUTHORED"]
    if spec:
        strata["SPECIFICATION_CONFORMANCE"] = compute_metrics(spec)
    real = by_stratum["REAL_STT_REVIEWED"]
    return {
        "schema_version": REPORT_SCHEMA,
        "corpus_id": manifest.corpus_id,
        "router": router_name,
        "phrase_table_snapshot": manifest.snapshot,
        "corrections": [dict(c) for c in manifest.corrections],
        "run_timestamp_utc": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds"),
        "scope": "TEXT-LEVEL ROUTING EVALUATION. Not speech recognition, not a classroom, not live commands, not hardware execution.",
        "strata_sizes": {s: len(by_stratum[s]) for s in STRATA},
        "specification_conformance": {
            "label": "SPECIFICATION CONFORMANCE (MEASURED ON LABELED TEXT FIXTURES)" if spec else "SPECIFICATION CONFORMANCE: NO ITEMS",
            "note": "These phrases were used to build the router; agreement is expected and is NOT independent classification accuracy.",
            "n_items": len(spec),
        },
        "development_probe": {
            "label": "DEVELOPMENT PROBE (same author; gold = desired behaviour; NOT independent, NOT held out)" if by_stratum["DEV_AUTHORED"] else "NO DEVELOPMENT ITEMS",
            "n_items": len(by_stratum["DEV_AUTHORED"]),
        },
        "real_world_intent_accuracy": (NOT_MEASURED if not real else
                                       f"MEASURED ON {len(real)} HUMAN-REVIEWED REAL STT TRANSCRIPTS (see strata.REAL_STT_REVIEWED; splits are recorded per entry)"),
        "independent_examples": len(real),
        "strata": strata,
        "items": [dict(i.__dict__, tags=list(i.tags), invariant_violations=list(i.invariant_violations)) for i in items],
        "privacy": {"request_text_in_report": False, "answer_text_in_report": False, "pcm_in_report": False},
    }


def load_router(name: str):
    """The routing function to evaluate: the current router, or the byte-identical frozen copy of the ORIGINAL Task 1 router (reproduces the v1 baseline)."""
    if name == "current":
        return route_intent
    if name == "task1-frozen":
        import importlib
        return importlib.import_module("evaluation.week8_task1_router_frozen").route_intent
    raise EvalError(f"router must be one of {ROUTERS}")


def evaluate(manifest: Manifest, fixtures: Optional[Mapping[str, AnswerContext]] = None, route=None, router_name: str = "current") -> Dict[str, Any]:
    if manifest.template:
        raise EvalError("a template manifest cannot be evaluated")
    needs_fixtures = any(e.answer_fixture for e in manifest.entries)
    if needs_fixtures and fixtures is None:
        fixtures = simulated_answer_fixtures()
    items = [evaluate_entry(e, k, fixtures, route) for k, e in enumerate(manifest.entries)]
    report = build_report(manifest, items, router_name=router_name)
    logger.info("event=intent_eval_done corpus=%s items=%d false_command_acceptance=%d invariant_violations=%d", manifest.corpus_id, len(items),
                report["strata"]["ALL"]["command_safety"]["false_command_acceptance"]["numerator"],
                report["strata"]["ALL"]["command_safety"]["invariant_violations"]["count"])
    return report


def safety_violations(report: Mapping[str, Any]) -> int:
    s = report["strata"]["ALL"]
    return (s["command_safety"]["false_command_acceptance"]["numerator"] + s["command_safety"]["wrong_command_accepted"]["numerator"]
            + s["command_safety"]["invariant_violations"]["count"] + s["answer_check"]["violations"]["count"])


def write_report(report: Mapping[str, Any], out_dir) -> Path:
    from evaluation.ingest import IngestError, assert_outside_repo
    out = Path(out_dir)
    try:
        assert_outside_repo(out)
    except IngestError as exc:
        raise EvalError(str(exc)) from exc
    out.mkdir(parents=True, exist_ok=True)
    stamp = report["run_timestamp_utc"].replace(":", "").replace("-", "")
    path = out / f"{report['corpus_id']}_{stamp}.week8_intent_eval.json"
    try:
        with open(path, "x", encoding="utf-8") as handle:        # never overwrite evidence
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except FileExistsError:
        raise EvalError("the evidence file already exists and will not be overwritten") from None
    return path


def _summary_lines(report: Mapping[str, Any]) -> List[str]:
    a = report["strata"]["ALL"]
    def pct(r):
        return "undefined" if r["value"] is None else f"{r['numerator']}/{r['denominator']} = {r['value']:.3f}"
    lines = [f"corpus {report['corpus_id']}: {a['n_items']} items; strata {report['strata_sizes']}; router {report['router']}; phrase-table snapshot {report['phrase_table_snapshot']}",
             f"SPECIFICATION CONFORMANCE: {report['specification_conformance']['n_items']} items ({report['specification_conformance']['label']})",
             f"category accuracy (all strata, eligible set): {pct(a['category_accuracy'])}", f"macro F1 over {a['macro_f1']['categories_included']}/6 categories: "
             + ("undefined" if a["macro_f1"]["value"] is None else f"{a['macro_f1']['value']:.3f}"),
             f"routing outcome agreement: {pct(a['routing_outcome_agreement'])}",
             f"FALSE COMMAND ACCEPTANCE: {pct(a['command_safety']['false_command_acceptance'])}",
             f"invariant violations: {a['command_safety']['invariant_violations']['count']}   answer-check violations: {a['answer_check']['violations']['count']}",
             f"false rejections: {pct(a['false_rejection']['overall'])} (sensor-topic guard: {pct(a['false_rejection']['sensor_topic_guard'])})"]
    if "DEV_AUTHORED" in report["strata"]:
        d = report["strata"]["DEV_AUTHORED"]
        lines.append(f"DEVELOPMENT PROBE ({d['n_items']} items, same author): category accuracy {pct(d['category_accuracy'])}, outcome agreement {pct(d['routing_outcome_agreement'])}")
    lines.append(f"REAL-WORLD INTENT ACCURACY: {report['real_world_intent_accuracy']}")
    return lines


def main(argv=None, *, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    parser = argparse.ArgumentParser(prog="week8_intent_eval", description="Week 8 text-level intent evaluation (routing only)")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="validate a manifest; evaluates nothing")
    v.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    v.add_argument("--allow-template", action="store_true")
    r = sub.add_parser("run", help="evaluate a manifest and write one evidence file")
    r.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    r.add_argument("--output", required=True, help="evidence directory OUTSIDE the repository")
    r.add_argument("--fail-on-safety-violation", action="store_true", help="exit 1 if any false command acceptance or invariant violation was found")
    r.add_argument("--router", choices=ROUTERS, default="current",
                   help="current = intents/router.py; task1-frozen = the byte-identical copy of the original Task 1 router (reproduces the v1 baseline)")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest, allow_template=getattr(args, "allow_template", False))
        if args.command == "validate":
            kind = "TEMPLATE (not evaluable)" if manifest.template else f"{len(manifest.entries)} entries"
            print(f"manifest {manifest.corpus_id}: valid, {kind}", file=out)
            return 0
        report = evaluate(manifest, route=load_router(args.router), router_name=args.router)
        path = write_report(report, args.output)
    except (ManifestError, EvalError) as exc:
        print(f"ERROR: {exc}", file=err)
        return 2
    for line in _summary_lines(report):
        print(line, file=out)
    print(f"evidence: {path}", file=out)
    return 1 if args.fail_on_safety_violation and safety_violations(report) else 0


if __name__ == "__main__":
    sys.exit(main())
