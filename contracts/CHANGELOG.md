# Contract changelog

`contracts/VERSION` holds the current version. `openapi.yaml` `info.version` must match it,
`validate.py` fails otherwise.

## Rules

1. Every change to anything under `contracts/` during the sprint bumps PATCH, `1.0.0` to `1.0.1`, in
   the same commit. Run `python contracts/generate.py` and `python contracts/validate.py` before
   committing, and commit the regenerated models with the change.
2. Every bump is announced in the group chat within the same hour: new version, what changed, who has
   to act.
3. Breaking changes are not allowed after **2026-10-16**. Until then a breaking change also needs a
   confirmation from every owner of an affected component before it is merged.
4. Breaking means anything that can make a correct v1 producer or consumer fail: removing or renaming
   a field, an endpoint, a topic or an enum value, changing a type, unit or range, making an optional
   field required, adding an enum value to a closed set such as intents or error codes, or changing
   timing rules in `protocol.md`.
5. Non-breaking means adding an optional request field, a response field, an endpoint or a topic,
   widening a range, or clarifying a description. Consumers ignore unknown fields, so these are
   safe after the freeze.
6. MAJOR is reserved for the next contract after this sprint.

## 1.0.3, 2026-10-20

- `HealthStatus` gains `degradations`, the V-04 degraded modes in force, and every component gains
  `uptime_s` and `restarts`. New response fields only.
- `OccupancySource` description: `presence` covers the radar on the Pi as well as the ESP32 PIR. The
  enum is unchanged, adding `radar` would break existing clients after the freeze.

## 1.0.2, 2026-10-20

- New topic `system.degraded` with payload `Degraded`, published by the V-03 runtime guard.
- New component `core` for the bus, state store and model runtime, the only publisher of
  `system.degraded`. Both are additions, nothing existing changes.

## 1.0.1, 2026-10-20

- `bus.md`: the bus runs in process, `services/core/bus.py`, as V-02 specifies. The MQTT
  transport, QoS and broker rows are replaced by queue, drop, replay and strict validation rules.
  Topics, payloads and publishers are unchanged.
- `README.md`: diagram updated to match.

## 1.0.0, 2026-10-13

First frozen contract.

- `openapi.yaml` with the 24 endpoints the app needs, one error model, request, success and error
  examples on every operation.
- JSON Schemas for every domain object, request body, wire message and bus payload, each with
  examples.
- ESP32 wire protocol in `protocol.md`, event bus in `bus.md`, units in `units.md`.
- Generated Python and Dart models in `generated/`.
