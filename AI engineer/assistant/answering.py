"""Deterministic answering foundation (VESTA, Team Leader midterm). TEXT in, TEXT out: no audio, no TTS, no LLM.

    recognized text -> narrow normalisation -> exact match against the frozen AI-1 question set (primary questions and
    their accepted variants) -> authoritative supplied state -> a truthful text answer + metadata

STATUS: SOFTWARE-VALIDATED on SIMULATED fixtures. It says nothing about classroom performance, recognition quality or
any live sensor: no live sensor interface, schedule source, professor-arrival state or announcement path exists yet.

SCOPE. Only the ten frozen AI-1 v1 question families are understood (evaluation/ai_question_test_set_v1.json is the
behavioural oracle; the phrases below are copied from it and a test keeps them identical). Anything else is
UNSUPPORTED_QUESTION. There is no semantic guessing, no keyword spotting and no prefix stripping: the whole normalised
utterance must equal a known phrase. Wake-prefix removal and transcription correction belong to a later stage.

STATE. The room answers read the fusion layer's ClassroomState and respect its freshness rules: a value is used only when
its SourceState is FRESH, it is not None, and its unit is the expected one. Missing, stale, invalid and unconfigured
sources all read "unavailable"; nothing is substituted (not a raw detection, not an expected count, not an average of
sensors). People count is OccupancyState.vision_count (the detected-person proxy; it undercounts dense rooms); the
diagnostic raw_vision_count is never used. Provenance (SIMULATED / REAL / UNKNOWN) is passed through from the fusion
SourceState and is never upgraded.

NO AUTHORITATIVE SOURCE EXISTS YET for: the current lecture, expected attendance, the next class, professor arrival, or
announcement playback. Therefore:
  * lecture / attendance / next class answer "unavailable" unless the caller passes an explicit SimulatedScheduleContext
    (a test fixture, always labelled SIMULATED; there is no production schedule);
  * professor arrival is ALWAYS "unavailable": no input can make it answer, and it is never inferred from occupancy,
    detections or the schedule;
  * "what can you do" and "can you make an announcement" follow the caller's CapabilityDeclaration, never a fixed list.
A room question whose capability is not declared is UNSUPPORTED_QUESTION, so the engine never answers something
it has just said it cannot do.

PRIVACY. Only structural fields are logged (status, intent, question id, source, input length). The input text and the
answer text never appear in a log line.
"""

import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple

from config.logging_config import setup_logging
from fusion.contracts import Origin, Unit, Usability
from fusion.fuse import ClassroomState

logger = setup_logging()


# ---------------------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------------------

class AnswerError(Exception):
    """Base class for answering problems that are the caller's or the implementation's fault (never a user's question)."""


class AnswerContextError(AnswerError, TypeError):
    """The request or the context handed to the engine has the wrong type or content."""


class AnswerContractError(AnswerError):
    """An AnswerResult or an engine broke the answering contract."""


# ---------------------------------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------------------------------

class Intent(Enum):
    ROOM_PEOPLE_COUNT = "ROOM_PEOPLE_COUNT"
    ROOM_TEMPERATURE = "ROOM_TEMPERATURE"
    ROOM_HUMIDITY = "ROOM_HUMIDITY"
    ROOM_BRIGHTNESS = "ROOM_BRIGHTNESS"
    LECTURE_CURRENT = "LECTURE_CURRENT"
    LECTURE_EXPECTED_ATTENDANCE = "LECTURE_EXPECTED_ATTENDANCE"
    PROFESSOR_ARRIVAL = "PROFESSOR_ARRIVAL"
    CAPABILITIES = "CAPABILITIES"
    NEXT_CLASS = "NEXT_CLASS"
    ANNOUNCEMENT_CAPABILITY = "ANNOUNCEMENT_CAPABILITY"


class AnswerStatus(Enum):
    ANSWERED = "ANSWERED"
    INFORMATION_UNAVAILABLE = "INFORMATION_UNAVAILABLE"
    UNSUPPORTED_QUESTION = "UNSUPPORTED_QUESTION"
    INVALID_INPUT = "INVALID_INPUT"


class AnswerSource(Enum):
    """Where an answer's information came from. Never stronger than the evidence."""

    SIMULATED = "SIMULATED"              # a simulated fixture / simulator (every test)
    REAL = "REAL"                        # fusion says the value was measured by physical hardware
    UNKNOWN_ORIGIN = "UNKNOWN_ORIGIN"    # provenance not established
    DECLARATION = "DECLARATION"          # the caller's capability declaration
    UNAVAILABLE = "UNAVAILABLE"          # no usable information
    NONE = "NONE"                        # no information was involved (unsupported / invalid input)


class Capability(Enum):
    PEOPLE_COUNT = "PEOPLE_COUNT"
    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    BRIGHTNESS = "BRIGHTNESS"
    LECTURE_INFORMATION = "LECTURE_INFORMATION"
    ANNOUNCEMENTS = "ANNOUNCEMENTS"


_ROOM_CAPABILITY = {
    Intent.ROOM_PEOPLE_COUNT: Capability.PEOPLE_COUNT,
    Intent.ROOM_TEMPERATURE: Capability.TEMPERATURE,
    Intent.ROOM_HUMIDITY: Capability.HUMIDITY,
    Intent.ROOM_BRIGHTNESS: Capability.BRIGHTNESS,
}

# AI-1 question ids (the frozen v1 numbering)
QUESTION_ID = {
    Intent.ROOM_PEOPLE_COUNT: "Q1", Intent.ROOM_TEMPERATURE: "Q2", Intent.ROOM_HUMIDITY: "Q3", Intent.ROOM_BRIGHTNESS: "Q4",
    Intent.LECTURE_CURRENT: "Q5", Intent.LECTURE_EXPECTED_ATTENDANCE: "Q6", Intent.PROFESSOR_ARRIVAL: "Q7",
    Intent.CAPABILITIES: "Q8", Intent.NEXT_CLASS: "Q9", Intent.ANNOUNCEMENT_CAPABILITY: "Q10",
}

# The frozen AI-1 v1 primary questions and accepted variants, ALREADY NORMALISED (lower case, no punctuation).
# tests/test_answering.py checks that this table equals the JSON oracle exactly.
PHRASES: Dict[Intent, Tuple[str, ...]] = {
    Intent.ROOM_PEOPLE_COUNT: ("how many people are in the room", "how many people are here", "how many people are in this room"),
    Intent.ROOM_TEMPERATURE: ("what is the temperature in the room", "what is the room temperature", "how warm is it in here"),
    Intent.ROOM_HUMIDITY: ("what is the humidity in the room", "what is the room humidity", "how humid is it in here"),
    Intent.ROOM_BRIGHTNESS: ("how bright is the room", "how much light is in the room", "what is the light level"),
    Intent.LECTURE_CURRENT: ("what lecture is scheduled now", "what lecture is on now", "what class is scheduled right now"),
    Intent.LECTURE_EXPECTED_ATTENDANCE: ("how many students are expected for this lecture", "how many students are expected",
                                         "how many students should be here"),
    Intent.PROFESSOR_ARRIVAL: ("has the professor arrived yet", "is the professor here yet", "has the lecturer arrived"),
    Intent.CAPABILITIES: ("what can you do", "what are you able to do", "how can you help"),
    Intent.NEXT_CLASS: ("what time is the next class", "when is the next class", "when does the next lecture start"),
    Intent.ANNOUNCEMENT_CAPABILITY: ("can you make an announcement", "can you make announcements", "are you able to announce something"),
}

# Missing-data answers: the AI-1 example wordings (a test keeps them identical to the JSON).
UNAVAILABLE_TEXT = {
    Intent.ROOM_PEOPLE_COUNT: "I don't have a current people count.",
    Intent.ROOM_TEMPERATURE: "Temperature information is not available.",
    Intent.ROOM_HUMIDITY: "Humidity information is not available.",
    Intent.ROOM_BRIGHTNESS: "Light level information is not available.",
    Intent.LECTURE_CURRENT: "Schedule information is not available.",
    Intent.LECTURE_EXPECTED_ATTENDANCE: "Expected student numbers are not available.",
    Intent.PROFESSOR_ARRIVAL: "I don't have professor arrival information.",
    Intent.NEXT_CLASS: "Schedule information is not available.",
}
UNSUPPORTED_TEXT = "I can't answer that yet."
INVALID_TEXT = "I didn't catch a question."

REASON_NO_STATE = "NO_CLASSROOM_STATE"
REASON_NO_VALUE = "NO_FRESH_VALID_VALUE"
REASON_NO_SCHEDULE = "NO_SCHEDULE_SOURCE"
REASON_NO_ARRIVAL = "NO_AUTHORITATIVE_ARRIVAL_STATE"
REASON_NOT_DECLARED = "CAPABILITY_NOT_DECLARED"
REASON_NO_MATCH = "NO_MATCHING_QUESTION"
REASON_EMPTY = "EMPTY_OR_NON_TEXT_INPUT"


# ---------------------------------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityDeclaration:
    """What the build under test can really do, stated by the caller. The default declares nothing."""

    capabilities: frozenset = frozenset()

    def __post_init__(self) -> None:
        caps = frozenset(self.capabilities)
        if not all(isinstance(c, Capability) for c in caps):
            raise AnswerContextError("CapabilityDeclaration needs Capability members")
        object.__setattr__(self, "capabilities", caps)

    @classmethod
    def none(cls) -> "CapabilityDeclaration":
        return cls(frozenset())

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def announcement_available(self) -> bool:
        return Capability.ANNOUNCEMENTS in self.capabilities


@dataclass(frozen=True)
class SimulatedScheduleContext:
    """A TEST fixture standing in for a schedule. There is no production schedule; every answer built from this is SIMULATED."""

    current_lecture: Optional[str] = None
    expected_attendance: Optional[int] = None
    next_class_time: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("current_lecture", "next_class_time"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise AnswerContextError(f"SimulatedScheduleContext.{name} must be a non-empty string or None, got {value!r}")
        n = self.expected_attendance
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
            raise AnswerContextError(f"SimulatedScheduleContext.expected_attendance must be an int >= 0 or None, got {n!r}")


@dataclass(frozen=True)
class AnswerContext:
    """Everything the engine may use besides the question: the classroom state, the declaration and an optional test schedule."""

    classroom_state: Optional[ClassroomState] = None
    capabilities: CapabilityDeclaration = field(default_factory=CapabilityDeclaration.none)
    schedule: Optional[SimulatedScheduleContext] = None

    def __post_init__(self) -> None:
        if self.classroom_state is not None and not isinstance(self.classroom_state, ClassroomState):
            raise AnswerContextError(f"classroom_state must be a ClassroomState or None, got {type(self.classroom_state).__name__}")
        if not isinstance(self.capabilities, CapabilityDeclaration):
            raise AnswerContextError("capabilities must be a CapabilityDeclaration")
        if self.schedule is not None and not isinstance(self.schedule, SimulatedScheduleContext):
            raise AnswerContextError("schedule must be a SimulatedScheduleContext (no production schedule exists) or None")


@dataclass(frozen=True)
class AnswerRequest:
    text: object                 # the recognised text; anything that is not a usable str gives INVALID_INPUT, not an exception
    context: AnswerContext

    def __post_init__(self) -> None:
        if not isinstance(self.context, AnswerContext):
            raise AnswerContextError("context must be an AnswerContext")


# ---------------------------------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AnswerFact:
    """One value stated in an answer, so callers and tests never have to parse the text."""

    name: str
    value: object
    unit: Optional[str] = None


@dataclass(frozen=True)
class AnswerResult:
    status: AnswerStatus
    text: str
    intent: Optional[Intent]
    question_id: Optional[str]
    source: AnswerSource
    reason_code: Optional[str] = None
    facts: Tuple[AnswerFact, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, AnswerStatus) or not isinstance(self.source, AnswerSource):
            raise AnswerContractError("status and source must be enum members")
        if not isinstance(self.text, str) or not self.text.strip():
            raise AnswerContractError("an answer always has text")
        if (self.intent is None) != (self.question_id is None):
            raise AnswerContractError("intent and question_id go together")
        if self.intent is not None and QUESTION_ID[self.intent] != self.question_id:
            raise AnswerContractError("question_id does not belong to the intent")
        s = self.status
        if s is AnswerStatus.ANSWERED:
            if self.intent is None or not self.facts or self.source in (AnswerSource.UNAVAILABLE, AnswerSource.NONE):
                raise AnswerContractError("ANSWERED needs an intent, facts and a real source")
        else:
            if self.facts:
                raise AnswerContractError("only an ANSWERED result carries facts")
            if s is AnswerStatus.INFORMATION_UNAVAILABLE and (self.intent is None or self.source is not AnswerSource.UNAVAILABLE):
                raise AnswerContractError("INFORMATION_UNAVAILABLE needs an intent and source UNAVAILABLE")
            if s is AnswerStatus.INVALID_INPUT and (self.intent is not None or self.source is not AnswerSource.NONE):
                raise AnswerContractError("INVALID_INPUT has no intent and source NONE")
            if s is AnswerStatus.UNSUPPORTED_QUESTION and self.source is not AnswerSource.NONE:
                raise AnswerContractError("UNSUPPORTED_QUESTION has source NONE")


# ---------------------------------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------------------------------

def normalize_question(text: str) -> str:
    """Trim, lower-case, turn punctuation into spaces (apostrophes stay) and collapse whitespace. Nothing else."""
    t = text.replace("’", "'").replace("‘", "'").lower()
    t = re.sub(r"[^\w\s']|_", " ", t)
    t = re.sub(r"(?<!\w)'|'(?!\w)", " ", t)           # an apostrophe survives only inside a word (what's), never alone or as a quote
    return " ".join(t.split())


def _build_phrase_index() -> Dict[str, Intent]:
    index: Dict[str, Intent] = {}
    for intent, phrases in PHRASES.items():
        for phrase in phrases:
            if phrase != normalize_question(phrase) or phrase in index:
                raise AnswerContractError(f"phrase table is not normalised or has a duplicate: {phrase!r}")
            index[phrase] = intent
    return index


_PHRASE_INDEX = _build_phrase_index()


def match_intent(text: str) -> Optional[Intent]:
    """The Intent whose phrase equals the whole normalised text, else None."""
    return _PHRASE_INDEX.get(normalize_question(text))


# ---------------------------------------------------------------------------------------------------
# answering
# ---------------------------------------------------------------------------------------------------

def _source_of(origins: Iterable[Optional[Origin]]) -> AnswerSource:
    """Conservative aggregate: any unknown -> UNKNOWN_ORIGIN; else any simulated -> SIMULATED; else REAL."""
    origins = list(origins)
    if not origins or any(o is None or o is Origin.UNKNOWN for o in origins):
        return AnswerSource.UNKNOWN_ORIGIN
    if any(o is Origin.SIMULATED for o in origins):
        return AnswerSource.SIMULATED
    return AnswerSource.REAL


def _fmt(value) -> str:
    """Whole numbers without a decimal point; otherwise one decimal place."""
    v = float(value)
    return str(int(v)) if v == int(v) else f"{v:.1f}"


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unavailable(intent: Intent, reason: str) -> AnswerResult:
    return AnswerResult(AnswerStatus.INFORMATION_UNAVAILABLE, UNAVAILABLE_TEXT[intent], intent, QUESTION_ID[intent], AnswerSource.UNAVAILABLE, reason)


def _answered(intent: Intent, text: str, source: AnswerSource, facts: Tuple[AnswerFact, ...]) -> AnswerResult:
    return AnswerResult(AnswerStatus.ANSWERED, text, intent, QUESTION_ID[intent], source, None, facts)


def _fresh_values(values, unit: Unit):
    """EnvironmentValues that are FRESH, numeric and in the expected unit; anything else is not usable."""
    return [v for v in values if v.state.usability is Usability.FRESH and _is_number(v.value) and v.unit is unit]


def _answer_people(ctx: AnswerContext) -> AnswerResult:
    state = ctx.classroom_state
    if state is None:
        return _unavailable(Intent.ROOM_PEOPLE_COUNT, REASON_NO_STATE)
    occ = state.occupancy
    n = occ.vision_count
    if not occ.vision_used or state.vision_state.usability is not Usability.FRESH or isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return _unavailable(Intent.ROOM_PEOPLE_COUNT, REASON_NO_VALUE)
    text = "There is 1 person in the room." if n == 1 else f"There are {n} people in the room."
    return _answered(Intent.ROOM_PEOPLE_COUNT, text, _source_of([state.vision_state.origin]), (AnswerFact("people_count", n, "people"),))


def _answer_temperature(ctx: AnswerContext) -> AnswerResult:
    state = ctx.classroom_state
    if state is None:
        return _unavailable(Intent.ROOM_TEMPERATURE, REASON_NO_STATE)
    fresh = _fresh_values(state.environment.temperatures, Unit.CELSIUS)
    if not fresh:
        return _unavailable(Intent.ROOM_TEMPERATURE, REASON_NO_VALUE)
    facts = tuple(AnswerFact("temperature", v.value, "degC") for v in fresh)
    readings = [f"{_fmt(v.value)} °C" for v in fresh]          # several sensors are listed, never averaged
    if len(readings) == 1:
        text = f"The temperature in the room is {readings[0]}."
    else:
        text = "The temperature sensors read " + ", ".join(readings[:-1]) + f" and {readings[-1]}."
    return _answered(Intent.ROOM_TEMPERATURE, text, _source_of(v.state.origin for v in fresh), facts)


def _answer_humidity(ctx: AnswerContext) -> AnswerResult:
    state = ctx.classroom_state
    if state is None:
        return _unavailable(Intent.ROOM_HUMIDITY, REASON_NO_STATE)
    fresh = _fresh_values([state.environment.humidity], Unit.PERCENT_RH)
    if not fresh:
        return _unavailable(Intent.ROOM_HUMIDITY, REASON_NO_VALUE)
    v = fresh[0]
    return _answered(Intent.ROOM_HUMIDITY, f"The relative humidity in the room is {_fmt(v.value)}%.", _source_of([v.state.origin]),
                     (AnswerFact("humidity", v.value, "%RH"),))


def _answer_brightness(ctx: AnswerContext) -> AnswerResult:
    state = ctx.classroom_state
    if state is None:
        return _unavailable(Intent.ROOM_BRIGHTNESS, REASON_NO_STATE)
    fresh = _fresh_values([state.environment.lux], Unit.LUX)
    if not fresh:
        return _unavailable(Intent.ROOM_BRIGHTNESS, REASON_NO_VALUE)
    v = fresh[0]
    return _answered(Intent.ROOM_BRIGHTNESS, f"The light level in the room is {_fmt(v.value)} lux.", _source_of([v.state.origin]),
                     (AnswerFact("lux", v.value, "lx"),))


def _answer_current_lecture(ctx: AnswerContext) -> AnswerResult:
    if ctx.schedule is None or ctx.schedule.current_lecture is None:
        return _unavailable(Intent.LECTURE_CURRENT, REASON_NO_SCHEDULE)
    name = ctx.schedule.current_lecture
    return _answered(Intent.LECTURE_CURRENT, f"The lecture scheduled now is {name}.", AnswerSource.SIMULATED, (AnswerFact("current_lecture", name),))


def _answer_expected_attendance(ctx: AnswerContext) -> AnswerResult:
    if ctx.schedule is None or ctx.schedule.expected_attendance is None:
        return _unavailable(Intent.LECTURE_EXPECTED_ATTENDANCE, REASON_NO_SCHEDULE)
    n = ctx.schedule.expected_attendance
    text = "1 student is expected for this lecture." if n == 1 else f"{n} students are expected for this lecture."
    return _answered(Intent.LECTURE_EXPECTED_ATTENDANCE, text, AnswerSource.SIMULATED, (AnswerFact("expected_attendance", n, "students"),))


def _answer_next_class(ctx: AnswerContext) -> AnswerResult:
    if ctx.schedule is None or ctx.schedule.next_class_time is None:
        return _unavailable(Intent.NEXT_CLASS, REASON_NO_SCHEDULE)
    when = ctx.schedule.next_class_time
    return _answered(Intent.NEXT_CLASS, f"The next class is at {when}.", AnswerSource.SIMULATED, (AnswerFact("next_class_time", when),))


def _answer_professor(ctx: AnswerContext) -> AnswerResult:
    # No authoritative arrival state exists, so no input is consulted: not occupancy, not detections, not the schedule.
    return _unavailable(Intent.PROFESSOR_ARRIVAL, REASON_NO_ARRIVAL)


_ROOM_LABEL = ((Capability.PEOPLE_COUNT, "number of people"), (Capability.TEMPERATURE, "temperature"),
               (Capability.HUMIDITY, "humidity"), (Capability.BRIGHTNESS, "light level"))


def _join(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def _answer_capabilities(ctx: AnswerContext) -> AnswerResult:
    declared = ctx.capabilities
    sentences = []
    room = [label for cap, label in _ROOM_LABEL if declared.has(cap)]
    if room:
        sentences.append("I can tell you " + _join(["the " + label for label in room]) + " in the room.")
    if declared.has(Capability.LECTURE_INFORMATION):
        sentences.append("I can answer questions about the lecture schedule.")
    if declared.has(Capability.ANNOUNCEMENTS):
        sentences.append("I can play announcements.")
    text = "I'm VESTA, a classroom assistant. " + " ".join(sentences) if sentences else "I'm VESTA, a classroom assistant, but no functions are enabled right now."
    names = tuple(sorted(c.value for c in declared.capabilities))
    return _answered(Intent.CAPABILITIES, text, AnswerSource.DECLARATION, (AnswerFact("declared_capabilities", names),))


def _answer_announcement(ctx: AnswerContext) -> AnswerResult:
    available = ctx.capabilities.announcement_available
    text = "Yes, I can play predefined announcements." if available else "Announcements are not available yet."
    return _answered(Intent.ANNOUNCEMENT_CAPABILITY, text, AnswerSource.DECLARATION, (AnswerFact("announcement_available", available),))


_HANDLERS = {
    Intent.ROOM_PEOPLE_COUNT: _answer_people, Intent.ROOM_TEMPERATURE: _answer_temperature,
    Intent.ROOM_HUMIDITY: _answer_humidity, Intent.ROOM_BRIGHTNESS: _answer_brightness,
    Intent.LECTURE_CURRENT: _answer_current_lecture, Intent.LECTURE_EXPECTED_ATTENDANCE: _answer_expected_attendance,
    Intent.PROFESSOR_ARRIVAL: _answer_professor, Intent.CAPABILITIES: _answer_capabilities,
    Intent.NEXT_CLASS: _answer_next_class, Intent.ANNOUNCEMENT_CAPABILITY: _answer_announcement,
}


class AnswerEngine(ABC):
    """Turn recognised text plus the supplied context into one AnswerResult. The base class owns validation and logging."""

    @abstractmethod
    def _answer(self, text: str, context: AnswerContext) -> AnswerResult:
        """Answer one non-empty question string."""

    def answer(self, request: AnswerRequest) -> AnswerResult:
        if not isinstance(request, AnswerRequest):
            raise AnswerContextError(f"answer() needs an AnswerRequest, got {type(request).__name__}")
        text = request.text
        if not isinstance(text, str) or not normalize_question(text):
            result = AnswerResult(AnswerStatus.INVALID_INPUT, INVALID_TEXT, None, None, AnswerSource.NONE, REASON_EMPTY)
            length = len(text) if isinstance(text, str) else 0
        else:
            result = self._answer(text, request.context)
            length = len(text)
        if not isinstance(result, AnswerResult):
            raise AnswerContractError(f"{type(self).__name__}._answer returned {type(result).__name__}, not an AnswerResult")
        # structural fields only: never the question, never the answer
        logger.info("event=answer_done status=%s intent=%s question_id=%s source=%s reason=%s input_chars=%d",
                    result.status.value, result.intent.value if result.intent else "-", result.question_id or "-",
                    result.source.value, result.reason_code or "-", length)
        return result


class DeterministicAnswerEngine(AnswerEngine):
    """The v1 engine: the ten frozen AI-1 question families, nothing else."""

    def _answer(self, text: str, context: AnswerContext) -> AnswerResult:
        intent = match_intent(text)
        if intent is None:
            return AnswerResult(AnswerStatus.UNSUPPORTED_QUESTION, UNSUPPORTED_TEXT, None, None, AnswerSource.NONE, REASON_NO_MATCH)
        needed = _ROOM_CAPABILITY.get(intent)
        if needed is not None and not context.capabilities.has(needed):
            return AnswerResult(AnswerStatus.UNSUPPORTED_QUESTION, UNSUPPORTED_TEXT, intent, QUESTION_ID[intent], AnswerSource.NONE, REASON_NOT_DECLARED)
        return _HANDLERS[intent](context)
