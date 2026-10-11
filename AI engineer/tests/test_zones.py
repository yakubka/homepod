"""Zone assignment and accounting core: synthetic detections only.

No images, no model, no Kaggle or private data, no hardware. The LEFT / CENTER /
RIGHT layouts below are SYNTHETIC FIXTURES, not calibrated classroom geometry.
"""

import dataclasses

import pytest

from vision.detector import Detection
from vision.roi import Roi
from vision.zones import (
    OUTSIDE, UNASSIGNED, Zone, ZoneAccountingError, ZoneError, ZoneOccupancyResult, ZoneSet, assign_detections,
)

W, H = 1000, 500
# Synthetic classroom: the lower 80 % of the frame. LEFT | CENTER | RIGHT partition it exactly,
# sharing the borders x = 0.4 and x = 0.7.
CLASSROOM = Roi.rectangle(0.0, 0.2, 1.0, 1.0)
LEFT = Zone("LEFT", "Left", Roi.rectangle(0.0, 0.2, 0.4, 1.0))
CENTER = Zone("CENTER", "Centre", Roi.rectangle(0.4, 0.2, 0.7, 1.0))
RIGHT = Zone("RIGHT", "Right", Roi.rectangle(0.7, 0.2, 1.0, 1.0))
ZONES = ZoneSet((LEFT, CENTER, RIGHT))


def person(cx, y2=400, w=1000, h=500, conf=0.9):
    """A 40 px wide box (up to 100 px tall, clamped at the frame top) whose bottom-centre is (cx, y2) in pixels.
    Keep 20 <= cx <= w - 20 so the box stays inside the frame and the bottom-centre is exactly (cx, y2)."""
    return Detection(cx - 20, max(0, y2 - 100), cx + 20, y2, conf, frame_width=w, frame_height=h)


def audit(result, inputs):
    """Independent re-check of invariants A-F against the INPUT list (not the result's own validation)."""
    assert result.total_detected == len(inputs)                                           # A
    assert result.inside_classroom_roi + result.outside_classroom_roi == result.total_detected   # B
    zsum = sum(n for _, n in result.zone_counts)
    assert zsum + result.unassigned_inside_roi == result.inside_classroom_roi             # C
    placed = [d for _, ds in result.zone_detections for d in ds] + list(result.unassigned_detections) + list(result.outside_detections)
    assert len(placed) == len(inputs)                                                     # D: none lost, none duplicated
    assert sorted(id(d) for d in placed) == sorted(id(d) for d in inputs)                 # D: by object identity
    assert zsum <= result.inside_classroom_roi                                            # E
    assert result.occupancy_count == result.inside_classroom_roi


def test_one_detection_inside_left():
    d = person(200)
    r = assign_detections([d], CLASSROOM, ZONES)
    assert r.count("LEFT") == 1 and r.count("CENTER") == r.count("RIGHT") == 0 and r.detections_in("LEFT")[0] is d
    audit(r, [d])


def test_one_detection_in_each_zone():
    ds = [person(200), person(550), person(850)]
    r = assign_detections(ds, CLASSROOM, ZONES)
    assert dict(r.zone_counts) == {"LEFT": 1, "CENTER": 1, "RIGHT": 1} and r.unassigned_inside_roi == 0
    assert r.detections_in("CENTER")[0] is ds[1] and r.detections_in("RIGHT")[0] is ds[2]
    audit(r, ds)


def test_detection_outside_the_classroom_roi_is_outside_and_never_consulted_against_zones():
    d = person(200, y2=50)                              # anchor y = 0.1, above the classroom (starts at 0.2)
    r = assign_detections([d], CLASSROOM, ZONES)
    assert r.outside_classroom_roi == 1 and r.outside_detections[0] is d and r.inside_classroom_roi == 0
    # a zone that wrongly extends outside the ROI must still not capture it (ROI is checked first)
    leaky = ZoneSet((Zone("LEAKY", "x", Roi.rectangle(0.0, 0.0, 1.0, 1.0)),))
    r2 = assign_detections([d], CLASSROOM, leaky)
    assert r2.count("LEAKY") == 0 and r2.outside_classroom_roi == 1
    audit(r, [d]); audit(r2, [d])


def test_inside_roi_but_in_no_zone_is_unassigned():
    gap_zones = ZoneSet((LEFT, RIGHT))                  # nothing covers 0.4 < x < 0.7
    d = person(550)
    r = assign_detections([d], CLASSROOM, gap_zones)
    assert r.unassigned_inside_roi == 1 and r.unassigned_detections[0] is d and r.inside_classroom_roi == 1
    assert sum(n for _, n in r.zone_counts) == 0
    audit(r, [d])


def test_shared_boundary_goes_to_the_earlier_zone_left_first():
    d = person(400)                                     # anchor x exactly 0.4: both LEFT and CENTER contain it
    assert LEFT.roi.contains(0.4, 0.8) and CENTER.roi.contains(0.4, 0.8)
    r = assign_detections([d], CLASSROOM, ZONES)
    assert r.count("LEFT") == 1 and r.count("CENTER") == 0
    audit(r, [d])


def test_reversed_zone_order_gives_the_boundary_to_center():
    d = person(400)
    r = assign_detections([d], CLASSROOM, ZoneSet((CENTER, LEFT, RIGHT)))
    assert r.count("CENTER") == 1 and r.count("LEFT") == 0           # ownership comes from ZoneSet order, not from a name
    assert [z for z, _ in r.zone_counts] == ["CENTER", "LEFT", "RIGHT"]
    audit(r, [d])


def test_overlapping_zones_first_match_wins_and_is_never_double_counted():
    a = Zone("A", "a", Roi.rectangle(0.0, 0.2, 0.6, 1.0))
    b = Zone("B", "b", Roi.rectangle(0.4, 0.2, 1.0, 1.0))              # overlaps A for 0.4 <= x <= 0.6
    d = person(500)
    r = assign_detections([d], CLASSROOM, ZoneSet((a, b)))
    assert (r.count("A"), r.count("B")) == (1, 0)
    r2 = assign_detections([d], CLASSROOM, ZoneSet((b, a)))
    assert (r2.count("A"), r2.count("B")) == (0, 1)
    audit(r, [d]); audit(r2, [d])


def test_original_detection_objects_are_preserved_by_identity():
    ds = [person(200), person(500), person(900), person(300, y2=40)]
    r = assign_detections(ds, CLASSROOM, ZONES)
    placed = [d for _, zs in r.zone_detections for d in zs] + list(r.unassigned_detections) + list(r.outside_detections)
    assert all(any(p is d for p in placed) for d in ds)                 # 'is', not equality
    equal_twin = person(200)
    r2 = assign_detections([ds[0], equal_twin], CLASSROOM, ZONES)       # equal but distinct objects stay distinct
    assert r2.detections_in("LEFT")[0] is ds[0] and r2.detections_in("LEFT")[1] is equal_twin


def test_same_normalised_zones_work_at_two_resolutions():
    small = [person(200, 400, 1000, 500), person(550, 400, 1000, 500)]
    large = [Detection(380, 400, 420, 800, 0.9, frame_width=2000, frame_height=1000),     # anchor (0.2, 0.8)
             Detection(1080, 400, 1120, 800, 0.9, frame_width=2000, frame_height=1000)]   # anchor (0.55, 0.8)
    for ds in (small, large):
        r = assign_detections(ds, CLASSROOM, ZONES)
        assert dict(r.zone_counts) == {"LEFT": 1, "CENTER": 1, "RIGHT": 0}
        audit(r, ds)


def test_no_zones_means_inside_detections_are_unassigned_and_outside_stay_outside():
    ds = [person(200), person(550), person(300, y2=40)]
    for zones in (None, ZoneSet()):
        r = assign_detections(ds, CLASSROOM, zones)
        assert not r.zones_configured and r.zone_counts == () and r.zone_detections == ()
        assert (r.inside_classroom_roi, r.unassigned_inside_roi, r.outside_classroom_roi) == (2, 2, 1)
        audit(r, ds)


def test_complete_partition_leaves_nothing_unassigned_and_zone_sum_equals_roi_count():
    ds = [person(x) for x in (20, 200, 399, 400, 401, 550, 699, 700, 701, 980)] + [person(300, y2=40)]
    r = assign_detections(ds, CLASSROOM, ZONES)
    assert r.unassigned_inside_roi == 0
    assert sum(n for _, n in r.zone_counts) == r.inside_classroom_roi
    audit(r, ds)


def test_zone_counts_never_exceed_the_detection_count():
    ds = [person(x) for x in (100, 200, 500, 800)]
    r = assign_detections(ds, CLASSROOM, ZONES)
    assert sum(n for _, n in r.zone_counts) <= len(ds) and all(n <= len(ds) for _, n in r.zone_counts)


def test_multiple_detections_in_the_same_zone():
    ds = [person(100), person(200), person(300), person(800)]
    r = assign_detections(ds, CLASSROOM, ZONES)
    assert dict(r.zone_counts) == {"LEFT": 3, "CENTER": 0, "RIGHT": 1}
    assert [d for d in r.detections_in("LEFT")] == ds[:3]                     # input order preserved
    audit(r, ds)


def test_mixture_of_zone_unassigned_and_outside_accounts_exactly():
    zones = ZoneSet((LEFT, RIGHT))
    z1, z2, gap, out1, out2 = person(100), person(900), person(550), person(500, y2=30), person(500, y2=60)
    ds = [z1, gap, out1, z2, out2]
    r = assign_detections(ds, CLASSROOM, zones)
    assert (r.total_detected, r.inside_classroom_roi, r.outside_classroom_roi) == (5, 3, 2)
    assert dict(r.zone_counts) == {"LEFT": 1, "RIGHT": 1} and r.unassigned_inside_roi == 1
    assert r.unassigned_detections == (gap,) and r.outside_detections == (out1, out2)
    audit(r, ds)


def test_anchor_is_the_bottom_centre_not_the_box_centre():
    front = Zone("FRONT", "front", Roi.rectangle(0.0, 0.2, 1.0, 0.6))     # y from 0.2 to 0.6
    back = Zone("BACK", "back", Roi.rectangle(0.0, 0.6, 1.0, 1.0))
    d = Detection(480, 100, 520, 400, 0.9, frame_width=W, frame_height=H)  # box centre y = 250 px (0.5), bottom y = 400 px (0.8)
    r = assign_detections([d], CLASSROOM, ZoneSet((front, back)))
    assert r.count("BACK") == 1 and r.count("FRONT") == 0
    audit(r, [d])


def test_no_roi_treats_the_whole_frame_as_classroom():
    ds = [person(200, y2=30), person(550)]
    r = assign_detections(ds, None, ZONES)
    assert r.outside_classroom_roi == 0 and r.inside_classroom_roi == 2
    audit(r, ds)


def test_empty_detection_list_and_a_repeated_instance():
    r = assign_detections([], CLASSROOM, ZONES)
    assert r.total_detected == 0 and dict(r.zone_counts) == {"LEFT": 0, "CENTER": 0, "RIGHT": 0}
    d = person(200)
    r2 = assign_detections([d, d], CLASSROOM, ZONES)                      # two list entries, two outcomes
    assert r2.count("LEFT") == 2 and r2.total_detected == 2


def test_assignment_is_deterministic_and_the_result_is_immutable():
    ds = [person(400), person(700), person(100)]
    assert assign_detections(ds, CLASSROOM, ZONES) == assign_detections(ds, CLASSROOM, ZONES)
    r = assign_detections(ds, CLASSROOM, ZONES)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.total_detected = 99


# -- Zone / ZoneSet validation -------------------------------------------------------

def test_duplicate_zone_ids_are_rejected():
    with pytest.raises(ZoneError):
        ZoneSet((LEFT, Zone("LEFT", "again", Roi.rectangle(0.5, 0.5, 0.9, 0.9))))


@pytest.mark.parametrize("zone_id", [OUTSIDE, UNASSIGNED, "", "  ", None, 3])
def test_invalid_or_reserved_zone_ids_are_rejected(zone_id):
    with pytest.raises(ZoneError):
        Zone(zone_id, "x", Roi.rectangle(0.0, 0.0, 0.5, 0.5))


def test_zone_needs_a_roi_and_zoneset_only_holds_zones():
    with pytest.raises(ZoneError):
        Zone("LEFT", "Left", (0.0, 0.0, 0.5, 0.5))
    with pytest.raises(ZoneError):
        ZoneSet((LEFT, "CENTER"))
    assert ZONES.ids == ("LEFT", "CENTER", "RIGHT") and len(ZONES) == 3 and [z.zone_id for z in ZONES] == list(ZONES.ids)


# -- the result rejects inconsistent manual accounting ---------------------------------

def _ok_fields():
    d = person(200)
    return dict(total_detected=1, inside_classroom_roi=1, outside_classroom_roi=0, zone_counts=(("LEFT", 1),), unassigned_inside_roi=0,
                zone_detections=(("LEFT", (d,)),), unassigned_detections=(), outside_detections=(), zones_configured=True)


def test_a_consistent_manual_result_is_accepted():
    assert ZoneOccupancyResult(**_ok_fields()).occupancy_count == 1


@pytest.mark.parametrize("change", [
    dict(total_detected=2),                                          # inside + outside != total
    dict(inside_classroom_roi=2, total_detected=2),                  # zones + unassigned != inside
    dict(unassigned_inside_roi=1),                                   # count does not match the (empty) unassigned bucket
    dict(outside_classroom_roi=1, total_detected=2),                 # count does not match the (empty) outside bucket
    dict(zone_counts=(("LEFT", 2),)),                                # zone count does not match its detections
    dict(zone_counts=(("LEFT", 1), ("LEFT", 0))),                    # duplicate zone ids
    dict(zone_counts=(("CENTER", 1),)),                              # counts and detections disagree on the zone
    dict(inside_classroom_roi=0),                                    # buckets and total consistent, but 'inside' is wrong
    dict(total_detected=-1),
    dict(zones_configured=False),                                    # zone buckets present although not configured
])
def test_inconsistent_manual_accounting_is_rejected(change):
    with pytest.raises(ZoneAccountingError):
        ZoneOccupancyResult(**{**_ok_fields(), **change})


def test_a_detection_in_two_buckets_is_rejected():
    f = _ok_fields()
    d = f["zone_detections"][0][1][0]
    f.update(total_detected=1, unassigned_inside_roi=0)
    f["outside_detections"] = (d,)                                   # same detection also listed as outside
    f["outside_classroom_roi"] = 1                                   # keeps count/bucket consistent but breaks inside+outside=total
    with pytest.raises(ZoneAccountingError):
        ZoneOccupancyResult(**f)
