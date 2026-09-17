import logging

log = logging.getLogger("detector")

SIM = True

PERSON_CLASS = 15
MIN_CONF = 0.5
PROTOTXT = "models/MobileNetSSD_deploy.prototxt"
WEIGHTS = "models/MobileNetSSD_deploy.caffemodel"
ZONES = ["left", "center", "right"]


class Detector:
    def __init__(self, width=640, zones=3):
        self.width = width
        self.zones = zones
        self.zone_width = width // zones
        self.net = None
        self.found = []
        self.zone = None

        if not SIM:
            import cv2
            self.cv2 = cv2
            self.net = cv2.dnn.readNetFromCaffe(PROTOTXT, WEIGHTS)
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            log.info("mobilenet ssd loaded")

    def zone_of(self, x):
        i = min(x // self.zone_width, self.zones - 1)
        return ZONES[i] if i < len(ZONES) else f"zone{i}"

    def detect(self, frame):
        self.found = []
        self.zone = None

        if frame is None:
            return self.found

        try:
            if SIM:
                if frame.get("person"):
                    cx = frame["x"] + frame["w"] // 2
                    self.found = [{
                        "x": frame["x"], "y": frame["y"],
                        "w": frame["w"], "h": frame["h"],
                        "conf": 0.85, "cx": cx, "zone": self.zone_of(cx),
                    }]
            else:
                h, w = frame.shape[:2]
                blob = self.cv2.dnn.blobFromImage(
                    self.cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5
                )
                self.net.setInput(blob)
                out = self.net.forward()

                for i in range(out.shape[2]):
                    if int(out[0, 0, i, 1]) != PERSON_CLASS:
                        continue
                    conf = float(out[0, 0, i, 2])
                    if conf < MIN_CONF:
                        continue
                    x1, y1, x2, y2 = (out[0, 0, i, 3:7] * [w, h, w, h]).astype(int)
                    cx = (x1 + x2) // 2
                    self.found.append({
                        "x": int(x1), "y": int(y1),
                        "w": int(x2 - x1), "h": int(y2 - y1),
                        "conf": round(conf, 2), "cx": int(cx),
                        "zone": self.zone_of(cx),
                    })
        except Exception as e:
            log.error(f"detection failed, {e}")
            return []

        if self.found:
            self.zone = self.found[0]["zone"]
        return self.found

    def status(self):
        return {
            "person": bool(self.found),
            "zone": self.zone,
            "count": len(self.found),
            "found": self.found,
        }
