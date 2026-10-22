# VESTA — Week 4 Status (closeout, 2026-10-06)

Authoritative summary. Full evidence and the historical task-by-task record: [WEEK4_REPORT.md](WEEK4_REPORT.md) (see its final section, "Week 4 Software Closeout"). This file replaces the earlier running log; earlier "IN PROGRESS", "PROVISIONAL" and "pending review" wording is superseded by what is below.

**WEEK 4 SOFTWARE FOUNDATION: COMPLETE**
**PHYSICAL CAMERA / RASPBERRY PI VALIDATION: PENDING HARDWARE**
**KNOWN MODEL PERFORMANCE LIMITATION: SEVERE DENSE-CLASSROOM UNDERCOUNTING**

Week 5 (student count + three-zone occupancy): **NOT STARTED.**

| Roadmap item | Status |
|---|---|
| Camera frame acquisition: offline frame source (`ImageFileSource`, `ArrayFrameSource`, EXIF handling) | SOFTWARE-VALIDATED |
| Camera frame acquisition: real camera | PENDING HARDWARE (NOT AVAILABLE; camera model/interface not specified) |
| Person detection (YOLOX-Tiny ONNX, 416, raw BGR 0..255, score 0.30, NMS 0.45) | SOFTWARE-VALIDATED |
| Bounding boxes (`Detection`, overlay) | SOFTWARE-VALIDATED |
| Classroom ROI mechanism (normalised rectangle/polygon, bottom-centre anchor, boundary inclusive, default none) | SOFTWARE-VALIDATED |
| Real classroom ROI calibration | PENDING HARDWARE (NOT CALIBRATED) |
| Basic occupancy count mechanism (`DetectionResult.count`, `count_in_roi`) | SOFTWARE-VALIDATED |
| Counting quality in dense classrooms | LIMITED / POOR (frozen benchmark below) |
| Performance, Windows CPU | MEASURED (median about 65 ms per 720p/1080p image; 24 MP photos about 155 ms) |
| Performance, Raspberry Pi 5 / live camera / camera-to-detector | NOT MEASURED (PENDING HARDWARE) |
| Initial evaluation | SOFTWARE-VALIDATED (two separate evaluations below) |
| Anything HARDWARE-VALIDATED | none |

**Evaluations (not combined):**
- *Three-image development/pilot* (human-reviewed boxes, IoU 0.50; development data, not independent): GT 20, ignore regions 19, TP 11, FP 6, FN 9, precision 0.647, recall 0.550, evaluable-count MAE 1.00 per image. Human review is complete (the earlier raw-count MAE 2.33 is superseded).
- *Frozen external count benchmark* (Kaggle Classroom-Data; 547 decodable of 562 images; counts only, no boxes; 12 inferred cameras with correlated frames; count rules partly undocumented; **FROZEN EXTERNAL BASELINE BENCHMARK**, not to be used for tuning): GT 19,583, predicted 3,236, MAE 29.88, RMSE 36.04, bias -29.88, camera-macro MAE 29.82; 441 undercounted frames, 0 overcounted; all 101 label-zero frames predicted zero. Seal hashes in the report.

**Known limitations:** undercounting grows with occupancy (maximum prediction 15 vs labels up to 65); small people at a 416 input, occlusion and crowding are plausible but untested contributors; labelling rules partly undocumented; no live camera, no Pi benchmark, no real ROI calibration.

**Carry-forward (pending hardware):** real camera backend, actual VESTA camera configuration, physical ROI calibration, Pi 5 latency/throughput, camera-to-detector end-to-end timing. Detector improvement would be a new pre-registered experiment with its own split.

**Tests (final closeout run):** 326 passed, 8 skipped, 0 failed (4 ReSpeaker hardware-gated; 4 openWakeWord affected by Windows Application Control, which comes and goes on this PC). No code changed in the closeout.
