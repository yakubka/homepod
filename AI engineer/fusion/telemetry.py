"""Week 6, Task 3: audit / log representation of a ClassroomState.

A THIN layer outside the pure fusion function. It consumes the FINAL ClassroomState and never recomputes a
fusion decision; it never changes the state; fusion correctness does not depend on logging succeeding.

  fusion_decision_record(state) -> dict    pure record builder (plain values, fixed key order)
  format_fusion_decision(record) -> str    pure key=value line in the project's logging style
  log_fusion_decision(state, logger)       emitter with an injected logger; a logging failure returns False

Nothing image-related or personal is recorded: only rule ids, states, counts, flags and origins.
"""

from typing import Dict

from fusion.fuse import ClassroomState


def _fmt(v) -> str:
    return "none" if v is None else str(v).lower() if isinstance(v, bool) else str(v)


def fusion_decision_record(state: ClassroomState) -> Dict[str, object]:
    occ = state.occupancy
    env = state.environment
    vs, ps = state.vision_state, state.presence_state
    return {
        "event": "fusion_decision",
        "rule_id": state.rule_id,
        "fallback_rule_id": state.fallback_rule_id,
        "fallback_basis": state.fallback_basis.value if state.fallback_basis is not None else None,
        "evidence": occ.evidence.value,
        "fused_presence": occ.fused_presence.value,
        "degraded": state.degraded,                       # occupancy-evidence quality only; environment health is separate
        "degraded_reasons": ",".join(r.value for r in state.degraded_reasons) or None,
        "vision_state": vs.usability.value,
        "vision_status": vs.source_status.value if vs.source_status else None,
        "vision_age_s": vs.age_s,
        "vision_origin": vs.origin.value if vs.origin else None,
        "presence_state": ps.usability.value,
        "presence_status": ps.source_status.value if ps.source_status else None,
        "presence_age_s": ps.age_s,
        "presence_origin": ps.origin.value if ps.origin else None,
        "vision_count": occ.vision_count,
        "raw_vision_count": occ.raw_vision_count,
        "sensor_present": occ.sensor_present,
        "raw_presence": occ.raw_presence,
        "vision_used": occ.vision_used,
        "presence_used": occ.presence_used,
        "zones_configured": occ.zones_configured,
        "zones": None if occ.zone_counts is None else ",".join(f"{z}={n}" for z, n in occ.zone_counts),
        "unassigned": occ.unassigned_inside_roi,
        "outside": occ.outside_classroom_roi,
        "lux": env.lux.value,
        "lux_state": env.lux.state.usability.value,
        "temperatures": ",".join(f"{t.sensor_id}:{t.state.usability.value}:{t.value}" for t in env.temperatures) or None,
        "humidity": env.humidity.value,
        "humidity_state": env.humidity.state.usability.value,
        "environment_issues": ",".join(env.issues) or None,
        "aggregate_origin": state.aggregate_origin.value,
    }


def format_fusion_decision(record: Dict[str, object]) -> str:
    return " ".join(f"{k}={_fmt(v)}" for k, v in record.items())


def log_fusion_decision(state: ClassroomState, logger) -> bool:
    """Emit one INFO line through an injected logger. Returns False if logging failed; the state is never touched."""
    try:
        logger.info("%s", format_fusion_decision(fusion_decision_record(state)))
        return True
    except Exception:
        return False
