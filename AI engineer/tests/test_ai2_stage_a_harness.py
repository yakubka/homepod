"""Team Leader AI-2 / Stage A: the DEVELOPMENT / INTEGRATION harness (scripts/ai2_stage_a_transcribe.py).

SOFTWARE-VALIDATED orchestration only. The recordings here are generated deterministic PCM, the VAD is a scripted
test double and the STT is usually a fake. Nothing in this file says anything about speech-recognition accuracy,
VAD accuracy or the microphone. The two tests at the end plug the REAL tiny.en engine (and the real Silero model)
into the harness on synthetic audio: "the real backend integrates", nothing more. No Hardware Engineer recording
exists, so no Stage A run has been performed.
"""

import hashlib
import itertools
import json
import logging
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from config import settings
from scripts import ai2_stage_a_transcribe as h
from speech.stt import SttEngine, SttModelNotFound
from tests.voice_synthetic import ScriptedVadEngine, index_pcm, region_scores

REPO_ROOT = Path(__file__).resolve().parent.parent
RATE, FRAME = 16000, 512
TOTAL = 64000                           # 4 s
SPEECH = [(16000, 32000)]               # scripted speech 1.0 s - 2.0 s
# With the harness's development values, frames of 512 samples and SPEECH above:
#   first frame fully inside the region starts at 16384 (the confirmed onset); onset padding 0.1 s = 1600 samples
#   -> segment start 14784. The last fully-inside frame ends at 31744 -> segment end 31744.
#   End silence 0.7 s = 11200 samples = 22 frames -> the decision falls at the end of frame 83 = sample 43008.
SEG_START, SEG_END, SEG_ONSET, DECIDED = 14784, 31744, 16384, 43008


def make_wav(path, samples=None, rate=RATE, channels=1, sampwidth=2, n=TOTAL):
    if samples is None:
        samples = index_pcm(0, n)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes() if sampwidth == 2 else bytes(len(samples) * sampwidth * channels))
    return path


def vad(speech=SPEECH, total=TOTAL):
    return ScriptedVadEngine(region_scores(0, total // FRAME + 4, speech, frame=FRAME), frame_samples=FRAME, sample_rate=RATE)


class FakeStt(SttEngine):
    sample_rate = RATE
    engine_id = "fake-stt"
    model_id = "fake-model"

    def __init__(self, reply="how many people are in the room", error=None, verified=None):
        super().__init__()
        self.reply, self.error, self.calls = reply, error, []
        if verified is not None:
            self.snapshot_verified = verified

    def _transcribe(self, samples):
        self.calls.append(samples.copy())
        if self.error:
            raise self.error
        return self.reply


def prov_dict(**over):
    d = {"recording_id": "HW-001", "speaker_code": "S01", "consent_reference": "NOT_APPLICABLE", "recording_origin": "SYNTHETIC",
         "microphone": "UNKNOWN", "distance_m": 1.0, "reference_transcript": "how many people are in the room"}
    d.update(over)
    return {k: v for k, v in d.items() if v is not None}


@pytest.fixture
def env(tmp_path):
    wav = make_wav(tmp_path / "rec.wav")
    prov = tmp_path / "rec.prov.json"
    prov.write_text(json.dumps(prov_dict()), encoding="utf-8")
    out = tmp_path / "evidence"
    return wav, prov, out


_RUNS = itertools.count()


def run(env, vad_engine=None, stt_engine=None, **kw):
    """Each call writes into its own evidence sub-directory (the harness refuses to overwrite evidence)."""
    wav, prov, out = env
    return h.run_stage_a(wav, prov, out / f"run{next(_RUNS)}", vad_engine=vad_engine or vad(), stt_engine=stt_engine or FakeStt(), **kw)


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    h.logger.addHandler(handler)
    yield handler.lines
    h.logger.removeHandler(handler)


# -- the happy path: sample indices, one STT call, the record --------------------------------------------------------------

def test_one_recording_gives_one_segment_one_stt_call_and_a_valid_record(env):
    stt = FakeStt()
    outcome = run(env, stt_engine=stt)
    r = outcome.record
    assert outcome.exit_code == 0 and outcome.result_path.name == "HW-001.stage_a.json" and outcome.result_path.is_file()
    assert len(stt.calls) == 1                                                  # exactly one call, after the segment was complete
    assert np.array_equal(stt.calls[0], index_pcm(SEG_START, SEG_END - SEG_START))   # the segment IS stream[start:end]
    assert (r["segment_outcome"], r["segment_start_sample"], r["segment_end_sample"], r["segment_onset_sample"]) == ("COMPLETE", SEG_START, SEG_END, SEG_ONSET)
    assert r["segment_duration_s"] == pytest.approx((SEG_END - SEG_START) / RATE)
    assert r["decided_at_sample"] == DECIDED and r["unscored_samples"] == 0 and r["samples_after_decision"] == TOTAL - DECIDED
    assert (r["stt_status"], r["actual_transcript"], r["stt_engine"], r["stt_model"]) == ("OK", "how many people are in the room", "fake-stt", "fake-model")
    assert r["inference_ms"] >= 0 and r["activation_mode"] == "DEVELOPMENT_STAGE_A" and r["activation_sample"] == 0
    h.validate_result(r)
    assert json.loads(outcome.result_path.read_text(encoding="utf-8")) == json.loads(json.dumps(r))


def test_the_vad_engine_saw_one_contiguous_stream_from_sample_zero(env):
    engine = vad()
    run(env, vad_engine=engine)
    seen = np.concatenate(engine.seen_frames)
    assert np.array_equal(seen, index_pcm(0, seen.size))          # no gap, no overlap, nothing reordered
    assert engine.reset_calls == 1                                # activate() reset the engine once


def test_speech_that_runs_to_the_end_of_the_file_is_finished_at_eof(env):
    wav, _p, _o = env
    stt = FakeStt()
    outcome = run(env, vad_engine=vad(speech=[(30000, TOTAL)]), stt_engine=stt)
    r = outcome.record
    assert r["segment_outcome"] == "STREAM_ENDED" and r["segment_end_sample"] == (TOTAL // FRAME) * FRAME
    assert r["unscored_samples"] == TOTAL - (TOTAL // FRAME) * FRAME           # the partial final frame is reported, never padded
    assert len(stt.calls) == 1 and outcome.exit_code == 0


def test_no_speech_means_no_stt_and_a_non_zero_exit(env):
    stt = FakeStt()
    outcome = run(env, vad_engine=vad(speech=[]), stt_engine=stt)
    r = outcome.record
    assert r["segment_outcome"] == "NO_SPEECH_STREAM_ENDED" and r["stt_status"] == "NOT_RUN" and r["actual_transcript"] == ""
    assert r["inference_ms"] is None and r["segment_start_sample"] is None and r["human_match"] == "NOT_REVIEWED"
    assert stt.calls == [] and outcome.exit_code == 1
    h.validate_result(r)


def test_a_too_short_burst_is_recorded_and_not_transcribed(env):
    stt = FakeStt()
    outcome = run(env, vad_engine=vad(speech=[(16384, 16384 + 5 * FRAME)]), stt_engine=stt)
    assert outcome.record["segment_outcome"] == "TOO_SHORT" and stt.calls == [] and outcome.exit_code == 1


def test_no_speech_timeout_on_a_long_silent_recording(tmp_path):
    n = 11 * RATE
    wav = make_wav(tmp_path / "long.wav", n=n)
    prov = tmp_path / "p.json"
    prov.write_text(json.dumps(prov_dict()), encoding="utf-8")
    stt = FakeStt()
    outcome = h.run_stage_a(wav, prov, tmp_path / "out", vad_engine=vad(speech=[], total=n), stt_engine=stt)
    assert outcome.record["segment_outcome"] == "NO_SPEECH_TIMEOUT" and stt.calls == [] and outcome.exit_code == 1


def test_stt_empty_text_is_recorded_as_empty_text(env):
    outcome = run(env, stt_engine=FakeStt(reply="   "))
    r = outcome.record
    assert (r["stt_status"], r["actual_transcript"], outcome.exit_code) == ("EMPTY_TEXT", "", 0)
    assert r["word_error_rate"] == 1.0 and r["character_error_rate"] == 1.0         # nothing recognised against a reference
    assert r["human_match"] == "NOT_REVIEWED"


def test_an_stt_exception_is_an_explicit_failure_record_without_the_message(env):
    stt = FakeStt(error=RuntimeError("secret words inside an exception"))
    outcome = run(env, stt_engine=stt)
    r = outcome.record
    assert outcome.exit_code == 3 and r["stt_status"] == "FAILED" and r["actual_transcript"] == ""
    assert r["run_error"] == {"type": "SttInferenceFailed", "stage": "stt"}
    assert "secret" not in json.dumps(r)
    assert outcome.result_path.is_file()


def test_model_missing_and_verification_failure_are_explicit_run_errors(env, monkeypatch):
    from speech import faster_whisper_backend as fb

    class Missing:
        def __init__(self, *a, **k):
            raise SttModelNotFound("no model")

    class BadHash:
        def __init__(self, *a, **k):
            raise fb.SttModelHashMismatch("model.bin: SHA-256 does not match")

    for cls, expected in ((Missing, "SttModelNotFound"), (BadHash, "SttModelHashMismatch")):
        monkeypatch.setattr(fb, "FasterWhisperEngine", cls)
        wav, prov, out = env
        out2 = out / expected
        outcome = h.run_stage_a(wav, prov, out2, vad_engine=vad())
        r = outcome.record
        assert outcome.exit_code == 3 and r["run_error"] == {"type": expected, "stage": "build_stt"}
        assert r["stt_model_verified"] is False and r["stt_status"] == "NOT_RUN"


def test_a_missing_vad_artifact_is_an_explicit_run_error(env, monkeypatch):
    from audio import silero_vad

    class Missing:
        def __init__(self, *a, **k):
            raise silero_vad.SileroModelNotFound("no silero")

    monkeypatch.setattr(silero_vad, "SileroOnnxVadEngine", Missing)
    wav, prov, out = env
    outcome = h.run_stage_a(wav, prov, out, stt_engine=FakeStt())
    assert outcome.exit_code == 3 and outcome.record["run_error"] == {"type": "SileroModelNotFound", "stage": "build_vad"}


# -- provenance honesty and labels ---------------------------------------------------------------------------------------------

def test_model_verification_is_never_claimed_for_a_fake_and_follows_the_engine(env):
    assert run(env).record["stt_model_verified"] is False
    assert run(env, stt_engine=FakeStt(verified=False)).record["stt_model_verified"] is False
    wav, prov, out = env
    verified = run(env, stt_engine=FakeStt(verified=True))
    assert verified.record["stt_model_verified"] is True
    assert verified.record["vad"]["artifact_verified"] is False                # the scripted VAD has no artifact


@pytest.mark.parametrize("origin,label", [("HARDWARE_ENGINEER_RECORDING", "RECORDED-HARDWARE AUDIO"),
                                          ("SYNTHETIC", "SOFTWARE-VALIDATED (SYNTHETIC INPUT)"),
                                          ("UNKNOWN", "UNVERIFIED RECORDING ORIGIN")])
def test_validation_label_follows_the_declared_origin_and_is_never_live(env, origin, label):
    wav, prov, out = env
    consent = "NOT_APPLICABLE" if origin == "SYNTHETIC" else "CONSENT-S01"
    prov.write_text(json.dumps(prov_dict(recording_origin=origin, consent_reference=consent)), encoding="utf-8")
    r = run(env).record
    assert r["validation_label"] == label and "LIVE" not in r["validation_label"].upper()


def test_a_live_label_is_rejected_by_the_validator(env):
    r = dict(run(env).record)
    r["validation_label"] = "LIVE HARDWARE-VALIDATED"
    with pytest.raises(h.HarnessError):
        h.validate_result(r)


def test_unknown_metadata_is_recorded_as_unknown_and_never_inferred(env):
    wav, prov, out = env
    prov.write_text(json.dumps({"recording_id": "HW-002", "speaker_code": "S02", "consent_reference": "CONSENT-S02",
                                "recording_origin": "HARDWARE_ENGINEER_RECORDING"}), encoding="utf-8")
    r = run(env).record
    p = r["recording_provenance"]
    for key in ("microphone", "recording_host", "room_noise_condition", "distance_m", "recorded_at", "processing_history"):
        assert p[key] == "UNKNOWN", key
    assert p["consent_reference"] == "CONSENT-S02"                     # consent is the one field that may not be UNKNOWN
    assert r["expected_transcript"] == "NOT_AVAILABLE" and r["word_error_rate"] == "NOT_AVAILABLE" and r["character_error_rate"] == "NOT_AVAILABLE"
    assert r["warnings"] == []
    assert p["source_filename"] == "rec.wav"


def test_negative_control_has_no_reference_and_no_metrics_and_is_still_transcribed(env):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(recording_kind="NEGATIVE_CONTROL", reference_transcript=None)), encoding="utf-8")
    r = run(env, stt_engine=FakeStt(reply="You")).record                       # whatever the pipeline does is recorded as-is
    assert r["recording_kind"] == "NEGATIVE_CONTROL" and r["expected_transcript"] == "" and r["actual_transcript"] == "You"
    assert r["word_error_rate"] == "NOT_AVAILABLE"


def test_conversion_history_records_both_files_and_the_recording_hash_is_of_the_file_given(env):
    wav, prov, out = env
    original = "ab" * 32
    prov.write_text(json.dumps(prov_dict(conversion={"description": "48 kHz stereo -> 16 kHz mono (channel 0)", "original_filename": "orig48.wav", "original_sha256": original})), encoding="utf-8")
    r = run(env).record
    assert r["conversion"] == {"applied": True, "description": "48 kHz stereo -> 16 kHz mono (channel 0)", "original_filename": "orig48.wav", "original_sha256": original}
    assert r["recording_sha256"] == hashlib.sha256(wav.read_bytes()).hexdigest()


def test_the_original_recording_is_never_modified_and_only_one_file_is_written(env):
    wav, prov, out = env
    before, mtime = wav.read_bytes(), wav.stat().st_mtime_ns
    run(env)
    assert wav.read_bytes() == before and wav.stat().st_mtime_ns == mtime
    outcome = run(env)
    assert sorted(p.name for p in outcome.result_path.parent.iterdir()) == ["HW-001.stage_a.json"]
    assert sorted(p.name for p in wav.parent.iterdir() if p.suffix == ".wav") == ["rec.wav"]


# -- WER / CER only with a human reference -------------------------------------------------------------------------------------

def test_wer_and_cer_against_a_human_reference(env):
    r = run(env, stt_engine=FakeStt(reply="How many people are in the roof?")).record
    assert r["word_error_rate"] == pytest.approx(1 / 7, abs=1e-4)
    assert 0 < r["character_error_rate"] < 0.1
    assert run(env, stt_engine=FakeStt(reply="how many PEOPLE are in the room.")).record["word_error_rate"] == 0.0


def test_error_rate_helpers():
    assert h.error_rates("How many people?", "how many people") == (0.0, 0.0)
    wer, cer = h.error_rates("one two three four", "one too three")
    assert wer == pytest.approx(2 / 4) and cer == pytest.approx(6 / 18)        # "too" for "two" (1 char) + the missing word " four" (5 chars)
    assert h.error_rates("", "anything") == (None, None) and h.error_rates("...", "x") == (None, None)
    assert h.edit_distance("kitten", "sitting") == 3 and h.edit_distance([], ["a", "b"]) == 2
    assert h.normalize_for_metrics("  It's  17, OK! ") == "it's 17 ok"


# -- input and provenance validation ---------------------------------------------------------------------------------------------

def test_wav_missing_wrong_format_malformed_and_empty_all_fail_before_anything_is_written(tmp_path):
    prov = tmp_path / "p.json"
    prov.write_text(json.dumps(prov_dict()), encoding="utf-8")
    out = tmp_path / "out"
    bad = {
        "missing.wav": None,
        "stereo.wav": lambda p: make_wav(p, np.zeros(2000, dtype=np.int16), channels=2),
        "r44.wav": lambda p: make_wav(p, np.zeros(2000, dtype=np.int16), rate=44100),
        "w24.wav": lambda p: make_wav(p, np.zeros(2000, dtype=np.int16), sampwidth=3),
        "garbage.wav": lambda p: p.write_bytes(b"this is not a wav file at all"),
        "empty.wav": lambda p: make_wav(p, np.zeros(0, dtype=np.int16)),
    }
    for name, maker in bad.items():
        path = tmp_path / name
        if maker:
            maker(path)
        with pytest.raises(h.HarnessInputError):
            h.run_stage_a(path, prov, out, vad_engine=vad(), stt_engine=FakeStt())
    assert not out.exists()                                                   # not even the evidence directory was created


def test_a_non_native_wav_message_says_nothing_was_converted_and_points_to_the_handoff(tmp_path):
    wav = make_wav(tmp_path / "s.wav", np.zeros(2000, dtype=np.int16), channels=2)
    prov = tmp_path / "p.json"
    prov.write_text(json.dumps(prov_dict()), encoding="utf-8")
    with pytest.raises(h.HarnessInputError) as info:
        h.run_stage_a(wav, prov, tmp_path / "o")
    text = str(info.value)
    assert "does not resample" in text and "COPY" in text and "handoff" in text


@pytest.mark.parametrize("change", [
    {"recording_id": None}, {"recording_id": "bad id with spaces"}, {"recording_id": "../escape"}, {"recording_id": "TEMPLATE-DO-NOT-USE"},
    {"speaker_code": "Alice"}, {"speaker_code": "S1"}, {"speaker_code": None}, {"consent_reference": None}, {"consent_reference": " "},
    {"consent_reference": "UNKNOWN"}, {"consent_reference": "CONSENT-S01"},                    # SYNTHETIC audio must say NOT_APPLICABLE
    {"recording_origin": "LIVE"}, {"recording_kind": "OTHER"}, {"recorded_at": "yesterday"}, {"distance_m": -1}, {"distance_m": "far"},
    {"distance_m": True}, {"microphone": ""}, {"processing_history": "none"}, {"processing_history": [1]},
    {"declared_format": {"sample_rate": 0}}, {"declared_format": {"bitrate": 1}}, {"recording_sha256": "xyz"},
    {"conversion": {"description": "x", "original_filename": "o.wav"}},                                     # no original SHA-256
    {"conversion": {"description": "x", "original_filename": "o.wav", "original_sha256": "short"}},
    {"reference_transcript": ""}, {"unexpected_field": 1}, {"notes": 5},
])
def test_invalid_provenance_is_rejected(env, change):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(**change)), encoding="utf-8")
    with pytest.raises(h.HarnessInputError):
        h.run_stage_a(wav, prov, out, vad_engine=vad(), stt_engine=FakeStt())
    assert not out.exists()


HUMAN_ORIGINS = ["HARDWARE_ENGINEER_RECORDING", "UNKNOWN"]       # an origin we cannot vouch for is treated as a real person's voice


def test_real_recording_with_an_explicit_consent_reference_is_accepted(env):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(recording_origin="HARDWARE_ENGINEER_RECORDING", consent_reference="CONSENT-S01")), encoding="utf-8")
    outcome = run(env)
    assert outcome.exit_code == 0
    assert outcome.record["recording_provenance"]["consent_reference"] == "CONSENT-S01"
    assert outcome.record["recording_provenance"]["speaker_code"] == "S01"        # coded; no real name is asked for anywhere
    assert outcome.record["validation_label"] == "RECORDED-HARDWARE AUDIO"


def test_a_recording_of_unstated_origin_is_treated_as_real_and_needs_consent(env):
    wav, prov, out = env
    data = prov_dict(consent_reference="CONSENT-S01")
    data.pop("recording_origin")
    prov.write_text(json.dumps(data), encoding="utf-8")
    assert run(env).record["recording_provenance"]["recording_origin"] == "UNKNOWN"
    data["consent_reference"] = "UNKNOWN"
    prov.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(h.HarnessInputError, match="consent"):
        run(env)


@pytest.mark.parametrize("origin", HUMAN_ORIGINS)
@pytest.mark.parametrize("consent", ["UNKNOWN", "unknown", " Unknown ", "", "   ", None, "NOT_APPLICABLE", "not_applicable"])
def test_real_recording_without_an_explicit_consent_reference_is_rejected(env, origin, consent):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(recording_origin=origin, consent_reference=consent)), encoding="utf-8")
    with pytest.raises(h.HarnessInputError, match="consent"):
        h.run_stage_a(wav, prov, out, vad_engine=vad(), stt_engine=FakeStt())
    assert not out.exists()                                                       # nothing was processed or written


def test_synthetic_test_audio_uses_not_applicable_and_nothing_else(env):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(recording_origin="SYNTHETIC", consent_reference="NOT_APPLICABLE")), encoding="utf-8")
    assert run(env).record["recording_provenance"]["consent_reference"] == "NOT_APPLICABLE"
    for bad in ("CONSENT-S01", "UNKNOWN", "", None):
        prov.write_text(json.dumps(prov_dict(recording_origin="SYNTHETIC", consent_reference=bad)), encoding="utf-8")
        with pytest.raises(h.HarnessInputError, match="consent"):
            run(env)


def test_negative_control_with_a_reference_is_rejected(env):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(recording_kind="NEGATIVE_CONTROL")), encoding="utf-8")        # the default reference is still there
    with pytest.raises(h.HarnessInputError):
        run(env)


def test_provenance_file_problems(tmp_path):
    for content in (None, "not json", "[]", "5"):
        p = tmp_path / "p.json"
        if content is not None:
            p.write_text(content, encoding="utf-8")
        with pytest.raises(h.HarnessInputError):
            h.load_provenance(p)
        p.unlink(missing_ok=True)


def test_provenance_that_disagrees_with_the_file_is_rejected(env):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(recording_sha256="0" * 64)), encoding="utf-8")
    with pytest.raises(h.HarnessInputError, match="does not match the file"):
        run(env)
    prov.write_text(json.dumps(prov_dict(declared_format={"sample_rate": 48000})), encoding="utf-8")
    with pytest.raises(h.HarnessInputError, match="declared_format"):
        run(env)
    good = hashlib.sha256(wav.read_bytes()).hexdigest()
    prov.write_text(json.dumps(prov_dict(recording_sha256=good, declared_format={"sample_rate": 16000, "channels": 1, "bit_depth": 16})), encoding="utf-8")
    assert run(env).exit_code == 0


def test_evidence_location_problems(env, tmp_path):
    wav, prov, out = env
    inside = REPO_ROOT / "_stage_a_must_not_exist"
    with pytest.raises(h.HarnessEvidenceError):
        h.run_stage_a(wav, prov, inside, vad_engine=vad(), stt_engine=FakeStt())
    assert not inside.exists()
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    with pytest.raises(h.HarnessEvidenceError):
        h.run_stage_a(wav, prov, blocker / "sub", vad_engine=vad(), stt_engine=FakeStt())
    shared = tmp_path / "shared_evidence"
    first = h.run_stage_a(wav, prov, shared, vad_engine=vad(), stt_engine=FakeStt())
    original_bytes = first.result_path.read_bytes()
    with pytest.raises(h.HarnessEvidenceError, match="will not be overwritten"):
        h.run_stage_a(wav, prov, shared, vad_engine=vad(), stt_engine=FakeStt(reply="different"))
    assert first.result_path.read_bytes() == original_bytes


# -- privacy ------------------------------------------------------------------------------------------------------------------------

def test_the_write_itself_refuses_to_overwrite_even_if_the_pre_check_was_raced(tmp_path):
    target = tmp_path / "HW-001.stage_a.json"
    target.write_text("original evidence", encoding="utf-8")
    with pytest.raises(h.HarnessEvidenceError, match="will not be overwritten"):
        h._write_json_exclusive(target, {"a": 1})
    assert target.read_text(encoding="utf-8") == "original evidence"


def test_transcripts_stay_out_of_logs_and_the_console_by_default(env, logs, capsys):
    wav, prov, out = env
    code = h.main(["run", "--wav", str(wav), "--provenance", str(prov), "--out-dir", str(out)], vad_engine=vad(), stt_engine=FakeStt(reply="my secret transcript"))
    captured = capsys.readouterr()
    assert code == 0
    assert "secret" not in captured.out and "secret" not in captured.err and not any("secret" in l for l in logs)
    assert "secret" in (out / "HW-001.stage_a.json").read_text(encoding="utf-8")        # only the evidence file carries it
    assert any("event=stage_a_done" in l for l in logs)


def test_show_transcript_prints_it_only_when_asked(env, capsys):
    wav, prov, out = env
    h.main(["run", "--wav", str(wav), "--provenance", str(prov), "--out-dir", str(out), "--show-transcript"], vad_engine=vad(), stt_engine=FakeStt(reply="visible words"))
    assert "visible words" in capsys.readouterr().out


def test_no_raw_pcm_is_serialised(env):
    wav, prov, out = env
    outcome = run(env)
    text = outcome.result_path.read_text(encoding="utf-8")
    forbidden = {"samples", "pcm", "audio", "frames", "waveform"}
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k.lower() not in forbidden, k
                walk(v)
        elif isinstance(node, list):
            assert len(node) <= 20                                           # no sample arrays
            for v in node:
                walk(v)
    walk(json.loads(text))
    assert len(text) < 20000
    assert index_pcm(SEG_START, 8).tobytes().hex() not in text and str(index_pcm(SEG_START, 3).tolist())[1:-1] not in text


def test_production_settings_are_not_touched_and_remain_uncalibrated(env):
    keys = [k for k in dir(settings) if k.isupper()]
    before = {k: getattr(settings, k) for k in keys}
    file_hash = hashlib.sha256(Path(settings.__file__).read_bytes()).hexdigest()
    r = run(env).record
    assert {k: getattr(settings, k) for k in keys} == before
    assert hashlib.sha256(Path(settings.__file__).read_bytes()).hexdigest() == file_hash
    for k in ("VAD_START_THRESHOLD", "VAD_END_THRESHOLD", "VAD_END_SILENCE_S", "VOICE_MAX_UTTERANCE_S"):
        assert getattr(settings, k) is None
    assert r["segmenter_config"]["values"] == h.DEV_SEGMENTER_VALUES and "UNCALIBRATED" in r["segmenter_config"]["status"]
    assert set(r["segmenter_config"]["rationale"]) == set(h.DEV_SEGMENTER_VALUES)


# -- schema, review, templates, docs --------------------------------------------------------------------------------------------------

def test_record_keys_are_exactly_the_schema_and_human_match_is_never_optimistic(env):
    r = run(env).record
    assert list(r) == list(h.RESULT_KEYS)
    assert r["human_match"] == "NOT_REVIEWED" and r["human_review_timestamp_utc"] is None
    for status_reply in ("ok words", "   "):
        assert run(env, stt_engine=FakeStt(reply=status_reply)).record["human_match"] == "NOT_REVIEWED"


def test_validator_rejects_inconsistent_records(env):
    good = run(env).record
    for key, value in (("recording_sha256", ""), ("stt_status", "SUCCESS"), ("human_match", "GOOD"), ("activation_mode", "WAKE"),
                       ("actual_transcript", ""), ("stt_model_verified", "yes"), ("inference_ms", -1)):
        bad = dict(good)
        bad[key] = value
        with pytest.raises(h.HarnessError):
            h.validate_result(bad)
    extra = dict(good, extra=1)
    with pytest.raises(h.HarnessError):
        h.validate_result(extra)
    failed = dict(good, stt_status="FAILED", actual_transcript="", inference_ms=None)
    with pytest.raises(h.HarnessError):
        h.validate_result(failed)                                            # FAILED without run_error


def test_review_command_sets_only_the_human_fields(env, capsys):
    wav, prov, out = env
    path = run(env).result_path
    before = json.loads(path.read_text(encoding="utf-8"))
    assert h.main(["review", str(path), "--human-match", "PARTIAL", "--notes", "dropped the last word"]) == 0
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["human_match"] == "PARTIAL" and after["notes"] == "dropped the last word" and after["human_review_timestamp_utc"]
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"human_match", "notes", "human_review_timestamp_utc"}
    assert not path.with_name(path.name + ".tmp").exists()
    with pytest.raises(h.HarnessInputError):
        h.review_result(path, "NOT_REVIEWED")
    with pytest.raises(h.HarnessInputError):
        h.review_result(path.parent / "nope.json", "MATCH")


def test_review_without_notes_keeps_the_existing_notes(env):
    wav, prov, out = env
    prov.write_text(json.dumps(prov_dict(notes="recorded by the hardware engineer")), encoding="utf-8")
    path = run(env).result_path
    h.review_result(path, "MATCH")
    assert json.loads(path.read_text(encoding="utf-8"))["notes"] == "recorded by the hardware engineer"


def test_the_recording_file_handle_is_released_after_a_run(env):
    wav, prov, out = env
    run(env)
    moved = wav.with_suffix(".moved")
    wav.replace(moved)                                  # fails on Windows while a handle is still open
    assert moved.is_file()


def test_main_returns_the_documented_exit_codes(env, tmp_path, capsys):
    wav, prov, out = env
    base = ["run", "--wav", str(wav), "--provenance", str(prov)]
    assert h.main(base + ["--out-dir", str(out / "a")], vad_engine=vad(), stt_engine=FakeStt()) == 0
    assert h.main(base + ["--out-dir", str(out / "b")], vad_engine=vad(speech=[]), stt_engine=FakeStt()) == 1
    assert h.main(["run", "--wav", str(tmp_path / "nope.wav"), "--provenance", str(prov), "--out-dir", str(out / "c")]) == 2
    assert h.main(base + ["--out-dir", str(out / "d")], vad_engine=vad(), stt_engine=FakeStt(error=RuntimeError("x"))) == 3
    assert "ERROR:" in capsys.readouterr().err


def test_command_line_entry_points_work_and_name_the_harness():
    for argv in ([sys.executable, "-m", "scripts.ai2_stage_a_transcribe", "--help"], [sys.executable, "scripts/ai2_stage_a_transcribe.py", "--help"]):
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(REPO_ROOT))
        assert proc.returncode == 0, proc.stderr
        flat = " ".join(proc.stdout.split())                                   # argparse wraps long lines
        assert "STAGE A" in flat.upper() and "not production code" in flat


def test_templates_are_templates_not_results():
    result = json.loads((REPO_ROOT / "evaluation" / "ai2_stage_a_result_template.json").read_text(encoding="utf-8"))
    assert "NO HARDWARE RUN YET" in result["_template"] and "TEMPLATE" in result["_template"]
    assert set(result) - {"_template"} == set(h.RESULT_KEYS)
    with pytest.raises(h.HarnessError):
        h.validate_result(result)
    provenance = json.loads((REPO_ROOT / "evaluation" / "ai2_stage_a_provenance_template.json").read_text(encoding="utf-8"))
    assert "NO HARDWARE RUN YET" in provenance["notes"]
    with pytest.raises(h.HarnessInputError, match="TEMPLATE"):
        h.validate_provenance(provenance)
    provenance["recording_id"] = "HW-009"
    provenance["reference_transcript"] = "how many people are in the room"
    assert h.validate_provenance(provenance)["recording_id"] == "HW-009"       # the template's shape is accepted once it is made real


def test_the_handoff_document_states_the_essentials():
    text = (REPO_ROOT / "docs" / "ai2_stage_a_hardware_handoff.md").read_text(encoding="utf-8")
    for needle in ("16 kHz", "mono", "16-bit", "keep the original", "UNKNOWN", "No personal names", "NOT YET PERFORMED", "negative control", "consent"):
        assert needle.lower() in text.lower(), needle


# =============================================================================================================
# Real-model plumbing (skipped when the gated artifacts are absent). "The real backend integrates with the
# harness" and nothing else: the audio is synthetic and no accuracy is implied.
# =============================================================================================================

def _real_stt():
    from speech.faster_whisper_backend import FasterWhisperEngine
    from speech.stt import SttRuntimeUnavailable
    try:
        return FasterWhisperEngine()
    except SttModelNotFound:
        pytest.skip("approved tiny.en snapshot not present")
    except SttRuntimeUnavailable as exc:
        pytest.skip(f"faster-whisper not importable: {exc}")


def test_real_stt_backend_integrates_with_the_harness_on_synthetic_audio(env):
    stt = _real_stt()
    tone = (3000 * np.sin(2 * np.pi * 220 * np.arange(TOTAL) / RATE)).astype(np.int16)
    wav, prov, out = env
    make_wav(wav, tone)
    r = h.run_stage_a(wav, prov, out, vad_engine=vad(), stt_engine=stt).record
    assert r["stt_status"] in ("OK", "EMPTY_TEXT")                           # a consumable result; NOT an accuracy claim
    assert r["stt_model_verified"] is True and r["stt_engine"] == "faster-whisper" and r["stt_model"].startswith("Systran/faster-whisper-tiny.en@")
    assert r["inference_ms"] > 0 and r["run_error"] is None
    h.validate_result(r)


def test_real_silero_and_real_stt_run_end_to_end_on_synthetic_audio(env):
    stt = _real_stt()
    from audio.silero_vad import SileroModelNotFound, SileroOnnxVadEngine
    try:
        real_vad = SileroOnnxVadEngine()
    except SileroModelNotFound:
        pytest.skip("approved Silero model not present")
    noise = (np.random.default_rng(0).standard_normal(TOTAL) * 2000).astype(np.int16)
    wav, prov, out = env
    make_wav(wav, noise)
    outcome = h.run_stage_a(wav, prov, out, vad_engine=real_vad, stt_engine=stt)
    r = outcome.record
    assert r["vad"]["artifact_verified"] is True and r["run_error"] is None          # whatever the segmenter decided, the record is complete and valid
    assert r["segment_outcome"] in {"COMPLETE", "MAX_DURATION", "STREAM_ENDED", "NO_SPEECH_STREAM_ENDED", "NO_SPEECH_TIMEOUT", "TOO_SHORT"}
    h.validate_result(r)
