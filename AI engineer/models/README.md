# Required AI Models

Large model binaries are intentionally excluded from Git.

The Hardware Engineer must obtain/install the required models locally
before running the complete AI subsystem.

Expected model assets include:

## VAD
Silero VAD v6.2.3

Expected project filename:

    silero_vad_v6.2.3.onnx

## STT
Faster-Whisper tiny.en

Expected local model directory:

    faster-whisper-tiny.en-0d3d19a3/

The STT backend verifies the locally installed model snapshot.

## TTS
Sherpa-ONNX with the Piper-format en_US-lessac-medium voice.

Expected local directory pattern:

    vits-piper-en_US-lessac-medium-9e3febfa/

The committed voice manifest is located at:

    ../speech/voices/vits-piper-en_US-lessac-medium.manifest.json

The TTS voice is an academic/non-commercial development asset and is
not redistributed through this repository.

## Vision
YOLOX-Tiny ONNX model is required by the vision subsystem.

Do not rename model files or change expected paths without checking the
corresponding backend/configuration.

## Important

Do not commit downloaded model binaries to this repository.

Model provenance, hashes and exact runtime checks are defined by the
corresponding AI modules/manifests where available.
