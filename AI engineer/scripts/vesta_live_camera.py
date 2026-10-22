"""VESTA MIDTERM DEMO 4 - LIVE CAMERA + PERSON DETECTION. Runs only when invoked; NOT HARDWARE-VALIDATED until a person checks it.

    camera (CameraFrameSource, OpenCV capture) -> YOLOX-Tiny person detector (vision/detector.py, unchanged)
        -> boxes + detected-person count per frame -> optional window -> one summary line at the end

Prints, per frame (or every --print-every frames): frame index, ACTUAL frame size, detected-person count, detector time and the
measured FPS so far. The count is the number of person detections in ONE frame: not a verified student count, not unique people,
and the frozen Week 4 benchmark shows severe undercounting in dense classrooms (WEEK4_REPORT.md). No zone calibration is implied.

Nothing is saved or uploaded: no frame, no video. The camera is released on every exit path (Ctrl+C, q, error, end).

    python -m scripts.vesta_live_camera --list                       # camera device nodes this host exposes (no device is opened)
    python -m scripts.vesta_live_camera --camera 0 --max-frames 100  # needs OpenCV; add --no-display on a headless Pi

Exit codes: 0 = ran; 2 = bad arguments / OpenCV missing; 3 = camera or model failure (message names the failing part).
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from fusion.contracts import Origin, SourceEnvelope, VisionObservation
from vision.camera import CameraError, CameraFrameSource, CameraUnavailable, open_opencv_capture
from vision.roi import roi_from_settings
from vision.zones import assign_detections, zones_from_settings

LABEL = "LIVE CAMERA PREVIEW - UNREVIEWED (NOT HARDWARE-VALIDATED until a person has checked it on the device)"


def list_cameras() -> list:
    """Device nodes only (Linux /dev/video*). Nothing is opened. Other platforms: use the index the OS camera tool shows."""
    return sorted(str(p) for p in Path("/dev").glob("video*")) if Path("/dev").is_dir() else []


def _device(arg: str):
    return int(arg) if arg.isdigit() else arg


def observation(result, origin: Origin, received_at_monotonic_s: float) -> VisionObservation:
    """One frame's detections as the fusion input (Week 5 accounting, configured ROI / zones only; none = whole frame, no zones)."""
    roi = roi_from_settings()
    occupancy = assign_detections(result.detections, roi, zones_from_settings(roi))
    return VisionObservation(SourceEnvelope("camera", origin, received_at_monotonic_s), occupancy)


def run(source, detector, *, max_frames: Optional[int], duration_s: Optional[float], show=None, print_every: int = 1,
        out=sys.stdout, clock=time.monotonic, store=None) -> dict:
    """Read -> detect -> report until a limit, q, or Ctrl+C. `show(frame, result) -> bool` returns False to stop. Always stops the source.
    With a ClassroomStateStore, every frame's observation (with the source's origin) replaces the previous one there."""
    frames, counts, t0, size = 0, [], None, None
    try:
        source.start()
        t0 = clock()
        while (max_frames is None or frames < max_frames) and (duration_s is None or clock() - t0 < duration_s):
            frame = source.read_frame()
            result = detector.detect(frame)
            frames += 1
            counts.append(result.count)
            if store is not None:
                store.ingest(observation(result, source.origin, store.clock()))
            size = (frame.width, frame.height)
            fps = frames / max(clock() - t0, 1e-9)
            if frames % print_every == 0:
                print(f"frame={frame.index} size={frame.width}x{frame.height} detected_persons={result.count} "
                      f"detect_ms={result.total_ms:.1f} measured_fps={fps:.2f}", file=out)
            if show is not None and show(frame, result) is False:
                break
    except KeyboardInterrupt:
        pass
    finally:
        source.stop()
    elapsed = (clock() - t0) if t0 is not None else 0.0
    summary = {"frames": frames, "frame_size": size, "elapsed_s": round(elapsed, 3),
               "measured_fps": round(frames / elapsed, 3) if frames and elapsed > 0 else None,
               "min_count": min(counts) if counts else None, "max_count": max(counts) if counts else None,
               "origin": getattr(source, "origin", Origin.UNKNOWN).value, "label": LABEL}
    print("summary " + " ".join(f"{k}={v}" for k, v in summary.items()), file=out)
    return summary


def opencv_window(title: str = "VESTA person detection (q = quit)"):
    """A show() callback drawing boxes and the count with the existing annotate helper. Needs OpenCV with GUI support."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    from vision.annotate import draw_detections

    def show(frame, result) -> bool:
        img = Image.fromarray(draw_detections(frame.image, result.detections))
        ImageDraw.Draw(img).text((8, 8), f"detected persons: {result.count}", fill=(255, 255, 0))
        cv2.imshow(title, cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR))
        return (cv2.waitKey(1) & 0xFF) != ord("q")

    return show


def main(argv=None, *, out=sys.stdout, err=sys.stderr, capture_factory=None, detector_factory=None) -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.vesta_live_camera", description="Live camera person-detection preview (VESTA Demo 4).")
    p.add_argument("--list", action="store_true", help="list camera device nodes and exit (opens nothing)")
    p.add_argument("--camera", help="camera index (e.g. 0) or device path (e.g. /dev/video0); required unless --list")
    p.add_argument("--width", type=int, default=None, help="requested capture width (the ACTUAL size is printed)")
    p.add_argument("--height", type=int, default=None)
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--duration-s", type=float, default=None)
    p.add_argument("--print-every", type=int, default=1)
    p.add_argument("--read-timeout-s", type=float, default=5.0, help="a frame that does not arrive in time is a camera failure")
    p.add_argument("--no-display", action="store_true", help="console output only (headless)")
    p.add_argument("--score-threshold", type=float, default=settings.PERSON_SCORE_THRESHOLD)
    args = p.parse_args(argv)

    if args.list:
        found = list_cameras()
        print("camera device nodes: " + (", ".join(found) if found else "none found (or not a Linux host)"), file=out)
        print("DEVICE DISCOVERY IS NOT HARDWARE VALIDATION.", file=out)
        return 0
    if args.camera is None or args.print_every < 1 or (args.max_frames is not None and args.max_frames < 1):
        print("ERROR: give --camera (and positive --max-frames / --print-every)", file=err)
        return 2
    device = _device(args.camera)
    try:
        if detector_factory is None:
            from vision.detector import YoloxOnnxPersonDetector
            detector = YoloxOnnxPersonDetector(score_threshold=args.score_threshold)
        else:
            detector = detector_factory()
    except Exception as exc:
        print(f"ERROR: person detector failed to load: {type(exc).__name__}: {exc}", file=err)
        return 3
    open_capture = capture_factory or (lambda: open_opencv_capture(device, width=args.width, height=args.height))
    # REAL only for a physical device; a file / URL through OpenCV is not live camera evidence; an injected capture is a test double
    if capture_factory is not None:
        origin = Origin.SIMULATED
    else:
        origin = Origin.REAL if isinstance(device, int) or str(device).startswith("/dev/video") else Origin.UNKNOWN
    source = CameraFrameSource(open_capture, label=str(device), origin=origin, read_timeout_s=args.read_timeout_s)
    try:
        show = None if args.no_display else opencv_window()
    except ImportError:
        print("ERROR: the window needs OpenCV; use --no-display or install it (needs approval)", file=err)
        return 2
    print(f"[{LABEL}]", file=out)
    try:
        run(source, detector, max_frames=args.max_frames, duration_s=args.duration_s, show=show, print_every=args.print_every, out=out)
    except CameraUnavailable as exc:
        print(f"ERROR: {exc}", file=err)
        return 2
    except CameraError as exc:
        print(f"ERROR: camera failure: {exc}", file=err)
        return 3
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=err)
        return 3
    finally:
        if show is not None:
            try:
                import cv2
                cv2.destroyAllWindows()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
