import { describe, expect, it } from 'vitest';
import {
  classifyRisk,
  classifyFromPolicyResult,
  type RiskSignal,
} from '../src/classifier.js';
import type { PolicyResult } from '../src/evaluator.js';

// ─── Pure function: single signals ──────────────────────────────────────────

describe('classifyRisk — single true signals', () => {
  it('ciFailure → auto-eligible', () => {
    const signals: RiskSignal = {
      ciFailure: true,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('auto-eligible');
  });

  it('unresolvedCritical → human-required', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: true,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('human-required');
  });

  it('policyMismatch → human-required', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: true,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('human-required');
  });

  it('duplicateEvidence → auto-eligible', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: true,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('auto-eligible');
  });

  it('touchesAuth → human-required', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: true,
    };
    expect(classifyRisk(signals)).toBe('human-required');
  });

  it('no signals → auto-eligible', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('auto-eligible');
  });
});

// ─── First-failure-wins ordering ────────────────────────────────────────────

describe('classifyRisk — first failure wins ordering', () => {
  it('ciFailure wins over unresolvedCritical', () => {
    const signals: RiskSignal = {
      ciFailure: true,
      unresolvedCritical: true,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('auto-eligible'); // ciFailure checked first
  });

  it('ciFailure wins over all others', () => {
    const signals: RiskSignal = {
      ciFailure: true,
      unresolvedCritical: true,
      policyMismatch: true,
      duplicateEvidence: true,
      touchesAuth: true,
    };
    expect(classifyRisk(signals)).toBe('auto-eligible');
  });

  it('unresolvedCritical wins over policyMismatch', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: true,
      policyMismatch: true,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('human-required');
  });

  it('unresolvedCritical wins over duplicateEvidence', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: true,
      policyMismatch: false,
      duplicateEvidence: true,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('human-required');
  });

  it('policyMismatch wins over duplicateEvidence', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: true,
      duplicateEvidence: true,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('human-required');
  });

  it('duplicateEvidence wins over touchesAuth', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: true,
      touchesAuth: true,
    };
    expect(classifyRisk(signals)).toBe('auto-eligible'); // duplicateEvidence checked before touchesAuth
  });
});

// ─── Determinism ────────────────────────────────────────────────────────────

describe('classifyRisk — pure / deterministic', () => {
  it('returns the same result for the same input every time', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: true,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    const a = classifyRisk(signals);
    const b = classifyRisk(signals);
    const c = classifyRisk(signals);
    expect(a).toBe(b);
    expect(b).toBe(c);
    expect(a).toBe('human-required');
  });

  it('does not mutate the input signal object', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: true,
    };
    const before = { ...signals };
    classifyRisk(signals);
    expect(signals).toEqual(before);
  });
});

// ─── classifyFromPolicyResult wrapper ────────────────────────────────────────

describe('classifyFromPolicyResult', () => {
  const makeResult = (reasonCode: string): PolicyResult => ({
    decision: 'fail',
    reasonCode: reasonCode as PolicyResult['reasonCode'],
    policyVersion: '0.1.0',
    detail: 'test',
  });

  it('CI_NOT_GREEN → auto-eligible', () => {
    const r = makeResult('CI_NOT_GREEN');
    expect(classifyFromPolicyResult(r)).toBe('auto-eligible');
  });

  it('UNRESOLVED_CRITICAL_FINDING → human-required', () => {
    const r = makeResult('UNRESOLVED_CRITICAL_FINDING');
    expect(classifyFromPolicyResult(r)).toBe('human-required');
  });

  it('POLICY_VERSION_MISMATCH → human-required', () => {
    const r = makeResult('POLICY_VERSION_MISMATCH');
    expect(classifyFromPolicyResult(r)).toBe('human-required');
  });

  it('DUPLICATE_EVIDENCE → auto-eligible', () => {
    const r = makeResult('DUPLICATE_EVIDENCE');
    expect(classifyFromPolicyResult(r)).toBe('auto-eligible');
  });

  it('PASS → auto-eligible', () => {
    const r = makeResult('PASS');
    expect(classifyFromPolicyResult(r)).toBe('auto-eligible');
  });

  it('EVIDENCE_INVALID → auto-eligible', () => {
    const r = makeResult('EVIDENCE_INVALID');
    expect(classifyFromPolicyResult(r)).toBe('auto-eligible');
  });

  it('EVIDENCE_STALE → auto-eligible', () => {
    const r = makeResult('EVIDENCE_STALE');
    expect(classifyFromPolicyResult(r)).toBe('auto-eligible');
  });

  it('HEAD_SHA_MISMATCH → auto-eligible', () => {
    const r = makeResult('HEAD_SHA_MISMATCH');
    expect(classifyFromPolicyResult(r)).toBe('auto-eligible');
  });

  it('HUMAN_APPROVAL_REQUIRED → auto-eligible', () => {
    const r = makeResult('HUMAN_APPROVAL_REQUIRED');
    expect(classifyFromPolicyResult(r)).toBe('auto-eligible');
  });
});