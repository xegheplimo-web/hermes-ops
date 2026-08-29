/**
 * GitHub ruleset integration for Hermes Ops.
 *
 * Builds an idempotent, fail-closed ruleset that enforces the canonical
 * required status checks and required pull requests on `main`. All GitHub
 * interaction is injected through a narrow transport interface so tests stay
 * pure and no real network is needed by the core logic.
 */

/** Narrow HTTP surface for GitHub API calls. Implementations may use fetch. */
export interface GitHubTransport {
  get(path: string): Promise<unknown>;
  post(path: string, body: unknown): Promise<unknown>;
  put(path: string, body: unknown): Promise<unknown>;
}

export const HERMES_RULESET_NAME = 'hermes-policy-gate' as const;

export const HERMES_STATUS_CHECKS = [
  'build-and-test',
  'skills-python-tests',
  'hermes-policy-gate',
] as const;

export type HermesStatusCheck = (typeof HERMES_STATUS_CHECKS)[number];

export interface RulesetPayload {
  readonly name: string;
  readonly target: 'branch' | 'tag';
  readonly enforcement: 'active' | 'disabled' | 'evaluate';
  readonly conditions: {
    readonly ref_name: {
      readonly include: readonly string[];
      readonly exclude: readonly string[];
    };
  };
  readonly rules: readonly (
    | {
        readonly type: 'required_status_checks';
        readonly parameters: {
            readonly required_status_checks: readonly { readonly context: string }[];
            readonly strict_required_status_checks_policy: boolean;
        };
      }
    | {
        readonly type: 'pull_request';
        readonly parameters: {
            readonly required_approving_review_count: number;
            readonly dismiss_stale_reviews_on_push: boolean;
            readonly require_code_owner_review: boolean;
            readonly require_last_push_approval: boolean;
            readonly required_review_thread_resolution: boolean;
        };
      }
  )[];
}

/** Build the canonical Hermes ruleset payload for the default branch. */
export const buildHermesRuleset = (name: string = HERMES_RULESET_NAME): RulesetPayload => ({
  name,
  target: 'branch',
  enforcement: 'active',
  conditions: {
    ref_name: {
      include: ['refs/heads/main'],
      exclude: [],
    },
  },
  rules: [
    {
      type: 'required_status_checks',
      parameters: {
        required_status_checks: HERMES_STATUS_CHECKS.map((context) => ({ context })),
        strict_required_status_checks_policy: true,
      },
    },
    {
      type: 'pull_request',
      parameters: {
        required_approving_review_count: 1,
        dismiss_stale_reviews_on_push: false,
        require_code_owner_review: false,
        require_last_push_approval: false,
        required_review_thread_resolution: false,
      },
    },
  ],
});

export interface ApplyRulesetOptions {
  /** Repository owner (user or org). */
  readonly owner: string;
  /** Repository name. */
  readonly repo: string;
  /** Transport used to call the GitHub API. */
  readonly transport: GitHubTransport;
  /** Ruleset name; defaults to {@link HERMES_RULESET_NAME}. */
  readonly name?: string;
  /** When true, do not call the API; only return the payload. */
  readonly dryRun?: boolean;
}

export interface ApplyRulesetResult {
  readonly created: boolean;
  readonly updated: boolean;
  readonly ruleset: RulesetPayload;
  readonly response?: unknown;
}

interface RulesetSummary {
  readonly id: number;
  readonly name: string;
}

/**
 * Apply the Hermes ruleset idempotently.
 *
 * Lists existing repository rulesets. If a ruleset with the same name exists,
 * it is updated via PUT; otherwise a new ruleset is created via POST.
 *
 * In dry-run mode no network call is made and the result carries the payload
 * that would be sent.
 */
export const applyRuleset = async (
  options: ApplyRulesetOptions,
): Promise<ApplyRulesetResult> => {
  const { owner, repo, transport, name = HERMES_RULESET_NAME, dryRun = false } = options;
  const ruleset = buildHermesRuleset(name);

  if (dryRun) {
    return { created: false, updated: false, ruleset };
  }

  const list = (await transport.get(`/repos/${owner}/${repo}/rulesets`)) as unknown;
  const rulesets = Array.isArray(list) ? (list as RulesetSummary[]) : [];
  const existing = rulesets.find((r) => r.name === name);

  if (existing) {
    const response = await transport.put(
      `/repos/${owner}/${repo}/rulesets/${existing.id}`,
      ruleset,
    );
    return { created: false, updated: true, ruleset, response };
  }

  const response = await transport.post(`/repos/${owner}/${repo}/rulesets`, ruleset);
  return { created: true, updated: false, ruleset, response };
};
