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
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
import shutil
from urllib.parse import urlparse
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

try:
    from policy_gate import find_gate_binary
    _HAS_GATE_DISCOVERY = True
except ImportError:
    _HAS_GATE_DISCOVERY = False

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


def _read_json_optional(path: Path, default: Any = None) -> Any:
    """Read an artifact that legitimately may not exist yet.

    `_read_json` stays strict on purpose: a missing required artifact is a real
    pipeline error and must surface. But some artifacts are conditional —
    `independent-review.json` only exists for HIGH/CRITICAL runs, and
    `reconciled-review.json` is absent on a dry run that stops early. Reading
    those with the strict helper turns "not applicable" into a crash.
    """
    if default is None:
        default = {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


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


def _initialize_state(out_dir: Path, run_id: str, repo: Path) -> None:
    """Create state from scratch; an output directory may contain old state."""
    _write_json(out_dir / "state.json", {
        "run_id": run_id,
        "trace_id": run_id,
        "status": "PREFLIGHT",
        "progress": 0,
        "repo": str(repo),
        "commit_sha": "",
        "branch": "",
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "started_at_monotonic": time.monotonic(),
        "stage_durations": {},
        "artifacts": {},
    })


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


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_risk(risk: Any) -> bool:
    """Return True if ``risk`` is one of the four canonical Hermes risk levels."""
    return isinstance(risk, str) and risk.upper() in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def _sanitize_repo_name(name: str) -> str:
    """Sanitize a repository owner/name for the evidence manifest."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._-")[:100]
    return cleaned or "repo"


def _repo_owner_name(state: dict) -> tuple[str, str]:
    """Derive repository identity from the verified origin remote."""
    repo = Path(str(state.get("repo", "."))).resolve()
    remote = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False,
    )
    if remote.returncode != 0 or not remote.stdout.strip():
        raise ValueError("repository origin remote is unavailable")
    value = remote.stdout.strip()
    if re.match(r"^[^@/:]+@[^:]+:.+$", value):
        host, parsed_path = value.split("@", 1)[1].split(":", 1)
        host = host.lower().rstrip(".")
    else:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        parsed_path = parsed.path
    if host not in {"github.com", "gitlab.com", "bitbucket.org"}:
        raise ValueError(f"cannot establish repository identity from origin: {value!r}")
    parts = [part for part in parsed_path.strip("/").split("/") if part]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"cannot establish repository identity from origin: {value!r}")
    return _sanitize_repo_name(parts[0]), _sanitize_repo_name(parts[1].removesuffix(".git"))


def _manifest_ci(out_dir: Path) -> dict:
    """Build the ``ci`` block of the evidence manifest from collected findings.

    CI success is never fabricated: an unknown/mock/absent CI status is mapped
    to ``failure`` so the gate cannot pass on unverified evidence.
    """
    ci = _read_json_optional(out_dir / "ci-findings.json", {})
    status = str(ci.get("ci_status", "") or "").lower()
    valid = {"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"}
    conclusion = status if status in valid else "failure"

    # A green conclusion requires an explicit flag or a checks list where every
    # check is success/neutral/skipped.
    if conclusion == "success" and ci.get("ci_green") is not True:
        checks = ci.get("checks")
        if isinstance(checks, list) and checks:
            bad = [c for c in checks if c.get("conclusion") not in ("success", "neutral", "skipped")]
            if bad:
                conclusion = "failure"
        else:
            conclusion = "failure"

    return {"conclusion": conclusion}


def _git_output(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _actual_changed_files(out_dir: Path, head_sha: str | None = None) -> list[str]:
    state = _load_state(out_dir)
    repo = Path(str(state.get("repo", "."))).resolve()
    head = head_sha or _git_output(repo, "rev-parse", "HEAD")
    base = state.get("base_sha") or os.environ.get("HERMES_BASE_SHA")
    if not base:
        try:
            base = _git_output(repo, "merge-base", head, "origin/main")
        except ValueError:
            parents = _git_output(repo, "rev-list", "--parents", "-n", "1", head).split()
            base = parents[1] if len(parents) > 1 else head
    raw = _git_output(repo, "diff", "--name-only", "--diff-filter=ACDMRTUXB", f"{base}..{head}")
    return [p for p in raw.splitlines() if p]


def _changed_files(out_dir: Path, head_sha: str | None = None) -> list[str]:
    """Collect actual base-to-head paths; task scope is only a consistency check."""
    changed = _actual_changed_files(out_dir, head_sha)
    plan = _read_json_optional(out_dir / "task-plan.json", {})
    declared: set[str] = set()
    for t in plan.get("tasks", []):
        for p in (t.get("write_scope") or t.get("scope") or []):
            if isinstance(p, str):
                declared.add(p)
    undeclared = [p for p in changed if p not in declared]
    if undeclared:
        raise ValueError(f"undeclared changed files: {', '.join(undeclared)}")
    return changed


def _build_manifest(out_dir: Path, state: dict, risk_level: str, changed_files: list[str]) -> dict:
    """Build a canonical EvidenceManifest v1 for the policy gate."""
    head_sha = state.get("commit_sha", "")
    if not re.match(r"^[0-9a-f]{40}$", head_sha):
        raise ValueError(f"commit_sha is not a 40-char lowercase hex SHA: {head_sha!r}")

    owner, name = _repo_owner_name(state)
    timestamp = state.get("evidence_generated_at") or state.get("created_at")
    if not timestamp:
        raise ValueError("evidence producer timestamp is missing")

    artifacts: list[dict[str, str]] = []
    for label, path in (
        ("final-risk.json", out_dir / "final-risk.json"),
        ("task-plan.json", out_dir / "task-plan.json"),
    ):
        if path.is_file():
            artifacts.append({"path": label, "sha256": _sha256_file(path)})
    if not artifacts:
        raise ValueError("no policy evidence artifacts available")

    return {
        "schemaVersion": 1,
        "repository": {"owner": owner, "name": name},
        "headSha": head_sha,
        "policyVersion": "0.1.0",
        "timestamp": timestamp,
        "artifacts": artifacts,
        "ci": _manifest_ci(out_dir),
        "source": {"kind": "local", "version": "0.1.0"},
    }


def _resolve_gate_binary() -> Path | None:
    """Locate the hermes-policy-gate binary, honouring a test override."""
    env_bin = os.environ.get("HERMES_GATE_BIN")
    if env_bin:
        env_path = Path(env_bin).resolve()
        if env_path.is_file():
            return env_path
    if _HAS_GATE_DISCOVERY:
        found = find_gate_binary()
        if found:
            return found
    repo_root = SKILL_DIR.parents[2]
    candidate = repo_root / "packages" / "gate" / "dist" / "bin.js"
    return candidate if candidate.is_file() else None


def _run_gate_binary(
    binary: Path,
    manifest_path: Path,
    head_sha: str,
    risk_level: str,
    changed_files: list[str],
    attempts: int,
    max_attempts: int,
    approval: dict | None = None,
) -> dict:
    """Invoke the policy-gate binary with the complete documented CLI vector."""
    if binary.suffix in (".js", ".mjs") or binary.name == "bin.js":
        cmd = ["node", str(binary)]
    elif binary.suffix == ".py":
        cmd = [sys.executable, str(binary)]
    else:
        cmd = [str(binary)]

    args = [
        "--manifest", str(manifest_path),
        "--head-sha", head_sha,
        "--policy-version", "0.1.0",
        "--risk", risk_level,
        "--attempts", str(attempts),
        "--max-attempts", str(max_attempts),
    ]
    if changed_files:
        args.extend(["--changed-files", ",".join(changed_files)])
    if approval:
        args.extend(["--approval", json.dumps(approval)])

    proc = subprocess.run(cmd + args, capture_output=True, text=True, timeout=120)

    parsed: dict = {}
    output_valid = True
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            output_valid = isinstance(parsed, dict)
        except json.JSONDecodeError:
            output_valid = False
            parsed = {"raw_stdout": proc.stdout.strip()[:500]}
    else:
        output_valid = False

    decision = "BLOCK"
    reason = "GATE_PROCESS_FAILED" if proc.returncode != 0 or not output_valid else "GATE_CONTRACT_INVALID"
    if proc.returncode in (0, 1) and isinstance(parsed, dict):
        required = parsed.get("requiredGates")
        risk_level = parsed.get("riskLevel")
        reason_code = parsed.get("reasonCode")
        detail = parsed.get("detail")
        contract_ok = (
            parsed.get("decision") in ("pass", "fail")
            and parsed.get("gate") in {"PASS", "REPAIR", "ESCALATE", "BLOCK"}
            and isinstance(reason_code, str) and reason_code.strip()
            and risk_level in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
            and isinstance(required, list) and len(required) > 0
            and all(isinstance(g, str) and g.strip() for g in required)
            and parsed.get("policyVersion") == "0.1.0"
            and isinstance(detail, str) and detail.strip()
        )
        semantic_ok = (
            (parsed.get("gate") == "PASS" and parsed.get("decision") == "pass"
             and parsed.get("reasonCode") == "PASS")
            or (parsed.get("gate") in {"REPAIR", "ESCALATE", "BLOCK"}
                and parsed.get("decision") == "fail")
        )
        exit_ok = (proc.returncode == 0 and parsed.get("gate") == "PASS") or (
            proc.returncode == 1 and parsed.get("gate") in {"REPAIR", "ESCALATE", "BLOCK"}
        )
        if contract_ok and semantic_ok and exit_ok:
            decision = str(parsed["gate"])
            reason = str(parsed["reasonCode"])
        elif proc.returncode in (0, 1) and output_valid:
            reason = "GATE_CONTRACT_INVALID"
    return {
        "decision": decision,
        "reason_code": reason,
        "risk_level": str(parsed.get("riskLevel", risk_level)),
        "required_gates": parsed.get("requiredGates"),
        "detail": str(parsed.get("detail", proc.stderr[:500] or "gate returned non-pass")),
        "raw": parsed,
    }


_INDEPENDENT_REVIEW_REQUIRED = [
    "executive_summary",
    "architecture_assessment",
    "findings",
    "missing_evidence",
    "priority_order",
]

_INDEPENDENT_FINDING_REQUIRED = [
    "id", "title", "severity", "confidence", "claim",
    "evidence_refs", "challenge_to_hermes", "recommendation", "verification",
]

_VALID_INDEPENDENT_SEVERITIES = {"low", "medium", "high", "critical"}


def _validate_independent_review(out_dir: Path, state: dict) -> dict | None:
    """Validate independent-review.json for HIGH/CRITICAL runs.

    Checks that the file parses, contains the expected schema fields, and is
    bound to the current run and head SHA (not merely a stale file on disk).
    """
    path = out_dir / "independent-review.json"
    if not path.is_file():
        return None
    try:
        review = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(review, dict):
        return None
    for key in _INDEPENDENT_REVIEW_REQUIRED:
        if key not in review:
            return None
    findings = review.get("findings")
    if not isinstance(findings, list):
        return None
    for f in findings:
        if not isinstance(f, dict):
            return None
        for key in _INDEPENDENT_FINDING_REQUIRED:
            if key not in f:
                return None
        if f.get("severity") not in _VALID_INDEPENDENT_SEVERITIES:
            return None

    trace_id = state.get("trace_id") or out_dir.name
    if review.get("trace_id") != trace_id:
        return None
    if review.get("head_sha") != state.get("commit_sha"):
        return None
    packet = out_dir / "external-review-packet.json"
    if not packet.is_file() or review.get("packet_sha256") != _sha256_file(packet):
        return None
    if not all(review.get(key) for key in ("reviewer_identity", "reviewer_provider", "reviewer_model", "invocation_id")):
        return None
    if review.get("review_mode") != "adversarial" or review.get("packet_sha256") != _sha256_file(packet):
        return None
    return review


def _set_gate_result(
    out_dir: Path,
    decision: str,
    detail: str,
    reason_code: str,
    final_risk: dict,
    extra: dict | None = None,
) -> dict:
    """Persist a terminal gate decision to policy-gate.json and state.json."""
    result: dict[str, Any] = {
        "decision": decision,
        "gate": decision,
        "reason": detail,
        "reason_code": reason_code,
        "risk_level": (final_risk.get("final_risk", "") or "").upper(),
        "final_risk": final_risk,
    }
    if extra:
        result.update(extra)
    _write_json(out_dir / "policy-gate.json", result)
    _save_state(out_dir, f"POLICY_{decision}", 95, result)
    _update_artifact(out_dir, "policy_gate", out_dir / "policy-gate.json")
    return result


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
            "evidence_generated_at": _read_json(out_dir / "repo-evidence.json").get("generated_at"),
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

    # Run state is needed to bind the independent review to this run/head SHA.
    state = _load_state(out_dir)
    trace_id = state.get("trace_id")
    head_sha = state.get("commit_sha", "")
    run_id = state.get("run_id") or out_dir.name

    if reviewer == "mock":
        mock_review = MOCK_REVIEW
        env_path = os.environ.get("HERMES_MOCK_REVIEW")
        if env_path and Path(env_path).is_file():
            mock_review = _read_json(Path(env_path))
        _write_json(review_path, mock_review)
    elif reviewer == "codex":
        args = ["--packet", str(packet), "--out", str(out_dir), "--timeout", "300"]
        if model:
            args.extend(["--model", model])
        _run_script("codex_review.py", *args, timeout=600)
    elif reviewer == "openai":
        _run_script(
            "openai_review.py",
            "--packet", str(packet),
            "--out", str(review_path),
            "--mode", "openai-api",
            "--model", model or "",
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
        if high_or_critical or independent_review:
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
                    "--out", str(indep_dir / "external-review.json"),
                    "--mode", "openai-api",
                    "--review-mode", "adversarial",
                    "--model", model or "",
                )
            else:
                # Mock output is never independent evidence.
                _write_json(indep_dir / "independent-review-invalid.json", {
                    "reason_code": "INDEPENDENT_REVIEW_INVALID",
                    "reviewer": "mock",
                })
            # Normalize to a single independent-review.json file
            src = indep_dir / "external-review.json"
            dst = out_dir / "independent-review.json"
            if src.is_file():
                indep = _read_json(src)
                # Binding is orchestrator-owned; reviewer provenance must come
                # from the adapter and is validated rather than self-asserted.
                indep["trace_id"] = trace_id
                indep["head_sha"] = head_sha
                indep["run_id"] = run_id
                _write_json(dst, indep)
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
        "note": "Real CI integration not yet implemented. Returning unknown; the policy gate will not pass on unverified CI.",
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

    # FINAL RISK must see every verification signal the pipeline produced, not
    # just CI. Passing only --test-results left review findings and security
    # findings invisible to the recalculation, so a CRITICAL reviewer finding
    # could not escalate risk and the run reached the policy gate understated.
    reconciled = _read_json_optional(out_dir / "reconciled-review.json")
    review_findings = reconciled.get("findings") or reconciled.get("reconciled") or []
    if isinstance(review_findings, dict):
        review_findings = list(review_findings.values())

    independent = _read_json_optional(out_dir / "independent-review.json")
    security_findings = [
        f for f in (independent.get("findings") or [])
        if str(f.get("category", "")).lower() in ("security", "secret", "vulnerability")
        or str(f.get("severity", "")).lower() == "critical"
    ]

    # Changed paths drive the sensitive-path escalation rules; an empty list
    # disabled that whole check.
    try:
        changed_paths = _actual_changed_files(out_dir)
    except ValueError:
        changed_paths = []

    result = _run_script(
        "final_risk.py",
        "--early-risk", early_risk,
        "--changed-paths", json.dumps(changed_paths),
        "--test-results", json.dumps(ci),
        "--review-findings", json.dumps(review_findings),
        "--security-findings", json.dumps(security_findings),
        "--out", str(out_dir / "final-risk.json"),
    )
    _save_state(out_dir, "FINAL_RISK_RECALCULATED", 88, result)
    _update_artifact(out_dir, "final_risk", out_dir / "final-risk.json")
    return result


def stage_policy_gate(out_dir: Path, attempts: int = 0, max_attempts: int = 3, dry_run: bool = False) -> dict:
    """Run the independent policy gate.

    HIGH/CRITICAL risk must have a valid, bound independent adversarial review
    artifact.  Unknown or unverified evidence must fail closed (BLOCK) rather
    than letting Hermes reconcile its own output.  The real gate binary is
    invoked with the complete documented CLI argument vector.
    """
    final_risk_path = out_dir / "final-risk.json"
    try:
        final_risk = _read_json(final_risk_path)
    except (OSError, json.JSONDecodeError) as exc:
        return _set_gate_result(
            out_dir, "BLOCK",
            f"final-risk.json missing or unreadable: {exc}",
            "RISK_EVIDENCE_INVALID", {},
        )

    raw_risk = final_risk.get("final_risk")
    if not _valid_risk(raw_risk):
        return _set_gate_result(
            out_dir, "BLOCK",
            f"final_risk {raw_risk!r} is not a valid canonical risk level",
            "RISK_EVIDENCE_INVALID", final_risk,
        )
    risk_level = raw_risk.upper()

    state = _load_state(out_dir)
    repo = Path(str(state.get("repo", "."))).resolve()
    head_sha = state.get("commit_sha", "")

    # Independent check: HIGH/CRITICAL requires a separate adversarial review.
    if risk_level in ("HIGH", "CRITICAL"):
        indep = _validate_independent_review(out_dir, state)
        if indep is None:
            return _set_gate_result(
                out_dir, "BLOCK",
                "HIGH/CRITICAL risk requires a valid, bound independent adversarial review",
                "INDEPENDENT_REVIEW_INVALID", final_risk,
            )

    try:
        actual_head = _git_output(repo, "rev-parse", "HEAD")
    except ValueError as exc:
        return _set_gate_result(out_dir, "BLOCK", str(exc), "HEAD_SHA_INVALID", final_risk)
    if actual_head != head_sha:
        return _set_gate_result(
            out_dir, "BLOCK", "repository HEAD changed after evidence collection",
            "HEAD_SHA_MISMATCH", final_risk, {"evidence_head_sha": head_sha, "head_sha": actual_head},
        )
    head_sha = actual_head

    if not re.match(r"^[0-9a-f]{40}$", head_sha):
        return _set_gate_result(
            out_dir, "BLOCK",
            f"commit_sha {head_sha!r} is not a 40-char lowercase hex SHA",
            "HEAD_SHA_INVALID", final_risk,
        )

    try:
        changed_files = _changed_files(out_dir, head_sha)
    except ValueError as exc:
        return _set_gate_result(out_dir, "BLOCK", str(exc), "CHANGED_FILES_INVALID", final_risk)
    try:
        manifest = _build_manifest(out_dir, state, risk_level, changed_files)
    except Exception as exc:
        return _set_gate_result(
            out_dir, "BLOCK",
            f"policy evidence manifest is invalid: {exc}",
            "MANIFEST_INVALID", final_risk,
        )
    manifest_path = out_dir / "policy-manifest.json"
    _write_json(manifest_path, manifest)

    binary = _resolve_gate_binary()
    if not binary:
        if dry_run:
            return _set_gate_result(
                out_dir, "BLOCK",
                "policy gate binary unavailable; dry-run is synthetic and cannot PASS",
                "GATE_UNAVAILABLE", final_risk,
                {"manifest_path": str(manifest_path), "synthetic_status": "UNAVAILABLE"},
            )
        return _set_gate_result(
            out_dir, "BLOCK",
            "policy gate binary not available",
            "GATE_UNAVAILABLE", final_risk,
            {"manifest_path": str(manifest_path)},
        )

    try:
        gate = _run_gate_binary(
            binary, manifest_path, head_sha, risk_level, changed_files,
            attempts, max_attempts,
        )
    except Exception as exc:
        return _set_gate_result(
            out_dir, "BLOCK",
            f"policy gate invocation failed: {exc}",
            "GATE_UNAVAILABLE", final_risk,
            {"manifest_path": str(manifest_path)},
        )

    return _set_gate_result(
        out_dir, gate["decision"], gate["detail"], gate["reason_code"], final_risk,
        {
            "risk_level": gate["risk_level"],
            "required_gates": gate["required_gates"],
            "head_sha": head_sha,
            "changed_files": changed_files,
            "manifest_path": str(manifest_path),
            "attempts": attempts,
            "max_attempts": max_attempts,
            "raw": gate.get("raw"),
        },
    )


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

    plan = _read_json_optional(out_dir / "task-plan.json", {"tasks": []})
    gate = _read_json_optional(out_dir / "policy-gate.json", {})

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

    # Do not overwrite a terminal non-PASS gate status (BLOCK/ESCALATE) with a
    # success-looking state.  PASS is still promoted to OUTCOME_COLLECTED.
    current_status = state.get("status", "")
    if current_status in ("POLICY_BLOCK", "POLICY_ESCALATE"):
        terminal_status = current_status
    else:
        terminal_status = "OUTCOME_COLLECTED"

    _save_state(out_dir, terminal_status, 100, metrics)
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
    parser.add_argument("--policy-gate-dry-run", action="store_true",
                        help="Explicitly allow dry-run policy-gate handling; it never grants a synthetic PASS")
    args = parser.parse_args()

    if args.policy_gate_dry_run and args.dispatch_mode != "dry-run":
        parser.error("--policy-gate-dry-run requires --dispatch-mode dry-run")

    repo = Path(args.repo).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Every invocation gets a fresh identity; output directories may be reused.
    run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}"
    os.environ["HERMES_TRACE_ID"] = run_id
    ownership_marker = out_dir / ".hermes-owned"
    if ownership_marker.is_file():
        for artifact in ("independent-review.json", "policy-gate.json", "policy-manifest.json", "outcome.json", "repair-result.json"):
            (out_dir / artifact).unlink(missing_ok=True)
        independent_dir = out_dir / "independent-review"
        if independent_dir.is_dir():
            shutil.rmtree(independent_dir)
    ownership_marker.write_text("hermes-open-design\n", encoding="ascii")

    # Initialize state
    _initialize_state(out_dir, run_id, repo)

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
        final_risk = stage_final_risk(out_dir)
        # Sensitive changed paths can escalate risk beyond primary review findings.
        if str(final_risk.get("final_risk", "")).upper() in ("HIGH", "CRITICAL"):
            stage_external_review(out_dir, args.reviewer, reviewer_model, independent_review=True)

        # 14. Repair budget
        repair_budget = RepairBudget(
            max_attempts=args.max_repair_attempts,
            max_duration_seconds=args.max_repair_duration,
            max_cost_usd=args.max_repair_cost,
        )

        # 15. Policy gate (initial)
        gate = stage_policy_gate(
            out_dir,
            attempts=0,
            max_attempts=repair_budget.max_attempts,
            dry_run=args.policy_gate_dry_run,
        )

        # 16. Repair loop (cost-bounded)
        while _load_state(out_dir).get("status") == "POLICY_REPAIR" and repair_budget.can_spend():
            stage_repair(out_dir, repair_budget)
            final_risk = stage_final_risk(out_dir)
            if str(final_risk.get("final_risk", "")).upper() in ("HIGH", "CRITICAL"):
                stage_external_review(out_dir, args.reviewer, reviewer_model, independent_review=True)
            gate = stage_policy_gate(
                out_dir,
                attempts=repair_budget.attempts,
                max_attempts=repair_budget.max_attempts,
                dry_run=args.policy_gate_dry_run,
            )

        # If budget was exhausted while the gate still wanted repair, escalate.
        if _load_state(out_dir).get("status") == "POLICY_REPAIR":
            final_risk = _read_json_optional(out_dir / "final-risk.json", {})
            _set_gate_result(
                out_dir, "ESCALATE",
                f"repair budget exhausted after {repair_budget.attempts} attempts",
                "REPAIR_BUDGET_EXHAUSTED", final_risk,
            )

        # 17. Outcome
        stage_outcome(out_dir, repair_budget)

        state = _load_state(out_dir)
        gate = _read_json_optional(out_dir / "policy-gate.json", {})
        decision = str(gate.get("decision", "")).upper()
        if decision in ("BLOCK", "ESCALATE") or state.get("status") in ("POLICY_BLOCK", "POLICY_ESCALATE"):
            print(json.dumps({
                "ok": False,
                "out_dir": str(out_dir),
                "state": str(out_dir / "state.json"),
                "final_status": state.get("status"),
                "gate_decision": decision,
                "synthetic_status": gate.get("synthetic_status"),
            }, indent=2), file=sys.stderr)
            return 1

        print(json.dumps({
            "ok": True,
            "out_dir": str(out_dir),
            "state": str(out_dir / "state.json"),
            "final_status": state.get("status"),
            "gate_decision": decision,
            "synthetic_status": gate.get("synthetic_status"),
        }, indent=2))
        return 0

    except Exception as exc:
        _save_state(out_dir, "FAILED", 0, {"error": str(exc)})
        print(json.dumps({"ok": False, "stage": _load_state(out_dir).get("status"), "error": str(exc)}, indent=2),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
