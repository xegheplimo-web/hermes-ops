import { describe, expect, it } from 'vitest';
import {
  DEFAULT_MAX_AGE_MS,
  MANIFEST_SCHEMA_VERSION,
  validateEvidenceManifest,
} from '../src/index.js';

const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
const SHA256 = 'a'.repeat(64);
const NOW = new Date('2026-08-19T12:00:00.000Z');

const iso = (offsetMs: number): string =>
  new Date(NOW.getTime() + offsetMs).toISOString();

const validManifest = () => ({
  schemaVersion: MANIFEST_SCHEMA_VERSION,
  repository: { owner: 'acme', name: 'hermes-ops' },
  prNumber: 42,
  headSha: HEAD_SHA,
  policyVersion: '0.1.0',
  timestamp: iso(0),
  artifacts: [{ path: 'reports/coverage.json', sha256: SHA256 }],
  ci: { conclusion: 'success', checks: [{ name: 'build', conclusion: 'success' }] },
  source: { kind: 'github-actions', version: '0.1.0', metadata: { run: 1 } },
  idempotencyKey: 'key-1',
});

const opts = (overrides: Partial<Parameters<typeof validateEvidenceManifest>[1]> = {}) => ({
  expectedHeadSha: HEAD_SHA,
  now: NOW,
  ...overrides,
});

describe('validateEvidenceManifest — valid', () => {
  it('accepts a well-formed v1 manifest', () => {
    const r = validateEvidenceManifest(validManifest(), opts());
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.manifest.schemaVersion).toBe(1);
      expect(r.manifest.repository).toEqual({ owner: 'acme', name: 'hermes-ops' });
      expect(r.manifest.prNumber).toBe(42);
      expect(r.manifest.headSha).toBe(HEAD_SHA);
      expect(r.manifest.artifacts).toHaveLength(1);
    }
  });

  it('accepts a manifest without optional fields', () => {
    const m = validManifest();
    delete (m as Partial<typeof m>).prNumber;
    delete (m as Partial<typeof m>).idempotencyKey;
    delete (m as Partial<typeof m>).ci;
    (m as Record<string, unknown>).ci = { conclusion: 'success' };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(true);
  });
});

describe('validateEvidenceManifest — missing required fields', () => {
  for (const field of [
    'schemaVersion',
    'repository',
    'headSha',
    'policyVersion',
    'timestamp',
    'artifacts',
    'ci',
    'source',
  ]) {
    it(`rejects missing ${field}`, () => {
      const m = validManifest() as Record<string, unknown>;
      delete m[field];
      const r = validateEvidenceManifest(m, opts());
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error.code).toBe('MISSING_REQUIRED_FIELD');
    });
  }
});

describe('validateEvidenceManifest — malformed', () => {
  it('rejects non-object input', () => {
    const r = validateEvidenceManifest('nope', opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('MALFORMED');
  });

  it('rejects unsupported schemaVersion', () => {
    const m = validManifest();
    (m as Record<string, unknown>).schemaVersion = 2;
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('SCHEMA_VERSION_UNSUPPORTED');
  });

  it('rejects invalid headSha format', () => {
    const m = validManifest();
    m.headSha = 'not-a-sha';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_HEAD_SHA');
  });

  it('rejects invalid sha256', () => {
    const m = validManifest();
    m.artifacts[0]!.sha256 = 'deadbeef';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_SHA256');
  });

  it('rejects uppercase sha256', () => {
    const m = validManifest();
    m.artifacts[0]!.sha256 = 'A'.repeat(64);
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_SHA256');
  });

  it('rejects invalid policy version', () => {
    const m = validManifest();
    m.policyVersion = 'latest';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_POLICY_VERSION');
  });

  it('rejects invalid prNumber', () => {
    const m = validManifest();
    (m as Record<string, unknown>).prNumber = -1;
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_PR_NUMBER');
  });

  it('rejects invalid repository', () => {
    const m = validManifest();
    m.repository = { owner: '', name: 'x' };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_REPOSITORY');
  });

  it('rejects invalid ci conclusion', () => {
    const m = validManifest();
    (m as Record<string, unknown>).ci = { conclusion: 'bogus' };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_CI_CONCLUSION');
  });

  it('rejects invalid source kind', () => {
    const m = validManifest();
    m.source.kind = 'unknown' as never;
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_SOURCE_ADAPTER');
  });

  it('rejects empty artifacts', () => {
    const m = validManifest();
    m.artifacts = [];
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('EMPTY_ARTIFACTS');
  });

  it('rejects duplicate artifact paths', () => {
    const m = validManifest();
    m.artifacts = [
      { path: 'reports/a.json', sha256: SHA256 },
      { path: 'reports/a.json', sha256: 'b'.repeat(64) },
    ];
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('DUPLICATE_ARTIFACT_PATH');
  });
});

describe('validateEvidenceManifest — absolute paths', () => {
  it('rejects posix absolute paths', () => {
    const m = validManifest();
    m.artifacts[0]!.path = '/etc/passwd';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('ABSOLUTE_PATH');
  });

  it('rejects windows drive paths', () => {
    const m = validManifest();
    m.artifacts[0]!.path = 'C:\\secrets\\key.txt';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('ABSOLUTE_PATH');
  });

  it('rejects path traversal', () => {
    const m = validManifest();
    m.artifacts[0]!.path = '../escape.json';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(['ABSOLUTE_PATH', 'PATH_TRAVERSAL']).toContain(r.error.code);
    }
  });
});

describe('validateEvidenceManifest — secret fields', () => {
  it('rejects a top-level secret-looking field', () => {
    const m = validManifest() as Record<string, unknown>;
    m.api_key = 'leak';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('SECRET_FIELD');
  });

  it('rejects a nested secret-looking field in source.metadata', () => {
    const m = validManifest();
    (m.source as Record<string, unknown>).metadata = { token: 'leak' };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('SECRET_FIELD');
  });

  it('rejects a secret-looking artifact sha256 key', () => {
    const m = validManifest() as Record<string, unknown>;
    const art = (m.artifacts as unknown[])[0] as Record<string, unknown>;
    art.secret = 'leak';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('SECRET_FIELD');
  });
});

describe('validateEvidenceManifest — stale timestamp', () => {
  it('rejects a timestamp older than the max age', () => {
    const m = validManifest();
    m.timestamp = iso(-(DEFAULT_MAX_AGE_MS + 1000));
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('STALE_TIMESTAMP');
  });

  it('rejects a timestamp far in the future', () => {
    const m = validManifest();
    m.timestamp = iso(60 * 60 * 1000);
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('STALE_TIMESTAMP');
  });

  it('accepts a timestamp within the freshness window', () => {
    const m = validManifest();
    m.timestamp = iso(-(DEFAULT_MAX_AGE_MS - 1000));
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(true);
  });

  it('rejects a non-ISO timestamp', () => {
    const m = validManifest();
    m.timestamp = '2026/08/19 12:00:00';
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_TIMESTAMP');
  });
});

describe('validateEvidenceManifest — head-SHA mismatch', () => {
  it('rejects when headSha does not match expected', () => {
    const m = validManifest();
    const r = validateEvidenceManifest(m, opts({ expectedHeadSha: 'f'.repeat(40) }));
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('HEAD_SHA_MISMATCH');
  });

  it('rejects an invalid expectedHeadSha option', () => {
    const r = validateEvidenceManifest(validManifest(), opts({ expectedHeadSha: 'short' }));
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_HEAD_SHA');
  });
});

describe('validateEvidenceManifest — optional blocks', () => {
  it('accepts coderabbit findings', () => {
    const m = validManifest();
    m.coderabbit = {
      findings: [{ id: 'f1', severity: 'high', resolved: true }],
    };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(true);
  });

  it('rejects malformed coderabbit findings', () => {
    const m = validManifest();
    (m as Record<string, unknown>).coderabbit = { findings: [{ id: 'f1', severity: 'critical' }] };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_CODERABBIT_FINDING');
  });

  it('accepts devin run metadata', () => {
    const m = validManifest();
    m.devin = { runId: 'r1', status: 'completed', startedAt: iso(-60000), finishedAt: iso(0) };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(true);
  });

  it('rejects malformed devin metadata', () => {
    const m = validManifest();
    (m as Record<string, unknown>).devin = { status: 'completed' };
    const r = validateEvidenceManifest(m, opts());
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('INVALID_DEVIN_METADATA');
  });
});
