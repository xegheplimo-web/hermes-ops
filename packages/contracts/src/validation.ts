/**
 * Runtime validation for EvidenceManifest v1.
 *
 * Validation is structural (rejects malformed/unknown input), security-conscious
 * (rejects absolute paths, path traversal, and secret-looking fields), and
 * freshness-aware (rejects stale timestamps and head-SHA mismatch against an
 * expected SHA provided by the caller).
 *
 * No network, no DB, no credentials. Pure functions only.
 */

import type {
  ArtifactReference,
  CiConclusion,
  CodeRabbitFindings,
  CodeRabbitSeverity,
  DevinRunMetadata,
  EvidenceManifest,
  RepositoryIdentity,
  SourceAdapter,
  SourceAdapterKind,
} from './manifest.js';
import { MANIFEST_SCHEMA_VERSION } from './manifest.js';
import type { ValidationError, ValidationErrorCode, ValidationResult } from './errors.js';

const SHA1_RE = /^[0-9a-f]{40}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const SEMVER_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const ISO_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

const CI_CONCLUSIONS: readonly CiConclusion[] = [
  'success',
  'failure',
  'neutral',
  'cancelled',
  'skipped',
  'timed_out',
  'action_required',
];

const SOURCE_KINDS: readonly SourceAdapterKind[] = [
  'github-actions',
  'local',
  'ci',
  'manual',
];

const CODERABBIT_SEVERITIES: readonly CodeRabbitSeverity[] = [
  'critical',
  'high',
  'medium',
  'low',
  'info',
];

/** Keys that look like secrets. Case-insensitive, token-delimited match. */
const SECRET_KEY_RE =
  /(?:^|[_-])(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|access[_-]?key|bearer)(?:$|[_-])/i;

/** Default freshness window: 24 hours. */
export const DEFAULT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** Allowed clock skew for timestamps slightly in the future. */
export const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000;

export interface ValidationOptions {
  /** Head SHA the caller expects the evidence to be bound to. */
  readonly expectedHeadSha: string;
  /** Injectable now for deterministic tests. Defaults to new Date(). */
  readonly now?: Date;
  /** Max age of the evidence timestamp in ms. Defaults to 24h. */
  readonly maxAgeMs?: number;
}

/** Top-level ok result. */
const okResult = (manifest: EvidenceManifest): ValidationResult => ({
  ok: true,
  manifest,
});

/** Top-level fail result. */
const failResult = (code: ValidationErrorCode, message: string, path?: string): ValidationResult => ({
  ok: false,
  error: { code, message, path },
});

/** Sub-validator result: carries a typed value on success. */
type SubResult<T> = { ok: true; value: T } | { ok: false; error: ValidationError };

const subOk = <T>(value: T): SubResult<T> => ({ ok: true, value });
const subErr = (code: ValidationErrorCode, message: string, path?: string): SubResult<never> => ({
  ok: false,
  error: { code, message, path },
});

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const isString = (v: unknown): v is string => typeof v === 'string';

const isNonEmptyString = (v: unknown): v is string =>
  typeof v === 'string' && v.length > 0;

/** Recursively scan an unknown value for secret-looking object keys. */
const findSecretKey = (value: unknown, trail: string): string | undefined => {
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

const isRelativePath = (path: string): boolean => {
  if (path.length === 0) return false;
  if (/^[A-Za-z]:[\\/]/.test(path)) return false;
  if (path.startsWith('\\\\')) return false;
  if (path.startsWith('/')) return false;
  if (path.startsWith('\\')) return false;
  if (path.includes('\\')) return false;
  const segments = path.split('/');
  if (segments.some((s) => s.length === 0)) return false;
  return true;
};

const hasTraversal = (path: string): boolean => path.split('/').some((s) => s === '..');

/**
 * Validate an unknown value as an EvidenceManifest v1.
 *
 * Returns a discriminated result. On success, the manifest is typed and trusted.
 * On failure, a stable `ValidationErrorCode` and human-readable message are
 * provided. Validation is fail-closed: any ambiguity rejects the input.
 */
export const validateEvidenceManifest = (
  input: unknown,
  options: ValidationOptions,
): ValidationResult => {
  if (!isObject(input)) {
    return failResult('MALFORMED', 'evidence manifest must be an object');
  }

  // Secret-looking fields anywhere in the raw input are rejected before any
  // structural work, so we never echo secrets back to callers.
  const secretPath = findSecretKey(input, '');
  if (secretPath) {
    return failResult(
      'SECRET_FIELD',
      'field looks like a secret and is not allowed',
      secretPath,
    );
  }

  // expectedHeadSha option must itself be valid.
  if (!isNonEmptyString(options.expectedHeadSha)) {
    return failResult('INVALID_HEAD_SHA', 'expectedHeadSha option is required');
  }
  if (!SHA1_RE.test(options.expectedHeadSha)) {
    return failResult(
      'INVALID_HEAD_SHA',
      'expectedHeadSha must be a 40-char lowercase hex SHA-1',
    );
  }

  // schemaVersion
  if (!('schemaVersion' in input)) {
    return failResult('MISSING_REQUIRED_FIELD', 'schemaVersion is required', 'schemaVersion');
  }
  if (input.schemaVersion !== MANIFEST_SCHEMA_VERSION) {
    return failResult(
      'SCHEMA_VERSION_UNSUPPORTED',
      `unsupported schemaVersion: expected ${MANIFEST_SCHEMA_VERSION}`,
      'schemaVersion',
    );
  }

  // repository
  if (!isObject(input.repository)) {
    return failResult('MISSING_REQUIRED_FIELD', 'repository is required', 'repository');
  }
  const repoResult = validateRepository(input.repository);
  if (!repoResult.ok) return failResult(repoResult.error.code, repoResult.error.message, repoResult.error.path);

  // prNumber (optional)
  let prNumber: number | undefined;
  if ('prNumber' in input && input.prNumber !== undefined) {
    if (
      typeof input.prNumber !== 'number' ||
      !Number.isInteger(input.prNumber) ||
      input.prNumber <= 0 ||
      input.prNumber > 0xffffffff
    ) {
      return failResult('INVALID_PR_NUMBER', 'prNumber must be a positive integer', 'prNumber');
    }
    prNumber = input.prNumber;
  }

  // headSha
  if (!isNonEmptyString(input.headSha)) {
    return failResult('MISSING_REQUIRED_FIELD', 'headSha is required', 'headSha');
  }
  if (!SHA1_RE.test(input.headSha)) {
    return failResult('INVALID_HEAD_SHA', 'headSha must be a 40-char lowercase hex SHA-1', 'headSha');
  }

  // policyVersion
  if (!isNonEmptyString(input.policyVersion)) {
    return failResult('MISSING_REQUIRED_FIELD', 'policyVersion is required', 'policyVersion');
  }
  if (!SEMVER_RE.test(input.policyVersion)) {
    return failResult('INVALID_POLICY_VERSION', 'policyVersion must be semver', 'policyVersion');
  }

  // timestamp
  if (!isNonEmptyString(input.timestamp)) {
    return failResult('MISSING_REQUIRED_FIELD', 'timestamp is required', 'timestamp');
  }
  if (!ISO_RE.test(input.timestamp)) {
    return failResult('INVALID_TIMESTAMP', 'timestamp must be ISO-8601', 'timestamp');
  }
  const tsMs = Date.parse(input.timestamp);
  if (Number.isNaN(tsMs)) {
    return failResult('INVALID_TIMESTAMP', 'timestamp is not a valid date', 'timestamp');
  }
  const now = options.now ?? new Date();
  const maxAge = options.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const ageMs = now.getTime() - tsMs;
  if (ageMs < -MAX_FUTURE_SKEW_MS) {
    return failResult('STALE_TIMESTAMP', 'timestamp is in the future', 'timestamp');
  }
  if (ageMs > maxAge) {
    return failResult('STALE_TIMESTAMP', `timestamp is older than ${maxAge}ms`, 'timestamp');
  }

  // artifacts
  if (!Array.isArray(input.artifacts)) {
    return failResult('MISSING_REQUIRED_FIELD', 'artifacts is required', 'artifacts');
  }
  if (input.artifacts.length === 0) {
    return failResult('EMPTY_ARTIFACTS', 'at least one artifact is required', 'artifacts');
  }
  const seenPaths = new Set<string>();
  const artifacts: ArtifactReference[] = [];
  for (let i = 0; i < input.artifacts.length; i++) {
    const a = input.artifacts[i];
    const basePath = `artifacts[${i}]`;
    if (!isObject(a)) {
      return failResult('INVALID_TYPE', 'artifact must be an object', basePath);
    }
    if (!isNonEmptyString(a.path)) {
      return failResult('MISSING_REQUIRED_FIELD', 'artifact.path is required', `${basePath}.path`);
    }
    if (!isRelativePath(a.path)) {
      return failResult('ABSOLUTE_PATH', 'artifact.path must be a relative forward-slash path', `${basePath}.path`);
    }
    if (hasTraversal(a.path)) {
      return failResult('PATH_TRAVERSAL', 'artifact.path must not contain ..', `${basePath}.path`);
    }
    if (seenPaths.has(a.path)) {
      return failResult('DUPLICATE_ARTIFACT_PATH', `duplicate artifact path: ${a.path}`, `${basePath}.path`);
    }
    seenPaths.add(a.path);
    if (!isNonEmptyString(a.sha256)) {
      return failResult('MISSING_REQUIRED_FIELD', 'artifact.sha256 is required', `${basePath}.sha256`);
    }
    if (!SHA256_RE.test(a.sha256)) {
      return failResult('INVALID_SHA256', 'sha256 must be 64 lowercase hex chars', `${basePath}.sha256`);
    }
    artifacts.push({ path: a.path, sha256: a.sha256 });
  }

  // ci
  if (!isObject(input.ci)) {
    return failResult('MISSING_REQUIRED_FIELD', 'ci is required', 'ci');
  }
  const ciResult = validateCi(input.ci);
  if (!ciResult.ok) return failResult(ciResult.error.code, ciResult.error.message, ciResult.error.path);

  // coderabbit (optional)
  let coderabbit: CodeRabbitFindings | undefined;
  if ('coderabbit' in input && input.coderabbit !== undefined) {
    const crResult = validateCodeRabbit(input.coderabbit);
    if (!crResult.ok) return failResult(crResult.error.code, crResult.error.message, crResult.error.path);
    coderabbit = crResult.value;
  }

  // devin (optional)
  let devin: DevinRunMetadata | undefined;
  if ('devin' in input && input.devin !== undefined) {
    const dResult = validateDevin(input.devin);
    if (!dResult.ok) return failResult(dResult.error.code, dResult.error.message, dResult.error.path);
    devin = dResult.value;
  }

  // source
  if (!isObject(input.source)) {
    return failResult('MISSING_REQUIRED_FIELD', 'source is required', 'source');
  }
  const sourceResult = validateSource(input.source);
  if (!sourceResult.ok) return failResult(sourceResult.error.code, sourceResult.error.message, sourceResult.error.path);

  // idempotencyKey (optional)
  let idempotencyKey: string | undefined;
  if ('idempotencyKey' in input && input.idempotencyKey !== undefined) {
    if (!isString(input.idempotencyKey) || input.idempotencyKey.length === 0) {
      return failResult('INVALID_IDEMPOTENCY_KEY', 'idempotencyKey must be a non-empty string', 'idempotencyKey');
    }
    idempotencyKey = input.idempotencyKey;
  }

  // head-SHA mismatch against expected SHA.
  if (input.headSha !== options.expectedHeadSha) {
    return failResult(
      'HEAD_SHA_MISMATCH',
      `headSha ${input.headSha} does not match expected ${options.expectedHeadSha}`,
      'headSha',
    );
  }

  const manifest: EvidenceManifest = {
    schemaVersion: MANIFEST_SCHEMA_VERSION,
    repository: repoResult.value,
    prNumber,
    headSha: input.headSha,
    policyVersion: input.policyVersion,
    timestamp: input.timestamp,
    artifacts,
    ci: ciResult.value,
    coderabbit,
    devin,
    source: sourceResult.value,
    idempotencyKey,
  };

  return okResult(manifest);
};

const validateRepository = (v: Record<string, unknown>): SubResult<RepositoryIdentity> => {
  if (!isNonEmptyString(v.owner)) {
    return subErr('INVALID_REPOSITORY', 'repository.owner is required', 'repository.owner');
  }
  if (!isNonEmptyString(v.name)) {
    return subErr('INVALID_REPOSITORY', 'repository.name is required', 'repository.name');
  }
  if (!/^[A-Za-z0-9._-]+$/.test(v.owner) || v.owner.length > 100) {
    return subErr('INVALID_REPOSITORY', 'repository.owner has invalid characters', 'repository.owner');
  }
  if (!/^[A-Za-z0-9._-]+$/.test(v.name) || v.name.length > 100) {
    return subErr('INVALID_REPOSITORY', 'repository.name has invalid characters', 'repository.name');
  }
  return subOk<RepositoryIdentity>({ owner: v.owner, name: v.name });
};

const validateCi = (v: Record<string, unknown>): SubResult<EvidenceManifest['ci']> => {
  if (!isString(v.conclusion) || !CI_CONCLUSIONS.includes(v.conclusion as CiConclusion)) {
    return subErr('INVALID_CI_CONCLUSION', 'ci.conclusion is invalid', 'ci.conclusion');
  }
  const conclusion = v.conclusion as CiConclusion;
  let checks: readonly { name: string; conclusion: CiConclusion }[] | undefined;
  if (v.checks !== undefined) {
    if (!Array.isArray(v.checks)) {
      return subErr('INVALID_TYPE', 'ci.checks must be an array', 'ci.checks');
    }
    const validated: { name: string; conclusion: CiConclusion }[] = [];
    for (let i = 0; i < v.checks.length; i++) {
      const c = v.checks[i];
      const path = `ci.checks[${i}]`;
      if (!isObject(c)) {
        return subErr('INVALID_TYPE', 'check must be an object', path);
      }
      if (!isNonEmptyString(c.name)) {
        return subErr('MISSING_REQUIRED_FIELD', 'check.name is required', `${path}.name`);
      }
      if (!isString(c.conclusion) || !CI_CONCLUSIONS.includes(c.conclusion as CiConclusion)) {
        return subErr('INVALID_CI_CONCLUSION', 'check.conclusion is invalid', `${path}.conclusion`);
      }
      validated.push({ name: c.name, conclusion: c.conclusion as CiConclusion });
    }
    checks = validated;
  }
  return subOk<EvidenceManifest['ci']>({ conclusion, checks });
};

const validateCodeRabbit = (v: unknown): SubResult<CodeRabbitFindings> => {
  if (!isObject(v)) {
    return subErr('INVALID_CODERABBIT_FINDING', 'coderabbit must be an object', 'coderabbit');
  }
  if (!Array.isArray(v.findings)) {
    return subErr('INVALID_CODERABBIT_FINDING', 'coderabbit.findings is required', 'coderabbit.findings');
  }
  const findings: CodeRabbitFindings['findings'][number][] = [];
  for (let i = 0; i < v.findings.length; i++) {
    const f = v.findings[i];
    const path = `coderabbit.findings[${i}]`;
    if (!isObject(f)) {
      return subErr('INVALID_CODERABBIT_FINDING', 'finding must be an object', path);
    }
    if (!isNonEmptyString(f.id)) {
      return subErr('INVALID_CODERABBIT_FINDING', 'finding.id is required', `${path}.id`);
    }
    if (!isString(f.severity) || !CODERABBIT_SEVERITIES.includes(f.severity as CodeRabbitSeverity)) {
      return subErr('INVALID_CODERABBIT_FINDING', 'finding.severity is invalid', `${path}.severity`);
    }
    if (typeof f.resolved !== 'boolean') {
      return subErr('INVALID_CODERABBIT_FINDING', 'finding.resolved must be boolean', `${path}.resolved`);
    }
    findings.push({ id: f.id, severity: f.severity as CodeRabbitSeverity, resolved: f.resolved });
  }
  return subOk<CodeRabbitFindings>({ findings });
};

const validateDevin = (v: unknown): SubResult<DevinRunMetadata> => {
  if (!isObject(v)) {
    return subErr('INVALID_DEVIN_METADATA', 'devin must be an object', 'devin');
  }
  if (!isNonEmptyString(v.runId)) {
    return subErr('INVALID_DEVIN_METADATA', 'devin.runId is required', 'devin.runId');
  }
  if (!isNonEmptyString(v.status)) {
    return subErr('INVALID_DEVIN_METADATA', 'devin.status is required', 'devin.status');
  }
  let startedAt: string | undefined;
  if (v.startedAt !== undefined) {
    if (!isString(v.startedAt) || !ISO_RE.test(v.startedAt)) {
      return subErr('INVALID_DEVIN_METADATA', 'devin.startedAt must be ISO-8601', 'devin.startedAt');
    }
    startedAt = v.startedAt;
  }
  let finishedAt: string | undefined;
  if (v.finishedAt !== undefined) {
    if (!isString(v.finishedAt) || !ISO_RE.test(v.finishedAt)) {
      return subErr('INVALID_DEVIN_METADATA', 'devin.finishedAt must be ISO-8601', 'devin.finishedAt');
    }
    finishedAt = v.finishedAt;
  }
  const devin: DevinRunMetadata = { runId: v.runId, status: v.status, startedAt, finishedAt };
  return subOk<DevinRunMetadata>(devin);
};

const validateSource = (v: Record<string, unknown>): SubResult<SourceAdapter> => {
  if (!isString(v.kind) || !SOURCE_KINDS.includes(v.kind as SourceAdapterKind)) {
    return subErr('INVALID_SOURCE_ADAPTER', 'source.kind is invalid', 'source.kind');
  }
  if (!isNonEmptyString(v.version) || !SEMVER_RE.test(v.version)) {
    return subErr('INVALID_SOURCE_ADAPTER', 'source.version must be semver', 'source.version');
  }
  let metadata: Readonly<Record<string, string | number | boolean>> | undefined;
  if (v.metadata !== undefined) {
    if (!isObject(v.metadata)) {
      return subErr('INVALID_SOURCE_ADAPTER', 'source.metadata must be an object', 'source.metadata');
    }
    const flat: Record<string, string | number | boolean> = {};
    for (const [k, val] of Object.entries(v.metadata)) {
      if (typeof val !== 'string' && typeof val !== 'number' && typeof val !== 'boolean') {
        return subErr('INVALID_SOURCE_ADAPTER', 'source.metadata values must be primitives', `source.metadata.${k}`);
      }
      flat[k] = val;
    }
    metadata = flat;
  }
  return subOk<SourceAdapter>({ kind: v.kind as SourceAdapterKind, version: v.version, metadata });
};
