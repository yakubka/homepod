import logging
import threading
import time

log = logging.getLogger("presence")


class Presence:
    def __init__(self, camera, detector, lights, display=None, interval=1.0):
        self.camera = camera
        self.detector = detector
        self.lights = lights
        self.display = display
        self.interval = interval

        self.running = False
        self.present = False
        self.zone = None
        self.hits = 0
        self.empty_since = None

    def start(self):
        if not self.camera.start():
            log.error("presence not started, camera failed")
            return False

        self.running = True

        def run():
            while self.running:
                try:
                    found = self.detector.detect(self.camera.read())
                    was, prev = self.present, self.zone
                    self.present = bool(found)
                    self.zone = self.detector.zone

                    if self.present:
                        self.hits += 1
                        self.empty_since = None
                    elif was:
                        self.empty_since = time.time()

                    self.lights.update(self.zone)

                    if was != self.present:
                        if self.present:
                            log.info("person entered")
                            if self.display:
                                self.display.show("Presence", "Person detected", f"Zone {self.zone}")
                        else:
                            log.info("room empty")
                            if self.display:
                                self.display.show("Presence", "Room empty", "Lights will dim")
                    elif prev != self.zone and self.zone:
                        log.info(f"person moved to {self.zone}")
                except Exception as e:
                    log.error(f"presence loop failed, {e}")
                time.sleep(self.interval)

        threading.Thread(target=run, daemon=True).start()
        log.info("presence started")
        return True

    def stop(self):
        self.running = False
        self.camera.stop()
        log.info("presence stopped")

    def status(self):
        return {
            "present": self.present,
            "zone": self.zone,
            "camera": self.camera.running,
            "hits": self.hits,
            "empty_for": int(time.time() - self.empty_since) if self.empty_since else None,
            "detector": self.detector.status(),
            "lights": self.lights.status(),
        }
