# VESTA Week 9 — AI ↔ Hardware Interface Decision Record (Task 1A)

**DRAFT — NOT TEAM-APPROVED.**

Prepared 2026-10-17 by the AI Engineer (AI side), for review by the Team Leader, Software Engineer, Hardware Engineer and Audio Hardware Engineer. **No decision in this document has been made by the team.** Every recommendation is a proposal from the AI side. No meeting has taken place, and no teammate has approved, rejected or commented on anything here. The "Team decision" fields are empty on purpose and must be filled in only by the named owner.

This document is **not** a wire protocol, a packet format, a UART / USB / network specification, a firmware design or a statement of what hardware exists. It lists what the AI side needs before it can implement (a) a typed execution-result contract and (b) a **simulated** hardware action loop for one path: classroom lights on / off.

| Tag | Meaning |
|---|---|
| **VERIFIED FACT** | read directly from source in this repository on 2026-10-17 (file and line given) |
| **PROPOSED** | an AI-side proposal; not implemented, not approved |
| **OPEN — TEAM DECISION** | belongs to the named owner; the AI side will not choose it |

Builds on: [WEEK8_COMMAND_SEMANTICS_V1.md](WEEK8_COMMAND_SEMANTICS_V1.md) (sections 3–4, R1–R14) and [WEEK8_REPORT.md](../../WEEK8_REPORT.md) section 20 (20.6 findings, 20.8 R-table, 20.9 D-register). Nothing in those documents is changed or removed here. Every R1–R14 requirement is kept.

---

## 1. Verified baseline (what exists today)

| Fact | Source | Tag |
|---|---|---|
| Six official categories. Command categories: LOCAL_COMMAND, CLASSROOM_CONTROL, SMART_HOME | `intents/contracts.py:46-57` | VERIFIED FACT |
| `CommandTarget` = SPEAKER_OUTPUT, CLASSROOM_LIGHTS, SMART_BULB. `CLASSROOM_LIGHTS` = "all classroom lights; no zone, no geometry, no endpoint" | `intents/contracts.py:94-97` | VERIFIED FACT |
| CLASSROOM_CONTROL / CLASSROOM_LIGHTS supports only TURN_ON and TURN_OFF, with no value | `intents/contracts.py:128-130` | VERIFIED FACT |
| `ExecutionStatus` has one member, `NOT_EXECUTED`, and `IntentResult` refuses any other value | `intents/contracts.py:79-80`, `:297-298` | VERIFIED FACT |
| `spoken_confirmation_allowed` is always `False`, and `requires_acknowledgement` is always `True` | `intents/contracts.py:204-206`, `:340-343` | VERIFIED FACT |
| No execution-result type, executor, hardware adapter or simulator exists anywhere in the repository | WEEK8_REPORT.md 20.6, re-checked: no `hardware/` package | VERIFIED FACT |
| `request_id` format: 1–64 chars `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` | `intents/contracts.py:35` (same regex in `assistant/output_arbiter.py:79`) | VERIFIED FACT |
| The runtime issues ids `answer-1`, `answer-2`, … from `itertools.count(1)`, created per `VoiceAssistantRuntime` instance | `assistant/runtime.py:291`, `:418` | VERIFIED FACT at Task 1A; **superseded by Task 1C** (see 6.1) |
| For a validated command the runtime speaks only *"I understood that request, but I can't carry out device commands yet."* | `assistant/runtime.py:96`, `:434-435` | VERIFIED FACT |
| The answering `Capability` enum covers answers only (PEOPLE_COUNT, TEMPERATURE, HUMIDITY, BRIGHTNESS, LECTURE_INFORMATION, ANNOUNCEMENTS). It has no command capability | `assistant/answering.py:100-106`, `:167-181` | VERIFIED FACT |
| Sensor provenance: `Origin` = REAL / SIMULATED / UNKNOWN, and only an adapter may say REAL | `fusion/contracts.py:46-49` | VERIFIED FACT |
| ESP32 wire protocol: every item UNDEFINED / NOT FOUND / PARTIALLY DEFINED (requirements only) | `WEEK3_REPORT.md` "ESP32 protocol status" | VERIFIED FACT |
| No hardware setting (timeout, port, device) in `config/settings.py` | WEEK8_REPORT.md 20.6 item 3 | VERIFIED FACT |

## 2. Architecture boundary (unchanged)

| Layer | Owns | Does not own |
|---|---|---|
| AI / decision engine (Pi) | intent, validated `StructuredCommand`, capability check, waiting for a result, the truthful response | transport, framing, pins, relays, PWM, timing |
| Pi hardware adapter (Pi; **owner open: D11**) | translating a `StructuredCommand` into the agreed message, correlating replies, enforcing the timeout, reporting a typed result | deciding intent or wording |
| ESP32-S3 firmware (hardware team) | actuator control, reporting what really happened | AI reasoning |

The AI side will only produce and consume structured values. It will not write ESP32 firmware, GPIO code, relay logic or any packet that has not been approved.

## 3. First demonstration path (scope of Week 9 Task 1B)

```
"turn on the lights"
  -> route_intent -> CLASSROOM_CONTROL / TURN_ON / CLASSROOM_LIGHTS  (exists, frozen)
  -> command-capability check                                       (PROPOSED, section 7)
  -> Pi hardware interface: submit                                  (PROPOSED; SIMULATED only in 1B)
  -> typed execution result                                         (PROPOSED, section 8)
  -> response policy -> truthful spoken reply                       (PROPOSED, section 9)
```

- `CLASSROOM_LIGHTS` keeps its **current meaning: all room lights, all-or-nothing.** It is **not** the LEFT / CENTER / RIGHT three-zone implementation, and this draft does not change it.
- **Contract-change request (needs approval, D6):** if the team wants zone commands in Week 9 rather than Week 10, the frozen vocabulary needs (a) a new target or a zone field (for example a `LIGHT_ZONE` target with a zone value LEFT / CENTER / RIGHT / PROJECTOR_AREA), (b) new phrase rules in the router, (c) a new corpus version, and (d) a mapping from each zone to real channels (D5). **This is not done here.** Recommendation (PROPOSED): first prove the all-room path end-to-end, then add zones in Week 10 as one approved, versioned contract change.

## 4. Requirements R1–R14 — coverage and gaps

Owner abbreviations: TL Team Leader, SW Software Engineer, HW Hardware Engineer, AHW Audio Hardware Engineer, AI AI Engineer.

| # | Requirement | Existing AI support (VERIFIED) | Missing | Owner | Decision needed | PROPOSED safe default | Tests needed | Acceptance condition |
|---|---|---|---|---|---|---|---|---|
| R1 | Command identity / correlation | `request_id` carried and cross-checked by `IntentRequest`, `IntentResult`, `StructuredCommand` | Ids are not unique across restarts. No reply type exists | AI proposes; SW + HW agree | D3 | session-scoped id (section 6). Every reply must echo it exactly | stale / mismatched / foreign-session reply is never matched | 0 wrong matches in the simulator suite, including across a simulated restart |
| R2 | Duplicate handling | the router is stateless: same text twice → two commands with separate ids | per-action idempotency rule; behaviour for two near-simultaneous requests to one target | SW + HW | D3 | no automatic retry. One request in flight per target, so a second request for the same target is refused as busy. A repeated delivery of one id must not be applied twice | duplicate id; second request while one is pending | the simulator shows exactly one effect per id; a busy target never gets a silent drop |
| R3 | Bounded timeout | none | timeout values; who enforces them | HW + SW | D4 | the AI side enforces its own deadline whatever the transport does. Value is a config parameter with **no default** until D4 (fail at startup if missing) | delayed reply after the deadline; reply just before it | the AI side always stops waiting at the configured bound |
| R4 | Target identification | three abstract targets, no addresses | the target → real channel mapping | HW | D5 | mapping lives in the Pi adapter config, never in `intents/`. Unmapped target → `UNSUPPORTED_TARGET` (section 7) | every target either resolves or is refused | mapping table signed off by HW |
| R5 | Capability discovery | `CapabilityDeclaration` for answers only | command capabilities | AI proposes; HW supplies facts | D5 (+ section 7) | separate command-capability declaration, default = nothing controllable (UNKNOWN) | absent / unavailable / unknown capability → command refused, never submitted | a router-recognised command for a missing light is never reported as controllable |
| R6 | Invalid-target rejection | unsupported targets rejected before any request | hardware-side "unknown target" result | HW | D2 | `REJECTED` with a reason code from HW | the simulator returns REJECTED for an unknown target | REJECTED is spoken as a refusal, not as a failure or a success |
| R7 | Actual execution acknowledgment | nothing (by design) | the result type; what "applied" means | HW + AI | D2 | success only from an explicit `APPLIED_CONFIRMED` (section 8). What "applied" means (relay commanded, or change observed) is stated per target | no success wording without APPLIED_CONFIRMED | mutation: promoting any other state to success is killed |
| R8 | Failure and unavailable states | rejection replies only | distinct results for rejected, failed, unavailable, unsupported | HW | D2 | REJECTED, FAILED, UNAVAILABLE as separate states, each with one fixed reply | one test per state | each state gives its own reviewed reply |
| R9 | Uncertainty after timeout | none | timeout means unknown; status query | HW + SW | D4 | timeout → `TIMEOUT_UNKNOWN`, spoken "I couldn't confirm that". No status query in Week 9 | timeout; late reply after timeout | a timeout never produces "done" or "failed" |
| R10 | Asynchronous completion | none | two-step accepted → completed flow? | HW + SW | D2 | `ACCEPTED` is non-terminal. Only a later terminal state is spoken | ACCEPTED then APPLIED; ACCEPTED then timeout | "accepted" is never spoken as "done" |
| R11 | Recovery after connection loss | none | link-loss / reconnect rule | SW + HW | D10 | on link loss, every pending request becomes `UNKNOWN`. Nothing is replayed after reconnect | link loss with a request pending; reconnect | no stale request acts or is confirmed after reconnect |
| R12 | No false success confirmation | **property holds today**: no success can be built | keeping it once results exist | AI | — (AI-side safety rule) | response policy (section 9) is the only path to confirmation wording | truth-table tests + mutation | 0 success replies outside APPLIED_CONFIRMED |
| R13 | Real versus simulated | sensors: `Origin`. Commands: none | origin on every execution result | HW + AI | D2 | every result carries origin REAL / SIMULATED / UNKNOWN. Only the real adapter may set REAL | a simulated result is never labelled REAL or HARDWARE-VALIDATED | provenance check in every result test |
| R14 | Sensor identity, unit, validity | fusion contracts carry `sensor_id`, `Unit`, `Usability`, `Origin` | the real ESP32 sensor messages; humidity device UNKNOWN | HW | D9 | no change. The adapter maps into the existing fusion contracts | adapter tests on recorded fixtures (later) | HARDWARE-VALIDATED only after a physical run |

## 5. Decision register D1–D12

Each entry: the question, the options, the AI-side recommendation (**PROPOSED**), and an empty team-decision block. **Priority for Task 1B: D1, D2, D3, D4, D5, D11.** D6–D10 and D12 are recorded here but not needed for the simulated lights path.

### D1 — Transport and framing (owner: SW + HW) — PRIORITY

| Option | Trade-offs |
|---|---|
| UART (GPIO serial) | simple, no USB enumeration. Needs agreed baud rate, framing and an integrity check. Wiring belongs to HW |
| USB CDC serial | one cable, power and data. Device naming and enumeration must be fixed (udev rule) |
| Network (Wi-Fi, e.g. MQTT / HTTP) | decoupled; adds broker / network failure modes and security work |

**PROPOSED (AI side):** no preference on transport. The AI side needs only a request / reply abstraction that carries `request_id`, a typed result, and a deadline. Task 1B is transport-free (simulator), so D1 can be settled in parallel. **AI requirement whichever option is chosen:** the framing must let the adapter tell a corrupted or partial message apart from a valid reply. A corrupted message must never be read as success.

Team decision: _________ Decided by: _________ Date: _________ Evidence / link: _________

### D2 — Execution-result vocabulary (owner: HW + AI proposes) — PRIORITY

Options: (a) the semantic states in section 8 as proposed; (b) a smaller set (OK / ERROR / TIMEOUT); (c) HW-defined codes mapped onto AI states by the adapter.
Trade-off: (b) cannot tell "rejected" from "failed" from "unavailable" (breaks R8), and cannot tell "accepted" from "applied" (breaks R10).
**PROPOSED:** (c) with the section 8 states as the AI-side target. HW keeps its own wire codes, and the adapter maps each one onto exactly one AI state. Any unmapped code becomes `UNKNOWN`, never success. HW to state, per target, what "applied" means: output commanded, or change observed.

Team decision: _________ Decided by: _________ Date: _________ Evidence / link: _________

### D3 — Request-id uniqueness and duplicate behaviour (owner: SW + HW; AI proposes) — PRIORITY

Options and recommendation: section 6. Duplicate rule options: (a) the firmware remembers recent ids and ignores a repeat (idempotent by id); (b) no dedupe on the firmware, and the AI side never resends; (c) both.
**PROPOSED:** (b) for Week 9: the AI side never retries automatically, and a repeated id is a bug. (a) is welcome as defence-in-depth if HW can do it cheaply. TURN_ON / TURN_OFF are naturally idempotent (setting a state), but VOLUME_UP is not (it changes a value). That is why the no-retry rule does not depend on the action.

Team decision: _________ Decided by: _________ Date: _________ Evidence / link: _________

### D4 — Timeout values and the unknown-outcome rule (owner: HW + SW) — PRIORITY

Questions: the expected worst-case time from request to final result for the lights, and whether "accepted" arrives first.
**PROPOSED rule (independent of the value):** after the AI-side deadline the result is `TIMEOUT_UNKNOWN`. A late reply for that id is logged as `LATE_REPLY` and **not** spoken. It may update app status later (D-question to SW). **No timeout value is proposed by the AI side.** The simulator will use test-only values, labelled as such.

Team decision (value per action): _________ Decided by: _________ Date: _________ Evidence / link: _________

### D5 — Mapping commands to real devices (owner: HW) — PRIORITY

Questions: which physical lights `CLASSROOM_LIGHTS` (all-room) switches; whether a single switchable channel exists for Week 9; whether `SMART_BULB` and `SPEAKER_OUTPUT` have any real device in the Week 9 build.
**PROPOSED:** a mapping table owned by HW and stored in the Pi adapter configuration. Any target without a row is `UNSUPPORTED_TARGET` in that build. Until HW provides the table, every target is `UNKNOWN_CAPABILITY`.

Team decision / mapping table: _________ Decided by: _________ Date: _________ Evidence / link: _________

### D6 — All-room versus zone lighting (owner: TL + HW)

Options: keep all-room for Week 9, add zones in Week 10 (versioned contract change, section 3); or add zones now.
**PROPOSED:** all-room in Week 9, zones in Week 10.

Team decision: _________ Decided by: _________ Date: _________

### D7 — Volume step, mute model, UNMUTE restore, percent-to-level (owner: AHW + SW)

Not needed for the lights path. Questions are in WEEK8_COMMAND_SEMANTICS_V1.md 2.1. **No proposal.**

Team decision: _________ Decided by: _________ Date: _________

### D8 — Is the generic smart bulb a real paired device, and which protocol (owner: HW + TL)

Not needed for the lights path. **PROPOSED:** keep it `UNKNOWN_CAPABILITY` until a device exists (CLAUDE.md: one reliable path first).

Team decision: _________ Decided by: _________ Date: _________

### D9 — Sensor-reading messages and the humidity device (owner: HW)

Not needed for the lights path. Must map onto the existing fusion contracts (`sensor_id`, `Unit`, `Usability`, `Origin`).

Team decision: _________ Decided by: _________ Date: _________

### D10 — Link-loss and reconnect behaviour (owner: SW + HW)

**PROPOSED (AI side):** pending requests become `UNKNOWN` on link loss. Nothing is replayed on reconnect. The capability state becomes `SUPPORTED_BUT_UNAVAILABLE` until the link is back. Needed for the simulator tests; the simulated behaviour follows this proposal until D10 is decided.

Team decision: _________ Decided by: _________ Date: _________

### D11 — Ownership and location of the Pi-side hardware adapter (owner: TL) — PRIORITY

Options: (a) SW owns the adapter, and AI owns only the result contract and response policy; (b) AI owns the adapter interface and the simulator, and SW owns the real-transport implementation behind it; (c) shared.
CLAUDE.md records this as "Software Engineer / AI Engineer — to be confirmed by the team lead".
**PROPOSED:** (b). The AI side defines the adapter *interface* and a *simulated* implementation for tests. SW implements the real transport behind the same interface. HW owns firmware. Location (PROPOSED): a new `hardware/` package as named in the CLAUDE.md layout guideline. **It will not be created until TL decides.**

Team decision: _________ Decided by: _________ Date: _________

### D12 — Whether a general-purpose backend is ever approved, and its guard (owner: project owner)

Unrelated to the lights path; recorded for completeness (WEEK8_REPORT.md 20.7). **No proposal.**

Team decision: _________ Decided by: _________ Date: _________

### 5.1 Questions per team member

| To | Questions |
|---|---|
| **Team Leader** | D11: who owns the Pi adapter and where it lives. D6: all-room now and zones in Week 10? May Task 1B start on the PROPOSED defaults (labelled PROVISIONAL), or must D2–D4 be decided first? |
| **Software Engineer** | D1 transport preference. D3: will the real adapter echo `request_id` unchanged? D4: who enforces the deadline on the transport side? D10 link-loss handling. Should a `LATE_REPLY` update the app status? |
| **Hardware Engineer** | D2: which results can the firmware really report (accepted? applied? observed?), and what does "applied" mean for the lights? D3: can the firmware ignore a repeated id? D4: worst-case time to switch the lights. D5: which physical lights exist for Week 9 and how they map to `CLASSROOM_LIGHTS`. R13: can the firmware report its own identity / version so REAL can be justified? |
| **Audio Hardware Engineer** | D7 only (volume step, mute model, UNMUTE restore, percent-to-level). Not blocking for the lights path |

## 6. Request-id correctness

### 6.1 Audit (VERIFIED FACT)

> **Superseded 2026-10-17 (Week 9 Task 1C):** `assistant/runtime.py` now takes every request id from an injected `RequestIdGenerator` (`c-<random session>-<n>`, option D below, PROVISIONAL). The audit below records the runtime as it was at Task 1A. See WEEK9_REPORT.md. The team decision D3 is still OPEN.

- `assistant/runtime.py:291` creates `itertools.count(1)` per runtime instance, and `:418` builds `answer-<n>`.
- So **every process start, and every new `VoiceAssistantRuntime`, starts again at `answer-1`.** Two runtimes in one process would also issue the same ids.
- The router copies the id into `IntentResult` and `StructuredCommand` unchanged (`intents/router.py:90-105`). The arbiter uses the same id for its speech request (`assistant/output_arbiter.py:147-154`).
- Today nothing external consumes the id, so this is harmless. Once a hardware reply is matched by id (R1), an ESP32 reply to `answer-3` from before a restart could be matched to a new, different `answer-3`. That is a false confirmation path.

### 6.2 Options

| Option | Format (fits the 64-char id regex) | For | Against |
|---|---|---|---|
| A. Keep the counter | `answer-<n>` | no change | reused after restart (the defect) |
| B. Persisted counter | `cmd-<n>` from a file | short, ordered | needs writable storage and crash-safe writes; a corrupted or restored file reuses ids |
| C. Wall-clock based | `cmd-<epoch_ms>-<n>` | readable | relies on the Pi clock being right at boot. Without network time, or without the optional RTC battery, the clock can be wrong or go backwards (**HW to confirm the Pi 5 RTC setup**) |
| D. Random session id + counter | `<session>-<n>`, session = 12 hex chars from `secrets` (48 bits), new at each runtime start | no storage, no clock, unique across restarts with negligible collision chance, still ordered inside a session | ids are opaque to a human; the session must be logged at startup |
| E. Full UUID4 per request | 36 chars | no state at all | no ordering inside a session; longer logs |

### 6.3 PROPOSED strategy (option D)

- **Process / session identity:** at runtime start, `session_id = secrets.token_hex(6)`, logged once (`event=runtime_session_start session_id=…`).
- **Request id:** `c-<session_id>-<n>` for commands (n from a per-session counter), e.g. `c-3fa91b0c22de-17` (21 chars, well under 64). The voice / answer id can use the same session prefix. **The format is a proposal; the runtime is not modified in Task 1A.**
- **Reply correlation:** the adapter keeps a table of pending requests: `request_id → (command, deadline)`. A reply is accepted only when its id is in that table **and** its echoed target / action match the pending command. Otherwise it is logged as `MISMATCHED_REPLY` and discarded.
- **Stale acknowledgements:** a reply whose id belongs to another session, has already finished, or has passed its deadline is logged as `STALE_REPLY` / `LATE_REPLY`. It never changes a spoken response and is never counted as a confirmation.
- **Duplicates:** a second reply for an id that already has a terminal state is ignored (logged). A second *request* with an id already in use is refused locally as a bug.
- **Retry versus new command:** **no automatic retry** in Week 9. If the user repeats "turn on the lights", that is a **new command with a new id**, never a resend of the old id. A future retry would reuse the *same* id only if D3 makes the firmware idempotent by id.
- **Crash / restart recovery:** pending requests are **not** persisted. After a restart, any request that was in flight has an unknown outcome. The new session id stops old replies from matching. If the actual light state matters after a restart, it must come from a status query (not in Week 9, R9).

## 7. Command capabilities

The answering `Capability` enum (`assistant/answering.py:100`) describes **what can be answered**, not what can be controlled. Adding command members to it would mix two meanings and would edit a frozen module. **Not proposed.**

### 7.1 Candidate semantic states (names are NOT approved protocol enums)

| Candidate state | Meaning | Command submitted? | Spoken (PROPOSED) |
|---|---|---|---|
| SUPPORTED_AND_AVAILABLE | the build has this target / action, and the device is reachable now | yes | depends on the execution result |
| SUPPORTED_BUT_UNAVAILABLE | the build has it, but it is not reachable now (link down, device offline) | no | "The lights aren't reachable right now." |
| UNSUPPORTED_TARGET | this build has no such device / action (no row in the D5 mapping) | no | "This room doesn't have lights I can control." |
| UNKNOWN_CAPABILITY | no declaration exists (the **default**) | no | "I can't control that yet." |

### 7.2 Options

| Option | Description | Assessment |
|---|---|---|
| A. Extend `assistant.answering.Capability` | add LIGHTS_CONTROLLABLE etc. | mixes answer and control meanings; edits a frozen module. **Rejected** |
| B. Independent command-capability declaration | a new immutable mapping `(CommandTarget, CommandAction) → state`, supplied to the action layer by the Pi adapter (from the HW mapping table, and later from discovery). Default: everything UNKNOWN | no frozen module touched. Reuses the existing `CommandTarget` / `CommandAction` enums by import. **Recommended (PROPOSED)** |
| C. Ask the hardware before each command | live discovery request per command | adds latency and a second failure mode per command. A possible later refinement of B (R5), not needed for Week 9 |

**Where the check runs (PROPOSED):** after the router (which stays pure and frozen) and before anything is submitted. A command the router recognised but that is not `SUPPORTED_AND_AVAILABLE` is **never submitted and never confirmed.** A missing physical light is never treated as controllable just because "turn on the lights" was routed.

## 8. Proposed execution-result vocabulary (DRAFT — candidate semantics, NOT a packet structure)

### 8.1 States

| Candidate state | Terminal? | Meaning | What it does **not** mean |
|---|---|---|---|
| SUBMITTED | no | the AI side handed the command to the Pi adapter (AI-internal) | that anything reached the ESP32 |
| ACCEPTED | no | the hardware received a well-formed, permitted request | **that it was executed** |
| APPLIED_CONFIRMED | yes | the hardware reports the action was applied (meaning per target, D2) | anything beyond what HW defines "applied" to mean |
| REJECTED | yes | the hardware refused the request (unknown target, not permitted, invalid), with a reason; nothing was attempted | an actuator fault |
| FAILED | yes | the hardware attempted the action and reports it did not take effect (actuator failure) | an unknown state |
| UNAVAILABLE | yes | the request could not be delivered (link down / device absent), **known not delivered** | that the device is broken |
| TIMEOUT_UNKNOWN | yes | no terminal reply within the deadline; the physical result is **unknown** | success, and also failure |
| UNKNOWN | yes | the final outcome cannot be established (link lost after sending, unmapped HW code, corrupted reply) | success, and also failure |

Transitions (PROPOSED): `SUBMITTED → ACCEPTED → {APPLIED_CONFIRMED | FAILED}`; `SUBMITTED → {REJECTED | UNAVAILABLE | APPLIED_CONFIRMED}` (one-step replies allowed); any non-terminal state → `TIMEOUT_UNKNOWN` at the deadline, or `UNKNOWN` on link loss. A terminal state never changes. Later replies are logged only (section 6.3).

`UNAVAILABLE` is used only when the adapter **knows** the request was not sent. If sending may have happened, the result is `UNKNOWN`.

### 8.2 Fields a result needs (semantic, not a wire format)

| Field | Purpose |
|---|---|
| `request_id` | correlation with the `StructuredCommand` (R1) |
| `target`, `action` | echo of the command, cross-checked on receipt |
| `state` | one of section 8.1 |
| `reason_code` | HW-defined code, carried as data and never spoken verbatim (R8) |
| `origin` | REAL / SIMULATED / UNKNOWN (same meaning as `fusion.contracts.Origin`). REAL only from the real adapter (R13) |
| `submitted_at`, `finished_at` | monotonic timestamps for latency logging (CLAUDE.md "Latency measurement") |
| `source` | which adapter produced it (for example `simulator` or `esp32-adapter`), and firmware version when HW provides one |

Provenance rule (PROPOSED): origin is set by the adapter that produced the result, never by the AI layer. A result with `origin = SIMULATED` can only lead to a SIMULATED verification label. An `UNKNOWN` origin is treated as **not confirmed**.

## 9. Response-policy truth table (PROPOSED — not implemented; `OutputArbiter` and runtime unchanged)

| Result state | Origin | Success confirmation eligible? | Spoken reply (PROPOSED wording, to be reviewed) | Verification label |
|---|---|---|---|---|
| APPLIED_CONFIRMED | REAL | **yes** | "The lights are on." / "The lights are off." | HARDWARE-VALIDATED only when observed in a physical test |
| APPLIED_CONFIRMED | SIMULATED | yes, **marked as simulated** (open question for TL: may a simulated demo speak plain confirmation?) | "Simulated: the lights are on." | SIMULATED — **never** HARDWARE-VALIDATED |
| APPLIED_CONFIRMED | UNKNOWN | **no** | "I sent the request but couldn't confirm it." | — |
| ACCEPTED (pending) | any | **no** (not a completed action) | nothing yet. If the deadline passes, the TIMEOUT_UNKNOWN reply | — |
| REJECTED | any | no | "I couldn't do that: the lights refused the request." | — |
| FAILED | any | no | "I tried, but the lights didn't switch." | — |
| UNAVAILABLE | any | no | "The lights aren't reachable right now." | — |
| TIMEOUT_UNKNOWN | any | no | "I couldn't confirm whether the lights changed." | — |
| UNKNOWN | any | no | "I couldn't confirm whether the lights changed." | — |
| capability not SUPPORTED_AND_AVAILABLE | — | no (never submitted) | section 7.1 wording | — |
| no executor configured (today) | — | no | existing notice: "I understood that request, but I can't carry out device commands yet." | SOFTWARE-VALIDATED (current) |

Invariants (PROPOSED, to be tested): (1) confirmation wording is reachable only from `APPLIED_CONFIRMED` with origin REAL or SIMULATED; (2) no reply echoes the HW reason code or the request text; (3) every reply passes `speech.tts.check_text` at import, as the current fixed replies do (`assistant/runtime.py:102-103`).

## 10. Simulated test plan (for Task 1B — none written or run in Task 1A; no hardware)

All tests use a scripted **fake adapter** (origin SIMULATED) with an injected clock. No test opens a port, socket or device.

| # | Scenario | Script | Expected | R |
|---|---|---|---|---|
| T01 | lights ON success | ACCEPTED → APPLIED_CONFIRMED | "Simulated: the lights are on."; origin SIMULATED | R7, R12, R13 |
| T02 | lights OFF success | APPLIED_CONFIRMED (one step) | off confirmation, simulated | R7 |
| T03 | device unavailable | capability SUPPORTED_BUT_UNAVAILABLE / adapter UNAVAILABLE | not submitted, or UNAVAILABLE reply; no success | R5, R8 |
| T04 | rejected target | REJECTED(reason) | refusal reply; reason not spoken | R6, R8 |
| T05 | command failure | ACCEPTED → FAILED | failure reply | R8 |
| T06 | delayed acknowledgement | APPLIED_CONFIRMED just before the deadline | confirmed | R3 |
| T07 | delayed past deadline | reply after the deadline | TIMEOUT_UNKNOWN reply; late reply logged, not spoken | R3, R9 |
| T08 | duplicate reply | APPLIED_CONFIRMED twice for one id | second ignored and logged; one response | R2 |
| T09 | duplicate request id | submit the same id twice | second refused locally | R1, R2 |
| T10 | stale / mismatched id | reply with an unknown id, or a wrong target for a known id | discarded as STALE / MISMATCHED; pending request still times out honestly | R1 |
| T11 | timeout with unknown outcome | no reply | TIMEOUT_UNKNOWN; neither "done" nor "failed" | R9 |
| T12 | lost connection | link drops with a request pending | UNKNOWN; capability → UNAVAILABLE; nothing replayed | R11 |
| T13 | recovery after restart | new session; old-session reply arrives | old reply never matched; new session ids differ | R1, R11 |
| T14 | no false success | every state × origin from section 9 | confirmation only in the eligible rows; mutation campaign on the policy | R12 |
| T15 | provenance labels | SIMULATED result | never labelled REAL or HARDWARE-VALIDATED | R13 |
| T16 | absence of a real executor | default runtime, no adapter | current notice unchanged; no transport / serial / socket import in the action layer (AST check) | R12 |
| T17 | capability default | no declaration | UNKNOWN_CAPABILITY; nothing submitted | R5 |
| T18 | unmapped HW code | the adapter receives a code with no mapping | UNKNOWN, never success | R7, D2 |

## 11. Software acceptance gates for Task 1B

| Gate | Condition |
|---|---|
| A1 Decisions | D2, D3, D4 and D11 recorded in section 5 by their owners, **or** the Team Leader explicitly allows Task 1B to proceed on the PROPOSED defaults labelled PROVISIONAL |
| A2 Frozen modules | `intents/`, `assistant/`, `speech/`, `audio/`, `wakeword/`, `fusion/`, `config/settings.py` and the evaluation corpora unchanged, unless a separate approval names the change (for example the runtime request-id fix in section 6) |
| A3 Tests | T01–T18 pass; the full regression passes against the reference (3315 passed, 6 skipped, 0 failed before Task 1B) plus the new tests |
| A4 Safety mutation | mutants that promote ACCEPTED / TIMEOUT_UNKNOWN / UNKNOWN / UNAVAILABLE to success, drop the id check, or relabel SIMULATED as REAL are all killed |
| A5 Labels | every Task 1B result is labelled SOFTWARE-VALIDATED / SIMULATED. Nothing is called HARDWARE-VALIDATED or "working on the device" |
| A6 No transport | no serial, socket, HTTP or GPIO import in the new code. No packet syntax invented |

## 12. Team decisions (to be filled in by the owners only)

| Decision | Status | Recorded by | Date |
|---|---|---|---|
| D1 | OPEN | | |
| D2 | OPEN | | |
| D3 | OPEN | | |
| D4 | OPEN | | |
| D5 | OPEN | | |
| D6 | OPEN | | |
| D7 | OPEN | | |
| D8 | OPEN | | |
| D9 | OPEN | | |
| D10 | OPEN | | |
| D11 | OPEN | | |
| D12 | OPEN | | |

**Status of this document: DRAFT — NOT TEAM-APPROVED. Week 9 execution-result implementation: NOT STARTED. Week 9 hardware validation: NOT STARTED.**
