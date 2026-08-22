/**
 * Post-diff Risk Recalculation — escalates risk to 'human-required' when
 * changed file paths touch sensitive areas (auth, credentials, secrets, etc.).
 *
 * Pure function: no side effects, deterministic.
 */

import type { RiskClass } from './classifier.js';

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
const matchesSensitivePath = (changedPaths: string[]): boolean =>
  changedPaths.some((p) =>
    SENSITIVE_PATTERNS.some((pattern) => pattern.test(p)),
  );

/**
 * Recalculate risk class based on the actual content of a diff.
 *
 * When any changed file path touches a sensitive area (auth, credentials,
 * secrets, etc.) the risk is escalated to 'human-required'. Otherwise the
 * original class is returned unchanged.
 *
 * @param changedPaths — list of file paths changed in the diff (may be empty)
 * @param originalClass — the risk class determined before diff inspection
 * @returns escalated class if sensitive paths are found, originalClass otherwise
 */
export const recalculatePostDiffRisk = (
  changedPaths: string[],
  originalClass: RiskClass,
): RiskClass => {
  if (matchesSensitivePath(changedPaths)) {
    return 'human-required';
  }
  return originalClass;
};