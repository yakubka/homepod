"""Team Leader spoken-output foundation: the speaker-side playback contract and sounddevice backend (audio/playback.py).

DEVELOPMENT-PC SOFTWARE/API VALIDATION. Almost everything here runs against a scripted fake `sounddevice` module whose stream
calls the real callback on a thread, so the callback / finished / abort / stop code paths run for real. The single real-device
test is gated behind VESTA_PLAYBACK_API_SMOKE=1 and plays SILENCE. Nothing here proves audibility, the project speaker, an
I2S DAC, the Raspberry Pi, volume or acoustic latency.
"""

import ast
import dataclasses
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from audio import playback as pb
from audio.playback import (
    PlaybackBusy, PlaybackDeviceNotFound, PlaybackFailed, PlaybackFormatError, PlaybackInputError, PlaybackOpenFailed,
    PlaybackResult, PlaybackRuntimeUnavailable, PlaybackStatus, SoundDevicePlaybackEngine, check_playable,
)
from tests.spoken_output_fakes import FakeSd, speech_audio, tone

REPO_ROOT = Path(__file__).resolve().parent.parent


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def logs():
    handler = _LogCapture()
    pb.logger.addHandler(handler)
    yield handler.lines
    pb.logger.removeHandler(handler)


def eng(sd=None, **kw):
    sd = sd or FakeSd()
    return SoundDevicePlaybackEngine(sd_module=sd, **kw), sd


# ---- a normal playback -----------------------------------------------------------------------------------------------------------------------

def test_playback_completes_and_hands_the_exact_samples_to_the_device_at_the_native_rate():
    audio = speech_audio(n=2205, rate=22050)
    engine, sd = eng()
    r = engine.play(audio)
    stream = sd.streams[0]
    assert r.status is PlaybackStatus.COMPLETED and r.audio_duration_s == pytest.approx(0.1)
    assert (stream.samplerate, stream.channels, stream.dtype) == (22050, 1, "int16")            # native rate; no resampling; mono int16
    pcm = stream.pcm()
    assert np.array_equal(pcm[:audio.samples.size], audio.samples) and not pcm[audio.samples.size:].any()    # exact; the tail is silence padding
    assert stream.closed and stream.started


def test_other_native_rates_are_passed_through_unchanged():
    for rate in (16000, 22050, 24000, 44100):
        engine, sd = eng()
        engine.play(speech_audio(n=800, rate=rate))
        assert sd.streams[0].samplerate == rate


def test_result_carries_device_identity_latency_and_ordered_software_timestamps():
    engine, sd = eng()
    r = engine.play(speech_audio())
    assert (r.device_name, r.host_api, r.stream_latency_s, r.underflow_count) == ("Fake Speaker", "FakeAPI", 0.123, 0)
    assert r.t_play_called <= r.t_stream_ready <= r.t_first_buffer_submitted <= r.t_finished
    assert r.t_first_buffer_submitted is not None and r.t_stream_ready is not None
    assert ("check_output_settings", {"device": 1, "channels": 1, "dtype": "int16", "samplerate": 22050}) in sd.calls


def test_the_audio_object_is_never_mutated_and_stays_read_only():
    audio = speech_audio()
    before = audio.samples.copy()
    eng()[0].play(audio)
    assert np.array_equal(audio.samples, before) and not audio.samples.flags.writeable


def test_play_can_be_repeated_and_each_result_is_independent():
    engine, sd = eng()
    a, b = engine.play(speech_audio(800)), engine.play(speech_audio(1600))
    assert a.status is b.status is PlaybackStatus.COMPLETED and a.t_play_called < b.t_play_called and len(sd.streams) == 2
    assert b.audio_duration_s == pytest.approx(2 * a.audio_duration_s)


# ---- device selection ------------------------------------------------------------------------------------------------------------------------------------

def test_default_device_is_the_platform_default_and_an_explicit_device_is_honoured():
    engine, sd = eng()
    assert engine.play(speech_audio(800)).device_name == "Fake Speaker" and sd.calls[0] == ("query_devices", None, "output")
    by_name, sd2 = eng(device="other")
    assert by_name.play(speech_audio(800)).device_name == "Other Speaker" and sd2.calls[-2][1]["device"] == 2
    by_index, sd3 = eng(device=2)
    assert by_index.play(speech_audio(800)).device_name == "Other Speaker"


# ---- is_playing and stop ------------------------------------------------------------------------------------------------------------------------------------------

def test_is_playing_goes_idle_playing_completed():
    engine, sd = eng(FakeSd(pace_s=0.002))
    observed = []
    inner = sd.OutputStream

    def watching(**kw):
        stream = inner(**kw)
        real_cb = kw["callback"]
        stream.callback = lambda *a: (observed.append(engine.is_playing), real_cb(*a))[1]
        return stream

    sd.OutputStream = watching
    assert engine.is_playing is False
    r = engine.play(speech_audio(2205))
    assert r.status is PlaybackStatus.COMPLETED and observed and all(observed) and engine.is_playing is False


def test_is_playing_goes_idle_playing_stopped():
    sd = FakeSd(pace_s=0.005)
    engine, _ = eng(sd)
    results = []
    t = threading.Thread(target=lambda: results.append(engine.play(speech_audio(n=22050 * 5))))
    assert engine.is_playing is False
    t.start()
    deadline = time.time() + 5
    while not engine.is_playing and time.time() < deadline:
        time.sleep(0.001)
    assert engine.is_playing is True
    while not sd.streams or not sd.streams[0].played:
        time.sleep(0.001)
    assert engine.stop() is True
    t.join(5)
    assert not t.is_alive() and engine.is_playing is False
    r = results[0]
    assert r.status is PlaybackStatus.STOPPED and r.t_first_buffer_submitted is not None
    assert sd.streams[0].pcm().size < 22050 * 5 and sd.streams[0].abort_calls >= 1 and sd.streams[0].closed


def test_stop_is_idempotent_and_safe_when_idle_and_does_not_poison_the_next_play():
    engine, sd = eng()
    assert engine.stop() is False and engine.stop() is False
    assert engine.play(speech_audio(800)).status is PlaybackStatus.COMPLETED
    assert engine.stop() is False                                              # after completion: nothing to stop
    assert engine.play(speech_audio(800)).status is PlaybackStatus.COMPLETED


def test_stop_during_a_long_playback_twice_is_harmless():
    sd = FakeSd(pace_s=0.005)
    engine, _ = eng(sd)
    out = []
    t = threading.Thread(target=lambda: out.append(engine.play(speech_audio(n=22050 * 5))))
    t.start()
    while not sd.streams or not sd.streams[0].played:
        time.sleep(0.001)
    assert engine.stop() is True
    engine.stop()
    t.join(5)
    assert out[0].status is PlaybackStatus.STOPPED and engine.is_playing is False


def test_a_second_play_while_playing_is_refused():
    sd = FakeSd(pace_s=0.005)
    engine, _ = eng(sd)
    t = threading.Thread(target=lambda: engine.play(speech_audio(n=22050 * 3)))
    t.start()
    while not engine.is_playing:
        time.sleep(0.001)
    with pytest.raises(PlaybackBusy):
        engine.play(speech_audio(800))
    engine.stop()
    t.join(5)
    assert engine.is_playing is False


# ---- underflow and status flags ----------------------------------------------------------------------------------------------------------------------------------

def test_underflows_are_counted_and_logged_and_do_not_fail_the_playback(logs):
    engine, sd = eng(FakeSd(underflow_blocks=(0, 2), other_flag_blocks=(1,)))
    r = engine.play(speech_audio(2205))
    assert r.status is PlaybackStatus.COMPLETED and r.underflow_count == 2
    joined = "\n".join(logs)
    assert "event=playback_underflow count=2" in joined and "event=playback_status_flags count=1" in joined and "underflows=2" in joined


# ---- failures are explicit ----------------------------------------------------------------------------------------------------------------------------------------

def test_missing_or_unusable_devices_are_device_not_found():
    for device in ("no such speaker", 99, 0):                                 # unknown name, bad index, an input-only device
        engine, sd = eng(device=device)
        with pytest.raises(PlaybackDeviceNotFound):
            engine.play(speech_audio())
        assert not sd.streams
    with pytest.raises(PlaybackDeviceNotFound):                               # an ambiguous name is reported, never guessed
        eng(device="speaker")[0].play(speech_audio())


def test_an_unsupported_format_is_a_format_error_and_nothing_is_opened():
    engine, sd = eng(FakeSd(check_error=FakeSd.PortAudioError("Invalid sample rate")))
    with pytest.raises(PlaybackFormatError):
        engine.play(speech_audio())
    assert not sd.streams and engine.is_playing is False


def test_stream_open_failure_is_open_failed_and_logged(logs):
    engine, sd = eng(FakeSd(open_error=FakeSd.PortAudioError("device busy")))
    with pytest.raises(PlaybackOpenFailed):
        engine.play(speech_audio())
    assert any("event=playback_open_failed" in l for l in logs) and engine.is_playing is False


def test_stream_start_failure_is_open_failed_and_the_stream_is_closed():
    engine, sd = eng(FakeSd(start_error=FakeSd.PortAudioError("cannot start")))
    with pytest.raises(PlaybackOpenFailed):
        engine.play(speech_audio())
    assert sd.streams[0].closed and engine.is_playing is False


def test_an_error_inside_the_callback_is_a_playback_failure():
    engine, sd = eng(FakeSd(bad_outdata=True))
    with pytest.raises(PlaybackFailed) as info:
        engine.play(speech_audio())
    assert info.value.__cause__ is not None and engine.is_playing is False and sd.streams[0].closed


def test_a_device_that_never_reports_completion_is_aborted_and_reported(monkeypatch):
    monkeypatch.setattr(pb, "COMPLETION_MARGIN_S", 0.05)
    engine, sd = eng(FakeSd(never_finish=True))
    with pytest.raises(PlaybackFailed, match="did not report completion"):
        engine.play(speech_audio(800))
    assert sd.streams[0].abort_calls >= 1 and sd.streams[0].closed and engine.is_playing is False


def test_a_stream_that_ends_early_without_a_stop_request_is_a_failure_not_a_completion():
    engine, sd = eng(FakeSd(finish_early=True))
    with pytest.raises(PlaybackFailed, match="before all audio was played"):
        engine.play(speech_audio(n=22050))


def test_missing_runtime_is_runtime_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(PlaybackRuntimeUnavailable):
        SoundDevicePlaybackEngine()


# ---- input validation ----------------------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, "hello", b"\x00\x00", [1, 2, 3], {"samples": 1}, np.zeros(100, dtype=np.int16), 5])
def test_only_speech_audio_is_accepted(bad):
    engine, sd = eng()
    with pytest.raises(PlaybackInputError):
        engine.play(bad)
    assert sd.calls == [] and engine.is_playing is False


def _forged(**changes):
    audio = speech_audio()
    for name, value in changes.items():
        object.__setattr__(audio, name, value)
    return audio


@pytest.mark.parametrize("changes", [
    dict(channels=2), dict(channels=0), dict(samples=np.zeros(100, dtype=np.float32)), dict(samples=np.zeros((100, 2), dtype=np.int16)),
    dict(samples=np.zeros((2, 50), dtype=np.int16)), dict(samples=np.empty(0, dtype=np.int16)), dict(sample_rate=0), dict(sample_rate=22050.0),
    dict(sample_rate=True),
])
def test_defence_in_depth_rejects_audio_that_is_not_mono_int16(changes):
    engine, sd = eng()
    with pytest.raises(PlaybackFormatError):
        engine.play(_forged(**changes))
    assert sd.calls == []                                                       # rejected before any device was touched
    with pytest.raises(PlaybackFormatError):
        check_playable(_forged(**changes))


# ---- the result type --------------------------------------------------------------------------------------------------------------------------------------------------------

def _result(**kw):
    base = dict(status=PlaybackStatus.COMPLETED, audio_duration_s=1.0, t_play_called=10.0, t_stream_ready=10.1, t_first_buffer_submitted=10.2,
                t_finished=11.3, device_name="d", host_api="h", stream_latency_s=0.05, underflow_count=0)
    base.update(kw)
    return PlaybackResult(**base)


def test_result_is_frozen_and_has_exactly_the_agreed_fields_and_no_hardware_status():
    r = _result()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = PlaybackStatus.STOPPED
    assert [f.name for f in dataclasses.fields(PlaybackResult)] == [
        "status", "audio_duration_s", "t_play_called", "t_stream_ready", "t_first_buffer_submitted", "t_finished",
        "device_name", "host_api", "stream_latency_s", "underflow_count"]
    assert {s.name for s in PlaybackStatus} == {"COMPLETED", "STOPPED"}


@pytest.mark.parametrize("kw", [
    dict(t_stream_ready=9.0), dict(t_first_buffer_submitted=10.05), dict(t_first_buffer_submitted=12.0), dict(t_finished=10.15),
    dict(t_finished=5.0), dict(t_first_buffer_submitted=None), dict(t_stream_ready=None), dict(status="COMPLETED"),
    dict(audio_duration_s=0.0), dict(audio_duration_s=float("nan")), dict(t_play_called=float("inf")), dict(t_finished=None),
    dict(device_name=""), dict(host_api=5), dict(stream_latency_s=-0.1), dict(stream_latency_s=float("nan")),
    dict(underflow_count=-1), dict(underflow_count=1.5), dict(underflow_count=True),
])
def test_invalid_results_and_out_of_order_timestamps_are_refused(kw):
    with pytest.raises(PlaybackFailed):
        _result(**kw)


def test_a_stopped_result_may_lack_the_later_markers():
    r = _result(status=PlaybackStatus.STOPPED, t_first_buffer_submitted=None, t_finished=10.3)
    assert r.t_first_buffer_submitted is None
    assert _result(status=PlaybackStatus.STOPPED, t_stream_ready=None, t_first_buffer_submitted=None, t_finished=10.3).t_stream_ready is None


def test_stop_requested_before_the_stream_starts_gives_a_stopped_result_without_playing():
    engine, sd = eng()
    real_open = sd.OutputStream

    def opening_then_stopping(**kw):
        stream = real_open(**kw)
        engine.stop()                                 # stop() arrives while the stream is being opened
        return stream

    sd.OutputStream = opening_then_stopping
    r = engine.play(speech_audio(22050))
    assert r.status is PlaybackStatus.STOPPED and r.t_first_buffer_submitted is None and not sd.streams[0].started and sd.streams[0].closed


# ---- the timing clock is injectable and one clock is used throughout ------------------------------------------------------------------------------------

def test_every_timestamp_comes_from_the_injected_clock():
    ticks = iter(float(i) for i in range(1000, 2000))
    lock = threading.Lock()

    def clock():
        with lock:
            return next(ticks)

    engine, sd = eng(clock=clock)
    r = engine.play(speech_audio(800))
    for value in (r.t_play_called, r.t_stream_ready, r.t_first_buffer_submitted, r.t_finished):
        assert 1000.0 <= value < 2000.0 and value == int(value)


# ---- privacy and scope ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_audio_values_and_texts_never_reach_the_logs(logs):
    pcm = np.full(2205, 12345, dtype=np.int16)
    engine, sd = eng(FakeSd(underflow_blocks=(0,)))
    engine.play(speech_audio(samples=pcm))
    joined = "\n".join(logs)
    assert "event=playback_done" in joined and "12345" not in joined and "[" not in joined.split("event=playback_done")[1]
    line = next(l for l in logs if "event=playback_done" in l)
    for field in ("status=COMPLETED", "audio_duration_s=0.100", "device='Fake Speaker'", "host_api=FakeAPI", "stream_latency_s=0.123", "underflows=1"):
        assert field in line


def test_playback_module_is_output_only_and_does_not_reach_for_capture_tts_or_the_network():
    tree = ast.parse((REPO_ROOT / "audio" / "playback.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"math", "threading", "time", "abc", "dataclasses", "enum", "typing", "numpy", "config", "speech", "sounddevice"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"AudioSource", "ReSpeakerMicrophone", "WavFileSource", "InputStream", "socket", "urlopen", "SherpaOnnxTtsEngine"}
    assert "sd.play" not in (REPO_ROOT / "audio" / "playback.py").read_text(encoding="utf-8").replace("sd_module", "")


def test_importing_playback_loads_no_tts_engine_runtime():
    code = (
        "import sys\n"
        "import audio.playback\n"
        "bad = [m for m in ('sherpa_onnx', 'faster_whisper', 'ctranslate2', 'huggingface_hub') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr


# =============================================================================================================
# DEVELOPMENT-PC SOFTWARE/API VALIDATION of the real sounddevice stream. Gated. Plays SILENCE only.
# Proves: the stream opens, buffers are accepted, the timing events occur. Does NOT prove audibility, the project speaker,
# an I2S DAC, the Raspberry Pi or volume, and a laptop speaker is not project hardware.
# =============================================================================================================

@pytest.mark.skipif(os.environ.get("VESTA_PLAYBACK_API_SMOKE") != "1", reason="set VESTA_PLAYBACK_API_SMOKE=1 to open a real output stream (silence)")
def test_real_sounddevice_stream_accepts_silence_on_the_development_pc():
    silence = speech_audio(samples=np.zeros(int(22050 * 0.3), dtype=np.int16))
    try:
        engine = SoundDevicePlaybackEngine()
        r = engine.play(silence)
    except (PlaybackDeviceNotFound, PlaybackRuntimeUnavailable) as exc:
        pytest.skip(f"no usable output device: {exc}")
    assert r.status is PlaybackStatus.COMPLETED
    assert r.t_play_called <= r.t_stream_ready <= r.t_first_buffer_submitted <= r.t_finished
    assert isinstance(r.device_name, str) and r.device_name and isinstance(r.host_api, str)
    print(f"\nDEV-PC API SMOKE (silence): device={r.device_name!r} host_api={r.host_api} rate=22050 open_ms={1000*(r.t_stream_ready-r.t_play_called):.0f} "
          f"call_to_first_buffer_ms={1000*(r.t_first_buffer_submitted-r.t_play_called):.0f} total_ms={1000*(r.t_finished-r.t_play_called):.0f} "
          f"reported_latency_s={r.stream_latency_s} underflows={r.underflow_count}")
