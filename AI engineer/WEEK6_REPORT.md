# VESTA — Week 6 Report: Sensor + Occupancy Fusion

Authoritative roadmap (CLAUDE.md): goal "combine classroom perception signals"; inputs camera, radar, lux, temperature, humidity; tasks sensor data interface, sensor validation, occupancy fusion, environment state representation, missing-sensor handling; deliverable "unified classroom-state representation". No fusion algorithm, confidence model or actuator output is specified.

---

# Week 6 Task 1 — Fusion Contracts + Freshness Semantics

Date: 2026-10-07. Scope: AI-side, protocol-independent input contracts and source-state semantics. **No fusion decision (R1-R8), evidence state, ClassroomState, logging side effect, ESP32 protocol, adapter or driver was implemented.** `vision/detector.py`, `vision/roi.py`, `vision/zones.py` and `vision/annotate.py` are unchanged (SHA-256 identical before and after); the frozen Kaggle benchmark was not run or touched.

## Baseline before the task

Full suite (actual run): **454 passed, 5 skipped, 0 failed**. The 5 skips are 4 ReSpeaker hardware-gated tests and the openWakeWord recorded-clip test; the openWakeWord block stayed lifted. Hashes: detector `254b2530...0a13`, roi `ddf73251...cbd6`, zones `3eaa182a...538f`.

## AI-side boundary and hardware handshake

The ESP32-S3 reads the raw sensors; the Raspberry Pi receives structured readings and fuses them. The real ESP32 wire protocol is NOT AVAILABLE (hardware-team owned), so these contracts do not describe it. The intended chain is:

```
hardware / software team: real protocol
    -> Pi hardware adapter (future; not in this task)
    -> Week 6 AI-side contracts (fusion/contracts.py)
    -> fusion logic (Task 2)
```

The camera is a Pi-side AI input and does not pass through the ESP32. Nothing in `fusion/` opens a port, parses frames, or touches GPIO.

## Contracts (`fusion/contracts.py`, all frozen dataclasses)

**`SourceEnvelope`**: `source_id` (non-empty), `origin` (`Origin.REAL` / `SIMULATED` / `UNKNOWN`, preserved exactly), `received_at_monotonic_s` (finite), `source_status` (`OK` / `UNAVAILABLE` / `FAILED`), optional `error_reason` (required for FAILED, forbidden for OK), optional opaque `device_timestamp`.

| Contract | Fields beyond the envelope | Rules |
|---|---|---|
| `VisionObservation` | `occupancy: ZoneOccupancyResult \| None` | OK requires a result; UNAVAILABLE/FAILED requires None. The Week 5 object is used as-is (not copied); its `occupancy_count` keeps the Week 5 meaning (detected persons in the classroom ROI). An unavailable camera is not a count of 0 |
| `PresenceReading` | `present: bool \| None` | whole-room only; `False` = valid reading of "nobody", `None` = no usable value; no distance, target counts or zone position (not verified in the project contract) |
| `LuxReading` | `value`, `unit = lx` | finite, >= 0; 0 lx is valid |
| `TemperatureReading` | `sensor_id`, `value`, `unit = degC` | finite, >= absolute zero (-273.15); several sensors distinguished by `sensor_id`, no fixed count; no comfort range |
| `HumidityReading` | `value`, `unit = %RH`, optional `sensor_id` | finite, 0..100 |

**Humidity hardware is UNKNOWN**: the roadmap names humidity, but CLAUDE.md names no humidity device, so nothing about a concrete sensor is assumed.

Non-OK sources must carry no value; an OK source must carry one. Booleans are rejected where numbers are expected, and non-booleans where a bool is expected.

## Units

`Unit` enum with exactly three members: `LUX` ("lx"), `CELSIUS` ("degC"), `PERCENT_RH` ("%RH"). A reading must carry the unit of its type; any other unit, string or None raises. **No unit conversion is performed.**

## Source status vs data usability

Two separate concepts. `SourceStatus` is what the acquisition path reports (OK / UNAVAILABLE / FAILED). `Usability` is derived by software: `FRESH`, `STALE`, `INVALID` (source UNAVAILABLE or FAILED; the status and reason are kept in the result), `MISSING` (no reading received), `UNCONFIGURED` (no max age configured; not "zero", not "failed").

## Clock rule

`age = now_monotonic_s - received_at_monotonic_s`, where `received_at_monotonic_s` is the Pi / host monotonic time at which the reading entered the AI-side adapter and `now_monotonic_s` is passed in explicitly. `device_timestamp` is retained as optional metadata only: the ESP32 clock domain and synchronisation are unknown, so it is never used for freshness (tested with a device timestamp in the past and the far future). Wall-clock time is not used. A `now` earlier than the receive time is impossible and raises `FusionContractError`.

## Freshness rules (`derive_source_state`, a pure helper — not fusion)

Order: no reading -> MISSING; source UNAVAILABLE or FAILED -> INVALID (even if no max age is configured); no configured max age -> UNCONFIGURED; `age <= max_age` -> FRESH (inclusive boundary, tested exactly at the boundary); `age > max_age` -> STALE. The returned `SourceState` always retains `source_id`, `origin`, `source_status`, `age_s`, `max_age_s` and a `reason`, so stale or failed readings stay in the diagnostic record. `fresh_value(reading, state)` returns the value only when FRESH and otherwise `None`, never a numeric stand-in.

**No timeout values were invented.** `config/settings.py` gains `FUSION_VISION_MAX_AGE_S`, `FUSION_PRESENCE_MAX_AGE_S`, `FUSION_LUX_MAX_AGE_S`, `FUSION_TEMPERATURE_MAX_AGE_S`, `FUSION_HUMIDITY_MAX_AGE_S` and `FUSION_MAX_SOURCE_SKEW_S`, all `None` (NOT CALIBRATED). `freshness_from_settings()` loads them into a validated `FusionFreshnessConfig`; the skew value is stored for Task 2 and not used yet.

## Missing is not zero

Tests prove: a missing or unavailable vision observation is not a count of 0 (a real zero count is a different object); missing presence is not False; a missing lux reading is not 0 lx; missing temperature is not 0 degC; missing humidity is not 0 %RH; stale, unconfigured and failed readings yield no value.

## Error handling

`FusionContractError` (a `ValueError`) with messages naming the source, field, unit or value (e.g. wrong unit, negative lux, humidity out of 0..100, below absolute zero, `occupancy` required / must be None, `now` earlier than receive time). Malformed input never surfaces as a tuple or index error.

## Tests (`tests/test_fusion_contracts.py`, 90 tests; SIMULATED / SYNTHETIC inputs only)

All 40 requested cases across envelope, vision, presence, lux, temperature (including two sensor IDs), humidity (0 and 100 valid, both sides rejected), freshness (MISSING, UNCONFIGURED, exact boundary FRESH, just-beyond STALE, FAILED / UNAVAILABLE never FRESH, `now` before receipt rejected, stale reading keeps age/origin/reason) and immutability (envelope, every reading, derived state, config; the Week 5 result is not copied or mutated), plus: device timestamp ignored for freshness, provenance preserved for every origin, determinism, configuration validation and defaults, settings loader, and a structural test that the module contains no fusion decisions or serial code.

## Mutation checks (each applied temporarily to `fusion/contracts.py`, restored in `finally`, restored hash compared)

| Mutation | Tests failed |
|---|---|
| A. missing / non-fresh value becomes numeric 0 | 8 |
| B. stale comparison reversed | 8 |
| C. exact max-age boundary inclusive -> exclusive | 2 |
| D. SIMULATED reported as REAL | 2 |
| E. negative lux accepted | 1 |
| F. RH above 100 accepted | 2 |
| G. non-fresh vision becomes 0 | 1 (a test for unavailable / stale vision was added after the first pass showed nothing covered this path) |
| G2. a non-OK source may carry a value | 3 |
| H. `device_timestamp` used for age | 1 |

All restored files compared identical by SHA-256 afterwards.

## Verification

Targeted (fusion contracts, Week 5, ROI, vision): 278 passed. Full suite **544 passed, 5 skipped, 0 failed** (454 + 90 new); skips unchanged and the openWakeWord block stayed lifted. Detector, ROI, zones and annotate hashes unchanged; frozen benchmark result files unchanged (hash and timestamp check, no inference).

## Validation status

Contracts and freshness semantics: **SOFTWARE-VALIDATED** (SIMULATED / SYNTHETIC fixtures only). Real sensor messages, radar and light behaviour, temperature and humidity readings, transport timing, the Pi hardware adapter and real max-age calibration: **PENDING HARDWARE**. Nothing is HARDWARE-VALIDATED.

Task 1: **COMPLETE (SOFTWARE-VALIDATED)**. Tasks 2-4: NOT STARTED. Next: Task 2, deterministic occupancy fusion decision logic (not started).


---

# Week 6 Task 2 — Deterministic Occupancy Fusion

Date: 2026-10-07. Scope: the pure fusion function `fuse_classroom_state` in `fusion/fuse.py`, rules R1–R8, the unified `ClassroomState`, environment pass-through and degraded reasoning. All validation is on **SIMULATED STRUCTURED INPUTS**; the max ages and skew used in tests (10 s, 2 s) are test values, not calibration. `vision/` (detector, roi, zones, annotate) and `fusion/contracts.py` are unchanged (SHA-256 identical before and after). No hardware adapter, ESP32 protocol, driver, logging side effect, actuator command, lighting or climate decision, numeric count correction or Week 7+ work was added; the Kaggle benchmark was not run.

Baseline before the task (actual run): 544 passed, 5 skipped, 0 failed. Hashes: detector `254b2530...0a13`, roi `ddf73251...cbd6`, zones `3eaa182a...538f`, annotate `685b86b3...`, contracts `1b57fdfb...`.

## Function and purity

`fuse_classroom_state(vision, presence, lux, temperatures, humidity, now_monotonic_s, freshness) -> ClassroomState`. It reads no clock, writes no log, touches no global or hardware state and mutates nothing; identical inputs, `now` and configuration give an identical immutable result (tested, including an AST check that the module imports no time, logging, random, os or serial and has no `global`). Usability comes from Task 1 `derive_source_state`; there is no second freshness implementation. A `now` earlier than a receive time still raises `FusionContractError` (a caller bug, not a sensor state).

## Semantics

- **`vision_count`** is the Week 5 `occupancy_count` (detected persons in the classroom ROI, per frame): not a student count, not unique people, not corrected ground truth. **Radar never changes it.** It is `None` whenever vision does not take part; never substituted by 0.
- **`fused_presence`** is PRESENT / ABSENT / UNKNOWN. A vision count of 0 alone is UNKNOWN, not ABSENT, because the detector has known false negatives (Week 4 frozen baseline).
- **CONFIRMED** means the two currently usable occupancy sources agree; it does not mean ground-truth verified. **CONFIRMED_EMPTY** means both sources agree on "nobody" at that instant; it is not independent ground truth.

## Decision table (V = vision, P = radar presence; "usable" = FRESH and not excluded by skew)

| Rule | Conditions | Evidence | Fused presence | Degraded | Reason |
|---|---|---|---|---|---|
| R1 | V count > 0, P True | CONFIRMED | PRESENT | no | none |
| R2 | V count 0, P False | CONFIRMED_EMPTY | ABSENT | no | none |
| R3 | V count 0, P True | CONFLICT | UNKNOWN | yes | PRESENCE_WITHOUT_DETECTIONS |
| R4 | V count > 0, P False | CONFLICT | UNKNOWN | yes | DETECTIONS_WITHOUT_PRESENCE |
| R5 | V usable, P not | VISION_ONLY | PRESENT if count > 0, else UNKNOWN | yes | PRESENCE_MISSING / STALE / INVALID / UNCONFIGURED |
| R6 | P usable, V not | SENSOR_ONLY | PRESENT or ABSENT from radar; `vision_count` None | yes | VISION_MISSING / STALE / INVALID / UNCONFIGURED |
| R7 | neither usable | UNKNOWN | UNKNOWN | yes | both unusable reasons |
| R8 | both FRESH but skew too large (or unconfigured) | newer source decides (R5 or R6 semantics) | per fallback rule | yes | SOURCE_SKEW or SKEW_UNCONFIGURED |

**Conflict semantics (R3, R4):** the conflict is surfaced, fused presence is UNKNOWN, no source silently wins, the vision count is unchanged (0 stays 0, 4 stays 4), and the raw radar value and both source states remain in the state. Radar cannot raise a count or fabricate "1".

**R8 skew:** `skew = |vision.received_at_monotonic_s - presence.received_at_monotonic_s|`. `skew <= max skew` allows corroboration; `skew > max skew` triggers R8 (both boundaries tested exactly). Only the **older** source is excluded from occupancy corroboration; it stays FRESH (not relabelled stale), and its `SourceState`, origin, raw value (`sensor_present` / `raw_*`) remain visible. Newer vision gives R5 semantics, newer radar R6 semantics. Representation: `rule_id = "R8"` and `fallback_rule_id = "R5"` or `"R6"`. **Unconfigured max skew never allows corroboration** (reason `SKEW_UNCONFIGURED`, never CONFIRMED); the same exclusion applies, and on an exact timestamp tie the vision reading is kept because it carries the count and zone data (a documented tie rule, flagged for review). Skew applies only to vision vs radar: lux, temperature and humidity are never compared with the camera and never reject each other.

## Zone data

Copied unchanged from the usable Week 5 `ZoneOccupancyResult` only (`zone_counts` is the same tuple object, `outside_classroom_roi`, `zones_configured`). When `zones_configured` is False, `zone_counts` and `unassigned_inside_roi` are `None`, not LEFT=0 / CENTER=0 / RIGHT=0 and not "UNASSIGNED = everything". Radar never contributes zone data, and when vision is not used all zone fields are `None`.

## Environment

Lux, temperature and humidity never take part in R1–R8. Each is derived with its own freshness: FRESH gives the validated value; MISSING / STALE / INVALID / UNCONFIGURED gives `value = None` with the `SourceState` (age, origin, status, reason) retained. No dark/bright, hot/cold or comfort labels. Several temperature sensors are kept individually, in input order, by `sensor_id`: never averaged, never reduced to a room estimate; duplicate ids are rejected. `environment.issues` holds stable codes (for example `LUX_STALE`, `TEMPERATURE[t-c]_STALE`); environment problems are informational and are **not** part of `degraded`, which describes occupancy fusion only. An unconfigured humidity device therefore cannot disturb occupancy logic (tested). Environment variants (fresh, 0 lx, stale, missing, failed) leave rule, evidence, occupancy and degraded unchanged for six occupancy cases.

## Provenance

Every source's `origin` stays in its `SourceState`. `aggregate_origin` is conservative: contributing sources are vision if used, radar if used, and each FRESH environment value; none contributing gives UNKNOWN; any UNKNOWN gives UNKNOWN; otherwise any SIMULATED gives SIMULATED; otherwise REAL. Stale, failed, missing or skew-excluded sources do not contribute (but stay visible). A simulated environment value taints an otherwise REAL aggregate.

## Degraded reasons

Stable codes (`DegradedReason`), in declaration order: `VISION_*`, `PRESENCE_*` (MISSING, STALE, INVALID, UNCONFIGURED), `SOURCE_SKEW`, `SKEW_UNCONFIGURED`, `PRESENCE_WITHOUT_DETECTIONS`, `DETECTIONS_WITHOUT_PRESENCE`. Degraded is False only for R1 and R2.

## ClassroomState (frozen)

`evaluated_at_monotonic_s`, `rule_id`, `fallback_rule_id`, `occupancy` (`evidence`, `fused_presence`, `vision_count`, `zone_counts`, `unassigned_inside_roi`, `outside_classroom_roi`, `zones_configured`, `vision_used`, `presence_used`, `sensor_present`, `raw_vision_count`, `raw_presence`), `vision_state`, `presence_state`, `environment` (`lux`, `temperatures`, `humidity`, `issues`), `degraded`, `degraded_reasons`, `aggregate_origin`. No actuator fields.

## Tests (`tests/test_fusion_logic.py`, 57 collected cases; SIMULATED inputs)

R1 to R7 (R5 and R6 each in both variants), R8 both orientations, exact skew boundary allowed and just-over excluded, unconfigured skew, skew not applied when a source is already unusable, stale / failed / unavailable / missing sources, unconfigured freshness, deterministic reason order, radar never changing the count (counts 0, 3, 7 against radar True, False, None), the known-limitation cases (0 + radar present and 3 + radar present), zone preservation and non-fabrication, environment pass-through, stale lux excluded, missing environment is None, humidity unconfigured, separate temperature sensors, duplicate sensor ids, environment independence over six occupancy cases and seven environment variants, no interpretation or actuator fields, provenance (per source, aggregate rules, taint by a simulated environment value, excluded sources), determinism, no input mutation, purity, immutability, invalid input errors.

## Mutation checks (each on a copy applied to `fusion/fuse.py`, restored in `finally`, restored hash compared)

| Mutation | Tests failed |
|---|---|
| A. R3 converted from CONFLICT to CONFIRMED | 2 |
| B. R4 silently trusts a source | 1 |
| C. radar present changes count 0 to 1 | 3 |
| D. radar value copied into zone counts | 4 |
| E. vision-only count 0 becomes ABSENT | 1 |
| F. older skewed source still participates | 3 |
| G. skew boundary `>` changed to `>=` | 1 |
| H. environment value changes occupancy evidence | 6 |
| I. stale source used as fresh | 3 |
| J. aggregate SIMULATED becomes REAL | 3 |

All restored files compared identical by SHA-256.

## Status

Fusion logic: **SOFTWARE-VALIDATED** on SIMULATED structured inputs. Real radar, lux, temperature and humidity (humidity device UNKNOWN), the Pi adapter, ESP32 protocol, timestamp transport behaviour and freshness / skew calibration remain PENDING HARDWARE / NOT AVAILABLE. Nothing is HARDWARE-VALIDATED. Open for review: the tie rule under unconfigured skew. Task 2: **COMPLETE (SOFTWARE-VALIDATED)**. Tasks 3 and 4: NOT STARTED. Next (not started): Task 3, synthetic end-to-end fusion validation, recovery, logging and performance.


---

# Week 6 Task 3 — Synthetic End-to-End Fusion Validation, Recovery, Logging and Performance

Date: 2026-10-07. Scope: validate the complete Week 6 software path with labelled **SIMULATED / SYNTHETIC** inputs, recovery as successive pure evaluations, a thin telemetry layer, and descriptive fusion-stage timing. No R1-R8 semantics changed; no driver, adapter, ESP32 protocol, actuator command, count correction, zone fabrication, temperature averaging or comfort/darkness classification was added. `vision/` (detector, roi, zones, annotate) and `fusion/contracts.py` are unchanged (SHA-256 identical before and after); `fusion/fuse.py` changed in comments and the module docstring only (tie wording, below); the Kaggle benchmark was not run.

Baseline (actual run): 601 passed, 5 skipped, 0 failed. Hashes before: contracts `1b57fdfb...`, fuse `e90166aa...`, detector `254b2530...`, roi `ddf73251...`, zones `3eaa182a...`, annotate `685b86b3...`.

## Pipeline exercised

```
synthetic Week 5 ZoneOccupancyResult (real assign_detections)
  -> VisionObservation + PresenceReading + LuxReading + TemperatureReading(s) + HumidityReading
  -> derive_source_state (inside fuse_classroom_state)
  -> fuse_classroom_state
  -> ClassroomState
  -> fusion_decision_record / format_fusion_decision (audit and log representation)
```

The fixtures call the real public APIs; nothing from Week 5 or Week 6 is duplicated. Fixtures live in `tests/fusion_synthetic.py` (nothing synthetic enters production code or settings). Test freshness values (10 s max age, 2 s max skew) are **TEST VALUES ONLY — NOT CALIBRATED**.

## Tie-rule wording (frozen)

With a configured max skew, equal timestamps give skew 0 and **do not** invoke R8; normal R1-R4 corroboration applies (tested). Only when the skew configuration itself is unconfigured and the timestamps are exactly equal is there no newer source: vision then receives **fallback precedence** (it carries the count and Week 5 zone data). That is precedence, not "vision is newer". The result stays degraded, `SKEW_UNCONFIGURED` stays visible, the radar value and state stay visible, and no CONFIRMED / CONFIRMED_EMPTY claim is possible. Only the `fuse.py` docstring and one comment were reworded to say this; behaviour is identical. The telemetry record carries `fallback_basis` (`NEWER_SOURCE`, `FALLBACK_PRECEDENCE` or none), read from the final state (equal source ages mean a tie) so a log cannot describe a tie as "newer".

## Primary audit scenario

Week 5 result LEFT 8, CENTER 7, RIGHT 9, UNASSIGNED 5, OUTSIDE 3 (29 inside the classroom ROI), radar True, lux 320, temperatures t-a 21.5 and t-b 22.0, humidity 45, all SIMULATED. Verified against explicit expectations: input origins and receive times; all sources FRESH; vision count 29; the exact zone numbers; radar True; environment values; **R1, no fallback, CONFIRMED, PRESENT, not degraded, no reasons, aggregate SIMULATED, no environment issues**; the receive times agree with the reported ages (`now - age`).

## Scenario coverage (`tests/fusion_synthetic.py`, 38 scenarios, 80 collected cases in `tests/test_fusion_scenarios.py`)

R1 occupied agreement; R2 empty agreement; R3 radar present without detections; R4 detections with radar absent; R5 vision positive only; R5 vision zero only; R6 radar present only; R6 radar absent only; R7 neither source; R8 vision newer; R8 radar newer; skew exactly at max (allowed); skew just beyond max; skew unconfigured (equal timestamps, and radar newer); equal timestamps with configured skew; stale vision; stale radar; FAILED radar; UNAVAILABLE camera; recovery sequences (stale-fresh, FAILED radar-OK, UNAVAILABLE camera-valid, skew-synchronised, stale environment-fresh); provenance (all simulated, structural REAL mix, structural all-REAL, UNKNOWN); zone preservation with radar present, absent, missing and stale; zones not configured; environment independence; undercount protection (0 plus radar present, 3 plus radar present); deterministic repeat. All rules R1 to R8 are represented (asserted).

**Synthetic fusion scenario correctness: 38 / 38 predefined scenarios matched their expected output.** The fixtures define expected states exactly; this is software scenario correctness, **not real-world fusion accuracy**, and no confidence interval is claimed.

## Conflict, single-source and skew behaviour

R3 and R4 stay CONFLICT with fused presence UNKNOWN; the vision count (0, 4) is unchanged; the raw radar value (True, False) and both source states remain visible. FAILED, missing and stale sources never turn into values: failed / missing / stale radar leaves `sensor_present` None (not False), and an unavailable / missing / stale camera leaves the count None (not 0), while a real 0 and a real False remain distinct. Skew exclusion keeps the older source FRESH (not relabelled stale), with its age, origin and raw value visible, and it never takes part in corroboration. Radar never changes a count (0 to 7 checked) and never creates zone data.

## Recovery and the absence of hysteresis

Recovery is tested as successive pure evaluations: FRESH, then STALE as `now` advances, then FRESH on a new reading; FAILED radar, then OK; UNAVAILABLE camera, then valid; skew, then synchronised readings; stale environment, then fresh. Replaying an early call after later ones returns exactly its original answer (no hidden state), a conflict is not sticky, and the fusion module holds no module-level mutable state (asserted). **Week 6 has no temporal smoothing, debounce, persistence, cooldown, state cache or hysteresis**: a sensor recovers on the next valid fresh evaluation. Any such policy would be separate, explicit work.

## Environment independence

Nine environment variants (fresh, 0 lx, 900 lx, stale, several temperatures, stale temperature, 0 and 100 %RH, stale humidity, all-fresh) across six occupancy situations leave the rule, fallback, occupancy state, degraded flag and reasons unchanged. Environment values never select R1-R8, never change fused presence, the count or zone counts. They affect only environment state, `environment.issues` and aggregate provenance. Environment sources have independent freshness (none is compared with the camera).

## Occupancy-degraded terminology

`ClassroomState.degraded` and `degraded_reasons` describe **occupancy-evidence quality**. They are not an overall health flag: environment sensor health is represented independently in `environment.issues` and in each `EnvironmentValue.state`. A state with a perfectly healthy R1 and missing lux / humidity is `degraded = False` with issues `LUX_MISSING`, `HUMIDITY_MISSING` (tested). The existing data model and earlier Task 2 wording already had this meaning; it is now stated explicitly in the tests, the report and the status page.

## Provenance

Per-source origin stays in each `SourceState`. Aggregate origin was exercised for all-simulated (SIMULATED), mixed structural REAL plus SIMULATED (SIMULATED), one UNKNOWN contributor (UNKNOWN), no contributing source (UNKNOWN) and all structural REAL (REAL). A structurally REAL test object tests provenance logic only and is **not** hardware validation.

## Zone preservation

The explicit LEFT / CENTER / RIGHT / UNASSIGNED / OUTSIDE numbers survive fusion unchanged (same tuple object) whether radar is present, absent, missing or stale. With `zones_configured = False`, zone data stays `None` (never LEFT = 0 / CENTER = 0 / RIGHT = 0), in the state and in the log (`zones=none`).

## Known vision undercount

The frozen Week 4 baseline (547 frames, 19,583 labelled, 3,236 predicted, MAE 29.88, bias -29.88) was not rerun. Regression protections: vision 0 with radar present stays 0; vision 3 with radar present stays 3 (counts 0 to 7 checked); no multiplier, inferred count or "at least one" replacement exists.

## Telemetry (`fusion/telemetry.py`)

A thin layer outside the pure function that consumes the final `ClassroomState`. `fusion_decision_record(state)` builds an ordered plain-value record: `event=fusion_decision`, rule and fallback ids, `fallback_basis`, evidence, fused presence, degraded flag and reason codes, per-source usability, status, age and origin, `vision_count`, `raw_vision_count`, `sensor_present`, `raw_presence`, `vision_used`, `presence_used`, zones (or none), unassigned, outside, lux / temperatures / humidity with states, environment issues and aggregate origin. `format_fusion_decision` renders the project's `key=value` style; `log_fusion_decision(state, logger)` emits one INFO line through an **injected** logger (the project's `homepod_ai` logger style) and returns False on any logging failure without touching the state. No images, detections or personal fields are recorded. Tests prove: CONFLICT is logged as CONFLICT, SIMULATED stays SIMULATED, SOURCE_SKEW / SKEW_UNCONFIGURED and the fallback basis are visible, R3/R4 raw evidence is retained, missing values log as `none` (not 0), the record reads the final state (a tampered state is reflected, so nothing is recomputed), formatting and logging do not alter the state, and a failing logger does not affect it.

## Fusion-stage latency (WINDOWS DEVELOPMENT-PC FUSION-STAGE LATENCY, descriptive)

`fuse_classroom_state` only: 500 warm-up calls and 5,000 timed calls per scenario, microseconds. No threshold is asserted anywhere, and nothing is derived for the Raspberry Pi, FPS or end-to-end VESTA latency.

| Scenario | Mean | Median | p95 |
|---|---|---|---|
| A. vision + radar only | 28.0 | 21.6 | 38.1 |
| B. + lux + 1 temperature + humidity | 31.7 | 26.5 | 45.4 |
| C. + lux + 2 temperatures + humidity | 32.6 | 30.3 | 40.6 |
| D. conflict path (R3) | 30.7 | 26.4 | 41.1 |
| E. skew path (R8) | 31.2 | 26.1 | 53.9 |
| F. missing / degraded path (R7) | 21.3 | 18.8 | 28.8 |

Machine-readable output: `vision_assets/results/week6_synthetic/fusion_benchmark.json`. The benchmark never modifies its inputs (tested).

## Mutation checks (each applied temporarily, restored in `finally`, restored hashes compared; per observation #8)

| Mutation | Tests failed |
|---|---|
| A. recovery stays STALE after a new fresh reading (hidden sticky state added to `derive_source_state`) | 88 (the sticky state leaks across the whole process; the recovery tests are among them) |
| B. FAILED radar becomes False | 6 |
| C. conflict omitted from telemetry | 2 |
| D. telemetry reports SIMULATED as REAL | 2 |
| E. SOURCE_SKEW omitted from telemetry | 1 |
| F. environment issue changes occupancy evidence | 65 |
| G. zones modified while formatting | 3 |
| H. no-zone state logged as zero zone counts | 1 |
| I. unconfigured-skew tie labelled as a newer source | 2 |
| J. benchmark helper mutates input fixtures | 1 |

## Verification

Targeted (fusion contracts, logic, scenarios, Week 5, ROI, vision): 415 passed. Full suite **681 passed, 5 skipped, 0 failed** (601 + 80); skips unchanged and the openWakeWord block stayed lifted. Detector, ROI, zones, annotate and contracts hashes unchanged; the five frozen Kaggle result files kept their recorded hashes (no inference).

## Limitations and pending hardware

All inputs are SIMULATED STRUCTURED INPUTS. Still PENDING HARDWARE / NOT AVAILABLE: the real ESP32 message format, the Pi adapter, real LD2410B behaviour, real BH1750 values, real temperature sensors, the humidity device (identity UNKNOWN), real timestamps and transport, freshness and skew calibration, physical failure and recovery, and end-to-end classroom fusion. Nothing here is HARDWARE-VALIDATED.

Task 3: **COMPLETE (SOFTWARE-VALIDATED)**. Task 4: NOT STARTED (Week 6 not closed). Next (not started): Task 4, software evaluation and formal closeout.


---

# Week 6 Software Closeout

Date: 2026-10-07. Audit, verification and documentation. No new functionality, no change to fusion semantics, no hardware adapter or ESP32 protocol, no Kaggle inference, no Week 7 work. Claude Code was the only writer.

**Decisions.** A. Week 6 unified classroom-state **software module: COMPLETE** (SOFTWARE-VALIDATED on SIMULATED structured inputs). B. **Real-hardware validation: NOT DONE** (all sensor inputs, the Pi adapter, the ESP32 protocol and calibration are PENDING HARDWARE). C. The project may begin Week 7 planning while hardware validation stays explicitly pending. **One caveat: the independent external review requested for this closeout (Antigravity in VS Code) was NOT PERFORMED**, because this session has no access to it. It is recorded as pending, a ready-to-run review brief was given to the user, and any change it proposes must be verified before Claude Code applies it. The conclusions below rest on Claude Code's own adversarial audit, the repository tests and an exhaustive invariant sweep, not on an independent reviewer.

## Authoritative Week 6 roadmap (CLAUDE.md)

Goal: combine classroom perception signals. Inputs: camera, radar, lux, temperature, humidity. Tasks: sensor data interface, sensor validation, occupancy fusion, environment state representation, missing-sensor handling. Deliverable: unified classroom-state representation. No metrics, algorithm, confidence model or actuator output is specified. Week 10 (lighting decisions) and Week 11 (climate / presentation) are separate.

## Baseline and integrity (re-verified at closeout)

Full suite at start: **681 passed, 5 skipped, 0 failed**, identical to the reference (skips: 4 ReSpeaker hardware-gated, 1 openWakeWord recorded clip; the openWakeWord block stayed lifted). Hashes at start: contracts `1b57fdfb...`, fuse `f46466c0...`, telemetry `06c0da9e...`, detector `254b2530...`, roi `ddf73251...`, zones `3eaa182a...`, annotate `685b86b3...`. Frozen Week 4 benchmark: the five result files, manifest (`3189a8bb...`) and model (`427cc366...`) match their recorded hashes and keep the 2026-10-06 16:02:31 modification time (hash and `stat` only, no inference). `fusion_benchmark.json` matches the Task 3 table exactly.

## Method

1. **Claude Code** (sole writer): read CLAUDE.md, re-read the three fusion modules, ran an AST audit (module-level mutable state, globals, imports, broad exception handlers, mutable defaults), a vocabulary search for future-week logic, and an **exhaustive invariant sweep of 2,394 input combinations** (vision missing / unavailable / failed / stale / counts 0, 1, 3 at five receive times; radar missing / unavailable / failed / stale / True / False at five receive times; three skew configurations; three environment variants). The sweep asserts: counts equal the raw vision count or None and never differ; zone data only from used vision, as the same object; ABSENT only from a usable radar False; CONFIRMED / CONFIRMED_EMPTY only with both sources used and skew-corroborated; conflicts are UNKNOWN; vision-only zero is UNKNOWN; sensor-only has no count; degraded flag and reason lists consistent; R8 exactly when the fallback id exists and with a skew reason; no corroboration under unconfigured skew; the skew boundary consistent with the rule; environment never changing occupancy; determinism; telemetry agreeing with the state; the precedence label only on exact ties. Result: **2,394 combinations, 0 violations, all eight rules reached.** The sweep was a throwaway script, not added to the repository.
2. **Antigravity**: NOT PERFORMED (no access from this session). Status: PENDING (user to run the brief; findings, if any, to be verified against CLAUDE.md and code before any change).
3. **Plugins / connectors**: none used; none would have materially improved a repository-internal check.

## Findings

| Source | Severity | Finding | Evidence | Decision |
|---|---|---|---|---|
| Claude audit | NONE | no module-level mutable state, no globals, no mutable defaults, no future-week logic in `fusion/` | AST audit; vocabulary hits are only "not done" disclaimers in comments | none needed |
| Claude audit | NONE | rule table consistent and reachable; no silent zero/False fallback; radar never changes a count; zones copied only from used vision | 2,394-combination sweep, 0 violations; mutation checks A to J (Tasks 2 and 3) | none needed |
| Claude audit | MEDIUM (integration contract) | `derive_source_state` raises `FusionContractError` when `now_monotonic_s` is earlier than a reading's receive time, for any source including optional environment sensors, so one such reading aborts the whole fusion call | `fusion/contracts.py`, tests `test_now_earlier_than_receive_time_rejected` and `test_invalid_inputs_fail_clearly`; required by the Task 1 specification | not changed (specified behaviour, not a defect). Documented requirement for Week 9/13 integration: the caller must sample `now` after collecting all readings (or reject such a reading at the adapter) |
| Claude audit | MEDIUM (open design question) | with unconfigured skew and unequal timestamps, the older source is excluded by recency even if the gap is tiny, because no calibrated skew exists to judge the gap | `fuse.py` R8 branch; documented in Task 2 and Task 3 | not changed; recorded for review when skew is calibrated (PENDING HARDWARE) |
| Claude audit | LOW | an expected-but-absent temperature sensor cannot be reported as MISSING (an empty tuple means none was supplied) because sensor ids are not configured | `fuse.py`; Task 2 report states it | not changed; revisit when the real sensor list is known |
| Claude audit | LOW | telemetry `fallback_basis` is derived from source ages in the final state (equal ages mean a tie) rather than stored | `fusion/telemetry.py` | accepted: it reads state only and does not recompute a decision; a data-model change was not justified |
| Claude audit | LOW | `log_fusion_decision` catches `Exception` and returns False without reporting the cause | `fusion/telemetry.py` | accepted: specified (a logging failure must not affect fusion); a failure counter can be added when a logging policy exists |
| Claude audit | LOW | `ClassroomState` had no docstring stating that `degraded` means occupancy-evidence quality | `fusion/fuse.py` | **FIXED** (docstring only) |
| Claude audit | LOW | a test carried a dead variable and a weak "occupancy in module docstring" assertion | `tests/test_fusion_scenarios.py` | **FIXED** (asserts the new docstring) |
| Antigravity | n/a | not performed | n/a | pending |

No substantive Claude / Antigravity disagreement exists to resolve.

## Code changes at closeout

Two, both non-behavioural and reported: a docstring on `ClassroomState` in `fusion/fuse.py` (hash `f46466c0...` to `b08a260a...`) and one test assertion cleanup in `tests/test_fusion_scenarios.py`. No fusion semantics changed (the rule table, sweep and all tests are unaffected).

## Requirement-to-evidence matrix

Classifications: SOFTWARE-VALIDATED on SIMULATED structured inputs unless stated. Nothing is HARDWARE-VALIDATED.

| Requirement | Implementation | Test evidence | Classification | Status |
|---|---|---|---|---|
| Sensor data interface | `fusion/contracts.py` envelope and five reading contracts (AI-side, protocol-independent) | `tests/test_fusion_contracts.py` (90) | SOFTWARE-VALIDATED; real adapter and protocol NOT AVAILABLE | Contract complete; adapter pending |
| Sensor validation | finite values, lux >= 0, temperature >= absolute zero, RH 0..100, explicit units, status / value consistency | same file | SOFTWARE-VALIDATED | Complete |
| Occupancy fusion | `fusion/fuse.py` R1 to R8 | `tests/test_fusion_logic.py` (57), scenarios, sweep, mutations | SOFTWARE-VALIDATED | Complete |
| Environment state representation | `EnvironmentState` / `EnvironmentValue` pass-through, no interpretation | logic and scenario tests | SOFTWARE-VALIDATED | Complete |
| Missing-sensor handling | MISSING / STALE / INVALID / UNCONFIGURED, never zero / False | contract, logic and scenario tests | SOFTWARE-VALIDATED | Complete |
| Camera input | `VisionObservation` wrapping the Week 5 result | contract and logic tests | SOFTWARE-VALIDATED; live camera PENDING HARDWARE | Contract complete |
| Radar input | `PresenceReading` (whole-room bool) | same | SOFTWARE-VALIDATED; LD2410B PENDING HARDWARE | Contract complete |
| Lux input | `LuxReading` (lx) | same | SOFTWARE-VALIDATED; BH1750 PENDING HARDWARE | Contract complete |
| Temperature input | `TemperatureReading` (degC, sensor ids) | same | SOFTWARE-VALIDATED; sensors PENDING HARDWARE | Contract complete |
| Humidity input | `HumidityReading` (%RH) | same | SOFTWARE-VALIDATED; device UNKNOWN HARDWARE | Contract complete |
| Unified ClassroomState | `ClassroomState` | logic, scenario, sweep | SOFTWARE-VALIDATED | Deliverable complete |
| Conflict handling | R3 / R4 CONFLICT with UNKNOWN presence, raw evidence kept | logic and scenario tests | SOFTWARE-VALIDATED | Complete |
| Freshness | monotonic receive-time freshness, inclusive boundary, skew | contract, logic and scenario tests | SOFTWARE-VALIDATED; calibration PENDING HARDWARE | Complete |
| Recovery | stateless recomputation, no hysteresis | scenario tests (five sequences) | SYNTHETICALLY VALIDATED | Complete |
| Telemetry | `fusion/telemetry.py` | scenario tests | SOFTWARE-VALIDATED | Complete |
| Performance | fusion-stage micro-timing | `fusion_benchmark.json` | MEASURED ON WINDOWS DEVELOPMENT PC | Descriptive only |

## Claim verification (code, test, documentation)

| Claim | Code | Test | Documentation |
|---|---|---|---|
| Missing / failed radar never becomes False | `fresh_value`, R5 / R7 branches | `test_failed_missing_and_stale_never_become_values`, scenario 18 | Task 1 and 3 reports |
| Radar never changes the numeric count | `count = vision.occupancy.occupancy_count` only | `test_radar_never_changes_the_numeric_count`, `test_undercount_is_never_corrected` | Task 2 and 3 reports |
| No fabricated zones | zone fields filled only inside `if use_v` | `test_radar_does_not_fabricate_zone_counts`, `test_no_zones_stays_unavailable_not_zero` | Task 2 report |
| Environment never drives occupancy | no environment variable reaches R1-R8 | `test_environment_never_changes_occupancy` | Task 2 and 3 reports |
| Freshness uses receive time only | `age = now - received_at_monotonic_s` | `test_freshness_uses_receive_time_not_device_timestamp` | Task 1 report |
| Equal timestamps do not invoke R8 (configured skew) | `skew > max_skew` only | `test_equal_timestamps_with_configured_skew_do_not_invoke_r8` | Task 3 report |
| Unconfigured-skew tie is precedence, not "newer" | R8 branch comment, `fallback_basis` | `test_unconfigured_skew_fallback_is_precedence_not_temporal_order` | Task 3 report |
| `degraded` is occupancy-only | `degraded = evidence not in (...)`, class docstring | `test_degraded_means_occupancy_evidence_quality_only` | Task 3 report, status |
| No hidden state | no module state, no globals | `test_fusion_module_keeps_no_cross_call_state`, recovery tests | Task 3 report |
| Telemetry reads the final state | `fusion_decision_record(state)` | `test_record_is_built_from_the_final_state_not_recomputed` | Task 3 report |
| No personal data logged | fixed key set | `test_log_contains_no_image_or_personal_fields` | Task 3 report |

All have code, test and documentation.

## Final architecture, rules, semantics

```
Week 5 ZoneOccupancyResult -> VisionObservation \
radar PresenceReading ------------------------- -> fuse_classroom_state(now, freshness) -> ClassroomState -> telemetry record
lux / temperature(s) / humidity readings ------/      (pure, deterministic, stateless)
```

Contract boundary: the ESP32 reads raw sensors and the Pi receives structured readings; the AI side defines protocol-independent contracts, and a future Pi adapter (NOT IMPLEMENTED, protocol NOT AVAILABLE) maps the real protocol into them.

Decision table (unchanged): R1 vision above 0 and radar True: CONFIRMED, PRESENT. R2 vision 0 and radar False: CONFIRMED_EMPTY, ABSENT. R3 vision 0 and radar True: CONFLICT, UNKNOWN. R4 vision above 0 and radar False: CONFLICT, UNKNOWN. R5 vision only: VISION_ONLY (PRESENT if the count is above 0, otherwise UNKNOWN). R6 radar only: SENSOR_ONLY (PRESENT or ABSENT, count None). R7 neither usable: UNKNOWN. R8 both fresh but not corroborable (skew above the maximum, or unconfigured skew): the older source is excluded and the newer one decides (R5 or R6 semantics), with fallback precedence to vision only on an exact tie under unconfigured skew.

Presence versus count: the count is the Week 5 detected-person count inside the classroom ROI, per frame; radar corroborates whole-room presence only. Known vision limitation (frozen Week 4 baseline, not rerun): 547 frames, 19,583 labelled persons, 3,236 predicted, MAE 29.88, bias -29.88, maximum prediction 15, maximum reference 65. **Fusion does not repair the undercount**; no multiplier, inferred count or "at least one" substitute exists.

Environment: lux, temperature (several sensors, never averaged) and humidity are validated and passed through with their own freshness; no dark/bright, hot/cold or comfort interpretation. Degraded: `ClassroomState.degraded` means occupancy-evidence degraded; environment health is separate (`environment.issues`, per-source state); example: R1 agreement with missing lux has `degraded = False` and issue `LUX_MISSING`. Recovery: successive pure evaluations (fresh, stale, fresh; FAILED to OK; UNAVAILABLE to valid; skewed to synchronised; stale environment to fresh), with no cache, sticky conflict, cooldown, debounce or hysteresis. Provenance: per-source origin retained; aggregate SIMULATED if any contributor is simulated, UNKNOWN if any is unknown or none contribute, REAL only if all contributors are REAL (structural in tests, not hardware evidence).

Telemetry privacy: records hold rule ids, states, counts, flags and origins only (no images, detections, faces, names or identifiers).

## Synthetic evaluation and performance

**Synthetic software scenario correctness: 38 / 38 predefined scenarios matched** (not real fusion accuracy). Windows development-PC fusion-stage latency (microseconds, mean / median / p95, 5,000 runs each): vision + radar 28.0 / 21.6 / 38.1; plus lux, 1 temperature, humidity 31.7 / 26.5 / 45.4; plus 2 temperatures 32.6 / 30.3 / 40.6; conflict path 30.7 / 26.4 / 41.1; skew path 31.2 / 26.1 / 53.9; missing / degraded path 21.3 / 18.8 / 28.8. This is not Pi latency, ESP32 latency, camera latency, FPS or end-to-end VESTA latency.

## Verification after closeout changes

Targeted (fusion contracts, logic, scenarios, Week 5, ROI, vision): 415 passed. Full suite: **681 passed, 5 skipped, 0 failed**. Protected hashes after the changes: detector, roi, zones, annotate, contracts and telemetry unchanged; fuse `b08a260a...` (docstring only). Frozen Kaggle outputs and the benchmark JSON unchanged. Scenario correctness re-confirmed at 38 / 38.

## Hardware-pending items (carried forward)

ESP32 protocol: NOT AVAILABLE. Pi adapter: NOT IMPLEMENTED. LD2410B: PENDING HARDWARE. BH1750: PENDING HARDWARE. Temperature sensors: PENDING HARDWARE. Humidity device: UNKNOWN / PENDING HARDWARE. Real timestamp transport: PENDING HARDWARE. Freshness calibration: PENDING HARDWARE. Source-skew calibration: PENDING HARDWARE. Physical failure / recovery: PENDING HARDWARE. End-to-end real fusion: PENDING HARDWARE. Integration note: sample `now` after collecting all readings.

## Deferred scope (confirmed absent from Week 6)

Week 7 VAD / STT, Week 8 intent understanding, Week 9 action router and ESP32 action loop, Week 10 lighting decisions, Week 11 climate / presentation intelligence, hardware adapters, serial / GPIO, sensor drivers, ML, LLM, probabilities, count correction, temporal smoothing.

## Final Week 6 status

WEEK 6 UNIFIED CLASSROOM-STATE SOFTWARE MODULE: COMPLETE. CONTRACTS: SOFTWARE-VALIDATED. OCCUPANCY FUSION: SOFTWARE-VALIDATED ON SIMULATED INPUTS. ENVIRONMENT REPRESENTATION: SOFTWARE-VALIDATED ON SIMULATED INPUTS. REAL SENSOR INPUTS: PENDING HARDWARE. PI / ESP32 ADAPTER: NOT IMPLEMENTED, PROTOCOL NOT AVAILABLE. REAL FUSION VALIDATION: PENDING HARDWARE. INDEPENDENT EXTERNAL (ANTIGRAVITY) REVIEW: PENDING. Week 7: NOT STARTED. "Software complete" must not be read as validated on real classroom hardware.


---

# Week 6 Post-Closeout — Independent Antigravity Review Reconciliation

Date: 2026-10-07. The Week 6 closeout above was completed by Claude Code **before** any external review, which is why it records the Antigravity review as not performed; that statement was true when written and is kept as history. An independent read-only Antigravity review was performed afterwards. This section reconciles it. Claude Code remained the only writer, each finding was verified against CLAUDE.md, the code, the tests and the existing design decisions, and Week 7 was not started.

**INDEPENDENT ANTIGRAVITY REVIEW: COMPLETED.** Antigravity reproduced the full suite (681 passed, 5 skipped, 0 failed) and all 38 synthetic scenarios, found no problem in duplicated core fusion logic, unreachable or contradictory R1-R8 rules, silent missing-to-zero/False fallback, mutable global state, environment values changing occupancy, radar changing counts, fabricated zone counts, or architectural compliance, and returned 7 findings. A post-fix read-only re-review is the remaining step.

## Decision matrix

| # | Finding | Severity | Verified | Decision | Behaviour change |
|---|---|---|---|---|---|
| 1 | A reading received after `now` aborts the whole evaluation | MEDIUM | Yes (`derive_source_state`; tests exist) | ACCEPTED CONTRACT PRECONDITION | No |
| 2 | Uncalibrated skew can exclude vision | MEDIUM | Yes (R8, radar newer) | KNOWN UNCALIBRATED-INTEGRATION LIMITATION | No |
| 3 | Telemetry re-derived the fallback basis | LOW | Yes (`_fallback_basis` compared ages) | ACCEPTED, FIXED | Yes: `ClassroomState.fallback_basis` |
| 4 | Expected temperature sensors cannot be flagged missing | LOW | Yes | DEFERRED UNTIL HARDWARE CONTRACT | No |
| 5 | `temperatures=None` leaked `TypeError` | LOW | Yes | ACCEPTED, FIXED | Yes: `FusionContractError` |
| 6 | Whitespace in ids breaks key=value telemetry | LOW | Yes | ACCEPTED, FIXED | Yes: canonical id rule |
| 7 | Review status documentation stale | LOW | Yes | ACCEPTED, FIXED | Documentation |

## Finding 1 — snapshot order (integration precondition)

The contract is unchanged and intentional: a future receive timestamp is a caller or snapshot-order violation and is **not** clamped to zero, ignored or marked stale. The existing tests (`test_now_earlier_than_receive_time_rejected`, `test_invalid_inputs_fail_clearly`) were verified, and a fusion-level test was added showing that even an optional environment reading received after `now` raises. **Integration requirement (hardware carry-forward):** a future Pi adapter / runtime must first obtain an immutable, safe snapshot of the readings and only then sample `now_monotonic_s` before calling fusion; it must not sample `now` and then accept a newer reading into the same evaluation snapshot. This is stated in the `fuse.py` docstring.

## Finding 2 — uncalibrated skew (known limitation, not a fixed defect)

Semantics unchanged (deliberately conservative). While `FUSION_MAX_SOURCE_SKEW_S` is `None`, two fresh sources with unequal receive times are never corroborated and the older one is excluded. If radar is newer, the fused `vision_count` is `None` while `raw_vision_count` keeps the vision value, so the fused usable count can alternate between a number and `None` from one evaluation to the next. **`None` means "not usable for fusion", never "zero people"**, and live downstream consumers must not treat it as zero. The skew must be calibrated with the real transport before production or live fusion decisions rely on corroboration. A test documents the alternation and keeps the raw count visible. Classification: KNOWN UNCALIBRATED-INTEGRATION LIMITATION (PENDING HARDWARE calibration).

## Finding 3 — `fallback_basis` ownership (fixed)

Architecture is now: fusion logic decides, `ClassroomState` records, telemetry serialises. New enum `FallbackBasis` (`NEWER_SOURCE`, `FALLBACK_PRECEDENCE`); `ClassroomState.fallback_basis` is set by `fuse_classroom_state` at decision time: R8 with a genuinely newer source gives `NEWER_SOURCE`; an exact timestamp tie under unconfigured skew gives `FALLBACK_PRECEDENCE` (vision kept by precedence, not because it is newer); R1-R7 give `None`; configured equal timestamps give normal corroboration with `None`. Telemetry reads `state.fallback_basis` directly; the duplicate `_fallback_basis()` function (age comparison) was deleted. The R8 branch was restructured into three explicit cases (radar newer, vision newer, tie) with identical behaviour. Tests: the basis is stored; R1-R7 have none; a newer-source R8 stores `NEWER_SOURCE`; an unconfigured tie stores `FALLBACK_PRECEDENCE`; telemetry returns exactly the stored value; changing ages or rule ids in a hand-built valid state does not change telemetry; changing the stored basis changes telemetry with no recomputation; `fusion.telemetry` no longer has `_fallback_basis`.

## Finding 4 — expected temperature sensors (deferred)

No code change, and no invented sensor ids. An empty `temperatures` collection means none were supplied and remains valid. **EXPECTED TEMPERATURE SOURCE PRESENCE CHECK: PENDING REAL HARDWARE CONTRACT.** When the hardware team confirms the expected temperature sensor ids, the adapter / configuration layer should make a missing expected source explicit.

## Finding 5 — invalid `temperatures` argument (fixed)

`fuse_classroom_state` now validates the argument before building the tuple: `None`, a string or bytes object, or a non-iterable (for example `123`) raises `FusionContractError` naming `temperatures`; every element must be a `TemperatureReading`, and the error names the offending index and type (`temperatures[1] must be TemperatureReading, got str`); an empty tuple stays valid; a generator is accepted; an exception raised by a valid iterable itself is not swallowed.

## Finding 6 — canonical identifiers (fixed at the contract boundary)

An audit of every id used by tests and production code (cam, esp, esp-sim, lux-sensor, radar, rh-1, s, sim-1, t, t-a, t-b, t1, temp-a, temp-b, t-c, vision, presence, lux, humidity) showed all already match the rule, so nothing legitimate broke. Rule: `source_id`, `TemperatureReading.sensor_id` and a non-`None` `HumidityReading.sensor_id` (and the expected-id argument of `derive_source_state`) must fully match `[A-Za-z0-9_-]+` (`re.fullmatch`, so a trailing newline is rejected). No escaping scheme was invented: raw hardware labels are not identifiers, and a future Pi adapter maps them to canonical ids. The rule is not applied to display text (`error_reason`, `device_timestamp`), which is tested. Tests cover valid ids (`camera_1`, `radar-1`, `temp1`), and invalid ones (`"temp room"`, leading or trailing space, tab, embedded newline, trailing newline, empty, `.`, `[`, `=`, `,`, non-ASCII), each raising `FusionContractError` that names the field, plus a test that a normal telemetry line is exactly one `key=value` token per field.

## Finding 7 — documentation (fixed)

The closeout section is kept as the historical record; this section, the status page and the status line in CLAUDE.md now state that the independent Antigravity review is completed.

## Mutation checks (temporary; restored in `finally`; hashes compared; per observation #8)

| Mutation | Tests failed |
|---|---|
| A. telemetry re-derives fallback basis from ages | 2 |
| B. `temperatures=None` leaks `TypeError` | 7 |
| C. whitespace identifier accepted | 12 |
| D. unconfigured tie basis becomes `NEWER_SOURCE` | 5 |
| E. vision-newer branch loses its basis | 5 |
| F. string accepted as a temperatures iterable | 2 (survived the first time: the element check also rejected it, only with a different message; the tests now assert the iterable message) |
| G. errors from a valid iterable swallowed | 1 |
| H. trailing-newline id accepted (`match` instead of `fullmatch`) | 9 |

## Clean areas re-verified after the fixes

The AST audit shows no mutable module state, no globals, no mutable defaults (the compiled id pattern is an immutable constant; the only broad `except` is the intentional one in telemetry). The 2,394-combination invariant sweep still reports 0 violations with all eight rules reached. Environment values do not influence occupancy, radar does not change counts, zones are not fabricated, there is no missing-to-zero fallback, and no future-week logic entered Week 6.

## Verification

Targeted (fusion contracts, logic, scenarios, Week 5, ROI, vision): 452 passed. Full suite: **718 passed, 5 skipped, 0 failed** (681 + 37 new; skips unchanged, the openWakeWord block stayed lifted). The 38 / 38 synthetic scenarios still match. Protected hashes: detector `254b2530...`, roi `ddf73251...`, zones `3eaa182a...`, annotate `685b86b3...` unchanged. Changed: `fusion/contracts.py` (`1b57fdfb...` to `83436a1d...`), `fusion/fuse.py` (`b08a260a...` to `7a332064...`), `fusion/telemetry.py` (`06c0da9e...` to `a61f22ce...`). The five frozen Kaggle result files keep their hashes and 2026-10-06 16:02:31 modification time (no inference). Source files are stored with Windows line endings.

## Status after reconciliation

WEEK 6 SOFTWARE MODULE: COMPLETE (unchanged). REAL HARDWARE VALIDATION: PENDING. INDEPENDENT ANTIGRAVITY REVIEW: COMPLETED; reconciliation applied; **post-fix read-only re-review PENDING**. Week 7: NOT STARTED.
