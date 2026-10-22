"""Starts services in declared order, supervises them, stops them in reverse.

Health is polled every supervisor.poll_interval. After supervisor.failure_limit
consecutive failed polls a service is restarted, waiting
supervisor.backoff_base * 2 ** (restarts - 1) seconds, capped at
supervisor.backoff_max. After supervisor.max_restarts restarts the supervisor
gives up on it, stops it and raises the service_failed degradation instead of
looping forever.
"""

import asyncio
import importlib
import logging
import os
import threading
import time

log = logging.getLogger("core")

HEALTH_NAMES = {"device": "bridge"}


class StartupError(RuntimeError):
    pass


def load(specs):
    classes = []
    for spec in specs:
        module, _, name = spec.partition(":")
        classes.append(getattr(importlib.import_module(module), name))
    return classes


class Record:
    def __init__(self, cls, service):
        self.cls = cls
        self.service = service
        self.name = service.name
        self.state = "running"
        self.restarts = 0
        self.failures = 0
        self.started = time.monotonic()
        self.detail = None


class Supervisor:
    def __init__(self, classes, ctx, start_timeout=5.0, stop_timeout=5.0, poll_interval=1.0, failure_limit=3,
                 backoff_base=1.0, backoff_max=30.0, max_restarts=5, degradations=None):
        self.classes = classes
        self.ctx = ctx
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval
        self.failure_limit = failure_limit
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.max_restarts = max_restarts
        self.degradations = degradations
        self.records = {}
        self.running = []
        self.tasks = set()

    async def _start_one(self, cls):
        service = cls(self.ctx)
        await asyncio.wait_for(service.start(), self.start_timeout)
        return service

    @staticmethod
    def _health(service):
        try:
            return service.health()
        except Exception as e:
            return {"ok": False, "detail": f"health() raised {e!r}", "last_tick": None}

    async def start(self):
        for cls in self.classes:
            try:
                service = await self._start_one(cls)
            except Exception as e:
                reason = "timed out" if isinstance(e, asyncio.TimeoutError) else repr(e)
                log.error("service failed to start", extra={"service": getattr(cls, "name", cls.__name__), "reason": reason})
                await self.stop()
                raise StartupError(f"{getattr(cls, 'name', cls.__name__)} failed to start: {reason}") from None
            self.running.append(service)
            self.records[service.name] = Record(cls, service)
            health = self._health(service)
            log.info(
                "service healthy" if health["ok"] else "service unhealthy",
                extra={"service": service.name, **health},
            )

    def supervise(self):
        task = asyncio.create_task(self._monitor(), name="supervisor")
        self.tasks.add(task)
        return task

    async def _monitor(self):
        while True:
            await asyncio.sleep(self.poll_interval)
            for record in list(self.records.values()):
                if record.state != "running":
                    continue
                health = self._health(record.service)
                ok, record.detail = health["ok"], health["detail"]
                record.failures = 0 if ok else record.failures + 1
                if record.failures >= self.failure_limit:
                    if record.restarts >= self.max_restarts:
                        await self._give_up(record)
                    else:
                        record.state = "restarting"
                        task = asyncio.create_task(self._restart(record), name=f"restart.{record.name}")
                        self.tasks.add(task)
                        task.add_done_callback(self.tasks.discard)

    async def _restart(self, record):
        record.restarts += 1
        delay = min(self.backoff_max, self.backoff_base * 2 ** (record.restarts - 1))
        log.warning("restarting service", extra={"service": record.name, "attempt": record.restarts,
                                                 "backoff_s": delay, "reason": record.detail})
        await self._stop_one(record.service)
        await asyncio.sleep(delay)
        try:
            service = await self._start_one(record.cls)
        except Exception as e:
            record.detail = f"restart failed: {e!r}"
            record.failures = self.failure_limit
            record.state = "running"
            log.error("service restart failed", extra={"service": record.name, "reason": repr(e)})
            return
        self.running[self.running.index(record.service)] = service
        record.service = service
        record.state = "running"
        record.failures = 0
        record.started = time.monotonic()
        log.info("service restarted", extra={"service": record.name, "restarts": record.restarts})

    async def _give_up(self, record):
        record.state = "failed"
        log.error("giving up on service", extra={"service": record.name, "restarts": record.restarts, "reason": record.detail})
        await self._stop_one(record.service)
        if self.degradations is not None:
            failed = sorted(r.name for r in self.records.values() if r.state == "failed")
            self.degradations.set("service_failed", ", ".join(failed))

    async def _stop_one(self, service):
        try:
            await asyncio.wait_for(service.stop(), self.stop_timeout)
            return True
        except asyncio.TimeoutError:
            log.error("service did not stop in time", extra={"service": service.name, "timeout_s": self.stop_timeout})
        except Exception as e:
            log.error("service failed to stop", extra={"service": service.name, "reason": repr(e)})
        return False

    async def stop(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        while self.running:
            service = self.running.pop()
            record = self.records.get(service.name)
            if record is not None and record.state == "failed":
                continue
            if await self._stop_one(service):
                log.info("service stopped", extra={"service": service.name})

    def service(self, name):
        record = self.records.get(name)
        return record.service if record else None

    def health(self):
        return {name: self._health(record.service) for name, record in self.records.items()}

    def report(self):
        now = time.monotonic()
        rows = []
        for record in self.records.values():
            health = self._health(record.service)
            if record.state == "failed":
                status = "down"
            elif record.state == "restarting" or not health["ok"]:
                status = "degraded"
            else:
                status = "ok"
            rows.append({
                "name": HEALTH_NAMES.get(record.name, record.name),
                "status": status,
                "last_seen_at": health["last_tick"],
                "detail": None if status == "ok" else (health["detail"] or record.state)[:200],
                "uptime_s": int(now - record.started) if record.state == "running" else None,
                "restarts": record.restarts,
            })
        return rows


class Watchdog:
    """Hard kill when a graceful shutdown takes longer than its timeout."""

    def __init__(self, timeout, kill=os._exit):
        self.timeout = timeout
        self.kill = kill
        self.timer = threading.Timer(timeout, self._fire)
        self.timer.daemon = True

    def _fire(self):
        log.error("shutdown timed out, killing the process", extra={"timeout_s": self.timeout})
        for handler in logging.getLogger().handlers:
            handler.flush()
        self.kill(1)

    def start(self):
        self.timer.start()
        return self

    def cancel(self):
        self.timer.cancel()
