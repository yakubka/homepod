"""Week 6, Task 1: AI-side input contracts and source-state semantics for sensor fusion.

AI-SIDE, PROTOCOL-INDEPENDENT. Nothing here describes the ESP32 wire format, a serial
frame, a driver or a pin. The hardware / software teams own the real protocol; a future
Pi hardware adapter maps it into these structures:

    hardware / software team: real protocol
        -> Pi hardware adapter
        -> these contracts
        -> fusion logic (Week 6 Task 2, not implemented here)

Principle: a missing, failed, stale or unconfigured source must never turn into a plausible
number. Missing is not 0 people, absent presence is not False, no light reading is not 0 lux.

Two separate ideas are kept apart on purpose:
  * SourceStatus  - what the acquisition path says (OK / UNAVAILABLE / FAILED), set by the adapter;
  * Usability     - what the software derives about a reading (FRESH / STALE / INVALID /
                    MISSING / UNCONFIGURED) from the reading, "now" and a configured max age.

Clock rule: freshness uses ONLY `received_at_monotonic_s` (the Pi / host monotonic clock at the
moment the reading entered the AI-side adapter) and an explicitly supplied `now_monotonic_s`.
`device_timestamp` is optional metadata in an unknown device clock domain and is never used for
freshness. Wall-clock time is not used at all.

Freshness boundary: age <= max_age is FRESH, age > max_age is STALE.

No fusion decision (rules R1-R8), evidence state or ClassroomState lives here: those are Task 2.
"""

import math
import re
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Optional, Union

from vision.zones import ZoneOccupancyResult

ABSOLUTE_ZERO_C = -273.15      # universal physical limit, not a classroom range


class FusionContractError(ValueError):
    """A fusion input violates its contract; the message names the source, field and value."""


class Origin(Enum):
    REAL = "REAL"              # measured by physical hardware (only an adapter may say so)
    SIMULATED = "SIMULATED"    # fixture / simulator; every synthetic test uses this
    UNKNOWN = "UNKNOWN"        # provenance not established


class SourceStatus(Enum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class Usability(Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    INVALID = "INVALID"            # source reported UNAVAILABLE or FAILED (status is retained)
    MISSING = "MISSING"            # no reading was received at all
    UNCONFIGURED = "UNCONFIGURED"  # no max age configured: freshness cannot be judged


class Unit(Enum):
    LUX = "lx"
    CELSIUS = "degC"
    PERCENT_RH = "%RH"


# ---------------------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------------------

def _is_number(v) -> bool:
    return isinstance(v, Real) and not isinstance(v, bool)


def _finite(who: str, field: str, v) -> None:
    if not _is_number(v) or not math.isfinite(v):
        raise FusionContractError(f"{who}: {field} must be a finite number, got {v!r}")


def _nonempty(who: str, field: str, v) -> None:
    if not isinstance(v, str) or not v.strip():
        raise FusionContractError(f"{who}: {field} must be a non-empty string, got {v!r}")


# Canonical AI-side identifier: letters, digits, underscore, hyphen. Identifiers appear as tokens in the
# space-delimited key=value telemetry, so whitespace and other separators are rejected at the contract boundary.
# Raw hardware labels are NOT identifiers; a future Pi adapter maps them to these canonical ids.
_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def _check_id(who: str, field: str, v) -> None:
    if not isinstance(v, str) or _ID_RE.fullmatch(v) is None:
        raise FusionContractError(f"{who}: {field} must be a canonical id matching [A-Za-z0-9_-]+ (no spaces or other characters), got {v!r}")


# ---------------------------------------------------------------------------------------
# common envelope
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceEnvelope:
    """Metadata every reading carries."""

    source_id: str
    origin: Origin
    received_at_monotonic_s: float
    source_status: SourceStatus = SourceStatus.OK
    error_reason: Optional[str] = None
    device_timestamp: Optional[Union[int, float, str]] = None   # opaque, device clock domain; NOT used for freshness

    def __post_init__(self) -> None:
        _check_id("envelope", "source_id", self.source_id)
        who = f"source {self.source_id!r}"
        if not isinstance(self.origin, Origin):
            raise FusionContractError(f"{who}: origin must be an Origin (REAL / SIMULATED / UNKNOWN), got {self.origin!r}")
        if not isinstance(self.source_status, SourceStatus):
            raise FusionContractError(f"{who}: source_status must be a SourceStatus, got {self.source_status!r}")
        _finite(who, "received_at_monotonic_s", self.received_at_monotonic_s)
        if self.error_reason is not None:
            _nonempty(who, "error_reason", self.error_reason)
        if self.source_status is SourceStatus.FAILED and self.error_reason is None:
            raise FusionContractError(f"{who}: a FAILED source must carry an error_reason")
        if self.source_status is SourceStatus.OK and self.error_reason is not None:
            raise FusionContractError(f"{who}: an OK source must not carry an error_reason ({self.error_reason!r})")
        dt = self.device_timestamp
        if dt is not None:
            if isinstance(dt, str):
                _nonempty(who, "device_timestamp", dt)
            else:
                _finite(who, "device_timestamp", dt)

    @property
    def ok(self) -> bool:
        return self.source_status is SourceStatus.OK


def _check_envelope(who: str, env) -> None:
    if not isinstance(env, SourceEnvelope):
        raise FusionContractError(f"{who}: envelope must be a SourceEnvelope, got {type(env).__name__}")


def _check_value_state(env: SourceEnvelope, field: str, value) -> None:
    """OK needs a value; UNAVAILABLE / FAILED must carry none (never a numeric stand-in)."""
    if env.ok and value is None:
        raise FusionContractError(f"source {env.source_id!r}: {field} is required when source_status is OK")
    if not env.ok and value is not None:
        raise FusionContractError(
            f"source {env.source_id!r}: {field} must be None when source_status is {env.source_status.value}, got {value!r}")


def _check_unit(env: SourceEnvelope, unit, expected: Unit) -> None:
    if unit is not expected:
        raise FusionContractError(
            f"source {env.source_id!r}: unit must be {expected.value!r} ({expected.name}), got {unit!r}; no unit conversion is done")


# ---------------------------------------------------------------------------------------
# readings
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class VisionObservation:
    """Camera pipeline output. `occupancy` is the Week 5 ZoneOccupancyResult, used as-is (not copied).

    Its `occupancy_count` keeps the Week 5 meaning: detected persons inside the classroom ROI.
    A camera that is unavailable has occupancy None; it is NOT a count of zero.
    """

    envelope: SourceEnvelope
    occupancy: Optional[ZoneOccupancyResult] = None

    def __post_init__(self) -> None:
        _check_envelope("VisionObservation", self.envelope)
        _check_value_state(self.envelope, "occupancy", self.occupancy)
        if self.occupancy is not None and not isinstance(self.occupancy, ZoneOccupancyResult):
            raise FusionContractError(
                f"source {self.envelope.source_id!r}: occupancy must be a ZoneOccupancyResult, got {type(self.occupancy).__name__}")


@dataclass(frozen=True)
class PresenceReading:
    """Whole-room presence (radar). present False = a valid reading of 'nobody'; None = no usable value."""

    envelope: SourceEnvelope
    present: Optional[bool] = None

    def __post_init__(self) -> None:
        _check_envelope("PresenceReading", self.envelope)
        _check_value_state(self.envelope, "present", self.present)
        if self.present is not None and not isinstance(self.present, bool):
            raise FusionContractError(f"source {self.envelope.source_id!r}: present must be a bool, got {self.present!r}")


@dataclass(frozen=True)
class LuxReading:
    """Whole-room ambient light in lux. 0 lx is a legitimate value."""

    envelope: SourceEnvelope
    value: Optional[float] = None
    unit: Unit = Unit.LUX

    def __post_init__(self) -> None:
        _check_envelope("LuxReading", self.envelope)
        _check_unit(self.envelope, self.unit, Unit.LUX)
        _check_value_state(self.envelope, "value", self.value)
        if self.value is not None:
            _finite(f"source {self.envelope.source_id!r}", "lux value", self.value)
            if self.value < 0:
                raise FusionContractError(f"source {self.envelope.source_id!r}: lux value must be >= 0, got {self.value!r}")


@dataclass(frozen=True)
class TemperatureReading:
    """One temperature sensor, degrees Celsius. Several sensors are distinguished by sensor_id."""

    envelope: SourceEnvelope
    sensor_id: str
    value: Optional[float] = None
    unit: Unit = Unit.CELSIUS

    def __post_init__(self) -> None:
        _check_envelope("TemperatureReading", self.envelope)
        _check_id(f"source {self.envelope.source_id!r}", "sensor_id", self.sensor_id)
        _check_unit(self.envelope, self.unit, Unit.CELSIUS)
        _check_value_state(self.envelope, "value", self.value)
        if self.value is not None:
            _finite(f"sensor {self.sensor_id!r}", "temperature value", self.value)
            if self.value < ABSOLUTE_ZERO_C:
                raise FusionContractError(
                    f"sensor {self.sensor_id!r}: temperature {self.value!r} degC is below absolute zero ({ABSOLUTE_ZERO_C})")


@dataclass(frozen=True)
class HumidityReading:
    """Relative humidity in %RH. The humidity hardware part is UNCONFIRMED: the roadmap names the input
    but CLAUDE.md names no device, so nothing about a concrete sensor is assumed here."""

    envelope: SourceEnvelope
    value: Optional[float] = None
    unit: Unit = Unit.PERCENT_RH
    sensor_id: Optional[str] = None

    def __post_init__(self) -> None:
        _check_envelope("HumidityReading", self.envelope)
        if self.sensor_id is not None:
            _check_id(f"source {self.envelope.source_id!r}", "sensor_id", self.sensor_id)
        _check_unit(self.envelope, self.unit, Unit.PERCENT_RH)
        _check_value_state(self.envelope, "value", self.value)
        if self.value is not None:
            _finite(f"source {self.envelope.source_id!r}", "humidity value", self.value)
            if not 0 <= self.value <= 100:
                raise FusionContractError(f"source {self.envelope.source_id!r}: humidity must be within 0..100 %RH, got {self.value!r}")


Reading = Union[VisionObservation, PresenceReading, LuxReading, TemperatureReading, HumidityReading]
_READING_TYPES = (VisionObservation, PresenceReading, LuxReading, TemperatureReading, HumidityReading)


# ---------------------------------------------------------------------------------------
# freshness configuration
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FusionFreshnessConfig:
    """Maximum ages in seconds per source, and the allowed skew between two sources (used from Task 2).

    None means NOT CALIBRATED: no number is invented. A source with no max age is UNCONFIGURED.
    """

    vision_max_age_s: Optional[float] = None
    presence_max_age_s: Optional[float] = None
    lux_max_age_s: Optional[float] = None
    temperature_max_age_s: Optional[float] = None
    humidity_max_age_s: Optional[float] = None
    max_source_skew_s: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("vision_max_age_s", "presence_max_age_s", "lux_max_age_s",
                     "temperature_max_age_s", "humidity_max_age_s", "max_source_skew_s"):
            v = getattr(self, name)
            if v is None:
                continue
            _finite("FusionFreshnessConfig", name, v)
            if v < 0:
                raise FusionContractError(f"FusionFreshnessConfig: {name} must be >= 0, got {v!r}")


def freshness_from_settings() -> FusionFreshnessConfig:
    """Read the FUSION_* keys from config/settings.py (all None = uncalibrated by default)."""
    from config import settings

    return FusionFreshnessConfig(
        vision_max_age_s=settings.FUSION_VISION_MAX_AGE_S,
        presence_max_age_s=settings.FUSION_PRESENCE_MAX_AGE_S,
        lux_max_age_s=settings.FUSION_LUX_MAX_AGE_S,
        temperature_max_age_s=settings.FUSION_TEMPERATURE_MAX_AGE_S,
        humidity_max_age_s=settings.FUSION_HUMIDITY_MAX_AGE_S,
        max_source_skew_s=settings.FUSION_MAX_SOURCE_SKEW_S,
    )


# ---------------------------------------------------------------------------------------
# derived source state (NOT fusion)
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceState:
    """What the software knows about one source at `now`. Diagnostic: stale or failed readings stay visible."""

    usability: Usability
    source_id: Optional[str]                 # None only when MISSING and no expected id was given
    origin: Optional[Origin]                 # None when MISSING (no reading, so no provenance)
    source_status: Optional[SourceStatus]    # None when MISSING
    age_s: Optional[float]                   # None when MISSING
    max_age_s: Optional[float]
    reason: str


def derive_source_state(reading: Optional[Reading], now_monotonic_s: float, max_age_s: Optional[float],
                        source_id: Optional[str] = None) -> SourceState:
    """Classify one reading as MISSING / INVALID / UNCONFIGURED / FRESH / STALE. Pure; no fusion.

    Order: no reading -> MISSING; UNAVAILABLE or FAILED source -> INVALID (status kept);
    no configured max age -> UNCONFIGURED; age <= max age -> FRESH; otherwise STALE.
    age = now_monotonic_s - received_at_monotonic_s (host monotonic clock only; device_timestamp is ignored).
    A `now` earlier than the receive time is impossible and raises.
    """
    _finite("derive_source_state", "now_monotonic_s", now_monotonic_s)
    if source_id is not None:
        _check_id("derive_source_state", "source_id", source_id)
    if max_age_s is not None:
        _finite("derive_source_state", "max_age_s", max_age_s)
        if max_age_s < 0:
            raise FusionContractError(f"derive_source_state: max_age_s must be >= 0, got {max_age_s!r}")
    if reading is None:
        return SourceState(Usability.MISSING, source_id, None, None, None, max_age_s, "no reading received")
    if not isinstance(reading, _READING_TYPES):
        raise FusionContractError(f"derive_source_state: unsupported reading type {type(reading).__name__}")
    env = reading.envelope
    age = now_monotonic_s - env.received_at_monotonic_s
    if age < 0:
        raise FusionContractError(
            f"source {env.source_id!r}: now_monotonic_s {now_monotonic_s!r} is earlier than received_at_monotonic_s "
            f"{env.received_at_monotonic_s!r}")
    if not env.ok:
        return SourceState(Usability.INVALID, env.source_id, env.origin, env.source_status, age, max_age_s,
                           env.error_reason or f"source {env.source_status.value}")
    if max_age_s is None:
        return SourceState(Usability.UNCONFIGURED, env.source_id, env.origin, env.source_status, age, None,
                           "max age not configured (calibration pending)")
    if age <= max_age_s:
        return SourceState(Usability.FRESH, env.source_id, env.origin, env.source_status, age, max_age_s, "within max age")
    return SourceState(Usability.STALE, env.source_id, env.origin, env.source_status, age, max_age_s,
                       f"age {age} s exceeds max age {max_age_s} s")


def fresh_value(reading: Optional[Reading], state: SourceState):
    """The reading's value only when its state is FRESH, otherwise None. Never a numeric stand-in.

    Returns the ZoneOccupancyResult (vision), the bool (presence) or the number (lux / temperature / humidity).
    """
    if reading is None or state.usability is not Usability.FRESH:
        return None
    if isinstance(reading, VisionObservation):
        return reading.occupancy
    if isinstance(reading, PresenceReading):
        return reading.present
    return reading.value
