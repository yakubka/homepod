import json
import os

MODE = os.environ.get("VESTA_MODE", "replay")
REAL = MODE == "real"

DEFAULTS = {
    "device_name": "VESTA-1",
    "api_port": 5050,
    "log_level": "info",
    "tick_interval": 1.0,
    "sensor_poll_interval": 10,
    "lux_poll_interval": 5,
    "camera_width": 640,
    "camera_height": 480,
    "camera_fps": 10,
    "detection_interval": 1.0,
    "occupancy.window": 10.0,
    "occupancy.min_confidence": 0.4,
    "light.hold": 30.0,
    "light.full_at": 12,
    "light.base": 40,
    "light.daylight_on": 250,
    "light.daylight_off": 400,
    "light.preparing": 60,
    "light.idle": 0,
    "light.presentation_dim": 0,
    "light.presentation_other": 25,
    "presentation.zones": ["center"],
    "professor.zone": "professor",
    "override.ttl": 600.0,
    "climate.enabled": False,
    "climate.temp_target": 23.0,
    "climate.temp_band": 2.0,
    "climate.humidity_target": 50,
    "climate.humidity_band": 15,
    "schedule.lead": 900.0,
    "schedule.overrun": 600.0,
    "announce.after": 300.0,
    "announce.confirm": 60.0,
}

PATH = os.path.join(os.path.dirname(__file__), "..", "..", "device_config.json")


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
    if not isinstance(value, type(DEFAULTS[key])) and not (
        isinstance(DEFAULTS[key], float) and isinstance(value, int)
    ):
        return False, f"{key} expects {type(DEFAULTS[key]).__name__}"
    cfg = load()
    cfg[key] = value
    save(cfg)
    return True, f"{key} set to {value}"
