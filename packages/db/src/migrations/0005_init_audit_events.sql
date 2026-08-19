-- Hermes Ops migration 0005: audit_events
--
-- Append-only audit log. Every state transition or significant action on a
-- task/job emits a row. `actor` is the worker id or system component that
-- performed the action; `action` is a stable string (e.g. 'task.claimed',
-- 'job.failed', 'evidence.ingested'); `detail` is a JSONB bag for structured
-- context. Both task_id and job_id are nullable so audit events can describe
-- task-level, job-level, or system-level actions.

CREATE TABLE IF NOT EXISTS audit_events (
    id              BIGSERIAL PRIMARY KEY,
    task_id         BIGINT REFERENCES tasks(id) ON DELETE CASCADE,
    job_id          BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
    actor           TEXT NOT NULL CHECK (actor <> ''),
    action          TEXT NOT NULL CHECK (action <> ''),
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_events (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_events (job_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events (action, created_at);
