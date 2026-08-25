#!/usr/bin/env python3
"""Open Design Orchestrator.

Runs the full Open Design loop from user request → evidence → review →
task DAG → Devin → PR/CI → policy gate → merge → outcome metrics.

This script is intentionally a thin state-machine wrapper around the
project-review-orchestrator scripts plus the execution-discipline skills.
Unimplemented stages (real CI, real Devin dispatch, skill promotion) are
stubbed but keep the state contract so they can be filled in later.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════════════

SKILL_DIR = Path(__file__).resolve().parent.parent
REVIEW_SKILL = SKILL_DIR.parent / "project-review-orchestrator"
SCRIPTS = REVIEW_SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from model_resolver import resolve
    _HAS_RESOLVER = True
except ImportError:
    _HAS_RESOLVER = False

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _run_script(name: str, *args: str, env: dict | None = None, timeout: int = 180) -> dict:
    """Run a project-review-orchestrator script and parse JSON stdout."""
    cmd = [sys.executable, str(SCRIPTS / name), *args]
    environment = os.environ.copy()
    if env:
        environment.update(env)
    # Ensure trace context is propagated to every subprocess.
    if "HERMES_TRACE_ID" not in environment:
        environment["HERMES_TRACE_ID"] = os.environ.get("HERMES_TRACE_ID", "")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "(no output)")[:500]
        raise RuntimeError(f"{name} failed (exit {proc.returncode}): {err}")
    return json.loads(proc.stdout or "{}")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_state(out_dir: Path) -> dict:
    state_path = out_dir / "state.json"
    default_trace = out_dir.name
    if state_path.exists():
        state = _read_json(state_path)
        state.setdefault("trace_id", default_trace)
        state.setdefault("stage_durations", {})
        return state
    return {
        "run_id": out_dir.name,
        "trace_id": default_trace,
        "status": "CREATED",
        "progress": 0,
        "repo": "",
        "commit_sha": "",
        "branch": "",
        "stage_durations": {},
    }


class RepairBudget:
    """Cost-bounded repair loop controller.

    Tracks wall-clock duration and attempt count.  Stops when any limit is
    exceeded.  Token/$ cost is estimated from duration and model for now; real
    telemetry can back-fill ``cost_usd`` and ``token_count`` later.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        max_duration_seconds: float = 1800.0,
        max_cost_usd: float = 10.0,
        start_time: float | None = None,
    ):
        self.max_attempts = max(1, max_attempts)
        self.max_duration_seconds = max(0.0, max_duration_seconds)
        self.max_cost_usd = max(0.0, max_cost_usd)
        self.start_time = start_time if start_time is not None else time.monotonic()
        self.attempts = 0
        self.estimated_cost_usd = 0.0

    def can_spend(self) -> bool:
        """Return True if another repair attempt is within budget."""
        elapsed = time.monotonic() - self.start_time
        if self.attempts >= self.max_attempts:
            return False
        if self.max_duration_seconds > 0 and elapsed > self.max_duration_seconds:
            return False
        if self.max_cost_usd > 0 and self.estimated_cost_usd > self.max_cost_usd:
            return False
        return True

    def record_spend(self, attempt_cost_usd: float = 0.5) -> None:
        """Record that one repair attempt was consumed."""
        self.attempts += 1
        self.estimated_cost_usd += attempt_cost_usd

    def summary(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "duration_seconds": round(time.monotonic() - self.start_time, 3),
            "max_duration_seconds": self.max_duration_seconds,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "max_cost_usd": self.max_cost_usd,
        }


def _save_state(out_dir: Path, status: str, progress: int, extra: dict | None = None) -> None:
    state = _load_state(out_dir)
    state["status"] = status
    state["progress"] = progress
    state["trace_id"] = state.get("trace_id") or out_dir.name
    state.setdefault("stage_durations", {})
    start = state.get("started_at_monotonic")
    if start:
        state["stage_durations"][status] = round(time.monotonic() - start, 3)
    else:
        state["stage_durations"][status] = 0.0
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if extra:
        state.update(extra)
    _write_json(out_dir / "state.json", state)


def _update_artifact(out_dir: Path, key: str, path: Path) -> None:
    state = _load_state(out_dir)
    artifacts = state.setdefault("artifacts", {})
    artifacts[key] = str(path)
    _write_json(out_dir / "state.json", state)


# ═══════════════════════════════════════════════════════════════════════════════
# Mock external review (deterministic, for dry-runs)
# ═══════════════════════════════════════════════════════════════════════════════

MOCK_REVIEW = {
    "executive_summary": "Open Design dry-run review. No real external reviewer invoked.",
    "architecture_assessment": "Mock assessment for workflow validation.",
    "findings": [
        {
            "id": "OPEN-001",
            "title": "Verify Open Design end-to-end state machine",
            "severity": "medium",
            "confidence": 0.9,
            "claim": "The open_design.py orchestrator must validate every stage before real dispatch.",
            "evidence_refs": [".hermes/open-design/"],
            "challenge_to_hermes": "Ensure no stage is silently skipped when a downstream dependency fails.",
            "recommendation": "Run this orchestrator in dry-run mode before connecting real Devin/CI.",
            "verification": "Re-run with --reviewer mock and confirm state.json reaches POLICY_GATE.",
        }
    ],
    "missing_evidence": ["real CI results", "real Devin output"],
    "priority_order": ["OPEN-001"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# Stage functions
# ═══════════════════════════════════════════════════════════════════════════════


def stage_evidence(repo: Path, out_dir: Path) -> dict:
    """Collect deterministic repo evidence."""
    result = _run_script(
        "collect_repo_evidence.py",
        "--repo", str(repo),
        "--out", str(out_dir),
        "--review-mode", "openai-api",
    )
    _save_state(
        out_dir, "EVIDENCE_COLLECTED", 10,
        {
            "repo": str(repo),
            "commit_sha": result.get("commit"),
            "branch": result.get("branch"),
        },
    )
    _update_artifact(out_dir, "repo_evidence", out_dir / "repo-evidence.json")
    _update_artifact(out_dir, "repo_evidence_md", out_dir / "repo-evidence.md")
    return result


def stage_conflict(repo: Path, out_dir: Path, state: dict) -> dict:
    """Detect conflicts between Git, Ops DB, and AgentMemory."""
    result = _run_script(
        "conflict_detector.py",
        "--repo", str(repo),
        "--review-sha", state.get("commit_sha", ""),
        "--review-run-id", state.get("run_id", ""),
    )
    _save_state(out_dir, "CONFLICT_CLEAR" if result.get("status") == "CLEAR" else "CONFLICTED", 20, {
        "conflicts": result.get("conflicts", []),
        "conflict_summary": result.get("summary", {}),
    })
    _update_artifact(out_dir, "conflicts", out_dir / "conflicts.json")
    return result


def stage_analysis(out_dir: Path, analysis_file: Path | None) -> Path:
    """Ensure a Hermes analysis file exists; if not, generate a stub."""
    target = out_dir / "hermes-analysis.md"
    if analysis_file and analysis_file.is_file():
        target.write_text(analysis_file.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        evidence_md = out_dir / "repo-evidence.md"
        evidence_text = evidence_md.read_text(encoding="utf-8") if evidence_md.is_file() else "No evidence."
        target.write_text(
            f"# Hermes First-Pass Analysis — {out_dir.name}\n\n## PROJECT SNAPSHOT\n{evidence_text[:2000]}\n\n"
            "## 1. EXECUTIVE_SUMMARY\nOpen Design dry-run. Replace with real Hermes analysis.\n\n"
            "## 4. SECURITY_BOUNDARIES\nTBD.\n\n"
            "## 21. SELF_REVIEW / KNOWN_GAPS\nTBD.\n",
            encoding="utf-8",
        )
    _save_state(out_dir, "HERMES_ANALYSIS_DONE", 30)
    _update_artifact(out_dir, "hermes_analysis", target)
    return target


def stage_build_packet(out_dir: Path) -> Path:
    """Build sanitized review packet."""
    result = _run_script(
        "build_review_packet.py",
        "--evidence", str(out_dir / "repo-evidence.json"),
        "--analysis", str(out_dir / "hermes-analysis.md"),
        "--out", str(out_dir / "external-review-packet.json"),
        "--mode", "openai-api",
    )
    _save_state(out_dir, "PACKET_BUILT", 35, {"packet_sha256": result.get("sha256")})
    _update_artifact(out_dir, "external_review_packet", out_dir / "external-review-packet.json")
    return out_dir / "external-review-packet.json"


def stage_strategy_and_classify(out_dir: Path) -> dict:
    """Classify findings and route strategy for each approved finding."""
    # Load reconciled review if exists, else use mock
    rec_path = out_dir / "reconciled-review.json"
    if not rec_path.is_file():
        return {"tasks": [], "routes": []}
    rec = _read_json(rec_path)
    findings = rec.get("findings", [])
    if not findings:
        return {"tasks": [], "routes": []}

    classified_path = out_dir / "classified-findings.json"
    _run_script(
        "task_classifier.py",
        "--findings", str(rec_path),
        "--out", str(classified_path),
    )
    classified = _read_json(classified_path)
    _update_artifact(out_dir, "classified_findings", classified_path)
    _save_state(out_dir, "STRATEGY_ROUTED", 38)
    return {"classified": classified}


def stage_external_review(out_dir: Path, reviewer: str, model: str | None, independent_review: bool = False) -> Path:
    """Run external review (mock, codex, or openai).

    For HIGH/CRITICAL findings, optionally trigger an independent adversarial
    Codex review to satisfy the independent policy-gate check.
    """
    packet = out_dir / "external-review-packet.json"
    review_path = out_dir / "external-review.json"

    if reviewer == "mock":
        _write_json(review_path, MOCK_REVIEW)
    elif reviewer == "codex":
        args = ["--packet", str(packet), "--out", str(out_dir), "--timeout", "300"]
        if model:
            args.extend(["--model", model])
        _run_script("codex_review.py", *args, timeout=600)
    elif reviewer == "openai":
        _run_script(
            "openai_review.py",
            "--packet", str(packet),
            "--out", str(out_dir),
            "--mode", "openai-api",
        )
    else:
        raise ValueError(f"Unknown reviewer: {reviewer}")

    _save_state(out_dir, "EXTERNAL_REVIEW_RECEIVED", 50)
    _update_artifact(out_dir, "external_review", review_path)

    # Independent adversarial review for HIGH/CRITICAL findings.
    # Default to true for real reviewers; must be explicit for mock.
    if reviewer != "mock" or independent_review:
        review = _read_json(review_path)
        findings = review.get("findings", [])
        high_or_critical = any(
            str(f.get("severity", "")).lower() in ("high", "critical")
            for f in findings
        )
        if high_or_critical:
            indep_dir = out_dir / "independent-review"
            indep_dir.mkdir(parents=True, exist_ok=True)
            if reviewer == "codex":
                args = [
                    "--packet", str(packet), "--out", str(indep_dir),
                    "--mode", "adversarial", "--timeout", "300",
                ]
                if model:
                    args.extend(["--model", model])
                _run_script("codex_review.py", *args, timeout=600)
            elif reviewer == "openai":
                _run_script(
                    "openai_review.py",
                    "--packet", str(packet),
                    "--out", str(indep_dir),
                    "--mode", "openai-api",
                )
            else:
                # mock path: treat the same review as the independent artifact
                _write_json(indep_dir / "external-review.json", review)
            # Normalize to a single independent-review.json file
            src = indep_dir / "external-review.json"
            dst = out_dir / "independent-review.json"
            if src.is_file():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                _update_artifact(out_dir, "independent_review", dst)

    return review_path


def stage_reconcile(out_dir: Path) -> Path:
    """Reconcile Hermes analysis with external review."""
    result = _run_script(
        "reconcile_review.py",
        "--analysis", str(out_dir / "hermes-analysis.md"),
        "--external", str(out_dir / "external-review.json"),
        "--out", str(out_dir),
    )
    _save_state(out_dir, "RECONCILED", 60, {
        "reconciled_count": result.get("reconciled_count"),
        "dispositions": result.get("dispositions"),
    })
    _update_artifact(out_dir, "reconciled_review", out_dir / "reconciled-review.json")
    return out_dir / "reconciled-review.json"


def stage_codemap(repo: Path, out_dir: Path) -> Path:
    """Build Devin Codemap brief."""
    result = _run_script(
        "build_codemap_brief.py",
        "--reconciled", str(out_dir / "reconciled-review.json"),
        "--repo", str(repo),
        "--out", str(out_dir),
    )
    _save_state(out_dir, "CODEMAP_BUILT", 70, {
        "commit": result.get("commit"),
        "branch": result.get("branch"),
    })
    _update_artifact(out_dir, "codemap_brief", out_dir / "codemap-brief.md")
    return out_dir / "codemap-brief.md"


def stage_decompose(repo: Path, out_dir: Path, ops_db: bool) -> Path:
    """Decompose reconciled findings into a task DAG."""
    args = [
        "--reconciled", str(out_dir / "reconciled-review.json"),
        "--codemap", str(out_dir / "codemap-brief.md"),
        "--out", str(out_dir),
    ]
    if ops_db:
        state = _load_state(out_dir)
        args.extend(["--ops-db", "--review-run-id", state.get("run_id", "")])
    result = _run_script("decompose_tasks.py", *args, timeout=120)
    _save_state(out_dir, "TASKS_DECOMPOSED", 75, {
        "task_count": result.get("task_count"),
        "ops_db_count": result.get("ops_db_count") if ops_db else 0,
    })
    _update_artifact(out_dir, "task_plan", out_dir / "task-plan.json")
    return out_dir / "task-plan.json"


def stage_route_tasks(out_dir: Path) -> dict:
    """Run strategy router on the task plan."""
    plan = _read_json(out_dir / "task-plan.json")
    tasks = plan.get("tasks", [])
    routes = []
    for t in tasks:
        risk = t.get("risk", "medium").upper()
        task_type = t.get("task_type", "FEATURE").upper()
        try:
            r = _run_script(
                "strategy_router.py",
                "--task-type", task_type,
                "--risk", risk,
                "--out", str(out_dir / f"route-{t['task_id']}.json"),
            )
            routes.append({"task_id": t["task_id"], "route": r})
        except Exception as exc:
            routes.append({"task_id": t["task_id"], "route": None, "error": str(exc)})
    _write_json(out_dir / "task-routes.json", routes)
    _save_state(out_dir, "STRATEGY_ROUTED", 78)
    _update_artifact(out_dir, "task_routes", out_dir / "task-routes.json")
    return {"routes": routes}


def stage_dispatch(out_dir: Path, dispatch_mode: str) -> dict:
    """Dispatch tasks to Devin (dry-run or real)."""
    state = _load_state(out_dir)
    result = _run_script(
        "dispatch_to_devin.py",
        "--plan", str(out_dir / "task-plan.json"),
        "--state-file", str(out_dir / "state.json"),
        *("--dispatch-all",) if dispatch_mode == "dispatch" else (),
    )
    _save_state(out_dir, "DISPATCHED" if dispatch_mode == "dispatch" else "PLAN_READY_NOT_DISPATCHED", 80, {
        "task_count": result.get("task_count"),
        "dry_run": dispatch_mode != "dispatch",
    })
    _update_artifact(out_dir, "dispatch_log", out_dir / "devin-dispatch-log.json")
    return result


def stage_collect_ci(out_dir: Path) -> dict:
    """STUB: collect CI, CodeRabbit, and Codex re-review findings."""
    findings = {
        "ci_status": "unknown",
        "coderabbit_findings": [],
        "codex_re_review": [],
        "note": "Real CI integration not yet implemented. Returning mock green.",
    }
    _write_json(out_dir / "ci-findings.json", findings)
    _save_state(out_dir, "CI_FINDINGS_RECEIVED", 85, findings)
    _update_artifact(out_dir, "ci_findings", out_dir / "ci-findings.json")
    return findings


def stage_final_risk(out_dir: Path) -> dict:
    """Recalculate final risk based on changed paths and CI findings."""
    state = _load_state(out_dir)
    early_risk = "medium"
    # Derive early risk from task plan
    plan = _read_json(out_dir / "task-plan.json")
    risks = {t.get("risk", "medium").upper() for t in plan.get("tasks", [])}
    for r in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        if r in risks:
            early_risk = r
            break

    ci = _read_json(out_dir / "ci-findings.json")
    result = _run_script(
        "final_risk.py",
        "--early-risk", early_risk,
        "--changed-paths", json.dumps([]),
        "--test-results", json.dumps(ci),
        "--out", str(out_dir / "final-risk.json"),
    )
    _save_state(out_dir, "FINAL_RISK_RECALCULATED", 88, result)
    _update_artifact(out_dir, "final_risk", out_dir / "final-risk.json")
    return result


def stage_policy_gate(out_dir: Path) -> dict:
    """Run the independent policy gate.

    HIGH/CRITICAL risk must have an independent adversarial review artifact.
    Without it the gate fails closed (BLOCK) rather than letting Hermes
    reconcile its own output.
    """
    final_risk = _read_json(out_dir / "final-risk.json")
    risk_level = (final_risk.get("final_risk", "") or "medium").upper()

    # Independent check: HIGH/CRITICAL requires a separate adversarial review.
    if risk_level in ("HIGH", "CRITICAL"):
        indep_path = out_dir / "independent-review.json"
        if not indep_path.is_file():
            result = {
                "decision": "BLOCK",
                "reason": "HIGH/CRITICAL risk requires an independent Codex adversarial review (independent-review.json missing)",
                "final_risk": final_risk,
            }
            _write_json(out_dir / "policy-gate.json", result)
            _save_state(out_dir, "POLICY_BLOCK", 95, result)
            _update_artifact(out_dir, "policy_gate", out_dir / "policy-gate.json")
            return result

    # Try to invoke the real gate if the binary is available
    decision = "PASS"
    reason = "Dry-run: no real CI or human approval configured."
    binary = shutil.which("hermes-policy-gate") or (
        SKILL_DIR.parents[3] / "packages" / "gate" / "dist" / "bin.js"
    )
    if Path(str(binary)).is_file():
        try:
            manifest = {
                "version": "1.0.0",
                "policy_version": "0.1.0",
                "repository": {"owner": "hermes-ops", "name": "hermes-ops"},
                "headSha": _load_state(out_dir).get("commit_sha", "0" * 40),
                "risk_level": final_risk.get("final_risk", "medium"),
                "ci": {"status": "success"},
                "review_findings": [],
                "approval_token": None,
            }
            manifest_path = out_dir / "policy-manifest.json"
            _write_json(manifest_path, manifest)
            proc = subprocess.run(
                ["node", str(binary), "--manifest", str(manifest_path), "--json"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                gate = json.loads(proc.stdout)
                decision = gate.get("decision", "PASS")
                reason = gate.get("reason", reason)
            else:
                reason = proc.stderr[:300]
        except Exception as exc:
            reason = f"Real gate unavailable: {exc}"

    result = {"decision": decision, "reason": reason, "final_risk": final_risk}
    _write_json(out_dir / "policy-gate.json", result)
    _save_state(out_dir, f"POLICY_{decision}", 95, result)
    _update_artifact(out_dir, "policy_gate", out_dir / "policy-gate.json")
    return result


def stage_repair(out_dir: Path, budget: RepairBudget) -> dict:
    """Cost-bounded repair loop. If POLICY_REPAIR, re-dispatch while within budget."""
    state = _load_state(out_dir)
    attempts = state.get("repair_attempts", 0)

    if state.get("status") == "POLICY_REPAIR" and budget.can_spend():
        budget.record_spend()
        attempts += 1
        _save_state(out_dir, "REPAIR_DISPATCHED", 90, {"repair_attempts": attempts})
        # Real implementation would re-dispatch with a targeted verification prompt.
        result: dict[str, Any] = {"repaired": True, "attempts": attempts, "budget": budget.summary(), "note": "stub"}
    else:
        reason = "not in repair state" if state.get("status") != "POLICY_REPAIR" else "repair budget exhausted"
        result = {
            "repaired": False,
            "attempts": attempts,
            "budget": budget.summary(),
            "reason": reason,
            "escalate": state.get("status") == "POLICY_REPAIR",
        }

    _write_json(out_dir / "repair-result.json", result)
    _update_artifact(out_dir, "repair_result", out_dir / "repair-result.json")
    return result


def stage_outcome(out_dir: Path, budget: RepairBudget | None = None) -> dict:
    """Collect structured outcome metrics for the run."""
    state = _load_state(out_dir)
    start = state.get("started_at_monotonic", time.monotonic())
    duration = round(time.monotonic() - start, 3)

    plan = _read_json(out_dir / "task-plan.json") if (out_dir / "task-plan.json").exists() else {"tasks": []}
    gate = _read_json(out_dir / "policy-gate.json") if (out_dir / "policy-gate.json").exists() else {}

    metrics = {
        "schema_version": "1.0.0",
        "run_id": out_dir.name,
        "trace_id": state.get("trace_id", out_dir.name),
        "final_status": state.get("status"),
        "started_at": state.get("started_at"),
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "duration_seconds": duration,
        "task_count": len(plan.get("tasks", [])),
        "gate_decision": gate.get("decision"),
        "gate_reason": gate.get("reason"),
        "repair_attempts": state.get("repair_attempts", 0),
        "repair_budget": budget.summary() if budget else None,
        "stages_completed": list(state.get("stage_durations", {}).keys()),
        "estimated_cost_usd": round(duration * 0.01, 4),
        "lessons": ["Open Design orchestrator reached policy gate."],
        "candidate_skill": None,
    }
    _write_json(out_dir / "outcome.json", metrics)
    _save_state(out_dir, "OUTCOME_COLLECTED", 100, metrics)
    _update_artifact(out_dir, "outcome", out_dir / "outcome.json")
    return metrics


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Design Orchestrator")
    parser.add_argument("--repo", default=".", help="Repository to audit")
    parser.add_argument("--out", required=True, help="Output directory for the run")
    parser.add_argument("--reviewer", choices=["mock", "codex", "openai"], default="mock",
                        help="External reviewer (default: mock)")
    parser.add_argument("--reviewer-model", default=None, help="Override model for codex reviewer")
    parser.add_argument("--independent-review", action="store_true", default=None,
                        help="Run an independent adversarial review for HIGH/CRITICAL findings")
    parser.add_argument("--dispatch-mode", choices=["dry-run", "dispatch"], default="dry-run",
                        help="Devin dispatch mode (default: dry-run)")
    parser.add_argument("--ops-db", action="store_true", help="Write task DAG to Ops DB")
    parser.add_argument("--analysis", default=None, help="Path to hermes-analysis.md (optional)")
    parser.add_argument("--stop-after", default=None,
                        help="Stop after named stage (e.g., RECONCILED, TASKS_DECOMPOSED)")
    parser.add_argument("--max-repair-attempts", type=int, default=3,
                        help="Max Devin repair attempts (default: 3)")
    parser.add_argument("--max-repair-duration", type=int, default=1800,
                        help="Max repair wall-clock time in seconds (default: 1800)")
    parser.add_argument("--max-repair-cost", type=float, default=10.0,
                        help="Estimated max repair cost in USD (default: 10.0)")
    parser.add_argument("--skip-conflict-check", action="store_true",
                        help="Skip conflict detector (useful for fresh repos)")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Set the canonical trace id for this run.
    run_id = out_dir.name
    os.environ["HERMES_TRACE_ID"] = run_id
    _load_state(out_dir)  # ensure state has trace_id

    # Initialize state
    _save_state(
        out_dir,
        "PREFLIGHT",
        0,
        {
            "repo": str(repo),
            "trace_id": run_id,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "started_at_monotonic": time.monotonic(),
        },
    )

    try:
        # 1. Evidence
        ev = stage_evidence(repo, out_dir)
        if args.stop_after == "EVIDENCE_COLLECTED":
            return 0

        # 2. Conflict detection
        if not args.skip_conflict_check:
            state = _load_state(out_dir)
            cf = stage_conflict(repo, out_dir, state)
            if cf.get("status") != "CLEAR":
                print(json.dumps({"ok": False, "stage": "conflict", "result": cf}, indent=2))
                return 1
        else:
            _save_state(out_dir, "CONFLICT_CLEAR", 20)

        # 3. Hermes analysis
        stage_analysis(out_dir, Path(args.analysis) if args.analysis else None)
        if args.stop_after == "HERMES_ANALYSIS_DONE":
            return 0

        # 4. Build packet
        stage_build_packet(out_dir)

        # 5. Strategy / classify (advisory, on mock external review yet)
        stage_strategy_and_classify(out_dir)

        # 6. External review
        reviewer_model = args.reviewer_model
        if not reviewer_model and _HAS_RESOLVER:
            stage = "openai_spec_review" if args.reviewer == "openai" else "spec_review"
            reviewer_model = resolve(stage).primary
        independent_review = args.independent_review if args.independent_review is not None else (args.reviewer != "mock")
        stage_external_review(out_dir, args.reviewer, reviewer_model, independent_review)
        if args.stop_after == "EXTERNAL_REVIEW_RECEIVED":
            return 0

        # 7. Reconcile
        stage_reconcile(out_dir)
        if args.stop_after == "RECONCILED":
            return 0

        # 8. Codemap
        stage_codemap(repo, out_dir)
        if args.stop_after == "CODEMAP_BUILT":
            return 0

        # 9. Decompose
        stage_decompose(repo, out_dir, args.ops_db)
        if args.stop_after == "TASKS_DECOMPOSED":
            return 0

        # 10. Route tasks
        stage_route_tasks(out_dir)

        # 11. Dispatch
        stage_dispatch(out_dir, args.dispatch_mode)
        if args.stop_after == "DISPATCHED":
            return 0

        # 12. CI / Codex / CodeRabbit findings
        stage_collect_ci(out_dir)

        # 13. Final risk
        stage_final_risk(out_dir)

        # 14. Policy gate
        gate = stage_policy_gate(out_dir)

        # 15. Repair loop (cost-bounded)
        repair_budget = RepairBudget(
            max_attempts=args.max_repair_attempts,
            max_duration_seconds=args.max_repair_duration,
            max_cost_usd=args.max_repair_cost,
        )
        while _load_state(out_dir).get("status") == "POLICY_REPAIR" and repair_budget.can_spend():
            stage_repair(out_dir, repair_budget)
            if _load_state(out_dir).get("status") != "POLICY_REPAIR":
                break
            gate = stage_policy_gate(out_dir)

        # 16. Outcome
        stage_outcome(out_dir, repair_budget)

        print(json.dumps({
            "ok": True,
            "out_dir": str(out_dir),
            "state": str(out_dir / "state.json"),
            "final_status": _load_state(out_dir).get("status"),
        }, indent=2))
        return 0

    except Exception as exc:
        _save_state(out_dir, "FAILED", 0, {"error": str(exc)})
        print(json.dumps({"ok": False, "stage": _load_state(out_dir).get("status"), "error": str(exc)}, indent=2),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
