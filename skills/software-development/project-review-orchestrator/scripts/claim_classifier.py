#!/usr/bin/env python3
"""
Epistemic claim classifier — FACT / INFERENCE / UNKNOWN.

Closes the pipeline gap between "Hermes Analysis" and "Strategy Router":
every non-trivial claim in hermes-analysis.md must be labelled with its
epistemic status so that downstream stages can refuse to build on air.

Rules (deterministic, evidence-first):

  FACT       claim carries at least one resolvable evidence reference
             (file path present in repo-evidence, commit SHA, test name,
             line ref, or explicit `[E:<id>]` marker)
  INFERENCE  claim carries reasoning language or a self-declared
             INFERENCE/UNVERIFIED label but no resolvable evidence
  UNKNOWN    claim declares absence of knowledge, or asserts something
             non-trivial with neither evidence nor reasoning trail

Downstream contract:
  - FACT      may become an implementation task
  - INFERENCE must be verified or downgraded before implementation
  - UNKNOWN   becomes an INVESTIGATION task, never an implementation task
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from trace_context import add_trace_argument, get_trace_id
    _HAS_TRACE = True
except ImportError:
    _HAS_TRACE = False


# ── Labels ──────────────────────────────────────────────────────────────────

FACT = "FACT"
INFERENCE = "INFERENCE"
UNKNOWN = "UNKNOWN"
ALL_LABELS = [FACT, INFERENCE, UNKNOWN]

# Claims allowed to carry no evidence (structural / navigational text).
_TRIVIAL_PATTERNS = [
    re.compile(r"^#{1,6}\s"),           # markdown heading
    re.compile(r"^\s*[-*]\s*$"),        # empty bullet
    re.compile(r"^\s*\|"),              # table row
    re.compile(r"^\s*```"),             # fence
    re.compile(r"^\s*\d+\.\s*$"),
]

# Explicit self-declared labels win over heuristics.
_EXPLICIT_UNKNOWN = re.compile(r"\b(UNKNOWN|NOT VERIFIED|NO EVIDENCE)\b")
_EXPLICIT_INFERENCE = re.compile(r"\b(INFERENCE|UNVERIFIED|ASSUMPTION|ASSUMED)\b")
_EXPLICIT_FACT = re.compile(r"\b(FACT|VERIFIED)\b")

# Evidence reference shapes.
_EVIDENCE_MARKER = re.compile(r"\[E:[A-Za-z0-9_.:/-]+\]")
_COMMIT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_FILE_PATH = re.compile(
    r"\b[\w./\\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|php|sh|sql|ya?ml|toml|json|md|cfg|ini)\b"
)
_LINE_REF = re.compile(r"\b(?:line|L)\s?\d+(?:-\d+)?\b", re.IGNORECASE)
_TEST_REF = re.compile(r"\b(?:test_[\w]+|[\w]+_test|::test[\w]*)\b")

# Reasoning / hedging language → INFERENCE.
_HEDGE_WORDS = [
    "likely", "probably", "appears", "appears to", "seems", "suggests",
    "presumably", "may ", "might ", "could ", "i expect", "we expect",
    "should be", "would be", "in theory", "implies", "indicates",
    "consistent with", "reasonable to", "my read", "reading the code",
]

# Absence-of-knowledge language → UNKNOWN.
_IGNORANCE_WORDS = [
    "unclear", "not clear", "unsure", "no idea", "cannot determine",
    "could not determine", "unable to determine", "not inspected",
    "not examined", "needs investigation", "requires investigation",
    "not measured", "no data", "no tests found for", "untested",
    "unaudited", "unverifiable", "tbd", "to be determined",
]

# Assertive language that demands evidence.
_ASSERTIVE_WORDS = [
    " is ", " are ", " has ", " have ", " does ", " will ", " must ",
    " always ", " never ", " all ", " none ", " every ", " broken",
    " fails", " passes", " missing", " duplicated", " leaks",
]


# ── Core classification ─────────────────────────────────────────────────────


def is_trivial(claim: str) -> bool:
    """Structural text that carries no epistemic weight."""
    s = claim.strip()
    if len(s) < 12:
        return True
    return any(p.match(s) for p in _TRIVIAL_PATTERNS)


def extract_evidence_refs(claim: str, known_paths: set[str] | None = None) -> list[str]:
    """Extract evidence references from a claim.

    When ``known_paths`` is supplied (from repo-evidence.json), a file path is
    only counted as evidence if it actually exists in the snapshot — this is
    what stops a hallucinated filename from laundering an inference into a fact.
    """
    refs: list[str] = []
    refs.extend(_EVIDENCE_MARKER.findall(claim))
    refs.extend(_LINE_REF.findall(claim))
    refs.extend(_TEST_REF.findall(claim))

    for path in _FILE_PATH.findall(claim):
        norm = path.replace("\\", "/").lstrip("./")
        if known_paths is None:
            refs.append(path)
        elif norm in known_paths or any(k.endswith("/" + norm) or k == norm for k in known_paths):
            refs.append(path)
        else:
            # Unresolvable path: explicitly not evidence.
            continue

    # Commit SHAs only count when they look deliberate (>= 7 hex, not a word).
    for sha in _COMMIT_SHA.findall(claim):
        if len(sha) >= 7 and not sha.isdigit():
            refs.append(sha)

    # Dedupe, keep order.
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def classify_claim(
    claim: str,
    known_paths: set[str] | None = None,
    declared_refs: list[str] | None = None,
) -> dict:
    """Classify one claim as FACT / INFERENCE / UNKNOWN with reasons."""
    reasons: list[str] = []
    text = claim.strip()
    lower = text.lower()

    if is_trivial(text):
        return {
            "claim": text,
            "label": FACT,
            "trivial": True,
            "evidence_refs": [],
            "reasons": ["Structural/trivial text — no epistemic claim"],
            "requires_verification": False,
            "blocks_implementation": False,
        }

    refs = list(declared_refs or [])
    refs.extend(r for r in extract_evidence_refs(text, known_paths) if r not in refs)

    # 1. Explicit self-declared labels take priority.
    if _EXPLICIT_UNKNOWN.search(text):
        label = UNKNOWN
        reasons.append("Explicit UNKNOWN label in claim")
    elif _EXPLICIT_INFERENCE.search(text):
        label = INFERENCE
        reasons.append("Explicit INFERENCE/UNVERIFIED label in claim")
    elif any(w in lower for w in _IGNORANCE_WORDS):
        label = UNKNOWN
        reasons.append("Absence-of-knowledge language detected")
    elif refs:
        label = FACT
        reasons.append(f"Resolvable evidence reference(s): {len(refs)}")
        if _EXPLICIT_FACT.search(text):
            reasons.append("Explicit FACT label corroborated by evidence")
        # Hedged + evidence → still inference about meaning, not about existence.
        if any(w in lower for w in _HEDGE_WORDS):
            label = INFERENCE
            reasons.append("Evidence present but claim is hedged → INFERENCE")
    elif any(w in lower for w in _HEDGE_WORDS):
        label = INFERENCE
        reasons.append("Reasoning/hedging language without evidence reference")
    elif _EXPLICIT_FACT.search(text):
        label = INFERENCE
        reasons.append("Claim asserts FACT but carries no resolvable evidence → downgraded")
    elif any(w in lower for w in _ASSERTIVE_WORDS):
        label = UNKNOWN
        reasons.append("Assertive claim with neither evidence nor reasoning trail")
    else:
        label = INFERENCE
        reasons.append("Non-assertive claim without evidence — treated as inference")

    return {
        "claim": text,
        "label": label,
        "trivial": False,
        "evidence_refs": refs,
        "reasons": reasons,
        # INFERENCE must be verified; UNKNOWN must be investigated first.
        "requires_verification": label in (INFERENCE, UNKNOWN),
        "blocks_implementation": label == UNKNOWN,
    }


# ── Document-level pass ─────────────────────────────────────────────────────

_SECTION_RE = re.compile(r"^#{1,6}\s+(.*)$")


def _load_known_paths(evidence_path: Path | None) -> set[str] | None:
    """Collect the file paths present in repo-evidence.json, if available."""
    if not evidence_path or not evidence_path.exists():
        return None
    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    paths: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("path", "file", "filename", "relpath") and isinstance(v, str):
                    paths.add(v.replace("\\", "/").lstrip("./"))
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, str) and ("/" in item or "." in item):
                    paths.add(item.replace("\\", "/").lstrip("./"))
                else:
                    walk(item)

    walk(data)
    return paths or None


def classify_document(text: str, known_paths: set[str] | None = None) -> dict:
    """Classify every claim line in a markdown analysis document."""
    results: list[dict] = []
    section = ""
    in_fence = False

    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue

        m = _SECTION_RE.match(stripped)
        if m:
            section = m.group(1).strip()
            continue

        # Strip bullet/number prefixes before classifying.
        claim = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", stripped)
        if not claim:
            continue

        r = classify_claim(claim, known_paths)
        if r["trivial"]:
            continue
        r["line"] = lineno
        r["section"] = section
        results.append(r)

    counts = {lbl: sum(1 for r in results if r["label"] == lbl) for lbl in ALL_LABELS}
    total = len(results) or 1
    unknowns = [r for r in results if r["label"] == UNKNOWN]

    return {
        "ok": True,
        "claim_count": len(results),
        "counts": counts,
        "fact_ratio": round(counts[FACT] / total, 4),
        "unknown_ratio": round(counts[UNKNOWN] / total, 4),
        # An analysis that is mostly unlabelled assertion is not a basis for work.
        "analysis_grounded": counts[UNKNOWN] / total <= 0.25 and counts[FACT] > 0,
        "investigation_required": [
            {"line": r["line"], "section": r["section"], "claim": r["claim"]}
            for r in unknowns
        ],
        "claims": results,
    }


def to_investigation_findings(doc: dict) -> list[dict]:
    """Turn UNKNOWN claims into INVESTIGATION findings for decompose_tasks.py."""
    out: list[dict] = []
    for i, r in enumerate(doc.get("claims", []), 1):
        if r["label"] != UNKNOWN:
            continue
        out.append({
            "id": f"UNK-{i:03d}",
            "title": f"Investigate: {r['claim'][:80]}",
            "claim": r["claim"],
            "rationale": "; ".join(r["reasons"]),
            "final_position": "UNVERIFIED",
            "final_severity": "medium",
            "evidence_refs": r.get("evidence_refs", []),
            "epistemic_label": UNKNOWN,
            "source_section": r.get("section", ""),
            "source_line": r.get("line"),
        })
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Label analysis claims FACT / INFERENCE / UNKNOWN."
    )
    parser.add_argument("--analysis", help="Path to hermes-analysis.md")
    parser.add_argument("--evidence", help="Path to repo-evidence.json (path resolution)")
    parser.add_argument("--out", help="Output JSON path (claims-labels.json)")
    parser.add_argument("--findings-out", help="Write UNKNOWN claims as investigation findings")
    parser.add_argument("--fail-on-ungrounded", action="store_true",
                        help="Exit 3 when UNKNOWN ratio > 25%% or no FACT claims")
    if _HAS_TRACE:
        add_trace_argument(parser)
    args = parser.parse_args()
    trace_id = get_trace_id(args) if _HAS_TRACE else os.environ.get("HERMES_TRACE_ID", "")

    try:
        if args.analysis:
            text = Path(args.analysis).read_text(encoding="utf-8")
        else:
            text = sys.stdin.read()
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    known_paths = _load_known_paths(Path(args.evidence) if args.evidence else None)
    doc = classify_document(text, known_paths)
    doc["trace_id"] = trace_id
    doc["evidence_resolution"] = "strict" if known_paths else "permissive"

    payload = json.dumps(doc, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")
        print(json.dumps({
            "ok": True, "trace_id": trace_id, "out": args.out,
            "counts": doc["counts"], "analysis_grounded": doc["analysis_grounded"],
        }, indent=2))
    else:
        print(payload)

    if args.findings_out:
        findings = to_investigation_findings(doc)
        Path(args.findings_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.findings_out).write_text(
            json.dumps({"findings": findings, "trace_id": trace_id}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if args.fail_on_ungrounded and not doc["analysis_grounded"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
