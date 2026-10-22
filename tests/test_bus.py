import asyncio
import io
import json
import logging
import time

import pytest

from services.core import logs
from services.core.bus import Bus, matches
from services.core.contract import ContractViolation

READING = {"ts": "2026-10-13T08:55:01.512Z", "device_id": "esp32-a1b2c3", "temperature_c": 23.4, "humidity_pct": 41, "lux": 140}


def test_wildcards_match_one_segment_or_the_rest():
    assert matches("sensor.*", "sensor.environment")
    assert not matches("sensor.*", "sensor.environment.extra")
    assert matches("sensor.#", "sensor.environment")
    assert matches("#", "system.state")
    assert not matches("device.*", "sensor.environment")
    assert matches("system.state", "system.state")


def test_a_slow_subscriber_loses_its_oldest_events_and_never_blocks_the_publisher():
    async def scenario():
        bus = Bus(strict=False, queue_size=100)
        slow = bus.subscribe("sensor.environment", name="slow")
        consumed = 0

        async def consume():
            nonlocal consumed
            async for _ in slow:
                consumed += 1
                await asyncio.sleep(0.001)

        consumer = asyncio.create_task(consume())
        worst = 0.0
        for n in range(10_000):
            started = time.perf_counter()
            bus.publish("sensor.environment", READING, "bridge")
            worst = max(worst, time.perf_counter() - started)
            if n % 100 == 0:
                await asyncio.sleep(0)
        slow.close()
        await consumer
        return consumed, slow.dropped, worst

    consumed, dropped, worst = asyncio.run(scenario())
    assert dropped > 0
    assert consumed + dropped == 10_000
    assert worst < 0.1


def test_strict_mode_rejects_a_payload_that_violates_its_schema():
    async def scenario():
        bus = Bus(strict=True)
        sub = bus.subscribe("#")
        with pytest.raises(ContractViolation) as caught:
            bus.publish("sensor.environment", {**READING, "temperature_c": "warm"}, "bridge")
        return sub.pending, bus.last_value("sensor.environment"), caught.value

    pending, last, error = asyncio.run(scenario())
    assert pending == 0 and last is None
    assert error.topic == "sensor.environment"
    assert any("temperature_c" in e for e in error.errors)


def test_strict_mode_rejects_the_wrong_publisher():
    bus = Bus(strict=True)
    with pytest.raises(ContractViolation):
        bus.publish("sensor.environment", READING, "vision")


def test_production_mode_logs_a_violation_and_still_delivers(caplog):
    async def scenario():
        bus = Bus(strict=False)
        sub = bus.subscribe("sensor.*")
        bus.publish("sensor.environment", {**READING, "lux": -5}, "bridge")
        return sub.pending, bus.violations

    with caplog.at_level(logging.WARNING, logger="bus"):
        pending, violations = asyncio.run(scenario())
    assert pending == 1 and violations == 1
    assert "contract violation" in caplog.text


def test_a_late_subscriber_gets_the_current_state_but_no_old_commands():
    async def scenario():
        bus = Bus(strict=True)
        bus.publish("sensor.environment", READING, "bridge")
        bus.publish(
            "device.command",
            {"type": "ping", "ts": "2026-10-13T09:31:10.000Z", "cmd_id": "c00a41f32"},
            "engine",
        )
        late = bus.subscribe("#")
        return [late.get_nowait()["topic"] for _ in range(late.pending)]

    assert asyncio.run(scenario()) == ["sensor.environment"]


def test_the_trace_id_follows_an_event_from_publisher_to_consumer_logs():
    stream = io.StringIO()
    handler = logs.configure("info", "json", stream)

    async def scenario():
        bus = Bus(strict=True)
        sub = bus.subscribe("sensor.environment")
        with logs.trace("req_8d1f6g3h2j"):
            envelope = bus.publish("sensor.environment", READING, "bridge")

        async def consumer():
            await sub.get()
            logging.getLogger("engine").info("handled reading")

        await asyncio.create_task(consumer())
        return envelope

    try:
        envelope = asyncio.run(scenario())
    finally:
        logging.getLogger().removeHandler(handler)
    assert envelope["correlation_id"] == "req_8d1f6g3h2j"
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["msg"] == "handled reading" and line["trace_id"] == "req_8d1f6g3h2j"
