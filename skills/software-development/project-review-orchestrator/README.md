# Project Review Orchestrator

A Hermes agent skill for orchestrating multi-phase, independent project reviews. Collects deterministic repository evidence, performs Hermes first-pass analysis, obtains an independent external critique via **Codex CLI** (ChatGPT Plus), reconciles findings, builds a Devin Codemap brief, decomposes into an execution task DAG, and dispatches to Devin for implementation — all governed by a state machine.

## Architecture Overview

```
Role:    Hermes = BRAIN      Codex = REVIEWER      Devin = CODER
```

**Review pipeline** (state machine driven):

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│ 1. Evidence  │────▶│ 2. Hermes    │────▶│ 3. Build     │────▶│ 4. External      │
│ Collection   │     │ Analysis     │     │ Packet       │     │ Review           │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────────┘
                                                                  ├ Codex CLI (default)
                                                                  └ OpenAI API (fallback)
                                                                          │
                                                                          ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 7. Task DAG  │◀────│ 6. Codemap   │◀────│ 5. Reconcile │◀────│ external     │
│ Decompose    │     │ Brief        │     │ Findings     │     │ review.json  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │
       ▼
┌──────────────┐     ┌──────────────┐
│ 8. Dispatch  │────▶│ Devin CLI    │
│ to Devin     │     │ → PR → CI    │
└──────────────┘     └──────────────┘
```

**Security model (two-layer lock):**
- Layer 1: Hermes policy — `CODEX_ROLE=reviewer`, Codex NEVER gets write capability
- Layer 2: Codex sandbox — filesystem forced to `read-only` via `~/.codex/config.toml`

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/collect_repo_evidence.py` | Collect deterministic repo metadata (git state, files, languages, manifests, tests, CI, TODOs, commits, churn). Creates `repo-evidence.json`, `repo-evidence.md`, `state.json`. |
| `scripts/build_review_packet.py` | Read evidence JSON + Hermes analysis text, redact secrets, build sanitized review packet JSON with SHA-256 integrity. |
| `scripts/codex_review.py` | **Default external reviewer** — runs `codex exec review --sandbox read-only`, parses output into structured findings. Supports `review`, `adversarial`, and `packet` modes. Falls back to OpenAI API via `--adapter openai`. |
| `scripts/openai_review.py` | **OpenAI API fallback** — sends packet to OpenAI Responses API with structured JSON schema. Used when Codex is unavailable or `--adapter openai`. |
| `scripts/reconcile_review.py` | Reconcile Hermes analysis (`hermes-analysis.md`) with external review findings. Classifies each finding as AGREE/PARTIAL/DISAGREE/NEW/UNVERIFIED. |
| `scripts/build_codemap_brief.py` | Build a Devin Codemap brief from reconciled review. Renders `templates/codemap-prompt.md` with actual findings, git SHA, branch. |
| `scripts/decompose_tasks.py` | Decompose reconciled findings into an acyclic task DAG with parallel groups, non-overlapping write scopes, and risk-based routing. |
| `scripts/dispatch_to_devin.py` | Dispatch task DAG to Devin CLI. Generates per-task prompt files, selects model by risk (`glm-5-2` low/med, `swe-1-7` high/critical). Dry-run mode available. |
| `scripts/update_state.py` | State machine manager with 14 ordered states + FAILED sink. Validates transitions, creates `state.json` on first call. |

## How to Run a Full Review Workflow

### Prerequisites

- Python 3.11+
- `pip install -r requirements.txt` (only `openai>=1.45.0` for fallback)
- **Codex CLI** installed via `npm install -g @openai/codex`, logged in with ChatGPT Plus
- `~/.codex/config.toml` set `sandbox = "read-only"` for Windows
- Git repository to review
- Devin CLI (for dispatch step)

### Full Workflow

```bash
# 1. Collect evidence
python scripts/collect_repo_evidence.py --repo /path/to/repo --out .hermes/reviews/<RUN_ID>

# 2. Write hermes-analysis.md (follow templates/hermes-analysis-prompt.md)

# 3. Build sanitized review packet
python scripts/build_review_packet.py \
  --evidence .hermes/reviews/<RUN_ID>/repo-evidence.json \
  --analysis .hermes/reviews/<RUN_ID>/hermes-analysis.md \
  --out .hermes/reviews/<RUN_ID>/external-review-packet.json

# 4. Get external review (Codex CLI — default)
python scripts/codex_review.py \
  --packet .hermes/reviews/<RUN_ID>/external-review-packet.json \
  --out .hermes/reviews/<RUN_ID>

# 5. Reconcile findings
python scripts/reconcile_review.py \
  --analysis .hermes/reviews/<RUN_ID>/hermes-analysis.md \
  --external .hermes/reviews/<RUN_ID>/external-review.json \
  --out .hermes/reviews/<RUN_ID>

# 6. Build Codemap brief
python scripts/build_codemap_brief.py \
  --reconciled .hermes/reviews/<RUN_ID>/reconciled-review.json \
  --repo /path/to/repo \
  --out .hermes/reviews/<RUN_ID>

# 7. Decompose into task DAG
python scripts/decompose_tasks.py \
  --reconciled .hermes/reviews/<RUN_ID>/reconciled-review.json \
  --codemap .hermes/reviews/<RUN_ID>/codemap-brief.md \
  --out .hermes/reviews/<RUN_ID>

# 8. Dispatch to Devin (dry-run first)
python scripts/dispatch_to_devin.py \
  --plan .hermes/reviews/<RUN_ID>/task-plan.json \
  --state-file .hermes/reviews/<RUN_ID>/state.json

# 8b. Dispatch for real
python scripts/dispatch_to_devin.py \
  --plan .hermes/reviews/<RUN_ID>/task-plan.json \
  --state-file .hermes/reviews/<RUN_ID>/state.json --dispatch-all
```

### Risk Routing

```
LOW/MED     → Hermes → Devin → PR → CI → CodeRabbit → Hermes
HIGH        → Hermes → Codex read-only review → Hermes reconcile → Devin
CRITICAL    → Hermes → Codex read-only review → Human → policy-gate
```

### Optional: OpenAI API Fallback

```bash
python scripts/codex_review.py \
  --adapter openai \
  --packet .hermes/reviews/<RUN_ID>/external-review-packet.json \
  --out .hermes/reviews/<RUN_ID>
```

## State Machine

```
CREATED → PREFLIGHT → EVIDENCE_COLLECTED → HERMES_ANALYSIS_DONE → PACKET_BUILT
  → EXTERNAL_REVIEW_REQUESTED → EXTERNAL_REVIEW_RECEIVED → RECONCILED
  → CODEMAP_BUILT → TASKS_DECOMPOSED → PLAN_READY_NOT_DISPATCHED
  → DISPATCHED → IN_PROGRESS → COMPLETED
```

- `FAILED` can transition from any non-terminal state
- `AWAITING_HUMAN_EXTERNAL_REVIEW` pause state (ChatGPT manual mode)

## File Structure

```
project-review-orchestrator/
├── SKILL.md                         # Skill definition
├── README.md                        # This file
├── requirements.txt                 # openai>=1.45.0 (fallback only)
├── references/
│   ├── codemap-contract.md
│   ├── governance.md
│   └── review-contract.md
├── templates/
│   ├── codemap-prompt.md
│   ├── external-review-prompt.md
│   └── hermes-analysis-prompt.md
└── scripts/
    ├── collect_repo_evidence.py     # Step 1
    ├── build_review_packet.py       # Step 3
    ├── codex_review.py              # Step 4 (default reviewer)
    ├── openai_review.py             # Step 4 (fallback reviewer)
    ├── reconcile_review.py          # Step 5
    ├── build_codemap_brief.py       # Step 6
    ├── decompose_tasks.py           # Step 7
    ├── dispatch_to_devin.py         # Step 8
    └── update_state.py              # State machine
```

## Security

- Codex runs with `sandbox = "read-only"` — FS is read-only
- Hermes adapter enforces `CODEX_ROLE=reviewer` — no write capability
- External review packet is redacted via `build_review_packet.py`
- DISAGREE findings are NEVER turned into implementation tasks
- All changes go through `PR → CI → CodeRabbit → Security → Hermes → policy-gate`