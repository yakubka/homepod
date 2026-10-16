"""Tests for evaluation/audio_analysis.py and evaluation/resample.py (synthetic audio only)."""

import numpy as np
import pytest

from evaluation.audio_analysis import (
    estimate_snr_db,
    propose_wake_boundaries,
    quality_metrics,
    rms_dbfs,
)
from evaluation.resample import resample

from tests.audio_helpers import SR
from tests.audio_helpers import clip_with_phrase as _clip_with_phrase
from tests.audio_helpers import noise as _noise


# -- quality ------------------------------------------------------------------

def test_quality_of_a_known_sine():
    t = np.arange(SR) / SR
    x = (0.5 * 32768 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    q = quality_metrics(x, SR)
    assert q.peak_dbfs == pytest.approx(-6.02, abs=0.1)
    assert q.rms_dbfs == pytest.approx(-9.03, abs=0.1)     # sine RMS = peak - 3 dB
    assert q.clipping_fraction == 0.0


def test_digital_silence_is_finite_and_floored():
    q = quality_metrics(np.zeros(SR, dtype=np.int16), SR)
    assert q.peak_dbfs == pytest.approx(-90.3, abs=0.1)
    assert q.noise_floor_dbfs == pytest.approx(-90.3, abs=0.1)
    assert np.isfinite(q.rms_dbfs)


def test_clipping_fraction_counts_full_scale_samples_including_minus_32768():
    x = np.zeros(1000, dtype=np.int16)
    x[:10] = 32767
    x[10:20] = -32768          # abs() of this in int16 would overflow; must still count
    q = quality_metrics(x, SR)
    assert q.clipping_fraction == pytest.approx(0.02)
    assert q.peak_dbfs == pytest.approx(0.0, abs=0.01)


def test_quality_rejects_wrong_dtype_or_empty():
    with pytest.raises(ValueError):
        quality_metrics(np.zeros(100, dtype=np.float32), SR)
    with pytest.raises(ValueError):
        quality_metrics(np.zeros(0, dtype=np.int16), SR)


# -- boundary proposal -----------------------------------------------------------

def test_proposal_finds_a_clean_phrase_within_60ms():
    x = _clip_with_phrase(1.5, 3.0)
    p = propose_wake_boundaries(x, SR)
    assert p is not None
    assert p.start_s == pytest.approx(1.5, abs=0.06)
    assert p.end_s == pytest.approx(3.0, abs=0.06)
    assert p.n_segments == 1
    assert p.warnings == ()
    assert p.start_sample == round(p.start_s * SR)


def test_proposal_bridges_the_pause_between_two_words():
    x = _clip_with_phrase(1.5, 3.0)
    x[int(2.2 * SR):int(2.4 * SR)] = np.random.default_rng(5).integers(-30, 30, int(0.2 * SR)).astype(np.int16)
    p = propose_wake_boundaries(x, SR)
    assert p.start_s == pytest.approx(1.5, abs=0.06)
    assert p.end_s == pytest.approx(3.0, abs=0.06)   # a 200 ms pause must not split the phrase


def test_proposal_picks_the_strongest_segment_and_warns_about_others():
    x = _clip_with_phrase(1.5, 3.0, total_s=7.0)
    # a comparably strong second utterance later in the clip
    a, b = int(4.5 * SR), int(5.5 * SR)
    x[a:b] = _clip_with_phrase(0.0, 1.0, total_s=1.0, seed=9)[: b - a]
    p = propose_wake_boundaries(x, SR)
    assert p.start_s == pytest.approx(1.5, abs=0.06)     # the longer/stronger one
    assert p.n_segments == 2
    assert "multiple_active_segments" in p.warnings


def test_faint_second_blip_is_ignored():
    x = _clip_with_phrase(1.5, 3.0, total_s=7.0)
    a = int(5.0 * SR)
    x[a:a + int(0.6 * SR)] += _noise(int(0.6 * SR), -50, 3).astype(np.int16)   # -50 dBFS blip
    p = propose_wake_boundaries(x, SR)
    assert p.start_s == pytest.approx(1.5, abs=0.06)
    assert "multiple_active_segments" not in p.warnings


def test_short_lead_in_and_tail_produce_warnings():
    x = _clip_with_phrase(0.7, 3.0, total_s=3.5)
    p = propose_wake_boundaries(x, SR)
    assert "lead_in_shorter_than_1s" in p.warnings
    assert "tail_shorter_than_1s" in p.warnings


def test_start_inside_warmup_is_flagged():
    x = _clip_with_phrase(0.3, 1.8, total_s=4.0)
    p = propose_wake_boundaries(x, SR)
    assert "starts_inside_engine_warmup_margin" in p.warnings


def test_silence_and_pure_low_noise_give_no_proposal():
    assert propose_wake_boundaries(np.zeros(SR * 3, dtype=np.int16), SR) is None
    assert propose_wake_boundaries(_noise(SR * 3, -65).astype(np.int16), SR) is None


def test_snr_estimate_tracks_the_true_ratio():
    x = _clip_with_phrase(1.5, 3.0, noise_dbfs=-60, speech_dbfs=-20)
    p = propose_wake_boundaries(x, SR)
    snr = estimate_snr_db(x, p, SR)
    assert snr == pytest.approx(rms_dbfs(x[p.start_sample:p.end_sample]) - rms_dbfs(x[: int(1.3 * SR)]), abs=3.0)
    assert 30 < snr < 50


def test_snr_is_none_without_enough_lead_in():
    x = _clip_with_phrase(0.35, 2.0, total_s=4.0)
    p = propose_wake_boundaries(x, SR)
    assert estimate_snr_db(x, p, SR) is None


# -- resampler ---------------------------------------------------------------------

def _peak_freq(y, rate):
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    return np.argmax(spec) * rate / len(y)


@pytest.mark.parametrize("rate_in", [48000, 44100, 32000, 8000, 22050])
def test_resample_preserves_frequency_amplitude_and_length(rate_in):
    n = rate_in * 2
    t = np.arange(n) / rate_in
    x = 10000 * np.sin(2 * np.pi * 1000 * t)
    y = resample(x, rate_in, 16000)
    assert len(y) == -(-n * 16000 // rate_in)                   # exact ceil length
    mid = y[1600:-1600]                                          # ignore edge transients
    assert _peak_freq(mid, 16000) == pytest.approx(1000, abs=10)
    assert np.abs(mid).max() == pytest.approx(10000, rel=0.02)


def test_resample_removes_content_above_the_new_nyquist():
    rate_in = 48000
    t = np.arange(rate_in * 2) / rate_in
    x = 10000 * np.sin(2 * np.pi * 10000 * t)                   # 10 kHz: above 8 kHz Nyquist
    y = resample(x, rate_in, 16000)[1600:-1600]
    assert np.abs(y).max() < 10000 * 10 ** (-40 / 20)           # > 40 dB attenuation


def test_resample_same_rate_is_identity_and_bad_input_rejected():
    x = np.arange(100, dtype=np.float64)
    assert np.array_equal(resample(x, 16000, 16000), x)
    with pytest.raises(ValueError):
        resample(np.zeros((10, 2)), 48000, 16000)
    with pytest.raises(ValueError):
        resample(x, 0, 16000)
