#!/usr/bin/env python3
"""Build a sanitized review packet from repo evidence + Hermes analysis."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    from trace_context import add_trace_argument, get_trace_id
    _HAS_TRACE = True
except ImportError:
    _HAS_TRACE = False

SECRET_PATTERNS = [
    # Private keys (PEM blocks)
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
    # OpenAI / sk- prefixed API keys
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_OPENAI_KEY]"),
    # GitHub tokens (ghp_/gho_/ghu_/ghs_/ghr_)
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    # AWS access key ID
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "[REDACTED_AWS_ACCESS_KEY]"),
    # AWS secret access key
    (re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*\S+"), "[REDACTED_AWS_SECRET_KEY]"),
    # Common key=value secrets (password, secret, token, etc.)
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    # Bearer tokens in headers
    (re.compile(r"(?i)\bAuthorization:\s*Bearer\s*[A-Za-z0-9._~+/=-]+"), "Authorization: Bearer ***"),
    # Slack tokens (xoxb-, xoxa-, xoxp-, xoxr-, xoxs-)
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    # JWT tokens (eyJ... encoded header)
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED_JWT]"),
    # .env.* file references in paths
    (re.compile(r"\S*?\.env\.[A-Za-z_][A-Za-z0-9_]*\b"), "[REDACTED_ENV_FILE]"),
    # *.pem / *.key file references
    (re.compile(r"\S*?\.(?:pem|key)\b"), "[REDACTED_CERT_FILE]"),
    # Browser profile paths referencing login data / cookies
    (re.compile(r"(?i)\S*(?:browser|profile)\S*(?:\\|/)\S*(?:login\s*data|cookies)\S*"), "[REDACTED_BROWSER_PATH]"),
]


def _has_high_entropy(s: str) -> bool:
    """Check if a string contains at least 3 character classes (upper, lower, digit, special)."""
    classes = 0
    if any(c.isupper() for c in s):
        classes += 1
    if any(c.islower() for c in s):
        classes += 1
    if any(c.isdigit() for c in s):
        classes += 1
    if any(not c.isalnum() for c in s):
        classes += 1
    return classes >= 3


def redact(text: str) -> tuple[str, int]:
    count = 0
    for pattern, replacement in SECRET_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    # Generic high-entropy token pattern (>=20 chars, >=3 char classes)
    generic_re = re.compile(r"\b[A-Za-z0-9-_=]{20,}\b")
    def _entropy_replacer(m: re.Match) -> str:
        return "[REDACTED_TOKEN]" if _has_high_entropy(m.group()) else m.group()
    text, n = generic_re.subn(_entropy_replacer, text)
    count += n
    return text, count


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", default="openai-api", choices=["openai-api", "chatgpt-human"],
                        help="Review delivery mode (default: openai-api)")
    if _HAS_TRACE:
        add_trace_argument(parser)
    args = parser.parse_args()
    trace_id = get_trace_id(args) if _HAS_TRACE else (os.environ.get("HERMES_TRACE_ID", ""))
    try:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        analysis_raw = Path(args.analysis).read_text(encoding="utf-8")
        analysis, redactions = redact(analysis_raw)
        packet = {
            "schema_version": "1.0",
            "purpose": "independent_project_review",
            "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "trace_id": trace_id,
            "review_mode": args.mode,
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
        print(json.dumps({"ok": True, "packet": str(out_path), "sha256": packet["packet_sha256"], "trace_id": trace_id, "redactions": redactions}, indent=2))
        return 0
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())