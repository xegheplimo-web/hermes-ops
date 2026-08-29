#!/usr/bin/env node
/**
 * AgentMemory Enrichment — HERMES-002
 *
 * Reads an evidence manifest + a policy-gate result JSON, and sends a verified
 * lesson to the AgentMemory HTTP API (POST /agentmemory/memories). Idempotent by
 * evidence_identity: if a memory already exists for the same identity, the
 * script skips (exit 0) without re-posting.
 *
 * No external dependencies — Node built-ins only (crypto, fs, path). Uses the
 * global `fetch` (Node 18+; the project standardizes on Node 22).
 *
 * Security posture (reuses the contracts validation spirit):
 *   - Secret-looking field names anywhere in the manifest are rejected before
 *     any network call. The secret-key regex matches the one in
 *     packages/contracts/src/validation.ts.
 *   - The gate result is redacted to a stable subset (decision, gate,
 *     reasonCode, riskLevel, policyVersion, evidenceIdentity) — never the full
 *     manifest or source content.
 *   - Dry-run prints the lesson payload but never calls fetch.
 *
 * Usage:
 *   node scripts/agentmemory-enrich.mjs \
 *     --manifest <evidence.json> \
 *     --gate-result <gate-result.json> \
 *     [--base-url <http://host:port>] \
 *     [--dry-run]
 *
 * Exit codes:
 *   0 — enriched or skipped (idempotent / gate not PASS)
 *   1 — validation failure (malformed manifest, secret field, transport error)
 *   2 — usage or operational error (bad args, unreadable file, bad JSON)
 *
 * Environment:
 *   AGENTMEMORY_BASE_URL — default base URL if --base-url is not given.
 *     No default is hardcoded; the caller must supply one for real enrichment.
 */

import { createHash } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// ── Constants ───────────────────────────────────────────────────────────────

const KNOWN_FLAGS = new Set([
  '--manifest',
  '--gate-result',
  '--base-url',
  '--dry-run',
  '--help',
]);

const USAGE =
  'usage: agentmemory-enrich --manifest <evidence.json> --gate-result <gate-result.json>\n' +
  '       [--base-url <http://host:port>] [--dry-run]\n' +
  '\n' +
  'Options:\n' +
  '  --manifest <file>       Path to the evidence manifest JSON (required)\n' +
  '  --gate-result <file>    Path to the policy-gate result JSON (required)\n' +
  '  --base-url <url>        AgentMemory HTTP base URL (default: $AGENTMEMORY_BASE_URL)\n' +
  '  --dry-run               Print the lesson payload; do not call the API\n' +
  '  --help                  Show this help message and exit\n' +
  '\n' +
  'Exit codes: 0=enriched/skipped, 1=validation fail, 2=usage error';

/** Secret-looking key detector — mirrors packages/contracts/src/validation.ts. */
const SECRET_KEY_RE =
  /(?:^|[_-])(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|access[_-]?key|bearer)(?:$|[_-])/i;

const SHA1_RE = /^[0-9a-f]{40}$/;
const SEMVER_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const SHA256_RE = /^[0-9a-f]{64}$/;

const MANIFEST_SCHEMA_VERSION = 1;
const SOURCE_KINDS = new Set(['github-actions', 'local', 'ci', 'manual']);
const CI_CONCLUSIONS = new Set([
  'success', 'failure', 'neutral', 'cancelled', 'skipped', 'timed_out', 'action_required',
]);

// ── Helpers ─────────────────────────────────────────────────────────────────

const isObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);
const isNonEmptyString = (v) => typeof v === 'string' && v.length > 0;

const isRelativePath = (p) => {
  if (p.length === 0) return false;
  if (/^[A-Za-z]:[\\/]/.test(p)) return false;
  if (p.startsWith('\\\\') || p.startsWith('/') || p.startsWith('\\')) return false;
  if (p.includes('\\')) return false;
  const segments = p.split('/');
  if (segments.some((s) => s.length === 0)) return false;
  return true;
};

const hasTraversal = (p) => p.split('/').some((s) => s === '..');

/** Recursively scan for secret-looking object keys. Returns the dotted path or undefined. */
const findSecretKey = (value, trail) => {
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      const found = findSecretKey(value[i], `${trail}[${i}]`);
      if (found) return found;
    }
    return undefined;
  }
  if (isObject(value)) {
    for (const key of Object.keys(value)) {
      const path = trail ? `${trail}.${key}` : key;
      if (SECRET_KEY_RE.test(key)) return path;
      const found = findSecretKey(value[key], path);
      if (found) return found;
    }
  }
  return undefined;
};

// ── Error classes ───────────────────────────────────────────────────────────

class UsageError extends Error {
  constructor(message) { super(message); this.name = 'UsageError'; }
}

class ValidationError extends Error {
  constructor(message, path) { super(message); this.name = 'ValidationError'; this.path = path; }
}

class OperationalError extends Error {
  constructor(message) { super(message); this.name = 'OperationalError'; }
}

// ── Arg parsing ─────────────────────────────────────────────────────────────

const parseArgs = (argv) => {
  const opts = { manifestPath: undefined, gateResultPath: undefined, baseUrl: undefined, dryRun: false };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith('--')) {
      throw new UsageError(`unexpected positional argument: ${arg}`);
    }
    const eq = arg.indexOf('=');
    let name;
    let value;
    if (eq >= 0) {
      name = arg.slice(0, eq);
      value = arg.slice(eq + 1);
    } else {
      name = arg;
      if (name === '--dry-run') {
        value = 'true';
      } else {
        value = argv[++i];
      }
    }
    if (!KNOWN_FLAGS.has(name)) throw new UsageError(`unknown argument: ${name}`);
    if (name !== '--dry-run' && (value === undefined || value === '')) {
      throw new UsageError(`missing value for ${name}`);
    }
    if (name !== '--dry-run' && value !== undefined && value.startsWith('--')) {
      throw new UsageError(`missing value for ${name}`);
    }
    switch (name) {
      case '--manifest': opts.manifestPath = value; break;
      case '--gate-result': opts.gateResultPath = value; break;
      case '--base-url': opts.baseUrl = value; break;
      case '--dry-run': opts.dryRun = true; break;
      case '--help': break;
    }
  }

  if (opts.manifestPath === undefined) throw new UsageError('--manifest is required');
  if (opts.gateResultPath === undefined) throw new UsageError('--gate-result is required');
  opts.baseUrl ??= process.env.AGENTMEMORY_BASE_URL ?? '';
  return opts;
};

// ── Manifest validation (structural subset of contracts) ────────────────────

const validateManifest = (input) => {
  if (!isObject(input)) throw new ValidationError('evidence manifest must be an object');

  const secretPath = findSecretKey(input, '');
  if (secretPath) {
    throw new ValidationError('field looks like a secret and is not allowed', secretPath);
  }

  if (input.schemaVersion !== MANIFEST_SCHEMA_VERSION) {
    throw new ValidationError(`unsupported schemaVersion: expected ${MANIFEST_SCHEMA_VERSION}`, 'schemaVersion');
  }

  if (!isObject(input.repository)) throw new ValidationError('repository is required', 'repository');
  const repo = input.repository;
  if (!isNonEmptyString(repo.owner)) throw new ValidationError('repository.owner is required', 'repository.owner');
  if (!isNonEmptyString(repo.name)) throw new ValidationError('repository.name is required', 'repository.name');
  if (!/^[A-Za-z0-9._-]+$/.test(repo.owner) || repo.owner.length > 100) {
    throw new ValidationError('repository.owner has invalid characters', 'repository.owner');
  }
  if (!/^[A-Za-z0-9._-]+$/.test(repo.name) || repo.name.length > 100) {
    throw new ValidationError('repository.name has invalid characters', 'repository.name');
  }

  let prNumber;
  if (input.prNumber !== undefined) {
    if (typeof input.prNumber !== 'number' || !Number.isInteger(input.prNumber) || input.prNumber <= 0 || input.prNumber > 0xffffffff) {
      throw new ValidationError('prNumber must be a positive integer', 'prNumber');
    }
    prNumber = input.prNumber;
  }

  if (!isNonEmptyString(input.headSha)) throw new ValidationError('headSha is required', 'headSha');
  if (!SHA1_RE.test(input.headSha)) throw new ValidationError('headSha must be a 40-char lowercase hex SHA-1', 'headSha');

  if (!isNonEmptyString(input.policyVersion)) throw new ValidationError('policyVersion is required', 'policyVersion');
  if (!SEMVER_RE.test(input.policyVersion)) throw new ValidationError('policyVersion must be semver', 'policyVersion');

  if (!isNonEmptyString(input.timestamp)) throw new ValidationError('timestamp is required', 'timestamp');
  if (!ISO_RE.test(input.timestamp)) throw new ValidationError('timestamp must be ISO-8601', 'timestamp');

  if (!Array.isArray(input.artifacts) || input.artifacts.length === 0) {
    throw new ValidationError('at least one artifact is required', 'artifacts');
  }
  const seenPaths = new Set();
  const artifacts = [];
  for (let i = 0; i < input.artifacts.length; i++) {
    const a = input.artifacts[i];
    const base = `artifacts[${i}]`;
    if (!isObject(a)) throw new ValidationError('artifact must be an object', base);
    if (!isNonEmptyString(a.path)) throw new ValidationError('artifact.path is required', `${base}.path`);
    if (!isRelativePath(a.path)) throw new ValidationError('artifact.path must be a relative forward-slash path', `${base}.path`);
    if (hasTraversal(a.path)) throw new ValidationError('artifact.path must not contain ..', `${base}.path`);
    if (seenPaths.has(a.path)) throw new ValidationError(`duplicate artifact path: ${a.path}`, `${base}.path`);
    seenPaths.add(a.path);
    if (!isNonEmptyString(a.sha256)) throw new ValidationError('artifact.sha256 is required', `${base}.sha256`);
    if (!SHA256_RE.test(a.sha256)) throw new ValidationError('sha256 must be 64 lowercase hex chars', `${base}.sha256`);
    artifacts.push({ path: a.path, sha256: a.sha256 });
  }

  if (!isObject(input.ci)) throw new ValidationError('ci is required', 'ci');
  if (!CI_CONCLUSIONS.has(input.ci.conclusion)) throw new ValidationError('ci.conclusion is invalid', 'ci.conclusion');
  const ci = { conclusion: input.ci.conclusion };
  if (input.ci.checks !== undefined) {
    if (!Array.isArray(input.ci.checks)) throw new ValidationError('ci.checks must be an array', 'ci.checks');
    const checks = [];
    for (let i = 0; i < input.ci.checks.length; i++) {
      const c = input.ci.checks[i];
      const p = `ci.checks[${i}]`;
      if (!isObject(c)) throw new ValidationError('check must be an object', p);
      if (!isNonEmptyString(c.name)) throw new ValidationError('check.name is required', `${p}.name`);
      if (!CI_CONCLUSIONS.has(c.conclusion)) throw new ValidationError('check.conclusion is invalid', `${p}.conclusion`);
      checks.push({ name: c.name, conclusion: c.conclusion });
    }
    ci.checks = checks;
  }

  if (!isObject(input.source)) throw new ValidationError('source is required', 'source');
  if (!SOURCE_KINDS.has(input.source.kind)) throw new ValidationError('source.kind is invalid', 'source.kind');
  if (!isNonEmptyString(input.source.version) || !SEMVER_RE.test(input.source.version)) {
    throw new ValidationError('source.version must be semver', 'source.version');
  }
  const source = { kind: input.source.kind, version: input.source.version };
  if (input.source.metadata !== undefined) {
    if (!isObject(input.source.metadata)) throw new ValidationError('source.metadata must be an object', 'source.metadata');
    const flat = {};
    for (const [k, val] of Object.entries(input.source.metadata)) {
      if (typeof val !== 'string' && typeof val !== 'number' && typeof val !== 'boolean') {
        throw new ValidationError('source.metadata values must be primitives', `source.metadata.${k}`);
      }
      flat[k] = val;
    }
    source.metadata = flat;
  }

  let idempotencyKey;
  if (input.idempotencyKey !== undefined) {
    if (typeof input.idempotencyKey !== 'string' || input.idempotencyKey.length === 0) {
      throw new ValidationError('idempotencyKey must be a non-empty string', 'idempotencyKey');
    }
    idempotencyKey = input.idempotencyKey;
  }

  return {
    schemaVersion: MANIFEST_SCHEMA_VERSION,
    repository: { owner: repo.owner, name: repo.name },
    prNumber,
    headSha: input.headSha,
    policyVersion: input.policyVersion,
    timestamp: input.timestamp,
    artifacts,
    ci,
    source,
    idempotencyKey,
  };
};

// ── Evidence identity (mirrors packages/contracts/src/identity.ts) ──────────

const canonicalize = (value) => {
  const json = JSON.stringify(value, (_key, v) => {
    if (v === undefined) return undefined;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      const sorted = {};
      for (const k of Object.keys(v).sort()) {
        const val = v[k];
        if (val !== undefined) sorted[k] = val;
      }
      return sorted;
    }
    return v;
  });
  return json ?? '';
};

const computeEvidenceIdentity = (manifest) => {
  const canon = canonicalize(manifest);
  return createHash('sha256').update(canon, 'utf8').digest('hex');
};

// ── Gate result validation + redaction ──────────────────────────────────────

const validateGateResult = (input) => {
  if (!isObject(input)) throw new ValidationError('gate result must be an object');
  const secretPath = findSecretKey(input, '');
  if (secretPath) throw new ValidationError('field looks like a secret and is not allowed', secretPath);
  if (!isNonEmptyString(input.gate)) throw new ValidationError('gate is required', 'gate');
  if (!isNonEmptyString(input.policyVersion)) throw new ValidationError('policyVersion is required', 'policyVersion');
  const result = {
    decision: typeof input.decision === 'string' ? input.decision : '',
    gate: input.gate,
    reasonCode: typeof input.reasonCode === 'string' ? input.reasonCode : '',
    riskLevel: typeof input.riskLevel === 'string' ? input.riskLevel : '',
    policyVersion: input.policyVersion,
  };
  if (input.evidenceIdentity !== undefined) {
    if (typeof input.evidenceIdentity !== 'string' || !SHA256_RE.test(input.evidenceIdentity)) {
      throw new ValidationError('evidenceIdentity must be 64 lowercase hex', 'evidenceIdentity');
    }
    result.evidenceIdentity = input.evidenceIdentity;
  }
  return result;
};

// ── Lesson payload ──────────────────────────────────────────────────────────

const buildLesson = (manifest, gate, evidenceIdentity) => {
  const shortSha = manifest.headSha.slice(0, 10);
  const lesson =
    `verified execution: gate ${gate.gate} for ${manifest.repository.owner}/${manifest.repository.name} ` +
    `@ ${shortSha} (policy ${manifest.policyVersion}, risk ${gate.riskLevel || 'unknown'})`;
  const payload = {
    evidence_identity: evidenceIdentity,
    kind: 'verified-lesson',
    repository: manifest.repository,
    head_sha: manifest.headSha,
    policy_version: manifest.policyVersion,
    gate: gate.gate,
    risk_level: gate.riskLevel,
    reason_code: gate.reasonCode,
    timestamp: manifest.timestamp,
    lesson,
    source: 'hermes-ops/agentmemory-enrich',
    artifacts: manifest.artifacts,
  };
  if (manifest.prNumber !== undefined) {
    payload.pr_number = manifest.prNumber;
  }
  return payload;
};

// ── IO helpers (injectable for tests) ───────────────────────────────────────

const defaultIo = {
  stdout: process.stdout,
  stderr: process.stderr,
  readFileSync: (p, enc) => readFileSync(p, enc),
  statSync: (p) => statSync(p),
  fetch: globalThis.fetch,
};

const readJsonFile = (io, path, label) => {
  let raw;
  try {
    const stat = io.statSync(path);
    if (!stat.isFile()) throw new OperationalError(`${label} path is not a regular file: ${path}`);
    raw = io.readFileSync(path, 'utf8');
  } catch (e) {
    if (e instanceof OperationalError) throw e;
    throw new OperationalError(`cannot read ${label} file: ${path}`);
  }
  try {
    return JSON.parse(raw);
  } catch {
    throw new OperationalError(`${label} is not valid JSON: ${path}`);
  }
};

// ── AgentMemory API ─────────────────────────────────────────────────────────

const joinUrl = (base, path) => {
  if (base === '') return path;
  const b = base.endsWith('/') ? base.slice(0, -1) : base;
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
};

/**
 * Check whether a memory already exists for the given evidence_identity.
 * Returns true if a memory exists (idempotent skip).
 */
const memoryExists = async (io, baseUrl, evidenceIdentity) => {
  const fetchFn = io.fetch;
  if (!fetchFn) throw new OperationalError('fetch is not available');
  const url = joinUrl(baseUrl, `/agentmemory/memories?evidence_identity=${encodeURIComponent(evidenceIdentity)}`);
  const res = await fetchFn(url, { method: 'GET' });
  if (res.status === 404) return false;
  if (!res.ok) throw new OperationalError(`AgentMemory search failed: HTTP ${res.status}`);
  const body = await res.json().catch(() => ({}));
  const memories = Array.isArray(body.memories) ? body.memories : Array.isArray(body) ? body : [];
  return memories.some((m) => isObject(m) && m.evidence_identity === evidenceIdentity);
};

const postMemory = async (io, baseUrl, payload) => {
  const fetchFn = io.fetch;
  if (!fetchFn) throw new OperationalError('fetch is not available');
  const url = joinUrl(baseUrl, '/agentmemory/memories');
  const res = await fetchFn(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new OperationalError(`AgentMemory POST failed: HTTP ${res.status}`);
  }
};

// ── Main ────────────────────────────────────────────────────────────────────

/**
 * Run the enrichment. Returns { exitCode, action }; never throws.
 *
 * @param argv raw argv (excluding node + script path)
 * @param io injectable IO surface (defaults to process stdio + fs + global fetch)
 */
export const runEnrich = async (argv, io = defaultIo) => {
  if (argv.includes('--help') || argv.includes('--help=true')) {
    io.stdout.write(`${USAGE}\n`);
    return { exitCode: 0, action: 'skipped' };
  }

  let opts;
  try {
    opts = parseArgs(argv);
  } catch (e) {
    io.stderr.write(`${USAGE}\n`);
    io.stderr.write(`${e.message}\n`);
    return { exitCode: 2, action: 'skipped' };
  }

  if (!opts.dryRun && opts.baseUrl === '') {
    io.stderr.write(`${USAGE}\n`);
    io.stderr.write('--base-url is required unless --dry-run is set or AGENTMEMORY_BASE_URL is defined\n');
    return { exitCode: 2, action: 'skipped' };
  }

  // Read + parse files (operational errors → exit 2).
  let manifestRaw;
  try {
    manifestRaw = readJsonFile(io, resolve(opts.manifestPath), 'manifest');
  } catch (e) {
    io.stderr.write(`${e.message}\n`);
    return { exitCode: 2, action: 'skipped' };
  }
  let gateRaw;
  try {
    gateRaw = readJsonFile(io, resolve(opts.gateResultPath), 'gate-result');
  } catch (e) {
    io.stderr.write(`${e.message}\n`);
    return { exitCode: 2, action: 'skipped' };
  }

  // Validate (validation failures → exit 1).
  let manifest;
  let gate;
  try {
    manifest = validateManifest(manifestRaw);
  } catch (e) {
    const pathStr = e.path ? ` (${e.path})` : '';
    io.stderr.write(`manifest validation failed: ${e.message}${pathStr}\n`);
    return { exitCode: 1, action: 'skipped' };
  }
  try {
    gate = validateGateResult(gateRaw);
  } catch (e) {
    const pathStr = e.path ? ` (${e.path})` : '';
    io.stderr.write(`gate-result validation failed: ${e.message}${pathStr}\n`);
    return { exitCode: 1, action: 'skipped' };
  }

  const evidenceIdentity = computeEvidenceIdentity(manifest);

  // Only verified (gate PASS) lessons are enriched. A non-PASS gate is a
  // legitimate skip, not a validation failure.
  if (gate.gate !== 'PASS') {
    io.stdout.write(`skipped: gate is ${gate.gate}, not PASS — no verified lesson to enrich\n`);
    return { exitCode: 0, action: 'skipped' };
  }

  const payload = buildLesson(manifest, gate, evidenceIdentity);

  if (opts.dryRun) {
    io.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
    io.stdout.write('dry-run: no HTTP request sent\n');
    return { exitCode: 0, action: 'dry-run' };
  }

  // Idempotency check: skip if a memory already exists for this identity.
  try {
    const exists = await memoryExists(io, opts.baseUrl, evidenceIdentity);
    if (exists) {
      io.stdout.write(`skipped: memory already exists for evidence_identity ${evidenceIdentity.slice(0, 12)}…\n`);
      return { exitCode: 0, action: 'skipped' };
    }
  } catch (e) {
    io.stderr.write(`${e.message}\n`);
    return { exitCode: 1, action: 'skipped' };
  }

  // POST the lesson.
  try {
    await postMemory(io, opts.baseUrl, payload);
  } catch (e) {
    io.stderr.write(`${e.message}\n`);
    return { exitCode: 1, action: 'skipped' };
  }

  io.stdout.write(`enriched: posted verified lesson for evidence_identity ${evidenceIdentity.slice(0, 12)}…\n`);
  return { exitCode: 0, action: 'enriched' };
};

// ── CLI entrypoint ──────────────────────────────────────────────────────────

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const argv = process.argv.slice(2);
  runEnrich(argv).then((r) => process.exit(r.exitCode));
}
