import time

from services.core.engine import DecisionLog, Occupancy, decide
from tests.helpers import lecture, room, settings


def test_every_applied_action_has_exactly_one_log_entry():
    s = settings()
    log = DecisionLog()
    r = room(now=0.0, counts={"left": 0, "center": 0, "right": 0}, lecture=lecture(), lux=60)
    r.window = []
    applied = 0

    for i in range(50):
        now = float(i * 60)
        counts = {"left": i % 7, "center": (i * 3) % 13, "right": i % 4}
        o = Occupancy(total=sum(counts.values()), zones=counts, source="camera", confidence=1.0, at=now)
        r.occupancy = o
        r.window = [o]
        res = decide(r, s, now)
        log.add(res.decisions)
        for a in res.actions:
            if a.kind == "zone_light":
                r.lights[a.target] = a.value
                r.changed[a.target] = now
        applied += len(res.actions)

    entries = log.query(limit=len(log.items), applied=True)
    assert len(entries) == applied
    assert all(e["line"] and e["inputs"] for e in entries)


def test_suppressed_decisions_are_kept_with_their_reason():
    s = settings()
    r = room(counts={"left": 12, "center": 12, "right": 12}, lecture=lecture(), presentation=True,
             lights={"left": 100, "center": 100, "right": 100, "professor": 0})
    log = DecisionLog()
    log.add(decide(r, s, 1000.0).decisions)
    suppressed = log.query(limit=100, applied=False)
    assert suppressed
    assert all(e["suppressed_by"] and "suppressed" in e["line"] for e in suppressed)


def test_query_filters():
    s = settings()
    r = room(counts={"center": 6, "left": 0, "right": 0}, lecture=lecture(), lux=60)
    log = DecisionLog()
    log.add(decide(r, s, 1000.0).decisions)
    assert log.query(rule="lighting.zone")
    assert log.query(rule="nothing.here") == []
    assert log.query(since=2000.0) == []
    assert log.query(until=500.0) == []
    assert len(log.query(limit=1)) == 1


def test_the_log_is_bounded():
    log = DecisionLog(keep=10)
    for d in [decide(room(counts={"center": 6}, lecture=lecture(), lux=60), settings(), float(i)).decisions
              for i in range(100)]:
        log.add(d)
    assert len(log.items) == 10


def test_a_hundred_thousand_rows_still_answer_fast():
    s = settings()
    log = DecisionLog(keep=100000)
    r = room(counts={"center": 6, "left": 0, "right": 0}, lecture=lecture(), lux=60)
    batch = decide(r, s, 1000.0).decisions
    while len(log.items) < 100000:
        log.add(batch)
    assert len(log.items) == 100000

    started = time.perf_counter()
    rows = log.query(limit=20)
    took = time.perf_counter() - started
    assert len(rows) == 20
    assert took < 0.05, f"query took {took * 1000:.1f} ms"
