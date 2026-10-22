"""Week 7 Task 4: real-component PLUMBING run of the voice pipeline (speech/pipeline.py).

WavFileSource (a generated 16 kHz WAV) + the real Silero VAD adapter + (second test) the real faster-whisper
engine, driven by a SCRIPTED wake detector (the real openWakeWord engine cannot detect a synthetic signal, and
it may be unimportable on this machine). The audio is a SYNTHETIC voiced signal (formant-filtered harmonic pulse
train with a syllable-rate envelope), NOT speech. These tests prove the components are wired, that indices line
up on the master clock and that the objects flow; they claim NOTHING about wake, VAD or recognition accuracy,
WER, a microphone, a ReSpeaker or a Raspberry Pi. They skip when a model is not on this machine (git-ignored
artifacts; one-time acquisition documented in WEEK7_REPORT.md / TEAM_LEADER_AI_REPORT.md).
"""

import math
import wave

import numpy as np
import pytest

from audio.file_source import WavFileSource
from audio.silero_vad import SileroModelNotFound, SileroOnnxVadEngine, SileroRuntimeUnavailable
from audio.utterance import SegmenterConfig, UtteranceSegmenter
from speech.pipeline import PipelineStatus, VoicePipeline
from speech.stt import SttEngine, SttModelNotFound, SttRuntimeUnavailable, SttStatus
from wakeword.detector import WakeWordDetector

RATE = 16000
CHUNK = 1280                         # 80 ms, the openWakeWord chunk size
LEAD_S, VOICED_S, TAIL_S = 0.6, 2.0, 1.6
WAKE_CHUNK = 12                      # chunk 12 ends at 13 * 1280 = 16640 samples = 1.04 s: inside the voiced region


def synthetic_voiced(seconds: float, f0: float = 120.0) -> np.ndarray:
    """A deterministic voiced-like signal (NOT speech): harmonic pulse train -> 3 resonators -> syllable envelope."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.05 * np.sin(2 * np.pi * 3 * t))) / RATE
    source = sum(np.sin(k * phase) / k for k in range(1, 30))

    def resonator(x, centre, bandwidth):
        r = math.exp(-math.pi * bandwidth / RATE)
        a1, a2 = -2 * r * math.cos(2 * math.pi * centre / RATE), r * r
        y = np.zeros_like(x)
        for i in range(2, x.size):
            y[i] = x[i] - a1 * y[i - 1] - a2 * y[i - 2]
        return y

    y = resonator(source, 700, 100) + resonator(source, 1200, 120) + 0.6 * resonator(source, 2600, 200)
    y = y * 0.5 * (1 + np.sin(2 * np.pi * 4 * t))
    return (y / np.abs(y).max() * 0.6 * 32767).astype(np.int16)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    lead = np.zeros(int(LEAD_S * RATE), np.int16)
    voiced = synthetic_voiced(VOICED_S)
    tail = np.zeros(int(TAIL_S * RATE), np.int16)
    pcm = np.concatenate((lead, voiced, tail))
    path = tmp_path_factory.mktemp("pipeline_real") / "synthetic_voiced.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm.astype("<i2").tobytes())
    return path, pcm, lead.size, lead.size + voiced.size


class ScriptedWake16k(WakeWordDetector):
    """SIMULATED wake: a real WakeWordDetector base firing on one scripted chunk."""

    sample_rate = RATE
    chunk_samples = CHUNK

    def _score_chunk(self, chunk):
        return {"hey_jarvis": 0.9 if self._chunks_processed == WAKE_CHUNK else 0.0}

    def _reset_backend(self):
        pass


def segmenter_for(vad):
    """An explicit TEST configuration (NOT a calibrated production value)."""
    return UtteranceSegmenter(SegmenterConfig(
        sample_rate=RATE, vad_frame_samples=vad.frame_samples, start_threshold=0.5, end_threshold=0.35,
        start_confirmation_s=0.064, end_silence_s=0.6, onset_padding_s=0.1, pre_roll_s=1.0,
        no_speech_timeout_s=3.0, min_utterance_s=0.2, max_utterance_s=10.0,
    ), vad)


@pytest.fixture(scope="module")
def silero():
    try:
        return SileroOnnxVadEngine()
    except SileroModelNotFound:
        pytest.skip("models/silero_vad_v6.2.3.onnx is not present (git-ignored; one-time acquisition not done here)")
    except SileroRuntimeUnavailable as exc:
        pytest.skip(f"onnxruntime cannot be imported on this machine: {exc}")


class RecordingStt(SttEngine):
    sample_rate = RATE
    engine_id = "recording-stt"
    model_id = "recording-model"

    def __init__(self):
        super().__init__()
        self.calls = []

    def _transcribe(self, samples):
        self.calls.append(np.array(samples))
        return "hey jarvis turn on the lights"


def test_wav_source_real_silero_and_a_scripted_wake_produce_an_aligned_segment(clip, silero):
    path, pcm, voiced_start, voiced_end = clip
    stt = RecordingStt()
    pipeline = VoicePipeline(ScriptedWake16k(), segmenter_for(silero), stt)
    results = pipeline.run_source(WavFileSource(path))
    assert len(results) == 1
    r = results[0]
    assert r.activation_sample == (WAKE_CHUNK + 1) * CHUNK and r.activation_clamped is False
    assert r.status in (PipelineStatus.TRANSCRIBED, PipelineStatus.MAX_DURATION), r.status
    assert r.normalized_text == "turn on the lights" and r.wake_prefix_removed is True
    # plumbing only: the segment lies on the master clock and overlaps the synthetic voiced region
    assert r.segment_start_sample < voiced_end and r.segment_end_sample > voiced_start
    assert 0 <= r.segment_start_sample and r.segment_end_sample <= pcm.size
    (heard,) = stt.calls
    assert np.array_equal(heard, pcm[r.segment_start_sample:r.segment_end_sample])
    assert pipeline.master_samples == pcm.size           # real samples only: no padding on the master clock


def test_silence_through_the_real_vad_is_no_speech_and_stt_is_not_called(tmp_path, silero):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(np.zeros(4 * RATE, "<i2").tobytes())
    stt = RecordingStt()
    results = VoicePipeline(ScriptedWake16k(), segmenter_for(silero), stt).run_source(WavFileSource(path))
    assert [r.status for r in results] == [PipelineStatus.NO_SPEECH] and stt.calls == []


def test_wav_source_real_silero_and_real_whisper_flow_end_to_end_as_plumbing(clip, silero):
    """The whole chain on synthetic audio. The decoded text (if any) is whatever the model emits for a non-speech
    signal and is NOT asserted: this is wiring, not an accuracy or hallucination measurement."""
    from speech.faster_whisper_backend import FASTER_WHISPER_TINY_EN, FasterWhisperEngine

    try:
        whisper = FasterWhisperEngine()
    except SttModelNotFound:
        pytest.skip("models/faster-whisper-tiny.en-0d3d19a3 is not present (one-time acquisition not done here)")
    except SttRuntimeUnavailable as exc:
        pytest.skip(f"faster-whisper cannot be imported on this machine: {exc}")
    path, pcm, voiced_start, voiced_end = clip
    pipeline = VoicePipeline(ScriptedWake16k(), segmenter_for(silero), whisper)
    results = pipeline.run_source(WavFileSource(path))
    assert len(results) == 1
    r = results[0]
    assert r.status in (PipelineStatus.TRANSCRIBED, PipelineStatus.WAKE_ONLY, PipelineStatus.STT_EMPTY,
                        PipelineStatus.MAX_DURATION), r.status
    assert r.stt_engine_id == "faster-whisper" and r.model_id == FASTER_WHISPER_TINY_EN.model_id
    assert r.raw_stt_status in (SttStatus.OK, SttStatus.EMPTY_TEXT) and isinstance(r.normalized_text, str)
    assert r.timings.stt_inference_ms is not None and math.isfinite(r.timings.stt_inference_ms) and r.timings.stt_inference_ms > 0
    assert 0 <= r.segment_start_sample < r.segment_end_sample <= pcm.size and r.validation_label == "SOFTWARE-VALIDATED"
