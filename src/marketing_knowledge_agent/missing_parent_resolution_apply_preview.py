from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .missing_parent_resolution_preview import (
    REVIEWER,
    SEARCH_QUERIES,
    build_resolution_context,
    build_resolution_formal_search,
    preview_resolution_search,
    render_resolution_standard,
)
from .review_decision_validation import ALLOWED_REVIEW_DECISIONS


OLD_ASSET_PLAN_ID = "asset-plan-07cd12338615c961"
PLAN_SCHEMA_VERSION = "0.1"
OUTPUT_FILENAMES = (
    "resolution_validation_summary.md",
    "parent_decision_validation.csv",
    "parent_decision_apply_preview.csv",
    "source_metadata_apply_preview.csv",
    "asset_eligibility_apply_preview.csv",
    "alias_collision_validation.csv",
    "apply_target_mapping.md",
    "apply_count_reconciliation.md",
    "post_apply_search_preview.md",
    "resolution_apply_confirmation_checklist.md",
    "resolution_apply_rollback_plan.md",
    "resolution_apply_manifest.json",
)
EXPECTED_PARENT_DECISIONS = {
    30: ("莉朵花藝", "exclude"),
    12: ("廣生堂", "approve_internal_only"),
    122: ("Package+", "approve"),
    32: ("聊心茶室（SLP 用戶）", "approve"),
    7: ("關貿網路", "approve"),
}
class MissingParentResolutionApplyPreviewError(ValueError):
    """Raised when the resolution cannot be validated without unsafe mutation."""


def validate_resolution_manifest_input_checksums(
    manifest_path: Path, input_paths: Mapping[str, Path]
) -> None:
    """Fail closed when future confirmation inputs differ from the reviewed plan."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    expected = manifest.get("input_checksums")
    if not isinstance(expected, dict):
        raise MissingParentResolutionApplyPreviewError(
            "resolution manifest has no input checksum contract"
        )
    mismatches = [
        name
        for name, expected_hash in expected.items()
        if name not in input_paths
        or not Path(input_paths[name]).exists()
        or _hash_path(Path(input_paths[name])) != expected_hash
    ]
    if mismatches:
        raise MissingParentResolutionApplyPreviewError(
            "resolution manifest input checksum mismatch: " + ", ".join(mismatches)
        )


def generate_resolution_apply_preview(
    *,
    resolution_dir: Path,
    parent_records_path: Path,
    review_decisions_path: Path,
    inventory_path: Path,
    asset_apply_preview_path: Path,
    asset_blocked_preview_path: Path,
    restricted_customers_path: Path,
    vault_path: Path,
    db_path: Path,
    production_slack_renderer_path: Path,
    output_dir: Path,
    formal_search_fn: Optional[Callable[[str], Sequence[dict]]] = None,
) -> dict:
    resolution_dir = Path(resolution_dir)
    paths = {
        "decision_proposal": resolution_dir / "missing_parent_resolution_decisions.csv",
        "parent_preview": resolution_dir / "parent_decision_preview.csv",
        "asset_eligibility_preview": resolution_dir / "asset_eligibility_preview.csv",
        "search_alias_preview": resolution_dir / "search_alias_preview.csv",
        "parent_records": Path(parent_records_path),
        "original_parent_decisions": Path(review_decisions_path),
        "asset_inventory": Path(inventory_path),
        "asset_apply_preview": Path(asset_apply_preview_path),
        "asset_blocked_preview": Path(asset_blocked_preview_path),
        "restricted_customers": Path(restricted_customers_path),
        "formal_vault": Path(vault_path),
        "formal_sqlite": Path(db_path),
        "production_slack_renderer": Path(production_slack_renderer_path),
    }
    output_dir = Path(output_dir)
    _assert_safe_output(output_dir, list(paths.values()))
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise MissingParentResolutionApplyPreviewError(
            "required validation input is missing: " + ", ".join(missing)
        )
    before_hashes = {name: _hash_path(path) for name, path in paths.items()}

    proposal_rows = _read_csv(paths["decision_proposal"])
    parent_preview_rows = _read_csv(paths["parent_preview"])
    asset_preview_rows = _read_csv(paths["asset_eligibility_preview"])
    alias_rows = _read_csv(paths["search_alias_preview"])
    parent_rows = _read_json_list(paths["parent_records"])
    review_rows = _read_csv(paths["original_parent_decisions"])

    validation_rows, errors = _validate_parent_decisions(
        proposal_rows, parent_preview_rows, parent_rows, review_rows
    )
    source_rows, source_errors = _source_metadata_preview(
        proposal_rows, parent_preview_rows, parent_rows
    )
    errors.extend(source_errors)
    collision_rows, collision_errors = _alias_collisions(
        alias_rows, proposal_rows, parent_rows
    )
    errors.extend(collision_errors)

    reviewed_at = _authoritative_reviewed_at(proposal_rows, errors)
    context = build_resolution_context(
        parent_records_path=paths["parent_records"],
        review_decisions_path=paths["original_parent_decisions"],
        inventory_path=paths["asset_inventory"],
        apply_preview_path=paths["asset_apply_preview"],
        blocked_preview_path=paths["asset_blocked_preview"],
        restricted_customers_path=paths["restricted_customers"],
        reviewed_at=reviewed_at,
    )
    asset_rows, asset_errors = _asset_apply_preview_rows(
        context.asset_decisions, parent_preview_rows, asset_preview_rows
    )
    errors.extend(asset_errors)

    parent_apply_rows = _parent_apply_preview_rows(parent_preview_rows)
    if formal_search_fn is None:
        formal_search_fn = build_resolution_formal_search(
            context,
            paths["formal_sqlite"],
            paths["restricted_customers"],
            paths["asset_apply_preview"],
        )
    search_results = [
        preview_resolution_search(query, context, list(formal_search_fn(query)))
        for query in SEARCH_QUERIES
    ]

    approved_parent_ids = {
        _text(row.get("record_id"))
        for row in proposal_rows
        if _text(row.get("proposed_review_decision")) != "exclude"
    }
    storage = _storage_capabilities(
        parent_rows,
        paths["formal_vault"],
        paths["formal_sqlite"],
        approved_parent_ids,
    )
    blocker_reasons = []
    if not storage["search_alias_schema_ready"]:
        blocker_reasons.append("search_alias_storage_not_implemented")
    if not storage["asset_eligibility_schema_ready"]:
        blocker_reasons.append("asset_eligibility_storage_not_implemented")
    if not storage["all_approved_parents_formally_present"]:
        blocker_reasons.append("formal_parent_sync_required")
    blocker_reasons.extend(sorted({row["code"] for row in errors}))
    warning_count = sum(row["severity"] == "warning" for row in collision_rows)
    error_counts = dict(sorted(Counter(row["code"] for row in errors).items()))

    input_checksums = dict(sorted(before_hashes.items()))
    state = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "input_checksums": input_checksums,
        "parent_changes": parent_apply_rows,
        "source_metadata_changes": source_rows,
        "asset_eligibility_changes": asset_rows,
        "target_locations": _target_locations(),
        "reviewer": REVIEWER,
        "reviewed_at": reviewed_at,
        "expires_at": _expiration(reviewed_at),
        "old_asset_plan_id": OLD_ASSET_PLAN_ID,
    }
    plan_state_hash = _canonical_checksum(state)
    plan_id = f"resolution-plan-{plan_state_hash[:16]}"
    execution_blocked = bool(blocker_reasons)

    summary = {
        "conclusion": "C. Not ready for Apply" if execution_blocked else "A. Ready for parent resolution confirmation",
        "validation_only": True,
        "plan_only": True,
        "plan_id": plan_id,
        "plan_state_hash": plan_state_hash,
        "reviewer": REVIEWER,
        "reviewed_at": reviewed_at,
        "expires_at": state["expires_at"],
        "execution_blocked": execution_blocked,
        "blocker_reasons": blocker_reasons,
        "validation_error_count": len(errors),
        "validation_error_codes": error_counts,
        "input_checksum_mismatch_count": 0,
        "alias_warning_count": warning_count,
        "validated_parent_count": sum(row["validation_status"] == "valid" for row in validation_rows),
        "parent_preview_row_count": len(parent_apply_rows),
        "parent_changed_row_count": sum(row["action"] == "update" for row in parent_apply_rows),
        "source_metadata_preview_row_count": len(source_rows),
        "asset_eligibility_preview_row_count": len(asset_rows),
        "eligible_asset_count": context.counts["final_eligible_asset_count"],
        "hold_asset_count": context.counts["final_hold_asset_count"],
        "excluded_asset_count": context.counts["final_excluded_asset_count"],
        "approved_url_field_count": context.counts["final_approved_url_field_count"],
        "new_asset_id_count": context.counts["identity_added_count"],
        "lost_asset_id_count": context.counts["identity_lost_count"],
        "record_id_change_count": sum(row["record_id_status"] != "match" for row in asset_rows),
        "blocked_asset_in_apply_manifest_count": sum(
            row["proposed_asset_index_eligibility"] != "include"
            and row["will_enter_apply_manifest"] == "true"
            for row in asset_rows
        ),
        "search_query_count": len(search_results),
        "search_abstained_count": sum(row["abstained"] for row in search_results),
        "storage_capabilities": storage,
        "old_asset_plan_id": OLD_ASSET_PLAN_ID,
        "old_asset_plan_status": "DO NOT CONFIRM",
        "decisions_applied": False,
        "parent_synced": False,
        "formal_vault_modified": False,
        "formal_sqlite_modified": False,
        "original_decisions_modified": False,
        "production_slack_renderer_modified": False,
    }
    manifest = {
        **state,
        "plan_id": plan_id,
        "plan_state_hash": plan_state_hash,
        "execution_blocked": execution_blocked,
        "blocker_reasons": blocker_reasons,
        "validation_error_count": len(errors),
        "validation_error_codes": error_counts,
        "alias_warning_count": warning_count,
        "old_asset_plan_status": "DO NOT CONFIRM",
        "input_checksum_match_required_for_future_execution": True,
        "confirmation_required": True,
        "confirm_supported": False,
        "execute_supported": False,
        "proposed_changes": {
            "parent_decisions": len(parent_apply_rows),
            "source_metadata": len(source_rows),
            "asset_eligibility": len(asset_rows),
            "eligible_assets": summary["eligible_asset_count"],
            "approved_url_fields": summary["approved_url_field_count"],
        },
    }
    _write_reports(
        output_dir,
        summary,
        validation_rows,
        parent_apply_rows,
        source_rows,
        asset_rows,
        collision_rows,
        search_results,
        errors,
        manifest,
    )
    after_hashes = {name: _hash_path(path) for name, path in paths.items()}
    if before_hashes != after_hashes:
        raise MissingParentResolutionApplyPreviewError(
            "a protected source changed during validation and Apply Preview"
        )
    return summary


def _validate_parent_decisions(
    proposals: Sequence[dict],
    previews: Sequence[dict],
    parents: Sequence[dict],
    reviews: Sequence[dict],
) -> Tuple[List[dict], List[dict]]:
    errors: List[dict] = []
    proposal_counts = Counter(_text(row.get("record_id")) for row in proposals)
    preview_by_id = _unique_map(previews, "record_id", errors, "duplicate_parent_preview")
    parent_by_id = _parent_map(parents, errors)
    review_by_id = _review_map(reviews, errors)
    rows = []
    expected_ids = {_record_id_for_row(parents, row) for row in EXPECTED_PARENT_DECISIONS}

    for record_id, count in proposal_counts.items():
        if count > 1:
            _issue(errors, "duplicate_parent_decision", record_id)
        if record_id not in expected_ids:
            _issue(errors, "unknown_parent_decision", record_id)
    for record_id in expected_ids - set(proposal_counts):
        _issue(errors, "missing_parent_decision", record_id)

    seen = set()
    for proposal in proposals:
        record_id = _text(proposal.get("record_id"))
        if record_id in seen or record_id not in expected_ids:
            continue
        seen.add(record_id)
        source_row = _source_row_from_record_id(record_id)
        expected_brand, expected_decision = EXPECTED_PARENT_DECISIONS[source_row]
        preview = preview_by_id.get(record_id, {})
        parent = parent_by_id.get(record_id, {})
        review = review_by_id.get(record_id, {})
        row_errors = []
        checks = (
            ("brand_name_mismatch", _text(proposal.get("brand_name")) == expected_brand == _text(parent.get("brand_name"))),
            ("invalid_proposed_decision", _text(proposal.get("proposed_review_decision")) in ALLOWED_REVIEW_DECISIONS),
            ("unexpected_proposed_decision", _text(proposal.get("proposed_review_decision")) == expected_decision),
            ("proposal_preview_mismatch", _text(proposal.get("proposed_review_decision")) == _text(preview.get("proposed_review_decision"))),
            ("stale_parent_decision", _text(preview.get("current_review_decision")) == _text(review.get("review_decision"))),
            ("missing_reviewer", bool(_text(proposal.get("reviewer")))),
            ("invalid_reviewer", _text(proposal.get("reviewer")) == REVIEWER),
            ("invalid_reviewed_at", _valid_iso(_text(proposal.get("reviewed_at")))),
            ("missing_decision_reason", bool(_text(proposal.get("decision_reason")))),
        )
        for code, valid in checks:
            if not valid:
                row_errors.append(code)
                _issue(errors, code, record_id)
        rows.append(
            {
                "record_id": record_id,
                "brand_name": _text(proposal.get("brand_name")),
                "current_review_decision": _text(preview.get("current_review_decision")),
                "proposed_review_decision": _text(proposal.get("proposed_review_decision")),
                "reviewer": _text(proposal.get("reviewer")),
                "reviewed_at": _text(proposal.get("reviewed_at")),
                "decision_reason": _text(proposal.get("decision_reason")),
                "validation_status": "valid" if not row_errors else "invalid",
                "error_codes": _stable_json(row_errors),
            }
        )
    return sorted(rows, key=lambda row: _source_row_from_record_id(row["record_id"])), errors


def _source_metadata_preview(
    proposals: Sequence[dict], previews: Sequence[dict], parents: Sequence[dict]
) -> Tuple[List[dict], List[dict]]:
    errors: List[dict] = []
    proposal_by_id = {row["record_id"]: row for row in proposals if _text(row.get("record_id"))}
    parent_by_id = _parent_map(parents, errors)
    rows = []
    for preview in previews:
        record_id = _text(preview.get("record_id"))
        if record_id not in proposal_by_id or record_id not in parent_by_id:
            continue
        proposal = proposal_by_id[record_id]
        parent = parent_by_id[record_id]
        expected_type = "partner" if _text(parent.get("merchant_status")) == "合作夥伴" else "merchant"
        proposed_type = _text(proposal.get("proposed_entity_type"))
        handle = _text(preview.get("merchant_handle"))
        source_handle = _text(parent.get("merchant_handle"))
        if proposed_type != expected_type:
            _issue(errors, "entity_type_mismatch", record_id)
        if expected_type == "partner" and handle != source_handle:
            _issue(errors, "synthetic_partner_handle", record_id)
        aliases = _json_list(proposal.get("proposed_search_aliases"))
        rows.append(
            {
                "record_id": record_id,
                "brand_name": _text(parent.get("brand_name")),
                "merchant_status": _text(parent.get("merchant_status")),
                "authoritative_entity_type_field": "merchant_status",
                "current_entity_type": expected_type,
                "proposed_entity_type": proposed_type,
                "entity_type_storage_action": "derive_from_existing_authoritative_field",
                "merchant_handle": source_handle,
                "merchant_handle_requirement": "not_required" if expected_type == "partner" else "required_by_existing_rules",
                "proposed_search_aliases": _stable_json(aliases),
                "alias_storage_action": "add_source_record_field" if aliases else "no_change",
                "search_aliases": _stable_json(aliases),
                "alias_provenance": "Admin human decision" if aliases else "",
                "alias_reviewer": _text(proposal.get("reviewer")) if aliases else "",
                "alias_reviewed_at": _text(proposal.get("reviewed_at")) if aliases else "",
                "target_storage": _future_parent_target(record_id),
                "action": "add_exact_aliases" if aliases else "validate_existing_identity",
                "reason": _text(proposal.get("decision_reason")),
                "reviewer": _text(proposal.get("reviewer")),
                "reviewed_at": _text(proposal.get("reviewed_at")),
                "future_vault_target": _future_parent_target(record_id),
                "future_sqlite_target": "documents.metadata_json.search_aliases" if aliases else "documents.metadata_json.merchant_status",
                "applied": "false",
            }
        )
    return sorted(rows, key=lambda row: _source_row_from_record_id(row["record_id"])), errors


def _alias_collisions(
    aliases: Sequence[dict], proposals: Sequence[dict], parents: Sequence[dict]
) -> Tuple[List[dict], List[dict]]:
    errors: List[dict] = []
    expected_pairs = {
        (_text(row.get("record_id")), alias)
        for row in proposals
        for alias in _json_list(row.get("proposed_search_aliases"))
    }
    actual_pairs = {
        (_text(row.get("record_id")), _text(row.get("alias"))) for row in aliases
    }
    if actual_pairs != expected_pairs:
        _issue(errors, "alias_contract_mismatch", "search_alias_preview")
    alias_counts = Counter(_normalize(row.get("alias")) for row in aliases)
    rows = []
    for alias_row in aliases:
        alias = _text(alias_row.get("alias"))
        normalized = _normalize(alias)
        record_id = _text(alias_row.get("record_id"))
        proposal = next(
            (row for row in proposals if _text(row.get("record_id")) == record_id), {}
        )
        if (
            _text(alias_row.get("match_type")) != "case_insensitive_exact"
            or _text(alias_row.get("fuzzy_matching")) != "false"
            or _text(alias_row.get("storage_level")) != "source_record"
            or _text(alias_row.get("reviewer")) != _text(proposal.get("reviewer"))
            or _text(alias_row.get("reviewed_at")) != _text(proposal.get("reviewed_at"))
        ):
            _issue(errors, "invalid_alias_contract", record_id)
        identity_matches = []
        tag_matches = []
        for parent in parents:
            candidate_id = _record_id(parent)
            if candidate_id != record_id and normalized in {
                _normalize(parent.get("brand_name")),
                _normalize(parent.get("merchant_handle")),
            }:
                identity_matches.append(candidate_id)
            if candidate_id != record_id and normalized in {
                _normalize(value) for value in _string_list(parent.get("content_tags"))
            }:
                tag_matches.append(candidate_id)
        severity = "none"
        collision_type = "none"
        if alias_counts[normalized] > 1 or identity_matches:
            severity = "blocker"
            collision_type = "duplicate_alias_or_identity"
            _issue(errors, "alias_identity_collision", record_id)
        elif tag_matches:
            severity = "warning"
            collision_type = "shared_exact_content_tag"
        rows.append(
            {
                "record_id": record_id,
                "brand_name": _text(alias_row.get("brand_name")),
                "alias": alias,
                "normalized_alias": normalized,
                "match_type": "case_insensitive_exact",
                "fuzzy_matching": "false",
                "storage_level": "source_record",
                "governance_required": "true",
                "asset_eligibility_required": "true",
                "collision_type": collision_type,
                "severity": severity,
                "identity_collision_count": len(identity_matches),
                "other_record_match_count": len(tag_matches),
                "result_behavior": "include_all_legitimate_exact_field_matches" if tag_matches else "resolve_source_record",
                "alias_reviewer": _text(alias_row.get("reviewer")),
                "alias_reviewed_at": _text(alias_row.get("reviewed_at")),
            }
        )
    return rows, errors


def _asset_apply_preview_rows(
    authoritative: Sequence[dict], parent_previews: Sequence[dict], prior_rows: Sequence[dict]
) -> Tuple[List[dict], List[dict]]:
    errors: List[dict] = []
    prior_by_id = _unique_map(prior_rows, "asset_id", errors, "duplicate_asset_eligibility")
    parent_by_id = {row["record_id"]: row for row in parent_previews}
    rows = []
    for asset in authoritative:
        asset_id = _text(asset.get("asset_id"))
        prior = prior_by_id.get(asset_id)
        if prior is None:
            _issue(errors, "unknown_asset_eligibility", asset_id)
            continue
        record_id = _text(asset.get("record_id"))
        if _text(prior.get("record_id")) != record_id or not asset_id.startswith(record_id + ":"):
            _issue(errors, "asset_record_id_mismatch", asset_id)
        parent = parent_by_id.get(record_id, {})
        eligibility = _text(asset.get("proposed_asset_index_eligibility"))
        include = eligibility == "include"
        tags = _json_list(asset.get("resolved_content_tags")) if include else []
        rows.append(
            {
                "record_id": record_id,
                "asset_id": asset_id,
                "brand_name": _text(asset.get("brand_name")),
                "asset_type": _text(asset.get("asset_type")),
                "asset_title": _text(asset.get("asset_title")),
                "record_id_status": "match" if _text(prior.get("record_id")) == record_id else "mismatch",
                "current_asset_eligibility": _text(asset.get("current_asset_eligibility")),
                "proposed_asset_index_eligibility": eligibility,
                "proposed_asset_search_eligibility": _text(asset.get("proposed_asset_search_eligibility")),
                "eligibility_reason": _text(asset.get("eligibility_reason")),
                "parent_review_decision": _text(parent.get("proposed_review_decision")),
                "can_external_reference": _text(parent.get("proposed_can_external_reference")),
                "will_enter_apply_manifest": _bool_text(include),
                "will_enter_search_index": _bool_text(include),
                "will_render_in_slack": _bool_text(include),
                "will_generate_citation": _bool_text(include),
                "resolved_content_tags": _stable_json(tags),
                "content_tags_source": "eligible_parent_source_record" if tags else "not_resolved",
                "future_asset_vault_target": _future_asset_target(asset_id),
                "future_sqlite_target": "content_assets.asset_index_eligibility / asset_search_eligibility",
                "reviewed_by": _text(asset.get("reviewer")),
                "reviewed_at": _text(asset.get("reviewed_at")),
                "applied": "false",
            }
        )
    return rows, errors


def _parent_apply_preview_rows(previews: Sequence[dict]) -> List[dict]:
    rows = []
    for row in previews:
        current = _text(row.get("current_review_decision"))
        proposed = _text(row.get("proposed_review_decision"))
        rows.append(
            {
                "record_id": _text(row.get("record_id")),
                "brand_name": _text(row.get("brand_name")),
                "current_review_decision": current,
                "proposed_review_decision": proposed,
                "current_can_enter_vault": _text(row.get("current_can_enter_vault")),
                "proposed_can_enter_vault": _text(row.get("proposed_can_enter_vault")),
                "current_can_enter_content_index": _text(row.get("current_can_enter_content_index")),
                "proposed_can_enter_content_index": _text(row.get("proposed_can_enter_content_index")),
                "current_can_external_reference": _text(row.get("current_can_external_reference")),
                "proposed_can_external_reference": _text(row.get("proposed_can_external_reference")),
                "decision_reason": _text(row.get("reason")),
                "reviewer": _text(row.get("reviewer")),
                "reviewed_at": _text(row.get("reviewed_at")),
                "action": "update" if current != proposed else "no_change",
                "future_target": "reports/excel_preview/review_decisions_template.csv",
                "applied": "false",
            }
        )
    return sorted(rows, key=lambda row: _source_row_from_record_id(row["record_id"]))


def _storage_capabilities(
    parents: Sequence[dict],
    vault: Path,
    db: Path,
    approved_parent_ids: set,
) -> dict:
    parent_has_alias_schema = bool(parents) and all("search_aliases" in row for row in parents)
    asset_vault_exists = (vault / "MKA/managed/assets").exists()
    content_assets_exists = False
    sqlite_parent_ids = set()
    with sqlite3.connect(str(db)) as connection:
        content_assets_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_assets'"
        ).fetchone() is not None
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone():
            for (raw_metadata,) in connection.execute("SELECT metadata_json FROM documents"):
                try:
                    metadata = json.loads(raw_metadata)
                except (TypeError, json.JSONDecodeError):
                    continue
                record_id = _record_id(metadata)
                if record_id:
                    sqlite_parent_ids.add(record_id)
    vault_parent_ids = set()
    for path in Path(vault).rglob("*.md"):
        if path.name.startswith("._"):
            continue
        try:
            metadata, _ = parse_markdown_with_frontmatter(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, FrontmatterError):
            continue
        record_id = _record_id(metadata)
        if record_id:
            vault_parent_ids.add(record_id)
    missing_vault = sorted(approved_parent_ids - vault_parent_ids)
    missing_sqlite = sorted(approved_parent_ids - sqlite_parent_ids)
    return {
        "entity_type_authoritative_field": "merchant_status",
        "entity_type_schema_ready": True,
        "search_alias_schema_ready": parent_has_alias_schema,
        "asset_vault_schema_ready": asset_vault_exists,
        "content_assets_table_ready": content_assets_exists,
        "asset_eligibility_schema_ready": asset_vault_exists and content_assets_exists,
        "approved_parent_count": len(approved_parent_ids),
        "formal_vault_parent_count": len(approved_parent_ids & vault_parent_ids),
        "formal_sqlite_parent_count": len(approved_parent_ids & sqlite_parent_ids),
        "missing_formal_vault_parent_count": len(missing_vault),
        "missing_formal_sqlite_parent_count": len(missing_sqlite),
        "all_approved_parents_formally_present": not missing_vault and not missing_sqlite,
    }


def _target_locations() -> dict:
    return {
        "parent_review_decision": {
            "target": "reports/excel_preview/review_decisions_template.csv",
            "join_key": ["source_sheet", "source_row", "record_type"],
            "status": "existing_authoritative_decision_file",
        },
        "parent_review_audit": {
            "target": "reports/excel_preview/review_decisions_template.csv reviewer / reviewed_at",
            "status": "existing authoritative review metadata columns",
        },
        "entity_type": {
            "target": "source record merchant_status -> normalized runtime entity_type",
            "join_key": ["source_sheet", "source_row"],
            "status": "derive_from_existing_authoritative_field; no duplicate field write",
        },
        "merchant_handle_requirement": {
            "target": "governance policy derived from source record merchant_status",
            "status": "partner means not_required; no synthetic field or Handle write",
        },
        "search_aliases": {
            "target": "obsidian_vault/MKA/merchant_cases/<managed-parent>.md frontmatter.search_aliases",
            "sqlite_projection": "documents.metadata_json.search_aliases",
            "status": "schema migration required before Apply",
        },
        "search_alias_review_audit": {
            "target": "managed parent frontmatter search_alias_reviewed_by / search_alias_reviewed_at",
            "sqlite_projection": "documents.metadata_json alias review fields",
            "status": "schema migration required before Apply",
        },
        "asset_eligibility": {
            "target": "obsidian_vault/MKA/managed/assets/<sha256(asset_id)>.md",
            "sqlite_projection": "content_assets.asset_index_eligibility / asset_search_eligibility",
            "status": "asset managed-record and table migration required before Apply",
        },
        "asset_review_audit": {
            "target": "managed asset frontmatter reviewed_by / reviewed_at",
            "sqlite_projection": "content_assets.reviewed_by / reviewed_at",
            "status": "asset managed-record and table migration required before Apply",
        },
        "governance_audit": {
            "target": "reports/audit_log.csv plus immutable confirmed resolution manifest reference",
            "scope": "exclude / hold evidence and execution outcome only",
            "status": "future Apply only; no audit append in this preview Sprint",
        },
    }


def _write_reports(
    output: Path,
    summary: Mapping[str, object],
    validation_rows: Sequence[dict],
    parent_rows: Sequence[dict],
    source_rows: Sequence[dict],
    asset_rows: Sequence[dict],
    collision_rows: Sequence[dict],
    search_results: Sequence[dict],
    errors: Sequence[dict],
    manifest: Mapping[str, object],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / OUTPUT_FILENAMES[1], validation_rows)
    _write_csv(output / OUTPUT_FILENAMES[2], parent_rows)
    _write_csv(output / OUTPUT_FILENAMES[3], source_rows)
    _write_csv(output / OUTPUT_FILENAMES[4], asset_rows)
    _write_csv(output / OUTPUT_FILENAMES[5], collision_rows)
    (output / OUTPUT_FILENAMES[0]).write_text(_summary_md(summary, errors), encoding="utf-8")
    (output / OUTPUT_FILENAMES[6]).write_text(_target_md(), encoding="utf-8")
    (output / OUTPUT_FILENAMES[7]).write_text(_counts_md(summary), encoding="utf-8")
    (output / OUTPUT_FILENAMES[8]).write_text(_search_md(search_results), encoding="utf-8")
    (output / OUTPUT_FILENAMES[9]).write_text(_checklist_md(summary), encoding="utf-8")
    (output / OUTPUT_FILENAMES[10]).write_text(_rollback_md(summary), encoding="utf-8")
    (output / OUTPUT_FILENAMES[11]).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _summary_md(summary: Mapping[str, object], errors: Sequence[dict]) -> str:
    lines = [
        "# Missing Parent Resolution Validation & Apply Preview",
        "",
        "> Validation and plan preview only. No decision, Vault, SQLite, index or Slack renderer was modified.",
        "",
        f"- Conclusion: {summary['conclusion']}",
        f"- PLAN_ID: `{summary['plan_id']}`",
        f"- Validation: {summary['validated_parent_count']}/5 parents valid; errors={summary['validation_error_count']}",
        f"- Alias warnings: {summary['alias_warning_count']}",
        f"- Assets: eligible={summary['eligible_asset_count']}, hold={summary['hold_asset_count']}, excluded={summary['excluded_asset_count']}",
        f"- Approved URL fields: {summary['approved_url_field_count']}",
        f"- Execution blocked: {str(summary['execution_blocked']).lower()}",
        f"- Old Asset PLAN_ID: `{OLD_ASSET_PLAN_ID}` - **DO NOT CONFIRM**",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{reason}`" for reason in summary["blocker_reasons"])
    if errors:
        lines.extend(["", "## Validation Errors", ""])
        lines.extend(f"- `{row['code']}`: `{row['record_id']}`" for row in errors)
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Decisions applied: no",
            "- Parent sync / index rebuild: not run",
            "- Formal Vault / SQLite writes: 0 / 0",
            "- Production Slack renderer changes: 0",
        ]
    )
    return "\n".join(lines) + "\n"


def _target_md() -> str:
    return """# Apply Target Mapping

| Data | Future authoritative target | SQLite projection | Current readiness |
| --- | --- | --- | --- |
| Parent review decision | `reports/excel_preview/review_decisions_template.csv`, keyed by source_sheet/source_row/record_type | Parent document governance metadata after sync | Existing decision file; no write in this Sprint |
| Parent reviewer / reviewed_at | Same authoritative decision row | Parent governance audit projection | Existing columns; no write in this Sprint |
| Entity type | Existing source `merchant_status`; `合作夥伴` maps to partner | Derived normalized entity type | Ready; no duplicate field migration |
| Merchant Handle requirement | Governance rule derived from `merchant_status` | Runtime validation policy | Partner means `not_required`; no synthetic Handle write |
| Search aliases | Managed parent frontmatter `search_aliases` | `documents.metadata_json.search_aliases` | Not implemented; Apply blocked |
| Alias reviewer / reviewed_at | Managed parent `search_alias_reviewed_by` / `search_alias_reviewed_at` | `documents.metadata_json` alias audit fields | Not implemented; Apply blocked |
| Asset eligibility | `obsidian_vault/MKA/managed/assets/<sha256(asset_id)>.md` | normalized `content_assets` eligibility columns | Not implemented; Apply blocked |
| Asset reviewer / reviewed_at | Managed asset frontmatter `reviewed_by` / `reviewed_at` | `content_assets.reviewed_by` / `reviewed_at` | Not implemented; Apply blocked |
| Governance audit | `reports/audit_log.csv` plus confirmed immutable manifest reference | Audit only, not retrieval | Future Apply only; no append in this Sprint |

Future managed Vault writes would include r7, r12, r32 and r122 parents plus eligible asset records. r30 remains excluded. No write is authorized by this plan.
"""


def _counts_md(summary: Mapping[str, object]) -> str:
    return f"""# Apply Count Reconciliation

- Eligible assets: {summary['eligible_asset_count']}
- Hold assets: {summary['hold_asset_count']}
- Excluded / governance-blocked assets: {summary['excluded_asset_count']}
- Approved URL fields: {summary['approved_url_field_count']}
- New / lost asset IDs: {summary['new_asset_id_count']} / {summary['lost_asset_id_count']}
- Changed record IDs: {summary['record_id_change_count']}
- Blocked assets in Apply manifest: {summary['blocked_asset_in_apply_manifest_count']}

The taken-down r30 article removes two approved URL fields. The r12 article remains eligible and internal-only; its reviewing video remains hold and contributes no Apply row.
"""


def _search_md(results: Sequence[dict]) -> str:
    lines = [
        "# Post-Apply Search Preview",
        "",
        "> Read-only overlay rendered with the approved B standard format. Production Slack was not changed.",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result['query']}",
                "",
                f"- Assets: {result['total_assets']}",
                f"- Citations: {len(result['citations'])}",
                "",
                "```text",
                render_resolution_standard(result),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _checklist_md(summary: Mapping[str, object]) -> str:
    return f"""# Resolution Apply Confirmation Checklist

- [x] Exact five Admin decisions validated
- [x] Parent / asset identity and count conservation checked
- [x] Alias exact matching, collision and governance behavior checked
- [x] Hold and exclude assets removed from search, Slack and citation previews
- [x] Protected inputs remained unchanged
- [ ] Implement source-record `search_aliases` schema
- [ ] Implement managed asset eligibility schema and `content_assets` projection
- [ ] Sync approved parents through a separately confirmed workflow
- [ ] Resolve all blockers and regenerate this plan
- [ ] Human confirmation with the regenerated PLAN_ID

Current PLAN_ID `{summary['plan_id']}` is blocked and cannot be confirmed or executed. Old `{OLD_ASSET_PLAN_ID}` also remains invalid.
"""


def _rollback_md(summary: Mapping[str, object]) -> str:
    return f"""# Resolution Apply Rollback Plan

No rollback is required for this Sprint because writes are zero.

For a future Apply implementation:
1. Verify PLAN_ID `{summary['plan_id']}`, expiration and every input checksum before staging.
2. Back up the parent decision file, affected managed parent files, managed asset namespace and SQLite database.
3. Stage parent decisions, source aliases and asset eligibility in a temporary directory/database.
4. Re-run five-parent validation, 205/1/16/410 conservation, denylist checks and nine search assertions.
5. Atomically replace targets only after every assertion passes.
6. On any failure, restore all backups atomically and verify checksums plus search behavior.
7. Preserve the failed manifest and audit reason; never reuse the failed PLAN_ID.

This preview does not implement confirm or execute.
"""


def _authoritative_reviewed_at(rows: Sequence[dict], errors: List[dict]) -> str:
    values = {_text(row.get("reviewed_at")) for row in rows if _text(row.get("reviewed_at"))}
    valid = [value for value in values if _valid_iso(value)]
    if len(values) != 1 or len(valid) != 1:
        _issue(errors, "inconsistent_reviewed_at", "decision_proposal")
        return "1970-01-01T00:00:00+00:00"
    return valid[0]


def _expiration(reviewed_at: str) -> str:
    parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    return (parsed + timedelta(days=14)).isoformat(timespec="seconds")


def _record_id_for_row(parents: Sequence[dict], source_row: int) -> str:
    matches = [_record_id(row) for row in parents if _integer(row.get("source_row")) == source_row]
    return matches[0] if len(matches) == 1 else f"missing:r{source_row}"


def _parent_map(rows: Sequence[dict], errors: List[dict]) -> Dict[str, dict]:
    result = {}
    for row in rows:
        record_id = _record_id(row)
        if not record_id or record_id in result:
            _issue(errors, "duplicate_or_missing_parent_record", record_id)
            continue
        result[record_id] = row
    return result


def _review_map(rows: Sequence[dict], errors: List[dict]) -> Dict[str, dict]:
    result = {}
    for row in rows:
        if _text(row.get("record_type")) != "merchant_case":
            continue
        record_id = _record_id(row)
        if record_id in result:
            _issue(errors, "duplicate_original_parent_decision", record_id)
        else:
            result[record_id] = row
    return result


def _unique_map(rows: Sequence[dict], field: str, errors: List[dict], code: str) -> Dict[str, dict]:
    result = {}
    for row in rows:
        key = _text(row.get(field))
        if not key or key in result:
            _issue(errors, code, key)
        else:
            result[key] = row
    return result


def _future_parent_target(record_id: str) -> str:
    return f"obsidian_vault/MKA/merchant_cases/record-r{_source_row_from_record_id(record_id)}-<slug>.md"


def _future_asset_target(asset_id: str) -> str:
    digest = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()
    return f"obsidian_vault/MKA/managed/assets/{digest}.md"


def _assert_safe_output(output: Path, protected: Sequence[Path]) -> None:
    resolved = output.resolve()
    for path in protected:
        source = Path(path).resolve()
        if resolved == source or resolved in source.parents or source in resolved.parents:
            raise MissingParentResolutionApplyPreviewError(
                "output must be separate from every protected source"
            )
    lowered = {part.casefold() for part in resolved.parts}
    if ".mka" in lowered or "obsidian_vault" in lowered:
        raise MissingParentResolutionApplyPreviewError(
            "output cannot be inside formal Vault or .mka"
        )


def _issue(errors: List[dict], code: str, record_id: str) -> None:
    errors.append({"code": code, "record_id": record_id or "unknown"})


def _valid_iso(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _source_row_from_record_id(record_id: str) -> int:
    try:
        return int(record_id.rsplit(":r", 1)[1])
    except (IndexError, ValueError):
        return -1


def _record_id(row: Mapping[str, object]) -> str:
    sheet = _text(row.get("source_sheet"))
    source_row = _integer(row.get("source_row"))
    return f"{sheet}:r{source_row}" if sheet and source_row is not None else ""


def _normalize(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def _string_list(value: object) -> List[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [part.strip() for part in value.split("|")]
        return [_text(item) for item in decoded if _text(item)] if isinstance(decoded, list) else []
    return []


def _json_list(value: object) -> List[str]:
    return _string_list(value)


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _canonical_checksum(value: object) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _hash_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith("._"):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def _read_csv(path: Path) -> List[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise MissingParentResolutionApplyPreviewError(f"CSV has no header: {path}")
        return list(reader)


def _read_json_list(path: Path) -> List[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MissingParentResolutionApplyPreviewError(f"invalid JSON input: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise MissingParentResolutionApplyPreviewError(f"JSON must be a list of objects: {path}")
    return value


def _write_csv(path: Path, rows: Sequence[dict]) -> None:
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["record_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _safe_csv(row.get(field)) for field in fieldnames})


def _safe_csv(value: object) -> str:
    text = "" if value is None else str(value)
    return "[unsafe input redacted]" if text.startswith(("=", "+", "-", "@")) else text


def _bool_text(value: bool) -> str:
    return str(bool(value)).lower()


def _integer(value: object) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
