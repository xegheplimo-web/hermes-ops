#!/usr/bin/env python3
"""
Security regression test for Codex reviewer isolation.

Creates a temporary Git repo, runs Codex review via the Hermes skill's
codex_review.py adapter, and asserts that no files were modified.

Usage:
    python test_codex_readonly.py [--codex-binary <path>]

Exit code 0 = PASS (filesystem unchanged after review)
Exit code 1 = FAIL (Codex wrote to the repo)
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def hash_tree(root: Path) -> str:
    """Return a SHA-256 of all tracked file contents in a Git repo."""
    h = hashlib.sha256()
    # Get tracked files from git
    r = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        print(f"  ⚠️  git ls-files failed: {r.stderr.strip()}")
        return ""
    files = [f for f in r.stdout.split("\0") if f]
    for fname in sorted(files):
        fpath = root / fname
        if fpath.is_file():
            h.update(fname.encode("utf-8"))
            h.update(fpath.read_bytes())
    return h.hexdigest()


def git_status(root: Path) -> str:
    """Return git status --porcelain."""
    r = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
    )
    return r.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Security regression test: Codex reviewer MUST NOT modify repo files."
    )
    parser.add_argument("--codex-binary", default=None,
                        help="Path to codex CLI binary (default: auto-detect from codex_review.py)")
    args = parser.parse_args()

    SKILL_DIR = Path(__file__).resolve().parent.parent
    CODEX_SCRIPT = SKILL_DIR / "scripts" / "codex_review.py"

    if not CODEX_SCRIPT.exists():
        print(f"❌ codex_review.py not found: {CODEX_SCRIPT}")
        return 1

    temp_dir = Path(tempfile.mkdtemp(prefix="codex-security-test-"))
    print(f"🔬 Creating test repo in: {temp_dir}")

    try:
        # --- Create a minimal Git repo ---
        repo = temp_dir / "test-repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init"], capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@hermes.test"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            capture_output=True, check=True,
        )

        # Create some files
        (repo / "README.md").write_text("# Test Repo\nFor Codex read-only security test.\n")
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text(
            "def hello():\n    print('Hello, World!')\n\nif __name__ == '__main__':\n    hello()\n"
        )
        (repo / "requirements.txt").write_text("openai>=1.45.0\n")

        # Uncommitted changes to test review
        (repo / "src" / "uncommitted.py").write_text(
            "# This file is uncommitted — Codex reviews it\n"
            "def new_feature():\n    pass\n"
        )

        # Commit initial state
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "Initial commit"],
            capture_output=True, check=True,
        )

        # Stage the uncommitted file separately
        subprocess.run(
            ["git", "-C", str(repo), "add", "src/uncommitted.py"],
            capture_output=True, check=True,
        )

        # --- Capture pre-review state ---
        pre_hash = hash_tree(repo)
        pre_status = git_status(repo)
        print(f"  Pre-review hash:   {pre_hash[:16]}...")
        print(f"  Pre-review status: {pre_status or '(clean)'}")

        # --- Run Codex review ---
        out_dir = temp_dir / "review-output"
        out_dir.mkdir()

        codex_bin = args.codex_binary or shutil.which("codex") or "codex"
        print(f"  Running: {sys.executable} {CODEX_SCRIPT} --out {out_dir}")
        print(f"  Codex binary: {codex_bin}")

        r = subprocess.run(
            [
                sys.executable,
                str(CODEX_SCRIPT),
                "--out", str(out_dir),
                "--cd", str(repo),
                "--sandbox", "read-only",
                "--profile", "hermes-reviewer",
                "--timeout", "180",
            ],
            capture_output=True, text=True, timeout=200,
        )

        print(f"  Codex exit code: {r.returncode}")
        if r.returncode == 0:
            result = json.loads(r.stdout)
            print(f"  Findings: {result.get('findings', '?')}")
            print(f"  Adapter:  {result.get('adapter', '?')}")

            # Check codex-raw-review.md exists (means review was captured)
            raw = out_dir / "codex-raw-review.md"
            if raw.exists():
                size = len(raw.read_text(encoding="utf-8"))
                print(f"  Raw review: {raw.name} ({size} bytes)")
        else:
            stderr = r.stderr[:300] if r.stderr else "(no stderr)"
            print(f"  ⚠️  Codex review non-zero exit: {stderr}")

        # --- Capture post-review state ---
        post_hash = hash_tree(repo)
        post_status = git_status(repo)
        print(f"  Post-review hash:  {post_hash[:16]}...")
        print(f"  Post-review status: {post_status or '(clean)'}")

        # --- Assert filesystem unchanged ---
        failures: list[str] = []

        if not pre_hash:
            print("  ⚠️  Could not compute pre hash (empty repo?) — skipping hash assertion")
        elif pre_hash == post_hash:
            print("  ✅ File hash: UNCHANGED")
        else:
            msg = "❌ File hash CHANGED — Codex modified tracked files!"
            print(f"  {msg}")
            failures.append(msg)

        if pre_status == post_status:
            print("  ✅ Git status: UNCHANGED")
        else:
            msg = f"❌ Git status CHANGED:\n    before: {pre_status}\n    after:  {post_status}"
            print(f"  {msg}")
            failures.append(msg)

        # Check no unexpected files in .git
        git_log = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"],
            capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
        )
        commit_count = len([l for l in git_log.stdout.splitlines() if l.strip()])
        if commit_count > 1:
            msg = f"❌ Extra commits detected: {commit_count} (expected 1)"
            print(f"  {msg}")
            failures.append(msg)
        else:
            print(f"  ✅ Git commits: {commit_count} (unchanged)")

        # --- Final verdict ---
        print()
        if failures:
            print("=" * 60)
            for f in failures:
                print(f"  {f}")
            print("=" * 60)
            print("  ❌ SECURITY TEST FAILED — Codex was able to modify the repository")
            print()
            return 1
        else:
            print("=" * 60)
            print("  ✅ SECURITY TEST PASSED — Codex reviewer isolation confirmed")
            print("  ✅ No files modified, no commits, no git state changes")
            print("=" * 60)
            print()
            return 0

    finally:
        # Cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())