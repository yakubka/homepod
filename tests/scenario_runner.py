import json
import pathlib
from dataclasses import replace

import yaml

from services.core import config
from services.core.engine import Environment, Lecture, Occupancy, Override, RoomState, decide

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "sim" / "scenarios"
GOLDEN = ROOT / "sim" / "golden"


def load(name):
    return yaml.safe_load((SCENARIOS / f"{name}.yaml").read_text())


def run(scenario):
    cfg = {**config.DEFAULTS, **scenario.get("settings", {})}
    room = RoomState(now=0.0, lights={z: 0 for z in ("left", "center", "right", "professor")})
    out = []

    for ev in scenario["events"]:
        now = float(ev["at"])
        if "lecture" in ev:
            room.lecture = Lecture(**ev["lecture"]) if ev["lecture"] else None
        if "occupancy" in ev:
            o = ev["occupancy"]
            room.occupancy = Occupancy(
                total=o.get("total"),
                zones=o.get("zones", {}),
                professor=o.get("professor"),
                source=o.get("source", "camera"),
                confidence=o.get("confidence", 1.0),
                at=now,
            )
            if room.occupancy.total is not None:
                room.window.append(room.occupancy)
            if room.occupancy.professor is False and room.absent_since is None:
                room.absent_since = now
            elif room.occupancy.professor:
                room.absent_since = None
        if "environment" in ev:
            e = ev["environment"]
            room.environment = Environment(
                temperature=e.get("temperature"),
                humidity=e.get("humidity"),
                lux=e.get("lux"),
                at=now,
            )
        if "presentation" in ev:
            room.presentation = bool(ev["presentation"])
        if "override" in ev:
            ov = ev["override"]
            if ov.get("brightness") is None:
                room.overrides.pop(ov["zone"], None)
            else:
                room.overrides[ov["zone"]] = Override(ov["brightness"], ov.get("source", "app"), now)

        res = decide(room, cfg, now)
        for a in res.actions:
            if a.kind == "zone_light":
                room.lights[a.target] = a.value
                room.changed[a.target] = now
            elif a.kind == "announce":
                room.announced.append(a.value)
            out.append({"at": now, "rule": a.rule, "kind": a.kind, "target": a.target, "value": a.value})
        for r in res.recommendations:
            out.append({"at": now, "rule": r.rule, "kind": "recommend", "target": r.kind, "value": r.target})

    return out


def golden(name):
    return json.loads((GOLDEN / f"{name}.json").read_text())


def write_golden(name, actions):
    GOLDEN.mkdir(parents=True, exist_ok=True)
    (GOLDEN / f"{name}.json").write_text(json.dumps(actions, indent=2) + "\n")


def diff(expected, actual):
    lines = []
    for i in range(max(len(expected), len(actual))):
        a = expected[i] if i < len(expected) else None
        b = actual[i] if i < len(actual) else None
        if a != b:
            lines.append(f"  step {i}\n    expected {a}\n    actual   {b}")
    return "\n".join(lines)


def names():
    return sorted(p.stem for p in SCENARIOS.glob("*.yaml"))
