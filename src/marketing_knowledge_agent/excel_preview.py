from __future__ import annotations

import json
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

from pydantic import ValidationError

from .excel_ingestion import (
    EXCEL_INGESTION_PLAN,
    PUBLIC_METRIC_CHANNEL_COLUMNS,
    SHEET_HANDLE_MAPPING,
    SHEET_MERCHANT_CASES,
    SHEET_PENDING_METRICS,
    SHEET_PUBLIC_METRICS,
    SHEET_RESTRICTED_CUSTOMERS,
    build_handle_mapping,
    contains_governance_risk,
    normalize_handle_mapping_row,
    normalize_merchant_case_row,
    normalize_pending_metric_row,
    normalize_public_metric_row,
    normalize_restricted_customer_row,
)
from .governance import (
    GovernanceIndex,
    RestrictedCustomerRecord,
    normalize_identity,
    normalize_website_identity,
    split_restricted_aliases,
)
from .models import DocumentMetadata
from .record_identity_lineage import (
    PREVIEW_LINEAGE_FILENAME,
    RECORD_IDENTITY_SCHEME_VERSION,
    preview_merchant_surface_digest,
    preview_merchant_surface_entries,
    preview_merchant_payload_digest,
    preview_merchant_payload_entries,
    workbook_lineage_identity,
)


WORKBOOK_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CELL_REF_RE = re.compile(r"([A-Z]+)(\d+)")
PREVIEW_FILENAMES = {
    "merchant_cases": "merchant_cases.json",
    "public_metrics": "public_metrics.json",
    "pending_metrics": "pending_metrics.json",
    "restricted_customers": "restricted_customers.json",
    "handle_mappings": "handle_mappings.json",
}
EXPECTED_SHEET_HEADERS = {
    SHEET_MERCHANT_CASES: [
        ("採訪年份",),
        ("狀態",),
        ("商家 / 夥伴名稱", "商家/夥伴名稱"),
        ("Handle",),
        ("Sales Category LV1",),
        ("Sales Category LV2",),
        ("內容相關標籤",),
        ("文章",),
        ("影片",),
        ("Podcast",),
        ("新聞",),
        ("備註",),
    ],
    SHEET_RESTRICTED_CUSTOMERS: [
        ("更新年份",),
        ("客戶品牌",),
        ("網站",),
        ("Sales Category LV1",),
        ("是否有簽保密 NDA",),
        ("NDA是否已上傳Salesforce",),
        ("店家狀況（例如：店家對中資事件敏感...）", "店家狀況"),
        ("填表人\n（部門/名字）", "填表人"),
    ],
    SHEET_PUBLIC_METRICS: [
        ("類型",),
        ("指標",),
        ("論述",),
        ("備註",),
        ("更新時間",),
        ("參考新聞連結",),
        ("新聞稿",),
        ("自媒體",),
        ("Saleskits",),
        ("口頭說明",),
        ("演講簡報",),
        ("官網/ 招募網站", "官網 / 招募網站", "官網/招募網站"),
        ("廣告",),
    ],
    SHEET_HANDLE_MAPPING: [
        ("Handle",),
        ("Name (with Link)", "Name with Link"),
        ("Lv1 Sales Category",),
        ("Lv2 Sales Category 1st",),
    ],
}
EXPECTED_HEADER_ROWS = {
    SHEET_MERCHANT_CASES: 6,
    SHEET_RESTRICTED_CUSTOMERS: 4,
    SHEET_PUBLIC_METRICS: 6,
    SHEET_HANDLE_MAPPING: 1,
}


class ExcelPreviewError(ValueError):
    """Raised when the workbook cannot be converted into a preview."""


class GovernancePreviewStore:
    """JSON-backed governance preview interface, replaceable by SQLite later."""

    def __init__(self, restricted_customers: List[dict], handle_mappings: List[dict]):
        self.restricted_customers = restricted_customers
        self.handle_mappings = handle_mappings

    def to_governance_index(self) -> GovernanceIndex:
        return GovernanceIndex(
            [
                RestrictedCustomerRecord(
                    brand_name=record.get("brand_name") or "",
                    website_url=record.get("website_url"),
                    merchant_handle=record.get("merchant_handle"),
                    restricted_aliases=record.get("restricted_aliases") or split_restricted_aliases(record.get("brand_name")),
                    source_sheet=record.get("source_sheet"),
                    source_row=record.get("source_row"),
                )
                for record in self.restricted_customers
            ]
        )

    def find_restricted(self, query: str) -> List[dict]:
        normalized_query = normalize_identity(query)
        matches = []
        for record in self.restricted_customers:
            candidates = [
                normalize_identity(record.get("brand_name")),
                normalize_identity(record.get("merchant_handle")),
                normalize_identity(record.get("website_url")),
                normalize_website_identity(record.get("website_url")),
                *(
                    normalize_identity(alias)
                    for alias in (record.get("restricted_aliases") or split_restricted_aliases(record.get("brand_name")))
                ),
            ]
            if any(candidate and candidate in normalized_query for candidate in candidates):
                matches.append(record)
        return matches


def generate_excel_preview(
    workbook_path: Path,
    output_dir: Path,
    captured_date: Optional[date] = None,
    normalized_at: Optional[str] = None,
) -> dict:
    workbook_path = Path(workbook_path)
    output_dir = Path(output_dir)
    captured_date = captured_date or date.today()
    normalized_at = normalized_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    sheets = read_xlsx_workbook(workbook_path)
    missing_sheets = [sheet for sheet in EXCEL_INGESTION_PLAN if sheet not in sheets]
    if missing_sheets:
        raise ExcelPreviewError(f"missing required sheets: {', '.join(missing_sheets)}")
    preflight_workbook_headers(sheets)

    validation_errors: List[dict] = []

    handle_mappings = _normalize_sheet_records(
        rows=sheets[SHEET_HANDLE_MAPPING],
        sheet_name=SHEET_HANDLE_MAPPING,
        header_row=1,
        start_row=2,
        normalizer=normalize_handle_mapping_row,
        captured_date=captured_date,
        normalized_at=normalized_at,
        validation_errors=validation_errors,
    )
    handle_mapping_index = build_handle_mapping(handle_mappings)

    merchant_cases = _normalize_sheet_records(
        rows=sheets[SHEET_MERCHANT_CASES],
        sheet_name=SHEET_MERCHANT_CASES,
        header_row=6,
        start_row=7,
        normalizer=lambda row, source_row, captured_date: normalize_merchant_case_row(
            row,
            source_row=source_row,
            captured_date=captured_date,
            handle_mapping=handle_mapping_index,
        ),
        captured_date=captured_date,
        normalized_at=normalized_at,
        validation_errors=validation_errors,
    )
    merchant_case_quality = annotate_merchant_case_record_groups(merchant_cases)
    restricted_customers = _normalize_sheet_records(
        rows=sheets[SHEET_RESTRICTED_CUSTOMERS],
        sheet_name=SHEET_RESTRICTED_CUSTOMERS,
        header_row=4,
        start_row=5,
        normalizer=normalize_restricted_customer_row,
        captured_date=captured_date,
        normalized_at=normalized_at,
        validation_errors=validation_errors,
    )
    public_metrics = _normalize_sheet_records(
        rows=sheets[SHEET_PUBLIC_METRICS],
        sheet_name=SHEET_PUBLIC_METRICS,
        header_row=6,
        start_row=7,
        normalizer=normalize_public_metric_row,
        captured_date=captured_date,
        normalized_at=normalized_at,
        validation_errors=validation_errors,
        fill_down_fields=("類型", "指標"),
    )
    pending_metrics = _normalize_sheet_records(
        rows=sheets[SHEET_PENDING_METRICS],
        sheet_name=SHEET_PENDING_METRICS,
        header_row=None,
        start_row=3,
        headers=["類型", "指標", "論述", "備註"],
        normalizer=normalize_pending_metric_row,
        captured_date=captured_date,
        normalized_at=normalized_at,
        validation_errors=validation_errors,
        fill_down_fields=("類型", "指標"),
    )

    preview = {
        "merchant_cases": merchant_cases,
        "public_metrics": public_metrics,
        "pending_metrics": pending_metrics,
        "restricted_customers": restricted_customers,
        "handle_mappings": handle_mappings,
    }
    summary = build_preview_summary(
        preview=preview,
        merchant_case_quality=merchant_case_quality,
        validation_errors=validation_errors,
        public_metric_headers=_headers_for(sheets[SHEET_PUBLIC_METRICS], 6),
        workbook_path=workbook_path,
        output_dir=output_dir,
        captured_date=captured_date,
        normalized_at=normalized_at,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for key, filename in PREVIEW_FILENAMES.items():
        _write_json(output_dir / filename, preview[key])
    _write_json(
        output_dir / PREVIEW_LINEAGE_FILENAME,
        _workbook_lineage_declaration(
            workbook_path=workbook_path,
            sheets=sheets,
            merchant_cases=merchant_cases,
            captured_date=captured_date,
            normalized_at=normalized_at,
        ),
    )
    _write_text(output_dir / "preview_summary.md", render_preview_summary(summary))
    _write_text(output_dir / "validation_errors.md", render_validation_errors(validation_errors))

    return summary


def _workbook_lineage_declaration(
    *,
    workbook_path: Path,
    sheets: Dict[str, List[List[object]]],
    merchant_cases: List[dict],
    captured_date: date,
    normalized_at: str,
) -> dict:
    """Record which workbook this preview was produced from.

    Provenance only: the preview itself is never gated on it. Downstream paths that bind existing
    row-coordinate review decisions read this to refuse a workbook the decisions were not
    reviewed against.

    The row identity surface and the semantic payload digest are stamped alongside so the
    declaration can be checked against the ``merchant_cases.json`` written beside it. That file is
    written first and this one last, so without the digests a run interrupted between the two
    would leave a new payload wearing a stale declaration, with nothing to tell them apart. The
    two digests are separate because they fail separately: the first states which merchant each
    row coordinate names, the second states what those merchants say.

    ``merchant_cases`` is already JSON-safe by this point — ``_normalize_sheet_records`` runs
    ``_json_safe`` over every record — so the digest stamped here canonicalizes exactly the value
    space the guard reads back out of the written file.
    """
    source_rows = [
        record["source_row"] for record in merchant_cases if isinstance(record.get("source_row"), int)
    ]
    lineage = workbook_lineage_identity(
        workbook_path,
        merchant_sheet_name=SHEET_MERCHANT_CASES,
        merchant_header_row=EXPECTED_HEADER_ROWS[SHEET_MERCHANT_CASES],
        merchant_headers=_normalized_headers_for(
            sheets[SHEET_MERCHANT_CASES], EXPECTED_HEADER_ROWS[SHEET_MERCHANT_CASES]
        ),
        merchant_record_count=len(merchant_cases),
        merchant_source_row_min=min(source_rows) if source_rows else None,
        merchant_source_row_max=max(source_rows) if source_rows else None,
    )
    return {
        "record_identity_scheme_version": RECORD_IDENTITY_SCHEME_VERSION,
        "workbook": lineage,
        "merchant_row_identity_surface_digest": preview_merchant_surface_digest(
            preview_merchant_surface_entries(merchant_cases)
        ),
        "merchant_payload_semantic_digest": preview_merchant_payload_digest(
            preview_merchant_payload_entries(merchant_cases)
        ),
        "workbook_path": str(workbook_path),
        "captured_date": captured_date.isoformat(),
        "normalized_at": normalized_at,
    }


def build_preview_summary(
    preview: Dict[str, List[dict]],
    merchant_case_quality: dict,
    validation_errors: List[dict],
    public_metric_headers: List[object],
    workbook_path: Path,
    output_dir: Path,
    captured_date: date,
    normalized_at: str,
) -> dict:
    merchant_cases = preview["merchant_cases"]
    public_metrics = preview["public_metrics"]
    pending_metrics = preview["pending_metrics"]
    restricted_customers = preview["restricted_customers"]
    handle_mappings = preview["handle_mappings"]
    unknown_channels = _unknown_exposure_channel_columns(public_metric_headers)
    governance_risks = [
        record
        for record in merchant_cases
        if contains_governance_risk(
            [
                record.get("merchant_status"),
                record.get("notes"),
                record.get("article_title"),
                record.get("video_title"),
            ]
        )
        or record.get("can_quote_externally") is False
    ]
    can_enter_content_index_count = sum(
        1
        for record in merchant_cases + public_metrics
        if record.get("can_enter_content_index", True)
    )
    no_valid_content_asset_records = [
        record for record in merchant_cases if record.get("no_valid_content_asset")
    ]
    public_metric_restricted_note_records = [
        record for record in public_metrics if record.get("restricted_note")
    ]

    return {
        "workbook_path": str(workbook_path),
        "output_dir": str(output_dir),
        "captured_date": captured_date.isoformat(),
        "normalized_at": normalized_at,
        "sheet_counts": {
            SHEET_MERCHANT_CASES: len(merchant_cases),
            SHEET_PUBLIC_METRICS: len(public_metrics),
            SHEET_PENDING_METRICS: len(pending_metrics),
            SHEET_RESTRICTED_CUSTOMERS: len(restricted_customers),
            SHEET_HANDLE_MAPPING: len(handle_mappings),
        },
        "content_index_preview_count": len(merchant_cases) + len(public_metrics),
        "can_enter_content_index_count": can_enter_content_index_count,
        "restricted_customer_count": len(restricted_customers),
        "pending_metric_count": len(pending_metrics),
        "missing_brand_name_count": sum(1 for record in merchant_cases if not record.get("brand_name")),
        "missing_handle_count": sum(1 for record in merchant_cases if not record.get("merchant_handle")),
        "public_metric_missing_channel_count": sum(1 for record in public_metrics if not record.get("allowed_exposure_channels")),
        "public_metric_missing_reference_source_count": sum(1 for record in public_metrics if not record.get("reference_source")),
        "public_metric_restricted_note_count": len(public_metric_restricted_note_records),
        "restricted_customer_blank_status_count": sum(
            1 for record in restricted_customers if record.get("restricted_reason") == "不可公開客戶名單"
        ),
        "restricted_customer_blank_submitted_by_count": sum(
            1 for record in restricted_customers if not record.get("submitted_by")
        ),
        "no_valid_content_asset_count": len(no_valid_content_asset_records),
        "unknown_exposure_channel_columns": unknown_channels,
        "merchant_case_governance_risk_count": len(governance_risks),
        "merchant_case_governance_risks": [
            {
                "source_row": record.get("source_row"),
                "brand_name": record.get("brand_name"),
                "merchant_status": record.get("merchant_status"),
                "notes": record.get("notes"),
                "issue_types": record.get("governance_issue_types", []),
                "risk_reasons": record.get("governance_risk_reasons", []),
                "risk_fields": record.get("governance_risk_fields", []),
                "invalid_asset_fields": record.get("invalid_asset_fields", []),
                "invalid_asset_values": record.get("invalid_asset_values", {}),
            }
            for record in governance_risks[:20]
        ],
        "no_valid_content_asset_records": [
            {
                "source_row": record.get("source_row"),
                "brand_name": record.get("brand_name"),
            }
            for record in no_valid_content_asset_records[:20]
        ],
        "same_brand_multiple_records": merchant_case_quality["same_brand_multiple_records"],
        "same_handle_multiple_records": merchant_case_quality["same_handle_multiple_records"],
        "suspected_duplicate_review_items": merchant_case_quality["suspected_duplicate_review_items"],
        "same_brand_multiple_record_count": len(merchant_case_quality["same_brand_multiple_records"]),
        "same_handle_multiple_record_count": len(merchant_case_quality["same_handle_multiple_records"]),
        "suspected_duplicate_review_count": len(merchant_case_quality["suspected_duplicate_review_items"]),
        "validation_error_count": len(validation_errors),
    }


def annotate_merchant_case_record_groups(records: List[dict]) -> dict:
    for record in records:
        record.setdefault("same_brand_multiple_records", False)
        record.setdefault("same_handle_multiple_records", False)
        record.setdefault("multi_interview_record", False)
        record.setdefault("suspected_duplicate_review", False)

    brand_groups = _group_records(records, "brand_name")
    handle_groups = _group_records(records, "merchant_handle")
    duplicate_groups = _group_records_by_key(records, _merchant_case_duplicate_key)

    same_brand_multiple_records = _render_record_groups(brand_groups, label_key="brand_name")
    same_handle_multiple_records = _render_record_groups(handle_groups, label_key="merchant_handle")
    suspected_duplicate_review_items = _render_record_groups(
        duplicate_groups,
        label_key="duplicate_key",
        include_single=False,
    )

    for group in brand_groups.values():
        if len(group) <= 1:
            continue
        for record in group:
            record["same_brand_multiple_records"] = True
            record["multi_interview_record"] = True

    for group in handle_groups.values():
        if len(group) <= 1:
            continue
        for record in group:
            record["same_handle_multiple_records"] = True
            record["multi_interview_record"] = True

    for group in duplicate_groups.values():
        if len(group) <= 1:
            continue
        for record in group:
            record["suspected_duplicate_review"] = True

    return {
        "same_brand_multiple_records": same_brand_multiple_records,
        "same_handle_multiple_records": same_handle_multiple_records,
        "suspected_duplicate_review_items": suspected_duplicate_review_items,
    }


def _group_records(records: List[dict], field: str) -> Dict[str, List[dict]]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for record in records:
        key = normalize_identity(record.get(field))
        if key:
            groups[key].append(record)
    return groups


def _group_records_by_key(records: List[dict], key_builder) -> Dict[str, List[dict]]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for record in records:
        key = key_builder(record)
        if key:
            groups[key].append(record)
    return groups


def _merchant_case_duplicate_key(record: dict) -> str:
    parts = [
        normalize_identity(record.get("brand_name")),
        normalize_identity(record.get("merchant_handle")),
        str(record.get("interview_year") or ""),
        normalize_identity(record.get("article_title")),
        normalize_identity(record.get("video_title")),
        normalize_identity(record.get("podcast_title")),
        normalize_identity(record.get("news_title")),
        normalize_identity(record.get("source_sheet")),
    ]
    return "|".join(parts)


def _render_record_groups(
    groups: Dict[str, List[dict]],
    label_key: str,
    include_single: bool = False,
) -> List[dict]:
    rendered = []
    for key, group in groups.items():
        if len(group) <= 1 and not include_single:
            continue
        first = group[0]
        rendered.append(
            {
                label_key: first.get(label_key) or key,
                "count": len(group),
                "source_rows": [record.get("source_row") for record in group],
                "brand_names": sorted({record.get("brand_name") for record in group if record.get("brand_name")}),
                "merchant_handles": sorted(
                    {record.get("merchant_handle") for record in group if record.get("merchant_handle")}
                ),
            }
        )
    return rendered


def render_preview_summary(summary: dict) -> str:
    lines = [
        "# Excel Preview Summary",
        "",
        f"- Workbook: `{summary['workbook_path']}`",
        f"- Output: `{summary['output_dir']}`",
        f"- Captured date: `{summary['captured_date']}`",
        f"- Normalized at: `{summary['normalized_at']}`",
        "",
        "## Record Counts",
        "",
    ]
    for sheet, count in summary["sheet_counts"].items():
        lines.append(f"- `{sheet}`: {count}")
    lines.extend(
        [
            "",
            "## Governance Counts",
            "",
            f"- Content index preview records: {summary['content_index_preview_count']}",
            f"- Can enter content index records: {summary['can_enter_content_index_count']}",
            f"- Restricted customers: {summary['restricted_customer_count']}",
            f"- Pending metrics: {summary['pending_metric_count']}",
            f"- Missing merchant brand_name: {summary['missing_brand_name_count']}",
            f"- Missing merchant handle: {summary['missing_handle_count']}",
            f"- Public metrics missing allowed_exposure_channels: {summary['public_metric_missing_channel_count']}",
            f"- Public metrics missing reference_source: {summary['public_metric_missing_reference_source_count']}",
            f"- Public metrics with restricted_note: {summary['public_metric_restricted_note_count']}",
            f"- Restricted customers blank 店家狀況: {summary['restricted_customer_blank_status_count']}",
            f"- Restricted customers blank 填表人: {summary['restricted_customer_blank_submitted_by_count']}",
            f"- Merchant cases with no valid content asset: {summary['no_valid_content_asset_count']}",
            f"- Unknown exposure channel columns: {', '.join(summary['unknown_exposure_channel_columns']) or 'None'}",
            f"- Merchant case governance risks: {summary['merchant_case_governance_risk_count']}",
            f"- Same brand multiple record groups: {summary['same_brand_multiple_record_count']}",
            f"- Same handle multiple record groups: {summary['same_handle_multiple_record_count']}",
            f"- Suspected duplicate review groups: {summary['suspected_duplicate_review_count']}",
            f"- Validation errors: {summary['validation_error_count']}",
            "",
            "## Merchant Case Governance Risks",
            "",
        ]
    )
    if summary["merchant_case_governance_risks"]:
        for risk in summary["merchant_case_governance_risks"]:
            lines.append(
                f"- row {risk['source_row']}: {risk.get('brand_name') or '(missing brand)'}; "
                f"issue_type={', '.join(risk.get('issue_types') or []) or 'unknown'}; "
                f"risk_reasons={', '.join(risk.get('risk_reasons') or []) or 'None'}; "
                f"risk_fields={', '.join(risk.get('risk_fields') or []) or 'None'}; "
                f"status={risk.get('merchant_status')}; notes={risk.get('notes')}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Merchant Cases With No Valid Content Asset", ""])
    if summary["no_valid_content_asset_records"]:
        for record in summary["no_valid_content_asset_records"]:
            lines.append(f"- row {record['source_row']}: {record.get('brand_name') or '(missing brand)'}")
    else:
        lines.append("- None")
    lines.extend(["", "## Same Brand Multiple Records", ""])
    if summary["same_brand_multiple_records"]:
        for group in summary["same_brand_multiple_records"][:20]:
            lines.append(
                f"- {group['brand_name']}: {group['count']} records; rows={group['source_rows']}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Same Handle Multiple Records", ""])
    if summary["same_handle_multiple_records"]:
        for group in summary["same_handle_multiple_records"][:20]:
            lines.append(
                f"- {group['merchant_handle']}: {group['count']} records; rows={group['source_rows']}; "
                f"brands={', '.join(group['brand_names'])}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Suspected Duplicate Review Items", ""])
    if summary["suspected_duplicate_review_items"]:
        for group in summary["suspected_duplicate_review_items"][:20]:
            lines.append(f"- rows={group['source_rows']}; brands={', '.join(group['brand_names'])}")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Next Human Review Suggestions",
            "",
            "- Review restricted customer preview before any future governance table import.",
            "- Review merchant cases with governance risks before allowing external use.",
            "- Fill missing merchant brand_name / handle where possible.",
            "- Confirm public metrics with no allowed exposure channels before using them.",
            "- Keep this preview separate from Obsidian Vault until reviewed.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_validation_errors(validation_errors: List[dict]) -> str:
    lines = ["# Excel Preview Validation Errors", ""]
    if not validation_errors:
        lines.append("No validation errors.")
        return "\n".join(lines) + "\n"
    for error in validation_errors:
        lines.extend(
            [
                f"## {error['source_sheet']} row {error['source_row']}",
                "",
                f"- record_type: `{error.get('record_type')}`",
                f"- error: `{error['error']}`",
                "",
            ]
        )
    return "\n".join(lines)


def read_xlsx_workbook(workbook_path: Path) -> Dict[str, List[List[object]]]:
    workbook_path = Path(workbook_path)
    if not workbook_path.exists():
        raise FileNotFoundError(workbook_path)

    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_paths = _read_sheet_paths(archive)
        return {
            sheet_name: _expand_merged_cells(
                _read_sheet_rows(archive, sheet_path, shared_strings),
                _read_merged_ranges(archive, sheet_path),
            )
            for sheet_name, sheet_path in sheet_paths.items()
        }


def preflight_workbook_headers(sheets: Dict[str, List[List[object]]]) -> None:
    errors = []
    for sheet_name, expected_headers in EXPECTED_SHEET_HEADERS.items():
        actual_headers = _normalized_headers_for(sheets[sheet_name], EXPECTED_HEADER_ROWS[sheet_name])
        expected_display = [_format_expected_header(header) for header in expected_headers]
        if len(actual_headers) != len(expected_headers) or any(
            actual not in expected for actual, expected in zip(actual_headers, expected_headers)
        ):
            errors.append(
                f"{sheet_name}: expected headers {expected_display}; actual headers {actual_headers}"
            )

    if not _pending_metric_has_data_row(sheets[SHEET_PENDING_METRICS]):
        errors.append(f"{SHEET_PENDING_METRICS}: expected row 3+ first 4 columns to contain data")

    if errors:
        raise ExcelPreviewError("workbook preflight failed:\n" + "\n".join(errors))


def _normalize_sheet_records(
    rows: List[List[object]],
    sheet_name: str,
    header_row: Optional[int],
    start_row: int,
    normalizer,
    captured_date: date,
    normalized_at: str,
    validation_errors: List[dict],
    headers: Optional[List[str]] = None,
    fill_down_fields: Tuple[str, ...] = (),
) -> List[dict]:
    records: List[dict] = []
    table_rows = _table_rows(rows, header_row=header_row, start_row=start_row, headers=headers)
    last_values: Dict[str, object] = {}
    for source_row, row in table_rows:
        for field in fill_down_fields:
            if row.get(field):
                last_values[field] = row[field]
            elif field in last_values:
                row[field] = last_values[field]

        payload = normalizer(row, source_row, captured_date)
        payload["normalized_at"] = normalized_at
        try:
            DocumentMetadata(**payload)
        except ValidationError as exc:
            validation_errors.append(
                {
                    "source_sheet": sheet_name,
                    "source_row": source_row,
                    "record_type": payload.get("record_type"),
                    "error": str(exc),
                }
            )
            continue
        records.append(_json_safe(payload))
    return records


def _table_rows(
    rows: List[List[object]],
    header_row: Optional[int],
    start_row: int,
    headers: Optional[List[str]] = None,
) -> List[Tuple[int, Dict[str, object]]]:
    if headers is None:
        headers = [str(value).strip() if value is not None else "" for value in rows[header_row - 1]]

    table_rows: List[Tuple[int, Dict[str, object]]] = []
    for source_row in range(start_row, len(rows) + 1):
        values = rows[source_row - 1]
        row = {
            header: values[index] if index < len(values) else None
            for index, header in enumerate(headers)
            if header
        }
        if any(value not in {None, ""} for value in row.values()):
            table_rows.append((source_row, row))
    return table_rows


def _headers_for(rows: List[List[object]], header_row: int) -> List[object]:
    if len(rows) < header_row:
        return []
    return rows[header_row - 1]


def _normalized_headers_for(rows: List[List[object]], header_row: int) -> List[str]:
    return [
        str(value).strip() if value is not None else ""
        for value in _trim_trailing_empty_cells(_headers_for(rows, header_row))
    ]


def _trim_trailing_empty_cells(row: List[object]) -> List[object]:
    trimmed = list(row)
    while trimmed and trimmed[-1] in {None, ""}:
        trimmed.pop()
    return trimmed


def _format_expected_header(headers: Tuple[str, ...]) -> str:
    return " | ".join(headers)


def _pending_metric_has_data_row(rows: List[List[object]]) -> bool:
    for source_row in range(3, len(rows) + 1):
        first_four_columns = rows[source_row - 1][:4]
        if any(value not in {None, ""} for value in first_four_columns):
            return True
    return False


def _unknown_exposure_channel_columns(headers: List[object]) -> List[str]:
    known_non_channel = {"類型", "指標", "論述", "備註", "更新時間", "參考新聞連結"}
    unknown = []
    for value in headers:
        header = str(value).strip() if value is not None else ""
        if not header or header in known_non_channel or header in PUBLIC_METRIC_CHANNEL_COLUMNS:
            continue
        unknown.append(header)
    return unknown


def _read_sheet_paths(archive: zipfile.ZipFile) -> Dict[str, str]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        relationship.attrib["Id"]: _resolve_workbook_target(relationship.attrib["Target"])
        for relationship in rels.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }
    sheet_paths: Dict[str, str] = {}
    for sheet in workbook.findall(f".//{{{WORKBOOK_NS}}}sheet"):
        rel_id = sheet.attrib.get(f"{{{REL_NS}}}id")
        if rel_id in rel_targets:
            sheet_paths[sheet.attrib["name"]] = rel_targets[rel_id]
    return sheet_paths


def _resolve_workbook_target(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def _read_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall(f"{{{WORKBOOK_NS}}}si"):
        strings.append("".join(text.text or "" for text in item.findall(f".//{{{WORKBOOK_NS}}}t")))
    return strings


def _read_sheet_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: List[str],
) -> List[List[object]]:
    root = ElementTree.fromstring(archive.read(sheet_path))
    rows: List[List[object]] = []
    for row_element in root.findall(f".//{{{WORKBOOK_NS}}}row"):
        row_index = int(row_element.attrib.get("r", len(rows) + 1))
        while len(rows) < row_index:
            rows.append([])
        row_values = rows[row_index - 1]
        for cell in row_element.findall(f"{{{WORKBOOK_NS}}}c"):
            cell_ref = cell.attrib.get("r", "")
            match = CELL_REF_RE.match(cell_ref)
            if not match:
                continue
            column_index = _column_index(match.group(1))
            while len(row_values) <= column_index:
                row_values.append(None)
            row_values[column_index] = _cell_value(cell, shared_strings)
    return rows


def _read_merged_ranges(archive: zipfile.ZipFile, sheet_path: str) -> List[Tuple[int, int, int, int]]:
    root = ElementTree.fromstring(archive.read(sheet_path))
    ranges: List[Tuple[int, int, int, int]] = []
    for merge_cell in root.findall(f".//{{{WORKBOOK_NS}}}mergeCell"):
        ref = merge_cell.attrib.get("ref", "")
        if ":" not in ref:
            continue
        start_ref, end_ref = ref.split(":", 1)
        start = _parse_cell_ref(start_ref)
        end = _parse_cell_ref(end_ref)
        if start is None or end is None:
            continue
        start_col, start_row = start
        end_col, end_row = end
        ranges.append((start_col, start_row, end_col, end_row))
    return ranges


def _expand_merged_cells(
    rows: List[List[object]],
    merged_ranges: List[Tuple[int, int, int, int]],
) -> List[List[object]]:
    physical_value_rows = {
        row_index
        for row_index, row_values in enumerate(rows, start=1)
        if any(value not in {None, ""} for value in row_values)
    }
    for start_col, start_row, end_col, end_row in merged_ranges:
        if start_col != end_col or start_row == end_row:
            continue
        if start_row - 1 >= len(rows) or start_col >= len(rows[start_row - 1]):
            continue
        merged_value = rows[start_row - 1][start_col]
        if merged_value in {None, ""}:
            continue
        for row_index in range(start_row, end_row + 1):
            if row_index not in physical_value_rows:
                continue
            while len(rows) < row_index:
                rows.append([])
            row_values = rows[row_index - 1]
            for column_index in range(start_col, end_col + 1):
                while len(row_values) <= column_index:
                    row_values.append(None)
                if row_values[column_index] in {None, ""}:
                    row_values[column_index] = merged_value
    return rows


def _parse_cell_ref(cell_ref: str) -> Optional[Tuple[int, int]]:
    match = CELL_REF_RE.match(cell_ref)
    if not match:
        return None
    return _column_index(match.group(1)), int(match.group(2))


def _cell_value(cell: ElementTree.Element, shared_strings: List[str]) -> object:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(f".//{{{WORKBOOK_NS}}}t"))

    value = cell.find(f"{{{WORKBOOK_NS}}}v")
    if value is None or value.text is None:
        return None
    raw_value = value.text
    if cell_type == "s":
        index = int(raw_value)
        return shared_strings[index] if index < len(shared_strings) else raw_value
    if cell_type == "b":
        return raw_value == "1"
    return raw_value


def _column_index(column_letters: str) -> int:
    index = 0
    for char in column_letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
