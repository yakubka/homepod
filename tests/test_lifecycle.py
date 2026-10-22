import asyncio
import threading

import pytest

from services.core import config
from services.core.bus import Bus
from services.core.service import Context, Service
from services.core.supervisor import StartupError, Supervisor, Watchdog


def context():
    return Context(bus=Bus(strict=True), state=None, config=config.DEFAULTS, mode="replay")


class Probe(Service):
    journal = None
    broken = None

    async def start(self):
        if self.name == self.broken:
            raise RuntimeError("no camera")
        self.journal.append(f"start {self.name}")
        await super().start()

    async def stop(self):
        self.journal.append(f"stop {self.name}")
        await super().stop()


def services(journal, broken=None):
    return [
        type(name.title(), (Probe,), {"name": name, "component": component, "journal": journal, "broken": broken})
        for name, component in (("device", "bridge"), ("vision", "vision"), ("api", "api"))
    ]


def test_services_start_in_order_and_stop_in_reverse():
    journal = []

    async def scenario():
        supervisor = Supervisor(services(journal), context())
        await supervisor.start()
        health = supervisor.health()
        await supervisor.stop()
        return health

    health = asyncio.run(scenario())
    assert journal == ["start device", "start vision", "start api", "stop api", "stop vision", "stop device"]
    assert all(h["ok"] and h["detail"] == "running" and h["last_tick"] for h in health.values())
    assert set(health["api"]) == {"ok", "detail", "last_tick"}


def test_a_failed_start_stops_what_already_started():
    journal = []

    async def scenario():
        await Supervisor(services(journal, broken="vision"), context()).start()

    with pytest.raises(StartupError, match="vision"):
        asyncio.run(scenario())
    assert journal == ["start device", "stop device"]


def test_a_crashed_background_task_turns_health_red():
    async def scenario():
        service = Service(context())
        service.component = "engine"
        await service.start()

        async def boom():
            raise ValueError("bad rule")

        service.spawn(boom(), "rules")
        await asyncio.sleep(0.01)
        health = service.health()
        await service.stop()
        return health

    health = asyncio.run(scenario())
    assert not health["ok"] and "bad rule" in health["detail"]


def test_the_watchdog_kills_a_shutdown_that_hangs():
    killed = threading.Event()
    Watchdog(0.05, kill=lambda code: killed.set()).start()
    assert killed.wait(1.0)


def test_a_finished_shutdown_cancels_the_watchdog():
    killed = threading.Event()
    Watchdog(0.05, kill=lambda code: killed.set()).start().cancel()
    assert not killed.wait(0.2)
