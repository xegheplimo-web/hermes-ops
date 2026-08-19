import { describe, expect, it } from 'vitest';
import { createHmac } from 'node:crypto';
import {
  createDeliveryDedupe,
  verifyGitHubWebhookSignature,
} from '../src/index.js';

const SECRET = Buffer.from('super-secret-webhook-token', 'utf8');
const PAYLOAD = Buffer.from('{"action":"opened","number":42}', 'utf8');

const sign = (payload: Uint8Array, secret: Uint8Array = SECRET): string =>
  `sha256=${createHmac('sha256', secret).update(payload).digest('hex')}`;

describe('verifyGitHubWebhookSignature — valid signatures', () => {
  it('accepts a correct signature over the raw payload', () => {
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: sign(PAYLOAD),
      secret: SECRET,
    });
    expect(r.ok).toBe(true);
  });

  it('accepts a signature for an empty payload', () => {
    const empty = Buffer.alloc(0);
    const r = verifyGitHubWebhookSignature({
      rawPayload: empty,
      signatureHeader: sign(empty),
      secret: SECRET,
    });
    expect(r.ok).toBe(true);
  });

  it('accepts a signature for a large payload', () => {
    const big = Buffer.from('x'.repeat(100_000), 'utf8');
    const r = verifyGitHubWebhookSignature({
      rawPayload: big,
      signatureHeader: sign(big),
      secret: SECRET,
    });
    expect(r.ok).toBe(true);
  });
});

describe('verifyGitHubWebhookSignature — invalid signatures', () => {
  it('rejects a wrong secret', () => {
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: sign(PAYLOAD, Buffer.from('wrong-secret', 'utf8')),
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MISMATCH');
  });

  it('rejects a tampered payload', () => {
    const tampered = Buffer.from('{"action":"closed","number":42}', 'utf8');
    const r = verifyGitHubWebhookSignature({
      rawPayload: tampered,
      signatureHeader: sign(PAYLOAD),
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MISMATCH');
  });

  it('rejects a missing sha256= prefix', () => {
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: createHmac('sha256', SECRET).update(PAYLOAD).digest('hex'),
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MALFORMED_HEADER');
  });

  it('rejects an uppercase hex digest (strict format)', () => {
    const hex = createHmac('sha256', SECRET).update(PAYLOAD).digest('hex');
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: `sha256=${hex.toUpperCase()}`,
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MALFORMED_HEADER');
  });

  it('rejects a too-short digest', () => {
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: 'sha256=abc',
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MALFORMED_HEADER');
  });

  it('rejects a too-long digest', () => {
    const hex = createHmac('sha256', SECRET).update(PAYLOAD).digest('hex');
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: `sha256=${hex}00`,
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MALFORMED_HEADER');
  });

  it('rejects a non-hex digest', () => {
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: `sha256=${'z'.repeat(64)}`,
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MALFORMED_HEADER');
  });

  it('rejects an empty header', () => {
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: '',
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MALFORMED_HEADER');
  });

  it('rejects a sha1= prefix (legacy)', () => {
    const hex = createHmac('sha1', SECRET).update(PAYLOAD).digest('hex');
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: `sha1=${hex}`,
      secret: SECRET,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('MALFORMED_HEADER');
  });

  it('never surfaces the secret in the result', () => {
    const r = verifyGitHubWebhookSignature({
      rawPayload: PAYLOAD,
      signatureHeader: 'sha256=' + '0'.repeat(64),
      secret: SECRET,
    });
    expect(JSON.stringify(r)).not.toContain(SECRET.toString('utf8'));
  });
});

describe('createDeliveryDedupe — bounded in-memory dedupe', () => {
  it('rejects a non-positive maxSize', () => {
    expect(() => createDeliveryDedupe({ maxSize: 0 })).toThrow(TypeError);
    expect(() => createDeliveryDedupe({ maxSize: -1 })).toThrow(TypeError);
    expect(() => createDeliveryDedupe({ maxSize: 1.5 })).toThrow(TypeError);
  });

  it('marks and detects a delivery id', () => {
    const d = createDeliveryDedupe({ maxSize: 16 });
    expect(d.has('d-1')).toBe(false);
    expect(d.checkAndMark('d-1')).toBe(false);
    expect(d.has('d-1')).toBe(true);
    expect(d.checkAndMark('d-1')).toBe(true);
    expect(d.size).toBe(1);
  });

  it('mark is idempotent', () => {
    const d = createDeliveryDedupe({ maxSize: 16 });
    d.mark('d-1');
    d.mark('d-1');
    expect(d.size).toBe(1);
  });

  it('evicts the oldest entry when at capacity (FIFO, bounded)', () => {
    const d = createDeliveryDedupe({ maxSize: 3 });
    d.mark('d-1');
    d.mark('d-2');
    d.mark('d-3');
    expect(d.size).toBe(3);
    expect(d.has('d-1')).toBe(true);
    // Adding a fourth evicts the oldest (d-1).
    d.mark('d-4');
    expect(d.size).toBe(3);
    expect(d.has('d-1')).toBe(false);
    expect(d.has('d-2')).toBe(true);
    expect(d.has('d-3')).toBe(true);
    expect(d.has('d-4')).toBe(true);
  });

  it('checkAndMark evicts on overflow and reports seen correctly', () => {
    const d = createDeliveryDedupe({ maxSize: 2 });
    expect(d.checkAndMark('a')).toBe(false);
    expect(d.checkAndMark('b')).toBe(false);
    expect(d.checkAndMark('c')).toBe(false); // evicts 'a'
    expect(d.has('a')).toBe(false);
    expect(d.has('b')).toBe(true);
    expect(d.has('c')).toBe(true);
    expect(d.checkAndMark('b')).toBe(true); // 'b' still present
  });

  it('ignores empty/invalid delivery ids', () => {
    const d = createDeliveryDedupe({ maxSize: 4 });
    d.mark('');
    expect(d.size).toBe(0);
    expect(d.checkAndMark('')).toBe(false);
    expect(d.size).toBe(0);
  });

  it('exposes maxSize', () => {
    const d = createDeliveryDedupe({ maxSize: 7 });
    expect(d.maxSize).toBe(7);
  });
});
