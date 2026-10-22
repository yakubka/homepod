"""Week 9 Task 1B: execution contract, command capabilities, request ids, SIMULATED adapter, action loop and response policy.

Everything here is SOFTWARE-VALIDATED / SIMULATED: a scripted fake adapter on a fake clock. No hardware, no port, no device, and no
real hardware result is used or invented. T01-T18 are the scenarios of docs/contracts/WEEK9_INTERFACE_DECISIONS_DRAFT.md section 10.
"""

import ast
import dataclasses
import io
import logging
import re
from pathlib import Path

import numpy as np
import pytest

from actions.contracts import (
    ALLOWED_REPLY_TRANSITIONS, PROGRESS_STATES, REPLY_STATES, TERMINAL_STATES, ActionContractError, CapabilityState,
    CommandCapabilityDeclaration, ExecutionResult, RequestIdGenerator, ResultState, SubmitStatus, check_timeout, parse_reply,
)
from actions.loop import ActionLoop, ActionOutcome, DiscardReason
from actions.response_policy import (
    ACCEPTED_TEXT, ALL_TEXTS, CAPABILITY_TEXT, COMPLETION_TEXT, STATE_TEXT, UNCONFIRMED_TEXT, respond,
)
from actions.simulated_adapter import DROP, SIMULATED_ADAPTER_IDENTITY, SIMULATED_LIGHTS_CAPABILITIES, FakeClock, SimulatedLightsAdapter
from fusion.contracts import Origin
from intents.contracts import (
    _ID as INTENT_ID, CommandAction, CommandTarget, ExecutionStatus, IntentCategory, IntentRequest, StructuredCommand,
)
from intents.router import route_intent

REPO = Path(__file__).resolve().parents[1]
ON, OFF = "turn on the lights", "turn off the lights"
LIGHTS = CommandTarget.CLASSROOM_LIGHTS
RS = ResultState


def make(caps=SIMULATED_LIGHTS_CAPABILITIES, timeout=2.0, token="aaaaaaaaaaaa", clock=None, adapter=None):
    clock = clock or FakeClock()
    adapter = adapter or SimulatedLightsAdapter(clock)
    ids = RequestIdGenerator(token_source=lambda n: token)
    return clock, adapter, ids, ActionLoop(adapter, caps, clock=clock, timeout_s=timeout)


def command(ids, text=ON):
    cmd = route_intent(IntentRequest(text, ids.next_id())).actionable_command
    assert cmd is not None
    return cmd


def raw(cmd, state, **kw):
    msg = {"request_id": cmd.request_id, "target": cmd.target.value, "action": cmd.action.value, "state": state}
    msg.update(kw)
    return msg


# ---------------------------------------------------------------------------------------------------
# T01-T18 (draft section 10)
# ---------------------------------------------------------------------------------------------------

def test_t01_lights_on_success_after_accept():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "ACCEPTED"), (0.3, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids))
    assert [p.state for p in o.progress] == [RS.SUBMITTED, RS.ACCEPTED] and o.result.state is RS.APPLIED_CONFIRMED
    d = respond(o)
    assert d.confirms_simulated_completion and d.text == "In the simulation, the classroom lights were turned on."
    assert o.result.origin is Origin.SIMULATED and o.result.validation_label == "SIMULATED"


def test_t02_lights_off_success_one_step():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.2, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids, OFF))
    assert o.result.state is RS.APPLIED_CONFIRMED and o.command.action is CommandAction.TURN_OFF
    assert respond(o).text == "In the simulation, the classroom lights were turned off."


def test_t03_device_unavailable_capability_and_not_delivered():
    unavailable = CommandCapabilityDeclaration({(LIGHTS, CommandAction.TURN_ON): CapabilityState.SUPPORTED_BUT_UNAVAILABLE}, source="simulation")
    _, ad, ids, loop = make(caps=unavailable)
    o = loop.execute(command(ids))
    assert not o.submitted and o.result is None and ad.submitted_ids == []
    assert respond(o) == respond(o) and not respond(o).confirms_simulated_completion
    assert respond(o).text == CAPABILITY_TEXT[CapabilityState.SUPPORTED_BUT_UNAVAILABLE]
    _, ad, ids, loop = make()
    ad.plan(submit=SubmitStatus.NOT_DELIVERED, replies=[(0.1, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids))
    assert o.result.state is RS.UNAVAILABLE and o.result.reason == "NOT_DELIVERED" and not o.submitted and o.progress == ()
    assert ad.submitted_ids == [] and respond(o).text == STATE_TEXT[RS.UNAVAILABLE]


def test_t04_rejected_reason_is_not_spoken():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "REJECTED", {"reason": "UNKNOWN_TARGET_CODE"})])
    o = loop.execute(command(ids))
    assert o.result.state is RS.REJECTED and o.result.reason == "UNKNOWN_TARGET_CODE"
    d = respond(o)
    assert d.text == STATE_TEXT[RS.REJECTED] and "UNKNOWN_TARGET_CODE" not in d.text and not d.confirms_simulated_completion


def test_t05_command_failure_after_accept():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "ACCEPTED"), (0.4, "FAILED")])
    o = loop.execute(command(ids))
    assert o.result.state is RS.FAILED and respond(o).text == STATE_TEXT[RS.FAILED]


def test_t06_delayed_ack_just_before_deadline_counts():
    clk, ad, ids, loop = make(timeout=2.0)
    ad.plan(replies=[(1.999, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids))
    assert o.result.state is RS.APPLIED_CONFIRMED and clk() == pytest.approx(1.999)


def test_t07_reply_after_deadline_is_late_and_never_spoken():
    clk, ad, ids, loop = make(timeout=2.0)
    ad.plan(replies=[(2.5, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids))
    assert o.result.state is RS.TIMEOUT_UNKNOWN and clk() == pytest.approx(2.0)
    late = loop.drain(1.0)
    assert [d.reason for d in late] == [DiscardReason.LATE_REPLY]
    assert o.result.state is RS.TIMEOUT_UNKNOWN and respond(o).text == UNCONFIRMED_TEXT


def test_t08_duplicate_reply_is_suppressed():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED"), (0.2, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids))
    assert o.result.state is RS.APPLIED_CONFIRMED
    assert [d.reason for d in loop.drain(1.0)] == [DiscardReason.DUPLICATE_REPLY]
    # the duplicate also cannot confirm a LATER command: it is discarded while that one waits
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED"), (0.2, "APPLIED_CONFIRMED")])
    first = loop.execute(command(ids))
    ad.plan(replies=[])
    second = loop.execute(command(ids, OFF))
    assert first.result.state is RS.APPLIED_CONFIRMED and second.result.state is RS.TIMEOUT_UNKNOWN
    assert [d.reason for d in second.discarded] == [DiscardReason.DUPLICATE_REPLY]


def test_t09_duplicate_request_id_refused_locally():
    _, ad, ids, loop = make()
    cmd = command(ids)
    loop.execute(cmd)
    with pytest.raises(ActionContractError, match="already used"):
        loop.execute(cmd)
    refused = make(caps=CommandCapabilityDeclaration())[3]
    c2 = command(ids)
    refused.execute(c2)
    with pytest.raises(ActionContractError):
        refused.execute(c2)                        # an id refused by the capability check is used too


@pytest.mark.parametrize("override, reason", [
    ({"request_id": "c-aaaaaaaaaaaa-99"}, DiscardReason.UNSOLICITED_OR_STALE),
    ({"request_id": "c-bbbbbbbbbbbb-1"}, DiscardReason.UNSOLICITED_OR_STALE),
    ({"target": "SMART_BULB"}, DiscardReason.MISMATCHED_TARGET_ACTION),
    ({"action": "TURN_OFF"}, DiscardReason.MISMATCHED_TARGET_ACTION),
    ({"state": "SUBMITTED"}, DiscardReason.MALFORMED_REPLY),
    ({"origin": "REAL"}, DiscardReason.MALFORMED_REPLY),
    ({"state": DROP}, DiscardReason.MALFORMED_REPLY),
])
def test_t10_stale_or_mismatched_reply_never_matches(override, reason):
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED", override)])
    o = loop.execute(command(ids))
    assert o.result.state is RS.TIMEOUT_UNKNOWN and [d.reason for d in o.discarded] == [reason]
    assert not respond(o).confirms_simulated_completion


def test_t11_timeout_is_unknown_not_failed_and_not_done():
    _, ad, ids, loop = make()
    o = loop.execute(command(ids))               # no plan: silence
    assert o.result.state is RS.TIMEOUT_UNKNOWN and o.result.state not in (RS.FAILED, RS.APPLIED_CONFIRMED)
    assert respond(o).text == UNCONFIRMED_TEXT and ad.submitted_ids == [o.command.request_id]   # submitted once, never retried


def test_t12_lost_connection_then_recovery():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.5, "APPLIED_CONFIRMED")])
    ad.lose_link(0.2)
    o = loop.execute(command(ids))
    assert o.result.state is RS.UNKNOWN and o.result.reason == "LINK_LOST_AFTER_SUBMISSION" and respond(o).text == UNCONFIRMED_TEXT
    blocked = loop.execute(command(ids))
    assert blocked.capability is CapabilityState.SUPPORTED_BUT_UNAVAILABLE and not blocked.submitted
    assert ad.submitted_ids == [o.command.request_id]                       # nothing replayed
    assert [d.reason for d in loop.drain(1.0)] == [DiscardReason.LATE_REPLY]  # the pre-loss reply arrives later: it changes nothing
    ad.reconnect()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    assert loop.execute(command(ids)).result.state is RS.APPLIED_CONFIRMED


def test_t13_restart_old_session_reply_never_matches():
    clock = FakeClock()
    _, ad, ids1, loop1 = make(clock=clock, token="111111111111")
    old = command(ids1)
    ad.plan(replies=[(3.0, "APPLIED_CONFIRMED")])         # arrives at t=3.0: after session 1's deadline, inside session 2's wait
    assert loop1.execute(old).result.state is RS.TIMEOUT_UNKNOWN
    _, _, ids2, loop2 = make(clock=clock, adapter=ad, token="222222222222")   # simulated restart: new session, new loop
    new = command(ids2)
    assert new.request_id != old.request_id
    ad.plan(replies=[])
    ad.inject(0.05, raw(old, "APPLIED_CONFIRMED", request_id=new.request_id.replace("222222222222", "111111111111")))
    o = loop2.execute(new)
    assert o.result.state is RS.TIMEOUT_UNKNOWN
    assert [d.reason for d in o.discarded] == [DiscardReason.UNSOLICITED_OR_STALE] * 2   # the scheduled old reply and the injected one


def test_t14_no_false_success_full_truth_table():
    cmd = command(RequestIdGenerator(token_source=lambda n: "abcabcabcabc"))
    for state in TERMINAL_STATES:
        for origin in (Origin.SIMULATED, Origin.UNKNOWN):
            for accepted in (False, True):
                if state is RS.UNAVAILABLE:
                    o = ActionOutcome(cmd, CapabilityState.SUPPORTED_AND_AVAILABLE, False, _res(cmd, state, origin, 1))
                else:
                    prog = (_res(cmd, RS.SUBMITTED, origin, 0),) + ((_res(cmd, RS.ACCEPTED, origin, 1),) if accepted else ())
                    o = ActionOutcome(cmd, CapabilityState.SUPPORTED_AND_AVAILABLE, True, _res(cmd, state, origin, 2), prog)
                d = respond(o)
                assert d.confirms_simulated_completion == (state is RS.APPLIED_CONFIRMED and origin is Origin.SIMULATED)
                assert ("In the simulation, the classroom lights were" in d.text) == d.confirms_simulated_completion
                if state is RS.TIMEOUT_UNKNOWN:
                    assert d.text == (ACCEPTED_TEXT if accepted and state is not RS.UNAVAILABLE else UNCONFIRMED_TEXT)
    for cap in CAPABILITY_TEXT:
        assert not respond(ActionOutcome(cmd, cap, False, None)).confirms_simulated_completion


def test_t15_provenance_always_simulated_never_real():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "ACCEPTED"), (0.2, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids))
    for r in (*o.progress, o.result):
        assert r.origin is Origin.SIMULATED and r.adapter_identity == SIMULATED_ADAPTER_IDENTITY and r.validation_label == "SIMULATED"
    assert ad.origin is Origin.SIMULATED
    with pytest.raises(ActionContractError, match="REAL"):
        dataclasses.replace(o.result, origin=Origin.REAL)
    with pytest.raises(ActionContractError):
        dataclasses.replace(o.result, validation_label="HARDWARE-VALIDATED")
    assert all("HARDWARE" not in t for t in ALL_TEXTS)


FORBIDDEN_IMPORTS = {"serial", "smbus", "smbus2", "RPi", "gpiozero", "lgpio", "pigpio", "spidev", "sounddevice", "socket", "requests",
                     "httpx", "http", "urllib", "paho", "bleak", "usb", "subprocess", "asyncio", "time"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module.split(".")[0]


def test_t16_no_real_executor_no_hardware_access():
    files = sorted((REPO / "actions").glob("*.py")) + [REPO / "scripts" / "week9_simulated_action_demo.py"]
    assert len(files) == 6
    for f in files:
        bad = set(_imports(f)) & FORBIDDEN_IMPORTS
        assert not bad, f"{f.name} imports {bad}"
    # Week 9 Task 1C: the runtime imports the action CONTRACT, LOOP and POLICY, never the simulated adapter (it builds no adapter);
    # with no injected loop a routed command still gets the fixed NOT_EXECUTED notice
    runtime = (REPO / "assistant" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(runtime)
    action_imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("actions")}
    assert action_imports == {"actions.contracts", "actions.loop", "actions.response_policy"} and "COMMAND_NOT_EXECUTED_TEXT" in runtime
    assert "SimulatedLightsAdapter" not in runtime and "FakeClock" not in runtime


def test_t17_capability_default_unknown_nothing_submitted():
    assert CommandCapabilityDeclaration().state_of(LIGHTS, CommandAction.TURN_ON) is CapabilityState.UNKNOWN_CAPABILITY
    _, ad, ids, loop = make(caps=CommandCapabilityDeclaration())
    o = loop.execute(command(ids))               # the router recognised it; that alone never makes it controllable
    assert o.capability is CapabilityState.UNKNOWN_CAPABILITY and not o.submitted and ad.submitted_ids == []
    assert respond(o).text == CAPABILITY_TEXT[CapabilityState.UNKNOWN_CAPABILITY]
    _, ad, ids, loop = make()                    # the simulation declares only the lights: the smart bulb stays UNKNOWN
    bulb = route_intent(IntentRequest("turn on the smart bulb", ids.next_id())).actionable_command
    assert loop.execute(bulb).capability is CapabilityState.UNKNOWN_CAPABILITY and ad.submitted_ids == []
    unsupported = CommandCapabilityDeclaration({(LIGHTS, CommandAction.TURN_ON): CapabilityState.UNSUPPORTED_TARGET}, source="simulation")
    _, ad, ids, loop = make(caps=unsupported)
    assert respond(loop.execute(command(ids))).text == CAPABILITY_TEXT[CapabilityState.UNSUPPORTED_TARGET]


@pytest.mark.parametrize("override", [{"reason": DROP, "state": "FAILED"}, {"state": "UNAVAILABLE", "reason": "X"}, {"reason": "lower"},
                                      {"target": "NOT_A_TARGET"}, {"request_id": "bad id"}, {"request_id": 7}])
def test_t18_unmapped_or_malformed_reply_is_never_success(override):
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED", override)])
    o = loop.execute(command(ids))
    assert o.result.state is RS.TIMEOUT_UNKNOWN and o.discarded[0].reason is DiscardReason.MALFORMED_REPLY


# ---------------------------------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------------------------------

def _res(cmd, state, origin=Origin.SIMULATED, seq=0, reason="R"):
    return ExecutionResult(cmd.request_id, cmd.target, cmd.action, state, origin, "simulated-classroom-lights", seq,
                           None if state is RS.APPLIED_CONFIRMED or state in PROGRESS_STATES else reason)


def test_state_sets_partition_and_transitions():
    assert PROGRESS_STATES | TERMINAL_STATES == set(RS) and not PROGRESS_STATES & TERMINAL_STATES
    assert RS.ACCEPTED in PROGRESS_STATES and RS.ACCEPTED not in TERMINAL_STATES
    assert REPLY_STATES == {RS.ACCEPTED, RS.APPLIED_CONFIRMED, RS.REJECTED, RS.FAILED}
    assert RS.REJECTED not in ALLOWED_REPLY_TRANSITIONS[RS.ACCEPTED] and RS.ACCEPTED not in ALLOWED_REPLY_TRANSITIONS[RS.ACCEPTED]


def test_result_is_immutable_and_validated():
    cmd = command(RequestIdGenerator(token_source=lambda n: "abcabcabcabc"))
    r = _res(cmd, RS.APPLIED_CONFIRMED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.state = RS.FAILED
    assert r.terminal and r.simulated_completion and not _res(cmd, RS.ACCEPTED).terminal
    bad = [dict(request_id="bad id"), dict(state="APPLIED_CONFIRMED"), dict(origin="SIMULATED"), dict(adapter_identity="Bad Name"),
           dict(sequence=True), dict(sequence=-1), dict(reason="lower"), dict(state=RS.FAILED, reason=None),
           dict(state=RS.TIMEOUT_UNKNOWN, reason=None)]
    for kw in bad:
        with pytest.raises(ActionContractError):
            dataclasses.replace(r, **kw)


def test_outcome_rejects_invalid_combinations():
    ids = RequestIdGenerator(token_source=lambda n: "abcabcabcabc")
    cmd, other = command(ids), command(ids)
    sub = (_res(cmd, RS.SUBMITTED),)
    with pytest.raises(ActionContractError):
        ActionOutcome(cmd, CapabilityState.UNKNOWN_CAPABILITY, True, None)
    with pytest.raises(ActionContractError):
        ActionOutcome(cmd, CapabilityState.UNKNOWN_CAPABILITY, False, _res(cmd, RS.APPLIED_CONFIRMED))
    with pytest.raises(ActionContractError):
        ActionOutcome(cmd, CapabilityState.SUPPORTED_AND_AVAILABLE, True, None, sub)
    with pytest.raises(ActionContractError):
        ActionOutcome(cmd, CapabilityState.SUPPORTED_AND_AVAILABLE, True, _res(other, RS.APPLIED_CONFIRMED), sub)    # wrong id
    with pytest.raises(ActionContractError):
        ActionOutcome(cmd, CapabilityState.SUPPORTED_AND_AVAILABLE, True, _res(cmd, RS.ACCEPTED), sub)              # not terminal
    with pytest.raises(ActionContractError):
        ActionOutcome(cmd, CapabilityState.SUPPORTED_AND_AVAILABLE, False, _res(cmd, RS.APPLIED_CONFIRMED))         # never submitted
    with pytest.raises(ActionContractError):
        ActionOutcome(cmd, CapabilityState.SUPPORTED_AND_AVAILABLE, True, _res(cmd, RS.APPLIED_CONFIRMED), ())      # no SUBMITTED event


def test_parse_reply_never_raises():
    for junk in (None, 3, "APPLIED_CONFIRMED", [], {}, {"request_id": "x"}, {"request_id": "a", "target": 1, "action": None, "state": []}):
        assert parse_reply(junk) is None
    ok = parse_reply({"request_id": "a-1", "target": "CLASSROOM_LIGHTS", "action": "TURN_ON", "state": "FAILED", "reason": "JAMMED"})
    assert ok.state is RS.FAILED and ok.reason == "JAMMED"


def test_capability_declaration_validated_and_immutable():
    for entries in ({("CLASSROOM_LIGHTS", "TURN_ON"): CapabilityState.SUPPORTED_AND_AVAILABLE},
                    {(LIGHTS, CommandAction.TURN_ON): "SUPPORTED_AND_AVAILABLE"}, [(LIGHTS, CommandAction.TURN_ON)]):
        with pytest.raises(ActionContractError):
            CommandCapabilityDeclaration(entries)
    src = {(LIGHTS, CommandAction.TURN_ON): CapabilityState.SUPPORTED_AND_AVAILABLE}
    decl = CommandCapabilityDeclaration(src, source="simulation")
    src[(LIGHTS, CommandAction.TURN_OFF)] = CapabilityState.SUPPORTED_AND_AVAILABLE
    assert decl.state_of(LIGHTS, CommandAction.TURN_OFF) is CapabilityState.UNKNOWN_CAPABILITY
    with pytest.raises(TypeError):
        decl.entries[(LIGHTS, CommandAction.TURN_OFF)] = CapabilityState.SUPPORTED_AND_AVAILABLE
    assert set(SIMULATED_LIGHTS_CAPABILITIES.entries) == {(LIGHTS, CommandAction.TURN_ON), (LIGHTS, CommandAction.TURN_OFF)}


@pytest.mark.parametrize("bad", [None, 0, -1.0, float("inf"), float("nan"), True, "2"])
def test_timeout_has_no_default_and_is_validated(bad):
    with pytest.raises(ActionContractError):
        check_timeout(bad)
    with pytest.raises(TypeError):
        ActionLoop(SimulatedLightsAdapter(FakeClock()), SIMULATED_LIGHTS_CAPABILITIES, clock=FakeClock())


def test_invalid_reply_transitions_are_discarded():
    _, ad, ids, loop = make()
    ad.plan(replies=[(0.1, "ACCEPTED"), (0.2, "ACCEPTED"), (0.3, "REJECTED"), (0.4, "APPLIED_CONFIRMED")])
    o = loop.execute(command(ids))
    assert [d.reason for d in o.discarded] == [DiscardReason.INVALID_TRANSITION] * 2 and o.result.state is RS.APPLIED_CONFIRMED


def test_delivery_uncertain_is_unknown_not_unavailable():
    _, ad, ids, loop = make()
    ad.plan(submit=SubmitStatus.DELIVERY_UNCERTAIN)
    o = loop.execute(command(ids))
    assert o.submitted and o.result.state is RS.UNKNOWN and o.result.reason == "DELIVERY_UNCERTAIN" and respond(o).text == UNCONFIRMED_TEXT


def test_loop_and_policy_reject_wrong_types():
    with pytest.raises(ActionContractError):
        ActionLoop(object(), SIMULATED_LIGHTS_CAPABILITIES, clock=FakeClock(), timeout_s=1)
    with pytest.raises(ActionContractError):
        ActionLoop(SimulatedLightsAdapter(FakeClock()), {}, clock=FakeClock(), timeout_s=1)
    with pytest.raises(ActionContractError):
        make()[3].execute({"target": "CLASSROOM_LIGHTS"})
    with pytest.raises(TypeError):
        respond(None)


# ---------------------------------------------------------------------------------------------------
# request ids
# ---------------------------------------------------------------------------------------------------

def test_request_id_format_and_length():
    gen = RequestIdGenerator(token_source=lambda n: "0123456789ab")
    ids = [gen.next_id() for _ in range(1000)]
    assert ids[0] == "c-0123456789ab-1" and ids[-1] == "c-0123456789ab-1000" and len(set(ids)) == 1000
    assert all(INTENT_ID.match(i) and len(i) <= 64 for i in ids)
    from actions.contracts import _ID
    assert _ID.pattern == INTENT_ID.pattern
    IntentRequest("x", ids[-1])                    # accepted by the frozen Week 8 contract
    assert RequestIdGenerator(prefix="cmd", token_source=lambda n: "0123456789ab").next_id() == "cmd-0123456789ab-1"


def test_request_ids_differ_across_sessions_and_tokens_are_validated():
    a = RequestIdGenerator(token_source=lambda n: "aaaaaaaaaaaa")
    b = RequestIdGenerator(token_source=lambda n: "bbbbbbbbbbbb")
    assert not {a.next_id() for _ in range(50)} & {b.next_id() for _ in range(50)}
    real1, real2 = RequestIdGenerator(), RequestIdGenerator()
    assert re.fullmatch(r"[0-9a-f]{12}", real1.session_id) and real1.session_id != real2.session_id   # residual collision risk 2**-48
    calls = []
    RequestIdGenerator(token_source=lambda n: calls.append(n) or "cccccccccccc")
    assert calls == [6]
    for bad in ("ABCDEF012345", "abc", "zzzzzzzzzzzz", 12, None):
        with pytest.raises(ActionContractError):
            RequestIdGenerator(token_source=lambda n, bad=bad: bad)
    for prefix in ("", "C", "toolongprefix", "a-b"):
        with pytest.raises(ActionContractError):
            RequestIdGenerator(prefix=prefix, token_source=lambda n: "aaaaaaaaaaaa")


def test_request_id_generator_is_not_clock_based():
    assert not {"time", "datetime"} & set(_imports(REPO / "actions" / "contracts.py"))


# ---------------------------------------------------------------------------------------------------
# privacy, demo, Week 8 preservation
# ---------------------------------------------------------------------------------------------------

def test_no_transcript_or_pcm_leakage():
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    log = logging.getLogger("homepod_ai")
    log.addHandler(handler)
    try:
        _, ad, ids, loop = make()
        ad.plan(replies=[(0.1, "ACCEPTED"), (0.2, "APPLIED_CONFIRMED")])
        o = loop.execute(command(ids, "please turn on the lights"))
    finally:
        log.removeHandler(handler)
    text = " ".join(r.getMessage() for r in records) + repr(o) + repr(respond(o))
    assert "please" not in text and "turn on the lights" not in text
    for obj in (o, o.result, *o.progress, respond(o)):
        assert not any(isinstance(getattr(obj, f.name), np.ndarray) for f in dataclasses.fields(obj))


def test_demo_is_deterministic_and_passes():
    from scripts.week9_simulated_action_demo import main
    runs = []
    for _ in range(2):
        buf = io.StringIO()
        assert main(out=buf) == 0
        runs.append(buf.getvalue())
    assert runs[0] == runs[1]
    out = runs[0]
    assert out.startswith("SIMULATED VESTA AI-TO-ACTION LOOP.\nNO PHYSICAL HARDWARE USED.\n")
    assert out.count("[OK]") == 7 and "UNEXPECTED" not in out and "simulated confirmations=3" in out and "HARDWARE-VALIDATED" in out.splitlines()[2]


def test_week8_contracts_preserved():
    assert list(ExecutionStatus) == [ExecutionStatus.NOT_EXECUTED]
    result = route_intent(IntentRequest(ON, "c-aaaaaaaaaaaa-1"))
    assert result.category is IntentCategory.CLASSROOM_CONTROL and result.spoken_confirmation_allowed is False
    assert result.execution_status is ExecutionStatus.NOT_EXECUTED and isinstance(result.actionable_command, StructuredCommand)
    _, ad, _, loop = make()
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    loop.execute(result.actionable_command)
    assert result.execution_status is ExecutionStatus.NOT_EXECUTED and result.spoken_confirmation_allowed is False   # nothing mutated
    for f in sorted((REPO / "actions").glob("*.py")):
        assert "assistant" not in set(_imports(f)), f.name          # the execution slice is independent of the production runtime


class _LateAdapter(SimulatedLightsAdapter):
    """Misbehaving adapter: ignores the deadline and returns its next event whenever it arrives (as a real link might)."""

    def next_event(self, deadline):
        return super().next_event(float("inf"))


def test_reply_returned_after_deadline_by_a_misbehaving_adapter_is_late():
    clock = FakeClock()
    ad = _LateAdapter(clock)
    loop = ActionLoop(ad, SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=2.0)
    ad.plan(replies=[(3.0, "APPLIED_CONFIRMED")])
    o = loop.execute(command(RequestIdGenerator(token_source=lambda n: "dddddddddddd")))
    assert o.result.state is RS.TIMEOUT_UNKNOWN and [d.reason for d in o.discarded] == [DiscardReason.LATE_REPLY]
    assert not respond(o).confirms_simulated_completion
