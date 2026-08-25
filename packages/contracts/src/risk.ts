/**
 * Canonical risk levels shared across the Hermes control plane.
 *
 * These four levels are the single source of truth for Python and TypeScript
 * risk engines, model selection, and policy gates. Python code uses the same
 * string values (case-insensitive at the edges, normalized to UPPERCASE here).
 */

/** Hermes risk levels, ordered from lowest to highest authority. */
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export const RISK_LEVELS: readonly RiskLevel[] = [
  'LOW',
  'MEDIUM',
  'HIGH',
  'CRITICAL',
] as const;

export const RISK_ORDER: Readonly<Record<RiskLevel, number>> = {
  LOW: 0,
  MEDIUM: 1,
  HIGH: 2,
  CRITICAL: 3,
} as const;

/** Case-insensitive normalization. Falls back to MEDIUM. */
export const normalizeRiskLevel = (risk: unknown): RiskLevel => {
  if (typeof risk !== 'string') return 'MEDIUM';
  const upper = risk.trim().toUpperCase();
  if (RISK_LEVELS.includes(upper as RiskLevel)) return upper as RiskLevel;
  return 'MEDIUM';
};

/** Model assignment by risk, matching Python dispatch_to_devin.py::RISK_MODEL_MAP. */
export const selectModelByRisk = (
  risk: RiskLevel,
  override?: string,
): string => {
  if (override) return override;
  if (risk === 'HIGH' || risk === 'CRITICAL') return 'swe-1-7';
  return 'glm-5-2';
};

/**
 * Map PolicyReasonCode to a canonical RiskLevel.
 *
 * This is the bridge between the fail-closed policy evaluator and the 4-level
 * Hermes risk engine used by dispatch and the gates.
 */
export const reasonCodeToRiskLevel = (reasonCode: string): RiskLevel => {
  switch (reasonCode) {
    case 'PASS':
      return 'LOW';
    case 'CI_NOT_GREEN':
    case 'DUPLICATE_EVIDENCE':
      return 'LOW';
    case 'HEAD_SHA_MISMATCH':
    case 'EVIDENCE_STALE':
      return 'MEDIUM';
    case 'POLICY_VERSION_MISMATCH':
    case 'UNRESOLVED_CRITICAL_FINDING':
      return 'CRITICAL';
    default:
      return 'MEDIUM';
  }
};
