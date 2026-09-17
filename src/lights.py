import logging
import time

log = logging.getLogger("lights")

SIM = True

RELAYS = {"left": 5, "center": 6, "right": 16}
AUTO_OFF = 30


class Lights:
    def __init__(self, auto_off=AUTO_OFF):
        self.zones = {n: False for n in RELAYS}
        self.auto_off = auto_off
        self.seen = {n: 0.0 for n in RELAYS}
        self.manual = {n: False for n in RELAYS}
        self.follow = True

        if not SIM:
            import RPi.GPIO as GPIO
            self.gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            for pin in RELAYS.values():
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.HIGH)

    def set(self, zone, on, manual=False):
        if zone not in RELAYS:
            log.warning(f"no zone named {zone}")
            return False
        if manual:
            self.manual[zone] = True
        if self.zones[zone] == on:
            return True
        self.zones[zone] = on
        if not SIM:
            self.gpio.output(RELAYS[zone], self.gpio.LOW if on else self.gpio.HIGH)
        log.info(f"light {zone} {'on' if on else 'off'}")
        return True

    def all(self, on):
        for zone in self.zones:
            self.set(zone, on, manual=True)

    def set_follow(self, on):
        self.follow = on
        if on:
            self.manual = {n: False for n in RELAYS}
        log.info(f"follow mode {'on' if on else 'off'}")
        return self.follow

    def update(self, zone):
        if not self.follow:
            return
        now = time.time()

        if zone in self.zones:
            self.seen[zone] = now
            if not self.manual[zone]:
                self.set(zone, True)

        for other in self.zones:
            if other == zone or self.manual[other] or not self.zones[other]:
                continue
            idle = now - self.seen[other]
            if idle > self.auto_off:
                self.set(other, False)
                log.info(f"zone {other} idle {int(idle)}s, switched off")

    def status(self):
        now = time.time()
        return {
            "zones": self.zones.copy(),
            "follow": self.follow,
            "manual": self.manual.copy(),
            "idle": {z: int(now - t) if t else None for z, t in self.seen.items()},
            "auto_off": self.auto_off,
        }

    def cleanup(self):
        self.all(False)
