"""Structured logging setup shared by the audio module, tests, and demo.

Produces log lines carrying consistent key=value fields (event, device,
latency_ms, error) so later phases (STT, LLM, TTS) can extend the same
logger with their own latency fields without changing the approach.
"""

import logging

from config.settings import LOG_LEVEL

_CONFIGURED = False


def setup_logging() -> logging.Logger:
    """Return the shared "homepod_ai" logger, configuring it on first call."""
    global _CONFIGURED

    logger = logging.getLogger("homepod_ai")

    if not _CONFIGURED:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        logger.propagate = False
        _CONFIGURED = True

    return logger
