"""Runtime guard.

Watches the process resident memory against models.memory_ceiling_mb and every
inference against guard.p95_factor times the declared p95 of its model. A
breach is turned into a system.degraded event, after evicting idle on-demand
models for a memory breach, so services can drop to their fallback. The guard
never raises, degradation is a decision and not a crash.
"""

import logging
import time

from services.core import config
from services.core.logs import iso_utc

log = logging.getLogger("guard")


def rss_mb():
    import psutil

    return psutil.Process().memory_info().rss / (1024 * 1024)


class RuntimeGuard:
    def __init__(self, registry, publish, ceiling_mb=None, factor=None, cooldown=None, rss=rss_mb, clock=time.monotonic):
        cfg = config.load()
        self.registry = registry
        self.publish = publish
        self.ceiling_mb = ceiling_mb or cfg["models.memory_ceiling_mb"]
        self.factor = factor or cfg["guard.p95_factor"]
        self.cooldown = cfg["guard.cooldown"] if cooldown is None else cooldown
        self.rss = rss
        self.clock = clock
        self.last = {}
        self.events = []
        self.level = 0
        self.last_breach = None
        self.on_breach = None
        self.on_relax = None

    def observe(self, spec, elapsed_ms):
        limit = spec.p95_ms * self.factor
        if elapsed_ms > limit:
            self._escalate()
            self._degrade("slow_inference", spec.id, elapsed_ms, limit, "ms", "fallback", [])

    def check_memory(self):
        measured = self.rss()
        if measured <= self.ceiling_mb:
            if self.level and self.clock() - self.last_breach > self.cooldown:
                self.level = 0
                self._call(self.on_relax)
            return None
        evicted = []
        try:
            evicted = self.registry.evict_idle()
        except Exception:
            log.exception("eviction during a memory breach failed")
        self._escalate()
        return self._degrade("memory_ceiling", None, measured, self.ceiling_mb, "mb",
                             "evicted" if evicted else "fallback", evicted)

    def _escalate(self):
        self.last_breach = self.clock()
        self.level += 1
        self._call(self.on_breach, self.level)

    def _call(self, hook, *args):
        if hook is None:
            return
        try:
            hook(*args)
        except Exception:
            log.exception("guard hook failed")

    def _degrade(self, reason, model_id, measured, limit, unit, action, evicted):
        key = (reason, model_id)
        now = self.clock()
        if key in self.last and now - self.last[key] < self.cooldown:
            return None
        self.last[key] = now
        payload = {
            "ts": iso_utc(),
            "reason": reason,
            "model_id": model_id,
            "measured": round(float(measured), 1),
            "limit": round(float(limit), 1),
            "unit": unit,
            "action": action,
            "evicted": evicted,
        }
        self.events.append(payload)
        log.warning("system degraded", extra=payload)
        try:
            self.publish("system.degraded", payload)
        except Exception:
            log.exception("could not publish system.degraded")
        return payload
