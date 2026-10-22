"""VESTA MIDTERM DEMO 3 - LIVE VOICE-TO-REPLY RUNNER. Runs only when invoked. NOT HARDWARE-VALIDATED until a person checks it on the device.

    ReSpeakerMicrophone -> Stage B capture worker + bounded queue (scripts/ai2_stage_b_live.py, reused)
        -> the ONE VoiceAssistantRuntime pipeline: openWakeWord -> Silero VAD -> faster-whisper -> Week 8 router
        -> DeterministicAnswerEngine (room facts from the ONE ClassroomStateStore) -> the ONE OutputArbiter -> sherpa-onnx TTS -> speaker

This is the "live runner" named in docs/handoff/FATIMA_AI_HARDWARE_HANDOFF.md section 14. It builds nothing new: every engine,
the runtime, the harness and the arbiter already exist and are tested; this file only constructs the REAL ones and wires them.

Honest defaults
  * The microphone is ALWAYS the real ReSpeakerMicrophone: there is no fake-input fallback in this script (tests inject one explicitly).
  * Nothing opens at import. The microphone opens when the session starts; the output device only when something is spoken.
  * No action loop is wired: a light command gets the fixed "can't carry out device commands yet" reply (no ESP32 adapter exists).
  * No sensor source is wired: the store is empty, so room questions answer "unavailable" (or "can't answer yet" when nothing is
    declared with --declare). Nothing is invented.
  * Half-duplex is NOT enforced (no echo cancellation in software): audio captured while VESTA speaks waits in the harness queue and
    is processed afterwards. Replies never contain the wake phrase, but echo / backlog must be observed on the device (see the
    validation protocol). The processing worker is blocked while a reply plays, so backlog grows by the reply length.
  * Privacy: transcripts and answers are printed only with --show-text; the Stage B evidence record (outside the repository) is
    structural only: no transcript, no audio.

    python -m scripts.vesta_live_voice --list-devices
    python -m scripts.vesta_live_voice --out-dir <evidence dir OUTSIDE the repo> --speaker-test --duration-s 120 --output-device <index or name>

Exit codes: 0 = session ran; 2 = invalid arguments / evidence location; 3 = a failure (the message names model / microphone / tts / playback).
"""

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.answering import AnswerContext, Capability, CapabilityDeclaration
from audio.utterance import UtteranceSegmenter
from config import settings
from fusion.live_state import ClassroomStateStore
from scripts.ai2_stage_b_live import (
    MODE_MANUAL, MODE_WAKE, SOURCE_LIVE, LiveConfig, StageBError, StageBInputError, StageBLiveHarness, StageBModelError, StageBWorkerError,
    build_live_segmenter_config, load_engines, prepare_evidence_dir, write_evidence,
)

LABEL = "LIVE VOICE RUN - UNREVIEWED (NOT HARDWARE-VALIDATED until a person has checked it on the device)"
SPEAKER_TEST_TEXT = "This is the VESTA speaker test."


def list_audio_devices(out) -> int:
    """Print every PortAudio input and output device. Only queries; opens no stream."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception as exc:
        print(f"ERROR: audio runtime unavailable: {type(exc).__name__}: {exc}", file=out)
        return 3
    for i, d in enumerate(devices):
        kinds = [k for k, n in (("in", d["max_input_channels"]), ("out", d["max_output_channels"])) if n > 0]
        print(f"[{i}] {d['name']!r} {'/'.join(kinds)} in_ch={d['max_input_channels']} out_ch={d['max_output_channels']} "
              f"default_rate={d['default_samplerate']}", file=out)
    print(f"input hint used by VESTA: {settings.DEVICE_NAME_HINT!r}. DEVICE DISCOVERY IS NOT HARDWARE VALIDATION.", file=out)
    return 0


def _real_mic(hint):
    from audio.microphone import ReSpeakerMicrophone
    return ReSpeakerMicrophone(device_name_hint=hint)


def _real_tts():
    from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
    return SherpaOnnxTtsEngine()


def _real_playback(device):
    from audio.playback import SoundDevicePlaybackEngine
    return SoundDevicePlaybackEngine(device)


def _device_arg(v):
    return int(v) if v is not None and v.isdigit() else v


def declaration(names: str) -> CapabilityDeclaration:
    caps = [Capability[n.strip().upper()] for n in names.split(",") if n.strip()] if names else []
    return CapabilityDeclaration(frozenset(caps))


def build(args, *, engines_factory=load_engines, mic_factory=_real_mic, tts_factory=_real_tts, playback_factory=_real_playback,
          store=None, out=sys.stdout):
    """Construct engines -> runtime -> harness. Raises on the first failing part; returns (runtime, harness, failure stage names)."""
    from assistant.runtime import TEXT_ANSWER, VoiceAssistantRuntime

    engines = engines_factory(MODE_MANUAL if args.activation == "manual" else MODE_WAKE)    # StageBModelError on a bad model
    tts = tts_factory()
    playback = playback_factory(_device_arg(args.output_device))
    store = store or ClassroomStateStore()
    caps = declaration(args.declare)
    observer = (lambda kind, text: print(f"{kind.lower()}: {text}", file=out)) if args.show_text else None
    runtime = VoiceAssistantRuntime(detector=engines.detector, segmenter=UtteranceSegmenter(build_live_segmenter_config(engines.vad), engines.vad),
                                    stt=engines.stt, tts=tts, playback=playback, text_observer=observer,
                                    context_provider=lambda: AnswerContext(store.snapshot(), caps))

    def on_result(result, entry) -> None:
        o = runtime.handle_result(result)
        granted = o.arbitration.decision.value if o.arbitration else "-"
        played = o.output.playback_status.value if o.output and o.output.playback_status else "-"
        print(f"interaction={entry['interaction_index']} outcome={o.kind.value} intent={o.intent_category or '-'} "
              f"answer={o.answer_status or '-'} arbitration={granted} playback={played} answer_ms="
              f"{'-' if o.answer_ms is None else round(o.answer_ms, 1)}", file=out)

    harness = StageBLiveHarness(mic_factory(args.device_hint), engines, source_kind=SOURCE_LIVE,
                                config=LiveConfig(processing_queue_blocks=args.processing_queue_blocks),
                                pipeline=runtime.pipeline, on_result=on_result, on_discontinuity=runtime.on_capture_abandoned)
    return runtime, harness, engines


def manual_loop(harness, detector, stdin, duration_s=None, poll_s=0.2) -> None:
    """Enter arms the detector, q (or end of input) quits. Returns as soon as the harness finishes by itself - a microphone failure
    or the end of the source ends the session WITHOUT anyone pressing Enter. stdin is read on a daemon thread so a blocked read
    cannot hold the session open or keep the process alive."""
    lines: "queue.Queue" = queue.Queue()

    def pump() -> None:
        try:
            for line in stdin:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, name="manual-stdin", daemon=True).start()
    deadline = None if duration_s is None else time.monotonic() + duration_s
    while not harness.is_finished and (deadline is None or time.monotonic() < deadline):
        try:
            line = lines.get(timeout=poll_s)
        except queue.Empty:
            continue
        if line is None or line.strip().lower() == "q":
            return
        detector.arm()


def main(argv=None, *, out=sys.stdout, err=sys.stderr, stdin=None, **factories) -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.vesta_live_voice", description="Live voice-to-reply runner (VESTA Demo 3).")
    p.add_argument("--list-devices", action="store_true", help="list audio input/output devices and exit (opens nothing)")
    p.add_argument("--out-dir", help="Stage B evidence directory OUTSIDE the repository (required for a session)")
    p.add_argument("--duration-s", type=float, default=None, help="stop after this many seconds (default: Ctrl+C / q)")
    p.add_argument("--activation", choices=("wake", "manual"), default="wake", help="manual = DEVELOPMENT key-press activation")
    p.add_argument("--device-hint", default=settings.DEVICE_NAME_HINT, help="input device name substring")
    p.add_argument("--output-device", default=None, help="output device index or name substring (default: platform default = NOT confirmed project speaker)")
    p.add_argument("--declare", default="", help="comma-separated answering capabilities to declare, e.g. TEMPERATURE,HUMIDITY (default none)")
    p.add_argument("--speaker-test", action="store_true", help="speak one fixed sentence through the arbiter before listening")
    p.add_argument("--show-text", action="store_true", help="print transcripts and answers (default: never)")
    p.add_argument("--processing-queue-blocks", type=int, default=LiveConfig.processing_queue_blocks)
    args = p.parse_args(argv)
    if args.list_devices:
        return list_audio_devices(out)
    try:
        if not args.out_dir:
            raise StageBInputError("--out-dir is required (a directory OUTSIDE the repository)")
        if args.duration_s is not None and not args.duration_s > 0:
            raise StageBInputError("--duration-s must be > 0")
        declaration(args.declare)
        out_dir = prepare_evidence_dir(args.out_dir)
    except (StageBInputError, KeyError) as exc:
        print(f"ERROR: {exc}", file=err)
        return 2
    if args.output_device is None:
        print("WARNING: --output-device not given: the platform default output is a development convenience, not the confirmed project speaker", file=err)

    from audio.microphone import DeviceNotFoundError
    from audio.playback import PlaybackError
    from speech.tts import TtsError
    print(f"[{LABEL}]", file=out)
    try:
        runtime, harness, engines = build(args, out=out, **factories)
        if args.speaker_test:
            o = runtime.arbiter.submit_answer(SPEAKER_TEST_TEXT, request_id="speaker-test-1")
            status = o.output.playback_status.value if o.granted and o.output and o.output.playback_status else "NOT_PLAYED"
            print(f"speaker_test arbitration={o.arbitration.decision.value} playback={status} (a person must confirm it was HEARD)", file=out)
        harness.start()
        try:
            if engines.activation_mode == MODE_MANUAL:
                print("MANUAL DEVELOPMENT ACTIVATION: press Enter to activate, q + Enter to quit.", file=out)
                manual_loop(harness, engines.detector, stdin or sys.stdin, args.duration_s)
            else:
                harness.wait(args.duration_s)
        except KeyboardInterrupt:
            pass
        finally:
            runtime.stop_output()
        report = harness.stop()
        code = 0
    except StageBModelError as exc:
        print(f"ERROR: model failure ({exc})", file=err)
        return 3
    except DeviceNotFoundError as exc:
        print(f"ERROR: microphone not found: {exc}", file=err)
        return 3
    except TtsError as exc:
        print(f"ERROR: tts failure: {type(exc).__name__}: {exc}", file=err)
        return 3
    except PlaybackError as exc:
        print(f"ERROR: playback failure: {type(exc).__name__}: {exc}", file=err)
        return 3
    except StageBWorkerError as exc:
        report, code = exc.report, 3
        print(f"ERROR: {exc}", file=err)
    except KeyboardInterrupt:                    # before listening started (model load / speaker test): nothing was captured
        print("interrupted before the session started; no evidence record", file=err)
        return 3
    try:
        path = write_evidence(report, out_dir)
    except StageBError as exc:
        print(f"ERROR: {exc}", file=err)
        return 2
    c = report.get("continuity") or {}
    print(f"session_id={report['session_id']} interactions={report['interaction_count']} exact_continuity={c.get('exact_continuity')} "
          f"discontinuities={c.get('discontinuity_count')} evidence={path}", file=out)
    return code


if __name__ == "__main__":
    sys.exit(main())
