# VESTA AI subsystem ↔ team backend: integration and hardening report

**Date:** 2026-10-22 · **Branch:** `fatima-vision-team-adapter` (base `origin/main` 97951cb) · **Not committed, pushed or merged.**
**Label:** SOFTWARE-VALIDATED / SIMULATED. **Nothing here is HARDWARE-VALIDATED.** No Pi, camera, ReSpeaker, ESP32, GPIO, relay, light or speaker was used.

**Readiness:** Draft PR: **GO** (with the approvals in section 6 requested in it). Merge: **NOT YET**. It needs those approvals, plus a team decision on production authentication and the mobile API (section 4).

---

## 1. Architecture of the whole AI system

```
INPUT (exactly one label per request)                  team backend (main.py, VESTA_MODE replay | real)
───────────────────────────────────────                ───────────────────────────────────────────────────────────────
TYPED_TEXT      POST /voice/ask {"text"} ─────────┐     REST server  services/api/server.py
                                                  │       bind 127.0.0.1 unless api.host + VESTA_API_KEY (env)
SYNTHETIC_AUDIO scripts/vesta_voice_replay.py ─┐  │       X-Room-Key on every route when a key is set
RECORDED_AUDIO  (WAV, consent ref required)    │  │       JSON-only POSTs, 16 KB cap, parameter validation, 500 not drop
LIVE_MICROPHONE ai.voice_input "microphone":   │  │       CORS only for api.cors_origins (CORS is not authentication)
  ReSpeaker → Stage B harness (bounded queue) ─┤  │                │
                                               ▼  ▼                ▼
             AiBridge (AI engineer/assistant/backend_bridge.py) — ONE VoiceAssistantRuntime, one lock
               audio: openWakeWord "hey jarvis" → Silero VAD → faster-whisper STT   (hash-verified local models)
               text and audio → Week 8 router → validated StructuredCommand ─┐   queries → answer engine
                                                                             │       ↑ ClassroomStateStore ← team hw.sensors / lux
                                                                             ▼         (freshness + origin; replay only)
               ActionLoop → TeamLightsAdapter → team Commands.run(lights_on | lights_off, source=voice)
                                                       → Runtime.override → Runtime tick (the ONE decision engine) → Lights
                                     ◄── confirmed only when every zone reaches the level; otherwise 504 / "not confirmed"
               truthful response policy → OutputArbiter (one speaker owner) → TTS → playback
                 main.py text mode: fake tone TTS, silent playback (text reply only)
                 microphone mode / replay demo: Piper TTS → speaker or reply WAV files

camera → OccupancyLoop(make_detector: mobilenet DEFAULT | yolox opt-in) → engine_occupancy
          yolox: counts shown on GET /occupancy ("used_for_lighting": false); the lighting engine sees "no camera count"
```

The team `Runtime` remains the single classroom state and decision engine. The AI never sets a light directly. It calls the same `Commands` that `/command` uses and accepts only what the team rules accept.

## 2. Connections: implemented, simulated, missing

| Path | Status | Evidence |
|---|---|---|
| Typed text → router → backend → confirmed reply | CONNECTED, SIMULATED lights | e2e S1–S14, contract tests |
| WAV → **real** wake → VAD → STT → router → backend → **real** Piper TTS | CONNECTED (replay script), SIMULATED lights | `vesta_voice_replay.py` runs; real-model test |
| Live ReSpeaker → Stage B harness → the same runtime → speaker | WIRED (`ai.voice_input: "microphone"`), **NOT RUN**: no ReSpeaker here | wiring test with fakes only |
| `main.py` text mode reply audio | **text only** (fake TTS) | by design: no speaker is assumed |
| Camera → YOLOX → occupancy → API | CONNECTED (opt-in), advisory | adapter + e2e + real-model tests |
| Sensors → AI answers | replay only; real mode **unavailable** (no timestamps or validity from team `Hardware`) | S4, S8 |
| Mobile app (real Flutter code) | **NOT CONNECTED**; proposal in section 4 | — |
| ESP32 / relays | **NOT CONNECTED**: team `Lights` drives GPIO directly; no ESP32 protocol exists | — |

**How the four input kinds differ:**
- **Typed text:** the recogniser never runs, and `stt_engine_id` says `text-input`.
- **Synthetic audio:** the Piper voice, resampled to 16 kHz. It is not a person.
- **Recorded audio:** a WAV of a real person. The replay script refuses it without `--consent-ref`, and none has been run.
- **Live microphone:** wired, but never run.

Every audio result carries its input kind and the STT engine id (`faster-whisper` for real, `demo-scripted-stt` in tests). A transcript exists only when the recogniser produced one.

## 3. Changed files and owners

| File | Owner | Change |
|---|---|---|
| `services/api/server.py` | Yokubjon | **Hardening:**<br>- loopback bind unless a key is set (`VESTA_API_KEY`, ≥16 characters, environment only);<br>- `X-Room-Key` on every route;<br>- CORS only for configured origins (no wildcard);<br>- JSON-only POSTs and a 16 KB cap;<br>- validation of zone (incl. professor), brightness (int 0–100, no bools), source, intent, params, `active`;<br>- errors always carry `detail`; handler errors return 500.<br>**Integration:** `POST /voice/ask`; `ai` block on `/state`. |
| `services/core/config.py` | Yokubjon | New keys: `vision.detector`, `vision.yolox_lighting`, `ai.enabled`, `ai.voice_input`, `api.host`, `api.cors_origins`. `LOCKED` set: `/config` refuses those six keys. |
| `services/core/engine/engine.py` | Yokubjon | Bug fix: an override for a zone no rule proposed for (e.g. professor, with unknown occupancy) is applied. |
| `main.py` | Javohir | Detector factory, advisory wrapper, AI start/stop when `ai.enabled`. |
| `services/vision/detector.py` | Ripon | `make_detector`, `engine_occupancy`. |
| `services/voice/ai.py` (new) | — (glue) | Builds `AiBridge`: text mode, or microphone mode with real engines through the live runner's factories. |
| `AI engineer/assistant/backend_bridge.py` (new) | Fatima | Bridge, adapter, sensor source, audio feed, live-harness hook, contract mapping. |
| `AI engineer/vision/team_adapter.py` (new) | Fatima | `YoloxTeamDetector` (0.4 floor), `AdvisoryOccupancy`. |
| `AI engineer/scripts/vesta_ai_backend_demo.py`, `vesta_voice_replay.py`, `vesta_backend_yolox_demo.py` (new) | Fatima | Windows demos. |
| `AI engineer/tests/test_ai_backend_e2e.py`, `test_ai_backend_security.py`, `test_ai_voice_path.py`, `test_team_adapter.py`, `test_team_backend_integration.py` (new) | Fatima | Tests. |
| `AI engineer/tests/test_runtime_routing.py`, `test_state_integration.py` | Fatima | Guard lists: reviewed additions. |
| `AI engineer/docs/handoff/AI_GITHUB_PR_READINESS.md` | Fatima | Restored (deleted by `97951cb`; its test could not collect). |

**Not touched:** `mobile_app/`, `services/voice/commands.py`, `services/device/*`, `services/vision/occupancy.py`, the team tests.

## 4. Mobile app ↔ backend: findings and smallest proposal (NOT implemented)

**What the real Flutter code does** (`mobile_app/lib/services/vesta_service.dart`):
- **Port:** default 8000, against the backend's default 5050.
- **No auth header.**
- **`GET /api/sensors`:** used only as a connection check. The sensor cards in `main.dart` show **hard-coded** values (`'52%'`, `'420 lux'`).
- **`POST /api/lighting`:** sends `{"action":"set_brightness","zone","brightness"}`, `{"action":"set_mode","automatic":bool}` and `{"action":"set_bulb","on":bool}`, and expects `{"success": true}`.
- **Never calls** `/voice/ask`, `/state` or `/occupancy`.

None of `/api/sensors` and `/api/lighting` exist on the server (404).

**Smallest compatible solution (needs the mobile developer, Yokubjon and the team lead):**

| App call | Map to (existing logic) | Note |
|---|---|---|
| `GET /api/sensors` | the `/sensors` handler | Response shape to agree (today `{"ok", "sensor": {...}, "ambient": {...}}`) |
| `POST /api/lighting` `set_brightness` | `runtime.override(zone, brightness, "app")`, with the same validation as `/lights/zone` | Reply `{"success": true}`: acknowledged, not "light changed" (the app's own comment says so) |
| `POST /api/lighting` `set_mode automatic=true` | `runtime.auto()` | `automatic=false` has no backend meaning yet: decision needed |
| `POST /api/lighting` `set_bulb` | none: there is no smart bulb in the backend | Decision: drop it, or define it |
| auth | the app sends `X-Room-Key` once LAN exposure is enabled | Key entry and storage in the app: mobile developer |
| port | app setting 5050, or `api_port` 8000 | Pick one |

The alternative is for the app to adopt the v-04 contract routes (`/api/v1/...`, `/voice/ask`). That is a larger app change. Whichever the team picks, the server-side change is small; I have not made it because no contract is approved.

## 5. Vision and lighting safety

| Signal | Meaning | Used for lighting |
|---|---|---|
| **Person count** | detected persons in one frame; YOLOX undercounts dense rooms (MAE 31.99 at the 0.4 floor, 547 images) | **never** (YOLOX default); MobileNet: yes (team policy) |
| **Zone presence** | at least one detected person in a third of the frame; 3 labelled photos only (8 of 9 zones agree) | no |
| **Room presence** | at least one person in the frame; 96.9% of occupied rooms detected, 0 of 101 empty rooms reported occupied (dataset only) | not yet (proposed policy below) |
| **Unknown occupancy** | no usable camera count (camera or inference failure, YOLOX advisory) | lights **hold**: the team rule proposes nothing |

**Current behaviour:**
- **YOLOX** is opt-in and advisory: a camera zero or a wrong count can't switch lights off or set a brightness (tested). `vision.yolox_lighting: true` hands counts to the team rule; this is locked against `/config`.
- **Default MobileNet path:** unchanged team policy. A camera zero during a lecture switches lit zones off (subject to the team's own smoothing and hold rules). This is encoded in the team's own tests (`"empty lit room switches off"`).
- **Team decision needed:**
  - whether a camera zero alone may switch lights off during a lecture;
  - proposed: require a second signal, such as radar absent or no presence for N windows;
  - the YOLOX room-presence policy: lights-on aid, never lights-off.

## 6. Security status and approvals

**Secure by default, development grade:**
- Binds to 127.0.0.1.
- LAN exposure only with `api.host` set in `device_config.json` **and** a key of at least 16 characters in the `VESTA_API_KEY` environment variable. The key is never in a file or in git.
- With a key, every route requires `X-Room-Key` (read-only routes included), compared in constant time.
- Safety keys can't be changed over the API.
- JSON-only POSTs (stops cross-site "simple" form posts).
- No wildcard CORS.

**Not done (team decision):**
- Production authentication: per-user or per-device keys, rotation, TLS. The key travels in plain HTTP on the LAN.
- Rate limiting.
- The single-threaded server blocks other requests for up to 3 s during `/voice/ask`.

**Approvals needed:**
- **Yokubjon:** server hardening, config keys and locks, the engine fix, `/voice/ask`.
- **Javohir:** the `main.py` hooks.
- **Ripon:** the vision factory and advisory default.
- **Mobile developer, with Yokubjon:** the section 4 API decision.
- **Team:**
  - the camera-zero lights-off policy and the YOLOX presence policy;
  - production auth;
  - adopting the v-04 `/voice/ask` contract. Deviations: `decision_id` null, no `/api/v1` prefix, rule-based confidence 1.0/0.0.

## 7. Tests (Windows 11, Python 3.13, 2026-10-22)

| Suite | Result |
|---|---|
| Full AI suite, nothing excluded | **3709 passed, 7 skipped, 0 failed, 0 errors** |
| — e2e / security + contract / voice path | 37 / 60 / 11 passed |
| Team suite (`tests/`) | **94 passed** |
| Mutation checks | 12 of 12 caught (5 e2e and 7 security / voice). The 7 new ones: no auth, LAN without a key, non-JSON accepted, brightness unvalidated, safety keys unlocked, audio labelled as text, unconfirmed audio command reported |

- **The 7 skips:** ReSpeaker ×4, a real "hey jarvis" recording, a real audio output stream, a permitted person image.
- **The counts include real-model tests** that need the git-ignored models in `AI engineer/models/`. A fresh clone shows more skips.
- **Contract checks** validate `/voice/ask` replies and errors against the `origin/v-04-supervision` JSON schemas (skipped if that ref is missing).

## 8. Windows demo (PowerShell, team repository root, environment with `AI engineer/requirements.txt` and the models)

```powershell
$env:VESTA_MODE = "replay"
python "AI engineer\scripts\vesta_voice_replay.py" --synthesize "Hey Jarvis. Turn on the lights." --out "$env:TEMP\vesta_voice" --show-text
python "AI engineer\scripts\vesta_voice_replay.py" --synthesize "Hey Jarvis, what is the temperature?" --out "$env:TEMP\vesta_voice2" --show-text
python "AI engineer\scripts\vesta_ai_backend_demo.py" --image "<classroom photo>" --audio --out "$env:TEMP\vesta_rest"
python -m pytest -q "AI engineer\tests\test_ai_backend_security.py" "AI engineer\tests\test_ai_voice_path.py" "AI engineer\tests\test_ai_backend_e2e.py"
```

**Executed output (2026-10-22):**
- **Command replay:** wake → VAD → `faster-whisper` heard "turn on the lights." Outcome `executed`; all four zones went 0 → 100; one reply WAV. 4.38 s of audio processed in 1.36 s.
- **Question replay:** heard "what is the temperature?", answered from the simulated sensor; lights unchanged.
- **REST demo:** 6 typed requests (executed, answered, answered, rejected, not_understood, executed). YOLOX on the photo reported 2 people in the centre, `used_for_lighting: false`.
- **Consent check:** `--input-kind recorded` without `--consent-ref` was refused (exit 2).

## 9. Raspberry Pi validation checklist (all PENDING)

1. Python environment on the Pi 5: `onnxruntime`, `faster-whisper`, `sherpa-onnx`, `openwakeword` (needs `tflite-runtime` on Linux), `sounddevice`. Copy the models; their hashes are checked at load.
2. Team device libraries on Pi 5: `RPi.GPIO`, `Adafruit-DHT`, `rpi-ws281x`. Known risk: Pi 5 uses a different GPIO chip.
3. `VESTA_MODE=real` with relays: the lights switch, and the AI reply stays "not confirmed" (origin UNKNOWN) until real feedback exists.
4. Camera: frames arrive; YOLOX p95 time against the 250 ms budget; room presence checked against people in the room.
5. ReSpeaker with `ai.voice_input: "microphone"`:
   - wake detection rate and false activations;
   - STT on consented recordings;
   - echo while VESTA speaks (half-duplex is not enforced);
   - end-to-end latency.
6. Speaker: Piper output level and intelligibility in the room.
7. Sensors: team `Hardware` readings with a timestamp and a validity flag before the AI answers real values.
8. LAN: set `api.host` and `VESTA_API_KEY` on the Pi, test the phone with the key, and confirm that unauthenticated requests get 401.
9. Signal shutdown (SIGINT/SIGTERM) on Linux: stops the capture harness, the engine and the lights cleanly.

## 10. Components in one line each (for the FYP video)

- **Wake word (openWakeWord):** listens on the device for "hey jarvis"; nothing is streamed anywhere.
- **VAD (Silero):** finds where the command starts and stops.
- **STT (faster-whisper tiny.en):** turns the command audio into text, on the device.
- **Router:** deterministic rules sort text into six intent categories; commands must pass validation.
- **Action loop and team adapter:** send one command to the team engine and confirm it only after the lights actually changed.
- **Response policy:** fixed, truthful sentences ("In the simulation…").
- **Output arbiter and Piper TTS:** one owner of the speaker; replies are synthesised locally.
- **Classroom state store:** sensor values with age and origin; stale or missing values are not answered.
- **YOLOX adapter:** person detection on camera frames; failures become "no count", never 0; counts don't drive lights.
- **Team engine:** the single decision maker for the lights (schedule, daylight, occupancy, manual overrides).
- **REST server:** local by default; with a key for the LAN; validates every action.
