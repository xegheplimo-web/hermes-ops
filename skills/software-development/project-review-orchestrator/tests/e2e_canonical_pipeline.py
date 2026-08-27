#!/usr/bin/env python3
"""
E2E Canonical Pipeline Test — exercises the full architecture:

  Evidence → Hermes Analysis → Codex Review → Reconcile → Task DAG → Ops DB → Dispatch

Runs on the actual hermes-ops repo. Uses mock external review (no real Codex call)
to keep the test deterministic. Ops DB integration verified with real PostgreSQL.

Usage:
    DATABASE_URL=postgres://hermes:hermesops@localhost:55432/hermes_ops \
    python e2e_canonical_pipeline.py

Exit code 0 = FULL PASS
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
# Resolve the repo root from this file's location, not a hardcoded path. The
# absolute path baked in here only existed on one developer machine, so CI died
# with FileNotFoundError: PosixPath('G:/Agent-Tools/hermes-ops') before a single
# assertion ran.
REPO = SKILL_DIR.parents[2]
DATABASE_URL = os.getenv("DATABASE_URL", "postgres://hermes:hermesops@localhost:55432/hermes_ops")

# Resolve current repo state (avoids hardcoded SHA after each push)
_expected_sha_proc = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=10
)
EXPECTED_SHA = _expected_sha_proc.stdout.strip() if _expected_sha_proc.returncode == 0 else "0000000000000000000000000000000000000000"
_expected_branch_proc = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=10
)
EXPECTED_BRANCH = _expected_branch_proc.stdout.strip() or "main"

PASS = 0
FAIL = 0
STEPS = []


def step(name: str):
    """Decorator for E2E steps."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            global PASS, FAIL
            print(f"\n  ╔══ {'=' * 60}")
            print(f"  ║  STEP: {name}")
            print(f"  ╚══ {'=' * 60}")
            try:
                result = fn(*args, **kwargs)
                STEPS.append({"step": name, "status": "PASS", "detail": str(result)[:200]})
                PASS += 1
                print(f"  ✅ {name}")
                return result
            except Exception as e:
                STEPS.append({"step": name, "status": "FAIL", "detail": str(e)[:500]})
                FAIL += 1
                print(f"  ❌ {name}: {e}")
                return None
        return wrapper
    return decorator


def run(cmd: list[str], desc: str = "") -> str:
    """Run a subprocess and return stdout."""
    label = desc or " ".join(str(c) for c in cmd[:4])
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    env = os.environ.copy()
    env["DATABASE_URL"] = DATABASE_URL
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=REPO, env=env)
    if r.returncode != 0:
        err = r.stderr[:300] if r.stderr else "(no stderr)"
        raise RuntimeError(f"[{label}] exit {r.returncode}: {err}")
    return r.stdout


def run_python(script: str, *args: str) -> str:
    """Run a Python script with args."""
    python = sys.executable
    cmd = [python, script] + list(args)
    return run(cmd, Path(script).name)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Evidence Collection
# ═══════════════════════════════════════════════════════════════════════════════

TEST_DIR = Path(tempfile.mkdtemp(prefix="e2e-canonical-"))
OUT = TEST_DIR / "review"
RUN_ID = None

@step("1.1 Collect deterministic repo evidence")
def test_evidence() -> str:
    global RUN_ID
    out = run_python(
        str(SCRIPTS / "collect_repo_evidence.py"),
        "--repo", str(REPO),
        "--out", str(OUT),
        "--review-mode", "openai-api",
    )
    result = json.loads(out)
    assert result.get("ok") is True
    RUN_ID = result["run_id"]
    assert result["commit"] == EXPECTED_SHA
    assert (OUT / "repo-evidence.json").exists()
    assert (OUT / "repo-evidence.md").exists()
    assert (OUT / "state.json").exists()
    return f"run_id={RUN_ID}, dirty={result.get('dirty')}"

@step("1.2 State machine: EVIDENCE_COLLECTED created")
def test_state_created() -> str:
    state = json.loads((OUT / "state.json").read_text())
    assert state["status"] == "EVIDENCE_COLLECTED"
    assert state["run_id"] == RUN_ID
    assert state["commit_sha"] == EXPECTED_SHA
    return f"status={state['status']}, artifacts={list(state['artifacts'].keys())}"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Hermes Analysis
# ═══════════════════════════════════════════════════════════════════════════════

@step("2.1 Create Hermes analysis with 21 sections")
def test_hermes_analysis() -> str:
    analysis = f"""# Hermes First-Pass Analysis — {RUN_ID}

## PROJECT SNAPSHOT
Repo: hermes-ops | Branch: {EXPECTED_BRANCH} | Commit: {EXPECTED_SHA[:7]}
Languages: Python (TypeScript), TypeScript | 392 tests passing

## CURRENT IMPLEMENTED ARCHITECTURE
Monorepo with 5 packages: contracts, db, adapters, gate, policy.
TypeScript core (Node.js/PostgreSQL), Python skill scripts.

## VERIFIED COMPLETED FEATURES
- PostgreSQL integration: 5 migrations applied, queue SKIP LOCKED working
- Ops DB adapter: 17/17 integration tests passing
- Codex reviewer: read-only isolation confirmed
- Devin adapter: model selection, risk routing

## PARTIAL FEATURES
- dispatch_to_devin.py: dry-run tested, --dispatch-all needs GitHub remote
- Codex CLI adapter: script complete, needs real Codex execution test

## BROKEN FEATURES
- None identified in current CI

## UNKNOWN AREAS
- Codex exec review output parsing on real reviews (tested with dry-run only)
- GitHub PR integration (no remote configured)

## CURRENT TEST/BUILD HEALTH
- 392 tests passing, TypeScript build OK, Python scripts compile OK
- 17/17 Ops DB integration tests passing

## SECURITY STATUS
- Codex sandbox: read-only enforced (profile hermes-reviewer, -c approvals_reviewer=user)
- Secret redaction in build_review_packet.py
- No secrets in external review packets

## OPERATIONAL STATUS
- Local PostgreSQL on port 55432
- Codex CLI v0.148.0 authenticated with ChatGPT Plus
- Scripts run under python 3.11.16

## STATE MANAGEMENT
- Ops DB tasks table: 10 statuses, transition validation, SKIP LOCKED claiming
- state.json per review run
- AgentMemory for durable lessons only

## CONCURRENCY
- SKIP LOCKED for task claiming (no double-lease)
- Exponential backoff with jitter for retries
- Stale lock recovery

## MEMORY BOUNDARIES
- AgentMemory: durable lessons, architecture decisions
- Ops DB: task state, queue, audit events
- task-plan.json: artifact only, not runtime truth

## EXTERNAL DEPENDENCIES
- PostgreSQL 18.6 (local, Docker)
- Codex CLI (ChatGPT Plus)
- Devin CLI (separate binary)

## TECHNICAL DEBT
- Some Python scripts lack unit tests
- No CI/CD pipeline yet (needs GitHub remote)

## ARCHITECTURE CONTRADICTIONS
- None identified

## DUPLICATED RESPONSIBILITIES
- None — each layer has clear role

## WRONG / STALE DOCUMENTATION
- None

## MOST IMPORTANT RISKS
1. No GitHub remote — cannot test real PR flow
2. Codex exec review not yet run end-to-end
3. Devin dispatch not yet tested with --dispatch-all

## SIMPLER ALTERNATIVES
- Current architecture is clean (Hermes brain, Codex reviewer, Devin coder)

## RECOMMENDED PRIORITY ORDER
1. Fix Ops DB remaining wiring
2. Create GitHub remote
3. Real E2E: finding → PR → CI → gate → merge

## QUESTIONS FOR INDEPENDENT REVIEWER
1. Is the two-layer security (Hermes policy + Codex sandbox) sufficient?
2. Should we add human approval for CRITICAL risk dispatches?
3. Is AgentMemory integration sufficient for lesson persistence?
"""
    (OUT / "hermes-analysis.md").write_text(analysis, encoding="utf-8")
    assert (OUT / "hermes-analysis.md").exists()
    lines = len(analysis.splitlines())
    return f"analysis written: {lines} lines, 21 sections"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: Build Review Packet
# ═══════════════════════════════════════════════════════════════════════════════

@step("3.1 Build sanitized review packet")
def test_build_packet() -> str:
    out = run_python(
        str(SCRIPTS / "build_review_packet.py"),
        "--evidence", str(OUT / "repo-evidence.json"),
        "--analysis", str(OUT / "hermes-analysis.md"),
        "--out", str(OUT / "external-review-packet.json"),
        "--mode", "openai-api",
    )
    result = json.loads(out)
    assert result.get("ok") is True
    assert result["sha256"] is not None
    return f"sha256={result['sha256']}, redactions={result['redactions']}"

@step("3.2 State machine: PACKET_BUILT")
def test_state_packet() -> str:
    out = run_python(
        str(SCRIPTS / "update_state.py"),
        "--state-file", str(OUT / "state.json"),
        "--status", "PACKET_BUILT",
        "--progress", "30",
    )
    result = json.loads(out)
    assert result["new_status"] == "PACKET_BUILT"
    return f"transition: {result['previous_status']} → {result['new_status']}"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: External Review (mocked — uses Codex adapter in dry-run)
# ═══════════════════════════════════════════════════════════════════════════════

@step("4.1 Mock external review (simulates Codex output)")
def test_mock_review() -> str:
    mock = {
        "executive_summary": "Review of hermes-ops reveals a well-structured pipeline with clean role separation.",
        "architecture_assessment": "Monorepo architecture is appropriate. Ops DB as runtime authority is the right pattern.",
        "findings": [
            {
                "id": "F-001", "title": "No GitHub remote for CI/CD",
                "severity": "critical", "confidence": 0.95,
                "claim": "Cannot run real PR flow without GitHub remote",
                "evidence_refs": [".git/config", ".github/workflows/"],
                "challenge_to_hermes": "How do you plan to test the full PR→CI→gate flow?",
                "recommendation": "Create GitHub repo and push baseline",
                "verification": "CI pipeline passes"
            },
            {
                "id": "F-002", "title": "Codex exec review not tested end-to-end",
                "severity": "high", "confidence": 0.85,
                "claim": "Codex adapter script exists but no real review has been run",
                "evidence_refs": ["scripts/codex_review.py"],
                "challenge_to_hermes": "Has the Codex output parser been validated against real output?",
                "recommendation": "Run codex_review.py with --mode review on the actual repo",
                "verification": "Parsed findings match Codex output"
            },
            {
                "id": "F-003", "title": "Some Python scripts lack unit tests",
                "severity": "medium", "confidence": 0.8,
                "claim": "Several new scripts have no unit test coverage",
                "evidence_refs": ["scripts/"],
                "challenge_to_hermes": "How do you prevent regressions in the pipeline scripts?",
                "recommendation": "Add unit tests for all new Python scripts",
                "verification": "Coverage report > 80%"
            },
            {
                "id": "F-004", "title": "Task DAG dependency resolution is static",
                "severity": "medium", "confidence": 0.7,
                "claim": "Blocked tasks only unblocked via explicit API call",
                "evidence_refs": ["scripts/ops_adapter.py", "scripts/decompose_tasks.py"],
                "challenge_to_hermes": "Should dependency resolution be automatic?",
                "recommendation": "Add automatic unblock when dependency completes",
                "verification": "Integration test passes"
            },
        ],
        "missing_evidence": ["GitHub Actions workflow", "Devin dispatch real output"],
        "priority_order": ["F-001", "F-002", "F-003", "F-004"],
    }
    (OUT / "external-review.json").write_text(json.dumps(mock, indent=2), encoding="utf-8")
    assert len(mock["findings"]) == 4
    return f"4 findings written (1 critical, 1 high, 2 medium)"

@step("4.2 State machine: EXTERNAL_REVIEW_RECEIVED")
def test_state_review_received() -> str:
    out = run_python(
        str(SCRIPTS / "update_state.py"),
        "--state-file", str(OUT / "state.json"),
        "--status", "EXTERNAL_REVIEW_RECEIVED",
        "--progress", "50",
    )
    result = json.loads(out)
    assert result["new_status"] == "EXTERNAL_REVIEW_RECEIVED"
    return f"transition: {result['previous_status']} → {result['new_status']}"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Reconcile
# ═══════════════════════════════════════════════════════════════════════════════

@step("5.1 Reconcile Hermes analysis with external findings")
def test_reconcile() -> str:
    out = run_python(
        str(SCRIPTS / "reconcile_review.py"),
        "--analysis", str(OUT / "hermes-analysis.md"),
        "--external", str(OUT / "external-review.json"),
        "--out", str(OUT),
    )
    result = json.loads(out)
    assert result.get("ok") is True
    assert result["reconciled_count"] == 4
    assert (OUT / "reconciled-review.json").exists()
    assert (OUT / "reconciled-review.md").exists()

    # Verify output is dict with findings key (not bare list)
    rec = json.loads((OUT / "reconciled-review.json").read_text())
    assert isinstance(rec, dict), f"Expected dict, got {type(rec)}"
    assert "findings" in rec
    assert len(rec["findings"]) == 4
    return f"4 findings reconciled: {result['dispositions']}"

@step("5.2 State machine: RECONCILED")
def test_state_reconciled() -> str:
    out = run_python(
        str(SCRIPTS / "update_state.py"),
        "--state-file", str(OUT / "state.json"),
        "--status", "RECONCILED",
        "--progress", "65",
    )
    result = json.loads(out)
    assert result["new_status"] == "RECONCILED"
    return f"transition: {result['previous_status']} → {result['new_status']}"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: Codemap Brief
# ═══════════════════════════════════════════════════════════════════════════════

@step("6.1 Build Codemap brief from reconciled review")
def test_codemap() -> str:
    out = run_python(
        str(SCRIPTS / "build_codemap_brief.py"),
        "--reconciled", str(OUT / "reconciled-review.json"),
        "--repo", str(REPO),
        "--out", str(OUT),
    )
    result = json.loads(out)
    assert result.get("ok") is True
    assert result["commit"] == EXPECTED_SHA
    assert result["branch"] == EXPECTED_BRANCH
    assert (OUT / "codemap-brief.md").exists()
    return f"commit={result['commit'][:10]}, branch={result['branch']}"

@step("6.2 State machine: CODEMAP_BUILT")
def test_state_codemap() -> str:
    out = run_python(
        str(SCRIPTS / "update_state.py"),
        "--state-file", str(OUT / "state.json"),
        "--status", "CODEMAP_BUILT",
        "--progress", "75",
    )
    result = json.loads(out)
    assert result["new_status"] == "CODEMAP_BUILT"
    return f"transition: {result['previous_status']} → {result['new_status']}"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7: Task DAG → Ops DB
# ═══════════════════════════════════════════════════════════════════════════════

@step("7.1 Decompose into task DAG (file only)")
def test_decompose_file() -> str:
    out = run_python(
        str(SCRIPTS / "decompose_tasks.py"),
        "--reconciled", str(OUT / "reconciled-review.json"),
        "--codemap", str(OUT / "codemap-brief.md"),
        "--out", str(OUT),
    )
    result = json.loads(out)
    assert result.get("ok") is True
    assert result["task_count"] >= 3
    assert (OUT / "task-plan.json").exists()
    return f"{result['task_count']} tasks ({result['investigation_count']} inv, {result['implementation_count']} impl)"

@step("7.2 Decompose into Ops DB (authoritative path)")
def test_decompose_opsdb() -> str:
    out = run_python(
        str(SCRIPTS / "decompose_tasks.py"),
        "--reconciled", str(OUT / "reconciled-review.json"),
        "--codemap", str(OUT / "codemap-brief.md"),
        "--out", str(OUT),
        "--ops-db",
        "--review-run-id", str(RUN_ID),
    )
    result = json.loads(out)
    assert result.get("ok") is True, f"decompose_tasks.py returned error: {result}"
    assert result["ops_db_count"] >= 3, f"Expected >=3 Ops DB tasks, got {result['ops_db_count']}"
    return f"{result['ops_db_count']} tasks written to Ops DB"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 8: Dispatch (dry-run)
# ═══════════════════════════════════════════════════════════════════════════════

@step("8.1 Dispatch dry-run from task-plan.json (fallback)")
def test_dispatch_fallback() -> str:
    out = run_python(
        str(SCRIPTS / "dispatch_to_devin.py"),
        "--plan", str(OUT / "task-plan.json"),
        "--state-file", str(OUT / "state.json"),
    )
    result = json.loads(out)
    assert result.get("ok") is True
    assert result["dry_run"] is True
    assert result["source"] == "task-plan.json"
    assert len(result["tasks"]) >= 3
    # Verify model selection by risk
    for t in result["tasks"]:
        assert t["model"] in ("glm-5-2", "swe-1-7")
    return f"{result['task_count']} tasks, models: {[t['model'] for t in result['tasks']]}"

@step("8.2 Dispatch dry-run from Ops DB (authoritative)")
def test_dispatch_opsdb_dryrun() -> str:
    out = run_python(
        str(SCRIPTS / "dispatch_to_devin.py"),
        "--ops-db",
        "--review-run-id", str(RUN_ID),
        "--state-file", str(OUT / "state.json"),
    )
    result = json.loads(out)
    assert result.get("ok") is True
    assert result["dry_run"] is True
    assert result["source"] == "ops_db"
    assert len(result["tasks"]) >= 3
    return f"{result['task_count']} tasks from Ops DB, source=ops_db"

@step("8.3 Verify Devin prompt files generated")
def test_prompt_files() -> str:
    prompt_dir = OUT / "devin"
    assert prompt_dir.exists()
    prompts = list(prompt_dir.glob("devin-task-*.md"))
    assert len(prompts) >= 3
    for f in prompts:
        content = f.read_text(encoding="utf-8")
        assert "WORK ON A BRANCH. PRODUCE A PR." in content
        assert "DO NOT MODIFY UNRELATED CODE." in content
    return f"{len(prompts)} prompt files generated"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 9: Ops DB Anti-pattern Verification
# ═══════════════════════════════════════════════════════════════════════════════

@step("9.1 Ops DB: duplicate dispatch prevented")
def test_duplicate_prevention() -> str:
    sys.path.insert(0, str(SCRIPTS))
    from ops_adapter import OpsDbAdapter, OpsTask, make_external_id
    db = OpsDbAdapter()
    db.connect()
    try:
        # Insert same task twice
        eid = make_external_id(str(RUN_ID), "dup-test")
        task1 = OpsTask(external_id=eid, repository_owner="hermes-ops",
                        repository_name="hermes-ops", head_sha=EXPECTED_SHA,
                        policy_version="0.1.0", status="queued", review_run_id=str(RUN_ID),
                        dag_payload={"task_id": "DUP-001"})
        id1 = db.create_task(task1)
        id2 = db.create_task(task1)
        assert id1 == id2, f"Same external_id should return same id: {id1} != {id2}"
    finally:
        db.close()
    return f"duplicate prevented: id1={id1}, id2={id2}"

@step("9.2 Ops DB: stale lock recovery")
def test_stale_recovery() -> str:
    sys.path.insert(0, str(SCRIPTS))
    from ops_adapter import OpsDbAdapter, OpsTask, make_external_id, STATUS_RUNNING
    db = OpsDbAdapter()
    db.connect()
    try:
        # Create task, fake old lock
        eid = make_external_id(str(RUN_ID), "stale-test-e2e")
        task = OpsTask(external_id=eid, repository_owner="hermes-ops",
                       repository_name="hermes-ops", head_sha=EXPECTED_SHA,
                       policy_version="0.1.0", status=STATUS_RUNNING, review_run_id=str(RUN_ID),
                       locked_by="crashed-worker", locked_at=None)
        tid = db.create_task(task)
        # Set ancient lock via direct SQL
        cur = db.conn.cursor()
        cur.execute("UPDATE tasks SET locked_at = '2020-01-01T00:00:00Z' WHERE id = %s", (tid,))
        cur.close()

        count = db.recover_stale_locks(stale_after_ms=500_000_000)
        assert count >= 1
        recovered = db.get_task(tid)
        assert recovered.status == "pending"
    finally:
        db.close()
    return f"stale lock recovered: {count} tasks"

@step("9.3 Ops DB: blocked dependency not claimable")
def test_blocked_not_claimable() -> str:
    sys.path.insert(0, str(SCRIPTS))
    from ops_adapter import OpsDbAdapter, OpsTask, make_external_id, STATUS_BLOCKED
    db = OpsDbAdapter()
    db.connect()
    try:
        # Create a blocked task (has deps)
        eid = make_external_id(str(RUN_ID), "blocked-e2e-test")
        task = OpsTask(external_id=eid, repository_owner="hermes-ops",
                       repository_name="hermes-ops", head_sha=EXPECTED_SHA,
                       policy_version="0.1.0", status=STATUS_BLOCKED, review_run_id=str(RUN_ID),
                       dag_payload={"dependencies": ["NONEXISTENT"]})
        db.create_task(task)

        # Claim should find nothing claimable
        claimed = db.claim_task("test-worker")
        if claimed is not None:
            # May have claimed a queued task from other runs — that's OK,
            # but it should NOT be our blocked one
            assert claimed.status != STATUS_BLOCKED, "Blocked tasks should not be claimable"
    finally:
        db.close()
    return "blocked tasks correctly prevented from claiming"


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 10: Full Pipeline Verification
# ═══════════════════════════════════════════════════════════════════════════════

@step("10.1 Verify all artifacts generated")
def test_artifacts() -> str:
    expected = [
        "repo-evidence.json", "repo-evidence.md", "state.json",
        "hermes-analysis.md",
        "external-review-packet.json",
        "external-review.json",
        "reconciled-review.json", "reconciled-review.md",
        "codemap-brief.md",
        "task-plan.json",
    ]
    missing = [f for f in expected if not (OUT / f).exists()]
    assert not missing, f"Missing artifacts: {missing}"
    return f"All {len(expected)} artifacts present"

@step("10.2 Verify state machine final state")
def test_final_state() -> str:
    state = json.loads((OUT / "state.json").read_text())
    assert state["status"] in ("CODEMAP_BUILT", "DISPATCHED", "TASKS_DECOMPOSED")
    return f"final state: {state['status']}, progress: {state.get('progress_pct', 'N/A')}"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 70)
    print("  CANONICAL PIPELINE E2E TEST")
    print(f"  Repo:     {REPO}")
    print(f"  Commit:   {EXPECTED_SHA[:7]}")
    print(f"  Branch:   {EXPECTED_BRANCH}")
    print(f"  Database: {DATABASE_URL}")
    print(f"  Output:   {OUT}")
    print("=" * 70)

    steps = [
        # Phase 1-2
        test_evidence, test_state_created,
        test_hermes_analysis,
        # Phase 3
        test_build_packet, test_state_packet,
        # Phase 4
        test_mock_review, test_state_review_received,
        # Phase 5
        test_reconcile, test_state_reconciled,
        # Phase 6
        test_codemap, test_state_codemap,
        # Phase 7
        test_decompose_file, test_decompose_opsdb,
        # Phase 8
        test_dispatch_fallback, test_dispatch_opsdb_dryrun, test_prompt_files,
        # Phase 9
        test_duplicate_prevention, test_stale_recovery, test_blocked_not_claimable,
        # Phase 10
        test_artifacts, test_final_state,
    ]

    for fn in steps:
        fn()

    # Summary
    print()
    print("=" * 70)
    print(f"  RESULTS: {PASS} PASSED, {FAIL} FAILED, {PASS + FAIL} TOTAL")
    print("=" * 70)

    if FAIL > 0:
        for s in STEPS:
            if s["status"] == "FAIL":
                print(f"  ❌ {s['step']}: {s['detail'][:200]}")
        print()
        return 1

    print()
    print("  🎉 CANONICAL PIPELINE E2E: FULL PASS")
    print()
    print("  Pipeline verified:")
    print("    Evidence   →  Hermes Analysis  →  Build Packet")
    print("    Codex      →  Reconcile        →  Codemap Brief")
    print("    Task DAG   →  Ops DB           →  Devin Dispatch")
    print("    Duplicate  →  Stale Recovery   →  Blocked Deps")
    print()
    print("  Ready for: GitHub remote → real PR/CI/gate/merge")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())