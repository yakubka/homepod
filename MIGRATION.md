# Moving the HomePod prototype into VESTA

V-14. What moved where, and what was deleted. Read this before looking for a file that is not in `src/` any more.

## Moved

| Was | Is now | Owner from here |
|---|---|---|
| `src/config.py` | `services/core/config.py` | Yokubjon |
| `src/hardware.py` | `services/device/hardware.py` | Yersultan |
| `src/camera.py` | `services/device/camera.py` | Yersultan |
| `src/light_sensor.py` | `services/device/light_sensor.py` | Yersultan |
| `src/led_ring.py` | `services/device/led_ring.py` | Yersultan |
| `src/display.py` | `services/device/display.py` | Yersultan |
| `src/servo.py` | `services/device/servo.py` | Yersultan |
| `src/lights.py` | `services/device/lights.py` | Yersultan |
| `src/detector.py` | `services/vision/detector.py` | Ripon, V-61 |
| `src/presence.py` | `services/vision/occupancy.py` | Ripon, V-61 |
| `src/commands.py` | `services/voice/commands.py` | Fatima, V-25 |
| `src/server.py` | `services/api/server.py` | Azimjon, V-42 |
| `src/main.py` | `main.py` | Javohir, V-02 |

`src/` is gone. Nothing imports from it.

## Deleted

- `src/audio.py`, the music player. A lecture room does not play a playlist. The speaker belongs to
  Shakhboz and the announcement path, V-30 to V-32.
- The music, timer and weather intents, and the follow me home lighting semantics. Nine of the
  eighteen old intents are gone and ten lecture room intents replace them, matching the intent set in the contract.
- `Lights.set_follow`, `Lights.update` and the auto off timer. Every one of those decisions now belongs
  to the engine, so the driver only sets a brightness.

## Changed

- **Zones.** Three living room zones became the lecture room seating blocks `left`, `center`, `right`,
  plus a separate `professor` zone. `services/device/lights.py` has a fourth relay on GPIO 26.
- **Brightness, not on and off.** `Lights.set(zone, brightness)` takes 0 to 100. Relays switch at
  anything above zero, so a dimmable bulb drops in without a change above the driver.
- **One mode switch.** The fourteen per module `SIM` booleans are gone. `VESTA_MODE` decides, with
  `replay` the default and `real` the only value that touches a device library.
  Every module reads it from `services.core.config`.
- **Settings.** `config.DEFAULTS` now carries every engine threshold. `load()` reads the file on each
  call, so changing a threshold takes effect on the next tick with no restart.
- **The lighting decision.** `presence.py` used to drive the lamps directly from the camera. That loop
  is now `services/vision/occupancy.py`, which only publishes an occupancy snapshot, and
  `services/core/runtime.py`, which calls the engine and applies what it decides.

## New

| Path | What it is |
|---|---|
| `services/core/engine/` | The decision engine, V-10 to V-12 |
| `services/core/runtime.py` | The tick that builds the room state, calls the engine and applies the actions |
| `sim/scenarios/`, `sim/golden/` | Eight room timelines and their expected outcomes, V-13 |
| `tests/` | 90 tests, including the scenario runner |
| `docs/rules.md` | Every rule, its inputs, its thresholds and its priority |

## Running it

```
VESTA_MODE=replay python3 main.py
PYTHONPATH=. python3 -m pytest tests/
```

`replay` imports no device library at all, which is what lets the other six lanes work without hardware.
