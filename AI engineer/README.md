# VESTA AI Engineer Subsystem

This folder contains the AI/voice and perception software developed by the AI Engineer.

## Current software capabilities

- Audio acquisition abstraction
- Wake-word processing
- VAD and utterance segmentation
- Offline Faster-Whisper STT
- Deterministic classroom question answering
- Offline TTS
- Playback abstraction
- Announcement and fallback orchestration
- Output arbitration / no-overlap policy
- LISTENING / CHECKING / SPEAKING state contract
- Vision counting and zone-processing foundations

## Setup

Create a local Python environment:

    python -m venv .venv

Windows:

    .venv\Scripts\activate

Install dependencies:

    pip install -r requirements.txt

## Models

Model binaries are intentionally not stored in GitHub.

See:

    models/README.md

for required model assets and their expected locations.

## Validation status

Software test baseline before GitHub transfer:

    1923 passed
    6 skipped
    0 failed

This is software validation only.

Hardware-dependent validation is still required for:

- live project microphone
- Raspberry Pi target
- project speaker
- classroom distance/noise tests
- announcement audibility
- conversation/announcement overlap on real hardware
- RED / BLUE / GREEN Face or LED synchronization

## Hardware Engineer

Clone or pull this repository, create your own virtual environment,
install requirements, obtain the required model files, and then perform
the hardware integration and validation.

Do not treat simulated/software-only results as hardware validation.
