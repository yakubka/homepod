"""Team Leader AI-8: the assistant state contract (assistant/state.py). SOFTWARE-VALIDATED, in-process, no hardware.

LISTENING = RED, CHECKING = BLUE, SPEAKING = GREEN, IDLE = NO_ACTIVE_COLOR. Nothing here drives an LED, a face, a display or a
transport; a fake subscriber stands in for the Vision/Face side. Live Vision/Face synchronization is NOT established by these tests.
"""

import ast
import dataclasses
import itertools
import logging
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from assistant import state as st
from assistant.state import (
    ALLOWED_TRANSITIONS, AssistantState, AssistantStateManager, COLOR_OF, InvalidStateTransition, StateColor, StateError, StateEvent,
    StateReason, StateReentrancyError, StateRequestError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
S = AssistantState
SECRET = "my secret exam answer is forty two"
WAIT = 10


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    st.logger.addHandler(handler)
    yield handler.lines
    st.logger.removeHandler(handler)


def walk(mgr, *steps):
    """steps: (state, reason) pairs."""
    return [mgr.transition(s, r) for s, r in steps]


ANSWER_PATH = ((S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED), (S.CHECKING, StateReason.UTTERANCE_CAPTURED),
               (S.SPEAKING, StateReason.PLAYBACK_STARTED), (S.IDLE, StateReason.PLAYBACK_COMPLETED))


# ---- the pinned contract --------------------------------------------------------------------------------------------------

def test_the_ai8_colour_mapping_is_pinned_exactly():
    assert {s.value: c.value for s, c in COLOR_OF.items()} == {"IDLE": "NO_ACTIVE_COLOR", "LISTENING": "RED", "CHECKING": "BLUE", "SPEAKING": "GREEN"}
    assert COLOR_OF[S.LISTENING] is StateColor.RED and COLOR_OF[S.CHECKING] is StateColor.BLUE and COLOR_OF[S.SPEAKING] is StateColor.GREEN
    assert COLOR_OF[S.IDLE] is StateColor.NO_ACTIVE_COLOR                 # no authoritative idle colour exists, so none is invented
    assert {s.name for s in S} == {"IDLE", "LISTENING", "CHECKING", "SPEAKING"}
    assert {c.name for c in StateColor} == {"NO_ACTIVE_COLOR", "RED", "BLUE", "GREEN"}
    assert len(COLOR_OF) == len(S)


def test_the_mapping_and_the_transition_table_cannot_be_modified_at_runtime():
    with pytest.raises(TypeError):
        COLOR_OF[S.LISTENING] = StateColor.GREEN
    with pytest.raises(TypeError):
        ALLOWED_TRANSITIONS[S.IDLE] = frozenset()
    assert ALLOWED_TRANSITIONS == {S.IDLE: {S.LISTENING, S.CHECKING}, S.LISTENING: {S.CHECKING, S.IDLE}, S.CHECKING: {S.SPEAKING, S.IDLE}, S.SPEAKING: {S.IDLE}}


def test_every_emitted_event_carries_the_colour_of_its_state():
    mgr = AssistantStateManager()
    events = walk(mgr, *ANSWER_PATH)
    assert [e.color_semantic for e in events] == [StateColor.RED, StateColor.BLUE, StateColor.GREEN, StateColor.NO_ACTIVE_COLOR]
    assert mgr.current_color is StateColor.NO_ACTIVE_COLOR


def test_an_event_cannot_carry_a_colour_that_does_not_belong_to_its_state():
    with pytest.raises(StateRequestError):
        StateEvent(S.LISTENING, S.IDLE, StateColor.GREEN, 1.0, StateReason.SPEECH_INPUT_ACTIVATED, 1)


def test_only_the_initial_event_has_sequence_zero_and_no_previous_state():
    StateEvent(S.IDLE, None, StateColor.NO_ACTIVE_COLOR, 1.0, StateReason.INITIAL, 0)                          # the one legal initial event
    with pytest.raises(StateRequestError):
        StateEvent(S.LISTENING, None, StateColor.RED, 1.0, StateReason.SPEECH_INPUT_ACTIVATED, 1)             # a later event must say where it came from
    with pytest.raises(StateRequestError):
        StateEvent(S.LISTENING, S.IDLE, StateColor.RED, 1.0, StateReason.SPEECH_INPUT_ACTIVATED, 0)           # sequence 0 is reserved for the initial event
    with pytest.raises(StateRequestError):
        StateEvent(S.IDLE, None, StateColor.NO_ACTIVE_COLOR, 1.0, "INITIAL", 0)                                # reason must be a StateReason
    with pytest.raises(StateRequestError):
        StateEvent(S.IDLE, None, StateColor.NO_ACTIVE_COLOR, float("nan"), StateReason.INITIAL, 0)
    with pytest.raises(StateRequestError):
        StateEvent(S.IDLE, None, StateColor.NO_ACTIVE_COLOR, 1.0, StateReason.INITIAL, True)


# ---- unit behaviour -----------------------------------------------------------------------------------------------------

def test_initial_state_is_idle_with_the_initial_event():
    mgr = AssistantStateManager()
    e = mgr.current_event
    assert mgr.current_state is S.IDLE and mgr.current_color is StateColor.NO_ACTIVE_COLOR
    assert (e.sequence_number, e.previous_state, e.reason_code, e.request_id) == (0, None, StateReason.INITIAL, None)


def test_the_normal_answer_path_and_every_event_field():
    mgr = AssistantStateManager()
    events = walk(mgr, *ANSWER_PATH)
    assert [(e.previous_state, e.state) for e in events] == [(S.IDLE, S.LISTENING), (S.LISTENING, S.CHECKING), (S.CHECKING, S.SPEAKING), (S.SPEAKING, S.IDLE)]
    assert [e.sequence_number for e in events] == [1, 2, 3, 4]
    assert [e.reason_code for e in events] == [r for _, r in ANSWER_PATH]
    assert all(isinstance(e.timestamp_monotonic, float) for e in events)


@pytest.mark.parametrize("reason", [StateReason.NO_SPEECH_DETECTED, StateReason.CAPTURE_CANCELLED])
def test_listening_can_end_without_a_request(reason):
    mgr = AssistantStateManager()
    mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)
    e = mgr.transition(S.IDLE, reason)
    assert e.previous_state is S.LISTENING and e.state is S.IDLE and e.reason_code is reason and mgr.current_state is S.IDLE


def test_checking_can_end_without_speaking():
    mgr = AssistantStateManager()
    walk(mgr, (S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED), (S.CHECKING, StateReason.UTTERANCE_CAPTURED))
    e = mgr.transition(S.IDLE, StateReason.NO_OUTPUT)
    assert (e.previous_state, e.state) == (S.CHECKING, S.IDLE) and mgr.current_state is S.IDLE


def test_an_announcement_never_passes_through_listening():
    mgr, rec = AssistantStateManager(), Recorder()
    mgr.subscribe(rec)
    walk(mgr, (S.CHECKING, StateReason.OUTPUT_ADMITTED), (S.SPEAKING, StateReason.PLAYBACK_STARTED), (S.IDLE, StateReason.PLAYBACK_COMPLETED))
    assert [e.state for e in rec.events] == [S.CHECKING, S.SPEAKING, S.IDLE] and S.LISTENING not in {e.state for e in rec.events}
    with pytest.raises(InvalidStateTransition):                      # and there is no IDLE -> SPEAKING shortcut
        mgr.transition(S.SPEAKING, StateReason.PLAYBACK_STARTED)


def test_a_fallback_during_an_interaction_and_a_standalone_fallback():
    during, rec = AssistantStateManager(), Recorder()
    during.subscribe(rec)
    walk(during, (S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED), (S.CHECKING, StateReason.UTTERANCE_CAPTURED),
         (S.SPEAKING, StateReason.PLAYBACK_STARTED), (S.IDLE, StateReason.PLAYBACK_COMPLETED))
    assert [e.state for e in rec.events] == [S.LISTENING, S.CHECKING, S.SPEAKING, S.IDLE]
    alone, rec2 = AssistantStateManager(), Recorder()
    alone.subscribe(rec2)
    walk(alone, (S.CHECKING, StateReason.OUTPUT_ADMITTED), (S.SPEAKING, StateReason.PLAYBACK_STARTED), (S.IDLE, StateReason.PLAYBACK_COMPLETED))
    assert [e.state for e in rec2.events] == [S.CHECKING, S.SPEAKING, S.IDLE]            # LISTENING is not invented


def test_the_same_state_is_an_idempotent_no_op():
    mgr, rec = AssistantStateManager(), Recorder()
    mgr.subscribe(rec)
    assert mgr.transition(S.IDLE, StateReason.NO_OUTPUT) is None
    first = mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)
    assert mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED) is None
    assert mgr.transition(S.LISTENING, StateReason.CAPTURE_CANCELLED, request_id="other-1") is None
    assert rec.events == [first] and mgr.current_event is first and mgr.current_event.sequence_number == 1
    assert mgr.transition(S.CHECKING, StateReason.UTTERANCE_CAPTURED).sequence_number == 2          # the no-ops did not consume sequence numbers


@pytest.mark.parametrize("source,target", [(a, b) for a in S for b in S if a is not b])
def test_every_transition_is_allowed_or_rejected_exactly_as_the_table_says(source, target):
    path = {S.IDLE: [], S.LISTENING: [(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)],
            S.CHECKING: [(S.CHECKING, StateReason.OUTPUT_ADMITTED)],
            S.SPEAKING: [(S.CHECKING, StateReason.OUTPUT_ADMITTED), (S.SPEAKING, StateReason.PLAYBACK_STARTED)]}[source]
    mgr = AssistantStateManager()
    walk(mgr, *path)
    before = mgr.current_event
    if target in ALLOWED_TRANSITIONS[source]:
        assert mgr.transition(target, StateReason.NO_OUTPUT).state is target
    else:
        with pytest.raises(InvalidStateTransition):
            mgr.transition(target, StateReason.NO_OUTPUT)
        assert mgr.current_event is before                           # a refused transition changes nothing and consumes no sequence number


def test_the_documented_shortcuts_and_refusals():
    ok = {(S.IDLE, S.LISTENING), (S.IDLE, S.CHECKING), (S.LISTENING, S.CHECKING), (S.LISTENING, S.IDLE), (S.CHECKING, S.SPEAKING), (S.CHECKING, S.IDLE), (S.SPEAKING, S.IDLE)}
    assert {(a, b) for a in S for b in ALLOWED_TRANSITIONS[a]} == ok
    for bad in [(S.IDLE, S.SPEAKING), (S.LISTENING, S.SPEAKING), (S.SPEAKING, S.LISTENING), (S.SPEAKING, S.CHECKING), (S.CHECKING, S.LISTENING)]:
        assert bad not in ok


@pytest.mark.parametrize("state,reason,request_id", [
    ("LISTENING", StateReason.NO_OUTPUT, None), (None, StateReason.NO_OUTPUT, None), (1, StateReason.NO_OUTPUT, None),
    (S.LISTENING, "SPEECH_INPUT_ACTIVATED", None), (S.LISTENING, SECRET, None), (S.LISTENING, None, None),
    (S.LISTENING, StateReason.NO_OUTPUT, SECRET), (S.LISTENING, StateReason.NO_OUTPUT, ""), (S.LISTENING, StateReason.NO_OUTPUT, 5),
])
def test_bad_arguments_are_refused_and_change_nothing(state, reason, request_id):
    mgr = AssistantStateManager()
    before = mgr.current_event
    with pytest.raises(StateRequestError):
        mgr.transition(state, reason, request_id=request_id)
    assert mgr.current_event is before


def test_the_current_state_can_be_queried_at_any_time_and_a_late_client_can_be_replayed():
    mgr = AssistantStateManager()
    walk(mgr, (S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED), (S.CHECKING, StateReason.UTTERANCE_CAPTURED))
    assert (mgr.current_state, mgr.current_color) == (S.CHECKING, StateColor.BLUE)
    late, replayed = Recorder(), Recorder()
    mgr.subscribe(late)
    mgr.subscribe(replayed, replay_current=True)
    assert late.events == [] and [e.state for e in replayed.events] == [S.CHECKING] and replayed.events[0] is mgr.current_event
    mgr.transition(S.SPEAKING, StateReason.PLAYBACK_STARTED)
    assert [e.state for e in late.events] == [S.SPEAKING] and [e.state for e in replayed.events] == [S.CHECKING, S.SPEAKING]


def test_sequence_numbers_increase_by_exactly_one_and_timestamps_never_go_back():
    mgr = AssistantStateManager()
    events = [mgr.current_event]
    for _ in range(5):
        events += walk(mgr, *ANSWER_PATH)
    assert [e.sequence_number for e in events] == list(range(len(events)))
    assert all(b.timestamp_monotonic >= a.timestamp_monotonic for a, b in zip(events, events[1:]))
    assert all(b.previous_state is a.state for a, b in zip(events, events[1:]))


def test_a_clock_that_goes_backwards_is_refused_and_changes_nothing():
    ticks = iter([10.0, 11.0, 9.0])
    mgr = AssistantStateManager(clock=lambda: next(ticks))
    mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)
    before = mgr.current_event
    with pytest.raises(StateError):
        mgr.transition(S.CHECKING, StateReason.UTTERANCE_CAPTURED)
    assert mgr.current_event is before


def test_the_timestamps_come_from_the_injected_monotonic_clock_not_wall_time():
    ticks = itertools.count(100)
    mgr = AssistantStateManager(clock=lambda: float(next(ticks)))
    assert mgr.current_event.timestamp_monotonic == 100.0
    assert mgr.transition(S.CHECKING, StateReason.OUTPUT_ADMITTED).timestamp_monotonic == 101.0


def test_the_default_clock_is_the_monotonic_clock_never_wall_time():
    import inspect
    assert inspect.signature(AssistantStateManager).parameters["clock"].default is time.monotonic


def test_events_are_immutable_and_the_manager_exposes_no_mutable_state():
    mgr = AssistantStateManager()
    e = mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)
    for field in ("state", "sequence_number", "color_semantic", "reason_code", "request_id"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(e, field, None)
    with pytest.raises(AttributeError):
        mgr.current_state = S.SPEAKING
    with pytest.raises(AttributeError):
        mgr.current_event = e
    assert mgr.current_state is S.LISTENING


def test_the_event_has_exactly_the_contract_fields():
    assert [f.name for f in dataclasses.fields(StateEvent)] == ["state", "previous_state", "color_semantic", "timestamp_monotonic", "reason_code",
                                                               "sequence_number", "request_id"]
    e = AssistantStateManager().transition(S.CHECKING, StateReason.OUTPUT_ADMITTED, request_id="req-1")
    assert e.request_id == "req-1"


# ---- subscribers -----------------------------------------------------------------------------------------------------------

def test_a_subscriber_receives_the_very_event_the_transition_returned_and_unsubscribing_stops_delivery():
    mgr, rec = AssistantStateManager(), Recorder()
    off = mgr.subscribe(rec)
    e = mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)
    assert rec.events == [e] and rec.events[0] is e
    off()
    off()                                                            # idempotent
    mgr.transition(S.CHECKING, StateReason.UTTERANCE_CAPTURED)
    assert rec.events == [e]
    with pytest.raises(StateRequestError):
        mgr.subscribe("not callable")


def test_two_subscribers_receive_the_same_ordered_events():
    mgr, a, b = AssistantStateManager(), Recorder(), Recorder()
    mgr.subscribe(a)
    mgr.subscribe(b)
    walk(mgr, *ANSWER_PATH)
    assert a.events == b.events and [e.sequence_number for e in a.events] == [1, 2, 3, 4]


def test_a_subscriber_always_sees_a_state_that_matches_the_event_it_is_handling():
    mgr, seen = AssistantStateManager(), []
    mgr.subscribe(lambda e: seen.append((mgr.current_state is e.state, mgr.current_event is e, mgr.current_color is e.color_semantic)))
    walk(mgr, *ANSWER_PATH)
    assert seen == [(True, True, True)] * 4                          # the state is committed before anyone is told


def test_a_broken_subscriber_cannot_corrupt_the_state_or_silence_the_others(logs):
    mgr, before, after = AssistantStateManager(), Recorder(), Recorder()

    def broken(event):
        raise RuntimeError(f"cannot show {SECRET}")

    mgr.subscribe(before)
    mgr.subscribe(broken)
    mgr.subscribe(after)
    e = mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)          # does not raise
    assert e is not None and mgr.current_event is e and mgr.current_state is S.LISTENING
    assert before.events == [e] and after.events == [e] and mgr.listener_failure_count == 1
    walk(mgr, *ANSWER_PATH[1:])
    assert [x.sequence_number for x in after.events] == [1, 2, 3, 4] and mgr.listener_failure_count == 4
    joined = "\n".join(logs)
    assert "event=state_listener_error" in joined and "RuntimeError" in joined and "secret" not in joined.lower()     # class name only


def test_a_subscriber_cannot_call_transition_from_inside_its_callback():
    mgr, errors = AssistantStateManager(), []

    def greedy(event):
        try:
            mgr.transition(S.CHECKING, StateReason.UTTERANCE_CAPTURED)
        except StateReentrancyError as exc:
            errors.append(exc)
            raise

    mgr.subscribe(greedy)
    e = mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)
    assert len(errors) == 1 and mgr.current_event is e and mgr.listener_failure_count == 1
    assert mgr.transition(S.CHECKING, StateReason.UTTERANCE_CAPTURED).sequence_number == 2          # the manager still works


# ---- privacy and scope -------------------------------------------------------------------------------------------------------

def test_state_logs_carry_no_text_only_structure(logs):
    mgr = AssistantStateManager()
    walk(mgr, *ANSWER_PATH)
    mgr.transition(S.CHECKING, StateReason.OUTPUT_ADMITTED, request_id="ann-7")
    joined = "\n".join(logs)
    assert joined.count("event=assistant_state") == 5
    first = logs[0]
    for field in ("state=LISTENING", "previous=IDLE", "color=RED", "sequence=1", "reason=SPEECH_INPUT_ACTIVATED", "request_id=-", "t_monotonic="):
        assert field in first
    assert "request_id=ann-7" in logs[-1]


def test_reasons_are_a_closed_set_of_codes_and_no_text_can_be_an_event_field():
    assert all(r.value == r.name and r.name.isupper() and " " not in r.value for r in StateReason)
    mgr = AssistantStateManager()
    for text in (SECRET, "The temperature is 24 degrees.", "I'm sorry, I can't complete that request right now."):
        with pytest.raises(StateRequestError):
            mgr.transition(S.CHECKING, text)
        with pytest.raises(StateRequestError):
            mgr.transition(S.CHECKING, StateReason.OUTPUT_ADMITTED, request_id=text)
    e = mgr.transition(S.CHECKING, StateReason.OUTPUT_ADMITTED)
    assert SECRET not in repr(e) and "secret" not in repr(mgr.current_event).lower()


def test_the_state_module_has_no_hardware_network_audio_or_stt_dependency():
    tree = ast.parse((REPO_ROOT / "assistant" / "state.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"math", "re", "threading", "time", "dataclasses", "enum", "types", "typing", "config"}
    source = (REPO_ROOT / "assistant" / "state.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("RPi", "gpiozero", "serial", "socket", "bluetooth", "websocket", "paho", "http", "urllib", "sounddevice", "sherpa", "whisper"):
        assert f"import {forbidden}" not in code and f"from {forbidden}" not in code


def test_importing_the_state_module_loads_no_hardware_audio_or_network_library():
    code = (
        "import sys\n"
        "import assistant.state\n"
        "bad = [m for m in ('RPi', 'gpiozero', 'serial', 'bluetooth', 'sounddevice', 'sherpa_onnx', 'faster_whisper', 'paho', 'websockets', 'requests') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr


# ---- concurrency (barriers and invariants, not sleeps that decide outcomes) -------------------------------------------------------

def _run_threads(n, target):
    barrier = threading.Barrier(n)
    results = [None] * n

    def wrapped(i):
        barrier.wait(WAIT)
        try:
            results[i] = target(i)
        except BaseException as exc:                                 # recorded, asserted on by the caller
            results[i] = exc

    threads = [threading.Thread(target=wrapped, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)
        assert not t.is_alive(), "worker did not finish"
    return results


def _assert_consistent_history(mgr, events):
    """The recorded stream is exactly one valid serial history."""
    assert [e.sequence_number for e in events] == list(range(1, len(events) + 1))          # no duplicate, no gap, in order
    previous = S.IDLE
    for e in events:
        assert e.previous_state is previous and e.state in ALLOWED_TRANSITIONS[previous] and e.color_semantic is COLOR_OF[e.state]
        previous = e.state
    assert all(b.timestamp_monotonic >= a.timestamp_monotonic for a, b in zip(events, events[1:]))
    assert mgr.current_state is previous and (mgr.current_event is events[-1] if events else True)


def test_simultaneous_identical_transitions_produce_exactly_one_event():
    for _ in range(25):
        mgr, rec = AssistantStateManager(), Recorder()
        mgr.subscribe(rec)
        results = _run_threads(16, lambda i: mgr.transition(S.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED, request_id=f"r{i}"))
        winners = [r for r in results if isinstance(r, StateEvent)]
        assert len(winners) == 1 and sum(r is None for r in results) == 15 and winners[0].sequence_number == 1
        assert rec.events == winners and mgr.current_state is S.LISTENING


def test_competing_different_transitions_leave_one_consistent_history():
    for _ in range(25):
        mgr, rec = AssistantStateManager(), Recorder()
        mgr.subscribe(rec)
        targets = [S.LISTENING, S.CHECKING] * 6
        results = _run_threads(12, lambda i: mgr.transition(targets[i], StateReason.OUTPUT_ADMITTED))
        assert not [r for r in results if isinstance(r, BaseException) and not isinstance(r, InvalidStateTransition)]
        _assert_consistent_history(mgr, rec.events)
        assert 1 <= len(rec.events) <= 2                              # IDLE -> X, and at most LISTENING -> CHECKING after it


@pytest.mark.parametrize("seed", range(6))
def test_many_threads_walking_the_machine_never_duplicate_or_reorder_events(seed):
    def slow_clock():                                                # called inside the critical section: widens the race window
        time.sleep(0.0001)
        return time.monotonic()

    mgr = AssistantStateManager(clock=slow_clock)
    a, b = Recorder(), Recorder()
    mgr.subscribe(a)
    mgr.subscribe(b)
    reasons = list(StateReason)[1:]

    def worker(i):
        rng = random.Random(seed * 100 + i)
        for _ in range(60):
            try:
                mgr.transition(rng.choice(list(S)), rng.choice(reasons), request_id=f"w{i}")
            except InvalidStateTransition:
                pass

    results = _run_threads(8, lambda i: worker(i))
    assert all(r is None for r in results), results
    assert a.events == b.events and len(a.events) > 20
    _assert_consistent_history(mgr, a.events)


def test_subscribing_and_unsubscribing_while_transitions_run_is_safe():
    mgr = AssistantStateManager()
    stop = threading.Event()
    seen = []

    def churn():
        while not stop.is_set():
            rec = Recorder()
            off = mgr.subscribe(rec, replay_current=True)
            off()
            seen.append(len(rec.events))

    t = threading.Thread(target=churn, daemon=True)
    t.start()
    for _ in range(40):
        walk(mgr, *ANSWER_PATH)
    stop.set()
    t.join(WAIT)
    assert not t.is_alive() and mgr.current_state is S.IDLE and mgr.current_event.sequence_number == 160 and all(n >= 1 for n in seen)
