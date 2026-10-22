"""Request and reply over the bus.

    decisions = Replies(self.bus, "decision.action", key=lambda p: p["request_id"], name="api.decisions")
    self.spawn(decisions.run())
    pending = decisions.expect(request_id)        # register before publishing the request
    self.publish("control.request", request)
    decision = await decisions.wait(pending, 3.0)  # asyncio.TimeoutError when nothing came

Replies that arrive before anyone waits for them are kept briefly, so a fast
answer is never lost to a race.
"""

import asyncio
from collections import OrderedDict


class Replies:
    def __init__(self, bus, topic, key, name, keep=128):
        self.sub = bus.subscribe(topic, name=name, maxsize=256)
        self.key = key
        self.keep = keep
        self.pending = {}
        self.recent = OrderedDict()

    async def run(self):
        try:
            async for envelope in self.sub:
                payload = envelope["payload"]
                try:
                    key = self.key(payload)
                except (KeyError, TypeError):
                    continue
                future = self.pending.pop(key, None)
                if future is not None and not future.done():
                    future.set_result(payload)
                elif key is not None:
                    self.recent[key] = payload
                    while len(self.recent) > self.keep:
                        self.recent.popitem(last=False)
        finally:
            self.sub.close()

    def expect(self, key):
        future = asyncio.get_running_loop().create_future()
        if key in self.recent:
            future.set_result(self.recent.pop(key))
        else:
            self.pending[key] = future
        future.key = key
        return future

    async def wait(self, future, timeout):
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(future.key, None)
