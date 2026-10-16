"""Audio acquisition package: interfaces and backends for capturing PCM
audio from the ReSpeaker XVF3800 (or other future microphone hardware).
"""

from audio.base import AudioSource
from audio.microphone import DeviceNotFoundError, ReSpeakerMicrophone, find_device

__all__ = [
    "AudioSource",
    "ReSpeakerMicrophone",
    "DeviceNotFoundError",
    "find_device",
]
