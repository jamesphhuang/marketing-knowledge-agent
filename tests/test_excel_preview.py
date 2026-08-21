import json
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.excel_ingestion import (
    SHEET_MERCHANT_CASES,
    SHEET_PUBLIC_METRICS,
    SHEET_RESTRICTED_CUSTOMERS,
)
from marketing_knowledge_agent.excel_preview import ExcelPreviewError, GovernancePreviewStore, generate_excel_preview


CAPTURED_DATE = date(2026, 7, 1)
NORMALIZED_AT = "2026-07-01T00:00:00+00:00"


def test_generate_excel_preview_writes_expected_files(tmp_path):
    workbook = tmp_path / "source.xlsx"
    output_dir = tmp_path / "preview"
    _write_preview_workbook(workbook)

    summary = generate_excel_preview(
        workbook,
        output_dir,
        captured_date=CAPTURED_DATE,
        normalized_at=NORMALIZED_AT,
    )

    expected_files = {
        "merchant_cases.json",
        "public_metrics.json",
        "pending_metrics.json",
        "restricted_customers.json",
        "handle_mappings.json",
        "preview_summary.md",
        "validation_errors.md",
        "workbook_lineage.json",
    }
    assert expected_files == {path.name for path in output_dir.iterdir()}

    merchant_cases = _read_json(output_dir / "merchant_cases.json")
    public_metrics = _read_json(output_dir / "public_metrics.json")
    restricted_customers = _read_json(output_dir / "restricted_customers.json")
    handle_mappings = _read_json(output_dir / "handle_mappings.json")

    assert summary["content_index_preview_count"] == 2
    assert summary["can_enter_content_index_count"] == 2
    assert summary["restricted_customer_count"] == 1
    assert summary["pending_metric_count"] == 1
    assert summary["merchant_case_governance_risk_count"] == 1
    assert summary["unknown_exposure_channel_columns"] == []
    assert summary["public_metric_missing_reference_source_count"] == 0
    assert summary["restricted_customer_blank_status_count"] == 0
    assert summary["restricted_customer_blank_submitted_by_count"] == 0
    assert merchant_cases[0]["record_type"] == "merchant_case"
    assert merchant_cases[0]["brand_name"] == "1982kids"
    assert merchant_cases[0]["source_sheet"] == "商家夥伴案例資料庫"
    assert merchant_cases[0]["source_row"] == 7
    assert merchant_cases[0]["normalized_at"] == NORMALIZED_AT
    assert "governance_risk_reasons" in merchant_cases[0]
    assert "影片" in merchant_cases[0]["invalid_asset_fields"]
    assert merchant_cases[0]["invalid_asset_values"]["影片"] == "暫時下架"
    assert "invalid_asset_value" in merchant_cases[0]["governance_issue_types"]
    assert public_metrics[0]["allowed_exposure_channels"] == ["saleskits", "verbal_briefing"]
    assert restricted_customers[0]["record_type"] == "restricted_customer"
    assert handle_mappings[0]["record_type"] == "handle_mapping"
    preview_summary = (output_dir / "preview_summary.md").read_text(encoding="utf-8")
    assert "risk_reasons=" in preview_summary
    assert "影片=暫時下架" in preview_summary
    assert "No validation errors." in (output_dir / "validation_errors.md").read_text(encoding="utf-8")


def test_excel_preview_reads_full_merchant_and_restricted_baseline(tmp_path):
    workbook = tmp_path / "baseline.xlsx"
    output_dir = tmp_path / "baseline_preview"
    _write_large_baseline_workbook(workbook)

    summary = generate_excel_preview(
        workbook,
        output_dir,
        captured_date=CAPTURED_DATE,
        normalized_at=NORMALIZED_AT,
    )

    merchant_cases = _read_json(output_dir / "merchant_cases.json")
    restricted_customers = _read_json(output_dir / "restricted_customers.json")

    assert summary["sheet_counts"][SHEET_MERCHANT_CASES] == 120
    assert summary["sheet_counts"][SHEET_RESTRICTED_CUSTOMERS] == 11
    assert summary["restricted_customer_count"] == 11
    assert summary["content_index_preview_count"] == 121
    assert summary["public_metric_missing_reference_source_count"] == 1
    assert summary["restricted_customer_blank_status_count"] == 11
    assert summary["restricted_customer_blank_submitted_by_count"] == 11
    assert len(merchant_cases) == 120
    assert len(restricted_customers) == 11
    assert [record["source_row"] for record in merchant_cases] == list(range(7, 127))
    assert any(record["source_row"] == 50 and record["merchant_handle"] is None for record in merchant_cases)

    no_asset_record = next(record for record in merchant_cases if record["source_row"] == 20)
    assert no_asset_record["no_valid_content_asset"] is True
    assert no_asset_record["can_enter_content_index"] is False
    assert no_asset_record["can_quote_externally"] is False
    assert "no_valid_content_asset" in no_asset_record["governance_risk_reasons"]
    assert no_asset_record["invalid_asset_fields"] == ["文章"]
    assert no_asset_record["invalid_asset_values"] == {"文章": "暫時下架"}
    assert summary["no_valid_content_asset_count"] == 1
    assert any(risk["source_row"] == 20 for risk in summary["merchant_case_governance_risks"])

    nisoro_records = [record for record in merchant_cases if record["brand_name"] == "NISORO"]
    jandan_records = [record for record in merchant_cases if record["brand_name"] == "簡單 JAN DAN"]
    assert len(nisoro_records) == 2
    assert len(jandan_records) == 2
    assert all(record["multi_interview_record"] for record in nisoro_records + jandan_records)
    assert summary["suspected_duplicate_review_count"] == 0
    shared_handle_records = [record for record in merchant_cases if record["merchant_handle"] == "sharedhandle"]
    assert len(shared_handle_records) == 2
    assert all(record["same_handle_multiple_records"] for record in shared_handle_records)
    assert all(not record["same_brand_multiple_records"] for record in shared_handle_records)

    restricted = restricted_customers[0]
    assert restricted["brand_name"] == "Restricted Brand 5"
    assert restricted["nda_signed"] is False
    assert restricted["submitted_by"] is None
    assert restricted["restricted_reason"] == "不可公開客戶名單"


def test_excel_preview_cli_writes_preview_files(tmp_path):
    workbook = tmp_path / "source.xlsx"
    output_dir = tmp_path / "cli_preview"
    _write_preview_workbook(workbook)

    exit_code = main(
        [
            "excel-preview",
            "--workbook",
            str(workbook),
            "--output",
            str(output_dir),
            "--captured-date",
            "2026-07-01",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "merchant_cases.json").exists()
    assert (output_dir / "restricted_customers.json").exists()
    assert (output_dir / "preview_summary.md").exists()


def test_excel_preview_expands_merged_reference_source(tmp_path):
    workbook = tmp_path / "merged.xlsx"
    output_dir = tmp_path / "merged_preview"
    _write_preview_workbook(
        workbook,
        public_metric_rows=[
            ["合作夥伴", "夥伴成效", "第一筆公開論述", "", "2025.07", "Shared reference", "False", "False", "True", "False", "False", "False", "False"],
            ["合作夥伴", "第二指標", "第二筆公開論述", "", "2025.08", "", "False", "False", "True", "False", "False", "False", "False"],
            ["合作夥伴", "第三指標", "第三筆公開論述", "", "2025.09", "", "False", "False", "True", "False", "False", "False", "False"],
        ],
        merges={SHEET_PUBLIC_METRICS: ["F7:F9"]},
    )

    generate_excel_preview(
        workbook,
        output_dir,
        captured_date=CAPTURED_DATE,
        normalized_at=NORMALIZED_AT,
    )

    public_metrics = _read_json(output_dir / "public_metrics.json")
    assert public_metrics[1]["reference_source"] == "Shared reference"
    assert public_metrics[2]["reference_source"] == "Shared reference"


def test_excel_preview_does_not_expand_horizontal_merged_header(tmp_path):
    workbook = tmp_path / "horizontal_merge.xlsx"
    output_dir = tmp_path / "horizontal_merge_preview"
    _write_preview_workbook(workbook, merges={SHEET_MERCHANT_CASES: ["L6:M6"]})

    summary = generate_excel_preview(
        workbook,
        output_dir,
        captured_date=CAPTURED_DATE,
        normalized_at=NORMALIZED_AT,
    )

    merchant_cases = _read_json(output_dir / "merchant_cases.json")
    assert summary["sheet_counts"][SHEET_MERCHANT_CASES] == 1
    assert merchant_cases[0]["notes"] == "停止營運，保留文章"


def test_excel_preview_preflight_rejects_renamed_header(tmp_path):
    workbook = tmp_path / "renamed_header.xlsx"
    output_dir = tmp_path / "preview"
    sheets = _preview_workbook_sheets()
    sheets[SHEET_MERCHANT_CASES][5][3] = "Wrong Handle"
    _write_xlsx(workbook, sheets)

    with pytest.raises(ExcelPreviewError) as exc_info:
        generate_excel_preview(
            workbook,
            output_dir,
            captured_date=CAPTURED_DATE,
            normalized_at=NORMALIZED_AT,
        )

    message = str(exc_info.value)
    assert "Wrong Handle" in message
    assert "Handle" in message


def test_governance_preview_store_supports_brand_handle_and_website_matches():
    store = GovernancePreviewStore(
        restricted_customers=[
            {
                "brand_name": "Secret Brand",
                "merchant_handle": "secret-handle",
                "website_url": "https://www.secret.example",
            }
        ],
        handle_mappings=[],
    )

    assert store.find_restricted("Secret Brand")
    assert store.find_restricted("secret-handle")
    assert store.find_restricted("secret.example")
    assert store.to_governance_index().check_text("請查 secret.example").blocked


def test_restricted_customer_alias_matching():
    store = GovernancePreviewStore(
        restricted_customers=[
            {"brand_name": "SHU UEMURA／植村秀", "website_url": "https://example.com/shu"},
            {"brand_name": "HR／赫蓮娜", "website_url": "https://example.com/hr"},
            {"brand_name": "Haagen-Dazs／哈根達斯", "website_url": "https://example.com/ice"},
        ],
        handle_mappings=[],
    )

    assert store.find_restricted("植村秀")
    assert store.find_restricted("赫蓮娜")
    assert store.find_restricted("哈根達斯")
    governance_index = store.to_governance_index()
    assert governance_index.check_text("請整理植村秀案例").blocked
    assert governance_index.check_text("請整理赫蓮娜案例").blocked
    assert governance_index.check_text("請整理哈根達斯案例").blocked


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _preview_workbook_sheets(public_metric_rows=None) -> dict:
    public_metric_rows = public_metric_rows or [
        ["合作夥伴", "夥伴成效", "合作後每月詢問與試用數增加 300%", "", "2025.07", "新聞稿標題", "False", "False", "True", "True", "False", "False", "False"],
    ]
    return {
        "商家夥伴案例資料庫": [
            ["篩選內容年份"],
            [],
            [],
            [],
            [],
            ["採訪年份", "狀態", "商家 / 夥伴名稱", "Handle", "Sales Category LV1", "Sales Category LV2", "內容相關標籤", "文章", "影片", "Podcast", "新聞", "備註"],
            ["2025", "已關店、轉走/結束合作關係", "", "1982kidsstore", "", "", "OMO, 會員經營", "1982 小時候案例", "暫時下架", "-", "", "停止營運，保留文章"],
        ],
        "「不可公開」客戶名單": [
            ["更新時間：2025/9/2"],
            ["注意事項"],
            ["SHOPLINE 不可公開客戶名單"],
            ["更新年份", "客戶品牌", "網站", "Sales Category LV1", "是否有簽保密 NDA", "NDA是否已上傳Salesforce", "店家狀況（例如：店家對中資事件敏感...）", "填表人\n（部門/名字）"],
            ["2025", "Secret Brand", "https://www.secret.example", "流行服飾", "True", "False", "NDA", "MKT/Test"],
        ],
        "「可公開」對外數據": [
            [],
            ["2025-08-22"],
            ["資料管理 PIC"],
            ["Kevin Wu"],
            ["", "", "", "", "", "", "是否可於以下渠道曝光"],
            ["類型", "指標", "論述", "備註", "更新時間", "參考新聞連結", "新聞稿", "自媒體", "Saleskits", "口頭說明", "演講簡報", "官網/ 招募網站", "廣告"],
            *public_metric_rows,
        ],
        "待確認數據": [
            [],
            [],
            ["會員規模", "品牌會員總數", "品牌總會員數累積超過 XX 百萬人", ""],
        ],
        "handle 比對": [
            ["Handle", "Name (with Link)", "Lv1 Sales Category", "Lv2 Sales Category 1st"],
            ["1982kidsstore", "1982kids", "旅遊＆文創服務", "藝術文創/圖書文具/宗教"],
        ],
    }


def _write_preview_workbook(path: Path, public_metric_rows=None, merges=None) -> None:
    _write_xlsx(path, _preview_workbook_sheets(public_metric_rows=public_metric_rows), merges=merges)


def _write_large_baseline_workbook(path: Path) -> None:
    merchant_rows = [
        ["篩選內容年份"],
        [],
        [],
        [],
        [],
        ["採訪年份", "狀態", "商家 / 夥伴名稱", "Handle", "Sales Category LV1", "Sales Category LV2", "內容相關標籤", "文章", "影片", "Podcast", "新聞", "備註"],
    ]
    for source_row in range(7, 127):
        brand = f"Brand {source_row}"
        handle = f"handle{source_row}"
        interview_year = "2025"
        article = f"案例文章 {source_row}"
        notes = ""
        if source_row == 9:
            brand = "NISORO"
            handle = "nisoro"
            interview_year = "2024"
            article = "NISORO 會員經營案例"
        elif source_row == 21:
            brand = "NISORO"
            handle = "nisoro"
            interview_year = "2025"
            article = "NISORO 訂閱制案例"
        elif source_row == 38:
            brand = "簡單 JAN DAN"
            handle = "jandan"
            interview_year = "2024"
            article = "簡單 JAN DAN 品牌故事"
        elif source_row == 45:
            brand = "Shared Handle A"
            handle = "sharedhandle"
            article = "Shared Handle A 案例"
        elif source_row == 46:
            brand = "Shared Handle B"
            handle = "sharedhandle"
            article = "Shared Handle B 案例"
        elif source_row == 68:
            brand = "簡單 JAN DAN"
            handle = "jandan"
            interview_year = "2025"
            article = "簡單 JAN DAN 社群經營"
        elif source_row == 20:
            brand = "NEOFLAM台灣官方購物網"
            handle = "neoflam"
            article = "暫時下架"
        elif source_row == 50:
            brand = "No Handle Brand"
            handle = "-"

        merchant_rows.append(
            [
                interview_year,
                "現有商家",
                brand,
                handle,
                "美食",
                "烘焙",
                "OMO",
                article,
                "-",
                "-",
                "-",
                notes,
            ]
        )

    restricted_rows = [
        ["更新時間：2025/9/2"],
        ["注意事項"],
        ["SHOPLINE 不可公開客戶名單"],
        ["更新年份", "客戶品牌", "網站", "Sales Category LV1", "是否有簽保密 NDA", "NDA是否已上傳Salesforce", "店家狀況（例如：店家對中資事件敏感...）", "填表人\n（部門/名字）"],
    ]
    for source_row in range(5, 16):
        restricted_rows.append(
            [
                "2025",
                f"Restricted Brand {source_row}",
                f"https://restricted{source_row}.example",
                "美食",
                "False",
                "False",
                "",
                "",
            ]
        )

    sheets = {
        "商家夥伴案例資料庫": merchant_rows,
        "「不可公開」客戶名單": restricted_rows,
        "「可公開」對外數據": [
            [],
            ["2025-08-22"],
            ["資料管理 PIC"],
            ["Kevin Wu"],
            ["", "", "", "", "", "", "是否可於以下渠道曝光"],
            ["類型", "指標", "論述", "備註", "更新時間", "參考新聞連結", "新聞稿", "自媒體", "Saleskits", "口頭說明", "演講簡報", "官網/ 招募網站", "廣告"],
            ["合作夥伴", "夥伴成效", "合作後每月詢問與試用數增加 300%", "", "2025.07", "", "False", "False", "True", "False", "False", "False", "False"],
        ],
        "待確認數據": [
            [],
            [],
            ["會員規模", "品牌會員總數", "品牌總會員數累積超過 XX 百萬人", ""],
        ],
        "handle 比對": [
            ["Handle", "Name (with Link)", "Lv1 Sales Category", "Lv2 Sales Category 1st"],
            ["nisoro", "NISORO", "醫療與保健", "養生/保健"],
            ["jandan", "簡單 JAN DAN", "美食", "甜點"],
        ],
    }
    _write_xlsx(path, sheets)


def _write_xlsx(path: Path, sheets: dict, merges=None) -> None:
    merges = merges or {}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  {sheet_overrides}
</Types>""".format(
                sheet_overrides="\n  ".join(
                    f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for index in range(1, len(sheets) + 1)
                )
            ),
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    {sheet_entries}
  </sheets>
</workbook>""".format(
                sheet_entries="\n    ".join(
                    f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
                    for index, name in enumerate(sheets, start=1)
                )
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {relationships}
</Relationships>""".format(
                relationships="\n  ".join(
                    f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
                    for index in range(1, len(sheets) + 1)
                )
            ),
        )
        for index, (sheet_name, rows) in enumerate(sheets.items(), start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows, merges.get(sheet_name)))


def _sheet_xml(rows, merges=None):
    merges = merges or []
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            if value is None or value == "":
                continue
            ref = f"{_column_letters(col_index)}{row_index}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    merge_xml = ""
    if merges:
        merge_xml = "\n  <mergeCells count=\"{count}\">\n    {ranges}\n  </mergeCells>".format(
            count=len(merges),
            ranges="\n    ".join(f'<mergeCell ref="{escape(ref)}"/>' for ref in merges),
        )
    return """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    {rows}
  </sheetData>{merge_xml}
</worksheet>""".format(rows="\n    ".join(row_xml), merge_xml=merge_xml)


def _column_letters(index):
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
