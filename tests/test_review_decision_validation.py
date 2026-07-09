import csv
import json
from pathlib import Path

import pytest

from marketing_knowledge_agent.review_decision_validation import (
    ALLOWED_REVIEW_DECISIONS,
    validate_review_decisions,
)
from marketing_knowledge_agent.review_template import REVIEW_COLUMNS, build_expected_review_rows


def test_required_fields_and_enum_validation(tmp_path):
    rows = [
        _decision_row(source_row="1", suggested_action=""),
        _decision_row(source_row="2", final_status=""),
        _decision_row(source_row="3", final_status="ready"),
        _decision_row(source_row="4", allowed_exposure_channels="saleskits|unknown_channel"),
        _decision_row(source_row="5", reviewed_at="2026/07/10"),
        _decision_row(source_row="6", reviewer="", reviewed_at=""),
    ]

    summary, _, errors, warnings = _run_validation(tmp_path, rows)

    assert summary["error_count"] == 5
    assert _rule_ids(errors) >= {
        "missing_suggested_action",
        "missing_final_status",
        "invalid_final_status",
        "invalid_exposure_channel",
        "invalid_reviewed_at",
    }
    assert _rule_ids(warnings) >= {"blank_reviewer", "blank_reviewed_at"}


@pytest.mark.parametrize(
    ("rule_id", "severity", "overrides"),
    [
        (
            "CR-5",
            "error",
            {
                "suggested_action": "exclude",
                "review_decision": "exclude",
                "can_enter_content_index": "true",
                "can_quote_externally": "false",
                "final_status": "excluded",
            },
        ),
        (
            "CR-6",
            "error",
            {
                "suggested_action": "exclude_from_content_index",
                "review_decision": "exclude_from_content_index",
                "can_enter_content_index": "true",
                "can_quote_externally": "false",
            },
        ),
        (
            "CR-7",
            "error",
            {
                "record_type": "restricted_customer",
                "suggested_action": "enter_governance_table_only",
                "review_decision": "enter_governance_table_only",
                "can_enter_vault": "false",
                "can_enter_content_index": "false",
                "can_enter_governance_table": "false",
                "can_quote_externally": "false",
                "final_status": "restricted",
            },
        ),
        (
            "CR-8",
            "error",
            {
                "suggested_action": "enter_governance_table_only",
                "review_decision": "enter_governance_table_only",
                "can_enter_content_index": "true",
                "can_enter_governance_table": "true",
                "can_quote_externally": "false",
            },
        ),
        (
            "CR-9",
            "error",
            {
                "suggested_action": "keep_internal_only",
                "review_decision": "keep_internal_only",
                "can_enter_content_index": "false",
                "can_quote_externally": "true",
                "final_status": "internal_only",
            },
        ),
        (
            "CR-10",
            "warning",
            {
                "suggested_action": "restricted_use_only",
                "review_decision": "restricted_use_only",
                "allowed_exposure_channels": "",
                "can_quote_externally": "false",
            },
        ),
        (
            "CR-10",
            "error",
            {
                "suggested_action": "restricted_use_only",
                "review_decision": "restricted_use_only",
                "allowed_exposure_channels": "",
                "can_quote_externally": "true",
            },
        ),
        (
            "CR-11",
            "warning",
            {
                "suggested_action": "approve",
                "review_decision": "approve",
                "can_quote_externally": "false",
            },
        ),
        (
            "CR-12",
            "error",
            {
                "suggested_action": "approve",
                "review_decision": "approve",
                "issue_type": "no_valid_content_asset",
                "can_enter_content_index": "false",
                "can_quote_externally": "false",
            },
        ),
        (
            "CR-13",
            "error",
            {
                "suggested_action": "needs_update",
                "review_decision": "needs_update",
                "final_status": "approved",
            },
        ),
        (
            "CR-14",
            "error",
            {
                "suggested_action": "deprecated",
                "review_decision": "deprecated",
                "can_quote_externally": "true",
            },
        ),
        (
            "CR-15",
            "error",
            {
                "record_type": "public_metric",
                "suggested_action": "enter_governance_table_only",
                "review_decision": "enter_governance_table_only",
                "can_enter_content_index": "false",
                "can_enter_governance_table": "true",
                "can_quote_externally": "false",
            },
        ),
        (
            "CR-18",
            "error",
            {
                "record_type": "public_metric",
                "suggested_action": "review_identity_mapping",
                "review_decision": "review_identity_mapping",
            },
        ),
    ],
)
def test_new_conflict_rules_emit_expected_ids(tmp_path, rule_id, severity, overrides):
    summary, _, errors, warnings = _run_validation(tmp_path, [_decision_row(**overrides)])

    assert summary[f"{severity}_count"] >= 1
    issues = errors if severity == "error" else warnings
    assert rule_id in _rule_ids(issues)


def test_new_conflict_rules_accept_valid_counterexamples(tmp_path):
    rows = [
        _decision_row(
            source_row="1",
            suggested_action="exclude",
            review_decision="exclude",
            can_enter_content_index="false",
            can_quote_externally="false",
            final_status="excluded",
        ),
        _decision_row(
            source_row="2",
            suggested_action="exclude_from_content_index",
            review_decision="exclude_from_content_index",
            can_enter_content_index="false",
            can_quote_externally="false",
        ),
        _decision_row(
            source_row="3",
            record_type="restricted_customer",
            suggested_action="enter_governance_table_only",
            review_decision="enter_governance_table_only",
            can_enter_vault="false",
            can_enter_content_index="false",
            can_enter_governance_table="true",
            can_quote_externally="false",
            final_status="restricted",
        ),
        _decision_row(
            source_row="4",
            suggested_action="keep_internal_only",
            review_decision="keep_internal_only",
            can_enter_content_index="false",
            can_quote_externally="false",
            final_status="internal_only",
        ),
        _decision_row(
            source_row="5",
            suggested_action="approve_internal_only",
            review_decision="approve_internal_only",
            can_quote_externally="false",
            final_status="internal_only",
        ),
        _decision_row(
            source_row="6",
            suggested_action="restricted_use_only",
            review_decision="restricted_use_only",
            allowed_exposure_channels="verbal_briefing",
            can_quote_externally="false",
        ),
        _decision_row(source_row="7", suggested_action="approve", review_decision="approve"),
        _decision_row(
            source_row="8",
            suggested_action="needs_update",
            review_decision="needs_update",
            final_status="review_required",
        ),
        _decision_row(
            source_row="9",
            suggested_action="deprecated",
            review_decision="deprecated",
            can_quote_externally="false",
        ),
        _decision_row(
            source_row="10",
            suggested_action="review_identity_mapping",
            review_decision="review_identity_mapping",
            can_quote_externally="false",
        ),
    ]

    summary, _, _, warnings = _run_validation(tmp_path, rows)

    assert summary["error_count"] == 0
    assert warnings == []


def test_info_signals_include_stamp_warning_threshold_and_preview_gap(tmp_path):
    ten_rows = [
        _decision_row(source_row=str(index), suggested_action="approve", review_decision="approve")
        for index in range(1, 11)
    ]
    summary_ten, _, _, _ = _run_validation(tmp_path / "ten", ten_rows)
    assert "IN-1" not in _info_ids(summary_ten)

    eleven_rows = [
        _decision_row(source_row=str(index), suggested_action="approve", review_decision="approve")
        for index in range(1, 12)
    ]
    summary_eleven, _, _, _ = _run_validation(tmp_path / "eleven", eleven_rows)
    assert "IN-1" in _info_ids(summary_eleven)

    preview_dir = tmp_path / "preview"
    _write_preview(preview_dir, handle_mappings=[{"record_type": "handle_mapping", "source_sheet": "handle 比對", "source_row": 2}])
    summary_preview, _, _, _ = _run_validation(tmp_path / "preview_run", [], preview_dir=preview_dir)
    assert "IN-3" in _info_ids(summary_preview)

    blank_rows = [_decision_row(source_row="1", reviewer="", reviewed_at="")]
    summary_blank, _, _, _ = _run_validation(tmp_path / "blank", blank_rows)
    assert "IN-2" in _info_ids(summary_blank)


def test_validation_writes_summary_errors_and_warnings_csv(tmp_path):
    row = _decision_row(
        suggested_action="restricted_use_only",
        review_decision="restricted_use_only",
        allowed_exposure_channels="",
        can_quote_externally="true",
        reviewer="",
    )

    summary, summary_path, errors, warnings = _run_validation(tmp_path, [row])

    assert summary_path.exists()
    assert Path(summary["errors_output_path"]).exists()
    assert Path(summary["warnings_output_path"]).exists()
    assert errors[0].keys() == {
        "severity",
        "rule_id",
        "row_number",
        "source_sheet",
        "source_row",
        "record_type",
        "review_decision",
        "message",
    }
    assert any(error["rule_id"] == "CR-10" for error in errors)
    assert any(warning["rule_id"] == "blank_reviewer" for warning in warnings)


def test_build_expected_review_rows_is_public_and_shared():
    preview = {
        "merchant_cases": [],
        "public_metrics": [],
        "pending_metrics": [
            {
                "source_sheet": "待確認數據",
                "source_row": 3,
                "record_type": "pending_metric",
                "metric_name": "Example",
                "title": "Example",
                "can_quote_externally": False,
            }
        ],
        "restricted_customers": [
            {
                "source_sheet": "「不可公開」客戶名單",
                "source_row": 5,
                "record_type": "restricted_customer",
                "brand_name": "Restricted Brand",
                "title": "Restricted Brand",
                "can_quote_externally": False,
            }
        ],
        "handle_mappings": [
            {
                "source_sheet": "handle 比對",
                "source_row": 2,
                "record_type": "handle_mapping",
            }
        ],
    }

    rows = build_expected_review_rows(preview)

    assert [row["record_type"] for row in rows] == ["restricted_customer", "pending_metric"]


def test_enum_in_governance_rules_matches_code():
    docs_values = _review_decisions_from_governance_rules(
        Path("docs/governance/GOVERNANCE_RULES.md")
    )

    assert docs_values == ALLOWED_REVIEW_DECISIONS


def _run_validation(tmp_path, rows, preview_dir=None):
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    decisions = tmp_path / "review_decisions.csv"
    summary_path = tmp_path / "review_decisions_validation_summary.md"
    _write_decisions(decisions, rows)
    summary = validate_review_decisions(decisions, summary_path, preview_dir=preview_dir)
    errors = _read_csv(summary_path.parent / "review_decisions_validation_errors.csv")
    warnings = _read_csv(summary_path.parent / "review_decisions_validation_warnings.csv")
    return summary, summary_path, errors, warnings


def _decision_row(**overrides):
    row = {column: "" for column in REVIEW_COLUMNS}
    row.update(
        {
            "source_sheet": "商家夥伴案例資料庫",
            "source_row": "1",
            "record_type": "merchant_case",
            "issue_type": "",
            "suggested_action": "approve",
            "review_decision": "approve",
            "can_enter_vault": "true",
            "can_enter_content_index": "true",
            "can_enter_governance_table": "false",
            "can_quote_externally": "true",
            "allowed_exposure_channels": "saleskits",
            "final_status": "approved",
            "reviewer": "Reviewer",
            "reviewed_at": "2026-07-10",
        }
    )
    row.update(overrides)
    return row


def _write_decisions(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in REVIEW_COLUMNS})


def _read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _rule_ids(rows):
    return {row["rule_id"] for row in rows}


def _info_ids(summary):
    return {item["id"] for item in summary.get("info_signals", [])}


def _write_preview(preview_dir: Path, **overrides):
    preview_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "merchant_cases": [],
        "public_metrics": [],
        "pending_metrics": [],
        "restricted_customers": [],
        "handle_mappings": [],
    }
    payload.update(overrides)
    filenames = {
        "merchant_cases": "merchant_cases.json",
        "public_metrics": "public_metrics.json",
        "pending_metrics": "pending_metrics.json",
        "restricted_customers": "restricted_customers.json",
        "handle_mappings": "handle_mappings.json",
    }
    for key, filename in filenames.items():
        (preview_dir / filename).write_text(
            json.dumps(payload[key], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _review_decisions_from_governance_rules(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    in_section = False
    values = set()
    for line in lines:
        if line.strip() == "## Review Decision Definitions":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("| `"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if not parts:
            continue
        values.add(parts[0].strip("`"))
    return values
