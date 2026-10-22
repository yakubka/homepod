"""Week 7 Task 5: the pure metric arithmetic (evaluation/week7_voice_metrics.py).

Exact examples pin every definition. Nothing here is an accuracy result.
"""

import math

import pytest

from evaluation import week7_voice_metrics as vm
from evaluation.week7_voice_metrics import ErrorCount


# -- normalisation --------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Turn ON the Lights.", "turn on the lights"),
    ("  hey,  jarvis!  ", "hey jarvis"),
    ("What's the temperature?", "what's the temperature"),
    ("'quoted' words", "quoted words"),
    ("it’s fine", "it's fine"),
    ("turn-on the lights", "turn on the lights"),
    ("set it to 50 percent", "set it to 50 percent"),
    ("under_score", "under score"),
    ("", ""),
    ("!!!", ""),
    ("A B", "a b"),
    ("ＴＵＲＮ", "turn"),               # fullwidth Latin letters fold to ASCII (NFKC)
])
def test_normalisation_is_fixed_and_documented(raw, expected):
    assert vm.normalize_text(raw) == expected


def test_normalisation_never_removes_words():
    text = "um the uh lights hey jarvis please"
    assert vm.normalize_text(text) == text               # no stop words, no fillers, no wake phrase removal


def test_normalisation_id_is_versioned():
    assert vm.NORMALIZATION_ID == "week7-text-norm-v1"


# -- WER arithmetic -------------------------------------------------------------------------------

def test_exact_match_is_zero():
    c = vm.word_errors("turn on the lights", "Turn on the lights.")
    assert (c.substitutions, c.deletions, c.insertions, c.errors, c.rate) == (0, 0, 0, 0, 0.0)


def test_one_substitution():
    c = vm.word_errors("turn on the lights", "turn off the lights")
    assert (c.substitutions, c.deletions, c.insertions) == (1, 0, 0) and c.rate == 0.25


def test_one_deletion():
    c = vm.word_errors("turn on the lights", "turn on lights")
    assert (c.substitutions, c.deletions, c.insertions) == (0, 1, 0) and c.rate == 0.25


def test_one_insertion():
    c = vm.word_errors("turn on the lights", "turn on the big lights")
    assert (c.substitutions, c.deletions, c.insertions) == (0, 0, 1) and c.rate == 0.25


def test_multiple_errors_and_a_rate_above_one():
    c = vm.word_errors("a b c", "x y z q r")           # 3 substitutions + 2 insertions
    assert (c.substitutions, c.deletions, c.insertions, c.errors) == (3, 0, 2, 5)
    assert c.rate == pytest.approx(5 / 3) and c.rate > 1.0


def test_mixed_errors():
    c = vm.word_errors("set the lights to fifty", "set lights to forty percent")
    assert (c.substitutions, c.deletions, c.insertions) == (1, 1, 1) and c.errors == 3 and c.rate == pytest.approx(3 / 5)


def test_empty_hypothesis_is_all_deletions_never_perfect():
    c = vm.word_errors("turn on the lights", "")
    assert (c.substitutions, c.deletions, c.insertions) == (0, 4, 0) and c.rate == 1.0
    assert vm.word_errors("turn on the lights", "!!! ...").rate == 1.0       # punctuation-only is empty after normalisation


def test_empty_reference_is_undefined_not_zero():
    c = vm.word_errors("", "turn on")
    assert c.rate is None and c.insertions == 2 and c.reference_length == 0
    assert vm.word_errors("", "").rate is None


def test_tie_break_is_deterministic():
    first = vm.edit_counts("a b".split(), "b a".split())
    assert first == vm.edit_counts("a b".split(), "b a".split()) and sum(first) == 2


def test_cer_counts_characters_including_spaces():
    c = vm.char_errors("lights", "light")
    assert (c.substitutions, c.deletions, c.insertions, c.reference_length) == (0, 1, 0, 6) and c.rate == pytest.approx(1 / 6)
    assert vm.char_errors("on off", "on of").errors == 1
    assert vm.char_errors("", "abc").rate is None and vm.char_errors("abc", "").rate == 1.0


def test_error_count_roundtrip_and_validation_of_fields():
    c = vm.word_errors("a b c", "a x c d")
    assert ErrorCount.from_dict(c.as_dict()) == c
    assert c.as_dict()["errors"] == c.errors == 2


# -- aggregation ----------------------------------------------------------------------------------

def test_aggregate_is_micro_averaged_not_a_mean_of_rates():
    a = vm.word_errors("a b c d", "a b c d")             # 0 / 4
    b = vm.word_errors("a", "x")                         # 1 / 1
    agg = vm.aggregate_errors([a, b])
    assert agg["errors"] == 1 and agg["reference_length"] == 5 and agg["rate"] == pytest.approx(0.2)   # a mean of rates would be 0.5
    assert agg["items"] == 2 and agg["substitutions"] == 1


def test_aggregate_counts_but_does_not_score_empty_references():
    agg = vm.aggregate_errors([vm.word_errors("a b", "a x"), vm.word_errors("", "noise words"), None])
    assert agg["items"] == 1 and agg["items_with_undefined_rate"] == 1 and agg["rate"] == 0.5
    empty = vm.aggregate_errors([])
    assert empty["rate"] is None and empty["items"] == 0 and vm.aggregate_errors([vm.word_errors("", "x")])["rate"] is None


# -- wake-prefix classification -------------------------------------------------------------------

@pytest.mark.parametrize("phrase,raw,removed,expected", [
    ("hey jarvis", "Hey Jarvis turn on the lights", True, "CORRECTLY_REMOVED"),
    ("hey jarvis", "hey jarvis turn on the lights", False, "RETAINED_SHOULD_STRIP"),
    ("hey jarvis", "hey travis turn on the lights", False, "PHRASE_NOT_TRANSCRIBED"),
    ("hey jarvis", "turn on the lights", False, "PHRASE_NOT_TRANSCRIBED"),
    ("hey jarvis", "okay vesta lights", True, "REMOVED_UNEXPECTEDLY"),
    ("", "turn on the lights", False, "NOT_APPLICABLE"),
    ("", "hey jarvis lights", True, "REMOVED_UNEXPECTEDLY"),
    ("hey jarvis", None, False, "NOT_APPLICABLE"),
    ("hey jarvis", "tell jarvis to stop", False, "PHRASE_NOT_TRANSCRIBED"),
])
def test_wake_prefix_outcomes(phrase, raw, removed, expected):
    assert vm.classify_wake_prefix(phrase, raw, removed) == expected
    assert expected in vm.WAKE_PREFIX_OUTCOMES


def test_extra_words_removed_catches_an_internal_wake_word_stripped():
    assert vm.extra_words_removed("hey jarvis tell jarvis to stop", "tell to stop", True, 2) == 1
    assert vm.extra_words_removed("hey jarvis tell jarvis to stop", "tell jarvis to stop", True, 2) == 0
    assert vm.extra_words_removed("tell jarvis to stop", "tell to stop", False, 2) == 1
    assert vm.extra_words_removed("turn on", "turn on", False, 2) == 0
    assert vm.extra_words_removed(None, "turn on", False, 2) == 0
    assert vm.extra_words_removed("a b", "a b c d", True, 2) == 0           # text can not "grow" into a negative count


# -- segmentation geometry ------------------------------------------------------------------------

def test_segment_geometry_signed_offsets_and_coverage():
    g = vm.segment_geometry(15000, 41000, 16000, 40000, 16000)
    assert g["start_offset_samples"] == -1000 and g["end_offset_samples"] == 1000
    assert g["start_offset_ms"] == pytest.approx(-62.5) and g["end_offset_ms"] == pytest.approx(62.5)
    assert g["speech_covered_fraction"] == 1.0 and g["segment_beyond_speech_samples"] == 2000
    clipped = vm.segment_geometry(20000, 30000, 16000, 40000, 16000)
    assert clipped["speech_covered_fraction"] == pytest.approx(10000 / 24000) and clipped["segment_beyond_speech_samples"] == 0
    assert vm.segment_geometry(0, 100, 5000, 6000, 16000)["speech_covered_fraction"] == 0.0


# -- rates and distributions ----------------------------------------------------------------------

def test_rate_is_none_for_an_empty_denominator():
    assert vm.rate(0, 0) is None and vm.rate(1, 4) == 0.25 and vm.rate(0, 5) == 0.0


def test_distribution_uses_nearest_rank_and_ignores_missing_values():
    d = vm.distribution([10.0, 20.0, 30.0, 40.0, None, float("nan"), 50.0])
    assert d == {"n": 5, "min": 10.0, "mean": 30.0, "median": 30.0, "p95": 50.0, "max": 50.0}
    one = vm.distribution([7.0])
    assert one["median"] == one["p95"] == one["max"] == 7.0
    assert vm.distribution([]) == {"n": 0, "min": None, "mean": None, "median": None, "p95": None, "max": None}
    assert math.isclose(vm.percentile([1.0, 2.0, 3.0, 4.0], 50), 2.0)
