# Hermes Bootstrap Report Template

This template is rendered by `bootstrap_project_skill.py` with the following placeholders:

- `{{PROJECT}}` — repository root name
- `{{BRANCH}}` — current Git branch
- `{{COMMIT_SHA}}` — exact HEAD commit SHA
- `{{DIRTY}}` — `true` if worktree has uncommitted changes
- `{{CHANGED_COUNT}}` — number of changed entries
- `{{RUN_ID}}` — timestamp + short SHA
- `{{GENERATED_AT}}` — ISO timestamp of the run

## Sections

1. **Snapshot** — repository state at bootstrap time.
2. **Canonical Skill** — whether `project-review-orchestrator` exists and passes validation.
3. **Actions Taken** — what the bootstrap script wrote.
4. **Next Step** — explicit command to run next.

## Usage

The script renders this automatically. To regenerate manually:

```bash
python skills/software-development/project-agent-bootstrapper/scripts/bootstrap_project_skill.py \
  --repo . \
  --out .hermes/bootstrap \
  --generate-draft
```

## Dirty-State Notice

If `{{DIRTY}}` is `true`, the report MUST include a prominent notice:

> **WARNING**: This is a dirty snapshot. Findings correspond to the working
> tree state, not to HEAD. Commit or stash before running the full review.
