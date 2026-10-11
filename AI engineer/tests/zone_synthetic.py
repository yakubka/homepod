"""SYNTHETIC TEST GEOMETRY - NOT CALIBRATED (Week 5, Task 3).

Everything here is invented for software validation: vertical thirds, a gapped layout and a
slanted-border layout of a synthetic 1000 x 500 frame. None of it describes a real classroom,
none of it may be copied into config/settings.py, and no real people or images are involved.

Used by tests/test_zone_scenarios.py, and as a small tool:

    python -m tests.zone_synthetic --demo        # writes a synthetic annotated image
    python -m tests.zone_synthetic --benchmark   # zone-stage timing on synthetic Detection lists
"""

import argparse
import json
import math
import statistics as st
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

from vision.annotate import draw_zone_occupancy
from vision.detector import Detection
from vision.roi import Roi
from vision.zones import ZoneSet, assign_detections, build_zone_set

W, H = 1000, 500
SYNTHETIC_CLASSROOM = Roi.rectangle(0.0, 0.2, 1.0, 1.0)      # the lower 80 % of the synthetic frame


def _rect(zid, x1, y1, x2, y2):
    return (zid, zid.title(), "rectangle", ((x1, y1), (x2, y2)))


def _poly(zid, pts):
    return (zid, zid.title(), "polygon", tuple(pts))


# Complete partition: LEFT | CENTER | RIGHT share the borders x = 0.4 and x = 0.7.
PARTITION_DEFS = (_rect("LEFT", 0.0, 0.2, 0.4, 1.0), _rect("CENTER", 0.4, 0.2, 0.7, 1.0), _rect("RIGHT", 0.7, 0.2, 1.0, 1.0))
# Gapped layout: nothing covers 0.3 < x < 0.4 or 0.6 < x < 0.7, so detections there are UNASSIGNED.
GAPPED_DEFS = (_rect("LEFT", 0.0, 0.2, 0.3, 1.0), _rect("CENTER", 0.4, 0.2, 0.6, 1.0), _rect("RIGHT", 0.7, 0.2, 1.0, 1.0))
# Slanted borders (a perspective-like partition): the LEFT/CENTER border runs from (0.5, 0.2) to (0.3, 1.0).
SLANTED_DEFS = (
    _poly("LEFT", [(0.0, 0.2), (0.5, 0.2), (0.3, 1.0), (0.0, 1.0)]),
    _poly("CENTER", [(0.5, 0.2), (0.8, 0.2), (0.6, 1.0), (0.3, 1.0)]),
    _poly("RIGHT", [(0.8, 0.2), (1.0, 0.2), (1.0, 1.0), (0.6, 1.0)]),
)


def zone_set(defs=PARTITION_DEFS) -> ZoneSet:
    """Built through the real Task 2 loader/validator."""
    return build_zone_set(defs, SYNTHETIC_CLASSROOM)


def person(cx, y2=400, w=W, h=H, conf=0.9, height=100) -> Detection:
    """A 40 px wide box of the given height whose bottom-centre is exactly (cx, y2) pixels."""
    return Detection(cx - 20, max(0, y2 - height), cx + 20, y2, conf, frame_width=w, frame_height=h)


_INSIDE_Y = (150, 250, 350, 450, 490)      # bottom edges inside the classroom (>= 100 px)
_OUTSIDE_Y = (30, 60, 90)                  # bottom edges above the classroom


def build_arrangement(spec: Dict[str, Sequence[int]]) -> Tuple[List[Detection], List[str]]:
    """spec maps an intended bucket (LEFT/CENTER/RIGHT/UNASSIGNED/OUTSIDE) to the bottom-centre x of each person.

    Returns the detections (deterministically interleaved, not grouped) and the intended bucket of each.
    """
    items = []
    for bucket, xs in spec.items():
        for k, cx in enumerate(xs):
            y2 = _OUTSIDE_Y[k % 3] if bucket == "OUTSIDE" else _INSIDE_Y[k % 5]
            items.append((bucket, cx, y2))
    items.sort(key=lambda t: ((t[1] * 7919 + t[2]) % 1009, t[1]))
    return [person(cx, y2) for _, cx, y2 in items], [b for b, _, _ in items]


# Intended arrangements for the GAPPED layout (LEFT 0-300, CENTER 400-600, RIGHT 700-1000 px; boundaries inclusive).
ARRANGEMENTS = {
    "sparse": {"LEFT": [100], "CENTER": [500], "RIGHT": [850]},
    "left_heavy": {"LEFT": [40, 90, 140, 190, 240, 290, 300], "CENTER": [450], "UNASSIGNED": [350]},
    "centre_heavy": {"LEFT": [150], "CENTER": [400, 430, 470, 500, 530, 570, 600], "RIGHT": [900]},
    "right_heavy": {"LEFT": [], "CENTER": [550], "RIGHT": [700, 760, 820, 880, 940, 980]},
    "mixed_dense": {"LEFT": [30, 70, 110, 150, 190, 230, 270, 300], "CENTER": [400, 440, 480, 520, 560, 590, 600],
                    "RIGHT": [700, 740, 780, 820, 860, 900, 940, 970, 980], "UNASSIGNED": [310, 350, 390, 610, 650],
                    "OUTSIDE": [200, 500, 800]},
    "empty": {},
}


def bucket_of(result, d) -> str:
    """Which bucket of a ZoneOccupancyResult holds this exact Detection object."""
    for zid, dets in result.zone_detections:
        if any(x is d for x in dets):
            return zid
    if any(x is d for x in result.unassigned_detections):
        return "UNASSIGNED"
    if any(x is d for x in result.outside_detections):
        return "OUTSIDE"
    raise AssertionError("detection missing from every bucket")


def timing_detections(n: int) -> List[Detection]:
    """Deterministic detections spread over zones, gaps and the outside region, for benchmarking."""
    return [person(20 + (i * 37) % 960, 40 + (i * 53) % 460) for i in range(n)]


def _stats(samples_us: List[float]) -> Dict[str, float]:
    s = sorted(samples_us)
    return {"mean_us": st.mean(s), "median_us": st.median(s), "p95_us": s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)], "runs": len(s)}


def run_benchmark(sizes=(0, 10, 25, 50, 100), warmup=200, runs=2000, draw_runs=50) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Descriptive timing only (no thresholds): assignment + accounting, and separately the overlay drawing."""
    zones = zone_set(GAPPED_DEFS)
    canvas = np.full((720, 1280, 3), 235, np.uint8)
    out = {"assignment_accounting": {}, "annotation_drawing": {}}
    for n in sizes:
        dets = timing_detections(n)
        for _ in range(warmup):
            assign_detections(dets, SYNTHETIC_CLASSROOM, zones)
        samples = []
        for _ in range(runs):
            t = time.perf_counter_ns()
            assign_detections(dets, SYNTHETIC_CLASSROOM, zones)
            samples.append((time.perf_counter_ns() - t) / 1000)
        out["assignment_accounting"][str(n)] = _stats(samples)
        scaled = [Detection(d.x1 * 1.28, d.y1 * 1.44, d.x2 * 1.28, d.y2 * 1.44, d.confidence, frame_width=1280, frame_height=720) for d in dets]
        res = assign_detections(scaled, SYNTHETIC_CLASSROOM, zones)
        for _ in range(5):
            draw_zone_occupancy(canvas, res, zones, SYNTHETIC_CLASSROOM)
        samples = []
        for _ in range(draw_runs):
            t = time.perf_counter_ns()
            draw_zone_occupancy(canvas, res, zones, SYNTHETIC_CLASSROOM)
            samples.append((time.perf_counter_ns() - t) / 1000)
        out["annotation_drawing"][str(n)] = _stats(samples)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--out-dir", default="vision_assets/results/week5_synthetic")
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if args.demo:
        from PIL import Image
        dets, _ = build_arrangement(ARRANGEMENTS["mixed_dense"])
        zones = zone_set(GAPPED_DEFS)
        res = assign_detections(dets, SYNTHETIC_CLASSROOM, zones)
        img = draw_zone_occupancy(np.full((H, W, 3), 235, np.uint8), res, zones, SYNTHETIC_CLASSROOM)
        Image.fromarray(img).save(out / "zone_demo.png")
        print(f"wrote {out / 'zone_demo.png'} (synthetic geometry, no real people); "
              f"total={res.total_detected} zones={dict(res.zone_counts)} unassigned={res.unassigned_inside_roi} outside={res.outside_classroom_roi}")
    if args.benchmark:
        r = run_benchmark()
        (out / "zone_stage_timing.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        for part, rows in r.items():
            print(part)
            for n, s in rows.items():
                print(f"  N={n:>3s}  mean {s['mean_us']:9.1f} us   median {s['median_us']:9.1f} us   p95 {s['p95_us']:9.1f} us   ({s['runs']} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
