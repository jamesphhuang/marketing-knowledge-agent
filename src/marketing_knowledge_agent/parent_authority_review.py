from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .frontmatter import parse_markdown_with_frontmatter
from .review_decision_validation import ALLOWED_REVIEW_DECISIONS


OUTPUT_FILENAMES = (
    "parent_authority_review_summary.md",
    "authority_gap_reconciliation.csv",
    "baseline_parent_authority_review_template.csv",
    "baseline_parent_review_evidence.csv",
    "baseline_parent_batch_review_groups.csv",
    "baseline_parent_batch_confirmation_template.md",
    "baseline_parent_manual_review_queue.csv",
    "existing_admin_resolution_coverage.csv",
    "legacy_authority_coverage.csv",
    "parent_decision_effect_matrix.md",
    "authority_coverage_simulation.md",
    "authority_review_validation_errors.csv",
    "authority_review_validation_warnings.csv",
    "next_decision_store_prerequisites.md",
)

OLD_PLAN_IDS = (
    "decision-store-plan-8f0655bae1febc90",
    "resolution-plan-a878e6d1036bac96",
    "asset-plan-07cd12338615c961",
)

REVIEW_TEMPLATE_COLUMNS = (
    "cohort_id",
    "record_id",
    "brand_name",
    "merchant_handle",
    "merchant_status",
    "entity_type",
    "source_sheet",
    "source_row",
    "content_tags",
    "asset_count",
    "asset_types",
    "valid_asset_url_count",
    "current_vault_presence",
    "current_index_presence",
    "can_enter_vault",
    "can_enter_content_index",
    "can_external_reference",
    "governance_flags",
    "metadata_missing_fields",
    "conflict_flags",
    "authority_status",
    "recommended_review_decision",
    "recommendation_reason",
    "recommendation_confidence",
    "batch_review_eligible",
    "final_review_decision",
    "reviewer",
    "reviewed_at",
    "notes",
)

ISSUE_COLUMNS = ("severity", "code", "record_id", "message")
EVIDENCE_COLUMNS = (
    "record_id",
    "brand_name",
    "merchant_handle",
    "merchant_status",
    "entity_type",
    "source_sheet",
    "source_row",
    "content_tags",
    "asset_count",
    "asset_types",
    "asset_titles",
    "valid_asset_url_count",
    "can_enter_vault",
    "can_enter_content_index",
    "can_external_reference",
    "classification",
    "source_status",
    "governance_flags",
    "restricted_status",
    "pending_status",
    "current_vault_presence",
    "current_index_presence",
    "existing_citation_eligibility",
    "metadata_completeness",
    "content_validity",
    "metadata_missing_fields",
    "conflict_flags",
    "existing_review_history",
    "authority_status",
    "recommended_review_decision",
    "recommendation_reason",
    "recommendation_confidence",
)
MANUAL_REVIEW_COLUMNS = (*EVIDENCE_COLUMNS, "cohort_id", "batch_approval_safe")


class ParentAuthorityReviewError(ValueError):
    """Raised when the parent authority review packet cannot be built safely."""


def prepare_parent_authority_review(
    *,
    merchant_cases_path: Path,
    review_decisions_path: Path,
    admin_resolutions_path: Path,
    baseline_import_preview_path: Path,
    decision_source_inventory_path: Path,
    asset_inventory_path: Path,
    asset_resolution_path: Path,
    asset_url_decisions_path: Path,
    formal_vault_path: Path,
    formal_db_path: Path,
    decision_store_path: Path,
    output_dir: Path,
) -> dict:
    inputs = {
        "merchant_cases": Path(merchant_cases_path),
        "legacy_review_decisions": Path(review_decisions_path),
        "admin_resolutions": Path(admin_resolutions_path),
        "baseline_import_preview": Path(baseline_import_preview_path),
        "decision_source_inventory": Path(decision_source_inventory_path),
        "asset_inventory": Path(asset_inventory_path),
        "asset_resolution": Path(asset_resolution_path),
        "asset_url_decisions": Path(asset_url_decisions_path),
        "formal_vault": Path(formal_vault_path),
        "formal_content_index": Path(formal_db_path),
    }
    for label, path in inputs.items():
        if not path.exists():
            raise ParentAuthorityReviewError(f"required {label} input does not exist: {path}")
    if Path(decision_store_path).exists():
        raise ParentAuthorityReviewError("formal Governance Decision Store already exists")
    _assert_safe_output(Path(output_dir), [*inputs.values(), Path(decision_store_path)])
    protected_before = {label: _hash_path(path) for label, path in inputs.items()}

    merchants = _read_json_list(inputs["merchant_cases"])
    review_rows = _read_csv(inputs["legacy_review_decisions"])
    admin_rows = _read_csv(inputs["admin_resolutions"])
    baseline_rows = _read_csv(inputs["baseline_import_preview"])
    source_inventory = _read_csv(inputs["decision_source_inventory"])
    asset_rows = _read_csv(inputs["asset_inventory"])
    asset_resolution_rows = _read_csv(inputs["asset_resolution"])
    asset_url_rows = _read_csv(inputs["asset_url_decisions"])

    errors: List[dict] = []
    warnings: List[dict] = []
    merchant_by_id = _unique_merchants(merchants)
    legacy_by_id = _legacy_merchant_decisions(review_rows, merchant_by_id)
    admin_by_id = _admin_resolutions(admin_rows, merchant_by_id, errors)
    assets_by_record = _assets_by_record(asset_rows, merchant_by_id, errors)
    vault_ids = _formal_vault_record_ids(inputs["formal_vault"])
    index_ids = _formal_index_record_ids(inputs["formal_content_index"])

    original_gap = set(merchant_by_id) - set(legacy_by_id)
    derived_gap = {
        row["record_id"]
        for row in baseline_rows
        if row.get("baseline_status") == "blocked_missing_authority"
        and row.get("subject_type") == "parent"
    }
    if derived_gap != original_gap:
        warnings.append(
            _issue(
                "warning",
                "derived_gap_mismatch",
                "",
                "Derived Decision Store gap differs from the source-of-record merchant reconciliation.",
            )
        )
    _validate_inventory_claim(source_inventory, len(merchants), len(review_rows), warnings)

    brand_counts = Counter(_text(row.get("brand_name")) for row in merchants)
    reconciliation = []
    evidence_rows = []
    review_candidates = []
    for record_id, merchant in sorted(merchant_by_id.items(), key=_record_sort_key):
        admin = admin_by_id.get(record_id)
        legacy = legacy_by_id.get(record_id)
        assets = assets_by_record.get(record_id, [])
        conflicts = _parent_conflicts(merchant, assets, brand_counts)
        if admin:
            status = "admin_resolution_confirmed"
            reason = "Existing Admin resolution supersedes the legacy parent decision; it is not reopened."
        elif legacy:
            status = "legacy_authority_confirmed"
            reason = "Existing validated legacy parent review decision supplies authority."
        elif "asset_parent_brand_mismatch" in conflicts:
            status = "mapping_conflict"
            reason = "Asset identity does not map cleanly to the Parent identity."
        elif conflicts:
            status = "manual_investigation_required"
            reason = "Authority is absent and multi-record or content evidence requires individual review."
        else:
            status = "authority_missing"
            reason = "No legacy decision or Admin resolution exists for this parent."
        reconciliation.append(
            {
                "record_id": record_id,
                "brand_name": _text(merchant.get("brand_name")),
                "merchant_handle": _text(merchant.get("merchant_handle")),
                "merchant_status": _text(merchant.get("merchant_status")),
                "source_sheet": _text(merchant.get("source_sheet")),
                "source_row": str(merchant.get("source_row", "")),
                "legacy_decision_exists": _bool(bool(legacy)),
                "admin_resolution_exists": _bool(bool(admin)),
                "authority_source": "admin_resolution" if admin else "legacy_review_decision" if legacy else "none",
                "authority_status": status,
                "requires_new_human_review": _bool(not legacy and not admin),
                "derived_gap_record": _bool(record_id in derived_gap),
                "same_brand_record_count": brand_counts[_text(merchant.get("brand_name"))],
                "reason": reason,
            }
        )
        if legacy or admin:
            continue
        evidence = _build_evidence(
            record_id,
            merchant,
            assets,
            vault_ids,
            index_ids,
            conflicts,
            status,
        )
        recommendation = _recommend_parent_decision(evidence)
        evidence.update(recommendation)
        evidence_rows.append(evidence)
        review_candidates.append(evidence)

    cohorts = _build_cohorts(review_candidates)
    cohort_by_record = {
        record_id: cohort
        for cohort in cohorts
        for record_id in cohort["record_ids"].split("|")
    }
    template_rows = [
        _review_template_row(row, cohort_by_record[row["record_id"]])
        for row in review_candidates
    ]
    manual_rows = [
        {
            **row,
            "cohort_id": cohort_by_record[row["record_id"]]["cohort_id"],
            "batch_approval_safe": "false",
        }
        for row in review_candidates
        if row["recommended_review_decision"] == "manual_review"
        or row["conflict_flags"] != "[]"
    ]

    admin_coverage = _admin_coverage_rows(admin_rows, legacy_by_id, asset_resolution_rows)
    legacy_coverage = _legacy_coverage_rows(legacy_by_id, admin_by_id)
    counts = _conservation_counts(review_rows, asset_url_rows, asset_resolution_rows)
    low_risk_parents = sum(
        int(row["record_count"])
        for row in cohorts
        if row["batch_approval_safe"] == "true"
    )
    manual_count = len(manual_rows)
    summary = {
        "conclusion": "A. Ready for Admin baseline review" if not errors else "C. Requires fixes before human review",
        "merchant_parent_count": len(merchant_by_id),
        "legacy_merchant_authority_count": len(legacy_by_id),
        "original_authority_gap_count": len(original_gap),
        "baseline_inventory_count": len(baseline_rows),
        "baseline_planned_import_count": sum(row.get("baseline_status") == "planned_import" for row in baseline_rows),
        "baseline_blocked_missing_authority_count": sum(row.get("baseline_status") == "blocked_missing_authority" for row in baseline_rows),
        "admin_resolution_count": len(admin_by_id),
        "admin_resolution_in_original_gap_count": len(set(admin_by_id) & original_gap),
        "requires_human_review_count": len(review_candidates),
        "batch_safe_cohort_count": sum(row["batch_approval_safe"] == "true" for row in cohorts),
        "batch_safe_parent_count": low_risk_parents,
        "manual_review_count": manual_count,
        "duplicate_record_id_count": 0,
        "restricted_authority_count": counts["restricted_authority_count"],
        "pending_authority_count": counts["pending_authority_count"],
        "excluded_parent_authority_count": counts["excluded_parent_authority_count"],
        "approved_url_field_count": counts["approved_url_field_count"],
        "formal_vault_record_count": len(vault_ids),
        "formal_index_record_count": len(index_ids),
        "gap_internal_count": sum(
            _text(merchant_by_id[record_id].get("data_classification")) == "internal"
            for record_id in original_gap
        ),
        "gap_restricted_count": sum(
            _text(merchant_by_id[record_id].get("data_classification")) == "restricted"
            for record_id in original_gap
        ),
        "gap_pending_count": sum(
            _text(merchant_by_id[record_id].get("data_classification")) == "pending"
            for record_id in original_gap
        ),
        "validation_error_count": len(errors),
        "validation_warning_count": len(warnings),
        "formal_data_modified": False,
        "output_dir": str(output_dir),
    }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_outputs(output_dir)
    _write_csv(output_dir / OUTPUT_FILENAMES[1], reconciliation)
    _write_csv(output_dir / OUTPUT_FILENAMES[2], template_rows, REVIEW_TEMPLATE_COLUMNS)
    _write_csv(output_dir / OUTPUT_FILENAMES[3], evidence_rows, EVIDENCE_COLUMNS)
    _write_csv(output_dir / OUTPUT_FILENAMES[4], cohorts)
    _write_csv(output_dir / OUTPUT_FILENAMES[6], manual_rows, MANUAL_REVIEW_COLUMNS)
    _write_csv(output_dir / OUTPUT_FILENAMES[7], admin_coverage)
    _write_csv(output_dir / OUTPUT_FILENAMES[8], legacy_coverage)
    _write_csv(output_dir / OUTPUT_FILENAMES[11], errors, ISSUE_COLUMNS)
    _write_csv(output_dir / OUTPUT_FILENAMES[12], warnings, ISSUE_COLUMNS)
    (output_dir / OUTPUT_FILENAMES[0]).write_text(
        _summary_markdown(summary, cohorts, reconciliation), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[5]).write_text(
        _batch_confirmation_markdown(cohorts), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[9]).write_text(_decision_effect_matrix(), encoding="utf-8")
    (output_dir / OUTPUT_FILENAMES[10]).write_text(
        _coverage_simulation_markdown(summary), encoding="utf-8"
    )
    (output_dir / OUTPUT_FILENAMES[13]).write_text(
        _next_prerequisites_markdown(summary), encoding="utf-8"
    )

    protected_after = {label: _hash_path(path) for label, path in inputs.items()}
    if protected_before != protected_after:
        raise ParentAuthorityReviewError("a protected input or formal path changed")
    if Path(decision_store_path).exists():
        raise ParentAuthorityReviewError("formal Governance Decision Store was created")
    actual = sorted(
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and not path.name.startswith("._")
    )
    if actual != sorted(OUTPUT_FILENAMES):
        raise ParentAuthorityReviewError("authority review output contract is incomplete")
    if errors:
        raise ParentAuthorityReviewError(f"authority review validation failed with {len(errors)} error(s)")
    return summary


def _unique_merchants(rows: Sequence[dict]) -> Dict[str, dict]:
    result = {}
    duplicates = []
    for row in rows:
        record_id = _merchant_record_id(row)
        if record_id in result:
            duplicates.append(record_id)
        result[record_id] = row
    if duplicates:
        raise ParentAuthorityReviewError(
            "duplicate record_id in merchant parent source: " + ", ".join(sorted(set(duplicates)))
        )
    return result


def _legacy_merchant_decisions(rows, merchant_by_id):
    result = {}
    for row in rows:
        if row.get("record_type") != "merchant_case":
            continue
        record_id = f"{row.get('source_sheet', '').strip()}:r{row.get('source_row', '').strip()}"
        if record_id not in merchant_by_id:
            raise ParentAuthorityReviewError(f"legacy merchant decision has unknown parent: {record_id}")
        if record_id in result:
            raise ParentAuthorityReviewError(f"duplicate legacy merchant decision: {record_id}")
        result[record_id] = row
    return result


def _admin_resolutions(rows, merchant_by_id, errors):
    result = {}
    for row in rows:
        record_id = row.get("record_id", "").strip()
        if record_id not in merchant_by_id:
            errors.append(_issue("error", "unknown_admin_parent", record_id, "Admin resolution parent does not exist."))
            continue
        if record_id in result:
            errors.append(_issue("error", "duplicate_admin_resolution", record_id, "Admin resolution is duplicated."))
            continue
        if row.get("reviewer") != "Admin":
            errors.append(_issue("error", "invalid_admin_reviewer", record_id, "Existing resolution reviewer must remain Admin."))
        try:
            reviewed_at = datetime.fromisoformat(row.get("reviewed_at", ""))
            if reviewed_at.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(_issue("error", "invalid_admin_reviewed_at", record_id, "Existing Admin reviewed_at must be an ISO 8601 timestamp with timezone."))
        decision = row.get("proposed_review_decision", "")
        if decision not in ALLOWED_REVIEW_DECISIONS:
            errors.append(_issue("error", "invalid_admin_decision", record_id, "Admin resolution uses an invalid decision."))
        result[record_id] = row
    return result


def _assets_by_record(rows, merchant_by_id, errors):
    grouped = defaultdict(list)
    asset_ids = set()
    for row in rows:
        record_id = row.get("record_id", "")
        asset_id = row.get("asset_id", "")
        if asset_id in asset_ids:
            errors.append(_issue("error", "duplicate_asset_id", record_id, f"Duplicate asset_id: {asset_id}"))
        asset_ids.add(asset_id)
        if record_id not in merchant_by_id:
            errors.append(_issue("error", "unknown_asset_parent", record_id, f"Asset has no parent: {asset_id}"))
            continue
        grouped[record_id].append(row)
    return grouped


def _parent_conflicts(merchant, assets, brand_counts):
    flags = []
    brand = _text(merchant.get("brand_name"))
    if any(_text(asset.get("brand_name")) != brand for asset in assets):
        flags.append("asset_parent_brand_mismatch")
    if brand_counts[brand] > 1 or _truthy(merchant.get("same_brand_multiple_records")):
        flags.append("same_brand_multiple_records_requires_relationship_review")
    if _truthy(merchant.get("suspected_duplicate_review")):
        flags.append("suspected_duplicate_review")
    return sorted(set(flags))


def _build_evidence(record_id, merchant, assets, vault_ids, index_ids, conflicts, authority_status):
    asset_types = sorted({_text(asset.get("asset_type")) for asset in assets if asset.get("asset_type")})
    valid_url_count = sum(bool(_asset_urls(asset)) for asset in assets)
    missing = []
    for field in ("brand_name", "merchant_status", "content_tags", "sales_category_lv1", "sales_category_lv2"):
        if merchant.get(field) in (None, "", []):
            missing.append(field)
    entity_type = "partner" if _text(merchant.get("merchant_status")) == "合作夥伴" else "merchant"
    if entity_type == "merchant" and not _text(merchant.get("merchant_handle")):
        missing.append("merchant_handle")
    governance_flags = sorted(
        set(_string_list(merchant.get("governance_issue_types")))
        | set(_string_list(merchant.get("governance_risk_reasons")))
    )
    invalid_assets = [asset for asset in assets if _text(asset.get("invalid_asset_value"))]
    if invalid_assets:
        conflicts = sorted(set(conflicts) | {"invalid_asset_evidence"})
    return {
        "record_id": record_id,
        "brand_name": _text(merchant.get("brand_name")),
        "merchant_handle": _text(merchant.get("merchant_handle")),
        "merchant_status": _text(merchant.get("merchant_status")),
        "entity_type": entity_type,
        "source_sheet": _text(merchant.get("source_sheet")),
        "source_row": str(merchant.get("source_row", "")),
        "content_tags": _json(_string_list(merchant.get("content_tags"))),
        "asset_count": len(assets),
        "asset_types": _json(asset_types),
        "asset_titles": _json([_text(asset.get("asset_title")) for asset in assets]),
        "valid_asset_url_count": valid_url_count,
        "can_enter_vault": "not_authoritatively_decided",
        "can_enter_content_index": _bool(_truthy(merchant.get("can_enter_content_index"))),
        "can_external_reference": _bool(_truthy(merchant.get("can_quote_externally"))),
        "classification": _text(merchant.get("data_classification")),
        "source_status": _text(merchant.get("status")),
        "governance_flags": _json(governance_flags),
        "restricted_status": "false",
        "pending_status": "false",
        "current_vault_presence": _bool(record_id in vault_ids),
        "current_index_presence": _bool(record_id in index_ids),
        "existing_citation_eligibility": _bool(_truthy(merchant.get("can_quote_externally"))),
        "metadata_completeness": "complete" if not missing else "incomplete",
        "content_validity": "valid" if assets and valid_url_count == len(assets) and not invalid_assets else "requires_review",
        "metadata_missing_fields": _json(sorted(missing)),
        "conflict_flags": _json(conflicts),
        "existing_review_history": "none",
        "authority_status": authority_status,
    }


def _recommend_parent_decision(evidence):
    conflicts = json.loads(evidence["conflict_flags"])
    missing = json.loads(evidence["metadata_missing_fields"])
    governance = json.loads(evidence["governance_flags"])
    if conflicts or governance:
        decision = "manual_review"
        reason = "Conflicting or governance-sensitive evidence requires an individual Admin decision."
        confidence = "low"
    elif evidence["content_validity"] != "valid" or int(evidence["asset_count"]) == 0:
        decision = "exclude"
        reason = "No complete, valid child asset evidence is available for normal search."
        confidence = "high"
    elif evidence["can_enter_content_index"] != "true":
        decision = "exclude_from_content_index"
        reason = "Source governance facts do not permit this parent to enter the content index."
        confidence = "high"
    elif missing:
        decision = "enrich_metadata"
        reason = "Content evidence exists, but required metadata is incomplete."
        confidence = "medium"
    elif evidence["can_external_reference"] != "true":
        decision = "approve_internal_only"
        reason = "Content is usable internally but source governance facts prohibit external citation."
        confidence = "high"
    else:
        decision = "approve"
        reason = (
            "Source facts show complete public metadata, valid assets, content-index eligibility, "
            "external citation eligibility, and no governance conflict. Formal presence is corroborating evidence only."
        )
        confidence = "high"
    if decision not in ALLOWED_REVIEW_DECISIONS:
        raise ParentAuthorityReviewError(f"recommended decision is not allowed: {decision}")
    return {
        "recommended_review_decision": decision,
        "recommendation_reason": reason,
        "recommendation_confidence": confidence,
    }


def _build_cohorts(rows):
    grouped = defaultdict(list)
    for row in rows:
        signature = {
            "decision": row["recommended_review_decision"],
            "classification": row["classification"],
            "can_enter_content_index": row["can_enter_content_index"],
            "can_external_reference": row["can_external_reference"],
            "governance_flags": row["governance_flags"],
            "metadata_missing_fields": row["metadata_missing_fields"],
            "conflict_flags": row["conflict_flags"],
            "content_validity": row["content_validity"],
            "entity_type": row["entity_type"],
        }
        grouped[_json(signature)].append(row)
    cohorts = []
    for signature_json, members in sorted(grouped.items()):
        signature = json.loads(signature_json)
        record_ids = sorted((row["record_id"] for row in members), key=_record_sort_key)
        checksum = hashlib.sha256(_json({"signature": signature, "record_ids": record_ids}).encode()).hexdigest()
        exception_count = sum(
            row["conflict_flags"] != "[]" or row["recommended_review_decision"] == "manual_review"
            for row in members
        )
        safe = exception_count == 0
        cohorts.append(
            {
                "cohort_id": f"parent-cohort-{checksum[:12]}",
                "recommended_review_decision": signature["decision"],
                "record_count": len(members),
                "record_ids": "|".join(record_ids),
                "shared_evidence": signature_json,
                "shared_governance_effect": _governance_effect(signature["decision"]),
                "exception_count": exception_count,
                "cohort_checksum": checksum,
                "batch_approval_safe": _bool(safe),
                "blocker_reason": "" if safe else "Cohort contains mapping, governance, or manual-review exceptions.",
            }
        )
    return sorted(cohorts, key=lambda row: row["cohort_id"])


def _review_template_row(row, cohort):
    return {
        "cohort_id": cohort["cohort_id"],
        "record_id": row["record_id"],
        "brand_name": row["brand_name"],
        "merchant_handle": row["merchant_handle"],
        "merchant_status": row["merchant_status"],
        "entity_type": row["entity_type"],
        "source_sheet": row["source_sheet"],
        "source_row": row["source_row"],
        "content_tags": row["content_tags"],
        "asset_count": row["asset_count"],
        "asset_types": row["asset_types"],
        "valid_asset_url_count": row["valid_asset_url_count"],
        "current_vault_presence": row["current_vault_presence"],
        "current_index_presence": row["current_index_presence"],
        "can_enter_vault": row["can_enter_vault"],
        "can_enter_content_index": row["can_enter_content_index"],
        "can_external_reference": row["can_external_reference"],
        "governance_flags": row["governance_flags"],
        "metadata_missing_fields": row["metadata_missing_fields"],
        "conflict_flags": row["conflict_flags"],
        "authority_status": row["authority_status"],
        "recommended_review_decision": row["recommended_review_decision"],
        "recommendation_reason": row["recommendation_reason"],
        "recommendation_confidence": row["recommendation_confidence"],
        "batch_review_eligible": cohort["batch_approval_safe"],
        "final_review_decision": "",
        "reviewer": "",
        "reviewed_at": "",
        "notes": "",
    }


def _admin_coverage_rows(admin_rows, legacy_by_id, asset_resolution_rows):
    assets = defaultdict(list)
    for row in asset_resolution_rows:
        assets[row.get("record_id", "")].append(row)
    result = []
    for row in sorted(admin_rows, key=lambda item: _record_sort_key(item.get("record_id", ""))):
        record_id = row["record_id"]
        held = [item for item in assets.get(record_id, []) if item.get("proposed_asset_index_eligibility") == "hold"]
        result.append(
            {
                "record_id": record_id,
                "brand_name": row.get("brand_name", ""),
                "legacy_decision_exists": _bool(record_id in legacy_by_id),
                "legacy_review_decision": legacy_by_id.get(record_id, {}).get("review_decision", ""),
                "proposed_review_decision": row.get("proposed_review_decision", ""),
                "reviewer": row.get("reviewer", ""),
                "reviewed_at": row.get("reviewed_at", ""),
                "hold_asset_count": len(held),
                "hold_asset_search_eligibility": "|".join(sorted({item.get("proposed_asset_search_eligibility", "") for item in held})),
                "reopened_for_review": "false",
                "future_decision_store_event_required": "true",
            }
        )
    return result


def _legacy_coverage_rows(legacy_by_id, admin_by_id):
    return [
        {
            "record_id": record_id,
            "brand_name": row.get("brand_name", ""),
            "review_decision": row.get("review_decision", ""),
            "reviewer": row.get("reviewer", ""),
            "reviewed_at": row.get("reviewed_at", ""),
            "authority_status": "superseded_by_admin_resolution" if record_id in admin_by_id else "legacy_authority_confirmed",
            "requires_new_human_review": "false",
        }
        for record_id, row in sorted(legacy_by_id.items(), key=lambda item: _record_sort_key(item[0]))
    ]


def _conservation_counts(review_rows, asset_url_rows, resolution_asset_rows):
    excluded_assets = {
        row.get("asset_id", "")
        for row in resolution_asset_rows
        if row.get("proposed_asset_index_eligibility") in {"exclude", "hold"}
    }
    approved_fields = 0
    for row in asset_url_rows:
        if row.get("asset_id") in excluded_assets:
            continue
        approved_fields += int(
            row.get("field") in {"asset_url", "canonical_url"}
            and row.get("review_decision") == "approve"
        )
    return {
        "restricted_authority_count": sum(row.get("record_type") == "restricted_customer" for row in review_rows),
        "pending_authority_count": sum(row.get("record_type") == "pending_metric" for row in review_rows),
        "excluded_parent_authority_count": sum(
            row.get("record_type") == "merchant_case" and row.get("review_decision") == "exclude"
            for row in review_rows
        ),
        "approved_url_field_count": approved_fields,
    }


def _formal_vault_record_ids(vault_path):
    root = Path(vault_path) / "MKA"
    ids = set()
    for path in sorted(root.rglob("*.md")):
        if path.name.startswith("._"):
            continue
        metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        source_path = _text(metadata.get("source_path"))
        if source_path:
            ids.add(_normalize_record_id(source_path))
    return ids


def _formal_index_record_ids(db_path):
    connection = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        return {
            _normalize_record_id(row[0])
            for row in connection.execute("SELECT source_path FROM documents")
            if row[0]
        }
    finally:
        connection.close()


def _summary_markdown(summary, cohorts, reconciliation):
    statuses = Counter(row["authority_status"] for row in reconciliation)
    decisions = Counter(row["recommended_review_decision"] for row in _review_rows_from_cohorts(cohorts))
    return "\n".join(
        [
            "# Parent Baseline Decision Authority Review",
            "",
            f"Conclusion: **{summary['conclusion']}**",
            "",
            "## Reconciliation",
            f"- Merchant parents: {summary['merchant_parent_count']}",
            f"- Legacy merchant authority: {summary['legacy_merchant_authority_count']}",
            f"- Original authority gap: {summary['original_authority_gap_count']}",
            f"- Cross-check inventory: {summary['baseline_inventory_count']} rows = {summary['baseline_planned_import_count']} planned legacy imports + {summary['baseline_blocked_missing_authority_count']} blocked gaps",
            f"- Existing Admin resolutions: {summary['admin_resolution_count']}",
            f"- Admin resolutions inside original gap: {summary['admin_resolution_in_original_gap_count']}",
            f"- Parents requiring new human review: {summary['requires_human_review_count']}",
            f"- Authority status counts: `{_json(dict(sorted(statuses.items())))}`",
            "",
            "## Review Packet",
            f"- Batch-safe cohorts: {summary['batch_safe_cohort_count']}",
            f"- Batch-safe parents: {summary['batch_safe_parent_count']}",
            f"- Individual manual review: {summary['manual_review_count']}",
            f"- Recommended decision counts: `{_json(dict(sorted(decisions.items())))}`",
            f"- Gap classification: internal={summary['gap_internal_count']}, restricted={summary['gap_restricted_count']}, pending={summary['gap_pending_count']}",
            f"- Formal presence observed: Vault={summary['formal_vault_record_count']}, SQLite={summary['formal_index_record_count']}",
            "- Final decision, reviewer, reviewed_at, and notes are intentionally blank.",
            "- Formal Vault/SQLite presence is corroborating evidence, not decision authority.",
            "",
            "## Authority Source Classification",
            f"- A. Authoritative Human Decision: {summary['legacy_merchant_authority_count']} merchant Parent subjects have legacy authority; 5 of those have later Admin Resolution authority.",
            f"- B. Existing Governance Fact: {summary['merchant_parent_count']} merchant Parent source records supply recommendation evidence only.",
            f"- C. Formal System Presence: Vault={summary['formal_vault_record_count']}, SQLite={summary['formal_index_record_count']}; presence never creates authority.",
            f"- D. Derived Report: the Decision Store preview independently reports {summary['baseline_blocked_missing_authority_count']} missing-authority Parents and is used only as a cross-check.",
            "- E. New Admin Resolution Decision: 5 existing resolutions are preserved and excluded from this new review queue.",
            "",
            "## Conservation",
            f"- Restricted decisions retained: {summary['restricted_authority_count']}",
            f"- Pending decisions retained: {summary['pending_authority_count']}",
            f"- Excluded merchant decisions retained: {summary['excluded_parent_authority_count']}",
            f"- Approved URL fields referenced only: {summary['approved_url_field_count']}",
            "- Asset URL decisions and held/excluded asset eligibility were not changed.",
            "",
            "## Safety",
            "- No Parent decision was applied.",
            "- No Governance Decision Store was created.",
            "- Formal Vault, managed Vault, formal SQLite, and production renderer were not modified.",
            *[f"- `{plan_id}`: DO NOT CONFIRM" for plan_id in OLD_PLAN_IDS],
            "",
        ]
    )


def _review_rows_from_cohorts(cohorts):
    for cohort in cohorts:
        for _ in range(int(cohort["record_count"])):
            yield {"recommended_review_decision": cohort["recommended_review_decision"]}


def _batch_confirmation_markdown(cohorts):
    lines = [
        "# Parent Baseline Batch Confirmation Template",
        "",
        "This template is intentionally unconfirmed. Completing it does not apply a decision.",
        "",
    ]
    for cohort in cohorts:
        lines.extend(
            [
                f"## {cohort['cohort_id']}",
                f"- Cohort checksum: `{cohort['cohort_checksum']}`",
                f"- Recommended decision: `{cohort['recommended_review_decision']}`",
                f"- Parent count: {cohort['record_count']}",
                f"- Batch approval safe: {cohort['batch_approval_safe']}",
                "- Final review decision:",
                "- Reviewer:",
                "- Reviewed at:",
                "- Confirmation statement: I reviewed every Parent in this cohort against the shared evidence and approve the stated decision for each independent Parent event.",
                "",
            ]
        )
    return "\n".join(lines)


def _decision_effect_matrix():
    rows = [
        ("approve", "include", "include", "source governance", "eligible children only", "eligible", "source governance"),
        ("approve_internal_only", "include", "internal only", "no", "eligible children only", "internal only", "no"),
        ("exclude", "exclude", "exclude", "no", "exclude", "no", "no"),
        ("exclude_from_content_index", "may retain", "exclude", "no", "not searchable", "no", "no"),
        ("needs_update", "hold", "hold", "no", "hold", "no", "no"),
        ("enrich_metadata", "hold", "hold", "no", "hold until complete", "no", "no"),
        ("manual_review", "hold", "hold", "no", "hold", "no", "no"),
        ("deprecated", "archive", "exclude", "no", "exclude", "no", "no"),
    ]
    lines = [
        "# Parent Decision Effect Matrix",
        "",
        "Only canonical values from `ALLOWED_REVIEW_DECISIONS` are shown.",
        "",
        "| Decision | Vault | Content Index | External Reference | Child Assets | Search | Citation |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    lines.extend(
        [
            "",
            "Parent approval never overrides an explicit child asset hold or exclusion.",
            "Recommendations are not authoritative decisions until Admin completes and validates the review packet.",
            "",
        ]
    )
    return "\n".join(lines)


def _coverage_simulation_markdown(summary):
    gap = summary["requires_human_review_count"]
    after_batch = summary["manual_review_count"]
    return "\n".join(
        [
            "# Parent Authority Coverage Simulation",
            "",
            "No scenario below has been applied.",
            "",
            "## A. Before New Admin Review",
            f"- Legacy merchant Parent authorities: {summary['legacy_merchant_authority_count']}",
            f"- Existing Admin Resolution Parents: {summary['admin_resolution_count']} (all supersede legacy subjects; no double counting)",
            f"- Remaining Parent Authority Gap: {gap}",
            "",
            "## B. All Batch-safe Cohorts Approved",
            f"- Batch-reviewed Parents (simulation): {summary['batch_safe_parent_count']}",
            f"- Parents still requiring individual review: {after_batch}",
            f"- Remaining Parent Authority Gap: {after_batch}",
            "",
            "## C. Every Parent Receives Explicit Authority",
            "- Parent Authority Gap: 0",
            f"- Parent coverage: {summary['merchant_parent_count']}/{summary['merchant_parent_count']}",
            f"- Restricted decisions retained: {summary['restricted_authority_count']}",
            f"- Pending decisions retained: {summary['pending_authority_count']}",
            f"- Excluded Parent decisions retained: {summary['excluded_parent_authority_count']}",
            "- Existing Admin resolutions are not duplicated.",
            "- Legacy reviewer metadata remains unchanged; Admin is used only for newly completed human review rows.",
            "",
        ]
    )


def _next_prerequisites_markdown(summary):
    return "\n".join(
        [
            "# Next Decision Store Prerequisites",
            "",
            "1. Admin reviews every row in `baseline_parent_authority_review_template.csv` or explicitly confirms each batch-safe cohort checksum.",
            "2. Validate final decisions against the canonical Parent decision enum and require nonblank reviewer/reviewed_at.",
            "3. Recompute the authority gap from source inputs; expected theoretical gap is zero only after all rows are approved or individually resolved.",
            "4. Preserve the five existing Admin Resolution Events and all legacy reviewer metadata without duplication.",
            "5. Rebuild a new Decision Store plan with fresh checksums; do not reuse any prior plan ID.",
            "6. Keep 410 Asset URL field decisions reference-only and preserve the explicit held asset boundary.",
            "",
            f"Current unreviewed Parent count: {summary['requires_human_review_count']}.",
            "No Decision Store confirm or execute step is available in this packet.",
            "",
        ]
    )


def _validate_inventory_claim(rows, merchant_count, review_count, warnings):
    claims = {row.get("source"): row for row in rows}
    if not claims:
        warnings.append(_issue("warning", "empty_source_inventory", "", "Decision source inventory is empty."))
    if merchant_count <= 0 or review_count <= 0:
        warnings.append(_issue("warning", "empty_authority_inputs", "", "Merchant or review source is empty."))


def _asset_urls(row):
    value = row.get("source_urls", "")
    try:
        urls = json.loads(value) if isinstance(value, str) and value else []
    except json.JSONDecodeError:
        urls = []
    return [url for url in urls if isinstance(url, str) and url.strip()]


def _string_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value.strip()] if value.strip() else []
        if isinstance(parsed, list):
            return [_text(item) for item in parsed if _text(item)]
    return [_text(value)] if _text(value) else []


def _governance_effect(decision):
    return {
        "approve": "Vault/index eligible; external citation remains governed by source facts; child holds remain binding.",
        "approve_internal_only": "Internal Vault/index only; no external citation; child holds remain binding.",
        "exclude": "No Vault, index, search, or citation.",
        "exclude_from_content_index": "May retain an audit/Vault record; excluded from content search and citation.",
        "needs_update": "Blocked until a later reviewed superseding decision.",
        "enrich_metadata": "Blocked until metadata is enriched and reviewed.",
        "manual_review": "No apply effect until an individual Admin decision exists.",
    }.get(decision, "Decision-specific governance rules apply; no automatic child eligibility.")


def _merchant_record_id(row):
    sheet = _text(row.get("source_sheet"))
    source_row = row.get("source_row")
    if not sheet or source_row in (None, ""):
        raise ParentAuthorityReviewError("merchant parent is missing source identity")
    return f"{sheet}:r{source_row}"


def _normalize_record_id(value):
    text = _text(value)
    if ":r" in text:
        return text
    if ":" in text:
        sheet, row = text.rsplit(":", 1)
        return f"{sheet}:r{row}"
    return text


def _record_sort_key(value):
    record_id = value[0] if isinstance(value, tuple) else value
    try:
        prefix, row = record_id.rsplit(":r", 1)
        return prefix, int(row)
    except (ValueError, AttributeError):
        return str(record_id), 0


def _assert_safe_output(output_dir, protected_paths):
    output = output_dir.resolve()
    for path in protected_paths:
        resolved = Path(path).resolve()
        if output == resolved or output in resolved.parents or resolved in output.parents:
            raise ParentAuthorityReviewError("output directory overlaps a protected input")


def _clear_outputs(output_dir):
    for name in OUTPUT_FILENAMES:
        path = output_dir / name
        if path.exists():
            path.unlink()


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json_list(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ParentAuthorityReviewError(f"expected JSON list: {path}")
    return value


def _write_csv(path, rows, fieldnames=None):
    fieldnames = list(fieldnames or (list(rows[0]) if rows else ISSUE_COLUMNS))
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _hash_path(path):
    path = Path(path)
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.name.startswith("._")):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _issue(severity, code, record_id, message):
    return {"severity": severity, "code": code, "record_id": record_id, "message": message}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value):
    return "" if value is None else str(value).strip()


def _truthy(value):
    if isinstance(value, bool):
        return value
    return _text(value).casefold() in {"true", "1", "yes"}


def _bool(value):
    return "true" if value else "false"
