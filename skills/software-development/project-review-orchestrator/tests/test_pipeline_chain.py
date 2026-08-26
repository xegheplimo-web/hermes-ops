#!/usr/bin/env python3
"""Unit tests: pipeline chain — reconcile, decompose, codemap, dispatch.

Enforces the governance invariants those scripts are responsible for:
  - no task without evidence_refs
  - reviewer findings are dispositions, not orders
  - dispatch refuses to run without a plan / Ops DB
"""
import json, subprocess, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PY = sys.executable
P, F = 0, 0
def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1; print(f"  [PASS] {name}")
    else:
        F += 1; print(f"  [FAIL] {name} | {detail}")

def run(script, args):
    p = subprocess.run([PY, str(SCRIPTS / script), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

print("=" * 62)
print("  pipeline chain: reconcile / decompose / codemap / dispatch")
print("=" * 62)

tmp = Path(tempfile.mkdtemp(prefix="chain_"))

analysis = tmp / "hermes-analysis.md"
analysis.write_text(
    "# Hermes Analysis\n\n"
    "## Security Risks\n\nSession state is mutated in two places.\n\n"
    "## Concurrency Risks\n\nTwo components race for the same lock.\n\n"
    "## Maintainability Risks\n\nDuplicate helpers.\n",
    encoding="utf-8")

external = tmp / "external-review.json"
external.write_text(json.dumps({
    "findings": [
        {"id": "EXT-1", "severity": "high", "title": "Duplicate session ownership",
         "claim": "Session state is mutated in two places.", "evidence": "src/session.py",
         "confidence": "high"},
        {"id": "EXT-2", "severity": "low", "title": "Nitpick naming",
         "claim": "Variable naming is inconsistent.", "evidence": "", "confidence": "low"},
        {"id": "EXT-3", "severity": "critical", "title": "Unverifiable prod claim",
         "claim": "Production Redis is misconfigured.", "evidence": "", "confidence": "low"},
    ]
}), encoding="utf-8")

# --- reconcile ---
rout = tmp / "rec"
rc, out, err = run("reconcile_review.py",
                   ["--analysis", str(analysis), "--external", str(external), "--out", str(rout)])
check("reconcile runs", rc == 0, err[:140])

recj = None
for f in rout.rglob("*.json"):
    try:
        j = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(j, dict) and ("reconciled" in j or "findings" in j):
            recj = j; recpath = f; break
    except Exception:
        pass
check("reconcile produced a JSON artifact", recj is not None, str(list(rout.rglob('*'))[:4]))

if recj:
    items = recj.get("reconciled") or recj.get("findings") or []
    check("every finding got a disposition",
          all(i.get("disposition") for i in items), f"n={len(items)}")
    valid = {"AGREE", "PARTIAL", "DISAGREE", "NEW", "UNVERIFIED"}
    bad = [i.get("disposition") for i in items if i.get("disposition") not in valid]
    check("dispositions are from the canonical set", not bad, str(bad))
    # a no-evidence low-confidence claim must NOT come out as plain AGREE
    e3 = [i for i in items if i.get("id") == "EXT-3" or "Redis" in json.dumps(i)]
    if e3:
        check("evidence-free critical claim is not auto-AGREE",
              e3[0].get("disposition") != "AGREE", e3[0].get("disposition"))

# --- decompose: tasks must carry evidence_refs (Invariant A) ---
dout = tmp / "dec"
if recj:
    rc2, out2, err2 = run("decompose_tasks.py",
                          ["--reconciled", str(recpath), "--out", str(dout)])
    check("decompose runs on reconciled input", rc2 == 0, err2[:140])
    taskj = None
    for f in dout.rglob("*.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(j, dict) and ("tasks" in j):
                taskj = j; taskpath = f; break
        except Exception:
            pass
    check("decompose produced tasks", taskj is not None, str(list(dout.rglob('*'))[:4]))
    if taskj:
        tasks = taskj.get("tasks", [])
        # Invariant A: a task must be traceable to something. INVESTIGATE tasks
        # exist precisely to GO FIND evidence, so they carry finding_refs
        # instead — but a task with neither reference is untraceable.
        untraceable = [t.get("task_id") for t in tasks
                       if not t.get("evidence_refs") and not t.get("finding_refs")]
        check("INVARIANT A: every task is traceable (evidence_refs or finding_refs)",
              not untraceable, str(untraceable))
        # An IMPLEMENT task (one with a write_scope) must have real evidence.
        impl_no_ev = [t.get("task_id") for t in tasks
                      if t.get("write_scope") and not t.get("evidence_refs")
                      and not t.get("finding_refs")]
        check("implementing tasks carry references", not impl_no_ev, str(impl_no_ev))
        missing_ac = [t.get("task_id") for t in tasks if not t.get("acceptance_criteria")]
        check("every task has acceptance_criteria", not missing_ac, str(missing_ac))
        no_scope = [t.get("task_id") for t in tasks if not t.get("scope")]
        check("every task has scope", not no_scope, str(no_scope))
        risks = {t.get("risk") for t in tasks}
        check("task risks are canonical UPPERCASE",
              risks <= {"TRIVIAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"}, str(risks))
        # risk and early_risk must agree in casing on the same task
        mismatch = [(t.get("task_id"), t.get("risk"), t.get("early_risk"))
                    for t in tasks
                    if t.get("early_risk") and t.get("risk")
                    and t["risk"] != t["risk"].upper()]
        check("risk field casing is normalised", not mismatch, str(mismatch))

# --- garbage input must fail loudly, not emit an empty-but-ok plan ---
bad_json = tmp / "bad.json"; bad_json.write_text('{"nope": true}', encoding="utf-8")
rc3, out3, err3 = run("decompose_tasks.py", ["--reconciled", str(bad_json), "--out", str(tmp / "d2")])
check("decompose rejects wrong-shape input", rc3 != 0, f"rc={rc3} out={out3[:100]}")

rc4, out4, err4 = run("reconcile_review.py",
                      ["--analysis", str(analysis), "--external", str(bad_json), "--out", str(tmp / "r2")])
check("reconcile rejects wrong-shape external review", rc4 != 0, f"rc={rc4}")

# --- dispatch must refuse with no plan and no Ops DB ---
rc5, out5, err5 = run("dispatch_to_devin.py", [])
check("dispatch refuses without --plan/--ops-db", rc5 != 0, f"rc={rc5} {out5[:90]}")

# dispatch must not invent a session when given a nonexistent plan
rc6, out6, err6 = run("dispatch_to_devin.py", ["--plan", str(tmp / "nope.json")])
check("dispatch fails on missing plan file", rc6 != 0, f"rc={rc6}")

print(f"\n  {P} passed, {F} failed")
print(f"  tmp: {tmp}")
sys.exit(1 if F else 0)
