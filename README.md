# Hermes Ops

Lean Hermes control plane for **canonical project execution**: evidence manifests, deterministic policy, PostgreSQL Ops DB, coding-agent orchestration (Devin primary / OpenCode secondary / Codex read-only review), and a fail-closed policy gate.

`hermes-ops` is the operational brain behind a governed pipeline: every implementation task flows through evidence → Task DAG → Ops DB → Devin → OpenCode (repair) → Codex review → PR → CI → policy gate → merge. Hermes (the agent) is the Brain + Judge + Orchestrator; this repo is the deterministic machinery it runs on.

## Authority model

```text
USER
  ↓
HERMES (Brain + Judge + Orchestrator)
  ↓
Git / Repo      = current implementation truth
Ops DB          = authoritative runtime/task truth
AgentMemory     = historical context only
  ↓
Task DAG → Ops DB → Devin (primary executor)
  ↓
OpenCode (secondary / bounded repair)
  ↓
Codex (READ-ONLY independent reviewer)
  ↓
PR → CI + Security → Hermes reconcile → policy-gate → PASS / REPAIR / HUMAN
```

Source of truth order: **Repo > AgentMemory**, **Ops DB > AgentMemory for runtime state**, **verified execution evidence > agent claim**, **policy gate > informal approval**.

## Boundary

`hermes-ops` owns:

- evidence manifest contracts (`packages/contracts`)
- deterministic policy decisions (`packages/policy`)
- PostgreSQL Ops DB schema and queue primitives (`packages/db`)
- GitHub evidence/enforcement adapters and webhook verification (`packages/adapters`)
- coding-agent adapters, Devin first (`packages/adapters`)
- deterministic `hermes-policy-gate` CLI (`packages/gate`)
- governance skills: orchestrator scripts, approval flow, redaction gate, e2e canonical pipeline tests (`skills/software-development/`)
- audit and reconciliation (Ops DB `audit_events`)

It does not own source analysis or the Understand Anything graph. That product remains in the sibling repository:

```text
G:/Agent-Tools/Understand-Anything
```

## Layout

```text
packages/contracts  # EvidenceManifest v1 types, runtime validation, evidence identity
packages/policy     # deterministic fail-closed policy evaluator
packages/db         # PostgreSQL migrations, queue claim/recovery SQL, retry/backoff helpers
packages/adapters   # GitHub webhook verification, CodeRabbit normalization, coding-agent adapters
packages/gate       # hermes-policy-gate local deterministic CLI around the policy evaluator
packages/ruleset    # hermes-ruleset CLI + GitHub ruleset / head-SHA status binding
skills/             # governance skills (orchestrator + tests, wired into CI)
e2e/                # end-to-end smoke tests
machine-discovery/  # repository/drive discovery artifacts
devin/              # devin task spec artifacts from real pipeline runs
.hermes/            # plans, reviews, governance artifacts of pipeline runs
```

## EvidenceManifest v1

`packages/contracts` defines a versioned manifest bound to repository identity,
an optional PR number, the head SHA, the policy version, a freshness timestamp,
artifact references (relative paths + SHA-256 hashes), CI conclusions, optional
CodeRabbit findings, optional Devin run metadata, and a source adapter.

`validateEvidenceManifest(input, { expectedHeadSha, now?, maxAgeMs? })` performs
runtime validation and rejects:

- malformed input and missing required fields
- unsupported `schemaVersion`
- absolute paths, path traversal (`..`), and duplicate artifact paths
- invalid SHA-256 (must be 64 lowercase hex) and invalid head SHA
- secret-looking field names (e.g. `token`, `api_key`, `secret`)
- stale or future timestamps (default 24h window, 5m future skew)
- head-SHA mismatch against the expected SHA
- invalid CI conclusions, CodeRabbit findings, Devin metadata, and source adapter

`computeEvidenceIdentity(manifest)` returns a deterministic SHA-256 over a
canonical JSON serialization of a validated manifest.

## Policy evaluator

`packages/policy` exposes `evaluatePolicy(input, options)`, a pure, fail-closed
function returning `{ decision, reasonCode, policyVersion, evidenceIdentity?,
detail, manifest? }`. Stable reason codes: `PASS`, `EVIDENCE_INVALID`,
`EVIDENCE_STALE`, `HEAD_SHA_MISMATCH`, `CI_NOT_GREEN`,
`UNRESOLVED_CRITICAL_FINDING`, `DUPLICATE_EVIDENCE`, `POLICY_VERSION_MISMATCH`.

Checks run in order, first failure wins:

1. manifest validation (structural, freshness, head-SHA mismatch)
2. policy version matches the evaluator's configured version
3. idempotency key not already seen (via injected `seenIdempotencyKeys`)
4. CI green (rollup `success`; checks may be `success`/`neutral`/`skipped`)
5. no unresolved critical CodeRabbit finding

Duplicate detection is driven by an injected `seenIdempotencyKeys` set so the
evaluator stays stateless and deterministic; persistence lives in the Ops DB.

## Ops DB schema and queue primitives

`packages/db` ships the control-plane schema and queue primitives. The runtime
database is PostgreSQL 16 (see `docker-compose.yml`); migrations are ordered,
idempotent, and applied via `pnpm db:migrate`.

**Migrations** (`packages/db/src/migrations/`, ordered by dependency):

1. `0001_init_tasks.sql` — top-level work unit, idempotent by `external_id`,
   bound to repository/PR/head SHA/policy version, with queue fields
   (`status`, `attempts`, `max_attempts`, `available_at`, `locked_at`,
   `locked_by`, `last_error`).
2. `0002_init_jobs.sql` — per-task executable unit, same queue fields.
3. `0003_init_agent_runs.sql` — one generic `agent_runs` table for all coding
   agents; `provider` is a column, not a separate table.
4. `0004_init_evidence.sql` — validated manifest rows bound to repository/PR/
   head SHA/policy version; `evidence_identity` (SHA-256) is globally unique;
   `(repo, head_sha, idempotency_key)` is unique when the key is present.
5. `0005_init_audit_events.sql` — append-only audit log keyed by task/job.

**Claim SQL** (`CLAIM_TASK_SQL` / `CLAIM_JOB_SQL`): a single atomic
`UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)` that
transitions a pending row to `running`, increments `attempts`, sets
`locked_at`/`locked_by`, and clears `available_at`. The worker id is passed as
`$1` — never string-interpolated.

**Retry/backoff** (`computeBackoffMs`, `computeNextAvailableAt`, `shouldRetry`):
exponential `baseMs * 2^(attempt-1)` capped at `maxMs`, with optional
deterministic full-jitter via a seedable `jitterSeed`. Bounded by
`maxAttempts` (default 5).

**Stale-lock recovery** (`RECOVER_STALE_*_SQL`, `computeStaleLockCutoff`):
re-queues rows stuck in `running` whose `locked_at` is older than a
caller-computed cutoff, clears the lock, and appends an observable note to
`last_error`.

**Idempotency**: `tasks.external_id` is `UNIQUE`; `evidence.evidence_identity`
is `UNIQUE`; `(repo, head_sha, idempotency_key)` is `UNIQUE` when present;
`(provider, external_run_id)` is `UNIQUE` for `agent_runs`. Claiming is NOT
idempotent (each claim increments `attempts`); retries go through stale-lock
recovery, never through re-claiming.

## Adapter contracts

`packages/adapters` ships adapter contracts; all external interaction is via
injected interfaces so tests stay pure.

**GitHub webhook verification** (`verifyGitHubWebhookSignature`): HMAC-SHA256
over the raw payload bytes against the `X-Hub-Signature-256` header. The header
format is strict (`sha256=<64 lowercase hex>`); comparison uses
`crypto.timingSafeEqual` (constant-time). The secret is passed as raw bytes and
is never logged or surfaced.

**Delivery-id dedupe** (`createDeliveryDedupe`): bounded in-memory dedupe for
`X-GitHub-Delivery` with FIFO eviction; shaped so a DB-backed implementation
can replace it without changing call sites.

**CodeRabbit normalization** (`normalizeCodeRabbitFindings`): normalizes an
unknown, untrusted payload into the contracts `CodeRabbitFindings` shape,
preserving ONLY `id`, `severity`, and `resolved`. Findings carrying untrusted
instruction-like fields (`instructions`, `commands`, `prompts`, `tools`,
`actions`, `exec`, `shell`, `run`) are rejected outright. Malformed findings
fail-closed the whole batch.

**Generic `CodingAgentAdapter`**: provider-agnostic interface with
`capabilities()`, `createRun()`, `getRun()`, `sendFeedback()`, and
`cancelRun()`, plus shared `AgentRun` types. A stable `AdapterError` with
`AdapterErrorCode` covers `INVALID_INPUT`, `BUDGET_EXCEEDS_MAX`,
`STRUCTURED_OUTPUT_REQUIRED`, `STRUCTURED_OUTPUT_INVALID`, `RUN_NOT_FOUND`,
`TRANSPORT_ERROR`, and `PROVIDER_ERROR`.

**`DevinAdapter`** (`createDevinAdapter`): normalizes the Devin API to/from the
shared contract via an injected `DevinTransport`. Enforced posture:

- Explicit **normal** default mode (`DEVIN_DEFAULT_MODE = 'normal'`); fast mode
  is never auto-selected regardless of risk.
- **Bounded budget**: `maxBudgetMs` (default 30m) enforced fail-closed; a
  caller exceeding it gets `BUDGET_EXCEEDS_MAX`, never silent clamping.
- **Structured output required** by default; `outputSchema` is required when
  `structuredOutputRequired` is true; a response lacking a conforming
  `structuredResult` is rejected with `STRUCTURED_OUTPUT_INVALID`.
- **Risk-based model selection** (`selectDevinModel`): `glm-5-2` is the
  default; `swe-1-7` is selected ONLY for `HIGH`/`CRITICAL` risk.
- Transport errors map to `AdapterError(TRANSPORT_ERROR)`; an `AdapterError`
  thrown by the transport is preserved as-is.

## Policy gate CLI

`packages/gate` ships `hermes-policy-gate`: a local, deterministic aggregate
check around the pure contracts and policy evaluator. It reads a JSON evidence
manifest from disk, evaluates it, and emits stable machine-readable JSON —
no GitHub SDK, HTTP, database, agent calls, credentials, or network.

```bash
hermes-policy-gate \
  --manifest evidence.json \
  --head-sha 0123456789abcdef0123456789abcdef01234567 \
  --policy-version 0.1.0 \
  [--output result.json]
```

Arguments are strict: `--manifest`, `--head-sha` (40 lowercase hex), and
`--policy-version` (semver) are required; `--output` is optional. Unknown
flags, positional arguments, missing flags, and bad value formats are rejected.
The manifest path must be a readable regular file; the output path must not be
a directory. A syntactically invalid JSON file is an operational error, while a
structurally malformed manifest is an evaluator failure.

The emitted JSON contains only `decision`, `reasonCode`, `policyVersion`,
`evidenceIdentity` (when available), and `detail` — never the full manifest or
source content. Keys are emitted in a fixed order and serialization is stable
across repeated runs.

Exit codes:

- `0` — policy PASS
- `1` — policy failure or invalid evidence (evaluator returned `fail`)
- `2` — usage or operational error (bad args, unreadable file, invalid JSON, ...)

> **CX-01 contract (locked by tests):** `final_risk.py` exits `0` when the risk
> decision is present and valid in its JSON output — exit `0` means CONTRACT
> SATISFIED, not "risk is low". Never map risk level to exit code.

## Runtime — Ops DB

```bash
docker compose up -d        # PostgreSQL 16 on 127.0.0.1:55432, network hermes-ops
pnpm install --frozen-lockfile
pnpm build
pnpm db:migrate             # applies ordered migrations (needs DATABASE_URL)
pnpm test                   # unit tests
pnpm test:integration       # integration tests against the live DB
pnpm e2e                    # end-to-end smoke (e2e/smoke.mjs)
```

Environment (see `.env.example`): `DATABASE_URL` (default
`postgres://hermes:***@127.0.0.1:55432/hermes_ops`), optional `GMAIL_APP_PASSWORD`
for notifications, optional `GH_TOKEN` for PR workflows. The local `.env` is
gitignored; runtime artifacts (`circuit-breaker.json`, `devin-dispatch-log.json`,
`.hermes/current-run`) are tracked as pipeline evidence.

## Governance skills

`skills/software-development/` ships the orchestrator skills exercised by CI
(job `skills-python-tests`):

- `project-agent-bootstrapper` — repo verification, provenance, permission
  model, minimal evidence, route selection; functional test suite.
- `project-review-orchestrator` — Standard/High/Critical review workflow,
  approval flow (Ops DB-backed), integration + canonical e2e pipeline tests,
  redaction gate tests (secret egress must be blocked).
- `open-design-orchestrator` — full Open Design loop with real policy gate
  enforcement (fail closed on non-PASS).
- `canonical-project-execution` — the 39-rule procedure + 7 templates
  (bootstrap, task-dag, state-machine, contracts, report) that encodes this
  README's authority model as a Hermes skill.

## CI

`.github/workflows/ci.yml` runs two jobs on every push to `main` and PR:

1. **build-and-test** — pnpm install (frozen), lint, build, unit tests,
   integration tests against a Postgres 16 service container.
2. **skills-python-tests** — builds the gate binary first (CI checkouts have no
   `dist/`), then runs every Python governance test suite: offline orchestrator
   unit tests, Ops DB tests, canonical e2e pipeline, and the redaction gate.

## Usage

```bash
pnpm install --frozen-lockfile
pnpm build            # tsc -b root builds all packages
pnpm lint             # tsc -b --pretty false
pnpm test             # all unit + integration tests
pnpm --filter ruleset test

# Preview the ruleset payload (dry-run)
hermes-ruleset apply --owner <owner> --repo <repo> --dry-run

# Apply or update the Hermes ruleset idempotently
hermes-ruleset apply --owner <owner> --repo <repo> --token $GITHUB_TOKEN

# Post a hermes-policy-gate commit status to a head SHA
hermes-ruleset status --owner <owner> --repo <repo> --sha <40-hex> --state <success|failure|error|pending>

# Evaluate a local evidence manifest
hermes-policy-gate --manifest evidence.json --head-sha <40-hex> --policy-version 0.1.0
```

`.github/workflows/gate.yml` triggers when `CI` completes, builds an
EvidenceManifest v1 bound to the PR head SHA, runs `hermes-policy-gate`, and
posts the resulting `hermes-policy-gate` commit status.

## Status

- ✅ EvidenceManifest v1 contracts + policy evaluator
- ✅ PostgreSQL migrations, `SKIP LOCKED` queue, retry/backoff, stale-lock recovery, audit
- ✅ GitHub webhook verification, CodeRabbit normalization, `CodingAgentAdapter`, `DevinAdapter`
- ✅ `hermes-policy-gate` CLI (exit 0/1/2 contract)
- ✅ Canonical pipeline proven end-to-end: Evidence → Devin → OpenCode → Codex review → PR → CI → gate PASS → merge (first green run recorded in git history)
- ✅ Governance skills + 2-job CI, redaction gate
- ✅ GitHub ruleset integration and head-SHA evidence binding
- 🔜 Optional AgentMemory enrichment for verified lessons
