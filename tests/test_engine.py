import random

import pytest

from services.core.engine import Action, Occupancy, Override, RoomState, decide, smooth
from tests.helpers import ZONES, brightness, lecture, room, settings


def test_pure_function_does_not_mutate_its_input():
    s = settings()
    r = room(counts={"left": 4, "center": 9, "right": 0}, lecture=lecture())
    before = (dict(r.lights), dict(r.changed), r.occupancy.total, r.now)
    decide(r, s, 2000.0)
    assert (dict(r.lights), dict(r.changed), r.occupancy.total, r.now) == before


def test_determinism():
    s = settings()
    r = room(counts={"left": 3, "center": 11, "right": 1}, lecture=lecture(), lux=120)
    first = [(a.kind, a.target, a.value, a.rule) for a in decide(r, s, 1000.0).actions]
    for _ in range(1000):
        again = [(a.kind, a.target, a.value, a.rule) for a in decide(r, s, 1000.0).actions]
        assert again == first


def test_property_sweep():
    rng = random.Random(7)
    s = settings()
    for _ in range(10000):
        counts = {z: rng.choice([None, 0, 1, 3, 7, 12, 40]) for z in ("left", "center", "right")}
        counts = {k: v for k, v in counts.items() if v is not None}
        now = rng.uniform(0.0, 1e6)
        r = room(
            now=now,
            counts=counts or {"left": 0, "center": 0, "right": 0},
            lux=rng.choice([None, 0, 60, 250, 399, 400, 900]),
            professor=rng.choice([None, True, False]),
            lecture=rng.choice([None, lecture(start=now - rng.uniform(-9000, 9000))]),
            presentation=rng.choice([True, False]),
            lights={z: rng.choice([0, 25, 50, 100]) for z in ZONES},
        )
        res = decide(r, s, now)
        seen = set()
        for a in res.actions:
            if a.kind == "zone_light":
                assert 0 <= a.value <= 100
                assert a.target not in seen
                seen.add(a.target)
            assert a.rule


def test_conflict_higher_priority_wins():
    s = settings()
    r = room(counts={"left": 12, "center": 12, "right": 12}, lecture=lecture(), presentation=True,
             lights={"left": 100, "center": 100, "right": 100, "professor": 0})
    res = decide(r, s, 1000.0)
    assert brightness(res, "center") == s["light.presentation_dim"]
    losers = [d for d in res.decisions if not d.applied and d.action.rule == "lighting.zone"]
    assert losers and "presentation.lighting has priority" in losers[0].suppressed_by


def test_conflict_equal_priority_takes_the_brighter_value():
    from services.core.engine import engine as eng

    a = Action("zone_light", "left", 30, "lighting.zone")
    b = Action("zone_light", "left", 80, "lighting.zone")
    assert eng._better(a, b).value == 80
    assert eng._better(b, a).value == 80


def test_manual_override_beats_every_rule_until_it_expires():
    s = settings()
    base = room(counts={"left": 12, "center": 12, "right": 12}, lecture=lecture())
    r = room(
        counts={"left": 12, "center": 12, "right": 12},
        lecture=lecture(),
        overrides={"center": Override(10, "voice", 1000.0)},
        lights=dict(base.lights),
    )
    res = decide(r, s, 1000.0)
    assert brightness(res, "center") == 10
    assert any("manual override by voice" in d.suppressed_by for d in res.decisions if not d.applied)

    expired = decide(r, s, 1000.0 + s["override.ttl"] + 1)
    assert brightness(expired, "center") == 100


def test_hold_blocks_a_reversal_but_not_the_first_change():
    s = settings()
    r = room(counts={"left": 0, "center": 0, "right": 0}, lecture=lecture(),
             lights={"left": 80, "center": 0, "right": 0, "professor": 0},
             changed={"left": 990.0})
    assert brightness(decide(r, s, 1000.0), "left") is None
    assert brightness(decide(r, s, 990.0 + s["light.hold"] + 1), "left") == 0


def test_no_action_when_the_value_is_already_set():
    s = settings()
    r = room(counts={"left": 12, "center": 0, "right": 0}, lecture=lecture(),
             lights={"left": 100, "center": 0, "right": 0, "professor": 0})
    assert brightness(decide(r, s, 1000.0), "left") is None


def test_smoothing_averages_the_window_and_rounds_half_up():
    s = settings(**{"occupancy.window": 10.0})
    now = 1000.0
    window = [
        Occupancy(total=1, zones={"left": 1, "center": 0, "right": 0}, at=now - 8),
        Occupancy(total=0, zones={"left": 0, "center": 0, "right": 0}, at=now - 4),
    ]
    r = room(now=now, counts={"left": 0, "center": 0, "right": 0})
    r.window = window
    assert smooth(r, s).of("left") == 1


def test_smoothing_ignores_samples_outside_the_window():
    s = settings(**{"occupancy.window": 5.0})
    now = 1000.0
    r = room(now=now, counts={"left": 0, "center": 0, "right": 0})
    r.window = [Occupancy(total=20, zones={"left": 20, "center": 0, "right": 0}, at=now - 60)]
    assert smooth(r, s).of("left") == 0


def test_unknown_count_produces_no_lighting_action():
    s = settings()
    r = room(counts={"left": 5, "center": 5, "right": 5}, lecture=lecture())
    r.occupancy = Occupancy(total=None, zones={}, source="none", at=r.now)
    r.window = []
    res = decide(r, s, 1000.0)
    assert not [a for a in res.actions if a.rule == "lighting.zone"]


def test_flapping_on_a_zone_boundary():
    s = settings()
    r = room(now=0.0, counts={"left": 1, "center": 0, "right": 0}, lecture=lecture(), lux=60)
    r.window = []
    changes = 0
    for i in range(151):
        now = float(i * 2)
        left, right = (1, 0) if i % 2 == 0 else (0, 1)
        o = Occupancy(total=1, zones={"left": left, "center": 0, "right": right}, source="camera", confidence=1.0, at=now)
        r.occupancy = o
        r.window.append(o)
        for a in decide(r, s, now).actions:
            if a.kind == "zone_light":
                r.lights[a.target] = a.value
                r.changed[a.target] = now
                changes += 1
    assert changes <= 2


@pytest.mark.parametrize("value,want", [(-50, 0), (0, 0), (55, 55), (100, 100), (260, 100)])
def test_brightness_is_clamped(value, want):
    from services.core.engine import clamp

    assert clamp(value) == want
