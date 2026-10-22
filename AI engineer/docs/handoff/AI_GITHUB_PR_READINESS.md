# VESTA — AI subsystem: GitHub pull-request readiness

**Purpose:** a reviewed inclusion/exclusion plan and a proposed PR description for uploading the AI work to the team's official GitHub AI repository.

**What this document does NOT do:** it does not run `git init` in the project folder, change any remote, push, force-add an ignored file, commit anything or merge into `main`. Those steps are left to the user, after the decisions in section 7. This project folder is **not a Git repository today** (there is no `.git`).

Tracking stays separate: Week 7 roadmap status is in `WEEK7_STATUS.md`; the Team Leader AI-1 to AI-9 status is in `TEAM_LEADER_AI_STATUS.md`. **This PR does not complete the AI leader tasks** (1 of 9 is complete).

---

## 1. How the plan was checked

The list of files a first `git add -A` would pick up was computed WITHOUT creating a repository in the project folder, using a scratch bare Git directory outside it (replace `<SCRATCH>` with any empty folder outside the project):

```bash
git init -q --bare <SCRATCH>/gd
git --git-dir=<SCRATCH>/gd --work-tree=. ls-files --others --exclude-standard
```

The result was cross-checked against this plan, then scanned for secrets, e-mail addresses, absolute user paths, IP addresses, key-like strings and user-name tokens. The only hits were fake values inside tests (a fake user-folder path in a test, and a test variable named `secret` that holds a made-up word). `tests/test_handoff_docs.py` re-checks that no candidate file is a recording, a model binary or a results file, and that the ignore patterns named below really exist in `.gitignore`.

`.gitignore` only stops accidental `git add`; it does not stop `git add -f`, cloud sync or backups. **Privacy is not delegated to it**: the plan below also removes risky items from the folder (section 7) and the tools refuse to read or write real evidence inside the repository.

## 2. INCLUDE

| Class | Paths | Notes |
|---|---|---|
| SOURCE | `audio/`, `wakeword/`, `speech/` (including `speech/voices/*.manifest.json`), `assistant/`, `fusion/`, `vision/`, `config/`, `scripts/`, `record_demo.py`, `requirements.txt` | production and development code of the AI subsystem |
| TESTS | `tests/` (test modules, shared helpers `tests/*_synthetic.py`, `tests/*_fakes.py`, `tests/week7_eval_support.py`, `tests/conftest.py`) | 2398 tests pass on the development PC (2346 before the handoff-document consistency tests were added) |
| EVALUATION TOOLS | `evaluation/*.py`, `evaluation/week7_corpus_manifest.template.json`, `evaluation/ai2_stage_a_*_template.json`, `evaluation/ai_question_test_set_v1.json`, `evaluation/manifest.example.csv` | templates carry no real data and are marked as templates |
| DOCUMENTATION | `docs/` (including `docs/handoff/`), `WEEK2_*` to `WEEK7_*` reports and status files, `TEAM_LEADER_AI_REPORT.md`, `TEAM_LEADER_AI_STATUS.md` | keep Week 7 and Team Leader files separate |
| MODEL MANIFESTS | `speech/voices/vits-piper-en_US-lessac-medium.manifest.json` (359 files with sizes and SHA-256); the Silero and Whisper pins live as constants in `audio/silero_vad.py` and `speech/faster_whisper_backend.py` | manifests and hashes only, never the binaries |
| REPOSITORY FILES | `.gitignore` | includes Week 7 evidence and manifest protection rules |
| **LOCAL-ONLY — NOT UPLOADED** (decided 2026-10-18) | `CLAUDE.md` | personal Claude Code instructions for the AI Engineer's own sessions (the "MY ROLE" section, assistant development rules, the task-observer activation that points at `.claude/skills/task-observer/`). It stays in the local project folder and is never part of the team upload. Its status section was refreshed on 2026-10-18; the authoritative status files are `WEEK7_STATUS.md` to `WEEK9_STATUS.md` and `TEAM_LEADER_AI_STATUS.md` |

At the time of the check there were 125 candidate files, about 2 MB, plus the three new files under `docs/handoff/` and `tests/test_handoff_docs.py`.

## 3. EXCLUDE

| Class | Items | How it is excluded | Extra step |
|---|---|---|---|
| PRIVATE FILES | `your_recording.wav` (a personal recording in the project folder), `.env`/`.env.*` (none exist now), `*.pem`, `*.key`, `credentials.json`, `secrets.json`, `.claude/` (local agent settings, hooks, skills), `.cmem-projects/`, `agent-transcripts/` | `.gitignore` (`*.wav`, `.env`, `.claude/`, `agent-transcripts/`, …) | **move `your_recording.wav` out of the folder**; do not rely on the pattern |
| MODEL BINARIES | `models/silero_vad_v6.2.3.onnx`, `models/yolox_tiny.onnx`, `models/faster-whisper-tiny.en-0d3d19a3/`, `models/vits-piper-en_US-lessac-medium-9e3febfa/`, openWakeWord files (inside `.venv`) | `*.onnx`, `*.tflite`, `models/faster-whisper-*/`, `models/vits-piper-*/` | acquire from the pinned sources instead (handoff section 7) |
| RECORDINGS | any `*.wav`, `*.flac`, `*.mp3`, `*.m4a`, … ; `vision_assets/` (164 MB of images that may show people); corpus folders `wakeword_corpus/`, `VESTA-data/`, `consent/`, `private/`, `speaker_key*` | audio patterns, `vision_assets/`, corpus patterns | only tiny SYNTHETIC test clips may ever be committed, and only under `tests/fixtures/synthetic/` |
| RESULTS | `week7_eval_items.jsonl` (+`.partial`), `week7_eval_report.json` (+`.partial`, `.INCOMPLETE.json`), `week7_calibration_sweep.json`, any real manifest (`*week7*manifest*.json` except the template), Stage A `*.stage_a.json` | added `.gitignore` rules, plus the tools refuse real evidence/manifests inside the repository | keep them in the external data folder |
| SCRATCH | `graphify-out/`, `_trial_temp/`, `.pytest_cache/`, `__pycache__/`, `*.pyc` | `.gitignore` | none |
| LOCAL ENVIRONMENTS | `.venv/`, `venv/` | `.gitignore` | none |
| TOOL STATE | `skill-observations/`, `skill-updates/` | `.gitignore` | none |

Ignore patterns verified present in `.gitignore` (checked by the test):

```
.env
.venv/
__pycache__/
*.onnx
*.wav
.claude/
.cmem-projects/
agent-transcripts/
graphify-out/
_trial_temp/
/CLAUDE.md
skill-observations/
skill-updates/
vision_assets/
models/faster-whisper-*/
models/vits-piper-*/
week7_eval_items.jsonl
week7_eval_report.json
week7_calibration_sweep.json
*week7*manifest*.json
!evaluation/week7_corpus_manifest.template.json
```

## 4. Privacy and licensing risks

- **Personal data:** the recordings, consent register, reference transcripts and Week 7 manifests/results of real people must never enter the repository. File names are treated as personal data: name files by id. `your_recording.wav` is personal and still sits in the project folder.
- The reports are text only, with no secrets, but they describe internal team structure; review before publishing to a shared repository. **CLAUDE.md** describes local tooling and stays local (section 2).
- **Consent** checks in the tools are syntactic only; a private consent register and human approval remain required.
- **Licences (unresolved questions are flagged, not decided):** Silero MIT; faster-whisper tiny.en MIT; the Lessac voice is an ACADEMIC / NON-COMMERCIAL asset and espeak-ng data is GPL-family (the voice files are not committed or redistributed); the licence of the openWakeWord pre-trained `hey_jarvis` model files is UNRESOLVED; the Week 4 external benchmark's provenance/licence is NOT VERIFIED. There is no `LICENSE` file at the repository root: the team must choose one. No legal review was done.

## 5. Integration risks when moving the subsystem into the official repository

| Risk | Detail | Suggested handling (no unrelated module is changed) |
|---|---|---|
| Package / import paths | Everything uses absolute top-level imports (`from audio.x import …`, `from config import settings`). The top-level names `audio`, `config`, `tests`, `scripts`, `docs`, `vision`, `evaluation` are generic and may collide with teammates' directories | agree the layout BEFORE copying; do not nest the code without a plan for the imports |
| Model paths | defaults are `models/` under the project root, computed from each module's file location | keep `models/` at the root of the AI package or pass explicit paths |
| Dependencies | one flat `requirements.txt` with exact pins for some packages and none for `numpy`, `sounddevice`, `pytest` (a test tool listed as a runtime requirement) | reconcile with teammates' requirements; no change made here |
| Windows versus Raspberry Pi | developed and tested only on Windows 11 / Python 3.13; aarch64 wheels for onnxruntime, ctranslate2, sherpa-onnx, av and openwakeword are unverified; the suite has never run on Linux | first Pi install and test run are evidence items (handoff section 12); some tests use Windows-only fallbacks (guarded) |
| Hardware ownership | ESP32 firmware and wire protocol, Face/LED, microphone and speaker hardware belong to other roles; the AI side only offers in-process Python contracts | document the contracts; do not edit their code |
| Missing composition root and live harness | nothing wires the modules into a running assistant; no Stage B | handoff sections 3 and 14 |
| Production VAD values | `config/settings.py` holds `None` for the VAD/segmenter keys; the loader raises until they are set | set only from DEV evidence (Week 7 Task 5) |
| Duplicated files | `CLAUDE.md`, `.gitignore`, `requirements.txt`, `tests/conftest.py` and a README are likely to exist already | do not overwrite a teammate's file; merge by hand |
| State and command interfaces | `StateEvent` is in-process only; the ESP32 structured commands (`SET_LIGHT_ZONE`, …) in `CLAUDE.md` are conceptual and NOT implemented; the sensor interface is NOT AVAILABLE | keep them as stated contracts |

## 6. What teammates need to do

- **Hardware Engineer:** supply recordings and metadata as described in `FATIMA_AI_HARDWARE_HANDOFF.md`; do the first Pi install and report it; identify the ReSpeaker device name and channel layout; check speaker playback at 22,050 Hz.
- **Vision/Face owner:** build the subscriber and transport for `AssistantStateManager` events (contract in `TEAM_LEADER_AI_REPORT.md` section 9.14).
- **Audio hardware owner:** choose and verify the output device used by the playback engine.
- **App/schedule owner:** define the announcement trigger (nothing in this repository sends announcements).
- **Team Leader:** set the AI-3 / AI-4 acceptance targets and approve the single next implementation (live capture harness).
- **Everyone:** do not commit recordings, results, consent records or model binaries.

## 7. Pre-upload checklist (for the user; none of this was done automatically)

1. Move `your_recording.wav` out of the project folder (and decide about `vision_assets/`).
2. Leave `CLAUDE.md` out of the upload (decided 2026-10-18: it is local-only). Check `git status --short` after staging that it is not there.
3. Agree the target layout and the license with the team; check the official repository for colliding names.
4. Compute the candidate list with the scratch Git directory command (section 1) and compare it with section 2.
5. Create the repository or clone the official one yourself, work on a **branch**, never on `main`.
6. After staging, review `git status --short` and `git diff --cached --stat`; never use `git add -f`.
7. Open the PR from the branch; do not merge it yourself; do not push to `main`.
8. Run `python -m pytest -q -p no:cacheprovider` on the new location and report the numbers honestly (skips depend on which models and devices exist).

## 8. Proposed PR summary

**Title:** AI subsystem: Week 7 voice pipeline (wake + VAD + STT), evaluation harness, spoken-output foundations, and hardware handoff

**What was implemented**

- Audio acquisition abstraction, WAV replay and the ReSpeaker capture backend.
- openWakeWord wake detector backend; Silero VAD adapter and a wake-activated utterance segmenter; faster-whisper tiny.en STT behind an engine-neutral contract.
- `speech/pipeline.py`: wake -> VAD -> STT -> normalised text, with a single master sample clock, a fail-closed policy on detector failure and a narrow wake-prefix rule.
- Week 7 Task 5 evaluation harness (WER/CER, hallucination-control and wake-prefix metrics, DEV-only calibration sweep) and the Team Leader AI-2 Stage A recording harness.
- Deterministic answering for the frozen 10-question set, offline TTS, a playback engine, announcement/fallback orchestration, an output arbiter and a LISTENING/CHECKING/SPEAKING state contract.
- Model manifests with pinned sources and SHA-256 values; no model binaries.

**What was software-validated** (2398 tests pass on the development PC; 6 skip because no ReSpeaker, no recorded wake clip and no opt-in playback flag): every item above, with fake or synthetic inputs, mutation checks of the key rules, and a same-author adversarial audit followed by a hardening pass. An independent external review is DEFERRED.

**What still requires real hardware**: live microphone capture (no live harness exists), ReSpeaker channel/device configuration, Raspberry Pi 5 installs and speed, real wake/VAD/STT accuracy and hallucination behaviour (nothing real has been measured), the project speaker, the Face/LED state display, and every classroom test.

**What teammates need to do**: see section 6.

**What remains open in Week 7**: Week 7 is IN PROGRESS. Tasks 1-2 are COMPLETE; Task 3 is COMPLETE — SOFTWARE-VALIDATED; Task 4 is COMPLETE — SOFTWARE-VALIDATED, HARDENED; the Task 5 evaluation harness is READY but the real recorded evaluation (DEV and one HELDOUT check) has not been performed. HELDOUT isolation is procedural, first-item latency includes cold start, and production VAD thresholds are uncalibrated.

**Not claimed**: this PR does not complete the Team Leader AI tasks (AI-1 is complete; AI-2 to AI-9 are not), does not validate any hardware, and reports no recognition accuracy. Model licences flagged above are unresolved.

**Not included**: model binaries, recordings, consent records, results, local tool state, `.venv`, `CLAUDE.md` (local-only).

## 9. Team repository target (checked read-only, 2026-10-18)

The official team repository already has an `AI engineer/` folder created by the AI Engineer (the 2026-10-11 snapshot, merged through its first pull request). The AI files go into that folder and nowhere else: every teammate's code lives outside it (`services/`, `sim/`, the root `tests/`, `main.py`, `mobile_app/`), and the team's pytest configuration collects only its own root `tests/`, so the two suites stay separate.

- The `README.md` at the root of this project folder is the README of the team's `AI engineer/` folder (it replaces the stale 2026-10-11 one). It is the only top-level file added for the team layout.
- Tests run from INSIDE the folder (every import is relative to it): change into the folder, then `python -m pytest -q -p no:cacheprovider`.
- Start from a fresh copy of the team's `main` on a new branch; the AI files and nothing else change.
