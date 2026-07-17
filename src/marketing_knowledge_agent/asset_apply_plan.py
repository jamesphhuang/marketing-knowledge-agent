from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter


ASSET_FIELDS = ("asset_url", "canonical_url")
ASSET_SOURCE_NAME = "MKT 內容產出資料庫_店家 / 夥伴案例 / 對外數據"
ASSET_VAULT_NAMESPACE = Path("MKA/managed/assets")
MANAGED_BY = "marketing-knowledge-agent"
PLAN_SCHEMA_VERSION = "0.1"
PLAN_OUTPUT_FILENAMES = (
    "asset_metadata_apply_plan_summary.md",
    "asset_metadata_apply_manifest.csv",
    "asset_vault_write_plan.csv",
    "asset_sqlite_migration_plan.md",
    "asset_source_record_join_validation.csv",
    "asset_tag_resolution_plan.md",
    "asset_governance_blocked.csv",
    "asset_pre_apply_checksums.json",
    "asset_rollback_execution_plan.md",
    "asset_apply_confirmation_checklist.md",
)


class AssetApplyPlanError(ValueError):
    """Raised when a read-only asset Apply Plan cannot be produced safely."""


def deterministic_asset_filename(asset_id: str) -> str:
    normalized = _text(asset_id)
    if not normalized:
        raise AssetApplyPlanError("asset_id is required for deterministic filename mapping")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() + ".md"


def create_asset_metadata_apply_plan(
    *,
    apply_preview_path: Path,
    blocked_preview_path: Path,
    inventory_path: Path,
    parent_records_path: Path,
    decisions_path: Path,
    validation_dir: Path,
    restricted_customers_path: Path,
    vault_path: Path,
    db_path: Path,
    output_dir: Path,
) -> dict:
    paths = {
        "apply_preview": Path(apply_preview_path),
        "blocked_preview": Path(blocked_preview_path),
        "inventory": Path(inventory_path),
        "parent_records": Path(parent_records_path),
        "decisions": Path(decisions_path),
        "validation": Path(validation_dir),
        "restricted_customers": Path(restricted_customers_path),
        "formal_vault": Path(vault_path),
        "formal_sqlite": Path(db_path),
    }
    output_dir = Path(output_dir)
    _assert_safe_output(output_dir, list(paths.values()))
    for name, path in paths.items():
        if not path.exists():
            raise AssetApplyPlanError(f"required plan input does not exist: {name}")

    before_hashes = {name: _hash_path(path) for name, path in paths.items()}
    apply_rows = _read_csv(paths["apply_preview"])
    blocked_rows = _read_csv(paths["blocked_preview"])
    inventory_rows = _read_csv(paths["inventory"])
    decision_rows = _read_csv(paths["decisions"])
    parent_rows = _read_json_list(paths["parent_records"])
    errors: List[dict] = []

    inventory_by_asset, duplicate_inventory = _unique_rows(
        inventory_rows, "asset_id"
    )
    preview_parents, duplicate_preview_parents = _parent_index(parent_rows)
    vault_parents, duplicate_vault_parents = _read_vault_parents(paths["formal_vault"])
    sqlite_parents, duplicate_sqlite_parents = _read_sqlite_parents(
        paths["formal_sqlite"]
    )
    if duplicate_inventory:
        _issue(errors, "duplicate_inventory_asset", duplicate_inventory)
    if duplicate_preview_parents:
        _issue(errors, "duplicate_preview_parent", duplicate_preview_parents)
    if duplicate_vault_parents:
        _issue(errors, "duplicate_vault_parent", duplicate_vault_parents)
    if duplicate_sqlite_parents:
        _issue(errors, "duplicate_sqlite_parent", duplicate_sqlite_parents)

    apply_by_asset = _validated_apply_rows(
        apply_rows, inventory_by_asset, errors
    )
    blocked_asset_ids = _validated_blocked_rows(blocked_rows, errors)
    _validate_decision_manifest(apply_rows, blocked_rows, decision_rows, errors)
    eligible_asset_ids = set(apply_by_asset)
    if eligible_asset_ids & blocked_asset_ids:
        _issue(
            errors,
            "governance_blocked_in_apply_manifest",
            len(eligible_asset_ids & blocked_asset_ids),
        )
    inventory_ids = set(inventory_by_asset)
    if inventory_ids != eligible_asset_ids | blocked_asset_ids:
        _issue(
            errors,
            "asset_identity_conservation",
            len(inventory_ids ^ (eligible_asset_ids | blocked_asset_ids)),
        )

    joins = []
    tag_resolutions = []
    planned_records = []
    for asset_id in sorted(eligible_asset_ids):
        rows = apply_by_asset[asset_id]
        inventory = inventory_by_asset[asset_id]
        record_id = _text(inventory.get("record_id"))
        preview_parent = preview_parents.get(record_id)
        vault_parent = vault_parents.get(record_id)
        sqlite_parent = sqlite_parents.get(record_id)
        join = _join_row(
            asset_id,
            record_id,
            inventory,
            preview_parent,
            vault_parent,
            sqlite_parent,
        )
        joins.append(join)
        if join["preview_parent_status"] != "unique_match":
            _issue(errors, "missing_preview_parent", 1, asset_id)
        if join["formal_vault_parent_status"] != "unique_match":
            _issue(errors, "missing_formal_vault_parent", 1, asset_id)
        if join["formal_sqlite_parent_status"] != "unique_match":
            _issue(errors, "missing_formal_sqlite_parent", 1, asset_id)
        if join["identity_status"] != "match":
            _issue(errors, "parent_identity_mismatch", 1, asset_id)

        tag_resolutions.append(
            _tag_resolution(asset_id, record_id, vault_parent)
        )
        if preview_parent is not None:
            planned_records.append(
                _planned_record(asset_id, inventory, preview_parent, rows)
            )

    if len(planned_records) != len(eligible_asset_ids):
        _issue(
            errors,
            "planned_asset_conservation",
            len(eligible_asset_ids) - len(planned_records),
        )

    planned_checksums = {
        record["asset_id"]: hashlib.sha256(
            _render_planned_asset_markdown(record).encode("utf-8")
        ).hexdigest()
        for record in planned_records
    }
    vault_rows = _vault_plan_rows(
        planned_records, planned_checksums, paths["formal_vault"]
    )
    manifest_rows = _manifest_rows(apply_by_asset, vault_rows)
    blocked_assets = _blocked_asset_rows(blocked_rows)

    state_payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "input_checksums": before_hashes,
        "planned_record_checksums": planned_checksums,
        "manifest_checksum": _rows_checksum(manifest_rows),
        "blocked_checksum": _rows_checksum(blocked_assets),
        "target_vault_namespace": ASSET_VAULT_NAMESPACE.as_posix(),
        "sqlite_target": str(paths["formal_sqlite"]),
        "expires_on": _plan_expiration(apply_rows),
    }
    plan_state_hash = _canonical_checksum(state_payload)
    plan_id = f"asset-plan-{plan_state_hash[:16]}"

    after_hashes = {name: _hash_path(path) for name, path in paths.items()}
    if before_hashes != after_hashes:
        _issue(errors, "protected_source_modified", 1)

    execution_blocked = bool(errors)
    for row in manifest_rows:
        row["plan_id"] = plan_id
        row["plan_state_hash"] = plan_state_hash
        row["execution_ready"] = str(not execution_blocked).lower()
    for row in vault_rows:
        row["plan_id"] = plan_id
        row["plan_state_hash"] = plan_state_hash
        row["execution_ready"] = str(not execution_blocked).lower()

    tag_counts = Counter(row["resolution_status"] for row in tag_resolutions)
    summary = {
        "conclusion": (
            "C. Not ready for Apply"
            if execution_blocked
            else "A. Ready for human confirmation"
        ),
        "plan_only": True,
        "plan_id": plan_id,
        "plan_state_hash": plan_state_hash,
        "expires_on": state_payload["expires_on"],
        "execution_blocked": execution_blocked,
        "error_count": len(errors),
        "error_codes": dict(
            sorted(Counter(item["code"] for item in errors).items())
        ),
        "inventory_asset_count": len(inventory_by_asset),
        "planned_asset_count": len(planned_records),
        "planned_url_field_count": len(manifest_rows),
        "governance_blocked_asset_count": len(blocked_asset_ids),
        "governance_blocked_field_count": len(blocked_rows),
        "new_asset_identity_count": len(eligible_asset_ids - inventory_ids),
        "lost_asset_identity_count": len(inventory_ids - eligible_asset_ids - blocked_asset_ids),
        "record_id_change_count": sum(
            row["identity_status"] == "mismatch" for row in joins
        ),
        "duplicate_preview_parent_count": duplicate_preview_parents,
        "duplicate_vault_parent_count": duplicate_vault_parents,
        "duplicate_sqlite_parent_count": duplicate_sqlite_parents,
        "preview_parent_join_count": sum(
            row["preview_parent_status"] == "unique_match" for row in joins
        ),
        "formal_vault_parent_join_count": sum(
            row["formal_vault_parent_status"] == "unique_match" for row in joins
        ),
        "formal_sqlite_parent_join_count": sum(
            row["formal_sqlite_parent_status"] == "unique_match" for row in joins
        ),
        "formal_vault_parent_missing_count": sum(
            row["formal_vault_parent_status"] != "unique_match" for row in joins
        ),
        "formal_sqlite_parent_missing_count": sum(
            row["formal_sqlite_parent_status"] != "unique_match" for row in joins
        ),
        "formal_parent_missing_record_count": len(
            {
                row["record_id"]
                for row in joins
                if row["formal_vault_parent_status"] != "unique_match"
                or row["formal_sqlite_parent_status"] != "unique_match"
            }
        ),
        "tag_resolved_count": tag_counts["resolved_from_parent"],
        "tag_governance_blocked_count": tag_counts["governance_blocked"],
        "tag_blank_count": tag_counts["empty_omit"],
        "tag_missing_parent_count": tag_counts["missing_parent"],
        "source_files_modified": before_hashes != after_hashes,
        "formal_vault_modified": False,
        "formal_sqlite_modified": False,
        "decisions_modified": False,
        "query_constraints_enabled": [],
    }
    checksum_payload = {
        **state_payload,
        "plan_id": plan_id,
        "plan_state_hash": plan_state_hash,
        "formal_targets": {
            "vault_namespace": ASSET_VAULT_NAMESPACE.as_posix(),
            "sqlite": str(paths["formal_sqlite"]),
        },
        "target_vault_checksums": {
            row["target_vault_path"]: row["current_file_checksum"]
            for row in vault_rows
        },
        "confirmation_required": True,
        "execution_enabled_in_this_sprint": False,
    }
    _write_reports(
        output_dir,
        summary,
        manifest_rows,
        vault_rows,
        joins,
        tag_resolutions,
        blocked_assets,
        checksum_payload,
        errors,
    )
    final_hashes = {name: _hash_path(path) for name, path in paths.items()}
    if final_hashes != before_hashes:
        raise AssetApplyPlanError(
            "a protected source changed while producing the read-only Apply Plan"
        )
    return summary


def reject_unimplemented_apply_stage(stage: str, plan_id: str) -> None:
    if stage not in {"confirm", "execute"}:
        raise AssetApplyPlanError("unknown asset Apply stage")
    if not _text(plan_id):
        raise AssetApplyPlanError(f"{stage} requires a PLAN_ID")
    raise AssetApplyPlanError(
        f"asset metadata {stage} is not enabled in this plan-only sprint"
    )


def _validated_apply_rows(
    rows: Sequence[dict], inventory_by_asset: Mapping[str, dict], errors: List[dict]
) -> Dict[str, Dict[str, dict]]:
    grouped: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for row in rows:
        asset_id = _text(row.get("asset_id"))
        field = _text(row.get("field"))
        if not asset_id or field not in ASSET_FIELDS:
            _issue(errors, "invalid_apply_manifest_key", 1)
            continue
        if field in grouped[asset_id]:
            _issue(errors, "duplicate_apply_field", 1, asset_id)
            continue
        inventory = inventory_by_asset.get(asset_id)
        if inventory is None:
            _issue(errors, "unknown_apply_asset", 1, asset_id)
            continue
        if _text(row.get("record_id")) != _text(inventory.get("record_id")):
            _issue(errors, "apply_record_identity_mismatch", 1, asset_id)
        if _text(row.get("review_decision")) != "approve":
            _issue(errors, "unapproved_apply_field", 1, asset_id)
        if _text(row.get("eligibility")) != "ready_for_apply_preview":
            _issue(errors, "ineligible_apply_field", 1, asset_id)
        if _text(row.get("governance_status")) != "eligible":
            _issue(errors, "governance_ineligible_apply_field", 1, asset_id)
        if not _text(row.get("proposed_value")):
            _issue(errors, "empty_apply_value", 1, asset_id)
        grouped[asset_id][field] = row
    for asset_id, fields in grouped.items():
        if set(fields) != set(ASSET_FIELDS):
            _issue(errors, "incomplete_asset_url_fields", 1, asset_id)
    return dict(grouped)


def _validated_blocked_rows(rows: Sequence[dict], errors: List[dict]) -> set:
    grouped: Dict[str, set] = defaultdict(set)
    for row in rows:
        asset_id = _text(row.get("asset_id"))
        field = _text(row.get("field"))
        if not asset_id or field not in ASSET_FIELDS:
            _issue(errors, "invalid_blocked_manifest_key", 1)
            continue
        if _text(row.get("governance_status")) != "blocked":
            _issue(errors, "blocked_row_not_governance_blocked", 1, asset_id)
        grouped[asset_id].add(field)
    for asset_id, fields in grouped.items():
        if fields != set(ASSET_FIELDS):
            _issue(errors, "incomplete_blocked_asset_fields", 1, asset_id)
    return set(grouped)


def _validate_decision_manifest(
    apply_rows: Sequence[dict],
    blocked_rows: Sequence[dict],
    decision_rows: Sequence[dict],
    errors: List[dict],
) -> None:
    decision_index = {}
    for row in decision_rows:
        field = _text(row.get("field"))
        if field not in ASSET_FIELDS:
            continue
        key = (
            _text(row.get("record_id")),
            _text(row.get("asset_id")),
            field,
        )
        if key in decision_index:
            _issue(errors, "duplicate_source_decision", 1, key[1])
        else:
            decision_index[key] = row
    expected_keys = set()
    for row in [*apply_rows, *blocked_rows]:
        key = (
            _text(row.get("record_id")),
            _text(row.get("asset_id")),
            _text(row.get("field")),
        )
        expected_keys.add(key)
        decision = decision_index.get(key)
        if decision is None:
            _issue(errors, "missing_source_decision", 1, key[1])
            continue
        for field in (
            "proposed_value",
            "review_decision",
            "reviewer",
            "reviewed_at",
        ):
            if _text(decision.get(field)) != _text(row.get(field)):
                _issue(errors, "decision_manifest_mismatch", 1, key[1])
                break
    if set(decision_index) != expected_keys:
        _issue(
            errors,
            "decision_coverage_mismatch",
            len(set(decision_index) ^ expected_keys),
        )


def _plan_expiration(rows: Sequence[dict]) -> str:
    reviewed_dates = []
    for row in rows:
        value = _text(row.get("reviewed_at"))
        try:
            reviewed_dates.append(datetime.fromisoformat(value).date())
        except ValueError as exc:
            raise AssetApplyPlanError(
                "reviewed_at must remain ISO-valid before Apply planning"
            ) from exc
    if not reviewed_dates:
        raise AssetApplyPlanError("Apply Plan requires reviewed URL decisions")
    return (max(reviewed_dates) + timedelta(days=30)).isoformat()


def _planned_record(
    asset_id: str,
    inventory: Mapping[str, object],
    parent: Mapping[str, object],
    rows: Mapping[str, dict],
) -> dict:
    reviewer_values = {_text(row.get("reviewer")) for row in rows.values()}
    reviewed_values = {_text(row.get("reviewed_at")) for row in rows.values()}
    if "" in reviewer_values or len(reviewer_values) != 1:
        raise AssetApplyPlanError(f"reviewer mismatch for asset: {asset_id}")
    if "" in reviewed_values or len(reviewed_values) != 1:
        raise AssetApplyPlanError(f"reviewed_at mismatch for asset: {asset_id}")
    record_id = _text(inventory.get("record_id"))
    provenance = {
        field: {
            "source": _text(row.get("provenance")),
            "source_location": _text(row.get("source_location")),
            "decision": "approve",
        }
        for field, row in sorted(rows.items())
    }
    return {
        "record_type": "asset_metadata",
        "asset_id": asset_id,
        "record_id": record_id,
        "brand_name": _text(parent.get("brand_name")),
        "merchant_handle": _text(parent.get("merchant_handle")),
        "asset_type": _text(inventory.get("asset_type")),
        "asset_title": _text(inventory.get("asset_title")),
        "asset_url": _text(rows["asset_url"].get("proposed_value")),
        "canonical_url": _text(rows["canonical_url"].get("proposed_value")),
        "source": ASSET_SOURCE_NAME,
        "source_location": record_id,
        "provenance": provenance,
        "reviewed_by": next(iter(reviewer_values)),
        "reviewed_at": next(iter(reviewed_values)),
        "review_decision": "approve",
        "governance_eligibility": "ready_for_apply_preview",
        "managed_by": MANAGED_BY,
    }


def _join_row(
    asset_id: str,
    record_id: str,
    inventory: Mapping[str, object],
    preview_parent: Optional[dict],
    vault_parent: Optional[dict],
    sqlite_parent: Optional[dict],
) -> dict:
    identity_values = {
        "preview": _parent_identity(preview_parent),
        "vault": _parent_identity(vault_parent),
        "sqlite": _parent_identity(sqlite_parent),
    }
    present = [value for value in identity_values.values() if value is not None]
    identity_match = bool(present) and all(value == present[0] for value in present)
    inventory_match = (
        preview_parent is not None
        and record_id == _text(inventory.get("record_id"))
    )
    return {
        "asset_id": asset_id,
        "record_id": record_id,
        "preview_parent_status": "unique_match" if preview_parent else "missing",
        "formal_vault_parent_status": "unique_match" if vault_parent else "missing",
        "formal_sqlite_parent_status": "unique_match" if sqlite_parent else "missing",
        "identity_status": "match" if identity_match and inventory_match else "mismatch",
        "reason": (
            "record_id and parent identity agree across all present sources"
            if identity_match and inventory_match
            else "formal parent is missing or parent identity differs"
        ),
    }


def _tag_resolution(asset_id: str, record_id: str, parent: Optional[dict]) -> dict:
    if parent is None:
        status = "missing_parent"
        tags: List[str] = []
        reason = "formal managed parent record is missing; fail closed"
    elif not _bool(parent.get("can_enter_content_index")) or not _bool(
        parent.get("can_quote_externally")
    ):
        status = "governance_blocked"
        tags = []
        reason = "parent governance does not allow external tag exposure"
    else:
        tags = _string_list(parent.get("content_tags"))
        status = "resolved_from_parent" if tags else "empty_omit"
        reason = (
            "exact content_tags from formal managed parent"
            if tags
            else "parent content_tags is blank; renderer must omit the line"
        )
    return {
        "asset_id": asset_id,
        "record_id": record_id,
        "resolution_status": status,
        "content_tags": tags,
        "reason": reason,
    }


def _vault_plan_rows(
    records: Sequence[dict], checksums: Mapping[str, str], vault_path: Path
) -> List[dict]:
    rows = []
    for record in sorted(records, key=lambda item: item["asset_id"]):
        relative_path = (
            ASSET_VAULT_NAMESPACE / deterministic_asset_filename(record["asset_id"])
        ).as_posix()
        target = Path(vault_path) / relative_path
        current_checksum = _hash_path(target) if target.is_file() else "missing"
        planned_checksum = checksums[record["asset_id"]]
        action = (
            "will_add"
            if current_checksum == "missing"
            else "unchanged"
            if current_checksum == planned_checksum
            else "will_update"
        )
        rows.append(
            {
                "plan_id": "",
                "plan_state_hash": "",
                "asset_id": record["asset_id"],
                "record_id": record["record_id"],
                "target_vault_path": relative_path,
                "filename_mapping": "sha256(asset_id)",
                "action": action,
                "current_file_checksum": current_checksum,
                "planned_file_checksum": planned_checksum,
                "proposed_record_json": json.dumps(
                    record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                "execution_ready": "",
            }
        )
    return rows


def _manifest_rows(
    apply_by_asset: Mapping[str, Mapping[str, dict]], vault_rows: Sequence[dict]
) -> List[dict]:
    vault_by_asset = {row["asset_id"]: row for row in vault_rows}
    result = []
    for asset_id, fields in sorted(apply_by_asset.items()):
        target = vault_by_asset.get(asset_id, {})
        for field, row in sorted(fields.items()):
            result.append(
                {
                    "plan_id": "",
                    "plan_state_hash": "",
                    "record_id": _text(row.get("record_id")),
                    "asset_id": asset_id,
                    "field": field,
                    "current_value": _text(row.get("current_value")),
                    "approved_value": _text(row.get("proposed_value")),
                    "review_decision": _text(row.get("review_decision")),
                    "reviewer": _text(row.get("reviewer")),
                    "reviewed_at": _text(row.get("reviewed_at")),
                    "target_vault_path": _text(target.get("target_vault_path")),
                    "target_record_checksum": _text(
                        target.get("planned_file_checksum")
                    ),
                    "execution_ready": "",
                }
            )
    return result


def _blocked_asset_rows(rows: Sequence[dict]) -> List[dict]:
    grouped: Dict[str, dict] = {}
    for row in rows:
        asset_id = _text(row.get("asset_id"))
        grouped.setdefault(
            asset_id,
            {
                "record_id": _text(row.get("record_id")),
                "asset_id": asset_id,
                "asset_type": _text(row.get("asset_type")),
                "blocked_field_count": 0,
                "governance_status": "blocked",
                "excluded_from_manifest": "true",
                "reason": "Governance-blocked asset is excluded before Apply planning.",
            },
        )
        grouped[asset_id]["blocked_field_count"] += 1
    return [grouped[key] for key in sorted(grouped)]


def _parent_index(rows: Sequence[dict]) -> Tuple[Dict[str, dict], int]:
    result: Dict[str, dict] = {}
    duplicate_count = 0
    for row in rows:
        if _text(row.get("record_type")) != "merchant_case":
            continue
        record_id = _record_id(row)
        if not record_id:
            continue
        if record_id in result:
            duplicate_count += 1
        else:
            result[record_id] = row
    return result, duplicate_count


def _read_vault_parents(vault_path: Path) -> Tuple[Dict[str, dict], int]:
    rows = []
    for path in sorted(Path(vault_path).rglob("*.md")):
        if path.name.startswith("._") or not path.is_file():
            continue
        try:
            metadata, _ = parse_markdown_with_frontmatter(
                path.read_text(encoding="utf-8")
            )
        except (UnicodeDecodeError, FrontmatterError):
            continue
        if _text(metadata.get("managed_by")) != MANAGED_BY:
            continue
        rows.append(metadata)
    return _parent_index(rows)


def _read_sqlite_parents(db_path: Path) -> Tuple[Dict[str, dict], int]:
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute("SELECT metadata_json FROM documents").fetchall()
    except sqlite3.Error as exc:
        raise AssetApplyPlanError(f"unable to read formal SQLite metadata: {exc}") from exc
    metadata_rows = []
    for (payload,) in rows:
        try:
            metadata_rows.append(json.loads(payload))
        except (TypeError, json.JSONDecodeError) as exc:
            raise AssetApplyPlanError("formal SQLite contains invalid metadata JSON") from exc
    return _parent_index(metadata_rows)


def _unique_rows(rows: Sequence[dict], key: str) -> Tuple[Dict[str, dict], int]:
    result = {}
    duplicates = 0
    for row in rows:
        value = _text(row.get(key))
        if not value:
            continue
        if value in result:
            duplicates += 1
        else:
            result[value] = row
    return result, duplicates


def _parent_identity(parent: Optional[Mapping[str, object]]) -> Optional[Tuple[str, str]]:
    if parent is None:
        return None
    return (_text(parent.get("brand_name")), _text(parent.get("merchant_handle")))


def _record_id(row: Mapping[str, object]) -> str:
    sheet = _text(row.get("source_sheet"))
    source_row = _text(row.get("source_row"))
    if not sheet or not source_row:
        return ""
    if source_row.endswith(".0"):
        source_row = source_row[:-2]
    return f"{sheet}:r{source_row}"


def _render_planned_asset_markdown(record: Mapping[str, object]) -> str:
    lines = ["---"]
    for key, value in record.items():
        if key == "provenance":
            value = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        text = _text(value)
        if "\n" in text or "\r" in text:
            raise AssetApplyPlanError(
                f"planned asset metadata contains a literal newline: {key}"
            )
        lines.append(f"{key}: '{text}'")
    lines.extend(
        ["---", "", f"# {_text(record.get('asset_title'))}", "", "Managed asset metadata record.", ""]
    )
    return "\n".join(lines)


def _write_reports(
    output_dir: Path,
    summary: Mapping[str, object],
    manifest_rows: Sequence[dict],
    vault_rows: Sequence[dict],
    joins: Sequence[dict],
    tag_resolutions: Sequence[dict],
    blocked_assets: Sequence[dict],
    checksum_payload: Mapping[str, object],
    errors: Sequence[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / PLAN_OUTPUT_FILENAMES[1], manifest_rows)
    _write_csv(output_dir / PLAN_OUTPUT_FILENAMES[2], vault_rows)
    _write_csv(output_dir / PLAN_OUTPUT_FILENAMES[4], joins)
    _write_csv(output_dir / PLAN_OUTPUT_FILENAMES[6], blocked_assets)
    (output_dir / PLAN_OUTPUT_FILENAMES[0]).write_text(
        _render_summary(summary, errors), encoding="utf-8"
    )
    (output_dir / PLAN_OUTPUT_FILENAMES[3]).write_text(
        _render_sqlite_plan(summary), encoding="utf-8"
    )
    (output_dir / PLAN_OUTPUT_FILENAMES[5]).write_text(
        _render_tag_plan(tag_resolutions), encoding="utf-8"
    )
    (output_dir / PLAN_OUTPUT_FILENAMES[7]).write_text(
        json.dumps(checksum_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (output_dir / PLAN_OUTPUT_FILENAMES[8]).write_text(
        _render_rollback(summary), encoding="utf-8"
    )
    (output_dir / PLAN_OUTPUT_FILENAMES[9]).write_text(
        _render_confirmation(summary), encoding="utf-8"
    )


def _render_summary(summary: Mapping[str, object], errors: Sequence[dict]) -> str:
    lines = [
        "# Asset Metadata Apply Plan",
        "",
        "> PLAN ONLY. No confirm or execute action ran; formal Vault and SQLite remain unchanged.",
        "",
        f"- Conclusion: **{summary['conclusion']}**",
        f"- PLAN_ID: `{summary['plan_id']}`",
        f"- Plan state hash: `{summary['plan_state_hash']}`",
        f"- Plan expires on: `{summary['expires_on']}`",
        f"- Execution blocked: {str(summary['execution_blocked']).lower()}",
        f"- Planned managed asset files: {summary['planned_asset_count']}",
        f"- Approved URL fields in manifest: {summary['planned_url_field_count']}",
        f"- Governance-blocked assets excluded: {summary['governance_blocked_asset_count']}",
        "",
        "## Conservation",
        "",
        f"- Inventory assets: {summary['inventory_asset_count']}",
        f"- New asset identities: {summary['new_asset_identity_count']}",
        f"- Lost asset identities: {summary['lost_asset_identity_count']}",
        f"- record_id changes: {summary['record_id_change_count']}",
        f"- Preview parent joins: {summary['preview_parent_join_count']}",
        f"- Formal Vault parent joins: {summary['formal_vault_parent_join_count']}",
        f"- Formal SQLite parent joins: {summary['formal_sqlite_parent_join_count']}",
        f"- Formal Vault parent missing: {summary['formal_vault_parent_missing_count']}",
        f"- Formal SQLite parent missing: {summary['formal_sqlite_parent_missing_count']}",
        f"- Unique formal parent records missing: {summary['formal_parent_missing_record_count']}",
        "",
        "## Tag Resolution",
        "",
        f"- Resolved from formal parent: {summary['tag_resolved_count']}",
        f"- Parent governance blocked: {summary['tag_governance_blocked_count']}",
        f"- Parent tags blank (omit): {summary['tag_blank_count']}",
        f"- Formal parent missing: {summary['tag_missing_parent_count']}",
        "- `content_tags` is never copied into an asset record and is never inferred from title/body.",
        "",
        "## Blocking Diagnostics",
        "",
    ]
    if errors:
        for code, count in sorted(Counter(item["code"] for item in errors).items()):
            lines.append(f"- `{code}`: {count}")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Formal Vault writes: 0",
            "- Formal SQLite writes: 0",
            "- Decision changes: 0",
            "- Query constraints enabled: 0",
            "- Confirm executed: no",
            "- Execute executed: no",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_sqlite_plan(summary: Mapping[str, object]) -> str:
    return f"""# Asset SQLite Migration Plan

> Design only. The formal database was opened read-only; no table was created.

## Candidate Migration

Run only during a future confirmed execute against a temporary copy of the formal database:

```sql
ALTER TABLE documents ADD COLUMN source_record_id TEXT;
CREATE UNIQUE INDEX idx_documents_source_record_id
    ON documents(source_record_id)
    WHERE source_record_id IS NOT NULL;

CREATE TABLE content_assets (
    asset_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    merchant_handle TEXT,
    asset_type TEXT NOT NULL,
    asset_title TEXT NOT NULL,
    asset_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source TEXT NOT NULL,
    source_location TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    review_decision TEXT NOT NULL CHECK (review_decision = 'approve'),
    governance_eligibility TEXT NOT NULL,
    FOREIGN KEY(record_id) REFERENCES documents(source_record_id)
);
CREATE INDEX idx_content_assets_record_id ON content_assets(record_id);
CREATE INDEX idx_content_assets_type ON content_assets(asset_type);
```

Populate `documents.source_record_id` only by parsing each document's existing `source_sheet` and `source_row`. Duplicate or missing values fail closed. Do not parse record identity from filenames.

## Join Contract

`content_assets.record_id -> documents.source_record_id` is a required many-assets-to-one-source-record join. Asset identity fields must match the formal managed parent. `content_tags` stays in parent `documents.metadata_json`; it is not copied into `content_assets`.

Future external rendering may resolve tags only after both asset and parent governance checks pass. A missing parent produces no tags and blocks the batch.

## Candidate Build And Atomic Swap

1. Stop readers and acquire an exclusive Apply lock.
2. Copy the formal DB to `.mka/asset_apply_staging/<PLAN_ID>/content_index.candidate.sqlite` and verify the backup checksum.
3. Apply the schema and insert exactly {summary['planned_asset_count']} candidate asset rows.
4. Run foreign-key, identity, governance, URL-field and citation assertions.
5. Reopen the candidate read-only and rerun conservation tests.
6. Rename the live DB to its batch backup, then atomically replace it with the candidate on the same filesystem.
7. On any failure, restore the exact pre-apply DB checksum.

Current formal parent join gaps: Vault={summary['formal_vault_parent_missing_count']}, SQLite={summary['formal_sqlite_parent_missing_count']}. Any non-zero value blocks confirmation.
"""


def _render_tag_plan(rows: Sequence[dict]) -> str:
    lines = [
        "# Asset Tag Resolution Plan",
        "",
        "> Tags are lookup-only parent metadata. They are not copied into asset records.",
        "",
        "Format: `asset_id | resolution_status | content_tags_json | reason`",
        "",
    ]
    for row in rows:
        tags = json.dumps(row["content_tags"], ensure_ascii=False, separators=(",", ":"))
        lines.append(
            f"- {row['asset_id']} | {row['resolution_status']} | {tags} | {row['reason']}"
        )
    return "\n".join(lines) + "\n"


def _render_rollback(summary: Mapping[str, object]) -> str:
    return f"""# Asset Apply Rollback Execution Plan

> Plan only. Nothing currently requires rollback because no formal write occurred.

## Pre-Apply Backup Manifest

- PLAN_ID: `{summary['plan_id']}`
- Back up the complete live `MKA/managed/assets/` namespace, including an explicit absent-directory marker.
- Back up the formal SQLite database and record its SHA-256 before any candidate mutation.
- Preserve the approved decision CSV, Apply Preview, blocked preview and all input checksums as immutable batch evidence.
- Rollback coverage must include all {summary['planned_asset_count']} planned asset records and all {summary['planned_url_field_count']} approved URL fields.

## Execute Sequence

1. Recompute every input, live target and decision checksum; reject stale or expired plans.
2. Build candidate Vault files under `obsidian_vault/MKA/.asset_apply_staging/<PLAN_ID>/managed/assets/` on the same filesystem, without changing `MKA/managed/assets/`, then reparse every file.
3. Build `.mka/asset_apply_staging/<PLAN_ID>/content_index.candidate.sqlite` and run all assertions.
4. Acquire an exclusive lock; rename live targets to batch backups.
5. Atomically swap staged Vault assets and the candidate DB into place.
6. Reopen and verify record, URL-field, parent-join, governance and checksum conservation.

## Automatic Rollback

If any write, parse, checksum, join, governance, citation or post-swap assertion fails, stop readers, restore the backed-up Vault namespace and SQLite file, verify their original checksums, mark the batch `aborted_and_restored`, and retain the failure manifest. Never delete a previous asset record without a manifest-backed archive.
"""


def _render_confirmation(summary: Mapping[str, object]) -> str:
    blocked = bool(summary["execution_blocked"])
    heading = "DO NOT CONFIRM" if blocked else "Human Confirmation Required"
    return f"""# Asset Apply Confirmation Checklist

## {heading}

- PLAN_ID: `{summary['plan_id']}`
- Plan state hash: `{summary['plan_state_hash']}`
- Plan expires on: `{summary['expires_on']}`
- Expected managed asset files: {summary['planned_asset_count']}
- Expected approved URL fields: {summary['planned_url_field_count']}
- Expected governance-blocked exclusions: {summary['governance_blocked_asset_count']}
- Execution blocked by plan diagnostics: {str(blocked).lower()}

## Future `confirm` Contract (not implemented in this sprint)

Confirmation must record all of the following without changing formal data:

- exact PLAN_ID and plan state hash;
- confirmer identity and ISO confirmation timestamp;
- exact Vault root and `MKA/managed/assets/` namespace;
- exact formal SQLite path;
- approved backup root on the same filesystem;
- explicit acknowledgements of {summary['planned_asset_count']} assets, {summary['planned_url_field_count']} URL fields and {summary['governance_blocked_asset_count']} blocked exclusions;
- confirmation that Slack readers are stopped for the future atomic swap;
- confirmation that no unsupported query constraint is activated.

## Future `execute` Contract (not implemented in this sprint)

`execute` must require a persisted confirmation artifact matching PLAN_ID and plan state hash. It must recompute all checksums, reject drift/expiry, create backups, build temporary Vault/SQLite candidates, pass all safety assertions, then perform atomic swaps. There is no `--skip-confirm`, implicit Apply or default execute path.
"""


def _write_csv(path: Path, rows: Sequence[dict]) -> None:
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["record_id", "asset_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _safe_csv_value(row.get(key)) for key in fieldnames})


def _safe_csv_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "[unsafe input redacted]"
    return text


def _read_csv(path: Path) -> List[dict]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise AssetApplyPlanError(f"CSV has no header: {path}")
            return list(reader)
    except UnicodeDecodeError as exc:
        raise AssetApplyPlanError(f"CSV is not valid UTF-8: {path}") from exc


def _read_json_list(path: Path) -> List[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetApplyPlanError(f"JSON input is invalid: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise AssetApplyPlanError(f"JSON input must be an array of objects: {path}")
    return payload


def _assert_safe_output(output_dir: Path, protected_paths: Sequence[Path]) -> None:
    output = output_dir.resolve()
    for path in protected_paths:
        protected = Path(path).resolve()
        if output == protected or output in protected.parents or protected in output.parents:
            raise AssetApplyPlanError("plan output must be separate from every protected source")
    lowered_parts = {part.casefold() for part in output.parts}
    if ".mka" in lowered_parts or "obsidian_vault" in lowered_parts:
        raise AssetApplyPlanError("plan output cannot be inside formal Vault or .mka")


def _hash_path(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return "missing"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if not child.is_file() or child.name.startswith("._"):
            continue
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def _rows_checksum(rows: Sequence[dict]) -> str:
    return _canonical_checksum(list(rows))


def _canonical_checksum(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _issue(errors: List[dict], code: str, count: int, asset_id: str = "") -> None:
    errors.append({"code": code, "count": count, "asset_id": asset_id})


def _string_list(value: object) -> List[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [item.strip() for item in value.split("|")]
        values = decoded if isinstance(decoded, list) else []
    else:
        values = []
    result = []
    for item in values:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).casefold() in {"true", "1", "yes"}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
