"""JSON line logging with a trace id that follows one interaction.

The trace id is the request id of a voice utterance or an app call. The bus
copies it into the envelope correlation_id on publish and back into the
consumer's context on receive, so every log line of one interaction carries the
same trace_id, from the utterance to the device command:

    grep '"trace_id": "req_8d1f6g3h2j"' vesta.log
"""

import contextvars
import json
import logging
import random
import string
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

trace_id = contextvars.ContextVar("trace_id", default=None)

_ALPHABET = string.ascii_lowercase + string.digits
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def new_id(prefix):
    return f"{prefix}_" + "".join(random.choices(_ALPHABET, k=10))


def new_request_id():
    return new_id("req")


def iso_utc(seconds=None):
    moment = datetime.now(timezone.utc) if seconds is None else datetime.fromtimestamp(seconds, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


@contextmanager
def trace(value=None):
    token = trace_id.set(value or new_request_id())
    try:
        yield trace_id.get()
    finally:
        trace_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {
            "ts": iso_utc(record.created),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        current = getattr(record, "trace_id", None) or trace_id.get()
        if current:
            out["trace_id"] = current
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in out and not key.startswith("_"):
                out[key] = value
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s [%(name)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")

    def format(self, record):
        line = super().format(record)
        current = getattr(record, "trace_id", None) or trace_id.get()
        return f"{line} trace={current}" if current else line


def configure(level="info", fmt="json", stream=None):
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    return handler
