"""Tests for the WAV replay source (audio/file_source.py). No hardware needed."""

import queue

import numpy as np
import pytest

from audio.base import AudioSource
from audio.file_source import EndOfAudio, WavFileSource
from config import settings


def _samples(n, seed=0):
    return np.random.default_rng(seed).integers(-32768, 32767, size=n, dtype=np.int16)


class FakeClock:
    """Manual clock: sleep() advances time instantly and records the total."""

    def __init__(self):
        self.t = 0.0
        self.slept = 0.0
        self.sleep_calls = 0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleep_calls += 1
        self.slept += seconds
        self.t += seconds


def test_is_an_audio_source(write_wav):
    src = WavFileSource(write_wav(_samples(100)))
    assert isinstance(src, AudioSource)
    assert src.sample_rate == 16000
    assert src.channels == 1


def test_blocks_match_wp1_format(write_wav):
    """Blocks must look like ReSpeakerMicrophone output: (block_size, 1) int16."""
    src = WavFileSource(write_wav(_samples(3000)))
    src.start()
    try:
        block = src.read_frame()
    finally:
        src.stop()
    assert isinstance(block, np.ndarray)
    assert block.dtype == np.int16
    assert block.shape == (settings.BLOCK_SIZE, 1)


def test_all_samples_delivered_exactly_in_order(write_wav):
    original = _samples(16000 * 2 + 357)  # not a multiple of the block size
    src = WavFileSource(write_wav(original))
    with src:
        blocks = list(src.frames())

    assert all(b.shape[1] == 1 for b in blocks)
    assert all(b.shape[0] == settings.BLOCK_SIZE for b in blocks[:-1])
    assert 0 < blocks[-1].shape[0] <= settings.BLOCK_SIZE  # short final block, not padded
    assert np.array_equal(np.concatenate(blocks).reshape(-1), original)


def test_eof_is_explicit_and_repeatable(write_wav):
    src = WavFileSource(write_wav(_samples(1500)), block_size=1024)
    src.start()
    try:
        src.read_frame()
        src.read_frame()  # short final block
        for _ in range(3):
            with pytest.raises(EndOfAudio):
                src.read_frame()
    finally:
        src.stop()


def test_eof_is_not_confused_with_a_mic_timeout():
    """A consumer catching queue.Empty for stalls must not swallow EOF."""
    assert not issubclass(EndOfAudio, queue.Empty)


def test_read_before_start_is_an_error(write_wav):
    src = WavFileSource(write_wav(_samples(100)))
    with pytest.raises(RuntimeError):
        src.read_frame()


def test_stream_position_comes_from_sample_counter(write_wav):
    src = WavFileSource(write_wav(_samples(16000)), block_size=1024)
    with src:
        assert src.samples_delivered == 0
        assert src.stream_time_s == 0.0
        src.read_frame()
        src.read_frame()
        assert src.samples_delivered == 2048
        assert src.stream_time_s == 2048 / 16000
    assert src.total_samples == 16000
    assert src.duration_s == 1.0


def test_restart_replays_from_the_beginning(write_wav):
    original = _samples(2500)
    src = WavFileSource(write_wav(original))
    with src:
        list(src.frames())
    with src:
        assert src.samples_delivered == 0
        assert np.array_equal(np.concatenate(list(src.frames())).reshape(-1), original)


def test_fast_mode_never_sleeps(write_wav):
    clock = FakeClock()
    src = WavFileSource(
        write_wav(_samples(16000)), realtime=False, clock=clock.now, sleep=clock.sleep
    )
    with src:
        list(src.frames())
    assert clock.sleep_calls == 0


def test_realtime_mode_paces_to_audio_duration(write_wav):
    """Total waiting equals the audio duration: no drift, no skipped time."""
    clock = FakeClock()
    src = WavFileSource(
        write_wav(_samples(16000 * 3)), realtime=True, clock=clock.now, sleep=clock.sleep
    )
    with src:
        list(src.frames())
    assert clock.slept == pytest.approx(3.0, abs=1e-6)


def test_realtime_first_block_is_not_released_early(write_wav):
    clock = FakeClock()
    src = WavFileSource(
        write_wav(_samples(16000)), block_size=1600, realtime=True,
        clock=clock.now, sleep=clock.sleep,
    )
    with src:
        src.read_frame()
    assert clock.slept == pytest.approx(0.1)  # 1600 samples = 100 ms


def test_realtime_slow_consumer_gets_no_catch_up_sleep(write_wav):
    clock = FakeClock()
    src = WavFileSource(
        write_wav(_samples(16000)), realtime=True, clock=clock.now, sleep=clock.sleep
    )
    with src:
        clock.t += 10.0  # consumer was busy far longer than the audio lasts
        list(src.frames())
    assert clock.sleep_calls == 0


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        (dict(rate=8000), "8000 Hz"),
        (dict(channels=2), "2 channels"),
        (dict(sampwidth=1, raw=bytes(200)), "8-bit"),
    ],
)
def test_unsupported_wav_formats_are_rejected(write_wav, kwargs, fragment):
    path = write_wav(_samples(200), **kwargs) if "raw" not in kwargs else write_wav(**kwargs)
    with pytest.raises(ValueError, match=fragment):
        WavFileSource(path)


def test_non_wav_file_is_rejected(tmp_path):
    bad = tmp_path / "not_audio.wav"
    bad.write_bytes(b"this is not a wav file")
    with pytest.raises(ValueError):
        WavFileSource(bad)


def test_invalid_block_size_is_rejected(write_wav):
    with pytest.raises(ValueError):
        WavFileSource(write_wav(_samples(100)), block_size=0)
