#!/usr/bin/env python3
"""
Strategy Router — P0

Deterministic routing of tasks to execution strategies based on task type and risk.

Each route specifies:
  - strategy: ordered list of skills/disciplines to apply
  - required_gates: gates that MUST pass (ci, codex, human, security)
  - max_attempts: maximum repair attempts before escalation
  - spec_level: none | lightweight | formal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from model_resolver import resolve, resolve_for_task
    _HAS_RESOLVER = True
except ImportError:
    _HAS_RESOLVER = False

# ── Task types ──────────────────────────────────────────────────────────────

TASK_FEATURE = "FEATURE"
TASK_BUG = "BUG"
TASK_SECURITY = "SECURITY"
TASK_REFACTOR = "REFACTOR"
TASK_PERFORMANCE = "PERFORMANCE"
TASK_INFRA = "INFRA"
TASK_CONFIG = "CONFIG"
TASK_MIGRATION = "MIGRATION"
TASK_INVESTIGATION = "INVESTIGATION"

# ── Routes ──────────────────────────────────────────────────────────────────

ROUTES: dict[str, dict] = {
    TASK_FEATURE: {
        "strategy": ["acceptance-criteria", "tdd", "implementation", "verification"],
        "required_gates": ["ci"],
        "max_attempts": 3,
        "spec_level": "lightweight",
        "description": "Feature: spec → TDD → implement → verify",
    },
    TASK_BUG: {
        "strategy": ["systematic-debugging", "regression-test", "fix", "verification"],
        "required_gates": ["ci"],
        "max_attempts": 3,
        "spec_level": "none",
        "description": "Bug: debug → regression test → fix → verify",
    },
    TASK_SECURITY: {
        "strategy": ["systematic-debugging", "threat-review", "fix", "security-verification"],
        "required_gates": ["ci", "codex", "security"],
        "max_attempts": 2,
        "spec_level": "formal",
        "description": "Security: threat review → fix → Codex review → security verify",
    },
    TASK_REFACTOR: {
        "strategy": ["characterization-test", "refactor", "verification"],
        "required_gates": ["ci"],
        "max_attempts": 3,
        "spec_level": "lightweight",
        "description": "Refactor: characterization test → refactor → verify",
    },
    TASK_PERFORMANCE: {
        "strategy": ["benchmark", "optimize", "re-benchmark", "verification"],
        "required_gates": ["ci"],
        "max_attempts": 3,
        "spec_level": "lightweight",
        "description": "Performance: benchmark → optimize → re-benchmark → verify",
    },
    TASK_INFRA: {
        "strategy": ["validate", "implement", "verification"],
        "required_gates": ["ci"],
        "max_attempts": 3,
        "spec_level": "none",
        "description": "Infra: validate → implement → verify",
    },
    TASK_CONFIG: {
        "strategy": ["validate", "apply", "verification"],
        "required_gates": ["ci"],
        "max_attempts": 2,
        "spec_level": "none",
        "description": "Config: validate → apply → verify (no TDD)",
    },
    TASK_MIGRATION: {
        "strategy": ["backup", "migrate", "verify-migration", "rollback-plan"],
        "required_gates": ["ci"],
        "max_attempts": 2,
        "spec_level": "formal",
        "description": "Migration: backup → migrate → verify → rollback plan",
    },
    TASK_INVESTIGATION: {
        "strategy": ["explore", "report"],
        "required_gates": [],
        "max_attempts": 1,
        "spec_level": "none",
        "description": "Investigation: explore → report findings (no code changes)",
    },
}


def get_route(task_type: str, risk: str | None = None) -> dict:
    """Get the execution route for a task type.

    Returns the base route, possibly escalated by risk level.
    """
    route = ROUTES.get(task_type)
    if not route:
        # Default to FEATURE route for unknown types
        route = ROUTES[TASK_FEATURE]

    # Clone to avoid mutation
    result = dict(route)
    result["task_type"] = task_type

    # Escalate gates based on risk
    if risk:
        risk_upper = risk.upper()
        if risk_upper in ("HIGH", "CRITICAL"):
            existing_gates = set(result.get("required_gates", []))
            if "codex" not in existing_gates:
                result["required_gates"] = list(existing_gates) + ["codex"]
                result["risk_escalation"] = f"Codex review added due to {risk_upper} risk"
            if risk_upper == "CRITICAL":
                if "human" not in existing_gates:
                    result["required_gates"] = result["required_gates"] + ["human"]
                    result["risk_escalation"] = "Human approval required for CRITICAL risk"

    # Resolve the model/role allocation for this task.
    if _HAS_RESOLVER and risk:
        try:
            assignment = resolve_for_task(risk, task_type)
            result["preferred_model"] = assignment.preferred
            result["model"] = assignment.primary
            result["model_stage"] = assignment.stage
            result["model_fallbacks"] = list(assignment.fallbacks)
            result["model_executor"] = assignment.executor
        except Exception:
            pass

    return result


def get_strategy_plan(
    task_type: str | None = None,
    risk: str | None = None,
    task_dict: dict | None = None,
) -> dict:
    """Get a full strategy plan for a task, considering both type and risk."""
    tt = task_type or (task_dict or {}).get("task_type", TASK_FEATURE)
    r = risk or (task_dict or {}).get("early_risk") or (task_dict or {}).get("risk", "low")
    return get_route(tt, r)


# ── Batch routing ───────────────────────────────────────────────────────────


def route_tasks(tasks: list[dict]) -> list[dict]:
    """Route a list of task DAG nodes through the strategy router."""
    results: list[dict] = []
    for t in tasks:
        plan = get_strategy_plan(task_dict=t)
        results.append({
            "task_id": t.get("task_id", "?"),
            "task_type": plan["task_type"],
            "strategy": plan["strategy"],
            "required_gates": plan["required_gates"],
            "max_attempts": plan["max_attempts"],
            "spec_level": plan["spec_level"],
            "description": plan["description"],
        })
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Route tasks to execution strategies."
    )
    parser.add_argument("--tasks", help="JSON file with task array")
    parser.add_argument("--task-type", choices=list(ROUTES.keys()) + [t.lower() for t in ROUTES.keys()],
                        help="Single task type to route")
    parser.add_argument("--risk", choices=["LOW", "MEDIUM", "HIGH", "CRITICAL", "low", "medium", "high", "critical"],
                        help="Risk level")
    parser.add_argument("--out", help="Output JSON file")
    args = parser.parse_args()

    if args.tasks:
        raw = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            tasks = raw.get("tasks", [raw])
        else:
            tasks = raw
        results = route_tasks(tasks)
    elif args.task_type:
        tt = args.task_type.upper()
        if tt not in ROUTES:
            print(json.dumps({"ok": False, "error": f"Unknown task type: {tt}"}))
            return 1
        plan = get_route(tt, args.risk.upper() if args.risk else None)
        results = [plan]
    else:
        # List all routes
        results = []
        for tt in sorted(ROUTES.keys()):
            results.append(get_route(tt))

    output = json.dumps(results, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())