"""Week 6 Task 3: synthetic end-to-end fusion validation, recovery, telemetry.

SIMULATED / SYNTHETIC structured inputs only. No sensor, ESP32 or camera produced any of it. Objects marked
Origin.REAL are structural provenance-logic fixtures, not hardware evidence. Freshness values are test values.
No timing threshold is asserted anywhere.
"""

import copy
import dataclasses
import logging

import pytest

from fusion.contracts import Origin, SourceStatus, Usability
from fusion.fuse import ClassroomState, DegradedReason as D, EvidenceState as E, FusedPresence as F, fuse_classroom_state
from fusion.telemetry import format_fusion_decision, fusion_decision_record, log_fusion_decision
from tests import fusion_synthetic as fs
from tests.fusion_synthetic import CFG, CFG_NO_SKEW, SCENARIOS, fuse, hum, inputs, lux, mixed_occ, pres, temp, vis


# ---- scenario suite ---------------------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_synthetic_scenario(scenario):
    for i, step in enumerate(scenario.steps):
        _, problems = fs.check_step(step)
        assert not problems, f"step {i}: {problems}"


def test_scenario_correctness_is_all_matched():
    matched, total, problems = fs.scenario_correctness()
    assert (matched, total) == (len(SCENARIOS), len(SCENARIOS)) and problems == [] and total >= 30


def test_scenarios_cover_every_rule():
    rules = {step.expect["rule"] for s in SCENARIOS for step in s.steps}
    assert rules == {f"R{i}" for i in range(1, 9)}


# ---- primary end-to-end audit -----------------------------------------------------------

def test_primary_end_to_end_audit():
    args = fs.primary_inputs()
    state = fuse(args)
    a = fs.audit_summary(args, state)
    assert a["input_origins"] == {"vision": "SIMULATED", "presence": "SIMULATED", "lux": "SIMULATED", "humidity": "SIMULATED",
                                  "temperatures": ["SIMULATED", "SIMULATED"]}
    assert a["input_received_at"] == {"vision": 99.0, "presence": 98.5, "lux": 99.0, "humidity": 97.0, "temperatures": [98.0, 98.0]}
    assert a["freshness"] == {"vision": "FRESH", "presence": "FRESH", "lux": "FRESH", "humidity": "FRESH", "temperatures": ["FRESH", "FRESH"]}
    assert (a["vision_count"], dict(a["zones"]), a["unassigned"], a["outside"]) == (29, {"LEFT": 8, "CENTER": 7, "RIGHT": 9}, 5, 3)
    assert (a["radar_present"], a["lux"], a["temperatures"], a["humidity"]) == (True, 320.0, [("t-a", 21.5), ("t-b", 22.0)], 45.0)
    assert (a["rule_id"], a["fallback_rule_id"], a["evidence"], a["fused_presence"]) == ("R1", None, "CONFIRMED", "PRESENT")
    assert (a["degraded"], a["degraded_reasons"], a["aggregate_origin"], a["environment_issues"]) == (False, [], "SIMULATED", [])
    # the audit's input timestamps agree with the ages the states report (receive time = now - age)
    assert state.evaluated_at_monotonic_s - state.vision_state.age_s == 99.0
    assert state.evaluated_at_monotonic_s - state.presence_state.age_s == 98.5
    assert a["log_record"]["rule_id"] == "R1" and a["log_record"]["aggregate_origin"] == "SIMULATED"


def test_end_to_end_uses_the_real_public_apis_not_copies():
    state = fuse(fs.primary_inputs())
    assert isinstance(state, ClassroomState)
    assert fs.fuse_classroom_state is fuse_classroom_state                          # no re-implementation in the fixture
    assert type(vis(result=mixed_occ()).occupancy).__module__ == "vision.zones"


# ---- stale / failed / missing are not valid zero / False -------------------------------

def test_failed_missing_and_stale_never_become_values():
    for p in (pres(status=SourceStatus.FAILED), None, pres(True, t=80.0)):
        s = fuse(inputs(vis(2), p))
        assert s.occupancy.sensor_present is None and s.occupancy.presence_used is False
        assert s.occupancy.fused_presence is F.PRESENT                              # from vision, not from a fabricated radar value
    for v in (vis(status=SourceStatus.UNAVAILABLE), None, vis(4, t=80.0)):
        s = fuse(inputs(v, pres(False)))
        assert s.occupancy.vision_count is None and s.occupancy.vision_used is False
    zero = fuse(inputs(vis(0), pres(False)))
    assert zero.occupancy.vision_count == 0 and zero.occupancy.sensor_present is False      # real zero / False stay distinct


# ---- recovery: successive pure evaluations, no hidden state, no hysteresis --------------

def test_fresh_stale_fresh_sequence_has_no_hidden_state():
    v = vis(3, t=99.0)
    p = pres(True, t=99.0)
    first = fuse(inputs(v, p, now=100.0))
    stale = fuse(inputs(v, p, now=120.0))
    v2, p2 = vis(3, t=119.0), pres(True, t=119.0)
    recovered = fuse(inputs(v2, p2, now=120.0))
    assert (first.vision_state.usability, stale.vision_state.usability, recovered.vision_state.usability) == \
           (Usability.FRESH, Usability.STALE, Usability.FRESH)
    assert recovered.rule_id == "R1" and recovered.occupancy.evidence is E.CONFIRMED          # immediate recovery
    # replaying the first call after the others gives exactly the first answer: nothing was remembered
    assert fuse(inputs(v, p, now=100.0)) == first
    assert fuse(inputs(v, p, now=120.0)) == stale


def test_recovery_after_failed_radar_and_unavailable_camera_and_skew_and_environment():
    assert fuse(inputs(vis(2), pres(status=SourceStatus.FAILED))).rule_id == "R5"
    assert fuse(inputs(vis(2), pres(True))).rule_id == "R1"
    assert fuse(inputs(vis(status=SourceStatus.UNAVAILABLE), pres(True))).rule_id == "R6"
    assert fuse(inputs(vis(2), pres(True))).rule_id == "R1"
    assert fuse(inputs(vis(2, t=99.0), pres(True, t=95.0))).rule_id == "R8"
    assert fuse(inputs(vis(2, t=99.0), pres(True, t=99.0))).rule_id == "R1"
    assert fuse(inputs(vis(2), pres(True), lux(5.0, t=10.0))).environment.lux.value is None
    assert fuse(inputs(vis(2), pres(True), lux(5.0))).environment.lux.value == 5.0


def test_a_conflict_is_not_sticky():
    assert fuse(inputs(vis(0), pres(True))).occupancy.evidence is E.CONFLICT
    assert fuse(inputs(vis(0), pres(False))).occupancy.evidence is E.CONFIRMED_EMPTY
    assert fuse(inputs(vis(0), pres(True))).occupancy.evidence is E.CONFLICT           # and back, no debounce or memory


def test_fusion_module_keeps_no_cross_call_state():
    import fusion.fuse as module
    assert not [n for n, v in vars(module).items() if isinstance(v, (dict, list, set)) and not n.startswith("__")]


# ---- conflict visibility and ties -------------------------------------------------------

def test_conflicts_keep_contradictory_raw_evidence():
    r3 = fuse(inputs(vis(0), pres(True)))
    r4 = fuse(inputs(vis(4), pres(False)))
    assert (r3.occupancy.vision_count, r3.occupancy.sensor_present, r3.occupancy.raw_vision_count, r3.occupancy.raw_presence) == (0, True, 0, True)
    assert (r4.occupancy.vision_count, r4.occupancy.sensor_present, r4.occupancy.raw_vision_count, r4.occupancy.raw_presence) == (4, False, 4, False)
    for s in (r3, r4):
        assert s.occupancy.fused_presence is F.UNKNOWN and s.degraded


def test_equal_timestamps_with_configured_skew_do_not_invoke_r8():
    for t in (99.0, 50.0 + 45.0, 95.0):
        s = fuse(inputs(vis(3, t=t), pres(True, t=t), now=100.0))
        assert s.rule_id == "R1" and s.fallback_rule_id is None and D.SOURCE_SKEW not in s.degraded_reasons
        assert fusion_decision_record(s)["fallback_basis"] is None


def test_unconfigured_skew_fallback_is_precedence_not_temporal_order():
    tie = fuse(inputs(vis(3, t=99.0), pres(True, t=99.0), cfg=CFG_NO_SKEW))
    assert (tie.rule_id, tie.fallback_rule_id, tie.occupancy.evidence) == ("R8", "R5", E.VISION_ONLY)
    assert tie.degraded and tie.degraded_reasons == (D.SKEW_UNCONFIGURED,)
    assert tie.occupancy.evidence not in (E.CONFIRMED, E.CONFIRMED_EMPTY)
    assert tie.occupancy.sensor_present is True and tie.presence_state.usability is Usability.FRESH      # radar stays visible
    assert fusion_decision_record(tie)["fallback_basis"] == "FALLBACK_PRECEDENCE"
    newer = fuse(inputs(vis(3, t=95.0), pres(True, t=99.0), cfg=CFG_NO_SKEW))
    assert fusion_decision_record(newer)["fallback_basis"] == "NEWER_SOURCE"
    exceeded = fuse(inputs(vis(3, t=99.0), pres(True, t=95.0)))
    assert fusion_decision_record(exceeded)["fallback_basis"] == "NEWER_SOURCE"


def test_skew_exclusion_keeps_the_older_source_visible_and_not_stale():
    s = fuse(inputs(vis(3, t=99.0), pres(True, t=95.0)))
    assert s.presence_state.usability is Usability.FRESH and s.presence_state.age_s == 5.0
    assert s.occupancy.presence_used is False and s.occupancy.raw_presence is True and D.SOURCE_SKEW in s.degraded_reasons


# ---- environment independence -----------------------------------------------------------

ENV_VARIANTS = [
    dict(), dict(l=lux(0.0)), dict(l=lux(900.0)), dict(l=lux(5.0, t=10.0)), dict(temps=[temp("a", 20.0), temp("b", 30.0)]),
    dict(temps=[temp("a", 20.0, t=10.0)]), dict(h=hum(0.0)), dict(h=hum(100.0, t=10.0)), dict(l=lux(1.0), temps=[temp("a", 1.0)], h=hum(1.0)),
]


@pytest.mark.parametrize("n,p", [(3, True), (0, True), (2, False), (0, False), (2, None), (None, True)])
def test_environment_never_changes_occupancy(n, p):
    v = None if n is None else vis(n)
    pr = None if p is None else pres(p)
    base = fuse(inputs(v, pr))
    for kw in ENV_VARIANTS:
        s = fuse(inputs(v, pr, **kw))
        assert (s.rule_id, s.fallback_rule_id, s.occupancy, s.degraded, s.degraded_reasons) == \
               (base.rule_id, base.fallback_rule_id, base.occupancy, base.degraded, base.degraded_reasons)


def test_environment_issue_does_not_make_occupancy_degraded():
    s = fuse(inputs(vis(3), pres(True), lux(1.0, t=10.0)))
    assert "LUX_STALE" in s.environment.issues and s.degraded is False and s.occupancy.evidence is E.CONFIRMED


def test_environment_sources_have_independent_freshness():
    s = fuse(inputs(vis(3, t=99.0), pres(True, t=99.0), lux(7.0, t=91.0), [temp("t", 20.0, t=90.0)], hum(30.0, t=90.5)))    # 'skewed' vs camera
    assert (s.environment.lux.value, s.environment.temperatures[0].value, s.environment.humidity.value) == (7.0, 20.0, 30.0)
    assert s.rule_id == "R1" and s.environment.issues == ()


# ---- occupancy-degraded terminology -----------------------------------------------------

def test_degraded_means_occupancy_evidence_quality_only():
    healthy_occ_bad_env = fuse(inputs(vis(2), pres(True)))                     # lux/humidity missing
    assert healthy_occ_bad_env.degraded is False and healthy_occ_bad_env.environment.issues == ("LUX_MISSING", "HUMIDITY_MISSING")
    assert "OCCUPANCY-EVIDENCE" in ClassroomState.__doc__ and "NOT an overall sensor-health flag" in ClassroomState.__doc__
    assert "degraded" in fusion_decision_record(healthy_occ_bad_env)


# ---- provenance -------------------------------------------------------------------------

def test_aggregate_provenance_scenarios():
    assert fuse(inputs(vis(1), pres(True))).aggregate_origin is Origin.SIMULATED
    assert fuse(inputs(vis(1, origin=Origin.REAL), pres(True, origin=Origin.REAL))).aggregate_origin is Origin.REAL     # structural only
    assert fuse(inputs(vis(1, origin=Origin.REAL), pres(True))).aggregate_origin is Origin.SIMULATED
    assert fuse(inputs(vis(1), pres(True, origin=Origin.UNKNOWN))).aggregate_origin is Origin.UNKNOWN
    assert fuse(inputs()).aggregate_origin is Origin.UNKNOWN


# ---- zones ------------------------------------------------------------------------------

@pytest.mark.parametrize("radar", [pres(True), pres(False), None, pres(True, t=80.0)])
def test_zone_numbers_survive_any_radar_state(radar):
    v = vis(result=mixed_occ())
    s = fuse(inputs(v, radar))
    assert s.occupancy.zone_counts is v.occupancy.zone_counts
    assert (dict(s.occupancy.zone_counts), s.occupancy.unassigned_inside_roi, s.occupancy.outside_classroom_roi) == \
           ({"LEFT": 8, "CENTER": 7, "RIGHT": 9}, 5, 3)


def test_no_zones_stays_unavailable_not_zero():
    s = fuse(inputs(vis(4, zones=False), pres(True)))
    assert s.occupancy.zones_configured is False and s.occupancy.zone_counts is None and s.occupancy.unassigned_inside_roi is None
    rec = fusion_decision_record(s)
    assert rec["zones"] is None and "LEFT=0" not in format_fusion_decision(rec)


# ---- known vision undercount protection -------------------------------------------------

def test_undercount_is_never_corrected():
    assert fuse(inputs(vis(0), pres(True))).occupancy.vision_count == 0
    assert fuse(inputs(vis(3), pres(True))).occupancy.vision_count == 3
    for n in range(0, 8):
        assert fuse(inputs(vis(n), pres(True))).occupancy.vision_count == n


# ---- determinism ------------------------------------------------------------------------

def test_repeated_evaluation_is_identical_and_inputs_unchanged():
    args = fs.primary_inputs()
    snap = copy.deepcopy([dataclasses.astuple(args[k]) for k in ("vision", "presence", "lux", "humidity")])
    temps_before = list(args["temperatures"])
    a, b = fuse(args), fuse(args)
    assert a == b and fusion_decision_record(a) == fusion_decision_record(b)
    assert [dataclasses.astuple(args[k]) for k in ("vision", "presence", "lux", "humidity")] == snap and args["temperatures"] == temps_before


# ---- telemetry --------------------------------------------------------------------------

def line(args):
    return format_fusion_decision(fusion_decision_record(fuse(args)))


def test_conflict_is_logged_as_conflict_with_raw_evidence():
    text = line(inputs(vis(0), pres(True)))
    assert text.startswith("event=fusion_decision ") and "evidence=CONFLICT" in text and "fused_presence=UNKNOWN" in text
    assert "degraded_reasons=PRESENCE_WITHOUT_DETECTIONS" in text and "vision_count=0" in text and "sensor_present=true" in text
    assert "rule_id=R3" in text


def test_r4_raw_evidence_logged():
    text = line(inputs(vis(4), pres(False)))
    assert "evidence=CONFLICT" in text and "vision_count=4" in text and "sensor_present=false" in text and "DETECTIONS_WITHOUT_PRESENCE" in text


def test_simulated_stays_simulated_in_the_log():
    text = line(fs.primary_inputs())
    assert "aggregate_origin=SIMULATED" in text and "vision_origin=SIMULATED" in text and "presence_origin=SIMULATED" in text
    assert "REAL" not in text


def test_source_skew_is_visible_in_the_log():
    text = line(inputs(vis(3, t=99.0), pres(True, t=95.0)))
    assert "rule_id=R8" in text and "fallback_rule_id=R5" in text and "SOURCE_SKEW" in text and "fallback_basis=NEWER_SOURCE" in text
    assert "presence_used=false" in text and "presence_state=FRESH" in text
    assert "SKEW_UNCONFIGURED" in line(inputs(vis(3), pres(True), cfg=CFG_NO_SKEW))
    assert "fallback_basis=FALLBACK_PRECEDENCE" in line(inputs(vis(3), pres(True), cfg=CFG_NO_SKEW))


def test_environment_states_and_issues_logged():
    text = line(inputs(vis(3), pres(True), lux(0.0), [temp("t-a", 21.5), temp("t-b", 30.0, t=10.0)], hum(40.0, t=10.0)))
    assert "lux=0.0" in text and "lux_state=FRESH" in text and "t-a:FRESH:21.5" in text and "t-b:STALE:None" in text
    assert "humidity_state=STALE" in text and "TEMPERATURE[t-b]_STALE" in text and "HUMIDITY_STALE" in text


def test_missing_values_are_logged_as_none_not_zero():
    text = line(inputs(None, pres(True)))
    assert "vision_count=none" in text and "vision_state=MISSING" in text and "lux=none" in text and "vision_count=0" not in text


def test_record_is_built_from_the_final_state_not_recomputed():
    s = fuse(inputs(vis(3), pres(True)))
    tampered = dataclasses.replace(s, rule_id="R9", occupancy=dataclasses.replace(s.occupancy, evidence=E.UNKNOWN))
    rec = fusion_decision_record(tampered)
    assert rec["rule_id"] == "R9" and rec["evidence"] == "UNKNOWN"


def test_telemetry_does_not_change_the_state():
    args = fs.primary_inputs()
    s = fuse(args)
    zones = s.occupancy.zone_counts
    before = dataclasses.astuple(s)
    format_fusion_decision(fusion_decision_record(s))
    log_fusion_decision(s, logging.getLogger("test_fusion_telemetry"))
    assert dataclasses.astuple(s) == before and s.occupancy.zone_counts is zones and dict(zones)["LEFT"] == 8


def test_log_emitter_uses_an_injected_logger_in_project_style():
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    lg = logging.getLogger("fusion_test_logger")
    lg.addHandler(Grab())
    lg.setLevel(logging.INFO)
    lg.propagate = False
    s = fuse(inputs(vis(3), pres(True)))
    assert log_fusion_decision(s, lg) is True
    assert len(records) == 1 and records[0].startswith("event=fusion_decision ") and "=" in records[0]


def test_logging_failure_does_not_affect_the_state():
    class Broken:
        def info(self, *a, **k):
            raise OSError("disk full")

    s = fuse(inputs(vis(3), pres(True)))
    before = dataclasses.astuple(s)
    assert log_fusion_decision(s, Broken()) is False
    assert dataclasses.astuple(s) == before and fuse(inputs(vis(3), pres(True))) == s


def test_log_contains_no_image_or_personal_fields():
    keys = set(fusion_decision_record(fuse(inputs(vis(3), pres(True)))))
    assert not keys & {"image", "frame", "pixels", "face", "detections", "name", "identity"}


# ---- benchmark helper -------------------------------------------------------------------

def test_benchmark_runs_without_modifying_inputs_and_asserts_no_timing():
    configs = fs.benchmark_configs()
    snap = {k: ([dataclasses.astuple(a[f]) if a[f] is not None else None for f in ("vision", "presence", "lux", "humidity")],
                list(a["temperatures"])) for k, a in configs.items()}
    r = fs.run_benchmark(warmup=1, runs=3, configs=configs)
    assert set(r) == set(configs) and all({"mean_us", "median_us", "p95_us", "runs"} <= set(v) for v in r.values())
    after = {k: ([dataclasses.astuple(a[f]) if a[f] is not None else None for f in ("vision", "presence", "lux", "humidity")],
                 list(a["temperatures"])) for k, a in configs.items()}
    assert after == snap


# ---- Week 6 post-closeout review fixes: telemetry is a formatter, not a decision engine --------------------

from fusion.fuse import FallbackBasis  # noqa: E402


def test_telemetry_returns_exactly_the_stored_fallback_basis():
    for args, want in ((inputs(vis(3), pres(True)), None),
                       (inputs(vis(3, t=99.0), pres(True, t=95.0)), "NEWER_SOURCE"),
                       (inputs(vis(3), pres(True), cfg=CFG_NO_SKEW), "FALLBACK_PRECEDENCE"),
                       (inputs(vis(3, t=95.0), pres(True, t=99.0), cfg=CFG_NO_SKEW), "NEWER_SOURCE")):
        s = fuse(args)
        assert (s.fallback_basis.value if s.fallback_basis else None) == want
        assert fusion_decision_record(s)["fallback_basis"] == want


def test_telemetry_does_not_infer_fallback_basis_from_ages_or_rule_ids():
    tie = fuse(inputs(vis(3), pres(True), cfg=CFG_NO_SKEW))                 # stored: FALLBACK_PRECEDENCE
    assert fusion_decision_record(tie)["fallback_basis"] == "FALLBACK_PRECEDENCE"
    skewed_ages = dataclasses.replace(
        tie, vision_state=dataclasses.replace(tie.vision_state, age_s=1.0), presence_state=dataclasses.replace(tie.presence_state, age_s=9.0))
    assert fusion_decision_record(skewed_ages)["fallback_basis"] == "FALLBACK_PRECEDENCE"       # ages changed, answer did not
    newer = fuse(inputs(vis(3, t=99.0), pres(True, t=95.0)))
    equalised = dataclasses.replace(
        newer, vision_state=dataclasses.replace(newer.vision_state, age_s=2.0), presence_state=dataclasses.replace(newer.presence_state, age_s=2.0))
    assert fusion_decision_record(equalised)["fallback_basis"] == "NEWER_SOURCE"
    no_rule = dataclasses.replace(tie, fallback_rule_id=None, rule_id="R5")
    assert fusion_decision_record(no_rule)["fallback_basis"] == "FALLBACK_PRECEDENCE"           # not derived from rule ids either


def test_changing_the_stored_basis_changes_telemetry_without_recomputation():
    s = fuse(inputs(vis(3, t=99.0), pres(True, t=95.0)))
    for basis, want in ((FallbackBasis.FALLBACK_PRECEDENCE, "FALLBACK_PRECEDENCE"), (FallbackBasis.NEWER_SOURCE, "NEWER_SOURCE"), (None, None)):
        assert fusion_decision_record(dataclasses.replace(s, fallback_basis=basis))["fallback_basis"] == want


def test_telemetry_no_longer_contains_its_own_basis_logic():
    import fusion.telemetry as tel
    assert not hasattr(tel, "_fallback_basis")


def test_telemetry_line_is_one_key_value_token_per_field():
    args = inputs(vis(3), pres(True), lux(), [temp("temp_1", 21.0), temp("t-2", 22.0)], hum())
    rec = fusion_decision_record(fuse(args))
    tokens = format_fusion_decision(rec).split(" ")
    assert len(tokens) == len(rec)
    import re
    assert all(re.fullmatch(r"[a-z_]+=\S*", t) for t in tokens)
    assert [t.split("=", 1)[0] for t in tokens] == list(rec)
