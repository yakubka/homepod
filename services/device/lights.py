import logging

from services.core.config import REAL

log = logging.getLogger("lights")

RELAYS = {"left": 5, "center": 6, "right": 16, "professor": 26}


class Lights:
    def __init__(self):
        self.zones = {n: 0 for n in RELAYS}
        self.gpio = None

        if REAL:
            import RPi.GPIO as GPIO

            self.gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            for pin in RELAYS.values():
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.HIGH)

    def set(self, zone, brightness):
        if zone not in RELAYS:
            log.warning(f"no zone named {zone}")
            return False
        brightness = max(0, min(100, int(brightness)))
        if self.zones[zone] == brightness:
            return True
        self.zones[zone] = brightness
        # relays are on or off, the 0 to 100 scale is what dimmable bulbs will take unchanged
        if self.gpio:
            self.gpio.output(RELAYS[zone], self.gpio.LOW if brightness else self.gpio.HIGH)
        log.info(f"light {zone} {brightness}")
        return True

    def all(self, brightness):
        for zone in self.zones:
            self.set(zone, brightness)

    def status(self):
        return {"zones": self.zones.copy(), "real": REAL}

    def cleanup(self):
        self.all(0)
