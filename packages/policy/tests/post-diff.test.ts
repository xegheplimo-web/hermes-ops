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

describe('recalculatePostDiffRisk — keeps original risk', () => {
  it('normal file change (no sensitive path) keeps LOW', () => {
    const result = recalculatePostDiffRisk(
      ['src/utils/helpers.ts', 'README.md'],
      'LOW',
    );
    expect(result).toBe('LOW');
  });

  it('normal file change (no sensitive path) keeps CRITICAL', () => {
    const result = recalculatePostDiffRisk(
      ['src/utils/helpers.ts'],
      'CRITICAL',
    );
    expect(result).toBe('CRITICAL');
  });

  it('empty path list keeps original risk', () => {
    const result = recalculatePostDiffRisk([], 'LOW');
    expect(result).toBe('LOW');
  });

  it('path with harmless substring like "author" does not falsely trigger', () => {
    const result = recalculatePostDiffRisk(
      ['docs/AUTHOR.md', 'src/authorization.ts'],
      'LOW',
    );
    expect(result).toBe('LOW');
  });
});

// ─── recalculatePostDiffRisk — escalation ────────────────────────────────────

describe('recalculatePostDiffRisk — escalates to CRITICAL', () => {
  it('file touching "auth" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/auth/login.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "oauth" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['services/oauth/provider.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "login" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['pages/login.tsx'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "credential" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['infra/credential-manager.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "secret" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['config/secrets.env'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "token" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/token-service.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "permission" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['rbac/permissions.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "security" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['security/audit-log.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "billing" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['services/billing.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "payment" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['payment/processor.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "migration" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['db/migrations/001.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "deploy" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['deploy/production.yml'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "production" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['config/production.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "policy" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['policies/access.json'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('file touching "gate" → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/gate/config.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });
});

// ─── recalculatePostDiffRisk — multiple files ────────────────────────────────

describe('recalculatePostDiffRisk — multiple files', () => {
  it('multiple files, one sensitive → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/components/Button.tsx', 'src/utils/helpers.ts', 'src/auth/login.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('multiple files, first is sensitive → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/auth/login.ts', 'src/components/Button.tsx'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('multiple files, last is sensitive → escalates', () => {
    const result = recalculatePostDiffRisk(
      ['src/components/Button.tsx', 'src/auth/login.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });
});

// ─── recalculatePostDiffRisk — escalation overrides original class ───────────

describe('recalculatePostDiffRisk — escalation overrides original risk', () => {
  it('escalation overrides LOW → CRITICAL', () => {
    const result = recalculatePostDiffRisk(
      ['src/credentials/keys.ts'],
      'LOW',
    );
    expect(result).toBe('CRITICAL');
  });

  it('escalation overrides even if original risk is already CRITICAL', () => {
    const result = recalculatePostDiffRisk(
      ['src/credentials/keys.ts'],
      'CRITICAL',
    );
    expect(result).toBe('CRITICAL');
  });
});

// ─── recalculatePostDiffRisk — pure / deterministic ──────────────────────────

describe('recalculatePostDiffRisk — pure / deterministic', () => {
  it('returns the same result for the same input every time', () => {
    const paths = ['src/auth/login.ts'];
    const a = recalculatePostDiffRisk(paths, 'LOW');
    const b = recalculatePostDiffRisk(paths, 'LOW');
    const c = recalculatePostDiffRisk(paths, 'LOW');
    expect(a).toBe(b);
    expect(b).toBe(c);
    expect(a).toBe('CRITICAL');
  });

  it('does not mutate the input array', () => {
    const paths = ['src/auth/login.ts', 'src/utils/helpers.ts'];
    const before = [...paths];
    recalculatePostDiffRisk(paths, 'LOW');
    expect(paths).toEqual(before);
  });

  it('does not mutate the input array even when no match', () => {
    const paths = ['src/utils/helpers.ts'];
    const before = [...paths];
    recalculatePostDiffRisk(paths, 'LOW');
    expect(paths).toEqual(before);
  });
});
