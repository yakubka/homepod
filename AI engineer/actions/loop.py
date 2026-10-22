"""The AI-side action loop (Week 9 Task 1B). PROVISIONAL / SIMULATED. Synchronous: one command in flight at a time.

    execute(command):
      1. a request id already used by this loop is refused (a repeated request is a NEW command with a NEW id; no retry, ever)
      2. capability check: anything but SUPPORTED_AND_AVAILABLE (and a connected adapter) is NEVER submitted
      3. adapter.submit:  NOT_DELIVERED -> UNAVAILABLE (known never submitted)
                          DELIVERY_UNCERTAIN -> UNKNOWN (it may have acted)
                          DELIVERED -> SUBMITTED, then wait for replies until the deadline
      4. a reply counts only if it is well-formed, its id is the pending one, its target and action match the command, and the state
         is a legal transition (ACCEPTED is progress, never completion); anything else is DISCARDED and logged, never spoken
      5. no terminal reply by the deadline -> TIMEOUT_UNKNOWN; link lost while waiting -> UNKNOWN. Neither is success or failure.
A reply for an id that already finished (a duplicate, a late reply after a timeout, a reply from before a restart) never changes that
result. Logging is structural only: ids, states, reason codes; never a transcript, never audio.
"""

import itertools
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from actions.contracts import (
    ALLOWED_REPLY_TRANSITIONS, LINK_LOST, ActionAdapter, ActionContractError, CapabilityState, CommandCapabilityDeclaration,
    ExecutionResult, ResultState, SubmitStatus, check_timeout, parse_reply,
)
from config.logging_config import setup_logging
from intents.contracts import StructuredCommand

logger = setup_logging()


class DiscardReason(Enum):
    MALFORMED_REPLY = "MALFORMED_REPLY"
    UNSOLICITED_OR_STALE = "UNSOLICITED_OR_STALE"          # an id this loop is not waiting for (other session, never issued)
    DUPLICATE_REPLY = "DUPLICATE_REPLY"                    # an id that already finished on a controller reply
    LATE_REPLY = "LATE_REPLY"                              # an id that already finished as TIMEOUT_UNKNOWN / UNKNOWN / UNAVAILABLE
    MISMATCHED_TARGET_ACTION = "MISMATCHED_TARGET_ACTION"
    INVALID_TRANSITION = "INVALID_TRANSITION"


@dataclass(frozen=True)
class Discard:
    request_id: Optional[str]                              # None when the message was malformed
    reason: DiscardReason


@dataclass(frozen=True)
class ActionOutcome:
    """What one execute() call established. No text, no audio."""

    command: StructuredCommand
    capability: CapabilityState
    submitted: bool
    result: Optional[ExecutionResult]                      # None ONLY when the capability check stopped the command
    progress: Tuple[ExecutionResult, ...] = ()
    discarded: Tuple[Discard, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.command, StructuredCommand) or not isinstance(self.capability, CapabilityState):
            raise ActionContractError("an outcome needs a StructuredCommand and a CapabilityState")
        if self.capability is not CapabilityState.SUPPORTED_AND_AVAILABLE:
            if self.submitted or self.result is not None or self.progress:
                raise ActionContractError("a command refused by the capability check is never submitted and has no result")
            return
        r = self.result
        if r is None or not r.terminal:
            raise ActionContractError("an executed command ends with a terminal result")
        if (r.request_id, r.target, r.action) != (self.command.request_id, self.command.target, self.command.action):
            raise ActionContractError("the result must be correlated with its command")
        if self.submitted != bool(self.progress) or (self.progress and self.progress[0].state is not ResultState.SUBMITTED):
            raise ActionContractError("a submitted command starts with a SUBMITTED event")
        if not self.submitted and r.state is not ResultState.UNAVAILABLE:
            raise ActionContractError("only UNAVAILABLE can end a command that was never submitted")

    @property
    def accepted(self) -> bool:
        return any(p.state is ResultState.ACCEPTED for p in self.progress)


class ActionLoop:
    """Owns the pending / finished request table for one session. Inject the adapter, the capability declaration and the clock."""

    def __init__(self, adapter: ActionAdapter, capabilities: CommandCapabilityDeclaration, *, clock: Callable[[], float], timeout_s: float) -> None:
        if not isinstance(adapter, ActionAdapter):
            raise ActionContractError("adapter must be an ActionAdapter")
        if not isinstance(capabilities, CommandCapabilityDeclaration):
            raise ActionContractError("capabilities must be a CommandCapabilityDeclaration")
        if not callable(clock):
            raise ActionContractError("clock must be callable")
        self._adapter, self._capabilities, self._clock = adapter, capabilities, clock
        self._timeout_s = check_timeout(timeout_s)
        self._seq = itertools.count()
        self._used = set()                                 # every request id this loop has seen, whatever happened to it
        self._finished: Dict[str, bool] = {}               # id -> True if it finished on a controller reply

    # -- public ------------------------------------------------------------------------------------

    def execute(self, command: StructuredCommand) -> ActionOutcome:
        if not isinstance(command, StructuredCommand):
            raise ActionContractError("execute needs a validated StructuredCommand")
        rid = command.request_id
        if rid in self._used:
            raise ActionContractError("request id already used: a repeated request is a new command with a new id")
        self._used.add(rid)
        capability = self._capabilities.state_of(command.target, command.action)
        if capability is CapabilityState.SUPPORTED_AND_AVAILABLE and not self._adapter.is_connected():
            capability = CapabilityState.SUPPORTED_BUT_UNAVAILABLE
        if capability is not CapabilityState.SUPPORTED_AND_AVAILABLE:
            logger.info("event=action_not_submitted request_id=%s capability=%s", rid, capability.value)
            return ActionOutcome(command, capability, False, None)

        status = self._adapter.submit(command)
        if status is SubmitStatus.NOT_DELIVERED:
            return self._finish(command, ResultState.UNAVAILABLE, "NOT_DELIVERED", False, [], [])
        progress = [self._result(command, ResultState.SUBMITTED)]
        if status is not SubmitStatus.DELIVERED:
            return self._finish(command, ResultState.UNKNOWN, "DELIVERY_UNCERTAIN", True, progress, [])

        state, discards = ResultState.SUBMITTED, []
        deadline = self._clock() + self._timeout_s
        while True:
            event = self._adapter.next_event(deadline)
            if event is None or self._clock() > deadline:
                if event is not None and event is not LINK_LOST:      # a real adapter returned after the deadline
                    discards.append(self._discard(event, rid))
                return self._finish(command, ResultState.TIMEOUT_UNKNOWN, "NO_TERMINAL_REPLY_BEFORE_DEADLINE", True, progress, discards)
            if event is LINK_LOST:
                return self._finish(command, ResultState.UNKNOWN, "LINK_LOST_AFTER_SUBMISSION", True, progress, discards)
            reply = parse_reply(event)
            reason = self._check(reply, command, state)
            if reason is not None:
                discards.append(self._log_discard(Discard(reply.request_id if reply else None, reason)))
                continue
            if reply.state is ResultState.ACCEPTED:
                state = ResultState.ACCEPTED
                progress.append(self._result(command, ResultState.ACCEPTED))
                continue
            return self._finish(command, reply.state, reply.reason, True, progress, discards, from_reply=True)

    def drain(self, duration_s: float) -> Tuple[Discard, ...]:
        """Consume events for `duration_s` while nothing is pending: every reply is discarded and can change nothing."""
        deadline = self._clock() + check_timeout(duration_s)
        out = []
        while (event := self._adapter.next_event(deadline)) is not None:
            if event is not LINK_LOST:
                out.append(self._discard(event, None))
        return tuple(out)

    # -- internals -----------------------------------------------------------------------------------

    def _check(self, reply, command: StructuredCommand, state: ResultState) -> Optional[DiscardReason]:
        if reply is None:
            return DiscardReason.MALFORMED_REPLY
        if reply.request_id in self._finished:
            return DiscardReason.DUPLICATE_REPLY if self._finished[reply.request_id] else DiscardReason.LATE_REPLY
        if reply.request_id != command.request_id:
            return DiscardReason.UNSOLICITED_OR_STALE
        if reply.target is not command.target or reply.action is not command.action:
            return DiscardReason.MISMATCHED_TARGET_ACTION
        if reply.state not in ALLOWED_REPLY_TRANSITIONS[state]:
            return DiscardReason.INVALID_TRANSITION
        return None

    def _discard(self, event, pending_id: Optional[str]) -> Discard:
        reply = parse_reply(event)
        if reply is None:
            return self._log_discard(Discard(None, DiscardReason.MALFORMED_REPLY))
        if reply.request_id in self._finished:
            reason = DiscardReason.DUPLICATE_REPLY if self._finished[reply.request_id] else DiscardReason.LATE_REPLY
        elif reply.request_id == pending_id:
            reason = DiscardReason.LATE_REPLY
        else:
            reason = DiscardReason.UNSOLICITED_OR_STALE
        return self._log_discard(Discard(reply.request_id, reason))

    @staticmethod
    def _log_discard(d: Discard) -> Discard:
        logger.info("event=action_reply_discarded request_id=%s reason=%s", d.request_id or "-", d.reason.value)
        return d

    def _result(self, command: StructuredCommand, state: ResultState, reason: Optional[str] = None) -> ExecutionResult:
        return ExecutionResult(command.request_id, command.target, command.action, state, self._adapter.origin, self._adapter.identity,
                               next(self._seq), reason)

    def _finish(self, command, state, reason, submitted, progress: List, discards: List, from_reply=False) -> ActionOutcome:
        self._finished[command.request_id] = from_reply
        result = self._result(command, state, reason)
        logger.info("event=action_result request_id=%s target=%s action=%s state=%s reason=%s origin=%s discarded=%d",
                    command.request_id, command.target.value, command.action.value, state.value, reason or "-", result.origin.value, len(discards))
        return ActionOutcome(command, CapabilityState.SUPPORTED_AND_AVAILABLE, submitted, result, tuple(progress), tuple(discards))
