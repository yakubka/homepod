# Model budget

V-03. What every model may use on the Raspberry Pi 5, and the headroom that is left. Every other
ticket in the sprint is accepted against these numbers. The declarations live in
`services/core/models.py`, the measurements in `eval/reports/models.md`.

> The measured columns come from the development laptop, an Intel i5-11300H, because the Pi was
> not reachable on Oct 20. Memory carries over to the Pi closely, latency does not. Rerun
> `python eval/bench_models.py` on the Pi and update the measured columns the day it is in hand.

## Target

| | |
|---|---|
| Board | Raspberry Pi 5, 4 Cortex-A76 cores |
| Cores for models | 4, `models.cores` |
| Memory ceiling for the room process | 1200 MB resident, `models.memory_ceiling_mb` |
| Process base without models | 250 MB allowance, `models.base_mb` |
| Memory left for models | 950 MB |

## Cores

Two lanes, never more than four threads computing at once.

| Lane | Runs | Threads | Rule |
|---|---|---:|---|
| Audio | wake word every 80 ms, VAD every 32 ms while listening | 1 | Realtime, never queued, one model after the other on the audio thread |
| Heavy | speech to text, text to speech, detector | 3 | One inference at a time behind the scheduler, voice before vision before background |
| **Peak** | | **4** | Equal to the cores, checked by `tests/test_models.py` |

The heavy lane's pool threads are pinned to cores 2 to 4 where ONNX Runtime allows it, core 1 is
left to the audio thread and the event loop. Spinning is disabled on every ONNX Runtime session and
CTranslate2 runs with `OMP_WAIT_POLICY=PASSIVE`, so an idle model costs no CPU. sherpa-onnx gives no
spin setting, its pool spins for a few milliseconds after each synthesis.

While a voice burst is open the vision loop skips its frames and resumes afterwards. On the laptop
this kept speech to text p95 within 40 percent of its alone value with vision at 1 frame per
second, `tests/test_models_runtime.py`.

## Per model allowance

| Model | Lane | Resident | Threads | Memory allowance MB | Measured MB | p95 allowance ms | Measured p95 ms, laptop alone |
|---|---|---|---:|---:|---:|---:|---:|
| wakeword | audio | yes | 1 | 20 | 14.9 | 20 per 80 ms step | 2.9 |
| vad | audio | yes | 1 | 30 | 20.4 | 5 per 32 ms frame | 0.4 |
| stt | heavy | no | 3 | 210 | 168.0 | 1500 for an utterance up to 3 s | 567.0 |
| tts | heavy | no | 3 | 250 | 193.8 | 900 for one sentence | 181.3 |
| detector | heavy | no | 3 | 80 | 60.0 | 250 per frame | 71.3 |
| **Total** | | | | **590** | **457.1** | | |

Memory allowances are the measured model memory plus about 25 percent, rounded up to 10 MB. The
p95 allowances are the latency each lane has to meet on the Pi. The runtime guard publishes
`system.degraded` when one inference takes more than three times its allowance.

## Headroom

| | MB |
|---|---:|
| Ceiling | 1200 |
| Base allowance | 250 |
| Model allowances | 590 |
| **Headroom** | **360** |

Measured on the laptop with every model loaded the process held 337 MB, 508 MB at peak during the
contention run.

The optional language model for general questions is not declared yet. The smallest useful one
needs about 400 MB, more than the headroom, so it can only load on demand while the registry
evicts text to speech and the detector, which it does least recently used first. Its ticket has to
declare it with a measured allowance and accept the reload cost of the evicted models.

## When a budget is breached

Core never crashes on a breach. Memory above the ceiling evicts idle on-demand models first, then
publishes `system.degraded` with reason `memory_ceiling`. An inference slower than three times its
allowance publishes `system.degraded` with reason `slow_inference` and the model id. Each service
picks its own fallback in `on_degraded()`, for example vision dropping to one frame every five
seconds or voice answering from templates.

## Changing the budget

1. Change the `ModelSpec` in `services/core/models.py`.
2. Run `python eval/bench_models.py` on the Pi and commit `eval/reports/models.md`.
3. Update this page in the same commit, the totals must still fit the ceiling and the peak threads
   the cores, `pytest tests/test_models.py` checks both.
