"""ReSpeaker XVF3800 audio capture backend.

Implements AudioSource (audio/base.py) using sounddevice/PortAudio. This
is the only module that should import sounddevice directly - everything
else in the pipeline depends on the AudioSource interface, so the
backend can be replaced later (e.g. with a raw ALSA implementation)
without touching consumer code.

Position telemetry (additive, Team Leader AI-2 Stage B): read_block() returns the same samples as read_frame() plus
their exact position in the callback's own sample count (MicBlock), and telemetry() reports cumulative counters,
including blocks dropped when the queue is full. read_frame() and the AudioSource interface are unchanged.
"""

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd

from audio.base import AudioSource
from config import settings
from config.logging_config import setup_logging

logger = setup_logging()


@dataclass(frozen=True, eq=False)
class MicBlock:
    """One callback block plus its exact position in the callback's own sample count.

    Positions are counted from the frames PortAudio DELIVERED to the callback, never from a clock:
      * start_sample - index of the first frame, counted from 0 at start() of this epoch. Blocks dropped from the
        queue still advance it, so a dropped block shows up as a jump in start_sample of exactly its frame count.
      * sequence     - 0-based callback count in this epoch (same idea, in blocks).
      * epoch        - incremented by every start(); positions restart at 0 in a new epoch.
      * overflow     - PortAudio flagged input_overflow on this callback: the DEVICE lost an UNKNOWN number of frames
        immediately BEFORE this block. The size is not reported by PortAudio and is never guessed here.
    Not detectable: loss below PortAudio (driver / USB) that it does not flag.
    """

    samples: np.ndarray          # (frames, channels), same array read_frame() returns
    sequence: int
    start_sample: int
    epoch: int
    overflow: bool


@dataclass(frozen=True)
class MicTelemetry:
    """Counters since construction (epoch is the number of start() calls so far)."""

    epoch: int
    blocks_received: int         # callbacks seen
    samples_received: int        # frames delivered to the callback
    blocks_dropped: int          # oldest blocks discarded because the queue was full
    samples_dropped: int
    overflow_events: int         # callbacks flagged input_overflow by PortAudio
    queue_depth: int
    queue_max_blocks: int


def _is_input_overflow(status) -> bool:
    """True when PortAudio's status says input overflow. Accepts CallbackFlags or a plain string (as tests pass)."""
    if not status:
        return False
    flag = getattr(status, "input_overflow", None)
    if flag is not None:
        return bool(flag)
    return "overflow" in str(status).lower()


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
        queue_max_blocks: int = settings.QUEUE_MAX_BLOCKS,
    ) -> None:
        if isinstance(queue_max_blocks, bool) or not isinstance(queue_max_blocks, int) or queue_max_blocks < 1:
            raise ValueError(f"queue_max_blocks must be an integer >= 1, got {queue_max_blocks!r}")
        self._device_name_hint = device_name_hint
        self._sample_rate = sample_rate
        self._channels = channels
        self._block_size = block_size
        self._dtype = dtype

        self._device_index: Optional[int] = None
        self._stream: Optional[sd.InputStream] = None
        self._queue: "queue.Queue[MicBlock]" = queue.Queue(maxsize=queue_max_blocks)

        # Telemetry. Written only inside _lock (callback thread); read by telemetry() under the same lock.
        self._lock = threading.Lock()
        self._epoch = 0
        self._epoch_sequence = 0          # next MicBlock.sequence in this epoch
        self._epoch_position = 0          # next MicBlock.start_sample in this epoch
        self._blocks_received = 0
        self._samples_received = 0
        self._blocks_dropped = 0
        self._samples_dropped = 0
        self._overflow_events = 0

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

        samples = indata.copy()
        frames_in_block = int(samples.shape[0])
        overflow = _is_input_overflow(status)
        with self._lock:
            entry = MicBlock(samples, self._epoch_sequence, self._epoch_position, self._epoch, overflow)
            # Positions advance for EVERY delivered block, including one dropped below, so a drop is always
            # visible to the consumer as a jump in start_sample.
            self._epoch_sequence += 1
            self._epoch_position += frames_in_block
            self._blocks_received += 1
            self._samples_received += frames_in_block
            if overflow:
                self._overflow_events += 1
            while True:
                try:
                    self._queue.put_nowait(entry)
                    break
                except queue.Full:
                    # Drop the oldest buffered block rather than block the real-time callback or grow
                    # memory unbounded - but COUNT it (the consumer may also see it as a position jump).
                    try:
                        oldest = self._queue.get_nowait()
                    except queue.Empty:
                        continue            # the consumer just made room
                    self._blocks_dropped += 1
                    self._samples_dropped += int(oldest.samples.shape[0])
                    if self._blocks_dropped == 1 or self._blocks_dropped % 100 == 0:
                        logger.error("event=mic_queue_overflow blocks_dropped=%d samples_dropped=%d",
                                     self._blocks_dropped, self._samples_dropped)

    def telemetry(self) -> MicTelemetry:
        """A consistent snapshot of the counters (see MicBlock / MicTelemetry for the exact semantics)."""
        with self._lock:
            return MicTelemetry(self._epoch, self._blocks_received, self._samples_received, self._blocks_dropped,
                                self._samples_dropped, self._overflow_events, self._queue.qsize(), self._queue.maxsize)

    def start(self) -> None:
        if self._running:
            return

        self._device_index = find_device(self._device_name_hint)
        self._start_time = time.monotonic()
        self._first_frame_logged = False
        with self._lock:
            # A new epoch is a new stream: positions restart at 0 and blocks left over from a previous run are
            # discarded so they can never be mistaken for the start of this one. Counters stay cumulative.
            self._epoch += 1
            self._epoch_sequence = 0
            self._epoch_position = 0
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

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
            return self._queue.get(timeout=timeout).samples
        except queue.Empty:
            logger.error("event=read_frame_timeout timeout=%.1f", timeout)
            raise

    def read_block(self, timeout: float = 1.0) -> MicBlock:
        """Like read_frame() but with the block's position metadata. Same queue: use one or the other per run."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            logger.error("event=read_block_timeout timeout=%.1f", timeout)
            raise
