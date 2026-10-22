"""Midterm readiness: sensor-message boundary and the ONE live ClassroomState store. PROVISIONAL - NOT TEAM-APPROVED.

    hardware-team adapter (real protocol, NOT IN THIS REPOSITORY)
        -> decoded message (a Mapping, UNTRUSTED)  -> decode_sensor_message()  -> a fusion.contracts Reading
        -> ClassroomStateStore.ingest()            -> latest reading per source
        -> ClassroomStateStore.snapshot()          -> fusion.fuse.fuse_classroom_state()  -> the ClassroomState that voice AND app read

The DECODED-MESSAGE shape below is an AI-side proposal (like the Week 9 reply shape), NOT a wire format: no framing, no baud
rate, no packet. Whatever the ESP32 firmware really sends, the Pi adapter that reads it maps it into this shape. Keys:

    kind          "TEMPERATURE" | "HUMIDITY" | "LUX" | "PRESENCE"
    source_id     canonical id, [A-Za-z0-9_-]+
    status        "OK" | "UNAVAILABLE" | "FAILED"
    value         a number (TEMPERATURE / HUMIDITY / LUX) or a bool (PRESENCE); REQUIRED when OK, ABSENT / None otherwise
    unit          "degC" | "%RH" | "lx"; REQUIRED when OK for the numeric kinds and must match the kind exactly (no conversion)
    sensor_id     optional (TEMPERATURE defaults to source_id)
    error_reason  required when FAILED
    device_timestamp  optional, opaque, NEVER used for freshness

Anything else (an unknown key, a wrong type, a missing unit, a bool where a number belongs, a value outside the contract range)
is REJECTED with a reason code; it is never repaired, defaulted or clamped. Provenance (Origin) comes from the SOURCE that
received the message, never from the message: a message cannot declare itself REAL. Receive time is the host monotonic clock.

No real source exists here. SensorSource is the seam the Pi adapter implements; FixtureSensorSource is SIMULATED by construction.
"""

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from fusion.contracts import (
    FusionContractError, FusionFreshnessConfig, HumidityReading, LuxReading, Origin, PresenceReading, SourceEnvelope, SourceStatus,
    TemperatureReading, Unit, VisionObservation, _ID_RE as _SENSOR_ID,
)
from fusion.fuse import ClassroomState, fuse_classroom_state

MESSAGE_STATUS = "PROVISIONAL DECODED-MESSAGE SHAPE - NOT A WIRE PROTOCOL - NOT TEAM-APPROVED"
_KEYS = frozenset({"kind", "source_id", "status", "value", "unit", "sensor_id", "error_reason", "device_timestamp"})
_NUMERIC = {"TEMPERATURE": (TemperatureReading, Unit.CELSIUS), "HUMIDITY": (HumidityReading, Unit.PERCENT_RH), "LUX": (LuxReading, Unit.LUX)}
_SLOTS = ((VisionObservation, "vision"), (PresenceReading, "presence"), (LuxReading, "lux"), (HumidityReading, "humidity"))


class SensorMessageError(ValueError):
    """A decoded sensor message was rejected. `reason` is an UPPER_CASE code safe to log."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def decode_sensor_message(raw: Any, *, origin: Origin, received_at_monotonic_s: float):
    """UNTRUSTED Mapping -> a validated fusion Reading. Raises SensorMessageError; never returns a stand-in value."""
    if not isinstance(origin, Origin):
        raise TypeError("origin must be an Origin (set by the source, never by the message)")
    if not isinstance(raw, Mapping):
        raise SensorMessageError("NOT_A_MAPPING")
    if not set(raw) <= _KEYS:
        raise SensorMessageError("UNKNOWN_KEY", ",".join(sorted(str(k) for k in set(raw) - _KEYS)))
    kind = raw.get("kind")
    if kind not in _NUMERIC and kind != "PRESENCE":
        raise SensorMessageError("UNKNOWN_KIND")
    try:
        status = SourceStatus[raw.get("status")] if isinstance(raw.get("status"), str) else None
    except KeyError:
        status = None
    if status is None:
        raise SensorMessageError("BAD_STATUS")
    value, unit_raw, sensor_id = raw.get("value"), raw.get("unit"), raw.get("sensor_id")
    if sensor_id is not None and (not isinstance(sensor_id, str) or not _SENSOR_ID.fullmatch(sensor_id)):
        raise SensorMessageError("BAD_SENSOR_ID")          # one rule for every kind: absent, or a canonical non-empty id
    try:
        env = SourceEnvelope(raw.get("source_id"), origin, received_at_monotonic_s, status, raw.get("error_reason"), raw.get("device_timestamp"))
        if kind in ("PRESENCE", "LUX") and sensor_id is not None and sensor_id != env.source_id:
            raise SensorMessageError("SENSOR_ID_MISMATCH", f"{kind} has one sensor per source; sensor_id must equal source_id")
        if kind == "PRESENCE":
            if unit_raw is not None:
                raise SensorMessageError("UNEXPECTED_UNIT")
            return PresenceReading(env, value)
        cls, unit = _NUMERIC[kind]
        if (status is SourceStatus.OK or unit_raw is not None) and unit_raw != unit.value:
            raise SensorMessageError("BAD_UNIT", f"{kind} needs {unit.value!r}")
        if isinstance(value, bool):
            raise SensorMessageError("BAD_VALUE", "a bool is not a number")
        if cls is TemperatureReading:
            return TemperatureReading(env, sensor_id if sensor_id is not None else env.source_id, value)
        if cls is HumidityReading:
            return HumidityReading(env, value, sensor_id=sensor_id)
        return LuxReading(env, value)
    except FusionContractError as exc:
        raise SensorMessageError("CONTRACT_VIOLATION", str(exc)[:200]) from exc


class SensorSource(ABC):
    """The seam a Pi hardware adapter implements (owner: hardware / software team). It, not the message, states the origin."""

    @property
    @abstractmethod
    def origin(self) -> Origin: ...

    @abstractmethod
    def poll(self) -> List[Any]:
        """Return the decoded messages received since the last call (possibly none). Must not block for long."""


class FixtureSensorSource(SensorSource):
    """Scripted messages for tests and demos. Origin is SIMULATED by construction and cannot be changed."""

    def __init__(self, messages: Iterable[Any] = ()) -> None:
        self._pending = list(messages)

    @property
    def origin(self) -> Origin:
        return Origin.SIMULATED

    def push(self, message: Any) -> None:
        self._pending.append(message)

    def poll(self) -> List[Any]:
        out, self._pending = self._pending, []
        return out


class ClassroomStateStore:
    """Latest reading per source -> one ClassroomState. Thread-safe. The single state object every consumer (voice, app) reads.

    snapshot() copies the readings FIRST and samples the clock AFTER (the fusion snapshot-order rule), then fuses. Freshness
    values come from the injected FusionFreshnessConfig; the default config is all None = UNCONFIGURED = every value unavailable.
    """

    def __init__(self, freshness: FusionFreshnessConfig = FusionFreshnessConfig(), *, clock: Callable[[], float] = time.monotonic,
                 expected_sources: Optional[Mapping[str, str]] = None) -> None:
        """expected_sources: the CONFIGURED identities, source_id -> kind ("temperature", "humidity", "lux", "presence", "vision"),
        from the hardware team's confirmed sensor ids. When given, any other source is rejected (UNEXPECTED_SOURCE). When None
        (not confirmed yet), the first source that sends an OK reading binds a slot; a FAILED / UNAVAILABLE reading never binds."""
        if not isinstance(freshness, FusionFreshnessConfig):
            raise TypeError("freshness must be a FusionFreshnessConfig")
        if expected_sources is not None and (not isinstance(expected_sources, Mapping)
                                             or not set(expected_sources.values()) <= {k for _, k in _SLOTS} | {"temperature"}):
            raise TypeError("expected_sources maps source_id -> one of temperature, humidity, lux, presence, vision")
        self._expected = dict(expected_sources) if expected_sources is not None else None
        self._freshness, self._clock = freshness, clock
        self._lock = threading.Lock()
        self._latest: Dict[str, Any] = {}             # "vision" / "presence" / "lux" / "humidity" / ("temperature", sensor_id)
        self._owner: Dict[Any, Any] = {}              # slot -> its bound identity: source_id, or (source_id, sensor_id) for humidity
        self._kind_of: Dict[str, str] = {}            # source_id -> the one kind it reports
        # ponytail: bindings last for the store's lifetime; a deliberate rebind (sensor swapped) = a new store / restart
        self.rejected: Dict[str, int] = {}            # reason code -> count (structural only)

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    def ingest(self, reading) -> None:
        """Store `reading` as the latest of its slot. IDENTITY BINDING: a slot is owned by one identity (one lux, one presence, one
        camera, one humidity sensor; one source per temperature sensor_id), and a source_id reports one kind only. A reading from any
        other identity raises SensorMessageError (UNEXPECTED_SOURCE / SOURCE_ID_MISMATCH / SENSOR_ID_MISMATCH / SOURCE_KIND_MISMATCH)
        and never overwrites. All checks run BEFORE anything is bound, so a rejected reading claims nothing."""
        if isinstance(reading, TemperatureReading):
            key = ("temperature", reading.sensor_id)
        else:
            key = next((k for cls, k in _SLOTS if isinstance(reading, cls)), None)
        if key is None:
            raise TypeError(f"not a fusion reading: {type(reading).__name__}")
        sid, kind = reading.envelope.source_id, key[0] if isinstance(key, tuple) else key
        ident = (sid, reading.sensor_id) if kind == "humidity" else sid       # one humidity slot: one source AND one sensor
        with self._lock:
            if self._expected is not None and self._expected.get(sid) != kind:
                raise SensorMessageError("UNEXPECTED_SOURCE", f"{sid!r} is not a configured {kind} source")
            owner = self._owner.get(key)
            if owner is not None and owner != ident:
                same_source = isinstance(owner, tuple) and owner[0] == sid
                raise SensorMessageError("SENSOR_ID_MISMATCH" if same_source else "SOURCE_ID_MISMATCH", f"{kind} is bound to another sensor")
            if self._kind_of.get(sid, kind) != kind:
                raise SensorMessageError("SOURCE_KIND_MISMATCH", f"source {sid!r} already reports {self._kind_of[sid]}")
            if reading.envelope.ok:                   # only a working reading binds; FAILED / UNAVAILABLE never claims a slot
                self._owner.setdefault(key, ident)
                self._kind_of.setdefault(sid, kind)
            self._latest[key] = reading

    def ingest_messages(self, messages: Iterable[Any], origin: Origin) -> Tuple[int, int]:
        """Decode and store each message (receive time = now). Returns (accepted, rejected); rejections are counted by reason."""
        accepted = rejected = 0
        for raw in messages:
            try:
                self.ingest(decode_sensor_message(raw, origin=origin, received_at_monotonic_s=self._clock()))
            except SensorMessageError as exc:
                rejected += 1
                with self._lock:
                    self.rejected[exc.reason] = self.rejected.get(exc.reason, 0) + 1
                continue
            accepted += 1
        return accepted, rejected

    def pump(self, source: SensorSource) -> Tuple[int, int]:
        return self.ingest_messages(source.poll(), source.origin)

    def snapshot(self) -> ClassroomState:
        with self._lock:
            latest = dict(self._latest)
        temps = [v for k, v in sorted(latest.items(), key=lambda kv: str(kv[0])) if isinstance(k, tuple)]
        return fuse_classroom_state(latest.get("vision"), latest.get("presence"), latest.get("lux"), temps, latest.get("humidity"),
                                    self._clock(), self._freshness)
