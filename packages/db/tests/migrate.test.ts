import { describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  INSERT_MIGRATION_SQL,
  SCHEMA_MIGRATIONS_DDL,
  SELECT_CHECKSUM_SQL,
  checksumOf,
  listMigrationFiles,
  runMigrations,
  versionOf,
  type MigrationClient,
} from '../src/index.js';

/* -------------------------------------------------------------------------- */
/* Stub client — records queries, no live database                            */
/* -------------------------------------------------------------------------- */

interface RecordedQuery {
  readonly text: string;
  readonly params: ReadonlyArray<unknown>;
}

interface StubOptions {
  /** Substring of a migration body that should make its execution throw. */
  readonly failOnContentContaining?: string;
}

interface StubClient extends MigrationClient {
  readonly queries: RecordedQuery[];
  /** Simulated `schema_migrations` rows: version -> checksum. */
  readonly applied: Map<string, string>;
}

/**
 * Fake client that records every `query` call and answers the bookkeeping
 * queries from an in-memory map. Migration body SQL is a no-op unless
 * `failOnContentContaining` matches, in which case it throws — exercising the
 * rollback path.
 */
const createStub = (options: StubOptions = {}): StubClient => {
  const queries: RecordedQuery[] = [];
  const applied = new Map<string, string>();
  const failOn = options.failOnContentContaining;

  const client: StubClient = {
    queries,
    applied,
    async query(text, params) {
      const p = (params ?? []) as unknown[];
      queries.push({ text, params: p });

      if (text === SCHEMA_MIGRATIONS_DDL) return { rows: [] };
      if (text === SELECT_CHECKSUM_SQL) {
        const version = p[0] as string;
        const checksum = applied.get(version);
        return { rows: checksum ? [{ checksum }] : [] };
      }
      if (text === INSERT_MIGRATION_SQL) {
        applied.set(p[0] as string, p[1] as string);
        return { rows: [] };
      }
      if (text === 'BEGIN' || text === 'COMMIT' || text === 'ROLLBACK') {
        return { rows: [] };
      }
      // Otherwise this is a migration body SQL statement.
      if (failOn !== undefined && text.includes(failOn)) {
        throw new Error(`simulated migration failure: ${failOn}`);
      }
      return { rows: [] };
    },
  };
  return client;
};

/* -------------------------------------------------------------------------- */
/* Temp migrations directory helpers                                          */
/* -------------------------------------------------------------------------- */

const makeTempMigrationsDir = (files: Record<string, string>): string => {
  const dir = mkdtempSync(join(tmpdir(), 'hermes-migrate-'));
  for (const [name, content] of Object.entries(files)) {
    writeFileSync(join(dir, name), content, 'utf8');
  }
  return dir;
};

const cleanup = (dir: string): void => {
  rmSync(dir, { recursive: true, force: true });
};

/* -------------------------------------------------------------------------- */
/* Pure helpers                                                               */
/* -------------------------------------------------------------------------- */

describe('migrate — pure helpers', () => {
  it('listMigrationFiles returns only .sql files, sorted ascending', () => {
    const dir = makeTempMigrationsDir({
      '0003_c.sql': '-- c',
      '0001_a.sql': '-- a',
      'README.md': '# not a migration',
      '0002_b.sql': '-- b',
    });
    try {
      expect(listMigrationFiles(dir)).toEqual([
        '0001_a.sql',
        '0002_b.sql',
        '0003_c.sql',
      ]);
    } finally {
      cleanup(dir);
    }
  });

  it('versionOf strips the .sql extension', () => {
    expect(versionOf('0001_init_tasks.sql')).toBe('0001_init_tasks');
    expect(versionOf('0042_add_index.sql')).toBe('0042_add_index');
  });

  it('checksumOf computes a stable SHA-256 hex digest', () => {
    expect(checksumOf('')).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    );
    expect(checksumOf('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
    // Deterministic.
    expect(checksumOf('abc')).toBe(checksumOf('abc'));
  });
});

/* -------------------------------------------------------------------------- */
/* runMigrations — happy path                                                 */
/* -------------------------------------------------------------------------- */

describe('runMigrations — fresh database', () => {
  it('creates the schema_migrations table first', async () => {
    const dir = makeTempMigrationsDir({ '0001_a.sql': '-- a' });
    const stub = createStub();
    try {
      await runMigrations(stub, { migrationsDir: dir });
      expect(stub.queries[0]?.text).toBe(SCHEMA_MIGRATIONS_DDL);
    } finally {
      cleanup(dir);
    }
  });

  it('applies every pending migration in ascending filename order', async () => {
    const dir = makeTempMigrationsDir({
      '0003_c.sql': 'CREATE TABLE c ();',
      '0001_a.sql': 'CREATE TABLE a ();',
      '0002_b.sql': 'CREATE TABLE b ();',
    });
    const stub = createStub();
    try {
      const results = await runMigrations(stub, { migrationsDir: dir });
      expect(results.map((r) => r.version)).toEqual([
        '0001_a',
        '0002_b',
        '0003_c',
      ]);
      expect(results.every((r) => r.status === 'applied')).toBe(true);
    } finally {
      cleanup(dir);
    }
  });

  it('wraps each migration in its own BEGIN / COMMIT transaction', async () => {
    const dir = makeTempMigrationsDir({
      '0001_a.sql': 'CREATE TABLE a ();',
      '0002_b.sql': 'CREATE TABLE b ();',
    });
    const stub = createStub();
    try {
      await runMigrations(stub, { migrationsDir: dir });

      const begins = stub.queries.filter((q) => q.text === 'BEGIN');
      const commits = stub.queries.filter((q) => q.text === 'COMMIT');
      expect(begins).toHaveLength(2);
      expect(commits).toHaveLength(2);

      // First migration block: BEGIN, body, INSERT, COMMIT — in that order.
      const beginIdx = stub.queries.findIndex((q) => q.text === 'BEGIN');
      const slice = stub.queries.slice(beginIdx, beginIdx + 4).map((q) => q.text);
      expect(slice).toEqual(['BEGIN', 'CREATE TABLE a ();', INSERT_MIGRATION_SQL, 'COMMIT']);
    } finally {
      cleanup(dir);
    }
  });

  it('records the version and checksum via the INSERT statement', async () => {
    const dir = makeTempMigrationsDir({ '0001_a.sql': 'CREATE TABLE a ();' });
    const stub = createStub();
    try {
      await runMigrations(stub, { migrationsDir: dir });
      const insert = stub.queries.find((q) => q.text === INSERT_MIGRATION_SQL);
      expect(insert).toBeDefined();
      expect(insert?.params[0]).toBe('0001_a');
      expect(insert?.params[1]).toBe(checksumOf('CREATE TABLE a ();'));
      expect(stub.applied.get('0001_a')).toBe(checksumOf('CREATE TABLE a ();'));
    } finally {
      cleanup(dir);
    }
  });

  it('returns checksums matching the file content', async () => {
    const dir = makeTempMigrationsDir({ '0001_a.sql': 'SELECT 1;' });
    const stub = createStub();
    try {
      const [result] = await runMigrations(stub, { migrationsDir: dir });
      expect(result?.checksum).toBe(checksumOf('SELECT 1;'));
    } finally {
      cleanup(dir);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* runMigrations — idempotency                                                */
/* -------------------------------------------------------------------------- */

describe('runMigrations — already applied', () => {
  it('skips versions whose checksum matches the recorded one', async () => {
    const dir = makeTempMigrationsDir({
      '0001_a.sql': 'CREATE TABLE a ();',
      '0002_b.sql': 'CREATE TABLE b ();',
    });
    const stub = createStub();
    // Pre-seed as if both were already applied with correct checksums.
    stub.applied.set('0001_a', checksumOf('CREATE TABLE a ();'));
    stub.applied.set('0002_b', checksumOf('CREATE TABLE b ();'));
    try {
      const results = await runMigrations(stub, { migrationsDir: dir });
      expect(results.every((r) => r.status === 'skipped')).toBe(true);
      // No transaction control for skipped migrations.
      expect(stub.queries.some((q) => q.text === 'BEGIN')).toBe(false);
      expect(stub.queries.some((q) => q.text === 'COMMIT')).toBe(false);
      // No INSERTs.
      expect(stub.queries.some((q) => q.text === INSERT_MIGRATION_SQL)).toBe(false);
    } finally {
      cleanup(dir);
    }
  });

  it('still issues the SELECT lookup for every file', async () => {
    const dir = makeTempMigrationsDir({
      '0001_a.sql': '-- a',
      '0002_b.sql': '-- b',
    });
    const stub = createStub();
    stub.applied.set('0001_a', checksumOf('-- a'));
    stub.applied.set('0002_b', checksumOf('-- b'));
    try {
      await runMigrations(stub, { migrationsDir: dir });
      const selects = stub.queries.filter((q) => q.text === SELECT_CHECKSUM_SQL);
      expect(selects).toHaveLength(2);
      expect(selects[0]?.params[0]).toBe('0001_a');
      expect(selects[1]?.params[0]).toBe('0002_b');
    } finally {
      cleanup(dir);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* runMigrations — checksum mismatch                                          */
/* -------------------------------------------------------------------------- */

describe('runMigrations — checksum mismatch', () => {
  it('fails loudly, naming the version, and does not apply anything after', async () => {
    const dir = makeTempMigrationsDir({
      '0001_a.sql': 'CREATE TABLE a ();',
      '0002_b.sql': 'CREATE TABLE b ();',
    });
    const stub = createStub();
    // 0001_a was applied with a DIFFERENT checksum (file drifted).
    stub.applied.set('0001_a', '0'.repeat(64));
    try {
      await expect(runMigrations(stub, { migrationsDir: dir })).rejects.toThrow(
        /Migration checksum mismatch for version 0001_a/,
      );
      // The error must name the version explicitly.
      await expect(runMigrations(stub, { migrationsDir: dir })).rejects.toThrow(
        /0001_a \(0001_a\.sql\)/,
      );
      // No transaction was opened for the mismatched migration.
      expect(stub.queries.some((q) => q.text === 'BEGIN')).toBe(false);
    } finally {
      cleanup(dir);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* runMigrations — failure / rollback                                         */
/* -------------------------------------------------------------------------- */

describe('runMigrations — failure rolls back', () => {
  it('rolls back when a migration body throws and names the version', async () => {
    const dir = makeTempMigrationsDir({
      '0001_a.sql': 'CREATE TABLE a ();',
      '0002_b.sql': 'CREATE TABLE b (); -- BOOM',
    });
    const stub = createStub({ failOnContentContaining: 'BOOM' });
    try {
      await expect(runMigrations(stub, { migrationsDir: dir })).rejects.toThrow(
        /Migration 0002_b \(0002_b\.sql\) failed and was rolled back/,
      );

      // 0001_a was applied before the failure (BEGIN/COMMIT pair for it).
      const begins = stub.queries.filter((q) => q.text === 'BEGIN');
      const commits = stub.queries.filter((q) => q.text === 'COMMIT');
      const rollbacks = stub.queries.filter((q) => q.text === 'ROLLBACK');
      expect(begins).toHaveLength(2); // one for 0001_a, one for 0002_b
      expect(commits).toHaveLength(1); // only 0001_a committed
      expect(rollbacks).toHaveLength(1); // 0002_b rolled back

      // The failing migration's INSERT never ran.
      const inserts = stub.queries.filter((q) => q.text === INSERT_MIGRATION_SQL);
      expect(inserts).toHaveLength(1);
      expect(stub.applied.has('0002_b')).toBe(false);
      expect(stub.applied.has('0001_a')).toBe(true);
    } finally {
      cleanup(dir);
    }
  });

  it('emits BEGIN, failing body, ROLLBACK in order for the failed migration', async () => {
    const dir = makeTempMigrationsDir({ '0001_a.sql': 'CREATE TABLE a (); -- BOOM' });
    const stub = createStub({ failOnContentContaining: 'BOOM' });
    try {
      await expect(runMigrations(stub, { migrationsDir: dir })).rejects.toThrow();
      const beginIdx = stub.queries.findIndex((q) => q.text === 'BEGIN');
      const slice = stub.queries.slice(beginIdx).map((q) => q.text);
      expect(slice[0]).toBe('BEGIN');
      expect(slice[1]).toContain('BOOM');
      expect(slice[2]).toBe('ROLLBACK');
      expect(slice).not.toContain('COMMIT');
    } finally {
      cleanup(dir);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* runMigrations — empty / no-op                                              */
/* -------------------------------------------------------------------------- */

describe('runMigrations — empty directory', () => {
  it('creates the bookkeeping table and applies nothing', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'hermes-migrate-empty-'));
    const stub = createStub();
    try {
      const results = await runMigrations(stub, { migrationsDir: dir });
      expect(results).toEqual([]);
      expect(stub.queries).toHaveLength(1);
      expect(stub.queries[0]?.text).toBe(SCHEMA_MIGRATIONS_DDL);
    } finally {
      cleanup(dir);
    }
  });
});
