from typing import List, Optional

from .engine import clamp, rule
from .types import (
    ENDED,
    IDLE,
    IN_SESSION,
    OVERRUN,
    PREPARING,
    SEATING,
    Action,
    Occupancy,
    Recommendation,
    RoomState,
)


def fuse(camera: Optional[Occupancy], radar: Optional[bool], in_lecture: bool, settings: dict) -> Occupancy:
    usable = camera is not None and camera.total is not None and camera.confidence >= settings["occupancy.min_confidence"]
    if usable and (in_lecture or radar is not False):
        return camera
    if usable and radar is False and not in_lecture:
        return Occupancy(total=0, zones={z: 0 for z in camera.zones}, professor=False, source="radar", confidence=1.0, at=camera.at)
    if radar is None:
        return Occupancy(source="none")
    return Occupancy(total=None, zones={}, professor=None, source="radar", confidence=1.0, at=camera.at if camera else 0.0)


def room_state(room: RoomState, settings: dict) -> str:
    lec = room.lecture
    if lec is None or lec.end <= lec.start:
        return IDLE
    if room.now < lec.start - settings["schedule.lead"]:
        return IDLE
    if room.now < lec.start:
        return PREPARING
    if room.now <= lec.end:
        return IN_SESSION
    if room.now <= lec.end + settings["schedule.overrun"]:
        return OVERRUN
    return ENDED


def _lit(room: RoomState, zone: str) -> bool:
    return room.lights.get(zone, 0) > 0


def _daylight_ok(room: RoomState, zone: str, settings: dict) -> bool:
    lux = room.environment.lux
    if lux is None:
        return True
    limit = settings["light.daylight_off"] if _lit(room, zone) else settings["light.daylight_on"]
    return lux < limit


@rule("lighting.zone", 20, ("occupancy", "environment", "lights"))
def lighting(room: RoomState, settings: dict) -> List[Action]:
    out = []
    full = settings["light.full_at"]
    base = settings["light.base"]
    for zone in SEATING:
        n = room.occupancy.of(zone)
        if n is None:
            continue
        if not _daylight_ok(room, zone, settings):
            out.append(
                Action(
                    "zone_light",
                    zone,
                    0,
                    inputs={"lux": room.environment.lux, "students": n},
                    why=f"{zone} zone off, {room.environment.lux} lux is enough daylight",
                )
            )
            continue
        want = 0 if n == 0 else clamp(base + (100 - base) * min(n, full) / full)
        out.append(
            Action(
                "zone_light",
                zone,
                want,
                inputs={"students": n, "lux": room.environment.lux, "full_at": full},
                why=f"{zone} zone to {want} percent, {n} students detected"
                + (f" and {room.environment.lux} lux measured" if room.environment.lux is not None else ""),
            )
        )

    prof = settings["professor.zone"]
    if room.occupancy.professor is not None and _daylight_ok(room, prof, settings):
        want = settings["light.base"] if room.occupancy.professor else 0
        out.append(
            Action(
                "zone_light",
                prof,
                want,
                inputs={"professor": room.occupancy.professor},
                why=f"{prof} zone to {want} percent, professor "
                + ("present" if room.occupancy.professor else "absent"),
            )
        )
    return out


@rule("schedule.room", 40, ("lecture",))
def schedule(room: RoomState, settings: dict) -> List[Action]:
    state = room_state(room, settings)
    if state == PREPARING:
        level = settings["light.preparing"]
    elif state in (IDLE, ENDED):
        level = settings["light.idle"]
    else:
        return []
    return [
        Action(
            "zone_light",
            zone,
            level,
            inputs={"room": state},
            why=f"{zone} zone to {level} percent, room is {state}",
        )
        for zone in SEATING
    ]


@rule("presentation.lighting", 60, ("presentation",))
def presentation(room: RoomState, settings: dict) -> List[Action]:
    if not room.presentation:
        return []
    dim = settings["light.presentation_dim"]
    other = settings["light.presentation_other"]
    projector = settings["presentation.zones"]
    out = []
    for zone in SEATING:
        want = dim if zone in projector else other
        out.append(
            Action(
                "zone_light",
                zone,
                want,
                inputs={"presentation": True, "projector": zone in projector},
                why=f"{zone} zone to {want} percent, presentation mode",
            )
        )
    return out


@rule("environment.comfort", 10, ("environment",))
def environmental(room: RoomState, settings: dict) -> List[Recommendation]:
    env = room.environment
    out = []
    if env.temperature is not None:
        target = settings["climate.temp_target"]
        delta = round(env.temperature - target, 1)
        if abs(delta) > settings["climate.temp_band"]:
            out.append(
                Recommendation(
                    "temperature",
                    env.temperature,
                    target,
                    delta,
                    f"{env.temperature} degrees against a {target} target, "
                    + ("cool the room" if delta > 0 else "warm the room"),
                )
            )
    if env.humidity is not None:
        target = settings["climate.humidity_target"]
        delta = env.humidity - target
        if abs(delta) > settings["climate.humidity_band"]:
            out.append(
                Recommendation(
                    "humidity",
                    env.humidity,
                    target,
                    delta,
                    f"{env.humidity} percent humidity against a {target} target, "
                    + ("dehumidify" if delta > 0 else "humidify"),
                )
            )
    return out


@rule("climate.control", 10, ("environment",))
def climate(room: RoomState, settings: dict) -> List[Action]:
    if not settings["climate.enabled"] or not room.climate_approved:
        return []
    recs = environmental(room, settings)
    target = settings["climate.temp_target"]
    if not any(r.kind == "temperature" for r in recs):
        return []
    return [
        Action(
            "climate",
            "ac",
            {"mode": "cool" if room.environment.temperature > target else "heat", "target": target},
            inputs={"temperature": room.environment.temperature, "target": target},
            why=f"air conditioner to {target} degrees, room is at {room.environment.temperature}",
        )
    ]


@rule("announce.professor_absent", 70, ("lecture", "occupancy"))
def announcement(room: RoomState, settings: dict) -> List[Action]:
    lec = room.lecture
    if lec is None or room_state(room, settings) not in (IN_SESSION, OVERRUN):
        return []
    if lec.id in room.announced or room.occupancy.professor is not False:
        return []
    if room.now - lec.start < settings["announce.after"]:
        return []
    if room.absent_since is None or room.now - room.absent_since < settings["announce.confirm"]:
        return []
    return [
        Action(
            "announce",
            "professor_absent",
            lec.id,
            inputs={
                "lecture": lec.name,
                "late_by": round(room.now - lec.start),
                "absent_for": round(room.now - room.absent_since),
            },
            why=f"announce professor absent, {round(room.now - lec.start)} seconds after the start of {lec.name}",
        )
    ]
