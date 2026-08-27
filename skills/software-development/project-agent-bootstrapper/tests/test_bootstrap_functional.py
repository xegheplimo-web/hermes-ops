#!/usr/bin/env python3
"""Functional verification of project-agent-bootstrapper (real execution, no mocks).

Run directly:  python tests/test_bootstrap_functional.py
Exits non-zero if any check fails.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "bootstrap_project_skill.py"
PY = sys.executable
results = []

def rec(tid, name, status, detail):
    results.append((tid, name, status, detail))

def run(args, cwd):
    p = subprocess.run([PY, str(SCRIPT), *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr

def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True)

tmp = Path(tempfile.mkdtemp(prefix="bootstrap_t_"))

# ---- T1: non-git dir must fail exit 1 with ok:false ----
nongit = tmp / "nongit"; nongit.mkdir()
rc, out, err = run(["--repo", ".", "--out", ".hermes/bootstrap"], nongit)
ok = rc == 1 and '"ok": false' in err
rec("T1", "non-git repo -> exit 1 + ok:false on stderr", "PASS" if ok else "FAIL", f"rc={rc} stderr_head={err.strip()[:120]!r}")

# ---- build a clean fresh git repo WITHOUT canonical skill ----
fresh = tmp / "fresh"; fresh.mkdir()
git(fresh, "init", "-q")
git(fresh, "config", "user.email", "t@t.t"); git(fresh, "config", "user.name", "t")
(fresh / "README.md").write_text("hi", encoding="utf-8")
git(fresh, "add", "-A"); git(fresh, "commit", "-qm", "init")

# ---- T2: run on fresh repo, canonical skill missing, no --generate-draft ----
rc, out, err = run(["--repo", ".", "--out", ".hermes/bootstrap"], fresh)
try:
    j = json.loads(out)
except Exception as e:
    j = None
if j:
    sc = j["skill_check"]
    ok = rc == 0 and sc["exists"] is False and j["generated_skill_draft"] is None and "SKILL.md is missing." in sc["issues"]
    rec("T2", "missing canonical skill reported, no draft written", "PASS" if ok else "FAIL",
        f"exists={sc['exists']} draft={j['generated_skill_draft']} issues={sc['issues']}")
    run1 = j
else:
    rec("T2", "missing canonical skill reported", "FAIL", f"rc={rc} bad json out={out[:200]!r} err={err[:200]!r}")
    run1 = None

# ---- T3: agent-roles.json written to .hermes/governance on first run ----
roles_p = fresh / ".hermes" / "governance" / "agent-roles.json"
ok = roles_p.exists()
rec("T3", "agent-roles.json created at .hermes/governance", "PASS" if ok else "FAIL", str(roles_p))

roles = json.loads(roles_p.read_text(encoding="utf-8")) if ok else {}

# ---- T4: checklist assertions on the permission matrix ----
actors = {a["name"]: a for a in roles.get("actors", [])}
required = ["USER","HERMES","AgentMemory","OpsDB","EvidenceCollector","RedactionGate",
            "OpenAIReviewAdapter","ChatGPTHumanMode","CodexReviewer","DevinCodemap",
            "DevinAdapter","Devin","OpenCode","GitHub","CI","SecurityScanners",
            "CodeRabbit","hermes/policy-gate","Human"]
missing_actors = [a for a in required if a not in actors]
rec("T4a", f"all {len(required)} required actors present", "PASS" if not missing_actors else "FAIL", f"missing={missing_actors}")

no_write = "modify repository" in actors.get("OpenAIReviewAdapter", {}).get("forbidden", [])
rec("T4b", "external reviewer has no write authority", "PASS" if no_write else "FAIL",
    str(actors.get("OpenAIReviewAdapter", {}).get("forbidden")))

mem_noqueue = "own task queue" in actors.get("AgentMemory", {}).get("forbidden", [])
rec("T4c", "AgentMemory forbidden from owning task queue", "PASS" if mem_noqueue else "FAIL",
    str(actors.get("AgentMemory", {}).get("forbidden")))

opsdb_truth = roles.get("authority_model", {}).get("runtime_execution_state") == "OpsDB"
rec("T4d", "OpsDB = runtime truth in authority_model", "PASS" if opsdb_truth else "FAIL",
    str(roles.get("authority_model", {}).get("runtime_execution_state")))

devin_gw = "bypass DevinAdapter" in actors.get("Devin", {}).get("forbidden", [])
rec("T4e", "Devin only through DevinAdapter", "PASS" if devin_gw else "FAIL",
    str(actors.get("Devin", {}).get("forbidden")))

every_actor_has_both = [n for n, a in actors.items() if not a.get("allowed") or not a.get("forbidden")]
rec("T4f", "every actor has allowed+forbidden", "PASS" if not every_actor_has_both else "FAIL",
    f"incomplete={every_actor_has_both}")

hr = roles.get("hard_rules", [])
rec("T4g", "hard_rules >= 10 entries", "PASS" if len(hr) >= 10 else "FAIL", f"count={len(hr)}")

# Codex must be READ-ONLY: no write verb may appear in its allowed list
codex = actors.get("CodexReviewer", {})
write_verbs = ("edit", "commit", "push", "merge", "implement", "modify")
codex_writes = [a for a in codex.get("allowed", [])
                if any(v in a.lower() for v in write_verbs)]
rec("T4h", "Codex reviewer has no write verb in allowed", "PASS" if not codex_writes else "FAIL",
    str(codex_writes))
rec("T4i", "Codex reviewer explicitly forbidden from editing files",
    "PASS" if "edit files" in codex.get("forbidden", []) else "FAIL",
    str(codex.get("forbidden")))

# OpenCode is SECONDARY: it may implement, but never silently displace Devin
oc = actors.get("OpenCode", {})
rec("T4j", "OpenCode declared secondary executor",
    "PASS" if "secondary" in oc.get("role", "") else "FAIL", oc.get("role"))
rec("T4k", "OpenCode cannot replace Devin without a recorded reason",
    "PASS" if any("without a recorded reason" in f for f in oc.get("forbidden", [])) else "FAIL",
    str(oc.get("forbidden")))
rec("T4l", "OpenCode cannot write outside assigned scope",
    "PASS" if "write outside the assigned scope" in oc.get("forbidden", []) else "FAIL",
    str(oc.get("forbidden")))

# authority model names both executors and the readonly reviewer
am = roles.get("authority_model", {})
rec("T4m", "authority_model names secondary executor + readonly reviewer",
    "PASS" if am.get("secondary_implementation_executor") == "OpenCode"
    and am.get("readonly_reviewer") == "CodexReviewer" else "FAIL",
    f"{am.get('secondary_implementation_executor')} / {am.get('readonly_reviewer')}")

# reviewer-never-executor must be an explicit hard rule
rec("T4n", "hard_rules forbid reviewer becoming executor",
    "PASS" if any("reviewer may never become an executor" in r.lower() for r in hr) else "FAIL",
    str([r for r in hr if "reviewer" in r.lower()]))

# ---- T5: idempotency / no silent overwrite on 2nd run ----
first_mtime = roles_p.stat().st_mtime_ns
first_bytes = roles_p.read_bytes()
rc2, out2, err2 = run(["--repo", ".", "--out", ".hermes/bootstrap"], fresh)
j2 = json.loads(out2) if rc2 == 0 else None
same = roles_p.read_bytes() == first_bytes
proposed = list((fresh / ".hermes" / "bootstrap").rglob("agent-roles.proposed.json"))
ok = same and len(proposed) == 1
rec("T5", "2nd run: production roles untouched, writes .proposed.json", "PASS" if ok else "FAIL",
    f"bytes_identical={same} proposed_files={[p.name for p in proposed]}")

# ---- T6: dirty state detection ----
(fresh / "dirty.txt").write_text("x", encoding="utf-8")
rc3, out3, _ = run(["--repo", ".", "--out", ".hermes/bootstrap"], fresh)
j3 = json.loads(out3)
ok = j3["dirty"] is True and j3["commit"] == run1["commit"] if run1 else j3["dirty"] is True
rec("T6", "dirty worktree detected + same commit SHA", "PASS" if ok else "FAIL",
    f"dirty={j3['dirty']} commit={j3['commit'][:7]}")
state3 = json.loads(Path(j3["state"]).read_text(encoding="utf-8"))
has_sample = len(state3["repository"]["changed_entries_sample"]) > 0
rec("T6b", "changed_entries_sample populated", "PASS" if has_sample else "FAIL",
    f"n={len(state3['repository']['changed_entries_sample'])}")

# ---- T7: --generate-draft writes draft OUTSIDE production path ----
rc4, out4, _ = run(["--repo", ".", "--out", ".hermes/bootstrap", "--generate-draft"], fresh)
j4 = json.loads(out4)
draft = j4["generated_skill_draft"]
prod = fresh / "skills" / "software-development" / "project-review-orchestrator" / "SKILL.md"
ok = draft is not None and Path(draft).exists() and not prod.exists()
rec("T7", "--generate-draft: draft created, production path still empty", "PASS" if ok else "FAIL",
    f"draft={draft} prod_exists={prod.exists()}")

# ---- T8: --install-if-missing WITHOUT --confirm-write must refuse ----
rc5, out5, _ = run(["--repo", ".", "--out", ".hermes/bootstrap", "--generate-draft", "--install-if-missing"], fresh)
j5 = json.loads(out5)
refused = any("--confirm-write missing" in a for a in j5["actions"]) and not prod.exists()
rec("T8", "install without --confirm-write refused", "PASS" if refused else "FAIL",
    f"prod_exists={prod.exists()} actions={[a for a in j5['actions'] if 'onfirm' in a or 'nstall' in a]}")

# ---- T9: --install-if-missing --confirm-write installs, and is valid frontmatter ----
rc6, out6, _ = run(["--repo", ".", "--out", ".hermes/bootstrap", "--generate-draft", "--install-if-missing", "--confirm-write"], fresh)
j6 = json.loads(out6)
ok = prod.exists() and j6["skill_check"]["exists"] and j6["skill_check"]["valid_frontmatter"]
rec("T9", "install with approval works + frontmatter valid", "PASS" if ok else "FAIL",
    f"prod={prod.exists()} check={j6['skill_check']}")

# ---- T10: re-run after install must NOT overwrite existing production skill ----
prod_bytes = prod.read_bytes()
prod.write_text(prod.read_text(encoding="utf-8") + "\n<!-- local edit -->\n", encoding="utf-8")
edited = prod.read_bytes()
rc7, out7, _ = run(["--repo", ".", "--out", ".hermes/bootstrap", "--generate-draft", "--install-if-missing", "--confirm-write"], fresh)
ok = prod.read_bytes() == edited
rec("T10", "existing production skill never overwritten", "PASS" if ok else "FAIL",
    f"preserved={ok}")

# ---- T11: RUN_ID format ----
import re
rid = j6["run_id"]
ok = re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{7}", rid) is not None
rec("T11", "RUN_ID = <ts>Z-<short sha> format", "PASS" if ok else "FAIL", rid)

# ---- T12: report references exact commit SHA + next command ----
rep = Path(j6["report"]).read_text(encoding="utf-8")
ok = j6["commit"] in rep and "/project-review-orchestrator" in rep
rec("T12", "report contains full SHA + next command", "PASS" if ok else "FAIL",
    f"sha_in_report={j6['commit'] in rep} next_cmd={'/project-review-orchestrator' in rep}")

# ---- T13: documented --dry-run flag ----
rc8, out8, err8 = run(["--repo", ".", "--out", ".hermes/bootstrap", "--dry-run"], fresh)
ok = rc8 == 0
rec("T13", "--dry-run (documented in SKILL.md Quick Reference) works", "PASS" if ok else "FAIL",
    f"rc={rc8} err={err8.strip().splitlines()[-1][:110] if err8.strip() else ''}")

# ---- T13b: --dry-run must not create ANY file ----
dryrepo = tmp / "dryrepo"; dryrepo.mkdir()
git(dryrepo, "init", "-q"); git(dryrepo, "config", "user.email", "t@t.t"); git(dryrepo, "config", "user.name", "t")
(dryrepo / "a.txt").write_text("a", encoding="utf-8")
git(dryrepo, "add", "-A"); git(dryrepo, "commit", "-qm", "init")
before = {str(p.relative_to(dryrepo)) for p in dryrepo.rglob("*") if ".git" not in p.parts}
rcd, outd, errd = run(["--repo", ".", "--out", ".hermes/bootstrap", "--dry-run",
                       "--generate-draft", "--install-if-missing", "--confirm-write"], dryrepo)
after = {str(p.relative_to(dryrepo)) for p in dryrepo.rglob("*") if ".git" not in p.parts}
jd = json.loads(outd) if rcd == 0 else {}
ok = rcd == 0 and before == after and jd.get("dry_run") is True
rec("T13b", "--dry-run writes zero files (side-effect free)", "PASS" if ok else "FAIL",
    f"rc={rcd} new_paths={sorted(after - before)} dry_run_flag={jd.get('dry_run')}")

# ---- T13c: --install-if-missing without --generate-draft must warn (silent no-op bug) ----
warnrepo = tmp / "warnrepo"; warnrepo.mkdir()
git(warnrepo, "init", "-q"); git(warnrepo, "config", "user.email", "t@t.t"); git(warnrepo, "config", "user.name", "t")
(warnrepo / "a.txt").write_text("a", encoding="utf-8")
git(warnrepo, "add", "-A"); git(warnrepo, "commit", "-qm", "init")
rcw, outw, _ = run(["--repo", ".", "--out", ".hermes/bootstrap", "--install-if-missing", "--confirm-write"], warnrepo)
jw = json.loads(outw)
warned = any("requires --generate-draft" in a for a in jw["actions"])
prod_w = warnrepo / "skills" / "software-development" / "project-review-orchestrator" / "SKILL.md"
ok = warned and not prod_w.exists()
rec("T13c", "--install-if-missing w/o --generate-draft warns, not silent", "PASS" if ok else "FAIL",
    f"warned={warned} prod_created={prod_w.exists()}")

# ---- T14: no task dispatched / no repo code mutated by bootstrap ----
st = git(fresh, "status", "--porcelain").stdout.splitlines()
code_touched = [l for l in st if "README.md" in l]
rec("T14", "bootstrap did not mutate existing repo code", "PASS" if not code_touched else "FAIL",
    f"touched={code_touched}")

# ---- T15: templates/agent-roles.json parity with script output ----
tpl = SKILL_ROOT / "templates" / "agent-roles.json"
try:
    t = json.loads(tpl.read_text(encoding="utf-8"))
    same_rules = t.get("hard_rules") == roles.get("hard_rules")
    same_auth = t.get("authority_model") == roles.get("authority_model")
    ok = same_rules and same_auth
    rec("T15", "template agent-roles.json matches script authority/hard_rules", "PASS" if ok else "FAIL",
        f"hard_rules_match={same_rules} authority_match={same_auth}")
except Exception as e:
    rec("T15", "template agent-roles.json readable", "FAIL", str(e))

# ---- T16: --out outside repo (path traversal / abs path) ----
outside = tmp / "outside_out"
rc9, out9, err9 = run(["--repo", ".", "--out", str(outside)], fresh)
ok = rc9 == 0 and outside.exists()
rec("T16", "--out accepts absolute path outside repo", "PASS" if ok else "FAIL", f"rc={rc9} exists={outside.exists()}")

# ---- T17: run from a SUBDIRECTORY of the repo (repo root resolution) ----
sub = fresh / "packages" / "deep" / "nested"
sub.mkdir(parents=True, exist_ok=True)
rc10, out10, err10 = run(["--repo", ".", "--out", ".hermes/bootstrap"], sub)
if rc10 == 0:
    j10 = json.loads(out10)
    resolved_root = Path(j10["repo"]).resolve()
    ok = resolved_root == fresh.resolve()
    # where did artifacts land?
    landed = Path(j10["state"]).resolve()
    in_sub = str(landed).startswith(str(sub.resolve()))
    rec("T17", "run from subdir resolves true repo root", "PASS" if ok else "FAIL", f"repo={resolved_root}")
    rec("T17b", "artifacts location when run from subdir", "INFO",
        f"state.json under {'SUBDIR (relative --out)' if in_sub else 'repo root'}: {landed}")
else:
    rec("T17", "run from subdir", "FAIL", f"rc={rc10} {err10[:150]}")

print("\n=== FUNCTIONAL TEST RESULTS (project-agent-bootstrapper) ===\n")
w = max(len(n) for _, n, _, _ in results)
npass = nfail = ninfo = 0
for tid, name, status, detail in results:
    if status == "PASS": npass += 1
    elif status == "FAIL": nfail += 1
    else: ninfo += 1
    print(f"[{status:4}] {tid:5} {name:<{w}}  | {detail}")
print(f"\nPASS={npass}  FAIL={nfail}  INFO={ninfo}")
print(f"tmp workspace: {tmp}")
if nfail == 0:
    shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if nfail else 0)
