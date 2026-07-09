from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .review_template import (
    REVIEW_COLUMNS,
    ReviewTemplateError,
    _merchant_review_rows,
    _pending_metric_review_rows,
    _public_metric_review_rows,
    _restricted_customer_review_rows,
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


class ReviewDecisionValidationError(ValueError):
    """Raised when a review decision CSV cannot be validated."""


@dataclass
class ValidationIssue:
    severity: str
    row_number: Optional[int]
    source_sheet: str
    source_row: str
    record_type: str
    code: str
    message: str


def validate_review_decisions(
    decisions_path: Path,
    output_path: Path,
    preview_dir: Optional[Path] = None,
) -> dict:
    rows, fieldnames = _read_decision_rows(decisions_path)
    issues: List[ValidationIssue] = []

    missing_columns = [column for column in REVIEW_COLUMNS if column not in fieldnames]
    for column in missing_columns:
        issues.append(
            ValidationIssue(
                severity="error",
                row_number=None,
                source_sheet="",
                source_row="",
                record_type="",
                code="missing_column",
                message=f"Required column is missing: {column}",
            )
        )

    _validate_rows(rows, issues)
    if preview_dir is not None:
        _validate_against_preview(preview_dir, rows, issues)

    summary = _build_summary(decisions_path, output_path, preview_dir, rows, issues)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_validation_report(summary, issues), encoding="utf-8")
    return summary


def render_validation_report(summary: dict, issues: List[ValidationIssue]) -> str:
    lines = [
        "# Review Decisions Validation",
        "",
        f"- Decision CSV: `{summary['decisions_path']}`",
        f"- Preview dir: `{summary['preview_dir'] or 'not checked'}`",
        f"- Report output: `{summary['output_path']}`",
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
    lines.extend(["", "## Issues", ""])
    if issues:
        lines.extend(
            [
                "| severity | row | record | code | message |",
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
                        _md_cell(issue.code),
                        _md_cell(issue.message),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- No validation errors or warnings.")
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
        _validate_review_decision(row, index, issues)
        _validate_boolean_columns(row, index, issues)
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
                code="duplicate_review_row",
                message=f"Review decision CSV has {count} rows for the same preview record.",
            )
        )


def _validate_against_preview(preview_dir: Path, rows: List[dict], issues: List[ValidationIssue]) -> None:
    try:
        preview = load_preview_records(preview_dir)
    except ReviewTemplateError as exc:
        issues.append(
            ValidationIssue(
                severity="error",
                row_number=None,
                source_sheet="",
                source_row="",
                record_type="",
                code="preview_load_failed",
                message=str(exc),
            )
        )
        return

    expected_rows: List[dict] = []
    expected_rows.extend(_merchant_review_rows(preview["merchant_cases"]))
    expected_rows.extend(_restricted_customer_review_rows(preview["restricted_customers"]))
    expected_rows.extend(_public_metric_review_rows(preview["public_metrics"]))
    expected_rows.extend(_pending_metric_review_rows(preview["pending_metrics"]))
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
                code="missing_expected_review_row",
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
                code="unexpected_review_row",
                message="Decision CSV contains a row that is not required by the current preview JSON.",
            )
        )


def _validate_required_identity(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    for column in ("source_sheet", "source_row", "record_type"):
        if not _text(row.get(column)):
            _add_row_issue(row, row_number, issues, "error", "missing_identity", f"Missing `{column}`.")


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


def _validate_record_policy(row: dict, row_number: int, issues: List[ValidationIssue]) -> None:
    record_type = _text(row.get("record_type"))
    issue_types = set(_split_pipe(row.get("issue_type")))
    decision = _text(row.get("review_decision"))

    if record_type == "restricted_customer":
        _require_decision(row, row_number, issues, "enter_governance_table_only")
        _require_bool(row, row_number, issues, "can_enter_vault", False)
        _require_bool(row, row_number, issues, "can_enter_content_index", False)
        _require_bool(row, row_number, issues, "can_enter_governance_table", True)
        _require_bool(row, row_number, issues, "can_quote_externally", False)
        _require_text(row, row_number, issues, "final_status", "restricted")
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


def _build_summary(
    decisions_path: Path,
    output_path: Path,
    preview_dir: Optional[Path],
    rows: List[dict],
    issues: List[ValidationIssue],
) -> dict:
    issue_counts = Counter(issue.severity for issue in issues)
    return {
        "decisions_path": str(decisions_path),
        "preview_dir": str(preview_dir) if preview_dir is not None else None,
        "output_path": str(output_path),
        "total_rows": len(rows),
        "error_count": issue_counts.get("error", 0),
        "warning_count": issue_counts.get("warning", 0),
        "blank_review_decision_count": sum(1 for row in rows if not _text(row.get("review_decision"))),
        "blank_reviewer_count": sum(1 for row in rows if not _text(row.get("reviewer"))),
        "blank_reviewed_at_count": sum(1 for row in rows if not _text(row.get("reviewed_at"))),
        "record_type_counts": dict(sorted(Counter(_text(row.get("record_type")) for row in rows).items())),
        "review_decision_counts": dict(sorted(Counter(_text(row.get("review_decision")) for row in rows).items())),
        "suggested_action_counts": dict(sorted(Counter(_text(row.get("suggested_action")) for row in rows).items())),
    }


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
) -> None:
    issues.append(
        ValidationIssue(
            severity=severity,
            row_number=row_number,
            source_sheet=_text(row.get("source_sheet")),
            source_row=_text(row.get("source_row")),
            record_type=_text(row.get("record_type")),
            code=code,
            message=message,
        )
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
