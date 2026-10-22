"""TEST DOUBLES for the Stage B live-capture harness tests (scripts/ai2_stage_b_live.py).

Everything here is a fake: a block source that replays a script, a scripted wake detector (a real WakeWordDetector
subclass, so the real cooldown / end_sample logic applies), an energy VAD tied to the audio, and a STT that can be
held on an Event. Nothing here is a microphone, a model or a measurement.

Default scale (same as the pipeline tests): 1000 Hz, 10-sample VAD frames, 40-sample wake chunks and capture blocks,
the make_config() segmenter (confirm 30, end silence 40, padding 20, pre-roll 100, no-speech timeout 200, min 50,
max 500). Speech samples carry their stream index (2000 + index % 1000), so a transcribed segment can be compared
sample for sample with the part of the stream it should contain.
"""

import queue
import threading

import numpy as np

from audio.file_source import EndOfAudio
from audio.microphone import MicBlock, MicTelemetry
from audio.vad import VadEngine
from speech.stt import SttEngine
from tests.voice_synthetic import FRAME, SR, make_config
from wakeword.detector import WakeWordDetector

CHUNK = 40
BLOCK = 40
GUARD_S = 10.0                    # a failure guard for Event waits, never a pacing delay


def stream(total, speech=()):
    x = np.zeros(total, np.int16)
    for a, b in speech:
        x[a:b] = (2000 + np.arange(a, b) % 1000).astype(np.int16)
    return x


def make_blocks(x, block=BLOCK, *, epoch=1, drop=(), overflow=(), start_at=0, first_index=0):
    """Cut `x` into MicBlocks with exact positions. `drop` omits those block numbers (their positions stay missing);
    `overflow` flags those blocks the way PortAudio's input_overflow would."""
    out = []
    for i, lo in enumerate(range(0, x.size, block)):
        if i in drop:
            continue
        piece = x[lo:lo + block].reshape(-1, 1).copy()
        out.append(MicBlock(piece, first_index + i, start_at + lo, epoch, i in overflow))
    return out


class ScriptedWake(WakeWordDetector):
    sample_rate = SR
    chunk_samples = CHUNK

    def __init__(self, fire=(8,), cooldown_s=0.0):
        self.fire = set(fire)
        super().__init__(threshold=0.5, cooldown_s=cooldown_s)

    def _score_chunk(self, chunk):
        return {"hey_jarvis": 0.9 if self._chunks_processed in self.fire else 0.0}

    def _reset_backend(self):
        pass


class EnergyVad(VadEngine):
    """0.9 when every sample of the frame is loud, else 0.0. Declares itself verified (it is a FAKE: the flag only
    lets it pass load_engines(), the same way a real, hash-checked engine would)."""

    sample_rate = SR
    frame_samples = FRAME
    artifact_verified = True

    def __init__(self, fail_after=None):
        self.fail_after, self.frames = fail_after, 0

    def _score_frame(self, frame):
        self.frames += 1
        if self.fail_after is not None and self.frames > self.fail_after:
            raise RuntimeError("scripted VAD failure")
        return 0.9 if int(frame.min()) >= 1000 else 0.0

    def reset(self):
        pass


class FakeStt(SttEngine):
    """Records every call's PCM. `entered` is set when a call starts; if `release` is given the call waits for it."""

    sample_rate = SR
    engine_id = "fake-stt"
    model_id = "fake-model"
    snapshot_verified = True

    def __init__(self, reply="hey jarvis turn on the lights", release=None):
        super().__init__()
        self.reply, self.release = reply, release
        self.calls = []
        self.entered = threading.Event()

    def _transcribe(self, samples):
        self.calls.append(np.array(samples))
        self.entered.set()
        if self.release is not None and not self.release.wait(GUARD_S):
            raise AssertionError("test never released the STT")
        return self.reply


# 16 kHz variants for the command-line tests (the CLI path uses the real 16 kHz segmenter defaults)
class EnergyVad16(EnergyVad):
    sample_rate = 16000
    frame_samples = 512


class ScriptedWake16(ScriptedWake):
    sample_rate = 16000
    chunk_samples = 1280


class FakeStt16(FakeStt):
    sample_rate = 16000


class Hold:
    """A script item: call `fn()` on the capture thread when the source reaches it (for example wait for an Event)."""

    def __init__(self, fn):
        self.fn = fn


class Timeout:
    """A script item: one read that times out (queue.Empty)."""


class Boom:
    """A script item: the read raises `exc`."""

    def __init__(self, exc):
        self.exc = exc


class FakeBlockSource:
    """Replays a script of MicBlocks / Hold / Timeout / Boom, then ends according to `end`:
    'eof' -> EndOfAudio; 'stall' -> every read times out; 'idle' -> every read waits `idle_wait_s` then times out."""

    channels = 1

    def __init__(self, script, *, end="eof", sample_rate=SR, mic_blocks_dropped=0, mic_overflow_events=0, fail_start=None,
                 idle_wait_s=0.005):
        self.sample_rate = sample_rate
        self.end, self.idle_wait_s = end, idle_wait_s
        self.mic_blocks_dropped, self.mic_overflow_events = mic_blocks_dropped, mic_overflow_events
        self.fail_start = fail_start
        self.all_read = threading.Event()
        self.started = self.stopped = self.reads = 0
        self.load(script)

    def load(self, script):
        self.script, self.pos = list(script), 0
        self.all_read.clear()

    def start(self):
        if self.fail_start is not None:
            raise self.fail_start
        self.started += 1

    def stop(self):
        self.stopped += 1

    def telemetry(self):
        return MicTelemetry(1, self.reads, self.reads * BLOCK, self.mic_blocks_dropped, self.mic_blocks_dropped * BLOCK,
                            self.mic_overflow_events, 0, 50)

    def read_block(self, timeout):
        while True:
            if self.pos >= len(self.script):
                self.all_read.set()
                if self.end == "eof":
                    raise EndOfAudio("end")
                if self.end == "idle":
                    threading.Event().wait(self.idle_wait_s)
                raise queue.Empty
            item = self.script[self.pos]
            self.pos += 1
            if isinstance(item, Hold):
                item.fn()
                continue
            if isinstance(item, Timeout):
                raise queue.Empty
            if isinstance(item, Boom):
                raise item.exc
            self.reads += 1
            return item


def engines(fire=(8,), reply="hey jarvis turn on the lights", release=None, vad=None, detector=None):
    from scripts.ai2_stage_b_live import Engines
    return Engines(detector or ScriptedWake(fire), vad or EnergyVad(), FakeStt(reply, release))


__all__ = ["BLOCK", "CHUNK", "Boom", "EnergyVad", "EnergyVad16", "FakeBlockSource", "FakeStt", "FakeStt16", "GUARD_S", "Hold",
           "ScriptedWake", "ScriptedWake16", "Timeout", "engines", "make_blocks", "make_config", "stream"]
