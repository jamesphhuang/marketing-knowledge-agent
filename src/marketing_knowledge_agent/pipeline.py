from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from .agentic import AgenticAnswer, AgentReflection, AgentTrace, QueryAnalysis, agentic_ask
from .chunking import chunk_documents
from .governance import (
    RESTRICTED_RESULT_REMOVAL_WARNING,
    GovernanceIndex,
    RestrictedCustomerRecord,
    apply_governance_to_answer,
    filter_restricted_results,
)
from .indexing import SQLiteIndex
from .ingestion import load_documents
from .llm import (
    DEFAULT_LLM_CONFIG_PATH,
    LLMConfig,
    LLMError,
    LLMProvider,
    load_llm_config,
    validate_provider_policy,
)
from .llm_generation import generate_answer_with_llm
from .models import GeneratedAnswer, SearchFilters, SearchResult
from .query_planning import (
    QueryCatalog,
    TypedQueryPlan,
    allow_semantic_fallback,
    build_query_plan,
    metadata_matches_query_plan,
)
from .reranking import rerank_results
from .retrieval import RetrievalWindow, SQLiteRetriever
from .retrieval import matches_filters
from .search_aliases import (
    DEFAULT_ALIAS_PROJECTION_PATH,
    EXPECTED_ALIAS_AUTHORITY,
    EXPECTED_ALIAS_BINDING,
    alias_merge_candidate_count,
    alias_results_for_parent_ids,
    load_alias_projection,
    merge_rank_and_cap_alias_results,
    resolve_exact_alias_parent_ids,
)
from .structured_results import (
    DEFAULT_ASSET_CAP,
    DEFAULT_PARENT_CAP,
    generate_structured_answer,
)
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


@dataclass
class RetrievalTruncation:
    """Whether a retrieval stage dropped candidates it had already found.

    This is a diagnostic channel, not a result: retrieval behaviour is identical whether or not a
    caller passes one. It exists because a presentation layer cannot otherwise tell a truncated
    result from a complete one -- both arrive as a short list -- and describing a truncated result
    as a complete total is the specific claim the Slack wording contract has to avoid.
    """

    exact_alias_capped: bool = False
    retrieval_window_capped: bool = False

    @property
    def any_stage_capped(self) -> bool:
        """True when any stage refused a candidate, whichever one it was.

        The two stages are separate fields rather than one flag because they cut for different
        reasons and either can bind on its own: a query can be cut by the retrieval window, by the
        alias merge, or by both, and a single flag written twice would let the second writer erase
        the first. A consumer that only needs "is this result the whole universe" reads this.
        """
        return self.exact_alias_capped or self.retrieval_window_capped


def search_index(
    query: str,
    db_path: Path,
    filters: Optional[SearchFilters] = None,
    limit: int = 5,
    mode: str = "hybrid",
    query_plan: Optional[TypedQueryPlan] = None,
    alias_projection_path: Optional[Path] = DEFAULT_ALIAS_PROJECTION_PATH,
    truncation: Optional[RetrievalTruncation] = None,
) -> List[SearchResult]:
    requested_filters = filters or SearchFilters()
    filters = apply_intent_gating(requested_filters)
    query_plan = query_plan or build_index_query_plan(query, db_path, requested_filters)
    if query_plan.execution_blocked:
        return []
    retriever = SQLiteRetriever(Path(db_path))
    # The retrieval window is decided here, three times the caller's limit, and the retriever is
    # the only place both the window and the candidates it refused exist at once. Asking for the
    # refusals costs the returned list nothing -- same query, same ranking, same slice.
    window = RetrievalWindow()
    initial_results = retriever.search(
        query=query,
        filters=filters,
        limit=max(limit * 3, limit),
        mode=mode,
        query_plan=query_plan,
        window=window,
    )
    ranked = rerank_results(query, initial_results, filters)
    if query_plan.query_mode == "structured_lookup" or query_plan.hard_constraints:
        ranked = _dedupe_document_results(ranked)
    alias_owner_ids = _exact_alias_owner_ids(
        query, query_plan, alias_projection_path
    )
    if not alias_owner_ids:
        if truncation is not None:
            truncation.retrieval_window_capped = bool(window.refused_document_ids)
        return ranked[:limit]
    alias_results = alias_results_for_parent_ids(
        db_path, alias_owner_ids, filters, query_plan
    )
    candidate_count = alias_merge_candidate_count(alias_results, ranked)
    # The alias merge caps are a frozen ranking contract and stay exactly as they are. What is new
    # is only that the caller can learn whether they bound: admitting fewer candidates than were
    # offered is the merge's own definition of having run out of room.
    admitted = merge_rank_and_cap_alias_results(
        alias_results, ranked, parent_cap=5, asset_cap=10
    )
    if truncation is not None:
        truncation.exact_alias_capped = len(admitted) < candidate_count
        # The merge can only refuse candidates it was offered, so it cannot see a document the
        # window dropped first: that document never reaches ``candidate_count`` and the comparison
        # above reads it as a result that fit. Subtract the records the alias branch fetches by
        # parent id -- those arrive whatever the window did, so refusing them lost nothing -- and
        # whatever is left is a document this query matched and will never be offered.
        alias_documents = {result.chunk.document_id for result in alias_results}
        truncation.retrieval_window_capped = bool(
            window.refused_document_ids - alias_documents
        )
    return admitted


def ask_index(
    question: str,
    db_path: Path,
    filters: Optional[SearchFilters] = None,
    limit: int = 5,
    mode: str = "hybrid",
    governance_index: Optional[GovernanceIndex] = None,
    restricted_customers_path: Optional[Path] = None,
    audit_log_path: Path = DEFAULT_QUERY_AUDIT_LOG,
    provider_name: str = "mock",
    llm_config_path: Path = DEFAULT_LLM_CONFIG_PATH,
    llm_config: Optional[LLMConfig] = None,
    llm_provider: Optional[LLMProvider] = None,
    dry_run_llm: bool = False,
    llm_audit_log_path: Path = Path("reports/audit_log.csv"),
    query_plan: Optional[TypedQueryPlan] = None,
    parent_cap: int = DEFAULT_PARENT_CAP,
    asset_cap: int = DEFAULT_ASSET_CAP,
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

    provider_name = _normalize_provider_name(provider_name)
    resolved_llm_config = _resolve_llm_config(
        provider_name,
        dry_run_llm,
        llm_config,
        llm_config_path,
    )
    if provider_name != "mock" and not dry_run_llm:
        validate_provider_policy(resolved_llm_config, provider_name)

    query_plan = query_plan or build_index_query_plan(question, db_path, filters)
    alias_owner_ids = _exact_alias_owner_ids(
        question, query_plan, DEFAULT_ALIAS_PROJECTION_PATH
    )
    retrieval_limit = (
        max(limit, SQLiteIndex(Path(db_path)).counts()["chunks"])
        if query_plan.query_mode == "structured_lookup"
        else limit
    )
    truncation = RetrievalTruncation()
    results = search_index(
        question,
        db_path=db_path,
        filters=filters,
        limit=retrieval_limit,
        mode=mode,
        query_plan=query_plan,
        truncation=truncation,
    )
    results, removed_count = filter_restricted_results(results, governance_index)
    internal_result_count = _internal_result_count(
        question, db_path, filters, limit, mode, query_plan=query_plan
    ) if not results else 0
    if query_plan.query_mode == "structured_lookup" or alias_owner_ids:
        answer = generate_structured_answer(
            question,
            results,
            query_plan,
            governance_index=governance_index,
            parent_cap=parent_cap,
            asset_cap=asset_cap,
            retrieval_truncated=truncation.any_stage_capped,
        )
    else:
        answer = generate_answer_with_llm(
            question,
            results,
            filters=filters,
            provider_name=provider_name,
            config=resolved_llm_config,
            provider=llm_provider,
            dry_run=dry_run_llm,
            citation_limit=min(3, limit),
            internal_result_count=internal_result_count,
            audit_log_path=llm_audit_log_path,
            command="ask",
        )
        answer.query_plan = query_plan.to_dict()
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
    provider_name: str = "mock",
    llm_config_path: Path = DEFAULT_LLM_CONFIG_PATH,
    llm_config: Optional[LLMConfig] = None,
    llm_provider: Optional[LLMProvider] = None,
    dry_run_llm: bool = False,
    llm_audit_log_path: Path = Path("reports/audit_log.csv"),
    query_audit_metadata: Optional[Mapping[str, str]] = None,
    parent_cap: int = DEFAULT_PARENT_CAP,
    asset_cap: int = DEFAULT_ASSET_CAP,
) -> AgenticAnswer:
    filters = filters or SearchFilters()
    governance_index, load_warning = resolve_governance_index(governance_index, restricted_customers_path)
    refused = precheck_restricted_query(
        question,
        governance_index,
        command="agent-ask",
        audit_log_path=audit_log_path,
        audit_metadata=query_audit_metadata,
    )
    if refused is not None:
        return _refused_agentic_answer(refused)

    provider_name = _normalize_provider_name(provider_name)
    resolved_llm_config = _resolve_llm_config(
        provider_name,
        dry_run_llm,
        llm_config,
        llm_config_path,
    )
    if provider_name != "mock" and not dry_run_llm:
        validate_provider_policy(resolved_llm_config, provider_name)

    query_plan = build_index_query_plan(question, db_path, filters)

    def configured_ask(question, db_path, filters, limit, mode):
        return ask_index(
            question,
            db_path,
            filters=filters,
            limit=limit,
            mode=mode,
            governance_index=governance_index,
            provider_name=provider_name,
            llm_config=resolved_llm_config,
            llm_provider=llm_provider,
            dry_run_llm=dry_run_llm,
            llm_audit_log_path=llm_audit_log_path,
            query_plan=query_plan,
            parent_cap=parent_cap,
            asset_cap=asset_cap,
        )

    def configured_search(question, db_path, filters, limit, mode):
        return search_index(
            question,
            db_path,
            filters=filters,
            limit=limit,
            mode=mode,
            query_plan=query_plan,
        )

    def configured_generation(question, results, citation_limit, filters, internal_result_count):
        return generate_answer_with_llm(
            question,
            results,
            filters=filters,
            provider_name=provider_name,
            config=resolved_llm_config,
            provider=llm_provider,
            dry_run=dry_run_llm,
            citation_limit=citation_limit,
            internal_result_count=internal_result_count,
            audit_log_path=llm_audit_log_path,
            command="agent-ask",
        )

    answer = agentic_ask(
        question=question,
        db_path=db_path,
        search_fn=configured_search,
        ask_fn=configured_ask,
        filters=filters,
        limit=limit,
        mode=mode,
        governance_index=governance_index,
        generation_fn=configured_generation,
        typed_query_plan=query_plan,
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
    query_plan: Optional[TypedQueryPlan] = None,
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
        query_plan=query_plan,
    )
    return len({result.chunk.document_id for result in results})


def build_index_query_plan(
    query: str,
    db_path: Path,
    filters: Optional[SearchFilters] = None,
) -> TypedQueryPlan:
    chunks = SQLiteIndex(Path(db_path)).load_chunks()
    catalog = QueryCatalog.from_metadata(item.chunk.metadata for item in chunks)
    plan = build_query_plan(query, catalog)
    if filters is not None and not filters.is_empty():
        plan = allow_semantic_fallback(plan)
    return plan


def explain_query(
    query: str,
    db_path: Path,
    filters: Optional[SearchFilters] = None,
    limit: int = 20,
    mode: str = "hybrid",
    governance_index: Optional[GovernanceIndex] = None,
) -> dict:
    if governance_index is not None and governance_index.check_text(query).blocked:
        return {
            "query_plan": {
                "raw_query": "[restricted query]",
                "normalized_query": "[restricted query]",
                "query_mode": "refused",
                "constraints": [],
                "hard_filters": [],
                "ambiguity_flags": [],
                "parser_warnings": ["查詢在規劃前命中 restricted denylist。"],
                "abstain_reason": "restricted_query",
            },
            "candidate_count_before_filtering": 0,
            "candidate_count_after_filtering": 0,
            "governance_removed_count": 0,
            "final_entity_count": 0,
            "final_asset_count": 0,
            "abstain_reason": "restricted_query",
        }
    effective_filters = apply_intent_gating(filters or SearchFilters())
    query_plan = build_index_query_plan(query, db_path, filters)
    indexed_chunks = SQLiteIndex(Path(db_path)).load_chunks()
    before_documents = {
        item.chunk.document_id
        for item in indexed_chunks
        if matches_filters(item.chunk.metadata, effective_filters)
    }
    after_documents = {
        item.chunk.document_id
        for item in indexed_chunks
        if matches_filters(item.chunk.metadata, effective_filters)
        and metadata_matches_query_plan(item.chunk.metadata, query_plan)
    }
    results = search_index(
        query,
        db_path=db_path,
        filters=effective_filters,
        limit=limit,
        mode=mode,
        query_plan=query_plan,
    )
    filtered_results, governance_removed_count = filter_restricted_results(results, governance_index)
    structured = generate_structured_answer(
        query,
        filtered_results,
        query_plan,
        governance_index=governance_index,
    )
    return {
        "query_plan": query_plan.to_dict(),
        "unsupported_constraints": [item.to_dict() for item in query_plan.unsupported_constraints],
        "ambiguous_constraints": [item.to_dict() for item in query_plan.ambiguous_constraints],
        "invalid_constraints": [item.to_dict() for item in query_plan.invalid_constraints],
        "execution_blocked": query_plan.execution_blocked,
        "candidate_count_before_filtering": len(before_documents),
        "candidate_count_after_filtering": len(after_documents),
        "governance_removed_count": governance_removed_count,
        "final_entity_count": structured.structured_result.total_entities,
        "final_asset_count": structured.structured_result.total_assets,
        "abstain_reason": structured.structured_result.abstain_reason,
    }


def _dedupe_document_results(results: List[SearchResult]) -> List[SearchResult]:
    deduped: List[SearchResult] = []
    seen = set()
    for result in results:
        if result.chunk.document_id in seen:
            continue
        seen.add(result.chunk.document_id)
        deduped.append(result)
    return deduped


def _exact_alias_owner_ids(
    query: str,
    query_plan: TypedQueryPlan,
    alias_projection_path: Optional[Path],
) -> List[str]:
    projection, _alias_diagnostic = load_alias_projection(
        alias_projection_path, EXPECTED_ALIAS_AUTHORITY, EXPECTED_ALIAS_BINDING
    )
    return resolve_exact_alias_parent_ids(query, query_plan, projection)


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


def _normalize_provider_name(provider_name: str) -> str:
    normalized = str(provider_name).strip().lower()
    if normalized not in {"mock", "anthropic"}:
        raise LLMError(f"不支援的 LLM provider：{normalized}")
    return normalized


def _resolve_llm_config(
    provider_name: str,
    dry_run_llm: bool,
    llm_config: Optional[LLMConfig],
    llm_config_path: Path,
) -> LLMConfig:
    if llm_config is not None:
        return llm_config
    if provider_name == "mock" and not dry_run_llm:
        return LLMConfig()
    return load_llm_config(llm_config_path)
