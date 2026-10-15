#!/usr/bin/env python3
import logging
import signal
import sys
import time

from services.api import server
from services.core import config
from services.core.runtime import Runtime
from services.device.camera import Camera
from services.device.display import Display
from services.device.hardware import Hardware
from services.device.led_ring import Ring
from services.device.light_sensor import LightSensor
from services.device.lights import Lights
from services.vision.detector import Detector
from services.vision.occupancy import OccupancyLoop
from services.voice.commands import Commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def main():
    cfg = config.load()
    log.info(f"starting {cfg['device_name']} in {config.MODE} mode")

    hw = Hardware()
    hw.set_led("status", True)
    hw.poll_sensors(cfg["sensor_poll_interval"])

    display = Display()
    display.show("VESTA", "Booting")

    ring = Ring()
    ring.thinking()

    lux = LightSensor()
    lux.poll(cfg["lux_poll_interval"])

    camera = Camera(width=cfg["camera_width"], height=cfg["camera_height"], fps=cfg["camera_fps"])
    detector = Detector(width=cfg["camera_width"])
    occupancy = OccupancyLoop(camera, detector, cfg["detection_interval"])
    lights = Lights()

    runtime = Runtime(lights, occupancy, lux, hw)
    commands = Commands(hw, runtime, config, display, ring, lux)
    api = server.start(commands, hw, runtime, config, cfg["api_port"], lux)

    occupancy.start()
    runtime.start()
    ring.off()

    def shutdown(sig, frame):
        log.info("shutting down")
        runtime.stop()
        occupancy.stop()
        lights.cleanup()
        lux.stop()
        display.stop()
        ring.off()
        hw.set_led("status", False)
        hw.cleanup()
        api.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    hw.blink("status", times=3)
    log.info("ready")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
