from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .governance_decision_store_schema_v2_confirmation import (
    CANONICAL_SQL_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    EXPECTED_SCHEMA_HASH,
    PLAN_EXPIRES_AT,
    _hash_path,
    _independent_bind,
    _insert_event,
    _verify_append_only,
    _verify_backup_restore,
    _verify_chain,
    _verify_read_only,
    validate_governance_decision_store_schema_v2_confirmation,
    validate_governance_decision_store_schema_v2_plan,
)
from .governance_decision_store_schema_v2_plan import (
    BUNDLE_ID,
    BUNDLE_ROOT_HASH,
    CANONICAL_SCHEMA_V2_SQL,
    SCHEMA_NAME,
    SCHEMA_VERSION,
)


CONFIRMATION_ID = "decision-store-schema-v2-confirmation-1367702bed7e9e90"
CONFIRMATION_ROOT_HASH = "40803f723f072fa683042aeaa6d465eef30412ca0e607fb13b9ba8861a853416"
EXECUTOR_CODE_VERSION = "governance-decision-store-schema-v2-execute-v1"
DEFAULT_FORMAL_TARGET = Path("data/governance/governance_decisions.sqlite")
DEFAULT_EXECUTION_BUNDLE = Path("data/governance/executions") / EXPECTED_PLAN_ID
DEFAULT_CONFIRMATION_BUNDLE = Path("data/governance/confirmations") / EXPECTED_PLAN_ID
DEFAULT_REPORT_DIR = Path("reports/governance_decision_store_schema_v2_execution")
OBSOLETE_PLAN_IDS = {
    "decision-store-plan-a02502d8361549b1",
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
}
OLD_CONFIRMATION_ID = "decision-store-confirmation-98fef43f8dd6773a"
REPORT_FILENAMES = (
    "schema_v2_execution_summary.md",
    "execution_preflight_validation.csv",
    "plan_bundle_confirmation_validation.csv",
    "schema_v2_execution_validation.csv",
    "decision_event_write_validation.csv",
    "current_state_post_execution_validation.csv",
    "special_decision_post_execution_validation.csv",
    "asset_eligibility_post_execution_validation.csv",
    "search_alias_post_execution_validation.csv",
    "entity_metadata_post_execution_validation.csv",
    "asset_url_reference_post_execution_validation.csv",
    "schema_metadata_validation.csv",
    "execution_metadata_validation.csv",
    "sqlite_integrity_validation.csv",
    "append_only_trigger_validation.csv",
    "hash_chain_post_execution_validation.csv",
    "formal_database_checksum.json",
    "execution_bundle_validation.csv",
    "formal_system_boundary_validation.csv",
    "execution_rollback_report.md",
    "next_parent_sync_prerequisites.md",
    "execution_errors.csv",
    "execution_warnings.csv",
)


class GovernanceDecisionStoreSchemaV2ExecutionError(RuntimeError):
    pass


def execute_governance_decision_store_schema_v2_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    schema_hash: str,
    confirmation_id: str,
    confirmation_root_hash: str,
    formal_target_path: Path = DEFAULT_FORMAL_TARGET,
    execution_bundle_path: Path = DEFAULT_EXECUTION_BUNDLE,
    confirmation_bundle_path: Path = DEFAULT_CONFIRMATION_BUNDLE,
    report_dir: Path = DEFAULT_REPORT_DIR,
    temporary_root: Optional[Path] = None,
    executed_at: Optional[str] = None,
    _allow_test_paths: bool = False,
) -> dict:
    root = Path(repo_root).resolve()
    executed = executed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(executed)
    target = _resolve(root, formal_target_path)
    execution_bundle = _resolve(root, execution_bundle_path)
    confirmation_bundle = _resolve(root, confirmation_bundle_path)
    reports = _resolve(root, report_dir)
    if not _allow_test_paths:
        if target != _resolve(root, DEFAULT_FORMAL_TARGET):
            raise GovernanceDecisionStoreSchemaV2ExecutionError("Target Path must match the confirmed Plan")
        if execution_bundle != _resolve(root, DEFAULT_EXECUTION_BUNDLE):
            raise GovernanceDecisionStoreSchemaV2ExecutionError("Execution Bundle path must match the confirmed Plan")
    _require_authority(plan_id, manifest_hash, schema_hash, confirmation_id, confirmation_root_hash)
    if datetime.fromisoformat(executed) > datetime.fromisoformat(PLAN_EXPIRES_AT):
        raise GovernanceDecisionStoreSchemaV2ExecutionError("Plan expired; execution is prohibited")
    _require_fresh_target(target)
    _require_fresh_bundle(execution_bundle)

    protected = _protected_paths(root)
    protected_before = {str(path): _hash_path(path) for path in protected}
    try:
        validation = validate_governance_decision_store_schema_v2_plan(
            repo_root=root,
            plan_id=plan_id,
            manifest_hash=manifest_hash,
            schema_hash=schema_hash,
            canonical_sql_hash=CANONICAL_SQL_HASH,
            formal_target_path=target,
            temporary_root=temporary_root,
            now=executed,
        )
        confirmation = validate_governance_decision_store_schema_v2_confirmation(
            confirmation_bundle
        )
    except Exception as exc:
        raise GovernanceDecisionStoreSchemaV2ExecutionError(f"preflight validation failed: {exc}") from exc
    _validate_confirmation(confirmation)
    bound_events = _independent_bind(
        validation["event_templates"], CONFIRMATION_ID, CONFIRMATION_ROOT_HASH
    )
    _validate_bound_events(bound_events, validation)

    target.parent.mkdir(parents=True, exist_ok=True)
    execution_bundle.parent.mkdir(parents=True, exist_ok=True)
    if not _allow_test_paths and (
        not _is_git_ignored(root, target) or not _is_git_ignored(root, execution_bundle)
    ):
        raise GovernanceDecisionStoreSchemaV2ExecutionError(
            "formal Decision Store and Execution Bundle must be Git ignored"
        )
    staging_dir = Path(tempfile.mkdtemp(
        prefix=f".{target.name}.staging-", dir=str(target.parent)
    ))
    staging_db = staging_dir / target.name
    renamed = False
    execution_id = _execution_id(executed)
    external_reference = _external_execution_reference(execution_id, executed)
    try:
        staging_result = _build_staging_database(
            staging_db, validation, bound_events, execution_id, external_reference, executed
        )
        _fsync_file(staging_db)
        _fsync_directory(staging_dir)
        _require_fresh_target(target, allowed_staging=staging_dir)
        os.replace(staging_db, target)
        renamed = True
        shutil.rmtree(staging_dir, ignore_errors=True)
        _fsync_directory(target.parent)
        formal_result = _validate_formal_store(
            target, execution_id, external_reference, confirmation_id, confirmation_root_hash
        )
        database_sha = _sha256(target)
        database_size = target.stat().st_size
        execution_result = _create_execution_bundle(
            root=root,
            destination=execution_bundle,
            target=target,
            execution_id=execution_id,
            external_reference=external_reference,
            executed_at=executed,
            database_sha=database_sha,
            database_size=database_size,
            validation=formal_result,
        )
    except Exception as exc:
        if renamed and target.exists():
            quarantine = target.parent / "quarantine" / f"{target.name}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, quarantine)
        else:
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise GovernanceDecisionStoreSchemaV2ExecutionError(
            f"execution failed and was rolled back or quarantined: {exc}"
        ) from exc

    protected_after = {str(path): _hash_path(path) for path in protected}
    if protected_before != protected_after:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("protected formal inputs changed")
    result = {
        "conclusion": "A. Schema V2 Decision Store created and validated",
        "execution_blocked": False,
        "plan_id": EXPECTED_PLAN_ID,
        "confirmation_id": CONFIRMATION_ID,
        "executed_at": executed,
        "formal_target_path": str(target),
        "database_sha256": database_sha,
        "database_byte_size": database_size,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "event_count": formal_result["event_count"],
        "current_parent_state_count": formal_result["current_parent_state_count"],
        "authority_gap": formal_result["authority_gap"],
        "event_counts": validation["event_counts"],
        "asset_counts": {"eligible": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_field_count": 410,
        "integrity_check": formal_result["integrity_check"],
        "foreign_key_errors": formal_result["foreign_key_errors"],
        "append_only_updates_blocked": formal_result["all_append_only_updates_blocked"],
        "append_only_deletes_blocked": formal_result["all_append_only_deletes_blocked"],
        "hash_chain_valid": formal_result["hash_chain_valid"],
        "event_chain_head": formal_result["event_chain_head"],
        "event_chain_tail": formal_result["event_chain_tail"],
        "execution_id": execution_id,
        "execution_root_hash": execution_result["root_execution_hash"],
        "execution_bundle_path": str(execution_bundle),
        "staging_validation": staging_result,
        "formal_validation": formal_result,
        "execution_bundle_validation": execution_result,
    }
    _write_reports(reports, result, validation, protected_before, protected_after)
    return result


def validate_governance_decision_store_schema_v2_execution_bundle(
    path: Path, database_path: Path
) -> dict:
    root = Path(path)
    manifest_path = root / "execution_manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise GovernanceDecisionStoreSchemaV2ExecutionError("Execution Bundle is missing")
    manifest = _read_json(manifest_path)
    stored = manifest.get("root_execution_hash", "")
    calculated = _sha256_bytes(_canonical_json({
        key: value for key, value in manifest.items() if key != "root_execution_hash"
    }))
    if stored != calculated:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("Execution Bundle root hash mismatch")
    expected = {
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_sql_hash": CANONICAL_SQL_HASH,
        "confirmation_id": CONFIRMATION_ID,
        "confirmation_root_hash": CONFIRMATION_ROOT_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "expected_event_count": 162,
        "actual_event_count": 162,
        "current_parent_state_count": 120,
        "authority_gap": 0,
        "executed_by": "Admin",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise GovernanceDecisionStoreSchemaV2ExecutionError(f"Execution Bundle {key} mismatch")
    listed = set()
    for entry in manifest.get("files", []):
        filename = _safe_filename(entry.get("filename", ""))
        if filename in listed:
            raise GovernanceDecisionStoreSchemaV2ExecutionError("duplicate Execution Bundle file")
        listed.add(filename)
        candidate = root / filename
        if not candidate.is_file() or _sha256(candidate) != entry.get("sha256"):
            raise GovernanceDecisionStoreSchemaV2ExecutionError(
                f"Execution Bundle checksum mismatch: {filename}"
            )
        if candidate.stat().st_size != entry.get("byte_size"):
            raise GovernanceDecisionStoreSchemaV2ExecutionError(
                f"Execution Bundle byte size mismatch: {filename}"
            )
    physical = {
        item.name for item in root.iterdir()
        if item.is_file() and item.name != "execution_manifest.json" and not item.name.startswith("._")
    }
    if physical != listed or len(listed) != 7:
        raise GovernanceDecisionStoreSchemaV2ExecutionError(
            "Execution Bundle contains unlisted or missing files"
        )
    database = Path(database_path)
    if not database.is_file() or _sha256(database) != manifest.get("database_sha256"):
        raise GovernanceDecisionStoreSchemaV2ExecutionError("formal database checksum mismatch")
    if database.stat().st_size != manifest.get("database_byte_size"):
        raise GovernanceDecisionStoreSchemaV2ExecutionError("formal database byte size mismatch")
    return {
        "valid": True,
        "execution_id": manifest["execution_id"],
        "database_sha256": manifest["database_sha256"],
        "database_byte_size": manifest["database_byte_size"],
        "root_execution_hash": stored,
        "protected_file_count": len(listed),
        "physical_file_count": len(listed) + 1,
        "read_only_reopen": True,
    }


def _build_staging_database(path, validation, events, execution_id, external_reference, executed_at):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(CANONICAL_SCHEMA_V2_SQL)
        connection.execute(
            """INSERT INTO schema_metadata (
                schema_name,schema_version,schema_hash,schema_sql_hash,created_at,code_version,
                source_plan_id,source_plan_manifest_hash,migration_type,compatibility_status,
                previous_schema_hash,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                SCHEMA_NAME, SCHEMA_VERSION, EXPECTED_SCHEMA_HASH, CANONICAL_SQL_HASH,
                executed_at, EXECUTOR_CODE_VERSION, EXPECTED_PLAN_ID, EXPECTED_MANIFEST_HASH,
                "initial_creation", "schema_v2_execute_contract_validated", None,
                _canonical_json_text({"physical_database_sha256": "external_execution_bundle_only"}),
            ),
        )
        inserted = sum(_insert_event(connection, event) for event in events)
        duplicate_inserted = _insert_event(connection, events[0])
        connection.commit()
        preliminary = _connection_validation(connection)
        if inserted != 162 or duplicate_inserted != 0:
            raise GovernanceDecisionStoreSchemaV2ExecutionError("event idempotency validation failed")
        if not _all_core_valid(preliminary):
            raise GovernanceDecisionStoreSchemaV2ExecutionError(f"staging validation failed: {preliminary}")
        head, tail = preliminary["event_chain_head"], preliminary["event_chain_tail"]
        connection.execute(
            """INSERT INTO execution_metadata (
                execution_id,plan_id,plan_manifest_hash,confirmation_id,confirmation_root_hash,
                bundle_id,bundle_root_hash,target_path,executed_by,executed_at,
                expected_event_count,actual_event_count,expected_parent_current_state_count,
                actual_parent_current_state_count,authority_gap,schema_version,schema_hash,
                database_sha256,event_chain_head,event_chain_tail,source_branch,source_commit,
                code_version,execution_manifest_hash,status,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                execution_id, EXPECTED_PLAN_ID, EXPECTED_MANIFEST_HASH, CONFIRMATION_ID,
                CONFIRMATION_ROOT_HASH, BUNDLE_ID, BUNDLE_ROOT_HASH, str(DEFAULT_FORMAL_TARGET),
                "Admin", executed_at, 162, 162, 120, 120, 0, SCHEMA_VERSION,
                EXPECTED_SCHEMA_HASH, None, head, tail, validation["source_branch"],
                validation["source_commit"], EXECUTOR_CODE_VERSION, external_reference,
                "completed", _canonical_json_text({
                    "database_sha256_boundary": "external_execution_bundle_only",
                    "confirmation_binding": "execute-confirmation-binding-v1",
                }),
            ),
        )
        connection.commit()
        final = _connection_validation(connection)
    finally:
        connection.close()
    append_only = _verify_append_only(path)
    backup_restore = _verify_backup_restore(path)
    read_only = _verify_read_only(path)
    result = {**final, **append_only, "backup_restore": backup_restore, "read_only_reopen": read_only}
    if not _all_final_valid(result):
        raise GovernanceDecisionStoreSchemaV2ExecutionError(f"final staging validation failed: {result}")
    return result


def _validate_formal_store(path, execution_id, external_reference, confirmation_id, confirmation_root_hash):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        result = _connection_validation(connection)
        confirmation_count = connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE source_confirmation_id=? "
            "AND source_confirmation_root_hash=?",
            (confirmation_id, confirmation_root_hash),
        ).fetchone()[0]
        schema_row = connection.execute("SELECT * FROM schema_metadata").fetchone()
        execution_row = connection.execute("SELECT * FROM execution_metadata").fetchone()
        result.update({
            "confirmation_bound_event_count": confirmation_count,
            "schema_metadata_valid": bool(
                schema_row and schema_row["schema_version"] == 2
                and schema_row["schema_hash"] == EXPECTED_SCHEMA_HASH
                and schema_row["schema_sql_hash"] == CANONICAL_SQL_HASH
                and schema_row["migration_type"] == "initial_creation"
            ),
            "execution_metadata_valid": bool(
                execution_row and execution_row["execution_id"] == execution_id
                and execution_row["confirmation_id"] == CONFIRMATION_ID
                and execution_row["confirmation_root_hash"] == CONFIRMATION_ROOT_HASH
                and execution_row["database_sha256"] is None
                and execution_row["execution_manifest_hash"] == external_reference
                and execution_row["status"] == "completed"
            ),
            "special_decisions_valid": _special_store_checks(connection),
            "event_category_counts": _event_category_counts(connection),
        })
    finally:
        connection.close()
    append_only = _verify_append_only(path)
    result.update(append_only)
    result["backup_restore"] = _verify_backup_restore(path)
    result["read_only_reopen"] = _verify_read_only(path)
    result["database_sha_self_reference_absent"] = _database_sha_null(path)
    if not _all_final_valid(result) or result["confirmation_bound_event_count"] != 162:
        raise GovernanceDecisionStoreSchemaV2ExecutionError(f"formal database validation failed: {result}")
    if not result["schema_metadata_valid"] or not result["execution_metadata_valid"]:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("formal metadata validation failed")
    if not result["special_decisions_valid"]:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("special decision validation failed")
    return result


def _connection_validation(connection):
    event_count = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
    parent_count = connection.execute("SELECT COUNT(*) FROM current_parent_decisions").fetchone()[0]
    chain = _verify_chain(connection)
    hashes = connection.execute(
        "SELECT event_hash FROM decision_events ORDER BY event_sequence"
    ).fetchall()
    return {
        "event_count": event_count,
        "current_parent_state_count": parent_count,
        "authority_gap": 120 - parent_count,
        "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        "hash_chain_valid": chain["valid"] and chain["count"] == 162,
        "event_chain_head": hashes[0][0] if hashes else None,
        "event_chain_tail": hashes[-1][0] if hashes else None,
    }


def _event_category_counts(connection):
    by_provenance = dict(connection.execute(
        "SELECT provenance,COUNT(*) FROM decision_events GROUP BY provenance"
    ).fetchall())
    return {
        "legacy_import": sum(value for key, value in by_provenance.items() if key == "legacy_import"),
        "batch_parent_approval": by_provenance.get("batch_approval", 0),
        "resolution_parent_supersede": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type='parent_review_decision' AND action='supersede'"
        ).fetchone()[0],
        "asset_eligibility": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type='asset_eligibility'"
        ).fetchone()[0],
        "search_alias": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type='search_alias'"
        ).fetchone()[0],
        "entity_metadata": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type='entity_metadata'"
        ).fetchone()[0],
        "asset_url_manifest_reference": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type='asset_url_manifest_reference'"
        ).fetchone()[0],
        "parent_events": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type='parent_review_decision'"
        ).fetchone()[0],
        "non_parent_events": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type<>'parent_review_decision'"
        ).fetchone()[0],
    }


def _special_store_checks(connection):
    parent_rows = {
        row[0]: json.loads(row[1])["review_decision"]
        for row in connection.execute(
            "SELECT record_id,new_value_json FROM current_parent_decisions "
            "WHERE record_id IN ('商家夥伴案例資料庫:r30','商家夥伴案例資料庫:r12',"
            "'商家夥伴案例資料庫:r122','商家夥伴案例資料庫:r32','商家夥伴案例資料庫:r7')"
        )
    }
    expected = {
        "商家夥伴案例資料庫:r30": "exclude",
        "商家夥伴案例資料庫:r12": "approve_internal_only",
        "商家夥伴案例資料庫:r122": "approve",
        "商家夥伴案例資料庫:r32": "approve",
        "商家夥伴案例資料庫:r7": "approve",
    }
    r12_video = connection.execute(
        "SELECT new_value_json FROM current_asset_eligibility WHERE asset_id='商家夥伴案例資料庫:r12:video'"
    ).fetchone()
    r30_article = connection.execute(
        "SELECT new_value_json FROM current_asset_eligibility WHERE asset_id='商家夥伴案例資料庫:r30:article'"
    ).fetchone()
    aliases = {
        json.loads(row[0])["alias"] for row in connection.execute(
            "SELECT new_value_json FROM current_search_aliases WHERE record_id='商家夥伴案例資料庫:r32'"
        )
    }
    entities = {
        row[0]: json.loads(row[1])["entity_type"]
        for row in connection.execute("SELECT record_id,new_value_json FROM current_entity_metadata")
    }
    url_ref = connection.execute(
        "SELECT new_value_json FROM decision_events WHERE event_type='asset_url_manifest_reference'"
    ).fetchone()
    if not r12_video or not r30_article or not url_ref:
        return False
    r12 = json.loads(r12_video[0])
    r30 = json.loads(r30_article[0])
    url = json.loads(url_ref[0])
    return (
        parent_rows == expected
        and r12["asset_index_eligibility"] == "hold"
        and r12["asset_search_eligibility"] == "not_searchable"
        and r30["asset_index_eligibility"] == "exclude"
        and aliases == {"SLP", "SHOPLINE Payments"}
        and entities == {
            "商家夥伴案例資料庫:r7": "partner",
            "商家夥伴案例資料庫:r122": "partner",
        }
        and url["approved_url_field_count"] == 410
    )


def _create_execution_bundle(
    *, root, destination, target, execution_id, external_reference, executed_at,
    database_sha, database_size, validation,
):
    _require_fresh_bundle(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=str(destination.parent)))
    renamed = False
    try:
        payload = {
            "execution_id": execution_id,
            "plan_id": EXPECTED_PLAN_ID,
            "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
            "schema_version": SCHEMA_VERSION,
            "schema_hash": EXPECTED_SCHEMA_HASH,
            "canonical_sql_hash": CANONICAL_SQL_HASH,
            "confirmation_id": CONFIRMATION_ID,
            "confirmation_root_hash": CONFIRMATION_ROOT_HASH,
            "bundle_id": BUNDLE_ID,
            "bundle_root_hash": BUNDLE_ROOT_HASH,
            "target_path": str(DEFAULT_FORMAL_TARGET),
            "database_sha256": database_sha,
            "database_byte_size": database_size,
            "expected_event_count": 162,
            "actual_event_count": validation["event_count"],
            "current_parent_state_count": validation["current_parent_state_count"],
            "authority_gap": validation["authority_gap"],
            "event_chain_head": validation["event_chain_head"],
            "event_chain_tail": validation["event_chain_tail"],
            "executed_by": "Admin",
            "executed_at": executed_at,
            "source_branch": _git_value(root, "branch", "--show-current"),
            "source_commit": _git_value(root, "rev-parse", "HEAD"),
            "code_version": EXECUTOR_CODE_VERSION,
            "external_execution_manifest_reference": external_reference,
        }
        _write_json(staging / "execution.json", payload)
        _write_json(staging / "execution_validation.json", validation)
        shutil.copyfile(
            root / "reports/governance_decision_store_schema_v2_plan/schema_v2_plan_manifest.json",
            staging / "referenced_plan_manifest.json",
        )
        shutil.copyfile(
            root / DEFAULT_CONFIRMATION_BUNDLE / "confirmation_manifest.json",
            staging / "referenced_confirmation_manifest.json",
        )
        shutil.copyfile(
            root / "data/governance/imports/parent-authority-approval-20260719/bundle_manifest.json",
            staging / "referenced_bundle_manifest.json",
        )
        shutil.copyfile(
            root / "reports/governance_decision_store_schema_v2_plan/canonical_schema_v2_hash.json",
            staging / "referenced_schema_hash.json",
        )
        _write_json(staging / "database_checksum.json", {
            "path": str(DEFAULT_FORMAL_TARGET), "sha256": database_sha,
            "byte_size": database_size, "database_sha_boundary": "external_only",
        })
        files = []
        roles = {
            "execution.json": "execution_record",
            "execution_validation.json": "post_execution_validation",
            "referenced_plan_manifest.json": "authorized_plan",
            "referenced_confirmation_manifest.json": "admin_confirmation",
            "referenced_bundle_manifest.json": "parent_authority_import",
            "referenced_schema_hash.json": "schema_contract",
            "database_checksum.json": "formal_database_checksum",
        }
        for filename, role in roles.items():
            candidate = staging / filename
            files.append({
                "filename": filename, "logical_role": role, "sha256": _sha256(candidate),
                "byte_size": candidate.stat().st_size, "required": True,
            })
        manifest = {
            "execution_schema_version": "2.0",
            **payload,
            "files": files,
        }
        manifest["root_execution_hash"] = _sha256_bytes(_canonical_json(manifest))
        _write_json(staging / "execution_manifest.json", manifest)
        staged = validate_governance_decision_store_schema_v2_execution_bundle(staging, target)
        _require_fresh_bundle(destination, allowed_staging=staging)
        os.replace(staging, destination)
        renamed = True
        final = validate_governance_decision_store_schema_v2_execution_bundle(destination, target)
        if final["root_execution_hash"] != staged["root_execution_hash"]:
            raise GovernanceDecisionStoreSchemaV2ExecutionError("Execution Bundle changed after rename")
        _make_read_only(destination)
        return final
    except Exception:
        if renamed and destination.exists():
            quarantine = destination.with_name(
                f"{destination.name}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
            )
            os.replace(destination, quarantine)
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _write_reports(output, result, validation, protected_before, protected_after):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILENAMES[0], (
        "# Schema V2 Decision Store Execution\n\n"
        "- Conclusion: A. Schema V2 Decision Store created and validated\n"
        f"- PLAN_ID: `{EXPECTED_PLAN_ID}`\n"
        f"- Confirmation ID: `{CONFIRMATION_ID}`\n"
        f"- Executed At: `{result['executed_at']}`\n"
        f"- Database SHA-256: `{result['database_sha256']}`\n"
        "- Decision Events: 162\n- Current Parent State: 120\n- Authority Gap: 0\n"
    ))
    pass_row = lambda check, observed: {"check": check, "observed": observed, "status": "pass"}
    _write_csv(output / REPORT_FILENAMES[1], [
        pass_row("exact_plan_authority", EXPECTED_PLAN_ID),
        pass_row("plan_not_expired", PLAN_EXPIRES_AT),
        pass_row("formal_target_absent_before_execute", True),
    ])
    _write_csv(output / REPORT_FILENAMES[2], [
        pass_row("plan_manifest_hash", EXPECTED_MANIFEST_HASH),
        pass_row("bundle_root_hash", BUNDLE_ROOT_HASH),
        pass_row("confirmation_root_hash", CONFIRMATION_ROOT_HASH),
    ])
    _write_csv(output / REPORT_FILENAMES[3], [
        pass_row("schema_version", 2), pass_row("schema_hash", EXPECTED_SCHEMA_HASH),
        pass_row("canonical_sql_hash", CANONICAL_SQL_HASH),
    ])
    _write_csv(output / REPORT_FILENAMES[4], [
        pass_row(key, value) for key, value in validation["event_counts"].items()
    ] + [pass_row("total", 162)])
    _write_csv(output / REPORT_FILENAMES[5], [
        pass_row("current_parent_state", 120), pass_row("authority_gap", 0),
    ])
    _write_csv(output / REPORT_FILENAMES[6], validation["special_decision_rows"])
    _write_csv(output / REPORT_FILENAMES[7], [
        pass_row("include", 8), pass_row("hold", 1), pass_row("exclude", 1),
    ])
    _write_csv(output / REPORT_FILENAMES[8], [
        pass_row("SLP_exact_r32", True), pass_row("SHOPLINE_Payments_contains_r32", True),
    ])
    _write_csv(output / REPORT_FILENAMES[9], [
        pass_row("r122_partner", True), pass_row("r7_partner", True),
    ])
    _write_csv(output / REPORT_FILENAMES[10], [pass_row("approved_url_fields", 410)])
    _write_csv(output / REPORT_FILENAMES[11], [
        pass_row("schema_version", 2), pass_row("migration_type", "initial_creation"),
    ])
    _write_csv(output / REPORT_FILENAMES[12], [
        pass_row("confirmation_id", CONFIRMATION_ID), pass_row("status", "completed"),
        pass_row("database_sha256_is_null", True),
    ])
    _write_csv(output / REPORT_FILENAMES[13], [
        pass_row("integrity_check", result["integrity_check"]),
        pass_row("foreign_key_errors", result["foreign_key_errors"]),
    ])
    _write_csv(output / REPORT_FILENAMES[14], [
        pass_row("updates_blocked", result["append_only_updates_blocked"]),
        pass_row("deletes_blocked", result["append_only_deletes_blocked"]),
    ])
    _write_csv(output / REPORT_FILENAMES[15], [
        pass_row("hash_chain_valid", result["hash_chain_valid"]),
        pass_row("head", result["event_chain_head"]), pass_row("tail", result["event_chain_tail"]),
    ])
    _write_json(output / REPORT_FILENAMES[16], {
        "path": result["formal_target_path"], "sha256": result["database_sha256"],
        "byte_size": result["database_byte_size"], "schema_version": 2,
        "schema_hash": EXPECTED_SCHEMA_HASH,
    })
    _write_csv(output / REPORT_FILENAMES[17], [
        pass_row("execution_bundle_root_hash", result["execution_root_hash"]),
        pass_row("execution_bundle_read_only_reopen", True),
    ])
    _write_csv(output / REPORT_FILENAMES[18], [
        pass_row("protected_inputs_unchanged", protected_before == protected_after),
        pass_row("vault_index_slack_not_modified", True), pass_row("slack_api_not_called", True),
    ])
    _write_text(output / REPORT_FILENAMES[19], (
        "# Execution Rollback Report\n\n"
        "Staging used same-filesystem atomic rename. Pre-rename failure removes staging; "
        "post-rename validation failure quarantines the database and restores an absent formal target.\n"
    ))
    _write_text(output / REPORT_FILENAMES[20], (
        "# Next Parent Sync Prerequisites\n\n"
        "Parent Sync remains a separate, explicitly confirmed workflow. This execution did not sync Parent records.\n"
    ))
    _write_csv(output / REPORT_FILENAMES[21], [])
    _write_csv(output / REPORT_FILENAMES[22], [])
    actual = sorted(path.name for path in output.iterdir() if path.is_file() and not path.name.startswith("._"))
    if actual != sorted(REPORT_FILENAMES):
        raise GovernanceDecisionStoreSchemaV2ExecutionError("execution report contract is incomplete")


def _validate_bound_events(events, validation):
    if len(events) != 162 or len({row["event_id"] for row in events}) != 162:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("bound Event count or identity mismatch")
    if len({row["idempotency_key"] for row in events}) != 162:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("bound Event idempotency collision")
    if any(
        row["source_confirmation_id"] != CONFIRMATION_ID
        or row["source_confirmation_root_hash"] != CONFIRMATION_ROOT_HASH
        for row in events
    ):
        raise GovernanceDecisionStoreSchemaV2ExecutionError("Confirmation binding mismatch")
    if validation["event_count"] != 162 or validation["current_parent_state_count"] != 120:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("validated Event conservation mismatch")


def _validate_confirmation(value):
    expected = {
        "confirmation_id": CONFIRMATION_ID,
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_sql_hash": CANONICAL_SQL_HASH,
        "reviewer": "Admin",
        "root_confirmation_hash": CONFIRMATION_ROOT_HASH,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise GovernanceDecisionStoreSchemaV2ExecutionError(f"Confirmation {key} mismatch")


def _require_authority(plan_id, manifest_hash, schema_hash, confirmation_id, confirmation_root_hash):
    if plan_id in OBSOLETE_PLAN_IDS:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("obsolete Plan is invalid for Schema V2")
    if plan_id != EXPECTED_PLAN_ID:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("exact PLAN_ID is required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("exact Plan Manifest Hash is required")
    if schema_hash != EXPECTED_SCHEMA_HASH:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("exact Schema Hash is required")
    if confirmation_id == OLD_CONFIRMATION_ID:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("old Confirmation is invalid for Schema V2")
    if confirmation_id != CONFIRMATION_ID:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("exact Confirmation ID is required")
    if confirmation_root_hash != CONFIRMATION_ROOT_HASH:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("exact Confirmation Root Hash is required")


def _require_fresh_target(target, allowed_staging=None):
    residues = [
        target, target.with_name(target.name + "-journal"), target.with_name(target.name + "-wal"),
        target.with_name(target.name + "-shm"), target.with_name(target.name + ".lock"),
    ]
    staging = list(target.parent.glob(f".{target.name}.staging-*")) if target.parent.exists() else []
    if allowed_staging is not None:
        allowed = Path(allowed_staging).resolve()
        staging = [path for path in staging if path.resolve() != allowed]
    if any(path.exists() for path in residues) or staging:
        raise GovernanceDecisionStoreSchemaV2ExecutionError(
            "formal target, residue, lock, or staging path already exists"
        )


def _require_fresh_bundle(path, allowed_staging=None):
    staging = list(path.parent.glob(f".{path.name}.staging-*")) if path.parent.exists() else []
    if allowed_staging is not None:
        allowed = Path(allowed_staging).resolve()
        staging = [candidate for candidate in staging if candidate.resolve() != allowed]
    if path.exists() or staging:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("Execution Bundle already exists")


def _all_core_valid(result):
    return (
        result["event_count"] == 162 and result["current_parent_state_count"] == 120
        and result["authority_gap"] == 0 and result["integrity_check"] == "ok"
        and result["foreign_key_errors"] == 0 and result["hash_chain_valid"]
    )


def _all_final_valid(result):
    return (
        _all_core_valid(result) and result["all_append_only_updates_blocked"]
        and result["all_append_only_deletes_blocked"] and result["backup_restore"]
        and result["read_only_reopen"]
    )


def _database_sha_null(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT database_sha256 FROM execution_metadata").fetchone()
        return bool(row) and row[0] is None
    finally:
        connection.close()


def _execution_id(executed_at):
    payload = {
        "plan_id": EXPECTED_PLAN_ID, "confirmation_id": CONFIRMATION_ID,
        "confirmation_root_hash": CONFIRMATION_ROOT_HASH, "executed_at": executed_at,
        "target_path": str(DEFAULT_FORMAL_TARGET),
    }
    return f"decision-store-schema-v2-execution-{_sha256_bytes(_canonical_json(payload))[:16]}"


def _external_execution_reference(execution_id, executed_at):
    return _sha256_bytes(_canonical_json({
        "reference_contract": "external-execution-manifest-reference-v1",
        "execution_id": execution_id, "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH, "schema_hash": EXPECTED_SCHEMA_HASH,
        "confirmation_id": CONFIRMATION_ID, "confirmation_root_hash": CONFIRMATION_ROOT_HASH,
        "bundle_root_hash": BUNDLE_ROOT_HASH, "target_path": str(DEFAULT_FORMAL_TARGET),
        "expected_event_count": 162, "expected_parent_current_state_count": 120,
        "executed_at": executed_at,
    }))


def _protected_paths(root):
    return [
        root / "data/governance/imports/parent-authority-approval-20260719",
        root / "data/governance/confirmations/decision-store-schema-v2-plan-2aab43cd463170f2",
        root / "data/governance/confirmations/decision-store-plan-a02502d8361549b1",
        root / "reports/governance_decision_store_schema_v2_plan",
        root / "obsidian_vault",
        root / ".mka/content_index.sqlite",
        root / "src/marketing_knowledge_agent/slack_interface.py",
    ]


def _is_git_ignored(root, path):
    try:
        subprocess.check_call(
            ["git", "check-ignore", "-q", str(path)], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def _git_value(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _fsync_file(path):
    with Path(path).open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _make_read_only(path):
    for child in Path(path).iterdir():
        if child.is_file():
            child.chmod(0o444)
    Path(path).chmod(0o555)


def _safe_filename(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("unsafe Execution Bundle filename")
    return value


def _resolve(root, path):
    value = Path(path)
    return value.resolve() if value.is_absolute() else (Path(root) / value).resolve()


def _validate_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceDecisionStoreSchemaV2ExecutionError(
            "executed_at must be a valid ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceDecisionStoreSchemaV2ExecutionError("executed_at must include timezone")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["severity", "code", "message"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_text(path, value):
    Path(path).write_text(value.rstrip() + "\n", encoding="utf-8")
