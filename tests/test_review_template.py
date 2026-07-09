import csv
import json
from pathlib import Path

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.review_decision_validation import validate_review_decisions
from marketing_knowledge_agent.review_template import generate_review_template


def test_review_template_includes_required_review_records(tmp_path):
    preview_dir = tmp_path / "preview"
    output = tmp_path / "review_decisions_template.csv"
    summary_output = tmp_path / "review_summary.md"
    _write_preview_fixture(preview_dir)

    summary = generate_review_template(preview_dir, output, summary_output)
    rows = _read_csv(output)

    assert output.exists()
    assert summary_output.exists()
    assert summary["total_review_rows"] == 11
    assert summary["record_type_counts"] == {
        "merchant_case": 5,
        "pending_metric": 2,
        "public_metric": 2,
        "restricted_customer": 2,
    }
    assert not any(row["record_type"] == "handle_mapping" for row in rows)

    merchant_rows = [row for row in rows if row["record_type"] == "merchant_case"]
    assert len(merchant_rows) == 5
    assert _row(rows, "商家夥伴案例資料庫", "20")["suggested_action"] == "exclude_from_content_index"
    assert "no_valid_content_asset" in _row(rows, "商家夥伴案例資料庫", "20")["issue_type"]
    assert _row(rows, "商家夥伴案例資料庫", "50")["suggested_action"] == "enrich_metadata"
    assert _row(rows, "商家夥伴案例資料庫", "9")["suggested_action"] == "keep_all_records"
    assert "suspected_duplicate_review" not in _row(rows, "商家夥伴案例資料庫", "9")["issue_type"]
    assert _row(rows, "商家夥伴案例資料庫", "45")["suggested_action"] == "keep_all_records"
    assert "same_handle_multiple_records" in _row(rows, "商家夥伴案例資料庫", "45")["issue_type"]

    multi_issue_row = _row(rows, "商家夥伴案例資料庫", "57")
    assert multi_issue_row["suggested_action"] == "exclude"
    assert "competitor_migration" in multi_issue_row["issue_type"]
    assert "no_valid_content_asset" in multi_issue_row["issue_type"]
    assert len([row for row in rows if row["source_sheet"] == "商家夥伴案例資料庫" and row["source_row"] == "57"]) == 1

    restricted_rows = [row for row in rows if row["record_type"] == "restricted_customer"]
    assert len(restricted_rows) == 2
    assert all(row["suggested_action"] == "enter_governance_table_only" for row in restricted_rows)
    assert all(row["can_enter_governance_table"] == "true" for row in restricted_rows)
    assert all(row["can_enter_content_index"] == "false" for row in restricted_rows)

    pending_rows = [row for row in rows if row["record_type"] == "pending_metric"]
    assert len(pending_rows) == 2
    assert all(row["suggested_action"] == "keep_internal_only" for row in pending_rows)
    assert all(row["can_quote_externally"] == "false" for row in pending_rows)

    missing_channel = _row(rows, "「可公開」對外數據", "20")
    assert missing_channel["suggested_action"] == "needs_update"
    assert missing_channel["can_enter_content_index"] == "false"
    restricted_note = _row(rows, "「可公開」對外數據", "15")
    assert restricted_note["suggested_action"] == "restricted_use_only"
    assert restricted_note["allowed_exposure_channels"] == "verbal_briefing"

    assert all(row["review_decision"] == "" for row in rows)
    assert all(row["reviewer"] == "" for row in rows)
    assert all(row["reviewed_at"] == "" for row in rows)

    summary_text = summary_output.read_text(encoding="utf-8")
    assert "- has suspected duplicate review item: no" in summary_text
    assert "- suggested enter_governance_table_only: 2" in summary_text
    assert "- suggested keep_internal_only: 2" in summary_text
    assert "- suggested enrich_metadata: 1" in summary_text
    assert "- suggested keep_all_records: 2" in summary_text
    assert "- suspected_duplicate_review: 0" in summary_text
    assert "Decisions have not been applied." in summary_text
    assert "Obsidian has not been synced." in summary_text


def test_review_template_cli_writes_outputs(tmp_path):
    preview_dir = tmp_path / "preview"
    output = tmp_path / "review.csv"
    summary_output = tmp_path / "review.md"
    _write_preview_fixture(preview_dir)

    exit_code = main(
        [
            "review-template",
            "--preview-dir",
            str(preview_dir),
            "--output",
            str(output),
            "--summary-output",
            str(summary_output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    assert summary_output.exists()


def test_review_template_csv_uses_utf8_sig_for_excel(tmp_path):
    preview_dir = tmp_path / "preview"
    output = tmp_path / "review.csv"
    summary_output = tmp_path / "review.md"
    _write_preview_fixture(preview_dir)

    generate_review_template(preview_dir, output, summary_output)

    assert output.read_bytes().startswith(b"\xef\xbb\xbf")


def test_validate_review_decisions_accepts_filled_template(tmp_path):
    preview_dir = tmp_path / "preview"
    decisions = tmp_path / "review.csv"
    summary_output = tmp_path / "review.md"
    validation_output = tmp_path / "validation.md"
    _write_preview_fixture(preview_dir)
    generate_review_template(preview_dir, decisions, summary_output)
    _fill_review_decisions(decisions, uppercase_booleans=True)

    summary = validate_review_decisions(decisions, validation_output, preview_dir=preview_dir)

    assert summary["error_count"] == 0
    assert summary["warning_count"] == 0
    assert summary["total_rows"] == 11
    assert summary["blank_review_decision_count"] == 0
    report = validation_output.read_text(encoding="utf-8")
    assert "- Valid for next preview step: yes" in report
    assert "- Decisions have not been applied." in report


def test_validate_review_decisions_rejects_restricted_customer_policy_conflict(tmp_path):
    preview_dir = tmp_path / "preview"
    decisions = tmp_path / "review.csv"
    summary_output = tmp_path / "review.md"
    validation_output = tmp_path / "validation.md"
    _write_preview_fixture(preview_dir)
    generate_review_template(preview_dir, decisions, summary_output)
    _fill_review_decisions(decisions)
    rows = _read_csv(decisions)
    for row in rows:
        if row["record_type"] == "restricted_customer":
            row["can_enter_content_index"] = "true"
            break
    _write_csv(decisions, rows)

    summary = validate_review_decisions(decisions, validation_output, preview_dir=preview_dir)

    assert summary["error_count"] == 1
    assert "policy_boolean_mismatch" in validation_output.read_text(encoding="utf-8")


def test_validate_review_decisions_rejects_no_valid_asset_external_quote(tmp_path):
    preview_dir = tmp_path / "preview"
    decisions = tmp_path / "review.csv"
    summary_output = tmp_path / "review.md"
    validation_output = tmp_path / "validation.md"
    _write_preview_fixture(preview_dir)
    generate_review_template(preview_dir, decisions, summary_output)
    _fill_review_decisions(decisions)
    rows = _read_csv(decisions)
    for row in rows:
        if row["source_sheet"] == "商家夥伴案例資料庫" and row["source_row"] == "20":
            row["can_quote_externally"] = "true"
            break
    _write_csv(decisions, rows)

    summary = validate_review_decisions(decisions, validation_output, preview_dir=preview_dir)

    assert summary["error_count"] == 1
    assert "policy_boolean_mismatch" in validation_output.read_text(encoding="utf-8")


def test_validate_review_decisions_cli_writes_report(tmp_path):
    preview_dir = tmp_path / "preview"
    decisions = tmp_path / "review.csv"
    summary_output = tmp_path / "review.md"
    validation_output = tmp_path / "validation.md"
    _write_preview_fixture(preview_dir)
    generate_review_template(preview_dir, decisions, summary_output)
    _fill_review_decisions(decisions)

    exit_code = main(
        [
            "validate-review-decisions",
            "--decisions",
            str(decisions),
            "--preview-dir",
            str(preview_dir),
            "--output",
            str(validation_output),
        ]
    )

    assert exit_code == 0
    assert validation_output.exists()


def _write_preview_fixture(preview_dir: Path) -> None:
    preview_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        preview_dir / "merchant_cases.json",
        [
            {
                "source_sheet": "商家夥伴案例資料庫",
                "source_row": 20,
                "record_type": "merchant_case",
                "brand_name": "NEOFLAM台灣官方購物網",
                "merchant_handle": "neoflam",
                "title": "NEOFLAM",
                "governance_issue_types": ["invalid_asset_value", "no_valid_content_asset"],
                "governance_risk_reasons": ["文章=暫時下架", "no_valid_content_asset", "can_quote_externally=false"],
                "governance_risk_fields": ["文章", "影片", "Podcast", "新聞"],
                "invalid_asset_fields": ["文章"],
                "invalid_asset_values": {"文章": "暫時下架"},
                "no_valid_content_asset": True,
                "can_enter_content_index": False,
                "can_quote_externally": False,
            },
            {
                "source_sheet": "商家夥伴案例資料庫",
                "source_row": 57,
                "record_type": "merchant_case",
                "brand_name": "未來實驗室（跨境）",
                "merchant_handle": None,
                "title": "未來實驗室",
                "notes": "轉至 Shopify ，下架內容",
                "governance_issue_types": [
                    "invalid_asset_value",
                    "no_valid_content_asset",
                    "competitor_migration",
                ],
                "governance_risk_reasons": ["文章=已下架", "no_valid_content_asset", "備註 contains Shopify"],
                "governance_risk_fields": ["文章", "備註"],
                "invalid_asset_fields": ["文章"],
                "invalid_asset_values": {"文章": "已下架"},
                "no_valid_content_asset": True,
                "can_enter_content_index": False,
                "can_quote_externally": False,
            },
            {
                "source_sheet": "商家夥伴案例資料庫",
                "source_row": 50,
                "record_type": "merchant_case",
                "brand_name": "No Handle Brand",
                "merchant_handle": None,
                "title": "No Handle Brand",
                "governance_issue_types": [],
                "governance_risk_reasons": [],
                "governance_risk_fields": [],
                "invalid_asset_fields": [],
                "invalid_asset_values": {},
                "no_valid_content_asset": False,
                "can_enter_content_index": True,
                "can_quote_externally": True,
            },
            {
                "source_sheet": "商家夥伴案例資料庫",
                "source_row": 9,
                "record_type": "merchant_case",
                "brand_name": "NISORO",
                "merchant_handle": "nisoro",
                "title": "NISORO",
                "same_brand_multiple_records": True,
                "same_handle_multiple_records": True,
                "multi_interview_record": True,
                "suspected_duplicate_review": False,
                "can_enter_content_index": True,
                "can_quote_externally": True,
            },
            {
                "source_sheet": "商家夥伴案例資料庫",
                "source_row": 45,
                "record_type": "merchant_case",
                "brand_name": "Shared Handle A",
                "merchant_handle": "sharedhandle",
                "title": "Shared Handle A",
                "same_brand_multiple_records": False,
                "same_handle_multiple_records": True,
                "multi_interview_record": True,
                "suspected_duplicate_review": False,
                "can_enter_content_index": True,
                "can_quote_externally": True,
            },
        ],
    )
    _write_json(
        preview_dir / "restricted_customers.json",
        [
            {
                "source_sheet": "「不可公開」客戶名單",
                "source_row": 5,
                "record_type": "restricted_customer",
                "brand_name": "位元堂",
                "title": "位元堂",
                "can_quote_externally": False,
            },
            {
                "source_sheet": "「不可公開」客戶名單",
                "source_row": 6,
                "record_type": "restricted_customer",
                "brand_name": "SHU UEMURA／植村秀",
                "title": "SHU UEMURA／植村秀",
                "restricted_aliases": ["SHU UEMURA", "植村秀"],
                "can_quote_externally": False,
            },
        ],
    )
    _write_json(
        preview_dir / "public_metrics.json",
        [
            {
                "source_sheet": "「可公開」對外數據",
                "source_row": 20,
                "record_type": "public_metric",
                "metric_name": "節慶檔期GMV",
                "title": "節慶檔期GMV",
                "missing_allowed_exposure_channels": True,
                "allowed_exposure_channels": [],
                "can_quote_externally": False,
            },
            {
                "source_sheet": "「可公開」對外數據",
                "source_row": 15,
                "record_type": "public_metric",
                "metric_name": "累計總GMV",
                "title": "累計總GMV",
                "restricted_note": "僅用於口頭說明，不留文字紀錄",
                "allowed_exposure_channels": ["verbal_briefing"],
                "can_quote_externally": True,
            },
        ],
    )
    _write_json(
        preview_dir / "pending_metrics.json",
        [
            {
                "source_sheet": "待確認數據",
                "source_row": 3,
                "record_type": "pending_metric",
                "metric_name": "品牌會員總數",
                "title": "品牌會員總數",
                "can_quote_externally": False,
            },
            {
                "source_sheet": "待確認數據",
                "source_row": 4,
                "record_type": "pending_metric",
                "metric_name": "GMV",
                "title": "GMV",
                "can_quote_externally": False,
            },
        ],
    )
    _write_json(
        preview_dir / "handle_mappings.json",
        [
            {
                "source_sheet": "handle 比對",
                "source_row": 2,
                "record_type": "handle_mapping",
                "brand_name": "NISORO",
                "merchant_handle": "nisoro",
            }
        ],
    )


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _fill_review_decisions(path: Path, uppercase_booleans: bool = False) -> None:
    rows = _read_csv(path)
    for row in rows:
        row["review_decision"] = row["suggested_action"]
        if uppercase_booleans:
            for column in [
                "can_enter_vault",
                "can_enter_content_index",
                "can_enter_governance_table",
                "can_quote_externally",
            ]:
                row[column] = row[column].upper()
    _write_csv(path, rows)


def _row(rows, source_sheet, source_row):
    for row in rows:
        if row["source_sheet"] == source_sheet and row["source_row"] == source_row:
            return row
    raise AssertionError(f"row not found: {source_sheet} {source_row}")
