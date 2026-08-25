/**
 * 4-way Policy Gate engine.
 *
 * Maps the pure policy evaluator result, plus runtime state (attempts,
 * max-attempts, human approval, risk), into one of four canonical outcomes:
 *   PASS, REPAIR, ESCALATE, BLOCK.
 *
 * The engine is deterministic and fail-closed: any missing or contradictory
 * input is treated as a reason to block rather than proceed.
 */

import {
  evaluatePolicy,
  classifyFromPolicyResult,
  recalculatePostDiffRisk,
} from '@hermes-ops/policy';
import {
  type RiskLevel,
  RISK_ORDER,
  normalizeRiskLevel,
} from '@hermes-ops/contracts';
import type { HumanApprovalToken } from './approval.js';

export type GateOutcome = 'PASS' | 'REPAIR' | 'ESCALATE' | 'BLOCK';

export interface GateEngineInput {
  /** The evidence manifest (unknown shape, validated by the evaluator). */
  readonly manifest: unknown;
  /** Expected 40-char lowercase hex HEAD SHA. */
  readonly expectedHeadSha: string;
  /** Policy version the gate is enforcing. */
  readonly policyVersion: string;
  /** Optional list of changed file paths for post-diff risk recalculation. */
  readonly changedFiles?: readonly string[];
  /** Current attempt count for the task. */
  readonly attempts: number;
  /** Maximum allowed attempts before escalation. */
  readonly maxAttempts: number;
  /** Explicit risk override from the control plane (early or final risk). */
  readonly explicitRisk?: RiskLevel;
  /** Optional human approval token (e.g., from Ops DB or CLI --approval). */
  readonly approval?: HumanApprovalToken;
  /** Optional now timestamp for deterministic freshness checks. */
  readonly now?: Date;
  /** Optional set of already-seen idempotency keys. */
  readonly seenIdempotencyKeys?: ReadonlySet<string>;
}

export interface GateEngineResult {
  /** The underlying policy decision (pass/fail). */
  readonly decision: 'pass' | 'fail';
  /** The 4-way gate outcome. */
  readonly gate: GateOutcome;
  /** Stable policy reason code from the evaluator or gate. */
  readonly reasonCode: string;
  /** Canonical risk level used for gating. */
  readonly riskLevel: RiskLevel;
  /** Gates required before merge for this risk. */
  readonly requiredGates: readonly string[];
  /** Human-readable detail. */
  readonly detail: string;
  /** Policy version passed through. */
  readonly policyVersion: string;
  /** Evidence identity, when available. */
  readonly evidenceIdentity?: string;
}

const MAX_ATTEMPTS_DEFAULT = 3;

const requiredGatesForRisk = (risk: RiskLevel): readonly string[] => {
  if (risk === 'CRITICAL') return ['ci', 'codex', 'human'];
  if (risk === 'HIGH') return ['ci', 'codex'];
  return ['ci'];
};

const maxRisk = (a: RiskLevel, b: RiskLevel): RiskLevel => {
  return RISK_ORDER[a] >= RISK_ORDER[b] ? a : b;
};

const isApprovalTokenValid = (token?: HumanApprovalToken): boolean => {
  if (!token) return false;
  return !!(
    token.signedAt &&
    token.approver &&
    token.reason &&
    token.signature
  );
};

const coerceAttempts = (n: unknown): number => {
  if (typeof n !== 'number' || !Number.isInteger(n) || n < 0) return 0;
  return n;
};

const coerceMaxAttempts = (n: unknown): number => {
  if (typeof n !== 'number' || !Number.isInteger(n) || n < 1) return MAX_ATTEMPTS_DEFAULT;
  return n;
};

/**
 * Evaluate the 4-way gate.
 *
 * Order (fail-closed):
 *   1. Stale evidence / HEAD SHA mismatch → BLOCK
 *   2. Risk downgraded without evidence → BLOCK
 *   3. attempts >= maxAttempts → ESCALATE
 *   4. CRITICAL without human approval → BLOCK
 *   5. CI red (or other evaluator fail) with attempts remaining → REPAIR
 *   6. All green → PASS
 */
export const evaluateGate = (input: GateEngineInput): GateEngineResult => {
  const attempts = coerceAttempts(input.attempts);
  const maxAttempts = coerceMaxAttempts(input.maxAttempts);

  const result = evaluatePolicy(input.manifest, {
    expectedHeadSha: input.expectedHeadSha,
    policyVersion: input.policyVersion,
    now: input.now,
    seenIdempotencyKeys: input.seenIdempotencyKeys,
  });

  // Compute risk from evidence + changed paths.
  let riskLevel = classifyFromPolicyResult(result);
  if (input.changedFiles && input.changedFiles.length > 0) {
    riskLevel = recalculatePostDiffRisk([...input.changedFiles], riskLevel);
  }

  // Honour explicit control-plane risk, if provided.
  if (input.explicitRisk) {
    riskLevel = maxRisk(riskLevel, input.explicitRisk);
  }

  const requiredGates = requiredGatesForRisk(riskLevel);

  // 1. Stale / SHA mismatch are unrecoverable blockers.
  if (result.reasonCode === 'EVIDENCE_STALE' || result.reasonCode === 'HEAD_SHA_MISMATCH') {
    return {
      decision: 'fail',
      gate: 'BLOCK',
      reasonCode: result.reasonCode,
      riskLevel,
      requiredGates,
      detail: `${result.reasonCode}: evidence cannot be used for this gate`,
      policyVersion: input.policyVersion,
    };
  }

  // 2. Attempts exhausted → ESCALATE before anything else.
  if (attempts >= maxAttempts) {
    return {
      decision: 'fail',
      gate: 'ESCALATE',
      reasonCode: result.reasonCode,
      riskLevel,
      requiredGates,
      detail: `attempts ${attempts} >= ${maxAttempts}: exhausted`,
      policyVersion: input.policyVersion,
    };
  }

  // 3. CRITICAL without durable human approval → BLOCK.
  if (riskLevel === 'CRITICAL' && !isApprovalTokenValid(input.approval)) {
    return {
      decision: 'fail',
      gate: 'BLOCK',
      reasonCode: 'HUMAN_APPROVAL_REQUIRED',
      riskLevel,
      requiredGates,
      detail: 'human approval required for CRITICAL risk',
      policyVersion: input.policyVersion,
    };
  }

  // 4. Policy passed → PASS.
  if (result.decision === 'pass') {
    return {
      decision: 'pass',
      gate: 'PASS',
      reasonCode: 'PASS',
      riskLevel,
      requiredGates,
      detail: 'evidence satisfies policy',
      policyVersion: input.policyVersion,
      evidenceIdentity: result.evidenceIdentity,
    };
  }

  // 5. Remaining evaluator failure with attempts left → REPAIR.
  return {
    decision: 'fail',
    gate: 'REPAIR',
    reasonCode: result.reasonCode,
    riskLevel,
    requiredGates,
    detail: `${result.reasonCode}: repairable failure, attempts ${attempts}/${maxAttempts}`,
    policyVersion: input.policyVersion,
  };
};

export { type RiskLevel, normalizeRiskLevel };
