"""python -m services.core

Starts the bus, the state store, the model runtime and every registered
service, prints one health line per service, supervises them, and runs until
Ctrl C or SIGTERM. Shutdown stops the services in reverse order and kills the
process if that takes longer than shutdown.timeout.
"""

import argparse
import asyncio
import logging
import signal
import sys
import threading

from services.core import config, logs
from services.core.bus import Bus
from services.core.degradation import Degradations, Faults
from services.core.guard import RuntimeGuard, rss_mb
from services.core.models import Registry
from services.core.registry import SERVICES
from services.core.scheduler import Scheduler
from services.core.service import Context
from services.core.state import StateStore
from services.core.supervisor import StartupError, Supervisor, Watchdog, load

log = logging.getLogger("core")


def _install_signals(loop, stop):
    def request(*_):
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, request)


def _budget(guard, scheduler, degradations, faults, ceiling_mb):
    def breach(level):
        scheduler.shed(level)
        degradations.set("over_budget", f"shedding {', '.join(['vision', 'general'][:scheduler.shed_level])}")

    def relax():
        scheduler.shed(0)
        degradations.clear("over_budget")

    guard.on_breach = breach
    guard.on_relax = relax
    guard.rss = lambda: ceiling_mb * 1.1 if faults.active("budget") else rss_mb()


async def run(run_for=None, specs=SERVICES):
    cfg = config.load()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    _install_signals(loop, stop)

    bus = Bus(queue_size=cfg["bus.queue_size"])
    bus.attach(loop)
    state = StateStore(bus)
    await state.start()
    degradations, faults = Degradations(), Faults()
    scheduler = Scheduler(permits=cfg["models.heavy_permits"])
    models = Registry(scheduler=scheduler)
    guard = RuntimeGuard(models, lambda topic, payload: bus.publish_threadsafe(topic, payload, "core"))
    models.observe = guard.observe
    _budget(guard, scheduler, degradations, faults, cfg["models.memory_ceiling_mb"])

    ctx = Context(bus=bus, state=state, config=cfg, mode=config.MODE, models=models, scheduler=scheduler,
                  degradations=degradations, faults=faults)
    supervisor = Supervisor(
        load(specs),
        ctx,
        start_timeout=cfg["service.start_timeout"],
        stop_timeout=cfg["service.stop_timeout"],
        poll_interval=cfg["supervisor.poll_interval"],
        failure_limit=cfg["supervisor.failure_limit"],
        backoff_base=cfg["supervisor.backoff_base"],
        backoff_max=cfg["supervisor.backoff_max"],
        max_restarts=cfg["supervisor.max_restarts"],
        degradations=degradations,
    )
    ctx.supervisor = supervisor

    async def watch_memory():
        while True:
            await asyncio.sleep(cfg["guard.interval"])
            guard.check_memory()

    watcher = asyncio.create_task(watch_memory(), name="guard")
    try:
        await supervisor.start()
        supervisor.supervise()
        log.info("core ready", extra={"mode": config.MODE, "strict": bus.strict, "chaos": config.CHAOS,
                                      "services": list(supervisor.health())})
        try:
            await asyncio.wait_for(stop.wait(), run_for)
        except asyncio.TimeoutError:
            pass
        log.info("core stopping")
    finally:
        watchdog = Watchdog(cfg["shutdown.timeout"]).start()
        watcher.cancel()
        await supervisor.stop()
        await state.stop()
        models.unload()
        watchdog.cancel()
    log.info("core stopped", extra={"bus": {"published": bus.published, "violations": bus.violations}})


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m services.core", description=__doc__.splitlines()[0])
    parser.add_argument("--run-for", type=float, default=None, help="stop by itself after this many seconds")
    args = parser.parse_args(argv)

    cfg = config.load()
    logs.configure(cfg["log_level"], cfg["log.format"])
    if sys.platform == "win32":
        # The Pi runs the selector loop, so development does too. The proactor
        # loop on Windows can trip over a connection accepted while the API restarts.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run(args.run_for))
    except StartupError:
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_DFL)
    leftover = [t.name for t in threading.enumerate() if t is not threading.main_thread() and not t.daemon]
    if leftover:
        log.warning("threads still running at exit", extra={"threads": leftover})
    return 0


if __name__ == "__main__":
    sys.exit(main())
