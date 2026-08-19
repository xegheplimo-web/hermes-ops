# Hermes Ops

Lean Hermes control plane for project workflow, evidence, policy, and coding-agent orchestration.

## Boundary

`hermes-ops` owns:

- evidence manifest contracts
- deterministic policy decisions
- PostgreSQL Ops DB and queue primitives
- GitHub evidence/enforcement adapters (next phase)
- generic coding-agent adapters, Devin first (next phase)
- audit and reconciliation (next phase)

It does not own source analysis or the Understand Anything graph. That product remains in the sibling repository:

```text
G:/Agent-Tools/Understand-Anything
```

## Current phase

Phase 0 implements the versioned evidence contract and deterministic policy
evaluator without external services or credentials.

Phase 1 adds the PostgreSQL schema and queue primitives in `packages/db`. It
introduces ordered SQL migrations, typed row/status definitions, a safe
single-worker claim query (`FOR UPDATE SKIP LOCKED`), retry/backoff and
stale-lock recovery pure helpers, and idempotency guidance. No DB driver,
GitHub SDK, CodeRabbit API, Devin API, HTTP server, credentials, or network
calls are involved; tests do not require a live database.

Phase 2 adds adapter contracts in `packages/adapters`: GitHub webhook
verification, CodeRabbit finding normalization, a generic `CodingAgentAdapter`
interface, and a `DevinAdapter`. All code is pure and testable — no HTTP
server, no network calls, no credentials, no real GitHub/CodeRabbit/Devin API
calls. Transports are injected; tests use in-memory doubles.

### Layout

```text
packages/contracts  # EvidenceManifest v1 types, runtime validation, evidence identity
packages/policy     # deterministic fail-closed policy evaluator
packages/db         # PostgreSQL migrations, queue claim/recovery SQL, retry/backoff helpers
packages/adapters   # GitHub webhook verification, CodeRabbit normalization, coding-agent adapters
packages/gate       # `hermes-policy-gate` local deterministic CLI around the policy evaluator
```

### EvidenceManifest v1

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

### Policy evaluator

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
evaluator stays stateless and deterministic in Phase 0; persistence lands later.

### DB schema and queue primitives

`packages/db` ships the control-plane schema and queue primitives without a DB
driver. It is intentionally service-free: no HTTP server, no GitHub/Devin/
CodeRabbit adapters, no credentials, no network calls.

**Migrations** (`packages/db/src/migrations/`, ordered by dependency):

1. `0001_init_tasks.sql` — top-level work unit, idempotent by `external_id`,
   bound to repository/PR/head SHA/policy version, with queue fields
   (`status`, `attempts`, `max_attempts`, `available_at`, `locked_at`,
   `locked_by`, `last_error`).
2. `0002_init_jobs.sql` — per-task executable unit, same queue fields.
3. `0003_init_agent_runs.sql` — one generic `agent_runs` table for all coding
   agents; `provider` is a column, not a separate table. No provider-specific
   session tables.
4. `0004_init_evidence.sql` — validated manifest rows bound to repository/PR/
   head SHA/policy version; `evidence_identity` (SHA-256) is globally unique;
   `(repo, head_sha, idempotency_key)` is unique when the key is present.
5. `0005_init_audit_events.sql` — append-only audit log keyed by task/job.

`pgcrypto` is not enabled: identifiers are `BIGSERIAL`, evidence identities are
SHA-256 computed in TypeScript, and PostgreSQL >= 13 exposes
`gen_random_uuid()` from core if UUIDs are ever needed.

**Claim SQL** (`CLAIM_TASK_SQL` / `CLAIM_JOB_SQL`): a single atomic
`UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)` that
transitions a pending row to `running`, increments `attempts`, sets
`locked_at`/`locked_by`, and clears `available_at`. The worker id is passed as
`$1` — never string-interpolated.

**Retry/backoff** (`computeBackoffMs`, `computeNextAvailableAt`, `shouldRetry`):
exponential `baseMs * 2^(attempt-1)` capped at `maxMs`, with optional
deterministic full-jitter via a seedable `jitterSeed`. Bounded by
`maxAttempts` (default 5). Timestamps are computed in TypeScript from an
injectable `now` so behavior is deterministic and testable.

**Stale-lock recovery** (`RECOVER_STALE_*_SQL`, `computeStaleLockCutoff`):
re-queues rows stuck in `running` whose `locked_at` is older than a
caller-computed cutoff (passed as `$1`), clears the lock, and appends an
observable note to `last_error`.

**Idempotency**: `tasks.external_id` is `UNIQUE`; `evidence.evidence_identity`
is `UNIQUE`; `(repo, head_sha, idempotency_key)` is `UNIQUE` when present;
`(provider, external_run_id)` is `UNIQUE` for `agent_runs`. Claiming is NOT
idempotent (each claim increments `attempts`); retries go through stale-lock
recovery, never through re-claiming.

### Adapter contracts (Phase 2)

`packages/adapters` ships the adapter contracts without any HTTP server,
network calls, credentials, or real GitHub/CodeRabbit/Devin API clients. All
external interaction is via injected interfaces so tests stay pure.

**GitHub webhook verification** (`verifyGitHubWebhookSignature`): HMAC-SHA256
over the raw payload bytes against the `X-Hub-Signature-256` header. The
header format is strict (`sha256=<64 lowercase hex>`); any deviation is
rejected. Comparison uses `crypto.timingSafeEqual` (constant-time). The secret
is passed in as raw bytes and is never logged or surfaced in any result or
error.

**Delivery-id dedupe** (`createDeliveryDedupe`): a bounded in-memory dedupe for
the `X-GitHub-Delivery` id, with FIFO eviction at `maxSize`. The
`DeliveryDedupe` interface is shaped so a DB-backed implementation (e.g. a
`webhook_deliveries` table with a UNIQUE constraint + TTL) can replace it later
without changing call sites.

**CodeRabbit normalization** (`normalizeCodeRabbitFindings`): normalizes an
unknown, untrusted payload into the contracts `CodeRabbitFindings` shape,
preserving ONLY `id`, `severity`, and `resolved`. Findings carrying
untrusted instruction-like fields (`instructions`, `commands`, `prompts`,
`tools`, `actions`, `exec`, `shell`, `run`) are rejected outright rather than
silently stripped. Malformed findings fail-closed the whole batch.

**Generic `CodingAgentAdapter`**: a provider-agnostic interface with
`capabilities()`, `createRun()`, `getRun()`, `sendFeedback()`, and
`cancelRun()`, plus shared `AgentRun` types (`AgentRunStatus`, `RiskLevel`,
`StructuredOutput`). Adapters inject their own transport and normalize to/from
`AgentRun`. A stable `AdapterError` with `AdapterErrorCode` covers
`INVALID_INPUT`, `BUDGET_EXCEEDS_MAX`, `STRUCTURED_OUTPUT_REQUIRED`,
`STRUCTURED_OUTPUT_INVALID`, `RUN_NOT_FOUND`, `TRANSPORT_ERROR`, and
`PROVIDER_ERROR`.

**`DevinAdapter`** (`createDevinAdapter`): normalizes the Devin API to/from the
shared contract via an injected `DevinTransport`. Enforced posture:
- Explicit **normal** default mode (`DEVIN_DEFAULT_MODE = 'normal'`); fast mode
  is never auto-selected regardless of risk.
- **Bounded budget**: `maxBudgetMs` (default 30m) is enforced fail-closed; a
  caller exceeding it gets `BUDGET_EXCEEDS_MAX`, never silent clamping.
- **Structured output required** by default; `outputSchema` is required when
  `structuredOutputRequired` is true, and a provider response lacking a
  conforming `structuredResult` is rejected with `STRUCTURED_OUTPUT_INVALID`.
- **Risk-based model selection** (`selectDevinModel`): `glm-5-2` is the
  default; `swe-1-7` is selected ONLY for `HIGH`/`CRITICAL` risk. `LOW`/
  `MEDIUM` and unspecified risk always use the default.
- Transport errors are mapped to `AdapterError(TRANSPORT_ERROR)`; an
  `AdapterError` thrown by the transport is preserved as-is.

### Policy gate CLI (OPS-004)

`packages/gate` ships the `hermes-policy-gate` CLI: a local, deterministic
aggregate check around the pure contracts and policy evaluator. No GitHub SDK,
HTTP, database, agent calls, credentials, or network are involved — it reads a
JSON evidence manifest from disk, evaluates it, and emits stable
machine-readable JSON.

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
source content. Keys are emitted in a fixed order and the serialization is
stable across repeated runs.

Exit codes:

- `0` — policy PASS
- `1` — policy failure or invalid evidence (evaluator returned `fail`)
- `2` — usage or operational error (bad args, unreadable file, invalid JSON, ...)

On usage/operational errors only a short, safe message is written to stderr;
secrets and raw manifest JSON are never printed. On pass/fail the result JSON is
written to stdout (or the `--output` file) and the exit code signals the
outcome.

### Commands

```bash
pnpm install --frozen-lockfile
pnpm test
pnpm build
pnpm lint
```

## Planned phases

1. Contracts and policy evaluator (done)
2. PostgreSQL migrations, `SKIP LOCKED` queue, audit records (done)
3. GitHub webhook verification, CodeRabbit normalization, coding-agent adapters (done)
4. Aggregate `hermes/policy-gate` workflow (done — local deterministic CLI)
5. GitHub ruleset integration and head-SHA evidence binding
6. Optional Hermes skill and AgentMemory enrichment
