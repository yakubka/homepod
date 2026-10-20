# Core: bus, state, lifecycle, config, logs

V-02. Every lane plugs into one process, `python -m services.core`. This page is all a lane owner needs.

## Your service

Each lane has `services/<lane>/service.py` with one class. Fill it in, keep the name and component.

```python
from services.core.service import Service


class VisionService(Service):
    name = "vision"        # log name and health key
    component = "vision"   # publisher name from contracts/bus.md

    async def start(self):
        await super().start()                        # starts the heartbeat
        self.frames = self.bus.subscribe("sensor.*")  # optional
        self.spawn(self.every(1.0, self.count), "count")

    async def count(self):
        snapshot = ...                                # build an OccupancySnapshot dict
        self.publish("perception.occupancy", snapshot)

    async def stop(self):
        await super().stop()                         # cancels everything spawn() started
```

- `start()` must return within `service.start_timeout`, 5 s. Long work goes into `spawn()`.
- `health()` returns `{ok, detail, last_tick}`. `every()` updates `last_tick`, call `self.tick()` if
  you loop yourself. A crashed background task turns health red with the exception as detail.
- Blocking calls, such as I2C reads or model inference, go through
  `await asyncio.get_running_loop().run_in_executor(None, fn)`. Threads publish with
  `self.bus.publish_threadsafe(...)`.
- Start order is `services/core/registry.py`, shutdown is the reverse.

## Bus

```python
self.publish("sensor.environment", payload)        # validated against the contract
sub = self.bus.subscribe("device.#", maxsize=64)   # * is one segment, # the rest
async for envelope in sub:                          # envelope["payload"] is the topic payload
    ...
self.bus.last_value("system.state")                 # latest envelope of any topic
```

Payloads are the dicts from `contracts/schemas`. With `VESTA_STRICT=1` a violation raises
`ContractViolation`, without it the violation is logged and the message delivered. A slow
subscriber loses its oldest messages, see `sub.dropped`, publishing never waits. State topics are
replayed to a new subscriber, commands and events never are.

## State

```python
room = self.state.snapshot()
room.environment.value.temperature_c   # typed, from contracts/generated/python
room.environment.age_s                 # seconds since the last update
room.environment.stale                 # older than state.stale.environment
room.stale_fields()                    # every stale field, heartbeats included
```

## Picking an implementation

`VESTA_MODE` is the only switch. Nothing else may check for hardware.

```python
from services.core import mode

camera = mode.pick(
    replay=lambda: RecordedCamera("sim/recordings/lecture.mp4"),
    sim=SyntheticCamera,
    real=PiCamera,   # import picamera2 inside PiCamera.__init__, never at module top
)
```

`tests/test_core.py` fails if replay mode imports a device library.

## Configuration

Later wins: `DEFAULTS` in `services/core/config.py`, `config/default.yaml`, `config/local.yaml`
(gitignored), `device_config.json` (written by the settings API), then `VESTA_*` environment variables.
`light.hold` is `VESTA_LIGHT_HOLD`. A new setting needs a default in `DEFAULTS` first, unknown keys are
ignored with a warning, wrong types fail at load.

## Logs

JSON lines on stdout. Every line logged while handling a voice utterance or an app call carries the
same `trace_id`, because the bus copies it into the envelope and back into the consumer:

```python
from services.core.logs import trace

with trace(request_id):
    self.publish("voice.intent", command)
```

```bash
python -m services.core | grep '"trace_id": "req_8d1f6g3h2j"'
```

Set `VESTA_LOG_FORMAT=text` for readable lines while developing.
