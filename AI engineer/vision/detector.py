"""Person detection for the VESTA classroom camera (Week 4, Task 1).

Backend: YOLOX (Apache-2.0) exported to ONNX, run with ONNX Runtime on
the CPU. Only the COCO "person" class is kept. The detector takes a
vision.frames.Frame and returns a DetectionResult; it knows nothing about
where the frame came from (image file today, Pi camera later) and nothing
about drawing (vision/annotate.py) or counting beyond `result.count`.

YOLOX FACTS. The input convention was settled EMPIRICALLY on the real
yolox_tiny.onnx and a real classroom photo (see WEEK4_REPORT.md, Week 4
Task 4): raw pixel values 0..255 give confident person detections, while
the ImageNet mean/std normalisation shown in the 0.1.1rc0 ONNX demo makes
the model see almost nothing (objectness <= 0.17 everywhere). Current
official YOLOX (main, 0.3.0) preproc also feeds raw cv2 BGR 0..255 and keeps
the RGB + mean/std path behind a `legacy` flag. Do not "fix" this to the
0.1.1rc0 demo convention without re-running the real-image test.
  * input  : float32 NCHW, BGR channel order (cv2 convention), values 0..255,
             no mean/std; letterboxed top-left onto a 114-valued canvas.
             Our Frame is RGB, so the channels are reversed here.
  * output : (1, N, 85) RAW grid predictions, N = sum over strides 8/16/32
             of (H/stride)*(W/stride): [cx, cy, w, h] undecoded, then
             objectness and 80 class scores already passed through sigmoid
  * score  : objectness * class score; "person" is COCO class 0
If the model file is a different export (e.g. decoded in the graph) the
output-shape check below fails loudly instead of returning wrong boxes.

Run on images:
    python -m vision.detector room.jpg [more.jpg] [--model PATH] [--annotate-dir DIR]
"""

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from config import settings
from config.logging_config import setup_logging
from vision.frames import EndOfFrames, Frame, FrameSourceError, ImageFileSource

logger = setup_logging()

PERSON_CLASS_ID = 0   # COCO class index for "person"
_STRIDES = (8, 16, 32)
_PAD_VALUE = 114.0
_NUM_CLASSES = 80


class PersonDetectorError(RuntimeError):
    """Base class for detector problems (not about the picture content)."""


class ModelFileMissing(PersonDetectorError):
    """The ONNX model file was not found."""


class DetectorOutputError(PersonDetectorError):
    """The model returned something other than the expected YOLOX layout."""


@dataclass(frozen=True)
class Detection:
    """One person in a frame. Box is pixels of the ORIGINAL frame, (x1, y1) top-left."""

    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    frame_width: int
    frame_height: int
    label: str = "person"
    class_id: int = PERSON_CLASS_ID

    def __post_init__(self) -> None:
        if not (0.0 <= self.x1 < self.x2 <= self.frame_width and 0.0 <= self.y1 < self.y2 <= self.frame_height):
            raise ValueError(
                f"box ({self.x1}, {self.y1}, {self.x2}, {self.y2}) is not a non-empty box "
                f"inside a {self.frame_width}x{self.frame_height} frame"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in 0..1, got {self.confidence}")


@dataclass(frozen=True)
class DetectionResult:
    detections: List[Detection]
    frame_index: int
    frame_source: str
    frame_width: int
    frame_height: int
    inference_ms: float   # ONNX Runtime session.run() only
    preprocess_ms: float  # letterbox + tensor build
    postprocess_ms: float # decode + person filter + NMS
    total_ms: float       # preprocess + inference + postprocess
    model: str            # model file name
    raw_candidates: int   # candidates above the score threshold, before NMS

    @property
    def count(self) -> int:
        """Number of person detections. A detection count only: not a verified
        student count, not occupancy, not unique people (no tracking)."""
        return len(self.detections)


# -- pure numpy pre/post-processing (unit-testable without a model) ---------

def letterbox(rgb: np.ndarray, input_hw: Tuple[int, int]) -> Tuple[np.ndarray, float]:
    """Resize keeping aspect ratio onto a 114 canvas; return (NCHW BGR float32 0..255, ratio)."""
    from PIL import Image

    in_h, in_w = input_hw
    h, w = rgb.shape[:2]
    ratio = min(in_h / h, in_w / w)
    new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
    resized = np.asarray(Image.fromarray(rgb).resize((new_w, new_h), Image.BILINEAR), dtype=np.float32)
    canvas = np.full((in_h, in_w, 3), _PAD_VALUE, dtype=np.float32)
    canvas[:new_h, :new_w] = resized
    bgr = canvas[:, :, ::-1]                       # YOLOX uses cv2 (BGR) images, raw 0..255, no normalisation
    return np.ascontiguousarray(bgr.transpose(2, 0, 1), dtype=np.float32)[None], ratio


def _grid(input_hw: Tuple[int, int]):
    grids, strides = [], []
    for s in _STRIDES:
        hs, ws = input_hw[0] // s, input_hw[1] // s
        xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((hs * ws, 1), s))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
    order = scores.argsort()[::-1]
    keep: List[int] = []
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


def postprocess_yolox(
    raw: np.ndarray,
    input_hw: Tuple[int, int],
    ratio: float,
    frame_wh: Tuple[int, int],
    score_threshold: float,
    nms_threshold: float,
) -> Tuple[List[Detection], int]:
    """Decode raw YOLOX output into person Detections. Returns (detections, candidates before NMS)."""
    grid, stride = _grid(input_hw)
    expected = (1, grid.shape[0], 5 + _NUM_CLASSES)
    if raw.shape != expected:
        raise DetectorOutputError(
            f"expected YOLOX raw output of shape {expected} for input {input_hw}, got {raw.shape}"
        )
    out = raw[0]
    cxcy = (out[:, 0:2] + grid) * stride
    wh = np.exp(out[:, 2:4]) * stride
    scores = out[:, 4] * out[:, 5 + PERSON_CLASS_ID]      # objectness * P(person)
    keep = scores >= score_threshold
    candidates = int(keep.sum())
    if candidates == 0:
        return [], 0

    cxcy, wh, scores = cxcy[keep], wh[keep], scores[keep]
    boxes = np.concatenate((cxcy - wh / 2, cxcy + wh / 2), axis=1) / ratio
    fw, fh = frame_wh
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, fw)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, fh)

    detections = []
    for i in _nms(boxes, scores, nms_threshold):
        x1, y1, x2, y2 = (float(v) for v in boxes[i])
        if x2 - x1 < 1.0 or y2 - y1 < 1.0:   # clipped away / degenerate
            continue
        detections.append(Detection(x1, y1, x2, y2, float(min(scores[i], 1.0)), fw, fh))
    return detections, candidates


# -- the detector -----------------------------------------------------------

def resolve_model_path(path: str = settings.PERSON_MODEL_PATH) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(__file__).resolve().parent.parent / p


class YoloxOnnxPersonDetector:
    """Person detector: YOLOX ONNX on ONNX Runtime CPU."""

    def __init__(
        self,
        model_path: str = settings.PERSON_MODEL_PATH,
        score_threshold: float = settings.PERSON_SCORE_THRESHOLD,
        nms_threshold: float = settings.PERSON_NMS_THRESHOLD,
        *,
        session=None,
    ) -> None:
        """session: ready-made onnxruntime-like object (get_inputs/get_outputs/run).
        Test hook, so the logic runs without a model file; leave None in production."""
        if not 0.0 < score_threshold <= 1.0:
            raise ValueError(f"score_threshold must be in (0, 1], got {score_threshold}")
        if not 0.0 < nms_threshold <= 1.0:
            raise ValueError(f"nms_threshold must be in (0, 1], got {nms_threshold}")
        self._score_threshold = score_threshold
        self._nms_threshold = nms_threshold
        self._model_path = resolve_model_path(model_path)

        if session is None:
            if not self._model_path.is_file():
                logger.error("event=person_model_missing path=%s", self._model_path)
                raise ModelFileMissing(
                    f"person model not found at {str(self._model_path)!r}. Place yolox_tiny.onnx there "
                    "(see WEEK4_REPORT.md) or pass model_path=..."
                )
            import onnxruntime as ort  # lazy, like the openWakeWord backend
            session = ort.InferenceSession(str(self._model_path), providers=["CPUExecutionProvider"])
        self._session = session

        shape = session.get_inputs()[0].shape            # e.g. [1, 3, 416, 416]
        if len(shape) != 4 or not all(isinstance(d, int) for d in shape[1:]) or shape[1] != 3:
            raise DetectorOutputError(f"expected a static NCHW input with 3 channels, got {shape}")
        self._input_name = session.get_inputs()[0].name
        self._input_hw = (int(shape[2]), int(shape[3]))
        logger.info(
            "event=person_detector_ready model=%s input_hw=%s providers=%s score_threshold=%.2f nms_threshold=%.2f",
            self._model_path.name, self._input_hw,
            getattr(session, "get_providers", lambda: ["unknown"])(),
            score_threshold, nms_threshold,
        )

    @property
    def score_threshold(self) -> float:
        return self._score_threshold

    @property
    def input_hw(self) -> Tuple[int, int]:
        return self._input_hw

    def detect(self, frame: Frame) -> DetectionResult:
        t0 = time.perf_counter()
        blob, ratio = letterbox(frame.image, self._input_hw)
        t1 = time.perf_counter()
        try:
            raw = self._session.run(None, {self._input_name: blob})[0]
        except Exception as exc:  # never answer "no people" when inference failed
            logger.error("event=person_inference_failed frame=%d error=%s", frame.index, exc)
            raise PersonDetectorError(f"inference failed on frame {frame.index}: {exc}") from exc
        t2 = time.perf_counter()
        detections, candidates = postprocess_yolox(
            np.asarray(raw), self._input_hw, ratio, (frame.width, frame.height),
            self._score_threshold, self._nms_threshold,
        )
        t3 = time.perf_counter()
        return DetectionResult(
            detections=detections, frame_index=frame.index, frame_source=frame.source,
            frame_width=frame.width, frame_height=frame.height,
            inference_ms=(t2 - t1) * 1000, preprocess_ms=(t1 - t0) * 1000,
            postprocess_ms=(t3 - t2) * 1000, total_ms=(t3 - t0) * 1000,
            model=self._model_path.name, raw_candidates=candidates,
        )


# -- command line: python -m vision.detector img.jpg ------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Detect people in image files; print one line per frame. 0 = ran, 2 = unusable setup/input."""
    parser = argparse.ArgumentParser(prog="python -m vision.detector", description=main.__doc__)
    parser.add_argument("images", nargs="+")
    parser.add_argument("--model", default=settings.PERSON_MODEL_PATH)
    parser.add_argument("--threshold", type=float, default=settings.PERSON_SCORE_THRESHOLD)
    parser.add_argument("--annotate-dir", help="also save copies with boxes drawn here")
    args = parser.parse_args(argv)

    try:
        detector = YoloxOnnxPersonDetector(args.model, score_threshold=args.threshold)
        source = ImageFileSource(args.images)
    except (PersonDetectorError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.annotate_dir) if args.annotate_dir else None
    if out_dir:
        from vision.annotate import save_annotated
        out_dir.mkdir(parents=True, exist_ok=True)

    source.start()
    try:
        while True:
            try:
                frame = source.read_frame()
                result = detector.detect(frame)
            except EndOfFrames:
                break
            except (FrameSourceError, PersonDetectorError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            print(
                f"frame={result.frame_index} source={result.frame_source} size={result.frame_width}x{result.frame_height} "
                f"model={result.model} threshold={detector.score_threshold} candidates={result.raw_candidates} "
                f"person_detections={result.count} inference={result.inference_ms:.1f}ms total={result.total_ms:.1f}ms"
            )
            if out_dir:
                save_annotated(frame, result.detections, out_dir / f"annotated_{frame.index:04d}.png")
    finally:
        source.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
