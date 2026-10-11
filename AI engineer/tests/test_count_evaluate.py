"""Count-evaluation metrics and runner, synthetic data only (no public dataset, no model)."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from vision.count_evaluate import (
    ManifestError, band_summaries, empty_room_analysis, evaluate, failure_row, group_summaries, macro_mae,
    make_row, read_manifest, summarize, timing_summary,
)


def rows(pairs, cam="camA", d="dirA"):
    return [make_row(d, f"i{i}.jpg", cam, gt, pred) for i, (gt, pred) in enumerate(pairs)]


def test_exact_match():
    s = summarize(rows([(10, 10)]))
    assert s["mae"] == s["rmse"] == s["bias"] == 0 and s["exact_pct"] == 100 and s["n_equal"] == 1


def test_undercount_and_overcount_signs():
    under, over = make_row("d", "a", "c", 10, 7), make_row("d", "b", "c", 10, 13)
    assert (under["error"], under["abs_error"]) == (-3, 3) and over["error"] == 3
    s = summarize([under, over])
    assert (s["n_under"], s["n_over"], s["mean_undercount"], s["mean_overcount"]) == (1, 1, 3, 3)


def test_zero_ground_truth_has_no_relative_error():
    r = make_row("d", "a", "c", 0, 2)
    assert r["rel_abs_error"] is None and r["error"] == 2                  # no division by zero
    s = summarize([r, make_row("d", "b", "c", 10, 8)])
    assert s["n_gt_positive"] == 1 and s["mean_rel_abs_error_gt_positive"] == pytest.approx(0.2)


def test_mae_rmse_bias():
    s = summarize(rows([(10, 12), (10, 5), (20, 20)]))                     # errors +2, -5, 0
    assert s["mae"] == pytest.approx(7 / 3) and s["bias"] == pytest.approx(-1.0)
    assert s["rmse"] == pytest.approx(math.sqrt((4 + 25 + 0) / 3)) and s["median_ae"] == 2
    assert (s["within1_pct"], s["within2_pct"], s["within5_pct"]) == pytest.approx((100 / 3, 200 / 3, 100))


def test_camera_macro_mae_weights_each_camera_equally():
    a = rows([(10, 11)] * 9, cam="A")                                      # camera A: 9 frames, MAE 1
    b = rows([(10, 20)], cam="B")                                          # camera B: 1 frame, MAE 10
    per = group_summaries(a + b, "camera_group")
    assert summarize(a + b)["mae"] == pytest.approx(1.9)                   # frame-weighted
    assert macro_mae(per) == pytest.approx(5.5)                            # camera-weighted


def test_empty_room_analysis_counts_false_alarms():
    e = empty_room_analysis(rows([(0, 0), (0, 3), (0, 1), (5, 5)]))
    assert (e["n_zero_gt"], e["n_pred_zero"], e["n_pred_ge1"], e["total_false_detections"], e["max_pred"]) == (3, 1, 2, 4, 3)
    assert e["fraction_ge1"] == pytest.approx(2 / 3) and e["mean_pred"] == pytest.approx(4 / 3)


def test_occupancy_bands_are_fixed_and_cover_every_count():
    b = band_summaries(rows([(0, 0), (5, 3), (10, 10), (11, 9), (40, 30), (41, 30), (65, 50)]))
    assert [v["n"] for v in b.values()] == [1, 2, 1, 1, 2] and sum(v["n"] for v in b.values()) == 7
    assert b["GT > 40"]["bias"] == pytest.approx(-13.0)   # errors -11 and -15


def test_timing_summary_and_p95():
    rs = [make_row("d", str(i), "c", 1, 1, dict(preprocess_ms=i, inference_ms=i, postprocess_ms=i, total_ms=float(i))) for i in range(1, 101)]
    t = timing_summary(rs)["total_ms"]
    assert (t["min"], t["max"], t["median"], t["p95"]) == (1, 100, 50.5, 95)


# -- manifest validation ------------------------------------------------------

def _manifest(tmp_path, body, header="directory,image_file_name,count,camera_group_inferred,included"):
    p = tmp_path / "m.csv"
    p.write_text(header + "\n" + body, encoding="utf-8")
    return p


def test_manifest_keeps_included_rows_only(tmp_path):
    p = _manifest(tmp_path, "d1,a.jpg,4,cam01,yes\nd1,b.jpg,0,cam01,no\n")
    assert read_manifest(p) == [dict(directory="d1", image_file_name="a.jpg", count=4, camera_group="cam01")]


@pytest.mark.parametrize("body", ["d1,a.jpg,x,cam01,yes\n", "d1,a.jpg,-1,cam01,yes\n", "d1,a.jpg,,cam01,yes\n", "d1,a.jpg,4,cam01,maybe\n",
                                  "d1,a.jpg,4,cam01,yes\nd1,a.jpg,5,cam01,yes\n"])
def test_invalid_manifest_entries_are_rejected(tmp_path, body):
    with pytest.raises(ManifestError):
        read_manifest(_manifest(tmp_path, body))


def test_manifest_missing_column_is_rejected(tmp_path):
    with pytest.raises(ManifestError):
        read_manifest(_manifest(tmp_path, "d1,a.jpg,4,yes\n", header="directory,image_file_name,count,included"))


# -- explicit failure accounting ----------------------------------------------

class FakeDetector:
    def __init__(self, fail_on=()):
        self.fail_on, self.calls = set(fail_on), 0

    def detect(self, frame):
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError("simulated inference failure")
        return SimpleNamespace(count=3, preprocess_ms=1.0, inference_ms=2.0, postprocess_ms=0.5, total_ms=3.5)


def test_failed_images_are_recorded_not_skipped(tmp_path):
    from PIL import Image
    (tmp_path / "d1" / "images").mkdir(parents=True)
    for n in ("a.jpg", "b.jpg"):
        Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(tmp_path / "d1" / "images" / n)
    m = [dict(directory="d1", image_file_name=n, count=3, camera_group="cam01") for n in ("a.jpg", "b.jpg", "missing.jpg", "c.jpg")]
    (tmp_path / "d1" / "images" / "c.jpg").write_bytes(b"not an image")
    res = evaluate(m, tmp_path, FakeDetector(fail_on={2}))
    assert len(res) == 4                                                    # one row per manifest image, nothing dropped
    assert [r["status"] for r in res] == ["SUCCESS", "FAILED", "FAILED", "FAILED"]
    assert all(r["failure_reason"] for r in res if r["status"] == "FAILED")
    s = summarize(res)
    assert s["n"] == 1 and failure_row("d", "x", "c", 1, "r")["pred"] is None
