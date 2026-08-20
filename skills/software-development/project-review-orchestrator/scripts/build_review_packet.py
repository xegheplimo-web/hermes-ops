#!/usr/bin/env python3
"""Build a sanitized review packet from repo evidence + Hermes analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "[REDACTED_AWS_ACCESS_KEY]"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\bAuthorization:\s*Bearer\s+[A-Za-z0-9._-]+"), "Authorization: Bearer [REDACTED]"),
]


def redact(text: str) -> tuple[str, int]:
    count = 0
    for pattern, replacement in SECRET_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        analysis_raw = Path(args.analysis).read_text(encoding="utf-8")
        analysis, redactions = redact(analysis_raw)
        packet = {
            "schema_version": "1.0",
            "purpose": "independent_project_review",
            "repository_snapshot": evidence,
            "hermes_analysis": analysis,
            "security": {
                "source_files_included": False,
                "redaction_matches": redactions,
                "warning": "The packet is sanitized but must still be treated as potentially sensitive.",
            },
        }
        canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        packet["packet_sha256"] = sha256_text(canonical)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": True, "packet": str(out_path), "sha256": packet["packet_sha256"], "redactions": redactions}, indent=2))
        return 0
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
