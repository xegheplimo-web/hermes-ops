#!/usr/bin/env python3
"""Regression tests for final_risk.py CLI exit-code / JSON contract (CX-01).

Contract under test:
  * Every successful classification (LOW/MEDIUM/HIGH/CRITICAL) exits 0 and
    emits JSON containing the expected ``final_risk`` and ``required_gates``.
  * Malformed ``--changed-paths`` / ``--test-results`` JSON exits non-zero.
  * Missing ``--early-risk`` exits non-zero (argparse usage error).

These tests exercise the CLI end-to-end via ``subprocess.run``; they do NOT
import ``recalculate`` directly (that path is covered by
``tests/test_final_risk.py``). They intentionally never modify
``final_risk.py`` — the contract is locked: 0 = classification OK.

Usage:
    python test_final_risk.py
Exit code 0 = all tests passed, 1 = at least one failed.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "final_risk.py"

PASS = 0
FAIL = 0


def test(fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  PASS {fn.__name__}")
    except Exception as e:  # noqa: BLE001 - test harness reports any failure
        FAIL += 1
        print(f"  FAIL {fn.__name__}: {e}")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Invoke final_risk.py with the given CLI args; return the completed process."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _json_stdout(proc: subprocess.CompletedProcess) -> dict:
    """Parse the CLI's stdout as JSON, asserting it is valid JSON first."""
    assert proc.stdout, f"expected JSON on stdout, got empty stdout (exit={proc.returncode})"
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Successful classifications: exit 0 + expected final_risk / required_gates.
# ---------------------------------------------------------------------------

def test_low_exits_zero_with_ci_gate():
    proc = _run(["--early-risk", "LOW"])
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr!r}"
    r = _json_stdout(proc)
    assert r["final_risk"] == "LOW"
    assert r["required_gates"] == ["ci"]


def test_medium_exits_zero_with_ci_gate():
    proc = _run(["--early-risk", "MEDIUM"])
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr!r}"
    r = _json_stdout(proc)
    assert r["final_risk"] == "MEDIUM"
    assert r["required_gates"] == ["ci"]


def test_high_exits_zero_with_codex_gate():
    # HIGH with no downgrade evidence stays HIGH (cannot downgrade without evidence).
    proc = _run(["--early-risk", "HIGH"])
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr!r}"
    r = _json_stdout(proc)
    assert r["final_risk"] == "HIGH"
    assert r["required_gates"] == ["ci", "codex"]


def test_critical_exits_zero_with_human_gate():
    proc = _run(["--early-risk", "CRITICAL"])
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr!r}"
    r = _json_stdout(proc)
    assert r["final_risk"] == "CRITICAL"
    assert r["required_gates"] == ["ci", "codex", "human"]


def test_lowercase_early_risk_exits_zero():
    # argparse accepts lowercase forms via the choices list; output normalises to upper.
    proc = _run(["--early-risk", "high"])
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr!r}"
    r = _json_stdout(proc)
    assert r["final_risk"] == "HIGH"
    assert r["required_gates"] == ["ci", "codex"]


def test_output_contains_all_contract_keys():
    proc = _run(["--early-risk", "LOW"])
    assert proc.returncode == 0
    r = _json_stdout(proc)
    for key in ("early_risk", "final_risk", "risk_changed",
                "risk_reasons", "required_gates", "changed_paths"):
        assert key in r, f"missing contract key: {key}"


# ---------------------------------------------------------------------------
# Malformed inputs: must exit non-zero.
# ---------------------------------------------------------------------------

def test_malformed_changed_paths_exits_nonzero():
    proc = _run(["--early-risk", "LOW", "--changed-paths", "{not json"])
    assert proc.returncode != 0, (
        f"malformed --changed-paths must exit non-zero, got 0; stdout={proc.stdout!r}"
    )


def test_malformed_test_results_exits_nonzero():
    proc = _run(["--early-risk", "LOW", "--test-results", "not json either"])
    assert proc.returncode != 0, (
        f"malformed --test-results must exit non-zero, got 0; stdout={proc.stdout!r}"
    )


def test_malformed_review_findings_exits_nonzero():
    proc = _run(["--early-risk", "LOW", "--review-findings", "[oops]"])
    assert proc.returncode != 0, (
        f"malformed --review-findings must exit non-zero, got 0; stdout={proc.stdout!r}"
    )


def test_missing_early_risk_exits_nonzero():
    # No --early-risk at all -> argparse usage error (exit code 2).
    proc = _run([])
    assert proc.returncode != 0, (
        f"missing --early-risk must exit non-zero, got 0; stdout={proc.stdout!r}"
    )


def test_invalid_early_risk_choice_exits_nonzero():
    # argparse rejects values outside the choices list with a usage error.
    proc = _run(["--early-risk", "NONSENSE"])
    assert proc.returncode != 0, (
        f"invalid --early-risk choice must exit non-zero, got 0; stdout={proc.stdout!r}"
    )


def main() -> int:
    print("=" * 60)
    print("  final_risk.py CLI contract regression tests (CX-01)")
    print("=" * 60)
    tests = [fn for _n, fn in inspect.getmembers(sys.modules[__name__])
             if _n.startswith("test_") and callable(fn)]
    for fn in tests:
        test(fn)
    print()
    print("=" * 60)
    print(f"  RESULTS: {PASS} PASSED, {FAIL} FAILED, {PASS + FAIL} TOTAL")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
