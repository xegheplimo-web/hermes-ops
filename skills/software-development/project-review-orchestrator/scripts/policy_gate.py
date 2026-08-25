#!/usr/bin/env python3
"""Python bridge to the `hermes-policy-gate` TypeScript binary.

Provides a 4-way gate (PASS/REPAIR/ESCALATE/BLOCK) by invoking the canonical
TS binary with runtime state: attempts, max-attempts, risk, and an optional
human approval token. Approval tokens are read from the Ops DB when available.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ops_adapter import Approval, OpsDbAdapter


@dataclass
class GateResult:
    """Canonical output from the hermes-policy-gate binary."""
    decision: str = ""
    gate: str = ""
    reason_code: str = ""
    risk_level: str = ""
    required_gates: list[str] | None = None
    policy_version: str = ""
    detail: str = ""
    evidence_identity: str | None = None
    raw: dict | None = None


def find_gate_binary() -> Path | None:
    """Locate the hermes-policy-gate JS bundle on PATH or in the monorepo."""
    from_path = shutil.which("hermes-policy-gate")
    if from_path:
        return Path(from_path)
    repo = Path(__file__).resolve().parent.parent.parent.parent.parent
    candidate = repo / "packages" / "gate" / "dist" / "bin.js"
    if candidate.is_file():
        return candidate
    return None


def _parse_token(token: Approval | dict | None) -> dict | None:
    if token is None:
        return None
    if isinstance(token, Approval):
        if not token.signed_at:
            return None
        return {
            "signedAt": token.signed_at.isoformat() if token.signed_at else None,
            "approver": token.approver,
            "reason": token.reason,
            "signature": token.signature,
        }
    if isinstance(token, dict):
        return token
    return None


def _find_approval_token(db: OpsDbAdapter | None, task_id: int | None) -> dict | None:
    """Fetch the newest approved token from the Ops DB, if available."""
    if db is None or task_id is None:
        return None
    if not db.is_approved(task_id):
        return None
    approvals = db.get_approvals_for_task(task_id)
    for a in approvals:
        if a.status == "approved":
            return _parse_token(a)
    return None


def evaluate_gate(
    manifest: dict,
    head_sha: str,
    policy_version: str,
    attempts: int = 0,
    max_attempts: int = 3,
    risk: str = "",
    changed_files: list[str] | None = None,
    approval: Approval | dict | None = None,
    db: OpsDbAdapter | None = None,
    task_id: int | None = None,
    binary: Path | None = None,
) -> GateResult:
    """Invoke hermes-policy-gate and return a 4-way gate result.

    If `approval` is not given but `db` and `task_id` are, the newest approved
    token from the Ops DB is used.
    """
    bin_path = binary or find_gate_binary()
    if not bin_path:
        raise RuntimeError("hermes-policy-gate binary not found")

    token = approval or _find_approval_token(db, task_id)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(manifest, f)
        manifest_file = f.name

    try:
        args = ["node", str(bin_path), "--manifest", manifest_file,
                "--head-sha", head_sha,
                "--policy-version", policy_version,
                "--attempts", str(attempts),
                "--max-attempts", str(max_attempts)]
        if risk:
            args.extend(["--risk", risk.upper()])
        if changed_files:
            args.extend(["--changed-files", ",".join(changed_files)])
        if token:
            args.extend(["--approval", json.dumps(token)])

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )

        parsed: dict[str, Any] = {}
        if result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
            except json.JSONDecodeError:
                parsed = {"detail": result.stdout.strip()}

        raw = {
            "exit_code": result.returncode,
            **parsed,
        }

        return GateResult(
            decision=raw.get("decision", ""),
            gate=raw.get("gate", "BLOCK"),
            reason_code=raw.get("reasonCode", "GATE_ERROR"),
            risk_level=raw.get("riskLevel", ""),
            required_gates=raw.get("requiredGates"),
            policy_version=raw.get("policyVersion", ""),
            detail=raw.get("detail", ""),
            evidence_identity=raw.get("evidenceIdentity"),
            raw=raw,
        )
    finally:
        try:
            os.unlink(manifest_file)
        except FileNotFoundError:
            pass
