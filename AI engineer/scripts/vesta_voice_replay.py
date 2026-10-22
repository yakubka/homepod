"""WAV REPLAY (Windows, no microphone): the FULL voice path with the real models, inside the team backend.

    WAV file (16 kHz mono 16-bit) -> openWakeWord ("hey jarvis") -> Silero VAD -> faster-whisper STT      [real, hash-verified models]
        -> Week 8 router -> validated command -> team Commands -> Runtime override -> tick -> team Lights (replay: a number in memory)
        -> truthful reply -> OutputArbiter -> Piper TTS -> reply WAV files in --out (or --speaker: the default output device)

Input kinds are never mixed up:
  --synthesize "Hey Jarvis. Turn on the lights."   SYNTHETIC_AUDIO: the Piper voice, resampled to 16 kHz, written to --out first
  --wav file.wav --input-kind synthetic            SYNTHETIC_AUDIO: a generated file you supply
  --wav file.wav --input-kind recorded --consent-ref REF   RECORDED_AUDIO: a real recording of a person (consent reference required)
Typed text is POST /voice/ask (scripts/vesta_ai_backend_demo.py); a live microphone is ai.voice_input "microphone" in main.py.
A transcript is printed only when faster-whisper produced it; if the wake word or the VAD does not fire, nothing is invented.

    set VESTA_MODE=replay
    python "AI engineer/scripts/vesta_voice_replay.py" --synthesize "Hey Jarvis. Turn on the lights." --out <folder outside the repo>

Exit codes: 0 = ran; 2 = bad arguments or WAV format; 3 = a model could not be loaded.
"""

import argparse
import os
import sys
import time
import wave
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parents[2]
AI_ROOT = TEAM_ROOT / "AI engineer"
BLOCK = 1280                      # 80 ms at 16 kHz
TAIL_SILENCE_S = 2.0              # lets the VAD close the last utterance


def read_wav_16k(path):
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{path} is {w.getframerate()} Hz / {w.getnchannels()} ch / {8 * w.getsampwidth()} bit; "
                             "need 16000 Hz mono 16-bit (convert a copy first; nothing is resampled here)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="VESTA full voice path from a WAV file (real models, simulated devices).")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--wav", help="16 kHz mono 16-bit WAV")
    src.add_argument("--synthesize", help="sentence for the Piper voice to say (SYNTHETIC input)")
    p.add_argument("--input-kind", choices=("synthetic", "recorded"), help="required with --wav")
    p.add_argument("--consent-ref", help="consent / project-approval reference, required for a recorded person")
    p.add_argument("--out", required=True, help="folder for reply WAVs (and the synthetic input); keep it outside the repository")
    p.add_argument("--speaker", action="store_true", help="play replies on the default output device instead of writing WAVs")
    p.add_argument("--show-text", action="store_true", help="print transcripts and replies (off by default: privacy)")
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("VESTA_MODE", "replay") != "replay":
        print("ERROR: run with VESTA_MODE=replay (this replay never touches room hardware)", file=sys.stderr)
        return 2
    if a.wav and not a.input_kind:
        print("ERROR: --wav needs --input-kind synthetic|recorded", file=sys.stderr)
        return 2
    if a.input_kind == "recorded" and not a.consent_ref:
        print("ERROR: a recording of a person needs --consent-ref", file=sys.stderr)
        return 2
    os.environ["VESTA_MODE"] = "replay"
    sys.path[:0] = [str(AI_ROOT)]
    sys.path.append(str(TEAM_ROOT))
    import logging
    logging.disable(logging.WARNING)

    import numpy as np
    from assistant.backend_bridge import RECORDED_AUDIO, SYNTHETIC_AUDIO, AiBridge
    from scripts.ai2_stage_b_live import MODE_WAKE, StageBModelError, load_engines
    from scripts.vesta_ai_backend_demo import WavPlayback, synthetic_16k
    from scripts.vesta_live_voice import _real_playback, _real_tts
    from services.core import config
    from services.core.runtime import Runtime
    from services.device.hardware import Hardware
    from services.device.light_sensor import LightSensor
    from services.device.lights import Lights
    from services.voice.commands import Commands

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        engines = load_engines(MODE_WAKE)                 # hash-verified local models only; no downloads
        tts = _real_tts()
    except (StageBModelError, Exception) as exc:
        print(f"ERROR: model load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    if a.synthesize:
        pcm, said_s = synthetic_16k(tts, a.synthesize)
        pcm = np.concatenate([np.zeros(8000, np.int16), pcm])          # 0.5 s lead-in
        wav_path, kind = out / "synthetic_input.wav", SYNTHETIC_AUDIO
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(pcm.tobytes())
        print(f"input: SYNTHETIC_AUDIO (Piper voice, {said_s:.2f} s, resampled to 16 kHz) -> {wav_path.name}")
    else:
        try:
            pcm = read_wav_16k(a.wav)
        except (OSError, ValueError, wave.Error) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        kind = RECORDED_AUDIO if a.input_kind == "recorded" else SYNTHETIC_AUDIO
        print(f"input: {kind} ({len(pcm) / 16000:.2f} s)" + (f", consent ref {a.consent_ref}" if a.consent_ref else ""))

    print("VESTA VOICE REPLAY - REAL MODELS - SIMULATED DEVICES (team replay mode) - NOT HARDWARE-VALIDATED")
    hw, lux, lights = Hardware(), LightSensor(), Lights()
    hw.read_sensors()
    lux.read()
    runtime = Runtime(lights, None, lux, hw)
    commands = Commands(hw, runtime, config, None, None, lux)
    ai = AiBridge(commands, runtime, hw, lux, simulated=True, engines=engines, tts=tts,
                  playback=_real_playback(None) if a.speaker else WavPlayback(out))
    runtime.start()
    try:
        print(f"lights before: {dict(lights.zones)}")
        records = []
        x = np.concatenate([pcm, np.zeros(int(TAIL_SILENCE_S * 16000), np.int16)])
        t0 = time.perf_counter()
        for lo in range(0, len(x), BLOCK):
            records += ai.feed_audio(x[lo:lo + BLOCK], kind)
        records += ai.finish_audio(kind)
        print(f"processed {len(x) / 16000:.2f} s of audio in {time.perf_counter() - t0:.2f} s (Windows CPU)")
        if not records:
            print("no utterance: the wake word or the VAD did not fire (nothing invented)")
        for r in records:
            print(f"- input={r['input']} pipeline={r['pipeline']} stt_engine={r['stt_engine']} outcome={r['outcome']} action={r['action']}"
                  f" contract_outcome={(r['contract'] or {}).get('outcome', 'none (not confirmed)' if r['action'] else '-')}")
            if a.show_text:
                print(f"  heard: {r['transcript']!r}\n  reply: {r['reply']!r}")
        print(f"lights after: {dict(lights.zones)}")
        if not a.speaker:
            print(f"reply WAVs: {sorted(f.name for f in out.glob('reply_*.wav'))}")
    finally:
        runtime.stop()
        ai.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
