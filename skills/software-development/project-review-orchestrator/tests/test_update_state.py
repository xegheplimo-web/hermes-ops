#!/usr/bin/env python3
"""Unit tests: update_state.py — state machine must refuse illegal transitions."""
import json, subprocess, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "update_state.py"
sys.path.insert(0, str(SCRIPTS))
import update_state as us

PY = sys.executable
P, F = 0, 0
def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1; print(f"  [PASS] {name}")
    else:
        F += 1; print(f"  [FAIL] {name} | {detail}")

def run(args):
    p = subprocess.run([PY, str(SCRIPT), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

print("=" * 62)
print("  update_state.py — state machine")
print("=" * 62)

tmp = Path(tempfile.mkdtemp(prefix="state_"))

# --- pure function level ---
ok, _ = us._validate_transition("CREATED", "PREFLIGHT", has_error=False)
check("forward transition allowed", ok)

ok, why = us._validate_transition("PREFLIGHT", "CREATED", has_error=False)
check("backward transition refused", not ok, why)

ok, _ = us._validate_transition("CREATED", "TASKS_DECOMPOSED", has_error=False)
check("forward skip allowed", ok)

ok, why = us._validate_transition("COMPLETED", "PREFLIGHT", has_error=False)
check("cannot leave terminal COMPLETED", not ok, why)

ok, why = us._validate_transition("COMPLETED", "FAILED", has_error=False)
check("terminal cannot go to FAILED", not ok, why)

ok, _ = us._validate_transition("EVIDENCE_COLLECTED", "FAILED", has_error=True)
check("FAILED reachable from non-terminal", ok)

ok, _ = us._validate_transition("EXTERNAL_REVIEW_REQUESTED", us.PAUSE_STATE, has_error=False)
check("pause branch allowed from EXTERNAL_REVIEW_REQUESTED", ok)

ok, _ = us._validate_transition(us.PAUSE_STATE, "EXTERNAL_REVIEW_RECEIVED", has_error=False)
check("pause exits to EXTERNAL_REVIEW_RECEIVED", ok)

ok, why = us._validate_transition(us.PAUSE_STATE, "COMPLETED", has_error=False)
check("pause cannot jump straight to COMPLETED", not ok, why)

ok, why = us._validate_transition("BOGUS_STATE", "PREFLIGHT", has_error=False)
check("unknown current state refused", not ok, why)

ok, why = us._validate_transition("CREATED", "BOGUS_STATE", has_error=False)
check("unknown desired state refused", not ok, why)

ok, _ = us._validate_transition("PREFLIGHT", "PREFLIGHT", has_error=False)
check("self-transition allowed (idempotent)", ok)

# --- CLI level: illegal transition must exit non-zero ---
sf = tmp / "s1.json"
rc, out, err = run(["--state-file", str(sf), "--status", "PREFLIGHT"])
check("CLI first transition succeeds", rc == 0, f"rc={rc} {err[:90]}")

rc2, out2, err2 = run(["--state-file", str(sf), "--status", "CREATED"])
check("CLI backward transition exits non-zero", rc2 != 0, f"rc={rc2} out={out2[:120]}")

# state file must NOT have been corrupted by the refused transition
body = json.loads(sf.read_text(encoding="utf-8"))
check("refused transition left status unchanged",
      body.get("status") == "PREFLIGHT", body.get("status"))

# unknown status must be refused at CLI too
rc3, out3, err3 = run(["--state-file", str(sf), "--status", "NOT_A_STATE"])
check("CLI unknown status exits non-zero", rc3 != 0, f"rc={rc3}")

# --- full legal walk to COMPLETED, then verify it is locked ---
sf2 = tmp / "s2.json"
walk = ["PREFLIGHT", "EVIDENCE_COLLECTED", "HERMES_ANALYSIS_DONE", "PACKET_BUILT",
        "EXTERNAL_REVIEW_REQUESTED", "EXTERNAL_REVIEW_RECEIVED", "RECONCILED",
        "CODEMAP_BUILT", "TASKS_DECOMPOSED", "PLAN_READY_NOT_DISPATCHED",
        "DISPATCHED", "IN_PROGRESS", "COMPLETED"]
bad = []
for s in walk:
    rc_w, _, err_w = run(["--state-file", str(sf2), "--status", s])
    if rc_w != 0:
        bad.append((s, err_w[:70]))
check("full canonical walk to COMPLETED succeeds", not bad, str(bad))

rc4, _, _ = run(["--state-file", str(sf2), "--status", "DISPATCHED"])
check("COMPLETED is locked at CLI level", rc4 != 0, f"rc={rc4}")

# run_id present and stable across transitions
b2 = json.loads(sf2.read_text(encoding="utf-8"))
check("state carries a run_id", bool(b2.get("run_id")), str(b2.get("run_id")))

# terminal/pause sets are consistent with ORDERED_STATES
check("TERMINAL is subset of known states",
      us.TERMINAL <= set(us.ORDERED_STATES), str(us.TERMINAL))
check("PAUSE_STATE is not in the ordered ladder",
      us.PAUSE_STATE not in us.ORDERED_STATES, us.PAUSE_STATE)

print(f"\n  {P} passed, {F} failed")
print(f"  tmp: {tmp}")
sys.exit(1 if F else 0)
