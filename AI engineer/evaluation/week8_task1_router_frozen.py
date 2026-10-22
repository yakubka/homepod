"""Week 8 deterministic intent router (v1): text -> one of the six official categories, or an explicit rejection. SOFTWARE ONLY.

    IntentRequest -> normalise -> explicit phrase tables and three small grammars -> IntentResult
    (queries are answered, if at all, by the FROZEN assistant/answering.py through answer_query(); nothing is executed here)

POLICY. Deterministic and closed: a request is routed only if it matches an explicit phrase or one of the grammars below. No LLM, no
embeddings, no cloud, no fuzzy matching, no keyword spotting. Anything else is UNSUPPORTED; anything with more than one reading, or a
missing / pronoun target, is AMBIGUOUS. Neither is ever executed or answered.

SUPPORTED PHRASE SET (v1; the tables below are the specification)
  LOCAL_COMMAND      "volume up / down", "turn the volume up", "mute", "unmute", "set the volume to 40 percent" (digits 0-100, the word "percent")
  CLASSROOM_CONTROL  "turn on / off the (classroom) lights", "turn the lights on", "lights off" ...  (ALL classroom lights; no zone)
  SMART_HOME         "turn on / off the smart bulb", "set the smart bulb brightness to 50 percent"  (the generic development bulb only)
  SENSOR_QUERY       temperature, humidity, light level (the frozen answering phrases plus a few shorter forms); "is the room dark" is
                     classified but ANSWER_NOT_IMPLEMENTED: no darkness threshold is defined, and none is invented
  OCCUPANCY_QUERY    how many people / students are here (the detected-person count); "is anyone in the room" is classified but
                     ANSWER_NOT_IMPLEMENTED. Expected attendance is a schedule question, never an occupancy query
  GENERAL_AI         the six deterministic answering families that are not sensor or occupancy (lecture, expected attendance, professor
                     arrival, capabilities, next class, announcement capability) -> ANSWERING_FOUNDATION; and open-ended explanatory
                     requests ("explain ...", "what is ...", "tell me a joke") -> NO_BACKEND_APPROVED (classified, never answered)
  Politeness is accepted around COMMANDS only ("please", "can you", "could you", "would you"). Every other variant is rejected.

DELIBERATE REJECTIONS (each tested): "turn on the light" (singular: classroom lights or the smart bulb?) AMBIGUOUS; "turn it on" and
"turn on" AMBIGUOUS; "set the volume" / "... to 50" / "... to 50 degrees" / "... to 150 percent" INVALID_COMMAND; any other target ("the
projector", "the fan", "the bedroom light", "the left lights", "the air conditioner") UNSUPPORTED_TARGET; "set the classroom lights to 50
percent" UNSUPPORTED_ACTION (dimming is not in v1); "play", "pause", "stop" UNSUPPORTED (no playback control exists).
SENSOR-TOPIC GUARD: an open-ended GENERAL_AI request that mentions a sensor or occupancy topic ("what is the temperature in the corridor")
is UNSUPPORTED, not GENERAL_AI, so that a future general backend can never be asked to invent a measurement.

PRIVACY: the request text is never stored in a result and never logged; only the rule id, category, status and the input length are.
"""

import logging
import re
from typing import Callable, Dict, List, Optional, Tuple

from assistant.answering import (
    PHRASES as ANSWER_PHRASES, AnswerContext, AnswerEngine, AnswerRequest, AnswerResult, DeterministicAnswerEngine, Intent as AnsweringIntent,
    normalize_question,
)
from config.logging_config import setup_logging
from intents.contracts import (
    COMMAND_CATEGORIES, CommandAction, CommandTarget, CommandUnit, CommandValidationStatus, Handling, IntentCategory, IntentContractError,
    IntentRequest, IntentResult, RoutingStatus, StructuredCommand, check_command_fields,
)

logger = setup_logging()

MAX_TEXT_CHARS = 200
Make = Callable[[str], IntentResult]
IC, RS, H = IntentCategory, RoutingStatus, Handling


_MINUS_BEFORE_DIGIT = re.compile(r"[-−–](?=\d)")


def normalize(text: str) -> str:
    """The answering layer's normalisation, plus: '%' is read as the word 'percent' (otherwise it would be dropped as punctuation), and a
    minus sign before a digit becomes the word 'minus' (otherwise '-5' would silently become '5')."""
    return normalize_question(_MINUS_BEFORE_DIGIT.sub(" minus ", text.replace("%", " percent ")))


# ---------------------------------------------------------------------------------------------------
# result builders (each returns a function of the request id, so a rule can be evaluated before the id is known)
# ---------------------------------------------------------------------------------------------------

def _command(rule_id, category, action, target, value=None, unit=None) -> Make:
    def make(request_id: str) -> IntentResult:
        cmd = StructuredCommand(category, action, target, request_id, value, unit)
        return IntentResult(request_id, RS.ROUTED, "COMMAND_VALIDATED", rule_id, category, cmd, None, H.EXECUTION_NOT_IMPLEMENTED)
    return make


def _query(rule_id, category, family, handling) -> Make:
    return lambda request_id: IntentResult(request_id, RS.ROUTED, "QUERY_CLASSIFIED", rule_id, category, None, family, handling)


def _general_no_backend(rule_id) -> Make:
    return lambda request_id: IntentResult(request_id, RS.ROUTED, "GENERAL_AI_NO_BACKEND_APPROVED", rule_id, IC.GENERAL_AI, None, None, H.NO_BACKEND_APPROVED)


def _reject(status, reason, rule_id, category=None) -> Make:
    return lambda request_id: IntentResult(request_id, status, reason, rule_id, category)


# ---------------------------------------------------------------------------------------------------
# exact phrase tables
# ---------------------------------------------------------------------------------------------------

def _answering_phrases(name: str) -> Tuple[str, ...]:
    return ANSWER_PHRASES[AnsweringIntent[name]]


# family -> (category, extra short forms). The frozen answering phrases are always included.
_QUERY_FAMILIES: Dict[str, Tuple[IntentCategory, Tuple[str, ...]]] = {
    "ROOM_TEMPERATURE": (IC.SENSOR_QUERY, ("what is the temperature", "what's the temperature", "what is the temperature in here",
                                          "how warm is it", "tell me the temperature")),
    "ROOM_HUMIDITY": (IC.SENSOR_QUERY, ("what is the humidity", "what's the humidity", "what is the humidity in here", "how humid is it")),
    "ROOM_BRIGHTNESS": (IC.SENSOR_QUERY, ("how bright is it", "how bright is it in here", "what is the brightness", "what is the light level in the room")),
    "ROOM_PEOPLE_COUNT": (IC.OCCUPANCY_QUERY, ("how many students are here", "how many students are in the room", "how many students are in this room",
                                               "how many students are in the class", "how many people are in the classroom")),
}
# the deterministic answering families that are neither a sensor nor an occupancy query: GENERAL_AI with a deterministic answer path
_GENERAL_FAMILIES = ("LECTURE_CURRENT", "LECTURE_EXPECTED_ATTENDANCE", "PROFESSOR_ARRIVAL", "CAPABILITIES", "NEXT_CLASS", "ANNOUNCEMENT_CAPABILITY")
# classified, but there is no answer logic: no darkness threshold and no presence handler exist in the frozen answering layer
_NOT_IMPLEMENTED: Tuple[Tuple[str, IntentCategory, Tuple[str, ...]], ...] = (
    ("ROOM_DARK", IC.SENSOR_QUERY, ("is the room dark", "is it dark in here", "is it dark")),
    ("ROOM_PRESENCE", IC.OCCUPANCY_QUERY, ("is someone in the room", "is anyone in the room", "is there anyone in the room", "is there someone in the room")),
)

# phrase -> (rule id, make-function factory). Built once; a duplicate or an un-normalised phrase is a programming error.
_EXACT: Dict[str, Tuple[str, Make]] = {}
CANONICAL_PHRASE: Dict[str, str] = {}      # answerable family -> the frozen answering phrase that makes DeterministicAnswerEngine answer it


def _add_exact(table: Dict[str, Tuple[str, Make]], phrase: str, rule_id: str, make: Make) -> None:
    if phrase != normalize(phrase) or phrase in table:
        raise IntentContractError(f"phrase table is not normalised or has a duplicate: {phrase!r}")
    table[phrase] = (rule_id, make)


for _family, (_cat, _extras) in _QUERY_FAMILIES.items():
    CANONICAL_PHRASE[_family] = _answering_phrases(_family)[0]
    for _p in _answering_phrases(_family) + _extras:
        _add_exact(_EXACT, _p, f"Q.{_family}", _query(f"Q.{_family}", _cat, _family, H.ANSWERING_FOUNDATION))
for _family in _GENERAL_FAMILIES:
    CANONICAL_PHRASE[_family] = _answering_phrases(_family)[0]
    for _p in _answering_phrases(_family):
        _add_exact(_EXACT, _p, f"G.{_family}", _query(f"G.{_family}", IC.GENERAL_AI, _family, H.ANSWERING_FOUNDATION))
for _family, _cat, _phrases in _NOT_IMPLEMENTED:
    for _p in _phrases:
        _add_exact(_EXACT, _p, f"Q.{_family}", _query(f"Q.{_family}", _cat, _family, H.ANSWER_NOT_IMPLEMENTED))

# commands, matched on the text with politeness removed
_COMMAND_EXACT: Dict[str, Tuple[str, Make]] = {}
_LC = IC.LOCAL_COMMAND
for _action, _phrases in (
    (CommandAction.VOLUME_UP, ("volume up", "turn the volume up", "turn up the volume", "increase the volume", "raise the volume")),
    (CommandAction.VOLUME_DOWN, ("volume down", "turn the volume down", "turn down the volume", "decrease the volume", "lower the volume", "reduce the volume")),
    (CommandAction.MUTE, ("mute", "mute the speaker", "mute the volume")),
    (CommandAction.UNMUTE, ("unmute", "unmute the speaker")),
):
    for _p in _phrases:
        _add_exact(_COMMAND_EXACT, _p, f"LC.{_action.value}", _command(f"LC.{_action.value}", _LC, _action, CommandTarget.SPEAKER_OUTPUT))

_POLITE_PREFIXES = ("please ", "can you ", "could you ", "would you ")


def _strip_polite(t: str) -> str:
    for _ in range(2):                       # at most two openers: "please could you ..."
        for prefix in _POLITE_PREFIXES:
            if t.startswith(prefix):
                t = t[len(prefix):]
                break
    if t.endswith(" please"):
        t = t[:-len(" please")]
    return t.strip()


# ---------------------------------------------------------------------------------------------------
# grammars: switch on / off, set <thing> to <number> <unit>
# ---------------------------------------------------------------------------------------------------

_CLASSROOM_OBJECTS = frozenset({"lights", "classroom lights"})
_SMART_OBJECTS = frozenset({"smart bulb", "smart light"})
_AMBIGUOUS_OBJECTS = frozenset({"light", "it", "that", "this", "them", "the light"})
_RE_SWITCH_A = re.compile(r"^(?:turn|switch) (on|off) (?:the )?(.+)$")
_RE_SWITCH_B = re.compile(r"^(?:turn|switch) (?:the )?(.+) (on|off)$")
_RE_SWITCH_C = re.compile(r"^(?:the )?(lights|classroom lights) (on|off)$")
_RE_SWITCH_BARE = re.compile(r"^(?:turn|switch) (?:on|off)$")
_RE_SET = re.compile(r"^set (?:the )?(.+?)(?: to (.+))?$")
_ACTION_OF = {"on": CommandAction.TURN_ON, "off": CommandAction.TURN_OFF}


def _switch(obj: str, onoff: str) -> Make:
    action = _ACTION_OF[onoff]
    if obj in _CLASSROOM_OBJECTS:
        return _command(f"CC.{action.value}", IC.CLASSROOM_CONTROL, action, CommandTarget.CLASSROOM_LIGHTS)
    if obj in _SMART_OBJECTS:
        return _command(f"SH.{action.value}", IC.SMART_HOME, action, CommandTarget.SMART_BULB)
    if obj in _AMBIGUOUS_OBJECTS:
        return _reject(RS.AMBIGUOUS, "AMBIGUOUS_TARGET", "SW.AMBIGUOUS_TARGET")
    return _reject(RS.UNSUPPORTED, "UNSUPPORTED_TARGET", "SW.UNSUPPORTED_TARGET")


_SET_VOLUME = ("volume",)
_SET_SMART = ("smart bulb", "smart light", "smart bulb brightness", "smart light brightness")
_SET_AMBIGUOUS = ("light", "light brightness", "brightness")
_SET_CLASSROOM = ("lights", "classroom lights", "lights brightness", "classroom lights brightness")


def _set(obj: str, arg: Optional[str]) -> Make:
    if arg is None and obj.endswith(" to"):         # "set the volume to" - the value is missing
        obj = obj[:-len(" to")]
    if obj in _SET_VOLUME:
        category, action, target, rule = IC.LOCAL_COMMAND, CommandAction.SET_VOLUME, CommandTarget.SPEAKER_OUTPUT, "LC.SET_VOLUME"
    elif obj in _SET_SMART:
        category, action, target, rule = IC.SMART_HOME, CommandAction.SET_BRIGHTNESS, CommandTarget.SMART_BULB, "SH.SET_BRIGHTNESS"
    elif obj in _SET_AMBIGUOUS:
        return _reject(RS.AMBIGUOUS, "AMBIGUOUS_TARGET", "SET.AMBIGUOUS_TARGET")
    elif obj in _SET_CLASSROOM:
        return _reject(RS.UNSUPPORTED, "UNSUPPORTED_ACTION", "SET.UNSUPPORTED_ACTION")
    else:
        return _reject(RS.UNSUPPORTED, "UNSUPPORTED_TARGET", "SET.UNSUPPORTED_TARGET")

    def invalid(status: CommandValidationStatus) -> Make:
        return _reject(RS.INVALID_COMMAND, status.value, rule, category)

    tokens = arg.split() if arg else []
    if not tokens:
        return invalid(CommandValidationStatus.MISSING_VALUE)
    if len(tokens) > 2:
        return invalid(CommandValidationStatus.INVALID_VALUE)
    number, unit_word = tokens[0], tokens[1] if len(tokens) == 2 else None
    if unit_word is not None and unit_word != "percent":
        return invalid(CommandValidationStatus.INCORRECT_UNIT)
    if not (number.isascii() and number.isdigit() and len(number) <= 4):
        return invalid(CommandValidationStatus.INVALID_VALUE)
    if unit_word is None:
        return invalid(CommandValidationStatus.MISSING_UNIT)
    value = int(number)
    status = check_command_fields(category, action, target, value, CommandUnit.PERCENT, {}, "probe")
    if status is not CommandValidationStatus.VALID:
        return invalid(status)
    return _command(rule, category, action, target, value, CommandUnit.PERCENT)


# ---------------------------------------------------------------------------------------------------
# GENERAL_AI: explicit explanatory openers only, behind the sensor-topic guard
# ---------------------------------------------------------------------------------------------------

_GENERAL_OPENERS = ("explain ", "what is ", "what are ", "what does ", "who is ", "who was ", "why ", "how does ", "how do ", "help me ", "define ",
                    "describe ", "teach me ", "tell me about ", "summarize ")
_GENERAL_EXACT = frozenset({"tell me a joke"})
_SENSOR_TOPIC_WORDS = frozenset({"temperature", "humidity", "humid", "lux", "brightness", "bright", "dark", "warm", "occupancy", "occupied", "crowded"})
_SENSOR_TOPIC_PHRASES = ("in the room", "in here", "in this room", "in the classroom", "in the class", "light level")


def _general(t: str) -> Optional[Make]:
    if t in _GENERAL_EXACT:
        return _general_no_backend("GEN.OPEN_ENDED")
    for opener in _GENERAL_OPENERS:
        if t.startswith(opener) and len(t) > len(opener):
            words = set(t.split())
            if words & _SENSOR_TOPIC_WORDS or any(p in t for p in _SENSOR_TOPIC_PHRASES) or "how many" in t or "how much light" in t:
                return _reject(RS.UNSUPPORTED, "SENSOR_TOPIC_NOT_ROUTABLE_TO_GENERAL_AI", "GEN.SENSOR_TOPIC_GUARD")
            return _general_no_backend("GEN.OPEN_ENDED")
    return None


# ---------------------------------------------------------------------------------------------------
# the router
# ---------------------------------------------------------------------------------------------------

def _matches(t: str) -> List[Make]:
    found: List[Make] = []
    hit = _EXACT.get(t)
    if hit:
        found.append(hit[1])
    s = _strip_polite(t)
    hit = _COMMAND_EXACT.get(s)
    if hit:
        found.append(hit[1])
    for regex, build in ((_RE_SWITCH_A, lambda m: _switch(m.group(2), m.group(1))), (_RE_SWITCH_B, lambda m: _switch(m.group(1), m.group(2))),
                         (_RE_SWITCH_C, lambda m: _switch(m.group(1), m.group(2)))):
        m = regex.match(s)
        if m:
            found.append(build(m))
    if _RE_SWITCH_BARE.match(s):
        found.append(_reject(RS.AMBIGUOUS, "MISSING_TARGET", "SW.MISSING_TARGET"))
    m = _RE_SET.match(s)
    if m:
        found.append(_set(m.group(1), m.group(2)))
    if not found:
        general = _general(t)
        if general:
            found.append(general)
    return found


def _signature(r: IntentResult):
    c = r.command
    return (r.routing_status, r.category, r.reason_code, r.query_family, r.handling, (c.action, c.target, c.value, c.unit) if c else None)


def route_intent(request: IntentRequest) -> IntentResult:
    """Classify ONE request. Never raises for bad text, never executes, never answers. Deterministic."""
    if not isinstance(request, IntentRequest):
        raise IntentContractError(f"route_intent() needs an IntentRequest, got {type(request).__name__}")
    text, rid = request.text, request.request_id
    if not isinstance(text, str) or not text.strip():
        result = IntentResult(rid, RS.INVALID_INPUT, "EMPTY_OR_NON_TEXT_INPUT", "IN.EMPTY")
        length = 0
    elif len(text) > MAX_TEXT_CHARS:
        result = IntentResult(rid, RS.INVALID_INPUT, "TEXT_TOO_LONG", "IN.TOO_LONG")
        length = len(text)
    else:
        length = len(text)
        t = normalize(text)
        if not t:
            result = IntentResult(rid, RS.INVALID_INPUT, "EMPTY_OR_NON_TEXT_INPUT", "IN.EMPTY")
        else:
            results = [make(rid) for make in _matches(t)]
            if not results:
                result = IntentResult(rid, RS.UNSUPPORTED, "NO_MATCHING_RULE", "NONE")
            elif len({_signature(r) for r in results}) > 1:
                result = IntentResult(rid, RS.AMBIGUOUS, "CONFLICTING_RULES", "CONFLICT")
            else:
                result = results[0]
    # structural fields only: never the request text
    logger.log(logging.INFO, "event=intent_routed request_id=%s status=%s category=%s reason=%s rule=%s handling=%s input_chars=%d",
               result.request_id, result.routing_status.value, result.category.value if result.category else "-", result.reason_code,
               result.rule_id or "-", result.handling.value, length)
    return result


def answer_query(result: IntentResult, context: AnswerContext, engine: Optional[AnswerEngine] = None) -> AnswerResult:
    """Hand a routed query whose handling is ANSWERING_FOUNDATION to the FROZEN answering engine, using its canonical phrase.

    The engine's own rules apply untouched: missing, stale, invalid or unconfigured information is "unavailable", an undeclared capability
    is unsupported, and nothing is substituted. Anything else (a command, a rejection, ANSWER_NOT_IMPLEMENTED, NO_BACKEND_APPROVED) is
    refused with IntentContractError: a command can never reach the answering engine, and an unanswerable query is never answered."""
    if not isinstance(result, IntentResult) or not result.routed or result.handling is not Handling.ANSWERING_FOUNDATION:
        raise IntentContractError("only a routed query with handling ANSWERING_FOUNDATION can be answered")
    phrase = CANONICAL_PHRASE.get(result.query_family or "")
    if phrase is None:
        raise IntentContractError("no canonical answering phrase for this family")
    return (engine or DeterministicAnswerEngine()).answer(AnswerRequest(phrase, context))
