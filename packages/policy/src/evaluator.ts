/**
 * Deterministic, fail-closed policy evaluator for Hermes Ops.
 *
 * The evaluator is a pure function: same inputs always yield the same decision.
 * It is fail-closed — any validation failure, freshness issue, head-SHA
 * mismatch, non-green CI, unresolved critical CodeRabbit finding, or duplicate
 * idempotency key yields a `fail` decision with a stable reason code.
 *
 * Phase 0 has no DB/queue/network. Duplicate detection is driven by an injected
 * `seenIdempotencyKeys` set so the evaluator stays stateless and testable.
 */

import {
  computeEvidenceIdentity,
  validateEvidenceManifest,
  type EvidenceManifest,
  type ValidationOptions,
} from '@hermes-ops/contracts';

/** Stable reason codes for policy decisions. */
export type PolicyReasonCode =
  | 'PASS'
  | 'EVIDENCE_INVALID'
  | 'EVIDENCE_STALE'
  | 'HEAD_SHA_MISMATCH'
  | 'CI_NOT_GREEN'
  | 'UNRESOLVED_CRITICAL_FINDING'
  | 'DUPLICATE_EVIDENCE'
  | 'POLICY_VERSION_MISMATCH';

export type PolicyDecision = 'pass' | 'fail';

export interface PolicyResult {
  readonly decision: PolicyDecision;
  readonly reasonCode: PolicyReasonCode;
  /** Policy version the evaluator was configured with. */
  readonly policyVersion: string;
  /** Stable SHA-256 identity of the evidence, when validation succeeded. */
  readonly evidenceIdentity?: string;
  /** Human-readable detail; not for control flow (use reasonCode). */
  readonly detail: string;
  /** The validated manifest, when validation succeeded. */
  readonly manifest?: EvidenceManifest;
}

export interface PolicyEvaluatorOptions {
  /** Head SHA the evidence must be bound to. */
  readonly expectedHeadSha: string;
  /** Policy version the evaluator is running. Must match the manifest's. */
  readonly policyVersion: string;
  /** Injectable now for deterministic freshness checks. */
  readonly now?: Date;
  /** Max evidence age in ms. Defaults to contracts' 24h. */
  readonly maxAgeMs?: number;
  /**
   * Idempotency keys already observed for this head SHA. If the manifest's
   * idempotencyKey is present and appears here, the decision is a duplicate fail.
   */
  readonly seenIdempotencyKeys?: ReadonlySet<string>;
}

const isCiGreen = (manifest: EvidenceManifest): boolean => {
  if (manifest.ci.conclusion !== 'success') return false;
  if (manifest.ci.checks) {
    for (const c of manifest.ci.checks) {
      if (c.conclusion !== 'success' && c.conclusion !== 'neutral' && c.conclusion !== 'skipped') {
        return false;
      }
    }
  }
  return true;
};

const hasUnresolvedCriticalFinding = (manifest: EvidenceManifest): boolean => {
  if (!manifest.coderabbit) return false;
  return manifest.coderabbit.findings.some(
    (f) => f.severity === 'critical' && !f.resolved,
  );
};

/**
 * Evaluate a policy decision over an unknown evidence payload.
 *
 * Order of checks (fail-closed, first failure wins):
 *   1. Validate the manifest (structural, freshness, head-SHA mismatch).
 *   2. Policy version must match the evaluator's configured version.
 *   3. Idempotency key must not already be seen.
 *   4. CI must be green.
 *   5. No unresolved critical CodeRabbit finding.
 *
 * Returns a `PolicyResult`. Never throws on bad input — bad input is a fail.
 */
export const evaluatePolicy = (
  input: unknown,
  options: PolicyEvaluatorOptions,
): PolicyResult => {
  const base = {
    policyVersion: options.policyVersion,
  };

  const validationOptions: ValidationOptions = {
    expectedHeadSha: options.expectedHeadSha,
    now: options.now,
    maxAgeMs: options.maxAgeMs,
  };

  const result = validateEvidenceManifest(input, validationOptions);
  if (!result.ok) {
    // Map validation error codes to policy reason codes where they matter.
    const code = result.error.code;
    let reasonCode: PolicyReasonCode = 'EVIDENCE_INVALID';
    if (code === 'STALE_TIMESTAMP') reasonCode = 'EVIDENCE_STALE';
    else if (code === 'HEAD_SHA_MISMATCH') reasonCode = 'HEAD_SHA_MISMATCH';
    return {
      ...base,
      decision: 'fail',
      reasonCode,
      detail: result.error.message,
    };
  }

  const manifest = result.manifest;

  if (manifest.policyVersion !== options.policyVersion) {
    return {
      ...base,
      decision: 'fail',
      reasonCode: 'POLICY_VERSION_MISMATCH',
      detail: `manifest policyVersion ${manifest.policyVersion} != evaluator ${options.policyVersion}`,
      manifest,
    };
  }

  if (
    manifest.idempotencyKey &&
    options.seenIdempotencyKeys?.has(manifest.idempotencyKey)
  ) {
    return {
      ...base,
      decision: 'fail',
      reasonCode: 'DUPLICATE_EVIDENCE',
      detail: `duplicate idempotency key: ${manifest.idempotencyKey}`,
      manifest,
    };
  }

  if (!isCiGreen(manifest)) {
    return {
      ...base,
      decision: 'fail',
      reasonCode: 'CI_NOT_GREEN',
      detail: `ci conclusion is ${manifest.ci.conclusion}`,
      manifest,
    };
  }

  if (hasUnresolvedCriticalFinding(manifest)) {
    return {
      ...base,
      decision: 'fail',
      reasonCode: 'UNRESOLVED_CRITICAL_FINDING',
      detail: 'one or more critical CodeRabbit findings are unresolved',
      manifest,
    };
  }

  return {
    ...base,
    decision: 'pass',
    reasonCode: 'PASS',
    evidenceIdentity: computeEvidenceIdentity(manifest),
    detail: 'evidence satisfies policy',
    manifest,
  };
};
