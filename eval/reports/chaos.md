# Chaos report

Run 2026-10-20 06:19 UTC with `python tools/chaos.py`, core in replay mode with
`VESTA_STRICT=1`, on Windows AMD64, Python 3.10.7. Settings: supervisor_max_restarts=3.
Whole run 133.8 s.

Detect is the time from the injection until the degraded behaviour was observed through the API. Recover
is the time from removing the failure, or from the kill for a service, until the room was healthy again.

| # | Failure | Injected | Expected | Detect s | Recover s | Result |
|---|---|---|---|---:|---:|---|
| 1 | device service crashes | POST /chaos/kill/device | supervisor restarts it, health back to ok | 0.22 | 4.06 | pass |
| 2 | recorder service crashes | POST /chaos/kill/recorder | supervisor restarts it, health back to ok | 0.25 | 4.19 | pass |
| 3 | vision service crashes | POST /chaos/kill/vision | supervisor restarts it, health back to ok | 0.22 | 4.06 | pass |
| 4 | voice service crashes | POST /chaos/kill/voice | supervisor restarts it, health back to ok | 0.22 | 3.95 | pass |
| 5 | engine service crashes | POST /chaos/kill/engine | supervisor restarts it, health back to ok | 0.23 | 4.05 | pass |
| 6 | api service crashes | POST /chaos/kill/api | supervisor restarts it, health back to ok | 0.23 | 4.08 | pass |
| 7 | ESP32 link lost | POST /chaos/cut/esp32 | sensors stale within 5 s, actuator calls 503 device_unavailable, local questions still answered | 1.88 | 0.98 | pass |
| 8 | Camera or detector dead | POST /chaos/cut/camera | occupancy from presence, source presence, every count null | 0.27 | 0.95 | pass |
| 9 | Speech to text unavailable | POST /chaos/cut/stt, then an utterance | ring still lit by the wake word, fallback sentence spoken, interaction ends | 0.03 | 0.09 | pass |
| 10 | Internet lost | POST /chaos/cut/internet | local intents work, general questions answered with a spoken sentence | 17.44 | 29.95 | pass |
| 11 | Models over budget | POST /chaos/cut/budget, memory reads 110 percent of the ceiling | vision shed first, then general questions, announcements always play | 4.22 | 29.98 | pass |
| 12 | Vision keeps crashing | POST /chaos/kill/vision until the supervisor gives up after 3 restarts | service_failed, vision down on /health, the rest of the room keeps running | 14.59 | - | pass |

## Observations

1. device service crashes: ok failure detected; ok restart 1, healthy again.
2. recorder service crashes: ok failure detected; ok restart 1, healthy again.
3. vision service crashes: ok failure detected; ok restart 1, healthy again.
4. voice service crashes: ok failure detected; ok restart 1, healthy again.
5. engine service crashes: ok failure detected; ok restart 1, healthy again.
6. api service crashes: ok failure detected; ok restart 1, healthy again.
7. ESP32 link lost: ok stale state shown in 1.88 s; ok lighting call answered 503 device_unavailable; ok voice answered locally: "The sensors are offline. The last reading, 2 seconds old, was 21.3 degrees."; ok link back, lighting call answered 200.
8. Camera or detector dead: ok source presence, total None, counts [None, None, None, None]; ok vision_unavailable on /health; ok camera counts again.
9. Speech to text unavailable: ok spoke "Sorry, I can't understand speech right now. Please use the app." and ended in 0.03 s; ok stt_unavailable on /health; ok next utterance answered: "Left lights set to 100 percent.".
10. Internet lost: ok local intent answered: "It is 41 percent humidity in the room."; ok general question: "I can't answer general questions while the room is offline, but I can still help with the room."; ok back online.
11. Models over budget: ok vision shed, occupancy from presence; ok general questions shed with a spoken sentence; ok announcement still plays, 200; ok budget back, vision counting again.
12. Vision keeps crashing: ok gave up after 3 restarts; ok room degraded, still answering.

## Log

Unhandled exceptions in the core log: none.
