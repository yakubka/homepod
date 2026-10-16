"""Week 6 Task 2: deterministic occupancy fusion (R1-R8) and ClassroomState.

SIMULATED / SYNTHETIC structured inputs only: no hardware, no ESP32 protocol. The max ages and the
skew used here (10 s, 2 s) are TEST VALUES, not calibration; real values are NOT CALIBRATED.
"""

import ast
import copy
import dataclasses
import inspect
import logging

import pytest

import fusion.fuse as fuse_module
from fusion.contracts import (
    FusionContractError, FusionFreshnessConfig, HumidityReading, LuxReading, Origin, PresenceReading, SourceEnvelope,
    SourceStatus, TemperatureReading, Usability, VisionObservation,
)
from fusion.fuse import ClassroomState, DegradedReason as D, EvidenceState as E, FusedPresence as F, fuse_classroom_state
from tests import zone_synthetic as zs
from vision.zones import assign_detections

NOW = 100.0
CFG = FusionFreshnessConfig(vision_max_age_s=10.0, presence_max_age_s=10.0, lux_max_age_s=10.0,
                            temperature_max_age_s=10.0, humidity_max_age_s=10.0, max_source_skew_s=2.0)
ZONES = zs.zone_set(zs.GAPPED_DEFS)


def env(sid, t=99.0, origin=Origin.SIMULATED, status=SourceStatus.OK):
    return SourceEnvelope(sid, origin, t, status, None if status is SourceStatus.OK else "synthetic fault")


def result(n, zones=True, outside=0):
    dets = [zs.person(50 + 40 * i) for i in range(n)] + [zs.person(500, 60) for _ in range(outside)]
    return assign_detections(dets, zs.SYNTHETIC_CLASSROOM, ZONES if zones else None)


def vis(n, t=99.0, origin=Origin.SIMULATED, zones=True, outside=0):
    return VisionObservation(env("cam", t, origin), result(n, zones, outside))


def pres(present, t=99.0, origin=Origin.SIMULATED):
    return PresenceReading(env("radar", t, origin), present)


def fuse(v=None, p=None, lux=None, temps=(), hum=None, now=NOW, cfg=CFG):
    return fuse_classroom_state(v, p, lux, temps, hum, now, cfg)


def check(s, rule, evidence, fused, degraded, reasons=()):
    assert (s.rule_id, s.occupancy.evidence, s.occupancy.fused_presence, s.degraded) == (rule, evidence, fused, degraded)
    assert s.degraded_reasons == tuple(reasons)


# ---- the decision table -----------------------------------------------------------------

def test_r1_agreement_occupied():
    s = fuse(vis(3), pres(True))
    check(s, "R1", E.CONFIRMED, F.PRESENT, False)
    assert s.occupancy.vision_count == 3 and s.fallback_rule_id is None
    assert s.occupancy.vision_used and s.occupancy.presence_used


def test_r2_agreement_empty():
    s = fuse(vis(0), pres(False))
    check(s, "R2", E.CONFIRMED_EMPTY, F.ABSENT, False)
    assert s.occupancy.vision_count == 0


def test_r3_presence_without_detections_is_a_conflict_and_count_stays_zero():
    s = fuse(vis(0), pres(True))
    check(s, "R3", E.CONFLICT, F.UNKNOWN, True, [D.PRESENCE_WITHOUT_DETECTIONS])
    assert s.occupancy.vision_count == 0 and s.occupancy.vision_count is not None
    assert s.occupancy.sensor_present is True and s.occupancy.raw_presence is True      # evidence stays visible


def test_r4_detections_without_presence_is_a_conflict_and_count_kept():
    s = fuse(vis(4), pres(False))
    check(s, "R4", E.CONFLICT, F.UNKNOWN, True, [D.DETECTIONS_WITHOUT_PRESENCE])
    assert s.occupancy.vision_count == 4 and s.occupancy.sensor_present is False


def test_r5a_vision_only_positive_is_present():
    s = fuse(vis(2))
    check(s, "R5", E.VISION_ONLY, F.PRESENT, True, [D.PRESENCE_MISSING])
    assert s.occupancy.vision_count == 2


def test_r5b_vision_only_zero_is_unknown_not_absent():
    s = fuse(vis(0))
    check(s, "R5", E.VISION_ONLY, F.UNKNOWN, True, [D.PRESENCE_MISSING])
    assert s.occupancy.vision_count == 0


def test_r6a_radar_present_only():
    s = fuse(None, pres(True))
    check(s, "R6", E.SENSOR_ONLY, F.PRESENT, True, [D.VISION_MISSING])
    assert s.occupancy.vision_count is None and s.occupancy.zone_counts is None


def test_r6b_radar_absent_only():
    s = fuse(None, pres(False))
    check(s, "R6", E.SENSOR_ONLY, F.ABSENT, True, [D.VISION_MISSING])
    assert s.occupancy.vision_count is None


def test_r7_no_usable_occupancy_source():
    s = fuse()
    check(s, "R7", E.UNKNOWN, F.UNKNOWN, True, [D.VISION_MISSING, D.PRESENCE_MISSING])
    assert s.occupancy.vision_count is None and s.occupancy.sensor_present is None


# ---- R8 skew ----------------------------------------------------------------------------

def test_r8a_vision_newer_excludes_radar_only():
    s = fuse(vis(3, t=99.0), pres(True, t=95.0))          # skew 4 > 2, radar older
    check(s, "R8", E.VISION_ONLY, F.PRESENT, True, [D.SOURCE_SKEW])
    assert s.fallback_rule_id == "R5"
    assert s.occupancy.vision_used and not s.occupancy.presence_used
    assert s.presence_state.usability is Usability.FRESH                       # not marked stale
    assert s.occupancy.sensor_present is True and s.occupancy.raw_presence is True and s.presence_state.origin is Origin.SIMULATED


def test_r8b_radar_newer_excludes_vision_only():
    s = fuse(vis(3, t=95.0), pres(False, t=99.0))
    check(s, "R8", E.SENSOR_ONLY, F.ABSENT, True, [D.SOURCE_SKEW])
    assert s.fallback_rule_id == "R6"
    assert s.occupancy.vision_count is None and s.occupancy.zone_counts is None and not s.occupancy.vision_used
    assert s.vision_state.usability is Usability.FRESH and s.occupancy.raw_vision_count == 3   # still visible


def test_skew_exactly_at_max_allows_corroboration():
    s = fuse(vis(3, t=99.0), pres(True, t=97.0))           # skew exactly 2.0
    assert s.rule_id == "R1" and s.occupancy.evidence is E.CONFIRMED


def test_skew_just_beyond_max_excludes_older_source():
    s = fuse(vis(3, t=99.0), pres(True, t=96.999))
    assert s.rule_id == "R8" and s.occupancy.evidence is not E.CONFIRMED


def test_unconfigured_skew_never_confirms():
    cfg = dataclasses.replace(CFG, max_source_skew_s=None)
    s = fuse(vis(3), pres(True), cfg=cfg)                    # identical timestamps
    assert s.rule_id == "R8" and s.fallback_rule_id == "R5" and s.occupancy.evidence is E.VISION_ONLY
    assert s.degraded_reasons == (D.SKEW_UNCONFIGURED,) and not s.occupancy.presence_used
    s2 = fuse(vis(3, t=95.0), pres(True, t=99.0), cfg=cfg)   # radar newer: radar kept
    assert s2.rule_id == "R8" and s2.fallback_rule_id == "R6" and s2.occupancy.evidence is E.SENSOR_ONLY


def test_skew_does_not_apply_when_a_source_is_already_unusable():
    s = fuse(vis(3, t=50.0), pres(True, t=99.0))             # vision stale, not skew
    assert s.rule_id == "R6" and s.degraded_reasons == (D.VISION_STALE,) and s.fallback_rule_id is None


# ---- unusable sources -------------------------------------------------------------------

def test_stale_vision():
    s = fuse(vis(5, t=80.0), pres(True))
    check(s, "R6", E.SENSOR_ONLY, F.PRESENT, True, [D.VISION_STALE])
    assert s.occupancy.vision_count is None and s.occupancy.raw_vision_count == 5   # diagnostic only
    assert s.vision_state.usability is Usability.STALE and s.vision_state.age_s == 20.0


def test_stale_radar():
    s = fuse(vis(2), pres(False, t=80.0))
    check(s, "R5", E.VISION_ONLY, F.PRESENT, True, [D.PRESENCE_STALE])
    assert s.occupancy.sensor_present is None and s.occupancy.raw_presence is False


def test_failed_vision():
    s = fuse(VisionObservation(env("cam", status=SourceStatus.FAILED)), pres(True))
    check(s, "R6", E.SENSOR_ONLY, F.PRESENT, True, [D.VISION_INVALID])
    assert s.vision_state.source_status is SourceStatus.FAILED and s.occupancy.vision_count is None


def test_unavailable_radar():
    s = fuse(vis(2), PresenceReading(env("radar", status=SourceStatus.UNAVAILABLE)))
    check(s, "R5", E.VISION_ONLY, F.PRESENT, True, [D.PRESENCE_INVALID])
    assert s.occupancy.raw_presence is None and s.occupancy.sensor_present is None


def test_unconfigured_freshness_makes_source_unusable_not_zero():
    cfg = dataclasses.replace(CFG, vision_max_age_s=None)
    s = fuse(vis(0), pres(True), cfg=cfg)
    check(s, "R6", E.SENSOR_ONLY, F.PRESENT, True, [D.VISION_UNCONFIGURED])
    assert s.occupancy.vision_count is None


def test_reasons_are_deterministically_ordered():
    s = fuse(vis(1, t=10.0), pres(True, t=10.0))
    assert s.degraded_reasons == (D.VISION_STALE, D.PRESENCE_STALE)
    order = list(D)
    assert list(s.degraded_reasons) == sorted(s.degraded_reasons, key=order.index)


# ---- count and zone preservation --------------------------------------------------------

@pytest.mark.parametrize("n", [0, 3, 7])
@pytest.mark.parametrize("radar", [True, False, None])
def test_radar_never_changes_the_numeric_count(n, radar):
    s = fuse(vis(n), None if radar is None else pres(radar))
    assert s.occupancy.vision_count == n and s.occupancy.raw_vision_count == n


def test_known_detector_limitation_is_not_corrected_numerically():
    s = fuse(vis(0), pres(True))
    assert s.occupancy.vision_count == 0 and s.occupancy.evidence is E.CONFLICT and s.occupancy.fused_presence is F.UNKNOWN
    s3 = fuse(vis(3), pres(True))
    assert s3.occupancy.vision_count == 3


def test_zone_counts_preserved_from_week_5():
    v = vis(3, outside=2)
    s = fuse(v, pres(True))
    r = v.occupancy
    assert s.occupancy.zone_counts is r.zone_counts and dict(s.occupancy.zone_counts) == {"LEFT": 3, "CENTER": 0, "RIGHT": 0}
    assert s.occupancy.unassigned_inside_roi == r.unassigned_inside_roi == 0
    assert s.occupancy.outside_classroom_roi == 2 and s.occupancy.zones_configured is True


def test_unconfigured_zones_are_not_fabricated():
    s = fuse(vis(4, zones=False), pres(True))
    assert s.occupancy.zones_configured is False
    assert s.occupancy.zone_counts is None and s.occupancy.unassigned_inside_roi is None     # not LEFT=0 / UNASSIGNED=4
    assert s.occupancy.vision_count == 4


def test_radar_does_not_fabricate_zone_counts():
    for s in (fuse(None, pres(True)), fuse(vis(3, t=95.0), pres(True, t=99.0))):
        o = s.occupancy
        assert (o.zone_counts, o.unassigned_inside_roi, o.outside_classroom_roi, o.zones_configured, o.vision_count) == (None,) * 5


# ---- environment ------------------------------------------------------------------------

def test_environment_values_pass_through_when_fresh():
    s = fuse(vis(1), pres(True), LuxReading(env("esp", 99.0), 0.0), [TemperatureReading(env("esp"), "t1", 21.5)],
             HumidityReading(env("esp"), 40.0))
    e = s.environment
    assert e.lux.value == 0.0 and e.lux.state.usability is Usability.FRESH
    assert e.temperatures[0].value == 21.5 and e.humidity.value == 40.0 and e.issues == ()


def test_stale_lux_is_excluded_as_a_usable_value():
    e = fuse(vis(1), pres(True), LuxReading(env("esp", 10.0), 500.0)).environment
    assert e.lux.value is None and e.lux.state.usability is Usability.STALE and e.lux.state.age_s == 90.0
    assert "LUX_STALE" in e.issues


def test_missing_environment_is_none_not_zero():
    e = fuse(vis(1), pres(True)).environment
    assert e.lux.value is None and e.humidity.value is None and e.temperatures == ()
    assert e.issues == ("LUX_MISSING", "HUMIDITY_MISSING")


def test_unconfigured_humidity_does_not_break_occupancy():
    cfg = dataclasses.replace(CFG, humidity_max_age_s=None)
    s = fuse(vis(2), pres(True), hum=HumidityReading(env("esp"), 50.0), cfg=cfg)
    assert s.rule_id == "R1" and not s.degraded and s.environment.humidity.value is None
    assert s.environment.humidity.state.usability is Usability.UNCONFIGURED


def test_multiple_temperature_sensors_stay_separate():
    a, b = TemperatureReading(env("esp"), "t-a", 20.0), TemperatureReading(env("esp"), "t-b", 26.0)
    e = fuse(vis(1), pres(True), temps=[b, a]).environment
    assert [(t.sensor_id, t.value) for t in e.temperatures] == [("t-b", 26.0), ("t-a", 20.0)]     # input order, no mean
    assert not hasattr(e, "room_temperature")
    stale = TemperatureReading(env("esp", 10.0), "t-c", 30.0)
    e2 = fuse(vis(1), pres(True), temps=[a, stale]).environment
    assert e2.temperatures[0].value == 20.0 and e2.temperatures[1].value is None
    assert "TEMPERATURE[t-c]_STALE" in e2.issues and not any("t-a" in i for i in e2.issues)


def test_duplicate_temperature_ids_rejected():
    t = TemperatureReading(env("esp"), "t1", 20.0)
    with pytest.raises(FusionContractError, match="duplicate"):
        fuse(vis(1), pres(True), temps=[t, t])


@pytest.mark.parametrize("occ", [(vis(3), pres(True)), (vis(0), pres(True)), (vis(2), pres(False)), (vis(0), pres(False)), (vis(2), None), (None, pres(True))])
def test_environment_does_not_change_occupancy_rule_evidence_or_count(occ):
    base = fuse(*occ)
    variants = [
        dict(lux=LuxReading(env("esp"), 0.0)), dict(lux=LuxReading(env("esp"), 900.0)), dict(),
        dict(temps=[TemperatureReading(env("esp"), "t1", 30.0)]), dict(temps=[TemperatureReading(env("esp", 10.0), "t1", 30.0)]),
        dict(hum=HumidityReading(env("esp"), 100.0)), dict(hum=HumidityReading(env("esp", status=SourceStatus.FAILED))),
    ]
    for kw in variants:
        s = fuse(*occ, **kw)
        assert (s.rule_id, s.fallback_rule_id, s.occupancy, s.degraded, s.degraded_reasons) == \
               (base.rule_id, base.fallback_rule_id, base.occupancy, base.degraded, base.degraded_reasons)


def test_no_environment_interpretation_or_actuator_fields():
    names = {f.name for c in (fuse_module.EnvironmentState, fuse_module.EnvironmentValue, fuse_module.OccupancyState, ClassroomState)
             for f in dataclasses.fields(c)}
    assert not names & {"dark", "bright", "hot", "cold", "comfort", "command", "actuator", "light_command", "fan"}


# ---- provenance -------------------------------------------------------------------------

def test_per_source_origin_preserved():
    s = fuse(vis(1, origin=Origin.REAL), pres(True, origin=Origin.SIMULATED))
    assert (s.vision_state.origin, s.presence_state.origin) == (Origin.REAL, Origin.SIMULATED)


def test_aggregate_origin_is_conservative():
    assert fuse(vis(1), pres(True)).aggregate_origin is Origin.SIMULATED
    assert fuse(vis(1, origin=Origin.REAL), pres(True, origin=Origin.SIMULATED)).aggregate_origin is Origin.SIMULATED
    assert fuse(vis(1, origin=Origin.REAL), pres(True, origin=Origin.REAL)).aggregate_origin is Origin.REAL
    assert fuse(vis(1, origin=Origin.REAL), pres(True, origin=Origin.UNKNOWN)).aggregate_origin is Origin.UNKNOWN
    assert fuse(vis(1, origin=Origin.UNKNOWN), pres(True, origin=Origin.SIMULATED)).aggregate_origin is Origin.UNKNOWN
    assert fuse().aggregate_origin is Origin.UNKNOWN                                     # nothing contributes


def test_simulated_environment_value_taints_an_otherwise_real_aggregate():
    s = fuse(vis(1, origin=Origin.REAL), pres(True, origin=Origin.REAL), lux=LuxReading(env("esp", origin=Origin.SIMULATED), 5.0))
    assert s.aggregate_origin is Origin.SIMULATED


def test_excluded_and_unusable_sources_do_not_contribute_to_aggregate():
    assert fuse(vis(1, origin=Origin.SIMULATED, t=99.0), pres(True, origin=Origin.REAL, t=95.0)).aggregate_origin is Origin.SIMULATED
    assert fuse(vis(1, origin=Origin.UNKNOWN, t=10.0), pres(True, origin=Origin.REAL)).aggregate_origin is Origin.REAL   # stale UNKNOWN excluded
    assert fuse(vis(1, origin=Origin.UNKNOWN, t=10.0), pres(True, origin=Origin.REAL)).vision_state.origin is Origin.UNKNOWN  # but still visible


# ---- purity, determinism, immutability --------------------------------------------------

def test_identical_inputs_give_identical_output():
    args = (vis(3), pres(True), LuxReading(env("esp"), 5.0), [TemperatureReading(env("esp"), "t1", 20.0)], HumidityReading(env("esp"), 40.0))
    assert fuse_classroom_state(*args, NOW, CFG) == fuse_classroom_state(*args, NOW, CFG)


def test_inputs_are_not_mutated():
    v, p, l = vis(3), pres(True), LuxReading(env("esp"), 5.0)
    t = [TemperatureReading(env("esp"), "t1", 20.0)]
    before = (copy.deepcopy(dataclasses.astuple(v)), dataclasses.astuple(p), dataclasses.astuple(l), list(t))
    s = fuse(v, p, l, t)
    assert (dataclasses.astuple(v), dataclasses.astuple(p), dataclasses.astuple(l), list(t)) == before
    assert s.occupancy.zone_counts is v.occupancy.zone_counts                      # shared, not copied


def test_fuse_is_pure_no_clock_logging_or_globals(caplog):
    tree = ast.parse(inspect.getsource(fuse_module))
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"time", "datetime", "logging", "random", "os", "serial"}
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Global, ast.Nonlocal))]
    with caplog.at_level(logging.DEBUG):
        fuse(vis(1), pres(True))
    assert caplog.records == []
    assert fuse(vis(1), pres(True), now=NOW) == fuse(vis(1), pres(True), now=NOW)  # depends only on its arguments


def test_classroom_state_is_immutable():
    s = fuse(vis(1), pres(True))
    for target, field in ((s, "rule_id"), (s.occupancy, "vision_count"), (s.environment, "issues")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(target, field, None)
    assert isinstance(s.degraded_reasons, tuple) and isinstance(s.environment.temperatures, tuple)


def test_invalid_inputs_fail_clearly():
    with pytest.raises(FusionContractError, match="vision"):
        fuse_classroom_state("cam", None, None, (), None, NOW, CFG)
    with pytest.raises(FusionContractError, match="freshness"):
        fuse_classroom_state(None, None, None, (), None, NOW, None)
    with pytest.raises(FusionContractError, match="earlier than received_at_monotonic_s"):
        fuse(vis(1, t=101.0), pres(True))
    with pytest.raises(FusionContractError, match=r"temperatures\[0\]"):
        fuse(temps=[object()])


def test_evaluated_time_is_the_supplied_now():
    assert fuse(now=123.5).evaluated_at_monotonic_s == 123.5


# ---- Week 6 post-closeout review fixes --------------------------------------------------

def _valid_temp(i="t1"):
    return TemperatureReading(env("esp"), i, 20.0)


@pytest.mark.parametrize("bad", [None, 123, "abc", b"abc", 4.5])
def test_invalid_temperatures_argument_raises_contract_error_not_typeerror(bad):
    with pytest.raises(FusionContractError, match="temperatures must be an iterable"):
        fuse_classroom_state(None, None, None, bad, None, NOW, CFG)


def test_valid_temperatures_forms_are_accepted():
    assert len(fuse(temps=[_valid_temp()]).environment.temperatures) == 1
    assert len(fuse(temps=(_valid_temp("a"), _valid_temp("b"))).environment.temperatures) == 2
    assert fuse(temps=(_valid_temp(i) for i in ("a", "b", "c"))).environment.temperatures[2].sensor_id == "c"
    assert fuse(temps=()).environment.temperatures == () and fuse(temps=[]).environment.temperatures == ()   # no expected ids are configured


def test_wrong_element_in_temperatures_names_the_offender():
    with pytest.raises(FusionContractError, match=r"temperatures\[1\].*str"):
        fuse(temps=[_valid_temp(), "t2"])
    with pytest.raises(FusionContractError, match=r"temperatures\[0\].*LuxReading"):
        fuse(temps=[LuxReading(env("esp"), 1.0)])


def test_errors_raised_by_a_valid_iterable_are_not_swallowed():
    def broken():
        yield _valid_temp("a")
        raise RuntimeError("iterator failed")
    with pytest.raises(RuntimeError, match="iterator failed"):
        fuse(temps=broken())


def test_reading_received_after_now_is_a_snapshot_order_violation_not_clamped():
    """Integration precondition: snapshot the readings first, then sample now. Even an optional sensor aborts the call."""
    late = 100.5                                              # received after NOW (100.0)
    for kw in (dict(lux=LuxReading(env("esp", late), 5.0)), dict(hum=HumidityReading(env("esp", late), 40.0)),
               dict(temps=[TemperatureReading(env("esp", late), "t1", 20.0)])):
        with pytest.raises(FusionContractError, match="earlier than received_at_monotonic_s"):
            fuse(vis(1), pres(True), **kw)
    with pytest.raises(FusionContractError, match="earlier than received_at_monotonic_s"):
        fuse(vis(1), pres(True, t=late))


def test_uncalibrated_skew_can_make_the_fused_count_none_while_raw_count_survives():
    """KNOWN UNCALIBRATED-INTEGRATION LIMITATION: None means 'not usable for fusion', never zero people."""
    cfg = dataclasses.replace(CFG, max_source_skew_s=None)
    older_vision = fuse(vis(5, t=98.0), pres(True, t=99.0), cfg=cfg)
    assert older_vision.occupancy.vision_count is None and older_vision.occupancy.raw_vision_count == 5
    assert older_vision.occupancy.vision_count != 0
    tie_or_newer = fuse(vis(5, t=99.0), pres(True, t=98.0), cfg=cfg)
    assert tie_or_newer.occupancy.vision_count == 5                        # so the fused count alternates between 5 and None
    assert older_vision.degraded and D.SKEW_UNCONFIGURED in older_vision.degraded_reasons


def test_fallback_basis_is_stored_in_the_state():
    from fusion.fuse import FallbackBasis
    cfg_none = dataclasses.replace(CFG, max_source_skew_s=None)
    for s in (fuse(vis(3), pres(True)), fuse(vis(0), pres(True)), fuse(vis(2), pres(False)), fuse(vis(2)), fuse(None, pres(True)), fuse()):
        assert s.rule_id != "R8" and s.fallback_basis is None                  # R1-R7 have no basis
    assert fuse(vis(3), pres(True, t=99.0), cfg=CFG).fallback_basis is None   # configured equal timestamps: no R8
    newer_v = fuse(vis(3, t=99.0), pres(True, t=95.0))
    newer_r = fuse(vis(3, t=95.0), pres(True, t=99.0))
    assert newer_v.fallback_basis is FallbackBasis.NEWER_SOURCE and newer_r.fallback_basis is FallbackBasis.NEWER_SOURCE
    tie = fuse(vis(3, t=99.0), pres(True, t=99.0), cfg=cfg_none)
    assert (tie.rule_id, tie.fallback_rule_id, tie.fallback_basis) == ("R8", "R5", FallbackBasis.FALLBACK_PRECEDENCE)
    assert fuse(vis(3, t=95.0), pres(True, t=99.0), cfg=cfg_none).fallback_basis is FallbackBasis.NEWER_SOURCE
    assert (fuse(vis(3, t=99.0), pres(True, t=95.0), cfg=cfg_none).fallback_basis) is FallbackBasis.NEWER_SOURCE
