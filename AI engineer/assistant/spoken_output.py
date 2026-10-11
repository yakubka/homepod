"""Spoken-output orchestration (VESTA, Team Leader AI-6 announcement + AI-9 offline fallback, SOFTWARE ONLY).

    approved text -> SpokenOutputController -> TtsEngine -> SpeechAudio -> PlaybackEngine -> SpokenOutputResult

One small controller owns ONE TtsEngine and ONE PlaybackEngine and uses them for three kinds of output, always named
explicitly by the caller (the kind is never guessed from the text):
  ANSWER        text from the answering layer (AnswerResult.text); the answer engine itself is untouched
  ANNOUNCEMENT  an AnnouncementRequest from a trigger (app / schedule / manual test)
  FALLBACK      one fixed offline sentence, spoken because an upstream service failed (a FallbackReason given by the caller)
There is no second TTS or playback implementation anywhere in this module.

It does NOT: transcribe, answer questions, read sensors, call an LLM, capture the microphone, parse schedules, expose an app/HTTP
API, detect the network, or decide classroom policy. It does not infer that anyone is late or absent: an announcement is exactly
the text the caller supplies, and the one built-in demo sentence is neutral.

FAILURES. Expected failures are reported, never hidden: TtsError subclasses become TTS_FAILED (or REJECTED when the text itself was
refused), PlaybackError subclasses become PLAYBACK_FAILED (or REJECTED for PlaybackBusy). A result never says COMPLETED unless the
playback engine said COMPLETED, so TTS success alone is never an "announcement played". Anything else, a TypeError or a wrong
return type from an engine, is a software bug or contract violation and PROPAGATES (after the controller is released); it is not
turned into a spoken apology.

ONE OUTPUT AT A TIME (AI-7 preparation only). While an output is active, a second request is REJECTED (error_stage BUSY) and is
neither queued nor played. No priority policy exists here; conversation-versus-announcement arbitration is a later layer that can
build on is_active and stop(). stop() asks the playback engine to stop; it cannot cancel a synthesis already running (that
finishes, then playback is skipped and the result is STOPPED), and a stop() that lands in the instant between "synthesis finished"
and "playback started" can be missed because the playback engine has nothing to stop yet.

STATUS: SOFTWARE-VALIDATED orchestration. COMPLETED means the playback engine reported that it handed every sample to the device. It does
not mean anyone heard anything: no project speaker, trigger integration, audibility or "plays clearly" is established here.
APP/SCHEDULE TRIGGER INTEGRATION: PENDING TEAM DEPENDENCY (nothing in this repository sends announcements yet).

PRIVACY. Text (answer, announcement or fallback) and audio never reach a log or a result: only the request type, the message id, the
text LENGTH, engine and voice ids, durations, timings, statuses and error class names are recorded.

TIMING. The *_accepted / tts_* markers come from this controller's clock; t_play_called, t_first_buffer_submitted and t_finished
are copied from the PlaybackResult. Give the controller and the playback engine the same clock (both default to time.perf_counter)
before comparing across them. t_first_buffer_submitted is the AI-4 SOFTWARE PROXY for the start of the spoken output, not acoustic onset.
"""

import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from audio.playback import PlaybackBusy, PlaybackEngine, PlaybackError, PlaybackResult, PlaybackStatus
from config.logging_config import setup_logging
from speech.tts import SpeechAudio, TtsEngine, TtsError, TtsInputError, check_text

logger = setup_logging()

# The fixed offline fallback sentence: short, no network, no LLM, no explanation that could be wrong.
FALLBACK_TEXT = "I'm sorry, I can't complete that request right now."
# The one fixed demonstration announcement: neutral, states nothing about the room, a lecture or anyone's whereabouts.
DEMO_ANNOUNCEMENT_ID = "demo-announcement-1"
DEMO_ANNOUNCEMENT_TEXT = "Attention please. This is a VESTA classroom announcement."

check_text(FALLBACK_TEXT)                     # a fallback that could not be synthesized would be worse than none: fail at import
check_text(DEMO_ANNOUNCEMENT_TEXT)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ---------------------------------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------------------------------

class RequestType(Enum):
    ANSWER = "ANSWER"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    FALLBACK = "FALLBACK"


class AnnouncementSource(Enum):
    """Where an announcement request says it came from. Nothing here is connected to a real app or schedule yet."""

    APP = "APP"
    SCHEDULE = "SCHEDULE"
    MANUAL_TEST = "MANUAL_TEST"
    UNKNOWN = "UNKNOWN"


class FallbackReason(Enum):
    """An EXPECTED upstream service failure, classified by the caller. This module never detects any of them itself."""

    ANSWER_SERVICE_UNAVAILABLE = "ANSWER_SERVICE_UNAVAILABLE"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    INTERNAL_SERVICE_FAILURE = "INTERNAL_SERVICE_FAILURE"


class OutputStatus(Enum):
    COMPLETED = "COMPLETED"              # the playback engine reported COMPLETED (software; not audibility)
    STOPPED = "STOPPED"                  # stop() interrupted the output (or arrived before playback began)
    TTS_FAILED = "TTS_FAILED"
    PLAYBACK_FAILED = "PLAYBACK_FAILED"
    REJECTED = "REJECTED"                # refused: the text was unspeakable, or an output was already active, or the engine was busy


STAGE_INPUT, STAGE_BUSY, STAGE_TTS, STAGE_PLAYBACK = "INPUT", "BUSY", "TTS", "PLAYBACK"


class SpokenOutputError(Exception):
    """Base class for orchestration errors that are the caller's or an implementation's fault (never a spoken failure)."""


class SpokenOutputRequestError(SpokenOutputError, ValueError):
    """A request object or argument is invalid (wrong type, unknown source or reason, bad id)."""


class SpokenOutputContractError(SpokenOutputError):
    """A TTS or playback engine returned something its contract forbids. This is a bug, so it is raised, not spoken about."""


# ---------------------------------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------------------------------

def _check_id(name: str, value) -> None:
    if not isinstance(value, str) or not _ID.match(value):
        raise SpokenOutputRequestError(f"{name} must be 1-64 characters of letters, digits, '.', '_' or '-', starting with a letter or digit, got {value!r}")


@dataclass(frozen=True)
class AnnouncementRequest:
    """An approved announcement from a trigger. The text is spoken exactly as given; this layer never writes or edits it."""

    announcement_id: str
    text: str
    source: AnnouncementSource
    trigger_id: Optional[str] = None

    def __post_init__(self) -> None:
        _check_id("announcement_id", self.announcement_id)
        if not isinstance(self.text, str) or not self.text.strip():
            raise SpokenOutputRequestError("announcement text must be an explicitly supplied, non-empty str")
        if not isinstance(self.source, AnnouncementSource):
            raise SpokenOutputRequestError(f"source must be an AnnouncementSource, got {self.source!r}")
        if self.trigger_id is not None:
            _check_id("trigger_id", self.trigger_id)


def demo_announcement_request() -> AnnouncementRequest:
    """The single fixed demonstration announcement, labelled MANUAL_TEST: it does not pretend to come from the app or a schedule."""
    return AnnouncementRequest(DEMO_ANNOUNCEMENT_ID, DEMO_ANNOUNCEMENT_TEXT, AnnouncementSource.MANUAL_TEST)


# ---------------------------------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SpokenOutputResult:
    """What one spoken-output request did. No text, no audio, no PCM."""

    request_type: RequestType
    status: OutputStatus
    message_id: Optional[str]
    origin: Optional[str]                        # AnnouncementSource or FallbackReason value; None for an answer
    text_chars: int
    trigger_id: Optional[str] = None
    tts_engine_id: Optional[str] = None
    voice_id: Optional[str] = None
    playback_status: Optional[PlaybackStatus] = None
    audio_duration_s: Optional[float] = None
    synthesis_ms: Optional[float] = None
    t_request_accepted: Optional[float] = None
    t_tts_started: Optional[float] = None
    t_tts_finished: Optional[float] = None
    t_play_called: Optional[float] = None
    t_first_buffer_submitted: Optional[float] = None    # SOFTWARE marker (AI-4 proxy for spoken start); NOT acoustic onset
    t_finished: Optional[float] = None
    error_stage: Optional[str] = None
    error_type: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_type, RequestType) or not isinstance(self.status, OutputStatus):
            raise SpokenOutputContractError("request_type and status must be enum members")
        if isinstance(self.text_chars, bool) or not isinstance(self.text_chars, int) or self.text_chars < 0:
            raise SpokenOutputContractError("text_chars must be an int >= 0")
        marks = [t for t in (self.t_request_accepted, self.t_tts_started, self.t_tts_finished) if t is not None]
        if any(b < a for a, b in zip(marks, marks[1:])):
            raise SpokenOutputContractError("controller timestamps are out of order")
        synthesized = all(v is not None for v in (self.tts_engine_id, self.voice_id, self.audio_duration_s, self.synthesis_ms))
        s = self.status
        if s in (OutputStatus.COMPLETED, OutputStatus.STOPPED):
            if self.error_stage is not None or self.error_type is not None or not synthesized:
                raise SpokenOutputContractError(f"{s.value} needs synthesis details and no error")
            if s is OutputStatus.COMPLETED and (
                    self.playback_status is not PlaybackStatus.COMPLETED or self.t_play_called is None
                    or self.t_first_buffer_submitted is None or self.t_finished is None):
                raise SpokenOutputContractError("COMPLETED needs a COMPLETED playback with its timestamps")
            if s is OutputStatus.STOPPED and self.playback_status not in (PlaybackStatus.STOPPED, None):
                raise SpokenOutputContractError("STOPPED cannot carry a COMPLETED playback")
        else:
            if not self.error_stage or not self.error_type:
                raise SpokenOutputContractError(f"{s.value} needs an error stage and type")
            if self.playback_status is not None:
                raise SpokenOutputContractError(f"{s.value} cannot carry a playback status")
            if s is OutputStatus.TTS_FAILED and (self.error_stage != STAGE_TTS or self.tts_engine_id is not None):
                raise SpokenOutputContractError("TTS_FAILED happens at stage TTS, before any audio exists")
            if s is OutputStatus.PLAYBACK_FAILED and (self.error_stage != STAGE_PLAYBACK or not synthesized):
                raise SpokenOutputContractError("PLAYBACK_FAILED happens at stage PLAYBACK, after a successful synthesis")
            if s is OutputStatus.REJECTED and self.error_stage not in (STAGE_INPUT, STAGE_BUSY, STAGE_PLAYBACK):
                raise SpokenOutputContractError("REJECTED happens at stage INPUT, BUSY or PLAYBACK")


# ---------------------------------------------------------------------------------------------------
# controller
# ---------------------------------------------------------------------------------------------------

class SpokenOutputController:
    """Speaks approved text through ONE TtsEngine and ONE PlaybackEngine, one output at a time."""

    def __init__(self, tts: TtsEngine, playback: PlaybackEngine, *, clock: Callable[[], float] = time.perf_counter) -> None:
        if not isinstance(tts, TtsEngine) or not isinstance(playback, PlaybackEngine):
            raise SpokenOutputRequestError("a TtsEngine and a PlaybackEngine are required")
        self._tts, self._playback, self._clock = tts, playback, clock
        self._lock = threading.Lock()
        self._active = False
        self._stop_requested = False

    @property
    def is_active(self) -> bool:
        """True from the moment a request is accepted (synthesis included) until its result is returned."""
        with self._lock:
            return self._active

    def stop(self) -> bool:
        """Ask the active output to stop. False when nothing is active. See the module docstring for what it cannot do."""
        with self._lock:
            if not self._active:
                return False
            self._stop_requested = True
        self._playback.stop()
        return True

    # -- the three explicit request types ---------------------------------------------------------

    def speak_answer(self, text: str, *, message_id: Optional[str] = None) -> SpokenOutputResult:
        """Speak answer text (for example AnswerResult.text). Always an ANSWER, whatever the text says."""
        if message_id is not None:
            _check_id("message_id", message_id)
        return self._speak(RequestType.ANSWER, message_id, None, None, text)

    def speak_announcement(self, request: AnnouncementRequest) -> SpokenOutputResult:
        if not isinstance(request, AnnouncementRequest):
            raise SpokenOutputRequestError(f"speak_announcement() needs an AnnouncementRequest, got {type(request).__name__}")
        return self._speak(RequestType.ANNOUNCEMENT, request.announcement_id, request.source.value, request.trigger_id, request.text)

    def speak_fallback(self, reason: FallbackReason) -> SpokenOutputResult:
        """Speak the fixed offline fallback sentence because the caller classified an upstream failure as `reason`."""
        if not isinstance(reason, FallbackReason):
            raise SpokenOutputRequestError(f"speak_fallback() needs a FallbackReason, got {reason!r}")
        return self._speak(RequestType.FALLBACK, "fallback-" + reason.value.lower().replace("_", "-"), reason.value, None, FALLBACK_TEXT)

    # -- internals ---------------------------------------------------------------------------------

    def _speak(self, request_type, message_id, origin, trigger_id, text) -> SpokenOutputResult:
        accepted = self._clock()
        base = dict(request_type=request_type, message_id=message_id, origin=origin, trigger_id=trigger_id,
                    text_chars=len(text) if isinstance(text, str) else 0, t_request_accepted=accepted)
        with self._lock:
            if self._active:
                return self._record(SpokenOutputResult(status=OutputStatus.REJECTED, error_stage=STAGE_BUSY, error_type="OutputBusy", **base))
            self._active = True
            self._stop_requested = False
        try:
            return self._run(text, base)
        finally:
            with self._lock:
                self._active = False

    def _run(self, text, base) -> SpokenOutputResult:
        t_start = self._clock()
        try:
            audio = self._tts.synthesize(text)
        except TtsInputError as exc:
            return self._record(SpokenOutputResult(status=OutputStatus.REJECTED, error_stage=STAGE_INPUT, error_type=type(exc).__name__,
                                                   t_tts_started=t_start, **base))
        except TtsError as exc:
            return self._record(SpokenOutputResult(status=OutputStatus.TTS_FAILED, error_stage=STAGE_TTS, error_type=type(exc).__name__,
                                                   t_tts_started=t_start, t_tts_finished=self._clock(), **base))
        t_done = self._clock()
        if not isinstance(audio, SpeechAudio):
            raise SpokenOutputContractError(f"the TtsEngine returned {type(audio).__name__}, not a SpeechAudio")
        spoken = dict(tts_engine_id=audio.tts_engine_id, voice_id=audio.voice_id, audio_duration_s=audio.duration_s,
                      synthesis_ms=audio.synthesis_ms, t_tts_started=t_start, t_tts_finished=t_done, **base)

        with self._lock:
            stopped_early = self._stop_requested
        if stopped_early:                                     # stop() arrived during synthesis: nothing is played
            return self._record(SpokenOutputResult(status=OutputStatus.STOPPED, **spoken))
        try:
            played = self._playback.play(audio)
        except PlaybackBusy as exc:
            return self._record(SpokenOutputResult(status=OutputStatus.REJECTED, error_stage=STAGE_PLAYBACK, error_type=type(exc).__name__, **spoken))
        except PlaybackError as exc:
            return self._record(SpokenOutputResult(status=OutputStatus.PLAYBACK_FAILED, error_stage=STAGE_PLAYBACK, error_type=type(exc).__name__, **spoken))
        if not isinstance(played, PlaybackResult):
            raise SpokenOutputContractError(f"the PlaybackEngine returned {type(played).__name__}, not a PlaybackResult")
        status = OutputStatus.COMPLETED if played.status is PlaybackStatus.COMPLETED else OutputStatus.STOPPED
        return self._record(SpokenOutputResult(status=status, playback_status=played.status, t_play_called=played.t_play_called,
                                               t_first_buffer_submitted=played.t_first_buffer_submitted, t_finished=played.t_finished, **spoken))

    @staticmethod
    def _record(result: SpokenOutputResult) -> SpokenOutputResult:
        # structural fields only: never the text, never the audio
        first = "-" if result.t_play_called is None or result.t_first_buffer_submitted is None else f"{(result.t_first_buffer_submitted - result.t_play_called) * 1000:.1f}"
        logger.info("event=spoken_output_done request_type=%s status=%s message_id=%s origin=%s text_chars=%d tts=%s voice=%s "
                    "audio_duration_s=%s synthesis_ms=%s playback_status=%s play_to_first_buffer_ms=%s error_stage=%s error_type=%s",
                    result.request_type.value, result.status.value, result.message_id or "-", result.origin or "-", result.text_chars,
                    result.tts_engine_id or "-", result.voice_id or "-",
                    "-" if result.audio_duration_s is None else f"{result.audio_duration_s:.3f}",
                    "-" if result.synthesis_ms is None else f"{result.synthesis_ms:.1f}",
                    result.playback_status.value if result.playback_status else "-", first, result.error_stage or "-", result.error_type or "-")
        return result
