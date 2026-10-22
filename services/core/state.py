"""Room state store.

One typed snapshot of the room, assembled from bus events. Every field carries
the time it was last updated, its age and a staleness flag, with the threshold
per field taken from the state.stale.<field> settings:

    room = store.snapshot()
    if room.environment.stale:
        ...  # the reading is older than state.stale.environment seconds
"""

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Dict, Generic, Optional, TypeVar

import room_contracts as rc
from pydantic import ValidationError

from services.core import config
from services.core.logs import iso_utc

log = logging.getLogger("state")

T = TypeVar("T")

TOPICS = {
    "sensor.environment": ("environment", rc.EnvironmentReading),
    "sensor.presence": ("presence", rc.PresenceReading),
    "perception.occupancy": ("occupancy", rc.OccupancySnapshot),
    "device.status": ("device", rc.DeviceLink),
    "schedule.lecture": ("lecture", rc.LectureEvent),
    "settings.changed": ("settings", rc.Settings),
    "system.state": ("system", rc.SystemState),
    "decision.action": ("decision", rc.Decision),
    "system.heartbeat": ("heartbeat", rc.Heartbeat),
}


@dataclass(frozen=True)
class Field(Generic[T]):
    value: Optional[T]
    updated_at: Optional[str]
    age_s: Optional[float]
    stale: bool


@dataclass(frozen=True)
class RoomSnapshot:
    at: str
    environment: Field[rc.EnvironmentReading]
    presence: Field[rc.PresenceReading]
    occupancy: Field[rc.OccupancySnapshot]
    device: Field[rc.DeviceLink]
    lecture: Field[rc.LectureEvent]
    settings: Field[rc.Settings]
    system: Field[rc.SystemState]
    decision: Field[rc.Decision]
    heartbeats: Dict[str, Field[rc.Heartbeat]]

    def stale_fields(self):
        names = [name for name in FIELDS if getattr(self, name).stale]
        return names + [f"heartbeat.{name}" for name, item in self.heartbeats.items() if item.stale]


FIELDS = ("environment", "presence", "occupancy", "device", "lecture", "settings", "system", "decision")
LINKED = ("environment", "presence")


class StateStore:
    def __init__(self, bus, clock=time.monotonic, settings=config.load):
        self.bus = bus
        self.clock = clock
        self.settings = settings
        self.fields = {}
        self.heartbeats = {}
        self.sub = None
        self.task = None

    async def start(self):
        self.sub = self.bus.subscribe("#", name="state", maxsize=1024)
        self.task = asyncio.create_task(self._run(), name="state")

    async def stop(self):
        if self.sub:
            self.sub.close()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)

    async def _run(self):
        async for envelope in self.sub:
            self.apply(envelope)

    def apply(self, envelope):
        entry = TOPICS.get(envelope["topic"])
        if entry is None:
            return
        name, model = entry
        try:
            value = model.model_validate(envelope["payload"])
        except ValidationError as e:
            log.warning("state ignored an invalid payload", extra={"topic": envelope["topic"], "errors": e.error_count()})
            return
        record = (value, envelope["ts"], self.clock())
        if name == "heartbeat":
            self.heartbeats[value.component.value] = record
        else:
            self.fields[name] = record

    def _field(self, record, threshold, now):
        if record is None:
            return Field(None, None, None, True)
        value, updated_at, seen = record
        age = now - seen
        return Field(value, updated_at, round(age, 3), age > threshold)

    def get(self, name, cfg=None):
        cfg = cfg or self.settings()
        field = self._field(self.fields.get(name), cfg[f"state.stale.{name}"], self.clock())
        if name in LINKED and self._link_down():
            field = replace(field, stale=True)
        return field

    def _link_down(self):
        record = self.fields.get("device")
        return record is not None and record[0].status.value != "ok"

    def snapshot(self):
        cfg = self.settings()
        now = self.clock()
        fields = {name: self._field(self.fields.get(name), cfg[f"state.stale.{name}"], now) for name in FIELDS}
        if self._link_down():
            for name in LINKED:
                fields[name] = replace(fields[name], stale=True)
        beats = {
            name: self._field(record, cfg["state.stale.heartbeat"], now)
            for name, record in sorted(self.heartbeats.items())
        }
        return RoomSnapshot(at=iso_utc(), heartbeats=beats, **fields)
