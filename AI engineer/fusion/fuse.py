"""Week 6, Task 2: deterministic occupancy fusion and the unified ClassroomState.

PURE: no clock, no logging, no global or hardware state, no mutation of inputs. Identical inputs,
`now_monotonic_s` and freshness configuration always give an identical, immutable ClassroomState.
Freshness comes from fusion.contracts.derive_source_state (not re-implemented here).

What this module does NOT do: correct the vision count, turn radar presence into a count, invent
zone values, average temperatures, interpret lux / temperature / humidity, or produce actuator
commands. It combines two OCCUPANCY sources (vision and radar presence) and passes the environment
values through with their own usability.

Vocabulary
  vision_count    Week 5 `occupancy_count`: detected persons inside the classroom ROI, per frame.
                  Not a student count, not unique people, not corrected ground truth. Radar never changes it.
  fused_presence  PRESENT / ABSENT / UNKNOWN. A vision count of 0 alone is UNKNOWN, not ABSENT, because the
                  detector is known to miss people (Week 4 frozen baseline).
  CONFIRMED       the two currently usable occupancy sources agree. It does not mean ground-truth verified.
  CONFIRMED_EMPTY both sources agree on "nobody" at this instant; not independent ground truth.

Decision table (vision V, radar presence P; "usable" = FRESH and not excluded by skew)
  R1  V usable count > 0   P usable True    CONFIRMED        PRESENT   not degraded
  R2  V usable count == 0  P usable False   CONFIRMED_EMPTY  ABSENT    not degraded
  R3  V usable count == 0  P usable True    CONFLICT         UNKNOWN   degraded  PRESENCE_WITHOUT_DETECTIONS
  R4  V usable count > 0   P usable False   CONFLICT         UNKNOWN   degraded  DETECTIONS_WITHOUT_PRESENCE
  R5  V usable, P not      VISION_ONLY      PRESENT if count > 0 else UNKNOWN      degraded
  R6  P usable, V not      SENSOR_ONLY      PRESENT / ABSENT from radar, count None degraded
  R7  neither usable       UNKNOWN          UNKNOWN                               degraded
  R8  V and P both FRESH but |received_at difference| > max skew (or max skew unconfigured):
      the two are not cross-checked. Only the OLDER one is excluded from occupancy corroboration (it stays
      FRESH and fully visible in the state); the newer one is handled by R5 (vision newer) or R6 (radar
      newer). rule_id is "R8" and fallback_rule_id says which single-source rule was applied.
      Skew boundary: skew <= max skew allows corroboration; skew > max skew triggers R8.
      Equal timestamps give skew 0, so with a CONFIGURED max skew they never trigger R8 (normal R1-R4).
      Unconfigured max skew never allows corroboration (reason SKEW_UNCONFIGURED). Only then, on exactly equal
      timestamps, there is no newer source: vision gets FALLBACK PRECEDENCE (it carries the count and the
      Week 5 zone data) and radar is excluded. That is precedence, not "vision is newer".

Integration preconditions and known limitations (carried forward; real values PENDING HARDWARE)
  * Snapshot order: a future Pi adapter / runtime must first take an immutable snapshot of the readings and THEN
    sample now_monotonic_s, never the other way round and never accepting a newer reading into the same evaluation.
    A reading received after `now` raises FusionContractError; it is deliberately not clamped, ignored or marked stale.
  * Uncalibrated skew (KNOWN UNCALIBRATED-INTEGRATION LIMITATION): while FUSION_MAX_SOURCE_SKEW_S is None, two fresh but
    unequal-time sources are never corroborated and the older one is excluded. If radar is the newer one, the fused
    `vision_count` is None although `raw_vision_count` still holds the vision value, so the fused count can alternate
    between a number and None from one evaluation to the next. None means "not usable for fusion", NEVER "zero people".
    The skew must be calibrated with the real transport before live decisions rely on corroboration.
  * Expected temperature sensors: an empty `temperatures` collection means none were supplied. Telling "none configured" from
    "expected sensors failed to report" needs the real sensor ids from the hardware team (PENDING REAL HARDWARE CONTRACT).

Skew applies ONLY to vision vs radar. Lux, temperature and humidity are independent environment values,
each judged by its own freshness only, and they never take part in the occupancy rules.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple

from fusion.contracts import (
    FusionContractError, FusionFreshnessConfig, HumidityReading, LuxReading, Origin, PresenceReading, SourceState,
    TemperatureReading, Unit, Usability, VisionObservation, derive_source_state, fresh_value,
)


class EvidenceState(Enum):
    CONFIRMED = "CONFIRMED"
    CONFIRMED_EMPTY = "CONFIRMED_EMPTY"
    VISION_ONLY = "VISION_ONLY"
    SENSOR_ONLY = "SENSOR_ONLY"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class FusedPresence(Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class FallbackBasis(Enum):
    """Why R8 chose its single source. Decided by fuse_classroom_state, recorded in ClassroomState, only serialised by telemetry."""

    NEWER_SOURCE = "NEWER_SOURCE"                  # the excluded source had the older receive time
    FALLBACK_PRECEDENCE = "FALLBACK_PRECEDENCE"    # exact timestamp tie under unconfigured skew: vision kept by precedence, NOT because newer


class DegradedReason(Enum):
    """Stable reason codes. The declaration order is the deterministic output order."""

    VISION_MISSING = "VISION_MISSING"
    VISION_STALE = "VISION_STALE"
    VISION_INVALID = "VISION_INVALID"
    VISION_UNCONFIGURED = "VISION_UNCONFIGURED"
    PRESENCE_MISSING = "PRESENCE_MISSING"
    PRESENCE_STALE = "PRESENCE_STALE"
    PRESENCE_INVALID = "PRESENCE_INVALID"
    PRESENCE_UNCONFIGURED = "PRESENCE_UNCONFIGURED"
    SOURCE_SKEW = "SOURCE_SKEW"
    SKEW_UNCONFIGURED = "SKEW_UNCONFIGURED"
    PRESENCE_WITHOUT_DETECTIONS = "PRESENCE_WITHOUT_DETECTIONS"
    DETECTIONS_WITHOUT_PRESENCE = "DETECTIONS_WITHOUT_PRESENCE"


@dataclass(frozen=True)
class EnvironmentValue:
    """One environment value passed through with its own usability. `value` is None unless the state is FRESH.

    No interpretation (dark / hot / comfortable) is applied here.
    """

    name: str                       # "lux", "temperature" or "humidity"
    sensor_id: Optional[str]
    value: Optional[float]
    unit: Unit
    state: SourceState


@dataclass(frozen=True)
class EnvironmentState:
    lux: EnvironmentValue
    temperatures: Tuple[EnvironmentValue, ...]    # one entry per supplied sensor, input order; never averaged
    humidity: EnvironmentValue
    issues: Tuple[str, ...]                       # codes such as LUX_STALE; informational, not part of `degraded`


@dataclass(frozen=True)
class OccupancyState:
    evidence: EvidenceState
    fused_presence: FusedPresence
    vision_count: Optional[int]                   # Week 5 occupancy_count; None unless vision participated
    zone_counts: Optional[Tuple[Tuple[str, int], ...]]   # Week 5 values; None if vision did not participate or zones are not configured
    unassigned_inside_roi: Optional[int]          # None unless vision participated and zones are configured
    outside_classroom_roi: Optional[int]
    zones_configured: Optional[bool]              # None when vision did not participate
    vision_used: bool                             # vision took part in occupancy fusion
    presence_used: bool                           # radar took part in occupancy fusion
    sensor_present: Optional[bool]                # radar value when its reading is FRESH (even if excluded by skew)
    raw_vision_count: Optional[int]               # diagnostic: from any OK vision reading, even stale or skew-excluded
    raw_presence: Optional[bool]                  # diagnostic: from any OK presence reading


@dataclass(frozen=True)
class ClassroomState:
    """Unified classroom state for one evaluation instant.

    `degraded` / `degraded_reasons` describe OCCUPANCY-EVIDENCE quality only (R1 and R2 are the only
    non-degraded outcomes). They are NOT an overall sensor-health flag: lux, temperature and humidity health is
    reported separately in `environment.issues` and in each EnvironmentValue's SourceState.
    """

    evaluated_at_monotonic_s: float
    rule_id: str
    fallback_rule_id: Optional[str]               # set only for R8
    fallback_basis: Optional[FallbackBasis]       # set only for R8 (None for R1-R7)
    occupancy: OccupancyState
    vision_state: SourceState
    presence_state: SourceState
    environment: EnvironmentState
    degraded: bool                                # occupancy fusion degraded (environment issues are separate)
    degraded_reasons: Tuple[DegradedReason, ...]
    aggregate_origin: Origin                      # conservative; per-source origins stay in the SourceStates


def _env_value(name, sensor_id, reading, state, unit) -> EnvironmentValue:
    return EnvironmentValue(name, sensor_id, fresh_value(reading, state), unit, state)


def _aggregate_origin(origins) -> Origin:
    """Contributing sources = vision if used, radar if used, and every FRESH environment value.
    None contributing -> UNKNOWN; any UNKNOWN -> UNKNOWN; else any SIMULATED -> SIMULATED; else REAL."""
    if not origins:
        return Origin.UNKNOWN
    if Origin.UNKNOWN in origins:
        return Origin.UNKNOWN
    if Origin.SIMULATED in origins:
        return Origin.SIMULATED
    return Origin.REAL


def _expect(name, value, kind, optional=True):
    if value is None and optional:
        return
    if not isinstance(value, kind):
        raise FusionContractError(f"fuse_classroom_state: {name} must be {kind.__name__}{' or None' if optional else ''}, got {type(value).__name__}")


def _temperature_tuple(temperatures) -> Tuple[TemperatureReading, ...]:
    """Validate the `temperatures` argument: an iterable (not a string) of TemperatureReading. Empty is valid."""
    if temperatures is None or isinstance(temperatures, (str, bytes)):
        raise FusionContractError(
            f"fuse_classroom_state: temperatures must be an iterable of TemperatureReading (an empty one is allowed), got {temperatures!r}")
    try:
        iterator = iter(temperatures)
    except TypeError:
        raise FusionContractError(
            f"fuse_classroom_state: temperatures must be an iterable of TemperatureReading, got {type(temperatures).__name__}") from None
    temps = tuple(iterator)                  # errors raised by a valid iterable itself are not swallowed
    for i, t in enumerate(temps):
        if not isinstance(t, TemperatureReading):
            raise FusionContractError(f"fuse_classroom_state: temperatures[{i}] must be TemperatureReading, got {type(t).__name__}")
    return temps


def fuse_classroom_state(
    vision: Optional[VisionObservation],
    presence: Optional[PresenceReading],
    lux: Optional[LuxReading],
    temperatures: Iterable[TemperatureReading],
    humidity: Optional[HumidityReading],
    now_monotonic_s: float,
    freshness: FusionFreshnessConfig,
) -> ClassroomState:
    """Fuse one evaluation instant. Pure and deterministic; see the module docstring for the rules."""
    _expect("vision", vision, VisionObservation)
    _expect("presence", presence, PresenceReading)
    _expect("lux", lux, LuxReading)
    _expect("humidity", humidity, HumidityReading)
    _expect("freshness", freshness, FusionFreshnessConfig, optional=False)
    temps = _temperature_tuple(temperatures)
    ids = [t.sensor_id for t in temps]
    if len(set(ids)) != len(ids):
        raise FusionContractError(f"fuse_classroom_state: duplicate temperature sensor_id in {ids!r}")

    vs = derive_source_state(vision, now_monotonic_s, freshness.vision_max_age_s, "vision")
    ps = derive_source_state(presence, now_monotonic_s, freshness.presence_max_age_s, "presence")
    v_fresh = vs.usability is Usability.FRESH
    p_fresh = ps.usability is Usability.FRESH
    use_v, use_p = v_fresh, p_fresh

    skew_reason = None
    basis = None
    if v_fresh and p_fresh:
        v_t = vision.envelope.received_at_monotonic_s
        p_t = presence.envelope.received_at_monotonic_s
        skew = abs(v_t - p_t)
        max_skew = freshness.max_source_skew_s
        if max_skew is None:
            skew_reason = DegradedReason.SKEW_UNCONFIGURED
        elif skew > max_skew:
            skew_reason = DegradedReason.SOURCE_SKEW
        if skew_reason is not None:          # R8: exclude only the OLDER source from corroboration
            if p_t > v_t:
                use_v = False                # radar newer: vision excluded
                basis = FallbackBasis.NEWER_SOURCE
            elif v_t > p_t:
                use_p = False                # vision newer: radar excluded
                basis = FallbackBasis.NEWER_SOURCE
            else:
                use_p = False                # exact tie (only possible with unconfigured skew): vision by fallback precedence
                basis = FallbackBasis.FALLBACK_PRECEDENCE

    count = vision.occupancy.occupancy_count if use_v else None
    present = presence.present if use_p else None

    if use_v and use_p:
        if count > 0 and present:
            rule, evidence, fused = "R1", EvidenceState.CONFIRMED, FusedPresence.PRESENT
        elif count == 0 and not present:
            rule, evidence, fused = "R2", EvidenceState.CONFIRMED_EMPTY, FusedPresence.ABSENT
        elif count == 0 and present:
            rule, evidence, fused = "R3", EvidenceState.CONFLICT, FusedPresence.UNKNOWN
        else:
            rule, evidence, fused = "R4", EvidenceState.CONFLICT, FusedPresence.UNKNOWN
    elif use_v:
        rule, evidence = "R5", EvidenceState.VISION_ONLY
        fused = FusedPresence.PRESENT if count > 0 else FusedPresence.UNKNOWN
    elif use_p:
        rule, evidence = "R6", EvidenceState.SENSOR_ONLY
        fused = FusedPresence.PRESENT if present else FusedPresence.ABSENT
    else:
        rule, evidence, fused = "R7", EvidenceState.UNKNOWN, FusedPresence.UNKNOWN

    fallback = None
    if skew_reason is not None:
        fallback, rule = rule, "R8"

    reasons = []
    if not v_fresh:
        reasons.append(DegradedReason["VISION_" + vs.usability.name])
    if not p_fresh:
        reasons.append(DegradedReason["PRESENCE_" + ps.usability.name])
    if skew_reason is not None:
        reasons.append(skew_reason)
    if evidence is EvidenceState.CONFLICT:
        reasons.append(DegradedReason.PRESENCE_WITHOUT_DETECTIONS if rule == "R3" else DegradedReason.DETECTIONS_WITHOUT_PRESENCE)
    degraded = evidence not in (EvidenceState.CONFIRMED, EvidenceState.CONFIRMED_EMPTY)

    # Week 5 data is copied from a usable vision result only; radar never contributes to any of it.
    vision_count = None
    zone_counts = None
    unassigned = None
    outside = None
    zones_configured = None
    if use_v:
        result = vision.occupancy
        vision_count = result.occupancy_count
        zones_configured = result.zones_configured
        outside = result.outside_classroom_roi
        if result.zones_configured:
            zone_counts = result.zone_counts
            unassigned = result.unassigned_inside_roi

    lux_ev = _env_value("lux", None, lux, derive_source_state(lux, now_monotonic_s, freshness.lux_max_age_s, "lux"), Unit.LUX)
    hum_ev = _env_value("humidity", humidity.sensor_id if humidity else None, humidity,
                        derive_source_state(humidity, now_monotonic_s, freshness.humidity_max_age_s, "humidity"), Unit.PERCENT_RH)
    temp_evs = tuple(
        _env_value("temperature", t.sensor_id, t, derive_source_state(t, now_monotonic_s, freshness.temperature_max_age_s, t.sensor_id),
                   Unit.CELSIUS)
        for t in temps)
    issues = []
    for ev in (lux_ev,) + temp_evs + (hum_ev,):
        if ev.state.usability is not Usability.FRESH:
            label = ev.name.upper() if ev.name != "temperature" else f"TEMPERATURE[{ev.sensor_id}]"
            issues.append(f"{label}_{ev.state.usability.name}")

    origins = set()
    if use_v:
        origins.add(vs.origin)
    if use_p:
        origins.add(ps.origin)
    for ev in (lux_ev,) + temp_evs + (hum_ev,):
        if ev.state.usability is Usability.FRESH:
            origins.add(ev.state.origin)

    return ClassroomState(
        evaluated_at_monotonic_s=now_monotonic_s,
        rule_id=rule,
        fallback_rule_id=fallback,
        fallback_basis=basis,
        occupancy=OccupancyState(
            evidence=evidence,
            fused_presence=fused,
            vision_count=vision_count,
            zone_counts=zone_counts,
            unassigned_inside_roi=unassigned,
            outside_classroom_roi=outside,
            zones_configured=zones_configured,
            vision_used=use_v,
            presence_used=use_p,
            sensor_present=fresh_value(presence, ps),
            raw_vision_count=vision.occupancy.occupancy_count if vision is not None and vision.occupancy is not None else None,
            raw_presence=presence.present if presence is not None else None,
        ),
        vision_state=vs,
        presence_state=ps,
        environment=EnvironmentState(lux=lux_ev, temperatures=temp_evs, humidity=hum_ev, issues=tuple(issues)),
        degraded=degraded,
        degraded_reasons=tuple(reasons),
        aggregate_origin=_aggregate_origin(origins),
    )
