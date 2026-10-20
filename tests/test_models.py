import os
import threading
import time

import pytest

from services.core import config, contract
from services.core.guard import RuntimeGuard
from services.core.models import MODELS, ModelBudgetError, ModelSpec, Priority, Registry, session_options
from services.core.scheduler import Scheduler


class FakeBackend:
    def __init__(self):
        self.loads = []
        self.runs = []

    def load(self, spec, path, cores):
        self.loads.append(spec.id)
        return {"id": spec.id}

    def sample(self, session, spec):
        return ("warmup",)

    def run(self, session, value, delay=0.0):
        time.sleep(delay)
        self.runs.append((session["id"], value))
        return value


def spec(model_id, memory_mb=100, resident=False, priority=Priority.VOICE, threads=1, p95_ms=50.0):
    return ModelSpec(model_id, model_id, "fake", memory_mb, threads, priority, resident, p95_ms)


def registry(specs, ceiling_mb=500, **kw):
    backend = FakeBackend()
    reg = Registry(specs=specs, directory=".", ceiling_mb=ceiling_mb, base_mb=0, backends={"fake": backend}, **kw)
    return reg, backend


def test_a_model_loads_on_first_use_and_warms_up_once():
    reg, backend = registry([spec("stt")])
    assert backend.loads == []
    model = reg.get("stt")
    assert reg.get("stt") is model and backend.loads == ["stt"]
    assert backend.runs == [("stt", "warmup")]
    assert model.load_ms >= 0 and model.warmup_ms >= 0
    assert reg.infer("stt", "audio") == "audio"


def test_the_least_recently_used_on_demand_model_is_evicted_first():
    reg, _ = registry([spec("wake", resident=True), spec("stt"), spec("tts"), spec("detector")], ceiling_mb=300)
    reg.get("wake")
    reg.get("stt")
    reg.get("tts")
    reg.get("stt")
    reg.get("detector")
    assert list(reg.loaded) == ["wake", "stt", "detector"]
    assert reg.evictions == 1


def test_resident_models_are_never_evicted():
    reg, _ = registry([spec("wake", resident=True, memory_mb=150), spec("vad", resident=True, memory_mb=150), spec("stt", memory_mb=150)],
                      ceiling_mb=300)
    reg.get("wake")
    reg.get("vad")
    with pytest.raises(ModelBudgetError, match="resident or busy"):
        reg.get("stt")
    with pytest.raises(ModelBudgetError, match="never evicted"):
        reg.evict("wake")
    assert list(reg.loaded) == ["wake", "vad"]


def test_a_busy_model_is_not_evicted_under_it():
    reg, _ = registry([spec("stt", memory_mb=200), spec("tts", memory_mb=200)], ceiling_mb=300)
    started = threading.Event()

    def long_call():
        model = reg.get("stt")
        started.set()
        model("audio", 0.3)

    worker = threading.Thread(target=long_call)
    worker.start()
    started.wait(1)
    time.sleep(0.05)
    with pytest.raises(ModelBudgetError):
        reg.get("tts")
    worker.join()
    reg.get("tts")
    assert list(reg.loaded) == ["tts"]


def test_an_undeclared_model_is_refused():
    reg, _ = registry([spec("stt")])
    with pytest.raises(KeyError, match="not declared"):
        reg.get("llm")


def test_declared_threads_never_exceed_the_core_budget():
    reg = Registry(directory=".")
    cores = config.DEFAULTS["models.cores"]
    assert reg.peak_threads(config.DEFAULTS["models.heavy_permits"]) <= cores
    assert all(s.threads <= cores - 1 for s in MODELS if s.priority is not Priority.REALTIME)
    assert sum(s.memory_mb for s in MODELS) + config.DEFAULTS["models.base_mb"] <= config.DEFAULTS["models.memory_ceiling_mb"]
    assert {s.id for s in MODELS if s.resident} == {"wakeword", "vad"}


def test_onnx_sessions_are_pinned_and_do_not_spin():
    options = session_options(2)
    assert options.intra_op_num_threads == 2 and options.inter_op_num_threads == 1
    assert options.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    assert options.get_session_config_entry("session.inter_op.allow_spinning") == "0"


def test_voice_is_served_before_vision_when_the_permit_frees():
    scheduler = Scheduler(permits=1)
    order = []
    release = threading.Event()

    def holder():
        with scheduler.slot(Priority.BACKGROUND):
            release.wait(2)

    def waiter(priority, label):
        with scheduler.slot(priority):
            order.append(label)

    threads = [threading.Thread(target=holder)]
    threads[0].start()
    time.sleep(0.05)
    for priority, label in ((Priority.VISION, "vision"), (Priority.BACKGROUND, "analytics"), (Priority.VOICE, "voice")):
        t = threading.Thread(target=waiter, args=(priority, label))
        t.start()
        threads.append(t)
        time.sleep(0.05)
    release.set()
    for t in threads:
        t.join(2)
    assert order == ["voice", "vision", "analytics"]


def test_vision_yields_while_a_voice_burst_is_open():
    scheduler = Scheduler()
    assert not scheduler.should_yield(Priority.VISION)
    with scheduler.burst(Priority.VOICE):
        assert scheduler.should_yield(Priority.VISION)
        assert scheduler.should_yield(Priority.BACKGROUND)
        assert not scheduler.should_yield(Priority.VOICE)
    assert not scheduler.should_yield(Priority.VISION)
    assert scheduler.wait_clear(Priority.VISION, timeout=0.1)


def test_a_slow_inference_degrades_once_per_cooldown_and_matches_the_contract():
    events = []
    reg, _ = registry([spec("stt", p95_ms=100.0)])
    guard = RuntimeGuard(reg, lambda topic, payload: events.append((topic, payload)), factor=3.0, cooldown=30.0)
    guard.observe(reg.spec("stt"), 250.0)
    guard.observe(reg.spec("stt"), 400.0)
    guard.observe(reg.spec("stt"), 450.0)
    assert len(events) == 1
    topic, payload = events[0]
    assert topic == "system.degraded" and payload["reason"] == "slow_inference" and payload["limit"] == 300.0
    envelope = {"id": "evt_abcdef1234", "topic": topic, "ts": payload["ts"], "source": "core",
                "contract_version": contract.version(), "correlation_id": None, "payload": payload}
    assert contract.envelope_errors(envelope) == []


def test_a_memory_breach_evicts_idle_models_and_never_raises():
    reg, _ = registry([spec("wake", resident=True), spec("stt"), spec("tts")])
    for model_id in ("wake", "stt", "tts"):
        reg.get(model_id)

    def broken_publish(topic, payload):
        raise RuntimeError("bus down")

    guard = RuntimeGuard(reg, broken_publish, ceiling_mb=1200, rss=lambda: 1300.0)
    payload = guard.check_memory()
    assert payload["action"] == "evicted" and sorted(payload["evicted"]) == ["stt", "tts"]
    assert list(reg.loaded) == ["wake"]
    assert RuntimeGuard(reg, broken_publish, ceiling_mb=1200, rss=lambda: 900.0).check_memory() is None


@pytest.mark.skipif(not os.cpu_count(), reason="cpu count unknown")
def test_the_core_budget_fits_this_machine_or_the_pi():
    assert Registry(directory=".").peak_threads() <= max(os.cpu_count(), config.DEFAULTS["models.cores"])
