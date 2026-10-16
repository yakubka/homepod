"""Tests for the audio acquisition module.

Hardware-independent tests always run. Tests that need the actual
ReSpeaker XVF3800 attached are skipped (not failed) when no matching
input device is found, so this suite passes on a dev machine without
the hardware and exercises the real device when run on the Raspberry
Pi 5.
"""

import numpy as np
import pytest

from audio.base import AudioSource
from audio.microphone import DeviceNotFoundError, ReSpeakerMicrophone, find_device
from config import settings


def _respeaker_available() -> bool:
    try:
        find_device(settings.DEVICE_NAME_HINT)
        return True
    except DeviceNotFoundError:
        return False


requires_respeaker = pytest.mark.skipif(
    not _respeaker_available(),
    reason="ReSpeaker XVF3800 not detected on this machine",
)


def test_audio_source_is_abstract():
    """AudioSource is an interface - it must not be instantiable directly."""
    with pytest.raises(TypeError):
        AudioSource()


def test_find_device_raises_when_missing():
    """Device discovery must fail clearly, not silently pick a wrong device."""
    with pytest.raises(DeviceNotFoundError):
        find_device(name_hint="definitely-not-a-real-device-xyz")


@requires_respeaker
def test_find_device_locates_respeaker():
    index = find_device(settings.DEVICE_NAME_HINT)
    assert isinstance(index, int)
    assert index >= 0


@requires_respeaker
def test_stream_open_close():
    mic = ReSpeakerMicrophone()
    mic.start()
    assert mic.is_running
    mic.stop()
    assert not mic.is_running


@requires_respeaker
def test_recording_produces_frames():
    mic = ReSpeakerMicrophone()
    mic.start()
    try:
        frame = mic.read_frame(timeout=3.0)
    finally:
        mic.stop()

    assert isinstance(frame, np.ndarray)
    assert frame.dtype == np.int16
    assert frame.shape[0] == settings.BLOCK_SIZE
