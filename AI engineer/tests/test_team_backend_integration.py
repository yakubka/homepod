"""Opt-in YOLOX in the team backend: services.vision.detector.make_detector -> OccupancyLoop -> fuse -> Runtime -> lights.

SOFTWARE-VALIDATED WITH MOCK CAMERA FRAMES AND SIMULATED LIGHTS (team replay mode). No camera, Raspberry Pi, GPIO or light
is used. Needs the team repository around this folder; skips in a stand-alone AI project.
"""

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

AI_ROOT = Path(__file__).resolve().parent.parent
TEAM_ROOT = AI_ROOT.parent
if not (TEAM_ROOT / "services" / "vision" / "occupancy.py").is_file():
    pytest.skip("the team backend (services/) is not next to this folder", allow_module_level=True)
if str(TEAM_ROOT) not in sys.path:
    sys.path.append(str(TEAM_ROOT))
os.environ.setdefault("VESTA_MODE", "replay")

import vision.team_adapter as team_adapter                                # noqa: E402
from vision.detector import Detection, DetectionResult, ModelFileMissing, PersonDetectorError   # noqa: E402
from services.core import config as team_config                           # noqa: E402
from services.core.engine import Lecture                                  # noqa: E402
from services.core.runtime import Runtime                                 # noqa: E402
from services.device.lights import Lights                                 # noqa: E402
from services.vision.detector import Detector, make_detector              # noqa: E402
from services.vision.occupancy import OccupancyLoop                       # noqa: E402

YOLOX = {**team_config.DEFAULTS, "vision.detector": "yolox"}
W, H = 640, 480
MODEL = AI_ROOT / "models" / "yolox_tiny.onnx"


class ScriptedYolox:
    """Replaces YoloxOnnxPersonDetector inside the adapter: scripted boxes per call, or an inference error."""

    def __init__(self, script):
        self.script = list(script)

    def __call__(self, session=None):              # used as the class the adapter instantiates
        return self

    def detect(self, frame):
        step = self.script.pop(0) if self.script else []
        if isinstance(step, Exception):
            raise step
        dets = [Detection(x1, y1, x2, y2, c, frame.width, frame.height) for x1, y1, x2, y2, c in step]
        return DetectionResult(dets, frame.index, frame.source, frame.width, frame.height, 1.0, 1.0, 1.0, 3.0, "scripted", len(dets))


class MockCamera:
    """The team Camera interface with prepared BGR frames; None = the camera delivered nothing."""

    def __init__(self, frames, starts=True):
        self.frames, self.starts, self.running = list(frames), starts, False

    def start(self):
        self.running = self.starts
        return self.starts

    def read(self):
        return self.frames.pop(0) if self.frames else None

    def stop(self):
        self.running = False


def frame():
    return np.full((H, W, 3), 90, np.uint8)


def centre(n):
    return [(float(220 + (i % 8) * 20), 60.0, float(236 + (i % 8) * 20), 260.0, 0.8) for i in range(n)]


def runtime_for(loop):
    rt = Runtime(Lights(), loop, dry=True)
    rt.lecture = Lecture(id="L1", name="Capstone", start=0.0, end=9e9, expected=20)
    return rt


def yolox_with(monkeypatch, script):
    monkeypatch.setattr(team_adapter, "YoloxOnnxPersonDetector", ScriptedYolox(script))
    det = make_detector(YOLOX)
    assert isinstance(det, team_adapter.YoloxTeamDetector)
    return det


def run_loop(loop, until, timeout=5.0):
    assert loop.start()
    try:
        deadline = time.monotonic() + timeout
        while not until(loop.last) and time.monotonic() < deadline:
            time.sleep(0.01)
        return loop.last
    finally:
        loop.stop()


# --- the factory: default unchanged, opt-in explicit, failures loud ----------------------------------------------

def test_default_setting_builds_the_unchanged_team_detector_without_loading_ai_code():
    code = ("import sys; sys.path.insert(0, %r)\nimport main\nfrom services.core import config\n"
            "d = main.make_detector(config.DEFAULTS)\n"
            "print(type(d).__module__, type(d).__name__, any(m.startswith('vision') for m in sys.modules), 'onnxruntime' in sys.modules)"
            ) % str(TEAM_ROOT)
    env = {**os.environ, "VESTA_MODE": "replay"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(TEAM_ROOT), timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.split() == ["services.vision.detector", "Detector", "False", "False"]
    assert team_config.DEFAULTS["vision.detector"] == "mobilenet"
    assert type(make_detector({**team_config.DEFAULTS, "vision.detector": "mobilenet"})) is Detector


def test_main_builds_its_detector_only_through_the_factory():
    tree = ast.parse((TEAM_ROOT / "main.py").read_text(encoding="utf-8"))
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.count("make_detector") == 1 and "Detector" not in calls


def test_an_unknown_detector_setting_stops_startup():
    with pytest.raises(ValueError, match="unknown vision.detector"):
        make_detector({**team_config.DEFAULTS, "vision.detector": "yolo"})


def test_yolox_without_its_model_file_fails_loudly_and_never_falls_back(monkeypatch, tmp_path):
    import vision.detector as ai_detector
    monkeypatch.setattr(ai_detector, "resolve_model_path", lambda path=None: tmp_path / "missing.onnx")
    with pytest.raises(ModelFileMissing):
        make_detector(YOLOX)


def test_the_setting_is_a_known_key_for_the_config_api():
    assert "vision.detector" in team_config.DEFAULTS and isinstance(team_config.DEFAULTS["vision.detector"], str)


# --- the real occupancy execution path (team OccupancyLoop thread, fuse, Runtime) ----------------------------------

def test_normal_detections_reach_the_lighting_decision(monkeypatch):
    loop = OccupancyLoop(MockCamera([frame()] * 20), yolox_with(monkeypatch, [centre(12)] * 20), interval=0.01)
    last = run_loop(loop, lambda o: o.source == "camera")
    assert (last.total, last.zones, last.source) == (12, {"left": 0, "center": 12, "right": 0}, "camera")
    rt = runtime_for(loop)
    res = rt.tick(team_config.DEFAULTS, now=1000.0)
    assert {a.target: a.value for a in res.actions if a.kind == "zone_light"} == {"center": 100}
    assert rt.lights.zones == {z: 0 for z in rt.lights.zones}          # simulated lights, dry run: nothing switched


def test_an_empty_classroom_is_a_measured_zero_from_a_real_frame(monkeypatch):
    loop = OccupancyLoop(MockCamera([frame()] * 20), yolox_with(monkeypatch, [[]] * 20), interval=0.01)
    last = run_loop(loop, lambda o: o.source == "camera")
    assert (last.total, last.source) == (0, "camera")                  # the frame arrived and the model ran: a real 0
    room = runtime_for(loop).state(team_config.DEFAULTS, now=1000.0)
    assert room.occupancy.total == 0


def test_a_camera_that_delivers_nothing_is_no_camera_count_never_zero(monkeypatch):
    loop = OccupancyLoop(MockCamera([]), yolox_with(monkeypatch, []), interval=0.01)
    loop.last = loop.last.__class__(total=5, zones={"left": 5}, source="camera", confidence=1.0, at=1.0)   # an older count
    last = run_loop(loop, lambda o: o.source == "none")
    assert last.source == "none" and last.total is None
    room = runtime_for(loop).state(team_config.DEFAULTS, now=1000.0)
    assert room.occupancy.total is None and room.occupancy.source == "none"


def test_a_camera_that_does_not_start_leaves_occupancy_unknown(monkeypatch):
    loop = OccupancyLoop(MockCamera([], starts=False), yolox_with(monkeypatch, []), interval=0.01)
    assert loop.start() is False and loop.last.total is None and loop.last.source == "none"


def test_an_inference_failure_is_no_camera_count_never_zero(monkeypatch):
    loop = OccupancyLoop(MockCamera([frame()] * 20), yolox_with(monkeypatch, [PersonDetectorError("onnx failed")] * 20), interval=0.01)
    last = run_loop(loop, lambda o: o.source == "none")
    assert last.source == "none" and last.total is None
    assert runtime_for(loop).state(team_config.DEFAULTS, now=1000.0).occupancy.total is None


def test_recovery_after_a_failure_reports_the_new_count(monkeypatch):
    script = [PersonDetectorError("transient")] + [centre(3)] * 20
    loop = OccupancyLoop(MockCamera([frame()] * 25), yolox_with(monkeypatch, script), interval=0.01)
    last = run_loop(loop, lambda o: o.source == "camera" and o.total == 3)
    assert (last.total, last.source) == (3, "camera")


# --- real YOLOX model (needs models/yolox_tiny.onnx, git-ignored) --------------------------------------------------

@pytest.mark.skipif(not MODEL.is_file(), reason="models/yolox_tiny.onnx is not present (git-ignored; one-time acquisition)")
def test_startup_factory_loads_the_real_yolox_and_runs_the_team_loop_on_a_mock_frame():
    det = make_detector(YOLOX)
    loop = OccupancyLoop(MockCamera([frame()] * 5), det, interval=0.01)
    last = run_loop(loop, lambda o: o.source == "camera", timeout=60)
    assert last.source == "camera" and last.total == 0                 # a flat grey frame: no person


@pytest.mark.skipif(not MODEL.is_file(), reason="models/yolox_tiny.onnx is not present (git-ignored; one-time acquisition)")
def test_in_replay_mode_the_team_simulated_camera_gives_no_yolox_count():
    code = ("import sys, time; sys.path.insert(0, %r)\n"
            "from services.core import config\nfrom services.device.camera import Camera\n"
            "from services.vision.detector import make_detector\nfrom services.vision.occupancy import OccupancyLoop\n"
            "loop = OccupancyLoop(Camera(), make_detector({**config.DEFAULTS, 'vision.detector': 'yolox'}), 0.05)\n"
            "loop.start(); time.sleep(0.6); s = loop.status(); loop.stop()\nprint(s['source'], s['total'])") % str(TEAM_ROOT)
    env = {**os.environ, "VESTA_MODE": "replay"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(TEAM_ROOT), timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.split() == ["none", "None"]                      # simulated dict frames are refused, never counted
    assert "yolox refuses them (no camera count)" in out.stderr


# --- the team confidence rule: YOLOX runs with occupancy.min_confidence as its score floor ----------------------------

# Measured 2026-10-22 on one local classroom photo (img01.jpg, 1920x1088; the photo is NOT in the repository): YOLOX-Tiny
# at its frozen 0.30 threshold returned these six persons (x1, y1, x2, y2, score). Before the floor, the team loop gave the
# frame confidence 0.34 (its lowest score) and fuse() discarded all six.
MEASURED_1920x1088 = [                                                    # exact YOLOX output, rounded to 0.01 px / 0.0001
    (569.67, 64.93, 711.07, 397.21, 0.5903), (845.58, 701.85, 1065.77, 966.58, 0.4659), (365.33, 871.57, 624.31, 1083.63, 0.3932),
    (287.54, 350.57, 570.85, 905.48, 0.3508), (603.13, 809.43, 817.02, 1085.73, 0.3411), (427.15, 569.69, 629.66, 903.00, 0.3392),
]


def photo_frame():
    return np.full((1088, 1920, 3), 90, np.uint8)


def test_measured_case_without_the_floor_the_whole_frame_is_discarded():
    from services.core.engine import fuse
    loop = OccupancyLoop(MockCamera([photo_frame()]), team_adapter.YoloxTeamDetector(detector=ScriptedYolox([MEASURED_1920x1088])()), 1.0)
    snap = loop.snapshot()
    assert (snap.total, snap.zones, snap.confidence) == (6, {"left": 3, "center": 3, "right": 0}, 0.34)
    assert fuse(snap, None, True, team_config.DEFAULTS).source == "none"            # 0.34 < 0.4: all six thrown away


def test_measured_case_with_the_team_floor_keeps_the_confident_people(monkeypatch):
    from services.core.engine import fuse
    det = yolox_with(monkeypatch, [MEASURED_1920x1088])                             # through the factory: floor from config
    assert det.min_confidence == team_config.DEFAULTS["occupancy.min_confidence"] == 0.4
    loop = OccupancyLoop(MockCamera([photo_frame()]), det, 1.0)
    snap = loop.snapshot()
    assert (snap.total, snap.zones, snap.confidence) == (2, {"left": 0, "center": 2, "right": 0}, 0.47)
    fused = fuse(snap, None, True, team_config.DEFAULTS)
    assert (fused.source, fused.total) == ("camera", 2)                              # the count now reaches the engine
    loop.last = snap
    res = runtime_for(loop).tick(team_config.DEFAULTS, now=1000.0)
    cfg = team_config.DEFAULTS                                         # team rule: base + (100 - base) * n / full_at
    want = round(cfg["light.base"] + (100 - cfg["light.base"]) * 2 / cfg["light.full_at"])
    assert want == 50 and {a.target: a.value for a in res.actions if a.kind == "zone_light"} == {"center": want}


def test_a_score_exactly_at_the_floor_is_kept_and_just_below_is_dropped(monkeypatch):
    det = yolox_with(monkeypatch, [[(10.0, 10.0, 50.0, 90.0, 0.40), (100.0, 10.0, 140.0, 90.0, 0.3999)]])
    assert [p["conf"] for p in det.detect(frame())] == [0.4]


def test_the_floor_follows_the_configured_team_setting(monkeypatch):
    monkeypatch.setattr(team_adapter, "YoloxOnnxPersonDetector", ScriptedYolox([]))
    assert make_detector({**YOLOX, "occupancy.min_confidence": 0.55}).min_confidence == 0.55


@pytest.mark.parametrize("bad", [-0.1, 1.5, True, "0.4"])
def test_an_invalid_floor_is_refused(bad):
    with pytest.raises(ValueError):
        team_adapter.YoloxTeamDetector(detector=ScriptedYolox([])(), min_confidence=bad)
