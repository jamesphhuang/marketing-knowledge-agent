from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from .models import DocumentMetadata, GeneratedAnswer, SearchResult


MERCHANT_CASE_RISK_TERMS = (
    "停止營運",
    "暫時下架",
    "已關店",
    "已關閉",
    "轉走",
    "結束合作",
    "結束合作關係",
    "結束夥伴關係",
    "已下架",
    "下架內容",
    "審核中",
    "請我們下架內容",
    "智慧廣告系統停用",
    "Shopify",
    "91APP",
    "EasyStore",
)
RESTRICTED_CUSTOMER_WARNING = "不可對外引用 / 不可公開提及：命中 restricted customer denylist，請人工確認。"
RESTRICTED_RESULT_REMOVAL_WARNING = "已依 restricted denylist 移除 {count} 筆來源"


@dataclass(frozen=True)
class RestrictedCustomerRecord:
    brand_name: str
    website_url: Optional[str] = None
    merchant_handle: Optional[str] = None
    restricted_aliases: Optional[List[str]] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None

    @property
    def normalized_terms(self) -> List[str]:
        alias_terms = self.restricted_aliases or split_restricted_aliases(self.brand_name)
        return [
            normalized
            for normalized in [
                normalize_identity(self.brand_name),
                normalize_identity(self.website_url),
                normalize_website_identity(self.website_url),
                normalize_identity(self.merchant_handle),
                *(normalize_identity(alias) for alias in alias_terms),
            ]
            if normalized
        ]


@dataclass(frozen=True)
class GovernanceDecision:
    blocked: bool
    warnings: List[str] = field(default_factory=list)
    restricted_match_count: int = 0


class GovernanceIndex:
    def __init__(self, restricted_customers: Iterable[RestrictedCustomerRecord] = ()):
        self.restricted_customers = [
            record
            for record in restricted_customers
            if record.brand_name and normalize_identity(record.brand_name)
        ]

    def check_text(self, text: str) -> GovernanceDecision:
        normalized_text = normalize_identity(text)
        matches = [
            record
            for record in self.restricted_customers
            if any(_restricted_term_matches(term, text, normalized_text) for term in record.normalized_terms)
        ]
        if not matches:
            return GovernanceDecision(blocked=False)
        return GovernanceDecision(
            blocked=True,
            warnings=[RESTRICTED_CUSTOMER_WARNING],
            restricted_match_count=len(matches),
        )

    def redact_text(self, text: str) -> str:
        redacted = text
        for record in self.restricted_customers:
            for term in [record.brand_name, *(record.restricted_aliases or split_restricted_aliases(record.brand_name))]:
                if not term:
                    continue
                redacted = re.sub(re.escape(term.strip()), "[restricted customer]", redacted, flags=re.IGNORECASE)
        return redacted


def normalize_identity(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value).strip().lower())


def normalize_website_identity(value: object) -> str:
    normalized = normalize_identity(value)
    normalized = re.sub(r"^https?://", "", normalized)
    normalized = re.sub(r"^www\.", "", normalized)
    return normalized.rstrip("/")


def split_restricted_aliases(brand_name: Optional[str]) -> List[str]:
    if brand_name is None:
        return []
    text = str(brand_name).strip()
    if not text:
        return []
    candidates = []
    candidates.extend(re.split(r"[／/|、,，]", text))
    candidates.extend(re.findall(r"[（(]([^）)]+)[）)]", text))
    aliases: List[str] = []
    for candidate in candidates:
        alias = candidate.strip()
        if alias and normalize_identity(alias) != normalize_identity(text) and alias not in aliases:
            aliases.append(alias)
    return aliases


def _restricted_term_matches(term: str, text: str, normalized_text: str) -> bool:
    if _requires_word_boundary_match(term):
        raw_text = "" if text is None else str(text).lower()
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", raw_text) is not None
    return term in normalized_text


def _requires_word_boundary_match(term: str) -> bool:
    return len(term) < 4 or (term.isascii() and len(term) <= 3)


def metadata_governance_warnings(metadata: DocumentMetadata, label: str) -> List[str]:
    warnings: List[str] = []
    if not metadata.can_quote_externally:
        warnings.append(f"{label} {metadata.title} 的 can_quote_externally=false，不可直接對外引用。")

    if metadata.record_type == "pending_metric":
        warnings.append(f"{label} {metadata.title} 是待確認數據，不可直接對外引用。")

    if metadata.record_type == "merchant_case" and _contains_risk_term(metadata):
        warnings.append(f"{label} {metadata.title} 的商家狀態或備註顯示風險，請人工確認後再引用。")

    return _dedupe(warnings)


def filter_restricted_results(
    results: Iterable[SearchResult],
    governance_index: Optional[GovernanceIndex],
) -> Tuple[List[SearchResult], int]:
    results = list(results)
    if governance_index is None:
        return results, 0

    kept_results: List[SearchResult] = []
    removed_count = 0
    for result in results:
        if governance_index.check_text(_restricted_result_check_text(result)).blocked:
            removed_count += 1
            continue
        kept_results.append(result)
    return kept_results, removed_count


def apply_governance_to_answer(answer: GeneratedAnswer, governance_index: Optional[GovernanceIndex]) -> GeneratedAnswer:
    if governance_index is None:
        return answer

    answer.governance_checked = True
    decision = governance_index.check_text(f"{answer.question}\n{answer.answer}")
    if decision.blocked:
        answer.answer = governance_index.redact_text(answer.answer)
        for warning in decision.warnings:
            if warning not in answer.warnings:
                answer.warnings.append(warning)
        answer.answer = _append_answer_warnings(answer.answer, decision.warnings)

    kept_citations = []
    removed_citations = 0
    for citation in answer.citations:
        citation_decision = governance_index.check_text(f"{citation.title}\n{citation.source_path}")
        if citation_decision.blocked:
            removed_citations += 1
            continue
        kept_citations.append(citation)
    if removed_citations:
        answer.citations = kept_citations
        removal_warning = f"已依 restricted denylist 移除 {removed_citations} 筆引用來源"
        if removal_warning not in answer.warnings:
            answer.warnings.append(removal_warning)
    return answer


def _restricted_result_check_text(result: SearchResult) -> str:
    chunk = result.chunk
    metadata = chunk.metadata
    return "\n".join(
        value
        for value in [
            metadata.title,
            chunk.source_path,
            metadata.brand_name,
            metadata.merchant_handle,
        ]
        if value
    )


def _contains_risk_term(metadata: DocumentMetadata) -> bool:
    text = " ".join(
        value
        for value in [metadata.merchant_status, metadata.notes, metadata.article_title, metadata.video_title]
        if value
    )
    return any(term in text for term in MERCHANT_CASE_RISK_TERMS)


def _append_answer_warnings(answer_text: str, warnings: List[str]) -> str:
    if not warnings:
        return answer_text
    lines = [answer_text, "", "資料治理提醒："]
    lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def _dedupe(values: List[str]) -> List[str]:
    deduped: List[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
