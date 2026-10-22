# VESTA Week 8 — Structured Command Semantics (v1)

**Status: APPLICATION-LEVEL SEMANTIC CONTRACT. SOFTWARE-VALIDATED. NOT HARDWARE-VALIDATED.**
Written 2026-10-17 (Week 8 Task 5). Owner of the AI side: AI Engineer. Reviewers needed: Software Engineer, Hardware Engineer, Team Leader.

This document is **not** an ESP32 wire protocol, a UART packet, an HTTP endpoint, a completed hardware integration or an executable device controller. It says what a recognised voice command *means* to the AI layer, what is implemented today, and what the AI layer needs from the hardware side before any command can be executed or confirmed. Every statement is labelled with one of three tags:

| Tag | Meaning |
|---|---|
| **IMPLEMENTED CONTRACT** | exists in `intents/contracts.py`, `intents/router.py` or `assistant/runtime.py` and is covered by automated tests (SOFTWARE-VALIDATED) |
| **PROPOSED FUTURE INTERFACE** | what the AI side needs; not implemented, no executable behaviour |
| **AWAITING TEAM APPROVAL** | a decision that belongs to the Hardware / Software teams or the Team Leader; the AI side will not choose it |

Architecture rule (CLAUDE.md): the Raspberry Pi AI layer *proposes structured commands*; the Pi hardware interface and the ESP32-S3 firmware (hardware team) own transport and physical control. The AI layer never holds pins, relays, PWM, bus timing or a network endpoint.

---

## 1. Implemented contract — vocabulary

All values below are checked against the source by `tests/test_week8_task5_audit.py` (this section cannot drift silently).

```text
ENUM IntentCategory = LOCAL_COMMAND | SENSOR_QUERY | OCCUPANCY_QUERY | CLASSROOM_CONTROL | SMART_HOME | GENERAL_AI
ENUM RoutingStatus = ROUTED | INVALID_COMMAND | UNSUPPORTED | AMBIGUOUS | INVALID_INPUT
ENUM Handling = NONE | ANSWERING_FOUNDATION | ANSWER_NOT_IMPLEMENTED | NO_BACKEND_APPROVED | EXECUTION_NOT_IMPLEMENTED
ENUM ExecutionStatus = NOT_EXECUTED
ENUM CommandAction = VOLUME_UP | VOLUME_DOWN | MUTE | UNMUTE | SET_VOLUME | TURN_ON | TURN_OFF | SET_BRIGHTNESS
ENUM CommandTarget = SPEAKER_OUTPUT | CLASSROOM_LIGHTS | SMART_BULB
ENUM CommandUnit = PERCENT
ENUM CommandValidationStatus = VALID | MALFORMED | CATEGORY_MISMATCH | UNSUPPORTED_TARGET | UNSUPPORTED_ACTION | UNEXPECTED_VALUE | MISSING_VALUE | MISSING_UNIT | INCORRECT_UNIT | INVALID_VALUE | VALUE_OUT_OF_RANGE | UNSUPPORTED_PARAMETER | INVALID_REQUEST_ID
```

There are exactly **six** official intent categories. `UNSUPPORTED`, `AMBIGUOUS`, `INVALID_COMMAND` and `INVALID_INPUT` are routing *outcomes*, never categories (only `INVALID_COMMAND` names the command category it recognised).

### 1.1 `StructuredCommand` (frozen dataclass, validated at construction — it cannot exist invalid)

| Field | Type | Rule |
|---|---|---|
| `intent_category` | `IntentCategory` | one of the three command categories: LOCAL_COMMAND, CLASSROOM_CONTROL, SMART_HOME |
| `action` | `CommandAction` | must be supported for the (category, target) pair |
| `target` | `CommandTarget` | must belong to the category |
| `request_id` | `str` | 1–64 characters of letters, digits `.` `_` `-`, starting with a letter or digit |
| `value` | `Optional[int]` | an integer (not bool) in the action's inclusive range, or `None` when the action takes no value |
| `unit` | `Optional[CommandUnit]` | `PERCENT` when a value is required, otherwise `None` |
| `parameters` | `Mapping[str, str]` | **must be empty** in v1 (no free-form parameters are accepted) |
| `validation_status` | `CommandValidationStatus` | always `VALID` (an invalid command is a `CommandValidationResult`, not a command) |

Derived: `requires_acknowledgement` is always `True`.

### 1.2 `IntentResult` (what the router returns; never contains the request text)

`request_id`, `routing_status`, `reason_code`, `rule_id` (a stable rule id, never words), `category`, `command`, `query_family`, `handling`, `execution_status`, `validation_label`. Invariants enforced at construction: a ROUTED command category carries its own validated command and handling `EXECUTION_NOT_IMPLEMENTED`; a rejection carries no category (except `INVALID_COMMAND`), no command and handling `NONE`; `execution_status` is always `NOT_EXECUTED`; `spoken_confirmation_allowed` is always `False`; there is no execution-result type.

### 1.3 Supported command table (the *whole* table; anything else cannot be constructed)

| Category | Target | Action | Value rule |
|---|---|---|---|
| LOCAL_COMMAND | SPEAKER_OUTPUT | VOLUME_UP, VOLUME_DOWN, MUTE, UNMUTE | no value, no unit |
| LOCAL_COMMAND | SPEAKER_OUTPUT | SET_VOLUME | integer 0–100 inclusive, unit PERCENT |
| CLASSROOM_CONTROL | CLASSROOM_LIGHTS | TURN_ON, TURN_OFF | no value, no unit |
| SMART_HOME | SMART_BULB | TURN_ON, TURN_OFF | no value, no unit |
| SMART_HOME | SMART_BULB | SET_BRIGHTNESS | integer 0–100 inclusive, unit PERCENT |

---

## 2. Implemented contract — meaning of each supported command

Recognition is deterministic phrase matching on normalised text (lower case, punctuation removed, `%` read as "percent", a minus sign before a digit read as "minus"; at most one leading "please / can you / could you / would you" and one trailing "please"). Text longer than 200 characters is `INVALID_INPUT`. There is no fuzzy matching and no model.

### 2.1 LOCAL_COMMAND on SPEAKER_OUTPUT

| Action | Accepted phrasings (exact) | Arguments | Target resolution still needed? | Result when no executor exists |
|---|---|---|---|---|
| VOLUME_UP | volume up; turn the volume up; turn up the volume; increase the volume; raise the volume; turn the sound up; turn up the sound | none | **No device address is carried**; *how much* louder is **AWAITING TEAM APPROVAL** (no step size is defined) | validated command returned, `NOT_EXECUTED` |
| VOLUME_DOWN | volume down; turn the volume down; turn down the volume; decrease the volume; lower the volume; reduce the volume; turn the sound down; turn down the sound | none | as above | `NOT_EXECUTED` |
| MUTE | mute; mute the speaker; mute the volume | none | whether mute is a state flag or volume 0 is **AWAITING TEAM APPROVAL** | `NOT_EXECUTED` |
| UNMUTE | unmute; unmute the speaker | none | what volume is restored is **AWAITING TEAM APPROVAL** | `NOT_EXECUTED` |
| SET_VOLUME | set the volume to N percent; set volume to N percent; set the speaker volume to N percent; set speaker volume to N percent (`N%` also accepted) | `value` = integer 0–100, `unit` = PERCENT | the mapping from percent to the real amplifier / player level is **AWAITING TEAM APPROVAL** | `NOT_EXECUTED` |

Rejected with no command (`INVALID_COMMAND`, category LOCAL_COMMAND): value over 100 (`VALUE_OUT_OF_RANGE`); a negative number, a decimal, number words ("fifty"), commas, more than four digits, extra words after the number (`INVALID_VALUE`); a missing number (`MISSING_VALUE`); a number without "percent" (`MISSING_UNIT`); any other unit (`INCORRECT_UNIT`). `set the speaker to 50 percent` (no "volume"), `turn the sound off` and `turn the sound up a lot` are `UNSUPPORTED`.

### 2.2 CLASSROOM_CONTROL on CLASSROOM_LIGHTS

| Action | Accepted phrasings | Arguments | Target resolution still needed? | Result when no executor exists |
|---|---|---|---|---|
| TURN_ON / TURN_OFF | turn on / off the lights; turn on / off the classroom lights; turn the lights on / off; switch (the) lights on / off; lights on / off | none | **Yes.** `CLASSROOM_LIGHTS` means "all classroom lights": no zone, no geometry, no fixture, no relay, no endpoint. Which physical channel(s) this selects is **AWAITING TEAM APPROVAL** (hardware team) | validated command returned, `NOT_EXECUTED` |

Not supported (no command): setting classroom-light brightness (`UNSUPPORTED`, reason `UNSUPPORTED_ACTION`); a named object that is not the lights (`UNSUPPORTED_TARGET`); an ambiguous object — "the light", "it", "that", "this", "them", "light" (`AMBIGUOUS`); `turn on` with no object (`AMBIGUOUS`, `MISSING_TARGET`).

### 2.3 SMART_HOME on SMART_BULB

| Action | Accepted phrasings | Arguments | Target resolution still needed? | Result when no executor exists |
|---|---|---|---|---|
| TURN_ON / TURN_OFF | turn on / off the smart bulb (or smart light); turn the smart bulb on / off | none | **Yes.** `SMART_BULB` is the *generic development smart bulb*. It does **not** imply a paired, discovered or reachable device, a named bulb or a protocol. Which bulb (if any) is addressed is **AWAITING TEAM APPROVAL** | validated command returned, `NOT_EXECUTED` |
| SET_BRIGHTNESS | set the smart bulb to N percent; set the smart light brightness to N percent (`N%` also accepted) | `value` = integer 0–100, `unit` = PERCENT | what "percent" means for a given bulb is **AWAITING TEAM APPROVAL** | `NOT_EXECUTED` |

### 2.4 Rejections and ambiguity (all IMPLEMENTED)

| Situation | Outcome | Command produced? |
|---|---|---|
| unknown or unsupported request | `UNSUPPORTED` | no |
| unsupported target ("turn on the projector", "set the fan to 50 percent") | `UNSUPPORTED` | no |
| ambiguous object / missing target / two rules disagree | `AMBIGUOUS` | no |
| recognised command with a bad argument | `INVALID_COMMAND` | no |
| empty, non-text or over-long input | `INVALID_INPUT` | no |
| **several actions in one request** ("turn the sound up and turn the sound down", "mute and unmute", "turn the lights on and off") or a repeated command | `UNSUPPORTED` or `AMBIGUOUS` — never a command, never "the first one wins" | no |
| a causal or state question about the actual professor's arrival or this class's attendance ("explain why the professor has not arrived") | `UNSUPPORTED`, reason `CLASS_EVENT_NOT_ROUTABLE_TO_GENERAL_AI` | no |

### 2.5 What the runtime does with a validated command (IMPLEMENTED; `assistant/runtime.py`)

The command is **returned** in the outcome with `execution_status = NOT_EXECUTED` and `spoken_confirmation_allowed = False`. The only thing spoken is the fixed notice *"I understood that request, but I can't carry out device commands yet."* — it says the opposite of success. Nothing is sent to any device, because no executor, hardware adapter or simulator exists. A rejection keeps its exact routing status and gets a fixed reply that does not echo the request.

---

## 3. Command life cycle

| Stage | Meaning | Status today |
|---|---|---|
| A. Request recognised | complete speech transcript received (wake word, VAD and STT done) | **IMPLEMENTED** (software; real-microphone path NOT VALIDATED) |
| B. Intent classified | `route_intent` assigns one of the six categories or a rejection outcome | **IMPLEMENTED** |
| C. Command validated | a `StructuredCommand` exists, in the table above, with valid arguments | **IMPLEMENTED** — **this is where Week 8 ends** |
| D. Execution requested | a validated command is handed to the Pi hardware interface | **NOT IMPLEMENTED** (Week 9) |
| E. Hardware acknowledges or fails | the ESP32 side reports applied / rejected / failed / unreachable / unknown | **NOT IMPLEMENTED** (Week 9; no result type exists) |
| F. Response generated | the spoken or app response states what *really* happened | **NOT IMPLEMENTED** beyond the stage-C notice |

**Spoken-response rule.** At stage C the assistant may say only that the request was understood and cannot yet be carried out. It must not say "the lights are on", "volume set", "done" or anything equivalent. A confirmation of an action becomes allowed only at stage F, only from a real stage-E result, and only after a contract exists. No fake execution-result object is added to make this diagram look complete: `ExecutionStatus` has exactly one member.

---

## 4. Proposed future interface — requirements the AI side needs (PROPOSED FUTURE INTERFACE — AWAITING TEAM APPROVAL)

These are *requirements*, not a design. **Transport, wire format, framing, timing values and error codes are OPEN** and are decided by the Software and Hardware teams. No pin, baud rate, GPIO command, address or network endpoint is proposed or assigned here. ESP32 firmware owns physical control.

| # | Requirement | Why the AI side needs it |
|---|---|---|
| R1 | **Command identity / correlation.** Every request and every reply carries an identifier the AI side can match (the AI side already generates `request_id`) | a late or repeated reply must not confirm the wrong action |
| R2 | **Duplicate handling.** The interface must define what happens when the same request is delivered twice (idempotent apply vs reject) and when two different requests target one device at nearly the same time | a retry after a timeout must not double-apply or silently drop |
| R3 | **Bounded timeout.** Every request has a defined upper bound after which the AI side stops waiting | a voice interaction must fail cleanly, not hang |
| R4 | **Target identification.** A way to map `CLASSROOM_LIGHTS`, `SMART_BULB` and `SPEAKER_OUTPUT` to real channels / devices, owned by the hardware side | the AI vocabulary carries no addresses |
| R5 | **Capability discovery.** The AI side can learn which targets and actions actually exist in this build | it must not offer or confirm something the build cannot do (no fabricated capability) |
| R6 | **Invalid-target rejection.** The hardware side rejects an unknown or unavailable target explicitly | distinguishes "no such light" from "light failed" |
| R7 | **Actual execution acknowledgment.** A positive result is sent only after the actuator change is applied (or observed, where the hardware allows) | the only basis for a spoken success |
| R8 | **Failure and unavailable states.** Distinct, explicit results for: rejected, failed, device unavailable, and not supported | each needs a different, honest spoken reply |
| R9 | **Uncertainty after timeout.** A timeout means *unknown*, not *failed* and not *done*; the interface should allow a later status query | the assistant must say "I couldn't confirm" instead of guessing |
| R10 | **Asynchronous completion, if needed.** If an action takes time, the interface says "accepted" and later "completed" as two separate facts | "accepted" must never be spoken as "done" |
| R11 | **Recovery after connection loss.** Behaviour on link loss and reconnect is defined (pending requests are failed or re-queried, never assumed) | no stale request may act after a reconnect without being re-confirmed |
| R12 | **Prevention of false success confirmations.** Success is derived only from an R7 result; absence of an error, a transmitted packet or a timeout never counts | the central safety property of the loop |
| R13 | **Real versus simulated.** A way to tell a real device from a simulator in every result | keeps SOFTWARE-VALIDATED, SIMULATED and HARDWARE-VALIDATED results distinguishable |
| R14 | **Sensor values carry sensor identity, unit and a validity flag** (already required by CLAUDE.md) | missing or failed sensors are reported, never replaced |

Open decisions (not for the AI side to make): the transport (UART / USB / network); the message format; the timeout values; the volume step and the mute model; whether `CLASSROOM_LIGHTS` needs zones. **Known gap with the conceptual architecture in CLAUDE.md:** the conceptual `SET_LIGHT_ZONE` names LEFT / CENTER / RIGHT / projector-area zones, but the implemented target `CLASSROOM_LIGHTS` has **no zone**. Adaptive three-zone lighting (roadmap Week 10) will need an approved vocabulary change; none is made here.

---

## 5. Verification labels

| Statement | Label |
|---|---|
| The vocabulary, validation rules and routing above | **SOFTWARE-VALIDATED** (`tests/test_intents.py`, `tests/test_week8_task4_routing.py`, `tests/test_week8_task5_audit.py`, `tests/test_runtime_routing.py`) |
| Anything involving the ESP32, relays, the speaker, a bulb, a camera or a microphone | **NOT VALIDATED** — no physical command has been executed |
| Sections 3 (stages D–F) and 4 | **PROPOSED**, no executable behaviour |

Only a result observed on the physical device may be described as "working on the device".
