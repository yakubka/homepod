"""Device lane, published as the bridge component.

Owns the link to the room hardware: sensor reports out, device commands in.
VESTA_MODE picks the link once: a seeded simulator in replay, a free running
simulator in sim, the board drivers in real, imported only inside BoardLink.

Degraded mode, docs/degraded.md: when the link drops, device.status goes down
at once, the state store marks the last readings stale, every device.command is
answered with a synthetic link_down rejection, and the link is retried every
device.retry_interval until it is back.
"""

import asyncio
import random
import time

from services.core import contract, mode
from services.core.logs import iso_utc
from services.core.service import Service

ZONES = ("left", "center", "right", "professor")


class LinkDown(RuntimeError):
    pass


class SimulatedLink:
    def __init__(self, faults=None, seed=None):
        self.faults = faults
        self.rng = random.Random(seed)
        self.lights = dict.fromkeys(ZONES, 0)
        self.fan = 0
        self.ring = "idle"

    def check(self):
        if self.faults is not None and self.faults.active("esp32"):
            raise LinkDown("serial port closed")

    def read(self):
        self.check()
        environment = {
            "temperature_c": round(21.0 + self.rng.uniform(0.0, 3.0), 1),
            "humidity_pct": self.rng.randint(38, 48),
            "lux": self.rng.randint(120, 420),
        }
        return environment, {zone: self.rng.random() < 0.4 for zone in ZONES}

    def apply(self, command):
        self.check()
        if command["type"] == "set_zone_light":
            self.lights[command["zone"]] = command["brightness"]
        elif command["type"] == "set_fan":
            self.fan = command["speed"]
        elif command["type"] == "set_ring":
            self.ring = command["pattern"]
        return self.actuators()

    def actuators(self):
        return {"lights": dict(self.lights), "fan": self.fan, "ring": self.ring}

    def close(self):
        pass


class BoardLink:
    def __init__(self):
        from services.device.hardware import Hardware
        from services.device.light_sensor import LightSensor
        from services.device.lights import Lights

        self.hw = Hardware()
        self.light = LightSensor()
        self.relays = Lights()
        self.fan = 0
        self.ring = "idle"

    def read(self):
        values = self.hw.read_sensors()
        environment = {
            "temperature_c": round(values["temperature"], 1),
            "humidity_pct": int(round(values["humidity"])),
            "lux": int(round(self.light.read())),
        }
        return environment, {}

    def apply(self, command):
        if command["type"] == "set_zone_light":
            self.relays.set(command["zone"], command["brightness"])
        elif command["type"] == "set_fan":
            self.fan = command["speed"]
        elif command["type"] == "set_ring":
            self.ring = command["pattern"]
        return self.actuators()

    def actuators(self):
        lights = {zone: int(self.relays.zones.get(zone, 0)) for zone in ZONES}
        return {"lights": lights, "fan": self.fan, "ring": self.ring}

    def close(self):
        self.light.stop()
        self.relays.cleanup()
        self.hw.cleanup()


class DeviceService(Service):
    name = "device"
    component = "bridge"

    async def start(self):
        self.link = mode.pick(
            replay=lambda: SimulatedLink(self.ctx.faults, seed=0),
            sim=lambda: SimulatedLink(self.ctx.faults),
            real=BoardLink,
        )
        self.up = None
        self.reason = None
        self.seq = 0
        self.last_seen = None
        self.status_at = 0.0
        await super().start()
        self.commands = self.bus.subscribe("device.command", name="device.commands", maxsize=64)
        self.spawn(self.reports(), "reports")
        self.spawn(self.execute(), "commands")

    async def reports(self):
        cfg = self.ctx.config
        while True:
            try:
                environment, presence = await asyncio.to_thread(self.link.read)
            except LinkDown as e:
                self.link_state(False, str(e))
                self.tick()
                await asyncio.sleep(cfg["device.retry_interval"])
                continue
            self.last_seen = iso_utc()
            self.link_state(True)
            device_id = cfg["device.id"]
            self.publish("sensor.environment", {"ts": self.last_seen, "device_id": device_id, **environment})
            if presence:
                zones = [{"zone": zone, "present": present} for zone, present in presence.items()]
                self.publish("sensor.presence", {"ts": self.last_seen, "device_id": device_id, "zones": zones})
            self.tick()
            await asyncio.sleep(cfg["device.report_interval"])

    def link_state(self, up, reason=None):
        changed = up != self.up
        self.up, self.reason = up, reason
        if changed:
            if up:
                self.recover("device_link_lost")
            else:
                self.degrade("device_link_lost", reason)
        if changed or time.monotonic() - self.status_at >= 5.0:
            self.status_at = time.monotonic()
            self.publish("device.status", {
                "device_id": self.ctx.config["device.id"],
                "status": "ok" if up else "down",
                "reason": None if up else "port_closed",
                "fw_version": None,
                "protocol_version": contract.version(),
                "uptime_ms": None,
                "last_seen_at": self.last_seen,
                "last_error": None,
            })

    async def execute(self):
        async for envelope in self.commands:
            command = envelope["payload"]
            try:
                state = await asyncio.to_thread(self.link.apply, command)
                self.seq += 1
                ack = {"type": "ack", "seq": self.seq, "ts": iso_utc(), "cmd_id": command["cmd_id"],
                       "status": "applied", "reason": None, "state": state}
            except LinkDown as e:
                self.link_state(False, str(e))
                ack = {"type": "ack", "ts": iso_utc(), "cmd_id": command["cmd_id"],
                       "status": "rejected", "reason": "link_down", "state": None}
            self.publish("device.ack", ack, envelope["correlation_id"])

    async def stop(self):
        self.commands.close()
        await super().stop()
        await asyncio.to_thread(self.link.close)
