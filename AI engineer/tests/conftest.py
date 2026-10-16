"""Shared pytest fixtures for the VESTA test suite."""

import wave

import numpy as np
import pytest


@pytest.fixture
def write_wav(tmp_path):
    """Factory: write int16 samples (or raw bytes) to a WAV file, return its path.

    Defaults give the project format (16 kHz, mono, 16-bit). Override
    rate/channels/sampwidth to build deliberately unsupported files.
    """

    def _write(samples=None, name="clip.wav", rate=16000, channels=1, sampwidth=2, raw=None):
        path = tmp_path / name
        if raw is None:
            raw = np.asarray(samples, dtype="<i2").tobytes()
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(sampwidth)
            wav.setframerate(rate)
            wav.writeframes(raw)
        return path

    return _write
