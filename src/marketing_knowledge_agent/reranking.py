from __future__ import annotations

from datetime import date
from typing import Iterable, List

from .embeddings import tokenize
from .models import SearchFilters, SearchResult


def rerank_results(
    query: str,
    results: Iterable[SearchResult],
    filters: SearchFilters,
    today: date = None,
) -> List[SearchResult]:
    today = today or date.today()
    query_tokens = set(tokenize(query))
    reranked = []

    for result in results:
        bonus = _metadata_bonus(result, filters)
        bonus += _lexical_bonus(query_tokens, result)
        bonus += _freshness_bonus(result, today)
        result.rerank_score = result.score + bonus
        reranked.append(result)

    return sorted(reranked, key=lambda result: result.rerank_score, reverse=True)


def _metadata_bonus(result: SearchResult, filters: SearchFilters) -> float:
    metadata = result.chunk.metadata
    bonus = 0.0
    if filters.source_type and metadata.source_type in filters.source_type:
        bonus += 0.04
    if filters.content_category and metadata.content_category in filters.content_category:
        bonus += 0.04
    if filters.parent_source_type and metadata.parent_source_type in filters.parent_source_type:
        bonus += 0.04
    if filters.status and metadata.status in filters.status:
        bonus += 0.04
    for field_name in ["product", "industry", "topic", "funnel_stage"]:
        expected = getattr(filters, field_name)
        actual = getattr(metadata, field_name)
        if expected and set(expected).intersection(actual):
            bonus += 0.04
    return bonus


def _lexical_bonus(query_tokens: set, result: SearchResult) -> float:
    if not query_tokens:
        return 0.0
    haystack = f"{result.chunk.metadata.title} {result.chunk.text}".lower()
    matches = sum(1 for token in query_tokens if token in haystack)
    return min(0.08, matches * 0.01)


def _freshness_bonus(result: SearchResult, today: date) -> float:
    age_days = (today - result.chunk.metadata.effective_date).days
    if age_days <= 365:
        return 0.06
    if age_days <= 730:
        return 0.03
    return 0.0
