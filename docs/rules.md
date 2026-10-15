# VESTA decision rules

Every automatic action the room takes comes from one of the rules below. The engine is a pure function,
`decide(room_state, settings, now)`, so the same room state always produces the same decision and every
decision carries the input values that caused it. Thresholds are settings, never numbers in the code,
and they can be changed while the system runs.

Rules propose actions, they do not perform them. The engine collects every proposal, resolves the
conflicts, and returns what should happen. A higher priority wins. On equal priority the brighter
value wins for a light. A manual override beats every rule until it expires. An action cannot be
reversed before its hold time has passed, which is what stops lamps flickering.

| Rule | Priority | Reads | Thresholds | Produces |
|---|---|---|---|---|
| `manual.override` | highest | overrides | `override.ttl` | Holds a zone at the brightness a person asked for, from the app or by voice, and reports the time left |
| `announce.professor_absent` | 70 | lecture, occupancy | `announce.after`, `announce.confirm` | One announcement request per lecture when the professor zone stays empty |
| `presentation.lighting` | 60 | presentation | `light.presentation_dim`, `light.presentation_other`, `presentation.zones` | Projector zones dim, the rest of the room keeps a working minimum |
| `schedule.room` | 40 | lecture | `schedule.lead`, `schedule.overrun`, `light.preparing`, `light.idle` | Room level defaults for an idle, preparing or ended room |
| `lighting.zone` | 20 | occupancy, environment, lights | `light.full_at`, `light.base`, `light.daylight_on`, `light.daylight_off` | Per zone brightness from the number of students and the daylight |
| `environment.comfort` | 10 | environment | `climate.temp_target`, `climate.temp_band`, `climate.humidity_target`, `climate.humidity_band` | A recommendation for the user to approve, never an action |
| `climate.control` | 10 | environment | `climate.enabled`, `climate.temp_target` | An air conditioner command, only once a recommendation has been approved |
| `occupancy.fusion` | before the rules | camera, radar, lecture window | `occupancy.min_confidence`, `occupancy.window` | One occupancy snapshot from the camera and the radar, smoothed over a window |

## How each one behaves

**`occupancy.fusion`.** The camera gives a count per zone, the radar gives presence only. During a lecture
the camera is trusted. Outside a lecture, a radar that reports an empty room overrules a camera count.
A camera reading below `occupancy.min_confidence` is not used. When neither sensor can answer, the count
is published as unknown rather than as a number, so the room never shows an invented figure. Counts are
averaged over `occupancy.window` seconds before any rule sees them, and a half is rounded up, so one
person sitting on a zone boundary lights both sides rather than neither.

**`lighting.zone`.** Brightness rises with the number of students, from `light.base` at one student to
full at `light.full_at` students. An empty zone goes off. Daylight has two thresholds rather than one,
lights may come on below `light.daylight_on` and go off above `light.daylight_off`, so a room hovering
around the threshold does not switch back and forth. The professor zone follows professor presence.

Example, 18 students detected in the centre zone and 120 lux measured, below the 250 lux daylight
threshold, so the centre zone goes to 100 percent.

**`schedule.room`.** The clock and the lecture give the room a state, idle, preparing, in session,
overrun or ended. Preparing starts `schedule.lead` seconds before the lecture and lifts the room to
`light.preparing`. An ended room drops to `light.idle`. During a lecture this rule stands aside and
`lighting.zone` decides.

**`presentation.lighting`.** Presentation mode is switched on from the application, it is never guessed
from the camera. The zones listed in `presentation.zones` drop to `light.presentation_dim` so the screen
is readable, every other zone holds `light.presentation_other` so the room is not dark.

**`environment.comfort`.** Temperature and humidity are compared against the comfort targets. Outside the
band the rule produces a recommendation with the current value, the target, the difference and a sentence
a person can read. It never acts on its own.

**`climate.control`.** Takes an approved recommendation and sends a command to the air conditioner.
The whole path is behind `climate.enabled`, which is off by default, so a room with no compatible
air conditioner behaves exactly as if the feature did not exist.

**`announce.professor_absent`.** `announce.after` seconds into the lecture the rule checks the professor
zone. If it has been empty for `announce.confirm` seconds the rule asks the audio lane to play the
announcement, once, recorded against the lecture so a restart cannot repeat it. The engine never plays
audio itself.

**`manual.override`.** An override is per zone and carries its source and the time it was set. It wins
over every rule for `override.ttl` seconds, then the room returns to automatic on its own. Asking for
automatic clears it at once.

## Reading the decision log

Every decision is recorded, including the ones that were suppressed, with the rule, the inputs and the
reason. A suppressed decision is as useful as an applied one, it is what shows that a threshold is set
wrong. `GET /decisions` serves the log with a limit, a time range and a rule filter.
