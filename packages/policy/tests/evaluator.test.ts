import { describe, expect, it } from 'vitest';
import { evaluatePolicy } from '../src/index.js';
import { MANIFEST_SCHEMA_VERSION } from '@hermes-ops/contracts';

const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
const SHA256 = 'a'.repeat(64);
const NOW = new Date('2026-08-19T12:00:00.000Z');
const POLICY_VERSION = '0.1.0';

const iso = (offsetMs: number): string =>
  new Date(NOW.getTime() + offsetMs).toISOString();

const validManifest = () => ({
  schemaVersion: MANIFEST_SCHEMA_VERSION,
  repository: { owner: 'acme', name: 'hermes-ops' },
  prNumber: 42,
  headSha: HEAD_SHA,
  policyVersion: POLICY_VERSION,
  timestamp: iso(0),
  artifacts: [{ path: 'reports/coverage.json', sha256: SHA256 }],
  ci: { conclusion: 'success' },
  source: { kind: 'github-actions', version: '0.1.0' },
  idempotencyKey: 'key-1',
});

const opts = (overrides: Partial<Parameters<typeof evaluatePolicy>[1]> = {}) => ({
  expectedHeadSha: HEAD_SHA,
  policyVersion: POLICY_VERSION,
  now: NOW,
  ...overrides,
});

describe('evaluatePolicy — pass', () => {
  it('passes on valid, fresh, green, sha-matching evidence', () => {
    const r = evaluatePolicy(validManifest(), opts());
    expect(r.decision).toBe('pass');
    expect(r.reasonCode).toBe('PASS');
    expect(r.policyVersion).toBe(POLICY_VERSION);
    expect(r.evidenceIdentity).toMatch(/^[0-9a-f]{64}$/);
    expect(r.manifest).toBeDefined();
  });

  it('is deterministic: same input yields same identity', () => {
    const a = evaluatePolicy(validManifest(), opts());
    const b = evaluatePolicy(validManifest(), opts());
    expect(a.evidenceIdentity).toBe(b.evidenceIdentity);
    expect(a.decision).toBe(b.decision);
  });

  it('passes with neutral/skipped checks under a success rollup', () => {
    const m = validManifest();
    m.ci = {
      conclusion: 'success',
      checks: [
        { name: 'build', conclusion: 'success' },
        { name: 'lint', conclusion: 'neutral' },
        { name: 'docs', conclusion: 'skipped' },
      ],
    };
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('pass');
  });
});

describe('evaluatePolicy — missing / malformed', () => {
  it('fails closed on non-object input', () => {
    const r = evaluatePolicy(null, opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('EVIDENCE_INVALID');
  });

  it('fails closed on a missing required field', () => {
    const m = validManifest() as Record<string, unknown>;
    delete m.ci;
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('EVIDENCE_INVALID');
  });

  it('fails closed on an invalid sha256', () => {
    const m = validManifest();
    m.artifacts[0]!.sha256 = 'bad';
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('EVIDENCE_INVALID');
  });
});

describe('evaluatePolicy — stale', () => {
  it('fails with EVIDENCE_STALE on an old timestamp', () => {
    const m = validManifest();
    m.timestamp = iso(-(25 * 60 * 60 * 1000));
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('EVIDENCE_STALE');
  });
});

describe('evaluatePolicy — head-SHA mismatch', () => {
  it('fails with HEAD_SHA_MISMATCH when headSha differs from expected', () => {
    const m = validManifest();
    m.headSha = 'f'.repeat(40);
    const r = evaluatePolicy(m, opts({ expectedHeadSha: HEAD_SHA }));
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('HEAD_SHA_MISMATCH');
  });
});

describe('evaluatePolicy — duplicate / idempotency key', () => {
  it('fails with DUPLICATE_EVIDENCE when idempotency key was already seen', () => {
    const seen = new Set<string>(['key-1']);
    const r = evaluatePolicy(validManifest(), opts({ seenIdempotencyKeys: seen }));
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('DUPLICATE_EVIDENCE');
  });

  it('passes when idempotency key is present but not yet seen', () => {
    const r = evaluatePolicy(validManifest(), opts({ seenIdempotencyKeys: new Set<string>() }));
    expect(r.decision).toBe('pass');
  });

  it('passes when no idempotency key is present even if seen set is provided', () => {
    const m = validManifest();
    delete (m as Partial<typeof m>).idempotencyKey;
    const r = evaluatePolicy(m, opts({ seenIdempotencyKeys: new Set<string>() }));
    expect(r.decision).toBe('pass');
  });
});

describe('evaluatePolicy — CI not green', () => {
  it('fails with CI_NOT_GREEN on a failure rollup', () => {
    const m = validManifest();
    (m as Record<string, unknown>).ci = { conclusion: 'failure' };
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('CI_NOT_GREEN');
  });

  it('fails with CI_NOT_GREEN when a check failed under a success rollup', () => {
    const m = validManifest();
    m.ci = {
      conclusion: 'success',
      checks: [{ name: 'build', conclusion: 'failure' }],
    };
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('CI_NOT_GREEN');
  });
});

describe('evaluatePolicy — unresolved critical CodeRabbit finding', () => {
  it('fails with UNRESOLVED_CRITICAL_FINDING on an unresolved critical', () => {
    const m = validManifest();
    m.coderabbit = {
      findings: [{ id: 'f1', severity: 'critical', resolved: false }],
    };
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('UNRESOLVED_CRITICAL_FINDING');
  });

  it('passes when a critical finding is resolved', () => {
    const m = validManifest();
    m.coderabbit = {
      findings: [{ id: 'f1', severity: 'critical', resolved: true }],
    };
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('pass');
  });

  it('passes when only non-critical findings are unresolved', () => {
    const m = validManifest();
    m.coderabbit = {
      findings: [{ id: 'f1', severity: 'high', resolved: false }],
    };
    const r = evaluatePolicy(m, opts());
    expect(r.decision).toBe('pass');
  });
});

describe('evaluatePolicy — policy version mismatch', () => {
  it('fails with POLICY_VERSION_MISMATCH', () => {
    const r = evaluatePolicy(validManifest(), opts({ policyVersion: '0.2.0' }));
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('POLICY_VERSION_MISMATCH');
  });
});

describe('evaluatePolicy — fail-closed ordering', () => {
  it('validation failures take precedence over downstream checks', () => {
    // Invalid (malformed) input that would also be stale/mismatched must report
    // EVIDENCE_INVALID, not a more specific downstream code.
    const r = evaluatePolicy('garbage', opts());
    expect(r.decision).toBe('fail');
    expect(r.reasonCode).toBe('EVIDENCE_INVALID');
    expect(r.evidenceIdentity).toBeUndefined();
  });

  it('policy version is checked before CI green', () => {
    const m = validManifest();
    (m as Record<string, unknown>).ci = { conclusion: 'failure' };
    const r = evaluatePolicy(m, opts({ policyVersion: '9.9.9' }));
    expect(r.reasonCode).toBe('POLICY_VERSION_MISMATCH');
  });

  it('duplicate is checked before CI green', () => {
    const m = validManifest();
    (m as Record<string, unknown>).ci = { conclusion: 'failure' };
    const r = evaluatePolicy(
      m,
      opts({ seenIdempotencyKeys: new Set(['key-1']) }),
    );
    expect(r.reasonCode).toBe('DUPLICATE_EVIDENCE');
  });
});
