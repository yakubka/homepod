"""python -m services.core

Starts the bus, the state store and every registered service, prints one
health line per service, and runs until Ctrl C or SIGTERM. Shutdown stops the
services in reverse order and kills the process if that takes longer than
shutdown.timeout.
"""

import argparse
import asyncio
import logging
import signal
import sys
import threading

from services.core import config, logs
from services.core.bus import Bus
from services.core.guard import RuntimeGuard
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


async def run(run_for=None, specs=SERVICES):
    cfg = config.load()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    _install_signals(loop, stop)

    bus = Bus(queue_size=cfg["bus.queue_size"])
    bus.attach(loop)
    state = StateStore(bus)
    await state.start()
    scheduler = Scheduler(permits=cfg["models.heavy_permits"])
    models = Registry(scheduler=scheduler)
    guard = RuntimeGuard(models, lambda topic, payload: bus.publish_threadsafe(topic, payload, "core"))
    models.observe = guard.observe
    supervisor = Supervisor(
        load(specs),
        Context(bus=bus, state=state, config=cfg, mode=config.MODE, models=models, scheduler=scheduler),
        start_timeout=cfg["service.start_timeout"],
        stop_timeout=cfg["service.stop_timeout"],
    )

    async def watch_memory():
        while True:
            await asyncio.sleep(cfg["guard.interval"])
            guard.check_memory()

    watcher = asyncio.create_task(watch_memory(), name="guard")
    try:
        await supervisor.start()
        log.info("core ready", extra={"mode": config.MODE, "strict": bus.strict, "services": list(supervisor.health())})
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
