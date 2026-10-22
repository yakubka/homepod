"""VESTA PRE-HARDWARE SELF-CHECK. READ-ONLY. DEVICE DISCOVERY IS NOT HARDWARE VALIDATION.

Reports the platform, Python, optional packages, audio input/output devices, camera device nodes, candidate ESP32 serial ports, local
model files (size + SHA-256 against the recorded pins) and a readiness line per subsystem. It never opens an audio stream, a camera or a
serial port, sends no command to any actuator, downloads nothing, changes no configuration and needs no privileges.

    python -m scripts.vesta_preflight
    python -m scripts.vesta_preflight --json          # one JSON object (save it as evidence, outside the repository)

Exit code: 0 = the report was produced (whatever it says). Readiness words: READY_TO_TEST (prerequisites found; NOT validated),
MISSING (a named prerequisite is absent), NOT_AVAILABLE (no software path exists yet: a team dependency).
"""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
OPTIONAL = ("numpy", "onnxruntime", "openwakeword", "faster_whisper", "sherpa_onnx", "sounddevice", "PIL", "cv2", "picamera2", "serial", "psutil")
# recorded pins (WEEK4_REPORT.md, audio/silero_vad.py); the whisper snapshot and the voice are checked by their own verifiers
MODEL_PINS = {
    "models/silero_vad_v6.2.3.onnx": "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
    "models/yolox_tiny.onnx": "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def packages() -> dict:
    return {name: importlib.util.find_spec(name) is not None for name in OPTIONAL}


def audio_devices() -> dict:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200], "inputs": [], "outputs": []}
    inputs = [{"index": i, "name": d["name"], "channels": d["max_input_channels"], "default_rate": d["default_samplerate"],
               "supports_16k_mono_int16": _format_ok(sd.check_input_settings, i, 16000)} for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    outputs = [{"index": i, "name": d["name"], "channels": d["max_output_channels"], "default_rate": d["default_samplerate"],
                "supports_22050_mono_int16": _format_ok(sd.check_output_settings, i, 22050)} for i, d in enumerate(devices) if d["max_output_channels"] > 0]
    return {"error": None, "inputs": inputs, "outputs": outputs}


def _format_ok(check, index, rate) -> bool:
    """PortAudio's format query (Pa_IsFormatSupported): opens no stream. 16 kHz mono int16 is what VESTA captures; 22,050 Hz mono is the voice."""
    try:
        check(device=index, samplerate=rate, channels=1, dtype="int16")
        return True
    except Exception:
        return False


def host(root: Path = Path("/")) -> dict:
    """Board model, CPU count, memory and load. Linux files only (read-only); other platforms report None."""
    def read(rel):
        try:
            return (root / rel).read_text(encoding="utf-8", errors="replace").replace(chr(0), "").strip()
        except OSError:
            return None
    mem = {}
    for line in (read("proc/meminfo") or "").splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            mem[key] = int(rest.split()[0]) // 1024
    load = read("proc/loadavg")
    return {"board_model": read("proc/device-tree/model"), "cpu_count": os.cpu_count(), "mem_total_mb": mem.get("MemTotal"),
            "mem_available_mb": mem.get("MemAvailable"), "loadavg_1m": float(load.split()[0]) if load else None}


# camera classification from sysfs (read-only). Pi 5 also exposes ISP / codec nodes (pispbe, rpivid, ...) that are NOT cameras.
_UVC_DRIVERS = {"uvcvideo"}
_PI_CAMERA_DRIVERS = {"rp1-cfe", "unicam", "bcm2835-unicam"}


def camera_nodes_detail(root: Path = Path("/")) -> list:
    out = []
    for node in sorted((root / "sys/class/video4linux").glob("video*")):
        try:
            name = (node / "name").read_text(encoding="utf-8").strip()
        except OSError:
            name = None
        driver = None
        try:                                            # DRIVER= in the device's uevent, else the driver symlink's target name
            driver = next((l[7:].strip() for l in (node / "device" / "uevent").read_text(encoding="utf-8").splitlines() if l.startswith("DRIVER=")), None)
        except OSError:
            pass
        if driver is None and (node / "device" / "driver").exists():
            driver = os.path.basename(os.path.realpath(node / "device" / "driver"))
        kind = "USB_UVC" if driver in _UVC_DRIVERS else "PI_CSI_CAMERA_RECEIVER" if driver in _PI_CAMERA_DRIVERS else "OTHER_NOT_A_USB_CAMERA"
        out.append({"node": "/dev/" + node.name, "name": name, "driver": driver, "kind": kind})
    return out


# USB-serial vendor ids (public USB-IF assignments). A match names the BRIDGE; it does not prove the board runs VESTA firmware.
_USB_VENDORS = {"303a": "Espressif native USB (ESP32-S3 USB-Serial/JTAG or CDC)", "10c4": "Silicon Labs CP210x bridge",
                "1a86": "WCH CH34x bridge", "0403": "FTDI bridge"}


def serial_detail(root: Path = Path("/")) -> list:
    out = []
    tty_dir = root / "sys/class/tty"
    for tty in sorted([*tty_dir.glob("ttyACM*"), *tty_dir.glob("ttyUSB*")]):
        vid = pid = None
        d = Path(os.path.realpath(tty / "device"))
        for parent in (d, *d.parents):
            if (parent / "idVendor").is_file():
                vid = (parent / "idVendor").read_text(encoding="utf-8").strip().lower()
                pid = (parent / "idProduct").read_text(encoding="utf-8").strip().lower() if (parent / "idProduct").is_file() else None
                break
        out.append({"port": "/dev/" + tty.name, "vid": vid, "pid": pid, "vendor": _USB_VENDORS.get(vid, "unknown")})
    return out


def requirement_versions(path: Path = REPO / "requirements.txt") -> dict:
    """Pinned requirement -> installed version (or MISSING). Records what the Pi install actually produced."""
    from importlib import metadata
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name, _, pin = line.partition("==")
        name = name.strip()
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = "MISSING"
        out[name] = {"pinned": pin.strip() or None, "installed": installed}
    return out


def device_nodes(patterns) -> list:
    dev = Path("/dev")
    return sorted({str(p) for pat in patterns for p in dev.glob(pat)}) if dev.is_dir() else []


def serial_candidates() -> list:
    found = device_nodes(("ttyUSB*", "ttyACM*", "serial/by-id/*"))
    if not found and importlib.util.find_spec("serial") is not None:
        from serial.tools import list_ports          # enumerates only; opens nothing
        found = [p.device for p in list_ports.comports()]
    return found


def models() -> dict:
    out = {}
    for rel, pin in MODEL_PINS.items():
        p = REPO / rel
        out[rel] = "MISSING" if not p.is_file() else ("OK" if _sha256(p) == pin else "HASH_MISMATCH")
    try:
        from speech.faster_whisper_backend import resolve_model_dir, verify_model_snapshot
        verify_model_snapshot(resolve_model_dir())
        out["faster-whisper tiny.en"] = "OK"
    except Exception as exc:
        out["faster-whisper tiny.en"] = f"FAIL {type(exc).__name__}"
    try:
        from speech.sherpa_onnx_tts import load_manifest, resolve_voice_dir, verify_voice_directory
        m = load_manifest()
        verify_voice_directory(resolve_voice_dir(m), m)
        out["lessac voice"] = "OK"
    except Exception as exc:
        out["lessac voice"] = f"FAIL {type(exc).__name__}"
    missing = oww_missing_files()
    out["openwakeword hey_jarvis"] = "PRESENT (not hash-verified by VESTA)" if not missing else "MISSING: " + ", ".join(missing)
    return out


# the ONNX files VESTA's backend loads (inference_framework="onnx"): the wake model plus the two shared feature models
OWW_REQUIRED = ("hey_jarvis*.onnx", "melspectrogram.onnx", "embedding_model.onnx")


def oww_missing_files(locations=None) -> list:
    """Required openWakeWord model files that are NOT present as non-empty files. Package installed alone is never enough.
    `locations` defaults to the installed package's directories (no import of openwakeword itself)."""
    if locations is None:
        spec = importlib.util.find_spec("openwakeword")
        locations = list(spec.submodule_search_locations or []) if spec is not None else []
    if not locations:
        return ["openwakeword package"]
    models = [Path(loc, "resources", "models") for loc in locations]
    return [pat for pat in OWW_REQUIRED
            if not any(p.is_file() and p.stat().st_size > 0 for d in models for p in d.glob(pat))]


def readiness(pkgs, audio, cams, serial, mdl, cam_detail=None) -> dict:
    from config import settings

    def need(*missing):
        missing = [m for m in missing if m]
        return "READY_TO_TEST" if not missing else "MISSING: " + ", ".join(missing)

    hint = settings.DEVICE_NAME_HINT.lower()
    mic = any(hint in d["name"].lower() and d.get("supports_16k_mono_int16", True) for d in audio["inputs"])
    uvc = [c for c in (cam_detail or []) if c["kind"] == "USB_UVC"]
    csi = [c for c in (cam_detail or []) if c["kind"] == "PI_CSI_CAMERA_RECEIVER"]
    voice_models = all(mdl[k] == "OK" for k in ("models/silero_vad_v6.2.3.onnx", "faster-whisper tiny.en")) and mdl["openwakeword hey_jarvis"].startswith("PRESENT")
    return {
        "microphone_voice_input": need(not pkgs["sounddevice"] and "sounddevice",
                                       not mic and f"input device matching {settings.DEVICE_NAME_HINT!r} that accepts 16 kHz mono int16",
                                       not voice_models and "verified wake/VAD/STT models"),
        "spoken_reply_output": need(not pkgs["sherpa_onnx"] and "sherpa_onnx", mdl["lessac voice"] != "OK" and "verified voice",
                                    not audio["outputs"] and "an output device"),
        "camera_person_detection": need(not pkgs["cv2"] and "cv2 (OpenCV, not installed; needs approval)",
                                        not (uvc if cam_detail is not None else cams) and "a USB/UVC camera node",
                                        mdl["models/yolox_tiny.onnx"] != "OK" and "verified yolox_tiny.onnx"),
        "csi_camera": ("NOT_SUPPORTED_BY_RUNNER: a Pi camera receiver is present; needs a Picamera2 capture object (python3-picamera2, "
                       "not written)" if csi else "none detected"),
        "esp32_link": ("NOT_AVAILABLE: no Pi-side adapter (wire protocol not agreed); candidate ports: " + (", ".join(serial) or "none")),
        "temperature_humidity_lux": "NOT_AVAILABLE: needs the ESP32 adapter and the confirmed sensor message format",
        "light_on_off": "NOT_AVAILABLE: needs the ESP32 adapter, device mapping and safety approval; ExecutionResult refuses REAL today",
        "mobile_app": "NOT_AVAILABLE: transport-free contract only (assistant/app_interface.py); no network service agreed",
    }


def collect() -> dict:
    pkgs, audio = packages(), audio_devices()
    cams, serial, mdl = device_nodes(("video*",)), serial_candidates(), models()
    cam_detail = camera_nodes_detail() if Path("/sys/class/video4linux").is_dir() else None
    return {"label": "DEVICE DISCOVERY IS NOT HARDWARE VALIDATION",
            "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                         "python": platform.python_version(), "executable_in_venv": sys.prefix != sys.base_prefix},
            "python_supported": sys.version_info[:2] >= (3, 11), "host": host(), "requirements": requirement_versions(), "packages": pkgs,
            "audio": audio, "camera_nodes": cams, "camera_detail": cam_detail, "serial_candidates": serial,
            "serial_detail": serial_detail() if Path("/sys/class/tty").is_dir() else None, "models": mdl,
            "readiness": readiness(pkgs, audio, cams, serial, mdl, cam_detail)}


def main(argv=None, *, out=sys.stdout, collect_fn=collect) -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.vesta_preflight", description="Read-only VESTA pre-hardware self-check.")
    p.add_argument("--json", action="store_true", help="print one JSON object")
    args = p.parse_args(argv)
    report = collect_fn()
    if args.json:
        print(json.dumps(report, indent=2), file=out)
        return 0
    print(report["label"], file=out)
    for section in ("platform", "host", "requirements", "packages", "models", "readiness"):
        print(f"[{section}]", file=out)
        for k, v in report[section].items():
            print(f"  {k}: {v}", file=out)
    print(f"[audio] error={report['audio']['error']}", file=out)
    for kind in ("inputs", "outputs"):
        for d in report["audio"][kind]:
            fmt = d.get("supports_16k_mono_int16", d.get("supports_22050_mono_int16"))
            print(f"  {kind[:-1]} [{d['index']}] {d['name']!r} ch={d['channels']} default_rate={d.get('default_rate')} vesta_format_ok={fmt}", file=out)
    print(f"[camera_nodes] {report['camera_nodes'] or 'none'}", file=out)
    for c in report.get("camera_detail") or []:
        print(f"  {c['node']} name={c['name']!r} driver={c['driver']} kind={c['kind']}", file=out)
    print(f"[serial_candidates] {report['serial_candidates'] or 'none'}", file=out)
    for t in report.get("serial_detail") or []:
        print(f"  {t['port']} vid={t['vid']} pid={t['pid']} {t['vendor']}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
