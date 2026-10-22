"""TEAM LEADER AI-2 / STAGE B / DEVELOPMENT / LIVE CAPTURE HARNESS.

NOT production code. NOT validated on a microphone. Prepared so that the Hardware Engineer can run the first
live Stage B test on the Raspberry Pi 5 + ReSpeaker XVF3800 the day the hardware exists.

    ReSpeakerMicrophone.read_block()  ->  CAPTURE WORKER  ->  bounded queue  ->  PROCESSING WORKER
        ->  continuity check  ->  VoicePipeline (wake -> Silero VAD -> faster-whisper -> normalised text)
        ->  one structural evidence record (JSON, written OUTSIDE the repository)

It orchestrates modules that already exist and are frozen (speech/pipeline.py, audio/utterance.py,
audio/silero_vad.py, speech/faster_whisper_backend.py, wakeword/). It re-implements none of them.

Why two workers: VoicePipeline.feed() runs speech-to-text synchronously, so one call can take seconds. If the
thread that reads the microphone also ran it, the microphone queue (audio/microphone.py) would overflow. The capture
worker therefore ONLY reads blocks and hands them to a bounded queue; the processing worker does everything slow.

Sample continuity (the central rule, nothing here is inferred from a clock):
  * every block carries its position in the callback's own sample count (MicBlock.start_sample, epoch, overflow);
  * a block the microphone dropped, or one this harness had to drop because the processing queue was full,
    appears as a JUMP in start_sample whose size is exact;
  * the PROCESSING worker is the single place that checks positions. On ANY discontinuity (sequence gap, overlap,
    epoch change, PortAudio input_overflow flag, trailing loss before the end) it logs a structural event, discards
    the affected partial utterance WITHOUT transcribing it, calls VoicePipeline.restart_stream() and continues with
    the block as the first block of a new stream. Audio on the two sides of a gap is never joined.
  * a device-level overflow has an UNKNOWN size; it is flagged, never estimated.
  * NOT detectable by any software here: loss below PortAudio that it does not flag. The evidence record says so.

Activation: by default the wake word (openWakeWord) must fire - the wake path is never bypassed silently. An explicit
DEVELOPMENT mode (--activation manual) replaces it with a key press; its results are labelled MANUAL_DEVELOPMENT
and are never wake-activated recognition.

Settings: the segmenter values are HARNESS-LOCAL, DEVELOPMENT and UNCALIBRATED; config/settings.py is unchanged.

Privacy: logs and the evidence record never contain a transcript or PCM. Transcripts are produced only when the
operator asks (--show-transcript, or --transcripts-out with an explicit path OUTSIDE the repository).

Run (from the project root; the output directory must be OUTSIDE the repository):

    python -m scripts.ai2_stage_b_live --out-dir D:\\evidence\\stage_b --duration-s 120
    python -m scripts.ai2_stage_b_live --out-dir D:\\evidence\\stage_b --activation manual     # Enter = activate, q = quit

Exit codes: 0 = the session ran and ended normally; 2 = invalid arguments or evidence location (nothing was
started); 3 = a failure (model loading / verification, microphone, a worker); a record with the failure class is
written when possible.
"""

import argparse
import dataclasses
import json
import logging
import os
import queue
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

if __name__ == "__main__":      # allow `python scripts/ai2_stage_b_live.py` as well as `python -m scripts....`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from audio.file_source import EndOfAudio
from audio.microphone import MicBlock
from audio.utterance import SegmenterConfig, UtteranceSegmenter
from config import settings
from config.logging_config import setup_logging
from scripts.ai2_stage_a_transcribe import DEV_SEGMENTER_RATIONALE, DEV_SEGMENTER_VALUES
from speech.pipeline import PipelineResult, VoicePipeline
from wakeword.detector import WakeWordDetector

logger = setup_logging()

SCHEMA_VERSION = "ai2-stage-b-session-v1"
HARNESS_LABEL = "TEAM LEADER AI-2 / STAGE B / DEVELOPMENT LIVE CAPTURE HARNESS"
SOURCE_FAKE = "FAKE_INPUT"
SOURCE_LIVE = "RESPEAKER_LIVE_MICROPHONE"
SOURCE_KINDS = (SOURCE_FAKE, SOURCE_LIVE)
MODE_WAKE = "WAKE"
MODE_MANUAL = "MANUAL_DEVELOPMENT"

# The harness can never produce the label HARDWARE-VALIDATED: that needs a person to check the device.
_LABELS = {
    SOURCE_FAKE: "SOFTWARE-VALIDATED (FAKE INPUT; NO MICROPHONE)",
    SOURCE_LIVE: "LIVE-MICROPHONE RUN - UNREVIEWED (NOT HARDWARE-VALIDATED until a person has checked this evidence on the device)",
}
_MANUAL_SUFFIX = " - MANUAL DEVELOPMENT ACTIVATION, NOT WAKE-ACTIVATED"

CONTINUITY_SCOPE = (
    "Positions are counted from the frames PortAudio delivered to the callback. Drops by the microphone queue and by this "
    "harness are exact. A PortAudio input_overflow flag marks a loss of UNKNOWN size. Loss below PortAudio that it does not "
    "flag (driver / USB) cannot be detected by software, so exact_continuity=true means 'no loss was reported', not 'proven'."
)

# harness-local live segmenter values: DEVELOPMENT, UNCALIBRATED. The Stage A values with ONE change - a pre-roll,
# because live capture activates when the wake phrase ends and the command may follow without a pause.
DEV_LIVE_SEGMENTER_VALUES = dict(DEV_SEGMENTER_VALUES, pre_roll_s=2.0)
DEV_LIVE_SEGMENTER_RATIONALE = dict(
    DEV_SEGMENTER_RATIONALE,
    pre_roll_s="HARNESS CHOICE: 2.0 s so the audio just before the wake detection (the end of the wake phrase and a command "
               "spoken without a pause) is replayed. No audio informed it.",
)
SEGMENTER_STATUS = "DEVELOPMENT / UNCALIBRATED (harness-local; config/settings.py is unchanged and still None)"

# a report must never contain these keys anywhere (transcripts and audio stay out of the evidence by construction)
_FORBIDDEN_KEYS = frozenset({"text", "transcript", "normalized_text", "actual_transcript", "pcm", "samples", "audio", "wav"})


# ---------------------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------------------

class StageBError(Exception):
    """Base class for harness problems."""


class StageBInputError(StageBError):
    """Invalid arguments or configuration. Nothing was started."""


class StageBEvidenceError(StageBInputError):
    """The evidence location is unusable (inside the repository, not writable, or the file already exists)."""


class StageBModelError(StageBError):
    """A model could not be loaded, or loaded but is not the verified artifact. Capture was never started."""

    def __init__(self, stage: str, cause_type: str, detail: str = "") -> None:
        super().__init__(f"{stage}: {cause_type}" + (f" ({detail})" if detail else ""))
        self.stage = stage
        self.cause_type = cause_type


class StageBReportError(StageBError, ValueError):
    """An evidence record broke the schema (for example it would contain a transcript)."""


class MicrophoneStall(RuntimeError):
    """The source delivered no block for max_consecutive_timeouts reads in a row."""


class StageBWorkerError(RuntimeError):
    """A worker (or the source start) failed. `report` is the complete evidence record, already marked failed."""

    def __init__(self, message: str, report: dict) -> None:
        super().__init__(message)
        self.report = report


# ---------------------------------------------------------------------------------------------------
# configuration, activation and engines
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveConfig:
    read_timeout_s: float = 0.5               # one read_block() wait
    max_consecutive_timeouts: int = 10        # 10 x 0.5 s = 5 s without any block = the microphone has stalled
    processing_queue_blocks: int = 400        # bounded: 400 x 1024 samples = 25.6 s of audio = ~0.8 MB
    join_timeout_s: float = 30.0              # how long stop() waits for a worker

    def __post_init__(self) -> None:
        def pos_num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 and v == v and v != float("inf")

        def pos_int(v):
            return isinstance(v, int) and not isinstance(v, bool) and v >= 1

        if not pos_num(self.read_timeout_s):
            raise StageBInputError(f"read_timeout_s must be a finite number > 0, got {self.read_timeout_s!r}")
        if not pos_int(self.max_consecutive_timeouts):
            raise StageBInputError(f"max_consecutive_timeouts must be an integer >= 1, got {self.max_consecutive_timeouts!r}")
        if not pos_int(self.processing_queue_blocks):
            raise StageBInputError(f"processing_queue_blocks must be an integer >= 1 (the queue is always bounded), got {self.processing_queue_blocks!r}")
        if not pos_num(self.join_timeout_s):
            raise StageBInputError(f"join_timeout_s must be a finite number > 0, got {self.join_timeout_s!r}")


class ManualActivationDetector(WakeWordDetector):
    """DEVELOPMENT ONLY. A WakeWordDetector that fires on the next chunk after arm(); there is no wake model.

    It exists so the VAD -> STT path can be tried before the wake model works on the device. Everything it produces is
    labelled MANUAL_DEVELOPMENT; it is never wake-word recognition."""

    activation_mode = MODE_MANUAL

    def __init__(self, sample_rate: int = settings.TARGET_SAMPLE_RATE, chunk_samples: int = 1280) -> None:
        self._rate, self._chunk = int(sample_rate), int(chunk_samples)
        self._armed = threading.Event()
        super().__init__(threshold=0.5, cooldown_s=0.0)

    @property
    def sample_rate(self) -> int:
        return self._rate

    @property
    def chunk_samples(self) -> int:
        return self._chunk

    def arm(self) -> None:
        """Activate on the next chunk. Safe to call from any thread."""
        self._armed.set()

    def _score_chunk(self, chunk):
        if self._armed.is_set():
            self._armed.clear()
            return {"manual": 1.0}
        return {"manual": 0.0}

    def _reset_backend(self) -> None:
        pass


@dataclass
class Engines:
    detector: WakeWordDetector
    vad: Any
    stt: Any

    @property
    def activation_mode(self) -> str:
        return MODE_MANUAL if isinstance(self.detector, ManualActivationDetector) else MODE_WAKE


def _default_vad():
    from audio.silero_vad import SileroOnnxVadEngine
    return SileroOnnxVadEngine()


def _default_stt():
    from speech.faster_whisper_backend import FasterWhisperEngine
    return FasterWhisperEngine()


def _default_wake():
    from wakeword.openwakeword_backend import OpenWakeWordDetector
    return OpenWakeWordDetector()


def load_engines(activation: str = MODE_WAKE, *, vad_factory=None, stt_factory=None, wake_factory=None) -> Engines:
    """Load and VERIFY every model before any capture starts. No downloads, no substitutions: a missing or unverified
    artifact raises StageBModelError (the frozen engines read only local files and check their recorded hashes)."""
    if activation not in (MODE_WAKE, MODE_MANUAL):
        raise StageBInputError(f"activation must be {MODE_WAKE} or {MODE_MANUAL}")
    stage = "vad"
    try:
        vad = (vad_factory or _default_vad)()
        if not getattr(vad, "artifact_verified", False):
            raise StageBModelError("vad", "ArtifactNotVerified", "the VAD model did not come from a hash-verified file")
        stage = "stt"
        stt = (stt_factory or _default_stt)()
        if not getattr(stt, "snapshot_verified", False):
            raise StageBModelError("stt", "SnapshotNotVerified", "the STT model snapshot was not hash-verified")
        stage = "wake"
        detector = ManualActivationDetector() if activation == MODE_MANUAL else (wake_factory or _default_wake)()
    except StageBModelError:
        raise
    except Exception as exc:
        raise StageBModelError(stage, type(exc).__name__, str(exc)[:300]) from exc
    return Engines(detector, vad, stt)


def validation_label(source_kind: str, activation_mode: str) -> str:
    if source_kind not in SOURCE_KINDS:
        raise StageBInputError(f"source_kind must be one of {SOURCE_KINDS}")
    return _LABELS[source_kind] + (_MANUAL_SUFFIX if activation_mode == MODE_MANUAL else "")


def build_live_segmenter_config(vad_engine) -> SegmenterConfig:
    if vad_engine.sample_rate != settings.TARGET_SAMPLE_RATE:
        raise StageBInputError(f"VAD engine runs at {vad_engine.sample_rate} Hz, the microphone at {settings.TARGET_SAMPLE_RATE} Hz")
    return SegmenterConfig(sample_rate=settings.TARGET_SAMPLE_RATE, vad_frame_samples=vad_engine.frame_samples, **DEV_LIVE_SEGMENTER_VALUES)


# ---------------------------------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _End:
    """Queue sentinel: the capture worker has finished. Carries where the last block it READ ended (dropped or not)."""

    reason: str                           # SOURCE_ENDED | STOP_REQUESTED
    last_epoch: Optional[int]
    last_end_sample: Optional[int]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _log(level: int, event: str, **fields) -> None:
    logger.log(level, "event=%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))


class StageBLiveHarness:
    """Capture worker + bounded queue + processing worker around a VoicePipeline. See the module docstring.

    source: anything with start(), stop(), read_block(timeout) -> MicBlock (queue.Empty on timeout, EndOfAudio at the
    end of a finite source), sample_rate, channels, and optionally telemetry(). ReSpeakerMicrophone qualifies.
    source_kind: SOURCE_LIVE only for the real microphone; everything else is SOURCE_FAKE."""

    def __init__(
        self,
        source,
        engines: Engines,
        *,
        source_kind: str = SOURCE_FAKE,
        config: LiveConfig = LiveConfig(),
        segmenter_config: Optional[SegmenterConfig] = None,
        sample_rate: int = settings.TARGET_SAMPLE_RATE,
        on_result: Optional[Callable[[PipelineResult, dict], None]] = None,
        on_discontinuity: Optional[Callable[[dict], None]] = None,
        pipeline: Optional[VoicePipeline] = None,
        now: Callable[[], datetime] = _utc_now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if source_kind not in SOURCE_KINDS:
            raise StageBInputError(f"source_kind must be one of {SOURCE_KINDS}")
        if source.sample_rate != sample_rate or source.channels != 1:
            raise StageBInputError(f"the source must deliver {sample_rate} Hz mono, got {source.sample_rate} Hz x {source.channels}")
        self._source, self._engines, self._source_kind, self._cfg = source, engines, source_kind, config
        self._on_result, self._on_discontinuity, self._now, self._clock = on_result, on_discontinuity, now, clock
        self._label = validation_label(source_kind, engines.activation_mode)
        if pipeline is not None:
            # a prebuilt pipeline (for example the one owned by assistant.runtime.VoiceAssistantRuntime): its segmenter and detector are
            # already wired, so the harness only records that it did not build them
            self._seg_record = {"values": None, "rationale": {}, "status": "PIPELINE SUPPLIED BY THE CALLER (settings not recorded here)"}
        elif segmenter_config is None:
            segmenter_config = build_live_segmenter_config(engines.vad)
            self._seg_record = {"values": dict(DEV_LIVE_SEGMENTER_VALUES), "rationale": dict(DEV_LIVE_SEGMENTER_RATIONALE), "status": SEGMENTER_STATUS}
        else:
            self._seg_record = {"values": dataclasses.asdict(segmenter_config), "rationale": {}, "status": "CALLER-SUPPLIED (not the harness default)"}
        self.pipeline = pipeline if pipeline is not None else VoicePipeline(engines.detector, UtteranceSegmenter(segmenter_config, engines.vad), engines.stt)
        self.session_id: Optional[str] = None      # optional: set by the caller before start() so the evidence name is known
        self._state = threading.Lock()
        self._running = False
        self._report: Optional[dict] = None
        self._reset_session()

    # -- session state -------------------------------------------------------------------------

    def _reset_session(self) -> None:
        self._queue: "queue.Queue" = queue.Queue(maxsize=self._cfg.processing_queue_blocks)
        self._stop_req = threading.Event()
        self._abort = threading.Event()
        self._finished = threading.Event()
        self._failure: Optional[dict] = None
        self._failure_exc: Optional[BaseException] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._process_thread: Optional[threading.Thread] = None
        # capture-thread counters
        self._blocks_captured = 0
        self._samples_captured = 0
        self._read_timeouts = 0
        self._h_dropped_blocks = 0
        self._h_dropped_samples = 0
        self._max_depth = 0
        self._end_reason: Optional[str] = None
        # processing-thread state
        self._blocks_processed = 0
        self._samples_processed = 0
        self._epoch: Optional[int] = None
        self._expected: int = 0
        self._run_index = -1
        self._runs: List[dict] = []
        self._discontinuities: List[dict] = []
        self._interactions: List[dict] = []
        self._discarded_partials = 0
        self._blocks_discarded_on_abort = 0
        self._started_at: Optional[datetime] = None
        self._ended_at: Optional[datetime] = None
        self._t0 = 0.0
        self._t1 = 0.0
        self._mic_start: Optional[dict] = None

    def _fail(self, stage: str, exc: BaseException) -> None:
        with self._state:
            if self._failure is not None:
                return
            self._failure = {"stage": stage, "type": type(exc).__name__}
            self._failure_exc = exc
        _log(logging.ERROR, "stage_b_failure", stage=stage, type=type(exc).__name__)
        self._abort.set()

    # -- public lifecycle ---------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_finished(self) -> bool:
        return self._finished.is_set()

    @property
    def last_report(self) -> Optional[dict]:
        return self._report

    def start(self) -> None:
        with self._state:
            if self._running:
                raise StageBInputError("the harness is already running; call stop() first")
            self._running = True
        self._reset_session()
        self._report = None
        self._started_at, self._t0 = self._now(), self._clock()
        self._sid = self.session_id or f"STAGEB-{self._started_at:%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
        self.pipeline.restart_stream()
        try:
            self._source.start()
        except Exception as exc:
            self._fail("source_start", exc)
            self._finalise(started_capture=False)
            raise StageBWorkerError("the audio source could not be started: " + type(exc).__name__, self._report) from exc
        self._mic_start = self._telemetry()
        self._capture_thread = threading.Thread(target=self._capture_loop, name="stageb-capture", daemon=True)
        self._process_thread = threading.Thread(target=self._process_loop, name="stageb-process", daemon=True)
        self._process_thread.start()
        self._capture_thread.start()
        _log(logging.INFO, "stage_b_start", source_kind=self._source_kind, activation_mode=self._engines.activation_mode,
             queue_blocks=self._cfg.processing_queue_blocks)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the session has finished by itself (source ended, or a failure). True if it finished."""
        deadline = None if timeout is None else self._clock() + timeout
        while not self._finished.is_set():
            remaining = 0.25 if deadline is None else min(0.25, deadline - self._clock())
            if remaining <= 0:
                return False
            self._finished.wait(remaining)
        return True

    def stop(self) -> dict:
        """Stop capturing, let the processing worker drain and finish, release the source, return the evidence record.
        Raises StageBWorkerError (carrying the record) if any worker failed. Idempotent after the first call."""
        with self._state:
            if not self._running:
                if self._report is None:
                    raise StageBInputError("the harness was never started")
                return self._report
        self._stop_req.set()
        for t in (self._capture_thread, self._process_thread):
            if t is not None:
                t.join(self._cfg.join_timeout_s)
                if t.is_alive():
                    self._fail("stop", TimeoutError(t.name))
                    t.join(1.0)
        self._finalise(started_capture=True)
        if self._failure is not None:
            raise StageBWorkerError(f"{self._failure['stage']} failed: {self._failure['type']}", self._report) from self._failure_exc
        return self._report

    def run(self, duration_s: Optional[float] = None) -> dict:
        """start(), wait for the end (or duration_s, or Ctrl+C), stop()."""
        self.start()
        try:
            self.wait(duration_s)
        except KeyboardInterrupt:
            pass
        return self.stop()

    def _finalise(self, *, started_capture: bool) -> None:
        try:
            self._source.stop()
        except Exception as exc:
            self._fail("source_stop", exc)
        while True:                                   # anything still queued after an abort was never processed
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, MicBlock):
                self._blocks_discarded_on_abort += 1
        self._ended_at, self._t1 = self._now(), self._clock()
        self._report = self._build_report(started_capture)
        validate_report(self._report)
        with self._state:
            self._running = False
        self._finished.set()
        _log(logging.INFO, "stage_b_stop", completed=self._report["completed"], end_reason=self._report["end_reason"],
             discontinuities=len(self._discontinuities), interactions=len(self._interactions))

    def stats(self) -> dict:
        """Advisory live counters (not synchronised with the workers)."""
        return {"blocks_captured": self._blocks_captured, "blocks_processed": self._blocks_processed,
                "harness_blocks_dropped": self._h_dropped_blocks, "max_queue_depth": self._max_depth,
                "discontinuities": len(self._discontinuities), "interactions": len(self._interactions)}

    # -- capture worker: reads blocks, never runs anything slow --------------------------------------------

    def _capture_loop(self) -> None:
        timeouts = 0
        last_epoch: Optional[int] = None
        last_end: Optional[int] = None
        try:
            while not self._stop_req.is_set() and not self._abort.is_set():
                try:
                    block = self._source.read_block(self._cfg.read_timeout_s)
                except queue.Empty:
                    timeouts += 1
                    self._read_timeouts += 1
                    if timeouts >= self._cfg.max_consecutive_timeouts:
                        raise MicrophoneStall(f"no block for {timeouts} consecutive reads")
                    continue
                except EndOfAudio:
                    self._end_reason = "SOURCE_ENDED"
                    break
                timeouts = 0
                n = int(block.samples.shape[0])
                self._blocks_captured += 1
                self._samples_captured += n
                last_epoch, last_end = block.epoch, block.start_sample + n
                try:
                    self._queue.put_nowait(block)
                    self._max_depth = max(self._max_depth, self._queue.qsize())
                except queue.Full:
                    # Never block the microphone reader. The block is counted, and its absence appears as an exact
                    # position jump at the next block (or as trailing loss at the end) in the processing worker.
                    self._h_dropped_blocks += 1
                    self._h_dropped_samples += n
                    if self._h_dropped_blocks == 1 or self._h_dropped_blocks % 100 == 0:
                        _log(logging.ERROR, "stage_b_queue_full_drop", blocks_dropped=self._h_dropped_blocks, samples_dropped=self._h_dropped_samples)
            if self._end_reason is None:
                self._end_reason = "STOP_REQUESTED"
            end = _End(self._end_reason, last_epoch, last_end)
            while not self._abort.is_set():           # the sentinel must not be lost; capture is over, so waiting is harmless
                try:
                    self._queue.put(end, timeout=0.05)
                    break
                except queue.Full:
                    continue
        except Exception as exc:
            self._fail("capture", exc)

    # -- processing worker: continuity check, pipeline, evidence ---------------------------------------------

    def _process_loop(self) -> None:
        try:
            while True:
                try:
                    item = self._queue.get(timeout=0.05)
                except queue.Empty:
                    if self._abort.is_set():
                        return
                    continue
                if self._abort.is_set():
                    if isinstance(item, MicBlock):          # taken off the queue but never processed: still accounted for
                        self._blocks_discarded_on_abort += 1
                    return
                if isinstance(item, _End):
                    self._on_end(item)
                    return
                self._on_block(item)
        except Exception as exc:
            self._fail("processing", exc)
        finally:
            self._finished.set()

    def _begin_run(self, block_start: int, epoch: int) -> None:
        self._run_index += 1
        self._epoch, self._expected = epoch, block_start
        self._runs.append({"run_index": self._run_index, "first_sample": block_start, "end_sample": block_start})

    def _on_block(self, block: MicBlock) -> None:
        reasons: List[str] = []
        gap: Optional[int] = 0
        first = self._epoch is None
        if first or block.epoch != self._epoch:
            if not first:
                reasons.append("EPOCH_CHANGE")
                gap = None                            # positions of two epochs are not comparable
            if block.start_sample != 0:
                reasons.append("LEADING_LOSS")        # an epoch always starts at 0, so a later start means earlier blocks were lost
                if first:
                    gap = block.start_sample
        else:
            if block.start_sample > self._expected:
                reasons.append("SEQUENCE_GAP")
                gap = block.start_sample - self._expected
            elif block.start_sample < self._expected:
                reasons.append("SEQUENCE_OVERLAP")
                gap = block.start_sample - self._expected
        if block.overflow:
            reasons.append("DEVICE_OVERFLOW")
        if reasons:
            self._discontinuity(reasons, gap, block.start_sample, block.epoch, bool(block.overflow))
        elif self._epoch is None:
            self._begin_run(block.start_sample, block.epoch)
        n = int(block.samples.shape[0])
        results = self.pipeline.feed(block.samples)
        self._blocks_processed += 1
        self._samples_processed += n
        self._expected = block.start_sample + n
        self._runs[-1]["end_sample"] = self._expected
        for result in results:
            self._record_result(result)

    def _on_end(self, end: _End) -> None:
        if self._epoch is not None and end.last_end_sample is not None and (end.last_epoch != self._epoch or end.last_end_sample != self._expected):
            gap = end.last_end_sample - self._expected if end.last_epoch == self._epoch else None
            self._discontinuity(["TRAILING_LOSS"], gap, end.last_end_sample, end.last_epoch, False, begin_new_run=False)
        elif self._epoch is not None:
            for result in self.pipeline.finish():
                self._record_result(result)

    def _discontinuity(self, reasons: List[str], gap: Optional[int], at_sample: int, epoch: int, overflow: bool, *, begin_new_run: bool = True) -> None:
        """Record it, discard any partial utterance WITHOUT transcribing, restart the pipeline stream. Runs only on the
        processing worker, between two feed() calls."""
        had_capture = self.pipeline.is_capturing
        entry = {
            "index": len(self._discontinuities),
            "reasons": list(reasons),
            "callback_domain_gap_samples": gap,           # exact when the epoch is unchanged; None when not knowable
            "device_overflow_flag": overflow,             # True = an additional loss of UNKNOWN size just before this point
            "at_stream_sample": at_sample,
            "run_index_before": self._run_index,
            "pipeline_master_samples_before": self.pipeline.master_samples,
            "open_capture_discarded": had_capture,
        }
        self._discontinuities.append(entry)
        if had_capture:
            self._discarded_partials += 1
        _log(logging.WARNING, "stage_b_discontinuity", reasons="+".join(reasons), gap_samples=gap, device_overflow=overflow,
             at_sample=at_sample, open_capture_discarded=had_capture)
        self.pipeline.restart_stream()
        if self._on_discontinuity is not None:
            self._on_discontinuity(entry)                # for example: the runtime ends LISTENING; a failure here is a processing failure
        if begin_new_run:
            self._begin_run(at_sample, epoch)
        else:
            self._epoch = None                           # nothing more will be fed in this session

    def _record_result(self, result: PipelineResult) -> None:
        entry = {
            "interaction_index": len(self._interactions),
            "run_index": self._run_index,
            "activation_mode": self._engines.activation_mode,
            "wake_detected": result.wake_detected,
            "wake_keyword": result.wake_keyword,
            "wake_prefix_removed": result.wake_prefix_removed,
            "status": result.status.value,
            "stt_status": result.raw_stt_status.value if result.raw_stt_status is not None else None,
            "segment_outcome": result.segment_outcome.value if result.segment_outcome is not None else None,
            "has_text": bool(result.normalized_text),
            "audio_duration_s": result.audio_duration_s,
            "activation_sample": result.activation_sample,
            "detector_end_sample": result.detector_end_sample,
            "activation_clamped": result.activation_clamped,
            "segment_start_sample": result.segment_start_sample,
            "segment_end_sample": result.segment_end_sample,
            "decided_at_sample": result.decided_at_sample,
            "unscored_samples": result.unscored_samples,
            "ignored_reactivation_count": result.ignored_reactivation_count,
            "stt_engine": result.stt_engine_id,
            "stt_model": result.model_id,
            "error_class": result.error_class,
            "decision_latency_s": result.timings.decision_latency_s,
            "stt_inference_ms": result.timings.stt_inference_ms,
            "post_decision_ms": result.timings.post_decision_ms,
            "validation_label": self._label,
        }
        self._interactions.append(entry)
        _log(logging.INFO, "stage_b_result", interaction=entry["interaction_index"], mode=entry["activation_mode"], status=entry["status"],
             segment_outcome=entry["segment_outcome"], stt_status=entry["stt_status"], error_class=entry["error_class"])
        if self._on_result is not None:
            self._on_result(result, entry)               # a sink failure is a processing failure, never swallowed

    # -- evidence -------------------------------------------------------------------------------------

    def _telemetry(self) -> Optional[dict]:
        fn = getattr(self._source, "telemetry", None)
        return dataclasses.asdict(fn()) if callable(fn) else None

    def _build_report(self, started_capture: bool) -> dict:
        mic_end = self._telemetry() if started_capture else None
        mic = None
        if mic_end is not None and self._mic_start is not None:
            mic = {k: mic_end[k] - self._mic_start[k] for k in ("blocks_received", "samples_received", "blocks_dropped", "samples_dropped", "overflow_events")}
            mic["queue_max_blocks"] = mic_end["queue_max_blocks"]
        failed = self._failure is not None
        mic_loss = bool(mic and (mic["blocks_dropped"] or mic["overflow_events"]))
        exact = bool(started_capture and not failed and not self._discontinuities and not self._h_dropped_blocks and not mic_loss)
        statuses: Dict[str, int] = {}
        for e in self._interactions:
            statuses[e["status"]] = statuses.get(e["status"], 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "harness_label": HARNESS_LABEL,
            "session_id": self._sid,
            "started_utc": self._started_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "ended_utc": self._ended_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "source_type": self._source_kind,
            "validation_label": self._label,
            "activation_mode": self._engines.activation_mode,
            "segmenter_config": self._seg_record,
            "models": engine_info(self._engines),
            "settings": {"read_timeout_s": self._cfg.read_timeout_s, "max_consecutive_timeouts": self._cfg.max_consecutive_timeouts,
                         "processing_queue_blocks": self._cfg.processing_queue_blocks},
            "capture_started": started_capture,
            "capture_duration_s": round(self._t1 - self._t0, 3),
            "continuity": {
                "exact_continuity": exact,
                "scope_note": CONTINUITY_SCOPE,
                "blocks_captured": self._blocks_captured,
                "samples_captured": self._samples_captured,
                "blocks_processed": self._blocks_processed,
                "samples_processed": self._samples_processed,
                "blocks_discarded_on_abort": self._blocks_discarded_on_abort,
                "harness_blocks_dropped": self._h_dropped_blocks,
                "harness_samples_dropped": self._h_dropped_samples,
                "mic_telemetry_delta": mic,
                "mic_read_timeouts": self._read_timeouts,
                "max_processing_queue_depth": self._max_depth,
                "runs": [dict(r) for r in self._runs],
                "discontinuity_count": len(self._discontinuities),
                "discontinuities": [dict(d) for d in self._discontinuities],
                "discarded_partial_utterances": self._discarded_partials,
            },
            "interaction_count": len(self._interactions),
            "status_counts": statuses,
            "interactions": [dict(e) for e in self._interactions],
            "completed": bool(started_capture and not failed),
            "end_reason": "FAILED" if failed else (self._end_reason or "STOP_REQUESTED"),
            "run_error": dict(self._failure) if failed else None,
            "privacy": {"transcripts_in_report": False, "pcm_in_report": False},
        }


def engine_info(engines: Engines) -> dict:
    return {
        "vad": {"engine": type(engines.vad).__name__, "artifact_verified": bool(getattr(engines.vad, "artifact_verified", False))},
        "stt": {"engine": getattr(engines.stt, "engine_id", None), "model": getattr(engines.stt, "model_id", None),
                "snapshot_verified": bool(getattr(engines.stt, "snapshot_verified", False))},
        "wake": {"detector": type(engines.detector).__name__, "model_hash_verified": False,
                 "note": "no recorded hash check exists for the wake model files"},
    }


def failure_report(stage: str, error_type: str, *, source_kind: str, activation_mode: str, session_id: str,
                   now: Callable[[], datetime] = _utc_now) -> dict:
    """The evidence record for a run that failed BEFORE capture could start (for example a model that did not load)."""
    t = now().astimezone(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": SCHEMA_VERSION, "harness_label": HARNESS_LABEL, "session_id": session_id,
        "started_utc": t, "ended_utc": t, "source_type": source_kind, "validation_label": validation_label(source_kind, activation_mode),
        "activation_mode": activation_mode, "segmenter_config": None, "models": None, "settings": None,
        "capture_started": False, "capture_duration_s": 0.0, "continuity": None, "interaction_count": 0, "status_counts": {},
        "interactions": [], "completed": False, "end_reason": "FAILED", "run_error": {"stage": stage, "type": error_type},
        "privacy": {"transcripts_in_report": False, "pcm_in_report": False},
    }


_REQUIRED = ("schema_version", "harness_label", "session_id", "started_utc", "ended_utc", "source_type", "validation_label",
             "activation_mode", "capture_started", "capture_duration_s", "continuity", "interaction_count", "interactions",
             "completed", "end_reason", "run_error", "privacy")


def validate_report(report: dict) -> None:
    """Structural check before anything is written: required keys, honest labels, and no transcript or audio keys anywhere."""
    for key in _REQUIRED:
        if key not in report:
            raise StageBReportError(f"missing key {key!r}")
    if report["schema_version"] != SCHEMA_VERSION:
        raise StageBReportError("unknown schema_version")
    if report["source_type"] not in SOURCE_KINDS or report["activation_mode"] not in (MODE_WAKE, MODE_MANUAL):
        raise StageBReportError("unknown source_type or activation_mode")
    label = report["validation_label"]
    if report["source_type"] == SOURCE_FAKE and not label.startswith("SOFTWARE-VALIDATED"):
        raise StageBReportError("a fake-input run must carry a SOFTWARE-VALIDATED label")
    if report["source_type"] == SOURCE_LIVE and ("UNREVIEWED" not in label or "NOT HARDWARE-VALIDATED" not in label):
        raise StageBReportError("a live run must be labelled UNREVIEWED / NOT HARDWARE-VALIDATED by this harness")
    if (report["activation_mode"] == MODE_MANUAL) != ("MANUAL DEVELOPMENT ACTIVATION" in label):
        raise StageBReportError("the label must say MANUAL DEVELOPMENT ACTIVATION exactly when activation was manual")
    if report["privacy"] != {"transcripts_in_report": False, "pcm_in_report": False}:
        raise StageBReportError("privacy block changed")
    if report["interaction_count"] != len(report["interactions"]):
        raise StageBReportError("interaction_count disagrees with interactions")
    for e in report["interactions"]:
        if e.get("activation_mode") != report["activation_mode"] or e.get("validation_label") != label:
            raise StageBReportError("an interaction disagrees with the session's activation mode or label")
    if (report["run_error"] is not None) != (report["end_reason"] == "FAILED") or report["completed"] == (report["run_error"] is not None):
        raise StageBReportError("completed / end_reason / run_error disagree")

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _FORBIDDEN_KEYS:
                    raise StageBReportError(f"forbidden key {k!r}: transcripts and audio never go into the report")
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(report)


# ---------------------------------------------------------------------------------------------------
# evidence files
# ---------------------------------------------------------------------------------------------------

def prepare_evidence_dir(out_dir) -> Path:
    from evaluation.ingest import IngestError, assert_outside_repo       # the project's existing personal-data guard
    out = Path(out_dir)
    try:
        assert_outside_repo(out)
    except IngestError as exc:
        raise StageBEvidenceError(str(exc)) from exc
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / f".write_probe_{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise StageBEvidenceError(f"evidence directory is not usable: {out} ({type(exc).__name__})") from exc
    return out


def write_evidence(report: dict, out_dir) -> Path:
    validate_report(report)
    path = prepare_evidence_dir(out_dir) / f"{report['session_id']}.stage_b.json"
    try:
        with open(path, "x", encoding="utf-8") as handle:          # "x": never overwrite existing evidence
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except FileExistsError:
        raise StageBEvidenceError(f"evidence file already exists and will not be overwritten: {path}") from None
    except OSError as exc:
        raise StageBEvidenceError(f"cannot write the evidence file {path}: {type(exc).__name__}") from exc
    return path


# ---------------------------------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai2_stage_b_live", description=HARNESS_LABEL + " (not production code)")
    p.add_argument("--out-dir", required=True, help="evidence directory OUTSIDE the repository")
    p.add_argument("--duration-s", type=float, default=None, help="stop after this many seconds (default: until Ctrl+C / 'q')")
    p.add_argument("--activation", choices=("wake", "manual"), default="wake",
                   help="wake = the wake word must fire (default); manual = DEVELOPMENT ONLY, Enter activates, results are labelled MANUAL_DEVELOPMENT")
    p.add_argument("--device-hint", default=settings.DEVICE_NAME_HINT, help="input device name hint")
    p.add_argument("--processing-queue-blocks", type=int, default=LiveConfig.processing_queue_blocks)
    p.add_argument("--show-transcript", action="store_true", help="print each transcript to the console (default: never)")
    p.add_argument("--transcripts-out", default=None, help="write transcripts to this NEW file OUTSIDE the repository (default: no transcripts are saved)")
    return p


def main(argv=None, *, engines_factory=None, source=None, source_kind=None, stdin=None, out=None, err=None,
         now: Callable[[], datetime] = _utc_now) -> int:
    out, err, stdin = out or sys.stdout, err or sys.stderr, stdin or sys.stdin
    args = _parser().parse_args(argv)
    activation = MODE_MANUAL if args.activation == "manual" else MODE_WAKE
    kind = source_kind or (SOURCE_FAKE if source is not None else SOURCE_LIVE)
    transcripts_handle = None
    try:
        if args.duration_s is not None and not args.duration_s > 0:
            raise StageBInputError("--duration-s must be > 0")
        config = LiveConfig(processing_queue_blocks=args.processing_queue_blocks)
        out_dir = prepare_evidence_dir(args.out_dir)
        session_id = f"STAGEB-{now():%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
        if args.transcripts_out:
            from evaluation.ingest import IngestError, assert_outside_repo
            try:
                assert_outside_repo(Path(args.transcripts_out).parent)
            except IngestError as exc:
                raise StageBEvidenceError(str(exc)) from exc
            try:
                transcripts_handle = open(args.transcripts_out, "x", encoding="utf-8")
            except FileExistsError:
                raise StageBEvidenceError("the --transcripts-out file already exists and will not be overwritten") from None
            except OSError as exc:
                raise StageBEvidenceError(f"cannot create the --transcripts-out file ({type(exc).__name__})") from exc
    except StageBInputError as exc:
        print(f"ERROR: {exc}", file=err)
        return 2

    def finish(report: dict, code: int) -> int:
        try:
            path = write_evidence(report, out_dir)
        except StageBError as exc:
            print(f"ERROR: {exc}", file=err)
            return 2
        finally:
            if transcripts_handle is not None:
                transcripts_handle.close()
        print(f"[{HARNESS_LABEL}]", file=out)
        print(f"session_id={report['session_id']} label={report['validation_label']}", file=out)
        c = report["continuity"]
        if c is not None:
            print(f"exact_continuity={c['exact_continuity']} discontinuities={c['discontinuity_count']} blocks_processed={c['blocks_processed']} "
                  f"harness_blocks_dropped={c['harness_blocks_dropped']} interactions={report['interaction_count']}", file=out)
        if report["run_error"]:
            print(f"ERROR: failed at stage {report['run_error']['stage']}: {report['run_error']['type']}", file=err)
        print(f"evidence: {path}", file=out)
        return code

    try:
        engines = (engines_factory or load_engines)(activation)
    except StageBModelError as exc:
        print(f"ERROR: model loading failed - {exc}", file=err)
        return finish(failure_report("model_load:" + exc.stage, exc.cause_type, source_kind=kind, activation_mode=activation,
                                     session_id=session_id, now=now), 3)

    if source is None:
        from audio.microphone import ReSpeakerMicrophone
        source = ReSpeakerMicrophone(device_name_hint=args.device_hint)

    def sink(result: PipelineResult, entry: dict) -> None:
        if transcripts_handle is not None:
            transcripts_handle.write(json.dumps({"session_id": session_id, "interaction_index": entry["interaction_index"],
                                                 "activation_mode": entry["activation_mode"], "status": entry["status"],
                                                 "text": result.normalized_text}, ensure_ascii=False) + "\n")
            transcripts_handle.flush()
        if args.show_transcript:
            print(f"transcript[{entry['interaction_index']}] ({entry['activation_mode']}): {result.normalized_text}", file=out)

    try:
        harness = StageBLiveHarness(source, engines, source_kind=kind, config=config, on_result=sink, now=now)
    except StageBInputError as exc:
        print(f"ERROR: {exc}", file=err)
        return finish(failure_report("configuration", type(exc).__name__, source_kind=kind, activation_mode=activation,
                                     session_id=session_id, now=now), 3)
    harness.session_id = session_id
    try:
        harness.start()
        try:
            if activation == MODE_MANUAL:
                print("MANUAL DEVELOPMENT ACTIVATION: press Enter to activate, q + Enter to quit.", file=out)
                for line in stdin:
                    if line.strip().lower() == "q" or harness.is_finished:
                        break
                    engines.detector.arm()
            else:
                harness.wait(args.duration_s)
        except KeyboardInterrupt:
            pass
        report = harness.stop()
        return finish(report, 0)
    except StageBWorkerError as exc:
        return finish(exc.report, 3)


if __name__ == "__main__":
    sys.exit(main())
