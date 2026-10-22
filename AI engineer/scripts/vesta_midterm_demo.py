"""VESTA MIDTERM DEMOS 1, 2 (lux part) AND 5 ON FIXTURES. SIMULATED - SOFTWARE ONLY - NO HARDWARE, NO NETWORK.

    sensors   fixture decoded sensor messages -> decode / validate -> the ONE ClassroomStateStore -> ClassroomState
              -> the deterministic answer engine ("what is the temperature / humidity / is it bright") -> printed answers
    app       the same store -> AppInterface.read_status() (JSON) -> an authorized and an unauthorized light request through the
              shared action loop with the SIMULATED lights adapter -> printed results
    replay    decode a JSON-lines file of messages captured from the real firmware (one message per line) WITHOUT opening any port;
              origin UNKNOWN (a file replay is not live evidence). Shows which lines the decoder accepts and why others are rejected.

Freshness limits here are DEMO values (30 simulated seconds), NOT calibrated hardware values. The light demo is the Week 9 simulated
adapter: no light is controlled. The real light path (Demo 2 actuation) is scripts/week9_simulated_action_demo.py in software and
needs the hardware team's adapter on the device.

    python -m scripts.vesta_midterm_demo sensors
    python -m scripts.vesta_midterm_demo app
    python -m scripts.vesta_midterm_demo replay <captured messages .jsonl>

Exit codes: 0 = ran; 2 = bad input file.
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.contracts import RequestIdGenerator
from actions.simulated_adapter import SIMULATED_LIGHTS_CAPABILITIES, FakeClock, SimulatedLightsAdapter
from assistant.answering import AnswerContext, AnswerRequest, Capability, CapabilityDeclaration, DeterministicAnswerEngine
from assistant.app_interface import AppInterface, SerializedActionLoop
from fusion.contracts import FusionFreshnessConfig, Origin
from fusion.live_state import ClassroomStateStore, FixtureSensorSource, SensorMessageError, decode_sensor_message

DEMO_FRESHNESS = FusionFreshnessConfig(lux_max_age_s=30.0, temperature_max_age_s=30.0, humidity_max_age_s=30.0, presence_max_age_s=30.0)
FIXTURE = (
    {"kind": "TEMPERATURE", "source_id": "temp_1", "status": "OK", "value": 23.4, "unit": "degC"},
    {"kind": "HUMIDITY", "source_id": "hum_1", "status": "OK", "value": 41.0, "unit": "%RH"},
    {"kind": "LUX", "source_id": "bh1750", "status": "OK", "value": 312.0, "unit": "lx"},
    {"kind": "LUX", "source_id": "bh1750", "status": "OK", "value": 300.0, "unit": "W/m2"},       # rejected: wrong unit
    {"kind": "TEMPERATURE", "source_id": "temp_2", "status": "OK", "value": 22.0, "unit": "degC", "origin": "REAL"},   # rejected
)
QUESTIONS = ("what is the temperature in the room", "what is the humidity in the room", "how bright is the room")


def _store(clock) -> ClassroomStateStore:
    store = ClassroomStateStore(DEMO_FRESHNESS, clock=clock)
    accepted, rejected = store.pump(FixtureSensorSource(FIXTURE))
    print(f"fixture messages: accepted={accepted} rejected={rejected} reasons={store.rejected} origin=SIMULATED")
    return store


def sensors() -> int:
    clock = FakeClock(100.0)
    store = _store(clock)
    caps = CapabilityDeclaration(frozenset({Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS}))
    engine = DeterministicAnswerEngine()
    for when in (5.0, 40.0):                      # 5 s after receipt (FRESH) and 40 s after (STALE: answers say unavailable)
        clock.advance_to(100.0 + when)
        print(f"-- {when:.0f} simulated seconds after the readings")
        ctx = AnswerContext(store.snapshot(), caps)
        for q in QUESTIONS:
            r = engine.answer(AnswerRequest(q, ctx))
            print(f"  {r.status.value:<24} source={r.source.value:<10} {r.text}")
    return 0


def app() -> int:
    clock = FakeClock(100.0)
    store = _store(clock)
    adapter = SimulatedLightsAdapter(clock)
    loop = SerializedActionLoop(adapter, SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=2.0)
    token = secrets.token_hex(16)                 # generated per run: no credential lives in the code
    api = AppInterface(store, action_loop=loop, authorize=lambda t: "demo-phone" if t == token else None,
                       request_ids=RequestIdGenerator(prefix="app", token_source=lambda n: "0" * 12))
    clock.advance_to(105.0)
    print(json.dumps(api.read_status(), indent=2))
    adapter.plan(replies=[(0.1, "ACCEPTED"), (0.3, "APPLIED_CONFIRMED")])
    print(json.dumps(api.request_light(client_token=token, action="TURN_ON", correlation_id="phone-req-1")))
    print(json.dumps(api.request_light(client_token="wrong", action="TURN_OFF", correlation_id="phone-req-2")))
    return 0


def replay(path: str) -> int:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {type(exc).__name__}", file=sys.stderr)
        return 2
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            reading = decode_sensor_message(json.loads(line), origin=Origin.UNKNOWN, received_at_monotonic_s=0.0)
            print(f"line {n}: ACCEPTED {type(reading).__name__} source_id={reading.envelope.source_id}")
        except json.JSONDecodeError:
            print(f"line {n}: REJECTED NOT_JSON")
        except SensorMessageError as exc:
            print(f"line {n}: REJECTED {exc.reason}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.vesta_midterm_demo", description="VESTA midterm fixture demos (SIMULATED).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sensors")
    sub.add_parser("app")
    r = sub.add_parser("replay")
    r.add_argument("jsonl")
    args = p.parse_args(argv)
    print("SIMULATED FIXTURE DEMO - NO HARDWARE - NOT HARDWARE-VALIDATED" if args.cmd != "replay" else "FILE REPLAY - origin UNKNOWN - NOT LIVE")
    return {"sensors": sensors, "app": app}.get(args.cmd, lambda: replay(args.jsonl))()


if __name__ == "__main__":
    sys.exit(main())
