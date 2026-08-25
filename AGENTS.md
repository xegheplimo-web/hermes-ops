# Hermes Ops — Agent Notes

## Verification commands

Run from repo root (`G:\Agent-Tools\hermes-ops`):

```bash
# Project-review-orchestrator tests
C:\pinokio\bin\miniforge\python.exe skills/software-development/project-review-orchestrator/tests/test_conflict_detector.py
C:\pinokio\bin\miniforge\python.exe skills/software-development/project-review-orchestrator/tests/test_strategy_router.py

# Open Design smoke tests
C:\pinokio\bin\miniforge\python.exe skills/software-development/open-design-orchestrator/tests/test_open_design_smoke.py

# E2E canonical pipeline (requires PostgreSQL on DATABASE_URL)
C:\pinokio\bin\miniforge\python.exe skills/software-development/project-review-orchestrator/tests/e2e_canonical_pipeline.py
```

## Python interpreter

PowerShell does not have `python` on PATH. Use `C:\pinokio\bin\miniforge\python.exe`.

## Recent fixes (blockers → implemented)

- Model/role allocation config: `project-review-orchestrator/scripts/model-roles.json` + `model_resolver.py`. Wired into `dispatch_to_devin.py`, `codex_review.py`, `openai_review.py`, `strategy_router.py`, `decompose_tasks.py`, and `open_design.py`.
- Fallback model chain: resolver filters each stage's fallback chain by the provider's valid model set (`provider_valid_models`).
- Trace ID propagation: `trace_context.py` helper; `HERMES_TRACE_ID` env set by `open_design.py` and carried through all script outputs + `ops_adapter.py` audit detail.
- Cost-bounded repair loop: `RepairBudget` in `open_design.py` enforces `max_attempts`, `max_duration_seconds`, and `max_cost_usd`.
- Independent policy gate for HIGH/CRITICAL: `open_design.py` runs an independent adversarial review for HIGH/CRITICAL findings and the policy gate blocks if `independent-review.json` is missing.
- Structured outcome metrics: `open_design.py` `stage_outcome()` writes `outcome.json` with `trace_id`, duration, task count, gate decision, repair budget, and estimated cost.
- Conflict severity weights + AgentMemory as hint: `conflict_detector.py` now has `severity_score`, `severity_threshold`, and AgentMemory conflicts are `info` severity with a verification rationale.
- Preferred vs concrete model: `model_resolver.py` returns both `preferred` (from the allocation table) and `primary` (first provider-valid runnable model). `decompose_tasks.py` and `task-plan.json` now include both `preferred_model` and `model`.
- Stage timing: `open_design.py` records `stage_durations` for every state transition and `outcome.json` includes `stages_completed`.
- State preservation: `collect_repo_evidence.py` now merges with existing `state.json` so orchestrator-owned keys (`started_at`, `trace_id`, `artifacts`) survive.
