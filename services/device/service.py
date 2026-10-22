"""Device lane. Publishes sensor.environment as the bridge component.

The sensor source is chosen once by VESTA_MODE: a seeded simulator in replay so
runs are reproducible, a free running simulator in sim, and the board drivers
in real. The drivers are imported only inside the real factory.
"""

import asyncio
import random

from services.core import mode
from services.core.logs import iso_utc
from services.core.service import Service


class SimulatedSensors:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def read(self):
        return {
            "temperature_c": round(21.0 + self.rng.uniform(0.0, 3.0), 1),
            "humidity_pct": self.rng.randint(38, 48),
            "lux": self.rng.randint(120, 420),
        }

    def close(self):
        pass


class BoardSensors:
    def __init__(self):
        from services.device.hardware import Hardware
        from services.device.light_sensor import LightSensor

        self.hw = Hardware()
        self.light = LightSensor()

    def read(self):
        values = self.hw.read_sensors()
        return {
            "temperature_c": round(values["temperature"], 1),
            "humidity_pct": int(round(values["humidity"])),
            "lux": int(round(self.light.read())),
        }

    def close(self):
        self.light.stop()
        self.hw.cleanup()


class DeviceService(Service):
    name = "device"
    component = "bridge"

    async def start(self):
        self.sensors = mode.pick(
            replay=lambda: SimulatedSensors(seed=0),
            sim=SimulatedSensors,
            real=BoardSensors,
        )
        await super().start()
        self.spawn(self.every(self.ctx.config["device.report_interval"], self.report), "report")

    async def report(self):
        values = await asyncio.get_running_loop().run_in_executor(None, self.sensors.read)
        self.publish("sensor.environment", {"ts": iso_utc(), "device_id": self.ctx.config["device.id"], **values})

    async def stop(self):
        await super().stop()
        self.sensors.close()
