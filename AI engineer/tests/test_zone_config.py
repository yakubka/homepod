"""Zone configuration loader and geometry validation (Week 5, Task 2).

All geometry below is a SYNTHETIC FIXTURE (vertical thirds of a synthetic classroom),
not calibrated classroom geometry. No images, no model, no private or Kaggle data.
"""

import pytest

from config import settings
from vision.detector import Detection
from vision.roi import Roi
from vision.zones import (
    CANONICAL_ZONE_IDS, UNASSIGNED, ZoneConfigError, ZoneSet, assign_detections, build_zone_set, zones_from_settings,
)

CLASSROOM = Roi.rectangle(0.0, 0.2, 1.0, 1.0)


def rect(zid, x1, y1, x2, y2, name=None):
    return (zid, name or zid.title(), "rectangle", ((x1, y1), (x2, y2)))


def poly(zid, pts, name=None):
    return (zid, name or zid.title(), "polygon", tuple(pts))


THIRDS = (rect("LEFT", 0.0, 0.2, 0.4, 1.0), rect("CENTER", 0.4, 0.2, 0.7, 1.0), rect("RIGHT", 0.7, 0.2, 1.0, 1.0))
POLY_THIRDS = (
    poly("LEFT", [(0.0, 0.2), (0.4, 0.2), (0.4, 1.0), (0.0, 1.0)]),
    poly("CENTER", [(0.4, 0.2), (0.7, 0.2), (0.7, 1.0), (0.4, 1.0)]),
    poly("RIGHT", [(0.7, 0.2), (1.0, 0.2), (1.0, 1.0), (0.7, 1.0)]),
)


def with_settings(monkeypatch, enabled, zones):
    monkeypatch.setattr(settings, "ZONES_ENABLED", enabled)
    monkeypatch.setattr(settings, "ZONES", zones)


def person(cx, y2=400, w=1000, h=500):
    return Detection(cx - 20, max(0, y2 - 100), cx + 20, y2, 0.9, frame_width=w, frame_height=h)


def replaced(defs, zid, new):
    return tuple(new if d[0] == zid else d for d in defs)


# -- 1. defaults and disabled -------------------------------------------------------------

def test_shipped_defaults_are_disabled_with_no_geometry():
    assert settings.ZONES_ENABLED is False and settings.ZONES == ()
    assert zones_from_settings() is None


def test_disabled_configuration_returns_no_zones_even_if_definitions_exist(monkeypatch):
    with_settings(monkeypatch, False, THIRDS)
    assert zones_from_settings(CLASSROOM) is None


# -- 2-4. valid configurations -------------------------------------------------------------

def test_valid_rectangles_load_through_settings(monkeypatch):
    with_settings(monkeypatch, True, THIRDS)
    zs = zones_from_settings(CLASSROOM)
    assert isinstance(zs, ZoneSet) and zs.ids == ("LEFT", "CENTER", "RIGHT")
    assert zs.zones[0].roi == Roi.rectangle(0.0, 0.2, 0.4, 1.0) and zs.zones[1].name == "Center"


def test_valid_polygon_zones_load(monkeypatch):
    with_settings(monkeypatch, True, POLY_THIRDS)
    zs = zones_from_settings(CLASSROOM)
    assert zs.ids == ("LEFT", "CENTER", "RIGHT") and all(z.roi.kind == "polygon" for z in zs)


def test_canonical_order_is_applied_whatever_the_settings_order(monkeypatch):
    shuffled = (THIRDS[2], THIRDS[0], THIRDS[1])
    with_settings(monkeypatch, True, shuffled)
    assert zones_from_settings(CLASSROOM).ids == CANONICAL_ZONE_IDS == ("LEFT", "CENTER", "RIGHT")


# -- 5-11. the exact-three-zones contract -----------------------------------------------------

@pytest.mark.parametrize("missing", ["LEFT", "CENTER", "RIGHT"])
def test_missing_canonical_zone_is_rejected(missing):
    with pytest.raises(ZoneConfigError, match=f"missing zone ids .*{missing}"):
        build_zone_set(tuple(d for d in THIRDS if d[0] != missing), CLASSROOM)


def test_extra_zone_id_is_rejected():
    with pytest.raises(ZoneConfigError, match="unexpected zone ids .*FRONT"):
        build_zone_set(THIRDS + (rect("FRONT", 0.0, 0.0, 0.1, 0.1),), None)


def test_duplicate_zone_id_is_rejected():
    with pytest.raises(ZoneConfigError, match="duplicate zone ids.*CENTER"):
        build_zone_set(THIRDS + (rect("CENTER", 0.45, 0.3, 0.6, 0.9),), CLASSROOM)


@pytest.mark.parametrize("reserved", ["OUTSIDE", "UNASSIGNED"])
def test_reserved_bucket_names_are_rejected_as_zone_ids(reserved):
    defs = replaced(THIRDS, "RIGHT", rect(reserved, 0.7, 0.2, 1.0, 1.0))
    with pytest.raises(ZoneConfigError, match="reserved"):
        build_zone_set(defs, CLASSROOM)


# -- 12-15. malformed definitions and Roi-owned geometry validation -------------------------------

def test_unsupported_geometry_kind_is_rejected():
    with pytest.raises(ZoneConfigError, match="unsupported geometry kind"):
        build_zone_set(replaced(THIRDS, "LEFT", ("LEFT", "Left", "circle", ((0.2, 0.5), 0.1))), None)


@pytest.mark.parametrize("points", [((0.0, 0.2),), ((0.0, 0.2), (0.4, 1.0), (0.5, 0.5)), ((0.0, "a"), (0.4, 1.0)), (0.0, 0.2, 0.4, 1.0), None])
def test_malformed_rectangle_is_rejected_with_a_clear_error(points):
    with pytest.raises(ZoneConfigError):
        build_zone_set(replaced(THIRDS, "LEFT", ("LEFT", "Left", "rectangle", points)), None)


@pytest.mark.parametrize("points", [((0.0, 0.2), (0.4, 0.2)), ((0.0, 0.2), (0.4, 0.2), (0.4,)), ((0.0, 0.2), (0.4, 0.2), (0.4, "x")), 5, None])
def test_malformed_polygon_is_rejected_with_a_clear_error(points):
    with pytest.raises(ZoneConfigError):
        build_zone_set(replaced(THIRDS, "LEFT", ("LEFT", "Left", "polygon", points)), None)


@pytest.mark.parametrize("pts", [((0.0, 0.2), (1.2, 1.0)), ((-0.1, 0.2), (0.4, 1.0)), ((0.4, 0.2), (0.1, 1.0)), ((0.0, 0.2), (float("nan"), 1.0))])
def test_coordinates_outside_zero_one_or_inverted_are_rejected_via_roi(pts):
    with pytest.raises(ZoneConfigError, match="invalid geometry"):
        build_zone_set(replaced(THIRDS, "LEFT", ("LEFT", "Left", "rectangle", pts)), None)


@pytest.mark.parametrize("bad", [None, ("LEFT", "Left", "rectangle"), ("LEFT", "", "rectangle", ((0, 0), (0.1, 0.1))),
                                 (3, "x", "rectangle", ((0, 0), (0.1, 0.1)))])
def test_malformed_definition_entries_give_config_errors_not_cryptic_ones(bad):
    with pytest.raises(ZoneConfigError):
        build_zone_set(THIRDS[1:] + (bad,), None)


def test_enabled_with_no_definitions_or_a_non_sequence_is_rejected(monkeypatch):
    with_settings(monkeypatch, True, ())
    with pytest.raises(ZoneConfigError, match="no zones"):
        zones_from_settings()
    with pytest.raises(ZoneConfigError):
        build_zone_set(42, None)


# -- 16. classroom containment ----------------------------------------------------------------

def test_zone_vertex_outside_the_classroom_roi_is_rejected():
    defs = replaced(THIRDS, "LEFT", rect("LEFT", 0.0, 0.1, 0.4, 1.0))     # y1 = 0.1 is above the classroom (starts at 0.2)
    with pytest.raises(ZoneConfigError, match="outside the classroom ROI"):
        build_zone_set(defs, CLASSROOM)
    assert build_zone_set(defs, None).ids == CANONICAL_ZONE_IDS          # no classroom ROI: full frame, accepted


def test_loader_works_without_a_classroom_roi(monkeypatch):
    with_settings(monkeypatch, True, THIRDS)
    assert zones_from_settings(None).ids == CANONICAL_ZONE_IDS


def test_vertex_containment_does_not_prove_edge_containment_for_a_concave_classroom_roi():
    """Documented limitation: the check is vertex-based. An L-shaped classroom with a notch is not fully respected."""
    l_shape = Roi.polygon([(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0)])
    center = ("CENTER", "Centre", "polygon", ((0.4, 0.45), (0.9, 0.55), (0.4, 0.6)))   # vertices inside, one edge crosses the notch
    defs = (rect("LEFT", 0.0, 0.0, 0.3, 0.4), center, rect("RIGHT", 0.6, 0.62, 1.0, 1.0))
    assert l_shape.contains(0.4, 0.45) and l_shape.contains(0.9, 0.55) and not l_shape.contains(0.7, 0.49)   # the notch is outside
    assert build_zone_set(defs, l_shape).ids == CANONICAL_ZONE_IDS       # accepted: not claimed to be rejected


# -- 17-21. overlap validation -----------------------------------------------------------------

def test_exact_duplicate_geometry_is_rejected():
    defs = replaced(THIRDS, "CENTER", rect("CENTER", 0.0, 0.2, 0.4, 1.0))        # same rectangle as LEFT
    with pytest.raises(ZoneConfigError, match="identical geometry"):
        build_zone_set(defs, CLASSROOM)


def test_a_vertex_strictly_inside_another_zone_is_rejected():
    defs = replaced(THIRDS, "CENTER", rect("CENTER", 0.3, 0.3, 0.7, 1.0))        # (0.3, 0.3) lies strictly inside LEFT
    with pytest.raises(ZoneConfigError, match="strictly inside"):
        build_zone_set(defs, CLASSROOM)


def test_proper_edge_crossing_is_rejected():
    plus = (rect("LEFT", 0.0, 0.4, 0.5, 0.6), rect("CENTER", 0.2, 0.2, 0.3, 0.8), rect("RIGHT", 0.7, 0.2, 1.0, 1.0))   # horizontal bar x vertical bar
    with pytest.raises(ZoneConfigError, match="edges cross|overlap"):
        build_zone_set(plus, CLASSROOM)
    polys = (poly("LEFT", [(0.0, 0.4), (0.5, 0.4), (0.5, 0.6), (0.0, 0.6)]),
             poly("CENTER", [(0.2, 0.2), (0.3, 0.2), (0.3, 0.8), (0.2, 0.8)]), THIRDS[2])
    with pytest.raises(ZoneConfigError, match="edges cross"):
        build_zone_set(polys, CLASSROOM)                                          # polygons: only the crossing test applies


def test_shared_boundary_between_adjacent_rectangles_is_accepted():
    assert build_zone_set(THIRDS, CLASSROOM).ids == CANONICAL_ZONE_IDS
    assert build_zone_set(POLY_THIRDS, CLASSROOM).ids == CANONICAL_ZONE_IDS       # shared edges between polygons too


def test_shared_vertex_only_is_accepted():
    defs = (rect("LEFT", 0.0, 0.2, 0.4, 0.6), rect("CENTER", 0.4, 0.6, 0.7, 1.0), rect("RIGHT", 0.8, 0.2, 1.0, 0.5))   # LEFT and CENTER touch at (0.4, 0.6)
    assert build_zone_set(defs, CLASSROOM).ids == CANONICAL_ZONE_IDS


def test_rectangles_overlapping_in_an_aligned_band_are_rejected_by_the_exact_rectangle_test():
    strips = (rect("LEFT", 0.0, 0.2, 0.5, 1.0), rect("CENTER", 0.4, 0.2, 0.7, 1.0), THIRDS[2])   # the common mistake: strips overlap by 0.1
    with pytest.raises(ZoneConfigError, match="rectangle interiors overlap"):
        build_zone_set(strips, CLASSROOM)


def test_nested_zone_sharing_borders_is_rejected_by_the_interior_sample_point():
    nested = (rect("LEFT", 0.0, 0.2, 0.5, 1.0), rect("CENTER", 0.0, 0.2, 0.5, 0.6), rect("RIGHT", 0.7, 0.2, 1.0, 1.0))
    with pytest.raises(ZoneConfigError):
        build_zone_set(nested, CLASSROOM)
    poly_nested = (poly("LEFT", [(0.0, 0.2), (0.5, 0.2), (0.5, 1.0), (0.0, 1.0)]),
                   poly("CENTER", [(0.0, 0.2), (0.5, 0.2), (0.5, 0.6), (0.0, 0.6)]), THIRDS[2])
    with pytest.raises(ZoneConfigError, match="strictly inside both"):
        build_zone_set(poly_nested, CLASSROOM)


def test_documented_limitation_polygon_band_overlap_is_not_detected_but_assignment_stays_single_bucket():
    """Two 4-point polygons overlapping in an aligned band slip past the lightweight checks (documented).
    Task 1's first-match assignment still gives every detection exactly one bucket."""
    band = (poly("LEFT", [(0.0, 0.2), (0.5, 0.2), (0.5, 1.0), (0.0, 1.0)]),
            poly("CENTER", [(0.4, 0.2), (0.7, 0.2), (0.7, 1.0), (0.4, 1.0)]), THIRDS[2])
    zs = build_zone_set(band, CLASSROOM)                                  # accepted: NOT claimed to be caught
    d = person(450)                                                       # inside the overlapping band
    r = assign_detections([d], CLASSROOM, zs)
    assert r.count("LEFT") == 1 and r.count("CENTER") == 0 and r.total_detected == 1


# -- 22-26. integration with the Task 1 assignment core ----------------------------------------

def test_disabled_zones_make_every_inside_detection_unassigned(monkeypatch):
    with_settings(monkeypatch, False, ())
    ds = [person(200), person(550), person(300, y2=40)]
    r = assign_detections(ds, CLASSROOM, zones_from_settings(CLASSROOM))
    assert not r.zones_configured and r.unassigned_inside_roi == 2 and r.outside_classroom_roi == 1 and r.total_detected == 3


def test_valid_loaded_zones_drive_the_task_1_assignment(monkeypatch):
    with_settings(monkeypatch, True, THIRDS)
    ds = [person(200), person(550), person(850), person(300, y2=40)]
    r = assign_detections(ds, CLASSROOM, zones_from_settings(CLASSROOM))
    assert dict(r.zone_counts) == {"LEFT": 1, "CENTER": 1, "RIGHT": 1} and r.outside_classroom_roi == 1 and r.unassigned_inside_roi == 0


def test_configuration_does_not_modify_detection_objects(monkeypatch):
    with_settings(monkeypatch, True, THIRDS)
    d = person(200)
    before = (d.x1, d.y1, d.x2, d.y2, d.confidence, d.frame_width, d.frame_height)
    r = assign_detections([d], CLASSROOM, zones_from_settings(CLASSROOM))
    assert r.detections_in("LEFT")[0] is d and before == (d.x1, d.y1, d.x2, d.y2, d.confidence, d.frame_width, d.frame_height)


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0), (2, 0, 1)])
def test_canonical_boundary_priority_survives_settings_loading(monkeypatch, order):
    """settings -> loader -> ZoneSet -> Task 1 ordered assignment, for every settings order."""
    with_settings(monkeypatch, True, tuple(THIRDS[i] for i in order))
    zs = zones_from_settings(CLASSROOM)
    left_center = person(400)                       # anchor x exactly 0.4: LEFT and CENTER both contain it
    center_right = person(700)                      # anchor x exactly 0.7: CENTER and RIGHT both contain it
    r = assign_detections([left_center, center_right], CLASSROOM, zs)
    assert r.detections_in("LEFT") == (left_center,) and r.detections_in("CENTER") == (center_right,) and r.count("RIGHT") == 0
    assert [z for z, _ in r.zone_counts] == ["LEFT", "CENTER", "RIGHT"]


def test_boundary_priority_with_polygon_configuration(monkeypatch):
    with_settings(monkeypatch, True, POLY_THIRDS)
    zs = zones_from_settings(CLASSROOM)
    a, b = person(400), person(700)
    r = assign_detections([a, b], CLASSROOM, zs)
    assert r.detections_in("LEFT") == (a,) and r.detections_in("CENTER") == (b,)
