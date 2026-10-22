"""Live camera FrameSource (midterm readiness). NOT HARDWARE-VALIDATED: no VESTA camera has been connected to this code yet.

The camera model and interface are still unknown (CLAUDE.md names none), so no camera API is assumed. The capture object is
INJECTED: anything with read() -> (ok, image) and release(). `open_opencv_capture` builds one from OpenCV's VideoCapture for a
USB / V4L2 camera; OpenCV is an OPTIONAL dependency imported only when that function is called (not installed in the project
environment today). A Raspberry Pi CSI camera on Pi 5 goes through libcamera and would need a separate Picamera2 capture object:
not written, because the camera has not been identified.

Rules: nothing opens at import; a camera that cannot open, a read that fails or times out raises CameraError (never an empty
frame, never "0 people"). Frames are converted from the capture's BGR to the pipeline's RGB.

Shutdown (bounded): each read runs on its own DAEMON thread, so a driver that never returns cannot keep the process alive. release()
is NEVER called while a read is in flight (VideoCapture is not safe to release from another thread mid-read): stop() hands the release
to the reading thread, which releases the device as soon as its read returns. After one timeout the source is STALLED and refuses
further reads. LIMIT: if the driver never returns from read(), the device is released only when the process exits (by the OS).
"""

import threading
from typing import Any, Callable, Optional, Union

import numpy as np

from fusion.contracts import Origin
from vision.frames import Frame, FrameSource, FrameSourceError


class CameraError(FrameSourceError):
    """The camera could not be opened or did not deliver a usable frame in time."""


class CameraUnavailable(CameraError):
    """The capture backend (OpenCV) is not installed."""


def open_opencv_capture(device: Union[int, str], *, width: Optional[int] = None, height: Optional[int] = None) -> Any:
    """Open a VideoCapture on `device` (an index such as 0, or a path such as /dev/video0). Raises CameraError if it does not open."""
    try:
        import cv2
    except ImportError as exc:
        raise CameraUnavailable("OpenCV (cv2) is not installed; it is needed for USB/V4L2 capture") from exc
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        cap.release()
        raise CameraError(f"camera {device!r} did not open")
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


class CameraFrameSource(FrameSource):
    """Frames from a live capture object. `origin` is stated by whoever builds the source: REAL only for a physical camera."""

    def __init__(self, open_capture: Callable[[], Any], *, label: str, origin: Origin, read_timeout_s: float = 5.0) -> None:
        if not callable(open_capture):
            raise TypeError("open_capture must be callable")
        if not isinstance(origin, Origin):
            raise TypeError("origin must be an Origin")
        if not read_timeout_s > 0:
            raise ValueError("read_timeout_s must be > 0")
        self._open, self.label, self.origin, self._timeout = open_capture, label, origin, float(read_timeout_s)
        self._cap = None
        self._lock = threading.Lock()
        self._in_flight = set()          # ids of the CAPTURES that have a read() running on a reader thread
        self._release_pending = set()    # ids of captures stop() handed to their reader thread to release
        self._stalled = False
        self._index = 0

    @property
    def release_deferred(self) -> bool:
        """True while a stopped capture waits for its stuck read() to return before it is released."""
        with self._lock:
            return bool(self._release_pending)

    def start(self) -> None:
        if self._cap is None:
            self._cap = self._open()
            self._stalled, self._index = False, 0

    def stop(self) -> None:
        cap, self._cap = self._cap, None
        if cap is None:
            return
        with self._lock:
            if id(cap) in self._in_flight:            # never release under a running read: its reader releases it
                self._release_pending.add(id(cap))
                return
        cap.release()

    def _read(self, cap, box: dict, done: threading.Event) -> None:
        try:
            box["value"] = cap.read()
        except Exception as exc:                      # reported by read_frame, never swallowed
            box["error"] = exc
        finally:
            with self._lock:                          # per capture: an old stuck read never clears a newer capture's state
                self._in_flight.discard(id(cap))
                release = id(cap) in self._release_pending
                self._release_pending.discard(id(cap))
            done.set()
            if release:
                cap.release()

    def read_frame(self) -> Frame:
        cap = self._cap
        if cap is None:
            raise CameraError("camera not started")
        if self._stalled:
            raise CameraError("the camera stalled earlier; stop and restart it")
        box, done = {}, threading.Event()
        with self._lock:
            self._in_flight.add(id(cap))
        # ponytail: one short-lived daemon thread per frame (cheap next to a detector pass); a persistent reader if profiling says so
        threading.Thread(target=self._read, args=(cap, box, done), name="camera-read", daemon=True).start()
        if not done.wait(self._timeout):
            self._stalled = True
            raise CameraError(f"no frame within {self._timeout} s")
        if "error" in box:
            raise CameraError(f"the camera read failed: {type(box['error']).__name__}") from box["error"]
        ok, image = box["value"] if isinstance(box.get("value"), tuple) and len(box["value"]) == 2 else (False, None)
        if not ok or not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise CameraError("the camera returned no usable frame")
        frame = Frame(np.ascontiguousarray(image[:, :, ::-1]), self._index, f"camera:{self.label}")
        self._index += 1
        return frame
