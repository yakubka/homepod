"""VESTA RUNTIME DEMO - DEVELOPMENT, SOFTWARE ONLY, DETERMINISTIC. Not production code. Not a live voice assistant.

Runs the whole software chain once through assistant/runtime.py:

    scripted FAKE wake word -> FAKE energy VAD -> FAKE scripted STT -> the REAL intent router -> real deterministic answering (queries)
        or a validated StructuredCommand that is NOT executed -> real output arbiter
        -> TTS (a FAKE tone by default; --tts real uses the real local voice, SYNTHESIS ONLY) -> FAKE playback

and prints, for four scripted utterances, what the pipeline decided, which intent category the router chose, what the answer engine said (a
question) or which structured command was produced and NOT executed (an action request) or why the request was rejected, what the arbiter
did and which assistant states (RED / BLUE / GREEN) were published. Everything is generated in memory: no microphone, no loudspeaker, no
recording, no network, no device. The four requests are SIMULATED inputs; room information is deliberately UNAVAILABLE (no sensor source
exists). NO COMMAND IS EVER EXECUTED: the action request shows the command the runtime hands back, with execution_status NOT_EXECUTED.

Transcripts and answer texts are printed only with --show-text (the scripted text is not personal data, but the default keeps the
same privacy posture as the rest of the project).

    python -m scripts.vesta_runtime_demo
    python -m scripts.vesta_runtime_demo --show-text
    python -m scripts.vesta_runtime_demo --tts real        # needs the local voice files (see the hand-off documents); playback stays fake

Exit codes: 0 = all four scripted interactions ran as expected; 1 = an interaction behaved differently; 3 = the real voice could not be loaded.
"""

import argparse
import sys
import threading
from pathlib import Path

if __name__ == "__main__":      # allow `python scripts/vesta_runtime_demo.py` as well as `python -m scripts....`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from assistant.answering import AnswerContext, Capability, CapabilityDeclaration
from assistant.runtime import TEXT_ANSWER, TEXT_TRANSCRIPT, OutcomeKind, VoiceAssistantRuntime
from assistant.state import AssistantStateManager
from audio.playback import PlaybackEngine, PlaybackResult, PlaybackStatus
from audio.utterance import SegmenterConfig, UtteranceSegmenter
from audio.vad import VadEngine
from speech.stt import SttEngine
from speech.tts import TtsEngine
from wakeword.detector import WakeWordDetector

RATE = 16000
BLOCK = 1024
WAKE_CHUNK = 1280
LABEL = "AI END-TO-END COMPOSITION: SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT   |   REAL LIVE VOICE ASSISTANT: NOT VALIDATED"

SCRIPT = (       # (what the fake STT "hears", the outcome kind the demo expects, and the detail that must match)
    ("hey jarvis what can you do", "ANSWER_OUTPUT", "ANSWERED"),                          # A. a spoken question -> routed -> a truthful answer
    ("hey jarvis how many people are in the room", "ANSWER_OUTPUT", "INFORMATION_UNAVAILABLE"),
    ("hey jarvis turn on the lights", "COMMAND_NOT_EXECUTED", "TURN_ON"),                 # B. an action request -> a structured command, NOT executed
    ("hey jarvis turn on the projector", "REQUEST_REJECTED", "UNSUPPORTED"),              # C. an unsupported request -> an explicit rejection
)
PERIOD = 6 * RATE                                  # one utterance every 6 s of stream
SPEECH = (1 * RATE, int(2.5 * RATE))               # loud region inside each period
WAKE_CHUNK_IN_PERIOD = 18                          # the scripted wake ends at chunk 19 = 1.52 s, inside the speech


# ---------------------------------------------------------------------------------------------------
# DEMO DOUBLES: deterministic, clearly fake
# ---------------------------------------------------------------------------------------------------

class ScriptedWake(WakeWordDetector):
    """A real WakeWordDetector (real threshold / cooldown / end_sample logic) with scripted scores. NOT a wake model."""

    sample_rate = RATE
    chunk_samples = WAKE_CHUNK

    def __init__(self, fire):
        self.fire = set(fire)
        super().__init__(threshold=0.5, cooldown_s=0.0)

    def _score_chunk(self, chunk):
        return {"hey_jarvis": 0.9 if self._chunks_processed in self.fire else 0.0}

    def _reset_backend(self):
        pass


class EnergyVad(VadEngine):
    """0.9 when every sample of the 512-sample frame is loud, else 0. NOT a speech model."""

    sample_rate = RATE
    frame_samples = 512

    def _score_frame(self, frame):
        return 0.9 if int(frame.min()) >= 1000 else 0.0

    def reset(self):
        pass


class ScriptedStt(SttEngine):
    """Returns the scripted sentences in order. NOT a recogniser."""

    sample_rate = RATE
    engine_id = "demo-scripted-stt"
    model_id = "demo-scripted"

    def __init__(self, sentences):
        super().__init__()
        self._sentences, self.calls = list(sentences), 0

    def _transcribe(self, samples):
        self.calls += 1
        return self._sentences[min(self.calls, len(self._sentences)) - 1]


class DemoTts(TtsEngine):
    """A deterministic tone whose length depends on the text length. NOT speech."""

    sample_rate = 22050
    engine_id = "demo-tone-tts"
    voice_id = "demo-tone"
    language = "en-US"

    def _synthesize(self, text):
        n = 2205 + 40 * len(text)
        return ((np.arange(n) % 64 - 32) * 30 + 1).astype(np.int16)


class DemoPlayback(PlaybackEngine):
    """Plays nothing. It reports the first buffer where a real engine would, so the SPEAKING state follows the real rule."""

    def __init__(self):
        self._listener, self._playing, self.calls = None, False, 0

    @property
    def supports_first_buffer_listener(self):
        return True

    def set_first_buffer_listener(self, listener):
        self._listener = listener

    def play(self, audio):
        self.calls += 1
        self._playing = True
        try:
            if self._listener is not None:
                self._listener()
            return PlaybackResult(PlaybackStatus.COMPLETED, audio.duration_s, 1.0, 1.01, 1.02, 1.02 + audio.duration_s, "demo-device", "demo-api", 0.0, 0)
        finally:
            self._playing = False

    def stop(self):
        return False

    @property
    def is_playing(self):
        return self._playing


def build_stream() -> np.ndarray:
    x = np.zeros(PERIOD * len(SCRIPT), np.int16)
    for k in range(len(SCRIPT)):
        a, b = k * PERIOD + SPEECH[0], k * PERIOD + SPEECH[1]
        x[a:b] = (2000 + np.arange(a, b) % 1000).astype(np.int16)
    return x


def segmenter_config() -> SegmenterConfig:
    # DEVELOPMENT values for this demo only (the scripted VAD makes them irrelevant to any real audio); config/settings.py is unchanged
    return SegmenterConfig(sample_rate=RATE, vad_frame_samples=512, start_threshold=0.5, end_threshold=0.35, start_confirmation_s=0.1,
                           end_silence_s=0.7, onset_padding_s=0.1, pre_roll_s=2.0, no_speech_timeout_s=10.0, min_utterance_s=0.25, max_utterance_s=20.0)


def main(argv=None, *, out=None, err=None, tts=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    parser = argparse.ArgumentParser(prog="vesta_runtime_demo", description="VESTA runtime demo (fake input/output, deterministic)")
    parser.add_argument("--tts", choices=("fake", "real"), default="fake")
    parser.add_argument("--show-text", action="store_true", help="also print the scripted transcripts and the answers")
    args = parser.parse_args(argv)

    if tts is None:
        if args.tts == "real":
            try:
                from speech.sherpa_onnx_tts import SherpaOnnxTtsEngine
                tts = SherpaOnnxTtsEngine()
            except Exception as exc:      # a missing / unverified voice, a blocked runtime: say so and stop (nothing is substituted)
                print(f"ERROR: the real voice could not be loaded ({type(exc).__name__}); nothing was substituted", file=err)
                return 3
        else:
            tts = DemoTts()

    shown = []
    state = AssistantStateManager()
    events = []
    state.subscribe(events.append, replay_current=True)
    wake = ScriptedWake(fire=[WAKE_CHUNK_IN_PERIOD + k * (PERIOD // WAKE_CHUNK) for k in range(len(SCRIPT))])
    playback = DemoPlayback()
    caps = CapabilityDeclaration(frozenset({Capability.PEOPLE_COUNT, Capability.TEMPERATURE, Capability.HUMIDITY, Capability.BRIGHTNESS,
                                            Capability.ANNOUNCEMENTS}))
    runtime = VoiceAssistantRuntime(
        detector=wake, segmenter=UtteranceSegmenter(segmenter_config(), EnergyVad()), stt=ScriptedStt([row[0] for row in SCRIPT]),
        tts=tts, playback=playback, state=state,
        context_provider=lambda: AnswerContext(classroom_state=None, capabilities=caps),     # no sensor source exists: room facts are UNAVAILABLE
        text_observer=(lambda kind, text: shown.append((kind, text))) if args.show_text else None)

    print("[VESTA RUNTIME DEMO - DEVELOPMENT - SIMULATED input, FAKE wake/VAD/STT, FAKE playback]", file=out)
    print(LABEL, file=out)
    x = build_stream()
    outcomes = []
    for lo in range(0, x.size, BLOCK):
        outcomes += runtime.feed(x[lo:lo + BLOCK])
    outcomes += runtime.finish()

    ok = len(outcomes) == len(SCRIPT)
    for i, o in enumerate(outcomes, 1):
        _, kind, detail = SCRIPT[i - 1] if i <= len(SCRIPT) else (None, None, None)
        got = {"ANSWER_OUTPUT": o.answer_status, "COMMAND_NOT_EXECUTED": o.command.action.value if o.command else None,
               "REQUEST_REJECTED": o.routing_status}.get(o.kind.value)
        good = (o.kind.value == kind and got == detail and o.output is not None and o.output.status.value == "COMPLETED"
                and o.execution_status == "NOT_EXECUTED" and o.spoken_confirmation_allowed is False)
        ok = ok and good
        command = f"{o.command.action.value}/{o.command.target.value}" if o.command else "-"
        print(f"interaction {i}: pipeline={o.pipeline_status.value} outcome={o.kind.value} category={o.intent_category} routing={o.routing_status} "
              f"handling={o.handling} command={command} execution={o.execution_status} confirmation_allowed={o.spoken_confirmation_allowed} "
              f"answer={o.answer_status} intent={o.intent} source={o.answer_source} arbiter={o.arbitration.decision.value if o.arbitration else '-'} "
              f"output={o.output.status.value if o.output else '-'} {'OK' if good else 'UNEXPECTED'}", file=out)
    print("states: " + " -> ".join(f"{e.state.value}({e.color_semantic.value})" for e in events), file=out)
    print(f"playback calls={playback.calls} (fake) state_errors={runtime.input_state_error_count} arbiter_state_errors={runtime.arbiter.state_sync_error_count}", file=out)
    if args.show_text:
        for kind, text in shown:
            print(f"{kind.lower()}: {text}", file=out)
    print("RESULT: " + ("all scripted interactions behaved as expected" if ok else "UNEXPECTED BEHAVIOUR"), file=out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
