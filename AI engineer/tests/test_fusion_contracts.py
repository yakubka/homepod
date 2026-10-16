"""Week 6 Task 1: fusion input contracts and source-state semantics.

SIMULATED / SYNTHETIC inputs only. No hardware, no ESP32 protocol, no drivers. Every fixture
uses Origin.SIMULATED unless a test is specifically about provenance or the contract structure.
"""

import dataclasses
import math

import pytest

from config import settings
from fusion.contracts import (
    FusionContractError, FusionFreshnessConfig, HumidityReading, LuxReading, Origin, PresenceReading,
    SourceEnvelope, SourceState, SourceStatus, TemperatureReading, Unit, Usability, VisionObservation,
    derive_source_state, freshness_from_settings, fresh_value,
)
from vision.detector import Detection
from vision.roi import Roi
from vision.zones import assign_detections


def env(sid="sim-1", origin=Origin.SIMULATED, t=100.0, status=SourceStatus.OK, reason=None, dt=None):
    return SourceEnvelope(sid, origin, t, status, reason, dt)


def bad_env(status):
    return env(status=status, reason="synthetic fault")


def occupancy(n=2):
    dets = [Detection(100 * i, 100, 100 * i + 40, 200, 0.9, frame_width=1000, frame_height=500) for i in range(1, n + 1)]
    return assign_detections(dets, Roi.rectangle(0.0, 0.0, 1.0, 1.0))


# ---- A. common envelope -----------------------------------------------------------------

def test_valid_simulated_envelope_keeps_origin():
    e = env(origin=Origin.SIMULATED)
    assert e.origin is Origin.SIMULATED and e.ok and e.error_reason is None


def test_valid_real_envelope_structure():
    e = env(origin=Origin.REAL, dt="esp32-tick-123")
    assert e.origin is Origin.REAL and e.device_timestamp == "esp32-tick-123"
    assert env(origin=Origin.UNKNOWN).origin is Origin.UNKNOWN


@pytest.mark.parametrize("sid", ["", "   ", None, 5])
def test_empty_or_bad_source_id_rejected(sid):
    with pytest.raises(FusionContractError, match="source_id"):
        SourceEnvelope(sid, Origin.SIMULATED, 1.0)


@pytest.mark.parametrize("t", [float("nan"), float("inf"), -float("inf"), "1.0", None, True])
def test_malformed_receive_timestamp_rejected(t):
    with pytest.raises(FusionContractError, match="received_at_monotonic_s"):
        SourceEnvelope("s", Origin.SIMULATED, t)


@pytest.mark.parametrize("dt", [float("nan"), True, ""])
def test_malformed_device_timestamp_rejected(dt):
    with pytest.raises(FusionContractError, match="device_timestamp"):
        SourceEnvelope("s", Origin.SIMULATED, 1.0, device_timestamp=dt)


def test_failed_needs_reason_and_ok_must_not_have_one():
    with pytest.raises(FusionContractError, match="FAILED"):
        SourceEnvelope("s", Origin.SIMULATED, 1.0, SourceStatus.FAILED)
    with pytest.raises(FusionContractError, match="OK source must not"):
        SourceEnvelope("s", Origin.SIMULATED, 1.0, SourceStatus.OK, "oops")
    assert SourceEnvelope("s", Origin.SIMULATED, 1.0, SourceStatus.UNAVAILABLE).error_reason is None


def test_origin_and_status_must_be_enums():
    with pytest.raises(FusionContractError, match="origin"):
        SourceEnvelope("s", "REAL", 1.0)
    with pytest.raises(FusionContractError, match="source_status"):
        SourceEnvelope("s", Origin.REAL, 1.0, "OK")


# ---- B. vision --------------------------------------------------------------------------

def test_vision_ok_with_result():
    occ = occupancy(2)
    v = VisionObservation(env(), occ)
    assert v.occupancy is occ and v.occupancy.occupancy_count == 2        # same object, Week 5 meaning


def test_vision_ok_without_result_rejected():
    with pytest.raises(FusionContractError, match="occupancy is required"):
        VisionObservation(env())


@pytest.mark.parametrize("status", [SourceStatus.UNAVAILABLE, SourceStatus.FAILED])
def test_unavailable_vision_with_result_rejected(status):
    with pytest.raises(FusionContractError, match="must be None"):
        VisionObservation(bad_env(status), occupancy(1))


def test_vision_result_must_be_zone_occupancy_result():
    with pytest.raises(FusionContractError, match="ZoneOccupancyResult"):
        VisionObservation(env(), 3)


def test_unavailable_vision_is_not_count_zero():
    v = VisionObservation(bad_env(SourceStatus.UNAVAILABLE))
    assert v.occupancy is None
    assert v.occupancy != 0 and not hasattr(v, "count")
    zero = VisionObservation(env(), occupancy(0))
    assert zero.occupancy is not None and zero.occupancy.occupancy_count == 0   # a real zero is different


# ---- C. presence ------------------------------------------------------------------------

def test_presence_true_and_false_are_both_valid_readings():
    assert PresenceReading(env(), True).present is True
    assert PresenceReading(env(), False).present is False


@pytest.mark.parametrize("status", [SourceStatus.UNAVAILABLE, SourceStatus.FAILED])
def test_unavailable_presence_is_none_not_false(status):
    p = PresenceReading(bad_env(status))
    assert p.present is None and p.present is not False


def test_presence_must_be_bool_and_absent_when_not_ok():
    with pytest.raises(FusionContractError, match="bool"):
        PresenceReading(env(), 1)
    with pytest.raises(FusionContractError, match="required"):
        PresenceReading(env())
    with pytest.raises(FusionContractError, match="must be None"):
        PresenceReading(bad_env(SourceStatus.FAILED), False)


# ---- D. lux -----------------------------------------------------------------------------

def test_zero_and_positive_lux_valid():
    assert LuxReading(env(), 0.0).value == 0.0
    assert LuxReading(env(), 350.5).value == 350.5


def test_negative_lux_rejected():
    with pytest.raises(FusionContractError, match=">= 0"):
        LuxReading(env(), -0.1)


@pytest.mark.parametrize("v", [float("nan"), float("inf"), -float("inf"), "5", True])
def test_non_finite_or_non_numeric_lux_rejected(v):
    with pytest.raises(FusionContractError, match="finite"):
        LuxReading(env(), v)


@pytest.mark.parametrize("unit", [Unit.CELSIUS, Unit.PERCENT_RH, "lx", None])
def test_wrong_lux_unit_rejected(unit):
    with pytest.raises(FusionContractError, match="unit"):
        LuxReading(env(), 10.0, unit)


def test_missing_lux_is_not_zero():
    state = derive_source_state(None, 100.0, 5.0, "lux-sensor")
    assert state.usability is Usability.MISSING and fresh_value(None, state) is None
    assert fresh_value(None, state) != 0
    failed = LuxReading(bad_env(SourceStatus.FAILED))
    assert failed.value is None and failed.value != 0


# ---- E. temperature ---------------------------------------------------------------------

def test_valid_celsius_and_boundary_at_absolute_zero():
    assert TemperatureReading(env(), "t1", 22.5).unit is Unit.CELSIUS
    assert TemperatureReading(env(), "t1", -273.15).value == -273.15
    assert TemperatureReading(env(), "t1", 0.0).value == 0.0            # 0 degC is a normal value


def test_below_absolute_zero_rejected():
    with pytest.raises(FusionContractError, match="absolute zero"):
        TemperatureReading(env(), "t1", -273.16)


@pytest.mark.parametrize("v", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_temperature_rejected(v):
    with pytest.raises(FusionContractError, match="finite"):
        TemperatureReading(env(), "t1", v)


@pytest.mark.parametrize("unit", ["kelvin", Unit.LUX, "degF", None])
def test_unknown_temperature_unit_rejected(unit):
    with pytest.raises(FusionContractError, match="unit"):
        TemperatureReading(env(), "t1", 20.0, unit)


def test_two_temperature_sensors_coexist_and_sensor_id_required():
    a, b = TemperatureReading(env(), "temp-a", 21.0), TemperatureReading(env(), "temp-b", 23.0)
    assert (a.sensor_id, b.sensor_id) == ("temp-a", "temp-b") and a != b
    with pytest.raises(FusionContractError, match="sensor_id"):
        TemperatureReading(env(), "", 21.0)


# ---- F. humidity ------------------------------------------------------------------------

@pytest.mark.parametrize("v", [0, 0.0, 55.5, 100, 100.0])
def test_valid_humidity(v):
    assert HumidityReading(env(), v).value == v


@pytest.mark.parametrize("v", [-0.01, 100.01, 250])
def test_out_of_range_humidity_rejected(v):
    with pytest.raises(FusionContractError, match="0..100"):
        HumidityReading(env(), v)


@pytest.mark.parametrize("v", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_humidity_rejected(v):
    with pytest.raises(FusionContractError, match="finite"):
        HumidityReading(env(), v)


def test_humidity_unit_and_optional_sensor_id():
    assert HumidityReading(env(), 40.0).sensor_id is None
    assert HumidityReading(env(), 40.0, sensor_id="rh-1").sensor_id == "rh-1"
    with pytest.raises(FusionContractError, match="unit"):
        HumidityReading(env(), 40.0, Unit.LUX)
    with pytest.raises(FusionContractError, match="sensor_id"):
        HumidityReading(env(), 40.0, sensor_id=" ")


def test_missing_temperature_and_humidity_are_not_zero():
    for r in (TemperatureReading(bad_env(SourceStatus.UNAVAILABLE), "t1"), HumidityReading(bad_env(SourceStatus.UNAVAILABLE))):
        assert r.value is None and r.value != 0
        assert fresh_value(r, derive_source_state(r, 100.0, 10.0)) is None


def test_missing_presence_is_not_false_and_missing_vision_is_not_count_zero():
    assert fresh_value(None, derive_source_state(None, 1.0, 1.0)) is None
    assert fresh_value(None, derive_source_state(None, 1.0, 1.0)) is not False


# ---- G. freshness -----------------------------------------------------------------------

def lux(t=100.0, **kw):
    return LuxReading(env(t=t, **kw), 50.0)


def test_no_reading_is_missing_and_keeps_expected_id():
    s = derive_source_state(None, 100.0, 5.0, "lux-sensor")
    assert (s.usability, s.source_id, s.origin, s.source_status, s.age_s) == (Usability.MISSING, "lux-sensor", None, None, None)


def test_no_configured_max_age_is_unconfigured_not_fresh():
    s = derive_source_state(lux(), 101.0, None)
    assert s.usability is Usability.UNCONFIGURED and s.age_s == 1.0 and s.max_age_s is None
    assert fresh_value(lux(), s) is None


def test_exactly_max_age_is_fresh_inclusive_boundary():
    s = derive_source_state(lux(t=100.0), 105.0, 5.0)
    assert s.usability is Usability.FRESH and s.age_s == 5.0


def test_just_beyond_max_age_is_stale():
    s = derive_source_state(lux(t=100.0), 105.001, 5.0)
    assert s.usability is Usability.STALE


def test_zero_age_and_zero_max_age():
    assert derive_source_state(lux(t=100.0), 100.0, 0.0).usability is Usability.FRESH
    assert derive_source_state(lux(t=100.0), 100.5, 0.0).usability is Usability.STALE


@pytest.mark.parametrize("status", [SourceStatus.FAILED, SourceStatus.UNAVAILABLE])
def test_failed_or_unavailable_source_is_never_fresh(status):
    r = LuxReading(bad_env(status))
    s = derive_source_state(r, 100.0, 1000.0)
    assert s.usability is Usability.INVALID and s.source_status is status
    assert fresh_value(r, s) is None
    assert derive_source_state(r, 100.0, None).usability is Usability.INVALID      # config not needed to know it failed


def test_now_earlier_than_receive_time_rejected():
    with pytest.raises(FusionContractError, match="earlier than received_at_monotonic_s"):
        derive_source_state(lux(t=100.0), 99.9, 5.0)


def test_stale_reading_keeps_age_origin_and_reason():
    s = derive_source_state(lux(t=100.0), 130.0, 5.0)
    assert (s.usability, s.age_s, s.origin, s.source_id) == (Usability.STALE, 30.0, Origin.SIMULATED, "sim-1")
    assert "exceeds" in s.reason and s.max_age_s == 5.0
    assert fresh_value(lux(t=100.0), s) is None                                    # kept for diagnostics, not used


def test_failed_reading_keeps_reason():
    s = derive_source_state(LuxReading(env(status=SourceStatus.FAILED, reason="bus error")), 101.0, 5.0)
    assert s.reason == "bus error" and s.age_s == 1.0


def test_fresh_value_returns_the_real_values():
    occ = occupancy(1)
    for reading, want in ((VisionObservation(env(), occ), occ), (PresenceReading(env(), False), False),
                          (LuxReading(env(), 0.0), 0.0), (TemperatureReading(env(), "t", 0.0), 0.0),
                          (HumidityReading(env(), 0.0), 0.0)):
        got = fresh_value(reading, derive_source_state(reading, 100.0, 1.0))
        assert got is want or got == want
    assert fresh_value(PresenceReading(env(), False), derive_source_state(PresenceReading(env(), False), 100.0, 1.0)) is False


def test_freshness_uses_receive_time_not_device_timestamp():
    r = LuxReading(env(t=100.0, dt=0.0), 10.0)          # device clock says 0, host says it arrived at t = 100
    assert derive_source_state(r, 103.0, 5.0).usability is Usability.FRESH
    r2 = LuxReading(env(t=100.0, dt=10_000.0), 10.0)     # device clock far in the future must not make it look new/young
    s = derive_source_state(r2, 130.0, 5.0)
    assert s.usability is Usability.STALE and s.age_s == 30.0


def test_invalid_now_or_max_age_rejected_and_unsupported_reading():
    with pytest.raises(FusionContractError, match="now_monotonic_s"):
        derive_source_state(lux(), float("nan"), 5.0)
    with pytest.raises(FusionContractError, match="max_age_s"):
        derive_source_state(lux(), 101.0, -1.0)
    with pytest.raises(FusionContractError, match="unsupported reading type"):
        derive_source_state("not a reading", 1.0, 1.0)


def test_derivation_is_deterministic():
    r = lux()
    assert derive_source_state(r, 103.0, 5.0) == derive_source_state(r, 103.0, 5.0)


# ---- configuration ----------------------------------------------------------------------

def test_default_settings_are_uncalibrated():
    cfg = freshness_from_settings()
    assert cfg == FusionFreshnessConfig() and all(v is None for v in dataclasses.astuple(cfg))
    assert settings.FUSION_VISION_MAX_AGE_S is None and settings.FUSION_MAX_SOURCE_SKEW_S is None


def test_settings_loader_reads_configured_values(monkeypatch):
    monkeypatch.setattr(settings, "FUSION_LUX_MAX_AGE_S", 7.5)
    assert freshness_from_settings().lux_max_age_s == 7.5


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), True, "5"])
def test_invalid_freshness_config_rejected(bad):
    with pytest.raises(FusionContractError, match="vision_max_age_s"):
        FusionFreshnessConfig(vision_max_age_s=bad)


# ---- H. immutability --------------------------------------------------------------------

def test_envelope_and_readings_are_immutable():
    e = env()
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.origin = Origin.REAL
    for r, field, val in ((LuxReading(e, 1.0), "value", 2.0), (PresenceReading(e, True), "present", False),
                          (TemperatureReading(e, "t", 20.0), "value", 1.0), (HumidityReading(e, 50.0), "value", 1.0),
                          (VisionObservation(e, occupancy(1)), "occupancy", None)):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(r, field, val)
    s = derive_source_state(LuxReading(e, 1.0), 100.0, 1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.usability = Usability.STALE
    with pytest.raises(dataclasses.FrozenInstanceError):
        FusionFreshnessConfig().lux_max_age_s = 1.0


def test_week5_result_is_not_copied_or_mutated():
    occ = occupancy(3)
    before = dataclasses.astuple(occ)
    obs = VisionObservation(env(), occ)
    state = derive_source_state(obs, 101.0, 5.0)
    assert fresh_value(obs, state) is occ and obs.occupancy is occ
    assert dataclasses.astuple(occ) == before


# ---- provenance and structure -----------------------------------------------------------

def test_simulated_origin_is_preserved_through_derivation():
    for r in (lux(), PresenceReading(env(), True), HumidityReading(env(), 40.0)):
        assert derive_source_state(r, 100.0, 5.0).origin is Origin.SIMULATED


def test_origin_is_preserved_exactly_for_every_value():
    for o in Origin:
        assert derive_source_state(LuxReading(env(origin=o), 1.0), 100.0, 5.0).origin is o


def test_contracts_contain_no_protocol_or_fusion_decisions():
    import fusion.contracts as c
    assert not any(hasattr(c, n) for n in ("fuse", "EvidenceState", "ClassroomState", "serial", "pyserial"))
    assert SourceState.__dataclass_fields__.keys() >= {"usability", "origin", "source_status", "age_s", "reason"}
    assert math.isfinite(c.ABSOLUTE_ZERO_C)


def test_unavailable_or_stale_vision_yields_no_value_not_zero():
    for obs, now in ((VisionObservation(bad_env(SourceStatus.UNAVAILABLE)), 100.0), (VisionObservation(env(t=100.0), occupancy(2)), 200.0)):
        got = fresh_value(obs, derive_source_state(obs, now, 5.0))
        assert got is None and got != 0


# ---- canonical identifiers (post-closeout review fix) -----------------------------------

GOOD_IDS = ["camera_1", "radar-1", "temp1", "A", "esp-sim", "T_9"]
BAD_IDS = ["temp room", " radar", "radar ", "temp\troom", "a\nb", "radar\n", "", "   ", "a.b", "t[1]", "a=b", "a,b", "é"]


@pytest.mark.parametrize("sid", GOOD_IDS)
def test_canonical_ids_accepted_everywhere(sid):
    assert env(sid).source_id == sid
    assert TemperatureReading(env(), sid, 20.0).sensor_id == sid
    assert HumidityReading(env(), 40.0, sensor_id=sid).sensor_id == sid
    assert derive_source_state(None, 1.0, 1.0, sid).source_id == sid


@pytest.mark.parametrize("sid", BAD_IDS)
def test_non_canonical_ids_rejected_naming_the_field(sid):
    with pytest.raises(FusionContractError, match="source_id"):
        SourceEnvelope(sid, Origin.SIMULATED, 1.0)
    with pytest.raises(FusionContractError, match="sensor_id"):
        TemperatureReading(env(), sid, 20.0)
    with pytest.raises(FusionContractError, match="sensor_id"):
        HumidityReading(env(), 40.0, sensor_id=sid)
    with pytest.raises(FusionContractError, match="source_id"):
        derive_source_state(None, 1.0, 1.0, sid)


def test_humidity_sensor_id_may_still_be_absent():
    assert HumidityReading(env(), 40.0).sensor_id is None


def test_id_rule_does_not_apply_to_display_text_fields():
    e = SourceEnvelope("radar-1", Origin.SIMULATED, 1.0, SourceStatus.FAILED, "bus error: radar not responding", "tick 12:00")
    assert e.error_reason == "bus error: radar not responding" and e.device_timestamp == "tick 12:00"
