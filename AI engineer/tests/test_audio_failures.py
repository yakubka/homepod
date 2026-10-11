"""Week 3: explicit-failure behaviour of the live microphone path.

Mock-based (SOFTWARE-VALIDATED): sounddevice is replaced, so these prove
how our code reacts to failures, not how PortAudio/ALSA behaves on the
Pi. The one hardware-gated test at the bottom feeds real ReSpeaker frames
through the existing Reframer and skips on machines without the device.
"""

import logging
import queue

import numpy as np
import pytest

from audio import microphone as mic_mod
from audio.microphone import DeviceNotFoundError, ReSpeakerMicrophone
from audio.reframer import Reframer
from tests.test_audio_acquisition import requires_respeaker


class _LogCapture(logging.Handler):
    """The homepod_ai logger has propagate=False, so caplog cannot see it."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    mic_mod.logger.addHandler(handler)
    yield handler.lines
    mic_mod.logger.removeHandler(handler)


class _FakeStream:
    def __init__(self, fail_on_start=False, **kwargs):
        self.fail_on_start = fail_on_start
        self.closed = False

    def start(self):
        if self.fail_on_start:
            raise RuntimeError("simulated PortAudio start failure")

    def stop(self):
        pass

    def close(self):
        self.closed = True


def _one_fake_device(monkeypatch):
    monkeypatch.setattr(
        mic_mod.sd, "query_devices",
        lambda: [{"name": "Fake ReSpeaker", "max_input_channels": 1}],
    )


def test_no_matching_device_fails_explicitly(monkeypatch, logs):
    # A built-in laptop mic must never be picked for a "ReSpeaker" hint.
    monkeypatch.setattr(
        mic_mod.sd, "query_devices",
        lambda: [{"name": "Microphone Array (AMD Audio Device)", "max_input_channels": 2}],
    )
    mic = ReSpeakerMicrophone()
    with pytest.raises(DeviceNotFoundError):
        mic.start()
    assert not mic.is_running
    assert any("event=device_not_found" in line for line in logs)


def test_stream_open_failure_is_raised_and_logged(monkeypatch, logs):
    _one_fake_device(monkeypatch)

    def boom(**kwargs):
        raise OSError("simulated: invalid sample rate")

    monkeypatch.setattr(mic_mod.sd, "InputStream", boom)
    mic = ReSpeakerMicrophone()
    with pytest.raises(OSError):
        mic.start()
    assert not mic.is_running
    assert any("event=stream_start_failed" in line for line in logs)


def test_stream_start_failure_releases_the_stream(monkeypatch, logs):
    _one_fake_device(monkeypatch)
    created = []

    def factory(**kwargs):
        created.append(_FakeStream(fail_on_start=True, **kwargs))
        return created[0]

    monkeypatch.setattr(mic_mod.sd, "InputStream", factory)
    mic = ReSpeakerMicrophone()
    with pytest.raises(RuntimeError):
        mic.start()
    assert not mic.is_running
    assert created[0].closed, "a stream that failed to start must not stay open"


def test_read_timeout_raises_and_never_returns_fake_audio(logs):
    mic = ReSpeakerMicrophone()  # never started: queue stays empty
    with pytest.raises(queue.Empty):
        mic.read_frame(timeout=0.05)
    assert any("event=read_frame_timeout" in line for line in logs)


def test_stream_status_flags_are_logged(logs):
    mic = ReSpeakerMicrophone()
    block = np.zeros((1024, 1), dtype=np.int16)
    mic._callback(block, 1024, None, "input overflow")
    assert any("event=stream_error" in line and "overflow" in line for line in logs)
    assert mic.read_frame(timeout=0.05).shape == (1024, 1)  # real block still delivered


@requires_respeaker
def test_live_frames_satisfy_the_reframer_contract():
    mic = ReSpeakerMicrophone()
    reframer = Reframer(1280)  # OpenWakeWordDetector.CHUNK_SAMPLES
    chunks = []
    mic.start()
    try:
        for _ in range(40):  # ~2.5 s of audio
            chunks += reframer.push(mic.read_frame(timeout=3.0))
    finally:
        mic.stop()
    assert chunks
    assert all(c.dtype == np.int16 and c.shape == (1280,) for c in chunks)
    assert reframer.samples_in == reframer.samples_out + reframer.pending_samples
