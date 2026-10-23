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

## 1.0.0, 2026-10-13

First frozen contract.

- `openapi.yaml` with the 24 endpoints the app needs, one error model, request, success and error
  examples on every operation.
- JSON Schemas for every domain object, request body, wire message and bus payload, each with
  examples.
- ESP32 wire protocol in `protocol.md`, event bus in `bus.md`, units in `units.md`.
- Generated Python and Dart models in `generated/`.
