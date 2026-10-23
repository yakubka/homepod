import logging
import sys
from pathlib import Path

log = logging.getLogger("detector")

from services.core.config import REAL

SIM = not REAL

PERSON_CLASS = 15
MIN_CONF = 0.5
PROTOTXT = "models/MobileNetSSD_deploy.prototxt"
WEIGHTS = "models/MobileNetSSD_deploy.caffemodel"
ZONES = ["left", "center", "right"]


class Detector:
    def __init__(self, width=640, zones=3):
        self.width = width
        self.zones = zones
        self.zone_width = width // zones
        self.net = None
        self.found = []
        self.zone = None

        if not SIM:
            import cv2
            self.cv2 = cv2
            self.net = cv2.dnn.readNetFromCaffe(PROTOTXT, WEIGHTS)
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            log.info("mobilenet ssd loaded")

    def zone_of(self, x):
        i = min(x // self.zone_width, self.zones - 1)
        return ZONES[i] if i < len(ZONES) else f"zone{i}"

    def detect(self, frame):
        self.found = []
        self.zone = None

        if frame is None:
            return self.found

        try:
            if SIM:
                if frame.get("person"):
                    cx = frame["x"] + frame["w"] // 2
                    self.found = [{
                        "x": frame["x"], "y": frame["y"],
                        "w": frame["w"], "h": frame["h"],
                        "conf": 0.85, "cx": cx, "zone": self.zone_of(cx),
                    }]
            else:
                h, w = frame.shape[:2]
                blob = self.cv2.dnn.blobFromImage(
                    self.cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5
                )
                self.net.setInput(blob)
                out = self.net.forward()

                for i in range(out.shape[2]):
                    if int(out[0, 0, i, 1]) != PERSON_CLASS:
                        continue
                    conf = float(out[0, 0, i, 2])
                    if conf < MIN_CONF:
                        continue
                    x1, y1, x2, y2 = (out[0, 0, i, 3:7] * [w, h, w, h]).astype(int)
                    cx = (x1 + x2) // 2
                    self.found.append({
                        "x": int(x1), "y": int(y1),
                        "w": int(x2 - x1), "h": int(y2 - y1),
                        "conf": round(conf, 2), "cx": int(cx),
                        "zone": self.zone_of(cx),
                    })
        except Exception as e:
            log.error(f"detection failed, {e}")
            return []

        if self.found:
            self.zone = self.found[0]["zone"]
        return self.found

    def status(self):
        return {
            "person": bool(self.found),
            "zone": self.zone,
            "count": len(self.found),
            "found": self.found,
        }


def make_detector(cfg):
    """vision.detector: "mobilenet" (default, the Detector above) or "yolox", the AI engineer's YOLOX through
    AI engineer/vision/team_adapter.py. Same detect() / status() interface; yolox raises on a missing frame or a failed
    inference, so the occupancy loop reports no camera count instead of 0 people."""
    kind = cfg.get("vision.detector", "mobilenet")
    if kind == "mobilenet":
        return Detector(width=cfg["camera_width"])
    if kind != "yolox":
        raise ValueError(f"unknown vision.detector {kind!r}, expected mobilenet or yolox")
    ai = str(Path(__file__).resolve().parents[2] / "AI engineer")
    if ai not in sys.path:
        sys.path.append(ai)
    from vision.team_adapter import YoloxTeamDetector
    if SIM:
        log.warning("replay mode: the team Camera simulates frames, yolox refuses them (no camera count); only real image frames are counted")
    log.info("yolox detector selected")
    return YoloxTeamDetector(width=cfg["camera_width"], min_confidence=cfg["occupancy.min_confidence"])


def engine_occupancy(cfg, loop):
    # yolox counts stay on /occupancy but the lighting engine ignores them unless vision.yolox_lighting is true
    if cfg.get("vision.detector") != "yolox" or cfg.get("vision.yolox_lighting", False):
        return loop
    from vision.team_adapter import AdvisoryOccupancy
    log.warning("yolox counts are advisory, lighting does not use them (vision.yolox_lighting is off)")
    return AdvisoryOccupancy(loop)
