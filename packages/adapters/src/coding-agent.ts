/**
 * Generic coding-agent adapter contract.
 *
 * Every coding-agent integration (Devin first, others later) implements this
 * interface so the control plane can drive them uniformly. The interface is
 * transport-agnostic: concrete adapters inject their own transport (HTTP
 * client, SDK, test double) and are responsible for normalizing to/from the
 * shared `AgentRun` shape defined here.
 *
 * Phase 2 keeps this pure and testable — no HTTP server, no network calls, no
 * credentials live in this module. Adapters receive an injected transport.
 */

/* -------------------------------------------------------------------------- */
/* Shared AgentRun types                                                      */
/* -------------------------------------------------------------------------- */

/**
 * Lifecycle status for a coding-agent run. Mirrors the DB `AgentRunStatus`
 * (in `@hermes-ops/db`) but is defined here so the adapter contract does not
 * depend on the storage package. The strings are kept identical so a row can
 * be mapped 1:1.
 */
export type AgentRunStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timed_out';

export const AGENT_RUN_STATUSES: readonly AgentRunStatus[] = [
  'pending',
  'running',
  'succeeded',
  'failed',
  'cancelled',
  'timed_out',
] as const;

/** Terminal statuses — no further transition expected. */
export const TERMINAL_AGENT_RUN_STATUSES: readonly AgentRunStatus[] = [
  'succeeded',
  'failed',
  'cancelled',
  'timed_out',
] as const;

import { type RiskLevel, RISK_LEVELS } from '@hermes-ops/contracts';
export { type RiskLevel, RISK_LEVELS } from '@hermes-ops/contracts';

/**
 * Structured output contract. Adapters that require structured output MUST
 * surface a `schema` (JSON Schema) and a parsed `result` when the run
 * succeeds. Free-form text results are never accepted when structured output
 * is required.
 */
export interface StructuredOutput {
  /** JSON Schema (draft 2020-12 or later) describing the expected result. */
  readonly schema: Readonly<Record<string, unknown>>;
  /** Parsed result conforming to `schema`. */
  readonly result: unknown;
}

/** A normalized agent run, provider-agnostic. */
export interface AgentRun {
  readonly runId: string;
  readonly status: AgentRunStatus;
  readonly provider: string;
  /** ISO-8601 timestamp the run was started, when known. */
  readonly startedAt?: string;
  /** ISO-8601 timestamp the run reached a terminal state, when known. */
  readonly finishedAt?: string;
  /** Provider-specific model id used for the run. */
  readonly model?: string;
  /** Structured output, when the run succeeded with structured output required. */
  readonly structuredOutput?: StructuredOutput;
  /** Free-form error detail, set on `failed`/`timed_out`. */
  readonly error?: string;
  /** Provider-specific raw metadata, opaque to the control plane. */
  readonly metadata?: Readonly<Record<string, string | number | boolean>>;
}

/* -------------------------------------------------------------------------- */
/* Adapter inputs                                                             */
/* -------------------------------------------------------------------------- */

export interface RepositoryRef {
  readonly owner: string;
  readonly name: string;
}

export interface CreateRunInput {
  /** The task prompt. Must be a non-empty string. */
  readonly prompt: string;
  readonly repository: RepositoryRef;
  /** Head commit SHA the run is bound to (40 lowercase hex). */
  readonly headSha: string;
  readonly prNumber?: number;
  /**
   * Budget in milliseconds. Adapters clamp to their configured max and reject
   * if the caller exceeds the max (fail-closed, not silent clamping).
   */
  readonly budgetMs?: number;
  /** When true, the adapter requires structured output and rejects free-form. */
  readonly structuredOutputRequired?: boolean;
  /** Optional JSON Schema to enforce when structured output is required. */
  readonly outputSchema?: Readonly<Record<string, unknown>>;
  /** Risk level hint, used for risk-based model selection. */
  readonly riskLevel?: RiskLevel;
  /** Caller-supplied idempotency key. */
  readonly idempotencyKey?: string;
}

export interface SendFeedbackInput {
  readonly runId: string;
  readonly feedback: string;
}

export interface CancelRunInput {
  readonly runId: string;
  readonly reason?: string;
}

/* -------------------------------------------------------------------------- */
/* Capabilities                                                               */
/* -------------------------------------------------------------------------- */

export interface CodingAgentCapabilities {
  readonly provider: string;
  readonly supportsStructuredOutput: boolean;
  readonly supportsFeedback: boolean;
  readonly supportsCancellation: boolean;
  /** Maximum budget the adapter will accept, in ms. */
  readonly maxBudgetMs: number;
  /** Default model id the adapter selects when no risk override applies. */
  readonly defaultModel: string;
  /** Models the adapter may select. */
  readonly models: readonly string[];
}

/* -------------------------------------------------------------------------- */
/* Adapter error                                                              */
/* -------------------------------------------------------------------------- */

/** Stable error codes for adapter operations. */
export type AdapterErrorCode =
  | 'INVALID_INPUT'
  | 'BUDGET_EXCEEDS_MAX'
  | 'STRUCTURED_OUTPUT_REQUIRED'
  | 'STRUCTURED_OUTPUT_INVALID'
  | 'RUN_NOT_FOUND'
  | 'TRANSPORT_ERROR'
  | 'PROVIDER_ERROR';

export interface AdapterError {
  readonly code: AdapterErrorCode;
  readonly message: string;
  readonly cause?: unknown;
}

/**
 * Thrown by adapters on recoverable, mapped failures. The `code` is stable for
 * control-flow; `message` is human-readable and must not contain secrets.
 */
export class AdapterError extends Error {
  readonly code!: AdapterErrorCode;
  constructor(code: AdapterErrorCode, message: string, cause?: unknown) {
    super(message);
    this.name = 'AdapterError';
    // Assigned via a local cast to avoid the @types/node `Error.code` clash
    // (Node's Error declares a mutable `code?: string`, which conflicts with
    // a direct `this.code =` assignment to a readonly field).
    Object.defineProperty(this, 'code', {
      value: code,
      writable: false,
      enumerable: true,
      configurable: true,
    });
    if (cause !== undefined) {
      Object.defineProperty(this, 'cause', {
        value: cause,
        writable: false,
        enumerable: true,
        configurable: true,
      });
    }
  }
}

/* -------------------------------------------------------------------------- */
/* Adapter interface                                                          */
/* -------------------------------------------------------------------------- */

/**
 * Generic coding-agent adapter.
 *
 * Implementations:
 *  - inject a transport (no network in tests).
 *  - normalize provider request/response shapes to/from `AgentRun`.
 *  - enforce structured-output requirements and bounded budgets.
 *  - select models based on risk, with a safe default.
 */
export interface CodingAgentAdapter {
  readonly provider: string;
  capabilities(): CodingAgentCapabilities;
  createRun(input: CreateRunInput): Promise<AgentRun>;
  getRun(runId: string): Promise<AgentRun>;
  sendFeedback(input: SendFeedbackInput): Promise<AgentRun>;
  cancelRun(input: CancelRunInput): Promise<AgentRun>;
}

/* -------------------------------------------------------------------------- */
/* Shared input validation helpers                                            */
/* -------------------------------------------------------------------------- */

const SHA1_RE = /^[0-9a-f]{40}$/;

export const isNonEmptyString = (v: unknown): v is string =>
  typeof v === 'string' && v.length > 0;

/** Validate a `CreateRunInput`'s common fields. Throws `AdapterError`. */
export const validateCreateRunInput = (input: CreateRunInput): void => {
  if (!input || typeof input !== 'object') {
    throw new AdapterError('INVALID_INPUT', 'createRun input is required');
  }
  if (!isNonEmptyString(input.prompt)) {
    throw new AdapterError('INVALID_INPUT', 'prompt must be a non-empty string');
  }
  if (!input.repository || !isNonEmptyString(input.repository.owner) || !isNonEmptyString(input.repository.name)) {
    throw new AdapterError('INVALID_INPUT', 'repository.owner and repository.name are required');
  }
  if (!isNonEmptyString(input.headSha) || !SHA1_RE.test(input.headSha)) {
    throw new AdapterError('INVALID_INPUT', 'headSha must be a 40-char lowercase hex SHA-1');
  }
  if (input.prNumber !== undefined) {
    if (
      typeof input.prNumber !== 'number' ||
      !Number.isInteger(input.prNumber) ||
      input.prNumber <= 0
    ) {
      throw new AdapterError('INVALID_INPUT', 'prNumber must be a positive integer');
    }
  }
  if (input.budgetMs !== undefined) {
    if (typeof input.budgetMs !== 'number' || !Number.isFinite(input.budgetMs) || input.budgetMs <= 0) {
      throw new AdapterError('INVALID_INPUT', 'budgetMs must be a positive finite number');
    }
  }
  if (input.riskLevel !== undefined && !RISK_LEVELS.includes(input.riskLevel)) {
    throw new AdapterError('INVALID_INPUT', `riskLevel must be one of ${RISK_LEVELS.join('/')}`);
  }
};

/** Validate a run id. Throws `AdapterError`. */
export const validateRunId = (runId: string): void => {
  if (!isNonEmptyString(runId)) {
    throw new AdapterError('INVALID_INPUT', 'runId must be a non-empty string');
  }
};
