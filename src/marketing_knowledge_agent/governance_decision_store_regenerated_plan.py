from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .governance_decision_store_plan import (
    DECISION_STORE_SCHEMA,
    GovernanceDecisionEvent,
    GovernanceDecisionStorePlanError,
    build_temporary_decision_store,
    legacy_event_from_review_row,
)
from .parent_authority_import_bundle import (
    ParentAuthorityImportBundleError,
    validate_parent_authority_import_bundle,
)


BUNDLE_ID = "parent-authority-approval-20260719"
BUNDLE_ROOT_HASH = "fa7cba755e296d54c03f65c127bf6e1381ea16e1a3b00f4e06ac94b5a87b2033"
BUNDLE_VALIDATOR_VERSION = "parent-authority-import-bundle-v1"
CODE_VERSION = "governance-decision-store-regenerated-plan-v2"
PLAN_TYPE = "governance_decision_store_create_from_immutable_bundle"
DEFAULT_BUNDLE_PATH = Path("data/governance/imports") / BUNDLE_ID
DEFAULT_TARGET_PATH = Path("data/governance/governance_decisions.sqlite")
DEFAULT_OUTPUT_DIR = Path("reports/governance_decision_store_regenerated_plan")
OBSOLETE_PLAN_IDS = (
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
)
EXPECTED_COUNTS = {
    "legacy_import": 46,
    "batch_parent_approval": 96,
    "resolution_parent_supersede": 5,
    "asset_eligibility": 10,
    "search_alias": 2,
    "entity_metadata": 2,
    "asset_url_manifest_reference": 1,
}
OUTPUT_FILENAMES = (
    "regenerated_decision_store_plan_summary.md",
    "immutable_bundle_validation.csv",
    "decision_event_reconciliation.csv",
    "parent_authority_coverage_validation.csv",
    "resolution_supersede_validation.csv",
    "asset_eligibility_validation.csv",
    "search_alias_validation.csv",
    "entity_metadata_validation.csv",
    "asset_url_manifest_reference_validation.csv",
    "temporary_decision_store_validation.md",
    "current_state_projection_validation.csv",
    "parent_sync_readiness.csv",
    "obsolete_plan_registry.csv",
    "decision_store_backup_plan.md",
    "decision_store_rollback_plan.md",
    "decision_store_confirmation_checklist.md",
    "regenerated_decision_store_manifest.json",
    "regenerated_plan_validation_errors.csv",
    "regenerated_plan_validation_warnings.csv",
)


class RegeneratedDecisionStorePlanError(ValueError):
    pass


def generate_regenerated_governance_decision_store_plan(
    *,
    repo_root: Path,
    bundle_path: Path,
    legacy_decisions_path: Path,
    merchant_cases_path: Path,
    asset_url_decisions_path: Path,
    asset_url_validation_path: Path,
    asset_apply_preview_path: Path,
    asset_blocked_preview_path: Path,
    formal_vault_path: Path,
    formal_db_path: Path,
    production_renderer_path: Path,
    target_path: Path = DEFAULT_TARGET_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    created_at: Optional[str] = None,
    source_branch: Optional[str] = None,
    source_commit: Optional[str] = None,
) -> dict:
    root = Path(repo_root).resolve()
    created = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(created, "created_at")
    source_branch = source_branch or _git_value(root, "branch", "--show-current")
    source_commit = source_commit or _git_value(root, "rev-parse", "HEAD")
    paths = _input_paths(
        bundle_path=bundle_path,
        legacy_decisions_path=legacy_decisions_path,
        merchant_cases_path=merchant_cases_path,
        asset_url_decisions_path=asset_url_decisions_path,
        asset_url_validation_path=asset_url_validation_path,
        asset_apply_preview_path=asset_apply_preview_path,
        asset_blocked_preview_path=asset_blocked_preview_path,
    )
    formal = {
        "formal_vault": Path(formal_vault_path),
        "managed_vault": Path(formal_vault_path) / "MKA",
        "formal_content_index": Path(formal_db_path),
        "production_renderer": Path(production_renderer_path),
    }
    for label, path in {**paths, **formal}.items():
        if not path.exists():
            prefix = "Bundle" if label == "bundle" else f"required {label} input"
            raise RegeneratedDecisionStorePlanError(f"{prefix} does not exist: {path}")
    target = Path(target_path)
    output = Path(output_dir)
    _assert_output_safe(output, [*paths.values(), *formal.values(), target])
    protected_before = {key: _hash_path(path) for key, path in {**paths, **formal}.items()}

    bundle_validation, bundle_manifest = _validate_bundle(paths["bundle"])
    input_checksums = {
        "bundle_root_manifest_hash": bundle_manifest["root_manifest_hash"],
        "bundle_manifest_sha256": _sha256(paths["bundle"] / "bundle_manifest.json"),
        "legacy_decisions": _sha256(paths["legacy_decisions"]),
        "merchant_cases": _sha256(paths["merchant_cases"]),
        "asset_url_decisions": _sha256(paths["asset_url_decisions"]),
        "asset_url_validation": _sha256(paths["asset_url_validation"]),
        "asset_apply_preview": _sha256(paths["asset_apply_preview"]),
        "asset_blocked_preview": _sha256(paths["asset_blocked_preview"]),
    }
    conservation = _asset_conservation(paths["bundle"], paths["asset_apply_preview"], paths["asset_blocked_preview"])
    _validate_asset_url_checksum_boundary(paths["bundle"], input_checksums)
    plan_state = {
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "input_checksums": input_checksums,
        "schema_version": hashlib.sha256(DECISION_STORE_SCHEMA.encode("utf-8")).hexdigest(),
        "code_version": CODE_VERSION,
        "expected_event_counts": EXPECTED_COUNTS,
        "target_path": _display_path(root, target),
    }
    plan_id = f"decision-store-plan-{hashlib.sha256(_canonical_json(plan_state)).hexdigest()[:16]}"
    event_plan = build_regenerated_event_plan(
        bundle_path=paths["bundle"],
        legacy_decisions_path=paths["legacy_decisions"],
        merchant_cases_path=paths["merchant_cases"],
        asset_url_decisions_path=paths["asset_url_decisions"],
        asset_url_validation_path=paths["asset_url_validation"],
        asset_apply_preview_path=paths["asset_apply_preview"],
        asset_blocked_preview_path=paths["asset_blocked_preview"],
        created_at=created,
        plan_id=plan_id,
    )
    if event_plan["input_checksums"] != input_checksums:
        raise RegeneratedDecisionStorePlanError("event plan input checksums changed during planning")

    with tempfile.TemporaryDirectory(prefix="mka-regenerated-decision-store-") as temp_name:
        temp_db = Path(temp_name) / "governance_decisions.sqlite"
        temporary = build_temporary_decision_store(temp_db, [*event_plan["events"], event_plan["events"][0]])
        append_only = _verify_append_only(temp_db)
        current = _validate_current_state(temp_db, paths["bundle"], paths["merchant_cases"])
    temporary["update_blocked"] = append_only["update_blocked"]
    temporary["delete_blocked"] = append_only["delete_blocked"]
    temporary["idempotency"] = temporary["duplicate_event_count"] == 1 and temporary["event_count"] == 162
    temporary["temporary_files_cleaned"] = not Path(temp_name).exists()

    blockers = []
    if target.exists():
        blockers.append("formal_target_already_exists")
    if not _data_is_ignored(root):
        blockers.append("formal_target_not_git_ignored")
    if not _temporary_checks_pass(temporary, current):
        blockers.append("temporary_decision_store_validation_failed")
    if event_plan["counts"] != EXPECTED_COUNTS:
        blockers.append("event_count_reconciliation_failed")
    if bundle_manifest["remaining_authority_gap"] != 0:
        blockers.append("parent_authority_gap_nonzero")
    execution_blocked = bool(blockers)
    conclusion = (
        "C. Not ready to create Decision Store"
        if execution_blocked
        else "A. Ready for Admin Decision Store confirmation"
    )
    expires_at = (datetime.fromisoformat(created) + timedelta(days=7)).isoformat()
    manifest = {
        "plan_id": plan_id,
        "plan_type": PLAN_TYPE,
        "target_path": _display_path(root, target),
        "created_at": created,
        "expires_at": expires_at,
        "source_branch": source_branch,
        "source_commit": source_commit,
        "code_version": CODE_VERSION,
        "reviewer_authority": "Admin",
        "bundle_id": BUNDLE_ID,
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "bundle_validation_timestamp": created,
        "bundle_validator_version": BUNDLE_VALIDATOR_VERSION,
        "bundle_file_checksum_result": {
            "verified_file_count": bundle_validation["manifest_file_count"],
            "checksum_errors": bundle_validation["file_checksum_errors"],
            "root_manifest_hash_valid": bundle_validation["root_manifest_hash_valid"],
        },
        "input_checksums": input_checksums,
        "expected_event_count": 162,
        "expected_parent_current_state_count": 120,
        "expected_asset_eligibility_count": 10,
        "expected_alias_count": 2,
        "expected_entity_metadata_count": 2,
        "expected_asset_url_reference_count": 1,
        "parent_authority_coverage": "120/120",
        "remaining_authority_gap": 0,
        "backup_plan": "temporary sibling build; SQLite backup API rehearsal; independent restore validation",
        "rollback_plan": "no formal file before confirm; post-create failure restores backup; later decisions append supersede/revoke",
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "confirm_supported": False,
        "execute_supported": False,
        "old_plan_status": {
            plan: ["DO NOT CONFIRM", "SUPERSEDED", "INVALID FOR EXECUTION"]
            for plan in OBSOLETE_PLAN_IDS
        },
        "formal_data_modified": False,
    }
    manifest["manifest_hash"] = hashlib.sha256(_canonical_json(manifest)).hexdigest()

    _write_outputs(
        output=output,
        conclusion=conclusion,
        manifest=manifest,
        bundle_validation=bundle_validation,
        bundle_manifest=bundle_manifest,
        event_plan=event_plan,
        temporary=temporary,
        current=current,
        conservation=conservation,
        bundle_path=paths["bundle"],
    )
    protected_after = {key: _hash_path(path) for key, path in {**paths, **formal}.items()}
    if protected_before != protected_after:
        raise RegeneratedDecisionStorePlanError("a protected Bundle, input, Vault, SQLite or renderer path changed")
    if target.exists():
        # Existing targets are blockers, but this Sprint must never create one.
        if "formal_target_already_exists" not in blockers:
            raise RegeneratedDecisionStorePlanError("formal Decision Store was unexpectedly created")
    actual = sorted(path.name for path in output.iterdir() if path.is_file() and not path.name.startswith("._"))
    if actual != sorted(OUTPUT_FILENAMES):
        raise RegeneratedDecisionStorePlanError("regenerated plan output contract is incomplete")

    return {
        "conclusion": conclusion,
        "bundle_id": BUNDLE_ID,
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "bundle_file_checksum_errors": bundle_validation["file_checksum_errors"],
        "plan_id": plan_id,
        "manifest_hash": manifest["manifest_hash"],
        "expires_at": expires_at,
        "expected_event_count": 162,
        "current_parent_state_count": current["current_parent_state_count"],
        "parent_authority_coverage": "120/120",
        "remaining_authority_gap": 0,
        "legacy_event_count": event_plan["counts"]["legacy_import"],
        "batch_event_count": event_plan["counts"]["batch_parent_approval"],
        "resolution_supersede_event_count": event_plan["counts"]["resolution_parent_supersede"],
        "asset_eligibility_event_count": event_plan["counts"]["asset_eligibility"],
        "search_alias_event_count": event_plan["counts"]["search_alias"],
        "entity_metadata_event_count": event_plan["counts"]["entity_metadata"],
        "asset_url_reference_event_count": event_plan["counts"]["asset_url_manifest_reference"],
        "eligible_asset_count": conservation["eligible_asset_count"],
        "hold_asset_count": conservation["hold_asset_count"],
        "excluded_asset_count": conservation["excluded_asset_count"],
        "approved_url_field_count": conservation["approved_url_field_count"],
        "temporary_event_count": temporary["event_count"],
        "temporary_integrity_check": temporary["integrity_check"],
        "temporary_foreign_key_errors": temporary["foreign_key_errors"],
        "temporary_update_blocked": temporary["update_blocked"],
        "temporary_delete_blocked": temporary["delete_blocked"],
        "temporary_idempotency": temporary["idempotency"],
        "temporary_hash_chain_valid": temporary["hash_chain_valid"],
        "temporary_tamper_detection": temporary["tamper_detection"],
        "temporary_read_only_reopen": temporary["read_only_reopen"],
        "temporary_transaction_rollback": temporary["transaction_rollback"],
        "temporary_backup_restore": temporary["backup_restore"],
        "temporary_supersede_projection": temporary["supersede_projection"],
        "temporary_revoke_projection": temporary["revoke_projection"],
        "temporary_alias_multi_parent": temporary["alias_multi_parent_projection"],
        "parent_sync_candidate_count": 4,
        "excluded_parent_sync_count": 1,
        "execution_blocked": execution_blocked,
        "blocker_reasons": blockers,
        "formal_data_modified": False,
        "output_dir": str(output),
    }


def build_regenerated_event_plan(
    *,
    bundle_path: Path,
    legacy_decisions_path: Path,
    merchant_cases_path: Path,
    asset_url_decisions_path: Path,
    asset_url_validation_path: Path,
    asset_apply_preview_path: Path,
    asset_blocked_preview_path: Path,
    created_at: str,
    plan_id: str,
) -> dict:
    _validate_timestamp(created_at, "created_at")
    bundle_validation, bundle_manifest = _validate_bundle(Path(bundle_path))
    del bundle_validation
    paths = _input_paths(
        bundle_path=bundle_path,
        legacy_decisions_path=legacy_decisions_path,
        merchant_cases_path=merchant_cases_path,
        asset_url_decisions_path=asset_url_decisions_path,
        asset_url_validation_path=asset_url_validation_path,
        asset_apply_preview_path=asset_apply_preview_path,
        asset_blocked_preview_path=asset_blocked_preview_path,
    )
    for label, path in paths.items():
        if not path.exists():
            raise RegeneratedDecisionStorePlanError(f"required {label} input does not exist: {path}")
    input_checksums = {
        "bundle_root_manifest_hash": bundle_manifest["root_manifest_hash"],
        "bundle_manifest_sha256": _sha256(paths["bundle"] / "bundle_manifest.json"),
        "legacy_decisions": _sha256(paths["legacy_decisions"]),
        "merchant_cases": _sha256(paths["merchant_cases"]),
        "asset_url_decisions": _sha256(paths["asset_url_decisions"]),
        "asset_url_validation": _sha256(paths["asset_url_validation"]),
        "asset_apply_preview": _sha256(paths["asset_apply_preview"]),
        "asset_blocked_preview": _sha256(paths["asset_blocked_preview"]),
    }
    bundle = paths["bundle"]
    legacy_rows = _read_csv(paths["legacy_decisions"])
    batch_rows = _read_csv(bundle / "evidence/approved_parent_authority.csv")
    resolution_parent_rows = _read_csv(bundle / "evidence/resolution_parent_decisions.csv")
    resolution_parent_preview = _read_csv(bundle / "evidence/resolution_parent_preview.csv")
    asset_rows = _read_csv(bundle / "evidence/resolution_asset_eligibility.csv")
    alias_rows = _read_csv(bundle / "evidence/resolution_search_aliases.csv")
    if len(legacy_rows) != 46 or len(batch_rows) != 96:
        raise RegeneratedDecisionStorePlanError("legacy or batch Parent event count mismatch")
    legacy_events = [
        legacy_event_from_review_row(
            row,
            source_manifest_hash=input_checksums["legacy_decisions"],
            input_checksums={"legacy_decisions": input_checksums["legacy_decisions"]},
            created_at=created_at,
        )
        for row in legacy_rows
    ]
    bundle_inputs = {
        "bundle_manifest": input_checksums["bundle_manifest_sha256"],
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
    }
    batch_events = [_batch_event(row, plan_id, created_at, bundle_inputs) for row in batch_rows]
    baseline_parents = {
        event.record_id: event for event in legacy_events
        if event.event_type == "parent_review_decision"
    }
    resolution_events = [
        _resolution_parent_event(row, baseline_parents, plan_id, created_at, bundle_inputs)
        for row in resolution_parent_rows
    ]
    asset_events = [_asset_event(row, plan_id, created_at, bundle_inputs) for row in asset_rows]
    alias_events = [_alias_event(row, plan_id, created_at, bundle_inputs) for row in alias_rows]
    preview_by_id = {row["record_id"]: row for row in resolution_parent_preview}
    entity_events = [
        _entity_event(preview_by_id[record_id], plan_id, created_at, bundle_inputs)
        for record_id in ("商家夥伴案例資料庫:r7", "商家夥伴案例資料庫:r122")
    ]
    conservation = _asset_conservation(bundle, paths["asset_apply_preview"], paths["asset_blocked_preview"])
    url_event = _asset_url_reference_event(
        plan_id=plan_id,
        created_at=created_at,
        input_checksums=input_checksums,
        decisions_path=paths["asset_url_decisions"],
        conservation=conservation,
    )
    events = [
        *legacy_events,
        *batch_events,
        *resolution_events,
        *asset_events,
        *alias_events,
        *entity_events,
        url_event,
    ]
    counts = {
        "legacy_import": len(legacy_events),
        "batch_parent_approval": len(batch_events),
        "resolution_parent_supersede": len(resolution_events),
        "asset_eligibility": len(asset_events),
        "search_alias": len(alias_events),
        "entity_metadata": len(entity_events),
        "asset_url_manifest_reference": 1,
    }
    if counts != EXPECTED_COUNTS or len(events) != 162:
        raise RegeneratedDecisionStorePlanError(f"event count reconciliation failed: {counts}")
    return {
        "events": events,
        "counts": counts,
        "input_checksums": input_checksums,
        "conservation": conservation,
    }


def _batch_event(row, plan_id, created_at, bundle_inputs):
    if row["final_review_decision"] != "approve" or row["reviewer"] != "Admin":
        raise RegeneratedDecisionStorePlanError("Bundle batch approval metadata is invalid")
    return GovernanceDecisionEvent(
        event_type="parent_review_decision",
        subject_type="parent",
        subject_id=row["record_id"],
        record_id=row["record_id"],
        asset_id=None,
        field_name="review_decision",
        action="approve",
        previous_value=None,
        new_value={
            "review_decision": row["final_review_decision"],
            "can_enter_vault": row["can_enter_vault"],
            "can_enter_content_index": row["can_enter_content_index"],
            "can_external_reference": row["can_external_reference"],
        },
        reviewer=row["reviewer"],
        reviewed_at=row["reviewed_at"],
        decision_reason=row["notes"],
        provenance="batch_approval",
        source_plan_id=plan_id,
        source_manifest_hash=BUNDLE_ROOT_HASH,
        input_checksums=bundle_inputs,
        supersedes_event_id=None,
        created_at=created_at,
        code_version=CODE_VERSION,
        source_bundle_id=BUNDLE_ID,
        source_bundle_root_hash=BUNDLE_ROOT_HASH,
    )


def _resolution_parent_event(row, baseline_parents, plan_id, created_at, bundle_inputs):
    previous = baseline_parents.get(row["record_id"])
    if previous is None:
        raise RegeneratedDecisionStorePlanError(f"resolution Parent lacks legacy baseline: {row['record_id']}")
    return GovernanceDecisionEvent(
        event_type="parent_review_decision",
        subject_type="parent",
        subject_id=row["record_id"],
        record_id=row["record_id"],
        asset_id=None,
        field_name="review_decision",
        action="supersede",
        previous_value=previous.new_value,
        new_value={
            "review_decision": row["proposed_review_decision"],
            "can_enter_vault": row["proposed_can_enter_vault"],
            "can_enter_content_index": row["proposed_can_enter_content_index"],
            "can_external_reference": row["proposed_can_external_reference"],
        },
        reviewer=row["reviewer"],
        reviewed_at=row["reviewed_at"],
        decision_reason=row["decision_reason"],
        provenance="admin_resolution",
        source_plan_id=plan_id,
        source_manifest_hash=BUNDLE_ROOT_HASH,
        input_checksums=bundle_inputs,
        supersedes_event_id=previous.event_id,
        created_at=created_at,
        code_version=CODE_VERSION,
        source_bundle_id=BUNDLE_ID,
        source_bundle_root_hash=BUNDLE_ROOT_HASH,
    )


def _asset_event(row, plan_id, created_at, bundle_inputs):
    eligibility = row["proposed_asset_index_eligibility"]
    return GovernanceDecisionEvent(
        event_type="asset_eligibility",
        subject_type="asset",
        subject_id=row["asset_id"],
        record_id=row["record_id"],
        asset_id=row["asset_id"],
        field_name="asset_eligibility",
        action={"include": "approve", "hold": "hold", "exclude": "exclude"}[eligibility],
        previous_value=row.get("current_asset_eligibility") or None,
        new_value={
            "asset_index_eligibility": eligibility,
            "asset_search_eligibility": row["proposed_asset_search_eligibility"],
        },
        reviewer=row["reviewer"],
        reviewed_at=row["reviewed_at"],
        decision_reason=row["eligibility_reason"],
        provenance="admin_resolution",
        source_plan_id=plan_id,
        source_manifest_hash=BUNDLE_ROOT_HASH,
        input_checksums=bundle_inputs,
        supersedes_event_id=None,
        created_at=created_at,
        code_version=CODE_VERSION,
        source_bundle_id=BUNDLE_ID,
        source_bundle_root_hash=BUNDLE_ROOT_HASH,
    )


def _alias_event(row, plan_id, created_at, bundle_inputs):
    normalized = " ".join(row["alias"].strip().casefold().split())
    if row["match_type"] != "case_insensitive_exact" or row["fuzzy_matching"] != "false":
        raise RegeneratedDecisionStorePlanError("search alias is not exact-only")
    return GovernanceDecisionEvent(
        event_type="search_alias",
        subject_type="search_alias",
        subject_id=f"{row['record_id']}|{normalized}",
        record_id=row["record_id"],
        asset_id=None,
        field_name="search_aliases",
        action="add",
        previous_value=None,
        new_value={"alias": row["alias"], "normalized_alias": normalized, "match_type": "case_insensitive_exact"},
        reviewer=row["reviewer"],
        reviewed_at=row["reviewed_at"],
        decision_reason="Admin-approved exact source-record alias",
        provenance="admin_resolution",
        source_plan_id=plan_id,
        source_manifest_hash=BUNDLE_ROOT_HASH,
        input_checksums=bundle_inputs,
        supersedes_event_id=None,
        created_at=created_at,
        code_version=CODE_VERSION,
        source_bundle_id=BUNDLE_ID,
        source_bundle_root_hash=BUNDLE_ROOT_HASH,
    )


def _entity_event(row, plan_id, created_at, bundle_inputs):
    if row["entity_type"] != "partner" or row["merchant_handle_requirement"] != "not_required":
        raise RegeneratedDecisionStorePlanError("partner entity metadata is invalid")
    return GovernanceDecisionEvent(
        event_type="entity_metadata",
        subject_type="parent",
        subject_id=row["record_id"],
        record_id=row["record_id"],
        asset_id=None,
        field_name="entity_type_and_handle_requirement",
        action="add",
        previous_value=None,
        new_value={"entity_type": "partner", "merchant_handle_requirement": "not_required"},
        reviewer=row["reviewer"],
        reviewed_at=row["reviewed_at"],
        decision_reason=row["reason"],
        provenance="admin_resolution",
        source_plan_id=plan_id,
        source_manifest_hash=BUNDLE_ROOT_HASH,
        input_checksums=bundle_inputs,
        supersedes_event_id=None,
        created_at=created_at,
        code_version=CODE_VERSION,
        source_bundle_id=BUNDLE_ID,
        source_bundle_root_hash=BUNDLE_ROOT_HASH,
    )


def _asset_url_reference_event(plan_id, created_at, input_checksums, decisions_path, conservation):
    rows = _read_csv(decisions_path)
    reviewed = [
        row for row in rows
        if row.get("review_decision") in {"approve", "exclude_asset"}
        and row.get("field") in {"asset_url", "canonical_url"}
    ]
    reviewers = {row.get("reviewer", "") for row in reviewed if row.get("reviewer")}
    reviewed_at = {row.get("reviewed_at", "") for row in reviewed if row.get("reviewed_at")}
    if reviewers != {"James Huang"} or len(reviewed_at) != 1:
        raise RegeneratedDecisionStorePlanError("Asset URL reviewer metadata is inconsistent")
    reference_hash = hashlib.sha256(_canonical_json({
        "decision": input_checksums["asset_url_decisions"],
        "validator": input_checksums["asset_url_validation"],
        "apply": input_checksums["asset_apply_preview"],
        "blocked": input_checksums["asset_blocked_preview"],
    })).hexdigest()
    return GovernanceDecisionEvent(
        event_type="asset_url_manifest_reference",
        subject_type="manifest",
        subject_id=f"asset-url-decisions|{reference_hash[:16]}",
        record_id=None,
        asset_id=None,
        field_name="asset_url_decision_manifest",
        action="add",
        previous_value=None,
        new_value={
            "approved_url_field_count": conservation["approved_url_field_count"],
            "eligible_asset_count": conservation["eligible_asset_count"],
            "hold_asset_count": conservation["hold_asset_count"],
            "excluded_or_blocked_asset_count": conservation["excluded_asset_count"],
            "decision_csv_checksum": input_checksums["asset_url_decisions"],
            "validator_output_checksum": input_checksums["asset_url_validation"],
            "apply_preview_checksum": input_checksums["asset_apply_preview"],
            "blocked_preview_checksum": input_checksums["asset_blocked_preview"],
            "manifest_hash": reference_hash,
            "source_path_reference": "reports/asset_metadata_preview/human_review_template.csv",
        },
        reviewer=next(iter(reviewers)),
        reviewed_at=next(iter(reviewed_at)),
        decision_reason="Reference validated Asset URL decisions without duplicating URL field values",
        provenance="validated_asset_url_manifest_reference",
        source_plan_id=plan_id,
        source_manifest_hash=reference_hash,
        input_checksums={
            "asset_url_decisions": input_checksums["asset_url_decisions"],
            "asset_url_validation": input_checksums["asset_url_validation"],
            "asset_apply_preview": input_checksums["asset_apply_preview"],
        },
        supersedes_event_id=None,
        created_at=created_at,
        code_version=CODE_VERSION,
    )


def _validate_bundle(bundle_path):
    try:
        validation = validate_parent_authority_import_bundle(bundle_path)
    except ParentAuthorityImportBundleError as exc:
        raise RegeneratedDecisionStorePlanError(f"Bundle validation failed: {exc}") from exc
    manifest = _read_json(Path(bundle_path) / "bundle_manifest.json")
    expected = {
        "bundle_id": BUNDLE_ID,
        "root_manifest_hash": BUNDLE_ROOT_HASH,
        "approved_parent_count": 96,
        "parent_authority_total": 120,
        "remaining_authority_gap": 0,
        "expected_decision_store_event_count": 162,
        "expected_parent_current_state_count": 120,
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise RegeneratedDecisionStorePlanError(f"Bundle {field} mismatch")
    if validation["file_checksum_errors"] or not validation["root_manifest_hash_valid"]:
        raise RegeneratedDecisionStorePlanError("Bundle checksum validation failed")
    if _read_csv(Path(bundle_path) / "validation/final_validation_errors.csv"):
        raise RegeneratedDecisionStorePlanError("Bundle contains final validation errors")
    if _read_csv(Path(bundle_path) / "validation/final_validation_warnings.csv"):
        raise RegeneratedDecisionStorePlanError("Bundle contains final validation warnings")
    return validation, manifest


def _asset_conservation(bundle, apply_path, blocked_path):
    apply_rows = _read_csv(apply_path)
    blocked_rows = _read_csv(blocked_path)
    resolution_assets = _read_csv(Path(bundle) / "evidence/resolution_asset_eligibility.csv")
    approved = {}
    for row in apply_rows:
        if row.get("review_decision") == "approve":
            approved.setdefault(row["asset_id"], set()).add(row["field"])
    excluded_asset = "商家夥伴案例資料庫:r30:article"
    eligible = set(approved) - {excluded_asset}
    held = {row["asset_id"] for row in resolution_assets if row["proposed_asset_index_eligibility"] == "hold"}
    blocked = {row["asset_id"] for row in blocked_rows}
    excluded = (blocked - held) | {excluded_asset}
    observed = (len(eligible), len(held), len(excluded), len(eligible) * 2)
    if observed != (205, 1, 16, 410) or len(eligible | held | excluded) != 222:
        raise RegeneratedDecisionStorePlanError(f"205/1/16/410 asset conservation failed: {observed}")
    return {
        "eligible_asset_count": 205,
        "hold_asset_count": 1,
        "excluded_asset_count": 16,
        "approved_url_field_count": 410,
    }


def _validate_asset_url_checksum_boundary(bundle, checksums):
    stored = _read_json(Path(bundle) / "manifests/parent_batch_approval_checksums.json")
    expected = stored.get("protected_input_checksums_after", {}).get("asset_url_decisions")
    if expected != checksums["asset_url_decisions"]:
        raise RegeneratedDecisionStorePlanError("Asset URL decision checksum changed since Bundle approval")


def _verify_append_only(db_path):
    connection = sqlite3.connect(db_path)
    results = {}
    try:
        for operation, sql in (
            ("update_blocked", "UPDATE decision_events SET decision_reason='tampered' WHERE event_sequence=1"),
            ("delete_blocked", "DELETE FROM decision_events WHERE event_sequence=1"),
        ):
            try:
                connection.execute(sql)
            except sqlite3.IntegrityError:
                connection.rollback()
                results[operation] = True
            else:
                connection.rollback()
                results[operation] = False
    finally:
        connection.close()
    return results


def _validate_current_state(db_path, bundle, merchant_cases_path):
    coverage = _read_csv(Path(bundle) / "evidence/parent_authority_120_coverage.csv")
    expected_parent_ids = {row["record_id"] for row in coverage}
    connection = sqlite3.connect(db_path)
    try:
        parent_rows = connection.execute("SELECT record_id, new_value_json FROM current_parent_decisions").fetchall()
        current_parent_ids = {row[0] for row in parent_rows}
        parents = {row[0]: json.loads(row[1]) for row in parent_rows}
        asset_rows = connection.execute("SELECT asset_id, new_value_json FROM current_asset_eligibility").fetchall()
        assets = {row[0]: json.loads(row[1]) for row in asset_rows}
        aliases = [json.loads(row[0]) for row in connection.execute("SELECT new_value_json FROM current_search_aliases")]
        entity_rows = connection.execute("SELECT record_id, new_value_json FROM current_entity_metadata").fetchall()
    finally:
        connection.close()
    merchant_cases = _read_json(merchant_cases_path)
    exact_tag_count = sum(
        "shopline payments" in {str(tag).strip().casefold() for tag in (record.get("content_tags") or [])}
        for record in merchant_cases
    )
    checks = {
        "current_parent_state_count": len(parent_rows),
        "authority_gap_count": len(expected_parent_ids - current_parent_ids),
        "unique_parent_subjects": len(current_parent_ids),
        "current_asset_eligibility_count": len(asset_rows),
        "current_search_alias_count": len(aliases),
        "current_entity_metadata_count": len(entity_rows),
        "r30_excluded": parents.get("商家夥伴案例資料庫:r30", {}).get("review_decision") == "exclude",
        "r12_internal_only": parents.get("商家夥伴案例資料庫:r12", {}).get("review_decision") == "approve_internal_only",
        "r12_video_held": assets.get("商家夥伴案例資料庫:r12:video", {}).get("asset_search_eligibility") == "not_searchable",
        "slp_exact": any(item.get("normalized_alias") == "slp" for item in aliases),
        "shopline_payments_exact": any(item.get("normalized_alias") == "shopline payments" for item in aliases),
        "shopline_payments_exact_tag_count": exact_tag_count,
        "partner_without_handle_count": sum(
            json.loads(row[1]).get("merchant_handle_requirement") == "not_required" for row in entity_rows
        ),
    }
    expected = {
        "current_parent_state_count": 120,
        "authority_gap_count": 0,
        "unique_parent_subjects": 120,
        "current_asset_eligibility_count": 10,
        "current_search_alias_count": 2,
        "current_entity_metadata_count": 2,
        "r30_excluded": True,
        "r12_internal_only": True,
        "r12_video_held": True,
        "slp_exact": True,
        "shopline_payments_exact": True,
        "partner_without_handle_count": 2,
    }
    if any(checks[key] != value for key, value in expected.items()) or exact_tag_count < 15:
        raise RegeneratedDecisionStorePlanError(f"current-state projection validation failed: {checks}")
    return checks


def _temporary_checks_pass(temporary, current):
    return all([
        temporary["event_count"] == 162,
        temporary["duplicate_event_count"] == 1,
        temporary["integrity_check"] == "ok",
        temporary["foreign_key_errors"] == 0,
        temporary["read_only_reopen"],
        temporary["transaction_rollback"],
        temporary["backup_restore"],
        temporary["hash_chain_valid"],
        temporary["tamper_detection"],
        temporary["supersede_projection"],
        temporary["revoke_projection"],
        temporary["alias_multi_parent_projection"],
        temporary["update_blocked"],
        temporary["delete_blocked"],
        current["current_parent_state_count"] == 120,
        current["authority_gap_count"] == 0,
    ])


def _write_outputs(output, conclusion, manifest, bundle_validation, bundle_manifest, event_plan, temporary, current, conservation, bundle_path):
    output.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILENAMES:
        path = output / name
        if path.exists():
            path.unlink()
    _write_text(output / OUTPUT_FILENAMES[0], _summary_markdown(conclusion, manifest, event_plan, temporary, current, conservation))
    bundle_rows = [
        {"check": "bundle_id", "expected": BUNDLE_ID, "observed": bundle_manifest["bundle_id"], "status": "pass"},
        {"check": "root_manifest_hash", "expected": BUNDLE_ROOT_HASH, "observed": bundle_manifest["root_manifest_hash"], "status": "pass"},
        {"check": "root_hash_valid", "expected": "true", "observed": _bool(bundle_validation["root_manifest_hash_valid"]), "status": "pass"},
    ]
    bundle_rows.extend({"check": f"file:{row['bundle_relative_path']}", "expected": row["sha256"], "observed": row["sha256"], "status": row["status"]} for row in bundle_validation["checksum_rows"])
    _write_csv(output / OUTPUT_FILENAMES[1], bundle_rows)
    _write_csv(output / OUTPUT_FILENAMES[2], [
        {"event_category": key, "expected_count": value, "observed_count": event_plan["counts"][key], "status": "pass"}
        for key, value in EXPECTED_COUNTS.items()
    ] + [
        {"event_category": "total", "expected_count": 162, "observed_count": len(event_plan["events"]), "status": "pass"},
        {"event_category": "parent_current_state", "expected_count": 120, "observed_count": current["current_parent_state_count"], "status": "pass"},
    ])
    coverage = _read_csv(Path(bundle_path) / "evidence/parent_authority_120_coverage.csv")
    _write_csv(output / OUTPUT_FILENAMES[3], coverage)
    parent_events = [event for event in event_plan["events"] if event.event_type == "parent_review_decision" and event.action == "supersede"]
    _write_csv(output / OUTPUT_FILENAMES[4], [_event_report(event) for event in parent_events])
    asset_events = [event for event in event_plan["events"] if event.event_type == "asset_eligibility"]
    _write_csv(output / OUTPUT_FILENAMES[5], [_event_report(event) for event in asset_events])
    alias_events = [event for event in event_plan["events"] if event.event_type == "search_alias"]
    _write_csv(output / OUTPUT_FILENAMES[6], [_event_report(event) for event in alias_events])
    entity_events = [event for event in event_plan["events"] if event.event_type == "entity_metadata"]
    _write_csv(output / OUTPUT_FILENAMES[7], [_event_report(event) for event in entity_events])
    url_event = next(event for event in event_plan["events"] if event.event_type == "asset_url_manifest_reference")
    _write_csv(output / OUTPUT_FILENAMES[8], [_event_report(url_event)])
    _write_text(output / OUTPUT_FILENAMES[9], _temporary_markdown(temporary, current))
    _write_csv(output / OUTPUT_FILENAMES[10], [
        {"projection": key, "expected": _expected_projection(key), "observed": value, "status": "pass"}
        for key, value in current.items()
    ])
    _write_csv(output / OUTPUT_FILENAMES[11], _sync_readiness(parent_events))
    _write_csv(output / OUTPUT_FILENAMES[12], [
        {"plan_id": plan, "status": "DO NOT CONFIRM | SUPERSEDED | INVALID FOR EXECUTION", "reason": "Authority gap and immutable Bundle inputs changed after this plan was created."}
        for plan in OBSOLETE_PLAN_IDS
    ])
    _write_text(output / OUTPUT_FILENAMES[13], _backup_markdown())
    _write_text(output / OUTPUT_FILENAMES[14], _rollback_markdown())
    _write_text(output / OUTPUT_FILENAMES[15], _confirmation_markdown(manifest))
    (output / OUTPUT_FILENAMES[16]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(output / OUTPUT_FILENAMES[17], [])
    _write_csv(output / OUTPUT_FILENAMES[18], [])


def _summary_markdown(conclusion, manifest, event_plan, temporary, current, conservation):
    return f"""# Regenerated Governance Decision Store Plan

- Conclusion: {conclusion}
- PLAN_ID: `{manifest['plan_id']}`
- Manifest hash: `{manifest['manifest_hash']}`
- Expires at: `{manifest['expires_at']}`
- Bundle: `{BUNDLE_ID}`
- Bundle root hash: `{BUNDLE_ROOT_HASH}`
- Events: {len(event_plan['events'])}
- Parent current state: {current['current_parent_state_count']}
- Parent authority coverage: 120/120; gap 0
- Eligible / hold / excluded assets: {conservation['eligible_asset_count']} / {conservation['hold_asset_count']} / {conservation['excluded_asset_count']}
- Approved URL fields: {conservation['approved_url_field_count']}
- Temporary integrity / FK: `{temporary['integrity_check']}` / {temporary['foreign_key_errors']}
- execution_blocked: {_bool(manifest['execution_blocked'])}
- Confirm supported: false
- Execute supported: false
- Formal data modified: false
"""


def _temporary_markdown(result, current):
    return f"""# Temporary Decision Store Validation

- Legacy Events: 46
- Batch Parent Approval Events: 96
- Resolution Parent Supersede Events: 5
- Asset Eligibility / Alias / Entity Metadata / URL Reference: 10 / 2 / 2 / 1
- Total stored Events: {result['event_count']}
- Current Parent State: {current['current_parent_state_count']}
- Authority Gap: {current['authority_gap_count']}
- integrity_check: `{result['integrity_check']}`
- foreign_key_check errors: {result['foreign_key_errors']}
- UPDATE / DELETE blocked: {_bool(result['update_blocked'])} / {_bool(result['delete_blocked'])}
- Duplicate idempotency: {_bool(result['idempotency'])}
- Supersede / Revoke projection: {_bool(result['supersede_projection'])} / {_bool(result['revoke_projection'])}
- Hash chain / tamper detection: {_bool(result['hash_chain_valid'])} / {_bool(result['tamper_detection'])}
- Read-only reopen / transaction rollback / backup restore: {_bool(result['read_only_reopen'])} / {_bool(result['transaction_rollback'])} / {_bool(result['backup_restore'])}
- Alias one-to-many: {_bool(result['alias_multi_parent_projection'])}
- SLP exact / SHOPLINE Payments exact: {_bool(current['slp_exact'])} / {_bool(current['shopline_payments_exact'])}
- Existing SHOPLINE Payments exact tags retained: {current['shopline_payments_exact_tag_count']}
- Temporary directory cleanup: true
"""


def _backup_markdown():
    return """# Decision Store Backup Plan

1. Revalidate all Bundle file SHA-256 values and root hash during Plan, Confirm and Execute.
2. Confirm the formal target remains absent and record a pre-create manifest.
3. Build a same-filesystem temporary SQLite database and run integrity/FK/hash/projection checks.
4. Rehearse SQLite Backup API restore before confirmation.
5. After a separate Admin confirmation, atomically rename the verified temporary database.
6. Record the post-create checksum and preserve the immutable Bundle reference.

This Sprint rehearsed backup and restore only on a temporary database.
"""


def _rollback_markdown():
    return """# Decision Store Rollback Plan

## Before Atomic Create
Delete the temporary database. The formal target remains absent.

## Post-create Validation Failure
Quarantine the failed new database, restore the independently verified backup, run integrity/FK/hash checks, and retain failure audit evidence.

## Later Decision Error
Never UPDATE or DELETE Events. Append a reviewed `supersede` or `revoke` Event and rebuild current-state projections.
"""


def _confirmation_markdown(manifest):
    return f"""# Decision Store Confirmation Checklist

## NOT CONFIRMED IN THIS SPRINT

- [ ] PLAN_ID: `{manifest['plan_id']}`
- [ ] Manifest hash: `{manifest['manifest_hash']}`
- [ ] Bundle ID: `{BUNDLE_ID}`
- [ ] Bundle root hash: `{BUNDLE_ROOT_HASH}`
- [ ] Target: `{manifest['target_path']}`
- [ ] Expected Events: 162
- [ ] Current Parent State: 120
- [ ] Reviewer authority: Admin
- [ ] Plan not expired at `{manifest['expires_at']}`
- [ ] Bundle and all input checksums revalidated
- [ ] Temporary dry-run and backup restore independently validated
- [ ] Admin provides a separate Confirmed At timestamp

No force, overwrite, skip-validation, ignore-checksum or auto-confirm path is supported.
"""


def _event_report(event):
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "subject_type": event.subject_type,
        "record_id": event.record_id or "",
        "asset_id": event.asset_id or "",
        "action": event.action,
        "reviewer": event.reviewer,
        "reviewed_at": event.reviewed_at or "",
        "provenance": event.provenance,
        "source_bundle_id": event.source_bundle_id or "",
        "source_bundle_root_hash": event.source_bundle_root_hash or "",
        "new_value_json": json.dumps(event.new_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "validation_status": "pass",
    }


def _sync_readiness(parent_events):
    by_id = {event.record_id: event for event in parent_events}
    rows = []
    for record_id in (
        "商家夥伴案例資料庫:r7",
        "商家夥伴案例資料庫:r12",
        "商家夥伴案例資料庫:r32",
        "商家夥伴案例資料庫:r122",
        "商家夥伴案例資料庫:r30",
    ):
        event = by_id[record_id]
        excluded = event.new_value["review_decision"] == "exclude"
        rows.append({
            "record_id": record_id,
            "review_decision": event.new_value["review_decision"],
            "sync_readiness": "not_syncable" if excluded else "syncable",
            "index_eligibility": "excluded" if excluded else "included",
            "search_eligibility": "not_searchable" if excluded else "governance_and_asset_eligibility_required",
            "validation_status": "pass",
        })
    return rows


def _expected_projection(key):
    return {
        "current_parent_state_count": 120,
        "authority_gap_count": 0,
        "unique_parent_subjects": 120,
        "current_asset_eligibility_count": 10,
        "current_search_alias_count": 2,
        "current_entity_metadata_count": 2,
        "r30_excluded": True,
        "r12_internal_only": True,
        "r12_video_held": True,
        "slp_exact": True,
        "shopline_payments_exact": True,
        "partner_without_handle_count": 2,
    }.get(key, "at_least_15")


def _input_paths(**paths):
    return {
        "bundle": Path(paths["bundle_path"]),
        "legacy_decisions": Path(paths["legacy_decisions_path"]),
        "merchant_cases": Path(paths["merchant_cases_path"]),
        "asset_url_decisions": Path(paths["asset_url_decisions_path"]),
        "asset_url_validation": Path(paths["asset_url_validation_path"]),
        "asset_apply_preview": Path(paths["asset_apply_preview_path"]),
        "asset_blocked_preview": Path(paths["asset_blocked_preview_path"]),
    }


def _assert_output_safe(output, protected):
    resolved = Path(output).resolve()
    for path in protected:
        candidate = Path(path).resolve()
        if resolved == candidate or resolved in candidate.parents or candidate in resolved.parents:
            raise RegeneratedDecisionStorePlanError("output directory overlaps a protected input or formal target")


def _data_is_ignored(root):
    ignore = Path(root) / ".gitignore"
    return ignore.is_file() and any(line.strip() == "/data/" for line in ignore.read_text(encoding="utf-8").splitlines())


def _display_path(root, path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RegeneratedDecisionStorePlanError(f"CSV has no header: {path}")
        return list(reader)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegeneratedDecisionStorePlanError(f"invalid JSON input: {path}") from exc


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
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RegeneratedDecisionStorePlanError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RegeneratedDecisionStorePlanError(f"{field} must include timezone")


def _bool(value):
    return "true" if bool(value) else "false"


def _git_value(root, *args):
    try:
        return subprocess.check_output(["git", *args], cwd=str(root), text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RegeneratedDecisionStorePlanError("unable to determine source git identity") from exc
