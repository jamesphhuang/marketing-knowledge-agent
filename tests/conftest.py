from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path

import pytest


SOURCE_BRANCH = "feat/retrieval-quality-typed-query"
V1_SOURCE_COMMIT = "0cfedf90b2f3f0ad8e061819ae6a63c281bdd11e"
V2_SOURCE_COMMIT = "470c914ff52e5820bfce6915eac93a55097b7d8d"
DECISION_STORE_SHA256 = "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
FORMAL_SQLITE_SHA256 = "74b6038ef5e0ae9077fb97f355b6b50ad8f7e80bb4281fe78199002b2db3effe"
MANAGED_VAULT_HASH = "b94c10de68939209e3076293935b78a81148524926805085c6573f53bd3a1881"
STORE_SYNC_EXECUTION_TREE_HASH = "195fa2b4d99533abd8ca1eea25452b2711fbdcfb767841cd56fbd8949ab35bcf"
ALIAS_BACKUP_ROOT_HASH = "0e636f08d9b69ce7e6015bdb2f6bc67e00a804a62613d4430c380938f756ed84"
STORE_SYNC_EXECUTION_ROOT_HASH = "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30"
STORE_SYNC_BACKUP_ROOT_HASH = "58fe888c3703bcaed896e8c2905ffce0d560e3bb87452c81748975d9707a7bd0"
STORE_SYNC_CONFIRMATION_ROOT_HASH = "a66af3756d43a4c042fdae0e20f02ca93317982ca9447660081b92157789d47e"
PARENT_AUTHORITY_ROOT_HASH = "fa7cba755e296d54c03f65c127bf6e1381ea16e1a3b00f4e06ac94b5a87b2033"
SCHEMA_V2_CONFIRMATION_ROOT_HASH = "40803f723f072fa683042aeaa6d465eef30412ca0e607fb13b9ba8861a853416"
SCHEMA_V2_EXECUTION_ROOT_HASH = "2813a7e9989c7f6b878d903c36e632e4ea38a0c8b4254fa2079a4900f57b58b7"
PLAN_MANIFEST_HASHES = {
    "reports/governance_decision_store_schema_v2_plan/schema_v2_plan_manifest.json":
        "3e697fdc37af4ee00523cb26a646b30bfb89b043b7bd3b3934d73c0909b924ea",
    "reports/parent_sync_plan_v2/store_data_sync_plan_v2_manifest.json":
        "e9101f37915e478beaf54c7969d979ce97b3109aee5677c4d2591886a6b2935c",
    "reports/production_search_alias_plan/production_search_alias_plan_manifest.json":
        "a53bb8fe36ca1cdac5a289002b4f3a681e88b29ad84cb396cb7e9e840e3371c2",
    "reports/production_search_alias_plan_v2/production_search_alias_plan_v2_manifest.json":
        "58b6a1c422f7d6d68ce5ea9f960d9afbdce67c9e446c4b09851b60e6a5c613e5",
}
IMMUTABLE_AUTHORITY_HASHES = frozenset(
    {
        ALIAS_BACKUP_ROOT_HASH,
        PARENT_AUTHORITY_ROOT_HASH,
        SCHEMA_V2_CONFIRMATION_ROOT_HASH,
        SCHEMA_V2_EXECUTION_ROOT_HASH,
        STORE_SYNC_BACKUP_ROOT_HASH,
        STORE_SYNC_CONFIRMATION_ROOT_HASH,
        STORE_SYNC_EXECUTION_ROOT_HASH,
        *PLAN_MANIFEST_HASHES.values(),
    }
)
HISTORICAL_INPUT_MANIFEST_PATH = Path(
    "tests/fixtures/historical_inputs_manifest.json"
)
HISTORICAL_INPUTS = (
    "data/governance/governance_decisions.sqlite",
    "data/governance/imports/parent-authority-approval-20260719",
    "data/governance/confirmations/decision-store-schema-v2-plan-2aab43cd463170f2",
    "data/governance/executions/decision-store-schema-v2-plan-2aab43cd463170f2",
    "data/governance/confirmations/store-data-sync-plan-v2-4c8eb2a08b399da4",
    "data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4",
    "data/governance/backups/store-data-sync-plan-v2-4c8eb2a08b399da4",
    "obsidian_vault/MKA",
    ".mka/content_index.sqlite",
    "reports/governance_decision_store_schema_v2_plan",
    "reports/parent_sync_plan_v2",
    "reports/production_search_alias_plan",
    "reports/production_search_alias_plan_v2",
    "reports/excel_preview/merchant_cases.json",
    "reports/asset_metadata_preview/asset_metadata_inventory.csv",
    "reports/asset_metadata_preview/human_review_template.csv",
    "reports/asset_metadata_review_validation/review_decision_status.csv",
    "reports/asset_metadata_apply_preview/asset_apply_preview.csv",
    "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv",
)


@pytest.fixture(scope="session")
def production_search_alias_pre_activation_repo(tmp_path_factory):
    root = _build_historical_repo(
        tmp_path_factory,
        V2_SOURCE_COMMIT,
        "production-search-alias-v2-pre-activation",
    )
    try:
        yield root
    finally:
        cleanup_fixture_path(root)


@pytest.fixture(scope="session")
def production_search_alias_v1_pre_activation_repo(tmp_path_factory):
    root = _build_historical_repo(
        tmp_path_factory,
        V1_SOURCE_COMMIT,
        "production-search-alias-v1-pre-activation",
    )
    try:
        yield root
    finally:
        cleanup_fixture_path(root)


def _build_historical_repo(tmp_path_factory, source_commit, prefix):
    formal_root = Path(__file__).resolve().parents[1]
    immutable_inputs = _validate_immutable_input_manifest(formal_root)
    _validate_live_historical_inputs(formal_root)
    root = tmp_path_factory.mktemp(prefix)
    try:
        _extract_exact_source_commit(formal_root, root, source_commit)

        for relative in HISTORICAL_INPUTS:
            _copy_path(
                formal_root / relative,
                root / relative,
                immutable_inputs[relative],
            )

        if _hash_path(root / "obsidian_vault/MKA") != MANAGED_VAULT_HASH:
            raise AssertionError("copied Managed Vault hash mismatch")
        if _hash_path(
            root / "data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4"
        ) != STORE_SYNC_EXECUTION_TREE_HASH:
            raise AssertionError("copied Store Sync Execution tree hash mismatch")
        if _sha256(root / ".mka/content_index.sqlite") != FORMAL_SQLITE_SHA256:
            raise AssertionError("copied Formal SQLite hash mismatch")
        if _sha256(
            root / "data/governance/governance_decisions.sqlite"
        ) != DECISION_STORE_SHA256:
            raise AssertionError("copied Decision Store hash mismatch")
        if (root / ".mka/search_alias_projection.json").exists():
            raise AssertionError("historical Alias target must be absent")
        if any(path.is_symlink() for path in root.rglob("*")):
            raise AssertionError("historical fixture must not contain live symlinks")
        return root
    except BaseException:
        cleanup_fixture_path(root)
        raise


def _validate_live_historical_inputs(root):
    alias_backup = (
        root
        / "data/governance/backups/production-search-alias-plan-v2-668c2856f39124ae"
    )
    _validate_bundle(
        alias_backup,
        "backup_manifest.json",
        "root_backup_hash",
        ALIAS_BACKUP_ROOT_HASH,
    )
    before = json.loads(
        (alias_backup / "formal_system_before_checksums.json").read_text(
            encoding="utf-8"
        )
    )
    expected_before = {
        "decision_store_sha256": DECISION_STORE_SHA256,
        "formal_sqlite_sha256": FORMAL_SQLITE_SHA256,
        "managed_vault_hash": MANAGED_VAULT_HASH,
        "store_sync_execution_hash": STORE_SYNC_EXECUTION_TREE_HASH,
    }
    for key, expected in expected_before.items():
        if before.get(key) != expected:
            raise AssertionError(f"immutable Alias backup {key} mismatch")

    _validate_bundle(
        root / "data/governance/imports/parent-authority-approval-20260719",
        "bundle_manifest.json",
        "root_manifest_hash",
        PARENT_AUTHORITY_ROOT_HASH,
    )
    _validate_bundle(
        root / "data/governance/confirmations/decision-store-schema-v2-plan-2aab43cd463170f2",
        "confirmation_manifest.json",
        "root_confirmation_hash",
        SCHEMA_V2_CONFIRMATION_ROOT_HASH,
    )
    _validate_bundle(
        root / "data/governance/executions/decision-store-schema-v2-plan-2aab43cd463170f2",
        "execution_manifest.json",
        "root_execution_hash",
        SCHEMA_V2_EXECUTION_ROOT_HASH,
    )

    if _sha256(root / "data/governance/governance_decisions.sqlite") != DECISION_STORE_SHA256:
        raise AssertionError("live Decision Store drifted")
    if _sha256(root / ".mka/content_index.sqlite") != FORMAL_SQLITE_SHA256:
        raise AssertionError("live Formal SQLite drifted")
    if _hash_path(root / "obsidian_vault/MKA") != MANAGED_VAULT_HASH:
        raise AssertionError("live Managed Vault drifted")
    if _hash_path(
        root / "data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4"
    ) != STORE_SYNC_EXECUTION_TREE_HASH:
        raise AssertionError("live Store Sync Execution tree drifted")

    _validate_bundle(
        root / "data/governance/confirmations/store-data-sync-plan-v2-4c8eb2a08b399da4",
        "confirmation_manifest.json",
        "root_confirmation_hash",
        STORE_SYNC_CONFIRMATION_ROOT_HASH,
    )
    _validate_bundle(
        root / "data/governance/executions/store-data-sync-plan-v2-4c8eb2a08b399da4",
        "execution_manifest.json",
        "root_execution_hash",
        STORE_SYNC_EXECUTION_ROOT_HASH,
    )
    _validate_bundle(
        root / "data/governance/backups/store-data-sync-plan-v2-4c8eb2a08b399da4",
        "backup_manifest.json",
        "root_backup_hash",
        STORE_SYNC_BACKUP_ROOT_HASH,
    )
    for relative, expected in PLAN_MANIFEST_HASHES.items():
        _validate_plan_manifest(root / relative, expected)


def _extract_exact_source_commit(formal_root, destination, source_commit):
    archive = subprocess.run(
        ["git", "archive", "--format=tar", source_commit],
        cwd=formal_root,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        tar.extractall(destination)

    _run_git(destination, "init", "--quiet", "--initial-branch", SOURCE_BRANCH)
    _run_git(destination, "add", "--all")
    actual_tree = _run_git(destination, "write-tree")
    expected_tree = _run_git(formal_root, "show", "-s", "--format=%T", source_commit)
    if actual_tree != expected_tree:
        raise AssertionError("archived source tree does not match exact source commit")

    for traceable_commit in (V1_SOURCE_COMMIT, V2_SOURCE_COMMIT):
        _import_commit_object(formal_root, destination, traceable_commit)
    _run_git(destination, "update-ref", f"refs/heads/{SOURCE_BRANCH}", source_commit)
    _run_git(destination, "symbolic-ref", "HEAD", f"refs/heads/{SOURCE_BRANCH}")


def _import_commit_object(formal_root, destination, source_commit):
    commit_bytes = subprocess.run(
        ["git", "cat-file", "commit", source_commit],
        cwd=formal_root,
        check=True,
        capture_output=True,
    ).stdout
    imported = subprocess.run(
        ["git", "hash-object", "-w", "-t", "commit", "--stdin"],
        cwd=destination,
        check=True,
        input=commit_bytes,
        capture_output=True,
        text=False,
    ).stdout.decode().strip()
    if imported != source_commit:
        raise AssertionError("source commit object hash mismatch")


def _validate_bundle(root, manifest_name, hash_field, expected_root):
    manifest = json.loads((root / manifest_name).read_text(encoding="utf-8"))
    stored = manifest.pop(hash_field)
    actual = _hash_json(manifest)
    if stored != expected_root or actual != expected_root:
        raise AssertionError(f"immutable bundle root mismatch: {root}")
    for entry in manifest["files"]:
        relative = (
            entry.get("filename")
            or entry.get("path")
            or entry.get("bundle_relative_path")
        )
        path = root / relative
        if (
            not path.is_file()
            or _sha256(path) != entry["sha256"]
            or path.stat().st_size != entry["byte_size"]
        ):
            raise AssertionError(f"immutable bundle file mismatch: {path}")


def _validate_plan_manifest(path, expected_hash):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    stored = manifest.pop("manifest_hash")
    if stored != expected_hash or _hash_json(manifest) != expected_hash:
        raise AssertionError(f"immutable Plan manifest mismatch: {path}")


def _validate_immutable_input_manifest(root):
    manifest_path = root / HISTORICAL_INPUT_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored_hash = manifest.pop("manifest_hash", "")
    if stored_hash != _hash_json(manifest):
        raise AssertionError("immutable historical input manifest hash mismatch")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise AssertionError("immutable historical input manifest is incomplete")
    if [item.get("relative_path") for item in inputs] != list(HISTORICAL_INPUTS):
        raise AssertionError("immutable historical input manifest path mismatch")

    validated = {}
    for expected in inputs:
        relative = expected["relative_path"]
        actual = _inventory_entry(root / relative, expected)
        if actual != expected:
            raise AssertionError(f"immutable historical input drift: {relative}")
        validated[relative] = expected
    return validated


def _copy_path(source, destination, expected):
    if _inventory_entry(source, expected) != expected:
        raise AssertionError(
            f"immutable historical input drift: {expected.get('relative_path')}"
        )
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=shutil.copy2)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    if _inventory_entry(destination, expected) != expected:
        cleanup_fixture_path(destination)
        raise AssertionError(f"historical input copy mismatch: {source}")


def _inventory_entry(path, authority):
    path = Path(path)
    source = authority.get("immutable_authority_source")
    authority_hash = authority.get("authority_manifest_hash")
    if (
        not source
        or not isinstance(authority_hash, str)
        or len(authority_hash) != 64
        or authority_hash not in IMMUTABLE_AUTHORITY_HASHES
    ):
        raise AssertionError("immutable authority is missing")
    if not path.exists() or path.is_symlink():
        raise AssertionError(
            f"immutable historical input drift: {authority.get('relative_path')}"
        )

    if path.is_file():
        files = [(Path("."), path)]
        input_type = "file"
    elif path.is_dir():
        children = sorted(
            path.rglob("*"),
            key=lambda child: child.relative_to(path).as_posix(),
        )
        if any(child.is_symlink() for child in children):
            raise AssertionError(
                f"immutable historical input drift: {authority.get('relative_path')}"
            )
        files = [
            (child.relative_to(path), child)
            for child in children
            if child.is_file()
        ]
        input_type = "directory"
    else:
        raise AssertionError(
            f"immutable historical input drift: {authority.get('relative_path')}"
        )

    inventory = [
        {
            "relative_path": relative.as_posix(),
            "size": child.stat().st_size,
            "sha256": _sha256(child),
            "appledouble": child.name.startswith("._"),
        }
        for relative, child in files
    ]
    expected_sha256 = (
        inventory[0]["sha256"]
        if input_type == "file"
        else _hash_json(inventory)
    )
    return {
        "relative_path": authority["relative_path"],
        "input_type": input_type,
        "expected_size": sum(item["size"] for item in inventory),
        "expected_sha256": expected_sha256,
        "file_count": len(inventory),
        "inventory": inventory,
        "immutable_authority_source": source,
        "authority_manifest_hash": authority_hash,
    }


def cleanup_fixture_path(path):
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, onerror=_handle_fixture_cleanup_error)
    _remove_appledouble_companion(path)
    parent = path.parent
    if (
        parent.name == ".test-fixtures"
        and parent.exists()
        and not any(parent.iterdir())
    ):
        parent.rmdir()
        _remove_appledouble_companion(parent)


def _handle_fixture_cleanup_error(function, path, error):
    exception = error[1]
    if isinstance(exception, FileNotFoundError) and Path(path).name.startswith("._"):
        return
    raise exception


def _remove_appledouble_companion(path):
    path = Path(path)
    companion = path.with_name(f"._{path.name}")
    if companion.is_symlink() or companion.is_file():
        companion.unlink(missing_ok=True)


@contextmanager
def managed_fixture_workspace(path):
    try:
        yield Path(path)
    finally:
        cleanup_fixture_path(path)


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for child in sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and not item.name.startswith("._")
    ):
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hash_json(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _run_git(root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
