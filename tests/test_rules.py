import ast
import pathlib
import re

import pytest

from services.core.engine import Occupancy, Override, decide, names, room_state
from services.core.engine.rules import fuse
from tests.helpers import brightness, lecture, room, settings

FULL = {"left": 12, "center": 12, "right": 12}
EMPTY = {"left": 0, "center": 0, "right": 0}
DARK = 60
BRIGHT = 900

#  name, counts, lux, lights, extra room kwargs, zone, expected brightness
TABLE = [
    ("empty room stays dark", EMPTY, DARK, {}, {}, "center", None),
    ("empty lit room switches off", EMPTY, DARK, {"center": 80}, {}, "center", 0),
    ("one person", {"center": 1}, DARK, {}, {}, "center", 45),
    ("two people", {"center": 2}, DARK, {}, {}, "center", 50),
    ("three people", {"center": 3}, DARK, {}, {}, "center", 55),
    ("four people", {"center": 4}, DARK, {}, {}, "center", 60),
    ("six people", {"center": 6}, DARK, {}, {}, "center", 70),
    ("eight people", {"center": 8}, DARK, {}, {}, "center", 80),
    ("exactly at full_at", {"center": 12}, DARK, {}, {}, "center", 100),
    ("one over full_at", {"center": 13}, DARK, {}, {}, "center", 100),
    ("full room", FULL, DARK, {}, {}, "center", 100),
    ("full room left zone", FULL, DARK, {}, {}, "left", 100),
    ("full room right zone", FULL, DARK, {}, {}, "right", 100),
    ("left only leaves right dark", {"left": 6, "center": 0, "right": 0}, DARK, {}, {}, "right", None),
    ("left only lights left", {"left": 6, "center": 0, "right": 0}, DARK, {}, {}, "left", 70),
    ("just under daylight_on", {"center": 6}, 249, {}, {}, "center", 70),
    ("exactly at daylight_on", {"center": 6}, 250, {}, {}, "center", None),
    ("over daylight_on with lights off", {"center": 6}, 300, {}, {}, "center", None),
    ("lit room under daylight_off stays", {"center": 6}, 399, {"center": 70}, {}, "center", None),
    ("lit room at daylight_off switches off", {"center": 6}, 400, {"center": 70}, {}, "center", 0),
    ("broad daylight switches off", FULL, BRIGHT, {"center": 100}, {}, "center", 0),
    ("no lux reading still lights", {"center": 6}, None, {}, {}, "center", 70),
    ("professor present", EMPTY, DARK, {}, {"professor": True}, "professor", 40),
    ("professor absent", EMPTY, DARK, {"professor": 40}, {"professor": False}, "professor", 0),
    ("professor unknown does nothing", EMPTY, DARK, {"professor": 40}, {"professor": None}, "professor", None),
    ("presentation dims the projector zone", FULL, DARK, {"center": 100}, {"presentation": True}, "center", 0),
    ("presentation keeps the other zones", FULL, DARK, {"left": 100}, {"presentation": True}, "left", 25),
    ("presentation beats daylight", FULL, BRIGHT, {"left": 0}, {"presentation": True}, "left", 25),
    ("manual override beats lighting", FULL, DARK, {}, {"overrides": {"center": Override(10, "app", 1000.0)}}, "center", 10),
    ("manual override beats presentation", FULL, DARK, {}, {"presentation": True, "overrides": {"center": Override(90, "voice", 1000.0)}}, "center", 90),
]

SCHEDULE = [
    ("before the lead time the room is idle", -7200, "idle"),
    ("inside the lead time the room is preparing", -600, "preparing"),
    ("at the start the room is in session", 0, "in_session"),
    ("mid lecture the room is in session", 2700, "in_session"),
    ("just after the end the room is overrun", 5460, "overrun"),
    ("long after the end the room has ended", 7200, "ended"),
]


@pytest.mark.parametrize("name,counts,lux,lights,extra,zone,want", TABLE, ids=[t[0] for t in TABLE])
def test_lighting_table(name, counts, lux, lights, extra, zone, want):
    s = settings()
    base = {"left": 0, "center": 0, "right": 0, "professor": 0}
    base.update(lights)
    counts = {**EMPTY, **counts}
    r = room(counts=counts, lux=lux, lecture=lecture(), lights=base, **extra)
    assert brightness(decide(r, s, 1000.0), zone) == want


@pytest.mark.parametrize("name,offset,want", SCHEDULE, ids=[s[0] for s in SCHEDULE])
def test_schedule_states(name, offset, want):
    s = settings()
    r = room(now=1000.0 + offset, counts=EMPTY, lecture=lecture(start=1000.0))
    assert room_state(r, s) == want


def test_no_lecture_is_idle():
    assert room_state(room(counts=EMPTY), settings()) == "idle"


def test_preparing_lifts_the_room_to_its_configured_level():
    s = settings()
    r = room(now=600.0, counts=EMPTY, lecture=lecture(start=1000.0))
    assert brightness(decide(r, s, 600.0), "center") == s["light.preparing"]


def test_ended_room_goes_to_energy_saving():
    s = settings()
    r = room(now=9000.0, counts=FULL, lecture=lecture(start=1000.0),
             lights={"left": 100, "center": 100, "right": 100, "professor": 0})
    res = decide(r, s, 9000.0)
    assert brightness(res, "center") == s["light.idle"]


def test_environment_recommends_but_never_acts():
    s = settings()
    r = room(counts=EMPTY, lecture=lecture(), temperature=28.0, humidity=70)
    res = decide(r, s, 1000.0)
    kinds = {rec.kind for rec in res.recommendations}
    assert kinds == {"temperature", "humidity"}
    assert not [a for a in res.actions if a.kind == "climate"]


def test_environment_stays_quiet_inside_the_comfort_band():
    s = settings()
    r = room(counts=EMPTY, lecture=lecture(), temperature=23.5, humidity=52)
    assert decide(r, s, 1000.0).recommendations == []


def test_climate_is_silent_while_the_capability_is_off():
    s = settings(**{"climate.enabled": False})
    r = room(counts=EMPTY, lecture=lecture(), temperature=29.0, climate_approved=True)
    assert not [a for a in decide(r, s, 1000.0).actions if a.kind == "climate"]


def test_climate_needs_approval_even_when_enabled():
    s = settings(**{"climate.enabled": True})
    r = room(counts=EMPTY, lecture=lecture(), temperature=29.0, climate_approved=False)
    assert not [a for a in decide(r, s, 1000.0).actions if a.kind == "climate"]


def test_climate_emits_the_contract_shape():
    s = settings(**{"climate.enabled": True})
    r = room(counts=EMPTY, lecture=lecture(), temperature=29.0, climate_approved=True)
    cmd = [a for a in decide(r, s, 1000.0).actions if a.kind == "climate"]
    assert cmd and cmd[0].value == {"mode": "cool", "target": s["climate.temp_target"]}
    assert cmd[0].target == "ac"


def _absent_room(now, start=1000.0, announced=None):
    r = room(now=now, counts={"center": 10}, lecture=lecture(start=start), professor=False)
    r.absent_since = start
    r.announced = announced or []
    return r


def test_announcement_waits_for_the_configured_delay():
    s = settings()
    assert not [a for a in decide(_absent_room(1200.0), s, 1200.0).actions if a.kind == "announce"]


def test_announcement_waits_for_the_confirmation_window():
    s = settings()
    r = _absent_room(1400.0)
    r.absent_since = 1390.0
    assert not [a for a in decide(r, s, 1400.0).actions if a.kind == "announce"]


def test_announcement_fires_once_across_a_two_hour_lecture_and_a_restart():
    s = settings()
    start = 1000.0
    fired = []
    for t in range(1000, 1000 + 7200, 30):
        r = _absent_room(float(t), start, announced=list(fired))
        for a in decide(r, s, float(t)).actions:
            if a.kind == "announce":
                fired.append(a.value)
    assert fired == ["L1"]

    # a restart loses nothing because the guard lives in the persisted announced list
    r = _absent_room(1000.0 + 7000, start, announced=fired)
    assert not [a for a in decide(r, s, 1000.0 + 7000).actions if a.kind == "announce"]


def test_announcement_does_not_fire_when_the_professor_is_there():
    s = settings()
    r = room(now=2000.0, counts={"center": 10}, lecture=lecture(start=1000.0), professor=True)
    assert not [a for a in decide(r, s, 2000.0).actions if a.kind == "announce"]


def test_announcement_does_not_fire_outside_a_lecture():
    s = settings()
    r = _absent_room(500.0)
    assert not [a for a in decide(r, s, 500.0).actions if a.kind == "announce"]


def test_fusion_prefers_the_camera_during_a_lecture():
    s = settings()
    cam = Occupancy(total=4, zones={"left": 4}, source="camera", confidence=1.0, at=1.0)
    assert fuse(cam, False, True, s).total == 4


def test_fusion_trusts_the_radar_outside_a_lecture():
    s = settings()
    cam = Occupancy(total=4, zones={"left": 4}, source="camera", confidence=1.0, at=1.0)
    assert fuse(cam, False, False, s).total == 0


def test_fusion_never_invents_a_count():
    s = settings()
    assert fuse(None, True, True, s).total is None
    assert fuse(None, None, False, s).source == "none"
    weak = Occupancy(total=9, zones={"left": 9}, source="camera", confidence=0.1, at=1.0)
    assert fuse(weak, True, True, s).total is None


def test_every_rule_has_a_row_in_the_documentation():
    doc = pathlib.Path(__file__).resolve().parent.parent / "docs" / "rules.md"
    body = doc.read_text()
    for name in names() + ["occupancy.fusion", "manual.override"]:
        assert f"`{name}`" in body, f"{name} is missing from docs/rules.md"


def test_rule_bodies_carry_no_thresholds():
    src = pathlib.Path(__file__).resolve().parent.parent / "services" / "core" / "engine" / "rules.py"
    tree = ast.parse(src.read_text())
    allowed = {0, 1, 100}
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for stmt in fn.body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and n.value not in allowed:
                    bad.append((fn.name, n.value))
    assert not bad, f"thresholds belong in settings, found {bad}"
