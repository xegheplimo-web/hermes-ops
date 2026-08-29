#!/usr/bin/env node
/**
 * `hermes-ruleset` CLI — idempotent GitHub ruleset + head-SHA status binding.
 *
 * Commands:
 *   apply   Create or update the Hermes ruleset on a repository.
 *   status  Post a hermes-policy-gate commit status to a head SHA.
 *
 * Exit codes:
 *   0 — success
 *   1 — operational failure (API error, invalid state, ...)
 *   2 — usage error (bad args, missing flags, ...)
 */

import { fileURLToPath } from 'node:url';
import { applyRuleset, buildHermesRuleset, HERMES_RULESET_NAME, type GitHubTransport } from './ruleset.js';
import { HERMES_POLICY_GATE_CONTEXT, postCommitStatus, type CommitState } from './head-sha.js';

const SHA1_RE = /^[0-9a-f]{40}$/;
const OWNER_REPO_RE = /^[A-Za-z0-9_.-]+$/;
const API_VERSION = '2022-11-28';

const USAGE =
  'usage: hermes-ruleset <command> [options]\n' +
  '\n' +
  'Commands:\n' +
  '  apply   Create or update the Hermes ruleset (idempotent).\n' +
  '  status  Post a hermes-policy-gate commit status to a head SHA.\n' +
  '\n' +
  'apply options:\n' +
  '  --owner <owner>          Repository owner (required)\n' +
  '  --repo <repo>            Repository name (required)\n' +
  '  --name <name>            Ruleset name (default: hermes-policy-gate)\n' +
  '  --dry-run                Print the ruleset payload and do not call the API\n' +
  '  --token <token>          GitHub token (or GITHUB_TOKEN env)\n' +
  '  --base-url <url>         GitHub API base (default: https://api.github.com)\n' +
  '\n' +
  'status options:\n' +
  '  --owner <owner>          Repository owner (required)\n' +
  '  --repo <repo>            Repository name (required)\n' +
  '  --sha <sha>              40-char lowercase hex HEAD SHA (required)\n' +
  '  --state <state>          One of: success, failure, error, pending (required)\n' +
  '  --context <context>      Status context (default: hermes-policy-gate)\n' +
  '  --description <text>     Short status description\n' +
  '  --target-url <url>       Detail URL for the status\n' +
  '  --token <token>          GitHub token (or GITHUB_TOKEN env)\n' +
  '  --base-url <url>         GitHub API base (default: https://api.github.com)\n' +
  '\n' +
  'Exit codes: 0=success, 1=operational failure, 2=usage error';

/** Usage error: bad invocation. Maps to exit code 2. */
export class UsageError extends Error {
  override readonly name = 'UsageError';
}

/** Operational error: API / transport failure. Maps to exit code 1. */
export class OperationalError extends Error {
  override readonly name = 'OperationalError';
}

/** Injectable stdio + env surface so the CLI logic is testable. */
export interface CliIo {
  readonly stdout: { write(s: string): void };
  readonly stderr: { write(s: string): void };
  getEnv(name: string): string | undefined;
}

type Command = 'apply' | 'status';

interface CommonFlags {
  owner?: string;
  repo?: string;
  token?: string;
  baseUrl?: string;
}

interface ApplyFlags extends CommonFlags {
  name?: string;
  dryRun?: boolean;
}

interface StatusFlags extends CommonFlags {
  sha?: string;
  state?: string;
  context?: string;
  description?: string;
  targetUrl?: string;
}

type ParsedArgs =
  | { command: 'apply'; flags: ApplyFlags }
  | { command: 'status'; flags: StatusFlags };

const isCommand = (s: string): s is Command => s === 'apply' || s === 'status';

const isBoolFlag = (name: string): boolean => name === '--dry-run';

const parse = (argv: readonly string[]): ParsedArgs => {
  if (argv.length === 0) {
    throw new UsageError('command is required');
  }

  const first = argv[0];
  if (!first || !isCommand(first)) {
    throw new UsageError(`unknown command: ${first}`);
  }

  const command = first;
  const flags: Record<string, string | boolean | undefined> = {};

  for (let i = 1; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg) continue;

    if (!arg.startsWith('--')) {
      throw new UsageError(`unexpected positional argument: ${arg}`);
    }

    const eq = arg.indexOf('=');
    let name: string;
    let value: string | boolean | undefined;
    if (eq >= 0) {
      name = arg.slice(0, eq);
      const raw = arg.slice(eq + 1);
      value = isBoolFlag(name) ? raw === 'true' : raw;
    } else if (isBoolFlag(arg)) {
      name = arg;
      value = true;
    } else {
      name = arg;
      const next = argv[++i];
      value = next;
    }

    if (value === undefined || value === '') {
      throw new UsageError(`missing value for ${name}`);
    }

    if (typeof value === 'string' && value.startsWith('--') && !isBoolFlag(name)) {
      throw new UsageError(`missing value for ${name}`);
    }

    if (command === 'apply' && !APPLY_FLAGS.has(name)) {
      throw new UsageError(`unknown flag for apply: ${name}`);
    }
    if (command === 'status' && !STATUS_FLAGS.has(name)) {
      throw new UsageError(`unknown flag for status: ${name}`);
    }

    const key = kebabToCamel(name.slice(2));
    flags[key] = value;
  }

  if (command === 'apply') {
    return { command, flags: flags as ApplyFlags };
  }
  return { command, flags: flags as StatusFlags };
};

const APPLY_FLAGS = new Set<string>([
  '--owner',
  '--repo',
  '--name',
  '--dry-run',
  '--token',
  '--base-url',
]);

const STATUS_FLAGS = new Set<string>([
  '--owner',
  '--repo',
  '--sha',
  '--state',
  '--context',
  '--description',
  '--target-url',
  '--token',
  '--base-url',
]);

const kebabToCamel = (s: string): string =>
  s.replace(/-([a-z])/g, (_, c) => (c ? c.toUpperCase() : ''));

const validateOwnerRepo = (flags: CommonFlags): { owner: string; repo: string } => {
  if (!flags.owner || !OWNER_REPO_RE.test(flags.owner)) {
    throw new UsageError('--owner is required and must be a valid GitHub owner');
  }
  if (!flags.repo || !OWNER_REPO_RE.test(flags.repo)) {
    throw new UsageError('--repo is required and must be a valid GitHub repository name');
  }
  return { owner: flags.owner, repo: flags.repo };
};

const validateToken = (flags: CommonFlags, io: CliIo): string => {
  const token = flags.token ?? io.getEnv('GITHUB_TOKEN') ?? io.getEnv('GH_TOKEN');
  if (!token) {
    throw new UsageError('GitHub token is required: use --token or set GITHUB_TOKEN');
  }
  return token;
};

const createFetchTransport = (token: string, baseUrl = 'https://api.github.com'): GitHubTransport => ({
  async get(path: string): Promise<unknown> {
    const res = await fetch(`${baseUrl}${path}`, {
      method: 'GET',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': API_VERSION,
      },
    });
    if (!res.ok) {
      throw new OperationalError(`GitHub API GET ${path} failed: ${res.status}`);
    }
    return res.json();
  },
  async post(path: string, body: unknown): Promise<unknown> {
    const res = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': API_VERSION,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new OperationalError(`GitHub API POST ${path} failed: ${res.status}`);
    }
    return res.json();
  },
  async put(path: string, body: unknown): Promise<unknown> {
    const res = await fetch(`${baseUrl}${path}`, {
      method: 'PUT',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': API_VERSION,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new OperationalError(`GitHub API PUT ${path} failed: ${res.status}`);
    }
    return res.json();
  },
});

const write = (io: CliIo, s: string): void => {
  io.stdout.write(s);
};

const safeError = (io: CliIo, message: string): void => {
  // Never echo tokens, raw payloads, or secrets here.
  io.stderr.write(`${message}\n`);
};

const COMMIT_STATES: readonly CommitState[] = ['success', 'failure', 'error', 'pending'];

const isCommitState = (s: string): s is CommitState => COMMIT_STATES.includes(s as CommitState);

const showHelp = (io: CliIo): number => {
  write(io, USAGE);
  write(io, '\n');
  return 0;
};

const showUsageError = (io: CliIo, message?: string): number => {
  safeError(io, USAGE);
  if (message) safeError(io, message);
  return 2;
};

export const runCli = async (
  argv: readonly string[],
  io: CliIo,
  transport?: GitHubTransport,
): Promise<number> => {
  if (argv.includes('--help') || argv.includes('-h')) {
    return showHelp(io);
  }

  let parsed: ParsedArgs;
  try {
    parsed = parse(argv);
  } catch (e) {
    if (e instanceof UsageError) {
      return showUsageError(io, e.message);
    }
    safeError(io, 'internal error while parsing arguments');
    return 2;
  }

  try {
    const { owner, repo } = validateOwnerRepo(parsed.flags);

    if (parsed.command === 'apply') {
      const ruleset = buildHermesRuleset(parsed.flags.name ?? HERMES_RULESET_NAME);

      if (parsed.flags.dryRun) {
        write(io, `${JSON.stringify(ruleset, null, 2)}\n`);
        return 0;
      }

      const token = validateToken(parsed.flags, io);
      const baseUrl = parsed.flags.baseUrl ?? 'https://api.github.com';
      const t = transport ?? createFetchTransport(token, baseUrl);
      const result = await applyRuleset({ owner, repo, transport: t, name: ruleset.name });
      const action = result.created ? 'created' : 'updated';
      write(io, `${JSON.stringify({ ok: true, action, ruleset: result.ruleset }, null, 2)}\n`);
      return 0;
    }

    // status command
    const flags = parsed.flags as StatusFlags;
    if (!flags.sha || !SHA1_RE.test(flags.sha)) {
      return showUsageError(io, '--sha is required and must be 40 lowercase hex characters');
    }
    if (!flags.state || !isCommitState(flags.state)) {
      return showUsageError(io, '--state is required and must be one of success, failure, error, pending');
    }

    const token = validateToken(flags, io);
    const baseUrl = flags.baseUrl ?? 'https://api.github.com';
    const t = transport ?? createFetchTransport(token, baseUrl);
    const context = flags.context ?? HERMES_POLICY_GATE_CONTEXT;
    await postCommitStatus({
      owner,
      repo,
      sha: flags.sha,
      state: flags.state,
      context,
      description: flags.description,
      targetUrl: flags.targetUrl,
      transport: t,
    });
    write(
      io,
      `${JSON.stringify({ ok: true, context, state: flags.state, sha: flags.sha }, null, 2)}\n`,
    );
    return 0;
  } catch (err: unknown) {
    if (err instanceof UsageError) {
      return showUsageError(io, err.message);
    }
    const message = err instanceof Error ? err.message : 'command failed';
    safeError(io, message);
    return 1;
  }
};

const realIo: CliIo = {
  stdout: process.stdout,
  stderr: process.stderr,
  getEnv: (name: string) => process.env[name],
};

const isMain = (): boolean => {
  const main = process.argv[1];
  if (!main) return false;
  try {
    return fileURLToPath(import.meta.url) === main;
  } catch {
    return false;
  }
};

if (isMain()) {
  const code = await runCli(process.argv.slice(2), realIo);
  process.exit(code);
}
