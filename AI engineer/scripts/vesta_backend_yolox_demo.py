"""DEMO (software only, Windows-friendly): the team backend's occupancy path with the opt-in YOLOX detector.

    image files (mock camera, BGR like the team Camera) -> services.vision.detector.make_detector(vision.detector=yolox)
        -> services.vision.occupancy.OccupancyLoop -> services.core.runtime.Runtime(dry run) -> simulated Lights

The same factory, loop, fuse rule and runtime that main.py uses; no API server, no GPIO, no camera, no light is switched
(dry run, replay-mode Lights). After the images, one missing frame shows the camera-failure path: "no camera count", never 0.
Person counts are detected persons in one frame (YOLOX-Tiny undercounts dense rooms). NOT hardware-validated.

Run from the team repository root:

    set VESTA_MODE=replay
    python "AI engineer/scripts/vesta_backend_yolox_demo.py" <image> [<image> ...]

Exit codes: 0 = ran; 2 = bad arguments or not inside the team repository; 3 = the YOLOX model could not be loaded.
"""

import argparse
import os
import sys
from pathlib import Path

TEAM_ROOT = Path(__file__).resolve().parents[2]


def bgr_image(path):
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"), dtype=np.uint8)
    return np.ascontiguousarray(rgb[:, :, ::-1])            # the team Camera delivers OpenCV-style BGR


class ImageCamera:
    """The team Camera interface over a list of frames; None stands for "the camera delivered nothing"."""

    def __init__(self, frames):
        self.frames, self.running = list(frames), False

    def start(self):
        self.running = True
        return True

    def read(self):
        return self.frames.pop(0) if self.frames else None

    def stop(self):
        self.running = False


def main(argv=None, out=sys.stdout) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("images", nargs="+", help="image files used as camera frames (kept local; nothing is saved or uploaded)")
    args = p.parse_args(argv)
    if not (TEAM_ROOT / "services" / "vision" / "occupancy.py").is_file():
        print("ERROR: run this inside the team repository (services/ next to 'AI engineer/')", file=sys.stderr)
        return 2
    os.environ.setdefault("VESTA_MODE", "replay")
    sys.path.insert(0, str(TEAM_ROOT))
    from services.core import config
    from services.core.engine import Lecture, Occupancy
    from services.core.runtime import Runtime
    from services.device.lights import Lights
    from services.vision.detector import make_detector
    from services.vision.occupancy import OccupancyLoop

    try:
        frames = [bgr_image(a) for a in args.images]
    except OSError as e:
        print(f"ERROR: cannot read an image: {e}", file=sys.stderr)
        return 2
    cfg = {**config.load(), "vision.detector": "yolox"}
    try:
        detector = make_detector(cfg)
    except Exception as e:
        print(f"ERROR: YOLOX could not be loaded: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    loop = OccupancyLoop(ImageCamera(frames + [None]), detector, cfg["detection_interval"])
    rt = Runtime(Lights(), loop, dry=True)
    rt.lecture = Lecture(id="DEMO", name="demo lecture", start=0.0, end=9e9, expected=20)
    print(f"[SOFTWARE DEMO - mock camera frames, dry run, simulated lights] detector={type(detector).__name__} mode={config.MODE}", file=out)
    for i, name in enumerate([*args.images, "(missing frame)"]):
        try:
            loop.last = loop.snapshot()                       # exactly what the loop thread does each interval
        except Exception as e:
            loop.last = Occupancy(source="none")              # the loop's own failure path
            print(f"  frame {i} {name}: camera/detector failure ({type(e).__name__}) -> no camera count", file=out)
        res = rt.tick(cfg, now=1000.0 + i * 60)
        occ = rt.state(cfg, now=1000.0 + i * 60).occupancy
        lights = {a.target: a.value for a in res.actions if a.kind == "zone_light"}
        print(f"  frame {i} {Path(name).name}: total={occ.total} zones={occ.zones} source={occ.source} "
              f"confidence={round(occ.confidence, 2)} -> lighting decision {lights or 'no change'}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
