/**
 * Typed table and status definitions for the Hermes Ops control-plane DB.
 *
 * These types mirror the SQL schema in `src/migrations/`. They are row shapes
 * only — no ORM, no driver. A Postgres driver (e.g. `pg` or `postgres`) is
 * intentionally optional; tests and pure helpers do not require a live
 * database. The claim/recovery SQL in `queue.ts` is driver-agnostic and uses
 * positional `$1` parameters.
 */

/* -------------------------------------------------------------------------- */
/* Status enums                                                               */
/* -------------------------------------------------------------------------- */

/** Lifecycle status for `tasks` and `jobs` (the queue tables). */
export type QueueStatus =
  | 'planning'
  | 'queued'
  | 'pending'
  | 'running'
  | 'dispatched'
  | 'verifying'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'blocked';

/** Lifecycle status for `agent_runs`. Distinct from `QueueStatus` because
 *  agent runs have a `succeeded`/`timed_out` distinction that queue rows do
 *  not. */
export type AgentRunStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timed_out';

/** All distinct status strings the schema accepts, for validation helpers. */
export const QUEUE_STATUSES: readonly QueueStatus[] = [
  'planning',
  'queued',
  'pending',
  'running',
  'dispatched',
  'verifying',
  'completed',
  'failed',
  'cancelled',
  'blocked',
] as const;

export const AGENT_RUN_STATUSES: readonly AgentRunStatus[] = [
  'pending',
  'running',
  'succeeded',
  'failed',
  'cancelled',
  'timed_out',
] as const;

/** Terminal queue statuses (no further transition expected). */
export const TERMINAL_QUEUE_STATUSES: readonly QueueStatus[] = [
  'completed',
  'failed',
  'cancelled',
] as const;

/** Allowed transitions for queue rows (tasks/jobs). */
export const QUEUE_TRANSITIONS: ReadonlyMap<QueueStatus, readonly QueueStatus[]> =
  new Map<QueueStatus, readonly QueueStatus[]>([
    ['planning', ['queued', 'cancelled', 'failed']],
    ['queued', ['pending', 'blocked', 'cancelled']],
    ['pending', ['running', 'blocked', 'cancelled']],
    ['running', ['dispatched', 'verifying', 'completed', 'failed', 'pending', 'cancelled']],
    ['dispatched', ['verifying', 'running', 'failed', 'cancelled']],
    ['verifying', ['completed', 'failed', 'running', 'cancelled']],
    ['completed', []],
    ['failed', ['pending', 'queued', 'cancelled']],
    ['cancelled', []],
    ['blocked', ['queued', 'pending', 'cancelled']],
  ]);

/**
 * Validate that a queue status transition is allowed by `QUEUE_TRANSITIONS`.
 * Pure, deterministic, no DB.
 */
export const isValidQueueTransition = (
  from: QueueStatus,
  to: QueueStatus,
): boolean => {
  const allowed = QUEUE_TRANSITIONS.get(from);
  return allowed !== undefined && allowed.includes(to);
};

/* -------------------------------------------------------------------------- */
/* Row shapes                                                                 */
/* -------------------------------------------------------------------------- */

/** `tasks` row. Queue fields are nullable per the schema. */
export interface TaskRow {
  readonly id: number;
  readonly external_id: string;
  readonly repository_owner: string;
  readonly repository_name: string;
  readonly pr_number: number | null;
  readonly head_sha: string;
  readonly policy_version: string;
  readonly payload: unknown;
  readonly status: QueueStatus;
  readonly attempts: number;
  readonly max_attempts: number;
  readonly available_at: Date;
  readonly locked_at: Date | null;
  readonly locked_by: string | null;
  readonly last_error: string | null;
  readonly created_at: Date;
  readonly updated_at: Date;
  readonly review_run_id: string | null;
  readonly dag_payload: unknown;
}

/** `jobs` row. */
export interface JobRow {
  readonly id: number;
  readonly task_id: number;
  readonly kind: string;
  readonly status: QueueStatus;
  readonly attempts: number;
  readonly max_attempts: number;
  readonly available_at: Date;
  readonly locked_at: Date | null;
  readonly locked_by: string | null;
  readonly last_error: string | null;
  readonly created_at: Date;
  readonly updated_at: Date;
}

/** `agent_runs` row. Generic across providers. */
export interface AgentRunRow {
  readonly id: number;
  readonly job_id: number;
  readonly provider: string;
  readonly external_run_id: string | null;
  readonly status: AgentRunStatus;
  readonly started_at: Date | null;
  readonly finished_at: Date | null;
  readonly result: unknown;
  readonly error: string | null;
  readonly created_at: Date;
  readonly updated_at: Date;
}

/** `evidence` row. Bound to repository/PR/head SHA and policy version. */
export interface EvidenceRow {
  readonly id: number;
  readonly task_id: number | null;
  readonly repository_owner: string;
  readonly repository_name: string;
  readonly pr_number: number | null;
  readonly head_sha: string;
  readonly policy_version: string;
  readonly evidence_identity: string;
  readonly manifest: unknown;
  readonly idempotency_key: string | null;
  readonly created_at: Date;
}

/** `audit_events` row. Append-only. */
export interface AuditEventRow {
  readonly id: number;
  readonly task_id: number | null;
  readonly job_id: number | null;
  readonly actor: string;
  readonly action: string;
  readonly detail: unknown;
  readonly created_at: Date;
}

/* -------------------------------------------------------------------------- */
/* Migration catalog                                                          */
/* -------------------------------------------------------------------------- */

/** Ordered list of migration filenames, in dependency order. */
export const MIGRATION_FILES: readonly string[] = [
  '0001_init_tasks.sql',
  '0002_init_jobs.sql',
  '0003_init_agent_runs.sql',
  '0004_init_evidence.sql',
  '0005_init_audit_events.sql',
  '0006_expand_task_statuses.sql',
] as const;

/** Table each migration creates, keyed by filename. */
export const MIGRATION_TABLES: ReadonlyMap<string, string> = new Map<
  string,
  string
>([
  ['0001_init_tasks.sql', 'tasks'],
  ['0002_init_jobs.sql', 'jobs'],
  ['0003_init_agent_runs.sql', 'agent_runs'],
  ['0004_init_evidence.sql', 'evidence'],
  ['0005_init_audit_events.sql', 'audit_events'],
    ['0006_expand_task_statuses.sql', 'tasks'],
  ]);
