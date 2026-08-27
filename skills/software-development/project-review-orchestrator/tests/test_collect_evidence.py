#!/usr/bin/env python3
"""Unit tests: collect_repo_evidence.py — deterministic, read-only, no secret export."""
import json, subprocess, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "collect_repo_evidence.py"
sys.path.insert(0, str(SCRIPTS))
import collect_repo_evidence as cre

PY = sys.executable
P, F = 0, 0
def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1; print(f"  [PASS] {name}")
    else:
        F += 1; print(f"  [FAIL] {name} | {detail}")

def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True)

print("=" * 62)
print("  collect_repo_evidence.py")
print("=" * 62)

# --- classifier purity ---
for p in [".env.production", "id_rsa.pem", "server.key", "cert.p12", "store.pfx"]:
    check(f"sensitive: {p}", cre.is_sensitive_path(p), p)
for p in ["src/main.py", "README.md", "env_helper.py"]:
    check(f"not sensitive: {p}", not cre.is_sensitive_path(p), p)

for p in ["tests/test_x.py", "src/__tests__/a.js", "a_test.py", "x.test.ts", "y.spec.js"]:
    check(f"test file: {p}", cre.looks_like_test(p), p)
check("not a test: src/latest.py", not cre.looks_like_test("src/latest.py"))

for p in [".github/workflows/ci.yml", ".gitlab-ci.yml", "Jenkinsfile", ".circleci/config.yml"]:
    check(f"ci file: {p}", cre.looks_like_ci(p), p)
check("not ci: src/cicero.py", not cre.looks_like_ci("src/cicero.py"))

# windows-style separators must classify the same as posix
check("backslash path classified as test",
      cre.looks_like_test(r"tests\test_x.py"), "windows sep")
check("backslash path classified as ci",
      cre.looks_like_ci(r".github\workflows\ci.yml"), "windows sep")

# --- real repo end-to-end ---
tmp = Path(tempfile.mkdtemp(prefix="evid_"))
repo = tmp / "repo"; repo.mkdir()
git(repo, "init", "-q")
git(repo, "config", "user.email", "t@t.t"); git(repo, "config", "user.name", "t")
(repo / "app.py").write_text("# TODO: fix\n# FIXME: later\nprint(1)\n", encoding="utf-8")
(repo / "README.md").write_text("hi", encoding="utf-8")
(repo / "requirements.txt").write_text("requests\n", encoding="utf-8")
tdir = repo / "tests"; tdir.mkdir()
(tdir / "test_app.py").write_text("def test_x(): pass\n", encoding="utf-8")
wf = repo / ".github" / "workflows"; wf.mkdir(parents=True)
(wf / "ci.yml").write_text("name: CI\n", encoding="utf-8")
# a REAL secret that must never reach the evidence artifact
(repo / ".env.production").write_text("API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")

out = tmp / "ev"
p = subprocess.run([PY, str(SCRIPT), "--repo", str(repo), "--out", str(out)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check("runs successfully on a real repo", p.returncode == 0, (p.stderr or "")[:140])

ev_files = list(out.rglob("*.json"))
check("wrote a JSON evidence artifact", len(ev_files) > 0, str([f.name for f in ev_files]))

blob = ""
for f in out.rglob("*"):
    if f.is_file():
        try: blob += f.read_text(encoding="utf-8")
        except Exception: pass

# INVARIANT: evidence collector must not export secret VALUES
check("secret value never appears in evidence",
      "sk-abcdefghijklmnopqrstuvwxyz123456" not in blob,
      "RAW SECRET LEAKED INTO EVIDENCE ARTIFACT")

# it may (and should) still *name* the sensitive path as a hint
check("sensitive path is still flagged as a hint",
      ".env.production" in blob or "sensitive" in blob.lower(), "no hint recorded")

# --- deterministic: same commit twice -> same counts ---
out2 = tmp / "ev2"
subprocess.run([PY, str(SCRIPT), "--repo", str(repo), "--out", str(out2)],
               capture_output=True, text=True)

def load_ev(d):
    for f in sorted(d.rglob("*.json")):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(j, dict) and len(j) > 2:
                return j
        except Exception:
            pass
    return {}

e1, e2 = load_ev(out), load_ev(out2)
def counts(e):
    s = json.dumps(e, sort_keys=True)
    import re
    return sorted(re.findall(r'"(?:tracked_file_count|test_file_count|todo|fixme)[^"]*":\s*(\d+)', s))
check("evidence is deterministic across runs", counts(e1) == counts(e2),
      f"{counts(e1)} vs {counts(e2)}")

# commit sha recorded and matches git
head = git(repo, "rev-parse", "HEAD").stdout.strip()
check("records the exact HEAD sha", head in json.dumps(e1), head[:12])

# --- repo must be untouched (read-only invariant) ---
st = git(repo, "status", "--porcelain").stdout.strip()
check("evidence collection did not modify the repo", st == "", st[:120])

# --- non-git dir must fail loudly ---
nogit = tmp / "nogit"; nogit.mkdir()
p2 = subprocess.run([PY, str(SCRIPT), "--repo", str(nogit), "--out", str(tmp / "ev3")],
                    capture_output=True, text=True)
check("non-git repo exits non-zero", p2.returncode != 0, f"rc={p2.returncode}")

print(f"\n  {P} passed, {F} failed")
print(f"  tmp: {tmp}")
sys.exit(1 if F else 0)
