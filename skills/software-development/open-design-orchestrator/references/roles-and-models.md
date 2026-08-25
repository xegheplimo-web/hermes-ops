# Open Design — Role & Model Assignment

This document maps each stage of the Open Design loop to the responsible agent / model and the rationale. It is used by `open_design.py` when `roles_mode = strict` (the default).

## Assignment Matrix

| Stage | Owner | Best Model | Helper(s) | Why |
|---|---|---|---|---|
| **1. User Request Intake** | Hermes | `glm-5-2` | — | Lightweight parsing, slash command dispatch, policy check. |
| **2. Context / Evidence Snapshot** | `collect_repo_evidence.py` | — | Git, `find` | Deterministic, no LLM. SHA-tied evidence. |
| **3. AgentMemory Recall** | `conflict_detector.py` (memory hook) | `glm-5-2` for recall | AgentMemory MCP | Memory is advisory only; never overrides Git HEAD. |
| **4. Conflict Detection** | `conflict_detector.py` | `glm-5-2` for triage | Ops DB, Git | Compare Git/Ops/Memory; deterministic core, LLM for triage only. |
| **5. Hermes First-Pass Analysis** | Hermes + `task_classifier.py` | `swe-1-7` | `collect_repo_evidence.py` output | Requires deep architecture reasoning; FACT/INFERENCE/UNKNOWN labels. |
| **6. Strategy Router** | `strategy_router.py` | `glm-5-2` | `final_risk.py` early input | Deterministic rules + simple classification. |
| **7. Formal Spec / Codex Spec Review** | `codex_review.py` (spec mode) | `gpt-5.6-sol` via Codex CLI | Hermes-provided schema | External, adversarial, read-only review. Triggered for HIGH/CRITICAL or `spec_level=formal`. |
| **8. Approved Task + Acceptance Criteria** | `decompose_tasks.py` | `swe-1-7` for task splitting | `build_codemap_brief.py` | Must generate non-overlapping write scopes and DAG. |
| **9. Task DAG → Ops DB + Trace ID** | `ops_adapter.py` + `decompose_tasks.py` | — | PostgreSQL | Transactional, no LLM. Trace ID = `run_id`. |
| **10. DevinAdapter Dispatch** | `dispatch_to_devin.py` | risk-based: `glm-5-2` (LOW/MED), `swe-1-7` (HIGH/CRIT) | Ops DB, Devin CLI | Scope/lease/timeout/idempotency enforced by adapter. |
| **11. Devin Execution Discipline** | Devin | per task | `execution-discipline/*` skills | BUG → `systematic-debugging`; FEATURE → `test-driven-development`; ALL → `verification-before-completion`; PR review → `receiving-code-review`. |
| **12. Draft PR / PR** | Devin | — | GitHub | External human-gated merge target. |
| **13. CI / Codex / CodeRabbit** | External services | — | `packages/adapters/coderabbit.ts`, `codex_review.py` | CI provides hard evidence; CodeRabbit normalizes findings; Codex re-reviews if HIGH/CRIT. |
| **14. Structured Findings** | `reconcile_review.py` | `glm-5-2` | Codex/CodeRabbit output | Disposition = AGREE / PARTIAL / DISAGREE / NEW / UNVERIFIED. |
| **15. Hermes Reconcile** | `reconcile_review.py` + Hermes | `swe-1-7` for contested findings | evidence snapshot | Hermes has final say. DISAGREE never becomes task. |
| **16. Devin Repair Loop** | `open_design.py` orchestrator | risk-based | `dispatch_to_devin.py` | Bounded loop ≤ 3. Gate result REPAIR → re-dispatch. |
| **17. Fresh Verification** | `verification-before-completion` skill | — | CI, test commands | Evidence before completion claims. |
| **18. Final Risk Recalculation** | `final_risk.py` | `glm-5-2` | `reconcile_review.py` output | Post-CI/diff risk; may escalate to HIGH/CRITICAL. |
| **19. Policy Gate** | `packages/gate/` CLI (`hermes-policy-gate`) | — | `packages/policy/`, Ops DB | PASS / REPAIR / ESCALATE / BLOCK. Fail-closed. |
| **20. Merge** | GitHub | — | Human (required for CRITICAL) | Only reached after PASS + branch rules. |
| **21. Outcome Metrics / Lessons** | `open_design.py` (outcome stage) | `glm-5-2` for summarization | `ops_adapter.py` audit table | Extract root cause + verify against AgentMemory. |
| **22. AgentMemory Update** | `open_design.py` (promote stage) | `glm-5-2` | AgentMemory MCP | Persist verified lessons; mark repeated patterns. |
| **23. Candidate Skill Promotion** | Human-in-the-loop | `swe-1-7` for draft SKILL.md | `skill_manage` tool | Promote only after ≥3 successful, repeated patterns. |

## Model Rationale

- **`glm-5-2`**: fast/cheap, used for parsing, routing, deterministic classification, and lightweight summaries. Never used for contested architecture decisions or high-risk tasks.
- **`swe-1-7`**: Hermes brain / heavy reasoning. Used for architecture analysis, task decomposition, contested findings, and drafting skills.
- **`gpt-5.6-sol` via Codex CLI**: external, read-only, adversarial reviewer. Used in sandbox; no write access. Fallback to OpenAI API (`openai_review.py`) if Codex unavailable.
- **Human**: required for CRITICAL approval, policy gate override, and skill promotion.

## Execution Discipline Selection

Selected by `strategy_router.py` and passed to Devin prompt:

| Task Type | Strategy (discipline sequence) |
|---|---|
| `BUG` | `systematic-debugging` → `test-driven-development` (regression test) → `verification-before-completion` |
| `FEATURE` | `test-driven-development` (red-green-refactor) → `verification-before-completion` |
| `SECURITY` | `systematic-debugging` → formal spec review → `test-driven-development` → `verification-before-completion` |
| `INFRA` | `test-driven-development` (config/contract tests) → `verification-before-completion` |
| `REFACTOR` | `test-driven-development` (characterization tests) → `verification-before-completion` → `receiving-code-review` |
| `MIGRATION` | formal spec review → `test-driven-development` → `verification-before-completion` |
| `INVESTIGATION` | `systematic-debugging` (Phase 1 only) → report |

## Agent Boundaries

- **Hermes** is the only final authority. Skills and external models are advisors.
- **Devin** does not decide scope; it receives leased tasks with write_scope and acceptance criteria.
- **Codex** is read-only and cannot approve merges.
- **AgentMemory** is historical context, not runtime state. It cannot override current Git/Ops DB.
