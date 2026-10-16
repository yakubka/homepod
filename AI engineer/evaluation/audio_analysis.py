"""Basic audio quality metrics and wake-phrase boundary PROPOSALS (WP2.4).

Pure numpy, no I/O. All levels are dBFS relative to int16 full scale
(32768). Digital silence is floored at 1 LSB (about -90.3 dBFS) instead of
-inf so results stay finite and comparable.

The boundary proposal is a simple energy detector. It is only a starting
point for a human annotator: soft consonants at the edges of the phrase
may be missed, and a long pause between the two words can split the
phrase. Anything suspicious is reported in `warnings`. A proposal is
never "ground truth" until a person verifies it (annotation_status).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

FULL_SCALE = 32768.0
FRAME_S = 0.02          # 20 ms analysis frames
WARMUP_S = 0.4          # openWakeWord cannot fire before this (see wakeword backend)


def _dbfs(rms) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(rms, 1.0) / FULL_SCALE)


def frame_rms_dbfs(x: np.ndarray, sample_rate: int, frame_s: float = FRAME_S) -> np.ndarray:
    """RMS level in dBFS of consecutive non-overlapping frames (partial tail dropped)."""
    win = int(round(frame_s * sample_rate))
    n = len(x) // win
    if n == 0:
        return np.empty(0)
    frames = x[: n * win].astype(np.float64).reshape(n, win)
    return _dbfs(np.sqrt((frames ** 2).mean(axis=1)))


def rms_dbfs(x: np.ndarray) -> float:
    if len(x) == 0:
        return float(_dbfs(0.0))
    return float(_dbfs(np.sqrt((x.astype(np.float64) ** 2).mean())))


@dataclass(frozen=True)
class QualityMetrics:
    peak_dbfs: float
    rms_dbfs: float
    noise_floor_dbfs: float      # 10th percentile of 20 ms frame levels
    clipping_fraction: float     # share of samples at int16 full scale


def quality_metrics(x: np.ndarray, sample_rate: int) -> QualityMetrics:
    if x.dtype != np.int16:
        raise ValueError(f"expected int16 samples, got {x.dtype}")
    if len(x) == 0:
        raise ValueError("empty audio")
    # int32 first: abs(int16(-32768)) overflows back to -32768.
    mag = np.abs(x.astype(np.int32))
    frames = frame_rms_dbfs(x, sample_rate)
    floor = float(np.percentile(frames, 10)) if len(frames) else rms_dbfs(x)
    return QualityMetrics(
        peak_dbfs=float(_dbfs(float(mag.max()))),
        rms_dbfs=rms_dbfs(x),
        noise_floor_dbfs=floor,
        clipping_fraction=float((mag >= 32767).mean()),
    )


@dataclass(frozen=True)
class BoundaryProposal:
    start_sample: int
    end_sample: int              # exclusive
    start_s: float
    end_s: float
    segment_rms_dbfs: float
    noise_floor_dbfs: float
    n_segments: int              # candidate segments found (1 is the clean case)
    warnings: Tuple[str, ...]


def _runs(active: np.ndarray, max_gap: int) -> List[Tuple[int, int]]:
    """(first, last) frame index of each run of True, merging gaps of <= max_gap frames."""
    idx = np.flatnonzero(active)
    if len(idx) == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > max_gap + 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))
    return list(zip(starts.tolist(), ends.tolist()))


def propose_wake_boundaries(
    x: np.ndarray,
    sample_rate: int,
    *,
    high_margin_db: float = 12.0,
    low_margin_db: float = 6.0,
    merge_gap_s: float = 0.3,
    min_segment_s: float = 0.3,
    min_active_dbfs: float = -55.0,
) -> Optional[BoundaryProposal]:
    """Propose where the spoken phrase starts and ends, or None if nothing found.

    A frame is "active" when it is `high_margin_db` above the noise floor
    (and above an absolute minimum). Active runs closer than `merge_gap_s`
    are merged (the pause between "hey" and "jarvis"), runs shorter than
    `min_segment_s` are dropped, and the run with the most energy wins.
    Its edges are then widened while frames stay `low_margin_db` above
    the floor, which recovers weak onsets/offsets.
    """
    if x.dtype != np.int16:
        raise ValueError(f"expected int16 samples, got {x.dtype}")
    db = frame_rms_dbfs(x, sample_rate)
    if len(db) == 0:
        return None

    floor = float(np.percentile(db, 10))
    high = (db > floor + high_margin_db) & (db > min_active_dbfs)
    low = (db > floor + low_margin_db) & (db > min_active_dbfs - 6.0)

    gap_frames = int(round(merge_gap_s / FRAME_S))
    min_frames = int(round(min_segment_s / FRAME_S))
    segments = [(a, b) for a, b in _runs(high, gap_frames) if (b - a + 1) >= min_frames]
    if not segments:
        return None

    energies = [float(np.sum(10.0 ** (db[a:b + 1] / 10.0))) for a, b in segments]
    best = int(np.argmax(energies))
    a, b = segments[best]
    while a > 0 and low[a - 1]:
        a -= 1
    while b < len(db) - 1 and low[b + 1]:
        b += 1

    win = int(round(FRAME_S * sample_rate))
    start = a * win
    end = min((b + 1) * win, len(x))
    duration = len(x) / sample_rate

    warnings: List[str] = []
    if len(segments) > 1:
        rest = max(e for i, e in enumerate(energies) if i != best)
        if 10.0 * np.log10(energies[best] / rest) < 20.0:
            warnings.append("multiple_active_segments")
    if start / sample_rate < WARMUP_S + 0.1:
        warnings.append("starts_inside_engine_warmup_margin")
    elif start / sample_rate < 1.0:
        warnings.append("lead_in_shorter_than_1s")
    if duration - end / sample_rate < 1.0:
        warnings.append("tail_shorter_than_1s")
    length_s = (end - start) / sample_rate
    if length_s < 0.4:
        warnings.append("segment_shorter_than_0.4s")
    if length_s > 2.5:
        warnings.append("segment_longer_than_2.5s")

    return BoundaryProposal(
        start_sample=int(start),
        end_sample=int(end),
        start_s=start / sample_rate,
        end_s=end / sample_rate,
        segment_rms_dbfs=rms_dbfs(x[start:end]),
        noise_floor_dbfs=floor,
        n_segments=len(segments),
        warnings=tuple(warnings),
    )


def estimate_snr_db(
    x: np.ndarray, proposal: BoundaryProposal, sample_rate: int, guard_s: float = 0.1
) -> Optional[float]:
    """Phrase RMS minus lead-in RMS, in dB. None if the lead-in is under 0.3 s.

    A rough estimate only: it assumes the noise before the phrase resembles
    the noise during it.
    """
    lead_end = proposal.start_sample - int(round(guard_s * sample_rate))
    if lead_end < int(round(0.3 * sample_rate)):
        return None
    return proposal.segment_rms_dbfs - rms_dbfs(x[:lead_end])
