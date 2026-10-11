"""Tests for audio/reframer.py: exact sample preservation. No hardware needed."""

import numpy as np
import pytest

from audio.reframer import Reframer


def _ramp(n):
    """Unique-ish values so any lost/duplicated/reordered sample is detected."""
    return (np.arange(n, dtype=np.int64) % 65536 - 32768).astype(np.int16)


def _feed(reframer, data, block_sizes):
    """Push `data` in blocks of the given (cycling) sizes; return emitted chunks."""
    chunks, pos, i = [], 0, 0
    while pos < len(data):
        size = block_sizes[i % len(block_sizes)]
        chunks.extend(reframer.push(data[pos:pos + size]))
        pos += size
        i += 1
    return chunks


@pytest.mark.parametrize("chunk_samples", [512, 1280, 1024, 1, 3000])
@pytest.mark.parametrize("block_sizes", [[1024], [1], [7, 1024, 333], [5000]])
def test_exact_sample_preservation(chunk_samples, block_sizes):
    data = _ramp(16000 * 2 + 137)
    r = Reframer(chunk_samples)
    chunks = _feed(r, data, block_sizes)
    tail = r.flush(pad=False)

    assert all(c.shape == (chunk_samples,) and c.dtype == np.int16 for c in chunks)
    rebuilt = np.concatenate(chunks + ([tail] if tail is not None else []))
    assert np.array_equal(rebuilt, data)  # nothing lost, duplicated or reordered


def test_wp1_1024_blocks_to_80ms_chunks():
    """The real case: WP1 delivers (1024, 1) blocks, openWakeWord wants 1280."""
    data = _ramp(1280 * 10)  # 12800 samples = 12.5 WP1 blocks
    r = Reframer(1280)
    emitted = []
    for start in range(0, len(data), 1024):
        block = data[start:start + 1024].reshape(-1, 1)
        emitted.extend(r.push(block))
    tail = r.flush()

    assert len(emitted) == 10
    assert tail is None
    assert np.array_equal(np.concatenate(emitted), data)


def test_sample_accounting_invariant():
    r = Reframer(1280)
    data = _ramp(5000)
    for start in range(0, 5000, 1024):
        r.push(data[start:start + 1024])
        assert r.samples_in == r.samples_out + r.pending_samples
        assert r.pending_samples < 1280
    assert r.samples_in == 5000
    assert r.samples_out == 3 * 1280
    assert r.pending_samples == 5000 - 3 * 1280


def test_no_chunk_until_enough_samples():
    r = Reframer(1280)
    assert r.push(_ramp(1024)) == []
    assert r.pending_samples == 1024
    chunks = r.push(_ramp(1024))
    assert len(chunks) == 1
    assert r.pending_samples == 2048 - 1280


def test_flush_without_padding_returns_exact_remainder():
    data = _ramp(1500)
    r = Reframer(1280)
    chunks = r.push(data)
    tail = r.flush(pad=False)
    assert len(chunks) == 1
    assert np.array_equal(tail, data[1280:])
    assert r.pending_samples == 0
    assert r.flush() is None  # nothing left


def test_flush_with_padding_adds_only_trailing_zeros():
    data = _ramp(1500)
    r = Reframer(1280)
    r.push(data)
    tail = r.flush(pad=True)
    assert tail.shape == (1280,)
    remainder = 1500 - 1280
    assert np.array_equal(tail[:remainder], data[1280:])
    assert not tail[remainder:].any()
    assert r.samples_out == 1500  # padding is not counted as real samples


def test_chunks_do_not_alias_the_callers_block():
    """Capture code may reuse its buffer; emitted audio must not change."""
    block = _ramp(1280)
    original = block.copy()
    r = Reframer(1280)
    (chunk,) = r.push(block)
    block[:] = 0
    assert np.array_equal(chunk, original)


def test_leftover_does_not_alias_the_callers_block():
    block = _ramp(100)
    original = block.copy()
    r = Reframer(1280)
    r.push(block)
    block[:] = 0
    tail = r.flush()
    assert np.array_equal(tail, original)


def test_empty_block_is_harmless():
    r = Reframer(1280)
    assert r.push(np.empty(0, dtype=np.int16)) == []
    assert r.push(np.empty((0, 1), dtype=np.int16)) == []
    assert r.samples_in == 0


def test_reset_starts_a_fresh_stream():
    r = Reframer(1280)
    r.push(_ramp(1000))
    r.reset()
    assert (r.samples_in, r.samples_out, r.pending_samples) == (0, 0, 0)
    assert r.flush() is None


@pytest.mark.parametrize("bad", [0, -5, 1.5, "1280", None])
def test_invalid_chunk_size_rejected(bad):
    with pytest.raises(ValueError):
        Reframer(bad)


def test_wrong_dtype_rejected():
    r = Reframer(1280)
    with pytest.raises(ValueError, match="int16"):
        r.push(np.zeros(1024, dtype=np.float32))


def test_multichannel_block_rejected():
    r = Reframer(1280)
    with pytest.raises(ValueError):
        r.push(np.zeros((1024, 2), dtype=np.int16))


def test_non_array_rejected():
    r = Reframer(1280)
    with pytest.raises(TypeError):
        r.push([1, 2, 3])
