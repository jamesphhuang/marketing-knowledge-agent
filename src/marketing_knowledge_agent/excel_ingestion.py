from __future__ import annotations

import re
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from .governance import MERCHANT_CASE_RISK_TERMS, normalize_identity, split_restricted_aliases


SHEET_MERCHANT_CASES = "商家夥伴案例資料庫"
SHEET_RESTRICTED_CUSTOMERS = "「不可公開」客戶名單"
SHEET_PUBLIC_METRICS = "「可公開」對外數據"
SHEET_PENDING_METRICS = "待確認數據"
SHEET_HANDLE_MAPPING = "handle 比對"

NULL_LIKE_VALUES = {"", "-", "null", "none", "nan", "n/a"}
INVALID_ASSET_VALUES = NULL_LIKE_VALUES.union({"已下架", "暫時下架", "審核中"})
REVIEWABLE_INVALID_ASSET_VALUES = {"已下架", "暫時下架", "審核中"}
PUBLIC_METRIC_RESTRICTED_NOTE_TERMS = (
    "不可公開",
    "僅用於口頭說明",
    "不留文字紀錄",
)
EXCEL_INGESTION_PLAN = {
    SHEET_MERCHANT_CASES: {
        "header_row": 6,
        "record_type": "merchant_case",
        "index_role": "content_index",
        "notes": "可產出 normalized preview；若商家狀態或備註有風險詞，需加 governance warning。",
    },
    SHEET_RESTRICTED_CUSTOMERS: {
        "header_row": 4,
        "record_type": "restricted_customer",
        "index_role": "governance_table",
        "notes": "不可進向量檢索，只能做 denylist / policy check。",
    },
    SHEET_PUBLIC_METRICS: {
        "header_row": 6,
        "record_type": "public_metric",
        "index_role": "content_index",
        "notes": "可進 RAG，但必須依 allowed_exposure_channels 控制用途。",
    },
    SHEET_PENDING_METRICS: {
        "header_row": 3,
        "record_type": "pending_metric",
        "index_role": "internal_inventory",
        "notes": "不可對外引用，只能做內部盤點或缺口提醒。",
    },
    SHEET_HANDLE_MAPPING: {
        "header_row": 1,
        "record_type": "handle_mapping",
        "index_role": "normalization_table",
        "notes": "不可進一般 RAG，只用於 merchant identity enrichment。",
    },
}
PUBLIC_METRIC_CHANNEL_COLUMNS = {
    "新聞稿": "press_release",
    "自媒體": "owned_media",
    "Saleskits": "saleskits",
    "口頭說明": "verbal_briefing",
    "演講簡報": "speaking_deck",
    "官網/ 招募網站": "website_recruiting",
    "官網 / 招募網站": "website_recruiting",
    "廣告": "ads",
}


def normalize_merchant_case_row(
    row: Dict[str, object],
    source_row: int,
    captured_date: date,
    handle_mapping: Optional[Dict[str, Dict[str, object]]] = None,
    source_sheet: str = SHEET_MERCHANT_CASES,
) -> Dict[str, object]:
    article_title = normalize_asset_title(row.get("文章"))
    video_title = normalize_asset_title(row.get("影片"))
    podcast_title = normalize_asset_title(row.get("Podcast"))
    news_title = normalize_asset_title(row.get("新聞"))
    no_valid_content_asset = not any([article_title, video_title, podcast_title, news_title])
    risk_context = analyze_merchant_case_governance(
        row=row,
        no_valid_content_asset=no_valid_content_asset,
    )
    brand_name = normalize_optional_text(row.get("商家 / 夥伴名稱"))
    merchant_handle = normalize_optional_text(row.get("Handle"))
    sales_category_lv1 = normalize_optional_text(row.get("Sales Category LV1"))
    sales_category_lv2 = normalize_optional_text(row.get("Sales Category LV2"))

    enrichment = lookup_handle_mapping(merchant_handle, handle_mapping or {})
    brand_name = brand_name or normalize_optional_text(enrichment.get("brand_name"))
    sales_category_lv1 = sales_category_lv1 or normalize_optional_text(enrichment.get("sales_category_lv1"))
    sales_category_lv2 = sales_category_lv2 or normalize_optional_text(enrichment.get("sales_category_lv2"))

    notes = normalize_optional_text(row.get("備註"))
    merchant_status = normalize_optional_text(row.get("狀態"))
    has_governance_risk = bool(risk_context["governance_risk_reasons"])
    title = first_non_empty([article_title, video_title, podcast_title, news_title, brand_name]) or "Untitled merchant case"

    return {
        "title": title,
        "source_type": "database",
        "record_type": "merchant_case",
        "status": "published",
        "publish_date": captured_date,
        "captured_date": captured_date,
        "source_path": f"{source_sheet}:{source_row}",
        "source_sheet": source_sheet,
        "source_row": source_row,
        "interview_year": normalize_int(row.get("採訪年份")),
        "merchant_status": merchant_status,
        "brand_name": brand_name,
        "merchant_handle": merchant_handle,
        "sales_category_lv1": sales_category_lv1,
        "sales_category_lv2": sales_category_lv2,
        "industry": [sales_category_lv1] if sales_category_lv1 else [],
        "industry_subcategory": sales_category_lv2,
        "content_tags": split_tags(row.get("內容相關標籤")),
        "article_title": article_title,
        "video_title": video_title,
        "podcast_title": podcast_title,
        "news_title": news_title,
        "notes": notes,
        "data_classification": "public",
        "can_quote_externally": not (has_governance_risk or no_valid_content_asset),
        "no_valid_content_asset": no_valid_content_asset,
        "can_enter_content_index": not no_valid_content_asset,
        "governance_issue_types": risk_context["governance_issue_types"],
        "governance_risk_reasons": risk_context["governance_risk_reasons"],
        "governance_risk_fields": risk_context["governance_risk_fields"],
        "invalid_asset_fields": risk_context["invalid_asset_fields"],
        "invalid_asset_values": risk_context["invalid_asset_values"],
    }


def normalize_restricted_customer_row(
    row: Dict[str, object],
    source_row: int,
    captured_date: date,
    source_sheet: str = SHEET_RESTRICTED_CUSTOMERS,
) -> Dict[str, object]:
    brand_name = normalize_optional_text(row.get("客戶品牌"))
    restricted_aliases = split_restricted_aliases(brand_name)
    restricted_reason = (
        normalize_optional_text(row.get("店家狀況"))
        or normalize_optional_text(row.get("店家狀況（例如：店家對中資事件敏感...）"))
        or "不可公開客戶名單"
    )
    return {
        "title": brand_name or "Restricted customer",
        "source_type": "database",
        "record_type": "restricted_customer",
        "status": "published",
        "publish_date": captured_date,
        "captured_date": captured_date,
        "source_path": f"{source_sheet}:{source_row}",
        "source_sheet": source_sheet,
        "source_row": source_row,
        "brand_name": brand_name,
        "restricted_aliases": restricted_aliases,
        "website_url": normalize_optional_text(row.get("網站")),
        "sales_category_lv1": normalize_optional_text(row.get("Sales Category LV1")),
        "nda_signed": normalize_bool(row.get("是否有簽保密 NDA")),
        "nda_uploaded_salesforce": normalize_bool(row.get("NDA是否已上傳Salesforce")),
        "restricted_reason": restricted_reason,
        "submitted_by": normalize_optional_text(row.get("填表人")) or normalize_optional_text(row.get("填表人\n（部門/名字）")),
        "restricted_updated_year": normalize_int(row.get("更新年份")),
        "data_classification": "restricted",
        "can_quote_externally": False,
    }


def normalize_public_metric_row(
    row: Dict[str, object],
    source_row: int,
    captured_date: date,
    source_sheet: str = SHEET_PUBLIC_METRICS,
) -> Dict[str, object]:
    metric_name = normalize_optional_text(row.get("指標"))
    claim_statement = normalize_optional_text(row.get("論述"))
    metric_note = normalize_optional_text(row.get("備註"))
    metric_updated_date = normalize_date(row.get("更新時間"))
    allowed_exposure_channels = normalize_allowed_exposure_channels(row)
    restricted_note = normalize_public_metric_restricted_note(metric_note)
    if restricted_note and "僅用於口頭說明" in restricted_note:
        allowed_exposure_channels = ["verbal_briefing"]
    missing_allowed_exposure_channels = not allowed_exposure_channels
    return {
        "title": metric_name or truncate_title(claim_statement) or "Public metric",
        "source_type": "database",
        "record_type": "public_metric",
        "status": "published",
        "publish_date": captured_date,
        "captured_date": captured_date,
        "last_reviewed": metric_updated_date,
        "source_path": f"{source_sheet}:{source_row}",
        "source_sheet": source_sheet,
        "source_row": source_row,
        "metric_type": normalize_optional_text(row.get("類型")),
        "metric_name": metric_name,
        "claim_statement": claim_statement,
        "metric_note": metric_note,
        "metric_updated_date": metric_updated_date,
        "reference_source": normalize_optional_text(row.get("參考新聞連結")),
        "allowed_exposure_channels": allowed_exposure_channels,
        "claim_status": "approved",
        "data_classification": "public",
        "can_quote_externally": not missing_allowed_exposure_channels,
        "missing_allowed_exposure_channels": missing_allowed_exposure_channels,
        "restricted_note": restricted_note,
    }


def normalize_pending_metric_row(
    row: Dict[str, object],
    source_row: int,
    captured_date: date,
    source_sheet: str = SHEET_PENDING_METRICS,
) -> Dict[str, object]:
    metric_name = normalize_optional_text(row.get("指標"))
    claim_statement = normalize_optional_text(row.get("論述"))
    return {
        "title": metric_name or truncate_title(claim_statement) or "Pending metric",
        "source_type": "database",
        "record_type": "pending_metric",
        "status": "draft",
        "publish_date": captured_date,
        "captured_date": captured_date,
        "source_path": f"{source_sheet}:{source_row}",
        "source_sheet": source_sheet,
        "source_row": source_row,
        "metric_type": normalize_optional_text(row.get("類型")),
        "metric_name": metric_name,
        "claim_statement": claim_statement,
        "metric_note": normalize_optional_text(row.get("備註")),
        "claim_status": "pending_review",
        "data_classification": "internal",
        "can_quote_externally": False,
    }


def normalize_handle_mapping_row(
    row: Dict[str, object],
    source_row: int,
    captured_date: date,
    source_sheet: str = SHEET_HANDLE_MAPPING,
) -> Dict[str, object]:
    brand_name = normalize_optional_text(row.get("Name with Link")) or normalize_optional_text(row.get("Name (with Link)"))
    return {
        "title": brand_name or normalize_optional_text(row.get("Handle")) or "Handle mapping",
        "source_type": "database",
        "record_type": "handle_mapping",
        "status": "published",
        "publish_date": captured_date,
        "captured_date": captured_date,
        "source_path": f"{source_sheet}:{source_row}",
        "source_sheet": source_sheet,
        "source_row": source_row,
        "merchant_handle": normalize_optional_text(row.get("Handle")),
        "brand_name": brand_name,
        "sales_category_lv1": normalize_optional_text(row.get("Lv1 Sales Category")),
        "sales_category_lv2": normalize_optional_text(row.get("Lv2 Sales Category 1st")),
        "data_classification": "internal",
        "can_quote_externally": False,
    }


def build_handle_mapping(records: Iterable[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    mapping: Dict[str, Dict[str, object]] = {}
    for record in records:
        handle = normalize_optional_text(record.get("merchant_handle"))
        brand_name = normalize_optional_text(record.get("brand_name"))
        for key in [handle, brand_name]:
            normalized_key = normalize_identity(key)
            if normalized_key:
                mapping[normalized_key] = record
    return mapping


def lookup_handle_mapping(value: Optional[str], mapping: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    return mapping.get(normalize_identity(value), {})


def normalize_allowed_exposure_channels(row: Dict[str, object]) -> List[str]:
    channels: List[str] = []
    for column, channel in PUBLIC_METRIC_CHANNEL_COLUMNS.items():
        if normalize_bool(row.get(column)) and channel not in channels:
            channels.append(channel)
    return channels


def normalize_asset_title(value: object) -> Optional[str]:
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None
    if normalized.strip().lower() in INVALID_ASSET_VALUES:
        return None
    return normalized


def analyze_merchant_case_governance(row: Dict[str, object], no_valid_content_asset: bool) -> Dict[str, object]:
    reasons: List[str] = []
    fields: List[str] = []
    issue_types: List[str] = []
    invalid_asset_fields: List[str] = []
    invalid_asset_values: Dict[str, str] = {}

    asset_fields = {
        "文章": row.get("文章"),
        "影片": row.get("影片"),
        "Podcast": row.get("Podcast"),
        "新聞": row.get("新聞"),
    }
    for field, raw_value in asset_fields.items():
        normalized = normalize_optional_text(raw_value)
        if normalized and normalized.strip().lower() in REVIEWABLE_INVALID_ASSET_VALUES:
            _append_unique(invalid_asset_fields, field)
            invalid_asset_values[field] = normalized
            _append_unique(fields, field)
            _append_unique(issue_types, "invalid_asset_value")
            _append_unique(reasons, f"{field}={normalized}")

    if no_valid_content_asset:
        _append_unique(issue_types, "no_valid_content_asset")
        _append_unique(reasons, "no_valid_content_asset")
        for field in asset_fields:
            _append_unique(fields, field)

    risk_sources = {
        "狀態": normalize_optional_text(row.get("狀態")),
        "備註": normalize_optional_text(row.get("備註")),
    }
    for field, text in risk_sources.items():
        if not text:
            continue
        for term in MERCHANT_CASE_RISK_TERMS:
            if term in text:
                _append_unique(fields, field)
                _append_unique(issue_types, _merchant_issue_type(field, term))
                _append_unique(reasons, f"{field} contains {term}")

    if reasons:
        _append_unique(issue_types, "can_quote_externally_false")
        _append_unique(reasons, "can_quote_externally=false")

    return {
        "governance_issue_types": issue_types,
        "governance_risk_reasons": reasons,
        "governance_risk_fields": fields,
        "invalid_asset_fields": invalid_asset_fields,
        "invalid_asset_values": invalid_asset_values,
    }


def _merchant_issue_type(field: str, term: str) -> str:
    if field == "備註" and term in {"Shopify", "91APP", "EasyStore"}:
        return "competitor_migration"
    if field == "備註":
        return "notes_governance_risk"
    return "merchant_status_governance_risk"


def _append_unique(values: List[str], value: str) -> None:
    if value not in values:
        values.append(value)


def normalize_optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    if text.lower() in NULL_LIKE_VALUES:
        return None
    return text


def normalize_bool(value: object) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "是", "有"}:
        return True
    if text in {"false", "no", "n", "0", "否", "無"}:
        return False
    return None


def normalize_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = normalize_optional_text(value)
    if not text:
        return None
    match = re.search(r"\d{4}|\d+", text)
    return int(match.group(0)) if match else None


def normalize_date(value: object) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_optional_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y.%m", "%Y-%m", "%Y/%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def split_tags(value: object) -> List[str]:
    text = normalize_optional_text(value)
    if not text:
        return []
    return [
        item.strip().lower()
        for item in re.split(r"[,，、]", text)
        if item.strip()
    ]


def contains_governance_risk(values: Iterable[Optional[str]]) -> bool:
    text = " ".join(value for value in values if value)
    return any(term in text for term in MERCHANT_CASE_RISK_TERMS)


def normalize_public_metric_restricted_note(value: object) -> Optional[str]:
    text = normalize_optional_text(value)
    if not text:
        return None
    if any(term in text for term in PUBLIC_METRIC_RESTRICTED_NOTE_TERMS):
        return text
    return None


def first_non_empty(values: Iterable[Optional[str]]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def truncate_title(value: Optional[str], max_length: int = 48) -> Optional[str]:
    if not value:
        return None
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1].rstrip()}..."
