"""DEMO (Windows, no hardware): the whole AI subsystem inside the team backend, driven through the team REST API.

    team replay devices -> team Runtime thread (the ONE classroom state and decision engine) -> team REST server (127.0.0.1)
    POST /voice/ask text -> AI router -> validated command -> team Commands -> override -> Runtime tick -> Lights -> confirmed reply
    --image: photo -> YOLOX (real model) -> team OccupancyLoop -> GET /occupancy (advisory: not used for lighting)
    --audio: every reply is spoken by the real Piper TTS into WAV files in --out (no loudspeaker). Requests here are TYPED TEXT;
             audio INPUT through the real wake word -> VAD -> STT is scripts/vesta_voice_replay.py

Replay-mode sensors are the team's random simulated values; lights are numbers in memory. SIMULATED / SOFTWARE-VALIDATED only.

    set VESTA_MODE=replay
    python "AI engineer/scripts/vesta_ai_backend_demo.py" [--image photo.jpg] [--audio --out <folder outside the repo>]
"""

import argparse
import json
import os
import sys

import urllib.error
import urllib.request
import wave
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parents[2]
AI_ROOT = TEAM_ROOT / "AI engineer"
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from scripts.vesta_runtime_demo import DemoPlayback   # noqa: E402

TEXTS =("turn on the lights", "what is the temperature", "how bright is the room", "how many students are here",
         "turn on the light", "turn off the lights")


def http(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def show(label, code, body):
    print(f"\n{label} -> HTTP {code}\n{json.dumps(body, indent=1, ensure_ascii=False)}")


class WavPlayback(DemoPlayback):
    """Writes each reply to a WAV file in `out` instead of a loudspeaker (no audio device is opened)."""

    def __init__(self, out):
        super().__init__()
        self.out = Path(out)

    def play(self, audio):
        with wave.open(str(self.out / f"reply_{self.calls + 1:02d}.wav"), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(audio.sample_rate), w.writeframes(audio.samples.tobytes())
        return super().play(audio)


def synthetic_16k(tts, sentence):
    """SYNTHETIC input audio: the Piper voice says `sentence`, linearly resampled to 16 kHz mono int16 (a demo resample, not a
    recording of a person). Returns (pcm, synthesized duration in seconds)."""
    import numpy as np
    said = tts.synthesize(sentence)
    x = said.samples.astype(np.float32)
    n = int(len(x) * 16000 / said.sample_rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16), said.duration_s


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="VESTA AI + team backend demo (SIMULATED, no hardware).")
    p.add_argument("--image", help="classroom photo for the YOLOX camera path")
    p.add_argument("--audio", action="store_true", help="real Piper TTS + faster-whisper STT (synthetic speech, WAV replies)")
    p.add_argument("--out", help="folder for reply WAVs (required with --audio; keep it outside the repository)")
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")         # replies contain the degree sign; the Windows console default would garble it
    if os.environ.get("VESTA_MODE", "replay") != "replay":
        print("ERROR: run with VESTA_MODE=replay (this demo never touches hardware)", file=sys.stderr)
        return 2
    if a.audio and not a.out:
        print("ERROR: --audio needs --out", file=sys.stderr)
        return 2
    os.environ["VESTA_MODE"] = "replay"
    sys.path[:0] = [str(AI_ROOT)]
    sys.path.append(str(TEAM_ROOT))
    import logging
    logging.disable(logging.WARNING)

    from assistant.backend_bridge import AiBridge
    from services.api import server
    from services.core import config
    from services.core.runtime import Runtime
    from services.device.hardware import Hardware
    from services.device.light_sensor import LightSensor
    from services.device.lights import Lights
    from services.vision.detector import engine_occupancy, make_detector
    from services.vision.occupancy import OccupancyLoop
    from services.voice.commands import Commands

    print("VESTA AI + TEAM BACKEND DEMO - SIMULATED DEVICES - NO HARDWARE - NOT HARDWARE-VALIDATED")
    hw, lux, lights = Hardware(), LightSensor(), Lights()
    hw.read_sensors()
    lux.read()
    occupancy = None
    if a.image:
        sys.path.insert(0, str(AI_ROOT / "scripts"))
        from vesta_backend_yolox_demo import ImageCamera, bgr_image
        cfg = {**config.DEFAULTS, "vision.detector": "yolox"}
        occupancy = engine_occupancy(cfg, OccupancyLoop(ImageCamera([bgr_image(a.image)]), make_detector(cfg), cfg["detection_interval"]))
        occupancy._loop.last = occupancy.snapshot()          # one frame, measured once (no camera thread in the demo)
    runtime = Runtime(lights, occupancy, lux, hw)
    commands = Commands(hw, runtime, config, None, None, lux)

    tts = playback = None
    if a.audio:
        from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        tts, playback = SherpaOnnxTtsEngine(), WavPlayback(out)

    ai = AiBridge(commands, runtime, hw, lux, simulated=True, tts=tts, playback=playback)
    api = server.start(commands, hw, runtime, config, 0, lux, ai)
    base = f"http://127.0.0.1:{api.server_port}"
    runtime.start()
    print(f"team REST server on {base} (AI enabled, origin {ai.status()['origin']})")
    try:
        for text in TEXTS:
            show(f'POST /voice/ask "{text}"', *http(base, "/voice/ask", {"text": text}))
        if a.audio:
            print(f"\nreplies spoken by Piper into WAV files (no speaker): {sorted(f.name for f in out.glob('reply_*.wav'))}")
            print("audio INPUT (wake -> VAD -> STT) is a separate demo: scripts/vesta_voice_replay.py")
        show("GET /sensors", *http(base, "/sensors"))
        if occupancy is not None:
            show("GET /occupancy", *http(base, "/occupancy"))
        show("GET /lights", *http(base, "/lights"))
        code, state = http(base, "/state")
        show("GET /state (ai part)", code, {"ai": state.get("ai"), "overrides": state.get("overrides"), "room": state.get("room")})
        code, dec = http(base, "/decisions?limit=4")
        print("\nGET /decisions (latest 4):")
        for d in dec["decisions"]:
            print(f"  {d['line']}")
    finally:
        runtime.stop()
        api.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
