import { describe, expect, it } from 'vitest';
import {
  AdapterError,
  DEVIN_DEFAULT_MODE,
  DEVIN_DEFAULT_MODEL,
  DEVIN_MODELS,
  createDevinAdapter,
  selectDevinModel,
  type DevinCreateRunRequest,
  type DevinRunResponse,
  type DevinTransport,
} from '../src/index.js';

const HEAD_SHA = '0123456789abcdef0123456789abcdef01234567';
const SCHEMA = { type: 'object', properties: { summary: { type: 'string' } }, required: ['summary'] } as const;

/** A recording transport that never touches the network. */
const makeTransport = (
  overrides: Partial<DevinTransport> = {},
): DevinTransport & {
  createRunCalls: DevinCreateRunRequest[];
  lastRunId?: string;
  lastFeedback?: string;
  lastCancelReason?: string;
} => {
  const calls: DevinCreateRunRequest[] = [];
  let lastRunId: string | undefined;
  let lastFeedback: string | undefined;
  let lastCancelReason: string | undefined;

  const defaultResponse: DevinRunResponse = {
    runId: 'run-1',
    status: 'running',
    model: DEVIN_DEFAULT_MODEL,
    mode: DEVIN_DEFAULT_MODE,
    startedAt: '2026-08-19T12:00:00.000Z',
  };

  const base: DevinTransport = {
    async createRun(req: DevinCreateRunRequest): Promise<DevinRunResponse> {
      calls.push(req);
      lastRunId = req.idempotencyKey ? `run-${req.idempotencyKey}` : 'run-1';
      // Echo a structured result when the run requires it, so the adapter's
      // structured-output enforcement passes for the happy-path tests.
      const structuredResult =
        req.structuredOutputRequired ? { summary: 'ok' } : undefined;
      return {
        ...defaultResponse,
        runId: lastRunId,
        model: req.model,
        mode: req.mode,
        structuredResult,
      };
    },
    async getRun(runId: string): Promise<DevinRunResponse> {
      lastRunId = runId;
      return { ...defaultResponse, runId, status: 'running' };
    },
    async sendFeedback(runId: string, feedback: string): Promise<DevinRunResponse> {
      lastRunId = runId;
      lastFeedback = feedback;
      return { ...defaultResponse, runId, status: 'running' };
    },
    async cancelRun(runId: string, reason?: string): Promise<DevinRunResponse> {
      lastRunId = runId;
      lastCancelReason = reason;
      return { ...defaultResponse, runId, status: 'cancelled' };
    },
  };

  const t: DevinTransport = { ...base, ...overrides };

  // Attach live getters (Object.assign would copy snapshot values, not
  // getters, so use defineProperties).
  Object.defineProperties(t, {
    createRunCalls: { get: () => calls, enumerable: true },
    lastRunId: { get: () => lastRunId, enumerable: true },
    lastFeedback: { get: () => lastFeedback, enumerable: true },
    lastCancelReason: { get: () => lastCancelReason, enumerable: true },
  });

  return t as DevinTransport & {
    createRunCalls: DevinCreateRunRequest[];
    lastRunId?: string;
    lastFeedback?: string;
    lastCancelReason?: string;
  };
};

const baseInput = () => ({
  prompt: 'fix the bug',
  repository: { owner: 'acme', name: 'hermes-ops' },
  headSha: HEAD_SHA,
  outputSchema: SCHEMA,
});

describe('selectDevinModel — risk routing', () => {
  it('selects glm-5-2 for undefined risk', () => {
    expect(selectDevinModel(undefined)).toBe('glm-5-2');
  });

  it('selects glm-5-2 for LOW', () => {
    expect(selectDevinModel('LOW')).toBe('glm-5-2');
  });

  it('selects glm-5-2 for MEDIUM', () => {
    expect(selectDevinModel('MEDIUM')).toBe('glm-5-2');
  });

  it('selects swe-1-7 for HIGH', () => {
    expect(selectDevinModel('HIGH')).toBe('swe-1-7');
  });

  it('selects swe-1-7 for CRITICAL', () => {
    expect(selectDevinModel('CRITICAL')).toBe('swe-1-7');
  });

  it('honors a configured defaultModel for LOW/MEDIUM/undefined risk', () => {
    expect(selectDevinModel(undefined, 'swe-1-7')).toBe('swe-1-7');
    expect(selectDevinModel('LOW', 'swe-1-7')).toBe('swe-1-7');
    expect(selectDevinModel('MEDIUM', 'swe-1-7')).toBe('swe-1-7');
  });

  it('forces swe-1-7 for HIGH/CRITICAL regardless of the configured default', () => {
    // Even if the configured default is swe-1-7, HIGH/CRITICAL still force it,
    // and even if the configured default were something else, HIGH/CRITICAL
    // would still force swe-1-7 (not the configured default).
    expect(selectDevinModel('HIGH', 'swe-1-7')).toBe('swe-1-7');
    expect(selectDevinModel('CRITICAL', 'swe-1-7')).toBe('swe-1-7');
  });
});

describe('DevinAdapter — defaultModel is honored for non-high risk', () => {
  it('uses the configured defaultModel for unspecified risk', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t, defaultModel: 'swe-1-7' });
    await a.createRun(baseInput());
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('uses the configured defaultModel for LOW risk', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t, defaultModel: 'swe-1-7' });
    await a.createRun({ ...baseInput(), riskLevel: 'LOW' });
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('uses the configured defaultModel for MEDIUM risk', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t, defaultModel: 'swe-1-7' });
    await a.createRun({ ...baseInput(), riskLevel: 'MEDIUM' });
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('forces swe-1-7 for HIGH risk even when defaultModel is glm-5-2', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t, defaultModel: 'glm-5-2' });
    await a.createRun({ ...baseInput(), riskLevel: 'HIGH' });
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('forces swe-1-7 for CRITICAL risk even when defaultModel is glm-5-2', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t, defaultModel: 'glm-5-2' });
    await a.createRun({ ...baseInput(), riskLevel: 'CRITICAL' });
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('still forces swe-1-7 for HIGH when defaultModel is already swe-1-7', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t, defaultModel: 'swe-1-7' });
    await a.createRun({ ...baseInput(), riskLevel: 'HIGH' });
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('defaults to glm-5-2 when no defaultModel is configured', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.createRun(baseInput());
    expect(t.createRunCalls[0]?.model).toBe('glm-5-2');
    expect(a.capabilities().defaultModel).toBe('glm-5-2');
  });

  it('reflects the configured defaultModel in capabilities', () => {
    const a = createDevinAdapter({ transport: makeTransport(), defaultModel: 'swe-1-7' });
    expect(a.capabilities().defaultModel).toBe('swe-1-7');
  });
});

describe('DevinAdapter — capabilities and defaults', () => {
  it('reports devin provider and supported capabilities', () => {
    const a = createDevinAdapter({ transport: makeTransport() });
    const c = a.capabilities();
    expect(c.provider).toBe('devin');
    expect(c.supportsStructuredOutput).toBe(true);
    expect(c.supportsFeedback).toBe(true);
    expect(c.supportsCancellation).toBe(true);
    expect(c.defaultModel).toBe(DEVIN_DEFAULT_MODEL);
    expect(c.models).toEqual(DEVIN_MODELS);
  });

  it('uses the explicit normal default mode', () => {
    expect(DEVIN_DEFAULT_MODE).toBe('normal');
  });

  it('does not include a fast mode in the model list', () => {
    expect(DEVIN_MODELS).not.toContain('fast');
  });
});

describe('DevinAdapter — createRun payload defaults', () => {
  it('sends the normal mode and glm-5-2 default model with a bounded default budget', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.createRun(baseInput());
    const req = t.createRunCalls[0];
    expect(req).toBeDefined();
    if (!req) return;
    expect(req.mode).toBe('normal');
    expect(req.model).toBe('glm-5-2');
    expect(req.budgetMs).toBeGreaterThan(0);
    expect(req.structuredOutputRequired).toBe(true);
    expect(req.outputSchema).toEqual(SCHEMA);
  });

  it('routes HIGH risk to swe-1-7', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.createRun({ ...baseInput(), riskLevel: 'HIGH' });
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('routes CRITICAL risk to swe-1-7', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.createRun({ ...baseInput(), riskLevel: 'CRITICAL' });
    expect(t.createRunCalls[0]?.model).toBe('swe-1-7');
  });

  it('keeps glm-5-2 for MEDIUM risk (no auto-upgrade)', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.createRun({ ...baseInput(), riskLevel: 'MEDIUM' });
    expect(t.createRunCalls[0]?.model).toBe('glm-5-2');
  });

  it('never selects fast mode regardless of risk', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    for (const risk of ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const) {
      await a.createRun({ ...baseInput(), riskLevel: risk });
    }
    for (const call of t.createRunCalls) {
      expect(call.mode).toBe('normal');
      expect(call.model).not.toBe('fast');
    }
  });

  it('passes through idempotency key and prNumber', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.createRun({ ...baseInput(), idempotencyKey: 'k-1', prNumber: 7 });
    expect(t.createRunCalls[0]?.idempotencyKey).toBe('k-1');
    expect(t.createRunCalls[0]?.prNumber).toBe(7);
  });
});

describe('DevinAdapter — bounded budget', () => {
  it('rejects a budget exceeding the configured max (fail-closed)', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({
      transport: t,
      maxBudgetMs: 1000,
      defaultBudgetMs: 500,
    });
    await expect(
      a.createRun({ ...baseInput(), budgetMs: 2000 }),
    ).rejects.toMatchObject({ code: 'BUDGET_EXCEEDS_MAX' });
    expect(t.createRunCalls).toHaveLength(0);
  });

  it('accepts a budget at the configured max', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({
      transport: t,
      maxBudgetMs: 1000,
      defaultBudgetMs: 500,
    });
    await a.createRun({ ...baseInput(), budgetMs: 1000 });
    expect(t.createRunCalls[0]?.budgetMs).toBe(1000);
  });

  it('uses the default budget when none is provided', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({
      transport: t,
      maxBudgetMs: 60_000,
      defaultBudgetMs: 5_000,
    });
    await a.createRun(baseInput());
    expect(t.createRunCalls[0]?.budgetMs).toBe(5_000);
  });

  it('rejects a non-positive budget', async () => {
    const a = createDevinAdapter({ transport: makeTransport() });
    await expect(
      a.createRun({ ...baseInput(), budgetMs: 0 }),
    ).rejects.toMatchObject({ code: 'INVALID_INPUT' });
  });
});

describe('DevinAdapter — structured output required', () => {
  it('requires outputSchema when structuredOutputRequired is true', async () => {
    const a = createDevinAdapter({ transport: makeTransport() });
    await expect(
      a.createRun({ ...baseInput(), structuredOutputRequired: true, outputSchema: undefined }),
    ).rejects.toMatchObject({ code: 'STRUCTURED_OUTPUT_REQUIRED' });
  });

  it('defaults structuredOutputRequired to true', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    // baseInput includes outputSchema, so this should succeed and require it.
    await a.createRun(baseInput());
    expect(t.createRunCalls[0]?.structuredOutputRequired).toBe(true);
  });

  it('rejects a provider response lacking structuredResult when required', async () => {
    const t = makeTransport({
      async createRun() {
        return { runId: 'r', status: 'succeeded', model: 'glm-5-2' };
      },
    });
    const a = createDevinAdapter({ transport: t });
    await expect(a.createRun(baseInput())).rejects.toMatchObject({
      code: 'STRUCTURED_OUTPUT_INVALID',
    });
  });

  it('surfaces structuredOutput when the provider returns a structuredResult', async () => {
    const t = makeTransport({
      async createRun(req) {
        return {
          runId: 'r',
          status: 'succeeded',
          model: req.model,
          structuredResult: { summary: 'done' },
          finishedAt: '2026-08-19T12:01:00.000Z',
        };
      },
    });
    const a = createDevinAdapter({ transport: t });
    const run = await a.createRun(baseInput());
    expect(run.status).toBe('succeeded');
    expect(run.structuredOutput).toBeDefined();
    expect(run.structuredOutput?.result).toEqual({ summary: 'done' });
    expect(run.structuredOutput?.schema).toEqual(SCHEMA);
  });

  it('allows free-form output when structuredOutputRequired is explicitly false', async () => {
    const t = makeTransport({
      async createRun(req) {
        return {
          runId: 'r',
          status: 'succeeded',
          model: req.model,
          output: 'all done',
        };
      },
    });
    const a = createDevinAdapter({ transport: t });
    const run = await a.createRun({
      ...baseInput(),
      structuredOutputRequired: false,
      outputSchema: undefined,
    });
    expect(run.status).toBe('succeeded');
    expect(run.structuredOutput).toBeUndefined();
  });
});

describe('DevinAdapter — response normalization', () => {
  it('maps provider status aliases to shared statuses', async () => {
    const cases: Array<[string, string]> = [
      ['queued', 'pending'],
      ['in_progress', 'running'],
      ['completed', 'succeeded'],
      ['success', 'succeeded'],
      ['error', 'failed'],
      ['canceled', 'cancelled'],
      ['timeout', 'timed_out'],
    ];
    for (const [providerStatus, expected] of cases) {
      const t = makeTransport({
        async getRun() {
          return { runId: 'r', status: providerStatus };
        },
      });
      const a = createDevinAdapter({ transport: t });
      const run = await a.getRun('r');
      expect(run.status).toBe(expected);
    }
  });

  it('maps an unknown provider status to failed (fail-closed)', async () => {
    const t = makeTransport({
      async getRun() {
        return { runId: 'r', status: 'wat' };
      },
    });
    const a = createDevinAdapter({ transport: t });
    const run = await a.getRun('r');
    expect(run.status).toBe('failed');
  });

  it('rejects a provider response missing a runId', async () => {
    const t = makeTransport({
      async getRun() {
        return { runId: '', status: 'running' };
      },
    });
    const a = createDevinAdapter({ transport: t });
    await expect(a.getRun('r')).rejects.toMatchObject({
      code: 'PROVIDER_ERROR',
    });
  });

  it('normalizes provider metadata onto the AgentRun', async () => {
    const t = makeTransport({
      async getRun() {
        return {
          runId: 'r',
          status: 'running',
          metadata: { foo: 'bar', n: 1, b: true },
        };
      },
    });
    const a = createDevinAdapter({ transport: t });
    const run = await a.getRun('r');
    expect(run.metadata).toEqual({ foo: 'bar', n: 1, b: true });
    expect(run.provider).toBe('devin');
  });
});

describe('DevinAdapter — error mapping', () => {
  it('maps a transport rejection to AdapterError(TRANSPORT_ERROR) with a stable generic message', async () => {
    const t = makeTransport({
      async createRun() {
        throw new Error('connection refused');
      },
    });
    const a = createDevinAdapter({ transport: t });
    await expect(a.createRun(baseInput())).rejects.toMatchObject({
      code: 'TRANSPORT_ERROR',
      message: 'transport call failed',
    });
  });

  it('does not surface a secret-bearing transport error message', async () => {
    const SECRET = 'sk-live-1234567890abcdef-DO-NOT-LEAK';
    const t = makeTransport({
      async createRun() {
        throw new Error(`Unauthorized: token=${SECRET} is invalid`);
      },
    });
    const a = createDevinAdapter({ transport: t });
    let caught: unknown;
    try {
      await a.createRun(baseInput());
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(AdapterError);
    const err = caught as AdapterError;
    expect(err.code).toBe('TRANSPORT_ERROR');
    // The thrown message must be the generic stable one and must NOT contain
    // the secret from the underlying transport error.
    expect(err.message).toBe('transport call failed');
    expect(err.message).not.toContain(SECRET);
    // The original error must not be attached as `cause`, since its own
    // message still carries the secret and could be logged downstream.
    expect(err.cause).toBeUndefined();
    // Belt-and-suspenders: the secret must not appear anywhere on the
    // stringified thrown object either.
    expect(JSON.stringify(err)).not.toContain(SECRET);
  });

  it('maps a non-Error transport rejection to the same stable generic message', async () => {
    const t = makeTransport({
      async getRun() {
        throw 'something weird'; // eslint-disable-line no-throw-literal
      },
    });
    const a = createDevinAdapter({ transport: t });
    await expect(a.getRun('r')).rejects.toMatchObject({
      code: 'TRANSPORT_ERROR',
      message: 'transport call failed',
    });
  });

  it('preserves an AdapterError thrown by the transport', async () => {
    const t = makeTransport({
      async getRun() {
        throw new AdapterError('RUN_NOT_FOUND', 'no such run');
      },
    });
    const a = createDevinAdapter({ transport: t });
    await expect(a.getRun('r')).rejects.toMatchObject({
      code: 'RUN_NOT_FOUND',
    });
  });

  it('rejects invalid createRun input', async () => {
    const a = createDevinAdapter({ transport: makeTransport() });
    await expect(a.createRun({ ...baseInput(), prompt: '' })).rejects.toMatchObject({
      code: 'INVALID_INPUT',
    });
    await expect(
      a.createRun({ ...baseInput(), headSha: 'not-a-sha' }),
    ).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(
      a.createRun({ ...baseInput(), repository: { owner: '', name: 'x' } }),
    ).rejects.toMatchObject({ code: 'INVALID_INPUT' });
  });

  it('rejects an invalid risk level', async () => {
    const a = createDevinAdapter({ transport: makeTransport() });
    await expect(
      a.createRun({ ...baseInput(), riskLevel: 'EXTREME' as never }),
    ).rejects.toMatchObject({ code: 'INVALID_INPUT' });
  });

  it('rejects an empty runId', async () => {
    const a = createDevinAdapter({ transport: makeTransport() });
    await expect(a.getRun('')).rejects.toMatchObject({ code: 'INVALID_INPUT' });
  });

  it('rejects empty feedback', async () => {
    const a = createDevinAdapter({ transport: makeTransport() });
    await expect(a.sendFeedback({ runId: 'r', feedback: '' })).rejects.toMatchObject({
      code: 'INVALID_INPUT',
    });
  });

  it('passes the cancel reason through to the transport', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.cancelRun({ runId: 'r', reason: 'policy-failed' });
    expect(t.lastCancelReason).toBe('policy-failed');
  });

  it('passes feedback through to the transport', async () => {
    const t = makeTransport();
    const a = createDevinAdapter({ transport: t });
    await a.sendFeedback({ runId: 'r', feedback: 'please retry' });
    expect(t.lastFeedback).toBe('please retry');
  });
});

describe('DevinAdapter — construction validation', () => {
  it('rejects a missing transport', () => {
    expect(() => createDevinAdapter({} as never)).toThrow(AdapterError);
  });

  it('rejects an invalid defaultModel', () => {
    expect(() =>
      createDevinAdapter({ transport: makeTransport(), defaultModel: 'fast' as never }),
    ).toThrow(AdapterError);
  });

  it('rejects a non-positive maxBudgetMs', () => {
    expect(() =>
      createDevinAdapter({ transport: makeTransport(), maxBudgetMs: 0 }),
    ).toThrow(AdapterError);
  });

  it('rejects a defaultBudgetMs greater than maxBudgetMs', () => {
    expect(() =>
      createDevinAdapter({
        transport: makeTransport(),
        maxBudgetMs: 1000,
        defaultBudgetMs: 2000,
      }),
    ).toThrow(AdapterError);
  });
});
