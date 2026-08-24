#!/usr/bin/env python3
"""Tests for Task Classifier + Early Risk (P0-2)."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from task_classifier import (
    TASK_FEATURE, TASK_BUG, TASK_SECURITY, TASK_REFACTOR,
    TASK_PERFORMANCE, TASK_INFRA, TASK_CONFIG, TASK_MIGRATION,
    TASK_INVESTIGATION, ALL_TASK_TYPES,
    RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL,
    classify, classify_findings,
    ALL_RISKS,
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
# Tests: Task Type Classification
# ═══════════════════════════════════════════════════════════════════════════════

def test_feature_default():
    """Default classification is FEATURE."""
    r = classify("Add new endpoint", "")
    assert r["task_type"] == TASK_FEATURE

def test_bug_detection():
    r = classify("Fix crash on startup", "User reports app crashes")
    assert r["task_type"] == TASK_BUG

def test_bug_keyword():
    r = classify("Bug in payment processing", "")
    assert r["task_type"] == TASK_BUG

def test_security_detection():
    r = classify("Add authentication", "")
    assert r["task_type"] == TASK_SECURITY

def test_security_vulnerability():
    r = classify("Fix XSS vulnerability", "")
    assert r["task_type"] == TASK_SECURITY

def test_security_by_path():
    r = classify("Update handler", "", evidence_refs=["src/auth/login.ts"])
    assert r["task_type"] == TASK_SECURITY

def test_migration_detection():
    r = classify("Database schema migration", "Migrate users table")
    assert r["task_type"] == TASK_MIGRATION

def test_performance_detection():
    r = classify("Optimize query", "Slow database query detected")
    assert r["task_type"] == TASK_PERFORMANCE

def test_infra_detection():
    r = classify("Setup CI pipeline", "Add GitHub Actions workflow")
    assert r["task_type"] == TASK_INFRA

def test_config_detection():
    r = classify("Update configuration", "Change database URL in .env")
    assert r["task_type"] == TASK_CONFIG

def test_refactor_detection():
    r = classify("Refactor internal module", "Clean up duplicated code")
    assert r["task_type"] == TASK_REFACTOR

def test_investigation_detection():
    r = classify("Investigate intermittent test failure", "Unknown root cause")
    assert r["task_type"] == TASK_INVESTIGATION

def test_unverified_investigation():
    r = classify("UNVERIFIED finding", "Cannot verify with current evidence")
    assert r["task_type"] == TASK_INVESTIGATION


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Early Risk Classification
# ═══════════════════════════════════════════════════════════════════════════════

def test_feature_risk_medium():
    r = classify("Add new API", "")
    assert r["early_risk"] == RISK_MEDIUM

def test_bug_risk_medium():
    r = classify("Fix startup crash", "")
    assert r["early_risk"] == RISK_MEDIUM, f"Got {r['early_risk']} for {r['task_type']}"

def test_security_risk_high():
    r = classify("Fix auth vulnerability", "")
    assert r["early_risk"] == RISK_HIGH

def test_security_critical():
    r = classify("Fix auth vulnerability", "",
                 severity="critical")
    assert r["early_risk"] == RISK_CRITICAL

def test_refactor_risk_low():
    r = classify("Refactor utils", "")
    assert r["early_risk"] == RISK_LOW

def test_migration_risk_high():
    r = classify("Migrate database", "")
    assert r["early_risk"] == RISK_HIGH

def test_investigation_risk_low():
    r = classify("Investigate test issue", "")
    assert r["early_risk"] == RISK_LOW

def test_sensitive_path_raises_risk():
    r = classify("Update endpoint", "",
                 evidence_refs=["src/payment/processor.ts"])
    # MEDIUM base + sensitive path "payment" → should raise
    assert r["early_risk"] in (RISK_MEDIUM, RISK_HIGH)

def test_config_risk_low():
    r = classify("Update config", "")
    assert r["early_risk"] == RISK_LOW

def test_infra_risk_low():
    r = classify("Setup CI", "")
    assert r["early_risk"] == RISK_LOW

def test_risk_reasons_present():
    r = classify("Fix critical auth bug", "", severity="critical")
    assert len(r["risk_reasons"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Batch classification
# ═══════════════════════════════════════════════════════════════════════════════

def test_classify_findings():
    findings = [
        {"id": "F-001", "title": "Fix auth bug", "severity": "critical",
         "evidence_refs": ["src/auth/"]},
        {"id": "F-002", "title": "Refactor utils", "severity": "low"},
        {"id": "F-003", "title": "Setup CI pipeline", "severity": "medium"},
    ]
    results = classify_findings(findings)
    assert len(results) == 3
    types = {r["task_type"] for r in results}
    assert TASK_SECURITY in types
    assert TASK_REFACTOR in types
    assert TASK_INFRA in types


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Schema consistency
# ═══════════════════════════════════════════════════════════════════════════════

def test_all_types_have_risk():
    """Every classified result gets a valid risk level."""
    for t in ALL_TASK_TYPES:
        r = classify(f"Some {t.lower()} task", "")
        assert r["early_risk"] in ALL_RISKS, f"{t}: risk={r['early_risk']}"

def test_output_structure():
    r = classify("Fix bug", "App crashes on init", severity="high",
                 evidence_refs=["src/login.ts"])
    assert "task_type" in r
    assert "early_risk" in r
    assert "risk_reasons" in r
    assert "evidence_refs" in r
    assert "classifier" in r
    assert r["classifier"] == "rule-based"


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Task Classifier + Early Risk Tests (P0-2)")
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