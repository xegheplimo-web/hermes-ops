#!/usr/bin/env python3
"""
Task Type Classifier + Early Risk — P0

Classifies review findings into task types and assigns initial risk levels.
Deterministic rules first, LLM augmentation when rules are insufficient.

Task types:
  FEATURE         — New functionality
  BUG             — Incorrect behavior / regression
  SECURITY        — Auth, secrets, permissions, injection
  REFACTOR        — Internal restructuring without behavior change
  PERFORMANCE     — Speed, memory, resource usage
  INFRA           — CI/CD, Docker, deployment, tooling
  CONFIG          — Configuration, environment, settings
  MIGRATION       — Database, data, schema migration
  INVESTIGATION   — Unknown area needing exploration
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ── Constants ───────────────────────────────────────────────────────────────

TASK_FEATURE = "FEATURE"
TASK_BUG = "BUG"
TASK_SECURITY = "SECURITY"
TASK_REFACTOR = "REFACTOR"
TASK_PERFORMANCE = "PERFORMANCE"
TASK_INFRA = "INFRA"
TASK_CONFIG = "CONFIG"
TASK_MIGRATION = "MIGRATION"
TASK_INVESTIGATION = "INVESTIGATION"

ALL_TASK_TYPES = [
    TASK_FEATURE, TASK_BUG, TASK_SECURITY, TASK_REFACTOR,
    TASK_PERFORMANCE, TASK_INFRA, TASK_CONFIG, TASK_MIGRATION,
    TASK_INVESTIGATION,
]

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_CRITICAL = "CRITICAL"
ALL_RISKS = [RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL]


# ── Keyword-based classification rules ──────────────────────────────────────


def classify_task_type(
    title: str,
    description: str,
    evidence_refs: list[str],
    severity: str | None = None,
) -> str:
    """Classify a task by its type using deterministic rules.

    Priority order (first match wins):
      1. INVESTIGATION — explicit investigation keywords
      2. SECURITY     — security-sensitive keywords or paths
      3. MIGRATION    — migration/data keywords
      4. BUG          — bug/defect keywords
      5. PERFORMANCE  — performance keywords
      6. INFRA        — infra/CI/CD keywords
      7. CONFIG       — config keywords
      8. REFACTOR     — refactor keywords
      9. FEATURE      — default
    """
    text = f"{title} {description}".lower()

    # INVESTIGATION
    if any(kw in text for kw in [
        "investigate", "investigation", "explore", "unknown",
        "unverified", "determine root cause", "find out",
    ]):
        return TASK_INVESTIGATION

    # SECURITY
    if any(kw in text for kw in [
        "security", "auth", "oauth", "login", "credential",
        "permission", "encrypt", "decrypt", "secret", "token",
        "xss", "csrf", "injection", "sql injection", "cve",
        "vulnerability", "authenticate", "authorize",
        "password", "api key", "pii",
    ]):
        return TASK_SECURITY

    # Check security-sensitive paths
    for ref in evidence_refs:
        ref_lower = ref.lower()
        if any(kw in ref_lower for kw in [
            "auth", "security", "secret", "token", "credential",
            "password", "permission", "login",
        ]):
            return TASK_SECURITY

    # MIGRATION
    if any(kw in text for kw in [
        "migration", "migrate", "schema change", "data migration",
        "database migration", "backfill", "etl",
    ]):
        return TASK_MIGRATION

    # BUG
    if any(kw in text for kw in [
        "bug", "fix", "error", "crash", "broken", "fail",
        "regression", "incorrect", "wrong", "issue", "defect",
        "exception", "panic", "not working", "does not work",
    ]):
        return TASK_BUG

    # PERFORMANCE
    if any(kw in text for kw in [
        "performance", "slow", "latency", "throughput",
        "optimize", "optimisation", "bottleneck", "n+1",
        "timeout", "memory leak", "cpu", "disk",
    ]):
        return TASK_PERFORMANCE

    # INFRA
    if any(kw in text for kw in [
        "ci", "cd", "pipeline", "deployment", "deploy",
        "docker", "kubernetes", "k8s", "infrastructure",
        "github action", "workflow", "build", "release",
        "terraform", "ansible",
    ]):
        return TASK_INFRA

    # CONFIG
    if any(kw in text for kw in [
        "config", "configuration", "setting", "environment",
        ".env", "env var", "flag", "toggle", "feature flag",
    ]):
        return TASK_CONFIG

    # REFACTOR
    if any(kw in text for kw in [
        "refactor", "restructure", "cleanup", "technical debt",
        "reorganize", "simplify", "extract", "deduplicate",
    ]):
        return TASK_REFACTOR

    # Default
    return TASK_FEATURE


# ── Risk classification ─────────────────────────────────────────────────────


def classify_early_risk(
    task_type: str,
    severity: str | None = None,
    evidence_refs: list[str] | None = None,
    description: str = "",
) -> tuple[str, list[str]]:
    """Assign early risk level with reasons.

    Uses task type as base, then adjusts by severity and paths.
    """
    reasons: list[str] = []

    # Base risk by task type
    type_risk_map: dict[str, str] = {
        TASK_FEATURE: RISK_MEDIUM,
        TASK_BUG: RISK_MEDIUM,
        TASK_SECURITY: RISK_HIGH,
        TASK_REFACTOR: RISK_LOW,
        TASK_PERFORMANCE: RISK_MEDIUM,
        TASK_INFRA: RISK_LOW,
        TASK_CONFIG: RISK_LOW,
        TASK_MIGRATION: RISK_HIGH,
        TASK_INVESTIGATION: RISK_LOW,
    }
    risk = type_risk_map.get(task_type, RISK_MEDIUM)
    reasons.append(f"Base risk for {task_type}: {risk}")

    # Adjust by severity from external review
    if severity:
        sev_lower = severity.lower()
        if sev_lower == "critical":
            risk = RISK_CRITICAL
            reasons.append("External severity: CRITICAL → escalate")
        elif sev_lower == "high" and risk != RISK_CRITICAL:
            risk = RISK_HIGH
            reasons.append("External severity: HIGH")
        elif sev_lower == "medium" and risk == RISK_LOW:
            risk = RISK_MEDIUM
            reasons.append("External severity: MEDIUM → raise from LOW")
        elif sev_lower == "low" and risk in (RISK_HIGH, RISK_CRITICAL):
            reasons.append("External severity: LOW but base risk unchanged (type override)")

    # Check evidence paths for sensitive areas
    sensitive_paths = ["auth", "payment", "billing", "database",
                        "migration", "secret", "credential", "production"]
    evidence_paths_hint = " ".join(evidence_refs or []).lower()
    for sp in sensitive_paths:
        if sp in evidence_paths_hint:
            # Only escalate if not already at highest
            if risk == RISK_LOW:
                risk = RISK_MEDIUM
                reasons.append(f"Sensitive path '{sp}' in evidence → raise to MEDIUM")
            elif risk == RISK_MEDIUM:
                risk = RISK_HIGH
                reasons.append(f"Sensitive path '{sp}' in evidence → raise to HIGH")
            elif risk == RISK_HIGH:
                # Stay at HIGH unless CRITICAL override
                pass

    # Deduplicate and normalize reasons
    seen: set[str] = set()
    unique_reasons: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    return risk, unique_reasons


# ── Full classification ─────────────────────────────────────────────────────


def classify(
    title: str,
    description: str = "",
    severity: str | None = None,
    evidence_refs: list[str] | None = None,
) -> dict:
    """Classify a task: type + early risk + reasons."""
    evidence_refs = evidence_refs or []
    task_type = classify_task_type(title, description, evidence_refs, severity)
    early_risk, risk_reasons = classify_early_risk(
        task_type, severity, evidence_refs, description,
    )
    return {
        "task_type": task_type,
        "early_risk": early_risk,
        "risk_reasons": risk_reasons,
        "evidence_refs": evidence_refs,
        "classifier": "rule-based",
    }


# ── Batch classification ────────────────────────────────────────────────────


def classify_findings(findings: list[dict]) -> list[dict]:
    """Classify a list of reconciled findings into typed tasks with risk."""
    results: list[dict] = []
    for f in findings:
        title = f.get("title", "") or f.get("claim", "")
        description = f.get("rationale", "") or f.get("claim", "")
        severity = f.get("final_severity") or f.get("severity")
        evidence_refs = f.get("evidence_refs", [])
        result = classify(title, description, severity, evidence_refs)
        result["finding_id"] = f.get("id", "")
        results.append(result)
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify tasks by type and assign early risk."
    )
    parser.add_argument("--findings", help="JSON file with findings array")
    parser.add_argument("--out", help="Output JSON file")
    args = parser.parse_args()

    if not args.findings:
        # Interactive mode: accept from stdin
        findings = json.loads(sys.stdin.read())
    else:
        findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))

    # Support both bare list and {"findings": [...]} format
    if isinstance(findings, dict):
        findings = findings.get("findings", [])

    results = classify_findings(findings)
    output = json.dumps(results, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())