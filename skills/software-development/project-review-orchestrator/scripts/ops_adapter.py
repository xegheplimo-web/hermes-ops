#!/usr/bin/env python3
"""
Ops DB adapter for the Hermes project-review-orchestrator pipeline.

Connects to PostgreSQL (via DATABASE_URL or explicit params) and provides
authoritative CRUD for tasks, jobs, agent_runs, evidence, and audit_events.

This is the SINGLE AUTHORITATIVE PATH for task state management.
task-plan.json is only an artifact/fallback — Ops DB is runtime truth.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class OpsTask:
    """Mirrors the `tasks` table row, expanded for the review pipeline."""
    id: int | None = None
    external_id: str = ""
    repository_owner: str = ""
    repository_name: str = ""
    pr_number: int | None = None
    head_sha: str = ""
    policy_version: str = "0.1.0"
    payload: dict = field(default_factory=dict)
    status: str = "planning"
    attempts: int = 0
    max_attempts: int = 5
    available_at: datetime | None = None
    locked_at: datetime | None = None
    locked_by: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    review_run_id: str | None = None
    dag_payload: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")


@dataclass
class AuditEvent:
    task_id: int | None = None
    job_id: int | None = None
    actor: str = "hermes"
    action: str = ""
    detail: dict = field(default_factory=dict)


# ── Status constants ─────────────────────────────────────────────────────────

# Task lifecycle states (see migration 0006)
STATUS_PLANNING = "planning"
STATUS_QUEUED = "queued"
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DISPATCHED = "dispatched"
STATUS_VERIFYING = "verifying"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_BLOCKED = "blocked"

# Terminal states
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})

# Allowed transitions (mirrors schema.ts QUEUE_TRANSITIONS)
QUEUE_TRANSITIONS: dict[str, set[str]] = {
    STATUS_PLANNING: {STATUS_QUEUED, STATUS_CANCELLED, STATUS_FAILED},
    STATUS_QUEUED: {STATUS_PENDING, STATUS_BLOCKED, STATUS_CANCELLED},
    STATUS_PENDING: {STATUS_RUNNING, STATUS_BLOCKED, STATUS_CANCELLED},
    STATUS_RUNNING: {STATUS_DISPATCHED, STATUS_VERIFYING, STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING, STATUS_CANCELLED},
    STATUS_DISPATCHED: {STATUS_VERIFYING, STATUS_RUNNING, STATUS_FAILED, STATUS_CANCELLED},
    STATUS_VERIFYING: {STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING, STATUS_CANCELLED},
    STATUS_COMPLETED: set(),
    STATUS_FAILED: {STATUS_PENDING, STATUS_QUEUED, STATUS_CANCELLED},
    STATUS_CANCELLED: set(),
    STATUS_BLOCKED: {STATUS_QUEUED, STATUS_PENDING, STATUS_CANCELLED},
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Check if a state transition is allowed."""
    allowed = QUEUE_TRANSITIONS.get(from_status, set())
    return to_status in allowed


# ── Idempotency helpers ──────────────────────────────────────────────────────

def make_external_id(run_id: str, task_id_str: str) -> str:
    """Create a deterministic external_id from run_id + task_id."""
    raw = f"{run_id}::{task_id_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def make_evidence_identity(manifest: dict) -> str:
    """SHA-256 of canonical evidence manifest."""
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── PostgreSQL adapter (psycopg2) ────────────────────────────────────────────


class OpsDbAdapter:
    """PostgreSQL adapter for the Hermes Ops control plane.

    Uses psycopg2 directly. All methods raise on DB errors — the caller
    should handle exceptions and report appropriately.
    """

    def __init__(self, connection_string: str | None = None):
        import psycopg2  # noqa: F811 — imported lazily
        self._conn_str = connection_string or os.getenv("DATABASE_URL", "")
        if not self._conn_str:
            raise ValueError(
                "DATABASE_URL not set and no connection_string provided. "
                "Set DATABASE_URL=postgres://user:pass@host:5432/dbname"
            )
        self._conn: Any = None
        self._conn_args = {}

    # ── Connection management ───────────────────────────────────────────

    def connect(self) -> None:
        import psycopg2
        self._conn = psycopg2.connect(self._conn_str)
        self._conn.autocommit = True

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _ensure_connected(self) -> None:
        if self._conn is None or self._conn.closed:
            self.connect()

    @property
    def conn(self):
        self._ensure_connected()
        return self._conn

    # ── Transactions ────────────────────────────────────────────────────

    def transaction(self):
        """Return a context manager wrapping a single transaction."""
        self._ensure_connected()
        return self.conn

    # ── Tasks CRUD ──────────────────────────────────────────────────────

    def create_task(self, task: OpsTask) -> int:
        """Insert a task and return its id. Idempotent via external_id UNIQUE."""
        cur = self.conn.cursor()
        try:
            cur.execute("""
                INSERT INTO tasks
                    (external_id, repository_owner, repository_name, head_sha,
                     policy_version, payload, status, max_attempts,
                     available_at, review_run_id, dag_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (external_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    dag_payload = EXCLUDED.dag_payload,
                    status = CASE WHEN tasks.status IN ('failed', 'cancelled')
                                  THEN EXCLUDED.status ELSE tasks.status END,
                    updated_at = now()
                RETURNING id
            """, (
                task.external_id,
                task.repository_owner,
                task.repository_name,
                task.head_sha,
                task.policy_version,
                json.dumps(task.payload),
                task.status,
                task.max_attempts,
                task.available_at or datetime.now(timezone.utc),
                task.review_run_id,
                json.dumps(task.dag_payload),
            ))
            row = cur.fetchone()
            return row[0]
        finally:
            cur.close()

    def bulk_create_tasks(self, tasks: list[OpsTask]) -> list[int]:
        """Insert multiple tasks in one transaction. Returns list of ids."""
        ids: list[int] = []
        with self.conn:
            cur = self.conn.cursor()
            try:
                for task in tasks:
                    cur.execute("""
                        INSERT INTO tasks
                            (external_id, repository_owner, repository_name, head_sha,
                             policy_version, payload, status, max_attempts,
                             available_at, review_run_id, dag_payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (external_id) DO UPDATE SET
                            payload = EXCLUDED.payload,
                            dag_payload = EXCLUDED.dag_payload,
                            status = CASE WHEN tasks.status IN ('failed', 'cancelled')
                                          THEN EXCLUDED.status ELSE tasks.status END,
                            updated_at = now()
                        RETURNING id
                    """, (
                        task.external_id,
                        task.repository_owner,
                        task.repository_name,
                        task.head_sha,
                        task.policy_version,
                        json.dumps(task.payload),
                        task.status,
                        task.max_attempts,
                        task.available_at or datetime.now(timezone.utc),
                        task.review_run_id,
                        json.dumps(task.dag_payload),
                    ))
                    row = cur.fetchone()
                    ids.append(row[0])
            finally:
                cur.close()
        return ids

    def get_task(self, task_id: int) -> OpsTask | None:
        """Fetch a task by id."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_task(row, cur)
        finally:
            cur.close()

    def get_task_by_external_id(self, external_id: str) -> OpsTask | None:
        """Fetch a task by external_id."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM tasks WHERE external_id = %s", (external_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_task(row, cur)
        finally:
            cur.close()

    def get_tasks_by_run(self, review_run_id: str) -> list[OpsTask]:
        """Fetch all tasks for a review run."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT * FROM tasks WHERE review_run_id = %s ORDER BY id",
                (review_run_id,),
            )
            return self._rows_to_tasks(cur)
        finally:
            cur.close()

    def claim_task(self, worker_id: str, max_attempts: int = 5) -> OpsTask | None:
        """Claim one pending/queued task via SKIP LOCKED.

        Returns the claimed task or None if nothing available.
        """
        cur = self.conn.cursor()
        try:
            cur.execute("""
                UPDATE tasks
                SET status = 'running',
                    attempts = attempts + 1,
                    locked_at = now(),
                    locked_by = %s,
                    updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM tasks
                    WHERE status IN ('pending', 'queued')
                      AND available_at <= now()
                    ORDER BY available_at ASC, id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING *
            """, (worker_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_task(row, cur)
        finally:
            cur.close()

    def transition_task(
        self, task_id: int, new_status: str,
        error: str | None = None, worker_id: str | None = None,
    ) -> OpsTask | None:
        """Transition a task to a new status. Validates the transition.

        Returns the updated task or None if not found.
        """
        # Fetch current state first
        task = self.get_task(task_id)
        if not task:
            return None
        if not is_valid_transition(task.status, new_status):
            raise ValueError(
                f"Invalid transition: {task.status} → {new_status} "
                f"(task_id={task_id})"
            )

        cur = self.conn.cursor()
        try:
            # Build SET clause based on target state
            set_parts = ["status = %s", "updated_at = now()"]
            params: list[Any] = [new_status]

            if new_status in ("pending", "queued") and task.status == "failed":
                # Re-queue: clear lock, bump available_at
                set_parts.append("locked_at = NULL")
                set_parts.append("locked_by = NULL")
                set_parts.append("available_at = now()")

            if new_status == "running" and task.status == "pending":
                # Claim: set lock fields
                set_parts.append("locked_at = now()")
                set_parts.append("locked_by = %s")
                params.append(worker_id or "hermes")
                set_parts.append("attempts = attempts + 1")

            if new_status in ("completed", "failed", "cancelled", "dispatched", "verifying"):
                # Terminal or mid-flight: clear locks
                set_parts.append("locked_at = NULL")
                set_parts.append("locked_by = NULL")

            if error is not None:
                set_parts.append("last_error = %s")
                params.append(error)

            params.append(task_id)
            sql = f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = %s RETURNING *"

            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                return None

            # Record audit event
            self._record_audit(AuditEvent(
                task_id=task_id,
                actor=worker_id or "hermes",
                action=f"transition:{task.status}→{new_status}",
                detail={"error": error} if error else {},
            ))

            return self._row_to_task(row, cur)
        finally:
            cur.close()

    def mark_blocked(self, task_id: int, reason: str) -> OpsTask | None:
        """Mark a task as blocked by an unmet dependency."""
        return self.transition_task(task_id, STATUS_BLOCKED, error=reason)

    def unblock_task(self, task_id: int) -> OpsTask | None:
        """Return a blocked task to queued state."""
        return self.transition_task(task_id, STATUS_QUEUED)

    def complete_task(self, task_id: int) -> OpsTask | None:
        """Mark a task as completed."""
        return self.transition_task(task_id, STATUS_COMPLETED)

    def fail_task(self, task_id: int, error: str) -> OpsTask | None:
        """Mark a task as failed."""
        return self.transition_task(task_id, STATUS_FAILED, error=error)

    # ── Stale lock recovery ─────────────────────────────────────────────

    def recover_stale_locks(self, stale_after_ms: int = 300_000) -> int:
        """Recover tasks stuck in 'running' with old locks. Returns count."""
        cur = self.conn.cursor()
        try:
            cutoff = datetime.now(timezone.utc).timestamp() * 1000 - stale_after_ms
            cur.execute("""
                UPDATE tasks
                SET status = 'pending',
                    available_at = now(),
                    locked_at = NULL,
                    locked_by = NULL,
                    last_error = COALESCE(last_error || E'\\n', '') || 'stale lock recovered',
                    updated_at = now()
                WHERE status = 'running'
                  AND locked_at IS NOT NULL
                  AND locked_at < to_timestamp(%s / 1000.0)
                RETURNING id
            """, (cutoff,))
            return cur.rowcount or 0
        finally:
            cur.close()

    # ── Audit events ────────────────────────────────────────────────────

    def _record_audit(self, event: AuditEvent) -> int | None:
        """Record an audit event. Returns id or None on failure."""
        cur = self.conn.cursor()
        try:
            cur.execute("""
                INSERT INTO audit_events (task_id, job_id, actor, action, detail)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (
                event.task_id, event.job_id, event.actor,
                event.action, json.dumps(event.detail),
            ))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None  # Audit failures should not break the pipeline
        finally:
            cur.close()

    def record_audit(self, event: AuditEvent) -> int | None:
        """Public audit method."""
        return self._record_audit(event)

    # ── Evidence ────────────────────────────────────────────────────────

    def insert_evidence(
        self,
        evidence_identity: str,
        repository_owner: str,
        repository_name: str,
        head_sha: str,
        manifest: dict,
        policy_version: str = "0.1.0",
        task_id: int | None = None,
    ) -> int | None:
        """Insert evidence. Idempotent via evidence_identity UNIQUE."""
        cur = self.conn.cursor()
        try:
            cur.execute("""
                INSERT INTO evidence
                    (evidence_identity, repository_owner, repository_name,
                     head_sha, policy_version, manifest, task_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (evidence_identity) DO NOTHING
                RETURNING id
            """, (
                evidence_identity, repository_owner, repository_name,
                head_sha, policy_version, json.dumps(manifest), task_id,
            ))
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            cur.close()

    # ── Query helpers ───────────────────────────────────────────────────

    def _row_to_task(self, row: tuple, cur: Any) -> OpsTask:
        """Convert a DB row to an OpsTask."""
        # Get column names from cursor description
        cols = [desc[0] for desc in cur.description]
        data = dict(zip(cols, row))
        return OpsTask(
            id=data["id"],
            external_id=data["external_id"],
            repository_owner=data["repository_owner"],
            repository_name=data["repository_name"],
            pr_number=data.get("pr_number"),
            head_sha=data["head_sha"],
            policy_version=data["policy_version"],
            payload=data.get("payload") or {},
            status=data["status"],
            attempts=data["attempts"],
            max_attempts=data["max_attempts"],
            available_at=data.get("available_at"),
            locked_at=data.get("locked_at"),
            locked_by=data.get("locked_by"),
            last_error=data.get("last_error"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            review_run_id=data.get("review_run_id"),
            dag_payload=data.get("dag_payload") or {},
        )

    def _rows_to_tasks(self, cur: Any) -> list[OpsTask]:
        """Convert multiple rows to OpsTask list."""
        tasks: list[OpsTask] = []
        for row in cur.fetchall():
            tasks.append(self._row_to_task(row, cur))
        return tasks