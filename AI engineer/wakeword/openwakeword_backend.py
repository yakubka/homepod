"""openWakeWord backend for the VESTA wake-word pipeline (WP2.3).

Adapts openWakeWord's Model to the engine-independent WakeWordDetector
interface (wakeword/detector.py). Threshold, cooldown, timing and the
Detection record all come from the base class, so this file only:

  1. loads the openWakeWord model lazily (see below),
  2. turns one 1280-sample chunk into a {keyword: score} dict,
  3. resets the engine's internal history.

LAZY IMPORT: nothing here imports openWakeWord at module level. It is
imported inside OpenWakeWordDetector.__init__, so
`import wakeword.openwakeword_backend` (and everything else in the
project) works on machines where openWakeWord cannot be imported - e.g.
the Windows dev PC whose Application Control policy blocks scipy/sklearn
DLLs that openWakeWord's package __init__ imports eagerly.

API FACTS (read from the installed openWakeWord 0.6.0 source, NOT yet
exercised against the real library - see "VERIFY ON PI" notes):
  * Model(wakeword_models=[name], inference_framework="onnx"|"tflite")
  * Model.predict(int16 ndarray) -> {model_name: score in 0..1}
  * Model.reset() clears feature and prediction history
  * the first 5 predictions after construction/reset are forced to 0.0
    (0.4 s warm-up); a wake word inside that window cannot be detected
  * model files are NOT bundled: they live in openwakeword/resources/models
    and must be fetched once with openwakeword.utils.download_models()

Run a WAV file through it (Raspberry Pi / Linux):
    python -m wakeword.openwakeword_backend path/to/clip.wav
"""

import argparse
import os
import sys
from typing import List, Mapping, Optional, Sequence

import numpy as np

from config.logging_config import setup_logging
from wakeword.detector import WakeWordDetector

logger = setup_logging()

DEFAULT_MODEL = "hey_jarvis"  # temporary development phrase (team has not chosen the final one)


class WakeWordBackendError(RuntimeError):
    """Base class for backend problems that are about the environment, not the audio."""


class OpenWakeWordUnavailable(WakeWordBackendError):
    """openWakeWord could not be imported (not installed, or blocked by the OS)."""


class ModelFilesMissing(WakeWordBackendError):
    """openWakeWord imports fine but the model files were never downloaded."""


class OpenWakeWordDetector(WakeWordDetector):
    """WakeWordDetector backed by openWakeWord's pre-trained (or custom) models."""

    SAMPLE_RATE = 16000
    # 80 ms. openWakeWord's native frame; other sizes work but add up to 80 ms delay.
    CHUNK_SAMPLES = 1280
    # From openWakeWord source: predictions are zeroed while fewer than 5
    # frames are buffered. VERIFY ON PI against the installed version.
    WARMUP_CHUNKS = 5

    def __init__(
        self,
        model_names: Sequence[str] = (DEFAULT_MODEL,),
        threshold: float = 0.5,
        cooldown_s: float = 2.0,
        inference_framework: str = "onnx",
        *,
        model=None,
    ) -> None:
        """
        model_names: pre-trained names (e.g. "hey_jarvis") or paths to
                     custom .onnx/.tflite model files.
        inference_framework: "onnx" by default. openWakeWord's own default
                     is "tflite", but if tflite-runtime is missing it silently
                     falls back to ONNX, so a benchmark row labelled "tflite"
                     might really be ONNX. Choosing explicitly avoids that.
        model:       a ready-made Model-like object (needs predict() and
                     reset()). Test hook - lets the adapter be tested with a
                     fake without importing openWakeWord.
        """
        super().__init__(threshold=threshold, cooldown_s=cooldown_s)

        if inference_framework not in ("onnx", "tflite"):
            raise ValueError(f"inference_framework must be 'onnx' or 'tflite', got {inference_framework!r}")
        names = list(model_names)
        if not names:
            raise ValueError("model_names must contain at least one model")

        self._model_names = names
        self._inference_framework = inference_framework
        self._peak_score = 0.0
        self._peak_end_sample = 0

        self._model = model if model is not None else self._load_model(names, inference_framework)
        logger.info(
            "event=oww_backend_ready models=%s framework=%s threshold=%.2f cooldown_s=%.1f",
            ",".join(names), inference_framework, threshold, cooldown_s,
        )

    # -- lazy loading ---------------------------------------------------

    @staticmethod
    def _load_model(names: List[str], framework: str):
        try:
            import openwakeword  # noqa: WPS433 - deliberately lazy, see module docstring
            from openwakeword.model import Model
        except ImportError as exc:
            logger.error("event=oww_backend_unavailable error=%s", exc)
            raise OpenWakeWordUnavailable(
                f"openWakeWord could not be imported ({exc}). Install it with "
                "'pip install openwakeword', and on Windows check that Application "
                "Control is not blocking its scipy/sklearn DLLs."
            ) from exc

        OpenWakeWordDetector._check_model_files(openwakeword, names, framework)
        # Fresh list: Model rewrites the list it is given.
        return Model(wakeword_models=list(names), inference_framework=framework)

    @staticmethod
    def _check_model_files(openwakeword, names: List[str], framework: str) -> None:
        """Fail early with an actionable message instead of a cryptic
        onnxruntime 'file not found'. Never downloads anything itself:
        fetching models is an explicit one-time network step, so a device
        that is offline keeps working once the files are in place."""
        ext = ".onnx" if framework == "onnx" else ".tflite"

        def with_ext(path: str) -> str:
            return os.path.splitext(path)[0] + ext

        required = [with_ext(m["model_path"]) for m in openwakeword.FEATURE_MODELS.values()]
        for name in names:
            if os.path.exists(name):        # custom model file given by path
                continue
            if name not in openwakeword.MODELS:
                raise ValueError(
                    f"unknown pre-trained model {name!r}; available: {sorted(openwakeword.MODELS)}"
                )
            required.append(with_ext(openwakeword.MODELS[name]["model_path"]))

        missing = [p for p in required if not os.path.exists(p)]
        if missing:
            raise ModelFilesMissing(
                "openWakeWord model files not found: "
                + ", ".join(os.path.basename(p) for p in missing)
                + ". Download them once (needs internet) with: "
                "python -c \"from openwakeword.utils import download_models; "
                f"download_models({names!r})\""
            )

    # -- WakeWordDetector interface -------------------------------------

    @property
    def sample_rate(self) -> int:
        return self.SAMPLE_RATE

    @property
    def chunk_samples(self) -> int:
        return self.CHUNK_SAMPLES

    def _score_chunk(self, chunk: np.ndarray) -> Mapping[str, float]:
        # predict() wants int16 (it rejects other dtypes); the base class
        # already guarantees a 1-D int16 array of exactly 1280 samples.
        raw = self._model.predict(chunk)
        scores = {str(name): float(score) for name, score in raw.items()}

        if scores:
            top = max(scores.values())
            if top > self._peak_score:
                self._peak_score = top
                # base class advances samples_processed AFTER this hook returns
                self._peak_end_sample = self.samples_processed + chunk.size
        return scores

    def _reset_backend(self) -> None:
        self._model.reset()
        self._peak_score = 0.0
        self._peak_end_sample = 0

    # -- extras ---------------------------------------------------------

    @property
    def model_names(self) -> List[str]:
        return list(self._model_names)

    @property
    def inference_framework(self) -> str:
        return self._inference_framework

    @property
    def warmup_s(self) -> float:
        """Audio at the start of a stream in which detection is impossible."""
        return self.WARMUP_CHUNKS * self.CHUNK_SAMPLES / self.SAMPLE_RATE

    @property
    def peak_score(self) -> float:
        """Highest score seen since the last reset (any keyword)."""
        return self._peak_score

    @property
    def peak_time_s(self) -> float:
        """Stream time at the end of the chunk that produced peak_score."""
        return self._peak_end_sample / self.SAMPLE_RATE


# -- command line: python -m wakeword.openwakeword_backend clip.wav -------

def main(argv: Optional[Sequence[str]] = None, detector: Optional[OpenWakeWordDetector] = None) -> int:
    """Replay one WAV through the detector and print scores/detections.

    Returns 0 if the run completed (with or without detections), 2 if the
    environment or the file was unusable. `detector` is a test hook.
    """
    from audio.file_source import WavFileSource
    from wakeword.detector import run_on_source

    parser = argparse.ArgumentParser(
        prog="python -m wakeword.openwakeword_backend",
        description="Replay a 16 kHz mono 16-bit WAV through the openWakeWord backend.",
    )
    parser.add_argument("wav", help="path to a 16 kHz, mono, 16-bit PCM WAV file")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="pre-trained model name (default: %(default)s)")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--cooldown", type=float, default=2.0, help="seconds")
    parser.add_argument("--framework", choices=("onnx", "tflite"), default="onnx")
    parser.add_argument("--realtime", action="store_true", help="pace replay at real time")
    args = parser.parse_args(argv)

    try:
        source = WavFileSource(args.wav, realtime=args.realtime)
        if detector is None:
            detector = OpenWakeWordDetector(
                model_names=[args.model],
                threshold=args.threshold,
                cooldown_s=args.cooldown,
                inference_framework=args.framework,
            )
    except (WakeWordBackendError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"file={args.wav} duration={source.duration_s:.2f}s realtime={args.realtime} "
        f"model={','.join(detector.model_names)} framework={detector.inference_framework} "
        f"threshold={detector.threshold} cooldown={detector.cooldown_s}s "
        f"(no detection possible in the first {detector.warmup_s:.1f}s: engine warm-up)"
    )
    detections = run_on_source(source, detector)
    for d in detections:
        print(
            f"DETECTION keyword={d.keyword} score={d.score:.3f} stream_time={d.stream_time_s:.3f}s "
            f"chunk={d.chunk_index} processing={d.processing_ms:.1f}ms"
        )
    print(
        f"peak_score={detector.peak_score:.3f} at {detector.peak_time_s:.3f}s "
        f"chunks={detector.chunks_processed} suppressed_by_cooldown={detector.suppressed_detections}"
    )
    print(f"RESULT: {len(detections)} detection(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
