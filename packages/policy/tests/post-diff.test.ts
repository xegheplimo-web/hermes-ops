import { describe, expect, it } from 'vitest';
import {
  recalculatePostDiffRisk,
  SENSITIVE_PATTERNS,
} from '../src/post-diff.js';

// ─── SENSITIVE_PATTERNS structure ────────────────────────────────────────────

describe('SENSITIVE_PATTERNS', () => {
  it('is an array of RegExp', () => {
    expect(Array.isArray(SENSITIVE_PATTERNS)).toBe(true);
    for (const p of SENSITIVE_PATTERNS) {
      expect(p).toBeInstanceOf(RegExp);
    }
  });

  it('contains all expected patterns', () => {
    const sources = SENSITIVE_PATTERNS.map((r) => r.source);
    const expected = [
      '\\bauths?\\b',
      '\\boauths?\\b',
      '\\blogins?\\b',
      '\\bcredentials?\\b',
      '\\bsecrets?\\b',
      '\\btokens?\\b',
      '\\bpermissions?\\b',
      '\\bsecurity\\b',
      '\\bbillings?\\b',
      '\\bpayments?\\b',
      '\\bmigrations?\\b',
      '\\bdeploys?\\b',
      '\\bproduction\\b',
      '\\bpolic(?:y|ies)\\b',
      '\\bgates?\\b',
    ];
    for (const s of expected) {
      expect(sources).toContain(s);
    }
  });
});

// ─── recalculatePostDiffRisk — no escalation ─────────────────────────────────

describe('recalculatePostDiffRisk — keeps original class', () => {
  it('normal file change (no sensitive path) keeps auto-eligible', () => {
    const result = recalculatePostDiffRisk(
      ['src/utils/helpers.ts', 'README.md'],
      'auto-eligible',
    );
    expect(result).toBe('auto-eligible');
  });

  it('normal file change (no sensitive path) keeps human-required', () => {
    const result = recalculatePostDiffRisk(
      ['src/utils/helpers.ts'],
      'human-required',
    );
    expect(result).toBe('human-required');
  });

  it('empty path list keeps original class', () => {
    const result = recalculatePostDiffRisk([], 'auto-eligible');
    expect(result).toBe('auto-eligible');
  });

  it('path with harmless substring like "author" does not falsely trigger', () => {
    const result = recalculatePostDiffRisk(
      ['docs/AUTHOR.md', 'src/authorization.ts'],
      'auto-eligible',
    );
    expect(result).toBe('auto-eligible');
  });
});

// ─── recalculatePostDiffRisk — escalation ────────────────────────────────────

describe('recalculatePostDiffRisk — escalates to human-required', () => {
  it('file touching "auth" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/auth/login.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "oauth" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['services/oauth/provider.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "login" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['pages/login.tsx'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "credential" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['infra/credential-manager.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "secret" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['config/secrets.env'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "token" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/token-service.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "permission" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['rbac/permissions.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "security" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['security/audit-log.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "billing" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['services/billing.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "payment" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['payment/processor.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "migration" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['db/migrations/001.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "deploy" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['deploy/production.yml'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "production" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['config/production.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "policy" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['policies/access.json'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('file touching "gate" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/gate/config.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });
});

// ─── recalculatePostDiffRisk — multiple files ────────────────────────────────

describe('recalculatePostDiffRisk — multiple files', () => {
  it('multiple files, one sensitive → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/components/Button.tsx', 'src/utils/helpers.ts', 'src/auth/login.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('multiple files, first is sensitive → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/auth/login.ts', 'src/components/Button.tsx'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('multiple files, last is sensitive → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/components/Button.tsx', 'src/auth/login.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });
});

// ─── recalculatePostDiffRisk — escalation overrides original class ───────────

describe('recalculatePostDiffRisk — escalation overrides original class', () => {
  it('escalation overrides auto-eligible → human-required', () => {
    const result = recalculatePostDiffRisk(
      ['src/credentials/keys.ts'],
      'auto-eligible',
    );
    expect(result).toBe('human-required');
  });

  it('escalation overrides even if original class is already human-required', () => {
    const result = recalculatePostDiffRisk(
      ['src/credentials/keys.ts'],
      'human-required',
    );
    expect(result).toBe('human-required');
  });
});

// ─── recalculatePostDiffRisk — pure / deterministic ──────────────────────────

describe('recalculatePostDiffRisk — pure / deterministic', () => {
  it('returns the same result for the same input every time', () => {
    const paths = ['src/auth/login.ts'];
    const a = recalculatePostDiffRisk(paths, 'auto-eligible');
    const b = recalculatePostDiffRisk(paths, 'auto-eligible');
    const c = recalculatePostDiffRisk(paths, 'auto-eligible');
    expect(a).toBe(b);
    expect(b).toBe(c);
    expect(a).toBe('human-required');
  });

  it('does not mutate the input array', () => {
    const paths = ['src/auth/login.ts', 'src/utils/helpers.ts'];
    const before = [...paths];
    recalculatePostDiffRisk(paths, 'auto-eligible');
    expect(paths).toEqual(before);
  });

  it('does not mutate the input array even when no match', () => {
    const paths = ['src/utils/helpers.ts'];
    const before = [...paths];
    recalculatePostDiffRisk(paths, 'auto-eligible');
    expect(paths).toEqual(before);
  });
});