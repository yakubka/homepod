"""SIMULATED classroom-lights adapter (Week 9 Task 1B). NO HARDWARE. NO SIDE EFFECTS. Origin is always SIMULATED.

It controls nothing and observes nothing: every reply it produces is SCRIPTED by the caller with plan() / inject() / lose_link().
It never claims a measurement or a device state. Time is a FakeClock that only moves forward, and only when next_event() waits.

    clock = FakeClock()
    adapter = SimulatedLightsAdapter(clock)
    adapter.plan(replies=[(0.1, "ACCEPTED"), (0.2, "APPLIED_CONFIRMED")])     # the script for the NEXT submission
"""

import heapq
import itertools
from collections import deque
from typing import Any, Iterable, Mapping, Optional, Tuple

from actions.contracts import LINK_LOST, ActionAdapter, CapabilityState, CommandCapabilityDeclaration, SubmitStatus
from fusion.contracts import Origin
from intents.contracts import CommandAction, CommandTarget, StructuredCommand

SIMULATED_ADAPTER_IDENTITY = "simulated-classroom-lights"
DROP = object()                      # an override value that removes a key from a scripted reply (to make it malformed)

# The ONE action family this simulation declares available: all classroom lights, on / off. Nothing else is declared.
SIMULATED_LIGHTS_CAPABILITIES = CommandCapabilityDeclaration({
    (CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON): CapabilityState.SUPPORTED_AND_AVAILABLE,
    (CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_OFF): CapabilityState.SUPPORTED_AND_AVAILABLE,
}, source="simulation")


class FakeClock:
    """Deterministic simulated seconds. Callable like time.monotonic."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def __call__(self) -> float:
        return self._t

    def advance_to(self, t: float) -> None:
        self._t = max(self._t, float(t))


class SimulatedLightsAdapter(ActionAdapter):
    """Scripted stand-in for the future Pi hardware adapter. A submission without a plan gets no reply at all (silence)."""

    def __init__(self, clock: FakeClock) -> None:
        if not isinstance(clock, FakeClock):
            raise TypeError("the simulated adapter needs a FakeClock")
        self._clock = clock
        self._events = []                     # heap of (time, order, event)
        self._order = itertools.count()
        self._plans = deque()
        self._connected = True
        self.submitted_ids = []               # request ids handed on (ids only: no text, no audio)

    @property
    def identity(self) -> str:
        return SIMULATED_ADAPTER_IDENTITY

    @property
    def origin(self) -> Origin:
        return Origin.SIMULATED

    def is_connected(self) -> bool:
        return self._connected

    def reconnect(self) -> None:
        self._connected = True

    # -- scripting ---------------------------------------------------------------------------------

    def plan(self, *, submit: SubmitStatus = SubmitStatus.DELIVERED, replies: Iterable[Tuple] = ()) -> None:
        """Script the NEXT submission. Each reply is (delay_s, state_name) or (delay_s, state_name, overrides); overrides may change
        request_id / target / action / reason, add a key, or remove one with DROP."""
        if not isinstance(submit, SubmitStatus):
            raise TypeError("submit must be a SubmitStatus")
        self._plans.append((submit, tuple(replies)))

    def inject(self, delay_s: float, raw: Any) -> None:
        """Schedule an arbitrary raw event (an unsolicited or stale reply, a malformed message)."""
        self._push(self._clock() + delay_s, raw)

    def lose_link(self, delay_s: float) -> None:
        self._push(self._clock() + delay_s, LINK_LOST)

    def _push(self, at: float, event: Any) -> None:
        heapq.heappush(self._events, (at, next(self._order), event))

    # -- the adapter interface -----------------------------------------------------------------------

    def submit(self, command: StructuredCommand) -> SubmitStatus:
        if not isinstance(command, StructuredCommand):
            raise TypeError("submit needs a StructuredCommand")
        submit, replies = self._plans.popleft() if self._plans else (SubmitStatus.DELIVERED, ())
        if submit is SubmitStatus.NOT_DELIVERED:
            return submit                     # known not sent: no reply can follow
        self.submitted_ids.append(command.request_id)
        now = self._clock()
        for step in replies:
            delay, state = step[0], step[1]
            overrides: Mapping = step[2] if len(step) > 2 else {}
            raw = {"request_id": command.request_id, "target": command.target.value, "action": command.action.value, "state": state}
            if state in ("REJECTED", "FAILED"):
                raw["reason"] = f"SIMULATED_{state}"
            for key, value in overrides.items():
                if value is DROP:
                    raw.pop(key, None)
                else:
                    raw[key] = value
            self._push(now + delay, raw)
        return submit

    def next_event(self, deadline: float) -> Optional[Any]:
        if self._events and self._events[0][0] <= deadline:
            at, _, event = heapq.heappop(self._events)
            self._clock.advance_to(at)
            if event is LINK_LOST:
                self._connected = False
            return event
        self._clock.advance_to(deadline)
        return None
