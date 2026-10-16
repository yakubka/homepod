"""Assistant state contract (VESTA, Team Leader AI-8, SOFTWARE ONLY): LISTENING / CHECKING / SPEAKING as structured events.

    lifecycle sources (input side, OutputArbiter) -> AssistantStateManager -> StateEvent -> subscribers (Vision/Face, UI, LED adapter)

This module publishes WHAT the assistant is doing as an in-process, offline, immutable event. It never renders anything: no GPIO,
no LED, no display, no ESP32 command, no serial/BLE/network transport. The Vision/Face or hardware side subscribes (or polls the
current state) and owns how a colour is actually shown. RED/BLUE/GREEN here are SEMANTIC names, not RGB values or brightness.

STATES AND COLOURS (authoritative for AI-8; pinned by tests)
    IDLE       NO_ACTIVE_COLOR   nothing is being captured, processed or spoken. No authoritative idle colour exists, so none is invented.
    LISTENING  RED               VESTA is actively collecting the user's spoken utterance.
    CHECKING   BLUE              the request is being processed, before any spoken output has started.
    SPEAKING   GREEN             software playback of a spoken output is active.

LISTENING starts when a speech-input session is activated and ends when the utterance capture finishes (-> CHECKING) or is abandoned
    (-> IDLE: no speech, cancellation). Passive wake-word monitoring is IDLE, not LISTENING. LISTENING is never held open indefinitely.
CHECKING covers everything between "the utterance is complete" (or "an output request was admitted", for requests with no user
    utterance) and "the first buffer of spoken audio reached the device": STT finalization, question matching / answering, TTS
    synthesis and arbitration admission, as far as the runtime exposes them. There is NO LLM in this system; CHECKING does not mean
    that anything is "thinking". It ends in SPEAKING, or in IDLE when nothing is spoken (unsupported/unavailable/rejected flows).
SPEAKING starts at the playback layer's first-buffer signal (the moment the first samples are in the device buffer), never at TTS
    completion, never at the arbiter's admission. It ends in IDLE when playback completes, is stopped or fails.
    SPEAKING means SOFTWARE PLAYBACK ACTIVE. It does not mean sound is audible: device latency, DAC, amplifier, speaker and room all
    come after the first-buffer marker. Physical LED/face synchronization is PENDING hardware integration.

TRANSITIONS (anything else raises InvalidStateTransition; the same state again is an idempotent no-op that emits nothing)
    IDLE -> LISTENING | CHECKING        LISTENING -> CHECKING | IDLE        CHECKING -> SPEAKING | IDLE        SPEAKING -> IDLE
    One consistent policy for every spoken output, announcements and fallbacks included: IDLE -> CHECKING (admitted) -> SPEAKING -> IDLE.
    There is no IDLE -> SPEAKING shortcut, and LISTENING is never faked for an announcement or a standalone fallback. A fallback during
    a user interaction is simply LISTENING -> CHECKING -> SPEAKING -> IDLE. Barge-in (speaking while listening) is not supported.

EVENTS carry: state, previous_state, color_semantic, timestamp_monotonic, reason_code, sequence_number, optional request_id. Reasons are
    a CLOSED enum, so no free text (transcript, answer, announcement or fallback wording) can ride on an event or reach a log. Sequence
    numbers start at 0 for the initial IDLE event and increase by exactly 1 per emitted event, so consumers can drop stale events;
    the timestamp is a monotonic clock for ordering/latency only, never wall time.

SUBSCRIBERS are called synchronously, in registration order, under the manager's lock, so each one sees events in sequence order and
    manager.current_state equals the event it is handling. They must be fast (hand the event to their own queue/thread), must not call
    transition() (that raises StateReentrancyError inside the listener), and a listener that raises is logged and counted; it never
    changes the state and never stops the other subscribers (the state is committed before any listener runs).
    A client that connects late reads current_event / current_state / current_color, or subscribes with replay_current=True.

PRIVACY: logs and events contain only state names, colour names, sequence numbers, reason codes, request ids and timing.
STATUS: SOFTWARE-VALIDATED. No live Vision/Face, LED or hardware synchronization is established here.
"""

import math
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Dict, Optional, Tuple

from config.logging_config import setup_logging

logger = setup_logging()

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class AssistantState(Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    CHECKING = "CHECKING"
    SPEAKING = "SPEAKING"


class StateColor(Enum):
    """Semantic colour names. Not RGB values: the Vision/Face owner decides how a colour is rendered."""

    NO_ACTIVE_COLOR = "NO_ACTIVE_COLOR"
    RED = "RED"
    BLUE = "BLUE"
    GREEN = "GREEN"


# The AI-8 mapping. Read-only on purpose: a remapping has to be a visible source change (and a failing test).
COLOR_OF = MappingProxyType({
    AssistantState.IDLE: StateColor.NO_ACTIVE_COLOR,
    AssistantState.LISTENING: StateColor.RED,
    AssistantState.CHECKING: StateColor.BLUE,
    AssistantState.SPEAKING: StateColor.GREEN,
})

ALLOWED_TRANSITIONS = MappingProxyType({
    AssistantState.IDLE: frozenset({AssistantState.LISTENING, AssistantState.CHECKING}),
    AssistantState.LISTENING: frozenset({AssistantState.CHECKING, AssistantState.IDLE}),
    AssistantState.CHECKING: frozenset({AssistantState.SPEAKING, AssistantState.IDLE}),
    AssistantState.SPEAKING: frozenset({AssistantState.IDLE}),
})


class StateReason(Enum):
    """Why a transition happened. A closed set: nothing a user said or the assistant says can be a reason."""

    INITIAL = "INITIAL"
    SPEECH_INPUT_ACTIVATED = "SPEECH_INPUT_ACTIVATED"                    # IDLE -> LISTENING
    UTTERANCE_CAPTURED = "UTTERANCE_CAPTURED"                            # LISTENING -> CHECKING
    NO_SPEECH_DETECTED = "NO_SPEECH_DETECTED"                            # LISTENING -> IDLE
    CAPTURE_CANCELLED = "CAPTURE_CANCELLED"                              # LISTENING -> IDLE
    OUTPUT_ADMITTED = "OUTPUT_ADMITTED"                                  # -> CHECKING: the arbiter granted a spoken output
    PLAYBACK_STARTED = "PLAYBACK_STARTED"                                # CHECKING -> SPEAKING: first buffer reached the device
    PLAYBACK_COMPLETED = "PLAYBACK_COMPLETED"                            # SPEAKING -> IDLE
    PLAYBACK_STOPPED = "PLAYBACK_STOPPED"                                # SPEAKING -> IDLE (stopped while speaking)
    OUTPUT_STOPPED_BEFORE_PLAYBACK = "OUTPUT_STOPPED_BEFORE_PLAYBACK"    # CHECKING -> IDLE (stopped before any audio played)
    PLAYBACK_FAILED = "PLAYBACK_FAILED"                                  # -> IDLE (from SPEAKING, or from CHECKING if it never started)
    TTS_FAILED = "TTS_FAILED"                                            # CHECKING -> IDLE
    OUTPUT_REJECTED = "OUTPUT_REJECTED"                                  # CHECKING -> IDLE
    OUTPUT_ERROR = "OUTPUT_ERROR"                                        # -> IDLE after a software bug propagated
    NO_OUTPUT = "NO_OUTPUT"                                              # CHECKING -> IDLE: the request finished without speaking


class StateError(Exception):
    """Base class for state-contract errors."""


class StateRequestError(StateError, ValueError):
    """An argument is the wrong type (not an AssistantState / StateReason, a bad request id, a listener that is not callable)."""


class InvalidStateTransition(StateError):
    """The requested transition is not in the allowed table."""


class StateReentrancyError(StateError):
    """transition() was called from inside a subscriber callback."""


def _is_real(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class StateEvent:
    """One committed state change (or the initial IDLE). No text, no audio, no speaker identity."""

    state: AssistantState
    previous_state: Optional[AssistantState]
    color_semantic: StateColor
    timestamp_monotonic: float
    reason_code: StateReason
    sequence_number: int
    request_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, AssistantState):
            raise StateRequestError("state must be an AssistantState")
        if self.previous_state is not None and not isinstance(self.previous_state, AssistantState):
            raise StateRequestError("previous_state must be an AssistantState or None")
        if COLOR_OF[self.state] is not self.color_semantic:
            raise StateRequestError(f"{self.state.value} is {COLOR_OF[self.state].value}, not {self.color_semantic!r}")
        if not _is_real(self.timestamp_monotonic):
            raise StateRequestError("timestamp_monotonic must be a finite number")
        if not isinstance(self.reason_code, StateReason):
            raise StateRequestError("reason_code must be a StateReason")
        if isinstance(self.sequence_number, bool) or not isinstance(self.sequence_number, int) or self.sequence_number < 0:
            raise StateRequestError("sequence_number must be an int >= 0")
        if (self.previous_state is None) != (self.sequence_number == 0):
            raise StateRequestError("only the initial event (sequence 0) has no previous state")
        if self.request_id is not None and (not isinstance(self.request_id, str) or not _ID.match(self.request_id)):
            raise StateRequestError("request_id must be a valid id or None")


class AssistantStateManager:
    """The one authoritative holder of the assistant state. Thread-safe, deterministic, offline, hardware-free."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._tls = threading.local()
        self._listeners: Dict[int, Callable[[StateEvent], None]] = {}
        self._next_token = 0
        self._listener_failures = 0
        self._event = StateEvent(AssistantState.IDLE, None, COLOR_OF[AssistantState.IDLE], clock(), StateReason.INITIAL, 0, None)

    # -- queries (a late-connecting client needs no transition to learn the state) -------------------------

    @property
    def current_event(self) -> StateEvent:
        with self._lock:
            return self._event

    @property
    def current_state(self) -> AssistantState:
        with self._lock:
            return self._event.state

    @property
    def current_color(self) -> StateColor:
        with self._lock:
            return self._event.color_semantic

    @property
    def listener_failure_count(self) -> int:
        with self._lock:
            return self._listener_failures

    # -- transitions -----------------------------------------------------------------------------------------

    def transition(self, new_state: AssistantState, reason: StateReason, *, request_id: Optional[str] = None) -> Optional[StateEvent]:
        """Move to `new_state`. Returns the emitted StateEvent, or None when already in that state (idempotent: nothing is emitted and
        the sequence does not advance). Raises InvalidStateTransition for a transition outside the table."""
        if not isinstance(new_state, AssistantState):
            raise StateRequestError(f"new_state must be an AssistantState, got {new_state!r}")
        if not isinstance(reason, StateReason):
            raise StateRequestError("reason must be a StateReason")
        if request_id is not None and (not isinstance(request_id, str) or not _ID.match(request_id)):
            raise StateRequestError("request_id must be a valid id or None")
        if getattr(self._tls, "delivering", False):
            raise StateReentrancyError("transition() was called from inside a subscriber callback")
        with self._lock:
            current = self._event
            if new_state is current.state:
                return None
            if new_state not in ALLOWED_TRANSITIONS[current.state]:
                raise InvalidStateTransition(f"{current.state.value} -> {new_state.value} is not an allowed transition")
            now = self._clock()
            if not _is_real(now) or now < current.timestamp_monotonic:
                raise StateError("the state clock must be a monotonic, finite clock")
            event = StateEvent(new_state, current.state, COLOR_OF[new_state], now, reason, current.sequence_number + 1, request_id)
            self._event = event                                         # committed BEFORE anyone is told
            self._log(event)
            self._deliver(event, tuple(self._listeners.values()))
            return event

    # -- subscribers --------------------------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[StateEvent], None], *, replay_current: bool = False) -> Callable[[], None]:
        """Register `listener(event)`. With replay_current=True it is first given the current event (so it need not wait for the next
        transition). Returns a function that unsubscribes it."""
        if not callable(listener):
            raise StateRequestError("a listener must be callable")
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._listeners[token] = listener
            if replay_current:
                self._deliver(self._event, (listener,))

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.pop(token, None)

        return unsubscribe

    # -- internals ----------------------------------------------------------------------------------------------

    def _deliver(self, event: StateEvent, listeners: Tuple[Callable[[StateEvent], None], ...]) -> None:
        previous = getattr(self._tls, "delivering", False)
        self._tls.delivering = True
        try:
            for listener in listeners:
                try:
                    listener(event)
                except Exception as exc:                                # one broken subscriber never corrupts the state or silences the rest
                    self._listener_failures += 1
                    logger.error("event=state_listener_error sequence=%d error=%s", event.sequence_number, type(exc).__name__)
        finally:
            self._tls.delivering = previous

    @staticmethod
    def _log(event: StateEvent) -> None:
        logger.info("event=assistant_state state=%s previous=%s color=%s sequence=%d reason=%s request_id=%s t_monotonic=%.6f",
                    event.state.value, event.previous_state.value if event.previous_state else "-", event.color_semantic.value,
                    event.sequence_number, event.reason_code.value, event.request_id or "-", event.timestamp_monotonic)
