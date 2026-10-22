"""YOLOX team adapter (vision/team_adapter.py) against the team backend's REAL occupancy loop, fuse rule, runtime and lights.

SOFTWARE-VALIDATED WITH FAKE CAMERA FRAMES. The team code runs in its default replay mode (no device library, simulated
lights). These tests need the team repository around this folder (services/ next to "AI engineer/"); they skip in a
stand-alone AI project. Nothing here touches a camera, the Raspberry Pi or a light.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from vision.detector import Detection, DetectionResult, PersonDetectorError
from vision.team_adapter import FrameUnavailable, YoloxTeamDetector

AI_ROOT = Path(__file__).resolve().parent.parent
TEAM_ROOT = AI_ROOT.parent
if not (TEAM_ROOT / "services" / "vision" / "occupancy.py").is_file():
    pytest.skip("the team backend (services/) is not next to this folder", allow_module_level=True)
if str(TEAM_ROOT) not in sys.path:
    sys.path.append(str(TEAM_ROOT))         # appended: the AI packages keep precedence, team code is reached as services.*

os.environ.setdefault("VESTA_MODE", "replay")
from services.core import config as team_config                            # noqa: E402
from services.core.engine import Lecture, fuse                            # noqa: E402
from services.core.runtime import Runtime                                 # noqa: E402
from services.device.lights import Lights                                 # noqa: E402
from services.vision.detector import Detector as TeamDetector             # noqa: E402
from services.vision.occupancy import OccupancyLoop                       # noqa: E402

W, H = 640, 480


def bgr(w=W, h=H):
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 0] = 200                       # blue channel: makes the BGR -> RGB flip observable
    return img


class FakeInner:
    """Stands in for YoloxOnnxPersonDetector: returns scripted boxes (pixel coordinates) and records what it was given."""

    def __init__(self, boxes=(), error=None):
        self.boxes, self.error, self.seen = list(boxes), error, []

    def detect(self, frame):
        self.seen.append(frame)
        if self.error is not None:
            raise self.error
        dets = [Detection(x1, y1, x2, y2, c, frame.width, frame.height) for x1, y1, x2, y2, c in self.boxes]
        return DetectionResult(dets, frame.index, frame.source, frame.width, frame.height, 1.0, 1.0, 1.0, 3.0, "fake", len(dets))


class FakeCamera:
    """The team Camera interface (start / read / stop / running) serving prepared BGR frames. No OpenCV."""

    def __init__(self, frames):
        self.frames, self.running = list(frames), False

    def start(self):
        self.running = True
        return True

    def read(self):
        return self.frames.pop(0) if self.frames else None

    def stop(self):
        self.running = False


def people(zone_counts, w=W):
    """Boxes whose centres fall in the requested thirds of the frame."""
    out, third = [], w // 3
    for i, zone in enumerate(("left", "center", "right")):
        for k in range(zone_counts.get(zone, 0)):
            x1 = i * third + 10 + (k % 8) * 20
            out.append((float(x1), 50.0, float(x1 + 16), 250.0, 0.8))
    return out


# --- interface conformance -----------------------------------------------------------------------------------------

def test_output_has_exactly_the_team_detector_shape_and_zone_rule():
    team = TeamDetector(width=W)                                  # the team class itself (replay mode: no model loaded)
    ad = YoloxTeamDetector(width=W, detector=FakeInner([(5.0, 10.0, 105.0, 210.0, 0.876), (300.0, 0.0, 340.0, 100.0, 0.5),
                                                         (600.0, 5.0, 639.0, 90.0, 0.31)]))
    found = ad.detect(bgr())
    assert [set(p) for p in found] == [{"x", "y", "w", "h", "conf", "cx", "zone"}] * 3
    assert all(isinstance(p[k], int) for p in found for k in ("x", "y", "w", "h", "cx")) and isinstance(found[0]["conf"], float)
    assert found[0] == {"x": 5, "y": 10, "w": 100, "h": 200, "conf": 0.88, "cx": 55, "zone": "left"}
    assert [p["zone"] for p in found] == ["left", "center", "right"]
    assert [p["zone"] for p in found] == [team.zone_of(p["cx"]) for p in found]      # identical to the team's own rule
    s = ad.status()
    assert set(s) >= set(team.status()) and (s["person"], s["count"], s["zone"]) == (True, 3, "left")


def test_the_team_bgr_frame_reaches_yolox_as_rgb_unchanged_otherwise():
    inner = FakeInner()
    frame = bgr()
    YoloxTeamDetector(detector=inner).detect(frame)
    (seen,) = inner.seen
    assert np.array_equal(seen.image, frame[:, :, ::-1]) and seen.image[0, 0, 2] == 200
    assert frame[0, 0, 0] == 200                                  # the camera's frame is not modified


def test_zones_follow_the_actual_frame_width_not_the_configured_one():
    ad = YoloxTeamDetector(width=640, detector=FakeInner([(700.0, 0.0, 740.0, 50.0, 0.9)]))
    assert ad.detect(bgr(w=1280, h=720))[0]["zone"] == "center"   # cx 720 of 1280 is the middle third


# --- failure is never "0 people" ------------------------------------------------------------------------------------

@pytest.mark.parametrize("frame", [None, {"sim": True, "person": True, "x": 1, "y": 1, "w": 2, "h": 2}, np.zeros((4, 4), np.uint8),
                                   np.zeros((4, 4, 3), np.float32)])
def test_a_missing_or_wrong_frame_raises_instead_of_reporting_nobody(frame):
    with pytest.raises(FrameUnavailable):
        YoloxTeamDetector(detector=FakeInner()).detect(frame)


def test_an_inference_failure_propagates_and_the_team_loop_reports_no_camera_count():
    ad = YoloxTeamDetector(detector=FakeInner(error=PersonDetectorError("onnx failed")))
    with pytest.raises(PersonDetectorError):
        ad.detect(bgr())
    loop = OccupancyLoop(FakeCamera([bgr()] * 50), ad, interval=0.01)
    assert loop.start()
    try:
        deadline = time.monotonic() + 5
        while loop.last.source != "none" and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        assert loop.last.source == "none" and loop.last.total is None
    finally:
        loop.stop()
    occ = fuse(loop.last, None, True, team_config.DEFAULTS)
    assert occ.total is None and occ.source == "none"            # the engine gets "unknown", never 0


def test_no_frame_from_the_camera_is_no_camera_count_in_the_team_loop():
    loop = OccupancyLoop(FakeCamera([]), YoloxTeamDetector(detector=FakeInner()), interval=0.01)
    with pytest.raises(FrameUnavailable):
        loop.snapshot()


# --- end to end through the team backend (replay mode, dry run) -----------------------------------------------------

def test_camera_frame_to_lighting_decision_through_the_team_runtime():
    counts = {"left": 0, "center": 12, "right": 0}
    loop = OccupancyLoop(FakeCamera([bgr()]), YoloxTeamDetector(detector=FakeInner(people(counts))), interval=1.0)
    snap = loop.snapshot()
    assert (snap.total, snap.zones, snap.source) == (12, counts, "camera") and snap.confidence == 0.8
    loop.last = snap
    lights = Lights()
    rt = Runtime(lights, loop, dry=True)
    rt.lecture = Lecture(id="L1", name="Capstone", start=0.0, end=9e9, expected=20)
    res = rt.tick(team_config.DEFAULTS, now=1000.0)
    zone_actions = {a.target: a.value for a in res.actions if a.kind == "zone_light"}
    assert zone_actions == {"center": 100}                        # 12 students = light.full_at -> full brightness, others stay off
    assert lights.zones["center"] == 0                            # dry run: nothing switched


def test_low_detection_confidence_is_not_trusted_by_the_team_fuse_rule():
    loop = OccupancyLoop(FakeCamera([bgr()]), YoloxTeamDetector(detector=FakeInner([(10.0, 10.0, 50.0, 90.0, 0.31)])), interval=1.0)
    snap = loop.snapshot()                                        # 0.31 < occupancy.min_confidence 0.4
    assert snap.total == 1 and fuse(snap, None, True, team_config.DEFAULTS).source == "none"


# --- safety: imports and defaults -----------------------------------------------------------------------------------

def test_importing_the_adapter_and_the_team_loop_loads_no_device_library():
    code = ("import sys; sys.path.insert(0, %r); sys.path.append(%r)\n"
            "import vision.team_adapter, services.vision.occupancy, services.core.runtime\n"
            "bad = [m for m in ('cv2', 'RPi', 'RPi.GPIO', 'smbus2', 'serial', 'picamera2', 'board', 'luma') if m in sys.modules]\n"
            "print(','.join(bad))") % (str(AI_ROOT), str(TEAM_ROOT))
    env = {**os.environ, "VESTA_MODE": "replay"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(TEAM_ROOT), timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip() == ""


def test_the_ai_config_package_wins_over_a_team_config_folder_on_the_path(tmp_path):
    (tmp_path / "config").mkdir()                                 # like the team's v-04 config/ folder (YAML only, no __init__.py)
    (tmp_path / "config" / "default.yaml").write_text("x: 1\n", encoding="utf-8")
    code = ("import sys; sys.path[:0] = [%r]; sys.path.append(%r)\n"
            "from vision.team_adapter import YoloxTeamDetector\nfrom config import settings\nprint(settings.PERSON_MODEL_PATH)") % (
        str(tmp_path), str(AI_ROOT))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(tmp_path), timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip().endswith("yolox_tiny.onnx")


def test_the_team_default_detector_is_unchanged():
    # main.py builds its detector through the opt-in factory; the default setting still gives the team's MobileNet Detector
    main_py = (TEAM_ROOT / "main.py").read_text(encoding="utf-8")
    assert "detector = make_detector(cfg)" in main_py and "team_adapter" not in main_py
    from services.vision.detector import make_detector
    assert team_config.DEFAULTS["vision.detector"] == "mobilenet"
    assert type(make_detector(team_config.DEFAULTS)) is TeamDetector


def test_the_real_yolox_model_runs_on_a_team_shaped_frame_when_present():
    model = AI_ROOT / "models" / "yolox_tiny.onnx"
    if not model.is_file():
        pytest.skip("models/yolox_tiny.onnx is not present (git-ignored; one-time acquisition)")
    ad = YoloxTeamDetector(width=W)
    t0 = time.perf_counter()
    found = ad.detect(np.zeros((H, W, 3), np.uint8))              # a blank frame: no person
    assert found == [] and ad.status()["count"] == 0
    assert time.perf_counter() - t0 < 30                          # a hang guard only; speed is NOT claimed
