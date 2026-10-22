"""Truthful result-to-response policy (Week 9 Task 1B). PROVISIONAL / SIMULATED. Deterministic, fixed texts, nothing echoed.

The ONLY way to a completion sentence: a capability-approved, submitted, correlated ActionOutcome whose terminal result is
APPLIED_CONFIRMED with origin SIMULATED, and even then the sentence says "In the simulation". Nothing here can say HARDWARE-VALIDATED.
"""

from dataclasses import dataclass

from actions.contracts import VALIDATION_LABEL, CapabilityState, ResultState
from actions.loop import ActionOutcome
from fusion.contracts import Origin
from intents.contracts import CommandAction, CommandTarget
from speech.tts import check_text

COMPLETION_TEXT = {
    (CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON): "In the simulation, the classroom lights were turned on.",
    (CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_OFF): "In the simulation, the classroom lights were turned off.",
}
ACCEPTED_TEXT = "The simulated controller accepted the command, but completion is not confirmed."
UNCONFIRMED_TEXT = "I couldn't confirm whether the simulated action completed."
STATE_TEXT = {
    ResultState.REJECTED: "The simulated controller rejected the command.",
    ResultState.FAILED: "The simulated controller reported that the command failed.",
    ResultState.UNAVAILABLE: "The simulated action could not be submitted.",
    ResultState.TIMEOUT_UNKNOWN: UNCONFIRMED_TEXT,
    ResultState.UNKNOWN: UNCONFIRMED_TEXT,
    ResultState.APPLIED_CONFIRMED: UNCONFIRMED_TEXT,          # reached only when the completion conditions below do not all hold
}
CAPABILITY_TEXT = {
    CapabilityState.SUPPORTED_BUT_UNAVAILABLE: "The simulated action could not be submitted because the controller is unavailable.",
    CapabilityState.UNSUPPORTED_TARGET: "That can't be controlled in this simulated room.",
    CapabilityState.UNKNOWN_CAPABILITY: "I can't control that yet.",
}
ALL_TEXTS = (*COMPLETION_TEXT.values(), ACCEPTED_TEXT, *STATE_TEXT.values(), *CAPABILITY_TEXT.values())
for _text in ALL_TEXTS:
    check_text(_text)                 # a reply that cannot be synthesized fails at import, as in assistant/runtime.py


@dataclass(frozen=True)
class ResponseDecision:
    text: str
    confirms_simulated_completion: bool
    validation_label: str = VALIDATION_LABEL


def respond(outcome: ActionOutcome) -> ResponseDecision:
    if not isinstance(outcome, ActionOutcome):
        raise TypeError("respond needs an ActionOutcome from the action loop")
    if outcome.capability is not CapabilityState.SUPPORTED_AND_AVAILABLE:
        return ResponseDecision(CAPABILITY_TEXT[outcome.capability], False)
    r, c = outcome.result, outcome.command
    key = (c.target, c.action)
    if (outcome.submitted and r.state is ResultState.APPLIED_CONFIRMED and r.origin is Origin.SIMULATED
            and (r.request_id, r.target, r.action) == (c.request_id, c.target, c.action) and key in COMPLETION_TEXT):
        return ResponseDecision(COMPLETION_TEXT[key], True)
    if r.state is ResultState.TIMEOUT_UNKNOWN and outcome.accepted:
        return ResponseDecision(ACCEPTED_TEXT, False)
    return ResponseDecision(STATE_TEXT[r.state], False)
