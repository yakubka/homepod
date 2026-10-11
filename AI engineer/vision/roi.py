"""Classroom region of interest (ROI) for occupancy counting (Week 4, Task 7).

Pipeline position (the detector knows nothing about the ROI):

    Frame -> detector -> Detection list -> ROI filter -> occupancy count

An ROI belongs to a camera/viewpoint, not to the model, so it is applied to
the detections AFTER inference: no cropping, no retraining, boxes and
confidences are never modified, and the original Detection objects are
returned.

Representation: normalised image coordinates, 0.0 <= x <= 1.0 (left to right)
and 0.0 <= y <= 1.0 (top to bottom), so one definition works at any
resolution. Two kinds: a rectangle and a polygon (implemented with plain
ray casting; no geometry dependency). "No ROI" (None) means the entire frame.

Membership rule: a detection belongs to the ROI when its BOTTOM-CENTRE point,

    anchor = ((x1 + x2) / 2 / frame_width,  y2 / frame_height),

lies inside the ROI. Why: the bottom-centre approximates where the person
stands or sits on the floor, so a box that merely crosses the ROI border
(an arm, a head leaning over a desk) does not change which side the person
is on, and a tall box is not penalised for its height. The rule was chosen
from this geometric argument before any evaluation; it was not selected by
looking at any dataset result. Known limitation: under occlusion (seated
students behind monitors) the visible box bottom is not the floor point.

Boundary rule: a point exactly on the ROI border counts as INSIDE.

There is deliberately NO default classroom geometry: no real VESTA camera has
been mounted or calibrated, so the default is "no ROI". Three-zone
partitioning (LEFT / CENTER / RIGHT) is Week 5, not here.
"""

import math
from dataclasses import dataclass
from typing import List, NamedTuple, Optional, Sequence, Tuple

from vision.detector import Detection

Point = Tuple[float, float]
_EPS = 1e-12


class RoiError(ValueError):
    """The ROI definition is invalid."""


@dataclass(frozen=True)
class Roi:
    """Normalised region of interest. Build with Roi.rectangle() or Roi.polygon()."""

    kind: str                   # "rectangle" or "polygon"
    points: Tuple[Point, ...]   # rectangle: (x1, y1), (x2, y2); polygon: the vertices in order

    @staticmethod
    def _check_point(p) -> Point:
        if len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p):
            raise RoiError(f"ROI point {p!r} must be two finite numbers")
        if not (0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0):
            raise RoiError(f"ROI point {p!r} is outside the normalised range 0..1")
        return (float(p[0]), float(p[1]))

    @classmethod
    def rectangle(cls, x1: float, y1: float, x2: float, y2: float) -> "Roi":
        a, b = cls._check_point((x1, y1)), cls._check_point((x2, y2))
        if not (a[0] < b[0] and a[1] < b[1]):
            raise RoiError(f"rectangle needs x1 < x2 and y1 < y2, got ({x1}, {y1}, {x2}, {y2})")
        return cls("rectangle", (a, b))

    @classmethod
    def polygon(cls, points: Sequence[Point]) -> "Roi":
        pts = tuple(cls._check_point(p) for p in points)
        if len(pts) < 3:
            raise RoiError("a polygon ROI needs at least 3 points")
        area2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
        if abs(area2) < 1e-9:
            raise RoiError("polygon ROI has zero area")
        return cls("polygon", pts)

    @classmethod
    def full_frame(cls) -> "Roi":
        return cls.rectangle(0.0, 0.0, 1.0, 1.0)

    def contains(self, x: float, y: float) -> bool:
        """True if the normalised point is inside or exactly on the border."""
        if self.kind == "rectangle":
            (x1, y1), (x2, y2) = self.points
            return x1 <= x <= x2 and y1 <= y <= y2
        pts = self.points
        n = len(pts)
        for i in range(n):                                   # on an edge or vertex -> inside
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
            cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
            if abs(cross) <= _EPS and min(ax, bx) - _EPS <= x <= max(ax, bx) + _EPS and min(ay, by) - _EPS <= y <= max(ay, by) + _EPS:
                return True
        inside = False                                       # ray casting
        for i in range(n):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
            if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
                inside = not inside
        return inside


class RoiCount(NamedTuple):
    total: int      # every detection
    inside: int     # detections whose anchor lies in the ROI
    outside: int


def anchor(d: Detection) -> Point:
    """Bottom-centre of the box in normalised coordinates."""
    return ((d.x1 + d.x2) / 2 / d.frame_width, d.y2 / d.frame_height)


def in_roi(d: Detection, roi: Optional[Roi]) -> bool:
    return True if roi is None else roi.contains(*anchor(d))


def filter_detections_by_roi(detections: Sequence[Detection], roi: Optional[Roi]) -> List[Detection]:
    """The original Detection objects whose anchor is inside the ROI (all of them when roi is None)."""
    return [d for d in detections if in_roi(d, roi)]


def count_in_roi(detections: Sequence[Detection], roi: Optional[Roi]) -> RoiCount:
    inside = sum(1 for d in detections if in_roi(d, roi))
    return RoiCount(total=len(detections), inside=inside, outside=len(detections) - inside)


def roi_from_settings() -> Optional[Roi]:
    """Build the optional ROI from config/settings.py. Default is disabled: no ROI, entire frame."""
    from config import settings
    if not getattr(settings, "ROI_ENABLED", False):
        return None
    pts = [tuple(p) for p in settings.ROI_POINTS]
    if settings.ROI_TYPE == "rectangle":
        if len(pts) != 2:
            raise RoiError("a rectangle ROI needs exactly two points: top-left and bottom-right")
        return Roi.rectangle(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
    if settings.ROI_TYPE == "polygon":
        return Roi.polygon(pts)
    raise RoiError(f"unknown ROI_TYPE {settings.ROI_TYPE!r}")
