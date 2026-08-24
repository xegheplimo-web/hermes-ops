#!/usr/bin/env python3
"""
Split reconciled review findings into an acyclic execution task DAG.

Reads reconciled-review.json (findings with dispositions + required_actions)
and optionally codemap-brief.md (architecture mapping). Produces task-plan.json
with a directed acyclic graph of independently verifiable execution tasks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ops DB integration
try:
    from ops_adapter import OpsDbAdapter, OpsTask, AuditEvent, make_external_id, STATUS_QUEUED, STATUS_PLANNING, STATUS_BLOCKED
    _HAS_OPS = True
except ImportError:
    _HAS_OPS = False

# Task classifier
try:
    from task_classifier import classify, classify_findings
    _HAS_CLASSIFIER = True
except ImportError:
    _HAS_CLASSIFIER = False


SEVERITY_RISK_MAP: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}

DISPOSITIONS_INCLUDE = frozenset({"AGREE", "PARTIAL", "NEW"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _findings_from(data: dict | list) -> list[dict[str, Any]]:
    """Extract findings from reconciled review — supports both dict and bare list."""
    if isinstance(data, list):
        return data
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ValueError("reconciled review missing 'findings' array")
    return findings


def _norm_disp(disp: Any) -> str:
    return str(disp).upper().strip() if disp else ""


def _risk(severity: str) -> str:
    return SEVERITY_RISK_MAP.get(severity.lower().strip(), "medium")


def _id(finding: dict) -> str:
    """Get finding id — supports both 'id' and 'finding_id' keys."""
    return str(finding.get("id") or finding.get("finding_id", ""))


def _classify_finding(finding: dict) -> dict:
    """Classify a finding into task_type + early_risk using the task_classifier."""
    if not _HAS_CLASSIFIER:
        return {
            "task_type": "FEATURE",
            "early_risk": finding.get("risk", "medium").upper(),
            "risk_reasons": ["Classifier not available"],
        }
    title = finding.get("title", "") or finding.get("claim", "")
    description = finding.get("rationale", "") or finding.get("claim", "")
    severity = finding.get("final_severity") or finding.get("severity")
    evidence_refs = finding.get("evidence_refs", [])
    result = classify(title, description, severity, evidence_refs)
    return result


def _generate_scope(finding: dict) -> str:
    """Build a human-readable scope description from a finding."""
    fid = _id(finding)
    action = finding.get("required_action", "") or finding.get("recommendation", "")
    return (
        f"Address finding {fid}: {action[:120]}"
        if action
        else f"Address finding {fid}: {finding.get('title', 'No details')}"
    )


# Ops DB writer
def _write_to_ops_db(
    dag: dict,
    review_run_id: str,
    repo_owner: str,
    repo_name: str,
    head_sha: str,
    policy_version: str,
) -> list[int]:
    """Write the task DAG to Ops DB as authoritative runtime state."""
    try:
        adapter = OpsDbAdapter()
        adapter.connect()
    except Exception as exc:
        raise RuntimeError(f"Cannot connect to Ops DB: {exc}") from exc

    try:
        ops_tasks: list[OpsTask] = []
        for t in dag.get("tasks", []):
            external_id = make_external_id(review_run_id, t["task_id"])
            # Determine initial status: BLOCKED if dependencies exist
            initial_status = STATUS_BLOCKED if t.get("dependencies") else STATUS_QUEUED
            ops_tasks.append(OpsTask(
                external_id=external_id,
                repository_owner=repo_owner,
                repository_name=repo_name,
                head_sha=head_sha or "0" * 40,
                policy_version=policy_version,
                payload={"task_id": t["task_id"], "title": t.get("title", "")},
                status=initial_status,
                review_run_id=review_run_id,
                dag_payload=t,
            ))

        ids = adapter.bulk_create_tasks(ops_tasks)
        adapter.record_audit(AuditEvent(
            actor="hermes",
            action="dag_decomposed",
            detail={
                "review_run_id": review_run_id,
                "task_count": len(ops_tasks),
                "task_ids": ids,
            },
        ))
        return ids
    except Exception as exc:
        raise RuntimeError(f"Ops DB write failed: {exc}") from exc
    finally:
        adapter.close()


def _generate_write_scope(finding: dict) -> list[str]:
    """
    Derive a minimal set of write-scope paths from evidence references.

    In a real pipeline the evidence_refs would contain concrete paths;
    here we accept whatever the review supplies.  If none are given we
    emit a placeholder so the consumer knows the scope needs refining.
    """
    refs = finding.get("evidence_refs", [])
    paths: list[str] = []
    for r in refs:
        r = r.strip()
        if r and not r.startswith("http") and r != _id(finding):
            paths.append(r)
    if not paths:
        paths.append("<evidence-path-from-finding>")
    return paths


# ---------------------------------------------------------------------------
# DAG generation
# ---------------------------------------------------------------------------

def build_dag(reconciled: dict, codemap: str | None = None) -> dict:
    """Build a task DAG from reconciled findings."""
    findings = _findings_from(reconciled)
    codemap_context = bool(codemap)

    # -- classify findings --------------------------------------------------
    investigations: list[dict] = []
    implementations: list[dict] = []

    for f in findings:
        disp = _norm_disp(f.get("disposition"))
        if disp == "DISAGREE":
            continue
        if disp == "UNVERIFIED":
            investigations.append(f)
        elif disp in DISPOSITIONS_INCLUDE:
            implementations.append(f)
        else:
            # Unknown dispositions are treated as UNVERIFIED
            investigations.append(f)

    tasks: list[dict] = []
    id_counter = 1

    # -- investigation tasks (no deps, risk=low, no write_scope) ------------
    for f in investigations:
        cls = _classify_finding(f)
        task = {
            "task_id": f"PROJ-{id_counter:03d}",
            "title": f"[INVESTIGATE] {f.get('title', 'Unknown finding')}",
            "task_type": cls["task_type"],
            "early_risk": cls["early_risk"],
            "risk_reasons": cls["risk_reasons"],
            "objective": (
                f"Investigate and verify: {f.get('required_action') or f.get('claim', 'No details')}"
            ),
            "finding_refs": [_id(f)],
            "evidence_refs": f.get("evidence_refs", []),
            "scope": "Repository investigation — locate relevant code and verify the claim",
            "write_scope": [],
            "non_goals": (
                "Do not implement fixes or refactor. This is fact-finding only."
            ),
            "dependencies": [],
            "risk": "low",
            "executor": "devin",
            "acceptance_criteria": [
                "Claim is verified or refuted with evidence",
                "Evidence paths are documented in the investigation artifact",
            ],
            "verification": "Review investigation output for completeness and accuracy",
            "rollback": "No code changes were made — nothing to roll back",
            "retry_limit": 3,
            "parallel_group": None,
        }
        tasks.append(task)
        id_counter += 1

    # Build a lookup: finding_id -> investigation task_id
    inv_by_finding: dict[str, str] = {}
    for t in tasks:
        for ref in t["finding_refs"]:
            inv_by_finding.setdefault(ref, t["task_id"])

    # -- implementation tasks -----------------------------------------------
    for f in implementations:
        cls = _classify_finding(f)
        deps: list[str] = []
        fid = _id(f)
        # If this finding has an existing investigation task, depend on it
        if fid in inv_by_finding:
            deps.append(inv_by_finding[fid])

        risk = _risk(f.get("severity", "medium"))
        write_scope = _generate_write_scope(f)
        codemap_note = ""
        if codemap_context:
            codemap_note = " Consult the codemap brief for architecture context. "

        task = {
            "task_id": f"PROJ-{id_counter:03d}",
            "title": f"[IMPLEMENT] {f.get('title', 'Unknown finding')}",
            "task_type": cls["task_type"],
            "early_risk": cls["early_risk"],
            "risk_reasons": cls["risk_reasons"],
            "objective": (
                f"Implement: {f.get('required_action') or f.get('recommendation', 'Address the finding')}"
            ),
            "finding_refs": [fid],
            "evidence_refs": f.get("evidence_refs", []),
            "scope": _generate_scope(f),
            "write_scope": write_scope,
            "non_goals": (
                "Do not refactor unrelated code or address findings "
                "outside the assigned finding_refs."
                + codemap_note
            ),
            "dependencies": deps,
            "risk": risk,
            "executor": "devin",
            "acceptance_criteria": [
                f"Finding {fid} is addressed per its required action",
                "All existing tests pass",
                "No regressions introduced in scoped modules",
            ],
            "verification": "Automated tests + code review",
            "rollback": (
                f"Revert changes to: {', '.join(write_scope) if write_scope else 'affected files'}"
            ),
            "retry_limit": 3,
            "parallel_group": None,
        }
        tasks.append(task)
        id_counter += 1

    # -- assign parallel groups (non-overlapping write_scopes) -------------
    _assign_parallel_groups(tasks)

    # -- count types -----------------------------------------------------
    type_counts: dict[str, int] = {}
    for t in tasks:
        tt = t.get("task_type", "UNKNOWN")
        type_counts[tt] = type_counts.get(tt, 0) + 1

    return {
        "meta": {
            "run_id": reconciled.get("run_id", ""),
            "project": reconciled.get("project", ""),
            "codemap_available": codemap_context,
            "task_count": len(tasks),
            "investigation_count": len(investigations),
            "implementation_count": len(implementations),
            "types": type_counts,
        },
        "tasks": tasks,
    }


def _assign_parallel_groups(tasks: list[dict]) -> None:
    """
    Greedy assignment of parallel groups so tasks with non-overlapping
    write_scopes may run concurrently.  Investigation tasks (empty
    write_scope) are all placed in one group.  Implementation tasks
    are partitioned so no two tasks in the same group touch the same path.
    """
    # Investigations: one group
    inv_group_assigned = False
    for t in tasks:
        if t["title"].startswith("[INVESTIGATE]"):
            t["parallel_group"] = "inv"
            inv_group_assigned = True

    # Implementation tasks: greedy bin-packing by write_scope overlap
    impl = [t for t in tasks if not t["title"].startswith("[INVESTIGATE]")]
    assigned: set[str] = set()
    group_idx = 1
    for t in impl:
        if t["task_id"] in assigned:
            continue
        group = f"impl-{group_idx:02d}"
        t["parallel_group"] = group
        assigned.add(t["task_id"])
        # Accumulate scopes for this group
        group_scopes: set[str] = set()
        if t.get("write_scope"):
            for s in t["write_scope"]:
                # If the scope entry looks like a real path (has a file-like
                # extension or path separator), treat it literally; otherwise
                # consider it a label that doesn't conflict.
                if "/" in s or "\\" in s or "." in s:
                    group_scopes.add(s)

        # Try to fit other unassigned tasks into this group
        for other in impl:
            if other["task_id"] in assigned:
                continue
            other_scopes: set[str] = set()
            for s in other.get("write_scope", []):
                if "/" in s or "\\" in s or "." in s:
                    other_scopes.add(s)
            # No overlap → can join the same group
            if not (group_scopes & other_scopes):
                other["parallel_group"] = group
                assigned.add(other["task_id"])
                group_scopes |= other_scopes

        group_idx += 1

    # Any leftover tasks (safety net)
    for t in tasks:
        if t["parallel_group"] is None:
            t["parallel_group"] = f"impl-{group_idx:02d}"
            group_idx += 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_dag(tasks: list[dict]) -> None:
    """Check that the DAG is acyclic and write_scopes don't overlap in
    parallel groups."""
    task_ids = {t["task_id"] for t in tasks}
    deps: dict[str, set[str]] = {}

    for t in tasks:
        tid = t["task_id"]
        dep_set = set()
        for d in t.get("dependencies", []):
            if d not in task_ids:
                raise ValueError(f"Task {tid} depends on unknown task {d}")
            dep_set.add(d)
        deps[tid] = dep_set

    # Cycle detection via topological sort (Kahn's algorithm)
    in_degree: dict[str, int] = {tid: 0 for tid in task_ids}
    for tid, dep_set in deps.items():
        in_degree[tid] = len(dep_set)

    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    sorted_count = 0
    while queue:
        tid = queue.pop(0)
        sorted_count += 1
        for other, dep_set in deps.items():
            if tid in dep_set:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    queue.append(other)

    if sorted_count != len(tasks):
        raise ValueError(
            f"DAG contains a cycle: only {sorted_count}/{len(tasks)} "
            f"tasks resolved in topological order"
        )

    # Check parallel groups for overlapping write_scopes
    groups: dict[str, list[dict]] = {}
    for t in tasks:
        g = t.get("parallel_group", "none")
        groups.setdefault(g, []).append(t)

    for g, members in groups.items():
        if g.startswith("inv"):
            continue  # investigation tasks don't write
        scopes_seen: set[str] = set()
        for m in members:
            for s in m.get("write_scope", []):
                if "/" in s or "\\" in s or "." in s:
                    if s in scopes_seen:
                        raise ValueError(
                            f"Parallel group '{g}' has overlapping write_scope "
                            f"entry '{s}' across tasks"
                        )
                    scopes_seen.add(s)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decompose reconciled review findings into a task-plan DAG."
    )
    parser.add_argument(
        "--reconciled", required=True,
        help="Path to reconciled-review.json",
    )
    parser.add_argument(
        "--codemap",
        default=None,
        help="Optional path to codemap-brief.md for architecture context",
    )
    parser.add_argument(
        "--out", required=True,
        help="Output directory for task-plan.json",
    )
    parser.add_argument(
        "--ops-db", action="store_true", default=False,
        help="Write tasks to Ops DB as well (requires DATABASE_URL)",
    )
    parser.add_argument(
        "--review-run-id", default=None,
        help="Review run ID for Ops DB linkage",
    )
    args = parser.parse_args()

    try:
        reconciled_path = Path(args.reconciled).resolve()
        codemap_path = Path(args.codemap).resolve() if args.codemap else None
        out_dir = Path(args.out).resolve()

        reconciled = json.loads(reconciled_path.read_text(encoding="utf-8"))
        codemap = None
        if codemap_path:
            if codemap_path.is_file():
                codemap = codemap_path.read_text(encoding="utf-8")
            else:
                print(
                    json.dumps(
                        {"ok": False, "error": f"Codemap file not found: {codemap_path}"}
                    ),
                    file=sys.stderr,
                )
                return 1

        dag = build_dag(reconciled, codemap)
        validate_dag(dag["tasks"])

        out_dir.mkdir(parents=True, exist_ok=True)
        plan_file = out_dir / "task-plan.json"
        plan_file.write_text(
            json.dumps(dag, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        meta = dag["meta"]

        # -- Write to Ops DB (authoritative runtime truth) ---------------------------------
        ops_ids = None
        if args.ops_db and _HAS_OPS:
            try:
                repo_owner = reconciled.get("project", "hermes-ops")
                repo_name = reconciled.get("project", "hermes-ops")
                run_id = args.review_run_id or meta.get("run_id", "")
                # Get real commit SHA from git
                import subprocess as _sp
                _r = _sp.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
                _sha = _r.stdout.strip() if _r.returncode == 0 else "0000000000000000000000000000000000000000"
                ops_ids = _write_to_ops_db(
                    dag, run_id, repo_owner, repo_name, _sha, "0.1.0",
                )
            except Exception as exc:
                print(json.dumps({"warning": f"Ops DB write failed (non-fatal): {exc}"}), file=sys.stderr)

        # -- Output ------------------------------------------------------------------------
        print(
            json.dumps(
                {
                    "ok": True,
                    "task_count": meta["task_count"],
                    "investigation_count": meta["investigation_count"],
                    "implementation_count": meta["implementation_count"],
                    "plan_file": str(plan_file),
                    "ops_db_count": len(ops_ids) if ops_ids else 0,
                    "ops_db_ids": ops_ids or [],
                },
                indent=2,
            )
        )
        return 0

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())