"""ReSpeaker XVF3800 audio capture backend.

Implements AudioSource (audio/base.py) using sounddevice/PortAudio. This
is the only module that should import sounddevice directly - everything
else in the pipeline depends on the AudioSource interface, so the
backend can be replaced later (e.g. with a raw ALSA implementation)
without touching consumer code.
"""

import queue
import time
from typing import Optional

import numpy as np
import sounddevice as sd

from audio.base import AudioSource
from config import settings
from config.logging_config import setup_logging

logger = setup_logging()


class DeviceNotFoundError(RuntimeError):
    """Raised when no input device matches the configured name hint."""


def find_device(name_hint: str = settings.DEVICE_NAME_HINT) -> int:
    """Return the sounddevice index of the first input device whose name
    contains name_hint (case-insensitive).

    Never hardcodes a device index: the index sounddevice assigns can
    change across reboots or when the USB port changes, so devices are
    always looked up by name at start() time instead.
    """
    devices = sd.query_devices()

    for index, device in enumerate(devices):
        if device["max_input_channels"] > 0 and name_hint.lower() in device["name"].lower():
            logger.info(
                "event=device_detected device_index=%d device_name=%r",
                index, device["name"],
            )
            return index

    available = [d["name"] for d in devices if d["max_input_channels"] > 0]
    logger.error("event=device_not_found hint=%r available=%r", name_hint, available)
    raise DeviceNotFoundError(
        f"No input device found matching hint {name_hint!r}. "
        f"Available input devices: {available}"
    )


class ReSpeakerMicrophone(AudioSource):
    """AudioSource implementation for the ReSpeaker XVF3800 over sounddevice."""

    def __init__(
        self,
        device_name_hint: str = settings.DEVICE_NAME_HINT,
        sample_rate: int = settings.TARGET_SAMPLE_RATE,
        channels: int = settings.CHANNELS,
        block_size: int = settings.BLOCK_SIZE,
        dtype: str = settings.DTYPE,
    ) -> None:
        self._device_name_hint = device_name_hint
        self._sample_rate = sample_rate
        self._channels = channels
        self._block_size = block_size
        self._dtype = dtype

        self._device_index: Optional[int] = None
        self._stream: Optional[sd.InputStream] = None
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=settings.QUEUE_MAX_BLOCKS)

        self._start_time: Optional[float] = None
        self._first_frame_logged = False
        self._running = False

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def is_running(self) -> bool:
        return self._running

    def _callback(self, indata, frames, time_info, status) -> None:
        # Runs on PortAudio's real-time thread - must stay fast. No
        # heavy processing here, only queue bookkeeping and short logs.
        if status:
            logger.error("event=stream_error detail=%s", status)

        if self._start_time is not None and not self._first_frame_logged:
            latency_ms = (time.monotonic() - self._start_time) * 1000
            self._first_frame_logged = True
            logger.info("event=first_frame_latency latency_ms=%.1f", latency_ms)

        block = indata.copy()
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            # Drop the oldest buffered block rather than block the
            # real-time callback or grow memory unbounded.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(block)

    def start(self) -> None:
        if self._running:
            return

        self._device_index = find_device(self._device_name_hint)
        self._start_time = time.monotonic()
        self._first_frame_logged = False

        try:
            self._stream = sd.InputStream(
                device=self._device_index,
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype=self._dtype,
                blocksize=self._block_size,
                callback=self._callback,
            )
            self._stream.start()
            self._running = True
            logger.info(
                "event=stream_start device_index=%d sample_rate=%d channels=%d block_size=%d",
                self._device_index, self._sample_rate, self._channels, self._block_size,
            )
        except Exception as exc:
            logger.error("event=stream_start_failed error=%s", exc)
            # Release a stream that was constructed but failed to start,
            # so the device is not left held open by a half-started object.
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            raise

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.error("event=stream_stop_error error=%s", exc)
            finally:
                self._stream = None

        self._running = False
        logger.info("event=stream_stop")

    def read_frame(self, timeout: float = 1.0) -> np.ndarray:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            logger.error("event=read_frame_timeout timeout=%.1f", timeout)
            raise
