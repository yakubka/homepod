"""Frozen count-only evaluation on an image-level count dataset (Week 4, Task 6C).

    python -m vision.count_evaluate vision_assets/annotations/classroom_data_eval_manifest.csv \
        --images-root "vision_assets/Classroom-Data (3)/Classroom-Data" \
        --expected-manifest-sha256 <hash> [--out-dir DIR]

Reads a frozen manifest (columns: directory, image_file_name, count,
camera_group_inferred, included, ...), runs the existing frozen
YoloxOnnxPersonDetector on every included image through ImageFileSource, and
compares DetectionResult.count with the label. Nothing here changes the
detector. These are COUNT-AGREEMENT statistics: no boxes exist, so no
object-level precision/recall, and not a student-count accuracy.
"""

import argparse
import csv
import hashlib
import json
import math
import platform
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from vision.frames import FrameSourceError, ImageFileSource

REQUIRED_COLUMNS = ("directory", "image_file_name", "count", "camera_group_inferred", "included")
# Occupancy bands fixed before any result was seen (label count of the image).
BANDS = (("GT = 0", 0, 0), ("GT 1-10", 1, 10), ("GT 11-20", 11, 20), ("GT 21-40", 21, 40), ("GT > 40", 41, 10 ** 9))


class ManifestError(ValueError):
    """The manifest is malformed (missing column, bad count, duplicate image)."""


# -- manifest ------------------------------------------------------------------

def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_manifest(path) -> List[Dict]:
    """Return the INCLUDED manifest rows, validated. Raises ManifestError on malformed input."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ManifestError(f"manifest lacks required columns: {missing}")
        rows, seen = [], set()
        for line_no, r in enumerate(reader, start=2):
            key = (r["directory"], r["image_file_name"])
            if not all((r[c] or "").strip() for c in REQUIRED_COLUMNS):
                raise ManifestError(f"line {line_no}: empty value in a required column")
            if key in seen:
                raise ManifestError(f"line {line_no}: duplicate entry {key}")
            seen.add(key)
            try:
                count = int(r["count"])
            except ValueError:
                raise ManifestError(f"line {line_no}: count {r['count']!r} is not an integer") from None
            if count < 0:
                raise ManifestError(f"line {line_no}: negative count {count}")
            if r["included"] == "yes":
                rows.append(dict(directory=r["directory"], image_file_name=r["image_file_name"], count=count, camera_group=r["camera_group_inferred"]))
            elif r["included"] != "no":
                raise ManifestError(f"line {line_no}: included must be yes or no, got {r['included']!r}")
    return rows


# -- metrics (pure functions, unit-tested with synthetic rows) ---------------------

def make_row(directory: str, name: str, camera: str, gt: int, pred: int, timing: Optional[Dict[str, float]] = None) -> Dict:
    err = pred - gt
    return dict(directory=directory, image_file_name=name, camera_group=camera, gt=gt, pred=pred, error=err, abs_error=abs(err),
                sq_error=err * err, rel_abs_error=(abs(err) / gt if gt > 0 else None), status="SUCCESS", failure_reason="",
                **(timing or dict(preprocess_ms=None, inference_ms=None, postprocess_ms=None, total_ms=None)))


def failure_row(directory: str, name: str, camera: str, gt: int, reason: str) -> Dict:
    return dict(directory=directory, image_file_name=name, camera_group=camera, gt=gt, pred=None, error=None, abs_error=None, sq_error=None,
                rel_abs_error=None, status="FAILED", failure_reason=reason, preprocess_ms=None, inference_ms=None, postprocess_ms=None, total_ms=None)


def summarize(rows: Sequence[Dict]) -> Dict:
    """Count-agreement statistics over SUCCESS rows. Empty input gives n=0 and no metrics."""
    ok = [r for r in rows if r["status"] == "SUCCESS"]
    n = len(ok)
    if n == 0:
        return dict(n=0)
    err = [r["error"] for r in ok]
    ae = [r["abs_error"] for r in ok]
    rel = [r["rel_abs_error"] for r in ok if r["rel_abs_error"] is not None]
    under = [-e for e in err if e < 0]
    over = [e for e in err if e > 0]
    empty = [r for r in ok if r["gt"] == 0]
    return dict(
        n=n, gt_total=sum(r["gt"] for r in ok), pred_total=sum(r["pred"] for r in ok),
        gt_min=min(r["gt"] for r in ok), gt_max=max(r["gt"] for r in ok), pred_min=min(r["pred"] for r in ok), pred_max=max(r["pred"] for r in ok),
        mae=st.mean(ae), rmse=math.sqrt(st.mean(e * e for e in err)), bias=st.mean(err), median_ae=st.median(ae),
        mean_rel_abs_error_gt_positive=(st.mean(rel) if rel else None), n_gt_positive=len(rel),
        exact_pct=100 * sum(1 for e in err if e == 0) / n, within1_pct=100 * sum(1 for a in ae if a <= 1) / n,
        within2_pct=100 * sum(1 for a in ae if a <= 2) / n, within5_pct=100 * sum(1 for a in ae if a <= 5) / n,
        n_under=len(under), n_equal=sum(1 for e in err if e == 0), n_over=len(over),
        mean_undercount=(st.mean(under) if under else None), mean_overcount=(st.mean(over) if over else None),
        n_empty=len(empty), empty_with_detection=sum(1 for r in empty if r["pred"] >= 1),
    )


def group_summaries(rows: Sequence[Dict], key: str) -> Dict[str, Dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def macro_mae(per_group: Dict[str, Dict]) -> Optional[float]:
    """Mean of the per-group MAEs: every group counts equally, whatever its number of frames."""
    maes = [g["mae"] for g in per_group.values() if g.get("n", 0) > 0]
    return st.mean(maes) if maes else None


def band_summaries(rows: Sequence[Dict]) -> Dict[str, Dict]:
    out = {}
    for label, lo, hi in BANDS:
        sel = [r for r in rows if r["status"] == "SUCCESS" and lo <= r["gt"] <= hi]
        s = summarize(sel)
        out[label] = dict(n=s["n"], mae=s.get("mae"), bias=s.get("bias"))
    return out


def empty_room_analysis(rows: Sequence[Dict]) -> Dict:
    """Count-level false alarms on label-0 frames. NOT object-level false positives (no boxes exist)."""
    empty = [r for r in rows if r["status"] == "SUCCESS" and r["gt"] == 0]
    if not empty:
        return dict(n_zero_gt=0)
    preds = [r["pred"] for r in empty]
    return dict(n_zero_gt=len(empty), n_pred_zero=sum(1 for p in preds if p == 0), n_pred_ge1=sum(1 for p in preds if p >= 1),
                fraction_ge1=sum(1 for p in preds if p >= 1) / len(empty), total_false_detections=sum(preds),
                mean_pred=st.mean(preds), max_pred=max(preds))


def timing_summary(rows: Sequence[Dict]) -> Dict:
    out = {}
    for k in ("preprocess_ms", "inference_ms", "postprocess_ms", "total_ms"):
        v = sorted(r[k] for r in rows if r["status"] == "SUCCESS" and r[k] is not None)
        if v:
            out[k] = dict(mean=st.mean(v), median=st.median(v), min=v[0], max=v[-1], p95=v[min(len(v) - 1, math.ceil(0.95 * len(v)) - 1)])
    return out


# -- running -----------------------------------------------------------------------

def evaluate(manifest_rows: Iterable[Dict], images_root, detector) -> List[Dict]:
    """One result row per manifest image: SUCCESS, or an explicit FAILED record. Never skips silently."""
    out = []
    for m in manifest_rows:
        path = Path(images_root) / m["directory"] / "images" / m["image_file_name"]
        try:
            src = ImageFileSource([path])
            src.start()
            result = detector.detect(src.read_frame())
        except Exception as exc:  # a technical failure on one image must not stop the run or vanish
            out.append(failure_row(m["directory"], m["image_file_name"], m["camera_group"], m["count"], f"{type(exc).__name__}: {exc}"[:200]))
            continue
        out.append(make_row(m["directory"], m["image_file_name"], m["camera_group"], m["count"], result.count,
                            dict(preprocess_ms=result.preprocess_ms, inference_ms=result.inference_ms, postprocess_ms=result.postprocess_ms, total_ms=result.total_ms)))
    return out


def _write_csv(path: Path, rows: List[Dict], fields: Sequence[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("NA" if r.get(k) is None else r.get(k)) for k in fields})


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vision.count_evaluate", description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--images-root", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--out-dir", default="vision_assets/results/public_count_eval")
    args = parser.parse_args(argv)

    actual = sha256_file(args.manifest)
    if actual != args.expected_manifest_sha256:
        print(f"STOP: manifest hash {actual} != expected {args.expected_manifest_sha256}; no inference was run", file=sys.stderr)
        return 2
    try:
        manifest_rows = read_manifest(args.manifest)
    except ManifestError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2

    import onnxruntime as ort
    from config import settings
    from vision.detector import YoloxOnnxPersonDetector, resolve_model_path
    detector = YoloxOnnxPersonDetector()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows = evaluate(manifest_rows, args.images_root, detector)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fields = ("directory", "image_file_name", "camera_group", "gt", "pred", "error", "abs_error", "sq_error", "rel_abs_error", "status", "failure_reason",
              "preprocess_ms", "inference_ms", "postprocess_ms", "total_ms")
    _write_csv(out / "per_image_results.csv", rows, fields)
    cams, dirs = group_summaries(rows, "camera_group"), group_summaries(rows, "directory")
    sfields = ("name", "n", "gt_min", "gt_max", "pred_min", "pred_max", "mae", "rmse", "bias", "median_ae", "n_empty", "empty_with_detection")
    _write_csv(out / "camera_group_summary.csv", [dict(name=k, **v) for k, v in cams.items()], sfields)
    _write_csv(out / "directory_summary.csv", [dict(name=k, **v) for k, v in dirs.items()], sfields)
    bands = band_summaries(rows)
    _write_csv(out / "occupancy_band_summary.csv", [dict(band=k, **v) for k, v in bands.items()], ("band", "n", "mae", "bias"))
    failures = [r for r in rows if r["status"] != "SUCCESS"]
    summary = dict(
        global_metrics=summarize(rows), camera_macro_mae=macro_mae(cams), empty_room=empty_room_analysis(rows), timing_ms=timing_summary(rows),
        reproducibility=dict(model_sha256=sha256_file(resolve_model_path(settings.PERSON_MODEL_PATH)), manifest_sha256=actual, onnxruntime=ort.__version__,
                             providers=["CPUExecutionProvider"], python=platform.python_version(), score_threshold=settings.PERSON_SCORE_THRESHOLD,
                             nms_threshold=settings.PERSON_NMS_THRESHOLD, input_hw=list(detector.input_hw), started=started,
                             manifest_images=len(manifest_rows), successful=len(rows) - len(failures), failures=len(failures)),
        failures=[dict(directory=r["directory"], image=r["image_file_name"], reason=r["failure_reason"]) for r in failures])
    (out / "evaluation_summary.json").write_text(json.dumps(summary, indent=1, default=float), encoding="utf-8")
    g = summary["global_metrics"]
    print(f"evaluated {g['n']} / {len(manifest_rows)} images, failures {len(failures)}; MAE {g['mae']:.2f}, RMSE {g['rmse']:.2f}, bias {g['bias']:+.2f}; "
          f"camera-macro MAE {summary['camera_macro_mae']:.2f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
