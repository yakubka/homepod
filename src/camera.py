import logging
import random
import threading
import time

log = logging.getLogger("camera")

SIM = True


class Camera:
    def __init__(self, source=0, width=640, height=480, fps=10):
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps
        self.cap = None
        self.frame = None
        self.lock = threading.Lock()
        self.running = False

    def start(self):
        if SIM:
            self.running = True
            log.info("camera started in sim mode")
            return True

        import cv2
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(self.source)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        if not self.cap.isOpened():
            log.error("camera did not open")
            return False

        self.running = True

        def run():
            while self.running:
                ok, frame = self.cap.read()
                if ok:
                    with self.lock:
                        self.frame = frame
                time.sleep(1.0 / self.fps)

        threading.Thread(target=run, daemon=True).start()
        log.info(f"camera started {self.width}x{self.height} at {self.fps} fps")
        return True

    def read(self):
        if SIM:
            return {
                "sim": True,
                "person": random.random() > 0.3,
                "x": random.randint(50, self.width - 100),
                "y": random.randint(80, self.height - 200),
                "w": random.randint(80, 150),
                "h": random.randint(180, 300),
            }
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
        log.info("camera stopped")
