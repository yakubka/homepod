"""END-TO-END: the AI subsystem inside the team backend, through the REAL REST server, team Runtime thread, Commands, Lights,
OccupancyLoop, the AI runtime, router, action loop, response policy and arbiter. SOFTWARE-VALIDATED / SIMULATED.

Simulated: team replay-mode devices (Hardware / LightSensor random values, Lights store a number), scripted camera frames and
scripted YOLOX boxes, fake TTS / playback (no audio). No Raspberry Pi, camera, microphone, ESP32, GPIO or light. The 14
scenarios of the integration task are marked S1..S14. The entry-point test runs the team main.main() in a subprocess.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

AI_ROOT = Path(__file__).resolve().parent.parent
TEAM_ROOT = AI_ROOT.parent
if not (TEAM_ROOT / "services" / "api" / "server.py").is_file():
    pytest.skip("the team backend (services/) is not next to this folder", allow_module_level=True)
if str(TEAM_ROOT) not in sys.path:
    sys.path.append(str(TEAM_ROOT))
os.environ.setdefault("VESTA_MODE", "replay")

import vision.team_adapter as team_adapter                                # noqa: E402
from assistant.backend_bridge import AiBridge, TeamLightsAdapter, valid_request_id   # noqa: E402
from assistant.runtime import OutcomeKind                                 # noqa: E402
from services.api import server                                           # noqa: E402
from services.core import config as team_config                           # noqa: E402
from services.core.engine import Lecture                                  # noqa: E402
from services.core.runtime import Runtime                                 # noqa: E402
from services.device.hardware import Hardware                             # noqa: E402
from services.device.light_sensor import LightSensor                      # noqa: E402
from services.device.lights import Lights                                 # noqa: E402
from services.vision.detector import engine_occupancy, make_detector      # noqa: E402
from services.vision.occupancy import OccupancyLoop                       # noqa: E402
from services.voice.commands import Commands                              # noqa: E402
from speech.pipeline import PipelineStatus                                # noqa: E402
from tests.runtime_support import make_result                             # noqa: E402
from tests.test_team_backend_integration import MockCamera, ScriptedYolox, centre, frame   # noqa: E402

ZONES = ("left", "center", "right", "professor")
ALL_ON, ALL_OFF = {z: 100 for z in ZONES}, {z: 0 for z in ZONES}
CONTRACT_KEYS = {"request_id", "command", "outcome", "reply_text", "decision_id"}
COMMAND_KEYS = {"request_id", "intent", "action", "target", "params", "confidence", "raw_text", "source"}


# --- the stack ----------------------------------------------------------------------------------------------------------

class Stack:
    """team devices (replay) + Runtime thread + Commands + AiBridge + the team REST server on a free port."""

    def __init__(self, occupancy=None, ai=True, simulated=True, commands=None, lux=True):
        self.hw = Hardware()
        self.hw.read_sensors()                                 # what the team poll thread does first
        self.lux = LightSensor() if lux else None
        if self.lux:
            self.lux.read()
        self.lights = Lights()
        self.runtime = Runtime(self.lights, occupancy, self.lux, self.hw)
        self.commands = commands(self) if commands else Commands(self.hw, self.runtime, team_config, None, None, self.lux)
        self.ai = AiBridge(self.commands, self.runtime, self.hw, self.lux, simulated=simulated) if ai else None
        self.api = server.start(self.commands, self.hw, self.runtime, team_config, 0, self.lux, self.ai)
        self.url = f"http://127.0.0.1:{self.api.server_port}"
        self.runtime.start()

    def close(self):
        self.runtime.stop()
        self.api.shutdown()
        self.api.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=10) as r:
            return r.status, json.loads(r.read())

    def post(self, path, body, raw=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def ask(self, text, **extra):
        return self.post("/voice/ask", {"text": text, **extra})

    def wait_lights(self, want, timeout=4.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.lights.zones == want:
                return True
            time.sleep(0.05)
        return False


@pytest.fixture
def stack():
    made = []

    def build(**kw):
        made.append(Stack(**kw))
        return made[-1]

    yield build
    for s in made:
        s.close()


def assert_contract(data, outcome, intent):
    assert set(data) == CONTRACT_KEYS and set(data["command"]) == COMMAND_KEYS
    assert valid_request_id(data["request_id"]) and data["command"]["request_id"] == data["request_id"]
    assert data["outcome"] == outcome and data["command"]["intent"] == intent
    assert 1 <= len(data["reply_text"]) <= 300 and data["decision_id"] is None
    assert set(data["command"]["target"]) == {"type", "zone"} and 0.0 <= data["command"]["confidence"] <= 1.0


# --- S1 valid voice command -----------------------------------------------------------------------------------------------

def test_s1_voice_command_reaches_the_team_lights_and_is_confirmed_only_after_they_change(stack):
    s = stack()
    played = s.ai.playback.calls
    code, body = s.ask("Turn on the lights.")
    assert code == 200
    assert_contract(body["data"], "executed", "lights_on")
    assert body["data"]["reply_text"] == "In the simulation, the classroom lights were turned on."
    assert s.lights.zones == ALL_ON                                     # confirmed BECAUSE every team zone (incl. professor) is at 100
    assert s.ai.playback.calls == played + 1                            # the reply went through the arbiter -> TTS -> playback
    _, lights = s.get("/lights")
    assert lights["zones"] == ALL_ON and lights["real"] is False
    _, dec = s.get("/decisions?limit=20")
    assert any(d["rule"] == "manual.override" and d["inputs"]["source"] == "voice" and d["applied"] for d in dec["decisions"])
    code, body = s.ask("turn off the lights")
    assert (code, body["data"]["outcome"], s.lights.zones) == (200, "executed", ALL_OFF)


# --- S2 unsupported / ambiguous ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, reply", [("turn on the projector", "Sorry, I can't help with that yet."),
                                         ("turn on the light", "Sorry, that could mean more than one thing. Please be more specific."),
                                         ("set the lights to 40 percent", "Sorry, I can't help with that yet."),
                                         ("tell me a joke", "Sorry, I can't help with that yet.")])
def test_s2_unsupported_or_ambiguous_requests_change_nothing(stack, text, reply):
    s = stack()
    code, body = s.ask(text)
    assert code == 200
    assert_contract(body["data"], "not_understood", "unknown")
    assert body["data"]["reply_text"] == reply
    assert s.runtime.overrides == {} and s.lights.zones == ALL_OFF


# --- S3 camera -> API ----------------------------------------------------------------------------------------------------------

def yolox_loop(monkeypatch, script, cfg):
    monkeypatch.setattr(team_adapter, "YoloxOnnxPersonDetector", ScriptedYolox(script))
    return engine_occupancy(cfg, OccupancyLoop(MockCamera([frame() for _ in script]), make_detector(cfg), 0.05))


def test_s3_yolox_counts_reach_the_api_marked_advisory(monkeypatch, stack):
    occ = yolox_loop(monkeypatch, [centre(5)], {**team_config.DEFAULTS, "vision.detector": "yolox"})
    occ._loop.last = occ.snapshot()
    s = stack(occupancy=occ)
    _, o = s.get("/occupancy")
    assert (o["total"], o["zones"], o["source"], o["used_for_lighting"]) == (5, {"left": 0, "center": 5, "right": 0}, "camera", False)
    _, st = s.get("/state")
    assert st["occupancy"]["used_for_lighting"] is False and st["ai"]["origin"] == "SIMULATED"


# --- S4 sensors -> state -> decisions -> API -----------------------------------------------------------------------------------------

def test_s4_sensor_answers_match_the_team_readings_and_carry_their_origin(stack):
    s = stack()
    _, sensors = s.get("/sensors")
    code, body = s.ask("what is the temperature")
    assert_contract(body["data"], "answered", "query_temperature")
    assert f"{s.hw.sensors['temperature']:g}" in body["data"]["reply_text"]       # the same reading, never a made-up value
    assert sensors["sensor"]["temperature"] == s.hw.sensors["temperature"]
    _, body = s.ask("what is the humidity")
    assert_contract(body["data"], "answered", "query_humidity")
    _, body = s.ask("how bright is the room")
    assert body["data"]["outcome"] == "answered" and body["data"]["command"]["intent"] == "unknown"   # no contract intent for lux
    _, st = s.get("/state")
    assert st["ai"]["sensors"] == "team-replay" and st["ai"]["capabilities"] == ["BRIGHTNESS", "HUMIDITY", "TEMPERATURE"]


def test_s4_the_team_engine_decides_from_the_same_lux(stack):
    s = stack()
    s.lux.lux = 900.0                                                  # bright daylight: the team rule switches a lit zone off
    res = s.runtime.tick(team_config.DEFAULTS, now=time.time())
    assert res.room is not None and s.runtime.state(team_config.DEFAULTS).environment.lux == 900


# --- S5 mobile command -> REST -> result -----------------------------------------------------------------------------------------------

def test_s5_mobile_rest_commands_use_the_same_team_path_and_voice_sees_the_result(stack):
    s = stack()
    code, body = s.post("/command", {"intent": "lights_on", "params": {"source": "app"}})
    assert (code, body["ok"]) == (200, True) and s.wait_lights(ALL_ON)
    code, body = s.post("/lights/zone", {"zone": "left", "brightness": 30})
    assert code == 200 and s.wait_lights({**ALL_ON, "left": 30})
    code, body = s.ask("turn off the lights")                         # voice after the app: same overrides table, last writer wins
    assert body["data"]["outcome"] == "executed" and s.lights.zones == ALL_OFF


# --- S6 conflicting voice and mobile commands ----------------------------------------------------------------------------------------

def test_s6_a_mobile_change_during_a_voice_command_is_never_confirmed_as_the_voice_result(stack):
    class AppInterferes(Commands):
        def run(self, intent, params=None):
            out = super().run(intent, params)
            self.rt.override("left", 30, "app")                       # the phone writes the same zone right after the voice command
            return out

    s = stack(commands=lambda st: AppInterferes(st.hw, st.runtime, team_config, None, None, st.lux))
    code, body = s.ask("turn on the lights")
    assert code == 504 and body["error"]["code"] == "device_timeout"  # unknown, not "executed", not "failed"
    assert s.wait_lights({**ALL_ON, "left": 30}) and s.runtime.overrides["left"].source == "app"
    assert s.ai.last["action"] == "TIMEOUT_UNKNOWN"


# --- S7 camera / microphone failure -----------------------------------------------------------------------------------------------------

def test_s7_camera_failure_is_no_camera_count_and_voice_still_works(monkeypatch, stack):
    occ = yolox_loop(monkeypatch, [RuntimeError("inference failed")], {**team_config.DEFAULTS, "vision.detector": "yolox"})
    assert occ.start() is True
    time.sleep(0.2)
    s = stack(occupancy=occ)
    _, o = s.get("/occupancy")
    assert o["total"] is None and o["source"] in ("none", "camera")   # never a fabricated 0
    assert s.ask("turn on the lights")[1]["data"]["outcome"] == "executed"
    occ.stop()


def test_s7_microphone_or_stt_failure_gives_the_fallback_and_never_a_command(stack):
    s = stack()
    out = s.ai.runtime.handle_result(make_result(PipelineStatus.STT_FAILED))
    assert out.kind is OutcomeKind.FALLBACK_OUTPUT and out.command is None and s.runtime.overrides == {}


# --- S8 stale or missing sensors ------------------------------------------------------------------------------------------------------

def test_s8_real_mode_sensors_are_unavailable_until_the_team_hardware_reports_validity(stack):
    s = stack(simulated=False)
    code, body = s.ask("what is the temperature")
    assert code == 200 and body["data"]["outcome"] == "rejected"      # "unavailable" is not an answer
    assert str(s.hw.sensors["temperature"]) not in body["data"]["reply_text"]
    assert s.ai.status()["origin"] == "UNKNOWN"


def test_s8_a_missing_light_sensor_is_unavailable_not_zero(stack):
    s = stack(lux=False)
    _, body = s.ask("how bright is the room")
    assert body["data"]["outcome"] == "rejected" and "0" not in body["data"]["reply_text"]


def test_s8_a_stale_reading_is_not_used(stack, monkeypatch):
    s = stack()
    s.ai._sensors = None                                              # the source stops delivering
    s.ai._store._clock = lambda: time.monotonic() + 3600              # an hour later every stored reading is stale
    _, body = s.ask("what is the temperature")
    assert body["data"]["outcome"] == "rejected"


# --- S9 invalid, repeated, malformed --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body, raw", [({"text": ""}, None), ({"text": "   "}, None), ({"text": "\t\n"}, None),
                                       ({"text": "x" * 201}, None), ({"text": 5}, None), ({}, None),
                                       ({"text": "hi", "extra": 1}, None), ({"text": "hi", "request_id": "REQ-1"}, None),
                                       (None, b"[1, 2]")])
def test_s9_malformed_requests_are_400_and_execute_nothing(stack, body, raw):
    s = stack()
    code, out = s.post("/voice/ask", body, raw)
    assert code == 400 and out["ok"] is False and out["error"]["code"] == "invalid_input" and "detail" in out["error"]
    assert s.runtime.overrides == {}


def test_s9_invalid_json_keeps_the_team_error_shape(stack):
    code, out = stack().post("/voice/ask", None, b"{not json")
    assert code == 400 and out["error"]["code"] == "invalid_input"


def test_s9_repeated_requests_are_new_commands_and_the_client_id_is_echoed(stack):
    s = stack()
    a = s.ask("turn on the lights", request_id="req_phone00001")[1]["data"]
    b = s.ask("turn on the lights", request_id="req_phone00001")[1]["data"]
    assert a["request_id"] == b["request_id"] == "req_phone00001"
    assert a["outcome"] == b["outcome"] == "executed"                 # no idempotency store: documented, the light state is the same


# --- S10 disconnection and recovery ----------------------------------------------------------------------------------------------------

def test_s10_engine_down_is_unknown_then_recovers(stack):
    s = stack()
    s.runtime.stop()
    time.sleep(1.2)                                                   # the tick thread exits
    code, body = s.ask("turn on the lights")
    assert code == 504 and s.lights.zones == ALL_OFF                  # accepted by Commands, never applied: not confirmed
    s.runtime.start()
    assert s.wait_lights(ALL_ON)                                      # the pending override applies once the engine runs again
    s.runtime.auto()
    s.runtime.overrides.clear()
    code, body = s.ask("turn off the lights")
    assert (code, body["data"]["outcome"]) == (200, "executed")


def test_s10_a_backend_refusal_is_rejected_then_the_next_request_works(stack):
    calls = []

    class FlakyCommands(Commands):
        def run(self, intent, params=None):
            calls.append(intent)
            if len(calls) == 1:
                return {"ok": False, "intent": intent, "say": "Something went wrong"}
            return super().run(intent, params)

    s = stack(commands=lambda st: FlakyCommands(st.hw, st.runtime, team_config, None, None, st.lux))
    assert s.ask("turn on the lights")[1]["data"]["outcome"] == "rejected"
    assert s.ask("turn on the lights")[1]["data"]["outcome"] == "executed"


def test_s10_an_exception_from_the_backend_is_unknown_not_failure():
    class Boom:
        def run(self, intent, params):
            raise ConnectionError("link down")

    adapter = TeamLightsAdapter(Boom(), Lights(), simulated=True)
    from actions.contracts import SubmitStatus
    from intents.contracts import CommandAction, CommandTarget, IntentCategory, validate_command
    cmd = validate_command({"intent_category": IntentCategory.CLASSROOM_CONTROL, "target": CommandTarget.CLASSROOM_LIGHTS,
                            "action": CommandAction.TURN_ON, "request_id": "c-aaaaaaaaaaaa-1"}).command
    assert adapter.submit(cmd) is SubmitStatus.DELIVERY_UNCERTAIN


# --- S11 missing models ----------------------------------------------------------------------------------------------------------------

def test_s11_missing_yolox_model_fails_startup_loudly_and_the_ai_needs_no_model(monkeypatch, tmp_path, stack):
    import vision.detector as ai_detector
    from vision.detector import ModelFileMissing
    monkeypatch.setattr(ai_detector, "resolve_model_path", lambda path=None: tmp_path / "missing.onnx")
    with pytest.raises(ModelFileMissing):
        make_detector({**team_config.DEFAULTS, "vision.detector": "yolox"})
    s = stack()                                                        # text voice path: no wake / VAD / STT / TTS model is loaded
    assert s.ask("what is the humidity")[0] == 200


# --- S12 startup and graceful shutdown (the real entry point) -------------------------------------------------------------------------

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


LAUNCH = ("import sys, os; sys.path.insert(0, os.getcwd()); from services.core import config; config.PATH = sys.argv[1]; "
          "import main; main.main()")


def run_main(tmp_path, settings):
    cfg = tmp_path / "device_config.json"
    cfg.write_text(json.dumps(settings), encoding="utf-8")
    env = {**os.environ, "VESTA_MODE": "replay", "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen([sys.executable, "-c", LAUNCH, str(cfg)], cwd=str(TEAM_ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def call(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_up(port, proc):
    for _ in range(100):
        if proc.poll() is not None:
            pytest.fail("main.py exited: " + proc.stdout.read().decode(errors="replace")[-2000:])
        try:
            return call(port, "/state")
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    pytest.fail("main.py did not start")


def test_s12_entry_point_starts_with_ai_enabled_and_serves_voice_ask(tmp_path):
    port = free_port()
    proc = run_main(tmp_path, {"api_port": port, "ai.enabled": True})
    try:
        _, state = wait_up(port, proc)
        assert state["ai"]["enabled"] is True and state["ai"]["origin"] == "SIMULATED"
        code, body = call(port, "/voice/ask", {"text": "turn on the lights"})
        assert code == 200 and body["data"]["outcome"] == "executed"
        assert call(port, "/lights")[1]["zones"] == ALL_ON
    finally:
        proc.terminate()                                               # Windows: no SIGINT/SIGTERM handler test (see the report)
        proc.wait(10)


def test_s12_graceful_shutdown_sequence_stops_ai_and_engine(stack):
    s = stack()
    s.ai.stop()
    s.runtime.stop()
    assert s.runtime.running is False


# --- S13 AI disabled = the default team behaviour -----------------------------------------------------------------------------------

def test_s13_ai_is_off_by_default_and_the_backend_behaves_as_before(tmp_path):
    assert team_config.DEFAULTS["ai.enabled"] is False and team_config.DEFAULTS["vision.yolox_lighting"] is False
    port = free_port()
    proc = run_main(tmp_path, {"api_port": port})
    try:
        _, state = wait_up(port, proc)
        assert "ai" not in state                                       # /state unchanged
        code, body = call(port, "/voice/ask", {"text": "turn on the lights"})
        assert code == 503 and body["error"]["code"] == "degraded"
        assert call(port, "/command", {"intent": "query_temperature"})[1]["ok"] is True   # team routes untouched
    finally:
        proc.terminate()
        proc.wait(10)
    out = proc.stdout.read().decode(errors="replace")
    assert "ai voice bridge" not in out


# --- S14 lighting safeguards ------------------------------------------------------------------------------------------------------------

def in_session(rt):
    rt.lecture = Lecture(id="L1", name="Capstone", start=0.0, end=9e9, expected=20)   # lecture running, no lux sensor = dark enough
    rt.room = "in_session"
    return rt


def test_s14_yolox_zero_never_turns_lights_off_and_counts_never_set_brightness(monkeypatch):
    lights = Lights()
    lights.zones.update(left=60, center=60, right=60)
    occ = yolox_loop(monkeypatch, [[], centre(12)], {**team_config.DEFAULTS, "vision.detector": "yolox"})
    rt = in_session(Runtime(lights, occ, None, None))
    for _ in range(2):
        occ._loop.last = occ.snapshot()                                # 0 people, then 12 in the centre
        rt.tick(team_config.DEFAULTS, now=1000.0)
        assert {z: lights.zones[z] for z in ("left", "center", "right")} == {"left": 60, "center": 60, "right": 60}


def test_s14_the_opt_in_hands_counts_to_the_team_rule(monkeypatch):
    occ = yolox_loop(monkeypatch, [centre(12)], {**team_config.DEFAULTS, "vision.detector": "yolox", "vision.yolox_lighting": True})
    assert isinstance(occ, OccupancyLoop)                              # no advisory wrapper when the team opts in
    occ.last = occ.snapshot()
    lights = Lights()
    in_session(Runtime(lights, occ, None, None)).tick(team_config.DEFAULTS, now=1000.0)
    assert lights.zones["center"] == 100


def test_s14_manual_override_reaches_a_zone_no_rule_proposes_for():
    lights = Lights()
    rt = Runtime(lights, None, None, None)
    rt.override("professor", 100, "app")
    rt.tick(team_config.DEFAULTS, now=time.time())
    assert lights.zones["professor"] == 100                            # before the engine fix: stayed 0 while "Lights on everywhere" was said


def test_s14_mobilenet_default_path_is_unwrapped():
    loop = OccupancyLoop(MockCamera([]), object(), 1.0)
    assert engine_occupancy(team_config.DEFAULTS, loop) is loop
