"""Model registry.

Every neural model on the Pi is declared once in MODELS. Services ask the
registry for a model by id and never build an inference session themselves:

    stt = registry.get("stt")                      # loads and warms up on first use
    text = registry.infer("stt", audio)            # scheduled by priority class

A model is loaded on first use and one dummy inference runs straight away, so
the first real request never pays the cold cost. On-demand models are evicted
least recently used first when the declared memory of the loaded set would pass
the ceiling. Resident models, the wake word and the VAD, are never evicted.

Thread budget. A session runs with exactly the threads declared for it, with
spinning disabled. Realtime models run one after another on the audio thread,
heavy models run one at a time behind the scheduler, so the threads that can
run at once are the realtime lane plus the largest heavy model, see
peak_threads(). docs/budget.md has the numbers.
"""

import enum
import gc
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from services.core import config

log = logging.getLogger("models")


class Priority(enum.IntEnum):
    REALTIME = 0
    VOICE = 1
    VISION = 2
    BACKGROUND = 3


@dataclass(frozen=True)
class ModelSpec:
    id: str
    path: str
    backend: str
    memory_mb: int
    threads: int
    priority: Priority
    resident: bool
    p95_ms: float
    options: dict = field(default_factory=dict, hash=False, compare=False)


MODELS = [
    ModelSpec("wakeword", "openwakeword", "openwakeword", memory_mb=20, threads=1,
              priority=Priority.REALTIME, resident=True, p95_ms=20.0,
              options={"classifier": "hey_jarvis_v0.1.onnx"}),
    ModelSpec("vad", "silero_vad_v6.2.3.onnx", "silero-vad", memory_mb=30, threads=1,
              priority=Priority.REALTIME, resident=True, p95_ms=5.0),
    ModelSpec("stt", "faster-whisper-tiny.en", "faster-whisper", memory_mb=210, threads=3,
              priority=Priority.VOICE, resident=False, p95_ms=1500.0,
              options={"compute_type": "int8", "beam_size": 5}),
    ModelSpec("tts", "vits-piper-en_US-lessac-medium", "sherpa-tts", memory_mb=250, threads=3,
              priority=Priority.VOICE, resident=False, p95_ms=900.0,
              options={"model": "en_US-lessac-medium.onnx"}),
    ModelSpec("detector", "yolox_tiny.onnx", "onnx-yolox", memory_mb=80, threads=3,
              priority=Priority.VISION, resident=False, p95_ms=250.0,
              options={"size": 416}),
]

SAMPLE_TEXT = "Turn on the lights in the left zone, please."


class ModelBudgetError(RuntimeError):
    pass


def session_options(threads, affinity=None):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    if affinity:
        options.add_session_config_entry("session.intra_op_thread_affinities", affinity)
    return options


def affinity_for(threads, cores):
    """Pool threads of a heavy model go to cores 2..n, core 1 stays with the audio thread.

    ONNX Runtime numbers logical processors from 1 and pins only the pool
    threads, the calling thread is the first of the intra op threads.
    """
    if threads < 2 or (os.cpu_count() or 1) < cores:
        return None
    return ";".join(str(core) for core in range(2, 2 + threads - 1)) if threads - 1 <= cores - 1 else None


def onnx_session(path, threads, cores):
    import onnxruntime as ort

    affinity = affinity_for(threads, cores)
    try:
        return ort.InferenceSession(str(path), session_options(threads, affinity), providers=["CPUExecutionProvider"])
    except Exception as e:
        if not affinity:
            raise
        log.warning("thread affinity rejected, loading unpinned", extra={"path": str(path), "reason": str(e)[:200]})
        return ort.InferenceSession(str(path), session_options(threads), providers=["CPUExecutionProvider"])


class OnnxYolox:
    def load(self, spec, path, cores):
        return onnx_session(path, spec.threads, cores)

    def sample(self, session, spec):
        size = spec.options["size"]
        return (np.random.default_rng(0).random((1, 3, size, size), dtype=np.float32) * 255.0,)

    def run(self, session, image):
        return session.run(None, {session.get_inputs()[0].name: image})[0]


class SileroVad:
    """Silero VAD v6, 512 samples at 16 kHz plus 64 samples of context per call."""

    def load(self, spec, path, cores):
        return onnx_session(path, spec.threads, cores)

    def sample(self, session, spec):
        return (np.zeros((1, 576), dtype=np.float32), np.zeros((2, 1, 128), dtype=np.float32))

    def run(self, session, audio, state):
        prob, state = session.run(None, {"input": audio, "state": state, "sr": np.array(16000, dtype=np.int64)})
        return float(prob[0][0]), state


class WakeWordPipeline:
    """openWakeWord streaming step: melspectrogram, embedding, classifier.

    One call consumes 1280 new samples, 80 ms at 16 kHz, and returns the
    classifier score. The three sessions run one after another on the caller.
    """

    def __init__(self, sessions):
        self.mel, self.embed, self.classify = sessions
        self.audio = np.zeros(1760, dtype=np.float32)
        self.mels = np.ones((76, 32), dtype=np.float32)
        self.embeddings = np.zeros((16, 96), dtype=np.float32)

    def step(self, chunk):
        self.audio = np.concatenate([self.audio[len(chunk):], chunk.astype(np.float32)])
        mel = self.mel.run(None, {self.mel.get_inputs()[0].name: self.audio[None, :]})[0]
        mel = np.squeeze(mel) / 10.0 + 2.0
        self.mels = np.concatenate([self.mels, mel])[-76:]
        embedding = self.embed.run(None, {self.embed.get_inputs()[0].name: self.mels[None, :, :, None]})[0]
        self.embeddings = np.concatenate([self.embeddings, embedding.reshape(1, 96)])[-16:]
        score = self.classify.run(None, {self.classify.get_inputs()[0].name: self.embeddings[None, :, :]})[0]
        return float(np.squeeze(score))


class OpenWakeWord:
    def load(self, spec, path, cores):
        files = ("melspectrogram.onnx", "embedding_model.onnx", spec.options["classifier"])
        return WakeWordPipeline([onnx_session(Path(path) / name, spec.threads, cores) for name in files])

    def sample(self, pipeline, spec):
        return (np.zeros(1280, dtype=np.int16),)

    def run(self, pipeline, chunk):
        return pipeline.step(chunk)


class FasterWhisper:
    def load(self, spec, path, cores):
        os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
        from faster_whisper import WhisperModel

        return WhisperModel(str(path), device="cpu", compute_type=spec.options["compute_type"],
                            cpu_threads=spec.threads, num_workers=1)

    def sample(self, model, spec):
        return (np.zeros(16000, dtype=np.float32),)

    def run(self, model, audio):
        segments, _ = model.transcribe(audio, language="en", beam_size=5, temperature=0.0,
                                       condition_on_previous_text=False, vad_filter=False)
        return " ".join(segment.text.strip() for segment in segments)


class SherpaTts:
    def load(self, spec, path, cores):
        import sherpa_onnx as so

        path = Path(path)
        vits = so.OfflineTtsVitsModelConfig(model=str(path / spec.options["model"]), tokens=str(path / "tokens.txt"),
                                            data_dir=str(path / "espeak-ng-data"))
        return so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(vits=vits, num_threads=spec.threads,
                                                                                provider="cpu")))

    def sample(self, tts, spec):
        return ("Ready.",)

    def run(self, tts, text):
        audio = tts.generate(text, sid=0, speed=1.0)
        return np.asarray(audio.samples, dtype=np.float32), audio.sample_rate


BACKENDS = {
    "onnx-yolox": OnnxYolox(),
    "silero-vad": SileroVad(),
    "openwakeword": OpenWakeWord(),
    "faster-whisper": FasterWhisper(),
    "sherpa-tts": SherpaTts(),
}


class Model:
    def __init__(self, spec, backend, session, load_ms, warmup_ms, observe):
        self.spec = spec
        self.backend = backend
        self.session = session
        self.load_ms = load_ms
        self.warmup_ms = warmup_ms
        self.observe = observe
        self.active = 0
        self.calls = 0

    def sample(self):
        return self.backend.sample(self.session, self.spec)

    def __call__(self, *args):
        self.active += 1
        started = time.perf_counter()
        try:
            return self.backend.run(self.session, *args)
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.active -= 1
            self.calls += 1
            if self.observe:
                self.observe(self.spec, elapsed)


class Registry:
    def __init__(self, specs=None, directory=None, ceiling_mb=None, base_mb=None, cores=None,
                 scheduler=None, backends=None, observe=None):
        cfg = config.load()
        self.specs = {spec.id: spec for spec in (MODELS if specs is None else specs)}
        directory = directory or cfg["models.dir"]
        self.directory = Path(directory if os.path.isabs(directory) else os.path.join(config.ROOT, directory))
        self.ceiling_mb = ceiling_mb or cfg["models.memory_ceiling_mb"]
        self.base_mb = cfg["models.base_mb"] if base_mb is None else base_mb
        self.cores = cores or cfg["models.cores"]
        self.scheduler = scheduler
        self.backends = backends or BACKENDS
        self.observe = observe
        self.loaded = OrderedDict()
        self.lock = threading.RLock()
        self.evictions = 0

    def spec(self, model_id):
        try:
            return self.specs[model_id]
        except KeyError:
            raise KeyError(f"model {model_id!r} is not declared in services/core/models.py") from None

    def get(self, model_id):
        with self.lock:
            model = self.loaded.get(model_id)
            if model is not None:
                self.loaded.move_to_end(model_id)
                return model
            spec = self.spec(model_id)
            self._make_room(spec)
            model = self._load(spec)
            self.loaded[model_id] = model
            return model

    def infer(self, model_id, *args, purpose=None):
        model = self.get(model_id)
        if self.scheduler is None or model.spec.priority is Priority.REALTIME:
            return model(*args)
        return self.scheduler.run(model.spec.priority, model, *args, purpose=purpose)

    def _load(self, spec):
        backend = self.backends[spec.backend]
        started = time.perf_counter()
        session = backend.load(spec, self.directory / spec.path, self.cores)
        loaded = time.perf_counter()
        backend.run(session, *backend.sample(session, spec))
        warmed = time.perf_counter()
        model = Model(spec, backend, session, (loaded - started) * 1000.0, (warmed - loaded) * 1000.0, self.observe)
        log.info("model loaded", extra={"model": spec.id, "load_ms": round(model.load_ms, 1),
                                        "warmup_ms": round(model.warmup_ms, 1), "threads": spec.threads})
        return model

    def budget_mb(self):
        return self.ceiling_mb - self.base_mb

    def declared_mb(self):
        return sum(model.spec.memory_mb for model in self.loaded.values())

    def _make_room(self, spec):
        while self.declared_mb() + spec.memory_mb > self.budget_mb():
            victim = next((m for m in self.loaded.values() if not m.spec.resident and m.active == 0), None)
            if victim is None:
                raise ModelBudgetError(
                    f"{spec.id} needs {spec.memory_mb} MB, {self.declared_mb()} MB of {self.budget_mb()} MB is held by "
                    "resident or busy models"
                )
            self.evict(victim.spec.id)

    def evict(self, model_id):
        with self.lock:
            model = self.loaded.get(model_id)
            if model is None:
                return False
            if model.spec.resident:
                raise ModelBudgetError(f"{model_id} is resident and is never evicted")
            del self.loaded[model_id]
            model.session = None
            self.evictions += 1
        gc.collect()
        log.info("model evicted", extra={"model": model_id})
        return True

    def evict_idle(self):
        with self.lock:
            idle = [m.spec.id for m in self.loaded.values() if not m.spec.resident and m.active == 0]
        return [model_id for model_id in idle if self.evict(model_id)]

    def unload(self):
        with self.lock:
            self.loaded.clear()
        gc.collect()

    def peak_threads(self, permits=1):
        realtime = max((s.threads for s in self.specs.values() if s.priority is Priority.REALTIME), default=0)
        heavy = sorted((s.threads for s in self.specs.values() if s.priority is not Priority.REALTIME), reverse=True)
        return realtime + sum(heavy[:permits])

    def status(self):
        return {
            model_id: {"calls": m.calls, "load_ms": round(m.load_ms, 1), "warmup_ms": round(m.warmup_ms, 1),
                       "resident": m.spec.resident, "memory_mb": m.spec.memory_mb}
            for model_id, m in self.loaded.items()
        }
