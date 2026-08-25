/**
 * Post-diff Risk Recalculation — escalates risk when changed file paths touch
 * sensitive areas (auth, credentials, secrets, etc.).
 *
 * Pure function: no side effects, deterministic.
 *
 * Aligned with Python final_risk.py: any changed path that matches a sensitive
 * pattern escalates the final risk toward CRITICAL.
 */

import { type RiskLevel, RISK_ORDER } from '@hermes-ops/contracts';

/**
 * Regular expressions that match file paths touching sensitive areas.
 * Each pattern is case-insensitive, word-boundary anchored, and supports
 * optional plural forms (e.g. `secret` matches both `secret` and `secrets`,
 * `policy` matches both `policy` and `policies`).
 */
export const SENSITIVE_PATTERNS: ReadonlyArray<RegExp> = [
  /\bauths?\b/i,
  /\boauths?\b/i,
  /\blogins?\b/i,
  /\bcredentials?\b/i,
  /\bsecrets?\b/i,
  /\btokens?\b/i,
  /\bpermissions?\b/i,
  /\bsecurity\b/i,
  /\bbillings?\b/i,
  /\bpayments?\b/i,
  /\bmigrations?\b/i,
  /\bdeploys?\b/i,
  /\bproduction\b/i,
  /\bpolic(?:y|ies)\b/i,
  /\bgates?\b/i,
] as const;

/**
 * Check whether any path in `changedPaths` matches a sensitive pattern.
 *
 * Pure function — no side effects.
 */
export const matchesSensitivePath = (changedPaths: string[]): boolean =>
  changedPaths.some((p) =>
    SENSITIVE_PATTERNS.some((pattern) => pattern.test(p)),
  );

/**
 * Recalculate risk based on the actual content of a diff.
 *
 * When any changed file path touches a sensitive area, the risk is escalated
 * to CRITICAL. Otherwise the original risk is returned unchanged.
 */
export const recalculatePostDiffRisk = (
  changedPaths: string[],
  originalRisk: RiskLevel,
): RiskLevel => {
  if (matchesSensitivePath(changedPaths)) {
    return 'CRITICAL';
  }
  return originalRisk;
};

/**
 * Clamp a candidate risk escalation to a cap so we never exceed the highest
 * meaningful level.
 */
export const escalateRisk = (current: RiskLevel, target: RiskLevel): RiskLevel => {
  return RISK_ORDER[current] >= RISK_ORDER[target] ? current : target;
};
