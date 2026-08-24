#!/usr/bin/env python3
"""
Dispatch tasks from Ops DB (authoritative) or task-plan.json (fallback) to Devin CLI.

Authoritative path:   --ops-db --review-run-id <id>
                      Claims tasks via SKIP LOCKED from Ops DB,
                      transitions to DISPATCHED, records audit events.
Fallback path:       --plan task-plan.json
                      Reads file directly (dev/test, no Ops DB).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from ops_adapter import (
        AuditEvent, OpsDbAdapter,
        STATUS_DISPATCHED, STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING,
        make_external_id,
    )
    _HAS_OPS = True
except ImportError:
    _HAS_OPS = False


RISK_MODEL_MAP: dict[str, str] = {
    "low": "glm-5-2",
    "medium": "glm-5-2",
    "high": "swe-1-7",
    "critical": "swe-1-7",
}


def _model(risk: str, override: str | None) -> str:
    if override:
        return override
    return RISK_MODEL_MAP.get(risk.lower(), "glm-5-2")


def _find_devin_binary(devin_binary: str) -> str | None:
    """Resolve the Devin CLI binary, with default-install fallbacks on Windows."""
    p = Path(devin_binary)
    if p.is_file():
        return str(p.resolve())
    from_path = shutil.which(devin_binary)
    if from_path:
        return from_path
    candidates = [
        Path.home() / "AppData" / "Local" / "Programs" / "Devin" / "resources" / "app" / "extensions" / "windsurf" / "devin" / "bin" / "devin.exe",
        Path(r"C:\\Users\\atton\\AppData\\Local\\Programs\\Devin\\resources\\app\\extensions\\windsurf\\devin\\bin\\devin.exe"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())
    return None


def _build_prompt(task: dict) -> str:
    """Build a Devin prompt from a task dict (DAG node)."""
    tid = task.get("task_id", "?")
    title = task.get("title", "")
    objective = task.get("objective", "")
    scope = task.get("scope", "")
    non_goals = task.get("non_goals", "Do not modify unrelated code.")
    acceptance = task.get("acceptance_criteria", ["All tests pass"])
    write_scope = task.get("write_scope", [])
    deps = task.get("dependencies", [])
    finding_refs = task.get("finding_refs", [])

    criteria = "\n".join(f"- {c}" for c in acceptance)
    ws = f"\nWrite scope: {', '.join(write_scope)}" if write_scope else ""
    deps_str = f"\nDepends on: {', '.join(deps)}" if deps else ""
    refs_str = f"\nFinding refs: {', '.join(finding_refs)}" if finding_refs else ""

    return f"""You are working on task {tid}.

TITLE: {title}
OBJECTIVE: {objective}
SCOPE: {scope}{ws}{deps_str}{refs_str}
NON-GOALS: {non_goals}

ACCEPTANCE CRITERIA:
{criteria}

INSTRUCTIONS:
- WORK ON A BRANCH. PRODUCE A PR.
- DO NOT MODIFY UNRELATED CODE.
- Follow test-driven-development when applicable.
- Run verification-before-completion before reporting done.
"""


def _dispatch_one(task: dict, out_dir: Path, devin_bin: str,
                  model: str, pm: str, dispatch_all: bool) -> dict:
    tid = task.get("task_id", "?")
    prompt = _build_prompt(task)
    prompt_dir = out_dir / "devin"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"devin-task-{tid}.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    entry: dict = {"task_id": tid, "model": model, "prompt_file": str(prompt_file)}
    if dispatch_all:
        resolved_bin = _find_devin_binary(devin_bin)
        if not resolved_bin:
            entry["command"] = f"{devin_bin} ..."
            entry["dispatched"] = False
            entry["error"] = f"Devin binary not found: {devin_bin}"
            return entry

        cmd = [
            resolved_bin,
            "--respect-workspace-trust", "false",
            "--model", model,
            "--permission-mode", pm,
            "-p",
            "--prompt-file", str(prompt_file),
        ]
        entry["command"] = " ".join(cmd)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            entry["devin_exit_code"] = result.returncode
            entry["devin_stdout"] = result.stdout[:2000] if result.stdout else ""
            entry["devin_stderr"] = result.stderr[:2000] if result.stderr else ""
            entry["dispatched"] = result.returncode == 0
        except subprocess.TimeoutExpired:
            entry["devin_exit_code"] = -1
            entry["devin_stderr"] = "Devin timed out after 300s"
            entry["dispatched"] = False
        except OSError as exc:
            entry["devin_exit_code"] = -1
            entry["devin_stderr"] = f"Failed to launch Devin: {exc}"
            entry["dispatched"] = False
    return entry


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch tasks to Devin from Ops DB (auth) or task-plan.json (fallback)."
    )
    parser.add_argument("--plan", help="Path to task-plan.json (fallback)")
    parser.add_argument("--state-file", help="Path to state.json")
    parser.add_argument("--devin-binary", default="devin")
    parser.add_argument("--dispatch-all", action="store_true")
    parser.add_argument("--permission-mode", default="dangerous")
    parser.add_argument("--model", help="Override model for all tasks")
    parser.add_argument("--ops-db", action="store_true", help="Use Ops DB as source")
    parser.add_argument("--review-run-id", help="Review run ID for Ops DB lookup")
    args = parser.parse_args()

    try:
        # ── Authoritative path: Ops DB ──────────────────────────────────
        if args.ops_db:
            if not _HAS_OPS:
                msg = "ops_adapter.py not available. Install psycopg2 and set DATABASE_URL."
                print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
                return 1
            if not args.review_run_id:
                print(json.dumps({"ok": False, "error": "--review-run-id required with --ops-db"}), file=sys.stderr)
                return 1

            with OpsDbAdapter() as db:
                tasks = db.get_tasks_by_run(args.review_run_id)
                if not tasks:
                    print(json.dumps({"ok": False, "error": f"No tasks found for run {args.review_run_id}"}), file=sys.stderr)
                    return 1

                results: list[dict] = []
                for t in tasks:
                    if t.status in ("completed", "failed", "cancelled"):
                        continue  # skip already-terminal tasks
                    dp = t.dag_payload or {}
                    risk = dp.get("risk", "medium")
                    model = _model(risk, args.model)
                    tid = dp.get("task_id", t.external_id)

                    # Build a dict-shaped task for _dispatch_one
                    task_dict = {
                        "task_id": tid,
                        "title": dp.get("title", ""),
                        "objective": dp.get("objective", ""),
                        "scope": dp.get("scope", ""),
                        "non_goals": dp.get("non_goals", "Do not modify unrelated code."),
                        "acceptance_criteria": dp.get("acceptance_criteria", ["All tests pass"]),
                        "write_scope": dp.get("write_scope", []),
                        "dependencies": dp.get("dependencies", []),
                        "finding_refs": dp.get("finding_refs", []),
                        "risk": risk,
                    }

                    if args.dispatch_all:
                        entry = _dispatch_one(task_dict, Path(args.plan).parent if args.plan else Path.cwd(),
                                              args.devin_binary, model, args.permission_mode, True)
                        entry["ops_db_id"] = t.id
                        entry["source"] = "ops_db"
                        # Transition to DISPATCHED
                        db.transition_task(t.id, STATUS_DISPATCHED, worker_id=f"hermes-{os.getpid()}")
                        db.record_audit(AuditEvent(
                            task_id=t.id, actor="hermes",
                            action="dispatched_to_devin",
                            detail={"model": model, "risk": risk, "task_id": tid},
                        ))
                    else:
                        entry = _dispatch_one(task_dict, Path(args.plan).parent if args.plan else Path.cwd(),
                                              args.devin_binary, model, args.permission_mode, False)
                        entry["ops_db_id"] = t.id
                        entry["source"] = "ops_db"
                        entry["status"] = t.status

                    results.append(entry)

                if args.dispatch_all:
                    log = {"ok": True, "tasks_dispatched": len(results), "state": "DISPATCHED", "source": "ops_db", "tasks": results}
                    log_path = Path(args.plan).parent / "devin-dispatch-log.json" if args.plan else Path("devin-dispatch-log.json")
                    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
                    print(json.dumps(log, indent=2))
                    return 0

                print(json.dumps({"ok": True, "task_count": len(results), "dry_run": True, "source": "ops_db", "tasks": results}, indent=2))
                return 0

        # ── Fallback: task-plan.json ────────────────────────────────────
        if not args.plan:
            print(json.dumps({"ok": False, "error": "Specify --plan or --ops-db"}), file=sys.stderr)
            return 1

        plan_path = Path(args.plan).resolve()
        if not plan_path.exists():
            print(json.dumps({"ok": False, "error": f"Plan file not found: {plan_path}"}), file=sys.stderr)
            return 1

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        tasks = plan.get("tasks", [])
        if not isinstance(tasks, list):
            print(json.dumps({"ok": False, "error": "task-plan.json missing 'tasks' array"}), file=sys.stderr)
            return 1

        out_dir = plan_path.parent
        results = []
        for t in tasks:
            risk = t.get("risk", "medium")
            model = _model(risk, args.model)
            entry = _dispatch_one(t, out_dir, args.devin_binary, model, args.permission_mode, args.dispatch_all)
            entry["risk"] = risk
            entry["source"] = "task-plan.json"
            results.append(entry)

        if args.dispatch_all:
            log = {"ok": True, "tasks_dispatched": len(results), "state": "DISPATCHED", "source": "task-plan.json", "tasks": results}
            log_path = out_dir / "devin-dispatch-log.json"
            log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
            print(json.dumps(log, indent=2))
            return 0

        print(json.dumps({"ok": True, "task_count": len(results), "dry_run": True, "source": "task-plan.json", "tasks": results}, indent=2))
        return 0

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())