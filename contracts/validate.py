#!/usr/bin/env python3
"""Validate the interface contract.

Checks, in order:
  1. Every file in schemas/ is a valid JSON Schema 2020-12 document, has a title,
     a description and examples, documents every property, and every $ref resolves.
  2. Every schema validates each of its own examples, followed by the contract
     rules JSON Schema cannot express (one decimal for Celsius, zone order,
     totals, time ranges).
  3. openapi.yaml passes openapi-spec-validator, contains every required
     endpoint, and every operation has a request example, a success example and
     an error example that validate against their schemas. Error examples must
     use the HTTP status their code maps to.
  4. JSON blocks in the markdown documents tagged with <!-- schema: Name -->
     validate against schemas/Name.json.
  5. VERSION matches openapi.yaml, every bus topic is documented in bus.md and
     every wire message type in protocol.md.

Usage: python contracts/validate.py
Exit code 0 when everything passes, 1 otherwise.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = ROOT / "schemas"
OPENAPI_FILE = ROOT / "openapi.yaml"
VERSION_FILE = ROOT / "VERSION"
DOC_FILES = ["README.md", "protocol.md", "bus.md"]

REQUIRED_SCHEMAS = [
    "Lecture",
    "OccupancySnapshot",
    "ZoneOccupancy",
    "EnvironmentReading",
    "LightingState",
    "ClimateState",
    "Decision",
    "Announcement",
    "Settings",
    "VoiceCommand",
    "DeviceCommand",
    "DeviceAck",
    "SensorReport",
    "ErrorResponse",
]

REQUIRED_OPERATIONS = [
    ("get", "/health"),
    ("get", "/state"),
    ("get", "/sensors"),
    ("get", "/occupancy"),
    ("get", "/lighting"),
    ("post", "/lighting/mode"),
    ("post", "/lighting/zone"),
    ("get", "/lectures"),
    ("post", "/lectures"),
    ("get", "/lectures/{id}"),
    ("patch", "/lectures/{id}"),
    ("delete", "/lectures/{id}"),
    ("get", "/lectures/current"),
    ("get", "/climate"),
    ("post", "/climate/target"),
    ("post", "/presentation"),
    ("get", "/announcements"),
    ("post", "/announcements/{id}/play"),
    ("get", "/decisions"),
    ("get", "/settings"),
    ("patch", "/settings"),
    ("get", "/history"),
    ("post", "/voice/ask"),
    ("get", "/events"),
]

ERROR_STATUS = {
    "invalid_input": "400",
    "unauthorized": "401",
    "not_found": "404",
    "conflict": "409",
    "internal": "500",
    "device_unavailable": "503",
    "degraded": "503",
    "device_timeout": "504",
}

UPLINK_TYPES = ["hello", "sensor_report", "ack", "error"]
ZONES = ["left", "center", "right", "professor"]
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
DOC_EXAMPLE = re.compile(r"<!--\s*schema:\s*(\w+)\s*-->\s*```json[^\n]*\n(.*?)```", re.DOTALL)
TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z$")
FORMAT_CHECKER = Draft202012Validator.FORMAT_CHECKER


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.counts: dict[str, int] = {}

    def fail(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def count(self, what: str, n: int = 1) -> None:
        self.counts[what] = self.counts.get(what, 0) + n


# ---------------------------------------------------------------- helpers


def parse_ts(value: str) -> datetime | None:
    match = TIMESTAMP.match(value)
    if not match:
        return None
    base, fraction = match.groups()
    micros = int((fraction or "0").ljust(6, "0"))
    return datetime.fromisoformat(base).replace(microsecond=micros, tzinfo=timezone.utc)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def decimals_ok(value: float, places: int) -> bool:
    return abs(round(value, places) - value) < 1e-9


def contract_rules(value: Any, path: str = "$") -> Iterator[str]:
    """Rules every instance must follow beyond what its JSON Schema checks."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from contract_rules(item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return

    for key, item in value.items():
        where = f"{path}.{key}"
        if key.endswith("_c") and is_number(item) and not decimals_ok(item, 1):
            yield f"{where}: {item} has more than one decimal place"
        if key in ("confidence", "min_confidence") and is_number(item) and not decimals_ok(item, 3):
            yield f"{where}: {item} has more than three decimal places"
        if key == "value" and is_number(item) and not decimals_ok(item, 1):
            yield f"{where}: {item} has more than one decimal place"

    for start_key, end_key in (("starts_at", "ends_at"), ("from", "to"), ("started_at", "expected_end_at")):
        start, end = value.get(start_key), value.get(end_key)
        if isinstance(start, str) and isinstance(end, str):
            start_ts, end_ts = parse_ts(start), parse_ts(end)
            if start_ts and end_ts and not start_ts < end_ts:
                yield f"{path}: {end_key} must be after {start_key}"

    if value.get("topic") == "system.heartbeat" and isinstance(value.get("payload"), dict):
        if value["payload"].get("component") != value.get("source"):
            yield f"{path}.payload.component: must equal the envelope source"

    zones = value.get("zones")
    if isinstance(zones, list) and zones and all(isinstance(z, dict) and "zone" in z for z in zones):
        names = [z["zone"] for z in zones]
        if len(set(names)) != len(names):
            yield f"{path}.zones: zone listed more than once"
        elif all(n in ZONES for n in names) and names != sorted(names, key=ZONES.index):
            yield f"{path}.zones: not in canonical order {ZONES}"
        for index, zone in enumerate(zones):
            if is_number(zone.get("count")) and isinstance(zone.get("occupied"), bool):
                if zone["occupied"] != (zone["count"] > 0):
                    yield f"{path}.zones[{index}]: occupied disagrees with count"
        if "total" in value and all("count" in z for z in zones):
            counts = [z["count"] for z in zones]
            expected = None if any(c is None for c in counts) else sum(counts)
            if value["total"] != expected:
                yield f"{path}.total: expected {expected}, found {value['total']}"
        if isinstance(value.get("occupied"), bool) and all(isinstance(z.get("occupied"), bool) for z in zones):
            if value["occupied"] != any(z["occupied"] for z in zones):
                yield f"{path}.occupied: disagrees with zones"

    for key, item in value.items():
        yield from contract_rules(item, f"{path}.{key}")


def walk(node: Any, path: str = "#") -> Iterator[tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for key, item in node.items():
            yield from walk(item, f"{path}/{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from walk(item, f"{path}/{index}")


# ---------------------------------------------------------------- schemas


def load_schemas(report: Report) -> dict[str, dict]:
    schemas: dict[str, dict] = {}
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        try:
            schemas[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.fail(f"schemas/{path.name}", f"invalid JSON: {exc}")
    for name in REQUIRED_SCHEMAS:
        if name not in schemas:
            report.fail("schemas", f"required schema {name}.json is missing")
    return schemas


def schema_uri(name: str) -> str:
    return (SCHEMA_DIR / f"{name}.json").as_uri()


def build_registry(schemas: dict[str, dict]) -> Registry:
    return Registry().with_resources(
        (schema_uri(name), Resource.from_contents(schema, default_specification=DRAFT202012))
        for name, schema in schemas.items()
    )


def validator_for(name: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator({"$ref": schema_uri(name)}, registry=registry, format_checker=FORMAT_CHECKER)


def instance_errors(validator: Draft202012Validator, instance: Any) -> list[str]:
    errors = []
    for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        location = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
        errors.append(f"{location}: {error.message}")
    errors.extend(contract_rules(instance))
    return errors


def check_schemas(schemas: dict[str, dict], registry: Registry, report: Report) -> None:
    for name, schema in schemas.items():
        where = f"schemas/{name}.json"
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            report.fail(where, f"not a valid JSON Schema: {exc.message}")
            continue

        for key in ("title", "description"):
            if not schema.get(key):
                report.fail(where, f"missing {key}")
        if not schema.get("examples"):
            report.fail(where, "missing examples")

        resolver = registry.resolver(base_uri=schema_uri(name))
        for pointer, node in walk(schema):
            if not isinstance(node, dict):
                continue
            ref = node.get("$ref")
            if isinstance(ref, str):
                try:
                    resolver.lookup(ref)
                except Unresolvable:
                    report.fail(where, f"{pointer}: unresolvable $ref {ref}")
            properties = node.get("properties")
            if isinstance(properties, dict) and not _inside_conditional(pointer):
                for prop, prop_schema in properties.items():
                    if isinstance(prop_schema, dict) and not prop_schema.get("description"):
                        report.fail(where, f"{pointer}/properties/{prop}: missing description")

        validator = validator_for(name, registry)
        for index, example in enumerate(schema.get("examples", [])):
            if name == "common":
                continue
            report.count("schema examples")
            for problem in instance_errors(validator, example):
                report.fail(f"{where} examples[{index}]", problem)
        report.count("schemas")


def _inside_conditional(pointer: str) -> bool:
    return bool({"if", "then", "else"} & set(pointer.split("/")))


def check_common_examples(schemas: dict[str, dict], registry: Registry, report: Report) -> None:
    """common.json has no root type, so its example is a map of definition name to instance."""
    common = schemas.get("common", {})
    lookup = {"zone": "Zone", "timestamp": "Timestamp", "zone_levels": "ZoneLevels", "action": "Action"}
    for index, example in enumerate(common.get("examples", [])):
        for key, value in example.items():
            definition = lookup.get(key)
            if not definition:
                report.fail(f"schemas/common.json examples[{index}]", f"unknown key {key}")
                continue
            validator = Draft202012Validator(
                {"$ref": f"{schema_uri('common')}#/$defs/{definition}"}, registry=registry, format_checker=FORMAT_CHECKER
            )
            report.count("schema examples")
            for problem in instance_errors(validator, value):
                report.fail(f"schemas/common.json examples[{index}].{key}", problem)


# ---------------------------------------------------------------- openapi


def resolve_local(document: dict, node: Any) -> Any:
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/"):
        target: Any = document
        for part in node["$ref"][2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        node = target
    return node


def inline_validator(schema: dict, registry: Registry) -> Draft202012Validator:
    rooted = {"$id": (ROOT / "openapi.inline.json").as_uri(), **schema}
    return Draft202012Validator(rooted, registry=registry, format_checker=FORMAT_CHECKER)


def media_examples(document: dict, media: dict) -> list[tuple[str, Any]]:
    found = []
    if "example" in media:
        found.append(("example", media["example"]))
    for key, example in (media.get("examples") or {}).items():
        example = resolve_local(document, example)
        if isinstance(example, dict) and "value" in example:
            found.append((key, example["value"]))
    return found


def sse_payloads(stream: str) -> list[Any]:
    payloads = []
    for line in stream.splitlines():
        if line.startswith("data:"):
            payloads.append(json.loads(line[len("data:"):].strip()))
    return payloads


def run_spec_validator(report: Report) -> None:
    try:
        from openapi_spec_validator import validate
        from openapi_spec_validator.readers import read_from_filename
    except ImportError:
        report.fail("openapi.yaml", "openapi-spec-validator is not installed, see requirements.txt")
        return
    try:
        spec, base_uri = read_from_filename(str(OPENAPI_FILE))
        validate(spec, base_uri=base_uri)
    except Exception as exc:  # the validator raises several unrelated exception types
        report.fail("openapi.yaml", f"openapi-spec-validator: {exc}")


def check_openapi(registry: Registry, report: Report) -> dict:
    if not OPENAPI_FILE.exists():
        report.fail("openapi.yaml", "missing")
        return {}
    document = yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))
    run_spec_validator(report)

    envelope = validator_for("BusEnvelope", registry)
    paths = document.get("paths", {})
    operations = {(method, path): op for path, item in paths.items() for method, op in item.items() if method in HTTP_METHODS}

    for method, path in REQUIRED_OPERATIONS:
        if (method, path) not in operations:
            report.fail("openapi.yaml", f"required operation {method.upper()} {path} is missing")

    for (method, path), operation in sorted(operations.items()):
        where = f"openapi.yaml {method.upper()} {path}"
        report.count("operations")
        for key in ("operationId", "summary", "tags"):
            if not operation.get(key):
                report.fail(where, f"missing {key}")
        if not operation.get("x-codeSamples"):
            report.fail(where, "missing x-codeSamples request example")

        parameters = [resolve_local(document, p) for p in paths[path].get("parameters", []) + operation.get("parameters", [])]
        for parameter in parameters:
            if "example" not in parameter:
                report.fail(where, f"parameter {parameter.get('name')} has no example")
                continue
            report.count("openapi examples")
            for problem in instance_errors(inline_validator(parameter.get("schema", {}), registry), parameter["example"]):
                report.fail(f"{where} parameter {parameter['name']}", problem)

        body = resolve_local(document, operation.get("requestBody"))
        if body:
            for media_type, media in body.get("content", {}).items():
                examples = media_examples(document, media)
                if not examples:
                    report.fail(where, f"request body {media_type} has no example")
                validator = inline_validator(media.get("schema", {}), registry)
                for key, value in examples:
                    report.count("openapi examples")
                    for problem in instance_errors(validator, value):
                        report.fail(f"{where} request example {key}", problem)

        success = error = False
        for status, response in operation.get("responses", {}).items():
            response = resolve_local(document, response)
            for media_type, media in (response.get("content") or {}).items():
                examples = media_examples(document, media)
                if not examples:
                    report.fail(where, f"response {status} {media_type} has no example")
                validator = inline_validator(media.get("schema", {}), registry)
                for key, value in examples:
                    report.count("openapi examples")
                    if media_type == "text/event-stream":
                        for index, event in enumerate(sse_payloads(value)):
                            for problem in instance_errors(envelope, event):
                                report.fail(f"{where} {status} {key} event {index}", problem)
                    else:
                        for problem in instance_errors(validator, value):
                            report.fail(f"{where} {status} example {key}", problem)
                    if str(status).startswith("2"):
                        success = True
                    elif isinstance(value, dict) and value.get("ok") is False:
                        error = True
                        code = value.get("error", {}).get("code")
                        if status != "default" and ERROR_STATUS.get(code) != str(status):
                            report.fail(where, f"example {key} uses code {code} under status {status}, expected {ERROR_STATUS.get(code)}")
        if not success:
            report.fail(where, "no success example")
        if not error:
            report.fail(where, "no error example")
    return document


# ---------------------------------------------------------------- documents


def check_documents(schemas: dict[str, dict], registry: Registry, report: Report) -> None:
    for doc in DOC_FILES:
        path = ROOT / doc
        if not path.exists():
            report.fail(doc, "missing")
            continue
        text = path.read_text(encoding="utf-8")
        for index, match in enumerate(DOC_EXAMPLE.finditer(text)):
            name, body = match.groups()
            where = f"{doc} example {index + 1} ({name})"
            if name not in schemas:
                report.fail(where, f"unknown schema {name}")
                continue
            try:
                instance = json.loads(body)
            except json.JSONDecodeError as exc:
                report.fail(where, f"invalid JSON: {exc}")
                continue
            report.count("document examples")
            for problem in instance_errors(validator_for(name, registry), instance):
                report.fail(where, problem)


def check_consistency(schemas: dict[str, dict], document: dict, report: Report) -> None:
    version = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        report.fail("VERSION", f"expected MAJOR.MINOR.PATCH, found {version!r}")
    if document and document.get("info", {}).get("version") != version:
        report.fail("openapi.yaml", f"info.version {document.get('info', {}).get('version')} differs from VERSION {version}")

    bus = (ROOT / "bus.md").read_text(encoding="utf-8") if (ROOT / "bus.md").exists() else ""
    for topic in schemas.get("common", {}).get("$defs", {}).get("BusTopic", {}).get("enum", []):
        if f"### `{topic}`" not in bus:
            report.fail("bus.md", f"topic {topic} has no section")

    protocol = (ROOT / "protocol.md").read_text(encoding="utf-8") if (ROOT / "protocol.md").exists() else ""
    downlink = schemas.get("DeviceCommand", {}).get("$defs", {}).get("DeviceCommandType", {}).get("enum", [])
    for message in downlink + UPLINK_TYPES:
        if f"### `{message}`" not in protocol:
            report.fail("protocol.md", f"message {message} has no section")


# ---------------------------------------------------------------- main


def main() -> int:
    report = Report()
    schemas = load_schemas(report)
    registry = build_registry(schemas)
    check_schemas(schemas, registry, report)
    check_common_examples(schemas, registry, report)
    document = check_openapi(registry, report)
    check_documents(schemas, registry, report)
    check_consistency(schemas, document, report)

    summary = ", ".join(f"{n} {what}" for what, n in report.counts.items())
    if report.errors:
        print(f"FAILED with {len(report.errors)} problem(s) after checking {summary}\n")
        for error in report.errors:
            print(f"  - {error}")
        return 1
    print(f"OK, checked {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
