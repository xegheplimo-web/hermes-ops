/**
 * `hermes-policy-gate` CLI — local, deterministic policy gate.
 *
 * Wraps the pure contracts + policy evaluator in a small, strict command-line
 * interface. No GitHub SDK, HTTP, database, agent calls, credentials, or
 * network. The CLI reads a JSON evidence manifest from disk, evaluates it
 * against the expected head SHA and policy version, and emits a stable,
 * machine-readable JSON result (no full manifest or source content).
 *
 * Exit codes:
 *   0 — policy PASS
 *   1 — policy failure / invalid evidence (evaluator returned `fail`)
 *   2 — usage or operational errors (bad args, unreadable file, bad JSON, ...)
 *
 * On usage/operational errors only a short, safe message is written to stderr;
 * secrets and raw manifest JSON are never printed. On pass/fail the stable
 * result JSON is written to stdout (or `--output` file) and the exit code
 * distinguishes the outcome.
 */

import { evaluatePolicy, classifyFromPolicyResult, type PolicyResult } from '@hermes-ops/policy';
import {
  type HumanApprovalToken,
} from './approval.js';

const HEAD_SHA_RE = /^[0-9a-f]{40}$/;
const SEMVER_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

const KNOWN_FLAGS = [
  '--manifest',
  '--head-sha',
  '--policy-version',
  '--output',
  '--approval',
] as const;
type KnownFlag = (typeof KNOWN_FLAGS)[number];

const USAGE =
  'usage: hermes-policy-gate --manifest <file.json> --head-sha <40-lowercase-hex> ' +
  '--policy-version <semver> [--output <file.json>]';

/** Injectable filesystem + stdio surface so the CLI logic is testable. */
export interface CliIo {
  readonly stdout: { write(s: string): void };
  readonly stderr: { write(s: string): void };
  readFileSync(path: string, encoding: 'utf8'): string;
  writeFileSync(path: string, data: string, encoding: 'utf8'): void;
  statSync(path: string): CliStatResult;
}

export interface CliStatResult {
  isFile(): boolean;
  isDirectory(): boolean;
}

export interface CliOptions {
  readonly manifestPath: string;
  readonly headSha: string;
  readonly policyVersion: string;
  readonly outputPath?: string;
  readonly approvalToken?: string;
}

/** Usage error: bad invocation. Maps to exit code 2. */
export class UsageError extends Error {
  override readonly name = 'UsageError';
  constructor(message: string) {
    super(message);
  }
}

/** Operational error: IO / parse failure. Maps to exit code 2. */
export class OperationalError extends Error {
  override readonly name = 'OperationalError';
  constructor(message: string) {
    super(message);
  }
}

/**
 * Parse a strict argv list into {@link CliOptions}.
 *
 * Accepts `--flag value` and `--flag=value`. Rejects unknown flags, positional
 * arguments, missing flags, and flags without a value. Does not validate flag
 * *values* beyond non-emptiness — format validation happens in {@link runCli}.
 */
type MutableCliOptions = {
  manifestPath?: string;
  headSha?: string;
  policyVersion?: string;
  outputPath?: string;
  approvalToken?: string;
};

export const parseArgs = (argv: readonly string[]): CliOptions => {
  const known = new Set<string>(KNOWN_FLAGS);
  const opts: MutableCliOptions = {};

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg) continue;
    if (!arg.startsWith('--')) {
      throw new UsageError(`unexpected positional argument: ${arg}`);
    }
    const eq = arg.indexOf('=');
    let name: string;
    let value: string | undefined;
    if (eq >= 0) {
      name = arg.slice(0, eq);
      value = arg.slice(eq + 1);
    } else {
      name = arg;
      value = argv[++i];
    }
    if (!known.has(name)) {
      throw new UsageError(`unknown argument: ${name}`);
    }
    if (value === undefined || value === '') {
      throw new UsageError(`missing value for ${name}`);
    }
    if (value.startsWith('--')) {
      throw new UsageError(`missing value for ${name}`);
    }
    setFlag(opts, name as KnownFlag, value);
  }

  if (opts.manifestPath === undefined) {
    throw new UsageError('--manifest is required');
  }
  if (opts.headSha === undefined) {
    throw new UsageError('--head-sha is required');
  }
  if (opts.policyVersion === undefined) {
    throw new UsageError('--policy-version is required');
  }
  return opts as CliOptions;
};

const setFlag = (opts: MutableCliOptions, name: KnownFlag, value: string): void => {
  switch (name) {
    case '--manifest':
      opts.manifestPath = value;
      break;
    case '--head-sha':
      opts.headSha = value;
      break;
    case '--policy-version':
      opts.policyVersion = value;
      break;
    case '--output':
      opts.outputPath = value;
      break;
    case '--approval':
      opts.approvalToken = value;
      break;
  }
};

/** Stable, machine-readable result JSON. No manifest or source content. */
export interface GateResultJson {
  readonly decision: PolicyResult['decision'];
  readonly reasonCode: PolicyResult['reasonCode'];
  readonly policyVersion: string;
  readonly evidenceIdentity?: string;
  readonly detail: string;
}

/**
 * Project a {@link PolicyResult} into the stable JSON shape emitted by the CLI.
 * `evidenceIdentity` is included only when present; `manifest` is never included.
 */
export const formatResult = (result: { decision: string; reasonCode: string; policyVersion: string; evidenceIdentity?: string; detail: string }): GateResultJson => {
  // Fixed key order for stable serialization:
  //   decision, reasonCode, policyVersion, evidenceIdentity?, detail
  if (result.evidenceIdentity !== undefined) {
    return {
      decision: result.decision as PolicyResult['decision'],
      reasonCode: result.reasonCode as PolicyResult['reasonCode'],
      policyVersion: result.policyVersion,
      evidenceIdentity: result.evidenceIdentity,
      detail: result.detail,
    };
  }
  return {
    decision: result.decision as PolicyResult['decision'],
    reasonCode: result.reasonCode as PolicyResult['reasonCode'],
    policyVersion: result.policyVersion,
    detail: result.detail,
  };
};

const safeError = (io: CliIo, message: string): void => {
  // Never echo secrets, file contents, or raw JSON here — only short messages.
  io.stderr.write(`${message}\n`);
};

/** Write result JSON to output (file or stdout). */
const writeResult = (io: CliIo, outputPath: string | undefined, json: string): void => {
  if (outputPath !== undefined) {
    io.writeFileSync(outputPath, json, 'utf8');
  } else {
    io.stdout.write(json);
  }
};

/**
 * Run the CLI against an argv list and an injectable IO surface.
 * Returns the process exit code (0 pass, 1 policy fail, 2 usage/operational).
 * Never throws — all errors are mapped to an exit code and a safe stderr line.
 */
export const runCli = (argv: readonly string[], io: CliIo): number => {
  let opts: CliOptions;
  try {
    opts = parseArgs(argv);
  } catch (e) {
    if (e instanceof UsageError) {
      safeError(io, USAGE);
      safeError(io, e.message);
      return 2;
    }
    safeError(io, 'internal error while parsing arguments');
    return 2;
  }

  // Validate flag value formats before any IO.
  if (!HEAD_SHA_RE.test(opts.headSha)) {
    safeError(io, USAGE);
    safeError(io, '--head-sha must be 40 lowercase hex characters');
    return 2;
  }
  if (!SEMVER_RE.test(opts.policyVersion)) {
    safeError(io, USAGE);
    safeError(io, '--policy-version must be a semver string');
    return 2;
  }

  // Read the manifest file. Reject missing paths and non-file paths.
  let raw: string;
  try {
    const stat = io.statSync(opts.manifestPath);
    if (!stat.isFile()) {
      safeError(io, `manifest path is not a regular file: ${opts.manifestPath}`);
      return 2;
    }
    raw = io.readFileSync(opts.manifestPath, 'utf8');
  } catch {
    safeError(io, `cannot read manifest file: ${opts.manifestPath}`);
    return 2;
  }

  // Parse JSON. A syntactically invalid file is an operational error (exit 2),
  // distinct from a structurally malformed manifest (evaluator fail, exit 1).
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    safeError(io, `manifest is not valid JSON: ${opts.manifestPath}`);
    return 2;
  }

  // Evaluate. The evaluator is pure and never throws on bad input.
  const result = evaluatePolicy(parsed, {
    expectedHeadSha: opts.headSha,
    policyVersion: opts.policyVersion,
  });

  // Human approval gate: check if the risk class requires human approval.
    const riskClass = classifyFromPolicyResult(result);
    if (riskClass === 'human-required') {
    if (opts.approvalToken === undefined) {
      // No token → block with HUMAN_APPROVAL_REQUIRED.
      const blocked = {
        decision: 'fail',
        reasonCode: 'HUMAN_APPROVAL_REQUIRED',
        policyVersion: opts.policyVersion,
        detail: `human approval required for risk class: ${riskClass}`,
      };
      writeResult(io, opts.outputPath, `${JSON.stringify(formatResult(blocked), null, 2)}\n`);
      return 1;
    }
    // Parse and validate the approval token.
    let token: HumanApprovalToken;
    try {
      token = JSON.parse(opts.approvalToken);
    } catch {
      safeError(io, USAGE);
      safeError(io, '--approval must be valid JSON');
      return 2;
    }
    if (!token.signedAt || !token.approver || !token.reason || !token.signature) {
      safeError(io, USAGE);
      safeError(io, '--approval must be a valid JSON token with signedAt, approver, reason, and signature');
      return 2;
    }
    // Token is valid — proceed with the original evaluation result.
  }

  const json = `${JSON.stringify(formatResult(result), null, 2)}\n`;

  // Emit. If --output is given, write to file; otherwise stdout.
  if (opts.outputPath !== undefined) {
    try {
      try {
        const stat = io.statSync(opts.outputPath);
        if (stat.isDirectory()) {
          safeError(io, `output path is a directory: ${opts.outputPath}`);
          return 2;
        }
      } catch {
        // Target does not exist yet — fine.
      }
      io.writeFileSync(opts.outputPath, json, 'utf8');
    } catch {
      safeError(io, `cannot write output file: ${opts.outputPath}`);
      return 2;
    }
  } else {
    io.stdout.write(json);
  }

  return result.decision === 'pass' ? 0 : 1;
};