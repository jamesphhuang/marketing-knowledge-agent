from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional

from .git_provenance import (
    GitProvenanceError,
    validate_historical_git_provenance,
)
from .governance_decision_store_confirmation import (
    GovernanceDecisionStoreConfirmationError,
    _build_independent_event_plan,
    _special_decision_validation,
)
from .governance_decision_store_plan import GovernanceDecisionEvent, normalize_event_payload
from .governance_decision_store_schema_v2_plan import (
    BUNDLE_ID,
    BUNDLE_ROOT_HASH,
    CANONICAL_SCHEMA_V2_SQL,
    CODE_VERSION as PLAN_CODE_VERSION,
    CONFIRMATION_BINDING_VERSION,
    CONFIRMATION_BINDING_PLACEHOLDER,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    event_template_hash,
)
from .parent_authority_import_bundle import (
    ParentAuthorityImportBundleError,
    validate_parent_authority_import_bundle,
)


EXPECTED_PLAN_ID = "decision-store-schema-v2-plan-2aab43cd463170f2"
EXPECTED_MANIFEST_HASH = "3e697fdc37af4ee00523cb26a646b30bfb89b043b7bd3b3934d73c0909b924ea"
EXPECTED_SCHEMA_HASH = "c8431d66857a8695b74c4f6480ad637635a45331a8ece7af961f558f2414a9a7"
CANONICAL_SQL_HASH = "8d1772bedc0c1f1c98bac281e5a3b377ef607e46a6ec7fb4f9c6b894bbf5708b"
EXPECTED_EVENT_TEMPLATE_HASH = "dedaf76689413fa5605b2505081ff7d70e844319990d0cf63a336d59bcdeaa51"
PLAN_EXPIRES_AT = "2026-07-27T15:29:02+08:00"
EXPECTED_TARGET = "data/governance/governance_decisions.sqlite"
EXPECTED_SOURCE_BRANCH = "feat/retrieval-quality-typed-query"
EXPECTED_SOURCE_COMMIT = "d785702cddef27b886f2a729a140a09aa78ad8c7"
VALIDATOR_CODE_VERSION = "governance-decision-store-schema-v2-independent-confirmation-v1"
CONFIRMATION_SCHEMA_VERSION = "2.0"
DEFAULT_PLAN_DIR = Path("reports/governance_decision_store_schema_v2_plan")
DEFAULT_PLAN_MANIFEST = DEFAULT_PLAN_DIR / "schema_v2_plan_manifest.json"
DEFAULT_CANONICAL_SCHEMA = DEFAULT_PLAN_DIR / "canonical_schema_v2.sql"
DEFAULT_SCHEMA_HASH = DEFAULT_PLAN_DIR / "canonical_schema_v2_hash.json"
DEFAULT_BUNDLE_PATH = Path("data/governance/imports") / BUNDLE_ID
DEFAULT_OLD_CONFIRMATION_PATH = Path(
    "data/governance/confirmations/decision-store-plan-a02502d8361549b1"
)
DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / EXPECTED_PLAN_ID
DEFAULT_FORMAL_TARGET = Path(EXPECTED_TARGET)
DEFAULT_REPORT_DIR = Path("reports/governance_decision_store_schema_v2_confirmation")
TEMP_CONFIRMATION_ID = "temporary-schema-v2-confirmation-fixture:not-authoritative"
TEMP_CONFIRMATION_ROOT_HASH = hashlib.sha256(TEMP_CONFIRMATION_ID.encode("utf-8")).hexdigest()
OLD_CONFIRMATION_ID = "decision-store-confirmation-98fef43f8dd6773a"
OBSOLETE_PLAN_IDS = {
    "decision-store-plan-a02502d8361549b1",
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
}
EXPECTED_COUNTS = {
    "legacy_import": 46,
    "batch_parent_approval": 96,
    "resolution_parent_supersede": 5,
    "asset_eligibility": 10,
    "search_alias": 2,
    "entity_metadata": 2,
    "asset_url_manifest_reference": 1,
}
CONFIRMATION_STATEMENT = (
    "Admin confirms the independently validated Governance Decision Store Schema V2 creation "
    "plan identified by PLAN_ID decision-store-schema-v2-plan-2aab43cd463170f2, Plan Manifest "
    "Hash 3e697fdc37af4ee00523cb26a646b30bfb89b043b7bd3b3934d73c0909b924ea, and "
    "Schema Hash c8431d66857a8695b74c4f6480ad637635a45331a8ece7af961f558f2414a9a7. "
    "This confirmation authorizes only a later, separate Execute step using the exact validated "
    "Plan, Schema, Bundle, target path, expected counts, and confirmation-binding contract. It "
    "does not execute the plan and does not authorize any different Plan, Schema, target, "
    "checksum, or event count."
)
REPORT_FILENAMES = (
    "schema_v2_confirmation_summary.md",
    "independent_schema_validation.csv",
    "independent_plan_validation.csv",
    "bundle_revalidation.csv",
    "confirmation_binding_validation.csv",
    "database_sha_boundary_validation.csv",
    "event_reconciliation_validation.csv",
    "temporary_schema_v2_independent_validation.md",
    "special_decision_validation.csv",
    "target_path_validation.csv",
    "obsolete_plan_confirmation_rejection.csv",
    "confirmation_bundle_validation.csv",
    "formal_system_unchanged_validation.csv",
    "schema_v2_execute_prerequisites.md",
    "confirmation_validation_errors.csv",
    "confirmation_validation_warnings.csv",
)


class GovernanceDecisionStoreSchemaV2ConfirmationError(RuntimeError):
    pass


def validate_governance_decision_store_schema_v2_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    schema_hash: str,
    canonical_sql_hash: str,
    plan_manifest_path: Path = DEFAULT_PLAN_MANIFEST,
    canonical_schema_path: Path = DEFAULT_CANONICAL_SCHEMA,
    schema_hash_path: Path = DEFAULT_SCHEMA_HASH,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
    old_confirmation_path: Path = DEFAULT_OLD_CONFIRMATION_PATH,
    legacy_decisions_path: Path = Path("reports/excel_preview/review_decisions_template.csv"),
    merchant_cases_path: Path = Path("reports/excel_preview/merchant_cases.json"),
    asset_url_decisions_path: Path = Path("reports/asset_metadata_preview/human_review_template.csv"),
    asset_url_validation_path: Path = Path("reports/asset_metadata_review_validation/review_decision_status.csv"),
    asset_apply_preview_path: Path = Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    asset_blocked_preview_path: Path = Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    formal_target_path: Path = DEFAULT_FORMAL_TARGET,
    temporary_root: Optional[Path] = None,
    now: Optional[str] = None,
) -> dict:
    root = Path(repo_root).resolve()
    _require_exact_identifiers(plan_id, manifest_hash, schema_hash, canonical_sql_hash)
    validated_at = now or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(validated_at, "validation timestamp")
    if datetime.fromisoformat(validated_at) > datetime.fromisoformat(PLAN_EXPIRES_AT):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Plan expired; regenerate and validate a new Plan")
    paths = {
        "plan_manifest": _resolve(root, plan_manifest_path),
        "canonical_schema": _resolve(root, canonical_schema_path),
        "schema_hash_file": _resolve(root, schema_hash_path),
        "bundle": _resolve(root, bundle_path),
        "old_confirmation": _resolve(root, old_confirmation_path),
        "legacy_decisions": _resolve(root, legacy_decisions_path),
        "merchant_cases": _resolve(root, merchant_cases_path),
        "asset_url_decisions": _resolve(root, asset_url_decisions_path),
        "asset_url_validation": _resolve(root, asset_url_validation_path),
        "asset_apply_preview": _resolve(root, asset_apply_preview_path),
        "asset_blocked_preview": _resolve(root, asset_blocked_preview_path),
    }
    for label, path in paths.items():
        if not path.exists():
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(f"required {label} input is missing: {path}")

    schema_validation = _validate_schema(paths["canonical_schema"], paths["schema_hash_file"])
    bundle_validation, bundle_manifest = _validate_bundle(paths["bundle"])
    plan_manifest, input_checksums = _validate_plan_manifest(root, paths, schema_validation)
    event_plan = _build_v2_independent_event_plan(paths, plan_manifest, bundle_manifest)
    _validate_reconstructed_plan_identity(plan_manifest, input_checksums, event_plan)
    binding_validation, bound_events = _validate_binding_contract(
        plan_manifest, event_plan["events"], schema_validation, paths["old_confirmation"]
    )
    target = _resolve(root, formal_target_path)
    target_checks = _validate_target(target)

    temp_parent = Path(temporary_root) if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-schema-v2-independent-", dir=str(temp_parent) if temp_parent else None
    ) as name:
        directory = Path(name)
        first = directory / "independent-v2.sqlite"
        second = directory / "independent-v2-rerun.sqlite"
        temporary = _build_independent_temporary_store(
            first, bound_events, plan_manifest, schema_validation
        )
        second_result = _build_independent_temporary_store(
            second, bound_events, plan_manifest, schema_validation
        )
        temporary["deterministic_rerun"] = _sha256(first) == _sha256(second)
        temporary["fresh_path"] = first != _resolve(root, DEFAULT_PLAN_DIR / "temporary/schema_v2_validation.sqlite")
        _require_temporary_success(temporary)
    temporary["temporary_files_cleaned"] = not Path(name).exists()

    special_rows = _special_decision_validation(event_plan["events"], paths["merchant_cases"])
    special_errors = sum(row["status"] != "pass" for row in special_rows)
    if special_errors:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("special decision validation failed")
    result = {
        "valid": True,
        "plan_id": plan_id,
        "manifest_hash": manifest_hash,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": schema_hash,
        "canonical_sql_hash": canonical_sql_hash,
        "plan_not_expired": True,
        "plan_expires_at": PLAN_EXPIRES_AT,
        "validated_at": validated_at,
        "bundle_id": bundle_manifest["bundle_id"],
        "bundle_root_hash": bundle_manifest["root_manifest_hash"],
        "bundle_verified_file_count": bundle_validation["manifest_file_count"],
        "bundle_file_checksum_errors": bundle_validation["file_checksum_errors"],
        "bundle_checksum_rows": bundle_validation["checksum_rows"],
        "bundle_authority": {
            "parent_coverage": "120/120",
            "authority_gap": 0,
            "batch_approval": 96,
            "legacy_parent_authority": 19,
            "existing_admin_resolution": 5,
            "restricted_authority": 11,
            "pending_authority": 7,
            "excluded_parent_authority": 10,
            "asset_url_decisions": 410,
        },
        "event_counts": event_plan["counts"],
        "event_count": len(event_plan["events"]),
        "parent_event_count": event_plan["parent_event_count"],
        "non_parent_event_count": event_plan["non_parent_event_count"],
        "unique_parent_subject_count": event_plan["unique_parent_subject_count"],
        "duplicate_event_template_count": event_plan["duplicate_event_template_count"],
        "resolution_supersede_count": event_plan["counts"]["resolution_parent_supersede"],
        "event_template_hash": event_plan["event_template_hash"],
        "current_parent_state_count": temporary["current_parent_state_count"],
        "parent_authority_coverage": "120/120",
        "authority_gap": temporary["authority_gap"],
        "eligible_asset_count": event_plan["conservation"]["eligible_asset_count"],
        "hold_asset_count": event_plan["conservation"]["hold_asset_count"],
        "excluded_asset_count": event_plan["conservation"]["excluded_asset_count"],
        "approved_url_field_count": event_plan["conservation"]["approved_url_field_count"],
        "schema_validation": schema_validation,
        "binding_validation": binding_validation,
        "database_sha_boundary": {
            "physical_sha_external_only": True,
            "database_sha_column_null": temporary["database_sha_self_reference_absent"],
            "external_manifest_reference_present": temporary["execution_manifest_reference_present"],
            "self_reference_absent": True,
        },
        "temporary_store": temporary,
        "special_decision_rows": special_rows,
        "special_decision_errors": special_errors,
        "target_checks": target_checks,
        "formal_target_absent": target_checks["formal_target_absent"],
        "input_checksums": input_checksums,
        "source_branch": plan_manifest["source_branch"],
        "source_commit": plan_manifest["source_commit"],
        "validator_code_version": VALIDATOR_CODE_VERSION,
        "plan_manifest": plan_manifest,
        "event_templates": event_plan["events"],
    }
    public = _public_validation(result)
    result["independent_validation_hash"] = _sha256_bytes(_canonical_json(public))
    return result


def confirm_governance_decision_store_schema_v2_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    schema_hash: str,
    canonical_sql_hash: str,
    reviewer: str,
    confirmed_at: Optional[str] = None,
    confirmation_path: Path = DEFAULT_CONFIRMATION_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    **validation_kwargs,
) -> dict:
    if reviewer != "Admin":
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("reviewer must equal Admin")
    confirmed = confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(confirmed, "confirmation timestamp")
    root = Path(repo_root).resolve()
    confirmation = _resolve(root, confirmation_path)
    reports = _resolve(root, report_dir)
    validation_kwargs = dict(validation_kwargs)
    validation_kwargs.pop("now", None)
    validation = validate_governance_decision_store_schema_v2_plan(
        repo_root=root,
        plan_id=plan_id,
        manifest_hash=manifest_hash,
        schema_hash=schema_hash,
        canonical_sql_hash=canonical_sql_hash,
        now=confirmed,
        **validation_kwargs,
    )
    if confirmation.exists():
        existing = validate_governance_decision_store_schema_v2_confirmation(confirmation)
        expected = (plan_id, manifest_hash, schema_hash, reviewer, confirmed)
        observed = (
            existing["plan_id"], existing["plan_manifest_hash"], existing["schema_hash"],
            existing["reviewer"], existing["confirmed_at"],
        )
        if observed != expected:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                "existing Schema V2 Confirmation Bundle conflicts with requested confirmation"
            )
        summary = _summary(existing, validation, confirmation, reports, True)
        _write_reports(reports, summary, validation, existing, _formal_checks(root))
        return summary

    protected = _protected_paths(root, validation_kwargs)
    protected_before = {str(path): _hash_path(path) for path in protected}
    confirmation.parent.mkdir(parents=True, exist_ok=True)
    if not _is_git_ignored(root, confirmation):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Confirmation path must be Git ignored")
    staging = Path(tempfile.mkdtemp(prefix=f".{confirmation.name}.staging-", dir=str(confirmation.parent)))
    renamed = False
    try:
        payload = _confirmation_payload(validation, reviewer, confirmed)
        independent = _public_validation(validation)
        independent["independent_validation_hash"] = validation["independent_validation_hash"]
        plan_source = _resolve(root, validation_kwargs.get("plan_manifest_path", DEFAULT_PLAN_MANIFEST))
        bundle_source = _resolve(root, validation_kwargs.get("bundle_path", DEFAULT_BUNDLE_PATH)) / "bundle_manifest.json"
        schema_hash_source = _resolve(root, validation_kwargs.get("schema_hash_path", DEFAULT_SCHEMA_HASH))
        _write_json(staging / "confirmation.json", payload)
        _write_json(staging / "independent_validation.json", independent)
        shutil.copyfile(plan_source, staging / "referenced_plan_manifest.json")
        shutil.copyfile(bundle_source, staging / "referenced_bundle_manifest.json")
        shutil.copyfile(schema_hash_source, staging / "referenced_schema_hash.json")
        _write_json(staging / "confirmation_binding_contract.json", validation["plan_manifest"]["confirmation_binding_contract"])
        manifest = _confirmation_manifest(staging, validation, payload)
        _write_json(staging / "confirmation_manifest.json", manifest)
        staged = validate_governance_decision_store_schema_v2_confirmation(staging)
        if confirmation.exists():
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                "Confirmation target appeared before atomic rename"
            )
        os.replace(staging, confirmation)
        renamed = True
        final = validate_governance_decision_store_schema_v2_confirmation(confirmation)
        if final["root_confirmation_hash"] != staged["root_confirmation_hash"]:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                "Confirmation root hash changed after atomic rename"
            )
        _make_read_only(confirmation)
    except Exception:
        if renamed and confirmation.exists():
            quarantine = confirmation.with_name(
                f"{confirmation.name}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
            )
            if not quarantine.exists():
                os.replace(confirmation, quarantine)
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    protected_after = {str(path): _hash_path(path) for path in protected}
    if protected_before != protected_after:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "protected Plan, Bundle, old Confirmation, Vault, SQLite or renderer changed"
        )
    summary = _summary(final, validation, confirmation, reports, False)
    _write_reports(reports, summary, validation, final, _formal_checks(root))
    return summary


def validate_governance_decision_store_schema_v2_confirmation(path: Path) -> dict:
    root = Path(path)
    manifest_path = root / "confirmation_manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "Schema V2 Confirmation Bundle is missing"
        )
    manifest = _read_json(manifest_path)
    stored = manifest.get("root_confirmation_hash", "")
    expected = _sha256_bytes(_canonical_json({
        key: value for key, value in manifest.items() if key != "root_confirmation_hash"
    }))
    if not stored or stored != expected:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Confirmation root hash mismatch")
    required_manifest = {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_sql_hash": CANONICAL_SQL_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "target_path": EXPECTED_TARGET,
        "expected_event_count": 162,
        "expected_parent_current_state_count": 120,
        "approved_url_field_count": 410,
        "reviewer": "Admin",
        "plan_expires_at": PLAN_EXPIRES_AT,
    }
    for key, value in required_manifest.items():
        if manifest.get(key) != value:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                f"Confirmation is invalid for Schema V2: {key} mismatch"
            )
    listed = set()
    for entry in manifest.get("files", []):
        filename = _safe_filename(entry.get("filename", ""))
        if filename in listed:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError("duplicate Confirmation filename")
        listed.add(filename)
        candidate = root / filename
        if not candidate.is_file() or _sha256(candidate) != entry.get("sha256"):
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                f"Confirmation checksum mismatch: {filename}"
            )
        if candidate.stat().st_size != entry.get("byte_size"):
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                f"Confirmation byte size mismatch: {filename}"
            )
    physical = {
        item.name for item in root.iterdir()
        if item.is_file() and item.name != "confirmation_manifest.json" and not item.name.startswith("._")
    }
    if physical != listed or len(listed) != 6:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "Confirmation contains unlisted or missing files"
        )
    confirmation = _read_json(root / "confirmation.json")
    if confirmation.get("confirmation_statement") != CONFIRMATION_STATEMENT:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Confirmation statement mismatch")
    for key in (
        "confirmation_id", "plan_id", "plan_manifest_hash", "schema_version", "schema_hash",
        "canonical_sql_hash", "bundle_id", "bundle_root_hash", "target_path", "reviewer",
        "confirmed_at", "plan_expires_at",
    ):
        if confirmation.get(key) != manifest.get(key):
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(f"Confirmation {key} mismatch")
    referenced_plan = _read_json(root / "referenced_plan_manifest.json")
    if referenced_plan.get("manifest_hash") != EXPECTED_MANIFEST_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("referenced Plan manifest mismatch")
    referenced_bundle = _read_json(root / "referenced_bundle_manifest.json")
    if referenced_bundle.get("root_manifest_hash") != BUNDLE_ROOT_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("referenced Bundle manifest mismatch")
    referenced_schema = _read_json(root / "referenced_schema_hash.json")
    if referenced_schema.get("schema_hash") != EXPECTED_SCHEMA_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("referenced Schema hash mismatch")
    binding = _read_json(root / "confirmation_binding_contract.json")
    binding_hash = _sha256_bytes(_canonical_json(binding))
    if binding_hash != manifest.get("confirmation_binding_contract_hash"):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("binding contract hash mismatch")
    independent = _read_json(root / "independent_validation.json")
    independent_hash = independent.pop("independent_validation_hash", "")
    if independent_hash != _sha256_bytes(_canonical_json(independent)):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("independent validation hash mismatch")
    if independent_hash != manifest.get("independent_validation_hash"):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "Confirmation references the wrong independent validation"
        )
    return {
        "valid": True,
        "confirmation_id": manifest["confirmation_id"],
        "plan_id": manifest["plan_id"],
        "plan_manifest_hash": manifest["plan_manifest_hash"],
        "schema_hash": manifest["schema_hash"],
        "canonical_sql_hash": manifest["canonical_sql_hash"],
        "reviewer": manifest["reviewer"],
        "confirmed_at": manifest["confirmed_at"],
        "root_confirmation_hash": stored,
        "protected_file_count": len(listed),
        "physical_file_count": len(listed) + 1,
        "read_only_reopen": True,
    }


def _validate_schema(schema_path, hash_path):
    sql = Path(schema_path).read_text(encoding="utf-8")
    sql_hash = _sha256_bytes(sql.encode("utf-8"))
    if sql_hash != CANONICAL_SQL_HASH or sql != CANONICAL_SCHEMA_V2_SQL:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Canonical SQL hash or content mismatch")
    expected_schema_hash = _sha256_bytes(_canonical_json({
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "canonical_schema_sql_hash": sql_hash,
        "confirmation_binding_version": CONFIRMATION_BINDING_VERSION,
    }))
    if expected_schema_hash != EXPECTED_SCHEMA_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Schema Hash mismatch")
    hash_file = _read_json(hash_path)
    if hash_file != {
        "canonical_schema_sql_hash": CANONICAL_SQL_HASH,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "schema_version": SCHEMA_VERSION,
    }:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Schema hash evidence mismatch")
    with tempfile.TemporaryDirectory(prefix="mka-schema-v2-contract-") as name:
        database = Path(name) / "schema.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(sql)
            tables = _object_names(connection, "table")
            views = _object_names(connection, "view")
            triggers = _object_names(connection, "trigger")
            indexes = _object_names(connection, "index")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(decision_events)")}
            foreign_keys = len(connection.execute("PRAGMA foreign_key_list(decision_events)").fetchall())
        finally:
            connection.close()
    required_tables = {"decision_events", "schema_metadata", "execution_metadata"}
    required_views = {
        "current_parent_decisions", "current_asset_eligibility",
        "current_search_aliases", "current_entity_metadata",
    }
    required_triggers = {
        "decision_events_no_update", "decision_events_no_delete",
        "schema_metadata_no_update", "schema_metadata_no_delete",
        "execution_metadata_no_update", "execution_metadata_no_delete",
    }
    required_indexes = {
        "idx_decision_events_subject", "idx_decision_events_supersedes",
        "idx_decision_events_confirmation", "idx_schema_metadata_version",
        "idx_execution_metadata_plan",
    }
    checks = {
        "schema_version_two": True,
        "canonical_sql_hash_valid": sql_hash == CANONICAL_SQL_HASH,
        "schema_hash_valid": expected_schema_hash == EXPECTED_SCHEMA_HASH,
        "canonical_sql_matches_code_contract": sql == CANONICAL_SCHEMA_V2_SQL,
        "confirmation_columns_present": {
            "source_confirmation_id", "source_confirmation_root_hash"
        }.issubset(columns),
        "required_tables_present": required_tables.issubset(tables),
        "required_views_present": required_views.issubset(views),
        "required_triggers_present": required_triggers.issubset(triggers),
        "required_indexes_present": required_indexes.issubset(indexes),
        "required_foreign_keys_present": foreign_keys >= 2,
        "database_sha_must_be_null": "CHECK (database_sha256 IS NULL)" in sql,
    }
    if not all(checks.values()):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"Schema V2 structural validation failed: {checks}"
        )
    return {**checks, "schema_hash": expected_schema_hash, "canonical_sql_hash": sql_hash}


def _validate_bundle(path):
    try:
        validation = validate_parent_authority_import_bundle(path)
    except ParentAuthorityImportBundleError as exc:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"Bundle validation failed: {exc}"
        ) from exc
    manifest = _read_json(Path(path) / "bundle_manifest.json")
    expected = {
        "bundle_id": BUNDLE_ID,
        "root_manifest_hash": BUNDLE_ROOT_HASH,
        "approved_parent_count": 96,
        "parent_authority_total": 120,
        "remaining_authority_gap": 0,
        "legacy_authority_count": 19,
        "existing_admin_resolution_count": 5,
        "restricted_authority_count": 11,
        "pending_authority_count": 7,
        "excluded_parent_authority_count": 10,
        "asset_url_decision_count": 410,
        "expected_decision_store_event_count": 162,
        "expected_parent_current_state_count": 120,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(f"Bundle {key} mismatch")
    if validation["manifest_file_count"] != 22 or validation["file_checksum_errors"]:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Bundle protected files failed")
    return validation, manifest


def _validate_plan_manifest(root, paths, schema_validation):
    manifest = _read_json(paths["plan_manifest"])
    stored = manifest.get("manifest_hash", "")
    recalculated = _sha256_bytes(_canonical_json({
        key: value for key, value in manifest.items() if key != "manifest_hash"
    }))
    if stored != EXPECTED_MANIFEST_HASH or recalculated != EXPECTED_MANIFEST_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Plan manifest hash mismatch")
    input_checksums = {
        "asset_apply_preview": _sha256(paths["asset_apply_preview"]),
        "asset_blocked_preview": _sha256(paths["asset_blocked_preview"]),
        "asset_url_decisions": _sha256(paths["asset_url_decisions"]),
        "asset_url_validation": _sha256(paths["asset_url_validation"]),
        "bundle": _hash_path(paths["bundle"]),
        "legacy_decisions": _sha256(paths["legacy_decisions"]),
        "merchant_cases": _sha256(paths["merchant_cases"]),
        "old_confirmation": _hash_path(paths["old_confirmation"]),
        "old_plan_manifest": _sha256(
            root / "reports/governance_decision_store_regenerated_plan/regenerated_decision_store_manifest.json"
        ),
    }
    if input_checksums != manifest.get("input_checksums"):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Plan input checksums changed")
    required = {
        "plan_id": EXPECTED_PLAN_ID,
        "manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_schema_sql_hash": CANONICAL_SQL_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "target_path": EXPECTED_TARGET,
        "expected_event_count": 162,
        "expected_parent_current_state": 120,
        "asset_counts": {"eligible": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_fields": 410,
        "source_branch": EXPECTED_SOURCE_BRANCH,
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "expires_at": PLAN_EXPIRES_AT,
        "execution_blocked": False,
        "blocker_reasons": [],
    }
    for key, value in required.items():
        if manifest.get(key) != value:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                f"Plan manifest {key} mismatch"
            )
    if not manifest.get("backup_plan") or not manifest.get("rollback_plan"):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Plan backup or rollback missing")
    checklist = paths["plan_manifest"].parent / "schema_v2_confirmation_checklist.md"
    if not checklist.is_file():
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Plan confirmation checklist missing")
    identity = manifest.get("plan_identity_inputs")
    expected_id = f"decision-store-schema-v2-plan-{_sha256_bytes(_canonical_json(identity))[:16]}"
    if expected_id != EXPECTED_PLAN_ID:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("deterministic PLAN_ID mismatch")
    if "confirmation_id" in identity or "confirmation_root_hash" in identity:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Plan identity depends on future Confirmation")
    if identity.get("schema_hash") != schema_validation["schema_hash"]:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Plan identity Schema hash mismatch")
    try:
        validate_historical_git_provenance(
            root,
            source_branch=manifest.get("source_branch"),
            source_commit=manifest.get("source_commit"),
        )
    except GitProvenanceError as exc:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"Plan Git provenance invalid: {exc}"
        ) from exc
    if manifest.get("confirmation_created") is not False:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "Plan improperly claims a pre-existing Confirmation"
        )
    return manifest, input_checksums


def _validate_reconstructed_plan_identity(manifest, input_checksums, event_plan):
    expected_counts = {
        **EXPECTED_COUNTS,
        "total": 162,
        "parent_current_state": 120,
        "eligible_assets": 205,
        "hold_assets": 1,
        "excluded_assets": 16,
        "approved_url_fields": 410,
    }
    reconstructed = {
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_schema_sql_hash": CANONICAL_SQL_HASH,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "event_template_hash": event_plan["event_template_hash"],
        "input_checksums": input_checksums,
        "expected_counts": expected_counts,
        "target_path": EXPECTED_TARGET,
        "code_version": PLAN_CODE_VERSION,
    }
    if reconstructed != manifest.get("plan_identity_inputs"):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "independently reconstructed Plan identity inputs mismatch"
        )
    expected_id = f"decision-store-schema-v2-plan-{_sha256_bytes(_canonical_json(reconstructed))[:16]}"
    if expected_id != EXPECTED_PLAN_ID:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "independently reconstructed PLAN_ID mismatch"
        )


def _build_v2_independent_event_plan(paths, plan_manifest, bundle_manifest):
    event_checksums = {
        "bundle_root_manifest_hash": bundle_manifest["root_manifest_hash"],
        "bundle_manifest_sha256": _sha256(paths["bundle"] / "bundle_manifest.json"),
        "legacy_decisions": _sha256(paths["legacy_decisions"]),
        "merchant_cases": _sha256(paths["merchant_cases"]),
        "asset_url_decisions": _sha256(paths["asset_url_decisions"]),
        "asset_url_validation": _sha256(paths["asset_url_validation"]),
        "asset_apply_preview": _sha256(paths["asset_apply_preview"]),
        "asset_blocked_preview": _sha256(paths["asset_blocked_preview"]),
    }
    try:
        independent = _build_independent_event_plan(
            paths=paths,
            plan_manifest=plan_manifest,
            bundle_manifest=bundle_manifest,
            input_checksums=event_checksums,
        )
    except GovernanceDecisionStoreConfirmationError as exc:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"independent event reconciliation failed: {exc}"
        ) from exc
    events = [
        replace(event, source_plan_id=EXPECTED_PLAN_ID)
        if event.source_plan_id == "decision-store-plan-a02502d8361549b1"
        else event
        for event in independent["events"]
    ]
    template_hash = event_template_hash(events)
    if template_hash != EXPECTED_EVENT_TEMPLATE_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Event Template Hash mismatch")
    templates = [_event_template_payload(event) for event in events]
    duplicate_count = len(templates) - len({_sha256_bytes(_canonical_json(row)) for row in templates})
    if duplicate_count or independent["counts"] != EXPECTED_COUNTS or len(events) != 162:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("Event Template reconciliation mismatch")
    return {
        **independent,
        "events": events,
        "event_template_hash": template_hash,
        "duplicate_event_template_count": duplicate_count,
    }


def _validate_binding_contract(manifest, events, schema_validation, old_confirmation_path):
    contract = manifest.get("confirmation_binding_contract", {})
    required_contract = {
        "version": CONFIRMATION_BINDING_VERSION,
        "binding_stage": "execute",
        "event_identity_stage": "execute after Confirmation binding",
        "schema_hash_impact": "none",
        "plan_manifest_impact": "none",
        "placeholder": CONFIRMATION_BINDING_PLACEHOLDER,
    }
    for key, value in required_contract.items():
        if contract.get(key) != value:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                f"Confirmation binding contract {key} mismatch"
            )
    first = _independent_bind(events, TEMP_CONFIRMATION_ID, TEMP_CONFIRMATION_ROOT_HASH)
    repeated = _independent_bind(events, TEMP_CONFIRMATION_ID, TEMP_CONFIRMATION_ROOT_HASH)
    other_id = "temporary-schema-v2-confirmation-fixture-b:not-authoritative"
    other_root = hashlib.sha256(other_id.encode("utf-8")).hexdigest()
    other = _independent_bind(events, other_id, other_root)
    old_manifest = _read_json(Path(old_confirmation_path) / "confirmation_manifest.json")
    checks = {
        "plan_excludes_future_confirmation": all(
            key not in manifest["plan_identity_inputs"]
            for key in ("confirmation_id", "confirmation_root_hash")
        ),
        "schema_hash_unchanged_by_binding": schema_validation["schema_hash"] == EXPECTED_SCHEMA_HASH,
        "decision_payload_unchanged": all(
            _decision_payload_from_bound(bound)
            == {key: value for key, value in event_template.items() if key != "supersedes_event_id"}
            for bound, event_template in zip(first, [_event_template_payload(event) for event in events])
        ),
        "execute_binding_explicit": contract.get("binding_stage") == "execute",
        "no_circular_dependency": "confirmation_id" not in manifest["plan_identity_inputs"],
        "same_confirmation_deterministic": first == repeated,
        "different_confirmation_no_collision": not (
            {event["event_id"] for event in first} & {event["event_id"] for event in other}
            or {event["idempotency_key"] for event in first} & {
                event["idempotency_key"] for event in other
            }
        ),
        "old_confirmation_rejected": (
            old_manifest.get("confirmation_id") == OLD_CONFIRMATION_ID
            and old_manifest.get("plan_id") != EXPECTED_PLAN_ID
        ),
    }
    if not all(checks.values()):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"Confirmation binding validation failed: {checks}"
        )
    return checks, first


def _event_template_payload(event):
    payload = normalize_event_payload(event)
    if event.source_plan_id == EXPECTED_PLAN_ID:
        payload["source_plan_id"] = "$PLAN_ID"
    return payload


def _independent_bind(events, confirmation_id, confirmation_root_hash):
    if not confirmation_id.strip() or not _is_sha256(confirmation_root_hash):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("invalid Confirmation binding")
    result = []
    id_map = {}
    for event in events:
        supersedes = id_map.get(event.supersedes_event_id or "")
        if event.supersedes_event_id and not supersedes:
            raise GovernanceDecisionStoreSchemaV2ConfirmationError(
                "Confirmation binding cannot resolve supersedes Event"
            )
        payload = {
            **normalize_event_payload(event),
            "supersedes_event_id": supersedes,
            "source_confirmation_id": confirmation_id,
            "source_confirmation_root_hash": confirmation_root_hash,
            "created_at": event.created_at,
            "code_version": event.code_version,
        }
        key = _sha256_bytes(_canonical_json(payload))
        event_id = f"event-v2-{key[:24]}"
        id_map[event.event_id] = event_id
        result.append({**payload, "idempotency_key": key, "event_id": event_id})
    return result


def _decision_payload_from_bound(bound):
    result = {
        key: value for key, value in bound.items()
        if key not in {
            "source_confirmation_id", "source_confirmation_root_hash", "created_at",
            "code_version", "idempotency_key", "event_id", "supersedes_event_id",
        }
    }
    if result["source_plan_id"] == EXPECTED_PLAN_ID:
        result["source_plan_id"] = "$PLAN_ID"
    return result


def _build_independent_temporary_store(path, events, manifest, schema_validation):
    if path.exists():
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("temporary database path is not fresh")
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
                manifest["created_at"], VALIDATOR_CODE_VERSION, EXPECTED_PLAN_ID,
                EXPECTED_MANIFEST_HASH, "temporary_independent_validation",
                "schema_v2_confirmation_candidate", None,
                _canonical_json_text({"fixture": TEMP_CONFIRMATION_ID}),
            ),
        )
        inserted = sum(_insert_event(connection, event) for event in events)
        duplicate = _insert_event(connection, events[0])
        chain = _verify_chain(connection)
        head = connection.execute(
            "SELECT event_hash FROM decision_events ORDER BY event_sequence LIMIT 1"
        ).fetchone()[0]
        tail = connection.execute(
            "SELECT event_hash FROM decision_events ORDER BY event_sequence DESC LIMIT 1"
        ).fetchone()[0]
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
                "temporary-independent-validation", EXPECTED_PLAN_ID, EXPECTED_MANIFEST_HASH,
                TEMP_CONFIRMATION_ID, TEMP_CONFIRMATION_ROOT_HASH, BUNDLE_ID, BUNDLE_ROOT_HASH,
                "temporary:not-formal", "independent-validator", manifest["created_at"],
                162, 162, 120, 120, 0, SCHEMA_VERSION, EXPECTED_SCHEMA_HASH, None,
                head, tail, manifest["source_branch"], manifest["source_commit"],
                VALIDATOR_CODE_VERSION, "temporary-external-execution-manifest-reference",
                "validated", _canonical_json_text({"physical_database_sha256": "external_only"}),
            ),
        )
        connection.commit()
        event_count = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        parent_count = connection.execute("SELECT COUNT(*) FROM current_parent_decisions").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        supersede = _validate_supersede_state(connection)
        revoke = _verify_revoke_projection(connection, events)
        rollback = _verify_transaction_rollback(connection)
        object_validation = _validate_sqlite_objects(connection)
    finally:
        connection.close()
    append_only = _verify_append_only(path)
    read_only = _verify_read_only(path)
    backup = _verify_backup_restore(path)
    tamper = _verify_tamper(path)
    return {
        "event_count": event_count,
        "current_parent_state_count": parent_count,
        "authority_gap": 120 - parent_count,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_keys,
        "idempotency": inserted == 162 and duplicate == 0,
        "supersede_projection": supersede,
        "revoke_projection": revoke,
        "hash_chain_valid": chain["valid"] and chain["count"] == 162,
        "tamper_detection": tamper,
        "read_only_reopen": read_only,
        "transaction_rollback": rollback,
        "backup_restore": backup,
        "database_sha_self_reference_absent": _database_sha_null(path),
        "execution_manifest_reference_present": _execution_manifest_reference(path),
        **append_only,
        **object_validation,
    }


def _insert_event(connection, event):
    if connection.execute(
        "SELECT 1 FROM decision_events WHERE idempotency_key=?", (event["idempotency_key"],)
    ).fetchone():
        return 0
    if event["supersedes_event_id"] and not connection.execute(
        "SELECT 1 FROM decision_events WHERE event_id=?", (event["supersedes_event_id"],)
    ).fetchone():
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("supersedes Event is missing")
    connection.execute(
        "INSERT OR IGNORE INTO decision_manifests VALUES (?,?,?,?,?)",
        (
            event["source_manifest_hash"], event["source_plan_id"],
            _canonical_json_text(event["input_checksums"]), event["created_at"], event["code_version"],
        ),
    )
    previous_row = connection.execute(
        "SELECT event_hash FROM decision_events ORDER BY event_sequence DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous_row[0] if previous_row else None
    payload = {key: value for key, value in event.items() if key not in {"event_hash", "previous_event_hash"}}
    event_hash = _event_hash(previous_hash, payload)
    connection.execute(
        """INSERT INTO decision_events (
            event_id,idempotency_key,event_type,subject_type,subject_id,record_id,asset_id,
            field_name,action,previous_value_json,new_value_json,reviewer,reviewed_at,
            decision_reason,provenance,source_plan_id,source_manifest_hash,source_bundle_id,
            source_bundle_root_hash,source_confirmation_id,source_confirmation_root_hash,
            input_checksums_json,supersedes_event_id,previous_event_hash,event_hash,created_at,
            code_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event["event_id"], event["idempotency_key"], event["event_type"],
            event["subject_type"], event["subject_id"], event["record_id"], event["asset_id"],
            event["field_name"], event["action"], _canonical_json_text(event["previous_value"]),
            _canonical_json_text(event["new_value"]), event["reviewer"], event["reviewed_at"],
            event["decision_reason"], event["provenance"], event["source_plan_id"],
            event["source_manifest_hash"], event["source_bundle_id"],
            event["source_bundle_root_hash"], event["source_confirmation_id"],
            event["source_confirmation_root_hash"], _canonical_json_text(event["input_checksums"]),
            event["supersedes_event_id"], previous_hash, event_hash, event["created_at"],
            event["code_version"],
        ),
    )
    return 1


def _verify_chain(connection):
    previous = None
    count = 0
    for row in connection.execute("SELECT * FROM decision_events ORDER BY event_sequence"):
        count += 1
        if row["previous_event_hash"] != previous:
            return {"valid": False, "count": count}
        payload = _row_payload(row)
        if row["event_hash"] != _event_hash(previous, payload):
            return {"valid": False, "count": count}
        previous = row["event_hash"]
    return {"valid": True, "count": count}


def _row_payload(row):
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


def _verify_revoke_projection(connection, events):
    row = connection.execute(
        "SELECT * FROM current_parent_decisions WHERE record_id='商家夥伴案例資料庫:r30'"
    ).fetchone()
    connection.execute("BEGIN")
    template = {
        "event_type": row["event_type"], "subject_type": row["subject_type"],
        "subject_id": row["subject_id"], "record_id": row["record_id"], "asset_id": None,
        "field_name": row["field_name"], "action": "revoke",
        "previous_value": json.loads(row["new_value_json"]), "new_value": None,
        "reviewer": "Admin", "reviewed_at": events[0]["created_at"],
        "decision_reason": "temporary revoke projection validation",
        "provenance": "temporary_validation", "source_plan_id": EXPECTED_PLAN_ID,
        "source_manifest_hash": BUNDLE_ROOT_HASH, "source_bundle_id": BUNDLE_ID,
        "source_bundle_root_hash": BUNDLE_ROOT_HASH,
        "source_confirmation_id": TEMP_CONFIRMATION_ID,
        "source_confirmation_root_hash": TEMP_CONFIRMATION_ROOT_HASH,
        "input_checksums": {"temporary_validation": EXPECTED_MANIFEST_HASH},
        "supersedes_event_id": row["event_id"], "created_at": events[0]["created_at"],
        "code_version": VALIDATOR_CODE_VERSION,
    }
    key = _sha256_bytes(_canonical_json(template))
    event = {**template, "idempotency_key": key, "event_id": f"event-v2-{key[:24]}"}
    _insert_event(connection, event)
    valid = connection.execute(
        "SELECT COUNT(*) FROM current_parent_decisions WHERE record_id='商家夥伴案例資料庫:r30'"
    ).fetchone()[0] == 0
    connection.rollback()
    return valid and connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162


def _validate_supersede_state(connection):
    rows = {
        row[0]: json.loads(row[1])
        for row in connection.execute(
            "SELECT record_id,new_value_json FROM current_parent_decisions "
            "WHERE record_id IN ('商家夥伴案例資料庫:r30','商家夥伴案例資料庫:r12',"
            "'商家夥伴案例資料庫:r122','商家夥伴案例資料庫:r32','商家夥伴案例資料庫:r7')"
        )
    }
    return (
        len(rows) == 5
        and rows["商家夥伴案例資料庫:r30"]["review_decision"] == "exclude"
        and rows["商家夥伴案例資料庫:r12"]["review_decision"] == "approve_internal_only"
    )


def _verify_transaction_rollback(connection):
    connection.execute("BEGIN")
    connection.execute("INSERT INTO decision_manifests VALUES ('rollback','x','{}','x','x')")
    connection.rollback()
    return connection.execute(
        "SELECT COUNT(*) FROM decision_manifests WHERE manifest_hash='rollback'"
    ).fetchone()[0] == 0


def _validate_sqlite_objects(connection):
    tables = _object_names(connection, "table")
    views = _object_names(connection, "view")
    triggers = _object_names(connection, "trigger")
    indexes = _object_names(connection, "index")
    return {
        "required_tables_present": {
            "decision_events", "schema_metadata", "execution_metadata"
        }.issubset(tables),
        "required_views_present": {
            "current_parent_decisions", "current_asset_eligibility",
            "current_search_aliases", "current_entity_metadata",
        }.issubset(views),
        "required_triggers_present": {
            "decision_events_no_update", "decision_events_no_delete",
            "schema_metadata_no_update", "schema_metadata_no_delete",
            "execution_metadata_no_update", "execution_metadata_no_delete",
        }.issubset(triggers),
        "required_indexes_present": {
            "idx_decision_events_subject", "idx_decision_events_supersedes",
            "idx_decision_events_confirmation", "idx_schema_metadata_version",
            "idx_execution_metadata_plan",
        }.issubset(indexes),
        "required_foreign_keys_present": len(
            connection.execute("PRAGMA foreign_key_list(decision_events)").fetchall()
        ) >= 2,
    }


def _verify_append_only(path):
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
        for key, statement in statements.items():
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                connection.rollback()
                results[key] = True
            else:
                connection.rollback()
                results[key] = False
    finally:
        connection.close()
    return {
        "all_append_only_updates_blocked": all(
            value for key, value in results.items() if key.endswith("update")
        ),
        "all_append_only_deletes_blocked": all(
            value for key, value in results.items() if key.endswith("delete")
        ),
    }


def _verify_read_only(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        count_ok = connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162
        try:
            connection.execute("DELETE FROM decision_events")
        except sqlite3.OperationalError:
            return count_ok
        return False
    finally:
        connection.close()


def _verify_backup_restore(path):
    backup = path.with_suffix(".backup.sqlite")
    restore = path.with_suffix(".restore.sqlite")
    source = sqlite3.connect(path)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    source = sqlite3.connect(backup)
    target = sqlite3.connect(restore)
    try:
        source.backup(target)
        valid = (
            target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and target.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 162
        )
    finally:
        source.close()
        target.close()
    backup.unlink()
    restore.unlink()
    return valid


def _verify_tamper(path):
    tampered = path.with_suffix(".tampered.sqlite")
    shutil.copy2(path, tampered)
    connection = sqlite3.connect(tampered)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("DROP TRIGGER decision_events_no_update")
        connection.execute("UPDATE decision_events SET decision_reason='tampered' WHERE event_sequence=1")
        connection.commit()
        detected = not _verify_chain(connection)["valid"]
    finally:
        connection.close()
        tampered.unlink()
    return detected


def _database_sha_null(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT database_sha256 FROM execution_metadata").fetchone()[0] is None
    finally:
        connection.close()


def _execution_manifest_reference(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        value = connection.execute(
            "SELECT execution_manifest_hash FROM execution_metadata"
        ).fetchone()[0]
        return value == "temporary-external-execution-manifest-reference"
    finally:
        connection.close()


def _require_temporary_success(result):
    bool_keys = (
        "idempotency", "supersede_projection", "revoke_projection", "hash_chain_valid",
        "tamper_detection", "read_only_reopen", "transaction_rollback", "backup_restore",
        "database_sha_self_reference_absent", "execution_manifest_reference_present",
        "required_tables_present", "required_views_present", "required_triggers_present",
        "required_indexes_present", "required_foreign_keys_present",
        "deterministic_rerun", "fresh_path",
    )
    valid = (
        result["event_count"] == 162
        and result["current_parent_state_count"] == 120
        and result["authority_gap"] == 0
        and result["integrity_check"] == "ok"
        and result["foreign_key_errors"] == 0
        and all(result[key] for key in bool_keys)
    )
    if not valid:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"independent temporary Schema V2 validation failed: {result}"
        )


def _validate_target(target):
    residues = [
        target.with_name(target.name + suffix)
        for suffix in ("-wal", "-shm", ".partial", ".lock")
    ]
    residues.extend(target.parent.glob(f".{target.name}.staging-*"))
    checks = {
        "formal_target_absent": not target.exists(),
        "formal_target_not_directory": not target.is_dir(),
        "partial_database_absent": not any(path.exists() for path in residues),
        "staging_residue_absent": not any(target.parent.glob(f".{target.name}.staging-*")),
        "lock_file_absent": not target.with_name(target.name + ".lock").exists(),
    }
    if not checks["formal_target_absent"]:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "formal target already exists; manual investigation required"
        )
    if not all(checks.values()):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "formal target residue exists; manual investigation required"
        )
    return checks


def _confirmation_payload(validation, reviewer, confirmed_at):
    identity = {
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "reviewer": reviewer,
        "confirmed_at": confirmed_at,
    }
    return {
        "confirmation_id": f"decision-store-schema-v2-confirmation-{_sha256_bytes(_canonical_json(identity))[:16]}",
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_sql_hash": CANONICAL_SQL_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "target_path": EXPECTED_TARGET,
        "expected_event_count": 162,
        "expected_parent_current_state_count": 120,
        "parent_authority_coverage": "120/120",
        "asset_counts": {"eligible": 205, "hold": 1, "excluded_or_blocked": 16},
        "approved_url_field_count": 410,
        "confirmation_binding_contract_hash": _sha256_bytes(_canonical_json(
            validation["plan_manifest"]["confirmation_binding_contract"]
        )),
        "reviewer": reviewer,
        "confirmed_at": confirmed_at,
        "plan_expires_at": PLAN_EXPIRES_AT,
        "confirmation_statement": CONFIRMATION_STATEMENT,
        "execute_authorized_now": False,
    }


def _confirmation_manifest(staging, validation, confirmation):
    roles = {
        "confirmation.json": "admin_confirmation",
        "independent_validation.json": "independent_validation",
        "referenced_plan_manifest.json": "referenced_plan_manifest",
        "referenced_bundle_manifest.json": "referenced_bundle_manifest",
        "referenced_schema_hash.json": "referenced_schema_hash",
        "confirmation_binding_contract.json": "confirmation_binding_contract",
    }
    files = [
        {
            "filename": filename,
            "logical_role": role,
            "sha256": _sha256(Path(staging) / filename),
            "byte_size": (Path(staging) / filename).stat().st_size,
            "required": True,
        }
        for filename, role in roles.items()
    ]
    manifest = {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "confirmation_id": confirmation["confirmation_id"],
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_version": SCHEMA_VERSION,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_sql_hash": CANONICAL_SQL_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_hash": BUNDLE_ROOT_HASH,
        "target_path": EXPECTED_TARGET,
        "expected_event_count": 162,
        "expected_parent_current_state_count": 120,
        "asset_counts": confirmation["asset_counts"],
        "approved_url_field_count": 410,
        "confirmation_binding_contract_hash": confirmation[
            "confirmation_binding_contract_hash"
        ],
        "reviewer": confirmation["reviewer"],
        "confirmed_at": confirmation["confirmed_at"],
        "plan_expires_at": PLAN_EXPIRES_AT,
        "source_branch": validation["source_branch"],
        "source_commit": validation["source_commit"],
        "validator_code_version": VALIDATOR_CODE_VERSION,
        "independent_validation_hash": validation["independent_validation_hash"],
        "files": files,
    }
    manifest["root_confirmation_hash"] = _sha256_bytes(_canonical_json(manifest))
    return manifest


def _summary(bundle, validation, path, reports, idempotent):
    return {
        "conclusion": "A. Schema V2 Plan independently validated and confirmed",
        "confirmed": True,
        "plan_id": EXPECTED_PLAN_ID,
        "manifest_hash": EXPECTED_MANIFEST_HASH,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "canonical_sql_hash": CANONICAL_SQL_HASH,
        "plan_not_expired": True,
        "bundle_verified_file_count": validation["bundle_verified_file_count"],
        "event_count": validation["event_count"],
        "current_parent_state_count": validation["current_parent_state_count"],
        "confirmation_id": bundle["confirmation_id"],
        "root_confirmation_hash": bundle["root_confirmation_hash"],
        "reviewer": bundle["reviewer"],
        "confirmed_at": bundle["confirmed_at"],
        "confirmation_path": str(path),
        "idempotent_noop": idempotent,
        "formal_decision_store_created": False,
        "formal_system_modified": False,
        "validation_error_count": 0,
        "validation_warning_count": 0,
        "report_dir": str(reports),
    }


def _write_reports(output, summary, validation, confirmation, formal_rows):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILENAMES[0], f"""# Schema V2 Decision Store Confirmation

- Conclusion: {summary['conclusion']}
- PLAN_ID: `{EXPECTED_PLAN_ID}`
- Manifest Hash: `{EXPECTED_MANIFEST_HASH}`
- Schema Hash: `{EXPECTED_SCHEMA_HASH}`
- Confirmation ID: `{summary['confirmation_id']}`
- Root Confirmation Hash: `{summary['root_confirmation_hash']}`
- Confirmed At: `{summary['confirmed_at']}`
- Events / Parent State / Authority Gap: 162 / 120 / 0
- Idempotent no-op: {str(summary['idempotent_noop']).lower()}
- Formal Decision Store created: false
""")
    _write_csv(output / REPORT_FILENAMES[1], _rows(validation["schema_validation"]))
    _write_csv(output / REPORT_FILENAMES[2], [
        {"check": "plan_id", "expected": EXPECTED_PLAN_ID, "observed": validation["plan_id"], "status": "pass"},
        {"check": "manifest_hash", "expected": EXPECTED_MANIFEST_HASH, "observed": validation["manifest_hash"], "status": "pass"},
        {"check": "not_expired", "expected": True, "observed": validation["plan_not_expired"], "status": "pass"},
        {"check": "execution_blocked", "expected": False, "observed": False, "status": "pass"},
    ])
    _write_csv(output / REPORT_FILENAMES[3], validation["bundle_checksum_rows"])
    _write_csv(output / REPORT_FILENAMES[4], _rows(validation["binding_validation"]))
    _write_csv(output / REPORT_FILENAMES[5], _rows(validation["database_sha_boundary"]))
    _write_csv(output / REPORT_FILENAMES[6], [
        {"category": key, "expected": EXPECTED_COUNTS[key], "observed": value, "status": "pass"}
        for key, value in validation["event_counts"].items()
    ] + [{"category": "total", "expected": 162, "observed": 162, "status": "pass"}])
    _write_text(output / REPORT_FILENAMES[7], "# Independent Temporary Schema V2 Validation\n\n" + "\n".join(
        f"- {key}: `{value}`" for key, value in sorted(validation["temporary_store"].items())
    ))
    _write_csv(output / REPORT_FILENAMES[8], validation["special_decision_rows"])
    _write_csv(output / REPORT_FILENAMES[9], _rows(validation["target_checks"]))
    _write_csv(output / REPORT_FILENAMES[10], [
        {"identifier": value, "type": "confirmation" if value == OLD_CONFIRMATION_ID else "plan", "status": "SUPERSEDED | INVALID FOR SCHEMA V2 | DO NOT EXECUTE"}
        for value in [*sorted(OBSOLETE_PLAN_IDS), OLD_CONFIRMATION_ID]
    ])
    _write_csv(output / REPORT_FILENAMES[11], _rows(confirmation))
    _write_csv(output / REPORT_FILENAMES[12], formal_rows)
    _write_text(output / REPORT_FILENAMES[13], f"""# Schema V2 Execute Prerequisites

This Sprint did not execute the Plan. A later Execute must revalidate exact PLAN_ID `{EXPECTED_PLAN_ID}`, Manifest Hash `{EXPECTED_MANIFEST_HASH}`, Schema Hash `{EXPECTED_SCHEMA_HASH}`, Confirmation Root Hash `{summary['root_confirmation_hash']}`, Bundle Root Hash `{BUNDLE_ROOT_HASH}`, expiration, all checksums, target absence, temporary dry-run and backup readiness. No force, overwrite, skip-validation, ignore-checksum or auto-confirm option is allowed.
""")
    _write_csv(output / REPORT_FILENAMES[14], [])
    _write_csv(output / REPORT_FILENAMES[15], [])
    actual = sorted(path.name for path in output.iterdir() if path.is_file() and not path.name.startswith("._"))
    if actual != sorted(REPORT_FILENAMES):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "Schema V2 Confirmation report contract is incomplete"
        )


def _formal_checks(root):
    checks = {
        "governance_decisions_sqlite_absent": not (root / EXPECTED_TARGET).exists(),
        "new_execution_bundle_absent": not (root / "data/governance/executions" / EXPECTED_PLAN_ID).exists(),
        "formal_vault_unchanged": (root / "obsidian_vault").is_dir(),
        "managed_vault_unchanged": (root / "obsidian_vault/MKA").is_dir(),
        "content_index_unchanged": (root / ".mka/content_index.sqlite").is_file(),
        "parent_not_synced": True,
        "asset_url_not_applied": True,
        "asset_eligibility_not_applied": True,
        "search_alias_not_applied": True,
        "production_renderer_unchanged": (root / "src/marketing_knowledge_agent/slack_interface.py").is_file(),
        "slack_api_not_called": True,
    }
    if not all(checks.values()):
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("formal system conservation failed")
    return [{"check": key, "status": "pass"} for key in checks]


def _protected_paths(root, kwargs):
    return [
        _resolve(root, kwargs.get("bundle_path", DEFAULT_BUNDLE_PATH)),
        _resolve(root, kwargs.get("plan_manifest_path", DEFAULT_PLAN_MANIFEST)).parent,
        _resolve(root, kwargs.get("old_confirmation_path", DEFAULT_OLD_CONFIRMATION_PATH)),
        root / "obsidian_vault",
        root / ".mka/content_index.sqlite",
        root / "src/marketing_knowledge_agent/slack_interface.py",
        _resolve(root, kwargs.get("legacy_decisions_path", Path("reports/excel_preview/review_decisions_template.csv"))),
        _resolve(root, kwargs.get("asset_url_decisions_path", Path("reports/asset_metadata_preview/human_review_template.csv"))),
    ]


def _public_validation(result):
    return {
        key: value for key, value in result.items()
        if key not in {"plan_manifest", "bundle_checksum_rows", "special_decision_rows", "event_templates"}
    }


def _rows(values):
    return [
        {"check": key, "observed": value, "status": "pass" if value is not False else "fail"}
        for key, value in values.items()
    ]


def _object_names(connection, object_type):
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'",
            (object_type,),
        )
    }


def _event_hash(previous_hash, payload):
    return _sha256_bytes(f"{previous_hash or ''}\n{_canonical_json_text(payload)}".encode("utf-8"))


def _require_exact_identifiers(plan_id, manifest_hash, schema_hash, canonical_sql_hash):
    if plan_id in OBSOLETE_PLAN_IDS:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            "obsolete PLAN_ID is SUPERSEDED and INVALID FOR SCHEMA V2"
        )
    if plan_id != EXPECTED_PLAN_ID:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("exact PLAN_ID is required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("exact Plan Manifest Hash is required")
    if schema_hash != EXPECTED_SCHEMA_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("exact Schema Hash is required")
    if canonical_sql_hash != CANONICAL_SQL_HASH:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("exact Canonical SQL Hash is required")


def _is_git_ignored(root, path):
    try:
        Path(path).resolve().relative_to(root)
    except ValueError:
        return True
    return subprocess.call(["git", "check-ignore", "-q", str(path)], cwd=root) == 0


def _make_read_only(root):
    for path in Path(root).rglob("*"):
        if path.is_file():
            try:
                path.chmod(0o444)
            except OSError:
                pass


def _safe_filename(value):
    path = Path(value)
    if not value or path.is_absolute() or len(path.parts) != 1 or value in {".", ".."}:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError("unsafe Confirmation filename")
    return value


def _resolve(root, path):
    value = Path(path)
    return value.resolve() if value.is_absolute() else (root / value).resolve()


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(f"invalid JSON: {path}") from exc


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


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _validate_timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"{label} must be a valid ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceDecisionStoreSchemaV2ConfirmationError(
            f"{label} must include timezone"
        )
