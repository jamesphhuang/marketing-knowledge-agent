from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


REVIEW_COLUMNS = [
    "source_sheet",
    "source_row",
    "record_type",
    "brand_name",
    "merchant_handle",
    "metric_name",
    "title",
    "current_issue",
    "issue_type",
    "governance_issue_types",
    "governance_risk_reasons",
    "governance_risk_fields",
    "invalid_asset_fields",
    "invalid_asset_values",
    "suggested_action",
    "review_decision",
    "can_enter_vault",
    "can_enter_content_index",
    "can_enter_governance_table",
    "can_quote_externally",
    "allowed_exposure_channels",
    "final_status",
    "reviewer",
    "reviewed_at",
    "notes",
]

PREVIEW_FILES = {
    "merchant_cases": "merchant_cases.json",
    "public_metrics": "public_metrics.json",
    "pending_metrics": "pending_metrics.json",
    "restricted_customers": "restricted_customers.json",
    "handle_mappings": "handle_mappings.json",
}

COMPETITOR_TERMS = ("Shopify", "91APP", "EasyStore", "BVSHOP", "Cyberbiz", "meepshop", "WACA")


class ReviewTemplateError(ValueError):
    """Raised when review template inputs are missing or invalid."""


def generate_review_template(preview_dir: Path, output_path: Path, summary_output_path: Path) -> dict:
    preview = load_preview_records(preview_dir)
    rows = build_expected_review_rows(preview)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(output_path, rows)
    summary = build_review_summary(rows)
    summary.update(
        {
            "preview_dir": str(preview_dir),
            "output_path": str(output_path),
            "summary_output_path": str(summary_output_path),
        }
    )
    summary_output_path.write_text(render_review_summary(summary), encoding="utf-8")
    return summary


def build_expected_review_rows(preview: Dict[str, List[dict]]) -> List[dict]:
    rows: List[dict] = []
    rows.extend(_merchant_review_rows(preview["merchant_cases"]))
    rows.extend(_restricted_customer_review_rows(preview["restricted_customers"]))
    rows.extend(_public_metric_review_rows(preview["public_metrics"]))
    rows.extend(_pending_metric_review_rows(preview["pending_metrics"]))
    return rows


def load_preview_records(preview_dir: Path) -> Dict[str, List[dict]]:
    preview_dir = Path(preview_dir)
    records = {}
    for key, filename in PREVIEW_FILES.items():
        path = preview_dir / filename
        if not path.exists():
            raise ReviewTemplateError(f"missing preview file: {path}")
        records[key] = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records[key], list):
            raise ReviewTemplateError(f"preview file must contain a JSON list: {path}")
    return records


def build_review_summary(rows: List[dict]) -> dict:
    record_type_counts = Counter(row["record_type"] for row in rows)
    suggested_action_counts = Counter(row["suggested_action"] for row in rows)
    issue_type_counts: Counter = Counter()
    for row in rows:
        for issue_type in _split_pipe(row["issue_type"]):
            issue_type_counts[issue_type] += 1

    return {
        "total_review_rows": len(rows),
        "record_type_counts": dict(sorted(record_type_counts.items())),
        "issue_type_counts": dict(sorted(issue_type_counts.items())),
        "suggested_action_counts": dict(sorted(suggested_action_counts.items())),
        "exclude_count": suggested_action_counts.get("exclude", 0),
        "exclude_from_content_index_count": suggested_action_counts.get("exclude_from_content_index", 0),
        "enter_governance_table_only_count": suggested_action_counts.get("enter_governance_table_only", 0),
        "keep_internal_only_count": suggested_action_counts.get("keep_internal_only", 0),
        "enrich_metadata_count": suggested_action_counts.get("enrich_metadata", 0),
        "restricted_use_only_count": suggested_action_counts.get("restricted_use_only", 0),
        "keep_all_records_count": suggested_action_counts.get("keep_all_records", 0),
        "same_brand_multiple_records_count": sum(
            1 for row in rows if "same_brand_multiple_records" in _split_pipe(row["issue_type"])
        ),
        "same_handle_multiple_records_count": sum(
            1 for row in rows if "same_handle_multiple_records" in _split_pipe(row["issue_type"])
        ),
        "suspected_duplicate_review_count": sum(
            1 for row in rows if "suspected_duplicate_review" in _split_pipe(row["issue_type"])
        ),
    }


def render_review_summary(summary: dict) -> str:
    lines = [
        "# Review Decisions Summary",
        "",
        f"- Preview dir: `{summary['preview_dir']}`",
        f"- CSV output: `{summary['output_path']}`",
        f"- Review summary output: `{summary['summary_output_path']}`",
        f"- Total rows requiring human review: {summary['total_review_rows']}",
        "",
        "## Counts By Record Type",
        "",
    ]
    lines.extend(_render_counter(summary["record_type_counts"]))
    lines.extend(["", "## Counts By Issue Type", ""])
    lines.extend(_render_counter(summary["issue_type_counts"]))
    lines.extend(["", "## Counts By Suggested Action", ""])
    lines.extend(_render_counter(summary["suggested_action_counts"]))
    lines.extend(
        [
            "",
            "## Required Review Signals",
            "",
            f"- suggested exclude: {summary['exclude_count']}",
            f"- suggested exclude_from_content_index: {summary['exclude_from_content_index_count']}",
            f"- suggested enter_governance_table_only: {summary['enter_governance_table_only_count']}",
            f"- suggested keep_internal_only: {summary['keep_internal_only_count']}",
            f"- suggested enrich_metadata: {summary['enrich_metadata_count']}",
            f"- suggested restricted_use_only: {summary['restricted_use_only_count']}",
            f"- suggested keep_all_records: {summary['keep_all_records_count']}",
            f"- same_brand_multiple_records: {summary['same_brand_multiple_records_count']}",
            f"- same_handle_multiple_records: {summary['same_handle_multiple_records_count']}",
            f"- suspected_duplicate_review: {summary['suspected_duplicate_review_count']}",
            f"- has suspected duplicate review item: {_yes_no(summary['suspected_duplicate_review_count'] > 0)}",
            "",
            "## Next Human Review Notes",
            "",
            "- `review_decision`, `reviewer`, and `reviewed_at` must be filled manually.",
            "- Decisions have not been applied.",
            "- Obsidian has not been synced.",
            "- No formal content index has been built.",
            "- `same_brand_multiple_records` and `same_handle_multiple_records` are informational review signals, not duplicate errors.",
        ]
    )
    return "\n".join(lines) + "\n"


def _merchant_review_rows(records: Iterable[dict]) -> List[dict]:
    rows = []
    for record in records:
        issue_types = _merchant_issue_types(record)
        if not issue_types:
            continue
        suggested_action = _merchant_suggested_action(record, issue_types)
        rows.append(
            _base_row(
                record,
                current_issue=_merchant_current_issue(record, issue_types),
                issue_type=issue_types,
                suggested_action=suggested_action,
                can_enter_vault=True,
                can_enter_content_index=_merchant_can_enter_content_index(record),
                can_enter_governance_table=False,
                can_quote_externally=_merchant_can_quote_externally(record, issue_types),
                final_status="review_required",
            )
        )
    return rows


def _restricted_customer_review_rows(records: Iterable[dict]) -> List[dict]:
    return [
        _base_row(
            record,
            current_issue="此資料來自不可公開客戶名單，只能進 governance denylist，不得進一般 RAG citation 或對外引用。",
            issue_type=["restricted_customer"],
            suggested_action="enter_governance_table_only",
            can_enter_vault=False,
            can_enter_content_index=False,
            can_enter_governance_table=True,
            can_quote_externally=False,
            final_status="restricted",
        )
        for record in records
    ]


def _public_metric_review_rows(records: Iterable[dict]) -> List[dict]:
    rows = []
    for record in records:
        issue_types = _public_metric_issue_types(record)
        if not issue_types:
            continue
        suggested_action = "needs_update" if record.get("missing_allowed_exposure_channels") else "restricted_use_only"
        can_enter_content_index = not record.get("missing_allowed_exposure_channels")
        can_quote_externally = False if record.get("missing_allowed_exposure_channels") else record.get("can_quote_externally", True)
        rows.append(
            _base_row(
                record,
                current_issue=_public_metric_current_issue(record, issue_types),
                issue_type=issue_types,
                suggested_action=suggested_action,
                can_enter_vault=True,
                can_enter_content_index=can_enter_content_index,
                can_enter_governance_table=False,
                can_quote_externally=can_quote_externally,
                final_status="review_required",
            )
        )
    return rows


def _pending_metric_review_rows(records: Iterable[dict]) -> List[dict]:
    return [
        _base_row(
            record,
            current_issue="此資料為待確認數據，只能內部盤點，不可對外引用。",
            issue_type=["pending_metric"],
            suggested_action="keep_internal_only",
            can_enter_vault=False,
            can_enter_content_index=False,
            can_enter_governance_table=False,
            can_quote_externally=False,
            final_status="pending_review",
        )
        for record in records
    ]


def _base_row(
    record: dict,
    current_issue: str,
    issue_type: List[str],
    suggested_action: str,
    can_enter_vault: bool,
    can_enter_content_index: bool,
    can_enter_governance_table: bool,
    can_quote_externally: bool,
    final_status: str,
) -> dict:
    return {
        "source_sheet": record.get("source_sheet"),
        "source_row": record.get("source_row"),
        "record_type": record.get("record_type"),
        "brand_name": record.get("brand_name"),
        "merchant_handle": record.get("merchant_handle"),
        "metric_name": record.get("metric_name"),
        "title": record.get("title"),
        "current_issue": current_issue,
        "issue_type": _join_list(issue_type),
        "governance_issue_types": _join_list(record.get("governance_issue_types", [])),
        "governance_risk_reasons": _join_list(record.get("governance_risk_reasons", [])),
        "governance_risk_fields": _join_list(record.get("governance_risk_fields", [])),
        "invalid_asset_fields": _join_list(record.get("invalid_asset_fields", [])),
        "invalid_asset_values": _format_dict(record.get("invalid_asset_values", {})),
        "suggested_action": suggested_action,
        "review_decision": "",
        "can_enter_vault": _bool_text(can_enter_vault),
        "can_enter_content_index": _bool_text(can_enter_content_index),
        "can_enter_governance_table": _bool_text(can_enter_governance_table),
        "can_quote_externally": _bool_text(can_quote_externally),
        "allowed_exposure_channels": _join_list(record.get("allowed_exposure_channels", [])),
        "final_status": final_status,
        "reviewer": "",
        "reviewed_at": "",
        "notes": "",
    }


def _merchant_issue_types(record: dict) -> List[str]:
    issue_types = []
    issue_types.extend(record.get("governance_issue_types", []))
    if record.get("governance_risk_reasons"):
        _append_unique(issue_types, "governance_risk")
    if record.get("no_valid_content_asset"):
        _append_unique(issue_types, "no_valid_content_asset")
    if record.get("can_quote_externally") is False:
        _append_unique(issue_types, "can_quote_externally_false")
    if record.get("can_enter_content_index") is False:
        _append_unique(issue_types, "cannot_enter_content_index")
    if not record.get("merchant_handle"):
        _append_unique(issue_types, "missing_merchant_handle")
    if record.get("same_brand_multiple_records"):
        _append_unique(issue_types, "same_brand_multiple_records")
    if record.get("same_handle_multiple_records"):
        _append_unique(issue_types, "same_handle_multiple_records")
    if record.get("suspected_duplicate_review"):
        _append_unique(issue_types, "suspected_duplicate_review")
    return issue_types


def _merchant_suggested_action(record: dict, issue_types: List[str]) -> str:
    text = " ".join(record.get("governance_risk_reasons", []) + [record.get("notes") or "", record.get("merchant_status") or ""])
    if "suspected_duplicate_review" in issue_types:
        return "manual_review"
    if any(term in text for term in COMPETITOR_TERMS):
        return "exclude"
    if "請我們下架內容" in text or "下架內容" in text:
        return "exclude"
    if "停止營運" in text or "已關店" in text:
        return "approve_internal_only"
    if "智慧廣告系統停用" in text:
        return "restricted_use_only"
    if record.get("no_valid_content_asset"):
        return "exclude_from_content_index"
    if "審核中" in text or any("審核中" in value for value in record.get("invalid_asset_values", {}).values()):
        return "needs_update"
    if not record.get("merchant_handle"):
        return "enrich_metadata"
    if record.get("same_brand_multiple_records") or record.get("same_handle_multiple_records"):
        return "keep_all_records"
    if record.get("governance_risk_reasons"):
        return "restricted_use_only"
    return "manual_review"


def _merchant_current_issue(record: dict, issue_types: List[str]) -> str:
    issues = []
    if record.get("governance_risk_reasons"):
        issues.append(f"治理風險：{_join_list(record.get('governance_risk_reasons'))}")
    if record.get("no_valid_content_asset"):
        issues.append("文章 / 影片 / Podcast / 新聞皆無有效素材，不應進正式 content index。")
    if not record.get("merchant_handle"):
        issues.append("merchant_handle 缺失，需補 metadata 或確認不適用。")
    if record.get("same_brand_multiple_records") or record.get("same_handle_multiple_records"):
        issues.append("同品牌或同 handle 有多筆紀錄；此為資訊性審核，不是 duplicate error，預設保留多次訪談。")
    if record.get("suspected_duplicate_review"):
        issues.append("疑似完全重複紀錄，需人工確認；不得自動刪除、合併或覆蓋。")
    return " ".join(issues) or f"需要人工審核：{_join_list(issue_types)}"


def _merchant_can_enter_content_index(record: dict) -> bool:
    if record.get("no_valid_content_asset"):
        return False
    if record.get("can_enter_content_index") is False:
        return False
    return True


def _merchant_can_quote_externally(record: dict, issue_types: List[str]) -> bool:
    if record.get("no_valid_content_asset"):
        return False
    if record.get("governance_risk_reasons"):
        return False
    return bool(record.get("can_quote_externally", True))


def _public_metric_issue_types(record: dict) -> List[str]:
    issue_types = []
    if record.get("missing_allowed_exposure_channels"):
        issue_types.append("missing_allowed_exposure_channels")
    if record.get("restricted_note"):
        issue_types.append("restricted_note")
    if record.get("can_quote_externally") is False:
        issue_types.append("can_quote_externally_false")
    return issue_types


def _public_metric_current_issue(record: dict, issue_types: List[str]) -> str:
    issues = []
    if "missing_allowed_exposure_channels" in issue_types:
        issues.append("缺少可用 exposure channel，不可直接對外引用。")
    if record.get("restricted_note"):
        issues.append(f"restricted_note：{record.get('restricted_note')}")
    channels = record.get("allowed_exposure_channels", [])
    if channels == ["verbal_briefing"]:
        issues.append("此數據只能用於 verbal_briefing，不可用於新聞稿 / 官網 / 廣告 / Saleskit。")
    elif record.get("restricted_note"):
        issues.append("使用前需依 restricted_note 檢查新聞稿 / 官網 / 廣告 / Saleskit 等外部用途限制。")
    return " ".join(issues) or "public metric 需人工確認 exposure channel 與對外引用限制。"


def _write_csv(path: Path, rows: List[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in REVIEW_COLUMNS})


def _render_counter(counter: Dict[str, int]) -> List[str]:
    if not counter:
        return ["- None"]
    return [f"- `{key}`: {value}" for key, value in counter.items()]


def _join_list(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return "|".join(str(item) for item in value if item is not None and str(item) != "")
    return str(value)


def _format_dict(value) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _split_pipe(value: str) -> List[str]:
    return [part for part in (value or "").split("|") if part]


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _append_unique(values: List[str], value: str) -> None:
    if value not in values:
        values.append(value)
