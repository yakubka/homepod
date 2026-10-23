# ESP32 wire protocol, v1

The serial link between the Pi bridge and the ESP32. The bridge is the only process on the Pi that
opens the port. Message shapes are pinned by the schemas in `schemas/`, this document pins framing,
timing and failure behaviour. Every JSON block below is validated by `validate.py`.

## 1. Link and framing

| Item | Value |
|------|-------|
| Physical link | USB serial, ESP32 USB-UART bridge, `/dev/ttyUSB0` on the Pi (udev symlink `/dev/room-esp32`) |
| Settings | 115200 baud, 8 data bits, no parity, 1 stop bit, no flow control |
| Framing | One JSON object per line, UTF-8, terminated by `\n`. The device also accepts `\r\n`. |
| Line length | At most 512 bytes including the terminator. Longer lines are dropped by the receiver. |
| Formatting | Compact JSON, no pretty printing, no BOM, no comments. |
| Unknown fields | Ignored by the receiver. The sender never sends fields that are not in the schema. |
| Missing fields | Every field in the schema is sent, there are no device side defaults. |

## 2. Fields on every message

| Field | Direction | Rule |
|-------|-----------|------|
| `type` | both | Message type, one of the sections below. |
| `seq` | both | Per-direction frame counter. Starts at 1 when the link opens (device: after boot, bridge: after opening the port), increases by 1 per frame, wraps from 4294967295 to 1. A receiver logs gaps but never asks for retransmission. Synthetic acks created by the bridge have no `seq`. |
| `ts` | both | ISO 8601 UTC with `Z`. The bridge stamps the write time just before sending. The device sets its clock from the `ts` of any downlink frame when it is unsynced or off by more than 1 s. Until then it sends `1970-01-01T00:00:00.000Z` plus its uptime. The bridge never trusts device time for bus timestamps, it uses its own receive time. |
| `cmd_id` | downlink, echoed in `ack` | Created by the engine, unique per link for 24 hours, 1 to 16 characters `[A-Za-z0-9_-]`. A retry reuses the same `cmd_id` with a new `seq` and `ts`. |

## 3. Session lifecycle

1. The bridge opens the port. The ESP32 board resets on open and sends `hello` with `seq` 1.
2. If no `hello` arrives within 3 s the bridge sends `query` with `what` `info`. It repeats this every 3 s. Uplink frames other than `hello` are dropped until a `hello` is received, because the device id is unknown.
3. On `hello` the bridge compares `protocol_version` MAJOR with `contracts/VERSION`. On mismatch it publishes `device.status` with status `down`, reason `protocol_mismatch`, and sends nothing but `query info` until the firmware is fixed.
4. On a matching `hello` the bridge sends `ping` immediately, which also syncs the device clock, and publishes `device.status` with status `ok`.
5. A `hello` whose `uptime_ms` is lower than the last known uptime means the device rebooted. The engine reacts to the `device.status` change by re-issuing the full desired state, every zone, fan and ring, as one decision with rule `device_resync`.

## 4. Command handling on the device

- Lines are processed strictly in order and answered in the same order.
- Every downlink frame that parses as JSON and carries a `cmd_id` is answered with exactly one `ack` within 200 ms of receiving the terminator. The ack is sent when the command is accepted, a running fade does not delay it.
- Validation failures are answered with `ack` status `rejected`: `invalid_field` for a missing field or wrong type, `out_of_range` for a value outside its range, `unknown_type` for an unknown `type`, `unsupported` for hardware that is not fitted, `busy` when the device queue is full, `hardware_fault` when the actuator failed.
- A line that is not valid JSON, or has no `cmd_id`, is answered with an `error` frame, `parse_error` or `line_too_long`. The bridge treats it as a missing ack and retries.
- The device remembers the last 16 `cmd_id` values and their acks. A repeated `cmd_id` is not executed again, the stored ack is sent again with a new `seq` and `ts`. This makes retries idempotent.
- Every ack carries `state`, the actuator targets after the command, so the Pi always learns the real device state.

## 5. Timing, retries and failures on the Pi

| Rule | Value |
|------|-------|
| Commands in flight | 1, stop and wait. The bridge queues further commands in order, at most 32. |
| Ack timeout | 500 ms after the line was written. |
| Retries | 2 retries, 3 attempts in total, same `cmd_id`, new `seq` and `ts`. Pings are never retried. |
| Worst case for one command | 1.5 s from first write to the final verdict. |
| Heartbeat | `ping` whenever no downlink frame was written for 5 s. |
| Link `degraded` | 2 consecutive commands timed out, reason `ack_timeouts`. Back to `ok` on the next applied ack. |
| Link `down` | No uplink frame for 15 s, reason `silent`, or a serial error, reason `port_closed`. Sensor reports every 2 s keep a healthy link chatty. |
| Reconnect | After `port_closed` the bridge reopens the port every 2 s and restarts at step 1 of the lifecycle. |

**When an ack never arrives.** After the third attempt times out the bridge:

1. publishes a synthetic `device.ack` with `status` `timeout`, `reason` `no_response`, no `seq`, `state` null,
2. counts the timeout towards `degraded` and publishes `device.status` if the link status changed,
3. moves on to the next queued command, the timed out command is never sent again.

The engine then marks the affected zone or fan `confirmed: false` and keeps the commanded value as the
desired state. It re-sends the desired state when the link returns to `ok`. An HTTP call waiting on the
command answers `504 device_timeout`.

**When the link is down.** The bridge does not write to the port. Every queued and every new command
is answered at once with a synthetic ack `status` `rejected`, `reason` `link_down`. The engine stops
issuing commands until `device.status` is `ok` again, and the API answers actuator calls with
`503 device_unavailable` without waiting. A full queue is answered the same way with `reason` `busy`.

## 6. Downlink messages, Pi to ESP32

### `set_zone_light`

Fades one zone to a brightness. `fade_ms` 0 is an instant change. A new command for the same zone
replaces a fade in progress, starting from the current output.

<!-- schema: DeviceCommand -->
```json
{"type":"set_zone_light","seq":4410,"ts":"2026-10-13T08:55:02.230Z","cmd_id":"c00a41f2e","zone":"left","brightness":80,"fade_ms":400}
```

### `set_ring`

Sets the LED ring animation used as voice feedback. `color` null uses the pattern default.
`success` and `error` play once and fall back to `idle` on the device without a further command.

<!-- schema: DeviceCommand -->
```json
{"type":"set_ring","seq":4411,"ts":"2026-10-13T09:30:10.020Z","cmd_id":"c00a41f2f","pattern":"listening","color":"#2D7FF9","brightness":60}
```

### `set_fan`

Sets the fan duty cycle. Below the fan's stall threshold the firmware outputs 0.

<!-- schema: DeviceCommand -->
```json
{"type":"set_fan","seq":4412,"ts":"2026-10-13T09:31:00.000Z","cmd_id":"c00a41f30","speed":60}
```

### `query`

Asks for data. The ack is always sent first. `sensors` is followed by a `sensor_report` with trigger
`query`, `state` needs nothing beyond the ack because every ack carries `state`, `info` is followed by
a `hello`.

<!-- schema: DeviceCommand -->
```json
{"type":"query","seq":4413,"ts":"2026-10-13T09:31:05.000Z","cmd_id":"c00a41f31","what":"sensors"}
```

### `ping`

Heartbeat and clock sync. Answered with an `applied` ack.

<!-- schema: DeviceCommand -->
```json
{"type":"ping","seq":4414,"ts":"2026-10-13T09:31:10.000Z","cmd_id":"c00a41f32"}
```

## 7. Uplink messages, ESP32 to Pi

### `hello`

First frame after boot, and the answer to `query info`.

<!-- schema: DeviceHello -->
```json
{"type":"hello","seq":1,"ts":"1970-01-01T00:00:01.204Z","device_id":"esp32-a1b2c3","fw_version":"0.4.0","protocol_version":"1.0.0","clock_synced":false,"uptime_ms":1204,"reset_reason":"power_on","report_interval_ms":2000,"capabilities":{"light_zones":["left","center","right","professor"],"presence_zones":["left","center","right","professor"],"fan":true,"ring":true,"sensors":["temperature","humidity","lux"]}}
```

### `sensor_report`

Sent every `report_interval_ms`, on every PIR edge but at most 5 per second, and after `query sensors`.
Temperature is rounded to one decimal on the device. A failed sensor reports null and the device also
sends one `error` with `sensor_fault`.

<!-- schema: SensorReport -->
```json
{"type":"sensor_report","seq":812,"ts":"2026-10-13T08:55:01.498Z","uptime_ms":3605112,"trigger":"presence_change","temperature_c":23.4,"humidity_pct":41,"lux":140,"presence":{"left":true,"center":false,"right":false,"professor":false}}
```

The bridge turns each report into one `sensor.environment` and one `sensor.presence` bus message,
stamped with the Pi receive time.

### `ack`

Answer to every downlink command, see section 4.

<!-- schema: DeviceAck -->
```json
{"type":"ack","seq":813,"ts":"2026-10-13T08:55:02.251Z","cmd_id":"c00a41f2e","status":"applied","reason":null,"state":{"lights":{"left":80,"center":0,"right":0,"professor":0},"fan":0,"ring":"idle"}}
```

A rejected command still reports the state the device holds:

<!-- schema: DeviceAck -->
```json
{"type":"ack","seq":902,"ts":"2026-10-13T09:31:00.040Z","cmd_id":"c00a41f30","status":"rejected","reason":"hardware_fault","state":{"lights":{"left":80,"center":80,"right":80,"professor":100},"fan":0,"ring":"idle"}}
```

Synthetic ack published by the bridge after three silent attempts, never sent on the wire:

<!-- schema: DeviceAck -->
```json
{"type":"ack","ts":"2026-10-13T08:55:03.740Z","cmd_id":"c00a41f2e","status":"timeout","reason":"no_response","state":null}
```

### `error`

Unsolicited fault that cannot be reported in an ack. The bridge records it in `device.status`
`last_error`.

<!-- schema: DeviceError -->
```json
{"type":"error","seq":845,"ts":"2026-10-13T09:02:44.010Z","code":"sensor_fault","message":"SHT31 not responding on I2C","ref_seq":null,"cmd_id":null}
```

## 8. Firmware notes

- Parse with ArduinoJson into a 512 byte document, reject before touching any output.
- Read the port without blocking. Keep the PWM fades, PIR sampling and sensor reads on timers so an
  ack is never delayed by a sensor read.
- Enable the task watchdog. A watchdog reset shows up as `reset_reason` `watchdog` in the next hello.
- Build the firmware against a tagged contract version and put it in `protocol_version`.
