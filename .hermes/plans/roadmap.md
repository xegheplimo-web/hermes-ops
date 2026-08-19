# Hermes Ops Plan

## Phase 0 — contracts and policy (done)

- Evidence manifest v1 bound to repository, PR, head SHA, and policy version.
- Relative artifact references plus content hashes; no secrets or absolute paths.
- Deterministic policy evaluator with reason codes and fail-closed behavior.
- Unit tests for malformed, stale, mismatched, duplicate, and valid evidence.

## Phase 1 — control plane DB and queue primitives (done)

- `packages/db` package, no DB driver required for tests, no live DB assumption.
- Ordered SQL migrations (dependency order):
  1. `0001_init_tasks.sql` — tasks, idempotent by `external_id`, queue fields.
  2. `0002_init_jobs.sql` — jobs referencing tasks, same queue fields.
  3. `0003_init_agent_runs.sql` — one generic `agent_runs` table; `provider` is
     a column, not a separate table. No provider-specific session tables.
  4. `0004_init_evidence.sql` — evidence bound to repository/PR/head SHA and
     policy version; `evidence_identity` (SHA-256) globally unique;
     `(repo, head_sha, idempotency_key)` unique when present.
  5. `0005_init_audit_events.sql` — append-only audit log keyed by task/job.
- `pgcrypto` not enabled (documented): BIGSERIAL ids, SHA-256 from TS,
  `gen_random_uuid()` available in PG >= 13 core if needed later.
- Typed row/status definitions: `TaskRow`, `JobRow`, `AgentRunRow`,
  `EvidenceRow`, `AuditEventRow`; `QueueStatus`, `AgentRunStatus`;
  `QUEUE_TRANSITIONS` and `isValidQueueTransition`.
- Safe single-worker claim SQL: `CLAIM_TASK_SQL` / `CLAIM_JOB_SQL` using
  `FOR UPDATE SKIP LOCKED`, transition to `running`, `attempts + 1`,
  `locked_at`, `locked_by = $1`, `available_at = NULL`. Worker id passed as a
  positional parameter — never interpolated.
- Retry/backoff pure helpers: `computeBackoffMs` (exponential, capped, optional
  deterministic jitter), `computeNextAvailableAt` (deterministic timestamps),
  `shouldRetry` (bounded by `maxAttempts`). `REQUEUE_OR_FAIL_*_SQL` for
  re-queue vs terminal failure.
- Stale-lock recovery: `computeStaleLockCutoff` (deterministic) and
  `RECOVER_STALE_*_SQL` re-queuing rows with `locked_at < $1`.
- Idempotency guidance documented in `IDEMPOTENCY_GUIDANCE`.
- Tests: migration ordering/content, claim SQL contains `SKIP LOCKED` and no
  unsafe interpolation, retry/backoff bounds, stale lock recovery, generic
  `agent_runs` (no provider-specific tables), queue transitions.

## Phase 2 — adapters (done)

- `packages/adapters` package, depends on `@hermes-ops/contracts`. No HTTP
  server, no network calls, no credentials, no real GitHub/CodeRabbit/Devin
  API calls. Transports are injected; tests use in-memory doubles.
- GitHub webhook HMAC-SHA256 verification (`verifyGitHubWebhookSignature`):
  strict `sha256=<64 lowercase hex>` header format, raw-payload input,
  constant-time comparison via `crypto.timingSafeEqual`, secrets never logged
  or surfaced in results/errors.
- GitHub delivery-id dedupe (`createDeliveryDedupe`): bounded in-memory FIFO
  eviction at `maxSize`; `DeliveryDedupe` interface shaped for later DB-backed
  replacement (UNIQUE constraint + TTL) without changing call sites.
- CodeRabbit normalization (`normalizeCodeRabbitFindings`): unknown input into
  contracts `CodeRabbitFindings` shape, preserving ONLY id/severity/resolved;
  rejects malformed findings (fail-closed the batch) and rejects untrusted
  instruction-like fields (`instructions`, `commands`, `prompts`, `tools`,
  `actions`, `exec`, `shell`, `run`) outright rather than silently stripping.
- Generic `CodingAgentAdapter` interface: `capabilities`, `createRun`,
  `getRun`, `sendFeedback`, `cancelRun`; shared `AgentRun` types
  (`AgentRunStatus`, `RiskLevel`, `StructuredOutput`); stable `AdapterError`
  with `AdapterErrorCode` (`INVALID_INPUT`, `BUDGET_EXCEEDS_MAX`,
  `STRUCTURED_OUTPUT_REQUIRED`, `STRUCTURED_OUTPUT_INVALID`, `RUN_NOT_FOUND`,
  `TRANSPORT_ERROR`, `PROVIDER_ERROR`).
- `DevinAdapter` (`createDevinAdapter`) with injected `DevinTransport`:
  - explicit **normal** default mode; fast mode never auto-selected.
  - bounded budget (`maxBudgetMs`, default 30m) enforced fail-closed
    (`BUDGET_EXCEEDS_MAX`), no silent clamping.
  - structured output required by default; `outputSchema` required when
    `structuredOutputRequired`; provider response lacking a conforming
    `structuredResult` rejected with `STRUCTURED_OUTPUT_INVALID`.
  - risk-based model selection (`selectDevinModel`): `glm-5-2` default,
    `swe-1-7` only for `HIGH`/`CRITICAL`; `LOW`/`MEDIUM`/unspecified always
    use the default.
  - transport errors mapped to `AdapterError(TRANSPORT_ERROR)`; `AdapterError`
    thrown by transport preserved as-is.
- Tests: valid/invalid signatures, replay/dedupe bounds, normalization
  (valid/malformed/untrusted fields), adapter payload defaults, risk routing,
  error mapping. No transport invoked in tests except in-memory doubles.

## Phase 3 — enforcement

- OPS-004: `packages/gate` ships the `hermes-policy-gate` CLI — a local,
  deterministic aggregate check around the pure contracts and policy evaluator.
  No GitHub SDK, HTTP, database, agent calls, credentials, or network.
- Strict argv parsing: required `--manifest <file.json>`, `--head-sha` (40
  lowercase hex), `--policy-version` (semver); optional `--output <file.json>`.
  Unknown flags, positional args, missing flags, and bad value formats are
  rejected. Manifest path must be a readable regular file; output path must not
  be a directory. Invalid JSON is an operational error; a structurally malformed
  manifest is an evaluator failure.
- Emits stable machine-readable JSON with `decision`, `reasonCode`,
  `policyVersion`, `evidenceIdentity` (when available), and `detail` — never the
  full manifest or source content. Fixed key order, deterministic
  serialization.
- Exit codes: `0` PASS, `1` policy failure / invalid evidence, `2` usage /
  operational error. On errors only a short safe stderr message is printed;
  secrets and raw manifest JSON are never surfaced.
- Tests are subprocess-based (`node dist/bin.js` after a `beforeAll` workspace
  build): pass, stale / head-SHA mismatch / CI failure / unresolved critical
  finding / policy-version mismatch, structurally malformed manifest, invalid
  JSON, missing/unknown/bad args, missing file, directory paths, `--output`
  file, stable byte-identical output, fixed key order, and exit-code summary.
- GitHub ruleset integration and head-SHA evidence binding (next).
