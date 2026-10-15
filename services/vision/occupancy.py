import logging
import threading
import time

from services.core.engine import Occupancy

log = logging.getLogger("occupancy")


class OccupancyLoop:
    def __init__(self, camera, detector, interval=1.0, professor_of=None):
        self.camera = camera
        self.detector = detector
        self.interval = interval
        # professor zone geometry is V-62 calibration, until then presence stays unknown
        self.professor_of = professor_of
        self.running = False
        self.last = Occupancy(source="none")

    def snapshot(self):
        found = self.detector.detect(self.camera.read())
        zones = {z: 0 for z in ("left", "center", "right")}
        for p in found:
            if p["zone"] in zones:
                zones[p["zone"]] += 1
        conf = min(p["conf"] for p in found) if found else 1.0
        return Occupancy(
            total=len(found),
            zones=zones,
            professor=self.professor_of(found) if self.professor_of else None,
            source="camera",
            confidence=conf,
            at=time.time(),
        )

    def start(self):
        if not self.camera.start():
            log.error("occupancy not started, camera failed")
            return False
        self.running = True

        def run():
            while self.running:
                try:
                    self.last = self.snapshot()
                except Exception as e:
                    log.error(f"occupancy loop failed, {e}")
                    self.last = Occupancy(source="none")
                time.sleep(self.interval)

        threading.Thread(target=run, daemon=True).start()
        log.info("occupancy started")
        return True

    def stop(self):
        self.running = False
        self.camera.stop()
        log.info("occupancy stopped")

    def status(self):
        o = self.last
        return {
            "total": o.total,
            "zones": o.zones,
            "professor": o.professor,
            "source": o.source,
            "confidence": round(o.confidence, 2),
            "age": round(time.time() - o.at, 1) if o.at else None,
            "camera": self.camera.running,
        }
