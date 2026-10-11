"""Abstract interface for audio input sources.

Downstream AI pipeline stages (wake word, VAD, STT - later phases) must
depend only on this interface, never on a specific capture backend, so
the backend or hardware can be swapped later without touching consumer
code.
"""

from abc import ABC, abstractmethod

import numpy as np


class AudioSource(ABC):
    """Contract that any PCM audio capture backend must implement."""

    @abstractmethod
    def start(self) -> None:
        """Open the device and begin streaming audio frames."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop streaming and release the device."""
        raise NotImplementedError

    @abstractmethod
    def read_frame(self, timeout: float = 1.0) -> np.ndarray:
        """Return the next available PCM frame as a numpy array."""
        raise NotImplementedError

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        raise NotImplementedError

    @property
    @abstractmethod
    def channels(self) -> int:
        raise NotImplementedError
