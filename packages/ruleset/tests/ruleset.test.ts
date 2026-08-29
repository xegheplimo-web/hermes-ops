import { describe, expect, it } from 'vitest';
import {
  applyRuleset,
  buildHermesRuleset,
  HERMES_POLICY_GATE_CONTEXT,
  HERMES_RULESET_NAME,
  HERMES_STATUS_CHECKS,
  postCommitStatus,
  type GitHubTransport,
} from '../src/index.js';

const owner = 'acme';
const repo = 'hermes-ops';

interface Call {
  method: 'get' | 'post' | 'put';
  path: string;
  body?: unknown;
}

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

const SHA = '0123456789abcdef0123456789abcdef01234567';

describe('buildHermesRuleset', () => {
  it('produces the canonical Hermes ruleset payload', () => {
    const payload = buildHermesRuleset();
    expect(payload.name).toBe(HERMES_RULESET_NAME);
    expect(payload.target).toBe('branch');
    expect(payload.enforcement).toBe('active');
    expect(payload.conditions.ref_name.include).toContain('refs/heads/main');

    const statusRule = payload.rules.find((r) => r.type === 'required_status_checks');
    expect(statusRule).toBeDefined();
    expect(statusRule?.parameters.required_status_checks.map((c) => c.context)).toEqual(
      HERMES_STATUS_CHECKS,
    );
    expect(statusRule?.parameters.strict_required_status_checks_policy).toBe(true);

    const prRule = payload.rules.find((r) => r.type === 'pull_request');
    expect(prRule).toBeDefined();
    expect(prRule?.parameters.required_approving_review_count).toBe(0);
    expect(prRule?.parameters.dismiss_stale_reviews_on_push).toBe(false);
  });

  it('includes build-and-test, skills-python-tests, and hermes-policy-gate', () => {
    const payload = buildHermesRuleset();
    const statusRule = payload.rules.find((r) => r.type === 'required_status_checks');
    const contexts = statusRule?.parameters.required_status_checks.map((c) => c.context);
    expect(contexts).toContain('build-and-test');
    expect(contexts).toContain('skills-python-tests');
    expect(contexts).toContain('hermes-policy-gate');
  });
});

describe('applyRuleset', () => {
  it('is idempotent: creates when no matching ruleset exists', async () => {
    const { transport, calls } = makeFakeTransport([
      { id: 7, name: 'some-other-ruleset' },
    ]);

    const result = await applyRuleset({ owner, repo, transport });

    expect(result.created).toBe(true);
    expect(result.updated).toBe(false);
    expect(calls).toHaveLength(2);
    expect(calls[0]).toEqual({ method: 'get', path: `/repos/${owner}/${repo}/rulesets` });
    expect(calls[1].method).toBe('post');
    expect(calls[1].path).toBe(`/repos/${owner}/${repo}/rulesets`);
    expect(calls[1].body).toEqual(result.ruleset);
  });

  it('is idempotent: updates when a matching ruleset already exists', async () => {
    const { transport, calls } = makeFakeTransport([
      { id: 7, name: 'some-other-ruleset' },
      { id: 99, name: HERMES_RULESET_NAME },
    ]);

    const result = await applyRuleset({ owner, repo, transport });

    expect(result.created).toBe(false);
    expect(result.updated).toBe(true);
    expect(calls).toHaveLength(2);
    expect(calls[0]).toEqual({ method: 'get', path: `/repos/${owner}/${repo}/rulesets` });
    expect(calls[1].method).toBe('put');
    expect(calls[1].path).toBe(`/repos/${owner}/${repo}/rulesets/99`);
    expect(calls[1].body).toEqual(result.ruleset);
  });

  it('supports a custom ruleset name', async () => {
    const customName = 'my-hermes-gate';
    const { transport, calls } = makeFakeTransport([
      { id: 1, name: customName },
    ]);

    const result = await applyRuleset({ owner, repo, name: customName, transport });

    expect(result.ruleset.name).toBe(customName);
    expect(result.updated).toBe(true);
    expect(calls[1].path).toBe(`/repos/${owner}/${repo}/rulesets/1`);
  });

  it('dry-run returns the payload and makes no API calls', async () => {
    const { transport, calls } = makeFakeTransport();

    const result = await applyRuleset({ owner, repo, transport, dryRun: true });

    expect(result.created).toBe(false);
    expect(result.updated).toBe(false);
    expect(calls).toHaveLength(0);
    expect(result.ruleset).toEqual(buildHermesRuleset());
  });
});

describe('postCommitStatus', () => {
  it('posts to /repos/{owner}/{repo}/statuses/{sha} with context hermes-policy-gate', async () => {
    const { transport, calls } = makeFakeTransport();

    await postCommitStatus({
      owner,
      repo,
      sha: SHA,
      state: 'success',
      transport,
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe('post');
    expect(calls[0].path).toBe(`/repos/${owner}/${repo}/statuses/${SHA}`);
    expect(calls[0].body).toMatchObject({
      state: 'success',
      context: HERMES_POLICY_GATE_CONTEXT,
    });
  });

  it('passes optional description and target_url', async () => {
    const { transport, calls } = makeFakeTransport();

    await postCommitStatus({
      owner,
      repo,
      sha: SHA,
      state: 'failure',
      description: 'policy gate failed',
      targetUrl: 'https://example.com/gate',
      transport,
    });

    expect(calls[0].body).toMatchObject({
      state: 'failure',
      context: HERMES_POLICY_GATE_CONTEXT,
      description: 'policy gate failed',
      target_url: 'https://example.com/gate',
    });
  });
});
