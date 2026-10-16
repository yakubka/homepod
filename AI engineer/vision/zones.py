"""Zone assignment and occupancy accounting (Week 5, Task 1).

Pipeline (the detector runs once; everything here is pure post-processing):

    Frame -> detector -> Detection list -> classroom ROI -> zone assignment -> accounting

SEMANTICS: this module counts DETECTED PERSONS. The detector finds COCO
"person"; it cannot tell a student from a teacher, visitor or staff member.
`occupancy_count` is therefore an operational proxy for "student count": the
number of detected persons whose anchor lies in the classroom ROI, per frame,
not a unique-person count over time and not a semantic classification. Zone
counts also inherit the detector's known dense-classroom undercounting (Week 4
benchmark): a person the detector never finds cannot be placed in any zone.

Every detection receives exactly ONE auditable outcome:

    a ZONE  |  UNASSIGNED (inside the classroom ROI but in no zone)  |  OUTSIDE (outside the ROI)

Rules (all reuse vision/roi.py; no geometry is duplicated here):
  1. The anchor is the bottom-centre of the box, and boundaries are inclusive,
     exactly as in vision/roi.py.
  2. If a classroom ROI is given and the anchor is outside it, the detection is
     OUTSIDE and no zone is consulted. No ROI means the full frame is classroom.
  3. Otherwise the zones are tested IN ORDER and the FIRST zone whose ROI
     contains the anchor owns the detection. Inclusive boundaries mean a point
     on a shared border satisfies both neighbours; the ZoneSet order breaks the
     tie, so ownership comes from the order, never from a hard-coded zone name.
     Overlapping zones are resolved the same way (first match wins); geometry
     validation belongs to the configuration layer (Week 5 Task 2).
  4. Inside the ROI but in no zone: UNASSIGNED. Nothing is ever dropped.
With no zones supplied, ROI classification still runs and every detection
inside the classroom is UNASSIGNED.

No LEFT / CENTER / RIGHT geometry exists: the physical zones are uncalibrated
(PENDING HARDWARE), so these classes carry no coordinates of their own.
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from vision.detector import Detection
from vision.roi import Roi, RoiError, anchor, in_roi

OUTSIDE = "OUTSIDE"
UNASSIGNED = "UNASSIGNED"
_RESERVED_IDS = (OUTSIDE, UNASSIGNED)


class ZoneError(ValueError):
    """Invalid zone or zone set."""


class ZoneAccountingError(ValueError):
    """A ZoneOccupancyResult whose numbers or buckets are inconsistent."""


@dataclass(frozen=True)
class Zone:
    """One named region of the classroom. The canonical IDs are LEFT, CENTER, RIGHT."""

    zone_id: str
    name: str
    roi: Roi

    def __post_init__(self) -> None:
        if not isinstance(self.zone_id, str) or not self.zone_id.strip():
            raise ZoneError("zone_id must be a non-empty string")
        if self.zone_id in _RESERVED_IDS:
            raise ZoneError(f"zone_id {self.zone_id!r} is reserved for an assignment bucket")
        if not isinstance(self.roi, Roi):
            raise ZoneError(f"zone {self.zone_id!r} needs a vision.roi.Roi, got {type(self.roi).__name__}")


@dataclass(frozen=True)
class ZoneSet:
    """Ordered zones. The order is the tie-break priority (earlier wins)."""

    zones: Tuple[Zone, ...] = ()

    def __post_init__(self) -> None:
        zs = tuple(self.zones)
        if not all(isinstance(z, Zone) for z in zs):
            raise ZoneError("a ZoneSet contains only Zone objects")
        ids = [z.zone_id for z in zs]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ZoneError(f"duplicate zone ids: {dup}")
        object.__setattr__(self, "zones", zs)

    def __iter__(self) -> Iterator[Zone]:
        return iter(self.zones)

    def __len__(self) -> int:
        return len(self.zones)

    @property
    def ids(self) -> Tuple[str, ...]:
        return tuple(z.zone_id for z in self.zones)


@dataclass(frozen=True)
class ZoneOccupancyResult:
    """Auditable outcome of assigning one frame's detections. Validates itself on construction."""

    total_detected: int
    inside_classroom_roi: int
    outside_classroom_roi: int
    zone_counts: Tuple[Tuple[str, int], ...]                   # (zone_id, count) in ZoneSet order
    unassigned_inside_roi: int
    zone_detections: Tuple[Tuple[str, Tuple[Detection, ...]], ...]
    unassigned_detections: Tuple[Detection, ...]
    outside_detections: Tuple[Detection, ...]
    zones_configured: bool

    def __post_init__(self) -> None:
        def fail(msg):
            raise ZoneAccountingError(msg)

        for name in ("total_detected", "inside_classroom_roi", "outside_classroom_roi", "unassigned_inside_roi"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                fail(f"{name} must be a non-negative integer, got {v!r}")
        ids = [i for i, _ in self.zone_counts]
        if len(set(ids)) != len(ids):
            fail("duplicate zone ids in zone_counts")
        if ids != [i for i, _ in self.zone_detections]:
            fail("zone_counts and zone_detections list different zones or a different order")
        if not self.zones_configured and ids:
            fail("zone buckets present although zones_configured is False")
        for (zid, n), (_, dets) in zip(self.zone_counts, self.zone_detections):
            if not isinstance(n, int) or n < 0 or n != len(dets):
                fail(f"zone {zid!r}: count {n!r} does not match its {len(dets)} detections")
        if self.unassigned_inside_roi != len(self.unassigned_detections):
            fail("unassigned_inside_roi does not match unassigned_detections")
        if self.outside_classroom_roi != len(self.outside_detections):
            fail("outside_classroom_roi does not match outside_detections")
        if self.inside_classroom_roi + self.outside_classroom_roi != self.total_detected:
            fail("inside + outside must equal total_detected")
        if sum(n for _, n in self.zone_counts) + self.unassigned_inside_roi != self.inside_classroom_roi:
            fail("sum(zone_counts) + unassigned must equal inside_classroom_roi")
        n_buckets = sum(len(d) for _, d in self.zone_detections) + len(self.unassigned_detections) + len(self.outside_detections)
        if n_buckets != self.total_detected:
            fail("every detection must appear in exactly one bucket")

    @property
    def occupancy_count(self) -> int:
        """Detected persons inside the classroom ROI. Per frame; NOT a unique-person count over
        time and NOT a semantic student count (the detector cannot tell students from staff)."""
        return self.inside_classroom_roi

    def count(self, zone_id: str) -> int:
        for zid, n in self.zone_counts:
            if zid == zone_id:
                return n
        raise KeyError(zone_id)

    def detections_in(self, zone_id: str) -> Tuple[Detection, ...]:
        for zid, dets in self.zone_detections:
            if zid == zone_id:
                return dets
        raise KeyError(zone_id)


def assign_detections(
    detections: Sequence[Detection],
    classroom_roi: Optional[Roi] = None,
    zones: Optional[ZoneSet] = None,
) -> ZoneOccupancyResult:
    """Place every detection in exactly one bucket (zone, UNASSIGNED or OUTSIDE).

    The original Detection objects are placed in the buckets; none are copied or modified.
    """
    zone_list = tuple(zones) if zones is not None else ()
    by_zone: Dict[str, List[Detection]] = {z.zone_id: [] for z in zone_list}
    unassigned: List[Detection] = []
    outside: List[Detection] = []
    for d in detections:
        if not in_roi(d, classroom_roi):                      # step 1: classroom ROI first
            outside.append(d)
            continue
        a = anchor(d)                                         # step 2: first zone in order owns it
        for z in zone_list:
            if z.roi.contains(*a):
                by_zone[z.zone_id].append(d)
                break
        else:                                                 # step 3: inside the classroom, in no zone
            unassigned.append(d)
    inside = len(detections) - len(outside)
    return ZoneOccupancyResult(
        total_detected=len(detections),
        inside_classroom_roi=inside,
        outside_classroom_roi=len(outside),
        zone_counts=tuple((z.zone_id, len(by_zone[z.zone_id])) for z in zone_list),
        unassigned_inside_roi=len(unassigned),
        zone_detections=tuple((z.zone_id, tuple(by_zone[z.zone_id])) for z in zone_list),
        unassigned_detections=tuple(unassigned),
        outside_detections=tuple(outside),
        zones_configured=len(zone_list) > 0,
    )


# ======================================================================================
# Zone configuration and geometry validation (Week 5, Task 2)
#
# This is a safety / usability layer. It does NOT change the assignment rules above:
# even with imperfect geometry, assign_detections() still gives every detection exactly
# one bucket (first match wins; the classroom ROI is checked first).
#
# The core Zone / ZoneSet stay generic. Only this loader requires the Week 5 contract:
# exactly the zones LEFT, CENTER, RIGHT, applied in that canonical priority regardless
# of the order in config/settings.py.
#
# Validation, honestly stated:
#   - Classroom containment: every zone vertex (rectangle corner or polygon vertex)
#     must lie inside or on the classroom ROI boundary (no ROI: the full frame [0,1]^2,
#     which Roi already guarantees). For a CONCAVE classroom ROI, vertices inside do not
#     prove that the edges between them stay inside; this is not claimed.
#   - Overlap (shared borders and shared corners are allowed). Rejected:
#       * identical vertex sets (exact duplicate geometry);
#       * a zone vertex STRICTLY inside another zone;
#       * proper edge crossings;
#       * a sample point (the vertex average) strictly inside BOTH zones, which proves
#         area overlap and catches nested zones whose vertices all touch the border;
#       * two rectangles whose interiors overlap (exact interval test).
#     NOT guaranteed to be detected: overlap involving a polygon that shares collinear
#     edges with another zone without any of the situations above (for example two
#     4-point polygons that overlap in a band with aligned edges). This is not a
#     computational-geometry proof. It is safe because first-match assignment cannot
#     double-count.
# ======================================================================================

CANONICAL_ZONE_IDS = ("LEFT", "CENTER", "RIGHT")      # also the priority order
_KINDS = ("rectangle", "polygon")
_EPS = 1e-12


class ZoneConfigError(ValueError):
    """The zone configuration is invalid."""


def _vertices(roi: Roi):
    if roi.kind == "rectangle":
        (x1, y1), (x2, y2) = roi.points
        return ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
    return roi.points


def _orient(p, q, r) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _on_segment(a, b, p) -> bool:
    return (abs(_orient(a, b, p)) <= _EPS and min(a[0], b[0]) - _EPS <= p[0] <= max(a[0], b[0]) + _EPS
            and min(a[1], b[1]) - _EPS <= p[1] <= max(a[1], b[1]) + _EPS)


def _on_boundary(roi: Roi, p) -> bool:
    v = _vertices(roi)
    return any(_on_segment(v[i], v[(i + 1) % len(v)], p) for i in range(len(v)))


def _strictly_inside(roi: Roi, p) -> bool:
    """Inside and not on the border. Roi.contains is inclusive, so the border test is added here."""
    return roi.contains(*p) and not _on_boundary(roi, p)


def _proper_cross(a, b, c, d) -> bool:
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0 and min(abs(o1), abs(o2), abs(o3), abs(o4)) > _EPS


def _vertex_mean(roi: Roi):
    v = _vertices(roi)
    return (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v))


def _overlap_reason(za: Zone, zb: Zone) -> Optional[str]:
    a, b = za.roi, zb.roi
    va, vb = _vertices(a), _vertices(b)
    if frozenset(va) == frozenset(vb):
        return "identical geometry"
    for v in va:
        if _strictly_inside(b, v):
            return f"a vertex of {za.zone_id} lies strictly inside {zb.zone_id}"
    for v in vb:
        if _strictly_inside(a, v):
            return f"a vertex of {zb.zone_id} lies strictly inside {za.zone_id}"
    for i in range(len(va)):
        for j in range(len(vb)):
            if _proper_cross(va[i], va[(i + 1) % len(va)], vb[j], vb[(j + 1) % len(vb)]):
                return "their edges cross"
    for m in (_vertex_mean(a), _vertex_mean(b)):
        if _strictly_inside(a, m) and _strictly_inside(b, m):
            return "a point strictly inside both zones exists"
    if a.kind == "rectangle" and b.kind == "rectangle":
        (ax1, ay1), (ax2, ay2) = a.points
        (bx1, by1), (bx2, by2) = b.points
        if min(ax2, bx2) - max(ax1, bx1) > _EPS and min(ay2, by2) - max(ay1, by1) > _EPS:
            return "the rectangle interiors overlap"
    return None


def _parse_zone(index: int, definition) -> Tuple[str, str, Roi]:
    where = f"zone definition #{index + 1}"
    try:
        zone_id, name, kind, points = definition
    except (TypeError, ValueError):
        raise ZoneConfigError(f"{where} must be (zone_id, name, kind, points)") from None
    if not isinstance(zone_id, str):
        raise ZoneConfigError(f"{where}: zone_id must be a string, got {zone_id!r}")
    if not isinstance(name, str) or not name.strip():
        raise ZoneConfigError(f"{where} ({zone_id}): name must be a non-empty string")
    if kind not in _KINDS:
        raise ZoneConfigError(f"{where} ({zone_id}): unsupported geometry kind {kind!r}; use one of {_KINDS}")
    try:
        pts = [tuple(p) for p in points]
        if kind == "rectangle":
            if len(pts) != 2:
                raise ZoneConfigError(f"{where} ({zone_id}): a rectangle needs exactly 2 points ((x1, y1), (x2, y2)), got {len(pts)}")
            roi = Roi.rectangle(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
        else:
            roi = Roi.polygon(pts)
    except ZoneConfigError:
        raise
    except RoiError as exc:
        raise ZoneConfigError(f"{where} ({zone_id}): invalid geometry: {exc}") from None
    except (TypeError, ValueError, IndexError):
        raise ZoneConfigError(f"{where} ({zone_id}): malformed point data {points!r}") from None
    return zone_id, name, roi


def build_zone_set(definitions, classroom_roi: Optional[Roi] = None) -> ZoneSet:
    """Validate zone definitions and return the canonical LEFT, CENTER, RIGHT ZoneSet."""
    try:
        defs = list(definitions)
    except TypeError:
        raise ZoneConfigError("ZONES must be a sequence of (zone_id, name, kind, points) entries") from None
    if not defs:
        raise ZoneConfigError("zones are enabled but ZONES defines no zones")
    parsed = [_parse_zone(i, d) for i, d in enumerate(defs)]
    ids = [p[0] for p in parsed]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ZoneConfigError(f"duplicate zone ids: {dup}")
    unexpected = [i for i in ids if i not in CANONICAL_ZONE_IDS]
    if unexpected:
        reserved = [i for i in unexpected if i in _RESERVED_IDS]
        note = f" ({reserved} are reserved bucket names)" if reserved else ""
        raise ZoneConfigError(f"unexpected zone ids {unexpected}{note}; exactly {list(CANONICAL_ZONE_IDS)} are required")
    missing = [i for i in CANONICAL_ZONE_IDS if i not in ids]
    if missing:
        raise ZoneConfigError(f"missing zone ids {missing}; exactly {list(CANONICAL_ZONE_IDS)} are required")
    by_id = {p[0]: p for p in parsed}
    zones = tuple(Zone(zid, by_id[zid][1], by_id[zid][2]) for zid in CANONICAL_ZONE_IDS)   # canonical priority
    if classroom_roi is not None:
        for z in zones:
            for v in _vertices(z.roi):
                if not classroom_roi.contains(*v):
                    raise ZoneConfigError(f"zone {z.zone_id} vertex {v} lies outside the classroom ROI")
    for i in range(len(zones)):
        for j in range(i + 1, len(zones)):
            reason = _overlap_reason(zones[i], zones[j])
            if reason:
                raise ZoneConfigError(f"zones {zones[i].zone_id} and {zones[j].zone_id} overlap: {reason}")
    return ZoneSet(zones)


def zones_from_settings(classroom_roi: Optional[Roi] = None) -> Optional[ZoneSet]:
    """Build the zones from config/settings.py, or None when disabled (the default).

    Pass the configured classroom ROI (vision.roi.roi_from_settings()) so containment can be
    checked; None means the full frame. When this returns None, assign_detections() reports every
    detection inside the classroom as UNASSIGNED: no second counting path exists.
    """
    from config import settings
    if not getattr(settings, "ZONES_ENABLED", False):
        return None
    return build_zone_set(settings.ZONES, classroom_roi)
