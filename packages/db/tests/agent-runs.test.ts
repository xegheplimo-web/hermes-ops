import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  AGENT_RUN_STATUSES,
  AgentRunRow,
  QUEUE_STATUSES,
  QUEUE_TRANSITIONS,
  isValidQueueTransition,
} from '../src/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const migration = readFileSync(
  join(here, '..', 'src', 'migrations', '0003_init_agent_runs.sql'),
  'utf8',
);

describe('agent_runs — generic, not provider-specific', () => {
  it('creates exactly one agent_runs table', () => {
    const matches = migration.match(/CREATE TABLE IF NOT EXISTS agent_runs/g);
    expect(matches).not.toBeNull();
    expect(matches!.length).toBe(1);
  });

  it('does not create provider-specific session tables', () => {
    expect(/CREATE TABLE IF NOT EXISTS devin_/i.test(migration)).toBe(false);
    expect(/CREATE TABLE IF NOT EXISTS coderabbit_/i.test(migration)).toBe(false);
    expect(/CREATE TABLE IF NOT EXISTS github_/i.test(migration)).toBe(false);
    expect(/CREATE TABLE IF NOT EXISTS \w+_sessions/i.test(migration)).toBe(false);
  });

  it('records provider as a column, not a table', () => {
    expect(migration).toContain('provider');
    expect(migration).toContain("CHECK (provider <> '')");
  });

  it('external_run_id is optional and unique per provider', () => {
    expect(migration).toContain('external_run_id TEXT');
    expect(migration).toContain('UNIQUE (provider, external_run_id)');
  });

  it('status enum includes succeeded and timed_out (distinct from queue)', () => {
    expect(migration).toContain("'succeeded'");
    expect(migration).toContain("'timed_out'");
    expect(AGENT_RUN_STATUSES).toContain('succeeded');
    expect(AGENT_RUN_STATUSES).toContain('timed_out');
  });

  it('queue statuses do not include succeeded/timed_out', () => {
    expect(QUEUE_STATUSES).not.toContain('succeeded');
    expect(QUEUE_STATUSES).not.toContain('timed_out');
  });

  it('AgentRunRow type is generic (no provider-specific fields)', () => {
    // Compile-time check: the type is exported and accepts a generic shape.
    const row: AgentRunRow = {
      id: 1,
      job_id: 1,
      provider: 'devin',
      external_run_id: 'run-1',
      status: 'running',
      started_at: new Date(),
      finished_at: null,
      result: null,
      error: null,
      created_at: new Date(),
      updated_at: new Date(),
    };
    expect(row.provider).toBe('devin');
    expect(row.status).toBe('running');
  });
});

describe('queue transitions', () => {
  it('allows pending -> running', () => {
    expect(isValidQueueTransition('pending', 'running')).toBe(true);
  });

  it('allows running -> completed/failed/pending/cancelled', () => {
    expect(isValidQueueTransition('running', 'completed')).toBe(true);
    expect(isValidQueueTransition('running', 'failed')).toBe(true);
    expect(isValidQueueTransition('running', 'pending')).toBe(true);
    expect(isValidQueueTransition('running', 'cancelled')).toBe(true);
  });

  it('allows failed -> pending (retry after backoff)', () => {
    expect(isValidQueueTransition('failed', 'pending')).toBe(true);
  });

  it('forbids transitions out of terminal states', () => {
    expect(isValidQueueTransition('completed', 'pending')).toBe(false);
    expect(isValidQueueTransition('completed', 'running')).toBe(false);
    expect(isValidQueueTransition('cancelled', 'pending')).toBe(false);
    expect(isValidQueueTransition('cancelled', 'running')).toBe(false);
  });

  it('forbids pending -> completed (must go through running)', () => {
    expect(isValidQueueTransition('pending', 'completed')).toBe(false);
    expect(isValidQueueTransition('pending', 'failed')).toBe(false);
  });

  it('QUEUE_TRANSITIONS covers every QueueStatus', () => {
    for (const s of QUEUE_STATUSES) {
      expect(QUEUE_TRANSITIONS.has(s)).toBe(true);
    }
  });
});
