#!/usr/bin/env python3
"""Tests for Conflict Detector (P0-1)."""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from conflict_detector import (
    CONFLICT_DONE_UNVERIFIED, CONFLICT_MEMORY_VS_REPO,
    CONFLICT_OPS_VS_GIT, CONFLICT_SHA_MISMATCH, CONFLICT_STALE_MEMORY,
    ALL_CONFLICT_TYPES, SEVERITY_ERROR, SEVERITY_WARNING,
    SEVERITY_INFO, SEVERITY_CRITICAL, DEFAULT_SEVERITY, SEVERITY_WEIGHT,
    GitEvidence, detect_all, detect_memory_vs_repo, detect_sha_mismatch,
    detect_stale_memory,
)

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


# ── Setup: create a temp git repo ───────────────────────────────────────────

TMP = Path(tempfile.mkdtemp(prefix="conflict-test-"))
REPO = TMP / "test-repo"
REPO.mkdir()

subprocess.run(["git", "init"], cwd=REPO, capture_output=True)
subprocess.run(["git", "config", "user.email", "test@test"], cwd=REPO, capture_output=True)
subprocess.run(["git", "config", "user.name", "Test"], cwd=REPO, capture_output=True)

(REPO / "README.md").write_text("# Test")
(REPO / "src").mkdir()
(REPO / "src" / "main.py").write_text("def main(): pass\n")
(REPO / "requirements.txt").write_text("openai>=1.0.0\npsycopg2-binary\n")

subprocess.run(["git", "add", "-A"], cwd=REPO, capture_output=True)
subprocess.run(["git", "commit", "-m", "Initial"], cwd=REPO, capture_output=True)
CURRENT_SHA = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
).stdout.strip()

GIT = GitEvidence(REPO)


# ═══════════════════════════════════════════════════════════════════════════════
# Test functions
# ═══════════════════════════════════════════════════════════════════════════════

def test_clean_no_conflicts():
    result = detect_all(REPO, memory_entries=[])
    assert result["status"] == "CLEAR", f"Expected CLEAR, got {result['status']}"
    assert result["conflicts"] == []
    assert result["requires_reconciliation"] is False


def test_memory_vs_repo_conflict():
    memory = [
        {"content": "This project requires redis-cache as a dependency", "source": "lesson-1"},
    ]
    conflicts = detect_memory_vs_repo(memory, GIT)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == CONFLICT_MEMORY_VS_REPO


def test_memory_vs_repo_no_conflict():
    memory = [
        {"content": "This project depends on README.md", "source": "lesson-2"},
    ]
    conflicts = detect_memory_vs_repo(memory, GIT)
    assert len(conflicts) == 0


def test_sha_mismatch():
    conflicts = detect_sha_mismatch("0000000", GIT)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == CONFLICT_SHA_MISMATCH


def test_sha_match():
    conflicts = detect_sha_mismatch(GIT.sha, GIT)
    assert len(conflicts) == 0


def test_stale_memory():
    memory = [
        {"content": "Fixed in commit abc1234", "source": "fix-1"},
    ]
    conflicts = detect_stale_memory(memory, GIT)
    assert len(conflicts) >= 1
    for c in conflicts:
        assert c["type"] == CONFLICT_STALE_MEMORY


def test_memory_vs_repo_multiple():
    memory = [
        {"content": "Requires nonexistent-package", "source": "m1"},
        {"content": "Depends on another-missing-lib", "source": "m2"},
    ]
    conflicts = detect_memory_vs_repo(memory, GIT)
    assert len(conflicts) == 2


def test_detect_all_with_sha_mismatch():
    result = detect_all(REPO, review_sha="0000000",
                         check_types={CONFLICT_SHA_MISMATCH})
    assert result["status"] == "CONFLICTED"
    assert result["summary"]["total"] == 1


def test_detect_all_with_memory():
    memory = [{"content": "Project requires redis-server", "source": "lesson"}]
    result = detect_all(REPO, memory_entries=memory,
                         check_types={CONFLICT_MEMORY_VS_REPO})
    assert result["status"] == "CONFLICTED"
    assert result["summary"]["total"] == 1


def test_git_evidence_smoke():
    assert len(GIT.sha) == 40
    assert GIT.branch in ("main", "master")
    assert "README.md" in GIT.tracked_files


def test_severity_classification():
    assert SEVERITY_ERROR == "error"
    assert SEVERITY_WARNING == "warning"
    assert SEVERITY_INFO == "info"
    assert SEVERITY_CRITICAL == "critical"


def test_severity_weights():
    assert SEVERITY_WEIGHT[SEVERITY_INFO] == 0
    assert SEVERITY_WEIGHT[SEVERITY_WARNING] == 1
    assert SEVERITY_WEIGHT[SEVERITY_ERROR] == 2
    assert SEVERITY_WEIGHT[SEVERITY_CRITICAL] == 3


def test_agentmemory_is_hint_only():
    """Memory conflicts should be info severity, not error."""
    assert DEFAULT_SEVERITY[CONFLICT_MEMORY_VS_REPO] == SEVERITY_INFO
    assert DEFAULT_SEVERITY[CONFLICT_STALE_MEMORY] == SEVERITY_INFO


def test_summary_includes_score():
    memory = [
        {"content": "Requires missing-dependency-12345", "source": "lesson"},
    ]
    result = detect_all(REPO, memory_entries=memory,
                        check_types={CONFLICT_MEMORY_VS_REPO})
    assert "severity_score" in result
    assert "severity_threshold" in result
    assert result["severity_score"] == 0  # info weight


def test_summary_counts():
    memory = [
        {"content": "Requires pkg-a-that-does-not-exist", "source": "m1"},
        {"content": "Depends on pkg-b-also-missing", "source": "m2"},
    ]
    result = detect_all(REPO, memory_entries=memory,
                         check_types={CONFLICT_MEMORY_VS_REPO})
    assert result["summary"]["total"] >= 1
    assert result["summary"]["by_type"][CONFLICT_MEMORY_VS_REPO] >= 1


def test_meta_in_result():
    result = detect_all(REPO)
    assert result["meta"]["repo_sha"] == GIT.sha
    assert result["meta"]["repo_branch"] == GIT.branch


def test_cli_help():
    r = subprocess.run(
        [sys.executable,
         str(Path(__file__).resolve().parent.parent / "scripts" / "conflict_detector.py"),
         "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "Detect conflicts" in r.stdout


def test_cli_clean():
    # Use the test temp repo for reliable results — pass its SHA
    r = subprocess.run(
        [sys.executable,
         str(Path(__file__).resolve().parent.parent / "scripts" / "conflict_detector.py"),
         "--repo", str(REPO),
         "--check", "SHA_MISMATCH",
         "--review-sha", CURRENT_SHA],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "DATABASE_URL": ""},
    )
    assert r.returncode == 0, f"stdout: {r.stdout[:200]} stderr: {r.stderr[:200]}"
    result = json.loads(r.stdout)
    assert result["status"] == "CLEAR"


def test_cli_mismatch():
    # Same repo, wrong SHA → CONFLICTED
    r = subprocess.run(
        [sys.executable,
         str(Path(__file__).resolve().parent.parent / "scripts" / "conflict_detector.py"),
         "--repo", str(REPO),
         "--check", "SHA_MISMATCH",
         "--review-sha", "0000000"],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "DATABASE_URL": ""},
    )
    assert r.returncode == 1, f"stdout: {r.stdout[:200]} stderr: {r.stderr[:200]}"
    result = json.loads(r.stdout)
    assert result["status"] == "CONFLICTED"


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Conflict Detector Tests (P0-1)")
    print(f"  Repo: {REPO}")
    print(f"  SHA:  {CURRENT_SHA[:10]}")
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