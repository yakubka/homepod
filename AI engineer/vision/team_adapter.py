"""Team-backend adapter: the AI engineer's YOLOX person detector behind the team's Detector interface (V-61 integration).

The team's occupancy loop (services/vision/occupancy.py, used by main.py and by the v-04 CameraCounter) only ever calls
`detector.detect(frame)` and reads `detector.status()`. YoloxTeamDetector offers exactly that, so selecting it is a change
of ONE constructor in teammate-owned code (see docs/handoff/VISION_TEAM_ADAPTER_PROPOSAL.md); nothing else in the team
backend changes.

    team Camera (real mode: BGR uint8 array) -> YoloxTeamDetector.detect(frame)
        -> [{"x", "y", "w", "h", "conf", "cx", "zone"}, ...]     # the team Detector's output shape
        -> OccupancyLoop.snapshot() -> Occupancy -> Runtime / fuse / lighting rules

Same as the team Detector: output keys and types, zone = equal thirds of the frame width by box centre x
("left", "center", "right"), person class only, status() keys.

Deliberately different, so a failure is never reported as "0 people" (CLAUDE.md: never fabricate occupancy):
  * a missing frame (None) or a replay-mode dict frame RAISES instead of returning [];
  * an inference failure RAISES instead of being swallowed;
  the team OccupancyLoop catches the exception and publishes Occupancy(source="none"), which its fuse() rule treats as
  "no camera count" and falls back to presence. Zones use the ACTUAL frame width, not the configured one.

Known limits carried over (not hidden): the detector undercounts dense classrooms (frozen Week 4 benchmark, MAE 29.88);
an empty frame is reported by the team loop with confidence 1.0; the professor zone is not calibrated. SOFTWARE-VALIDATED
with fake frames only; no camera or Raspberry Pi has run this code.
"""

from typing import Any, List, Optional

import numpy as np

from vision.detector import YoloxOnnxPersonDetector
from vision.frames import Frame

ZONES = ("left", "center", "right")


class FrameUnavailable(RuntimeError):
    """The camera delivered no usable frame. Raised, never turned into an empty detection list."""


class YoloxTeamDetector:
    """Drop-in for services.vision.detector.Detector. Inject `session` (e.g. the v-04 registry's "detector" session)
    or `detector` (tests); with neither, the hash-checked models/yolox_tiny.onnx in this folder is loaded."""

    def __init__(self, width: int = 640, zones: int = 3, *, session: Any = None, detector: Any = None,
                 min_confidence: float = 0.0) -> None:
        """min_confidence: detections scoring below it are dropped. The team factory passes occupancy.min_confidence (0.4):
        the team OccupancyLoop takes the LOWEST detection score as the frame confidence and fuse() discards the whole count
        below that setting, so one weak YOLOX box (YOLOX keeps scores from 0.30) would otherwise throw away every person in
        the frame. Dropping weak boxes keeps the frame usable but lowers recall (more undercounting). Fixed at construction."""
        if zones != len(ZONES):
            raise ValueError("the team backend uses exactly three seating zones")
        if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)) or not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be a number in 0..1")
        self.min_confidence = float(min_confidence)
        self.width, self.zones = width, zones                   # width kept for interface parity; zoning uses the real frame
        self._detector = detector if detector is not None else YoloxOnnxPersonDetector(session=session)
        self._frames = 0
        self.found: List[dict] = []
        self.zone: Optional[str] = None

    def zone_of(self, cx: int, frame_width: Optional[int] = None) -> str:
        width = frame_width or self.width
        return ZONES[min(int(cx) // max(width // self.zones, 1), self.zones - 1)]

    def detect(self, frame) -> List[dict]:
        self.found, self.zone = [], None
        if frame is None:
            raise FrameUnavailable("no frame from the camera")
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise FrameUnavailable(f"expected a BGR uint8 camera frame, got {type(frame).__name__}")
        rgb = Frame(np.ascontiguousarray(frame[:, :, ::-1]), self._frames, "team-camera")   # team camera is OpenCV BGR
        self._frames += 1
        result = self._detector.detect(rgb)                     # PersonDetectorError propagates on purpose
        width = rgb.width
        for d in result.detections:
            if d.confidence < self.min_confidence:
                continue
            cx = int((d.x1 + d.x2) / 2)
            self.found.append({"x": int(d.x1), "y": int(d.y1), "w": int(d.x2 - d.x1), "h": int(d.y2 - d.y1),
                               "conf": round(float(d.confidence), 2), "cx": cx, "zone": self.zone_of(cx, width)})
        if self.found:
            self.zone = self.found[0]["zone"]
        return self.found

    def status(self) -> dict:
        return {"person": bool(self.found), "zone": self.zone, "count": len(self.found), "found": self.found,
                "detector": "yolox"}


class AdvisoryOccupancy:
    """Wraps the team OccupancyLoop when YOLOX is selected and vision.yolox_lighting is off (the default): GET /occupancy still shows
    the YOLOX counts (marked used_for_lighting False), but the lighting engine reads 'no camera count', so lights hold - never off on
    a camera zero, never a brightness from a count. Why: frozen benchmark MAE 29.88 (31.99 with the 0.4 floor), median count about
    10% of the truth on 547 labelled classroom images."""

    def __init__(self, loop) -> None:
        self._loop = loop

    @property
    def last(self):
        from services.core.engine import Occupancy          # team type; this wrapper only runs inside the team backend
        return Occupancy(source="none")

    def __getattr__(self, name):
        return getattr(self._loop, name)

    def status(self) -> dict:
        return {**self._loop.status(), "used_for_lighting": False}
