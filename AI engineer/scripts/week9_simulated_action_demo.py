"""VESTA WEEK 9 TASK 1B DEMO - SIMULATED AI-TO-ACTION LOOP. SOFTWARE ONLY, DETERMINISTIC. NO PHYSICAL HARDWARE.

    real intent router -> validated StructuredCommand -> command-capability check -> SIMULATED adapter (scripted replies, fake clock)
        -> correlated ExecutionResult -> truthful response policy -> the runtime's ONE OutputArbiter -> FAKE tone TTS -> FAKE playback

The arbiter is the one built by assistant/runtime.py (the single composition root; tests/test_state_integration.py). The runtime is
constructed with the existing FAKE wake / VAD / STT doubles only to own that arbiter: no audio is fed to it, and it executes nothing.

Seven scenarios (A-G). The request-id session token is FIXED here so the output is reproducible; it is NOT random in this demo.
The timeout is a SIMULATED test value (2.0 simulated seconds), not a hardware value (decision D4 is OPEN). No light is controlled.

    python -m scripts.week9_simulated_action_demo

Exit codes: 0 = every scenario behaved as expected; 1 = a scenario behaved differently.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.contracts import CapabilityState, CommandCapabilityDeclaration, RequestIdGenerator
from actions.loop import ActionLoop
from actions.response_policy import respond
from actions.simulated_adapter import SIMULATED_LIGHTS_CAPABILITIES, FakeClock, SimulatedLightsAdapter
from assistant.runtime import VoiceAssistantRuntime
from audio.utterance import UtteranceSegmenter
from intents.contracts import CommandAction, CommandTarget, IntentRequest
from intents.router import route_intent
from scripts.vesta_runtime_demo import DemoPlayback, DemoTts, EnergyVad, ScriptedStt, ScriptedWake, segmenter_config

BANNER = ("SIMULATED VESTA AI-TO-ACTION LOOP.\nNO PHYSICAL HARDWARE USED.\n"
          "Contract: PROVISIONAL / SIMULATED - NOT TEAM-APPROVED (D1-D12 OPEN). Label: SIMULATED, never HARDWARE-VALIDATED.")
SIM_TIMEOUT_S = 2.0


def _command(text, ids):
    return route_intent(IntentRequest(text, ids.next_id())).actionable_command


def main(argv=None, *, out=None) -> int:
    out = out or sys.stdout
    tokens = iter(("5eed0000a001", "5eed0000b002"))     # fixed session tokens: session 1, then the session after a simulated restart
    clock = FakeClock()
    adapter = SimulatedLightsAdapter(clock)
    ids = RequestIdGenerator(token_source=lambda n: next(tokens))
    loop = ActionLoop(adapter, SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=SIM_TIMEOUT_S)
    playback = DemoPlayback()
    runtime = VoiceAssistantRuntime(detector=ScriptedWake(fire=()), segmenter=UtteranceSegmenter(segmenter_config(), EnergyVad()),
                                    stt=ScriptedStt(["unused"]), tts=DemoTts(), playback=playback)
    arbiter = runtime.arbiter
    on, off = "turn on the lights", "turn off the lights"
    rows, ok = [], True

    def run(name, expected_state, expected_confirm, outcome, extra_ok=True, note=""):
        nonlocal ok
        decision = respond(outcome)
        spoken = arbiter.submit_answer(decision.text, request_id=outcome.command.request_id)
        state = outcome.result.state.value if outcome.result else f"NOT_SUBMITTED({outcome.capability.value})"
        good = (state == expected_state and decision.confirms_simulated_completion is expected_confirm and extra_ok and spoken.granted
                and spoken.output.status.value == "COMPLETED")
        ok = ok and good
        discards = ",".join(d.reason.value for d in outcome.discarded) or "-"
        rows.append(f"{name}: id={outcome.command.request_id} {outcome.command.action.value}/{outcome.command.target.value} "
                    f"capability={outcome.capability.value} submitted={outcome.submitted} state={state} "
                    f"reason={outcome.result.reason if outcome.result and outcome.result.reason else '-'} "
                    f"origin={outcome.result.origin.value if outcome.result else '-'} discarded={discards} "
                    f"confirms={decision.confirms_simulated_completion} {note}\n   says: \"{decision.text}\"  [{'OK' if good else 'UNEXPECTED'}]")

    # A. lights ON, accepted then applied
    adapter.plan(replies=[(0.1, "ACCEPTED"), (0.3, "APPLIED_CONFIRMED")])
    run("A ON confirmed", "APPLIED_CONFIRMED", True, loop.execute(_command(on, ids)))
    # B. lights OFF, applied in one step
    adapter.plan(replies=[(0.2, "APPLIED_CONFIRMED")])
    run("B OFF confirmed", "APPLIED_CONFIRMED", True, loop.execute(_command(off, ids)))
    # C. accepted, never completed
    adapter.plan(replies=[(0.1, "ACCEPTED")])
    c = loop.execute(_command(on, ids))
    run("C accepted, not completed", "TIMEOUT_UNKNOWN", False, c, extra_ok=c.accepted)
    # D. silence -> timeout, outcome unknown
    adapter.plan(replies=[])
    run("D timeout", "TIMEOUT_UNKNOWN", False, loop.execute(_command(on, ids)))
    # E. the capability says unavailable -> never submitted
    unavailable = ActionLoop(adapter, CommandCapabilityDeclaration({(CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON): CapabilityState.SUPPORTED_BUT_UNAVAILABLE},
                                                                   source="simulation"), clock=clock, timeout_s=SIM_TIMEOUT_S)
    before = len(adapter.submitted_ids)
    run("E capability unavailable", "NOT_SUBMITTED(SUPPORTED_BUT_UNAVAILABLE)", False, unavailable.execute(_command(on, ids)),
        extra_ok=len(adapter.submitted_ids) == before)
    # F. simulated restart: a reply from the OLD session, then a wrong-action reply, are never accepted
    old_id = adapter.submitted_ids[-1]
    ids2 = RequestIdGenerator(token_source=lambda n: next(tokens))
    loop2 = ActionLoop(adapter, SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=SIM_TIMEOUT_S)
    cmd = _command(off, ids2)
    adapter.plan(replies=[(0.2, "APPLIED_CONFIRMED", {"action": "TURN_ON"})])
    adapter.inject(0.1, {"request_id": old_id, "target": "CLASSROOM_LIGHTS", "action": "TURN_OFF", "state": "APPLIED_CONFIRMED"})
    f = loop2.execute(cmd)
    run("F stale / wrong ack", "TIMEOUT_UNKNOWN", False, f, extra_ok=[d.reason.value for d in f.discarded] == ["UNSOLICITED_OR_STALE", "MISMATCHED_TARGET_ACTION"],
        note=f"(new session {ids2.session_id})")
    # G. duplicate reply -> one confirmation only
    adapter.plan(replies=[(0.1, "APPLIED_CONFIRMED"), (0.2, "APPLIED_CONFIRMED")])
    g = loop2.execute(_command(on, ids2))
    late = loop2.drain(1.0)
    run("G duplicate reply", "APPLIED_CONFIRMED", True, g, extra_ok=[d.reason.value for d in late] == ["DUPLICATE_REPLY"],
        note=f"(after: {','.join(d.reason.value for d in late)} discarded, not spoken)")

    print(BANNER, file=out)
    for row in rows:
        print(row, file=out)
    confirmations = sum(1 for r in rows if "confirms=True" in r)
    print(f"spoken replies={playback.calls} (fake playback) simulated confirmations={confirmations} simulated seconds elapsed={clock():.1f}", file=out)
    print("RESULT: " + ("all simulated scenarios behaved as expected" if ok else "UNEXPECTED BEHAVIOUR"), file=out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
