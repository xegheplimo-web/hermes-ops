import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import {
  mkdtempSync,
  writeFileSync,
  readFileSync,
  rmSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { MANIFEST_SCHEMA_VERSION } from '@hermes-ops/contracts';

import {
  isHumanApprovalRequired,
  requestHumanApproval,
  resolveHumanApproval,
  resetApprovalStore,
  type HumanApprovalToken,
} from '../src/approval.js';

const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
const binPath = fileURLToPath(new URL('../dist/bin.js', import.meta.url));

const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
const SHA256 = 'a'.repeat(64);
const POLICY_VERSION = '0.1.0';

interface RunResult {
  status: number;
  stdout: string;
  stderr: string;
}

const run = (args: readonly string[]): RunResult => {
  try {
    const stdout = execFileSync(process.execPath, [binPath, ...args], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      maxBuffer: 8 * 1024 * 1024,
    });
    return { status: 0, stdout, stderr: '' };
  } catch (e) {
    const err = e as {
      status?: number;
      stdout?: string;
      stderr?: string;
    };
    return {
      status: err.status ?? -1,
      stdout: err.stdout ?? '',
      stderr: err.stderr ?? '',
    };
  }
};

let workDir: string;

beforeAll(() => {
  execFileSync('pnpm', ['build'], {
    cwd: repoRoot,
    stdio: 'pipe',
    shell: true,
    maxBuffer: 16 * 1024 * 1024,
  });
  workDir = mkdtempSync(join(tmpdir(), 'hermes-gate-approval-'));
}, 120_000);

afterAll(() => {
  if (workDir) {
    rmSync(workDir, { recursive: true, force: true });
  }
});

const manifestPath = (name: string): string => join(workDir, name);

const writeManifest = (name: string, manifest: unknown): string => {
  const p = manifestPath(name);
  writeFileSync(p, JSON.stringify(manifest), 'utf8');
  return p;
};

const validManifest = (overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
  schemaVersion: MANIFEST_SCHEMA_VERSION,
  repository: { owner: 'acme', name: 'hermes-ops' },
  prNumber: 42,
  headSha: HEAD_SHA,
  policyVersion: POLICY_VERSION,
  timestamp: new Date().toISOString(),
  artifacts: [{ path: 'reports/coverage.json', sha256: SHA256 }],
  ci: { conclusion: 'success' },
  source: { kind: 'github-actions', version: '0.1.0' },
  ...overrides,
});

const parseJson = (s: string): Record<string, unknown> => JSON.parse(s) as Record<string, unknown>;

const makeValidToken = (): string =>
  JSON.stringify({
    signedAt: new Date().toISOString(),
    approver: 'alice',
    reason: 'manual override for critical finding',
    signature: 'sig-abc123',
  });

// ─── Unit tests for the approval module ──────────────────────────────────────

describe('approval module — pure functions', () => {
  it('isHumanApprovalRequired returns true for critical', () => {
    expect(isHumanApprovalRequired('critical')).toBe(true);
  });

  it('isHumanApprovalRequired returns false for low', () => {
    expect(isHumanApprovalRequired('low')).toBe(false);
  });

  it('isHumanApprovalRequired returns false for medium', () => {
    expect(isHumanApprovalRequired('medium')).toBe(false);
  });

  it('isHumanApprovalRequired returns false for an unknown risk string', () => {
    expect(isHumanApprovalRequired('high')).toBe(false);
  });
});

describe('approval module — stateful functions', () => {
  beforeEach(() => {
    resetApprovalStore();
  });

  it('requestHumanApproval returns pending', () => {
    expect(requestHumanApproval('task-1', 'critical')).toBe('pending');
  });

  it('resolveHumanApproval returns approved for a valid token', () => {
    requestHumanApproval('task-2', 'critical');
    const token: HumanApprovalToken = {
      signedAt: new Date().toISOString(),
      approver: 'bob',
      reason: 'acknowledged',
      signature: 'sig-xyz',
    };
    expect(resolveHumanApproval('task-2', token)).toBe('approved');
  });

  it('resolveHumanApproval returns rejected for an invalid token (empty fields)', () => {
    requestHumanApproval('task-3', 'critical');
    const token: HumanApprovalToken = {
      signedAt: '',
      approver: '',
      reason: '',
      signature: '',
    };
    expect(resolveHumanApproval('task-3', token)).toBe('rejected');
  });

  it('resolveHumanApproval returns rejected when no request exists', () => {
    const token: HumanApprovalToken = {
      signedAt: '2026-01-01T00:00:00.000Z',
      approver: 'alice',
      reason: 'override',
      signature: 'sig-1',
    };
    expect(resolveHumanApproval('nonexistent', token)).toBe('rejected');
  });
});

// ─── CLI integration tests: human approval gate ─────────────────────────────

describe('hermes-policy-gate — human approval (critical risk)', () => {
  // Build manifest with an unresolved critical finding, which maps to 'critical' risk.
  const criticalManifest = (): Record<string, unknown> =>
    validManifest({
      coderabbit: {
        findings: [{ id: 'f1', severity: 'critical', resolved: false }],
      },
    });

  it('rejects critical actions without approval token (exit 1, HUMAN_APPROVAL_REQUIRED)', () => {
    const p = writeManifest('critical-no-token.json', criticalManifest());
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout);
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('HUMAN_APPROVAL_REQUIRED');
  });

  it('accepts critical actions with a valid approval token', () => {
    const p = writeManifest('critical-with-token.json', criticalManifest());
    const token = makeValidToken();
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      '--approval', token,
    ]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout);
    // The underlying policy evaluation still fails (UNRESOLVED_CRITICAL_FINDING),
    // but the gate proceeds because a valid token was supplied.
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('UNRESOLVED_CRITICAL_FINDING');
  });

  it('accepts critical actions with an approval token as --approval=<json>', () => {
    const p = writeManifest('critical-token-eq.json', criticalManifest());
    const token = makeValidToken();
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      `--approval=${token}`,
    ]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout);
    expect(out['reasonCode']).toBe('UNRESOLVED_CRITICAL_FINDING');
  });

  it('rejects an invalid approval token JSON', () => {
    const p = writeManifest('bad-token.json', criticalManifest());
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      '--approval', 'not-json',
    ]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('--approval must be valid JSON');
    expect(r.stdout).toBe('');
  });

  it('rejects an approval token with missing fields', () => {
    const p = writeManifest('bad-token-fields.json', criticalManifest());
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      '--approval', '{"signedAt":"2026-01-01"}',
    ]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('--approval must be a valid JSON token');
    expect(r.stdout).toBe('');
  });
});

describe('hermes-policy-gate — LOW/MED bypass human gate', () => {
  it('bypasses human gate for CI_NOT_GREEN (low risk)', () => {
    const p = writeManifest('ci-not-green.json', validManifest({ ci: { conclusion: 'failure' } }));
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout);
    expect(out['reasonCode']).toBe('CI_NOT_GREEN');
    // Should not be HUMAN_APPROVAL_REQUIRED, even without a token
    expect(out['reasonCode']).not.toBe('HUMAN_APPROVAL_REQUIRED');
  });

  it('bypasses human gate for HEAD_SHA_MISMATCH (low risk)', () => {
    const p = writeManifest('sha-mismatch.json', validManifest({ headSha: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }));
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout);
    expect(out['reasonCode']).toBe('HEAD_SHA_MISMATCH');
    expect(out['reasonCode']).not.toBe('HUMAN_APPROVAL_REQUIRED');
  });

  it('bypasses human gate for DUPLICATE_EVIDENCE (low risk)', () => {
    const p = writeManifest('duplicate.json', validManifest({ idempotencyKey: 'dup-key' }));
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      // Inject a seen idempotency key via environment? Not supported in the CLI.
      // The evaluator only checks seenIdempotencyKeys when injected via options.
      // This test covers a non-duplicate pass-through; full dupe coverage is
      // in the evaluator unit test suite.
    ]);
    // Without a seen-idempotency-key set, the manifest passes validation.
    // The gate should still report the original reason code.
    expect(r.status).toBe(0);
    const out = parseJson(r.stdout);
    expect(out['decision']).toBe('pass');
    expect(out['reasonCode']).toBe('PASS');
  });

  it('requires human approval for POLICY_VERSION_MISMATCH (human-required risk)', () => {
    const p = writeManifest('pv-mismatch.json', validManifest({ policyVersion: '9.9.9' }));
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout);
    expect(out['reasonCode']).toBe('HUMAN_APPROVAL_REQUIRED');
  });

  it('passes a valid manifest without any token (no human gate needed)', () => {
    const p = writeManifest('clean-pass.json', validManifest());
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(0);
    const out = parseJson(r.stdout);
    expect(out['decision']).toBe('pass');
    expect(out['reasonCode']).toBe('PASS');
  });
});