"""Re-chunk audio blocks into the fixed size a wake-word backend needs (VESTA, WP2.1).

The capture layer delivers 1024-sample blocks (64 ms at 16 kHz), but
wake-word engines want their own chunk size - e.g. openWakeWord works
best with multiples of 80 ms (1280 samples). Reframer sits between them:
it accepts blocks of any size and emits exact `chunk_samples` chunks,
keeping the leftover samples for the next call.

Guarantee: concatenating every emitted chunk (plus a final flush) gives
back the input samples exactly - nothing lost, duplicated or reordered.
"""

from typing import List, Optional

import numpy as np


class Reframer:
    """Accumulate int16 mono samples and emit fixed-size chunks."""

    def __init__(self, chunk_samples: int) -> None:
        if not isinstance(chunk_samples, (int, np.integer)) or chunk_samples <= 0:
            raise ValueError(f"chunk_samples must be a positive integer, got {chunk_samples!r}")
        self._chunk_samples = int(chunk_samples)
        self._pending = np.empty(0, dtype=np.int16)
        self._samples_in = 0
        self._samples_out = 0

    @property
    def chunk_samples(self) -> int:
        return self._chunk_samples

    @property
    def pending_samples(self) -> int:
        """Samples held back, waiting for a full chunk (always < chunk_samples)."""
        return int(self._pending.size)

    @property
    def samples_in(self) -> int:
        return self._samples_in

    @property
    def samples_out(self) -> int:
        """Real samples emitted so far. Zero padding added by flush(pad=True)
        is not counted, so samples_in == samples_out + pending_samples always."""
        return self._samples_out

    @staticmethod
    def _as_mono_int16(block: np.ndarray) -> np.ndarray:
        if not isinstance(block, np.ndarray):
            raise TypeError(f"block must be a numpy array, got {type(block).__name__}")
        if block.dtype != np.int16:
            # No silent conversion: float or int32 audio here would mean a
            # config mistake upstream that should be visible, not hidden.
            raise ValueError(f"block must be int16, got {block.dtype}")
        if block.ndim == 1:
            return block
        if block.ndim == 2 and block.shape[1] == 1:
            return block.reshape(-1)  # WP1 layout (frames, 1) -> 1-D
        raise ValueError(f"block must be 1-D or shape (n, 1), got {block.shape}")

    def push(self, block: np.ndarray) -> List[np.ndarray]:
        """Add a block; return zero or more complete 1-D int16 chunks."""
        samples = self._as_mono_int16(block)
        self._samples_in += samples.size
        if samples.size == 0:
            return []

        # concatenate() always allocates a new array, so emitted chunks
        # never alias the caller's block (which sounddevice/WAV code may reuse).
        data = np.concatenate((self._pending, samples))
        n_chunks = data.size // self._chunk_samples
        used = n_chunks * self._chunk_samples

        chunks = [
            data[i * self._chunk_samples:(i + 1) * self._chunk_samples]
            for i in range(n_chunks)
        ]
        self._pending = data[used:].copy()
        self._samples_out += used
        return chunks

    def flush(self, pad: bool = False) -> Optional[np.ndarray]:
        """Release the leftover samples at end of stream.

        pad=False returns the short remainder as-is (exact sample
        accounting, used by tests). pad=True zero-pads it to a full chunk
        so a detector that requires exact chunk sizes still sees the end
        of the audio - the padding is silence that was not in the source.
        Returns None if nothing is pending.
        """
        n = self._pending.size
        if n == 0:
            return None

        if pad:
            out = np.zeros(self._chunk_samples, dtype=np.int16)
            out[:n] = self._pending
        else:
            out = self._pending

        self._pending = np.empty(0, dtype=np.int16)
        self._samples_out += n
        return out

    def reset(self) -> None:
        """Drop pending samples and zero the counters (start of a new stream)."""
        self._pending = np.empty(0, dtype=np.int16)
        self._samples_in = 0
        self._samples_out = 0
