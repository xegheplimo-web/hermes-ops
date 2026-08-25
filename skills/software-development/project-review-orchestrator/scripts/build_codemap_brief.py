#!/usr/bin/env python3
"""Build a Devin Codemap brief from a reconciled review.

Reads reconciled-review.json and the repository metadata, then renders
the codemap-prompt.md template with actual values. Produces codemap-brief.md
under the output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from trace_context import add_trace_argument, get_trace_id
    _HAS_TRACE = True
except ImportError:
    _HAS_TRACE = False


TEMPLATE_VARS = frozenset([
    "PROJECT",
    "HEAD_SHA",
    "RUN_ID",
    "RECONCILED_FINDINGS",
    "CODEMAP_QUESTIONS",
])

CODEPATH_PATTERN = re.compile(r"[A-Za-z]:\\(?:[^\\\n]+\\)*[^\\\n]+\.\w+")


def resolve_run_id(reconciled_path: Path) -> str:
    """Extract RUN_ID from the reconciled path, falling back to dir name."""
    parent = reconciled_path.parent
    # Walk up looking for the .hermes/reviews/<RUN_ID> pattern
    parts = parent.parts
    for i, part in enumerate(parts):
        if part == "reviews" and i > 0 and parts[i - 1] == ".hermes":
            if i + 1 < len(parts):
                return parts[i + 1]
    # Fallback: last two directory components
    return parent.name


def resolve_project(repo: Path) -> str:
    """Resolve project name from the repo root directory name."""
    return repo.resolve().name


def git_cmd(repo: Path, cmd: list[str]) -> str:
    """Run a git command and return its stripped stdout."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            cmd,
            output=proc.stdout,
            stderr=proc.stderr.strip(),
        )
    return proc.stdout.strip()


def infer_modules(evidence_refs: list[str]) -> list[str]:
    """Infer module paths from evidence file references."""
    modules: set[str] = set()
    for ref in evidence_refs:
        if not ref:
            continue
        p = Path(ref.replace("\\", "/"))
        parts = p.parts
        # Take the first 2-3 path components as the module
        if len(parts) >= 3:
            modules.add("/".join(parts[:2]))
        elif len(parts) >= 2:
            modules.add("/".join(parts[:1]))
        else:
            modules.add(str(p))
    return sorted(modules, key=lambda x: (not x.endswith(".py"), x))


def render_template(
    template_text: str,
    *,
    project: str,
    head_sha: str,
    run_id: str,
    reconciled_findings: list[dict[str, Any]],
) -> str:
    """Render the codemap template with actual values."""

    # Build the reconciled findings section for actionable items only
    actionable = [f for f in reconciled_findings if f.get("disposition") != "DISAGREE"]
    findings_lines: list[str] = []
    for f in actionable:
        fid = f.get("id") or f.get("finding_id", "?")
        title = f.get("title", "?")
        disp = f.get("disposition", "?")
        sev = f.get("final_severity", "?")
        action = f.get("required_action", "?")
        evidence = f.get("evidence_refs", [])
        refs_str = ", ".join(evidence[:5]) if evidence else "(none)"
        findings_lines.append(
            f"- **{fid}** ({sev}, {disp}): {title}  \n"
            f"  - Required: {action}  \n"
            f"  - Evidence refs: {refs_str}"
        )

    findings_block = "\n".join(findings_lines) if findings_lines else "- (no actionable findings)"

    # Infer modules from actionable findings
    all_refs: list[str] = []
    for f in actionable:
        all_refs.extend(f.get("evidence_refs", []))
    implicated_modules = infer_modules(all_refs)
    modules_block = "\n".join(f"- `{m}`" for m in implicated_modules) if implicated_modules else "- (no module paths inferred)"

    # Build CODEMAP_QUESTIONS based on actionable findings with security/high severity
    high_sev = [f for f in actionable if f.get("final_severity") in ("high", "critical")]
    if high_sev:
        sev_module_refs = []
        for f in high_sev:
            sev_module_refs.extend(f.get("evidence_refs", []))
        sev_modules = infer_modules(sev_module_refs)
        sev_q = "\n".join(f"- What is the exact flow through `{m}`?" for m in sev_modules[:5])
        questions_block = f"## High/Critical Priority Areas\n\n{sev_q}"
    else:
        questions_block = "## Questions Requiring Exact Repository Answers\n\n(No high/critical priority findings identified.)"

    replacements = {
        "{{PROJECT}}": project,
        "{{HEAD_SHA}}": head_sha,
        "{{RUN_ID}}": run_id,
        "{{RECONCILED_FINDINGS}}": findings_block,
        "{{CODEMAP_QUESTIONS}}": questions_block,
    }

    result = template_text
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    # Add the implicated modules appendix if the template didn't already capture it
    if "{{IMPLICATED_MODULES}}" not in template_text:
        result += f"\n\n## Implicated Modules\n\n{modules_block}\n"

    return result


def build_questions(reconciled_findings: list[dict[str, Any]]) -> str:
    """Build a static set of codemap questions based on findings patterns."""
    questions: list[str] = []
    topics: set[str] = set()
    for f in reconciled_findings:
        if f.get("disposition") == "DISAGREE":
            continue
        title = f.get("title", "").lower()
        evidence = f.get("evidence_refs", [])
        if any("security" in title or "auth" in title for t in [title]):
            topics.add("security_boundaries")
        if any("state" in title or "memory" in title for t in [title]):
            topics.add("state_ownership")
        if any("error" in title or "retry" in title or "recovery" in title for t in [title]):
            topics.add("error_retry")
        if any("concurr" in title or "race" in title or "lock" in title for t in [title]):
            topics.add("concurrency")
        if evidence:
            topics.add("implicated_modules")

    if "entrypoints" not in topics:
        topics.add("entrypoints")

    topic_map = {
        "entrypoints": "1. What are every program's entrypoints and how is control handed off?",
        "security_boundaries": "2. Map every security boundary: where does auth/authorization happen and where is it missing?",
        "state_ownership": "3. Who owns each piece of persistent/runtime state and what is the access pattern?",
        "error_retry": "4. Trace every error propagation path and retry chain — where is recovery incomplete?",
        "concurrency": "5. Where does concurrent access happen and what locks/leases protect shared state?",
        "implicated_modules": "6. Walk the modules listed in Implicated Modules — what are their public contracts and internal flows?",
    }

    for t in sorted(topics):
        questions.append(topic_map.get(t, f"- Inspect {t}"))
    return "\n".join(questions)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Devin Codemap brief from reconciled review."
    )
    parser.add_argument("--reconciled", required=True, help="Path to reconciled-review.json")
    parser.add_argument("--repo", required=True, help="Repository root path")
    parser.add_argument("--out", required=True, help="Output directory for codemap-brief.md")
    if _HAS_TRACE:
        add_trace_argument(parser)
    args = parser.parse_args()
    trace_id = get_trace_id(args) if _HAS_TRACE else os.environ.get("HERMES_TRACE_ID", "")

    try:
        reconciled_path = Path(args.reconciled)
        repo_path = Path(args.repo).resolve()
        out_dir = Path(args.out)

        if not reconciled_path.is_file():
            print(json.dumps({"ok": False, "error": f"Reconciled review not found: {reconciled_path}"}), file=sys.stderr)
            return 1
        if not (repo_path / ".git").is_dir():
            print(json.dumps({"ok": False, "error": f"Not a Git repository: {repo_path}"}), file=sys.stderr)
            return 1

        # Load reconciled findings
        reconciled = json.loads(reconciled_path.read_text(encoding="utf-8"))
        # Support both the array format and a possible { "findings": [...] } wrapper
        if isinstance(reconciled, dict):
            findings = reconciled.get("findings", reconciled.get("reconciled", []))
        elif isinstance(reconciled, list):
            findings = reconciled
        else:
            findings = []
        if not isinstance(findings, list):
            findings = []

        # Git metadata
        head_sha = git_cmd(repo_path, ["rev-parse", "HEAD"])
        branch = git_cmd(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
        project = resolve_project(repo_path)
        run_id = resolve_run_id(reconciled_path)

        # Load and render template
        skill_dir = Path(__file__).resolve().parent.parent
        template_path = skill_dir / "templates" / "codemap-prompt.md"

        if not template_path.is_file():
            print(json.dumps({"ok": False, "error": f"Template not found: {template_path}"}), file=sys.stderr)
            return 1

        template_text = template_path.read_text(encoding="utf-8")

        rendered = render_template(
            template_text,
            project=project,
            head_sha=head_sha,
            run_id=run_id,
            reconciled_findings=findings,
        )

        # Build and append questions section
        questions = build_questions(findings)
        rendered += f"\n\n## Repository-Specific Questions\n\n{questions}\n"

        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / "codemap-brief.md"
        md_path.write_text(rendered, encoding="utf-8")

        result = {
            "ok": True,
            "codemap_brief": str(md_path.resolve()),
            "commit": head_sha,
            "branch": branch,
            "trace_id": trace_id,
        }
        print(json.dumps(result, indent=2))
        return 0

    except subprocess.CalledProcessError as exc:
        print(json.dumps({"ok": False, "error": f"Git command failed ({exc.returncode}): {exc.stderr}"}), file=sys.stderr)
        return 1
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