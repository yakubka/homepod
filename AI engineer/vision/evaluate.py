"""Pilot detection evaluation against hand-drawn boxes (Week 4, Task 5).

    python -m vision.evaluate vision_assets/annotations/classroom_eval.json

Annotation file (JSON, boxes in the upright image's pixel coordinates):
    {"images": {"<file name in the same folder's parent>": {
        "width": W, "height": H,
        "persons": [{"box": [x1, y1, x2, y2], "occluded": false}, ...],
        "ignore":  [{"box": [x1, y1, x2, y2], "reason": "head only"}, ...]}}}
Images are looked up next to the annotations folder's parent (vision_assets/).

Matching: one-to-one, greedy by descending confidence, IoU >= iou_threshold.
A detection that matches no person is a false positive unless it covers at
least `ignore_cover` of an ignore box. The detector is run exactly as
configured; nothing here changes thresholds.
"""

import argparse
import json
import statistics as st
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from config import settings
from vision.detector import Detection, YoloxOnnxPersonDetector
from vision.frames import ImageFileSource

Box = Sequence[float]


def iou(a: Box, b: Box) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(0.0, iw) * max(0.0, ih)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def covered_fraction(pred: Box, region: Box) -> float:
    """Share of `region`'s area that lies inside `pred`."""
    iw = min(pred[2], region[2]) - max(pred[0], region[0])
    ih = min(pred[3], region[3]) - max(pred[1], region[1])
    area = (region[2] - region[0]) * (region[3] - region[1])
    return max(0.0, iw) * max(0.0, ih) / area if area > 0 else 0.0


def match_detections(
    preds: Sequence[Detection], gts: Sequence[Box], ignores: Sequence[Box] = (),
    iou_threshold: float = 0.5, ignore_cover: float = 0.5,
) -> Dict[str, list]:
    """Returns indices: tp [(pred, gt, iou)], fp [pred], ignored [pred], fn [gt]."""
    used = set()
    tp, fp, ignored = [], [], []
    for pi in sorted(range(len(preds)), key=lambda i: -preds[i].confidence):
        p = (preds[pi].x1, preds[pi].y1, preds[pi].x2, preds[pi].y2)
        best, best_iou = None, 0.0
        for gi, g in enumerate(gts):
            if gi in used:
                continue
            v = iou(p, g)
            if v > best_iou:
                best, best_iou = gi, v
        if best is not None and best_iou >= iou_threshold:
            used.add(best)
            tp.append((pi, best, best_iou))
        elif any(covered_fraction(p, ig) >= ignore_cover for ig in ignores):
            ignored.append(pi)
        else:
            fp.append(pi)
    return {"tp": tp, "fp": fp, "ignored": ignored, "fn": [g for g in range(len(gts)) if g not in used]}


def precision_recall(tp: int, fp: int, fn: int):
    return (tp / (tp + fp) if tp + fp else None, tp / (tp + fn) if tp + fn else None)


def count_views(raw_predictions: int, tp: int, fp: int, fn: int, ignored: int, gt: int) -> Dict[str, int]:
    """Two count views for one image (errors are predicted - ground truth).

    raw:       every final detection (what the baseline emits) against ground truth that
               EXCLUDES ignore-region people. The two sides are different populations.
    evaluable: detections that take part in the evaluation (TP + FP; ignored ones are outside it)
               against ground truth (TP + FN). Same population as TP/FP/FN, so the error is FP - FN.
    Neither is a student-count accuracy, and evaluable error 0 can hide FP and FN cancelling out.
    """
    evaluable = raw_predictions - ignored
    assert evaluable == tp + fp and gt == tp + fn, "inconsistent accounting"
    return dict(raw_count=raw_predictions, ignored=ignored, evaluable_count=evaluable, gt=gt,
                raw_error=raw_predictions - gt, evaluable_error=evaluable - gt)


def _draw(frame_image, preds, gts, ignores, result, path) -> None:
    from PIL import Image, ImageDraw
    img = Image.fromarray(frame_image).copy()
    d = ImageDraw.Draw(img)
    w = max(3, img.width // 900)
    for b in ignores:
        d.rectangle(list(b), outline=(160, 160, 160), width=w)
    for gi in result["fn"]:
        d.rectangle(list(gts[gi]), outline=(255, 165, 0), width=w)             # missed person: orange
    for pi, gi, _ in result["tp"]:
        d.rectangle(list(gts[gi]), outline=(0, 120, 255), width=w)             # matched ground truth: blue
        p = preds[pi]; d.rectangle([p.x1, p.y1, p.x2, p.y2], outline=(0, 220, 0), width=w)   # TP: green
    for pi in result["fp"]:
        p = preds[pi]; d.rectangle([p.x1, p.y1, p.x2, p.y2], outline=(255, 0, 0), width=w)   # FP: red
    for pi in result["ignored"]:
        p = preds[pi]; d.rectangle([p.x1, p.y1, p.x2, p.y2], outline=(200, 200, 0), width=w)  # ignored: yellow
    img.save(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vision.evaluate", description=__doc__)
    parser.add_argument("annotations")
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--timing-runs", type=int, default=10)
    parser.add_argument("--out-dir", default=None, help="default: <assets>/results")
    args = parser.parse_args(argv)

    ann_path = Path(args.annotations)
    assets = ann_path.resolve().parent.parent
    out_dir = Path(args.out_dir) if args.out_dir else assets / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ann = json.loads(ann_path.read_text(encoding="utf-8"))

    label = "HUMAN-REVIEWED PILOT METRICS (descriptive only)" if ann.get("review") else "PROVISIONAL PILOT METRICS (descriptive only)"
    detector = YoloxOnnxPersonDetector()
    print(f"config: score_threshold={detector.score_threshold} nms={settings.PERSON_NMS_THRESHOLD} input={detector.input_hw} iou_match={args.iou}")
    totals = dict(tp=0, fp=0, fn=0, ign=0, gt=0, pred=0)
    rows, summary = [], {}
    for name, entry in ann["images"].items():
        src = ImageFileSource([assets / name])
        src.start()
        frame = src.read_frame()
        if (frame.width, frame.height) != (entry["width"], entry["height"]):
            print(f"ERROR: {name} is {frame.width}x{frame.height} but annotations say {entry['width']}x{entry['height']}", file=sys.stderr)
            return 2
        gts = [p["box"] for p in entry["persons"]]
        ignores = [p["box"] for p in entry["ignore"]]
        detector.detect(frame)                                        # warm-up, discarded
        runs = [detector.detect(frame) for _ in range(args.timing_runs)]
        result = runs[-1]
        m = match_detections(result.detections, gts, ignores, args.iou)
        ratio = min(detector.input_hw[0] / frame.height, detector.input_hw[1] / frame.width)
        missed = [{"box": gts[g], "height_orig_px": round(gts[g][3] - gts[g][1]), "height_in416_px": round((gts[g][3] - gts[g][1]) * ratio, 1),
                   "occluded": entry["persons"][g]["occluded"]} for g in m["fn"]]
        hit = [{"box": gts[g], "height_orig_px": round(gts[g][3] - gts[g][1]), "height_in416_px": round((gts[g][3] - gts[g][1]) * ratio, 1),
                "occluded": entry["persons"][g]["occluded"], "iou": round(v, 2), "confidence": round(result.detections[p].confidence, 3)} for p, g, v in m["tp"]]
        t = lambda f: round(st.mean(getattr(r, f) for r in runs), 1)
        summary[name] = dict(size=[frame.width, frame.height], gt=len(gts), pred=result.count, candidates=result.raw_candidates,
                             tp=len(m["tp"]), fp=len(m["fp"]), fn=len(m["fn"]), ignored=len(m["ignored"]), matched=hit, missed=missed,
                             false_positive_boxes=[[round(c) for c in (result.detections[i].x1, result.detections[i].y1, result.detections[i].x2, result.detections[i].y2)] + [round(result.detections[i].confidence, 3)]
                                                                                                          + [round(max([iou((result.detections[i].x1, result.detections[i].y1, result.detections[i].x2, result.detections[i].y2), g) for g in gts] or [0.0]), 2)] for i in m["fp"]],  # last field: best IoU with any person box (descriptive only)
                             ms_mean=dict(preprocess=t("preprocess_ms"), inference=t("inference_ms"), postprocess=t("postprocess_ms"), total=t("total_ms")),
                             ms_total_median=round(st.median(r.total_ms for r in runs), 1), timing_runs=len(runs), letterbox_ratio=round(ratio, 4))
        cv = count_views(result.count, len(m["tp"]), len(m["fp"]), len(m["fn"]), len(m["ignored"]), len(gts))
        summary[name]["counts"] = dict(cv, ignore_regions=len(ignores))
        rows.append((name, cv, len(ignores), len(m["tp"]), len(m["fp"]), len(m["fn"])))
        for k, v in (("tp", len(m["tp"])), ("fp", len(m["fp"])), ("fn", len(m["fn"])), ("ign", len(m["ignored"])), ("gt", len(gts)), ("pred", result.count)):
            totals[k] += v
        _draw(frame.image, result.detections, gts, ignores, m, out_dir / f"{Path(name).stem}_eval.png")

    print(f"{'image':22s} {'GT':>3s} {'ign.reg':>7s} {'raw':>4s} {'ign.pred':>8s} {'evaluable':>9s} {'raw_err':>7s} {'eval_err':>8s} | {'TP':>3s} {'FP':>3s} {'FN':>3s}")
    for n, cv, nreg, tp, fp, fn in rows:
        print(f"{n:22s} {cv['gt']:3d} {nreg:7d} {cv['raw_count']:4d} {cv['ignored']:8d} {cv['evaluable_count']:9d} {cv['raw_error']:+7d} {cv['evaluable_error']:+8d} | {tp:3d} {fp:3d} {fn:3d}")
    prec, rec = precision_recall(totals["tp"], totals["fp"], totals["fn"])
    mae_raw = st.mean(abs(r[1]["raw_error"]) for r in rows)
    mae_eval = st.mean(abs(r[1]["evaluable_error"]) for r in rows)
    print(f"TOTAL: gt={totals['gt']} raw_pred={totals['pred']} TP={totals['tp']} FP={totals['fp']} FN={totals['fn']} ignored_preds={totals['ign']} "
          f"precision={prec:.3f} recall={rec:.3f} (IoU>={args.iou})")
    print(f"RAW-COUNT MAE (all detections vs ignore-excluded GT; different populations) = {mae_raw:.2f} per image")
    print(f"EVALUABLE-COUNT MAE (TP+FP vs TP+FN, = |FP-FN|; preferred) = {mae_eval:.2f} per image   " + label)
    (out_dir / "classroom_eval_metrics.json").write_text(json.dumps(dict(label=label, iou=args.iou, totals=totals, precision=prec, recall=rec, count_mae=mae_raw,
                                                                         count_mae_raw=mae_raw, count_mae_evaluable=mae_eval, images=summary), indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
