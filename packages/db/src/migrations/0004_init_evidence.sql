-- Hermes Ops migration 0004: evidence
--
-- `evidence` stores validated EvidenceManifest v1 rows (see
-- @hermes-ops/contracts). Each row is bound to repository identity, an optional
-- PR number, the head SHA the evidence was gathered against, and the policy
-- version it targets — the same binding the policy evaluator enforces.
--
-- `evidence_identity` is the deterministic SHA-256 computed in TypeScript over
-- the canonical manifest. It is globally unique and is the deduplication key
-- for idempotent evidence ingestion.
--
-- Idempotency: when a manifest carries an `idempotency_key`, the
-- (repository_owner, repository_name, head_sha, idempotency_key) tuple is
-- unique, so re-ingesting the same evidence for the same head SHA is a no-op.
-- Re-using an idempotency key for a DIFFERENT head SHA is allowed (the head SHA
-- is part of the key) but discouraged; callers should generate fresh keys per
-- evidence production.

CREATE TABLE IF NOT EXISTS evidence (
    id              BIGSERIAL PRIMARY KEY,
    task_id         BIGINT REFERENCES tasks(id) ON DELETE CASCADE,
    repository_owner TEXT NOT NULL CHECK (repository_owner <> ''),
    repository_name TEXT NOT NULL CHECK (repository_name <> ''),
    pr_number       INTEGER CHECK (pr_number IS NULL OR pr_number > 0),
    head_sha        CHAR(40) NOT NULL CHECK (head_sha ~ '^[0-9a-f]{40}$'),
    policy_version  TEXT NOT NULL CHECK (policy_version <> ''),
    evidence_identity CHAR(64) NOT NULL CHECK (evidence_identity ~ '^[0-9a-f]{64}$'),
    manifest        JSONB NOT NULL,
    idempotency_key TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT evidence_identity_unique UNIQUE (evidence_identity),
    CONSTRAINT evidence_repo_sha_idem_unique
        UNIQUE (repository_owner, repository_name, head_sha, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_evidence_repo_sha
    ON evidence (repository_owner, repository_name, head_sha);
CREATE INDEX IF NOT EXISTS idx_evidence_policy ON evidence (policy_version);
CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence (task_id);
