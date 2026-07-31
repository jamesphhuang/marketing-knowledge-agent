from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

from .governance_decision_store_schema_v2_confirmation import (
    CANONICAL_SQL_HASH,
    EXPECTED_MANIFEST_HASH,
    EXPECTED_PLAN_ID,
    EXPECTED_SCHEMA_HASH,
    _hash_path,
    _insert_event,
    validate_governance_decision_store_schema_v2_confirmation,
)
from .governance_decision_store_schema_v2_execution import (
    CONFIRMATION_ID,
    CONFIRMATION_ROOT_HASH,
    DEFAULT_CONFIRMATION_BUNDLE,
    DEFAULT_EXECUTION_BUNDLE,
    DEFAULT_FORMAL_TARGET,
    validate_governance_decision_store_schema_v2_execution_bundle,
)
from .governance_decision_store_schema_v2_plan import (
    BUNDLE_ID,
    BUNDLE_ROOT_HASH,
    CANONICAL_SCHEMA_V2_SQL,
    SCHEMA_NAME,
    SCHEMA_VERSION,
)
from .parent_authority_import_bundle import (
    DEFAULT_BUNDLE_PATH as DEFAULT_PARENT_AUTHORITY_BUNDLE,
    validate_parent_authority_import_bundle,
)


EXPECTED_DATABASE_SHA256 = "df82a18223f76fc47b1e438c7c0d36395f22f6d6aa121cad04bd37af904f7dc9"
EXPECTED_DATABASE_SIZE = 385024
EXPECTED_EXECUTION_ID = "decision-store-schema-v2-execution-ef6bf52051228295"
EXPECTED_EXECUTION_ROOT_HASH = "2813a7e9989c7f6b878d903c36e632e4ea38a0c8b4254fa2079a4900f57b58b7"
EXPECTED_EXECUTED_AT = "2026-07-20T16:20:20+08:00"
EXPECTED_CHAIN_HEAD = "bb0b4d007f7d8477333f08af8ead7d611540e35834fe1e11a2a64be51ed20b31"
EXPECTED_CHAIN_TAIL = "60daf915d898f074c6f4f6bb9535e36fc45e1ecb1361fdbd38255767b37c6243"
EXPECTED_EXTERNAL_EXECUTION_REFERENCE = (
    "7b754a242374243ceeefaec74e827a32817920641533303c100ae2c454994062"
)
EXPECTED_URL_MANIFEST_HASH = "4d9701eb039056a3400fe034fffae4529e8e198f9d83e281d9596af849bca5b9"
VALIDATOR_CODE_VERSION = "existing-governance-decision-store-independent-validation-v1"
DEFAULT_PLAN_MANIFEST = Path(
    "reports/governance_decision_store_schema_v2_plan/schema_v2_plan_manifest.json"
)
DEFAULT_CANONICAL_SCHEMA = Path(
    "reports/governance_decision_store_schema_v2_plan/canonical_schema_v2.sql"
)
DEFAULT_REPORT_DIR = Path("reports/existing_governance_decision_store_validation")
REPORT_FILENAMES = (
    "existing_store_validation_summary.md",
    "database_file_integrity_validation.csv",
    "execution_bundle_revalidation.csv",
    "authority_chain_validation.csv",
    "schema_v2_existing_store_validation.csv",
    "schema_metadata_validation.csv",
    "execution_metadata_validation.csv",
    "event_count_validation.csv",
    "event_identity_validation.csv",
    "event_hash_chain_validation.csv",
    "current_parent_state_validation.csv",
    "special_parent_decision_validation.csv",
    "asset_eligibility_validation.csv",
    "search_alias_validation.csv",
    "asset_url_reference_validation.csv",
    "sqlite_readonly_health_validation.csv",
    "temporary_copy_trigger_validation.csv",
    "formal_system_unchanged_validation.csv",
    "parent_sync_readiness_assessment.md",
    "existing_store_validation_errors.csv",
    "existing_store_validation_warnings.csv",
    "next_parent_sync_plan_prerequisites.md",
)


class ExistingGovernanceDecisionStoreValidationError(RuntimeError):
    pass


def validate_existing_governance_decision_store(
    *,
    repo_root: Path,
    database_path: Path = DEFAULT_FORMAL_TARGET,
    execution_bundle_path: Path = DEFAULT_EXECUTION_BUNDLE,
    parent_authority_bundle_path: Path = DEFAULT_PARENT_AUTHORITY_BUNDLE,
    confirmation_bundle_path: Path = DEFAULT_CONFIRMATION_BUNDLE,
    plan_manifest_path: Path = DEFAULT_PLAN_MANIFEST,
    canonical_schema_path: Path = DEFAULT_CANONICAL_SCHEMA,
    report_dir: Path = DEFAULT_REPORT_DIR,
    temporary_root: Optional[Path] = None,
) -> dict:
    root = Path(repo_root).resolve()
    database = _resolve(root, database_path)
    execution_bundle = _resolve(root, execution_bundle_path)
    parent_bundle = _resolve(root, parent_authority_bundle_path)
    confirmation_bundle = _resolve(root, confirmation_bundle_path)
    plan_manifest_file = _resolve(root, plan_manifest_path)
    canonical_schema_file = _resolve(root, canonical_schema_path)
    output = _resolve(root, report_dir)
    required = {
        "formal database": database,
        "Execution Bundle": execution_bundle,
        "Parent Authority Bundle": parent_bundle,
        "Schema V2 Confirmation Bundle": confirmation_bundle,
        "Schema V2 Plan manifest": plan_manifest_file,
        "Canonical Schema V2 SQL": canonical_schema_file,
    }
    for label, path in required.items():
        if not path.exists():
            raise ExistingGovernanceDecisionStoreValidationError(f"required {label} is missing: {path}")

    protected = _protected_paths(root, execution_bundle, parent_bundle, confirmation_bundle)
    protected_before = {str(path): _hash_path(path) for path in protected}
    file_before = _database_file_state(database)
    _require_expected_database(file_before)
    sidecars_before = _sidecars(database)
    if sidecars_before:
        raise ExistingGovernanceDecisionStoreValidationError(
            f"formal database sidecar residue exists: {sidecars_before}"
        )

    parent_validation = validate_parent_authority_import_bundle(parent_bundle)
    confirmation_validation = validate_governance_decision_store_schema_v2_confirmation(
        confirmation_bundle
    )
    execution_validation = validate_governance_decision_store_schema_v2_execution_bundle(
        execution_bundle, database
    )
    plan_manifest = _read_json(plan_manifest_file)
    execution_manifest = _read_json(execution_bundle / "execution_manifest.json")
    confirmation_manifest = _read_json(confirmation_bundle / "confirmation_manifest.json")
    parent_manifest = _read_json(parent_bundle / "bundle_manifest.json")
    authority_chain = _validate_authority_chain(
        plan_manifest_file,
        plan_manifest,
        execution_bundle,
        execution_manifest,
        confirmation_bundle,
        confirmation_manifest,
        parent_bundle,
        parent_manifest,
        parent_validation,
        confirmation_validation,
        execution_validation,
    )

    temp_parent = Path(temporary_root) if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-existing-store-validation-",
        dir=str(temp_parent) if temp_parent else None,
    ) as temporary_name:
        temporary = Path(temporary_name)
        schema_validation = _compare_schema_objects(
            database, temporary / "canonical-schema.sqlite", canonical_schema_file
        )
        with _readonly_connection(database) as connection:
            schema_version_pragma_before = connection.execute("PRAGMA schema_version").fetchone()[0]
            user_version_pragma_before = connection.execute("PRAGMA user_version").fetchone()[0]
            integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            schema_metadata = _validate_schema_metadata(connection)
            execution_metadata = _validate_execution_metadata(connection, execution_manifest)
            event_validation = _validate_events(connection)
            hash_chain = _recalculate_hash_chain(connection)
            parent_state = _validate_parent_state(connection)
            special_rows = _validate_special_parents(connection)
            asset_validation = _validate_assets(connection)
            alias_validation = _validate_aliases(connection)
            url_reference = _validate_url_reference(connection, root)
            formal_query_only = connection.execute("PRAGMA query_only").fetchone()[0] == 1
            schema_version_pragma_after = connection.execute("PRAGMA schema_version").fetchone()[0]
            user_version_pragma_after = connection.execute("PRAGMA user_version").fetchone()[0]
        temporary_copy = _validate_temporary_copy(database, temporary / "destructive")
    temporary_copy["temporary_files_cleaned"] = not Path(temporary_name).exists()

    _require_validation_success(
        authority_chain=authority_chain,
        schema_validation=schema_validation,
        schema_metadata=schema_metadata,
        execution_metadata=execution_metadata,
        event_validation=event_validation,
        hash_chain=hash_chain,
        parent_state=parent_state,
        special_rows=special_rows,
        asset_validation=asset_validation,
        alias_validation=alias_validation,
        url_reference=url_reference,
        integrity_check=integrity_check,
        foreign_key_errors=foreign_key_errors,
        formal_query_only=formal_query_only,
        temporary_copy=temporary_copy,
    )

    file_after = _database_file_state(database)
    sidecars_after = _sidecars(database)
    protected_after = {str(path): _hash_path(path) for path in protected}
    database_unchanged = file_before == file_after and not sidecars_after
    protected_unchanged = protected_before == protected_after
    if not database_unchanged or not protected_unchanged:
        raise ExistingGovernanceDecisionStoreValidationError(
            "formal database or protected authority inputs changed during read-only validation"
        )
    result = {
        "conclusion": "A. Existing Decision Store independently validated",
        "database_path": str(database),
        "database_sha256_before": file_before["sha256"],
        "database_sha256_after": file_after["sha256"],
        "database_size_before": file_before["byte_size"],
        "database_size_after": file_after["byte_size"],
        "database_mtime_ns_before": file_before["mtime_ns"],
        "database_mtime_ns_after": file_after["mtime_ns"],
        "mtime_unchanged": file_before["mtime_ns"] == file_after["mtime_ns"],
        "sidecars_before": sidecars_before,
        "sidecars_after": sidecars_after,
        "formal_database_unchanged": database_unchanged,
        "protected_inputs_unchanged": protected_unchanged,
        "execution_bundle": execution_validation,
        "authority_chain": authority_chain,
        "schema_validation": schema_validation,
        "schema_metadata_validation": schema_metadata,
        "execution_metadata_validation": execution_metadata,
        "event_validation": event_validation,
        "hash_chain_validation": hash_chain,
        "parent_state_validation": parent_state,
        "special_parent_rows": special_rows,
        "asset_validation": asset_validation,
        "alias_validation": alias_validation,
        "url_reference_validation": url_reference,
        "integrity_check": integrity_check,
        "foreign_key_errors": foreign_key_errors,
        "formal_query_only": formal_query_only,
        "schema_version_pragma_unchanged": (
            schema_version_pragma_before == schema_version_pragma_after
        ),
        "user_version_pragma_unchanged": user_version_pragma_before == user_version_pragma_after,
        "temporary_copy_validation": temporary_copy,
        "event_count": event_validation["counts"]["total"],
        "current_parent_state_count": parent_state["row_count"],
        "authority_gap": parent_state["authority_gap"],
        "parent_sync_readiness": {
            "syncable": [
                "商家夥伴案例資料庫:r12", "商家夥伴案例資料庫:r122",
                "商家夥伴案例資料庫:r32", "商家夥伴案例資料庫:r7",
            ],
            "not_syncable": ["商家夥伴案例資料庫:r30"],
            "other_authoritative_parents": 115,
            "requires_full_projection_boundary": True,
            "partial_authoritative_state_prohibited": True,
        },
        "validator_code_version": VALIDATOR_CODE_VERSION,
    }
    fingerprint_payload = {
        key: value for key, value in result.items()
        if key not in {"database_path", "validation_fingerprint"}
    }
    result["validation_fingerprint"] = _sha256_bytes(_canonical_json(fingerprint_payload))
    _write_reports(output, result)
    return result


class _readonly_connection:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.connection = None

    def __enter__(self):
        self.connection = sqlite3.connect(
            f"file:{self.path}?mode=ro&immutable=1", uri=True
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        self.connection.close()


def _validate_authority_chain(
    plan_path,
    plan,
    execution_bundle,
    execution,
    confirmation_bundle,
    confirmation,
    parent_bundle,
    parent,
    parent_validation,
    confirmation_validation,
    execution_validation,
):
    plan_hash = _manifest_hash(plan, "manifest_hash")
    confirmation_hash = _manifest_hash(confirmation, "root_confirmation_hash")
    execution_hash = _manifest_hash(execution, "root_execution_hash")
    parent_hash = _manifest_hash(parent, "root_manifest_hash")
    checks = {
        "parent_bundle_validator_passed": parent_validation["root_manifest_hash"] == BUNDLE_ROOT_HASH,
        "parent_bundle_root_hash_valid": parent_hash == BUNDLE_ROOT_HASH,
        "plan_manifest_hash_valid": plan_hash == EXPECTED_MANIFEST_HASH,
        "plan_references_parent_bundle": plan.get("bundle_root_hash") == BUNDLE_ROOT_HASH,
        "confirmation_validator_passed": (
            confirmation_validation["root_confirmation_hash"] == CONFIRMATION_ROOT_HASH
        ),
        "confirmation_root_hash_valid": confirmation_hash == CONFIRMATION_ROOT_HASH,
        "confirmation_references_plan": (
            confirmation.get("plan_id") == EXPECTED_PLAN_ID
            and confirmation.get("plan_manifest_hash") == EXPECTED_MANIFEST_HASH
        ),
        "confirmation_references_parent_bundle": (
            confirmation.get("bundle_root_hash") == BUNDLE_ROOT_HASH
        ),
        "execution_validator_passed": (
            execution_validation["root_execution_hash"] == EXPECTED_EXECUTION_ROOT_HASH
        ),
        "execution_root_hash_valid": execution_hash == EXPECTED_EXECUTION_ROOT_HASH,
        "execution_references_plan": (
            execution.get("plan_id") == EXPECTED_PLAN_ID
            and execution.get("plan_manifest_hash") == EXPECTED_MANIFEST_HASH
        ),
        "execution_references_confirmation": (
            execution.get("confirmation_id") == CONFIRMATION_ID
            and execution.get("confirmation_root_hash") == CONFIRMATION_ROOT_HASH
        ),
        "execution_references_parent_bundle": execution.get("bundle_root_hash") == BUNDLE_ROOT_HASH,
        "execution_id_valid": execution.get("execution_id") == EXPECTED_EXECUTION_ID,
        "executed_at_valid": execution.get("executed_at") == EXPECTED_EXECUTED_AT,
        "executed_by_admin": execution.get("executed_by") == "Admin",
        "target_path_valid": execution.get("target_path") == str(DEFAULT_FORMAL_TARGET),
        "execution_plan_copy_matches": (
            _sha256(execution_bundle / "referenced_plan_manifest.json") == _sha256(plan_path)
        ),
        "execution_confirmation_copy_matches": (
            _sha256(execution_bundle / "referenced_confirmation_manifest.json")
            == _sha256(confirmation_bundle / "confirmation_manifest.json")
        ),
        "execution_parent_bundle_copy_matches": (
            _sha256(execution_bundle / "referenced_bundle_manifest.json")
            == _sha256(parent_bundle / "bundle_manifest.json")
        ),
    }
    return checks


def _compare_schema_objects(
    database_path: Path,
    expected_path: Path,
    canonical_schema_path: Optional[Path] = None,
) -> dict:
    expected = Path(expected_path)
    if expected.exists():
        raise ExistingGovernanceDecisionStoreValidationError("canonical schema fixture path exists")
    sql = (
        Path(canonical_schema_path).read_text(encoding="utf-8")
        if canonical_schema_path
        else CANONICAL_SCHEMA_V2_SQL
    )
    sql_hash = _sha256_bytes(sql.encode("utf-8"))
    if sql != CANONICAL_SCHEMA_V2_SQL or sql_hash != CANONICAL_SQL_HASH:
        raise ExistingGovernanceDecisionStoreValidationError("Canonical SQL content or hash mismatch")
    connection = sqlite3.connect(expected)
    try:
        connection.executescript(sql)
        connection.commit()
    finally:
        connection.close()
    with _readonly_connection(Path(database_path)) as actual_connection:
        actual_rows = _schema_rows(actual_connection)
        actual_columns = _table_columns(actual_connection)
        actual_indexes = _index_contract(actual_connection)
    with _readonly_connection(expected) as expected_connection:
        expected_rows = _schema_rows(expected_connection)
        expected_columns = _table_columns(expected_connection)
        expected_indexes = _index_contract(expected_connection)
    actual_keys = set(actual_rows)
    expected_keys = set(expected_rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "schema_hash_valid": EXPECTED_SCHEMA_HASH == (
            "c8431d66857a8695b74c4f6480ad637635a45331a8ece7af961f558f2414a9a7"
        ),
        "canonical_sql_hash": sql_hash,
        "canonical_sql_hash_valid": sql_hash == CANONICAL_SQL_HASH,
        "objects_match_canonical": (
            actual_rows == expected_rows
            and actual_columns == expected_columns
            and actual_indexes == expected_indexes
        ),
        "unexpected_objects": sorted(actual_keys - expected_keys),
        "missing_objects": sorted(expected_keys - actual_keys),
        "object_sql_mismatches": sorted(
            key for key in actual_keys & expected_keys if actual_rows[key] != expected_rows[key]
        ),
        "columns_match_canonical": actual_columns == expected_columns,
        "indexes_match_canonical": actual_indexes == expected_indexes,
        "actual_object_count": len(actual_rows),
        "expected_object_count": len(expected_rows),
        "confirmation_columns_present": {
            "source_confirmation_id", "source_confirmation_root_hash"
        }.issubset({row[1] for row in actual_columns["decision_events"]}),
    }


def _validate_schema_metadata(connection):
    rows = connection.execute("SELECT * FROM schema_metadata").fetchall()
    row = rows[0] if len(rows) == 1 else None
    checks = {
        "row_count": len(rows) == 1,
        "schema_name": bool(row and row["schema_name"] == SCHEMA_NAME),
        "schema_version": bool(row and row["schema_version"] == 2),
        "schema_hash": bool(row and row["schema_hash"] == EXPECTED_SCHEMA_HASH),
        "schema_sql_hash": bool(row and row["schema_sql_hash"] == CANONICAL_SQL_HASH),
        "source_plan_id": bool(row and row["source_plan_id"] == EXPECTED_PLAN_ID),
        "source_plan_manifest_hash": bool(
            row and row["source_plan_manifest_hash"] == EXPECTED_MANIFEST_HASH
        ),
        "migration_type": bool(row and row["migration_type"] == "initial_creation"),
        "compatibility_status": bool(
            row and row["compatibility_status"] == "schema_v2_execute_contract_validated"
        ),
        "code_version_traceable": bool(row and row["code_version"]),
    }
    return {"valid": all(checks.values()), **checks, "decision_event_count_impact": 0}


def _validate_execution_metadata(connection, execution_manifest):
    rows = connection.execute("SELECT * FROM execution_metadata").fetchall()
    row = rows[0] if len(rows) == 1 else None
    expected = {
        "execution_id": EXPECTED_EXECUTION_ID,
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "confirmation_id": CONFIRMATION_ID,
        "confirmation_root_hash": CONFIRMATION_ROOT_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "target_path": str(DEFAULT_FORMAL_TARGET),
        "executed_by": "Admin",
        "executed_at": EXPECTED_EXECUTED_AT,
        "expected_event_count": 162,
        "actual_event_count": 162,
        "expected_parent_current_state_count": 120,
        "actual_parent_current_state_count": 120,
        "authority_gap": 0,
        "schema_version": 2,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "event_chain_head": EXPECTED_CHAIN_HEAD,
        "event_chain_tail": EXPECTED_CHAIN_TAIL,
        "execution_manifest_hash": EXPECTED_EXTERNAL_EXECUTION_REFERENCE,
        "status": "completed",
    }
    checks = {key: bool(row and row[key] == value) for key, value in expected.items()}
    checks.update({
        "row_count": len(rows) == 1,
        "database_sha256_is_null": bool(row and row["database_sha256"] is None),
        "external_database_sha_matches": execution_manifest.get("database_sha256") == EXPECTED_DATABASE_SHA256,
        "external_database_size_matches": execution_manifest.get("database_byte_size") == EXPECTED_DATABASE_SIZE,
        "self_reference_absent": bool(
            row and row["database_sha256"] is None
            and row["execution_manifest_hash"] == execution_manifest.get(
                "external_execution_manifest_reference"
            )
        ),
    })
    return {"valid": all(checks.values()), **checks}


def _validate_events(connection):
    total = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
    provenance = dict(connection.execute(
        "SELECT provenance,COUNT(*) FROM decision_events GROUP BY provenance"
    ).fetchall())
    counts = {
        "legacy_import": provenance.get("legacy_import", 0),
        "batch_parent_approval": provenance.get("batch_approval", 0),
        "resolution_parent_supersede": connection.execute(
            "SELECT COUNT(*) FROM decision_events "
            "WHERE event_type='parent_review_decision' AND action='supersede'"
        ).fetchone()[0],
        "asset_eligibility": _event_type_count(connection, "asset_eligibility"),
        "search_alias": _event_type_count(connection, "search_alias"),
        "entity_metadata": _event_type_count(connection, "entity_metadata"),
        "asset_url_manifest_reference": _event_type_count(
            connection, "asset_url_manifest_reference"
        ),
        "total": total,
        "parent_events": _event_type_count(connection, "parent_review_decision"),
        "non_parent_events": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE event_type<>'parent_review_decision'"
        ).fetchone()[0],
    }
    identity = _recalculate_event_identities(connection)
    orphan = connection.execute(
        "SELECT COUNT(*) FROM decision_events child LEFT JOIN decision_events parent "
        "ON parent.event_id=child.supersedes_event_id "
        "WHERE child.supersedes_event_id IS NOT NULL AND parent.event_id IS NULL"
    ).fetchone()[0]
    confirmation_count = connection.execute(
        "SELECT COUNT(*) FROM decision_events WHERE source_confirmation_id=? "
        "AND source_confirmation_root_hash=?",
        (CONFIRMATION_ID, CONFIRMATION_ROOT_HASH),
    ).fetchone()[0]
    resolution_targets = connection.execute(
        "SELECT COUNT(DISTINCT supersedes_event_id) FROM decision_events "
        "WHERE event_type='parent_review_decision' AND action='supersede'"
    ).fetchone()[0]
    legacy_reviewers = [tuple(row) for row in connection.execute(
        "SELECT reviewer,reviewed_at,COUNT(*) FROM decision_events "
        "WHERE provenance='legacy_import' GROUP BY reviewer,reviewed_at"
    )]
    batch_reviewers = [tuple(row) for row in connection.execute(
        "SELECT reviewer,reviewed_at,COUNT(*) FROM decision_events "
        "WHERE provenance='batch_approval' GROUP BY reviewer,reviewed_at"
    )]
    return {
        "counts": counts,
        "unique_event_ids": connection.execute(
            "SELECT COUNT(DISTINCT event_id) FROM decision_events"
        ).fetchone()[0],
        "unique_idempotency_keys": connection.execute(
            "SELECT COUNT(DISTINCT idempotency_key) FROM decision_events"
        ).fetchone()[0],
        "blank_event_ids": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE length(trim(event_id))=0"
        ).fetchone()[0],
        "blank_event_hashes": connection.execute(
            "SELECT COUNT(*) FROM decision_events WHERE length(trim(event_hash))=0"
        ).fetchone()[0],
        "orphan_supersedes": orphan,
        "resolution_supersede_targets": resolution_targets,
        "confirmation_reference_count": confirmation_count,
        "event_identity_recalculated": identity["valid"],
        "identity_mismatches": identity["mismatches"],
        "legacy_reviewer_groups": legacy_reviewers,
        "batch_reviewer_groups": batch_reviewers,
        "legacy_reviewer_preserved": legacy_reviewers == [("Admin", "2026-07-10", 46)],
        "batch_reviewer_admin": batch_reviewers == [
            ("Admin", "2026-07-19T18:14:14+08:00", 96)
        ],
    }


def _recalculate_event_identities(connection):
    mismatches = []
    for row in connection.execute("SELECT * FROM decision_events ORDER BY event_sequence"):
        payload = _event_payload(row)
        payload.pop("event_id")
        payload.pop("idempotency_key")
        key = _sha256_bytes(_canonical_json(payload))
        event_id = f"event-v2-{key[:24]}"
        if key != row["idempotency_key"] or event_id != row["event_id"]:
            mismatches.append(row["event_sequence"])
    return {"valid": not mismatches, "mismatches": mismatches}


def _recalculate_hash_chain(connection):
    previous = None
    mismatches = []
    head = None
    tail = None
    count = 0
    for row in connection.execute("SELECT * FROM decision_events ORDER BY event_sequence"):
        count += 1
        if head is None:
            head = row["event_hash"]
        payload = _event_payload(row)
        calculated = _sha256_bytes(
            f"{previous or ''}\n{_canonical_json_text(payload)}".encode("utf-8")
        )
        if row["previous_event_hash"] != previous or row["event_hash"] != calculated:
            mismatches.append(row["event_sequence"])
        previous = row["event_hash"]
        tail = row["event_hash"]
    return {
        "valid": not mismatches and count == 162,
        "count": count,
        "head": head,
        "tail": tail,
        "mismatches": mismatches,
        "head_matches_expected": head == EXPECTED_CHAIN_HEAD,
        "tail_matches_expected": tail == EXPECTED_CHAIN_TAIL,
        "first_previous_hash_is_null": connection.execute(
            "SELECT previous_event_hash IS NULL FROM decision_events ORDER BY event_sequence LIMIT 1"
        ).fetchone()[0] == 1,
    }


def _validate_parent_state(connection):
    rows = connection.execute("SELECT * FROM current_parent_decisions").fetchall()
    provenance = {}
    for row in rows:
        provenance[row["provenance"]] = provenance.get(row["provenance"], 0) + 1
    unique = len({row["record_id"] for row in rows})
    superseded_in_current = connection.execute(
        "SELECT COUNT(*) FROM current_parent_decisions current "
        "WHERE EXISTS (SELECT 1 FROM decision_events later "
        "WHERE later.supersedes_event_id=current.event_id)"
    ).fetchone()[0]
    return {
        "row_count": len(rows),
        "unique_record_ids": unique,
        "authority_gap": 120 - unique,
        "duplicate_current_states": len(rows) - unique,
        "batch_current": provenance.get("batch_approval", 0),
        "legacy_current": provenance.get("legacy_import", 0),
        "admin_resolution_current": provenance.get("admin_resolution", 0),
        "superseded_events_in_current": superseded_in_current,
    }


def _validate_special_parents(connection):
    rows = []
    parents = {
        row["record_id"]: json.loads(row["new_value_json"])
        for row in connection.execute(
            "SELECT record_id,new_value_json FROM current_parent_decisions "
            "WHERE record_id IN (?,?,?,?,?)",
            (
                "商家夥伴案例資料庫:r30", "商家夥伴案例資料庫:r12",
                "商家夥伴案例資料庫:r122", "商家夥伴案例資料庫:r32",
                "商家夥伴案例資料庫:r7",
            ),
        )
    }
    assets = {
        row["asset_id"]: json.loads(row["new_value_json"])
        for row in connection.execute("SELECT asset_id,new_value_json FROM current_asset_eligibility")
    }
    aliases = [json.loads(row[0]) for row in connection.execute(
        "SELECT new_value_json FROM current_search_aliases WHERE record_id='商家夥伴案例資料庫:r32'"
    )]
    entities = {
        row["record_id"]: json.loads(row["new_value_json"])
        for row in connection.execute("SELECT record_id,new_value_json FROM current_entity_metadata")
    }
    checks = {
        "r30_parent_exclude": parents.get("商家夥伴案例資料庫:r30", {}).get("review_decision") == "exclude",
        "r30_not_syncable_or_indexable": parents.get("商家夥伴案例資料庫:r30", {}).get("can_enter_content_index") == "false",
        "r30_article_excluded": assets.get("商家夥伴案例資料庫:r30:article", {}).get("asset_index_eligibility") == "exclude",
        "r30_article_not_searchable": assets.get("商家夥伴案例資料庫:r30:article", {}).get("asset_search_eligibility") in {"excluded", "not_searchable"},
        "r30_handle_has_no_alias_projection": connection.execute(
            "SELECT COUNT(*) FROM current_search_aliases WHERE lower(new_value_json) LIKE '%littlegirl%'"
        ).fetchone()[0] == 0,
        "r12_internal_only": parents.get("商家夥伴案例資料庫:r12", {}).get("review_decision") == "approve_internal_only",
        "r12_non_external": parents.get("商家夥伴案例資料庫:r12", {}).get("can_external_reference") == "false",
        "r12_article_internal": assets.get("商家夥伴案例資料庫:r12:article") == {"asset_index_eligibility": "include", "asset_search_eligibility": "searchable_internal"},
        "r12_video_hold": assets.get("商家夥伴案例資料庫:r12:video") == {"asset_index_eligibility": "hold", "asset_search_eligibility": "not_searchable"},
        "r122_approved_partner": parents.get("商家夥伴案例資料庫:r122", {}).get("review_decision") == "approve" and entities.get("商家夥伴案例資料庫:r122") == {"entity_type": "partner", "merchant_handle_requirement": "not_required"},
        "r122_three_assets": _asset_suffixes(assets, "商家夥伴案例資料庫:r122") == {"article", "video", "podcast"},
        "r32_approved": parents.get("商家夥伴案例資料庫:r32", {}).get("review_decision") == "approve",
        "r32_three_assets": _asset_suffixes(assets, "商家夥伴案例資料庫:r32") == {"article", "video", "podcast"},
        "r32_exact_aliases": {(item["alias"], item["normalized_alias"], item["match_type"]) for item in aliases} == {("SLP", "slp", "case_insensitive_exact"), ("SHOPLINE Payments", "shopline payments", "case_insensitive_exact")},
        "r7_approved_partner": parents.get("商家夥伴案例資料庫:r7", {}).get("review_decision") == "approve" and entities.get("商家夥伴案例資料庫:r7") == {"entity_type": "partner", "merchant_handle_requirement": "not_required"},
        "r7_article": _asset_suffixes(assets, "商家夥伴案例資料庫:r7") == {"article"},
        "no_synthetic_handle_metadata": all("merchant_handle" not in value for value in entities.values()),
    }
    return [
        {"check": check, "observed": value, "status": "pass" if value else "fail"}
        for check, value in checks.items()
    ]


def _validate_assets(connection):
    rows = [json.loads(row[0]) for row in connection.execute(
        "SELECT new_value_json FROM current_asset_eligibility"
    )]
    include = sum(row["asset_index_eligibility"] == "include" for row in rows)
    hold = sum(row["asset_index_eligibility"] == "hold" for row in rows)
    exclude = sum(row["asset_index_eligibility"] == "exclude" for row in rows)
    return {
        "count": len(rows), "include": include, "hold": hold, "exclude": exclude,
        "eligible_assets": 205, "hold_assets": 1, "excluded_or_blocked_assets": 16,
        "approved_url_fields": 410,
        "parent_approval_does_not_override_hold": hold == 1,
        "parent_approval_does_not_override_exclude": exclude == 1,
        "hold_not_searchable": all(
            row["asset_search_eligibility"] == "not_searchable"
            for row in rows if row["asset_index_eligibility"] == "hold"
        ),
        "exclude_not_searchable": all(
            row["asset_search_eligibility"] in {"excluded", "not_searchable"}
            for row in rows if row["asset_index_eligibility"] == "exclude"
        ),
    }


def _validate_aliases(connection):
    rows = connection.execute("SELECT * FROM current_search_aliases ORDER BY subject_id").fetchall()
    values = [json.loads(row["new_value_json"]) for row in rows]
    return {
        "count": len(rows),
        "exact_aliases": sorted(value["alias"] for value in values),
        "normalized_aliases": sorted(value["normalized_alias"] for value in values),
        "case_insensitive_exact": all(
            value["match_type"] == "case_insensitive_exact"
            and value["normalized_alias"] == value["alias"].casefold()
            for value in values
        ),
        "parent_level_only": all(row["asset_id"] is None for row in rows),
        "not_content_tags": all(row["field_name"] == "search_aliases" for row in rows),
        "excluded_parent_alias_count": connection.execute(
            "SELECT COUNT(*) FROM current_search_aliases alias "
            "JOIN current_parent_decisions parent ON parent.record_id=alias.record_id "
            "WHERE json_extract(parent.new_value_json,'$.review_decision')='exclude'"
        ).fetchone()[0],
        "view_supports_multi_parent_alias": "subject_id = event.subject_id" in (
            connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='view' AND name='current_search_aliases'"
            ).fetchone()[0]
        ),
    }


def _validate_url_reference(connection, root):
    rows = connection.execute(
        "SELECT * FROM decision_events WHERE event_type='asset_url_manifest_reference'"
    ).fetchall()
    value = json.loads(rows[0]["new_value_json"]) if len(rows) == 1 else {}
    current_hashes = {
        "decision_csv_checksum": _sha256(root / value.get("source_path_reference", "missing")),
        "validator_output_checksum": _sha256(
            root / "reports/asset_metadata_review_validation/review_decision_status.csv"
        ),
        "apply_preview_checksum": _sha256(
            root / "reports/asset_metadata_apply_preview/asset_apply_preview.csv"
        ),
        "blocked_preview_checksum": _sha256(
            root / "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"
        ),
    } if value else {}
    return {
        "count": len(rows),
        "approved_url_field_count": value.get("approved_url_field_count"),
        "eligible_asset_count": value.get("eligible_asset_count"),
        "hold_asset_count": value.get("hold_asset_count"),
        "excluded_or_blocked_asset_count": value.get("excluded_or_blocked_asset_count"),
        "manifest_hash": value.get("manifest_hash"),
        "manifest_hash_valid": value.get("manifest_hash") == EXPECTED_URL_MANIFEST_HASH,
        "source_path_reference": value.get("source_path_reference"),
        "source_checksums_valid": bool(value) and all(
            value.get(key) == checksum for key, checksum in current_hashes.items()
        ),
        "actual_url_values_stored": _contains_http_value(value),
    }


def _validate_temporary_copy(formal_database, directory):
    directory.mkdir(parents=True, exist_ok=False)
    base = directory / "existing-store-copy.sqlite"
    source = sqlite3.connect(
        f"file:{Path(formal_database).resolve()}?mode=ro&immutable=1", uri=True
    )
    destination = sqlite3.connect(base)
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    trigger_checks = _temporary_trigger_checks(base)
    duplicate_checks = _temporary_duplicate_checks(base)
    projection_checks = _temporary_projection_checks(base)
    alias_multi_parent = _temporary_alias_multi_parent_check(base)
    tampered_payload = directory / "tampered-payload.sqlite"
    shutil.copy2(base, tampered_payload)
    connection = sqlite3.connect(tampered_payload)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("DROP TRIGGER decision_events_no_update")
        connection.execute(
            "UPDATE decision_events SET decision_reason='temporary tamper' WHERE event_sequence=1"
        )
        connection.commit()
        tampered_detected = not _recalculate_hash_chain(connection)["valid"]
    finally:
        connection.close()
    broken_chain = directory / "broken-chain.sqlite"
    shutil.copy2(base, broken_chain)
    connection = sqlite3.connect(broken_chain)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("DROP TRIGGER decision_events_no_update")
        connection.execute(
            "UPDATE decision_events SET previous_event_hash=? WHERE event_sequence=2",
            ("0" * 64,),
        )
        connection.commit()
        broken_detected = not _recalculate_hash_chain(connection)["valid"]
    finally:
        connection.close()
    restore = directory / "restored.sqlite"
    source = sqlite3.connect(base)
    destination = sqlite3.connect(restore)
    try:
        source.backup(destination)
        backup_restore = (
            destination.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and destination.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162
        )
    finally:
        source.close()
        destination.close()
    with _readonly_connection(base) as connection:
        read_only_reopen = (
            connection.execute("PRAGMA query_only").fetchone()[0] == 1
            and connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162
        )
        temporary_integrity_check = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
    return {
        **trigger_checks,
        **duplicate_checks,
        **projection_checks,
        "alias_multi_parent_semantics": alias_multi_parent,
        "tampered_payload_detected": tampered_detected,
        "broken_previous_hash_detected": broken_detected,
        "backup_restore": backup_restore,
        "read_only_reopen": read_only_reopen,
        "integrity_check": temporary_integrity_check,
    }


def _temporary_trigger_checks(path):
    statements = {
        "decision_events_update": "UPDATE decision_events SET decision_reason='x' WHERE event_sequence=1",
        "decision_events_delete": "DELETE FROM decision_events WHERE event_sequence=1",
        "schema_metadata_update": "UPDATE schema_metadata SET compatibility_status='x' WHERE schema_sequence=1",
        "schema_metadata_delete": "DELETE FROM schema_metadata WHERE schema_sequence=1",
        "execution_metadata_update": "UPDATE execution_metadata SET status='failed' WHERE execution_sequence=1",
        "execution_metadata_delete": "DELETE FROM execution_metadata WHERE execution_sequence=1",
    }
    results = {}
    connection = sqlite3.connect(path)
    try:
        for key, sql in statements.items():
            try:
                connection.execute(sql)
            except sqlite3.IntegrityError:
                results[key] = True
                connection.rollback()
            else:
                results[key] = False
                connection.rollback()
    finally:
        connection.close()
    return {
        "all_updates_blocked": all(
            value for key, value in results.items() if key.endswith("update")
        ),
        "all_deletes_blocked": all(
            value for key, value in results.items() if key.endswith("delete")
        ),
        "trigger_results": results,
    }


def _temporary_duplicate_checks(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM decision_events WHERE event_sequence=1").fetchone()
    columns = [key for key in row.keys() if key != "event_sequence"]
    placeholders = ",".join("?" for _ in columns)
    sql = f"INSERT INTO decision_events ({','.join(columns)}) VALUES ({placeholders})"
    values = [row[key] for key in columns]
    try:
        connection.execute(sql, values)
    except sqlite3.IntegrityError:
        duplicate_event_id = True
        connection.rollback()
    else:
        duplicate_event_id = False
        connection.rollback()
    changed = dict(zip(columns, values))
    changed["event_id"] = "temporary-unique-event-id"
    changed["event_hash"] = "f" * 64
    try:
        connection.execute(sql, [changed[key] for key in columns])
    except sqlite3.IntegrityError:
        duplicate_idempotency = True
        connection.rollback()
    else:
        duplicate_idempotency = False
        connection.rollback()
    connection.close()
    return {
        "duplicate_event_id_rejected": duplicate_event_id,
        "duplicate_idempotency_key_rejected": duplicate_idempotency,
    }


def _temporary_projection_checks(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    current = connection.execute(
        "SELECT * FROM current_parent_decisions WHERE record_id='商家夥伴案例資料庫:r30'"
    ).fetchone()
    base = {
        "event_type": "parent_review_decision", "subject_type": "parent",
        "subject_id": current["subject_id"], "record_id": current["record_id"],
        "asset_id": None, "field_name": "review_decision",
        "previous_value": json.loads(current["new_value_json"]),
        "reviewer": "Admin", "reviewed_at": EXPECTED_EXECUTED_AT,
        "decision_reason": "temporary projection validation",
        "provenance": "temporary_validation", "source_plan_id": EXPECTED_PLAN_ID,
        "source_manifest_hash": BUNDLE_ROOT_HASH, "source_bundle_id": BUNDLE_ID,
        "source_bundle_root_hash": BUNDLE_ROOT_HASH,
        "source_confirmation_id": CONFIRMATION_ID,
        "source_confirmation_root_hash": CONFIRMATION_ROOT_HASH,
        "input_checksums": {"temporary_validation": EXPECTED_DATABASE_SHA256},
        "supersedes_event_id": current["event_id"], "created_at": EXPECTED_EXECUTED_AT,
        "code_version": VALIDATOR_CODE_VERSION,
    }
    connection.execute("BEGIN")
    supersede = _bound_temporary_event({
        **base, "action": "supersede", "new_value": {"review_decision": "temporary"}
    })
    _insert_event(connection, supersede)
    supersede_ok = json.loads(connection.execute(
        "SELECT new_value_json FROM current_parent_decisions "
        "WHERE record_id='商家夥伴案例資料庫:r30'"
    ).fetchone()[0])["review_decision"] == "temporary"
    connection.rollback()
    connection.execute("BEGIN")
    revoke = _bound_temporary_event({**base, "action": "revoke", "new_value": None})
    _insert_event(connection, revoke)
    revoke_ok = connection.execute(
        "SELECT COUNT(*) FROM current_parent_decisions "
        "WHERE record_id='商家夥伴案例資料庫:r30'"
    ).fetchone()[0] == 0
    connection.rollback()
    count_ok = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162
    connection.close()
    return {
        "supersede_projection": supersede_ok and count_ok,
        "revoke_projection": revoke_ok and count_ok,
    }


def _temporary_alias_multi_parent_check(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    payload = {
        "event_type": "search_alias", "subject_type": "search_alias",
        "subject_id": "商家夥伴案例資料庫:r7|slp", "record_id": "商家夥伴案例資料庫:r7",
        "asset_id": None, "field_name": "search_aliases", "action": "add",
        "previous_value": None,
        "new_value": {"alias": "SLP", "normalized_alias": "slp", "match_type": "case_insensitive_exact"},
        "reviewer": "Admin", "reviewed_at": EXPECTED_EXECUTED_AT,
        "decision_reason": "temporary alias multi-parent validation",
        "provenance": "temporary_validation", "source_plan_id": EXPECTED_PLAN_ID,
        "source_manifest_hash": BUNDLE_ROOT_HASH, "source_bundle_id": BUNDLE_ID,
        "source_bundle_root_hash": BUNDLE_ROOT_HASH,
        "source_confirmation_id": CONFIRMATION_ID,
        "source_confirmation_root_hash": CONFIRMATION_ROOT_HASH,
        "input_checksums": {"temporary_validation": EXPECTED_DATABASE_SHA256},
        "supersedes_event_id": None, "created_at": EXPECTED_EXECUTED_AT,
        "code_version": VALIDATOR_CODE_VERSION,
    }
    _insert_event(connection, _bound_temporary_event(payload))
    valid = connection.execute(
        "SELECT COUNT(*) FROM current_search_aliases "
        "WHERE json_extract(new_value_json,'$.normalized_alias')='slp'"
    ).fetchone()[0] == 2
    connection.rollback()
    connection.close()
    return valid


def _bound_temporary_event(payload):
    key = _sha256_bytes(_canonical_json(payload))
    return {**payload, "idempotency_key": key, "event_id": f"event-v2-{key[:24]}"}


def _require_validation_success(**values):
    expected_counts = {
        "legacy_import": 46, "batch_parent_approval": 96,
        "resolution_parent_supersede": 5, "asset_eligibility": 10,
        "search_alias": 2, "entity_metadata": 2,
        "asset_url_manifest_reference": 1, "total": 162,
        "parent_events": 125, "non_parent_events": 37,
    }
    events = values["event_validation"]
    parents = values["parent_state"]
    temporary = values["temporary_copy"]
    checks = [
        all(values["authority_chain"].values()),
        values["schema_validation"]["objects_match_canonical"],
        values["schema_validation"]["confirmation_columns_present"],
        values["schema_metadata"]["valid"],
        values["execution_metadata"]["valid"],
        events["counts"] == expected_counts,
        events["unique_event_ids"] == 162,
        events["unique_idempotency_keys"] == 162,
        events["orphan_supersedes"] == 0,
        events["resolution_supersede_targets"] == 5,
        events["confirmation_reference_count"] == 162,
        events["event_identity_recalculated"],
        events["legacy_reviewer_preserved"],
        events["batch_reviewer_admin"],
        values["hash_chain"]["valid"],
        values["hash_chain"]["head"] == EXPECTED_CHAIN_HEAD,
        values["hash_chain"]["tail"] == EXPECTED_CHAIN_TAIL,
        parents == {
            "row_count": 120, "unique_record_ids": 120, "authority_gap": 0,
            "duplicate_current_states": 0, "batch_current": 96, "legacy_current": 19,
            "admin_resolution_current": 5, "superseded_events_in_current": 0,
        },
        all(row["status"] == "pass" for row in values["special_rows"]),
        values["asset_validation"]["count"] == 10,
        values["asset_validation"]["include"] == 8,
        values["asset_validation"]["hold"] == 1,
        values["asset_validation"]["exclude"] == 1,
        values["alias_validation"]["count"] == 2,
        values["alias_validation"]["excluded_parent_alias_count"] == 0,
        values["url_reference"]["count"] == 1,
        values["url_reference"]["approved_url_field_count"] == 410,
        values["url_reference"]["source_checksums_valid"],
        not values["url_reference"]["actual_url_values_stored"],
        values["integrity_check"] == "ok",
        values["foreign_key_errors"] == 0,
        values["formal_query_only"],
        temporary["all_updates_blocked"], temporary["all_deletes_blocked"],
        temporary["duplicate_event_id_rejected"],
        temporary["duplicate_idempotency_key_rejected"],
        temporary["tampered_payload_detected"],
        temporary["broken_previous_hash_detected"],
        temporary["supersede_projection"], temporary["revoke_projection"],
        temporary["alias_multi_parent_semantics"], temporary["backup_restore"],
        temporary["read_only_reopen"],
    ]
    if not all(checks):
        raise ExistingGovernanceDecisionStoreValidationError(
            "independent Existing Decision Store validation failed"
        )


def _write_reports(output, result):
    output.mkdir(parents=True, exist_ok=True)
    pass_row = lambda check, observed: {"check": check, "observed": observed, "status": "pass"}
    _write_text(output / REPORT_FILENAMES[0], (
        "# Existing Governance Decision Store Validation\n\n"
        "- Conclusion: A. Existing Decision Store independently validated\n"
        f"- Database SHA-256: `{result['database_sha256_after']}`\n"
        f"- Database byte size: `{result['database_size_after']}`\n"
        "- Decision Events: 162\n- Current Parent State: 120\n- Authority Gap: 0\n"
        "- Formal database modified: false\n"
    ))
    _write_csv(output / REPORT_FILENAMES[1], [
        pass_row("sha_before", result["database_sha256_before"]),
        pass_row("sha_after", result["database_sha256_after"]),
        pass_row("size_before", result["database_size_before"]),
        pass_row("size_after", result["database_size_after"]),
        pass_row("mtime_unchanged", result["mtime_unchanged"]),
        pass_row("sidecars_after", len(result["sidecars_after"])),
    ])
    _write_csv(output / REPORT_FILENAMES[2], [
        pass_row("root_execution_hash", result["execution_bundle"]["root_execution_hash"]),
        pass_row("protected_files", result["execution_bundle"]["protected_file_count"]),
        pass_row("database_checksum", result["execution_bundle"]["database_sha256"]),
    ])
    _write_csv(output / REPORT_FILENAMES[3], [
        pass_row(check, observed) for check, observed in result["authority_chain"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[4], [
        pass_row(check, observed) for check, observed in result["schema_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[5], [
        pass_row(check, observed) for check, observed in result["schema_metadata_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[6], [
        pass_row(check, observed) for check, observed in result["execution_metadata_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[7], [
        pass_row(check, observed) for check, observed in result["event_validation"]["counts"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[8], [
        pass_row(check, observed) for check, observed in result["event_validation"].items()
        if check != "counts"
    ])
    _write_csv(output / REPORT_FILENAMES[9], [
        pass_row(check, observed) for check, observed in result["hash_chain_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[10], [
        pass_row(check, observed) for check, observed in result["parent_state_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[11], result["special_parent_rows"])
    _write_csv(output / REPORT_FILENAMES[12], [
        pass_row(check, observed) for check, observed in result["asset_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[13], [
        pass_row(check, observed) for check, observed in result["alias_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[14], [
        pass_row(check, observed) for check, observed in result["url_reference_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[15], [
        pass_row("query_only", result["formal_query_only"]),
        pass_row("integrity_check", result["integrity_check"]),
        pass_row("foreign_key_errors", result["foreign_key_errors"]),
        pass_row("schema_version_pragma_unchanged", result["schema_version_pragma_unchanged"]),
        pass_row("user_version_pragma_unchanged", result["user_version_pragma_unchanged"]),
    ])
    _write_csv(output / REPORT_FILENAMES[16], [
        pass_row(check, observed)
        for check, observed in result["temporary_copy_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[17], [
        pass_row("formal_database_unchanged", result["formal_database_unchanged"]),
        pass_row("protected_inputs_unchanged", result["protected_inputs_unchanged"]),
        pass_row("parent_not_synced", True), pass_row("search_alias_not_applied", True),
        pass_row("asset_eligibility_not_applied", True), pass_row("slack_api_not_called", True),
    ])
    _write_text(output / REPORT_FILENAMES[18], (
        "# Parent Sync Readiness Assessment\n\n"
        "Current State is a valid next-stage input. Readiness candidates are r12, r122, r32, and r7; "
        "r30 remains excluded and not syncable. The next Plan must choose and document either a four-record "
        "delta sync or a complete 120-record authoritative projection, and must fail closed against partial state.\n"
    ))
    _write_csv(output / REPORT_FILENAMES[19], [])
    _write_csv(output / REPORT_FILENAMES[20], [])
    _write_text(output / REPORT_FILENAMES[21], (
        "# Next Parent Sync Plan Prerequisites\n\n"
        "Use the existing Decision Store read-only as authority; validate its SHA and Execution Bundle again; "
        "define delta-versus-full projection scope; preserve asset holds and exclusions; require a new Plan, "
        "independent validation, and Admin confirmation before any sync.\n"
    ))
    actual = sorted(
        path.name for path in output.iterdir()
        if path.is_file() and not path.name.startswith("._")
    )
    if actual != sorted(REPORT_FILENAMES):
        raise ExistingGovernanceDecisionStoreValidationError("existing-store report contract incomplete")


def _event_payload(row):
    return {
        "event_type": row["event_type"], "subject_type": row["subject_type"],
        "subject_id": row["subject_id"], "record_id": row["record_id"],
        "asset_id": row["asset_id"], "field_name": row["field_name"],
        "action": row["action"], "previous_value": json.loads(row["previous_value_json"]),
        "new_value": json.loads(row["new_value_json"]), "reviewer": row["reviewer"],
        "reviewed_at": row["reviewed_at"], "decision_reason": row["decision_reason"],
        "provenance": row["provenance"], "source_plan_id": row["source_plan_id"],
        "source_manifest_hash": row["source_manifest_hash"],
        "source_bundle_id": row["source_bundle_id"],
        "source_bundle_root_hash": row["source_bundle_root_hash"],
        "source_confirmation_id": row["source_confirmation_id"],
        "source_confirmation_root_hash": row["source_confirmation_root_hash"],
        "input_checksums": json.loads(row["input_checksums_json"]),
        "supersedes_event_id": row["supersedes_event_id"], "created_at": row["created_at"],
        "code_version": row["code_version"], "event_id": row["event_id"],
        "idempotency_key": row["idempotency_key"],
    }


def _schema_rows(connection):
    return {
        f"{row[0]}:{row[1]}": (row[0], row[1], row[2], row[3])
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    }


def _table_columns(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {
        table: [tuple(row) for row in connection.execute(f"PRAGMA table_info({table})")]
        for table in tables
    }


def _index_contract(connection):
    tables = ("decision_events", "schema_metadata", "execution_metadata")
    return {
        table: [tuple(row) for row in connection.execute(f"PRAGMA index_list({table})")]
        for table in tables
    }


def _event_type_count(connection, event_type):
    return connection.execute(
        "SELECT COUNT(*) FROM decision_events WHERE event_type=?", (event_type,)
    ).fetchone()[0]


def _asset_suffixes(assets, record_id):
    return {
        asset_id.rsplit(":", 1)[-1]
        for asset_id, value in assets.items()
        if asset_id.startswith(f"{record_id}:") and value["asset_index_eligibility"] == "include"
    }


def _contains_http_value(value):
    if isinstance(value, str):
        return value.startswith(("http://", "https://"))
    if isinstance(value, dict):
        return any(_contains_http_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_http_value(item) for item in value)
    return False


def _database_file_state(path):
    candidate = Path(path)
    stat = candidate.stat()
    return {"sha256": _sha256(candidate), "byte_size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _require_expected_database(state):
    if state["sha256"] != EXPECTED_DATABASE_SHA256:
        raise ExistingGovernanceDecisionStoreValidationError("formal database SHA-256 mismatch")
    if state["byte_size"] != EXPECTED_DATABASE_SIZE:
        raise ExistingGovernanceDecisionStoreValidationError("formal database byte size mismatch")


def _sidecars(path):
    candidate = Path(path)
    suffixes = ("-journal", "-wal", "-shm", ".lock")
    return sorted(
        sidecar.name for sidecar in (
            candidate.with_name(candidate.name + suffix) for suffix in suffixes
        ) if sidecar.exists()
    )


def _protected_paths(root, execution, parent, confirmation):
    return [
        execution, parent, confirmation,
        root / "reports/governance_decision_store_schema_v2_plan",
        root / "obsidian_vault", root / ".mka/content_index.sqlite",
        root / "reports/asset_metadata_preview/human_review_template.csv",
        root / "src/marketing_knowledge_agent/slack_interface.py",
    ]


def _manifest_hash(manifest, field):
    stored = manifest.get(field, "")
    calculated = _sha256_bytes(_canonical_json({
        key: value for key, value in manifest.items() if key != field
    }))
    if stored != calculated:
        raise ExistingGovernanceDecisionStoreValidationError(f"{field} mismatch")
    return stored


def _resolve(root, path):
    value = Path(path)
    return value.resolve() if value.is_absolute() else (Path(root) / value).resolve()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["severity", "code", "message"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_text(path, value):
    Path(path).write_text(value.rstrip() + "\n", encoding="utf-8")
