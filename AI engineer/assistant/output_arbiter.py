"""Spoken-output arbitration (VESTA, Team Leader AI-7, SOFTWARE ONLY): WHO MAY SPEAK NOW.

    request(s) -> OutputArbiter -> [GRANTED] -> SpokenOutputController -> TtsEngine -> PlaybackEngine
                              \\-> [anything else] -> an explicit ArbitrationResult; no TTS, no playback

The arbiter is the single owner of access to spoken output. It owns the decision and the lifecycle only: it does no TTS, no
playback of its own, no question answering, no STT, no sensor or schedule logic and no app transport. It builds the one
SpokenOutputController (and with it the one TtsEngine / PlaybackEngine pair) that may speak, so nothing else in this
package should hold a reference to those engines.

POLICY AI7_MIDTERM_V1 (fixed, explicit, traceable through ArbitrationResult.policy_version)
  * Classes follow the REQUEST TYPE chosen by the caller, never the text:
        CONVERSATION: ANSWER, FALLBACK        ANNOUNCEMENT: ANNOUNCEMENT
  * CONVERSATION outranks ANNOUNCEMENT. No numeric priorities, no special emergency rank for FALLBACK: an ANSWER and a FALLBACK
    are equals and contend by first arrival (the order the caller submits them).
  * Contention BEFORE output begins is decided in one atomic step by submit_batch(): the highest class wins, then the earliest
    request. Every loser gets an explicit result.
  * NON-PREEMPTIVE once an output is active. While a request is synthesizing or playing, nothing else speaks:
        a CONVERSATION request meeting an active ANNOUNCEMENT  -> RETRY_REQUIRED (it outranks, but this version cannot interrupt)
        an ANNOUNCEMENT meeting an active CONVERSATION         -> REJECTED_LOWER_PRIORITY
        anything meeting an active output of its own class     -> REJECTED_ACTIVE_OUTPUT
    Why: a running synthesis cannot be cancelled safely (no thread killing here) and a stop() is not sample-perfect, so cutting an
    announcement off mid-sentence is not a clearer result than finishing it. Preemption could be revisited only after playback
    cancellation is validated on the project hardware. RETRY_REQUIRED tells the caller to submit again once the output is free.
  * NO HIDDEN QUEUE. Nothing is held back and replayed later; a rejected request is simply returned to its caller.
  * AT MOST ONE SPOKEN OUTPUT EXECUTES AT A TIME, synthesis and playback together: one request owns the arbiter from GRANTED until
    its result is returned. TTS is never run in parallel and two playbacks can never overlap.

LIFECYCLE: IDLE -> SYNTHESIZING (granted) -> PLAYING (set atomically with the cancel check, just before the engine plays) -> IDLE.
There is no STOPPING phase: a stop during playback is a request to the playback engine, and the phase stays PLAYING until it returns.
"Busy" (the arbiter owns a request) is not the same as "audio playing": only PLAYING means the playback engine has been called.

STOP / CANCELLATION (closes the gap the controller documented). stop() records a cancel flag under the same lock that guards the
phase. The synthesis itself cannot be cancelled and is allowed to finish; but the playback engine is reached only through a guard that,
under that same lock, checks the flag and moves SYNTHESIZING -> PLAYING in one step. So a stop landing during synthesis, or in the instant
after synthesis and before playback, makes the guard return STOPPED without ever calling the playback engine. A stop that lands once
PLAYING calls the engine's own stop() (waiting a bounded moment for the engine to register that it is playing, so a stop cannot slip
through the gap before it does). The flag is cleared on release, so a stop can never leak into the next request, and stop() when idle
does nothing. A STOPPED result is never turned into COMPLETED.

STATE SYNCHRONIZATION (AI-8, optional: pass state=AssistantStateManager). The arbiter publishes the lifecycle of the ONE granted request
and nothing else: CHECKING when it is admitted (reason OUTPUT_ADMITTED; a no-op if a user interaction already put the state in
CHECKING), SPEAKING only when the playback engine reports that its first buffer reached the device (never at admission, never at TTS
completion, never for a stop or failure before that), and IDLE just before the arbiter is released, with a reason taken from the
result (completed / stopped / failed / bug). A rejected, invalid or retry-required request changes nothing, so it cannot disturb the
state of the output that is active. Such a manager needs a playback engine that offers the first-buffer signal; one that does not is
refused at construction rather than guessed at. State publication is observational: a state error is logged and counted
(state_sync_error_count) but never stops or alters the speech. The single visible state describes ONE activity at a time; an
orchestrator must not admit an output in the middle of another interaction's LISTENING or CHECKING (not enforced here).

STATUS: SOFTWARE-VALIDATED with fake engines. It says nothing about the project speaker, real playback cancellation, audibility or
classroom behaviour.

PRIVACY: only request ids, types, classes, decisions, phases, the policy version and error class names are logged; never text or audio.
"""

import itertools
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Sequence, Tuple

from assistant.spoken_output import (
    AnnouncementRequest, FallbackReason, OutputStatus, RequestType, SpokenOutputController, SpokenOutputRequestError, SpokenOutputResult,
)
from assistant.state import AssistantState, AssistantStateManager, StateError, StateReason
from audio.playback import PlaybackEngine, PlaybackResult, PlaybackStatus
from config.logging_config import setup_logging
from speech.tts import TtsEngine, TtsInputError, check_text

logger = setup_logging()

POLICY_VERSION = "AI7_MIDTERM_V1"
# DEVELOPMENT POLICY, not calibrated: how long stop() waits for a playback engine that was just called to report is_playing.
STOP_REGISTRATION_WAIT_S = 1.0
_STOP_POLL_S = 0.0005
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ---------------------------------------------------------------------------------------------------
# vocabulary and policy
# ---------------------------------------------------------------------------------------------------

class RequestClass(Enum):
    CONVERSATION = "CONVERSATION"
    ANNOUNCEMENT = "ANNOUNCEMENT"


class Decision(Enum):
    GRANTED = "GRANTED"
    REJECTED_ACTIVE_OUTPUT = "REJECTED_ACTIVE_OUTPUT"      # an output of the same class is active, or another request was granted first
    REJECTED_LOWER_PRIORITY = "REJECTED_LOWER_PRIORITY"    # a higher class is active or won the same contention
    RETRY_REQUIRED = "RETRY_REQUIRED"                      # outranks the active output but cannot preempt it: submit again later
    INVALID_REQUEST = "INVALID_REQUEST"


class OutputPhase(Enum):
    IDLE = "IDLE"
    SYNTHESIZING = "SYNTHESIZING"
    PLAYING = "PLAYING"


CLASS_OF = {
    RequestType.ANSWER: RequestClass.CONVERSATION,
    RequestType.FALLBACK: RequestClass.CONVERSATION,          # same class as ANSWER: no emergency priority
    RequestType.ANNOUNCEMENT: RequestClass.ANNOUNCEMENT,
}

R_IDLE_GRANTED = "IDLE_GRANTED"
R_WON_BY_CLASS = "WON_CONTENTION_BY_CLASS"
R_WON_FIRST = "WON_CONTENTION_FIRST_ARRIVAL"
R_LOST_CLASS = "LOST_CONTENTION_LOWER_CLASS"
R_LOST_LATER = "LOST_CONTENTION_LATER_ARRIVAL"
R_ACTIVE_ANNOUNCEMENT = "ACTIVE_ANNOUNCEMENT_NON_PREEMPTIVE"
R_ACTIVE_CONVERSATION = "ACTIVE_CONVERSATION_ANNOUNCEMENT_REJECTED"
R_ACTIVE_SAME_CLASS = "ACTIVE_OUTPUT_SAME_CLASS"
R_INVALID = "INVALID_ARGUMENT"


def outranks(a: RequestClass, b: RequestClass) -> bool:
    """The whole v1 priority rule: CONVERSATION outranks ANNOUNCEMENT, nothing else outranks anything."""
    return a is RequestClass.CONVERSATION and b is RequestClass.ANNOUNCEMENT


class ArbiterError(Exception):
    """Base class for arbiter problems that are programming errors, not arbitration outcomes."""


class ArbiterRequestError(ArbiterError, ValueError):
    """An ArbitrationRequest is malformed."""


class ArbiterStateError(ArbiterError):
    """The arbiter's guard was used outside a granted request (a bug)."""


# ---------------------------------------------------------------------------------------------------
# values
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ArbitrationRequest:
    """One request as the policy sees it. The class always follows the type; it is never read from any text."""

    request_id: str
    request_type: RequestType
    request_class: RequestClass
    sequence: int                 # arrival order, assigned by the arbiter (lower = earlier)

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not _ID.match(self.request_id):
            raise ArbiterRequestError(f"request_id must be a valid id, got {self.request_id!r}")
        if not isinstance(self.request_type, RequestType) or not isinstance(self.request_class, RequestClass):
            raise ArbiterRequestError("request_type and request_class must be enum members")
        if CLASS_OF[self.request_type] is not self.request_class:
            raise ArbiterRequestError(f"{self.request_type.value} belongs to {CLASS_OF[self.request_type].value}, not {self.request_class.value}")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ArbiterRequestError("sequence must be an int >= 0")


@dataclass(frozen=True)
class ArbitrationResult:
    """The decision about one request. No text, no audio, no claim that anything was heard."""

    request_id: Optional[str]
    decision: Decision
    request_type: Optional[RequestType]
    request_class: Optional[RequestClass]
    policy_version: str
    reason_code: str
    active_request_type: Optional[RequestType] = None
    active_phase: Optional[OutputPhase] = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, Decision):
            raise ArbiterRequestError("decision must be a Decision")
        if not self.policy_version or not self.reason_code:
            raise ArbiterRequestError("policy_version and reason_code are required")


@dataclass(frozen=True)
class ArbitrationOutcome:
    arbitration: ArbitrationResult
    output: Optional[SpokenOutputResult] = None      # present only when the request was GRANTED and ran

    @property
    def granted(self) -> bool:
        return self.arbitration.decision is Decision.GRANTED


@dataclass(frozen=True)
class SpeechSubmission:
    """What a caller hands to the arbiter. Deliberately not validated here: a bad one becomes an INVALID_REQUEST result."""

    request_type: object
    request_id: Optional[str] = None
    text: Optional[str] = None
    announcement: Optional[AnnouncementRequest] = None
    reason: Optional[FallbackReason] = None

    @classmethod
    def answer(cls, text, request_id) -> "SpeechSubmission":
        return cls(RequestType.ANSWER, request_id, text=text)

    @classmethod
    def announce(cls, request: AnnouncementRequest) -> "SpeechSubmission":
        return cls(RequestType.ANNOUNCEMENT, getattr(request, "announcement_id", None), announcement=request)

    @classmethod
    def fallback(cls, reason, request_id=None) -> "SpeechSubmission":
        if request_id is None and isinstance(reason, FallbackReason):
            request_id = "fallback-" + reason.value.lower().replace("_", "-")
        return cls(RequestType.FALLBACK, request_id, reason=reason)


def decide(requests: Sequence[ArbitrationRequest], phase: OutputPhase, active_type: Optional[RequestType] = None) -> Tuple[ArbitrationResult, ...]:
    """The pure v1 policy. Returns one ArbitrationResult per request, in input order.

    IDLE: the highest class wins, then the lowest sequence. Not IDLE: nobody is granted (non-preemptive)."""
    if not requests:
        return ()
    if phase is not OutputPhase.IDLE and active_type is None:
        raise ArbiterRequestError("an active phase needs the active request type")

    def result(r, decision, reason):
        active = None if phase is OutputPhase.IDLE else active_type
        return ArbitrationResult(r.request_id, decision, r.request_type, r.request_class, POLICY_VERSION, reason, active,
                                 None if phase is OutputPhase.IDLE else phase)

    if phase is OutputPhase.IDLE:
        best = requests[0]
        for r in requests[1:]:
            if outranks(r.request_class, best.request_class) or (r.request_class is best.request_class and r.sequence < best.sequence):
                best = r
        out: List[ArbitrationResult] = []
        for r in requests:
            if r is best:
                if len(requests) == 1:
                    out.append(result(r, Decision.GRANTED, R_IDLE_GRANTED))
                else:
                    by_class = any(outranks(best.request_class, o.request_class) for o in requests)
                    out.append(result(r, Decision.GRANTED, R_WON_BY_CLASS if by_class else R_WON_FIRST))
            elif outranks(best.request_class, r.request_class):
                out.append(result(r, Decision.REJECTED_LOWER_PRIORITY, R_LOST_CLASS))
            else:
                out.append(result(r, Decision.REJECTED_ACTIVE_OUTPUT, R_LOST_LATER))
        return tuple(out)

    active_class = CLASS_OF[active_type]
    out = []
    for r in requests:
        if outranks(r.request_class, active_class):
            out.append(result(r, Decision.RETRY_REQUIRED, R_ACTIVE_ANNOUNCEMENT))
        elif outranks(active_class, r.request_class):
            out.append(result(r, Decision.REJECTED_LOWER_PRIORITY, R_ACTIVE_CONVERSATION))
        else:
            out.append(result(r, Decision.REJECTED_ACTIVE_OUTPUT, R_ACTIVE_SAME_CLASS))
    return tuple(out)


# ---------------------------------------------------------------------------------------------------
# the guarded playback gate
# ---------------------------------------------------------------------------------------------------

class _GuardedPlayback(PlaybackEngine):
    """The only route to the real playback engine. Checks the cancel flag and enters PLAYING in one step, under the arbiter's lock."""

    def __init__(self, arbiter: "OutputArbiter", inner: PlaybackEngine) -> None:
        self._arbiter, self._inner = arbiter, inner

    def play(self, audio):
        a = self._arbiter
        with a._cond:
            if a._phase is not OutputPhase.SYNTHESIZING:
                raise ArbiterStateError("playback was requested outside a granted request")
            if a._cancel_requested:
                t = a._clock()
                return PlaybackResult(PlaybackStatus.STOPPED, audio.duration_s, t, None, None, t, None, None, None, 0)     # never starts
            a._phase = OutputPhase.PLAYING
            a._playback_returned = False
        try:
            return self._inner.play(audio)
        finally:
            with a._cond:
                a._playback_returned = True
                a._cond.notify_all()

    def stop(self) -> bool:
        return self._inner.stop()

    @property
    def is_playing(self) -> bool:
        return self._inner.is_playing


# ---------------------------------------------------------------------------------------------------
# the arbiter
# ---------------------------------------------------------------------------------------------------

class OutputArbiter:
    """Grants spoken output to at most one request at a time under policy AI7_MIDTERM_V1."""

    def __init__(self, tts: TtsEngine, playback: PlaybackEngine, *, clock: Callable[[], float] = time.perf_counter,
                 state: Optional[AssistantStateManager] = None) -> None:
        if not isinstance(tts, TtsEngine) or not isinstance(playback, PlaybackEngine):
            raise ArbiterRequestError("a TtsEngine and a PlaybackEngine are required")
        if state is not None:
            if not isinstance(state, AssistantStateManager):
                raise ArbiterRequestError("state must be an AssistantStateManager")
            if not playback.supports_first_buffer_listener:
                raise ArbiterRequestError("a state manager needs a playback engine that reports its first buffer")
        self._state = state
        self._state_sync_errors = 0
        self._clock = clock
        self._playback = playback
        self._cond = threading.Condition()
        self._phase = OutputPhase.IDLE
        self._owner: Optional[ArbitrationRequest] = None
        self._cancel_requested = False
        self._playback_returned = False
        self._sequence = itertools.count()
        self._controller = SpokenOutputController(tts, _GuardedPlayback(self, playback), clock=clock)
        if state is not None:
            playback.set_first_buffer_listener(self._on_first_buffer)

    # -- state -------------------------------------------------------------------------------------

    @property
    def state_sync_error_count(self) -> int:
        with self._cond:
            return self._state_sync_errors

    @property
    def phase(self) -> OutputPhase:
        with self._cond:
            return self._phase

    @property
    def active_request_type(self) -> Optional[RequestType]:
        with self._cond:
            return self._owner.request_type if self._owner else None

    # -- submitting --------------------------------------------------------------------------------

    def submit_answer(self, text, *, request_id) -> ArbitrationOutcome:
        return self.submit_batch([SpeechSubmission.answer(text, request_id)])[0]

    def submit_announcement(self, request) -> ArbitrationOutcome:
        return self.submit_batch([SpeechSubmission.announce(request)])[0]

    def submit_fallback(self, reason, *, request_id=None) -> ArbitrationOutcome:
        return self.submit_batch([SpeechSubmission.fallback(reason, request_id)])[0]

    def submit_batch(self, submissions: Sequence[SpeechSubmission]) -> Tuple[ArbitrationOutcome, ...]:
        """Arbitrate requests that arrive together, in one atomic step, then run the winner (if any) to completion.

        The order of `submissions` is the arrival order. Returns one outcome per submission, in the same order."""
        prepared: List[Optional[Tuple[ArbitrationRequest, SpeechSubmission]]] = []
        invalid: dict = {}
        for index, sub in enumerate(submissions):
            problem = self._problem(sub)
            if problem:
                prepared.append(None)
                invalid[index] = ArbitrationResult(
                    sub.request_id if isinstance(getattr(sub, "request_id", None), str) and _ID.match(sub.request_id) else None, Decision.INVALID_REQUEST,
                    sub.request_type if isinstance(getattr(sub, "request_type", None), RequestType) else None,
                    CLASS_OF[sub.request_type] if isinstance(getattr(sub, "request_type", None), RequestType) else None, POLICY_VERSION, problem)
            else:
                prepared.append((None, sub))
        results = {}
        winner = None
        with self._cond:
            requests = []
            for index, item in enumerate(prepared):
                if item is not None:
                    sub = item[1]
                    req = ArbitrationRequest(sub.request_id, sub.request_type, CLASS_OF[sub.request_type], next(self._sequence))
                    requests.append((index, req, sub))
            if requests:
                decisions = decide([r for _, r, _ in requests], self._phase, self._owner.request_type if self._owner else None)
                for (index, req, sub), d in zip(requests, decisions):
                    results[index] = d
                    if d.decision is Decision.GRANTED:
                        winner = (index, req, sub)
                if winner is not None:
                    self._phase = OutputPhase.SYNTHESIZING
                    self._owner = winner[1]
                    self._cancel_requested = False
                    self._playback_returned = False
        for index, d in {**invalid, **results}.items():
            self._log(d)
        output = None
        if winner is not None:
            output = self._execute(winner[2])
        outcomes = []
        for index in range(len(submissions)):
            d = invalid.get(index) or results[index]
            outcomes.append(ArbitrationOutcome(d, output if winner is not None and winner[0] == index else None))
        return tuple(outcomes)

    @staticmethod
    def _problem(sub) -> Optional[str]:
        if not isinstance(sub, SpeechSubmission) or not isinstance(sub.request_type, RequestType):
            return R_INVALID
        if not isinstance(sub.request_id, str) or not _ID.match(sub.request_id):
            return R_INVALID
        if sub.request_type is RequestType.ANSWER:
            try:
                check_text(sub.text)
            except TtsInputError:
                return R_INVALID
        elif sub.request_type is RequestType.ANNOUNCEMENT:
            if not isinstance(sub.announcement, AnnouncementRequest):
                return R_INVALID
            try:
                check_text(sub.announcement.text)
            except TtsInputError:
                return R_INVALID
        elif not isinstance(sub.reason, FallbackReason):
            return R_INVALID
        return None

    def _execute(self, sub: SpeechSubmission) -> SpokenOutputResult:
        result: Optional[SpokenOutputResult] = None
        try:
            self._emit(AssistantState.CHECKING, StateReason.OUTPUT_ADMITTED, sub.request_id)
            if sub.request_type is RequestType.ANSWER:
                result = self._controller.speak_answer(sub.text, message_id=sub.request_id)
            elif sub.request_type is RequestType.ANNOUNCEMENT:
                result = self._controller.speak_announcement(sub.announcement)
            else:
                result = self._controller.speak_fallback(sub.reason)
            return result
        finally:
            try:
                self._emit(AssistantState.IDLE, self._idle_reason(result), sub.request_id)      # while still the owner: before the next grant
            finally:
                with self._cond:                                   # always released: also on a bug, so the arbiter is never stuck busy
                    self._phase = OutputPhase.IDLE
                    self._owner = None
                    self._cancel_requested = False                 # a stop can never leak into the next request
                    self._playback_returned = False
                    self._cond.notify_all()

    # -- state publication (AI-8) ------------------------------------------------------------------

    def _on_first_buffer(self) -> None:
        """Called on the playback thread, inside play(), once the first samples are in the device buffer."""
        with self._cond:
            owner = self._owner
            live = owner is not None and self._phase is OutputPhase.PLAYING
        if live:                                                   # the state manager is called without holding the arbiter lock
            self._emit(AssistantState.SPEAKING, StateReason.PLAYBACK_STARTED, owner.request_id)

    def _idle_reason(self, result: Optional[SpokenOutputResult]) -> StateReason:
        if self._state is None:
            return StateReason.NO_OUTPUT                           # unused: nothing is published without a state manager
        if result is None:
            return StateReason.OUTPUT_ERROR                        # a software bug propagated out of the controller
        if result.status is OutputStatus.COMPLETED:
            return StateReason.PLAYBACK_COMPLETED
        if result.status is OutputStatus.STOPPED:
            speaking = self._state.current_state is AssistantState.SPEAKING
            return StateReason.PLAYBACK_STOPPED if speaking else StateReason.OUTPUT_STOPPED_BEFORE_PLAYBACK
        if result.status is OutputStatus.TTS_FAILED:
            return StateReason.TTS_FAILED
        if result.status is OutputStatus.PLAYBACK_FAILED:
            return StateReason.PLAYBACK_FAILED
        return StateReason.OUTPUT_REJECTED

    def _emit(self, state: AssistantState, reason: StateReason, request_id: Optional[str]) -> None:
        if self._state is None:
            return
        try:
            self._state.transition(state, reason, request_id=request_id)
        except StateError as exc:                                  # observational: the speech itself is never blocked by a state error
            with self._cond:
                self._state_sync_errors += 1
            logger.error("event=state_sync_error target=%s reason=%s error=%s", state.value, reason.value, type(exc).__name__)

    # -- stopping ----------------------------------------------------------------------------------

    def stop(self) -> bool:
        """Cancel the active output. False (and no state change) when idle. See the module docstring."""
        with self._cond:
            if self._phase is OutputPhase.IDLE:
                return False
            self._cancel_requested = True
            playing = self._phase is OutputPhase.PLAYING
        if playing:
            self._stop_playback()
        return True

    def _stop_playback(self) -> None:
        deadline = time.monotonic() + STOP_REGISTRATION_WAIT_S          # real time, independent of any injected test clock
        while True:
            with self._cond:
                if self._phase is not OutputPhase.PLAYING or self._playback_returned:
                    return                                          # it already ended (or the request was released)
            if self._playback.is_playing or time.monotonic() >= deadline:
                break
            time.sleep(_STOP_POLL_S)                                # the engine was just called and has not registered yet
        self._playback.stop()

    # -- logging -----------------------------------------------------------------------------------

    @staticmethod
    def _log(d: ArbitrationResult) -> None:
        logger.info("event=output_arbitration policy=%s request_id=%s request_type=%s request_class=%s decision=%s reason=%s active_type=%s active_phase=%s",
                    d.policy_version, d.request_id or "-", d.request_type.value if d.request_type else "-", d.request_class.value if d.request_class else "-",
                    d.decision.value, d.reason_code, d.active_request_type.value if d.active_request_type else "-",
                    d.active_phase.value if d.active_phase else "-")
