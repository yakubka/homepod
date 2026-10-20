"""Voice lane, V-25. Turns speech and app text into voice.intent, speaks replies.

The intent parser here covers the sprint intent set from contracts/bus.md with
keywords, so the room answers before the trained parser lands.

Degraded modes, docs/degraded.md:
- speech to text down or too slow: the wake word still lights the ring, the
  assistant speaks FALLBACK_STT and publishes an empty transcript, which ends
  the interaction instead of leaving it hanging.
- internet down: tracked as internet_unavailable, local intents keep working,
  the engine answers general questions with a spoken sentence.
- announcements are spoken with purpose announcement, which is never shed.
"""

import asyncio
import re
import socket

from services.core import mode
from services.core.logs import iso_utc, new_request_id, trace
from services.core.replies import Replies
from services.core.service import Service
from services.voice.announcements import BY_ID

FALLBACK_STT = "Sorry, I can't understand speech right now. Please use the app."
ZONES = ("left", "center", "right", "professor")
NUMBERS = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
           "eighty": 80, "ninety": 90, "hundred": 100, "full": 100, "half": 50}
TARGETS = {
    "lights_on": ("set", "lighting"), "lights_off": ("set", "lighting"), "set_brightness": ("set", "lighting"),
    "query_temperature": ("query", "climate"), "query_humidity": ("query", "climate"),
    "query_occupancy": ("query", "occupancy"), "query_lecture": ("query", "lecture"),
    "start_presentation": ("start", "presentation"), "stop_presentation": ("stop", "presentation"),
    "play_announcement": ("play", "announcement"), "unknown": ("none", "none"),
}


class SttUnavailable(RuntimeError):
    pass


def answer_key(decision):
    """The decision that answers a request, never the ring feedback that shares its request id."""
    return None if decision["action"]["type"] == "ring.set" else decision["request_id"]


def parse(text):
    words = text.lower()
    zone = next((z for z in ZONES if z in words), None)
    params = {}
    if any(k in words for k in ("temperature", "how warm", "how hot", "how cold")):
        intent = "query_temperature"
    elif "humid" in words:
        intent = "query_humidity"
    elif any(k in words for k in ("how many", "people", "anyone", "occupied")):
        intent = "query_occupancy"
    elif any(k in words for k in ("lecture", "next class", "which class")):
        intent = "query_lecture"
    elif "presentation" in words:
        intent = "stop_presentation" if any(k in words for k in ("stop", "end", "finish")) else "start_presentation"
    elif "announce" in words or "break" in words or "silent" in words:
        intent = "play_announcement"
        params["announcement_id"] = "ann_break10" if "break" in words else "ann_phones"
    elif "light" in words or "lamp" in words:
        number = re.search(r"\b(\d{1,3})\s*(%|percent)", words)
        level = int(number.group(1)) if number else next((v for k, v in NUMBERS.items() if f"{k} percent" in words), None)
        if level is not None and 0 <= level <= 100:
            intent, params["brightness"] = "set_brightness", level
        elif " off" in f" {words}" or "dark" in words:
            intent = "lights_off"
        else:
            intent = "lights_on"
    else:
        intent = "unknown"
    action, target = TARGETS[intent]
    if target in ("climate", "lecture", "presentation", "announcement", "none"):
        zone = None
    return intent, action, {"type": target, "zone": zone}, params


class ScriptedStt:
    """Replay and sim: the utterance arrives as text, as if transcribed."""

    def __init__(self, faults):
        self.faults = faults

    def transcribe(self, utterance):
        if self.faults is not None and self.faults.active("stt"):
            raise SttUnavailable("speech to text not responding")
        return utterance


class ModelStt:
    def __init__(self, models, faults):
        self.models = models
        self.faults = faults

    def transcribe(self, audio):
        if self.faults is not None and self.faults.active("stt"):
            raise SttUnavailable("speech to text not responding")
        try:
            return self.models.infer("stt", audio)
        except Exception as e:
            raise SttUnavailable(f"speech to text failed: {e!r}") from e


class VoiceService(Service):
    name = "voice"
    component = "voice"

    async def start(self):
        self.stt = mode.pick(
            replay=lambda: ScriptedStt(self.ctx.faults),
            sim=lambda: ScriptedStt(self.ctx.faults),
            real=lambda: ModelStt(self.ctx.models, self.ctx.faults),
        )
        self.spoken = []
        await super().start()
        self.transcripts = self.bus.subscribe("voice.transcript", name="voice.transcripts", maxsize=64)
        self.decisions = Replies(self.bus, "decision.action", key=answer_key, name="voice.decisions")
        self.announcements = self.bus.subscribe("decision.action", name="voice.announcements", maxsize=64)
        self.spawn(self.decisions.run(), "decisions")
        self.spawn(self.app_requests(), "app")
        self.spawn(self.speak_announcements(), "announcements")
        self.spawn(self.every(self.ctx.config["internet.probe_interval"], self.probe_internet), "internet")

    async def app_requests(self):
        async for envelope in self.transcripts:
            payload = envelope["payload"]
            if payload["source"] == "app":
                self.publish_intent(payload["request_id"], payload["text"], "app")

    def publish_intent(self, request_id, text, source):
        intent, action, target, params = parse(text)
        self.publish("voice.intent", {
            "request_id": request_id, "intent": intent, "action": action, "target": target, "params": params,
            "confidence": 1.0 if source == "app" else 0.9, "raw_text": text[:200], "source": source,
        }, request_id)

    async def utterance(self, audio):
        """One microphone interaction after the wake word. Returns what was spoken."""
        request_id = new_request_id()
        with trace(request_id):
            self.publish("voice.wake", {"request_id": request_id, "ts": iso_utc(), "wake_word": "hey_jarvis", "confidence": 0.9})
            try:
                text = await asyncio.wait_for(asyncio.to_thread(self.stt.transcribe, audio), self.ctx.config["voice.stt_timeout"])
            except (SttUnavailable, asyncio.TimeoutError) as e:
                self.degrade("stt_unavailable", str(e) or "speech to text timed out")
                self.publish_transcript(request_id, "")
                await self.speak(FALLBACK_STT)
                return {"request_id": request_id, "outcome": "stt_unavailable", "spoken": FALLBACK_STT}
            self.recover("stt_unavailable")
            self.publish_transcript(request_id, text)
            pending = self.decisions.expect(request_id)
            self.publish_intent(request_id, text, "microphone")
            try:
                decision = await self.decisions.wait(pending, self.ctx.config["api.reply_timeout"])
            except asyncio.TimeoutError:
                await self.speak(FALLBACK_STT)
                return {"request_id": request_id, "outcome": "no_decision", "spoken": FALLBACK_STT}
            reply = decision["reply_text"] or "Done."
            await self.speak(reply)
            return {"request_id": request_id, "outcome": decision["outcome"], "spoken": reply}

    def publish_transcript(self, request_id, text):
        self.publish("voice.transcript", {
            "request_id": request_id, "ts": iso_utc(), "text": text[:200], "confidence": 0.9 if text else 0.0,
            "language": "en-US", "source": "microphone", "duration_ms": None,
        }, request_id)

    async def speak(self, text, purpose="voice"):
        self.spoken.append(text)
        self.log.info("spoke", extra={"text": text, "purpose": purpose})
        if self.ctx.mode == "real" and self.ctx.models is not None:
            await asyncio.to_thread(self.ctx.models.infer, "tts", text, purpose=purpose)

    async def speak_announcements(self):
        async for envelope in self.announcements:
            action = envelope["payload"]["action"]
            if action["type"] == "announcement.play" and envelope["payload"]["outcome"] == "issued":
                item = BY_ID.get(action["params"].get("announcement_id"))
                if item is not None:
                    await self.speak(item["text"], purpose="announcement")

    async def probe_internet(self):
        online = not self.fault("internet")
        if online and self.ctx.mode == "real":
            online = await asyncio.to_thread(self._reachable, self.ctx.config["internet.probe_host"])
        if online:
            self.recover("internet_unavailable")
        else:
            self.degrade("internet_unavailable", "no route to the internet")

    @staticmethod
    def _reachable(host):
        try:
            socket.create_connection((host, 53), timeout=2).close()
            return True
        except OSError:
            return False

    async def stop(self):
        self.transcripts.close()
        self.announcements.close()
        await super().stop()
