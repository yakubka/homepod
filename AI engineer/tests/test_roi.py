"""Classroom ROI: normalised rectangle/polygon, bottom-centre membership, inclusive boundary.

Synthetic detections only: no images, no model, no public or private data.
"""

import numpy as np
import pytest

from vision.annotate import draw_detections
from vision.detector import Detection
from vision.roi import Roi, RoiError, anchor, count_in_roi, filter_detections_by_roi, in_roi, roi_from_settings

W, H = 1000, 500


def det(x1, y1, x2, y2, w=W, h=H, conf=0.9):
    return Detection(x1, y1, x2, y2, conf, frame_width=w, frame_height=h)


LEFT_HALF = Roi.rectangle(0.0, 0.0, 0.5, 1.0)      # x from 0 to 0.5 of the frame width (500 px here)


def test_anchor_is_bottom_centre_in_normalised_units():
    assert anchor(det(100, 50, 300, 250)) == (0.2, 0.5)


def test_detection_clearly_inside_rectangle():
    assert in_roi(det(100, 100, 200, 400), LEFT_HALF)


def test_detection_clearly_outside_rectangle():
    assert not in_roi(det(700, 100, 800, 400), LEFT_HALF)


def test_bottom_centre_exactly_on_the_boundary_counts_as_inside():
    d = det(450, 100, 550, 400)                      # bottom-centre x = 500 -> exactly 0.5
    assert anchor(d)[0] == 0.5 and in_roi(d, LEFT_HALF)
    assert in_roi(det(100, 100, 200, 500), Roi.rectangle(0.0, 0.0, 1.0, 1.0))   # y2 on the bottom edge
    assert not in_roi(det(452, 100, 552, 400), LEFT_HALF)                      # 2 px beyond the border


def test_box_overlaps_roi_but_bottom_centre_is_outside():
    d = det(300, 100, 760, 400)                      # box spans the border, bottom-centre x = 530 px
    assert d.x1 < 500 < d.x2 and not in_roi(d, LEFT_HALF)


def test_box_mostly_outside_but_bottom_centre_inside():
    d = det(0, 100, 880, 400)                        # bottom-centre x = 440 px (< 500) although most of the box is outside
    assert (d.x1 + d.x2) / 2 == 440 and in_roi(d, LEFT_HALF)


def test_full_frame_roi_keeps_all_detections():
    ds = [det(0, 0, 10, 10), det(990, 490, 1000, 500), det(400, 100, 600, 500)]
    assert filter_detections_by_roi(ds, Roi.full_frame()) == ds


def test_no_roi_keeps_all_detections():
    ds = [det(0, 0, 10, 10), det(990, 490, 1000, 500)]
    assert filter_detections_by_roi(ds, None) == ds and in_roi(ds[0], None)
    assert count_in_roi(ds, None) == (2, 2, 0)


def test_normalised_roi_is_resolution_independent():
    small, large = det(100, 100, 200, 400, w=1000, h=500), det(200, 200, 400, 800, w=2000, h=1000)   # same relative position
    assert in_roi(small, LEFT_HALF) and in_roi(large, LEFT_HALF)
    far_small, far_large = det(700, 100, 800, 400, w=1000, h=500), det(1400, 200, 1600, 800, w=2000, h=1000)
    assert not in_roi(far_small, LEFT_HALF) and not in_roi(far_large, LEFT_HALF)
    assert anchor(small) == anchor(large)


@pytest.mark.parametrize("args", [(0.5, 0.0, 0.2, 1.0), (0.0, 0.6, 1.0, 0.6), (-0.1, 0.0, 0.5, 1.0), (0.0, 0.0, 1.2, 1.0),
                                  (0.0, 0.0, float("nan"), 1.0), (0.0, 0.0, float("inf"), 1.0)])
def test_invalid_rectangle_fails_clearly(args):
    with pytest.raises(RoiError):
        Roi.rectangle(*args)


def test_filter_returns_the_original_detection_objects_and_separate_counts():
    a, b, c = det(100, 100, 200, 400, conf=0.31), det(700, 100, 800, 400, conf=0.8), det(10, 10, 60, 200, conf=0.5)
    kept = filter_detections_by_roi([a, b, c], LEFT_HALF)
    assert kept == [a, c] and kept[0] is a and kept[1] is c       # same objects, same boxes and confidences
    assert count_in_roi([a, b, c], LEFT_HALF) == (3, 2, 1)         # total, inside, outside


# -- polygon ------------------------------------------------------------------------

TRIANGLE = Roi.polygon([(0.0, 1.0), (0.5, 0.0), (1.0, 1.0)])
L_SHAPE = Roi.polygon([(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0)])   # concave


def test_polygon_inside_outside_and_concave():
    assert TRIANGLE.contains(0.5, 0.6) and not TRIANGLE.contains(0.05, 0.1)
    assert L_SHAPE.contains(0.2, 0.2) and L_SHAPE.contains(0.8, 0.8) and not L_SHAPE.contains(0.8, 0.2)   # notch is outside


def test_polygon_boundary_and_vertices_count_as_inside():
    assert TRIANGLE.contains(0.5, 0.0)                 # apex vertex
    assert TRIANGLE.contains(0.25, 0.5)                # point on the left edge
    assert L_SHAPE.contains(0.5, 0.25) and L_SHAPE.contains(0.75, 0.5)
    assert not TRIANGLE.contains(0.25, 0.49)           # just outside that edge


def test_polygon_membership_uses_the_bottom_centre():
    d = det(450, 0, 550, 450)                          # bottom-centre (0.5, 0.9) inside the triangle
    assert in_roi(d, TRIANGLE)
    assert not in_roi(det(0, 0, 100, 100), TRIANGLE)   # bottom-centre (0.05, 0.2) outside


@pytest.mark.parametrize("pts", [[(0.1, 0.1), (0.9, 0.9)], [(0.1, 0.1), (0.5, 0.5), (0.9, 0.9)], [(0, 0), (0.5, 0), (0.5, 0), (0, 0)],
                                  [(0, 0), (1, 0), (1.5, 1)], [(0, 0), (1, 0), (float("nan"), 1)], [(0, 0), (1,), (1, 1)]])
def test_invalid_polygon_fails_clearly(pts):
    with pytest.raises(RoiError):
        Roi.polygon(pts)


# -- configuration ------------------------------------------------------------------

def test_default_configuration_is_no_roi(monkeypatch):
    from config import settings
    assert settings.ROI_ENABLED is False and roi_from_settings() is None      # no guessed classroom geometry


def test_settings_build_rectangle_polygon_and_reject_bad_config(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ROI_ENABLED", True)
    monkeypatch.setattr(settings, "ROI_TYPE", "rectangle")
    monkeypatch.setattr(settings, "ROI_POINTS", ((0.1, 0.1), (0.9, 0.8)))
    assert roi_from_settings() == Roi.rectangle(0.1, 0.1, 0.9, 0.8)
    monkeypatch.setattr(settings, "ROI_TYPE", "polygon")
    monkeypatch.setattr(settings, "ROI_POINTS", ((0, 1), (0.5, 0), (1, 1)))
    assert roi_from_settings() == TRIANGLE
    monkeypatch.setattr(settings, "ROI_TYPE", "circle")
    with pytest.raises(RoiError):
        roi_from_settings()
    monkeypatch.setattr(settings, "ROI_TYPE", "rectangle")
    monkeypatch.setattr(settings, "ROI_POINTS", ((0.1, 0.1),))
    with pytest.raises(RoiError):
        roi_from_settings()


# -- visualisation (debug aid only) -------------------------------------------------

def test_annotation_marks_the_roi_and_leaves_the_input_untouched():
    img = np.zeros((H, W, 3), np.uint8)
    out = draw_detections(img, [det(100, 100, 200, 400), det(700, 100, 800, 400)], LEFT_HALF)
    assert img.sum() == 0 and out.shape == img.shape
    cyan = (out == np.array([0, 255, 255])).all(axis=2).sum()
    inside_green = (out[100:101, 100:201] == np.array([0, 255, 0])).all(axis=2).sum()
    outside_red = (out[100:101, 700:801] == np.array([255, 0, 0])).all(axis=2).sum()
    assert cyan > 0 and inside_green > 0 and outside_red > 0
