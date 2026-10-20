"""Layered configuration.

Later layers win:
  1. DEFAULTS in this file
  2. config/default.yaml, committed
  3. config/local.yaml, gitignored, per machine
  4. device_config.json, written at runtime by the settings API
  5. environment variables, VESTA_ plus the key in upper case with dots as
     underscores, so light.hold is VESTA_LIGHT_HOLD

YAML files may nest keys, `light: {hold: 30}` is the same as `light.hold: 30`.
VESTA_MODE picks replay, sim or real and is the only switch that decides which
implementation of a source is built, see services.core.mode.
"""

import json
import logging
import os

import yaml

MODES = ("replay", "sim", "real")
MODE = os.environ.get("VESTA_MODE", "replay")
if MODE not in MODES:
    raise ValueError(f"VESTA_MODE must be one of {', '.join(MODES)}, got {MODE!r}")
REAL = MODE == "real"
STRICT = os.environ.get("VESTA_STRICT", "0") == "1"

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
    "log.format": "json",
    "bus.queue_size": 256,
    "heartbeat.interval": 5.0,
    "service.start_timeout": 5.0,
    "service.stop_timeout": 5.0,
    "shutdown.timeout": 10.0,
    "device.id": "esp32-000000",
    "device.report_interval": 2.0,
    "state.stale.environment": 10.0,
    "state.stale.presence": 10.0,
    "state.stale.occupancy": 5.0,
    "state.stale.device": 15.0,
    "state.stale.lecture": 86400.0,
    "state.stale.settings": 86400.0,
    "state.stale.system": 15.0,
    "state.stale.decision": 3600.0,
    "state.stale.heartbeat": 15.0,
    "models.dir": "models",
    "models.memory_ceiling_mb": 1200,
    "models.base_mb": 250,
    "models.cores": 4,
    "models.heavy_permits": 1,
    "guard.interval": 5.0,
    "guard.p95_factor": 3.0,
    "guard.cooldown": 30.0,
}

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_FILE = os.path.join(ROOT, "config", "default.yaml")
LOCAL_FILE = os.path.join(ROOT, "config", "local.yaml")
PATH = os.path.join(ROOT, "device_config.json")

log = logging.getLogger("config")
_cache = {}
_warned = set()


class ConfigError(ValueError):
    pass


def env_name(key):
    return "VESTA_" + key.upper().replace(".", "_")


def _flatten(data, prefix=""):
    out = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{name}."))
        else:
            out[name] = value
    return out


def _read(path, parse):
    try:
        stamp = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return {}
    cached = _cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    with open(path, encoding="utf-8") as f:
        data = parse(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must hold a mapping")
    data = _flatten(data)
    _cache[path] = (stamp, data)
    return data


def _environment():
    out = {}
    for key in DEFAULTS:
        raw = os.environ.get(env_name(key))
        if raw is not None:
            try:
                out[key] = yaml.safe_load(raw)
            except yaml.YAMLError:
                out[key] = raw
    return out


def _check(key, value, source):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, float):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        value = float(value) if ok else value
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(default))
    if not ok:
        raise ConfigError(f"{key} from {source} expects {type(default).__name__}, got {value!r}")
    return value


def layers():
    return [
        (DEFAULT_FILE, _read(DEFAULT_FILE, yaml.safe_load)),
        (LOCAL_FILE, _read(LOCAL_FILE, yaml.safe_load)),
        (PATH, _read(PATH, json.load)),
        ("environment", _environment()),
    ]


def load():
    cfg = DEFAULTS.copy()
    for source, values in layers():
        for key, value in values.items():
            if key not in DEFAULTS:
                if (source, key) not in _warned:
                    _warned.add((source, key))
                    log.warning(f"unknown config key {key} in {source}, ignored")
                continue
            cfg[key] = _check(key, value, source)
    return cfg


def save(cfg):
    with open(PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def update(key, value):
    if key not in DEFAULTS:
        return False, f"unknown key {key}"
    try:
        value = _check(key, value, "update")
    except ConfigError:
        return False, f"{key} expects {type(DEFAULTS[key]).__name__}"
    saved = _read(PATH, json.load).copy()
    saved[key] = value
    save(saved)
    return True, f"{key} set to {value}"
