#!/usr/bin/env python3
"""Final Risk Recalculation - P0"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_CRITICAL = "CRITICAL"
ALL_RISKS = [RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL]

SENSITIVE_PATTERNS = [
    re.compile(r"auth", re.I),
    re.compile(r"payment|billing|checkout", re.I),
    re.compile(r"migration|migrate|schema", re.I),
    re.compile(r"secret|credential|token", re.I),
    re.compile(r"production|deploy|release", re.I),
    re.compile(r"database|db/", re.I),
    re.compile(r"security|cve|vulnerability", re.I),
    re.compile(r"permission|role|access.?control", re.I),
    re.compile(r"encrypt|decrypt|crypto", re.I),
]
SEVERITY_MAP = {
    "secret|credential|token": RISK_CRITICAL,
    "security|cve|vulnerability": RISK_CRITICAL,
    "payment|billing|checkout": RISK_HIGH,
    "auth": RISK_HIGH,
    "migration|migrate|schema": RISK_HIGH,
    "encrypt|decrypt|crypto": RISK_HIGH,
    "permission|role|access.?control": RISK_HIGH,
    "database|db/": RISK_MEDIUM,
    "production|deploy|release": RISK_MEDIUM,
}

RI = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2, RISK_CRITICAL: 3}
RV = {0: RISK_LOW, 1: RISK_MEDIUM, 2: RISK_HIGH, 3: RISK_CRITICAL}


def recalculate(early_risk, changed_paths=None, test_results=None,
                review_findings=None, security_findings=None):
    reasons = []
    cur = RI.get(early_risk.upper(), 1)
    de = []
    cp = changed_paths or []

    hits = set()
    for path in cp:
        for pat in SENSITIVE_PATTERNS:
            if pat.search(path):
                hits.add(pat.pattern)
    for h in hits:
        el = RI.get(SEVERITY_MAP.get(h, RISK_MEDIUM), 1)
        if el > cur:
            cur = el
            reasons.append(f"Sensitive path '{h}' -> {RV[cur]}")

    tr = test_results or {}
    # Accept every shape the pipeline actually emits. collect_repo_evidence and
    # the CI collector write `tests_passed`/`tests_failed`; older callers wrote
    # `fail_count`/`pass_count`; vitest-style summaries use `failed`/`passed`.
    # Reading only one spelling silently zeroes the count, and a zeroed fail
    # count means a red build never escalates risk — a gate bypass, not a
    # cosmetic bug.
    def _count(*keys: str) -> int:
        for k in keys:
            v = tr.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and v:
                return int(v)
        return 0

    fc = _count("fail_count", "fail", "failed", "tests_failed")
    pc = _count("pass_count", "pass", "passed", "tests_passed")

    # An explicitly red CI is a failure signal even when no per-test counts
    # were reported.
    ci_status = str(tr.get("ci_status", "") or "").lower()
    ci_green = tr.get("ci_green")
    ci_red = ci_status in ("fail", "failed", "failing", "red", "error") or ci_green is False
    ci_unknown = ci_status == "unknown" or (ci_green is None and ci_status == "" and not fc and not pc)

    if fc > 0:
        if cur < RI[RISK_HIGH]:
            cur = RI[RISK_HIGH]
        reasons.append(f"Test failures ({fc}) -> HIGH")
    elif ci_red:
        if cur < RI[RISK_HIGH]:
            cur = RI[RISK_HIGH]
        reasons.append("CI reported failing -> HIGH")
    elif pc > 0 and fc == 0 and not ci_unknown:
        de.append(f"All {pc} tests pass")
    elif ci_unknown:
        reasons.append("Verification status unknown — no downgrade evidence")

    rf = review_findings or []
    oc = sum(1 for f in rf if f.get("severity","").lower()=="critical"
             and f.get("disposition") not in ("REJECTED","DEFERRED"))
    oh = sum(1 for f in rf if f.get("severity","").lower()=="high"
             and f.get("disposition") not in ("REJECTED","DEFERRED"))
    if oc > 0:
        cur = RI[RISK_CRITICAL]
        reasons.append(f"{oc} unresolved CRITICAL findings")
    elif oh > 0 and cur < RI[RISK_HIGH]:
        cur = RI[RISK_HIGH]
        reasons.append(f"{oh} unresolved HIGH findings")
    if oc == 0 and oh == 0 and rf:
        de.append("Review findings resolved")

    sf = security_findings or []
    sc = sum(1 for f in sf if f.get("severity","").lower()=="critical")
    if sc > 0:
        cur = RI[RISK_CRITICAL]
        reasons.append(f"{sc} security findings -> CRITICAL")

    ol = RI.get(early_risk.upper(), 1)
    if cur < ol:
        if de:
            reasons.append(f"Risk lowered from {early_risk} to {RV[cur]}: {'; '.join(de)}")
        else:
            cur = ol
            reasons.append(f"Cannot downgrade from {early_risk}: no evidence")

    fr = RV.get(cur, RISK_MEDIUM)
    gates = ["ci"]
    if fr in (RISK_HIGH, RISK_CRITICAL):
        gates.append("codex")
    if fr == RISK_CRITICAL:
        gates.append("human")

    return {"early_risk": early_risk, "final_risk": fr,
            "risk_changed": fr != early_risk.upper(),
            "risk_reasons": reasons, "required_gates": gates,
            "changed_paths": cp}


def main() -> int:
    p = argparse.ArgumentParser(description="Recalculate final risk.")
    p.add_argument("--early-risk", required=True,
                   choices=ALL_RISKS + [r.lower() for r in ALL_RISKS])
    p.add_argument("--changed-paths")
    p.add_argument("--test-results")
    p.add_argument("--review-findings")
    p.add_argument("--security-findings")
    p.add_argument("--git-diff")
    p.add_argument("--out")
    a = p.parse_args()

    cp = json.loads(a.changed_paths) if a.changed_paths else []
    tr = json.loads(a.test_results) if a.test_results else None
    rf = json.loads(a.review_findings) if a.review_findings else None
    sf = json.loads(a.security_findings) if a.security_findings else None

    result = recalculate(a.early_risk, cp, tr, rf, sf)
    output = json.dumps(result, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())