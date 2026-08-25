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
    from trace_context import add_trace_argument, get_trace_id
    _HAS_TRACE = True
except ImportError:
    _HAS_TRACE = False

try:
    from ops_adapter import (
        AuditEvent, OpsDbAdapter,
        STATUS_DISPATCHED, STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING,
        make_external_id,
    )
    _HAS_OPS = True
except ImportError:
    _HAS_OPS = False

try:
    from model_resolver import resolve_for_task
    _HAS_RESOLVER = True
except ImportError:
    _HAS_RESOLVER = False

try:
    from circuit_breaker import CircuitBreaker, CircuitOpenError, estimate_cost
    _HAS_BREAKER = True
except ImportError:
    _HAS_BREAKER = False


# Legacy risk→model map kept as a failsafe when the resolver is unavailable.
_RISK_MODEL_MAP: dict[str, str] = {
    "low": "glm-5-2",
    "medium": "glm-5-2",
    "high": "swe-1-7",
    "critical": "swe-1-7",
}


def _model(risk: str, override: str | None, task_type: str | None = None, task: dict | None = None) -> str:
    """Resolve the Devin model for a task, with config-driven fallback chain."""
    if override:
        return override
    if task and task.get("model"):
        return str(task["model"])
    if _HAS_RESOLVER:
        assignment = resolve_for_task(risk, task_type)
        return assignment.primary
    return _RISK_MODEL_MAP.get(risk.lower(), "glm-5-2")


def _find_devin_binary(devin_binary: str, allow_fallback: bool = True) -> str | None:
    """Resolve the Devin CLI binary.

    An explicit --devin-binary is honoured strictly: if it does not resolve we
    return None instead of silently launching a different binary. The
    default-install fallbacks apply only to the default name, so a test or a
    caller pinning a binary can never be redirected to the real Devin.
    """
    p = Path(devin_binary)
    if p.is_file():
        return str(p.resolve())
    from_path = shutil.which(devin_binary)
    if from_path:
        return from_path
    if not allow_fallback:
        return None
    candidates = [
        Path.home() / "AppData" / "Local" / "Programs" / "Devin" / "resources" / "app" / "extensions" / "windsurf" / "devin" / "bin" / "devin.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())
    return None


def _strategy_block(task: dict) -> str:
    """Render the Strategy Router decision as mandatory Devin instructions."""
    strategy = task.get("strategy") or []
    gates = task.get("required_gates") or []
    spec_level = task.get("spec_level") or "none"
    max_attempts = task.get("max_attempts", 3)

    if not strategy:
        return ""

    steps = "\n".join(f"  {i}. {s}" for i, s in enumerate(strategy, 1))
    lines = [
        "",
        "EXECUTION STRATEGY (assigned by Hermes Strategy Router — follow in order):",
        steps,
        f"Required gates before merge: {', '.join(gates) if gates else 'ci'}",
        f"Spec level: {spec_level}",
        f"Bounded attempts: {max_attempts}",
    ]
    if "systematic-debugging" in strategy:
        lines.append("- Use the systematic-debugging skill: find root cause before fixing.")
    if "tdd" in strategy or "test-first" in strategy:
        lines.append("- Use test-driven-development: write the failing test FIRST.")
    if "verification" in strategy or any("verif" in s for s in strategy):
        lines.append("- Use verification-before-completion before reporting done.")
    if spec_level == "formal":
        lines.append("- Produce a formal spec and stop for spec review before implementing.")
    return "\n".join(lines) + "\n"


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
    task_type = task.get("task_type", "")
    early_risk = task.get("early_risk", "")

    criteria = "\n".join(f"- {c}" for c in acceptance)
    ws = f"\nWrite scope: {', '.join(write_scope)}" if write_scope else ""
    deps_str = f"\nDepends on: {', '.join(deps)}" if deps else ""
    refs_str = f"\nFinding refs: {', '.join(finding_refs)}" if finding_refs else ""
    class_str = ""
    if task_type or early_risk:
        class_str = f"\nTask type: {task_type}    Early risk: {early_risk}"

    return f"""You are working on task {tid}.

TITLE: {title}
OBJECTIVE: {objective}
SCOPE: {scope}{ws}{deps_str}{refs_str}{class_str}
NON-GOALS: {non_goals}

ACCEPTANCE CRITERIA:
{criteria}
{_strategy_block(task)}
INSTRUCTIONS:
- WORK ON A BRANCH. PRODUCE A PR.
- DO NOT MODIFY UNRELATED CODE.
"""


def _dispatch_one(task: dict, out_dir: Path, devin_bin: str,
                  model: str, pm: str, dispatch_all: bool,
                  timeout_s: int = 300) -> dict:
    tid = task.get("task_id", "?")
    prompt = _build_prompt(task)
    prompt_dir = out_dir / "devin"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"devin-task-{tid}.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    entry: dict = {"task_id": tid, "model": model, "prompt_file": str(prompt_file)}
    if dispatch_all:
        # Only the default binary name may fall back to a known install path.
        resolved_bin = _find_devin_binary(devin_bin, allow_fallback=(devin_bin == "devin"))
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
                timeout=timeout_s,
            )
            entry["devin_exit_code"] = result.returncode
            entry["devin_stdout"] = result.stdout[:2000] if result.stdout else ""
            entry["devin_stderr"] = result.stderr[:2000] if result.stderr else ""
            entry["dispatched"] = result.returncode == 0
        except subprocess.TimeoutExpired:
            entry["devin_exit_code"] = -1
            entry["devin_stderr"] = f"Devin timed out after {timeout_s}s"
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
    parser.add_argument("--devin-timeout", type=int, default=300,
                        help="Per-task Devin launch timeout in seconds (default 300)")
    parser.add_argument("--model", help="Override model for all tasks")
    parser.add_argument("--ops-db", action="store_true", help="Use Ops DB as source")
    parser.add_argument("--review-run-id", help="Review run ID for Ops DB lookup")
    parser.add_argument("--reset-breaker", action="store_true",
                        help="Reset the run-level circuit breaker before dispatching")
    if _HAS_TRACE:
        add_trace_argument(parser)
    args = parser.parse_args()
    trace_id = get_trace_id(args) if _HAS_TRACE else os.environ.get("HERMES_TRACE_ID", "")

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

            worker_id = f"hermes-{os.getpid()}"
            out_dir = Path(args.plan).parent if args.plan else Path.cwd()

            with OpsDbAdapter() as db:
                # Dry run: inspect without claiming or mutating state.
                if not args.dispatch_all:
                    tasks = db.get_tasks_by_run(args.review_run_id)
                    if not tasks:
                        print(json.dumps({"ok": False, "error": f"No tasks found for run {args.review_run_id}"}), file=sys.stderr)
                        return 1
                    preview: list[dict] = []
                    for t in tasks:
                        dp = t.dag_payload or {}
                        risk = dp.get("early_risk") or dp.get("risk", "medium")
                        task_type = dp.get("task_type")
                        preview.append({
                            "task_id": dp.get("task_id", t.external_id),
                            "ops_db_id": t.id,
                            "status": t.status,
                            "claimable": t.status in ("pending", "queued") and t.attempts < t.max_attempts,
                            "attempts": t.attempts,
                            "max_attempts": t.max_attempts,
                            "task_type": task_type,
                            "early_risk": risk,
                            "strategy": dp.get("strategy", []),
                            "required_gates": dp.get("required_gates", []),
                            "spec_level": dp.get("spec_level"),
                            "model": _model(risk, args.model, task_type, task=dp),
                            "source": "ops_db",
                        })
                    print(json.dumps({
                        "ok": True, "task_count": len(preview), "dry_run": True,
                        "source": "ops_db", "tasks": preview,
                    }, indent=2))
                    return 0

                # Real execution: claim each task under a lease before dispatch.
                # Run-level circuit breaker sits OUTSIDE the per-task attempt
                # bound: it stops a systemic fault from burning every task's
                # budget on the same failure.
                breaker = None
                if _HAS_BREAKER:
                    breaker = CircuitBreaker(
                        out_dir / "circuit-breaker.json",
                        run_id=args.review_run_id,
                        trace_id=trace_id,
                        ops_db=db,
                    )
                    if args.reset_breaker:
                        breaker.reset()

                results: list[dict] = []
                breaker_stop: dict | None = None
                while True:
                    t = db.claim_task(worker_id, review_run_id=args.review_run_id)
                    if t is None:
                        break  # nothing claimable: blocked, exhausted, or done

                    dp = t.dag_payload or {}
                    risk = dp.get("early_risk") or dp.get("risk", "medium")
                    task_type = dp.get("task_type")
                    model = _model(risk, args.model, task_type, task=dp)
                    tid = dp.get("task_id", t.external_id)

                    # Gate BEFORE launching so an over-budget or systemically
                    # failing run spends nothing further. The claimed task is
                    # returned to the queue rather than consumed.
                    if breaker is not None:
                        allowed, reason = breaker.allow(model)
                        if not allowed:
                            db.transition_task(
                                t.id, STATUS_FAILED,
                                error=f"circuit breaker: {reason}"[:500],
                                worker_id=worker_id,
                            )
                            db.transition_task(t.id, STATUS_QUEUED, worker_id=worker_id)
                            db.record_audit(AuditEvent(
                                task_id=t.id, actor="hermes.circuit_breaker",
                                action="dispatch_refused",
                                detail={
                                    "task_id": tid, "model": model,
                                    "reason": reason, **breaker.report(),
                                },
                                trace_id=trace_id,
                            ))
                            breaker_stop = {
                                "task_id": tid, "ops_db_id": t.id,
                                "reason": reason, "breaker": breaker.report(),
                            }
                            break

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
                        "task_type": dp.get("task_type", ""),
                        "early_risk": risk,
                        "strategy": dp.get("strategy", []),
                        "required_gates": dp.get("required_gates", []),
                        "spec_level": dp.get("spec_level", "none"),
                        "max_attempts": t.max_attempts,
                        "risk": risk,
                    }

                    entry = _dispatch_one(task_dict, out_dir, args.devin_binary,
                                          model, args.permission_mode, True,
                                          timeout_s=args.devin_timeout)
                    entry["ops_db_id"] = t.id
                    entry["source"] = "ops_db"
                    entry["lease_owner"] = worker_id
                    entry["attempts"] = t.attempts
                    entry["max_attempts"] = t.max_attempts

                    # Transition strictly on the launch outcome.
                    if entry.get("dispatched"):
                        if breaker is not None:
                            breaker.record_success(model)
                            entry["breaker"] = breaker.report()
                        db.transition_task(t.id, STATUS_DISPATCHED, worker_id=worker_id)
                        db.record_audit(AuditEvent(
                            task_id=t.id, actor="hermes",
                            action="dispatched_to_devin",
                            detail={
                                "model": model, "risk": risk, "task_id": tid,
                                "strategy": dp.get("strategy", []),
                                "required_gates": dp.get("required_gates", []),
                                "lease_owner": worker_id,
                                "est_cost_usd": estimate_cost(model) if _HAS_BREAKER else None,
                            },
                            trace_id=trace_id,
                        ))
                        entry["ops_status"] = STATUS_DISPATCHED
                    else:
                        err = entry.get("error") or entry.get("devin_stderr") or "Devin launch failed"
                        if breaker is not None:
                            breaker.record_failure(model, err)
                            entry["breaker"] = breaker.report()
                        db.transition_task(t.id, STATUS_FAILED, error=err[:500], worker_id=worker_id)
                        db.record_audit(AuditEvent(
                            task_id=t.id, actor="hermes",
                            action="dispatch_failed",
                            detail={
                                "model": model, "risk": risk, "task_id": tid,
                                "error": err[:500], "attempts": t.attempts,
                                "max_attempts": t.max_attempts,
                                "exhausted": t.attempts >= t.max_attempts,
                                "breaker": breaker.report() if breaker else None,
                            },
                            trace_id=trace_id,
                        ))
                        entry["ops_status"] = STATUS_FAILED

                    results.append(entry)

                dispatched_ok = [r for r in results if r.get("dispatched")]
                failed = [r for r in results if not r.get("dispatched")]
                state = "DISPATCHED" if dispatched_ok and not failed else (
                    "PARTIALLY_DISPATCHED" if dispatched_ok else "DISPATCH_FAILED"
                )
                if breaker_stop:
                    state = "CIRCUIT_OPEN"
                log = {
                    "ok": len(failed) == 0 and breaker_stop is None,
                    "trace_id": trace_id,
                    "tasks_claimed": len(results),
                    "tasks_dispatched": len(dispatched_ok),
                    "tasks_failed": len(failed),
                    "state": state,
                    "source": "ops_db",
                    "lease_owner": worker_id,
                    "circuit_breaker": breaker.report() if breaker else None,
                    "circuit_stop": breaker_stop,
                    "tasks": results,
                }
                log_path = out_dir / "devin-dispatch-log.json"
                log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
                print(json.dumps(log, indent=2))
                if breaker_stop:
                    return 4
                return 0 if not failed else 1

        # ── Fallback: task-plan.json (DRY RUN ONLY) ─────────────────────
        if not args.plan:
            print(json.dumps({"ok": False, "error": "Specify --plan or --ops-db"}), file=sys.stderr)
            return 1

        if args.dispatch_all:
            print(json.dumps({
                "ok": False,
                "error": (
                    "Real dispatch requires Ops DB. task-plan.json is a dry-run "
                    "artifact only — it carries no lease, no attempt counter and "
                    "no dependency state. Re-run with --ops-db --review-run-id <id>."
                ),
                "state": "PLAN_READY_NOT_DISPATCHED",
            }, indent=2), file=sys.stderr)
            return 2

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
            risk = t.get("early_risk") or t.get("risk", "medium")
            task_type = t.get("task_type")
            model = _model(risk, args.model, task_type, task=t)
            entry = _dispatch_one(t, out_dir, args.devin_binary, model, args.permission_mode, False)
            entry["risk"] = risk
            entry["task_type"] = t.get("task_type")
            entry["strategy"] = t.get("strategy", [])
            entry["required_gates"] = t.get("required_gates", [])
            entry["source"] = "task-plan.json"
            results.append(entry)

        print(json.dumps({"ok": True, "trace_id": trace_id, "task_count": len(results), "dry_run": True, "source": "task-plan.json", "tasks": results}, indent=2))
        return 0

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())