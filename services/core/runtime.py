import logging
import threading
import time
from collections import deque

from services.core import config
from services.core.engine import (
    DecisionLog,
    Environment,
    Occupancy,
    Override,
    RoomState,
    decide,
    fuse,
    room_state,
)

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, lights, occupancy=None, lux=None, hw=None, announcer=None, dry=False):
        self.lights = lights
        self.occupancy = occupancy
        self.lux = lux
        self.hw = hw
        self.announcer = announcer
        self.dry = dry

        self.decisions = DecisionLog()
        self.window = deque(maxlen=60)
        self.changed = {}
        self.overrides = {}
        self.lecture = None
        self.presentation = False
        self.climate_approved = False
        self.announced = []
        self.absent_since = None
        self.room = "idle"
        self.running = False

    def state(self, cfg, now=None):
        now = now or time.time()
        cam = self.occupancy.last if self.occupancy else None
        radar = self.hw.presence() if self.hw and hasattr(self.hw, "presence") else None
        in_lecture = self.room in ("preparing", "in_session", "overrun")
        occ = fuse(cam, radar, in_lecture, cfg)

        if occ.professor is False and self.absent_since is None:
            self.absent_since = now
        elif occ.professor:
            self.absent_since = None

        env = Environment(at=now)
        if self.hw:
            s = self.hw.read_sensors()
            env.temperature = s.get("temperature")
            env.humidity = s.get("humidity")
        if self.lux:
            env.lux = int(self.lux.lux)

        return RoomState(
            now=now,
            occupancy=occ,
            environment=env,
            lecture=self.lecture,
            presentation=self.presentation,
            climate_approved=self.climate_approved,
            overrides=dict(self.overrides),
            lights=dict(self.lights.zones),
            changed=dict(self.changed),
            window=list(self.window),
            announced=list(self.announced),
            absent_since=self.absent_since,
        )

    def tick(self, cfg=None, now=None):
        cfg = cfg or config.load()
        now = now or time.time()
        room = self.state(cfg, now)
        if room.occupancy.total is not None:
            self.window.append(room.occupancy)
        self.room = room_state(room, cfg)

        res = decide(room, cfg, now)
        self.decisions.add(res.decisions)
        self.recommendations = res.recommendations

        if not self.dry:
            self.apply(res.actions, now)
        return res

    def apply(self, actions, now):
        for a in actions:
            if a.kind == "zone_light":
                if self.lights.set(a.target, a.value):
                    self.changed[a.target] = now
            elif a.kind == "announce":
                self.announced.append(a.value)
                if self.announcer:
                    self.announcer(a.target, a.inputs)
                else:
                    log.info(f"announcement {a.target} has no audio lane yet")
            elif a.kind == "climate":
                log.info(f"climate command {a.value}, no air conditioner interface yet")

    def override(self, zone, brightness, source="app"):
        self.overrides[zone] = Override(int(brightness), source, time.time())

    def auto(self, zone=None):
        if zone is None:
            self.overrides.clear()
        else:
            self.overrides.pop(zone, None)

    def start(self):
        self.running = True

        def run():
            while self.running:
                cfg = config.load()
                try:
                    self.tick(cfg)
                except Exception as e:
                    log.error(f"tick failed, {e}")
                time.sleep(cfg["tick_interval"])

        threading.Thread(target=run, daemon=True).start()
        log.info("engine running")

    def stop(self):
        self.running = False

    def status(self):
        return {
            "room": self.room,
            "presentation": self.presentation,
            "lecture": self.lecture.name if self.lecture else None,
            "overrides": {z: o.source for z, o in self.overrides.items()},
            "lights": self.lights.status(),
            "occupancy": self.occupancy.status() if self.occupancy else None,
            "last": self.decisions.query(limit=1),
        }
