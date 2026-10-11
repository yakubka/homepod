"""Speaker-side playback contract and sounddevice backend (VESTA, Team Leader spoken-output foundation).

OUTPUT ONLY. This module is the output-side counterpart of the input contracts (AudioSource, microphone, WavFileSource) and
shares nothing with them: it does not touch, subclass or overload any capture class. It receives a SpeechAudio (the TTS
result type, speech/tts.py) and knows nothing about how that audio was made.

    SpeechAudio -> PlaybackEngine.play() -> PlaybackResult

CONTRACT
  * play() accepts only a SpeechAudio of mono int16 PCM and plays it at its NATIVE sample rate. Nothing is resampled. A
    device that cannot run that rate raises PlaybackFormatError; it is never papered over.
  * play() blocks in v1 until the audio COMPLETED or was STOPPED (by stop(), from another thread). A failure raises a typed
    PlaybackError; nothing is swallowed and no failure is reported as COMPLETED.
  * is_playing is True from the moment play() has accepted the audio until it returns; stop() is idempotent and safe when idle.
  * Only one utterance plays at a time; a second play() raises PlaybackBusy. Who may speak when (conversation versus
    announcement) is NOT decided here: that is a later arbitration layer that can use is_playing and stop().

TIMING SEMANTICS (monotonic clock, time.perf_counter, one clock for all fields)
  t_play_called            play() entered (after the input was validated)
  t_stream_ready           the output stream is OPEN and about to start (device resolved, format accepted)
  t_first_buffer_submitted the first callback has put the first audio samples into the device buffer
  t_finished               the stream reported it finished (all buffers drained) or was aborted
  These are SOFTWARE markers. t_first_buffer_submitted is NOT the moment sound leaves the speaker: the device latency, a DAC,
  an amplifier, the speaker and the air all come after it. stream_latency_s is what the host API REPORTS for the output
  path; it is kept separate and is never added to a timestamp. Until physical acoustic timing is measured, the AI-4 SOFTWARE
  PROXY for "start of the spoken answer" is t_first_buffer_submitted.

UNDERFLOW POLICY: an output underflow flagged by PortAudio is counted in underflow_count and logged; playback continues and the
result still says COMPLETED, because the stream itself did not fail. Other status flags are logged. Nothing is hidden.

STATUS: SOFTWARE/API-VALIDATED. Tests use a fake stream. A gated smoke test opens a real stream and plays SILENCE on the
development PC: that proves the API accepts buffers and the timing events occur. It proves nothing about the project
speaker, an I2S DAC, the Raspberry Pi, audibility or volume.

PRIVACY: nothing here logs audio or text. Only durations, device identity, timings, counts and error classes are logged.
"""

import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import numpy as np

from config.logging_config import setup_logging
from speech.tts import SpeechAudio

logger = setup_logging()

# DEVELOPMENT POLICY, not calibrated: how long play() waits beyond the audio's own length (plus the reported latency)
# for the device to report that it finished, before it aborts the stream and raises PlaybackFailed.
COMPLETION_MARGIN_S = 10.0


# ---------------------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------------------

class PlaybackError(Exception):
    """Base class for every playback failure. Nothing is swallowed and no failure is reported as COMPLETED."""


class PlaybackRuntimeUnavailable(PlaybackError):
    """The audio output runtime (sounddevice / PortAudio) cannot be imported."""


class PlaybackInputError(PlaybackError, ValueError):
    """What was handed to play() is not a playable SpeechAudio."""


class PlaybackDeviceNotFound(PlaybackError):
    """No output device matches the request (or the default device cannot output)."""


class PlaybackFormatError(PlaybackError):
    """The device cannot play the audio's native format (mono int16 at its sample rate)."""


class PlaybackOpenFailed(PlaybackError):
    """The output stream could not be opened or started."""


class PlaybackFailed(PlaybackError):
    """Playback broke after it started (callback error, no completion report, stream error)."""


class PlaybackBusy(PlaybackError):
    """play() was called while another utterance is still playing."""


# ---------------------------------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------------------------------

class PlaybackStatus(Enum):
    COMPLETED = "COMPLETED"      # every sample was handed to the device and the device reported it finished
    STOPPED = "STOPPED"          # stop() interrupted the playback before it reached the end


def _is_real(value) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class PlaybackResult:
    """What one play() did. All t_* values are software markers on one monotonic clock; see the module docstring."""

    status: PlaybackStatus
    audio_duration_s: float
    t_play_called: float
    t_stream_ready: Optional[float]
    t_first_buffer_submitted: Optional[float]      # SOFTWARE marker only: NOT physical acoustic onset
    t_finished: float
    device_name: Optional[str]
    host_api: Optional[str]
    stream_latency_s: Optional[float]              # as REPORTED by the host API; not measured, not added to any timestamp
    underflow_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, PlaybackStatus):
            raise PlaybackFailed(f"PlaybackResult.status must be a PlaybackStatus, got {self.status!r}")
        if not _is_real(self.audio_duration_s) or self.audio_duration_s <= 0:
            raise PlaybackFailed(f"audio_duration_s must be a finite number > 0, got {self.audio_duration_s!r}")
        for name in ("t_play_called", "t_finished"):
            if not _is_real(getattr(self, name)):
                raise PlaybackFailed(f"{name} must be a finite number, got {getattr(self, name)!r}")
        for name in ("t_stream_ready", "t_first_buffer_submitted"):
            value = getattr(self, name)
            if value is not None and not _is_real(value):
                raise PlaybackFailed(f"{name} must be a finite number or None, got {value!r}")
        if self.status is PlaybackStatus.COMPLETED and (self.t_stream_ready is None or self.t_first_buffer_submitted is None):
            raise PlaybackFailed("a COMPLETED playback has a stream-ready and a first-buffer timestamp")
        if self.t_first_buffer_submitted is not None and self.t_stream_ready is None:
            raise PlaybackFailed("a first-buffer timestamp needs a stream-ready timestamp")
        marks = [t for t in (self.t_play_called, self.t_stream_ready, self.t_first_buffer_submitted, self.t_finished) if t is not None]
        if any(b < a for a, b in zip(marks, marks[1:])):
            raise PlaybackFailed("timestamps are out of order (need play_called <= stream_ready <= first_buffer <= finished)")
        for name in ("device_name", "host_api"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise PlaybackFailed(f"{name} must be a non-empty str or None, got {value!r}")
        if self.stream_latency_s is not None and (not _is_real(self.stream_latency_s) or self.stream_latency_s < 0):
            raise PlaybackFailed(f"stream_latency_s must be a finite number >= 0 or None, got {self.stream_latency_s!r}")
        if isinstance(self.underflow_count, bool) or not isinstance(self.underflow_count, int) or self.underflow_count < 0:
            raise PlaybackFailed(f"underflow_count must be an int >= 0, got {self.underflow_count!r}")


# ---------------------------------------------------------------------------------------------------
# engine contract
# ---------------------------------------------------------------------------------------------------

def check_playable(audio) -> None:
    """Raise PlaybackInputError (not a SpeechAudio) or PlaybackFormatError (not mono int16) unless `audio` can be played."""
    if not isinstance(audio, SpeechAudio):
        raise PlaybackInputError(f"play() needs a SpeechAudio, got {type(audio).__name__}")
    s = audio.samples
    if not isinstance(s, np.ndarray) or s.dtype != np.int16 or s.ndim != 1 or s.size == 0:
        raise PlaybackFormatError("audio samples must be non-empty 1-D int16")
    if audio.channels != 1:
        raise PlaybackFormatError(f"only mono audio can be played, got {audio.channels} channels")
    if isinstance(audio.sample_rate, bool) or not isinstance(audio.sample_rate, int) or audio.sample_rate <= 0:
        raise PlaybackFormatError(f"invalid sample rate {audio.sample_rate!r}")


class PlaybackEngine(ABC):
    """Play one SpeechAudio on an output device."""

    @abstractmethod
    def play(self, audio: SpeechAudio) -> PlaybackResult:
        """Play `audio` at its native rate; block until it completed or was stopped. Raise a PlaybackError on failure."""

    @abstractmethod
    def stop(self) -> bool:
        """Interrupt the playback in progress, if any. Idempotent and safe when idle. Returns True if a playback was interrupted."""

    @property
    @abstractmethod
    def is_playing(self) -> bool:
        """True from the moment play() accepted the audio until it returns."""

    # OPTIONAL first-buffer signal (added for AI-8 state synchronization). An engine that cannot report the moment its first samples
    # reach the device keeps these defaults, and callers that need the signal must refuse such an engine instead of guessing.
    @property
    def supports_first_buffer_listener(self) -> bool:
        return False

    def set_first_buffer_listener(self, listener: Optional[Callable[[], None]]) -> None:
        """Register `listener` (no arguments) to be called once per play(), on the thread that called play(), after the first samples
        are in the device buffer and before play() returns. None clears it. It is never called when playback stops or fails before
        the first buffer. A listener that raises does not affect the playback."""
        raise PlaybackError(f"{type(self).__name__} cannot report the first buffer")


# ---------------------------------------------------------------------------------------------------
# sounddevice backend
# ---------------------------------------------------------------------------------------------------

class _Run:
    """State of one play() call, shared with the PortAudio callbacks."""

    def __init__(self, samples: np.ndarray) -> None:
        self.samples = samples
        self.pos = 0
        self.t_first: Optional[float] = None
        self.t_finished: Optional[float] = None
        self.underflows = 0
        self.other_flags = 0
        self.reached_end = False
        self.stop_requested = False
        self.error: Optional[BaseException] = None
        self.stream = None
        self.done = threading.Event()
        self.first_event = threading.Event()      # set by the first buffer, or by the stream finishing (so a waiter is never stuck)


def _load_sounddevice():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:        # includes a missing PortAudio library
        raise PlaybackRuntimeUnavailable(f"sounddevice cannot be imported: {type(exc).__name__}: {exc}") from exc
    return sd


class SoundDevicePlaybackEngine(PlaybackEngine):
    """Plays SpeechAudio through sounddevice.OutputStream (callback mode). The device is chosen by the caller."""

    def __init__(self, device=None, *, sd_module=None, clock: Callable[[], float] = time.perf_counter) -> None:
        """device: None = the platform default output device (a DEVELOPMENT convenience, never assumed to be project
        hardware), an integer index, or a name substring. sd_module and clock are test hooks."""
        self._sd = sd_module if sd_module is not None else _load_sounddevice()
        self._device = device
        self._clock = clock
        self._lock = threading.Lock()
        self._run: Optional[_Run] = None
        self._playing = False
        self._first_listener: Optional[Callable[[], None]] = None

    # -- state -----------------------------------------------------------------------------------

    @property
    def supports_first_buffer_listener(self) -> bool:
        return True

    def set_first_buffer_listener(self, listener: Optional[Callable[[], None]]) -> None:
        if listener is not None and not callable(listener):
            raise PlaybackInputError("the first-buffer listener must be callable or None")
        with self._lock:
            self._first_listener = listener

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def stop(self) -> bool:
        with self._lock:
            run = self._run
            if run is None:
                return False
            run.stop_requested = True
            stream = run.stream
        if stream is not None:
            try:
                stream.abort()                       # best effort: not sample-perfect, a few buffers may still be heard
            except Exception as exc:
                logger.error("event=playback_stop_error error=%s", type(exc).__name__)
        return True

    # -- play ------------------------------------------------------------------------------------

    def play(self, audio: SpeechAudio) -> PlaybackResult:
        check_playable(audio)
        with self._lock:
            if self._playing:
                raise PlaybackBusy("another utterance is still playing")
            self._playing = True
            run = _Run(audio.samples)
            self._run = run
        try:
            return self._play(audio, run)
        finally:
            with self._lock:
                self._playing = False
                self._run = None

    def _resolve_device(self):
        sd = self._sd
        try:
            info = sd.query_devices(self._device, "output")
        except (ValueError, sd.PortAudioError) as exc:
            raise PlaybackDeviceNotFound(f"no usable output device for {self._device!r}: {type(exc).__name__}: {exc}") from exc
        if not info or info.get("max_output_channels", 0) < 1:
            raise PlaybackDeviceNotFound(f"device {info.get('name') if info else self._device!r} has no output channels")
        index = info.get("index", self._device)
        try:
            host_api = sd.query_hostapis(info["hostapi"])["name"]
        except Exception:
            host_api = None
        return index, info["name"], host_api

    def _play(self, audio: SpeechAudio, run: _Run) -> PlaybackResult:
        sd, clock = self._sd, self._clock
        t_called = clock()
        samples, total = audio.samples, audio.samples.size
        rate = audio.sample_rate
        device_name = host_api = None
        latency = None
        t_ready = None

        def callback(outdata, frames, time_info, status):
            try:
                if status:
                    if getattr(status, "output_underflow", False):
                        run.underflows += 1
                    else:
                        run.other_flags += 1
                if run.stop_requested:
                    raise sd.CallbackAbort
                n = min(frames, total - run.pos)
                if n > 0:
                    outdata[:n, 0] = samples[run.pos:run.pos + n]       # copy only this block into the device buffer
                if run.t_first is None:
                    run.t_first = clock()                                # first samples are now in the device buffer
                    run.first_event.set()                                # wakes play()'s thread; the listener never runs in this callback
                if n < frames:
                    outdata[n:] = 0
                    run.pos += n
                    run.reached_end = True
                    raise sd.CallbackStop
                run.pos += n
            except (sd.CallbackStop, sd.CallbackAbort):
                raise
            except BaseException as exc:                                 # never let an error escape into PortAudio's thread
                run.error = exc
                raise sd.CallbackAbort

        def finished():
            run.t_finished = clock()
            run.done.set()
            run.first_event.set()

        if run.stop_requested:
            return self._result(PlaybackStatus.STOPPED, audio, t_called, None, run, clock(), None, None, None)
        index, device_name, host_api = self._resolve_device()
        try:
            sd.check_output_settings(device=index, channels=1, dtype="int16", samplerate=rate)
        except (ValueError, sd.PortAudioError) as exc:
            raise PlaybackFormatError(f"device {device_name!r} cannot play mono int16 at {rate} Hz: {type(exc).__name__}: {exc}") from exc
        try:
            stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16", device=index, callback=callback, finished_callback=finished)
        except Exception as exc:
            logger.error("event=playback_open_failed device=%r error=%s", device_name, type(exc).__name__)
            raise PlaybackOpenFailed(f"could not open the output stream on {device_name!r}: {type(exc).__name__}: {exc}") from exc
        run.stream = stream
        try:
            latency = stream.latency
            if isinstance(latency, (tuple, list)):
                latency = latency[-1]
            latency = None if latency is None else float(latency)
            t_ready = clock()                                            # open, about to start
            if not run.stop_requested:
                try:
                    stream.start()
                except Exception as exc:
                    logger.error("event=playback_start_failed device=%r error=%s", device_name, type(exc).__name__)
                    raise PlaybackOpenFailed(f"could not start the output stream on {device_name!r}: {type(exc).__name__}: {exc}") from exc
                if run.stop_requested:
                    stream.abort()                                       # stop() arrived while starting
                if not self._wait_done(run, audio.duration_s + (latency or 0.0) + COMPLETION_MARGIN_S):
                    try:
                        stream.abort()
                    except Exception:
                        pass
                    raise PlaybackFailed(f"the device did not report completion within {COMPLETION_MARGIN_S:.0f} s of the audio's length")
            else:
                run.t_finished = clock()
        finally:
            try:
                stream.close()
            except Exception as exc:
                logger.error("event=playback_close_error error=%s", type(exc).__name__)
            run.stream = None

        if run.error is not None:
            raise PlaybackFailed(f"playback failed in the audio callback: {type(run.error).__name__}") from run.error
        if run.reached_end:
            status = PlaybackStatus.COMPLETED
        elif run.stop_requested:
            status = PlaybackStatus.STOPPED
        else:
            raise PlaybackFailed("the stream ended before all audio was played and nobody asked it to stop")
        finished_at = run.t_finished if run.t_finished is not None else clock()
        return self._result(status, audio, t_called, t_ready, run, finished_at, device_name, host_api, latency)

    def _wait_done(self, run: _Run, timeout: float) -> bool:
        """Wait for the stream to finish (True) or for `timeout` to pass (False). If a first-buffer listener is registered, call it
        here, on play()'s own thread, once the first buffer is in; so it always runs before play() returns and never in the callback."""
        with self._lock:
            listener = self._first_listener
        if listener is not None:
            started = time.monotonic()                                   # real time: the injected clock may be a fake
            run.first_event.wait(timeout)
            if run.t_first is not None:
                try:
                    listener()
                except Exception as exc:                                 # a broken listener must not break the playback
                    logger.error("event=playback_first_buffer_listener_error error=%s", type(exc).__name__)
            timeout = max(0.0, timeout - (time.monotonic() - started))
        return run.done.wait(timeout)

    def _result(self, status, audio, t_called, t_ready, run, t_finished, device_name, host_api, latency) -> PlaybackResult:
        t_first = run.t_first
        # no clamping: if the markers are ever out of order, PlaybackResult refuses them instead of hiding it
        result = PlaybackResult(status=status, audio_duration_s=audio.duration_s, t_play_called=t_called, t_stream_ready=t_ready,
                                t_first_buffer_submitted=t_first, t_finished=t_finished,
                                device_name=device_name, host_api=host_api, stream_latency_s=latency, underflow_count=run.underflows)
        if run.other_flags:
            logger.warning("event=playback_status_flags count=%d device=%r", run.other_flags, device_name)
        if run.underflows:
            logger.warning("event=playback_underflow count=%d device=%r", run.underflows, device_name)
        # structural fields only: never audio, never text
        logger.info("event=playback_done status=%s audio_duration_s=%.3f device=%r host_api=%s stream_latency_s=%s underflows=%d "
                    "open_ms=%s call_to_first_buffer_ms=%s total_ms=%.1f",
                    result.status.value, result.audio_duration_s, result.device_name, result.host_api,
                    "-" if latency is None else f"{latency:.3f}", result.underflow_count,
                    "-" if t_ready is None else f"{(t_ready - t_called) * 1000:.1f}",
                    "-" if t_first is None else f"{(t_first - t_called) * 1000:.1f}", (result.t_finished - t_called) * 1000)
        return result
