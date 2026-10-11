"""Ingest, annotate and validate corpus recordings (VESTA WP2.4).

    python -m evaluation.ingest init
    python -m evaluation.ingest ingest  <file.wav> --clip-id ... [options]
    python -m evaluation.ingest annotate --clip-id ... (--accept-proposal | --start-s .. --end-s ..) --annotator A01
    python -m evaluation.ingest validate [--require-verified]

Safety rules this tool enforces:
  * the ORIGINAL file is only read. A byte-identical copy goes into
    <corpus>/raw/ (read-only, hash-verified); the evaluation copy is a new
    16 kHz mono int16 WAV in <corpus>/processed/;
  * nothing is converted unless --convert is given, and multi-channel files
    whose channels differ are REJECTED (never silently downmixed) - choose a
    channel explicitly with --select-channel;
  * existing corpus files are never overwritten;
  * the corpus directory must be OUTSIDE this repository, so recordings
    cannot be committed by accident;
  * nothing is uploaded anywhere; everything is local file I/O.
"""

import argparse
import io
import os
import re
import shutil
import stat
import sys
import wave
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from evaluation import manifest as mf
from evaluation.audio_analysis import (
    BoundaryProposal,
    QualityMetrics,
    estimate_snr_db,
    propose_wake_boundaries,
    quality_metrics,
)
from evaluation.resample import resample

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_VAR = "VESTA_DATA_DIR"
DEFAULT_DATA_ROOT = Path.home() / "VESTA-data"
CORPUS_DIRNAME = "wakeword_corpus"
MANIFEST_NAME = f"manifest_{mf.SPLIT_VERSION}.csv"

# Manifest columns a caller may supply; everything else is computed by the tool.
METADATA_KEYS = (
    "clip_id", "label_class", "synthetic_method", "data_origin", "consent_ref",
    "session_id", "recorded_date", "recording_source", "device_id", "hardware_track",
    "gain_setting", "room_id", "environment", "condition", "noise_type", "noise_ref_id",
    "speaker_id", "distance_m", "speaking_style", "take_number", "prompt_text",
    "take_valid", "invalid_reason", "notes", "split", "split_version",
)


class IngestError(ValueError):
    """The input or the requested operation was rejected; nothing was written."""


@dataclass
class IngestResult:
    row: Dict[str, str]
    quality: QualityMetrics
    proposal: Optional[BoundaryProposal]
    messages: List[str] = field(default_factory=list)


# -- locations ----------------------------------------------------------------

def resolve_data_root(cli_value: Optional[str] = None) -> Path:
    """--data-dir, else $VESTA_DATA_DIR, else ~/VESTA-data. Never inside the repo."""
    raw = cli_value or os.environ.get(ENV_VAR) or str(DEFAULT_DATA_ROOT)
    root = Path(raw).expanduser().resolve()
    assert_outside_repo(root)
    return root


def assert_outside_repo(path: Path) -> None:
    p = Path(path).expanduser().resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise IngestError(
            f"{p} is inside the repository ({REPO_ROOT}). Voice recordings are personal "
            "data and must be stored outside it."
        )


def sync_warning(path: Path) -> Optional[str]:
    if "onedrive" in str(path).lower() or "dropbox" in str(path).lower():
        return (f"{path} looks like a cloud-synced folder. Recordings would be uploaded by the sync "
                "client; choose a non-synced location unless the team has agreed to that.")
    return None


_CORPUS_README = """VESTA wake-word evaluation corpus (personal data: voice recordings)

This directory is OUTSIDE the source repository on purpose. Do not copy it into the
repository, upload it to any cloud/transcription/annotation service, or share it
without the consent terms agreed with the speakers.

  raw/<session_id>/     original recordings, read-only, never edited
  processed/<label>/    16 kHz mono int16 evaluation copies, read-only after ingest
  processed/synthetic/  SYNTHETIC audio only - never mixed with real data
  annotations/          label files exported from the annotation tool
  manifests/            manifest_v1.csv (one row per clip)
  noise_refs/           noise-only reference recordings per session
  results/              benchmark outputs (metrics only, no audio)

Speakers are pseudonyms (S01..S05). The key mapping pseudonyms to people and the
signed consent forms live in ../private/, never in this directory or the repository.
Tool: python -m evaluation.ingest --help
"""

_PRIVATE_README = """VESTA private data (consent forms and speaker key) - NEVER in the repository.

  consent/           signed consent forms (scans or notes), one per speaker
  speaker_key.csv    pseudonym -> person mapping (create it yourself; no tool reads it)

Keep this directory separate from the audio corpus and restrict access to the
people who need it. Delete it according to the retention period agreed with the speakers.
"""


def init_corpus(root: Path) -> Path:
    """Create the corpus directory tree (idempotent). Returns the corpus directory."""
    assert_outside_repo(root)
    corpus = Path(root) / CORPUS_DIRNAME
    for label in mf.LABEL_CLASSES:
        (corpus / "processed" / label).mkdir(parents=True, exist_ok=True)
        (corpus / "processed" / "synthetic" / label).mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "annotations", "manifests", "noise_refs", "results"):
        (corpus / sub).mkdir(parents=True, exist_ok=True)
    private = Path(root) / "private"
    (private / "consent").mkdir(parents=True, exist_ok=True)

    for path, text in ((corpus / "README.txt", _CORPUS_README), (private / "README.txt", _PRIVATE_README)):
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    manifest = corpus / "manifests" / MANIFEST_NAME
    if not manifest.exists():
        mf.write_manifest(manifest, [])
    return corpus


def _require_corpus(corpus: Path) -> Path:
    corpus = Path(corpus)
    assert_outside_repo(corpus)
    if not (corpus / "manifests" / MANIFEST_NAME).is_file():
        raise IngestError(f"{corpus} is not an initialised corpus; run 'python -m evaluation.ingest init' first")
    return corpus


# -- reading and converting audio -----------------------------------------------

@dataclass
class RawAudio:
    data: np.ndarray          # shape (n, channels), native integer values
    sample_rate: int
    sample_width: int         # bytes per sample
    channels: int


def read_wav(path: Path) -> RawAudio:
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise IngestError(f"{path.name}: not a readable PCM WAV file ({exc}). "
                          "Float or compressed WAVs and other formats are not accepted.") from exc
    if frames == 0:
        raise IngestError(f"{path.name}: the file contains no audio")
    if width == 1:
        data = np.frombuffer(raw, np.uint8).astype(np.int32) - 128
    elif width == 2:
        data = np.frombuffer(raw, "<i2").astype(np.int16)
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        data = np.where(v & 0x800000, v - 0x1000000, v)
    elif width == 4:
        data = np.frombuffer(raw, "<i4").astype(np.int32)
    else:
        raise IngestError(f"{path.name}: unsupported sample width {width} bytes")
    return RawAudio(data.reshape(-1, channels), rate, width, channels)


def _to_int16(mono: np.ndarray, width: int) -> np.ndarray:
    if width == 2:
        return mono.astype(np.int16)
    if width == 1:
        v = mono.astype(np.int64) * 256
    elif width == 3:
        v = (mono.astype(np.int64) + 128) >> 8
    else:
        v = (mono.astype(np.int64) + 32768) >> 16
    return np.clip(v, -32768, 32767).astype(np.int16)


def to_processed(audio: RawAudio, *, convert: bool, select_channel: Optional[int]) -> Tuple[np.ndarray, List[str]]:
    """Return (16 kHz mono int16 samples, list of applied conversion steps).

    Raises IngestError when a needed conversion was not requested, or when
    channels differ and no channel was selected.
    """
    steps: List[str] = []
    needs: List[str] = []
    data, channels = audio.data, audio.channels

    if select_channel is not None and not 0 <= select_channel < channels:
        raise IngestError(f"--select-channel {select_channel} is out of range for {channels} channel(s)")

    if channels > 1 and select_channel is None:
        identical = all(np.array_equal(data[:, 0], data[:, c]) for c in range(1, channels))
        if not identical:
            raise IngestError(
                f"the file has {channels} channels that are NOT identical; refusing to downmix. "
                "Pick one explicitly with --select-channel N (0-based)."
            )
        needs.append(f"keep channel 0 of {channels} identical channels")
    if audio.sample_width != 2:
        needs.append(f"convert {8 * audio.sample_width}-bit PCM to 16-bit")
    if audio.sample_rate != mf.SAMPLE_RATE:
        needs.append(f"resample {audio.sample_rate} Hz to {mf.SAMPLE_RATE} Hz")
    if needs and not convert:
        raise IngestError("conversion needed but not requested: " + "; ".join(needs) + ". Re-run with --convert.")

    if channels > 1:
        col = 0 if select_channel is None else select_channel
        steps.append(f"channel_{col}_of_{channels}" + ("_identical" if select_channel is None else "_selected"))
    elif select_channel is not None:
        steps.append("channel_0_of_1_selected")
    mono = data[:, 0 if select_channel is None else select_channel]

    x = _to_int16(mono, audio.sample_width)
    if audio.sample_width != 2:
        steps.append(f"pcm{8 * audio.sample_width}_to_int16")
    if audio.sample_rate != mf.SAMPLE_RATE:
        y = resample(x.astype(np.float64), audio.sample_rate, mf.SAMPLE_RATE)
        x = np.clip(np.rint(y), -32768, 32767).astype(np.int16)
        steps.append(f"resample_{audio.sample_rate}_to_{mf.SAMPLE_RATE}_polyphase_numpy")
    return x, steps


def _wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(mf.SAMPLE_RATE)
        w.writeframes(x.astype("<i2").tobytes())
    return buf.getvalue()


def _sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


# -- manifest helpers --------------------------------------------------------------

def _rel(path: Path, corpus: Path) -> str:
    return path.relative_to(corpus).as_posix()


def _unlink_force(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()
    except OSError:
        pass


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _fmt(value: float, digits: int) -> str:
    return f"{value:.{digits}f}"


def _apply_proposal(row: Dict[str, str], proposal: Optional[BoundaryProposal], messages: List[str]) -> None:
    row["annotation_status"] = "pending"
    if proposal is None:
        row["notes"] = _join_notes(row.get("notes", ""), "no_boundary_proposal")
        messages.append("no phrase found by the energy detector; annotate manually")
        return
    if proposal.start_s < mf.MIN_LEAD_IN_S:
        row["notes"] = _join_notes(row.get("notes", ""), "proposal_discarded:starts_before_0.5s")
        messages.append(
            f"proposed phrase start {proposal.start_s:.2f}s is inside the {mf.MIN_LEAD_IN_S}s minimum lead-in; "
            "proposal discarded - check the recording, or ingest it with take_valid=0 and an invalid_reason"
        )
        return
    row["wake_start_sample"] = str(proposal.start_sample)
    row["wake_end_sample"] = str(proposal.end_sample)
    row["wake_start_s"] = _fmt(proposal.start_sample / mf.SAMPLE_RATE, 3)
    row["wake_end_s"] = _fmt(proposal.end_sample / mf.SAMPLE_RATE, 3)
    row["annotation_method"] = "auto_proposed"
    if proposal.warnings:
        row["notes"] = _join_notes(row.get("notes", ""), "proposal_warnings:" + "|".join(proposal.warnings))
        messages.extend(f"proposal warning: {w}" for w in proposal.warnings)


def _join_notes(existing: str, extra: str) -> str:
    return f"{existing}; {extra}" if existing else extra


# -- ingest ------------------------------------------------------------------------

def ingest_recording(
    input_path: Path,
    corpus_dir: Path,
    meta: Mapping[str, str],
    *,
    synthetic: bool = False,
    convert: bool = False,
    select_channel: Optional[int] = None,
) -> IngestResult:
    """Add one recording to the corpus. Writes nothing unless every check passes."""
    corpus = _require_corpus(corpus_dir)
    src = Path(input_path)
    if not src.is_file():
        raise IngestError(f"input file not found: {src}")
    unknown = set(meta) - set(METADATA_KEYS)
    if unknown:
        raise IngestError(f"unknown or tool-computed metadata keys: {sorted(unknown)}")

    audio = read_wav(src)
    x, steps = to_processed(audio, convert=convert, select_channel=select_channel)
    processed_bytes = _wav_bytes(x)

    row: Dict[str, str] = {c: "" for c in mf.COLUMNS}
    row.update({k: str(v) for k, v in meta.items() if v is not None})
    label = row["label_class"]
    row["is_synthetic"] = "1" if synthetic else "0"
    if synthetic:
        for key, default in (("data_origin", "synthetic"), ("consent_ref", "na"), ("hardware_track", "NA"),
                             ("room_id", "na"), ("environment", "na"), ("session_id", "synthetic"),
                             ("recording_source", "synthetic_generator"),
                             ("recorded_date", date.today().isoformat())):
            row[key] = row[key] or default
    row["data_origin"] = row["data_origin"] or "team_recorded"
    row["split_version"] = row["split_version"] or mf.SPLIT_VERSION
    if label != "positive":
        row["condition"] = row["condition"] or "na"
        row["speaking_style"] = row["speaking_style"] or "na"
    else:
        row["prompt_text"] = row["prompt_text"] or mf.TARGET_PHRASE
        row["take_valid"] = row["take_valid"] or "1"
    row["split"] = row["split"] or (mf.expected_split(row) or "")
    if not row["split"] and not row["speaker_id"]:
        raise IngestError(
            "no evaluation split can be derived for this clip because it has no speaker; "
            "pass an explicit --split dev, test or excluded (assigned per recording session)"
        )

    quality = quality_metrics(x, mf.SAMPLE_RATE)
    messages: List[str] = []
    proposal: Optional[BoundaryProposal] = None
    if label == "positive" and row["take_valid"] == "1":
        proposal = propose_wake_boundaries(x, mf.SAMPLE_RATE)
        _apply_proposal(row, proposal, messages)
        if proposal is not None and row["annotation_method"]:
            snr = estimate_snr_db(x, proposal, mf.SAMPLE_RATE)
            row["est_snr_db"] = "" if snr is None else _fmt(snr, 1)
    elif label != "positive":
        row["annotation_status"] = "not_applicable"
    else:
        row["annotation_status"] = "not_applicable"      # invalid take: kept, never annotated

    clip_id = row["clip_id"]
    row.update({
        "sha256": _sha256_bytes(processed_bytes),
        "raw_sha256": mf.sha256_file(src),
        "sample_rate": str(mf.SAMPLE_RATE), "channels": "1", "bit_depth": "16",
        "duration_s": _fmt(len(x) / mf.SAMPLE_RATE, 3),
        "orig_format": f"{audio.sample_rate}Hz/{audio.channels}ch/{8 * audio.sample_width}bit",
        "processing": ";".join(steps) if steps else "none",
        "peak_dbfs": _fmt(quality.peak_dbfs, 1),
        "noise_floor_dbfs": _fmt(quality.noise_floor_dbfs, 1),
        "clipping_fraction": _fmt(quality.clipping_fraction, 6),
    })
    session = row["session_id"]
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$", session or ""):
        raise IngestError("session_id must be letters, digits, '_' or '-' (it becomes a directory name)")
    raw_dest = corpus / "raw" / session / _safe_filename(src.name)
    proc_dir = corpus / "processed" / ("synthetic" if synthetic else "") / label
    proc_dest = proc_dir / f"{clip_id}.wav"
    row["raw_path"] = _rel(raw_dest, corpus)
    row["file_path"] = _rel(proc_dest, corpus)

    # Validate everything BEFORE touching the disk.
    manifest_path = corpus / "manifests" / MANIFEST_NAME
    existing = mf.read_manifest(manifest_path)
    problems = [i for i in mf.errors(mf.validate_manifest(existing + [row])) if i.clip_id == clip_id]
    if problems:
        raise IngestError("rejected, nothing written:\n  " + "\n  ".join(i.message for i in problems))
    for path in (raw_dest, proc_dest):
        if path.exists():
            raise IngestError(f"refusing to overwrite existing corpus file: {path}")

    created: List[Path] = []
    try:
        raw_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, raw_dest)
        created.append(raw_dest)
        if mf.sha256_file(raw_dest) != row["raw_sha256"]:
            raise IngestError("raw copy does not match the original (hash mismatch); aborted")
        _make_read_only(raw_dest)
        proc_dir.mkdir(parents=True, exist_ok=True)
        proc_dest.write_bytes(processed_bytes)
        created.append(proc_dest)
        _make_read_only(proc_dest)
        mf.write_manifest(manifest_path, existing + [row])
    except BaseException:
        for path in created:
            _unlink_force(path)
        raise
    return IngestResult(row, quality, proposal, messages)


def annotate_clip(
    corpus_dir: Path,
    clip_id: str,
    *,
    annotator_id: str,
    start_sample: Optional[int] = None,
    end_sample: Optional[int] = None,
    accept_proposal: bool = False,
    invalid_reason: Optional[str] = None,
) -> Dict[str, str]:
    """Record a human verification (or invalidate a take) for an existing clip."""
    corpus = _require_corpus(corpus_dir)
    manifest_path = corpus / "manifests" / MANIFEST_NAME
    rows = mf.read_manifest(manifest_path)
    matches = [r for r in rows if r["clip_id"] == clip_id]
    if not matches:
        raise IngestError(f"clip_id {clip_id!r} not found in the manifest")
    row = matches[0]
    if row["label_class"] != "positive":
        raise IngestError("only positive clips carry wake-word annotations")
    if not annotator_id.strip():
        raise IngestError("annotator_id is required")

    before = dict(row)
    if invalid_reason:
        row.update({"take_valid": "0", "invalid_reason": invalid_reason, "annotation_status": "not_applicable",
                    "wake_start_sample": "", "wake_end_sample": "", "wake_start_s": "", "wake_end_s": "",
                    "annotation_method": "", "annotator_id": annotator_id, "est_snr_db": ""})
    else:
        if row["take_valid"] != "1":
            raise IngestError("this take is marked invalid; it cannot be annotated")
        if accept_proposal:
            if row["annotation_method"] != "auto_proposed":
                raise IngestError("this clip has no automatic proposal to accept")
            row.update({"annotation_method": "auto_verified"})
        else:
            if start_sample is None or end_sample is None:
                raise IngestError("give --start-s/--end-s (or samples), or --accept-proposal")
            row.update({
                "wake_start_sample": str(start_sample), "wake_end_sample": str(end_sample),
                "wake_start_s": _fmt(start_sample / mf.SAMPLE_RATE, 3),
                "wake_end_s": _fmt(end_sample / mf.SAMPLE_RATE, 3),
                "annotation_method": "manual",
            })
        row.update({"annotation_status": "verified", "annotator_id": annotator_id})

    issues = [i for i in mf.errors(mf.validate_manifest([row])) if i.clip_id == clip_id]
    if issues:
        row.clear()
        row.update(before)
        raise IngestError("rejected, manifest unchanged:\n  " + "\n  ".join(i.message for i in issues))
    mf.write_manifest(manifest_path, rows)
    return row


def validate_corpus(corpus_dir: Path, *, require_verified: bool = False) -> List[mf.Issue]:
    corpus = _require_corpus(corpus_dir)
    rows = mf.read_manifest(corpus / "manifests" / MANIFEST_NAME)
    return mf.validate_manifest(rows, corpus, require_verified=require_verified)


# -- command line ----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evaluation.ingest", description=__doc__.split("\n\n")[0])
    p.add_argument("--data-dir", help=f"corpus parent directory (default: ${ENV_VAR} or ~/VESTA-data)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the corpus directory tree (outside the repository)")

    ing = sub.add_parser("ingest", help="add one WAV recording")
    ing.add_argument("input", help="WAV file to ingest (never modified)")
    ing.add_argument("--convert", action="store_true", help="allow needed conversions (bit depth, rate, identical channels)")
    ing.add_argument("--select-channel", type=int, help="explicitly choose channel N (0-based) of a multi-channel file")
    ing.add_argument("--synthetic", action="store_true", help="mark as SYNTHETIC data (clip-id must start with SYN_)")
    for key in METADATA_KEYS:
        ing.add_argument("--" + key.replace("_", "-"), dest=key, default=None)

    ann = sub.add_parser("annotate", help="record a human-verified annotation, or invalidate a take")
    ann.add_argument("--clip-id", required=True)
    ann.add_argument("--annotator", required=True, help="annotator pseudonym, e.g. A01")
    ann.add_argument("--accept-proposal", action="store_true")
    ann.add_argument("--start-s", type=float)
    ann.add_argument("--end-s", type=float)
    ann.add_argument("--invalid-reason")

    val = sub.add_parser("validate", help="check the manifest and files")
    val.add_argument("--require-verified", action="store_true", help="treat pending annotations as errors")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        root = resolve_data_root(args.data_dir)
        corpus = root / CORPUS_DIRNAME
        note = sync_warning(root)
        if note:
            print(f"WARNING: {note}", file=sys.stderr)

        if args.command == "init":
            init_corpus(root)
            print(f"corpus:  {corpus}\nprivate: {root / 'private'}  (consent forms and speaker key go here, never in the repo)")
            return 0

        if args.command == "ingest":
            meta = {k: getattr(args, k) for k in METADATA_KEYS if getattr(args, k) is not None}
            result = ingest_recording(Path(args.input), corpus, meta, synthetic=args.synthetic,
                                      convert=args.convert, select_channel=args.select_channel)
            r = result.row
            print(f"ingested {r['clip_id']}: {r['orig_format']} -> 16000Hz/1ch/16bit, processing={r['processing']}")
            print(f"  raw       {r['raw_path']}  sha256={r['raw_sha256'][:16]}...")
            print(f"  processed {r['file_path']}  sha256={r['sha256'][:16]}...  duration={r['duration_s']}s")
            print(f"  quality   peak={r['peak_dbfs']} dBFS  noise_floor={r['noise_floor_dbfs']} dBFS  "
                  f"clipping={r['clipping_fraction']}  est_snr={r['est_snr_db'] or 'n/a'} dB")
            if r["annotation_method"] == "auto_proposed":
                print(f"  PROPOSED phrase {r['wake_start_s']}s - {r['wake_end_s']}s (NOT verified; annotation_status=pending)")
            for m in result.messages:
                print(f"  note: {m}")
            return 0

        if args.command == "annotate":
            to_sample = lambda s: None if s is None else int(round(s * mf.SAMPLE_RATE))
            row = annotate_clip(corpus, args.clip_id, annotator_id=args.annotator,
                                start_sample=to_sample(args.start_s), end_sample=to_sample(args.end_s),
                                accept_proposal=args.accept_proposal, invalid_reason=args.invalid_reason)
            print(f"{row['clip_id']}: status={row['annotation_status']} method={row['annotation_method'] or '-'} "
                  f"phrase={row['wake_start_s'] or '-'}s..{row['wake_end_s'] or '-'}s")
            return 0

        issues = validate_corpus(corpus, require_verified=args.require_verified)
        for issue in issues:
            print(issue)
        n_err = len(mf.errors(issues))
        print(f"{len(issues) - n_err} warning(s), {n_err} error(s)")
        return 1 if n_err else 0
    except (IngestError, mf.ManifestError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
