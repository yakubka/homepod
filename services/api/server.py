import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("server")


def start(commands, hw, runtime, cfg, port=5050, lux=None):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, fmt, *args):
            log.debug(fmt % args)

        def send(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode())

        def fail(self, msg, code=400):
            self.send(code, {"ok": False, "error": {"code": "invalid_input", "message": msg}})

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path.rstrip("/")
            q = parse_qs(url.query)

            if path in ("", "/status", "/state"):
                self.send(200, {"ok": True, "device": cfg.load()["device_name"], **runtime.status()})
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
                self.send(404, {"ok": False, "error": {"code": "not_found", "message": "not found"}})

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            size = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(size)) if size else {}
            except json.JSONDecodeError:
                self.fail("invalid json")
                return

            if path == "/command":
                if not body.get("intent"):
                    self.fail("intent required")
                    return
                self.send(200, commands.run(body["intent"], body.get("params", {})))

            elif path == "/lights/zone":
                if body.get("zone") is None or body.get("brightness") is None:
                    self.fail("zone and brightness required")
                elif body["zone"] not in runtime.lights.zones:
                    self.fail(f"unknown zone {body['zone']}")
                else:
                    runtime.override(body["zone"], body["brightness"], body.get("source", "app"))
                    self.send(200, {"ok": True, "zone": body["zone"], "brightness": body["brightness"]})

            elif path == "/lights/auto":
                runtime.auto(body.get("zone"))
                self.send(200, {"ok": True, "overrides": list(runtime.overrides)})

            elif path == "/presentation":
                if body.get("active") is None:
                    self.fail("active required")
                else:
                    runtime.presentation = bool(body["active"])
                    self.send(200, {"ok": True, "presentation": runtime.presentation})

            elif path == "/config":
                if not body.get("key"):
                    self.fail("key required")
                    return
                ok, msg = cfg.update(body["key"], body.get("value"))
                self.send(200, {"ok": True, "message": msg}) if ok else self.fail(msg)

            else:
                self.send(404, {"ok": False, "error": {"code": "not_found", "message": "not found"}})

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(f"api listening on {port}")
    return server
