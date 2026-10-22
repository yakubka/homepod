"""Team Leader end-to-end software composition: assistant/runtime.py.

SOFTWARE-VALIDATED WITH FAKE INPUT / OUTPUT. A scripted wake detector (a real WakeWordDetector subclass), an energy VAD, a scripted STT,
a fake TTS and a fake playback engine stand in for the models and devices; the pipeline, the deterministic answer engine, the output
arbiter and the state manager are the real, frozen modules. These tests prove the wiring and the policy. They prove nothing about a
microphone, a speaker, wake / VAD / STT accuracy, latency or a live voice assistant: REAL LIVE VOICE ASSISTANT: NOT VALIDATED.

Concurrency uses Events held inside the fake TTS / playback; the waits are failure guards, not pacing delays.
"""

import ast
import re
import dataclasses
import logging
import threading
from pathlib import Path

import numpy as np
import pytest

from assistant import runtime as rtm
from assistant.answering import (
    AnswerContext, AnswerContractError, Capability, CapabilityDeclaration, DeterministicAnswerEngine,
)
from assistant.output_arbiter import ArbiterRequestError, Decision
from assistant.runtime import (
    NOT_SUPPORTED_TEXT, TEXT_ANSWER, TEXT_TRANSCRIPT, ExpectedServiceFailure, OutcomeKind, RuntimeConfigError, VoiceAssistantRuntime,
)
from assistant.spoken_output import FALLBACK_TEXT, AnnouncementRequest, AnnouncementSource, FallbackReason, OutputStatus
from assistant.state import AssistantState, StateReason
from audio.playback import PlaybackFailed
from speech.stt import SttInferenceFailed
from speech.tts import TtsSynthesisFailed
from tests.runtime_support import (
    GUARD_S, SILENCE, UTTERANCE, Rig, RuntimePlayback, RuntimeTts, S, ScriptedStt, SpyEngine, make_result, stream,
)

REPO = Path(__file__).resolve().parent.parent
ROOM = frozenset({Capability.PEOPLE_COUNT, Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS})
SECRET = "zebra quartz marmalade"
K = OutcomeKind


def context(*caps):
    declared = CapabilityDeclaration(frozenset(caps))
    return lambda: AnswerContext(capabilities=declared)


class _Logs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    h = _Logs()
    rtm.logger.addHandler(h)               # the shared homepod_ai logger: every module logs through it
    yield h.lines
    rtm.logger.removeHandler(h)


def only(outcomes):
    assert len(outcomes) == 1, [o.kind for o in outcomes]
    return outcomes[0]


# =========================================================================================
# A. one supported question, end to end, with the exact state lifecycle
# =========================================================================================

def test_a_supported_question_is_answered_spoken_and_the_states_follow_the_lifecycle():
    rig = Rig(context_provider=context(Capability.PEOPLE_COUNT, Capability.ANNOUNCEMENTS))
    o = only(rig.drive(UTTERANCE))
    assert (o.kind, o.pipeline_status, o.answer_status, o.intent, o.question_id) == (K.ANSWER_OUTPUT, S.TRANSCRIBED, "ANSWERED", "CAPABILITIES", "Q8")
    assert o.answer_source == "DECLARATION" and o.arbitration.decision is Decision.GRANTED and o.output.status is OutputStatus.COMPLETED
    assert rig.tts.texts == ["I'm VESTA, a classroom assistant. I can tell you the number of people in the room. I can play announcements."]
    assert rig.playback.calls == 1
    assert rig.states() == [("IDLE", "INITIAL"), ("LISTENING", "SPEECH_INPUT_ACTIVATED"), ("CHECKING", "UTTERANCE_CAPTURED"),
                            ("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_COMPLETED")]
    assert rig.colors() == ["NO_ACTIVE_COLOR", "RED", "BLUE", "GREEN", "NO_ACTIVE_COLOR"]
    assert [e.sequence_number for e in rig.events] == [0, 1, 2, 3, 4]
    assert rig.runtime.input_state_error_count == 0


def test_the_wake_prefix_is_removed_before_the_answer_engine_sees_the_text():
    seen = []
    rig = Rig(replies=("hey jarvis what can you do",), text_observer=lambda kind, text: seen.append((kind, text)))
    rig.drive(UTTERANCE)
    assert seen[0] == (TEXT_TRANSCRIPT, "what can you do") and seen[1][0] == TEXT_ANSWER


def test_the_runtime_owns_one_of_each_part_and_builds_no_engine_of_its_own():
    rig = Rig()
    rt = rig.runtime
    assert rt.state is rig.state and rt.pipeline.interactions == 0
    assert rt.arbiter._controller._tts is rig.tts                        # the injected TTS is the one the arbiter speaks through
    assert rt.arbiter._playback is rig.playback


# =========================================================================================
# B. unavailable information, unsupported questions, the default context
# =========================================================================================

def test_unavailable_room_information_is_spoken_as_unavailable_and_never_invented():
    rig = Rig(replies=("hey jarvis how many people are in the room",), context_provider=context(*ROOM))
    o = only(rig.drive(UTTERANCE))
    assert (o.answer_status, o.intent, o.answer_source, o.answer_reason_code) == ("INFORMATION_UNAVAILABLE", "ROOM_PEOPLE_COUNT", "UNAVAILABLE", "NO_CLASSROOM_STATE")
    assert rig.tts.texts == ["I don't have a current people count."]
    assert not any(ch.isdigit() for ch in rig.tts.texts[0])


def test_professor_arrival_is_always_unavailable_through_the_whole_chain():
    rig = Rig(replies=("hey jarvis has the professor arrived yet",), context_provider=context(*ROOM, Capability.LECTURE_INFORMATION))
    o = only(rig.drive(UTTERANCE))
    assert o.answer_status == "INFORMATION_UNAVAILABLE" and o.answer_reason_code == "NO_AUTHORITATIVE_ARRIVAL_STATE"
    assert rig.tts.texts == ["I don't have professor arrival information."]


def test_an_open_ended_request_is_classified_but_not_answered_and_no_model_is_called():
    engine = SpyEngine()
    rig = Rig(replies=("hey jarvis tell me a joke",), answer_engine=engine)
    o = only(rig.drive(UTTERANCE))
    assert (o.kind, o.intent_category, o.routing_status, o.handling) == (OutcomeKind.NOT_ANSWERABLE, "GENERAL_AI", "ROUTED", "NO_BACKEND_APPROVED")
    assert o.answer_status is None and engine.calls == 0                     # the answering engine is never asked, nothing is invented
    assert rig.tts.texts == [NOT_SUPPORTED_TEXT]


def test_the_default_context_declares_nothing_so_a_room_question_is_not_answered():
    rig = Rig(replies=("hey jarvis how many people are in the room",))
    o = only(rig.drive(UTTERANCE))
    assert o.answer_status == "UNSUPPORTED_QUESTION" and o.answer_reason_code == "CAPABILITY_NOT_DECLARED"


# =========================================================================================
# C. outcomes that are not a complete question
# =========================================================================================

def _no_answer_assertions(rig, engine, o, kind):
    assert o.kind is kind and o.arbitration is None and o.output is None and o.answer_status is None
    assert engine.calls == 0 and rig.tts.texts == [] and rig.playback.calls == 0           # nothing answered, synthesized or played


def test_wake_only_is_not_invented_into_a_question():
    engine = SpyEngine()
    rig = Rig(replies=("hey jarvis",), answer_engine=engine)
    o = only(rig.drive(UTTERANCE))
    _no_answer_assertions(rig, engine, o, K.WAKE_ONLY)
    assert o.pipeline_status is S.WAKE_ONLY
    assert rig.states() == [("IDLE", "INITIAL"), ("LISTENING", "SPEECH_INPUT_ACTIVATED"), ("CHECKING", "UTTERANCE_CAPTURED"), ("IDLE", "NO_OUTPUT")]


def test_no_speech_after_the_wake_ends_listening_without_stt_or_answer():
    engine = SpyEngine()
    rig = Rig(answer_engine=engine)
    o = only(rig.drive(SILENCE))
    _no_answer_assertions(rig, engine, o, K.NO_SPEECH)
    assert rig.stt.calls == [] and o.pipeline_status is S.NO_SPEECH
    assert rig.states() == [("IDLE", "INITIAL"), ("LISTENING", "SPEECH_INPUT_ACTIVATED"), ("IDLE", "NO_SPEECH_DETECTED")]


def test_a_too_short_burst_is_treated_like_no_speech():
    engine = SpyEngine()
    rig = Rig(answer_engine=engine)
    o = only(rig.drive(stream(1000, [(360, 400)])))
    assert o.pipeline_status is S.TOO_SHORT
    _no_answer_assertions(rig, engine, o, K.NO_SPEECH)
    assert rig.stt.calls == [] and rig.states()[-1] == ("IDLE", "NO_SPEECH_DETECTED")


def test_an_empty_transcript_is_not_answered():
    engine = SpyEngine()
    rig = Rig(replies=("",), answer_engine=engine)
    o = only(rig.drive(UTTERANCE))
    _no_answer_assertions(rig, engine, o, K.STT_EMPTY)
    assert rig.states()[-1] == ("IDLE", "NO_OUTPUT")


@pytest.mark.parametrize("name,x,status", [
    ("max duration", stream(1400, [(300, 1300)]), S.MAX_DURATION),
    ("stream ended while speaking", stream(500, [(300, 500)]), S.STREAM_ENDED),
])
def test_a_possibly_truncated_utterance_is_never_treated_as_a_complete_command(name, x, status):
    engine = SpyEngine()
    rig = Rig(replies=("hey jarvis what can you do",), answer_engine=engine, context_provider=context(Capability.ANNOUNCEMENTS))
    o = only(rig.drive(x))
    assert o.pipeline_status is status
    _no_answer_assertions(rig, engine, o, K.INCOMPLETE_UTTERANCE)
    assert rig.states()[-1] == ("IDLE", "NO_OUTPUT")


def test_an_activation_outside_the_delivered_audio_is_not_answered():
    engine = SpyEngine()
    rig = Rig(answer_engine=engine)
    o = rig.runtime.handle_result(make_result(S.INVALID_ACTIVATION))
    _no_answer_assertions(rig, engine, o, K.INVALID_ACTIVATION)


# =========================================================================================
# D. errors: expected service failures fall back, bugs propagate
# =========================================================================================

def test_a_typed_stt_failure_requests_the_fixed_fallback_sentence():
    rig = Rig(replies=(SttInferenceFailed("decoder failed"),))
    o = only(rig.drive(UTTERANCE))
    assert (o.kind, o.pipeline_status, o.fallback_reason) == (K.FALLBACK_OUTPUT, S.STT_FAILED, FallbackReason.INTERNAL_SERVICE_FAILURE)
    assert rig.tts.texts == [FALLBACK_TEXT] and o.output.status is OutputStatus.COMPLETED
    assert rig.states()[-1] == ("IDLE", "PLAYBACK_COMPLETED")


@pytest.mark.parametrize("reason", list(FallbackReason))
def test_an_explicit_service_failure_from_the_answer_engine_speaks_the_fallback(reason):
    rig = Rig(answer_engine=SpyEngine(error=ExpectedServiceFailure(reason)))
    o = only(rig.drive(UTTERANCE))
    assert o.kind is K.FALLBACK_OUTPUT and o.fallback_reason is reason and o.output.origin == reason.value
    assert rig.tts.texts == [FALLBACK_TEXT]


@pytest.mark.parametrize("bug", [RuntimeError("engine bug"), TypeError("wrong type"), AnswerContractError("contract broken"), KeyError("k")])
def test_a_programming_bug_in_the_answer_engine_propagates_and_is_not_spoken(bug):
    rig = Rig(answer_engine=SpyEngine(error=bug))
    with pytest.raises(type(bug)):
        rig.drive(UTTERANCE)
    assert rig.tts.texts == [] and rig.playback.calls == 0                  # no spoken apology
    assert rig.state.current_state is AssistantState.IDLE                   # and the runtime is not left stuck in CHECKING
    assert rig.states()[-1] == ("IDLE", "OUTPUT_ERROR")


def test_a_bug_in_the_context_provider_propagates_and_is_not_spoken():
    def bad():
        raise ValueError("provider bug")

    rig = Rig(context_provider=bad)
    with pytest.raises(ValueError):
        rig.drive(UTTERANCE)
    assert rig.tts.texts == [] and rig.state.current_state is AssistantState.IDLE


def test_a_contract_violation_below_the_runtime_propagates():
    rig = Rig(playback=RuntimePlayback(bug=TypeError("playback returned the wrong thing")))
    with pytest.raises(TypeError):
        rig.drive(UTTERANCE)
    assert rig.state.current_state is AssistantState.IDLE and rig.runtime.arbiter.phase.value == "IDLE"


def test_the_runtime_recovers_after_a_bug_and_serves_the_next_question():
    rig = Rig(answer_engine=SpyEngine(error=RuntimeError("once")))
    with pytest.raises(RuntimeError):
        rig.drive(UTTERANCE)
    rig.runtime.restart_stream()
    rig.runtime._engine.error = None
    o = only(rig.drive(UTTERANCE))
    assert o.kind is K.ANSWER_OUTPUT and o.output.status is OutputStatus.COMPLETED
    assert rig.states()[-4:] == [("LISTENING", "SPEECH_INPUT_ACTIVATED"), ("CHECKING", "UTTERANCE_CAPTURED"), ("SPEAKING", "PLAYBACK_STARTED"),
                                 ("IDLE", "PLAYBACK_COMPLETED")]


def test_a_tts_failure_is_reported_not_apologised_for_and_the_runtime_recovers():
    tts = RuntimeTts(error=TtsSynthesisFailed("voice broke"))
    rig = Rig(tts=tts)
    o = only(rig.drive(UTTERANCE))
    assert (o.kind, o.output.status) == (K.ANSWER_OUTPUT, OutputStatus.TTS_FAILED) and rig.playback.calls == 0
    assert rig.states()[-1] == ("IDLE", "TTS_FAILED")
    tts.error = None
    rig.runtime.restart_stream()
    assert only(rig.drive(UTTERANCE)).output.status is OutputStatus.COMPLETED


def test_a_playback_failure_that_never_reached_the_first_buffer_never_enters_speaking():
    rig = Rig(playback=RuntimePlayback(fail_before=PlaybackFailed("device vanished")))
    o = only(rig.drive(UTTERANCE))
    assert o.output.status is OutputStatus.PLAYBACK_FAILED
    assert "SPEAKING" not in [s for s, _ in rig.states()] and rig.states()[-1] == ("IDLE", "PLAYBACK_FAILED")


# =========================================================================================
# E. arbitration: rejections, no concurrent playback, the first-buffer rule
# =========================================================================================

def _run_in_thread(fn):
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:                  # pragma: no cover - surfaced by the test
            box["error"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t, box


def test_a_second_request_while_the_speaker_is_busy_is_rejected_without_tts_or_playback():
    gate = threading.Event()
    rig = Rig(tts=RuntimeTts(gate=gate))
    a, box_a = _run_in_thread(lambda: rig.runtime.handle_result(make_result(S.TRANSCRIBED, "what can you do")))
    assert rig.tts.entered.wait(GUARD_S)                      # A owns the speaker and is inside synthesis
    state_before = rig.state.current_event
    b = rig.runtime.handle_result(make_result(S.TRANSCRIBED, "what can you do"))
    assert b.kind is K.ANSWER_REJECTED and b.arbitration.decision is Decision.REJECTED_ACTIVE_OUTPUT and b.output is None
    assert len(rig.tts.texts) == 1 and rig.playback.calls == 0                            # B was neither synthesized nor played
    assert rig.state.current_event == state_before                                          # and B did not disturb A's state
    gate.set()
    a.join(GUARD_S)
    assert box_a["value"].output.status is OutputStatus.COMPLETED
    assert rig.playback.max_concurrent == 1 and len(rig.tts.texts) == 1


def test_a_question_during_an_announcement_gets_retry_required_and_leaves_the_state_alone():
    rig = Rig(playback=RuntimePlayback(hold=True))
    ann = AnnouncementRequest("ann-1", "Attention please.", AnnouncementSource.MANUAL_TEST)
    a, box_a = _run_in_thread(lambda: rig.runtime.arbiter.submit_announcement(ann))
    assert rig.playback.first_sent.wait(GUARD_S)
    assert rig.state.current_state is AssistantState.SPEAKING
    b = rig.runtime.handle_result(make_result(S.TRANSCRIBED, "what can you do"))
    assert b.kind is K.ANSWER_REJECTED and b.arbitration.decision is Decision.RETRY_REQUIRED
    assert rig.state.current_state is AssistantState.SPEAKING and rig.tts.texts == ["Attention please."]
    rig.playback.release.set()
    a.join(GUARD_S)
    assert rig.state.current_state is AssistantState.IDLE


def test_a_wake_during_speech_is_counted_not_forced_into_the_state_machine():
    rig = Rig(playback=RuntimePlayback(hold=True))
    ann = AnnouncementRequest("ann-2", "Attention please.", AnnouncementSource.MANUAL_TEST)
    a, _ = _run_in_thread(lambda: rig.runtime.arbiter.submit_announcement(ann))
    assert rig.playback.first_sent.wait(GUARD_S)
    rig.runtime._on_wake(None)                                  # the detector fires while the announcement is speaking
    assert rig.state.current_state is AssistantState.SPEAKING and rig.runtime.input_state_skipped_count == 1
    rig.playback.release.set()
    a.join(GUARD_S)
    assert rig.state.current_state is AssistantState.IDLE and rig.runtime.input_state_error_count == 0


def test_two_simultaneous_questions_never_play_concurrently():
    gate = threading.Event()
    rig = Rig(tts=RuntimeTts(gate=gate))
    barrier = threading.Barrier(2)
    results, first_back = [], threading.Event()

    def ask():
        barrier.wait(GUARD_S)
        results.append(rig.runtime.handle_result(make_result(S.TRANSCRIBED, "what can you do")))
        first_back.set()

    threads = [threading.Thread(target=ask, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    assert rig.tts.entered.wait(GUARD_S)
    assert first_back.wait(GUARD_S)                             # the loser returns at once; the winner waits at the TTS gate
    gate.set()
    for t in threads:
        t.join(GUARD_S)
    kinds = sorted(o.kind.value for o in results)
    assert kinds == ["ANSWER_OUTPUT", "ANSWER_REJECTED"] and rig.playback.max_concurrent == 1 and rig.playback.calls == 1


def test_speaking_starts_at_the_first_buffer_not_when_tts_finishes():
    hold = threading.Event()
    rig = Rig(playback=RuntimePlayback(before_first=hold))
    t, box = _run_in_thread(lambda: rig.drive(UTTERANCE))
    assert rig.playback.called.wait(GUARD_S)                    # synthesis is finished and playback has been called ...
    assert rig.tts.texts and rig.state.current_state is AssistantState.CHECKING      # ... but nothing reached the device yet
    assert "SPEAKING" not in [s for s, _ in rig.states()]
    hold.set()
    t.join(GUARD_S)
    assert [s for s, _ in rig.states()][-2:] == ["SPEAKING", "IDLE"]


def test_a_playback_engine_without_the_first_buffer_signal_is_refused_at_construction():
    class NoSignal(RuntimePlayback):
        supports_first_buffer_listener = False

    with pytest.raises(ArbiterRequestError):
        Rig(playback=NoSignal())


# =========================================================================================
# F. stop and cancel
# =========================================================================================

def test_stop_during_playback_ends_speaking_with_a_stopped_result():
    rig = Rig(playback=RuntimePlayback(hold=True))
    t, box = _run_in_thread(lambda: rig.drive(UTTERANCE))
    assert rig.playback.first_sent.wait(GUARD_S)
    assert rig.runtime.stop_output() is True
    t.join(GUARD_S)
    (o,) = box["value"]
    assert (o.kind, o.output.status) == (K.ANSWER_OUTPUT, OutputStatus.STOPPED)
    assert rig.states()[-2:] == [("SPEAKING", "PLAYBACK_STARTED"), ("IDLE", "PLAYBACK_STOPPED")]
    assert rig.runtime.stop_output() is False                   # idle: nothing to stop


def test_stop_during_synthesis_never_starts_playback():
    gate = threading.Event()
    rig = Rig(tts=RuntimeTts(gate=gate))
    t, box = _run_in_thread(lambda: rig.drive(UTTERANCE))
    assert rig.tts.entered.wait(GUARD_S)
    assert rig.runtime.stop_output() is True
    gate.set()
    t.join(GUARD_S)
    (o,) = box["value"]
    assert o.output.status is OutputStatus.STOPPED and rig.playback.calls == 0
    assert rig.states()[-1] == ("IDLE", "OUTPUT_STOPPED_BEFORE_PLAYBACK")


def test_cancelling_a_capture_abandons_the_utterance_without_transcription():
    rig = Rig()
    for lo in range(0, 480, 40):                                # the wake fired at 360 and speech is under way
        assert rig.runtime.feed(UTTERANCE[lo:lo + 40]) == []
    assert rig.runtime.pipeline.is_capturing and rig.state.current_state is AssistantState.LISTENING
    assert rig.runtime.cancel_capture() is True
    assert rig.state.current_state is AssistantState.IDLE and rig.states()[-1] == ("IDLE", "CAPTURE_CANCELLED")
    assert rig.stt.calls == [] and not rig.runtime.pipeline.is_capturing
    assert rig.runtime.cancel_capture() is False                # nothing to cancel any more
    assert only(rig.drive(UTTERANCE)).kind is K.ANSWER_OUTPUT   # and the next utterance is served normally


def test_restarting_the_stream_ends_listening():
    rig = Rig()
    for lo in range(0, 440, 40):
        rig.runtime.feed(UTTERANCE[lo:lo + 40])
    assert rig.state.current_state is AssistantState.LISTENING
    rig.runtime.restart_stream()
    assert rig.states()[-1] == ("IDLE", "CAPTURE_CANCELLED") and rig.stt.calls == []


# =========================================================================================
# G. privacy
# =========================================================================================

def test_no_transcript_or_answer_text_reaches_a_log_an_outcome_or_a_state_event(logs):
    rig = Rig(replies=(f"hey jarvis {SECRET}",))
    outcomes = rig.drive(UTTERANCE)
    blob = repr(outcomes) + repr(rig.events) + "\n".join(logs)
    assert SECRET not in blob and "marmalade" not in blob
    assert NOT_SUPPORTED_TEXT not in blob and "intent_routed" in blob           # the spoken text is not logged either; structure is
    assert any("event=runtime_outcome" in line for line in logs)


def test_text_leaves_the_runtime_only_through_the_explicit_observer():
    seen = []
    rig = Rig(replies=(f"hey jarvis {SECRET}",), text_observer=lambda kind, text: seen.append((kind, text)))
    rig.drive(UTTERANCE)
    assert seen == [(TEXT_TRANSCRIPT, SECRET), (TEXT_ANSWER, NOT_SUPPORTED_TEXT)]


def test_no_pcm_is_held_by_any_outcome_object():
    rig = Rig()
    (o,) = rig.drive(UTTERANCE)
    for obj in (o, o.arbitration, o.output):
        for field in dataclasses.fields(obj):
            assert not isinstance(getattr(obj, field.name), np.ndarray), field.name
    assert "array(" not in repr(o)


# =========================================================================================
# H. determinism and the static shape of the module
# =========================================================================================

def test_the_same_fake_input_gives_the_same_outcomes_and_state_sequence():
    def once():
        rig = Rig(context_provider=context(*ROOM))
        o = rig.drive(UTTERANCE)
        key = [(x.kind, x.pipeline_status, x.answer_status, x.intent, x.output.status) for x in o]
        return key, rig.states(), rig.tts.texts

    assert once() == once()


def test_constructor_dependencies_are_type_checked():
    rig = Rig()
    base = dict(detector=rig.detector, segmenter=rig.segmenter, stt=rig.stt, tts=rig.tts, playback=rig.playback)
    for name in base:
        with pytest.raises(RuntimeConfigError):
            VoiceAssistantRuntime(**{**base, name: object()})
    with pytest.raises(RuntimeConfigError):
        VoiceAssistantRuntime(**base, answer_engine=object())
    with pytest.raises(RuntimeConfigError):
        VoiceAssistantRuntime(**base, context_provider=5)


def test_the_runtime_module_builds_no_model_engine_and_imports_no_network_or_device_library():
    tree = ast.parse((REPO / "assistant" / "runtime.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"sounddevice", "socket", "urllib", "requests", "http", "openwakeword", "faster_whisper", "sherpa_onnx", "onnxruntime",
                 "speech.faster_whisper_backend", "speech.sherpa_onnx_tts", "audio.silero_vad", "audio.microphone",
                 "wakeword.openwakeword_backend"}
    assert not (imported & forbidden), imported & forbidden


def test_the_frozen_pipeline_still_sees_the_real_detector_clock_through_the_wrapper():
    rig = Rig()
    rig.drive(UTTERANCE)
    assert rig.runtime.pipeline.master_samples == 1000 and rig.detector.samples_processed == 1000


# =========================================================================================
# I. composition with the Stage B capture harness (fake block source; the harness drives the runtime)
# =========================================================================================

def _harness_for(rig, blocks_or_script, outcomes, **kw):
    from scripts.ai2_stage_b_live import Engines, LiveConfig, StageBLiveHarness
    from tests.stage_b_support import FakeBlockSource
    from tests.voice_synthetic import SR
    src = FakeBlockSource(blocks_or_script)
    cfg = LiveConfig(read_timeout_s=0.01, max_consecutive_timeouts=10 ** 6, processing_queue_blocks=400, join_timeout_s=GUARD_S)
    h = StageBLiveHarness(src, Engines(rig.detector, rig.vad, rig.stt), config=cfg, sample_rate=SR, pipeline=rig.runtime.pipeline,
                          on_result=lambda result, entry: outcomes.append(rig.runtime.handle_result(result)),
                          on_discontinuity=rig.runtime.on_capture_abandoned, **kw)
    return h, src


def test_the_stage_b_harness_drives_the_runtime_from_blocks_to_a_spoken_answer():
    from tests.stage_b_support import make_blocks
    rig = Rig(context_provider=context(Capability.ANNOUNCEMENTS))
    outcomes = []
    h, _ = _harness_for(rig, make_blocks(UTTERANCE), outcomes)
    report = h.run()
    assert report["completed"] and report["continuity"]["exact_continuity"] and h.pipeline is rig.runtime.pipeline
    (o,) = outcomes
    assert o.kind is K.ANSWER_OUTPUT and o.output.status is OutputStatus.COMPLETED
    assert [s for s, _ in rig.states()] == ["IDLE", "LISTENING", "CHECKING", "SPEAKING", "IDLE"]
    assert "I can play announcements" in rig.tts.texts[0]


def test_a_capture_gap_ends_listening_and_the_damaged_utterance_is_never_transcribed_or_answered():
    from tests.stage_b_support import make_blocks
    y = stream(1400, [(300, 600), (780, 1080)])
    rig = Rig()
    outcomes = []
    h, _ = _harness_for(rig, make_blocks(y, drop={11}), outcomes)
    report = h.run()
    assert report["continuity"]["discontinuity_count"] == 1 and report["continuity"]["discarded_partial_utterances"] == 1
    assert len(rig.stt.calls) == 1 and np.array_equal(rig.stt.calls[0], y[760:1080])
    assert [o.kind for o in outcomes] == [K.ANSWER_OUTPUT]
    assert rig.states()[:4] == [("IDLE", "INITIAL"), ("LISTENING", "SPEECH_INPUT_ACTIVATED"), ("IDLE", "CAPTURE_CANCELLED"),
                                ("LISTENING", "SPEECH_INPUT_ACTIVATED")]
    assert rig.states()[-1] == ("IDLE", "PLAYBACK_COMPLETED")


def test_a_bug_in_the_answer_engine_fails_the_harness_run_instead_of_being_swallowed():
    from scripts.ai2_stage_b_live import StageBWorkerError
    from tests.stage_b_support import make_blocks
    rig = Rig(answer_engine=SpyEngine(error=RuntimeError("engine bug")))
    h, _ = _harness_for(rig, make_blocks(UTTERANCE), [])
    with pytest.raises(StageBWorkerError) as info:
        h.run(GUARD_S)
    assert info.value.report["run_error"] == {"stage": "processing", "type": "RuntimeError"}
    assert rig.tts.texts == [] and rig.state.current_state is AssistantState.IDLE


def test_playback_in_the_processing_thread_does_not_stop_the_capture_worker_and_loss_is_reported():
    """While the answer is spoken the harness's processing worker is busy; capture keeps reading and a full queue is reported, not hidden."""
    from tests.stage_b_support import Hold, make_blocks
    gate = threading.Event()
    rig = Rig(tts=RuntimeTts(gate=gate))
    x = stream(1000 + 30 * 40, [(300, 600)])
    blocks = make_blocks(x)
    outcomes = []
    from scripts.ai2_stage_b_live import Engines, LiveConfig, StageBLiveHarness
    from tests.stage_b_support import FakeBlockSource
    from tests.voice_synthetic import SR
    src = FakeBlockSource(blocks[:16] + [Hold(lambda: rig.tts.entered.wait(GUARD_S))] + blocks[16:])
    h = StageBLiveHarness(src, Engines(rig.detector, rig.vad, rig.stt), sample_rate=SR, pipeline=rig.runtime.pipeline,
                          config=LiveConfig(read_timeout_s=0.01, max_consecutive_timeouts=10 ** 6, processing_queue_blocks=20, join_timeout_s=GUARD_S),
                          on_result=lambda result, entry: outcomes.append(rig.runtime.handle_result(result)))
    h.start()
    try:
        assert src.all_read.wait(GUARD_S)                       # every block was read while the runtime was still inside TTS
        assert rig.state.current_state is AssistantState.CHECKING
    finally:
        gate.set()
    report = h.stop()
    assert report["continuity"]["harness_blocks_dropped"] == len(blocks) - 16 - 20 and report["continuity"]["exact_continuity"] is False
    assert [o.kind for o in outcomes] == [K.ANSWER_OUTPUT]


# =========================================================================================
# J. the executable software demo (scripts/vesta_runtime_demo.py)
# =========================================================================================

def _demo(*args, **kw):
    import io
    from scripts import vesta_runtime_demo as demo
    out, err = io.StringIO(), io.StringIO()
    code = demo.main(list(args), out=out, err=err, **kw)
    return code, out.getvalue(), err.getvalue()


def test_the_demo_runs_four_scripted_interactions_and_labels_itself_honestly():
    code, out, err = _demo()
    assert code == 0 and err == ""
    assert out.count("outcome=ANSWER_OUTPUT") == 2 and out.count("outcome=COMMAND_NOT_EXECUTED") == 1 and out.count("outcome=REQUEST_REJECTED") == 1
    assert "RESULT: all scripted interactions behaved as expected" in out
    assert "SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT" in out and "REAL LIVE VOICE ASSISTANT: NOT VALIDATED" in out
    assert out.count("LISTENING(RED) -> CHECKING(BLUE) -> SPEAKING(GREEN) -> IDLE") == 4
    assert "command=TURN_ON/CLASSROOM_LIGHTS execution=NOT_EXECUTED confirmation_allowed=False" in out         # the command is handed back, never executed
    assert out.count("execution=NOT_EXECUTED") == 4 and "confirmation_allowed=True" not in out
    assert "answer=INFORMATION_UNAVAILABLE intent=ROOM_PEOPLE_COUNT" in out            # room information is unavailable, not invented
    assert "I don't have a current people count" not in out                              # text is hidden by default


def test_the_demo_prints_the_scripted_text_only_when_asked():
    code, out, err = _demo("--show-text")
    assert code == 0 and "answer: I don't have a current people count." in out and "transcript: turn on the projector" in out
    assert "answer: Sorry, I can't help with that yet." in out


def test_the_demo_is_deterministic():
    assert _demo()[1] == _demo()[1]


def test_the_demo_stops_clearly_when_the_real_voice_cannot_be_loaded(monkeypatch):
    from speech import sherpa_onnx_tts

    class Missing:
        def __init__(self, *a, **k):
            raise FileNotFoundError("no voice here")

    monkeypatch.setattr(sherpa_onnx_tts, "SherpaOnnxTtsEngine", Missing)
    code, out, err = _demo("--tts", "real")
    assert code == 3 and "nothing was substituted" in err and "interaction" not in out


def test_the_demo_with_the_real_local_voice_and_fake_playback():
    """Synthesis only; skips when the voice files are not on this machine. Plays nothing."""
    from speech.tts import TtsError
    try:
        from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
        voice = SherpaOnnxTtsEngine()
    except (TtsError, OSError) as exc:
        pytest.skip(f"local voice not available ({type(exc).__name__})")
    code, out, err = _demo(tts=voice)
    assert code == 0 and out.count("output=COMPLETED") == 4


# =========================================================================================
# K. lifecycle timing: CHECKING starts when the utterance reaches STT, LISTENING when the wake fires
# =========================================================================================

def test_checking_starts_when_the_utterance_is_handed_to_stt_not_after_it_returns():
    release, entered = threading.Event(), threading.Event()

    class HeldStt(ScriptedStt):
        def _transcribe(self, samples):
            entered.set()
            assert release.wait(GUARD_S), "STT never released"
            return super()._transcribe(samples)

    rig = Rig()
    rig.stt = HeldStt("hey jarvis what can you do")
    rig.runtime = VoiceAssistantRuntime(detector=rig.detector, segmenter=rig.segmenter, stt=rig.stt, tts=rig.tts, playback=rig.playback,
                                        state=rig.state)
    t, box = _run_in_thread(lambda: rig.drive(UTTERANCE))
    assert entered.wait(GUARD_S)                                # the segmenter finished and STT is running
    assert rig.state.current_state is AssistantState.CHECKING and rig.tts.texts == []
    release.set()
    t.join(GUARD_S)
    assert [s for s, _ in rig.states()] == ["IDLE", "LISTENING", "CHECKING", "SPEAKING", "IDLE"]


def test_listening_starts_at_the_wake_detection_and_not_before():
    rig = Rig()
    for lo in range(0, 320, 40):                                # chunks 0-7: the wake has not fired yet
        rig.runtime.feed(UTTERANCE[lo:lo + 40])
    assert rig.state.current_state is AssistantState.IDLE and rig.states() == [("IDLE", "INITIAL")]
    rig.runtime.feed(UTTERANCE[320:360])                        # chunk 8 ends at 360: the detector fires
    assert rig.state.current_state is AssistantState.LISTENING


def test_the_outcome_label_is_software_validated_with_fake_input_and_never_hardware():
    from assistant.runtime import RUNTIME_VALIDATION_LABEL
    (o,) = Rig().drive(UTTERANCE)
    assert o.validation_label == RUNTIME_VALIDATION_LABEL == "SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT"
    assert "HARDWARE" not in o.validation_label


def test_a_transition_the_state_table_refuses_is_counted_not_forced_and_never_raised():
    rig = Rig()
    rig.state.transition(AssistantState.CHECKING, StateReason.OUTPUT_ADMITTED)          # something else put the state in CHECKING
    rig.runtime._on_wake(None)                                                           # CHECKING -> LISTENING is not an allowed transition
    assert rig.runtime.input_state_error_count == 1 and rig.state.current_state is AssistantState.CHECKING


def test_every_interaction_gets_its_own_request_id_visible_in_the_state_events():
    rig = Rig()
    rig.drive(UTTERANCE)
    rig.runtime.restart_stream()
    rig.drive(UTTERANCE)
    ids = [e.request_id for e in rig.events if e.state is AssistantState.SPEAKING]
    # Week 9 Task 1C: ids come from the runtime's RequestIdGenerator (random session, counter from 1); production ids stay random
    assert len(ids) == 2 and [i.rsplit("-", 1)[1] for i in ids] == ["1", "2"] and len({i.rsplit("-", 1)[0] for i in ids}) == 1
    assert all(re.fullmatch(r"c-[0-9a-f]{12}-[0-9]+", i) for i in ids)
