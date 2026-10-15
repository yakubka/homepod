import logging

log = logging.getLogger("commands")

ZONE_WORDS = {
    "left": "on the left",
    "center": "in the middle",
    "right": "on the right",
    "professor": "at the front",
}


class Commands:
    def __init__(self, hw, runtime, cfg, display=None, ring=None, lux=None, announcer=None):
        self.hw = hw
        self.rt = runtime
        self.cfg = cfg
        self.display = display
        self.ring = ring
        self.lux = lux
        self.announcer = announcer
        self.handlers = {
            "lights_on": self.lights_on,
            "lights_off": self.lights_off,
            "set_brightness": self.set_brightness,
            "query_temperature": self.temperature,
            "query_humidity": self.humidity,
            "query_occupancy": self.occupancy,
            "query_lecture": self.lecture,
            "start_presentation": self.start_presentation,
            "stop_presentation": self.stop_presentation,
            "play_announcement": self.announce,
        }

    def run(self, intent, params=None):
        handler = self.handlers.get(intent)
        if not handler:
            log.warning(f"unknown intent {intent}")
            return {"ok": False, "intent": intent, "say": "I did not understand that"}

        log.info(f"running {intent} with {params or {}}")
        self.hw.blink("listening", times=2)
        if self.ring:
            self.ring.thinking()
        if self.display:
            self.display.listening()

        try:
            result = handler(params or {})
            if self.ring:
                self.ring.success()
            if self.display:
                self.display.message(result.get("say", ""))
            return {"ok": True, "intent": intent, **result}
        except Exception as e:
            log.error(f"{intent} failed, {e}")
            self.hw.blink("error", times=3)
            if self.ring:
                self.ring.error()
            return {"ok": False, "intent": intent, "say": "Something went wrong", "error": str(e)}

    def _zones(self, p):
        zone = p.get("zone")
        return [zone] if zone else list(self.rt.lights.zones)

    def lights_on(self, p):
        level = int(p.get("brightness", 100))
        for z in self._zones(p):
            self.rt.override(z, level, p.get("source", "voice"))
        where = ZONE_WORDS.get(p.get("zone"), "everywhere")
        return {"say": f"Lights on {where}"}

    def lights_off(self, p):
        for z in self._zones(p):
            self.rt.override(z, 0, p.get("source", "voice"))
        where = ZONE_WORDS.get(p.get("zone"), "everywhere")
        return {"say": f"Lights off {where}"}

    def set_brightness(self, p):
        level = int(p.get("brightness", 50))
        for z in self._zones(p):
            self.rt.override(z, level, p.get("source", "voice"))
        return {"say": f"Brightness {level} percent"}

    def temperature(self, p):
        t = self.hw.read_sensors().get("temperature")
        return {"say": f"The room is {t} degrees" if t is not None else "No temperature reading"}

    def humidity(self, p):
        h = self.hw.read_sensors().get("humidity")
        return {"say": f"Humidity is {h} percent" if h is not None else "No humidity reading"}

    def occupancy(self, p):
        if not self.rt.occupancy:
            return {"say": "The camera is not available"}
        s = self.rt.occupancy.status()
        if s["total"] is None:
            return {"say": "I cannot count the room right now", "data": s}
        if not s["total"]:
            return {"say": "The room is empty", "data": s}
        busiest = max(s["zones"], key=lambda z: s["zones"][z])
        return {
            "say": f"{s['total']} students, most of them {ZONE_WORDS.get(busiest, busiest)}",
            "data": s,
        }

    def lecture(self, p):
        lec = self.rt.lecture
        if not lec:
            return {"say": "No lecture is scheduled"}
        return {"say": f"{lec.name}, the room is {self.rt.room.replace('_', ' ')}", "data": {"room": self.rt.room}}

    def start_presentation(self, p):
        self.rt.presentation = True
        return {"say": "Presentation mode on"}

    def stop_presentation(self, p):
        self.rt.presentation = False
        return {"say": "Presentation mode off"}

    def announce(self, p):
        name = p.get("announcement", "professor_absent")
        if not self.announcer:
            return {"say": "The speaker is not available"}
        self.announcer(name, p)
        return {"say": "Playing the announcement"}

    def list(self):
        return list(self.handlers)
