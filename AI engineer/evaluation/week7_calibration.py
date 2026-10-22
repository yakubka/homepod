"""Week 7 Task 5 - DEV-ONLY segmentation parameter sweep SUPPORT (VESTA).

STATUS: machinery READY, NEVER RUN. No threshold has been tuned and none has been chosen. config/settings.py is not touched
and this module never writes to it. Running a sweep needs a real, consented DEV corpus; on synthetic fixtures it only
demonstrates the plumbing.

    base segmenter values + axes {name: [candidate values]}  ->  one configuration per combination
    for each configuration: evaluate every DEV recording with the real pipeline (evaluation/week7_voice_eval.py)
    -> one TRADEOFF ROW per configuration

Why a table and not a winner. One number optimised blindly (for example WER) can hide a worse system: a stricter start
threshold lowers false speech triggers and the hallucination rate but misses quiet commands; a longer end silence keeps a
command together but adds latency and merges old speech; a larger maximum utterance captures long commands but can exceed
the 30 s STT limit. Each row therefore carries, side by side: command capture rate, false speech trigger rate, TOO_SHORT rate,
hallucination-control rate, WER (decoded and end-to-end) and latency. NO "best" configuration is selected here: that is a
human decision on DEV evidence, followed by one frozen config file and one HELDOUT descriptive check.

SPLIT ENFORCEMENT. run_sweep() raises CalibrationSplitError when handed any recording that is not DEV; the CLI selects DEV rows
only, reports how many HELDOUT rows it ignored, and has no switch to include them.

POLICY CONFLICTS. A candidate with max_utterance_s above the STT backend's limit is NOT run: it is listed under `skipped` with
the code MAX_UTTERANCE_EXCEEDS_STT_LIMIT (nothing is silently truncated). Combinations the segmenter rejects are listed with
the reason.

CLI. Exit codes: 0 finished; 2 invalid manifest / grid / configuration / environment / output location (checked BEFORE any model is
loaded; nothing was evaluated); 3 fatal failure while sweeping (model integrity, contract or clock error: no sweep file is written).

COST. Every configuration re-runs VAD + segmentation; STT results are cached by the exact segment bytes, so identical segments
are decoded once (the cached inference time is the first measurement; do not read latency percentiles of a sweep as a
benchmark - use a normal evaluation run for timings).
"""

import argparse
import dataclasses
import hashlib
import itertools
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from audio.utterance import SegmenterConfigError
from config.logging_config import setup_logging
from evaluation import week7_voice_eval as ev
from speech.stt import SttEngine

logger = setup_logging()

SCHEMA_SWEEP = "week7-calibration-sweep-v1"
SWEEPABLE = ev.SEGMENTER_FIELDS
SWEEP_FILENAME = "week7_calibration_sweep.json"


class CalibrationSplitError(ev.EvalInputError):
    """A non-DEV recording reached the calibration sweep."""


class CachingStt(SttEngine):
    """Decode each distinct segment once; repeats return the first SttResult (see the module docstring on latency)."""

    def __init__(self, inner: SttEngine) -> None:
        super().__init__()
        self._inner = inner
        self._cache: Dict[str, Any] = {}
        self.hits = 0

    sample_rate = property(lambda self: self._inner.sample_rate)
    engine_id = property(lambda self: self._inner.engine_id)
    model_id = property(lambda self: self._inner.model_id)
    max_audio_s = property(lambda self: getattr(self._inner, "max_audio_s", None))
    snapshot_verified = property(lambda self: getattr(self._inner, "snapshot_verified", None))

    def _transcribe(self, samples):                      # never used: transcribe() is overridden
        raise NotImplementedError

    def transcribe(self, samples, sample_rate):
        key = hashlib.sha256(samples.tobytes()).hexdigest() + f":{sample_rate}"
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        result = self._inner.transcribe(samples, sample_rate)
        self._cache[key] = result
        return result


def expand_grid(base: Dict[str, Any], axes: Dict[str, Sequence[Any]], *, vad_frame_samples: int,
                stt_limit_s: Optional[float]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(runnable configurations, skipped combinations with reasons). Nothing is ever dropped without being listed."""
    unknown = sorted(set(axes) - set(SWEEPABLE))
    if unknown:
        raise ev.EvalInputError(f"cannot sweep {unknown}; sweepable segmentation parameters: {list(SWEEPABLE)}")
    for name, candidates in axes.items():
        if not isinstance(candidates, (list, tuple)) or not candidates:
            raise ev.EvalInputError(f"axis {name!r} needs a non-empty list of candidate values")
    names = sorted(axes)
    runnable: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for combo in itertools.product(*(axes[n] for n in names)):
        values = {**base, **dict(zip(names, combo))}
        try:
            ev.segmenter_config_from_values(values, vad_frame_samples)
        except (SegmenterConfigError, ev.EvalInputError) as exc:
            skipped.append({"values": values, "reason": "INVALID_COMBINATION", "detail": str(exc)})
            continue
        if stt_limit_s is not None and values["max_utterance_s"] > stt_limit_s:
            skipped.append({"values": values, "reason": "MAX_UTTERANCE_EXCEEDS_STT_LIMIT",
                            "detail": f"max_utterance_s {values['max_utterance_s']} > STT limit {stt_limit_s}; not run, not truncated"})
            continue
        runnable.append(values)
    return runnable, skipped


def tradeoff_row(values: Dict[str, Any], items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    agg = ev.aggregate_items(items)
    return {
        "config": values, "config_sha256": ev.config_fingerprint(values),
        "items_evaluated": agg["corpus"]["evaluated"], "items_excluded": agg["corpus"]["excluded"],
        "command_items": agg["command_metrics"]["command_items"],
        "command_capture_rate": agg["command_metrics"]["command_capture_rate"],
        "too_short_rate": agg["command_metrics"]["too_short_rate"],
        "no_speech_false_negatives": agg["command_metrics"]["no_speech_false_negatives"],
        "valid_non_speech_controls": agg["hallucination"]["valid_controls"],
        "false_speech_trigger_rate": agg["false_speech_trigger"]["rate"],
        "hallucination_control_rate": agg["hallucination"]["hallucination_control_rate"],
        "wer_decoded": agg["wer"]["decoded"]["rate"], "wer_decoded_items": agg["wer"]["decoded"]["items"],
        "wer_end_to_end": agg["wer"]["end_to_end"]["rate"], "wer_end_to_end_items": agg["wer"]["end_to_end"]["items"],
        "pipeline_wall_ms_p95": agg["latency"]["pipeline_wall_ms"]["p95"],
        "decision_latency_s_median": agg["latency"]["decision_latency_s"]["median"],
        "status_counts": agg["status_distribution"]["counts"],
    }


def _evidence_class(recordings: Sequence[ev.Recording]) -> str:
    real = any(not r.is_synthetic for r in recordings)
    synthetic = any(r.is_synthetic for r in recordings)
    return "REAL_RECORDINGS" if real and not synthetic else "SYNTHETIC_ONLY" if synthetic and not real else "MIXED" if real else "EMPTY"


def run_sweep(recordings: Sequence[ev.Recording], comps: ev.EvalComponents, base: Dict[str, Any], axes: Dict[str, Sequence[Any]], *,
              wake_mode: str = "annotated") -> Dict[str, Any]:
    """Evaluate every DEV recording under every configuration. Raises CalibrationSplitError for any non-DEV recording."""
    foreign = [r.recording_id for r in recordings if r.split != "DEV"]
    if foreign:
        raise CalibrationSplitError(f"calibration uses DEV recordings only; refusing {len(foreign)} non-DEV recording(s), first {foreign[0]!r}")
    limit = getattr(comps.stt, "max_audio_s", None)
    runnable, skipped = expand_grid(base, axes, vad_frame_samples=comps.vad.frame_samples, stt_limit_s=limit)
    cached = CachingStt(comps.stt)
    rows = []
    for values in runnable:
        cfg_comps = ev.EvalComponents(
            vad=comps.vad, stt=cached, segmenter_config=ev.segmenter_config_from_values(values, comps.vad.frame_samples),
            segmenter_source={"kind": "SWEEP_CANDIDATE", "sha256": ev.config_fingerprint(values), "frozen": False},
            wake_phrases=comps.wake_phrases, detector_factory=comps.detector_factory, startup={}, clock=comps.clock,
        )
        items = [ev.evaluate_recording(r, cfg_comps, wake_mode) for r in recordings]
        rows.append(tradeoff_row(values, items))
        logger.info("event=week7_calibration_row config_sha=%s items=%d", rows[-1]["config_sha256"][:12], len(items))
    return {
        "schema_version": SCHEMA_SWEEP, "split": "DEV", "selection": "NONE - no configuration is chosen automatically; compare the rows",
        "evidence_class": _evidence_class(recordings),
        "evidence_note": ("REAL DEV corpus" if _evidence_class(recordings) == "REAL_RECORDINGS" else
                          "NOT REAL EVIDENCE: synthetic or mixed input only demonstrates the plumbing"),
        "wake_mode": wake_mode, "wake_simulated": wake_mode != "detector",
        "base_config": base, "axes": {k: list(v) for k, v in axes.items()}, "recordings": len(recordings),
        "rows": rows, "skipped": skipped, "stt_cache_hits": cached.hits, "normalization": ev.vm.NORMALIZATION_ID,
        "limitations": list(ev.LIMITATIONS),
    }


def write_sweep(out_dir, result: Dict[str, Any], *, contains_real: bool) -> Path:
    out = ev.prepare_output_dir(out_dir, contains_real=contains_real, names=(SWEEP_FILENAME,))
    path = out / SWEEP_FILENAME
    ev._write_exclusive(path, json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evaluation.week7_calibration", description="DEV-only segmentation sweep (no winner is chosen)",
                                epilog="exit codes: 0 finished; 2 invalid input / configuration / environment (nothing evaluated); 3 fatal failure while sweeping")
    p.add_argument("--manifest", required=True)
    p.add_argument("--grid", required=True, help="JSON file {\"base\": {optional nine values}, \"axes\": {name: [values]}}")
    p.add_argument("--output", required=True)
    p.add_argument("--wake-mode", choices=ev.WAKE_MODES, default="annotated")
    args = p.parse_args(argv)
    try:
        manifest = ev.load_manifest(args.manifest)
        try:
            grid = json.loads(Path(args.grid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ev.EvalInputError("grid file is missing or not valid JSON") from None
        if not isinstance(grid, dict) or set(grid) - {"base", "axes"} or not isinstance(grid.get("axes"), dict):
            raise ev.EvalInputError("grid must be an object with an `axes` object and an optional `base` object")
        base = {**ev.EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES, **grid.get("base", {})}
        ev.validate_segmenter_values(base)                               # cheap checks first: nothing below loads a model
        from speech.faster_whisper_backend import DEFAULT_MAX_AUDIO_S
        runnable, skipped = expand_grid(base, grid["axes"], vad_frame_samples=ev.SILERO_MODEL.frame_samples, stt_limit_s=DEFAULT_MAX_AUDIO_S)
        if not runnable:
            reasons = sorted({s["reason"] for s in skipped})
            raise ev.EvalInputError(f"the grid has no runnable configuration ({len(skipped)} skipped: {', '.join(reasons)})")
        dev = [r for r in manifest.recordings if r.split == "DEV"]
        ignored = len(manifest.recordings) - len(dev)
        ev.prepare_output_dir(args.output, contains_real=manifest.has_real_recordings, names=(SWEEP_FILENAME,))
        comps = ev.build_real_components(base, {"kind": "SWEEP_BASE", "sha256": ev.config_fingerprint(base), "frozen": False}, wake_mode=args.wake_mode)
        result = run_sweep(dev, comps, base, grid["axes"], wake_mode=args.wake_mode)
        result["heldout_recordings_ignored"] = ignored
        path = write_sweep(args.output, result, contains_real=manifest.has_real_recordings)
    except ev.EvalFatalError as fatal:
        print(f"FATAL: {fatal}", "no sweep file was written", sep="; ", file=sys.stderr)
        return 3
    except (ev.EvalInputError, SegmenterConfigError, ev.SttError, ev.SileroVadError, ev.WakeWordBackendError, OSError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)      # an expected user / environment mistake: no traceback
        return 2
    print(f"{len(result['rows'])} configuration(s) evaluated, {len(result['skipped'])} skipped, {ignored} HELDOUT recording(s) ignored\n{path}")
    print("NO configuration was selected. Compare the rows; freeze one config file; then run ONE HELDOUT descriptive check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
