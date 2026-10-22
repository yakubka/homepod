# Week 2 — Wake Word Detection

**Project:** VESTA (modular AI smart speaker) — AI / voice subsystem
**Status:** FROZEN 2026-09-25. No further Week 2 work is planned.

> **Read this first.** Week 2 delivered and tested the wake-word *software layer* on a Windows development PC.
> **Raspberry Pi 5 validation: NOT performed. ReSpeaker XVF3800 validation: NOT performed. WP1 hardware validation: still PENDING.**
> The only real positive test is **one recording, used as an end-to-end smoke test. It is not an accuracy result.**

---

## Objective

Implement and validate the VESTA wake-word detection software layer **independently of the Raspberry Pi 5 and ReSpeaker XVF3800 hardware, which were not available**. The design goal was that everything downstream of the audio source (re-chunking, detection decision, engine adapter, evaluation tooling) could be built and tested from recorded WAV files, so that when the hardware arrives only the audio source changes.

The temporary development wake phrase is **"hey jarvis"** (a pre-trained openWakeWord model). The team has not chosen the final wake phrase.

---

## Completed work

### WP2.1 — WAV source and reframer
- **`audio/file_source.py` — `WavFileSource`** implements the WP1 `AudioSource` interface. It replays a 16 kHz, mono, 16-bit WAV file as `(1024, 1)` int16 blocks, the same shape and dtype the ReSpeaker backend produces.
  - Fast mode (no waiting) for benchmarking, and real-time paced mode for CPU/latency work. Pacing uses absolute deadlines so timing error cannot accumulate.
  - Stream time comes from a **sample counter**, never the wall clock, so results are reproducible.
  - A distinct `EndOfAudio` exception marks the end of the file. It is deliberately *not* a `queue.Empty`, so it cannot be mistaken for a live-stream timeout.
  - The last block is returned short and unpadded. Unsupported formats are rejected with a clear message.
- **`audio/reframer.py` — `Reframer`** converts blocks of any size into exact fixed-size chunks (1280 samples = 80 ms for openWakeWord). It never loses, duplicates or reorders samples; this is tested over 20 chunk-size and block-size combinations with exact array equality.

### WP2.2 — Wake-word abstraction
- **`wakeword/detector.py`** defines `WakeWordDetector` (abstract), the `Detection` record, and `run_on_source()`.
  - A backend supplies only the engine call and a reset. **Threshold and cooldown live in the base class**, so every engine is judged by the same decision rule.
  - Threshold is inclusive. Cooldown is measured in stream samples from the last *accepted* detection, so suppressed crossings do not extend it.
  - `Detection` carries: keyword, score, threshold, chunk index, end sample, stream time, wall time, and processing time.
  - `run_on_source()` wires source → reframer → detector for finite sources. The module imports **no** engine library (checked by a test in a fresh interpreter).

### WP2.3 — openWakeWord ONNX backend
- **`wakeword/openwakeword_backend.py` — `OpenWakeWordDetector`** (16 kHz, 1280-sample chunks, default model `hey_jarvis`).
- **Lazy import.** openWakeWord is imported only inside the constructor, so every other module and test works on machines where it cannot be imported. This mattered: the Windows dev PC intermittently blocks some scipy/scikit-learn DLLs that openWakeWord's package imports eagerly.
- **Model loading.** ONNX by default (openWakeWord's own default, TFLite, silently falls back to ONNX if `tflite-runtime` is missing, which could mislabel benchmark rows). The backend checks that the required model files exist and otherwise fails with the exact download command. It **never downloads anything itself**.
- **Model files** installed for development (3 ONNX files, about 3.69 MB, into the virtual environment — not the repository): `embedding_model.onnx`, `melspectrogram.onnx`, `hey_jarvis_v0.1.onnx`.
- **Engine behaviour read from the installed v0.6.0 source:** predictions are forced to 0 for the first 5 chunks (a **0.4 s warm-up**), so a wake word in the first 0.4 s of a stream cannot be detected. Test clips therefore need at least 0.5 s of leading silence.
- Also provided: a command-line entry point, `python -m wakeword.openwakeword_backend clip.wav`, that prints scores and detections.
- The backend also accepts a path to a custom model file (intended for a future VESTA phrase). **This is untested against the real library.**

### Real positive smoke test (one recording)
One real human recording of "hey jarvis" was run through the full chain. The file was 16 kHz, 16-bit, 5.735 s, stereo with bit-identical channels, so one channel was extracted losslessly. The original file was never modified.

### Synthetic negative tests
Synthetic silence, white noise (two levels) and a hum/tone were run through the real engine (see results below). These are sanity checks only.

### WP2.4 — Evaluation-corpus infrastructure (infrastructure only; no data)
- **Protocol** (`docs/wp2_4_evaluation_protocol.md`): 5 speakers, 24 positive takes each (120 total) over a fixed grid of distance × condition × speaking style; a negative corpus built incrementally; annotation definitions; speaker-independent split (S01/S02 development, S03–S05 test); privacy rules; and frozen metric definitions. The false-accepts-per-hour target of 0.5/h is **only proposed, not frozen**.
- **Manifest** (`evaluation/manifest.py`, `evaluation/manifest.example.csv`): 48-column schema with validation. Real and synthetic rows cannot be mixed, speakers must be pseudonyms (S01–S05), and a speaker cannot appear in two evaluation groups.
- **Ingest / annotate / validate tool** (`evaluation/ingest.py`, `python -m evaluation.ingest`):
  - never modifies the original; stores a hash-verified read-only raw copy and a 16 kHz mono int16 processed copy;
  - converts only when `--convert` is given, and rejects multi-channel files whose channels differ instead of downmixing;
  - computes SHA-256 and quality fields, and *proposes* wake-phrase boundaries (never marks them verified);
  - refuses to create a corpus inside the repository, never overwrites files, and rolls back on failure.
- **Supporting modules:** `evaluation/audio_analysis.py` (quality metrics, boundary proposal) and `evaluation/resample.py` (pure-numpy resampler; written because `scipy.signal` was intermittently blocked by the Windows policy, and to avoid a new dependency).
- **`.gitignore`** rules for audio files and corpus/consent/speaker-key paths (verified in a throwaway git repository).
- The corpus directory structure was created **empty**, outside the repository (see *Privacy/data status*).

---

## Architecture

```
AudioSource                         ReSpeakerMicrophone (WP1, hardware)  |  WavFileSource (WP2.1, files)
   read_frame() -> (1024, 1) int16 @ 16 kHz
        |
        v
Reframer(1280)                      exact 1-D int16 chunks, no sample lost / duplicated / reordered
        |
        v
WakeWordDetector.process(chunk)     abstract interface: threshold + cooldown + Detection live here
        |
        v
OpenWakeWordDetector                adapter: chunk -> openWakeWord Model.predict (ONNX) -> {keyword: score}
        |
        v
Detection event                     keyword, score, threshold, chunk_index, end_sample,
                                    stream_time_s, wall_time_s, processing_ms
```

**Isolation from the hardware.** Everything below `AudioSource` depends only on the `AudioSource` interface and on int16 chunks. It has no knowledge of the microphone, PortAudio, or the ReSpeaker. Replacing `WavFileSource` with `ReSpeakerMicrophone` is intended to require no change to the reframer, detector or backend. **This live path has not been run**, because the hardware was unavailable. The wake-word module also does not import the engine outside the backend, so another engine (for example Porcupine, deliberately not implemented) could be added as a second subclass.

---

## Validation results

All figures below were re-verified on 2026-09-25 (test counts) or recorded during Week 2 (timings).

### Automated tests
| Configuration | Passed | Skipped | Failed |
|---|---|---|---|
| Frozen Week 2 suite, with the real recording supplied (`VESTA_HEY_JARVIS_WAV`) | **215** | **3** | **0** |
| Frozen Week 2 suite, plain command | 214 | 4 | 0 |
| Snapshot at the end of WP2.3 (before WP2.4), recording supplied | 97 | 3 | 0 |

- 218 tests are collected in total. The 3 skips in every configuration are the **WP1 hardware tests** (no ReSpeaker present). The 4th skip in the plain run is the recorded-clip test, which needs a recording.
- The step from 97 to 215 passed tests is the WP2.4 tooling (audio analysis, manifest, ingest, resampler).
- A mutation check broke 7 safety features in a scratch copy (for example silent downmixing, writable stored copies, a corpus inside the repository); the tests caught all 7.

### Real openWakeWord inference
Real openWakeWord (v0.6.0, ONNX Runtime 1.30.0) was **successfully executed** on this machine, on 1280-sample int16 chunks through the full WAV → reframer → backend chain.

### Real positive recording (one clip; smoke test, NOT an accuracy result)
Settings: model `hey_jarvis`, ONNX, 16 kHz, 1280-sample chunks, threshold 0.5, cooldown 2 s.

| Item | Result |
|---|---|
| Phrase position (estimated from signal energy) | about 1.70–3.16 s, so ≥ 1.7 s of lead-in (the warm-up needs 0.5 s) |
| Detected | **Yes** — keyword `hey_jarvis` |
| Detection score (first threshold crossing) | **0.631** |
| Detection time | **2.800 s** into the stream (end of chunk 34) |
| Peak score | **0.970** at 3.12 s |
| Detections / chunks | 1 / 72; 4 further threshold crossings suppressed by the cooldown |
| Repeatability | identical in 3 runs |

The detection fired before the estimated end of the phrase, so no latency figure is claimed (the phrase boundaries are rough energy-based estimates, not verified annotations).

### Synthetic negatives (real engine; SYNTHETIC audio, 10 s each, threshold 0.5)
| Synthetic file | Detections | Peak score |
|---|---|---|
| Silence | 0 | 0.0000–0.0001 |
| White noise, −30 dBFS | 0 | 0.15–0.18 |
| White noise, −10 dBFS | 0 | 0.20–0.23 |
| 50/150 Hz hum + 1 kHz tone | 0 | 0.0000 |

Peak scores differed slightly between identical runs (for example 0.1475 vs 0.1801); the cause was not confirmed. The source suggests the engine seeds its feature buffer with random noise on reset.

### Timing — **Windows development PC only; NOT representative of the Raspberry Pi**
Windows 11, Python 3.13.14, single-thread ONNX sessions: mean about **3.2–3.9 ms per 80 ms chunk**, maximum 3.8–7.7 ms, real-time factor about **0.040–0.048**; backend load about 1.9 s. **These numbers are for development only.** No timing has been measured on the target hardware.

---

## Limitations

- **Raspberry Pi validation was not performed.**
- **ReSpeaker XVF3800 validation was not performed**, and WP1 hardware validation remains pending. The live microphone → detector path has never been run.
- **Only one real positive recording was tested** (one speaker, one quiet room, one laptop microphone).
- **No statistically meaningful detection rate was calculated**; one clip cannot support one.
- **No real-world false-activations-per-hour result was established.** The synthetic negatives do not measure it.
- **No threshold optimization was performed.** Threshold 0.5 and cooldown 2 s are defaults.
- **No large evaluation corpus was collected.** The corpus is empty.
- Nothing here shows the wake word works reliably; it shows the software chain runs end to end and produced one detection on one recording.

Additional known limitations:
- Detection times are quantised to the 80 ms chunk, and the engine cannot fire during its first 0.4 s.
- The boundary proposal and quality metrics in the ingest tool were validated on synthetic audio and one real clip; **human verification of annotations is essential**.
- The custom resampler was tested against synthetic tones only, not against a reference implementation.
- Tier-2 tests depend on openWakeWord importing, which on this PC was intermittently blocked by Windows Application Control; the tests skip cleanly when it is.
- The consent-reference field is required but its target file is not checked by the tool.

---

## Privacy/data status

- **The formal evaluation corpus is empty. No participant dataset was collected.** The manifest has 0 rows, and there are no audio files, and no consent records, in the corpus.
- The corpus structure exists at `C:\Users\<user>\VESTA-data\` (default), **outside the repository and outside any cloud-synced folder**, with consent forms and the speaker key in a separate `private/` directory.
- The single real recording used for the smoke test is a personal voice recording. It is **not** in the corpus and **not** in the repository. Ingesting it as a pilot clip was stopped **pending a consent record**, which the corpus requires and which has not been created.
- A temporary mono copy of that recording exists in the session's scratch directory outside the repository; it should be deleted when no longer needed.
- All test data used by the automated tests is synthetic. Nothing was uploaded to any external service.
- `.gitignore` blocks audio files and corpus paths, but cannot stop a forced add, cloud sync, or backups.

---

## Week 3 handoff

Next work, in order:
1. **Finalize evaluation recordings** — obtain consent records, decide retention, then record and ingest the positive and negative clips and verify their annotations (the corpus target is 120 positive clips and a negative corpus of about 12 hours, built incrementally).
2. **Benchmark threshold(s)** — WP2.5: sweep thresholds on the development speakers only, choose an operating point against the reviewed false-accept target (0.5/h is proposed, not frozen), then evaluate the test speakers once.
3. **Calculate detection / miss / false-activation metrics** with confidence intervals, per speaker and pooled; headline false accepts per hour on speech and media negatives only, with confuser and ambient results reported separately.
4. **Measure latency consistently** using the frozen definition (first in-window detection time minus annotated wake end; signed; median and 95th percentile), and CPU/memory on a long stream.
5. **Validate on the Raspberry Pi 5 + ReSpeaker XVF3800** when the hardware is available: run WP1 hardware validation first, then the wake-word chain and the benchmark on that hardware (Track B); Windows results are preliminary.

Carry-over items:
- Decide the false-accepts-per-hour target, consent wording, retention period, and who may access `private/`.
- `requirements.txt` (a WP1 file, deliberately untouched) does not yet list `openwakeword` and `onnxruntime`. Add them, with Pi-appropriate versions, when Pi setup starts.
- Confirm on the Pi that `pip install openwakeword` resolves on aarch64, that the engine's reset and 5-chunk warm-up behave as read from the source, and that the score key is `hey_jarvis`.
- `audio/__init__.py` imports the microphone backend, so even WAV replay loads `sounddevice` (WP1 packaging; left unchanged).
- `CLAUDE.md` still contains an older "Current project status" paragraph saying development is only beginning; it was not edited.

---

## Reproducing the results

From the project folder (PowerShell), with the project virtual environment:

```
.venv\Scripts\python.exe -m pytest -q -rs
```
Expected: 214 passed, 4 skipped (3 WP1 hardware tests + 1 recorded-clip test). To include the real-recording test, supply a 16 kHz mono 16-bit WAV of someone saying "hey jarvis" (at least 0.5 s after the start):
```
$env:VESTA_HEY_JARVIS_WAV = "C:\path\to\hey_jarvis_mono.wav"
.venv\Scripts\python.exe -m pytest -q -rs -s
```
To run a WAV through the backend directly:
```
.venv\Scripts\python.exe -m wakeword.openwakeword_backend C:\path\to\hey_jarvis_mono.wav
```
The model files must be present (`python -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])"` fetches them, along with extra unused files). Environment used: Windows 11, Python 3.13.14, openwakeword 0.6.0, onnxruntime 1.30.0, numpy 2.5.3, pytest 9.1.1.

## File inventory (Week 2)

| File | Purpose |
|---|---|
| `audio/file_source.py`, `audio/reframer.py` | WP2.1 WAV source, reframer |
| `wakeword/detector.py` | WP2.2 abstraction, `Detection`, `run_on_source` |
| `wakeword/openwakeword_backend.py` | WP2.3 openWakeWord backend and CLI |
| `evaluation/manifest.py`, `ingest.py`, `audio_analysis.py`, `resample.py`, `manifest.example.csv` | WP2.4 tooling |
| `docs/wp2_4_evaluation_protocol.md` | WP2.4 protocol |
| `tests/` (8 files, 218 tests) | Unit, integration and tooling tests (synthetic data only) |
| `.gitignore` | Audio and corpus protection |
