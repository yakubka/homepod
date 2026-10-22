# Week 7 — VAD + STT (report; Tasks 1-4)

**Roadmap (CLAUDE.md):** Week 7 — VAD + STT. Goal: complete the spoken-input pipeline after wake-word activation (`ReSpeaker -> Wake Word -> VAD -> STT -> normalized text`). Week 8 owns intent understanding.

> **Read this first (updated 2026-10-12: Tasks 3 and 4 are now also COMPLETE — SOFTWARE-VALIDATED, see sections 14 and 15; Task 5 is NOT STARTED; the sentence below is the Task 2-era summary and is kept as history).** Two tasks were done at that time. **Task 1** (sections 1-11): an engine-free audio buffer, VAD contract and utterance segmenter, **SOFTWARE-VALIDATED ON SCRIPTED / SYNTHETIC VAD SCORES**. **Task 2** (section 12): one exact Silero VAD ONNX artifact with a recorded provenance, an inspected machine interface and a thin adapter behind the Task 1 `VadEngine` contract, **SOFTWARE-VALIDATED ADAPTER / MODEL CONTRACT**. **Nothing here measures VAD accuracy**: no real speech, no microphone, no ReSpeaker, no Raspberry Pi and no STT has been used. Tasks 3-5 are NOT STARTED.

## 1. Task 1 — scope

Delivered:
- `RollingPcmBuffer`: a bounded, sample-indexed buffer of the newest PCM, with owned snapshots.
- `VadEngine`: an engine-independent contract (one int16 frame in, one score in [0, 1] out).
- `SegmenterConfig` and `UtteranceSegmenter`: a deterministic wake-activated segmenter that replays the buffer, applies true hysteresis and produces a `SpeechSegment` or a reason there is none.

Not delivered (by design): any VAD engine (Silero is Task 2), STT (Task 3), text normalization and wake-prefix removal (Task 4), intent, TTS, any model or new dependency.

Files:

| File | Purpose |
|---|---|
| `audio/vad.py` | `VadEngine` contract, `check_score`, `VadContractError` family |
| `audio/utterance.py` | `RollingPcmBuffer`, `PcmSnapshot`, `SegmenterConfig`, `segmenter_config_from_settings`, `SegmentOutcome`, `SegmenterPhase`, `SpeechSegment`, `SegmenterResult`, `UtteranceSegmenter`, errors |
| `tests/voice_synthetic.py` | TEST / SYNTHETIC helpers: `ScriptedVadEngine`, deterministic PCM, scenario runners |
| `tests/test_vad_contract.py` | 41 contract tests |
| `tests/test_utterance_segmenter.py` | 249 buffer, config, segmentation, wake-handoff, reframing and recovery tests (237 at completion + 12 post-review hardening) |
| `config/settings.py` | new Week 7 block, every value `None` (NOT CALIBRATED) |

Reused unchanged: `audio/reframer.py` (frame splitting, never padded), `wakeword/detector.py` (`Detection`, used by tests only). Protected files were not modified (hash-verified, section 8).

## 2. Architecture (frozen)

```
AudioSource -> master absolute sample index
   |-- wake-word path (Week 2, unchanged)
   |-- RollingPcmBuffer (always on, bounded)
wake detected -> segmenter.activate(activation_sample, buffer.snapshot())
              -> live blocks -> segmenter.push(block, start_sample)
              -> SegmenterResult -> (Week 7 later tasks) STT -> text
```

The buffer runs continuously; the VAD runs only after a wake detection. The pipeline glue that owns the master loop is Task 4 and does not exist yet.

## 3. Rolling buffer

- int16 mono, capacity in samples, newest samples kept, absolute stream positions tracked.
- Blocks of any size; a block larger than the capacity keeps its newest part. Pushed data is never modified and is copied in.
- `snapshot(max_samples=None)` returns an owned, read-only copy and the absolute index where it starts. Asking for more history than exists returns what exists.
- The first block with a `start_sample` sets where the stream starts. Later blocks must continue exactly; a gap or overlap raises `AudioContinuityError` and changes nothing.
- No clock, no threads, no device code.

## 4. VAD contract

`VadEngine` exposes `sample_rate`, `frame_samples` (NEW stream samples only), `process_frame(frame) -> float` and `reset()`. `process_frame` validates the frame (int16, 1-D, exact length) and the score (finite, within [0, 1]). Engine-private details such as a rolling context or recurrent state belong to the engine's own adapter (Task 2), never to this contract. `tests/voice_synthetic.ScriptedVadEngine` is the only engine, and it is labelled TEST / SYNTHETIC.

## 5. Segmenter semantics

**Boundaries (true hysteresis, `start_threshold > end_threshold`):**
- A frame starts speech when `score >= start_threshold` (equality counts).
- Once speaking, a frame is speech while `score > end_threshold` and silence when `score <= end_threshold` (equality is silence). Scores between the thresholds keep an utterance alive but never start one.

**Durations:** given in seconds, converted once to whole samples, rounding **up**. A value within 1e-6 of a whole number of samples is taken as that number (0.07 s at 100 Hz is 7 samples, not 8). All decisions use integer samples. The conversion is pinned by tests.

**Region.** Onset needs `start_confirmation` consecutive frames at or above the start threshold; a lower frame resets the run (a short burst is dropped without an outcome). The segment starts at `onset - onset_padding`, clamped to the oldest audio held, and ends at the end of the LAST SPEECH frame (trailing silence excluded) after `end_silence` of consecutive silence.

**Activation anchor.** Replayed pre-roll may contain old conversation. A speech region whose last speech sample is at or before `activation_sample` is entirely before the wake detection: it is discarded and listening continues. A region still speaking when the stream passes the activation point, or starting after it, is kept. The rule is exact at the boundary (last speech sample == activation: discarded; one frame past: kept). Known limit: speech joined to the wake phrase by a pause shorter than `end_silence` is a single region and is kept whole, old words included; the segmenter cannot separate them without knowing the phrase.

**No pause required.** There is no wait-for-silence and no fixed discard window. `Detection.end_sample` is the end of a detector chunk, not the end of the spoken phrase, and is used only as the activation point.

**Outcomes:**

| Outcome | Meaning | Segment |
|---|---|---|
| `COMPLETE` | ended by end-silence | yes |
| `MAX_DURATION` | speech extent reached `max_utterance`; cut there, audio kept | yes |
| `STREAM_ENDED` | `finish()` while speaking | yes (partial) |
| `NO_SPEECH_TIMEOUT` | no speech confirmed within `no_speech_timeout` of the activation sample | no |
| `NO_SPEECH_STREAM_ENDED` | `finish()` with no qualifying speech | no |
| `TOO_SHORT` | confirmed speech shorter than `min_utterance` | no |

- Minimum is measured from the confirmed onset to the last speech frame; a duration equal to the minimum passes. A segment cut by `MAX_DURATION` is not minimum-checked. The extent that reaches the cap while still speaking is `MAX_DURATION`; trailing silence never triggers it.
- The no-speech timer starts at the activation sample, not at the start of the replayed pre-roll. Speech confirmed in the frame that ends exactly at the deadline counts as speech.
- `finish()` never pads a partial frame; samples that never filled a frame are reported as `unscored_samples`.

**Second wake.** `activate()` while capturing returns `False`, increments `ignored_reactivation_count`, and changes nothing else: no engine reset, no restart, no replacement of the activation point.

**Reset / recovery.** `activate()` resets the VAD engine exactly once. `reset()` clears the segmenter only (it does not touch the engine) and returns it to IDLE; every outcome can be followed by a clean next activation with no old PCM, scores, indices or counts. An exception from the engine marks the segmenter as needing `reset()`.

**Frames and indices.** Incoming blocks of any size are re-chunked by the existing `Reframer`; frame `i` covers `[origin + i*F, origin + (i+1)*F)`. The result of a run is independent of the input block size (tested with sizes from 1 to 1000).

**Ownership.** `SpeechSegment.samples` and `PcmSnapshot.samples` are owned, read-only copies. Frames given to an engine are read-only views. Input arrays are never modified.

**Frame granularity.** Configuration durations are converted to sample counts, but the VAD scores whole frames, so every decision is taken when a whole frame ends. An entered duration is therefore effectively rounded up to the next frame boundary: with 10-sample frames a start confirmation of 25 samples is decided after 30, an end silence of 35 after 40, a no-speech timeout of 205 fires when the frame ending at 210 completes, and a minimum of 45 is measured in whole frames (40 fails, 50 passes). This is frame-granularity behaviour, not a numerical error. The frame length belongs to the VAD engine (512 samples is 32 ms at 16 kHz). Pinned by `test_decisions_happen_at_whole_frame_boundaries`.

**MAX_DURATION reference point.** The cap is measured from the **padded segment start** (onset minus onset padding, after clamping), not from the confirmed onset. Onset padding therefore uses part of the maximum budget: the emitted PCM never exceeds `max_utterance`, and the speech-only extent after the onset can be smaller than `max_utterance` by the padding amount. A capped segment is not minimum-checked, so its speech-only extent can even be below `min_utterance` (padding 20, max = min = 50 gives 30 samples of speech and still `MAX_DURATION`). Pinned by `test_maximum_duration_is_measured_from_the_padded_start` and `test_max_duration_wins_when_padding_leaves_less_speech_than_the_minimum`.

**Partial final frame.** `finish()` never pads an incomplete final VAD frame. Those samples (fewer than one frame) are reported as `unscored_samples` **and are excluded from the returned `SpeechSegment`**, because no VAD decision exists for a partial frame; the segment ends at the last scored speech frame. When `push()` decides in the middle of a block, the rest of that block is unscored in the same way and the caller keeps it for the next cycle. Pinned by `test_eof_with_a_partial_final_frame_pins_every_field` (outcome, `decided_at_sample`, segment end, `unscored_samples`, tail exclusion, no padded frame) and `test_a_partial_final_frame_is_never_padded_into_a_confirmation`.

**Timer nuance.** The no-speech timer is evaluated only while LISTENING. While an old region's end-silence countdown is running (it can outlast the timeout when `end_silence > no_speech_timeout`) no timeout is decided; if that region is then discarded, the timeout is decided on its closing frame. Pinned by `test_timeout_is_decided_on_the_closing_frame_of_a_discarded_old_region`.

**While a capture is active**, the arguments of an ignored `activate()` call are not validated, because they never become state. Behaviour unchanged and harmless.

## 6. Configuration

`SegmenterConfig` is frozen and has no defaults. It validates `0 <= end_threshold < start_threshold <= 1` (equality rejected), positive durations (padding and pre-roll may be zero), and these relationships: `min_utterance >= start_confirmation`, `max_utterance >= min_utterance`, `no_speech_timeout >= start_confirmation`, `pre_roll < max_utterance`, `onset_padding < max_utterance`.

**No production value is invented.** `config/settings.py` holds nine keys, all `None` (NOT CALIBRATED): `VAD_START_THRESHOLD`, `VAD_END_THRESHOLD`, `VAD_START_CONFIRMATION_S`, `VAD_END_SILENCE_S`, `VAD_ONSET_PADDING_S`, `VOICE_PRE_ROLL_S`, `VOICE_NO_SPEECH_TIMEOUT_S`, `VOICE_MIN_UTTERANCE_S`, `VOICE_MAX_UTTERANCE_S`. `segmenter_config_from_settings()` raises `SegmenterConfigError` naming each unconfigured key (for example `VOICE_PRE_ROLL_S is not calibrated/configured`). Tests build explicit configs. Week 2's one-recording trigger timing is DEVELOPMENT EVIDENCE for the pre-roll, not a calibration.

## 7. Validation

| Check | Result |
|---|---|
| New Task 1 tests, at completion (2026-10-07) | **278 passed** (41 `test_vad_contract.py`, 237 `test_utterance_segmenter.py`) |
| Full suite at completion (`.venv`, `pytest -q -rs`) | **996 passed, 5 skipped, 0 failed** (baseline before Task 1: 718 passed, 5 skipped) |
| Skips at completion | 4 ReSpeaker hardware tests, 1 recorded-clip wake-word test (`VESTA_HEY_JARVIS_WAV`) |
| Adversarial review run (2026-10-09) | **993 passed, 8 skipped, 0 failed**; Task 1 targeted tests 278 passed. Environmental: Windows Application Control blocks an sklearn DLL, so the 4 real-engine openWakeWord tests skip (no Task 1 regression; same 1001 collected) |
| After hardening (2026-10-09) | Task 1 targeted **290 passed** (278 + 12); full suite **1005 passed, 8 skipped, 0 failed** (993 + 12; same environmental skips) |
| Mutation check | **28 / 28 mutants killed** (original list; re-run against the hardened tests: still 28 / 28). Systematic adversarial pass: see section 11 |

Mutants (each applied to `audio/utterance.py` by a harness that restores the file in a `finally` block and verifies the SHA-256 after every mutant and at the end; the file hash was identical after the run): buffer keeps the oldest samples; buffer capacity validation removed; buffer size bound removed; pre-roll omitted on activation; silence-hunt reintroduced; old pre-activation utterance returned; VAD reset omitted on activation; start-threshold boundary changed; end threshold ignored; no-speech timer starting at pre-roll start; max duration disabled; segmenter continuity check removed; buffer continuity check removed; copy removed (ownership); second wake resets the capture; hysteresis equality allowed in config; end-silence off by one; minimum-utterance boundary changed; timeout beating confirmation at the deadline; onset padding not clamped; replay not limited by pre-roll; ceil replaced by floor; float-noise tolerance removed; segment including trailing silence; engine frames writable; activation not validated inside the replay; EOF with speech returning no segment; TOO_SHORT passed on as a segment. No mutant survived, so no test was strengthened for a surviving mutant.

Classification: **SOFTWARE-VALIDATED ON SCRIPTED / SYNTHETIC VAD SCORES**. Not "VAD accuracy validated", not "Silero validated", not "speech recognition validated", not "hardware validated", not "live microphone validated". The tests use scripted score sequences and a deterministic counter pattern as audio; synthetic tones are not evidence about a neural VAD.

## 8. Protected integrity

SHA-256 compared before and after Task 1:
- Unchanged: `audio/base.py`, `audio/reframer.py`, `audio/file_source.py`, `audio/microphone.py`, `audio/__init__.py`, `wakeword/__init__.py`, `wakeword/detector.py`, `wakeword/openwakeword_backend.py`, `fusion/__init__.py`, `fusion/contracts.py`, `fusion/fuse.py`, `fusion/telemetry.py`, `requirements.txt`, `.gitignore`, `config/logging_config.py`.
- Changed on purpose: `config/settings.py` (the Week 7 block only).
- Frozen Week 4 result files (aggregate hash over `vision_assets/results`): identical. No Kaggle inference was run.

## 9. Limitations and pending items

- **LIVE MICROPHONE TIMING: NOT VALIDATED. LIVE VAD CONTINUITY: NOT VALIDATED.** `audio/microphone.py` drops the oldest block silently when its queue is full and gives no discontinuity marker. `WavFileSource` and the synthetic fixtures are gap-free. Before any live claim a separate, separately approved change is needed (for example a dropped-block counter or discontinuity event, receive timestamps, an explicit reset on discontinuity), followed by measurement of VAD recurrent-state behaviour across a gap. The segmenter already fails loudly on a non-contiguous block instead of joining audio. LIVE AUDIO CONTINUITY: PENDING HARDWARE/INTEGRATION VALIDATION.
- Wake pre-roll, thresholds, confirmation, end-silence, padding, timeout, minimum and maximum utterance are all **NOT CALIBRATED**; calibration needs real clips and wake-trigger timing.
- A pre-wake region joined to the wake phrase by a short pause is kept whole (section 5).
- The segmenter keeps all audio since the replay origin (bounded by pre-roll + no-speech timeout + max utterance + end silence); the per-block concatenation is O(n) and fine for a few seconds of audio.
- A segment ends at the last speech frame, with no trailing padding, and a partial final frame is never scored (its samples are reported as `unscored_samples` and excluded from the segment).
- The real VAD (Silero ONNX), its exact tensor contract, STT, text normalization, wake-prefix removal, the pipeline glue, privacy-safe telemetry, the development evaluation and any WER/CER are Tasks 2-5.
- WAKE_ONLY FOLLOW-UP UX: DEFERRED / REQUIRES EXPLICIT PRODUCT DECISION. The initial semantics for a wake-only transcript is an explicit empty-command result (Task 4), with no automatic second listening cycle.
- PENDING HARDWARE: ReSpeaker capture, channel and processing mode, far-field pickup, real classroom noise, Pi 5 behaviour and latency.
- PRIVACY HOUSEKEEPING REQUIRED: `your_recording.wav` (personal recording) is inside the repository folder. It was not touched or used.

- **WAKE DETECTOR -> MASTER SAMPLE CLOCK MAPPING: DEFERRED TO T4 INTEGRATION.** `Detection.end_sample` counts samples since the detector's last `reset()` and, when a finite file is replayed with `run_on_source()`, includes the zero padding of the final chunk (a 1,300-sample file whose detection fires on the padded tail reports `end_sample` 2,560). The segmenter needs the activation sample on the same clock as the buffer and correctly rejects one outside the replay (`SegmenterInputError`). T4 must establish the shared clock origin and reset rules (a detector reset after the stream started; a detector that starts later than the buffer), map or clamp padded EOF detections appropriately, and test it explicitly.
- **TOO_SHORT FOLLOW-UP / CONTINUE-LISTENING POLICY: DEFERRED TO T4 / PRODUCT STATE MACHINE.** A confirmed-but-short region (for example a cough after the wake word) ends the activated capture, so a command arriving later is not captured. This matches the Task 1 policy and is not redesigned here.
- **KNOWN SEGMENTATION LIMITATION (to be considered during T4 pipeline / real-data validation).** If unrelated old speech is separated from the wake utterance by less than `end_silence`, the segmenter treats them as one region. The extra speech stays in the segment and, because `MAX_DURATION` is measured from the padded segment start, consumes part of the maximum budget, so the end of the intended command can be truncated. Whether the wake phrase itself is in the segment also depends on when the detector fires relative to the phrase end (before it, then a pause: the phrase alone is returned; after it, same pause: the phrase region is discarded and the command alone is returned). Later stages must handle both.

## 10. Recorded decisions (architecture lock)

English only for Week 7 v1; `speech/` package approved; consented team-read clips from the external corpus preferred for development audio; Silero VAD preferred with its exact artifact contract PENDING MODEL INSPECTION (upstream-verified shapes only until the file is inspected); faster-whisper preferred conditionally on a dependency dry-run; abandoned-thread STT timeout rejected; post-wake silence-hunt rejected; max utterance and pre-roll are configuration, not guarantees.

## 11. Post-review reconciliation (2026-10-09)

**Review.** A Claude adversarial read-only review of Task 1 was run because the external Antigravity review is **DEFERRED DUE QUOTA** (quota exhausted until 2026-10-14). It was performed by the same author and is therefore **not independent**. Method: 28,800 randomized buffer checks against a reference model; 20,000 randomized segmenter scenarios compared with an oracle written region-first from the documented semantics (zero mismatches, zero block-schedule dependence, zero invariant violations); boundary probes for every item in the review brief; 36 additional systematic mutants run on a scratch copy of the code. Verdict at code level: no CRITICAL or HIGH finding, no Task 1 code defect.

**Dispositions.**

| # | Finding (severity) | Disposition |
|---|---|---|
| 1 | Wake detector clock vs master sample index, EOF zero padding (MEDIUM, T4 contract) | **DEFERRED TO T4 INTEGRATION**; documented (section 9); segmenter behaviour (reject) kept |
| 2 | Merged old speech can consume the cap and truncate the command (LOW) | Documented as a KNOWN SEGMENTATION LIMITATION; behaviour unchanged |
| 3 | Test-coverage gaps from the extra mutants (LOW) | **Closed** by 12 new tests (below) |
| 4 | Frame quantization not documented (LOW) | Documented (section 5, module docstring) and pinned by a test |
| 5 | MAX_DURATION reference point ambiguous (LOW) | Documented (padded segment start) and pinned by two tests |
| 6 | Partial final frame exclusion not stated (LOW) | Documented and pinned by two tests |
| 7 | Ignored `activate()` arguments not validated (LOW) | Documented; behaviour unchanged (arguments never become state) |
| 8 | Environment-dependent test counts (LOW) | History preserved (section 7, WEEK7_STATUS.md) |
| 9 | Tautological assertion and an over-named test (LOW/INFO) | Tightened; no public `faulted` property added (existing observable behaviour, `SegmenterStateError`, is sufficient) |
| 10 | TOO_SHORT is terminal (LOW/INFO) | **DEFERRED TO T4 / PRODUCT STATE MACHINE**; documented |

**Runtime change: NONE.** `audio/vad.py` is byte-identical, and `audio/utterance.py` is identical to its pre-hardening version once docstrings are stripped (AST comparison). Only the module documentation was clarified.

**Tests added (12):** `test_activation_at_the_replay_start_is_accepted_and_one_below_is_rejected` (activation == `replay.start_sample` accepted, one below rejected, engine untouched on rejection); `test_no_state_survives_an_anchor_discard_new_speech_on_the_next_frame` (x2: a 1- and 2-frame speech blip right after an anchor discard must not confirm, and a real new region owns its onset 240 / start 220 / PCM, not the stale onset 100); `test_gap_equal_to_end_silence_makes_two_regions_one_shorter_makes_one` (x2: gap 30 is bridged, gap 40 splits); `test_eof_with_a_partial_final_frame_pins_every_field`; `test_a_partial_final_frame_is_never_padded_into_a_confirmation`; `test_max_duration_wins_when_padding_leaves_less_speech_than_the_minimum`; `test_timeout_is_decided_on_the_closing_frame_of_a_discarded_old_region`; `test_decisions_happen_at_whole_frame_boundaries`; and two new float-tolerance pins in the duration-conversion test. Two weak tests were tightened (a tautological assertion removed; the Detection test renamed and strengthened).

**Mutation follow-up.** The original 28 mutants: still 28 / 28 killed. The 36 adversarial mutants: 28 killed before hardening, **34 killed after**. Previously surviving, now killed by the new tests: activation == `replay.start` (N2), EOF `decided_at` with a partial frame (N8), timeout check after a discard (N9), stale candidate after an anchor discard (N12), float-tolerance magnitude (N21), TOO_SHORT applied to a capped segment (N33). The 2 remaining survivors are not coverage gaps and no test was forced for them: **N29** (removing the non-finite score check) is REDUNDANT VALIDATION, since NaN and infinity already fail the [0, 1] range check; **N35** (a replay-slicing change) is EQUIVALENT by construction.

**Status.** Week 7: IN PROGRESS. Task 1: COMPLETE — SOFTWARE-VALIDATED ON SCRIPTED / SYNTHETIC VAD SCORES. Task 1 adversarial reconciliation: COMPLETE. External Antigravity review: DEFERRED DUE QUOTA. Task 2: NOT STARTED.

## 12. Task 2 — Silero ONNX artifact, contract inspection and adapter (2026-10-09)

**Classification: SOFTWARE-VALIDATED ADAPTER / MODEL CONTRACT.** This does **not** mean VAD accuracy validated. It means: we hold one exact reproducible artifact, we know its real machine interface, and the adapter talks to that interface correctly without leaking model-specific state into the rest of VESTA.

### 12.1 Artifact provenance

| Field | Value |
|---|---|
| Model | Silero VAD, ONNX, multi-rate `silero_vad.onnx` (used at 16 kHz only) |
| Upstream | https://github.com/snakers4/silero-vad |
| Release / tag | **v6.2.3** (latest release at acquisition; "Make torchaudio optional", published 2026-09-23) |
| Immutable commit | `5cd7945676eb32225748052e2e6a0580e4686a08` |
| Source path | `src/silero_vad/data/silero_vad.onnx` |
| Source URL | `https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx` |
| License | **MIT** (LICENSE file read at that commit, "Copyright (c) 2020-present Silero Team"; the repository license API reports MIT) |
| Local file | `models/silero_vad_v6.2.3.onnx` (git-ignored by the existing `*.onnx` rule; `.gitignore` unchanged) |
| Size | **2,327,524 bytes** (measured from the downloaded file) |
| SHA-256 | `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` |
| Git blob id | `80c5592ef1f4c9ede3e357bbd02eb863358a6a9d`, computed from the downloaded bytes and equal to GitHub's own id for that file at the commit (evidence independent of our SHA-256) |
| Acquisition | 2026-10-09, explicit one-time development step with `curl` from the commit-pinned raw URL (not a moving branch); the runtime never downloads |
| Inspection platform | Windows-11-10.0.26200, Python 3.13.14, onnxruntime 1.30.0, numpy 2.5.3 |
| Execution provider | `CPUExecutionProvider` (explicit; `AzureExecutionProvider` is also available and is not used) |

Notes: the same file content is published as the default `silero_vad.onnx` from v6.2 through v6.2.3 (identical git blob id); v6.0 and v5.1.2 carry different blobs. Other model files in the same directory (`*_16k_op15`, `*_half`, `*_sequence`, `*_op18_ifless`, `*_openvino_16k`) were not used.

**Manual re-acquisition (fresh clone; one-time development step; the application never does this).** The model file is git-ignored, so a fresh clone fails with `MODEL_NOT_FOUND` until a developer fetches the exact approved file from the immutable official commit and checks it. Git Bash or any shell with `curl`, from the project root:

```bash
curl -L -o models/silero_vad_v6.2.3.onnx \
  https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx
sha256sum models/silero_vad_v6.2.3.onnx
# must print: 1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3  (2,327,524 bytes)
```

PowerShell equivalent for the check: `(Get-FileHash models\silero_vad_v6.2.3.onnx -Algorithm SHA256).Hash.ToLower()`. Even without that manual check, `SileroOnnxVadEngine` refuses any file whose SHA-256 differs (`MODEL_HASH_MISMATCH`). These exact commands were executed during the reconciliation into a scratch directory and reproduced the approved size and hash, and an engine built from that copy reported `artifact_verified = True`.

### 12.2 Inspected interface (ONNX Runtime metadata of the local file)

| Tensor | Direction | Type | Shape (as reported) |
|---|---|---|---|
| `input` | in | `tensor(float)` | `[dynamic, dynamic]` |
| `state` | in | `tensor(float)` | `[2, dynamic, 128]` |
| `sr` | in | `tensor(int64)` | `[]` (scalar) |
| `output` | out | `tensor(float)` | `[dynamic, 1]` |
| `stateN` | out | `tensor(float)` | `[dynamic, dynamic, dynamic]` |

Model metadata: producer `spox`, graph `spox_graph`, no custom metadata, no domain or description. Runtime check with a zero frame (64 zero context + 512 zeros, zero state, `sr` = 16000 as int64): `output` is a float32 `(1, 1)` array, `stateN` is a new float32 `(2, 1, 128)` array. No `onnx` package was needed; `get_inputs`, `get_outputs` and `get_modelmeta` were enough.

**What the artifact itself does NOT enforce** (information probes, nothing adapter-related was assumed): the audio length axis is symbolic, so a 512-sample input is accepted as readily as 576; a batch of 2 and an `sr` of shape `(1,)` are accepted. So the graph is **permissive about some shape conventions, including the scalar `sr` calling convention**, which the adapter enforces itself (it always feeds a 0-d int64 array, and the tests pin that). What the graph does reject, re-probed independently in the fresh-session review: wrong element types (a float64 audio input, a float `sr`), wrong literal `state` dimensions (for example `(2, 1, 64)`), and unsupported `sr` values (12345; and 8000 together with a 16 kHz-length input). The 576-sample input is therefore an upstream *algorithm* convention that **the adapter guarantees by construction**, and the tests pin it.

### 12.3 Expected vs actual contract

| Item | Expected (prior research) | Actual (local artifact) | |
|---|---|---|---|
| Input names | `input`, `state`, `sr` | `input`, `state`, `sr` | MATCH |
| Model input at 16 kHz | 1 x 576 float32 (64 context + 512 new) | symbolic `[dynamic, dynamic]`; 576 is the upstream algorithm, accepted by the graph, not enforced by it | MATCH (upstream algorithm); NOT ENFORCED by the graph |
| `state` | 2 x 1 x 128 float32 | `[2, dynamic, 128]` float32; runtime `(2, 1, 128)` | MATCH |
| `sr` | 16000 | `tensor(int64)`, scalar | MATCH |
| Outputs | speech score, updated state | `output` float32 `(1, 1)`, `stateN` float32 `(2, 1, 128)` | MATCH (names `output`, `stateN` newly recorded) |
| Output shapes | score and state | `stateN` is fully symbolic in metadata, so it is validated on every call | MATCH |

No material mismatch, so the work continued. The upstream algorithm was read from `src/silero_vad/utils_vad.py` (`OnnxWrapper`) **at the pinned commit**: first-call context = zeros, state = zeros (2, 1, 128), model input = concatenate(context, new audio), `sr` = int64 scalar, outputs unpacked as (score, state), next context = last 64 samples of the model input just used. PCM scaling: `int16 / 32768.0` (`examples/onnx_sequence/run.py`) and `* 1/32768` (the pyaudio streaming notebook) at the same commit.

### 12.4 Adapter (`audio/silero_vad.py`, new)

- `SILERO_MODEL` is the **single authoritative record** of filename, revision, SHA-256, size and expected tensor contract; the tests import it, and a real-model test proves its tensor specs equal the inspected file's metadata.
- `SileroOnnxVadEngine(VadEngine)`: `frame_samples` is **512 new samples**, `sample_rate` 16000. It owns only model-specific behaviour: local load, SHA-256 verification, one CPU session (1 + 1 threads: a DEVELOPMENT EXECUTION POLICY copied from / aligned with upstream's reference wrapper, NOT Pi-calibrated), contract validation, int16 to float32 scaling, the rolling 64-sample context and the recurrent state. It owns **no** thresholds, hysteresis, durations, pre-roll or segmentation (those stay in `audio/utterance.py`), and `config/settings.py` is unchanged (no thresholds added, no model-path key: the default path is derived from the spec).
- **Loading:** the file's bytes are read once, hashed, and those **same verified bytes** create the session, so the file cannot change between the check and the load. A hash mismatch raises before onnxruntime is imported or any session exists. `onnxruntime` is imported lazily; importing the module imports no runtime and no downloader.
- **PCM:** float32 = int16 / 32768.0, exact (power of two). Pinned: -32768 gives -1.0, -1 gives -2^-15, 0 gives 0.0, 1 gives 2^-15, 32767 gives 0.999969482421875. No resampling, channel mixing, gain or second normalisation. The caller's int16 frame is never modified.
- **Context:** after a reset the first call feeds 64 zeros + the 512 new samples; each later call feeds the last 64 samples of the previous *model input* (which are the tail of the previous new frame). The context is adapter-internal: it never advances the Task 1 sample index, never changes a segment's duration and never enters a `SpeechSegment` (tested through the segmenter).
- **State:** zeros (2, 1, 128) after a reset; each call receives the state returned by the previous call; only the returned state is kept, as a private copy.
- **Reset:** restores zero context and zero state only. It does not reload, re-hash or recreate the session.
- **Score:** one Python `float`, finite and within [0, 1]. A bad score, a wrong output shape/dtype or a wrong state shape/dtype raises (`VadScoreError` or `SileroModelContractMismatch`) and **leaves the engine state exactly as before the call**. An inference exception becomes `SileroInferenceFailed`; nothing becomes a score of 0.
- **Errors (each carries a `code`):** `MODEL_NOT_FOUND`, `MODEL_HASH_MISMATCH`, `MODEL_LOAD_FAILED` (and its subclass `SileroRuntimeUnavailable`), `MODEL_CONTRACT_MISMATCH`, `INFERENCE_FAILED`.
- **Contract validation** (once, at load): names, element types, ranks and literal dimensions; symbolic dimensions are accepted where the artifact uses them (and re-checked per call); a literal audio length must equal 576; a literal batch must be 1.
- **Test hook:** `session=` injects an onnxruntime-like object (as in the YOLOX detector); its metadata is still validated, and the file/hash/onnxruntime steps are skipped. Production code leaves it `None`. Since the post-adversarial hardening an injected session is explicitly **unverified**: `engine.artifact_verified` is `False`, `engine.verified_sha256` is `None`, and the `silero_vad_ready` log line says `artifact_verified=false source=injected_session` and quotes no hash. `artifact_verified` is `True` only when the session was built from a file whose bytes matched the approved SHA-256. `engine.model_spec` is the specification the engine is *checked against*, not evidence of verification.

### 12.5 Tests

`tests/test_silero_vad_backend.py`: **99 tests at completion** (142 after the post-adversarial hardening, section 12.10), groups A-L: provenance/hash (hash mismatch rejected before any session; corrupted and wrong files; not found; exact verified bytes loaded), metadata contract (24 single-defect mismatches each failing at load with nothing run; symbolic dimensions accepted; fake metadata equals the real file's metadata), PCM scaling and ownership, context (zeros, chaining over several calls, reset, copy semantics), recurrent state (chaining, reset, aliasing), reset (identical to a fresh engine; no reload/re-hash/revalidation), score validation (state not advanced on failure), error handling (inference failure, missing runtime, OS-level import failure, unreadable file, non-ONNX bytes), real-model smoke, Task 1 integration (the segmenter runs on the adapter with a scripted session; activation resets the engine; the real adapter plugs in), and import/no-network behaviour (fresh-interpreter import checks and an AST check that the module imports nothing that can download).

**Real-model smoke tests establish only:** the model loads on the CPU provider, its contract matches, inference executes, scores are finite and within [0, 1], and reset is deterministic (bit-identical on this runtime; the test tolerance is 1e-6). They do **not** establish VAD accuracy, recall, precision, classroom robustness or far-field behaviour. The tests skip with a reason when the git-ignored model file is absent or onnxruntime cannot be imported.

### 12.6 Mutation checks

44 mutants run on a scratch copy of the repo (including a copy of the model; the canonical artifact and the canonical source were never mutated, and every file was restored and hash-verified after each mutant). The set is the 12 required mutants plus a systematic pass over every comparator, validation branch, constant and copy in the adapter (observation #11). **42 / 44 killed.** Four real gaps found by the first run were closed with three tests (explicit CPU provider and single threads with the verified bytes, an OS-level import failure, an unreadable file). The 2 survivors are **EQUIVALENT**: storing the context as a view (it points into the engine's own per-call temporary), and dividing by a Python float instead of `np.float32` (bit-identical over all 65,536 int16 values under NumPy 2.5.3). The canonical model SHA-256 was identical before and after.

### 12.7 Validation, regression and integrity

| Check | Result |
|---|---|
| Task 2 targeted | **99 passed** |
| Task 1 tests | 290 passed (unchanged) |
| Existing audio + wake-word suites | 98 passed, 8 skipped (environmental, below) |
| Full suite | **1104 passed, 8 skipped, 0 failed** (1005 + 99) |
| Protected files | 21 files unchanged, including `audio/vad.py`, `audio/utterance.py`, the Task 1 tests, `audio/base.py`, `audio/reframer.py`, `audio/file_source.py`, `audio/microphone.py`, `wakeword/*`, `fusion/*`, `requirements.txt`, `.gitignore`, `config/settings.py`, `config/logging_config.py` |
| Frozen Week 4 artifacts / YOLOX model | unchanged |
| Canonical Silero model | SHA-256 equals the approved value |

The 8 skips are the same environmental ones as before Task 2: 4 ReSpeaker hardware tests and 4 real-engine openWakeWord tests that skip because Windows Application Control blocks an sklearn DLL; that issue was not touched.

### 12.8 Limitations and pending items

- **VAD ACCURACY: NOT MEASURED. REAL SPEECH VAD: NOT EVALUATED.** `your_recording.wav` was not used; real consented speech belongs to Task 5.
- **THRESHOLDS: NOT CALIBRATED** (all nine Task 1 settings remain `None`); no default threshold was added.
- **PENDING HARDWARE:** ReSpeaker input, Raspberry Pi 5 (aarch64 onnxruntime, provider, latency, memory), far-field and classroom noise/distance behaviour, live microphone continuity (still NOT VALIDATED).
- No performance claim is made: nothing about real-time operation, Pi capability or speed was measured or implied.
- The 16 kHz path only; the artifact's 8 kHz capability is recorded as a fact and not exposed.
- The artifact is a development download that is git-ignored: a fresh clone must repeat the one-time acquisition (source URL and SHA-256 above); a different file is rejected until explicitly re-approved.
- Upstream facts (algorithm, scaling convention) come from the default branch's files **at the pinned commit**; they were read directly, not from summaries.

### 12.9 Status

Week 7: IN PROGRESS. Task 1: COMPLETE — SOFTWARE-VALIDATED ON SCRIPTED / SYNTHETIC VAD SCORES. Task 2: COMPLETE — SOFTWARE-VALIDATED ADAPTER / MODEL CONTRACT. Task 2 adversarial reconciliation: COMPLETE (section 12.10). Tasks 3-5: NOT STARTED. External Antigravity review: DEFERRED DUE QUOTA (until 2026-10-14); a fresh-session Claude read-only adversarial review of Task 2 is the next step and is **not** independent.

### 12.10 Post-adversarial reconciliation (2026-10-09)

**Review.** A fresh-session Claude adversarial read-only review of Task 2 returned **B. CLEAN WITH DOCUMENTATION / TEST-ONLY ISSUES**. It is **not** an independent Antigravity review (**EXTERNAL ANTIGRAVITY REVIEW DEFERRED DUE QUOTA**). It re-derived the provenance from the GitHub API, found the adapter **bit-identical** to a numpy transcription of the pinned upstream algorithm over five stream types, and found all 20 injected failure cases preserving context and state. It found no production algorithm defect.

| # | Finding | Disposition |
|---|---|---|
| 1 | The "file re-read after hashing" (TOCTOU) mutant survived (MEDIUM, test gap) | **Closed** by a test that serves different bytes on any second read and asserts exactly one read and that the session receives the very bytes object that was hashed |
| 2 | The public `session=` seam let an unverified session appear to carry the approved SHA (LOW) | **Fixed**: `artifact_verified` / `verified_sha256`; honest log line; names kept (smallest change), documented |
| 3 | Report said the graph rejects "the scalar rank of `sr`" (LOW, documentation) | **Fixed** (section 12.2) |
| 4 | Default-path, non-`RuntimeError`, reset-dtype, same-size-state, `spec=` gaps (LOW) | **Closed** by tests; the fake session now rejects wrong dtypes and state literals like the real graph, and raises a non-`RuntimeError` exception type |
| 5 | Validator accepted duplicate names and an unknown-rank `sr` (LOW, hypothetical) | **Fixed** in code (`MODEL_CONTRACT_MISMATCH`); unknown rank is UNKNOWN, never a scalar |
| 6 | A malformed `run()` result became `INFERENCE_FAILED` (LOW) | **Fixed**: None / wrong count / non-list result is `MODEL_CONTRACT_MISMATCH`; an exception stays `INFERENCE_FAILED`; state and context stay atomic |
| 7 | Thread comment "predictable on a small CPU" (LOW) | **Replaced**: DEVELOPMENT EXECUTION POLICY, aligned with upstream's reference wrapper, NOT PI-CALIBRATED |
| 8 | No literal re-acquisition command (LOW) | **Added** (section 12.1) |
| 9 | Protected-file hashes not recorded (INFO) | **Recorded** (section 12.11) |

**Runtime changes (contract precision only; recurrence, 512 new samples, 64 context, state handling, PCM scaling and the approved artifact are untouched):** duplicate-name detection and unknown-rank handling in the metadata validator; a structural check of the `run()` result before unpacking; the `artifact_verified` / `verified_sha256` properties and a log line that never quotes a hash for an unverified session; comment wording. Evidence that the algorithm is unchanged: the algorithm lines of `_score_frame` are textually identical before and after, and the differential test against the upstream transcription (five stream types, 60 frames each) is still bit-identical with max difference 0.

**Tests: 43 added (99 -> 142).** Exact-verified-bytes (one read, same bytes object); default path equals `REPO_ROOT / "models" / SILERO_MODEL.filename` without needing the model; the spec drives the default path and the contract validation; inference failures of six non-`RuntimeError` exception types (`INFERENCE_FAILED`, context and state unchanged, next call equals the control); after a reset the feed is still float32 / float32 / int64 with the runtime shapes; seven same-size wrong state shapes rejected; duplicate names (input, state, sr, output, stateN); unknown rank (`sr` as `None` and as a missing attribute, others); seven malformed `run()` results plus a tuple result accepted; verified vs injected provenance including the log content; the real engine is marked verified.

**Mutation follow-up (scratch copy; canonical model and repository files never mutated; every file restored and hash-verified).** 24 mutants: the 8 requested (A re-read after hashing, B wrong default directory, C `RuntimeError` only, D same-size wrong state shape, E duplicate name accepted, F unknown `sr` rank accepted as scalar, G wrong output count mapped to inference failure, H injected session claiming verified provenance) and 16 variants around them. **21 killed.** The 3 survivors are not coverage gaps and no test was forced: **M** (names compared as sets) is EQUIVALENT because duplicates are now checked separately; **N** (`np.asarray` removed) is REDUNDANT VALIDATION because real ONNX Runtime returns arrays; **O** (the order of two independent attribute assignments) is EQUIVALENT. One mutant (spec ignored when resolving the default path) was killed only when the real model was present, which exposed a weak assertion in my own new test; it was tightened to check the resolved path, after which **no mutant depends on the real model file**.

**Results.** Task 2 tests 142 passed; Task 1 tests 290 passed; existing audio and wake-word suites 98 passed, 8 skipped; **full suite 1147 passed, 8 skipped, 0 failed** (1104 + 43; the same 4 ReSpeaker and 4 Application-Control openWakeWord skips). The 20-fault atomicity probe on the real model was re-run on the hardened code: every fault leaves context and state untouched.

### 12.11 Task 2 freeze point (SHA-256, 2026-10-09, after the hardening)

| File | SHA-256 |
|---|---|
| `models/silero_vad_v6.2.3.onnx` (approved artifact) | `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` |
| `audio/silero_vad.py` | `f85adea90834e1324f2da59275c4cb97acf4fd8a273efda0c27f5f917cab197f` |
| `tests/test_silero_vad_backend.py` | `60dd915c31ddeb7998d29b26594ec90842b6442a5cd671097c06146067b73478` |
| `audio/vad.py` | `aeb584f06f250366c239d82e7b88317b9eedeac4a2de7ce64a242c85af05598c` |
| `audio/utterance.py` | `884c04597a3a1541d35044e4427943f8f39352ccd64d57a81e8c53f29375bb50` |
| `audio/base.py` | `4be1d035b872d4d39d90e9260093442023929d6892c677b238a0105777631136` |
| `audio/reframer.py` | `bebe38407be56277f248ca54d8c9a8cb2c185f96a2e66206f5cee85a56db9a97` |
| `audio/file_source.py` | `fdc76d0bad09619d953140aa7a6e1e0cefbf006457a9da14613a645b0ebc3500` |
| `audio/microphone.py` | `18fe3d620070806afb8481489b47be14567f8a4e1882413430fc7cf573729f2b` |
| `wakeword/detector.py` | `f2dab5b6af6c68d73fa4e9005699febbdf87c325cd566213e4a9cd727b50488e` |
| `wakeword/openwakeword_backend.py` | `89da99a1271f4748dea365b97ed44764cce8479735fe156bf488ddacfa24ad5a` |
| `fusion/contracts.py` | `83436a1df18a00fd82b6aa26f18ee2b9b5e43150c7c23532ba928f84bf54806b` |
| `fusion/fuse.py` | `7a332064a7bba4dcf9b8b417a8458afac365462df56c99886401b5decef0837f` |
| `fusion/telemetry.py` | `a61f22ce3ac1c49813ccc96b8bd7abedb61d85e837dafa6d68c3f16aa0020e52` |
| `requirements.txt` | `40422ff668444725eca1b8d1a3639e8c0cd43946eb10c1403af49965e83e1acb` |
| `config/settings.py` | `e4c0484a64cb4bef250683eb02cb44b2ebe14e561f4908aee111dfc93a1a876d` |
| `.gitignore` | `6a68ec7ab7a1577e7c4d2a1476c1a6752efbdd6c8f7bcbe9857914c82107e7b3` |
| `tests/test_vad_contract.py` | `2f9eb13c9b6b72316c74dca7391f579a1e252206151947fe50a78cfaf098a0f9` |
| `tests/test_utterance_segmenter.py` | `9b0bfa8629246644f91f04b08256079f5e1132d73995a9712b9b86b85fad46e8` |
| `tests/voice_synthetic.py` | `7ddc38e5162cc2a6a61debfae81725375dd92dd5d69e9c2bff92d6acd26b265a` |

Before the hardening, `audio/silero_vad.py` was `f696457978aa9850…` and its test file `4f4ff8bfac7212f7…` (first 16 hex digits); every other file in the table matched its pre-hardening hash, which is the evidence that only those two files changed. `WEEK7_REPORT.md`, `WEEK7_STATUS.md` and `CLAUDE.md` are documents and are not part of the freeze.

### 12.12 Status after the reconciliation

Week 7: IN PROGRESS. Task 1: COMPLETE. Task 2: COMPLETE — SOFTWARE-VALIDATED ADAPTER / MODEL CONTRACT. Task 2 adversarial reconciliation: COMPLETE. VAD accuracy: NOT MEASURED. Real speech: NOT EVALUATED. Thresholds: NOT CALIBRATED. Pi / ReSpeaker: PENDING HARDWARE. Task 3: NOT STARTED. External Antigravity review: DEFERRED DUE QUOTA. *(Historical status at the time of the Task 2 reconciliation. Superseded on 2026-10-12: Task 3 reconciled COMPLETE — SOFTWARE-VALIDATED in section 14, Task 4 COMPLETE — SOFTWARE-VALIDATED PIPELINE in section 15.)*

## 13. Separate milestone note (not a Week 7 deliverable)

The Team Leader Oct 12-23 AI-1 evaluation set (a 10-question test set, `evaluation/ai_question_test_set_v1.json`) has been created for the separate Team Leader AI Engineer milestone. It is not one of the Week 7 roadmap tasks and does not change the Week 7 status in section 12.12 (Tasks 1-2 COMPLETE, Tasks 3-5 NOT STARTED). Details: [TEAM_LEADER_AI_REPORT.md](TEAM_LEADER_AI_REPORT.md).

## 14. Task 3 reconciliation — STT dependency gate + STT contract + faster-whisper backend (2026-10-12)

**Result: Task 3 is COMPLETE — SOFTWARE-VALIDATED.** The implementation was produced while satisfying a separate Team Leader dependency, but the technical artifact also satisfies the original Week 7 Task 3 specification. The two are separate project-tracking layers: Team Leader AI-2 ("live speech is recognised") is **not** Week 7 Task 3, and it remains NOT COMPLETE. Nothing was rewritten: the STT files are byte-identical to what the Team Leader work left (hashes in section 15.9).

Why the status file said NOT STARTED: it was last updated on 2026-10-09, before the STT work existed; the Team Leader sprint deliberately left the Week 7 status files alone. This section is the audit that closes that gap.

### 14.1 Audit against the Task 3 requirements

| Requirement | Evidence | Verdict |
|---|---|---|
| Engine-neutral STT contract | `speech/stt.py`: abstract `SttEngine` (sample_rate, engine_id, model_id, `_transcribe`), immutable `SttResult`; `tests/test_stt_contract.py` (63 tests, scripted fake engine) | MET |
| faster-whisper backend | `speech/faster_whisper_backend.py`: `FasterWhisperEngine`, `faster-whisper==1.2.1` pinned in `requirements.txt` | MET |
| Offline runtime | `local_files_only=True`; a test blocks socket access during real construction and inference; a test scans the module for network / download imports | MET |
| Pinned model | `Systran/faster-whisper-tiny.en` at commit `0d3d19a32d3338f10357c0889762bd8d64bbdeba`, MIT; manifest of 4 files (78,090,594 bytes) with size and SHA-256 (`FASTER_WHISPER_TINY_EN`) | MET |
| Local-only loading | the engine receives a directory path, never a model name; the runtime never downloads; acquisition is one explicit documented step (TEAM_LEADER_AI_REPORT.md section 3.3) | MET |
| Model verification | `verify_model_snapshot()` checks existence, size and SHA-256 of every file BEFORE the model is built; a test pins the order; `snapshot_verified` is True only for a verified directory, an injected model is `UNVERIFIED-INJECTED-MODEL` | MET |
| English v1 policy | `language="en"`; `_check_model_contract` refuses a multilingual or unknown model | MET |
| Typed errors | `SttRuntimeUnavailable`, `SttModelNotFound`, `SttModelLoadFailed` (+ `SttModelHashMismatch`), `SttInputError`, `SttInferenceFailed`, `SttContractError`; none becomes an empty transcript | MET |
| Privacy-safe logging | only structural fields (status, ids, durations, `text_chars`); tests assert the transcript never reaches a log | MET |
| Immutable result | frozen dataclass with invariants (normalised text, status/text agreement, finite durations) | MET |
| int16 16 kHz speech input | `_validate`: 1-D, non-empty, int16, integer sample rate equal to the engine's | MET |
| No silent resampling / down-mixing | wrong dtype, shape or rate raises `SttInputError`; the caller's array is never modified (the backend gets a read-only view) | MET |
| Lazy backend import | importing the package loads no engine or model (subprocess test); `faster_whisper` is imported only when an engine is built from files | MET |
| Real-model smoke test | 5 Tier-2 tests on the approved snapshot (verifies, loads, executes, repeats, enforces its length limit, runs with sockets blocked); they RAN (not skipped) in this audit | MET |
| Software-only validation | module docstrings and the report state SOFTWARE-VALIDATED ADAPTER, STT ACCURACY NOT MEASURED, HALLUCINATION MITIGATION NOT YET VALIDATED | MET |
| No unsupported Pi / hardware claims | "Pi 5 / aarch64 behaviour is PENDING TARGET-HARDWARE VERIFICATION"; CPU / int8 / 30 s are labelled DEVELOPMENT POLICY, NOT CALIBRATED | MET |
| Dependency gate | TEAM_LEADER_AI_REPORT.md section 3.2 (installed versions, `pip check` clean, `pip freeze` differs only by additions) and 3.5 (import, load and inference timings; the unexplained one-off 54 s first `import av` is recorded) | MET |

Audit run today (2026-10-12): the 119 STT tests pass (63 + 56, including the 5 real-model tests); 63-mutant campaign recorded by the Team Leader work (60 killed, 3 equivalent). No unresolved technical gap was found, so nothing was patched.

### 14.2 Notes that matter to later tasks (not defects)

- `max_audio_s = 30 s` is a backend-only DEVELOPMENT POLICY. If the segmenter is ever configured with `max_utterance_s` above 30 s, the STT backend refuses the segment (`SttInputError`) and the pipeline reports STT_FAILED. Keep `max_utterance_s <= 30` until the limit is revisited.
- Whisper can emit text for a non-speech signal (recorded anecdotally in two places: TEAM_LEADER_AI_REPORT.md section 3.5 and section 15.7 below). HALLUCINATION MITIGATION NOT YET VALIDATED; Task 5 must look at real behaviour.
- Task 3 has had no separate adversarial review and no independent external review (EXTERNAL INDEPENDENT REVIEW: DEFERRED).

## 15. Task 4 — wake + VAD + STT pipeline, wake-prefix normalisation, privacy-safe telemetry (2026-10-12)

**Result: Task 4 is COMPLETE — SOFTWARE-VALIDATED PIPELINE.** It ends at normalised text. It does not call intent, answering, TTS, playback or the output arbiter.

### 15.1 What was added (3 new code / test files; see section 16.1 for the exact change accounting, which corrects the original summary of this task)

| File | Role |
|---|---|
| `speech/pipeline.py` | `VoicePipeline`, `PipelineResult`, `PipelineStatus`, `PipelineTimings`, `strip_wake_prefix()`; imports only the frozen audio / wake / STT layers |
| `tests/test_voice_pipeline.py` | 124 scripted tests |
| `tests/test_voice_pipeline_real.py` | 3 real-component plumbing tests (skip when a model is absent) |

No frozen component was modified. No dependency was added; `requirements.txt` and `.gitignore` are unchanged by Task 4.

### 15.2 Architecture

```
AudioSource block -> master PCM timeline (real samples only)
   |-> RollingPcmBuffer (always on; capacity = pre_roll + one detector chunk)
   |-> Reframer -> WakeWordDetector -> Detection (detector clock)
wake -> master activation sample -> segmenter.activate(activation, buffer snapshot) -> live PCM
     -> SegmenterResult -> STT once -> wake-prefix strip -> PipelineResult (no PCM)
```

The pipeline owns the detector and the segmenter (it resets both). Blocks of any size are accepted; they are handled in slices of at most one detector chunk so the detection chunk always ends inside the slice just given to the buffer. It feeds the detector for the whole stream, including during a capture, so the detector's clock never drifts from the master clock; a wake during a capture is passed to the segmenter, which ignores and counts it (the VAD engine is reset exactly once per activation).

### 15.3 The master sample clock

- **Origin:** master sample 0 is the first sample of the first block fed after the pipeline is constructed or `restart_stream()` is called. Master indices count real delivered samples only.
- **Reset policy:** the master clock never moves backwards and never resets mid-stream. `restart_stream()` starts a new stream (clock 0, detector, buffer and segmenter reset); `run_source()` calls it. `finish()` closes the stream.
- **Rolling buffer:** its indices ARE master indices (every push passes the master start).
- **Segmenter:** `activation_sample`, the replay and every `SpeechSegment` index are master indices.
- **Wake detector:** its clock counts samples since its own last reset. The pipeline resets it at construction, `restart_stream()` and `reset_wake_detector()`, records the master index of that reset (`detector_origin`), and maps `master = detector_origin + Detection.end_sample`. `Detection.end_sample` is never passed through unmapped. After every chunk the pipeline checks that the detector's own count equals what it fed it; any other touch raises `PipelineClockError`.
- **Gaps:** an optional `start_sample` on `feed()` must equal the master end (`AudioContinuityError` otherwise). LIVE CONTINUITY remains NOT VALIDATED: the microphone backend drops blocks silently, and the pipeline cannot see a drop unless the caller supplies the index.

### 15.4 Padded end-of-file detections

`run_on_source`-style replay zero-pads the detector's last partial chunk. In `finish()` the pipeline pads only the detector's copy. If a detection fires on that chunk, its `end_sample` includes the padding; the mapped activation is **clamped to the last real delivered sample** and `activation_clamped=True` is recorded. The padding is never given to the buffer or the segmenter, and the master clock does not count it. Anywhere else, a mapped activation outside the buffered PCM is **INVALID_ACTIVATION** (a broken detector or clock), never clamped. Tested at the exact boundaries (tail of 1, 2, 20, 38 and 39 real samples; activation exactly at the buffer start and end, and one sample beyond either).

Consequence worth knowing: the frozen segmenter treats speech that ends at or before the activation as old, so a wake that fires only on the padded tail of a file (nothing can follow the last real sample) reports NO_SPEECH. This is the frozen policy, not a pipeline decision.

### 15.5 Pre-roll, continuous speech, old speech

The existing `RollingPcmBuffer` is reused. On wake the segmenter is activated at the mapped sample with the bounded pre-roll replay, then live PCM continues. There is no silence hunt and no fixed discard, so "Hey Jarvis turn on the lights" with no pause is one region (test: speech starts 60 samples before the wake). A speech region ending at or before the activation is discarded as old; one ending a frame later is kept (exact-boundary tests at 470 / 480 / 490 / 500). Speech at the very edge of the pre-roll window is clipped to exactly the pre-roll for any caller block size.

### 15.6 Outcomes and statuses

| Segmenter outcome | Pipeline status | STT called |
|---|---|---|
| COMPLETE, text after the wake prefix | TRANSCRIBED | once |
| COMPLETE / MAX_DURATION / STREAM_ENDED, transcript only the wake phrase | WAKE_ONLY (no command is invented) | once |
| MAX_DURATION with text | MAX_DURATION (text may be TRUNCATED) | once |
| STREAM_ENDED with text | STREAM_ENDED (text may be incomplete) | once |
| segment but empty decode | STT_EMPTY | once |
| segment but typed STT error | STT_FAILED (`error_class` = class name only) | once |
| NO_SPEECH_TIMEOUT / NO_SPEECH_STREAM_ENDED | NO_SPEECH | never |
| TOO_SHORT | TOO_SHORT | never |
| mapped activation outside the delivered PCM | INVALID_ACTIVATION | never |

Only TRANSCRIBED is a complete command. A non-`SttError` exception from the STT layer is a bug and propagates (the capture is abandoned and the pipeline returns to idle).

**TOO_SHORT policy (v1):** the frozen segmenter ends the activation; the pipeline returns TOO_SHORT and goes idle rather than silently re-listening. UX limitation: a cough after the wake word means the user must say the wake word again. The follow-up UX (including after WAKE_ONLY) stays a product / state-machine decision.

### 15.7 Wake-prefix normalisation

`strip_wake_prefix()` removes one configured phrase (default "hey jarvis") from the LEADING edge only: case-insensitive, whole words in order, any run of whitespace or punctuation between and around the words, followed by the end of the text or a separator. It does not remove an internal occurrence ("tell jarvis to ...", "what did hey jarvis say"), does not match "heyjarvis", "hey jarvisian" or "hey jarvis's", removes only the first phrase ("hey jarvis hey jarvis lights" -> "hey jarvis lights"), never lower-cases or rewrites the rest, and uses no model. 25 table cases plus configuration tests.

### 15.8 Result contract, telemetry, privacy

`PipelineResult` (frozen, no PCM, `normalized_text` hidden from `repr`): status, raw_stt_status, normalized_text, segment_outcome, activation_sample (master), detector_end_sample (detector clock, for audit), activation_clamped, segment_start/end_sample, audio_duration_s, stt_engine_id, model_id, wake_detected, wake_keyword, wake_prefix_removed, decided_at_sample, unscored_samples, ignored_reactivation_count, error_class, timings (deterministic decision latency, STT inference ms, post-decision wall ms) and `validation_label`. The label is always `SOFTWARE-VALIDATED`; a result cannot be constructed with any other value, so no code path can upgrade it. Logs (`pipeline_wake`, `pipeline_result`) carry sample indices, durations, statuses, ids, timings, a prefix_removed flag and error class names; tests verify the transcript, an exception message and PCM sample values never appear in them.

### 15.9 Validation

- **Scripted (124 tests):** real `WakeWordDetector` subclass with scripted scores, an energy-based fake VAD tied to the audio, a fake STT. Covers wake + pre-roll, continuous wake + command, old speech, the activation boundary, detector created at 0 and reset at non-zero indices (including pending samples dropped), several detector sessions, any caller block size (1 to 1000), padded and unpadded EOF, every status, the STT call count, prefix cases, gaps / overlaps, wiring errors, recovery after a VAD failure, `run_source`, the result contract and privacy.
- **Real-component plumbing (3 tests, WavFileSource + real Silero + scripted wake + fake or real STT):** a generated 16 kHz signal (formant-filtered 120 Hz harmonic train with a syllable-rate envelope), NOT speech. They prove the wiring and index alignment (the transcribed segment equals the WAV slice exactly) and that real Silero reports NO_SPEECH on silence. The all-real run (real faster-whisper) asserts structure only. One ad-hoc run of that chain produced status TRANSCRIBED with a few hundred characters of text for the non-speech signal: the decoder emits text for non-speech, so HALLUCINATION MITIGATION NOT YET VALIDATED; the text was not recorded and is not asserted. The wake is SIMULATED (the real openWakeWord engine cannot be imported on this machine and could not detect a synthetic signal anyway).
- **Mutation (60 mutants, scratch copies, restore + hash verified after each):** origin offset removed, detector reset offset ignored, reframer pending kept, padded clamp removed or misplaced, range bounds off by one, activation off by one, prefix searched anywhere / substring / case-sensitive / apostrophe / running-together / rewritten, WAKE_ONLY as STT_EMPTY or as a command, STT twice, STT on no segment, transcript or PCM leaked into logs or the result, exception message stored, gaps accepted, label upgraded or its check removed, capacity and slice changes, double or missing live push, segmenter not reset, clock check removed, status downgrades, restart not resetting state, and more. **59 killed.** One survivor, "STT_EMPTY text kept", is EQUIVALENT: an EMPTY_TEXT result already carries empty text by the `SttResult` invariant. A first pass left one real survivor (the interaction counter was never checked above zero); a test now covers it. The campaign gives identical results with and without the git-ignored model files (no kill depends on a gated artifact).
- **Regression:** Task 1 tests 290 passed; Task 2 tests 142 passed; Task 3 tests 119 passed; Task 4 tests 127 passed; wake-word 37 passed / 4 skipped; audio group 143 passed / 5 skipped; **full suite 2047 passed, 9 skipped, 0 failed** (baseline before Task 4: 1920 passed, 9 skipped; the quoted 1923 / 6 differed only by 3 environmental openWakeWord skips).
- **Integrity:** the other 18 files in the section 12.11 table (approved Silero model, audio, wake-word, fusion and config files, and the Task 1-2 tests) match their freeze hashes (re-verified mechanically today); `requirements.txt` and `.gitignore` differ from the section 12.11 values only because the Team Leader STT / TTS work added dependencies and ignore rules before this session, and Task 4 did not change them. Hashes at the Task 4 freeze point:

| File | SHA-256 |
|---|---|
| `speech/pipeline.py` | `072ea329826a06a923ed907f0c175f5c5f84195f33d54f5b2da36a383c42ff1a` |
| `tests/test_voice_pipeline.py` | `824ad6860cfb0557fc285e9a891000855ee655c50ce425be32056ce3460e01e4` |
| `tests/test_voice_pipeline_real.py` | `4b9b0c90402def9bc414d43159303c4c66ae58c018c9d412e443307dd225f04b` |
| `speech/stt.py` (unchanged) | `bb538123c734c9ca0f4d7d2b24e5bb4328aaa4f9d83713d25d124c72163fca7d` |
| `speech/faster_whisper_backend.py` (unchanged) | `c73cd43635b7d1b6e394e54de01b7aaa22ddc4a6585bc486da532174a68a4b31` |
| `tests/test_stt_contract.py` (unchanged) | `32c59ae9bb899b5a6267ecbf1b0ee064bc4d13306f937abc96e4e3eb4d9ae5e1` |
| `tests/test_faster_whisper_backend.py` (unchanged) | `8094b60be918880149328be608af643c1f339b5615155724064cc26062e09bed` |
| `requirements.txt` (unchanged by Task 4) | `602b10d3bf6c7fc398d10d743394c23295be541c1f69961ab4a5b54726f825d0` |
| `.gitignore` (unchanged by Task 4) | `2d11aab3c4c26f9240383731160629f7b3f62e58122eb7ec41f2894d750b7b0b` |

### 15.10 Limitations and what is not claimed

- Wake, VAD and recognition accuracy are NOT MEASURED. No real speech, no WER, no hallucination rate, no latency on target hardware.
- LIVE MICROPHONE TIMING and LIVE VAD CONTINUITY are NOT VALIDATED. ReSpeaker and Raspberry Pi 5 are PENDING HARDWARE. Nothing here is HARDWARE-VALIDATED.
- Pre-roll, thresholds, confirmation, end-silence, padding, timeout, minimum and maximum utterance are still NOT CALIBRATED (`config/settings.py` holds `None`; the tests use explicit test configurations).
- The known segmentation limitation (old speech separated from the wake utterance by less than the end silence is kept in the segment) is unchanged.
- The follow-up UX after TOO_SHORT and WAKE_ONLY is an open product decision.
- `your_recording.wav` was not used, copied or moved. PRIVACY HOUSEKEEPING still open for the user.
- EXTERNAL INDEPENDENT REVIEW of Tasks 1-4: DEFERRED (Antigravity quota until 2026-10-14). Tasks 3 and 4 have had no adversarial review yet.

### 15.11 Status

Week 7: IN PROGRESS. Task 1: COMPLETE. Task 2: COMPLETE. Task 3: COMPLETE — SOFTWARE-VALIDATED. Task 4: COMPLETE — SOFTWARE-VALIDATED PIPELINE. Task 5 (recorded development evaluation + performance + closeout): NOT STARTED.

## 16. Task 4 final reconciliation (2026-10-12)

### 16.1 Exact change accounting for the Task 4 session

The first Task 4 report said "only the three new files and the docs changed" while also listing CLAUDE.md and observer files as modified. That sentence was wrong. The exact accounting:

| File | Created / Modified | Authorized reason | Disposition now | Frozen? |
|---|---|---|---|---|
| `speech/pipeline.py` | Created | Task 4 deliverable | kept | frozen production code |
| `tests/test_voice_pipeline.py` | Created | Task 4 tests | kept | frozen test |
| `tests/test_voice_pipeline_real.py` | Created | Task 4 real-component plumbing tests | kept | frozen test |
| `WEEK7_STATUS.md`, `WEEK7_REPORT.md` | Modified | requested tracking updates | kept (history appended) | documents, not frozen |
| `CLAUDE.md` | **Modified (3 status lines)** | **NOT requested** (only the two Week 7 files were) | **REVERTED 2026-10-12 to the pre-Task-4 bytes** (SHA-256 `4f73f1c5ab5ec60e016cc3d265cf594d785ea378b4d4abb698fc6ca043305b22`, CRLF preserved) | instruction file, unchanged |
| `skill-observations/observation-log/0018-...md`, `.../archive/.id-floor`, `skill-observations/checkpoints.log` | Created / modified | required by the project-local task-observer workflow | kept | observer-generated working files, NOT production files and not frozen |

Why CLAUDE.md was reverted: the three edits only duplicated roadmap status that WEEK7_STATUS.md and WEEK7_REPORT.md already track, and no CLAUDE.md rule requires a status line to be updated per task. The Week 7 status lines in CLAUDE.md therefore still describe the Task 2 state ("Tasks 3-5 NOT STARTED"); WEEK7_STATUS.md is authoritative until the user decides otherwise.

### 16.2 Re-verification before freezing

After the revert: Task 4 tests 127 passed (124 scripted + 3 real-component), STT tests 119 passed, Task 1 tests 290 passed, Task 2 tests 142 passed, wake-word 37 passed / 4 skipped, audio group 143 passed / 5 skipped. Task 4 is frozen at the hashes in section 15.9 (re-checked). Expected Week 7 state unchanged: Tasks 1-4 COMPLETE (3 and 4 SOFTWARE-VALIDATED), Task 5 NOT COMPLETE, Week 7 IN PROGRESS.

## 17. Task 5 — recorded development evaluation HARNESS (2026-10-12)

**Status: TASK 5 EVALUATION HARNESS: READY / SOFTWARE-VALIDATED. TASK 5 EXECUTION: PENDING REAL RECORDED DEVELOPMENT CORPUS. TASK 5: NOT COMPLETE. WEEK 7: IN PROGRESS.** No recording exists yet, none was fabricated, `your_recording.wav` was not used, and no WER, VAD accuracy, hallucination rate, threshold calibration or latency figure is claimed.

### 17.1 Architecture

```
recording (16 kHz mono 16-bit WAV, OUTSIDE the repo)  + manifest entry (provenance, reference text, split)
   -> validated ingest (consent, location, format, optional SHA-256)
   -> speech.pipeline.VoicePipeline (real or injected VAD / STT; wake simulated or real)
   -> PipelineResult
   -> metrics (evaluation/week7_voice_metrics.py)
   -> per-item evidence (JSON lines)  -> aggregate report (JSON)
```

| File | Role |
|---|---|
| `evaluation/week7_voice_metrics.py` | pure arithmetic: normalisation, WER / CER with S / D / I counts, micro aggregation, wake-prefix classification, segmentation geometry, distributions |
| `evaluation/week7_voice_eval.py` | manifest contract and validation, runner, per-item schema, aggregation, report, evidence output, CLI |
| `evaluation/week7_calibration.py` | DEV-only sweep support (machinery only, never run) |
| `evaluation/week7_corpus_manifest.template.json` | versioned template: TEMPLATE, NO REAL RECORDINGS INCLUDED, NO WEEK 7 ACCURACY RESULT YET |
| `tests/week7_eval_support.py`, `tests/test_week7_voice_metrics.py`, `tests/test_week7_voice_eval.py`, `tests/test_week7_calibration.py` | fakes and 144 tests |

The evaluator only drives the frozen production code; no production module imports it, and it never imports `config.settings` (a test checks the imports). It wraps the VAD and STT with thin timing / capturing proxies on the evaluator side (production classes unchanged) to get VAD time and the RAW decoded text before the pipeline's prefix rule.

### 17.2 Corpus manifest contract (JSON, schema `week7-voice-eval-manifest-v1`)

Required per recording: `recording_id`, `audio_path`, `speaker_code`, `consent_reference`, `recording_origin`, `microphone_or_device`, `distance_condition`, `noise_condition`, `category`, `expected_wake_phrase`, `reference_command_text`, `split`, `notes`. Optional: `wake_end_sample`, `speech_start_sample` / `speech_end_sample` (annotated command region, 16 kHz sample indices, end exclusive), `human_review`, `audio_sha256`, `conversion`. Unknown fields, duplicate ids and any invalid entry fail the whole manifest with every problem listed.

Categories: `CLEAN_COMMAND`, `WAKE_IMMEDIATE_COMMAND`, `WAKE_PAUSE_COMMAND`, `SILENCE_CONTROL`, `NOISE_CONTROL`, `SPEECH_NO_WAKE`, `SHORT_NON_COMMAND`. The three controls (silence, noise, short non-speech such as a cough) must have an empty reference. `reference_command_text` is the command only, without the wake phrase. Splits: `DEV`, `HELDOUT` (no TRAIN). Origins: `HARDWARE_ENGINEER_RECORDING`, `TEAM_MEMBER_RECORDING`, `PROJECT_MICROPHONE_RECORDING`, `SYNTHETIC`, `UNKNOWN`, so recordings from the Hardware Engineer, team members or the project microphone all fit and the evaluator is not coupled to one device.

### 17.3 Consent, privacy, locations

- Every origin except SYNTHETIC (including UNKNOWN) needs an explicit `consent_reference`; `UNKNOWN`, `NOT_APPLICABLE` and empty are rejected. SYNTHETIC must say `NOT_APPLICABLE` and nothing else. Speakers are codes (`S01`) or `UNKNOWN`, never names.
- Real audio is read from, and real evidence written to, locations OUTSIDE the repository; paths inside it are refused. Recordings are opened read-only and never copied. Output is created with exclusive open and a pre-write existence check; nothing is overwritten.
- Normal logs carry recording ids, categories, splits, statuses, counts and timings only (tests prove no transcript, reference, note, speaker code, consent marker or device name appears). The per-item evidence file is a CONTROLLED RESEARCH ARTIFACT and does hold the reference and decoded transcripts; the aggregate report holds none.
- Format: native 16 kHz / mono / 16-bit only. Anything else is EXCLUDED (`NON_NATIVE_FORMAT`), never resampled; use `python -m evaluation.ingest` to make a documented converted copy, keep the original, and describe it in `conversion`.

### 17.4 Metrics (definitions fixed in code and tests)

- **Pipeline statuses:** counts and rates over evaluated items for every Task 4 status plus `NO_ACTIVATION` and `PIPELINE_ERROR`, overall, per category and per split.
- **WER / CER:** normalisation `week7-text-norm-v1` (NFKC, lower-case, punctuation to spaces, apostrophes only inside words, no word ever removed). WER = (S + D + I) / reference words; empty reference = undefined (None), empty hypothesis = all deletions (1.0); micro-averaged. `decoded` covers only items where STT produced a transcript; `end_to_end` also charges capture failures as full deletions. Both are reported.
- **Hallucination control:** `hallucination_control_rate` = non-speech controls with non-empty decoded text / valid non-speech controls. A control is valid unless the pipeline failed (`STT_FAILED`, `INVALID_ACTIVATION`, `PIPELINE_ERROR`). Speech clips never enter this metric (a wrong transcript of speech is an STT error). Optional `--stt-direct-controls` also runs the STT on the whole control clip, bypassing the VAD, as a separate stricter measure.
- **Wake prefix (separate from WER):** outcomes `CORRECTLY_REMOVED`, `RETAINED_SHOULD_STRIP`, `PHRASE_NOT_TRANSCRIBED`, `REMOVED_UNEXPECTEDLY`, `NOT_APPLICABLE`; words removed beyond the wake phrase (an internal "jarvis" stripped by mistake); WAKE_ONLY counts.
- **VAD / segmentation (annotation-based, NOT frame-level VAD accuracy):** captured or not, segment duration, signed start / end offsets and covered fraction against an annotated speech region, NO_SPEECH false negatives on command clips, false speech triggers on non-speech controls.
- **Wake detection (detector mode only):** missed wakes on wake categories and false activations on negatives. In annotated / start modes the wake is SIMULATED and the report says wake accuracy is NOT MEASURED.
- **Performance:** pipeline wall time, VAD time, STT inference, decision latency, audio duration, real-time factors. Startup (Silero and Whisper construction, the first STT inference) is reported separately from steady-state inference. Development PC only; no Pi claim.
- **Policy conflict:** `max_utterance_s` above the STT limit (30 s) is reported (`MAX_UTTERANCE_EXCEEDS_STT_LIMIT`); nothing is truncated and `config/settings.py` is not changed.

### 17.5 Wake modes

`annotated` (default): a scripted detector fires on the first 80 ms chunk ending at or after `wake_end_sample` (SIMULATED; up to one chunk of lag). `start`: every clip is activated at its first chunk end. `detector`: the real wake engine. Non-speech controls are always activated at the start so the VAD and STT are always stressed; in detector mode they additionally get a detector-only pass to count false activations.

### 17.6 Splits

Default run: DEV. HELDOUT needs `--allow-heldout` and an explicit, hashed `--segmenter-config` file; the report states that HELDOUT must not tune VAD thresholds, segmentation timings, wake-prefix rules or STT settings. The report lists `calibration_allowed_splits: ["DEV"]`, warns when one speaker appears in both splits, and warns about empty splits and categories. The calibration sweep refuses any non-DEV recording (`CalibrationSplitError`) and has no switch to include HELDOUT.

### 17.7 Calibration support

`expand_grid` builds the product of candidate values for the nine segmentation parameters, lists every skipped combination with a reason (invalid hysteresis, or `MAX_UTTERANCE_EXCEEDS_STT_LIMIT`), and `run_sweep` returns one TRADEOFF ROW per configuration: command capture rate, false speech trigger rate, TOO_SHORT rate, hallucination-control rate, WER (decoded and end-to-end), p95 pipeline time and decision latency. No "best" configuration is selected; the output says so. The next step is a human choice on DEV evidence, one frozen config file, then one HELDOUT descriptive check. STT results are cached by segment bytes, so sweep latencies are not benchmarks. The sweep has NEVER been run.

### 17.8 Output

`python -m evaluation.week7_voice_eval validate --manifest M` (no model) and `run --manifest M --output DIR [--wake-mode annotated|start|detector] [--split DEV|HELDOUT|ALL] [--allow-heldout] [--segmenter-config FILE] [--stt-direct-controls]` write `week7_eval_items.jsonl` (per item: provenance, status, activation / segment samples and durations, raw STT status, reference availability, WER / CER, wake-prefix outcome, hallucination flag, STT and pipeline times, validation label, review status; no PCM) and `week7_eval_report.json` (corpus and split counts, status distribution, WER / CER aggregates, hallucination rate, wake-prefix metrics, latency distributions, startup, segmenter configuration and its SHA-256, VAD and STT model identifiers and verification flags, decoder options, policy conflicts, limitations, claims). Synthetic or fake items appear only under `synthetic_plumbing_only`; with no real recording `accuracy_results` is null and the report says "NO REAL RECORDINGS EVALUATED". Every item label is one of SOFTWARE-VALIDATED (SYNTHETIC INPUT), RECORDED-HARDWARE AUDIO, RECORDED AUDIO (DEVELOPMENT EVALUATION), UNVERIFIED RECORDING ORIGIN; HARDWARE-VALIDATED and LIVE are rejected by `validate_item`. `human_review` (MATCH / PARTIAL / MISMATCH / EMPTY / NOT_REVIEWED) is recorded and counted but never replaces WER.

### 17.9 Validation (synthetic and fake inputs only)

144 new tests: 40 metric-arithmetic tests (exact S / D / I examples, empty reference and hypothesis, micro aggregation, tie-break, percentile, geometry), 88 evaluator tests (manifest and consent rules, template, every status path, WER substitution / deletion / insertion, hallucination vs speech error, exclusions, wake modes, splits, report content, privacy, output rules, CLI including one real-model plumbing run that skips when a model is absent), 16 calibration tests. Mutation: 57 mutants on scratch copies, 54 killed with and without the model files; survivors classified in WEEK7_STATUS.md (two redundant single validation layers whose combined removal is killed, one equivalent mutant); the with / without comparison exposed and fixed a real CLI bug (traceback on a missing model). A first campaign was invalid because the repository-local-output mutant really created a directory in the scratch copy; the harness now snapshots and cleans the copy after every mutant and the tests clean up any directory a regressed refusal would create.

Full suite: **2194 passed, 6 skipped, 0 failed** (2047 passed / 9 skipped before; 6 skipped now because the 3 real-engine openWakeWord tests ran again, an environment effect and not a code change).

### 17.10 Freeze point (SHA-256)

| File | SHA-256 |
|---|---|
| `evaluation/week7_voice_metrics.py` | `39bdf2f8fa5cb5a7784a1ada9ab4913a6f65294b1faea9d25e37a8683b51f85b` |
| `evaluation/week7_voice_eval.py` | `e0b711a819b35531c459b5cfebc8ae46d2c500ac054ca8ea812acd6ffa32b282` |
| `evaluation/week7_calibration.py` | `f936c8eef65bb686a9453fe3f4ed2a3840b4b4b47ad0dcc30e57e086b8580a10` |
| `evaluation/week7_corpus_manifest.template.json` | `231c811765c11738fb4f3f70a64e44b9858955792af09bed04ed4b7416aa5e1b` |
| `tests/week7_eval_support.py` | `835bdfc518acd3977cb8d9b04ac99ff4176b9e2c62ac77fe7967efe7e03356e1` |
| `tests/test_week7_voice_metrics.py` | `80071d13844ec5169723967d78f4c0e3388d5b07b42f0ee0bac81234ef8bf67a` |
| `tests/test_week7_voice_eval.py` | `94d1c819bfe2a77068a749cd62e1206768120a19d4dbe91e478d77ea1a897f71` |
| `tests/test_week7_calibration.py` | `da29a9fe52ab9a752107377bcaad6b3a84b7f21bbf069eac2ad9f3cc03ce24fc` |

### 17.11 What is not claimed

Real-speech WER, VAD accuracy, wake accuracy, hallucination rate on real silence or noise, latency on target hardware, classroom robustness, threshold calibration, and any hardware or live validation are all NOT MEASURED. Segmentation values remain the uncalibrated evaluation defaults unless a frozen config file is supplied. EXTERNAL INDEPENDENT REVIEW of Tasks 1-5 is DEFERRED (Antigravity quota until 2026-10-14); the Task 3, Task 4 and Task 5 work has had no adversarial review.

### 17.12 Exact real inputs needed to finish Task 5

1. A consented DEV set recorded with the project microphone or by the Hardware Engineer / team: at minimum a handful of clips in EACH of the seven categories (clean command, wake + immediate command, wake + short pause + command, silence control, background-noise control, speech without the wake phrase, short non-speech), several distances and at least one noisy condition, several coded speakers.
2. A separate HELDOUT set (preferably different speakers) that is not looked at while tuning.
3. For every clip: 16 kHz / mono / 16-bit WAV stored outside the repository, an explicit consent reference, a coded speaker id, a human-verified `reference_command_text` (command only), the spoken wake phrase, the microphone and conditions, and `wake_end_sample` for wake categories (or a working real wake engine for `--wake-mode detector`); `speech_start_sample` / `speech_end_sample` where VAD offsets are wanted.
4. A decision from the team on the Task 5 acceptance targets, taken before the HELDOUT run.
5. Optionally a Raspberry Pi 5 run of the same evaluator for Pi timings (separate, labelled).

### 17.13 Status

Week 7: IN PROGRESS. Task 1: COMPLETE. Task 2: COMPLETE. Task 3: COMPLETE — SOFTWARE-VALIDATED. Task 4: COMPLETE — SOFTWARE-VALIDATED. Task 5 evaluation harness: READY. Task 5 execution: PENDING REAL RECORDED DEVELOPMENT CORPUS. Task 5: NOT COMPLETE.

## 18. Pre-handoff targeted hardening (audit findings M1-M6 and L2)

Context: the fresh-session, same-author adversarial audit (NOT an independent Antigravity review, which remains DEFERRED) found no critical or high issue but listed M1-M8 and L1-L12. This pass fixes M1-M6 and L2 only. Baseline before any edit: 2194 passed, 6 skipped, 0 failed; every non-ignored repository file (1176) was hashed before the first edit. Team Leader files, CLAUDE.md, `your_recording.wav`, requirements and all models are unchanged.

### 18.1 Changed files and rebaselined SHA-256 values

Because these files were frozen at the earlier freeze points (sections 12.11, 15.9, 17.10), their hashes are REBASELINED here. `audio/file_source.py` is a frozen Week 1 file changed by an explicitly approved surgical privacy patch (M4).

| File | Change | Previous SHA-256 (first 16) | New SHA-256 |
|---|---|---|---|
| `speech/pipeline.py` | modified | `072ea329826a06a9…` | `08f7a9e06a87cd177f321bbb4abfcc32b34be22fca5fbf410627295bd34b5abb` |
| `audio/file_source.py` | modified | `fdc76d0bad09619d…` | `3a5582dd74c9bd87104afa9efd71b916884955aaef9fca201fc36a5492c76d77` |
| `evaluation/week7_voice_eval.py` | modified | `e0b711a819b35531…` | `d59bb10172805eaa3ef4338f4d394d43d98c21b8b5793806e3ec43e59c075ac7` |
| `evaluation/week7_calibration.py` | modified | `f936c8eef65bb686…` | `3c92e82f6a0a0b4ece301e78c2bcc19a1f795e7b0e1a76dea05b93e4bc48f162` |
| `tests/test_week7_voice_eval.py` | modified | `94d1c819bfe2a770…` | `7397f236e3007bfcd20acaf22c3ed04ce8c558192dd34902da96e61051f83247` |
| `tests/test_week7_calibration.py` | modified | `da29a9fe52ab9a75…` | `500d21108b84d8535bdb82070f5344ccbbdc20c061ab44f3b4ac064acd7a9fd5` |
| `.gitignore` | modified | `2d11aab3c4c26f92…` | `2af7125b878155177ff68fa9d64fb8b860dfb2324da17cd9aba95c2b3e948962` |
| `tests/test_week7_hardening.py` | created | (new) | `1d46dd076323e7e589bb585a41a86f2277ae274678b204186e3d07bc6d0f274b` |

Observer-generated files (`skill-observations/…`) are working files of the required observer workflow, not production files.

### 18.2 M1 — detector exception / sample-clock drift: FAIL CLOSED

Reproduced exactly (audit probe P1): the detector raised after successful chunks; the caller continued feeding; the lost chunk left the pipeline's record and the detector's own count in agreement (`_check_detector_clock` could not see it), so every later activation was mapped one chunk (80 ms at 16 kHz) too early. The frozen detector contract gives no way to resubmit or re-synchronise, so the policy is to invalidate the stream: `_detect()` wraps `process()`, the sample-count update and the clock check; ANY failure sets an untrusted flag and re-raises the original exception; `feed()`, `finish()` and `reset_wake_detector()` then raise `PipelineStateError` until `restart_stream()`, which resets the master clock, detector, reframer, pre-roll buffer and segmenter. Nothing is skipped silently and nothing ambiguous is resubmitted. Tested: failure after a non-zero detector origin, no later activation, the padded-tail failure, clock errors, restart with 10 samples pending in the reframer and loud pre-failure audio, the normal path unchanged, VAD failures not invalidating the clock.

### 18.3 M2 — consent validation

`consent_reference_problem()`: CONSENT-<id> or APPROVAL-<id> (letters, digits, `-`, `_`; at most 80 characters), the id must contain a digit (a register number, which also rejects a bare name) and no placeholder word (TBD, N/A, NONE, PENDING, UNKNOWN, FALSE, NO, TEST, XXX, …), and not a single repeated character. UNKNOWN, NOT_APPLICABLE, empty, `?` and every free-text value are rejected. SYNTHETIC still requires exactly NOT_APPLICABLE. Template entries (TEMPLATE- ids, templates allowed) are exempt so the committed template stays valid. **This is syntax validation: it does NOT prove consent was obtained. A private consent register kept outside the repository and a human approval of the manifest are still required.** Errors never echo the consent value, the speaker code or the audio path.

### 18.4 M3 — manifest location

`load_manifest()` refuses a manifest that holds real or possibly-human records when its own resolved path is inside the repository (the same canonical `assert_outside_repo` used for audio and evidence: resolved paths, component comparison, symlinks and junctions followed). Tested with a look-alike sibling (`repo-evidence`, allowed), `..` traversal and a directory link back into the repository (rejected), synthetic-only manifests (allowed anywhere) and the committed template (loadable only as a template). `.gitignore` gained rules for Week 7 evidence files and real manifests (`*week7*manifest*.json`) with `!evaluation/week7_corpus_manifest.template.json` after them; verified with `git check-ignore` in a scratch repository.

### 18.5 M4 — file-name privacy (surgical patch of a frozen file)

`audio/file_source.py` no longer logs the file name or path (`wav_source_start` now logs source type, rate, channels, sample count, duration, realtime flag, block size) and its exception messages no longer contain the path or base name (`EndOfAudio`, unreadable file, unsupported format). Decoding, block layout and timing behaviour are untouched; all existing audio tests pass unchanged. Basenames are deliberately not logged either.

### 18.6 M5 — preflight and failure policy

Before any model loads or evidence is written the whole selected manifest is validated: wake-phrase format (plain words), a reference that still starts with the wake phrase, wake and speech annotations against the real clip length (equality at the end allowed, one sample beyond rejected), `wake_end_sample` for WAKE_* categories in annotated mode, a detector in detector mode. All problems are listed with ids and counts only. At run time: per-recording failures are recorded on the item; fatal failures (`FATAL_ERRORS`, a fatal STT class reported through a result, an unexpected internal error) raise `EvalFatalError`. `EvidenceWriter` writes `week7_eval_items.jsonl.partial` incrementally; a normal run renames items then report (the report last) so `week7_eval_report.json` marks a finished run; a fatal run writes `week7_eval_report.INCOMPLETE.json` (`run_status` INCOMPLETE_FATAL, `accuracy_results` null) and leaves only `.partial`. No database, no architecture rewrite.

### 18.7 M6 — invalid configuration

Both CLIs validate the segmenter configuration (frame size from the Silero spec, no model needed) and, for the sweep, the whole grid (including "nothing runnable") before any model is loaded. `SegmenterConfigError` and the other expected typed errors give a concise `ERROR:` line and exit 2; a fatal run exits 3. Non-numeric config values are never echoed. Both `--help` texts document the exit codes. Subprocess tests confirm no traceback.

### 18.8 L2 — command punctuation

`_skip_after_phrase()` replaces the blanket separator skip. Removed after the phrase: whitespace, `, ; : ! ?`, the ellipsis, a full stop or hyphen / en dash that is punctuation, an em dash, and a closing quote or bracket that pairs with one that opened before the phrase. Kept: a minus sign (hyphen or en dash directly before a digit or before `.digit`), a decimal point (full stop directly before a digit), and any other bracket or quote. Examples: "hey jarvis -5 degrees" -> "-5 degrees"; "hey jarvis: -5 degrees" -> "-5 degrees"; "hey jarvis .5" -> ".5"; "Hey, Jarvis, what is the temperature?" -> "what is the temperature?"; internal "jarvis" untouched; WAKE_ONLY variants ("Hey Jarvis.", "hey jarvis -", "Hey Jarvis...", "hey jarvis —") unchanged. Known consequence: the WER normalisation turns punctuation into spaces and therefore cannot see a lost minus sign; numeric commands need a manual check.

### 18.9 Deferred and preserved

- **M7 — HELDOUT isolation is procedural, not fully enforced.** The code requires `--allow-heldout` and an explicit config file and warns, but it keeps no run ledger and a determined user can still tune on HELDOUT or call the API directly. A frozen configuration / protocol and ONE authorised HELDOUT evaluation are required before any final claim; nothing here makes HELDOUT misuse impossible.
- **M8 — cold start versus steady state.** Wall time, VAD time and the real-time factor of the first item include cold-start cost; only STT inference excludes its first call. Do not claim steady-state latency from mixed measurements.
- **Remaining low findings, transparently:** L1, L5, L6, L8, L9, L10, L11, L12 unchanged; L3, L4, L7 partly closed by the preflight and the no-echo rules (see WEEK7_STATUS.md for the list).

### 18.10 Validation

152 new tests in `tests/test_week7_hardening.py`. Existing Week 7 evaluator tests were changed only where a message or policy intentionally changed (consent wording, "outside / inside the source repository" wording, two exclusion tests became preflight tests plus a direct-call exclusion check, the log test's consent marker). Mutation: 61 mutants on scratch copies; **61 killed without the model files, 61 with them**; every file restored and hash-verified, stray files removed between mutants. Full suite: **2346 passed, 6 skipped, 0 failed**.

### 18.11 Status

Week 7: IN PROGRESS. Task 1 COMPLETE. Task 2 COMPLETE. Task 3 COMPLETE — SOFTWARE-VALIDATED. Task 4 COMPLETE — SOFTWARE-VALIDATED, HARDENED. Task 5 harness READY (SOFTWARE-VALIDATED, HARDENED). Task 5 real evaluation PENDING. EXTERNAL INDEPENDENT REVIEW: DEFERRED (same-author audit only). No wake, VAD or STT accuracy, hardware or live result exists.

## 19. Pre-handoff package: hardware-integration handoff and GitHub readiness (documentation only)

**Scope.** Documentation and documentation-focused tests only. No production module, no evaluator and no existing test was changed; Week 7 and Team Leader tracking stay separate and the Team Leader files are untouched. Baseline before this work: 2346 passed, 6 skipped, 0 failed; every non-ignored repository file was hashed before the first edit.

### 19.1 Files created

| File | Change | SHA-256 |
|---|---|---|
| `docs/handoff/FATIMA_AI_HARDWARE_HANDOFF.md` | created | `729e4ddd8025f3dc886d96dc1b9564cc121eea2158a71a094f5fa86176668cf3` |
| `docs/handoff/EVIDENCE_INDEX.md` | created | `d42af23ea624546084a5bc68bf4366c84f5aba484782a21e0d07fee3c51c332b` |
| `docs/handoff/AI_GITHUB_PR_READINESS.md` | created | `21e196207e6b40e45dac286c7dc8c00eb82bd0a27db400bb7f4cbc150996a35d` |
| `tests/test_handoff_docs.py` | created | `8a65e1111a72bd5ecb40543d308108e2318c8604ac3760389d7091d56a5e6e1d` |

### 19.2 What the audit of runnable integrations found

Checked against the repository and each command's actual `--help` output. **Runnable today:** the Team Leader Stage A harness (`python -m scripts.ai2_stage_a_transcribe run|review`) on a real recorded WAV; the Week 7 evaluator (`validate`, `run`) and calibration sweep; the wake detector replay (`python -m wakeword.openwakeword_backend`); the older corpus tool (`python -m evaluation.ingest`); `python record_demo.py` (records a WAV from the ReSpeaker). **API only, no command:** `VoicePipeline`, `DeterministicAnswerEngine`, TTS, playback, `SpokenOutputController`, `OutputArbiter`, `AssistantStateManager`. **Genuine integration gaps:** no composition root; no live-microphone harness (Stage B); no announcement trigger; no real upstream-failure classifier; no Face/LED subscriber or transport; no real room-state source. Passing unit tests of the individual modules do not make a working assistant, and the documents say so.

### 19.3 Hardware blockers (classified)

BLOCKING LIVE TEST: the microphone queue silently drops the oldest block; synchronous STT can stall capture; live sample continuity is unverified. HARDWARE-ONLY VALIDATION: the ReSpeaker device name / channel layout; Raspberry Pi 5 dependency compatibility and speed; project speaker playback. TEAM DEPENDENCY: Face/LED subscriber and transport; announcement trigger and sensor data source. NONBLOCKING DEVELOPMENT LIMITATION: uncalibrated VAD thresholds for a first smoke test; possible STT output on silence; wake accuracy not measured. A safe independent capture / processing design with discontinuity reporting and reset behaviour is described in the handoff as a PROPOSAL only; it needs a separately approved change to the frozen `audio/microphone.py`.

### 19.4 What the package contains

`docs/handoff/FATIMA_AI_HARDWARE_HANDOFF.md`: validation levels, the entry-point audit table, missing components, blockers, the capture-design proposal, environment setup, the model acquisition guide with exact pins and hashes (Silero v6.2.3, faster-whisper tiny.en, the sherpa-onnx Lessac voice, openWakeWord), licence questions, test commands, hardware tests A-J with owner / preconditions / command / evidence / acceptance / label (only A has a runnable command; B and D-J are BLOCKED — SOFTWARE INTEGRATION REQUIRED, C is runnable only on recordings), the Task 5 real-data procedure, the return list for the Hardware Engineer and the AI-1 to AI-9 acceptance matrix (statuses copied from `TEAM_LEADER_AI_STATUS.md`, none upgraded). `docs/handoff/EVIDENCE_INDEX.md`: where each kind of private evidence goes, all outside the repository. `docs/handoff/AI_GITHUB_PR_READINESS.md`: the inclusion / exclusion plan, privacy and licence risks, integration risks, teammate actions, a pre-upload checklist and the proposed PR summary. Nothing in these documents is a hardware result; models are referenced by pinned source and hash only.

### 19.5 Validation

`tests/test_handoff_docs.py` (52 tests): every `python -m` command names a real module and real flags (checked against `--help`), every `python -c` command parses and imports only names that exist, repository paths named in the documents exist, every model hash and size matches the code constants and the voice manifest, the observed openWakeWord hashes match the installed files when present, the AI-1 to AI-9 statuses equal the Team Leader status file, Week 7 is IN PROGRESS, tests B and D-J carry the BLOCKED label, every blocker is classified, no unqualified HARDWARE-VALIDATED claim, no e-mail / drive-letter path / recording name, the ignore patterns listed in the PR plan exist in `.gitignore`, and (with git, using a scratch bare Git directory outside the project) a first `git add -A` would pick up no recording, model, result or unplanned top-level folder. A mutation check on a scratch copy altered the documents and `.gitignore` 18 times (a wrong hash digit, an invented flag, an invented module, AI-2 upgraded to COMPLETE, a missing path, an e-mail, a drive path, a removed BLOCKED label, a removed ignore rule, a removed not-a-demo statement, a wrong size, an unqualified hardware claim, Week 7 declared complete, an unclassified blocker, a stray recording with its ignore rule removed, a results file with its rule removed, an unplanned folder, a non-existent import): **all 18 killed**; one extra control (an ignored stray recording) correctly survives because the ignore rule is the guard. Full suite: **2398 passed, 6 skipped, 0 failed**.

### 19.6 Status

Week 7: IN PROGRESS. Task 1 COMPLETE. Task 2 COMPLETE. Task 3 COMPLETE — SOFTWARE-VALIDATED. Task 4 COMPLETE — SOFTWARE-VALIDATED, HARDENED. Task 5 harness READY (SOFTWARE-VALIDATED, HARDENED); real recorded evaluation PENDING. Handoff verdict: **B — PARTIAL HANDOFF — LIVE INTEGRATION GAPS REMAIN** (documentation-ready, not a hardware demonstration). Team Leader AI-1 COMPLETE; AI-2 to AI-9 unchanged.
