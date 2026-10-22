# VESTA — Week 2 Status (FROZEN 2026-09-25)

Legend: **COMPLETE** = implemented and tested as described. **PENDING** = not done.
Full detail: [WEEK2_REPORT.md](WEEK2_REPORT.md).

| Area | Component | Status | Note |
|---|---|---|---|
| WP1 | Audio acquisition software | COMPLETE | Unchanged in Week 2 |
| WP1 | Hardware validation (Raspberry Pi 5 + ReSpeaker XVF3800) | **PENDING** | Hardware not available |
| WP2.1 | WAV file source (`WavFileSource`) | COMPLETE | Fast and real-time modes, sample-counter timing, explicit EOF |
| WP2.1 | Reframer | COMPLETE | Exact sample preservation tested |
| WP2.2 | `WakeWordDetector` abstraction | COMPLETE | Engine-independent |
| WP2.2 | Threshold and cooldown handling | COMPLETE | In the base class, shared by all engines |
| WP2.2 | `Detection` record and `run_on_source()` | COMPLETE | |
| WP2.3 | openWakeWord ONNX backend | COMPLETE | Lazy import; `hey_jarvis` development model |
| WP2.3 | Model file loading and checks | COMPLETE | Never auto-downloads |
| WP2.3 | Real openWakeWord inference executed | COMPLETE | Windows dev PC only |
| WP2.3 | Real positive test (1 recording) | COMPLETE | Smoke test only — **not an accuracy result** |
| WP2.3 | Synthetic negative tests | COMPLETE | Sanity checks; not a false-activation rate |
| WP2.3 | Raspberry Pi validation of the wake-word chain | **PENDING** | |
| WP2.3 | Live ReSpeaker microphone → detector run | **PENDING** | |
| WP2.4 | Evaluation protocol document | COMPLETE | |
| WP2.4 | Manifest schema and validator | COMPLETE | |
| WP2.4 | Ingest / annotate / validate tool | COMPLETE | Validated on synthetic audio and one real clip |
| WP2.4 | Corpus directory structure (empty) | COMPLETE | Outside the repository |
| WP2.4 | `.gitignore` protection for recordings | COMPLETE | |
| WP2.4 | Consent record for the pilot speaker | **PENDING** | Required before any ingestion |
| WP2.4 | Pilot clip ingestion | **PENDING** | Blocked on the consent record |
| WP2.4 | Annotation verification | **PENDING** | No annotations exist |
| WP2.4 | Positive recordings (target 120 clips) | **PENDING** | No participant data collected |
| WP2.4 | Negative corpus (target about 12 h, incremental) | **PENDING** | |
| WP2.5 | Benchmark harness | **PENDING** | Not started |
| WP2.5 | Threshold sweep and selection | **PENDING** | Defaults only (0.5 / 2 s) |
| WP2.5 | Detection / miss / false-activation metrics | **PENDING** | |
| WP2.5 | Consistent latency measurement | **PENDING** | |
| WP2.5 | CPU / memory measurement on the Pi | **PENDING** | Windows timings are development-only |
| WP2.5 | Review of the proposed 0.5 false-accepts/hour target | **PENDING** | Proposed, not frozen |
| Housekeeping | Add `openwakeword` / `onnxruntime` to `requirements.txt` | **PENDING** | Deferred to Pi setup |

**Last verified test result (2026-09-25):** 215 passed, 3 skipped, 0 failed with the recorded clip supplied; 214 passed, 4 skipped, 0 failed with the plain command. The skips are the WP1 hardware tests (and, in the plain run, the recorded-clip test).

**Hardware validation is not complete.** No wake-word accuracy, detection rate, or false-activation rate has been measured.
