"""Tests for evaluation/ingest.py: safe ingest, conversion rules, annotation, CLI.

All audio here is SYNTHETIC. Corpora are created in pytest temp directories
(outside the repository), never in a real data location.
"""

import os
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from evaluation import ingest as ing
from evaluation import manifest as mf
from evaluation.ingest import IngestError, annotate_clip, ingest_recording, init_corpus, validate_corpus
from tests.audio_helpers import clip_with_phrase, noise, write_pcm_wav

REPO_ROOT = Path(__file__).resolve().parents[1]


# -- helpers ---------------------------------------------------------------------

@pytest.fixture
def corpus(tmp_path):
    return init_corpus(tmp_path / "data")


def pos_meta(**over):
    meta = {
        "clip_id": "POS_S01_0001", "label_class": "positive", "session_id": "SES01",
        "recorded_date": "2026-09-21", "recording_source": "laptop_builtin", "hardware_track": "A",
        "room_id": "R1", "environment": "living_room", "condition": "quiet", "speaker_id": "S01",
        "distance_m": "2", "speaking_style": "normal", "consent_ref": "CONSENT-S01",
    }
    meta.update(over)
    return meta


def ambient_meta(**over):
    meta = pos_meta(clip_id="AMB_0001", label_class="negative_ambient", speaker_id="", distance_m="",
                    speaking_style="", split="test")
    meta.update(over)
    return {k: v for k, v in meta.items() if v != ""}


def make_src(tmp_path, samples=None, name="take.wav", **kw):
    path = tmp_path / "input" / name
    path.parent.mkdir(exist_ok=True)
    return write_pcm_wav(path, clip_with_phrase() if samples is None else samples, **kw)


def fingerprint(path):
    st = Path(path).stat()
    return mf.sha256_file(Path(path)), st.st_size, st.st_mtime_ns


def snapshot(corpus):
    return {p.relative_to(corpus).as_posix(): p.stat().st_size for p in corpus.rglob("*") if p.is_file()}


def rows(corpus):
    return mf.read_manifest(corpus / "manifests" / ing.MANIFEST_NAME)


def samples_of(path):
    with wave.open(str(path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        return np.frombuffer(w.readframes(w.getnframes()), "<i2")


# -- locations and directory structure -------------------------------------------------------

def test_init_creates_the_documented_tree_and_an_empty_manifest(tmp_path):
    corpus = init_corpus(tmp_path / "data")
    for sub in ("raw", "annotations", "manifests", "noise_refs", "results"):
        assert (corpus / sub).is_dir()
    for label in mf.LABEL_CLASSES:
        assert (corpus / "processed" / label).is_dir()
        assert (corpus / "processed" / "synthetic" / label).is_dir()
    assert (tmp_path / "data" / "private" / "consent").is_dir()
    assert (corpus / "README.txt").is_file() and (tmp_path / "data" / "private" / "README.txt").is_file()
    assert rows(corpus) == []
    # consent and speaker key live OUTSIDE the audio corpus directory
    assert not (corpus / "private").exists() and not (corpus / "consent").exists()


def test_init_is_idempotent_and_never_overwrites(tmp_path):
    corpus = init_corpus(tmp_path / "data")
    (corpus / "README.txt").write_text("edited by a person", encoding="utf-8")
    mf.write_manifest(corpus / "manifests" / ing.MANIFEST_NAME, [dict.fromkeys(mf.COLUMNS, "x")])
    init_corpus(tmp_path / "data")
    assert (corpus / "README.txt").read_text(encoding="utf-8") == "edited by a person"
    assert len(rows(corpus)) == 1


def test_corpus_inside_the_repository_is_refused_and_nothing_is_created():
    target = REPO_ROOT / "definitely_not_created_corpus"
    with pytest.raises(IngestError, match="inside the repository"):
        init_corpus(target)
    assert not target.exists()
    with pytest.raises(IngestError):
        ing.resolve_data_root(str(REPO_ROOT / "data"))
    with pytest.raises(IngestError):
        ing.resolve_data_root(str(REPO_ROOT))


def test_data_root_resolution_order(tmp_path, monkeypatch):
    monkeypatch.setattr(ing, "DEFAULT_DATA_ROOT", tmp_path / "default")
    monkeypatch.delenv(ing.ENV_VAR, raising=False)
    assert ing.resolve_data_root() == (tmp_path / "default").resolve()
    monkeypatch.setenv(ing.ENV_VAR, str(tmp_path / "from_env"))
    assert ing.resolve_data_root() == (tmp_path / "from_env").resolve()
    assert ing.resolve_data_root(str(tmp_path / "cli")) == (tmp_path / "cli").resolve()


def test_default_location_is_outside_the_repo_and_not_synced():
    assert REPO_ROOT not in ing.DEFAULT_DATA_ROOT.resolve().parents
    assert ing.sync_warning(ing.DEFAULT_DATA_ROOT) is None


def test_cloud_synced_folders_trigger_a_warning():
    assert "cloud-synced" in ing.sync_warning(Path("C:/Users/x/OneDrive/VESTA"))


def test_uninitialised_corpus_is_rejected(tmp_path):
    with pytest.raises(IngestError, match="init"):
        ingest_recording(make_src(tmp_path), tmp_path / "nowhere", pos_meta())


# -- normal ingest ----------------------------------------------------------------------------------

def test_ingest_of_a_conforming_recording(corpus, tmp_path):
    original = clip_with_phrase(1.5, 3.0)
    src = make_src(tmp_path, original, name="take one.wav")
    before = fingerprint(src)

    result = ingest_recording(src, corpus, pos_meta())
    row = result.row

    assert fingerprint(src) == before                                  # original never altered
    assert row["processing"] == "none" and row["orig_format"] == "16000Hz/1ch/16bit"
    assert (row["sample_rate"], row["channels"], row["bit_depth"], row["duration_s"]) == ("16000", "1", "16", "5.000")
    raw, proc = corpus / row["raw_path"], corpus / row["file_path"]
    assert raw.name == "take_one.wav" and raw.read_bytes() == src.read_bytes()
    assert np.array_equal(samples_of(proc), original)
    assert row["raw_sha256"] == mf.sha256_file(src) and row["sha256"] == mf.sha256_file(proc)
    assert row["split"] == "dev"                                        # derived from speaker S01
    assert rows(corpus) == [{c: row.get(c, "") for c in mf.COLUMNS}]
    assert mf.errors(validate_corpus(corpus)) == []


def test_stored_copies_are_read_only(corpus, tmp_path):
    row = ingest_recording(make_src(tmp_path), corpus, pos_meta()).row
    for rel in (row["raw_path"], row["file_path"]):
        with pytest.raises(PermissionError):
            open(corpus / rel, "ab")


def test_quality_fields_and_boundary_proposal_are_filled(corpus, tmp_path):
    row = ingest_recording(make_src(tmp_path, clip_with_phrase(1.5, 3.0)), corpus, pos_meta()).row
    assert -30 < float(row["peak_dbfs"]) < 0
    assert float(row["noise_floor_dbfs"]) < -50
    assert float(row["clipping_fraction"]) == 0.0
    assert row["annotation_method"] == "auto_proposed" and row["annotation_status"] == "pending"
    assert row["annotator_id"] == ""                                    # a proposal is not a verification
    assert float(row["wake_start_s"]) == pytest.approx(1.5, abs=0.06)
    assert float(row["wake_end_s"]) == pytest.approx(3.0, abs=0.06)
    assert int(row["wake_start_sample"]) == round(float(row["wake_start_s"]) * 16000)
    assert 30 < float(row["est_snr_db"]) < 50


def test_lead_in_shorter_than_warmup_discards_the_proposal(corpus, tmp_path):
    src = make_src(tmp_path, clip_with_phrase(0.3, 1.8, total_s=4.0))
    result = ingest_recording(src, corpus, pos_meta())
    row = result.row
    assert row["wake_start_sample"] == "" and row["annotation_method"] == ""
    assert row["annotation_status"] == "pending"
    assert "proposal_discarded" in row["notes"]
    assert any("minimum lead-in" in m for m in result.messages)


def test_no_phrase_found_leaves_annotation_to_a_person(corpus, tmp_path):
    src = make_src(tmp_path, noise(16000 * 4, -65).astype(np.int16))
    result = ingest_recording(src, corpus, pos_meta())
    assert result.row["wake_start_sample"] == "" and "no_boundary_proposal" in result.row["notes"]


def test_invalid_take_is_kept_with_a_reason_and_not_annotated(corpus, tmp_path):
    result = ingest_recording(make_src(tmp_path), corpus,
                              pos_meta(take_valid="0", invalid_reason="said the wrong words"))
    r = result.row
    assert r["take_valid"] == "0" and r["annotation_status"] == "not_applicable"
    assert r["wake_start_sample"] == "" and result.proposal is None


def test_negatives_are_not_annotated_and_split_follows_the_speaker(corpus, tmp_path):
    meta = pos_meta(clip_id="CONF_S04_0001", label_class="negative_confuser", speaker_id="S04",
                    prompt_text="hey travis", speaking_style="", distance_m="2")
    meta = {k: v for k, v in meta.items() if v != ""}
    row = ingest_recording(make_src(tmp_path), corpus, meta).row
    assert row["annotation_status"] == "not_applicable" and row["wake_start_sample"] == ""
    assert row["split"] == "test" and row["file_path"] == "processed/negative_confuser/CONF_S04_0001.wav"


def test_media_negatives_need_an_explicit_split(corpus, tmp_path):
    meta = ambient_meta(clip_id="MEDIA_0001", label_class="negative_media")
    del meta["split"]
    with pytest.raises(IngestError, match="--split"):
        ingest_recording(make_src(tmp_path), corpus, meta)
    assert ingest_recording(make_src(tmp_path), corpus, dict(meta, split="dev")).row["split"] == "dev"


# -- conversion rules -------------------------------------------------------------------------------------

def test_identical_stereo_needs_explicit_convert(corpus, tmp_path):
    src = make_src(tmp_path, channels=2)
    before = snapshot(corpus)
    with pytest.raises(IngestError, match="--convert"):
        ingest_recording(src, corpus, pos_meta())
    assert snapshot(corpus) == before and rows(corpus) == []           # nothing written

    original = clip_with_phrase()
    row = ingest_recording(make_src(tmp_path, original, name="st.wav", channels=2), corpus, pos_meta(),
                           convert=True).row
    assert row["processing"] == "channel_0_of_2_identical" and row["orig_format"] == "16000Hz/2ch/16bit"
    assert np.array_equal(samples_of(corpus / row["file_path"]), original)


def test_unequal_stereo_is_rejected_never_silently_downmixed(corpus, tmp_path):
    left, right = clip_with_phrase(seed=1), clip_with_phrase(seed=2)
    src = make_src(tmp_path, np.stack([left, right], axis=1), channels=2)
    for convert in (False, True):                                       # even --convert must not downmix
        with pytest.raises(IngestError, match="NOT identical"):
            ingest_recording(src, corpus, pos_meta(), convert=convert)
    assert rows(corpus) == []


def test_explicit_channel_selection(corpus, tmp_path):
    left, right = clip_with_phrase(seed=1), clip_with_phrase(seed=2)
    src = make_src(tmp_path, np.stack([left, right], axis=1), channels=2)
    row = ingest_recording(src, corpus, pos_meta(), select_channel=1).row
    assert row["processing"] == "channel_1_of_2_selected"
    assert np.array_equal(samples_of(corpus / row["file_path"]), right)
    with pytest.raises(IngestError, match="out of range"):
        ingest_recording(src, corpus, pos_meta(clip_id="POS_S01_0002"), select_channel=2)


@pytest.mark.parametrize("rate", [48000, 44100, 8000])
def test_other_sample_rates_need_convert_then_are_resampled(corpus, tmp_path, rate):
    src = make_src(tmp_path, noise(rate * 5, -30).astype(np.int16), rate=rate)
    meta = ambient_meta()
    with pytest.raises(IngestError, match="resample"):
        ingest_recording(src, corpus, meta)
    row = ingest_recording(src, corpus, meta, convert=True).row
    assert row["orig_format"] == f"{rate}Hz/1ch/16bit" and row["duration_s"] == "5.000"
    assert f"resample_{rate}_to_16000" in row["processing"]
    assert len(samples_of(corpus / row["file_path"])) == 80000


@pytest.mark.parametrize("width, scale", [(3, 256), (4, 65536)])
def test_higher_bit_depths_convert_exactly(corpus, tmp_path, width, scale):
    original = clip_with_phrase()
    src = make_src(tmp_path, original.astype(np.int64) * scale, width=width)
    with pytest.raises(IngestError, match="16-bit"):
        ingest_recording(src, corpus, pos_meta())
    row = ingest_recording(src, corpus, pos_meta(), convert=True).row
    assert f"pcm{8 * width}_to_int16" in row["processing"]
    assert np.array_equal(samples_of(corpus / row["file_path"]), original)


def test_8_bit_input_converts(corpus, tmp_path):
    eight = (np.random.default_rng(0).integers(-128, 127, 16000 * 3)).astype(np.int32)
    src = make_src(tmp_path, eight, width=1)
    row = ingest_recording(src, corpus, ambient_meta(), convert=True).row
    assert np.array_equal(samples_of(corpus / row["file_path"]), (eight * 256).astype(np.int16))


def _float_wav(path):
    data = np.zeros(1600, dtype="<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, 16000, 64000, 4, 32)             # format tag 3 = IEEE float
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def test_unreadable_inputs_are_rejected_without_writing(corpus, tmp_path):
    before = snapshot(corpus)
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "garbage.wav").write_bytes(b"this is not audio")
    write_pcm_wav(tmp_path / "input" / "empty.wav", np.zeros(0, dtype=np.int16))    # header only, 0 frames
    for name in ("garbage.wav", "empty.wav"):
        with pytest.raises(IngestError):
            ingest_recording(tmp_path / "input" / name, corpus, pos_meta(), convert=True)
    with pytest.raises(IngestError, match="not a readable PCM WAV"):
        ingest_recording(_float_wav(tmp_path / "input" / "float.wav"), corpus, pos_meta(), convert=True)
    with pytest.raises(IngestError, match="not found"):
        ingest_recording(tmp_path / "missing.wav", corpus, pos_meta())
    assert snapshot(corpus) == before


# -- refusing bad or unsafe requests ----------------------------------------------------------------------------

@pytest.mark.parametrize("over, fragment", [
    ({"recording_source": ""}, "recording_source"),
    ({"speaker_id": "S09", "split": "dev"}, "approved speaker split table"),
    ({"speaker_id": "S01", "split": "test"}, "implies 'dev'"),
    ({"session_id": "../escape"}, "session_id"),
    ({"clip_id": "bad id!"}, "clip_id"),
    ({"distance_m": ""}, "distance_m"),
    ({"condition": "loud"}, "condition"),
])
def test_invalid_metadata_is_rejected_before_anything_is_written(corpus, tmp_path, over, fragment):
    before = snapshot(corpus)
    with pytest.raises(IngestError, match=fragment):
        ingest_recording(make_src(tmp_path), corpus, pos_meta(**over))
    assert snapshot(corpus) == before


def test_tool_computed_fields_cannot_be_supplied(corpus, tmp_path):
    with pytest.raises(IngestError, match="unknown or tool-computed"):
        ingest_recording(make_src(tmp_path), corpus, pos_meta(sha256="a" * 64))


def test_duplicate_recording_or_clip_id_is_rejected(corpus, tmp_path):
    src = make_src(tmp_path)
    ingest_recording(src, corpus, pos_meta())
    before = snapshot(corpus)
    with pytest.raises(IngestError, match="duplicate raw_sha256"):
        ingest_recording(src, corpus, pos_meta(clip_id="POS_S01_0002"))         # same recording again
    other = make_src(tmp_path, clip_with_phrase(seed=7), name="other.wav")
    with pytest.raises(IngestError, match="duplicate clip_id"):
        ingest_recording(other, corpus, pos_meta())                             # same id, different audio
    assert snapshot(corpus) == before and len(rows(corpus)) == 1


def test_existing_corpus_files_are_never_overwritten(corpus, tmp_path):
    stray = corpus / "raw" / "SES01" / "take.wav"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"precious")
    src = make_src(tmp_path)
    with pytest.raises(IngestError, match="refusing to overwrite"):
        ingest_recording(src, corpus, pos_meta())
    assert stray.read_bytes() == b"precious" and rows(corpus) == []
    assert not list((corpus / "processed").rglob("*.wav"))


def test_a_failed_manifest_write_rolls_back_the_copied_files(corpus, tmp_path, monkeypatch):
    src = make_src(tmp_path)
    before, fp = snapshot(corpus), fingerprint(src)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(mf, "write_manifest", boom)
    with pytest.raises(RuntimeError):
        ingest_recording(src, corpus, pos_meta())
    monkeypatch.undo()
    assert snapshot(corpus) == before and fingerprint(src) == fp


# -- synthetic data stays separate ------------------------------------------------------------------------------------

def synth_meta(**over):
    meta = {"clip_id": "SYN_SILENCE_0001", "label_class": "negative_ambient", "synthetic_method": "silence"}
    meta.update(over)
    return meta


def test_synthetic_ingest_is_separated_and_labelled(corpus, tmp_path):
    src = make_src(tmp_path, np.zeros(16000 * 10, dtype=np.int16), name="silence.wav")
    row = ingest_recording(src, corpus, synth_meta(), synthetic=True).row
    assert row["is_synthetic"] == "1" and row["data_origin"] == "synthetic" and row["split"] == "synthetic"
    assert row["file_path"] == "processed/synthetic/negative_ambient/SYN_SILENCE_0001.wav"
    assert row["consent_ref"] == "na" and row["hardware_track"] == "NA"


@pytest.mark.parametrize("meta_over, synthetic, fragment", [
    ({"clip_id": "SILENCE_0001"}, True, "SYN_"),                       # synthetic without the prefix
    ({"synthetic_method": ""}, True, "synthetic_method"),
    ({"clip_id": "SYN_REAL_0001", "split": "dev"}, False, "reserved for synthetic"),   # real posing as synthetic
])
def test_synthetic_and_real_cannot_be_mixed(corpus, tmp_path, meta_over, synthetic, fragment):
    src = make_src(tmp_path, np.zeros(16000 * 3, dtype=np.int16))
    meta = ({**synth_meta(), **meta_over} if synthetic
            else ambient_meta(**meta_over))
    with pytest.raises(IngestError, match=fragment):
        ingest_recording(src, corpus, {k: v for k, v in meta.items() if v != ""}, synthetic=synthetic)
    assert rows(corpus) == []


# -- annotation -----------------------------------------------------------------------------------------------------------

def _ingested(corpus, tmp_path, **kw):
    return ingest_recording(make_src(tmp_path), corpus, pos_meta(**kw)).row


def test_accepting_the_proposal_records_a_verification(corpus, tmp_path):
    proposal = _ingested(corpus, tmp_path)
    row = annotate_clip(corpus, "POS_S01_0001", annotator_id="A01", accept_proposal=True)
    assert row["annotation_status"] == "verified" and row["annotation_method"] == "auto_verified"
    assert row["annotator_id"] == "A01"
    assert row["wake_start_sample"] == proposal["wake_start_sample"]
    assert mf.errors(validate_corpus(corpus, require_verified=True)) == []
    assert rows(corpus)[0]["annotation_status"] == "verified"           # persisted


def test_manual_boundaries_override_the_proposal(corpus, tmp_path):
    _ingested(corpus, tmp_path)
    row = annotate_clip(corpus, "POS_S01_0001", annotator_id="A02", start_sample=25000, end_sample=47000)
    assert (row["wake_start_sample"], row["wake_end_sample"]) == ("25000", "47000")
    assert (row["wake_start_s"], row["wake_end_s"]) == ("1.562", "2.938")     # derived, 3 decimals
    assert row["annotation_method"] == "manual" and row["annotator_id"] == "A02"


def test_invalid_annotation_is_rejected_and_the_manifest_is_unchanged(corpus, tmp_path):
    _ingested(corpus, tmp_path)
    path = corpus / "manifests" / ing.MANIFEST_NAME
    before = path.read_bytes()
    with pytest.raises(IngestError, match="minimum lead-in"):
        annotate_clip(corpus, "POS_S01_0001", annotator_id="A01", start_sample=4000, end_sample=30000)
    with pytest.raises(IngestError, match="start"):
        annotate_clip(corpus, "POS_S01_0001", annotator_id="A01", start_sample=30000, end_sample=24000)
    assert path.read_bytes() == before


def test_annotation_can_invalidate_a_take(corpus, tmp_path):
    _ingested(corpus, tmp_path)
    row = annotate_clip(corpus, "POS_S01_0001", annotator_id="A01", invalid_reason="cough over the phrase")
    assert row["take_valid"] == "0" and row["annotation_status"] == "not_applicable" and row["wake_start_sample"] == ""
    with pytest.raises(IngestError, match="invalid"):
        annotate_clip(corpus, "POS_S01_0001", annotator_id="A01", accept_proposal=True)


def test_annotation_argument_errors(corpus, tmp_path):
    _ingested(corpus, tmp_path)
    ingest_recording(make_src(tmp_path, clip_with_phrase(seed=3), name="amb.wav"), corpus, ambient_meta())
    with pytest.raises(IngestError, match="not found"):
        annotate_clip(corpus, "NOPE", annotator_id="A01", accept_proposal=True)
    with pytest.raises(IngestError, match="only positive"):
        annotate_clip(corpus, "AMB_0001", annotator_id="A01", accept_proposal=True)
    with pytest.raises(IngestError, match="annotator_id"):
        annotate_clip(corpus, "POS_S01_0001", annotator_id=" ", accept_proposal=True)
    with pytest.raises(IngestError, match="--start-s"):
        annotate_clip(corpus, "POS_S01_0001", annotator_id="A01")


# -- corpus validation ----------------------------------------------------------------------------------------------------

def test_validate_flags_pending_then_passes_when_verified(corpus, tmp_path):
    _ingested(corpus, tmp_path)
    assert mf.errors(validate_corpus(corpus)) == []
    assert any("pending" in i.message for i in validate_corpus(corpus))
    assert mf.errors(validate_corpus(corpus, require_verified=True))


def test_validate_detects_tampering_and_missing_files(corpus, tmp_path):
    row = _ingested(corpus, tmp_path)
    proc, raw = corpus / row["file_path"], corpus / row["raw_path"]
    os.chmod(proc, 0o666)
    proc.write_bytes(proc.read_bytes()[:-2] + b"\x01\x01")
    assert any("sha256 does not match" in i.message for i in mf.errors(validate_corpus(corpus)))
    os.chmod(raw, 0o666)
    raw.unlink()
    assert any("raw_path file is missing" in i.message for i in mf.errors(validate_corpus(corpus)))


# -- command line ---------------------------------------------------------------------------------------------------------------

def _ingest_argv(root, src, **over):
    flags = {"clip-id": "POS_S01_0001", "label-class": "positive", "session-id": "SES01",
             "recorded-date": "2026-09-21", "recording-source": "laptop_builtin", "hardware-track": "A",
             "room-id": "R1", "environment": "living_room", "condition": "quiet", "speaker-id": "S01",
             "distance-m": "2", "speaking-style": "normal", "consent-ref": "CONSENT-S01"}
    flags.update({k.replace("_", "-"): v for k, v in over.items()})
    argv = ["--data-dir", str(root), "ingest", str(src)]
    for k, v in flags.items():
        argv += [f"--{k}", v]
    return argv


def test_command_line_workflow_end_to_end(tmp_path, capsys):
    root = tmp_path / "data"
    src = make_src(tmp_path, channels=2)
    assert ing.main(["--data-dir", str(root), "init"]) == 0
    assert ing.main(_ingest_argv(root, src)) == 2                        # stereo needs --convert
    assert "--convert" in capsys.readouterr().err
    assert ing.main(_ingest_argv(root, src) + ["--convert"]) == 0
    out = capsys.readouterr().out
    assert "PROPOSED phrase" in out and "annotation_status=pending" in out

    assert ing.main(["--data-dir", str(root), "validate"]) == 0
    assert ing.main(["--data-dir", str(root), "validate", "--require-verified"]) == 1
    assert ing.main(["--data-dir", str(root), "annotate", "--clip-id", "POS_S01_0001",
                     "--annotator", "A01", "--accept-proposal"]) == 0
    capsys.readouterr()
    assert ing.main(["--data-dir", str(root), "validate", "--require-verified"]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_command_line_refuses_a_repository_path(capsys):
    assert ing.main(["--data-dir", str(REPO_ROOT / "data"), "init"]) == 2
    assert "inside the repository" in capsys.readouterr().err
    assert not (REPO_ROOT / "data").exists()
