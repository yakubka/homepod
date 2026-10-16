"""Tests for audio/utterance.py: RollingPcmBuffer, SegmenterConfig and UtteranceSegmenter.

SOFTWARE / SYNTHETIC STATE-MACHINE TESTS. VAD scores are SCRIPTED (tests/voice_synthetic.py) and the
audio is a deterministic counter pattern, so these tests prove segmentation logic, sample indexing,
buffering and ownership. They do NOT measure VAD accuracy and say nothing about speech, a real VAD
engine, a live microphone or hardware.

Test geometry (tests/voice_synthetic.py): 1000 Hz, 10-sample VAD frames. make_config() counts:
confirm 30, end silence 40, onset padding 20, pre-roll 100, no-speech timeout 200, min 50, max 500.
"""

import dataclasses
from types import SimpleNamespace

import numpy as np
import pytest

from audio.utterance import (
    AudioContinuityError,
    PcmSnapshot,
    RollingPcmBuffer,
    SegmentOutcome,
    SegmenterConfig,
    SegmenterConfigError,
    SegmenterInputError,
    SegmenterPhase,
    SegmenterStateError,
    SpeechSegment,
    UtteranceSegmenter,
    _to_samples,
    segmenter_config_from_settings,
)
from audio.vad import VadScoreError
from config import settings
from tests.voice_synthetic import (
    FRAME,
    SR,
    ScriptedVadEngine,
    feed,
    index_pcm,
    make_config,
    make_detection,
    region_scores,
    run_plain,
    run_wake,
    runs,
)

NEXT_BELOW_06 = float(np.nextafter(0.6, 0.0))
NEXT_ABOVE_04 = float(np.nextafter(0.4, 1.0))


# =======================================================================================
# RollingPcmBuffer (tests 1-12)
# =======================================================================================

@pytest.mark.parametrize("bad", [0, -1, -100, 1.5, True, False, "10", None])
def test_buffer_capacity_must_be_a_positive_integer(bad):
    with pytest.raises(SegmenterInputError):
        RollingPcmBuffer(bad)


def test_buffer_less_than_capacity():
    buf = RollingPcmBuffer(100)
    buf.push(index_pcm(0, 30), 0)
    snap = buf.snapshot()
    assert (buf.size, buf.start_sample, buf.end_sample) == (30, 0, 30)
    assert snap.start_sample == 0 and snap.end_sample == 30
    assert np.array_equal(snap.samples, index_pcm(0, 30))


def test_buffer_exactly_capacity():
    buf = RollingPcmBuffer(100)
    buf.push(index_pcm(0, 100), 0)
    assert (buf.size, buf.start_sample, buf.end_sample) == (100, 0, 100)
    assert np.array_equal(buf.snapshot().samples, index_pcm(0, 100))


def test_buffer_over_capacity_keeps_the_newest_samples():
    buf = RollingPcmBuffer(100)
    for start in range(0, 250, 25):
        buf.push(index_pcm(start, 25), start)
    snap = buf.snapshot()
    assert (buf.size, buf.start_sample, buf.end_sample) == (100, 150, 250)
    assert snap.start_sample == 150
    assert np.array_equal(snap.samples, index_pcm(150, 100))


def test_a_single_block_larger_than_capacity_keeps_its_newest_part():
    buf = RollingPcmBuffer(100)
    buf.push(index_pcm(0, 250), 0)
    snap = buf.snapshot()
    assert (buf.size, snap.start_sample, snap.end_sample) == (100, 150, 250)
    assert np.array_equal(snap.samples, index_pcm(150, 100))


@pytest.mark.parametrize("sizes", [[7], [10], [1], [3, 11, 5], [100], [99], [101]])
def test_buffer_wraparound_preserves_exact_order(sizes):
    cap = 10 if len(sizes) > 1 or sizes[0] < 20 else 100
    buf = RollingPcmBuffer(cap)
    pos, i = 0, 0
    while pos < 500:
        n = sizes[i % len(sizes)]
        buf.push(index_pcm(pos, n), pos)
        pos += n
        i += 1
        want = min(cap, pos)
        snap = buf.snapshot()
        assert snap.start_sample == pos - want
        assert np.array_equal(snap.samples, index_pcm(pos - want, want))
        assert buf.size <= cap


def test_buffer_arbitrary_random_block_sizes_match_a_reference():
    rng = np.random.default_rng(1234)
    buf = RollingPcmBuffer(64)
    pos = 0
    for _ in range(300):
        n = int(rng.integers(1, 200))
        buf.push(index_pcm(pos, n), pos)
        pos += n
        want = min(64, pos)
        assert buf.size <= buf.capacity
        assert np.array_equal(buf.snapshot().samples, index_pcm(pos - want, want))


def test_snapshot_start_sample_follows_a_nonzero_stream_origin():
    buf = RollingPcmBuffer(50)
    buf.push(index_pcm(5000, 20), 5000)
    snap = buf.snapshot()
    assert (snap.start_sample, snap.end_sample, buf.start_sample, buf.end_sample) == (5000, 5020, 5000, 5020)


def test_snapshot_max_samples_returns_the_newest_part():
    buf = RollingPcmBuffer(100)
    buf.push(index_pcm(0, 80), 0)
    snap = buf.snapshot(30)
    assert (snap.start_sample, snap.end_sample) == (50, 80)
    assert np.array_equal(snap.samples, index_pcm(50, 30))


def test_asking_for_more_history_than_exists_returns_what_exists():
    buf = RollingPcmBuffer(100)
    buf.push(index_pcm(0, 40), 0)
    snap = buf.snapshot(1000)
    assert (snap.start_sample, len(snap)) == (0, 40)


def test_empty_buffer_and_zero_history_snapshots_are_empty():
    buf = RollingPcmBuffer(10)
    assert len(buf.snapshot()) == 0 and buf.snapshot().start_sample == 0
    buf.push(index_pcm(0, 10), 0)
    zero = buf.snapshot(0)
    assert len(zero) == 0 and zero.start_sample == 10


@pytest.mark.parametrize("bad", [-1, 1.5, True, "3"])
def test_snapshot_max_samples_validated(bad):
    buf = RollingPcmBuffer(10)
    with pytest.raises(SegmenterInputError):
        buf.snapshot(bad)


def test_snapshot_is_an_owned_read_only_copy():
    buf = RollingPcmBuffer(20)
    buf.push(index_pcm(0, 20), 0)
    snap = buf.snapshot()
    expected = index_pcm(0, 20)
    buf.push(index_pcm(20, 20), 20)  # overwrites the whole ring
    assert np.array_equal(snap.samples, expected)  # the snapshot did not change
    assert not snap.samples.flags.writeable
    assert not np.shares_memory(snap.samples, buf._ring)
    with pytest.raises(ValueError):
        snap.samples[0] = 1


def test_pushed_input_is_never_modified_and_may_be_read_only():
    buf = RollingPcmBuffer(10)
    block = index_pcm(0, 25)
    before = block.copy()
    block.setflags(write=False)
    buf.push(block, 0)
    assert np.array_equal(block, before)
    assert not np.shares_memory(buf._ring, block)


def test_buffer_accepts_the_wp1_column_layout():
    buf = RollingPcmBuffer(30)
    buf.push(index_pcm(0, 10).reshape(-1, 1), 0)
    assert np.array_equal(buf.snapshot().samples, index_pcm(0, 10))


@pytest.mark.parametrize("bad", [
    [1, 2, 3], index_pcm(0, 5).astype(np.float32), index_pcm(0, 5).astype(np.int32),
    np.zeros((2, 3), dtype=np.int16), np.zeros(0, dtype=np.int16), None,
])
def test_buffer_rejects_malformed_blocks(bad):
    with pytest.raises(SegmenterInputError):
        RollingPcmBuffer(10).push(bad)


def test_buffer_tracks_absolute_continuity_and_rejects_gaps_and_overlaps():
    buf = RollingPcmBuffer(50)
    buf.push(index_pcm(500, 10), 500)  # the first block sets where the stream starts
    buf.push(index_pcm(510, 10), 510)
    buf.push(index_pcm(520, 10))       # omitted start_sample simply continues
    with pytest.raises(AudioContinuityError):
        buf.push(index_pcm(535, 5), 535)   # gap
    with pytest.raises(AudioContinuityError):
        buf.push(index_pcm(525, 5), 525)   # overlap
    assert (buf.end_sample, buf.size) == (530, 30)  # rejected blocks changed nothing
    buf.push(index_pcm(530, 5), 530)
    assert buf.end_sample == 535


def test_first_push_without_start_sample_starts_at_zero():
    buf = RollingPcmBuffer(10)
    buf.push(index_pcm(0, 4))
    assert (buf.start_sample, buf.end_sample) == (0, 4)


@pytest.mark.parametrize("bad", [-1, 1.5, True, "0"])
def test_buffer_rejects_invalid_start_samples(bad):
    with pytest.raises(SegmenterInputError):
        RollingPcmBuffer(10).push(index_pcm(0, 4), bad)


def test_buffer_never_exceeds_capacity():
    buf = RollingPcmBuffer(37)
    pos = 0
    for n in [1, 36, 37, 38, 500, 3, 3, 3]:
        buf.push(index_pcm(pos, n), pos)
        pos += n
        assert buf.size <= 37 and len(buf.snapshot()) <= 37
        assert buf._ring.size == 37


# =======================================================================================
# SegmenterConfig (test group "config")
# =======================================================================================

def test_explicit_config_and_its_pinned_sample_counts():
    cfg = make_config()
    assert (cfg.start_confirmation_samples, cfg.end_silence_samples, cfg.onset_padding_samples,
            cfg.pre_roll_samples, cfg.no_speech_timeout_samples, cfg.min_utterance_samples,
            cfg.max_utterance_samples) == (30, 40, 20, 100, 200, 50, 500)


def test_equal_thresholds_are_rejected_true_hysteresis_needed():
    with pytest.raises(SegmenterConfigError):
        make_config(start_threshold=0.5, end_threshold=0.5)


@pytest.mark.parametrize("start,end", [(0.4, 0.6), (1.0, 1.0), (0.0, 0.0), (1.1, 0.4), (0.6, -0.1), (2.0, 0.5)])
def test_invalid_thresholds_rejected(start, end):
    with pytest.raises(SegmenterConfigError):
        make_config(start_threshold=start, end_threshold=end)


@pytest.mark.parametrize("start,end", [(1.0, 0.0), (0.6, 0.0), (1.0, 0.99), (0.0001, 0.0)])
def test_extreme_but_valid_thresholds_accepted(start, end):
    make_config(start_threshold=start, end_threshold=end)


@pytest.mark.parametrize("name", ["start_confirmation_s", "end_silence_s", "no_speech_timeout_s",
                                  "min_utterance_s", "max_utterance_s"])
@pytest.mark.parametrize("bad", [0, 0.0, -0.01, float("nan"), float("inf"), True, "0.1", None])
def test_required_durations_must_be_positive_finite_numbers(name, bad):
    with pytest.raises(SegmenterConfigError):
        make_config(**{name: bad})


@pytest.mark.parametrize("name", ["onset_padding_s", "pre_roll_s"])
def test_padding_and_pre_roll_may_be_zero_but_not_negative(name):
    assert getattr(make_config(**{name: 0.0}), name) == 0.0
    with pytest.raises(SegmenterConfigError):
        make_config(**{name: -0.001})


@pytest.mark.parametrize("overrides", [
    dict(min_utterance_s=0.02),                       # below the start confirmation
    dict(max_utterance_s=0.04),                       # below the minimum utterance
    dict(no_speech_timeout_s=0.02),                   # speech could never be confirmed
    dict(pre_roll_s=0.5),                             # pre-roll must be < max utterance
    dict(onset_padding_s=0.5),                        # padding must be < max utterance
])
def test_relationship_constraints_rejected(overrides):
    with pytest.raises(SegmenterConfigError):
        make_config(**overrides)


@pytest.mark.parametrize("name,bad", [("sample_rate", 0), ("sample_rate", 16000.0), ("vad_frame_samples", -1),
                                      ("vad_frame_samples", True)])
def test_rate_and_frame_must_be_positive_integers(name, bad):
    with pytest.raises(SegmenterConfigError):
        make_config(**{name: bad})


def test_config_is_frozen():
    cfg = make_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.start_threshold = 0.9


@pytest.mark.parametrize("seconds,rate,expected", [
    (0.1, 16000, 1600),
    (0.032, 16000, 512),
    (0.07, 100, 7),              # 0.07 * 100 = 7.000000000000001 in floats: naive ceil would give 8
    (0.28, 100, 28),             # 28.000000000000004
    (0.07, 44100, 3087),         # 3087.0000000000005
    (0.03, 1000, 30),
    (0.0300000001, 1000, 30),    # float noise within 1e-6 of a whole sample
    (0.0301, 1000, 31),          # a real fraction rounds UP
    (0.030002, 1000, 31),
    (0.0300000005, 1000, 30),    # 30.0000005: within the 1e-6 tolerance of a whole sample
    (0.030000002, 1000, 31),     # 30.000002: outside the tolerance, so it rounds up
    (1 / 3, 16000, 5334),        # 5333.33 -> up
    (0.0001, 1000, 1),           # never rounds a positive duration to zero
])
def test_duration_to_sample_conversion_is_pinned(seconds, rate, expected):
    assert _to_samples(seconds, rate) == expected


def test_config_properties_use_the_same_rounding_rule():
    cfg = make_config(sample_rate=16000, vad_frame_samples=512, start_confirmation_s=0.1, end_silence_s=0.0301,
                      onset_padding_s=0.0, pre_roll_s=0.2, no_speech_timeout_s=1.0, min_utterance_s=0.25,
                      max_utterance_s=3.0)
    assert cfg.start_confirmation_samples == 1600
    assert cfg.end_silence_samples == 482              # 481.6 rounds UP
    assert cfg.onset_padding_samples == 0
    assert (cfg.pre_roll_samples, cfg.no_speech_timeout_samples) == (3200, 16000)
    assert (cfg.min_utterance_samples, cfg.max_utterance_samples) == (4000, 48000)


def test_production_settings_are_all_unconfigured():
    keys = ["VAD_START_THRESHOLD", "VAD_END_THRESHOLD", "VAD_START_CONFIRMATION_S", "VAD_END_SILENCE_S",
            "VAD_ONSET_PADDING_S", "VOICE_PRE_ROLL_S", "VOICE_NO_SPEECH_TIMEOUT_S", "VOICE_MIN_UTTERANCE_S",
            "VOICE_MAX_UTTERANCE_S"]
    for key in keys:
        assert hasattr(settings, key), key
        assert getattr(settings, key) is None, f"{key} must stay None (NOT CALIBRATED)"


def test_loader_refuses_the_uncalibrated_production_settings():
    with pytest.raises(SegmenterConfigError) as err:
        segmenter_config_from_settings(vad_frame_samples=512)
    assert "VOICE_PRE_ROLL_S is not calibrated/configured" in str(err.value)
    assert "VAD_START_THRESHOLD is not calibrated/configured" in str(err.value)


def _explicit_settings(**missing):
    values = dict(
        TARGET_SAMPLE_RATE=1000, VAD_START_THRESHOLD=0.6, VAD_END_THRESHOLD=0.4, VAD_START_CONFIRMATION_S=0.03,
        VAD_END_SILENCE_S=0.04, VAD_ONSET_PADDING_S=0.02, VOICE_PRE_ROLL_S=0.1, VOICE_NO_SPEECH_TIMEOUT_S=0.2,
        VOICE_MIN_UTTERANCE_S=0.05, VOICE_MAX_UTTERANCE_S=0.5,
    )
    values.update(missing)
    return SimpleNamespace(**values)


def test_loader_names_exactly_the_missing_value():
    with pytest.raises(SegmenterConfigError) as err:
        segmenter_config_from_settings(10, settings_module=_explicit_settings(VOICE_PRE_ROLL_S=None))
    assert str(err.value) == "VOICE_PRE_ROLL_S is not calibrated/configured"


def test_loader_builds_a_config_from_explicit_settings():
    cfg = segmenter_config_from_settings(10, settings_module=_explicit_settings())
    assert cfg == make_config()
    assert cfg.sample_rate == 1000 and cfg.vad_frame_samples == 10


def test_loader_still_validates_what_it_loads():
    with pytest.raises(SegmenterConfigError):
        segmenter_config_from_settings(10, settings_module=_explicit_settings(VAD_END_THRESHOLD=0.6))


def test_segmenter_rejects_engine_and_config_mismatches():
    with pytest.raises(SegmenterConfigError):
        UtteranceSegmenter(make_config(), ScriptedVadEngine([0.0], frame_samples=20))
    with pytest.raises(SegmenterConfigError):
        UtteranceSegmenter(make_config(), ScriptedVadEngine([0.0], sample_rate=16000))
    with pytest.raises(SegmenterConfigError):
        UtteranceSegmenter(make_config(), object())
    with pytest.raises(SegmenterConfigError):
        UtteranceSegmenter({"start_threshold": 0.5}, ScriptedVadEngine([0.0]))


# =======================================================================================
# Segmentation, no wake replay (tests 35-54)
# =======================================================================================

def test_pure_silence_ends_in_no_speech_timeout():          # 35
    seg, eng, res = run_plain(make_config(), runs((0.0, 60)), 400)
    assert res.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT
    assert res.segment is None and res.decided_at_sample == 200
    assert seg.phase is SegmenterPhase.FINISHED and seg.result is res


def test_speech_start_end_and_exact_indices():             # 36, 41, 42, 52, 53
    _, _, res = run_plain(make_config(), runs((0.0, 3), (0.9, 5), (0.0, 10)), 200)
    seg = res.segment
    assert res.outcome is SegmentOutcome.COMPLETE
    assert seg.onset_sample == 30            # first frame of the confirmed run
    assert seg.start_sample == 10            # onset minus 20 samples of padding
    assert seg.end_sample == 80              # end of the LAST SPEECH frame, trailing silence excluded
    assert res.decided_at_sample == 120      # 4 silent frames (40 samples) after the speech
    assert np.array_equal(seg.samples, index_pcm(10, 70))
    assert seg.duration_samples == len(seg.samples) == seg.end_sample - seg.start_sample
    assert seg.samples.dtype == np.int16 and seg.samples.ndim == 1
    assert seg.sample_rate == SR and seg.duration_s == pytest.approx(0.07)
    assert seg.activation_sample == 0 and seg.starts_before_activation is False


def test_start_threshold_equality_starts_speech():          # 37
    _, _, res = run_plain(make_config(), runs((0.6, 6), (0.0, 10)), 200)
    assert res.outcome is SegmentOutcome.COMPLETE and res.segment.onset_sample == 0


def test_just_below_the_start_threshold_never_starts_speech():   # 37
    _, _, res = run_plain(make_config(), runs((NEXT_BELOW_06, 30)), 400)
    assert res.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT


def test_start_confirmation_requirement():                  # 38
    two = run_plain(make_config(), runs((0.9, 2), (0.0, 40)), 400)[2]
    three = run_plain(make_config(), runs((0.9, 3), (0.0, 40)), 400)[2]
    assert two.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT          # 20 samples < 30: not confirmed
    assert three.outcome is SegmentOutcome.TOO_SHORT                # 30 samples: confirmed (then too short)


def test_isolated_short_bursts_are_rejected():              # 39
    scores = runs((0.9, 2), (0.0, 1), (0.9, 2), (0.0, 1), (0.0, 2), (0.9, 6), (0.0, 10))
    _, _, res = run_plain(make_config(), scores, 300)
    assert res.outcome is SegmentOutcome.COMPLETE
    assert res.segment.onset_sample == 80                           # the run of six, not an earlier burst
    assert res.segment.start_sample == 60 and res.segment.end_sample == 140


def test_a_dip_below_the_start_threshold_restarts_confirmation():
    # 0.9, 0.9, 0.5 (>= end but < start), 0.9: the 0.5 frame breaks the confirmation run
    _, _, res = run_plain(make_config(), runs((0.9, 2), (0.5, 1), (0.9, 6), (0.0, 10)), 300)
    assert res.segment.onset_sample == 30


def test_end_threshold_equality_counts_as_silence():        # 40
    seg, _, res = run_plain(make_config(), runs((0.9, 6), (0.4, 4), (0.0, 4)), 300)
    assert res.outcome is SegmentOutcome.COMPLETE
    assert res.segment.end_sample == 60 and res.decided_at_sample == 100


def test_scores_just_above_the_end_threshold_keep_the_utterance_alive():   # 40, hysteresis band
    _, _, res = run_plain(make_config(), runs((0.9, 6), (NEXT_ABOVE_04, 4), (0.0, 4)), 300)
    assert res.segment.end_sample == 100 and res.decided_at_sample == 140


def test_a_score_between_the_thresholds_never_starts_speech():
    _, _, res = run_plain(make_config(), runs((0.5, 40)), 400)
    assert res.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT


def test_end_silence_requirement():                         # 41
    bridged = run_plain(make_config(), runs((0.9, 6), (0.0, 3), (0.9, 4), (0.0, 6)), 300)[2]
    split = run_plain(make_config(), runs((0.9, 6), (0.0, 4), (0.9, 4), (0.0, 6)), 300)[2]
    assert bridged.segment.end_sample == 130 and bridged.decided_at_sample == 170   # 3 silent frames bridged
    assert split.segment.end_sample == 60 and split.decided_at_sample == 100        # 4 silent frames end it


def test_onset_padding_applied():                           # 42
    res = run_plain(make_config(), runs((0.0, 5), (0.9, 6), (0.0, 10)), 300)[2]
    assert (res.segment.onset_sample, res.segment.start_sample) == (50, 30)
    none = run_plain(make_config(onset_padding_s=0.0), runs((0.0, 5), (0.9, 6), (0.0, 10)), 300)[2]
    assert (none.segment.onset_sample, none.segment.start_sample) == (50, 50)


def test_onset_padding_clamps_to_the_oldest_available_sample():   # 43
    at_start = run_plain(make_config(), runs((0.9, 6), (0.0, 10)), 300)[2]
    assert at_start.segment.start_sample == 0 and at_start.segment.onset_sample == 0   # never negative
    # activated mid-stream with no replay: nothing before the activation point exists to include
    mid = run_plain(make_config(), runs((0.9, 6), (0.0, 10)), 300, activation=500)[2]
    assert mid.segment.onset_sample == 500 and mid.segment.start_sample == 500
    assert np.array_equal(mid.segment.samples, index_pcm(500, 60))


def test_minimum_utterance_and_its_exact_boundary():        # 44, 45
    four = run_plain(make_config(), runs((0.9, 4), (0.0, 10)), 300)[2]     # 40 samples < 50
    five = run_plain(make_config(), runs((0.9, 5), (0.0, 10)), 300)[2]     # exactly 50: passes
    assert four.outcome is SegmentOutcome.TOO_SHORT and four.segment is None
    assert five.outcome is SegmentOutcome.COMPLETE and five.segment is not None


def test_maximum_duration_cuts_deterministically_and_keeps_the_audio():   # 46
    seg, _, res = run_plain(make_config(), runs((0.9, 80)), 800)
    assert res.outcome is SegmentOutcome.MAX_DURATION
    assert res.segment is not None and res.segment.duration_samples == 500
    assert (res.segment.start_sample, res.segment.end_sample) == (0, 500) and res.decided_at_sample == 500
    assert np.array_equal(res.segment.samples, index_pcm(0, 500))


def test_maximum_duration_exact_boundary():                 # 47
    below = run_plain(make_config(), runs((0.9, 49), (0.0, 10)), 800)[2]     # 490 samples of speech
    at = run_plain(make_config(), runs((0.9, 50), (0.0, 10)), 800)[2]        # 500: reaches the cap while speaking
    assert below.outcome is SegmentOutcome.COMPLETE and below.segment.duration_samples == 490
    assert at.outcome is SegmentOutcome.MAX_DURATION and at.segment.duration_samples == 500


def test_maximum_duration_is_measured_from_the_padded_start():
    res = run_plain(make_config(), runs((0.0, 5), (0.9, 80)), 800)[2]
    assert res.outcome is SegmentOutcome.MAX_DURATION
    assert (res.segment.start_sample, res.segment.end_sample) == (30, 530)
    assert res.segment.duration_samples == 500 and res.segment.onset_sample == 50


def test_trailing_silence_does_not_trigger_the_maximum():
    # speech ends at 480, then 40 samples of silence push the stream past 500: still COMPLETE
    res = run_plain(make_config(), runs((0.9, 48), (0.0, 6)), 800)[2]
    assert res.outcome is SegmentOutcome.COMPLETE and res.segment.end_sample == 480


def test_no_speech_timeout_exact_boundary():                # 48, 49
    # speech confirmed in the frame that ENDS exactly at the deadline (200) counts as speech
    on_time = run_plain(make_config(), runs((0.0, 17), (0.9, 6), (0.0, 10)), 400)[2]
    # one frame later the deadline frame has already passed without a confirmed onset
    late = run_plain(make_config(), runs((0.0, 18), (0.9, 6), (0.0, 10)), 400)[2]
    assert on_time.outcome is SegmentOutcome.COMPLETE and on_time.segment.onset_sample == 170
    assert late.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT and late.decided_at_sample == 200


def test_eof_during_speech_returns_the_partial_segment():   # 50
    seg, _, res = run_plain(make_config(), runs((0.0, 2), (0.9, 8)), 100)
    assert res.outcome is SegmentOutcome.STREAM_ENDED
    assert res.segment.start_sample == 0 and res.segment.end_sample == 100
    assert np.array_equal(res.segment.samples, index_pcm(0, 100))
    assert res.decided_at_sample == 100 and res.unscored_samples == 0


def test_eof_never_pads_a_partial_frame():
    _, eng, res = run_plain(make_config(), runs((0.0, 2), (0.9, 8)), 105)
    assert res.outcome is SegmentOutcome.STREAM_ENDED
    assert res.segment.end_sample == 100         # the 5 unscored samples are not invented into a frame
    assert res.unscored_samples == 5 and len(eng.seen_frames) == 10


def test_eof_with_too_short_speech_is_too_short():
    res = run_plain(make_config(), runs((0.9, 4)), 40)[2]
    assert res.outcome is SegmentOutcome.TOO_SHORT and res.segment is None


def test_eof_with_no_speech():                              # 51
    res = run_plain(make_config(), runs((0.0, 10)), 100)[2]
    assert res.outcome is SegmentOutcome.NO_SPEECH_STREAM_ENDED and res.segment is None
    assert res.decided_at_sample == 100


def test_eof_during_unconfirmed_candidate_is_no_speech():
    res = run_plain(make_config(), runs((0.0, 2), (0.9, 2)), 40)[2]
    assert res.outcome is SegmentOutcome.NO_SPEECH_STREAM_ENDED


def test_phase_progression():
    eng = ScriptedVadEngine(runs((0.0, 2), (0.9, 6), (0.0, 10)))
    seg = UtteranceSegmenter(make_config(), eng)
    assert seg.phase is SegmenterPhase.IDLE and seg.expected_next_sample is None
    seg.activate(0)
    assert seg.phase is SegmenterPhase.LISTENING and seg.expected_next_sample == 0
    seg.push(index_pcm(0, 50), 0)                       # frames 2-4 are speech: confirmed at the end of frame 4
    assert seg.phase is SegmenterPhase.IN_SPEECH and seg.expected_next_sample == 50
    result, _ = feed(seg, 50, 200)
    assert result is not None and seg.phase is SegmenterPhase.FINISHED


def _blocks(total, size):
    return [(p, index_pcm(p, min(size, total - p))) for p in range(0, total, size)]


def test_segment_pcm_is_an_independent_read_only_copy():     # 54
    eng = ScriptedVadEngine(runs((0.0, 2), (0.9, 6), (0.0, 10)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    sent, res = [], None
    for start, block in _blocks(200, 10):
        sent.append(block)
        res = seg.push(block, start)
        if res:
            break
    segment = res.segment
    expected = index_pcm(segment.start_sample, segment.duration_samples)
    for block in sent:                       # trash every input block after the fact
        block[:] = 0
    assert np.array_equal(segment.samples, expected)
    assert not segment.samples.flags.writeable
    assert all(not np.shares_memory(segment.samples, b) for b in sent)
    with pytest.raises(ValueError):
        segment.samples[0] = 99


def test_inputs_are_not_mutated_and_engine_frames_are_read_only():   # 36 (no mutation)
    eng = ScriptedVadEngine(runs((0.0, 2), (0.9, 6), (0.0, 10)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    sent = [(s, b.copy(), b) for s, b in _blocks(200, 13)]
    for start, _, block in sent:
        if seg.push(block, start):
            break
    for _, original, block in sent:
        assert np.array_equal(block, original)
    assert eng.frames_were_read_only and all(eng.frames_were_read_only)


def test_speech_segment_validates_its_own_invariants():
    ok = dict(samples=index_pcm(0, 10), sample_rate=SR, start_sample=0, end_sample=10, onset_sample=2, activation_sample=0)
    SpeechSegment(**ok)
    for change in (dict(end_sample=11), dict(end_sample=0), dict(onset_sample=10), dict(start_sample=-1),
                   dict(sample_rate=0), dict(samples=index_pcm(0, 10).astype(np.int32))):
        with pytest.raises(SegmenterInputError):
            SpeechSegment(**{**ok, **change})


def test_speech_segment_copies_what_it_is_given():
    raw = index_pcm(0, 10)
    seg = SpeechSegment(raw, SR, 0, 10, 2, 0)
    raw[:] = 0
    assert np.array_equal(seg.samples, index_pcm(0, 10)) and not seg.samples.flags.writeable


# =======================================================================================
# Wake handoff (tests 24-34) - scripted scores, sample-exact
# =======================================================================================

def test_immediate_command_is_captured_with_no_pause_after_the_wake_word():   # 24, 25, 26, 27
    cfg = make_config(pre_roll_s=0.25)
    # speech runs 400-700 with NO silence: wake phrase 400-560, command 560-700. The wake detector
    # fires at 520, while the phrase is still being spoken.
    run = run_wake(cfg, speech=[(400, 700)], activation=520, buffered_until=530, buffer_capacity=300, stream_end=1000)
    res = run.result
    assert res.outcome is SegmentOutcome.COMPLETE
    seg = res.segment
    assert run.snapshot.start_sample < 520 < run.snapshot.end_sample           # replay holds samples before Detection.end_sample
    assert seg.starts_before_activation and seg.start_sample == 380 and seg.onset_sample == 400
    assert seg.end_sample == 700                                                # command fully retained
    assert np.array_equal(seg.samples, index_pcm(380, 320))
    assert seg.start_sample <= 400 and seg.end_sample >= 560 + 140             # first command samples and last ones present
    assert run.engine.reset_calls == 1                                          # VAD reset once, at activation
    assert res.activation_sample == 520


def test_a_short_pause_inside_the_phrase_does_not_split_the_utterance():
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(400, 500), (530, 700)], activation=520, buffered_until=530, buffer_capacity=300,
                   stream_end=1000)
    assert run.result.outcome is SegmentOutcome.COMPLETE
    assert (run.result.segment.start_sample, run.result.segment.end_sample) == (380, 700)


def test_a_detection_end_sample_is_accepted_as_the_activation_point():
    det = make_detection(520)                       # a real wakeword.detector.Detection
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(400, 700)], activation=det.end_sample, buffered_until=530, buffer_capacity=300,
                   stream_end=1000)
    res = run.result
    assert res.activation_sample == det.end_sample == 520
    # the detector chunk end is only an anchor: the phrase/command runs on past it and is kept
    assert res.segment.start_sample < det.end_sample < res.segment.end_sample


def test_detection_end_sample_does_not_define_the_phrase_end():   # a wake chunk end is not the phrase end
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(400, 700)], activation=450, buffered_until=460, buffer_capacity=300, stream_end=1000)
    assert run.result.segment.end_sample == 700 > 450


def test_no_speech_timer_starts_at_activation_not_at_replay_start():   # 28
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[], activation=450, buffered_until=460, buffer_capacity=300, stream_end=1000)
    assert run.origin == 200 and run.snapshot.start_sample == 160       # long silent replay behind the activation
    assert run.result.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT
    assert run.result.decided_at_sample == 450 + 200                    # activation + timeout, not origin + timeout


def test_wake_at_stream_beginning_with_a_partial_buffer():    # 29
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(30, 200)], activation=50, buffered_until=60, buffer_capacity=300, stream_end=500)
    assert run.snapshot.start_sample == 0 and len(run.snapshot) == 60 < 300
    assert run.origin == 0
    seg = run.result.segment
    assert (seg.start_sample, seg.onset_sample, seg.end_sample) == (10, 30, 200)
    assert np.array_equal(seg.samples, index_pcm(10, 190))


def test_wake_at_stream_beginning_clamps_padding_to_the_oldest_sample():
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(0, 200)], activation=50, buffered_until=60, buffer_capacity=300, stream_end=500)
    assert run.result.segment.start_sample == 0     # not -20


def test_onset_padding_clamps_to_the_oldest_replayed_sample():
    cfg = make_config(pre_roll_s=0.1)               # replay reaches back only to activation - 100
    run = run_wake(cfg, speech=[(400, 700)], activation=410, buffered_until=420, buffer_capacity=300, stream_end=1000)
    assert run.origin == 310
    assert run.result.segment.start_sample == 380   # 400 - 20: inside the replay, not clamped
    run2 = run_wake(make_config(pre_roll_s=0.02, onset_padding_s=0.05), speech=[(400, 700)], activation=410,
                    buffered_until=420, buffer_capacity=300, stream_end=1000)
    assert run2.origin == 390 and run2.result.segment.start_sample == 390      # clamped to the oldest replayed sample


def test_replay_is_bounded_by_the_pre_roll_and_by_the_buffer():   # 30
    cfg = make_config(pre_roll_s=0.05)
    run = run_wake(cfg, speech=[], activation=1000, buffered_until=1000, buffer_capacity=100, stream_end=1005,
                   finish=False)
    assert run.buffer.size == 100 <= run.buffer.capacity and len(run.snapshot) == 100
    assert run.origin == 950
    assert run.engine.position == 5                  # only 50 samples (5 frames) were replayed, not all 100
    big = run_wake(make_config(pre_roll_s=0.4), speech=[], activation=1000, buffered_until=1000, buffer_capacity=100,
                   stream_end=1005, finish=False)
    assert big.origin == 900 and big.engine.position == 10   # the buffer, not the pre-roll, is the limit


def test_old_speech_ending_before_activation_is_not_returned():   # 31 (the activation anchor)
    cfg = make_config(pre_roll_s=0.45)
    run = run_wake(cfg, speech=[(100, 200), (400, 600)], activation=450, buffered_until=470, buffer_capacity=500,
                   stream_end=1000)
    res = run.result
    assert res.outcome is SegmentOutcome.COMPLETE
    assert (res.segment.start_sample, res.segment.end_sample) == (380, 600)
    assert np.array_equal(res.segment.samples, index_pcm(380, 220))
    assert res.segment.start_sample > 200                            # nothing of the old utterance


def test_old_speech_alone_gives_no_speech_timeout_not_a_segment():   # 31
    cfg = make_config(pre_roll_s=0.45)
    run = run_wake(cfg, speech=[(100, 200)], activation=450, buffered_until=470, buffer_capacity=500, stream_end=1000)
    assert run.result.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT and run.result.segment is None
    assert run.result.decided_at_sample == 650


def test_old_speech_whose_end_silence_completes_after_activation_is_still_old():
    cfg = make_config(pre_roll_s=0.2)
    # speech 380-430 ends before activation (440) but its end-silence (40 samples) completes after it
    run = run_wake(cfg, speech=[(380, 430)], activation=440, buffered_until=450, buffer_capacity=300, stream_end=1000)
    assert run.result.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT and run.result.segment is None


def test_activation_anchor_boundary_is_exact():
    cfg = make_config(pre_roll_s=0.2)
    ends_at = run_wake(cfg, speech=[(400, 450)], activation=450, buffered_until=470, buffer_capacity=300, stream_end=1000)
    one_frame_past = run_wake(cfg, speech=[(400, 460)], activation=450, buffered_until=470, buffer_capacity=300,
                              stream_end=1000)
    assert ends_at.result.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT       # last speech sample == activation: before it
    assert one_frame_past.result.outcome is SegmentOutcome.COMPLETE         # one frame past: it reaches the activation
    assert one_frame_past.result.segment.end_sample == 460


def test_speech_continuing_across_the_activation_is_retained():   # 32
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(400, 600)], activation=500, buffered_until=510, buffer_capacity=300, stream_end=1000)
    seg = run.result.segment
    assert (seg.start_sample, seg.end_sample) == (380, 600) and seg.starts_before_activation
    assert seg.activation_sample == 500


def test_speech_that_begins_after_the_activation_is_retained():
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(600, 800)], activation=500, buffered_until=510, buffer_capacity=300, stream_end=1000)
    seg = run.result.segment
    assert (seg.start_sample, seg.onset_sample, seg.end_sample) == (580, 600, 800)
    assert not seg.starts_before_activation          # padding reaches back only into silence after the wake point


def test_speech_merged_with_the_wake_phrase_keeps_the_old_words_known_limit():
    cfg = make_config(pre_roll_s=0.45)
    # old speech 100-200, a 30-sample gap (< end_silence 40), then speech across the activation: ONE region
    run = run_wake(cfg, speech=[(100, 200), (230, 600)], activation=450, buffered_until=470, buffer_capacity=500,
                   stream_end=1000)
    assert run.result.segment.start_sample == 80      # documented limitation: cannot be split without a pause


def test_replay_may_finish_the_capture_by_itself():
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[(400, 500)], activation=450, buffered_until=600, buffer_capacity=400, stream_end=1000,
                   finish=False)
    res = run.result
    assert res is run.seg.result and res.outcome is SegmentOutcome.COMPLETE
    assert res.decided_at_sample == 540 and res.unscored_samples == 600 - 540
    assert run.seg.phase is SegmenterPhase.FINISHED
    with pytest.raises(SegmenterStateError):
        run.seg.push(index_pcm(600, 10), 600)


def test_second_activation_during_active_capture_changes_nothing():   # 33, 34
    cfg = make_config(pre_roll_s=0.25)
    kw = dict(speech=[(400, 700)], activation=520, buffered_until=530, buffer_capacity=300, stream_end=1000)
    control = run_wake(cfg, **kw)
    again = run_wake(cfg, second_activation_at=600, **kw)               # while IN_SPEECH
    assert again.engine.reset_calls == 1                                # not reset again
    assert again.result.ignored_reactivation_count == 1 and again.seg.ignored_reactivation_count == 1
    assert again.result.activation_sample == 520                        # activation point not replaced
    a, b = again.result.segment, control.result.segment
    assert (a.start_sample, a.end_sample, a.onset_sample) == (b.start_sample, b.end_sample, b.onset_sample)
    assert np.array_equal(a.samples, b.samples)                         # no duplicate or restarted capture
    assert again.result.decided_at_sample == control.result.decided_at_sample
    assert control.result.ignored_reactivation_count == 0


def test_second_activation_before_speech_does_not_restart_the_no_speech_timer():   # 33
    cfg = make_config(pre_roll_s=0.25)
    run = run_wake(cfg, speech=[], activation=500, buffered_until=510, buffer_capacity=300, stream_end=1500,
                   second_activation_at=540)
    assert run.engine.reset_calls == 1
    assert run.result.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT
    assert run.result.decided_at_sample == 700          # activation 500 + 200; a restart at 540 would give 740
    assert run.result.ignored_reactivation_count == 1 and run.result.activation_sample == 500


def test_activate_returns_a_clear_indicator():
    eng = ScriptedVadEngine(runs((0.0, 100)))
    seg = UtteranceSegmenter(make_config(), eng)
    assert seg.activate(0) is True
    assert seg.activate(5) is False and seg.activate(7) is False
    assert seg.ignored_reactivation_count == 2 and seg.activation_sample == 0 and eng.reset_calls == 1


# -- state and argument errors --------------------------------------------------------

def test_activation_must_lie_inside_the_replay():
    buf = RollingPcmBuffer(100)
    buf.push(index_pcm(100, 50), 100)
    snap = buf.snapshot()                                         # [100, 150)
    for bad in (99, 151, 0, 10_000):
        seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 50))))
        with pytest.raises(SegmenterInputError):
            seg.activate(bad, snap)
        assert seg.phase is SegmenterPhase.IDLE                   # a rejected activation changes nothing


@pytest.mark.parametrize("bad", [-1, 1.5, True, "5", None])
def test_invalid_activation_sample_rejected(bad):
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 5))))
    with pytest.raises(SegmenterInputError):
        seg.activate(bad)


def test_replay_must_be_a_snapshot():
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 5))))
    with pytest.raises(SegmenterInputError):
        seg.activate(0, index_pcm(0, 10))


def test_state_errors():
    eng = ScriptedVadEngine(runs((0.0, 100)))
    seg = UtteranceSegmenter(make_config(), eng)
    with pytest.raises(SegmenterStateError):
        seg.push(index_pcm(0, 10), 0)          # not activated
    with pytest.raises(SegmenterStateError):
        seg.finish()                           # not activated
    seg.activate(0)
    result, _ = feed(seg, 0, 400)
    assert result.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT
    for call in (lambda: seg.push(index_pcm(400, 10), 400), lambda: seg.finish(), lambda: seg.activate(400)):
        with pytest.raises(SegmenterStateError):
            call()
    assert seg.result is result                # still the same, untouched result


def test_the_first_live_block_must_follow_the_replay_not_the_activation_point():
    buf = RollingPcmBuffer(100)
    buf.push(index_pcm(0, 80), 0)
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 100))))
    seg.activate(50, buf.snapshot())
    assert seg.expected_next_sample == 80
    with pytest.raises(AudioContinuityError):
        seg.push(index_pcm(50, 10), 50)        # replaying audio the segmenter already holds is an overlap
    with pytest.raises(AudioContinuityError):
        seg.push(index_pcm(90, 10), 90)        # skipping audio is a gap
    seg.push(index_pcm(80, 10), 80)


# =======================================================================================
# Reframing and continuity (tests 55-61)
# =======================================================================================

def test_small_blocks_combine_into_one_vad_frame():            # 55
    eng = ScriptedVadEngine(runs((0.0, 50)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    seg.push(index_pcm(0, 3), 0)
    seg.push(index_pcm(3, 3), 3)
    seg.push(index_pcm(6, 3), 6)
    assert len(eng.seen_frames) == 0                           # 9 samples: not a frame yet
    seg.push(index_pcm(9, 1), 9)
    assert len(eng.seen_frames) == 1 and np.array_equal(eng.seen_frames[0], index_pcm(0, FRAME))


def test_one_large_block_creates_several_frames():             # 56
    eng = ScriptedVadEngine(runs((0.0, 50)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    seg.push(index_pcm(0, 105), 0)
    assert len(eng.seen_frames) == 10
    assert seg.expected_next_sample == 105


@pytest.mark.parametrize("size", [1, 3, 7, 10, 13, 25, 99, 100, 1000])
def test_frames_keep_exact_absolute_positions_for_any_block_size(size):     # 57, 58
    eng = ScriptedVadEngine(runs((0.0, 3), (0.9, 8), (0.0, 20)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    res, _ = feed(seg, 0, 400, (size,))
    assert res is not None
    for i, frame in enumerate(eng.seen_frames):                # every frame is exactly stream[i*F : (i+1)*F]
        assert np.array_equal(frame, index_pcm(i * FRAME, FRAME)), i


def test_frame_spanning_two_blocks_is_not_split_or_duplicated():     # 57
    eng = ScriptedVadEngine(runs((0.0, 50)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    seg.push(index_pcm(0, 7), 0)
    seg.push(index_pcm(7, 7), 7)
    assert len(eng.seen_frames) == 1
    assert np.array_equal(eng.seen_frames[0], index_pcm(0, 10))
    seg.push(index_pcm(14, 7), 14)
    assert len(eng.seen_frames) == 2 and np.array_equal(eng.seen_frames[1], index_pcm(10, 10))


def test_replay_frame_positions_are_absolute_and_continue_into_live_audio():   # 58
    run = run_wake(make_config(pre_roll_s=0.25), speech=[], activation=455, buffered_until=470, buffer_capacity=300,
                   stream_end=480, finish=False)
    assert run.snapshot.start_sample == 170 and run.origin == 205       # activation - pre-roll, inside the buffer
    frames = run.engine.seen_frames
    assert len(frames) == 27                                            # 26 replay frames + the one spanning replay/live
    for i, frame in enumerate(frames):
        assert np.array_equal(frame, index_pcm(205 + i * FRAME, FRAME)), i


def test_forward_gap_is_rejected_and_changes_nothing():         # 59
    eng = ScriptedVadEngine(runs((0.0, 50)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    seg.push(index_pcm(0, 20), 0)
    with pytest.raises(AudioContinuityError, match="gap"):
        seg.push(index_pcm(25, 10), 25)
    assert seg.expected_next_sample == 20 and len(eng.seen_frames) == 2
    seg.push(index_pcm(20, 10), 20)                                # the stream can carry on correctly
    assert len(eng.seen_frames) == 3


def test_overlapping_or_backward_start_is_rejected():            # 60
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 50))))
    seg.activate(0)
    seg.push(index_pcm(0, 20), 0)
    for start in (19, 10, 0):
        with pytest.raises(AudioContinuityError, match="overlap"):
            seg.push(index_pcm(start, 10), start)
    assert seg.expected_next_sample == 20


def test_a_replayed_stream_with_no_activation_offset_must_start_at_the_activation_sample():
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 50))))
    seg.activate(300)
    with pytest.raises(AudioContinuityError):
        seg.push(index_pcm(0, 10), 0)
    seg.push(index_pcm(300, 10), 300)


def test_repeated_identical_runs_are_deterministic():             # 61
    def once():
        run = run_wake(make_config(pre_roll_s=0.25), speech=[(400, 700)], activation=520, buffered_until=530,
                       buffer_capacity=300, stream_end=1000)
        r = run.result
        return (r.outcome, r.decided_at_sample, r.unscored_samples, r.segment.start_sample, r.segment.end_sample,
                r.segment.onset_sample, r.segment.samples.tobytes())
    assert once() == once() == once()


@pytest.mark.parametrize("size", [1, 3, 7, 10, 13, 64, 1000])
def test_result_does_not_depend_on_the_input_block_size(size):
    cfg = make_config()
    ref = run_plain(cfg, runs((0.0, 3), (0.9, 8), (0.0, 20)), 400, (FRAME,))[2]
    got = run_plain(cfg, runs((0.0, 3), (0.9, 8), (0.0, 20)), 400, (size,))[2]
    assert (got.outcome, got.decided_at_sample) == (ref.outcome, ref.decided_at_sample)
    assert (got.segment.start_sample, got.segment.end_sample) == (ref.segment.start_sample, ref.segment.end_sample)
    assert np.array_equal(got.segment.samples, ref.segment.samples)


def test_wake_handoff_result_does_not_depend_on_buffer_or_live_block_size():
    cfg = make_config(pre_roll_s=0.25)
    kw = dict(speech=[(400, 700)], activation=520, buffered_until=530, buffer_capacity=300, stream_end=1000)
    ref = run_wake(cfg, **kw).result
    for block, live in [(1, 1), (3, 17), (50, 3), (13, 100)]:
        got = run_wake(cfg, block=block, live_block=live, **kw).result
        assert (got.outcome, got.segment.start_sample, got.segment.end_sample) == (
            ref.outcome, ref.segment.start_sample, ref.segment.end_sample)
        assert np.array_equal(got.segment.samples, ref.segment.samples)


# =======================================================================================
# Reset and recovery (tests 62-68)
# =======================================================================================

def _scenario_complete():
    return runs((0.0, 3), (0.9, 8), (0.0, 20)), 400, True


def _scenario_timeout():
    return runs((0.0, 60)), 400, True


def _scenario_too_short():
    return runs((0.9, 3), (0.0, 20)), 400, True


def _scenario_max():
    return runs((0.9, 80)), 800, True


def _scenario_stream_ended():
    return runs((0.0, 2), (0.9, 8)), 100, True


def _scenario_no_speech_stream_ended():
    return runs((0.0, 10)), 100, True


_FIRST = [
    (_scenario_complete, SegmentOutcome.COMPLETE),
    (_scenario_timeout, SegmentOutcome.NO_SPEECH_TIMEOUT),
    (_scenario_too_short, SegmentOutcome.TOO_SHORT),
    (_scenario_max, SegmentOutcome.MAX_DURATION),
    (_scenario_stream_ended, SegmentOutcome.STREAM_ENDED),
    (_scenario_no_speech_stream_ended, SegmentOutcome.NO_SPEECH_STREAM_ENDED),
]


@pytest.mark.parametrize("scenario,outcome", _FIRST, ids=[o.value for _, o in _FIRST])
def test_every_outcome_can_be_followed_by_a_clean_next_utterance(scenario, outcome):   # 62-67
    cfg = make_config()
    first_scores, first_total, _ = scenario()
    eng = ScriptedVadEngine(first_scores)
    seg = UtteranceSegmenter(cfg, eng)
    seg.activate(0)
    res1, _ = feed(seg, 0, first_total)
    if res1 is None:
        res1 = seg.finish()
    assert res1.outcome is outcome
    consumed = eng.position
    assert consumed > 0 and eng.reset_calls == 1

    # second utterance, elsewhere in the stream, scored from a fresh script
    second = runs((0.0, 4), (0.9, 7), (0.0, 20))
    seg.reset()
    assert seg.phase is SegmenterPhase.IDLE and seg.result is None and seg.activation_sample is None
    assert seg.ignored_reactivation_count == 0 and seg.expected_next_sample is None
    eng.set_scores(second)
    seg.activate(5000)
    assert eng.reset_calls == 2 and eng.position == 0                  # 68: no old VAD score state survives
    res2, _ = feed(seg, 5000, 300)

    fresh = run_plain(cfg, second, 300, activation=5000)[2]
    assert res2.outcome is SegmentOutcome.COMPLETE and res2.activation_sample == 5000
    assert (res2.decided_at_sample, res2.unscored_samples) == (fresh.decided_at_sample, fresh.unscored_samples)
    assert res2.ignored_reactivation_count == 0
    assert res2.segment.start_sample >= 5000                          # 67: no old absolute indices
    assert np.array_equal(res2.segment.samples, index_pcm(res2.segment.start_sample, res2.segment.duration_samples))
    assert np.array_equal(res2.segment.samples, fresh.segment.samples)  # no old PCM leaked in


def test_reset_is_safe_from_any_phase_and_does_not_touch_the_engine():
    eng = ScriptedVadEngine(runs((0.0, 3), (0.9, 8), (0.0, 20)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.reset()
    seg.activate(0)
    seg.push(index_pcm(0, 60), 0)                        # frames 3-5 are speech: confirmed at the end of frame 5
    assert seg.phase is SegmenterPhase.IN_SPEECH
    seg.reset()
    assert seg.phase is SegmenterPhase.IDLE and eng.reset_calls == 1   # reset() did not reset the engine
    seg.activate(0)
    assert eng.reset_calls == 2


def test_reactivation_count_is_per_capture():
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 100))))
    seg.activate(0)
    seg.activate(1)
    assert seg.ignored_reactivation_count == 1
    seg.reset()
    assert seg.ignored_reactivation_count == 0


# =======================================================================================
# Faults and input validation
# =======================================================================================

def test_a_bad_engine_score_surfaces_and_the_segmenter_needs_a_reset():
    eng = ScriptedVadEngine([0.0, float("nan"), 0.0])
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    with pytest.raises(VadScoreError):
        seg.push(index_pcm(0, 50), 0)
    with pytest.raises(SegmenterStateError):
        seg.push(index_pcm(50, 10), 50)
    with pytest.raises(SegmenterStateError):
        seg.activate(0)
    seg.reset()
    eng.set_scores(runs((0.0, 60)))
    seg.activate(0)
    assert seg.push(index_pcm(0, 10), 0) is None            # usable again


def test_a_rejected_block_leaves_the_segmenter_usable():
    cfg = make_config()
    scores = runs((0.0, 3), (0.9, 8), (0.0, 20))
    ref = run_plain(cfg, scores, 400)[2]
    eng = ScriptedVadEngine(scores)
    seg = UtteranceSegmenter(cfg, eng)
    seg.activate(0)
    for bad, start in [(index_pcm(0, 5).astype(np.float32), 0), (np.zeros(0, dtype=np.int16), 0), ([1, 2], 0),
                       (index_pcm(0, 5), -1), (index_pcm(0, 5), 99)]:
        with pytest.raises((SegmenterInputError, AudioContinuityError)):
            seg.push(bad, start)
    res, _ = feed(seg, 0, 400)
    assert (res.outcome, res.segment.start_sample, res.segment.end_sample) == (
        ref.outcome, ref.segment.start_sample, ref.segment.end_sample)


@pytest.mark.parametrize("bad", [
    None, [1, 2, 3], b"\x00\x01", index_pcm(0, 10).astype(np.float64), index_pcm(0, 10).astype(np.int32),
    np.zeros((5, 2), dtype=np.int16), np.zeros((2, 5, 1), dtype=np.int16), np.zeros(0, dtype=np.int16),
])
def test_push_rejects_malformed_blocks_with_clear_errors(bad):
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 50))))
    seg.activate(0)
    with pytest.raises(SegmenterInputError):
        seg.push(bad, 0)


@pytest.mark.parametrize("start", [-1, 1.5, True, "0", None])
def test_push_rejects_invalid_start_samples(start):
    seg = UtteranceSegmenter(make_config(), ScriptedVadEngine(runs((0.0, 50))))
    seg.activate(0)
    with pytest.raises(SegmenterInputError):
        seg.push(index_pcm(0, 10), start)


def test_push_accepts_the_wp1_column_layout():
    eng = ScriptedVadEngine(runs((0.0, 50)))
    seg = UtteranceSegmenter(make_config(), eng)
    seg.activate(0)
    seg.push(index_pcm(0, 10).reshape(-1, 1), 0)
    assert len(eng.seen_frames) == 1 and eng.seen_frames[0].ndim == 1


def test_result_requires_segment_and_outcome_to_agree():
    from audio.utterance import SegmenterResult
    seg = SpeechSegment(index_pcm(0, 10), SR, 0, 10, 2, 0)
    with pytest.raises(SegmenterInputError):
        SegmenterResult(SegmentOutcome.COMPLETE, None, 0, 10, 0, 0)
    with pytest.raises(SegmenterInputError):
        SegmenterResult(SegmentOutcome.TOO_SHORT, seg, 0, 10, 0, 0)
    SegmenterResult(SegmentOutcome.COMPLETE, seg, 0, 10, 0, 0)
    SegmenterResult(SegmentOutcome.NO_SPEECH_TIMEOUT, None, 0, 10, 0, 0)


def test_outcomes_that_carry_a_segment():
    with_segment = {o for o in SegmentOutcome if o.has_segment}
    assert with_segment == {SegmentOutcome.COMPLETE, SegmentOutcome.MAX_DURATION, SegmentOutcome.STREAM_ENDED}


def test_no_text_intent_or_stt_fields_exist_on_results():
    names = {f.name for f in dataclasses.fields(SpeechSegment)} | {
        f.name for f in dataclasses.fields(__import__("audio.utterance", fromlist=["SegmenterResult"]).SegmenterResult)}
    assert not names & {"text", "transcript", "intent", "stt_status", "language", "confidence"}


def test_snapshot_is_validated():
    with pytest.raises(SegmenterInputError):
        PcmSnapshot(-1, index_pcm(0, 3))
    with pytest.raises(SegmenterInputError):
        PcmSnapshot(0, index_pcm(0, 3).astype(np.float32))
    assert len(PcmSnapshot(7, np.empty(0, dtype=np.int16))) == 0


# =======================================================================================
# Post-review hardening (Week 7 Task 1 adversarial review: closes the non-equivalent surviving mutants)
# Test + documentation only: none of these changes the runtime semantics they pin.
# =======================================================================================

def test_activation_at_the_replay_start_is_accepted_and_one_below_is_rejected():
    buf = RollingPcmBuffer(200)
    buf.push(index_pcm(100, 100), 100)
    snap = buf.snapshot()                                            # replay interval [100, 200)
    assert (snap.start_sample, snap.end_sample) == (100, 200)

    eng = ScriptedVadEngine(region_scores(100, 60, [(150, 300)]))
    seg = UtteranceSegmenter(make_config(pre_roll_s=0.25), eng)
    assert seg.activate(100, snap) is True                           # activation == replay.start_sample
    assert seg.phase is SegmenterPhase.IN_SPEECH                     # speech [150,300) began inside the replay
    assert seg.activation_sample == 100 and seg.expected_next_sample == 200 and eng.reset_calls == 1
    res, _ = feed(seg, 200, 600)
    assert res.outcome is SegmentOutcome.COMPLETE
    out = res.segment
    assert (out.start_sample, out.onset_sample, out.end_sample) == (130, 150, 300)
    assert np.array_equal(out.samples, index_pcm(130, 170))

    eng2 = ScriptedVadEngine([0.0] * 20)
    seg2 = UtteranceSegmenter(make_config(pre_roll_s=0.25), eng2)
    with pytest.raises(SegmenterInputError):
        seg2.activate(99, snap)                                      # replay.start_sample - 1
    assert seg2.phase is SegmenterPhase.IDLE and eng2.reset_calls == 0


@pytest.mark.parametrize("blip_frames", [1, 2])
def test_no_state_survives_an_anchor_discard_new_speech_on_the_next_frame(blip_frames):
    """Old speech [100,200) is discarded (it ends before A=450). The discard happens on the frame that
    completes 4 silent frames (it ends at 240); speech arriving on the very next frame must start from nothing."""
    cfg = make_config(pre_roll_s=0.45)
    # a speech blip shorter than the start confirmation (30) right after the discard must NOT confirm
    blip = run_wake(cfg, speech=[(100, 200), (240, 240 + 10 * blip_frames)], activation=450, buffered_until=470,
                    buffer_capacity=500, stream_end=1200).result
    assert blip.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT and blip.segment is None
    assert blip.decided_at_sample == 450 + 200

    # a real new region right after the discard owns its onset, padding and PCM
    real = run_wake(cfg, speech=[(100, 200), (240, 600)], activation=450, buffered_until=470, buffer_capacity=500,
                    stream_end=1200).result
    seg = real.segment
    assert real.outcome is SegmentOutcome.COMPLETE
    assert seg.onset_sample == 240                                   # not the stale onset 100
    assert seg.start_sample == 220                                   # 240 - padding 20, not 100 - 20
    assert seg.end_sample == 600 and np.array_equal(seg.samples, index_pcm(220, 380))


@pytest.mark.parametrize("gap,expected_start,expected_onset", [(30, 80, 100), (40, 220, 240)])
def test_gap_equal_to_end_silence_makes_two_regions_one_shorter_makes_one(gap, expected_start, expected_onset):
    # end_silence is 40 samples: a 30-sample gap is bridged, a 40-sample gap ends the old region
    old_end = 200
    run = run_wake(make_config(pre_roll_s=0.45), speech=[(100, old_end), (old_end + gap, 600)], activation=450,
                   buffered_until=470, buffer_capacity=500, stream_end=1200)
    seg = run.result.segment
    assert seg.onset_sample == expected_onset and seg.start_sample == expected_start
    assert np.array_equal(seg.samples, index_pcm(seg.start_sample, seg.duration_samples))
    if gap == 40:
        assert seg.start_sample > old_end                            # nothing of the old region is inherited


def test_eof_with_a_partial_final_frame_pins_every_field():
    seg, eng, res = run_plain(make_config(), runs((0.0, 2), (0.9, 8)), 105)
    assert res.outcome is SegmentOutcome.STREAM_ENDED
    assert res.decided_at_sample == 105                              # everything received, not the last scored frame end
    assert res.segment.end_sample == 100                             # the segment ends at the last scored speech frame
    assert res.segment.start_sample == 0 and res.segment.onset_sample == 20
    assert np.array_equal(res.segment.samples, index_pcm(0, 100))    # the 5 tail samples are NOT in the segment
    assert res.unscored_samples == 5 == res.decided_at_sample - res.segment.end_sample
    assert len(eng.seen_frames) == 10 and all(len(f) == 10 for f in eng.seen_frames)   # no padded 11th frame
    assert eng.position == 10


def test_a_partial_final_frame_is_never_padded_into_a_confirmation():
    # 2 speech frames (20 < confirm 30) and 5 more samples: padding that tail into a speech frame would
    # confirm speech; the script has no 6th score, so any attempt to score a padded frame fails loudly
    seg, eng, res = run_plain(make_config(), runs((0.0, 3), (0.9, 2)), 55)
    assert res.outcome is SegmentOutcome.NO_SPEECH_STREAM_ENDED and res.segment is None
    assert res.decided_at_sample == 55 and res.unscored_samples == 5
    assert eng.position == 5


def test_max_duration_wins_when_padding_leaves_less_speech_than_the_minimum():
    # max = min = 50 samples, padding 20: the capped segment holds only 30 samples of speech
    cfg = make_config(max_utterance_s=0.05, min_utterance_s=0.05, onset_padding_s=0.02, pre_roll_s=0.0)
    res = run_plain(cfg, runs((0.0, 5), (0.9, 20)), 400)[2]
    seg = res.segment
    assert res.outcome is SegmentOutcome.MAX_DURATION                # not TOO_SHORT: a capped segment is not min-checked
    assert (seg.start_sample, seg.onset_sample, seg.end_sample) == (30, 50, 80)
    assert seg.duration_samples == 50 == cfg.max_utterance_samples   # the emitted PCM is bounded by max_utterance
    assert seg.end_sample - seg.onset_sample == 30 < cfg.min_utterance_samples   # speech-only extent = max - padding
    assert np.array_equal(seg.samples, index_pcm(30, 50))
    # control: the same speech without reaching the cap is TOO_SHORT
    short = run_plain(make_config(), runs((0.0, 5), (0.9, 3), (0.0, 10)), 400)[2]
    assert short.outcome is SegmentOutcome.TOO_SHORT


def test_timeout_is_decided_on_the_closing_frame_of_a_discarded_old_region():
    # end_silence (250) > timeout (200): the old region's end-silence countdown outlasts the timeout, and the
    # timer is only evaluated while LISTENING. When the countdown ends and the region is discarded, the timeout
    # is decided on THAT frame (it ends at 750), not one frame later.
    cfg = make_config(end_silence_s=0.25, no_speech_timeout_s=0.2, pre_roll_s=0.2)
    run = run_wake(cfg, speech=[(400, 500)], activation=500, buffered_until=510, buffer_capacity=300, stream_end=1500)
    assert run.result.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT and run.result.segment is None
    assert run.result.decided_at_sample == 750


def test_decisions_happen_at_whole_frame_boundaries():
    """Durations become sample counts, but every VAD decision is taken when a whole 10-sample frame ends, so an
    entered duration is effectively rounded up to the next frame boundary (frame granularity, not an error)."""
    cfg = make_config(start_confirmation_s=0.025, end_silence_s=0.035, no_speech_timeout_s=0.205, min_utterance_s=0.045)
    assert (cfg.start_confirmation_samples, cfg.end_silence_samples, cfg.no_speech_timeout_samples,
            cfg.min_utterance_samples) == (25, 35, 205, 45)

    # confirmation: 25 samples are required, the decision comes after 30 (3 frames)
    seg = UtteranceSegmenter(cfg, ScriptedVadEngine(runs((0.9, 10))))
    seg.activate(0)
    seg.push(index_pcm(0, 20), 0)
    assert seg.phase is SegmenterPhase.LISTENING
    seg.push(index_pcm(20, 10), 20)
    assert seg.phase is SegmenterPhase.IN_SPEECH

    # end silence: 35 samples are required, the decision comes after 40 (4 frames)
    seg = UtteranceSegmenter(cfg, ScriptedVadEngine(runs((0.9, 6), (0.0, 10))))
    seg.activate(0)
    assert seg.push(index_pcm(0, 90), 0) is None                     # 3 silent frames = 30 < 35
    assert seg.phase is SegmenterPhase.IN_SPEECH
    res = seg.push(index_pcm(90, 10), 90)                            # the 4th silent frame
    assert res.outcome is SegmentOutcome.COMPLETE and res.decided_at_sample == 100 and res.segment.end_sample == 60

    # no-speech timeout: 205 samples, decided when the frame ending at 210 completes
    res = run_plain(cfg, runs((0.0, 40)), 400)[2]
    assert res.outcome is SegmentOutcome.NO_SPEECH_TIMEOUT and res.decided_at_sample == 210

    # minimum: 45 samples, speech is measured in whole frames (40 fails, 50 passes)
    assert run_plain(cfg, runs((0.9, 4), (0.0, 10)), 400)[2].outcome is SegmentOutcome.TOO_SHORT
    assert run_plain(cfg, runs((0.9, 5), (0.0, 10)), 400)[2].outcome is SegmentOutcome.COMPLETE
