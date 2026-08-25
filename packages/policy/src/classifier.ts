/**
 * Risk Classifier — maps policy reason codes to canonical 4-level Hermes risk.
 *
 * This unifies the TypeScript policy evaluator with the Python risk engine
 * (final_risk.py / task_classifier.py), which both speak LOW/MEDIUM/HIGH/CRITICAL.
 */

import {
  type RiskLevel,
  RISK_ORDER,
  reasonCodeToRiskLevel,
} from '@hermes-ops/contracts';
import type { PolicyReasonCode, PolicyResult } from './evaluator.js';

export type { RiskLevel } from '@hermes-ops/contracts';

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
 *   1. CI failure → LOW (repairable)
 *   2. Unresolved critical finding → CRITICAL
 *   3. Policy version mismatch → CRITICAL
 *   4. Duplicate evidence → LOW (repairable)
 *   5. Touches auth/credentials → HIGH
 *   6. No signals set → LOW (no risk)
 */
export const classifyRisk = (signals: RiskSignal): RiskLevel => {
  if (signals.ciFailure) return 'LOW';
  if (signals.unresolvedCritical) return 'CRITICAL';
  if (signals.policyMismatch) return 'CRITICAL';
  if (signals.duplicateEvidence) return 'LOW';
  if (signals.touchesAuth) return 'HIGH';
  return 'LOW';
};

/**
 * Map known {@link PolicyReasonCode}s to a canonical {@link RiskLevel}.
 */
export const classifyFromPolicyResult = (result: PolicyResult): RiskLevel => {
  return reasonCodeToRiskLevel(result.reasonCode);
};

/**
 * Compare two risk levels. Returns the higher one.
 */
export const maxRisk = (a: RiskLevel, b: RiskLevel): RiskLevel => {
  return RISK_ORDER[a] >= RISK_ORDER[b] ? a : b;
};
