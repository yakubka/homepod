# VESTA AI Engineer Subsystem

The AI side of VESTA: voice input, intent routing, truthful answers, spoken output, camera person detection and the classroom state, with the AI-side contracts towards the ESP32 and the mobile app. Everything here is **software-validated with fixtures, fakes or simulation; nothing is hardware-validated yet.**

Owner: the AI Engineer (lanes V-20 to V-26). Status files: `WEEK7_STATUS.md`, `WEEK8_STATUS.md`, `WEEK9_STATUS.md`, `TEAM_LEADER_AI_STATUS.md`. Hardware-day procedure: `docs/handoff/MIDTERM_HARDWARE_VALIDATION.md`.

## What is here

| Area | Folder | State |
|---|---|---|
| Audio capture (ReSpeaker backend, WAV replay), Silero VAD, utterance segmenter | `audio/` | software-validated; live microphone not validated |
| Wake word (openWakeWord, development phrase "hey jarvis") | `wakeword/` | software-validated; accuracy not measured |
| Speech-to-text (faster-whisper tiny.en, offline), wake / VAD / STT pipeline, offline TTS (sherpa-onnx) | `speech/` | software-validated; accuracy not measured |
| Intent router (six categories) and validated structured commands | `intents/` | software-validated on text fixtures |
| Deterministic answers for the 10 frozen questions, output arbiter (no overlapping speech), LISTENING / CHECKING / SPEAKING state, voice runtime, mobile-app contract | `assistant/` | software-validated with fakes |
| Execution-result contract and a SIMULATED lights adapter (origin REAL is refused) | `actions/` | simulated only |
| Sensor contracts, occupancy fusion, the single `ClassroomState` store | `fusion/` | software-validated on simulated inputs |
| YOLOX-Tiny person detection, ROI, three-zone occupancy, live camera source | `vision/` | software-validated offline; undercounts dense classrooms |
| Live voice and camera runners, read-only Pi preflight, fixture demos | `scripts/` | ready for a physical test, never run on a device |
| Evaluation harnesses and the frozen question set | `evaluation/` | ready; no real recordings evaluated |

This folder is self-contained. It does not import from, or change, `services/`, `sim/`, `mobile_app/` or the root `tests/` of the team repository.

## Setup

Use its own virtual environment, created inside this folder (Python 3.11 or newer).

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows activate with `.venv\Scripts\activate`. `numpy` is deliberately unpinned until the first Raspberry Pi install records which version pip resolves there.

Model files are NOT stored in Git. Fetch them once, by the explicit steps in `docs/handoff/FATIMA_AI_HARDWARE_HANDOFF.md` section 7 (Silero VAD, faster-whisper tiny.en, the Lessac voice, openWakeWord, and `models/yolox_tiny.onnx` from `WEEK4_REPORT.md`). Every engine checks its file by SHA-256 before loading and never downloads anything at run time. Licences to note: the Lessac voice is non-commercial only; the licence of the pre-trained hey_jarvis model is unresolved.

## Running

All commands run from INSIDE this folder (every import is relative to it).

```bash
python -m pytest -q -p no:cacheprovider
python -m scripts.vesta_preflight
python -m scripts.vesta_runtime_demo
```

Test baseline (development PC, Windows, Python 3.13, all models present, 2026-10-18, run inside this folder in a copy of the team repository): **3564 passed, 7 skipped, 0 failed**. The 7 skips are: no ReSpeaker (4), no recorded wake-word clip, no opt-in playback flag, and no permitted classroom image (classroom photos are not in the repository; with one present locally the result is 3565 passed, 6 skipped). The team's own suite (`PYTHONPATH=. python -m pytest tests/` from the repository root) is unaffected by this folder. A machine without the model files skips more; that is an environment difference, not a failure.

## Validation status

Software validation only. Still needs physical evidence before anything may be called hardware-validated: the live ReSpeaker on the Raspberry Pi 5, the project speaker, the camera, the ESP32 sensor and light messages, the low-voltage light test, classroom distance / noise tests and the mobile-app connection. Do not treat simulated or software-only results as hardware validation.
