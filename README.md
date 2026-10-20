# VESTA

Smart Lecture Room. A Raspberry Pi 5 and an ESP32-S3 watch a lecture room and adapt the lighting,
the climate and the announcements to what is actually happening in it, instead of waiting to be told.

Capstone Design, Group 3, Sejong University. This repository holds the software that runs on the Pi.

## What the room does

The camera counts students per seating zone, the radar confirms presence, the light sensor reports lux,
the climate sensor reports temperature and humidity, and the application supplies the lecture schedule.
A rule based engine combines all of it and decides the brightness of each zone, whether to recommend a
climate change, when to dim the projector area, and when to announce that the professor has not arrived.
Every decision it makes is recorded with the inputs that caused it, so the room can explain itself.

## Status

| Part | State | Owner |
|---|---|---|
| Decision engine, rule framework, conflict resolution | Working, 97 percent covered | Yokubjon, V-10 |
| Seven intelligences, occupancy, lighting, environment, climate, presentation, schedule, announcement | Working | Yokubjon, V-11 |
| Decision log and explainability | Working, in memory, persistence waits on V-33 | Yokubjon, V-12 |
| Eight room scenarios with golden outcomes | Working | Yokubjon, V-13 |
| Package layout, prototype ported | Working | Yokubjon, V-14 |
| Device drivers, GPIO, I2C, camera, ring, display | Carried over from the prototype, simulated, not validated on the board | Yersultan |
| Student counting and zone calibration | The detector counts, the professor zone is not calibrated yet | Ripon, V-61, V-62 |
| REST API | Eleven endpoints on the standard library, to be rebuilt against the contract | Azimjon, V-42 |
| Interface contract, event bus, model runtime | Not started | Javohir, V-01 to V-03 |
| Virtual classroom simulator | Not started | Azimjon, V-40 |
| Voice pipeline, VAD, speech to text, intent | Separate repository, audio and wake word only | Fatima, V-20 to V-26 |
| Text to speech, announcements, persistence | Not started | Shakhboz, V-30 to V-34 |
| Flutter application | Not started | Sufi, V-50 to V-56 |
| Air conditioner control | Built and switched off behind `climate.enabled` | Yokubjon, V-11 |

## Layout

```
main.py                       boots the prototype drivers directly
services/core/__main__.py     python -m services.core, starts the bus, the state store and every service
services/core/bus.py          in-process event bus, topics from contracts/bus.md
services/core/state.py        typed room snapshot with per field staleness
services/core/service.py      start, stop and health, the interface every lane implements
services/core/config.py       layered settings and the VESTA_MODE switch
services/core/mode.py         picks the replay, sim or real implementation of a source
services/core/logs.py         JSON line logging with a trace id per interaction
services/core/models.py       model registry, lazy load with warmup, eviction, pinned threads
services/core/scheduler.py    priority scheduler for heavy inference, voice before vision
services/core/guard.py        memory and latency guard, publishes system.degraded
eval/bench_models.py          model benchmark, report in eval/reports/models.md
docs/budget.md                per model core and memory allowance every lane is accepted against
services/recorder/            keeps bus history
contracts/                    interface contract, schemas and generated models
config/default.yaml           committed defaults, config/local.yaml overrides them per machine
services/core/engine/         the decision engine, pure, no input and no output
services/core/runtime.py      the tick, builds the room state and applies what the engine decides
services/device/              drivers, the only place a device library may be imported
services/vision/              detector and the occupancy loop
services/voice/               intent routing
services/api/                 REST
sim/scenarios/                eight room timelines
sim/golden/                   what the engine must decide for each one
docs/rules.md                 every rule, its inputs and its thresholds
```

## The engine

```python
decide(room_state, settings, now) -> DecisionResult
```

No network, no file reads, no clock inside. The same room state always produces the same decision,
which is what makes every other lane testable without hardware. The result carries the actions to take,
the recommendations a person has to approve, and every decision including the suppressed ones with the
reason they were suppressed.

Rules propose, the engine resolves. Higher priority wins, equal priority takes the brighter value,
a manual override beats everything until it expires, and nothing can be reversed before its hold time
has passed. `docs/rules.md` has the whole table.

## Running

```bash
VESTA_MODE=replay python3 -m services.core
```

Prints one healthy line per service and runs until Ctrl C. `replay` is the default and imports no
device library, so the whole stack runs on a laptop. `sim` adds randomised sensors, `VESTA_MODE=real`
is the only value that touches the hardware. Add `VESTA_STRICT=1` while developing so a payload that
breaks the contract fails the moment it is published. `docs/core.md` shows how a lane plugs in.

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. python3 -m pytest tests/
```

A hundred and twenty one tests. The eight room scenarios replay a two hour lecture in milliseconds and
diff against their golden outcomes, so a changed threshold shows up as a named scenario failing.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/status` | Room state, lights, overrides, occupancy, last decision |
| GET | `/sensors` | Temperature, humidity, ambient light |
| GET | `/occupancy` | Count per zone, source, confidence, age |
| GET | `/lights` | Brightness per zone |
| GET | `/decisions` | The decision log, with `limit`, `since`, `until` and `rule` |
| GET | `/recommendations` | Climate recommendations waiting for approval |
| GET | `/commands` | Available intents |
| POST | `/command` | Run an intent |
| POST | `/lights/zone` | Hold a zone at a brightness |
| POST | `/lights/auto` | Release one zone, or all of them, back to automatic |
| POST | `/presentation` | Start or stop presentation mode |
| POST | `/config` | Change a setting, applied on the next tick |

This is the prototype API. V-42 replaces it with the generated implementation of the contract.

## Hardware

| Component | Part | Interface |
|---|---|---|
| Controller | Raspberry Pi 5 | |
| Real time controller | ESP32-S3 | UART |
| Camera | 5MP CSI | CSI |
| Presence radar | LD2410B 24GHz mmWave | UART |
| Light sensor | BH1750 | I2C `0x23` |
| Climate sensor | DHT | 1-Wire GPIO 4 |
| Microphone array | ReSpeaker XVF3800 | USB |
| Audio amplifier | MAX98357A | I2S |
| Speaker | 8 ohm | |
| LED ring | WS2812B 24 pixels | PWM GPIO 12 |
| Zone lighting | Relays, GPIO 5, 6, 16, 26 | GPIO |

The detector needs the MobileNet SSD weights in `models/`, see `models/README.md`.
