#!/usr/bin/env python3
"""Smoke test for open_design.py on a temp git repo."""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PASS = 0
FAIL = 0
RESULTS = []


def test(fn):
    global PASS, FAIL
    RESULTS.append(fn.__name__)
    try:
        fn()
        PASS += 1
        print(f"  ✅ {fn.__name__}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {fn.__name__}: {e}")


# ── Setup temp git repo ─────────────────────────────────────────────────────
TMP = Path(tempfile.mkdtemp(prefix="open-design-test-"))
REPO = TMP / "test-repo"
REPO.mkdir()

subprocess.run(["git", "init"], cwd=REPO, capture_output=True)
subprocess.run(["git", "config", "user.email", "test@test"], cwd=REPO, capture_output=True)
subprocess.run(["git", "config", "user.name", "Test"], cwd=REPO, capture_output=True)

(REPO / "main.py").write_text("def hello(): return 1\n", encoding="utf-8")
(REPO / "README.md").write_text("# Test\n", encoding="utf-8")

subprocess.run(["git", "add", "-A"], cwd=REPO, capture_output=True)
subprocess.run(["git", "commit", "-m", "initial"], cwd=REPO, capture_output=True)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "open_design.py"
OUT = TMP / "open-design-run"


def test_cli_help():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "Open Design Orchestrator" in r.stdout


def test_full_dry_run():
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--repo", str(REPO),
         "--out", str(OUT),
         "--reviewer", "mock",
         "--skip-conflict-check"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"stdout: {r.stdout[:300]}\nstderr: {r.stderr[:300]}"
    state = json.loads((OUT / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "OUTCOME_COLLECTED", state
    assert state["progress"] == 100
    assert state["task_count"] >= 1
    assert (OUT / "task-plan.json").is_file()
    assert (OUT / "policy-gate.json").is_file()
    assert (OUT / "outcome.json").is_file()


def test_stop_after():
    out = TMP / "open-design-stop"
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--repo", str(REPO),
         "--out", str(out),
         "--reviewer", "mock",
         "--skip-conflict-check",
         "--stop-after", "RECONCILED"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "RECONCILED", state


def main():
    print("=" * 60)
    print("  Open Design Orchestrator Smoke Tests")
    print("=" * 60)

    tests = [fn for name, fn in inspect.getmembers(sys.modules[__name__])
             if name.startswith("test_") and callable(fn)]
    for fn in tests:
        test(fn)

    print()
    print("=" * 60)
    print(f"  RESULTS: {PASS} PASSED, {FAIL} FAILED, {PASS+FAIL} TOTAL")
    print("=" * 60)

    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
