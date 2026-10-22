import logging
import sys
from pathlib import Path

from services.core import config

log = logging.getLogger("ai")


def start_ai(commands, hw, runtime, lux=None):
    # the AI engineer voice stack (AI engineer/assistant/backend_bridge.py), started only when ai.enabled is true
    ai = str(Path(__file__).resolve().parents[2] / "AI engineer")
    if ai not in sys.path:
        sys.path.append(ai)
    from assistant.backend_bridge import AiBridge

    settings = config.load()
    if settings.get("ai.voice_input", "text") != "microphone":
        bridge = AiBridge(commands, runtime, hw, lux, simulated=not config.REAL)
        log.info(f"ai voice bridge ready (text only), origin {bridge.status()['origin']}")
        return bridge

    # microphone: hash-verified wake word, VAD and STT models, the real voice and speaker, the ReSpeaker array
    from scripts.ai2_stage_b_live import MODE_WAKE, load_engines
    from scripts.vesta_live_voice import _real_mic, _real_playback, _real_tts

    bridge = AiBridge(commands, runtime, hw, lux, simulated=not config.REAL, engines=load_engines(MODE_WAKE),
                      tts=_real_tts(), playback=_real_playback(None))
    bridge.start_microphone(_real_mic(None))
    log.info(f"ai voice bridge ready (microphone + text), origin {bridge.status()['origin']}")
    return bridge
