import json
import os
import signal
import subprocess
import sys
import time

import pytest

from services.core import config
from services.core.registry import SERVICES

DEVICE_LIBS = ("RPi", "smbus2", "cv2", "picamera2", "serial", "sounddevice", "board", "luma", "Adafruit_DHT", "rpi_ws281x", "neopixel")
NAMES = [spec.split(":")[0].split(".")[-2] for spec in SERVICES]


def run(args, **kw):
    env = {**os.environ, "VESTA_MODE": "replay", "VESTA_STRICT": "1", "PYTHONPATH": config.ROOT}
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, cwd=config.ROOT, timeout=60, **kw)


def lines(stdout):
    return [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]


def test_core_starts_every_service_healthy_and_exits_cleanly():
    out = run(["-m", "services.core", "--run-for", "1"])
    assert out.returncode == 0, out.stderr
    records = lines(out.stdout)
    healthy = [r["service"] for r in records if r["msg"] == "service healthy"]
    stopped = [r["service"] for r in records if r["msg"] == "service stopped"]
    assert healthy == NAMES and stopped == NAMES[::-1]
    assert records[-1]["msg"] == "core stopped" and records[-1]["bus"]["violations"] == 0
    assert "Traceback" not in out.stdout + out.stderr


def test_replay_runs_the_full_stack_without_a_device_library():
    code = (
        "import sys\n"
        "from services.core.__main__ import main\n"
        "assert main(['--run-for', '1']) == 0\n"
        f"bad = sorted(n for n in sys.modules if n.split('.')[0] in {DEVICE_LIBS!r})\n"
        "print('DEVICE_LIBS=' + ','.join(bad))\n"
    )
    out = run(["-c", code])
    assert out.returncode == 0, out.stderr
    assert "DEVICE_LIBS=\n" in out.stdout + "\n", out.stdout.splitlines()[-1]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_a_signal_shuts_down_cleanly_with_no_orphaned_thread(sig):
    env = {**os.environ, "VESTA_MODE": "replay", "VESTA_STRICT": "1", "PYTHONPATH": config.ROOT}
    proc = subprocess.Popen([sys.executable, "-m", "services.core"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=config.ROOT)
    deadline = time.time() + 20
    for line in proc.stdout:
        if '"core ready"' in line or time.time() > deadline:
            break
    proc.send_signal(sig)
    stdout, stderr = proc.communicate(timeout=20)
    assert proc.returncode == 0, stderr
    assert '"core stopped"' in stdout
    assert "threads still running" not in stdout
    assert "Traceback" not in stdout + stderr
