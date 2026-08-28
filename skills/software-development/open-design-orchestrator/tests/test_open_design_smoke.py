#!/usr/bin/env python3
"""Smoke and policy-gate tests for open_design.py on a temp git repo."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PASS = 0
FAIL = 0
RESULTS = []


def test(fn):
    global PASS, FAIL
    RESULTS.append(fn.__name__)
    try:
        fn()
        PASS += 1
        print(f"  [PASS] {fn.__name__}")
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] {fn.__name__}: {e}")


# ── Setup temp git repo ─────────────────────────────────────────────────────
TMP = Path(tempfile.mkdtemp(prefix="open-design-test-"))
REPO = TMP / "test-repo"
REPO.mkdir()

subprocess.run(["git", "init"], cwd=REPO, capture_output=True)
subprocess.run(["git", "config", "user.email", "test@test"], cwd=REPO, capture_output=True)
subprocess.run(["git", "config", "user.name", "Test"], cwd=REPO, capture_output=True)

(REPO / "main.py").write_text("def hello(): return 1\n", encoding="utf-8")
(REPO / "README.md").write_text("# Test\n", encoding="utf-8")

subprocess.run(["git", "add", "-A"], cwd=REPO, capture_output=True)
subprocess.run(["git", "commit", "-m", "initial"], cwd=REPO, capture_output=True)
subprocess.run(["git", "remote", "add", "origin", "https://github.com/test-owner/test-repo.git"], cwd=REPO, capture_output=True)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "open_design.py"
OUT = TMP / "open-design-run"

# Import open_design as a module so direct unit tests can call its helpers.
_SPEC = importlib.util.spec_from_file_location("open_design", str(SCRIPT))
_open_design = importlib.util.module_from_spec(_SPEC)
sys.modules["open_design"] = _open_design
_SPEC.loader.exec_module(_open_design)


# ── Fake policy-gate binary ─────────────────────────────────────────────────

FAKE_GATE_SRC = r'''#!/usr/bin/env python3
"""Deterministic fake hermes-policy-gate for orchestrator tests."""
import argparse
import json
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--manifest")
parser.add_argument("--head-sha")
parser.add_argument("--policy-version")
parser.add_argument("--output")
parser.add_argument("--approval")
parser.add_argument("--changed-files")
parser.add_argument("--risk")
parser.add_argument("--attempts", type=int, default=0)
parser.add_argument("--max-attempts", type=int, default=3)
args = parser.parse_args()

outcome = os.environ.get("FAKE_GATE_OUTCOME", "PASS")
log_path = os.environ.get("FAKE_GATE_LOG")
manifest_dir = str(Path(args.manifest).parent) if args.manifest else ""

if outcome == "REPAIR_THEN_PASS":
    outcome = "REPAIR" if args.attempts == 0 else "PASS"
elif outcome == "REPAIR" and args.attempts >= args.max_attempts:
    outcome = "ESCALATE"

decision = "pass" if outcome == "PASS" else "fail"
risk = (args.risk or "MEDIUM").upper()
required = ["ci"]
if risk in ("HIGH", "CRITICAL"):
    required.append("codex")
if risk == "CRITICAL":
    required.append("human")

result = {
    "decision": decision,
    "gate": outcome,
    "reasonCode": "PASS" if outcome == "PASS" else outcome,
    "riskLevel": risk,
    "requiredGates": required,
    "policyVersion": args.policy_version or "0.1.0",
    "detail": f"fake gate outcome: {outcome}",
}

if log_path:
    with open(log_path, "a", encoding="utf-8") as f:
        entry = {
            "manifest_dir": manifest_dir,
            "attempts": args.attempts,
            "max_attempts": args.max_attempts,
            "risk": risk,
            "outcome": outcome,
        }
        f.write(json.dumps(entry, sort_keys=True) + "\n")

print(json.dumps(result, indent=2))
sys.exit(0 if outcome == "PASS" else 1)
'''


def _make_fake_gate(log_path: Path) -> Path:
    fake = TMP / "fake_gate.py"
    fake.write_text(FAKE_GATE_SRC, encoding="utf-8")
    return fake


def _run(
    out: Path,
    extra_args: list[str],
    env: dict,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(REPO), "--out", str(out), *extra_args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _run_mock(out: Path, gate_outcome: str, extra_args: list[str] | None = None, log: Path | None = None) -> subprocess.CompletedProcess:
    log = log or TMP / f"fake-gate-{out.name}.log"
    fake = _make_fake_gate(log)
    env = {
        "HERMES_GATE_BIN": str(fake),
        "FAKE_GATE_OUTCOME": gate_outcome,
        "FAKE_GATE_LOG": str(log),
    }
    args = ["--reviewer", "mock", "--skip-conflict-check", *(extra_args or [])]
    return _run(out, args, env)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── Test cases ──────────────────────────────────────────────────────────────

def test_cli_help():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "Open Design Orchestrator" in r.stdout
    assert "--policy-gate-dry-run" in r.stdout


def test_full_dry_run():
    out = TMP / "full-dry-run"
    log = TMP / "full-dry-run.log"
    r = _run_mock(out, "PASS", log=log)
    assert r.returncode == 0, f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
    state = _load_json(out / "state.json")
    assert state["status"] == "OUTCOME_COLLECTED", state
    assert state["progress"] == 100
    assert state["task_count"] >= 1
    assert (out / "task-plan.json").is_file()
    assert (out / "policy-gate.json").is_file()
    assert (out / "outcome.json").is_file()
    gate = _load_json(out / "policy-gate.json")
    assert gate["decision"] == "PASS", gate
    invocations = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(invocations) == 1, invocations


def test_stop_after():
    out = TMP / "stop-after"
    r = _run(out, ["--reviewer", "mock", "--skip-conflict-check", "--stop-after", "RECONCILED"], {})
    assert r.returncode == 0
    state = _load_json(out / "state.json")
    assert state["status"] == "RECONCILED", state


def test_gate_pass():
    out = TMP / "gate-pass"
    log = TMP / "gate-pass.log"
    r = _run_mock(out, "PASS", log=log)
    assert r.returncode == 0
    state = _load_json(out / "state.json")
    gate = _load_json(out / "policy-gate.json")
    assert state["status"] == "OUTCOME_COLLECTED", state
    assert gate["decision"] == "PASS", gate
    assert json.loads(r.stdout)["ok"] is True


def test_gate_block():
    out = TMP / "gate-block"
    log = TMP / "gate-block.log"
    r = _run_mock(out, "BLOCK", log=log)
    assert r.returncode == 1, f"stdout: {r.stdout[:500]}"
    state = _load_json(out / "state.json")
    gate = _load_json(out / "policy-gate.json")
    assert state["status"] == "POLICY_BLOCK", state
    assert gate["decision"] == "BLOCK", gate
    assert json.loads(r.stdout or r.stderr)["ok"] is False


def test_gate_escalate():
    out = TMP / "gate-escalate"
    log = TMP / "gate-escalate.log"
    r = _run_mock(out, "ESCALATE", log=log)
    assert r.returncode == 1
    state = _load_json(out / "state.json")
    gate = _load_json(out / "policy-gate.json")
    assert state["status"] == "POLICY_ESCALATE", state
    assert gate["decision"] == "ESCALATE", gate
    assert json.loads(r.stdout or r.stderr)["ok"] is False


def test_repair_then_pass():
    out = TMP / "repair-then-pass"
    log = TMP / "repair-then-pass.log"
    r = _run_mock(out, "REPAIR_THEN_PASS", log=log)
    assert r.returncode == 0, f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
    state = _load_json(out / "state.json")
    gate = _load_json(out / "policy-gate.json")
    assert state["status"] == "OUTCOME_COLLECTED", state
    assert gate["decision"] == "PASS", gate
    invocations = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(invocations) == 2, invocations
    assert _load_json(out / "outcome.json")["repair_attempts"] >= 1


def test_repair_budget_exhausted():
    out = TMP / "repair-exhausted"
    log = TMP / "repair-exhausted.log"
    r = _run_mock(out, "REPAIR", ["--max-repair-attempts", "1"], log=log)
    assert r.returncode == 1
    state = _load_json(out / "state.json")
    gate = _load_json(out / "policy-gate.json")
    assert state["status"] == "POLICY_ESCALATE", state
    assert gate["decision"] == "ESCALATE", gate
    invocations = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(invocations) == 2, invocations


def test_high_critical_missing_independent():
    out = TMP / "high-missing-independent"
    mock_review = TMP / "mock-high.json"
    mock_review.write_text(json.dumps({
        "executive_summary": "High risk mock review.",
        "architecture_assessment": "Mock.",
        "findings": [
            {
                "id": "HIGH-001",
                "title": "High risk finding",
                "severity": "high",
                "confidence": 0.9,
                "claim": "A high risk issue.",
                "evidence_refs": ["main.py"],
                "challenge_to_hermes": "Challenge.",
                "recommendation": "Fix.",
                "verification": "Verify.",
            }
        ],
        "missing_evidence": [],
        "priority_order": ["HIGH-001"],
    }, ensure_ascii=False), encoding="utf-8")
    log = TMP / "high-missing-independent.log"
    fake = _make_fake_gate(log)
    env = {
        "HERMES_GATE_BIN": str(fake),
        "FAKE_GATE_OUTCOME": "PASS",
        "FAKE_GATE_LOG": str(log),
        "HERMES_MOCK_REVIEW": str(mock_review),
    }
    r = _run(out, ["--reviewer", "mock", "--skip-conflict-check"], env)
    assert r.returncode == 1, f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
    state = _load_json(out / "state.json")
    gate = _load_json(out / "policy-gate.json")
    assert state["status"] == "POLICY_BLOCK", state
    assert gate["decision"] == "BLOCK", gate
    assert gate["reason_code"] == "INDEPENDENT_REVIEW_INVALID", gate
    assert not (out / "independent-review.json").is_file()
    invocations = log.read_text(encoding="utf-8").strip().splitlines() if log.is_file() else []
    assert len(invocations) == 0, "gate should not be called when independent review is missing"


def test_high_critical_with_independent():
    out = TMP / "high-with-independent"
    mock_review = TMP / "mock-high2.json"
    mock_review.write_text(json.dumps({
        "executive_summary": "High risk mock review.",
        "architecture_assessment": "Mock.",
        "findings": [
            {
                "id": "HIGH-002",
                "title": "High risk finding",
                "severity": "high",
                "confidence": 0.9,
                "claim": "A high risk issue.",
                "evidence_refs": ["main.py"],
                "challenge_to_hermes": "Challenge.",
                "recommendation": "Fix.",
                "verification": "Verify.",
            }
        ],
        "missing_evidence": [],
        "priority_order": ["HIGH-002"],
    }, ensure_ascii=False), encoding="utf-8")
    log = TMP / "high-with-independent.log"
    fake = _make_fake_gate(log)
    env = {
        "HERMES_GATE_BIN": str(fake),
        "FAKE_GATE_OUTCOME": "PASS",
        "FAKE_GATE_LOG": str(log),
        "HERMES_MOCK_REVIEW": str(mock_review),
    }
    r = _run(out, ["--reviewer", "mock", "--skip-conflict-check", "--independent-review"], env)
    assert r.returncode == 1, f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
    gate = _load_json(out / "policy-gate.json")
    assert gate["decision"] == "BLOCK", gate
    assert gate["reason_code"] == "INDEPENDENT_REVIEW_INVALID", gate
    assert not (out / "independent-review.json").is_file()
    invocations = log.read_text(encoding="utf-8").strip().splitlines() if log.is_file() else []
    assert len(invocations) == 0, invocations


def test_invalid_risk_evidence_blocks():
    out = TMP / "invalid-risk"
    out.mkdir(parents=True, exist_ok=True)
    (out / "final-risk.json").write_text(json.dumps({"final_risk": "unknown"}), encoding="utf-8")
    (out / "task-plan.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
    (out / "state.json").write_text(json.dumps({
        "run_id": out.name,
        "trace_id": out.name,
        "commit_sha": "0" * 40,
        "repo": str(REPO),
        "stage_durations": {},
        "status": "FINAL_RISK_RECALCULATED",
    }), encoding="utf-8")
    (out / "ci-findings.json").write_text(json.dumps({"ci_status": "unknown"}), encoding="utf-8")
    result = _open_design.stage_policy_gate(out)
    assert result["decision"] == "BLOCK"
    assert result["reason_code"] == "RISK_EVIDENCE_INVALID"


def test_independent_review_invalid_binding():
    out = TMP / "invalid-binding"
    out.mkdir(parents=True, exist_ok=True)
    (out / "final-risk.json").write_text(json.dumps({"final_risk": "HIGH"}), encoding="utf-8")
    (out / "task-plan.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
    (out / "state.json").write_text(json.dumps({
        "run_id": out.name,
        "trace_id": out.name,
        "commit_sha": "0" * 40,
        "repo": str(REPO),
        "stage_durations": {},
        "status": "FINAL_RISK_RECALCULATED",
    }), encoding="utf-8")
    (out / "ci-findings.json").write_text(json.dumps({"ci_status": "unknown"}), encoding="utf-8")
    (out / "independent-review.json").write_text(json.dumps({
        "executive_summary": "x",
        "architecture_assessment": "x",
        "findings": [
            {
                "id": "I-1",
                "title": "t",
                "severity": "high",
                "confidence": 0.9,
                "claim": "c",
                "evidence_refs": [],
                "challenge_to_hermes": "c",
                "recommendation": "r",
                "verification": "v",
            }
        ],
        "missing_evidence": [],
        "priority_order": ["I-1"],
        "trace_id": "wrong-trace",
        "head_sha": "0" * 40,
    }), encoding="utf-8")
    result = _open_design.stage_policy_gate(out)
    assert result["decision"] == "BLOCK"
    assert result["reason_code"] == "INDEPENDENT_REVIEW_INVALID"


def test_gate_invocation_error_fail_closed():
    """A gate binary that returns an operational error must not silently PASS."""
    out = TMP / "gate-error"
    bad_gate = TMP / "bad_gate.py"
    bad_gate.write_text("print('not json')\nimport sys; sys.exit(2)\n", encoding="utf-8")
    log = TMP / "gate-error.log"
    env = {
        "HERMES_GATE_BIN": str(bad_gate),
        "FAKE_GATE_LOG": str(log),
    }
    r = _run(out, ["--reviewer", "mock", "--skip-conflict-check"], env)
    assert r.returncode == 1, f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
    state = _load_json(out / "state.json")
    gate = _load_json(out / "policy-gate.json")
    assert state["status"] == "POLICY_BLOCK", state
    assert gate["decision"] == "BLOCK", gate


def test_gate_nonzero_pass_stdout_is_blocked():
    out = TMP / "gate-nonzero-pass"
    fake = TMP / "pass_then_error.py"
    fake.write_text("import json, sys; print(json.dumps({'decision':'pass','gate':'PASS','reasonCode':'PASS','riskLevel':'LOW','requiredGates':[],'policyVersion':'0.1.0','detail':'fake'})); sys.exit(1)\n", encoding="utf-8")
    r = _run(out, ["--reviewer", "mock", "--skip-conflict-check"], {"HERMES_GATE_BIN": str(fake)})
    assert r.returncode == 1
    gate = _load_json(out / "policy-gate.json")
    assert gate["decision"] == "BLOCK"


def test_gate_exit_zero_semantic_contradiction_is_blocked():
    out = TMP / "gate-contradictory-pass"
    fake = TMP / "contradictory_gate.py"
    fake.write_text("import json; print(json.dumps({'decision':'fail','gate':'PASS','reasonCode':'PASS','riskLevel':'LOW','requiredGates':[],'policyVersion':'0.1.0','detail':'contradiction'}))\n", encoding="utf-8")
    r = _run(out, ["--reviewer", "mock", "--skip-conflict-check"], {"HERMES_GATE_BIN": str(fake)})
    assert r.returncode == 1
    gate = _load_json(out / "policy-gate.json")
    assert gate["decision"] == "BLOCK"
    assert gate["reason_code"] == "GATE_CONTRACT_INVALID"


def test_real_gate_unknown_ci_cannot_pass():
    out = TMP / "real-gate-unknown-ci"
    r = _run(out, ["--reviewer", "mock", "--skip-conflict-check"], {"HERMES_GATE_BIN": ""})
    assert r.returncode == 1, f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
    gate = _load_json(out / "policy-gate.json")
    assert gate["decision"] != "PASS", gate


def test_policy_gate_dry_run_rejects_real_dispatch():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(REPO), "--out", str(TMP / "dry-dispatch"),
         "--dispatch-mode", "dispatch", "--policy-gate-dry-run"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 2
    assert "requires --dispatch-mode dry-run" in r.stderr


def main():
    print("=" * 60)
    print("  Open Design Orchestrator Smoke Tests")
    print("=" * 60)

    tests = [fn for name, fn in inspect.getmembers(sys.modules[__name__])
             if name.startswith("test_") and callable(fn)]
    for fn in tests:
        test(fn)

    print()
    print("=" * 60)
    print(f"  RESULTS: {PASS} PASSED, {FAIL} FAILED, {PASS+FAIL} TOTAL")
    print("=" * 60)

    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
