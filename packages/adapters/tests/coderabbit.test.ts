import { describe, expect, it } from 'vitest';
import { normalizeCodeRabbitFindings } from '../src/index.js';

describe('normalizeCodeRabbitFindings — valid input', () => {
  it('normalizes an object with a findings array, preserving only id/severity/resolved', () => {
    const input = {
      findings: [
        {
          id: 'cr-1',
          severity: 'critical',
          resolved: false,
          // These must be dropped:
          file: 'src/a.ts',
          line: 10,
          comment: 'fix this',
          suggestion: 'use foo()',
        },
        {
          id: 'cr-2',
          severity: 'low',
          resolved: true,
          extra: { anything: 'else' },
        },
      ],
    };
    const r = normalizeCodeRabbitFindings(input);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.findings.findings).toEqual([
        { id: 'cr-1', severity: 'critical', resolved: false },
        { id: 'cr-2', severity: 'low', resolved: true },
      ]);
    }
  });

  it('accepts a bare array of findings', () => {
    const r = normalizeCodeRabbitFindings([
      { id: 'x', severity: 'info', resolved: true },
    ]);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.findings.findings).toHaveLength(1);
      expect(r.findings.findings[0]).toEqual({
        id: 'x',
        severity: 'info',
        resolved: true,
      });
    }
  });

  it('accepts all severity levels', () => {
    const severities = ['critical', 'high', 'medium', 'low', 'info'] as const;
    const r = normalizeCodeRabbitFindings(
      severities.map((s, i) => ({ id: `id-${i}`, severity: s, resolved: false })),
    );
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.findings.findings.map((f) => f.severity)).toEqual(severities);
    }
  });

  it('accepts an empty findings array', () => {
    const r = normalizeCodeRabbitFindings({ findings: [] });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.findings.findings).toHaveLength(0);
  });
});

describe('normalizeCodeRabbitFindings — malformed input', () => {
  it('rejects a non-object, non-array input', () => {
    const r = normalizeCodeRabbitFindings('not an object');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('MALFORMED');
  });

  it('rejects null', () => {
    const r = normalizeCodeRabbitFindings(null);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('MALFORMED');
  });

  it('rejects an object without a findings array', () => {
    const r = normalizeCodeRabbitFindings({ foo: 'bar' });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('FINDINGS_NOT_ARRAY');
  });

  it('rejects a findings array containing a non-object', () => {
    const r = normalizeCodeRabbitFindings({ findings: ['nope'] });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('FINDING_MALFORMED');
  });

  it('rejects a finding missing id', () => {
    const r = normalizeCodeRabbitFindings({
      findings: [{ severity: 'low', resolved: false }],
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('FINDING_MISSING_ID');
  });

  it('rejects a finding with an empty id', () => {
    const r = normalizeCodeRabbitFindings({
      findings: [{ id: '', severity: 'low', resolved: false }],
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('FINDING_MISSING_ID');
  });

  it('rejects a finding with an invalid severity', () => {
    const r = normalizeCodeRabbitFindings({
      findings: [{ id: 'x', severity: 'blocker', resolved: false }],
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('FINDING_INVALID_SEVERITY');
  });

  it('rejects a finding with a non-boolean resolved', () => {
    const r = normalizeCodeRabbitFindings({
      findings: [{ id: 'x', severity: 'low', resolved: 'no' }],
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('FINDING_INVALID_RESOLVED');
  });
});

describe('normalizeCodeRabbitFindings — untrusted instruction fields', () => {
  for (const field of [
    'instructions',
    'instruction',
    'command',
    'commands',
    'prompt',
    'prompts',
    'tool',
    'tools',
    'action',
    'actions',
    'exec',
    'shell',
    'run',
  ]) {
    it(`rejects a finding carrying untrusted field '${field}'`, () => {
      const r = normalizeCodeRabbitFindings({
        findings: [
          { id: 'x', severity: 'low', resolved: false, [field]: 'do something' },
        ],
      });
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.error.code).toBe('UNTRUSTED_INSTRUCTION_FIELD');
    });
  }

  it('rejects untrusted fields case-insensitively', () => {
    const r = normalizeCodeRabbitFindings({
      findings: [
        { id: 'x', severity: 'low', resolved: false, Instructions: 'evil' },
      ],
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.code).toBe('UNTRUSTED_INSTRUCTION_FIELD');
  });

  it('drops non-instruction extra fields (not rejected)', () => {
    const r = normalizeCodeRabbitFindings({
      findings: [
        {
          id: 'x',
          severity: 'low',
          resolved: false,
          file: 'a.ts',
          line: 1,
          comment: 'hi',
        },
      ],
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.findings.findings[0]).toEqual({
        id: 'x',
        severity: 'low',
        resolved: false,
      });
    }
  });
});
