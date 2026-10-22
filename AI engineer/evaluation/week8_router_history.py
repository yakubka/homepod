"""Week 8 Task 5: historical routers, so that every earlier evaluation result stays reproducible without another full copy of the router.

    ORIGINAL TASK 1 ROUTER  evaluation/week8_task1_router_frozen.py        (a byte-identical frozen copy, Week 8 Task 4)
    TASK 4 ROUTER           rebuilt here from the CURRENT intents/router.py by reverse-applying evaluation/week8_router_task4_to_task5_edits.json
    TASK 5 / CURRENT ROUTER intents/router.py                              (the only router production code uses)

The Task 4 router is rebuilt in memory, never written to disk, and only if the result is byte-identical (SHA-256) to the recorded Task 4 router. If
intents/router.py changes again, the recorded Task 5 hash no longer matches and the rebuild is REFUSED with a clear message (extend the edit chain
then). Nothing here is used by production code.

    python -m evaluation.week8_router_history [--output DIR]      run both frozen corpora with every router that applies and print one comparison
"""

import argparse
import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Callable, Dict, Optional

from evaluation import week8_intent_eval as ev
from intents.contracts import IntentRequest, IntentResult

REPO = Path(__file__).resolve().parents[1]
ROUTER_FILE = REPO / "intents" / "router.py"
EDIT_SCRIPT = Path(__file__).resolve().parent / "week8_router_task4_to_task5_edits.json"
TASK4_ROUTER_SHA256 = "6d9c5d30c9cd470f22062bb04bb02f8b95382390fae83712d251392b3b2485ca"


class RouterHistoryError(Exception):
    """A historical router could not be rebuilt faithfully. Nothing was evaluated."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def task4_router_source() -> str:
    """The source text of the Task 4 router, rebuilt from the current router. Raises RouterHistoryError unless it is byte-identical to Task 4."""
    doc = json.loads(EDIT_SCRIPT.read_text(encoding="utf-8"))
    if doc.get("task4_sha256") != TASK4_ROUTER_SHA256:
        raise RouterHistoryError("the edit script does not name the recorded Task 4 router hash")
    current = ROUTER_FILE.read_bytes()
    if _sha(current) != doc["task5_sha256"]:
        raise RouterHistoryError("intents/router.py is not the Task 5 router this edit script was recorded against; extend the edit chain")
    text = current.decode("utf-8")
    for edit in reversed(doc["edits"]):
        if text.count(edit["new"]) != 1:
            raise RouterHistoryError("an edit does not apply exactly once; the rebuild is refused")
        text = text.replace(edit["new"], edit["old"])
    if _sha(text.encode("utf-8")) != TASK4_ROUTER_SHA256:
        raise RouterHistoryError("the rebuilt router is not byte-identical to the recorded Task 4 router; the rebuild is refused")
    return text


_CACHE: Dict[str, types.ModuleType] = {}


def load_task4_router_module() -> types.ModuleType:
    """The Task 4 router as an in-memory module (not registered in sys.modules, not on disk)."""
    if "task4" not in _CACHE:
        source = task4_router_source()
        module = types.ModuleType("evaluation._router_task4_baseline")
        module.__file__ = "<Task 4 router rebuilt from the edit script>"
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        _CACHE["task4"] = module
    return _CACHE["task4"]


def load_task4_router() -> Callable[[IntentRequest], IntentResult]:
    """route_intent of the Task 4 router (historical evidence, never the production router)."""
    return load_task4_router_module().route_intent


def routers() -> Dict[str, Callable[[IntentRequest], IntentResult]]:
    return {"task1-frozen": ev.load_router("task1-frozen"), "task4-baseline": load_task4_router(), "task5-current": ev.load_router("current")}


def _fmt(d: dict) -> str:
    return f"{d['numerator']}/{d['denominator']}"


def summarize(report: dict) -> list:
    a = report["strata"]["ALL"]
    cs = a["command_safety"]
    return [f"  ALL {a['n_items']:3} items | category {_fmt(a['category_accuracy'])} (gold ROUTED items only; rejections excluded) | outcome {_fmt(a['routing_outcome_agreement'])}"
            f" | macro F1 {a['macro_f1']['value']:.3f} over {a['macro_f1']['categories_included']}/6",
            f"      false command acceptance {_fmt(cs['false_command_acceptance'])} | wrong command {_fmt(cs['wrong_command_accepted'])}"
            f" | command field agreement (gold commands) {_fmt(cs['exact_command_agreement_of_gold_commands'])}"
            f" | invariant violations {cs['invariant_violations']['count']} | answer-check violations {a['answer_check']['violations']['count']}"
            f" | mismatches {[m['id'] for m in a['mismatches']]}"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the frozen Week 8 corpora with every historical router that applies.")
    parser.add_argument("--output", help="directory OUTSIDE the repository for the evidence JSON files")
    args = parser.parse_args(argv)
    rts = routers()
    plan = [("W8-INTENT-V1", ev.DEFAULT_MANIFEST, ("task1-frozen", "task4-baseline", "task5-current")),
            ("W8-INTENT-V2", ev.V2_MANIFEST, ("task4-baseline", "task5-current"))]
    for corpus, path, names in plan:
        manifest = ev.load_manifest(path)
        print(f"== {corpus} ({len(manifest.entries)} entries, phrase-table snapshot {manifest.snapshot})")
        for name in names:
            report = ev.evaluate(manifest, route=rts[name], router_name=name)
            print(f" {name}")
            print("\n".join(summarize(report)))
            if args.output:
                # one sub-directory per router: the evidence file name carries only the corpus id and a one-second stamp, so two routers would collide
                print("   evidence:", ev.write_report(report, Path(args.output) / name))
    print("REAL-WORLD INTENT ACCURACY: NOT MEASURED (same-author text corpora)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
