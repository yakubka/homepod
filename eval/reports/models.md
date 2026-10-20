# Model benchmark

Measured 2026-10-20 05:41 UTC with `python eval/bench_models.py --runs 200`.

> **Not the target CPU.** These numbers come from an AMD64 development machine, the Raspberry Pi 5
> was not reachable. Use them for relative cost and memory, not for Pi latency. Rerun the same
> command on the Pi and commit the new report.

## Machine

| | |
|---|---|
| cpu | 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz |
| arch | AMD64 |
| cores | 4 physical, 8 logical |
| ram | 15.8 GB |
| os | Windows-10-10.0.19045-SP0 |
| python | 3.10.7 |
| runtimes | onnxruntime 1.23.2, ctranslate2 4.8.3, sherpa-onnx 1.13.8 |

## Model files

| Model | File | Size MB | sha256, first 16 |
|---|---|---:|---|
| wakeword | `openwakeword/embedding_model.onnx` | 1.3 | `70d164290c1d095d` |
| wakeword | `openwakeword/hey_jarvis_v0.1.onnx` | 1.2 | `94a13cfe60075b13` |
| wakeword | `openwakeword/melspectrogram.onnx` | 1.0 | `ba2b0e0f8b7b8753` |
| vad | `silero_vad_v6.2.3.onnx` | 2.2 | `1a153a22f4509e29` |
| stt | `faster-whisper-tiny.en/model.bin` | 72.0 | `1a5afae06a4db91c` |
| tts | `vits-piper-en_US-lessac-medium/en_US-lessac-medium.onnx` | 60.2 | `4ba07d8549906668` |
| detector | `yolox_tiny.onnx` | 19.3 | `427cc366d34e27ff` |

## Results

Times in milliseconds over 200 inferences. Speech to text transcribes a 2.3 s utterance synthesised
by the text to speech model, and it was transcribed as "Turn on the lights in the left zone, please.". Text to speech
synthesises "Turn on the lights in the left zone, please.". Wake word is one 80 ms step of the three-model
pipeline, VAD one 32 ms frame, detector one 416 x 416 frame. Contention times include the wait for the
scheduler, so vision and text to speech include time spent behind speech to text bursts, by design.

| Model | Threads | Resident | Cold load | Warmup | Peak RSS MB | Model MB | Alone p50 | p95 | p99 | Contention p50 | p95 | p99 |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| wakeword | 1 | yes | 33.1 | 2.3 | 59.4 | 14.9 | 1.81 | 2.88 | 3.2 | 1.99 | 3.11 | 3.62 |
| vad | 1 | yes | 133.1 | 0.7 | 64.6 | 20.4 | 0.15 | 0.44 | 0.53 | 0.18 | 0.58 | 0.97 |
| stt | 3 | no | 311.5 | 449.1 | 214.5 | 168.0 | 480.06 | 567.02 | 634.12 | 478.46 | 653.5 | 674.21 |
| tts | 3 | no | 1165.3 | 73.4 | 226.4 | 193.8 | 165.39 | 181.29 | 188.01 | 174.18 | 630.48 | 832.05 |
| detector | 3 | no | 115.7 | 74.7 | 104.3 | 60.0 | 64.31 | 71.27 | 72.75 | 182.17 | 1336.85 | 1630.35 |

Peak RSS MB is the peak resident memory of a fresh process holding only that model. Model MB is that peak
minus the process after importing its runtime, which is what the model itself costs.

## All models loaded

Resident memory with every model loaded: **337.0 MB**, peak during the contention run 508.5 MB,
ceiling 1200 MB.
