import os
import subprocess
import sys

from services.core import config
from services.core.engine import Lecture, Occupancy
from services.core.runtime import Runtime
from services.device.lights import Lights

DEVICE_LIBS = ("RPi", "RPi.GPIO", "smbus2", "cv2", "picamera2", "serial", "sounddevice", "board", "luma")


class FakeOccupancy:
    def __init__(self, counts, professor=None):
        self.last = Occupancy(
            total=sum(counts.values()), zones=dict(counts), professor=professor,
            source="camera", confidence=1.0, at=0.0,
        )

    def status(self):
        return {"total": self.last.total}


def _runtime(dry=False):
    rt = Runtime(Lights(), FakeOccupancy({"left": 0, "center": 12, "right": 0}), dry=dry)
    rt.lecture = Lecture(id="L1", name="Capstone", start=0.0, end=9e9, expected=20)
    return rt


def test_dry_run_decides_without_touching_the_lights():
    rt = _runtime(dry=True)
    res = rt.tick(config.DEFAULTS, now=1000.0)
    assert [a.target for a in res.actions if a.kind == "zone_light"] == ["center"]
    assert rt.lights.zones["center"] == 0


def test_a_live_tick_applies_what_it_decided():
    rt = _runtime()
    rt.tick(config.DEFAULTS, now=1000.0)
    assert rt.lights.zones["center"] == 100
    assert rt.changed["center"] == 1000.0
    assert rt.decisions.query(limit=1)[0]["applied"] is True


def test_an_announcement_is_recorded_so_a_restart_cannot_repeat_it():
    rt = _runtime()
    rt.occupancy.last = Occupancy(total=10, zones={"left": 0, "center": 10, "right": 0},
                                  professor=False, source="camera", confidence=1.0, at=0.0)
    cfg = config.DEFAULTS
    for t in (10.0, 400.0, 800.0, 1200.0):
        rt.tick(cfg, now=t)
    assert rt.announced == ["L1"]


def test_replay_mode_imports_no_device_library():
    # importing is not enough, the drivers reach for their library in __init__
    code = (
        "import importlib, pkgutil, sys, services\n"
        "for m in pkgutil.walk_packages(services.__path__, 'services.'):\n"
        "    importlib.import_module(m.name)\n"
        "from services.device.hardware import Hardware\n"
        "from services.device.camera import Camera\n"
        "from services.device.led_ring import Ring\n"
        "from services.device.display import Display\n"
        "from services.device.servo import Servo\n"
        "from services.device.light_sensor import LightSensor\n"
        "from services.device.lights import Lights\n"
        "from services.vision.detector import Detector\n"
        "for cls in (Hardware, Camera, Ring, Display, Servo, LightSensor, Lights, Detector):\n"
        "    cls()\n"
        "bad = [n for n in sys.modules if n.split('.')[0] in %r]\n"
        "print(','.join(sorted(bad)))\n" % (DEVICE_LIBS,)
    )
    env = {**os.environ, "VESTA_MODE": "replay", "PYTHONPATH": os.getcwd()}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=os.getcwd())
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"replay mode pulled in {out.stdout.strip()}"
