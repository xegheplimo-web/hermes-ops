---
name: project-agent-bootstrapper
description: Bootstrap governed project skills and agent permissions.
version: 0.1.0
author: Project Owner, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [bootstrap, governance, agents, skills, review]
    related_skills: [project-review-orchestrator]
    requires_toolsets: [terminal]
---

# Project Agent Bootstrapper

## When to Use

Use this skill when the user starts a coding project or coding session and asks Hermes to:

- verify that the repository is ready for governed implementation;
- ensure the canonical project review/execution skill exists;
- collect minimal repository provenance before coding;
- create or refresh the agent permission matrix;
- route work to the correct actors in the architecture:
  - Hermes;
  - AgentMemory;
  - Ops DB;
  - external reviewer;
  - Devin Codemap / Ask Devin;
  - DevinAdapter;
  - Devin;
  - GitHub CI / Security / CodeRabbit;
  - hermes/policy-gate;
  - Human authority.

For governance-sensitive runs, prefer explicit invocation:

`/project-agent-bootstrapper Prepare this repository and route agents.`

Do not rely only on automatic semantic skill matching.

Do not use this skill for:

- a single trivial typo fix;
- bypassing GitHub policy gates;
- allowing external reviewers to modify the repository;
- sending repository secrets, browser credentials, or tokens externally;
- using AgentMemory as authoritative runtime queue state.

## Prerequisites

Required:

- A Git repository.
- Python 3.11 or later.
- Git available to `terminal`.
- Hermes project tools for file access.

Optional:

- AgentMemory MCP for historical project context.
- Ops DB / queue integration for task execution.
- DevinAdapter for implementation dispatch.
- Devin Desktop or Ask Devin for architecture exploration.
- `OPENAI_API_KEY` for later independent external review.
- Devin Codemaps for visual architecture mapping.

## Authority Model

This skill enforces the following authority model:

```text
USER
  ↓
HERMES
Brain / Policy / Orchestrator
  ↓
┌──────────────────────┬──────────────────────┬──────────────────────┐
│ AgentMemory          │ Ops DB               │ Capability Plane     │
│ cognitive memory     │ runtime truth        │ browser/design/etc.  │
└──────────────────────┴──────────────────────┴──────────────────────┘
  ↓
Project Review Skill / Evidence Collector
  ↓
Hermes Analysis
  ↓
Redaction Gate
  ↓
External Review Adapter
  ↓
Hermes Reconcile
  ↓
Codemap Brief
  ↓
Devin Codemap / Ask Devin
  ↓
Hermes Task DAG
  ↓
Ops DB
  ↓
DevinAdapter
  ↓
Devin
  ↓
PR
  ↓
CI + CodeRabbit + Security
  ↓
Hermes Risk Reconcile
  ↓
hermes/policy-gate
  ↓
PASS / REPAIR
```

Hard rules:

1. Hermes is the only orchestrator.
2. External reviewers are critics, not implementers.
3. Devin Codemap is navigation/context, not proof.
4. AgentMemory is historical context, not runtime truth.
5. Ops DB owns execution state.
6. Devin implements only through DevinAdapter.
7. GitHub PR/CI/Security evidence is mandatory.
8. `hermes/policy-gate` is authoritative for enforcement.
9. Human approval is required for designated critical actions.
10. No task may be created without evidence references.

## How to Run

From the repository root:

```text
/project-agent-bootstrapper Prepare this repository and route agents.
```

Then follow the Procedure below.

Never substitute guessed values for repository facts.

## Quick Reference

Bootstrap the repository:

```text
terminal(command="python skills/software-development/project-agent-bootstrapper/scripts/bootstrap_project_skill.py --repo . --out .hermes/bootstrap --dry-run")
```

Generate a canonical skill draft if missing:

```text
terminal(command="python skills/software-development/project-agent-bootstrapper/scripts/bootstrap_project_skill.py --repo . --out .hermes/bootstrap --generate-draft")
```

Install the canonical skill only if it is missing and write approval is granted:

```text
terminal(command="python skills/software-development/project-agent-bootstrapper/scripts/bootstrap_project_skill.py --repo . --out .hermes/bootstrap --install-if-missing --confirm-write")
```

Then, for a full governed audit:

```text
/project-review-orchestrator Audit this project and prepare execution.
```

## Procedure

### 1. Preflight repository state

Resolve the actual repository root.

Record:

- repository root;
- project name;
- branch;
- exact commit SHA;
- dirty/clean state;
- changed entry count;
- run timestamp;
- RUN_ID;
- user objective.

RUN_ID should use timestamp plus short SHA:

```text
2026-08-20T143522Z-a81d9c4
```

If the repository is not a Git repository, stop.

If the worktree is dirty:

1. record that fact;
2. record changed paths where possible;
3. never claim findings correspond only to HEAD;
4. label the run `dirty_snapshot=true`.

Create:

```text
.hermes/bootstrap/<RUN_ID>/state.json
```

Completion criterion:

- repository root is verified;
- commit SHA exists;
- dirty status is explicitly recorded;
- RUN_ID exists.

### 2. Discover canonical project skill

Check whether the canonical governed review skill exists:

```text
skills/software-development/project-review-orchestrator/SKILL.md
```

If it exists:

- validate that it has frontmatter;
- validate that `name` is lowercase/hyphen;
- validate that `description` is one sentence and not too long;
- record the skill path as `canonical_skill_ref`.

If it does not exist:

- generate a draft under:

```text
.hermes/generated/skills/software-development/project-review-orchestrator/
```

Do not silently overwrite production skills.

If the user explicitly approves installation, and the target skill is missing, copy the generated draft into:

```text
skills/software-development/project-review-orchestrator/
```

Any modification to production skill files must go through PR/review like code.

Completion criterion:

- canonical skill exists;
- or a generated draft exists;
- or the missing skill is explicitly reported.

### 3. Create agent permission matrix

Create or refresh:

```text
.hermes/governance/agent-roles.json
```

If the file already exists, do not overwrite it silently. Create a proposed artifact under the current RUN_ID instead.

The permission matrix must define:

- who may decide;
- who may review;
- who may implement;
- who may queue tasks;
- who may approve merges;
- who may access secrets;
- who may send data externally;
- who may stop the pipeline.

The matrix must include at least these actors:

- USER;
- HERMES;
- AgentMemory;
- Ops DB;
- Evidence Collector;
- Redaction Gate;
- External Review Adapter;
- OpenAI API reviewer;
- ChatGPT human manual mode;
- Devin Codemap / Ask Devin;
- DevinAdapter;
- Devin;
- GitHub;
- CI;
- Security scanners;
- CodeRabbit;
- hermes/policy-gate;
- Human authority.

Completion criterion:

- agent role artifact exists;
- each actor has allowed actions;
- each actor has forbidden actions;
- no external reviewer has write authority;
- no memory system owns runtime task state.

### 4. Recall historical context

If AgentMemory tools are available, search for:

- architecture decisions;
- previous incidents;
- rejected approaches;
- recurring bugs;
- user constraints;
- prior review outcomes.

Treat retrieved memories as historical context only.

Repository evidence at HEAD outranks stale memory.

Do not write queue state to AgentMemory.

Completion criterion:

- relevant memories are recorded as references;
- or the run explicitly records that AgentMemory was unavailable or no relevant memory was found.

### 5. Collect minimal deterministic evidence

Before coding, collect at least:

- branch;
- commit SHA;
- dirty state;
- tracked file count;
- language distribution;
- manifests;
- test file count;
- CI files;
- security-sensitive path hints;
- recent commits;
- high-churn files;
- TODO/FIXME counts.

If the canonical `project-review-orchestrator` scripts exist, prefer:

```text
skills/software-development/project-review-orchestrator/scripts/collect_repo_evidence.py
```

If they do not exist, the bootstrap script may collect a minimal evidence summary.

Do not invent file counts, languages, test counts, branch state, commit history, CI files, or churn statistics.

Completion criterion:

- deterministic evidence artifact exists;
- evidence includes exact Git commit;
- no guessed repository statistic is presented as fact.

### 6. Decide routing mode

Based on evidence and user objective, choose one route.

#### Route A: Trivial low-risk change

Use only when all are true:

- change scope is tiny;
- no security-sensitive path is involved;
- no architecture change is involved;
- no data migration is involved;
- no external system contract changes;
- tests or verification are straightforward.

Then:

1. create a branch/worktree;
2. implement through DevinAdapter if coding is delegated;
3. require PR;
4. require CI;
5. require normal policy-gate.

Do not skip review gates.

#### Route B: Standard governed implementation

Use for normal feature work.

Then:

1. invoke `/project-review-orchestrator`;
2. collect full repository evidence;
3. perform Hermes first-pass analysis;
4. obtain external review if policy requires it;
5. reconcile findings;
6. create Codemap brief;
7. generate task DAG;
8. write tasks to Ops DB;
9. dispatch through DevinAdapter;
10. enforce PR/CI/policy-gate.

#### Route C: High-risk or critical change

Use when evidence touches:

- authentication;
- authorization;
- secrets;
- payments;
- billing;
- migrations;
- production deployment;
- data integrity;
- browser credential stores;
- external API authority;
- policy-gate logic;
- orchestrator logic.

Then:

1. require full `/project-review-orchestrator`;
2. require independent external review;
3. require stricter reconciliation;
4. require Codemap or Ask Devin architecture context;
5. require human approval for critical actions;
6. never allow direct implementation before reconciliation.

Completion criterion:

- a routing decision exists;
- routing reason is recorded;
- high-risk and critical routes require stronger review.

### 7. Produce bootstrap report

Create:

```text
.hermes/bootstrap/<RUN_ID>/bootstrap-report.md
```

The report must contain:

- snapshot;
- canonical skill status;
- agent role status;
- evidence summary;
- routing decision;
- required next command;
- missing prerequisites;
- warnings;
- dirty-state notice if present.

Completion criterion:

- report exists;
- report references exact commit SHA;
- report names the next required step.

### 8. Hand off to the canonical workflow

If the task is not trivial, Hermes should next invoke:

```text
/project-review-orchestrator Audit this project and prepare execution.
```

This bootstrapper does not replace the full review orchestrator.

It only prepares governance and routing.

Completion criterion:

- bootstrap completes;
- canonical workflow is recommended when required;
- no implementation task is dispatched directly by this skill.

## Pitfalls

### Skill becomes orchestrator

Wrong:

```text
Bootstrap skill -> tasks -> Devin
```

Correct:

```text
Bootstrap skill -> Hermes governance -> canonical review workflow -> Ops DB -> DevinAdapter -> Devin
```

### Auto-loading becomes auto-execution

Skill discovery is acceptable.

Silent self-execution is not.

Critical workflows should be explicitly invoked.

### AgentMemory becomes runtime queue

AgentMemory may store durable lessons.

It must not store authoritative task state.

### External reviewer becomes implementer

External reviewers may produce findings.

They must not modify repository files.

### Codemap becomes proof

Codemap is useful for navigation and architecture context.

Important claims must still be verified against source and tests.

### Dirty repository is treated as clean

If the worktree is dirty, all conclusions must be labeled as referring to a dirty snapshot.

### `--out` is relative when run from a subdirectory

`--repo .` resolves the true repo root via `git rev-parse --show-toplevel`, but a
relative `--out .hermes/bootstrap` resolves against the **current working directory**,
not the repo root. Running from `packages/foo/` writes artifacts to
`packages/foo/.hermes/bootstrap/`. Always invoke from the repo root, or pass an
absolute `--out`.

### `--install-if-missing` needs `--generate-draft`

Install is nested inside the draft-generation branch. `--install-if-missing
--confirm-write` alone is a no-op; the script now emits a WARNING action instead
of silently doing nothing.

## Verification

Run the functional suite (real git repos in a temp dir, no mocks):

```text
terminal(command="python skills/software-development/project-agent-bootstrapper/tests/test_bootstrap_functional.py")
```

It exits non-zero on any failure and covers: non-git rejection, missing/valid
canonical skill, permission-matrix completeness (17 actors, no external write
authority, AgentMemory not runtime truth, Devin gated by DevinAdapter), no-silent-
overwrite on re-run, dirty detection, draft/install approval gating, RUN_ID format,
report provenance, and `--dry-run` being side-effect free.

A successful bootstrap run proves all of the following:

1. Repository is a real Git repository.
2. Exact commit SHA is recorded.
3. Dirty state is explicit.
4. RUN_ID exists.
5. Canonical skill exists or a generated draft exists.
6. Agent permission matrix exists.
7. No external reviewer is granted write authority.
8. AgentMemory is not granted runtime queue authority.
9. Ops DB is marked as execution truth when available.
10. Devin is only allowed through DevinAdapter.
11. GitHub PR/CI/security/policy-gate remain mandatory.
12. A routing decision exists.
13. The next command is explicit.
14. No implementation was dispatched directly by this skill.
