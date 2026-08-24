#!/usr/bin/env python3
"""
Evidence Conflict Detector — P0

Detects inconsistencies between three runtime authorities:

  Git/Repo     = current implementation truth
  Ops DB       = runtime task state
  AgentMemory  = historical context / lessons

Outputs structured conflicts for Hermes to reconcile.
Does NOT auto-fix anything — detection only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ops_adapter import OpsDbAdapter
    _HAS_OPS = True
except ImportError:
    _HAS_OPS = False


# ── Conflict types ──────────────────────────────────────────────────────────

CONFLICT_MEMORY_VS_REPO = "MEMORY_VS_REPO"
CONFLICT_OPS_VS_GIT = "OPS_VS_GIT"
CONFLICT_DONE_UNVERIFIED = "DONE_UNVERIFIED"
CONFLICT_STALE_MEMORY = "STALE_MEMORY"
CONFLICT_SHA_MISMATCH = "SHA_MISMATCH"

SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

ALL_CONFLICT_TYPES = {
    CONFLICT_MEMORY_VS_REPO,
    CONFLICT_OPS_VS_GIT,
    CONFLICT_DONE_UNVERIFIED,
    CONFLICT_STALE_MEMORY,
    CONFLICT_SHA_MISMATCH,
}


# ── Data sources ────────────────────────────────────────────────────────────


class GitEvidence:
    """Snapshot of current Git state."""

    def __init__(self, repo_path: str | Path):
        self.repo = Path(repo_path).resolve()
        self._sha: str | None = None
        self._branch: str | None = None
        self._tracked_files: list[str] | None = None
        self._head_commit: dict | None = None

    @property
    def sha(self) -> str:
        if self._sha is None:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=self.repo,
            )
            self._sha = r.stdout.strip() if r.returncode == 0 else "unknown"
        return self._sha

    @property
    def branch(self) -> str:
        if self._branch is None:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=self.repo,
            )
            self._branch = r.stdout.strip() if r.returncode == 0 else "unknown"
        return self._branch

    @property
    def tracked_files(self) -> list[str]:
        if self._tracked_files is None:
            r = subprocess.run(
                ["git", "ls-files"], capture_output=True, text=True, cwd=self.repo,
            )
            self._tracked_files = r.stdout.splitlines() if r.returncode == 0 else []
        return self._tracked_files

    def file_content(self, path: str) -> str | None:
        """Get current file content from git HEAD."""
        r = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            capture_output=True, text=True, cwd=self.repo,
        )
        return r.stdout if r.returncode == 0 else None

    def diff_stat(self, base: str | None = None) -> list[dict]:
        """Get list of changed files (for final risk)."""
        ref = base or "HEAD~1"
        r = subprocess.run(
            ["git", "diff", "--numstat", ref, "HEAD"],
            capture_output=True, text=True, cwd=self.repo,
        )
        if r.returncode != 0:
            return []
        results: list[dict] = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                results.append({
                    "added": parts[0], "deleted": parts[1], "path": parts[2],
                })
        return results

    def snapshot(self) -> dict:
        return {
            "sha": self.sha,
            "branch": self.branch,
            "tracked_count": len(self.tracked_files),
        }


class OpsEvidence:
    """Snapshot of Ops DB state relevant to conflict detection.

    All methods return empty lists if Ops DB is unavailable.
    """

    def __init__(self, review_run_id: str | None = None):
        self._db = None
        self._review_run_id = review_run_id
        if _HAS_OPS and os.getenv("DATABASE_URL"):
            try:
                self._db = OpsDbAdapter()
            except Exception:
                self._db = None

    def _ensure_db(self):
        if self._db is None:
            return False
        try:
            self._db._ensure_connected()
            return True
        except Exception:
            return False

    def get_completed_tasks(self) -> list[dict]:
        """Get COMPLETED tasks that need verification evidence."""
        if not self._ensure_db():
            return []
        tasks = self._db.get_tasks_by_run(self._review_run_id) if self._review_run_id else []
        return [
            {"id": t.id, "external_id": t.external_id,
             "status": t.status, "dag_payload": t.dag_payload}
            for t in tasks if t.status == "completed"
        ]

    def get_tasks_missing_evidence(self) -> list[dict]:
        """Find COMPLETED tasks with no evidence records."""
        if not self._ensure_db():
            return []
        cur = self._db.conn.cursor()
        try:
            cur.execute("""
                SELECT t.id, t.external_id, t.status, t.review_run_id
                FROM tasks t
                LEFT JOIN evidence e ON e.task_id = t.id
                WHERE t.status = 'completed' AND e.id IS NULL
            """)
            return [
                {"id": r[0], "external_id": r[1], "status": r[2], "review_run_id": r[3]}
                for r in cur.fetchall()
            ]
        finally:
            cur.close()

    def close(self):
        if self._db:
            self._db.close()


# ── Conflict detection ──────────────────────────────────────────────────────


def detect_memory_vs_repo(memory_entries: list[dict], git: GitEvidence) -> list[dict]:
    """
    Check if AgentMemory claims conflict with current repo state.

    e.g. memory says "Redis required" but repo removed Redis dependency.
    """
    conflicts: list[dict] = []
    for entry in memory_entries:
        content = (entry.get("content") or "").lower()
        source = entry.get("source", "memory")
        # Simple keyword-based heuristic
        for keyword in ["required", "requires", "mandatory", "must be", "needs", "depends on", "depends upon"]:
            kw_lower = keyword.lower()
            if kw_lower not in content:
                continue
            # Extract potential package/dependency name
            # Find the keyword position and grab the next word
            idx = content.index(kw_lower)
            after = content[idx + len(kw_lower):].strip().strip(".,:;\"'()")
            dep = after.split()[0].strip(".,:;\"'()") if after else ""
            if dep and dep not in ("a", "an", "the", "this", "that", "it", "is", "be", "to", "by"):
                # Check if dep reference exists in repo
                if dep not in "\n".join(git.tracked_files).lower():
                    conflicts.append({
                        "type": CONFLICT_MEMORY_VS_REPO,
                        "severity": SEVERITY_WARNING,
                        "source": f"AgentMemory:{source}",
                        "claim": entry.get("content", ""),
                        "evidence": f"Dependency/component '{dep}' not found in current repo",
                        "current_sha": git.sha,
                        "rationale": (
                            f"AgentMemory records '{entry.get('content', '')[:100]}...' "
                            f"but git HEAD ({git.sha[:10]}) does not contain '{dep}'. "
                            f"This may indicate a stale memory."
                        ),
                    })
    return conflicts


def detect_ops_vs_git(ops: OpsEvidence, git: GitEvidence) -> list[dict]:
    """
    Check if Ops DB state matches git implementation.

    e.g. task DONE but no diff in expected files.
    """
    conflicts: list[dict] = []
    completed = ops.get_completed_tasks()
    for t in completed:
        dp = t.get("dag_payload", {})
        write_scope = dp.get("write_scope", [])
        if not write_scope:
            continue
        # Check if any of the write_scope files show changes in recent commits
        for scope_path in write_scope:
            # Check if scope path exists in tracked files
            if scope_path not in git.tracked_files and not any(
                scope_path in tf for tf in git.tracked_files
            ):
                conflicts.append({
                    "type": CONFLICT_OPS_VS_GIT,
                    "severity": SEVERITY_WARNING,
                    "source": f"task:{t['external_id']}",
                    "claim": f"Task completed but write_scope '{scope_path}' not in repo",
                    "evidence": f"Task {t['external_id']} status=completed, scope={scope_path}",
                    "current_sha": git.sha,
                    "rationale": (
                        f"Task {t['external_id']} is COMPLETED in Ops DB but the "
                        f"declared write_scope '{scope_path}' is not tracked by git. "
                        f"Either the scope was wrong or the task claim is inaccurate."
                    ),
                })
    return conflicts


def detect_done_unverified(ops: OpsEvidence) -> list[dict]:
    """
    Find COMPLETED tasks that lack verification evidence in Ops DB.

    Any COMPLETED task without a corresponding evidence row is suspect.
    """
    conflicts: list[dict] = []
    missing = ops.get_tasks_missing_evidence()
    for t in missing:
        conflicts.append({
            "type": CONFLICT_DONE_UNVERIFIED,
            "severity": SEVERITY_ERROR,
            "source": f"task:{t['external_id']}",
            "claim": f"Task {t['external_id']} COMPLETED without verification evidence",
            "evidence": f"Task id={t['id']}, status=completed, no evidence row found",
            "current_sha": "unknown",
            "rationale": (
                f"Task {t['external_id']} is COMPLETED in Ops DB but has no "
                f"corresponding evidence record. Per architecture rules, a COMPLETED "
                f"task must have verification evidence. This may indicate a false claim."
            ),
        })
    return conflicts


def detect_stale_memory(memory_entries: list[dict], git: GitEvidence) -> list[dict]:
    """
    Check if AgentMemory entries reference an older commit SHA.
    """
    conflicts: list[dict] = []
    for entry in memory_entries:
        content = entry.get("content", "")
        # Look for commit SHAs in memory (40-char hex or short 7-char hex)
        import re
        shas = re.findall(r'\b[0-9a-f]{7,40}\b', content)
        for sha in shas:
            if len(sha) >= 7 and not sha.startswith(git.sha[:10]):
                conflicts.append({
                    "type": CONFLICT_STALE_MEMORY,
                    "severity": SEVERITY_WARNING,
                    "source": f"AgentMemory:{entry.get('source', 'unknown')}",
                    "claim": f"Memory references commit {sha}, not current HEAD {git.sha[:10]}",
                    "evidence": f"Memory SHA: {sha}, Current SHA: {git.sha[:10]}",
                    "current_sha": git.sha,
                    "rationale": (
                        f"AgentMemory entry references commit {sha} but current HEAD "
                        f"is {git.sha[:10]}. Memory may be stale."
                    ),
                })
    return conflicts


def detect_sha_mismatch(review_sha: str | None, git: GitEvidence) -> list[dict]:
    """
    Check if the review was done on a different commit than current HEAD.
    """
    conflicts: list[dict] = []
    if review_sha and review_sha != git.sha:
        conflicts.append({
            "type": CONFLICT_SHA_MISMATCH,
            "severity": SEVERITY_WARNING,
            "source": "review_run",
            "claim": f"Review SHA {review_sha[:10]} ≠ current HEAD {git.sha[:10]}",
            "evidence": f"Review: {review_sha[:10]}, Current: {git.sha[:10]}",
            "current_sha": git.sha,
            "rationale": (
                f"The review run was created on commit {review_sha[:10]} but the "
                f"repo is currently at {git.sha[:10]}. Evidence and findings may be stale."
            ),
        })
    return conflicts


# ── Main detector ───────────────────────────────────────────────────────────


def detect_all(
    repo_path: str | Path,
    review_run_id: str | None = None,
    review_sha: str | None = None,
    memory_entries: list[dict] | None = None,
    check_types: set[str] | None = None,
) -> dict:
    """Run all configured conflict detectors and return structured result."""
    git = GitEvidence(repo_path)
    ops = OpsEvidence(review_run_id) if _HAS_OPS else None
    memory = memory_entries or []

    all_conflicts: list[dict] = []
    checks = check_types or ALL_CONFLICT_TYPES

    try:
        if CONFLICT_MEMORY_VS_REPO in checks and memory:
            all_conflicts.extend(detect_memory_vs_repo(memory, git))

        if CONFLICT_OPS_VS_GIT in checks and ops:
            all_conflicts.extend(detect_ops_vs_git(ops, git))

        if CONFLICT_DONE_UNVERIFIED in checks and ops:
            all_conflicts.extend(detect_done_unverified(ops))

        if CONFLICT_STALE_MEMORY in checks and memory:
            all_conflicts.extend(detect_stale_memory(memory, git))

        if CONFLICT_SHA_MISMATCH in checks:
            all_conflicts.extend(detect_sha_mismatch(review_sha, git))

    finally:
        if ops:
            ops.close()

    has_errors = any(c["severity"] == SEVERITY_ERROR for c in all_conflicts)
    status = "CONFLICTED" if all_conflicts else "CLEAR"

    return {
        "conflicts": all_conflicts,
        "status": status,
        "requires_reconciliation": bool(has_errors),
        "summary": {
            "total": len(all_conflicts),
            "by_type": {
                t: sum(1 for c in all_conflicts if c["type"] == t)
                for t in ALL_CONFLICT_TYPES
            },
            "by_severity": {
                SEVERITY_ERROR: sum(1 for c in all_conflicts if c["severity"] == SEVERITY_ERROR),
                SEVERITY_WARNING: sum(1 for c in all_conflicts if c["severity"] == SEVERITY_WARNING),
            },
        },
        "meta": {
            "repo_sha": git.sha,
            "repo_branch": git.branch,
            "review_run_id": review_run_id,
            "review_sha": review_sha,
            "check_types": sorted(checks),
        },
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect conflicts between Git, Ops DB, and AgentMemory."
    )
    parser.add_argument("--repo", default=".", help="Repository path (default: cwd)")
    parser.add_argument("--review-run-id", help="Review run ID for Ops DB queries")
    parser.add_argument("--review-sha", help="SHA the review was generated against")
    parser.add_argument("--memory-file", help="JSON file with AgentMemory entries")
    parser.add_argument("--check", nargs="*", choices=sorted(ALL_CONFLICT_TYPES),
                        help="Specific conflict types to check (default: all)")
    parser.add_argument("--out", help="Output file path (default: stdout)")
    args = parser.parse_args()

    memory: list[dict] = []
    if args.memory_file:
        mem_path = Path(args.memory_file)
        if mem_path.exists():
            raw = json.loads(mem_path.read_text(encoding="utf-8"))
            memory = raw if isinstance(raw, list) else raw.get("entries", [raw])

    check_types = set(args.check) if args.check else None

    try:
        result = detect_all(
            repo_path=args.repo,
            review_run_id=args.review_run_id,
            review_sha=args.review_sha,
            memory_entries=memory,
            check_types=check_types,
        )
        output = json.dumps(result, indent=2, ensure_ascii=False)

        if args.out:
            Path(args.out).write_text(output, encoding="utf-8")
        else:
            print(output)

        # Exit code: 0 = CLEAR, 1 = CONFLICTED
        return 1 if result["status"] == "CONFLICTED" else 0

    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())