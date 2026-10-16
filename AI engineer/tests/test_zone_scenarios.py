"""Week 5 Task 3: zone overlay, synthetic end-to-end scenarios, seating arrangements.

SYNTHETIC TEST GEOMETRY - NOT CALIBRATED. No images of people, no model, no hardware. Occlusion tests
only check how zone assignment behaves for altered box geometry; they say nothing about detector
robustness. Lighting is not tested: assignment never sees pixels and the detector under lighting is
NOT EVALUATED.
"""

import dataclasses
import inspect
import random

import numpy as np
import pytest
from PIL import Image, ImageDraw

from config import settings
from tests import zone_synthetic as zs
from vision import annotate
from vision.annotate import (
    OUTSIDE_COLOR, ROI_COLOR, UNASSIGNED_COLOR, ZONE_COLORS, box_labels, draw_detections, draw_zone_occupancy,
    save_zone_annotated, summary_lines,
)
from vision.detector import Detection
from vision.frames import Frame
from vision.zones import OUTSIDE, UNASSIGNED, ZoneOccupancyResult, assign_detections

GAPPED = zs.zone_set(zs.GAPPED_DEFS)
CLASSROOM = zs.SYNTHETIC_CLASSROOM


def blank(w=zs.W, h=zs.H):
    return np.full((h, w, 3), 235, np.uint8)


def run(spec, defs=zs.GAPPED_DEFS):
    dets, intended = zs.build_arrangement(spec)
    return dets, intended, assign_detections(dets, CLASSROOM, zs.zone_set(defs))


def px(img, x, y):
    return tuple(int(v) for v in img[y, x])


# ---- 1 / 22. backward compatibility ---------------------------------------------------------

def _old_draw_detections(image, detections, roi=None):
    """Independent copy of the Week 4 drawing rules."""
    canvas = Image.fromarray(image).copy()
    draw = ImageDraw.Draw(canvas)
    h, w = image.shape[:2]
    for d in detections:
        inside = roi is None or roi.contains(*_anchor(d))
        color = (0, 255, 0) if inside else (255, 0, 0)
        draw.rectangle([d.x1, d.y1, d.x2, d.y2], outline=color, width=2)
        draw.text((d.x1 + 3, d.y1 + 2), f"{d.confidence:.2f}", fill=color)
    return np.asarray(canvas, dtype=np.uint8)


def _anchor(d):
    return ((d.x1 + d.x2) / 2 / d.frame_width, d.y2 / d.frame_height)


def test_draw_detections_unchanged_without_zones():
    dets = [zs.person(100), zs.person(500)]
    assert np.array_equal(draw_detections(blank(), dets), _old_draw_detections(blank(), dets))


def test_draw_detections_roi_behaviour_unchanged():
    dets = [zs.person(100, 400), zs.person(500, 60)]
    out = draw_detections(blank(), dets, CLASSROOM)
    assert px(out, 80, 360) == (0, 255, 0) or px(out, 80, 300) == (0, 255, 0)   # inside box edge is green
    assert px(out, 480, 20) == (255, 0, 0) or px(out, 480, 10) == (255, 0, 0)   # outside box edge is red


def test_overlay_with_no_zones_and_no_roi_draws_only_boxes():
    dets, _, res = run({"LEFT": [100]})
    out = draw_zone_occupancy(blank(), res, None, None, show_summary=False)
    assert px(out, 500, 100) == (235, 235, 235)          # no zone / ROI outline anywhere
    assert px(out, 500, 250) == (235, 235, 235)
    assert not np.array_equal(out, blank())              # but the box is drawn


# ---- 2 / 3. outlines ------------------------------------------------------------------------

def test_zone_outlines_rendered_in_zone_colours():
    _, _, res = run({})
    out = draw_zone_occupancy(blank(), res, GAPPED, None, show_summary=False)
    assert px(out, 150, 300) == (235, 235, 235)                         # interior untouched
    assert px(out, 150, 100) == ZONE_COLORS["LEFT"]                     # LEFT top edge (y = 0.2 * 500)
    assert px(out, 500, 100) == ZONE_COLORS["CENTER"]
    assert px(out, 850, 100) == ZONE_COLORS["RIGHT"]
    assert len({ZONE_COLORS[z] for z in ("LEFT", "CENTER", "RIGHT")}) == 3


def test_polygon_zone_outlines_rendered():
    _, _, res = run({}, zs.SLANTED_DEFS)
    out = draw_zone_occupancy(blank(), res, zs.zone_set(zs.SLANTED_DEFS), None, show_summary=False)
    assert px(out, 100, 100) == ZONE_COLORS["LEFT"]
    assert px(out, 650, 100) == ZONE_COLORS["CENTER"]


def test_classroom_roi_outline_rendered():
    _, _, res = run({})
    out = draw_zone_occupancy(blank(), res, None, zs.Roi.rectangle(0.1, 0.3, 0.9, 0.9), show_summary=False)
    assert px(out, 500, 150) == ROI_COLOR                               # top edge y = 0.3 * 500
    assert px(out, 500, 300) == (235, 235, 235)


# ---- 4. labels come from the result ---------------------------------------------------------

def test_box_labels_match_buckets_for_every_arrangement():
    for name, spec in zs.ARRANGEMENTS.items():
        dets, intended, res = run(spec)
        labels = box_labels(res)
        assert len(labels) == len(dets), name
        by_id = {id(l.detection): l for l in labels}
        for d, want in zip(dets, intended):
            lab = by_id[id(d)]
            assert lab.text == want == zs.bucket_of(res, d), name
            assert lab.kind == ("unassigned" if want == UNASSIGNED else "outside" if want == OUTSIDE else "zone")


def test_labels_follow_result_not_geometry():
    """A hand-built result that disagrees with the geometry is drawn as the RESULT says (no recomputation)."""
    a, b = zs.person(100), zs.person(900)                               # geometrically LEFT and RIGHT
    res = ZoneOccupancyResult(
        total_detected=2, inside_classroom_roi=2, outside_classroom_roi=0,
        zone_counts=(("LEFT", 0), ("CENTER", 0), ("RIGHT", 2)), unassigned_inside_roi=0,
        zone_detections=(("LEFT", ()), ("CENTER", ()), ("RIGHT", (a, b))),
        unassigned_detections=(), outside_detections=(), zones_configured=True)
    assert {l.text for l in box_labels(res)} == {"RIGHT"}
    out = draw_zone_occupancy(blank(), res, GAPPED, CLASSROOM, show_summary=False)
    assert px(out, 80, 380) == ZONE_COLORS["RIGHT"] or px(out, 80, 300) == ZONE_COLORS["RIGHT"]


# ---- 5. summary text ------------------------------------------------------------------------

def test_summary_lines_state_counts_and_identity():
    _, _, res = run(zs.ARRANGEMENTS["mixed_dense"])
    lines = summary_lines(res)
    assert lines[:5] == ["LEFT: 8", "CENTER: 7", "RIGHT: 9", "UNASSIGNED: 5", "OUTSIDE: 3"]
    assert lines[5] == "TOTAL 32 = 8 + 7 + 9 + 5 + 3"
    assert lines[6].endswith(": 29")                                    # inside = zones + UNASSIGNED


def test_unassigned_not_counted_as_zone_in_summary():
    _, _, res = run({"UNASSIGNED": [350]})
    assert "LEFT: 0" in summary_lines(res) and "UNASSIGNED: 1" in summary_lines(res)


# ---- 6 / 7. UNASSIGNED and OUTSIDE look different -------------------------------------------

def test_unassigned_and_outside_are_visually_distinct():
    colours = {UNASSIGNED_COLOR, OUTSIDE_COLOR, *ZONE_COLORS.values()}
    assert len(colours) == 5
    dets, _, res = run({"UNASSIGNED": [350], "OUTSIDE": [500]})
    out = draw_zone_occupancy(blank(), res, GAPPED, CLASSROOM, show_summary=False)
    un = next(d for d in dets if zs.bucket_of(res, d) == UNASSIGNED)
    assert px(out, int(un.x1), int((un.y1 + un.y2) / 2)) == UNASSIGNED_COLOR        # left box edge


def test_outside_box_is_crossed_out_and_zone_box_is_not():
    dets, _, res = run({"OUTSIDE": [500], "CENTER": [450]})
    out = draw_zone_occupancy(blank(), res, None, None, show_summary=False)
    o = next(d for d in dets if zs.bucket_of(res, d) == OUTSIDE)
    c = next(d for d in dets if zs.bucket_of(res, d) == "CENTER")
    cx, cy = int((o.x1 + o.x2) / 2), int((o.y1 + o.y2) / 2)
    r, g, b = px(out, cx, cy)                                            # diagonals are antialiased: test 'strongly red'
    assert r > 180 and g < 60 and b < 60                                 # the X passes through the centre
    zx, zy = int((c.x1 + c.x2) / 2), int((c.y1 + c.y2) / 2)
    assert px(out, zx, zy) == (235, 235, 235)


# ---- 8. purity ------------------------------------------------------------------------------

def test_overlay_does_not_mutate_inputs():
    dets, _, res = run(zs.ARRANGEMENTS["mixed_dense"])
    img = blank()
    before_img, before_dets, before_res = img.copy(), list(dets), dataclasses.astuple(res)
    out = draw_zone_occupancy(img, res, GAPPED, CLASSROOM)
    assert out is not img and not np.shares_memory(out, img)
    assert np.array_equal(img, before_img)
    assert dets == before_dets and all(a is b for a, b in zip(dets, before_dets))
    assert dataclasses.astuple(res) == before_res
    assert not np.array_equal(out, img)


def test_overlay_signature_takes_result_not_detections():
    params = list(inspect.signature(draw_zone_occupancy).parameters)
    assert params[:2] == ["image", "result"]
    assert "detections" not in params


# ---- 10 / 11. end-to-end scenarios and exact accounting -------------------------------------

EXPECTED = {   # arrangement -> (LEFT, CENTER, RIGHT, UNASSIGNED, OUTSIDE)
    "sparse": (1, 1, 1, 0, 0), "left_heavy": (7, 1, 0, 1, 0), "centre_heavy": (1, 7, 1, 0, 0),
    "right_heavy": (0, 1, 6, 0, 0), "mixed_dense": (8, 7, 9, 5, 3), "empty": (0, 0, 0, 0, 0),
}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_arrangement_accounting_is_exact(name):
    dets, intended, res = run(zs.ARRANGEMENTS[name])
    left, centre, right, un, out = EXPECTED[name]
    assert dict(res.zone_counts) == {"LEFT": left, "CENTER": centre, "RIGHT": right}
    assert res.unassigned_inside_roi == un and res.outside_classroom_roi == out
    assert res.total_detected == len(dets) == left + centre + right + un + out
    assert res.inside_classroom_roi == left + centre + right + un
    assert res.total_detected == res.inside_classroom_roi + res.outside_classroom_roi
    assert res.occupancy_count == res.inside_classroom_roi
    for d, want in zip(dets, intended):
        assert zs.bucket_of(res, d) == want


def test_every_detection_in_exactly_one_bucket():
    dets, _, res = run(zs.ARRANGEMENTS["mixed_dense"])
    buckets = [x for _, ds in res.zone_detections for x in ds] + list(res.unassigned_detections) + list(res.outside_detections)
    assert sorted(id(x) for x in buckets) == sorted(id(x) for x in dets)


def test_detection_order_does_not_change_counts():
    dets, _ = zs.build_arrangement(zs.ARRANGEMENTS["mixed_dense"])
    base = assign_detections(dets, CLASSROOM, GAPPED)
    shuffled = dets[:]
    random.Random(7).shuffle(shuffled)
    other = assign_detections(shuffled, CLASSROOM, GAPPED)
    assert base.zone_counts == other.zone_counts
    assert (base.unassigned_inside_roi, base.outside_classroom_roi) == (other.unassigned_inside_roi, other.outside_classroom_roi)


def test_empty_scene_overlay_and_summary():
    _, _, res = run({})
    assert summary_lines(res)[5] == "TOTAL 0 = 0 + 0 + 0 + 0 + 0"
    out = draw_zone_occupancy(blank(), res, GAPPED, CLASSROOM)
    assert out.shape == blank().shape


def test_full_partition_leaves_nothing_unassigned_and_border_goes_to_first_zone():
    dets = [zs.person(x) for x in (100, 400, 550, 700, 900)]
    res = assign_detections(dets, CLASSROOM, zs.zone_set(zs.PARTITION_DEFS))
    assert res.unassigned_inside_roi == 0
    assert dict(res.zone_counts) == {"LEFT": 2, "CENTER": 2, "RIGHT": 1}      # x=400 -> LEFT, x=700 -> CENTER


def test_normalised_geometry_is_resolution_independent():
    spec = zs.ARRANGEMENTS["mixed_dense"]
    dets, _ = zs.build_arrangement(spec)
    big = [Detection(d.x1 * 2, d.y1 * 2, d.x2 * 2, d.y2 * 2, d.confidence, frame_width=2000, frame_height=1000) for d in dets]
    a, b = assign_detections(dets, CLASSROOM, GAPPED), assign_detections(big, CLASSROOM, GAPPED)
    assert a.zone_counts == b.zone_counts and a.unassigned_inside_roi == b.unassigned_inside_roi


# ---- 5. seating-arrangement shape checks ----------------------------------------------------

def test_arrangements_have_the_intended_shape():
    c = lambda n: dict(run(zs.ARRANGEMENTS[n])[2].zone_counts)
    assert c("left_heavy")["LEFT"] > c("left_heavy")["CENTER"] + c("left_heavy")["RIGHT"]
    assert c("centre_heavy")["CENTER"] > c("centre_heavy")["LEFT"] + c("centre_heavy")["RIGHT"]
    assert c("right_heavy")["RIGHT"] > c("right_heavy")["LEFT"] + c("right_heavy")["CENTER"]


# ---- partial occlusion: ASSIGNMENT behaviour under altered box geometry ----------------------

def test_top_truncation_keeps_the_anchor_and_the_zone():
    full = zs.person(450, 300, height=100)
    cut = Detection(430, 270, 470, 300, 0.9, frame_width=zs.W, frame_height=zs.H)     # top removed
    zones = zs.zone_set(zs.SLANTED_DEFS)
    r = assign_detections([full, cut], CLASSROOM, zones)
    assert zs.bucket_of(r, full) == zs.bucket_of(r, cut) == "CENTER"


def test_severe_bottom_truncation_can_change_the_zone():
    """Documented limitation: bottom-centre anchor moves up when the lower body is hidden/cut."""
    zones = zs.zone_set(zs.SLANTED_DEFS)
    whole = zs.person(450, 300)
    truncated = zs.person(450, 125)
    r = assign_detections([whole, truncated], CLASSROOM, zones)
    assert zs.bucket_of(r, whole) == "CENTER"
    assert zs.bucket_of(r, truncated) == "LEFT"


def test_truncated_box_can_leave_the_classroom():
    r = assign_detections([zs.person(450, 300), zs.person(450, 60)], CLASSROOM, zs.zone_set(zs.SLANTED_DEFS))
    assert r.outside_classroom_roi == 1


def test_assignment_uses_anchor_not_box_centre():
    d = Detection(430, 20, 470, 300, 0.9, frame_width=zs.W, frame_height=zs.H)        # centre y = 160, anchor y = 300
    assert zs.bucket_of(assign_detections([d], CLASSROOM, zs.zone_set(zs.SLANTED_DEFS)), d) == "CENTER"


# ---- lighting is not claimed ----------------------------------------------------------------

def test_assignment_takes_no_pixels():
    params = inspect.signature(assign_detections).parameters
    assert set(params) == {"detections", "classroom_roi", "zones"}


# ---- production settings stay clean ---------------------------------------------------------

def test_synthetic_geometry_is_not_in_production_settings():
    assert settings.ZONES_ENABLED is False and tuple(settings.ZONES) == ()
    assert "SYNTHETIC" in zs.__doc__ and "NOT CALIBRATED" in zs.__doc__


# ---- files and tool -------------------------------------------------------------------------

def test_save_zone_annotated_writes_png(tmp_path):
    _, _, res = run(zs.ARRANGEMENTS["sparse"])
    p = tmp_path / "z.png"
    save_zone_annotated(Frame(blank(), 0, "synthetic"), res, p, GAPPED, CLASSROOM)
    assert np.asarray(Image.open(p)).shape == (zs.H, zs.W, 3)


def test_timing_detections_are_deterministic_and_sized():
    assert zs.timing_detections(0) == []
    assert [d.as_tuple() if hasattr(d, "as_tuple") else dataclasses.astuple(d) for d in zs.timing_detections(25)] == \
           [d.as_tuple() if hasattr(d, "as_tuple") else dataclasses.astuple(d) for d in zs.timing_detections(25)]
    assert len(zs.timing_detections(100)) == 100


def test_benchmark_structure_only():
    """Shape of the output only: no timing thresholds are asserted anywhere."""
    r = zs.run_benchmark(sizes=(0, 3), warmup=1, runs=3, draw_runs=2)
    assert set(r) == {"assignment_accounting", "annotation_drawing"}
    for part in r.values():
        assert set(part) == {"0", "3"}
        assert all({"mean_us", "median_us", "p95_us", "runs"} <= set(s) for s in part.values())


def test_annotate_module_does_not_import_the_detector_model():
    assert not hasattr(annotate, "onnxruntime")
