"""TEST DOUBLES for the spoken-output foundation: a fake TTS engine, a fake playback engine and a fake `sounddevice` module.

Nothing here is a real voice, a real device or a real speaker. The fake stream calls the playback callback on a thread, the
way PortAudio does, so the callback/finished/abort/stop paths of audio/playback.py run for real against a scripted device.
"""

import threading
import time
from typing import Optional

import numpy as np

from audio.playback import PlaybackEngine, PlaybackResult, PlaybackStatus
from speech.tts import SpeechAudio, TtsEngine

RATE = 22050
UNSET = object()      # lets a test pass None as a deliberately bad value


def tone(n=2205, value=1000, offset=0) -> np.ndarray:
    """Deterministic non-silent int16 PCM (a repeating ramp, so a sample-for-sample comparison is meaningful)."""
    idx = (np.arange(n) + offset) % 64
    return ((idx - 32) * value // 32).astype(np.int16) + np.int16(1)


def speech_audio(n=2205, rate=RATE, samples=UNSET, **kw) -> SpeechAudio:
    base = dict(sample_rate=rate, channels=1, tts_engine_id="fake-tts", voice_id="fake-voice", synthesis_ms=1.0)
    base.update(kw)
    return SpeechAudio(samples=tone(n) if samples is UNSET else samples, **base)


class FakeTts(TtsEngine):
    """Deterministic fake: the audio length depends on the text length; the text itself is never stored."""

    sample_rate = RATE
    engine_id = "fake-tts"
    voice_id = "fake-voice"
    language = "en-US"

    def __init__(self, error=None, pcm=UNSET, clock=None):
        super().__init__(clock=clock) if clock else super().__init__()
        self.error, self.pcm, self.calls = error, pcm, 0

    def _synthesize(self, text):
        self.calls += 1
        if self.error:
            raise self.error
        return tone(RATE // 10 + 40 * len(text)) if self.pcm is UNSET else self.pcm


class FakePlayback(PlaybackEngine):
    """Records what it was asked to play and returns a well-formed result on a fake clock. Plays nothing."""

    def __init__(self):
        self.played = []
        self._playing = False
        self.stops = 0
        self._t = 100.0

    def play(self, audio):
        self.played.append(audio)
        t0 = self._t
        self._t += 0.5
        return PlaybackResult(PlaybackStatus.COMPLETED, audio.duration_s, t0, t0 + 0.01, t0 + 0.02, t0 + 0.02 + audio.duration_s,
                              "fake-device", "fake-api", 0.01, 0)

    def stop(self):
        self.stops += 1
        return False

    @property
    def is_playing(self):
        return self._playing


class FakeCallbackFlags:
    def __init__(self, underflow=False, other=False):
        self.output_underflow = underflow
        self.priming_output = other

    def __bool__(self):
        return self.output_underflow or self.priming_output


class FakeStream:
    def __init__(self, sd, samplerate, channels, dtype, device, callback, finished_callback):
        self.sd, self.samplerate, self.channels, self.dtype, self.device = sd, samplerate, channels, dtype, device
        self.callback, self.finished_callback = callback, finished_callback
        self.latency = sd.latency
        self.played = []
        self.aborted = threading.Event()
        self.closed = False
        self.started = False
        self.abort_calls = 0
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self.sd.start_error:
            raise self.sd.start_error
        self.started = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        sd = self.sd
        try:
            i = 0
            while not self.aborted.is_set():
                outdata = np.zeros((sd.block, 0 if sd.bad_outdata else 1), dtype=np.int16)
                flags = FakeCallbackFlags(underflow=i in sd.underflow_blocks, other=i in sd.other_flag_blocks)
                try:
                    self.callback(outdata, sd.block, None, flags)
                except sd.CallbackStop:
                    self.played.append(outdata.copy())
                    break
                except sd.CallbackAbort:
                    break
                self.played.append(outdata.copy())
                i += 1
                if sd.finish_early and i >= 2:
                    break
                if sd.pace_s:
                    time.sleep(sd.pace_s)
        finally:
            if not sd.never_finish:
                self.finished_callback()

    def abort(self):
        self.abort_calls += 1
        self.aborted.set()

    def stop(self):
        self.aborted.set()

    def close(self):
        self.closed = True

    def pcm(self):
        return np.concatenate(self.played)[:, 0] if self.played else np.empty(0, dtype=np.int16)


class FakeSd:
    """Stand-in for the `sounddevice` module (only the parts audio/playback.py uses)."""

    class PortAudioError(Exception):
        pass

    class CallbackStop(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    def __init__(self, block=256, pace_s=0.0, underflow_blocks=(), other_flag_blocks=(), open_error=None, start_error=None,
                 check_error=None, never_finish=False, finish_early=False, bad_outdata=False, latency=0.123, default_index=1):
        self.block, self.pace_s = block, pace_s
        self.underflow_blocks, self.other_flag_blocks = tuple(underflow_blocks), tuple(other_flag_blocks)
        self.open_error, self.start_error, self.check_error = open_error, start_error, check_error
        self.never_finish, self.finish_early, self.bad_outdata, self.latency = never_finish, finish_early, bad_outdata, latency
        self.default_index = default_index
        self.devices = [
            {"name": "Fake Microphone", "index": 0, "hostapi": 0, "max_output_channels": 0},
            {"name": "Fake Speaker", "index": 1, "hostapi": 0, "max_output_channels": 2},
            {"name": "Other Speaker", "index": 2, "hostapi": 0, "max_output_channels": 2},
        ]
        self.streams, self.calls = [], []

    def query_devices(self, device=None, kind=None):
        self.calls.append(("query_devices", device, kind))
        if device is None:
            return dict(self.devices[self.default_index])
        if isinstance(device, int):
            if not 0 <= device < len(self.devices):
                raise self.PortAudioError("invalid device")
            return dict(self.devices[device])
        matches = [d for d in self.devices if device.lower() in d["name"].lower() and d["max_output_channels"] > 0]
        if not matches:
            raise ValueError(f"No output device matching {device!r}")
        if len(matches) > 1:
            raise ValueError(f"Multiple output devices found for {device!r}")
        return dict(matches[0])

    def query_hostapis(self, index):
        return {"name": "FakeAPI"}

    def check_output_settings(self, **kw):
        self.calls.append(("check_output_settings", kw))
        if self.check_error:
            raise self.check_error

    def OutputStream(self, **kw):
        self.calls.append(("OutputStream", {k: v for k, v in kw.items() if k not in ("callback", "finished_callback")}))
        if self.open_error:
            raise self.open_error
        stream = FakeStream(self, **kw)
        self.streams.append(stream)
        return stream
