# VESTA Week 9 — Team Interface Review Packet

**NOT TEAM-APPROVED.** Prepared 2026-10-18 by the AI Engineer for the Team Leader, Software Engineer, Hardware Engineer and Audio Hardware Engineer.

Every option below marked "Recommended" is an AI-side proposal. A decision counts as made only when its owner fills in the record in section 3. Background and full options: [WEEK9_INTERFACE_DECISIONS_DRAFT.md](WEEK9_INTERFACE_DECISIONS_DRAFT.md). Implementation evidence: [WEEK9_REPORT.md](../../WEEK9_REPORT.md) section 6.

## 0. Where we are (one paragraph)

The AI side has a working **simulated** loop. It runs: voice → router → validated command → capability check → adapter → correlated result → truthful reply through the one speaker arbiter (`assistant/runtime.py`, `actions/`; 3426 tests pass). Everything is SOFTWARE-VALIDATED / SIMULATED. No ESP32, relay, light or wire protocol is used.

To control a real light, the hardware side needs **one Python class** that implements the existing `ActionAdapter` interface (`actions/contracts.py:212`):

| Member | Meaning |
|---|---|
| `identity` | a short adapter name, e.g. `esp32-lights` |
| `origin` | REAL only after a physical test passes (see the D2 note) |
| `is_connected()` | is the link to the ESP32 up right now? |
| `submit(command) -> SubmitStatus` | DELIVERED / NOT_DELIVERED (known not sent) / DELIVERY_UNCERTAIN (may have been sent) |
| `next_event(deadline)` | the next reply as a small mapping `{request_id, target, action, state, reason}`, the `LINK_LOST` event, or None at the deadline |

The six decisions below are what that class needs before anyone can write it.

## 1. Decisions needed, by person

| Person | Decides | Needed before |
|---|---|---|
| **Team Leader** | **D11** who owns and writes the real adapter. Also: may we proceed per decision as each is recorded? | Anyone writes adapter code |
| **Hardware Engineer** | **D2** what the firmware can report; **D5** which real lights exist and how they map; with SW: **D1** transport, **D3** id echo and duplicates, **D4** timing | Firmware and adapter |
| **Software Engineer** | with HW: **D1** transport and framing, **D3**, **D4**; with TL: the adapter's location if SW owns it | Adapter |
| **Audio Hardware Engineer** | nothing in this packet (D7 volume / mute is later) | — |

## 2. The six priority decisions

### D1 — Transport and framing · Owner: Software + Hardware Engineer

- **Current status:** nothing implemented, by design. No serial / socket / USB code anywhere (tested). CLAUDE.md says "UART and/or USB", undecided. Week 3: every protocol item is UNDEFINED.
- **Recommended:** **USB CDC serial** (the ESP32-S3's native USB): one cable, no level shifting, a stable device name through a udev rule. Use **one text line per message (newline-terminated)** with a **checksum or length field**, so a partial or corrupted line is detectable. The AI side needs only that a corrupted message can never be read as success.
- **Alternatives:**
  - UART on GPIO: simple and independent of USB enumeration, but needs wiring, a baud rate and level care, and has no built-in framing.
  - Wi-Fi (MQTT / HTTP): decoupled, but adds network failure modes, a broker and security work. CLAUDE.md asks for one reliable path first.
- **Question for SW + HW:** *"Will the Pi talk to the ESP32-S3 over USB CDC serial, UART, or Wi-Fi? What marks the end of one message, and how is a corrupted message detected?"*
- **Blocks real hardware:** without D1 there are no bytes to send or parse; the adapter cannot exist.
- **Acceptance test after approval:** an adapter unit test feeds recorded byte streams: a valid frame, a truncated frame, a frame with a bad checksum, and two frames in one read. Only the valid frame produces a reply; the others become MALFORMED and are discarded. Then a bench loopback with the real ESP32: 20 frames each way, 0 corrupt frames accepted.

### D2 — Execution-result vocabulary · Owner: Hardware Engineer (AI proposes)

- **Current status:** implemented AI-side, PROVISIONAL (`actions/contracts.py:44-63`).
  - Replies may carry `ACCEPTED`, `APPLIED_CONFIRMED`, `REJECTED` (+reason) or `FAILED` (+reason).
  - The loop itself decides `UNAVAILABLE`, `TIMEOUT_UNKNOWN` and `UNKNOWN`.
  - Only a correlated `APPLIED_CONFIRMED` may be described as done. ACCEPTED is never "done". Origin REAL is currently refused by the contract.
- **Recommended:** the firmware keeps its own codes, and the adapter maps each one onto exactly one of the four reply states. **Any unmapped code becomes UNKNOWN, never success.** HW states per target what "applied" means: (a) the relay / output was set, or (b) the change was observed, e.g. by the BH1750 lux sensor. Start with (a) and say so in the spoken / app wording.
- **Alternatives:**
  - OK / ERROR only: cannot separate rejected, failed and unavailable, and cannot separate accepted from done. **Not recommended.**
  - Firmware sends the AI enum names directly: simplest mapping, but couples firmware to AI naming.
- **Question for HW:** *"For the classroom lights, which of these can the firmware actually report: received / accepted, applied (output set), applied (observed), rejected with a reason, failed with a reason? What are your reason codes?"*
- **Blocks real hardware:** the adapter cannot translate replies, and the contract must be changed once to allow origin REAL. That change is a reviewed AI-side edit, made only after the first physical test.
- **Acceptance test after approval:** a mapping-table test where every firmware code maps to exactly one state, and an unmapped or garbled code gives UNKNOWN. A bench test with one real switch for each of applied / rejected / failed, each giving the matching spoken reply. Recorded as HARDWARE-VALIDATED only when someone observes the light change.

### D3 — Request ids and duplicates · Owner: Software + Hardware Engineer (AI proposes)

- **Current status:** implemented AI-side, PROVISIONAL.
  - Every interaction gets `c-<12 hex random session>-<n>` (`RequestIdGenerator`, `actions/contracts.py:175`; used by `assistant/runtime.py:449`).
  - A reply counts only if its id is the pending one AND its target and action match.
  - Stale, duplicate, late and old-session replies are discarded and never spoken (tested over 2880 adversarial variants).
  - No automatic retry; a repeated voice request is a new command with a new id.
  - Uniqueness across restarts is probabilistic (48 random bits), not guaranteed.
- **Recommended:** the firmware **echoes `request_id`, target and action byte-for-byte** in every reply. It **never retries on its own** and applies each request once. Optional extra: it ignores a repeat of the last few ids, for defence in depth.
- **Alternatives:**
  - Firmware-assigned sequence numbers: the AI side must then map its ids to theirs, which is more state and more failure points.
  - Persisted counter on the Pi: shorter ids, but they are reused if the file is lost or restored.
- **Question for SW + HW:** *"Can every firmware reply echo our request id (up to 64 characters of letters, digits, `.`, `_`, `-`) plus the target and action unchanged? Does the firmware ever resend or retry by itself?"*
- **Blocks real hardware:** without an echoed id, no reply can be correlated, so no result can ever be confirmed (by design).
- **Acceptance test after approval:** a bench test sends 3 commands. The replies carry the exact ids; a reply with a wrong id (injected on the bench) is discarded; restarting the Pi mid-command does not let the old reply confirm the new session.

### D4 — Timeouts and the unknown-outcome rule · Owner: Hardware + Software Engineer

- **Current status:** implemented rule, no value.
  - The loop requires `timeout_s` and has **no default** (`check_timeout`, `actions/contracts.py:234`).
  - No terminal reply by the deadline gives TIMEOUT_UNKNOWN: "I couldn't confirm…". This is never "done" and never "failed".
  - A reply after the deadline is LATE and ignored. The demo uses 2.0 *simulated* seconds, which is not a hardware value.
  - **Note:** the runtime waits inside CHECKING, so a long timeout blocks the voice loop for that long.
- **Recommended:** HW measures the worst-case time from command to final reply on the bench (at least 20 trials). The timeout is that worst case plus a margin, kept **under about 2 s** for a voice interaction. Keep the unknown-outcome rule as implemented.
- **Alternatives:**
  - A long timeout (5 s or more): fewer unknowns, but the assistant feels frozen and audio backs up.
  - Two-stage: wait briefly for ACCEPTED, speak "working on it", report completion later. Better UX, but needs an asynchronous result path and a later-announcement policy that doesn't exist yet. Defer.
- **Question for HW + SW:** *"What is the measured worst-case time for the ESP32 to switch the lights and reply? Does it send 'accepted' first and 'applied' later, or one reply?"*
- **Blocks real hardware:** without a value the loop refuses to start (intentional).
- **Acceptance test after approval:** the bench measurement table is recorded in WEEK9_REPORT. One test with a reply just inside the agreed timeout (confirmed) and one just outside (TIMEOUT_UNKNOWN, late reply ignored).

### D5 — Mapping commands to real devices · Owner: Hardware Engineer

- **Current status:** `CLASSROOM_LIGHTS` means **all room lights, all-or-nothing**; there are no zones. The simulation declares only CLASSROOM_LIGHTS TURN_ON / TURN_OFF as available (`SIMULATED_LIGHTS_CAPABILITIES`). Anything undeclared, including the smart bulb, is UNKNOWN and is never sent. Week 3 found no ESP32, relay or lighting hardware available.
- **Recommended:** for Week 9, **one physical channel** (one relay or LED group) stands for "classroom lights". HW provides a one-row mapping table, which is what the real capability declaration is built from. Zones (LEFT / CENTER / RIGHT / projector) wait for Week 10 as a separate, versioned change (D6).
- **Alternatives:**
  - Map all-room to several channels switched together: closer to a real room, but more wiring and partial-failure cases.
  - Do zones now: requires changing the frozen Week 8 command vocabulary first.
- **Question for HW:** *"Which physical output will represent the classroom lights in the first demo, and is it safe to switch from software (low voltage / LED, not mains)? Is anything else (fan, smart bulb) going to be available this week?"*
- **Blocks real hardware:** without a declared mapping the capability check answers UNKNOWN and nothing is sent, by design.
- **Acceptance test after approval:** the mapping table is reviewed by HW. The capability declaration built from it marks exactly those rows available; the test is that every other target stays UNKNOWN and unsubmitted. A physical check that the declared channel switches, observed by a person.

### D11 — Who owns the real Pi-side adapter · Owner: Team Leader

- **Current status:** the interface (`ActionAdapter`) and the simulated adapter exist (AI side). The runtime takes an injected loop and builds no adapter itself. Who writes the real adapter is undecided (CLAUDE.md: "Software Engineer / AI Engineer — to be confirmed by the team lead").
- **Recommended:** the **Software Engineer writes the real adapter** (transport, framing, code mapping) behind the existing interface. The **AI Engineer owns the interface, the result rules and the conformance tests**, and the **Hardware Engineer owns the firmware**. The real adapter lives in a new `hardware/` package (the CLAUDE.md layout).
- **Alternatives:**
  - The AI Engineer writes the adapter: fewer hand-offs, but it crosses into the Software Engineer's area and CLAUDE.md's ownership boundary.
  - Joint ownership: flexible, but nobody is accountable.
- **Question for the Team Leader:** *"Who writes and maintains the Pi-side ESP32 adapter class, and may it go in a new `hardware/` package?"*
- **Blocks real hardware:** nobody is assigned to write the one missing class.
- **Acceptance test after approval:** the real adapter passes the same contract scenarios as the simulated one (`tests/test_week9_action_loop.py` T01–T18, run against a loopback / recorded-replies harness), and then one supervised bench run of A (on) and B (off) from `scripts/week9_voice_to_action_demo.py` with the real adapter.

## 3. Decision record — to be filled in ONLY by the named owner

| # | Decision | Owner | Status | Decided by / date | Notes |
|---|---|---|---|---|---|
| D1 | transport + framing | SW + HW | **OPEN** | | |
| D2 | result vocabulary, meaning of "applied" | HW | **OPEN** | | |
| D3 | id echo, no firmware retry | SW + HW | **OPEN** | | |
| D4 | timeout value(s), one- or two-step reply | HW + SW | **OPEN** | | |
| D5 | lights → physical channel mapping | HW | **OPEN** | | |
| D11 | real adapter owner + location | TL | **OPEN** | | |

## 4. Also open (not needed for the first light, recorded so nothing is lost)

| Item | Owner | Why it matters later |
|---|---|---|
| D6 zones (LEFT / CENTER / RIGHT / projector) | TL + HW | Week 10 adaptive lighting needs a vocabulary change |
| D7 volume step, mute model, unmute restore | Audio HW + SW | speaker commands are still unexecuted |
| D8 is the smart bulb a real paired device? | HW + TL | stays UNKNOWN until then |
| D9 sensor messages (identity, unit, validity, origin), humidity device | HW | real sensor answers |
| D10 link loss / reconnect | SW + HW | current proposal: pending request becomes UNKNOWN, nothing replayed |
| D12 general AI backend | Project owner | unrelated to hardware |
| Cancellation / busy-speaker policy | TL | today: stop cannot cancel a running command; a command while the speaker is busy is refused, not queued |

Nothing in this packet changes code, firmware or wiring. Every result stays SIMULATED until a supervised physical test is recorded.
