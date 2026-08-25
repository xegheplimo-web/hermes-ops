#!/usr/bin/env python3
"""
Integration tests for durable human approval in Ops DB.

Requires PostgreSQL with the 0007 approvals migration applied and
DATABASE_URL set.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add skill scripts to path
_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "scripts"))

from ops_adapter import (
    Approval, OpsDbAdapter, OpsTask,
    make_external_id,
)

_TESTS_PASSED = 0
_TESTS_FAILED = 0


def test(name: str) -> Any:
    """Decorator: run a test function and track results."""
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


def _make_db() -> OpsDbAdapter:
    db = OpsDbAdapter()
    db.connect()
    return db


def _make_task(db: OpsDbAdapter, run_id: str, task_id: str) -> int:
    external_id = make_external_id(run_id, task_id)
    task = OpsTask(
        external_id=external_id,
        repository_owner="acme",
        repository_name="hermes-ops",
        head_sha="b4a92d86f10ab457e8107ade831d9be7123a83fc",
        policy_version="0.1.0",
        status="planning",
        max_attempts=3,
        review_run_id=run_id,
    )
    return db.create_task(task)


@test("request approval creates pending record")
def test_request_approval() -> None:
    db = _make_db()
    try:
        run_id = f"approval-test-{int(datetime.now(timezone.utc).timestamp())}"
        task_id = _make_task(db, run_id, "request")
        approval = db.request_approval(
            task_id=task_id,
            approver="alice",
            reason="acknowledged critical risk",
            signature="sig-001",
        )
        assert isinstance(approval, Approval)
        assert approval.task_id == task_id
        assert approval.status == "pending"
        assert approval.approver == "alice"
        assert approval.id is not None
    finally:
        db.close()


@test("resolve approval to approved")
def test_resolve_approval() -> None:
    db = _make_db()
    try:
        run_id = f"approval-test-{int(datetime.now(timezone.utc).timestamp())}"
        task_id = _make_task(db, run_id, "resolve")
        approval = db.request_approval(task_id, "bob", "override", "sig-002")
        resolved = db.resolve_approval(approval.id, "approved")
        assert resolved is not None
        assert resolved.status == "approved"
        assert db.is_approved(task_id) is True
    finally:
        db.close()


@test("resolve approval to rejected")
def test_reject_approval() -> None:
    db = _make_db()
    try:
        run_id = f"approval-test-{int(datetime.now(timezone.utc).timestamp())}"
        task_id = _make_task(db, run_id, "reject")
        approval = db.request_approval(task_id, "carol", "deny", "sig-003")
        resolved = db.resolve_approval(approval.id, "rejected")
        assert resolved is not None
        assert resolved.status == "rejected"
        assert db.is_approved(task_id) is False
    finally:
        db.close()


@test("get approvals returns newest first")
def test_get_approvals_for_task() -> None:
    db = _make_db()
    try:
        run_id = f"approval-test-{int(datetime.now(timezone.utc).timestamp())}"
        task_id = _make_task(db, run_id, "list")
        first = db.request_approval(task_id, "alice", "first", "sig-004")
        second = db.request_approval(task_id, "bob", "second", "sig-005")
        approvals = db.get_approvals_for_task(task_id)
        assert len(approvals) == 2
        assert approvals[0].id == second.id
        assert approvals[1].id == first.id
    finally:
        db.close()


@test("is_approved false when only pending")
def test_is_approved_pending() -> None:
    db = _make_db()
    try:
        run_id = f"approval-test-{int(datetime.now(timezone.utc).timestamp())}"
        task_id = _make_task(db, run_id, "pending-only")
        db.request_approval(task_id, "alice", "pending", "sig-006")
        assert db.is_approved(task_id) is False
    finally:
        db.close()


def main() -> int:
    print("=" * 60)
    print("  Ops DB Durable Approval Tests")
    print("=" * 60)

    test_request_approval()
    test_resolve_approval()
    test_reject_approval()
    test_get_approvals_for_task()
    test_is_approved_pending()

    print("=" * 60)
    print(f"  Results: {_TESTS_PASSED} passed, {_TESTS_FAILED} failed")
    print("=" * 60)
    return 1 if _TESTS_FAILED > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
