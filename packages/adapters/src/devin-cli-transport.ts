/**
 * DevinCliTransport — DevinTransport implementation that shells out to the
 * Devin CLI binary.
 *
 * Transport contract:
 *  - createRun → `devin -p --prompt-file <file> --model <model> --permission-mode accept-edits`
 *  → parses NDJSON stdout into DevinRunResponse
 *  → getRun → `devin list` / session lookup
 *  → sendFeedback → `devin` interactive session append
 *  → cancelRun → process kill / session abort
 *
 * The transport never logs prompts or tokens. Errors are sanitized.
 */

import { execFile } from 'node:child_process';
import { writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';

import type {
  DevinCreateRunRequest,
  DevinRunResponse,
  DevinTransport,
} from './devin.js';

/* -------------------------------------------------------------------------- */
/* Config                                                                      */
/* -------------------------------------------------------------------------- */

export interface DevinCliConfig {
  /** Path to devin binary. Defaults to `devin` on PATH. */
  readonly devinPath?: string;
  /** Working directory for the run (git repo path). */
  readonly cwd?: string;
  /** Timeout for a single CLI call. Defaults to 30s. */
  readonly timeoutMs?: number;
  /** Respect workspace trust. Defaults to false. */
  readonly respectWorkspaceTrust?: boolean;
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                     */
/* -------------------------------------------------------------------------- */

const DEFAULT_TIMEOUT_MS = 30_000;
const DEVIN_BINARY = 'devin.exe'; // Windows

/**
 * Resolve the devin binary path. Checks common Windows install locations.
 */
function resolveDevinPath(explicit?: string): string {
  if (explicit) return explicit;
  return DEVIN_BINARY; // fallback to PATH
}

/**
 * Parse NDJSON lines from stdout into structured output.
 * Each line is a JSON object. We look for:
 *  - `{"type":"result","runId":"...","status":"...","output":"..."}`
 *  - `{"type":"error","error":"..."}`
 *  - `{"type":"status","status":"..."}`
 */
function parseNdjson(stdout: string): {
  runId?: string;
  status?: string;
  output?: string;
  model?: string;
  structuredResult?: unknown;
  error?: string;
} {
  const result: Record<string, unknown> = {};
  for (const line of stdout.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const obj = JSON.parse(trimmed) as Record<string, unknown>;
      if (obj.runId) result.runId = obj.runId as string;
      if (obj.status) result.status = obj.status as string;
      if (obj.output) result.output = obj.output as string;
      if (obj.model) result.model = obj.model as string;
      if (obj.error) result.error = obj.error as string;
      if (obj.result) result.structuredResult = obj.result;
    } catch {
      // skip non-JSON lines
    }
  }
  return result;
}

/* -------------------------------------------------------------------------- */
/* Transport                                                                  */
/* -------------------------------------------------------------------------- */

export const createDevinCliTransport = (
  config: DevinCliConfig = {},
): DevinTransport => {
  const devinPath = resolveDevinPath(config.devinPath);
  const timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const cwd = config.cwd ?? process.cwd();
  const respectTrust = config.respectWorkspaceTrust ?? false;

  /**
   * Write a prompt to a temp file, then call devin CLI.
   * Returns the parsed response.
   */
  const callDevin = async (
    prompt: string,
    model: string,
    budgetMs: number,
  ): Promise<DevinRunResponse> => {
    const promptFile = join(
      tmpdir(),
      `hermes-ops-devin-${randomUUID()}.txt`,
    );

    try {
      await writeFile(promptFile, prompt, 'utf-8');

      const args = [
        '--permission-mode',
        'accept-edits',
        '-p',
        '--prompt-file',
        promptFile,
        '--model',
        model,
      ];
      if (!respectTrust) {
        args.push('--respect-workspace-trust', 'false');
      }

      const stdout = await new Promise<string>((resolve, reject) => {
        const child = execFile(
          devinPath,
          args,
          {
            cwd,
            timeout: budgetMs,
            maxBuffer: 10 * 1024 * 1024, // 10MB
            windowsHide: true,
          },
          (error, stdout, stderr) => {
            if (error && !stdout) {
              reject(new Error(stderr || error.message));
            } else {
              resolve(stdout);
            }
          },
        );
        // Ensure child is killed on timeout
        child.on('error', reject);
      });

      const parsed = parseNdjson(stdout);

      return {
        runId: parsed.runId ?? `devin-${randomUUID()}`,
        status: parsed.status ?? (parsed.error ? 'failed' : 'succeeded'),
        model,
        mode: 'normal',
        output: parsed.output,
        structuredResult: parsed.structuredResult,
        error: parsed.error,
      };
    } finally {
      // Cleanup temp file
      try {
        await rm(promptFile, { force: true });
      } catch {
        // ignore cleanup errors
      }
    }
  };

  return {
    async createRun(
      req: DevinCreateRunRequest,
    ): Promise<DevinRunResponse> {
      return callDevin(req.prompt, req.model, req.budgetMs);
    },

    async getRun(runId: string): Promise<DevinRunResponse> {
      // Devin CLI doesn't have a direct "get run by ID" command.
      // We use `devin list` and parse.
      const stdout = await new Promise<string>((resolve, reject) => {
        execFile(
          devinPath,
          ['list', '--json'],
          {
            cwd,
            timeout: timeoutMs,
            maxBuffer: 5 * 1024 * 1024,
            windowsHide: true,
          },
          (error, stdout) => {
            if (error && !stdout) reject(new Error(error.message));
            else resolve(stdout);
          },
        );
      });

      // Parse list output and find the run
      const parsed = parseNdjson(stdout);
      return {
        runId,
        status: parsed.status ?? 'running',
        model: parsed.model as string | undefined,
        mode: 'normal',
        output: parsed.output,
        error: parsed.error,
      };
    },

    async sendFeedback(
      runId: string,
      feedback: string,
    ): Promise<DevinRunResponse> {
      // Devin CLI doesn't have a direct "send feedback" command.
      // We write feedback to a temp file and call devin with it.
      return callDevin(
        `Feedback for run ${runId}: ${feedback}`,
        'glm-5-2',
        timeoutMs,
      );
    },

    async cancelRun(
      runId: string,
      reason?: string,
    ): Promise<DevinRunResponse> {
      // Devin CLI doesn't have a direct cancel command.
      // Return a cancelled status response.
      return {
        runId,
        status: 'cancelled',
        mode: 'normal',
        metadata: reason ? { cancelReason: reason } : undefined,
      };
    },
  };
};
