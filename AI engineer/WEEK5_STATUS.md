# VESTA — Week 5 Status (2026-10-07, closeout)

Full detail: [WEEK5_REPORT.md](WEEK5_REPORT.md) (section "Week 5 Software Closeout").

| Item | Status |
|---|---|
| WEEK 5 OCCUPANCY INTELLIGENCE SOFTWARE MODULE | **COMPLETE** |
| SOFTWARE ZONE ASSIGNMENT | **VALIDATED** (synthetic fixtures; 60 of 60 constructed assignments matched; accounting exact) |
| REAL ZONE CALIBRATION / REAL ZONE ACCURACY | PENDING HARDWARE / REAL DATA (no geometry shipped; `ZONES_ENABLED = False`) |
| REAL LIGHTING / OCCLUSION / SEATING VALIDATION | PENDING REAL DATA |
| LIVE CAMERA / RASPBERRY PI FPS AND LATENCY | PENDING HARDWARE |
| KNOWN UPSTREAM DETECTOR LIMITATION | SEVERE DENSE-CLASSROOM UNDERCOUNTING (frozen baseline: 547 images, labelled 19,583, predicted 3,236, MAE 29.88, bias -29.88) |
| Week 6 | NOT STARTED |

Tasks: 1 zone core COMPLETE; 2 zone configuration/validation COMPLETE; 3 overlay, synthetic end-to-end, zone-stage timing COMPLETE; 4 closeout COMPLETE. Nothing is HARDWARE-VALIDATED.

Semantics: "student count" = **detected-person occupancy within the configured classroom ROI** (COCO "person", per frame). No student/teacher distinction, no unique-person counting over time. Detected count is not a strict lower bound (false detections possible).

Requirement classification: zones, assignment, output schema, module = SOFTWARE-VALIDATED. Seating = SYNTHETICALLY VALIDATED (accounting only). Partial occlusion = zone-assignment geometry under synthetic box changes SOFTWARE-VALIDATED; YOLOX occlusion robustness NOT EVALUATED. Lighting = zone accounting LIGHTING-INDEPENDENT BY DESIGN; detector under lighting NOT EVALUATED. Counting error = 0 synthetic software accounting error (not real-world); real quality poor. Zone accuracy = synthetic fixture correctness only. Zone-stage latency = MEASURED ON WINDOWS DEVELOPMENT PC (100 detections: 0.14 ms mean). FPS = zone stage measured, live and Pi NOT MEASURED.

Roadmap items still open (need real data or hardware): real zone calibration and accuracy; real seating, occlusion and lighting validation; real counting error on VESTA frames; live and Pi FPS; end-to-end physical behaviour.

Deferred and not implemented: tracking, unique-person counting, student/teacher classification, homography, floor-plane calibration, sensor fusion, lighting control, detector or threshold tuning.

Tests: 454 passed, 5 skipped, 0 failed (4 ReSpeaker hardware-gated + 1 openWakeWord recorded clip). `vision/detector.py` and `vision/roi.py` unchanged; frozen benchmark files untouched.
