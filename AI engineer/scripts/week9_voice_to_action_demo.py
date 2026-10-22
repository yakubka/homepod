"""VESTA WEEK 9 TASK 1C DEMO - SIMULATED VOICE-TO-ACTION LOOP THROUGH THE REAL RUNTIME. SOFTWARE ONLY, DETERMINISTIC.

    FAKE scripted audio -> scripted wake -> energy VAD -> scripted STT -> VoicePipeline TRANSCRIBED -> the real router
        -> validated StructuredCommand -> command-capability check -> SIMULATED adapter (scripted replies, fake clock)
        -> correlated result -> truthful response policy -> the runtime's ONE OutputArbiter -> FAKE tone TTS -> FAKE playback

Every reply goes through VoiceAssistantRuntime (assistant/runtime.py), which owns the one arbiter and the one state manager. The request-id
session tokens are FIXED here so the output is reproducible (production runtimes draw a random session). The simulated timeout is a test
value (2.0 simulated seconds), not a hardware value (decision D4 is OPEN). No light, ESP32, microphone or loudspeaker is used.

    python -m scripts.week9_voice_to_action_demo

Exit codes: 0 = every scenario behaved as expected; 1 = a scenario behaved differently.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from actions.contracts import CommandCapabilityDeclaration, RequestIdGenerator
from actions.loop import ActionLoop
from actions.response_policy import respond as action_response
from actions.simulated_adapter import SIMULATED_LIGHTS_CAPABILITIES, FakeClock, SimulatedLightsAdapter
from assistant.answering import AnswerContext, Capability, CapabilityDeclaration
from assistant.runtime import VoiceAssistantRuntime
from assistant.state import AssistantStateManager
from audio.utterance import UtteranceSegmenter
from scripts.vesta_runtime_demo import (
    BLOCK, PERIOD, SPEECH, WAKE_CHUNK, WAKE_CHUNK_IN_PERIOD, DemoPlayback, DemoTts, EnergyVad, ScriptedStt, ScriptedWake, segmenter_config,
)

BANNER = "SIMULATED VESTA VOICE-TO-ACTION DEMO.\nFAKE AUDIO INPUT/OUTPUT.\nNO PHYSICAL HARDWARE CONTROL."
LABEL = "Label: SOFTWARE-VALIDATED / SIMULATED (PROVISIONAL contract, team decisions D1-D12 OPEN). Never HARDWARE-VALIDATED."
SIM_TIMEOUT_S = 2.0
OLD_SESSION_ID = "c-5eed0000a000-5"          # an id from a simulated EARLIER session: its reply must never match

# (scenario, what the fake STT hears, expected outcome kind, expected action detail, expected simulated confirmation)
MAIN = (
    ("A lights ON", "turn on the lights", "COMMAND_SIMULATED", "APPLIED_CONFIRMED", True),
    ("B lights OFF", "turn off the lights", "COMMAND_SIMULATED", "APPLIED_CONFIRMED", True),
    ("E accepted only", "turn on the lights", "COMMAND_SIMULATED", "TIMEOUT_UNKNOWN", False),
    ("F timeout", "turn on the lights", "COMMAND_SIMULATED", "TIMEOUT_UNKNOWN", False),
    ("G wrong / stale ack", "turn on the lights", "COMMAND_SIMULATED", "TIMEOUT_UNKNOWN", False),
    ("H information question", "what can you do", "ANSWER_OUTPUT", None, False),
    ("I unsupported request", "turn on the projector", "REQUEST_REJECTED", None, False),
    ("I ambiguous request", "turn on the light", "REQUEST_REJECTED", None, False),
)
PLANS = (                                    # the scripted SIMULATED controller replies, one per submitted command, in order
    dict(replies=[(0.1, "ACCEPTED"), (0.3, "APPLIED_CONFIRMED")]),                                   # A
    dict(replies=[(0.2, "APPLIED_CONFIRMED")]),                                                      # B
    dict(replies=[(0.1, "ACCEPTED")]),                                                               # E
    dict(replies=[]),                                                                                # F
    dict(replies=[(0.1, "APPLIED_CONFIRMED", {"request_id": OLD_SESSION_ID}), (0.2, "APPLIED_CONFIRMED", {"action": "TURN_OFF"})]),  # G
)
CAPS = CapabilityDeclaration(frozenset({Capability.PEOPLE_COUNT, Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS,
                                        Capability.ANNOUNCEMENTS}))


def _stream(n: int) -> np.ndarray:
    x = np.zeros(PERIOD * n, np.int16)
    for k in range(n):
        a, b = k * PERIOD + SPEECH[0], k * PERIOD + SPEECH[1]
        x[a:b] = (2000 + np.arange(a, b) % 1000).astype(np.int16)
    return x


def _runtime(sentences, token, action_loop):
    wake = ScriptedWake(fire=[WAKE_CHUNK_IN_PERIOD + k * (PERIOD // WAKE_CHUNK) for k in range(len(sentences))])
    playback, state = DemoPlayback(), AssistantStateManager()
    events = []
    state.subscribe(events.append, replay_current=True)
    runtime = VoiceAssistantRuntime(
        detector=wake, segmenter=UtteranceSegmenter(segmenter_config(), EnergyVad()), stt=ScriptedStt([f"hey jarvis {s}" for s in sentences]),
        tts=DemoTts(), playback=playback, state=state, context_provider=lambda: AnswerContext(classroom_state=None, capabilities=CAPS),
        action_loop=action_loop, request_ids=RequestIdGenerator(token_source=lambda n: token))
    x = _stream(len(sentences))
    outcomes = []
    for lo in range(0, x.size, BLOCK):
        outcomes += runtime.feed(x[lo:lo + BLOCK])
    return outcomes + runtime.finish(), playback, events, runtime


def _row(name, o, want_kind, want_detail, want_confirm, extra_ok=True):
    action = o.action
    detail = (action.result.state.value if action.result else action.capability.value) if action else (o.answer_status or o.routing_status)
    good = (o.pipeline_status.value == "TRANSCRIBED" and o.kind.value == want_kind and o.confirms_simulated_completion is want_confirm
            and (want_detail is None or detail == want_detail) and o.output is not None and o.output.status.value == "COMPLETED"
            and o.execution_status == "NOT_EXECUTED" and extra_ok)
    discards = (",".join(d.reason.value for d in action.discarded) or "-") if action else "-"
    line = (f"{name}: id={o.arbitration.request_id if o.arbitration else '-'} pipeline={o.pipeline_status.value} routing={o.routing_status} "
            f"category={o.intent_category} outcome={o.kind.value} action={detail if action else '-'} "
            f"submitted={action.submitted if action else '-'} origin={action.result.origin.value if action and action.result else '-'} "
            f"discarded={discards} confirms={o.confirms_simulated_completion} arbiter={o.arbitration.decision.value if o.arbitration else '-'} "
            f"output={o.output.status.value if o.output else '-'}  [{'OK' if good else 'UNEXPECTED'}]")
    if action:                                         # the fixed policy sentence the arbiter played (fake playback)
        line += f"\n   says: \"{action_response(action).text}\""
    return good, line


def main(argv=None, *, out=None) -> int:
    out = out or sys.stdout
    rows, ok = [], True

    clock = FakeClock()
    adapter = SimulatedLightsAdapter(clock)
    for plan in PLANS:
        adapter.plan(**plan)
    loop = ActionLoop(adapter, SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=SIM_TIMEOUT_S)
    outcomes, playback, events, runtime = _runtime([s for _, s, *_ in MAIN], "5eed0000c001", loop)
    ok = ok and len(outcomes) == len(MAIN)
    for (name, _, kind, detail, confirm), o in zip(MAIN, outcomes):
        extra = True
        if name.startswith("E"):
            extra = o.action.accepted
        if name.startswith("G"):
            extra = [d.reason.value for d in o.action.discarded] == ["UNSOLICITED_OR_STALE", "MISMATCHED_TARGET_ACTION"]
        if name.startswith(("H", "I")):
            extra = o.action is None
        good, line = _row(name, o, kind, detail, confirm, extra)
        ok, rows = ok and good, rows + [line]
    submissions_main = len(adapter.submitted_ids)
    ok = ok and submissions_main == 5 and runtime.input_state_error_count == 0
    states_main = [e.state.value for e in events]
    ok = ok and states_main == ["IDLE"] + ["LISTENING", "CHECKING", "SPEAKING", "IDLE"] * len(MAIN)

    # C. the same command with NO action loop: the Week 8 behaviour, nothing executed
    outcomes_c, _, _, _ = _runtime(["turn on the lights"], "5eed0000c002", None)
    good, line = _row("C no action loop", outcomes_c[0], "COMMAND_NOT_EXECUTED", "ROUTED", False, outcomes_c[0].action is None)
    ok, rows = ok and good and len(outcomes_c) == 1, rows + [line]

    # D. a loop whose capability declaration declares NOTHING: UNKNOWN_CAPABILITY, never submitted
    clock_d = FakeClock()
    adapter_d = SimulatedLightsAdapter(clock_d)
    adapter_d.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    loop_d = ActionLoop(adapter_d, CommandCapabilityDeclaration(), clock=clock_d, timeout_s=SIM_TIMEOUT_S)
    outcomes_d, _, _, _ = _runtime(["turn on the lights"], "5eed0000c003", loop_d)
    good, line = _row("D capability UNKNOWN", outcomes_d[0], "COMMAND_SIMULATED", "UNKNOWN_CAPABILITY", False, adapter_d.submitted_ids == [])
    ok, rows = ok and good and len(outcomes_d) == 1, rows + [line]

    print(BANNER, file=out)
    print(LABEL, file=out)
    for line in rows:
        print(line, file=out)
    print(f"main runtime: simulated submissions={submissions_main} (A, B, E, F, G only) spoken replies={playback.calls} (fake playback) "
          f"state errors={runtime.input_state_error_count}", file=out)
    print("states (main runtime): IDLE -> " + " | ".join(["LISTENING -> CHECKING -> SPEAKING -> IDLE"] * 2) + f" ... x{len(MAIN)}", file=out)
    print("RESULT: " + ("all scenarios behaved as expected" if ok else "UNEXPECTED BEHAVIOUR"), file=out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
