# Hermes Project Analysis

## Snapshot
- **Project**: hermes-ops
- **Commit**: `80ddaee10393eca8f5552e226ebef3675ad0d976`
- **Branch**: `main`
- **Dirty**: true (5 non-source entries: .pgdata, __pycache__, tsbuildinfo)
- **Design SHA**: `d50915eaa2713bfb889cfc0b5ec24b6f386d159a3214fe4db5ca3b99281a9214`
- **Objective**: Audit full design implementation, identify gaps, create execution DAG, drive to completion

## Architecture Understood

**hermes-ops** is a TypeScript pnpm monorepo with 5 packages:

1. **`packages/contracts`** — `EvidenceManifest` v1 type definitions, runtime validation, evidence identity computation, custom errors. Pure TypeScript, no runtime deps.
2. **`packages/policy`** — Deterministic fail-closed `evaluatePolicy()` pure function. Validates evidence manifests, checks CI green status, policy version match, idempotency, CodeRabbit critical findings. Returns `{decision, reasonCode, ...}`.
3. **`packages/db`** — PostgreSQL queue primitives. 5 ordered SQL migrations (tasks → jobs → agent_runs → evidence → audit_events). Claim SQL with `FOR UPDATE SKIP LOCKED`. Retry/backoff with deterministic jitter. Stale-lock recovery. Idempotency via UNIQUE constraints. Pure SQL constants and pure helper functions — no DB driver imported.
4. **`packages/adapters`** — GitHub webhook HMAC verification, CodeRabbit finding normalization, generic `CodingAgentAdapter` interface, `DevinAdapter` with `DevinCliTransport`. All pure — transports injected, tests use in-memory doubles.
5. **`packages/gate`** — `hermes-policy-gate` CLI tool wrapping the policy evaluator. Reads JSON manifest from disk, evaluates, emits stable machine-readable JSON result.

**Skills** (`skills/software-development/project-review-orchestrator/`):
- `SKILL.md` — full procedural workflow (preflight → evidence → analysis → redaction → review → reconcile → codemap → tasks → Ops DB → Devin → PR → gate)
- `scripts/collect_repo_evidence.py` — deterministic repo metadata collector
- `scripts/build_review_packet.py` — sanitized review packet builder with redaction
- `scripts/openai_review.py` — OpenAI Responses API structured review (requires OPENAI_API_KEY)
- `templates/external-review-prompt.md` — reviewer role/prompt
- `templates/codemap-prompt.md` — Devin Codemap prompt
- `references/governance.md`, `review-contract.md`, `codemap-contract.md`

## Verified Completed Areas
| Area | Evidence |
|------|----------|
| All 5 packages compile | `pnpm build` → exit 0 |
| 310/315 unit tests pass | `pnpm test` → 12 files passed, 5 skipped (integration) |
| EvidenceManifest validation | 38 tests in manifest.test.ts |
| Policy evaluator | 20 tests in evaluator.test.ts |
| Queue SQL + retry/backoff + recovery | 27+27 tests in queue-sql + recovery |
| Migration runner with SHA-256 checksums | 27 tests in migrations, 14 in migrate |
| DevinAdapter with risk-based model selection | 51 tests in devin.test.ts |
| GitHub webhook verification | 20 tests in github.test.ts |
| CodeRabbit finding normalization | 27 tests in coderabbit.test.ts |
| CodingAgentAdapter interface | 18 tests in coding-agent.test.ts |
| Policy gate CLI | 28 tests in cli.test.ts |
| Evidence scripts work | collect_repo_evidence.py ran successfully |

## Partially Completed Areas
| Area | Issue |
|------|-------|
| Integration tests | 5 queue.integration.test.ts tests skipped — need live PostgreSQL |
| DB migrations applied | Postgres container exists but Docker daemon stopped; tables not created |
| Risk classification | Policy evaluator handles CI/CodeRabbit/critical findings but simplified risk routing (auto-eligible/human-required) not implemented per Codex review |
| Audit events table | Migration 0005 exists but not applied to live DB |
| Agent runs table | Migration 0003 exists but not applied to live DB |

## Missing Areas
| Area | Impact |
|------|--------|
| **E2E vertical slice (CRITICAL)** | No end-to-end path exists. Cannot prove the system works end-to-end. |
| **GitHub remote + PR flow (BLOCKED)** | No remote configured. Cannot push, open PRs, run CI. |
| **Human gate for critical actions** | No code for human approval workflow. |
| **Post-diff risk recalculation** | After every PR diff, risk should be recalculated. Not implemented. |

## Correctness Risks
| Risk | Explanation |
|------|-------------|
| **State split between AgentMemory and Ops DB** | Current design correctly avoids this, but needs enforcement. |
| **Docker daemon not auto-started** | Every time the machine reboots, Docker must be started manually. No startup script. |
| **DevinAdapter not tested end-to-end** | 51 unit tests but no actual Devin CLI call was verified. |

## Security Risks
| Risk | Explanation |
|------|-------------|
| **No secrets detection in CI** | `.env` is in .gitignore but no CI check prevents accidental commit. |
| **No credential rotation policy** | Devin CLI transport stores credentials locally; no rotation documented. |
| **OpenAI API key in env var** | OPENAI_API_KEY in environment; no encryption at rest. |

## Data Integrity Risks
| Risk | Explanation |
|------|-------------|
| **Stale lock recovery relies on clock** | `computeStaleLockCutoff` uses Date.now() which depends on system clock accuracy. |
| **No verification for untracked .pgdata** | Docker volume .pgdata is outside Git; no backup strategy. |

## Missing Evidence
| Evidence | Why Missing |
|----------|-------------|
| Live PostgreSQL with 6 tables | Docker daemon stopped, migrations not applied |
| GitHub PR creation from DevinAdapter | No remote configured |
| Codex review → DB evidence row | Adapter exists but not wired |
| CI run on push | No remote to push to |

## Priority Recommendations
1. **Start Docker + apply migrations** (unblocks integration tests + live DB)
2. **Configure GitHub remote + gh CLI** (requires Sếp — blocker for everything PR-related)
3. **Run integration tests** against live DB
4. **Implement E2E vertical slice** (W1-8) end-to-end
5. **Implement human gate** for critical risk
6. **Implement post-diff risk recalculation**
7. **Implement risk routing** (auto-eligible vs human-required)

## Questions for Independent Reviewer
1. Is the simplified risk model (auto-eligible / human-required) sufficient for P0, or should we keep LOW/MED/HIGH/CRITICAL from the original design?
2. Should AgentMemory integration be attempted on Windows (iii-engine Docker exists) or deferred entirely?
3. Is the DevinCliTransport architecture safe for production (credentials in env/CLI args)?
4. Should we add an automated startup script for Docker + migration on boot?