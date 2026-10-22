"""Pure metric functions for the Week 7 Task 5 voice evaluation (VESTA).

No audio, no models, no I/O: only arithmetic on text and numbers, so every definition can be pinned by exact
examples (tests/test_week7_voice_metrics.py). Nothing here is an accuracy RESULT; it is the arithmetic a real
recorded corpus will be run through.

TEXT NORMALISATION (id NORMALIZATION_ID, fixed; changing it changes every WER and must bump the id)
  1. Unicode NFKC, then lower-case.
  2. The typographic apostrophe (U+2019) becomes an ASCII apostrophe.
  3. Every character that is not a letter, a digit, a space or an apostrophe becomes a space (so punctuation and
     hyphens separate words: "turn-on" -> "turn on").
  4. An apostrophe survives only BETWEEN two letters/digits ("what's" stays, "'lights'" -> "lights").
  5. Runs of whitespace collapse to one space; the ends are stripped.
  Digits are kept as written (no number spelling) and NO word is ever removed (no stop words, no fillers, no wake
  phrase). The wake phrase is handled by the pipeline's own prefix rule and by the wake-prefix metrics, never here.

WER / CER. WER = (S + D + I) / reference words on normalised word lists; CER the same on the normalised string
(spaces count as characters). Alignment is a standard Levenshtein DP; ties are broken diagonal (match / substitution),
then deletion, then insertion, so S / D / I are deterministic (the TOTAL is unaffected by the tie rule).
  * empty reference: the rate is UNDEFINED (None), never 0 and never infinity; the hypothesis words are still counted
    as insertions in the item evidence;
  * empty hypothesis with a non-empty reference: every reference word is a deletion, rate 1.0;
  * aggregates are MICRO averages (total errors / total reference words), not means of per-item rates.
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from speech.pipeline import strip_wake_prefix

NORMALIZATION_ID = "week7-text-norm-v1"

_NON_WORD = re.compile(r"[^\w\s']|_", re.UNICODE)
_APOSTROPHE_EDGE = re.compile(r"(?<![^\W_])'|'(?![^\W_])", re.UNICODE)


def normalize_text(text: str) -> str:
    """Fixed evaluation normalisation (see the module docstring)."""
    t = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    t = _NON_WORD.sub(" ", t)
    t = _APOSTROPHE_EDGE.sub(" ", t)
    return " ".join(t.split())


# ---------------------------------------------------------------------------------------
# error counts
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorCount:
    substitutions: int
    deletions: int
    insertions: int
    reference_length: int          # words (WER) or characters (CER) in the normalised reference
    hypothesis_length: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def rate(self) -> Optional[float]:
        """None when the reference is empty: a rate over zero reference units is undefined."""
        return None if self.reference_length == 0 else self.errors / self.reference_length

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "ErrorCount":
        return cls(int(d["substitutions"]), int(d["deletions"]), int(d["insertions"]), int(d["reference_length"]), int(d["hypothesis_length"]))

    def as_dict(self) -> Dict[str, object]:
        return {
            "substitutions": self.substitutions, "deletions": self.deletions, "insertions": self.insertions,
            "errors": self.errors, "reference_length": self.reference_length,
            "hypothesis_length": self.hypothesis_length, "rate": self.rate,
        }


def edit_counts(reference: Sequence, hypothesis: Sequence) -> Tuple[int, int, int]:
    """(substitutions, deletions, insertions) of a minimum-edit alignment of hypothesis against reference."""
    n, m = len(reference), len(hypothesis)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diagonal = cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1])
            cost[i][j] = min(diagonal, cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    subs = dels = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            subs += reference[i - 1] != hypothesis[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return int(subs), dels, ins


def word_errors(reference: str, hypothesis: str) -> ErrorCount:
    ref, hyp = normalize_text(reference).split(), normalize_text(hypothesis).split()
    s, d, i = edit_counts(ref, hyp)
    return ErrorCount(s, d, i, len(ref), len(hyp))


def char_errors(reference: str, hypothesis: str) -> ErrorCount:
    ref, hyp = normalize_text(reference), normalize_text(hypothesis)
    s, d, i = edit_counts(ref, hyp)
    return ErrorCount(s, d, i, len(ref), len(hyp))


def aggregate_errors(counts: Iterable[Optional[ErrorCount]]) -> Dict[str, object]:
    """Micro-average over the items that HAVE a defined error count. Items with an empty reference are counted, not scored."""
    items = [c for c in counts if c is not None]
    scored = [c for c in items if c.reference_length > 0]
    undefined = [c for c in items if c.reference_length == 0]
    reference = sum(c.reference_length for c in scored)
    errors = sum(c.errors for c in scored)
    return {
        "items": len(scored),
        "substitutions": sum(c.substitutions for c in scored),
        "deletions": sum(c.deletions for c in scored),
        "insertions": sum(c.insertions for c in scored),
        "errors": errors,
        "reference_length": reference,
        "rate": None if reference == 0 else errors / reference,
        "items_with_undefined_rate": len(undefined),
    }


# ---------------------------------------------------------------------------------------
# wake-prefix metrics (separate from WER)
# ---------------------------------------------------------------------------------------

WAKE_PREFIX_OUTCOMES = (
    "NOT_APPLICABLE",          # STT did not run, or no wake phrase was expected and none was removed
    "CORRECTLY_REMOVED",       # the expected phrase leads the decoded text and the pipeline removed it
    "RETAINED_SHOULD_STRIP",   # the expected phrase leads the decoded text but the pipeline kept it
    "PHRASE_NOT_TRANSCRIBED",  # a phrase was expected but the decoded text does not start with it (an STT / segmentation matter)
    "REMOVED_UNEXPECTEDLY",    # the pipeline removed a phrase that the manifest did not expect at the start
)


def classify_wake_prefix(expected_phrase: str, raw_text: Optional[str], prefix_removed: bool) -> str:
    """Compare what the pipeline did with the decoded text against what the manifest says was spoken.

    raw_text is the decoded text BEFORE the pipeline's prefix rule (None when STT did not run). The independent
    "should it have been removed" test uses the same whole-word leading-edge rule as the pipeline, so this metric
    detects configuration mistakes and mis-transcribed phrases; the rule itself is pinned by its own unit tests.
    """
    if raw_text is None:
        return "NOT_APPLICABLE"
    if not expected_phrase:
        return "REMOVED_UNEXPECTEDLY" if prefix_removed else "NOT_APPLICABLE"
    _, should_strip = strip_wake_prefix(raw_text, [expected_phrase])
    if should_strip:
        return "CORRECTLY_REMOVED" if prefix_removed else "RETAINED_SHOULD_STRIP"
    return "REMOVED_UNEXPECTEDLY" if prefix_removed else "PHRASE_NOT_TRANSCRIBED"


def extra_words_removed(raw_text: Optional[str], pipeline_text: str, prefix_removed: bool, phrase_words: int) -> int:
    """Words the pipeline removed BEYOND the leading wake phrase (an internal "jarvis" stripped by mistake counts here).

    0 when nothing extra was removed. Computed on normalised word counts: raw - output - (phrase length if the
    pipeline reports it removed one). Negative values (text grew) are reported as 0.
    """
    if raw_text is None:
        return 0
    raw, out = len(normalize_text(raw_text).split()), len(normalize_text(pipeline_text).split())
    return max(0, raw - out - (phrase_words if prefix_removed else 0))


# ---------------------------------------------------------------------------------------
# segmentation geometry (annotation-based, NOT frame-level VAD accuracy)
# ---------------------------------------------------------------------------------------

def segment_geometry(segment_start: int, segment_end: int, speech_start: int, speech_end: int, sample_rate: int) -> Dict[str, object]:
    """Compare a captured segment with an annotated speech region (end exclusive). Signed offsets: segment - speech."""
    overlap = max(0, min(segment_end, speech_end) - max(segment_start, speech_start))
    speech_len = speech_end - speech_start
    return {
        "start_offset_samples": segment_start - speech_start,
        "end_offset_samples": segment_end - speech_end,
        "start_offset_ms": (segment_start - speech_start) * 1000.0 / sample_rate,
        "end_offset_ms": (segment_end - speech_end) * 1000.0 / sample_rate,
        "speech_covered_fraction": overlap / speech_len if speech_len > 0 else None,
        "segment_beyond_speech_samples": max(0, speech_start - segment_start) + max(0, segment_end - speech_end),
    }


# ---------------------------------------------------------------------------------------
# rates and distributions
# ---------------------------------------------------------------------------------------

def rate(numerator: int, denominator: int) -> Optional[float]:
    """numerator / denominator, or None when the denominator is 0 (never a fake 0 or 1)."""
    return None if denominator == 0 else numerator / denominator


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in (0, 100]) of an ascending list."""
    k = max(1, math.ceil(q / 100.0 * len(sorted_values)))
    return float(sorted_values[k - 1])


def distribution(values: Iterable[Optional[float]]) -> Dict[str, object]:
    """n, min, mean, median, p95 (nearest rank) and max of the finite values; all None when there are none."""
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(v))
    if not vals:
        return {"n": 0, "min": None, "mean": None, "median": None, "p95": None, "max": None}
    return {
        "n": len(vals), "min": vals[0], "mean": sum(vals) / len(vals),
        "median": percentile(vals, 50), "p95": percentile(vals, 95), "max": vals[-1],
    }
