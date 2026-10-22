"""Starts services in declared order and stops them in reverse."""

import asyncio
import importlib
import logging
import os
import threading

log = logging.getLogger("core")


class StartupError(RuntimeError):
    pass


def load(specs):
    classes = []
    for spec in specs:
        module, _, name = spec.partition(":")
        classes.append(getattr(importlib.import_module(module), name))
    return classes


class Supervisor:
    def __init__(self, classes, ctx, start_timeout=5.0, stop_timeout=5.0):
        self.classes = classes
        self.ctx = ctx
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.running = []

    async def start(self):
        for cls in self.classes:
            service = cls(self.ctx)
            try:
                await asyncio.wait_for(service.start(), self.start_timeout)
            except Exception as e:
                reason = "timed out" if isinstance(e, asyncio.TimeoutError) else repr(e)
                log.error("service failed to start", extra={"service": service.name, "reason": reason})
                await self.stop()
                raise StartupError(f"{service.name} failed to start: {reason}") from None
            self.running.append(service)
            health = service.health()
            log.info(
                "service healthy" if health["ok"] else "service unhealthy",
                extra={"service": service.name, **health},
            )

    async def stop(self):
        while self.running:
            service = self.running.pop()
            try:
                await asyncio.wait_for(service.stop(), self.stop_timeout)
                log.info("service stopped", extra={"service": service.name})
            except asyncio.TimeoutError:
                log.error("service did not stop in time", extra={"service": service.name, "timeout_s": self.stop_timeout})
            except Exception as e:
                log.error("service failed to stop", extra={"service": service.name, "reason": repr(e)})

    def health(self):
        return {service.name: service.health() for service in self.running}


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
