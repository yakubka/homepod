"""TEAM LEADER AI-2 / STAGE A / DEVELOPMENT / INTEGRATION HARNESS.

NOT production code. NOT the wake-word path. NOT live-microphone validation.

What it does (and only this):

    Hardware Engineer WAV  ->  WavFileSource  ->  UtteranceSegmenter (Silero)  ->  SpeechSegment
        ->  FasterWhisperEngine  ->  one structured evidence record (JSON, written OUTSIDE the repository)

It orchestrates modules that already exist and are frozen (audio/file_source.py, audio/utterance.py,
audio/silero_vad.py, speech/). It re-implements none of them.

STAGE A means: a real recording made by the Hardware Engineer with the project microphone, run through the
real VESTA software path, with the transcript kept together with its provenance. Its validation label is
RECORDED-HARDWARE AUDIO. It is never LIVE HARDWARE-VALIDATED: that is Stage B (physical microphone, live capture).

Rules this script keeps:
  * The recording is only ever opened read-only. It is never converted, overwritten or moved. If the file is not
    16 kHz / mono / 16-bit PCM WAV it fails with a clear message; nothing is resampled, down-mixed or re-quantised
    here. A converted copy must be made first, explicitly, with the original kept (see docs/ai2_stage_a_hardware_handoff.md).
  * Metadata is never invented. Anything not supplied is recorded as UNKNOWN (or NOT_AVAILABLE for a reference transcript).
    The one exception is consent: a real (or possibly real) human recording MUST carry an explicit consent / project-approval
    reference (UNKNOWN is refused); only generated test audio (origin SYNTHETIC) uses NOT_APPLICABLE.
  * Production settings are not touched. The segmenter values below are HARNESS-LOCAL, DEVELOPMENT and UNCALIBRATED;
    config/settings.py keeps None. Nothing here tunes a VAD threshold or a decoder option.
  * ACTIVATION is DEVELOPMENT_STAGE_A: the segmenter is activated at stream sample 0, before the first block. That is a
    development stand-in for a wake detection, not the production wake-word path. Detection.end_sample is not used.
  * ONE recording = ONE intended phrase. The harness stops at the segmenter's first decision and records how much audio
    after it was not processed. Multi-utterance recordings are not supported in Stage A v1.
  * Transcripts: reusable code never logs them. The expected and actual transcripts exist only in the evidence file
    (an explicit evaluation artifact) and, if asked with --show-transcript, on the console.
  * Raw PCM is never serialised or logged.

Run (from the project root; the output directory must be OUTSIDE the repository):

    python -m scripts.ai2_stage_a_transcribe run --wav D:\\evidence\\rec.wav --provenance D:\\evidence\\rec.prov.json --out-dir D:\\evidence\\out
    python -m scripts.ai2_stage_a_transcribe review D:\\evidence\\out\\REC-001.stage_a.json --human-match MATCH --notes "..."

Exit codes: 0 = a transcript was produced (OK or EMPTY_TEXT); 1 = processed, but the segmenter produced no segment so STT did not
run; 2 = invalid input, provenance or output location (nothing was processed); 3 = a run failure (model, VAD or STT error;
a record with the error type is written when possible).
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

if __name__ == "__main__":      # allow `python scripts/ai2_stage_a_transcribe.py ...` as well as `python -m scripts....`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audio.file_source import EndOfAudio, WavFileSource
from audio.utterance import SegmenterConfig, UtteranceSegmenter
from config import settings
from config.logging_config import setup_logging

logger = setup_logging()

SCHEMA_VERSION = "ai2-stage-a-result-v1"
HARNESS_LABEL = "TEAM LEADER AI-2 / STAGE A / DEVELOPMENT-INTEGRATION HARNESS"
ACTIVATION_MODE = "DEVELOPMENT_STAGE_A"
UNKNOWN = "UNKNOWN"
NOT_AVAILABLE = "NOT_AVAILABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_REVIEWED = "NOT_REVIEWED"

# ---------------------------------------------------------------------------------------------------
# harness-local segmenter values: DEVELOPMENT, UNCALIBRATED. Not production. Not optimal. Not tuned on any audio.
# ---------------------------------------------------------------------------------------------------
DEV_SEGMENTER_VALUES = {
    "start_threshold": 0.5,
    "end_threshold": 0.35,
    "start_confirmation_s": 0.1,
    "end_silence_s": 0.7,
    "onset_padding_s": 0.1,
    "pre_roll_s": 0.0,
    "no_speech_timeout_s": 10.0,
    "min_utterance_s": 0.25,
    "max_utterance_s": 20.0,
}
DEV_SEGMENTER_RATIONALE = {
    "start_threshold": "Silero utility default threshold=0.5 (utils_vad.py at the pinned commit 5cd79456).",
    "end_threshold": "Silero utility rule neg_threshold = threshold - 0.15 = 0.35 (same file). Keeps true hysteresis.",
    "start_confirmation_s": "HARNESS CHOICE: ~0.1 s (rounds up to whole 32 ms frames) so a click is not taken as speech. No audio informed it.",
    "end_silence_s": "HARNESS CHOICE: 0.7 s so a short pause inside one phrase does not end the utterance (Silero's own 0.1 s default suits segmenting, not one phrase).",
    "onset_padding_s": "HARNESS CHOICE: 0.1 s kept before the confirmed onset so the first phoneme is not clipped (Silero default speech_pad_ms is 30 ms).",
    "pre_roll_s": "0.0: activation is at sample 0 with no replay, so there is no pre-roll to use.",
    "no_speech_timeout_s": "HARNESS CHOICE: 10 s, generous so leading silence in a recording does not end the capture.",
    "min_utterance_s": "Silero utility default min_speech_duration_ms=250.",
    "max_utterance_s": "HARNESS CHOICE: 20 s, below the STT backend's 30 s DEVELOPMENT input limit.",
}
SEGMENTER_STATUS = "DEVELOPMENT / UNCALIBRATED (harness-local; config/settings.py is unchanged and still None)"

RECORDING_ORIGINS = ("HARDWARE_ENGINEER_RECORDING", "SYNTHETIC", UNKNOWN)
RECORDING_KINDS = ("SPEECH", "NEGATIVE_CONTROL")
HUMAN_MATCH_VALUES = ("MATCH", "PARTIAL", "MISMATCH", "EMPTY")
STT_STATUSES = ("OK", "EMPTY_TEXT", "FAILED", "NOT_RUN")
# the label follows the declared origin; "LIVE ..." is deliberately not producible by this harness
VALIDATION_LABELS = {
    "HARDWARE_ENGINEER_RECORDING": "RECORDED-HARDWARE AUDIO",
    "SYNTHETIC": "SOFTWARE-VALIDATED (SYNTHETIC INPUT)",
    UNKNOWN: "UNVERIFIED RECORDING ORIGIN",
}

_RECORDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SPEAKER_CODE = re.compile(r"^S\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

PROVENANCE_KEYS = {
    "recording_id", "speaker_code", "consent_reference", "recording_origin", "recording_kind", "recorded_at",
    "microphone", "recording_host", "distance_m", "room_noise_condition", "source_filename", "declared_format",
    "recording_sha256", "processing_history", "conversion", "reference_transcript", "notes",
}
RESULT_KEYS = (
    "schema_version", "harness_label", "run_timestamp_utc", "recording_id", "recording_filename", "recording_sha256",
    "recording_provenance", "input_format", "conversion", "activation_mode", "activation_sample", "segmenter_config",
    "vad", "segment_outcome", "segment_start_sample", "segment_end_sample", "segment_duration_s", "segment_onset_sample",
    "decided_at_sample", "unscored_samples", "samples_after_decision", "stt_engine", "stt_model", "stt_model_verified",
    "stt_status", "expected_transcript", "actual_transcript", "inference_ms", "word_error_rate", "character_error_rate",
    "recording_kind", "human_match", "human_review_timestamp_utc", "notes", "validation_label", "warnings", "run_error",
)


class HarnessError(Exception):
    """Base class for harness problems."""


class HarnessInputError(HarnessError):
    """The recording, the provenance or the arguments are invalid. Nothing was processed."""


class HarnessEvidenceError(HarnessInputError):
    """The evidence output location is unusable (inside the repository, not writable, or the file already exists)."""


# ---------------------------------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------------------------------

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text_or_unknown(data: dict, key: str) -> str:
    value = data.get(key, UNKNOWN)
    if not isinstance(value, str) or not value.strip():
        raise HarnessInputError(f"provenance.{key} must be a non-empty string (use \"{UNKNOWN}\" if it is not known), got {value!r}")
    return value.strip()


def validate_provenance(data) -> dict:
    """Return a normalised provenance dict (every optional field present, unknowns as UNKNOWN) or raise HarnessInputError."""
    if not isinstance(data, dict):
        raise HarnessInputError("provenance must be a JSON object")
    extra = sorted(set(data) - PROVENANCE_KEYS)
    if extra:
        raise HarnessInputError(f"provenance has unknown field(s) {extra}; allowed: {sorted(PROVENANCE_KEYS)}")
    out: dict = {}
    warnings: List[str] = []

    rid = data.get("recording_id")
    if not isinstance(rid, str) or not _RECORDING_ID.match(rid):
        raise HarnessInputError("provenance.recording_id is required: letters, digits, '.', '_', '-' (max 64), starting with a letter or digit")
    if rid.upper().startswith("TEMPLATE"):
        raise HarnessInputError("provenance.recording_id starts with TEMPLATE: this is the blank template, not a real recording")
    out["recording_id"] = rid

    speaker = data.get("speaker_code")
    if speaker != UNKNOWN and not (isinstance(speaker, str) and _SPEAKER_CODE.match(speaker)):
        raise HarnessInputError(f"provenance.speaker_code must be a coded id like S01 (never a name) or \"{UNKNOWN}\", got {speaker!r}")
    out["speaker_code"] = speaker

    origin = data.get("recording_origin", UNKNOWN)
    if origin not in RECORDING_ORIGINS:
        raise HarnessInputError(f"provenance.recording_origin must be one of {RECORDING_ORIGINS}, got {origin!r}")
    out["recording_origin"] = origin

    # CONSENT POLICY: any recording that is, or may be, a real person's voice (a hardware recording, or an origin we do not
    # know) needs an explicit, non-empty consent / project-approval reference. UNKNOWN is not accepted. Only generated test
    # audio (origin SYNTHETIC) has no speaker and must say NOT_APPLICABLE. No real names: the reference is a marker, not a person.
    consent = data.get("consent_reference")
    if origin == "SYNTHETIC":
        if consent != NOT_APPLICABLE:
            raise HarnessInputError(f"provenance.consent_reference must be \"{NOT_APPLICABLE}\" for recording_origin SYNTHETIC, got {consent!r}")
    elif not isinstance(consent, str) or not consent.strip() or consent.strip().upper() in (UNKNOWN, NOT_APPLICABLE):
        raise HarnessInputError(
            "provenance.consent_reference is required for a real or possibly-real human recording: give the explicit consent / "
            f"project-approval reference (e.g. CONSENT-S01). \"{UNKNOWN}\", \"{NOT_APPLICABLE}\" and empty values are not accepted"
        )
    out["consent_reference"] = consent.strip()

    kind = data.get("recording_kind", "SPEECH")
    if kind not in RECORDING_KINDS:
        raise HarnessInputError(f"provenance.recording_kind must be one of {RECORDING_KINDS}, got {kind!r}")
    out["recording_kind"] = kind

    recorded_at = data.get("recorded_at", UNKNOWN)
    if recorded_at != UNKNOWN:
        try:
            datetime.fromisoformat(recorded_at)
        except (TypeError, ValueError):
            raise HarnessInputError(f"provenance.recorded_at must be an ISO 8601 date/time or \"{UNKNOWN}\", got {recorded_at!r}") from None
    out["recorded_at"] = recorded_at

    for key in ("microphone", "recording_host", "room_noise_condition"):
        out[key] = _text_or_unknown(data, key)

    distance = data.get("distance_m", UNKNOWN)
    if distance != UNKNOWN and not (_is_number(distance) and distance >= 0):
        raise HarnessInputError(f"provenance.distance_m must be a number >= 0 or \"{UNKNOWN}\", got {distance!r}")
    out["distance_m"] = distance

    out["source_filename"] = data.get("source_filename")
    if out["source_filename"] is not None and (not isinstance(out["source_filename"], str) or not out["source_filename"].strip()):
        raise HarnessInputError("provenance.source_filename must be a non-empty string when given")

    declared = data.get("declared_format")
    if declared is not None:
        if not isinstance(declared, dict) or set(declared) - {"sample_rate", "channels", "bit_depth"}:
            raise HarnessInputError("provenance.declared_format may only contain sample_rate, channels, bit_depth")
        for k, v in declared.items():
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise HarnessInputError(f"provenance.declared_format.{k} must be a positive integer, got {v!r}")
    out["declared_format"] = declared

    sha = data.get("recording_sha256")
    if sha is not None and not (isinstance(sha, str) and _SHA256.match(sha)):
        raise HarnessInputError("provenance.recording_sha256 must be 64 lowercase hex characters when given")
    out["recording_sha256"] = sha

    history = data.get("processing_history", UNKNOWN)
    if history != UNKNOWN and not (isinstance(history, list) and all(isinstance(h, str) and h.strip() for h in history)):
        raise HarnessInputError(f"provenance.processing_history must be a list of strings (use [] for none) or \"{UNKNOWN}\"")
    out["processing_history"] = history

    conversion = data.get("conversion")
    if conversion is not None:
        if not isinstance(conversion, dict) or set(conversion) != {"description", "original_filename", "original_sha256"}:
            raise HarnessInputError("provenance.conversion must have exactly: description, original_filename, original_sha256")
        for k in ("description", "original_filename"):
            if not isinstance(conversion[k], str) or not conversion[k].strip():
                raise HarnessInputError(f"provenance.conversion.{k} must be a non-empty string")
        if not (isinstance(conversion["original_sha256"], str) and _SHA256.match(conversion["original_sha256"])):
            raise HarnessInputError("provenance.conversion.original_sha256 is required (64 lowercase hex): the original recording must stay identifiable")
    out["conversion"] = conversion

    reference = data.get("reference_transcript")
    if reference is not None and (not isinstance(reference, str) or not reference.strip()):
        raise HarnessInputError("provenance.reference_transcript must be a non-empty string when given (omit it if there is no human reference)")
    if kind == "NEGATIVE_CONTROL" and reference is not None:
        raise HarnessInputError("a NEGATIVE_CONTROL recording has no reference transcript (nothing is meant to be recognised)")
    out["reference_transcript"] = reference

    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise HarnessInputError("provenance.notes must be a string")
    out["notes"] = notes
    out["_warnings"] = warnings
    return out


def load_provenance(path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise HarnessInputError(f"provenance file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessInputError(f"provenance file is not valid JSON: {p} ({type(exc).__name__})") from exc
    return validate_provenance(data)


# ---------------------------------------------------------------------------------------------------
# the recording
# ---------------------------------------------------------------------------------------------------

def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_wav(path: Path) -> dict:
    """Header facts of the WAV (read-only). Raises HarnessInputError for a missing, malformed or non-native file."""
    if not path.is_file():
        raise HarnessInputError(f"recording not found: {path}")
    try:
        with wave.open(str(path), "rb") as wav:
            facts = {"sample_rate": wav.getframerate(), "channels": wav.getnchannels(), "bit_depth": 8 * wav.getsampwidth(), "total_samples": wav.getnframes()}
    except (wave.Error, EOFError) as exc:
        raise HarnessInputError(f"{path.name} is not a readable PCM WAV file ({exc})") from exc
    return facts


def check_native_format(path: Path) -> None:
    """Use the real WavFileSource validation so the harness accepts exactly what the pipeline accepts."""
    try:
        WavFileSource(path)
    except ValueError as exc:
        raise HarnessInputError(
            f"{exc}\nStage A does not resample, down-mix or change bit depth. Ask for a native 16 kHz / mono / 16-bit PCM WAV, or make a "
            "converted COPY (keep the original unchanged), then describe the conversion and the original's SHA-256 in the provenance "
            "('conversion'). See docs/ai2_stage_a_hardware_handoff.md."
        ) from exc


# ---------------------------------------------------------------------------------------------------
# WER / CER (only when a human reference exists)
# ---------------------------------------------------------------------------------------------------

def normalize_for_metrics(text: str) -> str:
    """Lower-case, drop everything except letters, digits and apostrophes, collapse whitespace. Numbers are NOT spelled out."""
    cleaned = re.sub(r"[^\w\s']|_", " ", text.lower())
    return " ".join(cleaned.split())


def edit_distance(a, b) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def error_rates(reference: str, hypothesis: str):
    """(WER, CER) after normalisation, or (None, None) when the reference is empty after normalisation."""
    ref, hyp = normalize_for_metrics(reference), normalize_for_metrics(hypothesis)
    if not ref:
        return None, None
    wer = edit_distance(ref.split(), hyp.split()) / len(ref.split())
    cer = edit_distance(ref, hyp) / len(ref)
    return wer, cer


# ---------------------------------------------------------------------------------------------------
# result record
# ---------------------------------------------------------------------------------------------------

def validate_result(record) -> None:
    """Raise HarnessError unless `record` is a complete, internally consistent Stage A v1 record."""
    if not isinstance(record, dict) or set(record) != set(RESULT_KEYS):
        missing = sorted(set(RESULT_KEYS) - set(record)) if isinstance(record, dict) else "not an object"
        extra = sorted(set(record) - set(RESULT_KEYS)) if isinstance(record, dict) else ""
        raise HarnessError(f"result keys do not match the schema (missing {missing}, extra {extra})")
    if record["schema_version"] != SCHEMA_VERSION:
        raise HarnessError(f"unsupported schema_version {record['schema_version']!r}")
    if not (isinstance(record["recording_sha256"], str) and _SHA256.match(record["recording_sha256"])):
        raise HarnessError("recording_sha256 must be 64 lowercase hex characters")
    if record["activation_mode"] != ACTIVATION_MODE:
        raise HarnessError(f"activation_mode must be {ACTIVATION_MODE}")
    if record["stt_status"] not in STT_STATUSES:
        raise HarnessError(f"stt_status must be one of {STT_STATUSES}")
    if record["human_match"] not in HUMAN_MATCH_VALUES + (NOT_REVIEWED,):
        raise HarnessError(f"human_match must be one of {HUMAN_MATCH_VALUES + (NOT_REVIEWED,)}")
    if record["validation_label"] not in VALIDATION_LABELS.values() or "LIVE" in record["validation_label"].upper():
        raise HarnessError("validation_label must be a recorded / software label; Stage A can never be LIVE HARDWARE-VALIDATED")
    if record["recording_kind"] not in RECORDING_KINDS:
        raise HarnessError("recording_kind invalid")
    if not isinstance(record["stt_model_verified"], bool):
        raise HarnessError("stt_model_verified must be a bool")
    status = record["stt_status"]
    if status in ("OK", "EMPTY_TEXT"):
        if record["segment_outcome"] is None or record["segment_start_sample"] is None:
            raise HarnessError("a transcript needs a segment")
        if not _is_number(record["inference_ms"]) or record["inference_ms"] < 0:
            raise HarnessError("inference_ms must be a finite number >= 0 when STT ran")
        if (status == "OK") != bool(record["actual_transcript"]):
            raise HarnessError("stt_status and actual_transcript disagree")
    else:
        if record["actual_transcript"] != "" or record["inference_ms"] is not None:
            raise HarnessError("a run without a transcript must have actual_transcript '' and inference_ms null")
    if status == "FAILED" and record["run_error"] is None:
        raise HarnessError("FAILED needs run_error")


def _write_json_exclusive(path: Path, record: dict) -> None:
    try:
        with open(path, "x", encoding="utf-8") as handle:      # "x": never overwrite existing evidence
            json.dump(record, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except FileExistsError:
        raise HarnessEvidenceError(f"evidence file already exists and will not be overwritten: {path}") from None
    except OSError as exc:
        raise HarnessEvidenceError(f"cannot write the evidence file {path}: {type(exc).__name__}") from exc


def prepare_output_dir(out_dir, recording_id: str) -> Path:
    """Return the result path. The directory must be outside the repository and writable; nothing is overwritten."""
    from evaluation.ingest import IngestError, assert_outside_repo       # the project's existing personal-data guard
    out = Path(out_dir)
    try:
        assert_outside_repo(out)
    except IngestError as exc:
        raise HarnessEvidenceError(str(exc)) from exc
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / f".write_probe_{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise HarnessEvidenceError(f"evidence directory is not usable: {out} ({type(exc).__name__})") from exc
    target = out / f"{recording_id}.stage_a.json"
    if target.exists():
        raise HarnessEvidenceError(f"evidence file already exists and will not be overwritten: {target}")
    return target


# ---------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------

@dataclass
class StageAOutcome:
    record: dict
    result_path: Path
    exit_code: int


def build_segmenter_config(vad_engine) -> SegmenterConfig:
    if vad_engine.sample_rate != settings.TARGET_SAMPLE_RATE:
        raise HarnessInputError(f"VAD engine runs at {vad_engine.sample_rate} Hz, the recordings at {settings.TARGET_SAMPLE_RATE} Hz")
    return SegmenterConfig(sample_rate=settings.TARGET_SAMPLE_RATE, vad_frame_samples=vad_engine.frame_samples, **DEV_SEGMENTER_VALUES)


def run_stage_a(wav_path, provenance_path, out_dir, *, vad_engine=None, stt_engine=None,
                now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> StageAOutcome:
    """Process one recording and write one evidence record. vad_engine / stt_engine are test hooks; leave None for the real ones."""
    wav = Path(wav_path)
    prov = load_provenance(provenance_path)
    facts = inspect_wav(wav)
    check_native_format(wav)
    if facts["total_samples"] == 0:
        raise HarnessInputError(f"{wav.name} contains no audio samples")
    recording_sha = sha256_of_file(wav)
    if prov["recording_sha256"] is not None and prov["recording_sha256"] != recording_sha:
        raise HarnessInputError("provenance.recording_sha256 does not match the file: this is not the recording the provenance describes")
    declared = prov["declared_format"] or {}
    for key, actual in facts.items():
        if key in declared and declared[key] != actual:
            raise HarnessInputError(f"provenance.declared_format.{key} is {declared[key]} but the file has {actual}")
    result_path = prepare_output_dir(out_dir, prov["recording_id"])

    warnings = list(prov["_warnings"])
    run_error: Optional[dict] = None
    segment_fields = dict(outcome=None, start=None, end=None, duration=None, onset=None, decided=None, unscored=None, after=None)
    stt = dict(status="NOT_RUN", text="", inference_ms=None, engine=None, model=None, verified=False)
    vad_info = {"engine": None, "artifact_verified": False, "frame_samples": None}
    config_record = {"values": dict(DEV_SEGMENTER_VALUES), "rationale": dict(DEV_SEGMENTER_RATIONALE), "status": SEGMENTER_STATUS}

    stage = "build_vad"
    try:
        if vad_engine is None:
            from audio.silero_vad import SileroOnnxVadEngine
            vad_engine = SileroOnnxVadEngine()
        vad_info = {"engine": type(vad_engine).__name__, "artifact_verified": bool(getattr(vad_engine, "artifact_verified", False)),
                    "frame_samples": vad_engine.frame_samples}
        stage = "build_stt"
        if stt_engine is None:
            from speech.faster_whisper_backend import FasterWhisperEngine
            stt_engine = FasterWhisperEngine()
        stt.update(engine=getattr(stt_engine, "engine_id", None), model=getattr(stt_engine, "model_id", None),
                   verified=bool(getattr(stt_engine, "snapshot_verified", False)))

        stage = "segment"
        segmenter = UtteranceSegmenter(build_segmenter_config(vad_engine), vad_engine)
        source = WavFileSource(wav)                       # fast (non-realtime) replay; opens the file read-only
        segmenter.activate(0)                             # DEVELOPMENT_STAGE_A activation at stream sample 0
        next_sample = 0                                   # absolute index of the next block, counted from what the source delivered
        result = None
        source.start()
        try:
            while result is None:
                try:
                    block = source.read_frame()
                except EndOfAudio:
                    break
                result = segmenter.push(block, next_sample)
                next_sample += block.shape[0]
            if result is None:
                result = segmenter.finish()               # end of file: let the segmenter decide with what it received
        finally:
            source.stop()

        segment_fields.update(outcome=result.outcome.value, decided=result.decided_at_sample, unscored=result.unscored_samples,
                              after=facts["total_samples"] - result.decided_at_sample)
        if result.segment is not None:
            seg = result.segment
            segment_fields.update(start=seg.start_sample, end=seg.end_sample, duration=seg.duration_s, onset=seg.onset_sample)
            stage = "stt"
            stt_result = stt_engine.transcribe_segment(seg)          # exactly one call, only on a finished segment
            stt.update(status=stt_result.status.value, text=stt_result.text, inference_ms=stt_result.inference_ms,
                       engine=stt_result.engine_id, model=stt_result.model_id)
    except Exception as exc:        # STT / segmenter / model-artifact errors and anything unexpected: recorded by type and stage, never by message
        run_error = {"type": type(exc).__name__, "stage": stage}
        if stage == "stt":
            stt["status"] = "FAILED"

    reference = prov["reference_transcript"]
    expected = "" if prov["recording_kind"] == "NEGATIVE_CONTROL" else (reference if reference is not None else NOT_AVAILABLE)
    wer = cer = NOT_AVAILABLE
    if reference is not None and stt["status"] in ("OK", "EMPTY_TEXT"):
        rates = error_rates(reference, stt["text"])
        if rates[0] is not None:
            wer, cer = round(rates[0], 4), round(rates[1], 4)

    conversion = prov["conversion"]
    provenance_record = {k: v for k, v in prov.items() if not k.startswith("_") and k != "reference_transcript"}
    provenance_record["source_filename"] = prov["source_filename"] or wav.name
    record = {
        "schema_version": SCHEMA_VERSION,
        "harness_label": HARNESS_LABEL,
        "run_timestamp_utc": now().astimezone(timezone.utc).isoformat(timespec="seconds"),
        "recording_id": prov["recording_id"],
        "recording_filename": wav.name,
        "recording_sha256": recording_sha,
        "recording_provenance": provenance_record,
        "input_format": {**facts, "duration_s": facts["total_samples"] / facts["sample_rate"], "container": "WAV PCM (native, not converted by this harness)"},
        "conversion": ({"applied": True, **conversion} if conversion else {"applied": False}),
        "activation_mode": ACTIVATION_MODE,
        "activation_sample": 0,
        "segmenter_config": config_record,
        "vad": vad_info,
        "segment_outcome": segment_fields["outcome"],
        "segment_start_sample": segment_fields["start"],
        "segment_end_sample": segment_fields["end"],
        "segment_duration_s": segment_fields["duration"],
        "segment_onset_sample": segment_fields["onset"],
        "decided_at_sample": segment_fields["decided"],
        "unscored_samples": segment_fields["unscored"],
        "samples_after_decision": segment_fields["after"],
        "stt_engine": stt["engine"],
        "stt_model": stt["model"],
        "stt_model_verified": stt["verified"],
        "stt_status": stt["status"],
        "expected_transcript": expected,
        "actual_transcript": stt["text"],
        "inference_ms": stt["inference_ms"],
        "word_error_rate": wer,
        "character_error_rate": cer,
        "recording_kind": prov["recording_kind"],
        "human_match": NOT_REVIEWED,
        "human_review_timestamp_utc": None,
        "notes": prov["notes"],
        "validation_label": VALIDATION_LABELS[prov["recording_origin"]],
        "warnings": warnings,
        "run_error": run_error,
    }
    validate_result(record)
    _write_json_exclusive(result_path, record)
    # structural fields only: never a transcript, never audio
    logger.info("event=stage_a_done recording_id=%s segment_outcome=%s stt_status=%s run_error=%s",
                record["recording_id"], record["segment_outcome"], record["stt_status"], (run_error or {}).get("type"))

    if run_error is not None:
        code = 3
    elif stt["status"] in ("OK", "EMPTY_TEXT"):
        code = 0
    else:
        code = 1
    return StageAOutcome(record, result_path, code)


# ---------------------------------------------------------------------------------------------------
# human review
# ---------------------------------------------------------------------------------------------------

def review_result(result_path, human_match: str, notes: Optional[str] = None,
                  now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """Record the human judgement on an existing evidence file. Only human_match, notes and the review time change."""
    if human_match not in HUMAN_MATCH_VALUES:
        raise HarnessInputError(f"human_match must be one of {HUMAN_MATCH_VALUES}")
    path = Path(result_path)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessInputError(f"cannot read the evidence file {path} ({type(exc).__name__})") from exc
    try:
        validate_result(record)
    except HarnessError as exc:
        raise HarnessInputError(f"{path.name} is not a valid Stage A record: {exc}") from exc
    record["human_match"] = human_match
    record["human_review_timestamp_utc"] = now().astimezone(timezone.utc).isoformat(timespec="seconds")
    if notes is not None:
        record["notes"] = notes
    validate_result(record)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return record


# ---------------------------------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai2_stage_a_transcribe", description=HARNESS_LABEL + " (not production code)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="process one recording and write one evidence record")
    run.add_argument("--wav", required=True, help="the recording (native 16 kHz mono 16-bit PCM WAV); opened read-only")
    run.add_argument("--provenance", required=True, help="provenance JSON for this recording")
    run.add_argument("--out-dir", required=True, help="evidence directory OUTSIDE the repository")
    run.add_argument("--show-transcript", action="store_true", help="also print the transcript to the console (default: only in the evidence file)")
    review = sub.add_parser("review", help="record the human match judgement in an existing evidence file")
    review.add_argument("result", help="the *.stage_a.json file")
    review.add_argument("--human-match", required=True, choices=HUMAN_MATCH_VALUES)
    review.add_argument("--notes", default=None)
    return parser


def main(argv=None, *, vad_engine=None, stt_engine=None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = _parser().parse_args(argv)
    try:
        if args.command == "review":
            record = review_result(args.result, args.human_match, args.notes)
            print(f"reviewed {record['recording_id']}: human_match={record['human_match']}", file=out)
            return 0
        outcome = run_stage_a(args.wav, args.provenance, args.out_dir, vad_engine=vad_engine, stt_engine=stt_engine)
    except HarnessInputError as exc:
        print(f"ERROR: {exc}", file=err)
        return 2
    rec = outcome.record
    print(f"[{HARNESS_LABEL}]", file=out)
    print(f"recording_id={rec['recording_id']} label={rec['validation_label']} segment_outcome={rec['segment_outcome']} "
          f"stt_status={rec['stt_status']} stt_model_verified={rec['stt_model_verified']} inference_ms={rec['inference_ms']}", file=out)
    for w in rec["warnings"]:
        print(f"WARNING: {w}", file=err)
    if rec["run_error"]:
        print(f"ERROR: run failed at stage {rec['run_error']['stage']}: {rec['run_error']['type']}", file=err)
    if args.show_transcript:
        print(f"transcript: {rec['actual_transcript']}", file=out)
    print(f"evidence: {outcome.result_path}", file=out)
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
