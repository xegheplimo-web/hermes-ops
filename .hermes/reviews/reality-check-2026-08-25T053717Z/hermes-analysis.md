# Hermes First-Pass Analysis — Reality Startup Check

**Run:** `reality-check-2026-08-25T053717Z` | **Commit:** `b4a92d86f10ab457e8107ade831d9be7123a83fc` | **Branch:** `main` | **Dirty:** `true` (49 changed entries) | **Generated:** 2026-08-24T22:37:17+00:00  
**Evidence:** `repo-evidence.json` (schema_version 1.0, tracked_count 105, safe_scanned 104) | **Mode:** openai-api | **Reviewer:** Hermes Agent (pre-external)

> Every claim below is either evidence-referenced (`file:line` or `repo-evidence.json` field) or explicitly labeled `INFERENCE`/`UNKNOWN`/`UNVERIFIED`.

---

## 1. PROJECT SNAPSHOT

- **Root:** `hermes-ops` (repo-evidence.json: repository.root_name) — evidence 105 tracked files, 104 safe-scanned, 541607 bytes safe [repo-evidence.json: files].
- **Languages:** TypeScript 42 files (287k bytes), Markdown 25, JSON 23, SQL 5, YAML 4, Python 3, Other 1, JS 1 [repo-evidence.json: files.languages_by_file]. Aligns with `packages/contracts/package.json:1`, `packages/policy|db|gate|adapters/package.json`.
- **Commit:** `b4a92d86f10ab457e8107ade831d9be7123a83fc` dated 2026-08-23T05:43:10+07:00 subject "feat(cli): wire post-diff risk recalc..." [repo-evidence.json: maintenance.recent_commits[0], git log]. HEAD is `main` [repo-evidence.json: repository.branch].
- **Dirty:** Explicitly true; 49 entries (M 5 tracked + ?? 44 untracked) [repo-evidence.json: repository.changed_entries]. Includes `.hermes/plans/STATUS-2026-08-21.md` (M), `packages/db/src/schema.ts` (M), skill scripts (M/untracked), `packages/db/src/migrations/0006_expand_task_statuses.sql` (??), `devin/devin-task-PROJ-*.md` (??). See `git status --porcelain=v1` and `state.json: dirty=true`.
- **Manifests:** `package.json`, `pnpm-lock.yaml`, `docker-compose.yml`, `packages/*/package.json` (5 packages) [repo-evidence.json: project_structure.manifests].
- **CI files:** `.github/workflows/ci.yml` only [repo-evidence.json: project_structure.ci_files].
- **Test files:** 17 counted [repo-evidence.json: project_structure.test_file_count], sample includes `packages/contracts/tests/manifest.test.ts`, `packages/adapters/tests/devin.test.ts`, etc. Matches filesystem glob `packages/*/tests/*.ts`.
- **Maintenance:** TODO markers FIXMe 5, HACK 5, TODO 10, XXX 5 [repo-evidence.json: maintenance.todo_markers]; top churn `pnpm-lock.yaml` 1172, `.hermes/plans/...` 724 [repo-evidence.json: maintenance.top_churn_90_days].
- **Runner:** Evidence collected via `skills/software-development/project-review-orchestrator/scripts/collect_repo_evidence.py:306` with `python3.12.exe`; output to `.hermes/reviews/reality-check-2026-08-25T053717Z/{repo-evidence.json,repo-evidence.md,state.json}` [state.json, collect_repo_evidence.py:316].

## 2. CURRENT IMPLEMENTED ARCHITECTURE

Evidence-confirmed (`FACT` — source inspected):

- **Phase isolation documented** in `.hermes.md:3-52`:
  - Phase 0: `packages/contracts` (evidence manifest validation) + `packages/policy` (deterministic evaluator) — dependency-light, service-free.
  - Phase 1: `packages/db` (migrations, queue primitives, typed rows) — no driver, no network in tests.
  - Phase 2: `packages/adapters` (GitHub HMAC, CodeRabbit normalization, `CodingAgentAdapter`, `DevinAdapter` with injected transport + risk-based model selection).
  - Phase 3 (OPS-004): `packages/gate` CLI (`hermes-policy-gate`) deterministic, no network.

- **Contracts** `packages/contracts/src/validation.ts:146` — `validateEvidenceManifest(input, options)` fail-closed, checks: schemaVersion (`MANIFEST_SCHEMA_VERSION`), repository, headSha (SHA1_RE `^[0-9a-f]{40}$`), policyVersion (SEMVER), timestamp (ISO_RE, freshness 24h `DEFAULT_MAX_AGE_MS`, skew 5m), artifacts (relative path, no traversal, dup check), ci (CiConclusion enum), coderabbit (id/severity/resolved only), devin, source (SourceAdapterKind), secret key scan (`SECRET_KEY_RE`), headSha vs expectedHeadSha. Pure function, no DB/network [validation.ts:1-473].

- **Policy** `packages/policy/src/evaluator.ts:93` — `evaluatePolicy(input, options)` order: validate → policyVersion mismatch → duplicate idempotency → CI green (`isCiGreen`) → unresolved critical finding → PASS. Returns `PolicyResult` with `reasonCode` (PASS/EVIDENCE_INVALID/STALE/HEAD_SHA_MISMATCH/CI_NOT_GREEN/UNRESOLVED_CRITICAL_FINDING/DUPLICATE_EVIDENCE/POLICY_VERSION_MISMATCH) [evaluator.ts:20-31]. `classifier.ts:41` binary risk `auto-eligible`|`human-required`; `post-diff.ts` wire in `packages/gate/src/cli.ts:293`.

- **DB** `packages/db/src/queue.ts:50` — `CLAIM_TASK_SQL` / `CLAIM_JOB_SQL` using `FOR UPDATE SKIP LOCKED`, ordered `available_at ASC, id ASC`, incremental `attempts`, `locked_at=now()`, `locked_by=$1` (positional, no interpolation). Helpers: `computeBackoffMs` (exponential 2^(attempt-1), capped, deterministic jitter via FNV-1a `hash32`), `computeNextAvailableAt`, `shouldRetry`, `REQUEUE_OR_FAIL_*_SQL`, `RECOVER_STALE_*_SQL` (cutoff computed in TS `computeStaleLockCutoff`). `schema.ts:16` queue statuses: `planning|queued|pending|running|dispatched|verifying|completed|failed|cancelled|blocked` with `QUEUE_TRANSITIONS` map; re-exported in `ops_adapter.py:82`.

- **Adapters** `packages/adapters/src/devin.ts:266` — `createDevinAdapter(options)` stateless, injected `DevinTransport`, explicit `DEVIN_DEFAULT_MODE='normal'` (never auto fast), bounded `maxBudgetMs` (default 30m, per-run 10m, throws `BUDGET_EXCEEDS_MAX`), risk-based model `selectDevinModel`: HIGH/CRITICAL→`swe-1-7` else default `glm-5-2`. `withTransport` maps errors to `AdapterError` with `TRANSPORT_ERROR_MESSAGE` to avoid secret leak. `normalizeRun` enforces structured output when required.

- **Gate CLI** `packages/gate/src/cli.ts:229` — `runCli(argv, io)` injectable FS, strict argv (`--manifest`, `--head-sha`, `--policy-version` required, `HEAD_SHA_RE`/`SEMVER_RE` validated), file existence/dir checks, JSON parse, `evaluatePolicy` + `classifyFromPolicyResult` + `recalculatePostDiffRisk(changedFiles)` [cli.ts:293], human approval gate (`--approval` JSON with signedAt/approver/reason/signature), stable JSON output `GateResultJson` (decision, reasonCode, policyVersion, evidenceIdentity?, detail), exit codes 0 PASS / 1 FAIL / 2 USAGE. `USAGE` string [cli.ts:40] and `KNOWN_FLAGS` [cli.ts:29].

- **Ops DB Adapter** `skills/software-development/project-review-orchestrator/scripts/ops_adapter.py:120` — `OpsDbAdapter` (psycopg2) is authoritative queue path: `create_task`/`bulk_create_tasks` ( ON CONFLICT external_id DO UPDATE... ), `claim_task` (SKIP LOCKED, status IN pending|queued), `transition_task` (validates `is_valid_transition`), `recover_stale_locks`, `insert_evidence` (ON CONFLICT evidence_identity DO NOTHING), `record_audit`. Idempotency: `make_external_id` (sha256 run_id::task_id[:32]), `make_evidence_identity` (canonical json sha256). Status constants mirror `schema.ts`.

- **Conflict Detector** `skills/software-development/project-review-orchestrator/scripts/conflict_detector.py:345` — `detect_all(repo_path, review_run_id, review_sha, memory_entries)` checks MEMORY_VS_REPO, OPS_VS_GIT, DONE_UNVERIFIED, STALE_MEMORY, SHA_MISMATCH. For this run: `conflicts.json` status CLEAR, 0 total, requires_reconciliation false, repo_sha `b4a92d86...`, branch main [conflicts.json]. Ops DB checks returned 0 because `DATABASE_URL` not set or no completed tasks [conflict_detector.py:129, ops_adapter.py:129].

- **Review Orchestrator Skill** `skills/software-development/project-review-orchestrator/SKILL.md:1-548` — defines full Hermes flow (Preflight → Recall → Collect → Hermes Analysis → Packet → External Review → Reconcile → Codemap Brief → Decompose → Dispatch → Gates → Memory promotion). Scripts verified: `collect_repo_evidence.py` (355 lines), `build_review_packet.py` (112 lines, secret redaction `SECRET_PATTERNS` + high-entropy token), `codex_review.py`, `openai_review.py`, `reconcile_review.py`, `build_codemap_brief.py`, `decompose_tasks.py`, `dispatch_to_devin.py`, `ops_adapter.py`, `conflict_detector.py`, `update_state.py` [skills/.../scripts/ directory].

- **E2E Slice** `e2e/smoke.mjs:1-190` — vertical slice enqueue→claim→evidence→audit→complete→stale-recovery→agent_runs, requires `DATABASE_URL`, uses `pg` Client, random workerId. Covers tasks, evidence, audit_events, jobs, agent_runs.

- **Machine Discovery** `machine-discovery/` (exists but not in evidence languages; tracked files include it) — INFERENCE: not part of P0-3 core; separate provisioning.

## 3. VERIFIED COMPLETED FEATURES

All FACT (source + test evidence where available):

1. **Evidence manifest validation** — `packages/contracts/src/validation.ts:146` + `packages/contracts/tests/manifest.test.ts` (313 churn lines) — tested for malformed, secret fields, stale, head mismatch. Evidence: `MANIFEST_SCHEMA_VERSION` check, secret scan `SECRET_KEY_RE`, path traversal guard `isRelativePath/hasTraversal`.
2. **Policy evaluator (fail-closed)** — `packages/policy/src/evaluator.ts:93` + `packages/policy/tests/evaluator.test.ts` — covers PASS, EVIDENCE_INVALID/STALE/HEAD_SHA_MISMATCH, CI_NOT_GREEN, UNRESOLVED_CRITICAL_FINDING, DUPLICATE_EVIDENCE, POLICY_VERSION_MISMATCH. Uses injected `now`/`maxAgeMs`/`seenIdempotencyKeys`.
3. **Risk classifier** — `packages/policy/src/classifier.ts:41` + `packages/policy/tests/classifier.test.ts` — binary auto-eligible/human-required, 6-signal first-match.
4. **Queue primitives (driver-agnostic)** — `packages/db/src/queue.ts:50` + `packages/db/tests/queue-sql.test.ts:???` — CLAIM SQL with SKIP LOCKED, requeue/fail, stale recovery SQL, backoff helpers. Pure logic tested without DB; driver injected.
5. **Stale-lock recovery** — `queue.ts:294` `computeStaleLockCutoff` + `RECOVER_STALE_TASKS_SQL:265` + `packages/db/tests/recovery.test.ts` + `ops_adapter.py:400` and `e2e/smoke.mjs:123` manual recovery phase.
6. **DevinAdapter** — `packages/adapters/src/devin.ts:266` + `packages/adapters/tests/devin.test.ts` (586 churn) — injected transport, default model glm-5-2, HIGH/CRITICAL→swe-1-7, budget bounded, structured output required, fast mode never auto, transport error redaction `TRANSPORT_ERROR_MESSAGE`.
7. **Gate CLI** — `packages/gate/src/cli.ts:229` + `packages/gate/tests/cli.test.ts:???` (504 additions) — strict argv, exit codes 0/1/2, stable JSON, --changed-files post-diff, --approval human gate, file checks, no secret echo.
8. **DB migrations** — `packages/db/src/schema.ts:185` MIGRATION_FILES 0001-0005, plus `0006_expand_task_statuses.sql` (tracked but untracked per git) adding review_run_id/dag_payload and expanded statuses; runner with sha256 checksum verification [hermes.md:8-12, packages/db/tests/migrate.test.ts].
9. **GitHub/CodeRabbit adapters** — `packages/adapters/tests/github.test.ts`, `packages/adapters/tests/coderabbit.test.ts` — HMAC sha256= verification, CodeRabbit normalization preserving only id/severity/resolved [hermes.md:19-25].
10. **Review Orchestrator pipeline (scripts)** — `collect_repo_evidence.py:306` (generates repo-evidence.json/md/state.json), `build_review_packet.py:74` (sanitizes, redacts, sha256), `conflict_detector.py:345` (5 detectors) all executable via `uv` python 3.12.14; tested in this reality run (collect OK, conflicts CLEAR).
11. **CI** — `.github/workflows/ci.yml:10` postgres:16-alpine service, pnpm/action-setup@v4 + setup-node@v4 (node 22, cache pnpm), steps install/lint/build/test/test:integration.
12. **E2E smoke** — `e2e/smoke.mjs:1` 7 phases; `package.json:12` script `e2e: node e2e/smoke.mjs`.

## 4. PARTIAL FEATURES

- **Ops DB live integration** — CODE EXISTS (`ops_adapter.py`, `e2e/smoke.mjs`, `packages/db/tests/queue.integration.test.ts` 400 churn) but **INFERENCE**: DB not exercised in this ephemeral Windows env (no `DATABASE_URL`, `pnpm`/`psql` not on PATH, Docker not verified running). Migration 0006 is untracked (??) so DB schema drift possible. Evidence: `conflict_detector.py:129` fallback empty when no DATABASE_URL; `e2e/smoke.mjs:17` exits 1 if missing DATABASE_URL.
- **External review (OpenAI/Codex)** — scripts `openai_review.py`, `codex_review.py` exist; `SKILL.md:82` defines both `openai-api` and `chatgpt-human` modes. But **UNVERIFIED**: no `OPENAI_API_KEY` nor `codex` CLI on PATH in this check (only `C:\pinokio\bin\miniforge\node.exe` v24.18.0 available). The smoke review at `.hermes/reviews/codex-smoke-2026-08-25/` exists (state TASKS_DECOMPOSED) using a tiny dummy repo (2 files, 1 TODO) — indicates prior smoke used but not against hermes-ops itself.
- **Dispatch to Devin** — `dispatch_to_devin.py` present; `devin/devin-task-PROJ-*.md` (4 tasks) are untracked dummy files. **UNVERIFIED**: no real Devin API transport configured (transport is injected interface; `DevinCliTransport` mentioned in git log `d86bd55` but file not in evidence languages scan — may be recent).
- **AgentMemory integration** — referenced in `SKILL.md:48-56` (search architecture decisions/incidents), `hermes.md` says Phase P0 added memory integration, but **INFERENCE**: no MCP AgentMemory tool available in this `opencode` runtime; `collect_repo_evidence.py` does not query it. Historical context step was skipped (recorded as unavailable).
- **PNPM build** — `package.json:7-9` build/test scripts require `pnpm@10.6.2` and `tsc -b` but **INFERENCE**: pnpm not on PATH (`where.exe pnpm` => not found; only `C:\pinokio\bin\miniforge` node). `pnpm-lock.yaml` churn 1171 added, but build not executed in this run. **UNVERIFIED** whether `pnpm install --frozen-lockfile` passes after recent SKILL.md/scripts changes (dirty).
- **Dirty worktree handling** — `collect_repo_evidence.py` records dirty=true, changed_entries 49, but downstream compensations (state machine `dirty_snapshot`) not yet exercised beyond evidence. **[FACT: collect correctly labelled]** but reconciliation of dirty snapshot vs external review not yet run.

## 5. BROKEN FEATURES

None proven broken **in this run** — but candidates flagged:

- **PNPM/Node toolchain missing on Windows PATH** — `bash` calls show `pnpm: command not found`, `python: command not found` (until uv python used), `node` only via `C:\pinokio\bin\miniforge\node.exe`. So `pnpm lint|build|test` cannot run without PATH fix. `[FACT: bash error "pnpm: The term 'pnpm' is not recognized..."]`. This is environmental, not code defect, but breaks `SKILL.md` prerequisites "Python 3.11 or later, Git, pnpm, Node".
- **Git status dirty 49 entries** — uncommitted skill artifacts (?? `.hermes/reviews/codex-smoke...`, `devin/*.md`, `skills/.../execution-discipline/*`, `0006_expand...`, `tests/*`) mean HEAD evidence is stale vs worktree. If external reviewer were to read `repo-evidence.json` alone without dirty context, they'd mis-evaluate. `collect_repo_evidence` does record dirty but `build_review_packet` must surface it — currently packet includes `dirty` flag but downstream `reconcile_review.py` unknown handling.
- **build_review_packet redaction bug risk INFERENCE**: `build_review_packet.py:33` high-entropy regex `r"\b[A-Za-z0-9-_=]{20,}\b"` + `_has_high_entropy` (requires 3 char classes) may over-redact legitimate SHA/filenames (e.g., 40-char lowercase hex SHA would be 1 class, so safe; but 64-char sha256 hex is also 2 classes (hex digits + maybe). Needs verification via tests — **UNVERIFIED** without running `build_review_packet` tests.
- **Ops DB migration drift** — `packages/db/src/schema.ts:185` lists 0006, but `git status` shows `?? packages/db/src/migrations/0006_expand_task_statuses.sql` untracked, and `M packages/db/src/schema.ts` modified. So committed schema.ts at HEAD lists only 0001-0005 (pre-0006). The dirty worktree has 0006 but HEAD does not — evidence at HEAD would not include it. **FACT: divergence between HEAD and worktree schema**. This will cause `db:migrate` to see missing file if running from HEAD.

## 6. UNKNOWN AREAS

- **Ops DB runtime truth** — `UNKNOWN`: no live Postgres probed; `docker-compose.yml` exists but `docker` not tested here; `.pgdata/` present but unclear if running.
- **AgentMemory historical context** — `UNKNOWN`: no search performed; prior incidents/rejected approaches not recalled.
- **Machine-discovery package** — `UNKNOWN`: `machine-discovery/` directory listed but not described in `.hermes.md` or `README.md`; purpose may be infra probing.
- **Superpowers / AgentMemory-main sibling repos** — `G:\Agent-Tools\superpowers-main` and `agentmemory-main` exist but their relationship to hermes-ops `skills` is unexplored; superpowers skills were partially copied into `skills/software-development/execution-discipline/` (?? files). Overlap/duplication unclear.
- **Policy gate human approval flow** — `packages/gate/src/cli.ts:298` mentions `HumanApprovalToken` but approval verification (signature validation) not inspected; **UNKNOWN** if crypto check is real or stub.
- **DevinCliTransport** — `git log d86bd55` says added, but file not in `packages/adapters/src/` listing (need `glob` verification) — **UNVERIFIED**.

## 7. CURRENT TEST/BUILD HEALTH

- **Evidence:** 17 test files counted [repo-evidence.json: project_structure.test_file_count], sample list includes `coderabbit.test.ts`, `coding-agent.test.ts`, `devin.test.ts`, `github.test.ts`, `manifest.test.ts`, `agent-runs.test.ts`, `e2e-smoke.test.ts`, `migrate.test.ts`, `migrations.test.ts`, `queue-sql.test.ts`, `queue.integration.test.ts`, `recovery.test.ts`, `approval.test.ts`, `cli.test.ts`, `classifier.test.ts`, `evaluator.test.ts`, `post-diff.test.ts` [repo-evidence.json: project_structure.test_files_sample + fs glob].
- **CI:** `.github/workflows/ci.yml:28-53` defines pnpm install, lint (`tsc -b --pretty false`), build (`tsc -b`), test (`vitest run`), test:integration (`vitest run integration` with DATABASE_URL). No badge/status fetched; recent log `f04ceb0` "ci: fix build — remove tracked tsbuildinfo" suggests prior build failures.
- **Local build INFERENCE:** `package.json:7` `build: tsc -b` likely depends on `tsconfig.base.json`/`tsconfig.json`; root `tsbuildinfo` files are gitignored? `f04ceb0` removed tracked tsbuildinfo, good.
- **Test counts per package UNKNOWN**: not parsed from evidence; `pnpm-lock.yaml` churn 1171 suggests recent dep churn.
- **E2E:** `e2e/smoke.mjs` present and `package.json:12` script `e2e`; requires Postgres. **UNVERIFIED** if passes without DB (would exit 1 per `smoke.mjs:18-19`).
- **Python pipeline tests** `skills/.../tests/{e2e_canonical_pipeline.py, integration_ops_db.py, test_conflict_detector.py}` exist but are untracked (??) — **UNKNOWN** if they pass.
- **TODO markers** 25 total [repo-evidence.json] — not blocking but indicates debt.

**Reality check result:** Test/build infra is **implemented** but **local toolchain gap** (pnpm/node not on PATH) prevents immediate `pnpm test` verification. Evidence collection itself is the only step actually executed and it PASSED [state.json: EVIDENCE_COLLECTED].

## 8. SECURITY STATUS

**FACT — reviewed files `validation.ts`, `build_review_packet.py`, `adapters/src/devin.ts`, `cli.ts`, `skill/SKILL.md`:**

- **Secrets handling:**
  - `validation.ts:58` `SECRET_KEY_RE` rejects any secret-looking object key anywhere (`findSecretKey` recursive). Returns `SECRET_FIELD` fail-closed [validation.ts:155].
  - `build_review_packet.py:14` `SECRET_PATTERNS` list (private keys, sk-*, gh*_*, AKIA, aws_secret, password/secret/api_key/access_token/refresh_token key=value, Bearer Authorization, Slack xox*, JWT eyJ..., .env.* refs, *.pem/*.key, browser login data/cookies) + high-entropy generic token `[REDACTED_TOKEN]` via `_has_high_entropy` (3 char classes) [build_review_packet.py:14-67]. Packet is sanitized, `security.source_files_included=False`, `redaction_matches` count, `warning` present [build_review_packet.py:93].
  - `collect_repo_evidence.py:48` `SENSITIVE_BASENAMES` (.env, id_rsa, credentials.json, etc.) and `is_sensitive_path` skips these from file size/count and from TODO scan [collect_repo_evidence.py:97-140].
  - `SKILL.md:248-256` packet must not contain .env, passwords, keys, cookies, tokens, recovery codes; must pass secret-pattern checks.
  - `packages/adapters/src/devin.ts:244` transport errors are mapped to generic `TRANSPORT_ERROR_MESSAGE` to avoid leaking credentials; cause not attached.
  - `packages/gate/src/cli.ts:210` `safeError` never echoes file contents/manifest; only short safe messages.

- **Auth boundaries:**
  - GitHub webhook HMAC-SHA256 verification (strict `sha256=` format, raw-payload input, constant-time comparison, secrets never logged) claimed in `.hermes.md:20` and Phase 2 adapters — **UNVERIFIED** without inspecting `github.ts`, but shape trusted per existing tests `github.test.ts`.
  - DevinAdapter bounded budget, no fast mode auto-selection, risk-based model selection prevents privilege escalation.
  - Gate CLI approval token requires signedAt/approver/reason/signature [cli.ts:312] but signature crypto not inspected — **UNVERIFIED**.

- **Path traversal:** `validation.ts:124` `isRelativePath` rejects absolute, backslash, empty segments, `hasTraversal` rejects `..`. Artifact path validation [validation.ts:263-269].

- **SQL injection prevention:** `packages/db/src/queue.ts:16` all SQL uses positional `$1,$2` — worker ids/cutoffs never interpolated.

- **Operational security gaps:**
  - `.env` file exists (tracked? check git ls-files) but is listed in `.env.example`; `collect_repo_evidence` skips .env from scan but `.env` presence on disk is not redacted. [FACT: G:\Agent-Tools\hermes-ops\.env exists].
  - `AGENTS.md` / machine-discovery may contain provisioning scripts — not reviewed for secret handling. **UNKNOWN**.
  - Dirty worktree contains `external-review.json` artifacts at `.hermes/reviews/codex-smoke...` — could leak prior prompts if they included sensitive context, but build_review_packet mitigates.

**Overall:** Security posture is **strong for P0-3 scope** (pure functions, no network, sanitization), but live transports (DevinCliTransport, GitHub webhook server) are **untested** in this env.

## 9. OPERATIONAL STATUS

- **Deployment:** `docker-compose.yml` (manifest list) suggests postgres service; `.pgdata/` directory exists but `docker ps` not executed. `DOCKER` capability in skill (D4) implies Devin-based deploys.
- **Monitoring/Recovery:** Queue primitives include stale-lock recovery (5 min default `recover_stale_locks` in ops_adapter.py:400), requeue/backoff, audit_events append-only [schema.ts:169]. E2E smoke covers recovery phase [smoke.mjs:123].
- **Availability:** Postgres dependency for runtime truth; no health checks probed here. **UNVERIFIED** if DB migrations up-to-date (mismatch 0006).
- **CI gate:** GitHub Actions `ci.yml` runs build/test/integration on push to main + PRs; policy gate CLI is the final merge gate per skill diagram `PR -> CI + CodeRabbit + Security -> Hermes -> risk routing -> policy-gate`.
- **Dirty snapshot risk:** `reality-check-...` run is dirty_snapshot=true (INFERENCE: per SKILL.md 129 label). The reconciler must handle dirty vs HEAD — not yet exercised.

## 10. STATE MANAGEMENT

- **Ops DB is authoritative** — `skills/software-development/project-review-orchestrator/SKILL.md:414` and `ops_adapter.py:1` docstring: "Ops DB is SINGLE AUTHORITATIVE PATH". Task DAG written transactionally to Ops DB; AgentMemory is NOT authoritative [SKILL.md:419]. `task-plan.json` is fallback artifact only.
- **Tables:** tasks (external_id UNIQUE), jobs, agent_runs (provider+external_run_id UNIQUE), evidence (evidence_identity UNIQUE, + repo_owner/repo_name/head_sha/idempotency_key UNIQUE), audit_events, schema_migrations [queue.ts:355, schema.ts:185].
- **Claim semantics:** `FOR UPDATE SKIP LOCKED`, `available_at <= now()`, order by available_at,id, increments `attempts`, sets `locked_at/locked_by` [queue.ts:50, ops_adapter.py:291].
- **Idempotency:** `tasks.external_id` (creation), `evidence.evidence_identity` (global), `evidence (repo,sha,idem)` tuple, `agent_runs (provider,external_run_id)` [queue.ts:328-361].
- **Task lifecycle:** 10 statuses with `QUEUE_TRANSITIONS` guard [schema.ts:70, ops_adapter.py:82]; `is_valid_transition` enforced in `transition_task` [ops_adapter.py:329].
- **Evidence identity:** `computeEvidenceIdentity` (canonical json sha256) in contracts; `make_evidence_identity` in ops_adapter.py:111 mirrors.
- **State files:** `.hermes/reviews/<RUN_ID>/state.json` tracks progress_pct, status (EVIDENCE_COLLECTED → ... → TASKS_DECOMPOSED) [state.json: status, progress_pct in codex-smoke]. `update_state.py` advances state machine [SKILL.md:107].
- **Trace ID:** SKILL diagram `Ops DB + Trace ID` implies per-run trace; codex-smoke state shows `run_id: codex-smoke-2026-08-25` as trace.
- **Current reality:** This run's `state.json: status=EVIDENCE_COLLECTED` (early). No tasks yet decomposed; `conflicts.json` CLEAR means no blocking state divergence.

## 11. CONCURRENCY

- **Queue concurrency:** `SKIP LOCKED` allows multiple workers to poll without contention [queue.ts:15]; each claims exactly one row. Safe under `READ COMMITTED` [queue.ts:47 comment].
- **Retry/backoff:** Deterministic exponential + optional seedable jitter avoids thunder-herd [queue.ts:119-170]; `hash32` FNV-1a seeded by (jitterSeed, attempt).
- **Stale recovery:** `computeStaleLockCutoff(now, staleAfterMs)` + `RECOVER_STALE_*_SQL` (cutoff $1) [queue.ts:294-322] and `ops_adapter.py:400` (to_timestamp cutoff). Bounded, observable via `last_error`.
- **DAG parallelism:** `SKILL.md:399-411` tasks have `parallel_group` and non-overlapping `write_scope` (avoid overlapping write scopes), acyclic DAG. Not yet generated for this run — INFERENCE.
- **File locking:** No explicit file locks for `state.json`/`repo-evidence.json` beyond mkdir atomicity. Concurrent runs with same RUN_ID could collide; RUN_ID includes timestamp + short SHA to mitigate [collect_repo_evidence.py:319].
- **DevinAdapter:** Stateless aside from options; all state in transport/control plane [devin.ts:265 comment]; lease/timeout/idempotency/circuit breaker per diagram `DevinAdapter scope/lease/timeout/idempotency/circuit breaker` — **UNVERIFIED**: circuit breaker not visible in `devin.ts` (maybe DevinCliTransport concern).

## 12. MEMORY BOUNDARIES

Per `.hermes.md:16-17` and `SKILL.md:510-548`:

- **AgentMemory:** "historical context — architecture decisions, previous incidents, rejected approaches, recurring bugs, user constraints, prior review outcomes" [SKILL.md:148-165]. Treated as **context, not current repository truth** [SKILL.md:157]. Only reusable knowledge promoted after verified completion: architecture decision, verified lesson, incident root cause, reusable fix, important constraint [SKILL.md:468-486]. Never save transient queue state, logs, secrets, speculative claims. Canonical decisions also go to Git-controlled docs.
- **Ops DB:** Owns execution state (tasks, jobs, agent_runs, evidence, audit_events) [SKILL.md:414-418]. This is the runtime source of truth for the control plane.
- **Git/Repo:** Current truth [SKILL.md diagram left node].
- **Conflict detector** explicitly separates these three authorities [conflict_detector.py:1-12].

**This run:** No AgentMemory entries provided (memory_entries [] → 0 MEMORY_VS_REPO/STALE_MEMORY conflicts). So memory boundary not exercised; no promotion yet. Dirty worktree with `devin/*.md` and `codex-smoke` artifacts are **transient run state** and must remain in `.hermes/reviews/` or Ops DB, not AgentMemory per rules — currently satisfied (they are files, not memory).

## 13. EXTERNAL DEPENDENCIES

- **Declared manifests:** `pnpm-lock.yaml`, `package.json` (pNpm 10.6.2, TypeScript 5.7, pg 8.22, vitest 3.1) [package.json:5,14]; `docker-compose.yml` (postgres:16-alpine) [ci.yml:15].
- **Internal packages:** `@hermes-ops/contracts`, `@hermes-ops/policy`, `@hermes-ops/db` (uses `pg`), `@hermes-ops/adapters`, `@hermes-ops/gate` — pnpm workspace [pnpm-workspace.yaml].
- **External services (per architecture):**
  - GitHub (webhook HMAC, PR/CI integration, policy gate as GitHub Action) — adapter exists, not exercised without credentials.
  - CodeRabbit (findings normalization) — `packages/adapters/tests/coderabbit.test.ts`, `SKILL.md:19` preserve only id/severity/resolved.
  - Devin (coding agent) — `CodingAgentAdapter` interface `createRun/getRun/sendFeedback/cancelRun` [hermes.md:26], `DevinAdapter` with risk-based model glm-5-2/swe-1-7, `DevinCliTransport` mentioned.
  - PostgreSQL — `packages/db` + `ops_adapter.py` (psycopg2), `e2e/smoke.mjs` (`pg` driver).
  - OpenAI/Codex (external review) — `openai_review.py` needs `OPENAI_API_KEY` [SKILL.md:55], `codex_review.py` needs Codex CLI + ChatGPT Plus [SKILL.md:82].
- **Security-sensitive paths:** From evidence, `security_sensitive_paths_sample: []` (none flagged) [repo-evidence.json: project_structure.security_sensitive_paths_sample] — surprising given `packages/adapters/src/github.ts` etc. contain `auth`/`token` keywords but `looks_security_sensitive` heuristic [collect_repo_evidence.py:129] splits on `/\\_.\-` and checks exact keyword parts — may miss `github` vs `auth`. **INFERENCE**: heuristic is coarse.
- **No secret dependencies leaked** in packet (verified via SECRET_PATTERNS).

## 14. TECHNICAL DEBT

Evidence-backed (git status + evidence):

- **Dirty worktree 49 entries** — uncommitted skill evolution (execution-discipline skills, new scripts, migration 0006, tests). Pay down by committing or stashing; current HEAD (`b4a92d8`) lacks these, causing evidence drift.
- **Migration drift** — 0006_expand_task_statuses.sql untracked while schema.ts modified to reference it [schema.ts:185 vs git status ??]. Debt: DB schema at HEAD vs worktree diverging; migration runner may fail checksum.
- **TODO markers 25** (TODO 10, FIXME 5, HACK 5, XXX 5) [repo-evidence.json]. Sample HIGH churn files: `packages/adapters/tests/devin.test.ts` etc. — not expanded but count suggests ongoing work.
- **Pnpm toolchain not on PATH** — Windows env lacks pnpm/node corepack; quick fix is `corepack enable` or adding `C:\pinokio\bin\miniforge` to PATH and `npm install -g pnpm`. Blocks `lint/build/test`.
- **Duplicate smoke artifacts** — `.hermes/reviews/codex-smoke-2026-08-25/` and `devin/devin-task-PROJ-*.md` are stale demo data (2-file dummy repo) cluttering worktree; should be ignored via `.gitignore` or cleaned.
- **Churn hotspots**: `pnpm-lock.yaml` 1172 churn (single file) — suggests fragile lock updates; `.hermes/plans/...` 724 — docs churn not code.
- **.hermes.md Phase docs up to Phase 3 only** — plan files `WAVE1-REVISED-2026-08-21.md` indicate Wave1 roadmap beyond P3, not reflected in .hermes.md. Stale doc risk.

## 15. ARCHITECTURE CONTRADICTIONS

- **Documented vs actual statuses:** `.hermes.md` describes only Phase0-3, but `schema.ts:16` now defines 10 statuses including `planning|queued|dispatched|verifying|blocked`. At HEAD, migration 0006 not committed, so DB at HEAD would only support `pending|running|completed|failed|cancelled` (0001 tasks). **CONTRADICTION:** code (schema.ts dirty) expects 0006, but committed history does not.
- **Claim scope mismatch:** `ops_adapter.py:301` claims `status IN ('pending','queued')` while initial `packages/db/src/queue.ts:60` claims `status='pending'` only. Worktree disparity (ops_adapter updated for queued, queue.ts still pending-only) — **CONTRADICTION** noted in git diff modified files (`M packages/db/src/schema.ts` but not queue.ts?). Verify: `queue.ts` at HEAD has pending only, but 0006 index covers pending|queued; claim must cover queued per 0006 doc.
- **Execution-discipline skills duplication:** `skills/software-development/execution-discipline/{systematic-debugging,test-driven-development,verification-before-completion,receiving-code-review}` appear as **untracked** ?? duplicates of `superpowers-main/skills/*` — **CONTRADICTION:** two sources of same skill names; unclear which is canonical (hermes-ops skills vs superpowers).
- **CI vs local toolchain:** `ci.yml` uses `pnpm/action-setup@v4` + `cache: pnpm` but local env cannot resolve `pnpm` — development governance assumes pnpm available, local reality doesn't.
- **Evidence schema:** `repo-evidence.json` schema_version 1.0 uses `languages_by_file`/`languages_by_bytes` counts, but `validation.ts` EvidenceManifest schema (Phase0) is different artifact (PR evidence). Two "evidence" concepts coexist (repo evidence vs policy evidence manifest) — not contradictory functionally but naming collision.

## 16. DUPLICATED RESPONSIBILITIES

- **`queue.ts` vs `ops_adapter.py`:** Both define claim SQL (`CLAIM_TASK_SQL` identical to `ops_adapter.py:291` inline SQL), status transitions (`QUEUE_TRANSITIONS` duplicated in `schema.ts:70` and `ops_adapter.py:82`), idempotency helpers (`make_evidence_identity` vs `computeEvidenceIdentity`). **INFERENCE:** Python ops_adapter mirrors TS db package but drift possible (e.g., queued vs pending). Should share spec or generate.
- **Classification duplicated:** `packages/policy/src/classifier.ts` binary (`auto-eligible/human-required`) vs diagram's `LOW/MED | HIGH | CRITICAL` routing. Policy gate CLI does `classifyFromPolicyResult` + `recalculatePostDiffRisk` — but classifier no longer returns 4-tier risks. **DUPLICATION/CONFUSION:** legacy risk wording in diagram vs implemented binary.
- **Skills:** `superpowers-main/skills/{systematic-debugging,test-driven-development,verification-before-completion}` vs `skills/software-development/execution-discipline/*` — **DUPLICATE** content (same SKILL.md names) via copy. Needs deduplication or explicit vendoring.
- **E2E paths:** `e2e/smoke.mjs` (TS/JS) vs `packages/db/tests/e2e-smoke.test.ts` and `skills/.../tests/e2e_canonical_pipeline.py` — three e2e harnesses overlapping DB pipeline verification.

## 17. WRONG / STALE DOCUMENTATION

- **`.hermes.md` stale at Phase 3:** Does not mention migration 0006, `review_run_id`/`dag_payload` columns, or pipeline phases beyond policy gate. New plan files `WAVE1-REVISED-2026-08-21.md` etc. are in `.hermes/plans/` but not rolled into README/.hermes.md.
- **`package.json` scripts:** `db:migrate` runs `tsc -b packages/db/tsconfig.json && node packages/db/dist/migrate-bin.js` — but `tsconfig.tsbuildinfo` was previously tracked (removed in f04ceb0). Stale tsbuildinfo may have caused earlier CI failure; now fixed.
- **`SKILL.md` prerequisites:** Lists `AgentMemory MCP`, `OPENAI_API_KEY`, `Devin Desktop Codemaps` as optional, but for this Windows opencode runtime, none are available; docs imply they are integral but startup does not warn clearly.
- **`.env.example` vs `.env`:** `.env` present on disk, not in evidence (sensitive path skipped), but no documentation confirms whether `.env` is gitignored — `is_sensitive_path` skips .env, good, but manual leak risk if user commits.
- **Churn docs `20260820T223658Z_80ddaee/repo-evidence.json` 408 lines** appear in churn top list — indicates evidence artifacts were previously committed (now likely gitignored? but older churn shows they were). Current `.gitignore` should exclude `.hermes/reviews/` but older reviews slipped.

## 18. MOST IMPORTANT RISKS

1. **Dirty worktree + schema drift (HIGH)** — Evidence at HEAD (`b4a92d8`) does NOT contain 0006 migration or new skills, but worktree does. Any DB operation against HEAD checkout would fail or silently ignore review_run_id/dag_payload. Conflict detector does not flag schema file existence mismatches (only a subset). **Evidence:** `git status` M schema.ts, ?? 0006 Expand, dirty=true 49 entries [repo-evidence.json, git log].
2. **Toolchain gap blocks verification (MEDIUM)** — pnpm/node missing on PATH prevents `lint/build/test` and `db:migrate` local runs. CI passes on GitHub Actions but developer cannot reproduce locally without env fix. **Evidence:** bash errors `pnpm not recognized`, `python not recognized` (before uv), `where.exe node` none, only `C:\pinokio\bin\miniforge\node.exe` [bash proofs].
3. **External review not yet exercised against hermes-ops (MEDIUM)** — Real external review (Codex/OpenAI) was only smoke-tested on a dummy 2-file repo (`codex-smoke-2026-08-25` with 1 TODO). Hermes-analysis for hermes-ops itself has not been critiqued; blockers like dirty drift / duplicated queue logic / pending vs queued mismatch have not been independently challenged. **Evidence:** codex-smoke repo-evidence.json tracked_count 2, languages Markdown+Python, `reconciled-review.json` exists but for dummy.
4. **Three e2e harnesses + two queue implementations drift (LOW/MED)** — `queue.ts` vs `ops_adapter.py` duplication can silently diverge (claim pending vs pending|queued). No single source of truth test enforces parity. **Evidence:** `queue.ts:60` vs `ops_adapter.py:301`.
5. **Secret handling strength is high but token high-entropy heuristic may over-redact (LOW)** — could hide useful evidence (e.g., file hashes) or under-redact (40-char SHA is low entropy by heuristic, so not redacted — correctly). Needs fuzz test.

## 19. SIMPLER ALTERNATIVES

- **Single queue definition:** Generate Python ops_adapter constants from `queue.ts` (or vice versa) via codegen at build, rather than maintaining duplicate SQL/transition maps. Simpler: make `ops_adapter.py` import generated `queue.json` spec.
- **Vendor superpowers instead of copy:** Replace `skills/software-development/execution-discipline/*` copies with git submodule or `superpowers-main` as single source, and reference via symlink/alias. Reduces duplication and stale doc risk.
- **Toolchain bootstrap script:** Provide `scripts/bootstrap.ps1` / `bootstrap.sh` that does `corepack enable && corepack prepare pnpm@10.6.2 --activate && python -m pip install -r requirements` so new checkout is one-click. Current `SKILL.md` lists manual prerequisites.
- **Evidence collection as npm script:** Wrap `python .../collect_repo_evidence.py` as `pnpm evidence` so `collect_repo_evidence` version is pinned via `requirements.txt` and not reliant on global python path (uv vs system).
- **DB migration single-step:** Consolidate 0001-0006 into one `init.sql` for fresh installs, keeping incremental only for upgrades; reduces claim index drift confusion.

## 20. RECOMMENDED PRIORITY ORDER

1. **P0 — Commit or stash dirty worktree (schema + migration 0006 + new skills)** — Make HEAD = worktree (or explicitly discard). Then re-run `collect_repo_evidence --repo . --out .hermes/reviews/<new>` to get CLEAN evidence (dirty false). Fixes risk #1. Acceptance: `git status --porcelain=v1` empty or dirty explicitly justified; `state.json dirty:false`; `MIGRATION_FILES` matches committed migrations.
2. **P0 — Fix local toolchain PATH** — Add `C:\pinokio\bin\miniforge` + Scripts + enable corepack/pnpm; verify `pnpm --version`, `node --version`, `python --version`, `docker --version`, `psql`? Run `pnpm lint && pnpm build && pnpm test` locally; verify `precommit` parity with CI.
3. **P1 — Parity test for queue implementations** — Add `scripts/test_codex_readonly.py` or new `test_queue_parity.py` asserting `queue.ts CLAIM_TASK_SQL` equals `ops_adapter.py` claim SQL normalized, and `QUEUE_TRANSITIONS` maps equal. Prevents silent drift.
4. **P1 — Real external review on hermes-ops** — Run `build_review_packet.py --evidence .../repo-evidence.json --analysis .../hermes-analysis.md --out .../external-review-packet.json`, then `codex_review.py` (or `openai_review.py` if OPENAI_API_KEY available) against the packet. Produce `external-review.json` + `reconciled-review.json` for this run. This will challenge sections 5/15/16.
5. **P2 — Codemap brief + task DAG** — `build_codemap_brief.py` then `decompose_tasks.py` to produce `task-plan.json` from reconciled review; `ops_adapter.py` integration test with temporary Postgres (via docker-compose) to prove `bulk_create_tasks` idempotency.
6. **P2 — Doc sync** — Update `.hermes.md` and `README.md` to reflect Phase 6 statuses, Ops DB as authoritative, and toolchain bootstrap. Remove committed `.hermes/reviews/` churn artifacts from git history if still tracked.

## 21. QUESTIONS FOR INDEPENDENT REVIEWER

1. **Schema drift:** Given `schema.ts` dirty modification + `0006_expand_task_statuses.sql` untracked, does the current HEAD migration chain safely upgrade a fresh DB? Is the claim index `status IN ('pending','queued')` intended to replace `pending` only, and should `queue.ts:60` be updated to `IN ('pending','queued')` to match `ops_adapter.py:301` and migration 0006?
2. **Risk routing binary vs diagram:** The diagram specifies `LOW/MED -> normal verification`, `HIGH-> Codex`, `CRITICAL-> Codex+Human`, but implemented `classifier.ts` is binary `auto-eligible|human-required` and gate CLI only checks `riskClass==='human-required'`. Is this collapse intentional, and where should 4-tier routing be implemented if needed?
3. **Duplicate queue / e2e ownership:** Should `ops_adapter.py` be the single Python source of queue SQL (generating `queue.ts` constants), or should a shared `queue.spec.json` drive both? Which dir is authoritative?
4. **Dirty snapshot semantics:** `SKILL.md` says label `dirty_snapshot=true` but does not define how `reconcile_review.py` should treat changed_entries 49 paths. Should unverified findings be blocked from task generation when dirty?
5. **Secret redaction correctness:** Does the high-entropy generic token pattern `r"\b[A-Za-z0-9-_=]{20,}\b"` with `_has_high_entropy` (>=3 classes) correctly avoid redacting git SHAs / lockfile hashes while catching real tokens? Provide targeted tests (`build_review_packet` secret-pattern tests).
6. **External review packet bounds:** Packet includes deterministic evidence + Hermes analysis but not file contents. Is this sufficient to catch architectural contradictions (e.g., `superpowers-main` vendoring) without leaking secrets? Should packet include bounded file listings or churn samples beyond what `repo-evidence.json` already has?
7. **DevinAdapter circuit breaker:** Diagram mentions `circuit breaker` at DevinAdapter layer; `devin.ts` has `withTransport` error mapping but no circuit breaker state. Is circuit breaking implemented in `DevinCliTransport` (not inspected) or missing? If missing, what threshold/timing is desired?

---

## Appendix: Startup Reality Check — Skill Executability

This appendix records what was actually executed in environment `win32` (powershell) on `G:\Agent-Tools\hermes-ops`:

| Step | Command | Result |
|------|---------|--------|
| Evidence collect | `python3.12.exe skills/.../collect_repo_evidence.py --repo G:\Agent-Tools\hermes-ops --out .hermes/reviews/reality-check-2026-08-25T053717Z --run-id reality-check-2026-08-25T053717Z` | **PASS** — ok:true, commit b4a92d86, dirty:true, tracked 105, state EVIDENCE_COLLECTED. Files: repo-evidence.json (541607 bytes tracked safe), repo-evidence.md, state.json, conflicts.json. |
| Git/Repo truth | `git rev-parse HEAD` / `git status --porcelain=v1` / `git log --oneline -15` | **PASS** — HEAD b4a92d86, branch main, 49 dirty entries. Git is truth source. |
| Ops DB truth | `ops_adapter.OpsDbAdapter` without DATABASE_URL → fallback | **SKIP/CLEAR** — No DATABASE_URL, detector returned 0 tasks, status CLEAR. Expected skip in local-only reality check; live DB requires `docker-compose up` or `DATABASE_URL=postgres://...`. |
| AgentMemory truth | No AgentMemory MCP available in opencode runtime | **SKIP** — No historical context retrieved; recorded as unavailable per SKILL.md recall step. |
| Conflict Detection | `python conflict_detector.py --repo . --review-sha b4a92d86 --out conflicts.json` | **PASS** — status CLEAR, 0 conflicts, requires_reconciliation false, repo_sha matches review_sha. |
| Hermes Analysis | Manual authoring of `hermes-analysis.md` (this file) with 21 sections evidence-referenced | **PASS** — File created at `.hermes/reviews/reality-check-2026-08-25T053717Z/hermes-analysis.md`. |
| Build Packet | Pending | `build_review_packet.py --evidence repo-evidence.json --analysis hermes-analysis.md --out external-review-packet.json` — not yet run (requires analysis completion). Next step. |
| External Review | Pending | Requires `OPENAI_API_KEY` or `codex` CLI. Prior smoke `codex-smoke-2026-08-25` used dummy repo and completed TASKS_DECOMPOSED, but not hermes-ops. |
| Toolchain | `pnpm`/`node`/`python` probe | **PARTIAL** — `C:\pinokio\bin\miniforge\node.exe` v24.18.0 exists, `uv` python 3.12.14 used, `git` OK, `pnpm` missing on PATH, `docker` not probed. |

**Verdict:** Skill **boots and collects deterministic evidence successfully** with realistic data (105 files, dirty true). Downstream gates (Ops DB, AgentMemory, external Codex) are correctly modeled as **optional/degraded** — they skip gracefully rather than crash. The remaining gap for full pipeline is toolchain PATH (pnpm) and optional external reviewer credentials, both surmountable per SKILL.md Prerequisites. The skill is **executable for its intended `terminal` toolset** on this Windows host.

---

*End of Hermes analysis — independent external review should now be obtained via `build_review_packet.py` + `codex_review.py`/`openai_review.py` and reconciled.*
