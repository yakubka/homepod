#!/usr/bin/env python3
"""Generate the Python and Dart models from contracts/schemas.

Python: every schema is bundled into one document and passed to
datamodel-code-generator, which writes generated/python/room_contracts/models.py.

Dart: the schemas the app uses are passed to quicktype, which writes
generated/dart/lib/room_contracts.dart. Null entries are dropped from toJson so
an omitted optional request field is never sent as null.

Both generators receive the schemas without their if/then rules and examples.
The rules are validation only and are checked by validate.py.

Usage: python contracts/generate.py [--check]
  --check  generate into a temporary directory and fail when the committed
           output differs, for CI.

Requires datamodel-code-generator (requirements.txt) and quicktype
(npm ci --prefix contracts).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = ROOT / "schemas"
GENERATED = ROOT / "generated"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

PYTHON_PACKAGE = "room_contracts"
DART_PACKAGE = "room_contracts"

# Schemas the app reads or sends. Wire-only and engine-internal schemas stay out
# of the Dart package. Enums they share are pulled in through references.
DART_SCHEMAS = [
    "ErrorResponse",
    "HealthStatus",
    "SystemState",
    "SensorsSnapshot",
    "DeviceLink",
    "EnvironmentReading",
    "PresenceReading",
    "OccupancySnapshot",
    "ZoneOccupancy",
    "LightingState",
    "LightingModeRequest",
    "ZoneLightRequest",
    "Lecture",
    "LectureCreate",
    "LecturePatch",
    "CurrentLecture",
    "LectureEvent",
    "ClimateState",
    "ClimateTargetRequest",
    "PresentationState",
    "PresentationRequest",
    "Announcement",
    "PlayAnnouncementRequest",
    "AnnouncementPlayback",
    "Decision",
    "Settings",
    "SettingsPatch",
    "HistorySeries",
    "VoiceAskRequest",
    "VoiceAskResponse",
    "VoiceCommand",
    "VoiceWake",
    "VoiceTranscript",
    "Heartbeat",
    "BusEnvelope",
]

CONDITIONAL_KEYS = {"if", "then", "else"}


def load_schemas() -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(SCHEMA_DIR.glob("*.json"))}


def strip_for_codegen(node: Any) -> Any:
    """Remove if/then rules and examples, which code generators misread."""
    if isinstance(node, list):
        return [strip_for_codegen(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for key, value in node.items():
        if key in CONDITIONAL_KEYS or key == "examples":
            continue
        if key == "allOf":
            kept = [item for item in value if not (isinstance(item, dict) and set(item) <= CONDITIONAL_KEYS)]
            if not kept:
                continue
            value = kept
        result[key] = strip_for_codegen(value)
    return result


# ---------------------------------------------------------------- python


def bundle(schemas: dict[str, dict]) -> dict:
    """Merge every schema file into one document with all types under $defs."""
    defs: dict[str, Any] = {}

    def add(name: str, schema: Any, origin: str) -> None:
        if name in defs:
            raise SystemExit(f"duplicate type name {name} in {origin}, names must be unique across schemas")
        defs[name] = schema

    for stem, schema in schemas.items():
        schema = strip_for_codegen(schema)
        for name, definition in schema.pop("$defs", {}).items():
            add(name, definition, f"{stem}.json")
        schema.pop("$schema", None)
        if stem != "common":
            if schema.get("title") != stem:
                raise SystemExit(f"{stem}.json: title must equal the file name")
            add(stem, schema, f"{stem}.json")

    # Scalar definitions such as Lux or Timestamp are inlined so the models use
    # plain int, float, str and datetime fields instead of wrapper classes.
    scalars = {
        name: {key: value for key, value in definition.items() if key not in ("title", "description")}
        for name, definition in defs.items()
        if definition.get("type") in ("integer", "number", "string") and "enum" not in definition and "const" not in definition
    }

    def rewrite(node: Any) -> Any:
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {key: rewrite(value) for key, value in node.items()}
        ref = result.get("$ref")
        if isinstance(ref, str):
            file_part, _, pointer = ref.partition("#")
            if pointer.startswith("/$defs/"):
                name = pointer[len("/$defs/"):]
            elif file_part.endswith(".json") and not pointer:
                name = Path(file_part).stem
            else:
                raise SystemExit(f"cannot bundle $ref {ref}")
            if name in scalars:
                siblings = {key: value for key, value in result.items() if key != "$ref"}
                result = {**copy.deepcopy(scalars[name]), **siblings}
            else:
                result["$ref"] = f"#/$defs/{name}"
        if result.get("format") == "date-time":
            result.pop("pattern", None)
        return result

    kept = {name: definition for name, definition in defs.items() if name not in scalars}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RoomContracts",
        "description": f"Every type of contract {VERSION}.",
        "$defs": rewrite(kept),
    }


ROOT_MODEL = re.compile(r"\nclass RoomContracts\(RootModel\[Any\]\):\n(?:    .*\n|\s*\n)*?(?=\n\nclass )")


def generate_python(schemas: dict[str, dict], out: Path, work: Path) -> None:
    package = out / "python" / PYTHON_PACKAGE
    package.mkdir(parents=True)
    bundle_file = work / "room_contracts.json"
    bundle_file.write_text(json.dumps(bundle(schemas), indent=2), encoding="utf-8")

    run(
        [
            sys.executable, "-m", "datamodel_code_generator",
            "--input", str(bundle_file),
            "--input-file-type", "jsonschema",
            "--output", str(package / "models.py"),
            "--output-model-type", "pydantic_v2.BaseModel",
            "--target-python-version", "3.10",
            "--collapse-root-models",
            "--use-standard-collections",
            "--use-union-operator",
            "--use-double-quotes",
            "--use-schema-description",
            "--use-title-as-name",
            "--extra-fields", "ignore",
            "--allow-population-by-field-name",
            "--disable-timestamp",
            "--formatters", "black", "isort",
            "--custom-file-header", f"# Generated from contracts/schemas, contract {VERSION}. Do not edit.\n"
                                    "# Regenerate with: python contracts/generate.py",
        ]
    )
    models = package / "models.py"
    code, removed = ROOT_MODEL.subn("", models.read_text(encoding="utf-8").replace("\r\n", "\n"))
    if removed != 1:
        raise SystemExit("unexpected datamodel-codegen output, bundle root model not found")
    models.write_text(code, encoding="utf-8", newline="\n")
    (package / "__init__.py").write_text(
        f'"""Typed models for room controller contract {VERSION}."""\n\n'
        "from .models import *  # noqa: F401,F403\n\n"
        f'CONTRACT_VERSION = "{VERSION}"\n',
        encoding="utf-8",
        newline="\n",
    )
    (package / "py.typed").write_text("", encoding="utf-8")
    (out / "python" / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        f'name = "{PYTHON_PACKAGE.replace("_", "-")}"\n'
        f'version = "{VERSION}"\n'
        'description = "Generated models for the room controller interface contract."\n'
        'requires-python = ">=3.10"\n'
        'dependencies = ["pydantic>=2.6,<3"]\n\n'
        "[tool.setuptools]\n"
        f'packages = ["{PYTHON_PACKAGE}"]\n\n'
        "[tool.setuptools.package-data]\n"
        f'{PYTHON_PACKAGE} = ["py.typed"]\n',
        encoding="utf-8",
        newline="\n",
    )


# ---------------------------------------------------------------- dart

TO_JSON_BLOCK = re.compile(r"(Map<String, dynamic> toJson\(\) => )\{\n(.*?)\n    \};", re.DOTALL)


def quicktype_command() -> list[str]:
    local = ROOT / "node_modules" / ".bin" / ("quicktype.cmd" if os.name == "nt" else "quicktype")
    if local.exists():
        return [str(local)]
    found = shutil.which("quicktype")
    if found:
        return [found]
    raise SystemExit("quicktype not found, run: npm ci --prefix contracts")


def generate_dart(schemas: dict[str, dict], out: Path, work: Path) -> None:
    stripped = work / "schemas"
    stripped.mkdir()
    for stem, schema in schemas.items():
        (stripped / f"{stem}.json").write_text(json.dumps(strip_for_codegen(schema), indent=2), encoding="utf-8")

    package = out / "dart"
    (package / "lib").mkdir(parents=True)
    raw = work / "room_contracts.dart"
    run(
        quicktype_command()
        + ["--lang", "dart", "--src-lang", "schema", "--quiet", "--copy-with", "--out", str(raw)]
        + [str(stripped / f"{name}.json") for name in DART_SCHEMAS]
    )

    code = raw.read_text(encoding="utf-8").replace("\r\n", "\n")
    code, count = TO_JSON_BLOCK.subn(
        lambda m: f"{m.group(1)}<String, dynamic>{{\n{m.group(2)}\n    }}..removeWhere((_, value) => value == null);", code
    )
    if count == 0:
        raise SystemExit("unexpected quicktype output, no toJson block found")
    import_line = "import 'dart:convert';\n"
    if import_line not in code:
        raise SystemExit("unexpected quicktype output, dart:convert import not found")
    code = code.replace(import_line, f'{import_line}\nconst String contractVersion = "{VERSION}";\n', 1)
    header = (
        "// GENERATED CODE - DO NOT MODIFY BY HAND\n"
        f"// Generated from contracts/schemas, contract {VERSION}.\n"
        "// Regenerate with: python contracts/generate.py\n"
        "//\n"
        "// ignore_for_file: constant_identifier_names, prefer_null_aware_operators\n\n"
    )
    (package / "lib" / f"{DART_PACKAGE}.dart").write_text(header + code, encoding="utf-8", newline="\n")
    (package / "pubspec.yaml").write_text(
        f"name: {DART_PACKAGE}\n"
        "description: Generated models for the room controller interface contract.\n"
        f"version: {VERSION}\n"
        "publish_to: none\n\n"
        "environment:\n"
        '  sdk: ">=3.0.0 <4.0.0"\n',
        encoding="utf-8",
        newline="\n",
    )


# ---------------------------------------------------------------- main


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"command failed: {Path(command[0]).name}")


IGNORED = ("__pycache__", ".dart_tool", "pubspec.lock", ".egg-info")


def _files(root: Path) -> dict:
    """Relative path to content, line endings normalised, build and install leftovers skipped.

    A Windows checkout turns LF into CRLF and pip install -e leaves an egg-info
    folder behind, neither makes the generated models stale.
    """
    files = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_file() and not any(part.endswith(IGNORED) for part in relative.parts):
            files[relative.as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    return files


def differences(left: Path, right: Path) -> list[str]:
    fresh, committed = _files(left), _files(right)
    return sorted(name for name in fresh.keys() | committed.keys() if fresh.get(name) != committed.get(name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when the committed output is out of date")
    args = parser.parse_args()

    schemas = load_schemas()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        out = work / "generated"
        generate_python(schemas, out, work)
        generate_dart(schemas, out, work)

        if args.check:
            stale = differences(out, GENERATED) if GENERATED.exists() else ["generated/"]
            if stale:
                print("generated models are out of date, run python contracts/generate.py:")
                for name in stale:
                    print(f"  - {name}")
                return 1
            print("generated models are up to date")
            return 0

        if GENERATED.exists():
            shutil.rmtree(GENERATED)
        shutil.copytree(out, GENERATED)
    print(f"generated Python and Dart models for contract {VERSION} in {GENERATED.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
