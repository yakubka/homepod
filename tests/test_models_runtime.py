"""Acceptance tests on the real model files.

Skipped when the models are not installed. Point VESTA_MODELS_DIR at the
directory, see docs/budget.md for the file list.
"""

import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from services.core import config
from services.core.models import MODELS, SAMPLE_TEXT, Priority, Registry
from services.core.scheduler import Scheduler

MODELS_DIR = config.load()["models.dir"]
MODELS_DIR = MODELS_DIR if os.path.isabs(MODELS_DIR) else os.path.join(config.ROOT, MODELS_DIR)
INSTALLED = all(os.path.exists(os.path.join(MODELS_DIR, spec.path)) for spec in MODELS)

pytestmark = pytest.mark.skipif(not INSTALLED, reason=f"model files not installed in {MODELS_DIR}")


def utterance(registry):
    audio, rate = registry.get("tts")(SAMPLE_TEXT)
    positions = np.arange(0, len(audio) * 16000 / rate) * rate / 16000
    return np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)


def test_all_models_loaded_stay_under_the_memory_ceiling():
    code = (
        "import json, psutil\n"
        "from services.core.models import MODELS, Registry\n"
        "reg = Registry()\n"
        "for spec in MODELS:\n"
        "    model = reg.get(spec.id)\n"
        "    model(*model.sample())\n"
        "print(json.dumps({'rss_mb': psutil.Process().memory_info().rss / 1048576, 'loaded': list(reg.loaded)}))\n"
    )
    env = {**os.environ, "PYTHONPATH": config.ROOT}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=config.ROOT, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["loaded"] == [spec.id for spec in MODELS]
    assert result["rss_mb"] < config.load()["models.memory_ceiling_mb"], result


def test_every_onnx_session_runs_with_its_declared_threads():
    reg = Registry()
    for model_id in ("vad", "detector"):
        model = reg.get(model_id)
        assert model.session.get_session_options().intra_op_num_threads == model.spec.threads
    for session in (reg.get("wakeword").session.mel, reg.get("wakeword").session.classify):
        assert session.get_session_options().intra_op_num_threads == 1
    assert reg.peak_threads(config.load()["models.heavy_permits"]) <= config.load()["models.cores"]


def test_speech_to_text_keeps_its_latency_while_vision_runs_at_one_frame_per_second():
    scheduler = Scheduler(permits=1)
    reg = Registry(scheduler=scheduler)
    audio = utterance(reg)
    reg.evict("tts")
    detector = reg.get("detector")
    frame = detector.sample()[0]

    def transcribe():
        started = time.perf_counter()
        with scheduler.burst(Priority.VOICE):
            text = reg.infer("stt", audio)
        assert "lights" in text.lower()
        return (time.perf_counter() - started) * 1000.0

    transcribe()
    alone = [transcribe() for _ in range(20)]

    stop = threading.Event()
    frames = {"ran": 0, "skipped": 0}

    def vision():
        while not stop.is_set():
            if scheduler.should_yield(Priority.VISION):
                frames["skipped"] += 1
            else:
                reg.infer("detector", frame)
                frames["ran"] += 1
            stop.wait(1.0)

    loop = threading.Thread(target=vision, daemon=True)
    loop.start()
    time.sleep(1.5)
    contended = [transcribe() for _ in range(20)]
    stop.set()
    loop.join(5)

    p95_alone, p95_contended = np.percentile(alone, 95), np.percentile(contended, 95)
    assert frames["ran"] >= 1 and frames["skipped"] >= 1, frames
    assert p95_contended <= 1.4 * p95_alone, (round(p95_alone, 1), round(p95_contended, 1), frames)
