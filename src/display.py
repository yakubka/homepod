import logging
import threading
import time

log = logging.getLogger("display")

SIM = True

ADDR = 0x3C
WIDTH = 128
HEIGHT = 64
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


class Display:
    def __init__(self):
        self.lines = [""] * 4
        self.dev = None
        self.font = None
        self.running = False

        if not SIM:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            from PIL import ImageFont

            self.dev = ssd1306(i2c(port=1, address=ADDR))
            try:
                self.font = ImageFont.truetype(FONT_PATH, 12)
            except IOError:
                self.font = ImageFont.load_default()

    def show(self, l1="", l2="", l3="", l4=""):
        self.lines = [str(l1), str(l2), str(l3), str(l4)]

        if not SIM:
            from PIL import Image, ImageDraw
            img = Image.new("1", (WIDTH, HEIGHT), 0)
            draw = ImageDraw.Draw(img)
            for i, line in enumerate(self.lines):
                if line:
                    draw.text((2, i * 16), line, fill=1, font=self.font)
            self.dev.display(img)

        log.debug(f"display {self.lines}")

    def now_playing(self, track, volume):
        self.show("Now playing", (track or "none")[:20], f"Volume {volume}")

    def room(self, temp, humidity, name):
        self.show(name, f"Temp {temp} C", f"Humidity {humidity}", time.strftime("%H:%M:%S"))

    def listening(self):
        self.show("", "  Listening", "")

    def message(self, text):
        lines, cur = [], ""
        for word in text.split():
            if len(cur) + len(word) + 1 > 20:
                lines.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        if cur:
            lines.append(cur)
        self.show(*(lines + [""] * 4)[:4])

    def auto(self, hw, player, name="HomePod"):
        self.running = True

        def run():
            while self.running:
                state = player.status()
                if state["playing"]:
                    self.now_playing(state["track"], state["volume"])
                else:
                    self.room(
                        hw.sensors.get("temperature", 0),
                        hw.sensors.get("humidity", 0),
                        name,
                    )
                time.sleep(2)

        threading.Thread(target=run, daemon=True).start()
        log.info("auto display started")

    def stop(self):
        self.running = False
        self.show()
        if not SIM:
            self.dev.hide()
