from __future__ import annotations

import re
import subprocess
from pathlib import Path


_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class GitProvenanceError(RuntimeError):
    pass


def validate_historical_git_provenance(
    repo_root: Path,
    *,
    source_branch: str,
    source_commit: str,
) -> dict:
    root = Path(repo_root).resolve()
    if not isinstance(source_branch, str) or not source_branch.strip():
        raise GitProvenanceError("historical source branch is missing")
    source_branch = source_branch.strip()
    branch_check = _run_git(root, "check-ref-format", "--branch", source_branch)
    if branch_check.returncode != 0:
        raise GitProvenanceError("historical source branch format is invalid")

    if not isinstance(source_commit, str) or not _FULL_COMMIT_RE.fullmatch(source_commit):
        raise GitProvenanceError("historical source commit format is invalid")
    commit_check = _run_git(root, "cat-file", "-e", f"{source_commit}^{{commit}}")
    if commit_check.returncode != 0:
        raise GitProvenanceError("historical source commit is not traceable")

    branch_result = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_result.returncode == 1:
        raise GitProvenanceError("detached HEAD cannot validate historical provenance")
    if branch_result.returncode != 0:
        raise GitProvenanceError("unable to determine current Git branch")
    current_branch = branch_result.stdout.strip()
    if not current_branch:
        raise GitProvenanceError("detached HEAD cannot validate historical provenance")

    head_result = _run_git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head_result.returncode != 0:
        raise GitProvenanceError("unable to resolve current HEAD commit")
    current_head = head_result.stdout.strip()

    ancestry = _run_git(
        root,
        "merge-base",
        "--is-ancestor",
        source_commit,
        current_head,
    )
    if ancestry.returncode == 1:
        raise GitProvenanceError(
            "historical source commit is not an ancestor of current HEAD"
        )
    if ancestry.returncode != 0:
        raise GitProvenanceError("unable to verify historical source commit ancestry")

    return {
        "source_branch": source_branch,
        "source_commit": source_commit,
        "current_branch": current_branch,
        "current_head": current_head,
        "branch_matches_source": current_branch == source_branch,
        "source_commit_is_ancestor": True,
    }


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitProvenanceError("unable to execute Git provenance validation") from exc
