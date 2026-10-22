"""In-process asynchronous event bus.

Topics are the dotted names from contracts/bus.md. A subscription pattern may
use `*` for exactly one segment and `#` for any number of trailing segments:
`sensor.*`, `device.#`, `#`.

Every subscriber has its own bounded queue. publish() never waits: when a queue
is full the oldest envelope in it is dropped and the subscriber's `dropped`
counter goes up, so one slow consumer can never stall the room.

The latest envelope of every topic is kept. State topics are replayed to a new
subscriber at once, so a late starter sees the current state without waiting
for the next tick. Event topics such as voice.intent or device.command are not
replayed, a late subscriber must never act on an old command.
"""

import asyncio
import logging

from services.core import config, contract
from services.core.logs import iso_utc, new_id, trace_id

log = logging.getLogger("bus")

STATE_TOPICS = frozenset(
    {
        "sensor.environment",
        "sensor.presence",
        "perception.occupancy",
        "device.status",
        "settings.changed",
        "system.heartbeat",
        "system.state",
    }
)

_CLOSED = object()


def matches(pattern, topic):
    want, have = pattern.split("."), topic.split(".")
    for index, part in enumerate(want):
        if part == "#":
            return True
        if index >= len(have) or (part != "*" and part != have[index]):
            return False
    return len(want) == len(have)


class Subscription:
    def __init__(self, bus, pattern, name, maxsize):
        self.bus = bus
        self.pattern = pattern
        self.name = name
        self.queue = asyncio.Queue(maxsize)
        self.delivered = 0
        self.dropped = 0
        self.closed = False

    def offer(self, envelope):
        if self.closed:
            return
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(envelope)
        self.delivered += 1

    async def get(self):
        envelope = await self.queue.get()
        if envelope is _CLOSED:
            raise StopAsyncIteration
        trace_id.set(envelope["correlation_id"])
        return envelope

    def get_nowait(self):
        envelope = self.queue.get_nowait()
        if envelope is _CLOSED:
            raise asyncio.QueueEmpty
        return envelope

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.get()

    @property
    def pending(self):
        return self.queue.qsize()

    def close(self):
        self.bus.unsubscribe(self)

    def stats(self):
        return {"name": self.name, "pattern": self.pattern, "pending": self.pending, "delivered": self.delivered, "dropped": self.dropped}


class Bus:
    def __init__(self, strict=None, queue_size=None):
        self.strict = config.STRICT if strict is None else strict
        self.queue_size = queue_size or config.load()["bus.queue_size"]
        self.subscriptions = []
        self.last = {}
        self.published = 0
        self.violations = 0
        self.loop = None

    def attach(self, loop):
        self.loop = loop

    def subscribe(self, pattern, name=None, maxsize=None):
        sub = Subscription(self, pattern, name or pattern, maxsize or self.queue_size)
        self.subscriptions.append(sub)
        for topic, envelope in self.last.items():
            if topic in STATE_TOPICS and matches(pattern, topic):
                sub.offer(envelope)
        return sub

    def unsubscribe(self, sub):
        if sub in self.subscriptions:
            self.subscriptions.remove(sub)
        if not sub.closed:
            if sub.queue.full():
                sub.queue.get_nowait()
                sub.dropped += 1
            sub.queue.put_nowait(_CLOSED)
            sub.closed = True

    def last_value(self, topic):
        return self.last.get(topic)

    def publish(self, topic, payload, source, correlation_id=None):
        envelope = {
            "id": new_id("evt"),
            "topic": topic,
            "ts": iso_utc(),
            "source": source,
            "contract_version": contract.version(),
            "correlation_id": correlation_id if correlation_id is not None else trace_id.get(),
            "payload": payload,
        }
        errors = contract.envelope_errors(envelope)
        if errors:
            self.violations += 1
            if self.strict:
                raise contract.ContractViolation(topic, errors)
            log.warning("contract violation", extra={"topic": topic, "source": source, "errors": errors[:5]})
        self.last[topic] = envelope
        self.published += 1
        for sub in list(self.subscriptions):
            if matches(sub.pattern, topic):
                sub.offer(envelope)
        return envelope

    def publish_threadsafe(self, topic, payload, source, correlation_id=None):
        current = trace_id.get() if correlation_id is None else correlation_id
        self.loop.call_soon_threadsafe(self.publish, topic, payload, source, current)

    def stats(self):
        return {
            "published": self.published,
            "violations": self.violations,
            "subscribers": [sub.stats() for sub in self.subscriptions],
        }
