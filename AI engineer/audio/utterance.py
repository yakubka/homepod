"""Bounded PCM pre-roll buffer and wake-activated utterance segmenter (VESTA, Week 7 Task 1).

ENGINE-FREE: nothing here imports a VAD or STT engine. The segmenter only needs a
VadEngine (audio/vad.py) that returns one speech score per frame. Status:
SOFTWARE-VALIDATED ON SCRIPTED / SYNTHETIC VAD SCORES. It says nothing about the
accuracy of any real VAD, and nothing about a live microphone (see "Live audio").

Where it sits (frozen Week 7 architecture):

    AudioSource -> master absolute sample index
        |-- wake-word path (unchanged, Week 2)
        |-- RollingPcmBuffer (always on, bounded)
    wake detected -> segmenter.activate(activation_sample, buffer.snapshot())
                  -> live blocks -> segmenter.push(block, start_sample)
                  -> SegmenterResult (a SpeechSegment, or a reason there is none)

The neural VAD runs only after a wake detection. The buffer runs all the time, so the
start of "Hey Jarvis turn on the lights" is not clipped. Nobody has to pause after the
wake word: there is no wait-for-silence and no fixed discard period anywhere in here.

ABSOLUTE SAMPLE INDEX. Every block handed to the buffer or segmenter says where it starts
(`start_sample`, an index into the stream). The next block must start exactly where the
previous one ended; a gap or an overlap raises AudioContinuityError instead of being
silently joined. This is the software hook for the known live-microphone queue drops
(audio/microphone.py discards the oldest block when its queue is full).

Live audio: LIVE MICROPHONE TIMING and LIVE VAD CONTINUITY are NOT VALIDATED. File and
synthetic sources are gap-free by construction; a real overflowing queue is not.

DURATIONS are given in seconds and converted ONCE to whole samples, rounding UP (ceil).
One rule for every duration; a value within 1e-6 of a whole number of samples is taken as
that number: 0.07 s at 100 Hz is 7 samples, not 8 (0.07 * 100 is 7.000000000000001 in floats).
All state-machine decisions use integer samples.

FRAME GRANULARITY. Durations become sample counts, but the VAD scores whole frames, so every
decision is taken when a whole frame ends: an entered duration is effectively rounded UP to the
next frame boundary. Example with 10-sample frames: a start confirmation of 25 samples is decided
after 30 (3 frames); an end silence of 35 samples after 40 (4 frames); a no-speech timeout of 205
samples fires when the frame ending at 210 completes; a minimum utterance of 45 is measured in
whole frames (40 fails, 50 passes). This is frame-granularity behaviour, not a numerical error.
The frame length belongs to the VAD engine (e.g. 512 samples = 32 ms at 16 kHz).

THRESHOLD BOUNDARIES (true hysteresis, start_threshold > end_threshold):
  * a frame STARTS speech when score >= start_threshold (equality counts);
  * once speaking, a frame is speech while score > end_threshold and silence when
    score <= end_threshold (equality is silence). Scores between the two thresholds keep an
    utterance alive but never start one.

SPEECH REGION. Start confirmation needs start_confirmation consecutive frames with
score >= start_threshold; any lower frame resets it (a short burst is dropped without any
outcome). The confirmed onset is the first frame of that run. The segment starts at
onset - onset_padding, clamped to the oldest audio the segmenter holds. The region ends
after end_silence of consecutive silence frames; the segment ends at the end of the LAST
SPEECH frame (the trailing silence is not included).

ACTIVATION ANCHOR. Replayed pre-roll may hold old conversation. Pre-roll is context for the
START of the utterance that contains the wake phrase; it is not a source of utterances.
A speech region whose last speech sample is at or before activation_sample lies entirely
before the wake detection: it is discarded and listening continues. A region that is still
speaking when the stream passes activation_sample, or that begins after it, is kept. Known
limit: speech that runs into the wake phrase without a pause of at least end_silence is one
region and is kept whole, old words included; this module cannot tell them apart.

KNOWN SEGMENTATION LIMITATION (to be considered during T4 pipeline / real-data validation; the
behaviour is deliberate in Task 1 and unchanged). If unrelated old speech is separated from the wake
utterance by LESS than end_silence, it is treated as the same region. Consequences: the extra pre-wake
speech stays in the segment, and because MAX_DURATION is measured from the padded segment start that
old speech consumes part of the maximum-duration budget, so the END of the intended command can be
truncated. Related: whether the wake phrase itself is inside the returned segment depends on when the
detector fires relative to the end of the phrase. Fired before the phrase end, then a pause longer
than end_silence: the segment is the wake phrase alone and the command is not in it. Fired after the
phrase end, same pause: the phrase region lies before the activation and is discarded, so the command
alone is returned. Later stages must handle both.

OUTCOMES. COMPLETE (ended by end_silence), MAX_DURATION (the segment reached max_utterance,
measured from the PADDED segment start, and is cut there, audio kept), STREAM_ENDED (finish() while
speaking; the partial segment is returned), NO_SPEECH_TIMEOUT (no speech confirmed within
no_speech_timeout of activation_sample), NO_SPEECH_STREAM_ENDED (finish() with no
qualifying speech), TOO_SHORT (confirmed speech shorter than min_utterance, never returned as
a segment). Min utterance is measured from the confirmed onset to the last speech frame;
a duration equal to the minimum passes. A segment cut by MAX_DURATION is not min-checked.

MAX_DURATION is measured from the padded segment start (onset - onset_padding, after clamping), not
from the confirmed onset, so onset padding uses part of the maximum budget. The emitted PCM never
exceeds max_utterance; the speech-only extent after the onset can be smaller than max_utterance by
the padding amount (and, for a capped segment, even smaller than min_utterance, because capped
segments are not min-checked). Trailing silence never triggers the cap.

TOO_SHORT is TERMINAL for the activated capture: a short cough or noise after the wake word that is
confirmed as speech ends the capture, and a command arriving later is not captured. TOO_SHORT
FOLLOW-UP / CONTINUE-LISTENING POLICY: DEFERRED TO T4 / PRODUCT STATE MACHINE.

NO-SPEECH TIMER starts at activation_sample, not at the start of the replayed pre-roll.
Speech confirmed in the frame that ends exactly at the deadline counts as speech. The timer is
evaluated only while LISTENING: while an old region's end-silence countdown is running (it can outlast
the timeout when end_silence > no_speech_timeout) no timeout is decided, and if that region is then
discarded the timeout is decided on its closing frame.

FRAMING. Incoming blocks of any size are re-chunked by the existing Reframer (never padded
here). Frame i covers [origin + i*F, origin + (i+1)*F). finish() never pads a final incomplete
VAD frame. Those samples (fewer than one frame) are reported as unscored_samples AND are excluded
from the returned SpeechSegment, because no VAD decision exists for a partial frame: the segment
ends at the end of the last scored speech frame. When push() decides in the middle of a block, the
rest of that block is unscored in the same way and the caller keeps it for the next cycle.

WAKE DETECTOR -> MASTER SAMPLE CLOCK MAPPING: DEFERRED TO T4 INTEGRATION. Detection.end_sample counts
samples since the detector's last reset() and, when a finite file is replayed with run_on_source(),
includes the zero padding of the final chunk. The segmenter needs activation_sample on the SAME clock
as the buffer and correctly rejects one outside the replay [start, end] (SegmenterInputError). T4 must
establish the shared clock origin and reset rules (a detector reset after the stream started, a
detector that starts later than the buffer), map or clamp padded EOF detections, and test it.

LIFECYCLE. activate() resets the VadEngine exactly once; reset() clears the segmenter only
and does not touch the engine. A second activate() while capturing is ignored and counted;
it never resets the engine or restarts the capture. While a capture is active the arguments of
that ignored call are not validated, because they never become state. After a result, call
reset() before the next activate().

Memory is bounded by configuration: pre_roll + no_speech_timeout + max_utterance (+ end
silence). ponytail: the retained PCM is re-concatenated per block (O(n) each); fine for a few
seconds of audio, switch to a chunk list if utterances ever need to be long.
"""

import math
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Optional

import numpy as np

from audio.reframer import Reframer
from audio.vad import VadEngine

# A duration this close to a whole number of samples is that whole number (float noise).
_SAMPLE_EPS = 1e-6


# ---------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------

class SegmenterError(Exception):
    """Base class for every error raised by this module."""


class SegmenterConfigError(SegmenterError, ValueError):
    """A SegmenterConfig (or the settings it is built from) is invalid or not configured."""


class SegmenterInputError(SegmenterError, ValueError):
    """A block, snapshot, index or argument handed to the buffer or segmenter is invalid."""


class AudioContinuityError(SegmenterInputError):
    """A block did not start where the previous one ended (gap or overlap)."""


class SegmenterStateError(SegmenterError, RuntimeError):
    """A call is not allowed in the segmenter's current phase."""


# ---------------------------------------------------------------------------------------
# small validators
# ---------------------------------------------------------------------------------------

def _is_int(value) -> bool:
    return isinstance(value, (int, np.integer)) and not isinstance(value, bool)


def _check_sample_index(who: str, value) -> int:
    if not _is_int(value) or value < 0:
        raise SegmenterInputError(f"{who} must be a non-negative integer sample index, got {value!r}")
    return int(value)


def _as_mono_int16(block, who: str, allow_empty: bool = False) -> np.ndarray:
    """Return `block` as a 1-D int16 VIEW (no copy). Accepts 1-D or the (n, 1) layout of WP1."""
    if not isinstance(block, np.ndarray):
        raise SegmenterInputError(f"{who} must be a numpy array, got {type(block).__name__}")
    if block.dtype != np.int16:
        raise SegmenterInputError(f"{who} must be int16, got {block.dtype}")
    if block.ndim == 1:
        flat = block
    elif block.ndim == 2 and block.shape[1] == 1:
        flat = block.reshape(-1)
    else:
        raise SegmenterInputError(f"{who} must be 1-D or shape (n, 1), got {block.shape}")
    if flat.size == 0 and not allow_empty:
        raise SegmenterInputError(f"{who} must not be empty")
    return flat


def _owned_readonly(samples: np.ndarray) -> np.ndarray:
    owned = np.array(samples, dtype=np.int16, copy=True)
    owned.setflags(write=False)
    return owned


def _to_samples(seconds: float, sample_rate: int) -> int:
    """Seconds -> whole samples, rounding UP (see module docstring)."""
    exact = seconds * sample_rate
    nearest = round(exact)
    if abs(exact - nearest) < _SAMPLE_EPS:
        return int(nearest)
    return math.ceil(exact)


# ---------------------------------------------------------------------------------------
# snapshot + rolling buffer
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class PcmSnapshot:
    """An independent, read-only copy of recent PCM and where it starts in the stream."""

    start_sample: int
    samples: np.ndarray

    def __post_init__(self) -> None:
        _check_sample_index("PcmSnapshot.start_sample", self.start_sample)
        flat = _as_mono_int16(self.samples, "PcmSnapshot.samples", allow_empty=True)
        object.__setattr__(self, "start_sample", int(self.start_sample))
        object.__setattr__(self, "samples", _owned_readonly(flat))

    @property
    def end_sample(self) -> int:
        """Stream index just after the last sample (exclusive)."""
        return self.start_sample + int(self.samples.size)

    def __len__(self) -> int:
        return int(self.samples.size)


class RollingPcmBuffer:
    """Keep only the newest `capacity_samples` of a mono int16 stream, with absolute positions.

    Blocks of any size are accepted; a block larger than the capacity keeps its newest part.
    The buffer never grows past its capacity. snapshot() returns an owned copy and the absolute
    index where it starts. No clock, no threads, no device code.
    """

    def __init__(self, capacity_samples: int) -> None:
        if not _is_int(capacity_samples) or capacity_samples <= 0:
            raise SegmenterInputError(f"capacity_samples must be a positive integer, got {capacity_samples!r}")
        self._capacity = int(capacity_samples)
        self._ring = np.zeros(self._capacity, dtype=np.int16)
        self._write = 0          # ring index of the next sample to write
        self._size = 0           # samples currently held (<= capacity)
        self._end = 0            # stream index just after the newest sample
        self._started = False

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        return self._size

    @property
    def end_sample(self) -> int:
        """Stream index the NEXT pushed sample must have (just after the newest one)."""
        return self._end

    @property
    def start_sample(self) -> int:
        """Stream index of the oldest sample still held."""
        return self._end - self._size

    def push(self, block: np.ndarray, start_sample: Optional[int] = None) -> None:
        """Append a block. `start_sample`, if given, must equal end_sample once the stream has
        begun (the first block with a start_sample sets where the stream starts; default 0)."""
        samples = _as_mono_int16(block, "block")
        if start_sample is not None:
            start = _check_sample_index("start_sample", start_sample)
            if self._started and start != self._end:
                raise AudioContinuityError(
                    f"block starts at {start} but the stream continues at {self._end}"
                )
            if not self._started:
                self._end = start
        self._started = True

        n = int(samples.size)
        cap = self._capacity
        if n >= cap:
            self._ring[:] = samples[n - cap:]
            self._write = 0
            self._size = cap
        else:
            first = min(n, cap - self._write)
            self._ring[self._write:self._write + first] = samples[:first]
            if n > first:
                self._ring[:n - first] = samples[first:]
            self._write = (self._write + n) % cap
            self._size = min(cap, self._size + n)
        self._end += n

    def snapshot(self, max_samples: Optional[int] = None) -> PcmSnapshot:
        """Copy of the newest min(size, max_samples) samples (all held samples if None).

        Asking for more history than exists returns what exists.
        """
        if max_samples is None:
            m = self._size
        else:
            if not _is_int(max_samples) or max_samples < 0:
                raise SegmenterInputError(f"max_samples must be a non-negative integer, got {max_samples!r}")
            m = min(self._size, int(max_samples))

        if m == 0:
            data = np.empty(0, dtype=np.int16)
        else:
            begin = (self._write - m) % self._capacity
            if begin + m <= self._capacity:
                data = self._ring[begin:begin + m]
            else:
                data = np.concatenate((self._ring[begin:], self._ring[:m - (self._capacity - begin)]))
        return PcmSnapshot(start_sample=self._end - m, samples=data)


# ---------------------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SegmenterConfig:
    """Segmentation POLICY only. Durations in seconds; see the module docstring for rounding.

    There are no defaults on purpose: the production values are NOT CALIBRATED (config/settings.py
    holds None). Tests build explicit configs.
    """

    sample_rate: int
    vad_frame_samples: int          # NEW samples per VAD frame; must equal the engine's frame_samples
    start_threshold: float          # score >= this starts speech
    end_threshold: float            # score <= this is silence (must be < start_threshold)
    start_confirmation_s: float     # consecutive start-frames needed to confirm an onset
    end_silence_s: float            # consecutive silence needed to end an utterance
    onset_padding_s: float          # kept before the confirmed onset (>= 0)
    pre_roll_s: float               # how far before activation_sample replay may reach (>= 0)
    no_speech_timeout_s: float      # from activation_sample
    min_utterance_s: float          # onset -> last speech frame
    max_utterance_s: float          # cap on the segment length

    def __post_init__(self) -> None:
        def real(name, allow_zero=False):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v):
                raise SegmenterConfigError(f"{name} must be a finite number, got {v!r}")
            if v < 0 or (v == 0 and not allow_zero):
                raise SegmenterConfigError(f"{name} must be {'>= 0' if allow_zero else '> 0'}, got {v!r}")

        for name in ("sample_rate", "vad_frame_samples"):
            v = getattr(self, name)
            if not _is_int(v) or v <= 0:
                raise SegmenterConfigError(f"{name} must be a positive integer, got {v!r}")

        real("start_threshold", allow_zero=True)
        real("end_threshold", allow_zero=True)
        if not 0.0 <= self.end_threshold < self.start_threshold <= 1.0:
            raise SegmenterConfigError(
                "thresholds need 0 <= end_threshold < start_threshold <= 1 (true hysteresis), got "
                f"end={self.end_threshold!r} start={self.start_threshold!r}"
            )

        for name in ("start_confirmation_s", "end_silence_s", "no_speech_timeout_s",
                     "min_utterance_s", "max_utterance_s"):
            real(name)
        for name in ("onset_padding_s", "pre_roll_s"):
            real(name, allow_zero=True)

        if self.min_utterance_samples < self.start_confirmation_samples:
            raise SegmenterConfigError("min_utterance_s must be >= start_confirmation_s (confirmed speech is never shorter)")
        if self.max_utterance_samples < self.min_utterance_samples:
            raise SegmenterConfigError("max_utterance_s must be >= min_utterance_s")
        if self.no_speech_timeout_samples < self.start_confirmation_samples:
            raise SegmenterConfigError("no_speech_timeout_s must be >= start_confirmation_s (speech could never be confirmed)")
        if self.pre_roll_samples >= self.max_utterance_samples:
            raise SegmenterConfigError(
                "pre_roll_s must be < max_utterance_s (replayed history may not by itself exceed an utterance)"
            )
        if self.onset_padding_samples >= self.max_utterance_samples:
            raise SegmenterConfigError(
                "onset_padding_s must be < max_utterance_s (a capped segment must still contain the onset)"
            )

    # integer-sample views of the durations (pinned by tests)
    def _n(self, seconds: float) -> int:
        return _to_samples(seconds, self.sample_rate)

    @property
    def start_confirmation_samples(self) -> int:
        return self._n(self.start_confirmation_s)

    @property
    def end_silence_samples(self) -> int:
        return self._n(self.end_silence_s)

    @property
    def onset_padding_samples(self) -> int:
        return self._n(self.onset_padding_s)

    @property
    def pre_roll_samples(self) -> int:
        return self._n(self.pre_roll_s)

    @property
    def no_speech_timeout_samples(self) -> int:
        return self._n(self.no_speech_timeout_s)

    @property
    def min_utterance_samples(self) -> int:
        return self._n(self.min_utterance_s)

    @property
    def max_utterance_samples(self) -> int:
        return self._n(self.max_utterance_s)


# (settings key, SegmenterConfig field)
_SETTINGS_KEYS = (
    ("VAD_START_THRESHOLD", "start_threshold"),
    ("VAD_END_THRESHOLD", "end_threshold"),
    ("VAD_START_CONFIRMATION_S", "start_confirmation_s"),
    ("VAD_END_SILENCE_S", "end_silence_s"),
    ("VAD_ONSET_PADDING_S", "onset_padding_s"),
    ("VOICE_PRE_ROLL_S", "pre_roll_s"),
    ("VOICE_NO_SPEECH_TIMEOUT_S", "no_speech_timeout_s"),
    ("VOICE_MIN_UTTERANCE_S", "min_utterance_s"),
    ("VOICE_MAX_UTTERANCE_S", "max_utterance_s"),
)


def segmenter_config_from_settings(vad_frame_samples: int, sample_rate: Optional[int] = None,
                                   settings_module=None) -> SegmenterConfig:
    """Build a SegmenterConfig from config/settings.py, failing clearly if any value is unconfigured.

    The production values are NOT CALIBRATED (None), so this raises SegmenterConfigError until
    somebody sets them on purpose. `settings_module` is an injection point for tests.
    """
    if settings_module is None:
        from config import settings as settings_module  # noqa: WPS433 - late, keeps import order simple

    missing = [key for key, _ in _SETTINGS_KEYS if getattr(settings_module, key, None) is None]
    if missing:
        raise SegmenterConfigError("; ".join(f"{key} is not calibrated/configured" for key in missing))

    rate = sample_rate if sample_rate is not None else settings_module.TARGET_SAMPLE_RATE
    values = {field: getattr(settings_module, key) for key, field in _SETTINGS_KEYS}
    return SegmenterConfig(sample_rate=rate, vad_frame_samples=vad_frame_samples, **values)


# ---------------------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------------------

class SegmentOutcome(Enum):
    COMPLETE = "COMPLETE"
    MAX_DURATION = "MAX_DURATION"
    STREAM_ENDED = "STREAM_ENDED"
    NO_SPEECH_TIMEOUT = "NO_SPEECH_TIMEOUT"
    NO_SPEECH_STREAM_ENDED = "NO_SPEECH_STREAM_ENDED"
    TOO_SHORT = "TOO_SHORT"

    @property
    def has_segment(self) -> bool:
        return self in (SegmentOutcome.COMPLETE, SegmentOutcome.MAX_DURATION, SegmentOutcome.STREAM_ENDED)


class SegmenterPhase(Enum):
    IDLE = "IDLE"              # not activated
    LISTENING = "LISTENING"    # activated, no speech confirmed yet
    IN_SPEECH = "IN_SPEECH"    # speech confirmed, waiting for its end
    FINISHED = "FINISHED"      # a result exists; reset() before the next activation


@dataclass(frozen=True, eq=False)
class SpeechSegment:
    """One utterance's audio. `samples` is an owned, read-only copy of stream[start_sample:end_sample]."""

    samples: np.ndarray
    sample_rate: int
    start_sample: int         # stream index of samples[0] (onset minus padding, clamped)
    end_sample: int           # exclusive
    onset_sample: int         # confirmed speech onset (before padding)
    activation_sample: int    # the wake detection point this segment belongs to

    def __post_init__(self) -> None:
        flat = _as_mono_int16(self.samples, "SpeechSegment.samples")
        for name in ("start_sample", "end_sample", "onset_sample", "activation_sample"):
            object.__setattr__(self, name, _check_sample_index(f"SpeechSegment.{name}", getattr(self, name)))
        if not _is_int(self.sample_rate) or self.sample_rate <= 0:
            raise SegmenterInputError(f"SpeechSegment.sample_rate must be a positive integer, got {self.sample_rate!r}")
        if self.end_sample <= self.start_sample:
            raise SegmenterInputError("SpeechSegment needs end_sample > start_sample")
        if flat.size != self.end_sample - self.start_sample:
            raise SegmenterInputError(
                f"SpeechSegment has {flat.size} samples but covers {self.end_sample - self.start_sample}"
            )
        if not self.start_sample <= self.onset_sample < self.end_sample:
            raise SegmenterInputError("SpeechSegment onset_sample must lie inside the segment")
        object.__setattr__(self, "samples", _owned_readonly(flat))

    @property
    def duration_samples(self) -> int:
        return self.end_sample - self.start_sample

    @property
    def duration_s(self) -> float:
        return self.duration_samples / self.sample_rate

    @property
    def starts_before_activation(self) -> bool:
        """True when the segment reaches back into replayed pre-roll (e.g. the wake phrase)."""
        return self.start_sample < self.activation_sample


@dataclass(frozen=True)
class SegmenterResult:
    """What the segmenter decided. `segment` exists exactly for COMPLETE / MAX_DURATION / STREAM_ENDED."""

    outcome: SegmentOutcome
    segment: Optional[SpeechSegment]
    activation_sample: int
    decided_at_sample: int              # stream index received up to when this was decided
    ignored_reactivation_count: int     # extra activate() calls ignored during this capture
    unscored_samples: int               # received but never scored (partial frame / rest of the last block)

    def __post_init__(self) -> None:
        if self.outcome.has_segment != (self.segment is not None):
            raise SegmenterInputError(f"outcome {self.outcome.value} and segment presence disagree")


# ---------------------------------------------------------------------------------------
# the segmenter
# ---------------------------------------------------------------------------------------

class UtteranceSegmenter:
    """Wake-activated utterance segmentation over VAD scores. See the module docstring."""

    def __init__(self, config: SegmenterConfig, engine: VadEngine) -> None:
        if not isinstance(config, SegmenterConfig):
            raise SegmenterConfigError(f"config must be a SegmenterConfig, got {type(config).__name__}")
        if not isinstance(engine, VadEngine):
            raise SegmenterConfigError(f"engine must be a VadEngine, got {type(engine).__name__}")
        if engine.frame_samples != config.vad_frame_samples:
            raise SegmenterConfigError(
                f"engine frame is {engine.frame_samples} samples but config says {config.vad_frame_samples}"
            )
        if engine.sample_rate != config.sample_rate:
            raise SegmenterConfigError(
                f"engine runs at {engine.sample_rate} Hz but config says {config.sample_rate} Hz"
            )
        self._config = config
        self._engine = engine
        # convert durations once; decisions below use only these integers
        self._frame = config.vad_frame_samples
        self._confirm = config.start_confirmation_samples
        self._end_silence = config.end_silence_samples
        self._padding = config.onset_padding_samples
        self._pre_roll = config.pre_roll_samples
        self._timeout = config.no_speech_timeout_samples
        self._min = config.min_utterance_samples
        self._max = config.max_utterance_samples
        self.reset()

    # -- inspection ------------------------------------------------------

    @property
    def config(self) -> SegmenterConfig:
        return self._config

    @property
    def phase(self) -> SegmenterPhase:
        return self._phase

    @property
    def result(self) -> Optional[SegmenterResult]:
        """The result of the current activation, or None while it is still undecided."""
        return self._result

    @property
    def ignored_reactivation_count(self) -> int:
        return self._ignored

    @property
    def activation_sample(self) -> Optional[int]:
        return self._activation

    @property
    def expected_next_sample(self) -> Optional[int]:
        """Where the next pushed block must start (None until activated)."""
        return self._expected_next

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        """Back to a clean, un-activated state. Does NOT reset the VadEngine (activate() does that)."""
        self._phase = SegmenterPhase.IDLE
        self._faulted = False
        self._result: Optional[SegmenterResult] = None
        self._ignored = 0
        self._activation: Optional[int] = None
        self._expected_next: Optional[int] = None
        self._reframer: Optional[Reframer] = None
        self._origin = 0                       # stream index of the first scored sample
        self._frames = 0                       # frames scored so far
        self._pcm = np.empty(0, dtype=np.int16)  # every sample received since _origin
        self._clear_region()

    def _clear_region(self) -> None:
        self._cand_start: Optional[int] = None
        self._cand_run = 0
        self._onset = 0
        self._seg_start = 0
        self._last_speech_end = 0
        self._silence_run = 0

    def _require_usable(self) -> None:
        if self._faulted:
            raise SegmenterStateError("segmenter was left inconsistent by an earlier error; call reset()")

    def activate(self, activation_sample: int, replay: Optional[PcmSnapshot] = None) -> bool:
        """Start capturing for a wake detection at `activation_sample` (e.g. Detection.end_sample).

        `replay` is a RollingPcmBuffer snapshot. It must cover the activation point; only the part
        within pre_roll_s before the activation is used, and it may extend past the activation
        (audio the buffer already received). Live blocks must then start at replay.end_sample
        (or at activation_sample when there is no replay).

        Returns True when capture started, False when a capture was already active (the call is
        ignored and counted; nothing else changes). The replay can itself finish the capture:
        check `result` after activating.
        """
        self._require_usable()
        if self._phase in (SegmenterPhase.LISTENING, SegmenterPhase.IN_SPEECH):
            self._ignored += 1
            return False
        if self._phase is SegmenterPhase.FINISHED:
            raise SegmenterStateError("a result is waiting; call reset() before activating again")

        activation = _check_sample_index("activation_sample", activation_sample)
        if replay is not None:
            if not isinstance(replay, PcmSnapshot):
                raise SegmenterInputError(f"replay must be a PcmSnapshot, got {type(replay).__name__}")
            if not replay.start_sample <= activation <= replay.end_sample:
                raise SegmenterInputError(
                    f"activation_sample {activation} is outside the replay [{replay.start_sample}, {replay.end_sample}]"
                )

        self._engine.reset()  # exactly once per activation
        self._activation = activation
        self._reframer = Reframer(self._frame)
        self._phase = SegmenterPhase.LISTENING
        if replay is None:
            self._origin = activation
            self._expected_next = activation
            return True

        self._origin = max(replay.start_sample, activation - self._pre_roll)
        self._expected_next = replay.end_sample
        tail = replay.samples[self._origin - replay.start_sample:]
        if tail.size:
            self._ingest(tail)
        return True

    def push(self, block: np.ndarray, start_sample: int) -> Optional[SegmenterResult]:
        """Feed the next live block, which must start at expected_next_sample.

        Returns the SegmenterResult once the capture is decided, else None. Samples of the block
        after the deciding frame are not consumed (see unscored_samples).
        """
        self._require_usable()
        if self._phase is SegmenterPhase.IDLE:
            raise SegmenterStateError("not activated; call activate() first")
        if self._phase is SegmenterPhase.FINISHED:
            raise SegmenterStateError("a result is waiting; call reset() before pushing more audio")

        samples = _as_mono_int16(block, "block")
        start = _check_sample_index("start_sample", start_sample)
        if start != self._expected_next:
            kind = "gap" if start > self._expected_next else "overlap"
            raise AudioContinuityError(
                f"{kind}: block starts at {start}, expected {self._expected_next}"
            )
        self._expected_next += int(samples.size)
        return self._ingest(samples)

    def finish(self) -> SegmenterResult:
        """The stream ended. Decide with what has been received; never pads a partial frame."""
        self._require_usable()
        if self._phase is SegmenterPhase.IDLE:
            raise SegmenterStateError("not activated; call activate() first")
        if self._phase is SegmenterPhase.FINISHED:
            raise SegmenterStateError("a result already exists; call reset() before finishing again")

        decided = self._expected_next
        result = None
        if self._phase is SegmenterPhase.IN_SPEECH:
            result = self._close_region(self._last_speech_end, SegmentOutcome.STREAM_ENDED, decided, check_timeout=False)
        if result is None:
            result = self._terminal(SegmentOutcome.NO_SPEECH_STREAM_ENDED, None, decided)
        return result

    # -- internals ----------------------------------------------------------

    def _ingest(self, samples: np.ndarray) -> Optional[SegmenterResult]:
        """Store the samples, re-chunk them into VAD frames and step the state machine."""
        self._pcm = np.concatenate((self._pcm, samples))  # always a new array: never aliases the caller's block
        for frame in self._reframer.push(samples):
            start = self._origin + self._frames * self._frame
            self._frames += 1
            frame.setflags(write=False)
            try:
                score = self._engine.process_frame(frame)
                result = self._step(start, start + self._frame, score)
            except Exception:
                self._faulted = True
                raise
            if result is not None:
                return result
        return None

    def _step(self, frame_start: int, frame_end: int, score: float) -> Optional[SegmenterResult]:
        cfg = self._config
        if self._phase is SegmenterPhase.LISTENING:
            if score >= cfg.start_threshold:
                if self._cand_start is None:
                    self._cand_start = frame_start
                    self._cand_run = 0
                self._cand_run += self._frame
                if self._cand_run >= self._confirm:
                    self._onset = self._cand_start
                    self._seg_start = max(self._onset - self._padding, self._origin)
                    self._last_speech_end = frame_end
                    self._silence_run = 0
                    self._phase = SegmenterPhase.IN_SPEECH
                    return self._check_max(frame_end)
            else:
                self._cand_start = None
                self._cand_run = 0
            return self._check_timeout(frame_end)

        # IN_SPEECH
        if score > cfg.end_threshold:
            self._last_speech_end = frame_end
            self._silence_run = 0
            return self._check_max(frame_end)
        self._silence_run += self._frame
        if self._silence_run >= self._end_silence:
            return self._close_region(self._last_speech_end, SegmentOutcome.COMPLETE, frame_end)
        return None

    def _check_max(self, frame_end: int) -> Optional[SegmenterResult]:
        if self._last_speech_end - self._seg_start >= self._max:
            return self._close_region(self._seg_start + self._max, SegmentOutcome.MAX_DURATION, frame_end)
        return None

    def _check_timeout(self, frame_end: int) -> Optional[SegmenterResult]:
        # the timer starts at the activation point, never at the start of the replayed pre-roll
        if frame_end - self._activation >= self._timeout:
            return self._terminal(SegmentOutcome.NO_SPEECH_TIMEOUT, None, frame_end)
        return None

    def _close_region(self, end: int, outcome: SegmentOutcome, decided_at: int,
                      check_timeout: bool = True) -> Optional[SegmenterResult]:
        """A speech region ended at stream index `end`. Returns a result, or None if it was discarded."""
        if end <= self._activation:
            # entirely before the wake detection: old conversation, not this utterance
            self._clear_region()
            self._phase = SegmenterPhase.LISTENING
            return self._check_timeout(decided_at) if check_timeout else None

        if outcome is not SegmentOutcome.MAX_DURATION and end - self._onset < self._min:
            return self._terminal(SegmentOutcome.TOO_SHORT, None, decided_at)

        lo = self._seg_start - self._origin
        segment = SpeechSegment(
            samples=self._pcm[lo:end - self._origin],
            sample_rate=self._config.sample_rate,
            start_sample=self._seg_start,
            end_sample=end,
            onset_sample=self._onset,
            activation_sample=self._activation,
        )
        return self._terminal(outcome, segment, decided_at)

    def _terminal(self, outcome: SegmentOutcome, segment: Optional[SpeechSegment], decided_at: int) -> SegmenterResult:
        scored_to = self._origin + self._frames * self._frame
        self._result = SegmenterResult(
            outcome=outcome,
            segment=segment,
            activation_sample=self._activation,
            decided_at_sample=decided_at,
            ignored_reactivation_count=self._ignored,
            unscored_samples=self._expected_next - scored_to,
        )
        self._phase = SegmenterPhase.FINISHED
        return self._result
