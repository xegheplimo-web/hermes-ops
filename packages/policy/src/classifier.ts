/**
 * Risk Classifier — maps risk signals to 'auto-eligible' or 'human-required'.
 *
 * This replaces the unimplemented full LOW/MED/HIGH/CRITICAL routing with
 * a simple, deterministic binary classifier.
 */

import {
  type PolicyReasonCode,
  type PolicyResult,
} from './evaluator.js';

/** Binary classification of risk. */
export type RiskClass = 'auto-eligible' | 'human-required';

/** Input signals for the classifier. */
export interface RiskSignal {
  /** CI pipeline failed or is not green. */
  ciFailure: boolean;
  /** One or more critical findings are unresolved. */
  unresolvedCritical: boolean;
  /** The evidence's policy version does not match the configured version. */
  policyMismatch: boolean;
  /** The idempotency key was already seen (duplicate). */
  duplicateEvidence: boolean;
  /** The evidence touches authentication or credential material. */
  touchesAuth: boolean;
}

/**
 * Pure function: classify a set of risk signals.
 *
 * Order of checks (first match wins):
 *   1. CI failure → auto-eligible (retry is safe and expected)
 *   2. Unresolved critical finding → human-required
 *   3. Policy version mismatch → human-required
 *   4. Duplicate evidence → auto-eligible (retry)
 *   5. Touches auth/credentials → human-required
 *   6. No signals set → auto-eligible (no risk)
 */
export const classifyRisk = (signals: RiskSignal): RiskClass => {
  if (signals.ciFailure) return 'auto-eligible';
  if (signals.unresolvedCritical) return 'human-required';
  if (signals.policyMismatch) return 'human-required';
  if (signals.duplicateEvidence) return 'auto-eligible';
  if (signals.touchesAuth) return 'human-required';
  return 'auto-eligible';
};

/**
 * Map known {@link PolicyReasonCode}s to a {@link RiskSignal} and classify.
 *
 * Reason codes that do not map to a specific signal (e.g. EVIDENCE_INVALID,
 * EVIDENCE_STALE, HEAD_SHA_MISMATCH) are treated as empty signals, which
 * yields `'auto-eligible'`.
 */
export const classifyFromPolicyResult = (
  result: PolicyResult,
): RiskClass => {
  const signal = reasonCodeToSignal(result.reasonCode);
  return classifyRisk(signal);
};

/**
 * Map a single {@link PolicyReasonCode} to a {@link RiskSignal}.
 *
 * Only the corresponding signal field is set to `true` — all others default
 * to `false`. Reason codes not listed here produce an all-false signal.
 */
const reasonCodeToSignal = (
  code: PolicyReasonCode,
): RiskSignal => {
  switch (code) {
    case 'CI_NOT_GREEN':
      return { ciFailure: true, unresolvedCritical: false, policyMismatch: false, duplicateEvidence: false, touchesAuth: false };
    case 'UNRESOLVED_CRITICAL_FINDING':
      return { ciFailure: false, unresolvedCritical: true, policyMismatch: false, duplicateEvidence: false, touchesAuth: false };
    case 'POLICY_VERSION_MISMATCH':
      return { ciFailure: false, unresolvedCritical: false, policyMismatch: true, duplicateEvidence: false, touchesAuth: false };
    case 'DUPLICATE_EVIDENCE':
      return { ciFailure: false, unresolvedCritical: false, policyMismatch: false, duplicateEvidence: true, touchesAuth: false };
    default:
      return { ciFailure: false, unresolvedCritical: false, policyMismatch: false, duplicateEvidence: false, touchesAuth: false };
  }
};