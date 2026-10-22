"""REST hardening of the team backend (services/api/server.py, services/core/config.py) and the /voice/ask contract.

Real team server, Runtime, Commands and replay-mode Lights on 127.0.0.1. SOFTWARE-VALIDATED. No network beyond loopback.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parent.parent
TEAM_ROOT = AI_ROOT.parent
if not (TEAM_ROOT / "services" / "api" / "server.py").is_file():
    pytest.skip("the team backend (services/) is not next to this folder", allow_module_level=True)
if str(TEAM_ROOT) not in sys.path:
    sys.path.append(str(TEAM_ROOT))
os.environ.setdefault("VESTA_MODE", "replay")

from assistant.backend_bridge import AiBridge                             # noqa: E402
from services.api import server                                           # noqa: E402
from services.core import config as team_config                           # noqa: E402
from services.core.runtime import Runtime                                 # noqa: E402
from services.device.hardware import Hardware                             # noqa: E402
from services.device.lights import Lights                                 # noqa: E402
from services.voice.commands import Commands                              # noqa: E402
from tests.test_ai_backend_e2e import free_port, run_main                 # noqa: E402

KEY = "test-key-" + "0" * 16                    # generated test value, not a credential


class Cfg:
    """The team config module with chosen extra settings (no file is read or written)."""

    def __init__(self, **extra):
        self.extra = extra

    def load(self):
        return {**team_config.DEFAULTS, **self.extra}

    def update(self, key, value):
        return team_config.update(key, value)


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(team_config, "PATH", str(tmp_path / "device_config.json"))   # /config never writes into the repository
    made = []

    def build(key=None, ai=False, **settings):
        if key:
            monkeypatch.setenv(server.KEY_ENV, key)
        else:
            monkeypatch.delenv(server.KEY_ENV, raising=False)
        hw, lights = Hardware(), Lights()
        hw.read_sensors()
        rt = Runtime(lights, None, None, hw)
        cmd = Commands(hw, rt, team_config)
        bridge = AiBridge(cmd, rt, hw, None, simulated=True) if ai else None
        srv = server.start(cmd, hw, rt, Cfg(**settings), 0, None, bridge)
        made.append(srv)
        return srv, rt, lights

    yield build
    for s in made:
        s.shutdown()
        s.server_close()


def call(srv, path, body=None, *, raw=None, headers=None, method=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    h = {"Content-Type": "application/json"} if data is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}{path}", data=data, headers=h,
                                 method=method or ("GET" if data is None else "POST"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            return r.status, dict(r.headers), json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, dict(e.headers), json.loads(body) if body else None


# --- exposure ---------------------------------------------------------------------------------------------------------

def test_defaults_are_local_only_and_safety_keys_are_locked():
    d = team_config.DEFAULTS
    assert (d["api.host"], d["api.cors_origins"], d["ai.enabled"], d["ai.voice_input"]) == ("127.0.0.1", [], False, "text")
    assert {"ai.enabled", "ai.voice_input", "vision.detector", "vision.yolox_lighting", "api.host", "api.cors_origins"} <= team_config.LOCKED


@pytest.mark.parametrize("host, key, want", [
    ("127.0.0.1", None, ("127.0.0.1", None)),
    ("0.0.0.0", None, ("127.0.0.1", None)),                 # LAN asked for, no key: refused, local only
    ("0.0.0.0", "short", ("127.0.0.1", None)),              # a weak key is ignored
    ("0.0.0.0", KEY, ("0.0.0.0", KEY)),                     # LAN only with a key
])
def test_lan_exposure_needs_an_access_key(monkeypatch, host, key, want):
    monkeypatch.delenv(server.KEY_ENV, raising=False)
    if key:
        monkeypatch.setenv(server.KEY_ENV, key)
    assert server.access({"api.host": host}) == want


def test_the_server_really_binds_loopback_when_lan_is_refused(api):
    srv, _, _ = api(**{"api.host": "0.0.0.0"})
    assert srv.server_address[0] == "127.0.0.1"


def test_entry_point_refuses_lan_without_a_key(tmp_path):
    port = free_port()
    env_key = os.environ.pop(server.KEY_ENV, None)
    proc = run_main(tmp_path, {"api_port": port, "api.host": "0.0.0.0"})
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=2).read()
                break
            except (urllib.error.URLError, ConnectionError):
                if proc.poll() is not None:
                    break
                import time
                time.sleep(0.1)
    finally:
        proc.terminate()
        proc.wait(10)
        if env_key is not None:
            os.environ[server.KEY_ENV] = env_key
    log = proc.stdout.read().decode(errors="replace")
    assert "serving on 127.0.0.1 only" in log and f"api listening on 127.0.0.1:{port}" in log


# --- authentication ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method, path, body", [("GET", "/state", None), ("POST", "/command", {"intent": "lights_on"}),
                                                ("POST", "/lights/zone", {"zone": "left", "brightness": 50}),
                                                ("POST", "/config", {"key": "light.base", "value": 50}),
                                                ("POST", "/voice/ask", {"text": "turn on the lights"})])
def test_with_a_key_every_route_rejects_missing_or_wrong_keys(api, method, path, body):
    srv, rt, lights = api(key=KEY, ai=True)
    for headers in ({}, {"X-Room-Key": "wrong"}, {"X-Room-Key": KEY[:-1]}):
        code, _, out = call(srv, path, body, headers=headers, method=method)
        assert (code, out["error"]["code"]) == (401, "unauthorized")
    assert rt.overrides == {} and lights.zones["left"] == 0
    code, _, _ = call(srv, "/state", headers={"X-Room-Key": KEY})
    assert code == 200


def test_the_right_key_allows_a_command(api):
    srv, rt, _ = api(key=KEY)
    code, _, out = call(srv, "/lights/zone", {"zone": "left", "brightness": 40}, headers={"X-Room-Key": KEY})
    assert code == 200 and rt.overrides["left"].brightness == 40


# --- CORS is not authentication; cross-site posts are refused --------------------------------------------------------------

def test_cors_answers_only_configured_origins(api):
    srv, _, _ = api(**{"api.cors_origins": ["http://app.local"]})
    pre = {"Access-Control-Request-Method": "POST"}
    _, h, _ = call(srv, "/voice/ask", method="OPTIONS", headers={"Origin": "http://evil.example", **pre})
    assert "Access-Control-Allow-Origin" not in h
    _, h, _ = call(srv, "/voice/ask", method="OPTIONS", headers={"Origin": "http://app.local", **pre})
    assert h["Access-Control-Allow-Origin"] == "http://app.local" and "X-Room-Key" in h["Access-Control-Allow-Headers"]
    _, h, _ = call(srv, "/state", headers={"Origin": "http://evil.example"})
    assert "Access-Control-Allow-Origin" not in h                       # no wildcard anywhere


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", None])
def test_non_json_posts_are_refused_so_a_web_page_cannot_post_a_simple_request(api, ctype):
    srv, rt, _ = api()
    headers = {"Content-Type": ctype} if ctype else {}
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/lights/zone",
                                 data=json.dumps({"zone": "left", "brightness": 90}).encode(), headers=headers, method="POST")
    if ctype is None:
        req.remove_header("Content-type")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 400 and rt.overrides == {}


def test_an_oversized_body_is_refused_unread(api):
    srv, rt, _ = api()
    code, _, out = call(srv, "/command", raw=b"{" + b" " * (server.MAX_BODY + 10) + b"}")
    assert code == 400 and out["error"]["detail"]["reason"] == "too_large"


# --- locked safety settings -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key, value", [("ai.enabled", True), ("vision.yolox_lighting", True), ("vision.detector", "yolox"),
                                        ("api.host", "0.0.0.0"), ("api.cors_origins", ["*"]), ("ai.voice_input", "microphone")])
def test_safety_settings_cannot_be_changed_over_the_api(api, tmp_path, key, value):
    srv, _, _ = api()
    code, _, out = call(srv, "/config", {"key": key, "value": value})
    assert code == 400 and "device_config.json" in out["error"]["message"]
    assert not (tmp_path / "device_config.json").exists()


def test_ordinary_settings_still_change(api, tmp_path):
    srv, _, _ = api()
    code, _, out = call(srv, "/config", {"key": "light.base", "value": 50})
    assert code == 200 and json.loads((tmp_path / "device_config.json").read_text())["light.base"] == 50


# --- validation ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, body", [
    ("/lights/zone", {"zone": "left", "brightness": "abc"}), ("/lights/zone", {"zone": "left", "brightness": 150}),
    ("/lights/zone", {"zone": "left", "brightness": -5}), ("/lights/zone", {"zone": "left", "brightness": True}),
    ("/lights/zone", {"zone": "left", "brightness": 50.5}), ("/lights/zone", {"zone": "foo", "brightness": 50}),
    ("/lights/zone", {"zone": "left", "brightness": 50, "source": "Robert'); --"}),
    ("/command", {"intent": "explode"}), ("/command", {"intent": 5}), ("/command", {"intent": "lights_on", "params": {"zone": "foo"}}),
    ("/command", {"intent": "set_brightness", "params": {"brightness": "x"}}),
    ("/command", {"intent": "set_brightness", "params": {"brightness": 101}}),
    ("/command", {"intent": "lights_on", "params": {"colour": "red"}}), ("/command", {"intent": "lights_on", "params": [1]}),
    ("/lights/auto", {"zone": "foo"}), ("/presentation", {"active": "yes"}), ("/presentation", {"active": 1}),
    ("/config", {"key": 5}),
])
def test_invalid_parameters_are_400_and_change_nothing(api, path, body):
    srv, rt, lights = api()
    code, _, out = call(srv, path, body)
    assert code == 400 and out["ok"] is False and out["error"]["code"] == "invalid_input" and "detail" in out["error"]
    assert rt.overrides == {} and rt.presentation is False and set(lights.zones.values()) == {0}


def test_valid_parameters_still_work(api):
    srv, rt, _ = api()
    assert call(srv, "/command", {"intent": "set_brightness", "params": {"zone": "left", "brightness": 60, "source": "app"}})[2]["ok"]
    assert call(srv, "/lights/zone", {"zone": "professor", "brightness": 0})[0] == 200
    assert call(srv, "/presentation", {"active": True})[2]["presentation"] is True
    assert call(srv, "/lights/auto", {"zone": "left"})[0] == 200
    assert rt.overrides["professor"].brightness == 0


def test_a_handler_error_is_a_500_reply_not_a_dropped_connection(api, monkeypatch):
    srv, rt, _ = api()
    monkeypatch.setattr(rt, "status", lambda: 1 / 0)
    code, _, out = call(srv, "/state")
    assert (code, out["error"]["code"]) == (500, "internal")
    assert call(srv, "/lights")[0] == 200                               # the server keeps serving


# --- /voice/ask against the v-04 schemas (unmerged contract branch) ------------------------------------------------------

def _schemas():
    out = {}
    for name in ("VoiceAskResponse.json", "VoiceCommand.json", "common.json", "ErrorResponse.json"):
        r = subprocess.run(["git", "show", f"origin/v-04-supervision:contracts/schemas/{name}"], cwd=TEAM_ROOT, capture_output=True, text=True)
        if r.returncode:
            pytest.skip("origin/v-04-supervision is not available in this clone")
        out[name] = json.loads(r.stdout)
    return out


def _check(v, s, doc, S, path="$"):
    """The JSON-Schema subset these four schemas use: $ref, anyOf, allOf if/then, type, const, enum, pattern, lengths, ranges, objects."""
    errs = []
    if "$ref" in s:
        f, _, frag = s["$ref"].partition("#")
        node = S[f] if f else doc
        for part in [p for p in frag.split("/") if p]:
            node = node[part]
        errs += _check(v, node, S[f] if f else doc, S, path)
    if "anyOf" in s and not any(not _check(v, x, doc, S, path) for x in s["anyOf"]):
        errs.append(f"{path}: anyOf")
    for rule in s.get("allOf", []):
        if not _check(v, rule["if"], doc, S, path):
            errs += _check(v, rule["then"], doc, S, path)
    types = {"object": dict, "string": str, "number": (int, float), "integer": int, "null": type(None), "boolean": bool, "array": list}
    t = s.get("type")
    if t and not any(isinstance(v, types[x]) and not (x in ("number", "integer") and isinstance(v, bool)) for x in ([t] if isinstance(t, str) else t)):
        return errs + [f"{path}: type"]
    if "const" in s and v != s["const"]:
        errs.append(f"{path}: const")
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: enum")
    if isinstance(v, str) and ("pattern" in s and not re.search(s["pattern"], v) or not s.get("minLength", 0) <= len(v) <= s.get("maxLength", 10**9)):
        errs.append(f"{path}: string")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and not s.get("minimum", -1e18) <= v <= s.get("maximum", 1e18):
        errs.append(f"{path}: range")
    if isinstance(v, dict):
        errs += [f"{path}: missing {k}" for k in s.get("required", []) if k not in v]
        if s.get("additionalProperties") is False:
            errs += [f"{path}: extra {k}" for k in v if k not in s.get("properties", {})]
        for k, sub in s.get("properties", {}).items():
            if k in v:
                errs += _check(v[k], sub, doc, S, f"{path}.{k}")
    return errs


@pytest.mark.parametrize("text", ["turn on the lights", "what is the temperature", "what is the humidity", "how bright is the room",
                                  "how many students are here", "turn on the light", "tell me a joke", "turn off the lights"])
def test_voice_ask_replies_match_the_v04_schemas(api, text):
    S = _schemas()
    srv, rt, _ = api(ai=True)
    rt.start()
    try:
        code, _, out = call(srv, "/voice/ask", {"text": text})
    finally:
        rt.stop()
    assert code in (200, 504)
    target = S["VoiceAskResponse.json"] if code == 200 else S["ErrorResponse.json"]
    assert _check(out["data"] if code == 200 else out, target, target, S) == []


@pytest.mark.parametrize("body, raw", [({"text": ""}, None), ({"text": "   "}, None), ({"text": "x" * 201}, None),
                                       ({"text": "hi", "x": 1}, None), (None, b"{bad"), ({"text": "hi", "request_id": "REQ"}, None)])
def test_voice_ask_errors_match_the_v04_error_schema(api, body, raw):
    S = _schemas()
    srv, _, _ = api(ai=True)
    code, _, out = call(srv, "/voice/ask", body, raw=raw)
    assert code == 400 and _check(out, S["ErrorResponse.json"], S["ErrorResponse.json"], S) == []


def test_voice_ask_without_ai_is_the_v04_degraded_error(api):
    S = _schemas()
    srv, _, _ = api(ai=False)
    code, _, out = call(srv, "/voice/ask", {"text": "hi"})
    assert code == 503 and out["error"]["code"] == "degraded" and _check(out, S["ErrorResponse.json"], S["ErrorResponse.json"], S) == []
