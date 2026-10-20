"""Bus envelope validation against contracts/schemas."""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMAS = CONTRACTS / "schemas"


class ContractViolation(ValueError):
    def __init__(self, topic, errors):
        self.topic = topic
        self.errors = errors
        super().__init__(f"{topic} violates the contract: {'; '.join(errors[:3])}")


@lru_cache(maxsize=1)
def version():
    return (CONTRACTS / "VERSION").read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _validator():
    registry = Registry().with_resources(
        (path.as_uri(), Resource.from_contents(json.loads(path.read_text(encoding="utf-8")), default_specification=DRAFT202012))
        for path in SCHEMAS.glob("*.json")
    )
    return Draft202012Validator(
        {"$ref": (SCHEMAS / "BusEnvelope.json").as_uri()},
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def envelope_errors(envelope):
    return [
        f"{'.'.join(str(p) for p in error.absolute_path) or '$'}: {error.message}"
        for error in _validator().iter_errors(envelope)
    ]
