"""Vision lane, V-61. Publishes perception.occupancy once per vision.interval.

VESTA_MODE picks the people counter: a simulator in replay and sim, the camera
and detector loop in real.

Degraded mode, docs/degraded.md: when the camera or detector is dead, or the
scheduler sheds vision, occupancy comes from the presence sensor with source
presence, occupied per zone as a boolean and every count null. A count is
never estimated from presence.
"""

import asyncio
import random

from services.core import mode
from services.core.logs import iso_utc
from services.core.models import Priority
from services.core.service import Service

ZONES = ("left", "center", "right", "professor")


class CameraDown(RuntimeError):
    pass


class SimulatedCounter:
    def __init__(self, faults=None, seed=None):
        self.faults = faults
        self.rng = random.Random(seed)
        self.frame = 0

    def count(self):
        if self.faults is not None and self.faults.active("camera"):
            raise CameraDown("camera not responding")
        self.frame += 1
        return {zone: self.rng.choice((0, 0, 1, 2, 3)) for zone in ZONES}, self.frame

    def close(self):
        pass


class CameraCounter:
    def __init__(self, cfg):
        from services.device.camera import Camera
        from services.vision.detector import Detector
        from services.vision.occupancy import OccupancyLoop

        camera = Camera(width=cfg["camera_width"], height=cfg["camera_height"], fps=cfg["camera_fps"])
        self.loop = OccupancyLoop(camera, Detector(width=cfg["camera_width"]), cfg["detection_interval"])
        self.loop.start()
        self.frame = 0

    def count(self):
        last = self.loop.last
        if last.source == "none" or last.total is None:
            raise CameraDown("no frame from the camera loop")
        self.frame += 1
        counts = {zone: int(last.zones.get(zone, 0)) for zone in ZONES[:3]}
        counts["professor"] = 1 if last.professor else 0
        return counts, self.frame

    def close(self):
        self.loop.stop()


class VisionService(Service):
    name = "vision"
    component = "vision"

    async def start(self):
        self.counter = mode.pick(
            replay=lambda: SimulatedCounter(self.ctx.faults, seed=0),
            sim=lambda: SimulatedCounter(self.ctx.faults),
            real=lambda: CameraCounter(self.ctx.config),
        )
        self.frames = {"counted": 0, "yielded": 0, "fallback": 0}
        await super().start()
        self.spawn(self.every(self.ctx.config["vision.interval"], self.step), "occupancy")

    async def step(self):
        scheduler = self.ctx.scheduler
        if scheduler is not None and not scheduler.admits("vision"):
            self.publish_presence("shed while over budget")
            return
        if scheduler is not None and scheduler.should_yield(Priority.VISION):
            self.frames["yielded"] += 1
            return
        try:
            counts, frame = await asyncio.to_thread(self.counter.count)
        except CameraDown as e:
            self.degrade("vision_unavailable", str(e))
            self.publish_presence(str(e))
            return
        self.recover("vision_unavailable")
        self.frames["counted"] += 1
        zones = [{"zone": z, "count": counts[z], "occupied": counts[z] > 0, "confidence": 0.9} for z in ZONES]
        self.publish("perception.occupancy", {
            "ts": iso_utc(), "total": sum(counts.values()), "occupied": any(counts.values()), "zones": zones,
            "source": "vision", "confidence": 0.9, "stale": False, "frame_id": frame,
        })

    def publish_presence(self, reason):
        self.frames["fallback"] += 1
        reading = self.state.get("presence") if self.state is not None else None
        present = {}
        if reading is not None and reading.value is not None:
            present = {item.zone.value: item.present for item in reading.value.zones}
        zones = [{"zone": z, "count": None, "occupied": bool(present.get(z, False)), "confidence": 0.6} for z in ZONES]
        self.publish("perception.occupancy", {
            "ts": iso_utc(), "total": None, "occupied": any(z["occupied"] for z in zones), "zones": zones,
            "source": "presence", "confidence": 0.6 if present else 0.0,
            "stale": reading is None or reading.value is None or reading.stale, "frame_id": None,
        })

    async def stop(self):
        await super().stop()
        await asyncio.to_thread(self.counter.close)
