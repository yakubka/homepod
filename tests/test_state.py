import asyncio

from services.core import config
from services.core.bus import Bus
from services.core.state import StateStore

READING = {"ts": "2026-10-13T08:55:01.512Z", "device_id": "esp32-a1b2c3", "temperature_c": 23.4, "humidity_pct": 41, "lux": 140}
BEAT = {"component": "vision", "status": "ok", "version": "0.1.0", "uptime_s": 10, "detail": None}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def envelope(topic, payload, ts="2026-10-13T08:55:01.515Z"):
    return {"topic": topic, "ts": ts, "payload": payload}


def test_a_field_is_fresh_then_stale_after_its_own_threshold():
    clock = Clock()
    store = StateStore(bus=None, clock=clock, settings=lambda: {**config.DEFAULTS, "state.stale.environment": 10.0})
    store.apply(envelope("sensor.environment", READING))

    clock.now += 2
    fresh = store.snapshot().environment
    assert fresh.value.temperature_c == 23.4 and fresh.age_s == 2.0 and not fresh.stale

    clock.now += 240
    stale = store.snapshot().environment
    assert stale.stale and stale.age_s == 242.0 and stale.updated_at == "2026-10-13T08:55:01.515Z"


def test_a_field_never_seen_is_empty_and_stale():
    room = StateStore(bus=None, clock=Clock(), settings=lambda: config.DEFAULTS).snapshot()
    assert room.occupancy.value is None and room.occupancy.stale
    assert "occupancy" in room.stale_fields()


def test_an_invalid_payload_keeps_the_previous_value():
    store = StateStore(bus=None, clock=Clock(), settings=lambda: config.DEFAULTS)
    store.apply(envelope("sensor.environment", READING))
    store.apply(envelope("sensor.environment", {**READING, "lux": "bright"}))
    assert store.get("environment").value.lux == 140


def test_heartbeats_are_tracked_per_component():
    store = StateStore(bus=None, clock=Clock(), settings=lambda: config.DEFAULTS)
    store.apply(envelope("system.heartbeat", BEAT))
    assert store.snapshot().heartbeats["vision"].value.status.value == "ok"


def test_the_store_assembles_the_room_from_the_bus():
    async def scenario():
        bus = Bus(strict=True)
        store = StateStore(bus)
        await store.start()
        bus.publish("sensor.environment", READING, "bridge")
        await asyncio.sleep(0.01)
        room = store.snapshot()
        await store.stop()
        return room

    room = asyncio.run(scenario())
    assert room.environment.value.humidity_pct == 41 and not room.environment.stale
