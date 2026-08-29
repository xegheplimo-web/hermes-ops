import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { runCli, type CliIo, type GitHubTransport } from '../src/index.js';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('../../', import.meta.url));
const binPath = fileURLToPath(new URL('../dist/cli.js', import.meta.url));

interface RunResult {
  status: number;
  stdout: string;
  stderr: string;
}

const run = (args: readonly string[]): RunResult => {
  try {
    const stdout = execFileSync(process.execPath, [binPath, ...args], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      maxBuffer: 8 * 1024 * 1024,
    });
    return { status: 0, stdout, stderr: '' };
  } catch (e) {
    const err = e as { status?: number; stdout?: string; stderr?: string };
    return {
      status: err.status ?? -1,
      stdout: err.stdout ?? '',
      stderr: err.stderr ?? '',
    };
  }
};

interface Call {
  method: 'get' | 'post' | 'put';
  path: string;
  body?: unknown;
}

const makeIo = (env: Record<string, string> = {}): { io: CliIo; out: string[]; err: string[] } => {
  const out: string[] = [];
  const err: string[] = [];
  const io: CliIo = {
    stdout: { write: (s: string) => out.push(s) },
    stderr: { write: (s: string) => err.push(s) },
    getEnv: (name: string) => env[name],
  };
  return { io, out, err };
};

const makeFakeTransport = (listResponse: unknown = []): { transport: GitHubTransport; calls: Call[] } => {
  const calls: Call[] = [];
  const transport: GitHubTransport = {
    async get(path: string): Promise<unknown> {
      calls.push({ method: 'get', path });
      return listResponse;
    },
    async post(path: string, body: unknown): Promise<unknown> {
      calls.push({ method: 'post', path, body });
      return { id: 42 };
    },
    async put(path: string, body: unknown): Promise<unknown> {
      calls.push({ method: 'put', path, body });
      return { id: 1 };
    },
  };
  return { transport, calls };
};

beforeAll(() => {
  // Build the workspace so the ruleset binary exists for subprocess tests.
  execFileSync('pnpm', ['build'], {
    cwd: repoRoot,
    stdio: 'pipe',
    shell: true,
    maxBuffer: 16 * 1024 * 1024,
  });
}, 120_000);

describe('hermes-ruleset --help', () => {
  it('prints usage and exits 0', () => {
    const r = run(['--help']);
    expect(r.status).toBe(0);
    expect(r.stdout).toContain('usage:');
    expect(r.stdout).toContain('apply');
    expect(r.stdout).toContain('status');
    expect(r.stderr).toBe('');
  });
});

describe('hermes-ruleset apply --dry-run', () => {
  it('prints the ruleset payload with the canonical status checks', () => {
    const r = run(['apply', '--owner', 'acme', '--repo', 'hermes-ops', '--dry-run']);
    expect(r.status).toBe(0);
    expect(r.stderr).toBe('');
    const out = JSON.parse(r.stdout) as { rules: unknown[] };
    const statusRule = (out.rules as { parameters: { required_status_checks: { context: string }[] } }[]).find(
      (rule: { parameters?: { required_status_checks?: { context: string }[] } }) =>
        Array.isArray(rule.parameters?.required_status_checks),
    );
    const contexts = statusRule?.parameters.required_status_checks.map((c) => c.context) ?? [];
    expect(contexts).toEqual(['build-and-test', 'skills-python-tests', 'hermes-policy-gate']);
    const prRule = (out.rules as { type: string }[]).find((rule) => rule.type === 'pull_request');
    expect(prRule).toBeDefined();
  });
});

describe('runCli — unit', () => {
  it('returns exit 2 on missing command', async () => {
    const { io, out, err } = makeIo();
    const code = await runCli([], io);
    expect(code).toBe(2);
    expect(err.join('')).toContain('usage:');
  });

  it('returns exit 2 when apply is missing owner', async () => {
    const { io, err } = makeIo();
    const code = await runCli(['apply', '--repo', 'hermes-ops'], io);
    expect(code).toBe(2);
    expect(err.join('')).toContain('--owner');
  });

  it('returns exit 2 when apply has no token and is not dry-run', async () => {
    const { io, err } = makeIo();
    const { transport } = makeFakeTransport();
    const code = await runCli(['apply', '--owner', 'acme', '--repo', 'hermes-ops'], io, transport);
    expect(code).toBe(2);
    expect(err.join('')).toContain('token');
  });

  it('applies the ruleset idempotently', async () => {
    const { io, out } = makeIo({ GITHUB_TOKEN: 'fake' });
    const { transport, calls } = makeFakeTransport([
      { id: 1, name: 'hermes-policy-gate' },
    ]);
    const code = await runCli(['apply', '--owner', 'acme', '--repo', 'hermes-ops'], io, transport);
    expect(code).toBe(0);
    expect(calls).toHaveLength(2);
    expect(calls[0].method).toBe('get');
    expect(calls[1].method).toBe('put');
    expect(out.join('')).toContain('"updated"');
  });

  it('posts a commit status', async () => {
    const sha = '0123456789abcdef0123456789abcdef01234567';
    const { io, out } = makeIo({ GITHUB_TOKEN: 'fake' });
    const { transport, calls } = makeFakeTransport();
    const code = await runCli(
      ['status', '--owner', 'acme', '--repo', 'hermes-ops', '--sha', sha, '--state', 'success'],
      io,
      transport,
    );
    expect(code).toBe(0);
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe('post');
    expect(calls[0].path).toBe(`/repos/acme/hermes-ops/statuses/${sha}`);
    expect(calls[0].body).toMatchObject({ state: 'success', context: 'hermes-policy-gate' });
    expect(out.join('')).toContain(sha);
  });
});

afterAll(() => {
  // No persistent temp state to clean up.
});
