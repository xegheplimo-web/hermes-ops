-- Hermes Ops migration 0002: jobs
--
-- A `job` is a unit of work belonging to a task (many jobs per task). Jobs are
-- the rows a worker actually claims and executes; a task is the user-facing
-- aggregate. Jobs carry the same queue fields as tasks so they can be claimed
-- independently via `FOR UPDATE SKIP LOCKED`.

CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL PRIMARY KEY,
    task_id         BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind <> ''),
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts    INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at       TIMESTAMPTZ,
    locked_by       TEXT,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs (available_at, id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_jobs_task ON jobs (task_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
