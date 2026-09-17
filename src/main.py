#!/usr/bin/env python3
import logging
import signal
import sys
import time

import config
import server
from audio import Player
from camera import Camera
from commands import Commands
from detector import Detector
from display import Display
from hardware import Hardware
from led_ring import Ring
from lights import Lights
from presence import Presence
from servo import Servo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def main():
    cfg = config.load()
    log.info(f"starting {cfg['device_name']}")

    hw = Hardware()
    hw.set_led("status", True)
    hw.poll_sensors(cfg["sensor_poll_interval"])

    player = Player(cfg["music_directory"])
    player.set_volume(cfg["volume"])

    display = Display()
    display.show("HomePod", "Booting")

    ring = Ring()
    ring.thinking()

    servo = Servo()
    servo.show_volume(cfg["volume"])

    camera = Camera(width=cfg["camera_width"], height=cfg["camera_height"], fps=cfg["camera_fps"])
    detector = Detector(width=cfg["camera_width"])
    lights = Lights(auto_off=cfg["light_auto_off_delay"])
    presence = Presence(camera, detector, lights, display, cfg["detection_interval"])

    commands = Commands(hw, player, config, display, ring, servo, lights, presence)
    api = server.start(commands, hw, player, config, cfg["api_port"], lights, presence)

    if cfg["presence_detection"]:
        presence.start()

    display.auto(hw, player, cfg["device_name"])
    ring.off()

    def shutdown(sig, frame):
        log.info("shutting down")
        presence.stop()
        lights.cleanup()
        display.stop()
        ring.off()
        servo.stop()
        player.stop()
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
