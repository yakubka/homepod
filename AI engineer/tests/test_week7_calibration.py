"""Week 7 Task 5: the DEV-only calibration sweep SUPPORT (evaluation/week7_calibration.py).

The sweep machinery is SOFTWARE-VALIDATED on synthetic fixtures. It was never run on real data: no threshold has been tuned or chosen.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation import week7_calibration as cal
from evaluation import week7_voice_eval as ev
from evaluation.ingest import REPO_ROOT
from tests.week7_eval_support import (
    TEST_SEGMENTER_VALUES, clip, make_components, manifest_dict, parse, real_rec, synth_rec, write_wav,
)


_RESIDUE = (REPO_ROOT / "tests" / "_should_never_be_written", REPO_ROOT / "tests" / "_never", REPO_ROOT / "tests" / "_cli_never",
            REPO_ROOT / "tests" / "_sweep_never", REPO_ROOT / "evaluation" / "_nope")


@pytest.fixture(autouse=True)
def _no_repo_residue():
    """If a refusal ever regresses, the test fails AND the directory it wrongly created inside the repository is removed."""
    yield
    for path in _RESIDUE:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def recordings(tmp_path):
    """Two command clips and a loud 'noise' control, all DEV and SYNTHETIC."""
    loud = write_wav(tmp_path / "loud.wav", clip(speech=((1.0, 2.0),)))
    speech = write_wav(tmp_path / "speech.wav", clip())
    recs = [synth_rec("CMD1", speech), synth_rec("CMD2", write_wav(tmp_path / "s2.wav", clip(speech=((1.5, 3.0),)))),
            synth_rec("NOISE", loud, "NOISE_CONTROL")]
    return list(parse(recs, tmp_path).recordings)


def test_the_sweepable_parameters_are_the_nine_segmenter_values():
    assert set(cal.SWEEPABLE) == set(TEST_SEGMENTER_VALUES) and len(cal.SWEEPABLE) == 9


def test_expand_grid_builds_the_full_product_and_lists_every_skipped_combination():
    base = dict(TEST_SEGMENTER_VALUES)
    runnable, skipped = cal.expand_grid(base, {"start_threshold": [0.4, 0.5], "end_silence_s": [0.2, 0.4]}, vad_frame_samples=512, stt_limit_s=30.0)
    assert len(runnable) == 4 and skipped == []
    assert {(v["start_threshold"], v["end_silence_s"]) for v in runnable} == {(0.4, 0.2), (0.4, 0.4), (0.5, 0.2), (0.5, 0.4)}
    assert all(v["max_utterance_s"] == base["max_utterance_s"] for v in runnable)


def test_invalid_combinations_are_skipped_with_a_reason_not_dropped_silently():
    runnable, skipped = cal.expand_grid(dict(TEST_SEGMENTER_VALUES), {"start_threshold": [0.3, 0.6], "end_threshold": [0.35]},
                                        vad_frame_samples=512, stt_limit_s=30.0)
    assert len(runnable) == 1 and runnable[0]["start_threshold"] == 0.6
    (bad,) = skipped
    assert bad["reason"] == "INVALID_COMBINATION" and bad["values"]["start_threshold"] == 0.3 and "hysteresis" in bad["detail"]


def test_a_candidate_above_the_stt_limit_is_reported_as_a_policy_conflict_and_not_run():
    runnable, skipped = cal.expand_grid(dict(TEST_SEGMENTER_VALUES), {"max_utterance_s": [10.0, 30.0, 31.0, 60.0]}, vad_frame_samples=512, stt_limit_s=30.0)
    assert [v["max_utterance_s"] for v in runnable] == [10.0, 30.0]
    assert [s["values"]["max_utterance_s"] for s in skipped] == [31.0, 60.0]
    assert all(s["reason"] == "MAX_UTTERANCE_EXCEEDS_STT_LIMIT" and "not truncated" in s["detail"] for s in skipped)


@pytest.mark.parametrize("axes,message", [({"gain": [1]}, "cannot sweep"), ({"start_threshold": []}, "non-empty list"), ({"start_threshold": 0.5}, "non-empty list")])
def test_bad_axes_are_refused(axes, message):
    with pytest.raises(ev.EvalInputError, match=message):
        cal.expand_grid(dict(TEST_SEGMENTER_VALUES), axes, vad_frame_samples=512, stt_limit_s=30.0)


def test_a_sweep_runs_every_dev_recording_under_every_configuration_and_chooses_no_winner(recordings):
    comps = make_components(["hey jarvis turn on the lights"])
    result = cal.run_sweep(recordings, comps, dict(TEST_SEGMENTER_VALUES), {"start_threshold": [0.5, 0.6], "end_silence_s": [0.2, 0.4]})
    assert result["schema_version"] == cal.SCHEMA_SWEEP and result["split"] == "DEV" and len(result["rows"]) == 4
    assert result["evidence_class"] == "SYNTHETIC_ONLY" and "NOT REAL EVIDENCE" in result["evidence_note"]
    assert result["selection"].startswith("NONE")
    assert not any(k in result for k in ("best", "best_config", "recommended", "winner", "chosen"))
    row = result["rows"][0]
    for key in ("command_capture_rate", "false_speech_trigger_rate", "too_short_rate", "hallucination_control_rate",
                "wer_decoded", "wer_end_to_end", "pipeline_wall_ms_p95", "decision_latency_s_median", "config", "config_sha256"):
        assert key in row
    assert row["items_evaluated"] == 3 and row["command_items"] == 2 and row["valid_non_speech_controls"] == 1
    assert len({r["config_sha256"] for r in result["rows"]}) == 4
    json.dumps(result)


def test_the_tradeoff_rows_expose_a_real_tradeoff(recordings):
    """A stricter start threshold (above the fake VAD's 0.9 score) captures no command and triggers nothing on the control."""
    comps = make_components(["hey jarvis turn on the lights"])
    result = cal.run_sweep(recordings, comps, dict(TEST_SEGMENTER_VALUES), {"start_threshold": [0.5, 0.95]})
    lax, strict = sorted(result["rows"], key=lambda r: r["config"]["start_threshold"])
    assert lax["command_capture_rate"] == 1.0 and lax["false_speech_trigger_rate"] == 1.0 and lax["hallucination_control_rate"] == 1.0
    assert strict["command_capture_rate"] == 0.0 and strict["false_speech_trigger_rate"] == 0.0 and strict["hallucination_control_rate"] == 0.0


def test_identical_segments_are_decoded_once_across_configurations(recordings):
    comps = make_components(["hey jarvis turn on the lights"])
    result = cal.run_sweep(recordings, comps, dict(TEST_SEGMENTER_VALUES), {"end_silence_s": [0.2, 0.3]})
    assert result["stt_cache_hits"] > 0 and len(comps.stt.calls) < 2 * 3


def test_heldout_recordings_are_refused_by_the_sweep(tmp_path, recordings):
    held = list(parse([synth_rec("H1", write_wav(tmp_path / "h.wav", clip()), split="HELDOUT")], tmp_path).recordings)
    comps = make_components()
    with pytest.raises(cal.CalibrationSplitError, match="DEV recordings only"):
        cal.run_sweep(held, comps, dict(TEST_SEGMENTER_VALUES), {"start_threshold": [0.5]})
    with pytest.raises(cal.CalibrationSplitError):
        cal.run_sweep(recordings + held, comps, dict(TEST_SEGMENTER_VALUES), {"start_threshold": [0.5]})
    assert comps.stt.calls == []                                   # nothing ran before the refusal


def test_the_sweep_module_has_no_way_to_include_heldout_and_never_touches_settings():
    source = Path(cal.__file__).read_text(encoding="utf-8")
    assert "include-heldout" not in source and "allow-heldout" not in source and "include_heldout" not in source
    assert "config.settings" not in source and "settings.py\"" not in source


def test_a_real_sweep_output_must_be_outside_the_repository_and_is_never_overwritten(tmp_path, recordings):
    result = cal.run_sweep(recordings, make_components(), dict(TEST_SEGMENTER_VALUES), {"start_threshold": [0.5]})
    with pytest.raises(ev.EvalEvidenceError, match="outside the source repository"):
        cal.write_sweep(REPO_ROOT / "tests" / "_sweep_never", result, contains_real=True)
    assert not (REPO_ROOT / "tests" / "_sweep_never").exists()
    path = cal.write_sweep(tmp_path / "out", result, contains_real=True)
    assert json.loads(path.read_text(encoding="utf-8"))["split"] == "DEV"
    with pytest.raises(ev.EvalEvidenceError, match="already exists"):
        cal.write_sweep(tmp_path / "out", result, contains_real=True)


def test_real_dev_recordings_mark_the_sweep_as_real_evidence(tmp_path):
    recs = list(parse([real_rec("R1", write_wav(tmp_path / "r.wav", clip()))], tmp_path).recordings)
    result = cal.run_sweep(recs, make_components(), dict(TEST_SEGMENTER_VALUES), {"start_threshold": [0.5]})
    assert result["evidence_class"] == "REAL_RECORDINGS" and result["evidence_note"] == "REAL DEV corpus"


def test_the_sweep_leaves_the_production_settings_untouched():
    path = REPO_ROOT / "config" / "settings.py"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    from config import settings
    assert settings.VAD_START_THRESHOLD is None and settings.VOICE_MAX_UTTERANCE_S is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_cli_rejects_a_bad_grid_and_a_repo_output_before_loading_models(tmp_path):
    wav_path = write_wav(tmp_path / "a.wav", clip())
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(manifest_dict([real_rec("R", wav_path)])), encoding="utf-8")
    bad_grid = tmp_path / "g.json"
    bad_grid.write_text(json.dumps({"axes": {"start_threshold": [0.5]}, "extra": 1}), encoding="utf-8")
    run = lambda *a: subprocess.run([sys.executable, "-m", "evaluation.week7_calibration", *a], cwd=str(REPO_ROOT), capture_output=True, text=True)
    r = run("--manifest", str(manifest), "--grid", str(bad_grid), "--output", str(tmp_path / "o"))
    assert r.returncode == 2 and "grid" in r.stderr
    good_grid = tmp_path / "g2.json"
    good_grid.write_text(json.dumps({"axes": {"start_threshold": [0.5]}}), encoding="utf-8")
    r = run("--manifest", str(manifest), "--grid", str(good_grid), "--output", str(REPO_ROOT / "tests" / "_cli_never"))
    assert r.returncode == 2 and "outside the source repository" in r.stderr and not (REPO_ROOT / "tests" / "_cli_never").exists()
    assert run("--manifest", str(tmp_path / "none.json"), "--grid", str(good_grid), "--output", str(tmp_path / "o2")).returncode == 2
