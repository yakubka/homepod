"""Time the person detector on one fixed frame (Week 4).

    python -m vision.benchmark [--runs 30] [--model PATH] [--image PATH]

Default input is a blank 640x480 RGB frame, which measures the model's
speed, not its detection quality. Reports initialisation, the first
(cold) inference, one discarded warm-up, then `--runs` measured
inferences. Numbers describe THIS machine only; they are not camera FPS
and not Raspberry Pi speed.
"""

import argparse
import statistics as st
import time
from typing import Optional, Sequence

import numpy as np

from config import settings
from vision.detector import YoloxOnnxPersonDetector
from vision.frames import Frame, ImageFileSource


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vision.benchmark", description=__doc__)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--model", default=settings.PERSON_MODEL_PATH)
    parser.add_argument("--image", help="time on this image instead of a blank frame")
    args = parser.parse_args(argv)
    if args.runs < 10:
        parser.error("--runs must be at least 10")

    t = time.perf_counter()
    detector = YoloxOnnxPersonDetector(args.model)
    init_ms = (time.perf_counter() - t) * 1000

    load_ms = None
    if args.image:
        src = ImageFileSource([args.image])
        src.start()
        t = time.perf_counter()
        frame = src.read_frame()
        load_ms = (time.perf_counter() - t) * 1000
    else:
        frame = Frame(np.zeros((480, 640, 3), dtype=np.uint8), 0, "blank_640x480")

    first = detector.detect(frame)
    detector.detect(frame)  # warm-up, discarded
    results = [detector.detect(frame) for _ in range(args.runs)]
    inf = [r.inference_ms for r in results]
    tot = [r.total_ms for r in results]

    print(f"frame={frame.source} {frame.width}x{frame.height} model={first.model} input_hw={detector.input_hw} "
          f"threshold={detector.score_threshold} detections={results[-1].count}")
    print(f"init_ms={init_ms:.1f} first_inference_ms={first.inference_ms:.1f} (cold, not representative)")
    if load_ms is not None:
        print(f"image_load_decode_ms={load_ms:.1f} (once, not part of detector time)")
    for name, x in (("preprocess_ms", [r.preprocess_ms for r in results]), ("inference_ms", inf),
                    ("postprocess_ms", [r.postprocess_ms for r in results]), ("total_pipeline_ms", tot)):
        print(f"{name}: runs={len(x)} mean={st.mean(x):.1f} median={st.median(x):.1f} "
              f"min={min(x):.1f} max={max(x):.1f} stdev={st.stdev(x):.1f}")
    print(f"throughput on this machine (mean total pipeline): {1000 / st.mean(tot):.1f} frames/s "
          "- software throughput, not camera FPS, not Raspberry Pi FPS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
