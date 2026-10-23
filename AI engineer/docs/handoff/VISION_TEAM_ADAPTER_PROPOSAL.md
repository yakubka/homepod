# Proposal: use the AI engineer's YOLOX detector in the team backend

**For:** Ripon (V-61 vision, `services/vision/`), Javohir (V-02 `main.py`, v-04 model registry). **From:** Fatima (AI engineer).
**Status (updated 2026-10-22):** applied on the local branch `fatima-vision-team-adapter` for your review, NOT committed. Vision changes in your files: `services/vision/detector.py` (+`make_detector`, +`engine_occupancy`), `main.py` (uses both), `services/core/config.py` (+`"vision.detector": "mobilenet"`, +`"vision.yolox_lighting": False`). The same branch also carries the AI voice integration (see `AI_BACKEND_INTEGRATION_REPORT.md`). SOFTWARE-VALIDATED with mock frames and photos only; no camera or Raspberry Pi has run it.

**Confidence rule (resolved 2026-10-22, owner decision):** `OccupancyLoop` takes the LOWEST detection score as the frame confidence, and `fuse` drops the whole camera count below `occupancy.min_confidence` (0.4). YOLOX keeps detections from 0.30. The factory therefore runs the adapter with `min_confidence=cfg["occupancy.min_confidence"]`: weaker boxes are dropped and the frame stays usable. The floor is read at startup, so a live config change needs a restart.
- Measured on one local photo (img01.jpg, dataset label 53 people):
  - before: 6 detected (scores 0.34–0.59), frame confidence 0.34, so the engine saw no camera count;
  - after: 2 detected (0.47, 0.59), with confidence 0.47.
- The count now reaches the backend, but it is a severe undercount (2 of 53), and raising the floor lowers recall.

**Lighting use (added 2026-10-22, safe default pending your decision):** on 547 labelled images, YOLOX counts have MAE 31.99 at the 0.4 floor. So `engine_occupancy` wraps the loop in `AdvisoryOccupancy` unless `vision.yolox_lighting` is true:
- `GET /occupancy` shows the counts with `"used_for_lighting": false`;
- the engine sees "no camera count", so lights hold: never off on a camera zero, never a brightness from a count.
- Room-level presence on the same images: 96.9% of occupied rooms detected and 0 of 101 empty rooms reported occupied (dataset only, not on the device).
- Opting in (`vision.yolox_lighting: true`) passes counts to your rule unchanged. With it on, the photo above gives `center 50`. Not recommended.

## In one sentence

`AI engineer/vision/team_adapter.py` provides `YoloxTeamDetector`, a drop-in for your `services.vision.detector.Detector`: same `detect(frame)` output (`x, y, w, h, conf, cx, zone`), same equal-thirds zone rule, same `status()`. Your `OccupancyLoop`, `fuse`, `Runtime`, rules and API do not change.

## The switch (your files, needs your approval)

The call site is one line. Because `AI engineer/` is a folder with a space and not a Python package, a small factory reaches it; the default stays MobileNet, so nothing changes until the setting is flipped.

**main branch, `main.py`** — replace

```python
detector = Detector(width=cfg["camera_width"])
```

with

```python
detector = make_detector(cfg)
```

and wrap the loop:

```python
occupancy = engine_occupancy(cfg, OccupancyLoop(camera, detector, cfg["detection_interval"]))
```

As applied in `services/vision/detector.py` (shortened; the branch has the full version with log messages):

```python
def make_detector(cfg):
    kind = cfg.get("vision.detector", "mobilenet")
    if kind == "mobilenet":
        return Detector(width=cfg["camera_width"])
    if kind != "yolox":
        raise ValueError(f"unknown vision.detector {kind!r}, expected mobilenet or yolox")
    ai = str(Path(__file__).resolve().parents[2] / "AI engineer")
    if ai not in sys.path:
        sys.path.append(ai)          # appended: team packages keep precedence
    from vision.team_adapter import YoloxTeamDetector
    return YoloxTeamDetector(width=cfg["camera_width"], min_confidence=cfg["occupancy.min_confidence"])


def engine_occupancy(cfg, loop):
    if cfg.get("vision.detector") != "yolox" or cfg.get("vision.yolox_lighting", False):
        return loop
    from vision.team_adapter import AdvisoryOccupancy
    return AdvisoryOccupancy(loop)
```

plus two defaults in `services/core/config.py` (Yokubjon): `"vision.detector": "mobilenet"`, `"vision.yolox_lighting": False`. Note that `POST /config` has no authentication, so anyone on the network can change both keys; they take effect at the next restart.

**v-04 branch, `services/vision/service.py` `CameraCounter`** — the same factory replaces `Detector(width=cfg["camera_width"])`. Your registry already declares `ModelSpec("detector", "yolox_tiny.onnx", "onnx-yolox", ..., options={"size": 416})`, the same file and SHA-256 (`427cc366...`) and the same 416 input; pass its session in so no second session is built: `YoloxTeamDetector(width=..., session=registry.get("detector"))`.

## Behaviour you should know about

| Topic | Team Detector today | YoloxTeamDetector |
|---|---|---|
| No frame (`None`) | returns `[]` -> loop publishes 0 people, confidence 1.0 | raises -> loop publishes `source="none"`, `fuse` falls back |
| Inference error | swallowed, returns `[]` | raises (same fallback) |
| Replay-mode dict frame | simulated person | refused; keep the MobileNet/simulator path for replay |
| Zone width | configured `camera_width` | the actual frame width |
| Low confidence | conf from MobileNet | boxes below `occupancy.min_confidence` (0.4) are dropped before the loop |
| Used for lighting | yes | no by default (`AdvisoryOccupancy`); yes only with `vision.yolox_lighting: true` |

Open points for you, not changed by this proposal: an empty frame gets confidence 1.0 in `OccupancyLoop` (with an undercounting detector that can switch zones off); v-04 `CameraCounter` hard-codes confidence 0.9; replay mode labels simulated data `source="camera"`; the professor zone is not calibrated (V-62). Known limit: the detector undercounts dense classrooms (frozen benchmark MAE 29.88).

## Evidence (Windows, no hardware)

`AI engineer/tests/test_team_adapter.py`, run inside the team repository against your real `OccupancyLoop`, `fuse`, `Runtime(dry=True)` and replay-mode `Lights`: output shape and zone rule equal to your `Detector`; BGR frame reaches YOLOX as RGB; missing frame / inference error -> `source="none"` and `fuse` total `None`; 12 people in the centre -> `zone_light center 100`, lights untouched (dry); confidence below 0.4 not trusted; no device library imported in replay; `main.py` default unchanged; real YOLOX model on a 640x480 frame (when the model file is present). Re-introducing each adapter defect (missing frame -> `[]`, no colour flip, configured width, swallowed errors, wrong boundary) makes a test fail. Measured on the development PC only: about 83 ms per 640x480 frame on CPU; your registry target is p95 250 ms on the Pi, NOT measured.

## Acceptance before the switch is turned on

1. Your 94 tests and the AI suite pass with `vision.detector` at both values.
2. Replay mode unchanged (default MobileNet path, no device imports).
3. On the Pi with the real camera: frames arrive, counts change with people in view, p95 detection time recorded against 250 ms. Only then HARDWARE-VALIDATED.

## Message you can send

> Hi Ripon, Javohir — I've added a YOLOX drop-in for `services.vision.detector.Detector` at `AI engineer/vision/team_adapter.py` (same detect/status interface, fails as "no camera count" instead of 0 people). It's wired through a small factory in `services/vision/detector.py` and two lines in `main.py` (and `CameraCounter` on v-04 would need the same), with `vision.detector` defaulting to `mobilenet`, so nothing changes until we flip it. Even then, YOLOX counts are shown on `/occupancy` but not used for lighting unless `vision.yolox_lighting` is set, because they undercount badly. Details and tests: `AI engineer/docs/handoff/VISION_TEAM_ADAPTER_PROPOSAL.md`. Can you approve, or tell me where you'd prefer the factory to live?
