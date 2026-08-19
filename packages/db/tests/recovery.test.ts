import { describe, expect, it } from 'vitest';
import {
  DEFAULT_BACKOFF_BASE_MS,
  DEFAULT_BACKOFF_MAX_MS,
  DEFAULT_MAX_ATTEMPTS,
  computeBackoffMs,
  computeNextAvailableAt,
  computeStaleLockCutoff,
  shouldRetry,
} from '../src/index.js';

const NOW = new Date('2026-08-19T12:00:00.000Z');

describe('computeBackoffMs — exponential, capped', () => {
  it('returns baseMs for attempt 1', () => {
    expect(computeBackoffMs(1, { baseMs: 1000, maxMs: 60_000 })).toBe(1000);
  });

  it('doubles each attempt', () => {
    expect(computeBackoffMs(1, { baseMs: 1000, maxMs: 60_000 })).toBe(1000);
    expect(computeBackoffMs(2, { baseMs: 1000, maxMs: 60_000 })).toBe(2000);
    expect(computeBackoffMs(3, { baseMs: 1000, maxMs: 60_000 })).toBe(4000);
    expect(computeBackoffMs(4, { baseMs: 1000, maxMs: 60_000 })).toBe(8000);
  });

  it('is capped at maxMs', () => {
    expect(computeBackoffMs(20, { baseMs: 1000, maxMs: 5000 })).toBe(5000);
  });

  it('respects defaults', () => {
    expect(computeBackoffMs(1)).toBe(DEFAULT_BACKOFF_BASE_MS);
    expect(computeBackoffMs(50)).toBe(DEFAULT_BACKOFF_MAX_MS);
  });

  it('never exceeds maxMs for any attempt', () => {
    for (let a = 1; a <= 30; a++) {
      const d = computeBackoffMs(a, { baseMs: 500, maxMs: 4000 });
      expect(d).toBeLessThanOrEqual(4000);
      expect(d).toBeGreaterThanOrEqual(0);
    }
  });
});

describe('computeBackoffMs — deterministic jitter', () => {
  it('is deterministic for the same (seed, attempt)', () => {
    const a = computeBackoffMs(3, { baseMs: 1000, maxMs: 60_000, jitterSeed: 42 });
    const b = computeBackoffMs(3, { baseMs: 1000, maxMs: 60_000, jitterSeed: 42 });
    expect(a).toBe(b);
  });

  it('differs across attempts for the same seed', () => {
    const a = computeBackoffMs(1, { baseMs: 1000, maxMs: 60_000, jitterSeed: 42 });
    const b = computeBackoffMs(2, { baseMs: 1000, maxMs: 60_000, jitterSeed: 42 });
    // Not guaranteed to differ on every seed, but across attempts 1..6 at
    // least two should differ.
    const delays = new Set<number>();
    for (let i = 1; i <= 6; i++) {
      delays.add(computeBackoffMs(i, { baseMs: 1000, maxMs: 60_000, jitterSeed: 42 }));
    }
    expect(delays.size).toBeGreaterThan(1);
    // Sanity: a and b are within [0, capped].
    expect(a).toBeGreaterThanOrEqual(0);
    expect(b).toBeGreaterThanOrEqual(0);
    expect(a).toBeLessThanOrEqual(60_000);
    expect(b).toBeLessThanOrEqual(60_000);
  });

  it('jitter is bounded by the capped exponential', () => {
    for (let a = 1; a <= 20; a++) {
      const capped = Math.min(1000 * 2 ** (a - 1), 8000);
      const jittered = computeBackoffMs(a, {
        baseMs: 1000,
        maxMs: 8000,
        jitterSeed: 7,
      });
      expect(jittered).toBeGreaterThanOrEqual(0);
      expect(jittered).toBeLessThanOrEqual(capped);
    }
  });
});

describe('computeBackoffMs — input validation', () => {
  it('rejects non-positive attempts', () => {
    expect(() => computeBackoffMs(0)).toThrow(TypeError);
    expect(() => computeBackoffMs(-1)).toThrow(TypeError);
    expect(() => computeBackoffMs(1.5)).toThrow(TypeError);
  });

  it('rejects non-positive baseMs', () => {
    expect(() => computeBackoffMs(1, { baseMs: 0 })).toThrow(TypeError);
    expect(() => computeBackoffMs(1, { baseMs: -1 })).toThrow(TypeError);
  });

  it('rejects maxMs < baseMs', () => {
    expect(() => computeBackoffMs(1, { baseMs: 1000, maxMs: 500 })).toThrow(TypeError);
  });
});

describe('computeNextAvailableAt — deterministic timestamps', () => {
  it('returns now + backoff for retryable attempts', () => {
    const next = computeNextAvailableAt(1, NOW, { baseMs: 1000, maxMs: 60_000, maxAttempts: 5 });
    expect(next).toEqual(new Date(NOW.getTime() + 1000));
  });

  it('returns null when attempt reaches maxAttempts', () => {
    expect(computeNextAvailableAt(5, NOW, { maxAttempts: 5 })).toBeNull();
  });

  it('returns null when attempt exceeds maxAttempts', () => {
    expect(computeNextAvailableAt(6, NOW, { maxAttempts: 5 })).toBeNull();
  });

  it('is deterministic given the same now', () => {
    const a = computeNextAvailableAt(2, NOW, { baseMs: 1000, maxMs: 60_000 });
    const b = computeNextAvailableAt(2, NOW, { baseMs: 1000, maxMs: 60_000 });
    expect(a).toEqual(b);
  });

  it('respects the cap', () => {
    const next = computeNextAvailableAt(20, NOW, {
      baseMs: 1000,
      maxMs: 5000,
      maxAttempts: 30,
    });
    expect(next).toEqual(new Date(NOW.getTime() + 5000));
  });

  it('does not mutate the input Date', () => {
    const original = new Date(NOW);
    computeNextAvailableAt(1, NOW, { baseMs: 1000, maxMs: 60_000 });
    expect(NOW).toEqual(original);
  });
});

describe('shouldRetry — bounded attempts', () => {
  it('returns true while attempt < maxAttempts', () => {
    expect(shouldRetry(0, 5)).toBe(true);
    expect(shouldRetry(4, 5)).toBe(true);
  });

  it('returns false at attempt >= maxAttempts', () => {
    expect(shouldRetry(5, 5)).toBe(false);
    expect(shouldRetry(6, 5)).toBe(false);
  });

  it('uses DEFAULT_MAX_ATTEMPTS when omitted', () => {
    expect(shouldRetry(DEFAULT_MAX_ATTEMPTS - 1)).toBe(true);
    expect(shouldRetry(DEFAULT_MAX_ATTEMPTS)).toBe(false);
  });

  it('returns false for invalid inputs', () => {
    expect(shouldRetry(-1, 5)).toBe(false);
    expect(shouldRetry(1, 0)).toBe(false);
    expect(shouldRetry(1, -1)).toBe(false);
    expect(shouldRetry(1.5, 5)).toBe(false);
  });
});

describe('computeStaleLockCutoff — deterministic', () => {
  it('returns now - staleAfterMs', () => {
    const cutoff = computeStaleLockCutoff(NOW, 60_000);
    expect(cutoff).toEqual(new Date(NOW.getTime() - 60_000));
  });

  it('truncates fractional milliseconds', () => {
    const cutoff = computeStaleLockCutoff(NOW, 60_000.9);
    expect(cutoff).toEqual(new Date(NOW.getTime() - 60_000));
  });

  it('is deterministic given the same now', () => {
    const a = computeStaleLockCutoff(NOW, 30_000);
    const b = computeStaleLockCutoff(NOW, 30_000);
    expect(a).toEqual(b);
  });

  it('does not mutate the input Date', () => {
    const original = new Date(NOW);
    computeStaleLockCutoff(NOW, 60_000);
    expect(NOW).toEqual(original);
  });

  it('rejects invalid dates', () => {
    expect(() => computeStaleLockCutoff(new Date('nope'), 60_000)).toThrow(TypeError);
  });

  it('rejects non-positive staleAfterMs', () => {
    expect(() => computeStaleLockCutoff(NOW, 0)).toThrow(TypeError);
    expect(() => computeStaleLockCutoff(NOW, -1)).toThrow(TypeError);
    expect(() => computeStaleLockCutoff(NOW, Infinity)).toThrow(TypeError);
  });
});
