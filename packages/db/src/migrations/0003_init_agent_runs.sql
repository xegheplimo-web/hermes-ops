-- Hermes Ops migration 0003: agent_runs (generic)
--
-- A single generic `agent_runs` table records every coding-agent session,
-- regardless of provider. Provider identity is a column (`provider`), not a
-- separate table — this keeps the schema stable as new adapters (Devin, local,
-- future) are added in later phases. There are NO provider-specific session
-- tables in Phase 1.
--
-- `external_run_id` is the provider's own run id (e.g. Devin run id); it is
-- optional and unique only within (provider, external_run_id) where present.

CREATE TABLE IF NOT EXISTS agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL CHECK (provider <> ''),
    external_run_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'timed_out')),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    result          JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT agent_runs_provider_external_unique
        UNIQUE (provider, external_run_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_job ON agent_runs (job_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs (status);
-- Partial index for external_run_id lookups, skipping NULLs.
CREATE INDEX IF NOT EXISTS idx_agent_runs_provider_external
    ON agent_runs (provider, external_run_id)
    WHERE external_run_id IS NOT NULL;
