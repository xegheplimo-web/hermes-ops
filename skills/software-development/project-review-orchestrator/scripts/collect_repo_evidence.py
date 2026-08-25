#!/usr/bin/env python3
"""
Collect deterministic repository evidence for Hermes project reviews.

The collector intentionally avoids exporting file contents. It records
metadata, tracked paths, Git state, test/manifests/CI presence, TODO
counts, recent commits, and churn statistics.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

try:
    from trace_context import add_trace_argument, get_trace_id
    _HAS_TRACE = True
except ImportError:
    _HAS_TRACE = False


LANGUAGE_BY_SUFFIX = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".c": "C",
    ".h": "C/C++ Header", ".hpp": "C/C++ Header",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".scala": "Scala",
    ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".vue": "Vue", ".svelte": "Svelte",
    ".md": "Markdown", ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".toml": "TOML",
}

MANIFEST_NAMES = {
    "package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock",
    "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    "poetry.lock", "uv.lock", "cargo.toml", "cargo.lock",
    "go.mod", "go.sum", "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "gemfile", "dockerfile", "docker-compose.yml",
    "docker-compose.yaml",
}

SENSITIVE_BASENAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "credentials.json", "secrets.json",
    "login data", "cookies",
}

SECURITY_KEYWORDS = {
    "auth", "oauth", "login", "credential", "secret", "token",
    "permission", "policy", "security", "billing", "payment",
    "migration", "deploy", "production",
}

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)


class EvidenceError(RuntimeError):
    pass


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if check and proc.returncode != 0:
        raise EvidenceError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def resolve_repo(path: Path) -> Path:
    root = run_git(path, "rev-parse", "--show-toplevel").strip()
    if not root:
        raise EvidenceError(f"Not a Git repository: {path}")
    return Path(root).resolve()


def tracked_files(repo: Path) -> list[str]:
    raw = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if raw.returncode != 0:
        raise EvidenceError(raw.stderr.decode("utf-8", errors="replace").strip())
    return [p.decode("utf-8", errors="replace") for p in raw.stdout.split(b"\0") if p]


def is_sensitive_path(rel_path: str) -> bool:
    p = Path(rel_path)
    base = p.name.lower()
    if base in SENSITIVE_BASENAMES or base.startswith(".env."):
        return True
    return p.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}


def language_for(path: str) -> str:
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "Other")


def looks_like_test(path: str) -> bool:
    lower = path.replace("\\", "/").lower()
    name = Path(lower).name
    return (
        "/tests/" in f"/{lower}" or "/test/" in f"/{lower}"
        or "/__tests__/" in f"/{lower}" or name.startswith("test_")
        or name.endswith("_test.py") or ".test." in name or ".spec." in name
    )


def looks_like_ci(path: str) -> bool:
    norm = path.replace("\\", "/").lower()
    base = Path(norm).name
    return (
        norm.startswith(".github/workflows/")
        or base in {".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile", "circle.yml"}
        or norm.startswith(".circleci/")
    )


def looks_security_sensitive(path: str) -> bool:
    parts = re.split(r"[/\\\\_.\-]+", path.lower())
    return any(part in SECURITY_KEYWORDS for part in parts)


def count_todos(repo: Path, files: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for rel in files:
        if is_sensitive_path(rel):
            continue
        path = repo / rel
        try:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            data = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in TODO_RE.findall(data):
            result[match.upper()] = result.get(match.upper(), 0) + 1
    return dict(sorted(result.items()))


def recent_commits(repo: Path, limit: int = 30) -> list[dict[str, str]]:
    fmt = "%H%x09%ad%x09%an%x09%s"
    raw = run_git(repo, "log", f"-n{limit}", f"--pretty=format:{fmt}", "--date=iso-strict")
    commits: list[dict[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            commits.append({"sha": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3]})
    return commits


def churn(repo: Path, days: int = 90) -> list[dict[str, object]]:
    raw = run_git(repo, "log", f"--since={days}.days", "--numstat", "--format=", check=False)
    score: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"added": 0, "deleted": 0})
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, filename = parts
        if added == "-" or deleted == "-":
            continue
        try:
            a, d = int(added), int(deleted)
        except ValueError:
            continue
        if is_sensitive_path(filename):
            continue
        score[filename]["added"] += a
        score[filename]["deleted"] += d
    ranked = sorted(
        ({"path": p, "added": v["added"], "deleted": v["deleted"], "churn": v["added"] + v["deleted"]}
         for p, v in score.items()),
        key=lambda x: int(x["churn"]), reverse=True,
    )
    return ranked[:30]


def safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def build_evidence(repo: Path) -> dict[str, object]:
    files = tracked_files(repo)
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    commit = run_git(repo, "rev-parse", "HEAD").strip()
    status_raw = run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    status_lines = [l for l in status_raw.splitlines() if l.strip()]

    language_counts = collections.Counter()
    language_bytes = collections.Counter()
    manifests: list[str] = []
    tests: list[str] = []
    ci_files: list[str] = []
    security_paths: list[str] = []
    tracked_bytes = 0

    for rel in files:
        if is_sensitive_path(rel):
            continue
        full = repo / rel
        size = safe_size(full)
        tracked_bytes += size
        lang = language_for(rel)
        language_counts[lang] += 1
        language_bytes[lang] += size
        if Path(rel).name.lower() in MANIFEST_NAMES:
            manifests.append(rel)
        if looks_like_test(rel):
            tests.append(rel)
        if looks_like_ci(rel):
            ci_files.append(rel)
        if looks_security_sensitive(rel):
            security_paths.append(rel)

    return {
        "schema_version": "1.0",
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "repository": {
            "root_name": repo.name, "branch": branch, "commit_sha": commit,
            "dirty": bool(status_lines), "changed_entry_count": len(status_lines),
            "changed_entries": status_lines[:100],
        },
        "files": {
            "tracked_count": len(files),
            "safe_scanned_count": sum(1 for f in files if not is_sensitive_path(f)),
            "tracked_bytes_safe": tracked_bytes,
            "languages_by_file": dict(language_counts.most_common()),
            "languages_by_bytes": dict(language_bytes.most_common()),
        },
        "project_structure": {
            "manifests": sorted(manifests),
            "test_file_count": len(tests),
            "test_files_sample": sorted(tests)[:100],
            "ci_files": sorted(ci_files),
            "security_sensitive_paths_sample": sorted(security_paths)[:100],
        },
        "maintenance": {
            "todo_markers": count_todos(repo, files),
            "recent_commits": recent_commits(repo),
            "top_churn_90_days": churn(repo),
        },
    }


def render_markdown(evidence: dict[str, object]) -> str:
    repo = evidence["repository"]
    files = evidence["files"]
    structure = evidence["project_structure"]
    maintenance = evidence["maintenance"]
    languages = files["languages_by_file"]
    lang_lines = "\n".join(f"- {n}: {c}" for n, c in list(languages.items())[:15]) or "- None"
    commits = maintenance["recent_commits"][:10]
    commit_lines = "\n".join(f"- `{c['sha'][:10]}` {c['date']} — {c['subject']}" for c in commits) or "- None"
    churn_rows = maintenance["top_churn_90_days"][:15]
    churn_lines = "\n".join(f"- `{i['path']}`: {i['churn']} (+{i['added']}/-{i['deleted']})" for i in churn_rows) or "- None"
    return f"""# Repository Evidence

## Snapshot
- Project: {repo['root_name']}
- Branch: {repo['branch']}
- Commit: `{repo['commit_sha']}`
- Dirty: {repo['dirty']}
- Changed entries: {repo['changed_entry_count']}
- Generated: {evidence['generated_at']}

## Files
- Tracked: {files['tracked_count']}
- Safe scanned: {files['safe_scanned_count']}
- Test files: {structure['test_file_count']}

## Languages
{lang_lines}

## Manifests
{chr(10).join(f'- `{x}`' for x in structure['manifests']) or '- None'}

## CI
{chr(10).join(f'- `{x}`' for x in structure['ci_files']) or '- None'}

## TODO Markers
```json
{json.dumps(maintenance['todo_markers'], indent=2)}
```

## Recent Commits
{commit_lines}

## Top Churn — 90 Days
{churn_lines}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-id", default=None, help="Run identifier (auto-generated if omitted)")
    parser.add_argument("--review-mode", default="openai-api", help="Review mode identifier (default: openai-api)")
    if _HAS_TRACE:
        add_trace_argument(parser)
    args = parser.parse_args()
    trace_id = get_trace_id(args) if _HAS_TRACE else ""
    try:
        repo = resolve_repo(Path(args.repo).resolve())
        out = Path(args.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        evidence = build_evidence(repo)
        if args.run_id:
            run_id = args.run_id
        else:
            short_sha = run_git(repo, "rev-parse", "--short", "HEAD").strip()
            run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ") + "-" + short_sha
        evidence["run_id"] = run_id
        evidence["trace_id"] = trace_id or run_id

        json_path = out / "repo-evidence.json"
        md_path = out / "repo-evidence.md"
        state_path = out / "state.json"
        json_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(render_markdown(evidence), encoding="utf-8")

        # Preserve orchestrator-owned state keys (started_at, trace_id, etc.) if they exist.
        base_state: dict = {}
        if state_path.exists():
            try:
                base_state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                base_state = {}

        state = {
            **base_state,
            "run_id": base_state.get("run_id", run_id),
            "trace_id": base_state.get("trace_id") or trace_id or run_id,
            "created_at": evidence["generated_at"],
            "status": "EVIDENCE_COLLECTED",
            "project": evidence["repository"]["root_name"],
            "branch": evidence["repository"]["branch"],
            "commit_sha": evidence["repository"]["commit_sha"],
            "dirty": evidence["repository"]["dirty"],
            "review_mode": args.review_mode,
        }
        artifacts = state.get("artifacts", {})
        artifacts.update({
            "repo_evidence": "repo-evidence.json",
            "repo_evidence_md": "repo-evidence.md",
            "state": "state.json",
        })
        state["artifacts"] = artifacts
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": True, "repo": str(repo), "commit": evidence["repository"]["commit_sha"],
                           "dirty": evidence["repository"]["dirty"], "run_id": run_id,
                           "trace_id": trace_id or run_id,
                           "json": str(json_path), "markdown": str(md_path), "state": str(state_path)}, indent=2))
        return 0
    except (EvidenceError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())