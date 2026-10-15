from services.core import config
from services.core.engine import Environment, Lecture, Occupancy, RoomState, SEATING

ZONES = SEATING + ("professor",)


def settings(**over):
    return {**config.DEFAULTS, **over}


def room(now=1000.0, counts=None, lux=100, professor=None, lecture=None, **kw):
    counts = counts if counts is not None else {z: 0 for z in SEATING}
    occ = Occupancy(
        total=sum(counts.values()),
        zones=dict(counts),
        professor=professor,
        source="camera",
        confidence=1.0,
        at=now,
    )
    kw.setdefault("lights", {z: 0 for z in ZONES})
    return RoomState(
        now=now,
        occupancy=occ,
        environment=Environment(temperature=kw.pop("temperature", 23.0), humidity=kw.pop("humidity", 45), lux=lux, at=now),
        lecture=lecture,
        window=[occ],
        **kw,
    )


def lecture(start=0.0, length=5400.0, name="Capstone Design", id="L1", expected=20):
    return Lecture(id=id, name=name, start=start, end=start + length, expected=expected)


def brightness(res, zone):
    for a in res.actions:
        if a.kind == "zone_light" and a.target == zone:
            return a.value
    return None
