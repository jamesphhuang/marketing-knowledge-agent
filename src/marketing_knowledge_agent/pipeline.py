from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from .agentic import AgenticAnswer, AgentReflection, AgentTrace, QueryAnalysis, agentic_ask
from .chunking import chunk_documents
from .generation import generate_answer
from .governance import (
    RESTRICTED_RESULT_REMOVAL_WARNING,
    GovernanceIndex,
    RestrictedCustomerRecord,
    apply_governance_to_answer,
    filter_restricted_results,
)
from .indexing import SQLiteIndex
from .ingestion import load_documents
from .models import GeneratedAnswer, SearchFilters, SearchResult
from .reranking import rerank_results
from .retrieval import SQLiteRetriever
from .query_gating import (
    DEFAULT_QUERY_AUDIT_LOG,
    apply_intent_gating,
    enforce_external_citations,
    precheck_restricted_query,
)


DEFAULT_RESTRICTED_CUSTOMERS_PATH = Path("reports/excel_preview/restricted_customers.json")
DENYLIST_MISSING_WARNING = "restricted denylist 未載入（{path} 不存在），本答案未經 denylist 檢查"


def ingest_vault(
    vault_path: Path,
    db_path: Path,
    chunk_size: int = 900,
    overlap: int = 120,
) -> dict:
    documents = load_documents(Path(vault_path))
    chunks = chunk_documents(documents, chunk_size=chunk_size, overlap=overlap)
    index = SQLiteIndex(Path(db_path))
    index.rebuild(documents, chunks)
    return {
        "vault_path": str(vault_path),
        "db_path": str(db_path),
        "documents": len(documents),
        "chunks": len(chunks),
    }


def search_index(
    query: str,
    db_path: Path,
    filters: Optional[SearchFilters] = None,
    limit: int = 5,
    mode: str = "hybrid",
) -> List[SearchResult]:
    filters = apply_intent_gating(filters or SearchFilters())
    retriever = SQLiteRetriever(Path(db_path))
    initial_results = retriever.search(query=query, filters=filters, limit=max(limit * 3, limit), mode=mode)
    return rerank_results(query, initial_results, filters)[:limit]


def ask_index(
    question: str,
    db_path: Path,
    filters: Optional[SearchFilters] = None,
    limit: int = 5,
    mode: str = "hybrid",
    governance_index: Optional[GovernanceIndex] = None,
    restricted_customers_path: Optional[Path] = None,
    audit_log_path: Path = DEFAULT_QUERY_AUDIT_LOG,
) -> GeneratedAnswer:
    filters = filters or SearchFilters()
    governance_index, load_warning = resolve_governance_index(governance_index, restricted_customers_path)
    refused = precheck_restricted_query(
        question,
        governance_index,
        command="ask",
        audit_log_path=audit_log_path,
    )
    if refused is not None:
        return refused

    results = search_index(question, db_path=db_path, filters=filters, limit=limit, mode=mode)
    results, removed_count = filter_restricted_results(results, governance_index)
    internal_result_count = _internal_result_count(question, db_path, filters, limit, mode) if not results else 0
    answer = generate_answer(
        question,
        results,
        filters=filters,
        internal_result_count=internal_result_count,
    )
    answer = apply_governance_to_answer(answer, governance_index)
    _append_removal_warning(answer, removed_count)
    _append_warning(answer, load_warning)
    enforce_external_citations(answer, filters)
    return answer


def agent_ask(
    question: str,
    db_path: Path,
    filters: Optional[SearchFilters] = None,
    limit: int = 5,
    mode: str = "hybrid",
    governance_index: Optional[GovernanceIndex] = None,
    restricted_customers_path: Optional[Path] = None,
    audit_log_path: Path = DEFAULT_QUERY_AUDIT_LOG,
) -> AgenticAnswer:
    filters = filters or SearchFilters()
    governance_index, load_warning = resolve_governance_index(governance_index, restricted_customers_path)
    refused = precheck_restricted_query(
        question,
        governance_index,
        command="agent-ask",
        audit_log_path=audit_log_path,
    )
    if refused is not None:
        return _refused_agentic_answer(refused)

    answer = agentic_ask(
        question=question,
        db_path=db_path,
        search_fn=search_index,
        ask_fn=ask_index,
        filters=filters,
        limit=limit,
        mode=mode,
        governance_index=governance_index,
    )
    _append_warning(answer.generated, load_warning)
    enforce_external_citations(answer.generated, filters)
    return answer


def resolve_governance_index(
    governance_index: Optional[GovernanceIndex],
    restricted_customers_path: Optional[Path],
) -> Tuple[Optional[GovernanceIndex], Optional[str]]:
    if governance_index is not None:
        return governance_index, None
    if restricted_customers_path is None:
        return None, None
    return load_restricted_customers_governance_index(restricted_customers_path)


def load_restricted_customers_governance_index(path: Path) -> Tuple[Optional[GovernanceIndex], Optional[str]]:
    path = Path(path)
    if not path.exists():
        return None, DENYLIST_MISSING_WARNING.format(path=path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [
        RestrictedCustomerRecord(
            brand_name=record.get("brand_name") or "",
            website_url=record.get("website_url"),
            merchant_handle=record.get("merchant_handle"),
            restricted_aliases=record.get("restricted_aliases"),
            source_sheet=record.get("source_sheet"),
            source_row=record.get("source_row"),
        )
        for record in payload
        if isinstance(record, dict)
    ]
    return GovernanceIndex(records), None


def _append_warning(answer: GeneratedAnswer, warning: Optional[str]) -> None:
    if warning and warning not in answer.warnings:
        answer.warnings.append(warning)


def _append_removal_warning(answer: GeneratedAnswer, removed_count: int) -> None:
    if removed_count:
        _append_warning(answer, RESTRICTED_RESULT_REMOVAL_WARNING.format(count=removed_count))


def _internal_result_count(
    question: str,
    db_path: Path,
    filters: SearchFilters,
    limit: int,
    mode: str,
) -> int:
    if filters.intent != "external":
        return 0
    internal_data = filters.as_dict()
    internal_data["intent"] = "internal"
    internal_filters = SearchFilters(**internal_data)
    chunk_limit = max(limit, SQLiteIndex(Path(db_path)).counts()["chunks"])
    results = search_index(
        question,
        db_path=db_path,
        filters=internal_filters,
        limit=chunk_limit,
        mode=mode,
    )
    return len({result.chunk.document_id for result in results})


def _refused_agentic_answer(generated: GeneratedAnswer) -> AgenticAnswer:
    return AgenticAnswer(
        generated=generated,
        trace=AgentTrace(
            mode="refused",
            analysis=QueryAnalysis(
                question_type="restricted_query",
                needs_agent=False,
                reasons=["查詢在檢索前命中 restricted denylist。"],
            ),
            plan=[],
            observations=[],
            reflection=AgentReflection(sufficient=False, notes=["未執行任何檢索。"]),
        ),
    )
