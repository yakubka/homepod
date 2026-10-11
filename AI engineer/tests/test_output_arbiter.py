"""Team Leader AI-7: spoken-output arbitration (assistant/output_arbiter.py), policy AI7_MIDTERM_V1.

SOFTWARE-VALIDATED with fake TTS and fake playback; nothing is played. "No overlap" here means the arbiter never lets two spoken
outputs run at once and never calls the playback engine twice at once. It says nothing about the project speaker, real playback
cancellation, audibility or classroom use, so Team Leader AI-7 stays PARTIAL / PENDING REAL HARDWARE TEST.

Concurrency tests coordinate with events and barriers: a winner is held inside its synthesis or playback until every contender has
returned, so overlap is provoked deterministically instead of being left to timing luck.
"""

import ast
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from assistant import output_arbiter as oa
from assistant.output_arbiter import (
    ArbiterRequestError, ArbiterStateError, ArbitrationRequest, ArbitrationResult, CLASS_OF, Decision, OutputArbiter, OutputPhase,
    POLICY_VERSION, RequestClass, SpeechSubmission, decide, outranks,
)
from assistant.spoken_output import (
    AnnouncementRequest, AnnouncementSource, FALLBACK_TEXT, FallbackReason, OutputStatus, RequestType,
)
from audio.playback import PlaybackEngine, PlaybackFailed, PlaybackResult, PlaybackStatus
from speech.tts import TtsRuntimeUnavailable
from tests.spoken_output_fakes import FakeTts, speech_audio

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET = "my secret exam answer is forty two"
WAIT = 5


# ---- fakes with counters ---------------------------------------------------------------------------------------------------------------------------

class Probe:
    """Shared concurrency counters."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = {"tts": 0, "play": 0}
        self.max = {"tts": 0, "play": 0}

    def enter(self, kind):
        with self.lock:
            self.active[kind] += 1
            self.max[kind] = max(self.max[kind], self.active[kind])

    def leave(self, kind):
        with self.lock:
            self.active[kind] -= 1


class ProbeTts(FakeTts):
    def __init__(self, probe, gate=None, bug=None):
        super().__init__()
        self.probe, self.gate, self.bug = probe, gate, bug
        self.entered, self.finished = threading.Event(), threading.Event()
        self.texts = []

    def synthesize(self, text):
        self.probe.enter("tts")
        try:
            self.texts.append(text)
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(WAIT), "tts gate never released"
            if self.bug:
                raise self.bug
            audio = super().synthesize(text)
            self.finished.set()
            return audio
        finally:
            self.probe.leave("tts")


class ProbePlayback(PlaybackEngine):
    """Counts calls; can hold the 'speaker' until released or stopped; can delay registering as playing."""

    def __init__(self, probe, hold=False, register_gate=None, error=None, bug=None):
        self.probe, self.hold, self.register_gate, self.error, self.bug = probe, hold, register_gate, error, bug
        self.calls = 0
        self.stop_calls = 0
        self.called, self.registered, self.release = threading.Event(), threading.Event(), threading.Event()
        self._playing = False
        self.stopped = False

    def play(self, audio):
        self.probe.enter("play")
        try:
            self.calls += 1
            self.called.set()
            if self.register_gate is not None:
                assert self.register_gate.wait(WAIT), "register gate never released"
            self._playing = True
            self.registered.set()
            if self.bug:
                raise self.bug
            if self.error:
                raise self.error
            if self.hold:
                assert self.release.wait(WAIT), "playback never released"
            status = PlaybackStatus.STOPPED if self.stopped else PlaybackStatus.COMPLETED
            return PlaybackResult(status, audio.duration_s, 10.0, 10.01, 10.02, 10.5, "fake-device", "fake-api", 0.01, 0)
        finally:
            self._playing = False
            self.probe.leave("play")

    def stop(self):
        self.stop_calls += 1
        self.stopped = True
        self.release.set()
        return True

    @property
    def is_playing(self):
        return self._playing


def build(gate=None, hold=False, register_gate=None, tts_bug=None, play_error=None, play_bug=None, clock=None):
    probe = Probe()
    tts = ProbeTts(probe, gate=gate, bug=tts_bug)
    playback = ProbePlayback(probe, hold=hold, register_gate=register_gate, error=play_error, bug=play_bug)
    arbiter = OutputArbiter(tts, playback, clock=clock) if clock else OutputArbiter(tts, playback)
    return arbiter, tts, playback, probe


def ann(i=1, text="Lunch is served in the main hall.", **kw):
    base = dict(announcement_id=f"ann-{i}", text=text, source=AnnouncementSource.APP)
    base.update(kw)
    return AnnouncementRequest(**base)


def in_thread(fn):
    box = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    return t, box


def join(t):
    t.join(WAIT)
    assert not t.is_alive(), "worker thread did not finish"


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    oa.logger.addHandler(handler)
    yield handler.lines
    oa.logger.removeHandler(handler)


# ---- the scenario matrix (A-N) ------------------------------------------------------------------------------------------------------------------------

def sc_a():
    arb, tts, pb, _ = build()
    o = arb.submit_answer("There are 17 people in the room.", request_id="a1")
    return "GRANTED, COMPLETED", f"{o.arbitration.decision.value}, {o.output.status.value}", o.granted and o.output.status is OutputStatus.COMPLETED and len(tts.texts) == 1


def sc_b():
    arb, tts, pb, _ = build()
    o = arb.submit_announcement(ann())
    return "GRANTED, COMPLETED", f"{o.arbitration.decision.value}, {o.output.status.value}", o.granted and o.output.status is OutputStatus.COMPLETED and pb.calls == 1


def sc_c():
    arb, tts, pb, _ = build()
    o = arb.submit_fallback(FallbackReason.UPSTREAM_TIMEOUT)
    return "GRANTED, COMPLETED", f"{o.arbitration.decision.value}, {o.output.status.value}", o.granted and o.output.status is OutputStatus.COMPLETED and tts.texts == [FALLBACK_TEXT]


def _batch_winner(subs, expected_decisions, winner_text):
    arb, tts, pb, probe = build()
    outs = arb.submit_batch(subs)
    got = [o.arbitration.decision for o in outs]
    ok = got == expected_decisions and tts.texts == [winner_text] and pb.calls == 1 and probe.max["play"] == 1 and all(o.output is None for o in outs if not o.granted)
    return " / ".join(d.value for d in expected_decisions), " / ".join(d.value for d in got), ok


def sc_d():
    return _batch_winner([SpeechSubmission.announce(ann()), SpeechSubmission.answer("There are 17 people in the room.", "a1")],
                         [Decision.REJECTED_LOWER_PRIORITY, Decision.GRANTED], "There are 17 people in the room.")


def sc_e():
    return _batch_winner([SpeechSubmission.announce(ann()), SpeechSubmission.fallback(FallbackReason.NETWORK_UNAVAILABLE)],
                         [Decision.REJECTED_LOWER_PRIORITY, Decision.GRANTED], FALLBACK_TEXT)


def _active_then(first_kind, phase, second):
    """Hold an output in `phase`, submit `second` from this thread, release, and report what happened."""
    if phase == "SYNTHESIZING":
        gate = threading.Event()
        arb, tts, pb, probe = build(gate=gate)
        hold_started = lambda: tts.entered.wait(WAIT)
    else:
        arb, tts, pb, probe = build(hold=True)
        hold_started = lambda: pb.registered.wait(WAIT)
    first = SpeechSubmission.announce(ann(1, "First message text.")) if first_kind == "ANNOUNCEMENT" else (
        SpeechSubmission.answer("First answer text.", "first-1") if first_kind == "ANSWER" else SpeechSubmission.fallback(FallbackReason.UPSTREAM_TIMEOUT))
    t, box = in_thread(lambda: arb.submit_batch([first])[0])
    assert hold_started()
    assert arb.phase is (OutputPhase.SYNTHESIZING if phase == "SYNTHESIZING" else OutputPhase.PLAYING)
    calls_before = (len(tts.texts), pb.calls)
    outcome = arb.submit_batch([second])[0]
    no_extra_work = (len(tts.texts), pb.calls) == calls_before
    (gate.set() if phase == "SYNTHESIZING" else pb.release.set())
    join(t)
    return outcome, no_extra_work, box[0], probe, arb


def sc_f():
    outcome, quiet, first, probe, _ = _active_then("ANNOUNCEMENT", "PLAYING", SpeechSubmission.answer("Urgent answer.", "a2"))
    ok = outcome.arbitration.decision is Decision.RETRY_REQUIRED and outcome.output is None and quiet and first.output.status is OutputStatus.COMPLETED and probe.max["play"] == 1
    return "RETRY_REQUIRED, no overlap", f"{outcome.arbitration.decision.value}, max concurrent playback {probe.max['play']}", ok


def sc_g():
    outcome, quiet, first, probe, _ = _active_then("ANSWER", "PLAYING", SpeechSubmission.announce(ann(2)))
    ok = outcome.arbitration.decision is Decision.REJECTED_LOWER_PRIORITY and outcome.output is None and quiet and probe.max["play"] == 1
    return "REJECTED_LOWER_PRIORITY, no overlap", f"{outcome.arbitration.decision.value}, max concurrent playback {probe.max['play']}", ok


def sc_h():
    return _batch_winner([SpeechSubmission.announce(ann(1, "First announcement.")), SpeechSubmission.announce(ann(2, "Second announcement."))],
                         [Decision.GRANTED, Decision.REJECTED_ACTIVE_OUTPUT], "First announcement.")


def sc_i():
    a = _batch_winner([SpeechSubmission.answer("First answer.", "a1"), SpeechSubmission.answer("Second answer.", "a2")],
                      [Decision.GRANTED, Decision.REJECTED_ACTIVE_OUTPUT], "First answer.")
    b = _batch_winner([SpeechSubmission.answer("Second answer.", "a2"), SpeechSubmission.answer("First answer.", "a1")],
                      [Decision.GRANTED, Decision.REJECTED_ACTIVE_OUTPUT], "Second answer.")
    return "first request wins (both orders)", a[1] + " | " + b[1], a[2] and b[2]


def sc_j():
    gate = threading.Event()
    arb, tts, pb, _ = build(gate=gate)
    t, box = in_thread(lambda: arb.submit_announcement(ann()))
    assert tts.entered.wait(WAIT)
    stopped = arb.stop()
    gate.set()
    join(t)
    out = box[0].output
    return "STOPPED, playback never called", f"{out.status.value}, playback calls {pb.calls}", stopped and out.status is OutputStatus.STOPPED and pb.calls == 0 and arb.phase is OutputPhase.IDLE


def sc_k():
    probe = Probe()
    tts = ProbeTts(probe)
    pb = ProbePlayback(probe)

    class HookClock:
        reached, proceed, armed = threading.Event(), threading.Event(), True

        def __call__(self):
            if self.armed and tts.finished.is_set():          # synthesis has returned; playback has not been requested yet
                self.armed = False
                self.reached.set()
                assert self.proceed.wait(WAIT)
            return time.perf_counter()

    hook = HookClock()
    arb = OutputArbiter(tts, pb, clock=hook)
    t, box = in_thread(lambda: arb.submit_announcement(ann()))
    assert hook.reached.wait(WAIT)
    stopped = arb.stop()
    hook.proceed.set()
    join(t)
    out = box[0].output
    return "STOPPED, playback never called", f"{out.status.value}, playback calls {pb.calls}", stopped and out.status is OutputStatus.STOPPED and pb.calls == 0


def sc_l():
    arb, tts, pb, _ = build(hold=True)
    t, box = in_thread(lambda: arb.submit_answer("A long answer.", request_id="a1"))
    assert pb.registered.wait(WAIT)
    stopped = arb.stop()
    join(t)
    out = box[0].output
    return "playback.stop called once, STOPPED", f"stop calls {pb.stop_calls}, {out.status.value}", stopped and pb.stop_calls == 1 and out.status is OutputStatus.STOPPED and out.playback_status is PlaybackStatus.STOPPED


def sc_m():
    arb, tts, pb, _ = build()
    first = arb.stop()
    second = arb.stop()
    o = arb.submit_answer("Fine.", request_id="a1")
    return "False, False, next request COMPLETED", f"{first}, {second}, {o.output.status.value}", first is False and second is False and o.output.status is OutputStatus.COMPLETED


def sc_n():
    arb, tts, pb, _ = build()
    one = arb.submit_answer("First.", request_id="a1")
    two = arb.submit_announcement(ann())
    three = arb.submit_fallback(FallbackReason.INTERNAL_SERVICE_FAILURE)
    ok = all(o.granted and o.output.status is OutputStatus.COMPLETED for o in (one, two, three)) and arb.phase is OutputPhase.IDLE
    return "three sequential requests all GRANTED", ", ".join(o.arbitration.decision.value for o in (one, two, three)), ok


SCENARIOS = [("A", "answer alone", sc_a), ("B", "announcement alone", sc_b), ("C", "fallback alone", sc_c),
             ("D", "answer vs announcement contend before start", sc_d), ("E", "fallback vs announcement contend before start", sc_e),
             ("F", "announcement active, answer arrives", sc_f), ("G", "answer active, announcement arrives", sc_g),
             ("H", "two announcements", sc_h), ("I", "two answers", sc_i), ("J", "stop during synthesis", sc_j),
             ("K", "stop after synthesis, before playback", sc_k), ("L", "stop during playback", sc_l), ("M", "stop while idle", sc_m),
             ("N", "request after previous completion", sc_n)]


@pytest.mark.parametrize("sid,name,fn", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario_matrix(sid, name, fn):
    expected, actual, ok = fn()
    assert ok, f"{sid} {name}: expected {expected}, got {actual}"


@pytest.mark.parametrize("phase", ["SYNTHESIZING", "PLAYING"])
def test_an_active_announcement_is_never_interrupted_in_either_phase_and_the_conversation_must_retry(phase):
    for second in (SpeechSubmission.answer("Urgent.", "a2"), SpeechSubmission.fallback(FallbackReason.NETWORK_UNAVAILABLE)):
        outcome, quiet, first, probe, arb = _active_then("ANNOUNCEMENT", phase, second)
        assert outcome.arbitration.decision is Decision.RETRY_REQUIRED and outcome.arbitration.reason_code == oa.R_ACTIVE_ANNOUNCEMENT
        assert outcome.arbitration.active_request_type is RequestType.ANNOUNCEMENT and outcome.output is None and quiet
        assert first.output.status is OutputStatus.COMPLETED and probe.max == {"tts": 1, "play": 1}
        assert arb.submit_batch([second])[0].granted                     # once the announcement is done, the retry is granted


@pytest.mark.parametrize("phase", ["SYNTHESIZING", "PLAYING"])
@pytest.mark.parametrize("first_kind", ["ANSWER", "FALLBACK"])
def test_an_active_conversation_rejects_announcements_and_equals(phase, first_kind):
    outcome, quiet, first, probe, _ = _active_then(first_kind, phase, SpeechSubmission.announce(ann(2)))
    assert outcome.arbitration.decision is Decision.REJECTED_LOWER_PRIORITY and outcome.output is None and quiet
    for second, expected in ((SpeechSubmission.answer("Another answer.", "a9"), Decision.REJECTED_ACTIVE_OUTPUT),
                             (SpeechSubmission.fallback(FallbackReason.UPSTREAM_TIMEOUT), Decision.REJECTED_ACTIVE_OUTPUT)):
        o2, quiet2, _first, probe2, _ = _active_then(first_kind, phase, second)
        assert o2.arbitration.decision is expected and o2.arbitration.reason_code == oa.R_ACTIVE_SAME_CLASS and quiet2 and probe2.max["play"] == 1


def test_an_active_announcement_rejects_a_second_announcement():
    outcome, quiet, first, probe, _ = _active_then("ANNOUNCEMENT", "PLAYING", SpeechSubmission.announce(ann(2)))
    assert outcome.arbitration.decision is Decision.REJECTED_ACTIVE_OUTPUT and quiet and probe.max["play"] == 1


def test_fallback_has_no_special_priority_over_an_answer():
    arb, tts, _, _ = build()
    [first, second] = arb.submit_batch([SpeechSubmission.answer("Answer text.", "a1"), SpeechSubmission.fallback(FallbackReason.UPSTREAM_TIMEOUT)])
    assert first.granted and second.arbitration.decision is Decision.REJECTED_ACTIVE_OUTPUT and tts.texts == ["Answer text."]
    arb2, tts2, _, _ = build()
    [first, second] = arb2.submit_batch([SpeechSubmission.fallback(FallbackReason.UPSTREAM_TIMEOUT), SpeechSubmission.answer("Answer text.", "a1")])
    assert first.granted and not second.granted and tts2.texts == [FALLBACK_TEXT]


def test_a_batch_of_three_picks_the_conversation_request_that_arrived_first():
    arb, tts, _, _ = build()
    outs = arb.submit_batch([SpeechSubmission.announce(ann()), SpeechSubmission.answer("Second in line.", "a2"), SpeechSubmission.answer("Third in line.", "a3")])
    assert [o.arbitration.decision for o in outs] == [Decision.REJECTED_LOWER_PRIORITY, Decision.GRANTED, Decision.REJECTED_ACTIVE_OUTPUT]
    assert [o.arbitration.reason_code for o in outs] == [oa.R_LOST_CLASS, oa.R_WON_BY_CLASS, oa.R_LOST_LATER] and tts.texts == ["Second in line."]


# ---- the pure policy --------------------------------------------------------------------------------------------------------------------------------------

def req(rid, rtype, seq):
    return ArbitrationRequest(rid, rtype, CLASS_OF[rtype], seq)


def test_policy_constants_and_class_mapping():
    assert POLICY_VERSION == "AI7_MIDTERM_V1"
    assert CLASS_OF == {RequestType.ANSWER: RequestClass.CONVERSATION, RequestType.FALLBACK: RequestClass.CONVERSATION,
                        RequestType.ANNOUNCEMENT: RequestClass.ANNOUNCEMENT}
    assert outranks(RequestClass.CONVERSATION, RequestClass.ANNOUNCEMENT)
    for a, b in ((RequestClass.ANNOUNCEMENT, RequestClass.CONVERSATION), (RequestClass.CONVERSATION, RequestClass.CONVERSATION), (RequestClass.ANNOUNCEMENT, RequestClass.ANNOUNCEMENT)):
        assert not outranks(a, b)
    assert {d.name for d in Decision} == {"GRANTED", "REJECTED_ACTIVE_OUTPUT", "REJECTED_LOWER_PRIORITY", "RETRY_REQUIRED", "INVALID_REQUEST"}


@pytest.mark.parametrize("active", [RequestType.ANSWER, RequestType.FALLBACK, RequestType.ANNOUNCEMENT])
@pytest.mark.parametrize("phase", [OutputPhase.SYNTHESIZING, OutputPhase.PLAYING])
@pytest.mark.parametrize("incoming", [RequestType.ANSWER, RequestType.FALLBACK, RequestType.ANNOUNCEMENT])
def test_nothing_is_ever_granted_while_an_output_is_active(active, phase, incoming):
    [r] = decide([req("x1", incoming, 5)], phase, active)
    assert r.decision is not Decision.GRANTED and r.policy_version == POLICY_VERSION and r.active_request_type is active and r.active_phase is phase
    if CLASS_OF[incoming] is RequestClass.CONVERSATION and CLASS_OF[active] is RequestClass.ANNOUNCEMENT:
        assert r.decision is Decision.RETRY_REQUIRED
    elif CLASS_OF[incoming] is RequestClass.ANNOUNCEMENT and CLASS_OF[active] is RequestClass.CONVERSATION:
        assert r.decision is Decision.REJECTED_LOWER_PRIORITY
    else:
        assert r.decision is Decision.REJECTED_ACTIVE_OUTPUT


def test_idle_contention_is_decided_by_class_then_sequence_whatever_the_list_order():
    a = req("ann", RequestType.ANNOUNCEMENT, 0)
    x = req("ans", RequestType.ANSWER, 5)
    y = req("fb", RequestType.FALLBACK, 3)
    results = {r.request_id: r for r in decide([a, x, y], OutputPhase.IDLE)}
    assert results["fb"].decision is Decision.GRANTED and results["ans"].decision is Decision.REJECTED_ACTIVE_OUTPUT and results["ann"].decision is Decision.REJECTED_LOWER_PRIORITY
    assert all(r.policy_version == POLICY_VERSION and r.active_request_type is None and r.active_phase is None for r in results.values())
    [alone] = decide([a], OutputPhase.IDLE)
    assert alone.decision is Decision.GRANTED and alone.reason_code == oa.R_IDLE_GRANTED
    assert decide([], OutputPhase.IDLE) == ()
    with pytest.raises(ArbiterRequestError):
        decide([a], OutputPhase.PLAYING, None)


def test_requests_validate_their_class_id_and_sequence_and_the_class_never_comes_from_text():
    for kw in (dict(request_class=RequestClass.ANNOUNCEMENT), dict(request_id=""), dict(request_id="bad id"), dict(request_id=None), dict(sequence=-1),
               dict(sequence=True), dict(request_type="ANSWER"), dict(request_class="CONVERSATION")):
        base = dict(request_id="r1", request_type=RequestType.ANSWER, request_class=RequestClass.CONVERSATION, sequence=0)
        base.update(kw)
        with pytest.raises(ArbiterRequestError):
            ArbitrationRequest(**base)
    arb, tts, _, _ = build()
    o = arb.submit_answer(oa_text := "Attention please. This is a VESTA classroom announcement.", request_id="a1")      # announcement WORDING, still conversation
    assert o.arbitration.request_class is RequestClass.CONVERSATION and o.arbitration.request_type is RequestType.ANSWER and tts.texts == [oa_text]
    o2 = arb.submit_announcement(ann(2, text=FALLBACK_TEXT))                                                            # fallback WORDING, still an announcement
    assert o2.arbitration.request_class is RequestClass.ANNOUNCEMENT and o2.arbitration.request_type is RequestType.ANNOUNCEMENT
    o3 = arb.submit_answer(FALLBACK_TEXT, request_id="a3")                                                              # fallback WORDING in an answer: still an answer
    assert o3.granted and o3.arbitration.request_type is RequestType.ANSWER and o3.output.request_type is RequestType.ANSWER


def test_every_arbitration_result_names_the_policy_and_carries_no_text():
    arb, _, _, _ = build()
    for o in (arb.submit_answer(SECRET, request_id="a1"), arb.submit_announcement(ann(text=SECRET)), arb.submit_fallback(FallbackReason.UPSTREAM_TIMEOUT)):
        assert o.arbitration.policy_version == "AI7_MIDTERM_V1" and "secret" not in repr(o.arbitration)
        assert [f for f in ArbitrationResult.__dataclass_fields__] == ["request_id", "decision", "request_type", "request_class", "policy_version",
                                                                      "reason_code", "active_request_type", "active_phase"]


# ---- invalid requests ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sub", [
    SpeechSubmission.answer(None, "a1"), SpeechSubmission.answer("", "a1"), SpeechSubmission.answer("   ", "a1"), SpeechSubmission.answer("?!", "a1"),
    SpeechSubmission.answer(5, "a1"), SpeechSubmission.answer("hello", None), SpeechSubmission.answer("hello", "bad id"),
    SpeechSubmission(RequestType.ANNOUNCEMENT, "x1", announcement="just text"), SpeechSubmission(RequestType.ANNOUNCEMENT, "x1", announcement=None),
    SpeechSubmission.fallback("NETWORK_UNAVAILABLE"), SpeechSubmission.fallback(None), SpeechSubmission(RequestType.FALLBACK, "fb1", reason=7),
    SpeechSubmission("ANSWER", "a1", text="hello"), SpeechSubmission(None, "a1", text="hello"), SpeechSubmission([RequestType.ANSWER], "a1", text="hello"),
    "not a submission", None, 5,
])
def test_invalid_requests_are_reported_and_never_take_the_output(sub):
    arb, tts, pb, _ = build()
    [o] = arb.submit_batch([sub])
    assert o.arbitration.decision is Decision.INVALID_REQUEST and o.arbitration.reason_code == oa.R_INVALID and o.output is None
    assert o.arbitration.policy_version == POLICY_VERSION
    assert tts.texts == [] and pb.calls == 0 and arb.phase is OutputPhase.IDLE
    assert arb.submit_answer("A valid one.", request_id="ok1").granted


@pytest.mark.parametrize("bad_text", ["", "   ", "?!"])
def test_an_announcement_whose_text_is_unspeakable_is_invalid(bad_text):
    request = ann()
    object.__setattr__(request, "text", bad_text)                  # AnnouncementRequest validates on creation; bypass it to reach the arbiter's own check
    arb, tts, pb, _ = build()
    [o] = arb.submit_batch([SpeechSubmission.announce(request)])
    assert o.arbitration.decision is Decision.INVALID_REQUEST and o.output is None and tts.texts == [] and pb.calls == 0 and arb.phase is OutputPhase.IDLE


def test_an_invalid_request_in_a_batch_does_not_stop_the_valid_one():
    arb, tts, _, _ = build()
    outs = arb.submit_batch([SpeechSubmission.answer("", "a1"), SpeechSubmission.answer("Valid text.", "a2")])
    assert [o.arbitration.decision for o in outs] == [Decision.INVALID_REQUEST, Decision.GRANTED] and tts.texts == ["Valid text."]


# ---- stop and cancellation ------------------------------------------------------------------------------------------------------------------------------

def test_a_stop_during_synthesis_does_not_leak_into_the_next_request():
    gate = threading.Event()
    arb, tts, pb, _ = build(gate=gate)
    t, box = in_thread(lambda: arb.submit_answer("Cancelled one.", request_id="a1"))
    assert tts.entered.wait(WAIT) and arb.stop() is True
    gate.set()
    join(t)
    assert box[0].output.status is OutputStatus.STOPPED and pb.calls == 0 and arb.phase is OutputPhase.IDLE
    again = arb.submit_answer("Next one.", request_id="a2")
    assert again.output.status is OutputStatus.COMPLETED and pb.calls == 1               # the stale flag is gone


def test_a_stop_while_idle_changes_nothing_and_is_not_remembered():
    arb, tts, pb, _ = build()
    assert arb.stop() is False
    assert arb.submit_answer("Right after.", request_id="a1").output.status is OutputStatus.COMPLETED and pb.calls == 1


def test_a_stop_cannot_slip_through_the_gap_before_the_engine_registers_as_playing():
    register = threading.Event()
    arb, tts, pb, _ = build(hold=True, register_gate=register)
    t, box = in_thread(lambda: arb.submit_answer("Starting up.", request_id="a1"))
    assert pb.called.wait(WAIT) and pb.registered.is_set() is False                      # the engine has been called but is not yet "playing"
    s, stop_box = in_thread(lambda: arb.stop())
    time.sleep(0.05)                                                                      # stop() is now waiting for the engine to register
    assert pb.stop_calls == 0
    register.set()
    join(s)
    join(t)
    assert stop_box[0] is True and pb.stop_calls == 1 and box[0].output.status is OutputStatus.STOPPED


def test_a_stop_after_the_engine_has_returned_but_before_release_does_not_touch_the_engine_or_wait():
    arb, tts, pb, _ = build()
    reached, proceed = threading.Event(), threading.Event()

    class Hold(logging.Handler):                                   # the controller logs its result after playback returned, before the arbiter releases
        def emit(self, record):
            if "event=spoken_output_done" in record.getMessage():
                reached.set()
                assert proceed.wait(WAIT)

    handler = Hold()
    oa.logger.addHandler(handler)
    try:
        t, box = in_thread(lambda: arb.submit_answer("Finished already.", request_id="a1"))
        assert reached.wait(WAIT) and arb.phase is OutputPhase.PLAYING          # engine returned; arbiter not yet released
        started = time.monotonic()
        stopped = arb.stop()
        elapsed = time.monotonic() - started
        proceed.set()
        join(t)
    finally:
        oa.logger.removeHandler(handler)
    assert stopped is True and pb.stop_calls == 0 and elapsed < 0.5         # nothing left to stop, so no wait and no engine call
    assert box[0].output.status is OutputStatus.COMPLETED                   # it had already finished playing: not rewritten as STOPPED
    assert arb.phase is OutputPhase.IDLE


def test_a_stop_after_playback_has_ended_is_safe():
    arb, tts, pb, _ = build()
    arb.submit_answer("Done quickly.", request_id="a1")
    assert arb.stop() is False and pb.stop_calls == 0


def test_stopped_is_never_reported_as_completed():
    arb, tts, pb, _ = build(hold=True)
    t, box = in_thread(lambda: arb.submit_announcement(ann()))
    assert pb.registered.wait(WAIT)
    arb.stop()
    join(t)
    assert box[0].output.status is OutputStatus.STOPPED and box[0].output.playback_status is PlaybackStatus.STOPPED


def test_the_guard_refuses_a_playback_request_outside_a_granted_request():
    arb, _, pb, _ = build()
    with pytest.raises(ArbiterStateError):
        arb._controller._playback.play(speech_audio())
    assert pb.calls == 0


# ---- release on every path ----------------------------------------------------------------------------------------------------------------------------------

def test_the_arbiter_is_released_after_every_kind_of_failure():
    arb, tts, pb, _ = build(tts_bug=ValueError("a bug"))
    with pytest.raises(ValueError):
        arb.submit_answer("Boom.", request_id="a1")
    assert arb.phase is OutputPhase.IDLE and arb.active_request_type is None
    tts.bug = None
    assert arb.submit_answer("Works again.", request_id="a2").output.status is OutputStatus.COMPLETED

    arb2, _, pb2, _ = build(play_bug=KeyError("a bug"))
    with pytest.raises(KeyError):
        arb2.submit_answer("Boom.", request_id="a1")
    assert arb2.phase is OutputPhase.IDLE
    pb2.bug = None
    assert arb2.submit_answer("Fine.", request_id="a2").granted

    arb3, _, pb3, _ = build(play_error=PlaybackFailed("device"))
    o = arb3.submit_announcement(ann())
    assert o.granted and o.output.status is OutputStatus.PLAYBACK_FAILED and arb3.phase is OutputPhase.IDLE
    pb3.error = None
    assert arb3.submit_announcement(ann(2)).output.status is OutputStatus.COMPLETED

    arb4, _, _, _ = build()
    arb4._controller._tts._error = None
    from speech.tts import TtsSynthesisFailed
    arb4._controller._tts.error = TtsSynthesisFailed("x")
    o4 = arb4.submit_answer("Will fail.", request_id="a1")
    assert o4.granted and o4.output.status is OutputStatus.TTS_FAILED and arb4.phase is OutputPhase.IDLE


# ---- real thread contention ------------------------------------------------------------------------------------------------------------------------------------

KINDS = ["answer", "announcement", "fallback", "answer", "announcement", "answer", "fallback", "announcement"]


def _submit_kind(arb, i, kind):
    if kind == "answer":
        return arb.submit_answer(f"Answer number {i}.", request_id=f"a{i}")
    if kind == "announcement":
        return arb.submit_announcement(ann(i, text=f"Announcement number {i}."))
    return arb.submit_fallback(FallbackReason.UPSTREAM_TIMEOUT, request_id=f"fb{i}")


@pytest.mark.parametrize("hold_in", ["tts", "playback"])
def test_concurrent_requests_never_overlap_and_exactly_one_is_granted_per_round(hold_in):
    rounds, n = 12, len(KINDS)
    for rnd in range(rounds):
        gate = threading.Event() if hold_in == "tts" else None
        arb, tts, pb, probe = build(gate=gate, hold=(hold_in == "playback"))
        barrier = threading.Barrier(n)
        outcomes = [None] * n

        def worker(i):
            barrier.wait(WAIT)
            outcomes[i] = _submit_kind(arb, i, KINDS[i])

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
        for t in threads:
            t.start()
        deadline = time.time() + WAIT
        while sum(o is not None for o in outcomes) < n - 1 and time.time() < deadline:     # wait for every loser to have returned
            time.sleep(0.0005)
        assert sum(o is not None for o in outcomes) == n - 1, "losers did not return while the winner was held"
        (gate.set() if hold_in == "tts" else pb.release.set())
        for t in threads:
            join(t)
        granted = [i for i, o in enumerate(outcomes) if o.granted]
        assert len(granted) == 1                                                            # no double admission
        w = granted[0]
        wclass = CLASS_OF[outcomes[w].arbitration.request_type]
        for i, o in enumerate(outcomes):
            if i == w:
                assert o.output.status is OutputStatus.COMPLETED
                continue
            assert o.output is None
            c = CLASS_OF[o.arbitration.request_type]
            expected = {(RequestClass.CONVERSATION, RequestClass.CONVERSATION): (Decision.REJECTED_ACTIVE_OUTPUT,),
                        (RequestClass.ANNOUNCEMENT, RequestClass.ANNOUNCEMENT): (Decision.REJECTED_ACTIVE_OUTPUT,),
                        (RequestClass.ANNOUNCEMENT, RequestClass.CONVERSATION): (Decision.REJECTED_LOWER_PRIORITY,),
                        (RequestClass.CONVERSATION, RequestClass.ANNOUNCEMENT): (Decision.RETRY_REQUIRED,)}[(c, wclass)]
            assert o.arbitration.decision in expected
        assert len(tts.texts) == 1 and pb.calls == 1                                        # nothing queued behind the winner
        assert probe.max == {"tts": 1, "play": 1}                                           # never two syntheses, never two playbacks
        assert arb.phase is OutputPhase.IDLE and arb.active_request_type is None
        assert _submit_kind(arb, 99, "answer").granted                                      # not stuck busy


def test_many_sequential_and_stopped_requests_leave_no_stuck_state():
    arb, tts, pb, probe = build()
    for i in range(25):
        assert _submit_kind(arb, i, KINDS[i % len(KINDS)]).granted
    assert pb.calls == 25 and probe.max == {"tts": 1, "play": 1} and arb.phase is OutputPhase.IDLE


# ---- privacy and scope ---------------------------------------------------------------------------------------------------------------------------------------

def test_arbitration_logs_carry_no_text_and_name_the_policy(logs):
    arb, _, _, _ = build()
    arb.submit_answer(SECRET, request_id="a1")
    arb.submit_batch([SpeechSubmission.announce(ann(text=SECRET + " announced")), SpeechSubmission.fallback(FallbackReason.UPSTREAM_TIMEOUT)])
    arb.submit_batch([SpeechSubmission.answer("   ", "a2")])
    arb.submit_announcement(ann(2, text=SECRET + " announced granted"))      # a GRANTED announcement: its text must not reach the logs either
    joined = "\n".join(logs)
    assert joined.count("event=output_arbitration") == 5                  # one + a batch of two + one invalid + one granted announcement
    for fragment in ("secret", "exam", "forty", "announced", "sorry", "right now"):
        assert fragment not in joined.lower(), fragment
    line = next(l for l in logs if "decision=REJECTED_LOWER_PRIORITY" in l)
    for field in ("policy=AI7_MIDTERM_V1", "request_type=ANNOUNCEMENT", "request_class=ANNOUNCEMENT", "reason=LOST_CONTENTION_LOWER_CLASS", "request_id=ann-1"):
        assert field in line


def test_the_arbiter_module_has_no_audio_device_tts_runtime_or_network_dependency():
    tree = ast.parse((REPO_ROOT / "assistant" / "output_arbiter.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"itertools", "re", "threading", "time", "dataclasses", "enum", "typing", "assistant", "audio", "config", "speech"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"SherpaOnnxTtsEngine", "SoundDevicePlaybackEngine", "sounddevice", "sherpa_onnx", "socket", "urlopen"}       # the import allow-list above already excludes the `requests` library
    source = (REPO_ROOT / "assistant" / "output_arbiter.py").read_text(encoding="utf-8").lower()
    for word in ("professor", "occupancy", "classroomstate"):
        assert word not in source


def test_importing_the_arbiter_loads_no_engine_runtime():
    code = (
        "import sys\n"
        "import assistant.output_arbiter\n"
        "bad = [m for m in ('sherpa_onnx', 'faster_whisper', 'ctranslate2', 'huggingface_hub') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr


# =============================================================================================================
# Real offline voice + FAKE playback. Nothing is played. Shows that the arbiter works with the real TTS engine.
# =============================================================================================================

def test_the_arbiter_works_with_the_real_offline_voice_and_a_fake_speaker():
    from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
    from speech.tts import TtsModelNotFound
    try:
        real = SherpaOnnxTtsEngine()
    except TtsModelNotFound:
        pytest.skip("approved voice package not present")
    except TtsRuntimeUnavailable as exc:
        pytest.skip(f"sherpa-onnx not importable: {exc}")
    probe = Probe()
    playback = ProbePlayback(probe)
    arb = OutputArbiter(real, playback)
    outs = arb.submit_batch([SpeechSubmission.announce(ann()), SpeechSubmission.fallback(FallbackReason.NETWORK_UNAVAILABLE)])
    assert [o.arbitration.decision for o in outs] == [Decision.REJECTED_LOWER_PRIORITY, Decision.GRANTED]
    assert outs[1].output.status is OutputStatus.COMPLETED and outs[1].output.voice_id == "en_US-lessac-medium" and playback.calls == 1
    assert probe.max["play"] == 1
