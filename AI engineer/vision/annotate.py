"""Demo/evaluation helper: draw person boxes on a frame (Week 4) and zone occupancy (Week 5).

Not part of the detection representation: the detector never calls this,
and the Detection list is the authoritative result.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

import numpy as np

from vision.detector import Detection
from vision.frames import Frame
from vision.roi import Roi, in_roi
from vision.zones import OUTSIDE, UNASSIGNED, ZoneOccupancyResult, ZoneSet


def draw_detections(image: np.ndarray, detections: Sequence[Detection], roi: Optional[Roi] = None) -> np.ndarray:
    """Return a COPY of the RGB image with a box and confidence per detection.

    With an ROI (calibration/debug aid only): the ROI outline is drawn in cyan, and
    boxes whose bottom-centre anchor is outside it are drawn red instead of green.
    """
    from PIL import Image, ImageDraw

    canvas = Image.fromarray(image).copy()
    draw = ImageDraw.Draw(canvas)
    h, w = image.shape[:2]
    if roi is not None:
        pts = [(x * w, y * h) for x, y in (_rect_corners(roi) if roi.kind == "rectangle" else roi.points)]
        draw.line(pts + [pts[0]], fill=(0, 255, 255), width=3)
    for d in detections:
        color = (0, 255, 0) if in_roi(d, roi) else (255, 0, 0)
        draw.rectangle([d.x1, d.y1, d.x2, d.y2], outline=color, width=2)
        draw.text((d.x1 + 3, d.y1 + 2), f"{d.confidence:.2f}", fill=color)
    return np.asarray(canvas, dtype=np.uint8)


def _rect_corners(roi: Roi):
    (x1, y1), (x2, y2) = roi.points
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def save_annotated(frame: Frame, detections: Sequence[Detection], path: Union[str, Path], roi: Optional[Roi] = None) -> None:
    from PIL import Image

    Image.fromarray(draw_detections(frame.image, detections, roi)).save(path)


# ---------------------------------------------------------------------------------------
# Zone occupancy overlay (Week 5, Task 3). Debug / demonstration aid only.
#
# It CONSUMES a ZoneOccupancyResult from vision.zones.assign_detections and draws exactly the
# buckets in it: it never recomputes membership, never changes a detection, the result, an ROI
# or a count, and returns a copy of the image. LEFT / CENTER / RIGHT are image-space zone IDs
# only; they carry no meaning such as window side, door side or teacher side.
# Not colour alone: every box carries a text label (zone ID, UNASSIGNED or OUTSIDE), each zone
# outline is labelled with its ID and count, OUTSIDE boxes are crossed out and UNASSIGNED boxes
# are drawn thin, and a header lists the counts and the accounting identity.
# ---------------------------------------------------------------------------------------

ZONE_COLORS = {"LEFT": (255, 140, 0), "CENTER": (170, 70, 220), "RIGHT": (30, 130, 255)}
_FALLBACK_COLORS = ((0, 170, 120), (200, 60, 120), (120, 120, 0))
UNASSIGNED_COLOR = (255, 215, 0)
OUTSIDE_COLOR = (255, 0, 0)
ROI_COLOR = (0, 255, 255)


@dataclass(frozen=True)
class BoxLabel:
    detection: Detection
    kind: str        # "zone", "unassigned" or "outside"
    text: str        # the zone ID, "UNASSIGNED" or "OUTSIDE"


def box_labels(result: ZoneOccupancyResult) -> List[BoxLabel]:
    """One label per detection, read straight from the result's buckets (no recomputation)."""
    labels = [BoxLabel(d, "zone", zid) for zid, dets in result.zone_detections for d in dets]
    labels += [BoxLabel(d, "unassigned", UNASSIGNED) for d in result.unassigned_detections]
    labels += [BoxLabel(d, "outside", OUTSIDE) for d in result.outside_detections]
    return labels


def summary_lines(result: ZoneOccupancyResult) -> List[str]:
    """Header text: counts per bucket and the accounting identity N = zones + UNASSIGNED + OUTSIDE."""
    parts = [(zid, n) for zid, n in result.zone_counts] + [(UNASSIGNED, result.unassigned_inside_roi), (OUTSIDE, result.outside_classroom_roi)]
    lines = [f"{name}: {n}" for name, n in parts]
    lines.append(f"TOTAL {result.total_detected} = " + " + ".join(str(n) for _, n in parts))
    lines.append(f"occupancy (detected persons in classroom ROI): {result.occupancy_count}")
    return lines


def _zone_color(zone_id: str, index: int):
    return ZONE_COLORS.get(zone_id, _FALLBACK_COLORS[index % len(_FALLBACK_COLORS)])


def _outline_points(roi: Roi, w: int, h: int):
    pts = _rect_corners(roi) if roi.kind == "rectangle" else roi.points
    return [(x * w, y * h) for x, y in pts]


def draw_zone_occupancy(
    image: np.ndarray,
    result: ZoneOccupancyResult,
    zones: Optional[ZoneSet] = None,
    classroom_roi: Optional[Roi] = None,
    show_summary: bool = True,
) -> np.ndarray:
    """Return a COPY of the RGB image showing the classroom ROI, zone outlines and each assigned detection.

    show_summary draws the count header (top-left) and can be turned off so it does not hide image content."""
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.fromarray(image).copy()
    draw = ImageDraw.Draw(canvas)
    h, w = image.shape[:2]
    try:
        font = ImageFont.load_default(size=max(12, w // 70))
    except TypeError:                       # older Pillow: fixed-size default font
        font = ImageFont.load_default()
    if classroom_roi is not None:
        pts = _outline_points(classroom_roi, w, h)
        draw.line(pts + [pts[0]], fill=ROI_COLOR, width=3)
    if zones is not None:
        counts = dict(result.zone_counts)
        for i, z in enumerate(zones):
            pts = _outline_points(z.roi, w, h)
            color = _zone_color(z.zone_id, i)
            draw.line(pts + [pts[0]], fill=color, width=4)
            label = f"{z.zone_id} ({counts[z.zone_id]})" if z.zone_id in counts else z.zone_id
            draw.text((min(p[0] for p in pts) + 6, min(p[1] for p in pts) + 4), label, fill=color, font=font, stroke_width=2, stroke_fill=(0, 0, 0))
    index = {z.zone_id: i for i, z in enumerate(zones)} if zones is not None else {}
    for lab in box_labels(result):
        d = lab.detection
        if lab.kind == "zone":
            color, width = _zone_color(lab.text, index.get(lab.text, 0)), 3
        elif lab.kind == "unassigned":
            color, width = UNASSIGNED_COLOR, 1
        else:
            color, width = OUTSIDE_COLOR, 2
            draw.line([(d.x1, d.y1), (d.x2, d.y2)], fill=color, width=2)       # crossed out: not a classroom occupant
            draw.line([(d.x1, d.y2), (d.x2, d.y1)], fill=color, width=2)
        draw.rectangle([d.x1, d.y1, d.x2, d.y2], outline=color, width=width)
        draw.text((d.x1 + 3, d.y1 + 2), f"{lab.text} {d.confidence:.2f}", fill=color, font=font, stroke_width=1, stroke_fill=(0, 0, 0))
    if show_summary:
        lines = summary_lines(result)
        line_h = font.size + 4 if hasattr(font, "size") else 14
        draw.rectangle([0, 0, w * 0.55, line_h * len(lines) + 6], fill=(0, 0, 0))
        for i, text in enumerate(lines):
            draw.text((6, 4 + i * line_h), text, fill=(255, 255, 255), font=font)
    return np.asarray(canvas, dtype=np.uint8)


def save_zone_annotated(frame: Frame, result: ZoneOccupancyResult, path: Union[str, Path], zones: Optional[ZoneSet] = None,
                        classroom_roi: Optional[Roi] = None) -> None:
    from PIL import Image

    Image.fromarray(draw_zone_occupancy(frame.image, result, zones, classroom_roi)).save(path)
