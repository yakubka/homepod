"""WAV-file replay backend for the AudioSource interface (VESTA, WP2.1).

Lets the wake-word pipeline (and later VAD/STT) be developed and
benchmarked from recorded audio while the Raspberry Pi + ReSpeaker
XVF3800 hardware is unavailable. It deliberately mimics what the
ReSpeakerMicrophone backend delivers, so consumer code cannot tell the
difference:

  * blocks of `block_size` samples, int16, shape (n, 1) - the same
    (frames, channels) layout sounddevice produces;
  * 16 kHz mono, as configured in config/settings.py.

Two replay modes:

  * fast (default): blocks are returned as quickly as the consumer asks.
    Use for accuracy benchmarks - a 1 hour file is processed in seconds.
  * realtime: each block is released only once it "would have been
    recorded", so CPU/latency measurements see a realistic arrival rate.

Stream time is always derived from a sample counter (samples_delivered /
sample_rate), never from the wall clock, so results are reproducible in
either mode.
"""

import time
import wave
from pathlib import Path
from typing import Callable, Iterator, Optional, Union

import numpy as np

from audio.base import AudioSource
from config import settings
from config.logging_config import setup_logging

logger = setup_logging()


class EndOfAudio(Exception):
    """Raised by read_frame() once every sample of the file was delivered.

    Deliberately NOT a subclass of queue.Empty: the microphone backend
    raises queue.Empty for a *timeout* (a live stream stalled), and a
    consumer must never mistake the end of a finite file for that.
    """


class WavFileSource(AudioSource):
    """AudioSource that replays a 16 kHz mono int16 WAV file."""

    def __init__(
        self,
        path: Union[str, Path],
        block_size: int = settings.BLOCK_SIZE,
        realtime: bool = False,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """clock/sleep are injectable so pacing can be unit-tested without
        actually waiting; production code should leave the defaults."""
        if block_size <= 0:
            raise ValueError(f"block_size must be positive, got {block_size}")

        self._path = Path(path)
        self._block_size = block_size
        self._realtime = realtime
        self._clock = clock
        self._sleep = sleep

        self._wav: Optional[wave.Wave_read] = None
        self._samples_delivered = 0
        self._eof = False
        self._start_clock = 0.0

        # Validate the header now so a bad file fails at construction,
        # not halfway through a benchmark run.
        with self._open_validated() as wav:
            self._sample_rate = wav.getframerate()
            self._total_samples = wav.getnframes()

    # -- file handling -------------------------------------------------

    def _open_validated(self) -> wave.Wave_read:
        try:
            wav = wave.open(str(self._path), "rb")
        except (wave.Error, EOFError) as exc:
            # The stdlib wave module only reads integer PCM; float or
            # compressed WAVs land here.
            raise ValueError(f"{self._path}: not a readable PCM WAV file ({exc})") from exc

        problems = []
        if wav.getnchannels() != settings.CHANNELS:
            problems.append(f"{wav.getnchannels()} channels (need {settings.CHANNELS})")
        if wav.getsampwidth() != 2:
            problems.append(f"{8 * wav.getsampwidth()}-bit samples (need 16-bit)")
        if wav.getframerate() != settings.TARGET_SAMPLE_RATE:
            problems.append(f"{wav.getframerate()} Hz (need {settings.TARGET_SAMPLE_RATE} Hz)")
        if problems:
            wav.close()
            raise ValueError(f"{self._path}: unsupported WAV format: " + ", ".join(problems))
        return wav

    # -- AudioSource interface ----------------------------------------

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return settings.CHANNELS

    def start(self) -> None:
        """Open the file and begin replay from sample 0. Calling start()
        while already running is a no-op, like ReSpeakerMicrophone."""
        if self._wav is not None:
            return
        self._wav = self._open_validated()
        self._samples_delivered = 0
        self._eof = False
        self._start_clock = self._clock()
        logger.info(
            "event=wav_source_start path=%s total_samples=%d duration_s=%.3f realtime=%s block_size=%d",
            self._path.name, self._total_samples, self.duration_s, self._realtime, self._block_size,
        )

    def stop(self) -> None:
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        logger.info("event=wav_source_stop samples_delivered=%d", self._samples_delivered)

    def read_frame(self, timeout: float = 1.0) -> np.ndarray:
        """Return the next block, shape (n, 1) int16 (n == block_size except
        for the final block, which is returned short rather than padded so
        no invented samples enter the stream).

        Raises EndOfAudio when the file is exhausted (and on every call
        after that). `timeout` is accepted for interface compatibility but
        ignored: a file never stalls.
        """
        if self._wav is None:
            raise RuntimeError("WavFileSource.read_frame() called before start()")
        if self._eof:
            raise EndOfAudio(f"end of {self._path.name}")

        raw = self._wav.readframes(self._block_size)
        if not raw:
            self._eof = True
            logger.info("event=wav_source_eof samples_delivered=%d", self._samples_delivered)
            raise EndOfAudio(f"end of {self._path.name}")

        # WAV data is little-endian; astype() also gives a writable copy
        # (frombuffer on bytes is read-only).
        block = np.frombuffer(raw, dtype="<i2").astype(np.int16).reshape(-1, 1)
        self._samples_delivered += block.shape[0]

        if self._realtime:
            # A real mic can only hand over a block after it has been
            # recorded. Pace against an absolute deadline (not "sleep one
            # block length each time") so rounding error cannot accumulate.
            # A slow consumer gets no catch-up sleep, like a mic backlog.
            deadline = self._start_clock + self._samples_delivered / self._sample_rate
            delay = deadline - self._clock()
            if delay > 0:
                self._sleep(delay)

        return block

    # -- extras (not part of AudioSource) ------------------------------

    @property
    def is_running(self) -> bool:
        return self._wav is not None

    @property
    def realtime(self) -> bool:
        return self._realtime

    @property
    def total_samples(self) -> int:
        return self._total_samples

    @property
    def duration_s(self) -> float:
        return self._total_samples / self._sample_rate

    @property
    def samples_delivered(self) -> int:
        """Deterministic stream position: samples handed out so far."""
        return self._samples_delivered

    @property
    def stream_time_s(self) -> float:
        """Stream position in seconds, from the sample counter only."""
        return self._samples_delivered / self._sample_rate

    def frames(self) -> Iterator[np.ndarray]:
        """Yield blocks until EOF. The source must already be started."""
        while True:
            try:
                yield self.read_frame()
            except EndOfAudio:
                return

    def __enter__(self) -> "WavFileSource":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
