from __future__ import annotations

from datetime import date
from typing import Iterable, List, Optional

from .governance import metadata_governance_warnings
from .models import Citation, GeneratedAnswer, NON_PUBLIC_STATUSES, SearchFilters, SearchResult
from .query_gating import MIN_RELEVANCE_SCORE, format_effective_filters


def generate_answer(
    question: str,
    results: Iterable[SearchResult],
    citation_limit: int = 3,
    today: date = None,
    filters: Optional[SearchFilters] = None,
    internal_result_count: int = 0,
) -> GeneratedAnswer:
    today = today or date.today()
    filters = filters or SearchFilters()
    selected = list(results)[:citation_limit]
    if not selected:
        answer_lines = [
            "找不到符合條件的 mock vault 內容。請放寬 metadata filter 或先重新建立 index。",
            f"本次生效 filters：{format_effective_filters(filters)}。",
        ]
        if filters.intent == "external" and internal_result_count:
            answer_lines.append(
                f"有 {internal_result_count} 筆內部資料但無可對外引用版本,對外使用需人工核准。"
            )
        return GeneratedAnswer(
            question=question,
            answer="\n".join(answer_lines),
            citations=[],
            warnings=[],
        )

    if _effective_score(selected[0]) < MIN_RELEVANCE_SCORE:
        closest_titles = []
        for result in selected[:3]:
            title = result.chunk.metadata.title
            if title not in closest_titles:
                closest_titles.append(title)
        return GeneratedAnswer(
            question=question,
            answer="相關度不足，未產生事實性回答。最接近的內容：\n"
            + "\n".join(f"- {title}" for title in closest_titles),
            citations=[],
            warnings=[],
        )

    answer_lines = [
        "以下是根據 Marketing Knowledge Vault 檢索到的初步回答：",
    ]
    citations: List[Citation] = []
    warnings: List[str] = []

    for index, result in enumerate(selected, start=1):
        label = f"[{index}]"
        chunk = result.chunk
        metadata = chunk.metadata
        snippet = _snippet(chunk.text)
        answer_lines.append(f"{label} {snippet}")

        freshness_note = freshness_note_for(metadata.effective_date, today)
        citations.append(
            Citation(
                label=label,
                title=metadata.title,
                source_path=metadata.source_path,
                chunk_id=chunk.id,
                status=metadata.status,
                source_type=metadata.source_type,
                record_type=metadata.record_type,
                data_classification=metadata.data_classification,
                can_quote_externally=metadata.can_quote_externally,
                publish_date=metadata.publish_date.isoformat(),
                updated_date=metadata.updated_date.isoformat() if metadata.updated_date else None,
                captured_date=metadata.captured_date.isoformat() if metadata.captured_date else None,
                last_reviewed=metadata.last_reviewed.isoformat() if metadata.last_reviewed else None,
                source_sheet=metadata.source_sheet,
                source_row=metadata.source_row,
                canonical_url=metadata.canonical_url,
                freshness_note=freshness_note,
            )
        )

        if metadata.status in NON_PUBLIC_STATUSES:
            warnings.append(
                f"{label} {metadata.title} 的 status={metadata.status}，不可直接對外引用。"
            )
        warnings.extend(metadata_governance_warnings(metadata, label))

    if warnings:
        answer_lines.append("")
        answer_lines.append("引用限制提醒：")
        answer_lines.extend(f"- {warning}" for warning in warnings)

    answer_lines.append("")
    answer_lines.append("請在正式對外使用前，由內容 owner 複核引用與資料新鮮度。")

    return GeneratedAnswer(
        question=question,
        answer="\n".join(answer_lines),
        citations=citations,
        warnings=warnings,
    )


def freshness_note_for(effective_date: date, today: date = None) -> str:
    today = today or date.today()
    age_days = (today - effective_date).days
    if age_days < 0:
        return f"最新日期 {effective_date.isoformat()}；日期在未來，請確認 metadata。"
    if age_days > 540:
        return f"最新日期 {effective_date.isoformat()}；距今約 {age_days} 天，建議重新確認資料新鮮度。"
    return f"最新日期 {effective_date.isoformat()}；距今約 {age_days} 天。"


def _snippet(text: str, max_length: int = 260) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 1].rstrip()}..."


def _effective_score(result: SearchResult) -> float:
    return result.rerank_score or result.score
