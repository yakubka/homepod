"""Engine lane. Turns voice and app requests into decision.action and device.command.

Rules here are the request path only, the room automation rules stay in
services/core/engine/rules.py until they move onto the bus.

Degraded answers, docs/degraded.md:
- ESP32 link down: lighting requests are rejected, questions are answered
  from the last reading and say how old it is.
- internet down, or general questions shed over budget: unknown intents get a
  spoken sentence instead of silence.
"""

import random

from services.core.logs import iso_utc, new_id
from services.core.service import Service

ZONES = ("left", "center", "right", "professor")
OFFLINE = "I can't answer general questions while the room is offline, but I can still help with the room."
BUSY = "I can't answer general questions right now, the room is busy. I can still help with the room."
GENERAL = "I can only help with the room for now."
NO_DEVICE = "I can't reach the lights right now."
PRESETS = {"lecture": (80, 80, 80, 100), "presentation": (30, 20, 30, 60), "off": (0, 0, 0, 0)}


class EngineService(Service):
    name = "engine"
    component = "engine"

    async def start(self):
        await super().start()
        self.inputs = self.bus.subscribe("voice.*", name="engine.voice", maxsize=128)
        self.controls = self.bus.subscribe("control.request", name="engine.control", maxsize=128)
        self.spawn(self.voice(), "voice")
        self.spawn(self.control(), "control")

    async def stop(self):
        self.inputs.close()
        self.controls.close()
        await super().stop()

    # ------------------------------------------------------------ helpers

    def device_up(self):
        link = self.state.get("device").value if self.state is not None else None
        return link is not None and link.status.value == "ok"

    def command(self, kind, **fields):
        return {"type": kind, "ts": iso_utc(), "cmd_id": f"c{random.getrandbits(32):08x}", **fields}

    def decide(self, envelope, rule, action, outcome, summary, commands=(), request_id=None, reply=None):
        trigger = "api" if envelope["topic"] == "control.request" else "voice"
        decision = {
            "id": new_id("dec"), "ts": iso_utc(),
            "trigger": {"type": trigger, "topic": envelope["topic"], "event_id": envelope["id"]},
            "rule": rule, "action": action, "outcome": outcome, "summary": summary[:200],
            "commands": [{"cmd_id": c["cmd_id"], "type": c["type"], "status": "pending"} for c in commands],
            "request_id": request_id, "reply_text": reply,
        }
        correlation = request_id or decision["id"]
        self.publish("decision.action", decision, correlation)
        for command in commands:
            self.publish("device.command", command, correlation)
        self.tick()
        return decision

    def ring(self, envelope, pattern, request_id):
        if not self.device_up():
            return
        command = self.command("set_ring", pattern=pattern, color=None, brightness=60)
        self.decide(envelope, "voice_feedback_ring", {"type": "ring.set", "zone": None, "params": {"pattern": pattern}},
                    "issued", f"Ring shows {pattern}.", [command], request_id)

    def lights(self, envelope, zone, brightness, fade_ms, rule, request_id, reply):
        action = {"type": "lighting.set_zone", "zone": zone, "params": {"brightness": brightness, "fade_ms": fade_ms}}
        if not self.device_up():
            return self.decide(envelope, rule, action, "rejected", "The lighting controller is offline.",
                               request_id=request_id, reply=NO_DEVICE)
        zones = [zone] if zone else list(ZONES)
        commands = [self.command("set_zone_light", zone=z, brightness=brightness, fade_ms=fade_ms) for z in zones]
        return self.decide(envelope, rule, action, "issued", f"Set {zone or 'every zone'} to {brightness} percent.",
                           commands, request_id, reply)

    def answer(self, envelope, rule, request_id, reply, outcome="answered"):
        return self.decide(envelope, rule, {"type": "answer", "zone": None, "params": {}}, outcome,
                           reply, request_id=request_id, reply=reply)

    # ------------------------------------------------------------ voice

    async def voice(self):
        async for envelope in self.inputs:
            payload, topic = envelope["payload"], envelope["topic"]
            if topic == "voice.wake":
                self.ring(envelope, "listening", payload["request_id"])
            elif topic == "voice.transcript" and payload["source"] == "microphone" and not payload["text"]:
                self.ring(envelope, "error", payload["request_id"])
            elif topic == "voice.intent":
                decision = self.on_intent(envelope, payload)
                if payload["source"] == "microphone":
                    self.ring(envelope, "success" if decision["outcome"] != "rejected" else "error", payload["request_id"])

    def on_intent(self, envelope, intent):
        name, request_id, zone = intent["intent"], intent["request_id"], intent["target"]["zone"]
        if name in ("lights_on", "lights_off", "set_brightness"):
            level = {"lights_on": 100, "lights_off": 0}.get(name, intent["params"].get("brightness"))
            return self.lights(envelope, zone, level, 400, f"voice_{name}", request_id,
                               f"{(zone or 'All').capitalize()} lights set to {level} percent.")
        if name in ("query_temperature", "query_humidity"):
            return self.answer(envelope, f"voice_{name}", request_id, self.reading(name))
        if name == "query_occupancy":
            return self.answer(envelope, "voice_query_occupancy", request_id, self.occupancy())
        if name == "query_lecture":
            return self.answer(envelope, "voice_query_lecture", request_id, "No lecture is scheduled right now.")
        if name == "play_announcement":
            announcement_id = intent["params"]["announcement_id"]
            return self.decide(envelope, "voice_play_announcement",
                               {"type": "announcement.play", "zone": None, "params": {"announcement_id": announcement_id, "volume": 70}},
                               "issued", f"Playing {announcement_id}.", request_id=request_id, reply="Playing the announcement.")
        if name in ("start_presentation", "stop_presentation"):
            return self.answer(envelope, f"voice_{name}", request_id, "Presentation mode is not available yet.", "rejected")
        return self.general(envelope, request_id)

    def general(self, envelope, request_id):
        degradations, scheduler = self.ctx.degradations, self.ctx.scheduler
        if degradations is not None and degradations.active("internet_unavailable"):
            return self.answer(envelope, "voice_general_offline", request_id, OFFLINE, "rejected")
        if scheduler is not None and not scheduler.admits("general"):
            return self.answer(envelope, "voice_general_shed", request_id, BUSY, "rejected")
        return self.answer(envelope, "voice_general", request_id, GENERAL)

    def reading(self, intent):
        field = self.state.get("environment") if self.state is not None else None
        if field is None or field.value is None:
            return "I don't have a reading yet."
        if intent == "query_temperature":
            value = f"{field.value.temperature_c} degrees"
        else:
            value = f"{field.value.humidity_pct} percent humidity"
        if field.stale:
            age = int(field.age_s)
            return f"The sensors are offline. The last reading, {age} second{'' if age == 1 else 's'} old, was {value}."
        return f"It is {value} in the room."

    def occupancy(self):
        field = self.state.get("occupancy") if self.state is not None else None
        if field is None or field.value is None:
            return "I can't tell how many people are here."
        if field.value.total is None:
            return "Someone is here, but I can't count right now." if field.value.occupied else "The room looks empty."
        return f"There are {field.value.total} people in the room."

    # ------------------------------------------------------------ app

    async def control(self):
        async for envelope in self.controls:
            request = envelope["payload"]
            action, request_id = request["action"], request["request_id"]
            kind, params = action["type"], action["params"]
            if kind == "lighting.set_zone":
                self.lights(envelope, action["zone"], params["brightness"], params["fade_ms"], "app_set_zone", request_id, None)
            elif kind == "lighting.set_mode" and params["mode"] in PRESETS:
                if not self.device_up():
                    self.decide(envelope, "app_set_mode", action, "rejected", "The lighting controller is offline.", request_id=request_id)
                    continue
                levels = PRESETS[params["mode"]]
                commands = [self.command("set_zone_light", zone=z, brightness=b, fade_ms=params["fade_ms"]) for z, b in zip(ZONES, levels)]
                self.decide(envelope, "app_set_mode", action, "issued", f"Lighting mode {params['mode']}.", commands, request_id)
            elif kind == "announcement.play":
                self.decide(envelope, "app_play_announcement", action, "issued", f"Playing {params['announcement_id']}.",
                            request_id=request_id)
            else:
                self.decide(envelope, "app_not_available", action, "rejected", f"{kind} is not available yet.", request_id=request_id)
