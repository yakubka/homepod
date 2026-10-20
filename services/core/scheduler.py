"""Priority scheduler for heavy inference.

Heavy inference takes one of a few global permits, granted to the waiting
caller with the best priority class first: voice before vision before
background. Realtime models never come here.

A voice burst, for example one utterance going through speech to text, is
announced with burst(). While any burst of a better class is open, lower
classes see should_yield() and skip their next frame instead of competing:

    with scheduler.burst(Priority.VOICE):
        text = registry.infer("stt", audio)

    # vision loop
    if scheduler.should_yield(Priority.VISION):
        skipped += 1
        continue

The yield is cooperative, nothing is interrupted mid inference.

Over budget the scheduler sheds work in SHED_ORDER, vision first, then general
questions. The announcement path is never in the list and is never shed.
"""

import heapq
import itertools
import logging
import threading
from collections import Counter
from contextlib import contextmanager

log = logging.getLogger("scheduler")

SHED_ORDER = ("vision", "general")
PURPOSE = {2: "vision"}


class Shed(RuntimeError):
    def __init__(self, purpose):
        self.purpose = purpose
        super().__init__(f"{purpose} work is shed while the room is over budget")


class Scheduler:
    def __init__(self, permits=1):
        self.permits = permits
        self.free = permits
        self.cond = threading.Condition()
        self.waiting = []
        self.counter = itertools.count()
        self.bursts = Counter()
        self.granted = Counter()
        self.shed_level = 0

    def shed(self, level):
        level = max(0, min(level, len(SHED_ORDER)))
        if level != self.shed_level:
            log.warning("shedding changed", extra={"level": level, "shed": list(SHED_ORDER[:level])})
        self.shed_level = level

    def admits(self, purpose):
        return purpose not in SHED_ORDER[:self.shed_level]

    @contextmanager
    def slot(self, priority):
        ticket = (int(priority), next(self.counter))
        with self.cond:
            heapq.heappush(self.waiting, ticket)
            while self.free == 0 or self.waiting[0] != ticket:
                self.cond.wait()
            heapq.heappop(self.waiting)
            self.free -= 1
            self.granted[int(priority)] += 1
        try:
            yield
        finally:
            with self.cond:
                self.free += 1
                self.cond.notify_all()

    def run(self, priority, fn, *args, purpose=None):
        purpose = purpose or PURPOSE.get(int(priority))
        if purpose and not self.admits(purpose):
            raise Shed(purpose)
        with self.slot(priority):
            return fn(*args)

    @contextmanager
    def burst(self, priority):
        with self.cond:
            self.bursts[int(priority)] += 1
        try:
            yield
        finally:
            with self.cond:
                self.bursts[int(priority)] -= 1
                self.cond.notify_all()

    def should_yield(self, priority):
        with self.cond:
            if any(count > 0 for level, count in self.bursts.items() if level < int(priority)):
                return True
            return any(level < int(priority) for level, _ in self.waiting)

    def wait_clear(self, priority, timeout=None):
        """Block until no better class holds a burst or waits for a permit."""
        with self.cond:
            return self.cond.wait_for(
                lambda: not any(c > 0 for level, c in self.bursts.items() if level < int(priority))
                and not any(level < int(priority) for level, _ in self.waiting),
                timeout,
            )
