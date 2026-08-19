/**
 * CodeRabbit finding normalization.
 *
 * Normalizes an unknown, untrusted CodeRabbit payload into the contracts
 * `CodeRabbitFindings` shape — preserving ONLY `id`, `severity`, and
 * `resolved`. Every other field on the input is dropped.
 *
 * Security posture:
 *  - Fail-closed: any malformed finding rejects the whole batch.
 *  - Untrusted instruction-like fields are rejected outright. CodeRabbit
 *    payloads may carry free-form `suggestion`, `comment`, or instructions
 *    text; we never echo those into the evidence stream. A finding that
 *    carries an `instructions`/`command`/`prompt`/`tool` field is rejected
 *    rather than silently stripped, so callers know the upstream contract
 *    changed.
 *  - Severity is validated against the contracts enum.
 *  - No network, no credentials, no CodeRabbit API client. Pure function.
 */

import type {
  CodeRabbitFinding,
  CodeRabbitFindings,
  CodeRabbitSeverity,
} from '@hermes-ops/contracts';

/** Stable reason codes for normalization failures. */
export type CodeRabbitNormalizeErrorCode =
  | 'MALFORMED'
  | 'FINDINGS_NOT_ARRAY'
  | 'FINDING_MALFORMED'
  | 'FINDING_MISSING_ID'
  | 'FINDING_INVALID_SEVERITY'
  | 'FINDING_INVALID_RESOLVED'
  | 'UNTRUSTED_INSTRUCTION_FIELD';

export interface CodeRabbitNormalizeError {
  readonly code: CodeRabbitNormalizeErrorCode;
  readonly message: string;
  /** Dotted path into the input, when applicable. */
  readonly path?: string;
}

export type CodeRabbitNormalizeResult =
  | { readonly ok: true; readonly findings: CodeRabbitFindings }
  | { readonly ok: false; readonly error: CodeRabbitNormalizeError };

const CODERABBIT_SEVERITIES: readonly CodeRabbitSeverity[] = [
  'critical',
  'high',
  'medium',
  'low',
  'info',
];

/**
 * Field names that represent untrusted instructions or tool invocations. Their
 * presence on a finding is rejected (not silently dropped) because the
 * evidence stream must never carry prompt-like content that could influence
 * downstream agents.
 */
const UNTRUSTED_INSTRUCTION_FIELDS: readonly RegExp[] = [
  /^instructions?$/i,
  /^commands?$/i,
  /^prompts?$/i,
  /^tools?$/i,
  /^actions?$/i,
  /^exec$/i,
  /^shell$/i,
  /^run$/i,
];

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const isNonEmptyString = (v: unknown): v is string =>
  typeof v === 'string' && v.length > 0;

const hasUntrustedInstructionField = (
  obj: Record<string, unknown>,
): string | undefined => {
  for (const key of Object.keys(obj)) {
    if (UNTRUSTED_INSTRUCTION_FIELDS.some((re) => re.test(key))) {
      return key;
    }
  }
  return undefined;
};

/**
 * Normalize an unknown CodeRabbit payload into the contracts
 * `CodeRabbitFindings` shape.
 *
 * Accepts either:
 *  - an object with a `findings` array (the contracts shape), or
 *  - a bare array of findings (a common raw API shape).
 *
 * Only `id`, `severity`, and `resolved` are preserved per finding. Everything
 * else (suggestions, comments, line numbers, file paths, etc.) is dropped.
 */
export const normalizeCodeRabbitFindings = (
  input: unknown,
): CodeRabbitNormalizeResult => {
  let findingsArr: unknown;
  let basePath = 'findings';

  if (Array.isArray(input)) {
    findingsArr = input;
    basePath = 'findings';
  } else if (isObject(input)) {
    if (!Array.isArray(input.findings)) {
      return {
        ok: false,
        error: {
          code: 'FINDINGS_NOT_ARRAY',
          message: 'coderabbit.findings must be an array',
          path: 'findings',
        },
      };
    }
    findingsArr = input.findings;
  } else {
    return {
      ok: false,
      error: {
        code: 'MALFORMED',
        message: 'coderabbit payload must be an object or array',
      },
    };
  }

  const findings: CodeRabbitFinding[] = [];
  const arr = findingsArr as readonly unknown[];
  for (let i = 0; i < arr.length; i++) {
    const f = arr[i];
    const path = `${basePath}[${i}]`;

    if (!isObject(f)) {
      return {
        ok: false,
        error: {
          code: 'FINDING_MALFORMED',
          message: 'finding must be an object',
          path,
        },
      };
    }

    // Reject untrusted instruction-like fields BEFORE pulling anything out.
    const badField = hasUntrustedInstructionField(f);
    if (badField) {
      return {
        ok: false,
        error: {
          code: 'UNTRUSTED_INSTRUCTION_FIELD',
          message: `finding carries untrusted instruction field '${badField}'`,
          path: `${path}.${badField}`,
        },
      };
    }

    if (!isNonEmptyString(f.id)) {
      return {
        ok: false,
        error: {
          code: 'FINDING_MISSING_ID',
          message: 'finding.id is required and must be a non-empty string',
          path: `${path}.id`,
        },
      };
    }

    if (
      typeof f.severity !== 'string' ||
      !CODERABBIT_SEVERITIES.includes(f.severity as CodeRabbitSeverity)
    ) {
      return {
        ok: false,
        error: {
          code: 'FINDING_INVALID_SEVERITY',
          message: 'finding.severity must be one of critical/high/medium/low/info',
          path: `${path}.severity`,
        },
      };
    }

    if (typeof f.resolved !== 'boolean') {
      return {
        ok: false,
        error: {
          code: 'FINDING_INVALID_RESOLVED',
          message: 'finding.resolved must be a boolean',
          path: `${path}.resolved`,
        },
      };
    }

    // Preserve ONLY id/severity/resolved. Drop everything else.
    findings.push({
      id: f.id,
      severity: f.severity as CodeRabbitSeverity,
      resolved: f.resolved,
    });
  }

  return { ok: true, findings: { findings } };
};
