import { describe, expect, it } from 'vitest';
import { MANIFEST_SCHEMA_VERSION } from '@hermes-ops/contracts';
import { evaluateGate, type GateEngineInput } from '../src/engine.js';

const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
const VALID_MANIFEST = {
  schemaVersion: MANIFEST_SCHEMA_VERSION,
  repository: { owner: 'acme', name: 'hermes-ops' },
  prNumber: 42,
  headSha: HEAD_SHA,
  policyVersion: '0.1.0',
  timestamp: new Date().toISOString(),
  artifacts: [{ path: 'reports/coverage.json', sha256: 'a'.repeat(64) }],
  ci: { conclusion: 'success' },
  source: { kind: 'github-actions', version: '0.1.0' },
};

const makeInput = (
  manifest: unknown,
  overrides: Partial<GateEngineInput> = {},
): GateEngineInput => ({
  manifest,
  expectedHeadSha: HEAD_SHA,
  policyVersion: '0.1.0',
  attempts: 0,
  maxAttempts: 3,
  ...overrides,
});

describe('evaluateGate — PASS', () => {
  it('passes a clean manifest', () => {
    const r = evaluateGate(makeInput(VALID_MANIFEST));
    expect(r.decision).toBe('pass');
    expect(r.gate).toBe('PASS');
    expect(r.reasonCode).toBe('PASS');
    expect(r.riskLevel).toBe('LOW');
    expect(r.requiredGates).toEqual(['ci']);
  });
});

describe('evaluateGate — REPAIR', () => {
  it('CI failure with attempts remaining → REPAIR', () => {
    const manifest = { ...VALID_MANIFEST, ci: { conclusion: 'failure' } };
    const r = evaluateGate(makeInput(manifest, { attempts: 1 }));
    expect(r.gate).toBe('REPAIR');
    expect(r.reasonCode).toBe('CI_NOT_GREEN');
    expect(r.riskLevel).toBe('LOW');
  });

  it('unresolved critical finding with attempts remaining → REPAIR when approved', () => {
    const manifest = {
      ...VALID_MANIFEST,
      coderabbit: { findings: [{ id: 'f1', severity: 'critical', resolved: false }] },
    };
    const r = evaluateGate(makeInput(manifest, {
      attempts: 1,
      approval: { signedAt: new Date().toISOString(), approver: 'alice', reason: 'ack', signature: 'sig-1' },
    }));
    expect(r.gate).toBe('REPAIR');
    expect(r.riskLevel).toBe('CRITICAL');
  });
});

describe('evaluateGate — BLOCK', () => {
  it('CRITICAL without approval → BLOCK', () => {
    const manifest = {
      ...VALID_MANIFEST,
      coderabbit: { findings: [{ id: 'f1', severity: 'critical', resolved: false }] },
    };
    const r = evaluateGate(makeInput(manifest));
    expect(r.gate).toBe('BLOCK');
    expect(r.reasonCode).toBe('HUMAN_APPROVAL_REQUIRED');
    expect(r.riskLevel).toBe('CRITICAL');
  });

  it('HEAD_SHA_MISMATCH → BLOCK', () => {
    const manifest = { ...VALID_MANIFEST, headSha: 'a'.repeat(40) };
    const r = evaluateGate(makeInput(manifest, { expectedHeadSha: HEAD_SHA }));
    expect(r.gate).toBe('BLOCK');
    expect(r.reasonCode).toBe('HEAD_SHA_MISMATCH');
  });

  it('EVIDENCE_STALE → BLOCK', () => {
    const past = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString();
    const manifest = { ...VALID_MANIFEST, timestamp: past };
    const r = evaluateGate(makeInput(manifest, { now: new Date() }));
    expect(r.gate).toBe('BLOCK');
    expect(r.reasonCode).toBe('EVIDENCE_STALE');
  });

  it('sensitive changed files escalate to CRITICAL and block without approval', () => {
    const r = evaluateGate(makeInput(VALID_MANIFEST, { changedFiles: ['src/auth/login.ts'] }));
    expect(r.gate).toBe('BLOCK');
    expect(r.riskLevel).toBe('CRITICAL');
    expect(r.reasonCode).toBe('HUMAN_APPROVAL_REQUIRED');
  });
});

describe('evaluateGate — ESCALATE', () => {
  it('attempts >= maxAttempts → ESCALATE', () => {
    const manifest = { ...VALID_MANIFEST, ci: { conclusion: 'failure' } };
    const r = evaluateGate(makeInput(manifest, { attempts: 3, maxAttempts: 3 }));
    expect(r.gate).toBe('ESCALATE');
    expect(r.reasonCode).toBe('CI_NOT_GREEN');
  });

  it('CRITICAL with attempts exhausted → ESCALATE', () => {
    const manifest = {
      ...VALID_MANIFEST,
      coderabbit: { findings: [{ id: 'f1', severity: 'critical', resolved: false }] },
    };
    const r = evaluateGate(makeInput(manifest, {
      attempts: 5,
      maxAttempts: 5,
      approval: { signedAt: new Date().toISOString(), approver: 'alice', reason: 'ack', signature: 'sig-1' },
    }));
    expect(r.gate).toBe('ESCALATE');
  });
});

describe('evaluateGate — explicit risk', () => {
  it('explicit HIGH keeps lower computed risk', () => {
    const r = evaluateGate(makeInput(VALID_MANIFEST, { explicitRisk: 'HIGH' }));
    expect(r.gate).toBe('PASS');
    expect(r.riskLevel).toBe('HIGH');
    expect(r.requiredGates).toEqual(['ci', 'codex']);
  });

  it('explicit CRITICAL without approval blocks', () => {
    const r = evaluateGate(makeInput(VALID_MANIFEST, { explicitRisk: 'CRITICAL' }));
    expect(r.gate).toBe('BLOCK');
    expect(r.riskLevel).toBe('CRITICAL');
    expect(r.requiredGates).toEqual(['ci', 'codex', 'human']);
  });
});

describe('evaluateGate — pure / deterministic', () => {
  it('returns the same result for the same input', () => {
    const input = makeInput(VALID_MANIFEST);
    const a = evaluateGate(input);
    const b = evaluateGate(input);
    expect(a).toEqual(b);
  });

  it('does not mutate the input manifest', () => {
    const manifest = { ...VALID_MANIFEST };
    const before = JSON.stringify(manifest);
    evaluateGate(makeInput(manifest));
    expect(JSON.stringify(manifest)).toBe(before);
  });
});
