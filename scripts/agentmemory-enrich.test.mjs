#!/usr/bin/env node
/**
 * Offline tests for scripts/agentmemory-enrich.mjs — HERMES-002
 *
 * Uses node:test + node:assert. No external deps. No real HTTP — `fetch` is
 * mocked via an injectable IO surface. Run with:
 *
 *   node scripts/agentmemory-enrich.test.mjs
 *
 * or via the repo npm script:
 *
 *   pnpm test:agentmemory
 */

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

import { runEnrich } from './agentmemory-enrich.mjs';

// ── Fixtures ────────────────────────────────────────────────────────────────

const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
const POLICY_VERSION = '0.1.0';
const NOW_ISO = '2026-08-29T12:00:00Z';

const validManifest = (overrides = {}) => ({
  schemaVersion: 1,
  repository: { owner: 'hermes-ops', name: 'hermes-ops' },
  prNumber: 42,
  headSha: HEAD_SHA,
  policyVersion: POLICY_VERSION,
  timestamp: NOW_ISO,
  artifacts: [
    { path: 'e2e/smoke.mjs', sha256: 'a'.repeat(64) },
    { path: 'packages/contracts/src/index.ts', sha256: 'b'.repeat(64) },
  ],
  ci: { conclusion: 'success' },
  source: { kind: 'github-actions', version: '1.0.0' },
  ...overrides,
});

const passGateResult = (overrides = {}) => ({
  decision: 'pass',
  gate: 'PASS',
  reasonCode: 'PASS',
  riskLevel: 'LOW',
  requiredGates: ['ci'],
  policyVersion: POLICY_VERSION,
  detail: 'all gates satisfied',
  ...overrides,
});

/** Compute the evidence identity the same way the script does. */
const computeIdentity = (manifest) => {
  const canon = JSON.stringify(manifest, (_k, v) => {
    if (v === undefined) return undefined;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      const sorted = {};
      for (const k of Object.keys(v).sort()) {
        if (v[k] !== undefined) sorted[k] = v[k];
      }
      return sorted;
    }
    return v;
  });
  return createHash('sha256').update(canon, 'utf8').digest('hex');
};

// ── Test harness ────────────────────────────────────────────────────────────

const makeIo = (opts = {}) => {
  const stdoutChunks = [];
  const stderrChunks = [];
  const calls = [];
  const fetchMock = opts.fetch ?? (() => { throw new Error('fetch called unexpectedly'); });
  return {
    io: {
      stdout: { write(s) { stdoutChunks.push(s); } },
      stderr: { write(s) { stderrChunks.push(s); } },
      readFileSync: (p, enc) => opts.readFileSync ? opts.readFileSync(p, enc) : defaultReadFileSync(p, enc),
      statSync: (p) => opts.statSync ? opts.statSync(p) : defaultStatSync(p),
      fetch: fetchMock,
    },
    stdout: () => stdoutChunks.join(''),
    stderr: () => stderrChunks.join(''),
    calls,
  };
};

import { readFileSync as defaultReadFileSync, statSync as defaultStatSync } from 'node:fs';

const writeFixture = (dir, name, obj) => {
  const path = join(dir, name);
  writeFileSync(path, JSON.stringify(obj), 'utf8');
  return path;
};

const makeFetch = (responses = {}) => {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body });
    const key = init?.method ?? 'GET';
    const handler = responses[key];
    if (!handler) throw new Error(`unexpected fetch ${key} to ${url}`);
    return handler(String(url), init, calls);
  };
  fn.calls = calls;
  return fn;
};

const okJson = (body) => ({
  ok: true,
  status: 200,
  json: async () => body,
});

const notFound = () => ({
  ok: false,
  status: 404,
  json: async () => ({}),
});

// ── Tests ───────────────────────────────────────────────────────────────────

test('dry-run: prints lesson payload, no HTTP, exit 0', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch(); // no handlers → throws if called
    const { io, stdout, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--dry-run'],
      io,
    );

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'dry-run');
    assert.equal(fetchFn.calls.length, 0, 'fetch must not be called in dry-run');
    const out = stdout();
    assert.match(out, /"kind": "verified-lesson"/);
    assert.match(out, /"gate": "PASS"/);
    assert.match(out, /dry-run: no HTTP request sent/);
    assert.equal(stderr(), '');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('enriched: GET returns empty, POST called, exit 0', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifest = validManifest();
    const identity = computeIdentity(manifest);
    const manifestPath = writeFixture(dir, 'manifest.json', manifest);
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());

    const fetchFn = makeFetch({
      GET: (url) => {
        assert.match(url, /\/agentmemory\/memories\?evidence_identity=/);
        return okJson({ memories: [] });
      },
      POST: (url, init) => {
        assert.match(url, /\/agentmemory\/memories$/);
        const body = JSON.parse(init.body);
        assert.equal(body.evidence_identity, identity);
        assert.equal(body.kind, 'verified-lesson');
        assert.equal(body.gate, 'PASS');
        return okJson({ id: 'mem-1' });
      },
    });
    const { io, stdout, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'enriched');
    assert.equal(fetchFn.calls.length, 2, 'GET + POST');
    assert.equal(fetchFn.calls[0].method, 'GET');
    assert.equal(fetchFn.calls[1].method, 'POST');
    assert.match(stdout(), /enriched: posted verified lesson/);
    assert.equal(stderr(), '');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('idempotent skip: memory already exists, no POST, exit 0', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifest = validManifest();
    const identity = computeIdentity(manifest);
    const manifestPath = writeFixture(dir, 'manifest.json', manifest);
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());

    const fetchFn = makeFetch({
      GET: () => okJson({ memories: [{ evidence_identity: identity }] }),
      POST: () => { throw new Error('POST must not be called on idempotent skip'); },
    });
    const { io, stdout, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'skipped');
    assert.equal(fetchFn.calls.length, 1, 'only GET, no POST');
    assert.match(stdout(), /skipped: memory already exists/);
    assert.equal(stderr(), '');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('idempotent skip: GET 404 treated as no existing memory', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());

    const fetchFn = makeFetch({
      GET: () => notFound(),
      POST: () => okJson({ id: 'mem-2' }),
    });
    const { io } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'enriched');
    assert.equal(fetchFn.calls.length, 2);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('gate not PASS: skip, exit 0, no HTTP', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gatePath = writeFixture(dir, 'gate.json', passGateResult({ gate: 'REPAIR', decision: 'fail' }));
    const fetchFn = makeFetch(); // no handlers
    const { io, stdout, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'skipped');
    assert.equal(fetchFn.calls.length, 0);
    assert.match(stdout(), /skipped: gate is REPAIR/);
    assert.equal(stderr(), '');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('validation fail: malformed manifest, exit 1', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', { not: 'a manifest' });
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch();
    const { io, stdout, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 1);
    assert.equal(fetchFn.calls.length, 0);
    assert.match(stderr(), /manifest validation failed/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('validation fail: secret-looking field in manifest, exit 1, no HTTP', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifest = validManifest();
    manifest.artifacts[0].api_key = 'should-not-be-here';
    const manifestPath = writeFixture(dir, 'manifest.json', manifest);
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch();
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 1);
    assert.equal(fetchFn.calls.length, 0);
    assert.match(stderr(), /secret/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('validation fail: secret-looking field in gate result, exit 1', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gate = passGateResult();
    gate.token = 'leaked';
    const gatePath = writeFixture(dir, 'gate.json', gate);
    const fetchFn = makeFetch();
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 1);
    assert.equal(fetchFn.calls.length, 0);
    assert.match(stderr(), /gate-result validation failed/);
    assert.match(stderr(), /secret/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('usage error: missing --manifest, exit 2', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch();
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(['--gate-result', gatePath], io);

    assert.equal(result.exitCode, 2);
    assert.match(stderr(), /--manifest is required/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('usage error: missing --gate-result, exit 2', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const fetchFn = makeFetch();
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(['--manifest', manifestPath], io);

    assert.equal(result.exitCode, 2);
    assert.match(stderr(), /--gate-result is required/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('usage error: unknown flag, exit 2', async () => {
  const fetchFn = makeFetch();
  const { io, stderr } = makeIo({ fetch: fetchFn });

  const result = await runEnrich(['--bogus', 'x'], io);

  assert.equal(result.exitCode, 2);
  assert.match(stderr(), /unknown argument/);
});

test('usage error: --base-url required when not dry-run and no env, exit 2', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch();
    const { io, stderr } = makeIo({ fetch: fetchFn });

    // Ensure env doesn't leak into the test.
    const saved = process.env.AGENTMEMORY_BASE_URL;
    delete process.env.AGENTMEMORY_BASE_URL;
    try {
      const result = await runEnrich(
        ['--manifest', manifestPath, '--gate-result', gatePath],
        io,
      );
      assert.equal(result.exitCode, 2);
      assert.match(stderr(), /--base-url is required/);
    } finally {
      if (saved !== undefined) process.env.AGENTMEMORY_BASE_URL = saved;
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('operational error: unreadable manifest file, exit 2', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch();
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', join(dir, 'does-not-exist.json'), '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 2);
    assert.match(stderr(), /cannot read manifest file/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('operational error: invalid JSON manifest, exit 2', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = join(dir, 'bad.json');
    writeFileSync(manifestPath, '{ not valid json', 'utf8');
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch();
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 2);
    assert.match(stderr(), /not valid JSON/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('transport error: GET fails, exit 1', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch({
      GET: () => { throw new Error('connection refused'); },
    });
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 1);
    assert.match(stderr(), /connection refused/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('transport error: POST returns non-ok, exit 1', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch({
      GET: () => okJson({ memories: [] }),
      POST: () => ({ ok: false, status: 500, json: async () => ({}) }),
    });
    const { io, stderr } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080'],
      io,
    );

    assert.equal(result.exitCode, 1);
    assert.match(stderr(), /AgentMemory POST failed: HTTP 500/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('lesson payload includes redacted gate fields only', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gate = passGateResult();
    gate.detail = 'sensitive internal detail that should not leak';
    const gatePath = writeFixture(dir, 'gate.json', gate);
    const fetchFn = makeFetch(); // dry-run, no fetch
    const { io, stdout } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--dry-run'],
      io,
    );

    assert.equal(result.exitCode, 0);
    const out = stdout();
    const payload = JSON.parse(out.split('\ndry-run:')[0]);
    assert.equal(payload.gate, 'PASS');
    assert.equal(payload.risk_level, 'LOW');
    assert.equal(payload.reason_code, 'PASS');
    assert.equal(payload.policy_version, POLICY_VERSION);
    assert.equal(payload.detail, undefined, 'detail must not be in the lesson');
    assert.equal(payload.requiredGates, undefined);
    assert.equal(payload.kind, 'verified-lesson');
    assert.equal(payload.repository.owner, 'hermes-ops');
    assert.equal(payload.head_sha, HEAD_SHA);
    assert.equal(payload.pr_number, 42);
    assert.ok(payload.evidence_identity.length === 64, 'evidence_identity is 64 hex');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('evidence_identity is deterministic and matches canonical serialization', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifest = validManifest();
    const expected = computeIdentity(manifest);
    const manifestPath = writeFixture(dir, 'manifest.json', manifest);
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch(); // dry-run
    const { io, stdout } = makeIo({ fetch: fetchFn });

    await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--dry-run'],
      io,
    );

    const payload = JSON.parse(stdout().split('\ndry-run:')[0]);
    assert.equal(payload.evidence_identity, expected);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('--help prints usage, exit 0', async () => {
  const fetchFn = makeFetch();
  const { io, stdout } = makeIo({ fetch: fetchFn });

  const result = await runEnrich(['--help'], io);

  assert.equal(result.exitCode, 0);
  assert.match(stdout(), /usage: agentmemory-enrich/);
});

test('base URL trailing slash is normalized', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'am-enrich-'));
  try {
    const manifestPath = writeFixture(dir, 'manifest.json', validManifest());
    const gatePath = writeFixture(dir, 'gate.json', passGateResult());
    const fetchFn = makeFetch({
      GET: (url) => {
        assert.match(url, /^http:\/\/localhost:8080\/agentmemory\/memories\?/);
        return okJson({ memories: [] });
      },
      POST: (url) => {
        assert.equal(url, 'http://localhost:8080/agentmemory/memories');
        return okJson({ id: 'mem-3' });
      },
    });
    const { io } = makeIo({ fetch: fetchFn });

    const result = await runEnrich(
      ['--manifest', manifestPath, '--gate-result', gatePath, '--base-url', 'http://localhost:8080/'],
      io,
    );

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'enriched');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
