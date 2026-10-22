# VESTA — Midterm hardware + mobile-app readiness and hardware-day validation protocol

**Status (2026-10-18): SOFTWARE-READY ON FIXTURES AND FAKES. Nothing here is HARDWARE-VALIDATED yet.** No microphone, camera, ESP32, sensor, light or phone has been connected to this code. Every result below is SOFTWARE-VALIDATED or SIMULATED until a person runs the step on the device, records the evidence and changes its status. Device discovery is not hardware validation.

Validation levels (CLAUDE.md): **SOFTWARE-VALIDATED** (tests with fixtures) · **SIMULATED** (fake hardware interface) · **HARDWARE-VALIDATED** (observed working on the physical device; reached by nothing yet). This document supersedes section 14 of `docs/handoff/FATIMA_AI_HARDWARE_HANDOFF.md`: the live runner named there now exists as `scripts/vesta_live_voice.py` (never run on a device).

---

## 1. What exists for each midterm feature

| Feature | Reused (unchanged) | New in this step | Level today | What only the hardware can show |
|---|---|---|---|---|
| A. Temperature + humidity | `fusion/contracts.py`, `fusion/fuse.py` (the one `ClassroomState`), `assistant/answering.py` | `fusion/live_state.py`: decoded-message validator + `ClassroomStateStore` (the single state object voice and app read) | SOFTWARE-VALIDATED on fixtures | the real message format, real values, the humidity part, freshness limits |
| B. Lux + light ON/OFF | `actions/` (Week 9 contract, `ActionLoop`, response policy, simulated adapter), `intents/contracts.py` `validate_command` | lux through the same store; app light requests through `SerializedActionLoop` in `assistant/app_interface.py` | SIMULATED (no light is controlled) | BH1750 values; any real actuation (blocked, section 4) |
| C. Microphone + spoken reply | `audio/microphone.py`, `wakeword/`, Silero VAD, faster-whisper, Week 8 router, `assistant/runtime.py` (one arbiter, one state manager), sherpa-onnx TTS, `audio/playback.py`, Stage B harness `scripts/ai2_stage_b_live.py` | `scripts/vesta_live_voice.py`: constructs the REAL engines and wires them; `--list-devices`, `--speaker-test` | SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT | audibility, recognition, latency, echo / backlog, Pi CPU and RAM |
| D. Camera + person detection | `vision/detector.py` (YOLOX-Tiny), `vision/annotate.py`, `vision/zones.py` | `vision/camera.py` (live `FrameSource`, OpenCV optional), `scripts/vesta_live_camera.py` | SOFTWARE-VALIDATED with a fake capture | camera model, frame acquisition, real FPS, real counts |
| Mobile app | none existed (no app or backend code in this repository) | `assistant/app_interface.py`: transport-free READ / WRITE contract, deny-by-default authorization | SOFTWARE-VALIDATED with a fake mobile client | everything network-side (no protocol agreed) |
| Preflight | model verifiers in `speech/` | `scripts/vesta_preflight.py` | runs on the development PC | the Pi report |

Fixture demos: `python -m scripts.vesta_midterm_demo sensors` (Demo 1, lux of Demo 2), `python -m scripts.week9_simulated_action_demo` (Demo 2 light loop), `python -m scripts.vesta_runtime_demo` (Demo 3 chain with fakes), `python -m scripts.vesta_midterm_demo app` (Demo 5). Tests: `tests/test_midterm_readiness.py`.

Known limitations kept: the person count is detected persons in one frame and **undercounts dense classrooms** (frozen Week 4 benchmark, `WEEK4_REPORT.md`); a live preview says nothing about counting accuracy or three-zone calibration. Freshness limits (`config/settings.py` `FUSION_*`) are still None = UNCONFIGURED, so with the defaults every sensor value reads "unavailable" until a limit is chosen.

## 2. Mobile-app contract (PROVISIONAL, transport-free)

No protocol has been agreed with the Software and Mobile App Developers, so no network service exists. A future adapter (owner OPEN) calls only the four methods of section 14 and must add transport security itself. There is no unauthenticated control path: the default authorizer denies everyone, and an authorizer maps a token to a TRUSTED caller id (the caller never names itself): `env_token_authorizer` maps a token whose SHA-256 equals the environment variable `VESTA_APP_TOKEN_SHA256` to one caller id (no credential in the code); give each phone its own token and caller id. Correlation ids are scoped per caller, so callers can neither collide nor read each other's results.

**READ `read_status()`** returns one JSON object: `api_version`, `api_status`, `evaluated_at_monotonic_s`, `aggregate_origin`, `temperatures` (one entry per sensor, never averaged), `humidity`, `lux`, `detected_person_count` (with the undercount note), `presence`, `light` (capability per action, adapter CONNECTED / DISCONNECTED / NOT_CONFIGURED, adapter origin, last result), `assistant_state` (IDLE / LISTENING / CHECKING / SPEAKING). Every value has `value`, `usability` (FRESH / STALE / INVALID / MISSING / UNCONFIGURED), `origin` (REAL / SIMULATED / UNKNOWN), `age_s` and `reason`. `value` is non-null **only** when FRESH. The app must show the origin; SIMULATED must be visibly labelled. No transcript, audio or video is ever included.

**WRITE `request_light(client_token, action, correlation_id)`**, action `TURN_ON` or `TURN_OFF` for all classroom lights (no zones). Order: correlation id check -> authorization -> action allow-list -> `validate_command` (the same validation the voice path's commands pass) -> the ONE shared action loop -> the Week 9 response policy. Result `status`: `UNAUTHORIZED`, `INVALID_REQUEST` (+ `reason`), `NOT_AVAILABLE` (no adapter), `BUSY` (another command in flight; the app never waits), or `RESULT` with `request_id` (VESTA's), `correlation_id` (the app's), `capability`, `submitted`, `result_state`, `origin`, `confirmed`, `message`, `validation_label`. `confirmed` is true only for a correlated APPLIED_CONFIRMED; ACCEPTED, TIMEOUT_UNKNOWN and UNKNOWN are never success. Today every result is SIMULATED (the contract refuses origin REAL).

## 3. Proposed decoded sensor message (NOT a wire protocol)

What the Pi adapter must hand to `decode_sensor_message` after reading the real ESP32 output: `kind` (TEMPERATURE / HUMIDITY / LUX / PRESENCE), `source_id`, `status` (OK / UNAVAILABLE / FAILED), `value`, `unit` (`degC`, `%RH`, `lx`; required when OK, never converted), optional `sensor_id`, `error_reason` (required when FAILED), optional `device_timestamp` (never used for freshness). Unknown keys, a missing unit, a bool for a number, an out-of-range value or a message claiming its own origin are rejected with a reason code and counted. Origin comes from the adapter object; receive time is the Pi monotonic clock. The humidity device is **unidentified**: nothing assumes DHT22 or any other part.

## 4. Hardware and team dependencies (blockers)

| # | Dependency | Owner | Blocks |
|---|---|---|---|
| 1 | Real ESP32 output format for sensors (a captured sample), sensor ids, humidity part identity | Hardware Engineer | Demo 1, lux of Demo 2 |
| 2 | Pi-side ESP32 adapter implementing `SensorSource` and `ActionAdapter` (transport, framing, timeouts: decisions D1-D12 OPEN) | Software / Hardware, owner OPEN (D11) | Demos 1, 2, 5 on real data |
| 3 | Light output: firmware command, device mapping, voltage and safety approval, an approved safe low-voltage demo output | Hardware Engineer + Team Leader | Demo 2 actuation |
| 4 | Enabling REAL results: `actions/contracts.py` refuses origin REAL and `actions/response_policy.py` only has "In the simulation" texts. A reviewed change must allow REAL only from a named, hardware-validated adapter identity and add truthful real-world texts. NOT done here (protected modules) | AI Engineer, after 2 and 3 | any real light confirmation |
| 5 | Camera model and interface (USB / V4L2 works through OpenCV; a Pi CSI camera needs a Picamera2 capture object, not written) | Hardware Engineer | Demo 4 |
| 6 | OpenCV is not installed (`opencv-python`, or `opencv-python-headless` without a window; about 40-90 MB). Installing it needs approval | Project owner | Demo 4 |
| 7 | Pi 5 install of the pinned packages on aarch64 has never been tried | Hardware Engineer + AI | Demos 3, 4 on the Pi |
| 8 | ReSpeaker device name, channel layout and 16 kHz mono support; the project speaker device | Audio Hardware Engineer | Demo 3 |
| 9 | App transport, authentication scheme and network placement | Software + Mobile App Developers | Demo 5 on a phone |
| 10 | Freshness limits per sensor (choose and record a value with its reason) | AI + Hardware | sensor answers outside fixtures |

## 5. Preflight on the Raspberry Pi (read-only)

```bash
python -m scripts.vesta_preflight
python -m scripts.vesta_preflight --json
```

It reports OS / architecture / Python, optional packages, audio inputs and outputs, camera device nodes, candidate serial ports (`/dev/ttyUSB*`, `/dev/ttyACM*`, or the pyserial port list), model files against their pins and one readiness word per subsystem. It opens no stream, camera or port, sends no actuator command and downloads nothing. Save the JSON output in the external evidence folder (`environment/`, `docs/handoff/EVIDENCE_INDEX.md`). READY_TO_TEST means the prerequisites were found, not that anything works.

## 6. The five demonstrations: hardware-day procedure

Run from the project root inside the virtual environment. Evidence goes to the external evidence folder, never into the repository. Leave every measured cell empty until it is observed.

### Demo 1 — real temperature and humidity

- **Before hardware (runs now):** `python -m scripts.vesta_midterm_demo sensors` (SIMULATED fixture; also shows the stale -> unavailable path).
- **Connections:** ESP32-S3 with the hardware team's firmware and sensors, linked to the Pi as the hardware team specifies.
- **Steps:** (1) the Hardware Engineer shows the raw sensor output with their own tool and saves a sample; (2) convert the sample to one decoded message per line and check it: `python -m scripts.vesta_midterm_demo replay <capture.jsonl>` (opens no port; origin UNKNOWN); (3) only after dependency 2 exists, run the adapter into the store and ask "What is the temperature in the room?" by voice (Demo 3) or read it in the app contract (Demo 5).
- **Expected:** valid lines ACCEPTED, malformed lines REJECTED with a reason; spoken / app value equals the sensor's own display at the same time, with unit and origin REAL.
- **Evidence:** raw sample, replay output, a photo of a reference thermometer / hygrometer next to the sensor, the timestamps.
- **Success:** every reading traceable to a hardware message; a disconnected sensor reads unavailable, never a number.
- **Failure / timeout:** no message -> unavailable (correct behaviour, record it); rejected messages -> record the reason codes and give them to the hardware team; do not edit the decoder to accept an unagreed format.
- **Cleanup:** stop the adapter; no actuator was used.

### Demo 2 — real lux, then light ON/OFF with acknowledgement

- **Before hardware (runs now):** lux in `python -m scripts.vesta_midterm_demo sensors`; light loop in `python -m scripts.week9_simulated_action_demo`.
- **Connections:** BH1750 on the ESP32; for actuation ONLY the approved safe low-voltage demo output (an LED or relay module on low voltage). **Never mains lighting, never rewire anything.**
- **Steps:** (1) lux exactly as Demo 1 (cover the sensor, then light it: the value must fall, then rise); (2) actuation only after dependencies 2, 3 and 4: one TURN_ON, observe, one TURN_OFF, observe.
- **Expected:** lux in `lx`; for the light, the physical output changes AND the controller's acknowledgement for the same request id arrives before the timeout. Only then may the system say it happened.
- **Evidence:** lux values with the covering / lighting times; for each light command the request id, result state, a video of the output, the reply log line.
- **Success:** physical change and APPLIED_CONFIRMED match for every command; a missing reply gives "couldn't confirm", never "done".
- **Failure / timeout:** TIMEOUT_UNKNOWN or UNKNOWN -> the light state is unknown; check it by eye, record it, do not resend automatically (a repeat is a new command with a new id).
- **Cleanup:** turn the output OFF by the hardware team's manual means, disconnect the demo output.

### Demo 3 — ask a supported question by microphone and hear the answer

- **Before hardware (runs now):** `python -m scripts.vesta_runtime_demo` (fake audio in and out).
- **Connections:** ReSpeaker XVF3800 by USB to the Pi; Pi audio out -> DAC -> amplifier -> speakers (audio team).
- **Steps:** (1) `python -m scripts.vesta_live_voice --list-devices` and note the ReSpeaker and speaker entries; (2) `python -m scripts.vesta_live_voice --out-dir <external dir> --output-device <index> --speaker-test --duration-s 120` (add `--declare TEMPERATURE,HUMIDITY,BRIGHTNESS` only for what is really wired; add `--show-text` only if transcripts may be shown); (3) say "hey jarvis", then "What can you do?" and "What is the temperature in the room?".
- **Expected:** the speaker test is heard; each interaction prints one structural line (`outcome`, `intent`, `answer`, `arbitration`, `playback`); the reply is heard once, never overlapping; without a sensor source the temperature answer is "not available".
- **Evidence:** the `*.stage_b.json` record, console output, a room audio recording, a stopwatch or loopback measurement from end of question to start of reply, `top` / `free -m` readings during a reply.
- **Success:** the question is recognised and a truthful answer is heard; zero undetected discontinuities.
- **Failure / timeout:** see section 7 (microphone not found, no audible output, model too slow). Watch for echo: audio captured while VESTA speaks is processed afterwards (half-duplex is not enforced).
- **Cleanup:** Ctrl+C (or `q` in `--activation manual`); the runner stops output, stops the stream and writes the evidence record.

### Demo 4 — camera frames with person boxes and a count

- **Before hardware (runs now):** tests with a fake capture; offline images: `python -m vision.detector <image>`.
- **Connections:** the project camera on the Pi; OpenCV installed (dependency 6).
- **Steps:** `python -m scripts.vesta_live_camera --list`, then `python -m scripts.vesta_live_camera --camera 0 --max-frames 300` (or `--no-display` on a headless Pi, `--print-every 10`). Stand 1, 2, then 3 people in view.
- **Expected:** boxes and "detected persons: N" on the window; the console prints the ACTUAL frame size and measured FPS; a summary line at the end.
- **Evidence:** console output (frame size, FPS, counts), a screen photo with the true number of people noted; no video is saved by the tool.
- **Success:** frames arrive, boxes appear on people, the count changes with the scene. Record misses honestly; this is not an accuracy result.
- **Failure / timeout:** camera not found / no frame within the read timeout -> exit 3 with a message; the camera is released on every exit path.
- **Cleanup:** `q` in the window or Ctrl+C.

### Demo 5 — the same classroom facts in the app and a validated light request

- **Before hardware (runs now):** `python -m scripts.vesta_midterm_demo app` (fixture store, simulated lights, a fake client in the tests).
- **Connections:** none until a transport is agreed (dependency 9). With a transport, the phone calls `read_status()` and `request_light()` through it.
- **Expected:** the app shows the same values and origins that the voice answers use; SIMULATED is visibly labelled; missing data stays empty; an unauthorized request is refused; an authorized request shows the real result state, never assumed success.
- **Evidence:** screenshots next to the matching `read_status()` JSON; the request / result pair with correlation id and request id.
- **Success:** values identical to the store at the same moment; no confirmation without APPLIED_CONFIRMED.
- **Failure / timeout:** BUSY or NOT_AVAILABLE are correct answers, not errors; record them.
- **Cleanup:** close the app session.

### Measurement sheet (leave empty until observed)

| Item | Demo | Measured value | Method | Date / who |
|---|---|---|---|---|
| Temperature vs reference | 1 | | | |
| Humidity vs reference | 1 | | | |
| Lux covered / lit | 2 | | | |
| Light command -> acknowledgement time | 2 | | | |
| End of question -> start of reply | 3 | | | |
| Pi CPU / RAM during a reply | 3 | | | |
| Camera frame size and FPS on the Pi | 4 | | | |
| Detected vs true people (1 / 2 / 3) | 4 | | | |

## 7. Failure diagnostic guide

| Symptom | First checks | Then |
|---|---|---|
| Sensor not detected | the hardware team's own tool shows readings? wiring owned by them | report "unavailable" as the correct answer; do not substitute a value |
| ESP32 unavailable | preflight `serial_candidates`; is the adapter running; cable / power (hardware team) | commands answer "could not be submitted"; nothing is retried |
| Invalid messages | `python -m scripts.vesta_midterm_demo replay <capture.jsonl>` reason codes | agree the format with the hardware team; never widen the decoder silently |
| Camera not found | `python -m scripts.vesta_live_camera --list`; preflight `camera_nodes`; is OpenCV installed; CSI camera needs Picamera2 (blocker 5) | try another index; record the exact error |
| Microphone not found | `python -m scripts.vesta_live_voice --list-devices`; does a device name contain the hint; `--device-hint` | record the device list; 16 kHz mono may be unsupported (blocker 8) |
| No audible output | `--speaker-test` result line (GRANTED + COMPLETED means samples reached the device); correct `--output-device`; amplifier power / volume (audio team) | a COMPLETED line with silence is an output-chain problem, not a software success |
| Model too slow on the Pi | `answer_ms` and the `top` reading; Stage B `discontinuities` (queue overflow) | record the numbers; no speed claim is made; tuning is a later step |
| Mobile app cannot connect | no transport exists yet (blocker 9) | expected today; test the contract with the fake client |
| Unacknowledged lighting command | result TIMEOUT_UNKNOWN / UNKNOWN; adapter log | check the output by eye, switch it off manually, record; never claim success |

## 8. Privacy and safety rules for the session

No audio, transcript or video is saved by default (`--show-text` and the Stage B transcript options are explicit). Evidence stays outside the repository. Nothing streams microphone audio or camera video to the app. No actuator is driven by preflight or by any test. Only the approved low-voltage demo output may be switched. Any result not observed on the device keeps its software label; a status is changed to HARDWARE-VALIDATED only after the step above was performed and its evidence recorded.

---

# Pre-hardware compatibility gate (2026-10-18)

## 9. Feature status at the gate

| Feature | Status | Why |
|---|---|---|
| A. Temperature + humidity | **BLOCKED BY REAL INTERFACE EVIDENCE** (software: SOFTWARE-VALIDATED on fixtures) | no firmware sample, no transport, humidity part unidentified |
| B1. Lux (BH1750) | **BLOCKED BY REAL INTERFACE EVIDENCE** (software: SOFTWARE-VALIDATED on fixtures) | same as A |
| B2. Light ON/OFF + acknowledgement | **SIMULATED**; real path BLOCKED BY REAL INTERFACE EVIDENCE | no command / ACK format, no adapter, no approved low-voltage target; REAL results refused by contract (blocker 4) |
| C. Microphone + spoken reply | **READY FOR PHYSICAL TEST** | live runner built and tested with injected fakes; never run on a device |
| D. Camera + person count | **READY FOR PHYSICAL TEST** for a USB/UVC camera after OpenCV is approved; BLOCKED for a CSI camera (no Picamera2 capture) | camera type unknown |
| Mobile app | **SOFTWARE-VALIDATED** (transport-free contract, fake client) | transport and authentication not agreed |

No feature is HARDWARE-VALIDATED; that label needs the physical evidence of section 6.

## 10. ESP32 interface evidence checklist (for the Hardware Engineer)

**Gate result: NO FIRMWARE SAMPLE WAS PROVIDED. Work stops at the hardware-protocol boundary: no packet, frame or command format is assumed and no compatibility is claimed.** When a sample arrives, convert it line by line to the decoded shape of section 3 and run `python -m scripts.vesta_midterm_demo replay <capture.jsonl>`; every line must be ACCEPTED, and every rejection reason must be resolved with the hardware team, never by widening the decoder.

| # | Item needed (actual, from the running firmware) | Maps to | Received? |
|---|---|---|---|
| 1 | 5+ raw temperature messages copied from the real link, including one with the sensor unplugged | `kind` TEMPERATURE, `status`, `value`, `unit` degC | no |
| 2 | 5+ raw humidity messages, and the humidity part number | HUMIDITY, `%RH` | no |
| 3 | 5+ raw lux messages (covered, room light, lamp), BH1750 mode | LUX, `lx` | no |
| 4 | Presence (LD2410B) messages for present / empty, if sent | PRESENCE | no |
| 5 | Stable device / sensor identifiers and how two temperature sensors are told apart | `source_id`, `sensor_id` | no |
| 6 | Units as sent; any scaling (for example tenths of a degree) | `unit`, value conversion in the adapter | no |
| 7 | How an error or a missing sensor is reported | `status` UNAVAILABLE / FAILED, `error_reason` | no |
| 8 | Transport: USB CDC or UART, port name, baud rate, framing / line ending, push or poll, sending rate | Pi adapter (owner OPEN, D11) | no |
| 9 | Whether opening the port resets the board (DTR / RTS) | adapter start-up | no |
| 10 | Light command message for ON and OFF, with a request id field | `ActionAdapter.submit` | no |
| 11 | Acknowledgement messages: accepted, applied, rejected, failed, each echoing the request id | `parse_reply` states | no |
| 12 | Firmware timeout and behaviour when no reply is possible; what happens after a reset | TIMEOUT_UNKNOWN / UNKNOWN, decision D4 | no |
| 13 | The physical low-voltage lighting target (part, voltage, current, how it is driven) and its safety approval | blocker 3 | no |
| 14 | Firmware version / commit that produced the samples | evidence record | no |

The preflight lists USB-serial ports with their USB vendor id (`303a` Espressif native USB, `10c4` CP210x, `1a86` CH34x, `0403` FTDI). A vendor match names the bridge only; it never proves the board runs the VESTA firmware.

## 11. Raspberry Pi preflight: what each check does (all read-only)

| Check | Source | Safety |
|---|---|---|
| OS, architecture, Python, virtual environment | `platform`, `sys` | none needed |
| Board model, CPU count, RAM total / available, 1-minute load | files under `/proc` | read only |
| Installed version of every `requirements.txt` entry | package metadata | read only. Note: `sounddevice==0.5.6` and `pytest==9.1.1` are pinned (2026-10-18); `numpy` is deliberately NOT pinned until the first Pi install (the development PC's numpy 2.5.3 requires Python 3.12+, Bookworm ships 3.11): record the Pi's Python version and the numpy version pip resolves, then pin to that |
| ReSpeaker / microphone enumeration and 16 kHz mono int16 support | PortAudio device list and format query | the format query opens no stream |
| Output devices and 22,050 Hz mono int16 support (the voice) | PortAudio format query | opens no stream; the speaker is chosen with `--output-device` |
| Camera nodes classified USB_UVC / PI_CSI_CAMERA_RECEIVER / OTHER | `/sys/class/video4linux` | read only. Pi 5 exposes ISP / codec nodes that are not cameras; only USB_UVC counts for the OpenCV runner |
| ESP32 serial ports with USB vendor / product id | `/sys/class/tty`, or pyserial's port list | ports are listed, never opened |
| Model files against their pins | SHA-256 and the existing verifiers | read only |

```bash
python -m scripts.vesta_preflight --json
```

## 12. Camera compatibility decision

| Camera type | Supported by `scripts/vesta_live_camera.py` today | Dependency | Decision |
|---|---|---|---|
| USB / UVC webcam | yes, through OpenCV `VideoCapture` (index or `/dev/videoN`) | `opencv-python` (window) or `opencv-python-headless` (console only, use `--no-display`), installed into the project virtual environment | install only after the camera is confirmed USB and the owner approves |
| Raspberry Pi CSI camera (Camera Module on Pi 5) | **no**: Pi 5 CSI cameras go through libcamera; OpenCV V4L2 capture is not a supported path | Picamera2 (Raspberry Pi OS package `python3-picamera2`, needs a virtual environment created with system site packages) plus a small capture object for `CameraFrameSource` | not written until the camera is identified |

Shutdown with a stalled camera (`vision/camera.py`): a read that does not return in `--read-timeout-s` is a camera failure, the source refuses further reads, the device is never released while a read is still running (the release happens when that read returns), and the reader is a daemon thread, so the runner always exits. Limit: if the driver never returns from a read, the device is released only when the process exits. Nothing was installed. The dense-classroom undercount (frozen Week 4 benchmark) and the absence of any three-zone calibration still apply to every live preview.

## 13. Live voice runner audit and half-duplex proposal

| Area | Finding |
|---|---|
| Device selection | input by name hint (`--device-hint`, default from `config/settings.py`), never by a fixed index; output by `--output-device`, with a warning when it is left at the platform default; `--list-devices` opens nothing |
| Speaker output | through the runtime's one arbiter; `--speaker-test` reports arbitration and playback status, but only a person can confirm sound was heard |
| Echo / self-triggering | half-duplex is NOT enforced. Replies never contain the wake phrase, so self-activation needs the wake model to fire on VESTA's own voice: not measured. The larger risk is that a person speaking during a reply is processed late |
| Audio backlog | the processing worker plays the reply synchronously, so the capture queue grows by the reply length (bounded: 400 blocks = 25.6 s; overflow is a reported discontinuity, never hidden) |
| Stop / cancel | Ctrl+C or `q`: playback is stopped, workers joined with a timeout, the device released, the evidence written; Ctrl+C before listening starts exits cleanly (code 3, no record) |
| Microphone loss | 10 read timeouts in a row (5 s) end the session as a failure with a record; there is **no automatic recovery**: restart the runner after reconnecting |
| Pi resources | unmeasured; record `top` / `free -m` during a reply and the `answer_ms` values on the device |

**Smallest safe half-duplex policy (PROPOSAL, not implemented, needs approval):** while the arbiter owns the speaker, and for a short tail after playback ends, ignore wake detections whose audio was captured in that interval; keep capturing so continuity counters stay exact. It must be keyed to sample positions (the capture is processed late), so it needs the capture position at the SPEAKING start and end from the Stage B harness, a wrapper around the wake detector in the runner, and a tail length measured on the device. It touches no frozen audio module. Decide it only after the hardware session has measured whether the wake model fires on VESTA's own voice at classroom volume.

## 14. Flutter integration contract (conceptual operations, NOT approved HTTP routes)

The transport (HTTP, WebSocket, MQTT, BLE) and authentication are **for the Mobile App Developer and the Software Engineer to confirm**. Whatever is chosen must authenticate every operation, must not expose a control operation without authentication, and must carry the four operations below unchanged. Each maps to one method of `assistant/app_interface.py`.

| Operation | Method | Input | Output | Auth |
|---|---|---|---|---|
| GET classroom status | `read_status()` | none | the section 2 object | the transport must authenticate (the method itself holds no secret) |
| POST validated lighting request | `request_light(client_token, action, correlation_id)` | `action` TURN_ON or TURN_OFF; `correlation_id` 1-64 letters, digits, dot, underscore or hyphen, unique per user action | `status` + result fields | token required (default: everyone denied) |
| GET command execution result | `get_command_result(client_token, correlation_id)` | the correlation id used in the POST | the caller's own stored RESULT, or EXPIRED (executed, aged out) / NOT_FOUND / UNAUTHORIZED | token required |
| GET assistant state | `assistant_state()` | none | `state` IDLE / LISTENING / CHECKING / SPEAKING / NOT_CONFIGURED and `color` | the transport must authenticate |

Response semantics the app must follow:

- **Provenance:** every value carries `origin`; show SIMULATED and UNKNOWN visibly; show a value only when `usability` is FRESH, otherwise show "unavailable" with `reason`.
- **Lighting:** success only when `confirmed` is true. `result_state` ACCEPTED, TIMEOUT_UNKNOWN or UNKNOWN means "not confirmed" (the light may or may not have changed). REJECTED and FAILED are failures. UNKNOWN_CAPABILITY or SUPPORTED_BUT_UNAVAILABLE in `capability` means nothing was sent.
- **Retries:** resend the SAME `correlation_id` after a network failure. Within one VESTA process a (caller, correlation id) that was submitted to the controller is executed at most once: a recent id returns the stored result (`replayed: true`); an id that aged out of the 64-result history is refused as `CORRELATION_ID_EXPIRED`, never re-executed; the same id with a different action is `CORRELATION_ID_REUSED`. BUSY, NOT_AVAILABLE and any request that was never submitted (link down, capability unavailable or unknown) are not recorded, so retrying the same id later really runs. **Across a VESTA restart nothing is remembered** (no durable store, no device-side idempotency; exactly-once is NOT claimed): every response carries `server_session`, and when it changes the app must treat an unconfirmed earlier request as unknown and read the light state instead of retrying blindly.
- **Errors:** UNAUTHORIZED, INVALID_REQUEST (`reason` BAD_CORRELATION_ID, UNSUPPORTED_ACTION, CORRELATION_ID_REUSED, CORRELATION_ID_EXPIRED, REPLAY_RECORD_FULL or a validation status), NOT_AVAILABLE, BUSY, NOT_FOUND, EXPIRED. Full results are kept in memory for the last 64 requests; the record of used ids lasts for the process (bounded PER CALLER; when one caller's record is full, only that caller's new requests are refused until restart) and is lost on restart.
- **Privacy:** no transcript, audio or video is in any response.

Questions for the Mobile App Developer: (1) transport and message encoding; (2) authentication scheme, and how tokens are issued and revoked; (3) whether the phone polls or needs push for state changes; (4) where the service runs (on the Pi, local network only?); (5) which values the first app screen must show.
