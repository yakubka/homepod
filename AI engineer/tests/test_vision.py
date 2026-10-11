"""Week 4 Task 1: frame sources and the YOLOX person-detector logic.

Everything here except the last section runs WITHOUT a model file and
without real photos. The tests that feed hand-built raw tensors or a fake
ONNX session (SIMULATED) check our decode / filter / count logic and error
handling. They say nothing about detection quality: that needs the real
model and labelled classroom images (see the smoke tests at the bottom,
which skip unless both are supplied).
"""

import os
from pathlib import Path

import numpy as np
import pytest

from vision.annotate import draw_detections
from vision.detector import (
    Detection,
    DetectorOutputError,
    ModelFileMissing,
    PersonDetectorError,
    YoloxOnnxPersonDetector,
    _grid,
    letterbox,
    main,
    postprocess_yolox,
    resolve_model_path,
)
from vision.frames import ArrayFrameSource, EndOfFrames, Frame, FrameSourceError, ImageFileSource

HW = (416, 416)
N_CELLS = _grid(HW)[0].shape[0]   # 3549 for 416x416


# -- helpers: hand-built raw YOLOX output (SIMULATED) -------------------------

def raw_output(entries):
    """entries: (cell, cx_off, cy_off, w_log, h_log, objectness, person_score, other_class_score)."""
    raw = np.zeros((1, N_CELLS, 85), dtype=np.float32)
    for cell, ox, oy, lw, lh, obj, person, other in entries:
        raw[0, cell, :4] = (ox, oy, lw, lh)
        raw[0, cell, 4] = obj
        raw[0, cell, 5] = person
        raw[0, cell, 6] = other   # class 1 (bicycle)
    return raw


def cell_at(x_cell, y_cell):   # stride-8 grid is first: 52 x 52 cells
    return y_cell * 52 + x_cell


# A person centred at (cell 10,10)*8 = (80,80) px, w=h=exp(ln 5)*8 = 40 px
PERSON = (cell_at(10, 10), 0.0, 0.0, np.log(5.0), np.log(5.0), 0.9, 0.9, 0.0)


class FakeSession:
    """Quacks like onnxruntime.InferenceSession."""

    class _Item:
        def __init__(self, name, shape):
            self.name, self.shape = name, shape

    def __init__(self, raw=None, fail=False, shape=(1, 3, 416, 416)):
        self._raw, self._fail, self._shape = raw, fail, list(shape)
        self.fed = None

    def get_inputs(self):
        return [self._Item("images", self._shape)]

    def get_providers(self):
        return ["FakeCPU"]

    def run(self, _outputs, feed):
        if self._fail:
            raise RuntimeError("simulated runtime failure")
        self.fed = feed["images"]
        return [self._raw if self._raw is not None else raw_output([])]


def frame(w=640, h=480, index=0):
    return Frame(image=np.zeros((h, w, 3), dtype=np.uint8), index=index, source="array_fixture")


# -- Detection record ---------------------------------------------------------

def test_detection_accepts_a_valid_box():
    d = Detection(10, 20, 50, 80, 0.8, frame_width=640, frame_height=480)
    assert (d.label, d.class_id) == ("person", 0)


@pytest.mark.parametrize("box", [(50, 20, 10, 80), (10, 80, 50, 20), (10, 20, 700, 80), (-1, 0, 5, 5), (5, 5, 5, 9)])
def test_detection_rejects_invalid_boxes(box):
    with pytest.raises(ValueError):
        Detection(*box, 0.8, frame_width=640, frame_height=480)


@pytest.mark.parametrize("conf", [-0.1, 1.1])
def test_detection_rejects_bad_confidence(conf):
    with pytest.raises(ValueError):
        Detection(0, 0, 10, 10, conf, frame_width=64, frame_height=64)


# -- preprocessing --------------------------------------------------------------

def test_letterbox_gives_bgr_raw_0_255_input():
    """Empirically validated on the real model + a real photo (WEEK4_REPORT.md, Task 4): BGR order,
    raw 0..255 pixels, NO mean/std normalisation, 114 padding, top-left placement."""
    rgb = np.zeros((100, 200, 3), dtype=np.uint8)
    rgb[..., 0] = 255                       # pure red in RGB
    blob, ratio = letterbox(rgb, HW)
    assert blob.shape == (1, 3, 416, 416) and blob.dtype == np.float32
    assert ratio == pytest.approx(2.08)     # min(416/100, 416/200)
    assert blob[0, 2, 10, 10] == 255        # red lands in the LAST (BGR) channel
    assert blob[0, 0, 10, 10] == 0
    assert blob[0, 0, 415, 415] == 114      # padding value, bottom-right corner
    assert blob.min() >= 0 and blob.max() <= 255   # a normalised tensor would go negative


# -- postprocess: decode, person filter, threshold, NMS, clipping -------------

def test_decodes_a_person_box_in_frame_pixels():
    dets, cand = postprocess_yolox(raw_output([PERSON]), HW, 1.0, (416, 416), 0.3, 0.45)
    assert cand == 1 and len(dets) == 1
    d = dets[0]
    assert (d.x1, d.y1, d.x2, d.y2) == pytest.approx((60, 60, 100, 100))
    assert d.confidence == pytest.approx(0.81)       # objectness * class score


def test_box_is_rescaled_by_the_letterbox_ratio():
    dets, _ = postprocess_yolox(raw_output([PERSON]), HW, 0.5, (832, 832), 0.3, 0.45)
    assert (dets[0].x1, dets[0].x2) == pytest.approx((120, 200))


def test_non_person_classes_are_ignored():
    other = (cell_at(5, 5), 0, 0, 1.0, 1.0, 0.9, 0.0, 0.95)   # strong "bicycle", zero person
    dets, cand = postprocess_yolox(raw_output([other]), HW, 1.0, (416, 416), 0.3, 0.45)
    assert dets == [] and cand == 0


def test_score_threshold_is_applied():
    weak = (cell_at(20, 20), 0, 0, 1.0, 1.0, 0.5, 0.5, 0)      # score 0.25
    assert postprocess_yolox(raw_output([weak]), HW, 1.0, (416, 416), 0.3, 0.45)[0] == []
    assert len(postprocess_yolox(raw_output([weak]), HW, 1.0, (416, 416), 0.2, 0.45)[0]) == 1


def test_nms_merges_duplicates_but_keeps_separate_people():
    duplicate = (cell_at(11, 10), 0.0, 0.0, np.log(5.0), np.log(5.0), 0.8, 0.8, 0)   # 8 px right
    apart = (cell_at(40, 40), 0.0, 0.0, np.log(5.0), np.log(5.0), 0.7, 0.9, 0)
    dets, cand = postprocess_yolox(raw_output([PERSON, duplicate, apart]), HW, 1.0, (416, 416), 0.3, 0.45)
    assert cand == 3 and len(dets) == 2
    assert dets[0].confidence == pytest.approx(0.81)


def test_boxes_are_clipped_to_the_frame():
    edge = (cell_at(0, 0), 0, 0, np.log(5.0), np.log(5.0), 0.9, 0.9, 0)   # centre (0,0): half outside
    d = postprocess_yolox(raw_output([edge]), HW, 1.0, (416, 416), 0.3, 0.45)[0][0]
    assert (d.x1, d.y1) == (0.0, 0.0)


def test_empty_output_gives_no_detections():
    assert postprocess_yolox(raw_output([]), HW, 1.0, (416, 416), 0.3, 0.45) == ([], 0)


def test_unexpected_output_layout_fails_loudly():
    with pytest.raises(DetectorOutputError):
        postprocess_yolox(np.zeros((1, 100, 85), dtype=np.float32), HW, 1.0, (416, 416), 0.3, 0.45)


# -- detector with a fake session (SIMULATED) ---------------------------------

def test_detect_returns_structured_result_and_count():
    session = FakeSession(raw_output([PERSON]))
    result = YoloxOnnxPersonDetector(session=session).detect(frame(416, 416, index=7))
    assert result.count == 1 and result.frame_index == 7
    assert (result.frame_width, result.frame_height) == (416, 416)
    assert result.inference_ms >= 0 and result.total_ms >= result.inference_ms
    assert session.fed.shape == (1, 3, 416, 416)


def test_no_person_gives_count_zero():
    assert YoloxOnnxPersonDetector(session=FakeSession()).detect(frame()).count == 0


def test_threshold_is_configurable_and_validated():
    session = FakeSession(raw_output([(cell_at(20, 20), 0, 0, 1.0, 1.0, 0.5, 0.5, 0)]))
    assert YoloxOnnxPersonDetector(score_threshold=0.2, session=session).detect(frame(416, 416)).count == 1
    assert YoloxOnnxPersonDetector(score_threshold=0.4, session=session).detect(frame(416, 416)).count == 0
    with pytest.raises(ValueError):
        YoloxOnnxPersonDetector(score_threshold=0, session=session)


def test_inference_failure_is_raised_not_reported_as_no_people():
    with pytest.raises(PersonDetectorError):
        YoloxOnnxPersonDetector(session=FakeSession(fail=True)).detect(frame())


def test_dynamic_input_shape_is_rejected():
    with pytest.raises(DetectorOutputError):
        YoloxOnnxPersonDetector(session=FakeSession(shape=(1, 3, "h", "w")))


def test_missing_model_file_is_an_explicit_error(tmp_path):
    with pytest.raises(ModelFileMissing):
        YoloxOnnxPersonDetector(model_path=str(tmp_path / "nope.onnx"))


def test_relative_model_path_resolves_from_project_root_not_cwd():
    assert resolve_model_path("models/x.onnx").parts[-2:] == ("models", "x.onnx")
    assert resolve_model_path("models/x.onnx").is_absolute()


# -- frame sources ------------------------------------------------------------

def test_array_source_replays_then_ends():
    src = ArrayFrameSource([np.zeros((4, 6, 3), np.uint8)] * 2)
    src.start()
    assert [src.read_frame().index for _ in range(2)] == [0, 1]
    with pytest.raises(EndOfFrames):
        src.read_frame()


def test_frame_rejects_wrong_dtype_or_shape():
    with pytest.raises(FrameSourceError):
        Frame(np.zeros((4, 6, 3), np.float32), 0, "x")
    with pytest.raises(FrameSourceError):
        Frame(np.zeros((4, 6), np.uint8), 0, "x")


def test_image_file_source_reads_an_image_file(tmp_path):
    from PIL import Image
    p = tmp_path / "synthetic.png"      # synthetic gradient: interface test only, contains no person
    Image.fromarray(np.tile(np.arange(30, dtype=np.uint8), (20, 1))[..., None].repeat(3, 2)).save(p)
    src = ImageFileSource([p])
    src.start()
    f = src.read_frame()
    assert (f.width, f.height, f.source) == (30, 20, "image_file:synthetic.png")
    with pytest.raises(EndOfFrames):
        src.read_frame()


def test_image_file_source_honours_exif_orientation(tmp_path):
    from PIL import Image
    p = tmp_path / "sideways.jpg"            # synthetic: stored 30 wide x 20 high, EXIF says rotate 90 degrees
    exif = Image.Exif()
    exif[274] = 6
    Image.fromarray(np.zeros((20, 30, 3), np.uint8)).save(p, exif=exif)
    src = ImageFileSource([p])
    src.start()
    f = src.read_frame()
    assert (f.width, f.height) == (20, 30)


def test_image_file_source_missing_or_corrupt_file_is_an_explicit_error(tmp_path):
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"not an image")
    for path in (tmp_path / "missing.png", bad):
        with pytest.raises(FrameSourceError):
            ImageFileSource([path]).read_frame()


def test_image_file_source_requires_paths():
    with pytest.raises(ValueError):
        ImageFileSource([])


# -- annotation and CLI -------------------------------------------------------

def test_annotation_draws_on_a_copy_only():
    img = np.zeros((50, 50, 3), np.uint8)
    out = draw_detections(img, [Detection(10, 10, 40, 40, 0.9, 50, 50)])
    assert img.sum() == 0 and out.sum() > 0 and out.shape == img.shape


def test_cli_exits_2_when_model_missing(tmp_path, capsys):
    assert main([str(tmp_path / "a.png"), "--model", str(tmp_path / "nope.onnx")]) == 2
    assert "person model not found" in capsys.readouterr().err


# -- REAL model / REAL image smoke tests (skip unless supplied) ----------------

REAL_MODEL = resolve_model_path(os.environ.get("VESTA_PERSON_MODEL", "models/yolox_tiny.onnx"))
_DEFAULT_IMAGES = [resolve_model_path(f"vision_assets/{n}") for n in ("classroom_smoke_01.jpg", "classroom_eval_01.jpg")]   # git-ignored local assets (same photograph, renamed)
REAL_IMAGE = os.environ.get("VESTA_PERSON_TEST_IMAGE") or next((str(p) for p in _DEFAULT_IMAGES if p.is_file()), None)

needs_model = pytest.mark.skipif(not REAL_MODEL.is_file(), reason=f"model file not found: {REAL_MODEL}")


@needs_model
def test_real_model_runs_on_a_blank_frame_without_error():
    """Interface/shape check only. A blank image is NOT evidence of detection quality."""
    result = YoloxOnnxPersonDetector(str(REAL_MODEL)).detect(frame(640, 480))
    assert result.count == 0 and result.inference_ms > 0


@needs_model
def test_real_model_contract_matches_the_detector_assumptions():
    """Checks the real ONNX file against what vision/detector.py assumes (SOFTWARE-VALIDATED)."""
    import onnxruntime as ort
    session = ort.InferenceSession(str(REAL_MODEL), providers=["CPUExecutionProvider"])
    (inp,), (out,) = session.get_inputs(), session.get_outputs()
    assert (inp.name, inp.shape, inp.type) == ("images", [1, 3, 416, 416], "tensor(float)")
    assert out.shape == [1, N_CELLS, 85] and out.type == "tensor(float)"

    rng = np.random.default_rng(0)
    blob, _ = letterbox(rng.integers(0, 256, (480, 640, 3), dtype=np.uint8), HW)
    raw = session.run(None, {inp.name: blob})[0]
    assert raw.shape == (1, N_CELLS, 85) and np.isfinite(raw).all()
    # objectness and the 80 class scores must already be probabilities (sigmoid applied in the graph),
    # which is what postprocess_yolox multiplies; the decoder would be wrong on raw logits.
    assert 0.0 <= raw[0, :, 4:].min() and raw[0, :, 4:].max() <= 1.0


@needs_model
@pytest.mark.skipif(not REAL_IMAGE, reason="set VESTA_PERSON_TEST_IMAGE (or place vision_assets/classroom_eval_01.jpg) - a permitted image with at least one person")
def test_real_model_finds_a_person_in_a_real_image():
    """SOFTWARE-VALIDATED smoke test on ONE permitted image. Not accuracy, not a student count,
    not Pi performance. Deliberately asserts only structure, never an exact number of people."""
    import math
    src = ImageFileSource([REAL_IMAGE])
    src.start()
    result = YoloxOnnxPersonDetector(str(REAL_MODEL)).detect(src.read_frame())
    assert result.count >= 1, f"no person found; candidates above threshold: {result.raw_candidates}"
    assert result.count == len(result.detections) and result.raw_candidates >= result.count
    for d in result.detections:
        assert all(math.isfinite(v) for v in (d.x1, d.y1, d.x2, d.y2, d.confidence))
        assert 0.0 <= d.x1 < d.x2 <= result.frame_width and 0.0 <= d.y1 < d.y2 <= result.frame_height
        assert d.confidence >= 0.30   # configured score threshold
        assert (d.label, d.class_id) == ("person", 0)
