"""Audit of the pilot annotations and metrics (Week 4, Task 5B). Read-only: changes no result.

    python -m vision.audit vision_assets/annotations/classroom_eval.json

Checks the annotation file structurally, proves the EXIF/coordinate chain for
every image, reconciles prediction and ground-truth accounting, and writes
ID-labelled review overlays plus a checklist for the human reviewer to
vision_assets/results/audit/. The detector runs exactly as configured.
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from config import settings
from vision.detector import YoloxOnnxPersonDetector
from vision.evaluate import iou, match_detections
from vision.frames import ImageFileSource


def audit_annotations(ann: Dict, frame_sizes: Dict[str, Sequence[int]]) -> List[str]:
    """Return a list of structural problems (empty list = structurally valid)."""
    problems: List[str] = []
    for name, entry in ann["images"].items():
        if name not in frame_sizes:
            problems.append(f"{name}: image not found")
            continue
        w, h = frame_sizes[name]
        if (entry["width"], entry["height"]) != (w, h):
            problems.append(f"{name}: annotation size {entry['width']}x{entry['height']} != upright frame {w}x{h}")
        for kind in ("persons", "ignore"):
            boxes = []
            for i, item in enumerate(entry[kind]):
                b = item["box"]
                tag = f"{name} {kind}[{i}]"
                if len(b) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in b):
                    problems.append(f"{tag}: box is not four finite numbers")
                    continue
                if not (0 <= b[0] < b[2] <= w and 0 <= b[1] < b[3] <= h):
                    problems.append(f"{tag}: box {b} invalid or outside {w}x{h}")
                boxes.append((i, b))
            for a in range(len(boxes)):
                for c in range(a + 1, len(boxes)):
                    v = iou(boxes[a][1], boxes[c][1])
                    if boxes[a][1] == boxes[c][1]:
                        problems.append(f"{name} {kind}[{boxes[a][0]}] and [{boxes[c][0]}] are identical (duplicate)")
                    elif v > 0.5:
                        problems.append(f"{name} {kind}[{boxes[a][0]}] and [{boxes[c][0]}] overlap strongly (IoU {v:.2f}); check for an accidental duplicate")
    return problems


def _overlay(image: np.ndarray, preds, gts, ignores, m, path: Path, max_side: int = 2800) -> None:
    from PIL import Image, ImageDraw
    img = Image.fromarray(image)
    s = min(1.0, max_side / max(img.size))
    img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    lw = max(2, img.width // 700)

    def box(b, color, label):
        r = [c * s for c in b]
        d.rectangle(r, outline=color, width=lw)
        d.text((r[0] + 3, r[1] + 2), label, fill=color, stroke_width=2, stroke_fill=(0, 0, 0))

    tp_gt = {g for _, g, _ in m["tp"]}
    for i, b in enumerate(ignores):
        box(b, (170, 170, 170), f"I{i + 1}")
    for i, b in enumerate(gts):
        box(b, (0, 120, 255) if i in tp_gt else (255, 140, 0), f"G{i + 1}{'' if i in tp_gt else ' FN'}")
    kind = {p: "TP" for p, _, _ in m["tp"]}
    kind.update({p: "FP" for p in m["fp"]})
    kind.update({p: "IGN" for p in m["ignored"]})
    color = {"TP": (0, 220, 0), "FP": (255, 0, 0), "IGN": (220, 220, 0)}
    for i, p in enumerate(preds):
        k = kind[i]
        box((p.x1, p.y1, p.x2, p.y2), color[k], f"P{i + 1} {k} {p.confidence:.2f}")
    img.save(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ann_path = Path(argv[0] if argv else sys.argv[1])
    assets = ann_path.resolve().parent.parent
    out = assets / "results" / "audit"
    out.mkdir(parents=True, exist_ok=True)
    ann = json.loads(ann_path.read_text(encoding="utf-8"))
    detector = YoloxOnnxPersonDetector()
    print(f"config: score={detector.score_threshold} nms={settings.PERSON_NMS_THRESHOLD} input={detector.input_hw} iou=0.5 ignore_cover=0.5")

    frames, sizes = {}, {}
    for name in ann["images"]:
        src = ImageFileSource([assets / name]); src.start()
        frames[name] = src.read_frame()
        sizes[name] = (frames[name].width, frames[name].height)
    problems = audit_annotations(ann, sizes)
    print("structural audit:", "OK, no problems" if not problems else "PROBLEMS")
    for p in problems:
        print("  -", p)

    # EXIF / coordinate chain proof
    from PIL import Image, ImageOps
    for name in ann["images"]:
        with Image.open(assets / name) as raw:
            tag = raw.getexif().get(274)
            stored = raw.size
            upright = np.asarray(ImageOps.exif_transpose(raw).convert("RGB"))
        same = np.array_equal(upright, frames[name].image)
        print(f"exif {name}: orientation={tag} stored={stored[0]}x{stored[1]} frame={sizes[name][0]}x{sizes[name][1]} "
              f"annotation={ann['images'][name]['width']}x{ann['images'][name]['height']} frame_pixels_equal_to_one_exif_transpose={same}")

    rows, tot = [], dict(pred=0, tp=0, fp=0, ign=0, gt=0, fn=0)
    checklist = ["# Human review checklist (Week 4 Task 5B)", "",
                 "Open each overlay and check every box. Legend: blue = ground-truth person matched, orange `G# FN` = ground-truth person not matched, grey `I#` = ignore region,",
                 "green `P# TP` = detection matched, red `P# FP` = false positive, yellow `P# IGN` = detection inside an ignore region (not counted).",
                 "Review status below is for YOU to fill in. Nothing here is approved.", ""]
    for name, entry in ann["images"].items():
        gts = [p["box"] for p in entry["persons"]]
        ign = [p["box"] for p in entry["ignore"]]
        r = detector.detect(frames[name])
        m = match_detections(r.detections, gts, ign, 0.5, 0.5)
        ok_pred = len(r.detections) == len(m["tp"]) + len(m["fp"]) + len(m["ignored"])
        ok_gt = len(gts) == len(m["tp"]) + len(m["fn"])
        assert len({p for p, _, _ in m["tp"]}) == len(m["tp"]) and len({g for _, g, _ in m["tp"]}) == len(m["tp"]), "one-to-one violated"
        rows.append((name, len(r.detections), len(m["tp"]), len(m["fp"]), len(m["ignored"]), ok_pred, len(gts), len(m["fn"]), ok_gt))
        for k, v in (("pred", len(r.detections)), ("tp", len(m["tp"])), ("fp", len(m["fp"])), ("ign", len(m["ignored"])), ("gt", len(gts)), ("fn", len(m["fn"]))):
            tot[k] += v
        path = out / f"{Path(name).stem}_audit.png"
        _overlay(frames[name].image, r.detections, gts, ign, m, path)
        checklist += [f"## {Path(name).stem}", f"- GT persons: {len(gts)}  |  Ignore regions: {len(ign)}  |  Detections: {len(r.detections)}",
                      f"- Audit overlay: `{path.as_posix()}`", "- Human review: " + ("REVIEWED by the user on " + ann["review"]["date"] + "; corrections applied (see review list in the annotation file); corrected overlay may be re-checked" if ann.get("review") else "PENDING"), "- Corrections needed (box id and what is wrong): ", ""]
    print(f"\n{'image':22s} {'preds':>5s} {'TP':>3s} {'FP':>3s} {'ign':>4s} {'P=TP+FP+ign':>12s} | {'GT':>3s} {'FN':>3s} {'GT=TP+FN':>9s}")
    for n, p, tp, fp, ig, okp, g, fn, okg in rows:
        print(f"{n:22s} {p:5d} {tp:3d} {fp:3d} {ig:4d} {str(okp):>12s} | {g:3d} {fn:3d} {str(okg):>9s}")
    print(f"{'TOTAL':22s} {tot['pred']:5d} {tot['tp']:3d} {tot['fp']:3d} {tot['ign']:4d} {str(tot['pred'] == tot['tp'] + tot['fp'] + tot['ign']):>12s} | "
          f"{tot['gt']:3d} {tot['fn']:3d} {str(tot['gt'] == tot['tp'] + tot['fn']):>9s}")
    (out / "REVIEW_CHECKLIST.md").write_text("\n".join(checklist), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
