#!/usr/bin/env python3
"""Download the exact model files declared in services/core/models.py.

Sources and hashes are the ones pinned by the AI lanes. Files land in
models.dir, ./models by default, which git ignores.

Usage: python eval/fetch_models.py [--dir models]
"""

import argparse
import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    ("https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx",
     "silero_vad_v6.2.3.onnx", "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"),
    ("https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx",
     "openwakeword/melspectrogram.onnx", "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f"),
    ("https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx",
     "openwakeword/embedding_model.onnx", "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f"),
    ("https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx",
     "openwakeword/hey_jarvis_v0.1.onnx", "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb"),
    ("https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx", "yolox_tiny.onnx", "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-lessac-medium.tar.bz2",
     "vits-piper-en_US-lessac-medium.tar.bz2", "9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e"),
]
WHISPER = ("Systran/faster-whisper-tiny.en", "0d3d19a32d3338f10357c0889762bd8d64bbdeba", "faster-whisper-tiny.en")


def fetch(out):
    for url, name, sha in FILES:
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            print(f"downloading {name}")
            urllib.request.urlretrieve(url, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if sha and digest != sha:
            target.unlink()
            raise SystemExit(f"{name}: sha256 {digest} does not match the pinned {sha}")
        print(f"{name} sha256 {digest[:16]} {'verified' if sha else 'recorded'}")

    voice = out / "vits-piper-en_US-lessac-medium"
    if not voice.exists():
        with tarfile.open(out / "vits-piper-en_US-lessac-medium.tar.bz2") as tar:
            root = out.resolve()
            members = tar.getmembers()
            for member in members:
                if member.issym() or member.islnk() or not (out / member.name).resolve().is_relative_to(root):
                    raise SystemExit(f"unsafe archive member {member.name}")
            tar.extractall(out, members=members)

    from huggingface_hub import snapshot_download

    repo, revision, folder = WHISPER
    snapshot_download(repo, revision=revision, local_dir=out / folder)
    print(f"{repo}@{revision[:8]} ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=str(ROOT / "models"))
    fetch(Path(parser.parse_args().dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
