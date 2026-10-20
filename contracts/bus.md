# Internal event bus, v1

How the processes on the Pi talk to each other. Payload shapes are pinned by `schemas/`, the envelope
by `schemas/BusEnvelope.json`, which also fixes the payload schema and the publisher of every topic.
Every JSON block below is validated by `validate.py`.

## 1. Transport

| Item | Value |
|------|-------|
| Broker | Mosquitto on the Pi, `127.0.0.1:1883`, MQTT 3.1.1, bound to localhost, no authentication |
| Topic names | `room/v1/` plus the logical name with dots replaced by slashes. `sensor.environment` is `room/v1/sensor/environment`. |
| Payload | One `BusEnvelope` as compact UTF-8 JSON, at most 64 KiB |
| Client id | The component name: `api`, `engine`, `voice`, `vision`, `bridge`, `recorder` |
| Keepalive | 10 s. Every client sets a last will on `room/v1/system/heartbeat` with its own `Heartbeat`, status `down`, so a crash is visible within 15 s. |
| Delivery | QoS 1 topics are at least once. Consumers drop duplicates by envelope `id`, keeping the last 1000 ids. |
| Ordering | Per topic and publisher only. Consumers that join topics order by envelope `ts`. |

## 2. Envelope

| Field | Rule |
|-------|------|
| `id` | `evt_` plus at least 10 random lowercase base36 characters, unique per message. Re-publishing the same message keeps the id. |
| `topic` | Logical topic name, equal to the MQTT topic it is published on. |
| `ts` | Publish time on the Pi clock, ISO 8601 UTC with `Z`. All Pi processes share one NTP synced clock. |
| `source` | Publishing component. Must match the publisher column below. |
| `contract_version` | Contents of `contracts/VERSION` the publisher was built against. |
| `correlation_id` | The `request_id` for everything caused by a voice or app request. Otherwise the decision `id` from `decision.action` onward. Null for raw sensor, perception, status and schedule events. |
| `payload` | Topic payload, schema in the table below. |

## 3. Topics

| Topic | Payload | Publisher | Subscribers | Rate | QoS | Retained |
|-------|---------|-----------|-------------|------|-----|----------|
| `sensor.environment` | EnvironmentReading | bridge | engine, recorder, api | every sensor_report, 0.5 Hz, up to 5 Hz on PIR bursts | 0 | no |
| `sensor.presence` | PresenceReading | bridge | engine, api | every sensor_report, as above | 0 | no |
| `perception.occupancy` | OccupancySnapshot | vision | engine, recorder, api | 1 Hz, plus at once on a count change, at most 2 Hz | 0 | no |
| `voice.wake` | VoiceWake | voice | engine, api | per wake word, at most 1 per 2 s | 1 | no |
| `voice.transcript` | VoiceTranscript | voice, api | voice, recorder, api | per utterance or POST /voice/ask | 1 | no |
| `voice.intent` | VoiceCommand | voice | engine, api | per transcript, within 1 s of it | 1 | no |
| `control.request` | ControlRequest | api | engine | per actuator HTTP call | 1 | no |
| `decision.action` | Decision | engine | api, voice, recorder | per decision, typically under 1 Hz, bursts up to 10 Hz | 1 | no |
| `device.command` | DeviceCommand without `seq` | engine | bridge | per command, at most 20 Hz | 1 | no |
| `device.ack` | DeviceAck | bridge | engine, api | exactly one per device.command | 1 | no |
| `device.status` | DeviceLink | bridge | engine, api | on change and every 5 s | 1 | yes |
| `schedule.lecture` | LectureEvent | api | engine | per lecture change, start and end | 1 | no |
| `settings.changed` | Settings | api | engine, voice | at API start and per PATCH /settings | 1 | yes |
| `system.heartbeat` | Heartbeat | every component | api | every 5 s per component | 0 | no |
| `system.state` | SystemState | engine | api | on change at most 2 Hz, and every 5 s | 1 | yes |

`api` subscribes to every topic it relays over `GET /events`. Only the publisher listed may publish a
topic. The engine is the only component that issues `device.command`, and every command belongs to
exactly one `decision.action`.

### `sensor.environment`

Bridge output of every `sensor_report`. `ts` is the Pi receive time.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_2h7k1m9p4q","topic":"sensor.environment","ts":"2026-10-13T08:55:01.515Z","source":"bridge","contract_version":"1.0.0","correlation_id":null,
 "payload":{"ts":"2026-10-13T08:55:01.512Z","device_id":"esp32-a1b2c3","temperature_c":23.4,"humidity_pct":41,"lux":140}}
```

### `sensor.presence`

PIR state of every fitted zone, from the same `sensor_report`.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_8n3b5v7c1x","topic":"sensor.presence","ts":"2026-10-13T08:55:01.515Z","source":"bridge","contract_version":"1.0.0","correlation_id":null,
 "payload":{"ts":"2026-10-13T08:55:01.512Z","device_id":"esp32-a1b2c3","zones":[{"zone":"left","present":true},{"zone":"center","present":false},{"zone":"right","present":false},{"zone":"professor","present":false}]}}
```

### `perception.occupancy`

People count per zone from the camera. Vision sets `stale` when its newest frame is older than 5 s,
for example while the camera reconnects. The engine never dims a zone on stale data.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_9k2j4h1m0p","topic":"perception.occupancy","ts":"2026-10-13T08:55:02.180Z","source":"vision","contract_version":"1.0.0","correlation_id":null,
 "payload":{"ts":"2026-10-13T08:55:02.140Z","total":1,"occupied":true,"zones":[{"zone":"left","count":1,"occupied":true,"confidence":0.9},{"zone":"center","count":0,"occupied":false,"confidence":0.95},{"zone":"right","count":0,"occupied":false,"confidence":0.96},{"zone":"professor","count":0,"occupied":false,"confidence":0.97}],"source":"vision","confidence":0.9,"stale":false,"frame_id":18342}}
```

### `voice.wake`

Wake word detected. Creates the `request_id` used by the rest of the request. The engine answers with
a decision `ring.set` `listening`.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_4r6t8y0u2i","topic":"voice.wake","ts":"2026-10-13T09:30:10.006Z","source":"voice","contract_version":"1.0.0","correlation_id":"req_8d1f6g3h2j",
 "payload":{"request_id":"req_8d1f6g3h2j","ts":"2026-10-13T09:30:10.004Z","wake_word":"hey_room","confidence":0.88}}
```

### `voice.transcript`

Final transcript of one utterance. The API also publishes one with `source` `app` for each
`POST /voice/ask`, and the voice parser handles both the same way. The voice stack publishes it within
1.5 s of the end of speech, or an empty `text` when nothing was understood.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_1o3p5a7s9d","topic":"voice.transcript","ts":"2026-10-13T09:30:12.390Z","source":"voice","contract_version":"1.0.0","correlation_id":"req_8d1f6g3h2j",
 "payload":{"request_id":"req_8d1f6g3h2j","ts":"2026-10-13T09:30:12.380Z","text":"set the left lights to forty percent","confidence":0.91,"language":"en-US","source":"microphone","duration_ms":2140}}
```

### `voice.intent`

Parsed request for the engine, published within 1 s of the transcript. The intent set is closed for
this sprint and every intent fixes `action` and `target.type`:

| intent | action | target.type | target.zone | required params |
|--------|--------|-------------|-------------|-----------------|
| `lights_on` | `set` | `lighting` | zone or null for all | none |
| `lights_off` | `set` | `lighting` | zone or null for all | none |
| `set_brightness` | `set` | `lighting` | zone or null for all | `brightness` |
| `query_temperature` | `query` | `climate` | null | none |
| `query_humidity` | `query` | `climate` | null | none |
| `query_occupancy` | `query` | `occupancy` | zone or null for the room | none |
| `query_lecture` | `query` | `lecture` | null | none |
| `start_presentation` | `start` | `presentation` | null | none |
| `stop_presentation` | `stop` | `presentation` | null | none |
| `play_announcement` | `play` | `announcement` | null | `announcement_id` |
| `unknown` | `none` | `none` | null | none |

`lights_on` means brightness 100 and `lights_off` brightness 0, both switch lighting to manual.
Intents below `Settings.voice.min_confidence` are published as `unknown`.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_6f8g0h2j4k","topic":"voice.intent","ts":"2026-10-13T09:30:12.610Z","source":"voice","contract_version":"1.0.0","correlation_id":"req_8d1f6g3h2j",
 "payload":{"request_id":"req_8d1f6g3h2j","intent":"set_brightness","action":"set","target":{"type":"lighting","zone":"left"},"params":{"brightness":40},"confidence":0.93,"raw_text":"set the left lights to forty percent","source":"microphone"}}
```

### `control.request`

State change requested over HTTP. The API fills every default before publishing. Allowed action types
and their required params:

| action.type | from endpoint | required params |
|-------------|---------------|-----------------|
| `lighting.set_mode` | POST /lighting/mode | `mode`, `fade_ms` |
| `lighting.set_zone` | POST /lighting/zone | `brightness`, `fade_ms`, and a zone |
| `climate.set_target` | POST /climate/target | `target_c` |
| `presentation.start` | POST /presentation | none, `lecture_id` optional |
| `presentation.stop` | POST /presentation | none |
| `announcement.play` | POST /announcements/{id}/play | `announcement_id`, `volume` |

`climate.set_fan`, `ring.set`, `answer` and `none` are engine internal and rejected on this topic.
The API waits up to 1 s for the `decision.action` with the same `request_id`, then for every
`device.ack` of the decision's commands, and answers the HTTP call.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_7z9x1c3v5b","topic":"control.request","ts":"2026-10-13T09:05:00.012Z","source":"api","contract_version":"1.0.0","correlation_id":"req_3q7w1e5r9t",
 "payload":{"request_id":"req_3q7w1e5r9t","ts":"2026-10-13T09:05:00.010Z","action":{"type":"lighting.set_zone","zone":"professor","params":{"brightness":100,"fade_ms":400}}}}
```

### `decision.action`

Every engine decision, published once when made with command statuses `pending`. Consumers that need
delivery results follow `device.ack` by `cmd_id`. The voice stack speaks `reply_text` for its own
microphone requests and plays announcements on `announcement.play`.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_3c6v9b2n5m","topic":"decision.action","ts":"2026-10-13T08:55:02.210Z","source":"engine","contract_version":"1.0.0","correlation_id":"dec_01h9x4k2pq",
 "payload":{"id":"dec_01h9x4k2pq","ts":"2026-10-13T08:55:02.205Z","trigger":{"type":"occupancy","topic":"perception.occupancy","event_id":"evt_9k2j4h1m0p"},"rule":"occupancy_lights_on","action":{"type":"lighting.set_zone","zone":"left","params":{"brightness":80,"fade_ms":400}},"outcome":"issued","summary":"Left zone became occupied and ambient light is 140 lux, below the 300 lux target.","commands":[{"cmd_id":"c00a41f2e","type":"set_zone_light","status":"pending"}],"request_id":null,"reply_text":null}}
```

### `device.command`

A downlink message for the ESP32 without `seq`. The bridge adds `seq`, overwrites `ts` with the write
time and sends it. `correlation_id` carries the request or decision id so the chain stays traceable.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_0q2w4e6r8t","topic":"device.command","ts":"2026-10-13T08:55:02.214Z","source":"engine","contract_version":"1.0.0","correlation_id":"dec_01h9x4k2pq",
 "payload":{"type":"set_zone_light","ts":"2026-10-13T08:55:02.212Z","cmd_id":"c00a41f2e","zone":"left","brightness":80,"fade_ms":400}}
```

### `device.ack`

The device ack, republished unchanged, or a synthetic ack from the bridge. Exactly one per command,
at most 1.5 s after the command reached the bridge.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_5f8g2h6j3k","topic":"device.ack","ts":"2026-10-13T08:55:02.262Z","source":"bridge","contract_version":"1.0.0","correlation_id":"dec_01h9x4k2pq",
 "payload":{"type":"ack","seq":813,"ts":"2026-10-13T08:55:02.251Z","cmd_id":"c00a41f2e","status":"applied","reason":null,"state":{"lights":{"left":80,"center":0,"right":0,"professor":0},"fan":0,"ring":"idle"}}}
```

### `device.status`

Serial link state. Retained, so a component that starts late learns it at once. On a change to `ok`
after a reboot or a down period, the engine re-issues the full desired state.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_9l1k3j5h7g","topic":"device.status","ts":"2026-10-13T09:01:27.400Z","source":"bridge","contract_version":"1.0.0","correlation_id":null,
 "payload":{"device_id":"esp32-a1b2c3","status":"down","reason":"silent","fw_version":"0.4.0","protocol_version":"1.0.0","uptime_ms":3700020,"last_seen_at":"2026-10-13T09:01:12.400Z","last_error":null}}
```

### `schedule.lecture`

Lecture changes from the API. The API scheduler emits `started` and `ended` within 1 s of `starts_at`
and `ends_at`. On `started` the engine applies the lecture `lighting_mode` and the default climate
target.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_2s4d6f8g0h","topic":"schedule.lecture","ts":"2026-10-13T09:00:00.310Z","source":"api","contract_version":"1.0.0","correlation_id":null,
 "payload":{"event":"started","ts":"2026-10-13T09:00:00.300Z","lecture":{"id":"lec_7f3k2m9q1x","title":"Distributed Systems, week 7","course_code":"CS301","lecturer":"Dr. Karimova","starts_at":"2026-10-13T09:00:00.000Z","ends_at":"2026-10-13T10:30:00.000Z","status":"in_progress","lighting_mode":"presentation","notes":null,"created_at":"2026-10-10T14:02:11.000Z","updated_at":"2026-10-10T14:02:11.000Z"}}}
```

### `settings.changed`

Full settings after every change, retained. Consumers replace their copy, they never merge.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_4j6k8l0z2x","topic":"settings.changed","ts":"2026-10-13T07:30:12.010Z","source":"api","contract_version":"1.0.0","correlation_id":null,
 "payload":{"room_id":"b-204","room_name":"Lecture Hall B-204","timezone":"Asia/Tashkent","lighting":{"target_lux":350,"vacancy_timeout_s":300,"vacant_brightness":20,"daylight_harvesting":true,"presets":{"lecture":{"left":80,"center":80,"right":80,"professor":100},"presentation":{"left":30,"center":10,"right":30,"professor":60}}},"climate":{"default_target_c":22.5,"hysteresis_c":0.5},"voice":{"enabled":true,"wake_word":"hey_room","language":"en-US","min_confidence":0.6},"announcements":{"volume":70},"updated_at":"2026-10-13T07:30:12.000Z"}}
```

### `system.heartbeat`

Every component, every 5 s. `payload.component` equals the envelope `source`. The API derives
`GET /health` from these and from its own broker and database connections.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_6c8v0b2n4m","topic":"system.heartbeat","ts":"2026-10-13T08:55:04.000Z","source":"vision","contract_version":"1.0.0","correlation_id":null,
 "payload":{"component":"vision","status":"degraded","version":"0.2.0","uptime_s":7200,"detail":"Running at 0.5 fps, target is 1 fps."}}
```

### `system.state`

The engine's aggregated view, retained. `GET /state` returns the latest one and the API pushes it to
every new `GET /events` client.

<!-- schema: BusEnvelope -->
```json
{"id":"evt_8m0n2b4v6c","topic":"system.state","ts":"2026-10-13T08:55:02.420Z","source":"engine","contract_version":"1.0.0","correlation_id":null,
 "payload":{"ts":"2026-10-13T08:55:02.420Z","status":"degraded","contract_version":"1.0.0","devices":{"esp32":"ok","camera":"down","microphone":"ok","speaker":"ok"},
  "environment":{"ts":"2026-10-13T08:55:01.512Z","device_id":"esp32-a1b2c3","temperature_c":23.4,"humidity_pct":41,"lux":140},
  "occupancy":{"ts":"2026-10-13T08:55:01.512Z","total":null,"occupied":true,"zones":[{"zone":"left","count":null,"occupied":true,"confidence":0.6},{"zone":"center","count":null,"occupied":false,"confidence":0.6},{"zone":"right","count":null,"occupied":false,"confidence":0.6},{"zone":"professor","count":null,"occupied":false,"confidence":0.6}],"source":"presence","confidence":0.6,"stale":false,"frame_id":null},
  "lighting":{"mode":"auto","zones":[{"zone":"left","brightness":80,"confirmed":true,"updated_at":"2026-10-13T08:55:02.251Z"},{"zone":"center","brightness":0,"confirmed":true,"updated_at":"2026-10-13T08:40:00.000Z"},{"zone":"right","brightness":0,"confirmed":true,"updated_at":"2026-10-13T08:40:00.000Z"},{"zone":"professor","brightness":0,"confirmed":true,"updated_at":"2026-10-13T08:40:00.000Z"}],"ambient_lux":140,"updated_at":"2026-10-13T08:55:02.251Z"},
  "climate":{"mode":"auto","temperature_c":23.4,"humidity_pct":41,"target_c":22.5,"fan_speed":30,"fan_confirmed":true,"updated_at":"2026-10-13T08:50:00.000Z"},
  "presentation":{"active":false,"started_at":null,"started_by":null,"lecture_id":null},
  "lecture":{"at":"2026-10-13T08:55:02.420Z","current":null,"next":null}}}
```

## 4. Request chains

**App request**, for example POST /lighting/zone:
`control.request` (api) → `decision.action` (engine, same `request_id`) → `device.command` per zone
(engine) → `device.ack` per command (bridge) → HTTP response, then `system.state` (engine).

**Voice request**: `voice.wake` → `decision.action` ring listening → `voice.transcript` →
`voice.intent` → `decision.action` with `reply_text` → `device.command` and `device.ack` when the
intent changes a device → the voice stack speaks `reply_text` → `system.state`.

**Sensor rule**: `sensor.presence` or `perception.occupancy` → `decision.action` → `device.command` →
`device.ack` → `system.state`. The full walk-through with every message is in `README.md`.
