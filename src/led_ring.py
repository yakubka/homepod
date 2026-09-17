import logging
import math
import threading
import time

log = logging.getLogger("ring")

SIM = True

COUNT = 12
PIN = 12
BRIGHTNESS = 0.4


class Ring:
    def __init__(self, count=COUNT, pin=PIN):
        self.count = count
        self.pixels = [(0, 0, 0)] * count
        self.strip = None
        self.running = False
        self.thread = None

        if not SIM:
            from rpi_ws281x import PixelStrip, Color
            self.color = Color
            self.strip = PixelStrip(count, pin, 800000, 10, False, int(BRIGHTNESS * 255), 0)
            self.strip.begin()

    def flush(self):
        if not self.strip:
            return
        for i, (r, g, b) in enumerate(self.pixels):
            self.strip.setPixelColor(i, self.color(r, g, b))
        self.strip.show()

    def fill(self, r, g, b):
        self.pixels = [(r, g, b)] * self.count
        self.flush()

    def off(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1)
        self.fill(0, 0, 0)

    def animate(self, frame_fn, fps=30):
        self.off()
        self.running = True

        def run():
            n = 0
            while self.running:
                frame_fn(n)
                self.flush()
                n += 1
                time.sleep(1.0 / fps)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def listening(self):
        def frame(n):
            head = n % self.count
            for i in range(self.count):
                d = min(abs(i - head), self.count - abs(i - head))
                v = max(0, 255 - d * 80)
                self.pixels[i] = (v, v, v)

        self.animate(frame, fps=15)
        log.info("ring listening")

    def thinking(self):
        def frame(n):
            v = int((math.sin(n * 0.15) + 1) * 100)
            self.pixels = [(0, 0, v)] * self.count

        self.animate(frame, fps=30)
        log.info("ring thinking")

    def success(self):
        def frame(n):
            if n < 10:
                v = int(n / 10 * 255)
            elif n < 25:
                v = int((1 - (n - 10) / 15) * 255)
            else:
                self.running = False
                v = 0
            self.pixels = [(0, v, 0)] * self.count

        self.animate(frame, fps=30)
        log.info("ring success")

    def error(self):
        def frame(n):
            if n > 30:
                self.running = False
                self.pixels = [(0, 0, 0)] * self.count
                return
            self.pixels = [(200, 0, 0) if (n // 5) % 2 == 0 else (0, 0, 0)] * self.count

        self.animate(frame, fps=15)
        log.info("ring error")

    def volume(self, level):
        self.off()
        filled = int(self.count * level / 100)
        self.pixels = [
            (255, 140, 0) if i < filled else (10, 10, 10) for i in range(self.count)
        ]
        self.flush()
        log.info(f"ring volume {level}")

    def status(self):
        return {"count": self.count, "animating": self.running, "sim": SIM}
