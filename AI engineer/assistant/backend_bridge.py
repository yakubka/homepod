"""AI <-> team backend bridge: the AI voice stack on top of the team's Raspberry Pi backend (main.py). SOFTWARE-VALIDATED / SIMULATED.

    POST /voice/ask {"text"}  (or a transcript from the microphone pipeline)
      -> AiBridge.ask -> the REAL VoiceAssistantRuntime: router -> validated StructuredCommand -> ActionLoop
         -> TeamLightsAdapter -> team Commands.run("lights_on"/"lights_off", source="voice") -> team Runtime override
         -> the team runtime tick applies it to team Lights -> the adapter sees lights.zones match -> APPLIED_CONFIRMED
      -> truthful response policy -> OutputArbiter -> TTS -> playback          (one reply, one speaker owner)
      -> contract JSON (v-04 VoiceAskResponse shape)

    sensor questions: team Hardware.sensors / LightSensor.lux -> TeamSensorSource -> ClassroomStateStore (freshness, origin)
      -> the deterministic answer engine. Replay mode only: the team Hardware has no timestamps or validity flags and keeps
         the last value on a failed read, so real-mode readings are NOT taken (answers say unavailable) until it does.

The team Runtime stays the ONE classroom state and the ONE decision engine: the bridge never sets a light itself, it asks the
team Commands exactly like the REST /command route does, and it controls nothing the team rules do not already control.

Origins: replay mode -> SIMULATED (team Lights only store a number). Real mode -> UNKNOWN: lights.zones is the team's record of a
GPIO write, not a measurement, so the response policy never confirms it (origin REAL is refused everywhere until hardware tests).
Timeouts and freshness limits below are DEVELOPMENT values (team decision D4 open), not calibrated hardware values.
"""

import secrets
import threading
import time
from typing import Any, Callable, Optional

from actions.contracts import (
    LINK_LOST, ActionAdapter, CapabilityState, CommandCapabilityDeclaration, RequestIdGenerator, ResultState, SubmitStatus,
)
from actions.loop import ActionLoop
from assistant.answering import AnswerContext, Capability, CapabilityDeclaration
from assistant.runtime import OutcomeKind, VoiceAssistantRuntime
from audio.utterance import SegmentOutcome, UtteranceSegmenter
from fusion.contracts import FusionFreshnessConfig, Origin
from fusion.live_state import ClassroomStateStore, SensorSource
from intents.contracts import CommandAction, CommandTarget
from speech.pipeline import PIPELINE_VALIDATION_LABEL, PipelineResult, PipelineStatus, PipelineTimings
from speech.stt import SttStatus

BRIDGE_LABEL = "SOFTWARE-VALIDATED / SIMULATED - NOT HARDWARE-VALIDATED"
DEV_TIMEOUT_S = 3.0                  # team tick_interval is 1.0 s: an override shows up within one tick. Development value (D4 open).
DEV_FRESHNESS = FusionFreshnessConfig(lux_max_age_s=30.0, temperature_max_age_s=30.0, humidity_max_age_s=30.0, presence_max_age_s=30.0)
TEXT_STT_ID = "text-input"           # marks a typed request: no microphone, no wake model, no recogniser ran
_REQ_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

TEAM_LIGHTS_CAPABILITIES = CommandCapabilityDeclaration({
    (CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_ON): CapabilityState.SUPPORTED_AND_AVAILABLE,
    (CommandTarget.CLASSROOM_LIGHTS, CommandAction.TURN_OFF): CapabilityState.SUPPORTED_AND_AVAILABLE,
}, source="team-backend")
_TEAM_INTENT = {CommandAction.TURN_ON: ("lights_on", 100), CommandAction.TURN_OFF: ("lights_off", 0)}
_QUERY_INTENT = {"ROOM_TEMPERATURE": ("query_temperature", "climate"), "ROOM_HUMIDITY": ("query_humidity", "climate"),
                 "ROOM_PEOPLE_COUNT": ("query_occupancy", "occupancy")}


class TeamLightsAdapter(ActionAdapter):
    """ActionAdapter over the team Commands. Confirms only what it can see: every team light zone at the requested level."""

    def __init__(self, commands, lights, *, simulated: bool, poll_s: float = 0.05,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self._commands, self._lights, self._simulated = commands, lights, simulated
        self._poll_s, self._clock, self._sleep = poll_s, clock, sleep
        self._queue, self._pending = [], None

    @property
    def identity(self) -> str:
        return "team-backend-lights"

    @property
    def origin(self) -> Origin:
        return Origin.SIMULATED if self._simulated else Origin.UNKNOWN

    def is_connected(self) -> bool:
        return True                  # in-process call: there is no link that can drop

    def submit(self, command) -> SubmitStatus:
        intent, level = _TEAM_INTENT[command.action]
        base = {"request_id": command.request_id, "target": command.target.value, "action": command.action.value}
        try:
            result = self._commands.run(intent, {"source": "voice"})
        except Exception:                                     # the team Commands catches its own errors; anything else: maybe acted
            return SubmitStatus.DELIVERY_UNCERTAIN
        if not result.get("ok"):
            self._queue.append({**base, "state": "REJECTED", "reason": "BACKEND_REFUSED"})
        else:
            self._queue.append({**base, "state": "ACCEPTED"})
            self._pending = (base, level)
        return SubmitStatus.DELIVERED

    def next_event(self, deadline: float) -> Any:
        if self._queue:
            return self._queue.pop(0)
        while self._pending is not None:
            base, level = self._pending
            if all(v == level for v in dict(self._lights.zones).values()):
                self._pending = None
                return {**base, "state": "APPLIED_CONFIRMED"}
            if self._clock() >= deadline:
                break
            self._sleep(self._poll_s)
        self._pending = None                          # deadline passed: a later match is not this command's confirmation
        return None


class TeamSensorSource(SensorSource):
    """Team Hardware.sensors + LightSensor.lux as sensor messages. Replay mode only, so always SIMULATED."""

    def __init__(self, hw, lux) -> None:
        self._hw, self._lux = hw, lux

    @property
    def origin(self) -> Origin:
        return Origin.SIMULATED

    def poll(self):
        s = dict(self._hw.sensors)                    # the value the team poll thread last stored; reading never triggers a device read
        out = [{"kind": "TEMPERATURE", "source_id": "dht22_temp", "status": "OK", "value": s["temperature"], "unit": "degC"},
               {"kind": "HUMIDITY", "source_id": "dht22_hum", "status": "OK", "value": s["humidity"], "unit": "%RH"}]
        if self._lux is not None:
            out.append({"kind": "LUX", "source_id": "bh1750", "status": "OK", "value": self._lux.lux, "unit": "lx"})
        return out


def text_result(text: str) -> PipelineResult:
    """A TRANSCRIBED result for typed text. The sample / duration fields are placeholders: no audio exists (stt_engine_id says so)."""
    return PipelineResult(
        status=PipelineStatus.TRANSCRIBED, raw_stt_status=SttStatus.OK, normalized_text=text, segment_outcome=SegmentOutcome.COMPLETE,
        activation_sample=0, detector_end_sample=0, activation_clamped=False, segment_start_sample=0, segment_end_sample=1,
        audio_duration_s=0.0, stt_engine_id=TEXT_STT_ID, model_id="none", wake_detected=True, wake_keyword=TEXT_STT_ID,
        wake_prefix_removed=False, decided_at_sample=1, unscored_samples=0, ignored_reactivation_count=0, error_class=None,
        timings=PipelineTimings(0.0, 0.0, 0.0), validation_label=PIPELINE_VALIDATION_LABEL)


def new_request_id() -> str:
    return "req_" + "".join(secrets.choice(_REQ_ALPHABET) for _ in range(12))


def valid_request_id(rid) -> bool:
    return isinstance(rid, str) and rid.startswith("req_") and 6 <= len(rid) - 4 <= 32 and all(c in _REQ_ALPHABET for c in rid[4:])


TYPED_TEXT, SYNTHETIC_AUDIO, RECORDED_AUDIO, LIVE_MICROPHONE = "TYPED_TEXT", "SYNTHETIC_AUDIO", "RECORDED_AUDIO", "LIVE_MICROPHONE"
AUDIO_KINDS = (SYNTHETIC_AUDIO, RECORDED_AUDIO, LIVE_MICROPHONE)


class AiBridge:
    """The AI subsystem as one object the team main.py starts. Thread-safe: one request at a time (the runtime is single-flight).

    engines=None: text only (/voice/ask); the audio engines are inert. engines=Stage B Engines (real wake / VAD / STT, loaded with
    scripts.ai2_stage_b_live.load_engines): the same runtime also takes audio through feed_audio() or a live harness, and every result
    says how the input arrived (TYPED_TEXT, SYNTHETIC_AUDIO, RECORDED_AUDIO, LIVE_MICROPHONE). A transcript only exists when the
    recogniser produced it; typed text is never reported as recognised speech."""

    def __init__(self, commands, runtime, hw, lux=None, *, simulated: bool, tts=None, playback=None, engines=None,
                 timeout_s: float = DEV_TIMEOUT_S) -> None:
        from scripts.vesta_runtime_demo import DemoPlayback, DemoTts, EnergyVad, ScriptedStt, ScriptedWake, segmenter_config
        self.simulated = simulated
        self._store = ClassroomStateStore(DEV_FRESHNESS)
        self._sensors = TeamSensorSource(hw, lux) if simulated else None
        self._adapter = TeamLightsAdapter(commands, runtime.lights, simulated=simulated)
        caps = {Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS} if simulated else set()
        self._caps = CapabilityDeclaration(frozenset(caps))          # PEOPLE_COUNT not declared: camera counts are unreliable (see report)
        self._text = {}
        self.tts_engine = tts or DemoTts()
        self.playback = playback or DemoPlayback()
        self.engines = engines
        if engines is None:                          # inert audio side: text enters through handle_result()
            audio = dict(detector=ScriptedWake(fire=()), segmenter=UtteranceSegmenter(segmenter_config(), EnergyVad()), stt=ScriptedStt([""]))
        else:
            from scripts.ai2_stage_b_live import build_live_segmenter_config
            audio = dict(detector=engines.detector, stt=engines.stt,
                         segmenter=UtteranceSegmenter(build_live_segmenter_config(engines.vad), engines.vad))
        self.runtime = VoiceAssistantRuntime(
            **audio, tts=self.tts_engine, playback=self.playback, context_provider=self._context, text_observer=self._text.__setitem__,
            action_loop=ActionLoop(self._adapter, TEAM_LIGHTS_CAPABILITIES, clock=time.monotonic, timeout_s=timeout_s),
            request_ids=RequestIdGenerator(prefix="api"))
        self._lock = threading.Lock()
        self._harness = None
        self.requests = 0
        self.last = None

    # -- audio --------------------------------------------------------------------------------------------------------

    def feed_audio(self, block, input_kind: str) -> list:
        """One block of 16 kHz mono int16 PCM through the REAL wake / VAD / STT pipeline. Returns one record per finished utterance."""
        if self.engines is None:
            raise RuntimeError("this bridge was built without audio engines")
        if input_kind not in AUDIO_KINDS:
            raise ValueError(f"input_kind must be one of {AUDIO_KINDS}")
        with self._lock:
            return [self._handle(r, input_kind) for r in self.runtime.pipeline.feed(block)]

    def finish_audio(self, input_kind: str) -> list:
        with self._lock:
            return [self._handle(r, input_kind) for r in self.runtime.pipeline.finish()]

    def handle_audio_result(self, result, input_kind: str = LIVE_MICROPHONE) -> dict:
        """For a capture harness that runs the pipeline itself (on_result callback)."""
        with self._lock:
            return self._handle(result, input_kind)

    def _handle(self, result, input_kind: str) -> dict:
        self._text.clear()
        o = self.runtime.handle_result(result)
        self.requests += 1
        transcribed = result.status.value == "TRANSCRIBED"
        rec = {"input": input_kind, "pipeline": result.status.value, "stt_engine": result.stt_engine_id, "outcome": o.kind.value,
               "transcript": result.normalized_text if transcribed else None, "reply": self._text.get("ANSWER"),
               "action": o.action.result.state.value if o.action and o.action.result else None, "contract": None}
        if transcribed:
            rid = new_request_id()
            data, _ = _contract(o, rid, result.normalized_text, "microphone", rec["reply"] or "Sorry, I didn't catch that.")
            rec["contract"] = data                    # None when a command was not confirmed (TIMEOUT / UNKNOWN)
        self.last = {"input": input_kind, "pipeline": rec["pipeline"], "kind": rec["outcome"], "action": rec["action"]}   # no transcript
        return rec

    def start_microphone(self, mic, *, config=None) -> None:
        """Live capture through the existing Stage B harness (bounded queue, discontinuity handling). The harness feeds the runtime's
        pipeline; every result comes back through handle_audio_result. NOT HARDWARE-VALIDATED."""
        from scripts.ai2_stage_b_live import SOURCE_LIVE, LiveConfig, StageBLiveHarness
        if self.engines is None:
            raise RuntimeError("this bridge was built without audio engines")
        self._harness = StageBLiveHarness(mic, self.engines, source_kind=SOURCE_LIVE, config=config or LiveConfig(),
                                          pipeline=self.runtime.pipeline, on_result=lambda r, _e: self.handle_audio_result(r),
                                          on_discontinuity=self.runtime.on_capture_abandoned)
        self._harness.start()

    def _context(self) -> AnswerContext:
        if self._sensors is not None:
            self._store.pump(self._sensors)
        return AnswerContext(self._store.snapshot(), self._caps)

    def ask(self, text, request_id=None, source: str = "app"):
        """-> (http_status, body). Body follows the v-04 contract: {"ok": true, "data": VoiceAskResponse} or an ErrorResponse."""
        if not isinstance(text, str) or not 1 <= len(text) <= 200:
            return 400, _error("invalid_input", "text must be 1 to 200 characters.", {"field": "text", "reason": "length"})
        if not text.strip():                          # an empty transcript is not a valid TRANSCRIBED result: refuse it here
            return 400, _error("invalid_input", "text must contain at least one non-space character.", {"field": "text", "reason": "blank"})
        if request_id is not None and not valid_request_id(request_id):
            return 400, _error("invalid_input", "request_id must match req_ and 6 to 32 lower-case letters or digits.",
                               {"field": "request_id", "reason": "pattern"})
        if source not in ("app", "microphone"):
            raise ValueError("source must be app or microphone")
        rid = request_id or new_request_id()
        with self._lock:
            self._text.clear()
            outcome = self.runtime.handle_result(text_result(text.strip()))
            reply = self._text.get("ANSWER") or "Sorry, I didn't catch that."
            self.requests += 1
        data, status = _contract(outcome, rid, text, source, reply)
        self.last = {"input": TYPED_TEXT, "request_id": rid, "outcome": data["outcome"] if data else "timeout", "kind": outcome.kind.value,
                     "action": outcome.action.result.state.value if outcome.action and outcome.action.result else None}
        if data is None:
            return 504, _error("device_timeout", "The lighting command was not confirmed in time.",
                               {"device": "team-backend-lights", "cmd_id": rid, "attempts": 1})
        return status, {"ok": True, "data": data}

    def status(self) -> dict:
        voice = "text-input" if self.engines is None else ("text-input+live-microphone" if self._harness else "text-input+audio")
        return {"enabled": True, "label": BRIDGE_LABEL, "origin": self._adapter.origin.value, "voice": voice,
                "assistant_state": self.runtime.state.current_state.value, "requests": self.requests, "last": self.last,
                "sensors": "team-replay" if self._sensors else "unavailable: team Hardware has no timestamps/validity in real mode",
                "capabilities": sorted(c.value for c in self._caps.capabilities)}

    def stop(self) -> None:
        """Stop live capture (if any), then wait for a request in flight."""
        if self._harness is not None:
            self._harness.stop()
            self._harness = None
        with self._lock:
            pass


def _error(code, message, detail):
    return {"ok": False, "error": {"code": code, "message": message, "detail": detail}}


def _contract(o, rid, raw_text, source, reply):
    """RuntimeOutcome -> (VoiceAskResponse dict, 200), or (None, 504) for a submitted command whose outcome is unknown."""
    intent, action, ttype, conf = "unknown", "none", "none", 0.0
    if o.command is not None and o.command.target is CommandTarget.CLASSROOM_LIGHTS and o.command.action in _TEAM_INTENT:
        intent, action, ttype, conf = _TEAM_INTENT[o.command.action][0], "set", "lighting", 1.0
    elif o.query_family in _QUERY_INTENT and o.kind is OutcomeKind.ANSWER_OUTPUT:
        (intent, ttype), action, conf = _QUERY_INTENT[o.query_family], "query", 1.0
    if o.kind is OutcomeKind.ANSWER_OUTPUT:
        outcome = "answered" if o.answer_status == "ANSWERED" else "rejected"      # "unavailable" is not an answer
    elif o.action is not None and o.action.result is not None and o.action.result.state in (ResultState.TIMEOUT_UNKNOWN, ResultState.UNKNOWN):
        return None, 504
    elif o.action is not None:
        outcome = "executed" if (o.action.result is not None and o.action.result.state is ResultState.APPLIED_CONFIRMED) else "rejected"
    elif o.kind is OutcomeKind.COMMAND_NOT_ADMITTED:
        outcome = "rejected"
    else:
        outcome = "not_understood"
    command = {"request_id": rid, "intent": intent, "action": action, "target": {"type": ttype, "zone": None}, "params": {},
               "confidence": conf, "raw_text": raw_text[:200], "source": source}
    return {"request_id": rid, "command": command, "outcome": outcome, "reply_text": reply[:300], "decision_id": None}, 200
