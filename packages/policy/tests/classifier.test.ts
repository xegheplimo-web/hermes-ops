import { describe, expect, it } from 'vitest';
import {
  classifyRisk,
  classifyFromPolicyResult,
  type RiskSignal,
} from '../src/classifier.js';
import type { PolicyResult } from '../src/evaluator.js';

// ─── Pure function: single signals ──────────────────────────────────────────

describe('classifyRisk — single true signals', () => {
  it('ciFailure → LOW', () => {
    const signals: RiskSignal = {
      ciFailure: true,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('LOW');
  });

  it('unresolvedCritical → CRITICAL', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: true,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('CRITICAL');
  });

  it('policyMismatch → CRITICAL', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: true,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('CRITICAL');
  });

  it('duplicateEvidence → LOW', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: true,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('LOW');
  });

  it('touchesAuth → HIGH', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: true,
    };
    expect(classifyRisk(signals)).toBe('HIGH');
  });

  it('no signals → LOW', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('LOW');
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
    expect(classifyRisk(signals)).toBe('LOW'); // ciFailure checked first
  });

  it('ciFailure wins over all others', () => {
    const signals: RiskSignal = {
      ciFailure: true,
      unresolvedCritical: true,
      policyMismatch: true,
      duplicateEvidence: true,
      touchesAuth: true,
    };
    expect(classifyRisk(signals)).toBe('LOW');
  });

  it('unresolvedCritical wins over policyMismatch', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: true,
      policyMismatch: true,
      duplicateEvidence: false,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('CRITICAL');
  });

  it('unresolvedCritical wins over duplicateEvidence', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: true,
      policyMismatch: false,
      duplicateEvidence: true,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('CRITICAL');
  });

  it('policyMismatch wins over duplicateEvidence', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: true,
      duplicateEvidence: true,
      touchesAuth: false,
    };
    expect(classifyRisk(signals)).toBe('CRITICAL');
  });

  it('duplicateEvidence wins over touchesAuth', () => {
    const signals: RiskSignal = {
      ciFailure: false,
      unresolvedCritical: false,
      policyMismatch: false,
      duplicateEvidence: true,
      touchesAuth: true,
    };
    expect(classifyRisk(signals)).toBe('LOW'); // duplicateEvidence checked before touchesAuth
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
    expect(a).toBe('CRITICAL');
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

  it('CI_NOT_GREEN → LOW', () => {
    const r = makeResult('CI_NOT_GREEN');
    expect(classifyFromPolicyResult(r)).toBe('LOW');
  });

  it('UNRESOLVED_CRITICAL_FINDING → CRITICAL', () => {
    const r = makeResult('UNRESOLVED_CRITICAL_FINDING');
    expect(classifyFromPolicyResult(r)).toBe('CRITICAL');
  });

  it('POLICY_VERSION_MISMATCH → CRITICAL', () => {
    const r = makeResult('POLICY_VERSION_MISMATCH');
    expect(classifyFromPolicyResult(r)).toBe('CRITICAL');
  });

  it('DUPLICATE_EVIDENCE → LOW', () => {
    const r = makeResult('DUPLICATE_EVIDENCE');
    expect(classifyFromPolicyResult(r)).toBe('LOW');
  });

  it('PASS → LOW', () => {
    const r = makeResult('PASS');
    expect(classifyFromPolicyResult(r)).toBe('LOW');
  });

  it('EVIDENCE_INVALID → MEDIUM', () => {
    const r = makeResult('EVIDENCE_INVALID');
    expect(classifyFromPolicyResult(r)).toBe('MEDIUM');
  });

  it('EVIDENCE_STALE → MEDIUM', () => {
    const r = makeResult('EVIDENCE_STALE');
    expect(classifyFromPolicyResult(r)).toBe('MEDIUM');
  });

  it('HEAD_SHA_MISMATCH → MEDIUM', () => {
    const r = makeResult('HEAD_SHA_MISMATCH');
    expect(classifyFromPolicyResult(r)).toBe('MEDIUM');
  });

  it('HUMAN_APPROVAL_REQUIRED → MEDIUM', () => {
    const r = makeResult('HUMAN_APPROVAL_REQUIRED');
    expect(classifyFromPolicyResult(r)).toBe('MEDIUM');
  });
});
