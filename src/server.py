import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

log = logging.getLogger("server")


def start(commands, hw, player, cfg, port=5050, lights=None, presence=None, lux=None):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, fmt, *args):
            log.debug(fmt % args)

        def send(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def fail(self, msg, code=400):
            self.send(code, {"ok": False, "error": msg})

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            path = self.path.rstrip("/")

            if path == "/status":
                self.send(200, {
                    "ok": True,
                    "device": cfg.load().get("device_name"),
                    "hardware": hw.status(),
                    "audio": player.status(),
                })
            elif path == "/commands":
                self.send(200, {"ok": True, "commands": commands.list()})
            elif path == "/sensor":
                data = {"ok": True, "sensor": hw.read_sensors()}
                if lux:
                    data["ambient"] = lux.status()
                self.send(200, data)
            elif path == "/audio":
                self.send(200, {"ok": True, **player.status()})
            elif path == "/presence":
                if presence:
                    self.send(200, {"ok": True, **presence.status()})
                else:
                    self.fail("camera not available", 503)
            elif path == "/lights":
                if lights:
                    self.send(200, {"ok": True, **lights.status()})
                else:
                    self.fail("lights not available", 503)
            elif path == "/ambient":
                if lux:
                    self.send(200, {"ok": True, **lux.status()})
                else:
                    self.fail("light sensor not available", 503)
            else:
                self.fail("not found", 404)

        def do_POST(self):
            path = self.path.rstrip("/")
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

            elif path == "/volume":
                if body.get("level") is None:
                    self.fail("level required")
                    return
                self.send(200, {"ok": True, "volume": player.set_volume(body["level"])})

            elif path == "/config":
                if not body.get("key"):
                    self.fail("key required")
                    return
                ok, msg = cfg.update(body["key"], body.get("value"))
                self.send(200, {"ok": True, "message": msg}) if ok else self.fail(msg)

            elif path == "/lights/zone":
                if not lights:
                    self.fail("lights not available", 503)
                elif body.get("zone") is None or body.get("state") is None:
                    self.fail("zone and state required")
                elif lights.set(body["zone"], bool(body["state"]), manual=True):
                    self.send(200, {"ok": True, "zone": body["zone"], "state": bool(body["state"])})
                else:
                    self.fail(f"unknown zone {body['zone']}")

            elif path == "/lights/follow":
                if not lights:
                    self.fail("lights not available", 503)
                elif body.get("enabled") is None:
                    self.fail("enabled required")
                else:
                    self.send(200, {"ok": True, "follow": lights.set_follow(bool(body["enabled"]))})

            else:
                self.fail("not found", 404)

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(f"api listening on {port}")
    return server
