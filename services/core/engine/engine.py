import math
from dataclasses import replace
from typing import Callable, Dict, List, Tuple

from .types import (
    Action,
    Decision,
    DecisionResult,
    Occupancy,
    Recommendation,
    RoomState,
)

RULES: List["Rule"] = []


class Rule:
    def __init__(self, name: str, priority: int, reads: Tuple[str, ...], fn: Callable):
        self.name = name
        self.priority = priority
        self.reads = reads
        self.fn = fn


def rule(name: str, priority: int, reads: Tuple[str, ...]):
    def wrap(fn):
        RULES.append(Rule(name, priority, tuple(reads), fn))
        return fn

    return wrap


def names() -> List[str]:
    return [r.name for r in RULES]


def _round(x: float) -> int:
    # half up, so one person on a zone boundary lights both sides instead of neither
    return int(math.floor(x + 0.5))


def smooth(room: RoomState, settings: dict) -> Occupancy:
    span = settings["occupancy.window"]
    recent = [o for o in room.window if room.now - o.at <= span] or [room.occupancy]
    counted = [o for o in recent if o.total is not None]
    if not counted:
        return replace(room.occupancy, at=room.now)
    zones = {}
    for z in room.occupancy.zones:
        vals = [o.of(z) for o in counted if o.of(z) is not None]
        zones[z] = _round(sum(vals) / len(vals)) if vals else None
    return replace(
        room.occupancy,
        total=_round(sum(o.total for o in counted) / len(counted)),
        zones=zones,
        confidence=sum(o.confidence for o in counted) / len(counted),
        at=room.now,
    )


def clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def _better(a: Action, b: Action) -> Action:
    if a.kind == "zone_light":
        return a if a.value >= b.value else b
    return a


def _held(room: RoomState, want: Action, settings: dict) -> bool:
    if want.kind != "zone_light":
        return False
    current = room.lights.get(want.target)
    if current is None or current == want.value:
        return False
    last = room.changed.get(want.target)
    return last is not None and room.now - last < settings["light.hold"]


def decide(room: RoomState, settings: dict, now: float) -> DecisionResult:
    room = replace(room, now=now, occupancy=smooth(replace(room, now=now), settings))
    res = DecisionResult()

    proposals: Dict[Tuple[str, str], Action] = {}
    losers: List[Tuple[Action, Action]] = []

    for r in sorted(RULES, key=lambda x: -x.priority):
        for item in r.fn(room, settings) or []:
            if isinstance(item, Recommendation):
                res.recommendations.append(replace(item, rule=r.name))
                continue
            item = replace(item, rule=r.name)
            if item.kind == "zone_light":
                item = replace(item, value=clamp(item.value))
            held = proposals.get(item.slot)
            if held is None:
                proposals[item.slot] = item
                continue
            prev = next(x for x in RULES if x.name == held.rule)
            if prev.priority > r.priority:
                losers.append((item, held))
            elif prev.priority < r.priority:
                losers.append((held, item))
                proposals[item.slot] = item
            else:
                win = _better(held, item)
                losers.append((item if win is held else held, win))
                proposals[item.slot] = win

    for lost, won in losers:
        res.decisions.append(
            Decision(lost, False, f"{won.rule} has priority", now)
        )

    for slot, want in sorted(proposals.items()):
        over = room.overrides.get(want.target) if want.kind == "zone_light" else None
        if over is not None and now - over.at < settings["override.ttl"]:
            res.decisions.append(
                Decision(want, False, f"manual override by {over.source}", now)
            )
            if room.lights.get(want.target) != over.brightness:
                forced = Action(
                    "zone_light",
                    want.target,
                    clamp(over.brightness),
                    "manual.override",
                    {"source": over.source, "ttl_left": round(settings["override.ttl"] - (now - over.at))},
                    f"{want.target} zone to {clamp(over.brightness)} percent, manual override by {over.source}",
                )
                res.actions.append(forced)
                res.decisions.append(Decision(forced, True, "", now))
            continue

        if want.kind == "zone_light" and room.lights.get(want.target) == want.value:
            continue

        if _held(room, want, settings):
            res.decisions.append(
                Decision(want, False, f"held, changed less than {settings['light.hold']}s ago", now)
            )
            continue

        res.actions.append(want)
        res.decisions.append(Decision(want, True, "", now))

    # an override for a zone no rule proposed for (professor presence unknown, no camera count) still applies
    proposed = {w.target for w in proposals.values() if w.kind == "zone_light"}
    for zone, over in sorted(room.overrides.items()):
        if zone in proposed or now - over.at >= settings["override.ttl"] or room.lights.get(zone) == over.brightness:
            continue
        forced = Action(
            "zone_light",
            zone,
            clamp(over.brightness),
            "manual.override",
            {"source": over.source, "ttl_left": round(settings["override.ttl"] - (now - over.at))},
            f"{zone} zone to {clamp(over.brightness)} percent, manual override by {over.source}",
        )
        res.actions.append(forced)
        res.decisions.append(Decision(forced, True, "", now))

    from .rules import room_state

    res.room = room_state(room, settings)
    return res
