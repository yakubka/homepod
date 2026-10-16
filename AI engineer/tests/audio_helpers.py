"""Synthetic-audio helpers for the WP2.4 tests. Everything generated here is
SYNTHETIC test data; no real voice recording is ever used by the test suite."""

from pathlib import Path

import numpy as np

SR = 16000


def noise(n, dbfs, seed=0):
    return np.random.default_rng(seed).standard_normal(n) * 32768 * 10 ** (dbfs / 20)


def clip_with_phrase(start_s=1.5, end_s=3.0, total_s=5.0, noise_dbfs=-60, speech_dbfs=-20, seed=0):
    """Speech-like burst (noise band + 300 Hz tone) over a quiet noise bed, int16."""
    n = int(total_s * SR)
    x = noise(n, noise_dbfs, seed)
    a, b = int(start_s * SR), int(end_s * SR)
    t = np.arange(b - a) / SR
    x[a:b] += noise(b - a, speech_dbfs, seed + 1) * 0.7 + 32768 * 10 ** (speech_dbfs / 20) * 0.7 * np.sin(2 * np.pi * 300 * t)
    return np.clip(x, -32768, 32767).astype(np.int16)


def write_pcm_wav(path, samples, rate=16000, channels=1, width=2):
    """Write a PCM WAV. `samples` is (n,) or (n, channels) in the native integer
    range of `width` bytes (8-bit values are signed here and stored unsigned)."""
    import wave

    a = np.asarray(samples)
    if channels > 1 and a.ndim == 1:
        a = np.stack([a] * channels, axis=1)
    flat = a.reshape(-1)
    if width == 1:
        raw = (flat.astype(np.int32) + 128).astype(np.uint8).tobytes()
    elif width == 2:
        raw = flat.astype("<i2").tobytes()
    elif width == 3:
        v = flat.astype(np.int64) & 0xFFFFFF
        raw = np.stack([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF], axis=1).astype(np.uint8).tobytes()
    elif width == 4:
        raw = flat.astype("<i4").tobytes()
    else:
        raise ValueError(width)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(raw)
    return Path(path)
