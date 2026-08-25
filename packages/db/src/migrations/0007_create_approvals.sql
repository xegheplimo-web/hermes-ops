-- Hermes Ops migration 0007: durable human approvals
--
-- Stores explicit human sign-off for CRITICAL-risk tasks.
-- Each approval is bound to a single task and referenced by a stable
-- signature string. The token itself is not trusted cryptographically in
-- phase 0; the act of persisting it durably is what allows a gate to pass.

CREATE TABLE IF NOT EXISTS approvals (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL,
  signed_at TIMESTAMPTZ NOT NULL,
  approver TEXT NOT NULL,
  reason TEXT NOT NULL,
  signature TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT approvals_task_id_fk
    FOREIGN KEY (task_id) REFERENCES tasks(id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS approvals_task_id_idx ON approvals (task_id);
