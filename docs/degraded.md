# Degraded modes

V-04. What the room does when each part dies, decided before the demo. Each row is implemented in
the lane named, tested in `tests/test_degraded.py`, and run against the whole room by
`tools/chaos.py`, results in `eval/reports/chaos.md`. Active modes appear in `degradations` on
`GET /api/v1/health`.

| # | Failure | Detected by | Flag | What the room does | What it never does | Back to normal |
|---|---|---|---|---|---|---|
| 1 | ESP32 link lost | device lane, next read fails, within `device.report_interval` | `device_link_lost` | `device.status` goes down at once, the state store marks the last readings stale, `GET /sensors` shows the link down, actuator calls answer `503 device_unavailable` without waiting, commands get a synthetic `link_down` ack, voice still answers questions from the last reading and says it is old | Present an old reading as current, hang an actuator call | Link retried every `device.retry_interval`, flag clears on the first good read |
| 2 | Camera or detector dead | vision lane, the frame fails | `vision_unavailable` | Occupancy comes from the presence sensor, the ESP32 PIR or the radar on the Pi: `source: presence`, `occupied` per zone, every `count` and `total` null | Fabricate a count | Next good frame, `source: vision` again |
| 3 | Speech to text unavailable | voice lane, the call fails or passes `voice.stt_timeout` | `stt_unavailable` | The wake word still lights the ring, the assistant speaks "Sorry, I can't understand speech right now. Please use the app.", an empty transcript closes the interaction and the ring flashes error | Leave the ring listening, wait forever | Next utterance that transcribes |
| 4 | Internet lost | voice lane probe every `internet.probe_interval` | `internet_unavailable` | Every local intent works, lights, temperature, humidity, occupancy, announcements. A general question gets the spoken sentence "I can't answer general questions while the room is offline, but I can still help with the room." | Go silent on a question | Next successful probe |
| 5 | A model over budget | runtime guard, memory over `models.memory_ceiling_mb` or an inference over three times its p95 | `over_budget` | Idle models are evicted, then the scheduler sheds vision, occupancy falls back to presence as in row 2, then sheds general questions with the sentence "I can't answer general questions right now, the room is busy." | Shed the announcement path, announcements always play | `guard.cooldown` seconds within budget |
| 6 | A service crashes | supervisor, `supervisor.failure_limit` failed health polls in a row | none while restarting | Restarted with backoff `supervisor.backoff_base` doubling up to `supervisor.backoff_max`, `restarts` counted on `/health` | Loop forever | Healthy after the restart |
| 7 | A service keeps crashing | supervisor, `supervisor.max_restarts` restarts used | `service_failed`, detail names it | The supervisor stops it and gives up, `/health` shows it down and the room `degraded`, every other lane keeps running. A critical one, api, engine or the device bridge, makes `/health` answer 503 | Restart in a loop and starve the rest | Fix the cause and restart core, see `docs/runbook.md` |

## Shedding order

`services/core/scheduler.py` `SHED_ORDER` is vision first, then general questions. The announcement
path has its own purpose and is not in the list, so it is never shed.

## For lane owners

Read flags with `self.ctx.degradations.active("device_link_lost")`, raise your own with
`self.degrade(flag, detail)` and `self.recover(flag)`, react to model budget breaches in
`on_degraded(event)`. A new failure mode needs a new row here, a flag in
`contracts/schemas/HealthStatus.json`, a test in `tests/test_degraded.py` and a row in `tools/chaos.py`.
