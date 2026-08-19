#!/usr/bin/env node
/**
 * `hermes-db-migrate` bin entry. Connects to Postgres using `DATABASE_URL` and
 * runs every pending migration in `src/migrations/`.
 *
 * Exit codes:
 *   - 0: all migrations applied (or already up to date).
 *   - 1: missing `DATABASE_URL`, connection failure, or a migration failure
 *        (including a checksum mismatch on a previously-applied version).
 */

import { runMigrations } from './migrate.js';

const connectionString = process.env.DATABASE_URL;
if (!connectionString || connectionString.length === 0) {
  process.stderr.write(
    'DATABASE_URL is not set. Export DATABASE_URL=postgres://user:pass@host:5432/db before running db:migrate.\n',
  );
  process.exit(1);
}

const { Client } = await import('pg');

const client = new Client({ connectionString });
try {
  await client.connect();
  const results = await runMigrations(client);
  if (results.length === 0) {
    process.stdout.write('No migrations found.\n');
  } else {
    for (const r of results) {
      process.stdout.write(`${r.status}\t${r.version}\t${r.filename}\n`);
    }
  }
} catch (err) {
  const msg = err instanceof Error ? err.message : String(err);
  process.stderr.write(`db:migrate failed: ${msg}\n`);
  process.exitCode = 1;
} finally {
  await client.end().catch(() => {
    /* ignore end errors so the real cause stays visible */
  });
}
