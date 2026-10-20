"""Recorder lane. Keeps every bus envelope for GET /history and debugging."""

from collections import deque

from services.core.service import Service


class RecorderService(Service):
    name = "recorder"
    component = "recorder"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.recent = deque(maxlen=5000)

    async def start(self):
        await super().start()
        self.sub = self.bus.subscribe("#", name="recorder", maxsize=1024)
        self.spawn(self.record(), "record")

    async def record(self):
        async for envelope in self.sub:
            self.recent.append(envelope)
            self.tick()

    async def stop(self):
        self.sub.close()
        await super().stop()

    def health(self):
        health = super().health()
        if health["ok"]:
            health["detail"] = f"{len(self.recent)} envelopes kept, {self.sub.dropped} dropped"
        return health
