---
name: open-design-orchestrator
description: Orchestrate the full Open Design loop from user request to merge and skill promotion.
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [open-design, orchestration, governance, devin, codex]
    related_skills: [project-review-orchestrator, execution-discipline]
    requires_toolsets: [terminal]
---

# Open Design Orchestrator

## When to Use

Use this skill when the user asks Hermes to:

- perform a full Open Design cycle on a real project;
- go from user request → evidence → review → task DAG → Devin → PR → CI → merge;
- enforce the Open Design governance loop including policy gate and repair loop;
- assign the right model/agent to each stage.

Do not use this skill for:

- a single trivial edit;
- bypassing the policy gate or human approval for CRITICAL changes;
- running without a configured `DATABASE_URL` if Ops DB dispatch is required.

## Prerequisites

- Python 3.11+
- `git` in `PATH`
- PostgreSQL with Ops DB migrations applied (`DATABASE_URL`)
- Devin CLI authenticated (for real dispatch)
- Codex CLI or `OPENAI_API_KEY` (for external review)
- `~/.hermes/config.yaml` with `skills.external_dirs` pointing to the repo `skills/` directory

## Quick Reference

Run the full Open Design loop:

```bash
python skills/software-development/open-design-orchestrator/scripts/open_design.py \
  --repo . \
  --out .hermes/open-design/<RUN_ID> \
  --reviewer codex \
  --dispatch-mode dry-run
```

Run only up to policy gate:

```bash
python skills/software-development/open-design-orchestrator/scripts/open_design.py \
  --repo . \
  --out .hermes/open-design/<RUN_ID> \
  --reviewer mock \
  --stop-after policy_gate
```

## Procedure

1. **Evidence & Context**
   - Collect deterministic repo snapshot (`collect_repo_evidence.py`).
   - Recall AgentMemory (advisory only).
   - Run `conflict_detector.py` to detect Git/Ops/Memory conflicts.

2. **Hermes Analysis**
   - Produce `hermes-analysis.md` with FACT/INFERENCE/UNKNOWN labels.
   - `task_classifier.py` adds task type + early risk.

3. **Strategy & Spec Review**
   - `strategy_router.py` selects trivial / normal / formal path.
   - For HIGH/CRITICAL or `spec_level=formal`, run `codex_review.py` spec review.

4. **External Review**
   - Build sanitized packet (`build_review_packet.py`).
   - Run `codex_review.py` or `openai_review.py`.

5. **Reconcile & Codemap**
   - `reconcile_review.py` produces disposition matrix.
   - `build_codemap_brief.py` creates Devin-ready brief.

6. **Task DAG & Ops DB**
   - `decompose_tasks.py` builds DAG.
   - Write tasks to Ops DB with trace ID.

7. **Dispatch to Devin**
   - `dispatch_to_devin.py` claims and dispatches per risk.
   - Devin follows execution-discipline skill for task type.

8. **CI / Review / Gate**
   - Collect CI status, CodeRabbit findings, Codex re-review if needed.
   - `final_risk.py` recalculates risk.
   - `hermes-policy-gate` (packages/gate) returns PASS/REPAIR/ESCALATE/BLOCK.

9. **Repair Loop (bounded ≤3)**
   - If REPAIR: re-dispatch to Devin with `attempts` incremented.
   - If ESCALATE: require human-in-the-loop.
   - If BLOCK: stop and report.

10. **Merge & Learn**
    - On PASS, allow merge (Hermes or human creates PR/merge).
    - `open_design.py` collects outcome metrics.
    - Update AgentMemory with verified lessons.
    - If pattern repeats ≥3 times, draft candidate skill for Sếp approval.

## Role & Model Mapping

See `references/roles-and-models.md` for the full matrix. Key principles:

- `glm-5-2`: routing, parsing, lightweight summaries.
- `swe-1-7`: architecture analysis, task decomposition, contested findings.
- `gpt-5.6-sol` (Codex CLI): read-only adversarial review.
- Human: CRITICAL approval, policy gate override, skill promotion.

## State Machine

```
CREATED
  → PREFLIGHT
  → EVIDENCE_COLLECTED
  → CONFLICT_CLEAR
  → HERMES_ANALYSIS_DONE
  → STRATEGY_ROUTED
  → PACKET_BUILT
  → EXTERNAL_REVIEW_RECEIVED
  → RECONCILED
  → CODEMAP_BUILT
  → TASKS_DECOMPOSED
  → DISPATCHED
  → IMPLEMENTATION_DONE
  → CI_FINDINGS_RECEIVED
  → FINAL_RISK_RECALCULATED
  → POLICY_GATE
  → { PASS | REPAIR | ESCALATE | BLOCK }
  → MERGED (only from PASS)
  → OUTCOME_COLLECTED
  → LESSONS_PROMOTED
```

## Security

- Never send raw source to external review; only sanitized evidence packets.
- `codex_review.py` runs in `read-only` sandbox.
- `DISAGREE` findings are never turned into tasks.
- CRITICAL changes require human approval before merge.
