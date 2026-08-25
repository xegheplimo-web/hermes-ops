#!/usr/bin/env python3
"""Tests for the Python hermes-policy-gate bridge."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "scripts"))

from policy_gate import evaluate_gate, find_gate_binary

_TESTS_PASSED = 0
_TESTS_FAILED = 0


def test(name: str) -> Any:
    def decorator(fn):
        def wrapper(*args, **kwargs):
            global _TESTS_PASSED, _TESTS_FAILED
            try:
                result = fn(*args, **kwargs)
                _TESTS_PASSED += 1
                print(f"  ✅ {name}")
                return result
            except Exception as e:
                _TESTS_FAILED += 1
                print(f"  ❌ {name}: {e}")
                return None
        return wrapper
    return decorator


HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"


def _manifest() -> dict:
    return {
        "schemaVersion": 1,
        "repository": {"owner": "acme", "name": "hermes-ops"},
        "prNumber": 42,
        "headSha": HEAD_SHA,
        "policyVersion": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "artifacts": [{"path": "reports/coverage.json", "sha256": "a" * 64}],
        "ci": {"conclusion": "success"},
        "source": {"kind": "github-actions", "version": "0.1.0"},
    }


@test("finds the gate binary")
def test_find_binary() -> None:
    assert find_gate_binary() is not None


@test("clean manifest passes")
def test_clean_pass() -> None:
    r = evaluate_gate(_manifest(), HEAD_SHA, "0.1.0")
    assert r.gate == "PASS"
    assert r.decision == "pass"
    assert r.risk_level == "LOW"
    assert r.required_gates == ["ci"]


@test("CI failure is repairable")
def test_ci_failure_repair() -> None:
    manifest = _manifest()
    manifest["ci"] = {"conclusion": "failure"}
    r = evaluate_gate(manifest, HEAD_SHA, "0.1.0", attempts=0, max_attempts=3)
    assert r.gate == "REPAIR"
    assert r.reason_code == "CI_NOT_GREEN"


@test("CRITICAL without approval blocks")
def test_critical_blocks() -> None:
    manifest = _manifest()
    manifest["coderabbit"] = {"findings": [{"id": "f1", "severity": "critical", "resolved": False}]}
    r = evaluate_gate(manifest, HEAD_SHA, "0.1.0")
    assert r.gate == "BLOCK"
    assert r.reason_code == "HUMAN_APPROVAL_REQUIRED"
    assert r.risk_level == "CRITICAL"


@test("CRITICAL with approval token passes")
def test_critical_approved() -> None:
    manifest = _manifest()
    manifest["coderabbit"] = {"findings": [{"id": "f1", "severity": "critical", "resolved": False}]}
    token = {
        "signedAt": datetime.now(timezone.utc).isoformat(),
        "approver": "alice",
        "reason": "ack",
        "signature": "sig-test",
    }
    r = evaluate_gate(manifest, HEAD_SHA, "0.1.0", approval=token)
    assert r.gate == "REPAIR"
    assert r.risk_level == "CRITICAL"


@test("attempts exhausted escalates")
def test_attempts_exhausted() -> None:
    manifest = _manifest()
    manifest["ci"] = {"conclusion": "failure"}
    r = evaluate_gate(manifest, HEAD_SHA, "0.1.0", attempts=3, max_attempts=3)
    assert r.gate == "ESCALATE"


def main() -> int:
    print("=" * 60)
    print("  Policy Gate Bridge Tests")
    print("=" * 60)
    test_find_binary()
    test_clean_pass()
    test_ci_failure_repair()
    test_critical_blocks()
    test_critical_approved()
    test_attempts_exhausted()
    print("=" * 60)
    print(f"  Results: {_TESTS_PASSED} passed, {_TESTS_FAILED} failed")
    print("=" * 60)
    return 1 if _TESTS_FAILED > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
