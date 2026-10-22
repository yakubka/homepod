#!/usr/bin/env python3
"""Kill every service and cut every link of a running room, and time the recovery.

Starts its own core in replay mode with the chaos hooks on, walks the matrix in
docs/degraded.md row by row, checks each degraded behaviour through the HTTP
API, removes the failure and records how long detection and recovery took. Ends
by scanning the core log for unhandled exceptions, then writes
eval/reports/chaos.md. Exit code 1 when any row did not behave as documented.

Usage: python tools/chaos.py [--fast] [--out eval/reports/chaos.md]
  --fast  short supervisor, guard and probe intervals, for the test suite
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.core.engine.service import BUSY, OFFLINE  # noqa: E402
from services.voice.service import FALLBACK_STT  # noqa: E402

KEY = "chaos-key"
SERVICES = ["device", "recorder", "vision", "voice", "engine", "api"]
HEALTH_NAME = {"device": "bridge"}
FAST = {"VESTA_SUPERVISOR_POLL_INTERVAL": "0.5", "VESTA_SUPERVISOR_BACKOFF_BASE": "0.5", "VESTA_GUARD_INTERVAL": "0.5",
        "VESTA_GUARD_COOLDOWN": "2", "VESTA_INTERNET_PROBE_INTERVAL": "0.5", "VESTA_DEVICE_REPORT_INTERVAL": "1"}


class Room:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}/api/v1"

    def call(self, method, path, body=None, timeout=10):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"X-Room-Key": KEY, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
        except (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout):
            return None, None

    def health(self):
        status, body = self.call("GET", "/health")
        if status == 200:
            return body["data"]
        if status == 503:
            return body["error"]["detail"]["health"]
        return None

    def component(self, name):
        health = self.health()
        if health is None:
            return None
        return next((c for c in health["components"] if c["name"] == HEALTH_NAME.get(name, name)), None)

    def flags(self):
        health = self.health()
        return {d["flag"] for d in health["degradations"]} if health else set()

    def get(self, path):
        status, body = self.call("GET", path)
        return body["data"] if status == 200 else None


def wait(predicate, timeout, interval=0.1):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            if predicate():
                return round(time.monotonic() - started, 2)
        except (KeyError, TypeError):
            pass
        time.sleep(interval)
    return None


def row(failure, injected, expected):
    return {"failure": failure, "injected": injected, "expected": expected, "observed": [], "detect_s": None,
            "recover_s": None, "ok": True}


def check(result, condition, note):
    result["observed"].append(("ok " if condition else "FAILED ") + note)
    result["ok"] = result["ok"] and bool(condition)


def kill_service(room, name):
    result = row(f"{name} service crashes", f"POST /chaos/kill/{name}", "supervisor restarts it, health back to ok")
    before = room.component(name)["restarts"]
    started = time.monotonic()
    room.call("POST", f"/chaos/kill/{name}")
    result["detect_s"] = wait(lambda: (c := room.component(name)) is None or c["status"] != "ok" or c["restarts"] > before, 15)
    recovered = wait(lambda: (c := room.component(name)) is not None and c["status"] == "ok" and c["restarts"] == before + 1, 30)
    result["recover_s"] = round(time.monotonic() - started, 2) if recovered is not None else None
    check(result, result["detect_s"] is not None, "failure detected")
    check(result, recovered is not None, f"restart {before + 1}, healthy again")
    return result


def esp32(room):
    result = row("ESP32 link lost", "POST /chaos/cut/esp32", "sensors stale within 5 s, actuator calls 503 device_unavailable, local questions still answered")
    room.call("POST", "/chaos/cut/esp32")
    result["detect_s"] = wait(lambda: "device_link_lost" in room.flags() and room.get("/sensors")["device"]["status"] == "down", 10)
    check(result, result["detect_s"] is not None and result["detect_s"] <= 5, f"stale state shown in {result['detect_s']} s")
    status, body = room.call("POST", "/lighting/zone", {"zone": "left", "brightness": 80})
    check(result, status == 503 and body["error"]["code"] == "device_unavailable", f"lighting call answered {status} {body['error']['code'] if body and 'error' in body else ''}")
    status, body = room.call("POST", "/voice/ask", {"text": "what is the temperature"})
    reply = body["data"]["reply_text"] if status == 200 else ""
    check(result, "degrees" in reply and "offline" in reply, f"voice answered locally: \"{reply}\"")
    room.call("POST", "/chaos/restore/esp32")
    result["recover_s"] = wait(lambda: "device_link_lost" not in room.flags() and room.get("/sensors")["device"]["status"] == "ok", 15)
    status, _ = room.call("POST", "/lighting/zone", {"zone": "left", "brightness": 80})
    check(result, result["recover_s"] is not None and status == 200, "link back, lighting call answered 200")
    return result


def camera(room):
    result = row("Camera or detector dead", "POST /chaos/cut/camera", "occupancy from presence, source presence, every count null")
    room.call("POST", "/chaos/cut/camera")
    result["detect_s"] = wait(lambda: room.get("/occupancy")["source"] == "presence", 10)
    occupancy = room.get("/occupancy") or {}
    counts = [z["count"] for z in occupancy.get("zones", [])]
    check(result, result["detect_s"] is not None and occupancy.get("total") is None and counts and all(c is None for c in counts),
          f"source {occupancy.get('source')}, total {occupancy.get('total')}, counts {counts}")
    check(result, "vision_unavailable" in room.flags(), "vision_unavailable on /health")
    room.call("POST", "/chaos/restore/camera")
    result["recover_s"] = wait(lambda: room.get("/occupancy")["source"] == "vision" and "vision_unavailable" not in room.flags(), 15)
    check(result, result["recover_s"] is not None, "camera counts again")
    return result


def stt(room):
    result = row("Speech to text unavailable", "POST /chaos/cut/stt, then an utterance", "ring still lit by the wake word, fallback sentence spoken, interaction ends")
    room.call("POST", "/chaos/cut/stt")
    started = time.monotonic()
    status, body = room.call("POST", "/chaos/utterance", {"text": "turn on the left lights"}, timeout=20)
    result["detect_s"] = round(time.monotonic() - started, 2)
    spoken = body["data"]["spoken"] if status == 200 else None
    check(result, spoken == FALLBACK_STT, f"spoke \"{spoken}\" and ended in {result['detect_s']} s")
    check(result, "stt_unavailable" in room.flags(), "stt_unavailable on /health")
    room.call("POST", "/chaos/restore/stt")
    started = time.monotonic()
    status, body = room.call("POST", "/chaos/utterance", {"text": "turn on the left lights"}, timeout=20)
    result["recover_s"] = round(time.monotonic() - started, 2)
    spoken = body["data"]["spoken"] if status == 200 else None
    check(result, spoken and "lights" in spoken and "stt_unavailable" not in room.flags(), f"next utterance answered: \"{spoken}\"")
    return result


def internet(room):
    result = row("Internet lost", "POST /chaos/cut/internet", "local intents work, general questions answered with a spoken sentence")
    room.call("POST", "/chaos/cut/internet")
    result["detect_s"] = wait(lambda: "internet_unavailable" in room.flags(), 40)
    status, body = room.call("POST", "/voice/ask", {"text": "what is the humidity"})
    reply = body["data"]["reply_text"] if status == 200 else ""
    check(result, "humidity" in reply, f"local intent answered: \"{reply}\"")
    status, body = room.call("POST", "/voice/ask", {"text": "who won the world cup"})
    reply = body["data"]["reply_text"] if status == 200 else ""
    check(result, reply == OFFLINE, f"general question: \"{reply}\"")
    room.call("POST", "/chaos/restore/internet")
    result["recover_s"] = wait(lambda: "internet_unavailable" not in room.flags(), 40)
    check(result, result["recover_s"] is not None, "back online")
    return result


def budget(room):
    result = row("Models over budget", "POST /chaos/cut/budget, memory reads 110 percent of the ceiling",
                 "vision shed first, then general questions, announcements always play")
    room.call("POST", "/chaos/cut/budget")
    result["detect_s"] = wait(lambda: "over_budget" in room.flags() and room.get("/occupancy")["source"] == "presence", 30)
    check(result, result["detect_s"] is not None, "vision shed, occupancy from presence")
    shed_general = wait(lambda: room.call("POST", "/voice/ask", {"text": "who won the world cup"})[1]["data"]["reply_text"] == BUSY, 30, 0.5)
    check(result, shed_general is not None, "general questions shed with a spoken sentence")
    status, body = room.call("POST", "/announcements/ann_evacuate/play", {})
    check(result, status == 200, f"announcement still plays, {status}")
    room.call("POST", "/chaos/restore/budget")
    result["recover_s"] = wait(lambda: "over_budget" not in room.flags() and room.get("/occupancy")["source"] == "vision", 90)
    check(result, result["recover_s"] is not None, "budget back, vision counting again")
    return result


def give_up(room, max_restarts):
    result = row("Vision keeps crashing", f"POST /chaos/kill/vision until the supervisor gives up after {max_restarts} restarts",
                 "service_failed, vision down on /health, the rest of the room keeps running")
    started = time.monotonic()
    for _ in range(max_restarts + 2):
        if room.component("vision")["status"] == "down":
            break
        before = room.component("vision")["restarts"]
        room.call("POST", "/chaos/kill/vision")
        wait(lambda: (c := room.component("vision"))["status"] == "down" or (c["restarts"] > before and c["status"] == "ok"), 60)
    vision = room.component("vision")
    result["detect_s"] = round(time.monotonic() - started, 2)
    check(result, vision["status"] == "down" and "service_failed" in room.flags(), f"gave up after {vision['restarts']} restarts")
    status, _ = room.call("POST", "/voice/ask", {"text": "what is the temperature"})
    check(result, status == 200 and room.health()["status"] == "degraded", "room degraded, still answering")
    return result


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def report(rows, clean_log, settings, total_s):
    lines = [
        "# Chaos report",
        "",
        f"Run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} with `python tools/chaos.py`, core in replay mode with",
        f"`VESTA_STRICT=1`, on {platform.system()} {platform.machine()}, Python {platform.python_version()}. Settings: {settings}.",
        f"Whole run {total_s} s.",
        "",
        "Detect is the time from the injection until the degraded behaviour was observed through the API. Recover",
        "is the time from removing the failure, or from the kill for a service, until the room was healthy again.",
        "",
        "| # | Failure | Injected | Expected | Detect s | Recover s | Result |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for n, r in enumerate(rows, 1):
        lines.append(f"| {n} | {r['failure']} | {r['injected']} | {r['expected']} | {r['detect_s'] if r['detect_s'] is not None else '-'} | "
                     f"{r['recover_s'] if r['recover_s'] is not None else '-'} | {'pass' if r['ok'] else '**FAIL**'} |")
    lines += ["", "## Observations", ""]
    for n, r in enumerate(rows, 1):
        lines.append(f"{n}. {r['failure']}: " + "; ".join(r["observed"]) + ".")
    lines += ["", "## Log", "", f"Unhandled exceptions in the core log: {'none' if clean_log else '**found**'}.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "eval" / "reports" / "chaos.md"))
    args = parser.parse_args()

    port = free_port()
    max_restarts = 3
    env = {**os.environ, "PYTHONPATH": str(ROOT), "VESTA_MODE": "replay", "VESTA_STRICT": "1", "VESTA_CHAOS": "1",
           "VESTA_API_HOST": "127.0.0.1", "VESTA_API_PORT": str(port), "VESTA_API_KEY": KEY,
           "VESTA_SUPERVISOR_MAX_RESTARTS": str(max_restarts), "VESTA_LOG_FORMAT": "json"}
    if args.fast:
        env.update(FAST)
    settings = ", ".join(f"{k[6:].lower()}={v}" for k, v in sorted(env.items())
                         if k.startswith(("VESTA_SUPERVISOR", "VESTA_GUARD", "VESTA_INTERNET", "VESTA_DEVICE"))) or "defaults"

    handle, name = tempfile.mkstemp(prefix="vesta-chaos-", suffix=".log")
    os.close(handle)
    log = Path(name)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as sink:
        core = subprocess.Popen([sys.executable, "-m", "services.core"], cwd=ROOT, env=env, stdout=sink, stderr=subprocess.STDOUT)
        room = Room(port)
        rows = []
        try:
            if wait(lambda: (h := room.health()) is not None and h["status"] == "ok", 60, 0.5) is None:
                print("the room never became healthy, see", log)
                return 1
            for name in SERVICES:
                print(f"kill {name}", flush=True)
                rows.append(kill_service(room, name))
            for scenario in (esp32, camera, stt, internet, budget):
                print(scenario.__name__, flush=True)
                rows.append(scenario(room))
            print("give up", flush=True)
            rows.append(give_up(room, max_restarts))
        finally:
            core.terminate()
            try:
                core.wait(20)
            except subprocess.TimeoutExpired:
                core.kill()
    text = log.read_text(encoding="utf-8", errors="replace")
    clean = "Traceback" not in text and '"exc":' not in text
    out = report(rows, clean, settings, round(time.monotonic() - started, 1))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(out, encoding="utf-8", newline="\n")
    failed = [r["failure"] for r in rows if not r["ok"]]
    print(f"wrote {args.out}, {len(rows) - len(failed)} of {len(rows)} rows passed, log {'clean' if clean else 'has exceptions'}")
    if failed or not clean:
        print("failed:", ", ".join(failed) or "log", "| core log:", log)
        return 1
    log.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
