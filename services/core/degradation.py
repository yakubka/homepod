"""Degraded modes in force, and the faults chaos testing injects.

Degradations are what the room knows about itself, reported on /health and
read by services to pick their fallback. Faults are only ever set by
tools/chaos.py through the API, with VESTA_CHAOS=1, to cut a link on purpose.
docs/degraded.md is the matrix of what each degradation does.
"""

import logging
import threading
import time

from services.core.logs import iso_utc

log = logging.getLogger("degraded")

FLAGS = (
    "device_link_lost",
    "vision_unavailable",
    "stt_unavailable",
    "internet_unavailable",
    "over_budget",
    "service_failed",
)
LINKS = ("esp32", "camera", "stt", "internet", "budget")


class Degradations:
    def __init__(self):
        self.flags = {}
        self.lock = threading.Lock()

    def set(self, flag, detail=None):
        if flag not in FLAGS:
            raise ValueError(f"unknown degradation {flag!r}")
        with self.lock:
            entry = self.flags.get(flag)
            if entry is None:
                self.flags[flag] = {"since": iso_utc(), "began": time.monotonic(), "detail": detail}
                log.warning("degraded mode on", extra={"flag": flag, "detail": detail})
            else:
                entry["detail"] = detail

    def clear(self, flag):
        with self.lock:
            entry = self.flags.pop(flag, None)
        if entry is not None:
            log.info("degraded mode off", extra={"flag": flag, "lasted_s": round(time.monotonic() - entry["began"], 1)})

    def active(self, flag):
        return flag in self.flags

    def list(self):
        with self.lock:
            return [
                {"flag": flag, "since": self.flags[flag]["since"], "detail": self.flags[flag]["detail"]}
                for flag in FLAGS
                if flag in self.flags
            ]


class Faults:
    def __init__(self):
        self.cut_links = set()

    def cut(self, link):
        if link not in LINKS:
            raise ValueError(f"unknown link {link!r}, expected one of {', '.join(LINKS)}")
        self.cut_links.add(link)
        log.warning("fault injected", extra={"link": link})

    def restore(self, link):
        self.cut_links.discard(link)
        log.info("fault removed", extra={"link": link})

    def active(self, link):
        return link in self.cut_links
