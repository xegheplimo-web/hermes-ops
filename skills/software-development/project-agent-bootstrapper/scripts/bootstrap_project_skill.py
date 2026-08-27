#!/usr/bin/env python3
"""
Bootstrap a governed Hermes project workspace.

This script:
- verifies the Git repository;
- records branch, commit SHA, dirty state, and RUN_ID;
- creates bootstrap artifacts under .hermes/bootstrap/<RUN_ID>;
- generates an agent permission matrix;
- checks for the canonical project-review-orchestrator skill;
- optionally generates a draft canonical skill if missing;
- optionally installs the draft only if missing and write approval is given.

It does not dispatch tasks.
It does not modify repository code.
It does not bypass policy gates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path


class BootstrapError(RuntimeError):
    pass


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if check and proc.returncode != 0:
        raise BootstrapError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip()}"
        )

    return proc.stdout


def resolve_repo(path: Path) -> Path:
    root = run_git(path, "rev-parse", "--show-toplevel").strip()
    if not root:
        raise BootstrapError(f"Not a Git repository: {path}")
    return Path(root).resolve()


def git_branch(repo: Path) -> str:
    return run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def git_commit(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD").strip()


def git_status(repo: Path) -> tuple[bool, int, list[str]]:
    raw = run_git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    lines = [line for line in raw.splitlines() if line.strip()]
    return bool(lines), len(lines), lines[:100]


def make_run_id(commit_sha: str) -> str:
    timestamp = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
        .replace(":", "")
        .replace("-", "")
    )
    return f"{timestamp}-{commit_sha[:7]}"


def canonical_skill_path(repo: Path) -> Path:
    return (
        repo
        / "skills"
        / "software-development"
        / "project-review-orchestrator"
        / "SKILL.md"
    )


def generated_skill_dir(out_root: Path) -> Path:
    return (
        out_root
        / "generated"
        / "skills"
        / "software-development"
        / "project-review-orchestrator"
    )


def agent_roles(commit_sha: str, branch: str, dirty: bool) -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "repository_snapshot": {
            "branch": branch,
            "commit_sha": commit_sha,
            "dirty": dirty,
        },
        "authority_model": {
            "orchestrator": "HERMES",
            "cognitive_memory": "AgentMemory",
            "runtime_execution_state": "OpsDB",
            "implementation_gateway": "DevinAdapter",
            "implementation_executor": "Devin",
            "secondary_implementation_executor": "OpenCode",
            "readonly_reviewer": "CodexReviewer",
            "change_control_plane": "GitHub",
            "deterministic_evidence": [
                "CI",
                "SecurityScanners",
                "Tests",
            ],
            "review_signals": [
                "OpenAIReviewAdapter",
                "CodexReviewer",
                "CodeRabbit",
                "HumanReviewer",
            ],
            "enforcement": "hermes/policy-gate",
            "critical_authority": "Human",
        },
        "actors": [
            {
                "name": "USER",
                "role": "principal",
                "allowed": [
                    "request work",
                    "approve critical actions when required",
                    "inspect artifacts",
                ],
                "forbidden": [
                    "bypass policy-gate through informal instruction",
                ],
            },
            {
                "name": "HERMES",
                "role": "orchestrator",
                "allowed": [
                    "plan",
                    "reconcile evidence",
                    "route tasks",
                    "invoke skills",
                    "prepare review packets",
                    "coordinate agents",
                ],
                "forbidden": [
                    "bypass policy-gate",
                    "treat AgentMemory as runtime truth",
                    "let external reviewer implement code",
                ],
            },
            {
                "name": "AgentMemory",
                "role": "cognitive_memory",
                "allowed": [
                    "provide historical decisions",
                    "provide lessons",
                    "provide incidents",
                    "store verified durable lessons",
                ],
                "forbidden": [
                    "own task queue",
                    "own lease state",
                    "override repository evidence",
                ],
            },
            {
                "name": "OpsDB",
                "role": "runtime_truth",
                "allowed": [
                    "store task state",
                    "store queue state",
                    "store leases",
                    "store attempts",
                ],
                "forbidden": [
                    "replace Git evidence",
                    "replace policy-gate",
                ],
            },
            {
                "name": "EvidenceCollector",
                "role": "deterministic_evidence",
                "allowed": [
                    "read repository metadata",
                    "count files deterministically",
                    "collect Git state",
                    "collect manifests/tests/CI stats",
                ],
                "forbidden": [
                    "modify repository",
                    "export secrets",
                ],
            },
            {
                "name": "RedactionGate",
                "role": "security_boundary",
                "allowed": [
                    "block secret export",
                    "redact sensitive patterns",
                    "require manual review when uncertain",
                ],
                "forbidden": [
                    "send raw secrets externally",
                ],
            },
            {
                "name": "OpenAIReviewAdapter",
                "role": "external_review_signal",
                "allowed": [
                    "receive sanitized packet",
                    "produce structured critique",
                ],
                "forbidden": [
                    "modify repository",
                    "create tasks directly",
                    "approve merges",
                    "access secrets",
                ],
            },
            {
                "name": "ChatGPTHumanMode",
                "role": "manual_external_review",
                "allowed": [
                    "human-visible critique",
                    "manual copy/export by user",
                ],
                "forbidden": [
                    "programmatic scraping of ChatGPT consumer output",
                ],
            },
            {
                "name": "CodexReviewer",
                "role": "readonly_independent_reviewer",
                "allowed": [
                    "read repository at the reviewed SHA",
                    "produce structured findings with severity and confidence",
                    "challenge Hermes assumptions",
                    "challenge executor claims",
                ],
                "forbidden": [
                    "edit files",
                    "commit",
                    "push",
                    "merge",
                    "implement fixes",
                    "modify tests",
                    "create authoritative tasks",
                    "approve policy",
                ],
            },
            {
                "name": "DevinCodemap",
                "role": "architecture_navigation",
                "allowed": [
                    "map code flows",
                    "identify candidate files",
                    "support planning",
                ],
                "forbidden": [
                    "act as final proof",
                    "modify repository",
                ],
            },
            {
                "name": "DevinAdapter",
                "role": "implementation_gateway",
                "allowed": [
                    "dispatch approved tasks to Devin",
                    "track Devin sessions",
                ],
                "forbidden": [
                    "bypass Ops DB",
                    "bypass policy-gate",
                ],
            },
            {
                "name": "Devin",
                "role": "implementation_executor",
                "allowed": [
                    "implement assigned tasks",
                    "create branch/worktree",
                    "open PR",
                ],
                "forbidden": [
                    "merge without gates",
                    "change policy",
                    "bypass DevinAdapter",
                ],
            },
            {
                "name": "OpenCode",
                "role": "secondary_implementation_repair_executor",
                "allowed": [
                    "implement a bounded repair task assigned by Hermes",
                    "refactor within the assigned write scope",
                    "perform a second implementation pass",
                    "act as fallback implementer when Devin is unavailable",
                    "create branch/worktree",
                    "open PR",
                ],
                "forbidden": [
                    "replace Devin as primary executor without a recorded reason",
                    "decide architecture",
                    "merge without gates",
                    "approve its own task",
                    "bypass Ops DB",
                    "change policy",
                    "write outside the assigned scope",
                    "treat a review finding as fact before Hermes reconciles it",
                ],
            },
            {
                "name": "GitHub",
                "role": "change_control_plane",
                "allowed": [
                    "host branches",
                    "host PRs",
                    "record review history",
                ],
                "forbidden": [
                    "be bypassed by direct repo mutation",
                ],
            },
            {
                "name": "CI",
                "role": "deterministic_evidence",
                "allowed": [
                    "run tests",
                    "produce build/test evidence",
                ],
                "forbidden": [
                    "approve policy exceptions",
                ],
            },
            {
                "name": "SecurityScanners",
                "role": "deterministic_evidence",
                "allowed": [
                    "produce security findings",
                ],
                "forbidden": [
                    "expose secrets in reports",
                ],
            },
            {
                "name": "CodeRabbit",
                "role": "review_signal",
                "allowed": [
                    "comment on PR",
                    "suggest changes",
                ],
                "forbidden": [
                    "merge autonomously",
                    "override policy-gate",
                ],
            },
            {
                "name": "hermes/policy-gate",
                "role": "enforcement",
                "allowed": [
                    "pass or fail changes",
                    "require repair",
                    "require human approval",
                ],
                "forbidden": [
                    "be bypassed by any agent",
                ],
            },
            {
                "name": "Human",
                "role": "critical_authority",
                "allowed": [
                    "approve critical changes",
                    "resolve conflicts",
                    "grant exceptions explicitly",
                ],
                "forbidden": [
                    "be silently bypassed for critical actions",
                ],
            },
        ],
        "routing_rules": {
            "trivial_low_risk": [
                "small scope",
                "no security-sensitive path",
                "no architecture change",
                "no data migration",
                "clear verification",
            ],
            "standard_governed": [
                "normal feature work",
                "bounded risk",
                "requires PR and CI",
            ],
            "high_risk_or_critical": [
                "auth",
                "secrets",
                "billing",
                "payment",
                "migration",
                "production deploy",
                "data integrity",
                "policy-gate",
                "orchestrator logic",
            ],
        },
        "hard_rules": [
            "No execution task without evidence_refs.",
            "No implementation before external review when policy requires it.",
            "No external reviewer directly changes repository.",
            "Codex is READ-ONLY and may not modify repository files.",
            "Devin is the primary implementation executor; OpenCode is secondary.",
            "OpenCode acts only on a bounded repair task assigned by Hermes.",
            "Choosing OpenCode as primary requires a recorded executor_override_reason.",
            "A reviewer may never become an executor.",
            "No AgentMemory record is authoritative runtime state.",
            "No task is marked DONE without verification evidence.",
            "No HIGH task passes without required independent review.",
            "No CRITICAL task passes without human approval.",
            "No PR may bypass CI/security requirements.",
            "No external packet may knowingly contain secrets.",
            "Review commit SHA must match code snapshot or drift must be reconciled.",
            "No unbounded retry loops; exhausted attempts escalate instead of retrying.",
        ],
    }


CANONICAL_SKILL_DRAFT = """---
name: project-review-orchestrator
description: Audit a repo, obtain critique, then plan execution.
version: 0.1.0
author: Project Owner, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [repository, review, planning, codemap]
    related_skills: [project-agent-bootstrapper]
    requires_toolsets: [terminal]
---

# Project Review Orchestrator

## When to Use

Use this skill when Hermes must audit a real software project before implementation.

Use it to:

- collect deterministic repository evidence;
- perform Hermes first-pass analysis;
- obtain independent external review;
- reconcile external findings;
- prepare a Devin Codemap brief;
- split accepted findings into execution tasks.

Do not use it for:

- trivial one-line edits;
- bypassing GitHub policy gates;
- sending secrets externally;
- allowing external reviewers to modify code.

For governance-sensitive runs, prefer explicit invocation:

`/project-review-orchestrator Audit this project and prepare execution.`

## Core Procedure

1. Preflight repository state.
2. Recall AgentMemory as historical context only.
3. Collect deterministic repository evidence.
4. Perform Hermes independent analysis.
5. Build sanitized review packet.
6. Obtain independent external review.
7. Reconcile Hermes and external findings.
8. Build Devin Codemap brief.
9. Generate or request Codemap / Ask Devin context.
10. Decompose accepted findings into task DAG.
11. Write tasks to Ops DB.
12. Dispatch coding through DevinAdapter.
13. Enforce PR / CI / Security / CodeRabbit / policy-gate.
14. Promote only verified durable lessons to AgentMemory.

## Hard Rules

- Hermes is the orchestrator.
- External reviewers are critics, not implementers.
- AgentMemory is cognitive context, not runtime truth.
- Ops DB owns execution state.
- Devin implements through DevinAdapter.
- All changes require PR and normal gates.
- No secret may be sent externally.
- No task may exist without evidence references.
"""


def validate_skill_file(path: Path) -> dict:
    result = {
        "exists": path.exists(),
        "path": str(path),
        "valid_frontmatter": False,
        "name": None,
        "description": None,
        "issues": [],
    }

    if not path.exists():
        result["issues"].append("SKILL.md is missing.")
        return result

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        result["issues"].append(f"Unable to read SKILL.md: {exc}")
        return result

    if not text.startswith("---"):
        result["issues"].append("Missing YAML frontmatter delimiter.")
        return result

    parts = text.split("---", 2)
    if len(parts) < 3:
        result["issues"].append("Frontmatter is not closed.")
        return result

    frontmatter = parts[1]

    for line in frontmatter.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            result["name"] = line.split(":", 1)[1].strip()
        if line.startswith("description:"):
            result["description"] = line.split(":", 1)[1].strip()

    if not result["name"]:
        result["issues"].append("Missing name field.")
    elif result["name"] != result["name"].lower():
        result["issues"].append("name should be lowercase.")

    if not result["description"]:
        result["issues"].append("Missing description field.")
    else:
        if len(result["description"]) > 60:
            result["issues"].append("description should be 60 characters or fewer.")
        if not result["description"].endswith("."):
            result["issues"].append("description should end with a period.")

    if not result["issues"]:
        result["valid_frontmatter"] = True

    return result


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_report(state: dict, skill_check: dict, actions: list[str]) -> str:
    repo = state["repository"]
    action_lines = "\n".join(f"- {action}" for action in actions)

    return f"""# Hermes Bootstrap Report

## Snapshot

- Project: {repo['root_name']}
- Branch: `{repo['branch']}`
- Commit: `{repo['commit_sha']}`
- Dirty: `{repo['dirty']}`
- Changed entries: `{repo['changed_entry_count']}`
- Generated: `{state['generated_at']}`
- RUN_ID: `{state['run_id']}`

## Canonical Skill

- Path: `{skill_check['path']}`
- Exists: `{skill_check['exists']}`
- Valid: `{skill_check['valid_frontmatter']}`

### Skill Issues

{chr(10).join(f"- {issue}" for issue in skill_check["issues"]) or "- None"}

## Actions Taken

{action_lines}

## Next Step

If the task is non-trivial, run:

```text
/project-review-orchestrator Audit this project and prepare execution.
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap governed Hermes project workspace."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without creating or modifying any file.",
    )
    parser.add_argument(
        "--generate-draft",
        action="store_true",
        help="Generate canonical skill draft if missing.",
    )
    parser.add_argument(
        "--install-if-missing",
        action="store_true",
        help="Install generated canonical skill if target is missing.",
    )
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="Required together with --install-if-missing.",
    )
    args = parser.parse_args()

    try:
        repo = resolve_repo(Path(args.repo).resolve())
        out_root = Path(args.out).resolve()

        branch = git_branch(repo)
        commit_sha = git_commit(repo)
        dirty, changed_count, changed_entries = git_status(repo)
        run_id = make_run_id(commit_sha)

        run_dir = out_root / run_id
        if not args.dry_run:
            run_dir.mkdir(parents=True, exist_ok=True)

        generated_at = (
            dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )

        state = {
            "schema_version": "1.0",
            "run_id": run_id,
            "generated_at": generated_at,
            "repository": {
                "root": str(repo),
                "root_name": repo.name,
                "branch": branch,
                "commit_sha": commit_sha,
                "dirty": dirty,
                "changed_entry_count": changed_count,
                "changed_entries_sample": changed_entries,
            },
            "workflow": {
                "skill": "project-agent-bootstrapper",
                "purpose": "prepare governed coding workflow",
                "next_skill": "project-review-orchestrator",
            },
        }

        actions = []
        dry_run = args.dry_run

        if dry_run:
            actions.append(f"DRY-RUN: would write {run_dir / 'state.json'}")
        else:
            write_json(run_dir / "state.json", state)
            actions.append(f"Wrote {run_dir / 'state.json'}")

        roles = agent_roles(commit_sha, branch, dirty)
        governance_dir = repo / ".hermes" / "governance"
        roles_path = governance_dir / "agent-roles.json"

        if roles_path.exists():
            proposed_roles_path = run_dir / "agent-roles.proposed.json"
            if dry_run:
                actions.append(
                    f"DRY-RUN: existing agent-roles.json found; would write proposed copy to {proposed_roles_path}"
                )
            else:
                write_json(proposed_roles_path, roles)
                actions.append(
                    f"Existing agent-roles.json found; proposed copy written to {proposed_roles_path}"
                )
        else:
            if dry_run:
                actions.append(f"DRY-RUN: would write {roles_path}")
            else:
                write_json(roles_path, roles)
                actions.append(f"Wrote {roles_path}")

        target_skill = canonical_skill_path(repo)
        skill_check = validate_skill_file(target_skill)

        generated_skill_file = None

        if args.install_if_missing and not args.generate_draft:
            actions.append(
                "WARNING: --install-if-missing requires --generate-draft; no install was attempted."
            )

        if not target_skill.exists() and args.generate_draft:
            generated_dir = generated_skill_dir(run_dir)
            generated_skill_file = generated_dir / "SKILL.md"
            if dry_run:
                actions.append(
                    f"DRY-RUN: would generate canonical skill draft at {generated_skill_file}"
                )
            else:
                write_text(generated_skill_file, CANONICAL_SKILL_DRAFT)
                actions.append(f"Generated canonical skill draft at {generated_skill_file}")

            if args.install_if_missing:
                if not args.confirm_write:
                    actions.append(
                        "Install requested but --confirm-write missing; skipped install."
                    )
                elif target_skill.exists():
                    actions.append(
                        "Canonical skill already exists; refused to overwrite."
                    )
                elif dry_run:
                    actions.append(
                        f"DRY-RUN: would install canonical skill to {target_skill}"
                    )
                else:
                    target_skill.parent.mkdir(parents=True, exist_ok=True)
                    target_skill.write_text(
                        CANONICAL_SKILL_DRAFT,
                        encoding="utf-8",
                    )
                    skill_check = validate_skill_file(target_skill)
                    actions.append(f"Installed canonical skill to {target_skill}")
            else:
                actions.append(
                    "Install not requested; draft remains outside production skill path."
                )

        report = render_report(state, skill_check, actions)
        report_path = run_dir / "bootstrap-report.md"
        if dry_run:
            actions.append(f"DRY-RUN: would write {report_path}")
        else:
            write_text(report_path, report)
            actions.append(f"Wrote {report_path}")

        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": dry_run,
                    "run_id": run_id,
                    "repo": str(repo),
                    "branch": branch,
                    "commit": commit_sha,
                    "dirty": dirty,
                    "state": str(run_dir / "state.json"),
                    "roles": str(roles_path),
                    "skill_check": skill_check,
                    "report": str(report_path),
                    "generated_skill_draft": str(generated_skill_file)
                    if generated_skill_file
                    else None,
                    "actions": actions,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        return 0

    except (BootstrapError, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                indent=2,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
