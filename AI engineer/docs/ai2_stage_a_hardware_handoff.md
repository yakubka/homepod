# Team Leader AI-2, Stage A: what the AI side needs from the Hardware Engineer

Status: **harness READY, real Stage A run NOT YET PERFORMED.** No Hardware Engineer recording has been processed. This is a request list, not a result.

## Why

AI-2 asks us to integrate the new microphone. Stage A is the first step: your recordings go through the real VESTA software (segmentation, then speech recognition) and the transcript is kept with its provenance. Stage A is called **RECORDED-HARDWARE AUDIO**. It does not prove the live microphone works; that is Stage B.

## 1. What to record first

One short, clean recording is enough to start. Please make it with the **project microphone, on the project host** (Raspberry Pi 5 if that is what will run VESTA), not a phone or laptop.

- Speak **one phrase per file**, with a short silence before and after.
- Ideally read one of the project test questions aloud, for example "How many people are in the room?" or "What is the temperature in the room?". Tell us exactly what was said.
- Later (for AI-3) the same phrases at **1 m, 3 m and the back of the room**, and with background noise. Do not wait for those before sending the first clean file.
- Optional but useful: one **negative control**, a recording with **no intended speech** (room silence or background noise only). Tell us it is a negative control.

## 2. File format

**Preferred:** WAV, PCM, **16 kHz, mono (1 channel), 16-bit**.

If the hardware or your tool records something else (48 kHz, stereo, 24-bit, several channels): **keep the original file exactly as recorded** and send it. Do not convert it and delete the original. If you also make a converted copy, send both, say which channel you took and how you converted it. The AI harness will refuse anything that is not 16 kHz / mono / 16-bit and will never resample or mix channels for you.

Do not edit the audio (no trimming, noise reduction or normalising) unless you tell us what you did.

## 3. Information we need for each recording

Fill in one provenance file per recording (copy `evaluation/ai2_stage_a_provenance_template.json`). Write **UNKNOWN** for anything you do not know; do not guess. The one exception is `consent_reference`: it cannot be UNKNOWN (see the table).

| Field | What to put |
|---|---|
| `recording_id` | a short code, for example `HW-001`. Letters, digits, `.`, `_`, `-` only. **No personal names.** |
| `speaker_code` | a code such as `S01`, not a name (the key from code to person stays private) |
| `consent_reference` | **required for every recording of a person**: the explicit project consent / approval marker for this speaker, for example `CONSENT-S01`. `UNKNOWN`, empty and `NOT_APPLICABLE` are refused. If you do not have it, do not send the recording yet. (Only generated test audio, `recording_origin` `SYNTHETIC`, uses `NOT_APPLICABLE`.) |
| `recording_origin` | `HARDWARE_ENGINEER_RECORDING` |
| `recording_kind` | `SPEECH`, or `NEGATIVE_CONTROL` |
| `recorded_at` | date and time, ISO 8601 (for example `2026-10-14T15:30:00+09:00`) |
| `microphone` | exact microphone model (and firmware if known) |
| `recording_host` | the device that captured it (for example Raspberry Pi 5) |
| `distance_m` | speaker to microphone distance in metres |
| `room_noise_condition` | for example quiet, fan on, people talking |
| `source_filename` | the original file name |
| `declared_format` | sample rate, channels, bit depth you believe the file has (the harness checks it) |
| `processing_history` | list of anything done to the file (`[]` if nothing) |
| `reference_transcript` | exactly what was said (leave out for a negative control) |
| `conversion` | only if you made a converted copy: what you did, the original file name, and the original's SHA-256 |

To get a SHA-256 on Windows: `certutil -hashfile <file> SHA256`; on Linux: `sha256sum <file>`.

## 4. Where files go

Recordings and results contain voices and transcripts, which are personal data. **Do not put them in the repository folder or in a cloud-synced folder.** Send them through the agreed channel and keep them in an external folder; the harness refuses an output folder inside the repository. File names must not contain personal names.

## 5. What happens next (AI side)

```
python -m scripts.ai2_stage_a_transcribe run --wav <recording.wav> --provenance <recording.prov.json> --out-dir <external evidence folder>
python -m scripts.ai2_stage_a_transcribe review <recording_id>.stage_a.json --human-match MATCH|PARTIAL|MISMATCH|EMPTY
```

The first command writes one evidence record (never overwriting an existing one). A person then listens and records whether the transcript matches. Segmentation values used in Stage A are development values and are not calibrated; results from one or two recordings say nothing about accuracy.
