#!/usr/bin/env python3
"""
Integration tests for Ops DB authoritative task dispatch path.

Tests the full pipeline:
  decompose_tasks.py --ops-db → OpsDBAdapter → dispatch_to_devin.py --ops-db

Requires a running PostgreSQL with DATABASE_URL set.
Tests create and clean up their own data (external_id hashes are unique).

Usage:
    DATABASE_URL=postgres://hermes:hermesops@localhost:55432/hermes_ops \
    python test_ops_db_integration.py
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add skill scripts to path
_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "scripts"))

from ops_adapter import (
    AuditEvent, OpsDbAdapter, OpsTask,
    STATUS_PLANNING, STATUS_QUEUED, STATUS_PENDING, STATUS_RUNNING,
    STATUS_DISPATCHED, STATUS_VERIFYING, STATUS_COMPLETED, STATUS_FAILED,
    STATUS_CANCELLED, STATUS_BLOCKED,
    TERMINAL_STATUSES, is_valid_transition, make_external_id,
    make_evidence_identity, QUEUE_TRANSITIONS,
)

_TESTS_PASSED = 0
_TESTS_FAILED = 0
_ALL_RESULTS: list[dict] = []


def test(name: str) -> Any:
    """Decorator: run a test function and track results."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            global _TESTS_PASSED, _TESTS_FAILED
            try:
                result = fn(*args, **kwargs)
                _TESTS_PASSED += 1
                _ALL_RESULTS.append({"test": name, "status": "PASS"})
                print(f"  ✅ {name}")
                return result
            except Exception as e:
                _TESTS_FAILED += 1
                _ALL_RESULTS.append({"test": name, "status": "FAIL", "error": str(e)})
                print(f"  ❌ {name}: {e}")
                return None
        return wrapper
    return decorator


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_db() -> OpsDbAdapter:
    db = OpsDbAdapter()
    db.connect()
    return db


_RUN_ID = f"test-{int(time.time())}"
_REPO = "hermes-ops"
_SHA = "b4a92d86f10ab457e8107ade831d9be7123a83fc"


def _task_dict(task_id: str, deps: list[str] | None = None,
               risk: str = "medium") -> dict:
    return {
        "task_id": task_id,
        "title": f"Test task {task_id}",
        "objective": f"Implement {task_id}",
        "scope": f"Scope for {task_id}",
        "write_scope": [f"src/{task_id}/"],
        "non_goals": "Do not modify unrelated code.",
        "dependencies": deps or [],
        "risk": risk,
        "acceptance_criteria": [f"Finding {task_id} addressed", "All tests pass"],
        "evidence_refs": ["test/evidence.md"],
        "finding_refs": [f"F-{task_id}"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


@test("connection: can connect and ping")
def test_connection() -> None:
    db = _make_db()
    try:
        cur = db.conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
    finally:
        db.close()


@test("state transitions: all valid transitions are symmetric")
def test_state_transitions_symmetric() -> None:
    """Check that schema.ts QUEUE_TRANSITIONS matches our Python version."""
    expected: dict[str, set[str]] = {
        "planning": {"queued", "cancelled", "failed"},
        "queued": {"pending", "blocked", "cancelled"},
        "pending": {"running", "blocked", "cancelled"},
        "running": {"dispatched", "verifying", "completed", "failed", "pending", "cancelled"},
        "dispatched": {"verifying", "running", "failed", "cancelled"},
        "verifying": {"completed", "failed", "running", "cancelled"},
        "completed": set(),
        "failed": {"pending", "queued", "cancelled"},
        "cancelled": set(),
        "blocked": {"queued", "pending", "cancelled"},
    }
    for state, allowed in QUEUE_TRANSITIONS.items():
        assert state in expected, f"Unexpected state: {state}"
        assert allowed == expected[state], f"Mismatch for {state}: {allowed} != {expected[state]}"

    # Check terminal states
    assert "completed" in TERMINAL_STATUSES
    assert "failed" in TERMINAL_STATUSES
    assert "cancelled" in TERMINAL_STATUSES


@test("create and get task by id")
def test_create_and_get() -> None:
    db = _make_db()
    try:
        task = OpsTask(
            external_id=make_external_id(_RUN_ID, "test-create-1"),
            repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_PLANNING, review_run_id=_RUN_ID,
            dag_payload={"test": True},
        )
        tid = db.create_task(task)
        assert isinstance(tid, int) and tid > 0

        fetched = db.get_task(tid)
        assert fetched is not None
        assert fetched.status == STATUS_PLANNING
        assert fetched.review_run_id == _RUN_ID
        assert fetched.dag_payload.get("test") is True
    finally:
        db.close()


@test("create task with duplicate external_id is idempotent")
def test_idempotent_create() -> None:
    db = _make_db()
    try:
        eid = make_external_id(_RUN_ID, "test-idempotent")
        task1 = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_PLANNING, review_run_id=_RUN_ID,
            dag_payload={"version": 1},
        )
        tid1 = db.create_task(task1)

        task2 = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_PLANNING, review_run_id=_RUN_ID,
            dag_payload={"version": 2},  # updated payload
        )
        tid2 = db.create_task(task2)
        assert tid1 == tid2, "Same external_id should return same row"

        fetched = db.get_task(tid1)
        assert fetched is not None
        # Payload should be updated on conflict
        assert fetched.dag_payload.get("version") == 2
    finally:
        db.close()


@test("bulk create tasks")
def test_bulk_create() -> None:
    db = _make_db()
    try:
        tasks = [
            OpsTask(
                external_id=make_external_id(_RUN_ID, f"bulk-{i}"),
                repository_owner=_REPO, repository_name=_REPO,
                head_sha=_SHA, policy_version="0.1.0",
                status=STATUS_PLANNING, review_run_id=_RUN_ID,
                dag_payload={"idx": i},
            )
            for i in range(3)
        ]
        ids = db.bulk_create_tasks(tasks)
        assert len(ids) == 3
        assert all(isinstance(i, int) and i > 0 for i in ids)

        run_tasks = db.get_tasks_by_run(_RUN_ID)
        assert len(run_tasks) >= 3
    finally:
        db.close()


@test("transition: planning → queued → pending → running → completed")
def test_full_transition_chain() -> None:
    db = _make_db()
    try:
        eid = make_external_id(_RUN_ID, "test-chain")
        task = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_PLANNING, review_run_id=_RUN_ID,
        )
        tid = db.create_task(task)

        # planning → queued
        t = db.transition_task(tid, STATUS_QUEUED)
        assert t is not None and t.status == STATUS_QUEUED

        # queued → pending
        t = db.transition_task(tid, STATUS_PENDING)
        assert t is not None and t.status == STATUS_PENDING

        # pending → running
        t = db.transition_task(tid, STATUS_RUNNING)
        assert t is not None and t.status == STATUS_RUNNING
        assert t.locked_by is not None

        # running → dispatched
        t = db.transition_task(tid, STATUS_DISPATCHED)
        assert t is not None and t.status == STATUS_DISPATCHED

        # dispatched → verifying
        t = db.transition_task(tid, STATUS_VERIFYING)
        assert t is not None and t.status == STATUS_VERIFYING

        # verifying → completed
        t = db.transition_task(tid, STATUS_COMPLETED)
        assert t is not None and t.status == STATUS_COMPLETED
        assert t.is_terminal
    finally:
        db.close()


@test("invalid transition raises ValueError")
def test_invalid_transition() -> None:
    db = _make_db()
    try:
        eid = make_external_id(_RUN_ID, "test-invalid")
        task = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_COMPLETED,  # start terminal
            review_run_id=_RUN_ID,
        )
        tid = db.create_task(task)

        # completed → running is illegal
        try:
            db.transition_task(tid, STATUS_RUNNING)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass  # expected
    finally:
        db.close()


@test("claim task via SKIP LOCKED")
def test_claim_task() -> None:
    db = _make_db()
    try:
        eid = make_external_id(_RUN_ID, "test-claim")
        task = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_QUEUED, review_run_id=_RUN_ID,
        )
        tid = db.create_task(task)
        assert tid is not None

        # Transition to pending so it's claimable
        db.transition_task(tid, STATUS_PENDING)

        # Claim it
        claimed = db.claim_task("test-worker-1")
        assert claimed is not None
        assert claimed.status == STATUS_RUNNING
        assert claimed.locked_by == "test-worker-1"

        # Second claim should return None (task already claimed)
        should_be_none = db.claim_task("test-worker-2")
        if should_be_none is not None:
            # Could be another queued task; verify NOT the same one
            assert should_be_none.id != claimed.id
    finally:
        db.close()


@test("blocked → queued → pending → claim")
def test_blocked_flow() -> None:
    db = _make_db()
    try:
        eid = make_external_id(_RUN_ID, "test-blocked")
        task = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_BLOCKED, review_run_id=_RUN_ID,
            dag_payload={"dependencies": ["other-task"]},
        )
        tid = db.create_task(task)

        # blocked → queued (dependency satisfied)
        t = db.unblock_task(tid)
        assert t is not None and t.status == STATUS_QUEUED

        # queued → pending
        db.transition_task(tid, STATUS_PENDING)

        # pending → claim
        claimed = db.claim_task("test-worker-blocked")
        assert claimed is not None
        assert claimed.status == STATUS_RUNNING
    finally:
        db.close()


@test("fail and retry: running → failed → pending")
def test_fail_retry() -> None:
    db = _make_db()
    try:
        eid = make_external_id(_RUN_ID, "test-fail-retry")
        task = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_RUNNING, review_run_id=_RUN_ID,
        )
        tid = db.create_task(task)

        # running → failed
        t = db.fail_task(tid, "Something broke")
        assert t is not None and t.status == STATUS_FAILED
        assert "broke" in (t.last_error or "")

        # failed → pending (retry)
        t = db.transition_task(tid, STATUS_PENDING)
        assert t is not None and t.status == STATUS_PENDING
    finally:
        db.close()


@test("stale lock recovery")
def test_stale_lock_recovery() -> None:
    db = _make_db()
    try:
        # Create a task, set it to running with a very old lock
        eid = make_external_id(_RUN_ID, "test-stale")
        task = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_RUNNING, review_run_id=_RUN_ID,
            locked_by="crashed-worker",
        )
        tid = db.create_task(task)

        # Simulate ancient lock via direct SQL
        cur = db.conn.cursor()
        cur.execute(
            "UPDATE tasks SET locked_at = '2020-01-01T00:00:00Z' WHERE id = %s",
            (tid,),
        )
        cur.close()

        # Recover stale locks (100 year cutoff = anything before 2024)
        count = db.recover_stale_locks(stale_after_ms=200_000_000_000)
        assert count >= 1, f"Expected at least 1 recovered, got {count}"

        # Verify re-queued
        recovered = db.get_task(tid)
        assert recovered is not None
        assert recovered.status == STATUS_PENDING
        assert "stale lock recovered" in (recovered.last_error or "")
    finally:
        db.close()


@test("dispatch → complete cycle with audit events")
def test_dispatch_complete_cycle() -> None:
    db = _make_db()
    try:
        eid = make_external_id(_RUN_ID, "test-dispatch-cycle")
        task = OpsTask(
            external_id=eid, repository_owner=_REPO, repository_name=_REPO,
            head_sha=_SHA, policy_version="0.1.0",
            status=STATUS_PLANNING, review_run_id=_RUN_ID,
            dag_payload={"task_id": "PROJ-001", "risk": "critical"},
        )
        tid = db.create_task(task)

        # planning → queued → pending → running
        db.transition_task(tid, STATUS_QUEUED)
        db.transition_task(tid, STATUS_PENDING)
        db.transition_task(tid, STATUS_RUNNING, worker_id="devin-worker-1")

        # running → dispatched (Devin starts)
        db.transition_task(tid, STATUS_DISPATCHED)
        db.record_audit(AuditEvent(
            task_id=tid, actor="devin-worker-1",
            action="dispatched", detail={"model": "swe-1-7"},
        ))

        # dispatched → verifying (CI / code review / automated tests)
        db.transition_task(tid, STATUS_VERIFYING)
        db.complete_task(tid)

        # Final state check
        final = db.get_task(tid)
        assert final is not None
        assert final.status == STATUS_COMPLETED
        assert final.locked_by is None  # lock cleared on terminal
    finally:
        db.close()


@test("blocked dependency chain: multiple tasks")
def test_blocked_dependency_chain() -> None:
    """Simulate DAG with dependencies: PROJ-002 depends on PROJ-001."""
    db = _make_db()
    try:
        tasks_list = [
            OpsTask(
                external_id=make_external_id(_RUN_ID, "dep-A"),
                repository_owner=_REPO, repository_name=_REPO,
                head_sha=_SHA, policy_version="0.1.0",
                status=STATUS_QUEUED,  # no deps → queued
                review_run_id=_RUN_ID,
                dag_payload={"task_id": "PROJ-001", "risk": "low"},
            ),
            OpsTask(
                external_id=make_external_id(_RUN_ID, "dep-B"),
                repository_owner=_REPO, repository_name=_REPO,
                head_sha=_SHA, policy_version="0.1.0",
                status=STATUS_BLOCKED,  # has deps → blocked
                review_run_id=_RUN_ID,
                dag_payload={"task_id": "PROJ-002", "dependencies": ["PROJ-001"], "risk": "medium"},
            ),
        ]
        ids = db.bulk_create_tasks(tasks_list)
        assert len(ids) == 2

        # First task should be claimable — use direct transition
        db.transition_task(ids[0], STATUS_PENDING)
        db.transition_task(ids[0], STATUS_RUNNING, worker_id="worker-1")
        assert db.get_task(ids[0]).status == STATUS_RUNNING

        # Second task should still be BLOCKED — cannot run
        assert db.get_task(ids[1]).status == STATUS_BLOCKED

        # Complete first task (need to transition through running first)
        db.transition_task(ids[0], STATUS_PENDING)
        db.transition_task(ids[0], STATUS_RUNNING, worker_id="worker-1")
        db.complete_task(ids[0])
        db.unblock_task(ids[1])

        # Now second should be claimable
        db.transition_task(ids[1], STATUS_PENDING)
        db.transition_task(ids[1], STATUS_RUNNING, worker_id="worker-2")
        assert db.get_task(ids[1]).status == STATUS_RUNNING
    finally:
        db.close()


@test("evidence idempotency")
def test_evidence_idempotency() -> None:
    db = _make_db()
    try:
        manifest = {"ci": {"conclusion": "success"}, "test_count": 42, "run": _RUN_ID}
        identity = make_evidence_identity(manifest)

        # First insert
        eid1 = db.insert_evidence(identity, _REPO, _REPO, _SHA, manifest)
        assert eid1 is not None

        # Second insert with same identity → should return None (DO NOTHING)
        eid2 = db.insert_evidence(identity, _REPO, _REPO, _SHA, manifest)
        assert eid2 is None, "Duplicate evidence should return None"
    finally:
        db.close()


@test("get_tasks_by_run returns only tasks for a specific run")
def test_get_tasks_by_run() -> None:
    other_run = f"other-{int(time.time())}"
    db = _make_db()
    try:
        for run_id in (_RUN_ID, other_run):
            task = OpsTask(
                external_id=make_external_id(run_id, "by-run"),
                repository_owner=_REPO, repository_name=_REPO,
                head_sha=_SHA, policy_version="0.1.0",
                status=STATUS_QUEUED, review_run_id=run_id,
            )
            db.create_task(task)

        run_tasks = db.get_tasks_by_run(_RUN_ID)
        assert len(run_tasks) >= 1
        for t in run_tasks:
            assert t.review_run_id == _RUN_ID
    finally:
        db.close()


@test("make_external_id is deterministic")
def test_external_id_deterministic() -> None:
    a = make_external_id("run-1", "task-1")
    b = make_external_id("run-1", "task-1")
    assert a == b
    # Different inputs produce different hashes
    c = make_external_id("run-1", "task-2")
    assert a != c
    d = make_external_id("run-2", "task-1")
    assert a != d


@test("is_valid_transition enforces rules")
def test_is_valid_transition_rules() -> None:
    assert is_valid_transition("planning", "queued") is True
    assert is_valid_transition("completed", "running") is False
    assert is_valid_transition("failed", "pending") is True
    assert is_valid_transition("blocked", "queued") is True
    assert is_valid_transition("running", "completed") is True
    assert is_valid_transition("completed", "queued") is False


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 60)
    print(f"  Ops DB Integration Tests ({_RUN_ID})")
    print("=" * 60)
    print(f"  DATABASE_URL: {os.getenv('DATABASE_URL', 'NOT SET')}")
    print()

    # Collect all test functions
    test_fns = []
    for name, fn in inspect.getmembers(sys.modules[__name__]):
        if name.startswith("test_") and callable(fn):
            test_fns.append(fn)

    # Run
    for fn in test_fns:
        fn()

    # Summary
    print()
    print("=" * 60)
    passed = sum(1 for r in _ALL_RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in _ALL_RESULTS if r["status"] == "FAIL")
    print(f"  Results: {passed} PASSED, {failed} FAILED, {len(_ALL_RESULTS)} TOTAL")
    print("=" * 60)

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())