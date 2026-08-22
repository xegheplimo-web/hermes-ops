# Codemap Brief

## Snapshot
- **Project**: hermes-ops
- **Commit**: `80ddaee10`
- **Design SHA**: `d50915ea`
- **Review Run**: `2026-08-23T030522Z-80ddaee`

## Flows That Need Mapping

Based on reconciled findings, the Codemap should map:

### 1. Entry Points
- `hermes-policy-gate` CLI entry point (`packages/gate/src/bin.ts`)
- Migration runner entry point (`packages/db/src/migrate-bin.ts`)

### 2. Orchestration Flow
- How Hermes calls `collect_repo_evidence.py` → `build_review_packet.py` → `openai_review.py`
- How SKILL.md procedure translates to actual Hermes tool calls

### 3. Ops DB Ownership
- `packages/db/src/queue.ts`: claim, retry, recovery SQL
- `packages/db/src/schema.ts`: row types and status enums
- `packages/db/src/migrate.ts`: migration runner with SHA-256 checksums
- Migration files: `0001_tasks` → `0002_jobs` → `0003_agent_runs` → `0004_evidence` → `0005_audit_events`

### 4. Adapter Boundaries
- `packages/adapters/src/devin.ts`: DevinAdapter + risk-based model selection
- `packages/adapters/src/devin-cli-transport.ts`: CLI transport implementation
- `packages/adapters/src/coding-agent.ts`: Generic CodingAgentAdapter interface
- `packages/adapters/src/github.ts`: Webhook HMAC verification + delivery dedupe
- `packages/adapters/src/coderabbit.ts`: Finding normalization

### 5. Policy Evaluator + Gate
- `packages/policy/src/evaluator.ts`: evaluatePolicy() — the core deterministic function
- `packages/gate/src/cli.ts`: CLI wrapping evaluator

### 6. Contracts
- `packages/contracts/src/manifest.ts`: EvidenceManifest type + validation
- `packages/contracts/src/validation.ts`: 473-line validation engine
- `packages/contracts/src/identity.ts`: computeEvidenceIdentity SHA-256

### 7. Skill Infrastructure
- `skills/software-development/project-review-orchestrator/SKILL.md`
- `scripts/collect_repo_evidence.py`
- `scripts/build_review_packet.py`
- `scripts/openai_review.py`

### 8. Test Structure
- 13 test files across all packages
- `packages/db/tests/queue.integration.test.ts` (5 skipped — needs live DB)

## Modules Implicated by Accepted Findings
| Finding | Implicated Modules |
|---------|-------------------|
| MISSING E2E slice | All packages + Docker + Git |
| BLOCKED GitHub remote | Git + .github/workflows/ci.yml |
| PARTIAL DB migration | packages/db + Docker |
| MISSING human gate | packages/gate + new package |
| MISSING risk routing | packages/policy + packages/gate |