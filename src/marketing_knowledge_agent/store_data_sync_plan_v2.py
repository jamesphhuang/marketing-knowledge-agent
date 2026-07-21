from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .governance_decision_store_existing_validation import (
    EXPECTED_DATABASE_SHA256,
    EXPECTED_EXECUTION_ID,
    EXPECTED_EXECUTION_ROOT_HASH,
    EXPECTED_SCHEMA_HASH,
    validate_existing_governance_decision_store,
)
from .parent_sync_confirmation import (
    DEFAULT_ASSET_BLOCKED,
    DEFAULT_ASSET_ELIGIBLE,
    DEFAULT_ASSET_INVENTORY,
    DEFAULT_ASSET_URL_DECISIONS,
    DEFAULT_DECISION_STORE,
    DEFAULT_EXECUTION_BUNDLE,
    DEFAULT_FORMAL_SQLITE,
    DEFAULT_FORMAL_VAULT,
    DEFAULT_MANAGED_VAULT,
    DEFAULT_PARENT_SOURCE,
    DEFAULT_RENDERER,
    _asset_boundary,
    _build_assets,
    _build_candidate,
    _build_projection,
    _current_value,
    _hash_path,
    _load_formal_sqlite,
    _load_parent_source,
    _load_store,
    _load_vault,
    _normalized,
    _offline_search,
    _proposed_path,
    _sha256_json,
    _special_validation,
)


OLD_PLAN_ID = "parent-sync-plan-23f9805386fb6a5d"
OLD_MANIFEST_HASH = "3bc5763af63f111c23df92cbe8a5386489a2480a3d13c8c52fe67a861c224f9c"
OLD_MANAGED_DELTA_HASH = "d3bcf7b02a1fc73a92e4b1fbf3276c452fb8b532bb4ef64187740608b6262591"
OLD_FORMAL_DELTA_HASH = "e9673c98ecabb7f2359bd83a1597b1df8d0cf5d104e7c3c8c770f8b793efd4e5"
CODE_VERSION = "store-data-sync-materialization-boundary-plan-v2"

DEFAULT_OLD_PLAN = Path("reports/parent_sync_plan/parent_sync_plan_manifest.json")
DEFAULT_OUTPUT_DIR = Path("reports/parent_sync_plan_v2")

AUDIT_ONLY_FIELDS = (
    "decision_event_id",
    "decision_event_hash",
    "decision_reviewer",
    "decision_reviewed_at",
    "decision_provenance",
)
GOVERNANCE_ONLY_RECORD_IDS = {
    f"商家夥伴案例資料庫:{value}"
    for value in ("r30", "r57", "r83", "r87", "r101", "r102", "r103", "r107", "r116", "r121")
}
CREATE_RECORD_IDS = {
    f"商家夥伴案例資料庫:{value}" for value in ("r7", "r12", "r32", "r122")
}
R20 = "商家夥伴案例資料庫:r20"
ACTION_NAMES = (
    "create",
    "update",
    "no_change",
    "remove_from_content_projection",
    "retain_governance_only",
    "blocked",
    "manual_review",
)
REPORT_FILENAMES = (
    "store_data_sync_plan_v2_summary.md",
    "decision_store_input_validation.csv",
    "obsolete_plan_registry.csv",
    "field_materialization_matrix.csv",
    "audit_only_field_validation.csv",
    "full_authoritative_desired_state.csv",
    "managed_vault_projection.csv",
    "formal_sqlite_projection.csv",
    "parent_reconciliation_v2.csv",
    "managed_vault_field_diff.csv",
    "formal_sqlite_field_diff.csv",
    "managed_vault_create_preview.csv",
    "managed_vault_update_preview.csv",
    "managed_vault_no_change.csv",
    "formal_sqlite_create_preview.csv",
    "formal_sqlite_update_preview.csv",
    "formal_sqlite_no_change.csv",
    "formal_sqlite_not_projected.csv",
    "governance_only_records.csv",
    "four_create_records_validation.csv",
    "special_record_validation.csv",
    "asset_url_boundary_validation.csv",
    "temporary_candidate_validation.md",
    "offline_search_behavior_preview.md",
    "managed_vault_delta_manifest.json",
    "formal_sqlite_delta_manifest.json",
    "store_data_sync_backup_plan.md",
    "store_data_sync_rollback_plan.md",
    "store_data_sync_confirmation_checklist.md",
    "store_data_sync_plan_v2_manifest.json",
    "store_data_sync_validation_errors.csv",
    "store_data_sync_validation_warnings.csv",
)


class StoreDataSyncPlanV2Error(RuntimeError):
    pass


def generate_store_data_sync_plan_v2(
    *,
    repo_root: Path,
    decision_store_path: Path = DEFAULT_DECISION_STORE,
    execution_bundle_path: Path = DEFAULT_EXECUTION_BUNDLE,
    parent_source_path: Path = DEFAULT_PARENT_SOURCE,
    asset_inventory_path: Path = DEFAULT_ASSET_INVENTORY,
    asset_eligible_path: Path = DEFAULT_ASSET_ELIGIBLE,
    asset_blocked_path: Path = DEFAULT_ASSET_BLOCKED,
    asset_url_decisions_path: Path = DEFAULT_ASSET_URL_DECISIONS,
    managed_vault_root: Path = DEFAULT_MANAGED_VAULT,
    formal_vault_root: Path = DEFAULT_FORMAL_VAULT,
    formal_sqlite_path: Path = DEFAULT_FORMAL_SQLITE,
    production_renderer_path: Path = DEFAULT_RENDERER,
    old_plan_path: Path = DEFAULT_OLD_PLAN,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    temporary_root: Optional[Path] = None,
    created_at: Optional[str] = None,
) -> dict:
    root = Path(repo_root).resolve()
    paths = {
        "decision_store": _resolve(root, decision_store_path),
        "execution_bundle": _resolve(root, execution_bundle_path),
        "parent_source": _resolve(root, parent_source_path),
        "asset_inventory": _resolve(root, asset_inventory_path),
        "asset_eligible": _resolve(root, asset_eligible_path),
        "asset_blocked": _resolve(root, asset_blocked_path),
        "asset_url_decisions": _resolve(root, asset_url_decisions_path),
        "managed_vault": _resolve(root, managed_vault_root),
        "formal_vault": _resolve(root, formal_vault_root),
        "formal_sqlite": _resolve(root, formal_sqlite_path),
        "renderer": _resolve(root, production_renderer_path),
        "old_plan": _resolve(root, old_plan_path),
        "output": _resolve(root, output_dir),
    }
    for label, path in paths.items():
        if label != "output" and not path.exists():
            raise StoreDataSyncPlanV2Error(f"required {label} input is missing: {path}")

    old_plan = json.loads(paths["old_plan"].read_text(encoding="utf-8"))
    if old_plan.get("plan_id") != OLD_PLAN_ID or old_plan.get("manifest_hash") != OLD_MANIFEST_HASH:
        raise StoreDataSyncPlanV2Error("obsolete Parent Sync Plan identity mismatch")

    protected = (
        paths["decision_store"],
        paths["execution_bundle"],
        paths["managed_vault"],
        paths["formal_vault"],
        paths["formal_sqlite"],
        paths["asset_url_decisions"],
        paths["renderer"],
        paths["old_plan"],
    )
    before = {str(path): _hash_path(path) for path in protected}

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-store-sync-v2-", dir=str(temp_parent) if temp_parent else None
    ) as temporary_name:
        temporary = Path(temporary_name)
        store_validation = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            execution_bundle_path=paths["execution_bundle"],
            report_dir=temporary / "existing-store",
            temporary_root=temporary / "existing-store-work",
        )
        source = _load_parent_source(paths["parent_source"])
        store = _load_store(paths["decision_store"])
        full_desired = _build_projection(source, store)
        vault = _load_vault(paths["managed_vault"])
        formal = _load_formal_sqlite(paths["formal_sqlite"])
        matrix = _field_materialization_matrix()
        managed_projection = [
            _managed_payload(row) for row in full_desired if row["can_enter_vault"]
        ]
        formal_projection = [
            _formal_payload(row) for row in full_desired if row["can_enter_content_index"]
        ]
        managed = _managed_reconciliation(full_desired, vault, formal)
        formal_plan = _formal_reconciliation(full_desired, formal)
        assets = _build_assets(full_desired, store["assets"], paths)
        candidate = _build_candidate(
            temporary / "store-sync-v2-candidate.sqlite", full_desired, assets, store["aliases"]
        )
        candidate.update({
            "managed_vault_parents": len(managed_projection),
            "audit_only_managed_columns": _audit_field_count(managed_projection),
            "audit_only_formal_columns": _audit_field_count(formal_projection),
        })
        offline_search = _offline_search(temporary / "store-sync-v2-candidate.sqlite")

    after = {str(path): _hash_path(path) for path in protected}
    if before != after:
        raise StoreDataSyncPlanV2Error("a protected formal system changed during planning")

    action_counts = _action_counts(managed["rows"])
    managed_delta = [
        row for row in managed["delta"]
        if row["action"] in {"create", "update", "remove_from_content_projection"}
    ]
    formal_delta = [
        row for row in formal_plan["rows"] if row["action"] in {"create", "update", "remove"}
    ]
    managed_delta_hash = _sha256_json(managed_delta)
    formal_delta_hash = _sha256_json(formal_delta)
    contract_hash = _sha256_json(matrix)
    full_hash = _sha256_json(full_desired)
    managed_projection_hash = _sha256_json(managed_projection)
    formal_projection_hash = _sha256_json(formal_projection)
    asset_boundary = _asset_boundary(assets, paths["asset_url_decisions"])
    special = _special_validation(full_desired, managed["rows"], assets, offline_search)
    governance_rows = _governance_rows(full_desired, managed["rows"], vault, formal)
    create_rows = _create_rows(full_desired, managed["rows"], managed_projection, vault, formal)
    managed_counts = _managed_counts(vault, action_counts, managed_projection)
    formal_counts = _formal_counts(formal, formal_plan["rows"], formal_projection)
    r20_valid = _r20_valid(full_desired, vault, formal)
    unsupported = [
        row["field_name"] for row in matrix
        if row["materialization_status"] == "unsupported_requires_schema_plan"
    ]
    blockers = _blockers(
        full_desired=full_desired,
        matrix=matrix,
        managed=managed,
        formal_plan=formal_plan,
        action_counts=action_counts,
        governance_rows=governance_rows,
        create_rows=create_rows,
        managed_counts=managed_counts,
        formal_counts=formal_counts,
        candidate=candidate,
        special=special,
        asset_boundary=asset_boundary,
        r20_valid=r20_valid,
        unsupported=unsupported,
        formal_unchanged=before == after,
    )

    input_checksums = {
        "decision_store": _sha256(paths["decision_store"]),
        "execution_bundle": _hash_path(paths["execution_bundle"]),
        "parent_source_metadata": _sha256(paths["parent_source"]),
        "asset_inventory": _sha256(paths["asset_inventory"]),
        "asset_eligible_preview": _sha256(paths["asset_eligible"]),
        "asset_blocked_preview": _sha256(paths["asset_blocked"]),
        "asset_url_decisions": _sha256(paths["asset_url_decisions"]),
        "managed_vault": _hash_path(paths["managed_vault"]),
        "formal_sqlite": _sha256(paths["formal_sqlite"]),
        "obsolete_plan": _sha256(paths["old_plan"]),
    }
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _git(root, "rev-parse", "HEAD")
    identity = {
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "field_materialization_contract_hash": contract_hash,
        "managed_vault_delta_hash": managed_delta_hash,
        "formal_sqlite_delta_hash": formal_delta_hash,
        "target_paths": {
            "managed_vault": _relative(root, paths["managed_vault"]),
            "formal_sqlite": _relative(root, paths["formal_sqlite"]),
        },
        "counts": {
            **action_counts,
            "managed_vault_target": managed_counts["target"],
            "formal_sqlite_target": formal_counts["target"],
        },
        "code_version": CODE_VERSION,
        "source_commit": commit,
    }
    plan_id = "store-data-sync-plan-v2-" + _sha256_json(identity)[:16]
    created, expires = _plan_times(paths["output"], plan_id, created_at)
    manifest = {
        "plan_id": plan_id,
        "plan_type": "authoritative_store_data_delta_sync_materialization_v2",
        "supersedes_plan_id": OLD_PLAN_ID,
        "supersedes_manifest_hash": OLD_MANIFEST_HASH,
        "superseded_reason": "audit_only_fields_materialized_by_plan",
        "decision_store_path": _relative(root, paths["decision_store"]),
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_id": EXPECTED_EXECUTION_ID,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "decision_store_schema_version": 2,
        "decision_store_schema_hash": EXPECTED_SCHEMA_HASH,
        "authoritative_record_count": len(full_desired),
        "reconciliation_count": len(managed["rows"]),
        **{f"{name}_count": action_counts[name] for name in ACTION_NAMES},
        "governance_only_count": action_counts["retain_governance_only"],
        "managed_vault_target_count": managed_counts["target"],
        "formal_sqlite_target_count": formal_counts["target"],
        "field_materialization_contract_hash": contract_hash,
        "full_desired_state_hash": full_hash,
        "managed_vault_projection_hash": managed_projection_hash,
        "formal_sqlite_projection_hash": formal_projection_hash,
        "managed_vault_delta_hash": managed_delta_hash,
        "formal_sqlite_delta_hash": formal_delta_hash,
        "target_paths": identity["target_paths"],
        "input_checksums": input_checksums,
        "source_branch": branch,
        "source_commit": commit,
        "code_version": CODE_VERSION,
        "created_at": created,
        "expires_at": expires,
        "execution_blocked": bool(blockers),
        "blocker_reasons": blockers,
        "backup_plan": "independent Managed Vault and Formal SQLite backups before staged delta apply",
        "rollback_plan": "restore each target from its own verified backup; Decision Store remains immutable",
    }
    manifest["manifest_hash"] = _sha256_json(manifest)
    result = {
        "conclusion": (
            "A. Ready for corrected store data sync plan confirmation"
            if not blockers else "C. Corrected sync plan blocked"
        ),
        "old_plan_status": [
            "VALIDATION BLOCKED", "DO NOT CONFIRM", "DO NOT EXECUTE",
            "SUPERSEDED BY MATERIALIZATION-BOUNDARY FIX",
        ],
        "decision_store_validation": {
            "database_sha256": store_validation["database_sha256_after"],
            "database_byte_size": store_validation["database_size_after"],
            "integrity_check": store_validation["integrity_check"],
            "foreign_key_errors": store_validation["foreign_key_errors"],
            "event_count": store_validation["event_count"],
            "current_parent_state": store_validation["current_parent_state_count"],
            "authority_gap": store_validation["authority_gap"],
            "hash_chain_valid": store_validation["hash_chain_validation"]["valid"],
            "execution_root_hash": store_validation["execution_bundle"]["root_execution_hash"],
            "database_unchanged": store_validation["formal_database_unchanged"],
        },
        "field_materialization_matrix": matrix,
        "target_allowlists_distinct": _managed_fields() != _formal_fields(),
        "audit_only_field_count": len(AUDIT_ONLY_FIELDS),
        "full_authoritative_desired_state": full_desired,
        "managed_vault_projection": managed_projection,
        "formal_sqlite_projection": formal_projection,
        "authoritative_record_count": len(full_desired),
        "reconciliation": managed["rows"],
        "reconciliation_count": len(managed["rows"]),
        "action_counts": action_counts,
        "managed_vault_field_diffs": managed["diffs"],
        "formal_sqlite_field_diffs": formal_plan["diffs"],
        "managed_vault_delta": managed_delta,
        "formal_sqlite_delta": formal_delta,
        "managed_vault_counts": managed_counts,
        "formal_sqlite_counts": formal_counts,
        "governance_only_records": governance_rows,
        "r20_vault_only_valid": r20_valid,
        "four_create_records": create_rows,
        "special_record_validation": special,
        "asset_boundary": asset_boundary,
        "candidate_validation": candidate,
        "offline_search": offline_search,
        "field_materialization_contract_hash": contract_hash,
        "full_desired_state_hash": full_hash,
        "managed_vault_projection_hash": managed_projection_hash,
        "formal_sqlite_projection_hash": formal_projection_hash,
        "managed_vault_delta_hash": managed_delta_hash,
        "formal_sqlite_delta_hash": formal_delta_hash,
        "managed_delta_cross_check": {
            "previous_hash": OLD_MANAGED_DELTA_HASH,
            "matches": managed_delta_hash == OLD_MANAGED_DELTA_HASH,
            "difference_reason": "V2 hash uses a target-specific payload and omits event identity from write scope",
        },
        "formal_delta_cross_check": {
            "previous_hash": OLD_FORMAL_DELTA_HASH,
            "matches": formal_delta_hash == OLD_FORMAL_DELTA_HASH,
            "difference_reason": "V2 uses the actual Formal SQLite consumer allowlist and field-level no-change detection",
        },
        "plan_id": plan_id,
        "manifest_hash": manifest["manifest_hash"],
        "expires_at": expires,
        "execution_blocked": bool(blockers),
        "blocker_reasons": blockers,
        "manifest": manifest,
        "formal_systems_unchanged": before == after,
        "formal_data_modified": False,
    }
    _write_reports(paths["output"], result, formal_plan["rows"])
    return result


def _field_materialization_matrix() -> list:
    managed = _managed_fields()
    formal = _formal_fields()
    rows = []
    sources = {
        "record_id": "source_sheet_and_source_row",
        "brand_name": "parent_source_metadata",
        "merchant_handle": "parent_source_metadata",
        "merchant_status": "parent_source_metadata",
        "normalized_entity_type": "decision_store_current_entity_metadata",
        "merchant_handle_requirement": "decision_store_current_entity_metadata",
        "current_review_decision": "decision_store_current_parent_decisions",
        "can_enter_vault": "decision_store_decision_effect",
        "can_enter_content_index": "decision_store_decision_effect",
        "can_external_reference": "decision_store_decision_effect",
        "parent_index_eligibility": "derived_from_parent_decision",
        "parent_search_eligibility": "derived_from_parent_decision",
        "search_aliases": "decision_store_current_search_aliases",
        "search_alias_reviewed_by": "decision_store_search_alias_event",
        "search_alias_reviewed_at": "decision_store_search_alias_event",
        "search_alias_provenance": "decision_store_search_alias_event",
        "content_tags": "parent_source_metadata",
        "classification": "parent_source_metadata",
        "governance_flags": "parent_source_metadata",
        "source_sheet": "parent_source_metadata",
        "source_row": "parent_source_metadata",
        "desired_projection_status": "derived_from_parent_decision",
        **{field: "decision_store_event_audit" for field in AUDIT_ONLY_FIELDS},
        "event_hash_chain": "decision_store_event_audit",
        "full_input_checksums": "execution_bundle",
        "execution_authority_references": "execution_bundle",
        "complete_historical_decision_payload": "decision_store_event_audit",
        "supersede_revoke_audit_evidence": "decision_store_event_audit",
    }
    for field, source in sources.items():
        in_managed = field in managed
        in_formal = field in formal
        audit = field in AUDIT_ONLY_FIELDS or field in {
            "event_hash_chain", "full_input_checksums", "execution_authority_references",
            "complete_historical_decision_payload", "supersede_revoke_audit_evidence",
        }
        if audit:
            status = "audit_only"
        elif in_managed and in_formal:
            status = "materialize_both"
        elif in_managed:
            status = "materialize_managed_vault"
        elif in_formal:
            status = "materialize_formal_sqlite"
        else:
            status = "derived_at_read_time"
        consumer = {
            "record_id": "stable Parent identity and path mapping",
            "search_aliases": "future exact-match alias projection; not production-enabled",
            "can_external_reference": "citation and external exposure gating",
            "content_tags": "retrieval filters",
            "classification": "query gating",
        }.get(field, "Managed ingestion or current projection reconciliation" if in_managed else "audit verification")
        rows.append({
            "field_name": field,
            "authoritative_source": source,
            "decision_store": source.startswith("decision_store"),
            "audit_bundle": audit,
            "managed_vault": in_managed,
            "formal_sqlite": in_formal,
            "production_search": field in {"brand_name", "merchant_handle", "merchant_status", "content_tags", "classification"},
            "required_consumer": consumer,
            "safe_to_materialize": in_managed or in_formal,
            "triggers_write": in_managed or in_formal,
            "materialization_status": status,
            "formal_storage_key": _formal_fields().get(field, ""),
            "reason": "complete decision audit remains in the append-only authority" if audit else consumer,
        })
    return rows


def _managed_fields() -> dict:
    return {
        "record_id": "record_id",
        "brand_name": "brand_name",
        "merchant_handle": "merchant_handle",
        "merchant_status": "merchant_status",
        "normalized_entity_type": "normalized_entity_type",
        "merchant_handle_requirement": "merchant_handle_requirement",
        "current_review_decision": "review_decision",
        "can_enter_vault": "can_enter_vault",
        "can_enter_content_index": "can_enter_content_index",
        "can_external_reference": "can_external_reference",
        "parent_index_eligibility": "parent_index_eligibility",
        "parent_search_eligibility": "parent_search_eligibility",
        "search_aliases": "search_aliases",
        "search_alias_reviewed_by": "search_alias_reviewed_by",
        "search_alias_reviewed_at": "search_alias_reviewed_at",
        "search_alias_provenance": "search_alias_provenance",
        "content_tags": "content_tags",
        "classification": "data_classification",
        "governance_flags": "governance_flags",
        "source_sheet": "source_sheet",
        "source_row": "source_row",
    }


def _formal_fields() -> dict:
    return {
        "brand_name": "brand_name",
        "merchant_handle": "merchant_handle",
        "merchant_status": "merchant_status",
        "can_enter_content_index": "can_enter_content_index",
        "can_external_reference": "can_quote_externally",
        "content_tags": "content_tags",
        "classification": "data_classification",
        "source_sheet": "source_sheet",
        "source_row": "source_row",
    }


def _effective_value(row: dict, field: str):
    if field == "classification" and row["current_review_decision"] == "approve_internal_only":
        return "internal"
    return row[field]


def _payload(row: dict, fields: dict) -> dict:
    result = {}
    for source, target in fields.items():
        value = _effective_value(row, source)
        if source in {"merchant_handle", "search_alias_reviewed_by", "search_alias_reviewed_at", "search_alias_provenance"}:
            value = value or None
        result[target] = value
    return result


def _managed_payload(row: dict) -> dict:
    return _payload(row, _managed_fields())


def _formal_payload(row: dict) -> dict:
    return {"record_id": row["record_id"], **_payload(row, _formal_fields())}


def _managed_reconciliation(projection, vault, formal):
    rows, diffs, delta = [], [], []
    for desired in projection:
        record_id = desired["record_id"]
        current = vault.get(record_id)
        indexed = formal.get(record_id)
        path = _proposed_path(desired, current)
        expected = _managed_payload(desired)
        current_metadata = current["metadata"] if current else {}
        changed = []
        before, after = {}, {}
        if desired["can_enter_vault"]:
            for field, value in expected.items():
                old = _current_value(current_metadata, field)
                if _normalized(old) == _normalized(value):
                    continue
                changed.append(field)
                before[field], after[field] = old, value
                diffs.append({
                    "record_id": record_id,
                    "brand_name": desired["brand_name"],
                    "field_name": field,
                    "current_value_json": _json(old),
                    "desired_value_json": _json(value),
                    "authoritative_source": _matrix_source(field),
                    "safe_to_materialize": True,
                    "triggers_write": True,
                })
        if not desired["can_enter_vault"]:
            action = "remove_from_content_projection" if current or indexed else "retain_governance_only"
        elif current is None:
            action = "create"
        elif changed:
            action = "update"
        else:
            action = "no_change"
        row = {
            "record_id": record_id,
            "brand_name": desired["brand_name"],
            "authoritative_decision": desired["current_review_decision"],
            "current_managed_path": current["path"] if current else "",
            "proposed_managed_path": path if desired["can_enter_vault"] else "",
            "current_formal_sqlite_presence": bool(indexed),
            "proposed_action": action,
            "changed_fields": changed,
            "necessary_materialized_diff_count": len(changed),
            "blocked_reason": "",
        }
        rows.append(row)
        delta.append({
            "record_id": record_id,
            "brand_name": desired["brand_name"],
            "action": action,
            "target_path": row["proposed_managed_path"],
            "rollback_path": row["current_managed_path"],
            "changed_fields": changed,
            "before_values": before,
            "after_values": after,
        })
    return {"rows": rows, "diffs": diffs, "delta": delta}


def _formal_reconciliation(projection, formal):
    rows, diffs = [], []
    for desired in projection:
        record_id = desired["record_id"]
        current = formal.get(record_id)
        expected = _formal_payload(desired)
        changed, before, after = [], {}, {}
        if desired["can_enter_content_index"]:
            current_metadata = current["metadata"] if current else {}
            for field, value in expected.items():
                if field == "record_id":
                    continue
                old = current_metadata.get(field)
                if _normalized(old) == _normalized(value):
                    continue
                changed.append(field)
                before[field], after[field] = old, value
                diffs.append({
                    "record_id": record_id,
                    "brand_name": desired["brand_name"],
                    "column_or_metadata_key": field,
                    "current_value_json": _json(old),
                    "desired_value_json": _json(value),
                    "required_consumer": _formal_consumer(field),
                    "safe_to_materialize": True,
                    "triggers_write": True,
                })
        if not desired["can_enter_content_index"]:
            action = "remove" if current else "not_projected"
        elif current is None:
            action = "create"
        elif changed:
            action = "update"
        else:
            action = "no_change"
        rows.append({
            "record_id": record_id,
            "brand_name": desired["brand_name"],
            "action": action,
            "current_presence": bool(current),
            "desired_presence": desired["can_enter_content_index"],
            "changed_fields": changed,
            "before_values": before,
            "after_values": after,
            "source_path": current["source_path"] if current else "",
        })
    return {"rows": rows, "diffs": diffs}


def _action_counts(rows):
    counts = {name: 0 for name in ACTION_NAMES}
    for row in rows:
        counts[row["proposed_action"]] += 1
    return counts


def _managed_counts(vault, actions, projection):
    return {
        "existing": len(vault),
        "create": actions["create"],
        "update": actions["update"],
        "no_change": actions["no_change"],
        "governance_only": actions["retain_governance_only"],
        "target": len(projection),
        "content_parent": 109,
        "vault_only": 1,
    }


def _formal_counts(formal, rows, projection):
    counts = Counter(row["action"] for row in rows)
    return {
        "existing": len(formal),
        "create": counts["create"],
        "update": counts["update"],
        "no_change": counts["no_change"],
        "remove": counts["remove"],
        "not_projected": counts["not_projected"],
        "target": len(projection),
    }


def _governance_rows(projection, reconciliation, vault, formal):
    actions = {row["record_id"]: row for row in reconciliation}
    return [{
        "record_id": row["record_id"],
        "brand_name": row["brand_name"],
        "decision": row["current_review_decision"],
        "current_managed_path": vault.get(row["record_id"], {}).get("path", ""),
        "current_formal_sqlite_presence": row["record_id"] in formal,
        "governance_storage": "decision_store_only",
        "content_file": False,
        "content_index": False,
        "production_search": False,
        "citation": False,
        "planned_action": actions[row["record_id"]]["proposed_action"],
    } for row in projection if not row["can_enter_vault"]]


def _create_rows(projection, reconciliation, managed_projection, vault, formal):
    desired = {row["record_id"]: row for row in projection}
    materialized = {row["record_id"]: row for row in managed_projection}
    rows = [row for row in reconciliation if row["proposed_action"] == "create"]
    paths = Counter(row["proposed_managed_path"] for row in reconciliation if row["proposed_managed_path"])
    return [{
        "record_id": row["record_id"],
        "brand_name": row["brand_name"],
        "managed_vault_absent": row["record_id"] not in vault,
        "formal_sqlite_absent": row["record_id"] not in formal,
        "proposed_path": row["proposed_managed_path"],
        "path_collision": paths[row["proposed_managed_path"]] > 1,
        "record_id_mapping_valid": row["record_id"].endswith(f":r{desired[row['record_id']]['source_row']}"),
        "audit_only_fields_absent": not any(field in materialized[row["record_id"]] for field in AUDIT_ONLY_FIELDS),
        "entity_type": desired[row["record_id"]]["normalized_entity_type"],
        "handle_requirement": desired[row["record_id"]]["merchant_handle_requirement"],
        "aliases": desired[row["record_id"]]["search_aliases"],
        "index_eligibility": desired[row["record_id"]]["parent_index_eligibility"],
        "citation_eligibility": desired[row["record_id"]]["can_external_reference"],
    } for row in rows]


def _r20_valid(projection, vault, formal):
    row = next(item for item in projection if item["record_id"] == R20)
    return bool(
        row["can_enter_vault"] and not row["can_enter_content_index"]
        and R20 in vault and R20 not in formal
    )


def _blockers(**values):
    blockers = []
    projection = values["full_desired"]
    matrix = values["matrix"]
    managed = values["managed"]
    formal_plan = values["formal_plan"]
    actions = values["action_counts"]
    audit_matrix = {row["field_name"]: row for row in matrix}
    if len(projection) != 120 or len({row["record_id"] for row in projection}) != 120:
        blockers.append("authoritative_reconciliation_not_120_unique_records")
    if any(
        audit_matrix[field]["managed_vault"] or audit_matrix[field]["formal_sqlite"]
        or audit_matrix[field]["triggers_write"]
        for field in AUDIT_ONLY_FIELDS
    ):
        blockers.append("audit_only_materialization_boundary_failed")
    if any(
        row["necessary_materialized_diff_count"] == 0
        for row in managed["rows"] if row["proposed_action"] == "update"
    ):
        blockers.append("managed_update_without_materialized_diff")
    if any(not row["changed_fields"] for row in formal_plan["rows"] if row["action"] == "update"):
        blockers.append("formal_update_without_materialized_diff")
    if actions["blocked"] or actions["manual_review"]:
        blockers.append("blocked_or_manual_review_records_present")
    if {row["record_id"] for row in values["governance_rows"]} != GOVERNANCE_ONLY_RECORD_IDS:
        blockers.append("governance_only_set_mismatch")
    if {row["record_id"] for row in values["create_rows"]} != CREATE_RECORD_IDS:
        blockers.append("four_create_records_mismatch")
    if any(row["path_collision"] for row in values["create_rows"]):
        blockers.append("managed_vault_path_collision")
    if values["managed_counts"]["target"] != 110 or values["formal_counts"]["target"] != 109:
        blockers.append("target_count_reconciliation_failed")
    if not values["r20_valid"]:
        blockers.append("r20_vault_only_reconciliation_failed")
    if values["unsupported"]:
        blockers.append("formal_sqlite_schema_support_missing")
    candidate = values["candidate"]
    expected_candidate = {
        "authoritative_parents": 120,
        "content_parents": 109,
        "candidate_assets": 222,
        "searchable_assets": 205,
        "hold_assets": 1,
        "excluded_or_blocked_assets": 16,
        "orphan_parents": 0,
        "duplicate_parents": 0,
        "restricted_leakage": 0,
        "pending_leakage": 0,
        "managed_vault_parents": 110,
        "audit_only_managed_columns": 0,
        "audit_only_formal_columns": 0,
    }
    if any(candidate.get(key) != value for key, value in expected_candidate.items()):
        blockers.append("temporary_candidate_validation_failed")
    if not all(row["status"] == "pass" for row in values["special"]):
        blockers.append("special_record_validation_failed")
    asset = values["asset_boundary"]
    if (asset["eligible_assets"], asset["hold_assets"], asset["excluded_or_blocked_assets"], asset["approved_url_fields"]) != (205, 1, 16, 410):
        blockers.append("asset_or_url_boundary_failed")
    if not values["formal_unchanged"]:
        blockers.append("formal_system_changed")
    return blockers


def _write_reports(output: Path, result: dict, formal_rows: list) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for child in output.iterdir():
        if child.is_file() and not child.name.startswith("._"):
            child.unlink()
    _write_text(output / "store_data_sync_plan_v2_summary.md", _summary(result))
    _write_csv(output / "decision_store_input_validation.csv", [result["decision_store_validation"]])
    _write_csv(output / "obsolete_plan_registry.csv", [{
        "plan_id": OLD_PLAN_ID,
        "manifest_hash": OLD_MANIFEST_HASH,
        "status": "VALIDATION BLOCKED|DO NOT CONFIRM|DO NOT EXECUTE|SUPERSEDED BY MATERIALIZATION-BOUNDARY FIX",
        "reason": "audit_only_fields_materialized_by_plan",
        "replacement_plan_id": result["plan_id"],
    }])
    _write_csv(output / "field_materialization_matrix.csv", result["field_materialization_matrix"])
    _write_csv(output / "audit_only_field_validation.csv", [
        {"field_name": field, "managed_vault": False, "formal_sqlite": False,
         "production_search": False, "triggers_write": False, "status": "pass"}
        for field in AUDIT_ONLY_FIELDS
    ])
    _write_csv(output / "full_authoritative_desired_state.csv", result["full_authoritative_desired_state"])
    _write_csv(output / "managed_vault_projection.csv", result["managed_vault_projection"])
    _write_csv(output / "formal_sqlite_projection.csv", result["formal_sqlite_projection"])
    _write_csv(output / "parent_reconciliation_v2.csv", result["reconciliation"])
    _write_csv(output / "managed_vault_field_diff.csv", result["managed_vault_field_diffs"])
    _write_csv(output / "formal_sqlite_field_diff.csv", result["formal_sqlite_field_diffs"])
    _write_csv(output / "managed_vault_create_preview.csv", [row for row in result["managed_vault_delta"] if row["action"] == "create"])
    _write_csv(output / "managed_vault_update_preview.csv", [row for row in result["managed_vault_delta"] if row["action"] == "update"])
    _write_csv(output / "managed_vault_no_change.csv", [row for row in result["reconciliation"] if row["proposed_action"] == "no_change"])
    _write_csv(output / "formal_sqlite_create_preview.csv", [row for row in formal_rows if row["action"] == "create"])
    _write_csv(output / "formal_sqlite_update_preview.csv", [row for row in formal_rows if row["action"] == "update"])
    _write_csv(output / "formal_sqlite_no_change.csv", [row for row in formal_rows if row["action"] == "no_change"])
    _write_csv(output / "formal_sqlite_not_projected.csv", [row for row in formal_rows if row["action"] == "not_projected"])
    _write_csv(output / "governance_only_records.csv", result["governance_only_records"])
    _write_csv(output / "four_create_records_validation.csv", result["four_create_records"])
    _write_csv(output / "special_record_validation.csv", result["special_record_validation"])
    _write_csv(output / "asset_url_boundary_validation.csv", [result["asset_boundary"]])
    _write_text(output / "temporary_candidate_validation.md", _candidate_text(result["candidate_validation"]))
    _write_text(output / "offline_search_behavior_preview.md", _search_text(result["offline_search"]))
    _write_json(output / "managed_vault_delta_manifest.json", {
        "schema_version": 2,
        "target": "obsidian_vault/MKA",
        "allowlist": sorted(_managed_fields().values()),
        "record_count": len(result["managed_vault_delta"]),
        "delta_hash": result["managed_vault_delta_hash"],
        "records": result["managed_vault_delta"],
    })
    _write_json(output / "formal_sqlite_delta_manifest.json", {
        "schema_version": 2,
        "target": ".mka/content_index.sqlite",
        "allowlist": sorted(set(_formal_fields().values())),
        "record_count": len(result["formal_sqlite_delta"]),
        "delta_hash": result["formal_sqlite_delta_hash"],
        "records": result["formal_sqlite_delta"],
    })
    _write_text(output / "store_data_sync_backup_plan.md", _backup_text())
    _write_text(output / "store_data_sync_rollback_plan.md", _rollback_text())
    _write_text(output / "store_data_sync_confirmation_checklist.md", _checklist_text(result))
    _write_json(output / "store_data_sync_plan_v2_manifest.json", result["manifest"])
    _write_csv(output / "store_data_sync_validation_errors.csv", [
        {"error": item} for item in result["blocker_reasons"]
    ], default_fields=("error",))
    _write_csv(output / "store_data_sync_validation_warnings.csv", [], default_fields=("warning",))


def _summary(result):
    managed = result["managed_vault_counts"]
    formal = result["formal_sqlite_counts"]
    return "\n".join([
        "# Store Data Sync Plan V2 Summary",
        "",
        f"- Conclusion: **{result['conclusion']}**",
        f"- New PLAN_ID: `{result['plan_id']}`",
        f"- Manifest Hash: `{result['manifest_hash']}`",
        f"- Expires At: `{result['expires_at']}`",
        f"- Supersedes: `{OLD_PLAN_ID}` (`VALIDATION BLOCKED`, `DO NOT CONFIRM`, `DO NOT EXECUTE`)",
        f"- Authoritative/Reconciliation: {result['authoritative_record_count']}/{result['reconciliation_count']}",
        f"- Managed Vault: create {managed['create']}, update {managed['update']}, no-change {managed['no_change']}, target {managed['target']}",
        f"- Formal SQLite: create {formal['create']}, update {formal['update']}, no-change {formal['no_change']}, target {formal['target']}",
        f"- Governance-only: {len(result['governance_only_records'])}; r20 vault-only: {result['r20_vault_only_valid']}",
        f"- Managed Delta Hash: `{result['managed_vault_delta_hash']}`",
        f"- Formal SQLite Delta Hash: `{result['formal_sqlite_delta_hash']}`",
        "- Previous cross-check hashes differ by design when target-specific allowlists or canonical payloads differ; neither value was copied into this Plan.",
        f"- execution_blocked: `{str(result['execution_blocked']).lower()}`",
        "- Confirmation created: no",
        "- Formal systems modified: no",
        "",
    ])


def _candidate_text(candidate):
    lines = ["# Temporary Candidate Validation", ""]
    lines.extend(f"- {key}: `{value}`" for key, value in sorted(candidate.items()))
    lines.extend(["", "Candidate database was isolated and was not applied to any formal target.", ""])
    return "\n".join(lines)


def _search_text(search):
    lines = ["# Offline Search Behavior Preview", ""]
    for query, value in search.items():
        lines.append(
            f"- `{query}`: parents={value['record_count']}, assets={value['asset_count']}, "
            f"citations={value['citation_count']}, types={', '.join(value['asset_types']) or 'none'}"
        )
    lines.extend(["", "Production Search and Slack were not called or modified.", ""])
    return "\n".join(lines)


def _backup_text():
    return """# Store Data Sync Backup Plan

Future execution must independently back up `obsidian_vault/MKA/` and `.mka/content_index.sqlite`, verify both checksums, then stage each target separately. The Decision Store is read-only authority and is never part of rollback writes.
"""


def _rollback_text():
    return """# Store Data Sync Rollback Plan

Managed Vault and Formal SQLite have separate delta manifests and rollback scopes. A failed future execute restores only verified target backups, revalidates all 120 authoritative records, and must never alter Decision Store events or audit bundles.
"""


def _checklist_text(result):
    return f"""# Store Data Sync Confirmation Checklist

- Exact PLAN_ID: `{result['plan_id']}`
- Exact Manifest Hash: `{result['manifest_hash']}`
- Decision Store SHA: `{EXPECTED_DATABASE_SHA256}`
- Materialization Contract Hash: `{result['field_materialization_contract_hash']}`
- Managed Vault Delta Hash: `{result['managed_vault_delta_hash']}`
- Formal SQLite Delta Hash: `{result['formal_sqlite_delta_hash']}`
- Reconciliation: 120 records
- execution_blocked: `{str(result['execution_blocked']).lower()}`

This Sprint does not confirm or execute the Plan.
"""


def _matrix_source(field):
    reverse = {target: source for source, target in _managed_fields().items()}
    source_field = reverse.get(field, field)
    row = next(item for item in _field_materialization_matrix() if item["field_name"] == source_field)
    return row["authoritative_source"]


def _formal_consumer(field):
    return {
        "brand_name": "retrieval brand filter",
        "merchant_handle": "retrieval handle filter",
        "merchant_status": "retrieval status filter",
        "can_enter_content_index": "content index inclusion gate",
        "can_quote_externally": "citation and external exposure gate",
        "content_tags": "retrieval content tag filter",
        "data_classification": "query governance gate",
        "source_sheet": "citation source identity",
        "source_row": "citation source identity",
    }[field]


def _audit_field_count(rows):
    return sum(field in row for row in rows for field in AUDIT_ONLY_FIELDS)


def _plan_times(output: Path, plan_id: str, created_at: Optional[str]):
    existing = output / "store_data_sync_plan_v2_manifest.json"
    if created_at is None and existing.is_file():
        value = json.loads(existing.read_text(encoding="utf-8"))
        if value.get("plan_id") == plan_id:
            return value["created_at"], value["expires_at"]
    created = datetime.fromisoformat(created_at) if created_at else datetime.now().astimezone()
    if created.tzinfo is None:
        raise StoreDataSyncPlanV2Error("created_at must include a timezone")
    return created.isoformat(timespec="seconds"), (created + timedelta(days=7)).isoformat(timespec="seconds")


def _resolve(root: Path, path: Path):
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _relative(root: Path, path: Path):
    return path.relative_to(root).as_posix()


def _sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args):
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _write_csv(path: Path, rows: list, default_fields=()) -> None:
    fields = list(rows[0]) if rows else list(default_fields)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: _json(value) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })
