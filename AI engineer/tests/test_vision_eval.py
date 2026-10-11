"""Metric functions for the pilot evaluation, tested with synthetic boxes only (no images, no model)."""

import pytest

from vision.detector import Detection
from vision.evaluate import covered_fraction, iou, match_detections, precision_recall


def det(x1, y1, x2, y2, conf=0.9):
    return Detection(x1, y1, x2, y2, conf, frame_width=1000, frame_height=1000)


GT = (100, 100, 200, 300)


def test_iou_identical_disjoint_and_half_overlap():
    assert iou(GT, GT) == pytest.approx(1.0)
    assert iou(GT, (300, 100, 400, 300)) == 0.0
    assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(5 * 10 / (100 + 100 - 50))


def test_perfect_match():
    m = match_detections([det(*GT)], [GT])
    assert len(m["tp"]) == 1 and m["fp"] == [] and m["fn"] == []


def test_one_false_positive():
    m = match_detections([det(*GT), det(600, 600, 700, 800)], [GT])
    assert len(m["tp"]) == 1 and m["fp"] == [1] and m["fn"] == []


def test_one_false_negative():
    m = match_detections([det(*GT)], [GT, (500, 500, 600, 700)])
    assert len(m["tp"]) == 1 and m["fp"] == [] and m["fn"] == [1]


def test_duplicate_detection_matches_only_once():
    m = match_detections([det(*GT, conf=0.9), det(102, 102, 202, 302, conf=0.8)], [GT])
    assert len(m["tp"]) == 1 and m["tp"][0][0] == 0      # the higher-confidence box wins
    assert m["fp"] == [1] and m["fn"] == []              # the duplicate is a false positive


def test_each_prediction_takes_its_best_unused_ground_truth():
    g1, g2 = (100, 100, 200, 300), (180, 100, 280, 300)
    m = match_detections([det(*g1), det(*g2)], [g1, g2])
    assert sorted((p, g) for p, g, _ in m["tp"]) == [(0, 0), (1, 1)]


def test_below_iou_threshold_is_fp_and_fn():
    m = match_detections([det(100, 100, 200, 150)], [GT])      # IoU = 0.25
    assert m["tp"] == [] and m["fp"] == [0] and m["fn"] == [0]
    assert len(match_detections([det(100, 100, 200, 150)], [GT], iou_threshold=0.2)["tp"]) == 1


def test_prediction_over_an_ignore_region_is_ignored_not_fp():
    head = (600, 600, 650, 650)
    m = match_detections([det(580, 590, 700, 800)], [], [head])
    assert m["fp"] == [] and m["ignored"] == [0]
    # a prediction that only grazes the ignore region is still a false positive
    assert match_detections([det(640, 640, 800, 800)], [], [head])["fp"] == [0]


def test_ignore_region_never_creates_a_false_negative():
    m = match_detections([], [], [(600, 600, 650, 650)])
    assert m == {"tp": [], "fp": [], "ignored": [], "fn": []}


def test_covered_fraction():
    assert covered_fraction((0, 0, 10, 10), (5, 5, 15, 15)) == pytest.approx(0.25)


def test_precision_recall():
    assert precision_recall(3, 1, 2) == (0.75, 0.6)
    assert precision_recall(0, 0, 0) == (None, None)


# -- Task 5B audit additions -------------------------------------------------

def test_iou_exactly_at_the_threshold_counts_as_a_match():
    gt = (0, 0, 10, 10)
    assert iou(gt, (0, 0, 10, 5)) == 0.5                       # exactly 0.50
    assert len(match_detections([det(0, 0, 10, 5)], [gt])["tp"]) == 1
    assert match_detections([det(0, 0, 10, 4)], [gt])["tp"] == []   # IoU 0.40


def test_one_prediction_overlapping_two_people_takes_the_better_one_only():
    g1, g2 = (0, 0, 100, 100), (60, 0, 160, 100)
    pred = det(10, 0, 110, 100)                                 # IoU with g1 = 0.82, with g2 = 0.43
    m = match_detections([pred], [g1, g2])
    assert [(p, g) for p, g, _ in m["tp"]] == [(0, 0)] and m["fn"] == [1] and m["fp"] == []


def test_matching_is_deterministic_and_strictly_one_to_one():
    gts = [(0, 0, 100, 100), (50, 0, 150, 100), (100, 0, 200, 100)]
    preds = [det(5, 0, 105, 100, 0.9), det(55, 0, 155, 100, 0.8), det(105, 0, 205, 100, 0.7), det(0, 0, 100, 100, 0.6)]
    first = match_detections(preds, gts)
    assert first == match_detections(preds, gts)
    assert len({p for p, _, _ in first["tp"]}) == len(first["tp"])
    assert len({g for _, g, _ in first["tp"]}) == len(first["tp"])
    assert len(first["tp"]) + len(first["fp"]) + len(first["ignored"]) == len(preds)
    assert len(first["tp"]) + len(first["fn"]) == len(gts)


def test_ignore_rule_boundary_is_half_of_the_ignore_box_area():
    ignore = (0, 0, 10, 10)
    assert match_detections([det(0, 0, 10, 5)], [], [ignore])["ignored"] == [0]    # covers exactly 0.5
    assert match_detections([det(0, 0, 10, 4)], [], [ignore])["fp"] == [0]         # covers 0.4: still a false positive


def test_a_matched_prediction_is_never_also_ignored():
    m = match_detections([det(*GT)], [GT], [GT])
    assert len(m["tp"]) == 1 and m["ignored"] == [] and m["fp"] == []


# -- structural audit of the annotation file ---------------------------------

from vision.audit import audit_annotations   # noqa: E402


def _ann(persons, ignore=(), w=1000, h=1000):
    return {"images": {"a.jpg": {"width": w, "height": h, "persons": [{"box": b} for b in persons], "ignore": [{"box": b} for b in ignore]}}}


def test_audit_accepts_a_valid_file():
    assert audit_annotations(_ann([(10, 10, 100, 200)], [(500, 500, 600, 600)]), {"a.jpg": (1000, 1000)}) == []


@pytest.mark.parametrize("persons", [[(100, 10, 10, 200)], [(10, 100, 100, 10)], [(10, 10, 1200, 200)], [(-5, 10, 100, 200)],
                                      [(10, 10, 100, float("nan"))], [(10, 10, 100, 200), (10, 10, 100, 200)]])
def test_audit_flags_bad_boxes_and_duplicates(persons):
    assert audit_annotations(_ann(persons), {"a.jpg": (1000, 1000)})


def test_audit_flags_dimension_mismatch_e_g_an_unrotated_frame():
    problems = audit_annotations(_ann([(10, 10, 100, 200)], w=1000, h=1000), {"a.jpg": (1000, 800)})
    assert any("annotation size" in p for p in problems)


# -- Task 5D: raw versus evaluable count views --------------------------------

from vision.evaluate import count_views   # noqa: E402


def _views(preds, gts, ignores=()):
    m = match_detections(preds, gts, ignores)
    return count_views(len(preds), len(m["tp"]), len(m["fp"]), len(m["fn"]), len(m["ignored"]), len(gts))


def test_count_without_ignores_raw_equals_evaluable():
    v = _views([det(*GT), det(600, 600, 700, 800)], [GT, (800, 100, 900, 300)])
    assert v["raw_count"] == v["evaluable_count"] == 2 and v["raw_error"] == v["evaluable_error"] == 0


def test_one_ignored_prediction_makes_raw_and_evaluable_differ():
    head = (600, 600, 650, 650)
    v = _views([det(*GT), det(580, 590, 700, 800)], [GT], [head])
    assert (v["raw_count"], v["ignored"], v["evaluable_count"]) == (2, 1, 1)
    assert v["raw_error"] == +1 and v["evaluable_error"] == 0     # the ignored detection inflates only the raw error


def test_all_detections_ignored():
    head = (600, 600, 650, 650)
    v = _views([det(580, 590, 700, 800), det(590, 580, 660, 700)], [], [head])
    assert (v["raw_count"], v["evaluable_count"], v["gt"]) == (2, 0, 0)
    assert v["raw_error"] == +2 and v["evaluable_error"] == 0


def test_one_fp_and_one_fn_cancel_in_the_count_but_not_in_detection():
    gt_missed = (800, 100, 900, 300)
    preds = [det(*GT), det(600, 600, 700, 800)]                   # one TP, one FP; gt_missed is a FN
    m = match_detections(preds, [GT, gt_missed])
    v = _views(preds, [GT, gt_missed])
    assert (len(m["tp"]), len(m["fp"]), len(m["fn"])) == (1, 1, 1)
    assert v["evaluable_error"] == 0                              # count error hides two detection errors


def test_fp_only_gives_a_positive_evaluable_error():
    v = _views([det(*GT), det(600, 600, 700, 800)], [GT])
    assert v["evaluable_error"] == +1


def test_fn_only_gives_a_negative_evaluable_error():
    v = _views([det(*GT)], [GT, (800, 100, 900, 300)])
    assert v["evaluable_error"] == -1


def test_evaluable_error_always_equals_fp_minus_fn():
    preds = [det(*GT), det(600, 600, 700, 800), det(10, 10, 60, 60)]
    gts = [GT, (800, 100, 900, 300), (300, 500, 400, 700)]
    m = match_detections(preds, gts)
    assert _views(preds, gts)["evaluable_error"] == len(m["fp"]) - len(m["fn"])
