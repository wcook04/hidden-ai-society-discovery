"""Check pinned retry decisions against independently authored JSON vectors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
from retry_budget import plan_next

SCHEMA = "hidden-ai-society-retry-budget-vectors-v1"
DECISION_FIELDS = {
    "state", "remaining_requests", "remaining_seconds", "budget_exceeded",
    "dispatch_allowed", "earliest_start_seconds", "wait_seconds",
    "request_timeout_seconds", "reason",
}


def check_vectors(path: Path) -> tuple[int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("unknown conformance-vector schema")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty array")
    names: set[str] = set()
    decisions = 0
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("each case needs a nonempty string id")
        name = case["id"]
        if name in names:
            raise ValueError(f"duplicate case id: {name}")
        names.add(name)
        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"{name}: steps must be a nonempty array")
        for number, step in enumerate(steps, 1):
            where = f"{name} step {number}"
            if not isinstance(step, dict) or not isinstance(step.get("input"), dict):
                raise ValueError(f"{where}: input must be an object")
            expected = step.get("expected")
            if not isinstance(expected, dict) or set(expected) != DECISION_FIELDS:
                raise ValueError(f"{where}: expected must name every decision field")
            try:
                actual = plan_next(**step["input"])
            except (TypeError, ValueError, OverflowError) as error:
                raise AssertionError(f"{where}: planner rejected the vector input: {error}") from error
            for field, want in expected.items():
                if field not in actual:
                    raise AssertionError(f"{where}: missing {field}")
                got = actual[field]
                # Python considers True == 1; a JSON boolean must stay a boolean.
                if type(got) is not type(want) or got != want:
                    raise AssertionError(f"{where}: {field}: expected {want!r}, got {got!r}")
            decisions += 1
    if data.get("case_count") != len(cases) or data.get("decision_count") != decisions:
        raise AssertionError("declared case or decision count differs from the vectors")
    return len(cases), decisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path,
                        default=ROOT / "tests" / "retry_budget_vectors_v1.json")
    args = parser.parse_args()
    try:
        cases, decisions = check_vectors(args.vectors)
    except (OSError, ValueError, AssertionError) as error:
        print(f"retry-budget conformance failed: {error}", file=sys.stderr)
        return 1
    print(f"retry-budget conformance passed: {cases} cases, {decisions} decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
