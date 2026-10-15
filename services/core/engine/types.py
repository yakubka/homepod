from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SEATING = ("left", "center", "right")
ZONES = SEATING + ("professor",)

IDLE = "idle"
PREPARING = "preparing"
IN_SESSION = "in_session"
OVERRUN = "overrun"
ENDED = "ended"


@dataclass
class Occupancy:
    total: Optional[int] = None
    zones: Dict[str, Optional[int]] = field(default_factory=dict)
    professor: Optional[bool] = None
    source: str = "none"
    confidence: float = 0.0
    at: float = 0.0

    def of(self, zone: str) -> Optional[int]:
        return self.zones.get(zone)


@dataclass
class Environment:
    temperature: Optional[float] = None
    humidity: Optional[int] = None
    lux: Optional[int] = None
    at: float = 0.0


@dataclass
class Lecture:
    id: str = ""
    name: str = ""
    start: float = 0.0
    end: float = 0.0
    expected: int = 0


@dataclass
class Override:
    brightness: int
    source: str
    at: float


@dataclass
class RoomState:
    now: float
    occupancy: Occupancy = field(default_factory=Occupancy)
    environment: Environment = field(default_factory=Environment)
    lecture: Optional[Lecture] = None
    presentation: bool = False
    climate_approved: bool = False
    overrides: Dict[str, Override] = field(default_factory=dict)
    lights: Dict[str, int] = field(default_factory=dict)
    changed: Dict[str, float] = field(default_factory=dict)
    window: List[Occupancy] = field(default_factory=list)
    announced: List[str] = field(default_factory=list)
    absent_since: Optional[float] = None


@dataclass
class Action:
    kind: str
    target: str
    value: Any
    rule: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    why: str = ""

    @property
    def slot(self) -> Tuple[str, str]:
        return (self.kind, self.target)


@dataclass
class Recommendation:
    kind: str
    current: Any
    target: Any
    delta: Any
    why: str
    rule: str = ""


@dataclass
class Decision:
    action: Action
    applied: bool
    suppressed_by: str = ""
    at: float = 0.0

    def line(self) -> str:
        return self.action.why if self.applied else f"{self.action.why}, suppressed, {self.suppressed_by}"


@dataclass
class DecisionResult:
    room: str = IDLE
    actions: List[Action] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)
    decisions: List[Decision] = field(default_factory=list)
