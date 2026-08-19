import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import {
  mkdtempSync,
  writeFileSync,
  readFileSync,
  rmSync,
  statSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { MANIFEST_SCHEMA_VERSION } from '@hermes-ops/contracts';

const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
const binPath = fileURLToPath(new URL('../dist/bin.js', import.meta.url));

const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
const OTHER_SHA = 'fedcba9876543210fedcba9876543210fedcba98';
const SHA256 = 'a'.repeat(64);
const POLICY_VERSION = '0.1.0';
const OTHER_POLICY_VERSION = '0.2.0';

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
  // Build the workspace so the gate bin and its dependency dists exist for
  // subprocess execution. `tsc -b` is incremental and idempotent.
  execFileSync('pnpm', ['build'], {
    cwd: repoRoot,
    stdio: 'pipe',
    shell: true,
    maxBuffer: 16 * 1024 * 1024,
  });
  workDir = mkdtempSync(join(tmpdir(), 'hermes-gate-'));
}, 120_000);

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

const parseJson = (s: string): unknown => JSON.parse(s);

describe('hermes-policy-gate — pass', () => {
  it('exits 0 and emits a pass result with evidenceIdentity', () => {
    const p = writeManifest('pass.json', validManifest());
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(0);
    expect(r.stderr).toBe('');
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('pass');
    expect(out['reasonCode']).toBe('PASS');
    expect(out['policyVersion']).toBe(POLICY_VERSION);
    expect(out['evidenceIdentity']).toMatch(/^[0-9a-f]{64}$/);
    expect(out['detail']).toBe('evidence satisfies policy');
  });

  it('does not include manifest or source content in the output', () => {
    const p = writeManifest('pass-no-leak.json', validManifest());
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(0);
    expect(r.stdout).not.toContain('artifacts');
    expect(r.stdout).not.toContain('repository');
    expect(r.stdout).not.toContain('source');
    expect(r.stdout).not.toContain('coderabbit');
    expect(r.stdout).not.toContain('github-actions');
  });
});

describe('hermes-policy-gate — policy failures (exit 1)', () => {
  it('fails on stale timestamp with EVIDENCE_STALE', () => {
    const stale = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString();
    const p = writeManifest('stale.json', validManifest({ timestamp: stale }));
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('EVIDENCE_STALE');
    expect(out['evidenceIdentity']).toBeUndefined();
    expect(out['policyVersion']).toBe(POLICY_VERSION);
  });

  it('fails on head-SHA mismatch with HEAD_SHA_MISMATCH', () => {
    const p = writeManifest('mismatch.json', validManifest({ headSha: OTHER_SHA }));
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('HEAD_SHA_MISMATCH');
  });

  it('fails on CI failure with CI_NOT_GREEN', () => {
    const p = writeManifest('ci-fail.json', validManifest({ ci: { conclusion: 'failure' } }));
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('CI_NOT_GREEN');
  });

  it('fails on unresolved critical finding with UNRESOLVED_CRITICAL_FINDING', () => {
    const p = writeManifest(
      'finding.json',
      validManifest({
        coderabbit: {
          findings: [{ id: 'f1', severity: 'critical', resolved: false }],
        },
      }),
    );
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('UNRESOLVED_CRITICAL_FINDING');
  });

  it('fails on policy version mismatch with POLICY_VERSION_MISMATCH', () => {
    const p = writeManifest('pv-mismatch.json', validManifest({ policyVersion: OTHER_POLICY_VERSION }));
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('POLICY_VERSION_MISMATCH');
    expect(out['policyVersion']).toBe(POLICY_VERSION);
  });

  it('fails on structurally malformed manifest (valid JSON, bad shape) with EVIDENCE_INVALID', () => {
    const p = writeManifest('malformed-shape.json', { schemaVersion: MANIFEST_SCHEMA_VERSION, headSha: HEAD_SHA });
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('EVIDENCE_INVALID');
  });
});

describe('hermes-policy-gate — usage / operational errors (exit 2)', () => {
  it('exits 2 with a stderr message and no stdout on missing --manifest', () => {
    const r = run(['--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(2);
    expect(r.stdout).toBe('');
    expect(r.stderr).toContain('usage');
    expect(r.stderr).toContain('--manifest is required');
  });

  it('exits 2 on missing --head-sha', () => {
    const p = writeManifest('no-head.json', validManifest());
    const r = run(['--manifest', p, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('--head-sha is required');
  });

  it('exits 2 on missing --policy-version', () => {
    const p = writeManifest('no-pv.json', validManifest());
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('--policy-version is required');
  });

  it('exits 2 on unknown argument', () => {
    const p = writeManifest('unknown.json', validManifest());
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      '--bogus', 'x',
    ]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('unknown argument');
  });

  it('exits 2 on a positional argument', () => {
    const p = writeManifest('positional.json', validManifest());
    const r = run([p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('positional');
  });

  it('exits 2 on a flag without a value', () => {
    const r = run(['--manifest', '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('missing value for --manifest');
  });

  it('exits 2 on an invalid --head-sha format', () => {
    const p = writeManifest('bad-sha.json', validManifest());
    const r = run(['--manifest', p, '--head-sha', 'not-a-sha', '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('--head-sha');
  });

  it('exits 2 on an invalid --policy-version format', () => {
    const p = writeManifest('bad-pv.json', validManifest());
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', 'not-semver']);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('--policy-version');
  });

  it('exits 2 when the manifest file does not exist', () => {
    const r = run([
      '--manifest', manifestPath('does-not-exist.json'),
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
    ]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('cannot read manifest');
  });

  it('exits 2 when the manifest path is a directory', () => {
    const r = run([
      '--manifest', workDir,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
    ]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('not a regular file');
  });

  it('exits 2 on invalid JSON', () => {
    const p = manifestPath('bad-json.json');
    writeFileSync(p, '{ not valid json ,,,', 'utf8');
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('not valid JSON');
  });

  it('never prints raw manifest content on invalid JSON', () => {
    const p = manifestPath('secret-bad.json');
    writeFileSync(p, '{"api_key":"supersecret", broken}', 'utf8');
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(2);
    expect(r.stderr).not.toContain('supersecret');
    expect(r.stderr).not.toContain('api_key');
  });

  it('rejects a secret-looking manifest field as invalid evidence without leaking it', () => {
    const p = manifestPath('secret-field.json');
    writeFileSync(p, '{"api_key":"supersecret"}', 'utf8');
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(1);
    expect(r.stdout).not.toContain('supersecret');
    expect(r.stdout).not.toContain('api_key');
    expect(r.stderr).not.toContain('supersecret');
    expect(r.stderr).not.toContain('api_key');
    const out = parseJson(r.stdout) as Record<string, unknown>;
    expect(out['decision']).toBe('fail');
    expect(out['reasonCode']).toBe('EVIDENCE_INVALID');
  });

  it('exits 2 when --output targets a directory', () => {
    const p = writeManifest('out-dir.json', validManifest());
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      '--output', workDir,
    ]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain('output path is a directory');
  });
});

describe('hermes-policy-gate --output', () => {
  it('writes the result JSON to the output file instead of stdout', () => {
    const p = writeManifest('out-pass.json', validManifest());
    const out = manifestPath('result.json');
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      '--output', out,
    ]);
    expect(r.status).toBe(0);
    expect(r.stdout).toBe('');
    const file = readFileSync(out, 'utf8');
    const parsed = parseJson(file) as Record<string, unknown>;
    expect(parsed['decision']).toBe('pass');
    expect(parsed['reasonCode']).toBe('PASS');
  });

  it('writes a stable result file on policy failure too (exit 1)', () => {
    const p = writeManifest('out-fail.json', validManifest({ ci: { conclusion: 'failure' } }));
    const out = manifestPath('result-fail.json');
    const r = run([
      '--manifest', p,
      '--head-sha', HEAD_SHA,
      '--policy-version', POLICY_VERSION,
      '--output', out,
    ]);
    expect(r.status).toBe(1);
    expect(statSync(out).isFile()).toBe(true);
    const parsed = parseJson(readFileSync(out, 'utf8')) as Record<string, unknown>;
    expect(parsed['decision']).toBe('fail');
    expect(parsed['reasonCode']).toBe('CI_NOT_GREEN');
  });
});

describe('hermes-policy-gate — stable output', () => {
  it('produces byte-identical stdout across repeated pass runs', () => {
    const p = writeManifest('stable.json', validManifest());
    const a = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    const b = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(a.status).toBe(0);
    expect(b.status).toBe(0);
    expect(a.stdout).toBe(b.stdout);
  });

  it('produces byte-identical stdout across repeated fail runs', () => {
    const p = writeManifest('stable-fail.json', validManifest({ ci: { conclusion: 'failure' } }));
    const a = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    const b = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(a.status).toBe(1);
    expect(b.status).toBe(1);
    expect(a.stdout).toBe(b.stdout);
  });

  it('emits keys in a fixed order: decision, reasonCode, policyVersion, evidenceIdentity, detail', () => {
    const p = writeManifest('order.json', validManifest());
    const r = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(r.status).toBe(0);
    const keys = Object.keys(parseJson(r.stdout) as Record<string, unknown>);
    expect(keys).toEqual([
      'decision',
      'reasonCode',
      'policyVersion',
      'evidenceIdentity',
      'detail',
    ]);
  });
});

describe('hermes-policy-gate — exit code summary', () => {
  it('exit 0 for pass, 1 for fail, 2 for usage', () => {
    const p = writeManifest('summary-pass.json', validManifest());
    const pass = run(['--manifest', p, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(pass.status).toBe(0);

    const failP = writeManifest('summary-fail.json', validManifest({ ci: { conclusion: 'failure' } }));
    const fail = run(['--manifest', failP, '--head-sha', HEAD_SHA, '--policy-version', POLICY_VERSION]);
    expect(fail.status).toBe(1);

    const usage = run(['--head-sha', HEAD_SHA]);
    expect(usage.status).toBe(2);
  });
});

// Clean up the temp dir after the suite. Vitest runs this after all tests.
afterAll(() => {
  if (workDir) {
    rmSync(workDir, { recursive: true, force: true });
  }
});
