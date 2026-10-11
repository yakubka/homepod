"""Corpus manifest schema, I/O and validation (WP2.4).

One CSV row per audio file (positive, negative, or synthetic). The manifest
records everything needed to reproduce a benchmark run: which file, who
(pseudonym only), how it was recorded, where the wake phrase is, and which
evaluation group it belongs to.

Conventions
  * paths are relative to the corpus directory, with forward slashes;
  * booleans are "1"/"0"; blank means "not set"; "na" means "not applicable";
  * speaker IDs are pseudonyms (S01..S05). Real identities are NEVER stored
    here or anywhere in the repository;
  * wake_start_sample / wake_end_sample are the canonical annotation units
    (16 kHz samples, end exclusive); the *_s columns are derived.
"""

import csv
import hashlib
import os
import re
import tempfile
import wave
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

SAMPLE_RATE = 16000

COLUMNS = (
    # identity / provenance
    "clip_id", "file_path", "sha256", "raw_sha256", "label_class", "is_synthetic",
    "synthetic_method", "data_origin", "consent_ref",
    # audio
    "sample_rate", "channels", "bit_depth", "duration_s", "orig_format", "processing", "raw_path",
    # recording
    "session_id", "recorded_date", "recording_source", "device_id", "hardware_track",
    "gain_setting", "room_id", "environment", "condition", "noise_type", "noise_ref_id",
    # speaker / prompt
    "speaker_id", "distance_m", "speaking_style", "take_number", "prompt_text",
    "take_valid", "invalid_reason",
    # annotation
    "wake_start_sample", "wake_end_sample", "wake_start_s", "wake_end_s",
    "annotation_method", "annotator_id", "annotation_status",
    # quality
    "peak_dbfs", "noise_floor_dbfs", "clipping_fraction", "est_snr_db", "notes",
    # evaluation group
    "split", "split_version",
)

# Approved speaker-independent split (WP2.4). A speaker is in exactly one group.
SPEAKER_SPLIT: Dict[str, str] = {
    "S01": "dev", "S02": "dev",
    "S03": "test", "S04": "test", "S05": "test",
}
SPLIT_VERSION = "v1"

LABEL_CLASSES = ("positive", "negative_confuser", "negative_speech", "negative_media", "negative_ambient")
DATA_ORIGINS = ("team_recorded", "public_corpus", "synthetic")
HARDWARE_TRACKS = ("A", "B", "NA")
CONDITIONS = ("quiet", "moderate_noise", "na")
STYLES = ("normal", "soft", "raised", "fast_casual", "na")
ANNOTATION_STATUSES = ("pending", "verified", "not_applicable")
ANNOTATION_METHODS = ("", "auto_proposed", "manual", "auto_verified")
SPLITS = ("dev", "test", "synthetic", "excluded")
NOMINAL_DISTANCES_M = (0.5, 2.0, 3.0, 4.0)
TARGET_PHRASE = "hey jarvis"
MIN_LEAD_IN_S = 0.5      # hard minimum: engine warm-up is 0.4 s
RECOMMENDED_LEAD_IN_S = 1.0

_REQUIRED_ALWAYS = (
    "clip_id", "file_path", "sha256", "raw_sha256", "raw_path", "label_class", "is_synthetic",
    "data_origin", "consent_ref", "sample_rate", "channels", "bit_depth", "duration_s",
    "session_id", "recorded_date", "recording_source", "hardware_track", "room_id",
    "environment", "condition", "split", "split_version",
)
_SPEAKER_RE = re.compile(r"^S\d{2}(\+S\d{2})*$")
_CLIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """The manifest file itself is unreadable or has the wrong columns."""


@dataclass(frozen=True)
class Issue:
    severity: str        # "error" | "warning"
    clip_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity.upper():7s} {self.clip_id or '-':32s} {self.message}"


def errors(issues: Iterable[Issue]) -> List[Issue]:
    return [i for i in issues if i.severity == "error"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def speakers_of(speaker_id: str) -> List[str]:
    return [s for s in speaker_id.split("+") if s] if speaker_id else []


def expected_split(row: Mapping[str, str]) -> Optional[str]:
    """Split implied by the row itself, or None if it must be assigned explicitly.

    Synthetic rows are always "synthetic". Team-recorded rows with speaker(s)
    take the speaker's group (all speakers must agree). Media/ambient/public
    rows have no speaker and are assigned by session.
    """
    if _get(row, "is_synthetic") == "1":
        return "synthetic"
    speakers = speakers_of(_get(row, "speaker_id"))
    groups = {SPEAKER_SPLIT.get(s) for s in speakers}
    if len(groups) == 1 and None not in groups:
        return groups.pop()
    return None


# -- I/O --------------------------------------------------------------------

def read_manifest(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ManifestError(
                f"{path}: header does not match the manifest schema "
                f"(expected {len(COLUMNS)} columns starting with {COLUMNS[:3]})"
            )
        return [dict(r) for r in reader]


def write_manifest(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    """Atomically replace the manifest (write a temp file, then os.replace)."""
    path = Path(path)
    for row in rows:
        extra = set(row) - set(COLUMNS)
        if extra:
            raise ManifestError(f"unknown manifest columns: {sorted(extra)}")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in COLUMNS})
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# -- validation ---------------------------------------------------------------

def _get(row: Mapping[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _num(value: str, cast):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _row_issues(row: Mapping[str, str], require_verified: bool) -> List[Issue]:
    cid = _get(row, "clip_id")
    out: List[Issue] = []

    def err(msg: str) -> None:
        out.append(Issue("error", cid, msg))

    def warn(msg: str) -> None:
        out.append(Issue("warning", cid, msg))

    extra = set(row) - set(COLUMNS)
    if extra:
        err(f"unknown columns {sorted(extra)}")
    for col in _REQUIRED_ALWAYS:
        if not _get(row, col):
            err(f"required column '{col}' is blank")
    if out:                       # blanks make the rest of the checks noisy
        return out

    if not _CLIP_ID_RE.match(cid):
        err("clip_id must be letters, digits, '_' or '-'")
    if not _CLIP_ID_RE.match(_get(row, "session_id")):
        err("session_id must be letters, digits, '_' or '-' (it is used as a directory name)")
    if not _SHA_RE.match(_get(row, "sha256")):
        err("sha256 must be 64 lowercase hex characters")
    if not _SHA_RE.match(_get(row, "raw_sha256")):
        err("raw_sha256 must be 64 lowercase hex characters")
    for col in ("file_path", "raw_path"):
        p = _get(row, col)
        if p.startswith(("/", "\\")) or ":" in p or ".." in Path(p).parts or "\\" in p:
            err(f"{col} must be a relative forward-slash path inside the corpus")

    label = _get(row, "label_class")
    if label not in LABEL_CLASSES:
        err(f"label_class must be one of {LABEL_CLASSES}")
    for col, allowed in (
        ("data_origin", DATA_ORIGINS), ("hardware_track", HARDWARE_TRACKS),
        ("condition", CONDITIONS), ("split", SPLITS),
    ):
        if _get(row, col) not in allowed:
            err(f"{col} must be one of {allowed}")
    style = _get(row, "speaking_style")
    if style and style not in STYLES:
        err(f"speaking_style must be one of {STYLES}")
    status = _get(row, "annotation_status")
    if status not in ANNOTATION_STATUSES:
        err(f"annotation_status must be one of {ANNOTATION_STATUSES}")
    if _get(row, "annotation_method") not in ANNOTATION_METHODS:
        err(f"annotation_method must be one of {ANNOTATION_METHODS}")
    try:
        date.fromisoformat(_get(row, "recorded_date"))
    except ValueError:
        err("recorded_date must be an ISO date (YYYY-MM-DD)")

    if _get(row, "sample_rate") != str(SAMPLE_RATE):
        err(f"sample_rate must be {SAMPLE_RATE} (processed files only)")
    if _get(row, "channels") != "1":
        err("channels must be 1 (processed files only)")
    if _get(row, "bit_depth") != "16":
        err("bit_depth must be 16 (processed files only)")
    duration = _num(_get(row, "duration_s"), float)
    if duration is None or duration <= 0:
        err("duration_s must be a positive number")
    elif duration < 2.0 and label == "positive":
        warn("positive clip shorter than 2 s; protocol asks for about 4 s")

    # -- synthetic vs real must stay completely separate --
    synthetic = _get(row, "is_synthetic")
    if synthetic not in ("0", "1"):
        err("is_synthetic must be 0 or 1")
    else:
        in_synth_dir = _get(row, "file_path").startswith("processed/synthetic/")
        origin = _get(row, "data_origin")
        if synthetic == "1":
            if origin != "synthetic":
                err("synthetic rows must have data_origin=synthetic")
            if _get(row, "split") != "synthetic":
                err("synthetic rows must have split=synthetic")
            if not cid.startswith("SYN_"):
                err("synthetic clip_id must start with 'SYN_'")
            if not in_synth_dir:
                err("synthetic files must live under processed/synthetic/")
            if not _get(row, "synthetic_method"):
                err("synthetic rows need a synthetic_method")
        else:
            if origin == "synthetic":
                err("real rows must not have data_origin=synthetic")
            if _get(row, "split") == "synthetic":
                err("real rows must not have split=synthetic")
            if cid.startswith("SYN_"):
                err("'SYN_' clip_id prefix is reserved for synthetic data")
            if in_synth_dir:
                err("real files must not live under processed/synthetic/")

    # -- speaker / split --
    speaker = _get(row, "speaker_id")
    if speaker and not _SPEAKER_RE.match(speaker):
        err("speaker_id must be a pseudonym like S01 (or S01+S02 for conversations)")
    if label in ("positive", "negative_confuser", "negative_speech") and synthetic == "0" and not speaker:
        err(f"speaker_id is required for real {label} clips")
    implied = expected_split(row)
    if implied is not None and _get(row, "split") != implied:
        err(f"split is '{_get(row, 'split')}' but the speaker/synthetic status implies '{implied}'")
    for s in speakers_of(speaker):
        if s not in SPEAKER_SPLIT:
            err(f"speaker {s} is not in the approved speaker split table")
    if len({SPEAKER_SPLIT.get(s) for s in speakers_of(speaker)}) > 1:
        err("a multi-speaker clip mixes development and test speakers")
    if synthetic == "0" and not speaker and _get(row, "split") not in ("dev", "test", "excluded"):
        err("clips without a speaker need an explicit split (dev/test/excluded)")

    # -- take validity / annotation --
    valid = _get(row, "take_valid")
    start_s, end_s = _get(row, "wake_start_sample"), _get(row, "wake_end_sample")
    if label != "positive":
        if start_s or end_s or _get(row, "wake_start_s") or _get(row, "wake_end_s"):
            err("only positive clips may have wake-word boundaries")
        if status != "not_applicable":
            err("non-positive clips must have annotation_status=not_applicable")
        return out

    # positives from here on
    if _get(row, "prompt_text").lower() != TARGET_PHRASE:
        err(f"positive prompt_text must be '{TARGET_PHRASE}'")
    if valid not in ("0", "1"):
        err("take_valid must be 0 or 1 for positives")
        return out
    if valid == "0":
        if not _get(row, "invalid_reason"):
            err("invalid takes need an invalid_reason")
        return out
    if _get(row, "condition") == "na" or style in ("", "na"):
        err("valid positives need condition (quiet/moderate_noise) and speaking_style")
    distance = _num(_get(row, "distance_m"), float)
    if distance is None or distance <= 0:
        err("valid positives need a positive distance_m")
    elif distance not in NOMINAL_DISTANCES_M:
        warn(f"distance_m {distance} is not one of the nominal {NOMINAL_DISTANCES_M}")
    if status == "not_applicable":
        err("positive clips cannot have annotation_status=not_applicable")
    elif status == "pending":
        (err if require_verified else warn)("annotation is still pending human verification")

    if start_s or end_s or status == "verified":
        a, b = _num(start_s, int), _num(end_s, int)
        if a is None or b is None:
            err("wake_start_sample and wake_end_sample must be integers")
        else:
            n = round(duration * SAMPLE_RATE) if duration else 0
            if not 0 <= a < b:
                err("wake boundaries must satisfy 0 <= start < end")
            if n and b > n + SAMPLE_RATE // 100:
                err("wake_end_sample lies beyond the end of the clip")
            if a / SAMPLE_RATE < MIN_LEAD_IN_S:
                err(f"wake phrase starts at {a / SAMPLE_RATE:.3f}s, before the {MIN_LEAD_IN_S}s minimum lead-in "
                    "(engine warm-up); re-record with more leading silence")
            elif a / SAMPLE_RATE < RECOMMENDED_LEAD_IN_S:
                warn(f"lead-in {a / SAMPLE_RATE:.2f}s is under the recommended {RECOMMENDED_LEAD_IN_S}s")
            for col, samples in (("wake_start_s", a), ("wake_end_s", b)):
                derived = _num(_get(row, col), float)
                if derived is None or abs(derived - samples / SAMPLE_RATE) > 0.001:
                    err(f"{col} does not match its sample column")
    if status == "verified" and not _get(row, "annotator_id"):
        err("verified annotations need an annotator_id")
    clip = _num(_get(row, "clipping_fraction"), float)
    if clip is not None and clip > 0.001:
        warn(f"{clip:.3%} of samples are clipped")
    return out


def validate_manifest(
    rows: Sequence[Mapping[str, str]],
    corpus_dir: Optional[Path] = None,
    *,
    require_verified: bool = False,
) -> List[Issue]:
    """Check every row, cross-row rules and (if corpus_dir is given) the files.

    require_verified=True turns "annotation pending" into an error; the
    WP2.5 benchmark should validate that way.
    """
    issues: List[Issue] = []
    for row in rows:
        issues.extend(_row_issues(row, require_verified))

    seen: Dict[str, str] = {}
    for col in ("clip_id", "raw_sha256"):
        seen.clear()
        for row in rows:
            v = _get(row, col)
            if v and v in seen:
                issues.append(Issue("error", _get(row, "clip_id"), f"duplicate {col} (also {seen[v]})"))
            seen.setdefault(v, _get(row, "clip_id"))

    groups: Dict[str, set] = {}
    for row in rows:
        if _get(row, "is_synthetic") == "1":
            continue
        for s in speakers_of(_get(row, "speaker_id")):
            groups.setdefault(s, set()).add(_get(row, "split"))
    for s, splits in sorted(groups.items()):
        if len(splits - {"excluded"}) > 1:
            issues.append(Issue("error", "", f"speaker {s} appears in more than one evaluation group: {sorted(splits)}"))

    if corpus_dir is not None:
        for row in rows:
            issues.extend(_file_issues(row, Path(corpus_dir)))
    return issues


def _file_issues(row: Mapping[str, str], corpus_dir: Path) -> List[Issue]:
    cid = _get(row, "clip_id")
    out: List[Issue] = []
    root = corpus_dir.resolve()
    for col, hash_col in (("file_path", "sha256"), ("raw_path", "raw_sha256")):
        rel = _get(row, col)
        if not rel or ".." in Path(rel).parts:
            continue                                  # already reported by row checks
        path = (root / rel).resolve()
        if root not in path.parents:
            out.append(Issue("error", cid, f"{col} escapes the corpus directory"))
            continue
        if not path.is_file():
            out.append(Issue("error", cid, f"{col} file is missing: {rel}"))
            continue
        if sha256_file(path) != _get(row, hash_col):
            out.append(Issue("error", cid, f"{hash_col} does not match the file (modified or corrupt): {rel}"))
        if col == "file_path":
            try:
                with wave.open(str(path), "rb") as w:
                    fmt = (w.getframerate(), w.getnchannels(), 8 * w.getsampwidth())
                    frames = w.getnframes()
            except (wave.Error, EOFError) as exc:
                out.append(Issue("error", cid, f"processed file is not a readable WAV: {exc}"))
                continue
            if fmt != (SAMPLE_RATE, 1, 16):
                out.append(Issue("error", cid, f"processed file is {fmt[0]} Hz/{fmt[1]} ch/{fmt[2]}-bit, need 16000/1/16"))
            duration = _num(_get(row, "duration_s"), float)
            if duration is not None and abs(frames / SAMPLE_RATE - duration) > 0.002:
                out.append(Issue("error", cid, "duration_s does not match the file"))
    return out
