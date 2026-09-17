import datetime
import logging
import threading

log = logging.getLogger("commands")

ZONE_WORDS = {"left": "on the left", "center": "in the middle", "right": "on the right"}


class Commands:
    def __init__(self, hw, player, cfg, display=None, ring=None, servo=None,
                 lights=None, presence=None, lux=None):
        self.hw = hw
        self.player = player
        self.cfg = cfg
        self.display = display
        self.ring = ring
        self.servo = servo
        self.lights = lights
        self.presence = presence
        self.lux = lux
        self.handlers = {
            "play_music": self.play,
            "stop_music": self.stop,
            "next_track": self.next,
            "prev_track": self.prev,
            "set_volume": self.volume,
            "get_time": self.time,
            "get_weather": self.weather,
            "get_temperature": self.temperature,
            "set_timer": self.timer,
            "device_status": self.status,
            "turn_on_light": self.light_on,
            "turn_off_light": self.light_off,
            "lights_all_on": self.all_on,
            "lights_all_off": self.all_off,
            "follow_mode_on": self.follow_on,
            "follow_mode_off": self.follow_off,
            "is_anyone_home": self.anyone_home,
            "get_brightness": self.brightness,
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

    def play(self, p):
        if not self.player.play(p.get("track")):
            return {"say": "No tracks available"}
        if self.display:
            self.display.now_playing(self.player.track, self.player.volume)
        return {"say": f"Playing {self.player.track}"}

    def stop(self, p):
        self.player.stop()
        return {"say": "Playback stopped"}

    def next(self, p):
        self.player.next()
        return {"say": f"Next track, {self.player.track or 'none'}"}

    def prev(self, p):
        self.player.prev()
        return {"say": f"Previous track, {self.player.track or 'none'}"}

    def volume(self, p):
        level = self.player.set_volume(p.get("level", 50))
        if self.ring:
            self.ring.volume(level)
        if self.servo:
            self.servo.show_volume(level)
        return {"say": f"Volume {level} percent"}

    def time(self, p):
        return {"say": f"It is {datetime.datetime.now().strftime('%H:%M')}"}

    def weather(self, p):
        s = self.hw.read_sensors()
        say = f"Room is {s['temperature']} degrees, humidity {s['humidity']} percent"
        if self.lux:
            say += f", light is {self.lux.level()}"
        return {"say": say}

    def temperature(self, p):
        return {"say": f"Temperature is {self.hw.read_sensors()['temperature']} degrees"}

    def timer(self, p):
        minutes = int(p.get("minutes", 5))

        def done():
            self.hw.blink("status", times=10, interval=0.5)
            if self.ring:
                self.ring.error()
            if self.display:
                self.display.show("", f"  Timer {minutes}m", "  done")
            log.info(f"timer {minutes}m finished")

        t = threading.Timer(minutes * 60, done)
        t.daemon = True
        t.start()
        return {"say": f"Timer set for {minutes} minutes"}

    def status(self, p):
        data = {**self.hw.status(), "audio": self.player.status()}
        if self.presence:
            data["presence"] = self.presence.status()
        return {"say": "Device status ready", "data": data}

    def light_on(self, p):
        zone = p.get("zone")
        if zone and self.lights:
            self.lights.set(zone, True, manual=True)
            return {"say": f"Light {ZONE_WORDS.get(zone, zone)} is on"}
        self.hw.set_led("status", True)
        return {"say": "Light is on"}

    def light_off(self, p):
        zone = p.get("zone")
        if zone and self.lights:
            self.lights.set(zone, False, manual=True)
            return {"say": f"Light {ZONE_WORDS.get(zone, zone)} is off"}
        self.hw.set_led("status", False)
        return {"say": "Light is off"}

    def all_on(self, p):
        if not self.lights:
            return {"say": "Lights are not available"}
        self.lights.all(True)
        return {"say": "All lights are on"}

    def all_off(self, p):
        if not self.lights:
            return {"say": "Lights are not available"}
        self.lights.all(False)
        return {"say": "All lights are off"}

    def follow_on(self, p):
        if not self.lights:
            return {"say": "Lights are not available"}
        self.lights.set_follow(True)
        return {"say": "Follow mode on, light will move with you"}

    def follow_off(self, p):
        if not self.lights:
            return {"say": "Lights are not available"}
        self.lights.set_follow(False)
        return {"say": "Follow mode off"}

    def anyone_home(self, p):
        if not self.presence:
            return {"say": "Camera is not available"}
        s = self.presence.status()
        if s["present"]:
            return {"say": f"Yes, someone is {ZONE_WORDS.get(s['zone'], s['zone'])}", "data": s}
        if s["empty_for"]:
            return {"say": f"Room has been empty for {s['empty_for']} seconds", "data": s}
        return {"say": "Room is empty", "data": s}

    def brightness(self, p):
        if not self.lux:
            return {"say": "Light sensor is not available"}
        s = self.lux.status()
        return {"say": f"Room is {s['level']}, {s['lux']} lux", "data": s}

    def list(self):
        return list(self.handlers)
