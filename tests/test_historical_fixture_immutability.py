from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import conftest as fixture_support


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


def _inventory(path: Path) -> list[dict]:
    if path.is_file():
        files = [(Path("."), path)]
    else:
        files = [
            (child.relative_to(path), child)
            for child in sorted(item for item in path.rglob("*") if item.is_file())
        ]
    return [
        {
            "relative_path": relative.as_posix(),
            "size": child.stat().st_size,
            "sha256": _sha256(child),
            "appledouble": child.name.startswith("._"),
        }
        for relative, child in files
    ]


def _input_entry(path: Path) -> dict:
    inventory = _inventory(path)
    digest = hashlib.sha256(
        json.dumps(
            inventory,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "relative_path": "fixture-input",
        "input_type": "file" if path.is_file() else "directory",
        "expected_size": sum(item["size"] for item in inventory),
        "expected_sha256": _sha256(path) if path.is_file() else digest,
        "file_count": len(inventory),
        "inventory": inventory,
        "immutable_authority_source": "temporary-test-manifest",
        "authority_manifest_hash": fixture_support.PARENT_AUTHORITY_ROOT_HASH,
    }


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


def test_every_historical_input_has_complete_immutable_authority():
    manifest_path = _root() / fixture_support.HISTORICAL_INPUT_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored_hash = manifest.pop("manifest_hash")
    actual_hash = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert stored_hash == actual_hash
    assert [item["relative_path"] for item in manifest["inputs"]] == list(
        fixture_support.HISTORICAL_INPUTS
    )
    for item in manifest["inputs"]:
        assert item["input_type"] in {"file", "directory"}
        assert item["expected_size"] >= 0
        assert len(item["expected_sha256"]) == 64
        assert item["file_count"] == len(item["inventory"])
        assert item["immutable_authority_source"]
        assert len(item["authority_manifest_hash"]) == 64
        assert all(
            set(entry) == {"relative_path", "size", "sha256", "appledouble"}
            for entry in item["inventory"]
        )


def test_file_drift_is_rejected_before_copy(tmp_path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "copied.txt"
    source.write_text("before", encoding="utf-8")
    expected = _input_entry(source)
    source.write_text("after", encoding="utf-8")

    with pytest.raises(AssertionError, match="immutable historical input drift"):
        fixture_support._copy_path(source, destination, expected)
    assert not destination.exists()


def test_historical_report_drift_is_rejected_before_fixture_copy(
    tmp_path,
    monkeypatch,
):
    relative = "reports/excel_preview/merchant_cases.json"
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    shutil.copy2(_root() / relative, source)
    expected = _input_entry(source)
    expected["relative_path"] = relative
    manifest = {
        "schema_version": 1,
        "inputs": [expected],
    }
    manifest["manifest_hash"] = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest_path = tmp_path / "immutable-inputs.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(fixture_support, "HISTORICAL_INPUTS", (relative,))
    monkeypatch.setattr(
        fixture_support,
        "HISTORICAL_INPUT_MANIFEST_PATH",
        manifest_path.relative_to(tmp_path),
    )
    source.write_bytes(source.read_bytes() + b"\ndrift")

    with pytest.raises(AssertionError, match="immutable historical input drift"):
        fixture_support._validate_immutable_input_manifest(tmp_path)


@pytest.mark.parametrize("drift", ["add", "delete", "modify"])
def test_directory_inventory_drift_is_rejected_before_copy(tmp_path, drift):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.txt").write_text("one", encoding="utf-8")
    (source / "two.txt").write_text("two", encoding="utf-8")
    expected = _input_entry(source)

    if drift == "add":
        (source / "three.txt").write_text("three", encoding="utf-8")
    elif drift == "delete":
        (source / "two.txt").unlink()
    else:
        (source / "one.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(AssertionError, match="immutable historical input drift"):
        fixture_support._copy_path(source, tmp_path / "copied", expected)
    assert not (tmp_path / "copied").exists()


@pytest.mark.parametrize("drift", ["add", "delete", "modify"])
def test_appledouble_inventory_drift_is_rejected_before_copy(tmp_path, drift):
    source = tmp_path / "source"
    source.mkdir()
    (source / "record.md").write_text("record", encoding="utf-8")
    companion = source / "._record.md"
    if drift != "add":
        companion.write_bytes(b"appledouble-before")
    expected = _input_entry(source)

    if drift == "add":
        companion.write_bytes(b"appledouble-added")
    elif drift == "delete":
        companion.unlink()
    else:
        companion.write_bytes(b"appledouble-after")

    with pytest.raises(AssertionError, match="immutable historical input drift"):
        fixture_support._copy_path(source, tmp_path / "copied", expected)
    assert not (tmp_path / "copied").exists()


def test_verified_copy_is_independent_from_later_live_source_drift(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "record.md"
    original.write_text("immutable", encoding="utf-8")
    expected = _input_entry(source)
    destination = tmp_path / "copied"

    fixture_support._copy_path(source, destination, expected)
    original.write_text("live drift", encoding="utf-8")

    assert (destination / "record.md").read_text(encoding="utf-8") == "immutable"
    assert fixture_support._inventory_entry(destination, expected) == expected


def test_missing_immutable_authority_fails_closed(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("data", encoding="utf-8")
    expected = _input_entry(source)
    expected["immutable_authority_source"] = ""

    with pytest.raises(AssertionError, match="immutable authority"):
        fixture_support._copy_path(source, tmp_path / "copied.txt", expected)
    assert not (tmp_path / "copied.txt").exists()


def test_fixture_workspace_removes_child_and_empty_parent(tmp_path):
    parent = tmp_path / ".test-fixtures"
    child = parent / "case"
    child_companion = parent / "._case"
    parent_companion = tmp_path / "._.test-fixtures"

    with fixture_support.managed_fixture_workspace(child):
        child.mkdir(parents=True)
        (child / "payload").write_text("data", encoding="utf-8")
        child_companion.write_bytes(b"child companion")
        parent_companion.write_bytes(b"parent companion")

    assert not child.exists()
    assert not child_companion.exists()
    assert not parent.exists()
    assert not parent_companion.exists()


@pytest.mark.parametrize("phase", ["setup", "test"])
def test_fixture_workspace_cleans_after_exception(tmp_path, phase):
    parent = tmp_path / ".test-fixtures"
    child = parent / phase

    with pytest.raises(RuntimeError, match=phase):
        with fixture_support.managed_fixture_workspace(child):
            child.mkdir(parents=True)
            (child / "partial").write_text("data", encoding="utf-8")
            raise RuntimeError(phase)

    assert not child.exists()
    assert not parent.exists()


def test_fixture_cleanup_is_idempotent(tmp_path):
    parent = tmp_path / ".test-fixtures"
    child = parent / "case"
    child.mkdir(parents=True)
    (child / "payload").write_text("data", encoding="utf-8")

    fixture_support.cleanup_fixture_path(child)
    fixture_support.cleanup_fixture_path(child)

    assert not child.exists()
    assert not parent.exists()


def test_fixture_cleanup_preserves_unrelated_parent_content(tmp_path):
    parent = tmp_path / ".test-fixtures"
    child = parent / "case"
    sibling = parent / "other"
    child.mkdir(parents=True)
    sibling.mkdir()
    (sibling / "keep").write_text("keep", encoding="utf-8")

    fixture_support.cleanup_fixture_path(child)

    assert not child.exists()
    assert (sibling / "keep").read_text(encoding="utf-8") == "keep"
    assert parent.exists()
