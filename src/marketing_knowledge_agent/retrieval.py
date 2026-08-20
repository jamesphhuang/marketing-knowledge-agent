from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from .embeddings import cosine_similarity, embed_text
from .governance import metadata_allows_written_external_use
from .indexing import SQLiteIndex
from .models import Chunk, DocumentMetadata, NON_RETRIEVABLE_RECORD_TYPES, SearchFilters, SearchResult
from .query_planning import TypedQueryPlan, metadata_matches_query_plan


@dataclass
class RetrievalWindow:
    """Which documents the ranking window refused, for callers that must not call a cut list a total.

    ``search`` scores every chunk that passed the filters and the query plan, then keeps the first
    ``limit`` of them. Everything after that slice is gone before any later stage can count it, and
    the stages that follow -- the alias merge, the structured layer -- both reduce their input to
    one entry per ``document_id``. A chunk left outside the slice whose document is inside it
    therefore costs the caller nothing; a document left entirely outside is a candidate the caller
    will never be offered.

    The refused documents are named rather than counted because a caller can re-supply some of them
    by another route -- the exact-alias branch fetches its owner's records regardless of this
    window -- and only a caller that can subtract those knows whether anything was really lost.
    A result that fits inside the window refuses nothing, so an exactly-full window reports an empty
    set rather than a truncation.
    """

    refused_document_ids: Set[str] = field(default_factory=set)


class SQLiteRetriever:
    def __init__(self, db_path: Path):
        self.index = SQLiteIndex(Path(db_path))

    def search(
        self,
        query: str,
        filters: Optional[SearchFilters] = None,
        limit: int = 5,
        mode: str = "hybrid",
        query_plan: Optional[TypedQueryPlan] = None,
        window: Optional[RetrievalWindow] = None,
    ) -> List[SearchResult]:
        filters = filters or SearchFilters()
        indexed_chunks = [
            indexed_chunk
            for indexed_chunk in self.index.load_chunks()
            if matches_filters(indexed_chunk.chunk.metadata, filters)
            and metadata_matches_query_plan(indexed_chunk.chunk.metadata, query_plan)
        ]

        keyword_scores = self.index.keyword_scores(query) if mode in {"keyword", "hybrid"} else {}
        query_embedding = embed_text(query) if mode in {"vector", "hybrid"} else []

        results: List[SearchResult] = []
        for indexed_chunk in indexed_chunks:
            chunk = indexed_chunk.chunk
            keyword_score = keyword_scores.get(chunk.id, 0.0)
            vector_score = (
                max(0.0, cosine_similarity(query_embedding, indexed_chunk.embedding))
                if query_embedding
                else 0.0
            )

            if mode == "keyword":
                score = keyword_score
            elif mode == "vector":
                score = vector_score
            else:
                score = (0.45 * keyword_score) + (0.55 * vector_score)

            if score > 0 or not filters.is_empty() or (query_plan and query_plan.hard_constraints):
                results.append(
                    SearchResult(
                        chunk=chunk,
                        score=score,
                        keyword_score=keyword_score,
                        vector_score=vector_score,
                    )
                )

        ranked = sorted(results, key=lambda result: result.score, reverse=True)
        admitted = ranked[:limit]
        if window is not None:
            admitted_documents = {result.chunk.document_id for result in admitted}
            window.refused_document_ids = {
                result.chunk.document_id
                for result in ranked[limit:]
                if result.chunk.document_id not in admitted_documents
            }
        return admitted


def matches_filters(metadata: DocumentMetadata, filters: SearchFilters) -> bool:
    if metadata.record_type in NON_RETRIEVABLE_RECORD_TYPES:
        return False
    if filters.intent == "external" and not metadata_allows_written_external_use(metadata):
        return False
    if filters.intent == "external" and metadata.record_type == "pending_metric":
        return False
    if filters.record_type and metadata.record_type not in filters.record_type:
        return False
    if filters.source_type and metadata.source_type not in filters.source_type:
        return False
    if filters.content_category and metadata.content_category not in filters.content_category:
        return False
    if filters.parent_source_type and metadata.parent_source_type not in filters.parent_source_type:
        return False
    if filters.brand_name and not _matches_scalar(metadata.brand_name, filters.brand_name):
        return False
    if filters.merchant_handle and not _matches_scalar(metadata.merchant_handle, filters.merchant_handle):
        return False
    if filters.merchant_status and not _matches_scalar(metadata.merchant_status, filters.merchant_status):
        return False
    if filters.status and metadata.status not in filters.status:
        return False
    if filters.can_quote_externally is not None and metadata.can_quote_externally != filters.can_quote_externally:
        return False
    if filters.product and not _intersects(metadata.product, filters.product):
        return False
    if filters.industry and not _intersects(metadata.industry, filters.industry):
        return False
    if filters.sales_category_lv1 and not _matches_scalar(metadata.sales_category_lv1, filters.sales_category_lv1):
        return False
    if filters.sales_category_lv2 and not _matches_scalar(metadata.sales_category_lv2, filters.sales_category_lv2):
        return False
    if filters.content_tags and not _intersects(metadata.content_tags, filters.content_tags):
        return False
    if filters.topic and not _intersects(metadata.topic, filters.topic):
        return False
    if filters.metric_type and not _matches_scalar(metadata.metric_type, filters.metric_type):
        return False
    if filters.metric_name and not _matches_scalar(metadata.metric_name, filters.metric_name):
        return False
    if filters.claim_status and not _matches_scalar(metadata.claim_status, filters.claim_status):
        return False
    if filters.data_classification and not _matches_scalar(metadata.data_classification, filters.data_classification):
        return False
    if filters.exposure_channel and not _intersects(metadata.allowed_exposure_channels, filters.exposure_channel):
        return False
    if filters.funnel_stage and not _intersects(metadata.funnel_stage, filters.funnel_stage):
        return False
    return True


def result_to_dict(result: SearchResult) -> dict:
    chunk = result.chunk
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "score": round(result.rerank_score or result.score, 4),
        "keyword_score": round(result.keyword_score, 4),
        "vector_score": round(result.vector_score, 4),
        "source_path": chunk.source_path,
        "title": chunk.metadata.title,
        "metadata": chunk.metadata.metadata_dict(),
        "text": chunk.text,
    }


def _intersects(left: List[str], right: List[str]) -> bool:
    return bool(set(left).intersection(right))


def _matches_scalar(value: object, allowed_values: List[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in allowed_values
