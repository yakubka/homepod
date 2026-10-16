# VESTA WP2.4: Wake-Word Evaluation Corpus and Recording Protocol

Version 1 (design approved; infrastructure implemented; **no recordings collected yet**).

## 0. Status and scope

| Item | Status |
|---|---|
| WP1 audio acquisition software | Complete |
| WP1 hardware validation (Raspberry Pi 5 + ReSpeaker XVF3800) | **PENDING** |
| Raspberry Pi validation of the wake-word backend | **PENDING** |
| WP2.3 positive result | One clip, one speaker: an end-to-end **smoke test, not an accuracy result**. It is not part of this corpus. |
| WP2.4 corpus | Infrastructure ready. **No data collected.** |
| WP2.5 benchmark | Not implemented |

**Open (not frozen):** the false-accepts-per-hour operating target. **0.5 per hour is only a proposed target.** It will be reviewed before the final benchmark, and nothing in this project claims it has been achieved.

## 1. Approved design decisions

- 5 speakers, pseudonyms **S01-S05**. Speaker-independent split: **S01, S02 = development; S03, S04, S05 = test**.
- 24 positive takes per speaker, **120 in total**, of the phrase "hey jarvis".
- Real and synthetic data are completely separate.
- The corpus is stored **outside the repository**. Recordings are personal data.
- Track A = the currently available microphone (preliminary results). Track B = ReSpeaker XVF3800 + Raspberry Pi 5 (final validation).
- The negative corpus is built **incrementally**. The 12-hour target is a target, not a gate for development.

## 2. Positive recordings

Each speaker records one take for every cell of the grid below, one take per style per cell: 3 distances x 2 conditions x 4 styles = **24 takes**.

| Factor | Levels |
|---|---|
| Distance | 0.5 m, 2 m, 4 m (use **3 m** instead of 4 m if the room is too small; record the value actually used) |
| Condition | `quiet`, `moderate_noise` |
| Speaking style | `normal`, `soft` (low voice, not whispered), `raised` (across-the-room voice), `fast_casual` |

**Moderate noise:** a fixed background (for example TV dialogue) plays from a loudspeaker at a fixed volume setting, 2 m from the microphone at 90 degrees to the talker axis. Record 30 s of the noise alone per session (a `noise_refs/` entry). The signal-to-noise ratio is estimated automatically from each clip and stored.

**One take = one file** of about 4 s:
1. Start recording. 2. Stay silent for at least 1.0 s. 3. Say "hey jarvis" once. 4. Stay silent for at least 1.5 s. 5. Stop.

The phrase must start **at least 0.5 s** into the clip because the engine cannot fire during its 0.4 s warm-up. The tool enforces the 0.5 s minimum; 1.0 s or more is recommended.

**Rules that keep the data honest**
- Randomize the take order per speaker. Keep the microphone gain fixed within a session. Measure distances with a tape.
- A take is **invalid** if the wrong words were said, other speech is present, or the audio clips. Invalid takes are kept with `take_valid=0` and a reason, never silently deleted.
- **Validity is decided from the audio alone, before any detector is run.** Detector results must never influence which takes are kept.
- The recorder must output **mono**, or the file must be a dual-mono stereo file that is converted explicitly (section 10).

## 3. Negative recordings

| `label_class` | Content | Reported as |
|---|---|---|
| `negative_speech` | Read passages and conversation | **Headline** false accepts per hour |
| `negative_media` | TV, radio, music, played into the room and recorded through the same microphone | **Headline** false accepts per hour |
| `negative_confuser` | Similar-sounding phrases ("hey travis", "hey harvey", "hey marvin", "hey service", "jarvis" alone, "hey" alone, "have a service") | **Separate** metric: false accepts per utterance |
| `negative_ambient` | Empty room, fan, general room noise, silence | **Separate** results, never merged into the headline |

- **Target (not a gate):** about 12 hours in total, most of it unattended media and ambient recording. At 16 kHz mono that is about 1.4 GB. Add hours as they become available; the benchmark reports whatever hours exist.
- **Why ambient is separate:** adding quiet hours would lower the false-accept rate without making the test harder. The headline rate uses only speech and media negatives.
- **Statistics:** with zero false accepts in H hours, the 95 % upper bound is roughly 3/H per hour. Fewer hours means a weaker claim, and the report states the hours used.
- **Public corpora:** none are downloaded. Any use needs a licence check and an explicit approval first, and would be reported separately.
- Never leave a microphone running in a shared household (bystander speech). Use playback and team-only sessions.
- Conversations must be between speakers of the **same** split (for example S01+S02, or S03+S04).

## 4. Synthetic data

- Lives only under `processed/synthetic/`; `clip_id` starts with `SYN_`; `is_synthetic=1`, `data_origin=synthetic`, `split=synthetic`, and a `synthetic_method` is required.
- Includes silence, noise, hum, real audio mixed with noise, and any text-to-speech audio.
- Used for pipeline sanity checks only. It is reported in its own table and **never** in headline numbers. The validator rejects any row that mixes the two, and WP2.5 must exclude synthetic rows from headline metrics.

## 5. Annotation of positive clips

- **Wake start:** the first acoustic energy of "hey". **Wake end:** the last acoustic energy of the final "s" in "jarvis".
- **Units:** the canonical values are **sample indices at 16 kHz** (`wake_start_sample`, `wake_end_sample`, end exclusive). Seconds are derived. Annotate the *processed* file, never the raw one (resampling or trimming would change the time base).
- **Proposal:** `ingest` proposes boundaries from a 20 ms energy detector. The proposal is stored with `annotation_status=pending` and `annotation_method=auto_proposed`. It is **not ground truth**: weak consonants may be missed and a long pause can split the phrase. Warnings are written to `notes`.
- **Verification:** a person checks the boundaries on a waveform (for example in Audacity) to about +/-50 ms, then runs `annotate`. Accepting the proposal unchanged records `auto_verified`; entering different values records `manual`.
- **Second check:** a second annotator independently labels 20 randomly chosen clips; report the boundary differences.
- Only `verified` annotations are used by the benchmark.

## 6. Manifest

One CSV row per audio file, 48 columns, defined in `evaluation/manifest.py` (the single source of truth) with a fake example in `evaluation/manifest.example.csv`.

| Group | Columns |
|---|---|
| Identity | `clip_id`, `file_path`, `sha256`, `raw_sha256`, `label_class`, `is_synthetic`, `synthetic_method`, `data_origin`, `consent_ref` |
| Audio | `sample_rate`, `channels`, `bit_depth`, `duration_s`, `orig_format`, `processing`, `raw_path` |
| Recording | `session_id`, `recorded_date`, `recording_source`, `device_id`, `hardware_track`, `gain_setting`, `room_id`, `environment`, `condition`, `noise_type`, `noise_ref_id` |
| Speaker / prompt | `speaker_id`, `distance_m`, `speaking_style`, `take_number`, `prompt_text`, `take_valid`, `invalid_reason` |
| Annotation | `wake_start_sample`, `wake_end_sample`, `wake_start_s`, `wake_end_s`, `annotation_method`, `annotator_id`, `annotation_status` |
| Quality | `peak_dbfs`, `noise_floor_dbfs`, `clipping_fraction`, `est_snr_db`, `notes` |
| Split | `split`, `split_version` |

Paths are relative to the corpus directory. `sha256` is the hash of the processed evaluation file; `raw_sha256` is the hash of the original. Demographic fields are deliberately **not** collected (data minimisation; five speakers cannot support demographic claims).

## 7. Data split

- **Speaker-independent:** every speaker is in exactly one group. The only tuned parameter is the detection threshold, and it is chosen on development speakers. A speaker present in both groups would leak their voice into the threshold and inflate test results.
- **No training group:** openWakeWord is pre-trained and nothing is fitted to this corpus. A custom VESTA phrase, if ever trained, would use synthetic and public data, never these evaluation recordings.
- **Negatives:** development and test use disjoint recording sessions and disjoint media content (the same programme must not appear in both). Speech negatives follow their speakers; media and ambient sessions are assigned with `--split`.
- The split is stored per row (`split`, `split_version`) and validated: a speaker appearing in two groups is an error.
- **Uncertainty:** with 72 test positives and 95 % detected, the 95 % interval is roughly 87-98 % and wider still because takes from one speaker are not independent. Per-condition cells (12-20 clips) are descriptive only.

## 8. Privacy and storage

- Voice recordings are **personal data**. Nothing is uploaded to any cloud, transcription or annotation service; the tools do local file I/O only.
- The tool **refuses to create or use a corpus inside the repository**.
- Default location: `C:\Users\<you>\VESTA-data` (not inside the repository, and not inside OneDrive on this machine). Override with `--data-dir` or the `VESTA_DATA_DIR` environment variable. The tool warns if the location looks cloud-synced.

```
VESTA-data/                              # outside the repository
  wakeword_corpus/
    README.txt
    raw/<session_id>/                    # originals, read-only, never edited
    processed/
      positive/  negative_confuser/  negative_speech/  negative_media/  negative_ambient/
      synthetic/<same five class folders>/     # SYNTHETIC only
    annotations/                         # label files exported from the annotation tool
    manifests/manifest_v1.csv
    noise_refs/                          # noise-only reference per session
    results/                             # metrics only, no audio
  private/                               # consent and speaker key: separate from the audio
    README.txt
    consent/                             # signed consent forms
    speaker_key.csv                      # created by hand: pseudonym -> person. No tool reads it.
```

- Speakers appear only as **S01-S05**. Real names never appear in the repository, the manifest, or file names. `consent_ref` is a reference such as `CONSENT-S01`, not a name.
- `.gitignore` blocks audio files and the corpus, consent and key paths. It does not stop `git add -f`, cloud sync or backups, so keep recordings out of the repository folder entirely.
- **To be decided by the team (not decided here):** consent wording, retention period, who may access `private/`.

## 9. Evaluation definitions (approved; WP2.5 implements them)

Fixed detector settings: `hey_jarvis` v0.1 ONNX, openWakeWord 0.6.0, 1280-sample chunks, cooldown 2.0 s, VAD off. The results header records package versions and model file hashes.

- **True detection:** at least one detection whose stream time lies in `[wake_start, wake_end + 1.0 s]`. Extra detections inside that window are counted for information only; latency uses the first.
- **Missed detection:** a valid positive clip with no detection in that window.
- **False activation:** any detection outside a positive window, which includes every detection in a negative clip. Stray detections in positive clips are reported separately.
- **False accepts per hour:** post-cooldown detections in **speech and media** negative audio, divided by its hours, excluding the first 0.4 s of each stream. Ambient and confuser results are separate. Uncertainty is an exact Poisson 95 % interval (about 3/H if there are zero events).
- **Detection latency:** first in-window detection time minus `wake_end`, for true detections. It can be **negative** because the model may fire mid-phrase. `detection - wake_start` is also reported. Report median and 95th percentile. Detection times are quantised to the 80 ms chunk. Live latency on the Pi is measured separately.
- **Rates:** detection rate = true detections / valid positives, with a Wilson 95 % interval, per speaker and pooled.
- **Operating point (NOT frozen):** thresholds 0.10-0.95 in steps of 0.05 are swept on the development group only. The false-accepts-per-hour target used to pick the threshold (**proposed: 0.5 per hour**) will be reviewed and fixed by the team **before the final benchmark**. Then the test group is evaluated once.

## 10. Procedure: adding a recording

Run from the project folder in PowerShell. Global options such as `--data-dir` go **before** the subcommand. Nothing here records anyone; use recordings made with the speaker's consent.

1. **Create the corpus (once):**
   ```
   .venv\Scripts\python.exe -m evaluation.ingest init
   ```
2. **Record the take** following section 2, then ingest it. The original file is only read, never changed:
   ```
   .venv\Scripts\python.exe -m evaluation.ingest ingest "C:\path\to\take.wav" --clip-id POS_S01_0001 --label-class positive --session-id SES01 --recorded-date 2026-09-22 --recording-source laptop_builtin --hardware-track A --room-id R1 --environment living_room --condition quiet --speaker-id S01 --distance-m 0.5 --speaking-style normal --take-number 1 --consent-ref CONSENT-S01
   ```
   - Not 16 kHz mono 16-bit? The tool **stops and tells you what conversion is needed**. Add `--convert` to allow it (bit depth, sample rate, or keeping channel 0 of a dual-mono file).
   - Stereo with **different** channels is always rejected (never downmixed). Choose one explicitly with `--select-channel N` (0-based).
   - The tool only **proposes** phrase boundaries; a person must verify them (step 3).
3. **Verify** the boundaries: open `processed/positive/<clip_id>.wav` in an audio editor, check the proposed phrase start and end, then either:
   ```
   .venv\Scripts\python.exe -m evaluation.ingest annotate --clip-id POS_S01_0001 --annotator A01 --accept-proposal
   ```
   or give corrected times with `--start-s 1.52 --end-s 2.98`. A bad take is marked with `--invalid-reason "wrong words"`.
4. **Check the corpus:**
   ```
   .venv\Scripts\python.exe -m evaluation.ingest validate
   ```
   Use `--require-verified` to treat unverified annotations as errors (the benchmark will).

Other classes: use `--label-class negative_confuser|negative_speech|negative_media|negative_ambient`; add `--split dev|test|excluded` when there is no speaker. Synthetic audio: add `--synthetic --synthetic-method <what>` and a `SYN_` clip id.

Clip id convention: `POS_S01_0001`, `CONF_S03_0001`, `SPEECH_S01_S02_0001`, `MEDIA_0001`, `AMB_0001`, `SYN_SILENCE_0001`.

## 11. How WP2.5 will use this corpus

1. Validate the manifest with `--require-verified` (files, hashes, formats, warm-up margin, one group per speaker, synthetic excluded from headline metrics).
2. Run each clip once through the WAV source, reframer and detector, and record per-chunk score traces. Repeat 3 times because the engine's start-up state is randomised.
3. Sweep thresholds by replaying the recorded traces through the same threshold/cooldown code (scores do not depend on the threshold).
4. Choose the threshold on the development group against the reviewed false-accept target, fix it, then evaluate the test group once.
5. Report detection rate, misses, false accepts per hour (headline: speech and media only), confuser rate, ambient results, and latency, per speaker and pooled, with intervals. Track A results are preliminary; final numbers come from Track B.

## 12. Limitations to state in the final report

Five speakers from one team (probably a narrow accent range), particular rooms and equipment, and a corpus that grows incrementally. Results describe this system under these conditions and are not a general accuracy claim.
