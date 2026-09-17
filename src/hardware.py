import logging
import threading
import time

log = logging.getLogger("hardware")

SIM = True

LEDS = {"status": 17, "listening": 27, "error": 22}
DHT_PIN = 4
TOUCH_PIN = 18


class Hardware:
    def __init__(self):
        self.leds = {n: False for n in LEDS}
        self.sensors = {"temperature": 0.0, "humidity": 0.0}
        self.running = False

        if not SIM:
            import RPi.GPIO as GPIO
            self.gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in LEDS.values():
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            GPIO.setup(TOUCH_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    def set_led(self, name, on):
        if name not in LEDS:
            log.warning(f"no led named {name}")
            return False
        self.leds[name] = on
        if not SIM:
            self.gpio.output(LEDS[name], self.gpio.HIGH if on else self.gpio.LOW)
        log.info(f"led {name} {'on' if on else 'off'}")
        return True

    def blink(self, name, times=3, interval=0.3):
        def run():
            for _ in range(times):
                self.set_led(name, True)
                time.sleep(interval)
                self.set_led(name, False)
                time.sleep(interval)

        threading.Thread(target=run, daemon=True).start()

    def read_sensors(self):
        try:
            if SIM:
                import random
                self.sensors["temperature"] = round(20 + random.uniform(0, 8), 1)
                self.sensors["humidity"] = round(40 + random.uniform(0, 30), 1)
            else:
                import Adafruit_DHT
                h, t = Adafruit_DHT.read_retry(Adafruit_DHT.DHT22, DHT_PIN)
                if t is not None:
                    self.sensors["temperature"] = round(t, 1)
                if h is not None:
                    self.sensors["humidity"] = round(h, 1)
        except Exception as e:
            log.error(f"sensor read failed, {e}")
        return self.sensors.copy()

    def poll_sensors(self, interval=10):
        self.running = True

        def run():
            while self.running:
                self.read_sensors()
                time.sleep(interval)

        threading.Thread(target=run, daemon=True).start()
        log.info(f"sensor polling every {interval}s")

    def touched(self):
        if SIM:
            return False
        return self.gpio.input(TOUCH_PIN) == self.gpio.HIGH

    def cleanup(self):
        self.running = False
        if not SIM:
            self.gpio.cleanup()
        log.info("gpio released")

    def status(self):
        return {"leds": self.leds.copy(), "sensors": self.sensors.copy(), "sim": SIM}
