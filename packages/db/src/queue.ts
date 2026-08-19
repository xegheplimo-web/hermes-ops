/**
 * Queue primitives for the Hermes Ops control plane.
 *
 * This module is deliberately driver-agnostic. It exports:
 *   - SQL constants (with positional `$1` parameters) for claiming and
 *     recovering queue rows.
 *   - Pure, deterministic helpers for retry/backoff and stale-lock recovery.
 *
 * No DB driver is imported. A caller wires these into `pg` / `postgres` /
 * `pg-boss` by passing the SQL string and a parameter array. Tests do not
 * require a live database.
 *
 * Design notes:
 *   - Claiming uses `FOR UPDATE SKIP LOCKED` so multiple workers can poll
 *     concurrently without contention; each worker claims exactly one row.
 *   - All SQL uses positional parameters (`$1`, `$2`, ...). Worker ids, cutoff
 *     timestamps, and intervals are NEVER string-interpolated into SQL — this
 *     prevents SQL injection and plan-cache poisoning.
 *   - Retry/backoff is exponential with a deterministic, seedable jitter so
 *     tests are reproducible.
 *   - Stale-lock recovery re-queues rows whose `locked_at` is older than a
 *     caller-computed cutoff, making recovery bounded and observable.
 */

import type { QueueStatus } from './schema.js';

/* -------------------------------------------------------------------------- */
/* Claim SQL                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * Claim one `tasks` row for a single worker.
 *
 * The query:
 *   1. Selects the oldest available pending row, skipping rows already locked
 *      by other workers (`FOR UPDATE SKIP LOCKED`, `LIMIT 1`).
 *   2. Atomically transitions it to `running`, increments `attempts`, sets
 *      `locked_at = now()`, `locked_by = $1`, and clears `available_at`.
 *   3. Returns the claimed row.
 *
 * Parameters:
 *   - `$1`: worker id (string). NEVER interpolated — always passed as a
 *     bound parameter by the caller.
 *
 * Returns zero or one row. Callers should run this inside a transaction with
 * `READ COMMITTED` (the default) isolation.
 */
export const CLAIM_TASK_SQL = /* sql */ `
UPDATE tasks
SET status       = 'running',
    attempts     = attempts + 1,
    locked_at    = now(),
    locked_by    = $1,
    available_at = NULL,
    updated_at   = now()
WHERE id = (
    SELECT id
    FROM tasks
    WHERE status = 'pending'
      AND available_at <= now()
    ORDER BY available_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *
`.trim();

/**
 * Claim one `jobs` row for a single worker. Same shape as `CLAIM_TASK_SQL`.
 *
 * Parameters:
 *   - `$1`: worker id (string).
 */
export const CLAIM_JOB_SQL = /* sql */ `
UPDATE jobs
SET status       = 'running',
    attempts     = attempts + 1,
    locked_at    = now(),
    locked_by    = $1,
    available_at = NULL,
    updated_at   = now()
WHERE id = (
    SELECT id
    FROM jobs
    WHERE status = 'pending'
      AND available_at <= now()
    ORDER BY available_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *
`.trim();

/**
 * Build the parameter array to pass alongside `CLAIM_TASK_SQL` /
 * `CLAIM_JOB_SQL`. Kept as a helper so callers do not accidentally swap the
 * worker id into the wrong slot.
 *
 * @param workerId Stable, non-empty worker identifier (e.g. hostname + pid).
 */
export const claimParams = (workerId: string): readonly [string] => {
  if (typeof workerId !== 'string' || workerId.length === 0) {
    throw new TypeError('workerId must be a non-empty string');
  }
  return [workerId] as const;
};

/* -------------------------------------------------------------------------- */
/* Retry / backoff                                                            */
/* -------------------------------------------------------------------------- */

/** Default base backoff in milliseconds. */
export const DEFAULT_BACKOFF_BASE_MS = 1000;
/** Default backoff cap in milliseconds. */
export const DEFAULT_BACKOFF_MAX_MS = 5 * 60 * 1000;
/** Default maximum attempts (1 initial + 4 retries). */
export const DEFAULT_MAX_ATTEMPTS = 5;

export interface BackoffOptions {
  /** Base delay in ms. Defaults to 1000. Must be > 0. */
  readonly baseMs?: number;
  /** Cap on the delay in ms. Defaults to 300000 (5m). Must be >= baseMs. */
  readonly maxMs?: number;
  /**
   * Optional deterministic jitter seed. When provided, jitter is a stable
   * 0..1 multiplier derived from (seed, attempt). When omitted, no jitter is
   * applied (pure exponential, capped).
   */
  readonly jitterSeed?: number;
}

/**
 * Compute the backoff delay in milliseconds for a given attempt number.
 *
 * `attempt` is 1-based: the delay returned is the wait BEFORE retrying after
 * the Nth failed attempt. The delay is exponential: `baseMs * 2^(attempt-1)`,
 * capped at `maxMs`. With `jitterSeed`, a deterministic full-jitter multiplier
 * in [0, 1) is applied so retries do not thunder-herd while remaining
 * reproducible in tests.
 *
 * Bounds:
 *   - `attempt` must be >= 1.
 *   - `baseMs` must be > 0.
 *   - `maxMs` must be >= `baseMs`.
 *   - Result is always in [0, maxMs].
 */
export const computeBackoffMs = (
  attempt: number,
  options: BackoffOptions = {},
): number => {
  if (!Number.isInteger(attempt) || attempt < 1) {
    throw new TypeError('attempt must be a positive integer');
  }
  const baseMs = options.baseMs ?? DEFAULT_BACKOFF_BASE_MS;
  const maxMs = options.maxMs ?? DEFAULT_BACKOFF_MAX_MS;
  if (!(baseMs > 0)) throw new TypeError('baseMs must be > 0');
  if (!(maxMs >= baseMs)) throw new TypeError('maxMs must be >= baseMs');

  const exponential = baseMs * 2 ** (attempt - 1);
  const capped = Math.min(exponential, maxMs);

  if (options.jitterSeed === undefined) return capped;

  // Deterministic 0..1 hash from (jitterSeed, attempt). Uses a small integer
  // hash so it is reproducible across runtimes and does not require crypto.
  const seed = Math.trunc(options.jitterSeed);
  const h = hash32(seed, attempt);
  const jitter = (h >>> 0) / 0x100000000;
  return Math.floor(capped * jitter);
};

/**
 * Compute the next `available_at` timestamp for a row that failed its Nth
 * attempt and should be retried. Pure and deterministic given `now`.
 *
 * Returns `null` if the row should not be retried (attempt >= maxAttempts).
 */
export const computeNextAvailableAt = (
  attempt: number,
  now: Date,
  options: BackoffOptions & { readonly maxAttempts?: number } = {},
): Date | null => {
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    throw new TypeError('maxAttempts must be a positive integer');
  }
  if (attempt >= maxAttempts) return null;
  const delay = computeBackoffMs(attempt, options);
  return new Date(now.getTime() + delay);
};

/**
 * Decide whether a row should be retried after `attempt` failed attempts.
 * Bounded by `maxAttempts`.
 */
export const shouldRetry = (
  attempt: number,
  maxAttempts: number = DEFAULT_MAX_ATTEMPTS,
): boolean => {
  if (!Number.isInteger(attempt) || attempt < 0) return false;
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) return false;
  return attempt < maxAttempts;
};

/**
 * SQL to mark a claimed task as failed and re-queue it for retry with a
 * backoff-scheduled `available_at`, OR transition it to terminal `failed` if
 * the caller decides not to retry. The caller computes `available_at` and
 * `last_error` in TypeScript (using `computeNextAvailableAt`) and passes them
 * as parameters — no SQL-side time math, so behavior is deterministic and
 * testable.
 *
 * Parameters:
 *   - `$1`: task id (number)
 *   - `$2`: next `available_at` timestamp, or NULL to transition to `failed`
 *   - `$3`: last_error message (string, may be empty)
 *
 * When `$2` is NULL the row becomes `failed` (terminal). Otherwise it becomes
 * `pending` with the supplied `available_at`, clearing the lock.
 */
export const REQUEUE_OR_FAIL_TASK_SQL = /* sql */ `
UPDATE tasks
SET status       = CASE WHEN $2::timestamptz IS NULL THEN 'failed' ELSE 'pending' END,
    available_at = COALESCE($2::timestamptz, available_at),
    locked_at    = NULL,
    locked_by    = NULL,
    last_error   = $3,
    updated_at   = now()
WHERE id = $1
RETURNING *
`.trim();

/** Same as `REQUEUE_OR_FAIL_TASK_SQL` but for `jobs`. */
export const REQUEUE_OR_FAIL_JOB_SQL = /* sql */ `
UPDATE jobs
SET status       = CASE WHEN $2::timestamptz IS NULL THEN 'failed' ELSE 'pending' END,
    available_at = COALESCE($2::timestamptz, available_at),
    locked_at    = NULL,
    locked_by    = NULL,
    last_error   = $3,
    updated_at   = now()
WHERE id = $1
RETURNING *
`.trim();

/* -------------------------------------------------------------------------- */
/* Stale-lock recovery                                                        */
/* -------------------------------------------------------------------------- */

/**
 * SQL to recover queue rows stuck in `running` whose `locked_at` is older than
 * a caller-computed cutoff. Recovered rows are re-queued as `pending` with
 * `available_at = now()`, their lock cleared, and a note appended to
 * `last_error` so the recovery is observable.
 *
 * The cutoff is computed in TypeScript via `computeStaleLockCutoff` and passed
 * as `$1` — no SQL-side interval math, so the cutoff is deterministic and
 * testable.
 *
 * Parameters:
 *   - `$1`: cutoff timestamp. Rows with `locked_at < $1` are recovered.
 *
 * Returns the recovered ids and their attempt counts for observability.
 */
export const RECOVER_STALE_TASKS_SQL = /* sql */ `
UPDATE tasks
SET status       = 'pending',
    available_at = now(),
    locked_at    = NULL,
    locked_by    = NULL,
    last_error   = COALESCE(last_error || E'\n', '') || 'stale lock recovered',
    updated_at   = now()
WHERE status = 'running'
  AND locked_at IS NOT NULL
  AND locked_at < $1
RETURNING id, attempts
`.trim();

/** Same as `RECOVER_STALE_TASKS_SQL` but for `jobs`. */
export const RECOVER_STALE_JOBS_SQL = /* sql */ `
UPDATE jobs
SET status       = 'pending',
    available_at = now(),
    locked_at    = NULL,
    locked_by    = NULL,
    last_error   = COALESCE(last_error || E'\n', '') || 'stale lock recovered',
    updated_at   = now()
WHERE status = 'running'
  AND locked_at IS NOT NULL
  AND locked_at < $1
RETURNING id, attempts
`.trim();

/**
 * Compute the cutoff timestamp for stale-lock recovery: any row whose
 * `locked_at` is older than `now - staleAfterMs` is considered stale.
 *
 * Pure and deterministic given `now`. The caller passes the returned `Date` as
 * the `$1` parameter to `RECOVER_STALE_*_SQL`.
 *
 * @param now Current time (injectable for tests).
 * @param staleAfterMs Max acceptable lock age in ms. Must be > 0.
 */
export const computeStaleLockCutoff = (
  now: Date,
  staleAfterMs: number,
): Date => {
  if (!(now instanceof Date && !Number.isNaN(now.getTime()))) {
    throw new TypeError('now must be a valid Date');
  }
  if (!Number.isFinite(staleAfterMs) || staleAfterMs <= 0) {
    throw new TypeError('staleAfterMs must be a positive finite number');
  }
  return new Date(now.getTime() - Math.trunc(staleAfterMs));
};

/** Parameter array for `RECOVER_STALE_*_SQL`. */
export const staleRecoveryParams = (cutoff: Date): readonly [Date] => {
  if (!(cutoff instanceof Date && !Number.isNaN(cutoff.getTime()))) {
    throw new TypeError('cutoff must be a valid Date');
  }
  return [cutoff] as const;
};

/* -------------------------------------------------------------------------- */
/* Idempotency guidance                                                       */
/* -------------------------------------------------------------------------- */

/**
 * Idempotency guidance for the control plane.
 *
 * 1. `tasks.external_id` is the caller-supplied idempotency key for creating a
 *    task. Re-POSTing the same `external_id` MUST return the existing task
 *    rather than create a duplicate. The `tasks_external_id_unique` constraint
 *    enforces this at the DB level.
 *
 * 2. `evidence.evidence_identity` (the SHA-256 of the canonical manifest) is
 *    globally unique. Re-ingesting the same manifest is a no-op; the
 *    `evidence_identity_unique` constraint enforces this.
 *
 * 3. When a manifest carries an `idempotency_key`, the
 *    `(repository_owner, repository_name, head_sha, idempotency_key)` tuple is
 *    unique (`evidence_repo_sha_idem_unique`). This allows the same key to be
 *    reused across different head SHAs (different evidence productions) while
 *    preventing duplicates for the same head SHA.
 *
 * 4. Claiming is NOT idempotent: each successful claim increments `attempts`.
 *    Workers MUST use a stable worker id and only claim when they intend to
 *    execute. Re-claiming after a crash is handled by stale-lock recovery, not
 *    by re-running the claim.
 *
 * 5. `agent_runs` are idempotent per `(provider, external_run_id)` when the
 *    provider supplies an external id. Re-reporting the same external run
 *    updates the existing row rather than creating a duplicate.
 */
export const IDEMPOTENCY_GUIDANCE = [
  'tasks.external_id is the creation idempotency key (UNIQUE).',
  'evidence.evidence_identity (SHA-256 of canonical manifest) is globally UNIQUE.',
  'evidence (repo_owner, repo_name, head_sha, idempotency_key) is UNIQUE when idempotency_key is present.',
  'Claiming is NOT idempotent: each claim increments attempts. Use a stable worker id.',
  'agent_runs (provider, external_run_id) is UNIQUE when external_run_id is present.',
] as const;

/* -------------------------------------------------------------------------- */
/* Internal: deterministic 32-bit hash for jitter                             */
/* -------------------------------------------------------------------------- */

/**
 * Small deterministic 32-bit hash of (seed, attempt). Not cryptographic — used
 * only to derive a stable jitter multiplier. Uses the FNV-1a variant mixed
 * with the attempt so different attempts yield different delays.
 */
const hash32 = (seed: number, attempt: number): number => {
  let h = seed ^ 0x811c9dc5;
  // Mix the seed.
  h = Math.imul(h, 0x01000193);
  h ^= (seed >>> 8) & 0xff;
  h = Math.imul(h, 0x01000193);
  // Mix the attempt (little-endian bytes).
  const a = attempt | 0;
  for (let i = 0; i < 4; i++) {
    h ^= (a >>> (i * 8)) & 0xff;
    h = Math.imul(h, 0x01000193);
  }
  return h | 0;
};

/* -------------------------------------------------------------------------- */
/* Status transition helpers (re-exported for convenience)                    */
/* -------------------------------------------------------------------------- */

/** Re-exported type for callers who import everything from queue. */
export type { QueueStatus };
