from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .governance_decision_store_existing_validation import (
    EXPECTED_DATABASE_SHA256,
    EXPECTED_DATABASE_SIZE,
    EXPECTED_EXECUTION_ROOT_HASH,
    validate_existing_governance_decision_store,
)


EXPECTED_PLAN_ID = "store-data-sync-plan-v2-4c8eb2a08b399da4"
EXPECTED_MANIFEST_HASH = "e9101f37915e478beaf54c7969d979ce97b3109aee5677c4d2591886a6b2935c"
EXPECTED_MANAGED_DELTA_HASH = "a329349aa7c37f0ca5f750ebb059377ab1a0d08c63fc12d6ad052cbaec82adf1"
EXPECTED_SQLITE_DELTA_HASH = "0151ebd95c44e1f77e95027c1a438fb40764db4943c3aa78681e62a522179abb"
EXPECTED_CONTRACT_HASH = "5ac1666ea1d2e80ccdabb794d76a8da1edf1b517e2d1d172f365e3ec5cf8accf"
PLAN_EXPIRES_AT = "2026-07-28T17:04:13+08:00"
PLAN_SOURCE_COMMIT = "53fa7e16a13df2883a6b2e252d2f19bab787c59e"
PLAN_CODE_VERSION = "store-data-sync-materialization-boundary-plan-v2"
VALIDATOR_CODE_VERSION = "store-data-sync-plan-v2-independent-confirmation-v1"
CONFIRMATION_SCHEMA_VERSION = "1.0"

DEFAULT_PLAN_MANIFEST = Path("reports/parent_sync_plan_v2/store_data_sync_plan_v2_manifest.json")
DEFAULT_MANAGED_DELTA = Path("reports/parent_sync_plan_v2/managed_vault_delta_manifest.json")
DEFAULT_FORMAL_DELTA = Path("reports/parent_sync_plan_v2/formal_sqlite_delta_manifest.json")
DEFAULT_DECISION_STORE = Path("data/governance/governance_decisions.sqlite")
DEFAULT_EXECUTION_BUNDLE = Path(
    "data/governance/executions/decision-store-schema-v2-plan-2aab43cd463170f2"
)
DEFAULT_PARENT_SOURCE = Path("reports/excel_preview/merchant_cases.json")
DEFAULT_ASSET_INVENTORY = Path("reports/asset_metadata_preview/asset_metadata_inventory.csv")
DEFAULT_ASSET_ELIGIBLE = Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv")
DEFAULT_ASSET_BLOCKED = Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv")
DEFAULT_ASSET_URL_DECISIONS = Path("reports/asset_metadata_preview/human_review_template.csv")
DEFAULT_MANAGED_VAULT = Path("obsidian_vault/MKA")
DEFAULT_FORMAL_VAULT = Path("obsidian_vault")
DEFAULT_FORMAL_SQLITE = Path(".mka/content_index.sqlite")
DEFAULT_RENDERER = Path("src/marketing_knowledge_agent/slack_interface.py")
DEFAULT_CONFIRMATION_PATH = Path("data/governance/confirmations") / EXPECTED_PLAN_ID
DEFAULT_REPORT_DIR = Path("reports/store_data_sync_plan_v2_confirmation")

AUDIT_ONLY_FIELDS = (
    "decision_event_id", "decision_event_hash", "decision_reviewer",
    "decision_reviewed_at", "decision_provenance",
)
ACTION_NAMES = (
    "create", "update", "no_change", "remove_from_content_projection",
    "retain_governance_only", "blocked", "manual_review",
)
GOVERNANCE_ONLY_IDS = {
    f"商家夥伴案例資料庫:{value}"
    for value in ("r30", "r57", "r83", "r87", "r101", "r102", "r103", "r107", "r116", "r121")
}
CREATE_IDS = {
    f"商家夥伴案例資料庫:{value}" for value in ("r7", "r12", "r32", "r122")
}
NOT_PROJECTED_IDS = GOVERNANCE_ONLY_IDS | {"商家夥伴案例資料庫:r20"}
REPORT_FILENAMES = (
    "confirmation_summary.md",
    "decision_store_revalidation.csv",
    "materialization_contract_validation.csv",
    "audit_only_exclusion_validation.csv",
    "reconciliation_validation.csv",
    "managed_vault_delta_validation.csv",
    "formal_sqlite_delta_validation.csv",
    "governance_only_validation.csv",
    "r20_validation.csv",
    "four_create_validation.csv",
    "special_record_validation.csv",
    "asset_url_boundary_validation.csv",
    "candidate_validation.md",
    "offline_search_validation.md",
    "plan_identity_validation.csv",
    "confirmation_bundle_validation.csv",
    "formal_system_unchanged_validation.csv",
    "validation_errors.csv",
    "validation_warnings.csv",
)


class StoreDataSyncPlanV2ConfirmationError(RuntimeError):
    pass


def validate_store_data_sync_plan_v2(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    plan_manifest_path: Path = DEFAULT_PLAN_MANIFEST,
    managed_delta_path: Path = DEFAULT_MANAGED_DELTA,
    formal_delta_path: Path = DEFAULT_FORMAL_DELTA,
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
    temporary_root: Optional[Path] = None,
    now: Optional[str] = None,
) -> dict:
    _require_exact_identity(plan_id, manifest_hash)
    validated_at = now or datetime.now().astimezone().isoformat(timespec="seconds")
    timestamp = _timestamp(validated_at)
    if timestamp > datetime.fromisoformat(PLAN_EXPIRES_AT):
        raise StoreDataSyncPlanV2ConfirmationError("Plan expired; regenerate a new Plan")

    root = Path(repo_root).resolve()
    paths = {
        "plan": _resolve(root, plan_manifest_path),
        "managed_delta": _resolve(root, managed_delta_path),
        "formal_delta": _resolve(root, formal_delta_path),
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
    }
    for label, path in paths.items():
        if not path.exists():
            raise StoreDataSyncPlanV2ConfirmationError(f"required {label} input is missing: {path}")

    protected = (
        paths["decision_store"], paths["execution_bundle"], paths["managed_vault"],
        paths["formal_vault"], paths["formal_sqlite"], paths["renderer"], paths["plan"],
        paths["managed_delta"], paths["formal_delta"],
    )
    before = {str(path): _hash_path(path) for path in protected}
    sidecars_before = _sqlite_sidecars(paths["decision_store"]) | _sqlite_sidecars(paths["formal_sqlite"])

    plan = _read_json(paths["plan"])
    _validate_plan_manifest_static(plan)
    if subprocess.run(
        ["git", "cat-file", "-e", f"{PLAN_SOURCE_COMMIT}^{{commit}}"], cwd=root,
        check=False, capture_output=True, text=True,
    ).returncode != 0:
        raise StoreDataSyncPlanV2ConfirmationError("Plan source commit is not traceable")
    consumer_validation = _validate_consumers(root, paths["formal_sqlite"])
    contract = _materialization_contract()

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-store-sync-v2-independent-", dir=str(temp_parent) if temp_parent else None
    ) as temporary_name:
        temporary = Path(temporary_name)
        store_health = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            execution_bundle_path=paths["execution_bundle"],
            report_dir=temporary / "store-health",
            temporary_root=temporary / "store-health-work",
        )
        source = _load_parent_source(paths["parent_source"])
        store = _load_store(paths["decision_store"])
        desired = _build_desired_state(source, store)
        vault = _load_vault(paths["managed_vault"])
        formal = _load_formal_sqlite(paths["formal_sqlite"])
        managed = _managed_reconciliation(desired, vault, formal)
        formal_plan = _formal_reconciliation(desired, formal)
        assets = _build_assets(desired, store["assets"], paths)
        candidate_path = temporary / "independent-candidate.sqlite"
        candidate = _build_candidate(candidate_path, desired, assets, store["aliases"])
        search = _offline_search(candidate_path)

    after = {str(path): _hash_path(path) for path in protected}
    sidecars_after = _sqlite_sidecars(paths["decision_store"]) | _sqlite_sidecars(paths["formal_sqlite"])
    formal_unchanged = before == after and sidecars_before == sidecars_after
    if not formal_unchanged:
        raise StoreDataSyncPlanV2ConfirmationError("formal system changed during independent validation")

    counts = _action_counts(managed["rows"])
    managed_delta = [
        row for row in managed["delta"]
        if row["action"] in {"create", "update", "remove_from_content_projection"}
    ]
    managed_validation_rows = [{
        **row,
        "current_path": row["rollback_path"],
        "proposed_path": row["target_path"],
        "old_values": row["before_values"],
        "new_values": row["after_values"],
        "authoritative_source": "decision_store_current_state_and_parent_source_metadata",
        "write_required": True,
    } for row in managed_delta]
    formal_delta = [
        row for row in formal_plan["rows"] if row["action"] in {"create", "update", "remove"}
    ]
    managed_hash = _hash_json(managed_delta)
    formal_hash = _hash_json(formal_delta)
    contract_hash = _hash_json(contract)
    managed_projection = [_managed_payload(row) for row in desired if row["can_enter_vault"]]
    formal_projection = [_formal_payload(row) for row in desired if row["can_enter_content_index"]]
    input_checksums = _input_checksums(paths)
    identity = {
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "field_materialization_contract_hash": contract_hash,
        "managed_vault_delta_hash": managed_hash,
        "formal_sqlite_delta_hash": formal_hash,
        "target_paths": {"managed_vault": "obsidian_vault/MKA", "formal_sqlite": ".mka/content_index.sqlite"},
        "counts": {**counts, "managed_vault_target": len(managed_projection), "formal_sqlite_target": len(formal_projection)},
        "code_version": PLAN_CODE_VERSION,
        "source_commit": PLAN_SOURCE_COMMIT,
    }
    reproduced_plan_id = "store-data-sync-plan-v2-" + _hash_json(identity)[:16]
    reproduced_manifest_hash = _hash_json({key: value for key, value in plan.items() if key != "manifest_hash"})
    plan_identity_valid = all((
        reproduced_plan_id == EXPECTED_PLAN_ID,
        reproduced_manifest_hash == EXPECTED_MANIFEST_HASH,
        contract_hash == EXPECTED_CONTRACT_HASH,
        managed_hash == EXPECTED_MANAGED_DELTA_HASH,
        formal_hash == EXPECTED_SQLITE_DELTA_HASH,
        input_checksums == plan["input_checksums"],
        _hash_json(desired) == plan["full_desired_state_hash"],
        _hash_json(managed_projection) == plan["managed_vault_projection_hash"],
        _hash_json(formal_projection) == plan["formal_sqlite_projection_hash"],
    ))
    if not plan_identity_valid:
        raise StoreDataSyncPlanV2ConfirmationError("independent Plan identity reproduction failed")

    governance = _governance_rows(desired, managed["rows"], vault, formal)
    not_projected = [row for row in formal_plan["rows"] if row["action"] == "not_projected"]
    creates = _four_creates(desired, managed["rows"], managed_projection, vault, formal)
    asset_boundary = _asset_boundary(assets, paths["asset_url_decisions"])
    special = _special_validation(desired, managed["rows"], assets, search)
    audit_occurrences = sum(
        field in AUDIT_ONLY_FIELDS
        for row in managed_delta + formal_delta for field in row["changed_fields"]
    ) + sum(field in row for row in managed_projection + formal_projection for field in AUDIT_ONLY_FIELDS)
    formal_existing_diffs = sum(
        len(row["changed_fields"]) for row in formal_plan["rows"]
        if row["current_presence"] and row["desired_presence"]
    )
    errors = _validation_errors(
        desired, counts, managed_delta, formal_plan["rows"], governance, not_projected,
        creates, candidate, special, asset_boundary, audit_occurrences,
        formal_existing_diffs, consumer_validation, store_health,
    )
    warnings = []
    if errors:
        raise StoreDataSyncPlanV2ConfirmationError("independent validation failed: " + ", ".join(errors))

    result = {
        "valid": True,
        "plan_id": plan_id,
        "manifest_hash": manifest_hash,
        "plan_not_expired": True,
        "plan_expires_at": PLAN_EXPIRES_AT,
        "validated_at": validated_at,
        "generator_called": False,
        "decision_store_validation": {
            "sha256": store_health["database_sha256_after"],
            "byte_size": store_health["database_size_after"],
            "integrity_check": store_health["integrity_check"],
            "foreign_key_errors": store_health["foreign_key_errors"],
            "event_count": store_health["event_count"],
            "current_parent_state": store_health["current_parent_state_count"],
            "authority_gap": store_health["authority_gap"],
            "hash_chain_valid": store_health["hash_chain_validation"]["valid"],
            "execution_root_hash": store_health["execution_bundle"]["root_execution_hash"],
            "unchanged": store_health["formal_database_unchanged"],
        },
        "consumer_contract_validation": consumer_validation,
        "formal_schema_extended": False,
        "materialization_contract": contract,
        "field_materialization_contract_hash": contract_hash,
        "authoritative_records": desired,
        "authoritative_record_count": len(desired),
        "reconciliation": managed["rows"],
        "reconciliation_count": len(managed["rows"]),
        "unique_record_id_count": len({row["record_id"] for row in desired}),
        "authority_gap": 0,
        "action_counts": counts,
        "managed_vault_counts": {
            "current": len(vault), "create": counts["create"], "update": counts["update"],
            "no_change": counts["no_change"], "target": len(managed_projection),
            "path_collisions": _path_collision_count(managed["rows"]),
        },
        "managed_vault_delta": managed_validation_rows,
        "managed_vault_delta_records": managed_delta,
        "managed_vault_delta_hash": managed_hash,
        "formal_sqlite_counts": _formal_counts(formal, formal_plan["rows"], formal_projection),
        "formal_sqlite_delta": formal_delta,
        "formal_sqlite_delta_hash": formal_hash,
        "formal_existing_consumer_diff_count": formal_existing_diffs,
        "not_projected": not_projected,
        "governance_only_records": governance,
        "r20_vault_only_valid": _r20_valid(desired, vault, formal),
        "four_create_records": creates,
        "special_record_validation": special,
        "asset_boundary": asset_boundary,
        "candidate_validation": candidate,
        "offline_search": search,
        "audit_only_write_occurrences": audit_occurrences,
        "input_checksums": input_checksums,
        "reproduced_plan_id": reproduced_plan_id,
        "reproduced_manifest_hash": reproduced_manifest_hash,
        "plan_identity_valid": plan_identity_valid,
        "source_branch": plan["source_branch"],
        "source_commit": plan["source_commit"],
        "target_paths": plan["target_paths"],
        "validation_errors": errors,
        "validation_warnings": warnings,
        "formal_systems_unchanged": formal_unchanged,
        "generator_module_imported": False,
        "plan_manifest": plan,
        "managed_projection": managed_projection,
        "formal_projection": formal_projection,
    }
    result["independent_validation_hash"] = _hash_json(_public_validation(result))
    return result


def confirm_store_data_sync_plan_v2(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    reviewer: str,
    confirmed_at: Optional[str] = None,
    confirmation_path: Path = DEFAULT_CONFIRMATION_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    require_git_ignored: bool = True,
    **validation_kwargs,
) -> dict:
    if reviewer != "Admin":
        raise StoreDataSyncPlanV2ConfirmationError("reviewer must equal Admin")
    confirmed = confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    _timestamp(confirmed)
    root = Path(repo_root).resolve()
    confirmation = _resolve(root, confirmation_path)
    reports = _resolve(root, report_dir)
    validation_kwargs = dict(validation_kwargs)
    validation_kwargs.pop("now", None)
    validation = validate_store_data_sync_plan_v2(
        repo_root=root, plan_id=plan_id, manifest_hash=manifest_hash,
        now=confirmed, **validation_kwargs,
    )
    if validation["validation_errors"] or validation["validation_warnings"]:
        raise StoreDataSyncPlanV2ConfirmationError("confirmation requires zero errors and zero warnings")

    if confirmation.exists():
        existing = validate_store_data_sync_plan_v2_confirmation(confirmation)
        expected = (plan_id, manifest_hash, reviewer, confirmed)
        observed = (
            existing["plan_id"], existing["plan_manifest_hash"],
            existing["reviewer"], existing["confirmed_at"],
        )
        if observed != expected:
            raise StoreDataSyncPlanV2ConfirmationError("existing Confirmation Bundle conflicts with requested confirmation")
        summary = _confirmation_summary(existing, validation, confirmation, reports, False, True)
        _write_reports(reports, summary, validation, existing)
        return summary

    protected = _protected_paths(root, validation_kwargs)
    before = {str(path): _hash_path(path) for path in protected}
    confirmation.parent.mkdir(parents=True, exist_ok=True)
    if require_git_ignored and not _git_ignored(root, confirmation):
        raise StoreDataSyncPlanV2ConfirmationError("Confirmation path must be Git ignored")
    staging = Path(tempfile.mkdtemp(prefix=f".{confirmation.name}.staging-", dir=str(confirmation.parent)))
    renamed = False
    try:
        payload = _confirmation_payload(validation, reviewer, confirmed)
        public = _public_validation(validation)
        public["independent_validation_hash"] = validation["independent_validation_hash"]
        plan_source = _resolve(root, validation_kwargs.get("plan_manifest_path", DEFAULT_PLAN_MANIFEST))
        execution_source = _resolve(root, validation_kwargs.get("execution_bundle_path", DEFAULT_EXECUTION_BUNDLE)) / "execution_manifest.json"
        _write_json(staging / "confirmation.json", payload)
        _write_json(staging / "independent_validation.json", public)
        shutil.copyfile(plan_source, staging / "referenced_plan_manifest.json")
        shutil.copyfile(execution_source, staging / "referenced_decision_store_execution.json")
        _write_json(staging / "field_materialization_contract.json", {
            "contract_hash": validation["field_materialization_contract_hash"],
            "fields": validation["materialization_contract"],
        })
        _write_json(staging / "managed_vault_delta_manifest.json", _managed_manifest(validation))
        _write_json(staging / "formal_sqlite_delta_manifest.json", _formal_manifest(validation))
        _write_json(staging / "target_projection_summary.json", _target_summary(validation))
        manifest = _confirmation_manifest(staging, validation, payload)
        _write_json(staging / "confirmation_manifest.json", manifest)
        staged = validate_store_data_sync_plan_v2_confirmation(staging)
        if confirmation.exists():
            raise StoreDataSyncPlanV2ConfirmationError("Confirmation target appeared before atomic rename")
        os.replace(staging, confirmation)
        renamed = True
        final = validate_store_data_sync_plan_v2_confirmation(confirmation)
        if final["root_confirmation_hash"] != staged["root_confirmation_hash"]:
            raise StoreDataSyncPlanV2ConfirmationError("Confirmation root hash changed after atomic rename")
        _make_read_only(confirmation)
    except Exception:
        if renamed and confirmation.exists():
            quarantine = confirmation.with_name(f"{confirmation.name}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            if not quarantine.exists():
                os.replace(confirmation, quarantine)
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    after = {str(path): _hash_path(path) for path in protected}
    if before != after:
        raise StoreDataSyncPlanV2ConfirmationError("protected formal inputs changed while confirming")
    summary = _confirmation_summary(final, validation, confirmation, reports, True, False)
    _write_reports(reports, summary, validation, final)
    return summary


def validate_store_data_sync_plan_v2_confirmation(path: Path) -> dict:
    root = Path(path)
    manifest_path = root / "confirmation_manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise StoreDataSyncPlanV2ConfirmationError("Confirmation Bundle is missing")
    manifest = _read_json(manifest_path)
    stored = manifest.get("root_confirmation_hash", "")
    expected = _hash_json({key: value for key, value in manifest.items() if key != "root_confirmation_hash"})
    if not stored or stored != expected:
        raise StoreDataSyncPlanV2ConfirmationError("Confirmation root hash mismatch")
    required = {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "field_materialization_contract_hash": EXPECTED_CONTRACT_HASH,
        "managed_vault_delta_hash": EXPECTED_MANAGED_DELTA_HASH,
        "formal_sqlite_delta_hash": EXPECTED_SQLITE_DELTA_HASH,
        "plan_expires_at": PLAN_EXPIRES_AT,
        "reviewer": "Admin",
    }
    for key, value in required.items():
        if manifest.get(key) != value:
            raise StoreDataSyncPlanV2ConfirmationError(f"Confirmation {key} mismatch")
    listed = set()
    for entry in manifest.get("files", []):
        name = _safe_name(entry.get("filename", ""))
        if name in listed:
            raise StoreDataSyncPlanV2ConfirmationError("duplicate Confirmation filename")
        listed.add(name)
        candidate = root / name
        if not candidate.is_file() or _sha256(candidate) != entry.get("sha256"):
            raise StoreDataSyncPlanV2ConfirmationError(f"Confirmation checksum mismatch: {name}")
        if candidate.stat().st_size != entry.get("byte_size"):
            raise StoreDataSyncPlanV2ConfirmationError(f"Confirmation byte size mismatch: {name}")
    physical = {
        item.name for item in root.iterdir()
        if item.is_file() and item.name != "confirmation_manifest.json" and not item.name.startswith("._")
    }
    if physical != listed or len(listed) != 8:
        raise StoreDataSyncPlanV2ConfirmationError("Confirmation contains unlisted or missing files")
    confirmation = _read_json(root / "confirmation.json")
    for key in (
        "confirmation_id", "plan_id", "plan_manifest_hash", "reviewer",
        "confirmed_at", "plan_expires_at",
    ):
        if confirmation.get(key) != manifest.get(key):
            raise StoreDataSyncPlanV2ConfirmationError(f"Confirmation payload {key} mismatch")
    plan = _read_json(root / "referenced_plan_manifest.json")
    if plan.get("manifest_hash") != EXPECTED_MANIFEST_HASH:
        raise StoreDataSyncPlanV2ConfirmationError("referenced Plan mismatch")
    execution = _read_json(root / "referenced_decision_store_execution.json")
    if execution.get("root_execution_hash") != EXPECTED_EXECUTION_ROOT_HASH:
        raise StoreDataSyncPlanV2ConfirmationError("referenced Execution mismatch")
    contract = _read_json(root / "field_materialization_contract.json")
    if contract.get("contract_hash") != EXPECTED_CONTRACT_HASH or _hash_json(contract.get("fields")) != EXPECTED_CONTRACT_HASH:
        raise StoreDataSyncPlanV2ConfirmationError("materialization contract mismatch")
    managed = _read_json(root / "managed_vault_delta_manifest.json")
    formal = _read_json(root / "formal_sqlite_delta_manifest.json")
    if managed.get("delta_hash") != EXPECTED_MANAGED_DELTA_HASH or _hash_json(managed.get("records")) != EXPECTED_MANAGED_DELTA_HASH:
        raise StoreDataSyncPlanV2ConfirmationError("Managed Vault delta mismatch")
    if formal.get("delta_hash") != EXPECTED_SQLITE_DELTA_HASH or _hash_json(formal.get("records")) != EXPECTED_SQLITE_DELTA_HASH:
        raise StoreDataSyncPlanV2ConfirmationError("Formal SQLite delta mismatch")
    independent = _read_json(root / "independent_validation.json")
    independent_hash = independent.pop("independent_validation_hash", "")
    if independent_hash != _hash_json(independent) or independent_hash != manifest.get("independent_validation_hash"):
        raise StoreDataSyncPlanV2ConfirmationError("independent validation hash mismatch")
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


def _load_parent_source(path):
    values = _read_json(path)
    records = {}
    for value in values:
        record_id = _record_id(value.get("source_sheet"), value.get("source_row"))
        if record_id in records:
            raise StoreDataSyncPlanV2ConfirmationError(f"duplicate source Parent: {record_id}")
        records[record_id] = value
    if len(records) != 120:
        raise StoreDataSyncPlanV2ConfirmationError("Parent source must contain 120 records")
    return records


def _load_store(path):
    with _readonly(path) as connection:
        parents = [dict(row) for row in connection.execute("SELECT * FROM current_parent_decisions ORDER BY event_sequence")]
        aliases = [dict(row) for row in connection.execute("SELECT * FROM current_search_aliases ORDER BY event_sequence")]
        entities = [dict(row) for row in connection.execute("SELECT * FROM current_entity_metadata ORDER BY event_sequence")]
        assets = [dict(row) for row in connection.execute("SELECT * FROM current_asset_eligibility ORDER BY event_sequence")]
    if len(parents) != 120 or len({row["record_id"] for row in parents}) != 120:
        raise StoreDataSyncPlanV2ConfirmationError("Decision Store Parent state is not 120 unique records")
    return {"parents": parents, "aliases": aliases, "entities": entities, "assets": assets}


def _build_desired_state(source, store):
    aliases = defaultdict(list)
    alias_audit = {}
    for event in store["aliases"]:
        value = json.loads(event["new_value_json"])
        aliases[event["record_id"]].append(value["alias"])
        alias_audit[event["record_id"]] = {
            "reviewer": event["reviewer"], "reviewed_at": event["reviewed_at"],
            "provenance": event["provenance"],
        }
    entities = {
        event["record_id"]: json.loads(event["new_value_json"])
        for event in store["entities"]
    }
    result = []
    for event in store["parents"]:
        record_id = event["record_id"]
        metadata = source.get(record_id)
        if metadata is None:
            raise StoreDataSyncPlanV2ConfirmationError(f"orphan Parent decision: {record_id}")
        value = json.loads(event["new_value_json"])
        decision = value["review_decision"]
        can_vault, can_index, can_external = _decision_effects(decision, value, metadata)
        entity = entities.get(record_id, {})
        entity_type = entity.get("entity_type") or ("partner" if metadata.get("merchant_status") == "合作夥伴" else "merchant")
        requirement = entity.get("merchant_handle_requirement") or ("not_required" if entity_type == "partner" else "required_by_existing_rules")
        audit = alias_audit.get(record_id, {})
        flags = sorted({
            str(item) for key in ("governance_issue_types", "governance_risk_reasons", "governance_risk_fields")
            for item in (metadata.get(key) or []) if str(item).strip()
        })
        result.append({
            "record_id": record_id,
            "brand_name": _text(metadata.get("brand_name")),
            "merchant_handle": _text(metadata.get("merchant_handle")),
            "merchant_status": _text(metadata.get("merchant_status")),
            "normalized_entity_type": entity_type,
            "merchant_handle_requirement": requirement,
            "current_review_decision": decision,
            "can_enter_vault": can_vault,
            "can_enter_content_index": can_index,
            "can_external_reference": can_external,
            "parent_index_eligibility": "included" if can_index else "excluded",
            "parent_search_eligibility": "not_searchable" if not can_index else "searchable" if can_external else "searchable_internal",
            "search_aliases": aliases.get(record_id, []),
            "search_alias_reviewed_by": audit.get("reviewer", ""),
            "search_alias_reviewed_at": audit.get("reviewed_at", ""),
            "search_alias_provenance": audit.get("provenance", ""),
            "content_tags": metadata.get("content_tags") or [],
            "classification": metadata.get("data_classification") or "",
            "governance_flags": flags,
            "source_sheet": metadata.get("source_sheet"),
            "source_row": int(metadata.get("source_row")),
            "decision_event_id": event["event_id"],
            "decision_event_hash": event["event_hash"],
            "decision_reviewer": event["reviewer"],
            "decision_reviewed_at": event["reviewed_at"],
            "decision_provenance": event["provenance"],
            "desired_projection_status": "content" if can_index else "governance_only" if can_vault else "excluded",
        })
    result.sort(key=lambda row: row["source_row"])
    return result


def _decision_effects(decision, value, metadata):
    if decision == "exclude":
        return False, False, False
    if decision == "exclude_from_content_index":
        return True, False, False
    if decision == "approve_internal_only":
        return True, True, False
    if decision not in {"approve", "keep_all_records"}:
        raise StoreDataSyncPlanV2ConfirmationError(f"unsupported Parent decision: {decision}")
    external = value.get("can_quote_externally")
    if external is None:
        external = value.get("can_external_reference")
    if external is None:
        external = metadata.get("can_quote_externally")
    return True, True, _boolean(external)


def _load_vault(root):
    values = defaultdict(list)
    for path in sorted(Path(root).rglob("*.md")):
        if path.name.startswith("._") or "_archived" in path.parts:
            continue
        try:
            metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, FrontmatterError) as exc:
            raise StoreDataSyncPlanV2ConfirmationError(f"cannot parse Parent file {path}: {exc}") from exc
        if metadata.get("record_type") != "merchant_case":
            continue
        record_id = _record_id(metadata.get("source_sheet"), metadata.get("source_row"))
        values[record_id].append({"path": path.relative_to(root).as_posix(), "metadata": metadata})
    if any(len(rows) != 1 for rows in values.values()):
        raise StoreDataSyncPlanV2ConfirmationError("duplicate Managed Vault Parent")
    return {record_id: rows[0] for record_id, rows in values.items()}


def _load_formal_sqlite(path):
    values = defaultdict(list)
    with _readonly(path) as connection:
        for row in connection.execute("SELECT id,source_path,metadata_json FROM documents"):
            metadata = json.loads(row["metadata_json"])
            if metadata.get("record_type") != "merchant_case":
                continue
            record_id = _record_id(metadata.get("source_sheet"), metadata.get("source_row"))
            values[record_id].append({"document_id": row["id"], "source_path": row["source_path"], "metadata": metadata})
    if any(len(rows) != 1 for rows in values.values()):
        raise StoreDataSyncPlanV2ConfirmationError("duplicate Formal SQLite Parent")
    return {record_id: rows[0] for record_id, rows in values.items()}


def _managed_fields():
    return {
        "record_id": "record_id", "brand_name": "brand_name", "merchant_handle": "merchant_handle",
        "merchant_status": "merchant_status", "normalized_entity_type": "normalized_entity_type",
        "merchant_handle_requirement": "merchant_handle_requirement", "current_review_decision": "review_decision",
        "can_enter_vault": "can_enter_vault", "can_enter_content_index": "can_enter_content_index",
        "can_external_reference": "can_external_reference", "parent_index_eligibility": "parent_index_eligibility",
        "parent_search_eligibility": "parent_search_eligibility", "search_aliases": "search_aliases",
        "search_alias_reviewed_by": "search_alias_reviewed_by", "search_alias_reviewed_at": "search_alias_reviewed_at",
        "search_alias_provenance": "search_alias_provenance", "content_tags": "content_tags",
        "classification": "data_classification", "governance_flags": "governance_flags",
        "source_sheet": "source_sheet", "source_row": "source_row",
    }


def _formal_fields():
    return {
        "brand_name": "brand_name", "merchant_handle": "merchant_handle", "merchant_status": "merchant_status",
        "can_enter_content_index": "can_enter_content_index", "can_external_reference": "can_quote_externally",
        "content_tags": "content_tags", "classification": "data_classification",
        "source_sheet": "source_sheet", "source_row": "source_row",
    }


def _effective(row, field):
    if field == "classification" and row["current_review_decision"] == "approve_internal_only":
        return "internal"
    return row[field]


def _payload(row, fields):
    result = {}
    for source, target in fields.items():
        value = _effective(row, source)
        if source in {"merchant_handle", "search_alias_reviewed_by", "search_alias_reviewed_at", "search_alias_provenance"}:
            value = value or None
        result[target] = value
    return result


def _managed_payload(row):
    return _payload(row, _managed_fields())


def _formal_payload(row):
    return {"record_id": row["record_id"], **_payload(row, _formal_fields())}


def _managed_reconciliation(desired, vault, formal):
    rows, delta = [], []
    for item in desired:
        record_id = item["record_id"]
        current = vault.get(record_id)
        indexed = formal.get(record_id)
        expected = _managed_payload(item)
        metadata = current["metadata"] if current else {}
        changed, old_values, new_values = [], {}, {}
        if item["can_enter_vault"]:
            for field, value in expected.items():
                old = _current_value(metadata, field)
                if _normalized(old) == _normalized(value):
                    continue
                changed.append(field)
                old_values[field], new_values[field] = old, value
        if not item["can_enter_vault"]:
            action = "remove_from_content_projection" if current or indexed else "retain_governance_only"
        elif current is None:
            action = "create"
        elif changed:
            action = "update"
        else:
            action = "no_change"
        proposed = _proposed_path(item, current) if item["can_enter_vault"] else ""
        row = {
            "record_id": record_id, "brand_name": item["brand_name"],
            "authoritative_decision": item["current_review_decision"],
            "current_managed_path": current["path"] if current else "",
            "proposed_managed_path": proposed,
            "current_formal_sqlite_presence": bool(indexed),
            "proposed_action": action, "changed_fields": changed,
            "necessary_materialized_diff_count": len(changed), "blocked_reason": "",
        }
        rows.append(row)
        delta.append({
            "record_id": record_id, "brand_name": item["brand_name"], "action": action,
            "target_path": proposed, "rollback_path": current["path"] if current else "",
            "changed_fields": changed, "before_values": old_values, "after_values": new_values,
        })
    return {"rows": rows, "delta": delta}


def _formal_reconciliation(desired, formal):
    rows = []
    for item in desired:
        record_id = item["record_id"]
        current = formal.get(record_id)
        expected = _formal_payload(item)
        changed, before, after = [], {}, {}
        if item["can_enter_content_index"]:
            metadata = current["metadata"] if current else {}
            for field, value in expected.items():
                if field == "record_id":
                    continue
                old = metadata.get(field)
                if _normalized(old) == _normalized(value):
                    continue
                changed.append(field)
                before[field], after[field] = old, value
        if not item["can_enter_content_index"]:
            action = "remove" if current else "not_projected"
        elif current is None:
            action = "create"
        elif changed:
            action = "update"
        else:
            action = "no_change"
        rows.append({
            "record_id": record_id, "brand_name": item["brand_name"], "action": action,
            "current_presence": bool(current), "desired_presence": item["can_enter_content_index"],
            "changed_fields": changed, "before_values": before, "after_values": after,
            "source_path": current["source_path"] if current else "",
        })
    return {"rows": rows}


def _materialization_contract():
    managed, formal = _managed_fields(), _formal_fields()
    sources = {
        "record_id": "source_sheet_and_source_row", "brand_name": "parent_source_metadata",
        "merchant_handle": "parent_source_metadata", "merchant_status": "parent_source_metadata",
        "normalized_entity_type": "decision_store_current_entity_metadata",
        "merchant_handle_requirement": "decision_store_current_entity_metadata",
        "current_review_decision": "decision_store_current_parent_decisions",
        "can_enter_vault": "decision_store_decision_effect", "can_enter_content_index": "decision_store_decision_effect",
        "can_external_reference": "decision_store_decision_effect", "parent_index_eligibility": "derived_from_parent_decision",
        "parent_search_eligibility": "derived_from_parent_decision", "search_aliases": "decision_store_current_search_aliases",
        "search_alias_reviewed_by": "decision_store_search_alias_event",
        "search_alias_reviewed_at": "decision_store_search_alias_event",
        "search_alias_provenance": "decision_store_search_alias_event",
        "content_tags": "parent_source_metadata", "classification": "parent_source_metadata",
        "governance_flags": "parent_source_metadata", "source_sheet": "parent_source_metadata",
        "source_row": "parent_source_metadata", "desired_projection_status": "derived_from_parent_decision",
        **{field: "decision_store_event_audit" for field in AUDIT_ONLY_FIELDS},
        "event_hash_chain": "decision_store_event_audit", "full_input_checksums": "execution_bundle",
        "execution_authority_references": "execution_bundle", "complete_historical_decision_payload": "decision_store_event_audit",
        "supersede_revoke_audit_evidence": "decision_store_event_audit",
    }
    rows = []
    for field, source in sources.items():
        in_managed, in_formal = field in managed, field in formal
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
            "content_tags": "retrieval filters", "classification": "query gating",
        }.get(field, "Managed ingestion or current projection reconciliation" if in_managed else "audit verification")
        rows.append({
            "field_name": field, "authoritative_source": source,
            "decision_store": source.startswith("decision_store"), "audit_bundle": audit,
            "managed_vault": in_managed, "formal_sqlite": in_formal,
            "production_search": field in {"brand_name", "merchant_handle", "merchant_status", "content_tags", "classification"},
            "required_consumer": consumer, "safe_to_materialize": in_managed or in_formal,
            "triggers_write": in_managed or in_formal, "materialization_status": status,
            "formal_storage_key": formal.get(field, ""),
            "reason": "complete decision audit remains in the append-only authority" if audit else consumer,
        })
    return rows


def _build_assets(desired, decision_assets, paths):
    parents = {row["record_id"]: row for row in desired}
    inventory = {row["asset_id"]: row for row in _read_csv(paths["asset_inventory"])}
    eligible = defaultdict(dict)
    for row in _read_csv(paths["asset_eligible"]):
        eligible[row["asset_id"]][row["field"]] = row["proposed_value"]
    blocked = {row["asset_id"] for row in _read_csv(paths["asset_blocked"])}
    explicit = {row["asset_id"]: json.loads(row["new_value_json"]) for row in decision_assets}
    if len(inventory) != 222 or len(eligible) != 206 or len(blocked) != 16:
        raise StoreDataSyncPlanV2ConfirmationError("asset baseline mismatch")
    assets = []
    for asset_id, row in sorted(inventory.items()):
        record_id = row["record_id"]
        if record_id not in parents:
            raise StoreDataSyncPlanV2ConfirmationError(f"asset orphan Parent: {asset_id}")
        if asset_id in eligible:
            index, search = "include", "searchable"
        elif asset_id in blocked:
            index, search = "exclude", "excluded"
        else:
            raise StoreDataSyncPlanV2ConfirmationError(f"asset lacks eligibility: {asset_id}")
        if asset_id in explicit:
            index = explicit[asset_id]["asset_index_eligibility"]
            search = explicit[asset_id]["asset_search_eligibility"]
        parent = parents[record_id]
        if parent["current_review_decision"] == "exclude":
            index, search = "exclude", "excluded"
        if index == "include" and not parent["can_external_reference"]:
            search = "searchable_internal"
        assets.append({
            "asset_id": asset_id, "record_id": record_id, "asset_type": row["asset_type"],
            "asset_title": row["asset_title"], "index_eligibility": index,
            "search_eligibility": search, "can_external_reference": parent["can_external_reference"],
        })
    return assets


def _asset_boundary(assets, url_path):
    counts = Counter(row["index_eligibility"] for row in assets)
    included = {row["asset_id"] for row in assets if row["index_eligibility"] == "include"}
    decisions = _read_csv(url_path)
    approved = sum(row["review_decision"] == "approve" and row["asset_id"] in included for row in decisions)
    r30_excluded = sum(row["review_decision"] == "approve" and row["asset_id"].startswith("商家夥伴案例資料庫:r30:") for row in decisions)
    if r30_excluded != 2:
        raise StoreDataSyncPlanV2ConfirmationError("莉朵花藝 excluded URL field count mismatch")
    return {
        "eligible_assets": counts["include"], "hold_assets": counts["hold"],
        "excluded_or_blocked_assets": counts["exclude"], "approved_url_fields": approved,
        "asset_identity_creates": 0, "asset_identity_deletes": 0, "url_values_copied": 0,
        "parent_tags_copied_to_assets": 0, "aliases_copied_to_assets": 0,
    }


def _build_candidate(path, desired, assets, alias_events):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE parents(record_id TEXT PRIMARY KEY,brand_name TEXT NOT NULL,merchant_handle TEXT NOT NULL,decision TEXT NOT NULL,entity_type TEXT NOT NULL,can_vault INTEGER NOT NULL,can_index INTEGER NOT NULL,can_external INTEGER NOT NULL,tags_json TEXT NOT NULL,classification TEXT NOT NULL);
        CREATE TABLE aliases(record_id TEXT NOT NULL,alias TEXT NOT NULL,normalized_alias TEXT NOT NULL,match_type TEXT NOT NULL CHECK(match_type='case_insensitive_exact'),PRIMARY KEY(record_id,normalized_alias),FOREIGN KEY(record_id) REFERENCES parents(record_id));
        CREATE TABLE assets(asset_id TEXT PRIMARY KEY,record_id TEXT NOT NULL,asset_type TEXT NOT NULL,asset_title TEXT NOT NULL,index_eligibility TEXT NOT NULL,search_eligibility TEXT NOT NULL,can_external INTEGER NOT NULL,FOREIGN KEY(record_id) REFERENCES parents(record_id));
    """)
    for row in desired:
        connection.execute("INSERT INTO parents VALUES(?,?,?,?,?,?,?,?,?,?)", (
            row["record_id"], row["brand_name"], row["merchant_handle"], row["current_review_decision"],
            row["normalized_entity_type"], int(row["can_enter_vault"]), int(row["can_enter_content_index"]),
            int(row["can_external_reference"]), _json(row["content_tags"]), _effective(row, "classification"),
        ))
    for event in alias_events:
        value = json.loads(event["new_value_json"])
        connection.execute("INSERT INTO aliases VALUES(?,?,?,?)", (
            event["record_id"], value["alias"], value["normalized_alias"], value["match_type"],
        ))
    for row in assets:
        connection.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?)", (
            row["asset_id"], row["record_id"], row["asset_type"], row["asset_title"],
            row["index_eligibility"], row["search_eligibility"], int(row["can_external_reference"]),
        ))
    connection.commit()
    result = {
        "managed_vault_parents": connection.execute("SELECT COUNT(*) FROM parents WHERE can_vault=1").fetchone()[0],
        "content_parents": connection.execute("SELECT COUNT(*) FROM parents WHERE can_index=1").fetchone()[0],
        "candidate_assets": connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
        "searchable_assets": connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='include'").fetchone()[0],
        "hold_assets": connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='hold'").fetchone()[0],
        "excluded_or_blocked_assets": connection.execute("SELECT COUNT(*) FROM assets WHERE index_eligibility='exclude'").fetchone()[0],
        "orphan_parents": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        "duplicate_parents": 0,
        "restricted_leakage": connection.execute("SELECT COUNT(*) FROM parents WHERE can_index=1 AND classification='restricted'").fetchone()[0],
        "pending_leakage": connection.execute("SELECT COUNT(*) FROM parents WHERE can_index=1 AND classification='pending'").fetchone()[0],
        "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
    }
    connection.close()
    with _readonly(path) as reopened:
        result["read_only_reopen"] = reopened.execute("SELECT COUNT(*) FROM parents").fetchone()[0] == 120
    return result


def _offline_search(path):
    queries = ("莉朵花藝", "littlegirl", "廣生堂", "111gsttest", "Package+", "SLP", "SHOPLINE Payments", "聊心茶室", "關貿網路")
    result = {}
    with _readonly(path) as connection:
        parents = [dict(row) for row in connection.execute("SELECT * FROM parents")]
        aliases = defaultdict(set)
        for row in connection.execute("SELECT record_id,normalized_alias FROM aliases"):
            aliases[row["normalized_alias"]].add(row["record_id"])
        assets = defaultdict(list)
        for row in connection.execute("SELECT * FROM assets WHERE index_eligibility='include'"):
            assets[row["record_id"]].append(dict(row))
        for query in queries:
            normalized = _normalize_query(query)
            matched = set(aliases.get(normalized, set()))
            for parent in parents:
                tags = json.loads(parent["tags_json"])
                if normalized == _normalize_query(parent["merchant_handle"]) or normalized in _normalize_query(parent["brand_name"]) or normalized in {_normalize_query(tag) for tag in tags}:
                    matched.add(parent["record_id"])
            matched = {row["record_id"] for row in parents if row["record_id"] in matched and row["can_index"] == 1}
            found = [asset for record_id in matched for asset in assets.get(record_id, [])]
            result[query] = {
                "record_ids": sorted(matched), "record_count": len(matched), "asset_count": len(found),
                "asset_types": sorted({asset["asset_type"] for asset in found}),
                "citation_count": sum(asset["can_external"] == 1 for asset in found),
            }
    return result


def _governance_rows(desired, reconciliation, vault, formal):
    actions = {row["record_id"]: row for row in reconciliation}
    return [{
        "record_id": row["record_id"], "brand_name": row["brand_name"],
        "decision": row["current_review_decision"], "decision_store_only": True,
        "managed_presence": row["record_id"] in vault, "formal_presence": row["record_id"] in formal,
        "planned_action": actions[row["record_id"]]["proposed_action"],
        "content_file": False, "content_index": False, "search": False, "citation": False,
    } for row in desired if not row["can_enter_vault"]]


def _four_creates(desired, reconciliation, projection, vault, formal):
    desired_map = {row["record_id"]: row for row in desired}
    projected = {row["record_id"]: row for row in projection}
    paths = Counter(row["proposed_managed_path"] for row in reconciliation if row["proposed_managed_path"])
    result = []
    for row in reconciliation:
        if row["proposed_action"] != "create":
            continue
        item = desired_map[row["record_id"]]
        valid = all((
            row["record_id"] not in vault, row["record_id"] not in formal,
            paths[row["proposed_managed_path"]] == 1,
            not any(field in projected[row["record_id"]] for field in AUDIT_ONLY_FIELDS),
            item["merchant_handle_requirement"] == "not_required" if item["normalized_entity_type"] == "partner" else True,
        ))
        result.append({
            "record_id": row["record_id"], "brand_name": row["brand_name"],
            "managed_absent": row["record_id"] not in vault, "formal_absent": row["record_id"] not in formal,
            "proposed_path": row["proposed_managed_path"], "path_collision": paths[row["proposed_managed_path"]] > 1,
            "audit_only_absent": not any(field in projected[row["record_id"]] for field in AUDIT_ONLY_FIELDS),
            "parent_aliases": item["search_aliases"], "handle_requirement": item["merchant_handle_requirement"],
            "valid": valid,
        })
    return result


def _special_validation(desired, reconciliation, assets, search):
    parents = {row["record_id"]: row for row in desired}
    actions = {row["record_id"]: row for row in reconciliation}
    asset_map = {row["asset_id"]: row for row in assets}
    checks = {
        "r30_governance_only": actions["商家夥伴案例資料庫:r30"]["proposed_action"] == "retain_governance_only",
        "r30_zero_asset_and_citation": search["莉朵花藝"]["asset_count"] == search["littlegirl"]["citation_count"] == 0,
        "r12_internal_only": parents["商家夥伴案例資料庫:r12"]["current_review_decision"] == "approve_internal_only" and not parents["商家夥伴案例資料庫:r12"]["can_external_reference"],
        "r12_video_hold": asset_map["商家夥伴案例資料庫:r12:video"]["index_eligibility"] == "hold" and asset_map["商家夥伴案例資料庫:r12:video"]["search_eligibility"] == "not_searchable",
        "r122_partner_no_handle": parents["商家夥伴案例資料庫:r122"]["merchant_handle_requirement"] == "not_required",
        "r32_parent_aliases": parents["商家夥伴案例資料庫:r32"]["search_aliases"] == ["SLP", "SHOPLINE Payments"],
        "r32_exact_alias": search["SLP"]["record_ids"] == ["商家夥伴案例資料庫:r32"] and search["SHOPLINE Payments"]["record_count"] == 16,
        "r7_partner_no_handle": parents["商家夥伴案例資料庫:r7"]["merchant_handle_requirement"] == "not_required",
    }
    return [{"check": key, "observed": value, "status": "pass" if value else "fail"} for key, value in checks.items()]


def _validate_consumers(root, sqlite_path):
    files = {
        "models": root / "src/marketing_knowledge_agent/models.py",
        "retrieval": root / "src/marketing_knowledge_agent/retrieval.py",
        "query_gating": root / "src/marketing_knowledge_agent/query_gating.py",
        "content_index": root / "src/marketing_knowledge_agent/content_index.py",
    }
    text = {key: path.read_text(encoding="utf-8") for key, path in files.items()}
    checks = {
        "document_metadata_supports_formal_allowlist": all(token in text["models"] for token in (
            "brand_name:", "merchant_handle:", "merchant_status:", "content_tags:",
            "data_classification:", "can_quote_externally:", "can_enter_content_index:",
            "source_sheet:", "source_row:",
        )),
        "retrieval_consumes_allowlist": all(token in text["retrieval"] for token in (
            "metadata.merchant_handle", "metadata.merchant_status", "metadata.can_quote_externally",
            "metadata.content_tags", "metadata.data_classification",
        )),
        "citation_gate_consumes_external_flag": "citation.can_quote_externally" in text["query_gating"],
        "content_index_uses_source_identity": "top_metadata.source_sheet" in text["content_index"] and "top_metadata.source_row" in text["content_index"],
    }
    with _readonly(sqlite_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    checks["formal_schema_has_metadata_json"] = "metadata_json" in columns
    checks["formal_schema_not_extended_for_audit"] = not any(field in columns for field in AUDIT_ONLY_FIELDS)
    return {"valid": all(checks.values()), "checks": checks, "documents_columns": sorted(columns)}


def _validation_errors(desired, counts, managed_delta, formal_rows, governance, not_projected, creates, candidate, special, asset, audit_count, formal_diff_count, consumer, health):
    errors = []
    checks = {
        "authoritative_count": len(desired) == 120 and len({row["record_id"] for row in desired}) == 120,
        "actions": counts == {"create": 4, "update": 106, "no_change": 0, "remove_from_content_projection": 0, "retain_governance_only": 10, "blocked": 0, "manual_review": 0},
        "managed_writes": len(managed_delta) == 110 and all(row["changed_fields"] for row in managed_delta),
        "formal_counts": Counter(row["action"] for row in formal_rows) == Counter({"create": 4, "no_change": 105, "not_projected": 11}),
        "governance": {row["record_id"] for row in governance} == GOVERNANCE_ONLY_IDS,
        "not_projected": {row["record_id"] for row in not_projected} == NOT_PROJECTED_IDS,
        "creates": {row["record_id"] for row in creates} == CREATE_IDS and all(row["valid"] for row in creates),
        "candidate": all((candidate["managed_vault_parents"] == 110, candidate["content_parents"] == 109, candidate["candidate_assets"] == 222, candidate["searchable_assets"] == 205, candidate["orphan_parents"] == 0, candidate["duplicate_parents"] == 0, candidate["restricted_leakage"] == 0, candidate["pending_leakage"] == 0)),
        "special": all(row["status"] == "pass" for row in special),
        "asset": (asset["eligible_assets"], asset["hold_assets"], asset["excluded_or_blocked_assets"], asset["approved_url_fields"]) == (205, 1, 16, 410),
        "audit": audit_count == 0,
        "formal_existing_no_diff": formal_diff_count == 0,
        "consumers": consumer["valid"],
        "store": health["conclusion"].startswith("A.") and health["authority_gap"] == 0,
    }
    return [key for key, value in checks.items() if not value]


def _validate_plan_manifest_static(plan):
    required = {
        "plan_id": EXPECTED_PLAN_ID, "manifest_hash": EXPECTED_MANIFEST_HASH,
        "source_commit": PLAN_SOURCE_COMMIT, "expires_at": PLAN_EXPIRES_AT,
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "field_materialization_contract_hash": EXPECTED_CONTRACT_HASH,
        "managed_vault_delta_hash": EXPECTED_MANAGED_DELTA_HASH,
        "formal_sqlite_delta_hash": EXPECTED_SQLITE_DELTA_HASH,
        "execution_blocked": False, "blocker_reasons": [],
    }
    for key, value in required.items():
        if plan.get(key) != value:
            raise StoreDataSyncPlanV2ConfirmationError(f"Plan manifest {key} mismatch")


def _input_checksums(paths):
    return {
        "decision_store": _sha256(paths["decision_store"]),
        "execution_bundle": _hash_path(paths["execution_bundle"]),
        "parent_source_metadata": _sha256(paths["parent_source"]),
        "asset_inventory": _sha256(paths["asset_inventory"]),
        "asset_eligible_preview": _sha256(paths["asset_eligible"]),
        "asset_blocked_preview": _sha256(paths["asset_blocked"]),
        "asset_url_decisions": _sha256(paths["asset_url_decisions"]),
        "managed_vault": _hash_path(paths["managed_vault"]),
        "formal_sqlite": _sha256(paths["formal_sqlite"]),
        "obsolete_plan": _sha256(_resolve(paths["plan"].parents[2], "reports/parent_sync_plan/parent_sync_plan_manifest.json")),
    }


def _formal_counts(formal, rows, projection):
    counts = Counter(row["action"] for row in rows)
    return {"current": len(formal), "create": counts["create"], "update": counts["update"], "no_change": counts["no_change"], "target": len(projection), "not_projected": counts["not_projected"]}


def _action_counts(rows):
    counts = {name: 0 for name in ACTION_NAMES}
    for row in rows:
        counts[row["proposed_action"]] += 1
    return counts


def _r20_valid(desired, vault, formal):
    row = next(item for item in desired if item["record_id"] == "商家夥伴案例資料庫:r20")
    return row["can_enter_vault"] and not row["can_enter_content_index"] and row["record_id"] in vault and row["record_id"] not in formal


def _path_collision_count(rows):
    counts = Counter(row["proposed_managed_path"] for row in rows if row["proposed_managed_path"])
    return sum(value > 1 for value in counts.values())


def _confirmation_payload(validation, reviewer, confirmed):
    identity = {
        "plan_id": EXPECTED_PLAN_ID, "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "independent_validation_hash": validation["independent_validation_hash"],
        "reviewer": reviewer, "confirmed_at": confirmed,
    }
    confirmation_id = "store-data-sync-confirmation-" + _hash_json(identity)[:16]
    return {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "confirmation_id": confirmation_id, "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH,
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "field_materialization_contract_hash": EXPECTED_CONTRACT_HASH,
        "managed_vault_delta_hash": EXPECTED_MANAGED_DELTA_HASH,
        "formal_sqlite_delta_hash": EXPECTED_SQLITE_DELTA_HASH,
        "target_paths": validation["target_paths"], "counts": {
            **validation["action_counts"], "managed_vault_target": 110, "formal_sqlite_target": 109,
        },
        "input_checksums": validation["input_checksums"], "reviewer": reviewer,
        "confirmed_at": confirmed, "plan_expires_at": PLAN_EXPIRES_AT,
        "confirmation_statement": (
            f"Admin confirms the independently validated Store Data Sync Plan V2 identified by PLAN_ID {EXPECTED_PLAN_ID} "
            f"and Manifest Hash {EXPECTED_MANIFEST_HASH}. This authorizes only a later separate execute step using the exact "
            "Decision Store authority, materialization contract, target-specific delta manifests, paths, counts, and checksums. "
            "It does not execute or authorize any different plan, target, field mapping, count, or checksum."
        ),
    }


def _confirmation_manifest(staging, validation, payload):
    roles = {
        "confirmation.json": "admin_confirmation", "independent_validation.json": "independent_validation",
        "referenced_plan_manifest.json": "validated_plan", "referenced_decision_store_execution.json": "decision_store_execution",
        "field_materialization_contract.json": "materialization_contract", "managed_vault_delta_manifest.json": "managed_vault_delta",
        "formal_sqlite_delta_manifest.json": "formal_sqlite_delta", "target_projection_summary.json": "target_projection",
    }
    files = [{
        "filename": name, "logical_role": role, "sha256": _sha256(staging / name),
        "byte_size": (staging / name).stat().st_size, "required": True,
    } for name, role in roles.items()]
    manifest = {
        "confirmation_schema_version": CONFIRMATION_SCHEMA_VERSION,
        "confirmation_id": payload["confirmation_id"], "plan_id": EXPECTED_PLAN_ID,
        "plan_manifest_hash": EXPECTED_MANIFEST_HASH, "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "field_materialization_contract_hash": EXPECTED_CONTRACT_HASH,
        "managed_vault_delta_hash": EXPECTED_MANAGED_DELTA_HASH,
        "formal_sqlite_delta_hash": EXPECTED_SQLITE_DELTA_HASH,
        "target_paths": validation["target_paths"], "counts": payload["counts"],
        "input_checksums": validation["input_checksums"], "reviewer": payload["reviewer"],
        "confirmed_at": payload["confirmed_at"], "plan_expires_at": PLAN_EXPIRES_AT,
        "source_branch": validation["source_branch"], "source_commit": validation["source_commit"],
        "validator_code_version": VALIDATOR_CODE_VERSION,
        "independent_validation_hash": validation["independent_validation_hash"], "files": files,
    }
    manifest["root_confirmation_hash"] = _hash_json(manifest)
    return manifest


def _managed_manifest(validation):
    return {"schema_version": 2, "target": "obsidian_vault/MKA", "allowlist": sorted(_managed_fields().values()), "record_count": len(validation["managed_vault_delta_records"]), "delta_hash": validation["managed_vault_delta_hash"], "records": validation["managed_vault_delta_records"]}


def _formal_manifest(validation):
    return {"schema_version": 2, "target": ".mka/content_index.sqlite", "allowlist": sorted(set(_formal_fields().values())), "record_count": len(validation["formal_sqlite_delta"]), "delta_hash": validation["formal_sqlite_delta_hash"], "records": validation["formal_sqlite_delta"]}


def _target_summary(validation):
    return {"authoritative_records": 120, "managed_vault_current": 106, "managed_vault_target": 110, "formal_sqlite_current": 105, "formal_sqlite_target": 109, "managed_actions": validation["action_counts"], "formal_actions": validation["formal_sqlite_counts"], "governance_only": 10, "r20_vault_only": True, "asset_counts": {"eligible": 205, "hold": 1, "excluded": 16}, "approved_url_fields": 410}


def _public_validation(result):
    omitted = {"authoritative_records", "reconciliation", "managed_vault_delta", "managed_vault_delta_records", "formal_sqlite_delta", "not_projected", "governance_only_records", "four_create_records", "special_record_validation", "offline_search", "plan_manifest", "managed_projection", "formal_projection"}
    return {key: value for key, value in result.items() if key not in omitted and key != "independent_validation_hash"}


def _confirmation_summary(bundle, validation, confirmation, reports, created, noop):
    return {
        "conclusion": "A. Store Data Sync Plan V2 independently validated and confirmed",
        "plan_id": EXPECTED_PLAN_ID, "manifest_hash": EXPECTED_MANIFEST_HASH,
        "plan_not_expired": True, "confirmation_id": bundle["confirmation_id"],
        "root_confirmation_hash": bundle["root_confirmation_hash"],
        "confirmation_path": str(confirmation), "confirmation_created": created,
        "idempotent_noop": noop, "confirmed_at": bundle["confirmed_at"],
        "validation_errors": 0, "validation_warnings": 0,
        "formal_systems_unchanged": validation["formal_systems_unchanged"],
        "report_dir": str(reports),
    }


def _write_reports(output, summary, validation, bundle):
    output.mkdir(parents=True, exist_ok=True)
    for child in output.iterdir():
        if child.is_file() and not child.name.startswith("._"):
            child.unlink()
    _write_text(output / "confirmation_summary.md", "\n".join([
        "# Store Data Sync Plan V2 Confirmation", "",
        f"- Conclusion: **{summary['conclusion']}**", f"- PLAN_ID: `{EXPECTED_PLAN_ID}`",
        f"- Manifest Hash: `{EXPECTED_MANIFEST_HASH}`", f"- Confirmation ID: `{summary['confirmation_id']}`",
        f"- Root Confirmation Hash: `{summary['root_confirmation_hash']}`",
        f"- Idempotent no-op: `{str(summary['idempotent_noop']).lower()}`",
        "- Execute performed: `false`", "- Formal systems modified: `false`", "",
    ]))
    _write_csv(output / "decision_store_revalidation.csv", [validation["decision_store_validation"]])
    _write_csv(output / "materialization_contract_validation.csv", validation["materialization_contract"])
    matrix = {row["field_name"]: row for row in validation["materialization_contract"]}
    _write_csv(output / "audit_only_exclusion_validation.csv", [{"field_name": field, **matrix[field], "status": "pass"} for field in AUDIT_ONLY_FIELDS])
    _write_csv(output / "reconciliation_validation.csv", validation["reconciliation"])
    _write_csv(output / "managed_vault_delta_validation.csv", validation["managed_vault_delta"])
    _write_csv(output / "formal_sqlite_delta_validation.csv", validation["formal_sqlite_delta"])
    _write_csv(output / "governance_only_validation.csv", validation["governance_only_records"])
    _write_csv(output / "r20_validation.csv", [{"record_id": "商家夥伴案例資料庫:r20", "vault_only_valid": validation["r20_vault_only_valid"]}])
    _write_csv(output / "four_create_validation.csv", validation["four_create_records"])
    _write_csv(output / "special_record_validation.csv", validation["special_record_validation"])
    _write_csv(output / "asset_url_boundary_validation.csv", [validation["asset_boundary"]])
    _write_text(output / "candidate_validation.md", "# Temporary Candidate Validation\n\n" + "\n".join(f"- {key}: `{value}`" for key, value in sorted(validation["candidate_validation"].items())) + "\n")
    _write_text(output / "offline_search_validation.md", "# Offline Search Validation\n\n" + "\n".join(f"- `{query}`: parents={value['record_count']}, assets={value['asset_count']}, citations={value['citation_count']}" for query, value in validation["offline_search"].items()) + "\n")
    _write_csv(output / "plan_identity_validation.csv", [{"expected_plan_id": EXPECTED_PLAN_ID, "reproduced_plan_id": validation["reproduced_plan_id"], "expected_manifest_hash": EXPECTED_MANIFEST_HASH, "reproduced_manifest_hash": validation["reproduced_manifest_hash"], "valid": validation["plan_identity_valid"]}])
    _write_csv(output / "confirmation_bundle_validation.csv", [bundle])
    _write_csv(output / "formal_system_unchanged_validation.csv", [{"formal_systems_unchanged": validation["formal_systems_unchanged"], "decision_store_unchanged": validation["decision_store_validation"]["unchanged"], "execute_performed": False, "sync_performed": False, "slack_api_called": False}])
    _write_csv(output / "validation_errors.csv", [], ("error",))
    _write_csv(output / "validation_warnings.csv", [], ("warning",))


def _protected_paths(root, kwargs):
    return (
        _resolve(root, kwargs.get("decision_store_path", DEFAULT_DECISION_STORE)),
        _resolve(root, kwargs.get("execution_bundle_path", DEFAULT_EXECUTION_BUNDLE)),
        _resolve(root, kwargs.get("managed_vault_root", DEFAULT_MANAGED_VAULT)),
        _resolve(root, kwargs.get("formal_vault_root", DEFAULT_FORMAL_VAULT)),
        _resolve(root, kwargs.get("formal_sqlite_path", DEFAULT_FORMAL_SQLITE)),
        _resolve(root, kwargs.get("production_renderer_path", DEFAULT_RENDERER)),
        _resolve(root, kwargs.get("plan_manifest_path", DEFAULT_PLAN_MANIFEST)),
    )


def _make_read_only(root):
    for path in Path(root).iterdir():
        if path.is_file() and not path.name.startswith("._"):
            path.chmod(0o444)
    Path(root).chmod(0o555)


def _git_ignored(root, path):
    return subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=root, check=False).returncode == 0


def _require_exact_identity(plan_id, manifest_hash):
    if plan_id != EXPECTED_PLAN_ID:
        raise StoreDataSyncPlanV2ConfirmationError("exact PLAN_ID is required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise StoreDataSyncPlanV2ConfirmationError("exact Manifest Hash is required")


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise StoreDataSyncPlanV2ConfirmationError("timestamp must be ISO 8601") from exc
    if result.tzinfo is None:
        raise StoreDataSyncPlanV2ConfirmationError("timestamp must include timezone")
    return result


def _readonly(path):
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _record_id(sheet, row):
    if sheet is None or row is None:
        raise StoreDataSyncPlanV2ConfirmationError("Parent mapping lacks source identity")
    value = str(row).strip()
    if value.startswith("r"):
        value = value[1:]
    if not value.isdigit():
        raise StoreDataSyncPlanV2ConfirmationError(f"invalid source_row: {row}")
    return f"{sheet}:r{int(value)}"


def _proposed_path(row, current):
    if current:
        return current["path"]
    directory = "merchant_cases" if row["can_enter_content_index"] else "_vault_only"
    slug = unicodedata.normalize("NFKD", row["brand_name"]).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "record"
    return f"{directory}/record-r{row['source_row']}-{slug}.md"


def _current_value(metadata, field):
    if field == "can_external_reference" and field not in metadata:
        return metadata.get("can_quote_externally")
    return metadata.get(field)


def _normalized(value):
    if value is None or value == "":
        return None
    return list(value) if isinstance(value, tuple) else value


def _boolean(value):
    return value if isinstance(value, bool) else _text(value).lower() in {"1", "true", "yes", "y"}


def _normalize_query(value):
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def _text(value):
    return "" if value is None else str(value).strip()


def _input_root_from_plan(plan_path):
    return Path(plan_path).resolve().parents[2]


def _resolve(root, path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()


def _safe_name(value):
    name = Path(str(value)).name
    if not name or name != str(value) or name.startswith("."):
        raise StoreDataSyncPlanV2ConfirmationError("unsafe Confirmation filename")
    return name


def _sqlite_sidecars(path):
    return {item.name for item in Path(path).parent.glob(f"{Path(path).name}-*") if item.is_file()}


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hash_path(path):
    path = Path(path)
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


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
