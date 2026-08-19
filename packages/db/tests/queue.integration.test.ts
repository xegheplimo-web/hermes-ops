/**
 * Integration tests for the job queue against a REAL PostgreSQL database.
 *
 * These tests prove the `FOR UPDATE SKIP LOCKED` claim, stale-lock recovery,
 * retry/backoff re-queue, and idempotency-of-claiming behavior against a live
 * Postgres instance — not a mock.
 *
 * Connection:
 *   - The suite connects using `DATABASE_URL`. If `DATABASE_URL` is unset the
 *     whole suite is skipped via `describe.skipIf` — it never fails and never
 *     hangs in environments without a database.
 *
 * Isolation:
 *   - A dedicated ephemeral schema (`hermes_qit_<hex>`) is created per run and
 *     set as the `search_path` for every connection, so the unqualified
 *     `tasks`/`jobs`/`schema_migrations` tables land there and never touch real
 *     data. The schema is dropped with `CASCADE` in `afterAll`.
 *
 * Migrations:
 *   - `runMigrations` from `src/migrate.ts` is run against a client whose
 *     `search_path` points at the ephemeral schema, so the schema is created
 *     exactly as in production before tests run.
 *
 * Safety:
 *   - All user/row values are passed as positional parameters. The only
 *     identifier interpolated into SQL is the generated schema name, which is a
 *     trusted, validated `[a-z_][a-z0-9_]*` literal produced by this file (not
 *     user input) — Postgres does not accept identifiers as parameters.
 *   - All pool/client handles are closed in `afterAll` so vitest exits cleanly.
 */

import { randomBytes } from 'node:crypto';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import pg from 'pg';
import {
  CLAIM_TASK_SQL,
  RECOVER_STALE_TASKS_SQL,
  REQUEUE_OR_FAIL_TASK_SQL,
  computeNextAvailableAt,
  computeStaleLockCutoff,
  runMigrations,
} from '../src/index.js';

/* -------------------------------------------------------------------------- */
/* Environment                                                                */
/* -------------------------------------------------------------------------- */

const DATABASE_URL = process.env.DATABASE_URL ?? '';

/**
 * Schema name for this run. Generated from a fixed prefix plus 8 hex bytes so
 * parallel runs do not collide. Validated to match a Postgres identifier so the
 * interpolation below is provably injection-free.
 */
const SCHEMA_NAME = `hermes_qit_${randomBytes(8).toString('hex')}`;
if (!/^[a-z_][a-z0-9_]*$/.test(SCHEMA_NAME)) {
  throw new Error(`Generated schema name is not a valid identifier: ${SCHEMA_NAME}`);
}

/* -------------------------------------------------------------------------- */
/* Shared pool                                                                */
/* -------------------------------------------------------------------------- */

const pool = new pg.Pool({ connectionString: DATABASE_URL });

/** A `PoolClient` with its `search_path` pinned to the ephemeral schema. */
const connect = async (): Promise<pg.PoolClient> => {
  const client = await pool.connect();
  // SCHEMA_NAME is a validated identifier (see above), safe to interpolate.
  await client.query(`SET search_path TO ${SCHEMA_NAME}`);
  return client;
};

/**
 * Run a single query against a fresh client whose `search_path` is the
 * ephemeral schema. Use this for one-shot queries that do not need to share a
 * transaction or session state.
 */
const query = async <T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params?: ReadonlyArray<unknown>,
): Promise<pg.QueryResult<T>> => {
  const client = await connect();
  try {
    return await client.query<T>(text, params as unknown[]);
  } finally {
    client.release();
  }
};

/* -------------------------------------------------------------------------- */
/* Test-row helpers                                                           */
/* -------------------------------------------------------------------------- */

/** Monotonic counter for unique `external_id` / `head_sha` generation. */
let rowCounter = 0;

/**
 * Insert one `tasks` row in `pending` status and return its id.
 *
 * `availableAt` defaults to `now()` (immediately claimable); pass a future
 * `Date` to test deferred availability. All values are parameterized.
 */
const insertTask = async (availableAt: Date = new Date()): Promise<number> => {
  rowCounter += 1;
  const externalId = `it-${rowCounter}-${SCHEMA_NAME}`;
  const headSha = rowCounter.toString(16).padStart(40, '0').slice(-40);
  const { rows } = await query<{ id: number }>(
    `
    INSERT INTO tasks (external_id, repository_owner, repository_name, head_sha, policy_version, payload, status, available_at)
    VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)
    RETURNING id
    `.trim(),
    [
      externalId,
      'hermes-ops',
      'integration-test',
      headSha,
      'v0',
      JSON.stringify({ n: rowCounter }),
      availableAt,
    ],
  );
  if (rows.length !== 1) {
    throw new Error('insertTask did not return a row id');
  }
  return firstRow(rows).id;
};

/**
 * Return the first row of `rows`, throwing if there are none. Narrows away the
 * `T | undefined` that `noUncheckedIndexedAccess` adds to `rows[0]` after a
 * length check.
 */
const firstRow = <T extends pg.QueryResultRow>(rows: ReadonlyArray<T>): T => {
  if (rows.length === 0) throw new Error('expected at least one row, got none');
  return rows[0] as T;
};

/**
 * Claim every available task for `workerId`, looping until `CLAIM_TASK_SQL`
 * returns no row. Each claim is its own atomic `UPDATE ... RETURNING`. Returns
 * the claimed ids in claim order.
 */
const claimAll = async (client: pg.PoolClient, workerId: string): Promise<number[]> => {
  const ids: number[] = [];
  for (;;) {
    const { rows } = await client.query<{ id: number }>(CLAIM_TASK_SQL, [workerId]);
    if (rows.length === 0) break;
    if (rows.length > 1) {
      throw new Error('CLAIM_TASK_SQL returned more than one row');
    }
    ids.push(firstRow(rows).id);
  }
  return ids;
};

/* -------------------------------------------------------------------------- */
/* Lifecycle                                                                  */
/* -------------------------------------------------------------------------- */

beforeAll(async () => {
  // Create the ephemeral schema.
  // SCHEMA_NAME is a validated identifier (see above), safe to interpolate.
  await pool.query(`CREATE SCHEMA IF NOT EXISTS ${SCHEMA_NAME}`);

  // Apply migrations into the ephemeral schema via a client whose search_path
  // points at it. The migration runner creates `schema_migrations` and the
  // queue tables unqualified, so they land in our schema.
  const migrateClient = await connect();
  try {
    await runMigrations(migrateClient);
  } finally {
    migrateClient.release();
  }
});

afterAll(async () => {
  // Drop the ephemeral schema (and everything in it) and close the pool so
  // vitest can exit cleanly. Both are best-effort; errors are swallowed so a
  // failure here does not mask the real test result.
  try {
    // SCHEMA_NAME is a validated identifier (see above), safe to interpolate.
    await pool.query(`DROP SCHEMA IF EXISTS ${SCHEMA_NAME} CASCADE`);
  } catch {
    /* no-op */
  }
  await pool.end().catch(() => {
    /* no-op */
  });
});

/* -------------------------------------------------------------------------- */
/* Test suite — skipped entirely when DATABASE_URL is unset                   */
/* -------------------------------------------------------------------------- */

describe.skipIf(!DATABASE_URL)('queue — PostgreSQL integration', () => {
  afterEach(async () => {
    // Ensure each test starts from a clean queue state. Previous tests may
    // leave rows in `pending` (e.g. the stale-lock recovery test), and those
    // rows would otherwise be claimed by later tests that expect to claim the
    // row they just inserted.
    await query('TRUNCATE tasks, jobs RESTART IDENTITY CASCADE');
  });

  /* ---------------------------------------------------------------------- */
  /* (a) Two concurrent claimers never double-claim and cover every job     */
  /* ---------------------------------------------------------------------- */

  it('two concurrent SKIP LOCKED claimers partition all jobs with no overlap', async () => {
    const N = 20;
    const ids: number[] = [];
    for (let i = 0; i < N; i++) {
      ids.push(await insertTask());
    }
    const expected = new Set(ids);
    expect(expected.size).toBe(N);

    // Two independent clients, each with search_path pinned, claiming
    // concurrently. SKIP LOCKED must ensure no id is claimed by both.
    const [a, b] = await Promise.all([
      connect().then((c) => claimAll(c, 'worker-a').finally(() => c.release())),
      connect().then((c) => claimAll(c, 'worker-b').finally(() => c.release())),
    ]);

    const bSet = new Set(b);

    // No single job id is claimed twice.
    const overlap = a.filter((id) => bSet.has(id));
    expect(overlap).toEqual([]);

    // The union of claimed ids equals exactly the ids that were available.
    const union = new Set<number>([...a, ...b]);
    expect(union.size).toBe(N);
    for (const id of expected) {
      expect(union.has(id)).toBe(true);
    }
  });

  /* ---------------------------------------------------------------------- */
  /* (b) A job whose available_at is in the future is NOT claimed            */
  /* ---------------------------------------------------------------------- */

  it('does not claim a task whose available_at is in the future', async () => {
    const future = new Date(Date.now() + 60 * 60 * 1000); // +1h
    const id = await insertTask(future);

    // No pending+available row, so the claim returns nothing.
    const { rows } = await query<{ id: number }>(CLAIM_TASK_SQL, ['worker-future']);
    expect(rows).toHaveLength(0);

    // The row is still pending and untouched.
    const { rows: checkRows } = await query<{ status: string; attempts: number }>(
      'SELECT status, attempts FROM tasks WHERE id = $1',
      [id],
    );
    expect(checkRows).toHaveLength(1);
    expect(firstRow(checkRows).status).toBe('pending');
    expect(firstRow(checkRows).attempts).toBe(0);
  });

  /* ---------------------------------------------------------------------- */
  /* (c) Stale lock recovery re-queues a long-locked row                    */
  /* ---------------------------------------------------------------------- */

  it('recovers a row whose locked_at is older than the stale cutoff', async () => {
    const id = await insertTask();

    // Claim it so it becomes running with locked_at = now().
    const { rows: claimed } = await query<{ id: number }>(CLAIM_TASK_SQL, [
      'worker-stale',
    ]);
    expect(claimed).toHaveLength(1);
    expect(firstRow(claimed).id).toBe(id);

    // Simulate a stale lock by backdating locked_at to 10 minutes ago. The
    // cutoff is now - 60s, so the row is well past the stale threshold.
    const backdated = new Date(Date.now() - 10 * 60 * 1000);
    await query('UPDATE tasks SET locked_at = $1 WHERE id = $2', [backdated, id]);

    const cutoff = computeStaleLockCutoff(new Date(), 60_000);
    const { rows: recovered } = await query<{ id: number; attempts: number }>(
      RECOVER_STALE_TASKS_SQL,
      [cutoff],
    );

    // The recovery returned exactly our row.
    expect(recovered.map((r) => r.id)).toContain(id);

    // The row is pending again, lock cleared, available_at set, and the
    // recovery is observable in last_error.
    const { rows: checkRows } = await query<{
      status: string;
      locked_at: Date | null;
      locked_by: string | null;
      available_at: Date;
      last_error: string | null;
    }>('SELECT status, locked_at, locked_by, available_at, last_error FROM tasks WHERE id = $1', [
      id,
    ]);
    expect(checkRows).toHaveLength(1);
    const row = firstRow(checkRows);
    expect(row.status).toBe('pending');
    expect(row.locked_at).toBeNull();
    expect(row.locked_by).toBeNull();
    expect(row.available_at.getTime()).toBeGreaterThan(0);
    expect(row.last_error).toContain('stale lock recovered');
  });

  /* ---------------------------------------------------------------------- */
  /* (d) Retry/backoff: a failed job records last_error and future availability */
  /* ---------------------------------------------------------------------- */

  it('re-queues a failed task with last_error and a future available_at', async () => {
    const id = await insertTask();

    // Claim → attempts 0 -> 1, status running.
    const { rows: claimed } = await query<{ id: number; attempts: number }>(
      CLAIM_TASK_SQL,
      ['worker-retry'],
    );
    expect(claimed).toHaveLength(1);
    expect(firstRow(claimed).id).toBe(id);
    expect(firstRow(claimed).attempts).toBe(1);

    // Decide the next availability in TypeScript (deterministic) and re-queue.
    const now = new Date();
    const nextAvailable = computeNextAvailableAt(1, now, {
      baseMs: 1000,
      maxMs: 60_000,
      maxAttempts: 5,
    });
    expect(nextAvailable).not.toBeNull();
    const next = nextAvailable as Date;

    const { rows: requeued } = await query<{ id: number }>(REQUEUE_OR_FAIL_TASK_SQL, [
      id,
      next,
      'boom',
    ]);
    expect(requeued).toHaveLength(1);

    const { rows: checkRows } = await query<{
      status: string;
      attempts: number;
      available_at: Date;
      locked_at: Date | null;
      locked_by: string | null;
      last_error: string | null;
    }>(
      'SELECT status, attempts, available_at, locked_at, locked_by, last_error FROM tasks WHERE id = $1',
      [id],
    );
    expect(checkRows).toHaveLength(1);
    const row = firstRow(checkRows);
    // The claim incremented attempts; the re-queue preserves it.
    expect(row.attempts).toBe(1);
    // Re-queued as pending with a future available_at and the error recorded.
    expect(row.status).toBe('pending');
    expect(row.last_error).toBe('boom');
    expect(row.locked_at).toBeNull();
    expect(row.locked_by).toBeNull();
    // available_at moved into the future (matches the computed timestamp).
    expect(row.available_at.getTime()).toBe(next.getTime());
    expect(row.available_at.getTime()).toBeGreaterThan(now.getTime());
  });

  /* ---------------------------------------------------------------------- */
  /* (e) Idempotency: re-claiming an already-claimed job returns nothing    */
  /* ---------------------------------------------------------------------- */

  it('claiming an already-claimed task returns nothing rather than double-processing', async () => {
    const id = await insertTask();

    // First claim succeeds.
    const { rows: first } = await query<{ id: number; attempts: number }>(
      CLAIM_TASK_SQL,
      ['worker-idem-1'],
    );
    expect(first).toHaveLength(1);
    expect(firstRow(first).id).toBe(id);
    expect(firstRow(first).attempts).toBe(1);

    // A second claim (same OR different worker) finds no pending row because
    // the task is now `running`, so it returns nothing — no double processing.
    const { rows: second } = await query<{ id: number }>(CLAIM_TASK_SQL, [
      'worker-idem-2',
    ]);
    expect(second).toHaveLength(0);

    // The row is still running with attempts still 1 (not incremented again).
    const { rows: checkRows } = await query<{ status: string; attempts: number }>(
      'SELECT status, attempts FROM tasks WHERE id = $1',
      [id],
    );
    expect(checkRows).toHaveLength(1);
    expect(firstRow(checkRows).status).toBe('running');
    expect(firstRow(checkRows).attempts).toBe(1);
  });
});
