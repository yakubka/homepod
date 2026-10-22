#!/usr/bin/env python3
"""Benchmark every registered model, alone and under contention.

For each model in services/core/models.py:
  alone       a fresh process per model: cold load, warmup, peak resident
              memory, then p50, p95 and p99 over --runs inferences
  contention  one process with every model loaded, the target running back to
              back while the others run their real duty cycle through the
              scheduler: wake word and VAD on the audio thread every 80 ms and
              32 ms, vision at 1 frame per second yielding to voice, speech to
              text every 3 s and text to speech every 4 s as voice bursts

The speech to text input is a real utterance synthesised by the text to speech
model. Writes a markdown report naming the machine and the model files.

Usage: python eval/bench_models.py [--runs 200] [--out eval/reports/models.md]
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import psutil  # noqa: E402

from services.core import config  # noqa: E402
from services.core.models import MODELS, SAMPLE_TEXT, Priority, Registry  # noqa: E402
from services.core.scheduler import Scheduler  # noqa: E402

MB = 1024 * 1024
RUNTIME_IMPORTS = {"onnx-yolox": "onnxruntime", "silero-vad": "onnxruntime", "openwakeword": "onnxruntime",
                   "faster-whisper": "faster_whisper", "sherpa-tts": "sherpa_onnx"}


def peak_rss_mb():
    info = psutil.Process().memory_info()
    if hasattr(info, "peak_wset"):
        return info.peak_wset / MB
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def percentiles(samples):
    p50, p95, p99 = np.percentile(samples, [50, 95, 99])
    return {"p50": round(float(p50), 2), "p95": round(float(p95), 2), "p99": round(float(p99), 2)}


def inputs_for(model, speech):
    if model.spec.id == "stt":
        return (speech,)
    if model.spec.id == "tts":
        return (SAMPLE_TEXT,)
    if model.spec.id == "wakeword":
        return (np.random.default_rng(1).integers(-2000, 2000, 1280, dtype=np.int16),)
    return model.sample()


def bench_one(model_id, runs, speech_file):
    """Runs in a fresh process, prints one JSON line."""
    spec = next(s for s in MODELS if s.id == model_id)
    __import__(RUNTIME_IMPORTS[spec.backend])
    before = psutil.Process().memory_info().rss / MB
    registry = Registry()
    started = time.perf_counter()
    model = registry.get(model_id)
    total_load = (time.perf_counter() - started) * 1000.0
    args = inputs_for(model, np.load(speech_file))
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        model(*args)
        times.append((time.perf_counter() - t0) * 1000.0)
    print(json.dumps({
        "id": model_id,
        "cold_load_ms": round(model.load_ms, 1),
        "warmup_ms": round(model.warmup_ms, 1),
        "first_use_ms": round(total_load, 1),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "model_peak_mb": round(peak_rss_mb() - before, 1),
        "alone": percentiles(times),
    }))


def make_speech(path):
    registry = Registry()
    audio, rate = registry.get("tts")(SAMPLE_TEXT)
    positions = np.arange(0, len(audio) * 16000 / rate) * rate / 16000
    speech = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
    np.save(path, speech)
    text = registry.get("stt")(speech)
    return round(len(speech) / 16000, 2), text


def contention(runs, speech):
    scheduler = Scheduler(permits=config.load()["models.heavy_permits"])
    registry = Registry(scheduler=scheduler)
    for spec in MODELS:
        registry.get(spec.id)
    all_loaded_rss = psutil.Process().memory_info().rss / MB
    inputs = {spec.id: inputs_for(registry.get(spec.id), speech) for spec in MODELS}
    results = {}

    for target in MODELS:
        stop = threading.Event()
        workers = []

        def audio_lane():
            while not stop.is_set():
                if target.id != "wakeword":
                    registry.infer("wakeword", *inputs["wakeword"])
                if target.id != "vad":
                    state = inputs["vad"]
                    for _ in range(2):
                        registry.infer("vad", *state)
                stop.wait(0.08)

        def vision_lane():
            while not stop.is_set():
                if not scheduler.should_yield(Priority.VISION):
                    registry.infer("detector", *inputs["detector"])
                stop.wait(1.0)

        def voice_lane(model_id, period):
            def run():
                while not stop.wait(period):
                    with scheduler.burst(Priority.VOICE):
                        registry.infer(model_id, *inputs[model_id])
            return run

        lanes = [audio_lane]
        if target.id != "detector":
            lanes.append(vision_lane)
        if target.id != "stt":
            lanes.append(voice_lane("stt", 3.0))
        if target.id != "tts":
            lanes.append(voice_lane("tts", 4.0))
        for lane in lanes:
            worker = threading.Thread(target=lane, daemon=True)
            worker.start()
            workers.append(worker)

        time.sleep(1.0)
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            if target.priority is Priority.VOICE:
                with scheduler.burst(Priority.VOICE):
                    registry.infer(target.id, *inputs[target.id])
            else:
                registry.infer(target.id, *inputs[target.id])
            times.append((time.perf_counter() - t0) * 1000.0)
        stop.set()
        for worker in workers:
            worker.join(30)
        results[target.id] = percentiles(times)
    return results, round(all_loaded_rss, 1), round(peak_rss_mb(), 1)


def cpu_name():
    try:
        model = Path("/proc/device-tree/model")
        if model.exists():
            return model.read_text().strip("\x00 \n")
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    if sys.platform == "win32":
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    return platform.processor() or "unknown"


def machine():
    import ctranslate2
    import onnxruntime
    import sherpa_onnx

    return {
        "cpu": cpu_name(),
        "arch": platform.machine(),
        "cores": f"{psutil.cpu_count(logical=False)} physical, {psutil.cpu_count()} logical",
        "ram": f"{psutil.virtual_memory().total / (1024 ** 3):.1f} GB",
        "os": platform.platform(),
        "python": platform.python_version(),
        "runtimes": f"onnxruntime {onnxruntime.__version__}, ctranslate2 {ctranslate2.__version__}, sherpa-onnx {sherpa_onnx.__version__}",
    }


def model_files(directory):
    rows = []
    for spec in MODELS:
        path = directory / spec.path
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in (".onnx", ".bin"))
        for f in files:
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            rows.append((spec.id, f.relative_to(directory).as_posix(), f.stat().st_size / MB, digest[:16]))
    return rows


def report(info, files, alone, contended, loaded_rss, peak_all, runs, speech_s, transcript, ceiling):
    target = info["arch"].lower() in ("aarch64", "arm64")
    lines = [
        "# Model benchmark",
        "",
        f"Measured {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} with `python eval/bench_models.py --runs {runs}`.",
        "",
    ]
    if not target:
        lines += [
            f"> **Not the target CPU.** These numbers come from an {info['arch']} development machine, the Raspberry Pi 5",
            "> was not reachable. Use them for relative cost and memory, not for Pi latency. Rerun the same",
            "> command on the Pi and commit the new report.",
            "",
        ]
    lines += ["## Machine", "", "| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in info.items()] + [""]
    lines += ["## Model files", "", "| Model | File | Size MB | sha256, first 16 |", "|---|---|---:|---|"]
    lines += [f"| {mid} | `{name}` | {size:.1f} | `{digest}` |" for mid, name, size, digest in files] + [""]
    lines += [
        "## Results",
        "",
        f"Times in milliseconds over {runs} inferences. Speech to text transcribes a {speech_s} s utterance synthesised",
        f"by the text to speech model, and it was transcribed as \"{transcript}\". Text to speech",
        f"synthesises \"{SAMPLE_TEXT}\". Wake word is one 80 ms step of the three-model",
        "pipeline, VAD one 32 ms frame, detector one 416 x 416 frame. Contention times include the wait for the",
        "scheduler, so vision and text to speech include time spent behind speech to text bursts, by design.",
        "",
        "| Model | Threads | Resident | Cold load | Warmup | Peak RSS MB | Model MB | Alone p50 | p95 | p99 | Contention p50 | p95 | p99 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for spec in MODELS:
        a, c = alone[spec.id], contended[spec.id]
        lines.append(
            f"| {spec.id} | {spec.threads} | {'yes' if spec.resident else 'no'} | {a['cold_load_ms']} | {a['warmup_ms']} | "
            f"{a['peak_rss_mb']} | {a['model_peak_mb']} | {a['alone']['p50']} | {a['alone']['p95']} | {a['alone']['p99']} | "
            f"{c['p50']} | {c['p95']} | {c['p99']} |"
        )
    lines += [
        "",
        "Peak RSS MB is the peak resident memory of a fresh process holding only that model. Model MB is that peak",
        "minus the process after importing its runtime, which is what the model itself costs.",
        "",
        "## All models loaded",
        "",
        f"Resident memory with every model loaded: **{loaded_rss} MB**, peak during the contention run {peak_all} MB,",
        f"ceiling {ceiling} MB.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--out", default=str(ROOT / "eval" / "reports" / "models.md"))
    parser.add_argument("--one", help=argparse.SUPPRESS)
    parser.add_argument("--speech", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.one:
        bench_one(args.one, args.runs, args.speech)
        return 0

    directory = Registry().directory
    with tempfile.TemporaryDirectory() as tmp:
        speech_file = os.path.join(tmp, "speech.npy")
        speech_s, transcript = make_speech(speech_file)
        alone = {}
        for spec in MODELS:
            print(f"alone: {spec.id}", flush=True)
            out = subprocess.run([sys.executable, __file__, "--one", spec.id, "--runs", str(args.runs), "--speech", speech_file],
                                 capture_output=True, text=True, cwd=ROOT)
            if out.returncode != 0:
                sys.stderr.write(out.stderr)
                return 1
            alone[spec.id] = json.loads(out.stdout.strip().splitlines()[-1])
        print("contention", flush=True)
        contended, loaded_rss, peak_all = contention(args.runs, np.load(speech_file))

    text = report(machine(), model_files(directory), alone, contended, loaded_rss, peak_all, args.runs, speech_s, transcript,
                  config.load()["models.memory_ceiling_mb"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
