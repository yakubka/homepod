"""Week 7 Task 5 - recorded development evaluation HARNESS for the VESTA voice pipeline.

STATUS: harness READY (SOFTWARE-VALIDATED on synthetic fixtures). EXECUTION PENDING A REAL, CONSENTED
RECORDED DEVELOPMENT CORPUS. Running this tool on synthetic audio proves schema, arithmetic, wiring and error
handling ONLY; it measures no WER, no VAD accuracy and no classroom robustness. Nothing here is a Week 7 result.

    recording -> validated ingest -> speech.pipeline.VoicePipeline -> pipeline result
              -> reference transcript -> metrics -> per-item evidence -> aggregate report

The evaluator only DRIVES the frozen production code (audio/, wakeword/, speech/); it re-implements none of it and
no production module imports it. It is development tooling for the AI Engineer; it never touches config/settings.py.

CORPUS MANIFEST (JSON, schema week7-voice-eval-manifest-v1; a versioned template with NO real recordings is
evaluation/week7_corpus_manifest.template.json). One entry per recording:
    recording_id, audio_path, speaker_code, consent_reference, recording_origin, microphone_or_device,
    distance_condition, noise_condition, category, expected_wake_phrase, reference_command_text, split, notes
    (optional: wake_end_sample, speech_start_sample, speech_end_sample, human_review, audio_sha256, conversion)
  * category: CLEAN_COMMAND, WAKE_IMMEDIATE_COMMAND, WAKE_PAUSE_COMMAND, SILENCE_CONTROL, NOISE_CONTROL,
    SPEECH_NO_WAKE, SHORT_NON_COMMAND. SILENCE_CONTROL, NOISE_CONTROL and SHORT_NON_COMMAND (cough, click, breath;
    NOT speech) are the NON-SPEECH CONTROLS: their reference_command_text must be empty.
  * reference_command_text is the COMMAND ONLY, without the wake phrase. expected_wake_phrase is what was spoken at
    the start ("" when none); it is required for WAKE_IMMEDIATE_COMMAND and WAKE_PAUSE_COMMAND.
  * split: DEV or HELDOUT. No TRAIN. Calibration may use DEV only (see "Splits").
  * speaker_code: S01-style code or UNKNOWN; NEVER a name. consent_reference is a marker, never a person.
  * CONSENT: a real or possibly-real human recording (every origin except SYNTHETIC, including UNKNOWN) needs a STRUCTURED
    consent / project-approval reference: CONSENT-<id> or APPROVAL-<id> (letters, digits, '-' or '_'), where <id> must contain
    a digit and no placeholder word (TBD, N/A, NONE, PENDING, UNKNOWN, FALSE, NO, TEST, XXX ...). UNKNOWN, NOT_APPLICABLE, "?"
    and every free-text placeholder are rejected. Only recording_origin SYNTHETIC uses consent_reference NOT_APPLICABLE (and
    then no other value is accepted). THIS IS SYNTAX VALIDATION ONLY: it does NOT prove that consent was ever obtained. A private
    project consent register (kept outside the repository) and a human approval of the manifest are still required.
  * Audio must be 16 kHz / mono / 16-bit PCM WAV. The evaluator opens it read-only and NEVER resamples or down-mixes:
    a different format is EXCLUDED (reason NON_NATIVE_FORMAT). Convert a COPY with the existing ingest path
    (python -m evaluation.ingest), keep the original, and describe it in the optional `conversion` object
    ({description, original_filename, original_sha256}).
  * Real (non-SYNTHETIC) audio must live OUTSIDE the repository; a path inside it is rejected. Recordings are never copied.
  * A manifest that holds real or possibly-human records (reference transcripts, consent markers, device details) must itself
    live OUTSIDE the repository (canonical, symlink / junction-resolved path comparison, not a string prefix).
  * Name audio files by recording_id (REC-0001.wav), never by person or content: file names are treated as potentially personal.

WAKE MODES (--wake-mode). The real openWakeWord engine may be unavailable on a machine; wake accuracy is a separate question.
  annotated (default): a scripted detector fires on the first 80 ms chunk that ends at or after the manifest's wake_end_sample
     (a SIMULATED wake: activation lags the annotation by up to one chunk). Command categories without an annotation
     (CLEAN_COMMAND) activate at the start of the clip; WAKE_* categories without one are EXCLUDED (WAKE_ANNOTATION_MISSING).
  start: every clip is activated at its start (isolates VAD + STT; a SIMULATED wake).
  detector: the real wake engine runs on the clip. Missed wakes (NO_ACTIVATION) and false activations are measured.
  In every mode the NON-SPEECH CONTROLS are activated at the start, so the VAD and STT are always stressed; in detector mode
  they additionally get a detector-only pass that counts false activations. In annotated / start modes wake accuracy is
  NOT MEASURED and the report says so.

METRICS (see evaluation/week7_voice_metrics.py for the exact arithmetic)
  * pipeline status counts / rates (every Task 4 status plus NO_ACTIVATION and PIPELINE_ERROR);
  * WER and CER, MICRO averages. `decoded` = only items where STT produced a transcript (TRANSCRIBED, WAKE_ONLY,
    MAX_DURATION, STREAM_ENDED, STT_EMPTY): capture failures (NO_SPEECH, TOO_SHORT, STT_FAILED, INVALID_ACTIVATION,
    PIPELINE_ERROR, NO_ACTIVATION) are NOT in this denominator. `end_to_end` also counts every evaluated command item,
    with an empty hypothesis when no text was produced, so capture misses cost a full deletion. Both are reported;
    neither silently drops words;
  * hallucination_control_rate = non-speech controls whose STT decoded NON-EMPTY text / valid non-speech controls.
    A control is valid when the pipeline ran to a verdict (not STT_FAILED, INVALID_ACTIVATION, PIPELINE_ERROR). A control the
    VAD gates out before STT counts as not hallucinated. Speech clips NEVER enter this metric: a wrong transcript of speech
    is an STT error, not a hallucination-control sample. Optional --stt-direct-controls also runs STT on the whole control
    clip (bypassing the VAD) for a stricter, separate measure;
  * wake-prefix outcomes, separate from WER; extra words removed beyond the wake phrase; WAKE_ONLY counts;
  * segmentation geometry against an ANNOTATED speech region (start / end offset, covered fraction). This is NOT frame-level
    VAD accuracy: no frame-level ground truth exists. NO_SPEECH on a command clip and a segment on a control are reported
    as false negatives / false speech triggers;
  * timings: pipeline wall time, VAD time, STT inference, audio duration, real-time factor. STARTUP (Silero and Whisper
    construction, the first STT inference) is reported separately from steady-state inference. Development PC only: NO Pi claim.

SPLITS. Default run: DEV only. HELDOUT needs --allow-heldout AND an explicit --segmenter-config file (a frozen, hashed
configuration), and the report states that HELDOUT must not tune VAD thresholds, segmentation timings, wake-prefix rules or
STT settings. The calibration sweep (evaluation/week7_calibration.py) refuses any non-DEV recording.

PREFLIGHT AND FAILURE POLICY. Before any model is loaded or any evidence is written, the whole selected manifest is checked: every
field the evaluator consumes (wake-phrase format, annotation ranges against the real clip length, a reference that still starts with
the wake phrase, the mode's requirements such as wake_end_sample for WAKE_* categories in annotated mode, a detector in detector
mode) and the segmenter configuration. A bad manifest is rejected cleanly with EVERY problem listed and no field value echoed.
At run time a failure is either SAFE PER RECORDING (a decoder, VAD-scoring or detector error on one clip: recorded as that item's
PIPELINE_ERROR / STT_FAILED, the run continues) or FATAL (model integrity or availability, an STT / VAD contract violation, a
sample-clock error, an unexpected internal error: EvalFatalError). A fatal failure stops the run, keeps the items finished so far
in week7_eval_items.jsonl.partial and writes week7_eval_report.INCOMPLETE.json (run_status INCOMPLETE_FATAL, no accuracy results).
The normal names week7_eval_items.jsonl and week7_eval_report.json exist ONLY for a run that finished, so a partial output can
never be mistaken for a complete one. Exit codes: 0 finished; 2 invalid manifest / configuration / environment / output location
(nothing was evaluated); 3 fatal failure during the evaluation (partial, INCOMPLETE evidence).

EVIDENCE AND PRIVACY. Normal logs carry recording ids, categories, statuses, counts and timings only: never a transcript,
PCM, a speaker name or a consent detail. The per-item evidence file (JSON lines) is a CONTROLLED RESEARCH ARTIFACT and does
contain the reference and decoded transcripts; the aggregate report contains none. When the manifest holds any real
recording the output directory must be OUTSIDE the repository; nothing existing is ever overwritten.

CONFIG. The segmenter values used when no --segmenter-config is given are EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES: development
values chosen before any audio was seen, NOT calibrated, NOT production (config/settings.py still holds None and is not touched).
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import wave
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from audio.file_source import WavFileSource
from audio.silero_vad import (
    SILERO_MODEL, SileroModelContractMismatch, SileroModelHashMismatch, SileroModelLoadFailed, SileroModelNotFound, SileroVadError,
)
from audio.utterance import SegmenterConfig, SegmenterConfigError, UtteranceSegmenter
from audio.vad import VadContractError, VadEngine
from config.logging_config import setup_logging
from evaluation.ingest import IngestError, REPO_ROOT, assert_outside_repo
from evaluation import week7_voice_metrics as vm
from speech.pipeline import (
    DEFAULT_WAKE_PHRASES, PipelineClockError, PipelineConfigError, PipelineResult, PipelineStatus, VoicePipeline, _compile_phrases,
    strip_wake_prefix,
)
from speech.stt import SttContractError, SttEngine, SttError, SttModelLoadFailed, SttModelNotFound, SttRuntimeUnavailable, SttStatus
from wakeword.detector import WakeWordDetector, run_on_source
from wakeword.openwakeword_backend import WakeWordBackendError

logger = setup_logging()

SCHEMA_MANIFEST = "week7-voice-eval-manifest-v1"
SCHEMA_ITEM = "week7-voice-eval-item-v1"
SCHEMA_REPORT = "week7-voice-eval-report-v1"
EVALUATOR_LABEL = "WEEK 7 TASK 5 DEVELOPMENT EVALUATION HARNESS"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"
SAMPLE_RATE = 16000
WAKE_CHUNK = 1280                       # 80 ms, the openWakeWord chunk size

CATEGORIES = (
    "CLEAN_COMMAND", "WAKE_IMMEDIATE_COMMAND", "WAKE_PAUSE_COMMAND",
    "SILENCE_CONTROL", "NOISE_CONTROL", "SPEECH_NO_WAKE", "SHORT_NON_COMMAND",
)
COMMAND_CATEGORIES = ("CLEAN_COMMAND", "WAKE_IMMEDIATE_COMMAND", "WAKE_PAUSE_COMMAND")
WAKE_REQUIRED_CATEGORIES = ("WAKE_IMMEDIATE_COMMAND", "WAKE_PAUSE_COMMAND")
NON_SPEECH_CATEGORIES = ("SILENCE_CONTROL", "NOISE_CONTROL", "SHORT_NON_COMMAND")
SPLITS = ("DEV", "HELDOUT")
ORIGINS = ("HARDWARE_ENGINEER_RECORDING", "TEAM_MEMBER_RECORDING", "PROJECT_MICROPHONE_RECORDING", "SYNTHETIC", UNKNOWN)
REVIEW_STATUSES = ("MATCH", "PARTIAL", "MISMATCH", "EMPTY", "NOT_REVIEWED")
WAKE_MODES = ("annotated", "start", "detector")
ITEM_STATUSES = ("EVALUATED", "EXCLUDED")
EXCLUSION_REASONS = (
    "AUDIO_MISSING", "AUDIO_HASH_MISMATCH", "NON_NATIVE_FORMAT", "WAKE_ANNOTATION_MISSING", "DETECTOR_UNAVAILABLE",
)
# evaluator-level statuses added to the pipeline's own
PIPELINE_STATUS_VALUES = tuple(s.value for s in PipelineStatus) + ("NO_ACTIVATION", "PIPELINE_ERROR")
STT_OUTPUT_STATUSES = frozenset({"TRANSCRIBED", "WAKE_ONLY", "MAX_DURATION", "STREAM_ENDED", "STT_EMPTY"})
TEXT_STATUSES = frozenset({"TRANSCRIBED", "MAX_DURATION", "STREAM_ENDED"})
UNJUDGEABLE_CONTROL_STATUSES = frozenset({"STT_FAILED", "INVALID_ACTIVATION", "PIPELINE_ERROR", "NO_ACTIVATION"})

# Evaluation-only development values, chosen before any audio was seen. NOT calibrated, NOT production.
EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES: Dict[str, float] = {
    "start_threshold": 0.5,
    "end_threshold": 0.35,
    "start_confirmation_s": 0.1,
    "end_silence_s": 0.7,
    "onset_padding_s": 0.1,
    "pre_roll_s": 1.0,
    "no_speech_timeout_s": 10.0,
    "min_utterance_s": 0.25,
    "max_utterance_s": 20.0,
}
SEGMENTER_FIELDS = tuple(EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES)

LIMITATIONS = (
    "Synthetic or fake inputs validate schema, arithmetic, wiring and error handling only; they are reported separately and are never an accuracy result.",
    "WER / CER depend on the fixed normalisation week7-text-norm-v1 and on the quality of the human reference transcripts.",
    "Segmentation metrics compare against annotated speech regions; there is NO frame-level VAD ground truth, so no frame-level VAD accuracy is claimed.",
    "In annotated and start wake modes the wake is SIMULATED: wake accuracy is NOT MEASURED.",
    "Timings are from the development PC only; nothing here is a Raspberry Pi / ReSpeaker measurement and nothing is HARDWARE-VALIDATED or LIVE.",
    "Segmenter values are uncalibrated unless a frozen --segmenter-config was supplied; HELDOUT results must not be used to tune anything.",
    "Small corpora give wide uncertainty; every rate carries its denominator and none should be quoted without it.",
    "The WER / CER normalisation turns punctuation into spaces, so it cannot see a lost minus sign or decimal point (\"-5\" and \"5\" compare equal); check numeric commands by hand.",
    "Consent validation is syntactic: it does not prove consent was obtained. A private consent register and a human approval of the manifest are required.",
    "HELDOUT isolation is procedural, not enforced by the code: a frozen configuration/protocol and ONE authorised HELDOUT evaluation are required before any final claim.",
    "Latency percentiles include the first item's cold start (only STT inference excludes its first call); do not quote steady-state latency from a mixed measurement.",
)

_CONSENT_REF = re.compile(r"^(?:CONSENT|APPROVAL)-([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*)$")
_PLACEHOLDER_WORDS = frozenset({
    "TBD", "TODO", "NA", "N", "NONE", "NULL", "NIL", "PENDING", "UNKNOWN", "UNSPECIFIED", "FALSE", "TRUE", "NO", "YES", "OK", "OKAY",
    "TEST", "TESTING", "TEMP", "TEMPLATE", "PLACEHOLDER", "REPLACE", "REPLACEME", "CHANGEME", "FILLME", "ME", "XXX", "XXXX", "XX", "X",
    "ANY", "EXAMPLE", "SAMPLE", "DEFAULT", "BLANK", "EMPTY", "MISSING", "NOTAPPLICABLE", "NOTAVAILABLE", "UNDEFINED", "DUMMY", "FAKE",
})


def consent_reference_problem(value: Any) -> Optional[str]:
    """None if `value` is a syntactically acceptable consent / project-approval reference, else a generic reason.

    The reason NEVER echoes the value. Syntax only: this does not and cannot prove that consent was obtained.
    """
    if not isinstance(value, str):
        return "must be a string"
    text = value.strip()
    if text.upper() in (UNKNOWN, NOT_APPLICABLE) or not text:
        return "is missing, UNKNOWN or NOT_APPLICABLE"
    match = _CONSENT_REF.match(text)
    if not match or len(text) > 80:
        return "does not have the form CONSENT-<id> or APPROVAL-<id> (letters, digits, '-' or '_')"
    tail = match.group(1)
    tokens = re.split(r"[-_]", tail)
    if any(t.upper() in _PLACEHOLDER_WORDS for t in tokens) or len(set(tail.replace("-", "").replace("_", "").upper())) == 1:
        return "looks like a placeholder"
    if not any(ch.isdigit() for ch in tail):
        return "has no register number (the id must contain a digit; use a register id, not a name)"
    return None


def _plain_wake_phrase(phrase: str) -> bool:
    """True when the phrase is words of letters / digits only (what the pipeline's prefix rule accepts)."""
    try:
        _compile_phrases([phrase])
    except PipelineConfigError:
        return False
    return True


_RECORDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SPEAKER_CODE = re.compile(r"^S\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_RECORDING_KEYS = (
    "recording_id", "audio_path", "speaker_code", "consent_reference", "recording_origin", "microphone_or_device",
    "distance_condition", "noise_condition", "category", "expected_wake_phrase", "reference_command_text", "split", "notes",
)
OPTIONAL_RECORDING_KEYS = (
    "wake_end_sample", "speech_start_sample", "speech_end_sample", "human_review", "audio_sha256", "conversion",
)
MANIFEST_KEYS = {"schema_version", "corpus_id", "template", "banner", "recordings", "notes"}


class EvalInputError(ValueError):
    """The manifest, a setting or a request is invalid. Nothing was evaluated."""


class EvalEvidenceError(EvalInputError):
    """The evidence output location is unusable (inside the repository for real data, or it already exists)."""


class EvalFatalError(RuntimeError):
    """The run cannot be trusted any further (model integrity / availability, a contract or clock error, an unexpected internal error).

    Carries the items finished so far (`items`) and an INCOMPLETE report (`report`), filled in by evaluate_manifest.
    """

    def __init__(self, error_class: str) -> None:
        super().__init__(f"fatal evaluation failure ({error_class}); the run was stopped and its evidence is marked INCOMPLETE")
        self.error_class = error_class
        self.items: List[Dict[str, Any]] = []
        self.report: Optional[Dict[str, Any]] = None


# Failures that make the WHOLE run untrustworthy. Anything else raised while evaluating ONE recording is a safe per-recording
# failure and is recorded on that item (PIPELINE_ERROR / STT_FAILED).
FATAL_ERRORS: Tuple[type, ...] = (
    PipelineClockError, SttContractError, SttModelLoadFailed, SttModelNotFound, SttRuntimeUnavailable,
    SileroModelContractMismatch, SileroModelHashMismatch, SileroModelLoadFailed, SileroModelNotFound, VadContractError,
    WakeWordBackendError, MemoryError,
)
# The pipeline turns an SttError into a STT_FAILED result instead of raising it, so a fatal STT class also has to be recognised by NAME.
_FATAL_CLASS_NAMES = frozenset(c.__name__ for c in FATAL_ERRORS)


# ---------------------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Recording:
    recording_id: str
    audio_path: str
    audio_file: Path
    speaker_code: str
    consent_reference: str
    recording_origin: str
    microphone_or_device: str
    distance_condition: str
    noise_condition: str
    category: str
    expected_wake_phrase: str
    reference_command_text: str
    split: str
    notes: str
    wake_end_sample: Optional[int] = None
    speech_start_sample: Optional[int] = None
    speech_end_sample: Optional[int] = None
    human_review: str = "NOT_REVIEWED"
    audio_sha256: Optional[str] = None
    conversion: Optional[Dict[str, str]] = None

    @property
    def is_synthetic(self) -> bool:
        return self.recording_origin == "SYNTHETIC"

    @property
    def is_non_speech_control(self) -> bool:
        return self.category in NON_SPEECH_CATEGORIES


@dataclass(frozen=True)
class Manifest:
    path: Optional[Path]
    sha256: str
    corpus_id: str
    template: bool
    recordings: Tuple[Recording, ...]

    @property
    def has_real_recordings(self) -> bool:
        return any(not r.is_synthetic for r in self.recordings)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _parse_recording(raw: Any, base_dir: Path, allow_template: bool, problems: List[str]) -> Optional[Recording]:
    if not isinstance(raw, dict):
        problems.append("a recording entry is not a JSON object")
        return None
    rid = raw.get("recording_id")
    where = rid if isinstance(rid, str) else "<missing recording_id>"
    start = len(problems)

    def bad(msg: str) -> None:
        problems.append(f"{where}: {msg}")

    unknown = sorted(set(raw) - set(REQUIRED_RECORDING_KEYS) - set(OPTIONAL_RECORDING_KEYS))
    if unknown:
        bad(f"unknown field(s) {unknown}")
    missing = [k for k in REQUIRED_RECORDING_KEYS if k not in raw]
    if missing:
        bad(f"missing required field(s) {missing}")
        return None

    if not (isinstance(rid, str) and _RECORDING_ID.match(rid)):
        bad("recording_id must be letters, digits, '.', '_', '-' (max 64), starting with a letter or digit")
    elif rid.upper().startswith("TEMPLATE") and not allow_template:
        bad("recording_id starts with TEMPLATE: this is the blank template, not a real recording")

    for key in ("audio_path", "microphone_or_device", "distance_condition", "noise_condition"):
        if not _nonempty_str(raw[key]):
            bad(f"{key} must be a non-empty string (use \"{UNKNOWN}\" if it is not known)")

    is_template_entry = allow_template and str(rid).upper().startswith("TEMPLATE")
    origin = raw["recording_origin"]
    if origin not in ORIGINS:
        bad(f"recording_origin must be one of {ORIGINS}")

    speaker = raw["speaker_code"]
    consent = raw["consent_reference"]
    if origin == "SYNTHETIC":
        if consent != NOT_APPLICABLE:
            bad(f"consent_reference must be \"{NOT_APPLICABLE}\" for recording_origin SYNTHETIC")
        if speaker != NOT_APPLICABLE:
            bad(f"speaker_code must be \"{NOT_APPLICABLE}\" for recording_origin SYNTHETIC")
    else:
        # CONSENT POLICY (consistent with the Stage A harness, stricter in form): any recording that is, or may be, a real person's voice
        # needs a STRUCTURED consent / project-approval reference (see consent_reference_problem). This is SYNTAX validation only: it does
        # not prove consent was obtained; a private consent register and a human approval are still required. The value is never echoed.
        problem = None if is_template_entry else consent_reference_problem(consent)
        if problem is not None:
            bad(f"consent_reference {problem}: a real or possibly-real human recording needs a structured project consent reference such as "
                "CONSENT-<register id with a digit> (syntax check only; it does not prove consent was obtained)")
        if speaker != UNKNOWN and not (isinstance(speaker, str) and _SPEAKER_CODE.match(speaker)):
            bad(f"speaker_code must be a coded id like S01 (never a name) or \"{UNKNOWN}\"")

    category = raw["category"]
    if category not in CATEGORIES:
        bad(f"category must be one of {CATEGORIES}")
    split = raw["split"]
    if split not in SPLITS:
        bad(f"split must be one of {SPLITS}")

    phrase, reference = raw["expected_wake_phrase"], raw["reference_command_text"]
    if not isinstance(phrase, str):
        bad("expected_wake_phrase must be a string (\"\" when no wake phrase was spoken)")
    if not isinstance(reference, str):
        bad("reference_command_text must be a string (\"\" when there is no command)")
    if not isinstance(raw["notes"], str):
        bad("notes must be a string")
    if isinstance(phrase, str) and category in WAKE_REQUIRED_CATEGORIES and not phrase.strip():
        bad(f"expected_wake_phrase is required for {category}")
    if isinstance(reference, str) and category in NON_SPEECH_CATEGORIES and reference.strip():
        bad(f"{category} is a non-speech control: reference_command_text must be empty")
    if isinstance(phrase, str) and category in NON_SPEECH_CATEGORIES and phrase.strip():
        bad(f"{category} is a non-speech control: expected_wake_phrase must be empty")
    if isinstance(phrase, str) and category == "SPEECH_NO_WAKE" and phrase.strip():
        bad("SPEECH_NO_WAKE has no wake phrase: expected_wake_phrase must be empty")
    if isinstance(phrase, str) and phrase.strip():
        if not _plain_wake_phrase(phrase):
            bad("expected_wake_phrase must be plain words (letters and digits separated by spaces, no punctuation), as the pipeline's prefix rule requires")
        elif isinstance(reference, str) and reference.strip() and strip_wake_prefix(reference, [phrase])[1]:
            bad("reference_command_text must be the command only: it still starts with the expected wake phrase")

    for key in ("wake_end_sample", "speech_start_sample", "speech_end_sample"):
        if key in raw and raw[key] is not None and (not _is_int(raw[key]) or raw[key] < 0):
            bad(f"{key} must be a non-negative integer sample index when given")
    s0, s1 = raw.get("speech_start_sample"), raw.get("speech_end_sample")
    if (s0 is None) != (s1 is None):
        bad("speech_start_sample and speech_end_sample must be given together")
    elif s0 is not None and _is_int(s0) and _is_int(s1) and s1 <= s0:
        bad("speech_end_sample must be greater than speech_start_sample")
    review = raw.get("human_review", "NOT_REVIEWED")
    if review not in REVIEW_STATUSES:
        bad(f"human_review must be one of {REVIEW_STATUSES}")
    sha = raw.get("audio_sha256")
    if sha is not None and not (isinstance(sha, str) and _SHA256.match(sha)):
        bad("audio_sha256 must be 64 lowercase hex characters when given")
    conversion = raw.get("conversion")
    if conversion is not None:
        if not isinstance(conversion, dict) or set(conversion) != {"description", "original_filename", "original_sha256"}:
            bad("conversion must have exactly: description, original_filename, original_sha256")
        elif not (_nonempty_str(conversion["description"]) and _nonempty_str(conversion["original_filename"])
                  and isinstance(conversion["original_sha256"], str) and _SHA256.match(conversion["original_sha256"])):
            bad("conversion fields must be non-empty and original_sha256 must be 64 lowercase hex (the original must stay identifiable)")

    audio_path_raw = raw["audio_path"]
    audio_file = None
    if _nonempty_str(audio_path_raw):
        candidate = Path(audio_path_raw).expanduser()
        audio_file = candidate if candidate.is_absolute() else (base_dir / candidate)
        if origin != "SYNTHETIC" and not (allow_template and str(rid).upper().startswith("TEMPLATE")):
            try:
                assert_outside_repo(audio_file)                 # recordings are personal data: never inside the repository
            except IngestError:
                bad("audio_path resolves inside the source repository; real recordings are personal data and must be stored outside it")
    if len(problems) > start:
        return None
    return Recording(
        recording_id=rid, audio_path=audio_path_raw, audio_file=audio_file, speaker_code=speaker, consent_reference=str(consent).strip(),
        recording_origin=origin, microphone_or_device=raw["microphone_or_device"].strip(),
        distance_condition=raw["distance_condition"].strip(), noise_condition=raw["noise_condition"].strip(), category=category,
        expected_wake_phrase=phrase.strip(), reference_command_text=reference.strip(), split=split, notes=raw["notes"],
        wake_end_sample=raw.get("wake_end_sample"), speech_start_sample=s0, speech_end_sample=s1, human_review=review,
        audio_sha256=sha, conversion=conversion,
    )


def parse_manifest(data: Any, base_dir: Path, *, allow_template: bool = False, path: Optional[Path] = None, sha256: str = "") -> Manifest:
    """Validate a manifest dict. Raises EvalInputError listing EVERY problem; nothing is partially accepted."""
    if not isinstance(data, dict):
        raise EvalInputError("the manifest must be a JSON object")
    problems: List[str] = []
    extra = sorted(set(data) - MANIFEST_KEYS)
    if extra:
        problems.append(f"unknown top-level field(s) {extra}")
    if data.get("schema_version") != SCHEMA_MANIFEST:
        problems.append(f"schema_version must be {SCHEMA_MANIFEST!r}")
    corpus_id = data.get("corpus_id")
    if not _nonempty_str(corpus_id):
        problems.append("corpus_id must be a non-empty string")
    template = data.get("template", False)
    if template is not False and template is not True:
        problems.append("template must be true or false when given")
    if template is True and not allow_template:
        problems.append("this manifest is a TEMPLATE (NO REAL RECORDINGS INCLUDED): copy it, fill in real entries and remove \"template\"")
    raw_recordings = data.get("recordings")
    if not isinstance(raw_recordings, list) or not raw_recordings:
        problems.append("recordings must be a non-empty list")
        raw_recordings = []
    recordings: List[Recording] = []
    for raw in raw_recordings:
        rec = _parse_recording(raw, base_dir, allow_template, problems)
        if rec is not None:
            recordings.append(rec)
    ids = Counter(r["recording_id"] for r in raw_recordings if isinstance(r, dict) and isinstance(r.get("recording_id"), str))
    for rid, n in sorted(ids.items()):
        if n > 1:
            problems.append(f"{rid}: recording_id appears {n} times")
    if problems:
        raise EvalInputError("manifest is invalid:\n  - " + "\n  - ".join(problems))
    return Manifest(path=path, sha256=sha256, corpus_id=corpus_id, template=bool(template), recordings=tuple(recordings))


def load_manifest(path, *, allow_template: bool = False) -> Manifest:
    p = Path(path)
    if not p.is_file():
        raise EvalInputError("manifest file not found")
    raw = p.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvalInputError(f"manifest is not valid JSON ({type(exc).__name__})") from None
    manifest = parse_manifest(data, p.resolve().parent, allow_template=allow_template, path=p.resolve(), sha256=hashlib.sha256(raw).hexdigest())
    if manifest.has_real_recordings and not (allow_template and manifest.template):
        # The manifest holds reference transcripts, consent markers and device details of real people: it must never sit in the repository
        # (canonical comparison, symlinks / junctions resolved; see evaluation.ingest.assert_outside_repo).
        try:
            assert_outside_repo(p)
        except IngestError:
            raise EvalInputError("this manifest holds real or possibly-human records (transcripts, consent markers, device details) and must be "
                                 "stored outside the source repository") from None
    return manifest


def corpus_warnings(recordings: Sequence[Recording]) -> List[str]:
    """Loud, non-fatal corpus problems (a speaker spread over both splits, empty splits, thin categories)."""
    warnings: List[str] = []
    by_speaker: Dict[str, set] = {}
    for r in recordings:
        if not r.is_synthetic and r.speaker_code != UNKNOWN:
            by_speaker.setdefault(r.speaker_code, set()).add(r.split)
    for speaker, splits in sorted(by_speaker.items()):
        if len(splits) > 1:
            warnings.append(f"SPEAKER_IN_BOTH_SPLITS: {speaker} appears in {sorted(splits)}; HELDOUT is not speaker-independent")
    real = [r for r in recordings if not r.is_synthetic]
    for split in SPLITS:
        if not any(r.split == split for r in real):
            warnings.append(f"NO_REAL_RECORDINGS_IN_{split}")
    for category in CATEGORIES:
        if not any(r.category == category for r in real):
            warnings.append(f"NO_REAL_RECORDINGS_FOR_CATEGORY_{category}")
    return warnings


def preflight_manifest(selected: Sequence[Recording], *, wake_mode: str, detector_available: bool) -> None:
    """Check the whole selected manifest BEFORE any model is loaded or evidence is written. Raises EvalInputError listing every problem.

    Field values are never echoed (only recording ids, field names and sample counts). Audio that is missing, not native, or fails its hash
    stays a per-recording EXCLUSION at run time, but the annotations of every readable clip are checked against its real length here.
    """
    problems: List[str] = []
    for rec in selected:
        _, _, reason = plan_activation(rec, wake_mode, detector_available)
        if reason == "WAKE_ANNOTATION_MISSING":
            problems.append(f"{rec.recording_id}: wake_end_sample is required for {rec.category} in annotated mode")
        elif reason == "DETECTOR_UNAVAILABLE":
            problems.append(f"{rec.recording_id}: wake mode 'detector' needs a wake detector, and none is available")
        if rec.wake_end_sample is None and rec.speech_end_sample is None:
            continue
        try:
            total = WavFileSource(rec.audio_file).total_samples
        except (ValueError, OSError):
            continue                                              # reported as an exclusion when the run reaches it
        if rec.wake_end_sample is not None and rec.wake_end_sample > total:
            problems.append(f"{rec.recording_id}: wake_end_sample is beyond the end of the audio ({total} samples)")
        if rec.speech_end_sample is not None and rec.speech_end_sample > total:
            problems.append(f"{rec.recording_id}: speech_end_sample is beyond the end of the audio ({total} samples)")
    if problems:
        raise EvalInputError("manifest preflight failed:\n  - " + "\n  - ".join(problems))


# ---------------------------------------------------------------------------------------
# instrumentation wrappers (evaluator side only; the production classes are unchanged)
# ---------------------------------------------------------------------------------------

class TimedVad(VadEngine):
    """Delegates to a real VadEngine and accumulates the time spent scoring frames."""

    def __init__(self, inner: VadEngine, clock: Callable[[], float] = time.perf_counter) -> None:
        self._inner, self._clock = inner, clock
        self.total_s, self.frames = 0.0, 0

    @property
    def sample_rate(self) -> int:
        return self._inner.sample_rate

    @property
    def frame_samples(self) -> int:
        return self._inner.frame_samples

    def _score_frame(self, frame):
        began = self._clock()
        score = self._inner.process_frame(frame)
        self.total_s += self._clock() - began
        self.frames += 1
        return score

    def reset(self) -> None:
        self._inner.reset()

    def reset_counters(self) -> None:
        self.total_s, self.frames = 0.0, 0


class CapturingStt(SttEngine):
    """Delegates to a real SttEngine and records the RAW decoded text of every call (before the pipeline's prefix rule)."""

    def __init__(self, inner: SttEngine) -> None:
        super().__init__()
        self._inner = inner
        self.calls: List[Dict[str, Any]] = []
        self.total_calls = 0

    @property
    def sample_rate(self) -> int:
        return self._inner.sample_rate

    @property
    def engine_id(self) -> str:
        return self._inner.engine_id

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def max_audio_s(self) -> Optional[float]:
        return getattr(self._inner, "max_audio_s", None)

    def _transcribe(self, samples):                           # never used: transcribe() is overridden
        raise NotImplementedError

    def transcribe(self, samples, sample_rate):
        self.total_calls += 1
        try:
            result = self._inner.transcribe(samples, sample_rate)
        except SttError as exc:
            self.calls.append({"error_class": type(exc).__name__})
            raise
        self.calls.append({"raw_text": result.text, "status": result.status, "inference_ms": result.inference_ms})
        return result

    def reset_item(self) -> None:
        self.calls = []


class AnnotatedWakeDetector(WakeWordDetector):
    """SIMULATED wake: fires once, on the first chunk whose end is at or after `fire_after_sample` (0 = the first chunk)."""

    sample_rate = SAMPLE_RATE
    chunk_samples = WAKE_CHUNK

    def __init__(self, fire_after_sample: int = 0) -> None:
        self._fire_after = int(fire_after_sample)
        self._fired = False
        super().__init__(threshold=0.5, cooldown_s=0.0)

    def _score_chunk(self, chunk):
        end = (self._chunks_processed + 1) * self.chunk_samples
        if not self._fired and end >= self._fire_after:
            self._fired = True
            return {"annotated_wake": 0.9}
        return {"annotated_wake": 0.0}

    def _reset_backend(self) -> None:
        self._fired = False


@dataclass
class EvalComponents:
    """Everything the evaluator needs, injectable so the tests never need a model."""

    vad: VadEngine
    stt: SttEngine
    segmenter_config: SegmenterConfig
    segmenter_source: Dict[str, Any]
    wake_phrases: Tuple[str, ...] = DEFAULT_WAKE_PHRASES
    detector_factory: Optional[Callable[[], WakeWordDetector]] = None
    startup: Dict[str, Any] = field(default_factory=dict)
    stt_direct_controls: bool = False
    clock: Callable[[], float] = time.perf_counter

    def __post_init__(self) -> None:
        self.timed_vad = TimedVad(self.vad, self.clock)
        self.capturing_stt = CapturingStt(self.stt)


# ---------------------------------------------------------------------------------------
# segmenter configuration
# ---------------------------------------------------------------------------------------

def segmenter_config_from_values(values: Dict[str, Any], vad_frame_samples: int) -> SegmenterConfig:
    unknown = sorted(set(values) - set(SEGMENTER_FIELDS))
    missing = sorted(set(SEGMENTER_FIELDS) - set(values))
    if unknown or missing:
        raise EvalInputError(f"segmenter config needs exactly {list(SEGMENTER_FIELDS)} (missing {missing}, unknown {unknown})")
    return SegmenterConfig(sample_rate=SAMPLE_RATE, vad_frame_samples=vad_frame_samples, **values)


def validate_segmenter_values(values: Dict[str, Any], vad_frame_samples: int = SILERO_MODEL.frame_samples) -> SegmenterConfig:
    """Validate the segmenter configuration BEFORE any model is loaded (the frame size is the Silero adapter's fixed contract)."""
    try:
        return segmenter_config_from_values(values, vad_frame_samples)
    except SegmenterConfigError as exc:
        numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values.values())
        detail = str(exc) if numeric else "a value has the wrong type"        # never echo a non-numeric value
        raise EvalInputError(f"the segmenter configuration is invalid: {detail}") from None


def config_fingerprint(values: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: values[k] for k in sorted(values)}, sort_keys=True).encode("utf-8")).hexdigest()


def load_segmenter_values(path: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(values, source). No file -> the uncalibrated evaluation defaults; a file is hashed so a frozen config is identifiable."""
    if path is None:
        values = dict(EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES)
        return values, {"kind": "EVAL_DEFAULT_UNCALIBRATED", "sha256": config_fingerprint(values), "frozen": False}
    p = Path(path)
    if not p.is_file():
        raise EvalInputError("segmenter config file not found")
    raw = p.read_bytes()
    try:
        values = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise EvalInputError("segmenter config file is not valid JSON") from None
    if not isinstance(values, dict):
        raise EvalInputError("segmenter config must be a JSON object")
    return values, {"kind": "FILE", "sha256": hashlib.sha256(raw).hexdigest(), "frozen": True}


def policy_conflicts(config: SegmenterConfig, stt: SttEngine) -> List[Dict[str, Any]]:
    """Configuration-policy conflicts to REPORT (never to fix silently): a segment longer than the STT backend accepts."""
    limit = getattr(stt, "max_audio_s", None)
    if limit is None:
        from speech.faster_whisper_backend import DEFAULT_MAX_AUDIO_S
        limit = DEFAULT_MAX_AUDIO_S
    conflicts = []
    if config.max_utterance_s > limit:
        conflicts.append({
            "code": "MAX_UTTERANCE_EXCEEDS_STT_LIMIT", "max_utterance_s": config.max_utterance_s, "stt_max_audio_s": limit,
            "effect": "a segment longer than the limit is refused by the STT backend and reported as STT_FAILED; nothing is truncated",
        })
    return conflicts


# ---------------------------------------------------------------------------------------
# evaluating one recording
# ---------------------------------------------------------------------------------------

ITEM_KEYS = (
    "schema_version", "recording_id", "split", "category", "item_status", "exclusion_reason", "provenance", "wake_mode",
    "activation_kind", "wake_detected", "pipeline_status", "activation_sample", "segment_outcome", "segment_start_sample",
    "segment_end_sample", "segment_duration_s", "audio_duration_s", "speech_annotation", "segmentation", "raw_stt_status",
    "stt_engine_id", "model_id", "reference_available", "reference_command_text", "decoded_text_raw", "pipeline_text",
    "wer", "wer_end_to_end", "cer", "cer_end_to_end", "wake_prefix", "hallucination_control", "false_activations",
    "extra_activations", "timings", "validation_label", "review_status", "error_class", "notes",
)


def validation_label(origin: str) -> str:
    """The label follows the declared origin. HARDWARE-VALIDATED and LIVE labels are deliberately not producible here."""
    if origin == "SYNTHETIC":
        return "SOFTWARE-VALIDATED (SYNTHETIC INPUT)"
    if origin in ("HARDWARE_ENGINEER_RECORDING", "PROJECT_MICROPHONE_RECORDING"):
        return "RECORDED-HARDWARE AUDIO"
    if origin == "TEAM_MEMBER_RECORDING":
        return "RECORDED AUDIO (DEVELOPMENT EVALUATION)"
    return "UNVERIFIED RECORDING ORIGIN"


def validate_item(item: Dict[str, Any]) -> None:
    """Raise EvalInputError unless `item` is a complete, internally consistent per-recording result (no PCM, no upgraded label)."""
    if set(item) != set(ITEM_KEYS):
        raise EvalInputError(f"item keys do not match the schema (missing {sorted(set(ITEM_KEYS) - set(item))}, extra {sorted(set(item) - set(ITEM_KEYS))})")
    if item["schema_version"] != SCHEMA_ITEM:
        raise EvalInputError("unsupported item schema_version")
    label = item["validation_label"]
    if "HARDWARE-VALIDATED" in label.upper() or "LIVE" in label.upper():
        raise EvalInputError("an evaluation item can never be HARDWARE-VALIDATED or LIVE")
    if item["item_status"] not in ITEM_STATUSES or item["review_status"] not in REVIEW_STATUSES:
        raise EvalInputError("item_status or review_status invalid")
    if item["item_status"] == "EXCLUDED":
        if item["exclusion_reason"] not in EXCLUSION_REASONS:
            raise EvalInputError("an excluded item needs a known exclusion_reason")
    else:
        if item["pipeline_status"] not in PIPELINE_STATUS_VALUES:
            raise EvalInputError(f"pipeline_status {item['pipeline_status']!r} is not a known status")
        if item["wake_prefix"]["outcome"] not in vm.WAKE_PREFIX_OUTCOMES:
            raise EvalInputError("wake_prefix outcome invalid")
        if not item["model_id"] and item["pipeline_status"] in STT_OUTPUT_STATUSES:
            raise EvalInputError("an item that ran STT must record the model id")

    def walk(value):
        if isinstance(value, (np.ndarray, bytes, bytearray)):
            raise EvalInputError("an evaluation item must not contain PCM / raw bytes")
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(item)


def _provenance(rec: Recording, audio_sha: Optional[str]) -> Dict[str, Any]:
    return {
        "speaker_code": rec.speaker_code, "recording_origin": rec.recording_origin, "consent_reference": rec.consent_reference,
        "microphone_or_device": rec.microphone_or_device, "distance_condition": rec.distance_condition,
        "noise_condition": rec.noise_condition, "expected_wake_phrase": rec.expected_wake_phrase,
        "audio_sha256": audio_sha, "conversion": rec.conversion,
    }


def _empty_timings() -> Dict[str, Any]:
    return {"pipeline_wall_ms": None, "vad_ms": None, "vad_frames": None, "stt_inference_ms": None, "stt_calls": 0,
            "stt_first_call_of_run": False, "decision_latency_s": None, "post_decision_ms": None, "audio_duration_s": None,
            "real_time_factor": None, "stt_real_time_factor": None}


def _base_item(rec: Recording, wake_mode: str, audio_sha: Optional[str]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_ITEM, "recording_id": rec.recording_id, "split": rec.split, "category": rec.category,
        "item_status": "EVALUATED", "exclusion_reason": None, "provenance": _provenance(rec, audio_sha), "wake_mode": wake_mode,
        "activation_kind": None, "wake_detected": False, "pipeline_status": "NO_ACTIVATION", "activation_sample": None,
        "segment_outcome": None, "segment_start_sample": None, "segment_end_sample": None, "segment_duration_s": None,
        "audio_duration_s": None,
        "speech_annotation": None if rec.speech_start_sample is None else {"start_sample": rec.speech_start_sample, "end_sample": rec.speech_end_sample},
        "segmentation": None, "raw_stt_status": None, "stt_engine_id": None, "model_id": None,
        "reference_available": bool(rec.reference_command_text), "reference_command_text": rec.reference_command_text,
        "decoded_text_raw": None, "pipeline_text": "", "wer": None, "wer_end_to_end": None, "cer": None, "cer_end_to_end": None,
        "wake_prefix": {"outcome": "NOT_APPLICABLE", "prefix_removed": False, "extra_words_removed": 0},
        "hallucination_control": {"is_control": rec.is_non_speech_control, "valid": False, "hallucinated": None, "invalid_reason": None,
                                  "stt_direct": None},
        "false_activations": None, "extra_activations": 0, "timings": _empty_timings(),
        "validation_label": validation_label(rec.recording_origin), "review_status": rec.human_review, "error_class": None, "notes": rec.notes,
    }


def _excluded(rec: Recording, wake_mode: str, reason: str, audio_sha: Optional[str] = None) -> Dict[str, Any]:
    item = _base_item(rec, wake_mode, audio_sha)
    item["item_status"] = "EXCLUDED"
    item["exclusion_reason"] = reason
    item["pipeline_status"] = None
    item["wake_prefix"] = {"outcome": "NOT_APPLICABLE", "prefix_removed": False, "extra_words_removed": 0}
    item["hallucination_control"] = {"is_control": rec.is_non_speech_control, "valid": False, "hallucinated": None, "invalid_reason": reason, "stt_direct": None}
    logger.info("event=week7_eval_item recording_id=%s category=%s split=%s item_status=EXCLUDED reason=%s", rec.recording_id, rec.category, rec.split, reason)
    return item


def plan_activation(rec: Recording, wake_mode: str, detector_available: bool) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """(activation_kind, fire_after_sample, exclusion_reason). kind: detector | annotated | start."""
    if rec.is_non_speech_control:
        return "start", 0, None                                  # controls always stress the VAD and the STT
    if wake_mode == "start":
        return "start", 0, None
    if wake_mode == "detector":
        if rec.category == "CLEAN_COMMAND" and not rec.expected_wake_phrase:
            return "start", 0, None                              # no wake phrase was spoken: nothing for a detector to find
        if not detector_available:
            return None, None, "DETECTOR_UNAVAILABLE"
        return "detector", None, None
    # annotated
    if rec.category == "SPEECH_NO_WAKE":
        return "start", 0, None
    if rec.wake_end_sample is not None:
        return "annotated", rec.wake_end_sample, None
    if rec.category in WAKE_REQUIRED_CATEGORIES:
        return None, None, "WAKE_ANNOTATION_MISSING"
    return "start", 0, None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.int16)


def evaluate_recording(rec: Recording, comps: EvalComponents, wake_mode: str) -> Dict[str, Any]:
    """Run one recording through the real pipeline and return its structured, PCM-free evidence item."""
    path = rec.audio_file
    if not path.is_file():
        return _excluded(rec, wake_mode, "AUDIO_MISSING")
    audio_sha = _sha256_file(path)
    if rec.audio_sha256 is not None and rec.audio_sha256 != audio_sha:
        return _excluded(rec, wake_mode, "AUDIO_HASH_MISMATCH", audio_sha)
    try:
        probe = WavFileSource(path)                       # the project's own native-format validation; nothing is resampled
    except ValueError:
        return _excluded(rec, wake_mode, "NON_NATIVE_FORMAT", audio_sha)
    kind, fire_after, reason = plan_activation(rec, wake_mode, comps.detector_factory is not None)
    if reason is not None:
        return _excluded(rec, wake_mode, reason, audio_sha)

    item = _base_item(rec, wake_mode, audio_sha)
    item["activation_kind"] = kind
    audio_duration_s = probe.duration_s
    item["audio_duration_s"] = audio_duration_s
    comps.timed_vad.reset_counters()
    comps.capturing_stt.reset_item()
    calls_before = comps.capturing_stt.total_calls
    results: List[PipelineResult] = []
    began = comps.clock()
    try:
        detector = comps.detector_factory() if kind == "detector" else AnnotatedWakeDetector(fire_after or 0)
        pipeline = VoicePipeline(
            detector, UtteranceSegmenter(comps.segmenter_config, comps.timed_vad), comps.capturing_stt,
            wake_phrases=comps.wake_phrases, clock=comps.clock,
        )
        results = pipeline.run_source(WavFileSource(path))
    except FATAL_ERRORS as exc:                           # the run itself can no longer be trusted: stop, keep what is finished
        raise EvalFatalError(type(exc).__name__) from exc
    except Exception as exc:                              # noqa: BLE001 - a SAFE per-recording failure: recorded on this item (class name only)
        item["pipeline_status"] = "PIPELINE_ERROR"
        item["error_class"] = type(exc).__name__
    wall_ms = (comps.clock() - began) * 1000.0
    if results and results[0].error_class in _FATAL_CLASS_NAMES:      # e.g. an STT contract violation reported as STT_FAILED
        raise EvalFatalError(results[0].error_class)

    stt_calls = list(comps.capturing_stt.calls)
    raw_ok = next((c for c in stt_calls if "raw_text" in c), None)
    timings = item["timings"]
    timings.update({
        "pipeline_wall_ms": wall_ms, "vad_ms": comps.timed_vad.total_s * 1000.0, "vad_frames": comps.timed_vad.frames,
        "stt_calls": len(stt_calls), "stt_first_call_of_run": calls_before == 0 and len(stt_calls) > 0,
        "audio_duration_s": audio_duration_s, "real_time_factor": wall_ms / (audio_duration_s * 1000.0) if audio_duration_s > 0 else None,
    })
    if raw_ok is not None:
        timings["stt_inference_ms"] = raw_ok["inference_ms"]

    if results:
        r = results[0]
        item["wake_detected"] = True
        item["extra_activations"] = len(results) - 1
        item["pipeline_status"] = r.status.value
        item["activation_sample"] = r.activation_sample
        item["segment_outcome"] = r.segment_outcome.value if r.segment_outcome else None
        item["segment_start_sample"], item["segment_end_sample"] = r.segment_start_sample, r.segment_end_sample
        item["segment_duration_s"] = r.audio_duration_s
        item["raw_stt_status"] = r.raw_stt_status.value if r.raw_stt_status else None
        item["stt_engine_id"], item["model_id"] = r.stt_engine_id, r.model_id
        item["pipeline_text"] = r.normalized_text
        item["error_class"] = r.error_class
        timings["decision_latency_s"] = r.timings.decision_latency_s
        timings["post_decision_ms"] = r.timings.post_decision_ms
    if raw_ok is not None:
        item["decoded_text_raw"] = raw_ok["raw_text"]
    if item["segment_duration_s"] and timings["stt_inference_ms"] is not None:
        timings["stt_real_time_factor"] = timings["stt_inference_ms"] / (item["segment_duration_s"] * 1000.0)

    status = item["pipeline_status"]
    _fill_text_metrics(rec, item, status, results[0] if results else None, comps)
    _fill_segmentation(rec, item)
    _fill_hallucination(rec, item, status, comps, path, audio_duration_s)
    if wake_mode == "detector" and rec.category not in WAKE_REQUIRED_CATEGORIES and rec.category != "CLEAN_COMMAND" and comps.detector_factory is not None:
        try:
            item["false_activations"] = len(run_on_source(WavFileSource(path), comps.detector_factory()))
        except Exception as exc:                          # noqa: BLE001
            item["error_class"] = item["error_class"] or type(exc).__name__
    validate_item(item)
    logger.info(
        "event=week7_eval_item recording_id=%s category=%s split=%s item_status=EVALUATED pipeline_status=%s wake_mode=%s wall_ms=%.1f audio_duration_s=%.3f stt_calls=%d",
        rec.recording_id, rec.category, rec.split, status, wake_mode, wall_ms, audio_duration_s, len(stt_calls),
    )
    return item


def _fill_text_metrics(rec: Recording, item: Dict[str, Any], status: str, result: Optional[PipelineResult], comps: EvalComponents) -> None:
    raw_text = item["decoded_text_raw"]
    expected_words = len(rec.expected_wake_phrase.split()) or min(len(p.split()) for p in comps.wake_phrases)
    removed = bool(result.wake_prefix_removed) if result else False
    item["wake_prefix"] = {
        "outcome": vm.classify_wake_prefix(rec.expected_wake_phrase, raw_text, removed) if status in STT_OUTPUT_STATUSES else "NOT_APPLICABLE",
        "prefix_removed": removed,
        "extra_words_removed": vm.extra_words_removed(raw_text, item["pipeline_text"], removed, expected_words) if status in STT_OUTPUT_STATUSES else 0,
    }
    wer_category = rec.category in COMMAND_CATEGORIES or (rec.category == "SPEECH_NO_WAKE" and item["activation_kind"] != "detector")
    if not (rec.reference_command_text and wer_category):
        return
    hypothesis_e2e = item["pipeline_text"] if status in TEXT_STATUSES else ""
    item["wer_end_to_end"] = vm.word_errors(rec.reference_command_text, hypothesis_e2e).as_dict()
    item["cer_end_to_end"] = vm.char_errors(rec.reference_command_text, hypothesis_e2e).as_dict()
    if status in STT_OUTPUT_STATUSES:                     # ONLY items where STT produced a transcript are in the decoded denominator
        item["wer"] = vm.word_errors(rec.reference_command_text, item["pipeline_text"]).as_dict()
        item["cer"] = vm.char_errors(rec.reference_command_text, item["pipeline_text"]).as_dict()


def _fill_segmentation(rec: Recording, item: Dict[str, Any]) -> None:
    seg = {"command_expected": rec.category in COMMAND_CATEGORIES, "captured": item["segment_start_sample"] is not None,
           "no_speech_false_negative": False, "false_speech_trigger": False}
    if rec.category in COMMAND_CATEGORIES and item["pipeline_status"] == "NO_SPEECH":
        seg["no_speech_false_negative"] = True            # a command clip the VAD reported as containing no speech
    if rec.is_non_speech_control and item["segment_start_sample"] is not None:
        seg["false_speech_trigger"] = True                # a segment was produced from a clip that holds no speech
    if rec.speech_start_sample is not None and item["segment_start_sample"] is not None:
        seg.update(vm.segment_geometry(item["segment_start_sample"], item["segment_end_sample"], rec.speech_start_sample, rec.speech_end_sample, SAMPLE_RATE))
    item["segmentation"] = seg


def _fill_hallucination(rec: Recording, item: Dict[str, Any], status: str, comps: EvalComponents, path: Path, duration_s: float) -> None:
    h = item["hallucination_control"]
    if not rec.is_non_speech_control:
        return                                            # speech clips are NEVER hallucination-control samples
    if status in UNJUDGEABLE_CONTROL_STATUSES:
        h["valid"], h["invalid_reason"] = False, status
    else:
        h["valid"] = True
        h["hallucinated"] = item["raw_stt_status"] == SttStatus.OK.value
    if comps.stt_direct_controls:
        limit = getattr(comps.stt, "max_audio_s", None)
        if limit is not None and duration_s > limit:
            h["stt_direct"] = {"ran": False, "reason": "CLIP_LONGER_THAN_STT_LIMIT"}
        else:
            try:
                direct = comps.stt.transcribe(_read_pcm(path), SAMPLE_RATE)
                h["stt_direct"] = {"ran": True, "hallucinated": direct.status is SttStatus.OK, "decoded_text": direct.text}
            except SttError as exc:
                h["stt_direct"] = {"ran": False, "reason": type(exc).__name__}
            except (OSError, EOFError, wave.Error) as exc:             # the clip could not be read: this control is unjudged, the run goes on
                h["stt_direct"] = {"ran": False, "reason": type(exc).__name__}


# ---------------------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------------------

def _sum_errors(dicts: Sequence[Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    return vm.aggregate_errors([vm.ErrorCount.from_dict(d) for d in dicts if d is not None])


def aggregate_items(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    evaluated = [i for i in items if i["item_status"] == "EVALUATED"]
    excluded = [i for i in items if i["item_status"] == "EXCLUDED"]
    n = len(evaluated)
    status_counts = Counter(i["pipeline_status"] for i in evaluated)
    commands = [i for i in evaluated if i["category"] in COMMAND_CATEGORIES]
    controls = [i for i in evaluated if i["hallucination_control"]["is_control"]]
    valid_controls = [i for i in controls if i["hallucination_control"]["valid"]]
    hallucinated = [i for i in valid_controls if i["hallucination_control"]["hallucinated"]]
    direct = [i for i in controls if i["hallucination_control"]["stt_direct"] and i["hallucination_control"]["stt_direct"].get("ran")]
    prefix = Counter(i["wake_prefix"]["outcome"] for i in evaluated)
    stt_items = [i for i in evaluated if i["pipeline_status"] in STT_OUTPUT_STATUSES]
    steady = [i for i in evaluated if i["timings"]["stt_inference_ms"] is not None and not i["timings"]["stt_first_call_of_run"]]
    geometry = [i["segmentation"] for i in evaluated if i["segmentation"] and "start_offset_ms" in i["segmentation"]]
    wake_required = [i for i in evaluated if i["category"] in WAKE_REQUIRED_CATEGORIES]
    detector_mode = any(i["wake_mode"] == "detector" for i in evaluated)
    negatives = [i for i in evaluated if i["false_activations"] is not None]
    return {
        "corpus": {
            "items": len(items), "evaluated": n, "excluded": len(excluded),
            "exclusion_reasons": dict(Counter(i["exclusion_reason"] for i in excluded)),
            "by_split": dict(Counter(i["split"] for i in items)), "by_category": dict(Counter(i["category"] for i in items)),
            "by_origin": dict(Counter(i["provenance"]["recording_origin"] for i in items)),
            "audio_seconds": sum(i["audio_duration_s"] or 0.0 for i in evaluated),
        },
        "status_distribution": {
            "counts": {s: status_counts.get(s, 0) for s in PIPELINE_STATUS_VALUES},
            "rates": {s: vm.rate(status_counts.get(s, 0), n) for s in PIPELINE_STATUS_VALUES},
            "denominator": n,
            "by_category": {c: dict(Counter(i["pipeline_status"] for i in evaluated if i["category"] == c)) for c in CATEGORIES},
        },
        "command_metrics": {
            "command_items": len(commands),
            "command_capture_rate": vm.rate(sum(i["pipeline_status"] in TEXT_STATUSES for i in commands), len(commands)),
            "too_short_rate": vm.rate(sum(i["pipeline_status"] == "TOO_SHORT" for i in commands), len(commands)),
            "wake_only_rate": vm.rate(sum(i["pipeline_status"] == "WAKE_ONLY" for i in commands), len(commands)),
            "no_speech_false_negatives": sum(bool(i["segmentation"] and i["segmentation"]["no_speech_false_negative"]) for i in commands),
        },
        "wer": {"decoded": _sum_errors([i["wer"] for i in evaluated]), "end_to_end": _sum_errors([i["wer_end_to_end"] for i in evaluated]),
                "decoded_denominator_note": "only items where STT produced a transcript; capture failures are excluded here and appear in end_to_end"},
        "cer": {"decoded": _sum_errors([i["cer"] for i in evaluated]), "end_to_end": _sum_errors([i["cer_end_to_end"] for i in evaluated])},
        "hallucination": {
            "non_speech_controls": len(controls), "valid_controls": len(valid_controls), "invalid_controls": len(controls) - len(valid_controls),
            "hallucinated": len(hallucinated),
            "hallucination_control_rate": vm.rate(len(hallucinated), len(valid_controls)),
            "by_category": {c: {"valid": sum(1 for i in valid_controls if i["category"] == c),
                                "hallucinated": sum(1 for i in hallucinated if i["category"] == c)} for c in NON_SPEECH_CATEGORIES},
            "stt_direct": {"controls_run": len(direct),
                           "hallucinated": sum(bool(i["hallucination_control"]["stt_direct"]["hallucinated"]) for i in direct),
                           "rate": vm.rate(sum(bool(i["hallucination_control"]["stt_direct"]["hallucinated"]) for i in direct), len(direct))},
            "definition": "non-speech controls with non-empty decoded text / valid non-speech controls (speech clips never enter this metric)",
        },
        "false_speech_trigger": {
            "valid_controls": len(valid_controls),
            "controls_with_a_segment": sum(bool(i["segmentation"] and i["segmentation"]["false_speech_trigger"]) for i in valid_controls),
            "rate": vm.rate(sum(bool(i["segmentation"] and i["segmentation"]["false_speech_trigger"]) for i in valid_controls), len(valid_controls)),
        },
        "wake_prefix": {
            "outcomes": {o: prefix.get(o, 0) for o in vm.WAKE_PREFIX_OUTCOMES},
            "items_with_extra_words_removed": sum(i["wake_prefix"]["extra_words_removed"] > 0 for i in evaluated),
            "wake_only": {"count": status_counts.get("WAKE_ONLY", 0),
                          "with_a_reference_command": sum(1 for i in stt_items if i["pipeline_status"] == "WAKE_ONLY" and i["reference_available"])},
        },
        "wake_detection": ({
            "measured": True,
            "wake_required_items": len(wake_required),
            "missed_wakes": sum(i["pipeline_status"] == "NO_ACTIVATION" for i in wake_required),
            "missed_wake_rate": vm.rate(sum(i["pipeline_status"] == "NO_ACTIVATION" for i in wake_required), len(wake_required)),
            "negative_items": len(negatives), "negative_items_with_false_activation": sum(i["false_activations"] > 0 for i in negatives),
            "false_activations": sum(i["false_activations"] for i in negatives),
        } if detector_mode else {"measured": False, "reason": "wake was SIMULATED (annotated / start mode): wake accuracy is NOT MEASURED"}),
        "segmentation": {
            "items_with_annotation": len(geometry),
            "start_offset_ms": vm.distribution(g["start_offset_ms"] for g in geometry),
            "end_offset_ms": vm.distribution(g["end_offset_ms"] for g in geometry),
            "speech_covered_fraction": vm.distribution(g["speech_covered_fraction"] for g in geometry),
            "segment_duration_s": vm.distribution(i["segment_duration_s"] for i in evaluated),
            "note": "annotation-based; NOT frame-level VAD accuracy (no frame-level ground truth exists)",
        },
        "latency": {
            "pipeline_wall_ms": vm.distribution(i["timings"]["pipeline_wall_ms"] for i in evaluated),
            "vad_ms": vm.distribution(i["timings"]["vad_ms"] for i in evaluated),
            "stt_inference_ms_steady_state": vm.distribution(i["timings"]["stt_inference_ms"] for i in steady),
            "decision_latency_s": vm.distribution(i["timings"]["decision_latency_s"] for i in evaluated),
            "real_time_factor": vm.distribution(i["timings"]["real_time_factor"] for i in evaluated),
            "stt_real_time_factor": vm.distribution(i["timings"]["stt_real_time_factor"] for i in steady),
            "note": "development PC only; the first STT inference of a run is reported under startup, not here",
        },
        "human_review": {s: sum(1 for i in evaluated if i["review_status"] == s) for s in REVIEW_STATUSES},
    }


# ---------------------------------------------------------------------------------------
# running a manifest, building the report
# ---------------------------------------------------------------------------------------

def check_split_request(splits: Sequence[str], allow_heldout: bool, segmenter_source: Dict[str, Any]) -> None:
    for s in splits:
        if s not in SPLITS:
            raise EvalInputError(f"unknown split {s!r}; allowed {SPLITS}")
    if "HELDOUT" in splits:
        if not allow_heldout:
            raise EvalInputError("HELDOUT is evaluated only with --allow-heldout: it is a final descriptive check and must NEVER be used to tune thresholds, timings, prefix rules or STT settings")
        if not segmenter_source.get("frozen"):
            raise EvalInputError("a HELDOUT run needs an explicit, frozen --segmenter-config file: the uncalibrated evaluation defaults are not accepted for the final check")


def evaluate_manifest(manifest: Manifest, comps: EvalComponents, *, wake_mode: str = "annotated",
                      splits: Sequence[str] = ("DEV",), allow_heldout: bool = False,
                      on_item: Optional[Callable[[Dict[str, Any]], None]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Preflight the selected manifest, then evaluate it. `on_item` is called with each finished item (for incremental evidence).

    Raises EvalInputError (nothing was evaluated) or EvalFatalError (items finished so far and an INCOMPLETE report are attached).
    """
    if wake_mode not in WAKE_MODES:
        raise EvalInputError(f"wake_mode must be one of {WAKE_MODES}")
    if manifest.template:
        raise EvalInputError("a TEMPLATE manifest cannot be evaluated")
    check_split_request(splits, allow_heldout, comps.segmenter_source)
    selected = [r for r in manifest.recordings if r.split in splits]
    preflight_manifest(selected, wake_mode=wake_mode, detector_available=comps.detector_factory is not None)
    started = datetime.now(timezone.utc)
    logger.info("event=week7_eval_start corpus_id=%s recordings=%d selected=%d splits=%s wake_mode=%s",
                manifest.corpus_id, len(manifest.recordings), len(selected), ",".join(splits), wake_mode)
    items: List[Dict[str, Any]] = []
    try:
        for rec in selected:
            try:
                item = evaluate_recording(rec, comps, wake_mode)
            except EvalFatalError:
                raise
            except Exception as exc:                      # noqa: BLE001 - an unexpected internal error: the run is no longer trusted
                raise EvalFatalError(type(exc).__name__) from exc
            items.append(item)
            if on_item is not None:
                on_item(item)
    except EvalFatalError as fatal:
        fatal.items = items
        fatal.report = build_report(manifest, items, comps, wake_mode=wake_mode, splits=splits, started=started, selected=selected,
                                    run_status="INCOMPLETE_FATAL", fatal_error_class=fatal.error_class)
        logger.error("event=week7_eval_aborted error=%s items_finished=%d items_selected=%d", fatal.error_class, len(items), len(selected))
        raise
    report = build_report(manifest, items, comps, wake_mode=wake_mode, splits=splits, started=started, selected=selected)
    logger.info("event=week7_eval_done evaluated=%d excluded=%d", report["corpus"]["evaluated"], report["corpus"]["excluded"])
    return items, report


def _model_info(comps: EvalComponents) -> Dict[str, Any]:
    vad = comps.vad
    spec = getattr(vad, "model_spec", None)
    stt = comps.stt
    info = {
        "vad": {
            "class": type(vad).__name__, "model": getattr(spec, "filename", None), "approved_sha256": getattr(spec, "sha256", None),
            "verified_sha256": getattr(vad, "verified_sha256", None), "artifact_verified": getattr(vad, "artifact_verified", None),
            "frame_samples": vad.frame_samples,
        },
        "stt": {
            "class": type(stt).__name__, "engine_id": stt.engine_id, "model_id": stt.model_id,
            "snapshot_verified": getattr(stt, "snapshot_verified", None), "max_audio_s": getattr(stt, "max_audio_s", None),
        },
    }
    try:
        from speech.faster_whisper_backend import COMPUTE_TYPE, DECODER_OPTIONS, DEVICE
        if stt.engine_id == "faster-whisper":
            info["stt"].update({"decoder_options": dict(DECODER_OPTIONS), "device": DEVICE, "compute_type": COMPUTE_TYPE})
    except ImportError:
        pass
    return info


def build_report(manifest: Manifest, items: Sequence[Dict[str, Any]], comps: EvalComponents, *, wake_mode: str, splits: Sequence[str],
                 started: datetime, selected: Sequence[Recording], run_status: str = "COMPLETE",
                 fatal_error_class: Optional[str] = None) -> Dict[str, Any]:
    real = [i for i in items if i["provenance"]["recording_origin"] != "SYNTHETIC"]
    synthetic = [i for i in items if i["provenance"]["recording_origin"] == "SYNTHETIC"]
    values = {f: getattr(comps.segmenter_config, f) for f in SEGMENTER_FIELDS}
    first_call = next((i for i in items if i["timings"]["stt_first_call_of_run"]), None)
    kind = "REAL_RECORDINGS" if real and not synthetic else "SYNTHETIC_ONLY" if synthetic and not real else "MIXED" if items else "EMPTY"
    complete = run_status == "COMPLETE"
    report = {
        "schema_version": SCHEMA_REPORT, "evaluator_label": EVALUATOR_LABEL,
        "run_status": run_status, "fatal_error_class": fatal_error_class,
        "items_finished": len(items), "items_selected": len(selected),
        "run_started_utc": started.isoformat(), "corpus_id": manifest.corpus_id, "manifest_sha256": manifest.sha256,
        "corpus": {**aggregate_items(items)["corpus"], "not_run_by_split": dict(Counter(r.split for r in manifest.recordings if r.split not in splits))},
        "corpus_kind": kind,
        "accuracy_results": ({"split_scope": list(splits), "overall": aggregate_items(real),
                              "by_split": {s: aggregate_items([i for i in real if i["split"] == s]) for s in splits}}
                             if real and complete else None),
        "accuracy_results_note": ("measured on REAL recordings only" if real and complete else
                                  "INCOMPLETE RUN (fatal failure): no result in this report is final and none may be quoted" if not complete else
                                  "NO REAL RECORDINGS EVALUATED: there is NO Week 7 accuracy result in this report"),
        "synthetic_plumbing_only": ({"label": "PLUMBING / SCHEMA / ARITHMETIC ONLY - NOT AN ACCURACY RESULT", "aggregate": aggregate_items(synthetic)}
                                    if synthetic else None),
        "split_policy": {
            "splits_evaluated": list(splits), "calibration_allowed_splits": ["DEV"],
            "heldout_evaluated": "HELDOUT" in splits,
            "heldout_warning": ("HELDOUT was evaluated: these numbers are a final descriptive check and MUST NOT be used to tune VAD thresholds, "
                                "segmentation timings, wake-prefix rules or STT settings") if "HELDOUT" in splits else None,
        },
        "configuration": {
            "wake_mode": wake_mode, "wake_simulated": wake_mode != "detector", "wake_phrases": list(comps.wake_phrases),
            "segmenter": {"values": values, "sha256": config_fingerprint(values), "source": comps.segmenter_source},
            "normalization": vm.NORMALIZATION_ID, "stt_direct_controls": comps.stt_direct_controls,
        },
        "models": _model_info(comps),
        "policy_conflicts": policy_conflicts(comps.segmenter_config, comps.stt),
        "startup": {**comps.startup, "stt_first_inference_ms": first_call["timings"]["stt_inference_ms"] if first_call else None,
                    "note": "construction (hash verification + load) and the first inference are reported here, separately from steady-state inference; development PC only"},
        "corpus_warnings": corpus_warnings(selected),
        "limitations": list(LIMITATIONS),
        "claims": {"hardware_validated": False, "live_validated": False, "raspberry_pi_measured": False,
                   "week7_complete": False, "task5_complete": False},
    }
    return report


# ---------------------------------------------------------------------------------------
# evidence output
# ---------------------------------------------------------------------------------------

def prepare_output_dir(out_dir, *, contains_real: bool, names: Sequence[str]) -> Path:
    """Validate (and create) the output directory. Real evidence must be OUTSIDE the repository; nothing existing is overwritten."""
    out = Path(out_dir).expanduser()
    if contains_real:
        try:
            assert_outside_repo(out)
        except IngestError:
            raise EvalEvidenceError("evidence for real or possibly-human recordings must be written outside the source repository") from None
    resolved = out.resolve()
    for name in names:
        if (resolved / name).exists():
            raise EvalEvidenceError(f"{name} already exists in the output directory and will not be overwritten; choose a new output directory")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvalEvidenceError(f"cannot create the output directory ({type(exc).__name__})") from exc
    return resolved


def _write_exclusive(path: Path, text: str) -> None:
    try:
        with open(path, "x", encoding="utf-8") as handle:        # "x": never overwrite existing evidence
            handle.write(text)
    except FileExistsError:
        raise EvalEvidenceError(f"{path.name} already exists and will not be overwritten") from None


ITEMS_FILENAME = "week7_eval_items.jsonl"
REPORT_FILENAME = "week7_eval_report.json"
ITEMS_PARTIAL = ITEMS_FILENAME + ".partial"
REPORT_PARTIAL = REPORT_FILENAME + ".partial"
REPORT_INCOMPLETE = "week7_eval_report.INCOMPLETE.json"
_EVIDENCE_NAMES = (ITEMS_FILENAME, REPORT_FILENAME, ITEMS_PARTIAL, REPORT_PARTIAL, REPORT_INCOMPLETE)


def _dump_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


class EvidenceWriter:
    """Incremental, honest evidence output.

    Items are appended to week7_eval_items.jsonl.partial as they finish, so a crash loses at most the item in flight. The final
    names week7_eval_items.jsonl and week7_eval_report.json appear ONLY from finish(), after the run ended normally, and the report is
    renamed last: a directory without week7_eval_report.json is not a complete run. abort() writes week7_eval_report.INCOMPLETE.json
    and leaves the items as .partial, so partial output can never be mistaken for a finished evaluation.
    """

    def __init__(self, out_dir, *, contains_real: bool) -> None:
        self.dir = prepare_output_dir(out_dir, contains_real=contains_real, names=_EVIDENCE_NAMES)
        self._handle = open(self.dir / ITEMS_PARTIAL, "x", encoding="utf-8")        # "x": never overwrite existing evidence
        self.count = 0

    def add(self, item: Dict[str, Any]) -> None:
        self._handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        self._handle.flush()
        self.count += 1

    def finish(self, report: Dict[str, Any]) -> Tuple[Path, Path]:
        if report.get("run_status") != "COMPLETE":
            raise EvalEvidenceError("only a COMPLETE run can be finished; use abort() for an incomplete one")
        self._handle.close()
        _write_exclusive(self.dir / REPORT_PARTIAL, _dump_report(report))
        os.rename(self.dir / ITEMS_PARTIAL, self.dir / ITEMS_FILENAME)
        os.rename(self.dir / REPORT_PARTIAL, self.dir / REPORT_FILENAME)                # last: its presence marks a complete run
        return self.dir / ITEMS_FILENAME, self.dir / REPORT_FILENAME

    def discard(self) -> None:
        """Nothing was evaluated (the request was rejected): remove the empty partial file again."""
        self._handle.close()
        if self.count == 0:
            (self.dir / ITEMS_PARTIAL).unlink()

    def abort(self, report: Dict[str, Any]) -> Tuple[Path, Path]:
        self._handle.close()
        _write_exclusive(self.dir / REPORT_INCOMPLETE, _dump_report(report))
        return self.dir / ITEMS_PARTIAL, self.dir / REPORT_INCOMPLETE


def write_outputs(out_dir, items: Sequence[Dict[str, Any]], report: Dict[str, Any], *, contains_real: bool) -> Tuple[Path, Path]:
    writer = EvidenceWriter(out_dir, contains_real=contains_real)
    for item in items:
        writer.add(item)
    return writer.finish(report)


# ---------------------------------------------------------------------------------------
# real components + CLI
# ---------------------------------------------------------------------------------------

def build_real_components(segmenter_values: Dict[str, Any], segmenter_source: Dict[str, Any], *, wake_mode: str,
                          stt_direct_controls: bool = False) -> EvalComponents:
    """Construct the real Silero VAD and faster-whisper engines, timing their construction separately."""
    from audio.silero_vad import SileroOnnxVadEngine
    from speech.faster_whisper_backend import FasterWhisperEngine

    began = time.perf_counter()
    vad = SileroOnnxVadEngine()
    vad_ms = (time.perf_counter() - began) * 1000.0
    began = time.perf_counter()
    stt = FasterWhisperEngine()
    stt_ms = (time.perf_counter() - began) * 1000.0
    detector_factory = None
    if wake_mode == "detector":
        from wakeword.openwakeword_backend import OpenWakeWordDetector
        OpenWakeWordDetector()                                  # fail now (clean error) if the wake engine is unavailable, not on the first clip
        detector_factory = OpenWakeWordDetector
    return EvalComponents(
        vad=vad, stt=stt, segmenter_config=segmenter_config_from_values(segmenter_values, vad.frame_samples),
        segmenter_source=segmenter_source, detector_factory=detector_factory, stt_direct_controls=stt_direct_controls,
        startup={
            "silero_construct_ms": vad_ms, "silero_note": "SHA-256 verification + ONNX session creation (not separable)",
            "whisper_construct_ms": stt_ms, "whisper_model_load_ms": stt.load_ms,
            "whisper_verification_ms": None if stt.load_ms is None else max(0.0, stt_ms - stt.load_ms),
        },
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evaluation.week7_voice_eval", description=EVALUATOR_LABEL,
        epilog="exit codes: 0 finished; 2 invalid manifest / configuration / environment / output location (nothing evaluated); "
               "3 fatal failure during the evaluation (partial INCOMPLETE evidence was written)",
    )
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="check a manifest (schema, consent, locations) without running any model")
    v.add_argument("--manifest", required=True)
    v.add_argument("--allow-template", action="store_true", help="check the structure of the repository template")
    r = sub.add_parser("run", help="evaluate the manifest's recordings and write evidence OUTSIDE the repository")
    r.add_argument("--manifest", required=True)
    r.add_argument("--output", required=True, help="new directory for the item evidence and the aggregate report")
    r.add_argument("--wake-mode", choices=WAKE_MODES, default="annotated")
    r.add_argument("--split", choices=("DEV", "HELDOUT", "ALL"), default="DEV")
    r.add_argument("--allow-heldout", action="store_true")
    r.add_argument("--segmenter-config", default=None, help="JSON file with the nine segmenter values (frozen, hashed); default: uncalibrated evaluation values")
    r.add_argument("--stt-direct-controls", action="store_true", help="also run STT on whole non-speech control clips (bypasses the VAD)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest, allow_template=getattr(args, "allow_template", False))
        if args.command == "validate":
            print(f"manifest OK: {len(manifest.recordings)} recording(s), corpus_id={manifest.corpus_id}, template={manifest.template}")
            for w in corpus_warnings(manifest.recordings):
                print("WARNING", w)
            return 0
        splits = SPLITS if args.split == "ALL" else (args.split,)
        values, source = load_segmenter_values(args.segmenter_config)
        validate_segmenter_values(values)                                   # cheap checks first: nothing below loads a model
        check_split_request(splits, args.allow_heldout, source)
        preflight_manifest([r for r in manifest.recordings if r.split in splits], wake_mode=args.wake_mode, detector_available=True)
        prepare_output_dir(args.output, contains_real=manifest.has_real_recordings, names=_EVIDENCE_NAMES)
        comps = build_real_components(values, source, wake_mode=args.wake_mode, stt_direct_controls=args.stt_direct_controls)
        writer = EvidenceWriter(args.output, contains_real=manifest.has_real_recordings)
        try:
            items, report = evaluate_manifest(manifest, comps, wake_mode=args.wake_mode, splits=splits, allow_heldout=args.allow_heldout,
                                              on_item=writer.add)
        except EvalFatalError as fatal:
            partial, incomplete = writer.abort(fatal.report)
            print(f"FATAL: {fatal}\nitems finished so far (PARTIAL): {partial}\nINCOMPLETE report: {incomplete}", file=sys.stderr)
            return 3
        except EvalInputError:
            writer.discard()                                                # rejected before anything was evaluated
            raise
        except BaseException:
            writer.abort({"run_status": "INCOMPLETE_FATAL", "fatal_error_class": "UnhandledInterruption", "items_finished": writer.count})
            raise
        items_path, report_path = writer.finish(report)
    except (EvalInputError, SegmenterConfigError, SttError, SileroVadError, WakeWordBackendError, OSError) as exc:
        # expected user / environment mistakes (a bad manifest or config, a missing model, an unusable output location): a clean error, no traceback
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"evaluated {report['corpus']['evaluated']} / excluded {report['corpus']['excluded']}; corpus_kind={report['corpus_kind']}")
    print(f"items:  {items_path}\nreport: {report_path}")
    if report["accuracy_results"] is None:
        print("NOTE: no real recordings were evaluated; this report contains no Week 7 accuracy result.")
    for c in report["policy_conflicts"]:
        print("POLICY CONFLICT:", c["code"], c["effect"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
