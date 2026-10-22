"""Week 9 Task 1C: the SIMULATED action loop inside the voice-assistant runtime (assistant/runtime.py).

SOFTWARE-VALIDATED / SIMULATED. Scripted wake, energy VAD, scripted STT, fake TTS / playback, the real router, the real arbiter and state
manager, and the Task 1B action loop with its scripted SIMULATED adapter on a fake clock. No microphone, speaker, ESP32, light, port or
network is used, and nothing here says anything about real voice-command success or latency.
"""

import ast
import dataclasses
import json
import logging
import re
import threading
from pathlib import Path

import numpy as np
import pytest

from actions.contracts import ActionContractError, CapabilityState, CommandCapabilityDeclaration, RequestIdGenerator, ResultState, SubmitStatus
from actions.loop import ActionLoop, DiscardReason
from actions.response_policy import ACCEPTED_TEXT, CAPABILITY_TEXT, COMPLETION_TEXT, STATE_TEXT, UNCONFIRMED_TEXT
from actions.simulated_adapter import DROP, SIMULATED_LIGHTS_CAPABILITIES, FakeClock, SimulatedLightsAdapter
from assistant.output_arbiter import Decision
from assistant.runtime import COMMAND_NOT_EXECUTED_TEXT, OutcomeKind, RuntimeConfigError, VoiceAssistantRuntime
from assistant.spoken_output import OutputStatus
from assistant.state import AssistantState
from evaluation import week8_intent_eval as w8
from fusion.contracts import Origin
from intents.contracts import CommandAction, CommandTarget, IntentRequest
from intents.router import route_intent
from tests.runtime_support import GUARD_S, UTTERANCE, Rig, RuntimePlayback, RuntimeTts, S, make_result

REPO = Path(__file__).resolve().parent.parent
K = OutcomeKind
RS = ResultState
ON_TEXT = COMPLETION_TEXT[(CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON)]
OFF_TEXT = COMPLETION_TEXT[(CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_OFF)]
LIFECYCLE = ["IDLE", "LISTENING", "CHECKING", "SPEAKING", "IDLE"]
FIXTURES = w8.simulated_answer_fixtures()


def sim(caps=SIMULATED_LIGHTS_CAPABILITIES, adapter_cls=SimulatedLightsAdapter, clock=None):
    clock = clock or FakeClock()
    adapter = adapter_cls(clock)
    return adapter, ActionLoop(adapter, caps, clock=clock, timeout_s=2.0)


def ids(token="aaaaaaaaaaaa"):
    return RequestIdGenerator(token_source=lambda n: token)


def rig(text, loop=None, token="aaaaaaaaaaaa", **kw):
    return Rig(replies=(f"hey jarvis {text}",), action_loop=loop, request_ids=ids(token), **kw)


def run(text, loop=None, **kw):
    r = rig(text, loop, **kw)
    outcomes = r.drive(UTTERANCE)
    assert len(outcomes) == 1, [o.kind for o in outcomes]
    return r, outcomes[0]


def again(r, text):
    """A second complete utterance on the same runtime (same session, next request id)."""
    r.runtime.restart_stream()
    r.stt.replies = [f"hey jarvis {text}"]
    r.stt.calls.clear()
    outcomes = r.drive(UTTERANCE)
    assert len(outcomes) == 1
    return outcomes[0]


class GatedAdapter(SimulatedLightsAdapter):
    """Holds the FIRST wait inside the loop on an Event, so a test can act while a command is in flight (still SIMULATED, no device)."""

    def __init__(self, clock):
        super().__init__(clock)
        self.entered, self.release, self._held = threading.Event(), threading.Event(), False

    def next_event(self, deadline):
        if not self._held:
            self._held = True
            self.entered.set()
            assert self.release.wait(GUARD_S), "adapter gate never released"
        return super().next_event(deadline)


# =========================================================================================
# default: the Week 8 behaviour is unchanged
# =========================================================================================

def test_without_an_action_loop_a_command_is_returned_not_executed_with_the_fixed_notice():
    r, o = run("turn on the lights")
    assert o.kind is K.COMMAND_NOT_EXECUTED and r.tts.texts == [COMMAND_NOT_EXECUTED_TEXT] and o.action is None
    assert o.confirms_simulated_completion is False and o.execution_status == "NOT_EXECUTED" and o.spoken_confirmation_allowed is False
    assert [s for s, _ in r.states()] == LIFECYCLE


def test_constructor_rejects_wrong_types_and_builds_no_adapter():
    base = dict(detector=Rig().detector, segmenter=Rig().segmenter, stt=Rig().stt, tts=RuntimeTts(), playback=RuntimePlayback())
    with pytest.raises(RuntimeConfigError):
        VoiceAssistantRuntime(**base, action_loop=object())
    with pytest.raises(RuntimeConfigError):
        VoiceAssistantRuntime(**base, request_ids="c-1")
    src = (REPO / "assistant" / "runtime.py").read_text(encoding="utf-8")
    assert "SimulatedLightsAdapter" not in src and "FakeClock" not in src and "ActionLoop(" not in src


# =========================================================================================
# the simulated round trip and the success rule
# =========================================================================================

def test_lights_on_round_trip_is_simulation_qualified_and_follows_the_normal_lifecycle():
    ad, loop = sim()
    ad.plan(replies=[(0.1, "ACCEPTED"), (0.3, "APPLIED_CONFIRMED")])
    r, o = run("turn on the lights", loop)
    assert o.kind is K.COMMAND_SIMULATED and r.tts.texts == [ON_TEXT] and ON_TEXT.startswith("In the simulation,")
    assert o.confirms_simulated_completion and o.action.result.state is RS.APPLIED_CONFIRMED and o.action.result.origin is Origin.SIMULATED
    assert o.execution_status == "NOT_EXECUTED" and o.spoken_confirmation_allowed is False          # the Week 8 intent layer is unchanged
    assert o.output.status is OutputStatus.COMPLETED and [s for s, _ in r.states()] == LIFECYCLE and r.playback.calls == 1


def test_lights_off_round_trip():
    ad, loop = sim()
    ad.plan(replies=[(0.2, "APPLIED_CONFIRMED")])
    r, o = run("turn off the lights", loop)
    assert r.tts.texts == [OFF_TEXT] and o.confirms_simulated_completion and o.action.command.action is CommandAction.TURN_OFF


@pytest.mark.parametrize("plan, text", [
    (dict(replies=[(0.1, "ACCEPTED")]), ACCEPTED_TEXT),
    (dict(replies=[]), UNCONFIRMED_TEXT),
    (dict(replies=[(0.1, "REJECTED")]), STATE_TEXT[RS.REJECTED]),
    (dict(replies=[(0.1, "ACCEPTED"), (0.2, "FAILED")]), STATE_TEXT[RS.FAILED]),
    (dict(submit=SubmitStatus.NOT_DELIVERED), STATE_TEXT[RS.UNAVAILABLE]),
    (dict(submit=SubmitStatus.DELIVERY_UNCERTAIN), UNCONFIRMED_TEXT),
])
def test_every_non_success_result_is_reported_truthfully_and_never_as_done(plan, text):
    ad, loop = sim()
    ad.plan(**plan)
    r, o = run("turn on the lights", loop)
    assert r.tts.texts == [text] and not o.confirms_simulated_completion and "In the simulation" not in text
    assert len(ad.submitted_ids) <= 1                                                                # never retried


def test_accepted_is_not_applied_and_timeout_is_neither_failure_nor_success():
    ad, loop = sim()
    ad.plan(replies=[(0.1, "ACCEPTED")])
    _, o = run("turn on the lights", loop)
    assert o.action.accepted and o.action.result.state is RS.TIMEOUT_UNKNOWN and not o.confirms_simulated_completion


@pytest.mark.parametrize("caps, expected", [
    (CommandCapabilityDeclaration(), CapabilityState.UNKNOWN_CAPABILITY),
    (CommandCapabilityDeclaration({(CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON): CapabilityState.SUPPORTED_BUT_UNAVAILABLE}, source="simulation"),
     CapabilityState.SUPPORTED_BUT_UNAVAILABLE),
    (CommandCapabilityDeclaration({(CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON): CapabilityState.UNSUPPORTED_TARGET}, source="simulation"),
     CapabilityState.UNSUPPORTED_TARGET),
])
def test_a_command_without_declared_availability_is_never_submitted(caps, expected):
    ad, loop = sim(caps)
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r, o = run("turn on the lights", loop)
    assert o.kind is K.COMMAND_SIMULATED and o.action.capability is expected and not o.action.submitted and ad.submitted_ids == []
    assert r.tts.texts == [CAPABILITY_TEXT[expected]] and not o.confirms_simulated_completion


def test_smart_bulb_is_not_declared_by_the_simulation_so_it_is_not_submitted():
    ad, loop = sim()
    _, o = run("turn on the smart bulb", loop)
    assert o.action.capability is CapabilityState.UNKNOWN_CAPABILITY and ad.submitted_ids == []


def test_link_lost_then_the_next_command_is_unavailable_and_nothing_is_replayed():
    ad, loop = sim()
    ad.plan(replies=[(0.5, "APPLIED_CONFIRMED")])
    ad.lose_link(0.2)
    r, o = run("turn on the lights", loop)
    assert o.action.result.state is RS.UNKNOWN and r.tts.texts == [UNCONFIRMED_TEXT]
    o2 = again(r, "turn on the lights")
    assert o2.action.capability is CapabilityState.SUPPORTED_BUT_UNAVAILABLE and len(ad.submitted_ids) == 1


@pytest.mark.parametrize("override, reason", [
    ({"request_id": "c-aaaaaaaaaaaa-9"}, DiscardReason.UNSOLICITED_OR_STALE),
    ({"target": "SMART_BULB"}, DiscardReason.MISMATCHED_TARGET_ACTION),
    ({"action": "TURN_OFF"}, DiscardReason.MISMATCHED_TARGET_ACTION),
    ({"state": DROP}, DiscardReason.MALFORMED_REPLY),
    ({"origin": "REAL"}, DiscardReason.MALFORMED_REPLY),
])
def test_wrong_or_malformed_acknowledgements_never_confirm(override, reason):
    ad, loop = sim()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED", override)])
    r, o = run("turn on the lights", loop)
    assert [d.reason for d in o.action.discarded] == [reason] and r.tts.texts == [UNCONFIRMED_TEXT] and not o.confirms_simulated_completion


def test_stale_reply_after_a_simulated_restart_cannot_confirm_the_new_session():
    clock = FakeClock()
    ad, loop1 = sim(clock=clock)
    ad.plan(replies=[(3.0, "APPLIED_CONFIRMED")])                  # arrives after session 1's deadline, inside session 2's wait
    r1, o1 = run("turn on the lights", loop1, token="111111111111")
    assert o1.action.result.state is RS.TIMEOUT_UNKNOWN
    loop2 = ActionLoop(ad, SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=2.0)      # restart: new loop, new runtime, new session
    ad.plan(replies=[])
    r2, o2 = run("turn on the lights", loop2, token="222222222222")
    assert o2.command.request_id == "c-222222222222-1" != o1.command.request_id
    assert [d.reason for d in o2.action.discarded] == [DiscardReason.UNSOLICITED_OR_STALE] and r2.tts.texts == [UNCONFIRMED_TEXT]


def test_a_duplicate_reply_gives_exactly_one_audible_confirmation():
    ad, loop = sim()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED"), (0.2, "APPLIED_CONFIRMED")])
    r, o = run("turn on the lights", loop)
    ad.plan(replies=[])
    o2 = again(r, "turn on the lights")
    assert [d.reason for d in o2.action.discarded] == [DiscardReason.DUPLICATE_REPLY]
    assert r.tts.texts == [ON_TEXT, UNCONFIRMED_TEXT] and [o.confirms_simulated_completion, o2.confirms_simulated_completion] == [True, False]


def test_two_acknowledgements_for_one_request_and_a_late_result_after_timeout():
    ad, loop = sim()
    ad.plan(replies=[(0.1, "ACCEPTED"), (0.2, "ACCEPTED"), (0.3, "APPLIED_CONFIRMED")])
    r, o = run("turn on the lights", loop)
    assert [d.reason for d in o.action.discarded] == [DiscardReason.INVALID_TRANSITION] and r.tts.texts == [ON_TEXT]
    ad.plan(replies=[(2.5, "APPLIED_CONFIRMED")])
    o2 = again(r, "turn off the lights")
    assert o2.action.result.state is RS.TIMEOUT_UNKNOWN and not o2.confirms_simulated_completion
    ad.plan(replies=[])
    o3 = again(r, "turn on the lights")
    assert [d.reason for d in o3.action.discarded] == [DiscardReason.LATE_REPLY] and r.tts.texts.count(ON_TEXT) == 1


def test_unknown_origin_is_not_relabelled_and_real_origin_fails_closed():
    class UnknownOrigin(SimulatedLightsAdapter):
        origin = property(lambda self: Origin.UNKNOWN)

    class RealOrigin(SimulatedLightsAdapter):
        origin = property(lambda self: Origin.REAL)

    ad, loop = sim(adapter_cls=UnknownOrigin)
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r, o = run("turn on the lights", loop)
    assert o.action.result.origin is Origin.UNKNOWN and r.tts.texts == [UNCONFIRMED_TEXT] and not o.confirms_simulated_completion
    ad, loop = sim(adapter_cls=RealOrigin)
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r = rig("turn on the lights", loop)
    with pytest.raises(ActionContractError, match="REAL"):
        r.drive(UTTERANCE)
    assert r.tts.texts == [] and r.state.current_state is AssistantState.IDLE                     # a contract bug is never spoken


# =========================================================================================
# request ids: one per interaction, the same in every layer
# =========================================================================================

def test_one_request_id_correlates_intent_command_action_arbiter_and_state():
    seen = []
    ad, loop = sim()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r, o = run("turn on the lights", loop, route=lambda req: (seen.append(req.request_id), route_intent(req))[1])
    rid = seen[0]
    assert rid == "c-aaaaaaaaaaaa-1" == o.command.request_id == o.action.result.request_id == o.arbitration.request_id == ad.submitted_ids[0]
    assert all(p.request_id == rid for p in o.action.progress)
    assert [e.request_id for e in r.events if e.state is AssistantState.SPEAKING] == [rid]


def test_one_counter_for_questions_and_commands_and_the_default_session_is_random():
    ad, loop = sim()
    r, o1 = run("what can you do", loop)
    o2 = again(r, "turn on the lights")
    assert o1.arbitration.request_id == "c-aaaaaaaaaaaa-1" and o2.command.request_id == "c-aaaaaaaaaaaa-2"
    default_a, default_b = Rig(replies=("hey jarvis what can you do",)), Rig(replies=("hey jarvis what can you do",))
    a, b = default_a.drive(UTTERANCE)[0].arbitration.request_id, default_b.drive(UTTERANCE)[0].arbitration.request_id
    assert re.fullmatch(r"c-[0-9a-f]{12}-1", a) and a != b                                         # probabilistic: 2**-48 per pair
    assert "turn" not in o2.command.request_id and "light" not in o2.command.request_id


# =========================================================================================
# what may and may not reach the action loop
# =========================================================================================

@pytest.mark.parametrize("status", [S.WAKE_ONLY, S.STT_EMPTY, S.NO_SPEECH, S.TOO_SHORT, S.INVALID_ACTIVATION, S.MAX_DURATION, S.STREAM_ENDED, S.STT_FAILED])
def test_only_a_complete_transcript_can_reach_the_action_loop(status):
    ad, loop = sim()
    r = rig("turn on the lights", loop)
    text = "turn on the lights" if status in (S.MAX_DURATION, S.STREAM_ENDED) else ""
    o = r.runtime.handle_result(make_result(status, text))
    assert o.action is None and ad.submitted_ids == [] and ON_TEXT not in r.tts.texts


@pytest.mark.parametrize("text", ["turn on the projector", "turn on the light", "turn on", "explain machine learning", "is the room dark",
                                  "is anyone in the room", "set the volume to 150 percent", "turn the lights on and off", "what is the temperature"])
def test_rejected_ambiguous_general_and_unanswerable_requests_never_call_the_loop(text):
    ad, loop = sim()
    _, o = run(text, loop)
    assert o.action is None and ad.submitted_ids == [] and o.kind is not K.COMMAND_SIMULATED


def _questions():
    doc = json.loads((REPO / "evaluation" / "ai_question_test_set_v1.json").read_text(encoding="utf-8"))
    return [(q["id"], text) for q in doc["questions"] for text in [q["question"], *q.get("accepted_variants", [])]]


@pytest.mark.parametrize("fixture", ["FRESH", "NONE"])
def test_team_leader_q1_to_q10_are_unchanged_by_an_injected_action_loop(fixture):
    def outcome(text, loop):
        r = Rig(replies=(f"hey jarvis {text.rstrip('?.').lower()}",), action_loop=loop, request_ids=ids(),
                context_provider=lambda: FIXTURES[fixture])
        o = r.drive(UTTERANCE)[0]
        return (o.kind, o.routing_status, o.intent_category, o.answer_status, o.question_id, o.answer_source, tuple(r.tts.texts)), o
    ad, loop = sim()
    for qid, text in _questions():
        without, _ = outcome(text, None)
        with_loop, o = outcome(text, loop)
        assert with_loop == without, (qid, text)
        assert o.action is None and o.command is None, (qid, text)
    assert ad.submitted_ids == []


# =========================================================================================
# output, state and concurrency
# =========================================================================================

def test_speaking_starts_only_at_the_first_buffer_and_the_loop_ran_inside_checking():
    hold = threading.Event()
    ad, loop = sim()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r = rig("turn on the lights", loop, playback=RuntimePlayback(before_first=hold))
    t = threading.Thread(target=lambda: r.drive(UTTERANCE), daemon=True)
    t.start()
    assert r.playback.called.wait(GUARD_S)
    assert r.tts.texts == [ON_TEXT] and r.state.current_state is AssistantState.CHECKING and len(ad.submitted_ids) == 1
    hold.set()
    t.join(GUARD_S)
    assert [s for s, _ in r.states()] == LIFECYCLE and r.runtime.input_state_error_count == 0


def test_a_command_while_the_speaker_is_busy_is_not_admitted_and_not_submitted():
    gate = threading.Event()
    ad, loop = sim()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r = rig("what can you do", loop, tts=RuntimeTts(gate=gate))
    first = threading.Thread(target=lambda: r.runtime.handle_result(make_result(S.TRANSCRIBED, "what can you do")), daemon=True)
    first.start()
    assert r.tts.entered.wait(GUARD_S)
    before = r.state.current_event
    o = r.runtime.handle_result(make_result(S.TRANSCRIBED, "turn on the lights"))
    assert o.kind is K.COMMAND_NOT_ADMITTED and o.action is None and o.arbitration is None and ad.submitted_ids == []
    assert r.state.current_event == before and len(r.tts.texts) == 1
    gate.set()
    first.join(GUARD_S)
    assert r.playback.max_concurrent == 1


def test_two_simultaneous_commands_only_one_is_admitted_and_one_confirmation_is_spoken():
    clock = FakeClock()
    ad, loop = sim(adapter_cls=GatedAdapter, clock=clock)
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r = rig("turn on the lights", loop)
    box = {}
    t = threading.Thread(target=lambda: box.update(o=r.runtime.handle_result(make_result(S.TRANSCRIBED, "turn on the lights"))), daemon=True)
    t.start()
    assert ad.entered.wait(GUARD_S)
    second = r.runtime.handle_result(make_result(S.TRANSCRIBED, "turn off the lights"))
    assert second.kind is K.COMMAND_NOT_ADMITTED and second.action is None and len(ad.submitted_ids) == 1
    assert r.state.current_state is AssistantState.CHECKING                        # the in-flight interaction still owns the state
    ad.release.set()
    t.join(GUARD_S)
    assert box["o"].confirms_simulated_completion and r.tts.texts == [ON_TEXT] and r.playback.max_concurrent == 1


def test_speaker_taken_while_the_loop_waits_rejects_the_reply_without_overlap_documented_limit():
    gate = threading.Event()
    clock = FakeClock()
    ad, loop = sim(adapter_cls=GatedAdapter, clock=clock)
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r = rig("turn on the lights", loop, tts=RuntimeTts(gate=gate))
    box = {}
    a = threading.Thread(target=lambda: box.update(o=r.runtime.handle_result(make_result(S.TRANSCRIBED, "turn on the lights"))), daemon=True)
    a.start()
    assert ad.entered.wait(GUARD_S)
    b = threading.Thread(target=lambda: r.runtime.handle_result(make_result(S.TRANSCRIBED, "what can you do")), daemon=True)
    b.start()
    assert r.tts.entered.wait(GUARD_S)
    ad.release.set()
    a.join(GUARD_S)
    o = box["o"]
    assert o.kind is K.COMMAND_SIMULATED and o.action.result.state is RS.APPLIED_CONFIRMED and o.output is None
    assert o.arbitration.decision is Decision.REJECTED_ACTIVE_OUTPUT and ON_TEXT not in r.tts.texts       # simulated action done, reply NOT heard
    gate.set()
    b.join(GUARD_S)
    assert r.playback.max_concurrent == 1


def test_stop_during_the_loop_cannot_cancel_it_or_its_reply_documented_limit():
    clock = FakeClock()
    ad, loop = sim(adapter_cls=GatedAdapter, clock=clock)
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r = rig("turn on the lights", loop)
    box = {}
    t = threading.Thread(target=lambda: box.update(o=r.runtime.handle_result(make_result(S.TRANSCRIBED, "turn on the lights"))), daemon=True)
    t.start()
    assert ad.entered.wait(GUARD_S)
    assert r.runtime.stop_output() is False                                          # nothing is playing yet; no cancellation contract exists
    ad.release.set()
    t.join(GUARD_S)
    assert box["o"].output.status is OutputStatus.COMPLETED and r.tts.texts == [ON_TEXT]


def test_a_loop_bug_propagates_is_never_spoken_and_the_runtime_recovers():
    class Broken(SimulatedLightsAdapter):
        fail = True

        def submit(self, command):
            if Broken.fail:
                raise RuntimeError("adapter bug")
            return super().submit(command)

    ad, loop = sim(adapter_cls=Broken)
    r = rig("turn on the lights", loop)
    with pytest.raises(RuntimeError, match="adapter bug"):
        r.drive(UTTERANCE)
    assert r.tts.texts == [] and r.state.current_state is AssistantState.IDLE
    Broken.fail = False
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    o = again(r, "turn on the lights")
    assert o.confirms_simulated_completion and r.tts.texts == [ON_TEXT] and o.command.request_id.endswith("-2")


# =========================================================================================
# privacy, devices, architecture
# =========================================================================================

def test_no_transcript_or_pcm_leaves_through_logs_or_outcomes():
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    log = logging.getLogger("homepod_ai")
    log.addHandler(handler)
    try:
        ad, loop = sim()
        ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
        _, o = run("please turn on the lights", loop)
    finally:
        log.removeHandler(handler)
    text = " ".join(rec.getMessage() for rec in records) + repr(o.action) + repr(o.command)
    assert "please" not in text and "turn on the lights" not in text
    assert "action_state=APPLIED_CONFIRMED" in text and "simulated_confirmation=True" in text
    for obj in (o, o.action, o.action.result):
        assert not any(isinstance(getattr(obj, f.name), np.ndarray) for f in dataclasses.fields(obj))


def test_no_audio_device_stream_is_opened_during_a_simulated_round_trip(monkeypatch):
    import sounddevice as sd
    for name in ("OutputStream", "InputStream", "Stream", "RawOutputStream", "RawInputStream", "RawStream", "play", "rec", "playrec"):
        monkeypatch.setattr(sd, name, lambda *a, _n=name, **k: pytest.fail(f"sounddevice.{_n} was called"))
    ad, loop = sim()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    r, o = run("turn on the lights", loop)
    assert o.confirms_simulated_completion and r.playback.calls == 1


def test_the_actions_package_still_imports_nothing_from_assistant():
    for f in sorted((REPO / "actions").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m == "assistant" or m.startswith("assistant.") for m in mods), f.name


def test_the_voice_to_action_demo_is_deterministic_and_passes():
    import io
    from scripts.week9_voice_to_action_demo import main
    outs = []
    for _ in range(2):
        buf = io.StringIO()
        assert main(out=buf) == 0
        outs.append(buf.getvalue())
    assert outs[0] == outs[1]
    assert outs[0].startswith("SIMULATED VESTA VOICE-TO-ACTION DEMO.\nFAKE AUDIO INPUT/OUTPUT.\nNO PHYSICAL HARDWARE CONTROL.\n")
    assert "UNEXPECTED" not in outs[0] and outs[0].count("[OK]") == 10
