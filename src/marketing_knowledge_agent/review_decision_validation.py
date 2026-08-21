from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, get_args

from .models import AllowedExposureChannel
from .record_identity_lineage import describe_lineage_status, resolve_preview_lineage
from .review_template import (
    REVIEW_COLUMNS,
    ReviewTemplateError,
    build_expected_review_rows,
    load_preview_records,
)


BOOLEAN_COLUMNS = [
    "can_enter_vault",
    "can_enter_content_index",
    "can_enter_governance_table",
    "can_quote_externally",
]

ALLOWED_REVIEW_DECISIONS = {
    "approve",
    "approve_internal_only",
    "deprecated",
    "enrich_metadata",
    "enter_governance_table_only",
    "exclude",
    "exclude_from_content_index",
    "keep_all_records",
    "keep_internal_only",
    "manual_review",
    "needs_update",
    "restricted_use_only",
    "review_identity_mapping",
}
ALLOWED_FINAL_STATUSES = {
    "review_required",
    "restricted",
    "pending_review",
    "approved",
    "excluded",
    "internal_only",
}
ALLOWED_EXPOSURE_CHANNELS = set(get_args(AllowedExposureChannel))
VALIDATION_ERROR_FILENAME = "review_decisions_validation_errors.csv"
VALIDATION_WARNING_FILENAME = "review_decisions_validation_warnings.csv"
ISSUE_CSV_COLUMNS = [
    "severity",
    "rule_id",
    "row_number",
    "source_sheet",
    "source_row",
    "record_type",
    "review_decision",
    "message",
]


class ReviewDecisionValidationError(ValueError):
    """Raised when a review decision CSV cannot be validated."""


@dataclass
class ValidationIssue:
    severity: str
    row_number: Optional[int]
    source_sheet: str
    source_row: str
    record_type: str
    review_decision: str
    code: str
    rule_id: str
    message: str


def validate_review_decisions(
    decisions_path: Path,
    output_path: Path,
    preview_dir: Optional[Path] = None,
) -> dict:
    rows, fieldnames = _read_decision_rows(decisions_path)
    issues: List[ValidationIssue] = []
    preview_records: Optional[Dict[str, List[dict]]] = None

    missing_columns = [column for column in REVIEW_COLUMNS if column not in fieldnames]
    for column in missing_columns:
        issues.append(
            ValidationIssue(
                severity="error",
                row_number=None,
                source_sheet="",
                source_row="",
                record_type="",
                review_decision="",
                code="missing_column",
                rule_id="missing_column",
                message=f"Required column is missing: {column}",
            )
        )

    _validate_rows(rows, issues)
    if preview_dir is not None:
        preview_records = _load_preview_records(preview_dir, issues)
        if preview_records is not None:
            _validate_against_preview(preview_records, rows, issues)

    summary = _build_summary(decisions_path, output_path, preview_dir, rows, issues, preview_records)
    # Read-only analysis: a lineage mismatch is reported here, never enforced. Blocking validation
    # would remove the very tool needed to analyse a new workbook safely before rebinding.
    summary["row_v1_lineage"] = (
        resolve_preview_lineage(preview_dir) if preview_dir is not None else None
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_validation_report(summary, issues), encoding="utf-8")
    _write_issue_csv(Path(summary["errors_output_path"]), [issue for issue in issues if issue.severity == "error"])
    _write_issue_csv(Path(summary["warnings_output_path"]), [issue for issue in issues if issue.severity == "warning"])
    return summary


def render_validation_report(summary: dict, issues: List[ValidationIssue]) -> str:
    lines = [
        "# Review Decisions Validation",
        "",
        f"- Decision CSV: `{summary['decisions_path']}`",
        f"- Preview dir: `{summary['preview_dir'] or 'not checked'}`",
        f"- Summary output: `{summary['output_path']}`",
        f"- Errors CSV: `{summary['errors_output_path']}`",
        f"- Warnings CSV: `{summary['warnings_output_path']}`",
        f"- Total decision rows: {summary['total_rows']}",
        f"- Validation errors: {summary['error_count']}",
        f"- Validation warnings: {summary['warning_count']}",
        f"- Valid for next preview step: {_yes_no(summary['error_count'] == 0)}",
        f"- Blank review_decision rows: {summary['blank_review_decision_count']}",
        f"- Blank reviewer rows: {summary['blank_reviewer_count']}",
        f"- Blank reviewed_at rows: {summary['blank_reviewed_at_count']}",
        "",
        "## Counts By Record Type",
        "",
    ]
    lines.extend(_render_counter(summary["record_type_counts"]))
    lines.extend(["", "## Counts By Review Decision", ""])
    lines.extend(_render_counter(summary["review_decision_counts"]))
    lines.extend(["", "## Counts By Suggested Action", ""])
    lines.extend(_render_counter(summary["suggested_action_counts"]))
    lines.extend(["", "## Counts By Rule", ""])
    lines.extend(_render_counter(summary["rule_counts"]))
    lines.extend(["", "## Info Signals", ""])
    if summary["info_signals"]:
        for item in summary["info_signals"]:
            lines.append(f"- `{item['id']}`: {item['message']}")
    else:
        lines.append("- None")
    lines.extend(["", "## Issues", ""])
    if issues:
        lines.extend(
            [
                "| severity | row | record | rule_id | message |",
                "| --- | ---: | --- | --- | --- |",
            ]
        )
        for issue in issues:
            record = f"{issue.record_type} {issue.source_sheet}#{issue.source_row}".strip()
            row_number = "" if issue.row_number is None else str(issue.row_number)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(issue.severity),
                        _md_cell(row_number),
                        _md_cell(record),
                        _md_cell(issue.rule_id),
                        _md_cell(issue.message),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- No validation errors or warnings.")
    if summary.get("row_v1_lineage") is not None:
        lines.extend(["", "## Record Identity Lineage", ""])
        lines.extend(describe_lineage_status(summary["row_v1_lineage"]))
    lines.extend(
        [
            "",
            "## Next Step Notes",
            "",
            "- This command only validates the human decision CSV.",
            "- Decisions have not been applied.",
            "- Obsidian has not been synced.",
            "- No formal content index has been built.",
            "- Fix validation errors before any future `apply-review-decisions` preview step.",
        ]
    )
    return "\n".join(lines) + "\n"


def _read_decision_rows(path: Path) -> Tuple[List[dict], List[str]]:
    path = Path(path)
    if not path.exists():
        raise ReviewDecisionValidationError(f"review decision CSV not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ReviewDecisionValidationError(f"review decision CSV has no header: {path}")
        return list(reader), list(reader.fieldnames)


def _validate_rows(rows: List[dict], issues: List[ValidationIssue]) -> None:
    seen_keys: Counter = Counter()
    for index, row in enumerate(rows, start=2):
        key = _row_key(row)
        if all(key):
            seen_keys[key] += 1
        _validate_required_identity(row, index, issues)
        _validate_required_review_fields(row, index, issues)
        _validate_review_decision(row, index, issues)
        _validate_final_status(row, index, issues)
        _validate_exposure_channels(row, index, issues)
        _validate_boolean_columns(row, index, issues)
        _validate_reviewer_fields(row, index, issues)
        _validate_record_policy(row, index, issues)

    for key, count in seen_keys.items():
        if count <= 1:
            continue
        issues.append(
            ValidationIssue(
                severity="error",
                row_number=None,
                source_sheet=key[0],
                source_row=key[1],
                record_type=key[2],
                review_decision="",
                code="duplicate_review_row",
                rule_id="duplicate_review_row",
                message=f"Review decision CSV has {count} rows for the same preview record.",
            )
        )


def _load_preview_records(preview_dir: Path, issues: List[ValidationIssue]) -> Optional[Dict[str, List[dict]]]:
    try:
        return load_preview_records(preview_dir)
    except ReviewTemplateError as exc:
        issues.append(
            ValidationIssue(
                severity="error",
                row_number=None,
                source_sheet="",
                source_row="",
                record_type="",
                review_decision="",
                code="preview_load_failed",
                rule_id="preview_load_failed",
                message=str(exc),
            )
        )
        return None


def _validate_against_preview(preview: Dict[str, List[dict]], rows: List[dict], issues: List[ValidationIssue]) -> None:
    expected_rows = build_expected_review_rows(preview)
    expected_keys = {_row_key(row) for row in expected_rows}
    actual_keys = {_row_key(row) for row in rows if all(_row_key(row))}

    for key in sorted(expected_keys - actual_keys):
        issues.append(
            ValidationIssue(
                severity="error",
                row_number=None,
                source_sheet=key[0],
                source_row=key[1],
                record_type=key[2],
                review_decision="",
                code="missing_expected_review_row",
                rule_id="missing_expected_review_row",
                message="Decision CSV is missing a row required by the current preview JSON.",
            )
        )
    for key in sorted(actual_keys - expected_keys):
        issues.append(
            ValidationIssue(
                severity="error",
                row_number=None,
                source_sheet=key[0],
                source_row=key[1],
                record_type=key[2],
                review_decision="",
                code="unexpected_review_row",
                rule_id="unexpected_review_row",
                message="Decision CSV contains a row that is not required by the current preview JSON.",
            )
        )


def _validate_required_identity(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    for column in ("source_sheet", "source_row", "record_type"):
        if not _text(row.get(column)):
            _add_row_issue(row, row_number, issues, "error", "missing_identity", f"Missing `{column}`.")


def _validate_required_review_fields(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    if not _text(row.get("suggested_action")):
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "missing_suggested_action",
            "`suggested_action` is blank.",
        )
    if not _text(row.get("final_status")):
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "missing_final_status",
            "`final_status` is blank.",
        )


def _validate_review_decision(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    decision = _text(row.get("review_decision"))
    if not decision:
        _add_row_issue(row, row_number, issues, "error", "missing_review_decision", "`review_decision` is blank.")
        return
    if decision not in ALLOWED_REVIEW_DECISIONS:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "invalid_review_decision",
            f"`review_decision` is not allowed: {decision}",
        )


def _validate_final_status(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    final_status = _text(row.get("final_status"))
    if final_status and final_status not in ALLOWED_FINAL_STATUSES:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "invalid_final_status",
            f"`final_status` is not allowed: {final_status}",
        )


def _validate_exposure_channels(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    for channel in _split_pipe(row.get("allowed_exposure_channels")):
        if channel not in ALLOWED_EXPOSURE_CHANNELS:
            _add_row_issue(
                row,
                row_number,
                issues,
                "error",
                "invalid_exposure_channel",
                f"`allowed_exposure_channels` contains an unknown value: {channel}",
            )


def _validate_boolean_columns(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    for column in BOOLEAN_COLUMNS:
        if _bool_value(row.get(column)) is None:
            _add_row_issue(
                row,
                row_number,
                issues,
                "error",
                "invalid_boolean",
                f"`{column}` must be true or false.",
            )


def _validate_reviewer_fields(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    if not _text(row.get("reviewer")):
        _add_row_issue(row, row_number, issues, "warning", "blank_reviewer", "`reviewer` is blank.")
    reviewed_at = _text(row.get("reviewed_at"))
    if not reviewed_at:
        _add_row_issue(row, row_number, issues, "warning", "blank_reviewed_at", "`reviewed_at` is blank.")
    elif not _is_iso_date(reviewed_at):
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "invalid_reviewed_at",
            "`reviewed_at` must be an ISO date in YYYY-MM-DD format.",
        )


def _validate_record_policy(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    record_type = _text(row.get("record_type"))
    issue_types = set(_split_pipe(row.get("issue_type")))
    decision = _text(row.get("review_decision"))
    can_enter_content_index = _bool_value(row.get("can_enter_content_index"))
    can_enter_governance_table = _bool_value(row.get("can_enter_governance_table"))
    can_quote_externally = _bool_value(row.get("can_quote_externally"))
    channels = _split_pipe(row.get("allowed_exposure_channels"))
    final_status = _text(row.get("final_status"))

    if record_type == "restricted_customer":
        _require_decision(row, row_number, issues, "enter_governance_table_only")
        _require_bool(row, row_number, issues, "can_enter_vault", False)
        _require_bool(row, row_number, issues, "can_enter_content_index", False)
        _require_bool(row, row_number, issues, "can_enter_governance_table", True)
        _require_bool(row, row_number, issues, "can_quote_externally", False)
        _require_text(row, row_number, issues, "final_status", "restricted")
        if can_enter_governance_table is False:
            _add_row_issue(
                row,
                row_number,
                issues,
                "error",
                "governance_table_only_without_governance_table",
                "`enter_governance_table_only` requires can_enter_governance_table=true.",
                rule_id="CR-7",
            )
        return

    if record_type == "pending_metric":
        _require_decision(row, row_number, issues, "keep_internal_only")
        _require_bool(row, row_number, issues, "can_enter_vault", False)
        _require_bool(row, row_number, issues, "can_enter_content_index", False)
        _require_bool(row, row_number, issues, "can_enter_governance_table", False)
        _require_bool(row, row_number, issues, "can_quote_externally", False)
        _require_text(row, row_number, issues, "final_status", "pending_review")
        return

    if "missing_allowed_exposure_channels" in issue_types:
        _require_bool(row, row_number, issues, "can_enter_content_index", False)
        _require_bool(row, row_number, issues, "can_quote_externally", False)

    if "no_valid_content_asset" in issue_types:
        _require_bool(row, row_number, issues, "can_enter_content_index", False)
        _require_bool(row, row_number, issues, "can_quote_externally", False)

    if "suspected_duplicate_review" in issue_types and decision != "manual_review":
        _add_row_issue(
            row,
            row_number,
            issues,
            "warning",
            "suspected_duplicate_not_manual_review",
            "`suspected_duplicate_review` should normally stay as manual_review until reviewed.",
        )

    informational_multi_record = {"same_brand_multiple_records", "same_handle_multiple_records"}
    only_informational = bool(issue_types) and issue_types.issubset(informational_multi_record)
    if only_informational and decision in {"exclude", "deprecated", "exclude_from_content_index"}:
        _add_row_issue(
            row,
            row_number,
            issues,
            "warning",
            "multi_record_marked_for_removal",
            "same brand / same handle records are informational and should not be removed only for that reason.",
        )

    if decision == "exclude" and (can_enter_content_index is True or can_quote_externally is True):
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "exclude_conflicts_with_index_or_quote",
            "`exclude` requires can_enter_content_index=false and can_quote_externally=false.",
            rule_id="CR-5",
        )

    if decision == "exclude_from_content_index" and can_enter_content_index is True:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "exclude_from_content_index_conflict",
            "`exclude_from_content_index` requires can_enter_content_index=false.",
            rule_id="CR-6",
        )

    if decision == "enter_governance_table_only" and can_enter_governance_table is False:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "governance_table_only_without_governance_table",
            "`enter_governance_table_only` requires can_enter_governance_table=true.",
            rule_id="CR-7",
        )

    if (
        decision == "enter_governance_table_only"
        and record_type != "restricted_customer"
        and (can_enter_content_index is True or can_quote_externally is True)
    ):
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "governance_table_only_conflicts_with_content_or_quote",
            "`enter_governance_table_only` cannot enter content index or be quoted externally.",
            rule_id="CR-8",
        )

    if decision in {"keep_internal_only", "approve_internal_only"} and can_quote_externally is True:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "internal_only_external_quote_conflict",
            "`keep_internal_only` and `approve_internal_only` require can_quote_externally=false.",
            rule_id="CR-9",
        )

    if decision == "restricted_use_only" and not channels:
        if can_quote_externally is True:
            _add_row_issue(
                row,
                row_number,
                issues,
                "error",
                "restricted_use_external_quote_without_channels",
                "`restricted_use_only` cannot be externally quoted without allowed_exposure_channels.",
                rule_id="CR-10",
            )
        else:
            _add_row_issue(
                row,
                row_number,
                issues,
                "warning",
                "restricted_use_without_channels",
                "`restricted_use_only` has no allowed_exposure_channels; confirm restrictions are captured elsewhere.",
                rule_id="CR-10",
            )

    if decision == "approve" and can_quote_externally is False:
        _add_row_issue(
            row,
            row_number,
            issues,
            "warning",
            "approve_without_external_quote",
            "`approve` with can_quote_externally=false is allowed but should be confirmed.",
            rule_id="CR-11",
        )

    if decision == "approve" and "no_valid_content_asset" in issue_types:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "approve_no_valid_content_asset",
            "`approve` is not allowed for rows with no_valid_content_asset.",
            rule_id="CR-12",
        )

    if decision in {"needs_update", "enrich_metadata", "manual_review"} and final_status == "approved":
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "unfinished_decision_marked_approved",
            "Unfinished review decisions cannot have final_status=approved.",
            rule_id="CR-13",
        )

    if decision == "deprecated" and can_quote_externally is True:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "deprecated_external_quote_conflict",
            "`deprecated` requires can_quote_externally=false unless explicitly re-approved later.",
            rule_id="CR-14",
        )

    if record_type != "restricted_customer" and decision == "enter_governance_table_only":
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "governance_table_only_non_restricted_record",
            "`enter_governance_table_only` is only valid for restricted_customer records.",
            rule_id="CR-15",
        )

    if decision == "review_identity_mapping" and record_type != "merchant_case":
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "review_identity_mapping_non_merchant_case",
            "`review_identity_mapping` is temporarily limited to merchant_case rows.",
            rule_id="CR-18",
        )


def _build_summary(
    decisions_path: Path,
    output_path: Path,
    preview_dir: Optional[Path],
    rows: List[dict],
    issues: List[ValidationIssue],
    preview_records: Optional[Dict[str, List[dict]]],
) -> dict:
    issue_counts = Counter(issue.severity for issue in issues)
    blank_reviewer_count = sum(1 for row in rows if not _text(row.get("reviewer")))
    blank_reviewed_at_count = sum(1 for row in rows if not _text(row.get("reviewed_at")))
    preview_record_count = _preview_record_count(preview_records)
    unreviewed_record_count = max(preview_record_count - len(rows), 0) if preview_records is not None else 0
    return {
        "decisions_path": str(decisions_path),
        "preview_dir": str(preview_dir) if preview_dir is not None else None,
        "output_path": str(output_path),
        "errors_output_path": str(output_path.parent / VALIDATION_ERROR_FILENAME),
        "warnings_output_path": str(output_path.parent / VALIDATION_WARNING_FILENAME),
        "total_rows": len(rows),
        "error_count": issue_counts.get("error", 0),
        "warning_count": issue_counts.get("warning", 0),
        "blank_review_decision_count": sum(1 for row in rows if not _text(row.get("review_decision"))),
        "blank_reviewer_count": blank_reviewer_count,
        "blank_reviewed_at_count": blank_reviewed_at_count,
        "record_type_counts": dict(sorted(Counter(_text(row.get("record_type")) for row in rows).items())),
        "review_decision_counts": dict(sorted(Counter(_text(row.get("review_decision")) for row in rows).items())),
        "suggested_action_counts": dict(sorted(Counter(_text(row.get("suggested_action")) for row in rows).items())),
        "rule_counts": dict(sorted(Counter(issue.rule_id for issue in issues).items())),
        "preview_record_count": preview_record_count,
        "unreviewed_record_count": unreviewed_record_count,
        "info_signals": _build_info_signals(rows, blank_reviewer_count, blank_reviewed_at_count, unreviewed_record_count),
    }


def _build_info_signals(
    rows: List[dict],
    blank_reviewer_count: int,
    blank_reviewed_at_count: int,
    unreviewed_record_count: int,
) -> List[dict]:
    signals = []
    if rows and len(rows) > 10 and all(
        _text(row.get("review_decision")) == _text(row.get("suggested_action")) for row in rows
    ):
        signals.append(
            {
                "id": "IN-1",
                "message": "全部決策與機器建議相同，請確認確實逐筆人工審核（A 風險 #2）。",
            }
        )
    if blank_reviewer_count > 0 or blank_reviewed_at_count > 0:
        signals.append(
            {
                "id": "IN-2",
                "message": "apply-preview 前置檢查將要求全部填寫 reviewer 與 reviewed_at。",
            }
        )
    if unreviewed_record_count > 0:
        signals.append(
            {
                "id": "IN-3",
                "message": f"{unreviewed_record_count} 筆紀錄無 review row，apply 時將進入 not_reviewed 桶（K spec）。",
            }
        )
    return signals


def _preview_record_count(preview_records: Optional[Dict[str, List[dict]]]) -> int:
    if preview_records is None:
        return 0
    return sum(len(records) for records in preview_records.values())


def _require_decision(row: dict, row_number: int, issues: List[ValidationIssue], expected: str) -> None:
    if _text(row.get("review_decision")) != expected:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "policy_review_decision_mismatch",
            f"`review_decision` must be {expected} for this record type.",
        )


def _require_bool(row: dict, row_number: int, issues: List[ValidationIssue], column: str, expected: bool) -> None:
    value = _bool_value(row.get(column))
    if value is not expected:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "policy_boolean_mismatch",
            f"`{column}` must be {_bool_text(expected)} for this governance rule.",
        )


def _require_text(row: dict, row_number: int, issues: List[ValidationIssue], column: str, expected: str) -> None:
    if _text(row.get(column)) != expected:
        _add_row_issue(
            row,
            row_number,
            issues,
            "error",
            "policy_text_mismatch",
            f"`{column}` must be {expected} for this governance rule.",
        )


def _add_row_issue(
    row: dict,
    row_number: int,
    issues: List[ValidationIssue],
    severity: str,
    code: str,
    message: str,
    rule_id: Optional[str] = None,
) -> None:
    issues.append(
        ValidationIssue(
            severity=severity,
            row_number=row_number,
            source_sheet=_text(row.get("source_sheet")),
            source_row=_text(row.get("source_row")),
            record_type=_text(row.get("record_type")),
            review_decision=_text(row.get("review_decision")),
            code=code,
            rule_id=rule_id or code,
            message=message,
        )
    )


def _write_issue_csv(path: Path, issues: List[ValidationIssue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ISSUE_CSV_COLUMNS)
        writer.writeheader()
        for issue in issues:
            writer.writerow(
                {
                    "severity": issue.severity,
                    "rule_id": issue.rule_id,
                    "row_number": "" if issue.row_number is None else issue.row_number,
                    "source_sheet": issue.source_sheet,
                    "source_row": issue.source_row,
                    "record_type": issue.record_type,
                    "review_decision": issue.review_decision,
                    "message": issue.message,
                }
            )


def _row_key(row: dict) -> Tuple[str, str, str]:
    return (_text(row.get("source_sheet")), _text(row.get("source_row")), _text(row.get("record_type")))


def _bool_value(value) -> Optional[bool]:
    text = _text(value).lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _is_iso_date(value: str) -> bool:
    if not _matches_iso_date_shape(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _matches_iso_date_shape(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-" and all(
        value[index].isdigit() for index in (0, 1, 2, 3, 5, 6, 8, 9)
    )


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _split_pipe(value) -> List[str]:
    return [part for part in _text(value).split("|") if part]


def _render_counter(counter: Dict[str, int]) -> List[str]:
    if not counter:
        return ["- None"]
    return [f"- `{key or '(blank)'}`: {value}" for key, value in counter.items()]


def _md_cell(value: str) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
