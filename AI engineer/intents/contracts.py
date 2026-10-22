"""Week 8 intent and structured-command contract (VESTA, SOFTWARE ONLY, engine-neutral).

    recognised text -> intents.router.route_intent() -> IntentResult
        = ONE of the six official categories (+ a validated StructuredCommand for the three command categories)
        or an explicit routing outcome (UNSUPPORTED / AMBIGUOUS / INVALID_COMMAND / INVALID_INPUT) with NO category

THE SIX OFFICIAL CATEGORIES (exactly these names; they are the only IntentCategory members)
    LOCAL_COMMAND      a command for the speaker itself (volume up / down, mute, unmute, set volume to N percent)
    SENSOR_QUERY       a question about a measured environmental value (temperature, humidity, light)
    OCCUPANCY_QUERY    a question about how many people (or whether anyone) are in the room, from the detected-person count
    CLASSROOM_CONTROL  a request to change the classroom (the classroom lights, on / off)
    SMART_HOME         an explicit, approved development smart-home request (the generic "smart bulb": on / off / brightness)
    GENERAL_AI         everything conversational or informational that is not one of the above
UNSUPPORTED, AMBIGUOUS, INVALID_COMMAND and INVALID_INPUT are ROUTING OUTCOMES (RoutingStatus), not categories: they never carry a category
except INVALID_COMMAND, which names the recognised command category of a request whose arguments are invalid.

CLASSIFICATION IS NOT EXECUTION. Task 1 ends at "validated intent + structured request". Nothing here executes anything:
  * a StructuredCommand is a typed request (category, action, target, value, unit, parameters, request id, validation status). It carries
    no GPIO pin, packet format, UART setting, device address or ESP32 wire syntax; those belong to the Hardware / Software teams;
  * every command result has execution_status NOT_EXECUTED, handling EXECUTION_NOT_IMPLEMENTED and requires_acknowledgement True;
    spoken_confirmation_allowed is False, and there is no execution-result type yet from which a success could be inferred;
  * an answer path is reported separately from the classification (Handling): GENERAL_AI classifies successfully but has
    NO_BACKEND_APPROVED unless a deterministic answering family exists.

PRIVACY: nothing here stores or logs the request text. IntentResult holds a rule id, never the words.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional

RESULT_VALIDATION_LABEL = "SOFTWARE-VALIDATED"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class IntentError(Exception):
    """Base class for intent-layer errors that are the caller's fault."""


class IntentContractError(IntentError, ValueError):
    """An IntentRequest, StructuredCommand or IntentResult breaks the contract."""


class IntentCategory(Enum):
    """The six official categories. Nothing else may be added here without a decision by the Team Leader."""

    LOCAL_COMMAND = "LOCAL_COMMAND"
    SENSOR_QUERY = "SENSOR_QUERY"
    OCCUPANCY_QUERY = "OCCUPANCY_QUERY"
    CLASSROOM_CONTROL = "CLASSROOM_CONTROL"
    SMART_HOME = "SMART_HOME"
    GENERAL_AI = "GENERAL_AI"


COMMAND_CATEGORIES = frozenset({IntentCategory.LOCAL_COMMAND, IntentCategory.CLASSROOM_CONTROL, IntentCategory.SMART_HOME})
QUERY_CATEGORIES = frozenset({IntentCategory.SENSOR_QUERY, IntentCategory.OCCUPANCY_QUERY})


class RoutingStatus(Enum):
    ROUTED = "ROUTED"                      # a category was assigned (and a valid command, for the command categories)
    INVALID_COMMAND = "INVALID_COMMAND"    # a command was recognised but its argument is missing / invalid: NO execution
    UNSUPPORTED = "UNSUPPORTED"            # not in the supported phrase set, or an unsupported target / action: NO execution
    AMBIGUOUS = "AMBIGUOUS"                # more than one reading, or a missing target: NO execution
    INVALID_INPUT = "INVALID_INPUT"        # empty, non-text or too long


class Handling(Enum):
    """What can be done with a routed request TODAY. Reported separately from the classification."""

    NONE = "NONE"                                      # not routed
    ANSWERING_FOUNDATION = "ANSWERING_FOUNDATION"      # the frozen deterministic answering engine can answer it (assistant/answering.py)
    ANSWER_NOT_IMPLEMENTED = "ANSWER_NOT_IMPLEMENTED"  # classified, but no answer logic exists yet (for example a darkness threshold)
    NO_BACKEND_APPROVED = "NO_BACKEND_APPROVED"        # GENERAL_AI: no general-purpose backend is approved; nothing is invented
    EXECUTION_NOT_IMPLEMENTED = "EXECUTION_NOT_IMPLEMENTED"   # a validated command; no execution layer exists (Week 9)


class ExecutionStatus(Enum):
    NOT_EXECUTED = "NOT_EXECUTED"          # the only value in Task 1: nothing here executes anything


class CommandAction(Enum):
    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"
    MUTE = "MUTE"
    UNMUTE = "UNMUTE"
    SET_VOLUME = "SET_VOLUME"
    TURN_ON = "TURN_ON"
    TURN_OFF = "TURN_OFF"
    SET_BRIGHTNESS = "SET_BRIGHTNESS"


class CommandTarget(Enum):
    SPEAKER_OUTPUT = "SPEAKER_OUTPUT"          # the speaker's own output (no device address)
    CLASSROOM_LIGHTS = "CLASSROOM_LIGHTS"      # all classroom lights; no zone, no geometry, no endpoint
    SMART_BULB = "SMART_BULB"                  # the generic development smart bulb; no named device, no protocol


class CommandUnit(Enum):
    PERCENT = "PERCENT"


class CommandValidationStatus(Enum):
    VALID = "VALID"
    MALFORMED = "MALFORMED"
    CATEGORY_MISMATCH = "CATEGORY_MISMATCH"
    UNSUPPORTED_TARGET = "UNSUPPORTED_TARGET"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    UNEXPECTED_VALUE = "UNEXPECTED_VALUE"
    MISSING_VALUE = "MISSING_VALUE"
    MISSING_UNIT = "MISSING_UNIT"
    INCORRECT_UNIT = "INCORRECT_UNIT"
    INVALID_VALUE = "INVALID_VALUE"
    VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
    UNSUPPORTED_PARAMETER = "UNSUPPORTED_PARAMETER"
    INVALID_REQUEST_ID = "INVALID_REQUEST_ID"


# The WHOLE table of supported commands. (category, target) -> action -> value rule. A value rule is None (no value allowed) or
# (unit, minimum, maximum), inclusive. A command that is not in this table cannot be constructed.
_PERCENT_RULE = (CommandUnit.PERCENT, 0, 100)
SUPPORTED_COMMANDS = MappingProxyType({
    (IntentCategory.LOCAL_COMMAND, CommandTarget.SPEAKER_OUTPUT): MappingProxyType({
        CommandAction.VOLUME_UP: None, CommandAction.VOLUME_DOWN: None, CommandAction.MUTE: None, CommandAction.UNMUTE: None,
        CommandAction.SET_VOLUME: _PERCENT_RULE,
    }),
    (IntentCategory.CLASSROOM_CONTROL, CommandTarget.CLASSROOM_LIGHTS): MappingProxyType({
        CommandAction.TURN_ON: None, CommandAction.TURN_OFF: None,
    }),
    (IntentCategory.SMART_HOME, CommandTarget.SMART_BULB): MappingProxyType({
        CommandAction.TURN_ON: None, CommandAction.TURN_OFF: None, CommandAction.SET_BRIGHTNESS: _PERCENT_RULE,
    }),
})
_TARGETS_OF_CATEGORY = {cat: {t for (c, t) in SUPPORTED_COMMANDS if c is cat} for cat in COMMAND_CATEGORIES}

_ALLOWED_PARAMETER_KEYS = frozenset()       # v1 accepts NO free-form parameters; the field exists for later, approved extensions


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_command_fields(category, action, target, value, unit, parameters, request_id) -> CommandValidationStatus:
    """The ONE validation routine (used by StructuredCommand and validate_command). Returns VALID or the first problem found."""
    if not isinstance(category, IntentCategory) or not isinstance(action, CommandAction) or not isinstance(target, CommandTarget):
        return CommandValidationStatus.MALFORMED
    if unit is not None and not isinstance(unit, CommandUnit):
        return CommandValidationStatus.MALFORMED
    if category not in COMMAND_CATEGORIES:
        return CommandValidationStatus.CATEGORY_MISMATCH
    if target not in _TARGETS_OF_CATEGORY[category]:
        return CommandValidationStatus.UNSUPPORTED_TARGET
    rule_table = SUPPORTED_COMMANDS[(category, target)]
    if action not in rule_table:
        return CommandValidationStatus.UNSUPPORTED_ACTION
    rule = rule_table[action]
    if rule is None:
        if value is not None or unit is not None:
            return CommandValidationStatus.UNEXPECTED_VALUE
    else:
        want_unit, low, high = rule
        if value is None:
            return CommandValidationStatus.MISSING_VALUE
        if unit is None:
            return CommandValidationStatus.MISSING_UNIT
        if unit is not want_unit:
            return CommandValidationStatus.INCORRECT_UNIT
        if not _is_int(value):
            return CommandValidationStatus.INVALID_VALUE
        if not low <= value <= high:
            return CommandValidationStatus.VALUE_OUT_OF_RANGE
    if not isinstance(parameters, Mapping):
        return CommandValidationStatus.MALFORMED
    if parameters and not set(parameters) <= _ALLOWED_PARAMETER_KEYS:
        return CommandValidationStatus.UNSUPPORTED_PARAMETER
    if not isinstance(request_id, str) or not _ID.match(request_id):
        return CommandValidationStatus.INVALID_REQUEST_ID
    return CommandValidationStatus.VALID


@dataclass(frozen=True)
class StructuredCommand:
    """A typed, VALIDATED command request. It cannot be constructed invalid. It is a request, not an execution: no wire protocol, no pins."""

    intent_category: IntentCategory
    action: CommandAction
    target: CommandTarget
    request_id: str
    value: Optional[int] = None
    unit: Optional[CommandUnit] = None
    parameters: Mapping[str, str] = field(default_factory=dict)
    validation_status: CommandValidationStatus = CommandValidationStatus.VALID

    def __post_init__(self) -> None:
        if self.validation_status is not CommandValidationStatus.VALID:
            raise IntentContractError("a StructuredCommand is always VALID; an invalid command is a CommandValidationResult, not a command")
        status = check_command_fields(self.intent_category, self.action, self.target, self.value, self.unit, self.parameters, self.request_id)
        if status is not CommandValidationStatus.VALID:
            raise IntentContractError(f"invalid command: {status.value}")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def requires_acknowledgement(self) -> bool:
        """Every command needs an execution result before anyone may say it happened. None exists in Task 1."""
        return True


@dataclass(frozen=True)
class CommandValidationResult:
    status: CommandValidationStatus
    command: Optional[StructuredCommand] = None

    def __post_init__(self) -> None:
        if (self.status is CommandValidationStatus.VALID) != (self.command is not None):
            raise IntentContractError("a VALID result carries a command; any other status carries none")

    @property
    def valid(self) -> bool:
        return self.status is CommandValidationStatus.VALID


_COMMAND_KEYS = frozenset({"intent_category", "action", "target", "value", "unit", "parameters", "request_id"})


def _enum_of(kind, raw):
    if isinstance(raw, kind):
        return raw
    if isinstance(raw, str):
        try:
            return kind[raw]
        except KeyError:
            return None
    return None


def validate_command(candidate: Any) -> CommandValidationResult:
    """Validate an UNTRUSTED structured command (a StructuredCommand-like Mapping, or a StructuredCommand). Never raises for bad content:
    the answer is a CommandValidationResult with the reason, and a command object only when it is VALID."""
    if isinstance(candidate, StructuredCommand):
        candidate = {k: getattr(candidate, k) for k in _COMMAND_KEYS}
    if not isinstance(candidate, Mapping) or not set(candidate) <= _COMMAND_KEYS or not {"intent_category", "action", "target", "request_id"} <= set(candidate):
        return CommandValidationResult(CommandValidationStatus.MALFORMED)
    category = _enum_of(IntentCategory, candidate["intent_category"])
    action = _enum_of(CommandAction, candidate["action"])
    target = _enum_of(CommandTarget, candidate["target"])
    unit_raw = candidate.get("unit")
    unit = None if unit_raw is None else _enum_of(CommandUnit, unit_raw)
    if unit_raw is not None and unit is None:
        # an unknown unit name: the shape is right but the unit is not supported
        return CommandValidationResult(CommandValidationStatus.INCORRECT_UNIT if category in COMMAND_CATEGORIES else CommandValidationStatus.MALFORMED)
    if category is None or action is None or target is None:
        return CommandValidationResult(CommandValidationStatus.MALFORMED)
    status = check_command_fields(category, action, target, candidate.get("value"), unit, candidate.get("parameters", {}), candidate["request_id"])
    if status is not CommandValidationStatus.VALID:
        return CommandValidationResult(status)
    command = StructuredCommand(category, action, target, candidate["request_id"], candidate.get("value"), unit, dict(candidate.get("parameters", {})))
    return CommandValidationResult(CommandValidationStatus.VALID, command)


@dataclass(frozen=True)
class IntentRequest:
    """The text to classify. `text` may be anything: a non-text value gives INVALID_INPUT, not an exception. It is never logged."""

    text: object = field(repr=False)
    request_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not _ID.match(self.request_id):
            raise IntentContractError(f"request_id must be 1-64 characters of letters, digits, '.', '_' or '-', got {self.request_id!r}")


@dataclass(frozen=True)
class IntentResult:
    """What the router decided. No request text. `category` is one of the six official names or None."""

    request_id: str
    routing_status: RoutingStatus
    reason_code: str
    rule_id: Optional[str] = None                    # the matched rule (a stable id), never the words
    category: Optional[IntentCategory] = None
    command: Optional[StructuredCommand] = None
    query_family: Optional[str] = None               # for queries: the family name (an assistant.answering.Intent name, or a not-yet-answerable family)
    handling: Handling = Handling.NONE
    execution_status: ExecutionStatus = ExecutionStatus.NOT_EXECUTED
    validation_label: str = RESULT_VALIDATION_LABEL

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not _ID.match(self.request_id):
            raise IntentContractError("request_id must be a valid id")
        if not isinstance(self.routing_status, RoutingStatus) or not isinstance(self.handling, Handling):
            raise IntentContractError("routing_status and handling must be enum members")
        if self.category is not None and not isinstance(self.category, IntentCategory):
            raise IntentContractError("category must be one of the six IntentCategory members or None")
        if not self.reason_code or not isinstance(self.reason_code, str):
            raise IntentContractError("reason_code is required")
        if self.execution_status is not ExecutionStatus.NOT_EXECUTED:
            raise IntentContractError("nothing is executed in Task 1: execution_status is always NOT_EXECUTED")
        if self.validation_label != RESULT_VALIDATION_LABEL:
            raise IntentContractError(f"validation_label must be {RESULT_VALIDATION_LABEL!r}")
        s, c = self.routing_status, self.category
        if s is RoutingStatus.ROUTED:
            if c is None or self.handling is Handling.NONE:
                raise IntentContractError("a ROUTED result needs a category and a handling")
            if c in COMMAND_CATEGORIES:
                if self.command is None or self.command.intent_category is not c or self.command.request_id != self.request_id:
                    raise IntentContractError("a routed command category carries its own validated command")
                if self.handling is not Handling.EXECUTION_NOT_IMPLEMENTED or self.query_family is not None:
                    raise IntentContractError("a command is EXECUTION_NOT_IMPLEMENTED and has no query family")
            else:
                if self.command is not None:
                    raise IntentContractError("only the command categories carry a command")
                if c in QUERY_CATEGORIES and (self.query_family is None or self.handling not in (Handling.ANSWERING_FOUNDATION, Handling.ANSWER_NOT_IMPLEMENTED)):
                    raise IntentContractError("a query needs a family and an ANSWERING_FOUNDATION / ANSWER_NOT_IMPLEMENTED handling")
                if c is IntentCategory.GENERAL_AI:
                    deterministic = self.handling is Handling.ANSWERING_FOUNDATION and self.query_family is not None
                    no_backend = self.handling is Handling.NO_BACKEND_APPROVED and self.query_family is None
                    if not (deterministic or no_backend):
                        raise IntentContractError("GENERAL_AI is either a deterministic answering family or NO_BACKEND_APPROVED")
        elif s is RoutingStatus.INVALID_COMMAND:
            if c not in COMMAND_CATEGORIES or self.command is not None or self.query_family is not None or self.handling is not Handling.NONE:
                raise IntentContractError("INVALID_COMMAND names a command category and carries no command")
        else:
            if c is not None or self.command is not None or self.query_family is not None or self.handling is not Handling.NONE:
                raise IntentContractError(f"{s.value} carries no category, command, family or handling")

    @property
    def routed(self) -> bool:
        return self.routing_status is RoutingStatus.ROUTED

    @property
    def actionable_command(self) -> Optional[StructuredCommand]:
        """The validated command, only for a ROUTED command category. Otherwise None: there is nothing to execute."""
        return self.command if self.routed and self.category in COMMAND_CATEGORIES else None

    @property
    def requires_acknowledgement(self) -> bool:
        return self.actionable_command is not None

    @property
    def spoken_confirmation_allowed(self) -> bool:
        """Always False in Task 1: no execution result type exists, so a success can never be inferred from a classification."""
        return False
