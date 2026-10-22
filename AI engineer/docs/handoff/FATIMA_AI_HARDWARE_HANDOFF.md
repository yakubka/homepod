# VESTA — AI subsystem: hardware-integration handoff

**Status: DOCUMENTATION-READY SOFTWARE HANDOFF. NOT a runnable hardware demonstration.** Every module listed here passes its software tests. A development live-capture harness (Stage B, `scripts/ai2_stage_b_live.py`) and a software composition root (`assistant/runtime.py`, demo: `python -m scripts.vesta_runtime_demo`) now exist, both SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT only. Since 2026-10-18 a **live runner** (`scripts/vesta_live_voice.py`) constructs the real microphone, the real engines, real playback and the runtime together; it is tested only with injected fakes and has **never run on a device**, so the voice-in to voice-out chain is READY FOR PHYSICAL TEST, not working on hardware. (Historical record: until 2026-10-18 this document stated that no live runner existed; that was true when written.) Nothing in this repository is HARDWARE-VALIDATED or LIVE-validated.

Written for the Hardware Engineer and the team. Owner of the AI side: the AI Engineer. Two tracking layers are kept SEPARATE and never mixed:

| Layer | Where | State |
|---|---|---|
| Week 7 roadmap (VAD + STT) | `WEEK7_STATUS.md`, `WEEK7_REPORT.md` | Tasks 1-2 COMPLETE; Task 3 COMPLETE — SOFTWARE-VALIDATED; Task 4 COMPLETE — SOFTWARE-VALIDATED, HARDENED; Task 5 evaluation harness READY, real evaluation PENDING; **Week 7 IN PROGRESS** |
| Team Leader Oct 12-23 AI tasks | `TEAM_LEADER_AI_STATUS.md`, `TEAM_LEADER_AI_REPORT.md` | AI-1 COMPLETE; AI-2 to AI-9 NOT complete (1 of 9) |

Same-author reviews only: the audit and hardening that preceded this handoff were done by the same author. An independent external (Antigravity) review is DEFERRED.

---

## 1. What is validated, and at which level

Validation levels used everywhere: **SOFTWARE-VALIDATED** (unit/integration tests with fixtures), **SIMULATED** (fake hardware or fixtures standing in for it), **RECORDED-HARDWARE AUDIO** (a real file recorded by the Hardware Engineer, replayed through the software), **HARDWARE-VALIDATED / LIVE** (observed working on the physical device; reached by nothing yet).

| Capability | Code | Level today |
|---|---|---|
| Audio source abstraction, WAV replay, re-chunking | `audio/base.py`, `audio/file_source.py`, `audio/reframer.py` | SOFTWARE-VALIDATED |
| ReSpeaker capture backend | `audio/microphone.py` | written; tests SKIP without the device; **NOT hardware-validated** |
| Wake word (openWakeWord, `hey_jarvis` development phrase) | `wakeword/` | SOFTWARE-VALIDATED; accuracy NOT MEASURED |
| VAD (Silero v6.2.3) + wake-activated utterance segmenter | `audio/vad.py`, `audio/silero_vad.py`, `audio/utterance.py` | SOFTWARE-VALIDATED; thresholds NOT CALIBRATED |
| STT (faster-whisper tiny.en, English only) | `speech/stt.py`, `speech/faster_whisper_backend.py` | SOFTWARE-VALIDATED; accuracy NOT MEASURED; hallucination NOT VALIDATED |
| Wake + VAD + STT pipeline, wake-prefix normalisation | `speech/pipeline.py` | SOFTWARE-VALIDATED, HARDENED (scripted + synthetic plumbing only) |
| Week 7 Task 5 evaluation harness | `evaluation/week7_voice_eval.py`, `week7_voice_metrics.py`, `week7_calibration.py` | READY, SOFTWARE-VALIDATED; never run on real data |
| Team Leader AI-2 Stage A harness | `scripts/ai2_stage_a_transcribe.py` | READY; real Stage A run NOT performed |
| Deterministic question answering (text) | `assistant/answering.py` | SOFTWARE-VALIDATED on a SIMULATED fixture |
| TTS (sherpa-onnx, Lessac voice) | `speech/tts.py`, `speech/sherpa_onnx_tts.py` | SOFTWARE-VALIDATED (offline) |
| Playback | `audio/playback.py` | SOFTWARE/API-VALIDATED; project speaker NOT tested |
| Announcement / offline-fallback orchestration | `assistant/spoken_output.py` | SOFTWARE-VALIDATED (fake TTS/playback) |
| Overlap arbitration (`AI7_MIDTERM_V1`) | `assistant/output_arbiter.py` | SOFTWARE-VALIDATED |
| LISTENING / CHECKING / SPEAKING state contract | `assistant/state.py` | SOFTWARE-VALIDATED (fake subscriber) |

## 2. Entry-point audit: what can be executed today

Checked against the repository and the actual `--help` output of each command.

| ID | Workflow | Implementation | Runnable command or API | Needs | Test evidence | Missing integration | Hardware dependency | Validation level |
|---|---|---|---|---|---|---|---|---|
| A | Hardware recording -> WAV -> VAD -> STT -> evidence | `scripts/ai2_stage_a_transcribe.py` | `python -m scripts.ai2_stage_a_transcribe run --wav <wav> --provenance <json> --out-dir <external dir>` then `... review <result> --human-match MATCH\|PARTIAL\|MISMATCH\|EMPTY` | Silero + tiny.en models; a native 16 kHz mono 16-bit WAV; a provenance file | `tests/test_ai2_stage_a_harness.py` | none for what it does; it activates at sample 0 (no wake word) and handles one phrase per file | needs recordings from the Hardware Engineer | RECORDED-HARDWARE AUDIO once run on a real file; **not run yet** |
| B | WAV -> wake detector -> segmentation -> STT -> normalised text | `speech/pipeline.py` (`VoicePipeline.run_source`) | **API only.** Indirectly through `python -m evaluation.week7_voice_eval run ...` (see section 9) | Silero + tiny.en (+ openWakeWord for `--wake-mode detector`) | `tests/test_voice_pipeline.py`, `tests/test_voice_pipeline_real.py`, `tests/test_week7_hardening.py` | no standalone CLI; no live source | recordings for real data | SOFTWARE-VALIDATED; real data NOT used |
| B2 | WAV -> wake detector only | `wakeword/openwakeword_backend.py` | `python -m wakeword.openwakeword_backend <wav> [--model] [--threshold] [--cooldown] [--framework] [--realtime]` | openWakeWord model files (section 5) | `tests/test_openwakeword_backend.py` | none | a real "hey jarvis" recording for the gated test (`VESTA_HEY_JARVIS_WAV`) | SOFTWARE-VALIDATED |
| C | Physical microphone -> live capture -> voice pipeline -> transcript ("Stage B") | `scripts/ai2_stage_b_live.py`, `audio/microphone.py` (additive position / drop telemetry) | `python -m scripts.ai2_stage_b_live --out-dir <dir outside the repo> --duration-s 120` (on the device; `--activation manual` is a DEVELOPMENT-only mode). Also `python record_demo.py [seconds] [output_path]` records a WAV | `sounddevice`, the ReSpeaker on the Pi, the three local models | `tests/test_stage_b_live.py`, `tests/test_microphone_telemetry.py` (fake input; PortAudio replaced) | **transcribes only** (no answering or speech); the runtime is connected to it only in tests; nothing has run on a microphone | the physical microphone | SOFTWARE-VALIDATED WITH FAKE INPUT; LIVE MICROPHONE: NOT VALIDATED |
| D | Recognised text -> answer text | `assistant/answering.py` (`DeterministicAnswerEngine`) | **API only** (`AnswerRequest` in, `AnswerResult` out) | a supplied `AnswerContext` (room state) | `tests/test_answering.py`, `tests/test_ai_question_test_set.py` | no CLI; no real room-state source (the sensor interface to the ESP32 is NOT AVAILABLE); schedule/professor data do not exist | sensors for real room answers | SOFTWARE-VALIDATED on a SIMULATED fixture |
| E | Answer text -> TTS -> playback | `speech/sherpa_onnx_tts.py`, `audio/playback.py` | **API only.** Gated check: `VESTA_PLAYBACK_API_SMOKE=1 python -m pytest tests/test_playback.py -k real_sounddevice -q` plays SILENCE on the default device | Lessac voice files (section 5), `sounddevice` | `tests/test_sherpa_onnx_tts.py`, `tests/test_playback.py` | no CLI; no composition root; output device is chosen by the caller (default `None` = platform default) | the project speaker | SOFTWARE/API-VALIDATED; speaker NOT tested |
| F | Trigger -> arbiter -> spoken announcement / fallback | `assistant/spoken_output.py`, `assistant/output_arbiter.py` | **API only** (`OutputArbiter.submit_answer / submit_announcement / submit_fallback`) | TTS + playback engines | `tests/test_spoken_output_controller.py`, `tests/test_output_arbiter.py` | **no trigger** (app or schedule) and no real failure classifier (the composition root `assistant/runtime.py` exists, FAKE input/output only) | the project speaker | SOFTWARE-VALIDATED (fake engines) |
| G | LISTENING / CHECKING / SPEAKING -> Face/LED | `assistant/state.py` | **API only** (`AssistantStateManager.subscribe`) | a subscriber | `tests/test_assistant_state.py`, `tests/test_state_integration.py` | **no Vision/Face subscriber, no transport, nothing drives LISTENING/CHECKING from a real input** | the Face/LED hardware and its owner | SOFTWARE-VALIDATED (fake subscriber) |

Also runnable (support tools): `python -m evaluation.week7_voice_eval validate|run`, `python -m evaluation.week7_calibration`, `python -m evaluation.ingest init|ingest|annotate|validate` (the older wake-word corpus tool).

## 3. Missing runtime components (genuine integration gaps)

1. **Composition root and live runner: built in software only.** `assistant/runtime.py` wires `VoicePipeline`, the deterministic answer engine, the `OutputArbiter` (and with it the one TTS / playback pair) and the one `AssistantStateManager`; `python -m scripts.vesta_runtime_demo` runs it with fake wake / VAD / STT and fake playback. The live runner `scripts/vesta_live_voice.py` (2026-10-18) starts the real microphone, the real engines and real playback around it, but has never run on a device, and **no real room-state source** exists to give it (the ESP32 sensor adapter is NOT AVAILABLE). The chain is SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT; it is **not** a working assistant on hardware.
2. **Live-microphone harness (Stage B): built, never run on a device.** `scripts/ai2_stage_b_live.py` has a capture worker, a bounded hand-off queue, discontinuity reporting and `restart_stream()` recovery, on top of additive telemetry in `audio/microphone.py`. It is SOFTWARE-VALIDATED WITH FAKE INPUT and LIVE MICROPHONE: NOT VALIDATED. It records structural evidence and can transcribe, but it does not answer or speak by itself.
3. **No announcement trigger** (app or schedule) and **no real upstream-failure classifier** for the offline fallback.
4. **No Face/LED subscriber or transport.** The runtime emits LISTENING / CHECKING / SPEAKING from real software events (the wake detection, the hand-off of the finished utterance to STT, the playback engine's first buffer) with fake input; nothing renders the colours and nothing has been observed live.
5. **No real room-state source** (sensor interface with the ESP32 is NOT AVAILABLE); schedule, expected attendance and professor-arrival data do not exist.
6. Production segmentation values are unset (`config/settings.py` holds `None` for the VAD/segmenter keys).

Do not read passing unit tests as a ready demo. The smallest separately approved step that would unblock a genuine live test is in section 14.

## 4. Hardware blockers

| # | Issue | Evidence in the code | Classification |
|---|---|---|---|
| 1 | The microphone queue drops the OLDEST block silently when full (50 blocks); the additive telemetry now counts every drop and stamps each block with its exact position, but this is verified only with PortAudio replaced | `audio/microphone.py` `_callback`, `read_block()`, `telemetry()` | **HARDWARE-ONLY VALIDATION** (blocks the live test until the counters are seen on the device) |
| 2 | STT is synchronous: a Whisper decode blocks whoever called `feed()`; the Stage B harness therefore reads the microphone on a separate capture worker with a bounded queue (400 blocks = 25.6 s of audio, about 0.8 MB), and a full queue is reported as a discontinuity, never hidden | `speech/pipeline.py`, `speech/stt.py` ("no timeout, no cancellation"), `scripts/ai2_stage_b_live.py` | **HARDWARE-ONLY VALIDATION** (STT speed on the Pi is unmeasured; no latency bound is claimed) |
| 3 | Live sample continuity: positions are counted from the frames PortAudio delivers and any gap is detected exactly, but a PortAudio overflow flag has no size and loss below PortAudio that it does not flag cannot be detected by software | `audio/microphone.py`, `scripts/ai2_stage_b_live.py` | **HARDWARE-ONLY VALIDATION** |
| 4 | The real ReSpeaker XVF3800 device name hint, channel layout and processing mode are unknown; a 1-channel 16 kHz request may not be supported | `config/settings.py` comments | **HARDWARE-ONLY VALIDATION** (blocks Test B) |
| 5 | Production VAD/segmentation thresholds are uncalibrated | `config/settings.py` (`None`) | **NONBLOCKING DEVELOPMENT LIMITATION** for a first live smoke test; needed before any acceptance claim (Task 5 DEV data) |
| 6 | Pi 5 runtime/dependency compatibility is unverified (onnxruntime, ctranslate2/faster-whisper, sherpa-onnx, av, openwakeword on aarch64; Whisper speed on the Pi) | `requirements.txt` pins; no Pi run exists | **HARDWARE-ONLY VALIDATION** (blocks all Pi tests) |
| 7 | Project speaker playback is unverified (voice is 22,050 Hz; a device that cannot run that rate raises `PlaybackFormatError`; default device is only a development convenience) | `audio/playback.py` | **HARDWARE-ONLY VALIDATION**; **TEAM DEPENDENCY** (audio hardware) |
| 8 | Face/LED subscriber and transport are absent | `TEAM_LEADER_AI_REPORT.md` section 9.14 | **TEAM DEPENDENCY** (Vision/Face + hardware) |
| 9 | No announcement trigger, no sensor data source | see section 3 | **TEAM DEPENDENCY** |
| 10 | STT may emit text for silence or noise; the wake engine needs ~0.4 s of warm-up after any reset | `WEEK7_REPORT.md` 15.9; `evaluation/manifest.py` (`MIN_LEAD_IN_S`) | **NONBLOCKING DEVELOPMENT LIMITATION** (measure with Task 5 controls) |
| 11 | Wake-word accuracy and false-activation rate are NOT MEASURED | `WEEK2_REPORT.md` | **NONBLOCKING DEVELOPMENT LIMITATION** for STT tests; needed for any wake claim |

**Policy: never work around a discontinuity silently.** If audio is lost, the interaction is abandoned and reported; it is never joined across the gap.

## 5. Live-capture design (IMPLEMENTED in software 2026-10-16; live behaviour NOT VALIDATED)

1. A **capture worker** reads `ReSpeakerMicrophone.read_block()` and does nothing else; each block carries its exact position in the callback's own sample count (never a clock). No inference runs on it.
2. A **bounded hand-off queue** (default 400 blocks = 25.6 s = 819,200 bytes of PCM at 1024 samples / 16 kHz) feeds a **processing worker** that runs the frozen `VoicePipeline`; STT runs only there, so capture keeps reading while it decodes. Queue sizing and backlog: audio waits behind a slow STT or a spoken answer for up to 25.6 s, after which the harness drops the newest blocks and reports the exact loss. A full backlog was measured at about 1.6 s to drain on a Windows development PC with the real wake engine and no speech; that is NOT a Raspberry Pi figure and no latency bound is claimed.
3. **Discontinuity reporting:** a position gap or overlap, an epoch change, leading or trailing loss and a PortAudio overflow flag each become a structured event with a counter; the overflow flag has an unknown size and is never estimated.
4. **Reset behaviour:** on a discontinuity the open utterance is discarded WITHOUT transcription, `restart_stream()` is called and nothing from before the gap is replayed or joined.
5. A decode or answer longer than the queue can cover is itself reported as a discontinuity.
6. The only change to a frozen file was the additive, backward-compatible telemetry in `audio/microphone.py` (`read_frame()` and the `AudioSource` interface are unchanged).

## 6. Environment setup

Development reference: Windows 11, Python 3.13, a project virtual environment. **Raspberry Pi 5: NOT VERIFIED.**

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

`requirements.txt` lists `sounddevice==0.5.6`, `numpy` (deliberately unpinned), `pytest==9.1.1`, `openwakeword==0.6.0`, `onnxruntime==1.30.0`, `pillow==12.3.0`, `faster-whisper==1.2.1` and `sherpa-onnx==1.13.8` (`sounddevice` and `pytest` pinned 2026-10-18 to the versions installed on the development PC). `numpy` stays unpinned until the first Pi install: the development PC's numpy 2.5.3 requires Python 3.12 or newer, and Raspberry Pi OS Bookworm ships Python 3.11, so record the Pi's Python version and the numpy version pip resolves there, then pin to that. Whether each of these installs and runs on Raspberry Pi 5 / aarch64 (and the PortAudio system library that `sounddevice` needs) has **not** been checked: record the result of the first install attempt as evidence. Do not edit pins to make an install succeed without telling the AI Engineer.

Use one fresh virtual environment per machine. Keep model files and every recording OUTSIDE Git (section 7 and `docs/handoff/EVIDENCE_INDEX.md`).

## 7. Model acquisition and offline operation

All models are fetched **once**, by an explicit developer step. The runtime never downloads anything and verifies every file by size and SHA-256 before loading. Model binaries are git-ignored and are never committed.

| Model | Source (pinned) | Local location | SHA-256 | Licence |
|---|---|---|---|---|
| Silero VAD v6.2.3 (ONNX) | github.com/snakers4/silero-vad, commit `5cd7945676eb32225748052e2e6a0580e4686a08`, file `src/silero_vad/data/silero_vad.onnx`; 2,327,524 bytes | `models/silero_vad_v6.2.3.onnx` | `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` | MIT |
| faster-whisper tiny.en | Hugging Face `Systran/faster-whisper-tiny.en`, revision `0d3d19a32d3338f10357c0889762bd8d64bbdeba`; 4 files, 78,090,594 bytes | `models/faster-whisper-tiny.en-0d3d19a3/` | see below | MIT (model card at that revision) |
| sherpa-onnx Lessac medium voice | github.com/k2-fsa/sherpa-onnx release tag `tts-models`, asset `vits-piper-en_US-lessac-medium.tar.bz2`, 67,230,653 bytes | contents extracted into `models/vits-piper-en_US-lessac-medium-9e3febfa/` | archive `9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e`; all 359 files listed in the committed `speech/voices/vits-piper-en_US-lessac-medium.manifest.json` | **ACADEMIC / NON-COMMERCIAL DEVELOPMENT ASSET** (section 8) |
| openWakeWord `hey_jarvis` + feature models | `openwakeword==0.6.0` `download_models` | inside the virtual environment (`openwakeword/resources/models/`), not the repository | locally observed values below; **the runtime does NOT verify these files** | code Apache-2.0; **pre-trained model licence UNRESOLVED** |

**faster-whisper tiny.en file hashes** (enforced by `speech/faster_whisper_backend.py`):

| File | Bytes | SHA-256 |
|---|---|---|
| `config.json` | 2,317 | `14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5` |
| `model.bin` | 75,537,502 | `1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a` |
| `tokenizer.json` | 2,128,466 | `929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df` |
| `vocabulary.txt` | 422,309 | `ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf` |

**openWakeWord files as installed on the development PC** (observed, not independently verified against upstream): `embedding_model.onnx` `70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f`; `hey_jarvis_v0.1.onnx` `94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb`; `melspectrogram.onnx` `ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f`.

### One-time acquisition (commands recorded in the project reports)

```bash
# Silero VAD (executed and reproduced in WEEK7_REPORT.md section 12.1)
curl -L -o models/silero_vad_v6.2.3.onnx https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx
sha256sum models/silero_vad_v6.2.3.onnx
```

```bash
# faster-whisper tiny.en (TEAM_LEADER_AI_REPORT.md section 3.3)
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Systran/faster-whisper-tiny.en', revision='0d3d19a32d3338f10357c0889762bd8d64bbdeba', local_dir='models/faster-whisper-tiny.en-0d3d19a3', allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.txt'])"
```

```bash
# openWakeWord model files (WEEK2_REPORT.md; needs internet once)
python -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])"
```

The Lessac voice: download `https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-lessac-medium.tar.bz2`, check the archive SHA-256 above, extract it so that the CONTENTS of its single top-level folder sit in `models/vits-piper-en_US-lessac-medium-9e3febfa/` (`TEAM_LEADER_AI_REPORT.md` section 6.4).

### Verification commands (each was executed for this handoff on the development PC)

```bash
python -c "from audio.silero_vad import SileroOnnxVadEngine; e = SileroOnnxVadEngine(); print(e.artifact_verified, e.verified_sha256)"
python -c "from speech.faster_whisper_backend import resolve_model_dir, verify_model_snapshot; print(verify_model_snapshot(resolve_model_dir()))"
python -c "from speech.sherpa_onnx_tts import load_manifest, resolve_voice_dir, verify_voice_directory as v; m = load_manifest(); print(v(resolve_voice_dir(m), m))"
```

Expected output: `True 1a153a22...88e3`; `78090594`; `81147006`. Each engine also refuses a file whose hash differs when it is built, so a wrong file fails loudly (`MODEL_HASH_MISMATCH`). After acquisition the system works fully offline; the Whisper engine is built with `local_files_only=True`.

## 8. Licence questions (flag, do not decide)

- Silero VAD: MIT (LICENSE read at the pinned commit).
- faster-whisper tiny.en model: MIT (Hub model card at the pinned revision). faster-whisper, ctranslate2, onnxruntime: MIT (installed package metadata).
- Lessac voice data: released for **non-commercial use only**; the bundled espeak-ng data is GPL-family (not independently verified). The voice files are NOT committed and NOT redistributed; only the manifest is committed. **UNRESOLVED:** any commercial or redistributed use needs a separate licence review.
- openWakeWord: package metadata says Apache Software License for the code. The licence of the **pre-trained `hey_jarvis` model files** is **UNRESOLVED** and has not been verified here; check the upstream project before any redistribution or non-academic use.
- sherpa-onnx: "Apache licensed" (package metadata). Other dependencies (numpy, Pillow, sounddevice, PyAV) carry permissive licences in their metadata; this was not a legal review.
- Week 4 external vision benchmark: dataset provenance and licence NOT VERIFIED (`WEEK4_REPORT.md`); the images are not in the repository.

## 9. Tests

```bash
python -m pytest -q -p no:cacheprovider -rs
```

Reference on the development PC (Windows, all models present): **2398 passed, 6 skipped, 0 failed** (2346 plus the 52 consistency tests in `tests/test_handoff_docs.py`). The 6 skips are: 4 ReSpeaker hardware tests (no device), 1 recorded-clip wake-word test (`VESTA_HEY_JARVIS_WAV` not set) and 1 opt-in playback smoke (`VESTA_PLAYBACK_API_SMOKE=1`). A machine without the model files skips the real-model tests, and a machine where openWakeWord cannot be imported also skips the real-engine wake tests: a different skip count is an environment difference, not necessarily a regression. On the Raspberry Pi a first full run is itself an evidence item.

Targeted groups: `tests/test_voice_pipeline.py tests/test_voice_pipeline_real.py` (pipeline); `tests/test_week7_voice_eval.py tests/test_week7_voice_metrics.py tests/test_week7_calibration.py tests/test_week7_hardening.py` (evaluator); `tests/test_ai2_stage_a_harness.py` (Stage A); `tests/test_handoff_docs.py` (this handoff's consistency checks).

## 10. Hardware test plan

Nothing below has been run on hardware. **Do not mark a test passed without evidence collected as described in `docs/handoff/EVIDENCE_INDEX.md`.** Owner letters: HE = Hardware Engineer, AI = AI Engineer, FACE = Vision/Face owner, AUD = audio hardware owner, TL = Team Leader.

| Test | Owner | Preconditions | Runnable command | Input | Expected behaviour | Evidence | Acceptance | Validation label |
|---|---|---|---|---|---|---|---|---|
| **A** recorded WAV -> transcript | HE records, AI runs | models verified (section 7); native 16 kHz mono 16-bit WAV; provenance JSON with a real consent reference; external evidence dir | `python -m scripts.ai2_stage_a_transcribe run --wav <wav> --provenance <json> --out-dir <external dir>` (exit 0 transcript, 1 no segment, 2 invalid input, 3 run failure) | one phrase per file, silence before/after, plus one negative control | one evidence record; no overwrite; no PCM stored | evidence JSON; then `review` with the human judgement | a transcript record exists and a person recorded MATCH / PARTIAL / MISMATCH / EMPTY. **No accuracy threshold is claimed from 1-2 files** | RECORDED-HARDWARE AUDIO (never LIVE) |
| **B** live microphone -> transcript | HE + AI | Pi 5 install works; ReSpeaker detected; the three local models verified | **RUNNABLE ON THE DEVICE ONLY:** `python -m scripts.ai2_stage_b_live --out-dir <dir outside the repo> --duration-s 120` (Stage B harness: SOFTWARE-VALIDATED WITH FAKE INPUT; live NOT VALIDATED) | live speech after the wake phrase | a structural evidence record with continuity counters and interaction statuses (transcripts only with `--show-transcript`) | the single `*.stage_b.json` record | speech spoken near the device is recognised live with zero undetected discontinuities | LIVE-MICROPHONE RUN - UNREVIEWED until a person checks the evidence on the device; none yet |
| **C** 1 m / 3 m / back of room / noise | HE records, AI evaluates | Test A works; Task 5 manifest prepared | recorded route is runnable: Task 5 `validate` then `run` (section 11); live route **BLOCKED — SOFTWARE INTEGRATION REQUIRED** (needs Test B) | same phrases at each distance and noise | per-condition results | Task 5 items + report, recording metadata | recorded route: results per condition are produced; AI-3 acceptance (8 of 10) needs the live route | RECORDED-HARDWARE AUDIO (recorded route) |
| **D** 10 AI-1 questions, real classroom | AI + HE + TL | Tests B and E working; real room-state source; capability declaration written BEFORE the run | **BLOCKED — SOFTWARE INTEGRATION REQUIRED** (the live runner exists but has never run on a device; no real room-state source) | the frozen 10 questions in `evaluation/ai_question_test_set_v1.json` | spoken answers judged against expected facts | scored sheet per question, STT transcript, distance, noise | 8 of 10 primary questions PASS (scoring rules in the test set) | must be LIVE to count for AI-3 |
| **E** spoken answer + latency baseline | AI + AUD | project speaker + Pi audio output verified | **RUNNABLE ON THE DEVICE ONLY:** `python -m scripts.vesta_live_voice --out-dir <dir outside the repo> --output-device <index> --speaker-test --duration-s 120` (live runner: SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT; live NOT VALIDATED; result UNREVIEWED until a person checks it). Silent API check: `VESTA_PLAYBACK_API_SMOKE=1 python -m pytest tests/test_playback.py -k real_sounddevice -q` | a recognised question | audible answer; end of question -> first buffer timing (`t_first_buffer_submitted` is a software proxy, not sound at the speaker) | timing log, a stopwatch or audio-loopback measurement | baseline recorded (AI-4); no target set yet | LIVE only after a real run |
| **F** one approved announcement | AI + TL (message) + AUD | approved message text; trigger decided; speaker verified | **BLOCKED — SOFTWARE INTEGRATION REQUIRED** (no trigger; the live runner has no announcement path) | `AnnouncementRequest` | message plays clearly | recording of the room audio, operator note | message audible and clear (AI-6) | LIVE only after a real run |
| **G** no announcement/conversation overlap | AI + AUD | Tests E and F working through ONE `OutputArbiter` | **BLOCKED — SOFTWARE INTEGRATION REQUIRED** (real callers are not wired) | an announcement and a question at overlapping times | policy `AI7_MIDTERM_V1`: conversation outranks announcement, no preemption, no hidden queue | audio recording, arbitration log lines | no overlapping speech in the test (AI-7) | LIVE only after a real run |
| **H** RED / BLUE / GREEN synchronization | FACE + AI | Face subscriber and transport built by their owner; a live runner emits the states from real events | **BLOCKED — SOFTWARE INTEGRATION REQUIRED / TEAM DEPENDENCY** | one interaction end to end | LISTENING = RED, CHECKING = BLUE, SPEAKING = GREEN, matching the live phases | video of the device with the log, sequence numbers | all three states are seen to match live (AI-8) | LIVE only after a real run |
| **I** offline fallback | AI + AUD | real failure trigger defined; speaker verified | **BLOCKED — SOFTWARE INTEGRATION REQUIRED** (no failure classifier or caller) | disconnect the upstream/network path | the fixed fallback sentence is spoken | recording, log | a clear fallback is heard (AI-9) | LIVE only after a real run |
| **J** recovery from microphone/audio failures | AI + HE | Test B approved first | **BLOCKED — SOFTWARE INTEGRATION REQUIRED.** Exploratory only: `record_demo.py` logs `read_frame_timeout` if no block arrives; behaviour on unplug during capture is UNKNOWN | unplug/replug the mic; stop the output device | the failure is reported, the interaction is abandoned, nothing is joined across the gap, the system recovers | logs with timestamps | recovery without silent discontinuity | LIVE only after a real run |

## 11. Week 7 Task 5 procedure (real recorded development evaluation)

Reuse the existing evaluator; do not write another one. Status: harness READY, never run on real data; Task 5 and Week 7 stay open until a real DEV evaluation (and one authorised HELDOUT check) are performed.

1. **Prepare recordings** in the seven categories: `CLEAN_COMMAND`, `WAKE_IMMEDIATE_COMMAND`, `WAKE_PAUSE_COMMAND`, `SILENCE_CONTROL`, `NOISE_CONTROL`, `SPEECH_NO_WAKE`, `SHORT_NON_COMMAND`. Native 16 kHz mono 16-bit WAV, named by `recording_id` (for example `REC-0001.wav`), never by person or content. At least 0.5 s of lead-in before a wake phrase. The Hardware Engineer keeps the originals untouched. If a file is not native, do not resample by hand: either send a native recording or use the existing ingest path (`python -m evaluation.ingest ingest --help`) and describe the conversion in the manifest.
2. **Write the manifest outside the repository** starting from `evaluation/week7_corpus_manifest.template.json` (schema `week7-voice-eval-manifest-v1`). Required per recording: `recording_id`, `audio_path`, `speaker_code` (coded, e.g. `S01`), `consent_reference` (structured, e.g. `CONSENT-S01`), `recording_origin`, `microphone_or_device`, `distance_condition`, `noise_condition`, `category`, `expected_wake_phrase`, `reference_command_text` (the human-verified command only), `split` (`DEV` or `HELDOUT`), `notes`. Optional: `wake_end_sample`, `speech_start_sample`, `speech_end_sample`, `human_review`, `audio_sha256`, `conversion`.
3. **Consent:** the code is checked for form only (`CONSENT-<id>` or `APPROVAL-<id>` with a register number); this does NOT prove consent was obtained. A private consent register kept outside the repository and a human approval of the manifest are required. Never put names, voices or consent records in documentation.
4. **Splits:** DEV for all tuning; HELDOUT for one final check. Prefer different speakers in each. **HELDOUT isolation is currently procedural, not enforced by the code**: freeze one configuration and protocol first, run HELDOUT once, and do not tune on it.
5. **Validate (loads no model):** `python -m evaluation.week7_voice_eval validate --manifest <external manifest>`.
6. **Run DEV:** `python -m evaluation.week7_voice_eval run --manifest <external manifest> --output <new external dir> --wake-mode annotated` (modes `annotated`, `start`, `detector`; in `annotated`/`start` the wake is SIMULATED and wake accuracy is NOT MEASURED). Exit codes: 0 finished, 2 invalid input/config/environment (nothing evaluated), 3 fatal failure (partial `INCOMPLETE` evidence).
7. **Calibrate on DEV only:** `python -m evaluation.week7_calibration --manifest <external manifest> --grid <grid json> --output <new external dir>` (grid file `{"base": {...}, "axes": {name: [values]}}`). It prints no winner: compare the tradeoff rows, choose by human decision, and write the chosen nine values to a config file.
8. **One HELDOUT check:** `python -m evaluation.week7_voice_eval run --manifest <external manifest> --output <new external dir> --split HELDOUT --allow-heldout --segmenter-config <frozen config file>`.
9. **Read the report honestly:** with no real recording `accuracy_results` is null and the report says so. Synthetic or fake inputs are only plumbing and are never real speech accuracy. WER `decoded` covers only items where STT produced a transcript; `end_to_end` also charges capture misses. The first item of a run includes cold-start cost in wall time and VAD time: do not quote steady-state latency from mixed measurements. The WER normalisation cannot see a lost minus sign or decimal point: check numeric commands by hand.

Outputs per run: `week7_eval_items.jsonl` (per-item evidence including transcripts: private) and `week7_eval_report.json` (aggregate, no transcripts). A directory holding `.partial` / `INCOMPLETE` files is an unfinished run, never a result.

## 12. What the Hardware Engineer sends back

For every recording: the original WAV exactly as recorded; the native-format copy if one was made (with how and from which channel); the provenance/manifest fields (`microphone` model and firmware, host, distance, noise condition, gain/processing mode); the consent reference marker (the register itself stays private); SHA-256 of each file; the exact words spoken (for the reference transcript); the software and OS versions; and the result of the first install on the Pi. For each live test: logs saved verbatim (checked for transcripts before sharing), the timing measurements, observations of the speaker and Face/LED, and any failure with its time. Use `docs/handoff/EVIDENCE_INDEX.md` for where each item goes. Send private evidence through the agreed private channel, never through a commit.

## 13. Team Leader acceptance matrix (AI-1 to AI-9)

Statuses are copied from `TEAM_LEADER_AI_STATUS.md`; software readiness never upgrades a task.

| Task ID | Requirement | Software readiness | Live hardware readiness | Remaining dependency | Evidence needed | Final completion status |
|---|---|---|---|---|---|---|
| AI-1 | 10-question test set | v1 written, frozen, validated by tests | not applicable (a specification) | none for the specification | `evaluation/ai_question_test_set_v1.json`, `tests/test_ai_question_test_set.py` | **COMPLETE — TEST SET WRITTEN** |
| AI-2 | Integrate the new microphone; live speech is recognised | STT and Stage A harness SOFTWARE-VALIDATED | none: Stage A not run; the Stage B harness is built but has never run on a device | Hardware Engineer recordings; ReSpeaker on the Pi | Stage A evidence records; a live transcript with continuity report | **NOT COMPLETE** |
| AI-3 | Classroom tuning at 1 m, 3 m, back and noise; 8 of 10 correct | answering works on a SIMULATED fixture only (not an AI-3 result) | none | live microphone, real room state, speaker, classroom session | scored 10-question sheet per distance/noise (Test D) | **NOT COMPLETE / HARDWARE DEPENDENT** |
| AI-4 | End of question to start of spoken answer, baseline | software timing proxy exists (`t_first_buffer_submitted`) | none | a first live run (the live runner exists, never run on a device), live input, speaker | measured baseline (Test E) | **NOT STARTED** |
| AI-5 | Reduce delay below the baseline | none | none | AI-4 baseline first | before/after measurements | **NOT STARTED** |
| AI-6 | Spoken announcement from app or schedule | orchestration SOFTWARE-VALIDATED with fakes | none | trigger integration, project speaker, audibility | recording of an audible announcement (Test F) | **NOT COMPLETE** |
| AI-7 | No overlapping speech | arbitration SOFTWARE-VALIDATED with fake engines | none | real callers wired through one arbiter, project speaker | live overlap test recording (Test G) | **PARTIAL / PENDING REAL HARDWARE TEST** |
| AI-8 | LISTENING = RED, CHECKING = BLUE, SPEAKING = GREEN synchronized | state contract SOFTWARE-VALIDATED with a fake subscriber | none | Face subscriber and transport, orchestrator, live test | video showing all three states matching (Test H) | **PARTIAL / PENDING VISION-FACE + HARDWARE LIVE TEST** |
| AI-9 | Fallback sentence when the AI cannot connect | fallback SOFTWARE-VALIDATED, synthesized offline | none | real failure trigger, speaker, audibility | recording of the audible fallback (Test I) | **NOT COMPLETE** |
| **Overall** | | | | | | **NOT COMPLETE (1 of 9)** |

## 14. Single smallest next implementation (needs separate approval; NOT done here)

The Stage B capture harness and the software composition root are built (2026-10-16). The smallest next implementation is a **live runner** under `scripts/`: it would start `ReSpeakerMicrophone`, the real engines and the real playback engine, hand the Stage B harness's processing worker to `assistant/runtime.py` (`pipeline=runtime.pipeline`, `on_result=runtime.handle_result`, `on_discontinuity=runtime.on_capture_abandoned`) and read the room state from a real source once one exists. It is NOT built, and it needs separate approval. Until it exists, Test B (transcripts) can be run on the device with the Stage B harness alone; Tests D to J stay blocked.

**Update 2026-10-18 (the paragraph above is kept as the historical record).** The live runner was built on the project owner's instruction as `scripts/vesta_live_voice.py`, exactly with the wiring described above, plus `--list-devices` and `--speaker-test`; tests: `tests/test_midterm_readiness.py` (injected fakes only). It has never run on a device. Test E is now runnable on the device; Tests D and F to J stay blocked. Hardware-day procedure: `docs/handoff/MIDTERM_HARDWARE_VALIDATION.md`.
