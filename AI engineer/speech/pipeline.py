"""Wake -> VAD -> STT voice pipeline, ending at NORMALIZED TEXT (VESTA, Week 7 Task 4).

    AudioSource blocks -> master PCM timeline -> wake detector
                                  |-> RollingPcmBuffer (always on, bounded)
    wake detected -> segmenter.activate(master activation sample, buffer snapshot) -> live PCM
                  -> SegmenterResult -> STT (once) -> wake-prefix strip -> PipelineResult

This module is thin glue. It owns ONLY: the master sample clock, the wake-detector -> master mapping,
the activation / pre-roll replay hand-off, the single STT call, the narrow leading wake-prefix
normalisation, the immutable result and structural telemetry. VAD policy lives in audio/utterance.py, the
VAD engine in audio/silero_vad.py, the STT engine in speech/stt.py: none of them is changed or re-decided here.
It does NOT call intent, answering, TTS, playback or the output arbiter (Week 8 and other layers).

STATUS: SOFTWARE-VALIDATED PIPELINE (scripted detector / VAD / STT, plus a plumbing run on generated audio).
It says nothing about wake, VAD or recognition accuracy and nothing about a live microphone, a ReSpeaker or a
Raspberry Pi. The label on every result is PIPELINE_VALIDATION_LABEL; no code path can upgrade it.

MASTER SAMPLE CLOCK (the one authoritative timeline)
  * Origin: master sample 0 is the first sample of the first block fed after the pipeline is built or
    restart_stream() is called. Master indices count REAL delivered samples only; zero padding never counts.
  * It never moves backwards and never resets mid-stream. A new stream starts with restart_stream() (or
    run_source(), which calls it), which also resets the wake detector, the pre-roll buffer and the segmenter.
  * RollingPcmBuffer indices ARE master indices (every push passes the master start). The segmenter's
    activation_sample, its replay and every SpeechSegment index are master indices too.
  * The wake detector has its OWN clock: Detection.end_sample counts samples since the detector's last
    reset(). The pipeline owns the detector: it resets it at construction / restart_stream() and in
    reset_wake_detector(), and records the master index where that reset happened (detector_origin).
    master_activation = detector_origin + Detection.end_sample. It is NEVER passed through unmapped.
    Each processed chunk is checked: detector.samples_processed must equal what this pipeline fed it since the
    reset, otherwise something else touched the detector and PipelineClockError is raised (no guessing).
  * The pipeline feeds the detector through its own Reframer, in slices of at most one detector chunk, so the
    detection chunk always ends inside the slice just pushed to the buffer and the buffer always holds the
    pre-roll (capacity = pre_roll + one chunk). A caller block of any size works.
  * DETECTOR FAILURE POLICY (fail closed). If the wake detector raises, or its clock check fails, the chunk it was given is
    lost to it while the master clock has already advanced, so every later detection would be mapped one chunk too early
    and nothing would notice. The pipeline therefore marks the stream UNTRUSTED: the failing call re-raises the original
    exception, and every later feed(), finish() or reset_wake_detector() raises PipelineStateError until the caller
    explicitly calls restart_stream(). Nothing is skipped silently, no ambiguous audio is resubmitted, and no activation
    is ever mapped through a clock that is known to be broken. restart_stream() discards the pre-roll buffer, the
    detector's pending samples and the segmenter state, so nothing from before the failure crosses the restart.
    (VAD, segmenter and STT failures do not touch the master or detector clock; they abandon the capture only.)
  * Optional start_sample on feed() must equal the master end, else AudioContinuityError (a gap or overlap is
    never silently joined). LIVE CONTINUITY is NOT VALIDATED: audio/microphone.py can drop old blocks silently
    and reports nothing, so this check cannot see such a drop unless the caller supplies start_sample.
  * EOF / finite files: finish() flushes the detector's last partial chunk WITH ZERO PADDING (that is how
    wakeword.run_on_source works). A detection fired on that chunk reports an end_sample that includes the
    padding. It is CLAMPED to the last real delivered sample (activation_clamped=True) and the padding is never
    given to the buffer or the segmenter. Anywhere else a mapped activation outside the delivered PCM is
    INVALID_ACTIVATION (a broken detector / clock), never clamped.

SEGMENTATION OUTCOMES (frozen Task 1 behaviour, passed through, not redesigned)
  * Pre-roll is replayed on wake; there is no silence hunt and no fixed discard, so "Hey Jarvis turn on the
    lights" with no pause is one region. A region ending at or before the activation is old speech and is
    discarded by the segmenter; one still speaking at the activation, or starting after it, is eligible.
  * TOO_SHORT is terminal for the activation: a cough after the wake word ends the capture and the pipeline
    returns to idle with status TOO_SHORT. It does not listen again; the user says the wake word again.
    UX LIMITATION, DEFERRED TO THE PRODUCT STATE MACHINE (also WAKE_ONLY follow-up).
  * NO_SPEECH_TIMEOUT and NO_SPEECH_STREAM_ENDED both report status NO_SPEECH (the segment outcome keeps the
    difference). STT is called exactly once per completed segment (COMPLETE, MAX_DURATION, STREAM_ENDED) and
    never otherwise.

STATUSES. Only TRANSCRIBED is a complete command. MAX_DURATION and STREAM_ENDED carry the text of a CUT segment
(possibly truncated): the consumer must not treat them as complete. WAKE_ONLY means the transcript was only the
wake phrase (no command invented). STT_EMPTY / STT_FAILED are an empty decode / a typed STT error. The first
failure is never turned into another status.

WAKE-PREFIX NORMALISATION. strip_wake_prefix() removes ONE configured wake phrase from the LEADING edge only:
case-insensitive, whole words, any run of whitespace / punctuation between words and around the phrase. It never
touches an internal occurrence ("tell jarvis to ..."), never spells, rewrites or paraphrases, and uses no model. Punctuation that
belongs to the command survives: a leading minus sign, a decimal point, an opening bracket or quote.

PRIVACY. The result carries the normalized text (the point of the pipeline), but its repr hides it and nothing
here logs a transcript, PCM, or an exception message. Logs carry sample counts, durations, statuses, ids,
timings, a prefix_removed flag and error class names only.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from audio.file_source import EndOfAudio
from audio.reframer import Reframer
from audio.utterance import (
    AudioContinuityError, RollingPcmBuffer, SegmenterPhase, SegmenterResult, SegmentOutcome, UtteranceSegmenter,
)
from config.logging_config import setup_logging
from speech.stt import SttEngine, SttError, SttStatus
from wakeword.detector import Detection, WakeWordDetector

logger = setup_logging()

# The pipeline is SOFTWARE-VALIDATED. Only a separate physical test can ever make a device result
# HARDWARE-VALIDATED; this constant is the only label a result may carry.
PIPELINE_VALIDATION_LABEL = "SOFTWARE-VALIDATED"
DEFAULT_WAKE_PHRASES: Tuple[str, ...] = ("hey jarvis",)


# ---------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------

class PipelineError(Exception):
    """Base class for pipeline wiring / clock errors (STT and VAD failures keep their own types)."""


class PipelineConfigError(PipelineError, ValueError):
    """The components handed to the pipeline do not fit together."""


class PipelineInputError(PipelineError, ValueError):
    """A block handed to the pipeline breaks the input contract."""


class PipelineStateError(PipelineError, RuntimeError):
    """A call is not allowed in the pipeline's current state."""


class PipelineClockError(PipelineError, RuntimeError):
    """The wake detector's clock disagrees with the pipeline's record of what it fed it."""


# ---------------------------------------------------------------------------------------
# wake-prefix normalisation (pure function)
# ---------------------------------------------------------------------------------------

# Separators: whitespace and punctuation around / inside the wake phrase. The apostrophe is deliberately NOT a
# separator, so "jarvis's" is not the wake word followed by "'s".
_PUNCTUATION = ",.;:!?-–—…\"“”()[]"


def _is_separator(ch: str) -> bool:
    return ch.isspace() or ch in _PUNCTUATION


_OPENERS = "\"“(["
_CLOSER_FOR = {"\"": "\"", "”": "“", ")": "(", "]": "["}      # closing character -> the opener it pairs with
_DIGITS = "0123456789"


def _skip_after_phrase(text: str, pos: int, openers: List[str]) -> int:
    """Index of the first character of the command, after the punctuation that separates it from the wake phrase.

    Consumed: whitespace, , ; : ! ? and the ellipsis, a full stop or hyphen / en dash that is punctuation, an em dash, and a
    closing quote / bracket that pairs with one that opened BEFORE the wake phrase. NOT consumed (they belong to the
    command): a minus sign (a hyphen or en dash directly before a digit, or before a decimal point and a digit), a decimal
    point (a full stop directly before a digit), and any other bracket or quote.
    """
    n = len(text)
    openers = list(openers)
    while pos < n:
        c = text[pos]
        nxt = text[pos + 1] if pos + 1 < n else ""
        if c.isspace() or c in ",;:!?…":
            pos += 1
        elif c == ".":
            if nxt in _DIGITS and nxt:
                break
            pos += 1
        elif c in "-–":
            after = text[pos + 2] if pos + 2 < n else ""
            if (nxt in _DIGITS and nxt) or (nxt == "." and after in _DIGITS and after):
                break
            pos += 1
        elif c == "—":
            pos += 1
        elif c in _CLOSER_FOR and _CLOSER_FOR[c] in openers:
            openers.remove(_CLOSER_FOR[c])
            pos += 1
        else:
            break
    return pos


def _compile_phrases(phrases: Sequence[str]) -> Tuple[Tuple[str, ...], ...]:
    if isinstance(phrases, str) or not phrases:
        raise PipelineConfigError("wake_phrases must be a non-empty sequence of phrases")
    compiled = []
    for phrase in phrases:
        words = tuple(w.lower() for w in phrase.split()) if isinstance(phrase, str) else ()
        if not words or not all(w.isalnum() for w in words):
            raise PipelineConfigError(f"a wake phrase must be one or more plain words, got {phrase!r}")
        compiled.append(words)
    return tuple(sorted(compiled, key=lambda w: -len(w)))        # longest phrase first


def strip_wake_prefix(text: str, phrases: Sequence[str] = DEFAULT_WAKE_PHRASES) -> Tuple[str, bool]:
    """Remove one wake phrase from the LEADING edge of `text`. Returns (remaining_text, removed).

    Case-insensitive; the phrase must match whole words in order, separated by at least one whitespace /
    punctuation character, and must be followed by the end of the text or a separator. Leading separators
    before the phrase and the punctuation that separates it from the command are dropped. Nothing else in the
    text is changed; in particular a minus sign or a decimal point that starts the command is KEPT
    ("hey jarvis: -5 degrees" -> "-5 degrees", "hey jarvis .5" -> ".5"); see _skip_after_phrase.
    """
    compiled = _compile_phrases(phrases)
    n = len(text)
    start = 0
    while start < n and _is_separator(text[start]):
        start += 1
    openers = [c for c in text[:start] if c in _OPENERS]
    for words in compiled:
        pos = start
        matched = True
        for index, word in enumerate(words):
            if index:
                gap = pos
                while pos < n and _is_separator(text[pos]):
                    pos += 1
                if pos == gap:                 # words must not run together ("heyjarvis" is not "hey jarvis")
                    matched = False
                    break
            if text[pos:pos + len(word)].lower() != word:
                matched = False
                break
            pos += len(word)
        if not matched:
            continue
        if pos < n and not _is_separator(text[pos]):      # "jarvisian", "jarvis's": not the wake word
            continue
        pos = _skip_after_phrase(text, pos, openers)
        return text[pos:].strip(), True
    return text, False


# ---------------------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------------------

class PipelineStatus(Enum):
    TRANSCRIBED = "TRANSCRIBED"                  # complete segment, text after the wake prefix
    WAKE_ONLY = "WAKE_ONLY"                      # the transcript was only the wake phrase: no command
    NO_SPEECH = "NO_SPEECH"                      # no speech confirmed (timeout or stream end); STT not called
    TOO_SHORT = "TOO_SHORT"                      # speech shorter than the minimum; STT not called
    MAX_DURATION = "MAX_DURATION"                # segment cut at the maximum: text may be TRUNCATED
    STREAM_ENDED = "STREAM_ENDED"                # stream ended while speaking: text may be incomplete
    STT_EMPTY = "STT_EMPTY"                      # decode completed, no usable text
    STT_FAILED = "STT_FAILED"                    # typed STT error
    INVALID_ACTIVATION = "INVALID_ACTIVATION"    # mapped wake sample outside the delivered PCM

    @property
    def carries_text(self) -> bool:
        return self in _TEXT_STATUSES


_TEXT_STATUSES = frozenset({PipelineStatus.TRANSCRIBED, PipelineStatus.MAX_DURATION, PipelineStatus.STREAM_ENDED})


@dataclass(frozen=True)
class PipelineTimings:
    decision_latency_s: float            # stream time from the activation to the segmenter's decision (sample counts)
    stt_inference_ms: Optional[float]    # SttResult.inference_ms (None when STT was not called or failed)
    post_decision_ms: float              # wall time of the STT call + normalisation after the decision


@dataclass(frozen=True)
class PipelineResult:
    """What one wake activation produced. Holds no PCM. normalized_text is hidden from repr."""

    status: PipelineStatus
    raw_stt_status: Optional[SttStatus]
    normalized_text: str = field(repr=False)
    segment_outcome: Optional[SegmentOutcome]
    activation_sample: int               # MASTER index the segmenter was (or would have been) activated at
    detector_end_sample: int             # Detection.end_sample as the detector reported it (detector clock)
    activation_clamped: bool             # True when a padded-EOF detection was clamped to the delivered PCM
    segment_start_sample: Optional[int]  # master indices; None when there is no segment
    segment_end_sample: Optional[int]
    audio_duration_s: Optional[float]
    stt_engine_id: Optional[str]         # None when STT was not called
    model_id: Optional[str]
    wake_detected: bool
    wake_keyword: str
    wake_prefix_removed: bool
    decided_at_sample: int
    unscored_samples: int
    ignored_reactivation_count: int
    error_class: Optional[str]           # exception class name only, never a message
    timings: PipelineTimings
    validation_label: str

    def __post_init__(self) -> None:
        if self.validation_label != PIPELINE_VALIDATION_LABEL:
            raise PipelineInputError(f"validation_label must be {PIPELINE_VALIDATION_LABEL!r}, got {self.validation_label!r}")
        if bool(self.normalized_text) != self.status.carries_text:
            raise PipelineInputError(f"status {self.status.value} and text presence disagree")
        has_segment = self.segment_start_sample is not None
        if has_segment != (self.segment_end_sample is not None) or has_segment != (self.audio_duration_s is not None):
            raise PipelineInputError("segment fields must be all present or all absent")
        called_stt = self.status in _STT_CALLED
        if called_stt != has_segment or called_stt != (self.stt_engine_id is not None) or called_stt != (self.model_id is not None):
            raise PipelineInputError(f"status {self.status.value} and the segment / STT identity fields disagree")
        if (self.error_class is not None) != (self.status in (PipelineStatus.STT_FAILED, PipelineStatus.INVALID_ACTIVATION)):
            raise PipelineInputError(f"status {self.status.value} and error_class disagree")


_STT_CALLED = frozenset({
    PipelineStatus.TRANSCRIBED, PipelineStatus.WAKE_ONLY, PipelineStatus.MAX_DURATION, PipelineStatus.STREAM_ENDED,
    PipelineStatus.STT_EMPTY, PipelineStatus.STT_FAILED,
})


@dataclass(frozen=True)
class _Wake:
    keyword: str
    detector_end_sample: int
    activation_sample: int
    clamped: bool


# ---------------------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------------------

class VoicePipeline:
    """Feed audio blocks in; get PipelineResults out, one per wake activation. See the module docstring."""

    def __init__(
        self,
        detector: WakeWordDetector,
        segmenter: UtteranceSegmenter,
        stt: SttEngine,
        *,
        wake_phrases: Sequence[str] = DEFAULT_WAKE_PHRASES,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """The pipeline OWNS the detector and the segmenter (it resets them); do not feed them elsewhere.
        wake_phrases must match what the detector's model says; clock is injectable for timing tests."""
        for name, obj, kind in (("detector", detector, WakeWordDetector), ("segmenter", segmenter, UtteranceSegmenter),
                                ("stt", stt, SttEngine)):
            if not isinstance(obj, kind):
                raise PipelineConfigError(f"{name} must be a {kind.__name__}, got {type(obj).__name__}")
        rate = segmenter.config.sample_rate
        if detector.sample_rate != rate or stt.sample_rate != rate:
            raise PipelineConfigError(
                f"sample rates differ: detector {detector.sample_rate} Hz, segmenter {rate} Hz, stt {stt.sample_rate} Hz"
            )
        self._detector = detector
        self._seg = segmenter
        self._stt = stt
        self._clock = clock
        _compile_phrases(wake_phrases)                      # fail early on a bad phrase
        self._wake_phrases = tuple(wake_phrases)
        self._rate = rate
        self._slice = int(detector.chunk_samples)
        self._capacity = segmenter.config.pre_roll_samples + self._slice
        self.restart_stream()

    # -- inspection ---------------------------------------------------------

    @property
    def master_samples(self) -> int:
        """Master index just after the last delivered real sample."""
        return self._master_end

    @property
    def detector_origin(self) -> int:
        """Master index at which the wake detector's clock was last reset to 0."""
        return self._detector_origin

    @property
    def interactions(self) -> int:
        return self._interactions

    @property
    def is_capturing(self) -> bool:
        return self._seg.phase in (SegmenterPhase.LISTENING, SegmenterPhase.IN_SPEECH)

    # -- lifecycle -----------------------------------------------------------

    def restart_stream(self) -> None:
        """Begin a NEW stream: master clock back to 0, detector, pre-roll buffer and segmenter reset."""
        self._seg.reset()
        self._detector.reset()
        self._wake_reframer = Reframer(self._slice)
        self._buffer = RollingPcmBuffer(self._capacity)
        self._master_end = 0
        self._detector_origin = 0
        self._detector_fed = 0
        self._wake: Optional[_Wake] = None
        self._interactions = 0
        self._closed = False
        self._untrusted: Optional[str] = None        # class name of the detector failure that invalidated the stream

    def reset_wake_detector(self) -> None:
        """Reset the detector mid-stream (its clock restarts at 0). The master clock is untouched and the
        new detector origin is the current master end; samples the detector had not yet been given are dropped."""
        self._require_open()
        self._detector.reset()
        self._wake_reframer.reset()
        self._detector_origin = self._master_end
        self._detector_fed = 0

    def _require_open(self) -> None:
        if self._untrusted is not None:
            raise PipelineStateError(
                f"the wake detector failed ({self._untrusted}) and the stream's sample clock can no longer be trusted; "
                "call restart_stream() before feeding more audio"
            )
        if self._closed:
            raise PipelineStateError("the stream was finished; call restart_stream() before feeding more audio")

    # -- audio in -------------------------------------------------------------

    def feed(self, block: np.ndarray, start_sample: Optional[int] = None) -> List[PipelineResult]:
        """Deliver the next block (1-D int16 or the (n, 1) layout of the audio sources). Returns the results
        completed by this block (usually none). `start_sample`, if given, must equal master_samples."""
        self._require_open()
        samples = _flat_int16(block)
        if start_sample is not None:
            if isinstance(start_sample, bool) or not isinstance(start_sample, (int, np.integer)) or start_sample < 0:
                raise PipelineInputError(f"start_sample must be a non-negative integer, got {start_sample!r}")
            if start_sample != self._master_end:
                kind = "gap" if start_sample > self._master_end else "overlap"
                raise AudioContinuityError(f"{kind}: block starts at {int(start_sample)}, expected {self._master_end}")
        results: List[PipelineResult] = []
        try:
            for lo in range(0, samples.size, self._slice):
                results.extend(self._feed_slice(samples[lo:lo + self._slice]))
        except Exception:
            self._seg.reset()        # a faulted capture is abandoned; the pipeline is idle again
            self._wake = None
            raise
        return results

    def finish(self) -> List[PipelineResult]:
        """The stream ended. Flush the detector's padded tail, decide any open capture, close the stream."""
        self._require_open()
        results: List[PipelineResult] = []
        try:
            tail = self._wake_reframer.flush(pad=True)
            if tail is not None:
                detection = self._detect(tail)
                if detection is not None:
                    result = self._on_detection(detection, padded=True)
                    if result is not None:
                        results.append(result)
            if self.is_capturing:
                results.append(self._complete(self._seg.finish()))
        except Exception:
            self._seg.reset()
            self._wake = None
            raise
        finally:
            self._closed = True
        return results

    def run_source(self, source) -> List[PipelineResult]:
        """Replay a finite AudioSource (for example WavFileSource) from master sample 0 to its end."""
        if source.sample_rate != self._rate:
            raise PipelineConfigError(f"source is {source.sample_rate} Hz but the pipeline runs at {self._rate} Hz")
        if source.channels != 1:
            raise PipelineConfigError(f"source must be mono, got {source.channels} channels")
        self.restart_stream()
        results: List[PipelineResult] = []
        source.start()
        try:
            while True:
                try:
                    block = source.read_frame()
                except EndOfAudio:
                    break
                results.extend(self.feed(block))
            results.extend(self.finish())
        finally:
            source.stop()
        return results

    # -- internals ---------------------------------------------------------------

    def _feed_slice(self, piece: np.ndarray) -> List[PipelineResult]:
        start = self._master_end
        capturing_before = self.is_capturing
        self._buffer.push(piece, start)                 # buffer index == master index
        self._master_end = start + int(piece.size)
        results: List[PipelineResult] = []
        for chunk in self._wake_reframer.push(piece):
            detection = self._detect(chunk)
            if detection is not None:
                result = self._on_detection(detection, padded=False)
                if result is not None:
                    results.append(result)
        if capturing_before and self.is_capturing:
            # a capture that was already running: the piece is new to it. (A capture activated in THIS slice
            # already received the piece through the buffer replay, so it is not pushed again.)
            decided = self._seg.push(piece, start)
            if decided is not None:
                results.append(self._complete(decided))
        return results

    def _detect(self, chunk: np.ndarray) -> Optional[Detection]:
        """Give one chunk to the detector. ANY failure (the detector raising, or its clock disagreeing) makes the stream untrusted."""
        try:
            detection = self._detector.process(chunk)
            self._detector_fed += int(chunk.size)
            self._check_detector_clock()
        except Exception as exc:
            self._untrusted = type(exc).__name__
            raise
        return detection

    def _check_detector_clock(self) -> None:
        if self._detector.samples_processed != self._detector_fed:
            raise PipelineClockError(
                f"detector has processed {self._detector.samples_processed} samples but the pipeline fed it {self._detector_fed}"
            )

    def _on_detection(self, detection: Detection, padded: bool) -> Optional[PipelineResult]:
        activation = self._detector_origin + int(detection.end_sample)         # detector clock -> master clock
        if self.is_capturing:
            self._seg.activate(activation)      # ignored and counted by the segmenter; nothing else changes
            return None
        clamped = False
        if padded and activation > self._master_end:
            activation = self._master_end        # the detection fired on zero padding: clamp to the last real sample
            clamped = True
        wake = _Wake(detection.keyword, int(detection.end_sample), activation, clamped)
        replay = self._buffer.snapshot()
        logger.info(
            "event=pipeline_wake keyword=%s detector_end_sample=%d detector_origin=%d activation_sample=%d clamped=%s",
            wake.keyword, wake.detector_end_sample, self._detector_origin, activation, clamped,
        )
        if not replay.start_sample <= activation <= replay.end_sample:
            return self._finish_result(self._invalid_result(wake))
        self._wake = wake
        self._seg.activate(activation, replay)
        decided = self._seg.result                # the replay alone can already decide the capture
        return self._complete(decided) if decided is not None else None

    def _complete(self, decided: SegmenterResult) -> PipelineResult:
        wake, self._wake = self._wake, None
        self._seg.reset()                          # idle again; the next wake starts a fresh capture
        return self._finish_result(self._build_result(wake, decided))

    def _finish_result(self, result: PipelineResult) -> PipelineResult:
        self._interactions += 1
        seg_s = f"{result.segment_start_sample}-{result.segment_end_sample}" if result.segment_start_sample is not None else "none"
        logger.info(
            "event=pipeline_result status=%s raw_stt_status=%s segment_outcome=%s activation_sample=%d detector_end_sample=%d "
            "clamped=%s segment=%s audio_duration_s=%s stt_engine=%s model=%s prefix_removed=%s text_chars=%d "
            "decided_at_sample=%d unscored_samples=%d ignored_reactivations=%d decision_latency_s=%.3f stt_ms=%s post_decision_ms=%.1f error=%s",
            result.status.value, result.raw_stt_status.value if result.raw_stt_status else "none",
            result.segment_outcome.value if result.segment_outcome else "none", result.activation_sample,
            result.detector_end_sample, result.activation_clamped, seg_s,
            f"{result.audio_duration_s:.3f}" if result.audio_duration_s is not None else "none",
            result.stt_engine_id, result.model_id, result.wake_prefix_removed, len(result.normalized_text),
            result.decided_at_sample, result.unscored_samples, result.ignored_reactivation_count,
            result.timings.decision_latency_s,
            f"{result.timings.stt_inference_ms:.1f}" if result.timings.stt_inference_ms is not None else "none",
            result.timings.post_decision_ms, result.error_class or "none",
        )
        return result

    def _invalid_result(self, wake: _Wake) -> PipelineResult:
        return PipelineResult(
            status=PipelineStatus.INVALID_ACTIVATION, raw_stt_status=None, normalized_text="", segment_outcome=None,
            activation_sample=wake.activation_sample, detector_end_sample=wake.detector_end_sample,
            activation_clamped=wake.clamped, segment_start_sample=None, segment_end_sample=None, audio_duration_s=None,
            stt_engine_id=None, model_id=None, wake_detected=True, wake_keyword=wake.keyword, wake_prefix_removed=False,
            decided_at_sample=self._master_end, unscored_samples=0, ignored_reactivation_count=0,
            error_class="ActivationOutsideDeliveredPcm",
            timings=PipelineTimings(0.0, None, 0.0), validation_label=PIPELINE_VALIDATION_LABEL,
        )

    def _build_result(self, wake: _Wake, decided: SegmenterResult) -> PipelineResult:
        began = self._clock()
        outcome = decided.outcome
        segment = decided.segment
        raw_status: Optional[SttStatus] = None
        text, removed, stt_ms, error = "", False, None, None
        if outcome is SegmentOutcome.TOO_SHORT:
            status = PipelineStatus.TOO_SHORT
        elif segment is None:
            status = PipelineStatus.NO_SPEECH
        else:
            try:
                stt_result = self._stt.transcribe_segment(segment)       # the ONE STT call for this activation
            except SttError as exc:
                status, error = PipelineStatus.STT_FAILED, type(exc).__name__
            else:
                raw_status, stt_ms = stt_result.status, stt_result.inference_ms
                if stt_result.status is SttStatus.EMPTY_TEXT:
                    status = PipelineStatus.STT_EMPTY
                else:
                    text, removed = strip_wake_prefix(stt_result.text, self._wake_phrases)
                    if not text:
                        status = PipelineStatus.WAKE_ONLY
                    elif outcome is SegmentOutcome.MAX_DURATION:
                        status = PipelineStatus.MAX_DURATION
                    elif outcome is SegmentOutcome.STREAM_ENDED:
                        status = PipelineStatus.STREAM_ENDED
                    else:
                        status = PipelineStatus.TRANSCRIBED
        post_ms = (self._clock() - began) * 1000.0
        return PipelineResult(
            status=status, raw_stt_status=raw_status, normalized_text=text, segment_outcome=outcome,
            activation_sample=wake.activation_sample, detector_end_sample=wake.detector_end_sample,
            activation_clamped=wake.clamped,
            segment_start_sample=segment.start_sample if segment else None,
            segment_end_sample=segment.end_sample if segment else None,
            audio_duration_s=segment.duration_s if segment else None,
            stt_engine_id=self._stt.engine_id if segment else None,
            model_id=self._stt.model_id if segment else None,
            wake_detected=True, wake_keyword=wake.keyword, wake_prefix_removed=removed,
            decided_at_sample=decided.decided_at_sample, unscored_samples=decided.unscored_samples,
            ignored_reactivation_count=decided.ignored_reactivation_count, error_class=error,
            timings=PipelineTimings(
                decision_latency_s=(decided.decided_at_sample - decided.activation_sample) / self._rate,
                stt_inference_ms=stt_ms, post_decision_ms=post_ms,
            ),
            validation_label=PIPELINE_VALIDATION_LABEL,
        )


def _flat_int16(block) -> np.ndarray:
    if not isinstance(block, np.ndarray):
        raise PipelineInputError(f"block must be a numpy array, got {type(block).__name__}")
    if block.dtype != np.int16:
        raise PipelineInputError(f"block must be int16, got {block.dtype}")
    if block.ndim == 1:
        flat = block
    elif block.ndim == 2 and block.shape[1] == 1:
        flat = block.reshape(-1)
    else:
        raise PipelineInputError(f"block must be 1-D or shape (n, 1), got {block.shape}")
    if flat.size == 0:
        raise PipelineInputError("block must not be empty")
    return flat
