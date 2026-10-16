"""SIMULATED / SYNTHETIC fixtures for Week 6 Task 3 (fusion end-to-end validation).

Everything here is invented structured input: no sensor, ESP32 or camera produced it. Simulated inputs use
Origin.SIMULATED. Objects labelled Origin.REAL appear only in provenance-LOGIC tests and are structural, not
hardware evidence. The freshness values (10 s max age, 2 s max skew) are TEST VALUES ONLY - NOT CALIBRATED.

Used by tests/test_fusion_scenarios.py, and as a small tool:

    python -m tests.fusion_synthetic --scenarios    # synthetic scenario correctness (N / N matched)
    python -m tests.fusion_synthetic --benchmark    # fusion-stage timing on the development PC
"""

import argparse
import dataclasses
import json
import math
import statistics as st
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from fusion.contracts import (
    FusionFreshnessConfig, HumidityReading, LuxReading, Origin, PresenceReading, SourceEnvelope, SourceStatus,
    TemperatureReading, VisionObservation,
)
from fusion.fuse import ClassroomState, fuse_classroom_state
from fusion.telemetry import fusion_decision_record
from tests import zone_synthetic as zs
from vision.zones import assign_detections

NOW = 100.0
CFG = FusionFreshnessConfig(vision_max_age_s=10.0, presence_max_age_s=10.0, lux_max_age_s=10.0,
                            temperature_max_age_s=10.0, humidity_max_age_s=10.0, max_source_skew_s=2.0)   # TEST VALUES ONLY
CFG_NO_SKEW = dataclasses.replace(CFG, max_source_skew_s=None)
ZONES = zs.zone_set(zs.GAPPED_DEFS)
S = Origin.SIMULATED


# ---- builders ---------------------------------------------------------------------------

def env(sid, t=99.0, origin=S, status=SourceStatus.OK):
    return SourceEnvelope(sid, origin, t, status, None if status is SourceStatus.OK else "synthetic fault")


def occ(n, zones=True, outside=0):
    dets = [zs.person(50 + 40 * i) for i in range(n)] + [zs.person(500, 60) for _ in range(outside)]
    return assign_detections(dets, zs.SYNTHETIC_CLASSROOM, ZONES if zones else None)


def mixed_occ():
    """Explicit Week 5 result: LEFT 8, CENTER 7, RIGHT 9, UNASSIGNED 5, OUTSIDE 3 (inside the classroom: 29)."""
    dets, _ = zs.build_arrangement(zs.ARRANGEMENTS["mixed_dense"])
    return assign_detections(dets, zs.SYNTHETIC_CLASSROOM, ZONES)


def vis(n=None, t=99.0, origin=S, status=SourceStatus.OK, zones=True, result=None):
    if status is not SourceStatus.OK:
        return VisionObservation(env("cam", t, origin, status))
    return VisionObservation(env("cam", t, origin), result if result is not None else occ(n, zones))


def pres(present=None, t=99.0, origin=S, status=SourceStatus.OK):
    if status is not SourceStatus.OK:
        return PresenceReading(env("radar", t, origin, status))
    return PresenceReading(env("radar", t, origin), present)


def lux(v=320.0, t=99.0, origin=S):
    return LuxReading(env("esp-sim", t, origin), v)


def temp(sensor_id, v, t=99.0, origin=S):
    return TemperatureReading(env("esp-sim", t, origin), sensor_id, v)


def hum(v=45.0, t=99.0, origin=S):
    return HumidityReading(env("esp-sim", t, origin), v)


def inputs(v=None, p=None, l=None, temps=(), h=None, now=NOW, cfg=CFG) -> Dict[str, object]:
    return dict(vision=v, presence=p, lux=l, temperatures=list(temps), humidity=h, now_monotonic_s=now, freshness=cfg)


def fuse(args) -> ClassroomState:
    return fuse_classroom_state(args["vision"], args["presence"], args["lux"], args["temperatures"], args["humidity"],
                                args["now_monotonic_s"], args["freshness"])


# ---- scenarios --------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    build: Callable[[], Dict[str, object]]
    expect: Dict[str, object]


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: Tuple[Step, ...]


def flat(state: ClassroomState) -> Dict[str, object]:
    o = state.occupancy
    return {
        "rule": state.rule_id, "fallback": state.fallback_rule_id, "evidence": o.evidence.name, "fused": o.fused_presence.name,
        "degraded": state.degraded, "reasons": tuple(r.name for r in state.degraded_reasons), "vision_count": o.vision_count,
        "vision_used": o.vision_used, "presence_used": o.presence_used, "origin": state.aggregate_origin.name,
        "sensor_present": o.sensor_present, "zones_configured": o.zones_configured,
        "zone_counts": None if o.zone_counts is None else dict(o.zone_counts),
        "unassigned": o.unassigned_inside_roi, "outside": o.outside_classroom_roi, "issues": state.environment.issues,
        "vision_state": state.vision_state.usability.name, "presence_state": state.presence_state.usability.name,
    }


def exp(rule, evidence, fused, degraded, reasons=(), vision_count="-", vision_used=None, presence_used=None, origin="SIMULATED",
        fallback=None, **extra):
    d = {"rule": rule, "fallback": fallback, "evidence": evidence, "fused": fused, "degraded": degraded, "reasons": tuple(reasons),
         "origin": origin}
    if vision_count != "-":
        d["vision_count"] = vision_count
    if vision_used is not None:
        d["vision_used"] = vision_used
    if presence_used is not None:
        d["presence_used"] = presence_used
    d.update(extra)
    return d


def one(name, build, expect) -> Scenario:
    return Scenario(name, (Step(build, expect),))


R1 = exp("R1", "CONFIRMED", "PRESENT", False, vision_count=3, vision_used=True, presence_used=True)

SCENARIOS: List[Scenario] = [
    one("01 R1 occupied agreement", lambda: inputs(vis(3), pres(True)), R1),
    one("02 R2 empty agreement", lambda: inputs(vis(0), pres(False)),
        exp("R2", "CONFIRMED_EMPTY", "ABSENT", False, vision_count=0, vision_used=True, presence_used=True)),
    one("03 R3 radar present, no detections", lambda: inputs(vis(0), pres(True)),
        exp("R3", "CONFLICT", "UNKNOWN", True, ["PRESENCE_WITHOUT_DETECTIONS"], vision_count=0, sensor_present=True)),
    one("04 R4 detections, radar absent", lambda: inputs(vis(4), pres(False)),
        exp("R4", "CONFLICT", "UNKNOWN", True, ["DETECTIONS_WITHOUT_PRESENCE"], vision_count=4, sensor_present=False)),
    one("05 R5 vision positive only", lambda: inputs(vis(2)),
        exp("R5", "VISION_ONLY", "PRESENT", True, ["PRESENCE_MISSING"], vision_count=2, presence_used=False)),
    one("06 R5 vision zero only", lambda: inputs(vis(0)),
        exp("R5", "VISION_ONLY", "UNKNOWN", True, ["PRESENCE_MISSING"], vision_count=0)),
    one("07 R6 radar present only", lambda: inputs(None, pres(True)),
        exp("R6", "SENSOR_ONLY", "PRESENT", True, ["VISION_MISSING"], vision_count=None, vision_used=False, zone_counts=None)),
    one("08 R6 radar absent only", lambda: inputs(None, pres(False)),
        exp("R6", "SENSOR_ONLY", "ABSENT", True, ["VISION_MISSING"], vision_count=None)),
    one("09 R7 neither source", lambda: inputs(),
        exp("R7", "UNKNOWN", "UNKNOWN", True, ["VISION_MISSING", "PRESENCE_MISSING"], vision_count=None, origin="UNKNOWN")),
    one("10 R8 vision newer", lambda: inputs(vis(3, t=99.0), pres(True, t=95.0)),
        exp("R8", "VISION_ONLY", "PRESENT", True, ["SOURCE_SKEW"], vision_count=3, vision_used=True, presence_used=False,
            fallback="R5", sensor_present=True, presence_state="FRESH")),
    one("11 R8 radar newer", lambda: inputs(vis(3, t=95.0), pres(False, t=99.0)),
        exp("R8", "SENSOR_ONLY", "ABSENT", True, ["SOURCE_SKEW"], vision_count=None, vision_used=False, presence_used=True,
            fallback="R6", vision_state="FRESH")),
    one("12 skew exactly at max", lambda: inputs(vis(3, t=99.0), pres(True, t=97.0)), R1),
    one("13 skew just beyond max", lambda: inputs(vis(3, t=99.0), pres(True, t=96.999)),
        exp("R8", "VISION_ONLY", "PRESENT", True, ["SOURCE_SKEW"], vision_count=3, fallback="R5")),
    one("14 skew unconfigured, equal timestamps", lambda: inputs(vis(3), pres(True), cfg=CFG_NO_SKEW),
        exp("R8", "VISION_ONLY", "PRESENT", True, ["SKEW_UNCONFIGURED"], vision_count=3, fallback="R5", presence_used=False,
            sensor_present=True)),
    one("14b skew unconfigured, radar newer", lambda: inputs(vis(3, t=95.0), pres(True, t=99.0), cfg=CFG_NO_SKEW),
        exp("R8", "SENSOR_ONLY", "PRESENT", True, ["SKEW_UNCONFIGURED"], vision_count=None, fallback="R6")),
    one("15 equal timestamps, configured skew", lambda: inputs(vis(3, t=97.0), pres(True, t=97.0)), R1),
    one("16 stale vision", lambda: inputs(vis(5, t=80.0), pres(True)),
        exp("R6", "SENSOR_ONLY", "PRESENT", True, ["VISION_STALE"], vision_count=None, vision_state="STALE")),
    one("17 stale radar", lambda: inputs(vis(2), pres(False, t=80.0)),
        exp("R5", "VISION_ONLY", "PRESENT", True, ["PRESENCE_STALE"], vision_count=2, sensor_present=None, presence_state="STALE")),
    one("18 FAILED radar is not False", lambda: inputs(vis(2), pres(status=SourceStatus.FAILED)),
        exp("R5", "VISION_ONLY", "PRESENT", True, ["PRESENCE_INVALID"], vision_count=2, sensor_present=None, presence_state="INVALID")),
    one("19 UNAVAILABLE camera is not zero", lambda: inputs(vis(status=SourceStatus.UNAVAILABLE), pres(True)),
        exp("R6", "SENSOR_ONLY", "PRESENT", True, ["VISION_INVALID"], vision_count=None, vision_state="INVALID")),
    Scenario("20 recovery: stale then fresh (no hidden state)", (
        Step(lambda: inputs(vis(3, t=99.0), pres(True, t=99.0), now=100.0), R1),
        Step(lambda: inputs(vis(3, t=99.0), pres(True, t=99.0), now=120.0),
             exp("R7", "UNKNOWN", "UNKNOWN", True, ["VISION_STALE", "PRESENCE_STALE"], vision_count=None, origin="UNKNOWN")),
        Step(lambda: inputs(vis(3, t=119.0), pres(True, t=119.0), now=120.0), R1))),
    Scenario("20b recovery: FAILED radar to OK radar", (
        Step(lambda: inputs(vis(2), pres(status=SourceStatus.FAILED)),
             exp("R5", "VISION_ONLY", "PRESENT", True, ["PRESENCE_INVALID"], vision_count=2)),
        Step(lambda: inputs(vis(2), pres(True)),
             exp("R1", "CONFIRMED", "PRESENT", False, vision_count=2)))),
    Scenario("20c recovery: UNAVAILABLE camera to valid camera", (
        Step(lambda: inputs(vis(status=SourceStatus.UNAVAILABLE), pres(True)),
             exp("R6", "SENSOR_ONLY", "PRESENT", True, ["VISION_INVALID"], vision_count=None)),
        Step(lambda: inputs(vis(5), pres(True)),
             exp("R1", "CONFIRMED", "PRESENT", False, vision_count=5)))),
    Scenario("21 recovery: skew then synchronised readings", (
        Step(lambda: inputs(vis(3, t=99.0), pres(True, t=95.0)),
             exp("R8", "VISION_ONLY", "PRESENT", True, ["SOURCE_SKEW"], vision_count=3, fallback="R5")),
        Step(lambda: inputs(vis(3, t=99.0), pres(True, t=99.0)), R1))),
    Scenario("22 recovery: stale environment then fresh", (
        Step(lambda: inputs(vis(3), pres(True), lux(300.0, t=10.0), [temp("t-a", 21.0, t=10.0)], hum(40.0, t=10.0)),
             dict(R1, issues=("LUX_STALE", "TEMPERATURE[t-a]_STALE", "HUMIDITY_STALE"))),
        Step(lambda: inputs(vis(3), pres(True), lux(300.0), [temp("t-a", 21.0)], hum(40.0)), dict(R1, issues=())))),
    one("23 provenance: all simulated", lambda: inputs(vis(3), pres(True), lux(), [temp("t", 20.0)], hum()), R1),
    one("24 provenance: mixed REAL (structural) + SIMULATED", lambda: inputs(vis(3, origin=Origin.REAL), pres(True)),
        dict(R1, origin="SIMULATED")),
    one("24b provenance: all REAL (structural logic test only)", lambda: inputs(vis(3, origin=Origin.REAL), pres(True, origin=Origin.REAL)),
        dict(R1, origin="REAL")),
    one("25 provenance: UNKNOWN source", lambda: inputs(vis(3), pres(True, origin=Origin.UNKNOWN)), dict(R1, origin="UNKNOWN")),
    one("26 zone preservation (radar present)", lambda: inputs(vis(result=mixed_occ()), pres(True)),
        dict(R1, vision_count=29, zone_counts={"LEFT": 8, "CENTER": 7, "RIGHT": 9}, unassigned=5, outside=3, zones_configured=True)),
    one("26b zone preservation (radar absent)", lambda: inputs(vis(result=mixed_occ()), pres(False)),
        dict(exp("R4", "CONFLICT", "UNKNOWN", True, ["DETECTIONS_WITHOUT_PRESENCE"], vision_count=29),
             zone_counts={"LEFT": 8, "CENTER": 7, "RIGHT": 9}, unassigned=5, outside=3)),
    one("26c zone preservation (radar missing)", lambda: inputs(vis(result=mixed_occ())),
        dict(exp("R5", "VISION_ONLY", "PRESENT", True, ["PRESENCE_MISSING"], vision_count=29),
             zone_counts={"LEFT": 8, "CENTER": 7, "RIGHT": 9}, unassigned=5, outside=3)),
    one("26d zone preservation (radar stale)", lambda: inputs(vis(result=mixed_occ()), pres(True, t=80.0)),
        dict(exp("R5", "VISION_ONLY", "PRESENT", True, ["PRESENCE_STALE"], vision_count=29),
             zone_counts={"LEFT": 8, "CENTER": 7, "RIGHT": 9}, unassigned=5, outside=3)),
    one("27 zones not configured", lambda: inputs(vis(4, zones=False), pres(True)),
        dict(R1, vision_count=4, zones_configured=False, zone_counts=None, unassigned=None)),
    one("28 environment independence (extreme environment, R3)",
        lambda: inputs(vis(0), pres(True), lux(0.0), [temp("t-a", -40.0), temp("t-b", 80.0)], hum(100.0)),
        exp("R3", "CONFLICT", "UNKNOWN", True, ["PRESENCE_WITHOUT_DETECTIONS"], vision_count=0)),
    one("29 undercount protection: 0 + radar present", lambda: inputs(vis(0), pres(True)),
        exp("R3", "CONFLICT", "UNKNOWN", True, ["PRESENCE_WITHOUT_DETECTIONS"], vision_count=0)),
    one("29b undercount protection: 3 + radar present", lambda: inputs(vis(3), pres(True)), R1),
    one("30 deterministic repeat", lambda: inputs(vis(3), pres(True), lux(), [temp("t", 20.0)], hum()), R1),
]


def check_step(step: Step) -> Tuple[ClassroomState, List[str]]:
    state = fuse(step.build())
    got = flat(state)
    return state, [f"{k}: expected {v!r}, got {got[k]!r}" for k, v in step.expect.items() if got[k] != v]


def scenario_correctness() -> Tuple[int, int, List[str]]:
    """(scenarios matched, scenarios total, mismatch messages). Fixtures define their expected states exactly."""
    matched, problems = 0, []
    for sc in SCENARIOS:
        bad = [f"{sc.name}, step {i}: {m}" for i, step in enumerate(sc.steps) for m in check_step(step)[1]]
        matched += not bad
        problems += bad
    return matched, len(SCENARIOS), problems


# ---- primary end-to-end audit -----------------------------------------------------------

def primary_inputs() -> Dict[str, object]:
    """Complete synthetic path: Week 5 result -> contracts -> freshness -> fusion -> ClassroomState."""
    return inputs(vis(result=mixed_occ(), t=99.0), pres(True, t=98.5), lux(320.0, t=99.0),
                  [temp("t-a", 21.5, t=98.0), temp("t-b", 22.0, t=98.0)], hum(45.0, t=97.0))


def audit_summary(args: Dict[str, object], state: ClassroomState) -> Dict[str, object]:
    """Input facts from the inputs themselves, decision facts from the final state and its telemetry record."""
    def recv(r):
        return None if r is None else r.envelope.received_at_monotonic_s
    def origin(r):
        return None if r is None else r.envelope.origin.name
    rec = fusion_decision_record(state)
    return {
        "input_origins": {"vision": origin(args["vision"]), "presence": origin(args["presence"]), "lux": origin(args["lux"]),
                          "humidity": origin(args["humidity"]), "temperatures": [origin(t) for t in args["temperatures"]]},
        "input_received_at": {"vision": recv(args["vision"]), "presence": recv(args["presence"]), "lux": recv(args["lux"]),
                              "humidity": recv(args["humidity"]), "temperatures": [recv(t) for t in args["temperatures"]]},
        "freshness": {"vision": state.vision_state.usability.name, "presence": state.presence_state.usability.name,
                      "lux": state.environment.lux.state.usability.name, "humidity": state.environment.humidity.state.usability.name,
                      "temperatures": [t.state.usability.name for t in state.environment.temperatures]},
        "vision_count": state.occupancy.vision_count, "zones": state.occupancy.zone_counts,
        "unassigned": state.occupancy.unassigned_inside_roi, "outside": state.occupancy.outside_classroom_roi,
        "radar_present": state.occupancy.sensor_present, "lux": state.environment.lux.value,
        "temperatures": [(t.sensor_id, t.value) for t in state.environment.temperatures], "humidity": state.environment.humidity.value,
        "rule_id": state.rule_id, "fallback_rule_id": state.fallback_rule_id, "evidence": state.occupancy.evidence.name,
        "fused_presence": state.occupancy.fused_presence.name, "degraded": state.degraded,
        "degraded_reasons": [r.name for r in state.degraded_reasons], "aggregate_origin": state.aggregate_origin.name,
        "environment_issues": list(state.environment.issues), "log_record": rec,
    }


# ---- fusion-stage benchmark -------------------------------------------------------------

def benchmark_configs() -> Dict[str, Dict[str, object]]:
    return {
        "A vision+radar only": inputs(vis(result=mixed_occ()), pres(True)),
        "B + lux + 1 temperature + humidity": inputs(vis(result=mixed_occ()), pres(True), lux(), [temp("t-a", 21.5)], hum()),
        "C + lux + 2 temperatures + humidity": inputs(vis(result=mixed_occ()), pres(True), lux(), [temp("t-a", 21.5), temp("t-b", 22.0)], hum()),
        "D conflict path (R3)": inputs(vis(0), pres(True), lux(), [temp("t-a", 21.5)], hum()),
        "E skew path (R8)": inputs(vis(result=mixed_occ(), t=99.0), pres(True, t=95.0), lux(), [temp("t-a", 21.5)], hum()),
        "F missing / degraded path (R7)": inputs(None, None, None, [], None),
    }


def _stats(us: List[float]) -> Dict[str, float]:
    s = sorted(us)
    return {"mean_us": st.mean(s), "median_us": st.median(s), "p95_us": s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)], "runs": len(s)}


def run_benchmark(warmup=500, runs=5000, configs=None) -> Dict[str, Dict[str, float]]:
    """Descriptive timing of fuse_classroom_state only (no thresholds, no I/O). Inputs are never modified."""
    out = {}
    for name, args in (configs if configs is not None else benchmark_configs()).items():
        for _ in range(warmup):
            fuse(args)
        samples = []
        for _ in range(runs):
            t = time.perf_counter_ns()
            fuse(args)
            samples.append((time.perf_counter_ns() - t) / 1000)
        out[name] = _stats(samples)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenarios", action="store_true")
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--out-dir", default="vision_assets/results/week6_synthetic")
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if args.scenarios:
        m, n, problems = scenario_correctness()
        print(f"synthetic fusion scenario correctness: {m} / {n} predefined scenarios matched (SIMULATED inputs)")
        for p in problems:
            print("  MISMATCH", p)
    if args.benchmark:
        r = run_benchmark()
        (out / "fusion_benchmark.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        for name, s in r.items():
            print(f"  {name:40s} mean {s['mean_us']:8.1f} us  median {s['median_us']:8.1f} us  p95 {s['p95_us']:8.1f} us  ({s['runs']} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
