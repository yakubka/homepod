# VESTA — Week 4 Report: Computer Vision Foundation (Task 1)

Run date: 2026-10-02. Host: Windows 11, AMD Ryzen 5 7520U (4 cores / 8 threads), integrated AMD Radeon GPU (not used), 15.3 GB RAM, Python 3.13.14. Status: **IN PROGRESS**. The text below is the state at the end of Task 1; where it differs, the Task 2 section at the end of this file supersedes it (the model has since been downloaded and run).

## Objective

Build the smallest defensible camera-perception foundation against a stable frame interface, so the real Raspberry Pi camera can replace the offline source without touching the detector:

`FrameSource -> YoloxOnnxPersonDetector -> DetectionResult`

## Starting state

No vision, camera or model code; no image or video assets in the repository; no OpenCV, torch or ultralytics. `onnxruntime 1.30.0` and `numpy 2.5.3` already installed. Baseline: 216 passed, 8 skipped, 0 failed.

## Detector options considered (for a Raspberry Pi 5 CPU, no measurements made)

| Option | Pi 5 CPU fit (expectation only) | Model size | Install weight | Person quality | Licence | Verdict |
|---|---|---|---|---|---|---|
| A. Ultralytics YOLOv8n / YOLO11n | Good once exported to ONNX | about 6 MB | Heavy (pulls torch, torchvision, OpenCV) unless exported elsewhere | Strong | AGPL-3.0 code and weights: awkward for sharing a prototype | Rejected: licence and dependency weight |
| B. ONNX Runtime + YOLOX-Tiny | Plausible; small model at 416x416 | 20,219,662 bytes (checked from the GitHub release metadata) | None new: onnxruntime already installed; Pillow only | Good for COCO persons; weaker on small, distant, occluded people than larger models | Apache-2.0 (checked from the repository metadata) | **Selected** |
| B'. YOLOX-Nano | Faster | 3,659,407 bytes | same | Clearly weaker | Apache-2.0 | Fallback if Pi speed is a problem |
| B''. YOLOX-S (640) | Slower | 35,858,002 bytes | same | Better | Apache-2.0 | Candidate if Tiny is too weak (Week 5 evaluation) |
| C. torchvision (SSDlite / Faster R-CNN) | Poor to fair; needs torch on the Pi | 13 MB to 160 MB | Heavy (torch) | Fair to strong | BSD | Rejected: dependency weight |
| D. OpenCV DNN / HOG | HOG light but weak; DNN needs OpenCV | small | OpenCV (a native binary that Windows Application Control may block) | HOG poor | Apache-2.0 | Rejected |

Raspberry Pi speed for any option is an open concern to be **measured on the Pi**, not a result.

## Selected baseline

YOLOX-Tiny (`yolox_tiny.onnx`, 416x416) through ONNX Runtime CPU. Reasons: no new heavy dependency, Apache-2.0, one small file, ONNX Runtime has Linux aarch64 builds, and the model can be swapped by changing a path (input size is read from the model, not hard-coded).

- Input (final, settled in Task 4 on a real image; Task 3 had changed this wrongly and was reverted): float32 NCHW, BGR, raw 0..255, no normalisation, letterboxed on a 114 canvas. Output: raw `(1, 3549, 85)` grid predictions; score = objectness x class score; COCO class 0 = person. These facts come from the official YOLOX ONNX demo and were not checked against the real file in Task 1; Task 2 checked shape, dtype and score ranges (see below). The detector checks the output shape and fails loudly if it differs.
- Limitations: COCO-trained (not classroom-trained); small, distant and occluded people are the expected weak spot; Pillow bilinear resize is not identical to the OpenCV resize used in the reference preprocessing, so scores may differ slightly; thresholds are untuned starting values.

## Dependencies

Added: `pillow==12.3.0` (decode/resize/draw; confirmed to import under this PC's Windows Application Control). `onnxruntime==1.30.0` was already pinned. OpenCV, torch and ultralytics were **not** installed. The model file is not in the repository (`*.onnx` is git-ignored) and was not downloaded in Task 1 (downloaded in Task 2).

## Architecture added

| File | Purpose |
|---|---|
| `vision/frames.py` | `Frame` (RGB uint8 + index + source), `FrameSource` interface, `ImageFileSource` (offline images), `ArrayFrameSource` (test fixture), `EndOfFrames`, `FrameSourceError` |
| `vision/detector.py` | `Detection` (pixel box in the original frame, confidence, label/class, frame size), `DetectionResult` (detections, `count`, timings, model, frame info), `letterbox`, `postprocess_yolox`, `YoloxOnnxPersonDetector`, a `python -m vision.detector` command |
| `vision/annotate.py` | draws boxes on a copy of a frame (demo support; the detector never calls it) |
| `config/settings.py` (modified) | `PERSON_MODEL_PATH`, `PERSON_SCORE_THRESHOLD` (0.30), `PERSON_NMS_THRESHOLD` (0.45) |
| `tests/test_vision.py` | 33 tests |

A Pi camera backend is a second `FrameSource`; nothing else changes. Deliberately **not** built: tracking, zones, ROI, fusion, pose, faces, a detector interface/factory (one backend exists), a camera backend.

## Behaviour implemented

`detect(frame)` letterboxes, runs the ONNX session, decodes the raw grid output, keeps only the person class above the score threshold, applies NMS, maps boxes back to original-frame pixels, clips them to the frame, and returns a `DetectionResult`. `result.count` is the number of person detections: a **detection count only**, not a verified student count, occupancy or a unique-people number. Failures are explicit: missing model file, unreadable image, unexpected output layout, dynamic input shape and runtime inference errors all raise and are logged; an inference failure is never reported as "no people".

## ROI

Not implemented. A normalized (0..1) rectangle or polygon would be a reasonable mechanism, but no real classroom view exists to say what shape is needed, and the mechanism and the calibration are separate things. Deferred until a real or representative classroom image exists.

## Tests

`tests/test_vision.py`: 31 passed, 2 skipped. Coverage: Detection validation; letterbox shape, ratio, padding and BGR order; decode to frame pixels; letterbox-ratio rescale; non-person class ignored; threshold; NMS merges duplicates but keeps separate people; clipping; empty output; wrong output layout; result structure and count; configurable and validated threshold; inference failure raised; dynamic shape rejected; missing model; path resolution; frame sources including missing and corrupt files; annotation copies; CLI error exit. Four deliberate code mutations (no BGR flip, no NMS suppression, ignoring the class score, wrong ratio direction) were each caught by the tests.

**Label: SIMULATED / SOFTWARE-VALIDATED logic.** The decode tests use hand-built tensors and a fake session. They check our logic, **not detection quality**.

Skipped (2): `test_real_model_runs_on_a_blank_frame_without_error` (model file absent) and `test_real_model_finds_a_person_in_a_real_image` (needs `VESTA_PERSON_TEST_IMAGE` and the model).

Full suite: **247 passed, 10 skipped, 0 failed** (216 + 31 passed; 8 + 2 skipped). The 8 older skips are unchanged: 4 ReSpeaker hardware-gated and 4 openWakeWord real-engine tests blocked by Windows Application Control (see `WEEK3_REPORT.md`).

## Real-model smoke test (Task 1 state)

**NOT PERFORMED.** Two inputs are missing: the model file (not downloaded without approval) and a permitted image containing people (none in the repository; none fabricated). No detections, latency or FPS exist. No positive screenshot exists.

## Performance measurements (Task 1 state)

None. No real inference has run. No Raspberry Pi figure exists, and a Windows figure would not stand in for one.

## Needs your approval (Task 1 state; the model was approved and downloaded in Task 2, the image is still outstanding)

1. Download `yolox_tiny.onnx` (20,219,662 bytes) from the official YOLOX release `0.1.1rc0` at `github.com/Megvii-BaseDetection/YOLOX` into `models/` (git-ignored). This enables the blank-frame run, which would confirm the input name and the `(1, 3549, 85)` output assumption.
2. A permitted image containing one or more people, placed under `vision_assets/` (git-ignored) or pointed to by `VESTA_PERSON_TEST_IMAGE`, for the positive smoke test. Use someone's consent or a freely licensed picture; classroom-like is better.

Then run: `python -m vision.detector vision_assets/room.jpg --annotate-dir vision_assets/out`.

## Validation classification

| Item | Level |
|---|---|
| Frame sources, Detection records, decode/filter/NMS/count logic, error handling | SOFTWARE-VALIDATED (synthetic fixtures) |
| Fake-session detector flow | SIMULATED |
| Real YOLOX model loading and output layout | NOT TESTED |
| Person detection on a real image | NOT TESTED |
| Latency / FPS | NOT MEASURED |
| Raspberry Pi performance | NOT TESTED |
| Real VESTA camera | NOT AVAILABLE; nothing HARDWARE-VALIDATED |
| Counting or detection accuracy | NOT MEASURED |

## Deferred Week 4 work

Real camera `FrameSource` (needs the camera model and interface); classroom ROI; real-model smoke test; performance measurement on this PC and on the Pi; broader evaluation on classroom-like images; threshold selection (Week 5 evaluation). Tracking, three-zone assignment and student counting accuracy belong to Week 5; fusion to Week 6.

## Limitations

Detection counts only; thresholds untuned; COCO model not trained on classrooms; no real image has been processed; Windows results do not transfer to the Pi.

---

# Week 4 Task 2 — Real YOLOX-Tiny model contract and software inference validation

Run date: 2026-10-02. Same Windows 11 host as Task 1. Status: **Week 4 IN PROGRESS**.

## Baseline before Task 2

`pytest -q -rs`: 247 passed, 10 skipped, 0 failed. No model file and no image existed (`models/`, `vision_assets/` absent; `VESTA_PERSON_TEST_IMAGE` and `VESTA_PERSON_MODEL` unset).

## Model reproducibility record

| Field | Value |
|---|---|
| Model | YOLOX-Tiny (COCO, 80 classes) |
| Artifact | `yolox_tiny.onnx` |
| Source | official `Megvii-BaseDetection/YOLOX` GitHub release, tag `0.1.1rc0` |
| URL | `https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx` |
| Downloaded | 2026-10-02 (HTTP 200) |
| Size | 20,219,662 bytes (identical to the size in the release metadata) |
| SHA-256 (computed from the local file) | `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7` |
| Published checksum | none: the release metadata has no digest, so the hash could not be compared with a published value; the hash is the project's own record of the file used |
| Local location | `models/yolox_tiny.onnx` (git-ignored via `*.onnx`; not committed) |
| File header | ONNX protobuf, producer `pytorch` |
| Runtime | onnxruntime 1.30.0, Python 3.13.14, numpy 2.5.3, Pillow 12.3.0 |
| Providers | available: `AzureExecutionProvider`, `CPUExecutionProvider`; **used: `CPUExecutionProvider`** (the Radeon GPU is not used) |
| Score threshold / NMS | 0.30 / 0.45 (untuned starting values) |
| Classification | SOFTWARE-VALIDATED for real model loading and execution; not accuracy-validated |

## Model contract check

| Field | Expected | Observed | Match |
|---|---|---|---|
| Input name | `images` (any; read from the model) | `images` | yes |
| Input shape | `[1, 3, 416, 416]` static NCHW | `[1, 3, 416, 416]` | yes |
| Input dtype | float32 | `tensor(float)` | yes |
| Output count | 1 | 1 (`output`) | yes |
| Output shape | `(1, 3549, 85)` | `[1, 3549, 85]` | yes (3549 = 52² + 26² + 13²) |
| Output dtype | float32 | `tensor(float)` | yes |
| Classes | 80 (85 = 4 box + 1 objectness + 80) | 80 | yes |
| Objectness and class scores already in 0..1 | yes | on blank, noise and gradient frames: objectness within 0 to 0.0066, class scores within 0 to 0.73, all finite | consistent with sigmoid applied in the graph |
| Box regression raw (undecoded) | yes | width/height log-values between -1.4 and 3.9 | consistent |
| Provider | CPU | `CPUExecutionProvider` | yes |

**Implementation correction required: none.** Shape, dtype, layout and score ranges all agree with the Task 1 assumptions.

**Assumptions still NOT verified** because they need a person in the image: BGR channel order, the letterbox and ratio restoration, grid decode and box coordinates, class index 0 = person, and NMS on real overlaps. These are covered only by the synthetic-tensor tests. The new real-model tests do not prove them.

## Negative real-model test (SOFTWARE-VALIDATED, not a detection test)

Frame (blank 640x480 RGB) -> letterbox -> real ONNX Runtime session -> real inference -> decode -> `DetectionResult`: completed; 0 candidates and 0 detections on all 30 measured runs; the CPU provider was used. A blank frame producing zero people is not evidence of detection quality.

## Positive person test

**NOT PERFORMED — permitted human-containing image unavailable.** No image exists in the repository, `vision_assets/` does not exist, and `VESTA_PERSON_TEST_IMAGE` is unset. Nothing was downloaded or invented. The test `test_real_model_finds_a_person_in_a_real_image` stays skipped until an image is supplied.

## Timing (this Windows PC only; command: `python -m vision.benchmark --runs 30`)

Method: session created once; first inference (cold) reported separately; 1 warm-up discarded; 30 measured runs on one fixed blank 640x480 frame; `inference_ms` is `session.run()` only; `total_pipeline_ms` adds letterbox and decode. Laptop on a normal desktop session with other processes running, so figures vary run to run.

| Run | Init (ms) | First inference (ms) | Mean | Median | Min | Max | Stdev |
|---|---|---|---|---|---|---|---|
| A (ad-hoc script, same code path) | 147.9 | 246.3 | 74.9 | 63.3 | 53.8 | 257.3 | 39.3 |
| B (`vision.benchmark`) | 294.8 | 63.5 | 61.1 | 62.5 | 39.9 | 84.3 | 9.2 |
| C | 232.6 | 44.3 | 57.2 | 56.5 | 40.7 | 74.8 | 8.6 |
| D | 435.8 | 73.4 | 55.7 | 55.0 | 35.1 | 77.5 | 12.4 |

All values for `inference_ms`. Run B total pipeline: mean 66.5, median 67.6, min 44.3, max 90.4, stdev 9.6 ms, about 15.0 frames/s derived from the mean: **software throughput on this Windows development PC**, not camera FPS, not Raspberry Pi FPS, not real-time VESTA FPS. Individual run A inference times (ms): 74.1, 109.6, 76.5, 66.3, 56.5, 58.6, 65.5, 63.1, 63.6, 63.0, 60.1, 61.6, 63.8, 57.4, 55.7, 66.1, 55.7, 53.8, 64.1, 75.3, 58.1, 61.8, 61.6, 68.2, 56.3, 57.6, 145.2, 257.3, 98.6, 73.8.

Reading: warm inference is about 55 to 63 ms median here; cold start and outliers (up to 257 ms in run A) are much slower, so a single timing is not representative. Resource check (run A, Windows working set, approximate): 62 MB before model load, 107 MB after load, 179 MB after 30 inferences.

## Detector assumption review

| Assumption | Status after the real model run |
|---|---|
| Input 416x416 NCHW float32 | verified on the real file |
| Output (1, 3549, 85), 80 classes | verified |
| Objectness and class scores already sigmoided | consistent on three synthetic frames; not proven on a positive |
| BGR order, letterbox, ratio, decode, class 0 = person, NMS | **not verified on real positives**; synthetic tests only |
| Clipping to original frame | synthetic tests only |

No code needed changing, so none was rewritten.

## Code and test changes in Task 2

- New `vision/benchmark.py`: timing command (initialisation, cold first inference, warm-up, N measured runs).
- `tests/test_vision.py`: added `test_real_model_contract_matches_the_detector_assumptions` (skips without the model file).
- `vision/detector.py`: not modified in this task.

## Tests

`tests/test_vision.py`: 33 passed, 1 skipped. Full suite: **249 passed, 9 skipped, 0 failed**. Change from 247 / 10: the blank-frame real-model test moved from skip to pass (+1 passed, -1 skipped) and the new contract test passes (+1 passed). The 9 skips are 4 ReSpeaker hardware-gated, 4 openWakeWord real-engine (Windows Application Control, see `WEEK3_REPORT.md`) and 1 positive-image test. Pytest never downloads the model.

## Validation classification (end of Task 2)

| Item | Level |
|---|---|
| Real ONNX artifact loaded, real inference executed, model contract checked | SOFTWARE-VALIDATED |
| Fake-session and synthetic-tensor detector tests | SIMULATED |
| Positive person detection; BGR/letterbox/decode on real positives | NOT TESTED |
| Detection accuracy; classroom student-count accuracy | NOT MEASURED |
| Raspberry Pi latency / FPS | NOT MEASURED |
| Real VESTA camera | NOT AVAILABLE; nothing HARDWARE-VALIDATED |

## Remaining Week 4 work

Positive person smoke test (needs a permitted image); real camera `FrameSource` (needs camera model and interface); classroom ROI mechanism and calibration; Pi latency measurement; evaluation on classroom-like images and threshold choice.

---

# Week 4 Task 3 — Reference-preprocessing check and positive-image validation

> **SUPERSEDED — the preprocessing change made in this task was wrong and was reverted in Task 4 (see the last section).** The tables and conclusions below that say RGB, divide by 255 and ImageNet mean/std are incorrect for `yolox_tiny.onnx`. They are kept as the record of what was done and why it failed.

Run date: 2026-10-02. Status: **Week 4 IN PROGRESS.** Task 3 is **PARTIALLY COMPLETE**: the reference-preprocessing check was done and found a real preprocessing error, now corrected. The positive real-image run was **NOT PERFORMED** because no permitted image exists.

## Baseline

`models/yolox_tiny.onnx` present, 20,219,662 bytes, SHA-256 `427cc366...b0f7`, identical to the Task 2 record. `pytest -q -rs`: 249 passed, 9 skipped, 0 failed. `vision_assets/` does not exist, `VESTA_PERSON_TEST_IMAGE` is unset, and the project contains no image files. No other folders were searched, and nothing was downloaded.

## Correction notice (supersedes earlier Week 4 text)

Task 1 and Task 2 of this report state that YOLOX input is **BGR, values 0..255, no mean/std**. That was **wrong for this model**. It came from the layout of a later YOLOX repository version and was never checked against the release the model comes from. The `0.1.1rc0` source shows RGB, divide by 255, then ImageNet mean/std normalisation. Task 1 and Task 2 text that says otherwise (including the "BGR order" wording in their test and assumption lists) is superseded by this section.

Impact: with the old preprocessing the model would have received inputs on the wrong scale and channel order. Nothing in Tasks 1 and 2 measured detection, so no reported result is invalidated, but any positive detection run before this fix would have been unreliable. The blank-frame timings are unaffected in kind, and a benchmark rerun after the fix gave the same range (inference median about 57 ms, mean about 59 ms, 20 runs).

## Official reference check

Source: `Megvii-BaseDetection/YOLOX` at tag **0.1.1rc0** (the release containing `yolox_tiny.onnx`), files `demo/ONNXRuntime/onnx_inference.py`, `yolox/data/data_augment.py` (`preproc`, `ValTransform`) and `yolox/utils/demo_utils.py` (`demo_postprocess`, `nms`, `multiclass_nms`). Read as plain text from the tag; no upstream code was copied or any framework installed.

| Component | Our implementation (after correction) | Official reference at 0.1.1rc0 | Match? |
|---|---|---|---|
| Colour order | RGB (our `Frame` is RGB, no flip) | cv2 image is BGR, `preproc` flips to RGB (`[:, :, ::-1]`) | yes (after correction; was wrong before) |
| Scaling | divide by 255 | `/= 255.0` | yes (after correction) |
| Normalisation | ImageNet mean (0.485, 0.456, 0.406), std (0.229, 0.224, 0.225) | same values passed in the demo | yes (after correction) |
| Letterbox | ratio = min(H/h, W/w), resize to int(w·r) x int(h·r), top-left placement | same | yes |
| Padding value | 114 (before normalisation) | 114.0 (before flip and normalisation) | yes |
| Resize filter | Pillow `BILINEAR` | `cv2.resize(..., INTER_LINEAR)` | **not identical**: Pillow's bilinear filters when shrinking, OpenCV's does not. Known deviation, not changed (OpenCV is not installed by design). Effect not measured |
| Tensor layout | CHW, batch 1, float32 | transpose (2,0,1), `img[None]`, float32 | yes |
| Decode | `(xy + grid) * stride`, `exp(wh) * stride`, strides 8/16/32 | same equations | yes |
| Grid / stride construction | meshgrid over H/stride x W/stride, concatenated in stride order | same | yes |
| Score | objectness x class score | `predictions[:, 4:5] * predictions[:, 5:]` | yes |
| Person class | COCO index 0 | class index 0 of `COCO_CLASSES` (person) | yes |
| Box conversion and ratio | centre/size to corners, divided by ratio | same | yes |
| Thresholds and NMS | score filter ≥ 0.30 then NMS 0.45 | NMS 0.45 on scores > 0.1, then draw only scores ≥ 0.30 | equivalent result for final boxes at 0.30 (higher-scoring boxes always pass the 0.30 filter) |
| NMS area | width x height | width+1 x height+1 | tiny difference (1 pixel), not changed |
| Clipping to image | clipped to frame, degenerate boxes dropped | none (drawing only) | our addition, deliberate |

Source reading only; **no output was compared numerically with the reference** (no image, no OpenCV).

## Implementation correction

`vision/detector.py::letterbox` now returns an RGB, divide-by-255, ImageNet-normalised tensor (it previously returned BGR 0..255). Module docstring and the `Frame` comment updated. `tests/test_vision.py`: the old BGR letterbox test was replaced by `test_letterbox_matches_the_official_yolox_0_1_1rc0_preprocessing` (checks channel order, per-channel normalisation of image and padding, ratio). A deliberate mutation (removing the normalisation) fails that test. On the real model, blank and random-noise frames still give finite outputs with objectness at most 0.003 and class scores at most 0.69 (inputs now span -2.12 to 2.29).

## Positive real-image test, colour-order diagnostic, annotation, positive-image timing

**NOT PERFORMED — permitted human-containing image unavailable.** Consequently there is no person count, confidence, box, annotated image, per-stage timing on a real image, or colour-order A/B comparison. None was invented. The colour-order question is now settled by the official source rather than by running both variants; an A/B on a real image remains a worthwhile later confirmation but is not required to choose the production path. The production path follows the official reference, not whichever variant scores higher.

How to run once an image is supplied (existing tooling): `python -m vision.detector vision_assets/<file> --annotate-dir vision_assets/out` and `python -m vision.benchmark --image vision_assets/<file> --runs 30`, then set `VESTA_PERSON_TEST_IMAGE` to run `test_real_model_finds_a_person_in_a_real_image`. Per-stage timing (preprocess, inference, decode) is already inside `DetectionResult` (`inference_ms`, `total_ms`).

## Count semantics

`DetectionResult.count` is the number of final person detections in one frame. It is not unique people over time, a verified student count, occupancy, attendance or a tracking result.

## Tests

`tests/test_vision.py`: 33 passed, 1 skipped. Full suite: **249 passed, 9 skipped, 0 failed**, unchanged from the Task 2 reference (one test replaced, none added). Skips: 4 ReSpeaker hardware-gated, 4 openWakeWord (Windows Application Control), 1 positive-image test.

## Validation classification

| Item | Level |
|---|---|
| Preprocessing now matches the official 0.1.1rc0 reference (by source comparison and unit test) | SOFTWARE-VALIDATED (reading + synthetic test); not numerically compared with the reference |
| Real model loads and runs after the correction | SOFTWARE-VALIDATED (blank and noise frames) |
| Person detection on a real image; decode and coordinates on real positives | NOT TESTED |
| Detection accuracy, precision, recall, student-count accuracy, robustness | NOT MEASURED |
| Raspberry Pi performance; real VESTA camera | NOT MEASURED / NOT AVAILABLE; nothing HARDWARE-VALIDATED |

## What a positive smoke test would and would not prove

It would show only that this implementation can detect a person in that one image with structurally valid output. It would not show accuracy, counting accuracy, classroom, occlusion, distance or lighting robustness, or camera and Raspberry Pi behaviour.

---

# Week 4 Task 4 — Positive real-image person detection (and reversal of the Task 3 preprocessing change)

Run date: 2026-10-02. Status: **Week 4 IN PROGRESS.**

## IMPORTANT: the Task 3 "correction" was wrong and has been reverted

Task 3 changed preprocessing to RGB, divide by 255, ImageNet mean/std, because the official YOLOX ONNX demo **at tag 0.1.1rc0** does that. Task 4 ran the real model on a real photo and showed that this change **made the detector blind**. The authority of a source reading was not enough; the model file's actual behaviour decides.

Diagnostic, same image, same model, same thresholds (0.30 / 0.45), only the input tensor changed:

| Input convention | Whole image: max objectness / person candidates / final detections | Crop of the standing group: same fields |
|---|---|---|
| RGB, /255, ImageNet mean/std (Task 3 version, 0.1.1rc0 demo) | 0.027 / 0 / **0** | 0.166 / 0 / **0** |
| BGR, raw 0..255 (Task 1/2 version) | 0.991 / 56 / **10** (best 0.88) | 0.986 / 69 / **8** (0.57 to 0.89) |
| RGB, raw 0..255 | 0.990 / 53 / 9 | 0.981 / 65 / 8 |
| BGR, /255 + ImageNet | 0.020 / 0 / 0 | 0.158 / 0 / 0 |

The deciding factor is the **scale/normalisation**: ImageNet normalisation breaks the model, raw 0..255 works. Channel order matters little for this file (BGR and RGB both work; BGR gave marginally higher confidences on this image, which is **not** the reason for choosing it). BGR is chosen because it is the cv2 convention YOLOX uses and because current official YOLOX (`main` and tag `0.3.0`, `yolox/data/data_augment.py::preproc`) feeds raw cv2 BGR 0..255 with no flip and no normalisation, keeping the RGB + mean/std path only behind a `legacy` flag. The 0.1.1rc0 demo applies that legacy convention to a file that does not behave as a legacy model. The reason for the upstream inconsistency was not investigated.

Production now: `letterbox` returns **BGR, raw 0..255 float32, NCHW**, 114 padding, top-left placement. `tests/test_vision.py::test_letterbox_gives_bgr_raw_0_255_input` checks this (and that no normalisation is applied). Re-introducing normalisation fails that test and the real-image test (checked by mutation). All "RGB, /255, ImageNet" statements in the Task 3 section above, including its comparison table and "Implementation correction" paragraph, are **superseded and incorrect**; Task 1's original BGR 0..255 statement turned out to be right. The earlier line "earlier BGR 0..255 was wrong" in this report and in `WEEK4_STATUS.md` is retracted.

The user instruction for the diagnostic said production must stay on the reference-correct path whichever variant scores higher on one image. This situation differs from tuning: the reference variant gives essentially zero objectness on three different views, so no threshold or tuning question arises; the choice was made on the model contract, not on a score comparison, and it is documented here so it can be reviewed.

## Baseline

`models/yolox_tiny.onnx` 20,219,662 bytes, SHA-256 `427cc366...b0f7` (unchanged). `vision_assets/` held exactly one image. `pytest -q -rs`: 249 passed, 9 skipped, 0 failed.

## Test image

Source category: user-provided image placed in the git-ignored `vision_assets/` for this smoke test (permission taken from the user having deliberately placed it there). Identifier `classroom_smoke_01.jpg`; format MPO/JPEG, mode RGB, 5712 x 4284 pixels (about 24 MP), EXIF orientation 1 (no rotation). No other metadata was read. Content: a lecture room with many people visible (several seated students seen from behind, a group standing at the front). The image is an intentional **positive smoke fixture**, not labelled evaluation data; the number of people visible is not used as ground truth. No face recognition or identity inference was done.

## Result (production path, score threshold 0.30, NMS 0.45, CPUExecutionProvider)

Input 5712 x 4284 -> model input 416 x 416 (ratio 0.0728); raw output shape (1, 3549, 85); 56 person candidates at or above threshold before NMS; **10 final person detections** (`DetectionResult.count`, a detection count only):

| # | Confidence | Box (x1, y1, x2, y2) in original pixels |
|---|---|---|
| 1 | 0.880 | 314, 2612, 1626, 4214 |
| 2 | 0.732 | 2715, 2448, 2961, 2869 |
| 3 | 0.703 | 2375, 2870, 3120, 3270 |
| 4 | 0.631 | 1899, 2448, 2183, 2898 |
| 5 | 0.598 | 2450, 2451, 2731, 2855 |
| 6 | 0.538 | 3543, 2739, 3837, 2975 |
| 7 | 0.494 | 2328, 2456, 2488, 2806 |
| 8 | 0.363 | 513, 2911, 784, 3335 |
| 9 | 0.357 | 1702, 2705, 1915, 2909 |
| 10 | 0.307 | 2130, 2584, 2337, 2916 |

Structural check (programmatic, all 10): coordinates and confidence finite; confidence within 0..1; x1 < x2; y1 < y2; box inside the frame; positive area. All passed.

Visual check of the annotated image (manual, not annotation-based evaluation): 9 of 10 boxes visibly cover a person (the foreground student, the standing group, the leaning student at right, the hooded student at left, the student seen from behind in the centre); **box 9 (0.357) appears to cover a black office chair, not a person**, so at least one false positive exists. Several visible people were **not** detected (for example some seated students partly hidden behind monitors, and people at the image edges). This is an observation about one image and is not a precision or recall figure. Box 1 is loose and includes the chair.

Annotated output: `vision_assets/results/classroom_smoke_01_annotated.png` (git-ignored; the original was not modified). `DetectionResult` remains the authoritative output.

## Timing (this Windows PC; `python -m vision.benchmark --image ... --runs 30`)

Model initialised once (about 256 to 290 ms, reported separately); 1 cold first inference (about 49 to 51 ms) and 1 discarded warm-up; 30 measured runs on the same image; image decode/load measured once (262 and 296 ms, not part of detector time). Annotation is not included. Two runs shown to display variability. Milliseconds:

| Stage | Run 1 mean / median / min / max / stdev | Run 2 mean / median / min / max / stdev |
|---|---|---|
| Preprocess (resize 24 MP to 416, tensor build) | 105.8 / 105.3 / 100.2 / 115.3 / 4.1 | 110.7 / 111.8 / 102.6 / 117.9 / 4.7 |
| ONNX `session.run` | 48.4 / 47.3 / 37.0 / 59.8 / 6.4 | 54.6 / 53.6 / 41.8 / 65.6 / 7.8 |
| Decode, filter, NMS | 1.3 / 1.3 / 1.0 / 2.4 / 0.3 | 1.4 / 1.4 / 1.1 / 1.8 / 0.2 |
| Total detector (N = 30) | 155.6 / 154.1 / 138.5 / 173.5 / 7.7 | 166.6 / 168.0 / 147.3 / 182.9 / 10.8 |

Derived: about 6.0 to 6.4 frames/s, **software processing throughput on this Windows development PC** (not camera FPS, not Raspberry Pi FPS, not VESTA real-time performance). Preprocessing dominates because a 24 MP photo is shrunk by Pillow; the model inference itself is about 50 ms, in line with the blank-frame runs. A real camera frame would be much smaller.

## Code and test changes in Task 4

- `vision/detector.py`: `letterbox` back to BGR 0..255 (reverts the Task 3 normalisation); docstring records the evidence; `DetectionResult` gained `preprocess_ms` and `postprocess_ms`.
- `vision/benchmark.py`: prints image load time and per-stage statistics.
- `vision/frames.py`: comment only.
- `tests/test_vision.py`: letterbox test now asserts BGR raw 0..255; the positive real-image test falls back to `vision_assets/classroom_smoke_01.jpg` when `VESTA_PERSON_TEST_IMAGE` is unset, and asserts only structure (at least one person, finite in-bounds boxes, confidence at or above the threshold, person label); no exact count.
- Failure semantics unchanged and still tested: unreadable or missing image is an error; an inference failure is raised, never turned into "no people". Pytest never downloads the model or image; without the files the real-model tests skip.

## Tests

`tests/test_vision.py`: 34 passed, 0 skipped. Full suite: **250 passed, 8 skipped, 0 failed** (versus 249 / 9: the positive-image test now runs, +1 passed, -1 skipped; the replaced letterbox test did not change the count). Remaining 8 skips: 4 ReSpeaker hardware-gated, 4 openWakeWord (Windows Application Control).

## Validation classification

| Item | Level |
|---|---|
| The real YOLOX-Tiny model detected person(s) in the selected permitted positive smoke-test image using the VESTA inference pipeline (BGR 0..255 preprocessing, decode, person filter, NMS, coordinate restoration) | SOFTWARE-VALIDATED |
| Fake-session and synthetic-tensor detector tests | SIMULATED |
| Detection accuracy, precision, recall, student-count accuracy, classroom, occlusion, distance and lighting robustness | NOT MEASURED |
| Resize-filter deviation (Pillow versus OpenCV) effect | NOT MEASURED |
| Raspberry Pi latency / FPS | NOT MEASURED |
| Real VESTA camera | NOT AVAILABLE; nothing HARDWARE-VALIDATED |

## Not proven

Detector accuracy; student-count accuracy; classroom occupancy; robustness to occlusion, distance or lighting; real camera behaviour; Raspberry Pi performance. One image with one false positive and several apparent misses shows only that the path works end to end.

## Remaining Week 4 work

Real-camera `FrameSource` (needs camera model and interface); classroom ROI mechanism and calibration; Raspberry Pi latency measurement; evaluation on labelled classroom-like images and threshold selection (including whether 416 input is adequate for small, distant people, since the standing group is only about 25 to 35 pixels high in the 416-pixel model input).

---

# Week 4 Task 5 — Small Classroom Pilot Evaluation

Run date: 2026-10-02. Status: **Week 4 IN PROGRESS.** This is a small pilot evaluation on user-provided classroom photographs and is not a final estimate of generalization performance.

## Annotation policy (written before any annotation or detector evaluation of the three evaluation images)

A "person" is counted and boxed when a human observer can confidently see a real, physically present person. Rules:

1. **Include** standing people, seated people, and partly occluded people when the head **and** at least part of the neck/shoulders or upper body are visible (a head with its shoulder line counts; a head fragment alone does not).
2. **Box = the visible extent**: a tight axis-aligned rectangle around the parts of the person that can be seen (head, torso, visible arms). Do not extend the box over hidden body parts, and do not include the chair.
3. **Exclude**: reflections; people shown on monitors or the projector; objects a human cannot confidently identify as a person; fragments where the head or upper body cannot be judged; extremely tiny ambiguous figures.
4. No names, identities, demographics or faces are recorded. Only boxes are stored.
5. Each ground-truth box also gets an `occluded` flag (true when roughly half or more of the person's expected visible upper body is hidden by furniture or other people). Size (original and 416-input pixels) is computed from the box, not annotated.
6. The policy is fixed before results are computed. Any ambiguity found while annotating is resolved by applying these rules, never by looking at detector output.

Known weakness, stated up front: the ground-truth boxes are authored visually by the AI assistant from the photographs (see "Ground-truth method" below) and have **not** been independently checked by a human. Image `classroom_eval_01` is the same photograph as the earlier smoke-test image (`classroom_smoke_01`, renamed), so the detector's output on it was seen before annotation; the boxes for it were still drawn from the raw photograph without the overlay.

### Policy refinement and matching rule (fixed before any metric was computed)

- **Ignore regions.** People who fail rule 1 only because the head is cut off from the shoulders by furniture (head-only, cap-only, or a torso with no visible head) are recorded as `ignore` boxes, not as ground-truth persons. They count as neither a miss nor a hit. A detection that matches no ground-truth person is **not** counted as a false positive when it covers at least 50 % of the area of an ignore box (intersection area / ignore-box area >= 0.5); it is reported separately as "ignored".
- **Matching.** One-to-one, greedy by descending detector confidence: each detection takes the unused ground-truth box with the highest IoU, provided IoU >= 0.50. Unmatched detection = FP (unless ignored as above), unmatched ground truth = FN.
- **Ground-truth method: Option A** (explicit boxes). Boxes were drawn by the AI assistant by reading coordinates off zoomed, gridded crops of the raw photographs, **before** running the detector on images 2 and 3. Image 1 is the earlier smoke-test photograph, whose detector output had been seen. Ground truth has **not** been independently checked by a human; box precision is roughly tens of pixels on photos of 4300 to 5700 pixels. The annotation file (`vision_assets/annotations/classroom_eval.json`, git-ignored) stores only image size and boxes.
- **Orientation.** One photo (image 2) is stored sideways with EXIF orientation 6. `ImageFileSource` ignored that tag, so the detector would have seen a sideways room. It now applies EXIF orientation (frame loading only; detector, preprocessing, thresholds and model are unchanged). All annotation and evaluation use the upright image.

## Evaluation data and configuration (unchanged baseline)

Three user-provided classroom photographs in the git-ignored `vision_assets/`: `classroom_eval_01.jpg` (the earlier smoke-test photo, renamed), `classroom_eval_02.jpg`, `classroom_eval_03.jpg`. Each is MPO/JPEG, RGB, 5712 x 4284 as stored; image 2 is EXIF-rotated, so its upright frame is 4284 x 5712. Same room, three viewpoints, many people behind monitors. Model `yolox_tiny.onnx` (SHA-256 unchanged `427cc366...b0f7`), 416 x 416 input, BGR raw 0..255 preprocessing, **score threshold 0.30, NMS 0.45**, CPUExecutionProvider. **No threshold, model, preprocessing or resolution was changed**, and no other threshold was tried. The run command is `python -m vision.evaluate vision_assets/annotations/classroom_eval.json`; it writes `vision_assets/results/classroom_eval_metrics.json` and a colour-coded overlay per image (blue = matched ground truth, green = true-positive detection, red = false positive, orange = missed person, grey = ignore region, yellow = detection ignored).

## Per-image results (IoU >= 0.50; PILOT / DESCRIPTIVE ONLY)

| Image | Manual visible count (policy) | Predicted count | Count error (pred - manual) | TP | FP | FN | Ignored detections | Ignore regions |
|---|---|---|---|---|---|---|---|---|
| classroom_eval_01 | 13 | 10 | -3 | 6 | 4 | 7 | 0 | 5 |
| classroom_eval_02 (upright 4284 x 5712) | 6 | 5 | -1 | 4 | 0 | 2 | 1 | 3 |
| classroom_eval_03 | 6 | 6 | 0 | 2 | 2 | 4 | 2 | 6 |
| **Total** | **25** | **21** | | **12** | **6** | **13** | **3** | 14 |

**Count level:** 25 people visible under the policy, 21 detections, mean absolute count error 1.33 per image. This is descriptive for these three photographs only. Image 3 shows why a count can mislead: the count error is 0 although only 2 of its 6 people were matched.

**Detection level (pilot precision/recall on three user-provided classroom images, 25 ground-truth persons, IoU >= 0.50):** TP 12, FP 6, FN 13, **precision 0.667, recall 0.480**. No mAP and no other score was calculated. A detection that covered an ignore region was not counted as a false positive (3 cases). These figures depend on my own ground-truth boxes (unverified by a human) and on a strict IoU rule, so they are rough.

## Error analysis (descriptive; no model-internal causes claimed)

**False positives (6):**
- 2 cover no person: in image 1 a black office chair beside a tissue box (confidence 0.357); in image 3 a large box at the bottom right over what looks like a black coat and backpack on a chair (0.537).
- 4 lie on real people but overlap the ground-truth box by less than half: image 1, a box around a standing person that also takes in the chair (best IoU 0.45); a box over only the head of the leaning person at the right, whose torso lower in the frame is part of the ground truth (0.31); a box spanning the gap between two standing people (0.43); image 3, a box over only the head of a person whose body extends lower (0.27). These are localization differences, partly because the ground truth covers the whole visible extent and the detector boxes are sometimes tighter or looser.

**Misses (13):** 9 of the 13 are people marked occluded (largely hidden by a monitor or by another person), for example people behind the front-row monitors and one standing person hidden between two others. Two standing people in the front group were hidden partly behind neighbours. 4 misses are people not marked occluded; 3 of those have a nearby false positive on the same person with IoU below 0.5, so they are localization misses rather than undetected people. A person at the left image edge (image 3) was missed.

## Target scale

Person-box heights projected to the 416 input (letterbox ratio 0.0728 for all three images): matched 12 people, median 29.4 px (min 11.4, max 114.3); missed 13 people, median 23.5 px (min 8.6, max 37.9). By height: under 20 px, 1 of 5 matched; 20 to 30 px, 6 of 13; 30 px and above, 5 of 7. By occlusion: occluded people 3 of 12 matched, not-occluded people 9 of 13. In this pilot, misses are more strongly associated with occlusion than with size, but nearly every person is small at this input (all but one are under 40 px tall). With so few people the two effects are confounded; no minimum detectable size or generalised rule can be derived.

## Performance (Windows development PC software performance; not Raspberry Pi, not camera FPS)

One discarded warm-up then 10 measured runs per image, model initialised once, annotation not included. Mean milliseconds per stage; two evaluation runs are shown because the machine is noisy.

| Image | Run | Preprocess | Inference | Postprocess | Total (mean) | Total (median) |
|---|---|---|---|---|---|---|
| 01 | A | 105.1 | 49.2 | 1.2 | 155.4 | 156.4 |
| 01 | B | 115.1 | 60.5 | 1.4 | 177.0 | 175.3 |
| 02 | A | 117.6 | 59.0 | 1.9 | 178.5 | 166.2 |
| 02 | B | 109.6 | 54.0 | 1.2 | 164.9 | 164.7 |
| 03 | A | 106.4 | 46.3 | 1.1 | 153.8 | 152.5 |
| 03 | B | 127.1 | 70.6 | 1.6 | 199.4 | 189.7 |

Preprocessing dominates because 24 MP photos are shrunk to 416. Detection results were identical in both runs.

## Code and test changes in Task 5

- `vision/frames.py`: `ImageFileSource` now applies EXIF orientation (found because image 2 was stored sideways). Test `test_image_file_source_honours_exif_orientation`; removing the call makes it fail.
- `vision/evaluate.py` (new): IoU, one-to-one greedy matching with ignore regions, precision/recall, and the `python -m vision.evaluate` runner with overlays and a JSON summary. `tests/test_vision_eval.py` (new, 11 tests with synthetic boxes): perfect match, one FP, one FN, duplicate detection, below-IoU case, best-ground-truth assignment, ignore-region behaviour.
- `tests/test_vision.py`: the optional positive smoke test now falls back to `classroom_smoke_01.jpg` or `classroom_eval_01.jpg` (the same photograph renamed). It stays an optional test, and the evaluation images are not otherwise test dependencies.
- The detector, model, preprocessing and thresholds were not modified.

## Tests

`tests/test_vision.py` and `tests/test_vision_eval.py`: 46 passed. Full suite **262 passed, 8 skipped, 0 failed**. Compared with the starting 249 / 9: the optional positive-image test runs again after the image rename (+1 passed, -1 skipped), +1 EXIF test, +11 metric tests. The 8 skips are 4 ReSpeaker hardware-gated and 4 openWakeWord (Windows Application Control).

## Validation classification

| Item | Level |
|---|---|
| Image loading (incl. EXIF), detection, matching and metric code | SOFTWARE-VALIDATED (synthetic unit tests; the detector ran on the real images) |
| Pilot precision 0.667 / recall 0.480 / count MAE 1.33 on 3 user photos at IoU 0.5, score 0.30 | PILOT-MEASURED (descriptive only; ground truth AI-authored, unverified) |
| Pilot timing on this PC | PILOT-MEASURED (Windows development PC only) |
| Final detector accuracy, mAP, generalisation, threshold choice | NOT MEASURED |
| Raspberry Pi performance; real VESTA camera | NOT MEASURED / NOT AVAILABLE; nothing HARDWARE-VALIDATED |

## What the pilot supports, and does not

Supports: on these three photographs the unchanged baseline finds roughly half of the people judged visible, mostly the unoccluded ones, produces a few false alarms (two on non-person objects), and tends to miss people hidden behind monitors; the evaluation procedure and code work and can be re-run on better data.

Does not support: final detector accuracy; mAP; generalisation to other classrooms, lighting or camera positions; that 0.30 or 416 input is the best choice (no other setting was tried, deliberately, and these images must not be used to tune it); student-count accuracy or occupancy; Raspberry Pi or camera performance. The ground-truth boxes need human review, and the first-pass figures above are the ones produced before any inspection of the overlays; the ground truth was not edited afterwards.

## Remaining Week 4 work

Human review of the annotation file; more and independent evaluation images (including a held-out set for threshold choice and input size); real-camera `FrameSource`; classroom ROI; Raspberry Pi latency measurement.

---

# Week 4 Task 5B — Annotation and Metric Audit

Run date: 2026-10-04. Status: **Week 4 IN PROGRESS.**

> **Ground-truth annotations were prepared during development (by the AI assistant) and remain pending independent human review. Therefore all Task 5 pilot metrics (TP, FP, FN, precision, recall, count error, MAE) are PROVISIONAL PILOT METRICS.** Claude's own visual inspection of the overlays is not independent approval.

## Reproduction

The evaluator was run unchanged (`python -m vision.evaluate vision_assets/annotations/classroom_eval.json`, model SHA-256 unchanged, score 0.30, NMS 0.45, input 416, IoU 0.50) and **reproduced the Task 5 numbers exactly**: TP 12, FP 6, FN 13, precision 0.667, recall 0.480, 25 visible persons, 21 predictions, count MAE 1.33. Nothing was tuned or changed.

## Structural annotation audit (`python -m vision.audit ...`)

All 25 person boxes and 14 ignore boxes: four finite numbers, x1 < x2, y1 < y2, inside the upright frame; no identical or strongly overlapping (IoU > 0.5) duplicates; annotation size equals the upright frame size for every image. Result: **no problems**. No box was moved.

## EXIF / coordinate-system audit

| Image | EXIF orientation | Stored size | Upright frame | Annotation size | Frame pixels = one EXIF transpose of the file |
|---|---|---|---|---|---|
| classroom_eval_01 | 1 | 5712 x 4284 | 5712 x 4284 | 5712 x 4284 | yes |
| classroom_eval_02 | 6 | 5712 x 4284 | **4284 x 5712** | **4284 x 5712** | yes |
| classroom_eval_03 | 1 | 5712 x 4284 | 5712 x 4284 | 5712 x 4284 | yes |

Chain for image 2: raw JPEG -> `ImageFileSource` applies `ImageOps.exif_transpose` once -> upright `Frame` (4284 x 5712) -> detector coordinates are in that frame (a `Detection` is validated against the frame size) -> the ground-truth boxes were drawn on the same upright image and are used as-is by the evaluator with no further transform. The audit checks that the frame equals exactly one transpose and that the annotation size matches. Visual check of the audit overlay for image 2: ground-truth and detection boxes both sit on the same people in the upright picture. No double transformation.

## Prediction accounting (predictions = TP + FP + ignored)

| Image | Predictions | TP | FP | Ignored (inside an ignore region) | Reconciled |
|---|---|---|---|---|---|
| classroom_eval_01 | 10 | 6 | 4 | 0 | yes |
| classroom_eval_02 | 5 | 4 | 0 | 1 | yes |
| classroom_eval_03 | 6 | 2 | 2 | 2 | yes |
| **Total** | **21** | **12** | **6** | **3** | **21 = 12 + 6 + 3** |

The "missing" 3 predictions in the Task 5 summary (21 predicted vs TP + FP = 18) are the 3 predictions ignored because they cover an ignore region; this is computed, not assumed.

## Ground-truth accounting (GT persons = TP + FN)

| Image | GT persons | TP | FN | Reconciled |
|---|---|---|---|---|
| classroom_eval_01 | 13 | 6 | 7 | yes |
| classroom_eval_02 | 6 | 4 | 2 | yes |
| classroom_eval_03 | 6 | 2 | 4 | yes |
| **Total** | **25** | **12** | **13** | **25 = 12 + 13** |

## Exact ignore-region semantics (unchanged)

- An **ignore region** is an annotated box (`ignore` list) around a person who fails the "head plus neck/shoulders or upper body visible" rule only because the rest is hidden: head-only, cap-only, a torso without a visible head, or a head fragment at an image edge. 14 exist (5, 3, 6).
- These people are **not ground-truth persons**: they are excluded from recall (never a FN), and **not counted in the manual visible-person total** (25 counts only the `persons` list).
- A prediction is excluded from FP only if it (a) did **not** match a ground-truth person at IoU >= 0.50, and (b) covers at least 50 % of the area of at least one ignore box (intersection area / ignore-box area >= 0.5). Otherwise it is a FP. A prediction that matched a person is never ignored. The rule is deterministic and was fixed before the metrics were computed; it was not changed in this task.

## Matching algorithm verification

One-to-one, greedy by descending detector confidence; each prediction takes the unused ground-truth box with the highest IoU, matched if IoU >= 0.50 (boundary inclusive); ties cannot occur silently (first highest-IoU box wins, same input gives the same result). The audit asserts that no prediction and no ground-truth box is used twice. Unit-tested with synthetic boxes: perfect match, one FP, one FN, duplicate predictions on one person, one prediction overlapping two people (takes the better one; the other stays a FN), below-0.50, exactly 0.50 (match) versus 0.40 (no match), prediction on an ignore region, prediction grazing an ignore region (still FP), ignore boundary exactly 0.5 versus 0.4, matched prediction never ignored, determinism and the two accounting identities.

## Human-review overlays and checklist

Ignored by git, in `vision_assets/results/audit/`: `classroom_eval_01_audit.png`, `classroom_eval_02_audit.png`, `classroom_eval_03_audit.png` (downscaled to at most 2800 px on the long side) and `REVIEW_CHECKLIST.md`. Legend: blue `G#` ground-truth person matched; orange `G# FN` ground-truth person not matched; grey `I#` ignore region; green `P# TP`; red `P# FP`; yellow `P# IGN`. Numbered IDs let you refer to a box when requesting a correction. Original photographs and earlier results were not overwritten.

| Image | GT persons | Ignore regions | Detections | Human review |
|---|---|---|---|---|
| classroom_eval_01 | 13 | 5 | 10 | **PENDING USER REVIEW** |
| classroom_eval_02 | 6 | 3 | 5 | **PENDING USER REVIEW** |
| classroom_eval_03 | 6 | 6 | 6 | **PENDING USER REVIEW** |

## Code and test changes

- `vision/audit.py` (new, read-only): structural annotation audit, EXIF chain proof, accounting identities, ID-labelled overlays, review checklist.
- `tests/test_vision_eval.py`: 13 new tests (matching edge cases and audit functions).
- No change to the detector, model, thresholds, preprocessing, input size, IoU rule, ignore rule or annotations.

## Tests

Full suite **275 passed, 8 skipped, 0 failed** (262 + 13 new; skips unchanged: 4 ReSpeaker hardware-gated, 4 openWakeWord blocked by Windows Application Control). Evaluation and vision tests: 59 passed.

## What must happen before the metrics are accepted

The user must review the three audit overlays and the annotation file (`vision_assets/annotations/classroom_eval.json`), then approve or correct each box. Any correction requires re-running `python -m vision.evaluate` and `python -m vision.audit` and replacing the numbers above. Until then every figure from Task 5 is provisional.

---

# Week 4 Task 5C — Human-review corrections and HUMAN-REVIEWED PILOT METRICS

Date: 2026-10-04. Status: **Week 4 IN PROGRESS.** The user reviewed all three audit overlays and asked for the corrections below. Nothing else changed: detector, model, score threshold 0.30, NMS 0.45, input 416, preprocessing, IoU 0.50, matching rule, ignore rule and predictions are as before. No tuning was done and none is implied by the new numbers.

## Annotation corrections (applied exactly as requested; boxes unchanged, only their class changed)

| Image | Provisional ID | Box (x1, y1, x2, y2) | Change | Basis |
|---|---|---|---|---|
| classroom_eval_01 | none | none | approved as annotated, no change | user review |
| classroom_eval_02 | G3 (FN) | 2225, 3135, 2584, 3253 | person -> ignore | head and arm only, body hidden by the monitor (user) |
| classroom_eval_02 | G5 (FN) | 2796, 3124, 2947, 3447 | person -> ignore | predominantly head-only behind the monitors (user) |
| classroom_eval_02 | G1 (the ground truth matched by prediction P4 TP) | 1704, 3161, 1842, 3317 | person -> ignore | re-checked on the raw image only: a rounded head of long dark hair against a dark monitor, no discernible neck, shoulders or body; fails "head plus neck/shoulders or upper body visible" |
| classroom_eval_03 | G1 (FN) | 0, 2867, 200, 3192 | person -> ignore | essentially head-only above the monitor (user) |
| classroom_eval_03 | G2 (FN) | 2530, 2996, 2695, 3190 | person -> ignore | re-checked on the raw image only: back of a head with long hair, no discernible shoulders or body, so head-only |

Kept as they were: all clearly visible upper-body people remain ground truth. The large lower-right detection in image 3 (confidence 0.537, over a coat/backpack on a chair) stays a genuine false positive and no ground truth was created around it. The right-side prediction on the real person in image 3 stays a prediction; it fails IoU >= 0.50 against the valid ground-truth box (best IoU 0.27), and that localization FP/FN pair is preserved, not fitted away.

The previous annotation file is kept as `vision_assets/annotations/classroom_eval.provisional.json`; the corrected file `classroom_eval.json` records every change under its `review` key. IDs above refer to the provisional numbering used in the overlays you reviewed.

## Verification after the corrections

- Structural annotation audit: **OK, no problems.**
- EXIF/coordinate validation unchanged and passing: image 2 has orientation 6; upright frame 4284 x 5712 equals one EXIF transpose of the file and matches the annotation size; images 1 and 3 are orientation 1.
- Prediction accounting (predictions = TP + FP + ignored) and ground-truth accounting (GT = TP + FN) both hold for every image and in total.
- Audit overlays regenerated for all three images in `vision_assets/results/audit/` (the previous provisional overlays are kept in `vision_assets/results/audit_provisional_before_review/`). Overwriting existing PNGs failed intermittently with a Windows file error (probably a file lock), so the old overlays were moved aside and new ones written.

## HUMAN-REVIEWED PILOT METRICS (IoU >= 0.50, score 0.30, NMS 0.45, input 416; descriptive, 3 images)

| Image | GT persons | Ignore regions | Predictions | TP | FP | FN | Ignored predictions | Count error (pred - GT) |
|---|---|---|---|---|---|---|---|---|
| classroom_eval_01 | 13 | 5 | 10 | 6 | 4 | 7 | 0 | -3 |
| classroom_eval_02 | 3 | 6 | 5 | 3 | 0 | 0 | 2 | +2 |
| classroom_eval_03 | 4 | 8 | 6 | 2 | 2 | 2 | 2 | +2 |
| **Total** | **20** | **19** | **21** | **11** | **6** | **9** | **4** | |

- **Precision 0.647** (11 / 17), **recall 0.550** (11 / 20), **count MAE 2.33 per image** (7 / 3).
- Accounting: 21 = 11 + 6 + 4 and 20 = 11 + 9.

## Change from the provisional metrics

| Quantity | Provisional | Human-reviewed |
|---|---|---|
| GT persons | 25 | 20 |
| Ignore regions | 14 | 19 |
| TP / FP / FN | 12 / 6 / 13 | 11 / 6 / 9 |
| Ignored predictions | 3 | 4 |
| Precision | 0.667 | 0.647 |
| Recall | 0.480 | 0.550 |
| Count MAE per image | 1.33 | 2.33 |

Why: five ground-truth boxes moved to ignore. Four of them were misses (image 2 G3 and G5, image 3 G1 and G2), so FN fell from 13 to 9; one (image 2 G1) had been matched, so TP fell from 12 to 11, and the prediction that matched it is now ignored (ignored predictions 3 -> 4). Recall rose because fewer head-only people count as misses; precision fell slightly because one former TP no longer counts; the count error got worse because the manual count dropped while the number of predictions did not.

## Status and limits

These are HUMAN-REVIEWED PILOT METRICS for three user-provided photographs: the annotations were reviewed and corrected by the user, but the set is tiny, from one room, and not a random sample; the 2.33 count MAE and the precision/recall are descriptive only and are not final detector accuracy, mAP, generalisation, or a student-count accuracy. Image 1 was approved as annotated; the corrected images 2 and 3 were corrected on the user's instruction, and the corrected overlays can still be re-checked. The score threshold, input size and NMS must not be selected from these three images.

## Tests

Full suite **275 passed, 8 skipped, 0 failed**, unchanged from Task 5B (no test depends on the annotation values; the evaluate/audit label text changed, the metric logic did not). Skips unchanged: 4 ReSpeaker hardware-gated, 4 openWakeWord blocked by Windows Application Control.

## Tool changes

`vision/evaluate.py` labels its output "HUMAN-REVIEWED PILOT METRICS" when the annotation file contains a `review` record (otherwise "PROVISIONAL") and stores the label in its JSON; `vision/audit.py` writes the review status into the checklist from the same record. Metric, matching and ignore logic are untouched. Saved metric files: `classroom_eval_metrics_provisional.json` (before) and `classroom_eval_metrics_humanreviewed.json` (after), both in `vision_assets/results/`.

---

# Week 4 Task 5D — Count Metric Semantics

Date: 2026-10-04. Status: **Week 4 IN PROGRESS.** Annotations, detector, model, thresholds (score 0.30, NMS 0.45, IoU 0.50), input 416, preprocessing, matching and ignore policy were **not** changed. TP, FP, FN, precision and recall are unchanged.

## Reproduction and audit of the old count

The evaluator reproduced the Task 5C numbers exactly (GT 20, TP 11, FP 6, FN 9, ignored predictions 4, raw predictions 21, precision 0.647, recall 0.550, count MAE 2.33). In the code, the old count error was `result.count - len(gts)`, where `result.count` is **every final person detection the detector emitted**, including the 4 that evaluation classifies as "ignored", and `len(gts)` counts only ground-truth persons, which **excludes** ignore-region people. The old 2.33 therefore compared two different populations.

## Is raw predictions vs ignore-excluded GT valid? No

Ignore regions mark real people (head-only, torso without head, and similar) whom the policy leaves out of the ground truth only because their visibility is too poor to judge the detector fairly. A detection on such a person is therefore **not a false positive** (ignored prediction is not an FP), yet it still raises the raw detector count while the person is absent from the ground-truth count. The raw error is inflated by exactly the ignored predictions and measures a different thing from TP/FP/FN. The people in ignore regions must **not** be added to the ground truth to fix the arithmetic: they were excluded deliberately, only some of them were detected (4 ignored predictions against 19 ignore regions), and some regions may not be fully reliable people.

## Definitions (per image; errors are predicted minus ground truth)

- **RAW DETECTOR COUNT** = all final person detections. **Raw error** = raw count - GT. Operationally meaningful as "what the baseline outputs", but not aligned with the ignore-region evaluation.
- **EVALUABLE COUNT** = raw count - ignored predictions = TP + FP (this follows the implementation: an ignored prediction is an unmatched detection covering at least half of an ignore box, everything else is a TP or FP). Evaluable ground truth = TP + FN = GT. **Evaluable error** = (TP + FP) - (TP + FN) = **FP - FN**. This uses the same population as TP/FP/FN and is the **preferred pilot count metric**.
- Caveat: an evaluable count error of 0 does not mean the detections were right; false positives and misses can cancel (image 3 below has 2 FP and 2 FN and an evaluable error of 0). It is a count-consistency number, not a detection measure and not a student-count accuracy.

## Per-image recalculation (HUMAN-REVIEWED annotations)

| Image | GT | Ignore regions | Raw predictions | Ignored predictions | Evaluable predictions (TP+FP) | Raw error | Evaluable error (FP-FN) | TP / FP / FN |
|---|---|---|---|---|---|---|---|---|
| classroom_eval_01 | 13 | 5 | 10 | 0 | 10 | -3 | -3 | 6 / 4 / 7 |
| classroom_eval_02 | 3 | 6 | 5 | 2 | 3 | +2 | 0 | 3 / 0 / 0 |
| classroom_eval_03 | 4 | 8 | 6 | 2 | 4 | +2 | 0 | 2 / 2 / 2 |
| **Total** | **20** | **19** | **21** | **4** | **17** | | | **11 / 6 / 9** |

Accounting holds: raw 21 - ignored 4 = evaluable 17 = TP 11 + FP 6; GT 20 = TP 11 + FN 9.

## Aggregate count metrics

- **RAW-COUNT MAE = 2.33 per image** (|-3|, |+2|, |+2|). Kept for continuity and **superseded for evaluation purposes**: it mixes populations.
- **EVALUABLE-COUNT MAE = 1.00 per image** (|-3|, 0, 0). **Preferred** for the pilot report.
- The change comes entirely from images 2 and 3, where 2 ignored predictions each inflated the raw error by +2 for people the policy deliberately excludes.

## Detection metrics (unchanged)

TP 11, FP 6, FN 9, precision 0.647 (11/17), recall 0.550 (11/20), IoU >= 0.50.

## Final HUMAN-REVIEWED PILOT METRICS (3 user photos, descriptive only)

GT 20; ignore regions 19; raw predictions 21; TP 11; FP 6; FN 9; ignored predictions 4; precision 0.647; recall 0.550; **evaluable-count MAE 1.00** (raw-count MAE 2.33, superseded). Neither count metric is a student-count accuracy, and nothing here may be used to select a threshold or input size.

## Code and tests

- `vision/evaluate.py`: new `count_views()` returns raw count, ignored, evaluable count, GT, raw error and evaluable error and asserts the accounting identities; the runner prints both views and both MAEs and saves `count_mae_raw`, `count_mae_evaluable` (and the old `count_mae` key, equal to the raw value) plus per-image `counts` in `classroom_eval_metrics.json`. Matching, TP/FP/FN, precision, recall and the ignore rule are untouched.
- `tests/test_vision_eval.py`: 7 new tests: no ignores (raw equals evaluable); one ignored prediction (raw differs); all detections ignored; one FP and one FN cancel (error 0 while detection errors remain); FP-only (+1); FN-only (-1); evaluable error always equals FP - FN.

## Tests

Evaluation and vision tests: 66 passed. Full suite **285 passed, 5 skipped, 0 failed**, versus the reference 275 / 8 / 0. Explanation: +7 new count tests (282), and +3 more passes because three openWakeWord real-engine tests that skipped on 2026-10-02 now run: `import openwakeword` succeeds again on this PC, so the Windows Application Control block described in `WEEK3_REPORT.md` no longer applies here (an environment change, not a code change; nothing was done about it in this task). The 5 remaining skips are 4 ReSpeaker hardware-gated tests and the openWakeWord recorded-clip test, which needs `VESTA_HEY_JARVIS_WAV`. `WEEK3_REPORT.md` still correctly describes the state on its own date.

---

# Week 4 Task 6A — Public Dataset Audit

Date: 2026-10-04. Status: **Week 4 IN PROGRESS.** Inspection only. YOLOX was **not** run on this dataset, no overlays were made, and the detector, thresholds (score 0.30, NMS 0.45, IoU 0.50), input 416 and preprocessing are unchanged. The three earlier photographs remain the DEVELOPMENT / PILOT set and are not held-out data.

Location: a folder named `images13.9` under the git-ignored `vision_assets/` (development path only). The images show identifiable people and must not be published or reproduced in reports.

## Contents

109 files: 108 JPEG images (all `.jpg`, in an `images/` subfolder, names `img533.jpg` to `img658.jpg`) and one `labels.csv`. No README, licence, citation, split, metadata or annotation file exists.

## labels.csv schema

Columns: `image_file_name`, `count`. 108 data rows, no missing or empty values, all counts integers. Example rows: `img533.jpg,46`; `img534.jpg,58`; `img535.jpg,56`. **There is no class column, no bounding-box field, no split field and no confidence field.**

## Annotation type

**B: image-level counts.** The `count` column has no documented meaning. Nothing local states what is counted (all people, only seated students, heads) or the counting rules. A sampled visual check (16 images) found the numbers plausible for what is visible, but that is not a verification, and the label definition remains **undocumented**. Because there are no boxes, no bounding-box format question arises and no person-class mapping is possible: there is no class to map.

## Statistics

| Item | Value |
|---|---|
| Images / label rows | 108 / 108 |
| Images with count 0 ("empty") | 16 |
| Images with count >= 1 | 92 (counts 1 to 3: 11 images) |
| Total counted people | 3438 |
| Count per image | min 0, max 61, mean 31.8, median 36 |
| Resolutions | 1280x720 (45), 1920x1088 (36), 1920x1080 (27) |
| File format | JPEG, RGB; no EXIF segment in any file |
| Box size statistics | not available (no boxes) |

## Integrity findings (reported, nothing repaired)

- Every CSV reference resolves to a file and every file has a label: 0 missing, 0 unlabelled, 0 duplicate ids, 0 exact duplicate files.
- **1 truncated image: `img575.jpg` (183,388 bytes, labelled 45) fails a strict full decode** ("image file is truncated, 10 bytes not processed"). It would raise an error in `ImageFileSource`. It is not repaired; any evaluation must either exclude it with a recorded reason or decide explicitly how to handle it.
- Box validity checks do not apply (no boxes).
- Numbering gaps: 18 numbers inside img533 to img658 have no file (every 14th pair, for example 541 and 542, 555 and 556): consistent with a larger original sequence from which some images were removed; the reason is unknown.

## Scene structure and independence

The frames are CCTV views of real classrooms from high corner positions, with burned-in date/time and room labels. The numbering cycles with period 14: the 12 occupied classes of `number mod 14` each have exactly one resolution and exactly 9 images, matching 12 fixed camera views photographed 9 times (images 98 apart show the same room label). **This grouping is inferred** from numbering, resolution and a 16-image visual sample, not documented.

Consequences: the 108 images are 12 scenes x 9 snapshots, so the effective independent sample size is closer to 12 than 108; the same room appears repeatedly (near-identical empty-room frames exist: some pairs differ by under 0.5 grey levels on a 32x18 thumbnail); results must be reported per scene, and any confidence statement must be clustered by scene. By inferred scene: three scenes are (nearly) empty (class 7: all nine images 0; classes 11 and 12: 0 to 3), the others range from about 20 to 60, and one scene (class 13) varies from 13 to 61 as people enter and leave.

## EXIF / coordinate-system audit

Orientation tag present in **0 of 108** images (no EXIF at all), so there is no non-normal orientation and `ImageFileSource` loads them exactly as stored. Labels are counts, so there is no coordinate convention to prove or transform.

## Provenance and licence

**PROVENANCE NOT YET VERIFIED.** No local file names a dataset, author, source URL, citation or licence. The user describes it as public. The only in-image information is burned-in text: dates of 11-13-2024 and room labels. **Licence: unknown.**

## YOLOX training-overlap assessment

YOLOX-Tiny is COCO-pretrained (images collected by about 2014 to 2017). **TRAINING OVERLAP: UNLIKELY, not verified.** Evidence: burned-in timestamps of 13 Nov 2024 on the sampled frames and the CCTV character of the images (not COCO photographs, not a COCO repackaging by appearance). This is inferred from image content, not from documentation, so it does not establish independence on its own.

## Existing splits

None (no train, validation or test files or fields). No split was created.

## VESTA suitability: PARTIALLY SUITABLE

For: real classroom CCTV frames from fixed high corner cameras, close to VESTA's intended viewpoint; seated people, heavy occlusion, small and distant targets, glare and exposure changes, dense scenes up to 61 people, 16 empty-room negatives, several resolutions. Against: image-level counts only (no boxes, so no IoU matching), count definition undocumented, only 12 scenes, provenance and licence unverified, one truncated file, burned-in timestamps and identifiable faces (privacy handling needed), people are very small at a 416 input (a possible stress test, not something to tune on).

## Held-out eligibility: HELD-OUT ELIGIBILITY NOT YET ESTABLISHED

Satisfied: not used for VESTA detector development, threshold selection or preprocessing decisions (the detector has never been run on it); training overlap judged unlikely. Not yet satisfied: provenance and licence unverified; the label definition is undocumented, so label validity for our evaluation is not established; no split design. It must not be called held-out until the provenance, licence and label definition are confirmed.

## Metrics this dataset supports

Possible: per-image count error, bias, absolute error and MAE (by image and by scene), relative error on non-empty images, and an empty-room false-alarm rate (16 images with count 0, nine of them from one scene, so scene-limited). **Not possible without new human box annotation: IoU-based TP, FP, FN, precision, recall, F1.** A count error of 0 can hide cancelling errors (Task 5D). With no boxes there is no ignore-region logic, so "raw detector count versus label" is the only count view, and it is meaningful only if the label counts every person the detector might fire on.

## Proposed minimal adapter (not implemented)

A count-only evaluation path: read `labels.csv` into a file-to-count mapping; run the frozen detector through `ImageFileSource`; compare `DetectionResult.count` with the label; record failures (for example `img575.jpg`) explicitly rather than skipping silently; report per image and per inferred scene (`number mod 14`, flagged as inferred). It should sit beside `vision/evaluate.py`, not inside the box-matching code, and change nothing in the detector.

## Tests

No code changed. Full suite **285 passed, 5 skipped, 0 failed**, identical to the reference.

## Next action

Verify provenance, licence, the meaning of `count`, and the reason for the missing images and the truncated file, before any detector run.

---

# Week 4 Task 6B — Complete Classroom Data Audit

Date: 2026-10-06. Status: **Week 4 IN PROGRESS.** Audit only. **YOLOX was not run on this dataset**, no predictions or overlays exist, and nothing was tuned. The three VESTA pilot photographs stay the DEVELOPMENT / PILOT set.

Correction to Task 6A: the wording "12 scenes" and "effective sample about 12" is replaced by **fixed camera/viewpoint groups containing correlated temporal frames** (see below). Task 6A also said provenance and licence were unverified from local files; they are now taken from the public Kaggle listing (see Provenance and Licence). The 6A claim that all frames were dated 11-13-2024 was true only of `images13.9`; the full dataset covers two days.

## Dataset location and structure

Under the git-ignored `vision_assets/` (development path only; the images show identifiable people and must not be published): a folder `Classroom-Data (3)/Classroom-Data/` holding the **six original directories** `images13.4` to `images13.9` (each an `images/` subfolder plus one `labels.csv`), and, separately, `photos/photos/` (30 JPEG, `labels.csv`, `ReadMe.txt`) which is the dataset's other component. The six-directory structure was preserved. The old standalone `images13.9` copy is no longer at the top level (it is the one inside `Classroom-Data`).

## Six-directory inventory

| Directory | Images | CSV rows | Filename range | Resolutions | Count min / median / mean / max | Empty (count 0) | Strict-decode failures |
|---|---|---|---|---|---|---|---|
| images13.4 | 119 | 119 | img1 to img140 | 1920x1088 (39), 1280x720 (50), 1920x1080 (30) | 0 / 31 / 33.2 / 57 | 6 | 4 |
| images13.5 | 84 | 84 | img141 to img238 | 1920x1088 (28), 1280x720 (35), 1920x1080 (21) | 0 / 50 / 38.0 / 63 | 21 | 4 |
| images13.6 | 119 | 119 | img239 to img378 | 1920x1088 (40), 1280x720 (49), 1920x1080 (30) | 0 / 50 / 38.7 / 65 | 30 | 3 |
| images13.7 | 72 | 72 | img379 to img462 | 1920x1088 (24), 1280x720 (30), 1920x1080 (18) | 0 / 45.5 / 36.9 / 63 | 18 | 1 |
| images13.8 | 60 | 60 | img463 to img532 | 1920x1088 (20), 1280x720 (25), 1920x1080 (15) | 0 / 46 / 37.8 / 63 | 13 | 2 |
| images13.9 | 108 | 108 | img533 to img658 | 1920x1088 (36), 1280x720 (45), 1920x1080 (27) | 0 / 36 / 31.8 / 61 | 16 | 1 |
| **Total** | **562** | **562** | img1 to img658 | | sum of counts **20,116** | **104** | **15** |

Local size: 63,864,904 bytes for the six directories (65,813,488 including `photos`). The Kaggle listing states 64,213,029 bytes; the numbers do not match and the difference is unexplained, so size cannot be used as an integrity check.

## Label schema and linkage

All six `labels.csv` use the same two columns, `image_file_name,count`; counts are integers, with no nulls. Verified: every row points to an existing image and every image has a row (0 and 0); 0 duplicate ids within a directory; 0 filename reuse across directories (img1 to img658 are unique); 0 exact duplicate files (MD5) within or across directories. No box, class, split or confidence fields exist anywhere in the six directories.

## Decode integrity

Strict full decode of all 562 images: **547 decode, 15 fail** ("image file is truncated", missing 0 to 56 bytes):

| Directory | Image | Label |
|---|---|---|
| images13.4 | img27, img53, img63, img140 | 0, 28, 14, 17 |
| images13.5 | img162, img166, img197, img218 | 49, 0, 53, 59 |
| images13.6 | img274, img281, img291 | 52, 53, 0 |
| images13.7 | img456 | 55 |
| images13.8 | img470, img491 | 57, 51 |
| images13.9 | img575 | 45 |

`img575.jpg` is also truncated (10 bytes, 183,388 bytes long) in this complete copy, so it is not an artefact of the earlier partial subset, and there is **no intact copy to substitute**. I cannot tell whether the truncation is in the Kaggle files or in the local copy (no source checksum is available). No file was repaired. Preregistered rule: **Images failing strict decode are excluded for technical integrity reasons before model inference.** This rule does not depend on detector output. EXIF: 0 of 562 images carry any EXIF, so no orientation issue exists.

## Camera and temporal structure

Documented by the dataset: nothing about cameras, rooms or sessions (the listing has a one-line description only).

Inferred from the files and images (not documented):
- The filenames cycle with period 14. Numbers whose remainder mod 14 is 9 or 10 never appear (47 cycles, 94 absent numbers) plus two stray gaps (img85, img364); this leaves **12 numbering classes, each with exactly one resolution and 46 to 47 images**. Burned-in room labels match across directories (for example class 2 reads "2CSEA1" in all six), so each class is a **fixed camera/viewpoint**. The three resolutions correspond to camera types (class 5, 7, 13: 1920x1080; 1, 8, 11, 12: 1920x1088; the rest 1280x720).
- The six directories are **consecutive time blocks of the same 12 cameras**, not different classrooms, train/validation/test, or occupancy ranges: for camera class 2 the burned-in stamps read Monday 11-11-2024 15:11 to 15:44 in `images13.4`, then Wednesday 11-13-2024 14:39 to 14:44 (13.5), 14:50 to 14:57 (13.6), 15:00 to 15:04 (13.7), 15:07 to 15:10 (13.8), 15:13 to 15:19 (13.9). The first frames of all 12 cameras in `images13.4` show Monday 11-11-2024, and the last frames in `images13.9` show Wednesday 11-13-2024, so the first directory is a different day. Camera clocks are not synchronized with each other (stamps differ by minutes between cameras within one cycle), and one camera type uses another stamp format.
- Each camera contributes about 47 frames over two days; 3 cameras (classes 7, 11, 12) are almost always empty (zero counts in 40, 31 and 30 of their images) and one (class 13) varies from 0 to 62. Frames from one camera are correlated (same room, same furniture, often the same people); two empty-room frames from one camera can be near-identical.
- **Proposed stable grouping: camera/viewpoint group (12, `number mod 14`), optionally combined with the session day.** Images are not independent scenes; no confidence statement may treat the 547 images as independent samples.

## `photos/photos/` (second component)

30 JPEGs (about 1000 x 300 pixels, no decode failures) with `labels.csv` columns `filename,total,duplicates,original`. The `ReadMe.txt` states that `total` is people combined in both images, `duplicates` is people in both, `original` is unique people (`total - duplicates = original`; this holds in all 30 rows). Label paths begin `/kaggle/input/classroom-data/photos/photos/`, confirming it belongs to the same Kaggle dataset. Its labels describe stitched pairs of views and unique-person counting, which is **not** the same quantity as `DetectionResult.count` for one frame, so it is outside the frozen evaluation population.

## Count semantics

**COUNT SEMANTICS STRONGLY SUPPORTED BUT EXACT ANNOTATION RULES UNDOCUMENTED.** The Kaggle listing says only "CC captures of Images of Students in Classroom". The `count` column is not defined. Support: the dataset is about people in classrooms, the sibling `photos` component explicitly counts people, counts range 0 to 65, and in the 16 images viewed earlier the numbers matched the visible crowd roughly and empty rooms had 0. Nothing documents the treatment of teachers, partially visible, edge, head-only or occluded people, reflections or screens, and no such rules are assumed.

## Provenance and licence

| Item | Value (public Kaggle listing, read 2026-10-06) |
|---|---|
| Dataset | Classroom-Data ("Classroom Data") |
| Publisher | Harinivas Ganjarla (Kaggle user) |
| Platform / URL | Kaggle, https://www.kaggle.com/datasets/harinivasganjarla/classroom-data |
| Licence | **Kaggle listing licence: Apache 2.0.** |
| Last modified | 2024-12-26 |
| Description | "CC captures of Images of Students in Classroom" (capture source not described) |
| Listed size | 64,213,029 bytes |
| Citation | none shown |

Nothing in the local files states provenance; the identification rests on the user's statement, the `/kaggle/input/classroom-data/` paths in `photos/labels.csv`, and the listing. No stronger claim about image rights or privacy consent is made: the Apache 2.0 licence on the listing does not by itself establish consent of the people shown.

## YOLOX training-overlap assessment

**TRAINING OVERLAP VERY UNLIKELY** (not proven). YOLOX-Tiny's weights are COCO-pretrained (images collected by about 2014 to 2017, model released 2021). These frames are burned-in classroom CCTV stamped 11 and 13 November 2024, and the listing was last modified on 2024-12-26, after both. They are not COCO photographs.

## Official split status

**No official split documented**; the six directories are time batches. No random split was created. The data should be used as one **external count-evaluation set** with camera/viewpoint-aware reporting, not repartitioned.

## The earlier 108-image directory

`images13.9` is the last time block and was copied first for no documented reason. **DO NOT USE THE 108-IMAGE SUBSET AS THE HEADLINE EVALUATION SET**: choosing one time block would be an arbitrary subset, drops 454 other frames, covers only the latest time block (about 15:13 to 15:35 on one day), and it was the folder inspected in Task 6A. It remains only a subset of the full set.

## Frozen evaluation population (fixed before any inference)

- **Included:** all six directories `images13.4` to `images13.9`, every image that passes a strict JPEG decode: **547 images** (13.4: 115, 13.5: 80, 13.6: 116, 13.7: 71, 13.8: 58, 13.9: 107), labelled people **19,583**, counts 0 to 65 (mean 35.8, median 42).
- **Excluded (15)**, one reason only, "strict JPEG decode failed (truncated file)": img27, img53, img63, img140 (13.4); img162, img166, img197, img218 (13.5); img274, img281, img291 (13.6); img456 (13.7); img470, img491 (13.8); img575 (13.9). Excluded labels sum to 533 people, three of them zero-count images.
- **Also excluded:** the `photos/photos/` component (different label meaning, 30 images).
- **Camera/viewpoint groups:** 12 (inferred). **Empty images (count 0) in the population: 101.**
- No exclusion depends on detector behaviour. The manifest is `vision_assets/annotations/classroom_data_eval_manifest.csv` (git-ignored): 562 rows with directory, file, count, inferred camera group, resolution, inferred session date, included flag and exclusion reason. **Manifest SHA-256: `3189a8bb6ded572bb41f9bc687f98d47039d36e9386947adfbc32df96cd0c553`.**

## Metrics this dataset supports (count-only; boxes do not exist)

Supported: for each image, predicted count (`DetectionResult.count`), signed error (predicted minus label), absolute error; MAE; RMSE if justified; relative absolute error for label > 0; count bias (mean signed error, undercount/overcount tendency); empty-room false detections (per zero-count image, and fraction of zero-count images with at least one detection); per-camera-group MAE and the macro-average of group MAEs. **Predicted count = `DetectionResult.count`; ground-truth count = `labels.csv` count.** The evaluable-count definition (TP + FP) from the VESTA pilot is **not** used here: it depended on ignore boxes, and this dataset has none.

**Not supported** (no bounding-box ground truth): IoU TP/FP/FN, detection precision, recall, F1, mAP. A count error of 0 can still hide errors that cancel (see Task 5D), and the label rules are undocumented, so these are descriptive count-agreement numbers, not a student-count accuracy.

## Frozen detector configuration

Model YOLOX-Tiny ONNX (`yolox_tiny.onnx`, SHA-256 `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7`); input 416 x 416; score threshold 0.30; NMS 0.45; preprocessing the current verified raw BGR 0..255 YOLOX pipeline; ONNX Runtime CPUExecutionProvider. **The detector configuration was frozen before public-dataset inference. No model, threshold, NMS value, preprocessing rule or input resolution will be changed after observing these evaluation results.**

## Tests

No code changed. Full suite **282 passed, 8 skipped, 0 failed**. This differs from the 285 / 5 reference only because the Windows Application Control block on the openWakeWord real-engine import is active again: `import openwakeword` now fails with `DLL load failed while importing _weight_vector: An Application Control policy has blocked this file` (a different scikit-learn binary from the `_loss` file blocked on 2026-10-02), so three real-engine tests skip again (reference run: they passed, with only the recorded-clip test skipping). It is an environment state that comes and goes on this PC, not a code change; the 8 skips are 4 ReSpeaker hardware-gated and 4 openWakeWord (see `WEEK3_REPORT.md`).

## Next action

Run the frozen count-only evaluation on the 547-image population (not started).

---

# Week 4 Task 6C — Frozen External Classroom Count Evaluation

Date: 2026-10-06. Status: **Week 4 IN PROGRESS.** One frozen evaluation, run once. **SOFTWARE-VALIDATED EXTERNAL COUNT EVALUATION** (Windows development PC, offline images). No parameter was tuned using these results, and none may be: any improvement attempt is a new experiment with its own development protocol.

## Pre-inference source check

The optional check for intact copies of the 15 truncated JPEGs could not be completed: the anonymous Kaggle download endpoint returned 404 and the Kaggle CLI is not installed. A stored Kaggle credentials file exists on the PC but was **not used** (no authorisation to use it). No source correction happened, so the pre-registered population stands.

## Frozen population and manifest

547 decodable images from `images13.4` to `images13.9` (562 minus 15 strict-decode failures), 19,583 labelled people, 101 empty (label 0) images, 12 inferred camera/viewpoint groups. Manifest `classroom_data_eval_manifest.csv`, **SHA-256 `3189a8bb6ded572bb41f9bc687f98d47039d36e9386947adfbc32df96cd0c553`**, verified before inference (the runner stops without inference if the hash differs). The separate `photos/photos` component is excluded (different label meaning). Frames from one camera are temporally correlated; the 547 frames are **not** 547 independent scenes.

## Frozen detector

YOLOX-Tiny ONNX (SHA-256 `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7`), input 416 x 416, score threshold 0.30, NMS 0.45, raw BGR 0..255 preprocessing, ONNX Runtime 1.30.0 CPUExecutionProvider, Python 3.13.14, run started 2026-10-06T16:01:44. Predicted count = `DetectionResult.count`; ground truth = `labels.csv` count. Completion: **547 of 547 SUCCESS, 0 failures.**

## Global count results (frame-weighted, count agreement only)

| N | GT total | Predicted total | MAE | RMSE | Bias (mean signed error) | Median AE | Exact | Within +-1 | Within +-2 | Within +-5 |
|---|---|---|---|---|---|---|---|---|---|---|
| 547 | 19,583 | 3,236 | **29.88** | 36.04 | **-29.88** | 34 | 19.4 % (106) | 21.4 % | 22.5 % | 22.9 % |

The exact and within-N shares are dominated by the 101 empty frames, which are all predicted 0. On the 446 frames with label above 0: exact 5, within +-1: 16, within +-2: 22, within +-5: 24 (5.4 %). Mean relative absolute error on label > 0 (zero-label frames excluded): 0.798. The detector predicted 16.5 % of the labelled total; the median predicted-to-label ratio on label > 0 frames is 0.16.

## Camera-group results (12 inferred groups, `number mod 14`)

| Group | N | GT range | Pred range | MAE | RMSE | Bias | Median AE | Empty frames | False detections on empty |
|---|---|---|---|---|---|---|---|---|---|
| cam00 | 45 | 26-51 | 5-14 | 30.22 | 31.02 | -30.22 | 31 | 0 | n/a |
| cam01 | 42 | 28-61 | 6-13 | 38.07 | 38.75 | -38.07 | 39.5 | 0 | n/a |
| cam02 | 47 | 46-65 | 2-11 | 52.26 | 52.50 | -52.26 | 54 | 0 | n/a |
| cam03 | 47 | 45-65 | 4-11 | 51.09 | 51.36 | -51.09 | 53 | 0 | n/a |
| cam04 | 47 | 50-63 | 2-10 | 50.94 | 51.04 | -50.94 | 51 | 0 | n/a |
| cam05 | 47 | 20-44 | 2-11 | 23.89 | 24.76 | -23.89 | 22 | 0 | n/a |
| cam06 | 47 | 20-52 | 1-13 | 32.64 | 33.10 | -32.64 | 33 | 0 | n/a |
| cam07 | 46 | 0-31 | 0-12 | 1.33 | 4.56 | -1.33 | 0 | 40 | 0 |
| cam08 | 42 | 17-58 | 5-14 | 34.19 | 37.36 | -34.19 | 43.5 | 0 | n/a |
| cam11 | 45 | 0-31 | 0-12 | 3.27 | 7.46 | -3.27 | 0 | 30 | 0 |
| cam12 | 46 | 0-31 | 0-12 | 2.76 | 5.93 | -2.76 | 0 | 29 | 0 |
| cam13 | 46 | 0-62 | 0-15 | 37.15 | 42.73 | -37.15 | 50 | 2 | 0 |

**Camera-macro MAE (mean of the 12 camera MAEs, equal weight per camera): 29.82.** The frame-weighted global MAE is 29.88; the two are different quantities and happen to be close. The densest rooms (cam02, cam03, cam04, about 45 to 65 people) show MAE of about 51 to 52; the almost-empty cameras show about 1 to 3.

## Directory / time-block results (secondary; these are time blocks, not classrooms)

| Directory | N | GT range | Pred range | MAE | RMSE | Bias |
|---|---|---|---|---|---|---|
| images13.4 | 115 | 0-57 | 0-14 | 26.49 | 30.84 | -26.49 |
| images13.5 | 80 | 0-63 | 0-12 | 32.58 | 38.80 | -32.58 |
| images13.6 | 116 | 0-65 | 0-14 | 33.21 | 39.59 | -33.21 |
| images13.7 | 71 | 0-63 | 0-14 | 31.30 | 37.82 | -31.30 |
| images13.8 | 58 | 0-63 | 0-13 | 31.67 | 38.08 | -31.67 |
| images13.9 | 107 | 0-61 | 0-15 | 26.02 | 32.50 | -26.02 |

## Empty-room results (count-level false alarms, not object-level false positives)

101 zero-label frames: **all 101 predicted 0**; 0 with at least one detection (fraction 0.0); total false person detections 0; mean predicted count 0; maximum 0. Caveat: the empty frames come from only three or four cameras (cam07, cam11, cam12 mostly, cam13 twice), so this is a statement about those rooms and conditions, not about empty rooms in general.

## Occupancy bands (fixed before inspecting results)

| Band | N | MAE | Bias |
|---|---|---|---|
| GT = 0 | 101 | 0.00 | 0.00 |
| GT 1-10 | 21 | 1.05 | -1.05 |
| GT 11-20 | 15 | 8.67 | -8.67 |
| GT 21-40 | 131 | 23.51 | -23.51 |
| GT > 40 | 279 | 47.01 | -47.01 |

## Undercount / overcount

Prediction below label: **441 frames**; equal: 106 (101 empty plus 5 non-empty); above label: **0**. Mean undercount magnitude 37.07 people on the undercounted frames; no overcounts, so no overcount magnitude. The frozen baseline never overcounts and undercounts systematically, and the shortfall grows with occupancy; the maximum prediction anywhere is 15 against labels up to 65.

## Timing (Windows CPU software performance, 547 images; not Raspberry Pi, not camera FPS, not VESTA real-time performance)

| Stage (ms) | Mean | Median | Min | Max | p95 |
|---|---|---|---|---|---|
| Preprocess | 12.68 | 14.18 | 6.29 | 34.83 | 18.93 |
| Inference | 54.72 | 50.26 | 42.83 | 159.35 | 70.83 |
| Postprocess | 1.23 | 1.16 | 0.53 | 6.38 | 1.86 |
| Total detector | 68.63 | 64.93 | 51.69 | 197.33 | 88.51 |

These images are 1280x720 to 1920x1088, so preprocessing is much cheaper than for the 24 MP pilot photos.

## Interpretation

On this external, count-labelled CCTV set the frozen YOLOX-Tiny baseline (416 input, score 0.30, NMS 0.45) counts about one sixth of the labelled people and undercounts on 441 of 547 frames, increasingly so in dense rooms; it raises no count-level false alarms on empty frames. This is count agreement, not a student-count accuracy and not detection precision or recall. Plausible contributors (people are very small at a 416 input, heavy occlusion, crowding, the score threshold) are hypotheses; this evaluation did not test any of them and does not identify the cause.

## Limitations

- **Count labels only**: no boxes, so nothing here is box-level or object-level accuracy.
- **COUNT SEMANTICS STRONGLY SUPPORTED BUT EXACT ANNOTATION RULES UNDOCUMENTED** (head-only, partial, teacher, edge and heavily occluded people are not defined), so part of the gap could reflect label conventions; the size of that part is unknown.
- **Correlated temporal frames**: 12 fixed cameras x about 47 frames; the effective independent sample is far below 547, and no confidence interval is given.
- **Camera grouping inferred** from numbering, resolution and burned-in labels, not documented.
- 15 truncated images were excluded; they could not be restored.
- Windows development PC only; offline images, no live camera; Raspberry Pi performance not measured.

## Comparison with the three-image VESTA pilot (conceptual only)

The pilot used human-reviewed bounding boxes (IoU-based precision and recall) on three development photographs and was not independent. The public set has external image-level counts only, is much larger and has a repeated-camera structure. Pilot precision/recall and public count MAE are different metrics and are not compared numerically.

## Code, tests and reproducibility

New `vision/count_evaluate.py` (manifest check, existing `ImageFileSource` and `YoloxOnnxPersonDetector` unchanged, per-image CSV, camera/directory/band summaries, JSON summary) and `tests/test_count_evaluate.py` (16 synthetic tests: exact match, under- and over-count, zero label, no relative error for zero label, MAE, RMSE, bias, camera-macro versus frame-weighted MAE, bands, empty-room analysis, timing, invalid manifest entries, explicit failed-image accounting). Outputs in git-ignored `vision_assets/results/public_count_eval/`. Full suite **298 passed, 8 skipped, 0 failed** (282 + 16 new; the 8 skips are 4 ReSpeaker hardware-gated and 4 openWakeWord blocked by Windows Application Control, which is active in this environment).

## Week 4 status

Software milestones: frame acquisition (offline image source; real camera NOT AVAILABLE), person detection and bounding boxes (done, SOFTWARE-VALIDATED), basic occupancy count (`DetectionResult.count`, a detection count), performance measurement (Windows only; Pi not measured). Classroom ROI is also a Week 4 task and has **not** been done, and the real camera and Pi measurements are pending, so **Week 4 stays IN PROGRESS.**

---

# Week 4 Task 7 — Classroom ROI Foundation

Date: 2026-10-06. Status: **Week 4 software foundation complete; physical camera and Raspberry Pi validation pending.** Architecture and infrastructure only. The frozen 547-image public benchmark was **not** rerun, not used to choose any ROI geometry, and not used to compare ROI shapes; no claim is made that an ROI improves detector performance. The detector, model, threshold (0.30), NMS (0.45), 416 input and preprocessing are unchanged.

## Architecture placement

```
Frame -> YoloxOnnxPersonDetector -> Detection list -> ROI filter -> occupancy count
```

The ROI is a property of a camera/viewpoint, not of the model, so it is applied **after** inference in the new `vision/roi.py`. It does not crop frames, does not touch YOLOX, never changes boxes or confidences, and returns the original `Detection` objects. `DetectionResult.count` still means all person detections; the ROI count is a separate number (`RoiCount(total, inside, outside)`), so both can be obtained.

## Representation

Normalised image coordinates, 0.0 to 1.0 (origin top-left, x to the right, y downward), so one definition works at any resolution. `Roi.rectangle(x1, y1, x2, y2)`, `Roi.polygon(points)` (plain ray casting, no geometry dependency, concave polygons supported), and `Roi.full_frame()`. `None` means no ROI = the entire frame. Invalid definitions raise `RoiError`: coordinates outside 0..1, non-finite values, a rectangle with x1 >= x2 or y1 >= y2, a polygon with fewer than 3 points or zero area. Self-intersecting polygons are not detected (a stated limitation).

## Membership rule (chosen before implementation, not from any evaluation)

A detection belongs to the ROI when its **bottom-centre point** `((x1 + x2) / 2 / frame_width, y2 / frame_height)` lies inside the ROI. Reasoning: the bottom-centre approximates where the person physically stands or sits, so a box that merely crosses the ROI border (an arm, a head leaning forward) does not change which side the person is on, and tall boxes are not penalised for their height; requiring the whole box inside would drop people near the border. Known limitation: under occlusion (seated students behind monitors) the visible box bottom is where the occluder starts, not the floor point, so the anchor is least reliable exactly in the crowded scenes; whether that matters can only be judged with a calibrated real camera.

## Boundary rule

A point exactly on the ROI border (rectangle edge, polygon edge or vertex) counts as **inside**.

## Configuration

`config/settings.py` gains `ROI_ENABLED = False`, `ROI_TYPE = "rectangle"`, `ROI_POINTS = ()`. `roi_from_settings()` returns `None` unless enabled, and rejects an unknown type or a wrong number of points. **There is no default classroom geometry**: no VESTA camera has been mounted or calibrated. No multi-camera configuration and no three-zone partition were added (Week 5).

## Visualisation (debug aid only)

`vision/annotate.py::draw_detections` / `save_annotated` accept an optional ROI: the outline is drawn in cyan and boxes whose anchor is outside are drawn red instead of green. Without an ROI the output is unchanged. No overlays were generated for any dataset.

## Tests and failure check

`tests/test_roi.py`, 28 synthetic tests (no images, model or private/public data): inside, outside, bottom-centre exactly on the boundary (inside) and 2 px beyond (outside), box overlapping the ROI with the anchor outside, box mostly outside with the anchor inside, full-frame ROI, no ROI, the same relative position at 1000x500 and 2000x1000, invalid rectangles (6 cases), originals preserved with separate counts, polygon inside/outside/concave, polygon boundary and vertices, polygon anchor, invalid polygons (6 cases), default configuration is no ROI, configuration builders and rejection, annotation colours with the input untouched.

**Mutation check:** four deliberate breakages each failed the tests and were reverted: exclusive instead of inclusive boundary (2 tests failed), anchor on the box top instead of the bottom (1), whole-box-inside rule instead of bottom-centre (2), polygon edges and vertices no longer inside (1).

Full suite **326 passed, 8 skipped, 0 failed** (298 + 28 new). The 8 skips are unchanged: 4 ReSpeaker hardware-gated and 4 openWakeWord blocked by Windows Application Control.

## Calibration status and relation to Week 5

**Real camera ROI calibration: NOT AVAILABLE / NOT CALIBRATED.** Only the mechanism exists, tested on synthetic fixtures. A real classroom ROI needs the physically mounted VESTA camera. Week 5's LEFT / CENTER / RIGHT zones are expected to build on the same normalised geometry (a zone is a region plus the same bottom-centre membership), but no zone logic exists yet.

---

# Week 4 Software Closeout

Date: 2026-10-06. This section closes the **software** portion of Week 4 and **supersedes any earlier "IN PROGRESS" wording** in this file for the software foundation; the earlier task sections are kept as historical record. No code was changed for the closeout.

**WEEK 4 SOFTWARE FOUNDATION COMPLETE. PHYSICAL CAMERA / RASPBERRY PI VALIDATION PENDING HARDWARE. KNOWN MODEL LIMITATION: SEVERE DENSE-CLASSROOM UNDERCOUNTING.**

"Software complete" means the planned Week 4 software mechanisms exist, are tested and are truthfully documented. It does not mean the detector is accurate enough, that hardware was validated, that the real camera was calibrated, or that Raspberry Pi performance is known.

## Authoritative roadmap (CLAUDE.md, Week 4)

Goal: build the camera perception pipeline. Tasks: camera frame acquisition; person detection; bounding boxes; classroom region of interest; basic occupancy count; performance measurement. Deliverable: working camera perception module + initial evaluation. The roadmap says "camera frame acquisition", so the real-camera part of that task cannot be closed without the physical camera and is classified PENDING HARDWARE below; the software abstraction it will plug into is complete.

## Requirement-to-evidence matrix (each item verified against the code, not the prose)

| Requirement | Implementation | Evidence / tests | Classification | Status |
|---|---|---|---|---|
| Camera frame acquisition (software) | `vision/frames.py`: `Frame`, `FrameSource`, `ImageFileSource` (strict decode, EXIF orientation applied), `ArrayFrameSource` (fixture) | `tests/test_vision.py` (35 passed): array/file sources, end-of-frames, missing and corrupt file errors, EXIF orientation test with mutation check | SOFTWARE-VALIDATED (offline images) | COMPLETE |
| Camera frame acquisition (real camera) | none; a Pi/CSI/USB camera backend is not written because the camera model and interface are not specified | none | PENDING HARDWARE (NOT AVAILABLE) | NOT COMPLETE (hardware) |
| Person detection | `vision/detector.py` `YoloxOnnxPersonDetector`: `yolox_tiny.onnx` (20,219,662 bytes, SHA-256 `427cc366...b0f7`), input 416x416, raw BGR 0..255 (no normalisation), strides 8/16/32, person class 0, score 0.30, NMS 0.45, explicit failure on bad output/inference | `tests/test_vision.py`; real-model contract test; blank-frame real run; positive real-image smoke test (10 detections on a development photo); preprocessing settled by a real-data matrix (Task 4) | SOFTWARE-VALIDATED | COMPLETE |
| Bounding boxes | `Detection` (validated pixel box, confidence, frame size), `vision/annotate.py` overlay | box validation, clipping, annotation tests; human-reviewed overlays | SOFTWARE-VALIDATED | COMPLETE |
| Classroom region of interest | `vision/roi.py`: normalised rectangle/polygon, bottom-centre anchor, boundary inclusive, default no ROI, `config/settings.py` keys | `tests/test_roi.py` (28 passed), 4 mutants caught; no ROI code in `detector.py` or `count_evaluate.py` | ROI MECHANISM: SOFTWARE-VALIDATED. REAL CLASSROOM ROI CALIBRATION: PENDING HARDWARE (NOT CALIBRATED) | MECHANISM COMPLETE |
| Basic occupancy count | `DetectionResult.count`; `count_in_roi()` for inside/outside | count tests; pilot count views; 547-image count evaluation | COUNTING MECHANISM: SOFTWARE-VALIDATED. COUNTING QUALITY: LIMITED / POOR in dense classrooms | MECHANISM COMPLETE |
| Performance measurement | `vision/benchmark.py`; per-stage timing in `DetectionResult`; timings in `count_evaluate` | blank-frame, pilot-photo and 547-image timings (median total about 65 ms per 720p/1080p image) | WINDOWS CPU SOFTWARE PERFORMANCE: MEASURED. RASPBERRY PI: NOT MEASURED. LIVE CAMERA: NOT MEASURED | WINDOWS COMPLETE; PI/CAMERA PENDING HARDWARE |
| Deliverable: working perception module | frame source -> detector -> boxes -> ROI -> count, all exercised on offline frames | the 110 vision-related tests (35 + 31 + 16 + 28) and the evaluations below | SOFTWARE-VALIDATED (offline input) | COMPLETE for offline input |
| Deliverable: initial evaluation | `vision/evaluate.py`, `audit.py`, `count_evaluate.py` | pilot and frozen external benchmark below | SOFTWARE-VALIDATED | COMPLETE |

## Evaluations (kept separate; no combined headline metric)

**A. Three-image development/pilot evaluation** (human-reviewed boxes; IoU 0.50; supports detection metrics; development data, not independent, not final performance): GT persons 20, ignore regions 19, TP 11, FP 6, FN 9, precision 0.647, recall 0.550, preferred evaluable-count MAE 1.00 per image (raw-count MAE 2.33 is superseded because it mixed populations).

**B. Frozen external count benchmark** (Kaggle Classroom-Data, 547 decodable images of 562; image-level counts only; no boxes; 12 inferred fixed camera groups with temporally correlated frames; count semantics strongly supported but exact annotation rules undocumented): GT total 19,583; predicted 3,236 (16.5 %); MAE 29.88; RMSE 36.04; bias -29.88; camera-macro MAE 29.82; undercount on 441 frames, equal on 106 (101 of them empty), overcount on 0; all 101 label-zero frames predicted zero; the largest prediction anywhere is 15 against labels up to 65.

**Frozen-benchmark seal.** Verified at closeout without rerunning the detector: the five result files in `vision_assets/results/public_count_eval/` all carry the Task 6C modification time (2026-10-06 16:02:31) and still contain the documented values; the recorded manifest and model hashes equal the files on disk. SHA-256 of the result files: `per_image_results.csv` `2de6c4f38c49dc0b3c29b079c50d61940eb367e15fd539006cbc7ef0bd5c994f`, `evaluation_summary.json` `e0bcbd7fa441b47c73fa37f62fc2d49bfec07d150477feb81c071766f82030d0`, `camera_group_summary.csv` `ff3bdce7bab734afdeb330b6e7c8850d88cb3eb2277b0e3c363fa27461f4083a`, `directory_summary.csv` `ff4058f72c0e2d4f015487576a7e67f2dee70d50f69690be99acc8d64f4c79d2`, `occupancy_band_summary.csv` `e465c7dc0ac68574f292d714bd559c49a5b4064f8f9d2308648cd6c93b38ed3a`; manifest `3189a8bb6ded572bb41f9bc687f98d47039d36e9386947adfbc32df96cd0c553`. This is now the **FROZEN EXTERNAL BASELINE BENCHMARK**: later experiments may cite it as the historical baseline, but it must not become a tuning set while still being described as untouched external evaluation. Any improvement experiment needs its own development protocol with a separate split.

## Known limitations (no unsupported causal claims)

- Strong systematic undercounting in dense classrooms (frozen benchmark above); the shortfall grows with occupancy (band MAE: label 1 to 10: 1.05; 11 to 20: 8.67; 21 to 40: 23.51; above 40: 47.01).
- The detector's output maximum (15) is far below dense label counts (up to 65).
- Small and distant people at a 416 input, heavy occlusion and crowding are **plausible contributors, not a proven cause**; none was tested.
- Public count-label annotation rules are partly undocumented, so part of the gap may be a labelling-convention difference of unknown size.
- Public frames are temporally correlated and the camera grouping is inferred; there is no confidence interval.
- The ROI anchor (bottom-centre) is least reliable under occlusion.
- No live VESTA camera validation, no Raspberry Pi benchmark, no real ROI calibration.
- The three-image pilot is development data and its precision/recall are not independent performance.
- openWakeWord tests skip while Windows Application Control blocks a scikit-learn binary; this environment state comes and goes on this PC and changes the pass/skip split without any code change.

## Validation classification

| Area | Level |
|---|---|
| Software foundation (frames, detector, boxes, count, ROI mechanism, evaluators) | SOFTWARE-VALIDATED |
| Fake-session and synthetic-tensor detector tests | SIMULATED |
| Physical camera (frame acquisition, configuration) | PENDING HARDWARE; NOT AVAILABLE |
| Raspberry Pi 5 latency / throughput | PENDING HARDWARE; NOT MEASURED |
| Camera-to-detector end-to-end timing | PENDING HARDWARE; NOT MEASURED |
| Real classroom ROI calibration | PENDING HARDWARE; NOT CALIBRATED |
| Detector accuracy / student-count accuracy | NOT ESTABLISHED (limited and poor on the frozen benchmark) |
| HARDWARE-VALIDATED | nothing in Week 4 |

## Carry-forward items (no speculative implementation exists for any of them)

1. Real camera backend and frame acquisition (needs the camera model and interface from the hardware team; the Week 3 camera item is also still NOT AVAILABLE in `WEEK3_REPORT.md`).
2. Actual VESTA camera configuration (resolution, field of view, mounting).
3. Physical ROI calibration on the mounted camera, including whether the bottom-centre anchor suits the real view.
4. Raspberry Pi 5 latency and throughput of the same pipeline.
5. Camera-to-detector end-to-end timing.
6. Detector-quality improvement, if wanted, as a new experiment with a pre-registered development protocol and its own split (the frozen benchmark is not a tuning set).

Items 1, 2 and 4 depend on the Week 3 hardware interface validation and remain a Week 3 / hardware-team matter in the roadmap's sense; they are listed here only so they are not lost.

## Test state at closeout

Vision-related tests: 110 passed (35 + 31 + 16 + 28). Full suite: see the final run recorded in `WEEK4_STATUS.md`.
