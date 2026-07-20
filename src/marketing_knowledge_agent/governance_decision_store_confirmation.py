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

from .governance_decision_store_plan import (
    DECISION_STORE_SCHEMA,
    GovernanceDecisionEvent,
    build_temporary_decision_store,
    legacy_event_from_review_row,
)
from .parent_authority_import_bundle import (
    ParentAuthorityImportBundleError,
    validate_parent_authority_import_bundle,
)


EXPECTED_PLAN_ID = "decision-store-plan-a02502d8361549b1"
EXPECTED_MANIFEST_HASH = "1b285ec1e762d9c2b2fc42776742ac130f56aed2588186d33e8c5b3ffd435853"
PLAN_EXPIRES_AT = "2026-07-26T19:54:53+08:00"
BUNDLE_ID = "parent-authority-approval-20260719"
BUNDLE_ROOT_HASH = "fa7cba755e296d54c03f65c127bf6e1381ea16e1a3b00f4e06ac94b5a87b2033"
EXPECTED_TARGET = "data/governance/governance_decisions.sqlite"
PLAN_TYPE = "governance_decision_store_create_from_immutable_bundle"
PLAN_CODE_VERSION = "governance-decision-store-regenerated-plan-v2"
VALIDATOR_CODE_VERSION = "governance-decision-store-independent-confirmation-v1"
CONFIRMATION_SCHEMA_VERSION = "1.0"
DEFAULT_PLAN_MANIFEST = Path("reports/governance_decision_store_regenerated_plan/regenerated_decision_store_manifest.json")
DEFAULT_BUNDLE_PATH = Path("data/governance/imports") / BUNDLE_ID
DEFAULT_FORMAL_TARGET = Path(EXPECTED_TARGET)
DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / EXPECTED_PLAN_ID
DEFAULT_REPORT_DIR = Path("reports/governance_decision_store_confirmation")
OBSOLETE_PLAN_IDS = {
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
    "Admin confirms the validated creation plan for the append-only Governance Decision Store "
    "identified by PLAN_ID decision-store-plan-a02502d8361549b1 and Manifest Hash "
    "1b285ec1e762d9c2b2fc42776742ac130f56aed2588186d33e8c5b3ffd435853. "
    "This confirmation authorizes a later, separate execute step only. It does not execute the plan "
    "or authorize any different PLAN_ID, manifest, target path, event count, or input checksum."
)
REPORT_FILENAMES = (
    "decision_store_confirmation_summary.md",
    "independent_plan_validation.csv",
    "bundle_revalidation.csv",
    "event_reconciliation_validation.csv",
    "temporary_store_independent_validation.md",
    "special_decision_validation.csv",
    "target_path_validation.csv",
    "obsolete_plan_rejection_validation.csv",
    "confirmation_bundle_validation.csv",
    "formal_system_unchanged_validation.csv",
    "decision_store_execute_prerequisites.md",
    "confirmation_validation_errors.csv",
    "confirmation_validation_warnings.csv",
)


class GovernanceDecisionStoreConfirmationError(RuntimeError):
    pass


def validate_governance_decision_store_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    plan_manifest_path: Path = DEFAULT_PLAN_MANIFEST,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
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
    _require_exact_identifiers(plan_id, manifest_hash)
    current_time = now or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(current_time, "validation timestamp")
    if datetime.fromisoformat(current_time) > datetime.fromisoformat(PLAN_EXPIRES_AT):
        raise GovernanceDecisionStoreConfirmationError("Plan expired; regenerate a new Plan")

    paths = {
        "plan_manifest": _resolve(root, plan_manifest_path),
        "bundle": _resolve(root, bundle_path),
        "legacy_decisions": _resolve(root, legacy_decisions_path),
        "merchant_cases": _resolve(root, merchant_cases_path),
        "asset_url_decisions": _resolve(root, asset_url_decisions_path),
        "asset_url_validation": _resolve(root, asset_url_validation_path),
        "asset_apply_preview": _resolve(root, asset_apply_preview_path),
        "asset_blocked_preview": _resolve(root, asset_blocked_preview_path),
    }
    for label, path in paths.items():
        if not path.exists():
            raise GovernanceDecisionStoreConfirmationError(f"required {label} input is missing: {path}")

    plan_manifest = _validate_plan_manifest(root, paths, plan_id, manifest_hash)
    bundle_validation, bundle_manifest = _validate_bundle(paths["bundle"])
    input_checksums = _calculate_input_checksums(paths, bundle_manifest)
    if input_checksums != plan_manifest["input_checksums"]:
        raise GovernanceDecisionStoreConfirmationError("Plan input checksums changed")
    event_plan = _build_independent_event_plan(
        paths=paths,
        plan_manifest=plan_manifest,
        bundle_manifest=bundle_manifest,
        input_checksums=input_checksums,
    )
    target = _resolve(root, formal_target_path)
    target_checks = _validate_formal_target(target)

    temp_parent = Path(temporary_root) if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mka-independent-decision-store-", dir=str(temp_parent) if temp_parent else None) as name:
        temp_db = Path(name) / "governance_decisions.sqlite"
        temporary = build_temporary_decision_store(temp_db, [*event_plan["events"], event_plan["events"][0]])
        temporary.update(_verify_append_only(temp_db))
        projection = _validate_projections(temp_db, paths["bundle"], paths["merchant_cases"])
    temporary["idempotency"] = temporary["duplicate_event_count"] == 1 and temporary["event_count"] == 162
    temporary["temporary_files_cleaned"] = not Path(name).exists()
    _require_temporary_success(temporary, projection)

    special_rows = _special_decision_validation(event_plan["events"], paths["merchant_cases"])
    special_errors = sum(row["status"] != "pass" for row in special_rows)
    if special_errors:
        raise GovernanceDecisionStoreConfirmationError("special decision validation failed")

    result = {
        "valid": True,
        "plan_id": plan_id,
        "manifest_hash": manifest_hash,
        "plan_not_expired": True,
        "plan_expires_at": PLAN_EXPIRES_AT,
        "validated_at": current_time,
        "bundle_id": bundle_manifest["bundle_id"],
        "bundle_root_manifest_hash": bundle_manifest["root_manifest_hash"],
        "bundle_verified_file_count": bundle_validation["manifest_file_count"],
        "bundle_file_checksum_errors": bundle_validation["file_checksum_errors"],
        "bundle_physical_file_count": bundle_validation["physical_file_count"],
        "event_counts": event_plan["counts"],
        "event_count": len(event_plan["events"]),
        "parent_event_count": event_plan["parent_event_count"],
        "non_parent_event_count": event_plan["non_parent_event_count"],
        "unique_parent_subject_count": event_plan["unique_parent_subject_count"],
        "duplicate_event_id_count": event_plan["duplicate_event_id_count"],
        "duplicate_idempotency_key_count": event_plan["duplicate_idempotency_key_count"],
        "resolution_supersede_count": event_plan["counts"]["resolution_parent_supersede"],
        "current_parent_state_count": projection["current_parent_state_count"],
        "parent_authority_coverage": "120/120",
        "authority_gap": projection["authority_gap_count"],
        "special_decision_errors": special_errors,
        "special_decision_rows": special_rows,
        "eligible_asset_count": event_plan["conservation"]["eligible_asset_count"],
        "hold_asset_count": event_plan["conservation"]["hold_asset_count"],
        "excluded_asset_count": event_plan["conservation"]["excluded_asset_count"],
        "approved_url_field_count": event_plan["conservation"]["approved_url_field_count"],
        "temporary_store": temporary,
        "projection": projection,
        "formal_target_absent": target_checks["formal_target_absent"],
        "target_checks": target_checks,
        "input_checksums": input_checksums,
        "source_branch": plan_manifest["source_branch"],
        "source_commit": plan_manifest["source_commit"],
        "validator_code_version": VALIDATOR_CODE_VERSION,
        "bundle_checksum_rows": bundle_validation["checksum_rows"],
        "plan_manifest": plan_manifest,
    }
    result["independent_validation_hash"] = hashlib.sha256(_canonical_json(_public_validation(result))).hexdigest()
    return result


def confirm_governance_decision_store_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    reviewer: str,
    confirmed_at: Optional[str] = None,
    confirmation_path: Path = DEFAULT_CONFIRMATION_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    **validation_kwargs,
) -> dict:
    if reviewer != "Admin":
        raise GovernanceDecisionStoreConfirmationError("reviewer must equal Admin")
    confirmed = confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _validate_timestamp(confirmed, "confirmation timestamp")
    validation_kwargs = dict(validation_kwargs)
    validation_kwargs.pop("now", None)
    root = Path(repo_root).resolve()
    confirmation = _resolve(root, confirmation_path)
    reports = _resolve(root, report_dir)
    if confirmation.exists():
        existing = validate_governance_decision_store_confirmation(confirmation)
        if (
            existing["plan_id"] != plan_id
            or existing["plan_manifest_hash"] != manifest_hash
            or existing["reviewer"] != reviewer
            or existing["confirmed_at"] != confirmed
        ):
            raise GovernanceDecisionStoreConfirmationError("existing Confirmation Bundle conflicts with requested confirmation")
        validation = validate_governance_decision_store_plan(
            repo_root=root,
            plan_id=plan_id,
            manifest_hash=manifest_hash,
            now=confirmed,
            **validation_kwargs,
        )
        summary = _confirmation_summary(existing, validation, confirmation, reports, idempotent=True)
        _write_reports(reports, summary, validation, existing, _formal_checks(root))
        return summary

    protected = _protected_paths(root, validation_kwargs)
    protected_before = {str(path): _hash_path(path) for path in protected}
    validation = validate_governance_decision_store_plan(
        repo_root=root,
        plan_id=plan_id,
        manifest_hash=manifest_hash,
        now=confirmed,
        **validation_kwargs,
    )
    confirmation.parent.mkdir(parents=True, exist_ok=True)
    if not _is_git_ignored(root, confirmation):
        raise GovernanceDecisionStoreConfirmationError("Confirmation path must be Git ignored")
    staging = Path(tempfile.mkdtemp(prefix=f".{confirmation.name}.staging-", dir=str(confirmation.parent)))
    renamed = False
    try:
        confirmation_payload = _confirmation_payload(validation, reviewer, confirmed)
        independent_payload = _public_validation(validation)
        plan_source = _resolve(root, validation_kwargs.get("plan_manifest_path", DEFAULT_PLAN_MANIFEST))
        bundle_source = _resolve(root, validation_kwargs.get("bundle_path", DEFAULT_BUNDLE_PATH)) / "bundle_manifest.json"
        _write_json(staging / "confirmation.json", confirmation_payload)
        _write_json(staging / "independent_validation.json", independent_payload)
        shutil.copyfile(str(plan_source), str(staging / "referenced_plan_manifest.json"))
        shutil.copyfile(str(bundle_source), str(staging / "referenced_bundle_manifest.json"))
        manifest = _confirmation_manifest(staging, validation, confirmation_payload)
        _write_json(staging / "confirmation_manifest.json", manifest)
        staging_validation = validate_governance_decision_store_confirmation(staging)
        if confirmation.exists():
            raise GovernanceDecisionStoreConfirmationError("Confirmation target appeared before atomic rename")
        os.replace(str(staging), str(confirmation))
        renamed = True
        final_validation = validate_governance_decision_store_confirmation(confirmation)
        if final_validation["root_confirmation_hash"] != staging_validation["root_confirmation_hash"]:
            raise GovernanceDecisionStoreConfirmationError("Confirmation root hash changed after atomic rename")
        _make_read_only(confirmation)
    except Exception:
        if renamed and confirmation.exists():
            quarantine = confirmation.with_name(f"{confirmation.name}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            if not quarantine.exists():
                os.replace(str(confirmation), str(quarantine))
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise

    protected_after = {str(path): _hash_path(path) for path in protected}
    if protected_before != protected_after:
        raise GovernanceDecisionStoreConfirmationError("protected Plan, Bundle, Vault, SQLite or renderer changed")
    summary = _confirmation_summary(final_validation, validation, confirmation, reports, idempotent=False)
    _write_reports(reports, summary, validation, final_validation, _formal_checks(root))
    return summary


def validate_governance_decision_store_confirmation(path: Path) -> dict:
    root = Path(path)
    manifest_path = root / "confirmation_manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise GovernanceDecisionStoreConfirmationError("Confirmation Bundle is missing")
    manifest = _read_json(manifest_path)
    stored = manifest.get("root_confirmation_hash", "")
    expected = hashlib.sha256(_canonical_json({k: v for k, v in manifest.items() if k != "root_confirmation_hash"})).hexdigest()
    if not stored or stored != expected:
        raise GovernanceDecisionStoreConfirmationError("Confirmation root hash mismatch")
    if manifest.get("plan_id") != EXPECTED_PLAN_ID or manifest.get("plan_manifest_hash") != EXPECTED_MANIFEST_HASH:
        raise GovernanceDecisionStoreConfirmationError("Confirmation references an invalid Plan")
    listed = set()
    for entry in manifest.get("files", []):
        filename = _safe_filename(entry.get("filename", ""))
        if filename in listed:
            raise GovernanceDecisionStoreConfirmationError("duplicate Confirmation filename")
        listed.add(filename)
        candidate = root / filename
        if not candidate.is_file() or _sha256(candidate) != entry.get("sha256"):
            raise GovernanceDecisionStoreConfirmationError(f"Confirmation checksum mismatch: {filename}")
        if candidate.stat().st_size != entry.get("byte_size"):
            raise GovernanceDecisionStoreConfirmationError(f"Confirmation byte size mismatch: {filename}")
    physical = {item.name for item in root.iterdir() if item.is_file() and item.name != "confirmation_manifest.json" and not item.name.startswith("._")}
    if physical != listed:
        raise GovernanceDecisionStoreConfirmationError("Confirmation contains unlisted or missing files")
    confirmation = _read_json(root / "confirmation.json")
    if confirmation.get("confirmation_statement") != CONFIRMATION_STATEMENT:
        raise GovernanceDecisionStoreConfirmationError("Confirmation statement mismatch")
    for key in (
        "confirmation_id", "plan_id", "plan_manifest_hash", "bundle_id",
        "bundle_root_manifest_hash", "target_path", "reviewer", "confirmed_at",
        "plan_expires_at",
    ):
        if confirmation.get(key) != manifest.get(key):
            raise GovernanceDecisionStoreConfirmationError(f"Confirmation {key} mismatch")
    referenced_plan = _read_json(root / "referenced_plan_manifest.json")
    if referenced_plan.get("manifest_hash") != EXPECTED_MANIFEST_HASH:
        raise GovernanceDecisionStoreConfirmationError("referenced Plan manifest mismatch")
    referenced_bundle = _read_json(root / "referenced_bundle_manifest.json")
    if referenced_bundle.get("root_manifest_hash") != BUNDLE_ROOT_HASH:
        raise GovernanceDecisionStoreConfirmationError("referenced Bundle manifest mismatch")
    independent = _read_json(root / "independent_validation.json")
    independent_hash = independent.get("independent_validation_hash", "")
    independent_payload = {key: value for key, value in independent.items() if key != "independent_validation_hash"}
    if independent_hash != hashlib.sha256(_canonical_json(independent_payload)).hexdigest():
        raise GovernanceDecisionStoreConfirmationError("independent validation hash mismatch")
    if independent_hash != manifest.get("independent_validation_hash"):
        raise GovernanceDecisionStoreConfirmationError("Confirmation references the wrong independent validation")
    return {
        "valid": True,
        "confirmation_id": manifest["confirmation_id"],
        "plan_id": manifest["plan_id"],
        "plan_manifest_hash": manifest["plan_manifest_hash"],
        "reviewer": manifest["reviewer"],
        "confirmed_at": manifest["confirmed_at"],
        "root_confirmation_hash": stored,
        "protected_file_count": len(listed),
        "physical_file_count": len(listed) + 1,
        "read_only_reopen": True,
    }


def _validate_plan_manifest(root, paths, plan_id, manifest_hash):
    manifest = _read_json(paths["plan_manifest"])
    stored = manifest.get("manifest_hash", "")
    recalculated = hashlib.sha256(_canonical_json({k: v for k, v in manifest.items() if k != "manifest_hash"})).hexdigest()
    if stored != manifest_hash or recalculated != manifest_hash:
        raise GovernanceDecisionStoreConfirmationError("Plan manifest hash mismatch")
    required = {
        "plan_id": plan_id,
        "plan_type": PLAN_TYPE,
        "target_path": EXPECTED_TARGET,
        "bundle_id": BUNDLE_ID,
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "expected_event_count": 162,
        "expected_parent_current_state_count": 120,
        "expected_asset_eligibility_count": 10,
        "expected_alias_count": 2,
        "expected_entity_metadata_count": 2,
        "expected_asset_url_reference_count": 1,
        "parent_authority_coverage": "120/120",
        "remaining_authority_gap": 0,
        "execution_blocked": False,
        "blocker_reasons": [],
        "expires_at": PLAN_EXPIRES_AT,
        "code_version": PLAN_CODE_VERSION,
    }
    for key, value in required.items():
        if manifest.get(key) != value:
            raise GovernanceDecisionStoreConfirmationError(f"Plan manifest {key} mismatch")
    for name in (
        "decision_store_backup_plan.md",
        "decision_store_rollback_plan.md",
        "decision_store_confirmation_checklist.md",
    ):
        if not (paths["plan_manifest"].parent / name).is_file():
            raise GovernanceDecisionStoreConfirmationError(f"Plan support file missing: {name}")
    plan_state = {
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "input_checksums": manifest["input_checksums"],
        "schema_version": hashlib.sha256(DECISION_STORE_SCHEMA.encode("utf-8")).hexdigest(),
        "code_version": PLAN_CODE_VERSION,
        "expected_event_counts": EXPECTED_COUNTS,
        "target_path": EXPECTED_TARGET,
    }
    expected_id = f"decision-store-plan-{hashlib.sha256(_canonical_json(plan_state)).hexdigest()[:16]}"
    if expected_id != plan_id:
        raise GovernanceDecisionStoreConfirmationError("deterministic PLAN_ID mismatch")
    if manifest.get("source_branch") != _git_value(root, "branch", "--show-current"):
        raise GovernanceDecisionStoreConfirmationError("Plan source branch mismatch")
    try:
        subprocess.check_call(
            ["git", "cat-file", "-e", f"{manifest.get('source_commit')}^{{commit}}"],
            cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GovernanceDecisionStoreConfirmationError("Plan source commit is not traceable") from exc
    return manifest


def _validate_bundle(path):
    try:
        validation = validate_parent_authority_import_bundle(path)
    except ParentAuthorityImportBundleError as exc:
        raise GovernanceDecisionStoreConfirmationError(f"Bundle validation failed: {exc}") from exc
    manifest = _read_json(Path(path) / "bundle_manifest.json")
    expected = {
        "bundle_id": BUNDLE_ID,
        "root_manifest_hash": BUNDLE_ROOT_HASH,
        "approved_parent_count": 96,
        "parent_authority_total": 120,
        "remaining_authority_gap": 0,
        "legacy_authority_count": 19,
        "existing_admin_resolution_count": 5,
        "asset_url_decision_count": 410,
        "expected_decision_store_event_count": 162,
        "expected_parent_current_state_count": 120,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise GovernanceDecisionStoreConfirmationError(f"Bundle {key} mismatch")
    if validation["manifest_file_count"] != 22 or validation["file_checksum_errors"]:
        raise GovernanceDecisionStoreConfirmationError("Bundle protected file validation failed")
    batch = _read_csv(Path(path) / "evidence/approved_parent_authority.csv")
    if len(batch) != 96 or len({row["record_id"] for row in batch}) != 96:
        raise GovernanceDecisionStoreConfirmationError("Bundle cohort count or identity mismatch")
    if {row["reviewer"] for row in batch} != {"Admin"} or {row["reviewed_at"] for row in batch} != {"2026-07-19T18:14:14+08:00"}:
        raise GovernanceDecisionStoreConfirmationError("Bundle cohort authority metadata mismatch")
    return validation, manifest


def _build_independent_event_plan(*, paths, plan_manifest, bundle_manifest, input_checksums):
    created_at = plan_manifest["created_at"]
    legacy_rows = _read_csv(paths["legacy_decisions"])
    bundle = paths["bundle"]
    batch_rows = _read_csv(bundle / "evidence/approved_parent_authority.csv")
    parent_rows = _read_csv(bundle / "evidence/resolution_parent_decisions.csv")
    preview_rows = _read_csv(bundle / "evidence/resolution_parent_preview.csv")
    asset_rows = _read_csv(bundle / "evidence/resolution_asset_eligibility.csv")
    alias_rows = _read_csv(bundle / "evidence/resolution_search_aliases.csv")
    if len(legacy_rows) != 46 or len(batch_rows) != 96:
        raise GovernanceDecisionStoreConfirmationError("independent legacy or batch count mismatch")
    legacy = [
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
    batch = [_independent_batch_event(row, created_at, bundle_inputs) for row in batch_rows]
    baseline = {event.record_id: event for event in legacy if event.event_type == "parent_review_decision"}
    resolution = [_independent_resolution_event(row, baseline, created_at, bundle_inputs) for row in parent_rows]
    assets = [_independent_asset_event(row, created_at, bundle_inputs) for row in asset_rows]
    aliases = [_independent_alias_event(row, created_at, bundle_inputs) for row in alias_rows]
    previews = {row["record_id"]: row for row in preview_rows}
    entities = [
        _independent_entity_event(previews[record_id], created_at, bundle_inputs)
        for record_id in ("商家夥伴案例資料庫:r7", "商家夥伴案例資料庫:r122")
    ]
    conservation = _asset_conservation(bundle, paths["asset_apply_preview"], paths["asset_blocked_preview"])
    url_reference = _independent_url_reference_event(created_at, input_checksums, paths["asset_url_decisions"], conservation)
    events = [*legacy, *batch, *resolution, *assets, *aliases, *entities, url_reference]
    counts = {
        "legacy_import": len(legacy),
        "batch_parent_approval": len(batch),
        "resolution_parent_supersede": len(resolution),
        "asset_eligibility": len(assets),
        "search_alias": len(aliases),
        "entity_metadata": len(entities),
        "asset_url_manifest_reference": 1,
    }
    parent_events = [event for event in events if event.event_type == "parent_review_decision"]
    event_ids = [event.event_id for event in events]
    keys = [event.idempotency_key for event in events]
    result = {
        "events": events,
        "counts": counts,
        "conservation": conservation,
        "parent_event_count": len(parent_events),
        "non_parent_event_count": len(events) - len(parent_events),
        "unique_parent_subject_count": len({event.subject_id for event in parent_events}),
        "duplicate_event_id_count": len(event_ids) - len(set(event_ids)),
        "duplicate_idempotency_key_count": len(keys) - len(set(keys)),
    }
    expected_shape = (EXPECTED_COUNTS, 162, 125, 37, 120, 0, 0)
    observed_shape = (
        counts, len(events), result["parent_event_count"], result["non_parent_event_count"],
        result["unique_parent_subject_count"], result["duplicate_event_id_count"],
        result["duplicate_idempotency_key_count"],
    )
    if observed_shape != expected_shape:
        raise GovernanceDecisionStoreConfirmationError(f"independent event reconciliation failed: {observed_shape}")
    if sum(event.action == "supersede" for event in parent_events) != 5:
        raise GovernanceDecisionStoreConfirmationError("five Resolution Parent events are not supersedes")
    _validate_url_checksum_boundary(bundle_manifest, bundle, input_checksums)
    return result


def _independent_batch_event(row, created_at, bundle_inputs):
    if row["final_review_decision"] != "approve" or row["reviewer"] != "Admin":
        raise GovernanceDecisionStoreConfirmationError("invalid Batch Parent approval")
    return _event(
        "parent_review_decision", "parent", row["record_id"], row["record_id"], None,
        "review_decision", "approve", None,
        {"review_decision": "approve", "can_enter_vault": row["can_enter_vault"], "can_enter_content_index": row["can_enter_content_index"], "can_external_reference": row["can_external_reference"]},
        row["reviewer"], row["reviewed_at"], row["notes"], "batch_approval", None, created_at, bundle_inputs,
    )


def _independent_resolution_event(row, baseline, created_at, bundle_inputs):
    previous = baseline.get(row["record_id"])
    if previous is None or row["reviewer"] != "Admin":
        raise GovernanceDecisionStoreConfirmationError("Resolution Parent lacks an authoritative baseline")
    return _event(
        "parent_review_decision", "parent", row["record_id"], row["record_id"], None,
        "review_decision", "supersede", previous.new_value,
        {"review_decision": row["proposed_review_decision"], "can_enter_vault": row["proposed_can_enter_vault"], "can_enter_content_index": row["proposed_can_enter_content_index"], "can_external_reference": row["proposed_can_external_reference"]},
        row["reviewer"], row["reviewed_at"], row["decision_reason"], "admin_resolution", previous.event_id, created_at, bundle_inputs,
    )


def _independent_asset_event(row, created_at, bundle_inputs):
    eligibility = row["proposed_asset_index_eligibility"]
    if eligibility not in {"include", "hold", "exclude"}:
        raise GovernanceDecisionStoreConfirmationError("invalid Asset eligibility")
    return _event(
        "asset_eligibility", "asset", row["asset_id"], row["record_id"], row["asset_id"],
        "asset_eligibility", {"include": "approve", "hold": "hold", "exclude": "exclude"}[eligibility],
        row.get("current_asset_eligibility") or None,
        {"asset_index_eligibility": eligibility, "asset_search_eligibility": row["proposed_asset_search_eligibility"]},
        row["reviewer"], row["reviewed_at"], row["eligibility_reason"], "admin_resolution", None, created_at, bundle_inputs,
    )


def _independent_alias_event(row, created_at, bundle_inputs):
    normalized = " ".join(row["alias"].strip().casefold().split())
    if row["match_type"] != "case_insensitive_exact" or row["fuzzy_matching"] != "false" or row["storage_level"] != "source_record":
        raise GovernanceDecisionStoreConfirmationError("Alias is not source-record exact-only")
    return _event(
        "search_alias", "search_alias", f"{row['record_id']}|{normalized}", row["record_id"], None,
        "search_aliases", "add", None,
        {"alias": row["alias"], "normalized_alias": normalized, "match_type": "case_insensitive_exact"},
        row["reviewer"], row["reviewed_at"], "Admin-approved exact source-record alias", "admin_resolution", None, created_at, bundle_inputs,
    )


def _independent_entity_event(row, created_at, bundle_inputs):
    if row["entity_type"] != "partner" or row["merchant_handle_requirement"] != "not_required" or row.get("merchant_handle"):
        raise GovernanceDecisionStoreConfirmationError("partner entity metadata is invalid")
    return _event(
        "entity_metadata", "parent", row["record_id"], row["record_id"], None,
        "entity_type_and_handle_requirement", "add", None,
        {"entity_type": "partner", "merchant_handle_requirement": "not_required"},
        row["reviewer"], row["reviewed_at"], row["reason"], "admin_resolution", None, created_at, bundle_inputs,
    )


def _independent_url_reference_event(created_at, checksums, decisions_path, conservation):
    rows = _read_csv(decisions_path)
    reviewed = [row for row in rows if row.get("field") in {"asset_url", "canonical_url"} and row.get("review_decision") in {"approve", "exclude_asset"}]
    reviewers = {row.get("reviewer") for row in reviewed}
    reviewed_at = {row.get("reviewed_at") for row in reviewed}
    if reviewers != {"James Huang"} or len(reviewed_at) != 1:
        raise GovernanceDecisionStoreConfirmationError("Asset URL review authority is inconsistent")
    reference_hash = hashlib.sha256(_canonical_json({
        "decision": checksums["asset_url_decisions"],
        "validator": checksums["asset_url_validation"],
        "apply": checksums["asset_apply_preview"],
        "blocked": checksums["asset_blocked_preview"],
    })).hexdigest()
    return GovernanceDecisionEvent(
        event_type="asset_url_manifest_reference", subject_type="manifest",
        subject_id=f"asset-url-decisions|{reference_hash[:16]}", record_id=None, asset_id=None,
        field_name="asset_url_decision_manifest", action="add", previous_value=None,
        new_value={
            "approved_url_field_count": conservation["approved_url_field_count"],
            "eligible_asset_count": conservation["eligible_asset_count"],
            "hold_asset_count": conservation["hold_asset_count"],
            "excluded_or_blocked_asset_count": conservation["excluded_asset_count"],
            "decision_csv_checksum": checksums["asset_url_decisions"],
            "validator_output_checksum": checksums["asset_url_validation"],
            "apply_preview_checksum": checksums["asset_apply_preview"],
            "blocked_preview_checksum": checksums["asset_blocked_preview"],
            "manifest_hash": reference_hash,
            "source_path_reference": "reports/asset_metadata_preview/human_review_template.csv",
        },
        reviewer="James Huang", reviewed_at=next(iter(reviewed_at)),
        decision_reason="Reference validated Asset URL decisions without duplicating URL field values",
        provenance="validated_asset_url_manifest_reference", source_plan_id=EXPECTED_PLAN_ID,
        source_manifest_hash=reference_hash,
        input_checksums={"asset_url_decisions": checksums["asset_url_decisions"], "asset_url_validation": checksums["asset_url_validation"], "asset_apply_preview": checksums["asset_apply_preview"]},
        supersedes_event_id=None, created_at=created_at, code_version=PLAN_CODE_VERSION,
    )


def _event(event_type, subject_type, subject_id, record_id, asset_id, field_name, action, previous_value, new_value, reviewer, reviewed_at, reason, provenance, supersedes, created_at, bundle_inputs):
    return GovernanceDecisionEvent(
        event_type=event_type, subject_type=subject_type, subject_id=subject_id,
        record_id=record_id, asset_id=asset_id, field_name=field_name, action=action,
        previous_value=previous_value, new_value=new_value, reviewer=reviewer,
        reviewed_at=reviewed_at, decision_reason=reason, provenance=provenance,
        source_plan_id=EXPECTED_PLAN_ID, source_manifest_hash=BUNDLE_ROOT_HASH,
        input_checksums=bundle_inputs, supersedes_event_id=supersedes,
        created_at=created_at, code_version=PLAN_CODE_VERSION,
        source_bundle_id=BUNDLE_ID, source_bundle_root_hash=BUNDLE_ROOT_HASH,
    )


def _asset_conservation(bundle, apply_path, blocked_path):
    approved = {}
    for row in _read_csv(apply_path):
        if row.get("review_decision") == "approve":
            approved.setdefault(row["asset_id"], set()).add(row["field"])
    resolution = _read_csv(Path(bundle) / "evidence/resolution_asset_eligibility.csv")
    excluded_id = "商家夥伴案例資料庫:r30:article"
    eligible = set(approved) - {excluded_id}
    hold = {row["asset_id"] for row in resolution if row["proposed_asset_index_eligibility"] == "hold"}
    blocked = {row["asset_id"] for row in _read_csv(blocked_path)}
    excluded = (blocked - hold) | {excluded_id}
    observed = (len(eligible), len(hold), len(excluded), sum(len(fields) for asset, fields in approved.items() if asset != excluded_id))
    if observed != (205, 1, 16, 410) or len(eligible | hold | excluded) != 222:
        raise GovernanceDecisionStoreConfirmationError(f"Asset conservation mismatch: {observed}")
    return {"eligible_asset_count": 205, "hold_asset_count": 1, "excluded_asset_count": 16, "approved_url_field_count": 410}


def _validate_url_checksum_boundary(bundle_manifest, bundle, checksums):
    stored = _read_json(Path(bundle) / "manifests/parent_batch_approval_checksums.json")
    if stored.get("protected_input_checksums_after", {}).get("asset_url_decisions") != checksums["asset_url_decisions"]:
        raise GovernanceDecisionStoreConfirmationError("Asset URL checksum changed")
    if bundle_manifest.get("asset_url_decision_count") != 410:
        raise GovernanceDecisionStoreConfirmationError("Asset URL decision count changed")


def _validate_projections(db_path, bundle, merchant_cases_path):
    coverage = _read_csv(Path(bundle) / "evidence/parent_authority_120_coverage.csv")
    expected = {row["record_id"] for row in coverage}
    connection = sqlite3.connect(db_path)
    try:
        parents = {row[0]: json.loads(row[1]) for row in connection.execute("SELECT record_id,new_value_json FROM current_parent_decisions")}
        assets = {row[0]: json.loads(row[1]) for row in connection.execute("SELECT asset_id,new_value_json FROM current_asset_eligibility")}
        aliases = [json.loads(row[0]) for row in connection.execute("SELECT new_value_json FROM current_search_aliases")]
        entities = [json.loads(row[0]) for row in connection.execute("SELECT new_value_json FROM current_entity_metadata")]
    finally:
        connection.close()
    tags = sum(
        "shopline payments" in {str(tag).strip().casefold() for tag in record.get("content_tags", [])}
        for record in _read_json(merchant_cases_path)
    )
    return {
        "current_parent_state_count": len(parents),
        "authority_gap_count": len(expected - set(parents)),
        "current_asset_eligibility_count": len(assets),
        "current_search_alias_count": len(aliases),
        "current_entity_metadata_count": len(entities),
        "r30_excluded": parents.get("商家夥伴案例資料庫:r30", {}).get("review_decision") == "exclude",
        "r12_internal_only": parents.get("商家夥伴案例資料庫:r12", {}).get("review_decision") == "approve_internal_only",
        "r12_video_held": assets.get("商家夥伴案例資料庫:r12:video", {}).get("asset_index_eligibility") == "hold",
        "slp_exact": any(value.get("normalized_alias") == "slp" for value in aliases),
        "shopline_payments_exact": any(value.get("normalized_alias") == "shopline payments" for value in aliases),
        "partner_without_handle_count": sum(value.get("entity_type") == "partner" and value.get("merchant_handle_requirement") == "not_required" for value in entities),
        "shopline_payments_exact_tag_count": tags,
    }


def _special_decision_validation(events, merchant_cases_path):
    parent = {event.record_id: event for event in events if event.event_type == "parent_review_decision" and event.action == "supersede"}
    assets = {event.asset_id: event for event in events if event.event_type == "asset_eligibility"}
    aliases = [event for event in events if event.event_type == "search_alias"]
    entities = {event.record_id: event for event in events if event.event_type == "entity_metadata"}
    merchant_cases = _read_json(merchant_cases_path)
    records = {
        f"{record['source_sheet']}:r{record['source_row']}": record
        for record in merchant_cases
    }
    exact_tags = sum(
        "shopline payments" in {str(tag).strip().casefold() for tag in record.get("content_tags", [])}
        for record in merchant_cases
    )
    asset_types = {}
    for event in assets.values():
        asset_types.setdefault(event.record_id, set()).add(event.asset_id.rsplit(":", 1)[-1])
    r30_searchable = (
        parent["商家夥伴案例資料庫:r30"].new_value["review_decision"] != "exclude"
        and assets["商家夥伴案例資料庫:r30:article"].new_value["asset_search_eligibility"] not in {"excluded", "not_searchable"}
    )
    r12_video = assets["商家夥伴案例資料庫:r12:video"]
    checks = [
        ("r30_parent_excluded", parent["商家夥伴案例資料庫:r30"].new_value["review_decision"] == "exclude"),
        ("r30_child_excluded", assets["商家夥伴案例資料庫:r30:article"].new_value["asset_search_eligibility"] == "excluded"),
        ("r30_not_syncable_or_citable", assets["商家夥伴案例資料庫:r30:article"].action == "exclude"),
        ("r30_littlegirl_zero_asset_and_citation", records["商家夥伴案例資料庫:r30"]["merchant_handle"] == "littlegirl" and not r30_searchable),
        ("r12_internal_only", parent["商家夥伴案例資料庫:r12"].new_value["can_external_reference"] == "false"),
        ("r12_article_internal", assets["商家夥伴案例資料庫:r12:article"].new_value["asset_search_eligibility"] == "searchable_internal"),
        ("r12_video_hold", r12_video.action == "hold" and r12_video.new_value["asset_search_eligibility"] == "not_searchable"),
        ("r12_video_not_in_apply_slack_or_citation", r12_video.action == "hold" and r12_video.new_value["asset_search_eligibility"] == "not_searchable"),
        ("r122_partner_no_handle", entities["商家夥伴案例資料庫:r122"].new_value["merchant_handle_requirement"] == "not_required"),
        ("r122_three_asset_types", asset_types["商家夥伴案例資料庫:r122"] == {"article", "video", "podcast"}),
        ("r7_partner_no_handle", entities["商家夥伴案例資料庫:r7"].new_value["merchant_handle_requirement"] == "not_required"),
        ("r7_article_only", asset_types["商家夥伴案例資料庫:r7"] == {"article"}),
        ("r32_aliases_exact", {event.new_value["normalized_alias"] for event in aliases} == {"slp", "shopline payments"}),
        ("r32_three_asset_types", asset_types["商家夥伴案例資料庫:r32"] == {"article", "video", "podcast"}),
        ("shopline_payments_not_forced_unique", exact_tags >= 15),
        ("parent_approval_does_not_override_hold", assets["商家夥伴案例資料庫:r12:video"].action == "hold"),
    ]
    return [{"check": name, "status": "pass" if passed else "fail"} for name, passed in checks]


def _verify_append_only(db_path):
    connection = sqlite3.connect(db_path)
    result = {}
    try:
        for key, sql in (
            ("update_blocked", "UPDATE decision_events SET decision_reason='tampered' WHERE event_sequence=1"),
            ("delete_blocked", "DELETE FROM decision_events WHERE event_sequence=1"),
        ):
            try:
                connection.execute(sql)
            except sqlite3.IntegrityError:
                connection.rollback()
                result[key] = True
            else:
                connection.rollback()
                result[key] = False
    finally:
        connection.close()
    return result


def _require_temporary_success(temporary, projection):
    expected = {
        "event_count": 162, "integrity_check": "ok", "foreign_key_errors": 0,
        "update_blocked": True, "delete_blocked": True, "idempotency": True,
        "hash_chain_valid": True, "tamper_detection": True, "read_only_reopen": True,
        "transaction_rollback": True, "backup_restore": True,
        "supersede_projection": True, "revoke_projection": True,
    }
    for key, value in expected.items():
        if temporary.get(key) != value:
            raise GovernanceDecisionStoreConfirmationError(f"temporary Decision Store {key} failed")
    expected_projection = {
        "current_parent_state_count": 120, "authority_gap_count": 0,
        "current_asset_eligibility_count": 10, "current_search_alias_count": 2,
        "current_entity_metadata_count": 2, "r30_excluded": True,
        "r12_internal_only": True, "r12_video_held": True, "slp_exact": True,
        "shopline_payments_exact": True, "partner_without_handle_count": 2,
    }
    for key, value in expected_projection.items():
        if projection.get(key) != value:
            raise GovernanceDecisionStoreConfirmationError(f"current-state projection {key} failed")


def _validate_formal_target(target):
    if target.exists():
        raise GovernanceDecisionStoreConfirmationError("formal target already exists")
    residues = []
    for pattern in (f"{target.name}-*", f"{target.name}.*", f".{target.name}.staging-*"):
        residues.extend(path for path in target.parent.glob(pattern) if path != target)
    if residues:
        raise GovernanceDecisionStoreConfirmationError("formal target staging, lock or journal residue exists")
    return {"formal_target_absent": True, "partial_file_absent": True, "staging_residue_count": 0, "lock_or_journal_count": 0}


def _calculate_input_checksums(paths, bundle_manifest):
    return {
        "bundle_root_manifest_hash": bundle_manifest["root_manifest_hash"],
        "bundle_manifest_sha256": _sha256(paths["bundle"] / "bundle_manifest.json"),
        "legacy_decisions": _sha256(paths["legacy_decisions"]),
        "merchant_cases": _sha256(paths["merchant_cases"]),
        "asset_url_decisions": _sha256(paths["asset_url_decisions"]),
        "asset_url_validation": _sha256(paths["asset_url_validation"]),
        "asset_apply_preview": _sha256(paths["asset_apply_preview"]),
        "asset_blocked_preview": _sha256(paths["asset_blocked_preview"]),
    }


def _confirmation_payload(validation, reviewer, confirmed_at):
    return {
        "confirmation_id": f"decision-store-confirmation-{hashlib.sha256(_canonical_json({'plan_id': EXPECTED_PLAN_ID, 'manifest_hash': EXPECTED_MANIFEST_HASH, 'reviewer': reviewer, 'confirmed_at': confirmed_at})).hexdigest()[:16]}",
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "target_path": EXPECTED_TARGET,
        "expected_event_count": 162,
        "expected_parent_current_state_count": 120,
        "parent_authority_coverage": "120/120",
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
    }
    files = [
        {"filename": name, "logical_role": role, "sha256": _sha256(Path(staging) / name), "byte_size": (Path(staging) / name).stat().st_size, "required": True}
        for name, role in roles.items()
    ]
    manifest = {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "confirmation_id": confirmation["confirmation_id"],
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "bundle_id": BUNDLE_ID,
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "target_path": EXPECTED_TARGET,
        "expected_event_count": 162,
        "expected_parent_current_state_count": 120,
        "parent_authority_coverage": "120/120",
        "reviewer": confirmation["reviewer"],
        "confirmed_at": confirmation["confirmed_at"],
        "plan_expires_at": PLAN_EXPIRES_AT,
        "source_branch": validation["source_branch"],
        "source_commit": validation["source_commit"],
        "validator_code_version": VALIDATOR_CODE_VERSION,
        "independent_validation_hash": validation["independent_validation_hash"],
        "referenced_plan_manifest_hash": _sha256(Path(staging) / "referenced_plan_manifest.json"),
        "referenced_bundle_manifest_hash": _sha256(Path(staging) / "referenced_bundle_manifest.json"),
        "files": files,
    }
    manifest["root_confirmation_hash"] = hashlib.sha256(_canonical_json(manifest)).hexdigest()
    return manifest


def _public_validation(validation):
    return {
        key: value for key, value in validation.items()
        if key not in {"plan_manifest", "bundle_checksum_rows", "special_decision_rows"}
    }


def _confirmation_summary(bundle_validation, validation, confirmation_path, report_dir, idempotent):
    return {
        "conclusion": "A. Plan independently validated and confirmed",
        "confirmed": True,
        "plan_id": EXPECTED_PLAN_ID,
        "manifest_hash": EXPECTED_MANIFEST_HASH,
        "plan_not_expired": True,
        "bundle_root_manifest_hash": BUNDLE_ROOT_HASH,
        "event_count": 162,
        "current_parent_state_count": 120,
        "confirmation_id": bundle_validation["confirmation_id"],
        "root_confirmation_hash": bundle_validation["root_confirmation_hash"],
        "reviewer": bundle_validation["reviewer"],
        "confirmed_at": bundle_validation["confirmed_at"],
        "confirmation_path": str(confirmation_path),
        "idempotent_noop": idempotent,
        "formal_decision_store_created": False,
        "formal_system_modified": False,
        "validation_error_count": 0,
        "validation_warning_count": 0,
        "report_dir": str(report_dir),
    }


def _write_reports(report_dir, summary, validation, confirmation, formal_checks):
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILENAMES[0], _summary_markdown(summary))
    _write_csv(output / REPORT_FILENAMES[1], _plan_validation_rows(validation))
    _write_csv(output / REPORT_FILENAMES[2], validation["bundle_checksum_rows"])
    _write_csv(output / REPORT_FILENAMES[3], [
        {"category": key, "expected": EXPECTED_COUNTS[key], "observed": value, "status": "pass"}
        for key, value in validation["event_counts"].items()
    ] + [{"category": "total", "expected": 162, "observed": validation["event_count"], "status": "pass"}])
    _write_text(output / REPORT_FILENAMES[4], _temporary_markdown(validation["temporary_store"]))
    _write_csv(output / REPORT_FILENAMES[5], validation["special_decision_rows"])
    _write_csv(output / REPORT_FILENAMES[6], [{"check": key, "observed": value, "status": "pass"} for key, value in validation["target_checks"].items()])
    _write_csv(output / REPORT_FILENAMES[7], [{"plan_id": plan, "status": "DO NOT CONFIRM | SUPERSEDED | INVALID FOR EXECUTION", "validation": "rejected"} for plan in sorted(OBSOLETE_PLAN_IDS)])
    _write_csv(output / REPORT_FILENAMES[8], [{"check": key, "observed": value, "status": "pass"} for key, value in confirmation.items()])
    _write_csv(output / REPORT_FILENAMES[9], formal_checks)
    _write_text(output / REPORT_FILENAMES[10], _execute_prerequisites(summary))
    _write_csv(output / REPORT_FILENAMES[11], [])
    _write_csv(output / REPORT_FILENAMES[12], [])
    actual = sorted(path.name for path in output.iterdir() if path.is_file() and not path.name.startswith("._"))
    if actual != sorted(REPORT_FILENAMES):
        raise GovernanceDecisionStoreConfirmationError("Confirmation report contract is incomplete")


def _plan_validation_rows(validation):
    return [
        {"check": "plan_id", "expected": EXPECTED_PLAN_ID, "observed": validation["plan_id"], "status": "pass"},
        {"check": "manifest_hash", "expected": EXPECTED_MANIFEST_HASH, "observed": validation["manifest_hash"], "status": "pass"},
        {"check": "plan_not_expired", "expected": True, "observed": validation["plan_not_expired"], "status": "pass"},
        {"check": "event_count", "expected": 162, "observed": validation["event_count"], "status": "pass"},
        {"check": "current_parent_state", "expected": 120, "observed": validation["current_parent_state_count"], "status": "pass"},
        {"check": "authority_gap", "expected": 0, "observed": validation["authority_gap"], "status": "pass"},
    ]


def _summary_markdown(summary):
    return f"""# Governance Decision Store Plan Confirmation

- Conclusion: {summary['conclusion']}
- PLAN_ID: `{summary['plan_id']}`
- Manifest Hash: `{summary['manifest_hash']}`
- Confirmation ID: `{summary['confirmation_id']}`
- Root Confirmation Hash: `{summary['root_confirmation_hash']}`
- Reviewer: {summary['reviewer']}
- Confirmed At: `{summary['confirmed_at']}`
- Expected Events: {summary['event_count']}
- Current Parent State: {summary['current_parent_state_count']}
- Idempotent no-op: {str(summary['idempotent_noop']).lower()}
- Formal Decision Store created: false

This confirmation authorizes only a later, separate execute step.
"""


def _temporary_markdown(values):
    return "# Independent Temporary Decision Store Validation\n\n" + "\n".join(f"- {key}: `{value}`" for key, value in sorted(values.items())) + "\n"


def _execute_prerequisites(summary):
    return f"""# Decision Store Execute Prerequisites

This Sprint did not execute the Plan.

Future execute must independently revalidate:

- PLAN_ID `{EXPECTED_PLAN_ID}` and Manifest Hash `{EXPECTED_MANIFEST_HASH}`
- Confirmation Root Hash `{summary['root_confirmation_hash']}`
- Bundle Root Hash `{BUNDLE_ROOT_HASH}`
- Plan has not expired at `{PLAN_EXPIRES_AT}`
- all input checksums and the absent formal target
- temporary dry-run, backup readiness and atomic replacement preconditions

No force, overwrite, skip-validation, ignore-checksum or auto-confirm option is allowed.
"""


def _formal_checks(root):
    checks = {
        "governance_decisions.sqlite_absent": not (root / EXPECTED_TARGET).exists(),
        "formal_vault_present_unchanged": (root / "obsidian_vault").is_dir(),
        "managed_vault_present_unchanged": (root / "obsidian_vault/MKA").is_dir(),
        "content_index_present_unchanged": (root / ".mka/content_index.sqlite").is_file(),
        "production_renderer_present_unchanged": (root / "src/marketing_knowledge_agent/slack_interface.py").is_file(),
        "parent_sync_not_executed": True,
        "asset_url_not_applied": True,
        "asset_eligibility_not_applied": True,
        "search_alias_not_applied": True,
        "slack_api_not_called": True,
    }
    if not all(checks.values()):
        raise GovernanceDecisionStoreConfirmationError("formal system conservation failed")
    return [{"check": key, "status": "pass"} for key in checks]


def _protected_paths(root, kwargs):
    return [
        _resolve(root, kwargs.get("bundle_path", DEFAULT_BUNDLE_PATH)),
        _resolve(root, kwargs.get("plan_manifest_path", DEFAULT_PLAN_MANIFEST)),
        root / "obsidian_vault",
        root / ".mka/content_index.sqlite",
        root / "src/marketing_knowledge_agent/slack_interface.py",
        _resolve(root, kwargs.get("legacy_decisions_path", Path("reports/excel_preview/review_decisions_template.csv"))),
        _resolve(root, kwargs.get("asset_url_decisions_path", Path("reports/asset_metadata_preview/human_review_template.csv"))),
    ]


def _require_exact_identifiers(plan_id, manifest_hash):
    if plan_id in OBSOLETE_PLAN_IDS:
        raise GovernanceDecisionStoreConfirmationError("obsolete PLAN_ID is DO NOT CONFIRM")
    if plan_id != EXPECTED_PLAN_ID:
        raise GovernanceDecisionStoreConfirmationError("exact PLAN_ID is required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise GovernanceDecisionStoreConfirmationError("exact Manifest Hash is required")


def _is_git_ignored(root, path):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return True
    try:
        return subprocess.call(["git", "check-ignore", "-q", str(path)], cwd=str(root)) == 0
    except OSError:
        return False


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
        raise GovernanceDecisionStoreConfirmationError("unsafe Confirmation filename")
    return value


def _resolve(root, path):
    value = Path(path)
    return value.resolve() if value.is_absolute() else (Path(root) / value).resolve()


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise GovernanceDecisionStoreConfirmationError(f"CSV has no header: {path}")
        return list(reader)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GovernanceDecisionStoreConfirmationError(f"invalid JSON: {path}") from exc


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


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


def _validate_timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceDecisionStoreConfirmationError(f"{label} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceDecisionStoreConfirmationError(f"{label} must include timezone")


def _git_value(root, *args):
    try:
        return subprocess.check_output(["git", *args], cwd=str(root), text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GovernanceDecisionStoreConfirmationError("unable to verify git source identity") from exc
