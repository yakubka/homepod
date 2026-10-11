"""Tests for evaluation/manifest.py: schema, I/O and validation rules."""

import csv
import os
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from evaluation import manifest as mf
from tests.audio_helpers import write_pcm_wav

REPO_ROOT = Path(__file__).resolve().parents[1]


def positive(n=1, **over):
    """A valid, verified positive row with obviously fake hashes."""
    row = {c: "" for c in mf.COLUMNS}
    row.update({
        "clip_id": f"POS_S01_{n:04d}", "file_path": f"processed/positive/POS_S01_{n:04d}.wav",
        "sha256": f"{n:02x}" * 32, "raw_sha256": f"{n + 100:02x}" * 32,
        "raw_path": f"raw/SES01/take{n}.wav", "label_class": "positive", "is_synthetic": "0",
        "data_origin": "team_recorded", "consent_ref": "CONSENT-S01",
        "sample_rate": "16000", "channels": "1", "bit_depth": "16", "duration_s": "4.000",
        "orig_format": "16000Hz/1ch/16bit", "processing": "none",
        "session_id": "SES01", "recorded_date": "2026-09-21", "recording_source": "laptop_builtin",
        "hardware_track": "A", "room_id": "R1", "environment": "living_room", "condition": "quiet",
        "speaker_id": "S01", "distance_m": "2", "speaking_style": "normal", "take_number": str(n),
        "prompt_text": "hey jarvis", "take_valid": "1",
        "wake_start_sample": "24000", "wake_end_sample": "40000", "wake_start_s": "1.500",
        "wake_end_s": "2.500", "annotation_method": "manual", "annotator_id": "A01",
        "annotation_status": "verified", "split": "dev", "split_version": "v1",
    })
    row.update(over)
    return row


def negative(label="negative_media", n=1, **over):
    row = positive(n)
    row.update({
        "clip_id": f"NEG_{n:04d}", "file_path": f"processed/{label}/NEG_{n:04d}.wav", "label_class": label,
        "speaker_id": "", "distance_m": "", "speaking_style": "na", "condition": "na", "prompt_text": "",
        "take_valid": "", "wake_start_sample": "", "wake_end_sample": "", "wake_start_s": "",
        "wake_end_s": "", "annotation_method": "", "annotator_id": "", "annotation_status": "not_applicable",
        "split": "dev",
    })
    row.update(over)
    return row


def synthetic(n=1, **over):
    row = negative("negative_ambient", n)
    row.update({
        "clip_id": f"SYN_{n:04d}", "file_path": f"processed/synthetic/negative_ambient/SYN_{n:04d}.wav",
        "is_synthetic": "1", "synthetic_method": "silence", "data_origin": "synthetic", "split": "synthetic",
    })
    row.update(over)
    return row


def messages(issues, severity=None):
    return [i.message for i in issues if severity is None or i.severity == severity]


def has(issues, fragment, severity="error"):
    return any(fragment in m for m in messages(issues, severity))


def check(*rows, **kw):
    return mf.validate_manifest(list(rows), **kw)


# -- schema and I/O --------------------------------------------------------------

def test_schema_columns_are_unique_and_cover_the_approved_fields():
    assert len(set(mf.COLUMNS)) == len(mf.COLUMNS)
    for col in ("clip_id", "sha256", "speaker_id", "distance_m", "environment", "condition",
                "recording_source", "wake_start_sample", "wake_end_sample", "split", "is_synthetic"):
        assert col in mf.COLUMNS


def test_speaker_split_matches_the_approved_design():
    assert {s: g for s, g in mf.SPEAKER_SPLIT.items()} == {
        "S01": "dev", "S02": "dev", "S03": "test", "S04": "test", "S05": "test"}


def test_write_read_roundtrip_is_atomic(tmp_path):
    path = tmp_path / "m.csv"
    rows = [positive(1), negative(n=2)]
    mf.write_manifest(path, rows)
    assert mf.read_manifest(path) == [{c: r.get(c, "") for c in mf.COLUMNS} for r in rows]
    assert [p.name for p in tmp_path.iterdir()] == ["m.csv"]        # no temp file left behind


def test_write_manifest_rejects_unknown_columns_and_keeps_the_old_file(tmp_path):
    path = tmp_path / "m.csv"
    mf.write_manifest(path, [positive(1)])
    before = path.read_bytes()
    with pytest.raises(mf.ManifestError):
        mf.write_manifest(path, [dict(positive(2), surprise="x")])
    assert path.read_bytes() == before


def test_read_manifest_rejects_a_wrong_header(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(mf.ManifestError):
        mf.read_manifest(path)


# -- valid rows -----------------------------------------------------------------

def test_valid_rows_have_no_errors():
    issues = check(positive(1), negative(n=2), negative("negative_speech", 3, speaker_id="S03", split="test"),
                   synthetic(4), negative("negative_ambient", 5, split="test"))
    assert mf.errors(issues) == []


# -- required fields / formats -----------------------------------------------------

def test_blank_required_field_is_an_error():
    assert has(check(positive(1, recording_source="")), "recording_source")


def test_bad_hash_date_and_format_are_errors():
    assert has(check(positive(1, sha256="xyz")), "sha256")
    assert has(check(positive(1, recorded_date="21/09/2026")), "recorded_date")
    assert has(check(positive(1, sample_rate="44100")), "sample_rate")
    assert has(check(positive(1, channels="2")), "channels")
    assert has(check(positive(1, bit_depth="24")), "bit_depth")


def test_enumerations_are_enforced():
    assert has(check(positive(1, label_class="positve")), "label_class")
    assert has(check(positive(1, condition="loud")), "condition")
    assert has(check(positive(1, speaking_style="shouting")), "speaking_style")
    assert has(check(positive(1, hardware_track="C")), "hardware_track")


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside.wav", "C:/x.wav", "a\\b.wav"])
def test_paths_must_be_relative_and_inside_the_corpus(bad):
    assert has(check(positive(1, file_path=bad)), "relative forward-slash path")


def test_session_id_is_restricted_because_it_names_a_directory():
    assert has(check(positive(1, session_id="../evil")), "session_id")


# -- privacy: pseudonymous speakers only ------------------------------------------------

@pytest.mark.parametrize("name", ["Alice", "alice smith", "S1", "S001", "Speaker01"])
def test_real_looking_speaker_ids_are_rejected(name):
    assert has(check(positive(1, speaker_id=name)), "pseudonym")


def test_unknown_pseudonym_is_rejected():
    assert has(check(positive(1, speaker_id="S09", split="dev")), "not in the approved speaker split table")


# -- split rules --------------------------------------------------------------------------

def test_split_must_match_the_speakers_group():
    assert has(check(positive(1, speaker_id="S01", split="test")), "implies 'dev'")
    assert has(check(positive(1, speaker_id="S03", split="dev")), "implies 'test'")
    assert mf.errors(check(positive(1, speaker_id="S03", split="test"))) == []


def test_a_speaker_can_never_be_in_two_groups():
    a = positive(1)
    b = positive(2, split="test")                      # same speaker S01 recorded into test
    issues = check(a, b)
    assert has(issues, "S01 appears in more than one evaluation group")


def test_conversation_between_dev_and_test_speakers_is_rejected():
    row = negative("negative_speech", 1, speaker_id="S01+S03", split="dev")
    assert has(check(row), "mixes development and test speakers")
    ok = negative("negative_speech", 2, speaker_id="S03+S04", split="test")
    assert mf.errors(check(ok)) == []


def test_expected_split_helper():
    assert mf.expected_split(positive(1)) == "dev"
    assert mf.expected_split(positive(1, speaker_id="S04")) == "test"
    assert mf.expected_split(negative()) is None
    assert mf.expected_split(synthetic()) == "synthetic"


def test_media_negatives_need_an_explicit_real_split():
    assert has(check(negative(split="synthetic")), "must not have split=synthetic")
    assert mf.errors(check(negative(split="excluded"))) == []


# -- synthetic and real data stay completely separate -------------------------------------------

@pytest.mark.parametrize("over, fragment", [
    ({"data_origin": "team_recorded"}, "data_origin=synthetic"),
    ({"split": "dev"}, "split=synthetic"),
    ({"clip_id": "NOTSYN_1"}, "start with 'SYN_'"),
    ({"file_path": "processed/negative_ambient/SYN_0001.wav"}, "processed/synthetic/"),
    ({"synthetic_method": ""}, "synthetic_method"),
])
def test_synthetic_rows_are_fully_separated(over, fragment):
    assert has(check(synthetic(1, **over)), fragment)


@pytest.mark.parametrize("over, fragment", [
    ({"data_origin": "synthetic"}, "must not have data_origin=synthetic"),
    ({"clip_id": "SYN_0009"}, "reserved for synthetic"),
    ({"file_path": "processed/synthetic/positive/POS.wav"}, "must not live under processed/synthetic/"),
])
def test_real_rows_cannot_pose_as_synthetic(over, fragment):
    assert has(check(positive(1, **over)), fragment)


# -- annotation rules -------------------------------------------------------------------------------

def test_negatives_cannot_carry_wake_boundaries_or_annotation_status():
    assert has(check(negative(wake_start_sample="100")), "only positive clips")
    assert has(check(negative(annotation_status="pending")), "not_applicable")


def test_positive_prompt_must_be_the_target_phrase():
    assert has(check(positive(1, prompt_text="hey travis")), "hey jarvis")


def test_invalid_takes_need_a_reason_and_skip_boundary_checks():
    assert has(check(positive(1, take_valid="0", invalid_reason="")), "invalid_reason")
    ok = positive(1, take_valid="0", invalid_reason="said the wrong words", wake_start_sample="",
                  wake_end_sample="", wake_start_s="", wake_end_s="", annotation_status="not_applicable")
    assert mf.errors(check(ok)) == []


def test_lead_in_must_respect_engine_warmup():
    row = positive(1, wake_start_sample="4000", wake_start_s="0.250")      # 0.25 s < 0.5 s
    assert has(check(row), "minimum lead-in")
    soft = positive(1, wake_start_sample="12000", wake_start_s="0.750")    # ok but under 1 s
    issues = check(soft)
    assert mf.errors(issues) == [] and has(issues, "recommended", "warning")


def test_boundary_consistency_checks():
    assert has(check(positive(1, wake_end_sample="23000", wake_end_s="1.437")), "0 <= start < end")
    assert has(check(positive(1, wake_start_s="9.999")), "wake_start_s does not match")
    assert has(check(positive(1, wake_end_sample="999999", wake_end_s="62.500")), "beyond the end")


def test_pending_is_a_warning_until_require_verified():
    row = positive(1, annotation_status="pending", annotation_method="auto_proposed", annotator_id="")
    assert has(check(row), "pending", "warning") and mf.errors(check(row)) == []
    assert has(check(row, require_verified=True), "pending", "error")


def test_verified_needs_an_annotator():
    assert has(check(positive(1, annotator_id="")), "annotator_id")


def test_distance_and_style_requirements():
    assert has(check(positive(1, distance_m="")), "distance_m")
    assert has(check(positive(1, distance_m="1.3")), "nominal", "warning")
    assert has(check(positive(1, speaking_style="na")), "speaking_style")
    assert mf.errors(check(positive(1, distance_m="3"))) == []            # 3 m is the approved fallback


def test_clipping_is_a_warning():
    assert has(check(positive(1, clipping_fraction="0.02")), "clipped", "warning")


def test_duplicate_clip_ids_and_raw_hashes_are_errors():
    assert has(check(positive(1), positive(1)), "duplicate clip_id")
    a, b = positive(1), positive(2, raw_sha256=positive(1)["raw_sha256"])
    assert has(check(a, b), "duplicate raw_sha256")


# -- file-level checks ---------------------------------------------------------------------------------

def _row_for_file(tmp_path, samples=None, **over):
    path = tmp_path / "processed" / "positive" / "POS_S01_0001.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_pcm_wav(path, np.zeros(64000, dtype=np.int16) if samples is None else samples)
    raw = tmp_path / "raw" / "SES01" / "take1.wav"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"raw-bytes")
    return positive(1, sha256=mf.sha256_file(path), raw_sha256=mf.sha256_file(raw), duration_s="4.000", **over)


def test_file_checks_pass_for_a_consistent_corpus(tmp_path):
    assert mf.errors(mf.validate_manifest([_row_for_file(tmp_path)], tmp_path)) == []


def test_file_checks_detect_missing_modified_and_wrong_format_files(tmp_path):
    row = _row_for_file(tmp_path)
    target = tmp_path / row["file_path"]

    os.chmod(target, 0o666)
    write_pcm_wav(target, np.ones(64000, dtype=np.int16))          # modified after ingest
    assert has(mf.validate_manifest([row], tmp_path), "sha256 does not match")

    write_pcm_wav(target, np.zeros(64000, dtype=np.int16), rate=8000)
    row["sha256"] = mf.sha256_file(target)
    assert has(mf.validate_manifest([row], tmp_path), "need 16000/1/16")

    target.unlink()
    assert has(mf.validate_manifest([row], tmp_path), "file is missing")


def test_file_checks_detect_a_duration_mismatch(tmp_path):
    row = _row_for_file(tmp_path)
    row["duration_s"] = "9.000"
    assert has(mf.validate_manifest([row], tmp_path), "duration_s does not match")


def test_paths_cannot_escape_the_corpus_directory(tmp_path):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"x")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    row = positive(1, file_path="processed/../../outside.wav")
    issues = mf.validate_manifest([row], corpus)
    assert mf.errors(issues)          # rejected by the row check, never opened


# -- the shipped example template -----------------------------------------------------------------------

EXAMPLE = REPO_ROOT / "evaluation" / "manifest.example.csv"


def test_example_manifest_matches_the_schema_and_validates():
    rows = mf.read_manifest(EXAMPLE)                    # raises if the header drifted from COLUMNS
    assert len(rows) >= 5
    assert mf.errors(mf.validate_manifest(rows)) == []


def test_example_manifest_contains_only_obviously_fake_data():
    rows = mf.read_manifest(EXAMPLE)
    for row in rows:
        assert row["notes"].startswith("EXAMPLE"), row["clip_id"]
        assert re.fullmatch(r"(.)\1{63}", row["sha256"]), "example hashes must be visibly fake"
        assert re.fullmatch(r"(?:S\d{2}(\+S\d{2})*)?", row["speaker_id"])
        assert row["consent_ref"].startswith(("EXAMPLE", "na"))
    classes = {r["label_class"] for r in rows}
    assert classes == set(mf.LABEL_CLASSES)             # the template shows every class
    assert any(r["is_synthetic"] == "1" for r in rows)
