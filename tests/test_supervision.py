import asyncio

import pytest

from services.core import config
from services.core.bus import Bus
from services.core.degradation import Degradations, Faults
from services.core.service import Context, Service
from services.core.supervisor import Supervisor


class Flaky(Service):
    name = "vision"
    component = "vision"
    starts = 0
    broken_starts = 0

    async def start(self):
        type(self).starts += 1
        if type(self).starts > 1 and type(self).broken_starts:
            type(self).broken_starts -= 1
            raise RuntimeError("camera busy")
        await super().start()


def make(cls, **kw):
    degradations = Degradations()
    ctx = Context(bus=Bus(strict=True), state=None, config=config.DEFAULTS, mode="replay",
                  degradations=degradations, faults=Faults())
    options = {"poll_interval": 0.02, "failure_limit": 3, "backoff_base": 0.01, "backoff_max": 0.05, "max_restarts": 2}
    options.update(kw)
    return Supervisor([cls], ctx, degradations=degradations, **options), degradations


def fresh(name="vision", broken_starts=0):
    return type("Probe", (Flaky,), {"name": name, "starts": 0, "broken_starts": broken_starts})


async def until(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def test_three_failed_polls_restart_the_service_with_a_counter():
    async def scenario():
        cls = fresh()
        supervisor, _ = make(cls)
        await supervisor.start()
        supervisor.supervise()
        first = supervisor.service("vision")
        first.crash("test")
        assert await until(lambda: supervisor.records["vision"].restarts == 1 and supervisor.records["vision"].state == "running")
        second = supervisor.service("vision")
        report = supervisor.report()
        await supervisor.stop()
        return first, second, report, cls.starts

    first, second, report, starts = asyncio.run(scenario())
    assert second is not first and starts == 2
    assert report[0]["restarts"] == 1 and report[0]["status"] == "ok" and report[0]["uptime_s"] == 0


def test_backoff_doubles_up_to_its_cap():
    supervisor, _ = make(fresh(), backoff_base=1.0, backoff_max=5.0)
    delays = [min(supervisor.backoff_max, supervisor.backoff_base * 2 ** (n - 1)) for n in range(1, 6)]
    assert delays == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_the_supervisor_gives_up_and_degrades_instead_of_looping():
    async def scenario():
        supervisor, degradations = make(fresh(broken_starts=10), max_restarts=2)
        await supervisor.start()
        supervisor.supervise()
        supervisor.service("vision").crash("test")
        assert await until(lambda: supervisor.records["vision"].state == "failed", 5)
        restarts = supervisor.records["vision"].restarts
        await asyncio.sleep(0.2)
        report = supervisor.report()
        flags = degradations.list()
        await supervisor.stop()
        return restarts, report, flags

    restarts, report, flags = asyncio.run(scenario())
    assert restarts == 2
    assert report[0]["status"] == "down" and report[0]["uptime_s"] is None
    assert flags[0]["flag"] == "service_failed" and flags[0]["detail"] == "vision"


def test_a_health_check_that_raises_counts_as_a_failure():
    class Broken(Flaky):
        name = "engine"
        component = "engine"
        calls = 0

        def health(self):
            type(self).calls += 1
            if type(self).calls > 1 and type(self).calls < 6:
                raise RuntimeError("boom")
            return super().health()

    async def scenario():
        supervisor, _ = make(type("B", (Broken,), {"calls": 0, "starts": 0}))
        await supervisor.start()
        supervisor.supervise()
        assert await until(lambda: supervisor.records["engine"].restarts == 1)
        await supervisor.stop()

    asyncio.run(scenario())


def test_degradations_are_listed_in_matrix_order_and_cleared():
    flags = Degradations()
    flags.set("over_budget", "shedding vision")
    flags.set("device_link_lost")
    assert [d["flag"] for d in flags.list()] == ["device_link_lost", "over_budget"]
    flags.clear("device_link_lost")
    assert [d["flag"] for d in flags.list()] == ["over_budget"]
    with pytest.raises(ValueError):
        flags.set("cosmic_rays")
    faults = Faults()
    with pytest.raises(ValueError):
        faults.cut("toaster")
