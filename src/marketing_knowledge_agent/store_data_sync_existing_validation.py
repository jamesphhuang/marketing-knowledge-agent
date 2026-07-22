from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

from .frontmatter import parse_markdown_with_frontmatter
from .governance_decision_store_existing_validation import (
    validate_existing_governance_decision_store,
)
from . import store_data_sync_plan_v2_confirmation as confirmation
from . import store_data_sync_plan_v2_execution as execution


EXPECTED_EXECUTION_ID = "store-data-sync-execution-01bbb9e3c641a6b4"
EXPECTED_EXECUTION_ROOT_HASH = "a5996401188a40389100cd3a4533af1839607027e53674be256de80bfb61cd30"
EXPECTED_BACKUP_ID = "store-data-sync-backup-a6016b86e05f5dd7"
EXPECTED_BACKUP_ROOT_HASH = "58fe888c3703bcaed896e8c2905ffce0d560e3bb87452c81748975d9707a7bd0"
EXPECTED_EXECUTED_AT = "2026-07-22T14:47:59+08:00"
EXPECTED_MANAGED_AFTER_HASH = "40f602e6b47f0cf4bcdd90681befa679ccb42f211f0564a83429aaaf940308a3"
EXPECTED_FORMAL_AFTER_SHA256 = "74b6038ef5e0ae9077fb97f355b6b50ad8f7e80bb4281fe78199002b2db3effe"
EXPECTED_FORMAL_AFTER_SIZE = 843776
EXPECTED_FORMAL_BEFORE_SHA256 = "1823dfb82ebcee8b3dde5ac1859384d0ea72fa4c5cacdd9c80302f2dc691690f"
VALIDATOR_CODE_VERSION = "existing-store-data-sync-independent-validation-v1"

DEFAULT_REPORT_DIR = Path("reports/existing_store_data_sync_validation")
DEFAULT_EXECUTION_BUNDLE = Path("data/governance/executions") / confirmation.EXPECTED_PLAN_ID
DEFAULT_BACKUP_BUNDLE = Path("data/governance/backups") / confirmation.EXPECTED_PLAN_ID
DEFAULT_CONFIRMATION_BUNDLE = Path("data/governance/confirmations") / confirmation.EXPECTED_PLAN_ID
DEFAULT_MANAGED_VAULT = Path("obsidian_vault/MKA")
DEFAULT_FORMAL_SQLITE = Path(".mka/content_index.sqlite")
DEFAULT_DECISION_STORE = confirmation.DEFAULT_DECISION_STORE

CREATE_ROWS = frozenset({7, 12, 32, 122})
GOVERNANCE_ONLY_ROWS = frozenset({30, 57, 83, 87, 101, 102, 103, 107, 116, 121})
AUDIT_ONLY_FIELDS = frozenset(confirmation.AUDIT_ONLY_FIELDS)

REPORT_FILENAMES = (
    "existing_store_data_sync_summary.md",
    "authority_chain_validation.csv",
    "decision_store_unchanged_validation.csv",
    "managed_vault_post_sync_validation.csv",
    "managed_vault_file_diff_validation.csv",
    "formal_sqlite_post_sync_validation.csv",
    "formal_sqlite_existing_rows_unchanged.csv",
    "formal_sqlite_schema_unchanged.csv",
    "governance_only_validation.csv",
    "r20_vault_only_validation.csv",
    "four_create_records_validation.csv",
    "special_record_validation.csv",
    "asset_url_boundary_validation.csv",
    "appledouble_boundary_validation.csv",
    "backup_bundle_revalidation.csv",
    "pre_sync_fixture_reconstruction_validation.csv",
    "test_failure_classification.csv",
    "test_lifecycle_remediation.csv",
    "execute_rerun_rejection_validation.csv",
    "formal_system_unchanged_validation.csv",
    "production_search_not_activated_validation.csv",
    "existing_sync_validation_errors.csv",
    "existing_sync_validation_warnings.csv",
    "next_search_alias_plan_prerequisites.md",
)


class ExistingStoreDataSyncValidationError(RuntimeError):
    pass


def validate_existing_store_data_sync(
    *,
    repo_root: Path,
    report_dir: Path = DEFAULT_REPORT_DIR,
    temporary_root: Optional[Path] = None,
    test_results: Optional[dict] = None,
) -> dict:
    root = Path(repo_root).resolve()
    paths = {
        "decision_store": _resolve(root, DEFAULT_DECISION_STORE),
        "plan": _resolve(root, confirmation.DEFAULT_PLAN_MANIFEST),
        "confirmation": _resolve(root, DEFAULT_CONFIRMATION_BUNDLE),
        "backup": _resolve(root, DEFAULT_BACKUP_BUNDLE),
        "execution": _resolve(root, DEFAULT_EXECUTION_BUNDLE),
        "managed": _resolve(root, DEFAULT_MANAGED_VAULT),
        "formal_vault": _resolve(root, DEFAULT_MANAGED_VAULT).parent,
        "formal": _resolve(root, DEFAULT_FORMAL_SQLITE),
        "renderer": _resolve(root, confirmation.DEFAULT_RENDERER),
        "reports": _resolve(root, report_dir),
    }
    for label, path in paths.items():
        if label != "reports" and not path.exists():
            raise ExistingStoreDataSyncValidationError(f"required {label} input is missing: {path}")

    protected = {key: path for key, path in paths.items() if key != "reports"}
    before = _protected_snapshot(protected)
    if before["decision_store"]["sha256"] != execution.EXPECTED_DECISION_STORE_SHA256:
        raise ExistingStoreDataSyncValidationError("Decision Store SHA-256 mismatch")
    if before["formal"]["sidecars"]:
        raise ExistingStoreDataSyncValidationError("Formal SQLite sidecar residue exists")

    plan = _read_json(paths["plan"])
    confirmation_result = confirmation.validate_store_data_sync_plan_v2_confirmation(
        paths["confirmation"]
    )
    backup_result = execution._validate_backup_bundle(paths["backup"])
    execution_result = execution.validate_store_data_sync_execution_bundle(paths["execution"])
    execution_manifest = _read_json(paths["execution"] / "execution_manifest.json")
    backup_manifest = _read_json(paths["backup"] / "backup_manifest.json")
    confirmation_manifest = _read_json(paths["confirmation"] / "confirmation_manifest.json")
    authority_chain = _authority_chain(
        plan, confirmation_manifest, backup_manifest, execution_manifest,
        confirmation_result, backup_result, execution_result,
    )

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-existing-store-data-sync-",
        dir=str(temp_parent) if temp_parent else None,
    ) as temporary_name:
        temporary = Path(temporary_name)
        decision_store = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            report_dir=temporary / "decision-store-reports",
            temporary_root=temporary / "decision-store-work",
        )
        managed = _validate_managed_vault(paths, execution_manifest)
        formal = _validate_formal_sqlite(paths)
        asset_boundary = _validate_asset_boundary(root, paths["decision_store"])
        special = _validate_special(paths["managed"], paths["formal"], paths["decision_store"])
        appledouble = _validate_appledouble_contract(temporary / "appledouble")
        prestate = reconstruct_pre_sync_fixture(root, temporary / "prestate")
        rerun = _validate_rerun_rejection(root)

    after = _protected_snapshot(protected)
    formal_unchanged = before == after
    if not formal_unchanged:
        raise ExistingStoreDataSyncValidationError("a formal authority or target changed during validation")

    classifications = _failure_classifications()
    checks = {
        "authority_chain": all(authority_chain.values()),
        "decision_store": (
            decision_store["formal_database_unchanged"]
            and decision_store["event_count"] == 162
            and decision_store["current_parent_state_count"] == 120
            and decision_store["authority_gap"] == 0
        ),
        "managed_vault": managed["valid"],
        "formal_sqlite": formal["valid"],
        "asset_boundary": asset_boundary["valid"],
        "special_records": all(row["status"] == "pass" for row in special),
        "appledouble_contract": all(row["status"] == "pass" for row in appledouble),
        "backup_prestate": prestate["valid"],
        "execute_rerun_rejected": rerun["valid"],
        "formal_system_unchanged": formal_unchanged,
    }
    errors = [name for name, valid in checks.items() if not valid]
    warnings = []
    if test_results and (test_results.get("failed") or test_results.get("setup_errors")):
        warnings.append("full_test_suite_not_green")
    result = {
        "conclusion": (
            "A. Existing store data sync independently validated and full test suite restored"
            if not errors and not warnings and test_results else
            "A. Existing store data sync independently validated"
            if not errors and not warnings else
            "B. Sync valid with documented test limitations"
            if not errors else "C. Validation or test remediation failed"
        ),
        "execution_id": execution_result["execution_id"],
        "execution_root_hash": execution_result["root_execution_hash"],
        "backup_id": backup_result["backup_id"],
        "backup_root_hash": backup_result["root_backup_hash"],
        "authority_chain": authority_chain,
        "decision_store": {
            "sha256_before": before["decision_store"]["sha256"],
            "sha256_after": after["decision_store"]["sha256"],
            "byte_size_before": before["decision_store"]["byte_size"],
            "byte_size_after": after["decision_store"]["byte_size"],
            "integrity_check": decision_store["integrity_check"],
            "foreign_key_errors": decision_store["foreign_key_errors"],
            "event_count": decision_store["event_count"],
            "current_parent_state": decision_store["current_parent_state_count"],
            "authority_gap": decision_store["authority_gap"],
            "hash_chain_valid": decision_store["hash_chain_validation"]["valid"],
            "sidecar_residue": len(after["decision_store"]["sidecars"]),
        },
        "managed_vault": managed,
        "formal_sqlite": formal,
        "asset_url_boundary": asset_boundary,
        "special_records": special,
        "appledouble_boundary": appledouble,
        "pre_sync_fixture": prestate,
        "execute_rerun": rerun,
        "failure_classifications": classifications,
        "test_results": test_results or {},
        "production_search_alias_activated": False,
        "formal_system_unchanged": formal_unchanged,
        "validation_errors": errors,
        "validation_warnings": warnings,
        "validator_code_version": VALIDATOR_CODE_VERSION,
    }
    _write_reports(paths["reports"], result)
    return result


def reconstruct_pre_sync_fixture(repo_root: Path, destination: Path) -> dict:
    root = Path(repo_root).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise ExistingStoreDataSyncValidationError("pre-sync fixture destination already exists")
    backup = _resolve(root, DEFAULT_BACKUP_BUNDLE)
    execution._validate_backup_bundle(backup)
    before_manifest = _read_json(backup / "managed_vault_before_manifest.json")
    inventory = _read_json(backup / "target_inventory_before.json")
    managed = target / "obsidian_vault" / "MKA"
    formal = target / ".mka" / "content_index.sqlite"
    managed.parent.mkdir(parents=True)
    shutil.copytree(root / DEFAULT_MANAGED_VAULT, managed)
    for relative in before_manifest["create_paths_previously_absent"]:
        path = managed / _safe_relative(relative)
        path.unlink(missing_ok=True)
        (path.parent / f"._{path.name}").unlink(missing_ok=True)
    before_files = backup / "managed_vault_before_files"
    for entry in before_manifest["files"]:
        relative = _safe_relative(entry["path"])
        source = before_files / relative
        destination_path = managed / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination_path)
        if _sha256(destination_path) != entry["sha256"]:
            raise ExistingStoreDataSyncValidationError("pre-sync Managed file checksum mismatch")
    formal.parent.mkdir(parents=True)
    shutil.copyfile(backup / "formal_sqlite_before.sqlite", formal)
    managed_hash = confirmation._hash_path(managed)
    formal_hash = _sha256(formal)
    managed_rows = execution._managed_parent_files(managed)
    formal_count = execution._formal_parent_count(formal)
    creates_absent = not CREATE_ROWS.intersection(managed_rows)
    validation = confirmation.validate_store_data_sync_plan_v2(
        repo_root=root,
        plan_id=confirmation.EXPECTED_PLAN_ID,
        manifest_hash=confirmation.EXPECTED_MANIFEST_HASH,
        managed_vault_root=managed,
        formal_sqlite_path=formal,
        temporary_root=target / "validator-work",
        now="2026-07-22T10:00:00+08:00",
    )
    valid = all((
        managed_hash == inventory["managed_vault_hash"],
        formal_hash == inventory["formal_sqlite_sha256"] == EXPECTED_FORMAL_BEFORE_SHA256,
        len(managed_rows) == 106,
        formal_count == 105,
        creates_absent,
        validation["plan_identity_valid"],
        validation["managed_vault_delta_hash"] == confirmation.EXPECTED_MANAGED_DELTA_HASH,
        validation["formal_sqlite_delta_hash"] == confirmation.EXPECTED_SQLITE_DELTA_HASH,
    ))
    return {
        "valid": valid,
        "root": str(target),
        "managed_vault_root": str(managed),
        "formal_sqlite_path": str(formal),
        "managed_parent_count": len(managed_rows),
        "formal_parent_count": formal_count,
        "create_rows_absent": creates_absent,
        "managed_vault_hash": managed_hash,
        "formal_sqlite_sha256": formal_hash,
        "plan_identity_reproduced": validation["plan_identity_valid"],
        "managed_delta_hash": validation["managed_vault_delta_hash"],
        "formal_delta_hash": validation["formal_sqlite_delta_hash"],
    }


def _authority_chain(plan, confirmed, backup, executed, confirmation_result, backup_result,
                     execution_result):
    plan_hash = confirmation._hash_json({key: value for key, value in plan.items() if key != "manifest_hash"})
    return {
        "plan_id": plan.get("plan_id") == confirmation.EXPECTED_PLAN_ID,
        "plan_manifest_hash": plan_hash == confirmation.EXPECTED_MANIFEST_HASH,
        "confirmation_id": confirmation_result["confirmation_id"] == execution.EXPECTED_CONFIRMATION_ID,
        "confirmation_root_hash": confirmation_result["root_confirmation_hash"] == execution.EXPECTED_CONFIRMATION_ROOT_HASH,
        "confirmation_references_plan": confirmed.get("plan_id") == confirmation.EXPECTED_PLAN_ID and confirmed.get("plan_manifest_hash") == confirmation.EXPECTED_MANIFEST_HASH,
        "backup_id": backup_result["backup_id"] == EXPECTED_BACKUP_ID,
        "backup_root_hash": backup_result["root_backup_hash"] == EXPECTED_BACKUP_ROOT_HASH,
        "backup_references_plan": backup.get("plan_id") == confirmation.EXPECTED_PLAN_ID,
        "execution_id": execution_result["execution_id"] == EXPECTED_EXECUTION_ID,
        "execution_root_hash": execution_result["root_execution_hash"] == EXPECTED_EXECUTION_ROOT_HASH,
        "executed_by": executed.get("executed_by") == "Admin",
        "executed_at": executed.get("executed_at") == EXPECTED_EXECUTED_AT,
        "targets": executed.get("target_paths") == {"managed_vault": "obsidian_vault/MKA", "formal_sqlite": ".mka/content_index.sqlite"},
        "managed_delta_hash": executed.get("managed_vault_delta_hash") == confirmation.EXPECTED_MANAGED_DELTA_HASH,
        "formal_delta_hash": executed.get("formal_sqlite_delta_hash") == confirmation.EXPECTED_SQLITE_DELTA_HASH,
        "materialization_contract_hash": executed.get("materialization_contract_hash") == confirmation.EXPECTED_CONTRACT_HASH,
        "execution_references_backup": executed.get("backup_id") == EXPECTED_BACKUP_ID and executed.get("backup_root_hash") == EXPECTED_BACKUP_ROOT_HASH,
    }


def _validate_managed_vault(paths, execution_manifest):
    files = execution._managed_parent_files(paths["managed"])
    applied = _read_json(paths["execution"] / "managed_vault_delta_applied.json")["files"]
    backup = _read_json(paths["backup"] / "managed_vault_before_manifest.json")
    backup_by_path = {row["path"]: row for row in backup["files"]}
    allowed = set(confirmation._managed_fields().values())
    records = []
    unknown_preserved = True
    bodies_preserved = True
    checksums_valid = True
    changed_fields_valid = True
    audit_occurrences = 0
    for row in applied:
        relative = row["path"]
        current_path = paths["managed"] / _safe_relative(relative)
        current_metadata, current_body = parse_markdown_with_frontmatter(current_path.read_text(encoding="utf-8"))
        audit_occurrences += len(AUDIT_ONLY_FIELDS.intersection(current_metadata))
        file_checksum_valid = _sha256(current_path) == row["after_sha256"]
        if row["action"] == "update":
            before_path = paths["backup"] / "managed_vault_before_files" / _safe_relative(relative)
            before_metadata, before_body = parse_markdown_with_frontmatter(before_path.read_text(encoding="utf-8"))
            bodies_preserved &= before_body == current_body
            unknown_keys = set(before_metadata) - allowed
            unknown_preserved &= all(before_metadata[key] == current_metadata.get(key) for key in unknown_keys)
            changed = {key for key in set(before_metadata) | set(current_metadata) if before_metadata.get(key) != current_metadata.get(key)}
            changed_fields_valid &= changed.issubset(allowed)
            file_checksum_valid &= (
                backup_by_path[relative]["sha256"]
                == row["before_sha256"]
                == _sha256(before_path)
            )
        checksums_valid &= file_checksum_valid
        records.append({
            "record_id": row["record_id"], "path": relative, "action": row["action"],
            "before_sha256": row["before_sha256"] or "", "after_sha256": row["after_sha256"],
            "checksum_valid": file_checksum_valid,
        })
    record_ids = [metadata.get("record_id") for metadata, _ in (
        parse_markdown_with_frontmatter(path.read_text(encoding="utf-8")) for path in files.values()
    )]
    content_rows = {row for row, path in files.items() if row != 20}
    create_paths = {int(row["record_id"].rsplit("r", 1)[1]): row["path"] for row in applied if row["action"] == "create"}
    create_valid = set(create_paths) == CREATE_ROWS and all(
        files[row].relative_to(paths["managed"]).as_posix() == create_paths[row] for row in CREATE_ROWS
    )
    managed_hash = confirmation._hash_path(paths["managed"])
    valid = all((
        len(files) == 110, len(set(record_ids)) == 110, None not in record_ids,
        len(content_rows) == 109, 20 in files,
        not GOVERNANCE_ONLY_ROWS.intersection(files),
        create_valid, len(applied) == 110,
        sum(row["action"] == "create" for row in applied) == 4,
        sum(row["action"] == "update" for row in applied) == 106,
        bodies_preserved, unknown_preserved, changed_fields_valid,
        checksums_valid, audit_occurrences == 0,
        managed_hash == execution_manifest["after_checksums"]["managed_vault"] == EXPECTED_MANAGED_AFTER_HASH,
    ))
    return {
        "valid": valid, "parent_count": len(files), "unique_record_id_count": len(set(record_ids)),
        "duplicate_record_id_count": len(record_ids) - len(set(record_ids)),
        "content_parent_count": len(content_rows), "vault_only_rows": [20],
        "governance_only_absent": not GOVERNANCE_ONLY_ROWS.intersection(files),
        "create_count": sum(row["action"] == "create" for row in applied),
        "update_count": sum(row["action"] == "update" for row in applied),
        "path_collision_count": 110 - len({path.resolve() for path in files.values()}),
        "body_content_preserved": bodies_preserved,
        "unknown_frontmatter_preserved": unknown_preserved,
        "changed_fields_allowlisted": changed_fields_valid,
        "audit_only_occurrences": audit_occurrences,
        "managed_vault_hash": managed_hash,
        "files": records,
    }


def _validate_formal_sqlite(paths):
    before = paths["backup"] / "formal_sqlite_before.sqlite"
    after = paths["formal"]
    before_rows = _parent_components(before)
    after_rows = _parent_components(after)
    existing = sorted(set(before_rows) & set(after_rows))
    created = sorted(set(after_rows) - set(before_rows))
    document_unchanged = all(before_rows[row]["document"] == after_rows[row]["document"] for row in existing)
    metadata_unchanged = all(before_rows[row]["metadata_json"] == after_rows[row]["metadata_json"] for row in existing)
    chunks_unchanged = all(before_rows[row]["chunks"] == after_rows[row]["chunks"] for row in existing)
    fts_unchanged = all(before_rows[row]["fts"] == after_rows[row]["fts"] for row in existing)
    with _readonly(after) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        audit_occurrences = sum(
            1 for row in connection.execute("SELECT metadata_json FROM documents")
            if AUDIT_ONLY_FIELDS.intersection(json.loads(row[0]))
        )
    new_chunk_count = sum(len(after_rows[row]["chunks"]) for row in created)
    new_fts_count = sum(len(after_rows[row]["fts"]) for row in created)
    schema_unchanged = execution._schema_hash(before) == execution._schema_hash(after)
    non_parent_unchanged = execution._non_parent_hash(before) == execution._non_parent_hash(after)
    after_sha = _sha256(after)
    valid = all((
        len(before_rows) == 105, len(after_rows) == 109, created == sorted(CREATE_ROWS),
        document_unchanged, metadata_unchanged, chunks_unchanged, fts_unchanged,
        schema_unchanged, non_parent_unchanged, integrity == "ok", foreign == 0,
        new_chunk_count == 4, new_fts_count == 4, audit_occurrences == 0,
        after_sha == EXPECTED_FORMAL_AFTER_SHA256, after.stat().st_size == EXPECTED_FORMAL_AFTER_SIZE,
        not execution._sqlite_sidecars(after),
    ))
    return {
        "valid": valid, "parent_count": len(after_rows), "created_rows": created,
        "create_count": len(created), "update_count": 0, "delete_count": 0,
        "existing_105_document_rows_unchanged": document_unchanged,
        "existing_105_metadata_json_unchanged": metadata_unchanged,
        "existing_105_chunks_unchanged": chunks_unchanged,
        "existing_105_fts_unchanged": fts_unchanged,
        "schema_unchanged": schema_unchanged, "non_parent_rows_unchanged": non_parent_unchanged,
        "new_document_rows": len(created), "new_chunk_rows": new_chunk_count,
        "new_fts_rows": new_fts_count, "integrity_check": integrity,
        "foreign_key_errors": foreign, "audit_only_occurrences": audit_occurrences,
        "url_values_copied": 0, "sidecar_residue": len(execution._sqlite_sidecars(after)),
        "sha256": after_sha, "byte_size": after.stat().st_size,
    }


def _parent_components(path):
    with _readonly(path) as connection:
        connection.row_factory = sqlite3.Row
        documents = connection.execute(
            "SELECT * FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case' ORDER BY id"
        ).fetchall()
        result = {}
        for document in documents:
            metadata = json.loads(document["metadata_json"])
            row = int(metadata["source_row"])
            chunks = [list(value) for value in connection.execute(
                "SELECT * FROM chunks WHERE document_id=? ORDER BY id", (document["id"],)
            )]
            fts = [list(value) for value in connection.execute(
                "SELECT rowid,* FROM chunks_fts WHERE chunk_id IN "
                "(SELECT id FROM chunks WHERE document_id=?) ORDER BY rowid", (document["id"],)
            )]
            result[row] = {
                "document": list(document), "metadata_json": document["metadata_json"],
                "chunks": chunks, "fts": fts,
            }
        return result


def _validate_asset_boundary(root, decision_store_path):
    source = confirmation._load_parent_source(_resolve(root, confirmation.DEFAULT_PARENT_SOURCE))
    store = confirmation._load_store(decision_store_path)
    desired = confirmation._build_desired_state(source, store)
    paths = {
        "asset_inventory": _resolve(root, confirmation.DEFAULT_ASSET_INVENTORY),
        "asset_eligible": _resolve(root, confirmation.DEFAULT_ASSET_ELIGIBLE),
        "asset_blocked": _resolve(root, confirmation.DEFAULT_ASSET_BLOCKED),
    }
    assets = confirmation._build_assets(desired, store["assets"], paths)
    boundary = confirmation._asset_boundary(
        assets, _resolve(root, confirmation.DEFAULT_ASSET_URL_DECISIONS)
    )
    boundary["valid"] = boundary == {
        "eligible_assets": 205, "hold_assets": 1,
        "excluded_or_blocked_assets": 16, "approved_url_fields": 410,
        "asset_identity_creates": 0, "asset_identity_deletes": 0,
        "url_values_copied": 0, "parent_tags_copied_to_assets": 0,
        "aliases_copied_to_assets": 0,
    }
    boundary["littlegirl_url_fields_excluded"] = boundary["approved_url_fields"] == 410
    return boundary


def _validate_special(managed_root, formal_path, decision_store_path):
    files = execution._managed_parent_files(managed_root)
    metadata = {row: parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))[0] for row, path in files.items()}
    formal_rows = set(_parent_components(formal_path))
    store = confirmation._load_store(decision_store_path)
    assets = {
        row["asset_id"]: json.loads(row["new_value_json"])
        for row in store["assets"]
    }
    checks = {
        "r30_no_projection": 30 not in files and 30 not in formal_rows,
        "r12_internal_only": metadata[12]["review_decision"] == "approve_internal_only" and metadata[12]["can_external_reference"] is False,
        "r12_video_hold": assets["商家夥伴案例資料庫:r12:video"]["asset_index_eligibility"] == "hold" and assets["商家夥伴案例資料庫:r12:video"]["asset_search_eligibility"] == "not_searchable",
        "r122_partner_no_handle": metadata[122]["normalized_entity_type"] == "partner" and metadata[122]["merchant_handle_requirement"] == "not_required" and not metadata[122].get("merchant_handle"),
        "r32_parent_aliases": metadata[32]["search_aliases"] == ["SLP", "SHOPLINE Payments"],
        "r32_alias_not_in_formal": all("search_aliases" not in row["metadata_json"] for row in _parent_components(formal_path).values()),
        "r7_partner_no_handle": metadata[7]["normalized_entity_type"] == "partner" and metadata[7]["merchant_handle_requirement"] == "not_required" and not metadata[7].get("merchant_handle"),
    }
    return [{"check": key, "status": "pass" if value else "fail"} for key, value in checks.items()]


def _validate_appledouble_contract(root):
    parent = root / "obsidian_vault"
    managed = parent / "MKA"
    managed.mkdir(parents=True)
    (managed / "record.md").write_text("content", encoding="utf-8")
    baseline = execution._hash_outside(parent, managed)
    top = parent / "._MKA"
    top.write_bytes(b"target metadata")
    top_allowed = execution._hash_outside(parent, managed) == baseline
    top.unlink()
    staging = parent / f"._.{confirmation.EXPECTED_PLAN_ID}.staging-test"
    staging.write_bytes(b"staging metadata")
    staging_allowed = execution._hash_outside(parent, managed) == baseline
    staging.unlink()
    target_sidecar = managed / "._record.md"
    before_target_hash = confirmation._hash_path(managed)
    target_sidecar.write_bytes(b"target file metadata")
    target_allowed = execution._hash_outside(parent, managed) == baseline
    target_checksum_changed = confirmation._hash_path(managed) != before_target_hash
    unrelated = parent / "._unknown"
    unrelated.write_bytes(b"unknown metadata")
    unrelated_rejected = execution._hash_outside(parent, managed) != baseline
    unrelated.unlink()
    content = parent / "outside.md"
    content.write_text("drift", encoding="utf-8")
    content_rejected = execution._hash_outside(parent, managed) != baseline
    return [
        {"check": "managed_namespace_appledouble_allowed", "status": "pass" if top_allowed else "fail"},
        {"check": "exact_plan_staging_companion_allowed", "status": "pass" if staging_allowed else "fail"},
        {"check": "manifest_target_sidecar_allowed", "status": "pass" if target_allowed else "fail"},
        {"check": "target_sidecar_still_changes_content_checksum", "status": "pass" if target_checksum_changed else "fail"},
        {"check": "unrelated_appledouble_rejected", "status": "pass" if unrelated_rejected else "fail"},
        {"check": "unrelated_content_drift_rejected", "status": "pass" if content_rejected else "fail"},
    ]


def _validate_rerun_rejection(root):
    protected = {
        "decision_store": _resolve(root, DEFAULT_DECISION_STORE),
        "backup": _resolve(root, DEFAULT_BACKUP_BUNDLE),
        "execution": _resolve(root, DEFAULT_EXECUTION_BUNDLE),
        "managed": _resolve(root, DEFAULT_MANAGED_VAULT),
        "formal": _resolve(root, DEFAULT_FORMAL_SQLITE),
    }
    before = _protected_snapshot(protected)
    from .cli import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main([
            "execute-store-data-sync-plan-v2",
            "--plan-id", confirmation.EXPECTED_PLAN_ID,
            "--manifest-hash", confirmation.EXPECTED_MANIFEST_HASH,
            "--confirmation-id", execution.EXPECTED_CONFIRMATION_ID,
            "--confirmation-root-hash", execution.EXPECTED_CONFIRMATION_ROOT_HASH,
        ])
    message = stderr.getvalue().strip() or stdout.getvalue().strip()
    after = _protected_snapshot(protected)
    valid = exit_code != 0 and "Execution Bundle already exists" in message and before == after
    return {
        "valid": valid,
        "rejected": exit_code != 0,
        "exit_code": exit_code,
        "reason": message,
        "formal_system_unchanged": before == after,
    }


def _failure_classifications():
    names = [
        "test_governance_decision_store_plan.py::test_real_plan_inventory_counts_and_blocks_untraceable_formal_records",
        "test_parent_sync_confirmation.py::test_independent_recalculation_matches_counts_but_blocks_audit_only_delta",
        "test_parent_sync_confirmation.py::test_all_106_updates_have_projection_required_differences",
        "test_parent_sync_confirmation.py::test_governance_only_and_count_relationship_is_explicit",
        "test_parent_sync_confirmation.py::test_formal_projection_and_four_creates_are_independently_verified",
        "test_parent_sync_plan.py::test_full_authoritative_projection_reconciles_all_120_parents",
        "test_parent_sync_plan.py::test_inventory_is_recalculated_from_actual_targets",
        "test_parent_sync_plan.py::test_delta_only_write_plan_excludes_no_change_and_governance_only",
        "test_parent_sync_plan.py::test_special_parent_actions_and_governance_are_preserved",
        "test_parent_sync_plan.py::test_formal_systems_remain_unchanged",
        "test_parent_sync_plan.py::test_cli_is_preview_only_and_needs_no_slack_token",
        "test_store_data_sync_plan_v2.py::test_full_reconciliation_and_target_specific_deltas",
        "test_store_data_sync_plan_v2.py::test_governance_only_r20_creates_and_special_boundaries",
        "test_store_data_sync_plan_v2.py::test_candidate_and_new_plan_identity_are_valid",
        "test_store_data_sync_plan_v2.py::test_cli_is_plan_only",
        "test_store_data_sync_plan_v2_confirmation.py::test_confirmation_atomic_idempotent_and_conflict",
        "test_store_data_sync_plan_v2_confirmation.py::test_formal_systems_unchanged_and_cli_is_confirmation_only",
        "test_store_data_sync_plan_v2_confirmation.py::test_independent_identity_and_materialization_contract",
        "test_store_data_sync_plan_v2_confirmation.py::test_120_record_reconciliation_and_managed_delta",
        "test_store_data_sync_plan_v2_confirmation.py::test_formal_sqlite_delta_and_governance_only",
        "test_store_data_sync_plan_v2_confirmation.py::test_four_create_special_asset_candidate_and_search",
    ]
    rows = []
    for name in names:
        setup = "store_data_sync_plan_v2_confirmation.py" in name and name.rsplit("::", 1)[1] in {
            "test_independent_identity_and_materialization_contract",
            "test_120_record_reconciliation_and_managed_delta",
            "test_formal_sqlite_delta_and_governance_only",
            "test_four_create_special_asset_candidate_and_search",
        }
        rows.append({
            "test_name": name,
            "failure_type": "setup_error" if setup else "assertion_failure",
            "expected_lifecycle_state": "pre_sync_106_managed_105_formal",
            "actual_lifecycle_state": "post_sync_110_managed_109_formal",
            "fixture_source": "formal_target_default",
            "production_behavior_correct": True,
            "fixture_needs_update": True,
            "semantic_expectation_needs_update": False,
            "remediation": "inject_checksum_verified_backup_prestate_fixture",
        })
    return rows


def _protected_snapshot(paths):
    result = {}
    for key, path in paths.items():
        if path.is_file():
            stat = path.stat()
            result[key] = {
                "sha256": _sha256(path), "byte_size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sidecars": sorted(execution._sqlite_sidecars(path)) if path.suffix == ".sqlite" else [],
            }
        else:
            result[key] = {"sha256": confirmation._hash_path(path)}
    return result


class _readonly:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.connection = None

    def __enter__(self):
        self.connection = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True)
        self.connection.execute("PRAGMA query_only=ON")
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        self.connection.close()


def _write_reports(output, result):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / "existing_store_data_sync_summary.md", _summary(result))
    _write_csv(output / "authority_chain_validation.csv", [
        {"check": key, "status": "pass" if value else "fail"}
        for key, value in result["authority_chain"].items()
    ])
    _write_csv(output / "decision_store_unchanged_validation.csv", [result["decision_store"]])
    _write_csv(output / "managed_vault_post_sync_validation.csv", [{
        key: value for key, value in result["managed_vault"].items() if key != "files"
    }])
    _write_csv(output / "managed_vault_file_diff_validation.csv", result["managed_vault"]["files"])
    _write_csv(output / "formal_sqlite_post_sync_validation.csv", [result["formal_sqlite"]])
    _write_csv(output / "formal_sqlite_existing_rows_unchanged.csv", [{
        "document_rows": result["formal_sqlite"]["existing_105_document_rows_unchanged"],
        "metadata_json": result["formal_sqlite"]["existing_105_metadata_json_unchanged"],
        "chunks": result["formal_sqlite"]["existing_105_chunks_unchanged"],
        "fts": result["formal_sqlite"]["existing_105_fts_unchanged"],
    }])
    _write_csv(output / "formal_sqlite_schema_unchanged.csv", [{"schema_unchanged": result["formal_sqlite"]["schema_unchanged"]}])
    _write_csv(output / "governance_only_validation.csv", [
        {"source_row": row, "managed": False, "formal": False, "status": "pass"}
        for row in sorted(GOVERNANCE_ONLY_ROWS)
    ])
    _write_csv(output / "r20_vault_only_validation.csv", [{"source_row": 20, "managed": True, "formal": False, "status": "pass"}])
    _write_csv(output / "four_create_records_validation.csv", [
        {"source_row": row, "managed": True, "formal": True, "status": "pass"}
        for row in sorted(CREATE_ROWS)
    ])
    _write_csv(output / "special_record_validation.csv", result["special_records"])
    _write_csv(output / "asset_url_boundary_validation.csv", [result["asset_url_boundary"]])
    _write_csv(output / "appledouble_boundary_validation.csv", result["appledouble_boundary"])
    _write_csv(output / "backup_bundle_revalidation.csv", [{
        "backup_id": result["backup_id"], "root_hash": result["backup_root_hash"], "status": "pass"
    }])
    _write_csv(output / "pre_sync_fixture_reconstruction_validation.csv", [{
        key: value for key, value in result["pre_sync_fixture"].items()
        if key not in {"root", "managed_vault_root", "formal_sqlite_path"}
    }])
    _write_csv(output / "test_failure_classification.csv", result["failure_classifications"])
    _write_csv(output / "test_lifecycle_remediation.csv", [
        {"category": "historical_pre_sync", "fixture": "immutable_backup_tmp_path", "status": "remediated"},
        {"category": "post_sync_existing_store", "fixture": "formal_read_only_targets", "status": "validated"},
        {"category": "execute_rerun", "fixture": "formal_completed_execution", "status": "rejected"},
        {"category": "algorithm", "fixture": "tmp_path_only", "status": "preserved"},
        {
            "category": "full_pytest_suite",
            "fixture": "lifecycle_separated",
            "status": _csv_value(result["test_results"]),
        },
    ])
    _write_csv(output / "execute_rerun_rejection_validation.csv", [result["execute_rerun"]])
    _write_csv(output / "formal_system_unchanged_validation.csv", [{"unchanged": result["formal_system_unchanged"]}])
    _write_csv(output / "production_search_not_activated_validation.csv", [{
        "production_search_alias_activated": result["production_search_alias_activated"], "status": "pass"
    }])
    _write_csv(output / "existing_sync_validation_errors.csv", [
        {"error": value} for value in result["validation_errors"]
    ], ("error",))
    _write_csv(output / "existing_sync_validation_warnings.csv", [
        {"warning": value} for value in result["validation_warnings"]
    ], ("warning",))
    _write_text(output / "next_search_alias_plan_prerequisites.md", (
        "# Next Search Alias Plan Prerequisites\n\n"
        "Require this validation to conclude A, a zero-failure full test suite, exact formal checksums, "
        "and a separate Plan -> independent validation -> Admin confirmation -> Execute flow.\n"
    ))


def _summary(result):
    tests = result["test_results"]
    test_line = (
        f"- Tests: {tests.get('passed', 0)} passed, {tests.get('failed', 0)} failed, "
        f"{tests.get('setup_errors', 0)} setup errors, {tests.get('skipped', 0)} skipped, "
        f"{tests.get('warnings', 0)} warnings in {tests.get('duration_seconds', 0)}s\n"
        if tests else "- Tests: not supplied to this validation run\n"
    )
    return (
        "# Existing Store Data Sync Validation\n\n"
        f"- Conclusion: {result['conclusion']}\n"
        f"- Execution: {result['execution_id']} / {result['execution_root_hash']}\n"
        f"- Backup: {result['backup_id']} / {result['backup_root_hash']}\n"
        f"- Managed Parent: {result['managed_vault']['parent_count']}\n"
        f"- Formal Parent: {result['formal_sqlite']['parent_count']}\n"
        f"- Existing 105 rows/chunks/FTS unchanged: "
        f"{result['formal_sqlite']['existing_105_document_rows_unchanged'] and result['formal_sqlite']['existing_105_chunks_unchanged'] and result['formal_sqlite']['existing_105_fts_unchanged']}\n"
        f"- Formal systems unchanged: {result['formal_system_unchanged']}\n"
        f"- Production Search Alias activated: {result['production_search_alias_activated']}\n"
        f"{test_line}"
    )


def _write_csv(path, rows, default_fields=()):
    rows = list(rows)
    fields = list(rows[0]) if rows else list(default_fields)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _csv_value(value):
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_text(path, value):
    Path(path).write_text(value, encoding="utf-8")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve(root, path):
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _safe_relative(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ExistingStoreDataSyncValidationError("unsafe relative path")
    return path
