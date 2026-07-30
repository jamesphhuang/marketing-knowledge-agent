from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


SOURCE_COMMIT = "470c914ff52e5820bfce6915eac93a55097b7d8d"
SOURCE_BRANCH = "feat/retrieval-quality-typed-query"


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pre_activation_fixture_has_pinned_git_identity_and_no_live_symlinks(
    production_search_alias_pre_activation_repo,
):
    fixture = production_search_alias_pre_activation_repo

    assert _git(fixture, "branch", "--show-current") == SOURCE_BRANCH
    assert _git(fixture, "rev-parse", "HEAD") == SOURCE_COMMIT
    assert not any(path.is_symlink() for path in fixture.rglob("*"))


def test_pre_activation_runtime_bytes_match_exact_source_commit(
    production_search_alias_pre_activation_repo,
):
    fixture = production_search_alias_pre_activation_repo
    tracked_runtime = _git(
        _root(),
        "ls-tree",
        "-r",
        "--name-only",
        SOURCE_COMMIT,
        "src/marketing_knowledge_agent",
    ).splitlines()

    for relative in tracked_runtime:
        expected = subprocess.run(
            ["git", "show", f"{SOURCE_COMMIT}:{relative}"],
            cwd=_root(),
            check=True,
            capture_output=True,
        ).stdout
        actual = (fixture / relative).read_bytes()
        assert actual == expected, relative


def test_pre_activation_authority_inputs_are_copied_and_target_is_absent(
    production_search_alias_pre_activation_repo,
):
    fixture = production_search_alias_pre_activation_repo
    copied_inputs = (
        "data/governance/governance_decisions.sqlite",
        ".mka/content_index.sqlite",
        "obsidian_vault/MKA",
        "reports/production_search_alias_plan_v2",
    )

    for relative in copied_inputs:
        path = fixture / relative
        assert path.exists()
        assert not path.is_symlink()
        assert fixture in path.resolve().parents
    assert _sha256(
        fixture / "data/governance/governance_decisions.sqlite"
    ) == "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
    assert not (fixture / ".mka/search_alias_projection.json").exists()
