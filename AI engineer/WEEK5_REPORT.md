# VESTA — Week 5 Report: Student Count + Three-Zone Occupancy

Status: **WEEK 5 IN PROGRESS.** Planning was completed on 2026-10-07; this report records each implementation task.

Terminology used throughout: the detector finds COCO "person" and cannot tell a student from a teacher, visitor or staff member. Week 5 therefore produces **DETECTED-PERSON OCCUPANCY**. The roadmap and any UI may call it "student count", but the software value is an operational proxy based on detected persons inside the classroom ROI. It is a per-frame count, not a unique-person count over time and not a semantic classification.

---

# Week 5 Task 1 — Zone Assignment and Accounting Core

Date: 2026-10-07. Scope: the core zone model, deterministic assignment and accounting only. Configuration (`config/settings.py`), geometry validation, overlays and benchmarks are later tasks and were not touched. `vision/detector.py` and `vision/roi.py` are unchanged (SHA-256 recorded before and after: detector `254b2530...0a13`, ROI `ddf73251...cbd6`).

## Architecture

```
Frame -> detector (runs once) -> Detection list -> classroom ROI -> zone assignment -> occupancy accounting
```

New `vision/zones.py` is pure post-processing and reuses `vision/roi.py` (`Roi`, `anchor`, `in_roi`, inclusive `Roi.contains`); no geometry code is duplicated. No crops, no per-zone inference, no change to the detector.

## Zone and ZoneSet

- `Zone(zone_id, name, roi)`: frozen; `roi` is the existing `Roi`. The canonical IDs are LEFT, CENTER, RIGHT, but the class carries no coordinates and nothing in assignment depends on a particular name. `OUTSIDE` and `UNASSIGNED` are reserved and cannot be zone IDs.
- `ZoneSet(zones)`: frozen, **ordered**; the order is the tie-break priority. It rejects duplicate IDs and non-`Zone` members, is iterable and sized, and exposes `ids`. No requirement for exactly three zones exists at this level (the settings loader will pin that in Task 2).

## Assignment rule (deterministic)

1. If a classroom ROI is given and the detection's bottom-centre anchor lies outside it, the detection is **OUTSIDE** and no zone is consulted. With no ROI the full frame counts as classroom.
2. Otherwise zones are tested in order and the **first** zone whose ROI contains the anchor owns the detection.
3. Inside the classroom ROI but in no zone: **UNASSIGNED**, recorded explicitly, never dropped.

Inclusive boundaries mean a point on a shared border satisfies both neighbours; the ZoneSet order breaks the tie, so ownership comes from the order and not from a hard-coded zone name (tested by reversing the order). Overlapping zones are resolved by the same first-match rule; overlap/geometry validation is deliberately left to Task 2. With no zones (or `None`), ROI classification still runs, every detection inside the classroom becomes UNASSIGNED and outside ones stay OUTSIDE, so Task 2's "zones disabled" needs no second accounting path.

## Result model and invariants

`ZoneOccupancyResult` (frozen) holds `total_detected`, `inside_classroom_roi`, `outside_classroom_roi`, `zone_counts` (zone ID and count, in ZoneSet order), `unassigned_inside_roi`, `zone_detections`, `unassigned_detections`, `outside_detections`, `zones_configured`, plus `occupancy_count` (= detected persons inside the classroom ROI), `count(zone_id)` and `detections_in(zone_id)`. The **original `Detection` objects** are placed in the buckets, never copies.

It validates itself on construction and raises `ZoneAccountingError` otherwise:
- A. `total_detected` equals the number of input detections (independently re-checked in tests);
- B. `inside + outside = total`;
- C. `sum(zone_counts) + unassigned = inside`;
- D. every detection appears in exactly one bucket (checked by count in the result and by object identity in the tests);
- E. `sum(zone_counts) <= inside` (follows from C);
- F. for a complete synthetic partition, `unassigned = 0` and `sum(zone_counts) = inside` (tested);
- plus per-bucket count/length agreement, non-negative integers, unique zone IDs, and no zone buckets when `zones_configured` is False.

## Tests (`tests/test_zones.py`, 38 passed; synthetic detections and fixtures only)

Cases: one detection in LEFT; one in each of three zones; outside the ROI (also with a deliberately leaky zone, to show the ROI is checked first); inside the ROI but in no zone; the shared LEFT/CENTER boundary goes to LEFT, and with the order reversed to CENTER; overlapping zones, first match wins and no double count in either order; an independent audit of invariants A-F against the input list including identity; object identity preserved with `is` (equal-but-distinct twins stay distinct); the same normalised layout at 1000x500 and 2000x1000; no zones and an empty ZoneSet; a complete three-zone partition (zone sum equals ROI count, nothing unassigned); zone counts never exceed the detection count; several detections in one zone with input order kept; a mixture of zone, unassigned and outside; the bottom-centre anchor versus the box centre; no ROI (whole frame); empty input and a repeated instance; determinism and immutability; duplicate, reserved and invalid zone IDs; non-`Roi` zone and non-`Zone` member; and 11 inconsistent hand-built results plus a detection listed in two buckets, all rejected.

The `LEFT / CENTER / RIGHT` rectangles in the tests are **synthetic fixtures, not calibrated classroom geometry**.

## Mutation checks (each applied to a copy of `zones.py`, tested, then restored; file confirmed identical afterwards)

| Mutation | Tests failed |
|---|---|
| A. last match wins instead of first | 3 |
| B. classroom-ROI-first check removed | 4 |
| C. UNASSIGNED silently dropped | 4 |
| D. box centre instead of the bottom-centre anchor | 1 |
| E. detection allowed in several zone buckets (no `break`) | 15 |
| F. one accounting invariant removed, any single one of three | 0 (see below) |
| F, pairs of invariants removed | 1 to 2 each |
| F, all three removed | 4 |

**Finding on F:** removing any *single* one of the three accounting checks (`inside+outside=total`, `zones+unassigned=inside`, `buckets=total`) changed nothing, because together with the per-bucket count checks they are mathematically dependent: each follows from the other two, so one is defence in depth and no test can tell it from its absence. Removing a pair initially still passed for one pair, which exposed a test gap (no invalid case with consistent buckets and total but a wrong `inside`); that case was added, after which every pair and the triple are caught.

## Calibration and limitations

- **Real zone calibration: PENDING HARDWARE / NOT CALIBRATED.** No classroom geometry exists; the physical camera is not mounted. LEFT/CENTER/RIGHT are image-space regions as seen by the camera, and their mapping to room orientation and lighting circuits belongs to the hardware team.
- **Detector undercount inherited from Week 4.** Zone assignment cannot recover people the detector never finds. On the frozen external benchmark the baseline counted about 16.5 % of labelled people (3,236 of 19,583; maximum prediction 15 against labels up to 65), so zone counts will typically understate dense zones; false positives are also possible, so zone counts are detected-person counts, not guaranteed lower bounds. No correction multiplier is applied and nothing here was tuned on that benchmark.
- The bottom-centre anchor is least reliable under occlusion and oblique views; a zone drawn on the image plane is not a floor zone.
- No tracking, no unique-person counting and no instructor exclusion.

## Verification

Zone tests 38 passed; with the ROI, vision, evaluation and count tests 148 passed. Full suite **367 passed, 5 skipped, 0 failed**. Baseline before the task was 329 passed, 5 skipped; the difference is exactly the 38 new tests. The 5 skips are 4 ReSpeaker hardware-gated tests and the openWakeWord recorded-clip test. The Windows Application Control block on the openWakeWord import was lifted during this session (it came and went earlier too), which is why only one openWakeWord test skips; pass/skip totals therefore depend on that environment state, not on this code.

## Status

Task 1: **COMPLETE (SOFTWARE-VALIDATED, synthetic fixtures).** Week 5: IN PROGRESS. Next: Task 2, zone configuration and validation.


---

# Week 5 Task 2 — Zone Configuration and Validation

Date: 2026-10-07. Scope: configuration keys, a settings-to-ZoneSet loader, the canonical LEFT/CENTER/RIGHT contract and geometry sanity checks. `vision/detector.py` and `vision/roi.py` are unchanged (SHA-256 identical before and after). Task 1's assignment and accounting code is byte-for-byte unchanged: the pre-task `vision/zones.py` was reconstructed (one import line reverted, the new section removed) and its hash matched the one recorded before the task. Overlays, benchmarks and the Kaggle set were not touched.

## Configuration keys and schema

`config/settings.py` gains `ZONES_ENABLED = False` and `ZONES = ()`. **Zones are disabled by default and no geometry is shipped**: no LEFT/CENTER/RIGHT coordinates exist in settings (the camera is not mounted or calibrated). The file's comment describes the schema without numbers. When enabled, `ZONES` is a tuple of entries `(zone_id, display_name, kind, points)` in normalised 0..1 image coordinates (origin top-left):
- `kind = "rectangle"`: `points = ((x1, y1), (x2, y2))`;
- `kind = "polygon"`: `points = ((x, y), ...)` with 3 or more vertices.

Geometry is built only with the existing `Roi.rectangle(...)` / `Roi.polygon(...)`; their validation (0 to 1 range, finite values, x1 < x2, y1 < y2, minimum vertices, non-zero area) stays authoritative and its errors are re-raised as `ZoneConfigError` with the zone named.

## Canonical contract

When enabled, exactly the IDs **LEFT, CENTER, RIGHT** are required: missing, extra, duplicate or reserved (`OUTSIDE`, `UNASSIGNED`) IDs are rejected. The loader **constructs** the canonical priority LEFT, CENTER, RIGHT whatever the order in `settings.py`, so a shared LEFT/CENTER border always belongs to LEFT and a shared CENTER/RIGHT border to CENTER; an accidental settings order cannot change the contract. Core `Zone` / `ZoneSet` stay generic; only the loader enforces this.

## Loader

`build_zone_set(definitions, classroom_roi=None)` validates and returns the `ZoneSet`; `zones_from_settings(classroom_roi=None)` follows the style of `roi_from_settings()`: it returns `None` when disabled (and builds no fake zones), otherwise calls `build_zone_set(settings.ZONES, classroom_roi)`. Callers pass `roi_from_settings()` as the classroom ROI. With `None`, Task 1 assignment reports every detection inside the classroom as UNASSIGNED, with no second counting path (tested).

## Validation

**Classroom containment.** Every zone vertex (rectangle corner or polygon vertex) must lie inside or on the classroom ROI boundary; with no classroom ROI the domain is the full frame, which `Roi` already guarantees. **Limitation:** for a concave classroom ROI, vertices inside do not prove that the edges between them stay inside; this is not claimed (a test documents an L-shaped case that is accepted). This is a configuration sanity check; assignment correctness does not depend on it because the classroom ROI is checked first.

**Overlap.** Shared borders and shared corners are allowed. Rejected: identical vertex sets (exact duplicate geometry); a zone vertex strictly inside another zone; proper edge crossings; a sample point (the vertex average) strictly inside both zones, which proves area overlap and catches nested zones whose vertices all touch the border; and two rectangles whose interiors overlap (exact interval test, which catches the common mistake of horizontal strips overlapping by a band). `Roi.contains` is inclusive, so a small boundary test was added in `zones.py` to separate "strictly inside" from "on the border".

**Not guaranteed to be detected:** overlap involving a polygon that shares collinear edges with another zone without any of the situations above (for example two 4-point polygons overlapping in an aligned band). A test records this limitation and shows that Task 1's first-match assignment still gives the detection exactly one bucket. The validation is not a computational-geometry proof.

## Errors

`ZoneConfigError` (a `ValueError`) with specific messages: enabled with no definitions; non-sequence `ZONES`; malformed definition (wrong shape, non-string ID, empty name); unsupported kind; malformed rectangle or polygon point data; invalid Roi geometry (naming the zone); duplicate, unexpected (reserved names flagged) or missing IDs; vertex outside the classroom ROI; and "zones X and Y overlap: <reason>". Malformed input never surfaces as a tuple-unpacking or index error.

## Tests (`tests/test_zone_config.py`, 51 tests; geometry is synthetic fixtures only)

All 26 requested cases plus extras: disabled returns no zones (also with definitions present); valid rectangles and polygons load through the settings path; canonical order from four shuffled settings orders; each of LEFT, CENTER and RIGHT missing; extra, duplicate and reserved IDs; unsupported kind; malformed rectangles and polygons; coordinates outside 0..1, inverted and NaN via `Roi`; vertex outside the classroom ROI; no-ROI loading; exact duplicate; vertex strictly inside; proper crossing (rectangle and polygon forms); shared edge and shared vertex accepted; aligned-band rectangle overlap; nested zones; documented limitations (concave classroom, polygon band); disabled and valid zones integrated with Task 1 assignment; Detection objects unchanged; and the full chain settings, loader, ZoneSet, ordered assignment for every settings order with anchors exactly on the LEFT/CENTER and CENTER/RIGHT borders (LEFT and CENTER receive them), plus a polygon configuration.

## Mutation checks (each on a copy of `zones.py`, then restored; file confirmed identical afterwards)

| Mutation | Tests failed |
|---|---|
| A. allow a missing canonical ID | 3 |
| B. stop rejecting an unknown ID | 3 |
| C. reverse the canonical order | 9 |
| D. disable the vertex-outside-classroom check | 1 |
| E. disable proper-crossing rejection | 1 |
| F. rectangle test rejects touching borders | 12 |
| F2. "strictly inside" made inclusive (rejects shared borders) | 17 |
| G. return zones even when `ZONES_ENABLED = False` | 3 |
| H. exact rectangle-interior overlap test removed | 1 |
| I. interior sample-point check removed | 1 |
| J. settings order used instead of canonical order | 4 |
| K. exact-duplicate-geometry check removed | 1 |

## Status

Real zone geometry: **PENDING HARDWARE / NOT CALIBRATED**; the thirds in the tests are synthetic. The Week 4 detector undercount is inherited. Verification: Task 2 and zone tests 89 passed; with ROI, vision, evaluation and count tests 199 passed. Full suite **418 passed, 5 skipped, 0 failed**. Baseline before the task was 367 passed, 5 skipped; the difference is exactly the 51 new tests. The 5 skips are 4 ReSpeaker hardware-gated tests and the openWakeWord recorded-clip test; the openWakeWord Application Control block stayed lifted throughout this task.

Task 2: **COMPLETE (SOFTWARE-VALIDATED, synthetic fixtures).** Week 5: IN PROGRESS. Next: Task 3, visualization, synthetic end-to-end validation and zone-stage performance measurement.


---

# Week 5 Task 3 — Visualization, Synthetic End-to-End Validation and Zone-Stage Performance

Date: 2026-10-07. Scope: an optional downstream zone overlay, explicitly synthetic scenario fixtures, seating and occlusion-geometry tests, and zone-stage micro-timing. `vision/detector.py`, `vision/roi.py` and `vision/zones.py` are unchanged (SHA-256 identical before and after). Task 1/2 semantics are untouched. The frozen Kaggle benchmark was not run or used. All geometry is **SYNTHETIC TEST GEOMETRY — NOT CALIBRATED**; no real people or images are involved.

## Visualization (`vision/annotate.py`)

`draw_zone_occupancy(image, result, zones=None, classroom_roi=None, show_summary=True)` and `save_zone_annotated(...)` **consume the authoritative `ZoneOccupancyResult`**; they never recompute membership, never change a detection, result, ROI or count, and return a copy of the image. The Week 4 `draw_detections` is unchanged (output compared with an independent re-implementation of its rules). Not colour alone: every box carries a text label (zone ID, `UNASSIGNED` or `OUTSIDE`), zone outlines are labelled `ID (count)`, OUTSIDE boxes are crossed out, UNASSIGNED boxes are drawn thin, and a header lists per-bucket counts and the identity `TOTAL N = LEFT + CENTER + RIGHT + UNASSIGNED + OUTSIDE`. `box_labels(result)` and `summary_lines(result)` expose the label plan so it is testable without OCR. LEFT/CENTER/RIGHT are image-space IDs only (no window/door/teacher meaning).

## Synthetic fixtures (`tests/zone_synthetic.py`, outside production settings)

Synthetic 1000x500 frame; classroom = lower 80 %. Three layouts: complete partition, a gapped layout (UNASSIGNED strips), a slanted-border polygon layout. Six arrangements: sparse, left-heavy, centre-heavy, right-heavy, mixed/dense (32 detections: LEFT 8, CENTER 7, RIGHT 9, UNASSIGNED 5, OUTSIDE 3) and empty. `config/settings.py` still has `ZONES_ENABLED = False`, `ZONES = ()` (tested).

## Scenario validation

Every arrangement lands each detection in its intended bucket, with exact accounting: N = LEFT+CENTER+RIGHT+UNASSIGNED+OUTSIDE; inside = zones + UNASSIGNED; total = inside + outside; each detection in exactly one bucket (identity-checked). Counts are order-independent and resolution-independent (normalised geometry; 2x frame tested). On the full partition nothing is UNASSIGNED and shared borders go to LEFT / CENTER by priority.

## Occlusion — assignment behaviour only

These tests describe how **assignment behaves for altered box geometry**; they make **no claim about detector robustness to occlusion**. Removing the top of a box keeps the bottom-centre anchor and the zone. Severe truncation that moves the bottom edge up changes the anchor and **can change the zone** (slanted layout, x = 0.45: bottom at y = 0.60 gives CENTER, at y = 0.25 gives LEFT) or push it outside the classroom. This is a documented limitation of the bottom-centre anchor; it was not tuned.

## Lighting — not claimed

Assignment takes no pixels (signature tested), so it is independent of image lighting by construction. **Detector behaviour under lighting is NOT EVALUATED.** Real VESTA lighting conditions: PENDING real camera data.

## Zone-stage timing (Windows development PC only; descriptive, no thresholds)

Warm-up 200 runs (assignment) / 5 runs (drawing). Assignment+accounting 2,000 runs per size; overlay drawing on a 1280x720 canvas 50 runs per size. Drawing is measured separately from assignment.

| Detections | Assignment mean / median / p95 (µs) | Overlay drawing mean / median / p95 (ms) |
|---|---|---|
| 0 | 8.4 / 6.9 / 10.4 | 9.8 / 9.4 / 12.7 |
| 10 | 21.5 / 17.2 / 30.3 | 13.7 / 13.6 / 16.7 |
| 25 | 40.8 / 36.3 / 58.6 | 22.6 / 22.0 / 27.9 |
| 50 | 83.2 / 67.8 / 101.3 | 34.2 / 32.4 / 43.0 |
| 100 | 142.9 / 125.7 / 209.0 | 56.8 / 56.0 / 68.9 |

Zone assignment is microseconds, negligible next to detector inference; drawing is a debug aid and is not on the control path. These are **not Raspberry Pi 5 numbers** and no FPS is derived. No timing assertions exist in the tests (only the output shape is checked). Raw numbers: `vision_assets/results/week5_synthetic/zone_stage_timing.json`; demo overlay `zone_demo.png` (synthetic).

## Tests (`tests/test_zone_scenarios.py`, 36)

Backward compatibility of `draw_detections`; overlay with no zones/ROI; zone, polygon and ROI outlines; labels follow the result (including a hand-built result that disagrees with geometry); summary text; UNASSIGNED/OUTSIDE visually distinct and OUTSIDE crossed out; no mutation of image, detections or result; signature takes a result; six arrangements with exact accounting; order and resolution independence; partition behaviour; occlusion geometry (top cut keeps zone, bottom cut can change it, outside, anchor not box centre); lighting-independence by signature; production settings clean; PNG saving; timing fixture determinism and benchmark output shape.

## Mutation checks (temporary, each restored and hash-verified)

| Mutation | Tests failed |
|---|---|
| A. overlay labels the wrong zone text | 2 |
| A2. header total uses the inside count | 1 |
| B. overlay mutates the source image | 1 |
| C. UNASSIGNED boxes omitted | 2 |
| D. OUTSIDE shown as a regular zone | 2 |
| E. anchor changed to box centre (`roi.py`, restored) | 4 |
| F. scenario fixture drops a detection | 9 |

One harness crash during the first attempt left mutation A in place; it was reverted and the clean hash re-verified before the final run, which used try/finally.

## Status

Full suite: **454 passed, 5 skipped, 0 failed** (418 + 36 new). Skips unchanged (4 ReSpeaker hardware-gated, 1 openWakeWord recorded clip); the openWakeWord block stayed lifted. Task 3: **COMPLETE (SOFTWARE-VALIDATED, synthetic fixtures and synthetic Detection lists only).** Real zone geometry/calibration, zone accuracy on real frames and Pi timing remain PENDING HARDWARE / NOT CALIBRATED. The Week 4 undercount in dense scenes is inherited. Next (not started): Task 4, software evaluation and formal closeout.


---

# Week 5 Software Closeout

Date: 2026-10-07. Audit, verification and documentation only: no code, geometry, detector, threshold or configuration was changed, the frozen Kaggle benchmark was not run, and Week 6 was not started. Findings below come from the code and from commands run during this audit, not from earlier report prose.

**Software module decision: the Week 5 occupancy-intelligence software module is COMPLETE. Real-world validation of that module is NOT complete** (see "Remaining validation").

## Authoritative roadmap (CLAUDE.md, Week 5, reread)

Goal: convert camera detections into useful classroom intelligence. Tasks: define LEFT / CENTER / RIGHT zones; student counting; zone assignment; occupancy output schema; test different seating arrangements; test partial occlusion; test different lighting. Metrics: counting error, zone assignment accuracy, FPS, latency. Deliverable: occupancy intelligence module. Nothing here was interpreted more broadly.

## Requirement-to-evidence matrix

| Requirement | Evidence (verified in code / by command) | Classification | Status |
|---|---|---|---|
| Define LEFT / CENTER / RIGHT zones | `CANONICAL_ZONE_IDS` and `build_zone_set` in `vision/zones.py`; `ZONES_ENABLED = False`, `ZONES = ()` in `config/settings.py` (no geometry shipped); 51 config tests | SOFTWARE-VALIDATED (mechanism); real geometry PENDING HARDWARE / NOT CALIBRATED | Mechanism complete; calibration pending |
| Student counting | `ZoneOccupancyResult.occupancy_count` = detected persons whose anchor is in the classroom ROI; detector counts COCO "person" | SOFTWARE-VALIDATED as detected-person occupancy; real counting quality LIMITED (Week 4 frozen baseline) | Complete as defined; no semantic student claim |
| Zone assignment | `assign_detections`: ROI first, ordered first match, UNASSIGNED, OUTSIDE; 38 core tests | SOFTWARE-VALIDATED | Complete |
| Occupancy output schema | frozen, self-validating `ZoneOccupancyResult` (total, inside, outside, per-zone counts and detections, unassigned, `zones_configured`) | SOFTWARE-VALIDATED | Complete |
| Different seating arrangements | six synthetic arrangements, 60 constructed assignments, exact accounting | SYNTHETICALLY VALIDATED (zone accounting only) | Real seating PENDING REAL DATA |
| Partial occlusion | altered/truncated box geometry tests on the bottom-centre anchor | SOFTWARE-VALIDATED (assignment geometry under synthetic occlusion-like box changes); YOLOX occlusion robustness NOT EVALUATED | Real occlusion PENDING REAL DATA |
| Different lighting | assignment takes no pixels (signature tested) | Zone accounting after detections: LIGHTING-INDEPENDENT BY DESIGN; detector under lighting NOT EVALUATED in Week 5 | Real lighting PENDING REAL / VARIED CAMERA DATA |
| Counting error | synthetic software accounting error 0 on constructed fixtures; Week 4 frozen external baseline MAE 29.88 | SOFTWARE-VALIDATED (synthetic); real quality poor | Real-camera counting error PENDING REAL DATA |
| Zone assignment accuracy | 60 of 60 constructed assignments matched predefined buckets | SYNTHETIC FIXTURE ASSIGNMENT CORRECTNESS | Real accuracy PENDING REAL DATA (camera + calibrated zones + human-labelled ground truth) |
| Latency | zone-stage micro-timing (0 to 100 detections) | MEASURED ON WINDOWS DEVELOPMENT PC | Pi latency PENDING HARDWARE |
| FPS | no live pipeline; zone stage is microseconds | Zone-stage latency measured; detector pipeline throughput measured in Week 4 on Windows; live-camera FPS NOT MEASURED; Raspberry Pi FPS NOT MEASURED | PENDING HARDWARE |
| Occupancy intelligence module (deliverable) | `vision/roi.py` + `vision/zones.py` (+ optional `vision/annotate.py`) behind the Week 4 detector | SOFTWARE-VALIDATED | Software module COMPLETE |

No item is classified HARDWARE-VALIDATED: no physical camera, Raspberry Pi 5 or room was used in Week 5.

## Final software architecture

```
Frame -> YOLOX-Tiny detector (416, score 0.30, NMS 0.45) -> Detection list
      -> classroom ROI (bottom-centre anchor, inclusive boundary)
      -> zone assignment (ordered first match)
      -> ZoneOccupancyResult
      -> optional overlay (draw_zone_occupancy, consumes the result)
```

Verified in code: the anchor and ROI come from `vision/roi.py` (not duplicated); `assign_detections` checks the classroom ROI first (OUTSIDE), then the first containing zone, else UNASSIGNED; the result validates its own identities and raises `ZoneAccountingError` otherwise; the loader (`build_zone_set`) requires exactly LEFT, CENTER, RIGHT and imposes that priority whatever the settings order; `zones_from_settings()` returns `None` when disabled (no fake zones); the overlay never recomputes membership. The result exposes total detected, classroom occupancy, LEFT / CENTER / RIGHT counts, UNASSIGNED, OUTSIDE and `zones_configured`; no field was added for this closeout.

## Occupancy semantics

"Student count" is operationally implemented as **detected-person occupancy within the configured classroom ROI**, per frame. The detector recognises COCO "person" and cannot tell students from instructors, visitors or staff. There is no unique-person counting over time, and no semantic classification. False detections are possible, so the detected count is not a strict lower bound of the true number of people.

## Synthetic seating results

Gapped synthetic layout (SYNTHETIC TEST GEOMETRY, NOT CALIBRATED), (LEFT, CENTER, RIGHT, UNASSIGNED, OUTSIDE): sparse (1,1,1,0,0); left-heavy (7,1,0,1,0); centre-heavy (1,7,1,0,0); right-heavy (0,1,6,0,0); mixed/dense (8,7,9,5,3); empty (0,0,0,0,0). Every detection landed in its intended bucket; N = zones + UNASSIGNED + OUTSIDE held exactly. This validates zone accounting only, not detector accuracy under real seating.

## Synthetic occlusion-like geometry result

Removing the top of a box keeps the bottom-centre anchor and the zone; moving the bottom edge up (severe clipping) moves the anchor and can change the zone (slanted layout, x = 0.45: bottom y = 0.60 gives CENTER, y = 0.25 gives LEFT) or take it outside the ROI. This supports zone-assignment geometry under synthetic box changes only. It does not support YOLOX occlusion robustness or accuracy on occluded students, both NOT EVALUATED.

## Lighting limitation

Post-processing does not inspect pixels, so its output is independent of lighting once Detection objects exist. The detector may behave differently under different lighting; that was not evaluated in Week 5, and no brightness experiment on a fixed set of detections was run (it would prove nothing). Real VESTA lighting validation: PENDING REAL / VARIED CAMERA DATA. The roadmap item "test different lighting" therefore remains open at the detector level.

## Synthetic count correctness and assignment correctness

Synthetic counting error: 0 for the explicitly constructed fixtures. This is **software accounting error only**, not real-world person-count error. Synthetic fixture assignment correctness: 60 of 60 constructed assignments (the six arrangements above, 3 + 9 + 9 + 7 + 32 + 0) matched their predefined buckets (verified by an independent read-only script during this audit). This is not real-world zone accuracy. Real accuracy needs a real camera, calibrated zones and human-labelled ground truth.

## Windows zone-stage timing (Task 3 results, unchanged; development PC only)

| Detections | Assignment mean / median / p95 (µs) | Overlay drawing mean / median / p95 (ms) |
|---|---|---|
| 0 | 8.4 / 6.9 / 10.4 | 9.8 / 9.4 / 12.7 |
| 10 | 21.5 / 17.2 / 30.3 | 13.7 / 13.6 / 16.7 |
| 25 | 40.8 / 36.3 / 58.6 | 22.6 / 22.0 / 27.9 |
| 50 | 83.2 / 67.8 / 101.3 | 34.2 / 32.4 / 43.0 |
| 100 | 142.9 / 125.7 / 209.0 | 56.8 / 56.0 / 68.9 |

Qualitatively, zone assignment (tens to a few hundred microseconds) is far cheaper than YOLOX inference on the same PC (Week 4: about 55 to 63 ms median per warm inference). The timings are different measurements and were not summed or converted into a pipeline figure; nothing is inferred for the Raspberry Pi.

## FPS status

Zone-stage latency: MEASURED (Windows). Windows detector/pipeline latency: measured in Week 4 (about 15 frames/s derived from a 66.5 ms mean pipeline, software throughput only). Live camera FPS: NOT MEASURED. Raspberry Pi FPS: NOT MEASURED. No FPS was derived from the zone-stage microseconds. The FPS metric is PENDING HARDWARE.

## Known upstream detector limitation (inherited, not rerun)

Week 4 frozen external benchmark: 547 images, 12 inferred camera groups; labelled total 19,583, predicted 3,236; MAE 29.88; bias -29.88; maximum prediction 15 versus maximum label 65 (re-read from the sealed per-image CSV, no inference). Week 5 zone counts inherit these misses: a correct zone assignment cannot place a person the detector never found, and false detections can still inflate a zone.

## Real-camera dependencies carried forward

PENDING HARDWARE: mounted-camera LEFT/CENTER/RIGHT geometry and physical zone calibration; live camera FPS; Raspberry Pi 5 latency/FPS; end-to-end physical occupancy behaviour. PENDING REAL DATA: mapping of image LEFT/RIGHT to room orientation; zone ground truth on real frames; real seating-layout, occlusion and lighting validation.

## Deferred scope (confirmed absent from Week 5 code)

Tracking, unique-person counting, student/teacher classification, instructor exclusion, homography, floor-plane calibration, sensor fusion, lighting control, model tuning, detector replacement and threshold tuning were not implemented. A search of `vision/` and `config/` found only comments disclaiming tracking/teacher semantics. `detector.py` still uses score 0.30, NMS 0.45, 416 input.

## Integrity

Unchanged since the Week 4 closeout / Task 1 records (SHA-256 re-verified now): `vision/detector.py` `254b2530...0a13`; `vision/roi.py` `ddf73251...cbd6`; `vision/zones.py` `3eaa182a...538f` (unchanged since Task 2). `vision/annotate.py` was extended in Task 3 (`685b86b3...`); the Week 4 `draw_detections` behaviour is test-protected. Frozen benchmark: the five result files carry the 2026-10-06 16:02:31 modification time and the hashes recorded in the Week 4 report (`per_image_results.csv` `2de6c4f3...994f`, `evaluation_summary.json` `e0bcbd7f...30d0`, `camera_group_summary.csv` `ff3bdce7...083a`, `directory_summary.csv` `ff4058f7...9d2`, `occupancy_band_summary.csv` `e465c7dc...8d3a`); manifest `3189a8bb...c553` and model `427cc366...b0f7` match. All verified by hashing and `stat` only; no inference. Designation kept: **FROZEN EXTERNAL BASELINE BENCHMARK**.

## Tests

Week 5 targeted (zones, zone config, zone scenarios, ROI): 153 passed. Full suite: **454 passed, 5 skipped, 0 failed**, identical to the reference. Skips: 4 ReSpeaker hardware-gated, 1 openWakeWord recorded-clip test; the openWakeWord Application Control block stayed lifted.

## Remaining validation (roadmap items needing real data or hardware)

Real zone calibration and real zone-assignment accuracy; real seating, occlusion and lighting validation (including detector behaviour); real counting error on VESTA frames; live-camera and Raspberry Pi FPS/latency; end-to-end physical occupancy behaviour. Software completeness must not be read as real classroom performance.

## Final Week 5 status

WEEK 5 OCCUPANCY INTELLIGENCE SOFTWARE MODULE: COMPLETE. SOFTWARE ZONE ASSIGNMENT: VALIDATED (synthetic fixtures). REAL ZONE CALIBRATION / ACCURACY: PENDING HARDWARE / REAL DATA. REAL LIGHTING / OCCLUSION / SEATING VALIDATION: PENDING REAL DATA. LIVE / PI FPS: PENDING HARDWARE. KNOWN UPSTREAM LIMITATION: SEVERE DENSE-CLASSROOM UNDERCOUNTING. Week 6: NOT STARTED.
