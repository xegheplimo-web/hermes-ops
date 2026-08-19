/**
 * PostgreSQL migration runner for the Hermes Ops control plane.
 *
 * Design:
 *   - Migrations are plain `.sql` files in `src/migrations/`, applied in
 *     ascending filename order. The filename (minus `.sql`) is the version.
 *   - A `schema_migrations` table records applied versions and a SHA-256
 *     checksum of the file content at apply time. Already-applied versions are
 *     skipped; a checksum mismatch on a previously-applied version is a hard
 *     failure (the migration file drifted after being applied).
 *   - Each migration runs in its own transaction (BEGIN / COMMIT, ROLLBACK on
 *     error). The bookkeeping INSERT into `schema_migrations` is part of the
 *     same transaction so a failed migration never leaves a partial record.
 *
 * The runner is driver-agnostic over a minimal {@link MigrationClient} surface
 * (just `query(text, params?)`) so it can be unit-tested with a stub client and
 * no live database. `pg`'s `Client` satisfies this interface.
 */

import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Minimal client surface required by the runner. `pg.Client` satisfies this. */
export interface MigrationClient {
  query(text: string, params?: ReadonlyArray<unknown>): Promise<{
    readonly rows: ReadonlyArray<Record<string, unknown>>;
  }>;
}

/** Result of a single migration attempt. */
export interface MigrationResult {
  /** Version (filename without `.sql`). */
  readonly version: string;
  /** Filename, e.g. `0001_init_tasks.sql`. */
  readonly filename: string;
  /** SHA-256 hex digest of the file content. */
  readonly checksum: string;
  /** `applied` if newly applied, `skipped` if already recorded. */
  readonly status: 'applied' | 'skipped';
}

export interface RunMigrationsOptions {
  /**
   * Directory to read `.sql` files from. Defaults to the `src/migrations`
   * directory adjacent to this module (resolved relative to the module URL so
   * it works whether the runner is invoked from `src/` or the compiled `dist/`).
   */
  readonly migrationsDir?: string;
}

/** SQL to create the bookkeeping table if it does not already exist. */
export const SCHEMA_MIGRATIONS_DDL = /* sql */ `
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
)
`.trim();

/** SQL to look up the recorded checksum for a version. */
export const SELECT_CHECKSUM_SQL = /* sql */ `
SELECT checksum FROM schema_migrations WHERE version = $1
`.trim();

/** SQL to record a newly applied migration. */
export const INSERT_MIGRATION_SQL = /* sql */ `
INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)
`.trim();

/**
 * Resolve the default migrations directory. Uses `../src/migrations/` relative
 * to this module so the same path works from `src/migrate.ts` (→
 * `src/migrations/`) and from the compiled `dist/migrate.js` (→ `src/migrations/`
 * via `dist/../src/migrations/`). The package is private to the monorepo, so
 * the source tree is always present.
 */
const defaultMigrationsDir = (): string => {
  const here = dirname(fileURLToPath(import.meta.url));
  return join(here, '..', 'src', 'migrations');
};

/**
 * List migration files in a directory, sorted by filename ascending. Only
 * `.sql` files are considered.
 */
export const listMigrationFiles = (dir: string): string[] =>
  readdirSync(dir)
    .filter((f) => f.endsWith('.sql'))
    .sort();

/** Compute the SHA-256 hex digest of a string. */
export const checksumOf = (content: string): string =>
  createHash('sha256').update(content, 'utf8').digest('hex');

/** Derive the version string from a migration filename. */
export const versionOf = (filename: string): string => filename.replace(/\.sql$/, '');

/**
 * Run all pending migrations against `client`.
 *
 * Steps:
 *   1. Ensure `schema_migrations` exists.
 *   2. For each `.sql` file (ascending filename):
 *      - Compute version + SHA-256 checksum.
 *      - If the version is already recorded, compare the stored checksum to
 *        the computed one. On mismatch, throw — the file drifted after apply.
 *      - Otherwise, BEGIN, run the migration SQL, INSERT the bookkeeping row,
 *        COMMIT. On any error, ROLLBACK and rethrow.
 *
 * @returns Per-file results in applied order.
 */
export const runMigrations = async (
  client: MigrationClient,
  options: RunMigrationsOptions = {},
): Promise<MigrationResult[]> => {
  const dir = options.migrationsDir ?? defaultMigrationsDir();
  const files = listMigrationFiles(dir);

  await client.query(SCHEMA_MIGRATIONS_DDL);

  const results: MigrationResult[] = [];
  for (const filename of files) {
    const version = versionOf(filename);
    const content = readFileSync(join(dir, filename), 'utf8');
    const checksum = checksumOf(content);

    const { rows } = await client.query(SELECT_CHECKSUM_SQL, [version]);
    const existing = rows[0]?.checksum;

    if (typeof existing === 'string') {
      if (existing !== checksum) {
        throw new Error(
          `Migration checksum mismatch for version ${version} (${filename}): ` +
            `recorded ${existing} but file now hashes to ${checksum}. ` +
            `Applied migrations must not be modified.`,
        );
      }
      results.push({ version, filename, checksum, status: 'skipped' });
      continue;
    }

    try {
      await client.query('BEGIN');
      await client.query(content);
      await client.query(INSERT_MIGRATION_SQL, [version, checksum]);
      await client.query('COMMIT');
    } catch (err) {
      // Best-effort rollback; ignore rollback errors so the original cause is
      // the one that surfaces.
      try {
        await client.query('ROLLBACK');
      } catch {
        /* no-op */
      }
      const reason = err instanceof Error ? err.message : String(err);
      throw new Error(
        `Migration ${version} (${filename}) failed and was rolled back: ${reason}`,
      );
    }

    results.push({ version, filename, checksum, status: 'applied' });
  }

  return results;
};
