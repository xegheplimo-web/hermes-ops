# DESIGN CONTRACT — hermes-ops Project Review Orchestrator

## Source Document
- **File**: `C:\Users\atton\Downloads\deep-research-report (1).md`
- **SHA-256**: `d50915eaa2713bfb889cfc0b5ec24b6f386d159a3214fe4db5ca3b99281a9214`
- **Size**: 81,004 bytes / 3,004 lines
- **Modified**: 2026-08-21T05:41:00+07:00

## Intended Architecture

The design describes a **Hermes skill** (`project-review-orchestrator`) that orchestrates:

```
USER → HERMES → AgentMemory (recall) → Ops DB (runtime truth) → Project Review Skill
  → Evidence Collector → Hermes Analysis → Redaction Gate → External Review Adapter
  → (OpenAI Responses API | ChatGPT UI) → Hermes Reconcile → Codemap Brief
  → Devin Codemaps → Hermes Task DAG → Ops DB → DevinAdapter → Devin → PR
  → CI + CodeRabbit + Security → Risk Reconcile → policy-gate → PASS/REPAIR
```

## Authority Model (from design)
```
HERMES                = authoritative brain / orchestrator
AgentMemory           = cognitive context (NOT runtime state)
Ops DB                = authoritative execution/task state
DevinAdapter          = coding execution gateway
Devin                 = implementation executor
Git/GitHub            = authoritative code/change/evidence plane
CI/Security           = deterministic evidence producers
CodeRabbit/OpenAI     = independent review signals (NOT merge authority)
policy-gate           = deterministic enforcement
Human                 = required for CRITICAL actions
```

## Key Non-Goals (from design)
- No webhook receiver in P0 (use `gh api` polling instead)
- No GitHub App yet
- No AgentMemory / iii-worker / WSL Ubuntu in P0
- No leader election (single operator)
- No monitoring package (SQL query + log is enough)
- No reconciliation package (startup recovery job replaces it)
- No CodeRabbit until proven value-add
- No 3-tier risk (just auto-eligible / human-required)
- No programmatic ChatGPT scraping (violates OpenAI consumer Terms)

## Required Components
| # | Component | Status |
|---|-----------|--------|
| 1 | EvidenceManifest v1 contracts | INTENDED |
| 2 | Deterministic policy evaluator | INTENDED |
| 3 | PostgreSQL schema + migrations (5 tables) | INTENDED |
| 4 | Queue claim SQL (FOR UPDATE SKIP LOCKED) | INTENDED |
| 5 | Retry/backoff with deterministic jitter | INTENDED |
| 6 | Stale-lock recovery | INTENDED |
| 7 | Idempotency (external_id, evidence_identity) | INTENDED |
| 8 | GitHub webhook verification (HMAC-SHA256) | INTENDED |
| 9 | CodeRabbit finding normalization | INTENDED |
| 10 | Generic CodingAgentAdapter interface | INTENDED |
| 11 | DevinAdapter with CLI transport | INTENDED |
| 12 | hermes-policy-gate CLI | INTENDED |
| 13 | collect_repo_evidence.py script | INTENDED |
| 14 | build_review_packet.py script | INTENDED |
| 15 | openai_review.py script | INTENDED |
| 16 | SKILL.md with full procedure | INTENDED |
| 17 | Templates (external-review-prompt, codemap-prompt) | INTENDED |
| 18 | References (governance, review-contract, codemap-contract) | INTENDED |
| 19 | GitHub CI workflow | INTENDED |
| 20 | Audit events table | INTENDED |
| 21 | Agent runs table | INTENDED |
| 22 | Risk classification and routing | INTENDED |
| 23 | Low/Medium auto-gate | INTENDED |
| 24 | High requires independent review | INTENDED |
| 25 | Critical requires human approval | INTENDED |
| 26 | PASS/REPAIR bounded loop | INTENDED |
| 27 | AgentMemory promotion after verified completion | INTENDED |

## Verification Chain (from design)
```
RUN_ID → Git SHA → deterministic evidence → Hermes analysis → review packet hash
  → external structured review → reconciliation → Codemap → task DAG
  → Ops DB records → Devin sessions → PRs → CI/security/review → policy-gate → memory
```

## Design Invariants (from design)
1. No execution task without evidence_refs
2. No implementation before external review when policy requires
3. No external reviewer directly changes repo
4. No AgentMemory as authoritative runtime state
5. No task DONE without verification evidence
6. No HIGH task without independent review
7. No CRITICAL task without human approval
8. No PR bypasses CI/security
9. No external packet knowingly contains secrets
10. Review SHA must match the code being reasoned about

## Acceptance Criteria (from design)
- Every design requirement is COMPLETE_VERIFIED or NOT_APPLICABLE with justification
- No unexplained PARTIAL/MISSING/BROKEN/CONTRADICTED/UNKNOWN
- Implementation is not stub/placeholder/TODO/mock-only/dead code/unwired
- Tests pass, lint passes, build passes
- Security scans pass
- Governance flow satisfied: implementation → PR → CI → review → security → reconciliation → risk routing → policy-gate → PASS