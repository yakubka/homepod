"""Pure-numpy rational sample-rate conversion (WP2.4 ingest, explicit --convert only).

Why not scipy.signal.resample_poly: scipy is only present as a transitive
dependency of openWakeWord, and on the Windows dev PC an Application
Control policy intermittently blocks some of its DLLs (scipy.signal
included). A small numpy implementation avoids depending on that.

Method: polyphase windowed-sinc FIR (Kaiser window). Conceptually the input
is zero-stuffed by `up`, low-passed at the lower of the two Nyquist
frequencies, and decimated by `down`; the polyphase form only computes the
samples that are kept.
"""

import math

import numpy as np

_HALF_WIDTH = 10        # sinc zero-crossings on each side of the kernel centre
_KAISER_BETA = 8.6      # roughly 80 dB stop-band attenuation
_CUTOFF_MARGIN = 0.97   # keep the cutoff just below the lower Nyquist
_BLOCK = 1 << 18        # output samples per block (bounds memory)


def resample(x: np.ndarray, rate_in: int, rate_out: int) -> np.ndarray:
    """Resample a 1-D float signal from rate_in to rate_out (Hz).

    Output length is ceil(len(x) * rate_out / rate_in). Samples outside the
    input are treated as zero, so the first and last few output samples
    contain a short filter transient.
    """
    if x.ndim != 1:
        raise ValueError("resample() expects a 1-D array")
    if rate_in <= 0 or rate_out <= 0:
        raise ValueError("sample rates must be positive")
    if rate_in == rate_out:
        return x.astype(np.float64)

    g = math.gcd(rate_in, rate_out)
    up, down = rate_out // g, rate_in // g
    n_in = len(x)
    n_out = -(-n_in * up // down)  # ceil

    m = max(up, down)
    half = _HALF_WIDTH * m
    delay = half
    j = np.arange(2 * half + 1)
    cutoff = 0.5 / m * _CUTOFF_MARGIN   # cycles per upsampled sample
    h = up * 2.0 * cutoff * np.sinc(2.0 * cutoff * (j - delay)) * np.kaiser(2 * half + 1, _KAISER_BETA)

    taps = -(-len(h) // up)              # taps per polyphase branch
    h_pad = np.zeros(taps * up)
    h_pad[: len(h)] = h
    xf = x.astype(np.float64)
    y = np.empty(n_out)

    for start in range(0, n_out, _BLOCK):
        mm = np.arange(start, min(start + _BLOCK, n_out))
        s = mm * down + delay            # position in the upsampled stream
        phase = s % up
        base = s // up
        acc = np.zeros(len(mm))
        for k in range(taps):
            idx = base - k
            ok = (idx >= 0) & (idx < n_in)
            coef = h_pad[phase + k * up]
            acc[ok] += coef[ok] * xf[idx[ok]]
        y[start:start + len(mm)] = acc
    return y
