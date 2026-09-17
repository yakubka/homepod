import logging
import threading
import time

log = logging.getLogger("servo")

SIM = True
PIN = 13


class Servo:
    def __init__(self, pin=PIN):
        self.angle = 0
        self.pwm = None

        if not SIM:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.OUT)
            self.pwm = GPIO.PWM(pin, 50)
            self.pwm.start(0)

    def set(self, angle):
        self.angle = max(0, min(180, angle))
        if self.pwm:
            self.pwm.ChangeDutyCycle(2.5 + self.angle / 18.0)
            time.sleep(0.3)
            self.pwm.ChangeDutyCycle(0)
        log.debug(f"servo at {self.angle}")

    def move(self, target, step=2, delay=0.02):
        target = max(0, min(180, target))

        def run():
            pos = self.angle
            way = 1 if target > pos else -1
            while abs(pos - target) > step:
                pos += step * way
                self.set(pos)
                time.sleep(delay)
            self.set(target)

        threading.Thread(target=run, daemon=True).start()

    def show_volume(self, level):
        angle = int(level * 1.8)
        self.move(angle)
        return angle

    def stop(self):
        if self.pwm:
            self.pwm.stop()
        log.info("servo stopped")
