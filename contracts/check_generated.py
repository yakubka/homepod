#!/usr/bin/env python3
"""Prove the generated models round-trip every schema example.

Python: each example is parsed with the matching room_contracts model, dumped
back to JSON and validated against its schema again.

Dart: the generated package is analysed with the recommended lints, then a
small program parses each example with the generated fromJson, serialises it
with toJson, and the output must equal the input once null entries are
ignored. Skipped with a notice when the dart executable is not on PATH,
unless --require-dart is given.

Usage: python contracts/check_generated.py [--require-dart]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(GENERATED / "python"))

import validate  # noqa: E402
from generate import DART_SCHEMAS, DART_PACKAGE  # noqa: E402


def examples(schemas: dict[str, dict]) -> list[tuple[str, int, Any]]:
    return [
        (name, index, example)
        for name, schema in schemas.items()
        if name != "common"
        for index, example in enumerate(schema.get("examples", []))
    ]


def without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_nulls(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def check_python(schemas: dict[str, dict], registry, problems: list[str]) -> int:
    import room_contracts

    checked = 0
    for name, index, example in examples(schemas):
        model = getattr(room_contracts, name, None)
        if model is None:
            problems.append(f"python: no model named {name}")
            continue
        try:
            dumped = model.model_validate(example).model_dump(mode="json", by_alias=True, exclude_unset=True)
        except Exception as exc:  # pydantic raises ValidationError, anything else is a generator bug
            problems.append(f"python {name} examples[{index}]: {exc}")
            continue
        for problem in validate.instance_errors(validate.validator_for(name, registry), dumped):
            problems.append(f"python {name} examples[{index}] after round trip: {problem}")
        checked += 1
    return checked


def lower_camel(name: str) -> str:
    return name[0].lower() + name[1:]


DART_PROGRAM = """import 'dart:convert';
import 'dart:io';

import 'package:{package}/{package}.dart';

final codecs = <String, String Function(String)>{{
{entries}
}};

void main(List<String> args) {{
  final cases = jsonDecode(File(args[0]).readAsStringSync()) as List<dynamic>;
  final results = <Map<String, dynamic>>[];
  for (final item in cases) {{
    final schema = item['schema'] as String;
    final output = codecs[schema]!(jsonEncode(item['example']));
    results.add({{'schema': schema, 'index': item['index'], 'json': jsonDecode(output)}});
  }}
  File(args[1]).writeAsStringSync(jsonEncode(results));
}}
"""


def dart(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    executable = shutil.which("dart")
    return subprocess.run([executable, *args], cwd=cwd, capture_output=True, text=True)


def check_dart(schemas: dict[str, dict], problems: list[str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        package = work / DART_PACKAGE
        shutil.copytree(GENERATED / "dart", package, ignore=shutil.ignore_patterns(".dart_tool", "pubspec.lock"))
        with (package / "pubspec.yaml").open("a", encoding="utf-8") as pubspec:
            pubspec.write("\ndev_dependencies:\n  lints: ^5.0.0\n")
        (package / "analysis_options.yaml").write_text("include: package:lints/recommended.yaml\n", encoding="utf-8")

        for step in (["pub", "get"], ["analyze", "--fatal-infos", "--fatal-warnings"]):
            result = dart(step, package)
            if result.returncode != 0:
                problems.append(f"dart {' '.join(step)} in generated package:\n{result.stdout}{result.stderr}")
                return 0

        harness = work / "harness"
        (harness / "bin").mkdir(parents=True)
        (harness / "pubspec.yaml").write_text(
            "name: contract_check\npublish_to: none\nenvironment:\n  sdk: \">=3.0.0 <4.0.0\"\n"
            f"dependencies:\n  {DART_PACKAGE}:\n    path: {package.as_posix()}\n",
            encoding="utf-8",
        )
        entries = "\n".join(
            f"  '{name}': (s) => {lower_camel(name)}ToJson({lower_camel(name)}FromJson(s))," for name in DART_SCHEMAS
        )
        (harness / "bin" / "check.dart").write_text(DART_PROGRAM.format(package=DART_PACKAGE, entries=entries), encoding="utf-8")

        cases = [
            {"schema": name, "index": index, "example": example}
            for name, index, example in examples(schemas)
            if name in DART_SCHEMAS
        ]
        (work / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        for step in (["pub", "get"], ["run", "bin/check.dart", str(work / "cases.json"), str(work / "out.json")]):
            result = dart(step, harness)
            if result.returncode != 0:
                problems.append(f"dart {' '.join(step[:2])} harness:\n{result.stdout}{result.stderr}")
                return 0

        results = json.loads((work / "out.json").read_text(encoding="utf-8"))
        for case, result in zip(cases, results):
            if without_nulls(case["example"]) != without_nulls(result["json"]):
                problems.append(
                    f"dart {case['schema']} examples[{case['index']}] changed in round trip:\n"
                    f"    in:  {json.dumps(case['example'])}\n    out: {json.dumps(result['json'])}"
                )
        return len(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--require-dart", action="store_true", help="fail instead of skipping when dart is missing")
    args = parser.parse_args()

    report = validate.Report()
    schemas = validate.load_schemas(report)
    registry = validate.build_registry(schemas)
    problems: list[str] = []

    python_count = check_python(schemas, registry, problems)
    if shutil.which("dart"):
        dart_count = check_dart(schemas, problems)
        dart_note = f"{dart_count} Dart round trips"
    elif args.require_dart:
        problems.append("dart is not on PATH")
        dart_note = "Dart not run"
    else:
        dart_note = "Dart skipped, dart is not on PATH"

    if problems:
        print(f"FAILED with {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"OK, {python_count} Python round trips, {dart_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
