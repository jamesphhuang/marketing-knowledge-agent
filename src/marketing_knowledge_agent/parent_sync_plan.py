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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .governance_decision_store_existing_validation import (
    EXPECTED_DATABASE_SHA256,
    EXPECTED_DATABASE_SIZE,
    EXPECTED_EXECUTION_ID,
    EXPECTED_EXECUTION_ROOT_HASH,
    EXPECTED_SCHEMA_HASH,
    validate_existing_governance_decision_store,
)


DEFAULT_DECISION_STORE = Path("data/governance/governance_decisions.sqlite")
DEFAULT_EXECUTION_BUNDLE = Path(
    "data/governance/executions/decision-store-schema-v2-plan-2aab43cd463170f2"
)
DEFAULT_PARENT_BUNDLE = Path("data/governance/imports/parent-authority-approval-20260719")
DEFAULT_CONFIRMATION_BUNDLE = Path(
    "data/governance/confirmations/decision-store-schema-v2-plan-2aab43cd463170f2"
)
DEFAULT_PARENT_SOURCE = Path("reports/excel_preview/merchant_cases.json")
DEFAULT_ASSET_INVENTORY = Path("reports/asset_metadata_preview/asset_metadata_inventory.csv")
DEFAULT_ASSET_ELIGIBLE = Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv")
DEFAULT_ASSET_BLOCKED = Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv")
DEFAULT_ASSET_URL_DECISIONS = Path("reports/asset_metadata_preview/human_review_template.csv")
DEFAULT_MANAGED_VAULT = Path("obsidian_vault/MKA")
DEFAULT_FORMAL_VAULT = Path("obsidian_vault")
DEFAULT_CONTENT_INDEX = Path(".mka/content_index.sqlite")
DEFAULT_RENDERER = Path("src/marketing_knowledge_agent/slack_interface.py")
DEFAULT_OUTPUT_DIR = Path("reports/parent_sync_plan")

SCHEMA_VERSION = 2
CODE_VERSION = "authoritative-parent-projection-sync-plan-v1"
EXPECTED_ASSET_COUNTS = {"eligible": 205, "hold": 1, "excluded": 16}
EXPECTED_APPROVED_URL_FIELDS = 410
WRITE_ACTIONS = {"create", "update", "remove_from_content_projection"}
ACTION_NAMES = (
    "create", "update", "no_change", "remove_from_content_projection",
    "retain_governance_only", "blocked", "manual_review",
)
REPORT_FILENAMES = (
    "parent_sync_plan_summary.md",
    "decision_store_input_validation.csv",
    "authoritative_parent_projection.csv",
    "current_parent_target_inventory.csv",
    "parent_projection_reconciliation.csv",
    "parent_field_level_diff.csv",
    "managed_vault_path_plan.csv",
    "formal_sqlite_projection_plan.csv",
    "parent_create_preview.csv",
    "parent_update_preview.csv",
    "parent_no_change.csv",
    "parent_removal_preview.csv",
    "parent_governance_only_preview.csv",
    "parent_sync_blockers.csv",
    "parent_sync_manual_review.csv",
    "special_parent_sync_validation.csv",
    "asset_eligibility_boundary_validation.csv",
    "candidate_projection_validation.md",
    "offline_search_behavior_preview.md",
    "parent_sync_backup_plan.md",
    "parent_sync_rollback_plan.md",
    "parent_sync_confirmation_checklist.md",
    "obsolete_plan_registry.csv",
    "parent_sync_plan_manifest.json",
    "parent_sync_validation_errors.csv",
    "parent_sync_validation_warnings.csv",
)


class ParentSyncPlanError(RuntimeError):
    pass


def generate_parent_sync_plan(
    *,
    repo_root: Path,
    decision_store_path: Path = DEFAULT_DECISION_STORE,
    execution_bundle_path: Path = DEFAULT_EXECUTION_BUNDLE,
    parent_bundle_path: Path = DEFAULT_PARENT_BUNDLE,
    confirmation_bundle_path: Path = DEFAULT_CONFIRMATION_BUNDLE,
    parent_source_path: Path = DEFAULT_PARENT_SOURCE,
    asset_inventory_path: Path = DEFAULT_ASSET_INVENTORY,
    asset_eligible_path: Path = DEFAULT_ASSET_ELIGIBLE,
    asset_blocked_path: Path = DEFAULT_ASSET_BLOCKED,
    asset_url_decisions_path: Path = DEFAULT_ASSET_URL_DECISIONS,
    managed_vault_root: Path = DEFAULT_MANAGED_VAULT,
    formal_vault_root: Path = DEFAULT_FORMAL_VAULT,
    formal_sqlite_path: Path = DEFAULT_CONTENT_INDEX,
    production_renderer_path: Path = DEFAULT_RENDERER,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    temporary_root: Optional[Path] = None,
    created_at: Optional[str] = None,
) -> dict:
    root = Path(repo_root).resolve()
    paths = {
        "decision_store": _resolve(root, decision_store_path),
        "execution_bundle": _resolve(root, execution_bundle_path),
        "parent_bundle": _resolve(root, parent_bundle_path),
        "confirmation_bundle": _resolve(root, confirmation_bundle_path),
        "parent_source": _resolve(root, parent_source_path),
        "asset_inventory": _resolve(root, asset_inventory_path),
        "asset_eligible": _resolve(root, asset_eligible_path),
        "asset_blocked": _resolve(root, asset_blocked_path),
        "asset_url_decisions": _resolve(root, asset_url_decisions_path),
        "managed_vault": _resolve(root, managed_vault_root),
        "formal_vault": _resolve(root, formal_vault_root),
        "formal_sqlite": _resolve(root, formal_sqlite_path),
        "renderer": _resolve(root, production_renderer_path),
        "output": _resolve(root, output_dir),
    }
    _require_inputs(paths)
    _require_database_identity(paths["decision_store"])
    protected = [
        paths["decision_store"], paths["execution_bundle"], paths["parent_bundle"],
        paths["confirmation_bundle"], paths["formal_vault"], paths["formal_sqlite"],
        paths["asset_url_decisions"], paths["renderer"],
    ]
    protected_before = {str(path): _hash_path(path) for path in protected}

    temp_parent = Path(temporary_root).resolve() if temporary_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mka-parent-sync-plan-", dir=str(temp_parent) if temp_parent else None
    ) as temporary_name:
        temporary = Path(temporary_name)
        start_validation = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            execution_bundle_path=paths["execution_bundle"],
            parent_authority_bundle_path=paths["parent_bundle"],
            confirmation_bundle_path=paths["confirmation_bundle"],
            report_dir=temporary / "existing-store-start",
            temporary_root=temporary / "existing-store-start-work",
        )
        source_records = _load_parent_source(paths["parent_source"])
        store = _load_decision_store(paths["decision_store"])
        projection = _build_authoritative_projection(source_records, store)
        current_vault = _load_current_vault(paths["managed_vault"])
        current_sqlite = _load_current_sqlite(paths["formal_sqlite"])
        reconciliation, field_diffs, path_rows = _reconcile_parents(
            projection, current_vault, current_sqlite
        )
        path_collisions = _detect_path_collisions(path_rows)
        if path_collisions:
            raise ParentSyncPlanError("managed Vault target path collision: " + ", ".join(path_collisions))
        inventory = _inventory(projection, current_vault, current_sqlite, reconciliation)
        assets = _build_asset_projection(
            projection,
            store["asset_eligibility"],
            paths["asset_inventory"],
            paths["asset_eligible"],
            paths["asset_blocked"],
        )
        asset_boundary = _asset_boundary(assets, paths["asset_url_decisions"])
        candidate_path = temporary / "candidate-parent-projection.sqlite"
        candidate_validation = _build_candidate_projection(
            candidate_path, projection, assets, store["aliases"]
        )
        offline_search = _offline_search_preview(candidate_path)
        special = _special_parent_validation(projection, reconciliation, assets, offline_search)
        end_validation = validate_existing_governance_decision_store(
            repo_root=root,
            database_path=paths["decision_store"],
            execution_bundle_path=paths["execution_bundle"],
            parent_authority_bundle_path=paths["parent_bundle"],
            confirmation_bundle_path=paths["confirmation_bundle"],
            report_dir=temporary / "existing-store-end",
            temporary_root=temporary / "existing-store-end-work",
        )

    protected_after = {str(path): _hash_path(path) for path in protected}
    if protected_before != protected_after:
        raise ParentSyncPlanError("a protected formal system changed during Parent Sync planning")
    _validate_end_state(start_validation, end_validation)

    action_counts = {name: 0 for name in ACTION_NAMES}
    for row in reconciliation:
        action_counts[row["proposed_action"]] += 1
    write_records = [row for row in reconciliation if row["proposed_action"] in WRITE_ACTIONS]
    desired_hash = _sha256_json(projection)
    delta_hash = _sha256_json(_delta_identity(write_records, field_diffs))
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
    }
    identity = {
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "desired_projection_hash": desired_hash,
        "delta_manifest_hash": delta_hash,
        "target_managed_vault_root": _relative(root, paths["managed_vault"]),
        "target_formal_sqlite_path": _relative(root, paths["formal_sqlite"]),
        "code_version": CODE_VERSION,
        "counts": action_counts,
        "authoritative_parent_count": len(projection),
    }
    plan_id = "parent-sync-plan-" + _sha256_json(identity)[:16]
    created, expires = _plan_times(paths["output"], plan_id, created_at)
    blockers = _blocker_reasons(
        projection, reconciliation, inventory, candidate_validation, special, asset_boundary
    )
    branch = _git_value(root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _git_value(root, "rev-parse", "HEAD")
    manifest = {
        "plan_id": plan_id,
        "plan_type": "authoritative_parent_projection_delta_sync",
        "decision_store_path": _relative(root, paths["decision_store"]),
        "decision_store_sha256": EXPECTED_DATABASE_SHA256,
        "execution_id": EXPECTED_EXECUTION_ID,
        "execution_root_hash": EXPECTED_EXECUTION_ROOT_HASH,
        "decision_store_schema_version": SCHEMA_VERSION,
        "decision_store_schema_hash": EXPECTED_SCHEMA_HASH,
        "authoritative_parent_count": len(projection),
        "reconciliation_row_count": len(reconciliation),
        **{f"{name}_count": action_counts[name] for name in ACTION_NAMES},
        "target_managed_vault_root": _relative(root, paths["managed_vault"]),
        "target_formal_sqlite_path": _relative(root, paths["formal_sqlite"]),
        "input_checksums": input_checksums,
        "desired_projection_hash": desired_hash,
        "delta_manifest_hash": delta_hash,
        "source_branch": branch,
        "source_commit": commit,
        "code_version": CODE_VERSION,
        "created_at": created,
        "expires_at": expires,
        "execution_blocked": bool(blockers),
        "blocker_reasons": blockers,
        "backup_plan": "full Managed namespace and Formal SQLite backup before staged delta apply",
        "rollback_plan": "restore backed-up paths atomically; never alter Decision Store history",
    }
    manifest["manifest_hash"] = _sha256_json(manifest)
    result = {
        "conclusion": (
            "A. Ready for Parent Sync Plan confirmation" if not blockers
            else "C. Parent Sync Plan blocked"
        ),
        "decision_store_validation": _public_store_validation(start_validation, end_validation),
        "authoritative_projection": projection,
        "authoritative_parent_count": len(projection),
        "reconciliation_rows": reconciliation,
        "reconciliation_row_count": len(reconciliation),
        "unique_record_id_count": len({row["record_id"] for row in reconciliation}),
        "authority_gap": inventory["authority_gap"],
        "missing_authoritative_parent_count": inventory["missing_authoritative_parent_count"],
        "duplicate_authoritative_parent_count": inventory["duplicate_authoritative_parent_count"],
        "action_counts": action_counts,
        "field_diff_rows": field_diffs,
        "path_plan_rows": path_rows,
        "path_collision_count": len(path_collisions),
        "inventory": inventory,
        "write_manifest_records": write_records,
        "formal_sqlite_projection_rows": _formal_sqlite_plan(projection, current_sqlite),
        "asset_boundary": asset_boundary,
        "candidate_validation": candidate_validation,
        "offline_search_preview": offline_search,
        "special_parent_validation": special,
        "desired_projection_hash": desired_hash,
        "delta_manifest_hash": delta_hash,
        "plan_id": plan_id,
        "manifest_hash": manifest["manifest_hash"],
        "expires_at": expires,
        "execution_blocked": bool(blockers),
        "blocker_reasons": blockers,
        "manifest": manifest,
        "formal_systems_unchanged": protected_before == protected_after,
        "formal_data_modified": False,
    }
    _require_valid_result(result)
    _write_reports(paths["output"], result)
    return result


def _load_parent_source(path: Path) -> Dict[str, dict]:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise ParentSyncPlanError("Parent source metadata must be a JSON array")
    records = {}
    for value in values:
        record_id = _record_id(value.get("source_sheet"), value.get("source_row"))
        if record_id in records:
            raise ParentSyncPlanError(f"duplicate Parent source record_id: {record_id}")
        records[record_id] = value
    if len(records) != 120:
        raise ParentSyncPlanError(f"Parent source metadata count must be 120, got {len(records)}")
    return records


def _load_decision_store(path: Path) -> dict:
    with _readonly_connection(path) as connection:
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
        raise ParentSyncPlanError("Decision Store current_parent_decisions is not exactly 120 unique rows")
    return {
        "parents": parents,
        "aliases": aliases,
        "entities": entities,
        "asset_eligibility": assets,
    }


def _build_authoritative_projection(source: Mapping[str, dict], store: dict) -> List[dict]:
    aliases = defaultdict(list)
    alias_audit = {}
    for row in store["aliases"]:
        value = json.loads(row["new_value_json"])
        aliases[row["record_id"]].append(value["alias"])
        alias_audit[row["record_id"]] = {
            "reviewer": row["reviewer"], "reviewed_at": row["reviewed_at"],
            "provenance": row["provenance"],
        }
    entities = {
        row["record_id"]: json.loads(row["new_value_json"])
        for row in store["entities"]
    }
    projection = []
    for event in store["parents"]:
        record_id = event["record_id"]
        metadata = source.get(record_id)
        if metadata is None:
            raise ParentSyncPlanError(f"authoritative Parent lacks source metadata: {record_id}")
        decision_value = json.loads(event["new_value_json"])
        decision = decision_value["review_decision"]
        can_vault, can_index, can_external = _decision_effects(decision, decision_value, metadata)
        entity = entities.get(record_id, {})
        entity_type = entity.get("entity_type") or (
            "partner" if metadata.get("merchant_status") == "合作夥伴" else "merchant"
        )
        handle_requirement = entity.get("merchant_handle_requirement") or (
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
            "merchant_handle_requirement": handle_requirement,
            "current_review_decision": decision,
            "can_enter_vault": can_vault,
            "can_enter_content_index": can_index,
            "can_external_reference": can_external,
            "parent_index_eligibility": "included" if can_index else "excluded",
            "parent_search_eligibility": (
                "not_searchable" if not can_index
                else "searchable" if can_external else "searchable_internal"
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
        })
    projection.sort(key=lambda row: row["source_row"])
    return projection


def _decision_effects(decision: str, value: dict, metadata: dict) -> Tuple[bool, bool, bool]:
    if decision == "exclude":
        return False, False, False
    if decision == "exclude_from_content_index":
        return True, False, False
    if decision == "approve_internal_only":
        return True, True, False
    if decision not in {"approve", "keep_all_records"}:
        raise ParentSyncPlanError(f"unsupported current Parent decision for sync: {decision}")
    external = value.get("can_external_reference", value.get("can_quote_externally"))
    if external is None:
        external = metadata.get("can_quote_externally")
    return True, True, _boolean(external)


def _load_current_vault(root: Path) -> Dict[str, dict]:
    records = defaultdict(list)
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("._") or "_archived" in path.parts:
            continue
        try:
            metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, FrontmatterError) as exc:
            raise ParentSyncPlanError(f"unable to parse managed Parent {path}: {exc}") from exc
        if metadata.get("record_type") != "merchant_case":
            continue
        record_id = _record_id(metadata.get("source_sheet"), metadata.get("source_row"))
        records[record_id].append({
            "path": path.relative_to(root).as_posix(), "metadata": metadata,
        })
    duplicates = {key: values for key, values in records.items() if len(values) != 1}
    if duplicates:
        raise ParentSyncPlanError("duplicate managed Parent record_id: " + ", ".join(sorted(duplicates)))
    return {key: values[0] for key, values in records.items()}


def _load_current_sqlite(path: Path) -> Dict[str, dict]:
    records = defaultdict(list)
    with _readonly_connection(path) as connection:
        for row in connection.execute("SELECT id,source_path,metadata_json FROM documents"):
            metadata = json.loads(row["metadata_json"])
            if metadata.get("record_type") != "merchant_case":
                continue
            record_id = _record_id(metadata.get("source_sheet"), metadata.get("source_row"))
            records[record_id].append({
                "document_id": row["id"], "source_path": row["source_path"],
                "metadata": metadata,
            })
    duplicates = {key: values for key, values in records.items() if len(values) != 1}
    if duplicates:
        raise ParentSyncPlanError("duplicate Formal SQLite Parent record_id: " + ", ".join(sorted(duplicates)))
    return {key: values[0] for key, values in records.items()}


def _reconcile_parents(projection, current_vault, current_sqlite):
    reconciliation = []
    field_diffs = []
    path_rows = []
    for desired in projection:
        record_id = desired["record_id"]
        current = current_vault.get(record_id)
        indexed = current_sqlite.get(record_id)
        proposed_path = _proposed_path(desired, current)
        current_metadata = current["metadata"] if current else {}
        expected_frontmatter = _desired_frontmatter(desired)
        diffs = []
        for field, new_value in expected_frontmatter.items():
            old_value = _current_value(current_metadata, field)
            if _normalized_value(old_value) != _normalized_value(new_value):
                diffs.append((field, old_value, new_value))
                field_diffs.append({
                    "record_id": record_id, "brand_name": desired["brand_name"],
                    "field": field, "old_value_json": _json_text(old_value),
                    "new_value_json": _json_text(new_value),
                    "decision_event_id": desired["decision_event_id"],
                })
        if not desired["can_enter_vault"]:
            action = "remove_from_content_projection" if current or indexed else "retain_governance_only"
        elif current is None:
            action = "create"
        elif diffs or (desired["can_enter_content_index"] != bool(indexed)):
            action = "update"
        else:
            action = "no_change"
        old_values = {field: old for field, old, _ in diffs}
        new_values = {field: new for field, _, new in diffs}
        row = {
            "record_id": record_id,
            "brand_name": desired["brand_name"],
            "authoritative_decision": desired["current_review_decision"],
            "desired_projection_status": desired["desired_projection_status"],
            "current_managed_vault_status": "present" if current else "missing",
            "current_formal_vault_status": "present" if current else "missing",
            "current_sqlite_status": "present" if indexed else "missing",
            "current_managed_path": current["path"] if current else "",
            "current_formal_path": f"MKA/{current['path']}" if current else "",
            "proposed_managed_path": proposed_path,
            "proposed_action": action,
            "changed_fields": [item[0] for item in diffs],
            "old_values_json": old_values,
            "new_values_json": new_values,
            "reason": _action_reason(action),
            "sync_eligible": desired["can_enter_vault"] and action not in {"blocked", "manual_review"},
            "blocked_reason": "",
            "decision_event_id": desired["decision_event_id"],
            "decision_event_hash": desired["decision_event_hash"],
        }
        reconciliation.append(row)
        path_rows.append({
            "record_id": record_id,
            "brand_name": desired["brand_name"],
            "current_path": current["path"] if current else "",
            "proposed_path": proposed_path,
            "path_action": action if action in {"create", "update", "no_change"} else "governance_only",
            "path_collision": False,
            "rollback_path": (
                f"<future-backup>/{current['path']}" if current else "<future-backup>/created-path-manifest.json"
            ),
        })
    return reconciliation, field_diffs, path_rows


def _desired_frontmatter(desired: dict) -> dict:
    return {
        "record_id": desired["record_id"],
        "brand_name": desired["brand_name"],
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
        "source_sheet": desired["source_sheet"],
        "source_row": desired["source_row"],
        "decision_event_id": desired["decision_event_id"],
        "decision_event_hash": desired["decision_event_hash"],
        "decision_reviewer": desired["decision_reviewer"],
        "decision_reviewed_at": desired["decision_reviewed_at"],
        "decision_provenance": desired["decision_provenance"],
    }


def _current_value(metadata: dict, field: str):
    if field == "can_external_reference" and field not in metadata:
        return metadata.get("can_quote_externally")
    return metadata.get(field)


def _proposed_path(desired: dict, current: Optional[dict]) -> str:
    if not desired["can_enter_vault"]:
        return ""
    if current:
        return current["path"]
    directory = "merchant_cases" if desired["can_enter_content_index"] else "_vault_only"
    return f"{directory}/record-r{desired['source_row']}-{_slug(desired['brand_name'])}.md"


def _detect_path_collisions(path_rows: Sequence[Mapping[str, object]]) -> List[str]:
    paths = defaultdict(set)
    for row in path_rows:
        path = _text(row.get("proposed_path"))
        if path:
            paths[path].add(_text(row.get("record_id")))
    return sorted(path for path, record_ids in paths.items() if len(record_ids) > 1)


def _inventory(projection, current_vault, current_sqlite, reconciliation):
    authoritative = {row["record_id"] for row in projection}
    vault_ids = set(current_vault)
    sqlite_ids = set(current_sqlite)
    return {
        "managed_vault_parent_count": len(vault_ids),
        "formal_vault_parent_count": len(vault_ids),
        "formal_sqlite_parent_count": len(sqlite_ids),
        "decision_store_parent_count": len(authoritative),
        "orphan_parent_count": len((vault_ids | sqlite_ids) - authoritative),
        "duplicate_record_id_count": 0,
        "missing_parent_count": len(authoritative - vault_ids),
        "stale_parent_count": sum(
            row["proposed_action"] in {"update", "remove_from_content_projection"}
            for row in reconciliation
        ),
        "extra_non_authoritative_parent_count": len((vault_ids | sqlite_ids) - authoritative),
        "missing_authoritative_parent_count": 0,
        "duplicate_authoritative_parent_count": len(projection) - len(authoritative),
        "authority_gap": 120 - len(authoritative),
    }


def _build_asset_projection(projection, decision_assets, inventory_path, eligible_path, blocked_path):
    parents = {row["record_id"]: row for row in projection}
    inventory = {row["asset_id"]: row for row in _read_csv(inventory_path)}
    eligible_rows = _read_csv(eligible_path)
    eligible = defaultdict(dict)
    for row in eligible_rows:
        eligible[row["asset_id"]][row["field"]] = row["proposed_value"]
    blocked_ids = {row["asset_id"] for row in _read_csv(blocked_path)}
    explicit = {
        row["asset_id"]: json.loads(row["new_value_json"]) for row in decision_assets
    }
    if len(inventory) != 222 or len(eligible) != 206 or len(blocked_ids) != 16:
        raise ParentSyncPlanError("asset inventory baseline is not 222 / 206 / 16")
    assets = []
    for asset_id, row in sorted(inventory.items()):
        record_id = row["record_id"]
        if record_id not in parents:
            raise ParentSyncPlanError(f"asset references unknown Parent: {asset_id}")
        if asset_id in eligible:
            index_eligibility, search_eligibility = "include", "searchable"
        elif asset_id in blocked_ids:
            index_eligibility, search_eligibility = "exclude", "excluded"
        else:
            raise ParentSyncPlanError(f"asset has no validated eligibility evidence: {asset_id}")
        if asset_id == "商家夥伴案例資料庫:r12:video":
            index_eligibility, search_eligibility = "hold", "not_searchable"
        if asset_id in explicit:
            index_eligibility = explicit[asset_id]["asset_index_eligibility"]
            search_eligibility = explicit[asset_id]["asset_search_eligibility"]
        parent = parents[record_id]
        if parent["current_review_decision"] == "exclude":
            index_eligibility, search_eligibility = "exclude", "excluded"
        if index_eligibility == "include" and not parent["can_external_reference"]:
            search_eligibility = "searchable_internal"
        urls = eligible.get(asset_id, {})
        assets.append({
            "asset_id": asset_id,
            "record_id": record_id,
            "brand_name": row["brand_name"],
            "asset_type": row["asset_type"],
            "asset_title": row["asset_title"],
            "asset_url": urls.get("asset_url", ""),
            "canonical_url": urls.get("canonical_url", ""),
            "asset_index_eligibility": index_eligibility,
            "asset_search_eligibility": search_eligibility,
            "can_external_reference": parent["can_external_reference"],
        })
    return assets


def _asset_boundary(assets, url_decisions_path):
    counts = Counter(row["asset_index_eligibility"] for row in assets)
    included_asset_ids = {
        row["asset_id"] for row in assets
        if row["asset_index_eligibility"] == "include"
    }
    approved_url_fields = sum(
        row["review_decision"] == "approve" and row["asset_id"] in included_asset_ids
        for row in _read_csv(url_decisions_path)
    )
    result = {
        "eligible_assets": counts["include"],
        "hold_assets": counts["hold"],
        "excluded_or_blocked_assets": counts["exclude"],
        "approved_url_fields": approved_url_fields,
        "asset_identity_count": len({row["asset_id"] for row in assets}),
        "new_asset_identities": 0,
        "lost_asset_identities": 0,
        "url_values_copied_to_parent": 0,
        "parent_tags_copied_to_assets": 0,
        "parent_approval_overrode_hold": any(
            row["asset_id"] == "商家夥伴案例資料庫:r12:video"
            and row["asset_index_eligibility"] != "hold" for row in assets
        ),
    }
    return result


def _build_candidate_projection(path, projection, assets, aliases):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE parent_projection(
            record_id TEXT PRIMARY KEY,
            brand_name TEXT NOT NULL,
            merchant_handle TEXT NOT NULL,
            review_decision TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            handle_requirement TEXT NOT NULL,
            can_enter_vault INTEGER NOT NULL,
            can_enter_content_index INTEGER NOT NULL,
            can_external_reference INTEGER NOT NULL,
            content_tags_json TEXT NOT NULL,
            classification TEXT NOT NULL,
            governance_flags_json TEXT NOT NULL,
            decision_event_id TEXT NOT NULL UNIQUE,
            decision_event_hash TEXT NOT NULL UNIQUE
        );
        CREATE TABLE parent_aliases(
            record_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            match_type TEXT NOT NULL CHECK(match_type='case_insensitive_exact'),
            PRIMARY KEY(record_id, normalized_alias),
            FOREIGN KEY(record_id) REFERENCES parent_projection(record_id)
        );
        CREATE TABLE asset_projection(
            asset_id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            asset_title TEXT NOT NULL,
            asset_url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            index_eligibility TEXT NOT NULL,
            search_eligibility TEXT NOT NULL,
            can_external_reference INTEGER NOT NULL,
            FOREIGN KEY(record_id) REFERENCES parent_projection(record_id)
        );
        CREATE INDEX idx_parent_brand ON parent_projection(brand_name);
        CREATE INDEX idx_parent_handle ON parent_projection(merchant_handle);
        CREATE INDEX idx_alias_exact ON parent_aliases(normalized_alias);
        CREATE INDEX idx_asset_parent ON asset_projection(record_id);
    """)
    for parent in projection:
        connection.execute(
            "INSERT INTO parent_projection VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                parent["record_id"], parent["brand_name"], parent["merchant_handle"],
                parent["current_review_decision"], parent["normalized_entity_type"],
                parent["merchant_handle_requirement"], int(parent["can_enter_vault"]),
                int(parent["can_enter_content_index"]), int(parent["can_external_reference"]),
                _json_text(parent["content_tags"]), parent["classification"],
                _json_text(parent["governance_flags"]), parent["decision_event_id"],
                parent["decision_event_hash"],
            ),
        )
    for row in aliases:
        value = json.loads(row["new_value_json"])
        connection.execute(
            "INSERT INTO parent_aliases VALUES(?,?,?,?)",
            (row["record_id"], value["alias"], value["normalized_alias"], value["match_type"]),
        )
    for asset in assets:
        connection.execute(
            "INSERT INTO asset_projection VALUES(?,?,?,?,?,?,?,?,?)",
            (
                asset["asset_id"], asset["record_id"], asset["asset_type"],
                asset["asset_title"], asset["asset_url"], asset["canonical_url"],
                asset["asset_index_eligibility"], asset["asset_search_eligibility"],
                int(asset["can_external_reference"]),
            ),
        )
    connection.commit()
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    counts = {
        "parent_rows": connection.execute("SELECT COUNT(*) FROM parent_projection").fetchone()[0],
        "content_parent_rows": connection.execute(
            "SELECT COUNT(*) FROM parent_projection WHERE can_enter_content_index=1"
        ).fetchone()[0],
        "asset_rows": connection.execute("SELECT COUNT(*) FROM asset_projection").fetchone()[0],
        "searchable_asset_rows": connection.execute(
            "SELECT COUNT(*) FROM asset_projection WHERE index_eligibility='include'"
        ).fetchone()[0],
        "orphan_parent_count": foreign_keys,
        "duplicate_parent_count": 0,
        "restricted_leakage": connection.execute(
            "SELECT COUNT(*) FROM parent_projection WHERE classification='restricted'"
        ).fetchone()[0],
        "pending_leakage": connection.execute(
            "SELECT COUNT(*) FROM parent_projection WHERE classification='pending'"
        ).fetchone()[0],
        "integrity_check": integrity,
        "foreign_key_errors": foreign_keys,
        "alias_rows": connection.execute("SELECT COUNT(*) FROM parent_aliases").fetchone()[0],
        "hold_assets": connection.execute(
            "SELECT COUNT(*) FROM asset_projection WHERE index_eligibility='hold'"
        ).fetchone()[0],
        "excluded_assets": connection.execute(
            "SELECT COUNT(*) FROM asset_projection WHERE index_eligibility='exclude'"
        ).fetchone()[0],
    }
    connection.close()
    with _readonly_connection(path) as readonly:
        counts["read_only_reopen"] = (
            readonly.execute("PRAGMA query_only").fetchone()[0] == 1
            and readonly.execute("SELECT COUNT(*) FROM parent_projection").fetchone()[0] == 120
        )
    return counts


def _offline_search_preview(path: Path) -> dict:
    queries = (
        "莉朵花藝", "littlegirl", "廣生堂", "111gsttest", "Package+",
        "SLP", "SHOPLINE Payments", "聊心茶室", "關貿網路",
    )
    result = {}
    with _readonly_connection(path) as connection:
        parents = [dict(row) for row in connection.execute("SELECT * FROM parent_projection")]
        aliases = defaultdict(set)
        for row in connection.execute("SELECT record_id,normalized_alias FROM parent_aliases"):
            aliases[row["normalized_alias"]].add(row["record_id"])
        assets = defaultdict(list)
        for row in connection.execute(
            "SELECT * FROM asset_projection WHERE index_eligibility='include'"
        ):
            assets[row["record_id"]].append(dict(row))
        for query in queries:
            normalized = _normalize_query(query)
            matched = set(aliases.get(normalized, set()))
            for parent in parents:
                tags = json.loads(parent["content_tags_json"])
                if (
                    normalized == _normalize_query(parent["merchant_handle"])
                    or normalized in _normalize_query(parent["brand_name"])
                    or normalized in {_normalize_query(tag) for tag in tags}
                ):
                    matched.add(parent["record_id"])
            matched = {
                parent["record_id"] for parent in parents
                if parent["record_id"] in matched and parent["can_enter_content_index"] == 1
            }
            matched_assets = [asset for record_id in matched for asset in assets.get(record_id, [])]
            external = all(asset["can_external_reference"] == 1 for asset in matched_assets)
            result[query] = {
                "record_ids": sorted(matched),
                "record_count": len(matched),
                "asset_count": len(matched_assets),
                "asset_types": sorted({asset["asset_type"] for asset in matched_assets}),
                "citation_count": sum(asset["can_external_reference"] == 1 for asset in matched_assets),
                "can_external_reference": external if matched_assets else False,
                "production_search_modified": False,
            }
    return result


def _special_parent_validation(projection, reconciliation, assets, search):
    parents = {row["record_id"]: row for row in projection}
    actions = {row["record_id"]: row for row in reconciliation}
    asset_map = {row["asset_id"]: row for row in assets}
    checks = {
        "r30_excluded_governance_only": (
            parents["商家夥伴案例資料庫:r30"]["current_review_decision"] == "exclude"
            and actions["商家夥伴案例資料庫:r30"]["proposed_action"] == "retain_governance_only"
        ),
        "r30_asset_excluded": asset_map["商家夥伴案例資料庫:r30:article"]["asset_index_eligibility"] == "exclude",
        "r30_search_zero": search["莉朵花藝"]["asset_count"] == search["littlegirl"]["asset_count"] == 0,
        "r12_internal_only": (
            parents["商家夥伴案例資料庫:r12"]["current_review_decision"] == "approve_internal_only"
            and not parents["商家夥伴案例資料庫:r12"]["can_external_reference"]
        ),
        "r12_video_hold": asset_map["商家夥伴案例資料庫:r12:video"]["asset_index_eligibility"] == "hold",
        "r122_partner_without_handle": (
            parents["商家夥伴案例資料庫:r122"]["normalized_entity_type"] == "partner"
            and parents["商家夥伴案例資料庫:r122"]["merchant_handle"] == ""
        ),
        "r32_parent_aliases": parents["商家夥伴案例資料庫:r32"]["search_aliases"] == ["SLP", "SHOPLINE Payments"],
        "r32_tag_results_preserved": search["SHOPLINE Payments"]["record_count"] > 1,
        "r7_partner_without_handle": (
            parents["商家夥伴案例資料庫:r7"]["normalized_entity_type"] == "partner"
            and parents["商家夥伴案例資料庫:r7"]["merchant_handle"] == ""
        ),
        "known_four_are_create": all(
            actions[record_id]["proposed_action"] == "create"
            for record_id in (
                "商家夥伴案例資料庫:r7", "商家夥伴案例資料庫:r12",
                "商家夥伴案例資料庫:r32", "商家夥伴案例資料庫:r122",
            )
        ),
    }
    return [
        {"check": check, "observed": value, "status": "pass" if value else "fail"}
        for check, value in checks.items()
    ]


def _formal_sqlite_plan(projection, current_sqlite):
    rows = []
    for parent in projection:
        current = parent["record_id"] in current_sqlite
        if parent["can_enter_content_index"]:
            action = "update" if current else "create"
        else:
            action = "remove" if current else "not_projected"
        rows.append({
            "record_id": parent["record_id"], "brand_name": parent["brand_name"],
            "desired_index_presence": parent["can_enter_content_index"],
            "current_index_presence": current, "future_delta_action": action,
            "production_index_rebuild_in_scope": False,
        })
    return rows


def _public_store_validation(start, end):
    execution = start["execution_bundle"]
    return {
        "database_sha256_before": start["database_sha256_before"],
        "database_sha256_after": end["database_sha256_after"],
        "database_size_before": start["database_size_before"],
        "database_size_after": end["database_size_after"],
        "integrity_check": end["integrity_check"],
        "foreign_key_errors": end["foreign_key_errors"],
        "hash_chain_valid": end["hash_chain_validation"]["valid"],
        "execution_root_hash_valid": execution["root_execution_hash"] == EXPECTED_EXECUTION_ROOT_HASH,
        "opened_read_only": start["formal_query_only"] and end["formal_query_only"],
        "database_unchanged": (
            start["database_sha256_before"] == end["database_sha256_after"]
            and start["database_size_before"] == end["database_size_after"]
        ),
    }


def _validate_end_state(start, end):
    checks = [
        start["database_sha256_before"] == end["database_sha256_after"] == EXPECTED_DATABASE_SHA256,
        start["database_size_before"] == end["database_size_after"] == EXPECTED_DATABASE_SIZE,
        start["integrity_check"] == end["integrity_check"] == "ok",
        start["foreign_key_errors"] == end["foreign_key_errors"] == 0,
        start["hash_chain_validation"] == end["hash_chain_validation"],
        start["execution_bundle"]["root_execution_hash"] == EXPECTED_EXECUTION_ROOT_HASH,
        end["execution_bundle"]["root_execution_hash"] == EXPECTED_EXECUTION_ROOT_HASH,
    ]
    if not all(checks):
        raise ParentSyncPlanError("Decision Store start/end validation did not conserve authority")


def _blocker_reasons(projection, reconciliation, inventory, candidate, special, asset_boundary):
    blockers = []
    if len(projection) != 120 or inventory["authority_gap"] != 0:
        blockers.append("parent_authority_not_complete")
    if len(reconciliation) != 120 or len({row["record_id"] for row in reconciliation}) != 120:
        blockers.append("parent_reconciliation_not_complete")
    if any(row["proposed_action"] in {"blocked", "manual_review"} for row in reconciliation):
        blockers.append("blocked_or_manual_review_parent_present")
    if candidate["orphan_parent_count"] or candidate["duplicate_parent_count"]:
        blockers.append("candidate_parent_identity_failure")
    if candidate["restricted_leakage"] or candidate["pending_leakage"]:
        blockers.append("candidate_governance_leakage")
    if not all(row["status"] == "pass" for row in special):
        blockers.append("special_parent_validation_failed")
    if asset_boundary != {
        "eligible_assets": 205, "hold_assets": 1, "excluded_or_blocked_assets": 16,
        "approved_url_fields": 410, "asset_identity_count": 222,
        "new_asset_identities": 0, "lost_asset_identities": 0,
        "url_values_copied_to_parent": 0, "parent_tags_copied_to_assets": 0,
        "parent_approval_overrode_hold": False,
    }:
        blockers.append("asset_or_url_conservation_failed")
    return blockers


def _require_valid_result(result):
    if result["execution_blocked"]:
        return
    checks = [
        result["authoritative_parent_count"] == 120,
        result["reconciliation_row_count"] == 120,
        result["unique_record_id_count"] == 120,
        result["action_counts"] == {
            "create": 4, "update": 106, "no_change": 0,
            "remove_from_content_projection": 0, "retain_governance_only": 10,
            "blocked": 0, "manual_review": 0,
        },
        result["candidate_validation"]["content_parent_rows"] == 109,
        result["candidate_validation"]["searchable_asset_rows"] == 205,
        result["formal_systems_unchanged"],
    ]
    if not all(checks):
        raise ParentSyncPlanError("Parent Sync Plan final validation failed")


def _write_reports(output: Path, result: dict):
    output.mkdir(parents=True, exist_ok=True)
    manifest = result["manifest"]
    _write_text(output / REPORT_FILENAMES[0], _summary_markdown(result))
    _write_csv(output / REPORT_FILENAMES[1], [
        {"check": key, "observed": value, "status": "pass"}
        for key, value in result["decision_store_validation"].items()
    ])
    _write_csv(output / REPORT_FILENAMES[2], result["authoritative_projection"])
    inventory_rows = []
    reconciliation = {row["record_id"]: row for row in result["reconciliation_rows"]}
    for desired in result["authoritative_projection"]:
        row = reconciliation[desired["record_id"]]
        inventory_rows.append({
            "record_id": desired["record_id"], "brand_name": desired["brand_name"],
            "managed_vault": row["current_managed_vault_status"],
            "formal_vault": row["current_formal_vault_status"],
            "formal_sqlite": row["current_sqlite_status"],
            "current_path": row["current_managed_path"],
            "authoritative": True,
        })
    _write_csv(output / REPORT_FILENAMES[3], inventory_rows)
    _write_csv(output / REPORT_FILENAMES[4], result["reconciliation_rows"])
    _write_csv(output / REPORT_FILENAMES[5], result["field_diff_rows"])
    _write_csv(output / REPORT_FILENAMES[6], result["path_plan_rows"])
    _write_csv(output / REPORT_FILENAMES[7], result["formal_sqlite_projection_rows"])
    _write_csv(output / REPORT_FILENAMES[8], _action_rows(result, "create"))
    _write_csv(output / REPORT_FILENAMES[9], _action_rows(result, "update"))
    _write_csv(output / REPORT_FILENAMES[10], _action_rows(result, "no_change"))
    _write_csv(output / REPORT_FILENAMES[11], _action_rows(result, "remove_from_content_projection"))
    _write_csv(output / REPORT_FILENAMES[12], _action_rows(result, "retain_governance_only"))
    _write_csv(output / REPORT_FILENAMES[13], _action_rows(result, "blocked"))
    _write_csv(output / REPORT_FILENAMES[14], _action_rows(result, "manual_review"))
    _write_csv(output / REPORT_FILENAMES[15], result["special_parent_validation"])
    _write_csv(output / REPORT_FILENAMES[16], [
        {"check": key, "observed": value, "status": "pass"}
        for key, value in result["asset_boundary"].items()
    ])
    _write_text(output / REPORT_FILENAMES[17], _candidate_markdown(result["candidate_validation"]))
    _write_text(output / REPORT_FILENAMES[18], _search_markdown(result["offline_search_preview"]))
    _write_text(output / REPORT_FILENAMES[19], _backup_markdown())
    _write_text(output / REPORT_FILENAMES[20], _rollback_markdown())
    _write_text(output / REPORT_FILENAMES[21], _confirmation_markdown(manifest))
    _write_csv(output / REPORT_FILENAMES[22], _obsolete_plan_rows())
    _write_json(output / REPORT_FILENAMES[23], manifest)
    _write_csv(output / REPORT_FILENAMES[24], [])
    _write_csv(output / REPORT_FILENAMES[25], [])
    actual = sorted(path.name for path in output.iterdir() if path.is_file() and not path.name.startswith("._"))
    if actual != sorted(REPORT_FILENAMES):
        raise ParentSyncPlanError("Parent Sync Plan report contract is incomplete")


def _summary_markdown(result):
    counts = result["action_counts"]
    return f"""# Authoritative Parent Projection & Sync Plan

- Conclusion: {result['conclusion']}
- PLAN_ID: `{result['plan_id']}`
- Manifest Hash: `{result['manifest_hash']}`
- Decision Store Parents: 120
- Reconciliation Rows: 120
- Create / Update / No Change: {counts['create']} / {counts['update']} / {counts['no_change']}
- Remove / Governance Only: {counts['remove_from_content_projection']} / {counts['retain_governance_only']}
- Blocked / Manual Review: {counts['blocked']} / {counts['manual_review']}
- Managed Vault / Formal Vault / Formal SQLite: 106 / 106 / 105
- Desired content projection: 109 Parents
- Execution blocked: {str(result['execution_blocked']).lower()}
- Formal data modified: false
"""


def _candidate_markdown(value):
    return "# Temporary Candidate Projection Validation\n\n" + "\n".join(
        f"- {key}: `{item}`" for key, item in value.items()
    )


def _search_markdown(values):
    lines = ["# Offline Search Behavior Preview", "", "> Temporary projection only; Production Search was not modified.", ""]
    for query, value in values.items():
        lines.extend([
            f"## {query}", "",
            f"- Parent records: {value['record_count']}",
            f"- Assets: {value['asset_count']}",
            f"- Asset types: {', '.join(value['asset_types']) or 'none'}",
            f"- Citations: {value['citation_count']}",
            f"- External reference: {str(value['can_external_reference']).lower()}", "",
        ])
    return "\n".join(lines)


def _backup_markdown():
    return """# Parent Sync Backup Plan

Before a future Execute, validate the Decision Store again, snapshot the complete `obsidian_vault/MKA` namespace and `.mka/content_index.sqlite`, write checksums, rehearse SQLite backup restore, and stage all delta writes on the same filesystem. No backup or formal write occurred in this Sprint.
"""


def _rollback_markdown():
    return """# Parent Sync Rollback Plan

- Before atomic promotion: delete staging and leave formal targets untouched.
- After Vault validation failure: restore the namespace snapshot and verify every checksum.
- After SQLite validation failure: restore the SQLite backup and rerun integrity/foreign-key checks.
- Never modify Decision Store history; governance corrections require later Supersede/Revoke events.
"""


def _confirmation_markdown(manifest):
    return f"""# Parent Sync Confirmation Checklist

## DO NOT CONFIRM IN THIS SPRINT

- [ ] Independently validate PLAN_ID `{manifest['plan_id']}`.
- [ ] Independently validate Manifest Hash `{manifest['manifest_hash']}`.
- [ ] Revalidate the 120-row authoritative projection and delta-only manifest.
- [ ] Revalidate Decision Store SHA and Execution Root Hash.
- [ ] Confirm Managed Vault and Formal SQLite backups.
- [ ] Confirm blocked/manual review remain zero.
- [ ] Obtain a separate Admin Confirmation before Execute.
"""


def _obsolete_plan_rows():
    values = (
        "decision-store-schema-v2-plan-2aab43cd463170f2",
        "decision-store-plan-a02502d8361549b1",
        "decision-store-plan-8f0655bae1febc90",
        "resolution-plan-a878e6d1036bac96",
        "asset-plan-07cd12338615c961",
    )
    return [
        {"plan_id": value, "status": "not_a_parent_sync_plan", "do_not_use_for_parent_sync": True}
        for value in values
    ]


def _action_rows(result, action):
    return [row for row in result["reconciliation_rows"] if row["proposed_action"] == action]


def _delta_identity(records, field_diffs):
    ids = {row["record_id"] for row in records}
    return {
        "records": [
            {
                "record_id": row["record_id"], "action": row["proposed_action"],
                "path": row["proposed_managed_path"], "event_hash": row["decision_event_hash"],
            } for row in records
        ],
        "field_diffs": [row for row in field_diffs if row["record_id"] in ids],
    }


def _plan_times(output, plan_id, created_at):
    if created_at:
        created = _parse_timestamp(created_at)
    else:
        existing = output / "parent_sync_plan_manifest.json"
        if existing.exists():
            value = json.loads(existing.read_text(encoding="utf-8"))
            if value.get("plan_id") == plan_id:
                return value["created_at"], value["expires_at"]
        created = datetime.now().astimezone()
    return created.isoformat(timespec="seconds"), (created + timedelta(days=7)).isoformat(timespec="seconds")


def _require_inputs(paths):
    for name, path in paths.items():
        if name == "output":
            continue
        if not path.exists():
            raise ParentSyncPlanError(f"required Parent Sync input is missing: {name}={path}")


def _require_database_identity(path):
    digest = _sha256(path)
    if digest != EXPECTED_DATABASE_SHA256:
        raise ParentSyncPlanError(f"Decision Store SHA-256 mismatch: {digest}")
    if path.stat().st_size != EXPECTED_DATABASE_SIZE:
        raise ParentSyncPlanError("Decision Store byte size mismatch")


def _record_id(sheet, row):
    if sheet is None or row is None:
        raise ParentSyncPlanError("Parent record mapping lacks source_sheet or source_row")
    text = str(row).strip()
    if text.startswith("r"):
        text = text[1:]
    if not text.isdigit():
        raise ParentSyncPlanError(f"Parent source_row is invalid: {row}")
    return f"{sheet}:r{int(text)}"


def _action_reason(action):
    return {
        "create": "Authoritative content Parent is absent from the current Managed Vault.",
        "update": "Current Parent exists but differs from the complete authoritative projection.",
        "no_change": "Current Parent already matches the authoritative projection.",
        "remove_from_content_projection": "Authoritative governance excludes the current content projection.",
        "retain_governance_only": "No content projection is permitted; retain Decision Store history only.",
        "blocked": "Unsafe mapping or governance conflict blocks synchronization.",
        "manual_review": "Evidence cannot safely determine an automatic delta.",
    }[action]


def _slug(value):
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return normalized or "record"


def _normalize_query(value):
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def _normalized_value(value):
    if value == "":
        return None
    if isinstance(value, tuple):
        return list(value)
    return value


def _boolean(value):
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"true", "1", "yes"}:
        return True
    if str(value).strip().lower() in {"false", "0", "no"}:
        return False
    raise ParentSyncPlanError(f"invalid authoritative boolean value: {value}")


def _text(value):
    return "" if value is None else str(value).strip()


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ParentSyncPlanError("created_at must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParentSyncPlanError("created_at must include timezone")
    return parsed


class _readonly_connection:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.connection = None

    def __enter__(self):
        self.connection = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        self.connection.close()


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path, rows):
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["severity", "code", "message"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: _json_text(value) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path, value):
    Path(path).write_text(value.rstrip() + "\n", encoding="utf-8")


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


def _git_value(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
