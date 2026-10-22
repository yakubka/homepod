"""The degraded matrix of docs/degraded.md, row by row, in process.

tools/chaos.py runs the same rows against a whole running room, see test_chaos.py.
"""

import asyncio

import pytest

from services.core import config
from services.core.bus import Bus
from services.core.degradation import Degradations, Faults
from services.core.engine.service import BUSY, OFFLINE, EngineService
from services.core.guard import RuntimeGuard
from services.core.models import Priority, Registry
from services.core.scheduler import SHED_ORDER, Scheduler, Shed
from services.core.service import Context
from services.core.state import StateStore

READING = {"ts": "2026-10-20T09:00:00.000Z", "device_id": "esp32-000000", "temperature_c": 22.4, "humidity_pct": 40, "lux": 200}


def link(status):
    return {"device_id": "esp32-000000", "status": status, "reason": None if status == "ok" else "port_closed", "fw_version": None,
            "protocol_version": "1.0.3", "uptime_ms": None, "last_seen_at": "2026-10-20T09:00:00.000Z", "last_error": None}


def test_link_lost_marks_the_last_readings_stale_at_once():
    store = StateStore(bus=None, settings=lambda: config.DEFAULTS)
    store.apply({"topic": "sensor.environment", "ts": READING["ts"], "payload": READING})
    store.apply({"topic": "device.status", "ts": READING["ts"], "payload": link("ok")})
    assert not store.get("environment").stale
    store.apply({"topic": "device.status", "ts": READING["ts"], "payload": link("down")})
    assert store.get("environment").stale and store.snapshot().environment.stale
    assert store.get("environment").value.temperature_c == 22.4


def test_shedding_takes_vision_then_general_and_never_announcements():
    scheduler = Scheduler()
    assert SHED_ORDER == ("vision", "general")
    scheduler.shed(1)
    assert not scheduler.admits("vision") and scheduler.admits("general") and scheduler.admits("announcement")
    with pytest.raises(Shed):
        scheduler.run(Priority.VISION, lambda: None)
    scheduler.shed(2)
    assert not scheduler.admits("general") and scheduler.admits("announcement")
    assert scheduler.run(Priority.VOICE, lambda: "spoken", purpose="announcement") == "spoken"
    scheduler.shed(9)
    assert scheduler.shed_level == 2


def test_the_guard_escalates_while_over_budget_and_relaxes_after():
    clock = [0.0]
    levels = []
    registry = Registry(specs=[], directory=".")
    rss = [1300.0]
    guard = RuntimeGuard(registry, lambda *_: None, ceiling_mb=1200, cooldown=5.0, rss=lambda: rss[0], clock=lambda: clock[0])
    guard.on_breach = levels.append
    guard.on_relax = lambda: levels.append(0)
    guard.check_memory()
    clock[0] += 1
    guard.check_memory()
    assert levels == [1, 2]
    rss[0] = 800.0
    clock[0] += 1
    guard.check_memory()
    assert levels == [1, 2]
    clock[0] += 10
    guard.check_memory()
    assert levels == [1, 2, 0] and guard.level == 0


async def engine_room(degradations=None, scheduler=None, device="ok"):
    bus = Bus(strict=True)
    state = StateStore(bus)
    await state.start()
    bus.publish("device.status", link(device), "bridge")
    bus.publish("sensor.environment", READING, "bridge")
    ctx = Context(bus=bus, state=state, config=config.DEFAULTS, mode="replay", degradations=degradations or Degradations(),
                  faults=Faults(), scheduler=scheduler or Scheduler())
    engine = EngineService(ctx)
    await engine.start()
    out = bus.subscribe("#", name="test")
    await asyncio.sleep(0.02)
    return bus, state, engine, out


async def drain(sub):
    await asyncio.sleep(0.05)
    items = []
    while sub.pending:
        items.append(sub.get_nowait())
    return items


def intent(name, request_id="req_test000001", source="app", params=None, target="none"):
    return {"request_id": request_id, "intent": name, "action": "none" if name == "unknown" else "query",
            "target": {"type": target, "zone": None}, "params": params or {}, "confidence": 1.0, "raw_text": name, "source": source}


def test_the_wake_word_lights_the_ring_and_an_empty_transcript_ends_it():
    async def scenario():
        bus, state, engine, out = await engine_room()
        bus.publish("voice.wake", {"request_id": "req_wake000001", "ts": READING["ts"], "wake_word": "hey_jarvis", "confidence": 0.9}, "voice")
        bus.publish("voice.transcript", {"request_id": "req_wake000001", "ts": READING["ts"], "text": "", "confidence": 0.0,
                                         "language": "en-US", "source": "microphone", "duration_ms": None}, "voice")
        items = await drain(out)
        await engine.stop()
        await state.stop()
        return [e["payload"]["pattern"] for e in items if e["topic"] == "device.command"]

    assert asyncio.run(scenario()) == ["listening", "error"]


def test_link_lost_rejects_lights_but_answers_questions_from_the_last_reading():
    async def scenario():
        bus, state, engine, out = await engine_room(device="down")
        bus.publish("voice.intent", {**intent("lights_on", target="lighting"), "action": "set"}, "voice")
        bus.publish("voice.intent", {**intent("query_temperature", "req_test000002", target="climate")}, "voice")
        items = await drain(out)
        await engine.stop()
        await state.stop()
        return [e["payload"] for e in items if e["topic"] == "decision.action"], [e for e in items if e["topic"] == "device.command"]

    decisions, commands = asyncio.run(scenario())
    assert commands == []
    assert decisions[0]["outcome"] == "rejected" and "can't reach the lights" in decisions[0]["reply_text"]
    assert decisions[1]["outcome"] == "answered" and "offline" in decisions[1]["reply_text"] and "22.4 degrees" in decisions[1]["reply_text"]


@pytest.mark.parametrize("cause, sentence", [("internet", OFFLINE), ("shed", BUSY)])
def test_general_questions_fail_with_a_spoken_sentence(cause, sentence):
    async def scenario():
        degradations, scheduler = Degradations(), Scheduler()
        if cause == "internet":
            degradations.set("internet_unavailable")
        else:
            scheduler.shed(2)
        bus, state, engine, out = await engine_room(degradations, scheduler)
        bus.publish("voice.intent", intent("unknown"), "voice")
        bus.publish("voice.intent", {**intent("query_humidity", "req_test000003", target="climate")}, "voice")
        items = await drain(out)
        await engine.stop()
        await state.stop()
        return [e["payload"]["reply_text"] for e in items if e["topic"] == "decision.action"]

    replies = asyncio.run(scenario())
    assert replies[0] == sentence and replies[1] == "It is 40 percent humidity in the room."
