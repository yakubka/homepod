# VESTA — Team Leader AI Engineer Milestone Report (Oct 12–23)

Status table: [TEAM_LEADER_AI_STATUS.md](TEAM_LEADER_AI_STATUS.md). This report is separate from the CLAUDE.md Week 7 report ([WEEK7_REPORT.md](WEEK7_REPORT.md)); the two tracks are not mixed.

## 1. Baseline audit (2026-10-09, before AI-1)

Read-only audit of the repository against the nine Team Leader AI tasks. Result: 0 of 9 complete, 1 PARTIAL (AI-2), 1 BLOCKED by hardware (AI-3), 7 NOT STARTED.

Exists: audio capture and reframer, microphone backend (hardware tests skipped), openWakeWord backend (one human smoke clip, accuracy NOT MEASURED), VAD contract, utterance segmenter and Silero adapter (synthetic scores only), vision and fusion modules.

Does not exist: STT, TTS, audio playback, LLM or answer code, announcement or schedule code, overlap policy, LED/state enum or Vision/Face interface, ESP32/serial code, fallback sentence, any end-to-end latency measurement, any recording from the Hardware Engineer. `requirements.txt` has no STT, TTS or LLM package.

Roadmap conflict noted: the Team Leader expects question answering, TTS, announcements and state sync earlier than the CLAUDE.md roadmap places them (Weeks 8 and 12). Not resolved here; it needs a decision before AI-4 onward.

## 2. AI-1 — official 10-question test set (2026-10-09)

Task: "Build a test set. Write 10 questions in the style users will ask (about the room, the lecture, general ones). Record the answers you expect." Done when: test set written.

This is a Team Leader milestone artifact. It is not a Week 7 roadmap task. Specification only: no STT, TTS, LLM, router or package was added, and no VAD or wake-word code changed.

File: `evaluation/ai_question_test_set_v1.json` (v1, 2026-10-09, source TEAM LEADER AI-1 MILESTONE, 10 questions: 4 ROOM, 3 LECTURE, 3 GENERAL).

Validation: `tests/test_ai_question_test_set.py`, 10 tests: exactly 10 questions, unique ids, pinned question text, category counts, expected behaviour and missing-data behaviour present, no literal numbers outside fixtures, fixtures SIMULATED, fixture mappings name real `OccupancyState` / `EnvironmentState` fields, schedule and professor questions claim no source, announcement not marked implemented. Full suite: 1157 passed, 8 skipped, 0 failed (1147 + 10).

Answer-source audit used for the classification:

| Q | Information | Classification | Basis |
|---|---|---|---|
| Q1 | people count | SIMULATED / PLACEHOLDER STATE | `OccupancyState.vision_count` exists (simulated inputs); no camera connected; known dense-room undercounting |
| Q2 | temperature | SIMULATED / PLACEHOLDER STATE | `TemperatureReading` / `EnvironmentState`; no sensor interface; ESP32 protocol NOT AVAILABLE |
| Q3 | humidity | SIMULATED / PLACEHOLDER STATE | `HumidityReading`; physical humidity device UNKNOWN |
| Q4 | brightness | SIMULATED / PLACEHOLDER STATE | `LuxReading`; BH1750 interface NOT AVAILABLE; no bright/dark threshold exists |
| Q5, Q6, Q9 | schedule | PLANNED BUT NOT IMPLEMENTED | no schedule store, format or owner |
| Q7 | professor arrival | UNAVAILABLE | no state or rule; the person detector cannot tell a professor from a student |
| Q8 | capabilities | PLANNED BUT NOT IMPLEMENTED | no answer path; static text scored against a pre-run capability declaration |
| Q10 | announcement | PLANNED BUT NOT IMPLEMENTED | no TTS, playback, speaker path or trigger |

Scoring: PASS = all key facts correct and nothing contradictory or fabricated; the criterion is 8 of 10 primary questions on their primary condition; the missing-data check is secondary and does not change the denominator.

Limits: this is not an accuracy result; no question has been asked of any system. Four questions (Q5, Q6, Q7, Q9) can only pass today with an honest "unavailable" answer.

Status: Team Leader AI-1 COMPLETE — TEST SET WRITTEN. AI-2 to AI-9 incomplete. Overall milestone NOT COMPLETE.

## 3. AI-2 software dependency — offline STT engine (2026-10-11)

Track label: this supports Team Leader AI-2 ("Integrate the new microphone", done when live speech is recognised). It is NOT completion of AI-2. It also covers the technical content planned as roadmap Week 7 Task 3; the Week 7 status files are not changed by it. Week 1 audio, Week 2 and Week 7 Tasks 1-2 were not modified (hashes of `audio/*`, `wakeword/*`, `fusion/*`, `config/*` identical before and after).

Status: **STT IMPLEMENTED / SOFTWARE-VALIDATED. STT ACCURACY NOT MEASURED. HALLUCINATION MITIGATION NOT YET VALIDATED.** AI-2: NOT COMPLETE.

### 3.1 What was built

| File | Role |
|---|---|
| `speech/__init__.py` | package docstring only (imports nothing) |
| `speech/stt.py` | engine-neutral `SttEngine`, `SttResult` (statuses OK, EMPTY_TEXT), typed errors; numpy + logger only |
| `speech/faster_whisper_backend.py` | `FasterWhisperEngine`, model manifest `FASTER_WHISPER_TINY_EN`, snapshot verification; heavy imports lazy |
| `tests/test_stt_contract.py` | 63 tests, scripted fake engine |
| `tests/test_faster_whisper_backend.py` | 56 tests: fake model + temporary snapshots (Tier 1), 5 real-model tests (Tier 2, skip when the snapshot is absent) |
| `requirements.txt` | added `faster-whisper==1.2.1` |
| `.gitignore` | added `models/faster-whisper-*/` |

Contract: `transcribe(samples, sample_rate) -> SttResult` for a 1-D, non-empty, int16 array at exactly 16 kHz; nothing is resampled, down-mixed or written to the caller's array (the backend receives a read-only view). `transcribe_segment(segment)` is a duck-typed helper, so `speech/stt.py` does not import the segmenter. Failures are typed exceptions (`SttRuntimeUnavailable`, `SttModelNotFound`, `SttModelLoadFailed` incl. `SttModelHashMismatch`, `SttInputError`, `SttInferenceFailed`, `SttContractError`); none becomes an empty transcript. Inference is synchronous and has no timeout. Transcript text and PCM are never logged. `max_audio_s = 30 s` is a backend-only DEVELOPMENT POLICY, NOT CALIBRATED, not part of the generic contract.

### 3.2 Installed versions (project `.venv`, binary wheels, Python 3.13.14, Windows)

faster-whisper 1.2.1, ctranslate2 4.8.2, av 19.0.1, tokenizers 0.23.3, huggingface_hub 2.2.0, hf-xet 1.7.0, httpx2 2.13.1, httpcore2 2.13.1, anyio 4.15.1, click 8.5.0, filelock 4.1.0, fsspec 2026.9.0, h11 0.16.0, PyYAML 6.0.3, truststore 0.10.4, typing_extensions 4.16.0. `numpy 2.5.3` and `onnxruntime 1.30.0` were pinned during the install and are unchanged; `pip freeze` before/after differs only by additions; `pip check` reports no broken requirements.

### 3.3 Model acquisition (one explicit development step; the runtime never downloads)

Verified first from the Hub API at the pinned revision: sha `0d3d19a32d3338f10357c0889762bd8d64bbdeba`, license MIT (model card and tags), not gated.

```bash
.venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Systran/faster-whisper-tiny.en', revision='0d3d19a32d3338f10357c0889762bd8d64bbdeba', local_dir='models/faster-whisper-tiny.en-0d3d19a3', allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.txt'])"
```

Acquired 2026-10-11. `model.bin` SHA-256 matches the Hub LFS hash; the three small files match the Hub git blob ids. The Hub download cache (`.cache/`) and the model card are not part of the inference contract and are ignored by verification.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| config.json | 2,317 | `14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5` |
| model.bin | 75,537,502 | `1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a` |
| tokenizer.json | 2,128,466 | `929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df` |
| vocabulary.txt | 422,309 | `ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf` |

Total 78,090,594 bytes. At construction the engine checks existence, size and SHA-256 of all four files BEFORE loading, then builds the model from the local directory with `local_files_only=True`. A fresh clone must repeat the command above.

### 3.4 Decoder and execution policy (faster-whisper 1.2.1, read from the installed source)

`language="en"`, `condition_on_previous_text=False`, `vad_filter=False`, `temperature=0.0` (single), `beam_size=5` (UPSTREAM DEFAULT, NOT CALIBRATED). Everything else is the upstream default. With one temperature, `compression_ratio_threshold` and `log_prob_threshold` cannot trigger a second decode, so they are not independent hallucination filters; the built-in no-speech skip stays active and can drop a window (read as EMPTY_TEXT). No custom quality threshold was added. CPU / int8 is a DEVELOPMENT EXECUTION POLICY for the Windows backend, not a Pi optimisation. PCM is converted as int16 / 32768.0 to float32 (the scaling faster-whisper's own `decode_audio` uses); the segment iterable is lazy, and the engine consumes it completely inside the timed call.

### 3.5 Development measurements (Windows, AMD Ryzen 5 7520U; NOT Pi claims; synthetic 3 s of digital silence, not speech)

Two fresh interpreters: project import 0.18-0.19 s; `import faster_whisper` 0.47-0.53 s; snapshot verification 0.08-0.09 s; model load 415-489 ms; first / second / third inference 673 / 742 / 718 ms and 708 / 706 / 678 ms. The first-ever `import av` on 2026-10-11 took ~54 s in the dependency gate and was not reproduced afterwards (file cache); its cause is NOT known, so the demo harness should initialise the engine before user interaction.

One ad-hoc run (not a test, not a measurement of anything) fed 2 s of random noise to the engine and got the text "You". It is anecdotal evidence that the decoder can emit text for non-speech; HALLUCINATION MITIGATION NOT YET VALIDATED, and Stage A must measure real behaviour.

### 3.6 Validation

- Targeted: 119 STT tests pass (Tier 1 anywhere; Tier 2 on this machine). Task 1 tests (290), Task 2 Silero tests (142) and the audio / wake-word groups pass. Full suite **1279 passed, 5 skipped, 0 failed** (reference before: 1160 / 5).
- Mutation checks on scratch copies only (the canonical files and model were never mutated; every file restored and hash-verified after each mutant): 63 mutants (every item of the requested list plus comparator, boundary, state and provenance mutants). **60 killed, 3 survivors, all EQUIVALENT**: a bool accepted as `sample_rate` (rejected anyway because `True != 16000`), and two redundant per-segment `strip` / blank-filter steps that the shared whitespace normalisation makes unobservable. Two further mutants (size check removed, directory check removed) survived once because a later check produced the same exception type; message assertions were added and both are now killed. The campaign was run without the model snapshot (clean clone) and with it: results identical, so no kill depends on the gated artifact.
- Real-model smoke tests (Tier 2) show only that the approved snapshot verifies, the model loads, a synthetic input executes with a finite latency, repeated calls work, the 30 s limit is enforced and construction plus inference run with socket access disabled. They claim nothing about accuracy.

### 3.7 Stages still open

| Stage | Status |
|---|---|
| A: Hardware Engineer recordings -> WavFileSource -> segmenter -> STT -> transcript | **PENDING HARDWARE RECORDINGS** (will be RECORDED-HARDWARE AUDIO, not live validation) |
| B: physical new microphone -> live capture -> segmenter -> STT -> transcript | **PENDING PHYSICAL MICROPHONE** |
| Development harness (push-to-talk, DEVELOPMENT INTEGRATION HARNESS) | NOT IMPLEMENTED |
| Pi 5 / aarch64 import, load and speed | PENDING TARGET-HARDWARE VERIFICATION |

Not claimed: recognition accuracy, WER, silence behaviour, classroom readiness, live microphone validation, Pi performance. **Team Leader AI-2: NOT COMPLETE.**

## 4. AI-2 Stage A integration harness (2026-10-11)

Status: **STAGE A HARNESS READY. REAL STAGE A RUN NOT YET PERFORMED.** No Hardware Engineer recording exists; nothing here is Stage A evidence, and nothing says anything about recognition accuracy or the microphone. This is Team Leader AI-2 work, not a Week 7 task. AI-2: NOT COMPLETE.

### 4.1 What was added

| File | Role |
|---|---|
| `scripts/ai2_stage_a_transcribe.py` | the harness, labelled TEAM LEADER AI-2 / STAGE A / DEVELOPMENT-INTEGRATION HARNESS (commands `run` and `review`) |
| `scripts/__init__.py` | package marker |
| `tests/test_ai2_stage_a_harness.py` | 69 tests (67 with generated PCM, a scripted VAD and fake STT; 2 with the real models on synthetic audio) |
| `evaluation/ai2_stage_a_result_template.json` | blank result schema, marked TEMPLATE / NO HARDWARE RUN YET; not a valid record |
| `evaluation/ai2_stage_a_provenance_template.json` | blank provenance form; its `recording_id` starts with TEMPLATE and is refused by the harness |
| `docs/ai2_stage_a_hardware_handoff.md` | what the Hardware Engineer needs to provide |

Frozen and unchanged: `audio/*`, `wakeword/*`, `fusion/*`, `speech/*`, `config/settings.py` (hashes identical before and after, with `requirements.txt` and `.gitignore`).

### 4.2 Pipeline

`WavFileSource` (fast replay, opened read-only) -> one absolute sample counter -> existing `UtteranceSegmenter` + `SileroOnnxVadEngine` -> `SpeechSegment` -> existing `FasterWhisperEngine` (exactly one call, only on a finished segment) -> JSON evidence written outside the repository. Activation is `DEVELOPMENT_STAGE_A`: the segmenter is activated at sample 0, a development stand-in and not the production wake-word path; `Detection.end_sample` is not used. At end of file the harness calls `finish()` (no padding; `unscored_samples` is recorded). One recording is one intended phrase; the harness stops at the segmenter's decision and records `samples_after_decision`. Multi-utterance recordings are not supported in Stage A v1.

Segmenter values are harness-local and recorded in every result as DEVELOPMENT / UNCALIBRATED, each with its reason: start 0.5 and end 0.35 (Silero utility defaults at the pinned commit), minimum utterance 0.25 s (Silero default), and harness choices of start confirmation 0.1 s, end silence 0.7 s, onset padding 0.1 s, no-speech timeout 10 s, maximum 20 s, pre-roll 0. None was tuned on any audio; `config/settings.py` still holds None.

### 4.3 Contracts

- Input: native 16 kHz / mono / 16-bit PCM WAV only, validated with the real `WavFileSource`. Anything else fails with a message that nothing was resampled or mixed and how to supply a documented converted copy. The original is never opened for writing. Provenance records the conversion and the original's SHA-256, which is then required.
- Provenance (JSON): `recording_id`, `speaker_code` (S01-style or UNKNOWN), `consent_reference`, plus optional descriptive fields. Consent policy (revised 2026-10-11): a hardware recording, or any recording of unstated origin, MUST carry an explicit consent / project-approval reference; `UNKNOWN`, empty and `NOT_APPLICABLE` are refused. Only generated test audio (`recording_origin` SYNTHETIC) is accepted, and then it must say `NOT_APPLICABLE`. No real names are required. Unknown values are recorded as UNKNOWN and never inferred; unknown fields, a mismatching SHA-256 or a declared format that disagrees with the file are rejected.
- Result: 37 fixed fields (`RESULT_KEYS`), validated before writing. `validation_label` follows the declared origin (RECORDED-HARDWARE AUDIO, SOFTWARE-VALIDATED (SYNTHETIC INPUT) or UNVERIFIED RECORDING ORIGIN) and can never be LIVE. `human_match` is always NOT_REVIEWED until a person runs `review` (MATCH, PARTIAL, MISMATCH, EMPTY). WER and CER are computed only against a human reference and are otherwise NOT_AVAILABLE; a negative-control recording has no reference and is simply transcribed and recorded.
- Evidence output: must be outside the repository (reuses the ingest guard), writable, and is never overwritten (exclusive create). Transcripts exist only in the evidence file (and on the console with `--show-transcript`); logs carry structural fields only; no PCM is serialised.
- Exit codes: 0 transcript produced; 1 processed but no segment (STT not run); 2 invalid input / provenance / output; 3 run failure (model, VAD or STT error; a record with the error type and stage, never the message, is written).

### 4.4 Validation

- Tests: 69 harness tests pass; STT, Task 1, Task 2 and audio / wake-word groups still pass. Full suite **1348 passed, 5 skipped, 0 failed** (reference 1279 / 5).
- Mutation checks on scratch copies (52 mutants covering every requested item plus boundary and state mutants; each file restored and hash-verified): 52 killed, 0 survivors, with and without the gated model files. One mutant (exclusive create replaced by overwrite) first survived because the pre-check made it unobservable; a test of the write itself now kills it.
- Real-model plumbing (synthetic audio only): scripted VAD + real tiny.en gives a valid record with `stt_model_verified` true; real Silero + real tiny.en on 4 s of random noise produced NO_SPEECH_STREAM_ENDED, STT not run, a valid record, exit code 1. These show only that the real backends integrate with the harness.

### 4.5 Housekeeping note (line endings), reconciled in 4.7

A line-ending normalisation I ran by mistake during this task rewrote 20 pre-existing CRLF files to LF; CRLF was restored on all 20. The three `fusion/*` files are PROVEN byte-identical (4.7). The other 17 were never in the frozen set and have no pre-edit hash, so they are CONTENT-SEEMINGLY-UNCHANGED / LINE-ENDINGS-RESTORED BUT PRE-EDIT BYTE IDENTITY UNVERIFIABLE.

### 4.6 Not done / still open

Real Stage A run (needs Hardware Engineer recordings), Stage B (physical microphone, live capture), Pi 5 behaviour, VAD calibration, STT accuracy, hallucination behaviour on real silence or noise. **Team Leader AI-2: NOT COMPLETE.**

### 4.7 Integrity reconciliation (2026-10-11)

The two earlier statements concerned different file sets and are both true once separated. "All 21 frozen files are identical to the baseline hashes" refers to the 21 files hashed before the Stage A work began (that set includes the three `fusion/*` files the incident touched). "17 files could not be hash-verified" refers to 17 OTHER files that were never in the frozen set. No pre-edit hash exists for those 17 (searched: the hash tables in the reports, the scratch baselines, and the graphify index, which has no entries for them and does not use plain SHA-256). Nothing is inferred from timestamps or semantic similarity.

**A. Frozen files.** Pre-edit SHA-256 recorded at the start of the Stage A work; current value recomputed during this reconciliation. 21 / 21 exact matches (rechecked after the consent change and the full suite). Where an older independent record exists (WEEK7_REPORT section 12.11, 2026-10-09) it agrees for every file it lists except `requirements.txt` and `.gitignore`, which the STT work intentionally changed (pin added; model ignore rule) before this baseline was taken.

| File | Pre-edit SHA available? | Pre-edit SHA-256 | Current SHA-256 | Exact match? |
|---|---|---|---|---|
| `.gitignore` | YES | `7d9827513af8529de46720c2c57b18ff11378ce398f27454679e01adaa7b8672` | `7d9827513af8529de46720c2c57b18ff11378ce398f27454679e01adaa7b8672` | YES |
| `audio/__init__.py` | YES | `4d8181f99bbc63fa35f95d3a5066bda829a611e2cfdfe451592ec99dd253206a` | `4d8181f99bbc63fa35f95d3a5066bda829a611e2cfdfe451592ec99dd253206a` | YES |
| `audio/base.py` | YES | `4be1d035b872d4d39d90e9260093442023929d6892c677b238a0105777631136` | `4be1d035b872d4d39d90e9260093442023929d6892c677b238a0105777631136` | YES |
| `audio/file_source.py` | YES | `fdc76d0bad09619d953140aa7a6e1e0cefbf006457a9da14613a645b0ebc3500` | `fdc76d0bad09619d953140aa7a6e1e0cefbf006457a9da14613a645b0ebc3500` | YES |
| `audio/microphone.py` | YES | `18fe3d620070806afb8481489b47be14567f8a4e1882413430fc7cf573729f2b` | `18fe3d620070806afb8481489b47be14567f8a4e1882413430fc7cf573729f2b` | YES |
| `audio/reframer.py` | YES | `bebe38407be56277f248ca54d8c9a8cb2c185f96a2e66206f5cee85a56db9a97` | `bebe38407be56277f248ca54d8c9a8cb2c185f96a2e66206f5cee85a56db9a97` | YES |
| `audio/silero_vad.py` | YES | `f85adea90834e1324f2da59275c4cb97acf4fd8a273efda0c27f5f917cab197f` | `f85adea90834e1324f2da59275c4cb97acf4fd8a273efda0c27f5f917cab197f` | YES |
| `audio/utterance.py` | YES | `884c04597a3a1541d35044e4427943f8f39352ccd64d57a81e8c53f29375bb50` | `884c04597a3a1541d35044e4427943f8f39352ccd64d57a81e8c53f29375bb50` | YES |
| `audio/vad.py` | YES | `aeb584f06f250366c239d82e7b88317b9eedeac4a2de7ce64a242c85af05598c` | `aeb584f06f250366c239d82e7b88317b9eedeac4a2de7ce64a242c85af05598c` | YES |
| `config/settings.py` | YES | `e4c0484a64cb4bef250683eb02cb44b2ebe14e561f4908aee111dfc93a1a876d` | `e4c0484a64cb4bef250683eb02cb44b2ebe14e561f4908aee111dfc93a1a876d` | YES |
| `fusion/__init__.py` | YES | `c9e829d4551fd038c0d2fe1fe7349e0bd03a85c44d0358dd309553b787d754ba` | `c9e829d4551fd038c0d2fe1fe7349e0bd03a85c44d0358dd309553b787d754ba` | YES |
| `fusion/contracts.py` | YES | `83436a1df18a00fd82b6aa26f18ee2b9b5e43150c7c23532ba928f84bf54806b` | `83436a1df18a00fd82b6aa26f18ee2b9b5e43150c7c23532ba928f84bf54806b` | YES |
| `fusion/fuse.py` | YES | `7a332064a7bba4dcf9b8b417a8458afac365462df56c99886401b5decef0837f` | `7a332064a7bba4dcf9b8b417a8458afac365462df56c99886401b5decef0837f` | YES |
| `fusion/telemetry.py` | YES | `a61f22ce3ac1c49813ccc96b8bd7abedb61d85e837dafa6d68c3f16aa0020e52` | `a61f22ce3ac1c49813ccc96b8bd7abedb61d85e837dafa6d68c3f16aa0020e52` | YES |
| `requirements.txt` | YES | `874863ca8e1e8b158ef8ab0d492d2255c83ca3f83cd997dca7849df843717c28` | `874863ca8e1e8b158ef8ab0d492d2255c83ca3f83cd997dca7849df843717c28` | YES |
| `speech/__init__.py` | YES | `78a4c32123ccb28a83fcc0fd9f83e543aba03bef13cfe7e87e29e05addd01894` | `78a4c32123ccb28a83fcc0fd9f83e543aba03bef13cfe7e87e29e05addd01894` | YES |
| `speech/faster_whisper_backend.py` | YES | `c73cd43635b7d1b6e394e54de01b7aaa22ddc4a6585bc486da532174a68a4b31` | `c73cd43635b7d1b6e394e54de01b7aaa22ddc4a6585bc486da532174a68a4b31` | YES |
| `speech/stt.py` | YES | `bb538123c734c9ca0f4d7d2b24e5bb4328aaa4f9d83713d25d124c72163fca7d` | `bb538123c734c9ca0f4d7d2b24e5bb4328aaa4f9d83713d25d124c72163fca7d` | YES |
| `wakeword/__init__.py` | YES | `86e3a586642c95f85847f00455a8083e2106db98aeae75da2c880d75ba89a5b7` | `86e3a586642c95f85847f00455a8083e2106db98aeae75da2c880d75ba89a5b7` | YES |
| `wakeword/detector.py` | YES | `f2dab5b6af6c68d73fa4e9005699febbdf87c325cd566213e4a9cd727b50488e` | `f2dab5b6af6c68d73fa4e9005699febbdf87c325cd566213e4a9cd727b50488e` | YES |
| `wakeword/openwakeword_backend.py` | YES | `89da99a1271f4748dea365b97ed44764cce8479735fe156bf488ddacfa24ad5a` | `89da99a1271f4748dea365b97ed44764cce8479735fe156bf488ddacfa24ad5a` | YES |

**B. Non-frozen files touched by the incident (no pre-edit SHA-256 exists; current values recorded for future checks).** The first 17: CONTENT-SEEMINGLY-UNCHANGED / LINE-ENDINGS-RESTORED (all lines CRLF) BUT PRE-EDIT BYTE IDENTITY UNVERIFIABLE. The two WEEK7 documents were deliberately edited (status text); their original line endings are unverifiable and LF was assumed.

| File | Pre-edit SHA available? | Current SHA-256 |
|---|---|---|
| `CLAUDE.md` | NO | `4f73f1c5ab5ec60e016cc3d265cf594d785ea378b4d4abb698fc6ca043305b22` |
| `WEEK4_REPORT.md` | NO | `4fa413682ace9e4be329820e0c9fca0913c12695d22eb435fb3fc6a6d545291c` |
| `WEEK5_REPORT.md` | NO | `909963ace6734e20582a72a9691cfa838cc955ba4ae1e87c0bc824fc78387f18` |
| `WEEK5_STATUS.md` | NO | `0eaf09639a967c3c1995f779126dabdd7da21b52e28b3aeddb1258a3fd6dd58a` |
| `WEEK6_REPORT.md` | NO | `f72a39f866db40ed7adec53318009d012d9bedc0d9ba87cbccdd5a81b1fe9d08` |
| `WEEK6_STATUS.md` | NO | `1dddb74375a10e24b8b26a7974dceef069cdbe690dd74322ceccd4e44e74bba9` |
| `tests/test_fusion_logic.py` | NO | `c0cf2ecdbece2a3678a508a02e4db9c2010a7ce5a1b6bcabc2c472f7d1675cde` |
| `tests/test_fusion_scenarios.py` | NO | `10066d981ea40e036830a4a2c7cc091b7601d32a80ce2c9555143fad30724d82` |
| `tests/test_roi.py` | NO | `65b2f88f06b5b326bf81cb8deaffc0c6fb63d4f58ccd2d4b382ec1b84aab861b` |
| `tests/test_vision.py` | NO | `a6b9bdbc3bac087c657c9d960a7c7560165ba28c778498ee73eb470fa64028b6` |
| `tests/test_zones.py` | NO | `de134872eaac9a9518a887642c692935a34119cec6d69358a9633a2e76698c4d` |
| `tests/test_zone_scenarios.py` | NO | `1f2a0238dcc05c7c1a621e4f235fe202577d5460cca0476ae0518e4a283073a7` |
| `vision/audit.py` | NO | `b0607440b96e760bacec7bb74bd8e3623f55357fce796dd7df107a1032123bf2` |
| `vision/benchmark.py` | NO | `63aaf77c801d056ee2942854d3b0904096186a5f4643cc0e95ad5385232734fd` |
| `vision/detector.py` | NO | `254b25308791ab6fde44be659038f322e94b8d56b6abc183fac6023fa7fb0a13` |
| `vision/evaluate.py` | NO | `596d31a59016ac94b43854bf140bd995e429dc4f720f1600268524d65094bedc` |
| `vision/frames.py` | NO | `da954cc788ee5d964c7324c44b9c51923fa827633ff777a9a3500da160182d56` |
| `WEEK7_REPORT.md` | NO | `1e26b9dd7d2bfe166abade2b876b88734174d5029aca023a3215708dc028afcb` |
| `WEEK7_STATUS.md` | NO | `301220c633118ff8cc27458fdff74e90707cdd0d5a1100810bf2134241678d98` |

**Verdict.** STAGE A HARNESS: FROZEN / READY, with the limitation above stated explicitly. STAGE A EXECUTION: PENDING HARDWARE RECORDING. STAGE B: PENDING PHYSICAL MICROPHONE. TEAM LEADER AI-2: NOT COMPLETE.

## 5. Answering foundation — software-only (2026-10-11)

Status: **ANSWERING FOUNDATION: SOFTWARE-VALIDATED** (SIMULATED fixtures; NOT classroom validated). Hardware (microphone, recordings) is still unavailable; this phase builds the text answer path that follows the transcript. It is a Team Leader midterm prerequisite for AI-3 (question evaluation), AI-4 (spoken-answer latency) and AI-6 / AI-9 (spoken output); it completes none of them and is not a Week 7 item. AI-2: NOT COMPLETE.

### 5.1 State audit (what exists in software)

| Information | Authoritative source in the repository | Used as |
|---|---|---|
| People count | `ClassroomState.occupancy.vision_count` (fusion; the detected-person proxy that undercounts dense rooms; `raw_vision_count` is diagnostic only) | answered when vision participated, its state is FRESH and the count is a non-negative int |
| Temperature, humidity, light | `ClassroomState.environment` (`EnvironmentValue` with unit and `SourceState`) | answered when FRESH, numeric and in the expected unit (degC, %RH, lx) |
| Provenance | `SourceState.origin` (REAL / SIMULATED / UNKNOWN) | passed through, weakest label wins, never upgraded |
| Lecture schedule, expected attendance, next class | **none** | UNAVAILABLE unless the caller passes an explicit `SimulatedScheduleContext` (test fixture, always SIMULATED; there is no production schedule and its format is undecided) |
| Professor arrival | **none** (no rule, no input) | always UNAVAILABLE; never inferred from occupancy, detections or the schedule |
| Announcement playback / capability declaration | **none** | caller-supplied `CapabilityDeclaration`; the default declares nothing |

### 5.2 Files

Created: `assistant/__init__.py`, `assistant/answering.py`, `tests/test_answering.py`. Modified: this report and the status file. Not touched: `audio/*`, `wakeword/*`, `fusion/*`, `speech/*`, `scripts/*`, `config/*`, the AI-1 oracle (44 files compared by binary SHA-256 against the pre-edit baseline: 0 changed).

### 5.3 Contract

`AnswerEngine.answer(AnswerRequest(text, AnswerContext)) -> AnswerResult`; `DeterministicAnswerEngine` is the v1 engine. `AnswerContext` = optional `ClassroomState` + `CapabilityDeclaration` + optional `SimulatedScheduleContext`. `AnswerResult` = `status` (ANSWERED, INFORMATION_UNAVAILABLE, UNSUPPORTED_QUESTION, INVALID_INPUT), `text`, `intent`, `question_id` (Q1-Q10), `source` (SIMULATED, REAL, UNKNOWN_ORIGIN, DECLARATION, UNAVAILABLE, NONE), `reason_code`, and `facts` (name, value, unit) so nothing needs parsing from text. Invariants are enforced at construction. Bad requests / contexts raise typed errors; a bad question is a result (INVALID_INPUT), not an exception. Text only: no audio, no TTS, no LLM, no network.

### 5.4 Matching

Narrow normalisation only (trim, lower case, punctuation to spaces, whitespace collapse, apostrophes only inside words), then EXACT equality with the frozen AI-1 primary questions and accepted variants. No substring, keyword or prefix matching, no wake-prefix stripping, no transcription correction. A test keeps the phrase table identical to the AI-1 JSON, and the missing-data wording identical to its example wordings. A room question whose capability is not declared is UNSUPPORTED_QUESTION, so the engine never answers what its own capability answer denies.

### 5.5 Behaviour

Q1-Q4 state the value with its unit ("There are 17 people in the room.", "... 22.5 °C.", "... 45%.", "... 320 lux."), several temperature sensors are listed and never averaged. Q5, Q6, Q9 and Q7 answer with the AI-1 unavailable wording. Q8 lists only declared capabilities; Q10 follows `announcement_available` (false today: "Announcements are not available yet."). Anything else: "I can't answer that yet." Empty or non-text input: "I didn't catch a question."

### 5.6 AI-1 software / simulated evaluation (SIMULATED TEST FIXTURE: 17 people, 22.5 °C, 45 %RH, 320 lux; room capabilities declared, announcements not)

| ID | Expected | Actual | Result |
|---|---|---|---|
| Q1 | reports 17 people | There are 17 people in the room. | PASS |
| Q2 | reports 22.5 degrees Celsius | The temperature in the room is 22.5 °C. | PASS |
| Q3 | reports 45 percent relative humidity | The relative humidity in the room is 45%. | PASS |
| Q4 | reports 320 lux | The light level in the room is 320 lux. | PASS |
| Q5 | unavailable, no lecture | Schedule information is not available. | PASS |
| Q6 | unavailable, no number | Expected student numbers are not available. | PASS |
| Q7 | unavailable, neither yes nor no | I don't have professor arrival information. | PASS |
| Q8 | only declared capabilities | I'm VESTA, a classroom assistant. I can tell you the number of people, the temperature, the humidity and the light level in the room. | PASS |
| Q9 | unavailable, no time | Schedule information is not available. | PASS |
| Q10 | announcements not available yet | Announcements are not available yet. | PASS |

This is a software / simulated check of the frozen oracle. It is NOT the Team Leader AI-3 result (no microphone, no STT, no distance or noise, no classroom).

### 5.7 Validation

- `tests/test_answering.py`: 107 tests (AI-1 evaluation, missing / stale / unconfigured / failed state, forged-state defence in depth, accepted variants for both fixture and missing data, 30 false-match questions, invalid input, units and formatting, multi-sensor, expected-attendance vs people count, schedule placeholders, professor never guessed, capability and announcement declarations, gating, provenance, result invariants, privacy, imports / scope).
- Mutation checks on scratch copies: 53 mutants (every requested item plus boundary and state mutants), 53 killed after one literal-reason-code test was added for the single first survivor; each file restored and hash-verified.
- Full suite **1476 passed, 5 skipped, 0 failed** (reference 1369 / 5).

### 5.8 Limits

Simulated fixtures only; no live sensor, schedule, professor or announcement source exists; exact whole-utterance matching will reject any STT output that differs from a known phrase (including with a wake prefix) — measuring that is AI-3 and prefix handling is a later step; the people count is a detected-person proxy; the `SimulatedScheduleContext` shape is a test fixture and not a decided schedule format. No LLM, TTS or playback exists.

## 6. Spoken-output foundation — TTS + playback, software-only (2026-10-11)

Status: **SPOKEN-OUTPUT TTS: SOFTWARE-VALIDATED. PLAYBACK ENGINE: SOFTWARE/API-VALIDATED, PENDING PROJECT HARDWARE.** Windows development PC only. No claim of: speaker validated, Pi validated, audibility validated, voice quality, naturalness, or latency validated. AI-4, AI-6 and AI-9 are NOT COMPLETE. Not a Week 7 item. AI-2: NOT COMPLETE.

### 6.1 Architecture (approved: sherpa-onnx 1.13.8, voice en_US-lessac-medium, sounddevice, `audio/playback.py`)

```
AnswerResult.text -> TtsEngine.synthesize() -> SpeechAudio -> PlaybackEngine.play() -> PlaybackResult
```
Two independent contracts joined only by the `SpeechAudio` value type. TTS knows no device; playback knows nothing about how the audio was made. `DeterministicAnswerEngine` is unchanged and has no speech API. The announcement (AI-6) and fallback (AI-9) paths will reuse the same three pieces.

| File | Role |
|---|---|
| `speech/tts.py` | `SpeechAudio`, `TtsEngine`, typed errors, `check_text` (numpy + logger only) |
| `speech/sherpa_onnx_tts.py` | `SherpaOnnxTtsEngine`, voice manifest loader, full-package verification; `sherpa_onnx` imported lazily |
| `speech/voices/vits-piper-en_US-lessac-medium.manifest.json` | the voice's authoritative manifest (359 files with size and SHA-256); metadata only, no model data |
| `audio/playback.py` | output-only `PlaybackEngine`, `PlaybackResult`, `SoundDevicePlaybackEngine`, typed errors (new file; no existing audio module touched) |
| `tests/test_tts_contract.py`, `test_sherpa_onnx_tts.py`, `test_playback.py`, `test_spoken_output_integration.py`, `spoken_output_fakes.py` | 173 tests plus shared fakes (fake TTS, fake playback, fake `sounddevice` whose stream runs the real callback on a thread) |
| `requirements.txt`, `.gitignore` | `sherpa-onnx==1.13.8`; `models/vits-piper-*/` ignored |

### 6.2 Contracts

- **SpeechAudio** (frozen): owned read-only 1-D int16 `samples`, `sample_rate` (voice-native, never resampled), `channels` = 1, `duration_s` (derived, cannot be supplied), `tts_engine_id`, `voice_id`, `synthesis_ms`. No text, no intent.
- **TtsEngine**: properties `engine_id`, `voice_id`, `language`, `sample_rate`; `synthesize(text)`. The text must be a `str` with a non-whitespace letter or digit (punctuation-only is refused). Typed errors: `TtsError`, `TtsRuntimeUnavailable`, `TtsModelNotFound`, `TtsModelLoadFailed`, `TtsModelHashMismatch`, `TtsInputError`, `TtsSynthesisFailed`, `TtsContractError`. Silent or malformed backend output is a `TtsContractError`, never audio.
- **PlaybackEngine**: `play(audio) -> PlaybackResult` (blocks until COMPLETED or STOPPED), `stop()` (idempotent, safe when idle), `is_playing`. A second `play()` while playing raises `PlaybackBusy`. Errors: `PlaybackRuntimeUnavailable`, `PlaybackDeviceNotFound`, `PlaybackFormatError`, `PlaybackOpenFailed`, `PlaybackFailed`, `PlaybackInputError`, `PlaybackBusy`.
- **PlaybackResult** (frozen): `status` (COMPLETED, STOPPED), `audio_duration_s`, `t_play_called`, `t_stream_ready`, `t_first_buffer_submitted`, `t_finished` (one monotonic clock, `time.perf_counter`, order enforced), `device_name`, `host_api`, `stream_latency_s` (as REPORTED by the host API, never added to a timestamp), `underflow_count`.
- **Timing semantics.** `t_first_buffer_submitted` is a SOFTWARE marker: the first callback has put samples into the device buffer. It is NOT physical acoustic onset (device latency, DAC, amplifier, speaker and air come after it). **AI-4 SOFTWARE PROXY FOR SPOKEN START = `t_first_buffer_submitted`** until acoustic timing is measured on the project hardware.
- **Underflow policy.** Counted, logged, playback continues and still reports COMPLETED; other status flags are logged.
- **Stop.** `stop()` aborts the stream (best effort, not sample-perfect cancellation). Verified with a fake device: idle -> playing -> completed, and idle -> playing -> stopped.

### 6.3 Installation

`sherpa-onnx==1.13.8` and its dependency `sherpa-onnx-core==1.13.8` were added to the project `.venv` (binary wheels, Python 3.13.14, Windows). Nothing else changed: `pip freeze` before/after differs only by those two additions; `numpy 2.5.3` and `onnxruntime 1.30.0` are unchanged; `pip check`: no broken requirements.

### 6.4 Voice acquisition (one explicit step; the runtime never downloads)

Pins: packager k2-fsa/sherpa-onnx, release tag `tts-models`, asset `vits-piper-en_US-lessac-medium.tar.bz2`, 67,230,653 bytes, SHA-256 `9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e` (the digest recorded from the release in the gate; the tag itself is mutable, the digest is the pin). Upstream voice: rhasspy/piper-voices commit `c10ece1aade47bb51c153c893d14e5bf8e5b7117`, `en/en_US/lessac/medium` (sherpa repackages the model, so its `.onnx` differs byte-wise from Piper's original). Acquired 2026-10-11; the download matched the pinned digest exactly.

To repeat on another machine: download
`https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-lessac-medium.tar.bz2`, check the SHA-256 above, extract it (the archive contains one top-level folder `vits-piper-en_US-lessac-medium/`) so that its CONTENTS sit in `models/vits-piper-en_US-lessac-medium-9e3febfa/`, then verify against the committed manifest:

```bash
.venv/Scripts/python.exe -c "from speech.sherpa_onnx_tts import load_manifest, resolve_voice_dir, verify_voice_directory as v; m = load_manifest(); print(v(resolve_voice_dir(m), m))"
```
It prints 81147006 when every one of the 359 files matches.

### 6.5 Voice manifest (every runtime-relevant file; top-level entries shown, the 355 `espeak-ng-data/*` entries are in the manifest file)

Manifest: 359 files, 81,147,006 bytes in total.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| en_US-lessac-medium.onnx | 63,149,198 | `4ba07d8549906668ee855fd9abf9faf66c5db74742712ff026a159f7277fca9f` |
| en_US-lessac-medium.onnx.json | 4,885 | `efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0` |
| tokens.txt | 921 | `87c8ef66eae5473ed0cc0366b3964c736ca6c5f676c979522ea31234e47430b9` |
| MODEL_CARD | 351 | `ce49eb457742208166d399a40cdec2c7fa9db77960930031564ab56f12882645` |
| espeak-ng-data/ (355 files) | 17,991,651 | see manifest |

Voice id en_US-lessac-medium, language en-US, 22,050 Hz, mono, 1 speaker. At construction the engine checks existence, size and SHA-256 of ALL 359 files BEFORE loading, then checks that the loaded voice reports the manifest's sample rate and speaker count.

### 6.6 Licence / redistribution — ACADEMIC / NON-COMMERCIAL DEVELOPMENT ASSET

- sherpa-onnx runtime: Apache-2.0 (package metadata).
- Voice dataset: Lessac Technologies Blizzard 2013 data, released "for non-commercial use only" (dataset page, https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/).
- Bundled espeak-ng data: espeak-ng is GPL-family licensed (not independently verified here).
- Redistribution status: the voice files are NOT committed (git-ignored) and NOT redistributed from this repository; only the manifest (paths, sizes, hashes) is committed. No legal claim is made beyond these source statements. Any commercial or redistributed use needs a separate licence review.

### 6.7 Deterministic synthesis policy and real-voice check

DEVELOPMENT REPRODUCIBILITY POLICY (not a quality setting): `noise_scale = 0`, `noise_scale_w = 0`; CPU, 2 threads, speaker 0, speed 1.0 (development execution policy, not calibrated). Under it, repeated synthesis was bit-identical; with the engine defaults it was not. Prosody was not tuned and naturalness was not judged by anyone. Float samples are converted as `round(x * 32767)` clipped to int16. Real-voice tests (with the verified package): a short sentence ("There are 17 people in the room.", 1.68 s of audio) and a longer one (5.99 s) give mono int16 at 22,050 Hz, non-empty and not silent, with finite synthesis time; the longer text is longer than twice the shorter (the whole text was synthesized); repeats are identical; construction and synthesis also succeed with sockets disabled, which includes a fixed fallback sentence ("Sorry, I can't answer that right now.").

### 6.8 Development measurements (Windows, AMD Ryzen 5 7520U; NOT Pi claims)

Three fresh interpreters: project import 0.18-0.26 s; sherpa-onnx import 0.03 s once cached (about 2.6 s on its first cold import in the gate); voice verification 187-282 ms; model load 1.76-1.78 s; first / second synthesis of the short sentence 242-269 / 183-200 ms; of the longer sentence 653-762 / 664-774 ms (5.99 s of audio, real-time factor about 0.11-0.13). Playback, gated silent API smoke on the development PC (22,050 Hz mono int16, 0.3 s of silence, MME host API, default output device): stream open 257 ms, call to first buffer 296 ms, total 786 ms, reported latency 0.183 s, 0 underflows. DEVELOPMENT-PC SOFTWARE/API VALIDATION only; a laptop speaker is not project hardware.

### 6.9 Validation

- 173 new tests; targeted groups pass; full suite **1649 passed, 6 skipped, 0 failed** (reference 1476 / 5; the extra skip is the gated real-device smoke).
- Mutation checks on scratch copies: 65 mutants covering every requested item plus boundary, state and provenance mutants; 3 initial survivors (empty-text rule, engine dtype rule, manifest path-traversal rule) were redundant protections that produced the same error type, and are now killed by reason-specific assertions; final **65 killed, 0 survivors**, with and without the voice package; each file restored and hash-verified.
- Privacy: tests show text and PCM values never reach the logs. Default logs carry text length, durations, engine / voice ids, timings, device identity and error classes.
- Integrity: all 97 files baselined before the work were compared by binary SHA-256 afterwards; only `requirements.txt` and `.gitignore` differ (as intended), plus the two Team Leader docs after this section was written. No line-ending conversion was run; new files are LF.

### 6.10 Limits and what is NOT claimed

Not claimed: speaker, DAC, Pi or audibility validation; voice quality or naturalness; acoustic or end-to-end latency; AI-4, AI-6 or AI-9 completion. Importing `audio.playback` imports the `audio` package, whose existing `__init__` imports the microphone module and therefore `sounddevice` (that file is frozen); `speech/__init__.py` was likewise left unchanged. The Pi 5 / aarch64 behaviour of sherpa-onnx and of this voice is PENDING TARGET-HARDWARE VERIFICATION.

## 7. AI-6 announcement + AI-9 offline fallback — software orchestration (2026-10-11)

Status: **AI-6 SOFTWARE ORCHESTRATION: SOFTWARE-VALIDATED. AI-9 SOFTWARE FALLBACK: SOFTWARE-VALIDATED. AI-6: NOT COMPLETE (project speaker / trigger validation pending). AI-9: NOT COMPLETE (project hardware audibility validation pending).** Nothing was played; no speaker, app, schedule or audibility claim. Not a Week 7 item.

### 7.1 What was built

| File | Role |
|---|---|
| `assistant/spoken_output.py` | `SpokenOutputController`, `AnnouncementRequest`, `AnnouncementSource`, `FallbackReason`, `RequestType`, `OutputStatus`, `SpokenOutputResult`, the fixed texts |
| `tests/test_spoken_output_controller.py` | 93 tests |

No existing module was changed (106 baselined files compared by binary SHA-256: 0 changed before this documentation edit). The controller holds exactly ONE `TtsEngine` and ONE `PlaybackEngine`; there is no second speech stack.

### 7.2 Contract

- Three explicit methods name the request type (the type is never inferred from text): `speak_answer(text, message_id=None)`, `speak_announcement(AnnouncementRequest)`, `speak_fallback(FallbackReason)`; plus `stop()` and `is_active`.
- `AnnouncementRequest(announcement_id, text, source, trigger_id=None)`: the text is spoken exactly as supplied and must be explicit and non-empty; `source` is one of APP, SCHEDULE, MANUAL_TEST, UNKNOWN (a plain string is refused). There is no priority field: no priority policy exists until AI-7.
- `FallbackReason` (ANSWER_SERVICE_UNAVAILABLE, NETWORK_UNAVAILABLE, UPSTREAM_TIMEOUT, INTERNAL_SERVICE_FAILURE) is classified BY THE CALLER; this module never detects the network or catches arbitrary exceptions to apologise. An unclassified or unknown reason is refused.
- `SpokenOutputResult` (frozen; no text, no audio, no PCM): request type, status, message id, origin, trigger id, text length, TTS engine and voice ids, playback status, audio duration, synthesis time, controller timestamps (accepted, TTS start, TTS end), playback timestamps (play called, first buffer submitted, finished), error stage and error type. Statuses: COMPLETED, STOPPED, TTS_FAILED, PLAYBACK_FAILED, REJECTED (no hardware-validated or audible state exists).
- `t_first_buffer_submitted` stays the AI-4 SOFTWARE PROXY for the start of speech (not acoustic onset). Give the controller and playback engine the same clock before comparing their timestamps.

### 7.3 Fixed texts

- Demo announcement (ONE fixed, neutral sentence; source MANUAL_TEST; id `demo-announcement-1`): "Attention please. This is a VESTA classroom announcement." It states nothing about the room, a lecture or anyone's whereabouts. The module contains no professor, lateness or occupancy logic (a test scans the source for it); a professor-absent announcement is spoken only if an authoritative caller supplies exactly that text.
- Offline fallback (short, no network, no LLM, no explanation that could be wrong): "I'm sorry, I can't complete that request right now."

### 7.4 Failure handling (nothing hidden)

| Event | Result |
|---|---|
| Text unspeakable (empty, punctuation-only) | REJECTED, stage INPUT |
| Another output active | REJECTED, stage BUSY (not queued, TTS not called) |
| Any `TtsError` | TTS_FAILED, stage TTS; playback never called |
| Any `PlaybackError` | PLAYBACK_FAILED, stage PLAYBACK; TTS ran once, playback ran once; never COMPLETED |
| `PlaybackBusy` from the engine | REJECTED, stage PLAYBACK |
| Playback reports STOPPED / `stop()` during synthesis | STOPPED (never converted to COMPLETED) |
| Any other exception, or a wrong return type from an engine | PROPAGATES (a bug, not a service failure); the controller is released first |

The original error class is preserved in `error_type`; messages are never recorded. `stop()` cannot cancel a synthesis in progress, and a stop that lands between the end of synthesis and the start of playback can be missed (documented limitation; AI-7 owns arbitration).

### 7.5 Validation

- 93 tests: request-type explicitness, announcement and fallback paths, answer integration (`DeterministicAnswerEngine` unchanged), same-stack guarantee, TTS failure, playback failure, stop, busy (also during synthesis, no queueing), result contract, privacy (answer, announcement and fallback text, exception messages and PCM never reach logs), imports, plus a real-voice tier with the network disabled.
- Real voice (the manifest-verified en_US-lessac-medium) + fake playback: the fixed fallback sentence and the demo announcement synthesize offline and the controller reports a software COMPLETED. Nothing is audible.
- Mutation checks on scratch copies: 41 mutants (every requested item plus boundary and state mutants), 39 killed and 2 EQUIVALENT (removing the import-time fallback check, which a test also asserts; and re-asserting the busy flag inside the TTS-failure branch, which the finally clause clears anyway), with and without the voice package; each file restored and hash-verified.
- Full suite **1742 passed, 6 skipped, 0 failed** (reference 1649 / 6).

### 7.6 Not claimed

No speaker, Pi or audibility validation; no real app or schedule trigger; no real failure-path trigger; no AI-7 arbitration. **AI-6 and AI-9: NOT COMPLETE.** AI-6 needs an app or schedule trigger, the project speaker, real playback and a clearly audible result. AI-9 needs a real failure-path trigger, the project speaker and a clearly audible fallback.

## 8. AI-7 spoken-output arbitration — software-only (2026-10-11)

Status: **AI-7 SOFTWARE ARBITRATION: SOFTWARE-VALIDATED. Team Leader AI-7: PARTIAL / PENDING REAL HARDWARE TEST.** The Team Leader's "no overlapping speech in testing" is a test of the real speaker; this section establishes only that the software never lets two spoken outputs run at once. Nothing was played; no speaker, Pi or audibility claim. AI-6 and AI-9 remain NOT COMPLETE. Not a Week 7 item.

### 8.1 What was built

| File | Role |
|---|---|
| `assistant/output_arbiter.py` | `OutputArbiter`, `decide()` (the pure policy), `ArbitrationRequest`, `ArbitrationResult`, `ArbitrationOutcome`, `SpeechSubmission`, `Decision`, `RequestClass`, `OutputPhase`, `POLICY_VERSION` |
| `tests/test_output_arbiter.py` | 82 tests |

The arbiter owns WHO MAY SPEAK NOW and nothing else: no TTS, no playback of its own, no answering, STT, sensor, schedule or app logic. It builds the one `SpokenOutputController` (and with it the one TTS / playback pair) that may speak. No existing module was changed (108 baselined files compared by binary SHA-256: 0 changed before this documentation edit). The new files are LF.

### 8.2 Policy `AI7_MIDTERM_V1`

| Rule | Behaviour |
|---|---|
| Class | CONVERSATION = ANSWER and FALLBACK; ANNOUNCEMENT = ANNOUNCEMENT. Follows the request TYPE chosen by the caller; never inferred from text |
| Priority | CONVERSATION outranks ANNOUNCEMENT. No numeric priorities; FALLBACK has no emergency rank, so ANSWER vs FALLBACK is decided by first arrival |
| Contention before output starts | One atomic decision (`submit_batch`): highest class wins, then earliest arrival. Every loser gets an explicit result |
| Output already active (synthesizing or playing) | Nobody is granted. Conversation vs active announcement: **RETRY_REQUIRED**. Announcement vs active conversation: REJECTED_LOWER_PRIORITY. Same class: REJECTED_ACTIVE_OUTPUT |
| Preemption | None. An active announcement is never interrupted |
| Queue | None. No request is held and replayed |
| Concurrency | One request owns the arbiter from GRANTED until its result is returned: at most one synthesis and at most one playback at any time |

Decisions: GRANTED, REJECTED_ACTIVE_OUTPUT, REJECTED_LOWER_PRIORITY, RETRY_REQUIRED, INVALID_REQUEST. An `ArbitrationResult` (frozen; no text, no audio) carries request id, decision, request type, request class, policy version, reason code, and the active request type and phase when one was active. Reason codes: IDLE_GRANTED, WON_CONTENTION_BY_CLASS, WON_CONTENTION_FIRST_ARRIVAL, LOST_CONTENTION_LOWER_CLASS, LOST_CONTENTION_LATER_ARRIVAL, ACTIVE_ANNOUNCEMENT_NON_PREEMPTIVE, ACTIVE_CONVERSATION_ANNOUNCEMENT_REJECTED, ACTIVE_OUTPUT_SAME_CLASS, INVALID_ARGUMENT.

Lifecycle: IDLE -> SYNTHESIZING (granted) -> PLAYING (entered atomically with the cancel check, just before the engine plays) -> IDLE. There is no STOPPING phase: a stop during playback is a request to the playback engine and the phase stays PLAYING until it returns. "Busy" (the arbiter owns a request) is not the same as "audio playing".

### 8.3 Documented limitation: why a conversation cannot cut in

A running synthesis cannot be cancelled (no thread killing) and a stop is not sample-perfect, so there is no concurrent path and no fake cancellation. A conversation request that arrives during an active announcement receives an explicit RETRY_REQUIRED and the caller must submit again once the announcement has finished. Preemption could be revisited only after playback cancellation is validated on the project hardware.

### 8.4 Stop / cancellation (closes the gap documented in section 7)

`stop()` records a cancel flag under the same lock that guards the phase. The synthesis finishes (it cannot be cancelled) but the playback engine is reached only through a guard that, under that lock, checks the flag and moves SYNTHESIZING -> PLAYING in one step; so a stop during synthesis, or in the instant after synthesis and before playback, yields STOPPED without the playback engine ever being called. A stop once PLAYING calls the engine's own `stop()` (waiting a bounded `STOP_REGISTRATION_WAIT_S = 1.0` s, real time, an uncalibrated development value, for an engine that was just called to report that it is playing). The flag is cleared on release; a stop while idle returns False and leaves no state. STOPPED is never turned into COMPLETED; a stop that arrives after the engine returned but before release does not touch the engine and the result stays COMPLETED.

### 8.5 Validation (software only)

- 82 tests: the decision matrix (every active type x phase x incoming type), scenarios A-N (answer, announcement, fallback alone; answer / fallback vs announcement contending; announcement active + answer; answer active + announcement; two announcements; two answers; stop during synthesis, after synthesis before playback, during playback, while idle; a request after completion), invalid requests (nothing is granted, nothing synthesized), release after every failure kind (a bug in an engine still releases the arbiter), policy version in every result, and a real-voice + FAKE-playback test.
- Real threads: rounds of 8 simultaneous requests held at a barrier with the winner held inside synthesis or playback until every loser has returned. Each round has exactly one GRANTED, one synthesis, one playback call, no queued request, maximum concurrent playback 1 and maximum concurrent synthesis 1, and the arbiter is not left busy. No sleep decides an outcome: sleeps only poll until the losers have returned, or give a blocked `stop()` time to start waiting.
- Privacy: arbitration log lines carry only ids, types, classes, decision, reason, active type / phase and the policy version; answer text, announcement text (granted or not) and exception messages never reach the logs. The module imports no audio-device, TTS-runtime, STT or network code.
- Mutation checks on scratch copies: 49 mutants (priority reversed, announcement beats conversation, busy guard removed in two places, two requests granted, playback and TTS concurrency allowed, stop flag ignored, stop flag leaking, stop during synthesis still plays, STOPPED to COMPLETED, owner not released on error, silent queueing, request type inferred from text (two variants), policy version omitted, text in logs (three variants) and more), run with and without the voice package: 46 killed, 3 EQUIVALENT: 08a and 08b (the cancel flag is cleared both at grant and at release by design, so removing either one alone changes nothing; removing both is killed) and 39 (a constant arrival sequence: the batch list order already is the arrival order). Four first-run survivors exposed test gaps (fallback wording inside an answer, a granted announcement's text in the logs, an unspeakable announcement text, a stop after the engine returned) and were closed with tests. Every file was restored and hash-verified.
- Full suite **1824 passed, 6 skipped, 0 failed** (reference 1742 / 6).

### 8.6 Not claimed

No speaker, Pi or audibility validation; no real playback cancellation; no live app, schedule or voice trigger; nothing in the repository yet routes the answering, announcement or fallback paths through the arbiter (they can still reach the controller directly, so "single owner" holds only once the callers are wired through it). **Team Leader AI-7: PARTIAL / PENDING REAL HARDWARE TEST.** Needed to complete it: route the callers through the one `OutputArbiter`, then on the project speaker play an announcement, ask a question during it, and confirm by listening that there is no overlap and that a stop during real playback works.

## 9. AI-8 state synchronization — LISTENING / CHECKING / SPEAKING contract, software-only (2026-10-11)

Status: **AI-8 STATE CONTRACT: SOFTWARE-VALIDATED. Team Leader AI-8: PARTIAL / PENDING VISION-FACE + HARDWARE LIVE TEST.** **AI-7 SOFTWARE INTEGRATION: COMPLETE at call-site level. Team Leader AI-7: PARTIAL / PENDING REAL HARDWARE TEST.** AI-6 and AI-9 remain NOT COMPLETE. Nothing was played, no LED, display or face was driven, and no live synchronization was observed. Not a Week 7 item.

### 9.1 Call-site audit (done before any wiring)

Searched every non-test Python file for `SpokenOutputController`, `OutputArbiter`, `TtsEngine`, `PlaybackEngine`, `speak_answer`, `speak_announcement`, `speak_fallback`, `.play(` and `.synthesize(`.

| Call | Where (non-test) | Verdict |
|---|---|---|
| `SpokenOutputController(...)` | `assistant/output_arbiter.py` only | the arbiter builds the ONE controller |
| `speak_answer` / `speak_announcement` / `speak_fallback` calls | `assistant/output_arbiter.py` only | via the arbiter |
| `.synthesize(...)` | `assistant/spoken_output.py` only (the controller) | single TTS user |
| `.play(...)` | `assistant/output_arbiter.py` (the guard) and `assistant/spoken_output.py` (the controller, on ITS engine, which in an arbiter-built controller is the guard) | single playback user |
| `SoundDevicePlaybackEngine(...)`, `SherpaOnnxTtsEngine(...)`, `OutputArbiter(...)` | none (only tests build them) | no runtime composition root exists |

Result: **no runtime bypass exists, and there is no runtime orchestrator yet.** No existing caller needed rewiring, so no caller was changed. The full table is pinned by `test_every_runtime_path_to_speech_goes_through_the_output_arbiter` (any new caller anywhere fails it and forces a review), `test_no_runtime_module_constructs_a_real_engine_or_a_second_arbiter_yet`, `test_only_the_arbiter_imports_the_controller_in_runtime_code`, and a test that the controller an arbiter built can reach playback only through the guard. No second arbitration mechanism was created. The day a runtime orchestrator exists it must be the single composition root (engines -> `OutputArbiter`) and the audit test is then updated deliberately.

### 9.2 What was built

| File | Role |
|---|---|
| `assistant/state.py` (new) | `AssistantState`, `StateColor`, `COLOR_OF`, `ALLOWED_TRANSITIONS`, `StateReason`, `StateEvent`, `AssistantStateManager`, typed errors |
| `tests/test_assistant_state.py` (new, 60 tests) | contract, unit, subscriber, privacy, concurrency |
| `tests/test_state_integration.py` (new, 39 tests) | arbiter + state scenarios, the playback first-buffer signal, the fake Face, the call-site audit |
| `assistant/output_arbiter.py` (additive) | optional `state=` seam: publishes the lifecycle of the ONE granted request; unchanged behaviour without it (all 82 AI-7 tests pass unmodified) |
| `audio/playback.py` (additive, backward compatible) | optional first-buffer listener (see 9.9) |

No other pre-existing file changed (108 baselined files compared by binary SHA-256 before this documentation edit: 106 identical, the two above differ). All new and edited code files are LF.

### 9.3 State contract and colours

| State | Colour (semantic) | Meaning |
|---|---|---|
| IDLE | NO_ACTIVE_COLOR | nothing captured, processed or spoken. No authoritative idle colour exists, so none is invented |
| LISTENING | **RED** | VESTA is actively collecting the user's spoken utterance |
| CHECKING | **BLUE** | the request is being processed, before spoken output has started |
| SPEAKING | **GREEN** | software playback of a spoken output is active |

Colours are SEMANTIC names, not RGB or brightness; the Vision/Face owner decides rendering. `COLOR_OF` and `ALLOWED_TRANSITIONS` are read-only mappings and the colour of every event is validated against them, so an accidental remapping fails tests.

### 9.4 Transition policy

IDLE -> LISTENING | CHECKING; LISTENING -> CHECKING | IDLE; CHECKING -> SPEAKING | IDLE; SPEAKING -> IDLE. The same state again is an idempotent no-op (no event, no sequence number). Anything else raises `InvalidStateTransition` and changes nothing. Truthful exits are supported: LISTENING -> IDLE (no speech / cancelled), CHECKING -> IDLE (a flow that does not speak, or a stop / failure before audio), SPEAKING -> IDLE (completed, stopped or failed). Barge-in (speaking while listening) is not supported.

### 9.5 Semantics

- **LISTENING** starts when a speech-input session is activated and ends when the utterance capture finishes (-> CHECKING) or is abandoned (-> IDLE). Passive wake-word monitoring is IDLE. It is never held open indefinitely.
- **CHECKING** is the period after the utterance is complete (or, for an output with no user utterance, after the arbiter admitted it) until the first buffer of spoken audio reaches the device. It can include STT finalization, question matching, TTS synthesis and arbitration admission. **There is no LLM; CHECKING does not mean anything is thinking.**
- **SPEAKING** starts at the playback layer's first-buffer signal (the software proxy used by AI-4), never at TTS completion and never at admission, and ends when playback completes, stops or fails. **SPEAKING means SOFTWARE PLAYBACK ACTIVE, not that sound is audible**: device latency, DAC, amplifier, speaker and room all come after the marker.

### 9.6 Announcement and fallback paths

One consistent policy for every spoken output: IDLE -> CHECKING (OUTPUT_ADMITTED) -> SPEAKING (PLAYBACK_STARTED) -> IDLE. A standalone announcement or fallback never shows LISTENING (not faked). A fallback during a user interaction is LISTENING -> CHECKING -> SPEAKING -> IDLE (the arbiter's CHECKING is then a no-op). There is no IDLE -> SPEAKING shortcut.

### 9.7 Arbiter integration (one lifecycle, not a parallel wrapper)

Only the granted request publishes: CHECKING at admission, SPEAKING from the playback thread when the engine reports its first buffer (and only if the arbiter is still in PLAYING), IDLE just before the arbiter releases (so the next request cannot be admitted ahead of it). A rejected, retry-required, invalid or busy request publishes nothing, so it cannot disturb the active output's state. IDLE reasons: PLAYBACK_COMPLETED, PLAYBACK_STOPPED (stopped while speaking), OUTPUT_STOPPED_BEFORE_PLAYBACK, PLAYBACK_FAILED (from SPEAKING, or from CHECKING when it never started), TTS_FAILED, OUTPUT_REJECTED, OUTPUT_ERROR (a software bug propagated; the arbiter is still released). A state error is logged and counted (`state_sync_error_count`) but never stops the speech. A state-publishing arbiter refuses a playback engine without the first-buffer signal at construction instead of guessing. No state is derived from any wording.

### 9.8 Subscriber interface and failure handling

`subscribe(listener, replay_current=False)` returns an unsubscribe function; `current_event`, `current_state` and `current_color` can be read at any time, so a client that connects late needs no transition to learn the state. Listeners run synchronously, in registration order, under the manager's lock after the state is committed, so each sees events in sequence order and `current_state` equals the event it handles. A listener that raises is caught, logged (class name only) and counted (`listener_failure_count`); it never changes the state or stops the other listeners. A listener must not call `transition()` (it raises `StateReentrancyError` inside the listener) and must be fast: hand the event to its own thread or queue. No GUI framework is required.

### 9.9 The one change outside the new files: the first-buffer signal

`PlaybackEngine` had no way to say that its first samples reached the device (`t_first_buffer_submitted` is only known when `play()` returns), and the arbiter's PLAYING phase begins before the engine has opened its stream, so SPEAKING would have been published for a playback that then failed to open. A minimal additive hook was added: `supports_first_buffer_listener` (default False) and `set_first_buffer_listener()` on the contract, implemented by `SoundDevicePlaybackEngine`. The PortAudio callback only sets an event; the listener is called on `play()`'s own thread, after the first buffer and before `play()` returns, and never when playback fails or is stopped before the first buffer. A raising listener is logged and does not affect the playback. All 62 pre-existing playback tests pass unmodified.

### 9.10 Vision / Face interface audit

Searched the repository for any existing face, ring, LED, vision-status, UI-state or display-state contract. None exists (`vision/` is camera perception and draws annotation colours on images; `fusion/` is sensor fusion). The only requirement found is in `CLAUDE.md` (LED states are to be agreed with the team; do not hard-code final visual behaviour). So only the abstract state-event contract was created. No ESP32 protocol, GPIO, serial, BLE, LED driver, display code or network transport was added or invented.

### 9.11 Sequence numbers, timing, privacy

- Sequence: the initial IDLE event is 0; each emitted event is exactly previous + 1; no-ops and refused transitions consume none. Consumers drop events with a sequence number not greater than the last seen.
- Timestamps: `time.monotonic()` (injectable), read inside the critical section so they never decrease; a clock that goes backwards is refused. Wall time is never used for ordering.
- Privacy: events and logs carry state, previous state, colour, sequence, reason code (a CLOSED enum, so no free text can be a reason), optional request id (validated id pattern) and timing. Tests prove that answer, announcement and fallback text, transcripts and exception messages never reach an event or a log.

### 9.12 Validation (software only)

- 60 + 39 new tests: every allowed and refused transition (full matrix), the colour pins, idempotence, sequence and timestamp monotonicity, current-state query and replay, ordered delivery to several subscribers, a broken subscriber, re-entrancy, immutability, privacy, a no-hardware / no-network import check, and the AI-7 scenarios A-N-style flows (answer, announcement, standalone fallback and fallback during an interaction, busy or rejected request, stop during synthesis, stop between synthesis and playback, stop before the first buffer, stop during playback, playback failure before and after the first buffer, TTS failure, a software bug, an invalid request) plus a fake Face subscriber that receives RED, BLUE, GREEN, NO_ACTIVE_COLOR in order (SOFTWARE-ONLY, not live synchronization).
- Concurrency (barriers, events and invariants; the clock sleep only widens a race window): simultaneous identical transitions produce exactly one event; competing different transitions and 8-thread random walks always leave one valid serial history (contiguous sequence, previous-to-next chain, identical streams for two subscribers, final state equals the last event); simultaneous output requests publish exactly one CHECKING / SPEAKING / IDLE cycle with maximum concurrent playback 1.
- Mutation checks on scratch copies: 54 mutants (each colour mapped wrongly, sequence not incremented, event delivered before the state changes, stale event delivered, subscriber exception escaping or leaving the old state, announcement entering LISTENING, a busy or rejected request entering SPEAKING, SPEAKING at TTS completion / playback request, a stop during synthesis entering SPEAKING, completion leaving SPEAKING active, a text-derived request id or reason, the lock removed (duplicate sequence numbers), wrong transition-table entries, and the playback hook's own failure modes), run with and without the voice package: 52 killed, 2 EQUIVALENT (13a, 14a: removing the reason or request-id check in `transition()` alone changes nothing because `StateEvent` validates the same field again; removing both is killed). Two first-run survivors exposed real test gaps (forged first-event combinations and a stray first-buffer signal outside PLAYING) and were closed. Every file was restored and hash-verified.
- Full suite **1923 passed, 6 skipped, 0 failed** (reference 1824 / 6).

### 9.13 Not claimed

No live Vision/Face synchronization, LED or display rendering, speaker, Pi or audibility validation; no runtime orchestrator drives LISTENING or CHECKING from the real input pipeline yet (the input side calls `transition()` itself in the tests); no policy for an announcement arriving during another interaction's LISTENING or CHECKING (the single visible state describes one activity at a time, not enforced here); no barge-in. **Team Leader AI-8: PARTIAL / PENDING VISION-FACE + HARDWARE LIVE TEST. Team Leader AI-7: PARTIAL / PENDING REAL HARDWARE TEST.**

### 9.14 Vision / Face handoff contract

1. `from assistant.state import AssistantStateManager, AssistantState, StateColor, StateEvent`; use the ONE manager instance the AI side's composition root creates (none exists yet; the contract is in-process Python, no transport).
2. `unsubscribe = manager.subscribe(callback, replay_current=True)`; `callback(event)` receives a frozen `StateEvent(state, previous_state, color_semantic, timestamp_monotonic, reason_code, sequence_number, request_id)`. Alternatively poll `manager.current_event`.
3. Colours are semantic: LISTENING = RED, CHECKING = BLUE, SPEAKING = GREEN, IDLE = NO_ACTIVE_COLOR. RGB values, brightness, animation, the idle appearance and any ESP32 protocol belong to the Vision/Face and hardware side and are NOT defined here.
4. Ignore any event whose `sequence_number` is not greater than the last one applied. Timestamps are monotonic and in-process; they are not wall time and not comparable across processes.
5. The callback runs synchronously under the manager's lock: return quickly, hand the event to your own thread or queue, and never call `manager.transition()`. An exception in the callback is caught and counted; it will not change the state.
6. SPEAKING means software playback is active (first buffer reached the device), not that sound is audible.
7. Acceptance for the Team Leader item remains a live test with the real face / LED and the project speaker in which all three states are seen to match.

## 10. AI-2 Stage B live-capture harness — software-only (2026-10-16)

**Status: STAGE B CAPTURE HARNESS: SOFTWARE-VALIDATED WITH FAKE INPUT; LIVE MICROPHONE: NOT VALIDATED; TEAM LEADER AI-2: NOT COMPLETE.** No physical hardware was available and none is claimed. Week 7 is unchanged (IN PROGRESS, Task 5 real evaluation pending recordings).

### 10.1 Input-contract audit (done before any code)

`ReSpeakerMicrophone` (PortAudio callback thread) puts each block into a bounded `queue.Queue(50)`; when it is full the OLDEST block was discarded with no counter, no marker and no position, and a PortAudio `status` flag (input overflow) was only logged. `read_frame()` returned a bare array, so a consumer could not tell that anything was missing. `VoicePipeline.feed()` runs STT synchronously, so one call can block its caller for seconds, and `feed(start_sample=...)` can only check what the caller tells it. Conclusion: exact continuity CAN be established for everything the callback receives, by counting delivered frames inside the callback (a dropped block then appears as a jump of exactly its frame count); it CANNOT be established for loss below PortAudio that it does not flag, and a flagged overflow has no size. No sample index is inferred from a wall clock anywhere.

### 10.2 The one change to a frozen file

`audio/microphone.py` (additive, backward-compatible): `MicBlock` (samples, sequence, `start_sample`, epoch, overflow flag), `MicTelemetry` (cumulative received / dropped / overflow counters, queue depth), `read_block()`, `telemetry()`, an optional `queue_max_blocks` argument validated as an integer >= 1, a new epoch with positions restarting at 0 and a flushed queue on each `start()`, and drop counting where the drop happens (an error log on the first drop and every 100th). `read_frame()` still returns the bare array and the `AudioSource` interface is untouched; every existing audio test passes unchanged. SHA-256 before `18fe3d620070806afb8481489b47be14567f8a4e1882413430fc7cf573729f2b`, after `566e677daee3bf8fa7423b70407a107360eb3f4b5f82379b91a1fb4a478c60fc`.

### 10.3 The harness (`scripts/ai2_stage_b_live.py`)

Capture worker: only reads blocks and hands them to a bounded queue (default 400 blocks = 25.6 s = about 0.8 MB); a full queue drops the block it just read, counts it exactly and never blocks the microphone reader. Processing worker: the single place that checks positions; it runs the frozen `VoicePipeline` (wake word, Silero VAD, faster-whisper) and records one structural entry per interaction. Any discontinuity (sequence gap, overlap, epoch change, leading loss, PortAudio overflow flag, trailing loss) is logged, the open utterance is discarded WITHOUT transcription, `restart_stream()` is called and the next block begins a new stream, so audio on the two sides of a gap is never joined into one transcript. Failures (microphone stall after N consecutive read timeouts, any worker or sink exception, a source that cannot start) set one abort flag, are recorded by stage and exception class only, and are re-raised by `stop()` / `run()` as `StageBWorkerError` carrying the complete evidence record. Models are loaded and verified (hash-checked VAD artifact and STT snapshot) BEFORE capture starts; nothing is downloaded and nothing is substituted. Wake detection is never bypassed; `--activation manual` is an explicit development mode (Enter arms the next chunk) whose results are labelled MANUAL_DEVELOPMENT / NOT WAKE-ACTIVATED. Segmenter values are harness-local DEVELOPMENT / UNCALIBRATED (the Stage A values plus a 2 s pre-roll); `config/settings.py` is unchanged.

### 10.4 Evidence and labels

One JSON record per session, exclusive-create, refused inside the repository: source type, validation label, activation mode, models and their verification flags, capture duration, blocks and samples captured / processed / dropped / discarded, microphone telemetry deltas, every discontinuity (reasons, exact callback-domain gap or none, overflow flag, whether an open capture was discarded), every interaction's status, segment outcome, STT status, timings and error class, the failure class, and a continuity scope note. No transcripts or PCM (the writer refuses such keys). Labels: a fake-input run is `SOFTWARE-VALIDATED (FAKE INPUT; NO MICROPHONE)`; a run on the real microphone is `LIVE-MICROPHONE RUN - UNREVIEWED (NOT HARDWARE-VALIDATED until a person has checked this evidence on the device)`. The harness cannot produce HARDWARE-VALIDATED.

### 10.5 Validation (software only)

96 new tests: 25 on the telemetry (PortAudio replaced, callback called directly) and 71 on the harness with a scripted block source, a scripted wake detector, an energy VAD and a fake STT (Events and barriers; the only waits are failure guards; 15 repeated runs without a failure). They cover continuous ordered input, wake-activated utterance to STT with sample-exact PCM, no activation without a wake, slow STT while capture keeps reading, exact harness-queue drops and the identity captured = processed + dropped + discarded, microphone-side drops reaching the evidence, each discontinuity kind and recovery (including two gaps in one session), microphone stall and isolated timeouts, capture / pipeline / sink / source-start failures, graceful and repeated start / stop with no leaked worker threads, bounded queues, no transcript in logs or evidence, labels, model-load and verification failures, and the command line (exit codes 0 / 2 / 3, evidence never overwritten or placed inside the repository). One test runs the real Silero and tiny.en engines through the harness on a synthetic signal (plumbing only; skips when a model is absent). **Mutation campaign on scratch copies: 53 mutants, 53 killed** (drop counters, wrong positions in three places, silent gap merging, blocking put, restart omitted, partial utterance reused, unbounded queue, four ways of swallowing a failure, transcript leaked into the record and into the log, false hardware labels, missing trailing-loss / overflow / epoch / leading-loss / overlap detection, stall handling, verification skipped, overwrite allowed, evidence guard removed, and more). Two were killed by a hang (timeout) rather than a failing assertion. A first campaign left three survivors and a first-run failure; they exposed an uncounted block dequeued at abort, which was fixed and is now tested. Full suite: **2494 passed, 6 skipped, 0 failed** (reference 2398 / 6).

### 10.6 Not claimed

No live microphone capture, no PortAudio / ALSA behaviour on the Pi 5, no ReSpeaker device name or channel layout, no wake / VAD / STT accuracy, no latency on the Pi, no end-to-end recognition of live speech. Loss below PortAudio that it does not flag is undetectable by this software, so `exact_continuity: true` means "no loss was reported". The microphone mutants were killed without the git-ignored models (the only model-dependent test is the plumbing test above). At the time of writing the hand-off documents still said that no live-microphone harness exists; the technical hand-off was corrected in section 11.8, and the GitHub readiness document (`docs/handoff/AI_GITHUB_PR_READINESS.md`) was deliberately not touched.

### 10.7 First live test on the device (for the Hardware Engineer)

Preconditions: the Pi environment installed, the three models acquired as documented, the ReSpeaker visible to `sounddevice` under the configured name hint. Run `python -m scripts.ai2_stage_b_live --out-dir <folder outside the repository> --duration-s 120`, say the wake phrase followed by a question a few times, then read the single `*.stage_b.json` record: `exact_continuity`, the discontinuity list, the drop counters, the interaction statuses and timings. Use `--show-transcript` only while a person is listening to judge the text. Use `--activation manual` only to try the VAD and STT stages when the wake model is not working yet. Report the run as LIVE-MICROPHONE RUN, and as hardware-validated only after a person has checked the evidence on the device.

## 11. End-to-end software composition — `assistant/runtime.py` (2026-10-16)

**AI END-TO-END COMPOSITION: SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT. REAL LIVE VOICE ASSISTANT: NOT VALIDATED. AI-2: NOT COMPLETE — HARDWARE PENDING.** Software only; no microphone or loudspeaker was used and none is claimed.

### 11.1 Audit: what was genuinely composable

`VoicePipeline` has no event hooks, but it takes its detector and STT engine by injection, and `OutputArbiter` already publishes CHECKING / SPEAKING / IDLE for the output it grants. The three lifecycle facts the state machine needs are therefore real and observable WITHOUT touching a frozen module: the wake detector reporting a detection (a delegating `WakeWordDetector` wrapper), the pipeline handing a finished utterance to STT (a delegating `SttEngine` wrapper, called exactly once per segment), and the playback engine's first buffer (the arbiter, already). No STOP condition arose and no frozen module was edited. Gaps that remain, documented rather than patched: the pipeline offers no finer event between the wake and the end of the utterance; a wake and its decision inside one `feed()` call leave LISTENING unobserved (it is then simply not emitted); nothing mutes capture while the assistant speaks (no half-duplex, no echo cancellation in software); the answer engine is deterministic and offline, so the only "service failure" is one a future engine raises explicitly.

### 11.2 Architecture

`VoiceAssistantRuntime` owns the `VoicePipeline`, one answer engine (default `DeterministicAnswerEngine`), one `OutputArbiter` (which builds the one controller over the injected TTS and playback engines) and one `AssistantStateManager`. Everything is injected; the module imports no model backend, no `sounddevice`, no network library (an AST test pins this). `feed()`, `finish()`, `restart_stream()` and `cancel_capture()` belong to the one thread that feeds audio; `handle_result()` is the entry point for a result produced elsewhere (for example by the Stage B harness); `stop_output()` is thread-safe. The default context declares no capability, so an unconfigured runtime answers "I can't answer that yet" to everything; the caller supplies the capability declaration and the classroom state (there is no live source for it).

### 11.3 Voice-to-answer and answer-to-TTS flow

TRANSCRIBED -> `AnswerRequest(text, context)` -> `AnswerResult` -> `OutputArbiter.submit_answer(answer.text)` -> TTS -> playback -> `PlaybackResult`; the runtime returns a `RuntimeOutcome` (kind, pipeline status, answer status / intent / question id / source / reason code, arbitration result, spoken-output result, answer time) with no transcript, answer text or PCM. Unavailable room information is spoken as unavailable ("I don't have a current people count."); professor arrival is always unavailable; an unsupported question gets the unsupported answer.

### 11.4 Outcome policy

WAKE_ONLY, NO_SPEECH, TOO_SHORT, STT_EMPTY and INVALID_ACTIVATION are never turned into a question (nothing answered, synthesized or played); MAX_DURATION and STREAM_ENDED may be truncated and are never treated as complete commands. A typed STT failure requests the fixed fallback sentence (reason INTERNAL_SERVICE_FAILURE); an `ExpectedServiceFailure` from the answer engine requests the fallback with its own reason; ANY other exception (a TypeError, an `AnswerContractError`, a bug in the context provider, a contract violation in TTS or playback) returns the state to IDLE and propagates. A request the arbiter does not grant (busy, lower priority, retry required) returns the actual `ArbitrationResult`; nothing is synthesized or played and the state of the active output is not disturbed. TTS and playback failures are reported as such and not apologised for.

### 11.5 States

LISTENING (RED) at the wake detection, CHECKING (BLUE) when the utterance reaches STT, SPEAKING (GREEN) only at the playback engine's first buffer (a test holds playback before it and checks the state is still CHECKING after TTS finished), IDLE at the end. Input-side transitions are skipped while the arbiter owns the speaker and counted (never forced) when the transition table refuses them; cancelling a capture or restarting the stream ends LISTENING with CAPTURE_CANCELLED.

### 11.6 Software demo

`python -m scripts.vesta_runtime_demo [--show-text] [--tts real]`: three scripted utterances through scripted fake wake / VAD / STT, fake playback and the real answering, arbiter and state code; output is deterministic and shows pipeline status, answer, arbiter decision, output status and the state sequence LISTENING(RED) -> CHECKING(BLUE) -> SPEAKING(GREEN) -> IDLE three times. `--tts real` uses the real local voice for synthesis only (playback stays fake; it exits 3 with a clear message if the voice cannot be loaded and substitutes nothing).

### 11.7 Stage B review: the two "timeout" kills and the queue

Both survivors-by-hang were legitimate deadlocks, not slow tests: with the capture exception swallowed (S08c) the capture worker dies silently and the processing worker waits forever for an end marker; with the stall check removed (S17) a silent source is never detected. The old tests used an unbounded `run()` and were stopped only by the campaign's outer timeout. Every test that expects a failure now runs with a bounded wait (`run(duration_s)`, 3 s) and then asserts on the failure report, so the same mutants now die on an assertion ("did not raise" / wrong failure stage) and the repeated campaign reports **53 of 53 killed with 0 timeouts**. Queue arithmetic (no latency claim): the processing queue holds at most 400 blocks x 1024 samples = 409,600 samples = **25.6 s of 16 kHz audio = 819,200 bytes** of PCM; the microphone's own queue holds 50 blocks = 3.2 s. While STT runs or an answer is spoken, newer audio waits behind it for up to 25.6 s; beyond that the harness drops the newest blocks and reports the exact loss as a discontinuity, and a runtime driven by the harness cannot read new results until its spoken output returns. Measurement, this Windows development PC only (not a Raspberry Pi), real openWakeWord, idle, 469 blocks of low-level noise: 3.97 ms per 64 ms block (0.062 x real time), so a full 25.6 s backlog would take about 1.6 s to drain; this says nothing about STT, the Pi or the speaking case, and no latency bound is claimed or tested.

### 11.8 Documentation consistency

The technical hand-off (`docs/handoff/FATIMA_AI_HARDWARE_HANDOFF.md`) said that no Stage B harness and no composition root exist, and its Test B was labelled blocked. It now states that both exist as SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT, that Test B is runnable on the device with `python -m scripts.ai2_stage_b_live ...` (LIVE NOT VALIDATED), that a live runner does not exist, and that tests D to J stay blocked; the blocker table, the capture-design section and the next-step paragraph were updated to match, and `tests/test_handoff_docs.py` now pins the corrected statements (its command checks passed against the real `--help` output for the new commands). The GitHub readiness document was not touched.

### 11.9 Validation (software only)

59 runtime tests (every outcome in 11.4, the exact state sequence for each, no concurrent playback, busy / retry rejections, stop during synthesis and during playback, cancelling a capture, recovery after a bug / TTS failure / playback failure, determinism, no transcript / answer text / PCM in logs, outcomes or events, the demo, and the composition with the Stage B harness including a capture gap and a bug propagating through the harness). Mutation campaign on scratch copies: 29 runtime mutants, 28 killed, the one survivor an intentional canary (a pass/fail flag in the demo that no scripted run can expose). A first campaign left three real survivors (a refused input-side transition propagating, the discontinuity callback running before the restart, a reused request id) besides the canary; each got a test. Two existing call-site audit tests (`test_state_integration.py`) were updated on purpose: the runtime is now the one place that builds an `OutputArbiter`, and the demo the one place that can build the real voice. Full suite: **2561 passed, 6 skipped, 0 failed** (reference 2494 / 6).

### 11.10 Not claimed

No microphone, loudspeaker, wake / VAD / STT accuracy, latency, audibility or classroom result; no live runner; no real room state, schedule or professor-arrival source; no half-duplex protection; no change to the final hardware acceptance of AI-2 to AI-9. Frozen files untouched (audio/*, wakeword/*, speech/*, fusion/*, assistant/answering.py, assistant/spoken_output.py, assistant/output_arbiter.py, assistant/state.py, config/settings.py): byte-identical to the pre-task SHA-256 baseline. Changed or created: `assistant/runtime.py`, `scripts/vesta_runtime_demo.py`, `tests/runtime_support.py`, `tests/test_runtime.py`, plus additive hooks and bounded-wait tests in the Stage B harness and its tests, the two audit updates above, and the hand-off correction.
