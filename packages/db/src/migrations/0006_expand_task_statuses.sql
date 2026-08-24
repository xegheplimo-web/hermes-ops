-- Hermes Ops migration 0006: expanded task statuses for review/reconcile/dispatch pipeline
--
-- Expands the `tasks.status` CHECK constraint to support the full review-pipeline
-- lifecycle. Also adds `review_run_id` and `dag_payload` columns.

-- Step 1: Add columns (idempotent via IF NOT EXISTS)
ALTER TABLE IF EXISTS tasks
    ADD COLUMN IF NOT EXISTS review_run_id TEXT,
    ADD COLUMN IF NOT EXISTS dag_payload JSONB DEFAULT '{}'::jsonb;

-- Step 2: Replace the status CHECK constraint safely.
-- Drop old then add new (IF NOT EXISTS on constraint not supported, so drop first).
DO $$
BEGIN
    ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
    ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
        CHECK (status IN (
            'planning', 'queued', 'pending', 'running',
            'dispatched', 'verifying',
            'completed', 'failed', 'cancelled', 'blocked'
        ));
END $$;

-- Step 3: Update the claim index to cover 'queued' rows too.
DROP INDEX IF EXISTS idx_tasks_claim;
CREATE INDEX IF NOT EXISTS idx_tasks_claim
    ON tasks (available_at, id)
    WHERE status IN ('pending', 'queued');

-- Step 4: Add index for review_run_id lookups.
CREATE INDEX IF NOT EXISTS idx_tasks_review_run
    ON tasks (review_run_id)
    WHERE review_run_id IS NOT NULL;

-- Step 5: Update jobs status check.
DO $$
BEGIN
    ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
    ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
        CHECK (status IN (
            'pending', 'running', 'completed', 'failed', 'cancelled', 'blocked'
        ));
END $$;