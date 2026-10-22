"""Team Leader AI-8 integration: the state contract wired to the AI-7 OutputArbiter and the playback first-buffer signal.

SOFTWARE-ONLY. Fake TTS, fake playback (or the real sounddevice backend on a fake `sounddevice` module) and a fake Face subscriber.
Nothing is played, no LED or display is driven, and no live Vision/Face synchronization is established.

Also contains the runtime call-site audit that proves every spoken-output path in the non-test code goes through OutputArbiter.
"""

import ast
import logging
import threading
import time
from pathlib import Path

import pytest

from assistant import output_arbiter as oa
from assistant.output_arbiter import ArbiterRequestError, Decision, OutputArbiter, OutputPhase, SpeechSubmission
from assistant.spoken_output import AnnouncementRequest, AnnouncementSource, FallbackReason, OutputStatus
from assistant.state import AssistantState as S, AssistantStateManager, StateColor, StateEvent, StateReason as R
from audio import playback as pb_module
from audio.playback import (
    PlaybackEngine, PlaybackError, PlaybackFailed, PlaybackInputError, PlaybackOpenFailed, PlaybackResult, PlaybackStatus,
    SoundDevicePlaybackEngine,
)
from speech.tts import TtsSynthesisFailed
from tests.spoken_output_fakes import FakePlayback, FakeSd, FakeTts, speech_audio

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET = "my secret exam answer is forty two"
WAIT = 10


# ---- fakes --------------------------------------------------------------------------------------------------------------------

class GateTts(FakeTts):
    def __init__(self, gate=None, bug=None):
        super().__init__()
        self.gate, self.bug = gate, bug
        self.entered, self.finished = threading.Event(), threading.Event()
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(WAIT), "tts gate never released"
        if self.bug:
            raise self.bug
        audio = super().synthesize(text)
        self.finished.set()
        return audio


class SignallingPlayback(PlaybackEngine):
    """A fake engine WITH the first-buffer signal. It reports the first buffer exactly where a real engine would: after the stream
    is open and the first samples are in the device buffer, and never when it fails or is stopped before that."""

    def __init__(self, hold=False, before_first=None, fail_before=None, fail_after=None, bug=None):
        self.hold, self.before_first, self.fail_before, self.fail_after, self.bug = hold, before_first, fail_before, fail_after, bug
        self.calls = self.max_concurrent = self.active = 0
        self.lock = threading.Lock()
        self.called, self.first_sent, self.release = threading.Event(), threading.Event(), threading.Event()
        self.state_seen_after_listener = None
        self.stopped = False
        self.on_first = None                      # test hook: runs right after the listener, on the playback thread
        self._listener = None
        self._playing = False

    @property
    def supports_first_buffer_listener(self):
        return True

    def set_first_buffer_listener(self, listener):
        self._listener = listener

    def play(self, audio):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
        self._playing = True
        try:
            self.called.set()
            if self.before_first is not None:
                assert self.before_first.wait(WAIT), "before_first gate never released"
            if self.bug:
                raise self.bug
            if self.stopped:                                              # stopped before the first buffer
                return PlaybackResult(PlaybackStatus.STOPPED, audio.duration_s, 10.0, None, None, 10.001, None, None, None, 0)
            if self.fail_before:
                raise self.fail_before                                    # e.g. the stream could not be opened
            if self._listener is not None:
                self._listener()
            if self.on_first:
                self.on_first()
            self.first_sent.set()
            if self.fail_after:
                raise self.fail_after
            if self.hold:
                assert self.release.wait(WAIT), "playback never released"
            status = PlaybackStatus.STOPPED if self.stopped else PlaybackStatus.COMPLETED
            return PlaybackResult(status, audio.duration_s, 10.0, 10.01, 10.02, 10.5, "fake-device", "fake-api", 0.01, 0)
        finally:
            self._playing = False
            with self.lock:
                self.active -= 1

    def stop(self):
        self.stopped = True
        self.release.set()
        if self.before_first is not None:
            self.before_first.set()
        return True

    @property
    def is_playing(self):
        return self._playing


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)

    def trail(self):
        return [(e.state.name, e.reason_code.name) for e in self.events]


class FakeFace:
    """SOFTWARE-ONLY stand-in for the Vision/Face renderer: it records the colours it would be told to show. It drives nothing."""

    def __init__(self):
        self.shown = []

    def __call__(self, event):
        self.shown.append(event.color_semantic)


class Rig:
    pass


def rig(*, tts=None, playback=None, **playback_kw):
    r = Rig()
    r.mgr = AssistantStateManager()
    r.rec, r.face = Recorder(), FakeFace()
    r.mgr.subscribe(r.rec)
    r.mgr.subscribe(r.face)
    r.tts = tts or GateTts()
    r.pb = playback or SignallingPlayback(**playback_kw)
    r.arb = OutputArbiter(r.tts, r.pb, state=r.mgr)
    return r


def ann(i=1, text="Lunch is served in the main hall."):
    return AnnouncementRequest(f"ann-{i}", text, AnnouncementSource.APP)


def in_thread(fn):
    box = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    return t, box


def join(t):
    t.join(WAIT)
    assert not t.is_alive(), "worker did not finish"


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def capture(logger):
    h = _LogCapture()
    logger.addHandler(h)
    return h


# ---- the playback first-buffer signal (audio/playback.py) -----------------------------------------------------------------------------

def test_an_engine_without_the_signal_says_so_instead_of_pretending():
    fake = FakePlayback()
    assert fake.supports_first_buffer_listener is False
    with pytest.raises(PlaybackError):
        fake.set_first_buffer_listener(lambda: None)


def test_the_sounddevice_engine_calls_the_listener_once_on_the_play_thread_after_the_first_buffer_and_before_play_returns():
    sd = FakeSd()
    engine = SoundDevicePlaybackEngine(sd_module=sd)
    assert engine.supports_first_buffer_listener is True
    seen = []
    state = {"returned": False}
    caller = threading.get_ident()

    def listener():
        seen.append((threading.get_ident(), state["returned"], len(sd.streams[0].played)))

    engine.set_first_buffer_listener(listener)
    result = engine.play(speech_audio(4000))
    state["returned"] = True
    assert result.status is PlaybackStatus.COMPLETED and len(seen) == 1
    thread_id, returned_before, blocks_played = seen[0]
    assert thread_id == caller and returned_before is False and blocks_played >= 1       # the first buffer was already in; not the audio thread


def test_the_listener_fires_while_the_stream_is_still_running_not_when_it_finishes():
    sd = FakeSd(block=256, pace_s=0.005)                                  # a paced fake device: 16 blocks of audio take about 80 ms
    engine = SoundDevicePlaybackEngine(sd_module=sd)
    at_signal = []
    engine.set_first_buffer_listener(lambda: at_signal.append(len(sd.streams[0].played)))
    engine.play(speech_audio(4096))
    assert len(at_signal) == 1 and 1 <= at_signal[0] <= 8 < 16 <= len(sd.streams[0].played)       # signalled near the start, long before the end


def test_the_listener_runs_on_every_play_and_none_clears_it():
    engine = SoundDevicePlaybackEngine(sd_module=FakeSd())
    calls = []
    engine.set_first_buffer_listener(lambda: calls.append(1))
    engine.play(speech_audio(800))
    engine.play(speech_audio(800))
    engine.set_first_buffer_listener(None)
    engine.play(speech_audio(800))
    assert calls == [1, 1]
    with pytest.raises(PlaybackInputError):
        engine.set_first_buffer_listener("not callable")


@pytest.mark.parametrize("kw,error", [(dict(open_error=RuntimeError("boom")), PlaybackOpenFailed), (dict(start_error=RuntimeError("boom")), PlaybackOpenFailed),
                                      (dict(bad_outdata=True), PlaybackFailed)])
def test_the_listener_is_not_called_when_playback_fails_before_the_first_buffer(kw, error):
    engine = SoundDevicePlaybackEngine(sd_module=FakeSd(**kw))
    calls = []
    engine.set_first_buffer_listener(lambda: calls.append(1))
    with pytest.raises(error):
        engine.play(speech_audio(800))
    assert calls == []


def test_a_broken_listener_does_not_break_the_playback(monkeypatch):
    engine = SoundDevicePlaybackEngine(sd_module=FakeSd())
    handler = capture(pb_module.logger)

    def broken():
        raise RuntimeError(f"cannot show {SECRET}")

    engine.set_first_buffer_listener(broken)
    try:
        result = engine.play(speech_audio(800))
    finally:
        pb_module.logger.removeHandler(handler)
    joined = "\n".join(handler.lines)
    assert result.status is PlaybackStatus.COMPLETED and "playback_first_buffer_listener_error" in joined and "RuntimeError" in joined and "secret" not in joined.lower()


# ---- AI-7 + AI-8 scenarios ---------------------------------------------------------------------------------------------------------------

def test_scenario_a_answer_flow_listening_checking_speaking_idle():
    r = rig()
    r.mgr.transition(S.LISTENING, R.SPEECH_INPUT_ACTIVATED)                       # the input side: a speech-input session started
    r.mgr.transition(S.CHECKING, R.UTTERANCE_CAPTURED)                            # ... and the utterance is complete
    out = r.arb.submit_answer("There are 17 people in the room.", request_id="q1")
    assert out.granted and out.output.status is OutputStatus.COMPLETED
    assert r.rec.trail() == [("LISTENING", "SPEECH_INPUT_ACTIVATED"), ("CHECKING", "UTTERANCE_CAPTURED"), ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_COMPLETED")]
    assert [e.request_id for e in r.rec.events] == [None, None, "q1", "q1"]        # the arbiter's CHECKING was a no-op: the state was already CHECKING
    assert r.mgr.current_state is S.IDLE and r.arb.phase is OutputPhase.IDLE and r.arb.state_sync_error_count == 0


def test_scenario_b_announcement_flow_has_no_listening_and_goes_checking_speaking_idle():
    r = rig()
    out = r.arb.submit_announcement(ann(7))
    assert out.granted
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_COMPLETED")]
    assert [e.request_id for e in r.rec.events] == ["ann-7"] * 3 and S.LISTENING not in {e.state for e in r.rec.events}


def test_scenario_j_fallback_standalone_and_during_an_interaction():
    alone = rig()
    assert alone.arb.submit_fallback(FallbackReason.UPSTREAM_TIMEOUT).granted
    assert alone.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_COMPLETED")]
    during = rig()
    during.mgr.transition(S.LISTENING, R.SPEECH_INPUT_ACTIVATED)
    during.mgr.transition(S.CHECKING, R.UTTERANCE_CAPTURED)
    assert during.arb.submit_fallback(FallbackReason.NETWORK_UNAVAILABLE).granted
    assert [e.state for e in during.rec.events] == [S.LISTENING, S.CHECKING, S.SPEAKING, S.IDLE]


@pytest.mark.parametrize("phase", ["SYNTHESIZING", "SPEAKING"])
def test_scenario_c_a_busy_or_rejected_request_never_produces_speaking_or_disturbs_the_active_state(phase):
    gate = threading.Event()
    r = rig(tts=GateTts(gate=gate)) if phase == "SYNTHESIZING" else rig(hold=True)
    t, box = in_thread(lambda: r.arb.submit_answer("The active answer.", request_id="active-1"))
    assert (r.tts.entered if phase == "SYNTHESIZING" else r.pb.first_sent).wait(WAIT)
    expected_state = S.CHECKING if phase == "SYNTHESIZING" else S.SPEAKING
    assert r.mgr.current_state is expected_state
    trail_before = r.rec.trail()
    outcomes = [r.arb.submit_announcement(ann(2)),                                 # lower class: REJECTED_LOWER_PRIORITY
                r.arb.submit_answer("Another answer.", request_id="q2"),           # same class: REJECTED_ACTIVE_OUTPUT
                r.arb.submit_fallback(FallbackReason.UPSTREAM_TIMEOUT),
                r.arb.submit_batch([SpeechSubmission.answer("", "bad-1")])[0]]     # invalid
    assert [o.arbitration.decision for o in outcomes] == [Decision.REJECTED_LOWER_PRIORITY, Decision.REJECTED_ACTIVE_OUTPUT,
                                                         Decision.REJECTED_ACTIVE_OUTPUT, Decision.INVALID_REQUEST]
    assert r.rec.trail() == trail_before and r.mgr.current_state is expected_state       # nothing was published for the losers
    (gate.set() if phase == "SYNTHESIZING" else r.pb.release.set())
    join(t)
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_COMPLETED")]


def test_a_rejected_announcement_is_not_given_the_speaking_state_even_though_it_outranks_nothing():
    r = rig(hold=True)
    t, box = in_thread(lambda: r.arb.submit_announcement(ann(1)))
    assert r.pb.first_sent.wait(WAIT)
    retry = r.arb.submit_answer("A question arrives.", request_id="q9")           # conversation vs active announcement: RETRY_REQUIRED
    assert retry.arbitration.decision is Decision.RETRY_REQUIRED and r.mgr.current_state is S.SPEAKING
    assert [e.request_id for e in r.rec.events] == ["ann-1", "ann-1"]
    r.pb.release.set()
    join(t)
    assert r.arb.submit_answer("A question arrives.", request_id="q9").granted and r.mgr.current_state is S.IDLE


def test_scenario_d_stop_during_synthesis_never_produces_speaking():
    gate = threading.Event()
    r = rig(tts=GateTts(gate=gate))
    t, box = in_thread(lambda: r.arb.submit_answer("Cancelled.", request_id="q1"))
    assert r.tts.entered.wait(WAIT) and r.mgr.current_state is S.CHECKING
    assert r.arb.stop() is True
    gate.set()
    join(t)
    assert box[0].output.status is OutputStatus.STOPPED and r.pb.calls == 0
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("IDLE", "OUTPUT_STOPPED_BEFORE_PLAYBACK")]
    assert StateColor.GREEN not in r.face.shown


def test_scenario_n_stop_between_synthesis_and_playback_never_produces_speaking():
    tts, pb, mgr, rec = GateTts(), SignallingPlayback(), AssistantStateManager(), Recorder()
    mgr.subscribe(rec)

    class HookClock:
        reached, proceed, armed = threading.Event(), threading.Event(), True

        def __call__(self):
            if self.armed and tts.finished.is_set():                  # synthesis has returned; playback has not been requested
                self.armed = False
                self.reached.set()
                assert self.proceed.wait(WAIT)
            return time.perf_counter()

    hook = HookClock()
    arb = OutputArbiter(tts, pb, clock=hook, state=mgr)
    t, box = in_thread(lambda: arb.submit_announcement(ann()))
    assert hook.reached.wait(WAIT) and arb.stop() is True
    hook.proceed.set()
    join(t)
    assert box[0].output.status is OutputStatus.STOPPED and pb.calls == 0
    assert rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("IDLE", "OUTPUT_STOPPED_BEFORE_PLAYBACK")]


def test_scenario_e_stop_during_playback_goes_speaking_to_idle():
    r = rig(hold=True)
    t, box = in_thread(lambda: r.arb.submit_answer("A long answer.", request_id="q1"))
    assert r.pb.first_sent.wait(WAIT) and r.mgr.current_state is S.SPEAKING
    assert r.arb.stop() is True
    join(t)
    assert box[0].output.status is OutputStatus.STOPPED and box[0].output.playback_status is PlaybackStatus.STOPPED
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_STOPPED")]


def test_a_stop_that_beats_the_first_buffer_inside_the_engine_never_produces_speaking():
    gate = threading.Event()
    r = rig(before_first=gate)
    t, box = in_thread(lambda: r.arb.submit_answer("Stopped at the last moment.", request_id="q1"))
    assert r.pb.called.wait(WAIT) and r.mgr.current_state is S.CHECKING
    assert r.arb.stop() is True                                         # the engine is playing (open) but no sample has reached the device
    join(t)
    assert box[0].output.status is OutputStatus.STOPPED
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("IDLE", "OUTPUT_STOPPED_BEFORE_PLAYBACK")]


def test_scenario_f_playback_failing_before_the_first_buffer_never_produces_speaking():
    r = rig(fail_before=PlaybackOpenFailed("could not open the stream"))
    out = r.arb.submit_answer("This cannot be played.", request_id="q1")
    assert out.granted and out.output.status is OutputStatus.PLAYBACK_FAILED
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("IDLE", "PLAYBACK_FAILED")]
    assert StateColor.GREEN not in r.face.shown and r.mgr.current_state is S.IDLE


def test_scenario_g_playback_failing_after_it_started_goes_speaking_to_idle_with_the_error_reason():
    r = rig(fail_after=PlaybackFailed("the stream broke"))
    out = r.arb.submit_answer("This breaks midway.", request_id="q1")
    assert out.output.status is OutputStatus.PLAYBACK_FAILED
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_FAILED")]


def test_a_tts_failure_goes_checking_to_idle():
    r = rig(tts=FakeTts(error=TtsSynthesisFailed("no voice")))
    out = r.arb.submit_answer("Anything.", request_id="q1")
    assert out.output.status is OutputStatus.TTS_FAILED and r.pb.calls == 0
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("IDLE", "TTS_FAILED")]


@pytest.mark.parametrize("where", ["tts", "playback"])
def test_a_software_bug_still_returns_the_state_to_idle_and_frees_the_arbiter(where):
    r = rig(tts=GateTts(bug=KeyError("bug"))) if where == "tts" else rig(bug=KeyError("bug"))
    with pytest.raises(KeyError):
        r.arb.submit_answer("Boom.", request_id="q1")
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("IDLE", "OUTPUT_ERROR")]
    assert r.mgr.current_state is S.IDLE and r.arb.phase is OutputPhase.IDLE


def test_an_invalid_request_publishes_nothing():
    r = rig()
    [o] = r.arb.submit_batch([SpeechSubmission.answer("   ", "bad-1")])
    assert o.arbitration.decision is Decision.INVALID_REQUEST and r.rec.events == [] and r.mgr.current_event.sequence_number == 0


def test_the_state_stays_checking_until_the_first_buffer_not_at_admission_or_tts_completion():
    tts_gate, play_gate = threading.Event(), threading.Event()
    r = rig(tts=GateTts(gate=tts_gate), before_first=play_gate)
    t, box = in_thread(lambda: r.arb.submit_answer("Take your time.", request_id="q1"))
    assert r.tts.entered.wait(WAIT) and r.mgr.current_state is S.CHECKING             # admitted, synthesizing
    tts_gate.set()
    assert r.pb.called.wait(WAIT) and r.arb.phase is OutputPhase.PLAYING and r.tts.finished.is_set()
    assert r.mgr.current_state is S.CHECKING and StateColor.GREEN not in r.face.shown   # TTS is done and the arbiter is PLAYING, yet no sample has played
    play_gate.set()
    join(t)
    assert [e.state for e in r.rec.events] == [S.CHECKING, S.SPEAKING, S.IDLE]


def test_a_stray_first_buffer_signal_outside_the_playing_phase_is_ignored():
    gate = threading.Event()
    r = rig(tts=GateTts(gate=gate))
    r.arb._on_first_buffer()                                              # idle: there is no owner, so nothing may be published
    assert r.rec.events == [] and r.arb.state_sync_error_count == 0
    t, box = in_thread(lambda: r.arb.submit_answer("Synthesizing.", request_id="q1"))
    assert r.tts.entered.wait(WAIT)
    r.arb._on_first_buffer()                                              # admitted and synthesizing, but not playing
    assert r.mgr.current_state is S.CHECKING
    gate.set()
    join(t)
    assert [e.state for e in r.rec.events] == [S.CHECKING, S.SPEAKING, S.IDLE]


def test_speaking_is_published_by_the_playback_thread_before_the_engine_returns():
    r = rig()
    r.pb.on_first = lambda: setattr(r.pb, "state_seen_after_listener", (r.mgr.current_state, r.arb.phase))
    r.arb.submit_answer("Quick.", request_id="q1")
    assert r.pb.state_seen_after_listener == (S.SPEAKING, OutputPhase.PLAYING)


# ---- the arbiter's contract around the state manager ----------------------------------------------------------------------------------------

def test_a_state_manager_needs_an_engine_that_reports_its_first_buffer_and_the_arbiter_works_without_one():
    with pytest.raises(ArbiterRequestError):
        OutputArbiter(GateTts(), FakePlayback(), state=AssistantStateManager())
    with pytest.raises(ArbiterRequestError):
        OutputArbiter(GateTts(), SignallingPlayback(), state="not a manager")
    plain = OutputArbiter(GateTts(), FakePlayback())                       # no state manager: the AI-7 behaviour, unchanged
    assert plain.submit_answer("Fine.", request_id="q1").granted and plain.state_sync_error_count == 0


def test_a_state_error_is_counted_and_logged_but_never_stops_the_speech():
    r = rig()
    r.mgr.transition(S.CHECKING, R.OUTPUT_ADMITTED)
    r.mgr.transition(S.SPEAKING, R.PLAYBACK_STARTED)                      # someone else left the state in SPEAKING: CHECKING is now illegal
    handler = capture(oa.logger)
    try:
        out = r.arb.submit_answer("Still spoken.", request_id="q1")
    finally:
        oa.logger.removeHandler(handler)
    assert out.output.status is OutputStatus.COMPLETED and r.arb.state_sync_error_count == 1
    assert any("event=state_sync_error" in line and "InvalidStateTransition" in line for line in handler.lines)
    assert r.mgr.current_state is S.IDLE


def test_a_broken_face_subscriber_does_not_change_the_speech_or_the_state():
    r = rig()

    def broken(event):
        raise RuntimeError(f"display crashed {SECRET}")

    r.mgr.subscribe(broken)
    out = r.arb.submit_answer("Spoken anyway.", request_id="q1")
    assert out.output.status is OutputStatus.COMPLETED and r.mgr.current_state is S.IDLE
    assert r.rec.trail() == [("CHECKING", "OUTPUT_ADMITTED"), ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_COMPLETED")]
    assert r.mgr.listener_failure_count == 3 and r.arb.state_sync_error_count == 0


def test_a_face_that_connects_while_speaking_learns_green_immediately():
    r = rig(hold=True)
    t, box = in_thread(lambda: r.arb.submit_answer("Long answer.", request_id="q1"))
    assert r.pb.first_sent.wait(WAIT)
    late = Recorder()
    r.mgr.subscribe(late, replay_current=True)
    assert [(e.state, e.color_semantic) for e in late.events] == [(S.SPEAKING, StateColor.GREEN)] and r.mgr.current_color is StateColor.GREEN
    r.pb.release.set()
    join(t)
    assert [e.state for e in late.events] == [S.SPEAKING, S.IDLE]


def test_sequential_requests_make_one_contiguous_ordered_history():
    r = rig()
    r.arb.submit_answer("One.", request_id="q1")
    r.arb.submit_announcement(ann(2))
    r.arb.submit_fallback(FallbackReason.INTERNAL_SERVICE_FAILURE)
    seqs = [e.sequence_number for e in r.rec.events]
    assert seqs == list(range(1, 10)) and r.mgr.current_event.sequence_number == 9
    assert all(b.timestamp_monotonic >= a.timestamp_monotonic for a, b in zip(r.rec.events, r.rec.events[1:]))
    assert [e.request_id for e in r.rec.events] == ["q1"] * 3 + ["ann-2"] * 3 + ["fallback-internal-service-failure"] * 3


def test_many_simultaneous_requests_publish_exactly_one_lifecycle():
    for hold_in in ("tts", "playback"):
        for _ in range(8):
            gate = threading.Event() if hold_in == "tts" else None
            r = rig(tts=GateTts(gate=gate), hold=(hold_in == "playback"))
            n = 8
            barrier = threading.Barrier(n)
            outcomes = [None] * n
            kinds = ["answer", "announcement", "fallback", "answer", "announcement", "answer", "fallback", "announcement"]

            def worker(i):
                barrier.wait(WAIT)
                k = kinds[i]
                outcomes[i] = (r.arb.submit_answer(f"Answer {i}.", request_id=f"a{i}") if k == "answer" else
                               r.arb.submit_announcement(ann(i, f"Announcement {i}.")) if k == "announcement" else
                               r.arb.submit_fallback(FallbackReason.UPSTREAM_TIMEOUT, request_id=f"fb{i}"))

            threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
            for th in threads:
                th.start()
            deadline = time.time() + WAIT
            while sum(o is not None for o in outcomes) < n - 1 and time.time() < deadline:
                time.sleep(0.0005)
            assert sum(o is not None for o in outcomes) == n - 1
            assert len(r.rec.events) <= 2                                       # the losers published nothing; at most the winner's CHECKING / SPEAKING so far
            (gate.set() if hold_in == "tts" else r.pb.release.set())
            for th in threads:
                join(th)
            winner = [o for o in outcomes if o.granted]
            assert len(winner) == 1
            assert [e.state for e in r.rec.events] == [S.CHECKING, S.SPEAKING, S.IDLE]
            assert {e.request_id for e in r.rec.events} == {winner[0].arbitration.request_id}
            assert r.pb.max_concurrent == 1 and r.mgr.current_state is S.IDLE and r.arb.phase is OutputPhase.IDLE


def test_synthetic_answer_flow_shows_red_then_blue_then_green_then_no_colour_on_a_fake_face_SOFTWARE_ONLY():
    r = rig()
    r.mgr.transition(S.LISTENING, R.SPEECH_INPUT_ACTIVATED)
    r.mgr.transition(S.CHECKING, R.UTTERANCE_CAPTURED)
    r.arb.submit_answer("The room is occupied.", request_id="q1")
    assert r.face.shown == [StateColor.RED, StateColor.BLUE, StateColor.GREEN, StateColor.NO_ACTIVE_COLOR]
    assert [c.value for c in r.face.shown[:3]] == ["RED", "BLUE", "GREEN"]


def test_state_events_and_logs_never_contain_the_spoken_text(capsys):
    r = rig()
    handler = capture(oa.logger)                      # one shared "homepod_ai" logger carries the arbiter, state and controller lines
    try:
        r.mgr.transition(S.LISTENING, R.SPEECH_INPUT_ACTIVATED)
        r.mgr.transition(S.CHECKING, R.UTTERANCE_CAPTURED)
        r.arb.submit_answer(SECRET, request_id="q1")
        r.arb.submit_announcement(ann(2, text=SECRET + " announced"))
        r.arb.submit_fallback(FallbackReason.NETWORK_UNAVAILABLE)
    finally:
        oa.logger.removeHandler(handler)
    blob = "\n".join(handler.lines) + repr(r.rec.events)
    for fragment in ("secret", "exam", "forty", "announced", "sorry", "right now"):
        assert fragment not in blob.lower(), fragment
    assert blob.count("event=assistant_state") == 10                    # 2 input-side + (answer: 2, announcement: 3, fallback: 3)


# ---- AI-7 single owner: every runtime call site (non-test code) ------------------------------------------------------------------------------

_EXCLUDED = {".venv", "models", "tests", "skill-observations", ".claude", "__pycache__", ".pytest_cache", "skill-updates"}


def _runtime_python_files():
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if not (set(rel.parts) & _EXCLUDED):
            yield path, rel.as_posix()


def _call_sites():
    sites = set()
    for path, rel in _runtime_python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name in {"SpokenOutputController", "OutputArbiter", "speak_answer", "speak_announcement", "speak_fallback", "play", "synthesize",
                            "SoundDevicePlaybackEngine", "SherpaOnnxTtsEngine"}:
                    sites.add((name, rel))
    return sites


def test_every_runtime_path_to_speech_goes_through_the_output_arbiter():
    """The complete table of non-test call sites. Adding a caller anywhere else makes this fail and forces a review."""
    assert _call_sites() == {
        ("SpokenOutputController", "assistant/output_arbiter.py"),     # the arbiter builds the ONE controller
        ("speak_answer", "assistant/output_arbiter.py"),
        ("speak_announcement", "assistant/output_arbiter.py"),
        ("speak_fallback", "assistant/output_arbiter.py"),
        ("synthesize", "assistant/spoken_output.py"),                  # only the controller synthesizes
        ("play", "assistant/output_arbiter.py"),                       # the arbiter's guard is the only thing that reaches the real playback engine
        ("play", "assistant/spoken_output.py"),                        # the controller plays on ITS engine: in an arbiter-built controller that is the guard
        ("OutputArbiter", "assistant/runtime.py"),                     # the ONE composition root builds the ONE arbiter (deliberate update, 2026-10-16)
        ("SherpaOnnxTtsEngine", "scripts/vesta_runtime_demo.py"),      # the development demo's optional real VOICE (synthesis only; its playback is fake)
        ("SherpaOnnxTtsEngine", "scripts/vesta_live_voice.py"),        # midterm live runner (approved): the real voice, handed to the runtime
        ("SoundDevicePlaybackEngine", "scripts/vesta_live_voice.py"),  # midterm live runner (approved): the real speaker, handed to the runtime
        # team backend demo (reviewed addition, 2026-10-22): its real voice is handed to the bridge's runtime, so replies go through the arbiter;
        # its one direct synthesize() makes the SYNTHETIC INPUT sentence for the STT step and is never played
        ("SherpaOnnxTtsEngine", "scripts/vesta_ai_backend_demo.py"),
        ("synthesize", "scripts/vesta_ai_backend_demo.py"),
        ("play", "scripts/vesta_ai_backend_demo.py"),                  # its WAV-file playback (no speaker) delegates to the fake DemoPlayback
    }


def test_the_one_composition_root_is_the_only_place_that_builds_an_arbiter_and_no_real_playback_engine_is_built_anywhere():
    """assistant/runtime.py is the single composition root: it builds the ONE OutputArbiter. A real playback engine is constructed nowhere
    in this repository yet (a live runner would be the next, separately approved place), and the only real TTS construction is the
    development demo's optional --tts real. Any other site must be reviewed and added here on purpose."""
    sites = _call_sites()
    assert {s for s in sites if s[0] == "OutputArbiter"} == {("OutputArbiter", "assistant/runtime.py")}
    # midterm readiness: the approved live runner is the ONE place a real playback engine is built; it hands it to the runtime's arbiter
    assert {s for s in sites if s[0] == "SoundDevicePlaybackEngine"} == {("SoundDevicePlaybackEngine", "scripts/vesta_live_voice.py")}
    assert {s for s in sites if s[0] == "SherpaOnnxTtsEngine"} == {("SherpaOnnxTtsEngine", "scripts/vesta_runtime_demo.py"),
                                                                  ("SherpaOnnxTtsEngine", "scripts/vesta_live_voice.py"),
                                                                  ("SherpaOnnxTtsEngine", "scripts/vesta_ai_backend_demo.py")}   # 2026-10-22, see above


def test_only_the_arbiter_imports_the_controller_in_runtime_code():
    importers = set()
    for path, rel in _runtime_python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "assistant.spoken_output" and any(a.name == "SpokenOutputController" for a in node.names):
                importers.add(rel)
    assert importers == {"assistant/output_arbiter.py"}


def test_the_controller_the_arbiter_built_only_reaches_playback_through_the_guard():
    r = rig()
    controller = r.arb._controller
    assert isinstance(controller._playback, oa._GuardedPlayback) and controller._playback._inner is r.pb
    with pytest.raises(oa.ArbiterStateError):
        controller._playback.play(speech_audio())                          # outside a granted request the guard refuses
    assert r.pb.calls == 0
