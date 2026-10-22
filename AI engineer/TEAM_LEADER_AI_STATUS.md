# VESTA — Team Leader AI Engineer Milestone Status (Oct 12–23)

Source: Team Leader document "VESTA: Detailed Tasks for Four Roles (Oct 12-23)", AI Engineer list only. Goal: "VESTA reliably hears questions in the room, answers quickly, and can speak announcements."

Full detail: [TEAM_LEADER_AI_REPORT.md](TEAM_LEADER_AI_REPORT.md). Last updated 2026-10-16.

## Two separate tracks

This milestone is SEPARATE from the CLAUDE.md technical roadmap. Completion counts are never mixed.

| Track | Where tracked | Status |
|---|---|---|
| A — CLAUDE.md roadmap, Week 7 (VAD + STT) | [WEEK7_STATUS.md](WEEK7_STATUS.md) | IN PROGRESS: Tasks 1–4 COMPLETE — SOFTWARE-VALIDATED; Task 5 NOT COMPLETE (harness READY, real recorded evaluation PENDING recordings). Row corrected 2026-10-18; it said Tasks 3–5 NOT STARTED until then |
| B — Team Leader Oct 12–23 AI tasks (this file) | here | AI-1 COMPLETE; AI-2 to AI-9 incomplete |

Week 7 Task 3 (STT dependency gate, `SttEngine`, STT backend) is a technical dependency that helps satisfy Team Leader AI-2. It is not the same thing as AI-2, and finishing it would not by itself complete AI-2.

## Status

| ID | Task | Done when | Status |
|---|---|---|---|
| AI-1 | 10-question test set | Test set written | **COMPLETE — TEST SET WRITTEN** (`evaluation/ai_question_test_set_v1.json`, validated by `tests/test_ai_question_test_set.py`) |
| AI-2 | Integrate the new microphone | Live speech is recognised | **NOT COMPLETE** (STT software COMPLETE — SOFTWARE-VALIDATED; Stage A harness READY; Stage A execution PENDING HARDWARE RECORDING; Stage B capture harness SOFTWARE-VALIDATED WITH FAKE INPUT, live microphone NOT VALIDATED, Stage B live execution PENDING PHYSICAL MICROPHONE) |
| AI-3 | Classroom tuning (1 m, 3 m, back, noise) | 8 of 10 questions correct | **NOT COMPLETE / HARDWARE DEPENDENT** |
| AI-4 | End of question → start of spoken answer, baseline | Baseline recorded | **NOT STARTED** |
| AI-5 | Reduce delay | Delay lower than baseline | **NOT STARTED** |
| AI-6 | Spoken announcement (app or schedule) | Message plays clearly | **NOT COMPLETE** (software orchestration SOFTWARE-VALIDATED; trigger integration, project speaker and audibility PENDING) |
| AI-7 | Avoid speech overlap | No overlapping speech in testing | **PARTIAL / PENDING REAL HARDWARE TEST** (software arbitration `AI7_MIDTERM_V1` SOFTWARE-VALIDATED with fake engines; software integration at call-site level COMPLETE, see the AI-8 section; no real speaker test) |
| AI-8 | Sync LISTENING=RED / CHECKING=BLUE / SPEAKING=GREEN | All three states match in a live test | **PARTIAL / PENDING VISION-FACE + HARDWARE LIVE TEST** (state contract SOFTWARE-VALIDATED; nothing rendered, no live synchronization) |
| AI-9 | Fallback sentence when AI cannot connect | A clear fallback works | **NOT COMPLETE** (software fallback SOFTWARE-VALIDATED; real failure trigger, project speaker and audibility PENDING) |
| **Overall milestone** | | | **NOT COMPLETE** (1 of 9) |

## AI-2 software dependencies

| Dependency | Status |
|---|---|
| Audio capture, WAV source, reframer (Week 1/2) | REUSED UNCHANGED — SOFTWARE-VALIDATED; live hardware NOT VALIDATED |
| VAD + utterance segmenter (Week 7 Tasks 1-2, frozen) | REUSED UNCHANGED — SOFTWARE-VALIDATED on synthetic scores; NOT CALIBRATED |
| **STT: `SttEngine` contract + faster-whisper tiny.en backend** (`speech/`) | **COMPLETE — SOFTWARE-VALIDATED** (2026-10-11). Not an accuracy result |
| Stage A harness: `scripts/ai2_stage_a_transcribe.py` (recorded WAV -> WavFileSource -> segmenter -> STT -> evidence record) | **FROZEN / READY — SOFTWARE-VALIDATED** (2026-10-11), synthetic / fake inputs only. Consent policy: a real or possibly-real human recording needs an explicit consent reference (UNKNOWN refused); synthetic audio uses NOT_APPLICABLE |
| Stage A execution: Hardware Engineer recordings -> harness -> transcript + provenance | **PENDING HARDWARE RECORDING** — REAL STAGE A RUN NOT YET PERFORMED (will be RECORDED-HARDWARE AUDIO) |
| Stage B capture harness: `scripts/ai2_stage_b_live.py` (microphone `read_block()` -> capture worker -> bounded queue -> processing worker -> continuity check -> `VoicePipeline` -> evidence record) | **READY — SOFTWARE-VALIDATED WITH FAKE INPUT** (2026-10-16). LIVE MICROPHONE: NOT VALIDATED. Needs the additive position/drop telemetry in `audio/microphone.py` (see the section below) |
| Stage B execution: physical new microphone -> live capture -> wake -> segmenter -> STT -> transcript | **PENDING PHYSICAL MICROPHONE** (the stage that can satisfy "live speech is recognised"); nothing has run on a device |

The STT work is also the technical content of roadmap Week 7 Task 3; it is tracked here for the Team Leader milestone, and the Week 7 status files are unchanged. AI-2 remains NOT COMPLETE.

### Integrity note (2026-10-11 reconciliation)

All 21 frozen files (`audio/*`, `wakeword/*`, `fusion/*`, `speech/*`, `config/settings.py`, `requirements.txt`, `.gitignore`) are PROVEN byte-identical to SHA-256 values recorded before the Stage A work began (full table: `TEAM_LEADER_AI_REPORT.md` section 4.7). Limitation: a line-ending incident also rewrote 17 non-frozen files (`CLAUDE.md`, WEEK4-6 reports/status, `vision/*`, some older tests); CRLF was restored without a pre-edit hash, so their byte identity is UNVERIFIABLE (their content was not edited). `WEEK7_REPORT.md` and `WEEK7_STATUS.md` were deliberately edited and their original line endings are also unverifiable.

## Stage B capture harness (software-only, 2026-10-16)

STAGE B CAPTURE HARNESS: SOFTWARE-VALIDATED WITH FAKE INPUT; LIVE MICROPHONE: NOT VALIDATED; TEAM LEADER AI-2: NOT COMPLETE.

| Item | Status |
|---|---|
| `scripts/ai2_stage_b_live.py` (development harness; run: `python -m scripts.ai2_stage_b_live --out-dir <dir outside the repo>`) | READY — SOFTWARE-VALIDATED WITH FAKE INPUT (96 new tests incl. 25 microphone-telemetry tests; 53 mutants on a scratch copy, all killed). NOT run on a microphone |
| Additive telemetry in `audio/microphone.py`: `read_block()` -> `MicBlock` (exact position, epoch, overflow flag), `telemetry()` counters, optional `queue_max_blocks`; `read_frame()` and the `AudioSource` interface unchanged | SOFTWARE-VALIDATED with PortAudio replaced. A frozen file, changed deliberately and only by this addition; every other file in the pre-change SHA-256 baseline (1182 files) is byte-identical |
| Wake activation | The wake word must fire; no silent bypass. `--activation manual` is an explicit DEVELOPMENT mode whose results and label say MANUAL_DEVELOPMENT / NOT WAKE-ACTIVATED |
| Segmenter settings | Harness-local, DEVELOPMENT, UNCALIBRATED (Stage A values plus a 2 s pre-roll); `config/settings.py` unchanged |
| Loss handling | Every microphone-queue drop, harness-queue drop, overflow flag, epoch change or leading/trailing loss is a logged structural discontinuity; the open utterance is discarded WITHOUT transcription and `VoicePipeline.restart_stream()` is called. A device overflow has an UNKNOWN size and is never estimated. Loss below PortAudio that it does not flag cannot be detected by software |
| Privacy | Logs and evidence hold no transcripts or PCM; transcripts only with `--show-transcript` or an explicit external `--transcripts-out` file; evidence is never written inside the repository and never overwritten |
| Hardware dependencies still open | ReSpeaker device name / channel layout, PortAudio behaviour on the Pi 5, real wake accuracy, VAD thresholds, STT speed on the Pi |

This does not complete AI-2 ("live speech is recognised") and is not a Week 7 item; Week 7 stays IN PROGRESS with Task 5 real evaluation pending recordings. Detail: `TEAM_LEADER_AI_REPORT.md` section 10.

## End-to-end software composition (2026-10-16)

AI END-TO-END COMPOSITION: SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT. REAL LIVE VOICE ASSISTANT: NOT VALIDATED. AI-2: NOT COMPLETE — HARDWARE PENDING.

| Item | Status |
|---|---|
| `assistant/runtime.py` — `VoiceAssistantRuntime`, the ONE composition root: `VoicePipeline` -> `DeterministicAnswerEngine` -> `OutputArbiter` -> TTS -> playback, with the one `AssistantStateManager` | SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT (59 tests; 29 runtime mutants, 28 killed, the 1 survivor an intentional canary). No STT, TTS, VAD, wake or arbitration logic of its own; no network, LLM or model download |
| `python -m scripts.vesta_runtime_demo [--show-text] [--tts real]` | executable development demo: scripted fake wake / VAD / STT, fake playback (real local voice optional, synthesis only); deterministic; room information deliberately UNAVAILABLE |
| States | LISTENING (RED) at the wake detection, CHECKING (BLUE) when the finished utterance is handed to STT, SPEAKING (GREEN) at the playback engine's first buffer, IDLE at the end; never faked, never forced (a refused transition is counted) |
| Not a question | WAKE_ONLY, NO_SPEECH, TOO_SHORT, STT_EMPTY, INVALID_ACTIVATION are not answered; MAX_DURATION and STREAM_ENDED (possibly truncated) are never treated as complete commands |
| Failures | only an explicit `ExpectedServiceFailure` (or a typed STT failure) leads to the spoken offline fallback; any other exception is a bug: the state returns to IDLE and the exception propagates; an arbiter rejection is returned as is, with no TTS and no playback |
| Stage B composition | the harness accepts the runtime's pipeline and a discontinuity callback (`pipeline=`, `on_discontinuity=`); a capture gap ends LISTENING and the damaged utterance is never transcribed or answered. Tested with a fake block source only |
| Not built | a LIVE RUNNER (real microphone + real engines + real playback + the runtime), a real room-state source, a schedule or professor-arrival source, half-duplex muting during playback, a Face/LED subscriber |

The AI-6 / AI-7 / AI-8 / AI-9 hardware acceptance statuses above are unchanged: this is software orchestration with fakes. AI-3, AI-4 and AI-5 are not started or not complete. Week 7 stays IN PROGRESS (Task 5 real evaluation pending recordings). Detail: `TEAM_LEADER_AI_REPORT.md` section 11.

## Answering foundation (midterm software prerequisite, 2026-10-11)

| Item | Status |
|---|---|
| **ANSWERING FOUNDATION** (`assistant/answering.py`: recognised text -> frozen AI-1 question match -> supplied state -> truthful TEXT answer) | **SOFTWARE-VALIDATED** on SIMULATED fixtures. Not classroom validated |
| AI-1 ten questions, software / simulated evaluation | 10 / 10 PASS on the simulated fixture; missing-data run fabricates nothing. This is NOT the AI-3 8-of-10 result |
| Spoken output, playback, announcements, latency | NOT STARTED (no TTS; AI-4, AI-6, AI-9 untouched) |
| Information sources that still do not exist | schedule / current lecture / expected attendance / next class, professor arrival, announcement playback: answered as UNAVAILABLE (or by declaration) |

This work does not complete AI-2, AI-3, AI-4 or AI-6 and is not a Week 7 item.

## Spoken-output foundation (software-only, 2026-10-11)

| Item | Status |
|---|---|
| **SPOKEN-OUTPUT TTS** (`speech/tts.py`, `speech/sherpa_onnx_tts.py`: text -> mono int16 `SpeechAudio`, sherpa-onnx 1.13.8, voice en_US-lessac-medium) | **SOFTWARE-VALIDATED** (Windows development PC; offline; no quality, naturalness or Pi claim) |
| **PLAYBACK ENGINE** (`audio/playback.py`: `SpeechAudio` -> sounddevice `OutputStream` -> `PlaybackResult`) | **SOFTWARE/API-VALIDATED — PENDING PROJECT HARDWARE** (fake-device tests plus one gated silent smoke on the development PC; no speaker, DAC, Pi, audibility or volume claim) |
| Voice package | ACADEMIC / NON-COMMERCIAL DEVELOPMENT ASSET: not committed, not redistributed (Lessac dataset non-commercial; bundled espeak-ng data GPL-family) |
| AI-4 (spoken-answer latency) | NOT COMPLETE — `t_first_buffer_submitted` is available as the SOFTWARE PROXY for the start of the spoken answer; nothing measured end to end |
| AI-6 (spoken announcements) | NOT COMPLETE — same TTS and playback stack is ready for it; no trigger, schedule or app path |
| AI-9 (spoken fallback) | NOT COMPLETE — a fixed sentence synthesizes offline in a test; no fallback logic, no physical speech |

This work is not a Week 7 item and completes none of AI-2, AI-3, AI-4, AI-6 or AI-9.

## AI-6 / AI-9 software orchestration (2026-10-11)

| Item | Status |
|---|---|
| **AI-6 SOFTWARE ORCHESTRATION** (`assistant/spoken_output.py`: `AnnouncementRequest` -> same TTS -> same playback -> `SpokenOutputResult`) | **SOFTWARE-VALIDATED** (fake TTS / fake playback; one real-voice test needs no playback) |
| **AI-6** (spoken announcement from app or schedule; done when the message plays clearly) | **NOT COMPLETE — PROJECT SPEAKER / TRIGGER VALIDATION PENDING** |
| **AI-9 SOFTWARE FALLBACK** (explicit upstream failure -> fixed offline sentence -> same TTS -> same playback) | **SOFTWARE-VALIDATED** (synthesized offline with sockets disabled; nothing played) |
| **AI-9** (spoken fallback when the AI cannot connect; done when a clear fallback works) | **NOT COMPLETE — PROJECT HARDWARE AUDIBILITY VALIDATION PENDING** |
| AI-7 (no overlap) | SUPERSEDED by the AI-7 section below (at this point the controller only rejected a second request while one was active; no priority policy) |

Team dependencies for final completion. **AI-6** needs: an app or schedule trigger integration (APP TRIGGER INTEGRATION PENDING TEAM DEPENDENCY: nothing in this repository sends announcements yet), the project speaker, real playback, and a clearly audible result. **AI-9** needs: a real failure-path trigger (an upstream service failure classified by the caller), the project speaker, and a clearly audible fallback. COMPLETED in a software result means only that the playback engine reported completion.

## AI-7 software arbitration (2026-10-11)

| Item | Status |
|---|---|
| Policy | **`AI7_MIDTERM_V1`** (recorded in every `ArbitrationResult.policy_version` and every arbitration log line) |
| Priority | **CONVERSATION (ANSWER, FALLBACK) > ANNOUNCEMENT.** ANSWER and FALLBACK are equals: first arrival wins. No numeric priorities; the class follows the request TYPE, never the text |
| Preemption | **NON-PREEMPTIVE once an output is active** (synthesizing or playing). A conversation request meeting an active announcement gets RETRY_REQUIRED, not an interruption |
| Queueing | **NO HIDDEN QUEUE.** A rejected request is returned to its caller; nothing is replayed later |
| Concurrency | **AT MOST ONE SPOKEN OUTPUT ACTIVE** across synthesis + playback: no parallel TTS, no overlapping playback |
| Stop race | Closed in software: a cancel flag under the arbitration lock; a stop during synthesis or just before playback means playback is never called (STOPPED) |
| Code | `assistant/output_arbiter.py` (new); `tests/test_output_arbiter.py` (82 tests). No existing module changed |
| **AI-7 SOFTWARE ARBITRATION** | **SOFTWARE-VALIDATED** (fake TTS / fake playback; real threads with barriers and events; nothing played) |
| **Team Leader AI-7** (no overlapping speech in testing) | **PARTIAL / PENDING REAL HARDWARE TEST.** Not validated: the project speaker, real playback cancellation, audibility, a live app / schedule / voice trigger |
| AI-6, AI-9 | unchanged: **NOT COMPLETE** |

Remaining for AI-7 to be complete: wire the real callers (answer path, announcement trigger, fallback trigger) through the single `OutputArbiter`, then run an announcement and a conversational answer against the project speaker and confirm by listening that nothing overlaps and that a stop during real playback works.

## AI-8 software state synchronization (2026-10-11)

| Item | Status |
|---|---|
| Call-site audit (non-test code) | Every path to speech goes through `OutputArbiter`; **no runtime orchestrator exists yet** (the engines and the arbiter are built only by tests). Guarded by a test that pins the full call-site table |
| **AI-7 SOFTWARE INTEGRATION** | **COMPLETE at call-site level** (the controller is built only by the arbiter and reaches playback only through its guard). Not a runtime composition root: that is still to be written |
| **Team Leader AI-7** | **PARTIAL / PENDING REAL HARDWARE TEST** (unchanged) |
| Contract | `assistant/state.py`: `AssistantState`, `StateColor`, `StateReason`, `StateEvent`, `AssistantStateManager` |
| Mapping (pinned by tests) | **LISTENING = RED, CHECKING = BLUE, SPEAKING = GREEN, IDLE = NO_ACTIVE_COLOR** (no authoritative idle colour exists, none invented) |
| Transitions | IDLE -> LISTENING / CHECKING; LISTENING -> CHECKING / IDLE; CHECKING -> SPEAKING / IDLE; SPEAKING -> IDLE. Every spoken output (answer, announcement, fallback) goes IDLE/LISTENING -> CHECKING -> SPEAKING -> IDLE; no IDLE -> SPEAKING shortcut; LISTENING is never faked |
| SPEAKING entry | The playback engine's FIRST-BUFFER signal (first samples in the device buffer), never admission, never TTS completion. Needed one additive, backward-compatible change to `audio/playback.py` (an optional listener; engines without it are refused by a state-publishing arbiter) |
| Delivery | In-process, offline, synchronous, ordered, sequence-numbered; a broken subscriber is caught, logged and counted and never changes the state |
| Code | `assistant/state.py` (new); `tests/test_assistant_state.py` (60) and `tests/test_state_integration.py` (39) (new); additive edits to `assistant/output_arbiter.py` and `audio/playback.py` |
| **AI-8 STATE CONTRACT** | **SOFTWARE-VALIDATED** (fake engines, fake Face subscriber; nothing played, nothing rendered) |
| **Team Leader AI-8** (all three states match live with the Vision/Face side) | **PARTIAL / PENDING VISION-FACE + HARDWARE LIVE TEST** |
| AI-6, AI-9 | unchanged: **NOT COMPLETE** |

Remaining for AI-8: a Vision/Face subscriber that renders the colours (their side), a runtime orchestrator that drives LISTENING / CHECKING from the real input pipeline, the project speaker, and a live test in which the three states are seen to match.

## Shared deadlines (from the Team Leader document)

- Oct 16: first integration check — "the microphone works with the AI". **Not met**: the STT engine now exists (software-validated) but no microphone has been connected to it; no live capture has run. (2026-10-16: the Stage B capture harness is prepared and software-validated with fake input; still no microphone connected.)
- Oct 21: code freeze. Oct 22: full rehearsal. Oct 23: midterm target, 60% of project ready.
- Leader's fallback if time runs short: one fixed announcement instead of several.

## AI-1 facts to remember

- Specification only: 10 questions (4 ROOM, 3 LECTURE, 3 GENERAL), version v1, frozen, NOT an accuracy benchmark.
- Room answers (Q1–Q4) are scored against a SIMULATED fixture, not live sensors.
- Q5, Q6, Q7, Q9 have no information source today; the v1 oracle is an honest "unavailable" answer. A pass there is not schedule or professor-arrival capability.
- Q8 and Q10 are scored against a capability declaration written before each run. Q10 does not mark announcements implemented.
- Changes after any run require a new version (v2), never an edit of v1.

## Test history for this track

| Run | Result | Note |
|---|---|---|
| 2026-10-09 | **1157 passed, 8 skipped, 0 failed** | 1147 existing + 10 AI-1 artifact tests. Same environmental skips (4 ReSpeaker hardware, 4 openWakeWord real-engine blocked by Windows Application Control) |
| 2026-10-11 (AI-2 STT) | **1279 passed, 5 skipped, 0 failed** | 1160 + 119 new STT tests (63 in `test_stt_contract.py`, 56 in `test_faster_whisper_backend.py`, including 5 real-model tests). Skips: 4 ReSpeaker hardware, 1 recorded-clip wake word. The earlier 1157 / 8 baseline had 3 more skips: the openWakeWord real-engine tests ran again on 2026-10-11 (the Windows Application Control block on an sklearn DLL was no longer in effect; not repaired by us) |
| 2026-10-11 (AI-2 Stage A harness) | **1348 passed, 5 skipped, 0 failed** | 1279 + 69 new harness tests (67 run anywhere, 2 plug the real Silero / tiny.en models into the harness on synthetic audio). Same 5 skips |
| 2026-10-11 (AI-2 consent policy) | **1369 passed, 5 skipped, 0 failed** | 1348 + 21 new consent tests (the harness test file now has 90 tests). Same 5 skips |
| 2026-10-11 (answering foundation) | **1476 passed, 5 skipped, 0 failed** | 1369 + 107 new answering tests (`tests/test_answering.py`). Same 5 skips |
| 2026-10-11 (spoken-output foundation) | **1649 passed, 6 skipped, 0 failed** | 1476 + 173 new tests (62 TTS contract, 39 sherpa backend incl. real-voice tier, 62 playback, 10 integration). Skips: the previous 5 plus 1 gated real-device smoke (VESTA_PLAYBACK_API_SMOKE=1, silence) |
| 2026-10-11 (AI-6 / AI-9 orchestration) | **1742 passed, 6 skipped, 0 failed** | 1649 + 93 new tests (`tests/test_spoken_output_controller.py`). Same 6 skips |
| 2026-10-11 (AI-7 arbitration) | **1824 passed, 6 skipped, 0 failed** | 1742 + 82 new tests (`tests/test_output_arbiter.py`). Same 6 skips |
| 2026-10-11 (AI-8 state contract) | **1923 passed, 6 skipped, 0 failed** | 1824 + 99 new tests (60 in `test_assistant_state.py`, 39 in `test_state_integration.py`). Same 6 skips |
| 2026-10-16 (AI-2 Stage B capture harness) | **2494 passed, 6 skipped, 0 failed** | 2398 (the project baseline at the start of this work: the rows above plus the Week 7 Tasks 3-5, hardening and hand-off documentation tests, tracked in `WEEK7_STATUS.md`) + 96 new tests (25 in `test_microphone_telemetry.py`, 71 in `test_stage_b_live.py`; helpers in `tests/stage_b_support.py`). Same 6 skips |
| 2026-10-16 (end-to-end software composition) | **2561 passed, 6 skipped, 0 failed** | 2494 + 67: 59 in `test_runtime.py`, 4 in `test_stage_b_live.py` (prebuilt pipeline, discontinuity callback), and 4 parametrized cases in `test_handoff_docs.py` (one per documented command). `test_state_integration.py` keeps its test count; two call-site audit tests were updated deliberately for the new composition root. Same 6 skips |
