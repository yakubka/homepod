# VESTA — Week 3 Report: Hardware + Interface Validation (audio path, camera, ESP32, sensors, actuators)

Run date: 2026-10-02. Task 1 (audio path) is below; Task 2 (camera / ESP32 / sensors / actuators) is the last section.

## Verdict

| Item | Classification |
|---|---|
| Raspberry Pi 5 + ReSpeaker XVF3800 capture via `ReSpeakerMicrophone` | **NOT AVAILABLE** (hardware absent in this run) |
| Live mic -> `Reframer` -> wake-word compatibility smoke test | **NOT AVAILABLE** |
| Failure behaviour: device missing / stream open fail / stream start fail / read timeout / overflow flag | **SOFTWARE-VALIDATED** (mocked `sounddevice`; not PortAudio/ALSA behaviour) |
| Existing WAV -> Reframer -> openWakeWord real-engine path | **NOT TESTED this run** (engine import blocked by Windows policy; Week 2 SOFTWARE-VALIDATED result stands for its date) |

Nothing in this report is HARDWARE-VALIDATED. No command output from a Pi is claimed.

## Environment of this run

- Windows 11 (10.0.26200) AMD64 development PC, Python 3.13.14, sounddevice 0.5.6 (PortAudio V19.7.0), onnxruntime 1.30.0, openwakeword 0.6.0 installed.
- Not a Raspberry Pi; no ReSpeaker. Input devices present: built-in AMD/Realtek microphone arrays only. The `"ReSpeaker"` name hint matches none of them, so `find_device` raised `DeviceNotFoundError` rather than substituting a laptop mic (covered by `test_no_matching_device_fails_explicitly`).

## Baseline (before any change)

`pytest -q`: **211 passed, 7 skipped, 0 failed.**
Skips: 3 ReSpeaker hardware tests; 4 openWakeWord real-engine tests (`openWakeWord cannot be imported on this machine`).
This differs from the frozen Week 2 plain-run figure (214 passed / 4 skipped). See "openWakeWord environment issue" below: three real-engine tests that passed in Week 2 now skip. That is an environment inability to rerun them, **not a code regression**.

## openWakeWord environment issue (documented 2026-10-02, no packages changed)

**Tests affected** (`tests/test_openwakeword_backend.py`, Tier 2, all use the `real_detector` fixture, which skips when `import openwakeword` raises `ImportError`):

| Test | Week 2 plain run | Now |
|---|---|---|
| `test_real_engine_contract` (line 270) | passed | skipped |
| `test_real_engine_ignores_silence` (line 275) | passed | skipped |
| `test_real_engine_ignores_low_level_noise` (line 284) | passed | skipped |
| `test_real_engine_detects_hey_jarvis_in_a_recorded_clip` (line 290) | skipped (needs `VESTA_HEY_JARVIS_WAV`) | skipped (import fails first; would skip anyway without the env var) |

Skip reason for all four: `openWakeWord cannot be imported on this machine`. Underlying error (reproduced with a direct import):
`ImportError: DLL load failed while importing _loss: An Application Control policy has blocked this file.`
Import chain: `openwakeword/__init__` -> `custom_verifier_model` -> `sklearn.linear_model` -> `sklearn._loss.loss` -> `sklearn/_loss/_loss.cp313-win_amd64.pyd`.
The 17 openWakeWord tests that run (Tier 1, fake model; the file has 21 in total, 4 skipped) still pass, so the adapter logic is still exercised.

**Origin (from installed files, package metadata and the Windows Code Integrity log only):**

| Candidate | Finding |
|---|---|
| Windows Application Control | **Source of the block.** Code Integrity log (Microsoft-Windows-CodeIntegrity/Operational) records events 3033 and 3077 for `python3.13.exe` loading `...\sklearn\_loss\_loss.cp313-win_amd64.pyd`: "did not meet the Enterprise signing level requirements or violated code integrity policy (Policy ID {0283ac0f-fff1-49ae-ada1-8a933130cad6})". Events recur on each import attempt (5 pairs in the log window). `VerifiedAndReputablePolicyState = 1` in `HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy`, which I read as Smart App Control being in enforcement mode (my interpretation of that value; not confirmed in the Windows Security UI). |
| scikit-learn native loading | **Where it surfaces**, not why. Only this one binary is blocked: `sklearn.utils._openmp_helpers`, `_typedefs`, `__check_build`, `scipy.sparse`, `scipy.linalg._fblas` and `onnxruntime` all load. All sklearn `.pyd` files checked are unsigned (`NotSigned`), so signing alone does not explain why only `_loss` is refused; a reputation-based decision is plausible but unproven. |
| Python version compatibility | Not indicated. Python 3.13.14; scikit-learn 1.9.1 requires `>=3.11` and ships a `cp313` wheel; the error is a policy block, not a missing symbol or ABI mismatch. |
| openWakeWord | Not the cause. 0.6.0 simply imports sklearn at package import time (`custom_verifier_model`), a dependency it declares (`scikit-learn <2,>=1`). Our code does not use that module. |
| Other dependency | numpy 2.5.3, scipy 1.18.1, onnxruntime 1.30.0, joblib 1.6.0, threadpoolctl 3.7.0 all import. |

**What changed since Week 2 (not proven):** the real engine imported successfully on this PC on 2026-09-21 (~845 ms import, real inference run, 215 passed in Week 2). The same `.pyd` file is on disk dated 2026-09-21, so the package did not change; the Windows side did. Code Integrity policy files under `C:\Windows\System32\CodeIntegrity\CIPolicies` carry modification times of 2026-09-23 (e.g. `{60FD87F8-...}.cip`, `Reserved\{1EAF45D6-...}.cip`), which fits a Windows policy update after the Week 2 run. The blocking policy ID `{0283ac0f-...}` itself has a 2024 file date, so I cannot say which file change triggered the block.

**Classification:**
- Previously SOFTWARE-VALIDATED (Week 2): real openWakeWord inference on this PC: contract, silence and noise rejection, plus one real "hey jarvis" clip. Still recorded as validated for that date and environment.
- Currently: **cannot be re-run on this PC** (environment inability).
- Code regression: **none found.** No wake-word code changed; the only code change in Week 3 is in `audio/microphone.py`.

**Minimum next action (not performed):** none needed for the project goal, because the target runtime is the Raspberry Pi (Linux, where this policy does not apply) and the Pi procedure below re-runs these tests there. Only if real-engine runs on this Windows PC are wanted again: the Windows-side choices are to allow the blocked file or turn Smart App Control off, both security-setting changes the user must make deliberately, or to develop on the Pi. No package change is recommended, since the block is on a file's policy status rather than on a version.

## Existing code facts (assumptions the software currently makes)

| Item | Current assumption | Source |
|---|---|---|
| Device selection | by case-insensitive name substring `"ReSpeaker"`, never by index | `config/settings.py:13`, `audio/microphone.py:28` |
| Sample rate / channels / dtype | 16000 Hz, 1 channel, int16, requested directly from PortAudio | `config/settings.py:20-22` |
| Block size / queue | 1024 frames (64 ms); 50-block queue, oldest dropped on overflow | `config/settings.py:25-29` |
| Reframer | accepts int16, 1-D or (n,1); emits 1280-sample (80 ms) chunks | `audio/reframer.py` |
| Detector contract | 16 kHz, mono source (`run_on_source` rejects otherwise) | `wakeword/detector.py:183-188` |
| OS | none hard-coded; sounddevice backend (ALSA on Pi) | |

**Unverified on hardware (do not assume):**
1. That the real PortAudio/ALSA device name contains `"ReSpeaker"`. The `settings.py` comment already flags this. The XVF3800 may enumerate under a different string (e.g. an XMOS/"reSpeaker XVF3800" USB Audio name) — read it from the device list below and update `DEVICE_NAME_HINT` if needed.
2. That the device accepts `channels=1` at 16 kHz. If it exposes only a multi-channel format, `stream_start_failed` will appear in the log; the fix is then a channel-selection decision (which channel/processed output to use), to be made with the audio hardware engineer, not guessed.
3. That the dropped-block path stays unused at 1024-frame blocks on a Pi 5 under load.

## Code changes

- `audio/microphone.py` — `start()`: if the stream object is constructed but `stream.start()` raises, the stream is now closed and cleared before re-raising. Previously the half-started stream stayed open (found by `test_stream_start_failure_releases_the_stream`, which failed before the fix). No behaviour change on success.
- New `tests/test_audio_failures.py` (5 mocked failure tests + 1 hardware-gated test: live frames -> existing `Reframer(1280)`).
- No Week 1/2 tests changed or removed. No new capture implementation.

Final suite: **216 passed, 8 skipped, 0 failed** (skips: 4 hardware-gated incl. the new one, 4 openWakeWord import-blocked).

## Procedure to run on the Raspberry Pi 5 (to be executed; results go in the table below)

```bash
# 0. Environment record
cat /proc/device-tree/model; cat /etc/os-release | head -3; uname -m; python3 --version
pip list 2>/dev/null | grep -i -E "sounddevice|numpy|onnxruntime|openwakeword"
arecord -l; lsusb | grep -i -E "xmos|respeaker|seeed"

# 1. Device identity as PortAudio sees it (record the exact name, channels, default rate)
python -m sounddevice

# 2. Existing hardware tests + new live Reframer test (should no longer skip)
python -m pytest tests/test_audio_acquisition.py tests/test_audio_failures.py -v -rs

# 3. Several short recordings through the existing demo (repeat 3x)
python record_demo.py 5 week3_a.wav
python -c "import wave;w=wave.open('week3_a.wav');print(w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes()/w.getframerate())"
#    expect 1, 2, 16000, ~5.0 s. Watch the log for event=stream_error (overflow/underflow),
#    event=stream_start_failed, event=read_frame_timeout. Listen to playback.

# 4. Consented recording with the wake phrase, replayed through the EXISTING detector
python record_demo.py 6 week3_wake.wav        # speaker says "hey jarvis" once
python -m wakeword.openwakeword_backend week3_wake.wav
```

Recorded WAVs stay out of git (`*.wav` is ignored). Step 4 is a compatibility smoke test only: report "inference ran / a detection did or did not occur / processing ms per chunk", never a rate.

### Results table (fill in on the Pi)

| Check | Result | Classification |
|---|---|---|
| Pi model / OS / Python / lib versions | | |
| Exact device name, index, channels, rate | | |
| `DEVICE_NAME_HINT` matches | | |
| 16 kHz mono int16 stream opens | | |
| 3 recordings: duration / WAV properties / no stream errors | | |
| Live frames -> Reframer contract test | | |
| Wake-phrase clip: inference ran / detected / ms per chunk | | |

## Problems found this run

1. Windows Application Control blocks `sklearn/_loss/_loss.cp313-win_amd64.pyd`, so the real openWakeWord engine cannot be imported on this dev PC today (4 tests skipped). Environment issue, details above; not a code regression.
2. Resource leak in `ReSpeakerMicrophone.start()` on stream-start failure (fixed, tested with a mock).

## Not tested / refused assumptions

- Everything physical (see verdict table). No Pi output, device name, supported rate or channel layout was assumed or invented.
- Real PortAudio/ALSA error behaviour (permissions, device busy, unplugged mid-stream) — mocks only.
- Wake-word accuracy, false activations, Pi CPU/memory: still NOT MEASURED (Week 14 evaluation).
- Camera, ESP32, sensors, actuators: not part of Task 1; see Task 2 at the end.

---

# Week 3 Task 2 — Camera / ESP32 / Sensor / Actuator Validation

Run date: 2026-10-02, same Windows 11 development PC (Vivobook Go E1504FA) as Task 1. Inspection and documentation only; **no implementation code was changed**.

## Baseline before Task 2

`pytest -q -rs`: **216 passed, 8 skipped, 0 failed** (no warnings reported). Skips: 4 ReSpeaker hardware tests, 4 openWakeWord real-engine tests (Windows Application Control, see Task 1). Identical to the post-Task-1 result.

## What exists in the repository

| Item | Class | Evidence |
|---|---|---|
| Audio input (`AudioSource`, ReSpeaker backend, WAV source, reframer) | A. implemented | `audio/`, tested |
| Wake-word detector + openWakeWord backend | A. implemented | `wakeword/` |
| ESP32 / UART / serial / USB link code | D. absent | no hit in `audio/ config/ wakeword/ evaluation/ tests/ record_demo.py`; `pyserial` not installed or in `requirements.txt` |
| Camera acquisition (CSI / Picamera2 / libcamera / OpenCV) | D. absent | same search; no camera package installed |
| BH1750 / LD2410B / temperature-humidity code | D. absent | same search |
| Actuator / LED / fan / lighting code | D. absent | same search |
| Command / ACK / timeout / simulator abstraction for hardware | D. absent | only the audio `read_frame` timeout exists |
| Message names `SET_LIGHT_ZONE`, `SET_FAN`, `GET_TEMPERATURE`, `GET_HUMIDITY`, `GET_LUX`, `GET_PRESENCE` | C. conceptual only | `CLAUDE.md` ("conceptual only ... NOT a protocol") |
| Phase-8 names `LED_LISTENING`, `GET_LIGHT`, `MUTE`, ... | C. conceptual only | `CLAUDE.md` ("do not finalize protocol syntax") |
| Hardware-team deliverables (ESP32 firmware, protocol spec, wiring) | B/C. referenced, not present | `CLAUDE.md` ownership table; no file in this repository |

Searched for: ESP32, UART, serial, pyserial, camera, CSI, picamera, libcamera, cv2/opencv, BH1750, DHT, LD2410, baud, actuator, lux, fan. No document in the repository other than `CLAUDE.md` mentions an ESP32 or camera interface.

## Hardware availability in this session

Evidence is from this machine only (Windows PnP and COM-port enumeration), not from CLAUDE.md or code.

| Hardware | Classification | Evidence |
|---|---|---|
| Raspberry Pi 5 | NOT AVAILABLE | Host is Windows 11, model "Vivobook Go E1504FA" |
| VESTA classroom camera | NOT AVAILABLE | CLAUDE.md does not specify the camera model or interface. The laptop's built-in "USB2.0 HD UVC WebCam" is present but is not the VESTA camera and was **not opened** |
| ESP32-S3 | NOT AVAILABLE | No COM ports (`GetPortNames` and `Win32_SerialPort` both empty); no USB device with Espressif 303A, CP210x 10C4, CH340 1A86, FTDI 0403 or XMOS 20B1 VID |
| BH1750 light sensor | NOT AVAILABLE | No ESP32 / I2C path present |
| LD2410B radar | NOT AVAILABLE | No ESP32 / UART path present |
| Temperature / humidity sensor | NOT AVAILABLE | No ESP32 path present |
| Safe actuator / test LED | NOT AVAILABLE | None attached |
| Lighting hardware | NOT AVAILABLE | None attached |

## ESP32 protocol status

ESP32 wire protocol is not yet available in this repository; physical communication cannot be implemented defensibly without hardware-team input.

| Protocol Item | Status | Evidence / Source |
|---|---|---|
| Physical transport | PARTIALLY DEFINED | CLAUDE.md: "UART and/or USB" (undecided which) |
| USB vs UART | UNDEFINED | same line, "and/or" |
| Serial device selection | NOT FOUND | no port name, VID/PID or udev rule anywhere |
| Baud rate | UNDEFINED | CLAUDE.md: "not decided" |
| Byte framing | UNDEFINED | CLAUDE.md: "UART / USB framing ... not decided" |
| Line framing | NOT FOUND | no delimiter or packet format specified |
| Encoding (text / JSON / binary) | NOT FOUND | CLAUDE.md: "packet format ... not decided" |
| Request format | UNDEFINED | no syntax defined |
| Response format | UNDEFINED | no syntax defined |
| Command names | PARTIALLY DEFINED | six conceptual names only; explicitly "NOT a protocol" |
| Argument schema | NOT FOUND | zone id, brightness, fan level encodings not defined |
| Sensor-response format | UNDEFINED | CLAUDE.md lists only *requirements*: sensor identity, validity flag, units |
| Units | PARTIALLY DEFINED | requirement "readings carry units"; no unit list |
| Validity / error representation | PARTIALLY DEFINED | requirement only: "validity / error flag" |
| Success acknowledgement | PARTIALLY DEFINED | requirement only: "every command returns an execution result" |
| Failure acknowledgement | PARTIALLY DEFINED | requirement only: "success / failure / reason" |
| Error codes | UNDEFINED | CLAUDE.md: "error codes ... not decided" |
| Timeout requirements | PARTIALLY DEFINED | requirement "a defined timeout for each request"; no values |
| Request / response correlation | NOT FOUND | no request id or ordering rule |
| Simulator identification | PARTIALLY DEFINED | requirement "a way to tell real hardware from a simulator"; no mechanism |
| Firmware version compatibility | NOT FOUND | no version query or policy |

"PARTIALLY DEFINED" for the last block means CLAUDE.md states what the AI side *needs* (its "contract requirements" table), not what the ESP32 firmware actually does. CONFIRMED: no item.

## Is any implementation change justified now?

**No.** There is no hardware interface in the repository to be defective: no camera, serial, sensor or actuator code exists, and the only real interface (audio) was covered in Task 1. A serial layer, parser, sensor-result type or simulator now would mean inventing the missing protocol and building the Week 9 action loop early. A neutral sensor-result or command-result type becomes defensible only after the hardware team fixes field names and units. Documentation-only progress is the correct result. No dependency (pyserial, OpenCV, Picamera2) was installed.

## Failure-behaviour testing

Not applicable beyond Task 1: device-missing, open failure, timeout, disconnect, malformed response, unsupported command, invalid or missing sensor and actuator failure all need an interface that does not exist. No tests were written for an undefined protocol. The microphone failure tests (`tests/test_audio_failures.py`, SOFTWARE-VALIDATED) are unchanged.

## Task 2 validation table

| Interface | Hardware Available | Existing Implementation | Test Performed | Result | Validation Level | Blocker / Next Action |
|---|---|---|---|---|---|---|
| Camera (VESTA) | No | None | Repository + OS device search only | No camera code; VESTA camera absent; laptop webcam deliberately not used | NOT AVAILABLE | Hardware team names camera model / interface (CSI vs USB); run Procedure A on the Pi |
| ESP32-S3 link | No | None | Repository search, COM-port and USB-VID enumeration | No port, no code, no protocol | NOT AVAILABLE | Hardware team supplies protocol and a board; Procedure B |
| BH1750 (lux) | No | None | None possible | none | NOT AVAILABLE | Needs ESP32 path and protocol; Procedure C |
| LD2410B (presence) | No | None | None possible | none | NOT AVAILABLE | same; Procedure C |
| Temperature / humidity | No | None | None possible | none | NOT AVAILABLE | same; sensor part not named in CLAUDE.md; Procedure C |
| Safe actuator / test LED | No | None | None possible | none | NOT AVAILABLE | Needs a safe low-voltage output and protocol; Procedure D |
| Lighting hardware | No | None | None possible | none | NOT AVAILABLE | Hardware-team owned; mains is out of scope for the AI side |

Nothing in Task 2 is SOFTWARE-VALIDATED, SIMULATED or HARDWARE-VALIDATED.

## Future procedures (not results — to be executed when the hardware exists)

Save terminal output to a text file per procedure with timestamps; keep recorded frames and audio out of git.

### A. Camera (Raspberry Pi 5)
1. Identify: hardware team names the model and interface. CSI: `rpicam-hello --list-cameras`; USB: `v4l2-ctl --list-devices` and `lsusb`.
2. OS sees it: confirm the device node (`/dev/video*` or the libcamera listing); `dmesg | tail` shows no errors; record OS and kernel versions.
3. Open a stream with the library the team picks (record name and version). Open and close three times with no resource errors.
4. Capture one frame; save it outside git; record shape, dtype, channel order.
5. Capture a sequence of about 100 frames with per-frame timestamps.
6. Verify the *actual* resolution and the measured mean frame interval and FPS (not the requested values); note dropped or repeated frames.
7. Failure: open with the camera unplugged or held by another process; confirm an explicit error, not an empty frame.
8. Save the evidence and fill the table. Acquisition only; no detection.

### B. ESP32-S3 link
1. Identify the board; `lsusb` and `ls -l /dev/serial/by-id/`. Record VID:PID and the stable by-id name (prefer it to `/dev/ttyUSB0` / `/dev/ttyACM0`).
2. Obtain the hardware team's written protocol (transport, baud, framing, encoding, commands, responses, error codes, timeouts, firmware-version query). **Do not start without it.**
3. Open the port with exactly the documented parameters.
4. Send the single simplest documented read-only request (a version or ping command, if one exists).
5. Confirm the response matches the documented format; record the request, raw response bytes and parsed result.
6. Measure round-trip latency over at least 20 requests (min, median, max).
7. Test timeout (device silent) and disconnect (unplug mid-request); confirm explicit errors and a clean reopen.
8. Save the transcript and firmware version.

### C. Sensors (through the ESP32 path, after B passes)
1. Send the documented query for one sensor.
2. Check the response identifies the sensor (BH1750, LD2410B, or the actual temperature/humidity part).
3. Check the value is present.
4. Check the unit.
5. Check the validity flag.
6. Repeat at least 20 times; compare against a reference (a lux meter or lamp for BH1750, a person entering and leaving the radar zone for LD2410B, a reference thermometer).
7. If safe, disconnect the sensor or cover the BH1750 and repeat.
8. Confirm the error path reports invalid or missing explicitly and never returns a plausible number.

### D. Safe actuator (low-voltage test LED or LED ring only; no mains)
1. Send one documented safe command (for example, test LED on).
2. Record the interface acknowledgement or result code with the raw response.
3. Observe the physical effect yourself and record it separately; an ACK is not proof of actuation.
4. Record latency from send to ACK and from send to visible effect (video or a second observer).
5. Send the documented inverse or reset command and confirm the state returns.
6. Test failure safely: an unsupported command, and a command with the device disconnected; confirm explicit failure results.
Fill three separate columns: command accepted, acknowledgement received, physical effect observed.

## Concrete hardware-team dependencies

1. Wire protocol document for the ESP32-S3 link: transport (UART or USB), baud, framing, encoding, request and response schemas, commands with arguments, error codes, timeouts, request/response correlation, firmware-version query.
2. How real hardware is distinguished from a simulator.
3. Unit and encoding conventions for lux, temperature, humidity and presence, plus the validity/error field for each sensor.
4. Name, model and interface (CSI or USB) of the classroom camera, and test access to it.
5. A safe low-voltage actuator (test LED or LED ring) with a documented command, for Procedure D.
6. Access to the Raspberry Pi 5, ReSpeaker XVF3800, camera and an ESP32-S3 with current firmware.

## Task 2 conclusions

No ESP32 protocol, sensor value or hardware result was invented; no simulator is described as hardware; no dependency was installed; no vision, fusion, VAD/STT, router, decision-engine or TTS/LLM code was written. Regression result after Task 2 (no code changed): 216 passed, 8 skipped, 0 failed, same as the baseline above.

**Week 3 status after Task 2 (superseded by the Closeout section below): IN PROGRESS.** The roadmap's acceptance criterion (each interface marked HARDWARE-VALIDATED, FAILED or NOT AVAILABLE with evidence) is met only as NOT AVAILABLE for every interface. Week 3 is not complete until the physical interfaces are exercised.

---

# Week 3 Closeout (2026-10-02)

## Evidence audit

This closeout re-read the whole report against the repository. Corrections made: the Tier 1 openWakeWord adapter test count was stated as 30 and is **17** (`pytest tests/test_openwakeword_backend.py`: 17 passed, 4 skipped); the title and the openWakeWord row of the Task 1 verdict table were reworded so they do not imply a physical audio validation. No other statement needed correction. Checked and confirmed: mocked tests are labelled SOFTWARE-VALIDATED and never physical; the openWakeWord policy issue is documented as an environment inability, not a VESTA regression; no wake-word accuracy is claimed (NOT MEASURED); no ReSpeaker compatibility, camera, ESP32, sensor or actuator result is claimed; the `SET_*` / `GET_*` names are described as conceptual only.

Test result at closeout: **216 passed, 8 skipped, 0 failed** (skips: 4 ReSpeaker hardware-gated, 4 openWakeWord real-engine blocked by Windows Application Control).

## Acceptance matrix

Roadmap rule: each interface is classified HARDWARE-VALIDATED, FAILED or NOT AVAILABLE, with evidence. No interface was physically exercised, so every physical interface is NOT AVAILABLE.

| Week 3 item | Expected activity | Result | Evidence | Classification | Remaining future action |
|---|---|---|---|---|---|
| 1. Raspberry Pi environment | Record Pi model, OS, Python, library versions | Host is Windows 11, not a Pi | Task 1 environment section; PnP/OS query | NOT AVAILABLE | Procedure step 0 in Task 1 |
| 2. ReSpeaker detection / recording | Detect device, record, check WAV | No ReSpeaker; built-in microphones only; `find_device` fails explicitly. Failure handling tested with mocks | Task 1 verdict table; `tests/test_audio_failures.py` | NOT AVAILABLE (hardware). Failure paths SOFTWARE-VALIDATED | Task 1 Pi procedure, steps 1-3 |
| 3. Camera detection / frame capture | Enumerate camera, capture a frame | No VESTA camera; no camera code; laptop webcam not used | Task 2 hardware table and repository search | NOT AVAILABLE | Procedure A |
| 4. ESP32 <-> Pi communication | Open link, request/response | No ESP32, no COM port, no protocol | Task 2 enumeration and protocol matrix | NOT AVAILABLE | Procedure B, after hardware-team protocol |
| 5. Sensor-data interface | Query BH1750, LD2410B, temp/humidity | No sensors, no path, no response format | Task 2 | NOT AVAILABLE | Procedure C |
| 6. Lighting / actuator command interface | One safe command, ACK, observed effect | No actuator, no command protocol | Task 2 | NOT AVAILABLE | Procedure D |
| 7. Exact device / channel / sample-rate discovery | Record the real configuration | Not discovered. Only software assumptions are known (16 kHz, mono, int16, name hint "ReSpeaker"); all flagged unverified | Task 1 "Existing code facts" | NOT AVAILABLE | Read the real values on the Pi; update `config/settings.py` if they differ |
| 8. Interface-contract documentation | Document the contracts | Done as a gap analysis and requirements handoff. No real contract exists to document | Task 2 protocol matrix; Handoff section below | DOCUMENTED (requirements only; real contract NOT AVAILABLE) | Hardware team supplies the protocol |

## Final status

- **Week 3 — COMPLETE (validation phase).** The planned inspection and validation exercise was executed, every interface has an evidence-backed outcome (all legitimately NOT AVAILABLE), and future procedures exist for each.
- **Physical hardware validation: PENDING.** No physical interface was exercised. Raspberry Pi + ReSpeaker, camera and ESP32 hardware validation all remain PENDING. This is not equivalent to HARDWARE-VALIDATED.

Design decisions behind this result: no speculative hardware interface was built (no protocol exists to build it against); no sensor values were invented; the Week 9 action loop was not started early.

## Hardware-team interface-requirements handoff

This is a list of decisions the AI side needs from the hardware team before ESP32 integration. It is **not** a protocol, and no value below has been decided on their behalf.

| # | Decision needed | Notes (AI-side need, from `CLAUDE.md` contract requirements) |
|---|---|---|
| 1 | Transport: UART, USB serial, or another mechanism | Currently "UART and/or USB" |
| 2 | Connection parameters: how the Pi identifies the device (stable name / VID:PID), and the baud rate if applicable | A stable identifier is preferred over a numeric port |
| 3 | Framing and encoding: byte or line framing, text or binary | |
| 4 | Request format | |
| 5 | Response format | |
| 6 | Command vocabulary and arguments (lighting zones, fan level, sensor queries) | The conceptual names in `CLAUDE.md` are placeholders only |
| 7 | Sensor-response schema | Must identify the sensor and carry a validity flag |
| 8 | Units: lux, temperature, humidity, and how presence is encoded | Readings must carry units |
| 9 | Validity / error representation per sensor | Failed sensors must be reportable, never replaced by a value |
| 10 | Command-result semantics: success, failure, reason | The Response Manager must not confirm an action that did not happen |
| 11 | Timeout requirements per request | |
| 12 | Request / response correlation, if needed | |
| 13 | Firmware-version identification | |
| 14 | How real hardware is distinguished from a simulator or test backend | Keeps SOFTWARE-VALIDATED / SIMULATED / HARDWARE-VALIDATED separable |
| 15 | A safe, low-voltage test command for first integration | e.g. a test LED, not mains equipment |

## AI-side interface decision

**Should an AI-side hardware abstraction be implemented now? No.** There is no real protocol, no ESP32 hardware, and the AI-to-hardware action loop is planned for Week 9. Classes or parsers written now would encode guessed field names, units and error semantics, which is the failure the project's rules warn against, and would need reworking once the hardware team decides. The requirements in `CLAUDE.md` (structured command, execution result, sensor response with validity and units, timeout, real-versus-simulated flag) are sufficient as a statement of need. The abstraction should be written at the start of Week 9, against the real protocol, with a simulator behind the same interface.

## FYP evidence index

- **Architecture:** AI versus hardware responsibility boundary and the protocol-neutral interface principle (`CLAUDE.md`, "AI <-> HARDWARE INTERFACE"); the requirements-only handoff above.
- **Implementation:** `audio/microphone.py` stream cleanup fix in `start()`; `tests/test_audio_failures.py`.
- **Tests:** baseline 211 passed / 7 skipped before changes; 216 / 8 after; skip reasons recorded; Windows Code Integrity diagnosis of the openWakeWord skips.
- **Hardware:** explicit absence of Pi, ReSpeaker, camera, ESP32, sensors and actuators, with the device-search evidence; absence of any wire protocol.
- **Decisions:** no speculative hardware interface, no fake sensor values, no early Week 9 work.
- **Limitations:** no physical Pi/ReSpeaker, camera, ESP32, sensors or actuator were available; wake-word accuracy NOT MEASURED.
- **Future validation:** the Pi/ReSpeaker procedure and Procedures A to D.
- No screenshots, measurements or physical results exist or are claimed.
