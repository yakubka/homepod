"""Service lifecycle interface.

Every package exposes one Service subclass. Core constructs it with a Context,
awaits start(), polls health() and awaits stop() on shutdown. The base class
publishes system.heartbeat for the service, so a stub that only subclasses it
is already a healthy, visible member of the room.

    class VisionService(Service):
        name = "vision"
        component = "vision"

        async def start(self):
            await super().start()
            self.spawn(self.loop())
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from services.core.logs import iso_utc


@dataclass
class Context:
    bus: object
    state: object
    config: dict
    mode: str
    models: object = None
    scheduler: object = None
    degradations: object = None
    faults: object = None
    supervisor: object = None


class Service:
    name = "service"
    component = None
    version = "0.1.0"

    def __init__(self, ctx):
        self.ctx = ctx
        self.bus = ctx.bus
        self.state = ctx.state
        self.log = logging.getLogger(self.name)
        self.started_at = None
        self.last_tick = None
        self.failure = None
        self.tasks = []

    async def start(self):
        self.started_at = time.monotonic()
        self.tick()
        self.spawn(self._heartbeat(), "heartbeat")
        self.spawn(self._degradations(), "degraded")

    def on_degraded(self, event):
        """Override to switch to a fallback when core reports a budget breach, see system.degraded."""

    async def _degradations(self):
        sub = self.bus.subscribe("system.degraded", name=f"{self.name}.degraded", maxsize=16)
        try:
            async for envelope in sub:
                self.on_degraded(envelope["payload"])
        finally:
            sub.close()

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.started_at = None

    def health(self):
        if self.failure:
            return {"ok": False, "detail": self.failure, "last_tick": self.last_tick}
        if self.started_at is None:
            return {"ok": False, "detail": "not started", "last_tick": self.last_tick}
        return {"ok": True, "detail": "running", "last_tick": self.last_tick}

    def crash(self, reason="killed"):
        """Stop every task as if the service had died. Used by chaos testing, the supervisor restarts it."""
        self.failure = reason
        for task in self.tasks:
            task.cancel()

    def degrade(self, flag, detail=None):
        if self.ctx.degradations is not None:
            self.ctx.degradations.set(flag, detail)

    def recover(self, flag):
        if self.ctx.degradations is not None:
            self.ctx.degradations.clear(flag)

    def fault(self, link):
        return self.ctx.faults is not None and self.ctx.faults.active(link)

    def tick(self):
        self.last_tick = iso_utc()

    def publish(self, topic, payload, correlation_id=None):
        return self.bus.publish(topic, payload, self.component, correlation_id)

    def spawn(self, coro, label=None):
        task = asyncio.create_task(coro, name=f"{self.name}.{label or 'task'}")
        task.add_done_callback(self._finished)
        self.tasks.append(task)
        return task

    def _finished(self, task):
        if task.cancelled() or task.exception() is None:
            return
        self.failure = f"{task.get_name()} crashed: {task.exception()!r}"
        self.log.error("background task crashed", exc_info=task.exception(), extra={"task": task.get_name()})

    async def every(self, interval, work):
        while True:
            await work()
            self.tick()
            await asyncio.sleep(interval)

    async def _heartbeat(self):
        while True:
            try:
                health = self.health()
            except Exception as e:
                health = {"ok": False, "detail": f"health() raised {e!r}"}
            self.publish(
                "system.heartbeat",
                {
                    "component": self.component,
                    "status": "ok" if health["ok"] else "degraded",
                    "version": self.version,
                    "uptime_s": int(time.monotonic() - (self.started_at or time.monotonic())),
                    "detail": None if health["ok"] else health["detail"][:200],
                },
            )
            await asyncio.sleep(self.ctx.config["heartbeat.interval"])
