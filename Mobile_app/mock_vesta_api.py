"""VESTA DEVELOPMENT-ONLY API MOCK. No real Raspberry Pi hardware control.

Run: py mock_vesta_api.py     (Windows)
Set emulator Settings: IP 10.0.2.2, port 8000.
Endpoints: GET /api/sensors; GET /api/lighting; POST /api/lighting.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

lighting = {
    "automatic": True,
    "on": True,
    "zones": {"left": 70, "center": 80, "right": 65},
}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/api/sensors":
            self.respond(200, {
                "temperature": 24.0,
                "humidity": 52,
                "light_level": 420,
                "students": 23,
                "mock": True,
            })
        elif self.path == "/api/lighting":
            self.respond(200, {"success": True, "lighting": lighting, "mock": True})
        else:
            self.respond(404, {"success": False, "error": "Not found"})

    def do_POST(self):
        if self.path != "/api/lighting":
            self.respond(404, {"success": False, "error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 4096:
                raise ValueError("Invalid body size")
            data = json.loads(self.rfile.read(size))
            action = data.get("action")

            if action == "set_mode" and type(data.get("automatic")) is bool:
                lighting["automatic"] = data["automatic"]
            elif action == "set_bulb" and type(data.get("on")) is bool:
                if lighting["automatic"]:
                    raise ValueError("Switch to manual lighting mode first")
                lighting["on"] = data["on"]
            elif action == "set_brightness":
                zone = data.get("zone")
                level = data.get("brightness")
                if lighting["automatic"]:
                    raise ValueError("Switch to manual lighting mode first")
                if zone not in lighting["zones"] or type(level) is not int or not 0 <= level <= 100:
                    raise ValueError("Invalid lighting zone or brightness")
                lighting["zones"][zone] = level
            else:
                raise ValueError("Unknown action or invalid payload")

            print("Lighting request accepted:", data, "->", lighting, flush=True)
            self.respond(200, {"success": True, "lighting": lighting, "mock": True})
        except (ValueError, json.JSONDecodeError, TypeError) as exc:
            self.respond(400, {"success": False, "error": str(exc)})


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8000), Handler)
    print("VESTA MOCK API listening at http://127.0.0.1:8000")
    print("Android emulator: use 10.0.2.2:8000 in Settings")
    print("MOCK ONLY: nothing here controls physical lights")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped")
    finally:
        server.server_close()
