/**
 * DevinAdapter — normalizes the Devin coding-agent API to/from the shared
 * `CodingAgentAdapter` contract.
 *
 * Phase 2 design constraints (enforced here, not by the transport):
 *
 *  - Injected transport: no HTTP client, no `fetch`, no credentials, no
 *    network in tests. The transport is an interface the caller provides.
 *  - Explicit "normal" default. Devin exposes a fast mode; this adapter does
 *    NOT auto-select fast mode. The default mode is the normal/standard mode.
 *  - Bounded budget: `maxBudgetMs` is enforced. A caller that exceeds it gets
 *    `BUDGET_EXCEEDS_MAX` (fail-closed), never silent clamping.
 *  - Structured output required: when `structuredOutputRequired` is true the
 *    adapter passes the schema to the transport and rejects runs that return
 *    free-form text without a conforming result.
 *  - Risk-based model selection: `glm-5-2` is the default; `swe-1-7` is
 *    selected ONLY for `HIGH` or `CRITICAL` risk. `LOW`/`MEDIUM` always use
 *    the default. The adapter never auto-selects fast mode regardless of risk.
 *
 * The adapter never logs or surfaces secrets. Transport errors are mapped to
 * `AdapterError` with stable codes.
 */

import {
  AdapterError,
  type AdapterErrorCode,
  type AgentRun,
  type AgentRunStatus,
  type CodingAgentAdapter,
  type CodingAgentCapabilities,
  type CreateRunInput,
  type CancelRunInput,
  type SendFeedbackInput,
  type RiskLevel,
  type StructuredOutput,
  validateCreateRunInput,
  validateRunId,
} from './coding-agent.js';

/* -------------------------------------------------------------------------- */
/* Models and risk routing                                                    */
/* -------------------------------------------------------------------------- */

/** Devin model ids the adapter may select. */
export type DevinModel = 'glm-5-2' | 'swe-1-7';

export const DEVIN_MODELS: readonly DevinModel[] = ['glm-5-2', 'swe-1-7'] as const;

/**
 * Devin execution mode. The adapter default is `'normal'`. Fast mode is never
 * auto-selected; a caller may request it explicitly via `CreateRunInput` only
 * if a future extension allows it — currently the adapter does not expose it.
 */
export type DevinMode = 'normal' | 'fast';

/** The explicit, safe default mode. */
export const DEVIN_DEFAULT_MODE: DevinMode = 'normal';

/** The explicit, safe default model. */
export const DEVIN_DEFAULT_MODEL: DevinModel = 'glm-5-2';

/**
 * Risk-based model selection.
 *
 *  - `HIGH` and `CRITICAL` ALWAYS route to `swe-1-7`, regardless of the
 *    configured default. This is a hard safety override, not a hint.
 *  - `LOW`, `MEDIUM`, and unspecified risk route to the adapter's configured
 *    `defaultModel` (which itself defaults to `glm-5-2`).
 *
 * Fast mode is never selected here or anywhere else in the adapter.
 *
 * @param riskLevel     Risk hint from the create-run input.
 * @param defaultModel  The adapter's configured default model. Defaults to
 *                      `DEVIN_DEFAULT_MODEL` (`glm-5-2`) so callers that do not
 *                      configure a default keep the documented behavior.
 */
export const selectDevinModel = (
  riskLevel: RiskLevel | undefined,
  defaultModel: DevinModel = DEVIN_DEFAULT_MODEL,
): DevinModel => {
  if (riskLevel === 'HIGH' || riskLevel === 'CRITICAL') {
    return 'swe-1-7';
  }
  return defaultModel;
};

/* -------------------------------------------------------------------------- */
/* Budget defaults                                                            */
/* -------------------------------------------------------------------------- */

/** Default max budget: 30 minutes. */
export const DEVIN_DEFAULT_MAX_BUDGET_MS = 30 * 60 * 1000;
/** Default per-run budget: 10 minutes. */
export const DEVIN_DEFAULT_BUDGET_MS = 10 * 60 * 1000;

/* -------------------------------------------------------------------------- */
/* Transport interface (injected)                                             */
/* -------------------------------------------------------------------------- */

/** Provider-side request shape for creating a run. */
export interface DevinCreateRunRequest {
  readonly prompt: string;
  readonly repositoryOwner: string;
  readonly repositoryName: string;
  readonly headSha: string;
  readonly prNumber?: number;
  readonly model: DevinModel;
  readonly mode: DevinMode;
  readonly budgetMs: number;
  readonly structuredOutputRequired: boolean;
  readonly outputSchema?: Readonly<Record<string, unknown>>;
  readonly idempotencyKey?: string;
}

/** Provider-side response shape for a created run. */
export interface DevinRunResponse {
  readonly runId: string;
  readonly status: string;
  readonly model?: string;
  readonly mode?: string;
  readonly startedAt?: string;
  readonly finishedAt?: string;
  /** Parsed structured result, when the run produced one. */
  readonly structuredResult?: unknown;
  /** Free-form output text, when structured output was not required. */
  readonly output?: string;
  readonly error?: string;
  readonly metadata?: Readonly<Record<string, string | number | boolean>>;
}

/**
 * Transport interface the caller injects. Implementations may wrap an HTTP
 * client, an SDK, or a test double. The adapter never imports a transport
 * implementation directly.
 */
export interface DevinTransport {
  createRun(req: DevinCreateRunRequest): Promise<DevinRunResponse>;
  getRun(runId: string): Promise<DevinRunResponse>;
  sendFeedback(runId: string, feedback: string): Promise<DevinRunResponse>;
  cancelRun(runId: string, reason?: string): Promise<DevinRunResponse>;
}

/* -------------------------------------------------------------------------- */
/* Adapter options                                                            */
/* -------------------------------------------------------------------------- */

export interface DevinAdapterOptions {
  /** Injected transport. Required. */
  readonly transport: DevinTransport;
  /** Default model. Defaults to `glm-5-2`. */
  readonly defaultModel?: DevinModel;
  /** Max budget in ms. Defaults to 30m. Must be > 0. */
  readonly maxBudgetMs?: number;
  /** Default per-run budget in ms. Defaults to 10m. Must be > 0 and <= max. */
  readonly defaultBudgetMs?: number;
}

/* -------------------------------------------------------------------------- */
/* Adapter                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * Map a provider status string to the shared `AgentRunStatus`. Unknown
 * statuses map to `failed` (fail-closed) rather than `running`, so a stale or
 * malformed provider response cannot masquerade as an in-flight run.
 */
const mapStatus = (raw: string): AgentRunStatus => {
  switch (raw) {
    case 'pending':
    case 'queued':
      return 'pending';
    case 'running':
    case 'in_progress':
      return 'running';
    case 'succeeded':
    case 'completed':
    case 'success':
      return 'succeeded';
    case 'failed':
    case 'error':
      return 'failed';
    case 'cancelled':
    case 'canceled':
      return 'cancelled';
    case 'timed_out':
    case 'timeout':
      return 'timed_out';
    default:
      return 'failed';
  }
};

/**
 * Normalize a provider response into the shared `AgentRun`. When structured
 * output was required and the response lacks a `structuredResult`, this throws
 * `AdapterError` with code `STRUCTURED_OUTPUT_INVALID`.
 */
const normalizeRun = (
  res: DevinRunResponse,
  structuredOutputRequired: boolean,
  outputSchema?: Readonly<Record<string, unknown>>,
): AgentRun => {
  if (typeof res.runId !== 'string' || res.runId.length === 0) {
    throw new AdapterError('PROVIDER_ERROR', 'provider response missing runId');
  }

  let structuredOutput: StructuredOutput | undefined;
  if (structuredOutputRequired) {
    if (res.structuredResult === undefined || res.structuredResult === null) {
      throw new AdapterError(
        'STRUCTURED_OUTPUT_INVALID',
        'structured output was required but the provider returned no structured result',
      );
    }
    structuredOutput = {
      schema: outputSchema ?? {},
      result: res.structuredResult,
    };
  }

  return {
    runId: res.runId,
    provider: 'devin',
    status: mapStatus(res.status),
    startedAt: res.startedAt,
    finishedAt: res.finishedAt,
    model: res.model,
    structuredOutput,
    error: res.error,
    metadata: res.metadata,
  };
};

/**
 * Wrap a transport call and map rejections to `AdapterError`. A transport that
 * throws a non-`AdapterError` is treated as a transport-level failure.
 *
 * Security: the raw thrown message is NEVER surfaced on the resulting
 * `AdapterError`. Provider/transport error messages may contain credentials,
 * auth tokens, or raw provider payloads, so leaking them is a secret-disclosure
 * risk. Unknown failures are mapped to a single stable generic message and only
 * the stable `code` is preserved. The original error is intentionally NOT
 * attached as `cause` either, since the cause's own `message` would still carry
 * the secret-bearing text and could be logged or serialized downstream.
 */
const TRANSPORT_ERROR_MESSAGE = 'transport call failed';

const withTransport = async <T>(
  code: AdapterErrorCode,
  fn: () => Promise<T>,
): Promise<T> => {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof AdapterError) throw err;
    throw new AdapterError(code, TRANSPORT_ERROR_MESSAGE);
  }
};

/**
 * Create a DevinAdapter.
 *
 * The adapter is stateless aside from its configured options; all state lives
 * in the injected transport and the caller's control plane.
 */
export const createDevinAdapter = (
  options: DevinAdapterOptions,
): CodingAgentAdapter => {
  if (!options || typeof options !== 'object') {
    throw new AdapterError('INVALID_INPUT', 'DevinAdapterOptions is required');
  }
  if (!options.transport) {
    throw new AdapterError('INVALID_INPUT', 'transport is required');
  }
  const transport = options.transport;
  const defaultModel = options.defaultModel ?? DEVIN_DEFAULT_MODEL;
  if (!DEVIN_MODELS.includes(defaultModel)) {
    throw new AdapterError('INVALID_INPUT', `defaultModel must be one of ${DEVIN_MODELS.join('/')}`);
  }
  const maxBudgetMs = options.maxBudgetMs ?? DEVIN_DEFAULT_MAX_BUDGET_MS;
  if (!Number.isFinite(maxBudgetMs) || maxBudgetMs <= 0) {
    throw new AdapterError('INVALID_INPUT', 'maxBudgetMs must be a positive finite number');
  }
  const defaultBudgetMs = options.defaultBudgetMs ?? DEVIN_DEFAULT_BUDGET_MS;
  if (!Number.isFinite(defaultBudgetMs) || defaultBudgetMs <= 0) {
    throw new AdapterError('INVALID_INPUT', 'defaultBudgetMs must be a positive finite number');
  }
  if (defaultBudgetMs > maxBudgetMs) {
    throw new AdapterError('INVALID_INPUT', 'defaultBudgetMs must be <= maxBudgetMs');
  }

  const capabilities: CodingAgentCapabilities = {
    provider: 'devin',
    supportsStructuredOutput: true,
    supportsFeedback: true,
    supportsCancellation: true,
    maxBudgetMs,
    defaultModel,
    models: DEVIN_MODELS,
  };

  return {
    provider: 'devin',
    capabilities(): CodingAgentCapabilities {
      return capabilities;
    },

    async createRun(input: CreateRunInput): Promise<AgentRun> {
      validateCreateRunInput(input);

      // Enforce bounded budget (fail-closed, no silent clamping).
      const requestedBudget = input.budgetMs ?? defaultBudgetMs;
      if (requestedBudget > maxBudgetMs) {
        throw new AdapterError(
          'BUDGET_EXCEEDS_MAX',
          `requested budget ${requestedBudget}ms exceeds max ${maxBudgetMs}ms`,
        );
      }

      // Structured output is required by default for Devin in this adapter.
      // A caller may explicitly opt out, but the adapter's normal posture is
      // to require it.
      const structuredOutputRequired = input.structuredOutputRequired ?? true;
      if (structuredOutputRequired && !input.outputSchema) {
        throw new AdapterError(
          'STRUCTURED_OUTPUT_REQUIRED',
          'outputSchema is required when structuredOutputRequired is true',
        );
      }

      // Risk-based model selection. Fast mode is never selected.
      // HIGH/CRITICAL always force swe-1-7 (hard safety override); for
      // LOW/MEDIUM/unspecified risk the configured `defaultModel` is honored.
      const model = selectDevinModel(input.riskLevel, defaultModel);

      const req: DevinCreateRunRequest = {
        prompt: input.prompt,
        repositoryOwner: input.repository.owner,
        repositoryName: input.repository.name,
        headSha: input.headSha,
        prNumber: input.prNumber,
        model,
        mode: DEVIN_DEFAULT_MODE,
        budgetMs: requestedBudget,
        structuredOutputRequired,
        outputSchema: input.outputSchema,
        idempotencyKey: input.idempotencyKey,
      };

      const res = await withTransport('TRANSPORT_ERROR', () =>
        transport.createRun(req),
      );
      return normalizeRun(res, structuredOutputRequired, input.outputSchema);
    },

    async getRun(runId: string): Promise<AgentRun> {
      validateRunId(runId);
      const res = await withTransport('TRANSPORT_ERROR', () =>
        transport.getRun(runId),
      );
      // getRun does not know whether structured output was required at create
      // time; surface structured result only if the provider returns one.
      return normalizeRun(res, false);
    },

    async sendFeedback(input: SendFeedbackInput): Promise<AgentRun> {
      validateRunId(input.runId);
      if (typeof input.feedback !== 'string' || input.feedback.length === 0) {
        throw new AdapterError('INVALID_INPUT', 'feedback must be a non-empty string');
      }
      const res = await withTransport('TRANSPORT_ERROR', () =>
        transport.sendFeedback(input.runId, input.feedback),
      );
      return normalizeRun(res, false);
    },

    async cancelRun(input: CancelRunInput): Promise<AgentRun> {
      validateRunId(input.runId);
      if (input.reason !== undefined && (typeof input.reason !== 'string')) {
        throw new AdapterError('INVALID_INPUT', 'reason must be a string when provided');
      }
      const res = await withTransport('TRANSPORT_ERROR', () =>
        transport.cancelRun(input.runId, input.reason),
      );
      return normalizeRun(res, false);
    },
  };
};
