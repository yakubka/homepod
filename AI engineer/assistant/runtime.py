"""VESTA voice-assistant runtime: the ONE minimal software composition root (Team Leader end-to-end composition, SOFTWARE ONLY).

    audio blocks -> VoicePipeline (wake -> VAD -> STT -> normalised text)            [speech/pipeline.py, frozen]
        TRANSCRIBED -> intent router (ONE owner: intents.router.route_intent)           [intents/, frozen]
            query, ANSWERING_FOUNDATION -> DeterministicAnswerEngine via answer_query()  [assistant/answering.py, frozen]
            validated command           -> returned as a StructuredCommand, NOT_EXECUTED, never executed
            anything else               -> an explicit rejection / "not answerable" outcome
        every spoken reply -> OutputArbiter (one speaker at a time) -> TTS -> playback   [assistant/output_arbiter.py, frozen]
    one AssistantStateManager: LISTENING (RED) -> CHECKING (BLUE) -> SPEAKING (GREEN) -> IDLE   [assistant/state.py, frozen]

This module only WIRES those parts together. It contains no STT, TTS, VAD, wake-word, answering or arbitration logic of its own, calls
no LLM, no cloud service and no network, reads no sensor and invents no schedule or professor-arrival source. Everything is injected, so
the whole chain runs in tests with fake wake / VAD / STT / TTS / playback and never needs a microphone or a loudspeaker.

WHAT EACH PIPELINE OUTCOME DOES (nothing is guessed or invented)
    TRANSCRIBED                    a complete utterance -> the router -> (see "ROUTING" below) -> a spoken reply through the arbiter
    WAKE_ONLY                      the user said only the wake phrase: NOT a question; nothing is answered or spoken
    NO_SPEECH / TOO_SHORT          nothing usable was said: nothing is answered or spoken
    STT_EMPTY                      the decoder returned no text: nothing is answered or spoken
    MAX_DURATION / STREAM_ENDED    the text may be TRUNCATED: it is never treated as a complete command (not answered, not spoken)
    INVALID_ACTIVATION             the wake sample was outside the delivered audio: nothing is answered or spoken
    STT_FAILED                     a typed STT failure (an explicit, expected failure): the fixed offline fallback sentence is requested
Only an ExpectedServiceFailure raised by the answer engine (or a typed STT failure) leads to the spoken fallback. Any other exception, a
TypeError, an AnswerContractError, a contract violation in TTS or playback, is a software bug: the state is returned to IDLE and the
exception PROPAGATES. It is never turned into a spoken apology. If the arbiter does not grant a request, nothing is synthesized or played and
the actual ArbitrationResult is returned.

ROUTING (Week 8 Task 3). Every complete transcript goes through the router exactly once, whatever it says; the runtime has no second parser.
    SENSOR_QUERY / OCCUPANCY_QUERY / GENERAL_AI (Q5-Q10)   handling ANSWERING_FOUNDATION: answered by the frozen DeterministicAnswerEngine through
                                                           answer_query(), so freshness, units, provenance, missing-data and capability rules apply unchanged
    LOCAL_COMMAND / CLASSROOM_CONTROL / SMART_HOME         a validated StructuredCommand is RETURNED in the outcome with execution_status NOT_EXECUTED and
                                                           spoken_confirmation_allowed False. With NO action_loop (the default) nothing is executed, no device or
                                                           ESP32 interface exists here, and the only thing spoken is a fixed notice that the command was NOT carried out.
                                                           With an INJECTED action_loop (Week 9 Task 1C, SIMULATED ONLY) see "SIMULATED ACTIONS" below
    open-ended GENERAL_AI, "is the room dark", "is anyone in the room"   classified but not answerable (NO_BACKEND_APPROVED / ANSWER_NOT_IMPLEMENTED):
                                                           a fixed "can't help yet" reply; nothing is invented and no model is called
    UNSUPPORTED / AMBIGUOUS / INVALID_COMMAND / INVALID_INPUT   the router's outcome is kept as it is (never reinterpreted as GENERAL_AI) and a fixed
                                                           reply says so; no StructuredCommand exists for any of them
The fixed replies below never echo the request and never claim that anything happened. A router or answering exception that is not an
ExpectedServiceFailure is a bug and propagates.

SIMULATED ACTIONS (Week 9 Task 1C; PROVISIONAL / SIMULATED; team decisions D1-D12 OPEN). The runtime never builds an adapter: an ActionLoop
(actions/loop.py) is injected already configured, or there is none. With one, a routed command is admitted only when the arbiter is IDLE and
no other command is in flight; otherwise it is NOT admitted (OutcomeKind COMMAND_NOT_ADMITTED): nothing is submitted, nothing is spoken and
the state is left to its owner (fail closed, no queue). An admitted command runs the loop synchronously INSIDE CHECKING (bounded by the
loop's own timeout), and the actions response policy chooses the reply, spoken through the ONE arbiter like every other reply. Only a
correlated APPLIED_CONFIRMED with origin SIMULATED is described as done, and then as "In the simulation, ...". The Week 8 fields
execution_status (NOT_EXECUTED) and spoken_confirmation_allowed (False) keep their intent-layer meaning; the simulated result is in `action`.
KNOWN LIMITS: stop_output() during the loop does not cancel it or its reply (no cancellation contract exists); if the speaker becomes busy
while the loop waits, the simulated action has happened but its reply is rejected by the arbiter (see `arbitration`); a live capture
harness would build an audio backlog while the loop blocks.

REQUEST IDS. One id per complete transcript from an injected RequestIdGenerator ('c-<random session>-<n>'; a new random session per runtime
unless a generator is injected), used for the intent request, the command, the action result, the arbiter request and the state events.
The random session makes reuse across restarts improbable (about N*N / 2**49 over N sessions), not impossible.

STATE LIFECYCLE (emitted only from lifecycle events the software really observes)
    LISTENING  when the wake detector reports a detection (the pipeline starts collecting the utterance)    [wrapper around the detector]
    CHECKING   when the pipeline hands the finished utterance to STT; routing and answering happen inside CHECKING [wrapper around the STT engine]
    SPEAKING   when the playback engine reports that its first buffer reached the device                       [OutputArbiter]
    IDLE       when playback ends, or when the interaction ends without speaking
The two wrappers only DELEGATE and notify; the frozen modules are untouched. A transition the state table refuses (for example a wake
detection while an announcement is speaking: barge-in is not supported) is counted and logged, never forced. When the arbiter owns the
speaker, the input side leaves the state alone. If a transition was not observed (a wake and its decision inside one feed() call), the
missing state is simply not emitted; it is never made up afterwards.

KNOWN LIMITATIONS (documented gaps, not hidden)
  * Half-duplex is NOT enforced: nothing here mutes the capture path while the assistant speaks (no echo cancellation exists in software), so
    audio captured during playback is processed afterwards. With the Stage B harness the processing worker is busy while handle_result()
    speaks, so that audio waits in the harness queue (see the queue notes in scripts/ai2_stage_b_live.py).
  * LISTENING is entered at the wake detection; the interval from there to the end of the utterance is the segmenter's, with no finer event.
  * Wake detection is the only activation path here (no push-to-talk); the manual development detector of the Stage B harness works as a detector.
  * The classroom state and the capability declaration come from the injected context_provider; the default declares NOTHING, so every
    question is answered "can't answer yet". There is no live sensor source, schedule or professor-arrival source in this repository.

STATUS: SOFTWARE-VALIDATED WITH FAKE INPUT / OUTPUT. LIVE VOICE ASSISTANT: NOT VALIDATED (no microphone, speaker, wake accuracy or latency
claim). Privacy: only structural fields are logged; transcripts and answer texts reach an optional explicit text_observer, nothing else.
"""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

import numpy as np

from actions.contracts import RequestIdGenerator
from actions.loop import ActionLoop, ActionOutcome
from actions.response_policy import respond as action_response
from assistant.answering import (
    AnswerContext, AnswerEngine, AnswerRequest, AnswerResult, DeterministicAnswerEngine,
)
from assistant.output_arbiter import ArbitrationOutcome, ArbitrationResult, OutputArbiter, OutputPhase
from assistant.spoken_output import FallbackReason, SpokenOutputResult
from assistant.state import AssistantState, AssistantStateManager, StateError, StateReason
from audio.playback import PlaybackEngine
from intents.contracts import COMMAND_CATEGORIES, Handling, IntentRequest, IntentResult, RoutingStatus, StructuredCommand
from intents.router import answer_query, route_intent
from audio.utterance import UtteranceSegmenter
from config.logging_config import setup_logging
from speech.pipeline import PipelineResult, PipelineStatus, VoicePipeline
from speech.stt import SttEngine
from speech.tts import TtsEngine, check_text
from wakeword.detector import Detection, WakeWordDetector

logger = setup_logging()

RUNTIME_VALIDATION_LABEL = "SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT"
TEXT_TRANSCRIPT, TEXT_ANSWER = "TRANSCRIPT", "ANSWER"

# Fixed spoken replies for requests the answering engine does not answer. Reviewed text: none echoes the request, none says that anything was done.
COMMAND_NOT_EXECUTED_TEXT = "I understood that request, but I can't carry out device commands yet."
NOT_SUPPORTED_TEXT = "Sorry, I can't help with that yet."
AMBIGUOUS_TEXT = "Sorry, that could mean more than one thing. Please be more specific."
INVALID_COMMAND_TEXT = "Sorry, I didn't understand the value in that request."
INVALID_INPUT_TEXT = "Sorry, I didn't catch that."
FIXED_REPLIES = (COMMAND_NOT_EXECUTED_TEXT, NOT_SUPPORTED_TEXT, AMBIGUOUS_TEXT, INVALID_COMMAND_TEXT, INVALID_INPUT_TEXT)
for _reply in FIXED_REPLIES:
    check_text(_reply)                  # a reply that could not be synthesized would be worse than none: fail at import


class AssistantRuntimeError(Exception):
    """Base class for runtime problems that are the caller's fault (never a spoken failure)."""


class RuntimeConfigError(AssistantRuntimeError, ValueError):
    """A dependency has the wrong type."""


class ExpectedServiceFailure(Exception):
    """RAISE THIS from an answer engine for an explicit, EXPECTED upstream failure (a service that is down, a timeout, no network).
    It is the ONLY engine exception the runtime answers with the spoken offline fallback; anything else is treated as a bug."""

    def __init__(self, reason: FallbackReason) -> None:
        if not isinstance(reason, FallbackReason):
            raise TypeError("reason must be a FallbackReason")
        super().__init__(reason.value)
        self.reason = reason


class OutcomeKind(Enum):
    NO_SPEECH = "NO_SPEECH"                          # NO_SPEECH, TOO_SHORT: nothing usable was said
    WAKE_ONLY = "WAKE_ONLY"                          # only the wake phrase: not a question
    STT_EMPTY = "STT_EMPTY"                          # the decoder produced no text
    INVALID_ACTIVATION = "INVALID_ACTIVATION"
    INCOMPLETE_UTTERANCE = "INCOMPLETE_UTTERANCE"    # MAX_DURATION / STREAM_ENDED: possibly truncated, never answered
    ANSWER_OUTPUT = "ANSWER_OUTPUT"                  # the answer was granted the speaker and ran (see `output` for its status)
    ANSWER_REJECTED = "ANSWER_REJECTED"              # the arbiter did not grant it: nothing was synthesized or played
    FALLBACK_OUTPUT = "FALLBACK_OUTPUT"
    FALLBACK_REJECTED = "FALLBACK_REJECTED"
    COMMAND_NOT_EXECUTED = "COMMAND_NOT_EXECUTED"    # a validated StructuredCommand was returned; nothing was executed (see arbitration / output for the notice)
    COMMAND_SIMULATED = "COMMAND_SIMULATED"          # Week 9: the injected SIMULATED action loop ran (or refused) the command; see `action`
    COMMAND_NOT_ADMITTED = "COMMAND_NOT_ADMITTED"    # Week 9: output busy or another command in flight: NOT submitted, nothing spoken
    NOT_ANSWERABLE = "NOT_ANSWERABLE"                # routed, but no answer exists (NO_BACKEND_APPROVED / ANSWER_NOT_IMPLEMENTED)
    REQUEST_REJECTED = "REQUEST_REJECTED"            # the router said UNSUPPORTED / AMBIGUOUS / INVALID_COMMAND / INVALID_INPUT


@dataclass(frozen=True)
class RuntimeOutcome:
    """What one pipeline result led to. No transcript, no answer text, no PCM."""

    kind: OutcomeKind
    pipeline_status: PipelineStatus
    answer_status: Optional[str] = None
    intent: Optional[str] = None
    question_id: Optional[str] = None
    answer_source: Optional[str] = None
    answer_reason_code: Optional[str] = None
    fallback_reason: Optional[FallbackReason] = None
    arbitration: Optional[ArbitrationResult] = None
    output: Optional[SpokenOutputResult] = None
    answer_ms: Optional[float] = None
    validation_label: str = RUNTIME_VALIDATION_LABEL
    # --- the router's decision (Week 8 Task 3); never the request text ---
    intent_category: Optional[str] = None            # one of the six official names, or None for a rejection
    routing_status: Optional[str] = None             # ROUTED / INVALID_COMMAND / UNSUPPORTED / AMBIGUOUS / INVALID_INPUT
    routing_rule_id: Optional[str] = None
    query_family: Optional[str] = None
    handling: Optional[str] = None
    command: Optional[StructuredCommand] = None      # only for a ROUTED command category: a request, never an execution
    execution_status: str = "NOT_EXECUTED"           # the Week 8 intent-layer status, always: nothing PHYSICAL is executed anywhere
    spoken_confirmation_allowed: bool = False        # the Week 8 intent-layer flag, always False
    # --- Week 9 Task 1C: the SIMULATED action result (only with an injected action loop) ---
    action: Optional[ActionOutcome] = None           # the actions-layer outcome (capability, result state, origin, discards), unchanged
    confirms_simulated_completion: bool = False      # True only when the response policy described a SIMULATED completion


# ---------------------------------------------------------------------------------------------------
# delegating wrappers: the notification points that let the runtime observe real lifecycle events
# ---------------------------------------------------------------------------------------------------

class _NotifyingDetector(WakeWordDetector):
    """Delegates everything to the real detector and tells the runtime when it reports a detection."""

    def __init__(self, inner: WakeWordDetector, on_detection: Callable[[Detection], None]) -> None:
        self._inner, self._on_detection = inner, on_detection
        super().__init__(threshold=inner.threshold, cooldown_s=inner.cooldown_s)

    @property
    def sample_rate(self) -> int:
        return self._inner.sample_rate

    @property
    def chunk_samples(self) -> int:
        return self._inner.chunk_samples

    @property
    def samples_processed(self) -> int:
        return self._inner.samples_processed

    @property
    def chunks_processed(self) -> int:
        return self._inner.chunks_processed

    @property
    def suppressed_detections(self) -> int:
        return self._inner.suppressed_detections

    @property
    def last_scores(self):
        return self._inner.last_scores

    def process(self, chunk: np.ndarray) -> Optional[Detection]:
        detection = self._inner.process(chunk)
        if detection is not None:
            self._on_detection(detection)
        return detection

    def reset(self) -> None:
        self._inner.reset()

    def _score_chunk(self, chunk):                        # never called: process() is delegated
        raise NotImplementedError

    def _reset_backend(self) -> None:                     # never called: reset() is delegated
        raise NotImplementedError


class _NotifyingStt(SttEngine):
    """Delegates to the real STT engine and tells the runtime just before a finished utterance is transcribed."""

    def __init__(self, inner: SttEngine, on_utterance: Callable[[], None]) -> None:
        super().__init__()
        self._inner, self._on_utterance = inner, on_utterance

    @property
    def sample_rate(self) -> int:
        return self._inner.sample_rate

    @property
    def engine_id(self) -> str:
        return self._inner.engine_id

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    def transcribe(self, samples, sample_rate):
        return self._inner.transcribe(samples, sample_rate)

    def transcribe_segment(self, segment):
        self._on_utterance()                              # the utterance is complete and is being handed to STT
        return self._inner.transcribe_segment(segment)

    def _transcribe(self, samples):                       # never called: transcribe() is delegated
        raise NotImplementedError


# ---------------------------------------------------------------------------------------------------
# the runtime
# ---------------------------------------------------------------------------------------------------

class VoiceAssistantRuntime:
    """Owns the VoicePipeline, the answer engine, the OutputArbiter (and with it the one TTS / playback pair) and the one state manager."""

    def __init__(
        self,
        *,
        detector: WakeWordDetector,
        segmenter: UtteranceSegmenter,
        stt: SttEngine,
        tts: TtsEngine,
        playback: PlaybackEngine,
        answer_engine: Optional[AnswerEngine] = None,
        context_provider: Optional[Callable[[], AnswerContext]] = None,
        state: Optional[AssistantStateManager] = None,
        text_observer: Optional[Callable[[str, str], None]] = None,
        clock: Callable[[], float] = time.perf_counter,
        route: Callable[[IntentRequest], IntentResult] = route_intent,
        action_loop: Optional[ActionLoop] = None,
        request_ids: Optional[RequestIdGenerator] = None,
    ) -> None:
        """text_observer(kind, text), kind TRANSCRIPT or ANSWER, is the ONLY way text leaves the runtime; leave None to keep it inside."""
        for name, obj, kind in (("detector", detector, WakeWordDetector), ("segmenter", segmenter, UtteranceSegmenter), ("stt", stt, SttEngine),
                                ("tts", tts, TtsEngine), ("playback", playback, PlaybackEngine)):
            if not isinstance(obj, kind):
                raise RuntimeConfigError(f"{name} must be a {kind.__name__}, got {type(obj).__name__}")
        if answer_engine is not None and not isinstance(answer_engine, AnswerEngine):
            raise RuntimeConfigError("answer_engine must be an AnswerEngine")
        if state is not None and not isinstance(state, AssistantStateManager):
            raise RuntimeConfigError("state must be an AssistantStateManager")
        if context_provider is not None and not callable(context_provider):
            raise RuntimeConfigError("context_provider must be callable")
        if not callable(route):
            raise RuntimeConfigError("route must be callable")
        if action_loop is not None and not isinstance(action_loop, ActionLoop):
            raise RuntimeConfigError("action_loop must be an ActionLoop (actions/loop.py) or None")
        if request_ids is not None and not isinstance(request_ids, RequestIdGenerator):
            raise RuntimeConfigError("request_ids must be a RequestIdGenerator or None")
        self._action_loop = action_loop                  # None = the Week 8 behaviour: commands are returned NOT_EXECUTED
        self._request_ids = request_ids or RequestIdGenerator()     # ONE id source; a new random session per runtime
        self._action_lock = threading.Lock()             # at most one command in the action loop (fail closed, no queue)
        self._route = route                              # the ONE router of this runtime (injectable for tests only)
        self._clock = clock
        self._engine = answer_engine or DeterministicAnswerEngine()
        self._context_provider = context_provider or AnswerContext          # the default context declares NOTHING
        self._observer = text_observer
        self._state = state or AssistantStateManager()
        self._arbiter = OutputArbiter(tts, playback, clock=clock, state=self._state)      # refuses a playback engine without the first-buffer signal
        self._pipeline = VoicePipeline(_NotifyingDetector(detector, self._on_wake), segmenter, _NotifyingStt(stt, self._on_utterance))
        self._lock = threading.Lock()
        self._input_state_errors = 0
        self._input_state_skipped = 0

    # -- inspection ------------------------------------------------------------------------------

    @property
    def pipeline(self) -> VoicePipeline:
        return self._pipeline

    @property
    def arbiter(self) -> OutputArbiter:
        return self._arbiter

    @property
    def state(self) -> AssistantStateManager:
        return self._state

    @property
    def input_state_error_count(self) -> int:
        """Input-side transitions the state table refused (never forced)."""
        with self._lock:
            return self._input_state_errors

    @property
    def input_state_skipped_count(self) -> int:
        """Input-side transitions left alone because the arbiter owned the speaker."""
        with self._lock:
            return self._input_state_skipped

    # -- the audio side --------------------------------------------------------------------------

    def feed(self, block: np.ndarray, start_sample: Optional[int] = None) -> List[RuntimeOutcome]:
        """Feed one audio block; every pipeline result it completes is handled and returned. Call from ONE thread only."""
        return [self.handle_result(r) for r in self._pipeline.feed(block, start_sample)]

    def finish(self) -> List[RuntimeOutcome]:
        """The stream ended: flush the pipeline and handle what it decides."""
        return [self.handle_result(r) for r in self._pipeline.finish()]

    def restart_stream(self) -> None:
        """Begin a new stream. An utterance being collected is abandoned WITHOUT transcription, and LISTENING (if it was entered) ends."""
        self._pipeline.restart_stream()
        self.on_capture_abandoned()

    def cancel_capture(self) -> bool:
        """Abandon an utterance being collected (processing thread only: the pipeline is not thread-safe). True if there was one."""
        if not self._pipeline.is_capturing:
            return False
        self.restart_stream()
        return True

    def on_capture_abandoned(self, *_ignored) -> None:
        """Hook for a stream restart done by someone else (for example the Stage B harness after a discontinuity)."""
        if self._arbiter.phase is OutputPhase.IDLE and self._state.current_state is AssistantState.LISTENING:
            self._try(AssistantState.IDLE, StateReason.CAPTURE_CANCELLED)

    # -- stopping --------------------------------------------------------------------------------

    def stop_output(self) -> bool:
        """Stop the spoken output in progress (thread-safe). The result of that request reports STOPPED."""
        return self._arbiter.stop()

    # -- lifecycle events from the wrappers ----------------------------------------------------------

    def _on_wake(self, detection: Detection) -> None:
        self._try(AssistantState.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)

    def _on_utterance(self) -> None:
        self._try(AssistantState.CHECKING, StateReason.UTTERANCE_CAPTURED)

    def _try(self, target: AssistantState, reason: StateReason) -> None:
        """An input-side transition: skipped while the arbiter owns the speaker, counted (never forced) when the table refuses it."""
        if self._arbiter.phase is not OutputPhase.IDLE:
            with self._lock:
                self._input_state_skipped += 1
            return
        try:
            self._state.transition(target, reason)
        except StateError as exc:
            with self._lock:
                self._input_state_errors += 1
            logger.error("event=runtime_state_error target=%s reason=%s error=%s", target.value, reason.value, type(exc).__name__)

    def _settle(self, listening_reason: StateReason, checking_reason: StateReason) -> None:
        """End an interaction that is not being spoken: LISTENING / CHECKING -> IDLE, only when the arbiter does not own the state."""
        if self._arbiter.phase is not OutputPhase.IDLE:
            return
        current = self._state.current_state
        if current is AssistantState.LISTENING:
            self._try(AssistantState.IDLE, listening_reason)
        elif current is AssistantState.CHECKING:
            self._try(AssistantState.IDLE, checking_reason)

    # -- the result side -----------------------------------------------------------------------------

    def handle_result(self, result: PipelineResult) -> RuntimeOutcome:
        """Turn one PipelineResult into behaviour. See the module docstring for the table."""
        status = result.status
        if status is PipelineStatus.TRANSCRIBED:
            return self._respond(result)
        if status is PipelineStatus.STT_FAILED:
            self._ensure_checking()
            return self._speak_fallback(result, FallbackReason.INTERNAL_SERVICE_FAILURE)
        if status in (PipelineStatus.NO_SPEECH, PipelineStatus.TOO_SHORT):
            kind = OutcomeKind.NO_SPEECH
        elif status is PipelineStatus.INVALID_ACTIVATION:
            kind = OutcomeKind.INVALID_ACTIVATION
        elif status is PipelineStatus.WAKE_ONLY:
            kind = OutcomeKind.WAKE_ONLY
        elif status is PipelineStatus.STT_EMPTY:
            kind = OutcomeKind.STT_EMPTY
        elif status in (PipelineStatus.MAX_DURATION, PipelineStatus.STREAM_ENDED):
            kind = OutcomeKind.INCOMPLETE_UTTERANCE
        else:                                              # a new PipelineStatus must be handled on purpose, not by accident
            raise RuntimeConfigError(f"unhandled pipeline status {status!r}")
        self._settle(StateReason.NO_SPEECH_DETECTED, StateReason.NO_OUTPUT)
        return self._done(RuntimeOutcome(kind, status))

    def _ensure_checking(self) -> None:
        if self._state.current_state in (AssistantState.LISTENING, AssistantState.IDLE):
            self._try(AssistantState.CHECKING, StateReason.UTTERANCE_CAPTURED)

    def _respond(self, result: PipelineResult) -> RuntimeOutcome:
        """A complete transcript: route it ONCE, then handle exactly what the router decided."""
        self._ensure_checking()
        request_id = self._request_ids.next_id()
        self._observe(TEXT_TRANSCRIPT, result.normalized_text)
        try:
            intent = self._route(IntentRequest(result.normalized_text, request_id))
            if not isinstance(intent, IntentResult):
                raise RuntimeConfigError(f"the router returned {type(intent).__name__}, not an IntentResult")
        except BaseException:
            self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_ERROR)         # a router bug is not spoken about
            raise
        meta = dict(pipeline_status=result.status, intent_category=intent.category.value if intent.category else None,
                    routing_status=intent.routing_status.value, routing_rule_id=intent.rule_id, query_family=intent.query_family,
                    handling=intent.handling.value, command=intent.actionable_command, execution_status=intent.execution_status.value,
                    spoken_confirmation_allowed=intent.spoken_confirmation_allowed)
        if intent.routed:
            if intent.handling is Handling.ANSWERING_FOUNDATION:
                return self._answer_query(result, intent, request_id, meta)
            if intent.category in COMMAND_CATEGORIES and intent.handling is Handling.EXECUTION_NOT_IMPLEMENTED:
                if self._action_loop is None:
                    return self._speak_fixed(COMMAND_NOT_EXECUTED_TEXT, request_id, OutcomeKind.COMMAND_NOT_EXECUTED, meta)
                return self._simulate_command(intent, request_id, meta)
            if intent.handling in (Handling.NO_BACKEND_APPROVED, Handling.ANSWER_NOT_IMPLEMENTED):
                return self._speak_fixed(NOT_SUPPORTED_TEXT, request_id, OutcomeKind.NOT_ANSWERABLE, meta)
        else:
            reply = {RoutingStatus.AMBIGUOUS: AMBIGUOUS_TEXT, RoutingStatus.INVALID_COMMAND: INVALID_COMMAND_TEXT,
                     RoutingStatus.INVALID_INPUT: INVALID_INPUT_TEXT, RoutingStatus.UNSUPPORTED: NOT_SUPPORTED_TEXT}[intent.routing_status]
            return self._speak_fixed(reply, request_id, OutcomeKind.REQUEST_REJECTED, meta)
        self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_ERROR)
        raise RuntimeConfigError("the router returned a result this runtime does not handle")     # a new routing result must be handled on purpose

    def _answer_query(self, result: PipelineResult, intent: IntentResult, request_id: str, meta: dict) -> RuntimeOutcome:
        began = self._clock()
        try:
            answer: AnswerResult = answer_query(intent, self._context_provider(), self._engine)
        except ExpectedServiceFailure as exc:
            return self._speak_fallback(result, exc.reason)
        except BaseException:
            self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_ERROR)     # never left stuck in CHECKING; the bug is NOT spoken about
            raise
        answer_ms = (self._clock() - began) * 1000.0
        self._observe(TEXT_ANSWER, answer.text)
        try:
            outcome = self._arbiter.submit_answer(answer.text, request_id=request_id)
        except BaseException:
            self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_ERROR)
            raise
        base = dict(meta, answer_status=answer.status.value, intent=answer.intent.value if answer.intent else None,
                    question_id=answer.question_id, answer_source=answer.source.value, answer_reason_code=answer.reason_code, answer_ms=answer_ms)
        return self._finish_output(outcome, OutcomeKind.ANSWER_OUTPUT, OutcomeKind.ANSWER_REJECTED, base)

    def _simulate_command(self, intent: IntentResult, request_id: str, meta: dict) -> RuntimeOutcome:
        """Week 9 Task 1C, SIMULATED. Admit only when the arbiter is IDLE and no other command is in the loop; otherwise fail closed:
        nothing submitted, nothing spoken, the state left to whoever owns it. A loop exception is a bug: settle and propagate."""
        if self._arbiter.phase is not OutputPhase.IDLE or not self._action_lock.acquire(blocking=False):
            logger.info("event=runtime_command_not_admitted request_id=%s", request_id)
            return self._done(RuntimeOutcome(OutcomeKind.COMMAND_NOT_ADMITTED, **meta))
        try:
            action = self._action_loop.execute(intent.actionable_command)
        except BaseException:
            self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_ERROR)
            raise
        finally:
            self._action_lock.release()
        decision = action_response(action)
        return self._speak_fixed(decision.text, request_id, OutcomeKind.COMMAND_SIMULATED,
                                 dict(meta, action=action, confirms_simulated_completion=decision.confirms_simulated_completion))

    def _speak_fixed(self, text: str, request_id: str, kind: OutcomeKind, meta: dict) -> RuntimeOutcome:
        """Speak one of the fixed replies through the arbiter. The outcome keeps `kind` whether or not the arbiter granted the speaker."""
        self._observe(TEXT_ANSWER, text)
        try:
            outcome = self._arbiter.submit_answer(text, request_id=request_id)
        except BaseException:
            self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_ERROR)
            raise
        return self._finish_output(outcome, kind, kind, dict(meta))

    def _speak_fallback(self, result: PipelineResult, reason: FallbackReason) -> RuntimeOutcome:
        try:
            outcome = self._arbiter.submit_fallback(reason)
        except BaseException:
            self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_ERROR)
            raise
        return self._finish_output(outcome, OutcomeKind.FALLBACK_OUTPUT, OutcomeKind.FALLBACK_REJECTED,
                                   dict(pipeline_status=result.status, fallback_reason=reason))

    def _finish_output(self, outcome: ArbitrationOutcome, granted_kind: OutcomeKind, rejected_kind: OutcomeKind, base: dict) -> RuntimeOutcome:
        if outcome.granted:
            return self._done(RuntimeOutcome(granted_kind, arbitration=outcome.arbitration, output=outcome.output, **base))
        self._settle(StateReason.CAPTURE_CANCELLED, StateReason.OUTPUT_REJECTED)       # nothing was synthesized or played
        return self._done(RuntimeOutcome(rejected_kind, arbitration=outcome.arbitration, **base))

    def _observe(self, kind: str, text: str) -> None:
        if self._observer is not None:
            self._observer(kind, text)

    @staticmethod
    def _done(outcome: RuntimeOutcome) -> RuntimeOutcome:
        logger.log(logging.INFO, "event=runtime_outcome kind=%s pipeline_status=%s category=%s routing=%s handling=%s command=%s action_state=%s "
                   "simulated_confirmation=%s answer_status=%s intent=%s question_id=%s source=%s decision=%s output_status=%s fallback=%s answer_ms=%s",
                   outcome.kind.value, outcome.pipeline_status.value, outcome.intent_category or "-", outcome.routing_status or "-",
                   outcome.handling or "-", outcome.command.action.value if outcome.command else "-",
                   outcome.action.result.state.value if outcome.action and outcome.action.result else
                   (outcome.action.capability.value if outcome.action else "-"), outcome.confirms_simulated_completion,
                   outcome.answer_status or "-", outcome.intent or "-",
                   outcome.question_id or "-", outcome.answer_source or "-",
                   outcome.arbitration.decision.value if outcome.arbitration else "-", outcome.output.status.value if outcome.output else "-",
                   outcome.fallback_reason.value if outcome.fallback_reason else "-",
                   f"{outcome.answer_ms:.2f}" if outcome.answer_ms is not None else "-")
        return outcome
