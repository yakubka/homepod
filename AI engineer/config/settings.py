"""Central configuration for the audio acquisition module.

Keeping these values here, rather than inline in audio/microphone.py,
means swapping the target device or capture backend later is a one-line
config change, not a hunt through consumer code.
"""

# Substring used to find the ReSpeaker XVF3800 among sounddevice's
# reported input devices (case-insensitive match against device name).
# The exact ALSA/PortAudio name string for the XVF3800 has not been
# confirmed on real hardware yet - update this hint after running
# device discovery once on the Raspberry Pi 5.
DEVICE_NAME_HINT = "ReSpeaker"

# Target capture format. 16 kHz mono 16-bit PCM matches what the wake
# word and STT engines planned for later phases expect. If the XVF3800
# does not support this rate/channel combination directly, that will
# surface as a stream_start_failed error in the logs - do not assume
# support until verified against the hardware.
TARGET_SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

# Number of frames per audio block delivered by the capture callback.
BLOCK_SIZE = 1024

# Max blocks buffered between the audio callback and consumers before
# the oldest block is dropped, to bound memory use if a consumer stalls.
QUEUE_MAX_BLOCKS = 50

LOG_LEVEL = "INFO"

# --- Vision (Week 4) ---------------------------------------------------
# Person-detection model for vision/detector.py. The file is NOT in the
# repository (*.onnx is git-ignored); fetch it once, see WEEK4_REPORT.md.
# Relative paths are resolved from the project root, not the cwd.
PERSON_MODEL_PATH = "models/yolox_tiny.onnx"
# Starting values only - not tuned. Threshold selection needs labelled
# classroom images (Week 5 evaluation), which do not exist yet.
PERSON_SCORE_THRESHOLD = 0.30
PERSON_NMS_THRESHOLD = 0.45

# --- Classroom ROI (Week 4, Task 7) ---------------------------------------
# Optional region of interest in NORMALISED image coordinates (0..1, origin
# top-left). Disabled by default = no ROI = the entire frame. No real VESTA
# camera has been mounted or calibrated, so there is deliberately no default
# geometry. ROI_TYPE "rectangle": ROI_POINTS = ((x1, y1), (x2, y2));
# "polygon": ROI_POINTS = ((x, y), ...) with at least 3 vertices.
ROI_ENABLED = False
ROI_TYPE = "rectangle"
ROI_POINTS = ()

# --- Classroom zones (Week 5, Task 2) -------------------------------------
# Disabled by default: no LEFT / CENTER / RIGHT geometry has been calibrated
# (no VESTA camera is mounted), so none is shipped here. When enabled, ZONES
# must define exactly LEFT, CENTER and RIGHT, in NORMALISED image coordinates
# (0..1, origin top-left). Each entry is a tuple:
#     (zone_id, display_name, kind, points)
# kind "rectangle": points = ((x1, y1), (x2, y2));
# kind "polygon":   points = ((x, y), (x, y), (x, y), ...) with 3 or more vertices.
# Order in this tuple does not matter: the loader always applies the canonical
# priority LEFT, CENTER, RIGHT (a shared border belongs to the earlier zone).
ZONES_ENABLED = False
ZONES = ()

# --- Sensor fusion freshness (Week 6, Task 1) ------------------------------
# NOT CALIBRATED: no maximum ages exist yet (no sensor hardware, no measured
# transport behaviour), so none is invented. None means "unconfigured": the
# source is reported UNCONFIGURED, never FRESH. Values are seconds on the
# host/Pi monotonic clock. FUSION_MAX_SOURCE_SKEW_S is the allowed age
# difference between two sources (used from Week 6 Task 2).
FUSION_VISION_MAX_AGE_S = None
FUSION_PRESENCE_MAX_AGE_S = None
FUSION_LUX_MAX_AGE_S = None
FUSION_TEMPERATURE_MAX_AGE_S = None
FUSION_HUMIDITY_MAX_AGE_S = None
FUSION_MAX_SOURCE_SKEW_S = None

# --- Voice input: VAD + utterance segmentation (Week 7, Task 1) -------------
# NOT CALIBRATED: no real speech, hardware or measured wake-trigger timing exists
# to base any of these on, so none is invented. None means "unconfigured":
# audio.utterance.segmenter_config_from_settings() raises until each is set on
# purpose. Tests build their own explicit SegmenterConfig instead.
# Thresholds are engine scores in [0, 1] with VAD_END_THRESHOLD < VAD_START_THRESHOLD
# (true hysteresis). Everything ending in _S is seconds (rounded UP to whole samples).
VAD_START_THRESHOLD = None
VAD_END_THRESHOLD = None
VAD_START_CONFIRMATION_S = None
VAD_END_SILENCE_S = None
VAD_ONSET_PADDING_S = None
# How far before the wake detection replayed audio may reach. Week 2's one recording
# is DEVELOPMENT EVIDENCE of the wake trigger lag, not a calibration.
VOICE_PRE_ROLL_S = None
VOICE_NO_SPEECH_TIMEOUT_S = None
VOICE_MIN_UTTERANCE_S = None
VOICE_MAX_UTTERANCE_S = None
