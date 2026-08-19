/**
 * Stable evidence identity.
 *
 * The identity is a deterministic SHA-256 over a canonical JSON serialization of
 * a validated manifest. The same manifest always yields the same identity, which
 * lets the policy evaluator (and later phases) deduplicate and audit evidence.
 */

import { createHash } from 'node:crypto';
import type { EvidenceManifest } from './manifest.js';

/**
 * Canonicalize a manifest into a deterministic JSON string.
 *
 * Rules:
 *  - Object keys are sorted lexicographically.
 *  - No whitespace.
 *  - `undefined` optional fields are omitted.
 *  - Arrays preserve order (artifact order is significant).
 */
export const canonicalizeManifest = (manifest: EvidenceManifest): string => {
  const json = JSON.stringify(manifest, (_key, value) => {
    if (value === undefined) return undefined;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const sorted: Record<string, unknown> = {};
      for (const k of Object.keys(value as Record<string, unknown>).sort()) {
        const v = (value as Record<string, unknown>)[k];
        if (v !== undefined) sorted[k] = v;
      }
      return sorted;
    }
    return value;
  });
  return json ?? '';
};

/**
 * Compute a stable SHA-256 identity for a validated manifest.
 * Returns a 64-char lowercase hex digest.
 */
export const computeEvidenceIdentity = (manifest: EvidenceManifest): string => {
  const canon = canonicalizeManifest(manifest);
  return createHash('sha256').update(canon, 'utf8').digest('hex');
};
