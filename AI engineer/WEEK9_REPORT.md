# VESTA — Week 9 Report: AI ↔ ESP32 Action Loop

Status summary: [WEEK9_STATUS.md](WEEK9_STATUS.md). Interface draft: [docs/contracts/WEEK9_INTERFACE_DECISIONS_DRAFT.md](docs/contracts/WEEK9_INTERFACE_DECISIONS_DRAFT.md).

## 1. Task 1A — interface decision draft (2026-10-17)

**DRAFT READY FOR TEAM REVIEW — NOT TEAM-APPROVED.** The draft covers R1–R14, the decision register D1–D12 (all OPEN), request-id options, command-capability options, a candidate result vocabulary, a response truth table, a simulated test plan (T01–T18) and acceptance gates. `tests/test_week9_interface_draft.py` (4 tests) keeps it marked as a draft with every decision OPEN and checks that its cited facts match the source.

## 2. Task 1B — AI execution contract + SIMULATED action loop (2026-10-17)

**Result: SOFTWARE-VALIDATED / SIMULATED.** Physical hardware: **NOT VALIDATED.** Team decisions D1–D12: **STILL OPEN.**

Approval scope (project owner, this task): a bounded, software-only simulation that may use the Task 1A proposals **provisionally**. That approval does not decide D1–D12 on behalf of the team. No UART / USB framing, GPIO, device mapping, real timeout or live actuator behaviour was chosen or implemented.

### 2.1 Baseline and audit

- Baseline before editing: **3319 passed, 6 skipped, 0 failed** (reproduced). 1191 repository files hashed with binary SHA-256 first (`.venv`, caches, graphify output, transcripts and the observation log excluded).
- Read: CLAUDE.md, WEEK8_REPORT.md / WEEK8_STATUS.md, both contract documents, `intents/contracts.py`, `intents/router.py`, `assistant/runtime.py`, `assistant/output_arbiter.py`, `assistant/state.py`, the test fakes and the runtime demo.

**Discrepancies between the Task 1A draft and what Task 1B implements.** All of these are deliberate and provisional. The draft itself was not edited, because it is under team review.

| # | Draft says | Task 1B does | Why |
|---|---|---|---|
| 1 | success wording "Simulated: the lights are on." | "In the simulation, the classroom lights were turned on." | the wording the project owner specified for this task |
| 2 | ACCEPTED then deadline → the generic TIMEOUT_UNKNOWN reply | a separate reply: "The simulated controller accepted the command, but completion is not confirmed." | the owner-specified ACCEPTED rule; it is still not a completion |
| 3 | origin REAL / SIMULATED / UNKNOWN on results | REAL is **refused** by `ExecutionResult` | no hardware-validated adapter exists; allowing REAL needs a contract change after physical validation |
| 4 | `submitted_at` / `finished_at` timestamps | a logical `sequence` only | simulated time must not look like a latency measurement |
| 5 | discard names STALE / MISMATCHED / LATE | `UNSOLICITED_OR_STALE`, `MISMATCHED_TARGET_ACTION`, `DUPLICATE_REPLY`, `LATE_REPLY`, `MALFORMED_REPLY`, `INVALID_TRANSITION` | finer distinctions needed by the tests |
| 6 | capability-unavailable → "not submitted, or an UNAVAILABLE reply" | a capability refusal has **no result** (`result=None`) and gets a capability reply; `UNAVAILABLE` is used only when the adapter knows the request was never sent | keeps "never submitted by policy" apart from "could not be delivered" |
| 7 | R2: a second request for a busy target is refused | not needed: the loop is synchronous (one command in flight) | a busy state appears only with concurrency |
| 8 | a late reply may update the app status | not implemented | no app interface exists |

### 2.2 Files

New: `actions/__init__.py`, `actions/contracts.py`, `actions/simulated_adapter.py`, `actions/loop.py`, `actions/response_policy.py`, `scripts/week9_simulated_action_demo.py`, `tests/test_week9_action_loop.py`, `WEEK9_STATUS.md`, this report.

Modified (allow-list entries only, each with a comment; no production file): `tests/test_handoff_docs.py` adds `actions` to the known top-level packages and `WEEK9_STATUS.md` / `WEEK9_REPORT.md` to the planned root files; `tests/test_runtime_routing.py` adds the four `actions/` modules and the demo to the reviewed list of `intents` consumers. These guards exist to force a review when a new item appears, and the Week 8 entries were added the same way.

The package is named `actions/`, not `hardware/` as in the CLAUDE.md layout guideline. Ownership and location of the Pi hardware adapter are still decision D11, and this layer contains no hardware code.

### 2.3 Provisional design

```
route_intent (frozen) -> StructuredCommand
  -> ActionLoop.execute
       1. request id already used by this loop?            -> refused (a repeat is a NEW command with a NEW id; no retry)
       2. CommandCapabilityDeclaration.state_of(target, action), downgraded to SUPPORTED_BUT_UNAVAILABLE if the adapter is disconnected
          anything but SUPPORTED_AND_AVAILABLE              -> NOT submitted, result None
       3. adapter.submit: NOT_DELIVERED -> UNAVAILABLE | DELIVERY_UNCERTAIN -> UNKNOWN | DELIVERED -> SUBMITTED, wait
       4. each event before the deadline: parse_reply (untrusted) -> id pending? target + action match? legal transition?
          otherwise DISCARDED and logged; ACCEPTED = progress only
       5. deadline with no terminal reply -> TIMEOUT_UNKNOWN; LINK_LOST -> UNKNOWN
  -> ActionOutcome (invariants: correlated, terminal, submitted iff SUBMITTED event, unsubmitted only UNAVAILABLE)
  -> response_policy.respond -> ResponseDecision(text, confirms_simulated_completion, label SIMULATED)
  -> OutputArbiter (unchanged) -> fake TTS / playback
```

- **`ResultState`.** Progress states: SUBMITTED, ACCEPTED. Terminal states: APPLIED_CONFIRMED, REJECTED, FAILED, UNAVAILABLE, TIMEOUT_UNKNOWN, UNKNOWN. A reply may carry only ACCEPTED, APPLIED_CONFIRMED, REJECTED or FAILED; the loop decides the others. Legal reply transitions are SUBMITTED → {ACCEPTED, APPLIED, REJECTED, FAILED} and ACCEPTED → {APPLIED, FAILED}.
- **`ExecutionResult`.** Frozen and validated at construction: request id (the same 64-character rule as Week 8), target, action, state, origin (REAL refused), adapter identity, sequence, and a reason code (required for every non-success terminal state). The label is always `SIMULATED`, and HARDWARE-VALIDATED is refused.
- **Provenance.** Origin comes from the adapter object. A reply message that carries an `origin` key is malformed.

### 2.4 Command capabilities

`CommandCapabilityDeclaration` is a separate, immutable mapping from (CommandTarget, CommandAction) to `CapabilityState`. It does **not** extend the frozen answering `Capability` enum.

- The default is `UNKNOWN_CAPABILITY`, and nothing is submitted.
- The simulation declares only CLASSROOM_LIGHTS TURN_ON / TURN_OFF as SUPPORTED_AND_AVAILABLE.
- A routed smart-bulb command therefore stays UNKNOWN and is never submitted.
- Routing a command never implies it is controllable.

### 2.5 Request ids

`RequestIdGenerator` produces `c-<12 hex session>-<n>`, for example `c-5eed0000a001-3`.

- The session is 48 random bits from an injectable `token_source` (default `secrets.token_hex`), drawn once per generator.
- The id is not based on the clock (tested: no time import), fits the frozen Week 8 id rule, and is accepted by `IntentRequest`.
- **Residual collision risk:** about N²/2⁴⁹ over N sessions, roughly 1.8 × 10⁻⁷ for 10,000 restarts. This is not a guarantee.
- The production runtime still uses `answer-<n>`. The integration change is in section 4.

### 2.6 Simulated adapter

`SimulatedLightsAdapter` runs on a `FakeClock`. Its replies are scripted with `plan()`, `inject()` and `lose_link()`, and an unscripted submission gets silence.

- Origin is always SIMULATED. It has no side effects and records only the ids it was given.
- AST check: no `actions/` module and no demo imports serial, smbus, GPIO libraries, sounddevice, socket, HTTP, USB, subprocess, asyncio or time.

### 2.7 Timeout and unknown outcomes

- A deadline without a terminal reply gives `TIMEOUT_UNKNOWN`: neither failure nor success.
- A reply after the deadline gives `LATE_REPLY`, which is discarded, even if a misbehaving adapter returns it.
- `DELIVERY_UNCERTAIN` and link loss give `UNKNOWN`.
- Only a request known never to have been sent is `UNAVAILABLE`.
- There is no retry or replay. The loop requires `timeout_s` with no default (D4); the demo's 2.0 s is a simulated test value.

### 2.8 Response policy

| Outcome | Reply | Confirms? |
|---|---|---|
| APPLIED_CONFIRMED, origin SIMULATED, submitted, correlated | "In the simulation, the classroom lights were turned on." / "…turned off." | yes (simulated) |
| TIMEOUT_UNKNOWN after ACCEPTED | "The simulated controller accepted the command, but completion is not confirmed." | no |
| TIMEOUT_UNKNOWN, UNKNOWN, APPLIED with origin UNKNOWN | "I couldn't confirm whether the simulated action completed." | no |
| REJECTED / FAILED | "The simulated controller rejected the command." / "…reported that the command failed." | no |
| UNAVAILABLE (not delivered) | "The simulated action could not be submitted." | no |
| capability SUPPORTED_BUT_UNAVAILABLE / UNSUPPORTED_TARGET / UNKNOWN_CAPABILITY | "…could not be submitted because the controller is unavailable." / "That can't be controlled in this simulated room." / "I can't control that yet." | no |

All texts are fixed, pass `check_text` at import, never echo a reason code or the request, and never say "hardware".

### 2.9 Software demo (`python -m scripts.week9_simulated_action_demo`)

The demo prints the banner "SIMULATED VESTA AI-TO-ACTION LOOP. NO PHYSICAL HARDWARE USED." It uses the real router and the runtime's ONE OutputArbiter. A `VoiceAssistantRuntime` is built with the existing fake wake / VAD / STT / tone TTS / playback doubles from `scripts/vesta_runtime_demo.py`, only to own that arbiter: no audio is fed, and the runtime executes nothing. Fixed session tokens keep the output reproducible.

| Scenario | Result | Reply |
|---|---|---|
| A ON | ACCEPTED → APPLIED_CONFIRMED | simulated "turned on" (confirms) |
| B OFF | APPLIED_CONFIRMED | simulated "turned off" (confirms) |
| C accepted only | TIMEOUT_UNKNOWN after ACCEPTED | "accepted …, completion is not confirmed" |
| D silence | TIMEOUT_UNKNOWN | "couldn't confirm" |
| E capability unavailable | not submitted (adapter call count unchanged) | "could not be submitted because the controller is unavailable" |
| F simulated restart | an old-session reply (UNSOLICITED_OR_STALE) and a wrong-action reply (MISMATCHED_TARGET_ACTION) are discarded → TIMEOUT_UNKNOWN | "couldn't confirm" |
| G duplicate reply | APPLIED_CONFIRMED once; the second reply is DUPLICATE_REPLY, discarded and not spoken | one confirmation |

7 replies spoken through the arbiter (fake playback), 3 simulated confirmations, exit 0. Two runs give byte-identical output (tested).

### 2.10 Tests and mutation

`tests/test_week9_action_loop.py` has **51 tests**:
- T01–T18 from the draft;
- contract immutability and invalid combinations;
- `parse_reply` on malformed input;
- capability validation;
- the timeout having no default;
- invalid transitions and uncertain delivery;
- request-id format, length, session uniqueness, token validation and no clock use;
- transcript / PCM leakage;
- demo determinism;
- Week 8 preservation;
- a misbehaving late adapter.

Mutation campaign: scratch copy outside the repository; each mutant restored and hash-verified, and the scratch files were hash-equal to the repository afterwards.

| Result | Mutants |
|---|---|
| **KILLED by behavioural tests (23/23 non-equivalent)** | M01 ACCEPTED promoted to terminal; M02 accepted-then-timeout spoken as completion; M03 timeout → success; M04 wrong id accepted; M05 wrong target; M06 wrong action; M07 duplicate check removed; M08 finished table never written; M09 unknown capability → available; M10 capability refusal skipped (unavailable submitted); M11 disconnected adapter not downgraded; M12 adapter origin SIMULATED → REAL; M13 REAL refusal removed; M14 policy ignores origin; M17 outcome correlation invariant removed; M18 restart: match on counter only; M19 session token ignored; M20 REJECTED / ACCEPTED after ACCEPTED allowed; M21 reply after deadline accepted; M22 reply may carry SUBMITTED / UNAVAILABLE; M23 link loss → FAILED; M24 uncertain delivery → UNAVAILABLE; M25 duplicate request id allowed |
| Hash-only kills | **0** (no test pins an `actions/` file hash) |
| SURVIVED, equivalent by construction | M15 policy ignores `submitted`, M16 policy ignores correlation: both are second guards behind `ActionOutcome` invariants, which make an uncorrelated or unsubmitted-but-applied outcome impossible to build. Combined with the invariant removed (M15 + the "unsubmitted only UNAVAILABLE" check; M16 + M17), both are killed |
| Canary | comment-only change survived, as expected |

**Counts:**
- 26 single-edit runs = 25 mutants + 1 canary.
- 25 mutants = 23 non-equivalent + 2 equivalent (M15, M16).
- 23 of 23 non-equivalent mutants were killed by behavioural tests, with 0 hash-only kills.
- 2 extra combination mutants (M15 + invariant, M16 + M17) were both killed.

**Response-policy properties killed by a policy-only mutant, so tested on their own:**
- the origin guard (M14);
- ACCEPTED-then-timeout never becoming a completion (M02).

**Policy properties protected only together with the loop or contract:**
- completion only from APPLIED_CONFIRMED (M01, M03);
- capability refusal (M09, M10, M11);
- REAL refused (M12, M13).

**Policy guards that are not independently testable:** `submitted` (M15) and correlation (M16). The `ActionOutcome` invariants make their failing inputs impossible to construct, so they are defence in depth only.

### 2.11 Regression

New tests: 51 passed. **The first full run found 4 failures, all from existing architecture guards:**
- 2 in `tests/test_state_integration.py`: the first demo version constructed its own `OutputArbiter`, which breaks the single-composition-root rule. Fixed in the demo: it now speaks through the runtime's arbiter. The guard is unchanged.
- 1 in `tests/test_handoff_docs.py`: the upload gate rejected the unplanned top-level files.
- 1 in `tests/test_runtime_routing.py`: the `intents` consumer allow-list did not include `actions/`.

The last two were resolved by the reviewed allow-list entries in 2.2. The mutation campaign was then re-run (same result). Final full repository: **3370 passed, 6 skipped, 0 failed** (= the 3319 reference + 51 new).

### 2.12 Protected integrity

Binary SHA-256 of the 1191 pre-existing files before and after: identical except the two allow-list test files in 2.2. The other differences are the 9 new files. Untouched: `assistant/*` (runtime, answering, output_arbiter, state), `intents/*`, `audio/*`, `wakeword/*`, `speech/*`, `fusion/*`, `config/settings.py`, the evaluation corpora, CLAUDE.md, and both contract documents.

### 2.13 Final verification (2026-10-17, independent probe script outside the repository)

**Integrity.** The file count went from 1191 to 1200: 9 created, 2 modified, nothing else changed (binary SHA-256).
- Created: `actions/__init__.py`, `actions/contracts.py`, `actions/loop.py`, `actions/response_policy.py`, `actions/simulated_adapter.py`, `scripts/week9_simulated_action_demo.py`, `tests/test_week9_action_loop.py`, `WEEK9_STATUS.md`, `WEEK9_REPORT.md`.
- Modified (the two architectural allow-list tests):
  1. `tests/test_handoff_docs.py`, `dc931ff2…` → `65151505…`. Added `"actions"` to `KNOWN_TOP` and `WEEK9_STATUS.md` / `WEEK9_REPORT.md` to the planned root files. **Why:** this upload gate exists to force a review of every new top-level item. The new package and the two required status files are planned items, registered the same way Week 8 registered `intents` and its own reports. Nothing forbidden (audio, model, result, log) became allowed.
  2. `tests/test_runtime_routing.py`, `ff321c55…` → `9bbe9372…`. Added the four `actions/` modules and the demo to the reviewed set of `intents` consumers. **Why:** they import only the frozen command vocabulary and the router, never the runtime. The test's purpose, that no unreviewed production module consumes the intent package, is kept: the set is still exact.
- Protected files are byte-identical: `assistant/runtime.py`, `output_arbiter.py`, `state.py`, `answering.py`, `intents/contracts.py`, `intents/router.py`, `config/settings.py`, CLAUDE.md, WEEK7 / WEEK8 / Team Leader status files, and the Task 1A draft.

**Test count.** 3370 − 3319 = 51, and `tests/test_week9_action_loop.py` collects exactly 51 tests.

**Probe results (all pass):**

| # | Check | Result |
|---|---|---|
| 4 | Single arbiter | The demo makes no `OutputArbiter` / `SpokenOutputController` call and imports neither. It uses `arbiter = runtime.arbiter`. Repository-wide AST: `OutputArbiter` is constructed only in `assistant/runtime.py`, also enforced by `tests/test_state_integration.py` |
| 5 | No hardware | No `actions/` module and not the demo imports serial / smbus / GPIO / spidev / usb / socket / HTTP / subprocess / asyncio / sounddevice. Running the action slice alone loads none of them. With every `sounddevice` stream / play entry point booby-trapped, the demo exits 0 and opens **no** stream. **Pre-existing limitation, recorded and not changed:** the demo builds the runtime, whose existing import chain (`assistant/output_arbiter.py` → `assistant/spoken_output.py` → `audio/__init__.py` → `audio/microphone.py`) imports `sounddevice`. That initializes the PortAudio library at import (no device is opened). The standard library loads `socket` / `subprocess` as a side effect of loading it (`platform`, `ctypes.util`). The same is true of every existing runtime test and `scripts/vesta_runtime_demo.py` |
| 6 | Simulated wording | Both completion texts start "In the simulation,". No response text says "hardware" or a plain "the lights are on" / "done" |
| 7 | Acknowledgements | Exhaustive sweep of **2880** reply variants: request id correct / old-session same counter / never issued / other prefix / malformed; × target × action × state (including invalid and loop-only states); × in-time / after the deadline; × with / without an `origin: REAL` key. **Only the single fully correct, in-time reply confirms.** A duplicate gives one confirmation, and the second reply is discarded as `DUPLICATE_REPLY`. An expired reply gives TIMEOUT_UNKNOWN, discarded as `LATE_REPLY` |
| 8 | Unknown capability | An empty declaration, and an undeclared smart bulb: not submitted, no result, the adapter is never called, no confirmation |
| 9 | Request ids | `assistant/runtime.py` does not reference `RequestIdGenerator` or `actions`, and still issues `answer-<n>`. The Week 9 documents describe the generator as **not integrated** |
| 10 | Other statuses | Week 8 COMPLETE — SOFTWARE-VALIDATED, Week 7 IN PROGRESS, Team Leader AI-1 COMPLETE and AI-2 to AI-9 not complete (overall 1 of 9). Unchanged, and their files are hash-identical |

## 3. What is NOT claimed

- No physical device, ESP32, relay or light was used or controlled.
- No result is HARDWARE-VALIDATED.
- No latency is measured, and no real timeout is chosen.
- No team decision is made.
- The production voice runtime is not wired to the loop.
- Zone lighting is not implemented. CLASSROOM_LIGHTS still means all room lights.
- The request-id generator is not used by the production runtime.
- Limitations:
  - one action family only;
  - a synchronous loop (one command in flight);
  - no status query after a timeout;
  - no app notification of late replies;
  - the demo's runtime import initializes PortAudio (pre-existing chain, 2.13).

## 4. Changes needed for runtime integration (written at Task 1B; APPLIED in Task 1C with project-owner approval, see section 6)

1. **Request ids** (`assistant/runtime.py:291`, `:418`): add a constructor parameter `request_ids: Optional[RequestIdGenerator] = None` (default: a new generator). Replace `self._ids = itertools.count(1)` with `self._request_ids = request_ids or RequestIdGenerator()`, and `request_id = f"answer-{next(self._ids)}"` with `request_id = self._request_ids.next_id()`.
   - The id format changes from `answer-N` to `c-<session>-N`.
   - 7 test references to `"answer-` need updating: `tests/test_runtime.py` (1), `tests/test_runtime_routing.py` (5), `tests/test_week9_interface_draft.py` (1, which pins the current counter on purpose).
2. **Command branch** (`assistant/runtime.py:434-435`): add an optional `action_loop: Optional[ActionLoop] = None`.
   - With no loop, behaviour stays exactly as today (`COMMAND_NOT_EXECUTED_TEXT`).
   - With a loop: `outcome = self._action_loop.execute(intent.actionable_command)`, `decision = respond(outcome)`, then `_speak_fixed(decision.text, …)` with a new `OutcomeKind` (for example `COMMAND_SIMULATED`).
3. **`RuntimeOutcome`**: `execution_status` is the fixed string `"NOT_EXECUTED"` and `spoken_confirmation_allowed` is always `False`. Integration needs new fields instead (for example `action_state`, `action_origin`, `confirms_simulated_completion`). The Week 8 `IntentResult` fields stay unchanged, because the intent layer still executes nothing.
4. **Blocking**: `execute()` is synchronous and would block the processing thread for up to the timeout inside the CHECKING state. A real adapter needs an agreed timeout (D4) and probably an asynchronous hand-off. That is a design decision, not a one-line change.
5. **Single arbiter and single state owner, preserved by construction.**
   - The runtime keeps building the ONE `OutputArbiter` (`assistant/runtime.py:289`) and the ONE `AssistantStateManager` (`:288`).
   - The `ActionLoop` is passed in already built, with its adapter, capability declaration, clock and timeout. It receives neither the arbiter nor the state manager.
   - `actions/` must keep importing nothing from `assistant/` (tested), and it never calls `transition()`.
   - The loop runs while the runtime is already in CHECKING (entered by `_ensure_checking()` at `:417`).
   - The reply goes through the existing `_speak_fixed` → `self._arbiter.submit_answer(text, request_id=…)`. SPEAKING and IDLE therefore keep coming only from the arbiter's first-buffer and finish events, and a refused arbitration still settles the state through `_finish_output`.
   - An exception from the loop (a contract bug) settles via `_settle(CAPTURE_CANCELLED, OUTPUT_ERROR)` and propagates, like today's router and answering bugs. It is never spoken.
   - The call-site table in `tests/test_state_integration.py` must stay unchanged: no new arbiter, controller or playback site.
6. **Tests to add with Task 1C:** a command with no loop is unchanged (`COMMAND_NOT_EXECUTED`); with the simulated loop, each of A–G gives the policy text through the arbiter. Also check that the state sequence is LISTENING → CHECKING → SPEAKING → IDLE with no extra transition, and that the runtime outcome never reports a hardware label.

## 5. Recommended next task (written at Task 1B; superseded by section 6.12)

At Task 1B the recommendation was Task 1C: the minimal runtime integration in section 4. It is now done (section 6). The team decisions D2, D3, D4 and D11 are still open.

## 6. Task 1C — SIMULATED action execution inside the voice runtime (2026-10-17)

**Result: COMPLETE — SOFTWARE-VALIDATED / SIMULATED.** Real ESP32 integration: **NOT STARTED.** D1–D12: **OPEN.** No physical voice-command success is claimed.

Approval scope (project owner): minimal, necessary changes to `assistant/runtime.py` for this simulated integration only. No other protected file was changed.

### 6.1 Baseline and audit

- Baseline: **3370 passed, 6 skipped, 0 failed** (reproduced). 1200 files hashed with binary SHA-256 first.
- Audited: `assistant/runtime.py`, `output_arbiter.py` (phase, `submit_answer`, request-id rule, state transitions with the request id), `spoken_output.py`, `state.py`, `intents/*`, `actions/*`, both demos, the runtime test support (`Rig`, gated TTS / playback) and every test that scans or pins the runtime.
- **No blocker was found**: the Task 1B `ActionLoop.execute` / `respond` API is enough.

The audit found three existing guards that the approved edit would touch:
1. `tests/test_runtime_routing.py::test_the_runtime_code_names_no_device_protocol_or_hardware_interface` forbids the token `execute` in runtime code.
2. `tests/test_week8_task5_audit.py` forbids runtime function names starting with `execute` / `dispatch`. Respected: the new method is `_simulate_command`.
3. Four tests pin the old `answer-N` ids.

### 6.2 Files

- **Created:** `scripts/week9_voice_to_action_demo.py` and `tests/test_week9_runtime_actions.py` (56 tests).
- **Modified:**

| File | Change |
|---|---|
| `assistant/runtime.py` (approved) | 10 lines replaced, 62 added; diffed against the hash-verified original |
| `tests/test_runtime.py` | 1 assertion: the `answer-N` pin becomes the `c-<session>-<n>` format with one session and counter 1, 2. **Production ids stay random** |
| `tests/test_runtime_routing.py` | 3 id assertions as above. The `execute` guard narrowed to exactly ONE `self._action_loop.execute(...)` call, so any other use still fails |
| `tests/test_week9_action_loop.py` | T16 now pins the new truth: the runtime imports `actions.contracts` / `actions.loop` / `actions.response_policy`, never the adapter, and still keeps the NOT_EXECUTED notice |
| `tests/test_week9_interface_draft.py` | the request-id fact now checks the generator and the draft's superseded note |
| `docs/contracts/WEEK9_INTERFACE_DECISIONS_DRAFT.md` | a dated "superseded by Task 1C" note in 6.1 and on the fact row. **D3 is still OPEN** |
| `scripts/week9_simulated_action_demo.py` | **line endings only** (CRLF → LF, the repository convention); content byte-identical |
| `WEEK9_REPORT.md`, `WEEK9_STATUS.md` | this section and the status rows |

**Line-ending incident, found and fixed before the final runs.** My scripted patches used a text-mode write, which on Windows turned four files to CRLF: `assistant/runtime.py`, both demos and this report. Observation #14 records exactly this hazard. Raw-byte counts found it; all four were restored to LF. The runtime diff then contains only the intended lines.

### 6.3 Runtime integration design

```
TRANSCRIBED -> route_intent (ONE router, unchanged) -> ROUTED command, EXECUTION_NOT_IMPLEMENTED
   action_loop is None  -> the Week 8 path, unchanged: COMMAND_NOT_EXECUTED + the fixed "can't carry out device commands yet" notice
   action_loop injected -> _simulate_command:
       arbiter not IDLE, or another command already in the loop -> COMMAND_NOT_ADMITTED: not submitted, nothing spoken, state untouched
       otherwise -> ActionLoop.execute(command) inside CHECKING -> actions.response_policy.respond -> _speak_fixed -> the ONE arbiter
```

- The constructor takes `action_loop: Optional[ActionLoop] = None` and `request_ids: Optional[RequestIdGenerator] = None`; both are type-checked.
- **The runtime builds no adapter** (tested: no `SimulatedLightsAdapter`, `FakeClock` or `ActionLoop(` in its source).
- New `OutcomeKind` values: `COMMAND_SIMULATED` and `COMMAND_NOT_ADMITTED`.
- Two `RuntimeOutcome` fields reuse the Task 1B types instead of duplicating the schema:
  - `action: Optional[ActionOutcome]` carries capability, result state, origin, reason and discards;
  - `confirms_simulated_completion: bool` comes from the policy decision.
- Whether the reply was granted and played is the existing `arbitration` / `output`.
- The Week 8 fields keep their meaning: `execution_status` is still `NOT_EXECUTED` (nothing physical is executed) and `spoken_confirmation_allowed` is still `False`.
- The structural log line gained `action_state` and `simulated_confirmation`.

### 6.4 Request ids

- One `RequestIdGenerator` per runtime; a new random session unless one is injected.
- `_respond` issues one id per complete transcript. The same id is used by the `IntentRequest`, the `StructuredCommand`, every `ExecutionResult`, the adapter submission, the arbiter request and the SPEAKING state event (tested end to end).
- The old `itertools` counter was removed, so there is no second counter: questions and commands share one counter.
- Ids contain no request text.
- **Uniqueness across sessions is probabilistic** (about N² / 2⁴⁹ over N sessions), not guaranteed.
- Tests and demos inject fixed tokens; production does not.

### 6.5 Capability gating

The capability check is the Task 1B one, unchanged.
- Declaration says UNKNOWN, UNSUPPORTED or UNAVAILABLE, or the adapter is disconnected → never submitted, with a capability reply.
- The simulation declares only CLASSROOM_LIGHTS TURN_ON / TURN_OFF. The smart bulb stays UNKNOWN even though the router recognises it.

### 6.6 Simulated execution results

- Completion wording ("In the simulation, the classroom lights were turned on / off.") appears only for a correlated, well-formed, in-window, non-duplicate APPLIED_CONFIRMED with origin SIMULATED.
- ACCEPTED-then-timeout gives "accepted … completion is not confirmed".
- TIMEOUT_UNKNOWN, UNKNOWN and an UNKNOWN origin give "couldn't confirm".
- REJECTED, FAILED and UNAVAILABLE are reported as such.
- Origin REAL raises `ActionContractError` (the Task 1B contract refuses it). The runtime settles to IDLE and the error propagates; nothing is spoken. An UNKNOWN origin is never relabelled SIMULATED.

### 6.7 Output and state lifecycle

- There is still exactly one `AssistantStateManager`, one `OutputArbiter` and one `SpokenOutputController`, and the call-site table is unchanged. `actions/` imports nothing from `assistant/` (tested).
- The loop runs inside CHECKING.
- SPEAKING starts only at the playback's first-buffer event: tested by holding the playback before its first buffer, when the state is still CHECKING.
- IDLE follows the existing completion path, and the lifecycle is IDLE → LISTENING → CHECKING → SPEAKING → IDLE.
- No state transition is forced, and there is no playback bypass (`playback.calls` matches the granted replies).

### 6.8 Concurrency and stop — what is enforced and what is a documented limit

| Situation | Behaviour (tested) |
|---|---|
| A command while the speaker is busy | NOT admitted: no submission, nothing spoken, state event unchanged, max concurrent playback 1 |
| Two simultaneous commands | The second is NOT admitted while the first is in the loop. One submission, one confirmation, state still owned by the first |
| Duplicate reply / two acks for one request / late reply after timeout / stale reply after restart / wrong id, target or action / malformed / forged `origin` | Discarded (DUPLICATE_REPLY, INVALID_TRANSITION, LATE_REPLY, UNSOLICITED_OR_STALE, MISMATCHED_TARGET_ACTION, MALFORMED_REPLY). Exactly one audible confirmation per confirmed request. No retry, no replay |
| **LIMIT:** the speaker is taken by another request WHILE the loop waits | The simulated action completes, but its reply is REJECTED by the arbiter (`REJECTED_ACTIVE_OUTPUT`) and is not heard. No overlap |
| **LIMIT:** `stop_output()` during the loop | Returns False (nothing is playing) and cancels neither the loop nor its reply. Safe cancellation needs a new lifecycle contract, which is **not invented here** |
| A loop / adapter bug | Propagates, is never spoken, state settles to IDLE. The runtime recovers and the next command works with the next id |

### 6.9 Timeout and blocking

- The loop waits synchronously inside CHECKING, bounded by its injected timeout. Tests and demos use a FakeClock (2.0 simulated seconds), so they take no real time.
- No real-world latency is measured, and no real timeout is chosen (D4).
- A live capture harness (Stage B) would build an audio backlog while the loop blocks. The Stage B harness was **not modified**, and no live microphone runner exists.

### 6.10 Voice outcome safety and Q1–Q10

- Only TRANSCRIBED reaches the router and the loop. WAKE_ONLY, STT_EMPTY, NO_SPEECH, TOO_SHORT, INVALID_ACTIVATION and STT_FAILED, as well as MAX_DURATION / STREAM_ENDED carrying "turn on the lights", never call the loop (tested).
- These never call the loop either: UNSUPPORTED, AMBIGUOUS, missing target, multiple actions, invalid value, GENERAL_AI with no backend, and unanswerable "is the room dark" / "is anyone in the room".
- **Team Leader Q1–Q10:** every question and accepted variant (FRESH and NONE fixtures) gives identical outcomes and identical spoken text with and without an injected loop. No command is produced and nothing is submitted.

### 6.11 Demo (`python -m scripts.week9_voice_to_action_demo`)

Banner: "SIMULATED VESTA VOICE-TO-ACTION DEMO. / FAKE AUDIO INPUT/OUTPUT. / NO PHYSICAL HARDWARE CONTROL." Fake audio goes through the real `VoicePipeline`, and the runtime's own arbiter and state are used. 10 rows, all OK:
- **A** ON → APPLIED_CONFIRMED, simulated "turned on";
- **B** OFF → simulated "turned off";
- **C** no loop → COMMAND_NOT_EXECUTED with the fixed notice;
- **D** empty declaration → UNKNOWN_CAPABILITY, not submitted;
- **E** accepted only → "accepted … not confirmed";
- **F** silence → "couldn't confirm";
- **G** old-session and wrong-action acks discarded → "couldn't confirm";
- **H** "what can you do" → deterministic answer;
- **I** unsupported and ambiguous → rejected, no loop call.

Main runtime: 5 submissions (A, B, E, F, G), 8 replies, 0 state errors, and the lifecycle repeats IDLE → LISTENING → CHECKING → SPEAKING → IDLE for every utterance. Two runs give identical output (tested).

**PortAudio.** Building the runtime imports `sounddevice` through the pre-existing `audio/` import chain, so the PortAudio library is loaded. **Loading the library is not opening a stream.** With every `sounddevice` stream / play / rec / `query_devices` entry point booby-trapped, the demo exits 0 and calls none of them. No serial / GPIO / smbus / USB / HTTP module is imported. Protected audio files were not changed.

### 6.12 Tests, mutation, regression, integrity

- **New tests:** 56 in `tests/test_week9_runtime_actions.py`.
- **Full suite:** **3426 passed, 6 skipped, 0 failed** = 3370 + 56. Targeted suites together: 1111 passed (Week 8 intents / audit / routing / evaluation, the Task 1B loop, runtime, runtime routing, arbiter, state, state integration, Task 1C, Q1–Q10).
- **Mutation** (scratch copy, each mutant restored and hash-verified, scratch equal to the repository afterwards): **20 mutants + 1 canary.** 19 killed behaviourally, 1 artifact-only, 0 survivors; the canary survived as expected.
  - Behavioural: X01 capability bypass; X02 correlation skipped; X03 ACCEPTED → success; X04 TIMEOUT → success; X05 runtime speaks success without a confirmed result; X06 confirmation flag forced; X08 fixed / stale request id; X09 session token ignored; X10 MAX_DURATION / STREAM_ENDED routed; X11 transcript logged; X12 duplicate check removed; X13 busy speaker ignored; X14 in-flight command ignored; X15 lock never released; X16 no-loop default lost; X17 loop bug not settled; X18 not-admitted path speaks; X19 policy origin guard removed; X20 loop type not validated.
  - Artifact-only: X07, a second, unused arbiter built in the demo, killed only by the call-site guard. An unused arbiter has no observable behaviour; the guard exists for exactly that.
- **Integrity:** 1200 → 1202 files; 2 created, 8 modified (6.2); every protected file byte-identical.

### 6.13 Not claimed / limitations

- No microphone, speaker, ESP32, relay or light was used.
- There is no live voice assistant and no measured latency.
- No result is HARDWARE-VALIDATED.
- The stop / cancellation contract does not exist.
- The speaker-taken-during-wait reply loss is a known limit.
- There is one action family only (all classroom lights), with no zones.
- The request-id format and duplicate rule are PROVISIONAL (D3 open).

### 6.14 Recommended next task

**Week 9 Task 2 (proposed, not started): team interface review.** Present WEEK9_REPORT.md sections 2.1, 6.8 and 6.13 with the Task 1A draft to the Team Leader, Software Engineer and Hardware Engineer. The aim is recorded decisions on D2 (result vocabulary), D3 (ids and duplicates), D4 (timeouts), D11 (adapter ownership) and a cancellation / busy-speaker policy. Only after D1 / D5 and hardware-team firmware exist can a real adapter replace `SimulatedLightsAdapter` behind `ActionAdapter`. It may be called HARDWARE-VALIDATED only after a physical test.
