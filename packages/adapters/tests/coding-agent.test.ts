import { describe, expect, it } from 'vitest';
import {
  AdapterError,
  AGENT_RUN_STATUSES,
  RISK_LEVELS,
  TERMINAL_AGENT_RUN_STATUSES,
  isNonEmptyString,
  validateCreateRunInput,
  validateRunId,
} from '../src/index.js';

describe('shared AgentRun types and constants', () => {
  it('exposes the six agent run statuses', () => {
    expect(AGENT_RUN_STATUSES).toEqual([
      'pending',
      'running',
      'succeeded',
      'failed',
      'cancelled',
      'timed_out',
    ]);
  });

  it('terminal statuses are a subset of all statuses', () => {
    for (const t of TERMINAL_AGENT_RUN_STATUSES) {
      expect(AGENT_RUN_STATUSES).toContain(t);
    }
    expect(TERMINAL_AGENT_RUN_STATUSES).not.toContain('running');
    expect(TERMINAL_AGENT_RUN_STATUSES).not.toContain('pending');
  });

  it('exposes the four risk levels in severity order', () => {
    expect(RISK_LEVELS).toEqual(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']);
  });
});

describe('AdapterError', () => {
  it('carries a stable code and message', () => {
    const e = new AdapterError('RUN_NOT_FOUND', 'no such run');
    expect(e).toBeInstanceOf(Error);
    expect(e.name).toBe('AdapterError');
    expect(e.code).toBe('RUN_NOT_FOUND');
    expect(e.message).toBe('no such run');
  });

  it('carries an optional cause', () => {
    const cause = new Error('boom');
    const e = new AdapterError('TRANSPORT_ERROR', 'failed', cause);
    expect(e.cause).toBe(cause);
  });
});

describe('isNonEmptyString', () => {
  it('accepts a non-empty string', () => {
    expect(isNonEmptyString('x')).toBe(true);
  });
  it('rejects an empty string', () => {
    expect(isNonEmptyString('')).toBe(false);
  });
  it('rejects non-strings', () => {
    expect(isNonEmptyString(1)).toBe(false);
    expect(isNonEmptyString(null)).toBe(false);
    expect(isNonEmptyString(undefined)).toBe(false);
  });
});

describe('validateCreateRunInput', () => {
  const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
  const valid = () => ({
    prompt: 'p',
    repository: { owner: 'o', name: 'n' },
    headSha: HEAD_SHA,
  });

  it('accepts valid input without throwing', () => {
    expect(() => validateCreateRunInput(valid())).not.toThrow();
  });

  it('accepts a positive prNumber', () => {
    expect(() => validateCreateRunInput({ ...valid(), prNumber: 5 })).not.toThrow();
  });

  it('rejects an empty prompt', () => {
    expect(() => validateCreateRunInput({ ...valid(), prompt: '' })).toThrow(AdapterError);
  });

  it('rejects an invalid headSha', () => {
    expect(() => validateCreateRunInput({ ...valid(), headSha: 'xyz' })).toThrow(AdapterError);
  });

  it('rejects a non-positive prNumber', () => {
    expect(() => validateCreateRunInput({ ...valid(), prNumber: 0 })).toThrow(AdapterError);
    expect(() => validateCreateRunInput({ ...valid(), prNumber: -1 })).toThrow(AdapterError);
  });

  it('rejects a non-positive budgetMs', () => {
    expect(() => validateCreateRunInput({ ...valid(), budgetMs: 0 })).toThrow(AdapterError);
    expect(() => validateCreateRunInput({ ...valid(), budgetMs: -1 })).toThrow(AdapterError);
  });

  it('rejects an invalid riskLevel', () => {
    expect(() =>
      validateCreateRunInput({ ...valid(), riskLevel: 'EXTREME' as never }),
    ).toThrow(AdapterError);
  });

  it('accepts all valid risk levels', () => {
    for (const r of RISK_LEVELS) {
      expect(() => validateCreateRunInput({ ...valid(), riskLevel: r })).not.toThrow();
    }
  });
});

describe('validateRunId', () => {
  it('accepts a non-empty runId', () => {
    expect(() => validateRunId('r-1')).not.toThrow();
  });
  it('rejects an empty runId', () => {
    expect(() => validateRunId('')).toThrow(AdapterError);
  });
});
