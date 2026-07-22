from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .chunking import chunk_documents
from .embeddings import embed_text
from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .ingestion import stable_id
from .models import Document, DocumentMetadata
from . import store_data_sync_plan_v2_confirmation as confirmation


EXPECTED_PLAN_ID = confirmation.EXPECTED_PLAN_ID
EXPECTED_MANIFEST_HASH = confirmation.EXPECTED_MANIFEST_HASH
EXPECTED_CONFIRMATION_ID = "store-data-sync-confirmation-395a1e9b7b71d697"
EXPECTED_CONFIRMATION_ROOT_HASH = "a66af3756d43a4c042fdae0e20f02ca93317982ca9447660081b92157789d47e"
EXPECTED_CONTRACT_HASH = confirmation.EXPECTED_CONTRACT_HASH
EXPECTED_MANAGED_DELTA_HASH = confirmation.EXPECTED_MANAGED_DELTA_HASH
EXPECTED_FORMAL_DELTA_HASH = confirmation.EXPECTED_SQLITE_DELTA_HASH
EXPECTED_DECISION_STORE_SHA256 = confirmation.EXPECTED_DATABASE_SHA256
EXPECTED_DECISION_STORE_SIZE = confirmation.EXPECTED_DATABASE_SIZE
EXPECTED_DECISION_STORE_EXECUTION_ROOT = confirmation.EXPECTED_EXECUTION_ROOT_HASH
EXPECTED_CONFIRMATION_VALIDATOR_COMMIT = "74da9e4cce360988b48e3a6cc2beb69df8d54746"
PLAN_EXPIRES_AT = confirmation.PLAN_EXPIRES_AT
EXECUTOR_CODE_VERSION = "store-data-sync-plan-v2-execution-v1"
BACKUP_SCHEMA_VERSION = "1.0"
EXECUTION_SCHEMA_VERSION = "1.0"

DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / EXPECTED_PLAN_ID
DEFAULT_BACKUP_PATH = Path("data/governance/backups") / EXPECTED_PLAN_ID
DEFAULT_EXECUTION_PATH = Path("data/governance/executions") / EXPECTED_PLAN_ID
DEFAULT_REPORT_DIR = Path("reports/store_data_sync_plan_v2_execution")
DEFAULT_MANAGED_VAULT = confirmation.DEFAULT_MANAGED_VAULT
DEFAULT_FORMAL_SQLITE = confirmation.DEFAULT_FORMAL_SQLITE
DEFAULT_DECISION_STORE = confirmation.DEFAULT_DECISION_STORE
DEFAULT_PARENT_SOURCE = confirmation.DEFAULT_PARENT_SOURCE
DEFAULT_RENDERER = confirmation.DEFAULT_RENDERER

CREATE_ROWS = {7, 12, 32, 122}
GOVERNANCE_ONLY_ROWS = {30, 57, 83, 87, 101, 102, 103, 107, 116, 121}
AUDIT_ONLY_FIELDS = set(confirmation.AUDIT_ONLY_FIELDS)
REPORT_FILENAMES = (
    "store_data_sync_execution_summary.md",
    "execution_preflight_validation.csv",
    "plan_confirmation_validation.csv",
    "materialization_contract_validation.csv",
    "backup_bundle_validation.csv",
    "managed_vault_staging_validation.csv",
    "managed_vault_write_validation.csv",
    "managed_vault_post_sync_validation.csv",
    "formal_sqlite_staging_validation.csv",
    "formal_sqlite_write_validation.csv",
    "formal_sqlite_existing_rows_unchanged.csv",
    "formal_sqlite_post_sync_validation.csv",
    "governance_only_post_sync_validation.csv",
    "r20_vault_only_post_sync_validation.csv",
    "four_create_records_post_sync_validation.csv",
    "special_record_post_sync_validation.csv",
    "asset_url_boundary_post_sync_validation.csv",
    "decision_store_unchanged_validation.csv",
    "rollback_rehearsal_validation.csv",
    "execution_bundle_validation.csv",
    "formal_system_boundary_validation.csv",
    "production_search_not_activated_validation.csv",
    "execution_errors.csv",
    "execution_warnings.csv",
)


class StoreDataSyncPlanV2ExecutionError(RuntimeError):
    pass


def execute_store_data_sync_plan_v2(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    confirmation_id: str,
    confirmation_root_hash: str,
    executed_at: Optional[str] = None,
    confirmation_path: Path = DEFAULT_CONFIRMATION_PATH,
    backup_path: Path = DEFAULT_BACKUP_PATH,
    execution_path: Path = DEFAULT_EXECUTION_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    managed_vault_root: Path = DEFAULT_MANAGED_VAULT,
    formal_sqlite_path: Path = DEFAULT_FORMAL_SQLITE,
    decision_store_path: Path = DEFAULT_DECISION_STORE,
    parent_source_path: Path = DEFAULT_PARENT_SOURCE,
    production_renderer_path: Path = DEFAULT_RENDERER,
    temporary_root: Optional[Path] = None,
    require_git_ignored: bool = True,
    allow_noncanonical_test_targets: bool = False,
) -> dict:
    _require_exact_authority(plan_id, manifest_hash, confirmation_id, confirmation_root_hash)
    timestamp = executed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    executed = _timestamp(timestamp)
    if executed > datetime.fromisoformat(PLAN_EXPIRES_AT):
        raise StoreDataSyncPlanV2ExecutionError("Plan expired; Execute is forbidden")

    root = Path(repo_root).resolve()
    paths = {
        "confirmation": _resolve(root, confirmation_path),
        "backup": _resolve(root, backup_path),
        "execution": _resolve(root, execution_path),
        "reports": _resolve(root, report_dir),
        "managed": _resolve(root, managed_vault_root),
        "formal": _resolve(root, formal_sqlite_path),
        "decision_store": _resolve(root, decision_store_path),
        "parent_source": _resolve(root, parent_source_path),
        "renderer": _resolve(root, production_renderer_path),
        "plan": _resolve(root, confirmation.DEFAULT_PLAN_MANIFEST),
    }
    _validate_target_paths(root, paths, allow_noncanonical_test_targets)
    if paths["backup"].exists():
        raise StoreDataSyncPlanV2ExecutionError("Backup Bundle already exists; Execute cannot overwrite it")
    if paths["execution"].exists():
        raise StoreDataSyncPlanV2ExecutionError("Execution Bundle already exists; Execute cannot be rerun")
    if require_git_ignored:
        for label in ("backup", "execution", "reports"):
            if not confirmation._git_ignored(root, paths[label]):
                raise StoreDataSyncPlanV2ExecutionError(f"{label} path must be Git ignored")

    try:
        confirmation_bundle = confirmation.validate_store_data_sync_plan_v2_confirmation(paths["confirmation"])
    except confirmation.StoreDataSyncPlanV2ConfirmationError as exc:
        raise StoreDataSyncPlanV2ExecutionError(f"Confirmation preflight failed: {exc}") from exc
    if confirmation_bundle["confirmation_id"] != EXPECTED_CONFIRMATION_ID:
        raise StoreDataSyncPlanV2ExecutionError("Confirmation ID mismatch")
    if confirmation_bundle["root_confirmation_hash"] != EXPECTED_CONFIRMATION_ROOT_HASH:
        raise StoreDataSyncPlanV2ExecutionError("Confirmation Root Hash mismatch")

    try:
        validation = confirmation.validate_store_data_sync_plan_v2(
            repo_root=root,
            plan_id=plan_id,
            manifest_hash=manifest_hash,
            decision_store_path=paths["decision_store"],
            parent_source_path=paths["parent_source"],
            managed_vault_root=paths["managed"],
            formal_sqlite_path=paths["formal"],
            production_renderer_path=paths["renderer"],
            temporary_root=temporary_root,
            now=timestamp,
        )
    except Exception as exc:
        raise StoreDataSyncPlanV2ExecutionError(f"preflight validation failed: {exc}") from exc
    preflight = _preflight(root, paths, validation, confirmation_bundle)
    before = _formal_boundary_snapshot(root, paths)

    workspace = Path(tempfile.mkdtemp(
        prefix=f".{EXPECTED_PLAN_ID}.staging-", dir=str(paths["managed"].parent)
    ))
    journal = paths["reports"] / "execution_journal.json"
    backup = None
    formal_staging = None
    writes_started = False
    try:
        paths["reports"].mkdir(parents=True, exist_ok=True)
        source = _parent_source(paths["parent_source"])
        managed_staging = workspace / "managed_parent_projection"
        managed_stage = _build_managed_staging(paths["managed"], managed_staging, validation, source)
        formal_staging = _build_formal_staging(
            paths["formal"], paths["formal"].parent, managed_staging, validation, source
        )
        rollback_rehearsal = _rollback_rehearsal(
            workspace, paths["managed"], managed_staging, paths["formal"], formal_staging, validation
        )
        backup = _create_backup_bundle(paths["backup"], paths, validation, timestamp, require_git_ignored)
        _write_journal(journal, "prepared", timestamp, backup, [])

        writes_started = True
        managed_write = _apply_managed_delta(paths["managed"], managed_staging, validation, journal, timestamp, backup)
        _write_journal(journal, "managed_committed", timestamp, backup, managed_write["files"])
        _atomic_replace_file(formal_staging, paths["formal"])
        formal_staging = None
        _write_journal(journal, "targets_committed", timestamp, backup, managed_write["files"])

        post = _post_sync_validation(root, paths, validation, managed_stage, before)
        execution = _create_execution_bundle(
            paths["execution"], paths, validation, confirmation_bundle, timestamp,
            backup, managed_write, post, rollback_rehearsal, require_git_ignored,
        )
        _write_journal(journal, "completed", timestamp, backup, managed_write["files"], execution)
        summary = _summary(timestamp, paths, before, validation, backup, execution, post, rollback_rehearsal)
        _write_reports(paths["reports"], summary, preflight, validation, backup, managed_stage,
                       managed_write, post, rollback_rehearsal, execution)
        return summary
    except Exception as exc:
        rollback = None
        if paths["execution"].exists():
            quarantine = paths["execution"].with_name(
                f"{paths['execution'].name}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
            )
            if not quarantine.exists():
                os.replace(paths["execution"], quarantine)
        if writes_started and backup is not None:
            try:
                rollback = _rollback_targets(paths, validation, backup)
            except Exception as rollback_exc:
                _write_failure_reports(paths["reports"], exc, rollback_exc)
                raise StoreDataSyncPlanV2ExecutionError(
                    f"Execute failed and rollback validation failed: {exc}; {rollback_exc}"
                ) from exc
        _write_failure_reports(paths["reports"], exc, None, rollback)
        if isinstance(exc, StoreDataSyncPlanV2ExecutionError):
            if writes_started:
                raise StoreDataSyncPlanV2ExecutionError(f"Execute failed and was rolled back: {exc}") from exc
            raise
        if writes_started:
            raise StoreDataSyncPlanV2ExecutionError(f"Execute failed and was rolled back: {exc}") from exc
        raise StoreDataSyncPlanV2ExecutionError(f"Execute blocked before write: {exc}") from exc
    finally:
        if formal_staging and formal_staging.exists():
            formal_staging.unlink()
        shutil.rmtree(workspace, ignore_errors=True)


def validate_store_data_sync_execution_bundle(path: Path) -> dict:
    root = Path(path)
    manifest_path = root / "execution_manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise StoreDataSyncPlanV2ExecutionError("Execution Bundle is missing")
    manifest = _read_json(manifest_path)
    stored = manifest.get("root_execution_hash", "")
    expected = _hash_json({key: value for key, value in manifest.items() if key != "root_execution_hash"})
    if not stored or stored != expected:
        raise StoreDataSyncPlanV2ExecutionError("Execution root hash mismatch")
    required = {
        "execution_schema_version": EXECUTION_SCHEMA_VERSION,
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "confirmation_id": EXPECTED_CONFIRMATION_ID,
        "confirmation_root_hash": EXPECTED_CONFIRMATION_ROOT_HASH,
        "materialization_contract_hash": EXPECTED_CONTRACT_HASH,
        "managed_vault_delta_hash": EXPECTED_MANAGED_DELTA_HASH,
        "formal_sqlite_delta_hash": EXPECTED_FORMAL_DELTA_HASH,
        "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
        "decision_store_execution_root_hash": EXPECTED_DECISION_STORE_EXECUTION_ROOT,
        "executed_by": "Admin",
    }
    for key, value in required.items():
        if manifest.get(key) != value:
            raise StoreDataSyncPlanV2ExecutionError(f"Execution {key} mismatch")
    listed = set()
    for entry in manifest.get("files", []):
        name = _safe_name(entry.get("filename", ""))
        candidate = root / name
        if name in listed or not candidate.is_file():
            raise StoreDataSyncPlanV2ExecutionError("Execution Bundle file inventory mismatch")
        if _sha256(candidate) != entry.get("sha256") or candidate.stat().st_size != entry.get("byte_size"):
            raise StoreDataSyncPlanV2ExecutionError(f"Execution checksum mismatch: {name}")
        listed.add(name)
    physical = {
        item.name for item in root.iterdir()
        if item.is_file() and item.name != "execution_manifest.json" and not item.name.startswith("._")
    }
    if physical != listed or len(listed) != 11:
        raise StoreDataSyncPlanV2ExecutionError("Execution contains unlisted or missing files")
    execution = _read_json(root / "execution.json")
    for key in (
        "execution_id", "plan_id", "plan_manifest_hash", "confirmation_id",
        "confirmation_root_hash", "materialization_contract_hash",
        "managed_vault_delta_hash", "formal_sqlite_delta_hash",
        "decision_store_sha256", "decision_store_execution_root_hash",
        "executed_by", "executed_at",
    ):
        if execution.get(key) != manifest.get(key):
            raise StoreDataSyncPlanV2ExecutionError(f"Execution payload {key} mismatch")
    if not manifest.get("executor_source_branch") or not manifest.get("executor_source_commit"):
        raise StoreDataSyncPlanV2ExecutionError("Executor source identity is missing")
    referenced_plan = _read_json(root / "referenced_plan_manifest.json")
    referenced_confirmation = _read_json(root / "referenced_confirmation_manifest.json")
    if referenced_plan.get("manifest_hash") != EXPECTED_MANIFEST_HASH:
        raise StoreDataSyncPlanV2ExecutionError("referenced Plan mismatch")
    if referenced_confirmation.get("root_confirmation_hash") != EXPECTED_CONFIRMATION_ROOT_HASH:
        raise StoreDataSyncPlanV2ExecutionError("referenced Confirmation mismatch")
    backup = _read_json(root / "backup_reference.json")
    if backup.get("root_backup_hash") != manifest.get("backup_root_hash"):
        raise StoreDataSyncPlanV2ExecutionError("referenced Backup mismatch")
    formal = _read_json(root / "formal_sqlite_after_checksum.json")
    if formal.get("sha256") != manifest.get("after_checksums", {}).get("formal_sqlite"):
        raise StoreDataSyncPlanV2ExecutionError("formal SQLite after checksum mismatch")
    return {
        "valid": True,
        "execution_id": manifest["execution_id"],
        "root_execution_hash": stored,
        "executed_at": manifest["executed_at"],
        "protected_file_count": len(listed),
        "physical_file_count": len(listed) + 1,
        "read_only_reopen": True,
    }


def _preflight(root, paths, validation, confirmation_bundle):
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", EXPECTED_CONFIRMATION_VALIDATOR_COMMIT, "HEAD"],
        cwd=root, check=False, capture_output=True,
    ).returncode != 0:
        raise StoreDataSyncPlanV2ExecutionError("Confirmation Validator Commit is not an ancestor of the executor")
    if validation["managed_vault_counts"] != {
        "current": 106, "create": 4, "update": 106,
        "no_change": 0, "target": 110, "path_collisions": 0,
    }:
        raise StoreDataSyncPlanV2ExecutionError("Managed Vault preflight counts mismatch")
    if validation["formal_sqlite_counts"] != {
        "current": 105, "create": 4, "update": 0,
        "no_change": 105, "target": 109, "not_projected": 11,
    }:
        raise StoreDataSyncPlanV2ExecutionError("Formal SQLite preflight counts mismatch")
    if validation["audit_only_write_occurrences"] != 0:
        raise StoreDataSyncPlanV2ExecutionError("audit-only materialization boundary failed")
    if _sqlite_sidecars(paths["decision_store"]) or _sqlite_sidecars(paths["formal"]):
        raise StoreDataSyncPlanV2ExecutionError("SQLite sidecar residue detected")
    residues = list(paths["managed"].parent.glob(f".{EXPECTED_PLAN_ID}.staging-*"))
    residues += list(paths["managed"].parent.glob(f".{paths['managed'].name}.{EXPECTED_PLAN_ID}.*"))
    residues += list(paths["formal"].parent.glob(f".{paths['formal'].name}.{EXPECTED_PLAN_ID}.*"))
    if residues:
        raise StoreDataSyncPlanV2ExecutionError("staging or execution-in-progress residue detected")
    files = _managed_parent_files(paths["managed"])
    for row in validation["managed_vault_delta_records"]:
        source_row = _source_row(row["record_id"])
        relative = _safe_relative(row["target_path"]).as_posix()
        target = paths["managed"] / relative
        if row["action"] == "create" and target.exists():
            raise StoreDataSyncPlanV2ExecutionError(f"create path collision: {relative}")
        if row["action"] == "update" and (source_row not in files or files[source_row] != target):
            raise StoreDataSyncPlanV2ExecutionError(f"update path mismatch: {relative}")
    if confirmation_bundle["protected_file_count"] != 8:
        raise StoreDataSyncPlanV2ExecutionError("Confirmation protected file count mismatch")
    return [{"check": key, "status": "pass", "value": value} for key, value in {
        "plan_identity": EXPECTED_PLAN_ID,
        "confirmation_identity": EXPECTED_CONFIRMATION_ID,
        "confirmation_validator_commit": EXPECTED_CONFIRMATION_VALIDATOR_COMMIT,
        "plan_not_expired": True,
        "managed_current": 106,
        "formal_current": 105,
        "path_collision": 0,
        "sqlite_sidecars": 0,
    }.items()]


def _build_managed_staging(current_root, staging_root, validation, source):
    staging_root.mkdir(parents=True, exist_ok=False)
    current_files = _managed_parent_files(current_root)
    projection = {row["record_id"]: row for row in validation["managed_projection"]}
    desired = {row["record_id"]: row for row in validation["authoritative_records"]}
    rows = []
    for delta in validation["managed_vault_delta_records"]:
        record_id = delta["record_id"]
        source_row = _source_row(record_id)
        relative = _safe_relative(delta["target_path"])
        target = staging_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if delta["action"] == "update":
            original = current_files[source_row]
            raw = original.read_text(encoding="utf-8")
            before_metadata, before_body = parse_markdown_with_frontmatter(raw)
            updated = _patch_frontmatter(raw, delta["after_values"])
            after_metadata, after_body = parse_markdown_with_frontmatter(updated)
            if before_body != after_body:
                raise StoreDataSyncPlanV2ExecutionError(f"body changed in staging: {record_id}")
            for key, value in before_metadata.items():
                if key not in delta["changed_fields"] and after_metadata.get(key) != value:
                    raise StoreDataSyncPlanV2ExecutionError(f"unknown frontmatter changed: {record_id}:{key}")
        elif delta["action"] == "create":
            updated = _create_parent_markdown(source[source_row], projection[record_id], desired[record_id])
            before_body = ""
        else:
            raise StoreDataSyncPlanV2ExecutionError(f"unexpected Managed action: {delta['action']}")
        _write_text_fsync(target, updated)
        metadata, body = parse_markdown_with_frontmatter(updated)
        if AUDIT_ONLY_FIELDS.intersection(metadata):
            raise StoreDataSyncPlanV2ExecutionError(f"audit-only field in staging: {record_id}")
        for key, value in projection[record_id].items():
            observed = metadata.get("can_quote_externally") if key == "can_external_reference" and key not in metadata else metadata.get(key)
            if not _materialized_values_equal(key, observed, value):
                raise StoreDataSyncPlanV2ExecutionError(f"Managed staging value mismatch: {record_id}:{key}")
        rows.append({
            "record_id": record_id,
            "action": delta["action"],
            "path": relative.as_posix(),
            "before_sha256": _sha256(current_files[source_row]) if source_row in current_files else None,
            "after_sha256": _sha256(target),
            "body_preserved": delta["action"] == "create" or body == before_body,
            "audit_only_occurrences": 0,
        })
    if len(rows) != 110 or sum(row["action"] == "create" for row in rows) != 4:
        raise StoreDataSyncPlanV2ExecutionError("Managed staging count mismatch")
    if {row for row in GOVERNANCE_ONLY_ROWS if row in _managed_parent_files(staging_root)}:
        raise StoreDataSyncPlanV2ExecutionError("governance-only Parent entered Managed staging")
    return {"valid": True, "count": len(rows), "rows": rows}


def _build_formal_staging(source_db, directory, managed_staging, validation, source):
    fd, name = tempfile.mkstemp(prefix=f".{Path(source_db).name}.{EXPECTED_PLAN_ID}.", suffix=".staging", dir=str(directory))
    os.close(fd)
    staging = Path(name)
    shutil.copy2(source_db, staging)
    before_schema = _schema_hash(staging)
    before_existing = _existing_parent_hashes(staging)
    before_non_parent = _non_parent_hash(staging)
    try:
        connection = sqlite3.connect(staging)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        source_by_id = {f"{value['source_sheet']}:r{int(value['source_row'])}": value for value in source.values()}
        for delta in validation["formal_sqlite_delta"]:
            if delta["action"] != "create":
                raise StoreDataSyncPlanV2ExecutionError("Formal SQLite delta contains a non-create action")
            record_id = delta["record_id"]
            row = _source_row(record_id)
            managed_delta = next(item for item in validation["managed_vault_delta_records"] if item["record_id"] == record_id)
            markdown_path = managed_staging / managed_delta["target_path"]
            metadata, body = parse_markdown_with_frontmatter(markdown_path.read_text(encoding="utf-8"))
            payload = dict(source_by_id[record_id])
            payload.update(delta["after_values"])
            payload["source_path"] = source_by_id[record_id]["source_path"]
            if AUDIT_ONLY_FIELDS.intersection(payload) or AUDIT_ONLY_FIELDS.intersection(metadata):
                raise StoreDataSyncPlanV2ExecutionError("audit-only field reached Formal SQLite payload")
            document_metadata = DocumentMetadata(**payload)
            relative = Path(managed_delta["target_path"]).as_posix()
            document = Document(
                id=stable_id("doc", f"MKA/{relative}"),
                metadata=document_metadata,
                content=body.strip(),
            )
            _insert_document(connection, document)
            _insert_chunks(connection, document)
        connection.commit()
        connection.close()
        _fsync_file(staging)
    except Exception:
        if 'connection' in locals():
            try:
                connection.rollback()
                connection.close()
            except Exception:
                pass
        staging.unlink(missing_ok=True)
        raise
    checks = _formal_validation(staging, before_existing, before_schema, before_non_parent)
    if not checks["valid"]:
        staging.unlink(missing_ok=True)
        raise StoreDataSyncPlanV2ExecutionError("Formal SQLite staging validation failed")
    return staging


def _insert_document(connection, document):
    metadata = document.metadata
    connection.execute(
        """
        INSERT INTO documents (
            id,title,source_type,content_category,parent_source_type,product_json,industry_json,
            topic_json,funnel_stage_json,status,publish_date,updated_date,source_path,canonical_url,
            language,author,asset_type,metadata_json,content
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            document.id, metadata.title, metadata.source_type, metadata.content_category,
            metadata.parent_source_type, _json(metadata.product), _json(metadata.industry),
            _json(metadata.topic), _json(metadata.funnel_stage), metadata.status,
            metadata.publish_date.isoformat(), metadata.updated_date.isoformat() if metadata.updated_date else None,
            metadata.source_path, metadata.canonical_url, metadata.language, metadata.author,
            metadata.asset_type, json.dumps(metadata.metadata_dict(), ensure_ascii=False), document.content,
        ),
    )


def _insert_chunks(connection, document):
    chunks = chunk_documents([document])
    for chunk in chunks:
        connection.execute(
            "INSERT INTO chunks(id,document_id,chunk_index,text,embedding_json,start_char,end_char) VALUES(?,?,?,?,?,?,?)",
            (chunk.id, chunk.document_id, chunk.chunk_index, chunk.text,
             json.dumps(embed_text(chunk.text)), chunk.start_char, chunk.end_char),
        )
        connection.execute(
            "INSERT INTO chunks_fts(chunk_id,title,body) VALUES(?,?,?)",
            (chunk.id, chunk.metadata.title, chunk.text),
        )


def _create_backup_bundle(target, paths, validation, created_at, require_git_ignored):
    target.parent.mkdir(parents=True, exist_ok=True)
    if require_git_ignored and not confirmation._git_ignored(paths["plan"].parents[2], target):
        raise StoreDataSyncPlanV2ExecutionError("Backup Bundle path must be Git ignored")
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent)))
    try:
        before_files = staging / "managed_vault_before_files"
        inventory = []
        current = _managed_parent_files(paths["managed"])
        for delta in validation["managed_vault_delta_records"]:
            if delta["action"] != "update":
                continue
            row = _source_row(delta["record_id"])
            source = current[row]
            relative = _safe_relative(delta["target_path"])
            destination = before_files / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            inventory.append({
                "record_id": delta["record_id"], "path": relative.as_posix(),
                "sha256": _sha256(destination), "byte_size": destination.stat().st_size,
            })
        formal_copy = staging / "formal_sqlite_before.sqlite"
        shutil.copy2(paths["formal"], formal_copy)
        _write_json(staging / "managed_vault_before_manifest.json", {
            "record_count": len(inventory), "files": inventory,
            "create_paths_previously_absent": [
                _safe_relative(row["target_path"]).as_posix()
                for row in validation["managed_vault_delta_records"] if row["action"] == "create"
            ],
        })
        _write_json(staging / "formal_sqlite_before_checksum.json", {
            "sha256": _sha256(formal_copy), "byte_size": formal_copy.stat().st_size,
        })
        _write_json(staging / "target_inventory_before.json", {
            "managed_vault_parent_count": 106, "formal_sqlite_parent_count": 105,
            "managed_vault_hash": confirmation._hash_path(paths["managed"]),
            "formal_sqlite_sha256": _sha256(paths["formal"]),
        })
        _write_text(staging / "rollback_instructions.md", "# Store Data Sync Rollback\n\nRestore 106 listed Managed Vault files, remove four create paths, then atomically restore `formal_sqlite_before.sqlite`.\n")
        roles = _bundle_file_inventory(staging, exclude={"backup_manifest.json"})
        identity = {
            "plan_id": EXPECTED_PLAN_ID, "created_at": created_at,
            "managed_vault_hash": confirmation._hash_path(paths["managed"]),
            "formal_sqlite_sha256": _sha256(paths["formal"]),
        }
        manifest = {
            "backup_schema_version": BACKUP_SCHEMA_VERSION,
            "backup_id": "store-data-sync-backup-" + _hash_json(identity)[:16],
            "plan_id": EXPECTED_PLAN_ID, "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
            "created_at": created_at, "managed_vault_update_count": 106,
            "managed_vault_create_count": 4, "formal_sqlite_sha256": _sha256(paths["formal"]),
            "files": roles,
        }
        manifest["root_backup_hash"] = _hash_json(manifest)
        _write_json(staging / "backup_manifest.json", manifest)
        validated = _validate_backup_bundle(staging)
        if target.exists():
            raise StoreDataSyncPlanV2ExecutionError("Backup Bundle appeared before atomic rename")
        os.replace(staging, target)
        final = _validate_backup_bundle(target)
        if final["root_backup_hash"] != validated["root_backup_hash"]:
            raise StoreDataSyncPlanV2ExecutionError("Backup root changed after atomic rename")
        _make_read_only(target)
        return final
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_backup_bundle(path):
    root = Path(path)
    manifest = _read_json(root / "backup_manifest.json")
    stored = manifest.get("root_backup_hash")
    if stored != _hash_json({key: value for key, value in manifest.items() if key != "root_backup_hash"}):
        raise StoreDataSyncPlanV2ExecutionError("Backup root hash mismatch")
    listed = set()
    for entry in manifest.get("files", []):
        relative = _safe_relative(entry["path"]).as_posix()
        if relative in listed:
            raise StoreDataSyncPlanV2ExecutionError("duplicate Backup filename")
        listed.add(relative)
        candidate = root / entry["path"]
        if not candidate.is_file() or _sha256(candidate) != entry["sha256"] or candidate.stat().st_size != entry["byte_size"]:
            raise StoreDataSyncPlanV2ExecutionError(f"Backup checksum mismatch: {entry['path']}")
    physical = {
        item.relative_to(root).as_posix() for item in root.rglob("*")
        if item.is_file() and item.name != "backup_manifest.json" and not item.name.startswith("._")
    }
    if physical != listed:
        raise StoreDataSyncPlanV2ExecutionError("Backup contains unlisted or missing files")
    managed = _read_json(root / "managed_vault_before_manifest.json")
    if managed.get("record_count") != 106 or len(managed.get("create_paths_previously_absent", [])) != 4:
        raise StoreDataSyncPlanV2ExecutionError("Backup Managed inventory mismatch")
    return {
        "valid": True, "backup_id": manifest["backup_id"],
        "root_backup_hash": stored, "path": str(root), "read_only_reopen": True,
    }


def _apply_managed_delta(target_root, staging_root, validation, journal, timestamp, backup):
    files = []
    for delta in validation["managed_vault_delta_records"]:
        relative = _safe_relative(delta["target_path"]).as_posix()
        target = target_root / relative
        staged = staging_root / relative
        if delta["action"] == "create" and target.exists():
            raise StoreDataSyncPlanV2ExecutionError(f"create target appeared during Execute: {relative}")
        if delta["action"] == "update":
            expected = delta["before_values"]
            if not target.is_file():
                raise StoreDataSyncPlanV2ExecutionError(f"update target disappeared: {relative}")
        before_sha = _sha256(target) if target.exists() else None
        _atomic_copy(staged, target)
        after_sha = _sha256(target)
        if after_sha != _sha256(staged):
            raise StoreDataSyncPlanV2ExecutionError(f"Managed write checksum mismatch: {relative}")
        files.append({
            "record_id": delta["record_id"], "action": delta["action"], "path": relative,
            "before_sha256": before_sha, "after_sha256": after_sha,
        })
        _write_journal(journal, "managed_applying", timestamp, backup, files)
    return {"valid": True, "create": 4, "update": 106, "files": files}


def _post_sync_validation(root, paths, validation, managed_stage, before):
    managed_files = _managed_parent_files(paths["managed"])
    if len(managed_files) != 110 or set(GOVERNANCE_ONLY_ROWS).intersection(managed_files):
        raise StoreDataSyncPlanV2ExecutionError("Managed Vault post-sync count or governance boundary mismatch")
    staged = {row["path"]: row for row in managed_stage["rows"]}
    audit_count = 0
    for relative, row in staged.items():
        target = paths["managed"] / relative
        if _sha256(target) != row["after_sha256"]:
            raise StoreDataSyncPlanV2ExecutionError(f"Managed post-sync checksum mismatch: {relative}")
        metadata, _ = parse_markdown_with_frontmatter(target.read_text(encoding="utf-8"))
        audit_count += len(AUDIT_ONLY_FIELDS.intersection(metadata))
    if audit_count:
        raise StoreDataSyncPlanV2ExecutionError("audit-only fields were written")
    formal = _formal_validation(
        paths["formal"], before["existing_parent_hashes"], before["formal_schema_hash"],
        before["formal_non_parent_hash"],
    )
    if not formal["valid"]:
        raise StoreDataSyncPlanV2ExecutionError("Formal SQLite post-sync validation failed")
    after_boundary = _formal_boundary_snapshot(root, paths, post_sync=True)
    if before["decision_store_sha256"] != after_boundary["decision_store_sha256"]:
        raise StoreDataSyncPlanV2ExecutionError("Decision Store changed during Execute")
    protected_keys = (
        "decision_store_size", "decision_store_sidecars", "renderer_sha256",
        "outside_managed_vault_hash", "plan_sha256", "confirmation_hash",
        "asset_input_hashes",
    )
    if any(before[key] != after_boundary[key] for key in protected_keys):
        raise StoreDataSyncPlanV2ExecutionError("formal boundary outside approved targets changed")
    special = _special_post_validation(paths["managed"], paths["formal"])
    return {
        "valid": True, "managed_parent_count": 110, "formal_parent_count": 109,
        "audit_only_write_occurrences": 0, "formal": formal,
        "decision_store_unchanged": True, "special": special,
        "asset_boundary": validation["asset_boundary"],
        "formal_sqlite_after_sha256": _sha256(paths["formal"]),
        "formal_sqlite_after_size": paths["formal"].stat().st_size,
        "managed_vault_after_hash": confirmation._hash_path(paths["managed"]),
        "sidecar_residue": len(_sqlite_sidecars(paths["formal"])),
    }


def _formal_validation(path, before_existing, before_schema, before_non_parent):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        rows = connection.execute(
            "SELECT json_extract(metadata_json,'$.source_row') FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case'"
        ).fetchall()
        chunk_count = connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE json_extract(metadata_json,'$.source_row') IN (7,12,32,122) AND json_extract(metadata_json,'$.record_type')='merchant_case')"
        ).fetchone()[0]
        fts_count = connection.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE json_extract(metadata_json,'$.source_row') IN (7,12,32,122) AND json_extract(metadata_json,'$.record_type')='merchant_case'))"
        ).fetchone()[0]
    after_existing = _existing_parent_hashes(path, exclude=CREATE_ROWS)
    created = {int(row[0]) for row in rows} - set(before_existing)
    return {
        "valid": all((
            integrity == "ok", foreign == 0, len(rows) == 109,
            created == CREATE_ROWS, after_existing == before_existing,
            _schema_hash(path) == before_schema, _non_parent_hash(path) == before_non_parent,
            chunk_count == 4, fts_count == 4, not _sqlite_sidecars(path),
        )),
        "integrity_check": integrity, "foreign_key_errors": foreign,
        "parent_count": len(rows), "created_rows": sorted(created),
        "existing_105_unchanged": after_existing == before_existing,
        "schema_unchanged": _schema_hash(path) == before_schema,
        "non_parent_rows_unchanged": _non_parent_hash(path) == before_non_parent,
        "new_chunk_rows": chunk_count, "new_fts_rows": fts_count,
        "sidecar_residue": len(_sqlite_sidecars(path)),
    }


def _rollback_rehearsal(workspace, managed, managed_staging, formal, formal_staging, validation):
    rehearsal = workspace / "rollback_rehearsal"
    rehearsal_managed = rehearsal / "MKA"
    shutil.copytree(managed, rehearsal_managed)
    original_hash = confirmation._hash_path(rehearsal_managed)
    for delta in validation["managed_vault_delta_records"]:
        relative = _safe_relative(delta["target_path"])
        _atomic_copy(managed_staging / relative, rehearsal_managed / relative)
    current = _managed_parent_files(managed)
    for delta in validation["managed_vault_delta_records"]:
        target = rehearsal_managed / _safe_relative(delta["target_path"])
        if delta["action"] == "create":
            target.unlink()
        else:
            _atomic_copy(current[_source_row(delta["record_id"])], target)
    managed_restored = confirmation._hash_path(rehearsal_managed) == original_hash
    rehearsal_sqlite = rehearsal / "content_index.sqlite"
    shutil.copy2(formal_staging, rehearsal_sqlite)
    _atomic_copy(formal, rehearsal_sqlite)
    sqlite_restored = _sha256(rehearsal_sqlite) == _sha256(formal)
    if not managed_restored or not sqlite_restored:
        raise StoreDataSyncPlanV2ExecutionError("rollback rehearsal failed")
    return {"valid": True, "managed_vault_restored": True, "formal_sqlite_restored": True}


def _rollback_targets(paths, validation, backup):
    backup_root = Path(backup["path"])
    manifest = _read_json(backup_root / "managed_vault_before_manifest.json")
    for relative in manifest["create_paths_previously_absent"]:
        target = paths["managed"] / _safe_relative(relative)
        if target.exists():
            target.unlink()
    for entry in manifest["files"]:
        relative = _safe_relative(entry["path"])
        _atomic_copy(backup_root / "managed_vault_before_files" / relative, paths["managed"] / relative)
    _atomic_copy(backup_root / "formal_sqlite_before.sqlite", paths["formal"])
    inventory = _read_json(backup_root / "target_inventory_before.json")
    valid = (
        confirmation._hash_path(paths["managed"]) == inventory["managed_vault_hash"]
        and _sha256(paths["formal"]) == inventory["formal_sqlite_sha256"]
        and not _sqlite_sidecars(paths["formal"])
    )
    if not valid:
        raise StoreDataSyncPlanV2ExecutionError("rollback checksums did not restore the before state")
    return {"valid": True, "managed_vault_restored": True, "formal_sqlite_restored": True}


def _create_execution_bundle(target, paths, validation, confirmation_bundle, executed_at,
                             backup, managed_write, post, rollback_rehearsal, require_git_ignored):
    target.parent.mkdir(parents=True, exist_ok=True)
    if require_git_ignored and not confirmation._git_ignored(paths["plan"].parents[2], target):
        raise StoreDataSyncPlanV2ExecutionError("Execution Bundle path must be Git ignored")
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent)))
    try:
        identity = {
            "plan_id": EXPECTED_PLAN_ID, "confirmation_id": EXPECTED_CONFIRMATION_ID,
            "executed_at": executed_at, "managed_after": post["managed_vault_after_hash"],
            "formal_after": post["formal_sqlite_after_sha256"],
        }
        execution_id = "store-data-sync-execution-" + _hash_json(identity)[:16]
        executor_branch, executor_commit = _git_identity(paths["plan"].parents[2])
        execution_payload = {
            "execution_schema_version": EXECUTION_SCHEMA_VERSION, "execution_id": execution_id,
            "plan_id": EXPECTED_PLAN_ID, "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
            "confirmation_id": EXPECTED_CONFIRMATION_ID,
            "confirmation_root_hash": EXPECTED_CONFIRMATION_ROOT_HASH,
            "materialization_contract_hash": EXPECTED_CONTRACT_HASH,
            "managed_vault_delta_hash": EXPECTED_MANAGED_DELTA_HASH,
            "formal_sqlite_delta_hash": EXPECTED_FORMAL_DELTA_HASH,
            "decision_store_sha256": EXPECTED_DECISION_STORE_SHA256,
            "decision_store_execution_root_hash": EXPECTED_DECISION_STORE_EXECUTION_ROOT,
            "target_paths": {"managed_vault": "obsidian_vault/MKA", "formal_sqlite": ".mka/content_index.sqlite"},
            "counts": {"managed_create": 4, "managed_update": 106, "managed_target": 110,
                       "formal_create": 4, "formal_update": 0, "formal_no_change": 105, "formal_target": 109},
            "before_checksums": validation["input_checksums"],
            "after_checksums": {"managed_vault": post["managed_vault_after_hash"],
                                "formal_sqlite": post["formal_sqlite_after_sha256"]},
            "backup_id": backup["backup_id"], "backup_root_hash": backup["root_backup_hash"],
            "executed_by": "Admin", "executed_at": executed_at,
            "source_branch": validation["source_branch"], "source_commit": validation["source_commit"],
            "executor_source_branch": executor_branch, "executor_source_commit": executor_commit,
            "code_version": EXECUTOR_CODE_VERSION, "status": "completed",
        }
        _write_json(staging / "execution.json", execution_payload)
        _write_json(staging / "execution_validation.json", post)
        shutil.copy2(paths["plan"], staging / "referenced_plan_manifest.json")
        shutil.copy2(paths["confirmation"] / "confirmation_manifest.json", staging / "referenced_confirmation_manifest.json")
        shutil.copy2(paths["confirmation"] / "field_materialization_contract.json", staging / "field_materialization_contract.json")
        _write_json(staging / "managed_vault_delta_applied.json", managed_write)
        _write_json(staging / "formal_sqlite_delta_applied.json", {
            "create": 4, "update": 0, "no_change": 105,
            "created_record_ids": [f"商家夥伴案例資料庫:r{row}" for row in sorted(CREATE_ROWS)],
            "formal_sqlite_after_sha256": post["formal_sqlite_after_sha256"],
        })
        _write_json(staging / "managed_vault_after_manifest.json", {
            "parent_count": 110, "sha256": post["managed_vault_after_hash"], "files": managed_write["files"],
        })
        _write_json(staging / "formal_sqlite_after_checksum.json", {
            "sha256": post["formal_sqlite_after_sha256"], "byte_size": post["formal_sqlite_after_size"],
            "integrity_check": post["formal"]["integrity_check"],
        })
        _write_json(staging / "backup_reference.json", backup)
        _write_json(staging / "rollback_validation.json", rollback_rehearsal)
        files = [
            {"filename": entry["path"], "sha256": entry["sha256"],
             "byte_size": entry["byte_size"], "required": entry["required"]}
            for entry in _bundle_file_inventory(staging, exclude={"execution_manifest.json"})
        ]
        manifest = {**execution_payload, "files": files}
        manifest["root_execution_hash"] = _hash_json(manifest)
        _write_json(staging / "execution_manifest.json", manifest)
        staged = validate_store_data_sync_execution_bundle(staging)
        if target.exists():
            raise StoreDataSyncPlanV2ExecutionError("Execution Bundle appeared before atomic rename")
        os.replace(staging, target)
        final = validate_store_data_sync_execution_bundle(target)
        if final["root_execution_hash"] != staged["root_execution_hash"]:
            raise StoreDataSyncPlanV2ExecutionError("Execution root changed after atomic rename")
        _make_read_only(target)
        return {**final, "path": str(target)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _formal_boundary_snapshot(root, paths, post_sync=False):
    return {
        "decision_store_sha256": _sha256(paths["decision_store"]),
        "decision_store_size": paths["decision_store"].stat().st_size,
        "decision_store_sidecars": sorted(_sqlite_sidecars(paths["decision_store"])),
        "renderer_sha256": _sha256(paths["renderer"]),
        "outside_managed_vault_hash": _hash_outside(paths["managed"].parent, paths["managed"]),
        "plan_sha256": _sha256(paths["plan"]),
        "confirmation_hash": confirmation._hash_path(paths["confirmation"]),
        "asset_input_hashes": {
            key: _sha256(_resolve(root, value)) for key, value in {
                "inventory": confirmation.DEFAULT_ASSET_INVENTORY,
                "eligible": confirmation.DEFAULT_ASSET_ELIGIBLE,
                "blocked": confirmation.DEFAULT_ASSET_BLOCKED,
                "urls": confirmation.DEFAULT_ASSET_URL_DECISIONS,
            }.items()
        },
        "formal_schema_hash": _schema_hash(paths["formal"]),
        "formal_non_parent_hash": _non_parent_hash(paths["formal"]),
        "existing_parent_hashes": _existing_parent_hashes(paths["formal"], exclude=CREATE_ROWS if post_sync else set()),
        "formal_sha256": _sha256(paths["formal"]),
        "managed_hash": confirmation._hash_path(paths["managed"]),
    }


def _special_post_validation(managed_root, formal_path):
    files = _managed_parent_files(managed_root)
    metadata = {row: parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))[0] for row, path in files.items()}
    with sqlite3.connect(formal_path) as connection:
        formal_rows = {int(row[0]) for row in connection.execute(
            "SELECT json_extract(metadata_json,'$.source_row') FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case'"
        )}
    checks = {
        "r30_governance_only": 30 not in files and 30 not in formal_rows,
        "r12_internal_only": metadata[12]["review_decision"] == "approve_internal_only" and metadata[12]["can_external_reference"] is False,
        "r122_partner_no_handle": metadata[122]["normalized_entity_type"] == "partner" and metadata[122]["merchant_handle_requirement"] == "not_required" and not metadata[122].get("merchant_handle"),
        "r32_aliases_parent_only": metadata[32]["search_aliases"] == ["SLP", "SHOPLINE Payments"],
        "r7_partner_no_handle": metadata[7]["normalized_entity_type"] == "partner" and metadata[7]["merchant_handle_requirement"] == "not_required" and not metadata[7].get("merchant_handle"),
        "r20_vault_only": 20 in files and 20 not in formal_rows,
    }
    if not all(checks.values()):
        raise StoreDataSyncPlanV2ExecutionError("special Parent post-sync validation failed")
    return [{"check": key, "status": "pass"} for key in checks]


def _create_parent_markdown(source, projection, desired):
    payload = dict(source)
    payload.update(projection)
    metadata = DocumentMetadata(**payload).metadata_dict()
    metadata.update(projection)
    metadata["managed_by"] = "marketing-knowledge-agent"
    for field in AUDIT_ONLY_FIELDS:
        metadata.pop(field, None)
    body = _parent_body(source, desired["current_review_decision"])
    return _render_markdown(metadata, body)


def _parent_body(source, decision):
    lines = [f"# {source['title']}", ""]
    if decision == "approve_internal_only":
        lines.extend(["> Internal-only. Do not quote externally.", ""])
    lines.extend([
        "## Content Assets", "", "| Asset | Title |", "| --- | --- |",
        f"| Article | {source.get('article_title') or ''} |",
        f"| Video | {source.get('video_title') or ''} |",
        f"| Podcast | {source.get('podcast_title') or ''} |",
        f"| News | {source.get('news_title') or ''} |", "", "## Notes", "", source.get("notes") or "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _patch_frontmatter(raw, updates):
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise StoreDataSyncPlanV2ExecutionError("Managed Parent lacks frontmatter")
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise StoreDataSyncPlanV2ExecutionError("Managed Parent frontmatter is unterminated")
    front = lines[1:end]
    blocks = []
    index = 0
    while index < len(front):
        line = front[index]
        if line[:1].isspace() or ":" not in line:
            raise StoreDataSyncPlanV2ExecutionError("unsupported Managed frontmatter structure")
        key = line.split(":", 1)[0].strip()
        finish = index + 1
        while finish < len(front) and (front[finish][:1].isspace() or ":" not in front[finish]):
            finish += 1
        blocks.append((key, front[index:finish]))
        index = finish
    seen = set()
    output = [lines[0]]
    for key, block in blocks:
        if key in updates:
            output.extend(line + "\n" for line in _yaml_lines(key, updates[key]))
            seen.add(key)
        else:
            output.extend(block)
    for key, value in updates.items():
        if key not in seen:
            output.extend(line + "\n" for line in _yaml_lines(key, value))
    output.extend(lines[end:])
    return "".join(output)


def _render_markdown(metadata, body):
    lines = ["---"]
    for key, value in metadata.items():
        lines.extend(_yaml_lines(key, value))
    lines.extend(["---", "", body.rstrip(), ""])
    return "\n".join(lines)


def _yaml_lines(key, value):
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        return [f"{key}:"] + [f"  - {_yaml_scalar(item)}" for item in value]
    if isinstance(value, dict):
        return [f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"]
    return [f"{key}: {_yaml_scalar(value)}"]


def _yaml_scalar(value):
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise StoreDataSyncPlanV2ExecutionError("frontmatter value contains a newline")
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _parent_source(path):
    rows = _read_json(path)
    result = {int(row["source_row"]): row for row in rows}
    if len(result) != 120:
        raise StoreDataSyncPlanV2ExecutionError("Parent source count mismatch")
    return result


def _managed_parent_files(root):
    result = {}
    for path in sorted(Path(root).rglob("*.md")):
        if path.name.startswith("._") or "_archived" in path.parts:
            continue
        try:
            metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, FrontmatterError) as exc:
            raise StoreDataSyncPlanV2ExecutionError(f"cannot parse Managed file {path}: {exc}") from exc
        if metadata.get("record_type") != "merchant_case":
            continue
        row = int(str(metadata["source_row"]).removeprefix("r"))
        if row in result:
            raise StoreDataSyncPlanV2ExecutionError(f"duplicate Managed Parent r{row}")
        result[row] = path
    return result


def _existing_parent_hashes(path, exclude=frozenset()):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case' ORDER BY id"
        ).fetchall()
        chunk_rows = {
            document_id: [list(row) for row in connection.execute(
                "SELECT * FROM chunks WHERE document_id=? ORDER BY id", (document_id,)
            )]
            for document_id in (row["id"] for row in rows)
        }
        fts_rows = {
            document_id: [list(row) for row in connection.execute(
                "SELECT rowid,* FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?) ORDER BY rowid",
                (document_id,),
            )]
            for document_id in (row["id"] for row in rows)
        }
    result = {}
    for row in rows:
        source_row = int(json.loads(row["metadata_json"])["source_row"])
        if source_row not in exclude:
            result[source_row] = _hash_json({
                "document": list(row), "chunks": chunk_rows[row["id"]], "fts": fts_rows[row["id"]],
            })
    return result


def _non_parent_hash(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        documents = [list(row) for row in connection.execute(
            "SELECT * FROM documents WHERE json_extract(metadata_json,'$.record_type')!='merchant_case' ORDER BY id"
        )]
        chunks = [list(row) for row in connection.execute(
            "SELECT * FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE json_extract(metadata_json,'$.record_type')!='merchant_case') ORDER BY id"
        )]
        fts = [list(row) for row in connection.execute(
            "SELECT rowid,* FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE json_extract(metadata_json,'$.record_type')!='merchant_case')) ORDER BY rowid"
        )]
    return _hash_json({"documents": documents, "chunks": chunks, "fts": fts})


def _schema_hash(path):
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall()
    return _hash_json(rows)


def _formal_parent_count(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM documents WHERE json_extract(metadata_json,'$.record_type')='merchant_case'"
        ).fetchone()[0]


def _hash_outside(parent, excluded):
    digest = hashlib.sha256()
    for child in sorted(item for item in Path(parent).rglob("*") if item.is_file()):
        relative = child.relative_to(parent)
        if any(part.startswith(f".{EXPECTED_PLAN_ID}.staging-") for part in relative.parts):
            continue
        try:
            child.relative_to(excluded)
            continue
        except ValueError:
            pass
        digest.update(relative.as_posix().encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _bundle_file_inventory(root, exclude):
    rows = []
    for path in sorted(item for item in Path(root).rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in exclude or path.name.startswith("._"):
            continue
        rows.append({"path": relative, "sha256": _sha256(path), "byte_size": path.stat().st_size, "required": True})
    return rows


def _validate_target_paths(root, paths, allow_test):
    if allow_test:
        return
    expected = {
        "managed": (root / DEFAULT_MANAGED_VAULT).resolve(),
        "formal": (root / DEFAULT_FORMAL_SQLITE).resolve(),
        "decision_store": (root / DEFAULT_DECISION_STORE).resolve(),
        "confirmation": (root / DEFAULT_CONFIRMATION_PATH).resolve(),
        "backup": (root / DEFAULT_BACKUP_PATH).resolve(),
        "execution": (root / DEFAULT_EXECUTION_PATH).resolve(),
    }
    for key, value in expected.items():
        if paths[key] != value:
            raise StoreDataSyncPlanV2ExecutionError(f"exact {key} target path is required")


def _require_exact_authority(plan_id, manifest_hash, confirmation_id, confirmation_root_hash):
    if plan_id != EXPECTED_PLAN_ID:
        raise StoreDataSyncPlanV2ExecutionError("exact PLAN_ID is required; old Plan is rejected")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise StoreDataSyncPlanV2ExecutionError("exact Manifest Hash is required")
    if confirmation_id != EXPECTED_CONFIRMATION_ID:
        raise StoreDataSyncPlanV2ExecutionError("exact Confirmation ID is required")
    if confirmation_root_hash != EXPECTED_CONFIRMATION_ROOT_HASH:
        raise StoreDataSyncPlanV2ExecutionError("exact Confirmation Root Hash is required")


def _write_journal(path, state, timestamp, backup, files, execution=None):
    payload = {
        "plan_id": EXPECTED_PLAN_ID, "state": state, "updated_at": timestamp,
        "backup": backup, "applied_files": files,
    }
    if execution:
        payload["execution"] = execution
    _write_json_atomic(path, payload)


def _summary(timestamp, paths, before, validation, backup, execution, post, rollback):
    return {
        "conclusion": "A. Store data synchronized and validated",
        "plan_id": EXPECTED_PLAN_ID, "confirmation_id": EXPECTED_CONFIRMATION_ID,
        "executed_at": timestamp,
        "managed_vault_counts": {"before": 106, "after": 110, "create": 4, "update": 106},
        "formal_sqlite_counts": {"before": 105, "after": 109, "create": 4, "update": 0, "no_change": 105},
        "decision_store_sha256_before": before["decision_store_sha256"],
        "decision_store_sha256_after": _sha256(paths["decision_store"]),
        "decision_store_unchanged": before["decision_store_sha256"] == _sha256(paths["decision_store"]),
        "materialization_contract_hash": EXPECTED_CONTRACT_HASH,
        "audit_only_write_occurrences": post["audit_only_write_occurrences"],
        "backup_id": backup["backup_id"], "backup_root_hash": backup["root_backup_hash"],
        "execution_id": execution["execution_id"], "root_execution_hash": execution["root_execution_hash"],
        "execution_bundle_path": execution["path"],
        "formal_sqlite_sha256_after": post["formal_sqlite_after_sha256"],
        "formal_sqlite_byte_size_after": post["formal_sqlite_after_size"],
        "sqlite_integrity_check": post["formal"]["integrity_check"],
        "sqlite_foreign_key_errors": post["formal"]["foreign_key_errors"],
        "existing_105_unchanged": post["formal"]["existing_105_unchanged"],
        "governance_only_count": 10, "r20_vault_only": True,
        "asset_boundary": validation["asset_boundary"],
        "rollback_validation": rollback,
        "production_search_alias_activated": False,
        "slack_api_called": False,
        "validation_errors": 0, "validation_warnings": 0,
        "report_dir": str(paths["reports"]),
    }


def _write_reports(output, summary, preflight, validation, backup, managed_stage,
                   managed_write, post, rollback, execution):
    output.mkdir(parents=True, exist_ok=True)
    for child in output.iterdir():
        if child.is_file() and child.name != "execution_journal.json" and not child.name.startswith("._"):
            child.unlink()
    _write_text(output / "store_data_sync_execution_summary.md", "\n".join([
        "# Store Data Sync Plan V2 Execution", "",
        f"- Conclusion: **{summary['conclusion']}**",
        f"- PLAN_ID: `{EXPECTED_PLAN_ID}`",
        f"- Confirmation ID: `{EXPECTED_CONFIRMATION_ID}`",
        f"- Execution ID: `{summary['execution_id']}`",
        f"- Execution Root Hash: `{summary['root_execution_hash']}`",
        "- Production Search Alias activated: `false`", "- Slack API called: `false`", "",
    ]))
    _write_csv(output / "execution_preflight_validation.csv", preflight)
    _write_csv(output / "plan_confirmation_validation.csv", [{
        "plan_id": EXPECTED_PLAN_ID, "manifest_hash": EXPECTED_MANIFEST_HASH,
        "confirmation_id": EXPECTED_CONFIRMATION_ID, "confirmation_root_hash": EXPECTED_CONFIRMATION_ROOT_HASH,
        "status": "pass",
    }])
    _write_csv(output / "materialization_contract_validation.csv", validation["materialization_contract"])
    _write_csv(output / "backup_bundle_validation.csv", [backup])
    _write_csv(output / "managed_vault_staging_validation.csv", managed_stage["rows"])
    _write_csv(output / "managed_vault_write_validation.csv", managed_write["files"])
    _write_csv(output / "managed_vault_post_sync_validation.csv", [{"parent_count": 110, "audit_only_occurrences": 0, "status": "pass"}])
    _write_csv(output / "formal_sqlite_staging_validation.csv", [post["formal"]])
    _write_csv(output / "formal_sqlite_write_validation.csv", [{"create": 4, "update": 0, "no_change": 105, "new_chunk_rows": 4, "new_fts_rows": 4}])
    _write_csv(output / "formal_sqlite_existing_rows_unchanged.csv", [{"existing_rows": 105, "unchanged": post["formal"]["existing_105_unchanged"]}])
    _write_csv(output / "formal_sqlite_post_sync_validation.csv", [post["formal"]])
    _write_csv(output / "governance_only_post_sync_validation.csv", [{"source_row": row, "in_managed": False, "in_formal": False, "status": "pass"} for row in sorted(GOVERNANCE_ONLY_ROWS)])
    _write_csv(output / "r20_vault_only_post_sync_validation.csv", [{"source_row": 20, "in_managed": True, "in_formal": False, "status": "pass"}])
    _write_csv(output / "four_create_records_post_sync_validation.csv", [{"source_row": row, "managed_created": True, "formal_created": True, "status": "pass"} for row in sorted(CREATE_ROWS)])
    _write_csv(output / "special_record_post_sync_validation.csv", post["special"])
    _write_csv(output / "asset_url_boundary_post_sync_validation.csv", [post["asset_boundary"]])
    _write_csv(output / "decision_store_unchanged_validation.csv", [{"before": summary["decision_store_sha256_before"], "after": summary["decision_store_sha256_after"], "unchanged": True}])
    _write_csv(output / "rollback_rehearsal_validation.csv", [rollback])
    _write_csv(output / "execution_bundle_validation.csv", [execution])
    _write_csv(output / "formal_system_boundary_validation.csv", [{"only_managed_vault_and_formal_sqlite_modified": True, "slack_renderer_modified": False, "decision_store_modified": False}])
    _write_csv(output / "production_search_not_activated_validation.csv", [{"production_search_alias_activated": False, "full_index_rebuilt": False, "slack_api_called": False}])
    _write_csv(output / "execution_errors.csv", [], ("error",))
    _write_csv(output / "execution_warnings.csv", [], ("warning",))


def _write_failure_reports(output, error, rollback_error=None, rollback=None):
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "execution_errors.csv", [{"error": str(error), "rollback_error": str(rollback_error or ""), "rolled_back": bool(rollback and rollback.get("valid"))}])
    _write_csv(output / "execution_warnings.csv", [], ("warning",))


def _atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copy2(source, temporary)
        _fsync_file(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_replace_file(source, target):
    _fsync_file(source)
    os.replace(source, target)
    _fsync_directory(target.parent)


def _write_text_fsync(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _fsync_file(path):
    with Path(path).open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _make_read_only(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    Path(root).chmod(0o555)


def _sqlite_sidecars(path):
    return {item.name for item in Path(path).parent.glob(f"{Path(path).name}-*") if item.is_file()}


def _source_row(record_id):
    value = str(record_id).rsplit(":r", 1)[-1]
    if not value.isdigit():
        raise StoreDataSyncPlanV2ExecutionError(f"invalid record_id: {record_id}")
    return int(value)


def _normalized(value):
    return None if value in (None, "") else value


def _materialized_values_equal(key, observed, expected):
    if key == "source_row":
        return str(observed).removeprefix("r") == str(expected).removeprefix("r")
    return _normalized(observed) == _normalized(expected)


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise StoreDataSyncPlanV2ExecutionError("timestamp must be ISO 8601") from exc
    if result.tzinfo is None:
        raise StoreDataSyncPlanV2ExecutionError("timestamp must include timezone")
    return result


def _safe_name(value):
    name = Path(str(value)).name
    if not name or name != str(value) or name.startswith("."):
        raise StoreDataSyncPlanV2ExecutionError("unsafe Execution filename")
    return name


def _git_identity(root):
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return branch, commit


def _safe_relative(value):
    path = Path(str(value))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise StoreDataSyncPlanV2ExecutionError("unsafe target-relative path")
    return path


def _resolve(root, path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path, value):
    Path(path).write_text(value, encoding="utf-8")


def _write_csv(path, rows, default_fields=()):
    rows = list(rows)
    fields = list(rows[0]) if rows else list(default_fields)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _json(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
