/**
 * Bind a policy-gate result to a head SHA via the GitHub commit status API.
 *
 * The context is always `hermes-policy-gate` so the ruleset's required status
 * check and the posted status share the same identity. All HTTP calls are
 * injected through {@link GitHubTransport} for testability.
 */

import type { GitHubTransport } from './ruleset.js';

export const HERMES_POLICY_GATE_CONTEXT = 'hermes-policy-gate' as const;

export type CommitState = 'success' | 'failure' | 'error' | 'pending';

export interface PostCommitStatusOptions {
  /** Repository owner (user or org). */
  readonly owner: string;
  /** Repository name. */
  readonly repo: string;
  /** 40-character lowercase hex HEAD SHA. */
  readonly sha: string;
  /** Commit status state. */
  readonly state: CommitState;
  /** Status context; defaults to {@link HERMES_POLICY_GATE_CONTEXT}. */
  readonly context?: string;
  /** Short description shown in the GitHub UI. */
  readonly description?: string;
  /** Optional URL for the status detail. */
  readonly targetUrl?: string;
  /** Transport used to call the GitHub API. */
  readonly transport: GitHubTransport;
}

/**
 * Post a commit status to the head SHA.
 *
 * Calls POST /repos/{owner}/{repo}/statuses/{sha} with the given state and the
 * hermes-policy-gate context. Any optional description or target URL is passed
 * through without modification.
 */
export const postCommitStatus = async (
  options: PostCommitStatusOptions,
): Promise<unknown> => {
  const {
    owner,
    repo,
    sha,
    state,
    context = HERMES_POLICY_GATE_CONTEXT,
    description,
    targetUrl,
    transport,
  } = options;

  const body: Record<string, unknown> = { state, context };
  if (description !== undefined) {
    body.description = description;
  }
  if (targetUrl !== undefined) {
    body.target_url = targetUrl;
  }

  return transport.post(`/repos/${owner}/${repo}/statuses/${sha}`, body);
};
