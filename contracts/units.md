# Units and ranges, v1

One table, so nobody guesses. The schemas enforce every row, `validate.py` enforces the precision
rules JSON Schema cannot express.

| Quantity | JSON type | Unit | Range | Precision | Field names | Example |
|----------|-----------|------|-------|-----------|-------------|---------|
| Brightness | integer | percent of zone maximum | 0 to 100 | whole numbers | `brightness`, presets, `lights.*` | `80` |
| Illuminance | integer | lux | 0 to 100000 | whole numbers | `lux`, `ambient_lux`, `target_lux` | `140` |
| Measured temperature | number | degrees Celsius | -40.0 to 85.0 | one decimal | `temperature_c` | `23.4` |
| Target temperature | number | degrees Celsius | 16.0 to 30.0 | one decimal | `target_c`, `default_target_c` | `22.5` |
| Temperature band | number | degrees Celsius | 0.1 to 3.0 | one decimal | `hysteresis_c` | `0.5` |
| Relative humidity | integer | percent | 0 to 100 | whole numbers | `humidity_pct` | `41` |
| Fan speed | integer | percent duty | 0 to 100 | whole numbers | `fan_speed`, `speed`, `fan` | `60` |
| Volume | integer | percent | 0 to 100 | whole numbers | `volume` | `70` |
| Confidence | number | ratio | 0.0 to 1.0 | at most three decimals | `confidence`, `min_confidence` | `0.91` |
| People | integer | people | 0 to 500 | whole numbers, null when unknown | `count`, `total` | `3` |
| Fade | integer | milliseconds | 0 to 5000 | whole numbers | `fade_ms` | `400` |
| Durations | integer | unit in the suffix | per field | whole numbers | `*_ms`, `*_s` | `uptime_ms: 3605112` |
| Zone | string enum | | `left`, `center`, `right`, `professor` | | `zone`, keys of zone maps | `"left"` |
| Timestamp | string | ISO 8601 UTC | must end in `Z` | send milliseconds, accept 0 to 6 digits | `ts`, `*_at`, `from`, `to`, `at` | `"2026-10-13T09:00:00.000Z"` |
| Colour | string | sRGB | `#000000` to `#FFFFFF` | uppercase hex | `color` | `"#2D7FF9"` |
| Language | string | BCP 47 tag | `xx` or `xx-YY` | | `language` | `"en-US"` |
| Version | string | semantic version | `MAJOR.MINOR.PATCH` | | `*_version` | `"1.0.0"` |

## Conventions

- Field names are `snake_case` everywhere, on the wire, on the bus and in HTTP. Generated Dart models
  map them to `camelCase` properties.
- A unit suffix is part of the name when the unit is not obvious: `_c`, `_pct`, `_ms`, `_s`.
- Point in time events use `ts`, lifecycle moments use `*_at`.
- Zones are always listed in the order `left`, `center`, `right`, `professor`. Arrays of per-zone
  records hold one entry per zone in that order.
- Timestamps never carry an offset. The app converts to `Settings.timezone` only for display.
- Every response field is always present. Optional values are `null`, never missing. Only request
  bodies have omittable fields.
- Identifiers carry a type prefix: `lec_`, `ann_`, `dec_`, `req_`, `evt_`, `pb_`. Device command ids
  are short, `c` plus 8 hex characters, to fit the 512 byte serial line.
