"""The audio side of the AI bridge inside the team backend: audio -> wake -> VAD -> STT -> router -> team Commands -> Lights -> reply.

Deterministic tests use the clearly fake demo engines (scripted wake, energy VAD, scripted STT: their engine id says so). One test
runs the REAL hash-verified models (openWakeWord, Silero, faster-whisper, Piper) on a SYNTHETIC Piper sentence when the git-ignored
model files are present. No microphone, speaker or device. SOFTWARE-VALIDATED / SIMULATED.
"""

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

AI_ROOT = Path(__file__).resolve().parent.parent
TEAM_ROOT = AI_ROOT.parent
if not (TEAM_ROOT / "services" / "api" / "server.py").is_file():
    pytest.skip("the team backend (services/) is not next to this folder", allow_module_level=True)
if str(TEAM_ROOT) not in sys.path:
    sys.path.append(str(TEAM_ROOT))
os.environ.setdefault("VESTA_MODE", "replay")

from assistant.backend_bridge import (                                    # noqa: E402
    LIVE_MICROPHONE, RECORDED_AUDIO, SYNTHETIC_AUDIO, TYPED_TEXT, AiBridge,
)
from scripts.ai2_stage_b_live import Engines                              # noqa: E402
from scripts.vesta_runtime_demo import BLOCK, PERIOD, SPEECH, WAKE_CHUNK, WAKE_CHUNK_IN_PERIOD, EnergyVad, ScriptedStt, ScriptedWake   # noqa: E402
from services.core import config as team_config                           # noqa: E402
from services.core.runtime import Runtime                                 # noqa: E402
from services.device.hardware import Hardware                             # noqa: E402
from services.device.lights import Lights                                 # noqa: E402
from services.voice.commands import Commands                              # noqa: E402

ALL_ON = {z: 100 for z in ("left", "center", "right", "professor")}
MODELS = AI_ROOT / "models"


def speech_stream(n):
    """n periods of 6 s, each with a loud 1.0-2.5 s region (the demo's energy VAD calls it speech). Not speech audio."""
    x = np.zeros(PERIOD * n, np.int16)
    for k in range(n):
        a, b = k * PERIOD + SPEECH[0], k * PERIOD + SPEECH[1]
        x[a:b] = (2000 + np.arange(a, b) % 1000).astype(np.int16)
    return x


def build(sentences=(), wake=True, engines=None, start=True):
    hw, lights = Hardware(), Lights()
    hw.read_sensors()
    rt = Runtime(lights, None, None, hw)
    fire = [WAKE_CHUNK_IN_PERIOD + k * (PERIOD // WAKE_CHUNK) for k in range(len(sentences))] if wake else []
    engines = engines or Engines(ScriptedWake(fire=fire), EnergyVad(), ScriptedStt([f"hey jarvis {s}" for s in sentences] or [""]))
    bridge = AiBridge(Commands(hw, rt, team_config), rt, hw, None, simulated=True, engines=engines)
    if start:
        rt.start()
    return bridge, rt, lights


def feed(bridge, x, kind):
    out = []
    for lo in range(0, len(x), BLOCK):
        out += bridge.feed_audio(x[lo:lo + BLOCK], kind)
    return out + bridge.finish_audio(kind)


def test_audio_command_goes_through_wake_vad_stt_to_the_team_lights():
    bridge, rt, lights = build(["turn on the lights"])
    try:
        recs = feed(bridge, speech_stream(1), SYNTHETIC_AUDIO)
    finally:
        rt.stop()
    assert len(recs) == 1
    r = recs[0]
    assert (r["input"], r["pipeline"], r["stt_engine"], r["action"]) == (SYNTHETIC_AUDIO, "TRANSCRIBED", "demo-scripted-stt", "APPLIED_CONFIRMED")
    assert r["transcript"] == "turn on the lights"                      # the wake phrase was removed by the pipeline
    assert r["contract"]["outcome"] == "executed" and r["contract"]["command"]["source"] == "microphone"
    assert lights.zones == ALL_ON and bridge.playback.calls == 1        # the reply went through the arbiter
    assert "transcript" not in bridge.status()["last"] and bridge.status()["last"]["input"] == SYNTHETIC_AUDIO


def test_no_wake_word_means_nothing_happens_and_nothing_is_invented():
    bridge, rt, lights = build(["turn on the lights"], wake=False)
    try:
        recs = feed(bridge, speech_stream(1), RECORDED_AUDIO)
    finally:
        rt.stop()
    assert recs == [] and set(lights.zones.values()) == {0} and bridge.playback.calls == 0


def test_two_spoken_requests_in_one_recording_are_two_results():
    bridge, rt, lights = build(["turn on the lights", "what is the temperature"])
    try:
        recs = feed(bridge, speech_stream(2), RECORDED_AUDIO)
    finally:
        rt.stop()
    assert [r["contract"]["command"]["intent"] for r in recs] == ["lights_on", "query_temperature"]
    assert recs[1]["contract"]["outcome"] == "answered" and f"{bridge._store.snapshot().environment.temperatures[0].value:g}" in recs[1]["reply"]


def test_an_unconfirmed_spoken_command_is_not_reported_executed():
    bridge, rt, lights = build(["turn on the lights"], start=False)     # the team engine is not ticking: nothing is applied
    recs = feed(bridge, speech_stream(1), SYNTHETIC_AUDIO)
    assert recs[0]["action"] == "TIMEOUT_UNKNOWN" and recs[0]["contract"] is None
    assert recs[0]["reply"] == "The simulated controller accepted the command, but completion is not confirmed."


def test_text_and_audio_share_one_runtime_and_stay_labelled():
    bridge, rt, _ = build(["turn off the lights"])
    try:
        code, body = bridge.ask("what is the humidity")
        assert code == 200 and bridge.status()["last"]["input"] == TYPED_TEXT
        recs = feed(bridge, speech_stream(1), SYNTHETIC_AUDIO)
    finally:
        rt.stop()
    assert recs[0]["input"] == SYNTHETIC_AUDIO and bridge.status()["voice"] == "text-input+audio"


@pytest.mark.parametrize("kind", [TYPED_TEXT, "MICROPHONE", None])
def test_audio_input_kind_must_be_an_audio_kind(kind):
    bridge, _, _ = build(start=False)
    with pytest.raises(ValueError):
        bridge.feed_audio(np.zeros(BLOCK, np.int16), kind)


def test_a_text_only_bridge_refuses_audio():
    hw, lights = Hardware(), Lights()
    rt = Runtime(lights, None, None, hw)
    bridge = AiBridge(Commands(hw, rt, team_config), rt, hw, None, simulated=True)
    with pytest.raises(RuntimeError):
        bridge.feed_audio(np.zeros(BLOCK, np.int16), SYNTHETIC_AUDIO)
    with pytest.raises(RuntimeError):
        bridge.start_microphone(object())


def test_start_ai_is_text_only_by_default_and_wires_the_microphone_only_when_asked(monkeypatch):
    from services.voice import ai as glue
    hw, lights = Hardware(), Lights()
    rt = Runtime(lights, None, None, hw)
    cmd = Commands(hw, rt, team_config)
    monkeypatch.setattr(team_config, "load", lambda: dict(team_config.DEFAULTS))
    assert glue.start_ai(cmd, hw, rt).status()["voice"] == "text-input"

    import scripts.ai2_stage_b_live as stage_b
    import scripts.vesta_live_voice as live
    from scripts.vesta_runtime_demo import DemoPlayback, DemoTts
    started = []
    monkeypatch.setattr(team_config, "load", lambda: {**team_config.DEFAULTS, "ai.voice_input": "microphone"})
    monkeypatch.setattr(stage_b, "load_engines", lambda mode: Engines(ScriptedWake(fire=[]), EnergyVad(), ScriptedStt([""])))
    monkeypatch.setattr(live, "_real_tts", DemoTts)
    monkeypatch.setattr(live, "_real_playback", lambda device: DemoPlayback())
    monkeypatch.setattr(live, "_real_mic", lambda hint: "fake-mic")
    monkeypatch.setattr(AiBridge, "start_microphone", lambda self, mic, config=None: started.append(mic))
    bridge = glue.start_ai(cmd, hw, rt)
    assert started == ["fake-mic"] and bridge.engines is not None


@pytest.mark.skipif(not (MODELS / "yolox_tiny.onnx").exists() or not any(MODELS.glob("faster-whisper-*")) or not any(MODELS.glob("vits-piper-*")),
                    reason="real models not present in AI engineer/models (git-ignored)")
def test_real_models_hear_a_synthetic_command_and_the_team_lights_change():
    from scripts.ai2_stage_b_live import MODE_WAKE, load_engines
    from scripts.vesta_ai_backend_demo import synthetic_16k
    from scripts.vesta_live_voice import _real_tts
    tts = _real_tts()
    pcm, _ = synthetic_16k(tts, "Hey Jarvis. Turn on the lights.")
    x = np.concatenate([np.zeros(8000, np.int16), pcm, np.zeros(32000, np.int16)])
    bridge, rt, lights = build(engines=load_engines(MODE_WAKE))
    try:
        recs = feed(bridge, x, SYNTHETIC_AUDIO)
    finally:
        rt.stop()
    assert len(recs) == 1 and recs[0]["stt_engine"] == "faster-whisper" and recs[0]["pipeline"] == "TRANSCRIBED"
    assert "turn on the lights" in recs[0]["transcript"].lower() and recs[0]["contract"]["outcome"] == "executed"
    assert lights.zones == ALL_ON
