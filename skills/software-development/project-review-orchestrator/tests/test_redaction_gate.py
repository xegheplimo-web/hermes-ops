#!/usr/bin/env python3
"""Adversarial test of the Redaction Gate (build_review_packet.py).

Invariant M: "No external packet knowingly contains secrets."
This feeds REAL secret shapes through the gate and greps the OUTPUT packet
for the raw secret. Anything that survives is an Invariant M violation.
"""
import json, subprocess, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "build_review_packet.py"
PY = sys.executable
tmp = Path(tempfile.mkdtemp(prefix="redact_"))
results = []

def rec(tid, name, status, detail):
    results.append((tid, name, status, detail))

# Real-shaped secrets (fake values, real formats)
SECRETS = {
    "bearer_header":   "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    "openai_key":      "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
    "github_pat":      "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "aws_akid":        "AKIAIOSFODNN7EXAMPLE",
    "password_kv":     "password=Giahung136899$$",
    "slack_token":     "xoxb-1234567890-abcdefghijklmnop",
    "jwt":             "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    "pg_dsn":          "postgres://hermes:hermesops@127.0.0.1:55432/hermes_ops",
}

def run(evidence_obj, analysis_text, extra=None):
    ev = tmp / "ev.json"; ev.write_text(json.dumps(evidence_obj), encoding="utf-8")
    an = tmp / "an.md"; an.write_text(analysis_text, encoding="utf-8")
    out = tmp / "packet.json"
    if out.exists(): out.unlink()
    args = [PY, str(SCRIPT), "--evidence", str(ev), "--analysis", str(an), "--out", str(out)]
    if extra: args += extra
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    body = out.read_text(encoding="utf-8") if out.exists() else ""
    return p.returncode, (p.stdout or "").strip(), body

print("=" * 78)
print("  REDACTION GATE — ADVERSARIAL TEST (Invariant M)")
print("=" * 78)

# ---- V1: secrets in the ANALYSIS path (the path that IS redacted) ----
print("\n--- V1: secrets placed in --analysis (documented redaction path) ---")
analysis = "# Findings\n" + "\n".join(f"- {k}: {v}" for k, v in SECRETS.items())
rc, out, packet = run({"branch": "main"}, analysis)
for name, secret in SECRETS.items():
    leaked = secret in packet
    rec(f"V1.{name}", f"analysis: {name} redacted", "LEAK" if leaked else "PASS",
        f"raw_present_in_packet={leaked}")
    print(f"  [{'LEAK' if leaked else 'PASS'}] {name:16} {'RAW SECRET SURVIVED' if leaked else 'redacted'}")

# ---- V2: secrets in the EVIDENCE path ----
print("\n--- V2: secrets placed in --evidence (repository_snapshot) ---")
ev_with_secrets = {
    "branch": "main",
    "commit_sha": "deadbeef",
    "config_sample": {k: v for k, v in SECRETS.items()},
    "env_files": ["DATABASE_URL=" + SECRETS["pg_dsn"]],
}
rc2, out2, packet2 = run(ev_with_secrets, "# clean analysis, nothing sensitive here")
n_leaked = 0
for name, secret in SECRETS.items():
    leaked = secret in packet2
    if leaked: n_leaked += 1
    rec(f"V2.{name}", f"evidence: {name} redacted", "LEAK" if leaked else "PASS",
        f"raw_present_in_packet={leaked}")
    print(f"  [{'LEAK' if leaked else 'PASS'}] {name:16} {'RAW SECRET SURVIVED' if leaked else 'redacted'}")

try:
    pj = json.loads(packet2)
    claim = pj.get("security", {})
    print(f"\n  packet security block claims: redaction_matches={claim.get('redaction_matches')} "
          f"source_files_included={claim.get('source_files_included')}")
    rec("V2.claim", "security block honest about evidence redaction",
        "LEAK" if (n_leaked > 0 and claim.get("redaction_matches", 0) >= 0) else "PASS",
        f"leaked={n_leaked} but claims redaction_matches={claim.get('redaction_matches')}")
except Exception as e:
    print(f"  packet unparseable: {e}")

# ---- V3: the Authorization: Bearer regex specifically ----
print("\n--- V3: Authorization: Bearer header regex (line 35 looks corrupted) ---")
import re
sys.path.insert(0, str(SCRIPTS))
import build_review_packet as brp
bearer_pat = None
for pat, repl in brp.SECRET_PATTERNS:
    if "Authorization" in pat.pattern:
        bearer_pat = pat
        print(f"  pattern source: {pat.pattern!r}")
        print(f"  replacement   : {repl!r}")
        break
if bearer_pat:
    probe = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    m = bearer_pat.search(probe)
    rec("V3", "bearer regex actually matches a real bearer header",
        "PASS" if m else "BROKEN", f"match={m}")
    print(f"  [{'PASS' if m else 'BROKEN'}] regex {'matches' if m else 'DOES NOT MATCH'} a real header")
    # what does the whole redact() do to it?
    red, n = brp.redact(probe)
    print(f"  redact() output: {red!r} (n={n})")
    rec("V3b", "bearer token value removed by redact() overall",
        "PASS" if "abcdefghijklmnopqrstuvwxyz0123456789ABCD" not in red else "LEAK",
        f"result={red!r}")

# ---- V4: wrong-shape evidence still yields ok:true "valid packet" ----
print("\n--- V4: wrong-shape evidence accepted as a valid review packet ---")
rc4, out4, packet4 = run({"totally": "wrong", "shape": 1}, "x")
try:
    j4 = json.loads(out4)
    ok = j4.get("ok") is True
    rec("V4", "wrong-shape evidence rejected", "WEAK" if ok else "PASS",
        f"ok={ok} — no schema validation on --evidence")
    print(f"  [{'WEAK' if ok else 'PASS'}] ok={ok} (no schema validation of evidence shape)")
except Exception as e:
    print(f"  stdout unparseable: {e}")

print("\n" + "=" * 78)
leaks = [r for r in results if r[2] == "LEAK"]
broken = [r for r in results if r[2] == "BROKEN"]
weak = [r for r in results if r[2] == "WEAK"]
npass = len([r for r in results if r[2] == "PASS"])
print(f"  PASS={npass}  LEAK={len(leaks)}  BROKEN={len(broken)}  WEAK={len(weak)}")
if leaks:
    print("\n  *** INVARIANT M VIOLATIONS (raw secret survived into external packet) ***")
    for tid, name, _, detail in leaks:
        print(f"    {tid:20} {name}")
if broken:
    print("\n  *** BROKEN PATTERNS ***")
    for tid, name, _, detail in broken:
        print(f"    {tid:20} {name} | {detail}")
print(f"\n  tmp: {tmp}")
