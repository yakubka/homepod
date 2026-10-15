from collections import deque
from typing import List, Optional

from .types import Decision


class DecisionLog:
    def __init__(self, keep: int = 2000):
        self.items = deque(maxlen=keep)

    def add(self, decisions: List[Decision]) -> None:
        self.items.extend(decisions)

    def query(
        self,
        limit: int = 50,
        since: Optional[float] = None,
        until: Optional[float] = None,
        rule: Optional[str] = None,
        applied: Optional[bool] = None,
    ) -> List[dict]:
        out = []
        for d in reversed(self.items):
            if since is not None and d.at < since:
                continue
            if until is not None and d.at > until:
                continue
            if rule is not None and d.action.rule != rule:
                continue
            if applied is not None and d.applied != applied:
                continue
            out.append(
                {
                    "at": d.at,
                    "rule": d.action.rule,
                    "kind": d.action.kind,
                    "target": d.action.target,
                    "value": d.action.value,
                    "inputs": d.action.inputs,
                    "applied": d.applied,
                    "suppressed_by": d.suppressed_by,
                    "line": d.line(),
                }
            )
            if len(out) >= limit:
                break
        return out
