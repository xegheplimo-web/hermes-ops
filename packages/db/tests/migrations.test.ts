import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  AGENT_RUN_STATUSES,
  MIGRATION_FILES,
  MIGRATION_TABLES,
  QUEUE_STATUSES,
} from '../src/index.js';

const here = dirname(fileURLToPath(import.meta.url));
const migrationsDir = join(here, '..', 'src', 'migrations');

const readMigration = (name: string): string =>
  readFileSync(join(migrationsDir, name), 'utf8');

const listMigrationFiles = (): string[] =>
  readdirSync(migrationsDir).filter((f) => f.endsWith('.sql')).sort();

/** Strip SQL line comments and normalize CRLF so documentation mentions of
 *  features (e.g. `FOR UPDATE SKIP LOCKED`, `pgcrypto`) do not trip content
 *  assertions about what the migration actually does. */
const stripComments = (sql: string): string =>
  sql
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((l) => l.replace(/--.*$/, ''))
    .join('\n');

describe('migrations — ordering and presence', () => {
  it('exposes an ordered, dependency-correct catalog', () => {
    expect(MIGRATION_FILES).toEqual([
      '0001_init_tasks.sql',
      '0002_init_jobs.sql',
      '0003_init_agent_runs.sql',
      '0004_init_evidence.sql',
      '0005_init_audit_events.sql',
    ]);
  });

  it('every catalog file exists on disk and vice versa', () => {
    const onDisk = listMigrationFiles();
    expect(onDisk).toEqual([...MIGRATION_FILES]);
  });

  it('filenames are zero-padded and strictly increasing', () => {
    const nums = MIGRATION_FILES.map((f) => Number(f.slice(0, 4)));
    for (let i = 0; i < nums.length; i++) {
      expect(nums[i]).toBe(i + 1);
    }
  });

  it('jobs migration runs after tasks (FK dependency)', () => {
    const tasksIdx = MIGRATION_FILES.indexOf('0001_init_tasks.sql');
    const jobsIdx = MIGRATION_FILES.indexOf('0002_init_jobs.sql');
    expect(jobsIdx).toBeGreaterThan(tasksIdx);
  });

  it('agent_runs migration runs after jobs (FK dependency)', () => {
    const jobsIdx = MIGRATION_FILES.indexOf('0002_init_jobs.sql');
    const runsIdx = MIGRATION_FILES.indexOf('0003_init_agent_runs.sql');
    expect(runsIdx).toBeGreaterThan(jobsIdx);
  });

  it('evidence migration runs after tasks (FK dependency)', () => {
    const tasksIdx = MIGRATION_FILES.indexOf('0001_init_tasks.sql');
    const evidenceIdx = MIGRATION_FILES.indexOf('0004_init_evidence.sql');
    expect(evidenceIdx).toBeGreaterThan(tasksIdx);
  });

  it('audit_events migration runs after tasks and jobs (FK dependency)', () => {
    const tasksIdx = MIGRATION_FILES.indexOf('0001_init_tasks.sql');
    const jobsIdx = MIGRATION_FILES.indexOf('0002_init_jobs.sql');
    const auditIdx = MIGRATION_FILES.indexOf('0005_init_audit_events.sql');
    expect(auditIdx).toBeGreaterThan(tasksIdx);
    expect(auditIdx).toBeGreaterThan(jobsIdx);
  });
});

describe('migrations — content', () => {
  for (const file of MIGRATION_FILES) {
    const table = MIGRATION_TABLES.get(file);
    if (!table) throw new Error(`missing table mapping for ${file}`);

    it(`${file} creates table ${table} if not exists`, () => {
      const sql = readMigration(file);
      expect(sql).toContain(`CREATE TABLE IF NOT EXISTS ${table}`);
    });

    it(`${file} does not drop tables (init migrations are additive)`, () => {
      const sql = readMigration(file);
      expect(/DROP\s+TABLE/i.test(sql)).toBe(false);
    });

    it(`${file} does not destructively alter columns`, () => {
      const sql = readMigration(file);
      expect(/DROP\s+COLUMN/i.test(sql)).toBe(false);
    });
  }

  it('tasks migration defines queue fields and constraints', () => {
    const sql = readMigration('0001_init_tasks.sql');
    for (const field of [
      'status',
      'attempts',
      'max_attempts',
      'available_at',
      'locked_at',
      'locked_by',
      'last_error',
      'external_id',
      'head_sha',
      'policy_version',
    ]) {
      expect(sql).toContain(field);
    }
    expect(sql).toContain("status IN ('pending', 'running', 'completed', 'failed', 'cancelled')");
    expect(sql).toContain('UNIQUE (external_id)');
    // Claim SQL lives in queue.ts, not in the migration. Check the
    // comment-stripped body so the documentation mention does not trip this.
    expect(stripComments(sql)).not.toContain('FOR UPDATE SKIP LOCKED');
    expect(sql).toContain('idx_tasks_claim');
  });

  it('evidence migration binds to repo/PR/head SHA and policy version', () => {
    const sql = readMigration('0004_init_evidence.sql');
    for (const field of [
      'repository_owner',
      'repository_name',
      'pr_number',
      'head_sha',
      'policy_version',
      'evidence_identity',
      'idempotency_key',
    ]) {
      expect(sql).toContain(field);
    }
    expect(sql).toContain('UNIQUE (evidence_identity)');
    expect(sql).toContain('UNIQUE (repository_owner, repository_name, head_sha, idempotency_key)');
    // evidence_identity is 64 lowercase hex (SHA-256).
    expect(sql).toContain("evidence_identity ~ '^[0-9a-f]{64}$'");
  });

  it('no migration enables pgcrypto or any extension', () => {
    for (const file of MIGRATION_FILES) {
      const sql = stripComments(readMigration(file));
      expect(/CREATE\s+EXTENSION/i.test(sql)).toBe(false);
      expect(/gen_random_uuid\(\)/i.test(sql)).toBe(false);
      expect(/pgcrypto/i.test(sql)).toBe(false);
    }
  });
});

describe('schema — status enums', () => {
  it('QUEUE_STATUSES matches the tasks/jobs CHECK constraint', () => {
    expect(QUEUE_STATUSES).toEqual([
      'pending',
      'running',
      'completed',
      'failed',
      'cancelled',
    ]);
  });

  it('AGENT_RUN_STATUSES matches the agent_runs CHECK constraint', () => {
    expect(AGENT_RUN_STATUSES).toEqual([
      'pending',
      'running',
      'succeeded',
      'failed',
      'cancelled',
      'timed_out',
    ]);
  });
});
