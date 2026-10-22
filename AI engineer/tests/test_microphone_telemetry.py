"""Team Leader AI-2 / Stage B: the additive position + drop telemetry of audio/microphone.py.

SOFTWARE-VALIDATED with PortAudio replaced: the callback is called directly, the way PortAudio's thread would call it.
This proves what OUR code counts. It says nothing about what PortAudio, ALSA or the XVF3800 really do on a device
(for example, loss below PortAudio that it does not flag cannot be seen by any software test).
"""

import logging

import numpy as np
import pytest

from audio import microphone as mic_mod
from audio.microphone import MicBlock, MicTelemetry, ReSpeakerMicrophone, _is_input_overflow

FR = 8                                    # frames per fake callback block


def _blk(value, frames=FR):
    return np.full((frames, 1), value, np.int16)


class _Handler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    h = _Handler()
    mic_mod.logger.addHandler(h)
    yield h.lines
    mic_mod.logger.removeHandler(h)


class _Stream:
    def __init__(self, **kw):
        self.kw = kw

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def started(monkeypatch):
    """A microphone whose start() worked against a fake PortAudio; returns (mic_factory)."""
    monkeypatch.setattr(mic_mod.sd, "query_devices", lambda: [{"name": "ReSpeaker XVF3800", "max_input_channels": 1}])
    streams = []

    def factory(**kw):
        streams.append(_Stream(**kw))
        return streams[-1]

    monkeypatch.setattr(mic_mod.sd, "InputStream", factory)

    def make(**kw):
        m = ReSpeakerMicrophone(**kw)
        m.start()
        return m

    return make


# -- positions -----------------------------------------------------------------------------------------

def test_blocks_carry_exact_consecutive_positions(started):
    m = started(queue_max_blocks=10)
    for i in range(4):
        m._callback(_blk(i), FR, None, None)
    blocks = [m.read_block(timeout=0.05) for _ in range(4)]
    assert [(b.sequence, b.start_sample, b.epoch, b.overflow) for b in blocks] == [(i, i * FR, 1, False) for i in range(4)]
    assert [int(b.samples[0, 0]) for b in blocks] == [0, 1, 2, 3]
    t = m.telemetry()
    assert (t.blocks_received, t.samples_received, t.blocks_dropped, t.samples_dropped, t.overflow_events) == (4, 4 * FR, 0, 0, 0)


def test_positions_follow_the_frames_not_the_block_count(started):
    m = started(queue_max_blocks=10)
    for n in (8, 3, 5):
        m._callback(_blk(1, n), n, None, None)
    assert [m.read_block(timeout=0.05).start_sample for _ in range(3)] == [0, 8, 11]


# -- drops ---------------------------------------------------------------------------------------------

def test_a_full_queue_drops_the_oldest_and_counts_it_exactly(started, logs):
    m = started(queue_max_blocks=3)
    for i in range(10):
        m._callback(_blk(i), FR, None, None)
    t = m.telemetry()
    assert (t.blocks_received, t.blocks_dropped, t.samples_dropped, t.queue_depth, t.queue_max_blocks) == (10, 7, 7 * FR, 3, 3)
    survivors = [m.read_block(timeout=0.05) for _ in range(3)]
    assert [b.sequence for b in survivors] == [7, 8, 9]
    assert [b.start_sample for b in survivors] == [7 * FR, 8 * FR, 9 * FR]      # the loss is visible as a position jump
    assert any("event=mic_queue_overflow" in line for line in logs)


def test_queue_depth_never_exceeds_the_bound(started):
    m = started(queue_max_blocks=2)
    for i in range(50):
        m._callback(_blk(i), FR, None, None)
        assert m.telemetry().queue_depth <= 2


def test_dropped_samples_equal_the_position_jump_seen_by_the_consumer(started):
    m = started(queue_max_blocks=4)
    for i in range(9):
        m._callback(_blk(i), FR, None, None)
    first = m.read_block(timeout=0.05)
    t = m.telemetry()
    assert first.start_sample == t.samples_dropped            # nothing was consumed before it, so the jump is the drop


# -- device overflow -------------------------------------------------------------------------------------

class _Flags:
    def __init__(self, **flags):
        self.__dict__.update(flags)

    def __bool__(self):
        return any(self.__dict__.values())


@pytest.mark.parametrize("status,expected", [
    (None, False), ("", False), ("input overflow", True), ("Input Overflow", True), ("input underflow", False),
    (_Flags(input_overflow=True), True), (_Flags(input_overflow=False, input_underflow=True), False),
])
def test_overflow_detection_accepts_flag_objects_and_strings(status, expected):
    assert _is_input_overflow(status) is expected


def test_an_overflow_flag_is_attached_to_the_block_and_counted_but_no_size_is_invented(started):
    m = started(queue_max_blocks=10)
    m._callback(_blk(0), FR, None, None)
    m._callback(_blk(1), FR, None, "input overflow")
    m._callback(_blk(2), FR, None, None)
    blocks = [m.read_block(timeout=0.05) for _ in range(3)]
    assert [b.overflow for b in blocks] == [False, True, False]
    assert [b.start_sample for b in blocks] == [0, FR, 2 * FR]       # callback-domain positions do NOT pretend to know the lost frames
    t = m.telemetry()
    assert t.overflow_events == 1 and t.samples_dropped == 0


# -- epochs / lifecycle ------------------------------------------------------------------------------------

def test_a_new_start_is_a_new_epoch_with_positions_from_zero_and_stale_blocks_gone(started):
    m = started(queue_max_blocks=10)
    for i in range(3):
        m._callback(_blk(i), FR, None, None)
    m.stop()
    assert m.read_block(timeout=0.05).start_sample == 0           # blocks already queued stay readable after stop()
    m.start()
    t = m.telemetry()
    assert t.epoch == 2 and t.queue_depth == 0                    # the stale block was discarded by the new start
    m._callback(_blk(9), FR, None, None)
    b = m.read_block(timeout=0.05)
    assert (b.epoch, b.sequence, b.start_sample) == (2, 0, 0)
    assert m.telemetry().blocks_received == 4                     # counters are cumulative across epochs


def test_read_frame_is_unchanged_and_still_returns_the_bare_array(started):
    m = started(queue_max_blocks=10)
    m._callback(_blk(5), FR, None, None)
    frame = m.read_frame(timeout=0.05)
    assert isinstance(frame, np.ndarray) and frame.shape == (FR, 1) and frame.dtype == np.int16 and int(frame[0, 0]) == 5


def test_read_block_timeout_raises_queue_empty_and_logs(started, logs):
    import queue
    m = started(queue_max_blocks=2)
    with pytest.raises(queue.Empty):
        m.read_block(timeout=0.02)
    assert any("event=read_block_timeout" in line for line in logs)


def test_the_callback_works_before_start_like_the_existing_tests_use_it():
    m = ReSpeakerMicrophone()
    m._callback(_blk(1), FR, None, "input overflow")
    b = m.read_block(timeout=0.05)
    assert (b.epoch, b.sequence, b.start_sample, b.overflow) == (0, 0, 0, True)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, None, "3"])
def test_queue_max_blocks_is_validated(bad):
    with pytest.raises(ValueError):
        ReSpeakerMicrophone(queue_max_blocks=bad)


def test_the_default_queue_bound_is_still_the_configured_one():
    from config import settings
    assert ReSpeakerMicrophone().telemetry().queue_max_blocks == settings.QUEUE_MAX_BLOCKS


def test_the_new_types_are_plain_data():
    assert MicTelemetry(1, 2, 3, 4, 5, 6, 7, 8).blocks_dropped == 4
    b = MicBlock(np.zeros((1, 1), np.int16), 0, 0, 1, False)
    with pytest.raises(Exception):
        b.sequence = 3                                        # frozen
