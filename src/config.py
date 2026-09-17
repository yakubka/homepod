import json
import os

DEFAULTS = {
    "device_name": "HomePod-1",
    "wake_word": "hey homepod",
    "volume": 70,
    "language": "en",
    "led_brightness": 80,
    "api_port": 5050,
    "music_directory": "/home/pi/music",
    "log_level": "info",
    "sensor_poll_interval": 10,
    "presence_detection": True,
    "camera_width": 640,
    "camera_height": 480,
    "camera_fps": 10,
    "detection_interval": 1.0,
    "light_auto_off_delay": 30,
    "lux_sensor": True,
    "lux_poll_interval": 5,
    "dark_threshold": 50,
}

PATH = os.path.join(os.path.dirname(__file__), "..", "device_config.json")


def load():
    if os.path.exists(PATH):
        with open(PATH) as f:
            return {**DEFAULTS, **json.load(f)}
    return DEFAULTS.copy()


def save(cfg):
    with open(PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def update(key, value):
    if key not in DEFAULTS:
        return False, f"unknown key {key}"
    cfg = load()
    cfg[key] = value
    save(cfg)
    return True, f"{key} set to {value}"
