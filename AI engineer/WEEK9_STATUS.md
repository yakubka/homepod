# VESTA — Week 9 Status (AI ↔ ESP32 Action Loop)

Full detail: [WEEK9_REPORT.md](WEEK9_REPORT.md). Week 8 stays COMPLETE (see [WEEK8_STATUS.md](WEEK8_STATUS.md)); Week 7 real-speech evaluation stays PENDING.

| Item | Status |
|---|---|
| TASK 1A — INTERFACE DECISION DRAFT | **DRAFT READY FOR TEAM REVIEW** ([docs/contracts/WEEK9_INTERFACE_DECISIONS_DRAFT.md](docs/contracts/WEEK9_INTERFACE_DECISIONS_DRAFT.md)). NOT TEAM-APPROVED |
| TASK 1B — AI EXECUTION CONTRACT + SIMULATED ACTION LOOP | **COMPLETE — SOFTWARE-VALIDATED / SIMULATED** (`actions/`, `scripts/week9_simulated_action_demo.py`, `tests/test_week9_action_loop.py`, 51 tests; 25 mutants + 1 canary on scratch copies: 23 killed by behavioural tests, 0 hash-only kills, 2 equivalent survivors (guards made redundant by the outcome invariant), canary survived). One family only: CLASSROOM_CONTROL / TURN_ON, TURN_OFF / CLASSROOM_LIGHTS (all room lights, no zones) |
| TASK 1B — FINAL VERIFICATION | **PASSED** (WEEK9_REPORT.md 2.13). 9 files created, 2 allow-list tests modified (`tests/test_handoff_docs.py`, `tests/test_runtime_routing.py`), all protected files byte-identical. 3370 − 3319 = 51 = the new test file's count. The single runtime arbiter is the only `OutputArbiter`. 2880 adversarial reply variants: only the one fully correct, in-time reply confirms. UNKNOWN capability is never submitted. No audio stream opened. Limitation (pre-existing): importing the runtime initializes PortAudio through `audio/microphone.py` |
| TASK 1B — CONTRACT STATUS | **PROVISIONAL / SIMULATED.** The result states, capability states, request-id format, duplicate rule and timeout rule are AI-side proposals used inside a replaceable simulation layer. They are NOT team decisions |
| PHYSICAL HARDWARE | **NOT VALIDATED.** No ESP32, relay, light, port or transport was used. No light was controlled. Every result has origin SIMULATED; origin REAL is refused by the contract |
| TEAM INTERFACE DECISIONS D1-D12 | **STILL OPEN** (none decided; none assumed) |
| TASK 1C — SIMULATED ACTION EXECUTION IN THE VOICE RUNTIME | **COMPLETE — SOFTWARE-VALIDATED / SIMULATED** (WEEK9_REPORT.md section 6). Approved edit to `assistant/runtime.py` only: an optional injected `action_loop` (default None = the unchanged Week 8 behaviour) and an injected `RequestIdGenerator` (one id across intent, command, action result, arbiter and state). New: `scripts/week9_voice_to_action_demo.py` (scenarios A-I, 10 rows OK) and `tests/test_week9_runtime_actions.py` (56 tests). Mutation: 20 mutants, 19 behavioural kills, 1 artifact-only kill (an unused second arbiter, caught by the call-site guard), 0 survivors, canary survived. Full suite **3426 passed, 6 skipped, 0 failed** |
| TASK 1C — DOCUMENTED LIMITS | Fail closed while the speaker is busy or another command is in flight (not admitted, no queue). `stop_output()` cannot cancel the loop or its reply (no cancellation contract). If the speaker is taken while the loop waits, the simulated reply is rejected unheard. The loop blocks inside CHECKING (a live capture harness would backlog). Importing the runtime loads PortAudio through the pre-existing `audio/` chain; no stream is opened |
| PRODUCTION RUNTIME INTEGRATION | **SIMULATED ONLY.** With no injected loop every command is still returned NOT_EXECUTED with the fixed notice. **WEEK 9 REAL ESP32 INTEGRATION: NOT STARTED** |
| REAL HARDWARE TIMEOUTS | **NOT CHOSEN** (decision D4). The loop has no default timeout; the demo uses a simulated 2.0 s test value |
| WEEK 8 SOFTWARE | **COMPLETE** (unchanged) |
| WEEK 7 REAL EVALUATION | **PENDING** (unchanged) |
| TEAM LEADER AI-1 TO AI-9 | **UNCHANGED** (AI-1 complete; AI-2 to AI-9 not complete) |
| CLAUDE.md STATUS CORRECTION | **NOT APPLIED** (needs project-owner approval; text in WEEK8_REPORT.md 20.11) |
