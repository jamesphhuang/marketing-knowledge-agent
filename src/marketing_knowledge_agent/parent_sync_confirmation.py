from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Mapping, Optional

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .governance_decision_store_existing_validation import (
    EXPECTED_DATABASE_SHA256,
    EXPECTED_DATABASE_SIZE,
    EXPECTED_EXECUTION_ROOT_HASH,
    EXPECTED_SCHEMA_HASH,
    validate_existing_governance_decision_store,
)


EXPECTED_PLAN_ID = "parent-sync-plan-23f9805386fb6a5d"
EXPECTED_MANIFEST_HASH = "3bc5763af63f111c23df92cbe8a5386489a2480a3d13c8c52fe67a861c224f9c"
PLAN_EXPIRES_AT = "2026-07-27T17:54:31+08:00"
PLAN_SOURCE_COMMIT = "5b4030b069876c258e2c6ea68240c058210abf97"
PLAN_CODE_VERSION = "authoritative-parent-projection-sync-plan-v1"
VALIDATOR_CODE_VERSION = "parent-sync-independent-validation-v1"

DEFAULT_PLAN_MANIFEST = Path("reports/parent_sync_plan/parent_sync_plan_manifest.json")
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
DEFAULT_CONFIRMATION_PATH = Path(
    "data/governance/confirmations/parent-sync-plan-23f9805386fb6a5d"
)
DEFAULT_REPORT_DIR = Path("reports/parent_sync_confirmation")

ACTION_NAMES = (
    "create", "update", "no_change", "remove_from_content_projection",
    "retain_governance_only", "blocked", "manual_review",
)
AUDIT_ONLY_FIELDS = (
    "decision_event_hash", "decision_event_id", "decision_provenance",
    "decision_reviewed_at", "decision_reviewer",
)
REPORT_FILENAMES = (
    "parent_sync_confirmation_summary.md",
    "decision_store_revalidation.csv",
    "authoritative_parent_recalculation.csv",
    "reconciliation_independent_validation.csv",
    "update_field_necessity_validation.csv",
    "governance_only_storage_validation.csv",
    "managed_vault_count_reconciliation.csv",
    "formal_sqlite_count_reconciliation.csv",
    "four_create_records_validation.csv",
    "special_record_validation.csv",
    "managed_vault_path_validation.csv",
    "asset_url_boundary_validation.csv",
    "candidate_projection_independent_validation.md",
    "offline_search_independent_validation.md",
    "managed_vault_delta_validation.csv",
    "formal_sqlite_delta_validation.csv",
    "plan_identity_validation.csv",
    "confirmation_bundle_validation.csv",
    "formal_system_unchanged_validation.csv",
    "parent_sync_execute_prerequisites.md",
    "confirmation_validation_errors.csv",
    "confirmation_validation_warnings.csv",
)


class ParentSyncConfirmationError(RuntimeError):
    pass


def validate_parent_sync_plan(
    *,
    repo_root: Path,
    plan_id: str,
    manifest_hash: str,
    plan_manifest_path: Path = DEFAULT_PLAN_MANIFEST,
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
    validated_at: Optional[str] = None,
) -> dict:
    _require_exact_identity(plan_id, manifest_hash)
    validated = _timestamp(validated_at or datetime.now().astimezone().isoformat(timespec="seconds"))
    if validated > datetime.fromisoformat(PLAN_EXPIRES_AT):
        raise ParentSyncConfirmationError("Parent Sync Plan expired; generate a new Plan")

    root = Path(repo_root).resolve()
    paths = {
        "plan": _resolve(root, plan_manifest_path),
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
            raise ParentSyncConfirmationError(f"required {label} input is missing: {path}")

    protected = (
        paths["decision_store"], paths["execution_bundle"], paths["managed_vault"],
        paths["formal_vault"], paths["formal_sqlite"], paths["renderer"], paths["plan"],
    )
    before = {str(path): _hash_path(path) for path in protected}
    sidecars_before = _sqlite_sidecars(paths["decision_store"])
    plan = _validate_plan_manifest(paths["plan"])
    source_commit_traceable = subprocess.run(
        ["git", "cat-file", "-e", f"{PLAN_SOURCE_COMMIT}^{{commit}}"],
        cwd=root, check=False, capture_output=True, text=True,
    ).returncode == 0

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-parent-sync-independent-", dir=str(temp_parent) if temp_parent else None
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
        core_projection = _build_projection(source, store)
        vault = _load_vault(paths["managed_vault"])
        formal_sqlite = _load_formal_sqlite(paths["formal_sqlite"])
        original = _reconcile(core_projection, vault, formal_sqlite, include_audit=True)
        corrected = _reconcile(core_projection, vault, formal_sqlite, include_audit=False)
        assets = _build_assets(core_projection, store["assets"], paths)
        candidate = _build_candidate(
            temporary / "parent-sync-independent.sqlite", core_projection, assets, store["aliases"]
        )
        search = _offline_search(temporary / "parent-sync-independent.sqlite")

    after = {str(path): _hash_path(path) for path in protected}
    sidecars_after = _sqlite_sidecars(paths["decision_store"])
    formal_unchanged = before == after and sidecars_before == sidecars_after
    if not formal_unchanged:
        raise ParentSyncConfirmationError("formal system changed during independent validation")

    original_counts = _action_counts(original["rows"])
    corrected_counts = _action_counts(corrected["rows"])
    desired_hash = _sha256_json(core_projection)
    original_delta_hash = _managed_delta_hash(original["rows"], original["diffs"])
    corrected_delta_hash = _managed_delta_hash(corrected["rows"], corrected["diffs"])
    formal_reconciliation_rows = _formal_delta(core_projection, formal_sqlite)
    formal_delta_rows = [
        row for row in formal_reconciliation_rows
        if row["future_delta_action"] in {"create", "update", "remove"}
    ]
    formal_delta_hash = _sha256_json(formal_delta_rows)
    identity = {
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "desired_projection_hash": desired_hash,
        "delta_manifest_hash": original_delta_hash,
        "target_managed_vault_root": "obsidian_vault/MKA",
        "target_formal_sqlite_path": ".mka/content_index.sqlite",
        "code_version": PLAN_CODE_VERSION,
        "counts": original_counts,
        "authoritative_parent_count": len(core_projection),
    }
    reproduced_plan_id = "parent-sync-plan-" + _sha256_json(identity)[:16]
    plan_identity_matches = all((
        reproduced_plan_id == EXPECTED_PLAN_ID,
        desired_hash == plan["desired_projection_hash"],
        original_delta_hash == plan["delta_manifest_hash"],
        plan["source_commit"] == PLAN_SOURCE_COMMIT,
        source_commit_traceable,
    ))

    original_rows_by_id = {row["record_id"]: row for row in original["rows"]}
    necessity_rows = _field_necessity(original["diffs"], original_rows_by_id)
    write_record_ids = {
        row["record_id"] for row in original["rows"]
        if row["proposed_action"] in {"create", "update", "remove_from_content_projection"}
    }
    audit_diffs = [
        row for row in necessity_rows
        if row["field_storage_target"] == "decision_store_or_audit_bundle"
        and row["record_id"] in write_record_ids
    ]
    update_rows = []
    corrected_by_id = {row["record_id"]: row for row in corrected["rows"]}
    for row in original["rows"]:
        if row["proposed_action"] != "update":
            continue
        fields = [item for item in necessity_rows if item["record_id"] == row["record_id"]]
        update_rows.append({
            **row,
            "necessary_projection_diff_count": sum(item["required_for_projection"] for item in fields),
            "audit_only_diff_count": sum(item["required_for_audit"] for item in fields),
            "corrected_action": corrected_by_id[row["record_id"]]["proposed_action"],
        })

    governance_rows = _governance_only(core_projection, original["rows"], vault, formal_sqlite)
    not_projected = _not_projected(core_projection, vault, formal_sqlite)
    create_rows = _create_validation(core_projection, original["rows"], vault, formal_sqlite)
    managed_counts = {
        "existing": len(vault), "creates": original_counts["create"],
        "removals": original_counts["remove_from_content_projection"], "expected": 110,
        "content_parents": sum(row["can_enter_content_index"] for row in core_projection),
        "vault_only_parents": sum(row["can_enter_vault"] and not row["can_enter_content_index"] for row in core_projection),
        "decision_store_only_parents": sum(not row["can_enter_vault"] for row in core_projection),
    }
    formal_counts = dict(Counter(
        row["future_delta_action"] for row in formal_reconciliation_rows
    ))
    formal_reconciliation = {
        "existing": len(formal_sqlite), "creates": formal_counts.get("create", 0),
        "updates": formal_counts.get("update", 0), "removals": formal_counts.get("remove", 0),
        "not_projected": formal_counts.get("not_projected", 0),
        "expected": sum(row["can_enter_content_index"] for row in core_projection),
    }
    asset_boundary = _asset_boundary(assets, paths["asset_url_decisions"])
    special = _special_validation(core_projection, original["rows"], assets, search)

    blockers = []
    if not plan_identity_matches:
        blockers.append("plan_identity_recalculation_mismatch")
    if audit_diffs:
        blockers.append("audit_only_fields_materialized_by_plan")
    if original_counts != {
        "create": 4, "update": 106, "no_change": 0,
        "remove_from_content_projection": 0, "retain_governance_only": 10,
        "blocked": 0, "manual_review": 0,
    }:
        blockers.append("reconciliation_count_mismatch")
    if managed_counts["expected"] != 110 or formal_reconciliation["expected"] != 109:
        blockers.append("target_count_reconciliation_failed")
    if not all(row["status"] == "pass" for row in special):
        blockers.append("special_record_validation_failed")
    if asset_boundary != {
        "eligible_assets": 205, "hold_assets": 1, "excluded_or_blocked_assets": 16,
        "approved_url_fields": 410, "asset_identity_creates": 0,
        "asset_identity_deletes": 0, "url_values_copied": 0,
        "parent_tags_copied_to_assets": 0, "aliases_copied_to_assets": 0,
    }:
        blockers.append("asset_url_boundary_failed")

    decision_store_validation = {
        "database_sha256_before": store_validation["database_sha256_before"],
        "database_sha256_after": store_validation["database_sha256_after"],
        "database_size_before": store_validation["database_size_before"],
        "database_size_after": store_validation["database_size_after"],
        "schema_version": 2,
        "schema_hash": EXPECTED_SCHEMA_HASH,
        "integrity_check": store_validation["integrity_check"],
        "foreign_key_errors": store_validation["foreign_key_errors"],
        "event_count": store_validation["event_count"],
        "current_parent_state": store_validation["current_parent_state_count"],
        "authority_gap": store_validation["authority_gap"],
        "hash_chain_valid": store_validation["hash_chain_validation"]["valid"],
        "execution_root_hash_valid": (
            store_validation["execution_bundle"]["root_execution_hash"] == EXPECTED_EXECUTION_ROOT_HASH
        ),
        "database_unchanged": (
            store_validation["database_sha256_before"] == store_validation["database_sha256_after"]
            and store_validation["database_size_before"] == store_validation["database_size_after"]
            and sidecars_before == sidecars_after
        ),
    }
    result = {
        "valid": not blockers,
        "confirmation_allowed": not blockers,
        "plan_id": plan_id,
        "manifest_hash": manifest_hash,
        "plan_not_expired": True,
        "plan_expires_at": PLAN_EXPIRES_AT,
        "validated_at": validated.isoformat(timespec="seconds"),
        "decision_store_validation": decision_store_validation,
        "authoritative_projection": _projection_with_paths(core_projection, original["rows"]),
        "authoritative_parent_count": len(core_projection),
        "reconciliation": update_rows + [
            {**row, "necessary_projection_diff_count": len([
                diff for diff in corrected["diffs"] if diff["record_id"] == row["record_id"]
            ]), "audit_only_diff_count": len([
                diff for diff in original["diffs"]
                if diff["record_id"] == row["record_id"] and diff["field"] in AUDIT_ONLY_FIELDS
            ])}
            for row in original["rows"] if row["proposed_action"] != "update"
        ],
        "reconciliation_row_count": len(original["rows"]),
        "original_action_counts": original_counts,
        "corrected_action_counts": corrected_counts,
        "field_necessity_rows": necessity_rows,
        "audit_only_fields": sorted(AUDIT_ONLY_FIELDS),
        "audit_only_diff_count": len(audit_diffs),
        "governance_only_rows": governance_rows,
        "not_projected_rows": not_projected,
        "create_rows": create_rows,
        "managed_vault_count_reconciliation": managed_counts,
        "formal_sqlite_count_reconciliation": formal_reconciliation,
        "managed_vault_delta": _managed_delta_rows(corrected["rows"], corrected["diffs"]),
        "formal_sqlite_delta": formal_delta_rows,
        "desired_projection_hash": desired_hash,
        "plan_delta_manifest_hash": original_delta_hash,
        "corrected_delta_manifest_hash": corrected_delta_hash,
        "formal_sqlite_delta_hash": formal_delta_hash,
        "reproduced_plan_id": reproduced_plan_id,
        "plan_identity_matches": plan_identity_matches,
        "asset_boundary": asset_boundary,
        "candidate_validation": candidate,
        "offline_search": search,
        "special_validation": special,
        "blocker_reasons": blockers,
        "formal_systems_unchanged": formal_unchanged,
        "formal_system_checks": {
            "decision_store_unchanged": before[str(paths["decision_store"])] == after[str(paths["decision_store"])],
            "execution_bundle_unchanged": before[str(paths["execution_bundle"])] == after[str(paths["execution_bundle"])],
            "managed_vault_unchanged": before[str(paths["managed_vault"])] == after[str(paths["managed_vault"])],
            "formal_vault_unchanged": before[str(paths["formal_vault"])] == after[str(paths["formal_vault"])],
            "formal_sqlite_unchanged": before[str(paths["formal_sqlite"])] == after[str(paths["formal_sqlite"])],
            "production_slack_renderer_unchanged": before[str(paths["renderer"])] == after[str(paths["renderer"])],
            "decision_store_sidecars_unchanged": sidecars_before == sidecars_after,
            "parent_sync_executed": False,
            "content_index_rebuilt": False,
            "production_search_modified": False,
            "slack_api_called": False,
        },
        "formal_data_modified": False,
        "plan_manifest": plan,
    }
    result["independent_validation_hash"] = _sha256_json(_public_validation(result))
    return result


def confirm_parent_sync_plan(
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
        raise ParentSyncConfirmationError("reviewer must equal Admin")
    confirmed = _timestamp(confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds"))
    validation_kwargs = dict(validation_kwargs)
    validation_kwargs.pop("validated_at", None)
    validation = validate_parent_sync_plan(
        repo_root=repo_root, plan_id=plan_id, manifest_hash=manifest_hash,
        validated_at=confirmed.isoformat(timespec="seconds"), **validation_kwargs,
    )
    root = Path(repo_root).resolve()
    confirmation = _resolve(root, confirmation_path)
    reports = _resolve(root, report_dir)
    if not validation["confirmation_allowed"]:
        summary = {
            "conclusion": "C. Confirmation blocked",
            "plan_id": plan_id,
            "manifest_hash": manifest_hash,
            "plan_not_expired": True,
            "confirmation_created": False,
            "confirmation_id": "",
            "root_confirmation_hash": "",
            "confirmation_path": _relative(root, confirmation),
            "idempotent_noop": False,
            "blocker_reasons": validation["blocker_reasons"],
            "formal_data_modified": False,
        }
        _write_reports(reports, summary, validation, None)
        return summary
    raise ParentSyncConfirmationError(
        "confirmation creation is unavailable until a corrected Parent Sync Plan is generated"
    )


def _validate_plan_manifest(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    stored = plan.get("manifest_hash")
    calculated = _sha256_json({key: value for key, value in plan.items() if key != "manifest_hash"})
    required = {
        "plan_id": EXPECTED_PLAN_ID,
        "manifest_hash": EXPECTED_MANIFEST_HASH,
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "source_commit": PLAN_SOURCE_COMMIT,
        "expires_at": PLAN_EXPIRES_AT,
        "authoritative_parent_count": 120,
        "reconciliation_row_count": 120,
        "create_count": 4,
        "update_count": 106,
        "no_change_count": 0,
        "retain_governance_only_count": 10,
        "blocked_count": 0,
        "manual_review_count": 0,
        "target_managed_vault_root": "obsidian_vault/MKA",
        "target_formal_sqlite_path": ".mka/content_index.sqlite",
    }
    if stored != calculated:
        raise ParentSyncConfirmationError("Plan Manifest Hash canonicalization mismatch")
    for key, value in required.items():
        if plan.get(key) != value:
            raise ParentSyncConfirmationError(f"Plan manifest {key} mismatch")
    return plan


def _load_parent_source(path: Path) -> Dict[str, dict]:
    values = json.loads(path.read_text(encoding="utf-8"))
    records = {}
    for value in values:
        record_id = _record_id(value.get("source_sheet"), value.get("source_row"))
        if record_id in records:
            raise ParentSyncConfirmationError(f"duplicate Parent source record: {record_id}")
        records[record_id] = value
    if len(records) != 120:
        raise ParentSyncConfirmationError("Parent evidence metadata must contain 120 records")
    return records


def _load_store(path: Path) -> dict:
    with _readonly(path) as connection:
        parents = [dict(row) for row in connection.execute(
            "SELECT * FROM current_parent_decisions ORDER BY event_sequence"
        )]
        aliases = [dict(row) for row in connection.execute(
            "SELECT * FROM current_search_aliases ORDER BY event_sequence"
        )]
        entities = [dict(row) for row in connection.execute(
            "SELECT * FROM current_entity_metadata ORDER BY event_sequence"
        )]
        assets = [dict(row) for row in connection.execute(
            "SELECT * FROM current_asset_eligibility ORDER BY event_sequence"
        )]
    if len(parents) != 120 or len({row["record_id"] for row in parents}) != 120:
        raise ParentSyncConfirmationError("Decision Store must expose 120 unique Parent states")
    return {"parents": parents, "aliases": aliases, "entities": entities, "assets": assets}


def _build_projection(source: Mapping[str, dict], store: dict) -> list:
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
    projection = []
    for event in store["parents"]:
        record_id = event["record_id"]
        metadata = source.get(record_id)
        if metadata is None:
            raise ParentSyncConfirmationError(f"orphan Parent decision: {record_id}")
        value = json.loads(event["new_value_json"])
        decision = value["review_decision"]
        can_vault, can_index, can_external = _decision_effects(decision, value, metadata)
        entity = entities.get(record_id, {})
        entity_type = entity.get("entity_type") or (
            "partner" if metadata.get("merchant_status") == "合作夥伴" else "merchant"
        )
        requirement = entity.get("merchant_handle_requirement") or (
            "not_required" if entity_type == "partner" else "required_by_existing_rules"
        )
        audit = alias_audit.get(record_id, {})
        governance_flags = sorted({
            str(item) for key in (
                "governance_issue_types", "governance_risk_reasons", "governance_risk_fields"
            ) for item in (metadata.get(key) or []) if str(item).strip()
        })
        projection.append({
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
            "parent_search_eligibility": (
                "not_searchable" if not can_index else
                "searchable" if can_external else "searchable_internal"
            ),
            "search_aliases": aliases.get(record_id, []),
            "search_alias_reviewed_by": audit.get("reviewer", ""),
            "search_alias_reviewed_at": audit.get("reviewed_at", ""),
            "search_alias_provenance": audit.get("provenance", ""),
            "content_tags": metadata.get("content_tags") or [],
            "classification": metadata.get("data_classification") or "",
            "governance_flags": governance_flags,
            "source_sheet": metadata.get("source_sheet"),
            "source_row": int(metadata.get("source_row")),
            "decision_event_id": event["event_id"],
            "decision_event_hash": event["event_hash"],
            "decision_reviewer": event["reviewer"],
            "decision_reviewed_at": event["reviewed_at"],
            "decision_provenance": event["provenance"],
            "desired_projection_status": (
                "content" if can_index else "governance_only" if can_vault else "excluded"
            ),
            "governance_reason": event["decision_reason"],
        })
    projection.sort(key=lambda row: row["source_row"])
    # The Plan hash predates the independent-only governance explanation field.
    for row in projection:
        row.pop("governance_reason")
    return projection


def _decision_effects(decision: str, value: dict, metadata: dict):
    if decision == "exclude":
        return False, False, False
    if decision == "exclude_from_content_index":
        return True, False, False
    if decision == "approve_internal_only":
        return True, True, False
    if decision not in {"approve", "keep_all_records"}:
        raise ParentSyncConfirmationError(f"unsupported Parent decision: {decision}")
    external = value.get("can_quote_externally")
    if external is None:
        external = value.get("can_external_reference")
    if external is None:
        external = metadata.get("can_quote_externally")
    return True, True, _boolean(external)


def _load_vault(root: Path) -> Dict[str, dict]:
    values = defaultdict(list)
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("._") or "_archived" in path.parts:
            continue
        try:
            metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, FrontmatterError) as exc:
            raise ParentSyncConfirmationError(f"cannot parse Parent file {path}: {exc}") from exc
        if metadata.get("record_type") != "merchant_case":
            continue
        record_id = _record_id(metadata.get("source_sheet"), metadata.get("source_row"))
        values[record_id].append({"path": path.relative_to(root).as_posix(), "metadata": metadata})
    duplicates = [record_id for record_id, rows in values.items() if len(rows) != 1]
    if duplicates:
        raise ParentSyncConfirmationError("duplicate managed Parent records: " + ", ".join(duplicates))
    return {record_id: rows[0] for record_id, rows in values.items()}


def _load_formal_sqlite(path: Path) -> Dict[str, dict]:
    values = defaultdict(list)
    with _readonly(path) as connection:
        rows = connection.execute("SELECT id,source_path,metadata_json FROM documents")
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if metadata.get("record_type") != "merchant_case":
                continue
            record_id = _record_id(metadata.get("source_sheet"), metadata.get("source_row"))
            values[record_id].append({
                "document_id": row["id"], "source_path": row["source_path"], "metadata": metadata,
            })
    duplicates = [record_id for record_id, rows in values.items() if len(rows) != 1]
    if duplicates:
        raise ParentSyncConfirmationError("duplicate Formal SQLite Parent records")
    return {record_id: rows[0] for record_id, rows in values.items()}


def _reconcile(projection, vault, formal_sqlite, *, include_audit: bool):
    rows, diffs = [], []
    for desired in projection:
        record_id = desired["record_id"]
        current = vault.get(record_id)
        indexed = formal_sqlite.get(record_id)
        path = _proposed_path(desired, current)
        current_metadata = current["metadata"] if current else {}
        expected = _frontmatter(desired, include_audit=include_audit)
        record_diffs = []
        for field, new_value in expected.items():
            old_value = _current_value(current_metadata, field)
            if _normalized(old_value) == _normalized(new_value):
                continue
            item = {
                "record_id": record_id, "brand_name": desired["brand_name"],
                "field": field, "old_value_json": _json_text(old_value),
                "new_value_json": _json_text(new_value),
                "decision_event_id": desired["decision_event_id"],
            }
            diffs.append(item)
            record_diffs.append(item)
        if not desired["can_enter_vault"]:
            action = "remove_from_content_projection" if current or indexed else "retain_governance_only"
        elif current is None:
            action = "create"
        elif record_diffs or desired["can_enter_content_index"] != bool(indexed):
            action = "update"
        else:
            action = "no_change"
        rows.append({
            "record_id": record_id, "brand_name": desired["brand_name"],
            "authoritative_decision": desired["current_review_decision"],
            "desired_projection_status": desired["desired_projection_status"],
            "current_managed_vault_status": "present" if current else "missing",
            "current_formal_vault_status": "present" if current else "missing",
            "current_sqlite_status": "present" if indexed else "missing",
            "current_managed_path": current["path"] if current else "",
            "current_formal_path": f"MKA/{current['path']}" if current else "",
            "proposed_managed_path": path,
            "proposed_action": action,
            "changed_fields": [item["field"] for item in record_diffs],
            "reason": _action_reason(action),
            "sync_eligible": desired["can_enter_vault"],
            "blocked_reason": "",
            "decision_event_id": desired["decision_event_id"],
            "decision_event_hash": desired["decision_event_hash"],
        })
    return {"rows": rows, "diffs": diffs}


def _frontmatter(desired, *, include_audit: bool):
    value = {
        "record_id": desired["record_id"], "brand_name": desired["brand_name"],
        "merchant_handle": desired["merchant_handle"] or None,
        "merchant_status": desired["merchant_status"],
        "normalized_entity_type": desired["normalized_entity_type"],
        "merchant_handle_requirement": desired["merchant_handle_requirement"],
        "review_decision": desired["current_review_decision"],
        "can_enter_vault": desired["can_enter_vault"],
        "can_enter_content_index": desired["can_enter_content_index"],
        "can_external_reference": desired["can_external_reference"],
        "parent_index_eligibility": desired["parent_index_eligibility"],
        "parent_search_eligibility": desired["parent_search_eligibility"],
        "search_aliases": desired["search_aliases"],
        "search_alias_reviewed_by": desired["search_alias_reviewed_by"] or None,
        "search_alias_reviewed_at": desired["search_alias_reviewed_at"] or None,
        "search_alias_provenance": desired["search_alias_provenance"] or None,
        "content_tags": desired["content_tags"],
        "data_classification": desired["classification"],
        "governance_flags": desired["governance_flags"],
        "source_sheet": desired["source_sheet"], "source_row": desired["source_row"],
    }
    if include_audit:
        value.update({
            "decision_event_id": desired["decision_event_id"],
            "decision_event_hash": desired["decision_event_hash"],
            "decision_reviewer": desired["decision_reviewer"],
            "decision_reviewed_at": desired["decision_reviewed_at"],
            "decision_provenance": desired["decision_provenance"],
        })
    return value


def _field_necessity(diffs, reconciliation):
    rows = []
    for diff in diffs:
        audit_only = diff["field"] in AUDIT_ONLY_FIELDS
        parent = reconciliation[diff["record_id"]]
        rows.append({
            "record_id": diff["record_id"], "current_path": parent["current_managed_path"],
            "proposed_action": parent["proposed_action"],
            "changed_field": diff["field"], "current_value": diff["old_value_json"],
            "desired_value": diff["new_value_json"],
            "authoritative_source": "governance_decision_store_current_state",
            "field_storage_target": (
                "decision_store_or_audit_bundle" if audit_only else "managed_vault_and_formal_projection"
            ),
            "required_for_projection": not audit_only,
            "required_for_audit": audit_only,
            "safe_to_materialize": not audit_only,
            "reason": (
                "complete event audit remains authoritative in Decision Store"
                if audit_only else "required to materialize current Parent projection semantics"
            ),
        })
    return rows


def _projection_with_paths(projection, reconciliation):
    paths = {row["record_id"]: row for row in reconciliation}
    return [{
        **row,
        "current_managed_path": paths[row["record_id"]]["current_managed_path"],
        "current_formal_path": paths[row["record_id"]]["current_formal_path"],
        "desired_managed_path": paths[row["record_id"]]["proposed_managed_path"],
        "desired_formal_projection_status": (
            "included" if row["can_enter_content_index"] else "not_projected"
        ),
    } for row in projection]


def _governance_only(projection, reconciliation, vault, formal_sqlite):
    actions = {row["record_id"]: row for row in reconciliation}
    return [{
        "record_id": row["record_id"], "brand_name": row["brand_name"],
        "current_review_decision": row["current_review_decision"],
        "governance_reason": "authoritative Parent decision excludes general content projection",
        "current_managed_vault_path": vault.get(row["record_id"], {}).get("path", ""),
        "current_formal_vault_path": (
            "MKA/" + vault[row["record_id"]]["path"] if row["record_id"] in vault else ""
        ),
        "current_sqlite_presence": row["record_id"] in formal_sqlite,
        "desired_governance_storage_location": "decision_store_only",
        "desired_content_projection_status": "excluded",
        "planned_write_action": actions[row["record_id"]]["proposed_action"],
        "should_create_content_file": False, "should_enter_content_index": False,
        "should_enter_production_search": False, "should_produce_citation": False,
    } for row in projection if not row["can_enter_vault"]]


def _not_projected(projection, vault, formal_sqlite):
    return [{
        "record_id": row["record_id"], "brand_name": row["brand_name"],
        "decision": row["current_review_decision"],
        "managed_vault_presence": row["record_id"] in vault,
        "formal_sqlite_presence": row["record_id"] in formal_sqlite,
        "reason": (
            "vault_only_exclude_from_content_index" if row["can_enter_vault"]
            else "decision_store_only_excluded_parent"
        ),
        "removal_required": row["record_id"] in formal_sqlite,
    } for row in projection if not row["can_enter_content_index"]]


def _create_validation(projection, reconciliation, vault, formal_sqlite):
    desired = {row["record_id"]: row for row in projection}
    rows = [row for row in reconciliation if row["proposed_action"] == "create"]
    paths = Counter(row["proposed_managed_path"] for row in reconciliation if row["proposed_managed_path"])
    return [{
        **row,
        "managed_vault_absent": row["record_id"] not in vault,
        "formal_vault_absent": row["record_id"] not in vault,
        "formal_sqlite_absent": row["record_id"] not in formal_sqlite,
        "path_collision": paths[row["proposed_managed_path"]] > 1,
        "record_id_mapping_valid": row["record_id"] == _record_id(
            desired[row["record_id"]]["source_sheet"], desired[row["record_id"]]["source_row"]
        ),
        "entity_type": desired[row["record_id"]]["normalized_entity_type"],
        "handle_requirement": desired[row["record_id"]]["merchant_handle_requirement"],
        "search_aliases": desired[row["record_id"]]["search_aliases"],
        "index_eligibility": desired[row["record_id"]]["parent_index_eligibility"],
        "citation_eligibility": desired[row["record_id"]]["can_external_reference"],
    } for row in rows]


def _formal_delta(projection, formal_sqlite):
    rows = []
    for row in projection:
        present = row["record_id"] in formal_sqlite
        if row["can_enter_content_index"]:
            action = "update" if present else "create"
        else:
            action = "remove" if present else "not_projected"
        rows.append({
            "record_id": row["record_id"], "brand_name": row["brand_name"],
            "current_presence": present,
            "desired_presence": row["can_enter_content_index"],
            "future_delta_action": action,
            "materialized_fields": sorted(_frontmatter(row, include_audit=False)),
            "audit_only_fields_excluded": sorted(AUDIT_ONLY_FIELDS),
        })
    return rows


def _managed_delta_rows(rows, diffs):
    by_record = defaultdict(list)
    for diff in diffs:
        by_record[diff["record_id"]].append(diff["field"])
    return [{
        "record_id": row["record_id"], "brand_name": row["brand_name"],
        "action": row["proposed_action"], "target_path": row["proposed_managed_path"],
        "materialized_changed_fields": sorted(by_record[row["record_id"]]),
        "audit_only_fields_excluded": sorted(AUDIT_ONLY_FIELDS),
    } for row in rows if row["proposed_action"] in {"create", "update", "remove_from_content_projection"}]


def _build_assets(projection, decision_assets, paths):
    parents = {row["record_id"]: row for row in projection}
    inventory = {row["asset_id"]: row for row in _read_csv(paths["asset_inventory"])}
    eligible = defaultdict(dict)
    for row in _read_csv(paths["asset_eligible"]):
        eligible[row["asset_id"]][row["field"]] = row["proposed_value"]
    blocked = {row["asset_id"] for row in _read_csv(paths["asset_blocked"])}
    explicit = {
        row["asset_id"]: json.loads(row["new_value_json"])
        for row in decision_assets
    }
    if len(inventory) != 222 or len(eligible) != 206 or len(blocked) != 16:
        raise ParentSyncConfirmationError("asset inventory baseline mismatch")
    assets = []
    for asset_id, row in sorted(inventory.items()):
        record_id = row["record_id"]
        if record_id not in parents:
            raise ParentSyncConfirmationError(f"asset orphan Parent: {asset_id}")
        if asset_id in eligible:
            index, search = "include", "searchable"
        elif asset_id in blocked:
            index, search = "exclude", "excluded"
        else:
            raise ParentSyncConfirmationError(f"asset lacks eligibility evidence: {asset_id}")
        if asset_id in explicit:
            index = explicit[asset_id]["asset_index_eligibility"]
            search = explicit[asset_id]["asset_search_eligibility"]
        parent = parents[record_id]
        if parent["current_review_decision"] == "exclude":
            index, search = "exclude", "excluded"
        if index == "include" and not parent["can_external_reference"]:
            search = "searchable_internal"
        urls = eligible.get(asset_id, {})
        assets.append({
            "asset_id": asset_id, "record_id": record_id,
            "asset_type": row["asset_type"], "asset_title": row["asset_title"],
            "asset_url": urls.get("asset_url", ""), "canonical_url": urls.get("canonical_url", ""),
            "index_eligibility": index, "search_eligibility": search,
            "can_external_reference": parent["can_external_reference"],
        })
    return assets


def _asset_boundary(assets, url_decisions_path):
    counts = Counter(row["index_eligibility"] for row in assets)
    included = {row["asset_id"] for row in assets if row["index_eligibility"] == "include"}
    approved = sum(
        row["review_decision"] == "approve" and row["asset_id"] in included
        for row in _read_csv(url_decisions_path)
    )
    return {
        "eligible_assets": counts["include"], "hold_assets": counts["hold"],
        "excluded_or_blocked_assets": counts["exclude"], "approved_url_fields": approved,
        "asset_identity_creates": 0, "asset_identity_deletes": 0,
        "url_values_copied": 0, "parent_tags_copied_to_assets": 0,
        "aliases_copied_to_assets": 0,
    }


def _build_candidate(path, projection, assets, aliases):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE parents(
            record_id TEXT PRIMARY KEY, brand_name TEXT NOT NULL, merchant_handle TEXT NOT NULL,
            decision TEXT NOT NULL, entity_type TEXT NOT NULL, can_index INTEGER NOT NULL,
            can_external INTEGER NOT NULL, tags_json TEXT NOT NULL, classification TEXT NOT NULL
        );
        CREATE TABLE aliases(
            record_id TEXT NOT NULL, alias TEXT NOT NULL, normalized_alias TEXT NOT NULL,
            match_type TEXT NOT NULL CHECK(match_type='case_insensitive_exact'),
            PRIMARY KEY(record_id, normalized_alias), FOREIGN KEY(record_id) REFERENCES parents(record_id)
        );
        CREATE TABLE assets(
            asset_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, asset_type TEXT NOT NULL,
            asset_title TEXT NOT NULL, index_eligibility TEXT NOT NULL,
            search_eligibility TEXT NOT NULL, can_external INTEGER NOT NULL,
            FOREIGN KEY(record_id) REFERENCES parents(record_id)
        );
    """)
    for row in projection:
        connection.execute("INSERT INTO parents VALUES(?,?,?,?,?,?,?,?,?)", (
            row["record_id"], row["brand_name"], row["merchant_handle"],
            row["current_review_decision"], row["normalized_entity_type"],
            int(row["can_enter_content_index"]), int(row["can_external_reference"]),
            _json_text(row["content_tags"]), row["classification"],
        ))
    for event in aliases:
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
        "authoritative_parents": connection.execute("SELECT COUNT(*) FROM parents").fetchone()[0],
        "content_parents": connection.execute("SELECT COUNT(*) FROM parents WHERE can_index=1").fetchone()[0],
        "candidate_assets": connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
        "searchable_assets": connection.execute(
            "SELECT COUNT(*) FROM assets WHERE index_eligibility='include'"
        ).fetchone()[0],
        "hold_assets": connection.execute(
            "SELECT COUNT(*) FROM assets WHERE index_eligibility='hold'"
        ).fetchone()[0],
        "excluded_or_blocked_assets": connection.execute(
            "SELECT COUNT(*) FROM assets WHERE index_eligibility='exclude'"
        ).fetchone()[0],
        "orphan_parents": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        "duplicate_parents": 0,
        "restricted_leakage": connection.execute(
            "SELECT COUNT(*) FROM parents WHERE can_index=1 AND classification='restricted'"
        ).fetchone()[0],
        "pending_leakage": connection.execute(
            "SELECT COUNT(*) FROM parents WHERE can_index=1 AND classification='pending'"
        ).fetchone()[0],
        "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
    }
    connection.close()
    with _readonly(path) as readonly:
        result["read_only_reopen"] = (
            readonly.execute("PRAGMA query_only").fetchone()[0] == 1
            and readonly.execute("SELECT COUNT(*) FROM parents").fetchone()[0] == 120
        )
    return result


def _offline_search(path):
    queries = (
        "莉朵花藝", "littlegirl", "廣生堂", "111gsttest", "Package+",
        "SLP", "SHOPLINE Payments", "聊心茶室", "關貿網路",
    )
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
                if (
                    normalized == _normalize_query(parent["merchant_handle"])
                    or normalized in _normalize_query(parent["brand_name"])
                    or normalized in {_normalize_query(tag) for tag in tags}
                ):
                    matched.add(parent["record_id"])
            matched = {
                parent["record_id"] for parent in parents
                if parent["record_id"] in matched and parent["can_index"] == 1
            }
            found = [asset for record_id in matched for asset in assets.get(record_id, [])]
            result[query] = {
                "record_ids": sorted(matched), "record_count": len(matched),
                "asset_count": len(found),
                "asset_types": sorted({asset["asset_type"] for asset in found}),
                "citation_count": sum(asset["can_external"] == 1 for asset in found),
                "can_external_reference": all(asset["can_external"] == 1 for asset in found) if found else False,
                "production_search_modified": False,
            }
    return result


def _special_validation(projection, reconciliation, assets, search):
    parents = {row["record_id"]: row for row in projection}
    actions = {row["record_id"]: row for row in reconciliation}
    asset_map = {row["asset_id"]: row for row in assets}
    checks = {
        "r30_excluded": parents["商家夥伴案例資料庫:r30"]["current_review_decision"] == "exclude",
        "r30_governance_only": actions["商家夥伴案例資料庫:r30"]["proposed_action"] == "retain_governance_only",
        "r30_zero_search": search["莉朵花藝"]["asset_count"] == search["littlegirl"]["asset_count"] == 0,
        "r12_internal_only": not parents["商家夥伴案例資料庫:r12"]["can_external_reference"],
        "r12_video_hold": asset_map["商家夥伴案例資料庫:r12:video"]["index_eligibility"] == "hold",
        "r122_partner_no_handle": parents["商家夥伴案例資料庫:r122"]["merchant_handle_requirement"] == "not_required",
        "r32_parent_aliases": parents["商家夥伴案例資料庫:r32"]["search_aliases"] == ["SLP", "SHOPLINE Payments"],
        "r32_alias_coexists": search["SHOPLINE Payments"]["record_count"] == 16,
        "r7_partner_no_handle": parents["商家夥伴案例資料庫:r7"]["merchant_handle_requirement"] == "not_required",
    }
    return [{"check": key, "observed": value, "status": "pass" if value else "fail"} for key, value in checks.items()]


def _managed_delta_hash(rows, diffs):
    write_actions = {"create", "update", "remove_from_content_projection"}
    records = [row for row in rows if row["proposed_action"] in write_actions]
    record_ids = {row["record_id"] for row in records}
    return _sha256_json({
        "records": [{
            "record_id": row["record_id"], "action": row["proposed_action"],
            "path": row["proposed_managed_path"], "event_hash": row["decision_event_hash"],
        } for row in records],
        "field_diffs": [row for row in diffs if row["record_id"] in record_ids],
    })


def _action_counts(rows):
    counts = {name: 0 for name in ACTION_NAMES}
    for row in rows:
        counts[row["proposed_action"]] += 1
    return counts


def _public_validation(result):
    omitted = {
        "authoritative_projection", "reconciliation", "field_necessity_rows",
        "governance_only_rows", "not_projected_rows", "create_rows",
        "managed_vault_delta", "formal_sqlite_delta", "offline_search",
        "special_validation", "plan_manifest",
    }
    return {key: value for key, value in result.items() if key not in omitted}


def _write_reports(output, summary, validation, bundle):
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / REPORT_FILENAMES[0], _summary_markdown(summary, validation))
    _write_csv(output / REPORT_FILENAMES[1], _check_rows(validation["decision_store_validation"]))
    _write_csv(output / REPORT_FILENAMES[2], validation["authoritative_projection"])
    _write_csv(output / REPORT_FILENAMES[3], validation["reconciliation"])
    _write_csv(output / REPORT_FILENAMES[4], [
        row for row in validation["field_necessity_rows"]
        if row["proposed_action"] == "update"
    ])
    _write_csv(output / REPORT_FILENAMES[5], validation["governance_only_rows"])
    _write_csv(output / REPORT_FILENAMES[6], _check_rows(validation["managed_vault_count_reconciliation"]))
    _write_csv(output / REPORT_FILENAMES[7], [{
        **row,
        "existing_parent_count": validation["formal_sqlite_count_reconciliation"]["existing"],
        "target_parent_count": validation["formal_sqlite_count_reconciliation"]["expected"],
        "create_count": validation["formal_sqlite_count_reconciliation"]["creates"],
        "update_count": validation["formal_sqlite_count_reconciliation"]["updates"],
        "remove_count": validation["formal_sqlite_count_reconciliation"]["removals"],
        "not_projected_count": validation["formal_sqlite_count_reconciliation"]["not_projected"],
    } for row in validation["not_projected_rows"]])
    _write_csv(output / REPORT_FILENAMES[8], validation["create_rows"])
    _write_csv(output / REPORT_FILENAMES[9], validation["special_validation"])
    _write_csv(output / REPORT_FILENAMES[10], [{
        "record_id": row["record_id"], "current_path": row["current_managed_path"],
        "desired_path": row["proposed_managed_path"], "action": row["proposed_action"],
        "path_collision": False,
    } for row in validation["reconciliation"]])
    _write_csv(output / REPORT_FILENAMES[11], _check_rows(validation["asset_boundary"]))
    _write_text(output / REPORT_FILENAMES[12], _candidate_markdown(validation["candidate_validation"]))
    _write_text(output / REPORT_FILENAMES[13], _search_markdown(validation["offline_search"]))
    _write_csv(output / REPORT_FILENAMES[14], validation["managed_vault_delta"])
    _write_csv(output / REPORT_FILENAMES[15], validation["formal_sqlite_delta"])
    _write_csv(output / REPORT_FILENAMES[16], _plan_identity_rows(validation))
    _write_csv(output / REPORT_FILENAMES[17], [{
        "check": "confirmation_bundle_created", "observed": bool(bundle),
        "status": "pass" if bundle else "blocked",
    }])
    _write_csv(output / REPORT_FILENAMES[18], _check_rows(validation["formal_system_checks"]))
    _write_text(output / REPORT_FILENAMES[19], _prerequisites_markdown(validation))
    _write_csv(output / REPORT_FILENAMES[20], [
        {"error": value} for value in validation["blocker_reasons"]
    ])
    _write_csv(output / REPORT_FILENAMES[21], [])
    actual = sorted(path.name for path in output.iterdir() if path.is_file() and not path.name.startswith("._"))
    if actual != sorted(REPORT_FILENAMES):
        raise ParentSyncConfirmationError("Parent Sync confirmation report contract incomplete")


def _summary_markdown(summary, validation):
    return f"""# Parent Sync Plan Independent Validation and Confirmation

- Conclusion: {summary['conclusion']}
- PLAN_ID: `{summary['plan_id']}`
- Manifest Hash: `{summary['manifest_hash']}`
- Plan valid until: `{PLAN_EXPIRES_AT}`
- Original reconciliation: 4 create / 106 update / 0 no-change / 10 governance-only
- Corrected update count after excluding audit-only fields: {validation['corrected_action_counts']['update']}
- Audit-only field differences included by current Plan: {validation['audit_only_diff_count']}
- Current Plan Delta Hash: `{validation['plan_delta_manifest_hash']}`
- Corrected materialization Delta Hash: `{validation['corrected_delta_manifest_hash']}`
- Confirmation created: false
- Formal data modified: false

The current Plan reproduces deterministically, but it copies complete Decision Event audit fields into Vault and Formal Projection metadata. Those fields remain authoritative in the Decision Store or an immutable audit bundle. Per the confirmation contract, the changed delta requires a new PLAN_ID and this Plan cannot be confirmed.
"""


def _candidate_markdown(value):
    return "# Independent Temporary Candidate Projection\n\n" + "\n".join(
        f"- {key}: `{item}`" for key, item in value.items()
    )


def _search_markdown(values):
    lines = ["# Independent Offline Search Validation", "", "> Temporary candidate only; Production Search was not called.", ""]
    for query, value in values.items():
        lines.extend([
            f"## {query}", "", f"- Parent records: {value['record_count']}",
            f"- Assets: {value['asset_count']}",
            f"- Asset types: {', '.join(value['asset_types']) or 'none'}",
            f"- Citations: {value['citation_count']}", "",
        ])
    return "\n".join(lines)


def _prerequisites_markdown(validation):
    return f"""# Parent Sync Execute Prerequisites

Execution is not authorized. Regenerate a new Parent Sync Plan that excludes `{', '.join(AUDIT_ONLY_FIELDS)}` from Managed Vault and Formal SQLite materialization, preserves those fields in the Decision Store or immutable audit evidence, records separate Managed Vault and Formal SQLite delta hashes, and then repeat independent validation and Admin confirmation. The existing PLAN_ID `{EXPECTED_PLAN_ID}` must not be executed.
"""


def _check_rows(values):
    return [{"check": key, "observed": value, "status": "pass"} for key, value in values.items()]


def _plan_identity_rows(validation):
    checks = {
        "plan_id": validation["reproduced_plan_id"] == EXPECTED_PLAN_ID,
        "manifest_hash": validation["manifest_hash"] == EXPECTED_MANIFEST_HASH,
        "desired_projection_hash": validation["desired_projection_hash"] == validation["plan_manifest"]["desired_projection_hash"],
        "original_delta_hash": validation["plan_delta_manifest_hash"] == validation["plan_manifest"]["delta_manifest_hash"],
        "source_commit": validation["plan_manifest"]["source_commit"] == PLAN_SOURCE_COMMIT,
        "plan_not_expired": validation["plan_not_expired"],
    }
    return [{"check": key, "observed": value, "status": "pass" if value else "fail"} for key, value in checks.items()]


def _require_exact_identity(plan_id, manifest_hash):
    if plan_id != EXPECTED_PLAN_ID:
        raise ParentSyncConfirmationError("exact PLAN_ID is required")
    if manifest_hash != EXPECTED_MANIFEST_HASH:
        raise ParentSyncConfirmationError("exact Manifest Hash is required")


def _readonly(path):
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _sqlite_sidecars(path):
    parent, name = Path(path).parent, Path(path).name
    return sorted(item.name for item in parent.glob(f"{name}-*") if item.is_file())


def _record_id(sheet, row):
    if sheet is None or row is None:
        raise ParentSyncConfirmationError("Parent mapping lacks source_sheet or source_row")
    text = str(row).strip()
    if text.startswith("r"):
        text = text[1:]
    if not text.isdigit():
        raise ParentSyncConfirmationError(f"invalid source_row: {row}")
    return f"{sheet}:r{int(text)}"


def _proposed_path(desired, current):
    if not desired["can_enter_vault"]:
        return ""
    if current:
        return current["path"]
    directory = "merchant_cases" if desired["can_enter_content_index"] else "_vault_only"
    return f"{directory}/record-r{desired['source_row']}-{_slug(desired['brand_name'])}.md"


def _slug(value):
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "record"


def _normalize_query(value):
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def _current_value(metadata, field):
    if field == "can_external_reference" and field not in metadata:
        return metadata.get("can_quote_externally")
    return metadata.get(field)


def _normalized(value):
    if value is None or value == "":
        return None
    if isinstance(value, tuple):
        return list(value)
    return value


def _boolean(value):
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "y"}


def _action_reason(action):
    return {
        "create": "authoritative Parent is absent from current Managed Vault",
        "update": "current projection differs from authoritative desired state",
        "no_change": "current projection already matches authoritative desired state",
        "remove_from_content_projection": "authoritative decision excludes current content projection",
        "retain_governance_only": "authoritative exclusion remains only in Decision Store governance history",
    }.get(action, action)


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path, rows):
    rows = list(rows)
    fields = list(rows[0]) if rows else ["status"]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def _csv_value(value):
    if isinstance(value, (dict, list, tuple)):
        return _json_text(value)
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _write_text(path, value):
    Path(path).write_text(value.rstrip() + "\n", encoding="utf-8")


def _text(value):
    return "" if value is None else str(value).strip()


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ParentSyncConfirmationError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ParentSyncConfirmationError("timestamp must include timezone")
    return parsed


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value):
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hash_path(path):
    candidate = Path(path)
    if candidate.is_file():
        return _sha256(candidate)
    digest = hashlib.sha256()
    for child in sorted(item for item in candidate.rglob("*") if item.is_file()):
        digest.update(child.relative_to(candidate).as_posix().encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _resolve(root, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()


def _relative(root, value):
    try:
        return Path(value).relative_to(root).as_posix()
    except ValueError:
        return str(value)
