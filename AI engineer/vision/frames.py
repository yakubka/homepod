"""Frame sources for the VESTA camera perception pipeline (Week 4).

The person detector (vision/detector.py) only ever sees a `Frame`, so it
cannot tell where the pixels came from. Today the only real source is
ImageFileSource (offline images); the Raspberry Pi camera backend will be
a second FrameSource added once the real camera model and interface are
known (CLAUDE.md does not name them). No camera API is guessed here.

Frame layout: RGB, uint8, shape (height, width, 3). Detectors that want
another channel order or scaling convert internally (YOLOX wants BGR 0..255).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Union

import numpy as np

from config.logging_config import setup_logging

logger = setup_logging()


class FrameSourceError(RuntimeError):
    """A frame could not be produced (missing/unreadable file, bad array)."""


class EndOfFrames(Exception):
    """A finite source delivered every frame (same idea as audio EndOfAudio)."""


@dataclass(frozen=True)
class Frame:
    image: np.ndarray   # RGB uint8 (H, W, 3)
    index: int          # position in the source, starting at 0
    source: str         # human-readable origin, e.g. "image_file:room.jpg"

    def __post_init__(self) -> None:
        img = self.image
        if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 3:
            raise FrameSourceError("Frame.image must be a uint8 RGB array of shape (H, W, 3)")
        if img.shape[0] == 0 or img.shape[1] == 0:
            raise FrameSourceError("Frame.image must not be empty")

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


class FrameSource(ABC):
    """Contract for anything that delivers camera-like frames."""

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_frame(self) -> Frame:
        """Return the next frame; finite sources raise EndOfFrames at the end."""
        raise NotImplementedError


class ImageFileSource(FrameSource):
    """Replays image files (any format Pillow reads) as frames, in order."""

    def __init__(self, paths: Iterable[Union[str, Path]]) -> None:
        self._paths: List[Path] = [Path(p) for p in paths]
        if not self._paths:
            raise ValueError("ImageFileSource needs at least one image path")
        self._next = 0

    def start(self) -> None:
        self._next = 0

    def stop(self) -> None:
        pass

    def read_frame(self) -> Frame:
        if self._next >= len(self._paths):
            raise EndOfFrames()
        path = self._paths[self._next]
        try:
            from PIL import Image, ImageOps  # lazy: only file replay needs Pillow
            with Image.open(path) as img:
                # Phone photos are often stored sideways with an EXIF orientation tag; use the
                # upright picture a person sees (a no-op for files without the tag).
                upright = ImageOps.exif_transpose(img)
                rgb = np.asarray(upright.convert("RGB"), dtype=np.uint8).copy()
        except Exception as exc:  # missing file, corrupt data, unsupported format
            logger.error("event=frame_read_failed path=%s error=%s", path, exc)
            raise FrameSourceError(f"cannot read image {str(path)!r}: {exc}") from exc
        frame = Frame(image=rgb, index=self._next, source=f"image_file:{path.name}")
        self._next += 1
        return frame


class ArrayFrameSource(FrameSource):
    """TEST FIXTURE: serves ready-made RGB arrays. Not a camera, not an image dataset."""

    def __init__(self, images: Sequence[np.ndarray]) -> None:
        self._images = list(images)
        self._next = 0

    def start(self) -> None:
        self._next = 0

    def stop(self) -> None:
        pass

    def read_frame(self) -> Frame:
        if self._next >= len(self._images):
            raise EndOfFrames()
        frame = Frame(image=self._images[self._next], index=self._next, source="array_fixture")
        self._next += 1
        return frame
