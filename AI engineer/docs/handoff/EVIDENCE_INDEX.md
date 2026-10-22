# VESTA — evidence index (where hardware evidence goes)

**All private evidence lives OUTSIDE the Git repository** and outside any cloud-synced folder. This file only names the places; it contains no consent records, no voices and no transcripts. The tools refuse to write real evidence inside the repository and never overwrite an existing result.

Let `<EXTERNAL_DATA_DIR>` be one folder agreed by the team, for example a path under the user's own data area (never inside the project folder). The Week 7 tools take explicit paths, so this layout is a recommendation, not something the code requires.

| What | Put it here (under `<EXTERNAL_DATA_DIR>`) | Naming | Schema / tool | Who |
|---|---|---|---|---|
| Original recordings, exactly as recorded | `recordings/raw/` (keep read-only) | `HW-001.wav`, never a person's name or the words spoken | any format; hash with `sha256sum` | Hardware Engineer |
| Native 16 kHz mono 16-bit WAVs used by the tools | `recordings/native/` | same id as the original | `WavFileSource` format; record how it was converted | Hardware Engineer |
| Recording metadata (Stage A) | `provenance/` | `HW-001.prov.json` | copy `evaluation/ai2_stage_a_provenance_template.json` | Hardware Engineer |
| Week 7 corpus manifest (recording metadata for the evaluator) | `manifests/` | `week7_manifest_<date>.json` | start from `evaluation/week7_corpus_manifest.template.json`; the loader refuses a manifest with real records inside the repository | AI Engineer + Hardware Engineer |
| Consent register | `consent_register/` (private; not shared, not copied anywhere else) | register ids such as `CONSENT-S01` in the manifest only | the manifest carries the marker; the register itself is never in the repo | project owner |
| Stage A run results | `results/stage_a/` | `<recording_id>.stage_a.json` (created by the tool) | `python -m scripts.ai2_stage_a_transcribe run --out-dir ...` | AI Engineer |
| Transcripts and human reviews | inside the Stage A result file (`review` adds the judgement) and in `reviews/` for notes | one line per recording | `python -m scripts.ai2_stage_a_transcribe review ...`; Week 7 `human_review` field | a person who listened |
| Week 7 evaluation runs | `results/week7_eval/<run id>/` (a NEW directory per run) | `week7_eval_items.jsonl`, `week7_eval_report.json`; `.partial` / `INCOMPLETE` mean an unfinished run | `python -m evaluation.week7_voice_eval run --output ...` | AI Engineer |
| Calibration sweeps (DEV only) | `results/sweeps/<run id>/` | `week7_calibration_sweep.json` | `python -m evaluation.week7_calibration ... --output ...` | AI Engineer |
| Latency measurements | `latency/` | one sheet per test, with the method named (stopwatch, loopback, software marker) | free text / CSV; a software marker is NOT sound at the speaker | Hardware Engineer + AI Engineer |
| Speaker and Face/LED observations | `observations/` | `TEST-H-<date>.md` and a video named by test id | notes of what was heard or seen, with time stamps and the log lines | Hardware Engineer / Face owner |
| Failure logs | `failure_logs/` | `<test id>-<date>.log` | console output saved verbatim; check it for transcripts before it leaves the machine | whoever ran the test |
| Environment record | `environment/` | `pi5-install-<date>.txt` | OS version, Python version, `pip list`, the first `pytest` result | Hardware Engineer |

Rules: name files by id, never by person or content (file names are treated as personal data). The evidence files that hold transcripts (`*.stage_a.json`, `week7_eval_items.jsonl`) are private; the aggregate `week7_eval_report.json` holds no transcripts but is still kept with its run. A result that is not accompanied by its provenance/manifest is not evidence.
