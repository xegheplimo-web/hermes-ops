/**
 * GitHub webhook verification and delivery-id deduplication.
 *
 * Phase 2 adapter contract. Pure and testable: no HTTP server, no network
 * calls, no credentials baked in. The webhook secret is passed in by the
 * caller as raw bytes and is NEVER logged or echoed back in errors/results.
 *
 * Two concerns live here:
 *
 *  1. `verifyGitHubWebhookSignature` — HMAC-SHA256 verification of a raw
 *     payload against the `X-Hub-Signature-256` header, using a strict
 *     `sha256=<hex>` format and a constant-time comparison.
 *  2. `createDeliveryDedupe` — a bounded in-memory dedupe for the
 *     `X-GitHub-Delivery` id. The shape is interface-driven so a DB-backed
 *     implementation can replace it later without touching call sites.
 */

import { createHmac, timingSafeEqual } from 'node:crypto';

/* -------------------------------------------------------------------------- */
/* Signature verification                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Strict signature header format: `sha256=` followed by exactly 64 lowercase
 * hex chars. GitHub sends lowercase hex; we reject anything else to avoid
 * ambiguity and lenient parsing that could mask attacks.
 */
const SIGNATURE_PREFIX = 'sha256=';
const HEX64_RE = /^[0-9a-f]{64}$/;

export interface VerifySignatureOptions {
  /**
   * Raw webhook payload bytes exactly as received on the wire. Re-serializing
   * JSON would change byte ordering and break the HMAC; callers MUST pass the
   * original raw body.
   */
  readonly rawPayload: Uint8Array;
  /**
   * The `X-Hub-Signature-256` header value, e.g. `sha256=...`. Treated as
   * untrusted input.
   */
  readonly signatureHeader: string;
  /**
   * The webhook secret as raw bytes (UTF-8 of the shared secret). NEVER
   * logged, NEVER returned in any result or error.
   */
  readonly secret: Uint8Array;
}

export type VerifySignatureResult =
  | { readonly ok: true }
  | {
      readonly ok: false;
      readonly reason: 'MALFORMED_HEADER' | 'LENGTH_MISMATCH' | 'MISMATCH';
    };

/**
 * Verify a GitHub webhook signature.
 *
 * Strict format: the header MUST be exactly `sha256=<64 lowercase hex>`.
 * Comparison is constant-time via `crypto.timingSafeEqual`, and a length
 * mismatch short-circuits to a safe non-secret-dependent rejection (the
 * buffers must be equal length before `timingSafeEqual` will accept them).
 *
 * The secret is never returned or surfaced in any error. Errors are stable
 * reason strings only.
 */
export const verifyGitHubWebhookSignature = (
  options: VerifySignatureOptions,
): VerifySignatureResult => {
  const { rawPayload, signatureHeader, secret } = options;

  // Strict header parsing. Any deviation is a hard reject — no lenient
  // trimming, no case folding, no alternate prefixes.
  if (typeof signatureHeader !== 'string') {
    return { ok: false, reason: 'MALFORMED_HEADER' };
  }
  if (!signatureHeader.startsWith(SIGNATURE_PREFIX)) {
    return { ok: false, reason: 'MALFORMED_HEADER' };
  }
  const hex = signatureHeader.slice(SIGNATURE_PREFIX.length);
  if (!HEX64_RE.test(hex)) {
    return { ok: false, reason: 'MALFORMED_HEADER' };
  }

  // Compute the expected HMAC over the raw payload.
  const expected = createHmac('sha256', secret).update(rawPayload).digest();

  // Convert the hex digest from the header to bytes for a constant-time
  // comparison. `timingSafeEqual` requires equal-length buffers.
  const provided = Buffer.from(hex, 'utf8'); // 64 ASCII hex bytes
  const expectedHex = expected.toString('hex'); // 64 lowercase hex chars

  if (provided.length !== expectedHex.length) {
    // Should not happen given HEX64_RE, but guard defensively.
    return { ok: false, reason: 'LENGTH_MISMATCH' };
  }

  // Compare the hex strings in constant time. We compare the ASCII hex
  // representation rather than the raw digest bytes so a malformed-but-64-char
  // header (e.g. non-hex) is handled by the regex above, not by Buffer.from
  // producing a shorter buffer.
  const expectedBuf = Buffer.from(expectedHex, 'utf8');
  if (!timingSafeEqual(provided, expectedBuf)) {
    return { ok: false, reason: 'MISMATCH' };
  }

  return { ok: true };
};

/* -------------------------------------------------------------------------- */
/* Delivery-id dedupe                                                         */
/* -------------------------------------------------------------------------- */

/**
 * Bounded delivery-id dedupe. Interface-driven so a DB-backed implementation
 * (e.g. a `webhook_deliveries` table) can replace the in-memory one without
 * changing call sites.
 *
 * Semantics:
 *  - `has(id)` — true if the delivery id has been recorded.
 *  - `mark(id)` — record a delivery id. If at capacity, the oldest entry is
 *    evicted (FIFO, bounded). Idempotent: marking twice is a no-op.
 *  - `checkAndMark(id)` — convenience: returns true if already seen, otherwise
 *    marks and returns false. This is the typical webhook handler flow.
 *
 * The in-memory implementation is bounded by `maxSize`. A later DB-backed
 * implementation would rely on a UNIQUE constraint + TTL instead.
 */
export interface DeliveryDedupe {
  has(deliveryId: string): boolean;
  mark(deliveryId: string): void;
  checkAndMark(deliveryId: string): boolean;
  readonly size: number;
  readonly maxSize: number;
}

export interface CreateDeliveryDedupeOptions {
  /** Maximum number of delivery ids to retain. Must be a positive integer. */
  readonly maxSize: number;
}

/**
 * Create a bounded in-memory delivery-id dedupe.
 *
 * Uses a `Map` (insertion-ordered) so eviction is FIFO and `has`/`mark` are
 * O(1). Suitable as a stand-in until a DB-backed dedupe is wired in.
 */
export const createDeliveryDedupe = (
  options: CreateDeliveryDedupeOptions,
): DeliveryDedupe => {
  if (!Number.isInteger(options.maxSize) || options.maxSize <= 0) {
    throw new TypeError('maxSize must be a positive integer');
  }
  const seen = new Map<string, true>();

  const evictIfNeeded = (): void => {
    while (seen.size > options.maxSize) {
      // Map iteration is insertion-ordered; the first key is the oldest.
      const oldest = seen.keys().next();
      if (oldest.done || oldest.value === undefined) break;
      seen.delete(oldest.value);
    }
  };

  return {
    maxSize: options.maxSize,
    get size(): number {
      return seen.size;
    },
    has(deliveryId: string): boolean {
      return seen.has(deliveryId);
    },
    mark(deliveryId: string): void {
      if (typeof deliveryId !== 'string' || deliveryId.length === 0) return;
      if (seen.has(deliveryId)) return;
      seen.set(deliveryId, true);
      evictIfNeeded();
    },
    checkAndMark(deliveryId: string): boolean {
      if (typeof deliveryId !== 'string' || deliveryId.length === 0) {
        // An empty/invalid delivery id cannot be deduped; treat as not-seen
        // and do not mark. Callers should reject these before reaching here.
        return false;
      }
      if (seen.has(deliveryId)) return true;
      seen.set(deliveryId, true);
      evictIfNeeded();
      return false;
    },
  };
};
