-- Hermes Ops migration 0001: tasks
--
-- The `tasks` table is the top-level unit of work in the control plane. Each
-- task is idempotent by `external_id` (caller-supplied), bound to a repository,
-- an optional PR number, a head SHA, and the policy version it targets. The
-- queue fields (status, attempts, available_at, locked_at, locked_by,
-- last_error) drive single-worker claiming via `FOR UPDATE SKIP LOCKED` (see
-- packages/db/src/queue.ts).
--
-- No provider-specific columns live here; provider sessions are recorded in the
-- generic `agent_runs` table (migration 0003).
--
-- pgcrypto is NOT enabled: identifiers are BIGSERIAL (8-byte, monotonic, no
-- collision risk) and evidence identities are SHA-256 computed in TypeScript
-- (see @hermes-ops/contracts). PostgreSQL >= 13 also exposes
-- `gen_random_uuid()` from core if UUIDs are ever needed later.

CREATE TABLE IF NOT EXISTS tasks (
    id              BIGSERIAL PRIMARY KEY,
    external_id     TEXT NOT NULL,
    repository_owner TEXT NOT NULL CHECK (repository_owner <> ''),
    repository_name TEXT NOT NULL CHECK (repository_name <> ''),
    pr_number       INTEGER CHECK (pr_number IS NULL OR pr_number > 0),
    head_sha        CHAR(40) NOT NULL CHECK (head_sha ~ '^[0-9a-f]{40}$'),
    policy_version  TEXT NOT NULL CHECK (policy_version <> ''),
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts    INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at       TIMESTAMPTZ,
    locked_by       TEXT,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tasks_external_id_unique UNIQUE (external_id)
);

-- Claim index: only pending rows, ordered by availability for FIFO claiming.
CREATE INDEX IF NOT EXISTS idx_tasks_claim
    ON tasks (available_at, id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_repo_sha
    ON tasks (repository_owner, repository_name, head_sha);
