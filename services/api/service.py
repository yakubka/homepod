"""API lane, V-42. HTTP on api.port inside the core process, paths from contracts/openapi.yaml.

Served here: GET /health, /sensors, /occupancy, /announcements, POST /lighting/zone,
/voice/ask and /announcements/{id}/play. The rest of the contract lands with V-42.

With VESTA_CHAOS=1 the chaos hooks used by tools/chaos.py are mounted too:
POST /chaos/kill/{service}, /chaos/cut/{link}, /chaos/restore/{link} and
/chaos/utterance. They are not part of the contract and never on in a demo.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

from services.core import config, contract
from services.core.logs import iso_utc, new_id, new_request_id, trace
from services.core.replies import Replies
from services.core.service import Service
from services.voice.announcements import ANNOUNCEMENTS, BY_ID
from services.voice.service import answer_key

PREFIX = "/api/v1"
STATUS = {"invalid_input": 400, "unauthorized": 401, "not_found": 404, "conflict": 409, "internal": 500,
          "device_unavailable": 503, "degraded": 503, "device_timeout": 504}
REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 409: "Conflict",
           500: "Internal Server Error", 503: "Service Unavailable", 504: "Gateway Timeout"}
CRITICAL = {"api", "bus", "engine", "bridge"}


class Failure(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


class ApiService(Service):
    name = "api"
    component = "api"
    version = "0.4.0"
    server = None

    async def start(self):
        cfg = self.ctx.config
        self.started = time.monotonic()
        self.playing_until = 0.0
        await super().start()
        self.decisions = Replies(self.bus, "decision.action", key=answer_key, name="api.decisions")
        self.intents = Replies(self.bus, "voice.intent", key=lambda p: p["request_id"], name="api.intents")
        self.acks = Replies(self.bus, "device.ack", key=lambda p: p["cmd_id"], name="api.acks")
        for replies in (self.decisions, self.intents, self.acks):
            self.spawn(replies.run(), replies.sub.name)
        self.server = await asyncio.start_server(self.handle, cfg["api.host"], cfg["api.port"])
        self.log.info("api listening", extra={"host": cfg["api.host"], "port": cfg["api.port"], "chaos": config.CHAOS})

    async def stop(self):
        self.server.close()
        await super().stop()

    # ------------------------------------------------------------ http

    async def handle(self, reader, writer):
        status, body = 500, None
        try:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1").strip()
            method, target = line.split(" ")[:2]
            headers = {}
            while True:
                raw = await reader.readline()
                if raw in (b"\r\n", b"\n", b""):
                    break
                key, _, value = raw.decode("latin-1").partition(":")
                headers[key.strip().lower()] = value.strip()
            payload = await reader.readexactly(int(headers.get("content-length") or 0))
            with trace():
                status, body = await self.route(method, target.split("?")[0], headers, payload)
        except Failure as e:
            status, body = STATUS[e.code], {"ok": False, "error": {"code": e.code, "message": e.message, "detail": e.detail}}
        except (ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            status, body = 400, {"ok": False, "error": {"code": "invalid_input", "message": "Malformed HTTP request.", "detail": {}}}
        except Exception:
            self.log.exception("request failed")
            status, body = 500, {"ok": False, "error": {"code": "internal", "message": "Unexpected server error.", "detail": {}}}
        data = json.dumps(body).encode()
        writer.write(f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
        try:
            await writer.drain()
        finally:
            writer.close()

    async def route(self, method, path, headers, payload):
        if not path.startswith(PREFIX):
            raise Failure("not_found", f"No route {path}.", {"resource": "route", "id": path})
        parts = path[len(PREFIX):].strip("/").split("/")
        if (method, parts) == ("GET", ["health"]):
            return self.health_response()
        if headers.get("x-room-key") != self.ctx.config["api.key"]:
            raise Failure("unauthorized", "X-Room-Key header is missing or wrong.")
        body = self.json(payload)
        if config.CHAOS and parts[0] == "chaos" and method == "POST":
            return 200, {"ok": True, "data": await self.chaos(parts[1:], body)}
        routes = {
            ("GET", "sensors"): self.sensors,
            ("GET", "occupancy"): self.occupancy,
            ("GET", "announcements"): self.announcements,
            ("POST", "lighting/zone"): self.zone,
            ("POST", "voice/ask"): self.ask,
        }
        handler = routes.get((method, "/".join(parts)))
        if handler is not None:
            return 200, {"ok": True, "data": await handler(body)}
        if method == "POST" and len(parts) == 3 and parts[0] == "announcements" and parts[2] == "play":
            return 200, {"ok": True, "data": await self.play(parts[1], body)}
        raise Failure("not_found", f"No route {method} {path}.", {"resource": "route", "id": path})

    @staticmethod
    def json(payload):
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            raise Failure("invalid_input", "Body is not valid JSON.", {"field": None, "reason": "invalid_json"}) from None

    @staticmethod
    def check(schema, body):
        errors = contract.schema_errors(schema, body)
        if errors:
            field, _, reason = errors[0].partition(": ")
            raise Failure("invalid_input", errors[0][:300], {"field": None if field == "$" else field, "reason": reason[:100]})

    def last(self, topic):
        envelope = self.bus.last_value(topic)
        return envelope["payload"] if envelope else None

    # ------------------------------------------------------------ health

    def health(self):
        health = super().health()
        if health["ok"] and self.server is not None and not self.server.is_serving():
            return {"ok": False, "detail": "http server not serving", "last_tick": self.last_tick}
        return health

    def health_response(self):
        components = self.ctx.supervisor.report() if self.ctx.supervisor else []
        link = self.last("device.status")
        components.append({
            "name": "esp32", "status": "ok" if link and link["status"] == "ok" else "down" if link else "degraded",
            "last_seen_at": link["last_seen_at"] if link else None,
            "detail": None if link and link["status"] == "ok" else (link["reason"] if link else "no device status yet"),
            "uptime_s": None, "restarts": 0,
        })
        components.append({"name": "bus", "status": "ok", "last_seen_at": iso_utc(), "detail": None,
                           "uptime_s": int(time.monotonic() - self.started), "restarts": 0})
        degradations = self.ctx.degradations.list() if self.ctx.degradations else []
        if any(c["status"] == "down" and c["name"] in CRITICAL for c in components):
            status = "down"
        elif degradations or any(c["status"] != "ok" for c in components):
            status = "degraded"
        else:
            status = "ok"
        data = {"status": status, "ts": iso_utc(), "service_version": self.version,
                "contract_version": contract.version(), "uptime_s": int(time.monotonic() - self.started),
                "components": components, "degradations": degradations}
        if status == "down":
            down = [{"name": c["name"], "status": "down"} for c in components if c["status"] == "down"]
            return 503, {"ok": False, "error": {"code": "degraded", "message": "A critical component is down.",
                                                "detail": {"components": down, "health": data}}}
        return 200, {"ok": True, "data": data}

    # ------------------------------------------------------------ room

    async def sensors(self, body):
        link = self.last("device.status") or {
            "device_id": None, "status": "down", "reason": "no_hello", "fw_version": None, "protocol_version": None,
            "uptime_ms": None, "last_seen_at": None, "last_error": None,
        }
        return {"environment": self.last("sensor.environment"), "presence": self.last("sensor.presence"), "device": link}

    async def occupancy(self, body):
        snapshot = self.last("perception.occupancy")
        if snapshot is None:
            raise Failure("degraded", "Occupancy is unknown, vision and presence sensors are both unavailable.",
                          {"components": [{"name": "vision", "status": "down"}]})
        return snapshot

    async def announcements(self, body):
        return [{**item, "last_played_at": None} for item in ANNOUNCEMENTS]

    def require_device(self):
        link = self.last("device.status")
        if not link or link["status"] != "ok":
            raise Failure("device_unavailable", "The room controller is offline.", {
                "device": link["device_id"] if link else None, "reason": "link_down",
                "last_seen_at": link["last_seen_at"] if link else None})

    async def request(self, action):
        request_id = new_request_id()
        pending = self.decisions.expect(request_id)
        self.publish("control.request", {"request_id": request_id, "ts": iso_utc(), "action": action}, request_id)
        try:
            return await self.decisions.wait(pending, self.ctx.config["api.reply_timeout"])
        except asyncio.TimeoutError:
            raise Failure("degraded", "The engine did not answer.", {"components": [{"name": "engine", "status": "down"}]}) from None

    async def zone(self, body):
        self.check("ZoneLightRequest", body)
        self.require_device()
        params = {"brightness": body["brightness"], "fade_ms": body.get("fade_ms", 400)}
        decision = await self.request({"type": "lighting.set_zone", "zone": body["zone"], "params": params})
        if decision["outcome"] == "rejected":
            raise Failure("device_unavailable", decision["summary"], {"device": self.ctx.config["device.id"], "reason": "rejected"})
        acks = []
        for command in decision["commands"]:
            try:
                acks.append(await self.acks.wait(self.acks.expect(command["cmd_id"]), 2.0))
            except asyncio.TimeoutError:
                raise Failure("device_timeout", "The room controller did not acknowledge the command.",
                              {"device": self.ctx.config["device.id"], "cmd_id": command["cmd_id"], "attempts": 3}) from None
        if any(ack["status"] != "applied" for ack in acks):
            raise Failure("device_unavailable", "The room controller rejected the command.",
                          {"device": self.ctx.config["device.id"], "reason": acks[-1]["reason"]})
        now = iso_utc()
        lights = acks[-1]["state"]["lights"]
        return {"mode": "manual", "zones": [{"zone": z, "brightness": lights[z], "confirmed": True, "updated_at": now} for z in lights],
                "ambient_lux": (self.last("sensor.environment") or {}).get("lux"), "updated_at": now}

    async def ask(self, body):
        self.check("VoiceAskRequest", body)
        request_id = body.get("request_id") or new_request_id()
        intent_pending, decision_pending = self.intents.expect(request_id), self.decisions.expect(request_id)
        self.publish("voice.transcript", {"request_id": request_id, "ts": iso_utc(), "text": body["text"], "confidence": 1.0,
                                          "language": "en-US", "source": "app", "duration_ms": None}, request_id)
        timeout = self.ctx.config["api.reply_timeout"]
        try:
            intent = await self.intents.wait(intent_pending, timeout)
            decision = await self.decisions.wait(decision_pending, timeout)
        except asyncio.TimeoutError:
            raise Failure("degraded", "Requests cannot be understood right now, the voice stack is down.",
                          {"components": [{"name": "voice", "status": "down"}]}) from None
        if intent["intent"] == "unknown":
            outcome = "rejected" if decision["outcome"] == "rejected" else "not_understood"
        else:
            outcome = {"issued": "executed", "answered": "answered"}.get(decision["outcome"], "rejected")
        return {"request_id": request_id, "command": intent, "outcome": outcome,
                "reply_text": decision["reply_text"] or decision["summary"], "decision_id": decision["id"]}

    async def play(self, announcement_id, body):
        self.check("PlayAnnouncementRequest", body)
        item = BY_ID.get(announcement_id)
        if item is None:
            raise Failure("not_found", f"Announcement {announcement_id} was not found.", {"resource": "announcement", "id": announcement_id})
        if time.monotonic() < self.playing_until:
            raise Failure("conflict", "Another announcement is playing.", {"resource": "playback", "conflicting_id": None})
        volume = body.get("volume", 70)
        decision = await self.request({"type": "announcement.play", "zone": None,
                                       "params": {"announcement_id": announcement_id, "volume": volume}})
        self.playing_until = time.monotonic() + item["duration_s"]
        started = datetime.now(timezone.utc)
        end = started + timedelta(seconds=item["duration_s"])
        return {"playback_id": new_id("pb"), "announcement_id": announcement_id, "decision_id": decision["id"],
                "started_at": iso_utc(started.timestamp()), "expected_end_at": iso_utc(end.timestamp()), "volume": volume}

    # ------------------------------------------------------------ chaos

    async def chaos(self, parts, body):
        supervisor, faults = self.ctx.supervisor, self.ctx.faults
        if len(parts) == 2 and parts[0] == "kill":
            service = supervisor.service(parts[1])
            if service is None:
                raise Failure("not_found", f"No service {parts[1]}.", {"resource": "service", "id": parts[1]})
            asyncio.get_running_loop().call_later(0.2, service.crash, "killed by chaos")
            return {"killed": parts[1]}
        if len(parts) == 2 and parts[0] in ("cut", "restore"):
            try:
                getattr(faults, parts[0])(parts[1])
            except ValueError as e:
                raise Failure("invalid_input", str(e), {"field": "link", "reason": "unknown"}) from None
            return {parts[0]: parts[1]}
        if parts == ["utterance"]:
            voice = supervisor.service("voice")
            return await voice.utterance(body.get("text", "what is the temperature"))
        raise Failure("not_found", "No such chaos action.", {"resource": "chaos", "id": "/".join(parts)})
