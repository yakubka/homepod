"""Shared TEST doubles and fixtures for the Week 7 Task 5 evaluator tests.

Everything is synthetic or fake. NOTHING here is a production VAD, STT or corpus, and nothing here says anything about
recognition, VAD or wake accuracy. 16 kHz mono audio, 512-sample VAD frames, 1280-sample (80 ms) wake chunks.
"""

import wave
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from audio.vad import VadEngine
from evaluation import week7_voice_eval as ev
from speech.stt import SttEngine
from wakeword.detector import WakeWordDetector

SR = 16000
FRAME = 512
CHUNK = 1280

TEST_SEGMENTER_VALUES = {
    "start_threshold": 0.5, "end_threshold": 0.35, "start_confirmation_s": 0.064, "end_silence_s": 0.2,
    "onset_padding_s": 0.05, "pre_roll_s": 1.0, "no_speech_timeout_s": 3.0, "min_utterance_s": 0.2, "max_utterance_s": 10.0,
}


def clip(seconds: float = 4.0, speech: Sequence = ((1.0, 2.5),)) -> np.ndarray:
    """Silence with loud, index-coded regions (every sample >= 2000), so a segment can be compared with the stream."""
    x = np.zeros(int(seconds * SR), np.int16)
    for a, b in speech:
        lo, hi = int(a * SR), int(b * SR)
        x[lo:hi] = (2000 + np.arange(lo, hi) % 1000).astype(np.int16)
    return x


def write_wav(path: Path, x: np.ndarray, rate: int = SR, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.asarray(x, "<i2").tobytes())
    return path


class EnergyVad16k(VadEngine):
    """Speech score tied to the audio: 0.9 when every sample of the frame is loud, else 0.0."""

    sample_rate = SR
    frame_samples = FRAME

    def __init__(self):
        self.frames = 0

    def _score_frame(self, frame):
        self.frames += 1
        return 0.9 if int(frame.min()) >= 1000 else 0.0

    def reset(self):
        pass


class SeqStt(SttEngine):
    """Returns the scripted replies in call order; an Exception instance in the list is raised instead."""

    sample_rate = SR
    engine_id = "fake-stt"
    model_id = "fake-model@test"
    max_audio_s = 30.0
    snapshot_verified = True

    def __init__(self, replies: Sequence = ("hey jarvis turn on the lights",)):
        super().__init__()
        self.replies = list(replies)
        self.calls: List[np.ndarray] = []

    def _transcribe(self, samples):
        self.calls.append(np.array(samples))
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


class FiresAt(WakeWordDetector):
    """A scripted 'real-style' detector for detector mode: fires once on chunk `chunk` (or never when None)."""

    sample_rate = SR
    chunk_samples = CHUNK

    def __init__(self, chunk: Optional[int] = 10):
        self._chunk = chunk
        super().__init__(threshold=0.5, cooldown_s=0.0)

    def _score_chunk(self, chunk):
        return {"hey_jarvis": 0.9 if self._chunk is not None and self._chunks_processed == self._chunk else 0.0}

    def _reset_backend(self):
        pass


class FakeClock:
    """Advances 1 ms per call so durations are deterministic."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.001
        return self.now


def make_components(replies=("hey jarvis turn on the lights",), values=None, source=None, **kw) -> ev.EvalComponents:
    vad = EnergyVad16k()
    values = dict(TEST_SEGMENTER_VALUES if values is None else values)
    return ev.EvalComponents(
        vad=vad, stt=SeqStt(replies), segmenter_config=ev.segmenter_config_from_values(values, FRAME),
        segmenter_source=source or {"kind": "TEST", "sha256": ev.config_fingerprint(values), "frozen": False},
        startup={"silero_construct_ms": 1.0}, **kw,
    )


def synth_rec(rid: str, path, category: str = "WAKE_IMMEDIATE_COMMAND", **over) -> dict:
    """A SYNTHETIC manifest entry (consent NOT_APPLICABLE)."""
    non_speech = category in ev.NON_SPEECH_CATEGORIES
    d = {
        "recording_id": rid, "audio_path": str(path), "speaker_code": "NOT_APPLICABLE", "consent_reference": "NOT_APPLICABLE",
        "recording_origin": "SYNTHETIC", "microphone_or_device": "generated", "distance_condition": "NOT_APPLICABLE",
        "noise_condition": "none", "category": category,
        "expected_wake_phrase": "hey jarvis" if category in ev.WAKE_REQUIRED_CATEGORIES else "",
        "reference_command_text": "" if non_speech else "turn on the lights", "split": "DEV", "notes": "test fixture",
    }
    if category in ev.WAKE_REQUIRED_CATEGORIES:
        d["wake_end_sample"] = 20000
    d.update(over)
    return d


def real_rec(rid: str, path, category: str = "WAKE_IMMEDIATE_COMMAND", **over) -> dict:
    """An entry that DECLARES a real human recording (the audio file is still a test fixture)."""
    d = synth_rec(rid, path, category)
    d.update({"speaker_code": "S01", "consent_reference": "CONSENT-S01", "recording_origin": "HARDWARE_ENGINEER_RECORDING",
              "microphone_or_device": "test-mic", "distance_condition": "1 m", "noise_condition": "quiet"})
    d.update(over)
    return d


def manifest_dict(recordings: Sequence[dict], corpus_id: str = "TEST-CORPUS", **extra) -> dict:
    return {"schema_version": ev.SCHEMA_MANIFEST, "corpus_id": corpus_id, "recordings": list(recordings), **extra}


def parse(recordings: Sequence[dict], base_dir: Path, **kw) -> ev.Manifest:
    return ev.parse_manifest(manifest_dict(recordings), base_dir, **kw)
