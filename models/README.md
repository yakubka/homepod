# Models

Model files are never committed, git ignores everything here except this file.

## Registered models

Every model the room runs is declared in `services/core/models.py` and loaded only through the
registry there. Download the exact pinned files, hashes checked, with:

```bash
python eval/fetch_models.py
```

| Id | Path in this directory | Backend |
|---|---|---|
| wakeword | `openwakeword/` melspectrogram, embedding and `hey_jarvis_v0.1` | ONNX Runtime |
| vad | `silero_vad_v6.2.3.onnx` | ONNX Runtime |
| stt | `faster-whisper-tiny.en/` | CTranslate2 |
| tts | `vits-piper-en_US-lessac-medium/` | sherpa-onnx |
| detector | `yolox_tiny.onnx` | ONNX Runtime |

Set `VESTA_MODELS_DIR` to keep them somewhere else. Memory and core allowances per model are in
`docs/budget.md`, measured numbers in `eval/reports/models.md`.

## Legacy person detection model

The prototype detector in `services/vision/detector.py` still uses the model below until the vision
lane moves to the registry.

MobileNet SSD in Caffe format, trained on COCO. Person is class id 15.

## Setup on Raspberry Pi

```bash
cd models
wget https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt -O MobileNetSSD_deploy.prototxt
wget https://drive.google.com/uc?id=0B3gersZ2cHIxRm5PMWRoTkdHdHc -O MobileNetSSD_deploy.caffemodel
```

## Why this model

Runs on the Pi 4 CPU at roughly 5 to 8 frames per second, weighs about 23 MB and needs no
accelerator. Accuracy is good enough to tell whether a room is occupied.

## Alternatives

YOLOv8n with a Coral USB accelerator gives about 30 frames per second but needs the external TPU.
MediaPipe Pose returns a full skeleton and is too heavy for the Pi.
