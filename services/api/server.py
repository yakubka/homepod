import hmac
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("server")

LOOPBACK = ("127.0.0.1", "localhost", "::1")
KEY_ENV = "VESTA_API_KEY"
MIN_KEY_LEN = 16
MAX_BODY = 16 * 1024
SOURCE = re.compile(r"^[a-z][a-z_]{0,15}$")
ANNOUNCEMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PARAMS = {"zone", "brightness", "source", "announcement_id"}


def access(settings):
    # loopback only unless an access key is set in the environment (never in a file in git)
    host = settings.get("api.host", "127.0.0.1")
    key = os.environ.get(KEY_ENV, "")
    if key and len(key) < MIN_KEY_LEN:
        log.error(f"{KEY_ENV} is shorter than {MIN_KEY_LEN} characters, ignored")
        key = ""
    if host not in LOOPBACK and not key:
        log.error(f"api.host {host} needs {KEY_ENV}; serving on 127.0.0.1 only")
        host = "127.0.0.1"
    return host, key or None


def level_ok(v):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100


def start(commands, hw, runtime, cfg, port=5050, lux=None, ai=None):
    settings = cfg.load()
    host, key = access(settings)
    origins = set(settings.get("api.cors_origins", []))

    def zone_ok(z):
        return isinstance(z, str) and z in runtime.lights.zones

    def bad_params(p):
        # same rules for every caller; the mobile app and /command must not reach states voice cannot
        if not isinstance(p, dict) or set(p) - PARAMS:
            return "params"
        if "zone" in p and not zone_ok(p["zone"]):
            return "zone"
        if "brightness" in p and not level_ok(p["brightness"]):
            return "brightness"
        if "source" in p and not (isinstance(p["source"], str) and SOURCE.match(p["source"])):
            return "source"
        if "announcement_id" in p and not (isinstance(p["announcement_id"], str) and ANNOUNCEMENT.match(p["announcement_id"])):
            return "announcement_id"
        return None

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, fmt, *args):
            log.debug(fmt % args)

        def cors(self):
            # CORS only tells browsers which pages may read replies; it is not authentication
            origin = self.headers.get("Origin")
            if origin and origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def send(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.cors()
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())

        def fail(self, msg, code=400, detail=None, err="invalid_input"):
            self.send(code, {"ok": False, "error": {"code": err, "message": msg, "detail": detail or {}}})

        def invalid(self, field, reason="invalid"):
            self.fail(f"{field} is not valid.", detail={"field": field, "reason": reason})

        def authorized(self):
            if key is None:
                return True
            if hmac.compare_digest(self.headers.get("X-Room-Key", "").encode(), key.encode()):
                return True
            self.fail("X-Room-Key missing or wrong.", 401, err="unauthorized")
            return False

        def do_OPTIONS(self):
            self.send_response(204)
            if self.headers.get("Origin") in origins:
                self.cors()
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Room-Key")
            self.end_headers()

        def do_GET(self):
            if self.authorized():
                self.guard(self.get)

        def do_POST(self):
            if self.authorized():
                self.guard(self.post)

        def guard(self, fn):
            try:
                fn()
            except Exception as e:
                log.error(f"{self.command} {self.path} failed, {type(e).__name__}")
                self.fail("Unexpected server error.", 500, err="internal")

        def get(self):
            url = urlparse(self.path)
            path = url.path.rstrip("/")
            q = parse_qs(url.query)

            if path in ("", "/status", "/state"):
                data = {"ok": True, "device": cfg.load()["device_name"], **runtime.status()}
                if ai:
                    data["ai"] = ai.status()
                self.send(200, data)
            elif path == "/commands":
                self.send(200, {"ok": True, "commands": commands.list()})
            elif path == "/sensors":
                data = {"ok": True, "sensor": hw.read_sensors()}
                if lux:
                    data["ambient"] = lux.status()
                self.send(200, data)
            elif path == "/occupancy":
                if runtime.occupancy:
                    self.send(200, {"ok": True, **runtime.occupancy.status()})
                else:
                    self.fail("camera not available", 503)
            elif path == "/lights":
                self.send(200, {"ok": True, **runtime.lights.status()})
            elif path == "/decisions":
                self.send(
                    200,
                    {
                        "ok": True,
                        "decisions": runtime.decisions.query(
                            limit=int(q.get("limit", [50])[0]),
                            since=float(q["since"][0]) if "since" in q else None,
                            until=float(q["until"][0]) if "until" in q else None,
                            rule=q["rule"][0] if "rule" in q else None,
                        ),
                    },
                )
            elif path == "/recommendations":
                self.send(200, {"ok": True, "recommendations": getattr(runtime, "recommendations", [])})
            else:
                self.send(404, {"ok": False, "error": {"code": "not_found", "message": "not found", "detail": {}}})

        def post(self):
            path = urlparse(self.path).path.rstrip("/")
            if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                self.invalid("Content-Type", "must be application/json")      # also stops cross-site form posts
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self.invalid("Content-Length")
                return
            if size < 0 or size > MAX_BODY:
                self.invalid("body", "too_large")
                return
            try:
                body = json.loads(self.rfile.read(size)) if size else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.fail("invalid json", detail={"field": "body", "reason": "json"})
                return
            if not isinstance(body, dict):
                self.invalid("body", "not_an_object")
                return

            if path == "/voice/ask":
                if ai is None:
                    down = {"components": [{"name": "voice", "status": "down"}]}
                    msg = "Requests cannot be understood right now, the voice stack is down."
                    self.send(503, {"ok": False, "error": {"code": "degraded", "message": msg, "detail": down}})
                elif set(body) - {"text", "request_id"}:
                    self.invalid("body", "unknown_field")
                else:
                    self.send(*ai.ask(body.get("text"), body.get("request_id")))

            elif path == "/command":
                intent = body.get("intent")
                if not isinstance(intent, str) or intent not in commands.handlers:
                    self.invalid("intent", "unknown")
                    return
                params = body.get("params", {})
                problem = bad_params(params)
                if problem:
                    self.invalid(problem)
                    return
                self.send(200, commands.run(intent, params))

            elif path == "/lights/zone":
                if not zone_ok(body.get("zone")):
                    self.invalid("zone")
                elif not level_ok(body.get("brightness")):
                    self.invalid("brightness", "integer 0 to 100")
                elif bad_params({"source": body["source"]}) if "source" in body else False:
                    self.invalid("source")
                else:
                    runtime.override(body["zone"], body["brightness"], body.get("source", "app"))
                    self.send(200, {"ok": True, "zone": body["zone"], "brightness": body["brightness"]})

            elif path == "/lights/auto":
                if body.get("zone") is not None and not zone_ok(body["zone"]):
                    self.invalid("zone")
                else:
                    runtime.auto(body.get("zone"))
                    self.send(200, {"ok": True, "overrides": list(runtime.overrides)})

            elif path == "/presentation":
                if not isinstance(body.get("active"), bool):
                    self.invalid("active", "boolean")
                else:
                    runtime.presentation = body["active"]
                    self.send(200, {"ok": True, "presentation": runtime.presentation})

            elif path == "/config":
                if not isinstance(body.get("key"), str) or not body["key"]:
                    self.fail("key required")
                    return
                ok, msg = cfg.update(body["key"], body.get("value"))
                self.send(200, {"ok": True, "message": msg}) if ok else self.fail(msg)

            else:
                self.send(404, {"ok": False, "error": {"code": "not_found", "message": "not found", "detail": {}}})

    server = HTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(f"api listening on {host}:{server.server_port}" + (" with an access key" if key else " (local only, no key)"))
    return server
