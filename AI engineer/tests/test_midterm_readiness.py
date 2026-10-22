"""Midterm hardware + mobile-app readiness (sensor boundary, ClassroomState store, app contract, camera / voice runners, preflight).

SOFTWARE-VALIDATED WITH FIXTURES AND FAKES ONLY. No microphone, camera, serial port, ESP32, light or network is touched. These tests prove
what the new boundary code does with the inputs it is given; they prove nothing about any physical device.
"""

import ast
import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from actions.contracts import CapabilityState, CommandCapabilityDeclaration, RequestIdGenerator, ResultState
from actions.simulated_adapter import SIMULATED_LIGHTS_CAPABILITIES, FakeClock, SimulatedLightsAdapter
from assistant.answering import AnswerContext, AnswerRequest, AnswerSource, AnswerStatus, Capability, CapabilityDeclaration, DeterministicAnswerEngine
from assistant.app_interface import AppInterface, SerializedActionLoop, deny_all, env_token_authorizer
from fusion.contracts import FusionFreshnessConfig, Origin, Usability
from fusion.live_state import ClassroomStateStore, FixtureSensorSource, SensorMessageError, SensorSource, decode_sensor_message
from vision.camera import CameraError, CameraFrameSource, CameraUnavailable, open_opencv_capture
from vision.detector import Detection, DetectionResult

REPO = Path(__file__).resolve().parent.parent
FRESH = FusionFreshnessConfig(lux_max_age_s=10.0, temperature_max_age_s=10.0, humidity_max_age_s=10.0, presence_max_age_s=10.0, vision_max_age_s=10.0)
T = {"kind": "TEMPERATURE", "source_id": "temp_1", "status": "OK", "value": 23.5, "unit": "degC"}
H = {"kind": "HUMIDITY", "source_id": "hum_1", "status": "OK", "value": 40.0, "unit": "%RH"}
L = {"kind": "LUX", "source_id": "bh1750", "status": "OK", "value": 0.0, "unit": "lx"}
P = {"kind": "PRESENCE", "source_id": "ld2410b", "status": "OK", "value": False}


def dec(msg, origin=Origin.SIMULATED):
    return decode_sensor_message(msg, origin=origin, received_at_monotonic_s=1.0)


# =========================================================================================
# A. sensor message boundary
# =========================================================================================

def test_valid_messages_decode_with_origin_from_the_source_and_zero_is_a_value():
    t, h, l, p = dec(T, Origin.REAL), dec(H), dec(L), dec(P)
    assert (t.value, t.unit.value, t.sensor_id, t.envelope.origin) == (23.5, "degC", "temp_1", Origin.REAL)
    assert (h.value, l.value, p.present) == (40.0, 0.0, False)          # 0 lx and "nobody" are real readings, not missing


@pytest.mark.parametrize("change, reason", [
    ({"origin": "REAL"}, "UNKNOWN_KEY"),                       # a message cannot declare itself REAL
    ({"extra": 1}, "UNKNOWN_KEY"),
    ({"kind": "CO2"}, "UNKNOWN_KIND"),
    ({"status": "GOOD"}, "BAD_STATUS"),
    ({"status": None}, "BAD_STATUS"),
    ({"unit": None}, "BAD_UNIT"),                              # OK without a unit
    ({"unit": "degF"}, "BAD_UNIT"),                            # no conversion, ever
    ({"value": True}, "BAD_VALUE"),
    ({"value": "23.5"}, "CONTRACT_VIOLATION"),
    ({"value": None}, "CONTRACT_VIOLATION"),                   # OK needs a value
    ({"value": float("nan")}, "CONTRACT_VIOLATION"),
    ({"value": -300.0}, "CONTRACT_VIOLATION"),                 # below absolute zero
    ({"source_id": "temp 1"}, "CONTRACT_VIOLATION"),
    ({"source_id": None}, "CONTRACT_VIOLATION"),
    ({"status": "UNAVAILABLE"}, "CONTRACT_VIOLATION"),         # UNAVAILABLE must carry no value
    ({"status": "FAILED", "value": None}, "CONTRACT_VIOLATION"),   # FAILED needs an error_reason
])
def test_malformed_temperature_messages_are_rejected_with_a_reason(change, reason):
    with pytest.raises(SensorMessageError) as info:
        dec({**T, **change})
    assert info.value.reason == reason


@pytest.mark.parametrize("msg, reason", [
    ({**H, "value": 100.5}, "CONTRACT_VIOLATION"), ({**H, "unit": "%"}, "BAD_UNIT"),
    ({**L, "value": -1.0}, "CONTRACT_VIOLATION"), ({**L, "sensor_id": "x"}, "SENSOR_ID_MISMATCH"),
    ({**P, "value": 1}, "CONTRACT_VIOLATION"), ({**P, "unit": "lx"}, "UNEXPECTED_UNIT"),
    ("not a mapping", "NOT_A_MAPPING"), ([("kind", "LUX")], "NOT_A_MAPPING"),
])
def test_malformed_other_messages_are_rejected(msg, reason):
    with pytest.raises(SensorMessageError) as info:
        dec(msg)
    assert info.value.reason == reason


def test_unavailable_and_failed_messages_are_kept_as_invalid_never_as_a_number():
    r = dec({"kind": "LUX", "source_id": "bh1750", "status": "FAILED", "error_reason": "I2C_NACK"})
    assert r.value is None and r.envelope.error_reason == "I2C_NACK"
    assert dec({"kind": "TEMPERATURE", "source_id": "t", "status": "UNAVAILABLE"}).value is None
    with pytest.raises(TypeError):
        decode_sensor_message(T, origin="REAL", received_at_monotonic_s=1.0)


# =========================================================================================
# B. the one ClassroomState store
# =========================================================================================

def test_the_store_reports_fresh_then_stale_and_never_substitutes_a_value():
    clock = FakeClock(100.0)
    store = ClassroomStateStore(FRESH, clock=clock)
    assert store.ingest_messages([T, H, L, P, {**T, "unit": "K"}], Origin.SIMULATED) == (4, 1) and store.rejected == {"BAD_UNIT": 1}
    clock.advance_to(105.0)
    s = store.snapshot()
    assert [t.value for t in s.environment.temperatures] == [23.5] and s.environment.humidity.value == 40.0 and s.environment.lux.value == 0.0
    assert s.environment.lux.state.usability is Usability.FRESH and s.environment.lux.state.origin is Origin.SIMULATED
    clock.advance_to(111.0)
    s = store.snapshot()
    assert s.environment.lux.value is None and s.environment.lux.state.usability is Usability.STALE
    assert s.environment.temperatures[0].value is None and s.environment.humidity.value is None


def test_default_freshness_is_unconfigured_so_nothing_is_usable_and_an_empty_store_is_missing():
    store = ClassroomStateStore(clock=FakeClock(5.0))
    assert store.snapshot().environment.lux.state.usability is Usability.MISSING
    store.ingest_messages([L], Origin.SIMULATED)
    s = store.snapshot()
    assert s.environment.lux.value is None and s.environment.lux.state.usability is Usability.UNCONFIGURED
    assert s.occupancy.vision_count is None and s.occupancy.sensor_present is None


def test_latest_reading_wins_and_two_temperature_sensors_stay_separate():
    clock = FakeClock(0.0)
    store = ClassroomStateStore(FRESH, clock=clock)
    store.ingest_messages([T, {**T, "source_id": "temp_2", "value": 21.0}, {**T, "value": 24.0}], Origin.SIMULATED)
    assert [(t.sensor_id, t.value) for t in store.snapshot().environment.temperatures] == [("temp_1", 24.0), ("temp_2", 21.0)]
    with pytest.raises(TypeError):
        store.ingest({"kind": "LUX"})


def test_fixture_source_is_always_simulated_and_a_real_source_stays_real_in_the_answer():
    src = FixtureSensorSource([T])
    assert src.origin is Origin.SIMULATED and isinstance(src, SensorSource)

    class HardwareStandIn(SensorSource):               # what the hardware team's adapter would be: it states REAL, the message cannot
        origin = Origin.REAL

        def poll(self):
            return [T]

    clock = FakeClock(0.0)
    store = ClassroomStateStore(FRESH, clock=clock)
    store.pump(HardwareStandIn())
    caps = CapabilityDeclaration(frozenset({Capability.TEMPERATURE}))
    r = DeterministicAnswerEngine().answer(AnswerRequest("what is the temperature in the room", AnswerContext(store.snapshot(), caps)))
    assert r.status is AnswerStatus.ANSWERED and r.source is AnswerSource.REAL and "23.5" in r.text
    store.pump(src)                                     # a newer SIMULATED reading replaces it and is labelled SIMULATED
    r = DeterministicAnswerEngine().answer(AnswerRequest("what is the temperature in the room", AnswerContext(store.snapshot(), caps)))
    assert r.source is AnswerSource.SIMULATED


def test_snapshot_reads_then_samples_the_clock_so_a_reading_is_never_from_the_future():
    order = []
    clock = FakeClock(0.0)
    store = ClassroomStateStore(FRESH, clock=lambda: (order.append("clock"), clock())[1])
    store.ingest_messages([L], Origin.SIMULATED)
    order.clear()
    store.snapshot()
    assert order == ["clock"]


# =========================================================================================
# C. mobile-app contract + a fake mobile client
# =========================================================================================

def app(*, plan=None, caps=SIMULATED_LIGHTS_CAPABILITIES, authorize=lambda t: "phone-a" if t == "good-token" else None, loop=True, connected=True):
    clock = FakeClock(100.0)
    store = ClassroomStateStore(FRESH, clock=clock)
    store.ingest_messages([T, H, L], Origin.SIMULATED)
    adapter = SimulatedLightsAdapter(clock)
    if not connected:
        adapter.lose_link(0.0)
        adapter.next_event(100.0)
    lp = SerializedActionLoop(adapter, caps, clock=clock, timeout_s=2.0) if loop else None
    if plan is not None:
        adapter.plan(replies=plan)
    return AppInterface(store, action_loop=lp, authorize=authorize, request_ids=RequestIdGenerator(prefix="app", token_source=lambda n: "a" * 12)), adapter, lp


class FakeMobileClient:
    """What the app developer's client must be able to rely on: JSON in, JSON out, nothing else."""

    REQUIRED = {"api_version", "api_status", "evaluated_at_monotonic_s", "aggregate_origin", "temperatures", "humidity", "lux",
                "detected_person_count", "presence", "light", "assistant_state"}

    def __init__(self, api):
        self.api = api

    def status(self):
        doc = json.loads(json.dumps(self.api.read_status()))
        assert self.REQUIRED <= set(doc)
        for v in (*doc["temperatures"], doc["humidity"], doc["lux"], doc["detected_person_count"], doc["presence"]):
            assert {"value", "usability", "origin", "age_s", "reason"} <= set(v)
            assert v["value"] is None or v["usability"] == "FRESH"         # a value is shown ONLY when it is fresh
        return doc

    def light(self, token, action, cid):
        return json.loads(json.dumps(self.api.request_light(client_token=token, action=action, correlation_id=cid)))


def test_the_app_reads_the_same_state_with_origin_labels_and_missing_stays_missing():
    api, _, _ = app()
    doc = FakeMobileClient(api).status()
    assert doc["temperatures"][0]["value"] == 23.5 and doc["temperatures"][0]["origin"] == "SIMULATED" and doc["aggregate_origin"] == "SIMULATED"
    assert doc["detected_person_count"]["value"] is None and doc["detected_person_count"]["usability"] == "MISSING"
    assert "undercounts" in doc["detected_person_count"]["note"] and doc["light"]["adapter_origin"] == "SIMULATED"
    assert "transcript" not in json.dumps(doc).lower()


def test_an_authorized_request_confirms_only_a_correlated_simulated_application():
    api, ad, _ = app(plan=[(0.1, "ACCEPTED"), (0.2, "APPLIED_CONFIRMED")])
    r = FakeMobileClient(api).light("good-token", "TURN_ON", "phone-1")
    assert (r["status"], r["result_state"], r["confirmed"], r["origin"], r["validation_label"]) == ("RESULT", "APPLIED_CONFIRMED", True, "SIMULATED", "SIMULATED")
    assert r["correlation_id"] == "phone-1" and r["request_id"] == "app-aaaaaaaaaaaa-1" and ad.submitted_ids == ["app-aaaaaaaaaaaa-1"]
    assert "simulation" in r["message"].lower()
    assert FakeMobileClient(api).status()["light"]["last_result"]["result_state"] == "APPLIED_CONFIRMED"


@pytest.mark.parametrize("plan, state", [([(0.1, "ACCEPTED")], "TIMEOUT_UNKNOWN"), ([], "TIMEOUT_UNKNOWN"), ([(0.1, "FAILED")], "FAILED"),
                                         ([(0.1, "APPLIED_CONFIRMED", {"request_id": "c-bbbbbbbbbbbb-9"})], "TIMEOUT_UNKNOWN")])
def test_no_trusted_acknowledgement_means_no_confirmation(plan, state):
    api, _, _ = app(plan=plan)
    r = api.request_light(client_token="good-token", action="TURN_OFF", correlation_id="c1")
    assert r["result_state"] == state and r["confirmed"] is False


def test_unauthorized_invalid_unavailable_and_busy_requests_submit_nothing():
    api, ad, lp = app(authorize=deny_all)
    assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="c1")["status"] == "UNAUTHORIZED"
    api, ad, lp = app()
    assert api.request_light(client_token="good-token", action="SET_BRIGHTNESS", correlation_id="c1")["reason"] == "UNSUPPORTED_ACTION"
    assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="bad id!")["reason"] == "BAD_CORRELATION_ID"
    lp._busy.acquire()
    try:
        assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="c2")["status"] == "BUSY"
    finally:
        lp._busy.release()
    assert ad.submitted_ids == []
    api, _, _ = app(loop=False)
    assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="c3")["status"] == "NOT_AVAILABLE"
    assert api.read_status()["light"]["capability"] == "NOT_CONFIGURED"


def test_unknown_capability_or_lost_link_is_never_submitted():
    api, ad, _ = app(caps=CommandCapabilityDeclaration())
    r = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="c1")
    assert (r["capability"], r["submitted"], r["confirmed"]) == ("UNKNOWN_CAPABILITY", False, False) and ad.submitted_ids == []
    api, ad, _ = app(connected=False)
    r = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="c1")
    assert r["capability"] == "SUPPORTED_BUT_UNAVAILABLE" and not r["submitted"] and api.read_status()["light"]["adapter"] == "DISCONNECTED"


def test_env_token_authorizer_denies_when_unset_and_compares_a_hash(monkeypatch):
    import hashlib
    auth = env_token_authorizer("VESTA_TEST_TOKEN_SHA256")
    monkeypatch.delenv("VESTA_TEST_TOKEN_SHA256", raising=False)
    assert auth("anything") is None
    monkeypatch.setenv("VESTA_TEST_TOKEN_SHA256", hashlib.sha256(b"s3cret").hexdigest())
    assert auth("s3cret") == "app-client" and auth("wrong") is None and auth("") is None


def test_voice_and_app_share_one_serialized_loop_and_one_validation_path():
    from assistant.runtime import VoiceAssistantRuntime
    from scripts.ai2_stage_b_live import build_live_segmenter_config
    from scripts.vesta_runtime_demo import DemoPlayback, DemoTts
    from audio.utterance import UtteranceSegmenter
    from tests.stage_b_support import EnergyVad16, FakeStt16, ScriptedWake16, make_blocks

    api, ad, lp = app(plan=[(0.1, "APPLIED_CONFIRMED")])
    assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="c1")["confirmed"] is True
    vad = EnergyVad16()
    rt = VoiceAssistantRuntime(detector=ScriptedWake16(fire=(15,)), segmenter=UtteranceSegmenter(build_live_segmenter_config(vad), vad),
                               stt=FakeStt16("hey jarvis turn off the lights"), tts=DemoTts(), playback=DemoPlayback(), action_loop=lp,
                               request_ids=RequestIdGenerator(token_source=lambda n: "c" * 12))
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    x = np.zeros(64000, np.int16)
    x[16000:40000] = (2000 + np.arange(16000, 40000) % 1000).astype(np.int16)
    outcomes = [o for b in make_blocks(x, block=1024) for o in rt.feed(b.samples)] + rt.finish()
    acted = [o for o in outcomes if o.action is not None]
    assert len(acted) == 1 and acted[0].action.result.state is ResultState.APPLIED_CONFIRMED
    assert ad.submitted_ids == ["app-aaaaaaaaaaaa-1", "c-cccccccccccc-1"]          # one loop, two distinct id spaces


def test_the_app_interface_opens_nothing_and_names_no_transport():
    src = (REPO / "assistant" / "app_interface.py").read_text(encoding="utf-8")
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module} | \
           {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    assert not mods & {"socket", "http", "urllib", "flask", "fastapi", "websockets", "serial", "requests", "paho"}


# =========================================================================================
# D. camera source + live camera runner
# =========================================================================================

class FakeCapture:
    def __init__(self, frames, block=None):
        self.frames, self.block, self.released = list(frames), block, 0

    def read(self):
        if self.block is not None:
            self.block.wait(5)
        return (True, self.frames.pop(0)) if self.frames else (False, None)

    def release(self):
        self.released += 1


def bgr(h=48, w=64):
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 0] = 255                                   # pure BLUE in BGR
    return img


def test_camera_source_converts_bgr_to_rgb_counts_frames_and_releases():
    cap = FakeCapture([bgr(), bgr()])
    src = CameraFrameSource(lambda: cap, label="fake", origin=Origin.SIMULATED)
    with pytest.raises(CameraError):
        src.read_frame()
    src.start()
    f0, f1 = src.read_frame(), src.read_frame()
    assert (f0.index, f1.index, f0.width, f0.height) == (0, 1, 64, 48) and tuple(f0.image[0, 0]) == (0, 0, 255)
    with pytest.raises(CameraError):
        src.read_frame()                                # no more frames: an error, never an empty frame
    src.stop()
    src.stop()
    assert cap.released == 1


def test_a_camera_that_never_delivers_times_out():
    gate = threading.Event()
    src = CameraFrameSource(lambda: FakeCapture([bgr()], block=gate), label="slow", origin=Origin.SIMULATED, read_timeout_s=0.05)
    src.start()
    with pytest.raises(CameraError):
        src.read_frame()
    gate.set()
    src.stop()


def test_missing_opencv_is_a_typed_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    with pytest.raises(CameraUnavailable):
        open_opencv_capture(0)


class FakeDetector:
    def __init__(self, counts):
        self.counts = list(counts)

    def detect(self, frame):
        n = self.counts.pop(0)
        dets = [Detection(1.0 + i, 1.0, 5.0 + i, 20.0, 0.9, frame.width, frame.height) for i in range(n)]
        return DetectionResult(dets, frame.index, frame.source, frame.width, frame.height, 1.0, 1.0, 1.0, 3.0, "fake", n)


def test_camera_run_reports_size_count_fps_and_propagates_the_count_with_provenance():
    from scripts.vesta_live_camera import run
    clock = FakeClock(0.0)
    store = ClassroomStateStore(FRESH, clock=clock)
    cap = FakeCapture([bgr(), bgr(), bgr()])
    out = io.StringIO()
    summary = run(CameraFrameSource(lambda: cap, label="fake", origin=Origin.SIMULATED), FakeDetector([2, 0, 3]), max_frames=3,
                  duration_s=None, out=out, store=store)
    assert (summary["frames"], summary["frame_size"], summary["min_count"], summary["max_count"]) == (3, (64, 48), 0, 3)
    assert "size=64x48 detected_persons=2" in out.getvalue() and cap.released == 1 and summary["origin"] == "SIMULATED"
    s = store.snapshot()
    assert s.occupancy.raw_vision_count == 3 and s.vision_state.origin is Origin.SIMULATED


def test_camera_run_releases_the_camera_when_detection_fails():
    from scripts.vesta_live_camera import run
    cap = FakeCapture([bgr()])

    class Broken:
        def detect(self, frame):
            raise RuntimeError("inference failed")

    with pytest.raises(RuntimeError):
        run(CameraFrameSource(lambda: cap, label="fake", origin=Origin.SIMULATED), Broken(), max_frames=1, duration_s=None, out=io.StringIO())
    assert cap.released == 1


def test_camera_cli_list_args_and_injected_run():
    from scripts.vesta_live_camera import main
    out, err = io.StringIO(), io.StringIO()
    assert main(["--list"], out=out) == 0 and "NOT HARDWARE VALIDATION" in out.getvalue()
    assert main([], out=out, err=err) == 2
    out = io.StringIO()
    code = main(["--camera", "0", "--max-frames", "2", "--no-display"], out=out, err=err,
                capture_factory=lambda: FakeCapture([bgr(), bgr()]), detector_factory=lambda: FakeDetector([1, 1]))
    assert code == 0 and "origin=SIMULATED" in out.getvalue() and "UNREVIEWED" in out.getvalue()
    cap = FakeCapture([])
    code = main(["--camera", "0", "--max-frames", "1", "--no-display"], out=io.StringIO(), err=(err := io.StringIO()),
                capture_factory=lambda: cap, detector_factory=lambda: FakeDetector([0]))
    assert code == 3 and "camera failure" in err.getvalue() and cap.released == 1


# =========================================================================================
# E. live voice runner (fake engines and a fake block source injected explicitly)
# =========================================================================================

def _voice(tmp_path, *extra, reply="hey jarvis what is the temperature in the room"):
    from scripts.ai2_stage_b_live import Engines
    from scripts.vesta_live_voice import main
    from scripts.vesta_runtime_demo import DemoPlayback, DemoTts
    from tests.stage_b_support import EnergyVad16, FakeBlockSource, FakeStt16, ScriptedWake16, make_blocks
    x = np.zeros(64000, np.int16)
    x[16000:40000] = (2000 + np.arange(16000, 40000) % 1000).astype(np.int16)
    playback = DemoPlayback()
    out, err = io.StringIO(), io.StringIO()
    code = main(["--out-dir", str(tmp_path / "ev"), "--output-device", "3", *extra], out=out, err=err,
                engines_factory=lambda a: Engines(ScriptedWake16(fire=(15,)), EnergyVad16(), FakeStt16(reply)),
                mic_factory=lambda hint: FakeBlockSource(make_blocks(x, block=1024), sample_rate=16000),
                tts_factory=DemoTts, playback_factory=lambda device: playback)
    return code, out.getvalue(), err.getvalue(), playback


def test_live_voice_runner_answers_truthfully_and_speaks_through_the_one_arbiter(tmp_path):
    code, out, err, playback = _voice(tmp_path, "--declare", "TEMPERATURE", "--speaker-test")
    assert code == 0, err
    assert "speaker_test arbitration=GRANTED playback=COMPLETED" in out
    assert "outcome=ANSWER_OUTPUT intent=SENSOR_QUERY answer=INFORMATION_UNAVAILABLE arbitration=GRANTED playback=COMPLETED" in out
    assert playback.calls == 2 and "what is the temperature" not in out and "not available" not in out    # no transcript / answer text by default
    (rec,) = list((tmp_path / "ev").glob("*.stage_b.json"))
    assert "temperature" not in rec.read_text(encoding="utf-8") and "LIVE-MICROPHONE RUN - UNREVIEWED" in rec.read_text(encoding="utf-8")


def test_live_voice_runner_prints_text_only_when_asked_and_never_executes_a_command(tmp_path):
    code, out, err, _ = _voice(tmp_path, "--show-text", reply="hey jarvis turn on the lights")
    assert code == 0 and "transcript: turn on the lights" in out and "outcome=COMMAND_NOT_EXECUTED" in out
    assert "can't carry out device commands yet" in out


def test_live_voice_runner_argument_and_failure_paths(tmp_path):
    from scripts.vesta_live_voice import _real_mic, build, main
    assert main([], out=io.StringIO(), err=(e := io.StringIO())) == 2 and "--out-dir" in e.getvalue()
    inside = REPO / "tests" / "_live_voice_inside_repo"
    assert main(["--out-dir", str(inside)], out=io.StringIO(), err=io.StringIO()) == 2 and not inside.exists()
    assert main(["--out-dir", str(tmp_path), "--declare", "TELEPATHY"], out=io.StringIO(), err=io.StringIO()) == 2
    from scripts.ai2_stage_b_live import StageBModelError

    def bad_engines(a):
        raise StageBModelError("stt", "SnapshotNotVerified", "x")

    assert main(["--out-dir", str(tmp_path / "ev")], out=io.StringIO(), err=(e := io.StringIO()), engines_factory=bad_engines) == 3
    assert "model failure" in e.getvalue()
    assert build.__kwdefaults__["mic_factory"] is _real_mic                 # no fake-microphone fallback by default


# =========================================================================================
# F. preflight
# =========================================================================================

def test_preflight_readiness_never_claims_the_hardware_team_paths():
    from scripts.vesta_preflight import readiness
    pkgs = dict.fromkeys(("sounddevice", "sherpa_onnx", "cv2"), True)
    audio = {"inputs": [{"name": "reSpeaker XVF3800"}], "outputs": [{"name": "x"}]}
    mdl = {"models/silero_vad_v6.2.3.onnx": "OK", "faster-whisper tiny.en": "OK", "openwakeword hey_jarvis": "PRESENT", "lessac voice": "OK",
           "models/yolox_tiny.onnx": "OK"}
    r = readiness(pkgs, audio, ["/dev/video0"], ["/dev/ttyACM0"], mdl)
    assert r["microphone_voice_input"] == r["spoken_reply_output"] == r["camera_person_detection"] == "READY_TO_TEST"
    for k in ("esp32_link", "temperature_humidity_lux", "light_on_off", "mobile_app"):
        assert r[k].startswith("NOT_AVAILABLE")
    r = readiness({**pkgs, "cv2": False}, {"inputs": [], "outputs": []}, [], [], {**mdl, "lessac voice": "FAIL X"})
    assert r["microphone_voice_input"].startswith("MISSING") and "cv2" in r["camera_person_detection"] and "voice" in r["spoken_reply_output"]


def test_preflight_json_output_and_no_device_opening_calls():
    from scripts.vesta_preflight import main
    out = io.StringIO()
    assert main(["--json"], out=out, collect_fn=lambda: {"label": "DEVICE DISCOVERY IS NOT HARDWARE VALIDATION"}) == 0
    assert json.loads(out.getvalue())["label"].startswith("DEVICE DISCOVERY")
    tree = ast.parse((REPO / "scripts" / "vesta_preflight.py").read_text(encoding="utf-8"))
    called = {(n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"InputStream", "OutputStream", "VideoCapture", "Serial", "rec", "play", "playrec", "urlopen", "download_models", "submit"}


# =========================================================================================
# G. import-time safety
# =========================================================================================

def test_new_modules_open_nothing_and_import_no_camera_or_serial_library():
    code = ("import sys\n"
            "import fusion.live_state, vision.camera, assistant.app_interface, scripts.vesta_live_camera, scripts.vesta_preflight, scripts.vesta_midterm_demo\n"
            "bad = [m for m in ('cv2', 'serial', 'picamera2', 'socket_server') if m in sys.modules]\n"
            "print(bad)")
    done = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
    assert done.stdout.strip().splitlines()[-1] == "[]"


@pytest.mark.parametrize("cmd", ["sensors", "app"])
def test_the_fixture_demos_run_and_say_simulated(cmd, capsys):
    from scripts.vesta_midterm_demo import main
    assert main([cmd]) == 0
    text = capsys.readouterr().out
    assert "SIMULATED" in text and "REAL" not in text.replace("NOT HARDWARE-VALIDATED", "")


def test_the_replay_demo_decodes_a_captured_file_with_origin_unknown(tmp_path, capsys):
    from scripts.vesta_midterm_demo import main
    f = tmp_path / "capture.jsonl"
    f.write_text(json.dumps(T) + "\n" + json.dumps({**T, "unit": "F"}) + "\nnot json\n", encoding="utf-8")
    assert main(["replay", str(f)]) == 0
    text = capsys.readouterr().out
    assert "line 1: ACCEPTED TemperatureReading" in text and "line 2: REJECTED BAD_UNIT" in text and "line 3: REJECTED NOT_JSON" in text


# =========================================================================================
# H. compatibility gate: Pi preflight detail, app result lookup / replay / state
# =========================================================================================

def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_preflight_host_reads_board_memory_and_load_from_proc(tmp_path):
    from scripts.vesta_preflight import host
    _write(tmp_path / "proc/device-tree/model", "Raspberry Pi 5 Model B Rev 1.0\x00")
    _write(tmp_path / "proc/meminfo", "MemTotal:        8245732 kB\nMemFree: 1 kB\nMemAvailable:    7000000 kB\n")
    _write(tmp_path / "proc/loadavg", "0.42 0.30 0.10 1/200 999\n")
    h = host(tmp_path)
    assert (h["board_model"], h["mem_total_mb"], h["mem_available_mb"], h["loadavg_1m"]) == ("Raspberry Pi 5 Model B Rev 1.0", 8052, 6835, 0.42)
    assert host(tmp_path / "missing")["board_model"] is None


def test_preflight_tells_a_usb_camera_from_pi_isp_and_csi_nodes(tmp_path):
    from scripts.vesta_preflight import camera_nodes_detail, readiness
    v = tmp_path / "sys/class/video4linux"
    for node, name, driver in (("video0", "USB2.0 HD UVC WebCam", "uvcvideo"), ("video19", "pispbe-input", "pispbe"),
                               ("video20", "rp1-cfe-csi2_ch0", "rp1-cfe")):
        _write(v / node / "name", name + "\n")
        _write(v / node / "device" / "uevent", f"OF_NAME=x\nDRIVER={driver}\n")
    detail = camera_nodes_detail(tmp_path)
    assert [c["kind"] for c in detail] == ["USB_UVC", "OTHER_NOT_A_USB_CAMERA", "PI_CSI_CAMERA_RECEIVER"]
    pkgs = dict.fromkeys(("sounddevice", "sherpa_onnx", "cv2"), True)
    mdl = {"models/silero_vad_v6.2.3.onnx": "OK", "faster-whisper tiny.en": "OK", "openwakeword hey_jarvis": "PRESENT", "lessac voice": "OK",
           "models/yolox_tiny.onnx": "OK"}
    audio = {"inputs": [{"name": "ReSpeaker", "supports_16k_mono_int16": False}], "outputs": [{"name": "x"}]}
    r = readiness(pkgs, audio, ["/dev/video19"], [], mdl, detail[1:])          # ISP nodes only: NOT a usable camera
    assert "USB/UVC" in r["camera_person_detection"] and r["csi_camera"].startswith("NOT_SUPPORTED_BY_RUNNER")
    assert "16 kHz" in r["microphone_voice_input"]                             # a ReSpeaker that refuses 16 kHz mono is not ready
    assert readiness(pkgs, audio, [], [], mdl, detail[:1])["camera_person_detection"] == "READY_TO_TEST"


def test_preflight_names_the_usb_serial_bridge_without_claiming_firmware(tmp_path):
    from scripts.vesta_preflight import serial_detail
    _write(tmp_path / "sys/class/tty/ttyACM0/idVendor", "303a\n")
    _write(tmp_path / "sys/class/tty/ttyACM0/idProduct", "1001\n")
    (tmp_path / "sys/class/tty/ttyACM0/device").mkdir()
    _write(tmp_path / "sys/class/tty/ttyUSB0/idVendor", "abcd\n")
    (tmp_path / "sys/class/tty/ttyUSB0/device").mkdir()
    d = serial_detail(tmp_path)
    assert d[0] == {"port": "/dev/ttyACM0", "vid": "303a", "pid": "1001", "vendor": "Espressif native USB (ESP32-S3 USB-Serial/JTAG or CDC)"}
    assert d[1]["vendor"] == "unknown" and d[1]["pid"] is None


def test_preflight_format_query_and_requirement_versions(tmp_path):
    from scripts.vesta_preflight import _format_ok, requirement_versions
    seen = []
    assert _format_ok(lambda **kw: seen.append(kw), 3, 16000) is True
    assert seen == [{"device": 3, "samplerate": 16000, "channels": 1, "dtype": "int16"}]

    def refuse(**kw):
        raise ValueError("Invalid sample rate")

    assert _format_ok(refuse, 3, 16000) is False
    req = tmp_path / "requirements.txt"
    req.write_text("# c\nnumpy\npytest==0.0.1\nsurely-not-installed-pkg==1.0  # x\n", encoding="utf-8")
    v = requirement_versions(req)
    assert v["numpy"]["pinned"] is None and v["numpy"]["installed"] != "MISSING"
    assert v["surely-not-installed-pkg"] == {"pinned": "1.0", "installed": "MISSING"}


def test_a_retried_correlation_id_is_never_executed_twice_and_results_can_be_looked_up():
    api, ad, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")])
    first = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="phone-7")
    again = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="phone-7")
    assert first["confirmed"] and again["replayed"] is True and again["request_id"] == first["request_id"] and len(ad.submitted_ids) == 1
    other = api.request_light(client_token="good-token", action="TURN_OFF", correlation_id="phone-7")
    assert (other["status"], other["reason"]) == ("INVALID_REQUEST", "CORRELATION_ID_REUSED") and len(ad.submitted_ids) == 1
    assert api.get_command_result(client_token="good-token", correlation_id="phone-7")["result_state"] == "APPLIED_CONFIRMED"
    assert api.get_command_result(client_token="bad", correlation_id="phone-7")["status"] == "UNAUTHORIZED"
    assert api.get_command_result(client_token="good-token", correlation_id="never")["status"] == "NOT_FOUND"
    assert api.request_light(client_token="bad", action="TURN_ON", correlation_id="phone-7")["status"] == "UNAUTHORIZED"   # no replay leak


def test_busy_and_unavailable_answers_are_not_stored_so_a_retry_can_run():
    api, ad, lp = app(plan=[(0.1, "APPLIED_CONFIRMED")])
    lp._busy.acquire()
    try:
        assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="r1")["status"] == "BUSY"
    finally:
        lp._busy.release()
    assert api.get_command_result(client_token="good-token", correlation_id="r1")["status"] == "NOT_FOUND"
    assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="r1")["confirmed"] is True
    api._write.acquire()
    try:
        assert api.request_light(client_token="good-token", action="TURN_OFF", correlation_id="r2")["status"] == "BUSY"
    finally:
        api._write.release()


def test_result_history_is_bounded():
    from assistant.app_interface import RESULT_HISTORY
    api, _, _ = app()                                             # submitted, no reply: TIMEOUT_UNKNOWN in simulated time (fast)
    for i in range(RESULT_HISTORY + 5):
        api.request_light(client_token="good-token", action="TURN_ON", correlation_id=f"c{i}")
    assert api.get_command_result(client_token="good-token", correlation_id="c0")["status"] == "EXPIRED"      # aged out, still remembered
    assert api.get_command_result(client_token="good-token", correlation_id=f"c{RESULT_HISTORY + 4}")["status"] == "RESULT"


def test_assistant_state_operation_reports_the_shared_state_manager():
    from assistant.state import AssistantState, AssistantStateManager, StateReason
    mgr = AssistantStateManager()
    api = AppInterface(ClassroomStateStore(clock=FakeClock(0.0)), assistant_state=mgr)
    assert api.assistant_state()["state"] == "IDLE"
    mgr.transition(AssistantState.LISTENING, StateReason.SPEECH_INPUT_ACTIVATED)
    assert api.assistant_state() == {"state": "LISTENING", "color": mgr.current_color.value} and api.read_status()["assistant_state"] == "LISTENING"
    assert AppInterface(ClassroomStateStore()).assistant_state() == {"state": "NOT_CONFIGURED", "color": None}


# =========================================================================================
# I. code-review remediation: one regression test (or more) per confirmed finding
# =========================================================================================

# --- F1: the wake-word preflight requires the real model files, never just the package ---

def _oww_dir(tmp_path, files):
    d = tmp_path / "oww"
    (d / "resources" / "models").mkdir(parents=True)
    for name, data in files.items():
        (d / "resources" / "models" / name).write_bytes(data)
    return [str(d)]


@pytest.mark.parametrize("files, missing", [
    ({}, ["hey_jarvis*.onnx", "melspectrogram.onnx", "embedding_model.onnx"]),                          # empty models directory
    ({"hey_jarvis_v0.1.onnx": b"", "melspectrogram.onnx": b"x", "embedding_model.onnx": b"x"}, ["hey_jarvis*.onnx"]),   # 0-byte file
    ({"hey_mycroft_v0.1.onnx": b"x", "melspectrogram.onnx": b"x", "embedding_model.onnx": b"x"}, ["hey_jarvis*.onnx"]),  # wrong model
    ({"hey_jarvis_v0.1.tflite": b"x", "melspectrogram.onnx": b"x", "embedding_model.onnx": b"x"}, ["hey_jarvis*.onnx"]), # wrong format
    ({"hey_jarvis_v0.1.onnx": b"x", "melspectrogram.onnx": b"x"}, ["embedding_model.onnx"]),            # feature model missing
    ({"hey_jarvis_v0.1.onnx": b"x", "melspectrogram.onnx": b"x", "embedding_model.onnx": b"x"}, []),
])
def test_f1_wake_model_check_needs_every_real_file(tmp_path, files, missing):
    from scripts.vesta_preflight import oww_missing_files
    assert oww_missing_files(_oww_dir(tmp_path, files)) == missing


def test_f1_no_package_or_no_models_dir_is_missing_and_never_ready(tmp_path):
    from scripts.vesta_preflight import oww_missing_files, readiness
    assert oww_missing_files([]) == ["openwakeword package"]
    assert oww_missing_files([str(tmp_path / "no_such_dir")]) == ["hey_jarvis*.onnx", "melspectrogram.onnx", "embedding_model.onnx"]
    mdl = {"models/silero_vad_v6.2.3.onnx": "OK", "faster-whisper tiny.en": "OK", "lessac voice": "OK", "models/yolox_tiny.onnx": "OK",
           "openwakeword hey_jarvis": "MISSING: hey_jarvis*.onnx"}
    audio = {"inputs": [{"name": "ReSpeaker", "supports_16k_mono_int16": True}], "outputs": [{"name": "x"}]}
    pkgs = dict.fromkeys(("sounddevice", "sherpa_onnx", "cv2"), True)
    assert readiness(pkgs, audio, [], [], mdl)["microphone_voice_input"].startswith("MISSING")


# --- F2: a stalled camera never gets release() under a running read, and cannot keep the process alive ---

class _StalledCapture:
    def __init__(self, frames=1):
        self.gate, self.reading, self.overlap, self.released = threading.Event(), 0, False, 0
        self.frames = frames

    def read(self):
        self.reading += 1
        try:
            self.gate.wait(10)
            return True, bgr()
        finally:
            self.reading -= 1

    def release(self):
        self.overlap = self.overlap or self.reading > 0
        self.released += 1


def test_f2_stop_during_a_stuck_read_defers_release_until_the_read_returns():
    cap = _StalledCapture()
    src = CameraFrameSource(lambda: cap, label="stall", origin=Origin.SIMULATED, read_timeout_s=0.05)
    src.start()
    before = set(threading.enumerate())                        # only the reader THIS test starts, never another test's leftovers
    with pytest.raises(CameraError, match="no frame within"):
        src.read_frame()
    reader = [t for t in set(threading.enumerate()) - before if t.name == "camera-read"]
    assert len(reader) == 1
    assert reader and all(t.daemon for t in reader)            # a daemon thread: it cannot block interpreter exit
    src.stop()
    assert cap.released == 0 and src.release_deferred          # NOT released while read() is still running
    cap.gate.set()
    for t in reader:
        t.join(5)
    assert cap.released == 1 and not cap.overlap and not src.release_deferred


def test_f2_a_stalled_source_refuses_further_reads_and_restarts_cleanly():
    caps = [_StalledCapture(), FakeCapture([bgr()])]
    src = CameraFrameSource(lambda: caps.pop(0), label="s", origin=Origin.SIMULATED, read_timeout_s=0.05)
    src.start()
    with pytest.raises(CameraError):
        src.read_frame()
    with pytest.raises(CameraError, match="stalled"):
        src.read_frame()
    src.stop()
    src.start()                                                 # a new capture after a restart works normally
    assert src.read_frame().index == 0
    src.stop()


def test_f2_a_read_that_raises_is_a_camera_error_and_release_is_immediate():
    class Broken:
        released = 0

        def read(self):
            raise OSError("device gone")

        def release(self):
            Broken.released += 1

    src = CameraFrameSource(lambda: Broken(), label="b", origin=Origin.SIMULATED, read_timeout_s=1.0)
    src.start()
    with pytest.raises(CameraError, match="OSError"):
        src.read_frame()
    src.stop()
    assert Broken.released == 1


def test_f2_a_process_with_a_permanently_stuck_camera_still_exits():
    code = ("import sys, threading\nsys.path.insert(0, %r)\n"
            "from vision.camera import CameraFrameSource, CameraError\nfrom fusion.contracts import Origin\n"
            "class Stuck:\n    def read(self):\n        threading.Event().wait()\n    def release(self):\n        pass\n"
            "s = CameraFrameSource(lambda: Stuck(), label='x', origin=Origin.SIMULATED, read_timeout_s=0.05)\ns.start()\n"
            "try:\n    s.read_frame()\nexcept CameraError:\n    print('timeout reported')\ns.stop()\n") % str(REPO)
    done = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True, timeout=20)
    assert done.returncode == 0 and "timeout reported" in done.stdout


# --- F4: trusted caller identity, isolation, conflicting payloads ---

TWO = {"token-a": "phone-a", "token-b": "phone-b"}


def test_f4_two_callers_with_the_same_correlation_id_are_isolated():
    api, ad, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")], authorize=TWO.get)
    a = api.request_light(client_token="token-a", action="TURN_ON", correlation_id="req-1")
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    b = api.request_light(client_token="token-b", action="TURN_ON", correlation_id="req-1")
    assert a["confirmed"] and b["confirmed"] and "replayed" not in b and a["request_id"] != b["request_id"]
    assert len(ad.submitted_ids) == 2                           # B's own request really ran
    assert api.get_command_result(client_token="token-b", correlation_id="req-1")["request_id"] == b["request_id"]
    assert api.get_command_result(client_token="token-a", correlation_id="req-1")["request_id"] == a["request_id"]


def test_f4_a_caller_cannot_read_another_callers_result():
    api, _, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")], authorize=TWO.get)
    api.request_light(client_token="token-a", action="TURN_ON", correlation_id="only-a")
    assert api.get_command_result(client_token="token-b", correlation_id="only-a")["status"] == "NOT_FOUND"


def test_f4_conflicting_payload_for_the_same_callers_id_is_refused():
    api, ad, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")], authorize=TWO.get)
    api.request_light(client_token="token-a", action="TURN_ON", correlation_id="x1")
    r = api.request_light(client_token="token-a", action="TURN_OFF", correlation_id="x1")
    assert (r["status"], r["reason"]) == ("INVALID_REQUEST", "CORRELATION_ID_REUSED") and len(ad.submitted_ids) == 1


@pytest.mark.parametrize("returned", [True, False, None, "", "bad id!", 7, "x" * 65])
def test_f4_an_authorizer_must_return_a_valid_caller_id_or_the_request_is_denied(returned):
    api, ad, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")], authorize=lambda t: returned)
    assert api.request_light(client_token="t", action="TURN_ON", correlation_id="c1")["status"] == "UNAUTHORIZED"
    assert api.get_command_result(client_token="t", correlation_id="c1")["status"] == "UNAUTHORIZED" and ad.submitted_ids == []


def test_f4_default_is_still_deny_all():
    clock = FakeClock(0.0)
    loop = SerializedActionLoop(SimulatedLightsAdapter(clock), SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=2.0)
    api = AppInterface(ClassroomStateStore(clock=clock), action_loop=loop)
    assert api.request_light(client_token="anything", action="TURN_ON", correlation_id="c1")["status"] == "UNAUTHORIZED"


# --- F5: an id that already executed is never executed again, even after history eviction ---

def test_f5_retry_after_eviction_is_refused_not_re_executed():
    from assistant.app_interface import RESULT_HISTORY
    api, ad, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")])
    assert api.request_light(client_token="good-token", action="TURN_ON", correlation_id="old-1")["confirmed"]
    for i in range(RESULT_HISTORY):                             # fill the history (never submitted: unknown capability is not needed)
        api._results[("phone-a", f"f{i}")] = {"status": "RESULT", "action": "TURN_ON"}
    while len(api._results) > RESULT_HISTORY:
        api._results.popitem(last=False)
    before = list(ad.submitted_ids)
    r = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="old-1")
    assert (r["status"], r["reason"]) == ("INVALID_REQUEST", "CORRELATION_ID_EXPIRED") and ad.submitted_ids == before
    assert api.get_command_result(client_token="good-token", correlation_id="old-1")["status"] == "EXPIRED"


def test_f5_a_full_used_id_record_fails_closed(monkeypatch):
    import assistant.app_interface as ai
    monkeypatch.setattr(ai, "USED_ID_LIMIT", 2)
    api, ad, _ = app(authorize=TWO.get)
    for cid in ("a1", "a2"):
        assert api.request_light(client_token="token-a", action="TURN_ON", correlation_id=cid)["submitted"] is True
    r = api.request_light(client_token="token-a", action="TURN_ON", correlation_id="a3")
    assert (r["status"], r["reason"]) == ("INVALID_REQUEST", "REPLAY_RECORD_FULL")
    # the budget is PER CALLER: a full record for phone A never blocks phone B (review finding 4)
    assert api.request_light(client_token="token-b", action="TURN_ON", correlation_id="b1")["submitted"] is True
    api = ai.AppInterface(api._store, action_loop=api._loop, authorize=TWO.get)
    for cid in ("a1", "a2"):
        api.request_light(client_token="token-a", action="TURN_ON", correlation_id=cid)
    assert api.request_light(client_token="token-a", action="TURN_ON", correlation_id="a1")["replayed"] is True   # replays still work


class _BlockingAdapter(SimulatedLightsAdapter):
    """submit() waits until the test releases it: a request stays in flight while another one arrives."""

    def __init__(self, clock):
        super().__init__(clock)
        self.entered, self.release = threading.Event(), threading.Event()

    def submit(self, command):
        self.entered.set()
        assert self.release.wait(10)
        return super().submit(command)


def test_f5_concurrent_duplicates_execute_once():
    clock = FakeClock(100.0)
    ad = _BlockingAdapter(clock)
    loop = SerializedActionLoop(ad, SIMULATED_LIGHTS_CAPABILITIES, clock=clock, timeout_s=2.0)
    api = AppInterface(ClassroomStateStore(clock=clock), action_loop=loop, authorize=lambda t: "phone-a")
    ad.plan(replies=[(0.1, "APPLIED_CONFIRMED")])
    first = {}
    t = threading.Thread(target=lambda: first.update(api.request_light(client_token="t", action="TURN_ON", correlation_id="dup")))
    t.start()
    assert ad.entered.wait(5)
    assert api.request_light(client_token="t", action="TURN_ON", correlation_id="dup")["status"] == "BUSY"   # while in flight
    ad.release.set()
    t.join(5)
    again = api.request_light(client_token="t", action="TURN_ON", correlation_id="dup")
    assert first["confirmed"] and again["replayed"] is True and len(ad.submitted_ids) == 1


def test_f5_every_answer_names_the_server_session_and_a_restart_forgets_everything():
    api, ad, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")])
    r = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="s1")
    assert r["server_session"] == api.server_session == api.read_status()["server_session"]
    for bad in (api.request_light(client_token="no", action="TURN_ON", correlation_id="s2"),
                api.request_light(client_token="good-token", action="TURN_ON", correlation_id="bad id")):
        assert bad["server_session"] == api.server_session
    restarted, _, _ = app()                                     # documented limit: in-memory only, nothing survives a restart
    assert restarted.get_command_result(client_token="good-token", correlation_id="s1")["status"] == "NOT_FOUND"
    a, b = AppInterface(ClassroomStateStore()), AppInterface(ClassroomStateStore())      # default ids: a random session per process
    assert a.server_session != b.server_session                 # so a client CAN see that VESTA restarted


# --- F6: a harness failure in manual mode ends the session without anyone pressing Enter ---

class _NeverStdin:
    def __iter__(self):
        return self

    def __next__(self):
        threading.Event().wait()


def _manual_main(tmp_path, script, stdin):
    from scripts.ai2_stage_b_live import Engines, ManualActivationDetector
    from scripts.vesta_live_voice import main
    from scripts.vesta_runtime_demo import DemoPlayback, DemoTts
    from tests.stage_b_support import EnergyVad16, FakeBlockSource, FakeStt16
    result, err = {}, io.StringIO()

    def go():
        result["code"] = main(["--out-dir", str(tmp_path / "ev"), "--activation", "manual", "--output-device", "1"], out=io.StringIO(), err=err,
                              stdin=stdin, engines_factory=lambda a: Engines(ManualActivationDetector(), EnergyVad16(), FakeStt16("x")),
                              mic_factory=lambda h: FakeBlockSource(script, sample_rate=16000), tts_factory=DemoTts,
                              playback_factory=lambda d: DemoPlayback())

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(10)
    return t.is_alive(), result.get("code"), err.getvalue()


def test_f6_a_microphone_failure_ends_manual_mode_without_input(tmp_path):
    from tests.stage_b_support import Boom
    alive, code, err = _manual_main(tmp_path, [Boom(RuntimeError("mic unplugged"))], _NeverStdin())
    assert not alive and code == 3 and "RuntimeError" in err
    assert list((tmp_path / "ev").glob("*.stage_b.json"))      # the evidence record is still written


def test_f6_q_and_end_of_input_quit_manual_mode(tmp_path):
    from tests.stage_b_support import FakeBlockSource
    from scripts.vesta_live_voice import manual_loop

    class Never:
        is_finished = False

    armed = []
    det = type("D", (), {"arm": lambda self: armed.append(1)})()
    manual_loop(Never(), det, iter(["\n", "\n", "q\n", "\n"]), poll_s=0.01)
    assert armed == [1, 1]
    manual_loop(Never(), det, iter([]), poll_s=0.01)           # end of input returns instead of hanging
    t0 = __import__("time").monotonic()
    manual_loop(Never(), det, _NeverStdin(), duration_s=0.2, poll_s=0.01)
    assert __import__("time").monotonic() - t0 < 5             # --duration-s bounds manual mode too


# --- F7 / F9: consistent sensor identity, no overwriting by another source, subclasses accepted ---

@pytest.mark.parametrize("msg, reason", [
    ({**P, "sensor_id": "radar_b"}, "SENSOR_ID_MISMATCH"),
    ({**L, "sensor_id": "other"}, "SENSOR_ID_MISMATCH"),
    ({**T, "sensor_id": ""}, "BAD_SENSOR_ID"),
    ({**H, "sensor_id": ""}, "BAD_SENSOR_ID"),
    ({**L, "sensor_id": ""}, "BAD_SENSOR_ID"),
    ({**T, "sensor_id": "t 1"}, "BAD_SENSOR_ID"),
    ({**H, "sensor_id": 5}, "BAD_SENSOR_ID"),
])
def test_f7_sensor_id_rules_are_the_same_for_every_kind(msg, reason):
    with pytest.raises(SensorMessageError) as info:
        dec(msg)
    assert info.value.reason == reason


def test_f7_a_sensor_id_equal_to_the_source_is_accepted_for_single_sensor_kinds():
    assert dec({**P, "sensor_id": "ld2410b"}).present is False and dec({**L, "sensor_id": "bh1750"}).value == 0.0


def test_f7_a_second_source_cannot_overwrite_a_bound_slot():
    clock = FakeClock(0.0)
    store = ClassroomStateStore(FRESH, clock=clock)
    spoof = {**L, "source_id": "spoof", "value": 5.0}
    other_temp_source = {**T, "source_id": "imposter", "sensor_id": "temp_1", "value": 99.0}
    cross_kind = {"kind": "TEMPERATURE", "source_id": "bh1750", "status": "OK", "value": 30.0, "unit": "degC"}
    assert store.ingest_messages([L, T, spoof, other_temp_source, cross_kind], Origin.SIMULATED) == (2, 3)
    assert store.rejected == {"SOURCE_ID_MISMATCH": 2, "SOURCE_KIND_MISMATCH": 1}
    s = store.snapshot()
    assert (s.environment.lux.value, s.environment.lux.state.source_id) == (0.0, "bh1750")
    assert [(t.sensor_id, t.value) for t in s.environment.temperatures] == [("temp_1", 23.5)]


def test_f7_the_bound_source_can_still_report_failure_and_go_stale():
    clock = FakeClock(0.0)
    store = ClassroomStateStore(FRESH, clock=clock)
    store.ingest_messages([L, {"kind": "LUX", "source_id": "bh1750", "status": "FAILED", "error_reason": "I2C_NACK"}], Origin.SIMULATED)
    s = store.snapshot()
    assert s.environment.lux.value is None and s.environment.lux.state.usability is Usability.INVALID


def test_f9_reading_subclasses_are_stored():
    from fusion.contracts import LuxReading, SourceEnvelope

    class MyLux(LuxReading):
        pass

    store = ClassroomStateStore(FRESH, clock=FakeClock(1.0))
    store.ingest(MyLux(SourceEnvelope("lux_x", Origin.SIMULATED, 1.0), 3.0))
    assert store.snapshot().environment.lux.value == 3.0


# --- F3: covered in tests/test_handoff_docs.py (the actual git file selection must exclude CLAUDE.md) ---


# =========================================================================================
# J. second review round: regression tests
# =========================================================================================

def test_r1_a_reading_rejected_for_its_kind_claims_no_slot():
    store = ClassroomStateStore(FRESH, clock=FakeClock(0.0))
    cross = {"kind": "TEMPERATURE", "source_id": "bh1750", "status": "OK", "value": 30.0, "unit": "degC", "sensor_id": "temp_9"}
    real = {"kind": "TEMPERATURE", "source_id": "temp_9", "status": "OK", "value": 21.0, "unit": "degC"}
    assert store.ingest_messages([L, cross, real], Origin.SIMULATED) == (2, 1)
    assert store.rejected == {"SOURCE_KIND_MISMATCH": 1}
    assert [(t.sensor_id, t.value) for t in store.snapshot().environment.temperatures] == [("temp_9", 21.0)]


def test_r3_configured_sources_replace_first_come_binding():
    expected = {"bh1750": "lux", "temp_1": "temperature"}
    store = ClassroomStateStore(FRESH, clock=FakeClock(0.0), expected_sources=expected)
    bench = {**L, "source_id": "bench_lux", "value": 999.0}
    assert store.ingest_messages([bench, L, T, {**H}], Origin.SIMULATED) == (2, 2)            # bench board first, then the real one
    assert store.rejected == {"UNEXPECTED_SOURCE": 2}
    s = store.snapshot()
    assert (s.environment.lux.value, s.environment.lux.state.source_id) == (0.0, "bh1750")
    with pytest.raises(TypeError):
        ClassroomStateStore(expected_sources={"x": "co2"})


def test_r3_without_configuration_a_failed_first_reporter_does_not_own_the_slot():
    store = ClassroomStateStore(FRESH, clock=FakeClock(0.0))
    broken = {"kind": "LUX", "source_id": "broken_board", "status": "FAILED", "error_reason": "NO_SENSOR"}
    assert store.ingest_messages([broken, L], Origin.SIMULATED) == (2, 0)                   # the working sensor still binds
    assert store.snapshot().environment.lux.state.source_id == "bh1750"
    assert store.ingest_messages([broken], Origin.SIMULATED) == (0, 1) and store.rejected == {"SOURCE_ID_MISMATCH": 1}


def test_r6_two_humidity_sensors_behind_one_source_cannot_overwrite_each_other():
    store = ClassroomStateStore(FRESH, clock=FakeClock(0.0))
    left = {**H, "source_id": "env_board", "sensor_id": "hum_left", "value": 40.0}
    right = {**H, "source_id": "env_board", "sensor_id": "hum_right", "value": 70.0}
    assert store.ingest_messages([left, right, {**left, "value": 41.0}], Origin.SIMULATED) == (2, 1)
    assert store.rejected == {"SENSOR_ID_MISMATCH": 1}
    h = store.snapshot().environment.humidity
    assert (h.sensor_id, h.value) == ("hum_left", 41.0)


def test_r2_an_old_stuck_read_never_clears_a_newer_captures_in_flight_state():
    old, new = _StalledCapture(), _StalledCapture()
    caps = [old, new]
    src = CameraFrameSource(lambda: caps.pop(0), label="r", origin=Origin.SIMULATED, read_timeout_s=0.05)
    src.start()
    with pytest.raises(CameraError):
        src.read_frame()                                        # old capture stalls
    src.stop()                                                  # its release is deferred to its reader
    src.start()
    with pytest.raises(CameraError):
        src.read_frame()                                        # a read on the NEW capture is now in flight too
    old.gate.set()                                              # the old read returns: it releases ONLY the old capture
    for _ in range(100):
        if old.released:
            break
        threading.Event().wait(0.02)
    assert old.released == 1 and new.reading == 1
    src.stop()                                                  # the new read is still running: release must be deferred
    assert new.released == 0 and src.release_deferred
    new.gate.set()
    for _ in range(100):
        if new.released:
            break
        threading.Event().wait(0.02)
    assert new.released == 1 and not old.overlap and not new.overlap


def test_r5_a_request_that_was_never_submitted_can_be_retried_with_the_same_id():
    api, ad, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")], connected=False)
    first = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="req-5")
    assert (first["capability"], first["submitted"]) == ("SUPPORTED_BUT_UNAVAILABLE", False) and ad.submitted_ids == []
    assert api.get_command_result(client_token="good-token", correlation_id="req-5")["status"] == "NOT_FOUND"   # not recorded
    ad.reconnect()
    again = api.request_light(client_token="good-token", action="TURN_ON", correlation_id="req-5")
    assert again["confirmed"] is True and "replayed" not in again and len(ad.submitted_ids) == 1


def test_r8_an_invalid_action_is_reported_even_for_a_used_correlation_id():
    api, _, _ = app(plan=[(0.1, "APPLIED_CONFIRMED")])
    api.request_light(client_token="good-token", action="TURN_ON", correlation_id="x1")
    r = api.request_light(client_token="good-token", action="SET_BRIGHTNESS", correlation_id="x1")
    assert (r["status"], r["reason"]) == ("INVALID_REQUEST", "UNSUPPORTED_ACTION")
