"""Demo: ReSpeaker XVF3800 -> capture PCM -> save WAV file.

Run on the Raspberry Pi 5 with the ReSpeaker XVF3800 connected:

    python record_demo.py [duration_seconds] [output_path]

Defaults to a 5 second recording saved to demo_recording.wav.
"""

import sys
import time
import wave

from audio.microphone import ReSpeakerMicrophone
from config.logging_config import setup_logging

logger = setup_logging()


def record(duration_seconds: float, output_path: str) -> None:
    mic = ReSpeakerMicrophone()
    frames = []

    mic.start()
    try:
        start = time.monotonic()
        while time.monotonic() - start < duration_seconds:
            frame = mic.read_frame(timeout=2.0)
            frames.append(frame)
    finally:
        mic.stop()

    audio_bytes = b"".join(frame.tobytes() for frame in frames)

    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(mic.channels)
        wav_file.setsampwidth(2)  # int16 PCM = 2 bytes per sample
        wav_file.setframerate(mic.sample_rate)
        wav_file.writeframes(audio_bytes)

    logger.info(
        "event=recording_saved path=%s duration_s=%.1f",
        output_path, duration_seconds,
    )


if __name__ == "__main__":
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    output = sys.argv[2] if len(sys.argv) > 2 else "demo_recording.wav"
    record(duration, output)
