#!/usr/bin/env python3
"""Reconcile Hermes first-pass analysis with an independent external review.

Reads hermes-analysis.md (Markdown, 21 required sections) and
external-review.json (JSON array of findings). Produces reconciled-review.json
and reconciled-review.md under the output directory.
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

REQUIRED_SECTIONS = frozenset([
    "PROJECT SNAPSHOT",
    "CURRENT IMPLEMENTED ARCHITECTURE",
    "VERIFIED COMPLETED FEATURES",
    "PARTIAL FEATURES",
    "BROKEN FEATURES",
    "UNKNOWN AREAS",
    "CURRENT TEST/BUILD HEALTH",
    "SECURITY STATUS",
    "OPERATIONAL STATUS",
    "STATE MANAGEMENT",
    "CONCURRENCY",
    "MEMORY BOUNDARIES",
    "EXTERNAL DEPENDENCIES",
    "TECHNICAL DEBT",
    "ARCHITECTURE CONTRADICTIONS",
    "DUPLICATED RESPONSIBILITIES",
    "WRONG / STALE DOCUMENTATION",
    "MOST IMPORTANT RISKS",
    "SIMPLER ALTERNATIVES",
    "RECOMMENDED PRIORITY ORDER",
    "QUESTIONS FOR INDEPENDENT REVIEWER",
])


def resolve_section(heading: str) -> str:
    """Return the canonical section name for a heading."""
    stripped = heading.strip().upper().rstrip("#").strip()
    # Discard section numbering like "2. CURRENT IMPLEMENTED ARCHITECTURE"
    cleaned = re.sub(r"^\d+[\.\)]?\s*", "", stripped)
    for known in REQUIRED_SECTIONS:
        if known == cleaned or cleaned.startswith(known[:20]):
            return known
    return heading.strip()


def parse_analysis_sections(text: str) -> dict[str, str]:
    """Split the Hermes analysis markdown into a dict of section -> body."""
    sections: dict[str, str] = {}
    lines = text.splitlines()
    current_key = None
    current_body: list[str] = []

    for line in lines:
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if current_key is not None and current_key not in sections:
                sections[current_key] = "\n".join(current_body).strip()
            current_key = resolve_section(m.group(1))
            current_body = []
        else:
            current_body.append(line)

    if current_key is not None and current_key not in sections:
        sections[current_key] = "\n".join(current_body).strip()
    return sections


def load_external_findings(path: Path) -> list[dict[str, Any]]:
    """Load and validate the external-review.json file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Support both a bare findings array and the wrapped format from openai_review.py
    if isinstance(raw, dict):
        findings = raw.get("findings", [])
    elif isinstance(raw, list):
        findings = raw
    else:
        raise ValueError(f"Unexpected external-review structure: {type(raw).__name__}")
    if not isinstance(findings, list):
        raise ValueError("'findings' must be a JSON array")
    return findings


def match_finding_to_section(
    finding: dict[str, Any],
    sections: dict[str, str],
) -> tuple[str, str]:
    """Find the most relevant analysis section for a finding.

    Returns (canonical_section_name, matched_text).
    """
    corpus = {
        key: text.lower()
        for key, text in sections.items()
    }

    # Build search tokens from the finding's title, claim, and challenge
    title = str(finding.get("title", ""))
    claim = str(finding.get("claim", ""))
    challenge = str(finding.get("challenge_to_hermes", ""))
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", title + " " + claim + " " + challenge)
    tokens = sorted({t.lower() for t in tokens if len(t) > 3}, key=len, reverse=True)[:20]

    # Score each section by token overlap
    best_section = None
    best_score = 0
    matched = ""

    for key, text in corpus.items():
        score = sum(1 for t in tokens if t in text)
        if score > best_score:
            best_score = score
            best_section = key
            # Grab the first 3 paragraphs of the matching section as evidence
            paras = [p.strip() for p in sections[key].split("\n\n") if p.strip()]
            matched = " | ".join(paras[:3])

    if best_section is None:
        return ("OTHER", "")

    return (best_section, matched)


def classify_disposition(
    finding: dict[str, Any],
    section_text: str,
    section_name: str,
) -> tuple[str, str, bool]:
    """Classify a finding's disposition.

    Returns (disposition, rationale, verification_required).

    Dispositions:
    - NEW       => finding not addressed in Hermes analysis
    - UNVERIFIED => cannot verify with current evidence
    - AGREE     => Hermes agrees
    - PARTIAL   => Hermes partially agrees
    - DISAGREE  => Hermes disagrees
    """
    severity = finding.get("severity", "medium").lower()
    confidence = float(finding.get("confidence", 0.5))
    claim = str(finding.get("claim", ""))
    evidence_refs = finding.get("evidence_refs", [])

    # UNVERIFIED: low confidence or no evidence
    if confidence < 0.3 or (not evidence_refs and confidence < 0.5):
        return ("UNVERIFIED", "Low confidence or insufficient evidence to verify claim", True)

    # NEW: no relevant section found
    if not section_text or section_name == "OTHER":
        return ("NEW", "Finding not addressed in Hermes first-pass analysis", False)

    claim_lower = claim.lower()
    section_lower = section_text.lower()

    # Look for explicit disagreement markers in the section
    disagreement_patterns = [
        r"\bnot\s+(implemented|supported|addressed|covered)\b",
        r"\bdoes\s+not\s+(handle|support|cover)\b",
        r"\bmissing\b",
        r"\bunimplemented\b",
        r"\bno\s+(support|implementation|handling)\s+for\b",
    ]
    agreement_patterns = [
        r"\b(implemented|supported|handled|covered)\b",
        r"\bpresent\b",
        r"\bexists?\b",
        r"\bin\s+place\b",
    ]

    disagree_count = sum(1 for p in disagreement_patterns if re.search(p, claim_lower[:200]))
    agree_count = sum(1 for p in agreement_patterns if re.search(p, claim_lower[:200]))

    # If the section name aligns directly with the finding's topic
    topic_relevant = any(
        token in section_name.lower()
        for token in re.findall(r"[A-Za-z]{4,}", claim_lower)
    )

    if not topic_relevant and agree_count < disagree_count:
        return ("PARTIAL", "Section partially addresses the finding but gaps remain", False)

    if disagree_count > agree_count:
        return ("DISAGREE", "Hermes analysis does not support this finding based on current evidence", False)

    # Determine AGREE vs PARTIAL based on how well the section covers the claim
    claim_tokens = {t for t in re.findall(r"[A-Za-z]{4,}", claim_lower)}
    section_tokens = {t for t in re.findall(r"[A-Za-z]{4,}", section_lower)}
    overlap = len(claim_tokens & section_tokens) / max(len(claim_tokens), 1)

    if overlap > 0.4:
        return ("AGREE", "Finding is consistent with Hermes analysis", False)
    else:
        return ("PARTIAL", "Some aspects of the finding are addressed, others need clarification", False)


def build_required_action(disposition: str, severity: str) -> str:
    """Map a disposition+severity to a required action."""
    if disposition == "DISAGREE":
        return "none"
    if disposition == "UNVERIFIED":
        return "investigate"
    severity_upper = severity.upper()
    if severity_upper == "CRITICAL":
        return "immediate_fix"
    elif severity_upper == "HIGH":
        return "fix"
    elif severity_upper == "MEDIUM":
        return "address"
    else:
        return "consider"


def reconcile(
    analysis_text: str,
    external_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Produce a reconciled findings list."""
    sections = parse_analysis_sections(analysis_text)
    reconciled: list[dict[str, Any]] = []

    for i, finding in enumerate(external_findings):
        finding_id = finding.get("id", f"external-finding-{i}")
        title = str(finding.get("title", ""))
        severity = finding.get("severity", "medium").lower()
        confidence = float(finding.get("confidence", 0.5))
        claim = str(finding.get("claim", ""))
        evidence_refs = finding.get("evidence_refs", [])

        section_name, section_text = match_finding_to_section(finding, sections)
        disposition, rationale, verification_needed = classify_disposition(
            finding, section_text, section_name
        )

        # Pull hermes_position from the matched section
        if section_text:
            # Return first ~500 chars of the section
            hermes_pos = section_text[:500]
            if len(section_text) > 500:
                hermes_pos += "..."
        else:
            hermes_pos = "Not explicitly addressed in Hermes analysis"

        required_action = build_required_action(disposition, severity)

        reconciled.append({
            "id": finding_id,
            "title": title,
            "severity": severity,
            "confidence": round(confidence, 2),
            "disposition": disposition,
            "external_claim": claim,
            "hermes_position": hermes_pos,
            "evidence_refs": evidence_refs,
            "final_severity": severity,
            "rationale": rationale,
            "required_action": required_action,
            "verification_required": verification_needed,
        })

    return reconciled


def render_markdown_table(reconciled: list[dict[str, Any]]) -> str:
    """Render reconciled findings as a markdown matrix table."""
    header = "| ID | Title | Severity | Disposition | Rationale | Required Action |"
    sep = "|---|---|---|---|---|---|"
    rows: list[str] = []
    for r in reconciled:
        fid = r.get("id", r.get("finding_id", "?"))
        title = r["title"].replace("|", "\\|")
        sev = r["final_severity"]
        disp = r["disposition"]
        rat = r["rationale"].replace("|", "\\|")
        act = r["required_action"]
        rows.append(f"| {fid} | {title} | {sev} | {disp} | {rat} | {act} |")

    return f"# Reconciled Review\n\n## Summary Matrix\n\n{header}\n{sep}\n" + "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile Hermes analysis with external review findings."
    )
    parser.add_argument("--analysis", required=True, help="Path to hermes-analysis.md")
    parser.add_argument("--external", required=True, help="Path to external-review.json")
    parser.add_argument("--out", required=True, help="Output directory for reconciled files")
    if _HAS_TRACE:
        add_trace_argument(parser)
    args = parser.parse_args()
    trace_id = get_trace_id(args) if _HAS_TRACE else os.environ.get("HERMES_TRACE_ID", "")

    try:
        analysis_path = Path(args.analysis)
        external_path = Path(args.external)
        out_dir = Path(args.out)

        if not analysis_path.is_file():
            print(json.dumps({"ok": False, "error": f"Analysis file not found: {analysis_path}"}), file=sys.stderr)
            return 1
        if not external_path.is_file():
            print(json.dumps({"ok": False, "error": f"External review file not found: {external_path}"}), file=sys.stderr)
            return 1

        analysis_text = analysis_path.read_text(encoding="utf-8")
        external_findings = load_external_findings(external_path)

        if not external_findings:
            print(json.dumps({"ok": False, "error": "No findings in external review"}), file=sys.stderr)
            return 1

        reconciled = reconcile(analysis_text, external_findings)

        # Compute summary stats
        dispositions: dict[str, int] = {}
        for r in reconciled:
            d = r["disposition"]
            dispositions[d] = dispositions.get(d, 0) + 1

        out_dir.mkdir(parents=True, exist_ok=True)

        json_path = out_dir / "reconciled-review.json"
        # Wrap in dict with run_id, project, findings for consumers
        run_id = os.path.basename(str(out_dir))
        project = analysis_path.resolve().parent.parent.name if analysis_path else ""
        output = {
            "run_id": run_id,
            "trace_id": trace_id or run_id,
            "project": project,
            "findings": reconciled,
        }
        json_path.write_text(
            json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        md_text = render_markdown_table(reconciled)
        md_path = out_dir / "reconciled-review.md"
        md_path.write_text(md_text, encoding="utf-8")

        result = {
            "ok": True,
            "reconciled_count": len(reconciled),
            "dispositions": dispositions,
            "trace_id": trace_id or run_id,
            "json": str(json_path.resolve()),
            "markdown": str(md_path.resolve()),
        }
        print(json.dumps(result, indent=2))
        return 0

    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"Invalid JSON: {exc}"}), file=sys.stderr)
        return 1
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"IO error: {exc}"}), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"Unexpected error: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())