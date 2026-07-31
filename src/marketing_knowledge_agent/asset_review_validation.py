from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlsplit

from .asset_metadata import NONCANONICAL_HOST_PATHS, SHORTENER_HOSTS, TRACKING_QUERY_KEYS
from .asset_metadata_preview import ENRICHMENT_COLUMNS
from .governance import (
    GovernanceIndex,
    RestrictedCustomerRecord,
    split_restricted_aliases,
)


ALLOWED_ASSET_REVIEW_DECISIONS = (
    "approve",
    "reject",
    "needs_update",
    "exclude_asset",
    "manual_review",
)
REVIEWED_FIELDS = ("asset_url", "canonical_url")
OUTPUT_FILENAMES = (
    "review_validation_summary.md",
    "review_validation_errors.csv",
    "review_validation_warnings.csv",
    "review_decision_status.csv",
    "apply_preview_eligibility.csv",
    "unresolved_manual_review.csv",
)
DECISION_COLUMNS = tuple(ENRICHMENT_COLUMNS) + (
    "review_decision",
    "reviewer",
    "reviewed_at",
    "notes",
)
INVENTORY_REQUIRED_COLUMNS = (
    "record_id",
    "asset_id",
    "brand_name",
    "asset_type",
    "asset_title",
    "invalid_asset_value",
)
ISSUE_COLUMNS = (
    "severity",
    "code",
    "row_number",
    "record_id",
    "asset_id",
    "field",
    "message",
)
STATUS_COLUMNS = (
    "record_id",
    "asset_id",
    "brand_name",
    "asset_type",
    "asset_title",
    "proposed_asset_url",
    "proposed_canonical_url",
    "asset_url_decision",
    "canonical_url_decision",
    "reviewer",
    "reviewed_at",
    "review_note",
    "eligibility",
    "reason_codes",
)
ELIGIBILITY_COLUMNS = (
    "record_id",
    "asset_id",
    "asset_type",
    "eligibility",
    "approved_fields",
    "rejected_fields",
    "reason_codes",
)

_DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@")
_CREDENTIAL_PATTERN = re.compile(
    r"(?:xox[baprs]-|xapp-|sk-[A-Za-z0-9]|bearer\s+[A-Za-z0-9._-]{12,}|api[_-]?key\s*[:=])",
    re.IGNORECASE,
)
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class AssetReviewValidationError(ValueError):
    """Raised when the asset review inputs cannot be validated safely."""


@dataclass(frozen=True)
class AssetReviewIssue:
    severity: str
    code: str
    row_number: Optional[int]
    record_id: str
    asset_id: str
    field: str
    message: str


def validate_asset_review_decisions(
    decisions_path: Path,
    inventory_path: Path,
    enrichment_path: Path,
    output_dir: Path,
    restricted_customers_path: Optional[Path] = None,
) -> dict:
    """Validate URL decisions and write reports without applying any value."""
    decisions_path = Path(decisions_path)
    inventory_path = Path(inventory_path)
    enrichment_path = Path(enrichment_path)
    output_dir = Path(output_dir)
    _assert_safe_output(output_dir, decisions_path, inventory_path, enrichment_path)

    inventory_rows, inventory_columns = _read_csv(inventory_path)
    enrichment_rows, enrichment_columns = _read_csv(enrichment_path)
    decision_rows, decision_columns = _read_csv(decisions_path)
    issues: List[AssetReviewIssue] = []
    _validate_columns(inventory_columns, INVENTORY_REQUIRED_COLUMNS, "inventory", issues)
    _validate_columns(enrichment_columns, ENRICHMENT_COLUMNS, "enrichment", issues)
    _validate_columns(decision_columns, DECISION_COLUMNS, "decisions", issues)

    inventory_by_asset = _inventory_index(inventory_rows, issues)
    enrichment_by_key = _enrichment_index(enrichment_rows, issues)
    decision_by_key, duplicate_keys = _decision_index(decision_rows, issues)
    expected_keys = set(enrichment_by_key)
    actual_keys = set(decision_by_key)
    missing_keys = expected_keys - actual_keys
    unexpected_keys = actual_keys - expected_keys
    for asset_id, field in sorted(missing_keys):
        _add_issue(issues, "error", "missing_review_row", None, "", asset_id, field, "Expected review row is missing.")
    for asset_id, field in sorted(unexpected_keys):
        row_number, row = decision_by_key[(asset_id, field)]
        _add_row_issue(issues, "error", "unexpected_review_row", row_number, row, "Review row has no matching enrichment proposal.")

    restricted_index = _load_restricted_index(restricted_customers_path)
    asset_flags: Dict[str, set] = defaultdict(set)
    in_scope_rows = []
    for row_number, row in enumerate(decision_rows, start=2):
        inventory = _validate_identity_and_proposal(
            row_number,
            row,
            inventory_by_asset,
            enrichment_by_key,
            issues,
        )
        if _text(row.get("field")) not in REVIEWED_FIELDS:
            continue
        in_scope_rows.append(row)
        _validate_decision_row(
            row_number,
            row,
            inventory,
            duplicate_keys,
            issues,
            asset_flags,
            restricted_index,
        )

    _validate_duplicate_canonical_urls(decision_by_key, issues, asset_flags)
    statuses = _build_asset_statuses(
        inventory_by_asset,
        decision_by_key,
        issues,
        asset_flags,
    )
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    summary = _build_summary(
        decisions_path,
        inventory_path,
        enrichment_path,
        output_dir,
        decision_rows,
        in_scope_rows,
        inventory_by_asset,
        statuses,
        errors,
        warnings,
        missing_keys,
        unexpected_keys,
        duplicate_keys,
    )
    _write_reports(output_dir, summary, errors, warnings, statuses)
    return summary


def _validate_decision_row(
    row_number: int,
    row: dict,
    inventory: Optional[dict],
    duplicate_keys: set,
    issues: List[AssetReviewIssue],
    asset_flags: Dict[str, set],
    restricted_index: Optional[GovernanceIndex],
) -> None:
    asset_id = _text(row.get("asset_id"))
    field = _text(row.get("field"))
    decision = _text(row.get("review_decision"))
    reviewer = _text(row.get("reviewer"))
    reviewed_at = _text(row.get("reviewed_at"))
    notes = _text(row.get("notes"))

    if (asset_id, field) in duplicate_keys:
        asset_flags[asset_id].add("duplicate_asset_field")
    if not decision:
        _add_row_issue(issues, "error", "missing_review_decision", row_number, row, "review_decision is blank.")
        asset_flags[asset_id].add("incomplete_review")
    elif decision not in ALLOWED_ASSET_REVIEW_DECISIONS:
        _add_row_issue(issues, "error", "invalid_review_decision", row_number, row, "review_decision is outside the allowed enum.")
        asset_flags[asset_id].add("invalid_decision")
    if not reviewer:
        _add_row_issue(issues, "error", "missing_reviewer", row_number, row, "reviewer is required for URL decisions.")
        asset_flags[asset_id].add("incomplete_review")
    if not reviewed_at:
        _add_row_issue(issues, "error", "missing_reviewed_at", row_number, row, "reviewed_at is required for URL decisions.")
        asset_flags[asset_id].add("incomplete_review")
    elif not _valid_iso_timestamp(reviewed_at):
        _add_row_issue(issues, "error", "invalid_reviewed_at", row_number, row, "reviewed_at must be an ISO date or datetime.")
        asset_flags[asset_id].add("invalid_decision")

    for column, value in (("reviewer", reviewer), ("notes", notes)):
        unsafe_code = _unsafe_human_input_code(value)
        if unsafe_code:
            _add_row_issue(issues, "error", "unsafe_human_input", row_number, row, f"{column} contains unsafe untrusted input ({unsafe_code}).")
            asset_flags[asset_id].add("invalid_decision")
    if decision in {"reject", "needs_update", "exclude_asset", "manual_review"} and not notes:
        _add_row_issue(issues, "error", "missing_review_note", row_number, row, "This decision requires a review note.")
        asset_flags[asset_id].add("incomplete_review")

    proposed = _text(row.get("proposed_value"))
    if decision == "approve":
        if not proposed:
            _add_row_issue(issues, "error", "approved_empty_value", row_number, row, "approve requires a non-empty proposed URL.")
            asset_flags[asset_id].add("missing_evidence")
        else:
            _validate_approved_url(row_number, row, field, proposed, issues, asset_flags)
    if decision in {"manual_review", "needs_update"}:
        asset_flags[asset_id].add("manual_review_required")

    if inventory is not None and _is_governance_only(inventory):
        asset_flags[asset_id].add("governance_blocked")
        if decision == "approve":
            _add_row_issue(issues, "error", "governance_approve_conflict", row_number, row, "Governance-only or invalid asset evidence cannot be approved.")
    if restricted_index is not None:
        restricted_text = "\n".join(
            value
            for value in (
                _text(inventory.get("brand_name")) if inventory else "",
                _text(inventory.get("asset_title")) if inventory else "",
                proposed,
                reviewer,
                notes,
            )
            if value
        )
        if restricted_text and restricted_index.check_text(restricted_text).blocked:
            _add_row_issue(issues, "error", "restricted_data_match", row_number, row, "Review row matches restricted governance data; sensitive values are not shown.")
            asset_flags[asset_id].add("governance_blocked")


def _validate_identity_and_proposal(
    row_number: int,
    row: dict,
    inventory_by_asset: Mapping[str, dict],
    enrichment_by_key: Mapping[Tuple[str, str], dict],
    issues: List[AssetReviewIssue],
) -> Optional[dict]:
    asset_id = _text(row.get("asset_id"))
    record_id = _text(row.get("record_id"))
    field = _text(row.get("field"))
    inventory = inventory_by_asset.get(asset_id)
    if not asset_id:
        _add_row_issue(issues, "error", "missing_asset_id", row_number, row, "asset_id is required.")
    elif inventory is None:
        _add_row_issue(issues, "error", "unknown_asset_id", row_number, row, "asset_id does not exist in inventory.")
    if not record_id:
        _add_row_issue(issues, "error", "missing_record_id", row_number, row, "record_id is required.")
    elif inventory is not None and record_id != _text(inventory.get("record_id")):
        _add_row_issue(issues, "error", "mismatched_record_asset_pair", row_number, row, "record_id does not match asset_id inventory identity.")
    if inventory is not None:
        if _text(row.get("asset_type")) != _text(inventory.get("asset_type")):
            _add_row_issue(issues, "error", "mismatched_asset_type", row_number, row, "asset_type does not match inventory identity.")
        if _text(row.get("brand_name")) != _text(inventory.get("brand_name")):
            _add_row_issue(issues, "error", "mismatched_brand_name", row_number, row, "brand_name does not match inventory identity.")
    expected = enrichment_by_key.get((asset_id, field))
    if expected is not None:
        for column in ENRICHMENT_COLUMNS:
            if _csv_text(row.get(column)) != _csv_text(expected.get(column)):
                _add_row_issue(issues, "error", "proposal_modified", row_number, row, "Original enrichment proposal fields must remain unchanged.")
                break
    return inventory


def _validate_approved_url(
    row_number: int,
    row: dict,
    field: str,
    value: str,
    issues: List[AssetReviewIssue],
    asset_flags: Dict[str, set],
) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _add_row_issue(issues, "error", "invalid_approved_url", row_number, row, "Approved URL must be an absolute HTTP(S) URL.")
        asset_flags[_text(row.get("asset_id"))].add("invalid_decision")
        return
    if parsed.username or parsed.password or _CONTROL_PATTERN.search(value) or _CREDENTIAL_PATTERN.search(value):
        _add_row_issue(issues, "error", "unsafe_approved_url", row_number, row, "Approved URL contains unsafe or credential-like content.")
        asset_flags[_text(row.get("asset_id"))].add("invalid_decision")
    if field != "canonical_url":
        return
    host = parsed.netloc.casefold()
    path = parsed.path.casefold()
    if host in SHORTENER_HOSTS or (host, path) in NONCANONICAL_HOST_PATHS:
        _add_row_issue(issues, "error", "noncanonical_url_approved", row_number, row, "Canonical URL cannot be a search, redirect, or short URL.")
        asset_flags[_text(row.get("asset_id"))].add("invalid_decision")
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if any(key.startswith("utm_") or key in TRACKING_QUERY_KEYS for key in query_keys):
        _add_row_issue(issues, "warning", "canonical_tracking_parameters", row_number, row, "Canonical URL contains tracking parameters and requires manual confirmation.")
        asset_flags[_text(row.get("asset_id"))].add("manual_review_required")


def _validate_duplicate_canonical_urls(
    decision_by_key: Mapping[Tuple[str, str], Tuple[int, dict]],
    issues: List[AssetReviewIssue],
    asset_flags: Dict[str, set],
) -> None:
    by_url: Dict[str, List[Tuple[int, dict]]] = defaultdict(list)
    for (asset_id, field), (row_number, row) in decision_by_key.items():
        if field != "canonical_url" or _text(row.get("review_decision")) != "approve":
            continue
        value = _text(row.get("proposed_value"))
        if value:
            by_url[value].append((row_number, row))
    for rows in by_url.values():
        asset_ids = {_text(row.get("asset_id")) for _, row in rows}
        if len(asset_ids) < 2:
            continue
        for row_number, row in rows:
            _add_row_issue(issues, "warning", "duplicate_canonical_url", row_number, row, "Approved canonical URL is shared by multiple assets and requires duplicate review.")
            asset_flags[_text(row.get("asset_id"))].add("manual_review_required")


def _build_asset_statuses(
    inventory_by_asset: Mapping[str, dict],
    decision_by_key: Mapping[Tuple[str, str], Tuple[int, dict]],
    issues: Sequence[AssetReviewIssue],
    asset_flags: Mapping[str, set],
) -> List[dict]:
    issues_by_asset: Dict[str, set] = defaultdict(set)
    for issue in issues:
        if issue.asset_id:
            issues_by_asset[issue.asset_id].add(issue.code)
    statuses = []
    for asset_id, inventory in sorted(inventory_by_asset.items()):
        rows = {
            field: decision_by_key.get((asset_id, field), (None, {}))[1]
            for field in REVIEWED_FIELDS
        }
        decisions = {field: _text(row.get("review_decision")) for field, row in rows.items()}
        flags = set(asset_flags.get(asset_id, set()))
        if "exclude_asset" in decisions.values() and any(
            decision and decision != "exclude_asset" for decision in decisions.values()
        ):
            flags.add("conflicting_decision")
            if "exclude_approve_conflict" not in issues_by_asset[asset_id]:
                row = next(row for row in rows.values() if row)
                _add_row_issue(
                    issues if isinstance(issues, list) else [],
                    "error",
                    "exclude_approve_conflict",
                    None,
                    row,
                    "exclude_asset cannot be combined with another URL decision for the same asset.",
                )
                issues_by_asset[asset_id].add("exclude_approve_conflict")

        error_codes = {
            issue.code for issue in issues if issue.asset_id == asset_id and issue.severity == "error"
        }
        restricted_match = "restricted_data_match" in error_codes
        if "governance_blocked" in flags or _is_governance_only(inventory):
            eligibility = "governance_blocked"
        elif "conflicting_decision" in flags or "exclude_approve_conflict" in error_codes:
            eligibility = "conflicting_decision"
        elif not all(decisions.values()):
            eligibility = "incomplete_review"
        elif error_codes:
            incomplete_codes = {
                "missing_review_decision",
                "missing_reviewer",
                "missing_reviewed_at",
                "missing_review_note",
                "approved_empty_value",
                "missing_review_row",
            }
            eligibility = (
                "incomplete_review"
                if error_codes & incomplete_codes
                else "invalid_decision"
            )
        elif "manual_review_required" in flags:
            eligibility = "manual_review_required"
        elif set(decisions.values()) == {"exclude_asset"} or set(decisions.values()) == {"reject"}:
            eligibility = "excluded"
        elif "needs_update" in decisions.values():
            eligibility = "incomplete_review"
        elif "manual_review" in decisions.values():
            eligibility = "manual_review_required"
        elif any(decision == "approve" for decision in decisions.values()):
            eligibility = "ready_for_apply_preview"
        else:
            eligibility = "excluded"

        reviewers = _unique(_text(row.get("reviewer")) for row in rows.values())
        reviewed_values = _unique(_text(row.get("reviewed_at")) for row in rows.values())
        notes = _unique(_text(row.get("notes")) for row in rows.values())
        approved_fields = [field for field, decision in decisions.items() if decision == "approve"]
        rejected_fields = [field for field, decision in decisions.items() if decision in {"reject", "exclude_asset"}]
        statuses.append(
            {
                "record_id": _text(inventory.get("record_id")),
                "asset_id": asset_id,
                "brand_name": "[restricted data redacted]" if restricted_match else _text(inventory.get("brand_name")),
                "asset_type": _text(inventory.get("asset_type")),
                "asset_title": "[restricted data redacted]" if restricted_match else _text(inventory.get("asset_title")),
                "proposed_asset_url": "[restricted data redacted]" if restricted_match else _text(rows["asset_url"].get("proposed_value")),
                "proposed_canonical_url": "[restricted data redacted]" if restricted_match else _text(rows["canonical_url"].get("proposed_value")),
                "asset_url_decision": decisions["asset_url"],
                "canonical_url_decision": decisions["canonical_url"],
                "reviewer": "[restricted data redacted]" if restricted_match else "|".join(reviewers),
                "reviewed_at": "|".join(reviewed_values),
                "review_note": "[restricted data redacted]" if restricted_match else "|".join(notes),
                "review_complete": all(
                    _text(row.get(column))
                    for row in rows.values()
                    for column in ("review_decision", "reviewer", "reviewed_at")
                ),
                "eligibility": eligibility,
                "approved_fields": "|".join(approved_fields),
                "rejected_fields": "|".join(rejected_fields),
                "reason_codes": "|".join(sorted(issues_by_asset[asset_id] | flags)),
            }
        )
    return statuses


def _inventory_index(rows: Sequence[dict], issues: List[AssetReviewIssue]) -> Dict[str, dict]:
    result = {}
    for row_number, row in enumerate(rows, start=2):
        asset_id = _text(row.get("asset_id"))
        if not asset_id:
            _add_row_issue(issues, "error", "missing_inventory_asset_id", row_number, row, "Inventory row has no asset_id.")
            continue
        if asset_id in result:
            _add_row_issue(issues, "error", "duplicate_inventory_asset_id", row_number, row, "Inventory contains duplicate asset_id.")
            continue
        result[asset_id] = row
    return result


def _enrichment_index(rows: Sequence[dict], issues: List[AssetReviewIssue]) -> Dict[Tuple[str, str], dict]:
    result = {}
    for row_number, row in enumerate(rows, start=2):
        key = (_text(row.get("asset_id")), _text(row.get("field")))
        if not all(key):
            _add_row_issue(issues, "error", "missing_enrichment_identity", row_number, row, "Enrichment row requires asset_id and field.")
            continue
        if key in result:
            _add_row_issue(issues, "error", "duplicate_enrichment_key", row_number, row, "Enrichment contains duplicate asset_id/field.")
            continue
        result[key] = row
    return result


def _decision_index(
    rows: Sequence[dict], issues: List[AssetReviewIssue]
) -> Tuple[Dict[Tuple[str, str], Tuple[int, dict]], set]:
    result = {}
    duplicates = set()
    for row_number, row in enumerate(rows, start=2):
        key = (_text(row.get("asset_id")), _text(row.get("field")))
        if not all(key):
            continue
        if key in result:
            duplicates.add(key)
            _add_row_issue(issues, "error", "duplicate_asset_field", row_number, row, "Duplicate asset_id/field review row.")
            continue
        result[key] = (row_number, row)
    return result, duplicates


def _build_summary(
    decisions_path: Path,
    inventory_path: Path,
    enrichment_path: Path,
    output_dir: Path,
    decision_rows: Sequence[dict],
    in_scope_rows: Sequence[dict],
    inventory_by_asset: Mapping[str, dict],
    statuses: Sequence[dict],
    errors: Sequence[AssetReviewIssue],
    warnings: Sequence[AssetReviewIssue],
    missing_keys: set,
    unexpected_keys: set,
    duplicate_keys: set,
) -> dict:
    eligibility = Counter(row["eligibility"] for row in statuses)
    completed_assets = sum(bool(row.get("review_complete")) for row in statuses)
    error_codes = Counter(issue.code for issue in errors)
    warning_codes = Counter(issue.code for issue in warnings)
    return {
        "decisions_path": str(decisions_path),
        "inventory_path": str(inventory_path),
        "enrichment_path": str(enrichment_path),
        "output_dir": str(output_dir),
        "total_review_rows": len(decision_rows),
        "in_scope_review_rows": len(in_scope_rows),
        "unique_asset_id_count": len(inventory_by_asset),
        "duplicate_asset_id_count": len(duplicate_keys),
        "missing_asset_id_count": sum(not _text(row.get("asset_id")) for row in decision_rows),
        "unknown_asset_id_count": error_codes["unknown_asset_id"],
        "mismatched_record_asset_pair_count": error_codes["mismatched_record_asset_pair"],
        "missing_review_row_count": len(missing_keys),
        "unexpected_extra_row_count": len(unexpected_keys),
        "completed_review_count": completed_assets,
        "incomplete_review_count": len(statuses) - completed_assets,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "conflict_count": eligibility["conflicting_decision"],
        "manual_review_count": eligibility["manual_review_required"],
        "excluded_count": eligibility["excluded"],
        "governance_blocked_count": eligibility["governance_blocked"],
        "ready_for_apply_preview_count": eligibility["ready_for_apply_preview"],
        "eligibility_counts": dict(sorted(eligibility.items())),
        "error_codes": dict(sorted(error_codes.items())),
        "warning_codes": dict(sorted(warning_codes.items())),
        "formal_index_modified": False,
        "vault_modified": False,
        "decisions_applied": False,
        "ready_for_apply_preview": bool(
            len(errors) == 0
            and eligibility["incomplete_review"] == 0
            and eligibility["invalid_decision"] == 0
            and eligibility["conflicting_decision"] == 0
            and eligibility["manual_review_required"] == 0
        ),
    }


def _write_reports(
    output_dir: Path,
    summary: dict,
    errors: Sequence[AssetReviewIssue],
    warnings: Sequence[AssetReviewIssue],
    statuses: Sequence[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "review_validation_errors.csv", [_issue_dict(issue) for issue in errors], ISSUE_COLUMNS)
    _write_csv(output_dir / "review_validation_warnings.csv", [_issue_dict(issue) for issue in warnings], ISSUE_COLUMNS)
    _write_csv(output_dir / "review_decision_status.csv", statuses, STATUS_COLUMNS)
    _write_csv(output_dir / "apply_preview_eligibility.csv", statuses, ELIGIBILITY_COLUMNS)
    unresolved = [
        row
        for row in statuses
        if row["eligibility"]
        in {
            "incomplete_review",
            "invalid_decision",
            "conflicting_decision",
            "manual_review_required",
        }
        or (
            row["eligibility"] == "governance_blocked"
            and any(
                code in row["reason_codes"]
                for code in ("missing_review_decision", "missing_reviewer", "missing_reviewed_at")
            )
        )
    ]
    _write_csv(output_dir / "unresolved_manual_review.csv", unresolved, STATUS_COLUMNS)
    (output_dir / "review_validation_summary.md").write_text(
        _render_summary(summary), encoding="utf-8"
    )


def _render_summary(summary: Mapping[str, object]) -> str:
    conclusion = (
        "A. Ready for Apply Preview"
        if summary["ready_for_apply_preview"] and summary["warning_count"] == 0
        else "B. Ready for Apply Preview with documented limitations"
        if summary["ready_for_apply_preview"]
        else "C. Requires review fixes before Apply Preview"
    )
    lines = [
        "# Asset URL & Identity Review Validation",
        "",
        "> Validation only. No decision was applied; Obsidian and the formal SQLite index were not modified.",
        "",
        f"- Conclusion: **{conclusion}**",
        f"- Total review rows: {summary['total_review_rows']}",
        f"- URL decision rows in scope: {summary['in_scope_review_rows']}",
        f"- Unique assets: {summary['unique_asset_id_count']}",
        f"- Completed asset reviews: {summary['completed_review_count']}",
        f"- Incomplete asset reviews: {summary['incomplete_review_count']}",
        f"- Validation errors: {summary['error_count']}",
        f"- Validation warnings: {summary['warning_count']}",
        f"- Decision conflicts: {summary['conflict_count']}",
        f"- Manual review required: {summary['manual_review_count']}",
        f"- Excluded: {summary['excluded_count']}",
        f"- Governance blocked: {summary['governance_blocked_count']}",
        f"- Ready for Apply Preview: {summary['ready_for_apply_preview_count']}",
        "",
        "## Conservation",
        "",
        f"- Missing review rows: {summary['missing_review_row_count']}",
        f"- Unexpected extra rows: {summary['unexpected_extra_row_count']}",
        f"- Duplicate asset/field rows: {summary['duplicate_asset_id_count']}",
        f"- Unknown asset IDs: {summary['unknown_asset_id_count']}",
        f"- Mismatched record/asset pairs: {summary['mismatched_record_asset_pair_count']}",
        "",
        "## Eligibility",
        "",
    ]
    for name, count in sorted(summary["eligibility_counts"].items()):
        lines.append(f"- `{name}`: {count}")
    lines.extend(["", "## Errors By Code", ""])
    lines.extend(_counter_lines(summary["error_codes"]))
    lines.extend(["", "## Warnings By Code", ""])
    lines.extend(_counter_lines(summary["warning_codes"]))
    lines.extend(
        [
            "",
            "## Safety Boundary",
            "",
            "- `ready_for_apply_preview` is validation-only and does not mean applied.",
            "- URL approval does not establish published_at, publication_status, interview fields, review_status, or partner identity.",
            "- Unsupported query constraints remain fail closed.",
            "- Fix every validation error before a separate Apply Preview sprint.",
        ]
    )
    return "\n".join(lines) + "\n"


def _read_csv(path: Path) -> Tuple[List[dict], List[str]]:
    path = Path(path)
    if not path.is_file():
        raise AssetReviewValidationError(f"required validation input does not exist: {path}")
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise AssetReviewValidationError(f"validation input has no CSV header: {path}")
            return list(reader), list(reader.fieldnames)
    except UnicodeDecodeError as exc:
        raise AssetReviewValidationError(f"validation input is not valid UTF-8: {path}") from exc


def _validate_columns(
    actual: Sequence[str], required: Sequence[str], source: str, issues: List[AssetReviewIssue]
) -> None:
    for column in required:
        if column not in actual:
            _add_issue(issues, "error", "missing_column", None, "", "", "", f"{source} is missing required column: {column}")


def _load_restricted_index(path: Optional[Path]) -> Optional[GovernanceIndex]:
    if path is None or not Path(path).is_file():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetReviewValidationError("restricted governance preview could not be loaded safely") from exc
    if not isinstance(payload, list):
        raise AssetReviewValidationError("restricted governance preview must be a JSON array")
    records = []
    for item in payload:
        if not isinstance(item, dict) or not _text(item.get("brand_name")):
            continue
        aliases = item.get("restricted_aliases")
        records.append(
            RestrictedCustomerRecord(
                brand_name=_text(item.get("brand_name")),
                website_url=_text(item.get("website_url")) or None,
                merchant_handle=_text(item.get("merchant_handle")) or None,
                restricted_aliases=(
                    [str(value) for value in aliases if value]
                    if isinstance(aliases, list)
                    else split_restricted_aliases(_text(item.get("brand_name")))
                ),
            )
        )
    return GovernanceIndex(records)


def _assert_safe_output(output_dir: Path, *sources: Path) -> None:
    output = output_dir.resolve()
    source_paths = {path.resolve() for path in sources}
    if output in source_paths or any(source == output or output in source.parents for source in source_paths):
        raise AssetReviewValidationError("validation output must not overwrite or contain source files")
    parts = {part.casefold() for part in output.parts}
    if ".mka" in parts or "obsidian_vault" in parts:
        raise AssetReviewValidationError("validation reports cannot be written to the formal index or Obsidian Vault")


def _is_governance_only(inventory: Mapping[str, object]) -> bool:
    return bool(_text(inventory.get("invalid_asset_value")) or not _text(inventory.get("asset_title")))


def _valid_iso_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _unsafe_human_input_code(value: str) -> str:
    if not value:
        return ""
    if value.startswith(_DANGEROUS_CSV_PREFIXES):
        return "csv_formula"
    if _CREDENTIAL_PATTERN.search(value):
        return "credential_pattern"
    if _CONTROL_PATTERN.search(value):
        return "control_character"
    if "../" in value or "..\\" in value:
        return "path_traversal"
    return ""


def _add_row_issue(
    issues: List[AssetReviewIssue],
    severity: str,
    code: str,
    row_number: Optional[int],
    row: Mapping[str, object],
    message: str,
) -> None:
    _add_issue(
        issues,
        severity,
        code,
        row_number,
        _text(row.get("record_id")),
        _text(row.get("asset_id")),
        _text(row.get("field")),
        message,
    )


def _add_issue(
    issues: List[AssetReviewIssue],
    severity: str,
    code: str,
    row_number: Optional[int],
    record_id: str,
    asset_id: str,
    field: str,
    message: str,
) -> None:
    issues.append(AssetReviewIssue(severity, code, row_number, record_id, asset_id, field, message))


def _issue_dict(issue: AssetReviewIssue) -> dict:
    return {
        "severity": issue.severity,
        "code": issue.code,
        "row_number": "" if issue.row_number is None else issue.row_number,
        "record_id": issue.record_id,
        "asset_id": issue.asset_id,
        "field": issue.field,
        "message": issue.message,
    }


def _write_csv(path: Path, rows: Sequence[dict], columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _safe_csv_value(row.get(column)) for column in columns})


def _safe_csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=isinstance(value, dict))
    else:
        text = str(value)
    if _CREDENTIAL_PATTERN.search(text):
        return "[redacted sensitive input]"
    if _CONTROL_PATTERN.search(text) or "../" in text or "..\\" in text:
        return "[unsafe input redacted]"
    if text.startswith(_DANGEROUS_CSV_PREFIXES):
        return "[unsafe input redacted]"
    return text


def _counter_lines(values: Mapping[str, int]) -> List[str]:
    return [f"- `{key}`: {value}" for key, value in sorted(values.items())] or ["- None"]


def _unique(values: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _csv_text(value: object) -> str:
    return "" if value is None else str(value)
