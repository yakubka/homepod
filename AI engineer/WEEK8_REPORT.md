# Week 8 — Intent + Command Routing (report; Tasks 1, 2, 3, 4 and 5)

**Roadmap (CLAUDE.md):** Week 8 — turn natural-language requests into safe structured commands. Official intent categories (six): `LOCAL_COMMAND`, `SENSOR_QUERY`, `OCCUPANCY_QUERY`, `CLASSROOM_CONTROL`, `SMART_HOME`, `GENERAL_AI`. Deliverable: a tested intent router + command schema. Status table: [WEEK8_STATUS.md](WEEK8_STATUS.md). Week 7 is tracked in [WEEK7_REPORT.md](WEEK7_REPORT.md) and is unchanged (IN PROGRESS, Task 5 real evaluation pending recordings).

**Task 1 status: COMPLETE — SOFTWARE-VALIDATED.** Scope ends at *validated intent + structured request*. No ESP32 integration, no execution, no change to `VoicePipeline`, TTS, playback, the arbiter or the runtime. Week 8 is **not** complete.

**Task 2 status: COMPLETE — EVALUATION HARNESS SOFTWARE-VALIDATED; SPECIFICATION CONFORMANCE MEASURED ON LABELED TEXT FIXTURES; REAL STT INTENT ACCURACY NOT MEASURED** (section 16).

**Task 3 status: COMPLETE — SOFTWARE-VALIDATED RUNTIME INTEGRATION** (section 17). Week 8 is still IN PROGRESS.

## 1. Baseline

2561 passed, 6 skipped, 0 failed (confirmed before any edit). Every non-ignored repository file (818) was SHA-256 hashed before the first edit.

## 2. Audit: what already existed

Searched the repository for intent, command, validation and routing contracts. Result:
- `assistant/answering.py` has an `Intent` enum, but it is the ten frozen AI-1 *question families* (people count, temperature, humidity, brightness, current lecture, expected attendance, professor arrival, capabilities, next class, announcement capability), matched by exact whole-utterance phrases. It is not the six official categories and has no command concept. Its missing-data rules (unavailable when the state is missing, stale, invalid or unconfigured; undeclared capability = unsupported) are exactly what Week 8 must preserve.
- `assistant/runtime.py` calls the answering engine directly; there is no router stage. `speech/pipeline.py` ends at normalised text.
- `fusion/contracts.py` / `fusion/fuse.py` give `ClassroomState` with per-source freshness (FRESH / STALE / ...), units and origin, which the answering layer already honours.
- No existing structured-command, command-validation or routing contract anywhere (including the conceptual `SET_FAN` / `GET_*` names in CLAUDE.md, which are explicitly not a protocol).

## 3. Reusable components

`normalize_question` and the frozen phrase tables (`PHRASES`) from the answering layer; `DeterministicAnswerEngine`, `AnswerContext`, `AnswerRequest` for every answerable query; the fusion synthetic builders for tests; the project's id pattern, privacy conventions, and mutation-harness methodology. Nothing was copied or re-implemented.

## 4. Missing Week 8 requirements (before this task)

The six categories, a typed immutable command, a validation routine, a router with explicit rejection outcomes, the separation of "classified" from "answerable / executable", and the safety rules (no execution on unknown, ambiguous, unsupported, invalid input; no spoken success without an execution result).

## 5. Files

Created: `intents/__init__.py`, `intents/contracts.py`, `intents/router.py`, `tests/test_intents.py`, `WEEK8_STATUS.md`, `WEEK8_REPORT.md`. Modified: `tests/test_handoff_docs.py` (two lines: the upload-plan test now knows the `intents` folder and the two Week 8 files; the GitHub readiness document itself was not touched). No frozen or production file was modified.

## 6. The six categories and the routing outcomes

`IntentCategory` has exactly the six names. `RoutingStatus` is `ROUTED`, `INVALID_COMMAND` (a command was recognised, its argument is missing or wrong; the category is named, no command exists), `UNSUPPORTED`, `AMBIGUOUS`, `INVALID_INPUT`. Rejections never carry a category (`INVALID_COMMAND` excepted). `Handling` states what can be done today, separately from the classification: `ANSWERING_FOUNDATION`, `ANSWER_NOT_IMPLEMENTED`, `NO_BACKEND_APPROVED`, `EXECUTION_NOT_IMPLEMENTED`, `NONE`.

## 7. Structured command schema

`StructuredCommand(intent_category, action, target, request_id, value, unit, parameters, validation_status)`, frozen, validated in `__post_init__` (also by `dataclasses.replace`), so an invalid command cannot exist. `validate_command(untrusted)` accepts a mapping (enum members or names) or a command and returns a `CommandValidationResult` with the reason (MALFORMED, CATEGORY_MISMATCH, UNSUPPORTED_TARGET, UNSUPPORTED_ACTION, UNEXPECTED_VALUE, MISSING_VALUE, MISSING_UNIT, INCORRECT_UNIT, INVALID_VALUE, VALUE_OUT_OF_RANGE, UNSUPPORTED_PARAMETER, INVALID_REQUEST_ID); unknown keys are rejected. The whole supported set is one table:

| Category | Target | Actions |
|---|---|---|
| LOCAL_COMMAND | SPEAKER_OUTPUT | VOLUME_UP, VOLUME_DOWN, MUTE, UNMUTE, SET_VOLUME (0-100 PERCENT) |
| CLASSROOM_CONTROL | CLASSROOM_LIGHTS | TURN_ON, TURN_OFF (all classroom lights; no zone, no geometry, no endpoint) |
| SMART_HOME | SMART_BULB | TURN_ON, TURN_OFF, SET_BRIGHTNESS (0-100 PERCENT) (the generic development bulb; no named device, no protocol) |

`parameters` exists but accepts nothing in v1. There are no pins, packet formats, baud rates, addresses or ESP32 syntax (an AST test over the code, excluding docstrings, enforces this).

## 8. Routing and validation design

`route_intent(IntentRequest) -> IntentResult`: normalise (the answering layer's normaliser, plus `%` read as "percent" and a minus sign before a digit read as "minus"), then collect matches from the exact tables (queries), the command tables (politeness stripped: "please", "can you", "could you", "would you"), the switch grammar (turn / switch on / off), the set grammar ("set <thing> to <number> percent") and, only if nothing else matched, the explanatory GENERAL_AI openers. More than one distinct match is AMBIGUOUS (`CONFLICTING_RULES`), never resolved by order. The result holds a rule id, never the request text. A routed result with a command is built through the same validation as an untrusted command.

Deliberate rejections (all tested): "turn on the light" (singular), "turn it on", "turn on", "set the light ..." AMBIGUOUS; "set the volume", "... to 50", "... to 50 degrees", "... to 150 percent", "... to loud", "... to -5 percent" INVALID_COMMAND with the reason; the projector, the fan, the bedroom light, the left lights, the air conditioner, "the smart bulbs" UNSUPPORTED_TARGET; "set the classroom lights to 50 percent" UNSUPPORTED_ACTION; "play", "pause", "stop", "dim the lights" UNSUPPORTED. The fan is excluded on purpose: CLAUDE.md describes the 40 mm fan as device cooling, so "the fan" is ambiguous with a classroom actuator and is not assumed.

A defect found by the first test run, fixed: the shared normaliser turned punctuation into spaces, so "set the volume to -5 percent" read as 5 percent. A minus sign before a digit now becomes the word "minus" and the request is rejected (ASCII and Unicode minus tested; mutant V14).

## 9. Sensor and occupancy queries reuse the frozen engine

"What is the temperature?" is SENSOR_QUERY; "How many students are here?" is OCCUPANCY_QUERY. `answer_query(result, context, engine)` gives the frozen `DeterministicAnswerEngine` the canonical answering phrase of the family, so every missing-data rule applies unchanged: no state, a missing sensor, a stale reading and an undeclared capability all give "unavailable" / "unsupported" with no number in the text, and the detected-person count is never replaced by the expected attendance (tested with an explicit 30-student schedule fixture). Expected-attendance questions are routed as schedule questions, never as occupancy. `answer_query` refuses anything that is not a routed query with handling `ANSWERING_FOUNDATION`: a command, a rejection, a not-yet-answerable family or a GENERAL_AI without a backend can never reach the engine. "Is the room dark" (no darkness threshold exists) and "is anyone in the room" (no presence handler exists in the answering layer) are classified but `ANSWER_NOT_IMPLEMENTED`; no threshold or presence logic was invented.

## 10. Local, classroom and smart-home commands

Structured requests only: volume up / down, mute, unmute, set volume N percent; classroom lights on / off; the generic smart bulb on / off / brightness N percent. No device API is called and none is assumed present (no audio mixer, no ESP32, no Matter / Wi-Fi / Bluetooth controller, no named device). Every routed command has `requires_acknowledgement` true and `spoken_confirmation_allowed` false.

## 11. GENERAL_AI

Classification succeeds, availability is reported separately. Open-ended requests ("explain ...", "what is ...", "tell me a joke", "help me ...") are `GENERAL_AI` with handling `NO_BACKEND_APPROVED`; no conversational answer is produced and there is no LLM or cloud dependency. A sensor-topic guard turns an open-ended request that mentions temperature, humidity, light level, "in the room" etc. into UNSUPPORTED, so a future general backend can never be asked to invent a measurement. The six deterministic answering families that fit no other category (lecture, expected attendance, professor arrival, capabilities, next class, announcement capability) are provisionally `GENERAL_AI` with handling `ANSWERING_FOUNDATION`; this mapping needs the Team Leader's confirmation.

## 12. Safety invariants (each tested)

Unknown, ambiguous, unsupported-target and invalid-argument requests produce no command and no answer. `IntentResult` cannot be built inconsistent (a rejection with a category, a command on a query category, a routed command without handling `EXECUTION_NOT_IMPLEMENTED`, and so on). `execution_status` can only be NOT_EXECUTED; the result label can only be SOFTWARE-VALIDATED; `spoken_confirmation_allowed` is always false. Logs and results hold no request text (a sentinel test). The package imports no device, network or model library and none of the frozen layers imports it.

## 13. Tests and mutation results

`tests/test_intents.py`: 224 tests covering the six categories, every supported phrase family, politeness and case variants, unsupported / ambiguous / invalid-command / invalid-input tables, every command validation status including malformed untrusted input, immutability, result invariants, reuse of the frozen engine (fixture, missing, stale, unconfigured, expected-attendance, professor arrival), GENERAL_AI handling, privacy, determinism and the package boundary. Mutation campaign on scratch copies (restored and hash-verified after every mutant, stray files cleaned): **47 mutants, 46 killed**. They cover intent misclassification (4), wrong command target (4), invalid command accepted (16), unknown or unsupported routed to an action (6), ambiguity (1), fabricated or substituted data (5), hardware success inferred (5), schema and category drift (3) and privacy (3). The first run left four survivors: one was a no-op mutant (removed), two showed that the answer bridge's handling gate was protected only by a second, redundant lookup (a defence-in-depth test now pins the gate by itself), and one is the intentional canary below. **Survivor V03** (the unit comparison in the schema validator) is an equivalent mutant: `CommandUnit` has a single member, so the comparison cannot fail; unknown unit names are rejected earlier. Full suite: **2785 passed, 6 skipped, 0 failed** (reference 2561 / 6).

## 14. Not claimed

No classification accuracy on real speech or unseen phrasing (not measured); no integration with the runtime, the pipeline, the ESP32 or any device; no execution result, no spoken confirmation path; no LLM or general answers; the category of the lecture / schedule questions is provisional.

## 15. Remaining Week 8 work (proposed; nothing started or approved)

1. Wire the router in front of the answering step in `assistant/runtime.py` (a separately approved change to a frozen module) so that commands are never sent to the answering engine and rejections are spoken or ignored by an explicit policy.
2. An intent-classification evaluation set (labelled phrases, including unseen variants and real STT outputs) and the accuracy / command-acceptance metrics from CLAUDE.md.
3. A written command-semantics contract for the Hardware / Software teams (what each action means; nothing about the wire protocol), and a decision on volume semantics (relative step size, mute state).
4. The Team Leader's decision on the category of the lecture / schedule / professor / capability questions, and on whether a general-purpose backend will ever be approved for GENERAL_AI.
5. Week 9 (not Week 8) owns execution results; until then no command may be reported as done.

## 16. Task 2 — intent evaluation dataset + scoring harness (2026-10-16)

**EVALUATION HARNESS: SOFTWARE-VALIDATED. SPECIFICATION CONFORMANCE: MEASURED ON LABELED TEXT FIXTURES. REAL STT INTENT ACCURACY: NOT MEASURED.** This task evaluates ROUTING of text. It claims nothing about speech recognition, a classroom, live commands or hardware execution. Baseline 2785 passed, 6 skipped, 0 failed; all 825 non-ignored repository files were hashed before the first edit.

### 16.1 Approved category mapping and the Task 1 audit

The project owner approved: Q5 current lecture, Q6 expected attendance, Q7 professor arrival, Q8 capabilities, Q9 next class and Q10 announcement capability are `GENERAL_AI` and use the existing deterministic answering path; open-ended `GENERAL_AI` stays `NO_BACKEND_APPROVED`; expected attendance is never the detected-person count; the six category names are unchanged. Audit of `intents/contracts.py`, `intents/router.py`, `tests/test_intents.py`, `assistant/answering.py` and `evaluation/ai_question_test_set_v1.json`: **no discrepancy**. Every question and accepted variant of the AI-1 oracle routes as Q1 `OCCUPANCY_QUERY`, Q2-Q4 `SENSOR_QUERY`, Q5-Q10 `GENERAL_AI` with handling `ANSWERING_FOUNDATION`; this is now also pinned by a test against the JSON oracle. (The oracle's own ROOM / LECTURE / GENERAL labels are a different, descriptive taxonomy and are not the six routing categories.) Task 1 was not edited.

### 16.2 Files

Created: `evaluation/week8_intent_eval.py` (manifest validation, per-item evaluation, metrics, evidence, command line), `evaluation/week8_intent_manifest_v1.json` (280 entries, frozen), `evaluation/week8_intent_real_stt_manifest.template.json` (placeholders only), `tests/test_week8_intent_eval.py` (98 tests). Modified: `WEEK8_STATUS.md`, `WEEK8_REPORT.md`. No router, contract, runtime, speech, audio, fusion or Task 1 test file was touched.

### 16.3 Corpus design and the gold-label contract

Manually authored, public, non-sensitive phrases; no STT output was invented. Every entry has `id`, `text`, `source_type`, `split_or_purpose`, `expected_outcome`, `expected_category`, `expected_handling`, `expected_query_family`, `expected_reason_code`, `expected_action / target / value / unit`, an optional answer check (`answer_fixture`, `expected_answer_status`, text that must / must not appear), `tags` and `notes`. The expected category of UNSUPPORTED, AMBIGUOUS and INVALID_INPUT is null; INVALID_COMMAND carries the recognised command category (the Task 1 contract); for the confusion matrix every non-ROUTED result has the label `NO_CATEGORY`. A gold command must itself pass `validate_command`. Provenance is verified mechanically: an entry is `ROUTER_PHRASE_TABLE` if and only if its text is a member of the router's phrase tables (politeness removed), so a specification example cannot be filed as development or real data (a fresh run of the generator reclassified six of my own entries). Strata: **SPEC_TABLE** 112 entries (every phrase of the router's tables = 71, plus 35 answer-fixture repeats of table phrases and 6 politeness variants of command-table phrases), **SPEC_AUTHORED** 133 (cases the Task 1 specification defines: grammar forms and rejections), **DEV_AUTHORED** 35 (same-author probes whose gold is the DESIRED behaviour; not independent, not held out). The word HELDOUT cannot appear in the versioned corpus; real entries (`REAL_STT_REVIEWED`, split DEV or HELDOUT) need a consent reference, human review, an STT engine id and a recording reference, and are accepted only in a manifest stored outside the repository.

### 16.4 Coverage

All six categories and all four rejection outcomes are present. By gold category (ROUTED items): LOCAL_COMMAND 30, SENSOR_QUERY 46, OCCUPANCY_QUERY 23, CLASSROOM_CONTROL 15, SMART_HOME 11, GENERAL_AI 58; by gold outcome: ROUTED 183, INVALID_COMMAND 21, UNSUPPORTED 62, AMBIGUOUS 10, INVALID_INPUT 4. Included: volume up / down / mute / unmute and valid and invalid percentages (0, 100, 101, 150, negative, decimal, `%`, words, a missing value or unit, a wrong unit); temperature, humidity and light with a fresh, missing, stale and unconfigured state (35 answer checks); detected-person count including zero, missing and stale, and the expected attendance never replacing it; lights on / off, ambiguous and zone and device targets; the generic smart bulb with values, units and unsupported devices; the six deterministic GENERAL_AI families and ten open-ended questions; compound, repeated and conflicting actions, near misses, unsafe hardware requests, the professor-arrival-versus-presence cases, and ten safe educational questions that contain a sensor word.

### 16.5 Metrics and denominators

Every ratio is returned with its numerator and denominator; a zero denominator is `null` with a reason, never 0 or 100 %. Category accuracy: gold-ROUTED items whose result is ROUTED with the same category / all gold-ROUTED items. Precision / recall / F1 per category from the 7-label confusion matrix (six categories + NO_CATEGORY); F1 is undefined when precision or recall is; macro F1 averages the defined ones and states how many of the six were included. Rejection precision = both rejected / actually rejected; recall = both rejected / gold rejected. **False command acceptance** (the separate safety metric) = items with no gold command for which the router produced an actionable command / items with no gold command. Also reported: wrong command accepted, gold command not produced, exact command agreement (of gold commands and of produced commands), per-field agreement, outcome and handling agreement, query-family agreement, invalid-command category agreement, false rejection overall and by reason, the sensor-topic guard, per-category sample counts, the unsupported and ambiguous counts, the mismatch list (ids only) and invariant violations on the ACTUAL results.

### 16.6 Results (run on the frozen corpus)

| Stratum | Items | Category agreement | Outcome agreement | False command acceptance | Wrong command |
|---|---|---|---|---|---|
| SPEC_TABLE | 112 | 112 / 112 | 112 / 112 | 0 / 91 | 0 / 21 |
| SPEC_AUTHORED | 133 | 41 / 41 | 133 / 133 | 0 / 104 | 0 / 29 |
| **SPECIFICATION CONFORMANCE (both)** | 245 | 153 / 153 | 245 / 245 | 0 / 195 | 0 / 50 |
| DEV_AUTHORED (same author, gold = desired) | 35 | 5 / 30 | 10 / 35 | 0 / 29 | 0 / 6 |
| ALL | 280 | 158 / 183 (0.863) | 255 / 280 | 0 / 224 | 0 / 56 |

All strata: 0 invariant violations (execution status always NOT_EXECUTED, spoken confirmation never allowed, no command on a rejection, no negative or non-integer value) and 0 answer-check violations (35 answer checks, all statuses agree, no number in any non-answer). ALL-stratum macro F1 0.946 over 6 of 6 categories; per-category precision is 1.000 for all six (no request was ever sent to a wrong category); recall LOCAL 0.933, SENSOR 0.870, OCCUPANCY 0.826, CLASSROOM 1.000, SMART_HOME 1.000, GENERAL_AI 0.776. Rejection precision / recall (ALL) 97 / 122 and 97 / 97. Confusion matrix (rows gold, columns actual; NC = NO_CATEGORY): LOCAL 28 + 2 NC; SENSOR 40 + 6 NC; OCCUPANCY 19 + 4 NC; CLASSROOM 15; SMART_HOME 11; GENERAL_AI 45 + 13 NC; NC 97 + 0. Every off-diagonal cell is a request the router rejected although the gold says it should be routed; no request is routed to the wrong category.

### 16.7 Specification conformance versus generalization

The SPEC strata are conformance to the specification the router was built from: the phrases are the router's own, so 100 % agreement is expected and is **not** a classification accuracy. The DEV probe is the only place where the router meets phrasings its tables do not list, and it is written by the same author; its 5 / 30 shows the router is a closed phrase matcher: reasonable sensor ("what's the temperature right now"), occupancy ("how many people are there"), schedule ("what class is on today") and a few command phrasings ("set the speaker volume to 50 percent") are rejected (12 `NO_MATCHING_RULE`, 1 `UNSUPPORTED_TARGET`), which is the safe direction. The sensor-topic guard rejects 12 gold-routed items, including all 10 safe educational questions that contain a sensor word (for example "what is humidity"): measured and reported, not hidden or tuned away. **REAL-WORLD INTENT ACCURACY: NOT MEASURED.** Do not quote any figure above as accuracy on speech, on a classroom, or on unseen users.

### 16.8 Real STT readiness

`python -m evaluation.week8_intent_eval validate|run --manifest <file>` accepts an external manifest of human-reviewed real STT outputs (consent reference, `reviewed_by_human`, engine id, recording reference; DEV or HELDOUT) that must live outside the repository, as must its evidence; the evidence holds ids and labels only, never the transcript. The template is placeholders only. `VoicePipeline` and the Stage B harness were not touched; connecting live transcription to the router is Task 3.

### 16.9 Validation

98 tests: the corpus (size, provenance for every entry, completeness against the router tables, coverage, the approved mapping against the AI-1 oracle), manifest validation (30 invalid-entry cases and the envelope), real-entry and template rules, hand-computed arithmetic (a 10-item dataset with precision, recall, F1, macro F1, the confusion matrices, rejection, command safety, false rejection, handling and family agreement; empty, single-category, rejection-only and strata cases; zero denominators), the run on the frozen corpus, determinism, privacy (a sentinel text reaches neither evidence, summary nor logs), the import boundary, a router that accepts everything (caught: 224 / 224 false acceptances), a router that rejects everything (no false acceptance, false rejection 100 %), the invariant checks on a stub that claims execution or confirmation, the answer-check violations, evidence handling and the command line (exit codes, `--fail-on-safety-violation`). Mutation campaign on scratch copies (restored and SHA-verified; stray files removed): **45 mutants, 45 killed** (swapped precision and recall, zero denominators turned into 100 % or 0 %, false acceptance omitted, a rejection counted as accepted, a wrong command value counted as correct, a no-backend GENERAL_AI counted as answered, expected attendance not excluded, confirmation and execution status not flagged, text written to the log, spec examples filed as development or real, the provenance, consent, review and in-repository checks removed, a template made evaluable, transposed matrices, evidence overwrite, plus seven router regressions the corpus must detect). The first run left two survivors, both genuine test gaps (a command that differs only in its value; the safety-gate arithmetic), now covered. Full suite: **2883 passed, 6 skipped, 0 failed** (reference 2785 / 6).

### 16.10 Not claimed

No real-speech intent accuracy, no classroom or hardware result, no execution, no LLM, no integration with the runtime. The gold labels of the SPEC strata were written from the Task 1 specification and the owner's mapping and are validated against the router only by this harness; the DEV gold is the author's judgement.

### 16.11 Remaining Week 8 work (proposed; nothing started)

1. **Task 3:** integrate the router into the voice-assistant runtime (a separately approved change to `assistant/runtime.py`): route first, answer only `ANSWERING_FOUNDATION` queries, never send a command or a rejection to the answering engine, and decide what is spoken for rejections.
2. Collect consented, human-reviewed real STT transcripts from the Week 7 recordings and run this harness on them outside the repository (the only way to measure real-world intent accuracy).
3. Decide whether to extend the phrase coverage or the sensor-topic guard using the DEV probe's findings (a change to Task 1, to be approved and re-evaluated, with the frozen corpus kept as a regression set).
4. The command-semantics contract for the Hardware / Software teams.

## 17. Task 3 — the intent router inside the voice-assistant runtime (2026-10-16)

**WEEK 8 TASK 3: COMPLETE — SOFTWARE-VALIDATED RUNTIME INTEGRATION (fake input / output). WEEK 8: IN PROGRESS. WEEK 7: IN PROGRESS — real evaluation pending.** Baseline 2883 passed, 6 skipped, 0 failed (confirmed before any edit); all 830 non-ignored files were hashed first.

### 17.1 API audit

Read `intents/contracts.py`, `intents/router.py`, `assistant/runtime.py`, `assistant/answering.py`, `assistant/output_arbiter.py`, `assistant/spoken_output.py`, `assistant/state.py` and their tests. The facts the integration relies on: `route_intent(IntentRequest(text, request_id)) -> IntentResult` never raises for bad text and is deterministic; `IntentResult` is internally consistent (a routed command category always carries its own validated command and handling `EXECUTION_NOT_IMPLEMENTED`; a rejection never carries a category except `INVALID_COMMAND`); `answer_query(result, context, engine)` accepts only a routed query with handling `ANSWERING_FOUNDATION` and refuses everything else; `OutputArbiter.submit_answer(text, request_id)` is the only speaking path and publishes CHECKING / SPEAKING / IDLE; the state manager's table forbids input-side transitions while the arbiter owns the speaker. No protected contract blocked the integration, so no STOP was needed. There is no `DeterministicIntentRouter` class (Task 1 built a module function); the runtime holds that function as its one router, injectable for tests.

### 17.2 What changed

`assistant/runtime.py`: the TRANSCRIBED path now routes first. A `route` constructor argument (default `route_intent`) is the runtime's single router; there is no second parser (a test checks the runtime imports no `re` and that the runtime and the evaluator are the only production consumers of the intent package). New `RuntimeOutcome` fields (category, routing status, rule id, query family, handling, command, `execution_status`, `spoken_confirmation_allowed`) and three new outcome kinds. `scripts/vesta_runtime_demo.py`: four scripted interactions instead of three. Tests: `tests/test_runtime_routing.py` (new, 86), `tests/test_runtime.py` (six existing assertions updated because a joke is now `GENERAL_AI` / `NO_BACKEND_APPROVED`, not an engine "unsupported", and the demo has four interactions), `tests/runtime_support.py` (the spy engine also records the texts it receives), `tests/test_intents.py` (one Task 1 boundary test no longer lists `assistant/runtime.py` as a non-consumer; the other frozen layers stay in it).

### 17.3 Routing architecture

`VoicePipeline` -> (only a complete TRANSCRIBED result) -> `route_intent` -> one of:
- **ROUTED, handling `ANSWERING_FOUNDATION`** (Q1 occupancy, Q2-Q4 sensors, Q5-Q10 `GENERAL_AI`): `answer_query()` with the injected context provider (read afresh for every request) and the frozen engine; the answer text is spoken through the arbiter. Freshness, units, provenance, missing-data and capability rules are the engine's, unchanged. An `ExpectedServiceFailure` still triggers the offline fallback.
- **ROUTED command category** (`LOCAL_COMMAND`, `CLASSROOM_CONTROL`, `SMART_HOME`): the validated `StructuredCommand` is returned in the outcome (kind `COMMAND_NOT_EXECUTED`). Nothing is executed. The only thing spoken is the fixed notice "I understood that request, but I can't carry out device commands yet.", which says the opposite of success.
- **ROUTED, `NO_BACKEND_APPROVED` or `ANSWER_NOT_IMPLEMENTED`** (open-ended `GENERAL_AI`, "is the room dark", "is anyone in the room"): kind `NOT_ANSWERABLE`, the fixed reply "Sorry, I can't help with that yet."; the engine is never asked and no model exists to call.
- **UNSUPPORTED / AMBIGUOUS / INVALID_COMMAND / INVALID_INPUT**: kind `REQUEST_REJECTED`, the exact routing status is kept (never reinterpreted as `GENERAL_AI`), no command exists, and a fixed reply (not echoing the request) says why.

### 17.4 Voice-pipeline outcomes, state and output

WAKE_ONLY, NO_SPEECH, TOO_SHORT, STT_EMPTY, INVALID_ACTIVATION, MAX_DURATION and STREAM_ENDED never reach the router (a test counts router calls for each); truncated speech cannot become a device command. State: LISTENING / CHECKING / SPEAKING / IDLE exactly as before; routing and answering happen inside CHECKING; SPEAKING still waits for the playback first-buffer event (re-tested with a held playback engine). All speech, including the fixed replies, goes through `OutputArbiter`; when the speaker is busy the reply is rejected with the arbiter's decision, the command is STILL returned (it is a request, not a spoken statement) and the state of the active output is untouched. Two simultaneous requests never play concurrently.

### 17.5 Errors

A router or answering exception that is not an `ExpectedServiceFailure` returns the state to IDLE (reason `OUTPUT_ERROR`) and propagates; no spoken apology. A router that returns something other than an `IntentResult` raises `RuntimeConfigError`. A TTS failure on the notice is reported as such; the command outcome is unchanged and still not executed. After any error the next interaction works.

### 17.6 Demo

`python -m scripts.vesta_runtime_demo [--show-text] [--tts real]`: (A) "what can you do" and "how many people are in the room" -> routed (`GENERAL_AI`, `OCCUPANCY_QUERY`) -> a truthful answer (the people count is "unavailable": no sensor source exists); (B) "turn on the lights" -> `CLASSROOM_CONTROL`, command `TURN_ON/CLASSROOM_LIGHTS`, `execution=NOT_EXECUTED confirmation_allowed=False`; (C) "turn on the projector" -> `UNSUPPORTED`, no command. The banner states fake wake / VAD / STT and fake playback and that no command is ever executed; the output is deterministic.

### 17.7 Task 2 corpus regression

The frozen 280-entry manifest is byte-identical (SHA-256 `a03f494c...c8b0` pinned by a test) and the Task 1 router and contract are untouched. The existing evaluator, run after the integration, gives: SPEC_TABLE 112/112 category and outcome agreement; SPEC_AUTHORED 41/41 categories and 133/133 outcomes; SPECIFICATION CONFORMANCE (both) 153/153 and 245/245; DEV_AUTHORED (same author, not independent) category 5/30, outcome 10/35, with 12 sensor-topic-guard false rejections including all 10 safe educational questions that contain a sensor word; false command acceptance 0/224, invariant violations 0. The weak generalization result is unchanged and still visible. This is specification conformance and a same-author probe: REAL-WORLD INTENT ACCURACY: NOT MEASURED.

### 17.8 Validation

86 new tests with the real router, answering engine, arbiter and state manager behind fakes: Q1-Q10 end to end (fresh, zero, missing, stale, unconfigured and default context), open-ended and not-implemented queries, seven commands (all three command categories) returning NOT_EXECUTED, invalid numeric commands, ambiguous and unsupported requests, one router call per transcript (and none for the seven non-complete pipeline outcomes), the request id linking command, arbitration and state events, the RED/BLUE/GREEN lifecycle for every kind of request, a busy arbiter and no concurrent playback, stopping the notice, router and answering bugs, a TTS failure, privacy (a sentinel never reaches a log, an outcome or a state event; not even a valid command's words are logged), determinism, and the real local voice speaking all five fixed replies. Mutation campaign on scratch copies (restored and SHA-verified): **24 mutants, 23 killed** (router bypass, a second parse, wrong delegation, a command reported as executed, NOT_EXECUTED upgraded to a confirmation permission, a spoken success, stale and ignored context, an unapproved backend path, an ambiguous request taken as a command, the transcript logged or echoed, playback bypassing the arbiter, truncated and wake-only utterances routed, the command not returned, a rejection relabelled, a stuck state after a router bug, a non-result accepted, the service-failure path removed, a reused request id); the one survivor is an intentional canary (the demo's own pass flag). Full suite: **2969 passed, 6 skipped, 0 failed** (reference 2883 / 6).

### 17.9 Not claimed

No live voice assistant, no microphone, speaker or wake accuracy, no latency, no ESP32 or device execution, no execution-result contract (a spoken confirmation of any action is impossible until one exists), no general-purpose LLM, no real-world intent accuracy. The fixed replies are new reviewed text, not engine output; they are checked at import for speakability and tested to contain no success wording.

### 17.10 Remaining Week 8 work (proposed; nothing started)

1. Real-STT intent evaluation: run the Task 2 harness on consented, human-reviewed transcripts from the Week 7 recordings, outside the repository.
2. Decide, from the development probe, whether to extend the phrase coverage or soften the sensor-topic guard (a Task 1 change, to be approved and re-evaluated against the frozen corpus).
3. The command-semantics contract for the Hardware / Software teams (what each action means; volume step size; mute state); the execution-result contract belongs to Week 9.
4. A live runner composing the Stage B harness, the real engines and the runtime (not built).

## 18. Task 4 — controlled intent coverage hardening (2026-10-17)

**WEEK 8 TASK 4: COMPLETE — SOFTWARE-VALIDATED ROUTING HARDENING. WEEK 8: IN PROGRESS. REAL-STT INTENT ACCURACY: NOT MEASURED. WEEK 7: UNCHANGED (IN PROGRESS).** Baseline 2969 passed, 6 skipped, 0 failed (confirmed before any edit); all non-ignored files were hashed first. Policy decisions A-E below were approved by the project owner after a read-only review of the 35 development probes.

### 18.1 Approved policy decisions and how each is implemented

| Decision | Implementation | Test |
|---|---|---|
| A. "what is humidity" / "what is relative humidity" are AMBIGUOUS | 10 bare-definition phrases ("what is" / "what's" + humidity, relative humidity, temperature, light level, brightness) in a separate `_AMBIGUOUS_EXACT` table, outcome AMBIGUOUS, reason `SENSOR_TOPIC_AMBIGUOUS`, no category | `test_decision_a_*` |
| B. keep unsupported: "how many people are there", "what class is on today", "what is the lux" | no rule added; the sensor-topic guard / no-match behaviour stands | `test_decision_b_*`, `test_today_is_not_now` |
| C. no "present" without a physical location; expected, registered and detected counts stay separate | "present" is accepted ONLY inside "...present in the room / in this room"; bare or "present today" stays UNSUPPORTED; expected attendance still uses the schedule family and is answered only from a schedule | `test_decision_c_*` (including answers against simulated contexts: a schedule of 30 never becomes a detected count, a detected 17 never becomes expected attendance) |
| D. professor presence is not professor arrival | "is the professor in the room / present / in the building", "is the lecturer in the classroom" are UNSUPPORTED; "has the professor arrived yet" is still answered only from an arrival source (a present person in the state gives "unavailable") | `test_decision_d_*` |
| E. corrected v2 dataset, frozen v1 untouched | `evaluation/week8_intent_manifest_v2.json` (section 18.5); v1 is byte-identical | `test_the_frozen_artifacts_*`, `test_decision_e_*` |

### 18.2 What was added to the router (`intents/router.py`)

A small reviewed set, narrow deterministic matching, no fuzzy matching: **66 exact query phrases** (19 temperature, 13 humidity, 12 brightness, 15 people-in-the-room, 7 next-class begin/start) and **4 command phrases** ("turn the sound up / down", "turn up / down the sound"), plus `speaker volume` as an explicit object of the existing "set ... to N percent" grammar, plus **10 AMBIGUOUS definitions**. Every added measurement phrase names the room ("in the room", "in this room", "in here"), "current" or "right now". Table size: 55 -> 121 exact phrases, 16 -> 20 command phrases, 0 -> 10 ambiguous. The categories are unchanged (exactly six; no seventh); `intents/contracts.py` is untouched, so `execution_status` is still always NOT_EXECUTED and `spoken_confirmation_allowed` is still always false. Each added phrase has its own parametrised test, and a collision test proves each table phrase is claimed by exactly one rule.

**Educational frame.** The sensor-topic guard still rejects any sentence with a sensor word. A sentence it would reject is let through (GENERAL_AI / `NO_BACKEND_APPROVED`, classified, never answered, nothing generated) only if it opens with an educational frame ("explain", "describe", "define", "why does/do", "how does/do", "what does", "what is a/an") AND has no room, now/today/current, attendance, arrival, expected, professor, measured or reading context AND no "the <sensor noun>". "explain how humidity works", "explain what temperature means in physics", "what is a lux in photography" and "why does humidity affect plants" are GENERAL_AI; "explain the temperature in the room", "describe the humidity here", "explain how many people are in the room", "what does the measured light level mean" stay rejected. Every outcome that did not hit the guard is unchanged.

### 18.3 Historical reproducibility (v1 is frozen)

The router grew, so the v1 manifest's mechanical provenance check ("is this text a member of the router's phrase tables?") would have started rejecting entries that the original router did not contain. Rather than redefine past SPEC_TABLE membership against the enlarged tables:
- `evaluation/week8_task1_router_frozen.py` is a **byte-identical copy of the original Task 1 router** (SHA-256 `e592240c...e62f`); it only imports `intents.contracts` and is never used by production code (a test pins the importer set).
- `evaluation/week8_phrase_table_task1_snapshot.json` (55 exact + 16 command phrases) and `..._task4_snapshot.json` (121 + 20 + 10) were extracted **mechanically from the two routers' own tables** (not from desired scores); tests assert each snapshot equals its router's table.
- The evaluator judges a corpus against the snapshot the corpus names (v1 = `task1`; v2 = `task4`), and `run --router task1-frozen` reproduces the original baseline.

### 18.4 The unchanged v1 corpus: original router versus new router

Same 280 frozen entries (112 SPEC_TABLE / 133 SPEC_AUTHORED / 35 DEV_AUTHORED). "Category" = gold ROUTED items routed to the same category (denominator excludes rejection gold); "outcome" = routing-outcome agreement over all entries.

| Stratum | Original router: category / outcome | New router: category / outcome |
|---|---|---|
| SPEC_TABLE (112) | 112/112 / 112/112 | 112/112 / 112/112 |
| SPEC_AUTHORED (133) | 41/41 / 133/133 | 41/41 / 133/133 |
| DEV_AUTHORED (35, same author) | 5/30 / 10/35 | 21/30 / 26/35 |
| ALL (280) | 158/183 / 255/280 | 174/183 / 271/280 |

Macro F1 0.946 -> 0.982 (6/6 categories); per-category recall LOCAL_COMMAND 0.933 -> 1.000, SENSOR_QUERY 0.870 -> 0.978, OCCUPANCY_QUERY 0.826 -> 0.913, GENERAL_AI 0.776 -> 0.897; precision stays 1.000 in every category. False command acceptance 0/224 and invariant violations 0 in both runs; structured-command exact-field agreement of gold commands 54/56 -> 56/56 (the two former misses were "turn the sound up/down"); false rejections 25/183 -> 9/183. **These improvements are partly an artefact of the process:** the author inspected the 35 probes and designed the changes, and 8 of the 9 remaining DEV mismatches are cases where the owner decided the v1 gold label was wrong (next section), not router errors. They must not be read as generalisation accuracy.

### 18.5 The v2 dataset (`evaluation/week8_intent_manifest_v2.json`, schema `week8-intent-eval-manifest-v2`)

173 entries, SHA-256 `2488542b...0ca0`, pinned to snapshot `task4`. It holds **only** (1) 8 corrected v1 gold labels, (2) newly supported phrase families (all 80 new table phrases, 23 answer-fixture checks with simulated contexts, 16 command-grammar cases), (3) new educational, protection and adversarial controls (11 educational, 10 protection, 23 adversarial negatives, 2 known gaps). Its banner states: **development / regression evidence, not independent, not held out, real-world accuracy NOT MEASURED**; the same author inspected the v1 probes and designed the changes. It is deliberately not called held-out. A genuinely independent evaluation needs separately sourced, human-reviewed requests or real consented STT transcripts (the external template from Task 2 still applies).

Corrections (listed in the manifest's `corrections` block, each with a reason; v1 itself is not edited):

| v1 id | Text | v1 gold | v2 gold | Reason |
|---|---|---|---|---|
| D-SQ-006 | what is the lux | SENSOR_QUERY | UNSUPPORTED | decision B |
| D-OQ-001 | how many people are there | OCCUPANCY_QUERY | UNSUPPORTED | decision B |
| D-OQ-002 | how many students are present | OCCUPANCY_QUERY | UNSUPPORTED | decision C |
| D-GA-001 | is the professor in the room | GENERAL_AI (arrival) | UNSUPPORTED | decision D |
| D-GA-002 | what class is on today | GENERAL_AI (current lecture) | UNSUPPORTED | decision B ("today" is not "now") |
| D-GA-004 / -005 | what is (relative) humidity | GENERAL_AI | AMBIGUOUS | decision A |
| D-GA-010 | describe why the room feels warm in summer | GENERAL_AI | UNSUPPORTED | the sentence names the room |

The one remaining v1 DEV mismatch, D-GA-013 ("what is the dark side of the moon"), is a plain false rejection by the sensor word "dark"; it is left as a known gap (outside the approved scope).

**v2 results (new router, one run):** SPEC_TABLE 108 items (category 95/95, outcome 108/108); DEV_AUTHORED 65 items (category 14/14, outcome 63/65); SPEC_AUTHORED empty (no authored specification items, so the stratum is absent, never reported as 0 % or 100 %); ALL 173 items: **category 109/109, outcome 171/173**, structured-command exact-field agreement 9/9, false command acceptance 0/164, invariant violations 0, answer-check violations 0 (23 checked), false rejections 0/109. Per category (precision / recall): LOCAL_COMMAND 9/9, 9/9; SENSOR_QUERY 59/59, 59/59; OCCUPANCY_QUERY 21/21, 21/21; GENERAL_AI 20/22, 20/20; CLASSROOM_CONTROL and SMART_HOME have no v2 items (undefined, macro F1 over 4/6 categories = 0.988). Ambiguous: 13 gold / 13 actual; unsupported: 45 gold / 43 actual. All denominators are the counts above. Because the gold labels were written by the router's author from the policy decisions, these figures are conformance to those decisions, **not** accuracy.

**Authoring honesty.** The first v2 run showed three mismatches; none was "fixed" by editing the router to match. (1) `set the speaker volume to 50 percent and mute`: I had guessed UNSUPPORTED; the original grammar returns INVALID_VALUE for the identical `volume` text, and an explicit speaker-volume object inherits it, so I amended that one gold label to INVALID_COMMAND **before freezing** (a safe rejection either way; no command is produced). (2) and (3) **KNOWN GAPS K-001 and K-002** ("explain why the professor has not arrived", "why does the classroom attendance look low") are kept with the safer gold UNSUPPORTED and are reported as the two outcome mismatches (and the 2 false GENERAL_AI predictions behind GENERAL_AI precision 20/22). They are pre-existing Task 1 behaviour (the original router gives the identical result): an open-ended question with no sensor word reaches GENERAL_AI / `NO_BACKEND_APPROVED`, nothing is answered and nothing is fabricated. If a general backend is ever approved, questions about this class's professor or attendance would reach it; a guard needs an owner decision first and is proposed for Task 5.

### 18.6 Safety

All command results are unchanged in kind: `execution_status` NOT_EXECUTED, handling `EXECUTION_NOT_IMPLEMENTED`, `spoken_confirmation_allowed` false (asserted for every new command phrase and for the speaker-volume grammar). Negative numbers, decimals, out-of-range and unitless percentages, unsupported device targets ("set the speaker to 50 percent", "turn the sound off", fan, projector), repeated and conflicting commands are rejected without a command. Other places, times and people ("the sun", "paris", "outside", "tomorrow", "in the building") are never a room reading. Educational questions are classified but never answered, and no GENERAL_AI request reaches an unapproved backend. All ten AI-1 questions and their accepted variants keep their approved routing (`test_all_ai_1_questions_*`, plus the existing AI-1 suite). No ESP32 protocol, no physical execution, no LLM or new model, no hardware change.

### 18.7 Files

Created: `evaluation/week8_task1_router_frozen.py`, `evaluation/week8_phrase_table_task1_snapshot.json`, `evaluation/week8_phrase_table_task4_snapshot.json`, `evaluation/week8_intent_manifest_v2.json`, `tests/test_week8_task4_routing.py` (176 tests). Modified: `intents/router.py`; `evaluation/week8_intent_eval.py` (provenance support only: v2 schema, pinned snapshots, `--router`, `corrections`); `tests/test_week8_intent_eval.py` and `tests/test_runtime_routing.py` (deliberate updates: the Task 2 numbers are now reproduced with the frozen original router; table completeness uses the task1 snapshot; the frozen copy is added to the allowed importers); `WEEK8_STATUS.md`; this report. Untouched and byte-verified: `intents/contracts.py`, `assistant/*`, `speech/*`, `audio/*`, `wakeword/*`, `fusion/*`, `config/settings.py`, `evaluation/ai_question_test_set_v1.json`, `evaluation/week8_intent_manifest_v1.json`.

### 18.8 Mutation results (scratch copies, restored and SHA-verified)

21 mutants: **20 killed, 1 intentional canary survived** (a docstring word). The killed set covers: "today" mapped to the current lecture; "there" mapped to occupancy; professor presence mapped to arrival; bare humidity turned into a room reading; the educational frame ignoring room context, and ignoring attendance / arrival / measurement stems; a general backend assumed for GENERAL_AI; a wrong device target accepted; a negative percentage accepted; execution reported as successful (spoken confirmation allowed); v1 provenance judged against the grown live tables; a v1 gold label modified; an AI-1 family category altered; a v2 gold label modified; the frozen router edited; the snapshot altered; the ambiguous lookup removed; the educational frame widened; expected attendance answered from the detected count; "turn the sound off" accepted. The first pass stops at the first failing test, and the table-level mutants were killed first by the snapshot-consistency test, so a second pass with that one test deselected re-ran them: 5/5 still killed by behavioural tests.

### 18.9 Full regression and not claimed

Full suite after Task 4: **3145 passed, 6 skipped, 0 failed** (reference 2969 / 6; +176 = the new Task 4 tests). The Task 1 intent tests, Task 2 evaluation tests, Task 3 runtime-routing tests, AI-1 question tests and answering tests all pass.

**Not claimed:**

No real-world intent accuracy; no speech recognition result; the v2 corpus is same-author development / regression evidence, not independent and not held out; phrase coverage is still a small closed set; the sensor-topic guard still falsely rejects some harmless questions (D-GA-013); K-001 / K-002 are open; no execution, no execution result, no ESP32, no hardware; Week 7 is unchanged and its real recorded evaluation is still pending.

### 18.10 Remaining Week 8 work (proposed; nothing started)

**Week 8 Task 5 — structured command semantics, router safety audit and software closeout preparation:** the command-semantics contract for the hardware and software teams (what each action means; volume step; mute state); an audit of the router's remaining open-ended GENERAL_AI exposure (K-001 / K-002, an owner decision on a guard); then closeout. Real-STT intent evaluation on consented, human-reviewed transcripts remains blocked on the Week 7 recordings.

## 19. Task 5 — structured command semantics, final router safety audit and software closeout preparation (2026-10-17)

**WEEK 8 TASK 5: COMPLETE — SOFTWARE-VALIDATED. WEEK 8 SOFTWARE MILESTONE: READY FOR CLOSEOUT. WEEK 8 REAL-WORLD INTENT EVALUATION: PENDING. WEEK 9 HARDWARE ACTION EXECUTION: NOT STARTED. WEEK 7 REAL SPEECH EVALUATION: PENDING.** Nothing here is a hardware result; no command was ever executed.

### 19.1 Baseline and discrepancies found before any edit

Reference 3145 passed / 6 skipped. First run: **3144 passed, 7 skipped**. The extra skip is `tests/test_ai2_stage_a_harness.py:628` ("faster-whisper not importable ... An Application Control policy has blocked this file"): a transient Windows policy block during the full run; a read-only import check and a rerun of that file immediately afterwards gave 90 passed, 0 skipped. Other discrepancies, each recorded before implementing:
1. **"Explain the difference between attendance and occupancy." was NOT GENERAL_AI** (the task text says it "remains" GENERAL_AI). It was `UNSUPPORTED` via the sensor-topic guard because "occupancy" is a guard word and the Task 4 educational frame rejected the bare stem `attend`. This is a Task 4 over-rejection and is fixed in 19.3.
2. `docs/contracts/` did not exist (created; `docs/` already held `handoff/`).
3. The conceptual `SET_LIGHT_ZONE` in CLAUDE.md names LEFT / CENTER / RIGHT / projector-area zones; the implemented `CLASSROOM_LIGHTS` target has no zone. Recorded in the contract; not changed.
4. 838 pre-existing files were hashed first (binary SHA-256).

### 19.2 Scope and architecture audit

Read `intents/contracts.py`, `intents/router.py`, `assistant/runtime.py`, `assistant/answering.py` and the prior Week 8 reports. The six categories, the four rejection outcomes, `NOT_EXECUTED` and `spoken_confirmation_allowed = False` are as documented; no protected module needed a change, so no STOP was required. Only Pi-side classification exists; the ESP32, relays and wire protocol remain hardware-team items.

### 19.3 The two known gaps and the exact policy implemented

Before: "explain why the professor has not arrived" and "why does the classroom attendance look low" were `GENERAL_AI / NO_BACKEND_APPROVED` (never answered). Policy (owner-approved): a causal or state question about the actual professor's arrival or this class's attendance is not an educational question; it is refused as `UNSUPPORTED` (no answer, no action, no command, no fabricated explanation). Implemented as a narrow closed-list **class-event guard** in `intents/router.py` (`_class_event`), consulted inside `_general` before any GENERAL_AI path, so exact table phrases are never affected. Reason `CLASS_EVENT_NOT_ROUTABLE_TO_GENERAL_AI`, rule `GEN.CLASS_EVENT_GUARD`:
- **professor event:** a definite reference to this class's staff member ("the / our / my / this / that / your" + professor, lecturer, instructor, teacher) AND an arrival-state word or phrase (arrive/arrived/late/absent/here/yet/come/missing/...);
- **attendance event:** the noun "attendance" with a this-class or time cue (definite "the / our / this / today's [word] attendance", "the classroom", "this class", "in class", "today", "now" ...), or an attendance-state phrase said without the noun ("class is empty"; "few students", "students absent" with a this-class cue).

The Task 4 educational frame no longer rejects on the bare stems `attend`, `absent`, `arriv`, `professor`, `lecturer` (the guard now owns those cases); `enrol`, `expected`, `measured`, `reading`, `present` stay. Kept as GENERAL_AI / NO_BACKEND_APPROVED (tested): "Explain why university professors can be late.", "Explain the difference between attendance and occupancy.", "why do professors arrive late", "explain what attendance means in a university", "explain the attendance policy at universities", "explain voter turnout" and 22 more (28 benign phrasings in total). Collision and over-generalization checks: the guard changes **exactly 2 of the 453 frozen corpus items** (K-001, K-002) and **0 of the 30 AI-1 phrases**; it never acts on a gold-ROUTED item (test). **Known conservative false rejections (pinned):** a generic sentence that uses "the professor" with an arrival word ("explain why the professor can be late"; say "professors"); sentences containing the literal phrases "empty class(room)" or "class is empty" in unrelated senses; and "what is the difference between attendance and occupancy" (the older sensor-word guard; "what is the ..." is deliberately not an educational frame). Not covered (residual exposure, no source could answer them either): questions about this class that name neither the professor nor attendance, for example "who is the professor of this class" or "what class is on today" (the latter already UNSUPPORTED). A real general backend, if ever approved, needs its own guard before it sees any such question.

### 19.4 Command schema audit (`StructuredCommand`)

Fields: `intent_category`, `action`, `target`, `request_id`, `value` (int or None), `unit` (`PERCENT` or None), `parameters` (must be empty in v1), `validation_status` (always `VALID`). Enumerations: 8 `CommandAction`, 3 `CommandTarget`, 1 `CommandUnit`, 13 `CommandValidationStatus`, 1 `ExecutionStatus` (`NOT_EXECUTED`). The supported table has exactly 10 (category, target, action) entries. Nothing in the task text disagreed with the code; classroom-light brightness is **not** supported (`UNSUPPORTED_ACTION`), only the generic smart bulb takes brightness.

### 19.5 Contract documents

`docs/contracts/WEEK8_COMMAND_SEMANTICS_V1.md` (new): vocabulary, the supported table, per-command phrasings / arguments / validation / unsupported arguments / whether target resolution is still needed / result with no executor, the rejection map (including multiple actions), the runtime behaviour, the A-F life cycle (A-C implemented, D-F not), fourteen hardware-team requirements R1-R14 (identity, duplicates, timeout, targets, capability discovery, invalid-target rejection, acknowledgment, failures, uncertainty after timeout, asynchronous completion, recovery after connection loss, false-success prevention, real-versus-simulated, sensor validity), and the open decisions. Every statement is tagged IMPLEMENTED CONTRACT, PROPOSED FUTURE INTERFACE or AWAITING TEAM APPROVAL. Transport and wire format are explicitly OPEN; no pin, baud rate, GPIO command, address or endpoint is assigned (a test scans for them). No execution-result object was added.

### 19.6 Query data safety audit (ten families, tested against the SIMULATED fixtures)

| Family | Rule confirmed | Evidence |
|---|---|---|
| temperature / humidity / lux | used only if FRESH, numeric and in the expected unit; missing, stale or unconfigured is "unavailable" / unsupported; several sensors are listed, never averaged | 13 missing/stale cases contain no digit |
| detected-person occupancy | `vision_count` only when vision is FRESH; zero is a real answer; never the schedule | `test_expected_attendance_is_never_the_observed_count...` |
| current lecture / next class / expected attendance | answered only from an explicit `SimulatedScheduleContext`, always labelled SIMULATED; with no schedule: unavailable `NO_SCHEDULE_SOURCE` | 12 cases, no digit |
| professor arrival | ALWAYS unavailable (`NO_AUTHORITATIVE_ARRIVAL_STATE`), in every fixture, including when someone is present | 11 fixtures |
| capabilities / announcements | follow the declaration; with nothing declared "no functions are enabled"; no speaker, camera or microphone is ever named | tested |

### 19.7 Historical evaluation preserved (no second full code copy)

`intents/router.py` is the only production router. The Task 4 router is rebuilt **in memory** by `evaluation/week8_router_history.py` from the current router by reverse-applying `evaluation/week8_router_task4_to_task5_edits.json` (3 recorded edits) and is used only if the result is byte-identical (SHA-256 `6d9c5d30...485ca`) to the Task 4 router; if the router changes again, the rebuild is refused with a clear message (tested, including tampered edit script, tampered task4 hash and a wrong task5 hash). The Task 1 frozen copy, both snapshots, both manifests and the v1 / v2 gold labels are unmodified. Two Task 4 tests that pinned the Task 4 router's known-gap behaviour now run against the rebuilt Task 4 router (historical evidence), and the importer-set test lists the history module.

### 19.8 Evaluation results (exact; category accuracy covers gold-ROUTED items only and excludes rejection gold)

**v1 (280 frozen entries, snapshot task1)**

| Router | SPEC_TABLE cat / out | SPEC_AUTHORED cat / out | DEV_AUTHORED cat / out | ALL cat / out | macro F1 | command fields (gold) | false command acceptance | wrong command | violations |
|---|---|---|---|---|---|---|---|---|---|
| Task 1 original (frozen) | 112/112, 112/112 | 41/41, 133/133 | 5/30, 10/35 | 158/183, 255/280 | 0.946 (6/6) | 54/56 | 0/224 | 0/56 | 0 |
| Task 4 baseline (rebuilt) | 112/112, 112/112 | 41/41, 133/133 | 21/30, 26/35 | 174/183, 271/280 | 0.982 (6/6) | 56/56 | 0/224 | 0/56 | 0 |
| **Task 5 current** | 112/112, 112/112 | 41/41, 133/133 | 21/30, 26/35 | 174/183, 271/280 | 0.982 (6/6) | 56/56 | 0/224 | 0/56 | 0 |

Task 5 changes **nothing** on v1 (the same 9 DEV mismatches; 8 are the owner-corrected gold labels, 1 is the known D-GA-013 false rejection).

**v2 (173 entries, snapshot task4; SPEC_AUTHORED empty)**

| Router | SPEC_TABLE cat / out | DEV_AUTHORED cat / out | ALL cat / out | macro F1 (4/6 categories) | GENERAL_AI precision | command fields | false command acceptance | wrong command | violations | mismatches |
|---|---|---|---|---|---|---|---|---|---|---|
| Task 4 baseline | 95/95, 108/108 | 14/14, 63/65 | 109/109, 171/173 | 0.988 | 20/22 | 9/9 | 0/164 | 0/9 | 0 | K-001, K-002 |
| **Task 5 current** | 95/95, 108/108 | 14/14, 65/65 | 109/109, **173/173** | **1.000** | 20/20 | 9/9 | 0/164 | 0/9 | 0 | none |

Gold unsupported 45 / actual 45 (Task 4: 43); ambiguous 13 / 13; invalid command 6 / 6. **The v2 improvement is by construction:** the gold labels of K-001 and K-002 were written as the safer behaviour before the guard existed, so the guard makes the router satisfy its own author's labels. It is not accuracy. All corpora are same-author text; **REAL-WORLD INTENT ACCURACY: NOT MEASURED**.

### 19.9 Tests and mutation results

`tests/test_week8_task5_audit.py`: **170 new tests** (guard: 19 unsafe and 28 benign phrasings, documented limits, order, determinism; before/after change set; history rebuild and tamper refusal; a 25-case command matrix that reaches all 10 supported entries; 15 invalid numeric values; 12 unsupported targets; ambiguity; 12 multiple-command cases; the validator; no false hardware success; no executor or transport import; duplicate semantics; the ten query families with stale, missing, expected-versus-observed and capability cases; runtime refusal and NOT_EXECUTED for every family; no transcript or PCM leakage; contract-document consistency). Mutation (scratch copies, restored and SHA-verified, 21 mutants): **20 killed, 1 intentional canary survived**. Classified by the first killer: **12 behavioural** (guard disabled; attendance list emptied; benign professors rejected; attendance with no cue rejected; guard applied to table phrases; a success notice spoken; stale data used; hidden duplicate suppression; a v2 gold label changed; the contract document with a wrong enumeration, with stage D marked implemented, or with a transport detail); **6 killed first by an artifact / history check and ALSO killed by behavioural tests when those were deselected** (ambiguous command accepted; wrong target; unknown target accepted; hardware status promoted to success; attendance used as occupancy; transcript leaked in logs); **2 killed ONLY by artifact-integrity checks, by design** (the recorded edit script altered; the frozen Task 1 router edited), which is exactly what those hash checks are for.

**Full regression after Task 5: 3315 passed, 6 skipped, 0 failed** (3145 + the 170 new tests). Skip reasons: ReSpeaker XVF3800 not detected on this machine (4 tests), `VESTA_HEY_JARVIS_WAV` not set (1), `VESTA_PLAYBACK_API_SMOKE` not set (1); the transient faster-whisper skip seen in the first baseline run did not recur. Rerun and Task 1-4 suites, runtime-routing, answering, arbiter / state and documentation tests are all inside this run.

### 19.10 Week 8 readiness and what is not claimed

- **WEEK 8 SOFTWARE MILESTONE: READY FOR CLOSEOUT** (Tasks 1-5 SOFTWARE-VALIDATED: routing, structured commands, evaluation harness, runtime integration, hardening, final audit and contract).
- **WEEK 8 REAL-WORLD INTENT EVALUATION: PENDING** (no human-reviewed real STT transcript exists).
- **WEEK 9 HARDWARE ACTION EXECUTION: NOT STARTED.** No executor, hardware adapter, simulator or execution result exists; the physical voice-command loop is not finished and no "lights turned on" confirmation is possible.
- **WEEK 7 REAL SPEECH EVALUATION: PENDING** (Task 5 real recorded evaluation).
- **Team Leader AI-1 COMPLETE; AI-2 to AI-9 NOT COMPLETE** (unchanged; hardware dependent). Nothing here completes any of them.
- Raspberry Pi 5 + ReSpeaker, camera and ESP32 hardware validation remain PENDING; the contract's requirements R1-R14 need Software / Hardware team approval before Week 9.
- The `CLAUDE.md` status table was not updated in this task (outside the allowed edit list); it should be refreshed by the project owner.

### 19.11 Remaining hardware and real-data requirements

Recorded, consented, human-reviewed speech and transcripts (Week 7 Stage A / B) to measure real-STT intent accuracy; a Software / Hardware decision on the R1-R14 requirements, the transport and the target mapping; the volume-step, mute-state and zone questions in the contract; physical Pi / microphone / camera / ESP32 validation; an owner decision on a general-backend guard before any backend is approved.

## 20. Formal software closeout and Week 9 interface readiness audit (2026-10-17)

**WEEK 8 SOFTWARE MILESTONE: COMPLETE — SOFTWARE-VALIDATED.** Kept separate: REAL-WORLD INTENT ACCURACY **NOT MEASURED**; REAL STT EVALUATION **PENDING CONSENTED RECORDINGS**; HARDWARE ACTION EXECUTION **NOT VALIDATED**; WEEK 9 **NOT STARTED**; WEEK 7 **IN PROGRESS**; Team Leader AI-1 to AI-9 **UNCHANGED** (AI-1 complete; AI-2 to AI-9 not complete). This session changed no production file and no test; it added only this section and the status rows.

### 20.1 How this was verified (not from earlier reports)

Baseline reproduced exactly: **3315 passed, 6 skipped, 0 failed** (1 min 55 s); 843 files hashed first. I then probed the live code with an independent closeout-gate script (scratch, outside the repository): **35 checks, all PASS** (G1-G15: categories, immutability, input validation, rejections, answering delegation, runtime, no transport imports, no false success, sensor honesty, attendance / occupancy, professor presence / arrival, no LLM backend, ambiguity, secrets, frozen hashes). Two checks failed on their first run **because of my own over-broad scan patterns**, not because of the code: an "OpenAI" regex matched a docstring saying the *local* Whisper model is a CTranslate2 conversion of OpenAI Whisper and the project's own secret-scanner hook (`.claude/hooks/secret_scan.py`); a "secret" regex matched the leakage-test canary `"zebracrossing"` in three tests. I replaced them with stricter checks (LLM client imports and API URLs by AST / regex; the canary matches listed and asserted to be only that string) and recorded this here instead of silently loosening the gate. Result of the stricter checks: no LLM client import, no LLM API URL, no `openai` / `anthropic` / `requests` / `httpx` in `requirements.txt`, no secret-shaped string outside tests.

### 20.2 Original roadmap (CLAUDE.md, Week 8) against the implementation

Goal: *natural-language requests -> safe structured commands.* Deliverable: *tested intent router + command schema.*

| Requirement (CLAUDE.md) | Classification | Evidence |
|---|---|---|
| Exactly six official categories, no seventh | **COMPLETE — SOFTWARE-VALIDATED** | G1; the router produces all six and nothing else on 453 corpus items |
| The four roadmap examples ("Turn on the lights" -> CLASSROOM_CONTROL, "What is the temperature?" -> SENSOR_QUERY, "How many students are here?" -> OCCUPANCY_QUERY, "Explain machine learning." -> GENERAL_AI) | **COMPLETE — SOFTWARE-VALIDATED** | G1c |
| Safe structured commands (typed, validated, immutable, no free-form parameters) | **COMPLETE — SOFTWARE-VALIDATED** | G2 / G2b / G2c; 10-entry table; 5 of 5 invalid constructions refused |
| Command validation and routing rejections (unsupported, ambiguous, invalid command, invalid input) | **COMPLETE — SOFTWARE-VALIDATED** | G3 / G4 / G13: 114 unsupported, 27 invalid-command, 25 ambiguous, 4 invalid-input across the corpora, none carries a command |
| Deterministic local routing first, LLM only when appropriate; no cloud streaming | **COMPLETE — SOFTWARE-VALIDATED** (no LLM exists; GENERAL_AI is classified `NO_BACKEND_APPROVED`) | G12 / G12b / G12d |
| Sensor / occupancy queries answered from real data only, never invented | **COMPLETE — SOFTWARE-VALIDATED on SIMULATED fixtures** | G5 / G9 / G10 / G11; no live sensor source exists |
| Tested intent router + command schema (the deliverable) | **COMPLETE — SOFTWARE-VALIDATED** | 3315 tests; 5 mutation campaigns across Tasks 1-5 |
| Runtime integration (voice pipeline -> router -> answer / command outcome) | **COMPLETE — SOFTWARE-VALIDATED with fake input / output** | G6 / G6b / G6c / G6d |
| Original voice Route A examples "Play music", "Stop", "Turn the fan on" | **OUT OF SCOPE / FUTURE WEEK** (all `UNSUPPORTED`; no music player, no fan target in the command table; the fan belongs to the ESP32 side) | probed |
| Route B examples "Turn on the bedroom light", "Set the light brightness to 50 percent" | **PARTIAL** (only the generic smart bulb and the classroom lights exist; "bedroom light" is `UNSUPPORTED`; "the light" is `AMBIGUOUS` by design) | probed |
| Route C "Is someone in the room?", "Is the room dark?" | **PARTIAL** (classified: ROOM_PRESENCE / ROOM_DARK with `ANSWER_NOT_IMPLEMENTED`; no answer logic) | probed |
| Route D general conversation / OpenAI | **OUT OF SCOPE / FUTURE WEEK** (classified only; no backend approved, no LLM, by decision) | G12 |
| Intent-classification accuracy on real speech | **PENDING REAL-WORLD VALIDATION** | no human-reviewed real STT transcript exists |
| Executing a command; AI -> ESP32 loop; execution result | **OUT OF SCOPE / FUTURE WEEK (Week 9)** — NOT STARTED | none exists |

### 20.3 Implementation coverage (verified in source)

`intents/contracts.py`: 6 `IntentCategory`; frozen `StructuredCommand` validated in `__post_init__` (cannot exist invalid; `parameters` is a read-only mapping that must be empty); one validation routine shared by construction and `validate_command`; `IntentResult` invariants (a routed command carries its own command and `EXECUTION_NOT_IMPLEMENTED`; every rejection has no category, command or family; `execution_status` can only be `NOT_EXECUTED`; `spoken_confirmation_allowed` is a read-only `False`). `intents/router.py`: normalised exact phrase tables plus three small grammars, a politeness strip, a conflict rule (two rules disagreeing gives `AMBIGUOUS`), `answer_query()` refusing anything but `ANSWERING_FOUNDATION`, structural-only logging. `assistant/runtime.py`: exactly one `route` call per complete transcript, answering through the frozen engine, a validated command returned with `NOT_EXECUTED` and a fixed not-done notice, rejections keeping their status, truncated / wake-only utterances never routed. `evaluation/week8_intent_eval.py` (+ history module): stratified, provenance-checked, zero-denominator-safe scoring. No module in `intents/`, `assistant/` or `evaluation/` imports a transport, network, GPIO or subprocess library; the only repository-wide hit outside tests is `subprocess` in the project's own `.claude/hooks/secret_scan.py`.

### 20.4 Historical evaluation reproduced (exact numerators and denominators)

Corpus hashes: v1 `a03f494c...c8b0` (280 entries: 112 SPEC_TABLE, 133 SPEC_AUTHORED, 35 DEV_AUTHORED); v2 `2488542b...0ca0` (173 entries: 108 SPEC_TABLE, 65 DEV_AUTHORED, SPEC_AUTHORED empty). Task 4 router rebuilt byte-identically (`6d9c5d30...485ca`). Category accuracy = gold-ROUTED items only (183 on v1, 109 on v2); rejection gold is scored in the outcome-agreement column.

| Corpus / router | Category | Outcome | Macro F1 | False command acceptance | Wrong command | Command fields gold / produced | Invariant / answer violations |
|---|---|---|---|---|---|---|---|
| v1, Task 1 original | 158/183 | 255/280 | 0.946 (6/6) | 0/224 | 0/56 | 54/56, 54/54 | 0 / 0 |
| v1, Task 4 baseline | 174/183 | 271/280 | 0.982 (6/6) | 0/224 | 0/56 | 56/56, 56/56 | 0 / 0 |
| v1, Task 5 current | 174/183 | 271/280 | 0.982 (6/6) | 0/224 | 0/56 | 56/56, 56/56 | 0 / 0 |
| v2, Task 4 baseline | 109/109 | 171/173 | 0.988 (4/6) | 0/164 | 0/9 | 9/9, 9/9 | 0 / 0 |
| v2, Task 5 current | 109/109 | 173/173 | 1.000 (4/6) | 0/164 | 0/9 | 9/9, 9/9 | 0 / 0 |

Per-category precision / recall, v1 Task 5: LOCAL_COMMAND 30/30, 30/30; SENSOR_QUERY 45/45, 45/46; OCCUPANCY_QUERY 21/21, 21/23; CLASSROOM_CONTROL 15/15, 15/15; SMART_HOME 11/11, 11/11; GENERAL_AI 52/52, 52/58. v2 Task 5: LOCAL_COMMAND 9/9, 9/9; SENSOR_QUERY 59/59, 59/59; OCCUPANCY_QUERY 21/21, 21/21; GENERAL_AI 20/20, 20/20 (CLASSROOM_CONTROL and SMART_HOME have no v2 gold, so they are undefined and macro F1 covers 4 of 6). These figures are **same-author development / regression text results, not independent accuracy**: the author wrote the phrase tables, the gold labels and the corrections. They show the router satisfies its own specification and that safety counts are zero; they say nothing about real speech.

### 20.5 Independent review status

**No independent external review of Week 8 exists** in the repository or in any status file. Week 6 had an external (Antigravity) review; Week 7's external reviews are recorded as DEFERRED DUE QUOTA (quota note: until 2026-10-14; no result is recorded since). Every Week 8 review (the Task 4 read-only review, the Task 5 audit and this closeout) was done by the same author or the same assistant and must not be cited as independent. This verdict is a software acceptance check, not an external assurance.

### 20.6 Closeout safety gate result

No blocking issue was found; nothing in production code was changed. Properties confirmed: no hardware action and no execution result exist; no confirmation is allowed on any of the 453 corpus items; stale / missing readings give "unavailable" with no number; a fresh simulated reading is labelled SIMULATED; expected attendance never answers the detected count and the reverse; professor arrival is unavailable in every fixture and presence phrasings are not routed to it; GENERAL_AI cannot be answered; malformed, ambiguous and multiple commands never produce a command; no secret-shaped string or LLM client in production; the runtime never calls the answering engine for a command or an open-ended question; frozen manifests, snapshots, the Task 1 router copy and contracts match their recorded hashes.

**Findings that are not blockers but matter for Week 9 (recorded, not fixed):**
1. **Request ids are not globally unique.** The runtime issues `answer-1`, `answer-2`, ... from a per-instance counter that restarts at 1 whenever the process restarts. Nothing external consumes the id today; once a hardware side correlates or de-duplicates by id (R1, R2), restarts would reuse ids.
2. **Commands are not capability-aware.** The answering `Capability` enum has no command capabilities (nothing like "lights controllable"), and the router accepts "turn on the lights" even in a build with no lights. The AI-side capability declaration for commands must be designed with R5.
3. **No timeout, retry or error vocabulary exists for a hardware link** (`config/settings.py` has no hardware setting), consistent with the absence of any adapter.
4. The `CLASSROOM_CONTROL` target is all-room with no zone; the roadmap's three-zone lighting (Week 10) and the conceptual `SET_LIGHT_ZONE` need an approved vocabulary change.

### 20.7 Known router limitations (each verified by a probe)

A closed phrase set: paraphrases such as "make it louder" and "turn it up" are `UNSUPPORTED`; number words ("fifty percent") are `INVALID_VALUE`; only ASCII digits are accepted and text over 200 characters is rejected. No fuzzy matching, by design. Conservative false rejections: "what is the dark side of the moon" (sensor word "dark"), "what is the difference between attendance and occupancy", "explain why the professor can be late". `ROOM_PRESENCE` and `ROOM_DARK` are classified but not answerable. Open-ended questions about this class that name neither the professor nor attendance (for example "who is the professor of this class") still reach `GENERAL_AI / NO_BACKEND_APPROVED`; harmless today, but a guard is needed before any general backend is approved. Classroom lights are all-or-nothing (no zone, no brightness); the volume step and mute model are undefined; the generic smart bulb implies no real device. Accuracy on STT errors is unknown (no real transcript was ever evaluated).

### 20.8 Week 9 interface readiness: requirements R1-R14

Source: `docs/contracts/WEEK8_COMMAND_SEMANTICS_V1.md` section 4. Nothing below is a design: no packet, pin, baud rate, endpoint, capability or team approval is invented, and every "must agree" cell is a decision for the Hardware / Software teams and the Team Leader.

| # | Requirement | What the AI side already implements | Hardware / Software team must agree | Open dependency | Proposed verification method |
|---|---|---|---|---|---|
| R1 | Command identity and correlation | `request_id` (1-64 chars, validated) is carried by `IntentRequest`, `IntentResult` and `StructuredCommand` and must match; the runtime logs it. **Not globally unique** (per-process counter, 20.6 item 1) | the identifier field, its uniqueness scope across restarts, and what the reply must echo | a unique-id scheme; no reply type exists | contract test: a reply with a wrong or stale id is never matched; simulator test across a simulated restart |
| R2 | Duplicate handling | stateless pure router: the same utterance twice gives two independent commands with separate ids; no dedupe (tested) | whether each action is idempotent or counted (VOLUME_UP twice = twice?), and how two near-simultaneous requests for one target resolve | per-action idempotency semantics; volume-step semantics | simulator test: replay the same id and different ids; assert exactly the agreed effect |
| R3 | Bounded timeout | none (no timeout setting, no hardware wait) | the maximum wait per request and who enforces it | timeout values; link behaviour under load | simulator with delayed / dropped replies; assert the AI side stops waiting at the agreed bound |
| R4 | Target identification | three abstract targets only (`SPEAKER_OUTPUT`, `CLASSROOM_LIGHTS`, `SMART_BULB`); no addresses by design | the mapping from each target to real channels / devices, owned by the hardware team | which lights, which relay or bulb, which amplifier path | a mapping table reviewed by both teams; a test that every AI target resolves or is rejected explicitly |
| R5 | Capability discovery | `CapabilityDeclaration` exists for answers (sensors, lecture info, announcements) and follows the declaration; **no command capabilities** (20.6 item 2) | how the AI side learns which targets / actions exist in this build | a command-capability vocabulary (extends a frozen module, so a separate approval) | test: a declared-absent capability makes the command refused with an honest reply, never confirmed |
| R6 | Invalid-target rejection | unsupported targets and actions are rejected before any request (`UNSUPPORTED`, 12 target / action cases tested) | the hardware-side result for "unknown or unavailable target" | result vocabulary | negative tests against the simulator and later the device |
| R7 | Actual execution acknowledgment | nothing: `ExecutionStatus` has one member; no result type; `spoken_confirmation_allowed` is always false (mutation-tested) | what counts as "applied" (command accepted, actuator changed, or change observed) and the message that carries it | the result contract itself | contract test: no success wording can be produced without a result of the agreed type; later a hardware-observed run |
| R8 | Failure and unavailable states | rejection replies only; no hardware-failure reply | distinct results for rejected, failed, device unavailable, not supported | error codes | one fixed, truthful reply per result, checked by tests; wording reviewed |
| R9 | Unknown outcome after timeout | none | that a timeout means "unknown", and whether a status query exists | status-query support on the hardware side | test: a timeout yields "I couldn't confirm", never done and never failed |
| R10 | Asynchronous completion | none | whether actions are "accepted" then "completed", and event delivery | message flow | test: "accepted" is never spoken as "done" |
| R11 | Recovery after connection loss | none | what happens to pending requests on link loss and reconnect | link model | simulator test: pending requests are failed or re-queried, never assumed done |
| R12 | Prevention of false success confirmations | **implemented as a property today**: success cannot be constructed, the notice contains no success wording, the runtime never speaks a confirmation | that success derives only from an R7 result | the R7 result type | existing tests plus mutation: promoting a status or a timeout to success must be killed |
| R13 | Real versus simulated provenance | sensor facts carry `Origin` REAL / SIMULATED / UNKNOWN through fusion into answers; commands carry none | an origin field in every execution result | result contract | test: a simulated result can never be reported as REAL |
| R14 | Sensor values with identity, unit, validity | fusion contracts carry `sensor_id`, `Unit`, `Usability` (FRESH / STALE / INVALID / MISSING / UNCONFIGURED), `Origin`; answers use only FRESH, numeric, correctly-united values | the actual sensor-reading messages from the ESP32 (identity, unit, validity flag); the humidity device is still UNKNOWN | no Pi adapter, no ESP32 protocol | adapter tests against recorded fixtures first; labelled HARDWARE-VALIDATED only after a physical run |

**Priority items the teams must settle first:** (1) the execution acknowledgement (R7) and failure vocabulary (R8), because every spoken confirmation depends on them; (2) command correlation and duplicates (R1, R2) with a unique-id scheme; (3) bounded timeouts and the unknown-outcome rule (R3, R9); (4) hardware capability mapping and missing targets (R4, R5, R6); (5) all-room versus three-zone lighting; (6) volume step and mute semantics; (7) the actual sensor-reading contract (R14) and command-failure reporting (R8).

### 20.9 Decisions required from teammates (none has been made or assumed)

| # | Decision | Owner |
|---|---|---|
| D1 | the transport and the message / framing format for commands and replies | Software + Hardware |
| D2 | the execution-result vocabulary (accepted, applied, rejected, failed, unavailable, unknown) and what "applied" means | Hardware + AI (proposes) |
| D3 | the request-id scheme and the duplicate / idempotency rule per action | Software + Hardware |
| D4 | timeout values per action and the rule after a timeout | Hardware + Software |
| D5 | the mapping of `CLASSROOM_LIGHTS`, `SMART_BULB`, `SPEAKER_OUTPUT` to real channels; which devices exist | Hardware |
| D6 | all-room versus LEFT / CENTER / RIGHT / projector-area zones, and when | Team Leader + Hardware |
| D7 | the volume step, the mute model, what UNMUTE restores, and percent-to-level mapping | Audio Hardware + Software |
| D8 | whether the generic smart bulb is a real, paired device, and the protocol if so (one reliable path first) | Hardware + Team Leader |
| D9 | the sensor-reading messages (identity, unit, validity, origin) and the humidity device | Hardware |
| D10 | link-loss and reconnect behaviour | Software + Hardware |
| D11 | where the Pi-side hardware interface module lives and who owns it | Team Leader |
| D12 | whether a general-purpose backend is ever approved, and its guard (owner decision) | Project owner |

### 20.10 Week 7, Team Leader and tracking

Week 7 stays IN PROGRESS (Tasks 1-4 software complete; Task 5 harness ready, execution pending a real recorded development corpus; external reviews DEFERRED). Team Leader AI-1 COMPLETE, AI-2 to AI-9 NOT COMPLETE, unchanged. Raspberry Pi 5, ReSpeaker, camera and ESP32 validation PENDING HARDWARE.

### 20.11 Proposed status-only correction to CLAUDE.md (NOT applied; needs separate approval)

`CLAUDE.md` "CURRENT PROJECT STATUS" is stale: it says Week 7 "Tasks 3-5 (STT, pipeline, development evaluation) NOT STARTED" while `WEEK7_STATUS.md` records Task 3 and Task 4 COMPLETE — SOFTWARE-VALIDATED and Task 5's harness READY; and it has no Week 8 entry. Exact proposed replacements (status text only):

1. In the Week 7 line, replace `Tasks 3-5 (STT, pipeline, development evaluation) NOT STARTED;` with `Task 3 (STT dependency gate + faster-whisper backend) COMPLETE — SOFTWARE-VALIDATED; Task 4 (wake / VAD / STT pipeline) COMPLETE — SOFTWARE-VALIDATED, HARDENED; Task 5 (recorded development evaluation) NOT COMPLETE — harness READY, execution PENDING a real recorded corpus;`.
2. Insert after the Week 7 line: `Week 8 — Intent + Command Routing: SOFTWARE MILESTONE COMPLETE — SOFTWARE-VALIDATED (Tasks 1-5 and formal closeout 2026-10-17: six official categories, immutable validated structured commands, explicit rejections, deterministic answering and runtime integration; nothing executed and no success confirmation possible). REAL-WORLD INTENT ACCURACY NOT MEASURED; REAL STT EVALUATION PENDING CONSENTED RECORDINGS; HARDWARE ACTION EXECUTION NOT VALIDATED; no independent external review of Week 8 exists — see WEEK8_REPORT.md, WEEK8_STATUS.md, docs/contracts/WEEK8_COMMAND_SEMANTICS_V1.md`.
3. In the paragraph "The next roadmap milestone ...", replace `Week 7 (VAD + STT; IN PROGRESS, Tasks 1-2 software complete, Tasks 3-5 not started), then intent routing, AI-to-hardware control, and complete system integration.` with `Week 7 (VAD + STT; IN PROGRESS, Tasks 1-4 software complete, Task 5 real evaluation pending recordings) and Week 8 (intent routing; SOFTWARE MILESTONE COMPLETE), followed by Week 9 (AI-to-hardware action loop; NOT STARTED) and complete system integration.`
4. In the status table, replace the Week 7 row's `Tasks 3-5 NOT STARTED` with the wording in item 1, and add rows: `Week 8 (intent + command routing) | SOFTWARE MILESTONE COMPLETE — SOFTWARE-VALIDATED; real-world intent accuracy NOT MEASURED; no independent review` and `Hardware action execution (Week 9) | NOT STARTED; requirements R1-R14 await Software / Hardware decisions`.

### 20.12 Recommended first Week 9 task (proposed; not started, not approved)

**Week 9 Task 1 — joint interface decision record and AI-side execution-result contract, SIMULATED only, for ONE path (classroom lights on / off).** Step 1 is a documentation task with the Hardware and Software leads: settle D1-D5 and D11 (transport, result vocabulary, id scheme, timeout, target mapping, ownership). Step 2, only after those decisions, is software-only: a typed execution-result contract on the AI side, a fake hardware interface that can return every agreed result including timeout / unknown, and tests proving that a spoken confirmation exists only for an agreed success. The two AI-side prerequisites to fix first are globally unique request ids and a command-capability declaration. Nothing physical until the Hardware team provides a device and firmware; every result before that is labelled SIMULATED.
