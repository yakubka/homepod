"""Week 7 Task 5: the evaluation HARNESS (evaluation/week7_voice_eval.py).

SOFTWARE-VALIDATED with synthetic audio and fake VAD / STT. These tests prove schema, metric wiring, consent and location
rules, split enforcement, privacy and error handling. They do NOT measure WER, VAD accuracy, wake accuracy or anything about a
real recording; the 'real' entries below only DECLARE a real origin to exercise the stricter rules, and their audio is synthetic.
"""

import dataclasses
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from evaluation import week7_voice_eval as ev
from evaluation.ingest import REPO_ROOT
from speech.stt import SttInferenceFailed
from tests.week7_eval_support import (
    CHUNK, SR, FakeClock, FiresAt, clip, make_components, manifest_dict, parse, real_rec, synth_rec, write_wav,
)

TEMPLATE = REPO_ROOT / "evaluation" / "week7_corpus_manifest.template.json"


_RESIDUE = (REPO_ROOT / "tests" / "_should_never_be_written", REPO_ROOT / "tests" / "_never", REPO_ROOT / "tests" / "_cli_never",
            REPO_ROOT / "tests" / "_sweep_never", REPO_ROOT / "evaluation" / "_nope")


@pytest.fixture(autouse=True)
def _no_repo_residue():
    """If a refusal ever regresses, the test fails AND the directory it wrongly created inside the repository is removed."""
    yield
    for path in _RESIDUE:
        shutil.rmtree(path, ignore_errors=True)


class _Logs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _Logs()
    ev.logger.addHandler(handler)
    yield handler.lines
    ev.logger.removeHandler(handler)


@pytest.fixture
def wav(tmp_path):
    """Write a synthetic clip into tmp_path and return its path."""
    def make(name="a.wav", **kw):
        return write_wav(tmp_path / name, clip(**kw))
    return make


def run(tmp_path, recordings, replies=("hey jarvis turn on the lights",), wake_mode="annotated", splits=("DEV",), allow_heldout=False, **kw):
    manifest = parse(recordings, tmp_path)
    comps = make_components(replies, **kw)
    items, report = ev.evaluate_manifest(manifest, comps, wake_mode=wake_mode, splits=splits, allow_heldout=allow_heldout)
    return items, report, comps


# =========================================================================================
# A. manifest contract, consent, locations
# =========================================================================================

def test_a_valid_synthetic_manifest_parses(tmp_path, wav):
    m = parse([synth_rec("SYN-1", wav())], tmp_path)
    (r,) = m.recordings
    assert r.is_synthetic and r.split == "DEV" and r.category == "WAKE_IMMEDIATE_COMMAND" and not m.has_real_recordings


def test_a_real_recording_with_explicit_consent_parses_when_the_audio_is_outside_the_repository(tmp_path, wav):
    m = parse([real_rec("REAL-1", wav())], tmp_path)
    assert m.has_real_recordings and m.recordings[0].consent_reference == "CONSENT-S01"


@pytest.mark.parametrize("consent", ["UNKNOWN", "unknown", "NOT_APPLICABLE", "", "   ", None])
def test_a_real_recording_without_an_explicit_consent_reference_is_rejected(tmp_path, wav, consent):
    with pytest.raises(ev.EvalInputError, match="consent_reference"):
        parse([real_rec("REAL-1", wav(), consent_reference=consent)], tmp_path)


def test_an_unknown_origin_is_treated_as_possibly_human_and_needs_consent(tmp_path, wav):
    with pytest.raises(ev.EvalInputError, match="consent_reference"):
        parse([real_rec("U-1", wav(), recording_origin="UNKNOWN", consent_reference="UNKNOWN")], tmp_path)
    parse([real_rec("U-2", wav(), recording_origin="UNKNOWN", consent_reference="CONSENT-S01")], tmp_path)


def test_synthetic_audio_must_say_not_applicable_and_no_other_value_is_accepted(tmp_path, wav):
    with pytest.raises(ev.EvalInputError, match="must be \"NOT_APPLICABLE\" for recording_origin SYNTHETIC"):
        parse([synth_rec("SYN-1", wav(), consent_reference="CONSENT-S01")], tmp_path)
    with pytest.raises(ev.EvalInputError, match="speaker_code must be \"NOT_APPLICABLE\""):
        parse([synth_rec("SYN-1", wav(), speaker_code="S01")], tmp_path)


@pytest.mark.parametrize("speaker", ["Alice Smith", "alice", "S1", "S001", "", None])
def test_a_speaker_code_must_be_a_code_never_a_name(tmp_path, wav, speaker):
    with pytest.raises(ev.EvalInputError, match="speaker_code"):
        parse([real_rec("REAL-1", wav(), speaker_code=speaker)], tmp_path)


def test_a_real_recording_inside_the_repository_is_rejected_but_synthetic_audio_may_live_anywhere():
    inside = REPO_ROOT / "tests" / "not_a_real_file.wav"
    with pytest.raises(ev.EvalInputError, match="inside the source repository"):
        ev.parse_manifest(manifest_dict([real_rec("REAL-1", inside)]), REPO_ROOT)
    with pytest.raises(ev.EvalInputError, match="inside the source repository"):
        ev.parse_manifest(manifest_dict([real_rec("REAL-2", "relative/clip.wav")]), REPO_ROOT)      # relative to a repo manifest dir
    ev.parse_manifest(manifest_dict([synth_rec("SYN-1", inside)]), REPO_ROOT)


@pytest.mark.parametrize("change,message", [
    (dict(split="TRAIN"), "split must be one of"),
    (dict(split="dev"), "split must be one of"),
    (dict(category="LOUD_MUSIC"), "category must be one of"),
    (dict(recording_origin="WIZARD"), "recording_origin must be one of"),
    (dict(recording_id="bad id!"), "recording_id must be"),
    (dict(recording_id="TEMPLATE-1"), "starts with TEMPLATE"),
    (dict(wake_end_sample=-1), "non-negative integer"),
    (dict(wake_end_sample=True), "non-negative integer"),
    (dict(speech_start_sample=100), "given together"),
    (dict(speech_start_sample=500, speech_end_sample=500), "greater than"),
    (dict(human_review="GOOD"), "human_review must be one of"),
    (dict(audio_sha256="abc"), "audio_sha256"),
    (dict(conversion={"description": "x"}), "conversion must have exactly"),
    (dict(unexpected_field=1), "unknown field"),
    (dict(microphone_or_device=""), "microphone_or_device"),
    (dict(notes=5), "notes must be a string"),
])
def test_invalid_entries_are_rejected_loudly(tmp_path, wav, change, message):
    with pytest.raises(ev.EvalInputError, match=message):
        parse([real_rec("REAL-1", wav(), **change)], tmp_path)


def test_category_rules(tmp_path, wav):
    for category in ev.NON_SPEECH_CATEGORIES:
        with pytest.raises(ev.EvalInputError, match="reference_command_text must be empty"):
            parse([synth_rec("C-1", wav(), category, reference_command_text="turn on")], tmp_path)
        with pytest.raises(ev.EvalInputError, match="expected_wake_phrase must be empty"):
            parse([synth_rec("C-1", wav(), category, expected_wake_phrase="hey jarvis")], tmp_path)
    for category in ev.WAKE_REQUIRED_CATEGORIES:
        with pytest.raises(ev.EvalInputError, match="expected_wake_phrase is required"):
            parse([synth_rec("W-1", wav(), category, expected_wake_phrase="")], tmp_path)
    with pytest.raises(ev.EvalInputError, match="no wake phrase"):
        parse([synth_rec("N-1", wav(), "SPEECH_NO_WAKE", expected_wake_phrase="hey jarvis")], tmp_path)


def test_every_problem_is_listed_and_a_duplicate_id_is_an_error(tmp_path, wav):
    with pytest.raises(ev.EvalInputError) as err:
        parse([synth_rec("DUP", wav()), synth_rec("DUP", wav()), real_rec("REAL-2", wav(), split="TRAIN", consent_reference="UNKNOWN")], tmp_path)
    text = str(err.value)
    assert "DUP: recording_id appears 2 times" in text and "split must be one of" in text and "consent_reference" in text


def test_missing_fields_and_top_level_errors(tmp_path, wav):
    rec = synth_rec("SYN-1", wav())
    del rec["noise_condition"]
    with pytest.raises(ev.EvalInputError, match="missing required field"):
        parse([rec], tmp_path)
    for data, message in (
        ([], "must be a JSON object"),
        ({"schema_version": "v0", "corpus_id": "x", "recordings": [synth_rec("S", wav())]}, "schema_version"),
        ({"schema_version": ev.SCHEMA_MANIFEST, "recordings": [synth_rec("S", wav())]}, "corpus_id"),
        ({"schema_version": ev.SCHEMA_MANIFEST, "corpus_id": "x", "recordings": []}, "non-empty list"),
        ({"schema_version": ev.SCHEMA_MANIFEST, "corpus_id": "x", "recordings": [synth_rec("S", wav())], "extra": 1}, "unknown top-level"),
    ):
        with pytest.raises(ev.EvalInputError, match=message):
            ev.parse_manifest(data, tmp_path)


def test_the_repository_template_is_clearly_a_template_and_has_no_recordings():
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    banner = data["banner"]
    assert data["template"] is True and "TEMPLATE" in banner and "NO REAL RECORDINGS INCLUDED" in banner and "NO WEEK 7 ACCURACY RESULT YET" in banner
    assert data["schema_version"] == ev.SCHEMA_MANIFEST
    assert {r["category"] for r in data["recordings"]} == set(ev.CATEGORIES)          # every future category is represented
    assert all(r["recording_id"].startswith("TEMPLATE-") for r in data["recordings"])
    assert not list(TEMPLATE.parent.glob("*.wav"))                                      # no audio ships with the template
    with pytest.raises(ev.EvalInputError, match="TEMPLATE"):
        ev.load_manifest(TEMPLATE)                                                      # a template can never be evaluated
    m = ev.load_manifest(TEMPLATE, allow_template=True)                                 # but its structure stays valid
    assert m.template and len(m.recordings) == 7


def test_corpus_warnings_report_speakers_in_both_splits_and_missing_categories(tmp_path, wav):
    m = parse([real_rec("A", wav(), split="DEV"), real_rec("B", wav(), split="HELDOUT")], tmp_path)
    warnings = ev.corpus_warnings(m.recordings)
    assert any(w.startswith("SPEAKER_IN_BOTH_SPLITS: S01") for w in warnings)
    assert "NO_REAL_RECORDINGS_FOR_CATEGORY_SILENCE_CONTROL" in warnings


# =========================================================================================
# B. running recordings: statuses, WER, wake prefix
# =========================================================================================

def test_a_perfect_transcript_has_zero_wer_and_the_prefix_is_removed(tmp_path, wav):
    items, report, comps = run(tmp_path, [synth_rec("SYN-1", wav())])
    (item,) = items
    assert item["item_status"] == "EVALUATED" and item["pipeline_status"] == "TRANSCRIBED"
    assert item["wer"]["rate"] == 0.0 and item["cer"]["rate"] == 0.0 and item["wer"]["reference_length"] == 4
    assert item["pipeline_text"] == "turn on the lights" and item["decoded_text_raw"] == "hey jarvis turn on the lights"
    assert item["wake_prefix"] == {"outcome": "CORRECTLY_REMOVED", "prefix_removed": True, "extra_words_removed": 0}
    assert item["wake_detected"] and item["activation_sample"] == 16 * CHUNK == 20480       # first 80 ms chunk ending at or after wake_end_sample 20000
    ev.validate_item(item)


def test_wake_end_annotation_drives_the_simulated_wake_chunk(tmp_path, wav):
    items, _, _ = run(tmp_path, [synth_rec("SYN-1", wav(), wake_end_sample=12000)])
    assert items[0]["activation_sample"] == 10 * CHUNK and items[0]["activation_kind"] == "annotated"      # first chunk ending >= 12000: 12800
    start_items, _, _ = run(tmp_path, [synth_rec("SYN-1", wav())], wake_mode="start")
    assert start_items[0]["activation_sample"] == CHUNK and start_items[0]["activation_kind"] == "start"


def test_a_substitution_deletion_and_insertion_are_counted_per_item(tmp_path, wav):
    recs = [synth_rec("SUB", wav("a.wav")), synth_rec("DEL", wav("b.wav")), synth_rec("INS", wav("c.wav"))]
    replies = ["hey jarvis turn off the lights", "hey jarvis turn on lights", "hey jarvis turn on the big lights"]
    items, report, _ = run(tmp_path, recs, replies=replies)
    sub, dele, ins = (i["wer"] for i in items)
    assert (sub["substitutions"], sub["deletions"], sub["insertions"]) == (1, 0, 0)
    assert (dele["substitutions"], dele["deletions"], dele["insertions"]) == (0, 1, 0)
    assert (ins["substitutions"], ins["deletions"], ins["insertions"]) == (0, 0, 1)
    wer = ev.aggregate_items(items)["wer"]["decoded"]
    assert wer["errors"] == 3 and wer["reference_length"] == 12 and wer["rate"] == pytest.approx(0.25)


def test_an_empty_transcript_counts_as_a_full_deletion_in_the_decoded_wer(tmp_path, wav):
    items, _, _ = run(tmp_path, [synth_rec("EMP", wav())], replies=[""])
    (item,) = items
    assert item["pipeline_status"] == "STT_EMPTY" and item["raw_stt_status"] == "EMPTY_TEXT"
    assert item["wer"]["rate"] == 1.0 and item["wer"]["deletions"] == 4 and item["wake_prefix"]["outcome"] == "PHRASE_NOT_TRANSCRIBED"


def test_wake_only_is_a_decoded_item_with_an_empty_hypothesis(tmp_path, wav):
    items, report, _ = run(tmp_path, [synth_rec("WO", wav())], replies=["Hey Jarvis."])
    (item,) = items
    assert item["pipeline_status"] == "WAKE_ONLY" and item["wer"]["rate"] == 1.0 and item["wake_prefix"]["outcome"] == "CORRECTLY_REMOVED"
    agg = ev.aggregate_items(items)
    assert agg["wake_prefix"]["wake_only"] == {"count": 1, "with_a_reference_command": 1}
    assert agg["command_metrics"]["wake_only_rate"] == 1.0


def test_a_wake_prefix_left_in_the_text_is_counted_separately_from_wer(tmp_path, wav):
    manifest = parse([synth_rec("KEPT", wav(), expected_wake_phrase="hey jarvis")], tmp_path)
    comps = make_components(["hey jarvis turn on the lights"], wake_phrases=("okay vesta",))      # pipeline configured with the WRONG phrase
    (item,), report = ev.evaluate_manifest(manifest, comps)
    assert item["wake_prefix"]["outcome"] == "RETAINED_SHOULD_STRIP" and item["wake_prefix"]["prefix_removed"] is False
    assert item["wer"]["insertions"] == 2                                  # the retained words also cost WER ...
    assert ev.aggregate_items([item])["wake_prefix"]["outcomes"]["RETAINED_SHOULD_STRIP"] == 1      # ... and are counted on their own


def test_a_phrase_the_stt_never_transcribed_is_not_a_prefix_removal_error(tmp_path, wav):
    items, _, _ = run(tmp_path, [synth_rec("MISS", wav())], replies=["hey travis turn on the lights"])
    assert items[0]["wake_prefix"]["outcome"] == "PHRASE_NOT_TRANSCRIBED" and items[0]["wer"]["insertions"] == 2


def test_an_internal_wake_word_that_survives_is_not_flagged(tmp_path, wav):
    rec = synth_rec("INT", wav(), reference_command_text="tell jarvis to stop")
    items, _, _ = run(tmp_path, [rec], replies=["hey jarvis tell jarvis to stop"])
    assert items[0]["wake_prefix"]["extra_words_removed"] == 0 and items[0]["wer"]["rate"] == 0.0


def test_commands_without_a_reference_have_no_wer_but_are_still_counted(tmp_path, wav):
    items, report, _ = run(tmp_path, [synth_rec("NOREF", wav(), reference_command_text="")])
    assert items[0]["reference_available"] is False and items[0]["wer"] is None and items[0]["pipeline_status"] == "TRANSCRIBED"
    agg = ev.aggregate_items(items)
    assert agg["wer"]["decoded"]["rate"] is None and agg["command_metrics"]["command_capture_rate"] == 1.0


def test_a_failed_pipeline_is_not_in_the_decoded_wer_denominator_but_is_in_the_end_to_end_one(tmp_path, wav):
    recs = [synth_rec("OK", wav("a.wav")), synth_rec("FAIL", wav("b.wav"))]
    items, _, _ = run(tmp_path, recs, replies=["hey jarvis turn on the lights", SttInferenceFailed("boom words")])
    ok, fail = items
    assert fail["pipeline_status"] == "STT_FAILED" and fail["error_class"] == "SttInferenceFailed" and fail["wer"] is None
    assert fail["wer_end_to_end"]["rate"] == 1.0 and fail["wer_end_to_end"]["deletions"] == 4
    agg = ev.aggregate_items(items)
    assert agg["wer"]["decoded"]["reference_length"] == 4 and agg["wer"]["decoded"]["rate"] == 0.0
    assert agg["wer"]["end_to_end"]["reference_length"] == 8 and agg["wer"]["end_to_end"]["rate"] == 0.5


def test_no_speech_and_too_short_are_capture_failures_not_stt_samples(tmp_path, wav):
    silent = write_wav(tmp_path / "silent.wav", clip(speech=()))
    burst = write_wav(tmp_path / "burst.wav", clip(speech=((1.5, 1.6),)))                  # 100 ms of speech AFTER the wake (20480): shorter than the 0.2 s minimum
    items, _, comps = run(tmp_path, [synth_rec("SIL", silent), synth_rec("BURST", burst)])
    assert [i["pipeline_status"] for i in items] == ["NO_SPEECH", "TOO_SHORT"] and comps.stt.calls == []
    assert all(i["wer"] is None and i["wer_end_to_end"]["rate"] == 1.0 for i in items)
    agg = ev.aggregate_items(items)
    assert agg["command_metrics"]["command_capture_rate"] == 0.0 and agg["command_metrics"]["too_short_rate"] == 0.5
    assert agg["command_metrics"]["no_speech_false_negatives"] == 1 and agg["wer"]["decoded"]["rate"] is None


# =========================================================================================
# C. non-speech controls and the hallucination metric
# =========================================================================================

def test_silence_with_no_decoded_text_is_a_correct_control(tmp_path, wav):
    items, _, comps = run(tmp_path, [synth_rec("SIL", write_wav(tmp_path / "s.wav", clip(speech=())), "SILENCE_CONTROL")])
    (item,) = items
    h = item["hallucination_control"]
    assert (h["is_control"], h["valid"], h["hallucinated"]) == (True, True, False) and comps.stt.calls == []
    assert item["wer"] is None and item["pipeline_status"] == "NO_SPEECH"
    assert ev.aggregate_items(items)["hallucination"]["hallucination_control_rate"] == 0.0


def test_a_control_that_reaches_the_stt_and_gets_text_is_a_hallucination(tmp_path, wav):
    loud = write_wav(tmp_path / "noise.wav", clip(speech=((1.0, 2.0),)))                   # the fake VAD takes the loud region for speech
    items, _, comps = run(tmp_path, [synth_rec("NOISE", loud, "NOISE_CONTROL")], replies=["you"])
    (item,) = items
    assert item["hallucination_control"] == {"is_control": True, "valid": True, "hallucinated": True, "invalid_reason": None, "stt_direct": None}
    assert item["segmentation"]["false_speech_trigger"] is True and len(comps.stt.calls) == 1
    agg = ev.aggregate_items(items)
    assert agg["hallucination"]["hallucination_control_rate"] == 1.0 and agg["false_speech_trigger"]["rate"] == 1.0


def test_a_control_whose_decode_is_empty_is_not_a_hallucination(tmp_path):
    loud = write_wav(tmp_path / "n.wav", clip(speech=((1.0, 2.0),)))
    items, _, _ = run(tmp_path, [synth_rec("N", loud, "NOISE_CONTROL")], replies=[""])
    assert items[0]["hallucination_control"]["hallucinated"] is False and items[0]["pipeline_status"] == "STT_EMPTY"


def test_a_speech_clip_with_a_wrong_transcript_is_an_stt_error_not_a_hallucination_control_sample(tmp_path, wav):
    items, _, _ = run(tmp_path, [synth_rec("SPEECH", wav())], replies=["hey jarvis open the garage door"])
    (item,) = items
    assert item["hallucination_control"]["is_control"] is False and item["hallucination_control"]["hallucinated"] is None
    assert item["wer"]["rate"] > 0
    agg = ev.aggregate_items(items)
    assert agg["hallucination"]["valid_controls"] == 0 and agg["hallucination"]["hallucination_control_rate"] is None


def test_an_unjudgeable_control_is_invalid_and_leaves_the_denominator(tmp_path):
    loud = write_wav(tmp_path / "n.wav", clip(speech=((1.0, 2.0),)))
    clean = write_wav(tmp_path / "c.wav", clip(speech=()))
    recs = [synth_rec("BAD", loud, "NOISE_CONTROL"), synth_rec("GOOD", clean, "SILENCE_CONTROL")]
    items, _, _ = run(tmp_path, recs, replies=[SttInferenceFailed("x")])
    bad, good = items
    assert bad["pipeline_status"] == "STT_FAILED" and bad["hallucination_control"]["valid"] is False and bad["hallucination_control"]["invalid_reason"] == "STT_FAILED"
    h = ev.aggregate_items(items)["hallucination"]
    assert (h["non_speech_controls"], h["valid_controls"], h["invalid_controls"], h["hallucinated"]) == (2, 1, 1, 0) and h["hallucination_control_rate"] == 0.0


def test_an_invalid_control_is_not_in_the_denominator_of_the_rate(tmp_path):
    loud1 = write_wav(tmp_path / "n1.wav", clip(speech=((1.0, 2.0),)))
    loud2 = write_wav(tmp_path / "n2.wav", clip(speech=((1.0, 2.0),)))
    recs = [synth_rec("HALLU", loud1, "NOISE_CONTROL"), synth_rec("BROKEN", loud2, "NOISE_CONTROL")]
    items, _, _ = run(tmp_path, recs, replies=["you", SttInferenceFailed("x")])
    h = ev.aggregate_items(items)["hallucination"]
    assert (h["non_speech_controls"], h["valid_controls"], h["hallucinated"]) == (2, 1, 1)
    assert h["hallucination_control_rate"] == 1.0                      # 1 / 1 valid, NOT 1 / 2


def test_the_rate_over_several_controls(tmp_path):
    recs = []
    for k, (cat, loud) in enumerate([("NOISE_CONTROL", True), ("NOISE_CONTROL", True), ("SILENCE_CONTROL", False), ("SHORT_NON_COMMAND", False)]):
        recs.append(synth_rec(f"C{k}", write_wav(tmp_path / f"c{k}.wav", clip(speech=((1.0, 2.0),) if loud else ())), cat))
    items, _, _ = run(tmp_path, recs, replies=["you", ""])
    h = ev.aggregate_items(items)["hallucination"]
    assert h["valid_controls"] == 4 and h["hallucinated"] == 1 and h["hallucination_control_rate"] == 0.25
    assert h["by_category"]["NOISE_CONTROL"] == {"valid": 2, "hallucinated": 1}


def test_stt_direct_controls_bypass_the_vad_and_are_reported_separately(tmp_path):
    silent = write_wav(tmp_path / "s.wav", clip(speech=()))
    items, report, comps = run(tmp_path, [synth_rec("SIL", silent, "SILENCE_CONTROL")], replies=["thanks for watching"], stt_direct_controls=True)
    (item,) = items
    assert item["hallucination_control"]["hallucinated"] is False                            # the pipeline (VAD-gated) was clean
    direct = item["hallucination_control"]["stt_direct"]
    assert direct["ran"] and direct["hallucinated"] is True                                  # but the STT alone invented text
    h = report["synthetic_plumbing_only"]["aggregate"]["hallucination"]
    assert h["stt_direct"] == {"controls_run": 1, "hallucinated": 1, "rate": 1.0} and h["hallucination_control_rate"] == 0.0


# =========================================================================================
# D. exclusions, wake modes
# =========================================================================================

def test_missing_hash_mismatched_and_non_native_audio_are_excluded_never_converted(tmp_path, wav):
    good = wav("good.wav")
    wrong_rate = write_wav(tmp_path / "rate.wav", clip(), rate=44100)
    stereo = write_wav(tmp_path / "stereo.wav", np.zeros(8000, np.int16), channels=2)
    recs = [synth_rec("MISSING", tmp_path / "nope.wav"), synth_rec("HASH", good, audio_sha256="0" * 64), synth_rec("RATE", wrong_rate),
            synth_rec("STEREO", stereo), synth_rec("OK", good)]
    items, report, _ = run(tmp_path, recs)
    reasons = {i["recording_id"]: i["exclusion_reason"] for i in items}
    assert reasons == {"MISSING": "AUDIO_MISSING", "HASH": "AUDIO_HASH_MISMATCH", "RATE": "NON_NATIVE_FORMAT", "STEREO": "NON_NATIVE_FORMAT", "OK": None}
    assert report["corpus"]["excluded"] == 4 and report["corpus"]["exclusion_reasons"]["NON_NATIVE_FORMAT"] == 2
    for i in items:
        ev.validate_item(i)


def test_a_matching_audio_hash_is_accepted_and_recorded(tmp_path, wav):
    path = wav()
    sha = ev._sha256_file(path)
    items, _, _ = run(tmp_path, [synth_rec("H", path, audio_sha256=sha)])
    assert items[0]["item_status"] == "EVALUATED" and items[0]["provenance"]["audio_sha256"] == sha


def test_a_wake_category_without_an_annotation_is_rejected_by_the_preflight_in_annotated_mode(tmp_path, wav):
    rec = synth_rec("NOANN", wav())
    del rec["wake_end_sample"]
    with pytest.raises(ev.EvalInputError, match="NOANN: wake_end_sample is required"):
        run(tmp_path, [rec])                                           # preflight: nothing was evaluated
    start_items, _, _ = run(tmp_path, [rec], wake_mode="start")        # the 'start' mode does not need the annotation
    assert start_items[0]["item_status"] == "EVALUATED"
    manifest = parse([rec], tmp_path)
    item = ev.evaluate_recording(manifest.recordings[0], make_components(), "annotated")      # defensive path: a direct call still excludes
    assert item["exclusion_reason"] == "WAKE_ANNOTATION_MISSING"


def test_detector_mode_measures_missed_wakes_and_false_activations(tmp_path, wav):
    silent = write_wav(tmp_path / "s.wav", clip(speech=()))
    recs = [synth_rec("HIT", wav("a.wav")), synth_rec("MISS", wav("b.wav")), synth_rec("CTRL", silent, "SILENCE_CONTROL"),
            synth_rec("NOWAKE", wav("c.wav"), "SPEECH_NO_WAKE")]
    makers = iter([FiresAt(10), FiresAt(None), FiresAt(None), FiresAt(2), FiresAt(2)])        # per detector the pipeline / pass uses
    items, report, _ = run(tmp_path, recs, wake_mode="detector", detector_factory=lambda: next(makers))
    hit, miss, ctrl, nowake = items
    assert hit["wake_detected"] and miss["pipeline_status"] == "NO_ACTIVATION" and miss["wer"] is None
    assert ctrl["activation_kind"] == "start" and ctrl["false_activations"] == 0                 # the control run itself is forced
    assert nowake["activation_kind"] == "detector" and nowake["false_activations"] == 1
    wd = ev.aggregate_items(items)["wake_detection"]
    assert wd["measured"] is True and wd["wake_required_items"] == 2 and wd["missed_wakes"] == 1 and wd["missed_wake_rate"] == 0.5
    assert (wd["negative_items"], wd["negative_items_with_false_activation"], wd["false_activations"]) == (2, 1, 1)


def test_detector_mode_without_a_detector_is_rejected_by_the_preflight_instead_of_faking_one(tmp_path, wav):
    with pytest.raises(ev.EvalInputError, match="W: wake mode 'detector' needs a wake detector"):
        run(tmp_path, [synth_rec("W", wav())], wake_mode="detector")
    manifest = parse([synth_rec("W", wav())], tmp_path)
    item = ev.evaluate_recording(manifest.recordings[0], make_components(), "detector")       # defensive path: a direct call still excludes
    assert item["exclusion_reason"] == "DETECTOR_UNAVAILABLE"


def test_simulated_wake_modes_say_that_wake_accuracy_is_not_measured(tmp_path, wav):
    _, report, _ = run(tmp_path, [synth_rec("W", wav())])
    assert report["configuration"]["wake_simulated"] is True
    assert report["synthetic_plumbing_only"]["aggregate"]["wake_detection"]["measured"] is False
    assert "NOT MEASURED" in report["synthetic_plumbing_only"]["aggregate"]["wake_detection"]["reason"]


# =========================================================================================
# E. segmentation geometry
# =========================================================================================

def test_annotated_speech_gives_offsets_and_coverage_but_no_frame_level_claim(tmp_path, wav):
    rec = synth_rec("SEG", wav(), speech_start_sample=16000, speech_end_sample=40000)
    items, report, _ = run(tmp_path, [rec])
    seg = items[0]["segmentation"]
    assert seg["captured"] is True and seg["speech_covered_fraction"] == pytest.approx(1.0, abs=0.01)         # up to one VAD frame is lost at the end
    assert seg["start_offset_samples"] == items[0]["segment_start_sample"] - 16000 and seg["end_offset_samples"] == items[0]["segment_end_sample"] - 40000
    agg = ev.aggregate_items(items)["segmentation"]
    assert agg["items_with_annotation"] == 1 and "NOT frame-level" in agg["note"]


# =========================================================================================
# F. splits
# =========================================================================================

def _two_split_recordings(wav):
    return [synth_rec("D1", wav("d.wav")), synth_rec("H1", wav("h.wav"), split="HELDOUT")]


def test_the_default_run_is_dev_only_and_heldout_is_not_run(tmp_path, wav):
    items, report, _ = run(tmp_path, _two_split_recordings(wav))
    assert [i["recording_id"] for i in items] == ["D1"] and report["corpus"]["not_run_by_split"] == {"HELDOUT": 1}
    assert report["split_policy"] == {"splits_evaluated": ["DEV"], "calibration_allowed_splits": ["DEV"], "heldout_evaluated": False, "heldout_warning": None}


def test_the_defaults_are_dev_only_in_the_api_and_on_the_command_line(tmp_path, wav):
    manifest = parse(_two_split_recordings(wav), tmp_path)
    items, report = ev.evaluate_manifest(manifest, make_components())            # no splits argument at all
    assert [i["recording_id"] for i in items] == ["D1"] and report["split_policy"]["splits_evaluated"] == ["DEV"]
    args = ev._parser().parse_args(["run", "--manifest", "m.json", "--output", "o"])
    assert args.split == "DEV" and args.allow_heldout is False and args.segmenter_config is None and args.wake_mode == "annotated"


def test_heldout_needs_the_explicit_flag_and_a_frozen_config(tmp_path, wav):
    with pytest.raises(ev.EvalInputError, match="--allow-heldout"):
        run(tmp_path, _two_split_recordings(wav), splits=("DEV", "HELDOUT"))
    with pytest.raises(ev.EvalInputError, match="frozen --segmenter-config"):
        run(tmp_path, _two_split_recordings(wav), splits=("HELDOUT",), allow_heldout=True)
    frozen = {"kind": "FILE", "sha256": "a" * 64, "frozen": True}
    items, report, _ = run(tmp_path, _two_split_recordings(wav), splits=("HELDOUT",), allow_heldout=True, source=frozen)
    assert [i["recording_id"] for i in items] == ["H1"] and report["split_policy"]["heldout_evaluated"] is True
    assert "MUST NOT be used to tune" in report["split_policy"]["heldout_warning"]


def test_unknown_split_and_train_are_refused():
    with pytest.raises(ev.EvalInputError, match="unknown split"):
        ev.check_split_request(["TRAIN"], False, {"frozen": True})


# =========================================================================================
# G. report content
# =========================================================================================

def test_the_report_carries_models_configuration_counts_and_limitations(tmp_path, wav):
    silent = write_wav(tmp_path / "s.wav", clip(speech=()))
    items, report, comps = run(tmp_path, [synth_rec("A", wav()), synth_rec("B", silent, "SILENCE_CONTROL")], clock=FakeClock())
    assert report["schema_version"] == ev.SCHEMA_REPORT and report["corpus_kind"] == "SYNTHETIC_ONLY"
    assert report["corpus"]["items"] == 2 and report["corpus"]["by_category"] == {"WAKE_IMMEDIATE_COMMAND": 1, "SILENCE_CONTROL": 1}
    assert report["models"]["stt"]["model_id"] == "fake-model@test" and report["models"]["stt"]["engine_id"] == "fake-stt"
    assert report["models"]["stt"]["snapshot_verified"] is True and report["models"]["vad"]["frame_samples"] == 512
    cfg = report["configuration"]
    assert cfg["segmenter"]["sha256"] == ev.config_fingerprint(cfg["segmenter"]["values"]) and cfg["segmenter"]["source"]["kind"] == "TEST"
    assert cfg["normalization"] == "week7-text-norm-v1" and cfg["wake_phrases"] == ["hey jarvis"] and cfg["wake_mode"] == "annotated"
    sd = report["synthetic_plumbing_only"]["aggregate"]["status_distribution"]
    assert set(sd["counts"]) == set(ev.PIPELINE_STATUS_VALUES) and sd["counts"]["TRANSCRIBED"] == 1 and sd["counts"]["NO_SPEECH"] == 1 and sd["denominator"] == 2
    assert report["limitations"] and report["claims"] == {"hardware_validated": False, "live_validated": False, "raspberry_pi_measured": False,
                                                          "week7_complete": False, "task5_complete": False}
    json.dumps(report)                                                                       # fully serialisable


def test_synthetic_results_are_never_reported_as_accuracy_results(tmp_path, wav):
    _, report, _ = run(tmp_path, [synth_rec("A", wav())])
    assert report["accuracy_results"] is None and "NO REAL RECORDINGS EVALUATED" in report["accuracy_results_note"]
    assert "NOT AN ACCURACY RESULT" in report["synthetic_plumbing_only"]["label"]


def test_real_recordings_populate_accuracy_results_per_split_and_synthetic_stays_separate(tmp_path, wav):
    recs = [real_rec("R1", wav("r1.wav")), real_rec("R2", wav("r2.wav"), speaker_code="S02", split="HELDOUT"), synth_rec("S1", wav("s1.wav"))]
    frozen = {"kind": "FILE", "sha256": "b" * 64, "frozen": True}
    _, report, _ = run(tmp_path, recs, splits=("DEV", "HELDOUT"), allow_heldout=True, source=frozen)
    assert report["corpus_kind"] == "MIXED" and report["accuracy_results"]["overall"]["corpus"]["evaluated"] == 2
    assert set(report["accuracy_results"]["by_split"]) == {"DEV", "HELDOUT"}
    assert report["accuracy_results"]["by_split"]["DEV"]["corpus"]["evaluated"] == 1
    assert report["synthetic_plumbing_only"]["aggregate"]["corpus"]["evaluated"] == 1
    assert report["accuracy_results_note"] == "measured on REAL recordings only"


def test_validation_labels_follow_the_origin_and_can_never_be_hardware_validated_or_live(tmp_path, wav):
    assert ev.validation_label("SYNTHETIC") == "SOFTWARE-VALIDATED (SYNTHETIC INPUT)"
    for origin in ev.ORIGINS:
        label = ev.validation_label(origin).upper()
        assert "HARDWARE-VALIDATED" not in label and "LIVE" not in label
    items, _, _ = run(tmp_path, [synth_rec("A", wav())])
    for bad in ("HARDWARE-VALIDATED", "LIVE HARDWARE-VALIDATED", "live capture"):
        with pytest.raises(ev.EvalInputError, match="HARDWARE-VALIDATED or LIVE"):
            ev.validate_item({**items[0], "validation_label": bad})


def test_validate_item_rejects_pcm_and_schema_drift(tmp_path, wav):
    items, _, _ = run(tmp_path, [synth_rec("A", wav())])
    item = items[0]
    with pytest.raises(ev.EvalInputError, match="PCM"):
        ev.validate_item({**item, "notes": np.zeros(4, np.int16)})
    with pytest.raises(ev.EvalInputError, match="PCM"):
        ev.validate_item({**item, "provenance": {**item["provenance"], "x": [b"\x00\x01"]}})
    with pytest.raises(ev.EvalInputError, match="item keys"):
        ev.validate_item({**item, "extra": 1})
    with pytest.raises(ev.EvalInputError, match="not a known status"):
        ev.validate_item({**item, "pipeline_status": "GREAT"})
    with pytest.raises(ev.EvalInputError, match="record the model id"):
        ev.validate_item({**item, "model_id": None})
    with pytest.raises(ev.EvalInputError, match="review_status"):
        ev.validate_item({**item, "review_status": "GOOD"})


def test_item_keys_cover_the_requested_result_schema(tmp_path, wav):
    items, _, _ = run(tmp_path, [synth_rec("A", wav())])
    required = {"recording_id", "split", "provenance", "pipeline_status", "wake_detected", "activation_sample", "segment_start_sample",
                "segment_end_sample", "segment_duration_s", "raw_stt_status", "reference_available", "wer", "cer", "wake_prefix",
                "hallucination_control", "timings", "validation_label", "review_status"}
    assert required <= set(items[0]) and {"stt_inference_ms", "pipeline_wall_ms"} <= set(items[0]["timings"])
    assert items[0]["model_id"] == "fake-model@test" and items[0]["stt_engine_id"] == "fake-stt"


def test_human_review_is_recorded_without_replacing_wer(tmp_path, wav):
    items, report, _ = run(tmp_path, [synth_rec("A", wav("a.wav"), human_review="PARTIAL"), synth_rec("B", wav("b.wav"))])
    assert [i["review_status"] for i in items] == ["PARTIAL", "NOT_REVIEWED"] and all(i["wer"] is not None for i in items)
    assert report["synthetic_plumbing_only"]["aggregate"]["human_review"] == {"MATCH": 0, "PARTIAL": 1, "MISMATCH": 0, "EMPTY": 0, "NOT_REVIEWED": 1}


# =========================================================================================
# H. timings and startup
# =========================================================================================

def test_timings_are_recorded_and_the_first_inference_is_kept_out_of_the_steady_state(tmp_path, wav):
    recs = [synth_rec(f"T{k}", wav(f"t{k}.wav")) for k in range(3)]
    items, report, _ = run(tmp_path, recs, clock=FakeClock())
    t = [i["timings"] for i in items]
    assert [x["stt_first_call_of_run"] for x in t] == [True, False, False]
    assert all(x["pipeline_wall_ms"] > 0 and x["audio_duration_s"] == 4.0 and x["real_time_factor"] > 0 for x in t)
    assert all(x["vad_frames"] and x["vad_ms"] >= 0 and x["stt_calls"] == 1 and x["stt_inference_ms"] >= 0 for x in t)
    lat = report["synthetic_plumbing_only"]["aggregate"]["latency"]
    assert lat["stt_inference_ms_steady_state"]["n"] == 2 and lat["pipeline_wall_ms"]["n"] == 3
    assert report["startup"]["silero_construct_ms"] == 1.0 and report["startup"]["stt_first_inference_ms"] == t[0]["stt_inference_ms"]
    assert "separately from steady-state" in report["startup"]["note"]


# =========================================================================================
# I. configuration policy
# =========================================================================================

def test_max_utterance_above_the_stt_limit_is_reported_as_a_policy_conflict_and_not_truncated(tmp_path, wav):
    values = dict(ev.EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES, max_utterance_s=40.0)
    _, report, comps = run(tmp_path, [synth_rec("A", wav())], values=values)
    (conflict,) = report["policy_conflicts"]
    assert conflict["code"] == "MAX_UTTERANCE_EXCEEDS_STT_LIMIT" and conflict["max_utterance_s"] == 40.0 and conflict["stt_max_audio_s"] == 30.0
    assert "nothing is truncated" in conflict["effect"]
    _, ok_report, _ = run(tmp_path, [synth_rec("A", wav())])
    assert ok_report["policy_conflicts"] == []


def test_segmenter_config_loading(tmp_path):
    values, source = ev.load_segmenter_values(None)
    assert source["kind"] == "EVAL_DEFAULT_UNCALIBRATED" and source["frozen"] is False and values == ev.EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(ev.EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES), encoding="utf-8")
    loaded, src = ev.load_segmenter_values(str(path))
    assert src["kind"] == "FILE" and src["frozen"] is True and len(src["sha256"]) == 64 and loaded == values
    with pytest.raises(ev.EvalInputError, match="exactly"):
        ev.segmenter_config_from_values({"start_threshold": 0.5}, 512)
    with pytest.raises(ev.EvalInputError, match="not found"):
        ev.load_segmenter_values(str(tmp_path / "missing.json"))


def test_config_settings_still_hold_none_and_are_never_written_by_the_harness():
    from config import settings
    for key in ("VAD_START_THRESHOLD", "VAD_END_THRESHOLD", "VOICE_MAX_UTTERANCE_S"):
        assert getattr(settings, key) is None
    import ast
    tree = ast.parse(Path(ev.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if m == "config" or m.startswith("config.") and m != "config.logging_config"}      # settings are never imported, so never written


# =========================================================================================
# J. evidence output
# =========================================================================================

def test_outputs_are_written_exclusively_and_never_overwrite(tmp_path, wav):
    items, report, _ = run(tmp_path, [synth_rec("A", wav())])
    out = tmp_path / "out"
    items_path, report_path = ev.write_outputs(out, items, report, contains_real=False)
    lines = items_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["recording_id"] == "A" and json.loads(report_path.read_text(encoding="utf-8"))["corpus"]["items"] == 1
    before = items_path.read_bytes()
    with pytest.raises(ev.EvalEvidenceError, match="already exists"):
        ev.write_outputs(out, items, report, contains_real=False)
    assert items_path.read_bytes() == before


def test_real_evidence_inside_the_repository_is_refused_but_synthetic_may_go_anywhere(tmp_path, wav):
    items, report, _ = run(tmp_path, [synth_rec("A", wav())])
    inside = REPO_ROOT / "tests" / "_should_never_be_written"
    with pytest.raises(ev.EvalEvidenceError, match="outside the source repository"):
        ev.write_outputs(inside, items, report, contains_real=True)
    assert not inside.exists()
    with pytest.raises(ev.EvalEvidenceError, match="outside the source repository"):
        ev.prepare_output_dir(REPO_ROOT / "evaluation" / "_nope", contains_real=True, names=("x",))
    assert ev.prepare_output_dir(tmp_path / "fine", contains_real=True, names=("x",)).is_dir()


def test_the_item_file_holds_the_transcripts_and_the_report_does_not(tmp_path, wav):
    secret = "zebracrossing"
    rec = synth_rec("A", wav(), reference_command_text=f"open the {secret}")
    items, report, _ = run(tmp_path, [rec], replies=[f"hey jarvis open the {secret}"])
    ip, rp = ev.write_outputs(tmp_path / "o", items, report, contains_real=False)
    assert secret in ip.read_text(encoding="utf-8")                      # controlled evidence artifact
    assert secret not in rp.read_text(encoding="utf-8")                  # aggregate report: structure only


def test_normal_logs_never_carry_transcripts_references_or_consent_details(tmp_path, wav, logs):
    secret = "zebracrossing"
    rec = real_rec("REAL-1", wav(), reference_command_text=f"open the {secret}", consent_reference="CONSENT-77123", notes="private note")
    silent = write_wav(tmp_path / "s.wav", clip(speech=()))
    manifest = parse([rec, real_rec("REAL-2", silent, "SILENCE_CONTROL"), real_rec("REAL-3", wav("x.wav"), speaker_code="S02")], tmp_path)
    comps = make_components([f"hey jarvis open the {secret}", SttInferenceFailed(f"decoder said {secret}")])
    ev.evaluate_manifest(manifest, comps)
    joined = "\n".join(logs)
    assert "event=week7_eval_item" in joined and "event=week7_eval_start" in joined and "pipeline_status=TRANSCRIBED" in joined
    for forbidden in (secret, "CONSENT-77123", "private note", "S01", "S02", "hey jarvis", "test-mic"):
        assert forbidden not in joined, forbidden


# =========================================================================================
# K. CLI
# =========================================================================================

def _cli(*args):
    return subprocess.run([sys.executable, "-m", "evaluation.week7_voice_eval", *args], cwd=str(REPO_ROOT), capture_output=True, text=True)


def test_cli_validate_accepts_a_good_manifest_and_refuses_the_template(tmp_path, wav):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest_dict([synth_rec("A", wav())])), encoding="utf-8")
    ok = _cli("validate", "--manifest", str(path))
    assert ok.returncode == 0 and "manifest OK: 1 recording" in ok.stdout
    template = _cli("validate", "--manifest", str(TEMPLATE))
    assert template.returncode == 2 and "TEMPLATE" in template.stderr
    assert _cli("validate", "--manifest", str(TEMPLATE), "--allow-template").returncode == 0


def test_cli_rejects_bad_requests_before_loading_any_model(tmp_path, wav):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest_dict([real_rec("R", wav(), consent_reference="UNKNOWN")])), encoding="utf-8")
    assert _cli("run", "--manifest", str(path), "--output", str(tmp_path / "o")).returncode == 2
    good = tmp_path / "g.json"
    good.write_text(json.dumps(manifest_dict([real_rec("R", wav())])), encoding="utf-8")
    inside = _cli("run", "--manifest", str(good), "--output", str(REPO_ROOT / "tests" / "_never"))
    assert inside.returncode == 2 and "outside the source repository" in inside.stderr and not (REPO_ROOT / "tests" / "_never").exists()
    held = _cli("run", "--manifest", str(good), "--output", str(tmp_path / "o2"), "--split", "HELDOUT")
    assert held.returncode == 2 and "--allow-heldout" in held.stderr and not (tmp_path / "o2").exists()
    assert _cli("run", "--manifest", str(tmp_path / "missing.json"), "--output", str(tmp_path / "o3")).returncode == 2
    (tmp_path / "o4").mkdir()
    (tmp_path / "o4" / ev.REPORT_FILENAME).write_text("existing", encoding="utf-8")
    exists = _cli("run", "--manifest", str(good), "--output", str(tmp_path / "o4"))
    assert exists.returncode == 2 and "already exists" in exists.stderr and (tmp_path / "o4" / ev.REPORT_FILENAME).read_text() == "existing"


def test_cli_refuses_existing_evidence_before_any_model_is_loaded(tmp_path, wav, monkeypatch):
    """In-process, with model construction forbidden: the existing-evidence check must come first (and not depend on a model)."""
    good = tmp_path / "g.json"
    good.write_text(json.dumps(manifest_dict([real_rec("R", wav())])), encoding="utf-8")
    out = tmp_path / "o"
    out.mkdir()
    (out / ev.ITEMS_FILENAME).write_text("existing", encoding="utf-8")
    monkeypatch.setattr(ev, "build_real_components", lambda *a, **k: pytest.fail("a model was constructed"))
    assert ev.main(["run", "--manifest", str(good), "--output", str(out)]) == 2
    assert (out / ev.ITEMS_FILENAME).read_text(encoding="utf-8") == "existing" and not (out / ev.REPORT_FILENAME).exists()


def test_cli_reports_a_missing_or_unverifiable_model_as_a_clean_error(tmp_path, wav, monkeypatch, capsys):
    from audio.silero_vad import SileroModelNotFound
    from speech.stt import SttModelNotFound
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest_dict([synth_rec("A", wav())])), encoding="utf-8")
    for error in (SileroModelNotFound("models/silero missing"), SttModelNotFound("model directory not found")):
        def boom(*a, **k):
            raise error
        monkeypatch.setattr(ev, "build_real_components", boom)
        assert ev.main(["run", "--manifest", str(path), "--output", str(tmp_path / f"o_{type(error).__name__}")]) == 2
        assert type(error).__name__ in capsys.readouterr().err


def test_cli_end_to_end_with_the_real_models_on_synthetic_audio(tmp_path, wav):
    """Plumbing only: the real Silero + faster-whisper engines driven through the CLI. Skips when a model is absent."""
    from audio.silero_vad import SileroModelNotFound, SileroOnnxVadEngine, SileroRuntimeUnavailable
    from speech.faster_whisper_backend import FasterWhisperEngine
    from speech.stt import SttModelNotFound, SttRuntimeUnavailable
    try:
        SileroOnnxVadEngine()
        FasterWhisperEngine()
    except (SileroModelNotFound, SttModelNotFound, SileroRuntimeUnavailable, SttRuntimeUnavailable) as exc:
        pytest.skip(f"a model is not available on this machine: {type(exc).__name__}")
    silent = write_wav(tmp_path / "silent.wav", np.zeros(5 * SR, np.int16))
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest_dict([synth_rec("SIL", silent, "SILENCE_CONTROL")])), encoding="utf-8")
    result = _cli("run", "--manifest", str(path), "--output", str(tmp_path / "out"), "--stt-direct-controls")
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "out" / ev.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert report["accuracy_results"] is None and report["models"]["vad"]["artifact_verified"] is True
    assert report["models"]["stt"]["snapshot_verified"] is True and report["models"]["stt"]["decoder_options"]["language"] == "en"
    assert report["startup"]["silero_construct_ms"] > 0 and report["startup"]["whisper_construct_ms"] > 0
    assert "NO real recordings" in result.stdout or "no real recordings" in result.stdout
    item = json.loads((tmp_path / "out" / ev.ITEMS_FILENAME).read_text(encoding="utf-8").splitlines()[0])
    assert item["pipeline_status"] in ev.PIPELINE_STATUS_VALUES and item["validation_label"] == "SOFTWARE-VALIDATED (SYNTHETIC INPUT)"
