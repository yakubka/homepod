"""Week 7 pre-handoff hardening: M1-M6 and L2 from the same-author adversarial audit.

SOFTWARE-VALIDATED with fakes and synthetic audio only. Nothing here is a real-data or hardware result.

  M1  a wake-detector failure makes the stream UNTRUSTED (fail closed) until restart_stream()
  M2  structured consent references; placeholders rejected; values never echoed
  M3  a manifest holding real records must live outside the repository (canonical path checks); gitignore rules
  M4  audio/file_source.py no longer logs or raises with file names (an approved surgical privacy patch)
  M5  whole-manifest preflight; safe per-recording failures vs fatal failures; partial output never labelled complete
  M6  invalid configuration is a clean exit 2 before any model is loaded
  L2  the wake-prefix rule keeps meaningful command punctuation (minus sign, decimal point)
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from audio import file_source as fs
from audio.file_source import EndOfAudio, WavFileSource
from audio.utterance import SegmenterConfigError
from audio.vad import VadContractError
from evaluation import ingest
from evaluation import week7_calibration as cal
from evaluation import week7_voice_eval as ev
from evaluation.ingest import REPO_ROOT
from speech.pipeline import PipelineClockError, PipelineStateError, PipelineStatus, VoicePipeline, strip_wake_prefix
from speech.stt import SttContractError
from tests.test_voice_pipeline import CHUNK as CH1K, EnergyVad, FakeStt, Rig, S, ScriptedWake, stream
from tests.week7_eval_support import (
    SR, TEST_SEGMENTER_VALUES, EnergyVad16k, SeqStt, clip, make_components, manifest_dict, parse, real_rec, synth_rec, write_wav,
)
from wakeword.openwakeword_backend import WakeWordBackendError

_RESIDUE = (REPO_ROOT / "tests" / "_hardening_never",)


@pytest.fixture(autouse=True)
def _no_repo_residue():
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
    fs.logger.addHandler(handler)          # the shared homepod_ai logger: every module logs through it
    yield handler.lines
    fs.logger.removeHandler(handler)


# =========================================================================================
# M1 - detector failure fails closed
# =========================================================================================

class ThrowingWake(ScriptedWake):
    """A real WakeWordDetector whose scoring raises on the chunks (indices since the last reset) listed in fail_on."""

    def __init__(self, fire=(8,), fail_on=(), error=RuntimeError):
        self.fail_on = set(fail_on)
        self.error = error
        super().__init__(fire)

    def _score_chunk(self, chunk):
        if self._chunks_processed in self.fail_on:
            raise self.error("detector boom")
        return super()._score_chunk(chunk)


def _feed_until_failure(pipeline, x, start, block=40):
    pos = start
    while pos < x.size:
        try:
            pipeline.feed(x[pos:pos + block])
        except RuntimeError as exc:
            return pos + block, exc
        pos += block
    raise AssertionError("the scripted failure never happened")


def test_a_detector_failure_after_a_non_zero_origin_invalidates_the_stream_and_nothing_is_mapped_afterwards():
    rig = Rig(detector=ThrowingWake(fire=(8,), fail_on={2}))
    x = stream(2000, [(380, 700)])
    rig.pipeline.feed(x[:110])                              # 2 chunks fed, 30 samples pending
    rig.pipeline.reset_wake_detector()                      # the detector clock now starts at master 110
    assert rig.pipeline.detector_origin == 110
    pos, exc = _feed_until_failure(rig.pipeline, x, 110)
    assert str(exc) == "detector boom"                      # the ORIGINAL exception reaches the caller
    results, errors = [], []
    while pos < x.size:                                     # the caller carries on feeding, as the audit's probe did
        try:
            results += rig.pipeline.feed(x[pos:pos + 40])
        except PipelineStateError as err:
            errors.append(err)
        pos += 40
    assert results == [] and len(errors) > 10
    assert all("restart_stream" in str(e) and "RuntimeError" in str(e) for e in errors) and len(errors) > 10
    for call in (lambda: rig.pipeline.finish(), lambda: rig.pipeline.reset_wake_detector()):
        with pytest.raises(PipelineStateError, match="restart_stream"):
            call()
    assert rig.pipeline.interactions == 0 and rig.stt.calls == []         # no activation was ever mapped, no STT ran


def test_the_audits_exact_reproduction_no_longer_yields_a_silently_shifted_activation():
    """Before the fix: detector raised once, the caller continued, and a later wake was reported one chunk too early."""
    rig = Rig(detector=ThrowingWake(fire=(8,), fail_on={3}))
    x = stream(1500, [(380, 700)])
    results, raised = [], []
    pos = 0
    while pos < x.size:
        try:
            results += rig.pipeline.feed(x[pos:pos + 40], pos)
        except (RuntimeError, PipelineStateError) as err:
            raised.append(type(err).__name__)
        pos += 40
    assert raised[0] == "RuntimeError" and set(raised[1:]) == {"PipelineStateError"} and results == []


def test_an_explicit_restart_creates_a_fresh_consistent_clock_with_nothing_stale():
    rig = Rig(detector=ThrowingWake(fire=(), fail_on={1}))
    old = stream(800, [(0, 800)])                           # loud audio BEFORE the failure: it must never reach a later segment
    rig.pipeline.feed(old[:50])                             # chunk 0 is fine; 10 samples stay pending in the detector's reframer
    with pytest.raises(RuntimeError):
        rig.pipeline.feed(old[50:90])                       # chunk 1 raises while 10 samples are still pending behind it
    assert rig.pipeline.master_samples == 90 and rig.pipeline._wake_reframer.pending_samples == 10
    rig.detector.fail_on = set()
    rig.detector.fire = {2}                                 # after the restart the wake fires on chunk 2 (ends at 120)
    rig.pipeline.restart_stream()
    assert (rig.pipeline.master_samples, rig.pipeline.detector_origin, rig.pipeline.interactions) == (0, 0, 0)
    assert rig.pipeline._buffer.size == 0 and rig.pipeline._wake_reframer.pending_samples == 0 and not rig.pipeline.is_capturing
    new = stream(1000, [(200, 500)])
    results = []
    for lo in range(0, new.size, 40):
        results += rig.pipeline.feed(new[lo:lo + 40], lo)
    results += rig.pipeline.finish()
    (r,) = results
    assert r.status is S.TRANSCRIBED and (r.activation_sample, r.detector_end_sample) == (120, 120)
    assert np.array_equal(rig.stt.calls[0], new[r.segment_start_sample:r.segment_end_sample])      # only NEW audio, aligned to the new clock
    assert r.segment_start_sample >= 0


def test_a_clock_error_also_makes_the_stream_untrusted():
    rig = Rig()
    rig.pipeline.feed(stream(80))
    rig.detector.process(np.zeros(CH1K, np.int16))          # someone feeds the detector behind the pipeline's back
    with pytest.raises(PipelineClockError):
        rig.pipeline.feed(stream(80))
    with pytest.raises(PipelineStateError, match="restart_stream"):
        rig.pipeline.feed(stream(80))
    rig.pipeline.restart_stream()
    rig.pipeline.feed(stream(80))                           # and a restart makes it usable again


def test_a_detector_failure_on_the_padded_tail_chunk_also_fails_closed():
    rig = Rig(detector=ThrowingWake(fire=(), fail_on={25}))
    rig.pipeline.feed(stream(25 * CH1K + 10))               # 25 full chunks fine, a 10-sample tail pending
    with pytest.raises(RuntimeError, match="detector boom"):
        rig.pipeline.finish()
    with pytest.raises(PipelineStateError, match="wake detector failed"):             # not merely 'the stream was finished'
        rig.pipeline.feed(stream(40))
    rig.pipeline.restart_stream()
    rig.pipeline.feed(stream(40))


def test_vad_and_stt_failures_do_not_invalidate_the_stream_clock():
    rig = Rig(fire=(8, 36))
    rig.vad.fail_after = 20
    x = stream(3000, [(300, 600), (1500, 1800)])
    with pytest.raises(RuntimeError, match="scripted VAD failure"):
        for lo in range(0, 1000, 40):
            rig.pipeline.feed(x[lo:lo + 40])
    rig.vad.fail_after = None
    resume = rig.pipeline.master_samples
    results = rig.pipeline.feed(x[resume:], resume) + rig.pipeline.finish()       # no PipelineStateError: the clock stayed trustworthy
    assert [r.status for r in results] == [S.TRANSCRIBED] and results[0].activation_sample == 36 * CH1K + CH1K


def test_the_clean_path_is_unchanged_by_the_fail_closed_policy():
    rig = Rig(detector=ThrowingWake(fire=(8,), fail_on=()))
    results = rig.run(stream(1000, [(300, 600)]))
    assert len(results) == 1 and results[0].status is S.TRANSCRIBED and results[0].activation_sample == 360


# =========================================================================================
# L2 - the wake-prefix rule keeps command punctuation
# =========================================================================================

@pytest.mark.parametrize("text,expected", [
    ("hey jarvis -5 degrees", ("-5 degrees", True)),
    ("hey jarvis: -5 degrees", ("-5 degrees", True)),
    ("Hey, Jarvis, -5 degrees", ("-5 degrees", True)),
    ("hey jarvis-5 degrees", ("-5 degrees", True)),
    ("hey jarvis –5 degrees", ("–5 degrees", True)),            # an en dash directly before a digit is kept as a minus
    ("hey jarvis -.5", ("-.5", True)),
    ("hey jarvis .5", (".5", True)),
    ("hey jarvis: .5 volume", (".5 volume", True)),
    ("Hey, Jarvis, what is the temperature?", ("what is the temperature?", True)),
    ("Hey Jarvis. What is the temperature?", ("What is the temperature?", True)),
    ("hey jarvis. 5 degrees", ("5 degrees", True)),                       # a full stop followed by a space is punctuation
    ("hey jarvis - turn on the lights", ("turn on the lights", True)),    # a spaced hyphen is punctuation
    ("hey jarvis — turn on the lights", ("turn on the lights", True)),
    ("hey jarvis—turn on", ("turn on", True)),                       # an em dash is never a minus sign
    ("hey jarvis - turn on", ("turn on", True)),
    ("hey jarvis (turn on the lights)", ("(turn on the lights)", True)),  # a bracket that belongs to the command survives
    ("hey jarvis “play”", ("“play”", True)),
    ('"Hey Jarvis," lights on', ("lights on", True)),                     # a quote that wrapped the phrase is closed with it
    ("(Hey Jarvis) lights on", ("lights on", True)),
    ("hey jarvis, tell jarvis to stop", ("tell jarvis to stop", True)),   # an internal occurrence survives
    ("hey jarvis 5", ("5", True)),
    ("hey jarvis!", ("", True)),
    ("Hey Jarvis...", ("", True)),
    ("Hey, Jarvis.", ("", True)),
    ("hey jarvis -", ("", True)),
    ("hey jarvis —", ("", True)),
    ("hey jarvis: ", ("", True)),
    ("hey jarvis …", ("", True)),
    ("tell jarvis -5 degrees", ("tell jarvis -5 degrees", False)),
    ("-5 hey jarvis", ("-5 hey jarvis", False)),
    ("hey jarvisian -5", ("hey jarvisian -5", False)),
])
def test_the_wake_prefix_rule_preserves_command_semantics(text, expected):
    assert strip_wake_prefix(text) == expected


def test_a_minus_or_decimal_point_survives_the_whole_pipeline():
    r = Rig(reply="Hey Jarvis, -5 degrees").run(stream(1000, [(300, 600)]))[0]
    assert r.status is S.TRANSCRIBED and r.normalized_text == "-5 degrees" and r.wake_prefix_removed is True
    r = Rig(reply="hey jarvis .5").run(stream(1000, [(300, 600)]))[0]
    assert r.normalized_text == ".5"


@pytest.mark.parametrize("reply", ["Hey Jarvis.", "hey, jarvis!", "Hey Jarvis...", "hey jarvis -", "hey jarvis —"])
def test_wake_only_punctuation_variants_stay_wake_only(reply):
    r = Rig(reply=reply).run(stream(1000, [(300, 600)]))[0]
    assert r.status is S.WAKE_ONLY and r.normalized_text == "" and r.wake_prefix_removed is True


# =========================================================================================
# M4 - file names never reach logs or exception text
# =========================================================================================

NAMED = "alice_smith_turn_on_the_lights.wav"
FORBIDDEN = ("alice", "smith", "turn_on", "lights", ".wav")


def _clean(text):
    return not any(word in text.lower() for word in FORBIDDEN)


def test_wav_source_logs_carry_no_file_name_or_path(write_wav, logs, tmp_path):
    path = write_wav(np.arange(3000, dtype=np.int16), name=NAMED)
    with WavFileSource(path, block_size=1024) as src:
        list(src.frames())
    assert logs, "the source must still log its structural events"
    joined = "\n".join(logs)
    assert _clean(joined) and tmp_path.name.lower() not in joined.lower() and "\\" not in joined and "/" not in joined
    start = next(l for l in logs if "wav_source_start" in l)
    for field in ("source=wav_file", "sample_rate=16000", "channels=1", "total_samples=3000", "duration_s=", "block_size=1024"):
        assert field in start
    assert any("wav_source_eof" in l for l in logs) and any("wav_source_stop" in l for l in logs)


def test_wav_source_exception_text_carries_no_file_name(write_wav, tmp_path):
    path = write_wav(np.zeros(100, np.int16), name=NAMED)
    with WavFileSource(path) as src:
        src.read_frame()
        with pytest.raises(EndOfAudio) as end:
            src.read_frame()
            src.read_frame()
        with pytest.raises(EndOfAudio) as again:
            src.read_frame()
    for exc in (end.value, again.value):
        assert _clean(str(exc))
    bad = tmp_path / "bob_jones_says_open_the_door.wav"
    bad.write_bytes(b"this is not a wav file")
    with pytest.raises(ValueError) as unreadable:
        WavFileSource(bad)
    wrong_rate = write_wav(np.zeros(100, np.int16), name="carol_open_door.wav", rate=8000)
    with pytest.raises(ValueError, match="8000 Hz") as unsupported:
        WavFileSource(wrong_rate)
    for exc in (unreadable.value, unsupported.value):
        chain = [exc, exc.__cause__]
        assert all(_clean(str(e)) and "bob" not in str(e).lower() and "carol" not in str(e).lower() and tmp_path.name not in str(e) for e in chain if e)


def test_decoding_and_block_layout_are_unchanged(write_wav):
    samples = (np.arange(2500) % 1000 - 500).astype(np.int16)
    with WavFileSource(write_wav(samples, name=NAMED), block_size=1000) as src:
        blocks = list(src.frames())
    assert [b.shape for b in blocks] == [(1000, 1), (1000, 1), (500, 1)] and np.array_equal(np.concatenate(blocks)[:, 0], samples)


def test_a_pipeline_run_over_a_named_file_logs_no_name(write_wav, logs):
    path = write_wav(clip(), name=NAMED)
    comps = make_components(["hey jarvis turn on the lights"])
    pipeline = VoicePipeline(ev.AnnotatedWakeDetector(20000), ev.UtteranceSegmenter(comps.segmenter_config, comps.vad), comps.stt)
    pipeline.run_source(WavFileSource(path))
    joined = "\n".join(logs)
    assert "event=pipeline_wake" in joined and _clean(joined)          # no file name and ('lights' is forbidden too) no transcript


def test_the_evaluator_logs_no_audio_file_name_even_when_the_name_is_a_person_and_a_transcript(tmp_path, logs):
    path = write_wav(tmp_path / NAMED, clip())
    manifest = parse([real_rec("REC-001", path)], tmp_path)
    comps = make_components(["hey jarvis turn on the lights"])
    ev.evaluate_manifest(manifest, comps)
    assert any("week7_eval_item" in l for l in logs) and _clean("\n".join(logs))


# =========================================================================================
# M2 - structured consent references
# =========================================================================================

@pytest.mark.parametrize("value", ["CONSENT-S01", "CONSENT-2026-001", "APPROVAL-PRJ-17", "CONSENT-S01_B2", "  CONSENT-S01  ", "CONSENT-A1"])
def test_structured_consent_references_are_accepted(value):
    assert ev.consent_reference_problem(value) is None


@pytest.mark.parametrize("value", [
    "UNKNOWN", "unknown", "NOT_APPLICABLE", "", "   ", None, 5, ["CONSENT-S01"],
    "TBD", "tbd", "N/A", "n/a", "NA", "none", "None", "null", "pending", "PENDING", "false", "False", "no", "No", "yes", "ok", "x", "xx", "?", "??", "-", "...",
    "CONSENT", "CONSENT-", "CONSENT-TBD", "CONSENT-NONE-1", "CONSENT-PENDING-1", "CONSENT-NA", "CONSENT-XXX", "CONSENT-000", "CONSENT-TEST-1",
    "CONSENT-ALICE", "CONSENT-ALICE-SMITH", "CONSENT S01", "CONSENT-S01; DROP", "consent-s01", "S01", "approved", "yes, they agreed",
    "CONSENT-" + "A1" * 60,
])
def test_placeholders_and_free_text_are_not_consent_references(value):
    problem = ev.consent_reference_problem(value)
    assert problem is not None
    for secret in ("SECRETMARKER", "CONSENT-SECRETMARKER", "free text SECRETMARKER"):
        assert "SECRETMARKER" not in ev.consent_reference_problem(secret)          # a rejected value is never echoed in the reason


def test_the_manifest_accepts_a_valid_code_and_rejects_placeholders_without_echoing_anything(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    parse([real_rec("R-1", path, consent_reference="CONSENT-S01")], tmp_path)
    for secret in ("TBD", "pending", "N/A", "false", "?", "none", "CONSENT-ALICE-SMITH", "Alice Smith agreed on Monday"):
        with pytest.raises(ev.EvalInputError) as err:
            parse([real_rec("R-1", path, consent_reference=secret)], tmp_path)
        text = str(err.value)
        assert "consent_reference" in text and "does not prove consent" in text
        for fragment in ("Alice", "ALICE", "SMITH", "Smith", "Monday", "pending", "TBD"):
            assert fragment not in text.replace("PENDING", "")        # the message may name rule words in capitals only, never the value


def test_synthetic_stays_not_applicable_and_other_values_are_rejected_without_echo(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    parse([synth_rec("S-1", path)], tmp_path)
    with pytest.raises(ev.EvalInputError) as err:
        parse([synth_rec("S-1", path, consent_reference="CONSENT-S01SECRET")], tmp_path)
    assert "SECRET" not in str(err.value) and "NOT_APPLICABLE" in str(err.value)
    with pytest.raises(ev.EvalInputError) as err:
        parse([real_rec("R-1", path, speaker_code="Alice Smith")], tmp_path)
    assert "Alice" not in str(err.value) and "speaker_code" in str(err.value)


def test_only_template_entries_are_exempt_and_only_when_templates_are_allowed(tmp_path):
    data = json.loads((REPO_ROOT / "evaluation" / "week7_corpus_manifest.template.json").read_text(encoding="utf-8"))
    assert ev.parse_manifest(data, REPO_ROOT / "evaluation", allow_template=True).template
    rec = real_rec("TEMPLATE-X", tmp_path / "x.wav", consent_reference="<fill in>")
    with pytest.raises(ev.EvalInputError):
        parse([rec], tmp_path)
    parse([rec], tmp_path, allow_template=True)


def test_consent_values_stay_out_of_cli_errors_and_logs(tmp_path, capsys):
    path = write_wav(tmp_path / "a.wav", clip())
    man = tmp_path / "m.json"
    man.write_text(json.dumps(manifest_dict([real_rec("R-1", path, consent_reference="CONSENT-ALICE-SMITH")])), encoding="utf-8")
    assert ev.main(["validate", "--manifest", str(man)]) == 2
    err = capsys.readouterr().err
    assert "ALICE" not in err and "SMITH" not in err and "does not prove consent" in err


# =========================================================================================
# M3 - the manifest of real records must live outside the repository
# =========================================================================================

@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(ingest, "REPO_ROOT", repo.resolve())          # the guard compares against this
    return repo


def _write_manifest(directory: Path, recordings, name="manifest.json"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(manifest_dict(recordings)), encoding="utf-8")
    return path


def test_a_real_manifest_inside_the_repository_is_rejected(tmp_path, fake_repo):
    audio = write_wav(tmp_path / "outside_audio.wav", clip())
    inside = _write_manifest(fake_repo / "evaluation", [real_rec("R-1", audio)])
    with pytest.raises(ev.EvalInputError, match="outside the source repository") as err:
        ev.load_manifest(inside)
    assert str(tmp_path) not in str(err.value) and "manifest.json" not in str(err.value)


def test_a_manifest_outside_the_repository_is_accepted_even_with_a_look_alike_name(tmp_path, fake_repo):
    audio = write_wav(tmp_path / "outside_audio.wav", clip())
    sibling = tmp_path / "repo-evidence"                              # shares the repository path as a STRING prefix
    assert str(sibling).startswith(str(fake_repo))
    ev.load_manifest(_write_manifest(sibling, [real_rec("R-1", audio)]))
    ev.load_manifest(_write_manifest(tmp_path / "elsewhere", [real_rec("R-1", audio)]))


def test_dot_dot_traversal_back_into_the_repository_is_rejected(tmp_path, fake_repo):
    audio = write_wav(tmp_path / "outside_audio.wav", clip())
    inside = _write_manifest(fake_repo, [real_rec("R-1", audio)])
    (tmp_path / "other").mkdir()
    sneaky = tmp_path / "other" / ".." / "repo" / inside.name
    with pytest.raises(ev.EvalInputError, match="outside the source repository"):
        ev.load_manifest(sneaky)


def test_a_synthetic_only_manifest_may_live_anywhere(tmp_path, fake_repo):
    audio = write_wav(tmp_path / "s.wav", clip())
    ev.load_manifest(_write_manifest(fake_repo, [synth_rec("S-1", audio)]))


def _make_dir_link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":                                               # a directory junction needs no privileges
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        return done.returncode == 0 and link.exists()
    return False


def test_a_link_or_junction_that_leads_back_into_the_repository_is_resolved_and_rejected(tmp_path, fake_repo):
    audio = write_wav(tmp_path / "outside_audio.wav", clip())
    inside = _write_manifest(fake_repo / "data", [real_rec("R-1", audio)])
    link = tmp_path / "innocent_looking_folder"
    if not _make_dir_link(link, fake_repo / "data"):
        pytest.skip("cannot create a symlink or junction on this machine")
    with pytest.raises(ev.EvalInputError, match="outside the source repository"):
        ev.load_manifest(link / inside.name)
    with pytest.raises(ev.EvalInputError, match="inside the source repository"):         # the same holds for audio reached through the link
        parse([real_rec("R-2", link / "a.wav")], tmp_path)
    with pytest.raises(ev.EvalEvidenceError, match="outside the source repository"):
        ev.prepare_output_dir(link / "out", contains_real=True, names=("x",))


def test_the_repository_template_is_still_loadable_as_a_template():
    manifest = ev.load_manifest(REPO_ROOT / "evaluation" / "week7_corpus_manifest.template.json", allow_template=True)
    assert manifest.template and manifest.has_real_recordings


def test_gitignore_protects_real_manifests_and_evidence_but_not_the_template(tmp_path):
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rule in ("week7_eval_items.jsonl", "week7_eval_items.jsonl.partial", "week7_eval_report.json", "week7_eval_report.json.partial",
                 "week7_eval_report.INCOMPLETE.json", "week7_calibration_sweep.json", "*week7*manifest*.json"):
        assert rule in lines
    assert lines.index("!evaluation/week7_corpus_manifest.template.json") > lines.index("*week7*manifest*.json")
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed: rule order was checked textually only")
    repo = tmp_path / "gi"
    (repo / "evaluation").mkdir(parents=True)
    shutil.copy(REPO_ROOT / ".gitignore", repo / ".gitignore")
    subprocess.run([git, "init", "-q"], cwd=repo, check=True)
    cases = {"evaluation/week7_corpus_manifest.template.json": False, "evaluation/week7_corpus_manifest_real.json": True,
             "my_week7_manifest.json": True, "week7_eval_items.jsonl": True, "out/week7_eval_report.json": True,
             "week7_eval_report.INCOMPLETE.json": True, "week7_eval_items.jsonl.partial": True, "week7_calibration_sweep.json": True,
             "evaluation/week7_voice_eval.py": False, "evaluation/ai_question_test_set_v1.json": False}
    for name, ignored in cases.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        status = subprocess.run([git, "check-ignore", "-q", name], cwd=repo).returncode
        assert (status == 0) is ignored, name


# =========================================================================================
# M5 - preflight, safe per-recording failures, fatal failures, honest partial output
# =========================================================================================

def _forbidden_models(monkeypatch):
    monkeypatch.setattr(ev, "build_real_components", lambda *a, **k: pytest.fail("a model was constructed"))


def test_the_preflight_lists_every_problem_before_any_model_or_evidence(tmp_path, monkeypatch, capsys):
    good = write_wav(tmp_path / "g.wav", clip())
    recs = [synth_rec("W-1", good, wake_end_sample=9_999_999), synth_rec("W-2", good, speech_start_sample=100, speech_end_sample=9_999_999),
            synth_rec("W-3", good), synth_rec("OK-1", good)]
    del recs[2]["wake_end_sample"]
    man = tmp_path / "m.json"
    man.write_text(json.dumps(manifest_dict(recs)), encoding="utf-8")
    _forbidden_models(monkeypatch)
    out = tmp_path / "out"
    assert ev.main(["run", "--manifest", str(man), "--output", str(out)]) == 2
    err = capsys.readouterr().err
    assert "W-1: wake_end_sample is beyond the end of the audio" in err and "W-2: speech_end_sample is beyond" in err
    assert "W-3: wake_end_sample is required" in err and "OK-1" not in err and "Traceback" not in err
    assert not out.exists()                                           # nothing was created: no directory, no partial file


def test_the_api_preflight_runs_before_the_first_recording_is_evaluated(tmp_path):
    good = write_wav(tmp_path / "g.wav", clip())
    manifest = parse([synth_rec("A-1", good), synth_rec("B-1", good, wake_end_sample=9_999_999)], tmp_path)
    comps = make_components()
    with pytest.raises(ev.EvalInputError, match="B-1: wake_end_sample is beyond"):
        ev.evaluate_manifest(manifest, comps)
    assert comps.vad.frames == 0 and comps.stt.calls == []            # A-1 (a valid entry) was not evaluated either


def test_manifest_fields_the_evaluator_consumes_are_validated_up_front_without_echo(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    with pytest.raises(ev.EvalInputError) as err:
        parse([synth_rec("A-1", path, expected_wake_phrase="Hey, Jarvis!")], tmp_path)
    assert "expected_wake_phrase must be plain words" in str(err.value) and "Jarvis!" not in str(err.value)
    with pytest.raises(ev.EvalInputError) as err:
        parse([synth_rec("A-2", path, reference_command_text="hey jarvis open the zebracrossing")], tmp_path)
    assert "still starts with the expected wake phrase" in str(err.value) and "zebracrossing" not in str(err.value)
    parse([synth_rec("A-3", path, reference_command_text="tell jarvis to open the door")], tmp_path)      # an internal word is fine


def test_a_bad_audio_path_message_does_not_echo_the_path(tmp_path, fake_repo):
    with pytest.raises(ev.EvalInputError) as err:
        parse([real_rec("R-1", fake_repo / "alice_smith.wav")], tmp_path)
    assert "alice" not in str(err.value).lower() and str(fake_repo) not in str(err.value)


class _FailingVad(EnergyVad16k):
    """Raises `error` when scoring frame number `at` of a run whose index is in `bad_runs`."""

    def __init__(self, error, bad_runs, at=3):
        super().__init__()
        self.error, self.bad_runs, self.at, self.run = error, set(bad_runs), at, -1
        self.calls_this_run = 0

    def reset(self):
        self.run += 1
        self.calls_this_run = 0

    def _score_frame(self, frame):
        self.calls_this_run += 1
        if self.run in self.bad_runs and self.calls_this_run >= self.at:
            raise self.error("scripted VAD failure")
        return super()._score_frame(frame)


def _components_with_vad(vad, replies=("hey jarvis turn on the lights",)):
    comps = make_components(replies)
    comps.vad = vad
    comps.timed_vad = ev.TimedVad(vad)
    return comps


def test_a_safe_per_recording_failure_is_recorded_on_that_item_and_the_run_continues(tmp_path):
    paths = [write_wav(tmp_path / f"c{k}.wav", clip()) for k in range(3)]
    manifest = parse([synth_rec(f"C-{k}", paths[k]) for k in range(3)], tmp_path)
    comps = _components_with_vad(_FailingVad(RuntimeError, bad_runs={1}))
    items, report = ev.evaluate_manifest(manifest, comps)
    assert [i["pipeline_status"] for i in items] == ["TRANSCRIBED", "PIPELINE_ERROR", "TRANSCRIBED"]
    assert items[1]["error_class"] == "RuntimeError" and report["run_status"] == "COMPLETE" and report["items_finished"] == 3


@pytest.mark.parametrize("error", [VadContractError, SttContractError])
def test_a_contract_violation_is_fatal_keeps_what_finished_and_labels_the_report_incomplete(tmp_path, error):
    paths = [write_wav(tmp_path / f"c{k}.wav", clip()) for k in range(3)]
    manifest = parse([synth_rec(f"C-{k}", paths[k]) for k in range(3)], tmp_path)
    if error is VadContractError:
        comps = _components_with_vad(_FailingVad(VadContractError, bad_runs={1}))
    else:
        comps = make_components([("hey jarvis turn on the lights"), SttContractError("model broke")])
    seen = []
    with pytest.raises(ev.EvalFatalError) as fatal:
        ev.evaluate_manifest(manifest, comps, on_item=seen.append)
    exc = fatal.value
    assert exc.error_class == error.__name__ and len(exc.items) == 1 and len(seen) == 1 and exc.items[0]["recording_id"] == "C-0"
    report = exc.report
    assert report["run_status"] == "INCOMPLETE_FATAL" and report["fatal_error_class"] == error.__name__
    assert (report["items_finished"], report["items_selected"]) == (1, 3)
    assert report["accuracy_results"] is None and "INCOMPLETE" in report["accuracy_results_note"]


def test_a_clock_error_and_an_unexpected_internal_error_are_fatal(tmp_path, monkeypatch):
    path = write_wav(tmp_path / "a.wav", clip())
    manifest = parse([synth_rec("A-1", path)], tmp_path)

    class ClockLiar(ev.AnnotatedWakeDetector):
        def process(self, chunk):
            d = super().process(chunk)
            self._samples_processed += 1                              # the detector count no longer matches what the pipeline fed it
            return d

    monkeypatch.setattr(ev, "AnnotatedWakeDetector", ClockLiar)
    with pytest.raises(ev.EvalFatalError) as fatal:
        ev.evaluate_manifest(manifest, make_components())
    assert fatal.value.error_class == "PipelineClockError"
    monkeypatch.undo()
    monkeypatch.setattr(ev, "evaluate_recording", lambda *a, **k: (_ for _ in ()).throw(KeyError("bug")))
    with pytest.raises(ev.EvalFatalError) as fatal:
        ev.evaluate_manifest(manifest, make_components())
    assert fatal.value.error_class == "KeyError" and fatal.value.report["run_status"] == "INCOMPLETE_FATAL"


def test_a_missing_wake_engine_is_fatal_but_an_ordinary_detector_error_is_per_recording(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    manifest = parse([synth_rec("A-1", path, "CLEAN_COMMAND", expected_wake_phrase="hey jarvis")], tmp_path)

    def unavailable():
        raise WakeWordBackendError("engine missing")

    with pytest.raises(ev.EvalFatalError) as fatal:
        ev.evaluate_manifest(manifest, make_components(detector_factory=unavailable), wake_mode="detector")
    assert fatal.value.error_class == "WakeWordBackendError"

    def flaky():
        raise RuntimeError("one detector hiccup")

    items, report = ev.evaluate_manifest(manifest, make_components(detector_factory=flaky), wake_mode="detector")
    assert items[0]["pipeline_status"] == "PIPELINE_ERROR" and items[0]["error_class"] == "RuntimeError" and report["run_status"] == "COMPLETE"


def test_the_evidence_writer_never_presents_partial_output_as_complete(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    manifest = parse([synth_rec(f"A-{k}", path) for k in range(2)], tmp_path)
    items, report = ev.evaluate_manifest(manifest, make_components())
    out = tmp_path / "evidence"
    writer = ev.EvidenceWriter(out, contains_real=False)
    assert (out / ev.ITEMS_PARTIAL).exists() and not (out / ev.ITEMS_FILENAME).exists()
    writer.add(items[0])
    assert len((out / ev.ITEMS_PARTIAL).read_text(encoding="utf-8").splitlines()) == 1        # flushed per item: a crash loses at most one
    writer.abort({**report, "run_status": "INCOMPLETE_FATAL", "fatal_error_class": "Boom"})
    assert sorted(p.name for p in out.iterdir()) == sorted([ev.REPORT_INCOMPLETE, ev.ITEMS_PARTIAL])
    assert not (out / ev.ITEMS_FILENAME).exists() and not (out / ev.REPORT_FILENAME).exists()      # the normal names mean 'finished'
    with pytest.raises(ev.EvalEvidenceError, match="already exists"):
        ev.EvidenceWriter(out, contains_real=False)                    # and nothing existing is overwritten


def test_only_a_complete_report_can_finish_and_the_report_name_appears_last(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    manifest = parse([synth_rec("A-1", path)], tmp_path)
    items, report = ev.evaluate_manifest(manifest, make_components())
    writer = ev.EvidenceWriter(tmp_path / "o1", contains_real=False)
    writer.add(items[0])
    with pytest.raises(ev.EvalEvidenceError, match="only a COMPLETE run"):
        writer.finish({**report, "run_status": "INCOMPLETE_FATAL"})
    writer2 = ev.EvidenceWriter(tmp_path / "o2", contains_real=False)
    writer2.add(items[0])
    items_path, report_path = writer2.finish(report)
    assert items_path.name == ev.ITEMS_FILENAME and report_path.name == ev.REPORT_FILENAME
    assert sorted(p.name for p in (tmp_path / "o2").iterdir()) == sorted([ev.ITEMS_FILENAME, ev.REPORT_FILENAME])
    assert json.loads(report_path.read_text(encoding="utf-8"))["run_status"] == "COMPLETE"
    discard = ev.EvidenceWriter(tmp_path / "o3", contains_real=False)
    discard.discard()
    assert list((tmp_path / "o3").iterdir()) == []


def test_the_cli_exits_3_on_a_fatal_failure_and_leaves_only_incomplete_evidence(tmp_path, monkeypatch, capsys):
    paths = [write_wav(tmp_path / f"c{k}.wav", clip()) for k in range(2)]
    man = tmp_path / "m.json"
    man.write_text(json.dumps(manifest_dict([synth_rec(f"C-{k}", paths[k]) for k in range(2)])), encoding="utf-8")
    monkeypatch.setattr(ev, "build_real_components", lambda *a, **k: make_components(["hey jarvis turn on the lights", SttContractError("secretwords")]))
    out = tmp_path / "out"
    assert ev.main(["run", "--manifest", str(man), "--output", str(out)]) == 3
    err = capsys.readouterr().err
    assert "FATAL" in err and "SttContractError" in err and "secretwords" not in err and "INCOMPLETE" in err
    assert sorted(p.name for p in out.iterdir()) == sorted([ev.ITEMS_PARTIAL, ev.REPORT_INCOMPLETE])
    assert json.loads((out / ev.REPORT_INCOMPLETE).read_text(encoding="utf-8"))["run_status"] == "INCOMPLETE_FATAL"
    assert len((out / ev.ITEMS_PARTIAL).read_text(encoding="utf-8").splitlines()) == 1


def test_the_cli_writes_the_normal_names_for_a_finished_run(tmp_path, monkeypatch, capsys):
    path = write_wav(tmp_path / "a.wav", clip())
    man = tmp_path / "m.json"
    man.write_text(json.dumps(manifest_dict([synth_rec("A-1", path)])), encoding="utf-8")
    monkeypatch.setattr(ev, "build_real_components", lambda *a, **k: make_components())
    out = tmp_path / "out"
    assert ev.main(["run", "--manifest", str(man), "--output", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted([ev.ITEMS_FILENAME, ev.REPORT_FILENAME])
    assert json.loads((out / ev.REPORT_FILENAME).read_text(encoding="utf-8"))["run_status"] == "COMPLETE"


# =========================================================================================
# M6 - invalid configuration is a clean exit 2 before any model is loaded
# =========================================================================================

def _manifest_file(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    man = tmp_path / "m.json"
    man.write_text(json.dumps(manifest_dict([synth_rec("A-1", path)])), encoding="utf-8")
    return man


@pytest.mark.parametrize("change", [
    dict(start_threshold=0.3, end_threshold=0.35),                    # hysteresis violated
    dict(end_silence_s=0), dict(max_utterance_s=-1), dict(pre_roll_s=50.0),
    dict(start_threshold="SECRETVALUE"), dict(start_threshold=True),
])
def test_an_invalid_segmenter_config_file_is_a_clean_error_with_no_model_and_no_traceback(tmp_path, monkeypatch, capsys, change):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({**ev.EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES, **change}), encoding="utf-8")
    _forbidden_models(monkeypatch)
    out = tmp_path / "out"
    assert ev.main(["run", "--manifest", str(_manifest_file(tmp_path)), "--output", str(out), "--segmenter-config", str(cfg)]) == 2
    err = capsys.readouterr().err
    assert "segmenter configuration is invalid" in err and "Traceback" not in err and "SECRETVALUE" not in err and not out.exists()


@pytest.mark.parametrize("content", ["not json", "[]", '{"start_threshold": 0.5}', json.dumps({**ev.EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES, "gain": 1})])
def test_malformed_config_files_are_clean_errors(tmp_path, monkeypatch, capsys, content):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(content, encoding="utf-8")
    _forbidden_models(monkeypatch)
    assert ev.main(["run", "--manifest", str(_manifest_file(tmp_path)), "--output", str(tmp_path / "o"), "--segmenter-config", str(cfg)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("ERROR:") and "Traceback" not in err and str(tmp_path) not in err


def test_a_configuration_error_raised_late_is_still_a_clean_exit(tmp_path, monkeypatch, capsys):
    def late(*a, **k):
        raise SegmenterConfigError("frame mismatch")
    monkeypatch.setattr(ev, "build_real_components", late)
    assert ev.main(["run", "--manifest", str(_manifest_file(tmp_path)), "--output", str(tmp_path / "o")]) == 2
    assert "SegmenterConfigError" in capsys.readouterr().err


def _grid(tmp_path, grid):
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(grid), encoding="utf-8")
    return path


@pytest.mark.parametrize("grid,message", [
    ({"base": {"start_threshold": 0.2, "end_threshold": 0.35}, "axes": {"end_silence_s": [0.2]}}, "segmenter configuration is invalid"),
    ({"axes": {"start_threshold": [0.2, 0.1]}}, "no runnable configuration"),                       # every candidate breaks the hysteresis
    ({"axes": {"max_utterance_s": [31.0, 60.0]}}, "no runnable configuration"),                      # every candidate exceeds the STT limit
    ({"axes": {"gain": [1]}}, "cannot sweep"),
    ({"axes": {"start_threshold": []}}, "non-empty list"),
    ({"axes": {"start_threshold": [0.5]}, "extra": 1}, "grid must be an object"),
])
def test_the_calibration_cli_validates_base_and_grid_before_loading_a_model(tmp_path, monkeypatch, capsys, grid, message):
    _forbidden_models(monkeypatch)
    out = tmp_path / "out"
    assert cal.main(["--manifest", str(_manifest_file(tmp_path)), "--grid", str(_grid(tmp_path, grid)), "--output", str(out)]) == 2
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err and not out.exists()


def test_the_calibration_cli_handles_a_late_configuration_error_and_a_fatal_failure_cleanly(tmp_path, monkeypatch, capsys):
    grid = _grid(tmp_path, {"axes": {"start_threshold": [0.5]}})
    man = _manifest_file(tmp_path)

    def late(*a, **k):
        raise SegmenterConfigError("frame mismatch")
    monkeypatch.setattr(ev, "build_real_components", late)
    assert cal.main(["--manifest", str(man), "--grid", str(grid), "--output", str(tmp_path / "o1")]) == 2
    monkeypatch.setattr(ev, "build_real_components", lambda *a, **k: make_components([SttContractError("x")]))
    assert cal.main(["--manifest", str(man), "--grid", str(grid), "--output", str(tmp_path / "o2")]) == 3
    err = capsys.readouterr().err
    assert "FATAL" in err and "no sweep file was written" in err and not list((tmp_path / "o2").glob("*.json"))


@pytest.mark.parametrize("module", ["evaluation.week7_voice_eval", "evaluation.week7_calibration"])
def test_both_clis_never_print_a_traceback_for_a_bad_config_in_a_real_subprocess(tmp_path, module):
    man = _manifest_file(tmp_path)
    cfg = tmp_path / "bad.json"
    cfg.write_text(json.dumps({**ev.EVAL_DEFAULT_UNCALIBRATED_SEGMENTER_VALUES, "start_threshold": 0.1, "end_threshold": 0.4}), encoding="utf-8")
    if module.endswith("eval"):
        args = ["run", "--manifest", str(man), "--output", str(tmp_path / "o"), "--segmenter-config", str(cfg)]
    else:
        grid = _grid(tmp_path, {"base": json.loads(cfg.read_text()), "axes": {"end_silence_s": [0.2]}})
        args = ["--manifest", str(man), "--grid", str(grid), "--output", str(tmp_path / "o")]
    done = subprocess.run([sys.executable, "-m", module, *args], cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "segmenter configuration is invalid" in done.stderr


def test_the_documented_exit_codes_are_in_the_help(capsys):
    assert "exit codes" in ev._parser().epilog
    with pytest.raises(SystemExit):
        cal.main(["--help"])
    assert "exit codes" in capsys.readouterr().out


def test_an_annotation_exactly_at_the_end_of_the_clip_is_accepted_and_one_sample_beyond_is_not(tmp_path):
    path = write_wav(tmp_path / "a.wav", clip())
    total = clip().size
    ev.preflight_manifest(list(parse([synth_rec("OK", path, wake_end_sample=total, speech_start_sample=10, speech_end_sample=total)], tmp_path).recordings),
                          wake_mode="annotated", detector_available=False)
    with pytest.raises(ev.EvalInputError, match="wake_end_sample is beyond"):
        ev.preflight_manifest(list(parse([synth_rec("BAD", path, wake_end_sample=total + 1)], tmp_path).recordings),
                              wake_mode="annotated", detector_available=False)


def test_an_incomplete_real_run_reports_no_accuracy_results_at_all(tmp_path):
    paths = [write_wav(tmp_path / f"r{k}.wav", clip()) for k in range(2)]
    manifest = parse([real_rec("R-1", paths[0]), real_rec("R-2", paths[1])], tmp_path)
    comps = make_components(["hey jarvis turn on the lights", SttContractError("x")])
    frozen = {"kind": "FILE", "sha256": "a" * 64, "frozen": True}
    comps.segmenter_source = frozen
    with pytest.raises(ev.EvalFatalError) as fatal:
        ev.evaluate_manifest(manifest, comps)
    report = fatal.value.report
    assert report["corpus_kind"] == "REAL_RECORDINGS" and report["items_finished"] == 1 and report["run_status"] == "INCOMPLETE_FATAL"
    assert report["accuracy_results"] is None and "INCOMPLETE" in report["accuracy_results_note"] and "none may be quoted" in report["accuracy_results_note"]
    ok_items, ok_report = ev.evaluate_manifest(manifest, make_components(["hey jarvis turn on the lights"]))
    assert ok_report["run_status"] == "COMPLETE" and ok_report["accuracy_results"] is not None       # the same corpus, finished


def test_a_request_rejected_after_the_writer_was_opened_leaves_no_partial_file(tmp_path, monkeypatch, capsys):
    path = write_wav(tmp_path / "a.wav", clip())
    man = tmp_path / "m.json"
    man.write_text(json.dumps(manifest_dict([synth_rec("A-1", path)])), encoding="utf-8")
    monkeypatch.setattr(ev, "build_real_components", lambda *a, **k: make_components())

    def rejected(*a, **k):
        raise ev.EvalInputError("rejected before anything was evaluated")
    monkeypatch.setattr(ev, "evaluate_manifest", rejected)
    out = tmp_path / "out"
    assert ev.main(["run", "--manifest", str(man), "--output", str(out)]) == 2
    assert list(out.iterdir()) == []                                  # no empty .partial file and no INCOMPLETE report


def test_the_documented_exit_codes_are_in_the_eval_help():
    epilog = ev._parser().epilog
    assert "0 finished" in epilog and "2 invalid manifest / configuration / environment / output location (nothing evaluated)" in epilog
    assert "3 fatal failure during the evaluation (partial INCOMPLETE evidence was written)" in epilog
