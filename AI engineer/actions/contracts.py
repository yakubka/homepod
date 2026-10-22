"""Week 9 Task 1B execution contract (VESTA AI side). PROVISIONAL / SIMULATED — NOT TEAM-APPROVED (D1-D12 OPEN).

    validated StructuredCommand -> command-capability check -> ActionAdapter.submit -> replies -> ExecutionResult -> response policy

What this module defines (semantics only, NO wire format, NO packet, NO transport, NO timing value):
  * ResultState     progress events (SUBMITTED, ACCEPTED) and terminal outcomes (the other six). ACCEPTED is NEVER "applied".
  * ExecutionResult one immutable, correlated record: request id, target, action, state, origin, adapter identity, logical sequence,
                    reason code. Origin REAL is REFUSED: no hardware-validated adapter exists, so nothing here may claim a real device.
  * parse_reply     turns an UNTRUSTED adapter message (a Mapping) into an AdapterReply, or None when it is malformed. Never raises.
  * CommandCapabilityDeclaration   what the build can control: (target, action) -> CapabilityState. Default UNKNOWN_CAPABILITY.
                    Independent of the answering Capability enum (assistant/answering.py), which describes answers, not control.
  * RequestIdGenerator   '<prefix>-<12 hex session>-<n>': a random session token per generator plus a local counter. Not time-based.
  * ActionAdapter   the replaceable interface a real Pi hardware adapter would implement later (owner OPEN, D11).

Only the adapter that produced a result sets its origin; a reply message cannot declare its own origin.
"""

import itertools
import math
import re
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from fusion.contracts import Origin
from intents.contracts import CommandAction, CommandTarget, StructuredCommand

CONTRACT_STATUS = "PROVISIONAL / SIMULATED - NOT TEAM-APPROVED (D1-D12 OPEN)"
VALIDATION_LABEL = "SIMULATED"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")          # the same rule as intents/contracts.py (tested)
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,63}$")
_SESSION = re.compile(r"^[0-9a-f]{12}$")
_PREFIX = re.compile(r"^[a-z]{1,8}$")


class ActionContractError(ValueError):
    """An execution-layer value breaks the contract (a caller bug, never a spoken failure)."""


class ResultState(Enum):
    SUBMITTED = "SUBMITTED"                    # progress: handed on by the adapter; nothing is known about the device
    ACCEPTED = "ACCEPTED"                      # progress: the controller took the request; NOT executed
    APPLIED_CONFIRMED = "APPLIED_CONFIRMED"    # terminal: the controller reports the action applied
    REJECTED = "REJECTED"                      # terminal: refused before any attempt
    FAILED = "FAILED"                          # terminal: attempted, did not take effect
    UNAVAILABLE = "UNAVAILABLE"                # terminal: KNOWN never submitted
    TIMEOUT_UNKNOWN = "TIMEOUT_UNKNOWN"        # terminal: submitted, no terminal reply before the deadline; the outcome is UNKNOWN
    UNKNOWN = "UNKNOWN"                        # terminal: submitted (or maybe submitted) and the outcome cannot be established


PROGRESS_STATES = frozenset({ResultState.SUBMITTED, ResultState.ACCEPTED})
TERMINAL_STATES = frozenset(set(ResultState) - PROGRESS_STATES)
REPLY_STATES = frozenset({ResultState.ACCEPTED, ResultState.APPLIED_CONFIRMED, ResultState.REJECTED, ResultState.FAILED})
_NEEDS_REASON = TERMINAL_STATES - {ResultState.APPLIED_CONFIRMED}
# The only legal reply transitions. REJECTED after ACCEPTED is inconsistent (a rejection happens before any attempt).
ALLOWED_REPLY_TRANSITIONS = MappingProxyType({
    ResultState.SUBMITTED: frozenset({ResultState.ACCEPTED, ResultState.APPLIED_CONFIRMED, ResultState.REJECTED, ResultState.FAILED}),
    ResultState.ACCEPTED: frozenset({ResultState.APPLIED_CONFIRMED, ResultState.FAILED}),
})


@dataclass(frozen=True)
class ExecutionResult:
    """One correlated progress event or terminal outcome. `sequence` is a logical order inside one loop: no clock, no hardware time."""

    request_id: str
    target: CommandTarget
    action: CommandAction
    state: ResultState
    origin: Origin
    adapter_identity: str
    sequence: int
    reason: Optional[str] = None
    validation_label: str = VALIDATION_LABEL

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not _ID.match(self.request_id):
            raise ActionContractError("request_id must be a valid id")
        if not isinstance(self.target, CommandTarget) or not isinstance(self.action, CommandAction) or not isinstance(self.state, ResultState):
            raise ActionContractError("target, action and state must be enum members")
        if not isinstance(self.origin, Origin):
            raise ActionContractError("origin must be an Origin")
        if self.origin is Origin.REAL:
            raise ActionContractError("origin REAL is refused: no hardware-validated adapter exists")
        if not isinstance(self.adapter_identity, str) or not _IDENTITY.match(self.adapter_identity):
            raise ActionContractError("adapter_identity must be a short lower-case identifier")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ActionContractError("sequence must be an int >= 0")
        if self.reason is not None and (not isinstance(self.reason, str) or not _REASON.match(self.reason)):
            raise ActionContractError("reason must be an UPPER_CASE code or None")
        if self.state in _NEEDS_REASON and self.reason is None:
            raise ActionContractError(f"{self.state.value} needs a reason code")
        if self.validation_label != VALIDATION_LABEL:
            raise ActionContractError(f"validation_label must be {VALIDATION_LABEL!r}; nothing here is hardware-validated")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def simulated_completion(self) -> bool:
        """True only for APPLIED_CONFIRMED from a SIMULATED adapter. Never a hardware claim."""
        return self.state is ResultState.APPLIED_CONFIRMED and self.origin is Origin.SIMULATED


@dataclass(frozen=True)
class AdapterReply:
    """A well-formed reply, as parsed. It carries no origin: provenance comes from the adapter object, not from the message."""

    request_id: str
    target: CommandTarget
    action: CommandAction
    state: ResultState
    reason: Optional[str] = None


_REPLY_KEYS = frozenset({"request_id", "target", "action", "state", "reason"})


def _member(kind, raw):
    try:
        return kind[raw] if isinstance(raw, str) else None
    except KeyError:
        return None


def parse_reply(raw: Any) -> Optional[AdapterReply]:
    """Validate an UNTRUSTED reply message. None = malformed (unknown key, missing field, bad enum, missing reason, wrong state)."""
    if not isinstance(raw, Mapping) or not set(raw) <= _REPLY_KEYS or not {"request_id", "target", "action", "state"} <= set(raw):
        return None
    rid, reason = raw["request_id"], raw.get("reason")
    target, action, state = _member(CommandTarget, raw["target"]), _member(CommandAction, raw["action"]), _member(ResultState, raw["state"])
    if not isinstance(rid, str) or not _ID.match(rid) or target is None or action is None or state not in REPLY_STATES:
        return None
    if reason is not None and (not isinstance(reason, str) or not _REASON.match(reason)):
        return None
    if state in _NEEDS_REASON and reason is None:
        return None
    return AdapterReply(rid, target, action, state, reason)


class CapabilityState(Enum):
    SUPPORTED_AND_AVAILABLE = "SUPPORTED_AND_AVAILABLE"
    SUPPORTED_BUT_UNAVAILABLE = "SUPPORTED_BUT_UNAVAILABLE"
    UNSUPPORTED_TARGET = "UNSUPPORTED_TARGET"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"      # the default: nothing is controllable unless declared


@dataclass(frozen=True)
class CommandCapabilityDeclaration:
    """What this build can control, stated explicitly by whoever configures it. Routing a command never implies it is controllable."""

    entries: Mapping = field(default_factory=dict)
    source: str = "none"

    def __post_init__(self) -> None:
        if not isinstance(self.entries, Mapping):
            raise ActionContractError("entries must be a Mapping")
        for key, value in self.entries.items():
            if (not isinstance(key, tuple) or len(key) != 2 or not isinstance(key[0], CommandTarget) or not isinstance(key[1], CommandAction)
                    or not isinstance(value, CapabilityState)):
                raise ActionContractError("entries map (CommandTarget, CommandAction) to a CapabilityState")
        if not isinstance(self.source, str) or not _IDENTITY.match(self.source):
            raise ActionContractError("source must be a short lower-case identifier")
        object.__setattr__(self, "entries", MappingProxyType(dict(self.entries)))

    def state_of(self, target: CommandTarget, action: CommandAction) -> CapabilityState:
        return self.entries.get((target, action), CapabilityState.UNKNOWN_CAPABILITY)


class RequestIdGenerator:
    """PROVISIONAL request ids: '<prefix>-<session>-<n>', e.g. 'c-3fa91b0c22de-17'.

    `session` is 12 lower-case hex characters (48 random bits) drawn ONCE per generator from `token_source(6)` (default
    secrets.token_hex); n counts from 1. No wall clock is used. Tests inject a deterministic token_source.
    RESIDUAL RISK (not a guarantee): two sessions draw the same token with probability 2**-48 per pair, about N*N / 2**49 over N
    sessions (roughly 1.8e-7 for 10,000 restarts). One generator per process; call next_id() from one thread.
    """

    def __init__(self, *, prefix: str = "c", token_source: Callable[[int], str] = secrets.token_hex) -> None:
        if not isinstance(prefix, str) or not _PREFIX.match(prefix):
            raise ActionContractError("prefix must be 1-8 lower-case letters")
        session = token_source(6)
        if not isinstance(session, str) or not _SESSION.match(session):
            raise ActionContractError("token_source(6) must return 12 lower-case hex characters")
        self._prefix, self._session, self._count = prefix, session, itertools.count(1)

    @property
    def session_id(self) -> str:
        return self._session

    def next_id(self) -> str:
        rid = f"{self._prefix}-{self._session}-{next(self._count)}"
        if not _ID.match(rid):
            raise ActionContractError("request-id space exhausted for this session")
        return rid


class SubmitStatus(Enum):
    DELIVERED = "DELIVERED"                        # handed on towards the controller
    NOT_DELIVERED = "NOT_DELIVERED"                # KNOWN not sent: the request cannot have acted
    DELIVERY_UNCERTAIN = "DELIVERY_UNCERTAIN"      # it may have been sent: the outcome is unknown


LINK_LOST = "LINK_LOST"           # the event an adapter returns when its link to the controller drops


class ActionAdapter(ABC):
    """The replaceable Pi-side interface. A real implementation (owner OPEN, D11) would add the agreed transport behind it."""

    @property
    @abstractmethod
    def identity(self) -> str: ...

    @property
    @abstractmethod
    def origin(self) -> Origin: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def submit(self, command: StructuredCommand) -> SubmitStatus: ...

    @abstractmethod
    def next_event(self, deadline: float) -> Any:
        """Block until the next event or the deadline: a raw reply Mapping, LINK_LOST, or None when the deadline passed first."""


def check_timeout(timeout_s) -> float:
    """A positive, finite number of seconds. There is NO default: real values are team decision D4."""
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ActionContractError("timeout_s must be a positive finite number (no default: real values are decision D4)")
    return float(timeout_s)
