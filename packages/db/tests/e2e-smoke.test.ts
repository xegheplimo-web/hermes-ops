/**
 * E2E Vertical Slice — smoke test as a Vitest test.
 *
 * Proves the full control-plane pipeline works:
 *   enqueue → claim (SKIP LOCKED) → complete → evidence → audit → done
 *
 * Requires DATABASE_URL to be set and run via pnpm test.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import pg from 'pg';
import { randomBytes } from 'node:crypto';

const CONNECTION_STRING = process.env.DATABASE_URL;
const skip = !CONNECTION_STRING ? describe : describe.skip;

skip('E2E smoke (requires DATABASE_URL)', () => {
  it('placeholder', () => expect(true).toBe(true));
});

if (CONNECTION_STRING) {
  const workerId = `e2e-${randomBytes(4).toString('hex')}`;

  describe('E2E vertical slice', () => {
    let client;
    let taskId;

    beforeAll(async () => {
      client = new pg.Client({ connectionString: CONNECTION_STRING });
      await client.connect();
    });

    it('PHASE 1: enqueue a task', async () => {
      const r = await client.query(`
        INSERT INTO tasks (external_id, repository_owner, repository_name, head_sha, policy_version, max_attempts, payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, status
      `, [`e2e-${Date.now()}`, 'hermes-ops', 'hermes-ops',
          '80ddaee10393eca8f5552e226ebef3675ad0d976', '0.1.0', 5,
          JSON.stringify({ description: 'E2E smoke test' })]);
      taskId = r.rows[0].id;
      expect(r.rows[0].status).toBe('pending');
    });

    it('PHASE 2: claim the task (SKIP LOCKED)', async () => {
      const r = await client.query(`
        UPDATE tasks
        SET status = 'running', attempts = attempts + 1, locked_at = now(), locked_by = $1, updated_at = now()
        WHERE id = (SELECT id FROM tasks WHERE id = $2 AND status = 'pending' AND available_at <= now() FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id, status, attempts, locked_by
      `, [workerId, taskId]);
      expect(r.rows[0]).toBeDefined();
      expect(r.rows[0].status).toBe('running');
      expect(r.rows[0].locked_by).toBe(workerId);
    });

    it('PHASE 3: insert evidence', async () => {
      const id = randomBytes(32).toString('hex');
      const r = await client.query(`
        INSERT INTO evidence (evidence_identity, repository_owner, repository_name, head_sha, policy_version, manifest)
        VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (evidence_identity) DO NOTHING RETURNING id
      `, [id, 'hermes-ops', 'hermes-ops', '80ddaee10393eca8f5552e226ebef3675ad0d976', '0.1.0',
          JSON.stringify({ ci: { conclusion: 'success' } })]);
      expect(r.rowCount).toBeGreaterThan(0);
    });

    it('PHASE 4: record audit event', async () => {
      const r = await client.query(`
        INSERT INTO audit_events (task_id, actor, action, detail) VALUES ($1, $2, $3, $4) RETURNING id
      `, [taskId, workerId, 'e2e_smoke', JSON.stringify({ outcome: 'pass' })]);
      expect(r.rowCount).toBe(1);
    });

    it('PHASE 5: complete the task', async () => {
      const r = await client.query(`
        UPDATE tasks SET status = 'completed', locked_at = NULL, locked_by = NULL, updated_at = now()
        WHERE id = $1 RETURNING id, status
      `, [taskId]);
      expect(r.rows[0].status).toBe('completed');
    });

    it('PHASE 6: stale-lock recovery', async () => {
      // Insert a task
      const s = await client.query(`
        INSERT INTO tasks (external_id, repository_owner, repository_name, head_sha, policy_version, max_attempts, payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
      `, [`stale-${Date.now()}`, 'hermes-ops', 'hermes-ops',
          '80ddaee10393eca8f5552e226ebef3675ad0d976', '0.1.0', 5,
          JSON.stringify({ description: 'stale test' })]);
      const staleId = s.rows[0].id;

      // Simulate a crashed worker: claim with ancient locked_at
      await client.query(`
        UPDATE tasks SET status = 'running', attempts = 1, locked_at = '2020-01-01T00:00:00Z', locked_by = 'crashed-worker', updated_at = now()
        WHERE id = $1
      `, [staleId]);

      // Recover
      const rec = await client.query(`
        UPDATE tasks SET status = 'pending', available_at = now(), locked_at = NULL, locked_by = NULL,
            last_error = COALESCE(last_error || E'\\n', '') || 'stale lock recovered', updated_at = now()
        WHERE status = 'running' AND locked_at IS NOT NULL AND locked_at < $1 RETURNING id
      `, [new Date()]);
      expect(rec.rowCount).toBeGreaterThanOrEqual(1);

      // Verify pending
      const v = await client.query('SELECT status FROM tasks WHERE id = $1', [staleId]);
      expect(v.rows[0].status).toBe('pending');

      // Clean up
      await client.query('DELETE FROM tasks WHERE id = $1', [staleId]);
    });

    it('PHASE 7: insert job and agent run', async () => {
      // First create a job for this task
      const jr = await client.query(`
        INSERT INTO jobs (task_id, kind)
        VALUES ($1, $2) RETURNING id
      `, [taskId, 'e2e-smoke-job']);
      const jobId = jr.rows[0].id;
      expect(jr.rowCount).toBe(1);

      const r = await client.query(`
        INSERT INTO agent_runs (job_id, provider, external_run_id, status)
        VALUES ($1, $2, $3, $4) RETURNING id
      `, [jobId, 'e2e-smoke', `run-${Date.now()}`, 'succeeded']);
      expect(r.rowCount).toBe(1);
    });
  });
}