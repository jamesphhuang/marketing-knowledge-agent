from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from .governance import GovernanceIndex, metadata_allows_written_external_use
from .models import GeneratedAnswer, SearchFilters


RESTRICTED_QUERY_REFUSAL = "此查詢涉及受限制的客戶資訊,無法提供相關內容。若為內部業務需要,請聯繫管理者人工確認。"
EXTERNAL_CITATION_REMOVAL_WARNING = "已移除 {count} 筆不符對外資格的來源"
MIN_RELEVANCE_SCORE = 0.1  # 待 eval 校準。
DEFAULT_QUERY_AUDIT_LOG = Path("reports/audit_log.csv")
NO_MATCH_FILTER_VALUE = "__intent_no_match__"


def apply_intent_gating(filters: SearchFilters) -> SearchFilters:
    return SearchFilters(**_gated_filter_data(filters))


def precheck_restricted_query(
    query: str,
    governance_index: Optional[GovernanceIndex],
    command: str,
    audit_log_path: Path = DEFAULT_QUERY_AUDIT_LOG,
    audit_metadata: Optional[Mapping[str, str]] = None,
) -> Optional[GeneratedAnswer]:
    if governance_index is None:
        return None
    decision = governance_index.check_text(query)
    if not decision.blocked:
        return None

    append_denylist_query_audit(
        audit_log_path,
        command=command,
        match_count=decision.restricted_match_count,
        audit_metadata=audit_metadata,
        warning_count=len(decision.warnings),
    )
    return GeneratedAnswer(
        question=query,
        answer=RESTRICTED_QUERY_REFUSAL,
        citations=[],
        warnings=list(decision.warnings),
        governance_checked=True,
    )


def enforce_external_citations(answer: GeneratedAnswer, filters: SearchFilters) -> GeneratedAnswer:
    if filters.intent != "external":
        return answer

    kept = [
        citation
        for citation in answer.citations
        if citation.can_quote_externally
        and metadata_allows_written_external_use(citation)
    ]
    removed_count = len(answer.citations) - len(kept)
    if removed_count:
        answer.citations = kept
        warning = EXTERNAL_CITATION_REMOVAL_WARNING.format(count=removed_count)
        if warning not in answer.warnings:
            answer.warnings.append(warning)
    return answer


def format_effective_filters(filters: SearchFilters) -> str:
    effective_filters = SearchFilters(**_gated_filter_data(filters))
    values = effective_filters.as_dict()
    parts = [f"intent={effective_filters.intent}"]
    for key, value in values.items():
        if key == "intent" or value in (None, [], ""):
            continue
        if isinstance(value, list):
            rendered = "|".join("no-match" if item == NO_MATCH_FILTER_VALUE else str(item) for item in value)
        elif isinstance(value, bool):
            rendered = str(value).lower()
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


def append_denylist_query_audit(
    path: Path,
    command: str,
    match_count: int,
    audit_metadata: Optional[Mapping[str, str]] = None,
    warning_count: int = 0,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    header = []
    if exists:
        with path.open("r", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        slack_header = [
            "timestamp",
            "event",
            "channel_id",
            "user_id",
            "citation_count",
            "warning_count",
            "query",
        ]
        if audit_metadata is not None and not exists:
            header = slack_header
            writer.writerow(header)
        if header == slack_header:
            writer.writerow(
                [
                    _utc_now(),
                    "denylist_query_hit",
                    audit_metadata.get("channel_id", "") if audit_metadata else "",
                    audit_metadata.get("user_id", "") if audit_metadata else "",
                    0,
                    warning_count,
                    "",
                ]
            )
            return
        if not exists:
            header = ["timestamp", "command", "event", "match_count"]
            writer.writerow(header)
        if header == ["timestamp", "command", "event", "match_count"]:
            writer.writerow([_utc_now(), command, "denylist_query_hit", match_count])
            return
        if header == ["timestamp", "batch_id", "action", "add", "update", "archive", "operator", "plan_path"]:
            writer.writerow(
                [
                    _utc_now(),
                    audit_metadata.get("channel_id", "") if audit_metadata else "",
                    "denylist_query_hit",
                    match_count,
                    warning_count,
                    0,
                    audit_metadata.get("user_id", "") if audit_metadata else "",
                    "" if audit_metadata else command,
                ]
            )
            return
        if header == ["timestamp", "command", "index_count", "db_path"]:
            writer.writerow([_utc_now(), command, "denylist_query_hit", match_count])
            return
        if header == [
            "timestamp",
            "command",
            "provider",
            "model",
            "payload_chunk_count",
            "internal_removed_count",
        ]:
            writer.writerow(
                [
                    _utc_now(),
                    command,
                    "denylist_query_hit",
                    "",
                    match_count,
                    warning_count,
                ]
            )
            return
        raise ValueError(f"unsupported audit log header: {header}")


def _intersect_or_default(values, required_value: str):
    if not values:
        return [required_value]
    return [required_value] if required_value in values else [NO_MATCH_FILTER_VALUE]


def _gated_filter_data(filters: SearchFilters) -> dict:
    data = filters.as_dict()
    if filters.intent == "internal":
        return data

    data["can_quote_externally"] = True
    data["status"] = _intersect_or_default(filters.status, "published")
    data["data_classification"] = _intersect_or_default(filters.data_classification, "public")
    if filters.record_type:
        allowed_record_types = [value for value in filters.record_type if value != "pending_metric"]
        data["record_type"] = allowed_record_types or [NO_MATCH_FILTER_VALUE]
    return data


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
