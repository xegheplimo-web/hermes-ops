#!/usr/bin/env python3
"""Tests for Strategy Router (P0-3)."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from strategy_router import (
    TASK_FEATURE, TASK_BUG, TASK_SECURITY, TASK_REFACTOR,
    TASK_PERFORMANCE, TASK_INFRA, TASK_CONFIG, TASK_MIGRATION,
    TASK_INVESTIGATION,
    ROUTES, get_route, get_strategy_plan, route_tasks,
)

PASS = 0
FAIL = 0


def test(fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✅ {fn.__name__}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {fn.__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_every_type_has_route():
    """Every task type has a defined route."""
    for tt in [TASK_FEATURE, TASK_BUG, TASK_SECURITY, TASK_REFACTOR,
               TASK_PERFORMANCE, TASK_INFRA, TASK_CONFIG, TASK_MIGRATION,
               TASK_INVESTIGATION]:
        route = get_route(tt)
        assert route is not None, f"No route for {tt}"
        assert "strategy" in route
        assert "required_gates" in route
        assert "max_attempts" in route
        assert route["max_attempts"] >= 1

def test_feature_route():
    route = get_route(TASK_FEATURE)
    assert "tdd" in route["strategy"]
    assert "ci" in route["required_gates"]

def test_bug_route():
    route = get_route(TASK_BUG)
    strat = " ".join(route["strategy"])
    assert "debug" in strat
    assert "regression" in strat

def test_security_route():
    route = get_route(TASK_SECURITY)
    assert "threat" in " ".join(route["strategy"]).lower()
    assert "codex" in route["required_gates"]
    assert "security" in route["required_gates"]

def test_config_route():
    route = get_route(TASK_CONFIG)
    assert "tdd" not in " ".join(route["strategy"]).lower()
    assert route["spec_level"] == "none"

def test_migration_route():
    route = get_route(TASK_MIGRATION)
    assert any("backup" in s for s in route["strategy"])
    assert any("rollback" in s for s in route["strategy"])

def test_investigation_route():
    route = get_route(TASK_INVESTIGATION)
    assert "report" in route["strategy"]
    assert route["required_gates"] == []
    assert route["max_attempts"] == 1

def test_risk_escalation_high():
    """HIGH risk adds Codex gate."""
    route = get_route(TASK_FEATURE, "HIGH")
    assert "codex" in route["required_gates"]

def test_risk_escalation_critical():
    """CRITICAL risk adds Codex + Human gates."""
    route = get_route(TASK_FEATURE, "CRITICAL")
    assert "codex" in route["required_gates"]
    assert "human" in route["required_gates"]

def test_risk_escalation_does_not_duplicate():
    """Escalation doesn't duplicate existing gates."""
    route = get_route(TASK_SECURITY, "HIGH")
    counts = {g: route["required_gates"].count(g) for g in route["required_gates"]}
    for g, c in counts.items():
        assert c == 1, f"Gate '{g}' appears {c} times"

def test_no_escalation_for_low():
    """LOW risk doesn't add gates."""
    route = get_route(TASK_FEATURE, "LOW")
    assert "codex" not in route["required_gates"]
    assert "human" not in route["required_gates"]

def test_unknown_type_defaults_to_feature():
    """Unknown types get FEATURE route."""
    route = get_route("UNKNOWN")
    assert route["strategy"] == ROUTES[TASK_FEATURE]["strategy"]

def test_get_strategy_plan_from_dict():
    """get_strategy_plan accepts a task dict."""
    task = {"task_type": TASK_SECURITY, "early_risk": "HIGH"}
    plan = get_strategy_plan(task_dict=task)
    assert plan["task_type"] == TASK_SECURITY
    assert "codex" in plan["required_gates"]

def test_get_strategy_plan_with_params():
    """get_strategy_plan with explicit params."""
    plan = get_strategy_plan(TASK_BUG, "CRITICAL")
    assert plan["task_type"] == TASK_BUG
    assert "human" in plan["required_gates"]

def test_route_tasks_batch():
    """Batch routing works."""
    tasks = [
        {"task_id": "P1", "task_type": TASK_BUG, "risk": "medium"},
        {"task_id": "P2", "task_type": TASK_SECURITY, "risk": "high"},
        {"task_id": "P3", "task_type": TASK_CONFIG, "risk": "low"},
    ]
    results = route_tasks(tasks)
    assert len(results) == 3
    # P1: Bug → has debugging
    assert any("debug" in " ".join(r["strategy"]).lower() for r in results)
    # At least one has codex gate
    assert any("codex" in r["required_gates"] for r in results)

def test_all_routes_have_valid_structure():
    for tt, route in ROUTES.items():
        assert isinstance(route["strategy"], list), f"{tt}: strategy not list"
        assert len(route["strategy"]) >= 1, f"{tt}: empty strategy"
        assert isinstance(route["required_gates"], list), f"{tt}: gates not list"
        assert isinstance(route["max_attempts"], int), f"{tt}: max_attempts not int"
        assert route["max_attempts"] >= 1, f"{tt}: max_attempts < 1"

def test_spec_levels():
    """Spec levels are consistent with route type."""
    for tt, route in ROUTES.items():
        assert route["spec_level"] in ("none", "lightweight", "formal"), \
            f"{tt}: bad spec_level {route['spec_level']}"


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Strategy Router Tests (P0-3)")
    print("=" * 60)

    tests = [fn for name, fn in inspect.getmembers(sys.modules[__name__])
             if name.startswith("test_") and callable(fn)]
    for fn in tests:
        test(fn)

    print()
    print("=" * 60)
    print(f"  RESULTS: {PASS} PASSED, {FAIL} FAILED, {PASS+FAIL} TOTAL")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())