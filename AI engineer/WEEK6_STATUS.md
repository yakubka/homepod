# VESTA — Week 6 Status (2026-10-07, closeout + review reconciliation)

Full detail: [WEEK6_REPORT.md](WEEK6_REPORT.md) (section "Week 6 Software Closeout").

| Item | Status |
|---|---|
| WEEK 6 UNIFIED CLASSROOM-STATE SOFTWARE MODULE | **COMPLETE** |
| CONTRACTS | SOFTWARE-VALIDATED |
| OCCUPANCY FUSION (R1-R8) | SOFTWARE-VALIDATED ON SIMULATED INPUTS |
| ENVIRONMENT REPRESENTATION | SOFTWARE-VALIDATED ON SIMULATED INPUTS |
| TELEMETRY / RECOVERY / SCENARIOS | SOFTWARE-VALIDATED / SYNTHETICALLY VALIDATED (38 / 38 predefined scenarios; software correctness, not real fusion accuracy) |
| FUSION-STAGE LATENCY | MEASURED ON WINDOWS DEVELOPMENT PC (about 21-33 us mean; not Pi, not FPS) |
| REAL SENSOR INPUTS (LD2410B, BH1750, temperature, humidity) | PENDING HARDWARE (humidity device UNKNOWN) |
| PI / ESP32 ADAPTER | NOT IMPLEMENTED — PROTOCOL NOT AVAILABLE |
| FRESHNESS / SKEW CALIBRATION, TIMESTAMP TRANSPORT, PHYSICAL FAILURE/RECOVERY | PENDING HARDWARE |
| REAL FUSION VALIDATION (END TO END) | PENDING HARDWARE |
| INDEPENDENT ANTIGRAVITY REVIEW | **COMPLETED** after the original closeout (reproduced 681 passed / 5 skipped, 38 / 38 scenarios; 7 findings); reconciliation applied (see below); post-fix read-only re-review PENDING |
| Week 7 | NOT STARTED |

Tasks: 1 contracts COMPLETE; 2 occupancy fusion COMPLETE; 3 synthetic end-to-end, recovery, telemetry, timing COMPLETE; 4 closeout COMPLETE. Nothing is HARDWARE-VALIDATED.

Semantics to remember: the count is the Week 5 detected-person count and radar never changes it; vision-only zero is UNKNOWN presence; R3/R4 are CONFLICT with UNKNOWN presence; `ClassroomState.degraded` means occupancy-evidence degraded (environment health is in `environment.issues` and per-source state); freshness uses the Pi monotonic receive time only; equal timestamps do not invoke R8 when skew is configured, and an unconfigured-skew tie falls back to vision by precedence (not because it is newer); no hysteresis or hidden state. Fusion does not repair the known vision undercount (frozen baseline: 19,583 labelled, 3,236 predicted, MAE 29.88).

Integration note: callers must sample `now` after collecting all readings (a reading received after `now` raises). Open for calibration: recency-based exclusion under unconfigured skew; expected temperature sensor ids.

Review reconciliation: F1 future-timestamp rule kept as a documented integration precondition (snapshot the readings, THEN sample now); F2 uncalibrated skew kept as a KNOWN UNCALIBRATED-INTEGRATION LIMITATION (fused count may alternate between a number and None; None is never zero; calibrate skew before live use); F3 FIXED (`ClassroomState.fallback_basis` owned by fusion, telemetry only serialises); F4 DEFERRED (expected temperature sensor ids: PENDING REAL HARDWARE CONTRACT); F5 FIXED (`temperatures` validation raises FusionContractError); F6 FIXED (canonical ids `[A-Za-z0-9_-]+`); F7 FIXED (this documentation).

Tests: 718 passed, 5 skipped, 0 failed (681 + 37 post-review tests). Detector, ROI, zones and annotate unchanged; frozen Kaggle outputs untouched.
