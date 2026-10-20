# Room controller interface contract, v1

The single source of truth that the app, the API, the engine, the voice stack, the vision stack and
the ESP32 agree on. If code and contract disagree, the code is wrong. Current version: see
[`VERSION`](VERSION).

```
 Flutter app ──HTTP + SSE──▶ api ─┐                 ┌─ vision (camera)
                                  │  event bus in   │
            recorder ◀────────────┼── the Pi ───────┼─ voice (mic, speaker)
                                  │  services.core  │
                          engine ─┘                 └─ bridge ──USB serial──▶ ESP32
                                                                 (lights, fan, ring, PIR, sensors)
```

| File | What it pins |
|------|--------------|
| [`openapi.yaml`](openapi.yaml) | HTTP API for the app, 24 endpoints, error model, examples |
| [`schemas/`](schemas) | JSON Schema 2020-12 for every object, with examples |
| [`protocol.md`](protocol.md) | ESP32 serial protocol, framing, timeouts, retries |
| [`bus.md`](bus.md) | Event bus topics, publishers, subscribers, rates |
| [`units.md`](units.md) | Units, ranges and naming, in one table |
| [`CHANGELOG.md`](CHANGELOG.md) | Versioning rules and history |
| [`SIGNOFF.md`](SIGNOFF.md) | Who has read which version |
| [`generated/`](generated) | Python and Dart models generated from the schemas, do not edit |
| [`validate.py`](validate.py), [`generate.py`](generate.py), [`check_generated.py`](check_generated.py) | Contract checks, model generation, model round trips |

## Walkthrough: a person enters the room

08:55, no lecture yet. Lighting is in `auto`, every zone is at 0, ambient light is 140 lux and the
target is 300 lux. A student walks in and sits on the left.

**1. ESP32 to bridge, serial.** The left PIR fires. The ESP32 sends a report at once instead of
waiting for the 2 s interval.

<!-- schema: SensorReport -->
```json
{"type":"sensor_report","seq":812,"ts":"2026-10-13T08:55:01.498Z","uptime_ms":3605112,"trigger":"presence_change","temperature_c":23.4,"humidity_pct":41,"lux":140,"presence":{"left":true,"center":false,"right":false,"professor":false}}
```

**2. Bridge to bus, `sensor.environment` and `sensor.presence`.** The bridge splits the report into
two messages, stamps them with the Pi clock and wraps each in a `BusEnvelope`. The presence payload:

<!-- schema: PresenceReading -->
```json
{"ts":"2026-10-13T08:55:01.512Z","device_id":"esp32-a1b2c3","zones":[{"zone":"left","present":true},{"zone":"center","present":false},{"zone":"right","present":false},{"zone":"professor","present":false}]}
```

The engine notes the motion but does not act yet. While vision is fresh its counts decide, which
avoids PIR false triggers. PIR only decides when vision is stale.

**3. Vision to bus, `perception.occupancy`.** The next frame counts one person on the left.

<!-- schema: OccupancySnapshot -->
```json
{"ts":"2026-10-13T08:55:02.140Z","total":1,"occupied":true,"zones":[{"zone":"left","count":1,"occupied":true,"confidence":0.9},{"zone":"center","count":0,"occupied":false,"confidence":0.95},{"zone":"right","count":0,"occupied":false,"confidence":0.96},{"zone":"professor","count":0,"occupied":false,"confidence":0.97}],"source":"vision","confidence":0.9,"stale":false,"frame_id":18342}
```

**4. Engine to bus, `decision.action`.** The left zone went from vacant to occupied and 140 lux is below
the target, so rule `occupancy_lights_on` fires. The decision names its trigger and lists the command
it is about to send, still `pending`.

<!-- schema: Decision -->
```json
{"id":"dec_01h9x4k2pq","ts":"2026-10-13T08:55:02.205Z","trigger":{"type":"occupancy","topic":"perception.occupancy","event_id":"evt_9k2j4h1m0p"},"rule":"occupancy_lights_on","action":{"type":"lighting.set_zone","zone":"left","params":{"brightness":80,"fade_ms":400}},"outcome":"issued","summary":"Left zone became occupied and ambient light is 140 lux, below the 300 lux target.","commands":[{"cmd_id":"c00a41f2e","type":"set_zone_light","status":"pending"}],"request_id":null,"reply_text":null}
```

**5. Engine to bus, `device.command`.** The command itself, without `seq`. The envelope's
`correlation_id` is `dec_01h9x4k2pq`, and so are those of the ack and state that follow.

<!-- schema: DeviceCommand -->
```json
{"type":"set_zone_light","ts":"2026-10-13T08:55:02.212Z","cmd_id":"c00a41f2e","zone":"left","brightness":80,"fade_ms":400}
```

**6. Bridge to ESP32, serial.** The bridge adds `seq`, restamps `ts` and writes one line.

<!-- schema: DeviceCommand -->
```json
{"type":"set_zone_light","seq":4410,"ts":"2026-10-13T08:55:02.230Z","cmd_id":"c00a41f2e","zone":"left","brightness":80,"fade_ms":400}
```

**7. ESP32 to lamp, and ESP32 to bridge.** The firmware starts a 400 ms PWM fade on the left channel
from 0 to 80 percent. The lamp is changing now. Within 200 ms it answers with the same `cmd_id` and
the state it holds.

<!-- schema: DeviceAck -->
```json
{"type":"ack","seq":813,"ts":"2026-10-13T08:55:02.251Z","cmd_id":"c00a41f2e","status":"applied","reason":null,"state":{"lights":{"left":80,"center":0,"right":0,"professor":0},"fan":0,"ring":"idle"}}
```

**8. Bridge to bus, `device.ack`.** The ack is republished unchanged. Had it not arrived within
500 ms, the bridge would have retried twice with the same `cmd_id`, then published a synthetic
`timeout` ack (see [`protocol.md`](protocol.md) section 5).

**9. Engine to bus, `system.state`.** The engine marks the left zone `brightness` 80,
`confirmed: true` and publishes the new `SystemState`, retained.

**10. API to app.** The app holds `GET /events?topics=system.state,decision.action` open and receives
the decision and the new state as Server-Sent Events. Its lighting card shows the left zone at 80
percent, and the activity feed shows the decision summary. A client that just connected calls
`GET /state` or `GET /lighting` and sees the same values.

About 750 ms from the first motion to the fade start, of which about 650 ms is waiting for the next
camera frame. If the student leaves, vision reports the zone vacant and after `vacancy_timeout_s`,
300 s, a `timer` decision fades the zone back to 0 through the same steps 4 to 10.

## Working with the contract

Requirements: Python 3.10 or newer, Node 18 or newer for Redocly and quicktype, the Dart SDK for the
Dart round-trip check. Install the tools once:

```bash
pip install -r contracts/requirements.txt
```

```bash
npm ci --prefix contracts
```

Then, after every change:

```bash
python contracts/validate.py
```

```bash
npm run lint --prefix contracts
```

```bash
python contracts/generate.py
```

```bash
python contracts/check_generated.py
```

| Script | Does |
|--------|------|
| `validate.py` | Validates every schema, every example in the schemas, in `openapi.yaml` and in these documents, and the cross-file rules. Runs `openapi-spec-validator`. |
| `redocly.yaml` | Lint rules for `npm run lint`, the recommended set with example checks raised to errors. |
| `generate.py` | Rewrites `generated/python` with datamodel-code-generator and `generated/dart` with quicktype. `--check` fails when they are stale. |
| `check_generated.py` | Round-trips every example through the generated Python and Dart models and analyses the Dart package. |

Never edit generated files by hand. Change the schema, regenerate, bump `VERSION`, announce. CI in
`.github/workflows/contracts.yml` runs all of the above and compiles the Dart models in a fresh
`flutter create` project.

Using the generated models:

- Python: `pip install -e contracts/generated/python`, then `from room_contracts import Lecture`.
  Parse with `Lecture.model_validate(data)`, serialise with
  `model_dump(mode="json", by_alias=True, exclude_unset=True)`.
- Dart and Flutter: add `room_contracts: {path: ../contracts/generated/dart}` to `pubspec.yaml`,
  then `import 'package:room_contracts/room_contracts.dart';`. Every type has a pair of functions,
  for example `lectureFromJson(body)` and `lectureToJson(lecture)`. Omitted optional fields are
  left out of the JSON, so request models are safe to send as they are.
