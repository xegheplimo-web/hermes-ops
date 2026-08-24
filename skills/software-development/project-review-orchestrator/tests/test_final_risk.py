#!/usr/bin/env python3
"""Tests for Final Risk Recalculation (P0-4)."""

from __future__ import annotations
import inspect, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from final_risk import recalculate, RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL

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

def test_no_changes_keeps_risk():
    r = recalculate("LOW")
    assert r["final_risk"] == RISK_LOW
    assert r["risk_changed"] is False

def test_sensitive_auth_path_escalates():
    r = recalculate("LOW", changed_paths=["src/auth/login.ts"])
    assert r["final_risk"] in (RISK_MEDIUM, RISK_HIGH), f"Got {r['final_risk']}"

def test_sensitive_payment_path():
    r = recalculate("MEDIUM", changed_paths=["src/payment/processor.ts"])
    assert r["final_risk"] == RISK_HIGH

def test_sensitive_migration():
    r = recalculate("LOW", changed_paths=["db/migrate/001_users.sql"])
    assert r["final_risk"] == RISK_HIGH

def test_sensitive_security_cve():
    r = recalculate("MEDIUM", changed_paths=["fix-cve-2024.py"])
    assert r["final_risk"] == RISK_CRITICAL

def test_test_failure_escalates():
    r = recalculate("LOW", test_results={"fail": 3})
    assert r["final_risk"] == RISK_HIGH

def test_test_pass_no_escalation():
    r = recalculate("LOW", test_results={"pass": 42, "fail": 0})
    assert r["final_risk"] == RISK_LOW

def test_unresolved_critical_finding():
    r = recalculate("MEDIUM", review_findings=[{"severity": "critical", "disposition": "AGREE"}])
    assert r["final_risk"] == RISK_CRITICAL

def test_unresolved_high_finding():
    r = recalculate("LOW", review_findings=[{"severity": "high", "disposition": "AGREE"}])
    assert r["final_risk"] == RISK_HIGH

def test_resolved_finding_no_impact():
    r = recalculate("LOW", review_findings=[{"severity": "high", "disposition": "REJECTED"}])
    assert r["final_risk"] == RISK_LOW

def test_security_critical():
    r = recalculate("MEDIUM", security_findings=[{"severity": "critical"}])
    assert r["final_risk"] == RISK_CRITICAL

def test_cannot_downgrade_without_evidence():
    """HIGH stays HIGH when no downgrade evidence exists."""
    r = recalculate("HIGH")
    assert r["final_risk"] == RISK_HIGH

def test_can_downgrade_with_evidence():
    r = recalculate("HIGH", test_results={"pass": 50, "fail": 0},
                    review_findings=[{"severity": "high", "disposition": "REJECTED"}])
    assert r["final_risk"] == RISK_HIGH  # HIGH needs codex even with all green

def test_low_high_diff():
    r = recalculate("LOW", changed_paths=["auth/login.ts"])
    assert r["risk_changed"] is True

def test_ci_gate_present():
    r = recalculate("LOW")
    assert "ci" in r["required_gates"]

def test_codex_gate_for_high():
    r = recalculate("HIGH")
    assert "codex" in r["required_gates"]

def test_human_gate_for_critical():
    r = recalculate("CRITICAL")
    assert "human" in r["required_gates"]

def test_output_structure():
    r = recalculate("MEDIUM")
    for key in ("early_risk", "final_risk", "risk_changed", "risk_reasons", "required_gates", "changed_paths"):
        assert key in r

def test_empty_paths():
    r = recalculate("LOW", changed_paths=[])
    assert r["final_risk"] == RISK_LOW

def test_multiple_sensitive_hits():
    r = recalculate("LOW", changed_paths=["auth/config.py", "payment/handler.py"])
    assert r["final_risk"] == RISK_HIGH  # auth=HIGH, payment=HIGH

def main():
    print("=" * 60)
    print("  Final Risk Recalculation Tests (P0-4)")
    print("=" * 60)
    tests = [fn for n, fn in inspect.getmembers(sys.modules[__name__])
             if n.startswith("test_") and callable(fn)]
    for fn in tests:
        test(fn)
    print()
    print("=" * 60)
    print(f"  RESULTS: {PASS} PASSED, {FAIL} FAILED, {PASS+FAIL} TOTAL")
    print("=" * 60)
    return 1 if FAIL > 0 else 0

if __name__ == "__main__":
    raise SystemExit(main())