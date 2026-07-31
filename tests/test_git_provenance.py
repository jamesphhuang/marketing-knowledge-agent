from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from marketing_knowledge_agent.git_provenance import (
    GitProvenanceError,
    validate_historical_git_provenance,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch", "source-line")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Tests")
    (root / "source.txt").write_text("source\n", encoding="utf-8")
    _git(root, "add", "source.txt")
    _git(root, "commit", "--quiet", "-m", "source")
    return root, _git(root, "rev-parse", "HEAD")


def test_same_source_branch_with_ancestor_commit_passes(tmp_path):
    root, source_commit = _repository(tmp_path)

    result = validate_historical_git_provenance(
        root,
        source_branch="source-line",
        source_commit=source_commit,
    )

    assert result["current_branch"] == "source-line"
    assert result["source_commit_is_ancestor"] is True
    assert result["branch_matches_source"] is True


def test_merged_source_commit_on_different_branch_passes(tmp_path):
    root, source_commit = _repository(tmp_path)
    _git(root, "switch", "-c", "integration-line")
    (root / "integration.txt").write_text("integration\n", encoding="utf-8")
    _git(root, "add", "integration.txt")
    _git(root, "commit", "--quiet", "-m", "integration")

    result = validate_historical_git_provenance(
        root,
        source_branch="source-line",
        source_commit=source_commit,
    )

    assert result["current_branch"] == "integration-line"
    assert result["source_commit_is_ancestor"] is True
    assert result["branch_matches_source"] is False


@pytest.mark.parametrize("source_branch", ["", "bad branch"])
def test_missing_or_invalid_source_branch_fails_closed(tmp_path, source_branch):
    root, source_commit = _repository(tmp_path)

    with pytest.raises(GitProvenanceError, match="source branch"):
        validate_historical_git_provenance(
            root,
            source_branch=source_branch,
            source_commit=source_commit,
        )


@pytest.mark.parametrize("source_commit", ["0" * 40, "tampered"])
def test_missing_or_tampered_source_commit_fails_closed(tmp_path, source_commit):
    root, _ = _repository(tmp_path)

    with pytest.raises(GitProvenanceError, match="source commit"):
        validate_historical_git_provenance(
            root,
            source_branch="source-line",
            source_commit=source_commit,
        )


def test_source_commit_outside_current_head_ancestry_fails_closed(tmp_path):
    root, source_commit = _repository(tmp_path)
    _git(root, "switch", "--orphan", "isolated-line")
    (root / "source.txt").unlink(missing_ok=True)
    (root / "isolated.txt").write_text("isolated\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "isolated")

    with pytest.raises(GitProvenanceError, match="ancestor"):
        validate_historical_git_provenance(
            root,
            source_branch="source-line",
            source_commit=source_commit,
        )


def test_detached_head_fails_closed(tmp_path):
    root, source_commit = _repository(tmp_path)
    _git(root, "switch", "--detach", source_commit)

    with pytest.raises(GitProvenanceError, match="detached"):
        validate_historical_git_provenance(
            root,
            source_branch="source-line",
            source_commit=source_commit,
        )
