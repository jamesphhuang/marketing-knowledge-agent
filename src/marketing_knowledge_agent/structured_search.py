"""Slack structured (faceted) search: request contract, plan builder and execution.

The Slack modal collects up to three hard filters -- interview year, Sales Category LV2, content
tags -- plus an optional free-text goal. This module turns that selection directly into a
``TypedQueryPlan`` and executes it; it never serialises the selection back into a natural-language
string and hands it to the free-text parser. Doing that would re-introduce every ambiguity the
parser exists to refuse, for a value the user already stated unambiguously by picking it from a
list.

Semantics, restated because they are load-bearing:

- multiple values in one field are OR'd together (``operator="in"`` / ``"contains_any"``);
- the (at most three) fields the user touched are AND'd together;
- a field the user selected in the modal can never be overridden or added to by the free-text
  parser -- ``build_query_plan``'s ``preresolved_fields`` keeps the Authority from re-opening it,
  and any residual free-text constraint on that field is dropped here besides;
- a zero-result search is reported with the filters that were actually applied, never silently
  relaxed.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .governance import (
    RESTRICTED_RESULT_REMOVAL_WARNING,
    apply_governance_to_answer,
    filter_restricted_results,
)
from .indexing import SQLiteIndex
from .models import GeneratedAnswer, SearchFilters, SearchResult
from .pipeline import (
    DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    ask_index,
    load_restricted_customers_governance_index,
)
from .query_gating import DEFAULT_QUERY_AUDIT_LOG, apply_intent_gating, enforce_external_citations
from .query_planning import (
    TAXONOMY_FIELDS,
    QueryCatalog,
    QueryConstraint,
    TypedQueryPlan,
    build_query_plan,
    metadata_matches_query_plan,
    normalize_exact_value,
)
from .retrieval import matches_filters
from .search_facets import FacetCatalog
from .search_taxonomy import SearchTaxonomy
from .structured_results import DEFAULT_ASSET_CAP, DEFAULT_PARENT_CAP, generate_structured_answer


MAX_SELECTED_PER_FIELD = 3
STRUCTURED_SEARCH_SOURCE = "slack_modal"


class StructuredSearchValidationError(ValueError):
    """Raised when a Slack structured-search submission cannot be trusted as stated."""


class StaleFacetCatalogError(StructuredSearchValidationError):
    """Raised when a submission was built against a facet catalog that is no longer current."""


@dataclass(frozen=True)
class StructuredSearchRequest:
    interview_years: Tuple[int, ...] = ()
    sales_category_lv2: Tuple[str, ...] = ()
    content_tags: Tuple[str, ...] = ()
    free_text: str = ""
    catalog_version: str = ""


def validate_structured_search_request(request: StructuredSearchRequest, catalog: FacetCatalog) -> None:
    """Re-validate a submission server-side against the live facet catalog, or refuse it.

    Nothing here trusts the Slack payload's display text. Every selected value is re-checked
    against ``catalog`` -- the same pinned Authority/index intersection the modal was opened
    with -- and a catalog whose version no longer matches is refused outright rather than executed
    against options that may no longer be eligible.
    """
    if request.catalog_version != catalog.catalog_version:
        raise StaleFacetCatalogError("搜尋條件已過期，請重新開啟「條件搜尋」視窗。")

    if not (
        request.interview_years
        or request.sales_category_lv2
        or request.content_tags
        or request.free_text.strip()
    ):
        raise StructuredSearchValidationError("請至少填寫一個搜尋條件。")

    for label, values, is_valid in (
        ("採訪年份", request.interview_years, catalog.is_valid_year),
        ("Sales Category LV2", request.sales_category_lv2, catalog.is_valid_sales_category_lv2),
        ("內容相關標籤", request.content_tags, catalog.is_valid_content_tag),
    ):
        if len(values) > MAX_SELECTED_PER_FIELD:
            raise StructuredSearchValidationError(f"{label} 最多只能選擇 {MAX_SELECTED_PER_FIELD} 個。")
        for value in values:
            if not is_valid(value):
                raise StructuredSearchValidationError(f"{label} 的選項「{value}」不在目前可搜尋的選項中。")


def build_structured_query_plan(
    request: StructuredSearchRequest,
    query_catalog: QueryCatalog,
    taxonomy: Optional[SearchTaxonomy],
) -> TypedQueryPlan:
    """Build one ``TypedQueryPlan`` directly from a validated request.

    The modal's own selections become hard constraints without ever passing through the free-text
    parser. Only the optional free-text goal is parsed, and only for fields the modal left
    untouched: ``preresolved_fields`` stops the Authority from reopening a field the user already
    decided, and any free-text constraint that still lands on that field is dropped below besides.
    """
    modal_constraints: List[QueryConstraint] = []
    modal_fields: set = set()

    if request.interview_years:
        years = sorted({int(year) for year in request.interview_years})
        modal_constraints.append(
            QueryConstraint(
                field="interview_year",
                value=years,
                normalized_value=years,
                operator="in",
                match_type="exact",
                hard_filter=True,
                source=STRUCTURED_SEARCH_SOURCE,
            )
        )
        modal_fields.add("interview_year")

    if request.sales_category_lv2:
        values = list(dict.fromkeys(request.sales_category_lv2))
        modal_constraints.append(
            QueryConstraint(
                field="sales_category_lv2",
                value=values,
                normalized_value=[normalize_exact_value(value) for value in values],
                operator="in",
                match_type="canonical_exact",
                hard_filter=True,
                source=STRUCTURED_SEARCH_SOURCE,
            )
        )
        modal_fields.add("sales_category_lv2")

    if request.content_tags:
        values = list(dict.fromkeys(request.content_tags))
        modal_constraints.append(
            QueryConstraint(
                field="content_tags",
                value=values,
                normalized_value=[normalize_exact_value(value) for value in values],
                operator="contains_any",
                match_type="exact_tag",
                hard_filter=True,
                source=STRUCTURED_SEARCH_SOURCE,
            )
        )
        modal_fields.add("content_tags")

    free_text = request.free_text.strip()
    normalized_query = ""
    free_constraints: List[QueryConstraint] = []
    free_text_terms: List[str] = []
    requested_asset_types: List[str] = []
    resolved_entities = []
    ambiguity_flags: List[str] = []
    parser_warnings: List[str] = []
    abstain_reason: Optional[str] = None

    if free_text:
        preresolved_fields = tuple(name for name in modal_fields if name in TAXONOMY_FIELDS)
        sub_plan = build_query_plan(
            free_text, query_catalog, taxonomy=taxonomy, preresolved_fields=preresolved_fields
        )
        normalized_query = sub_plan.normalized_query
        # The field the modal already decided must not be overridden or widened by free text, so
        # any residual constraint the parser still produced for it (an explicit ``field=value``
        # mention, for instance) is dropped rather than merged.
        free_constraints = [item for item in sub_plan.constraints if item.field not in modal_fields]
        free_text_terms = list(sub_plan.free_text_terms)
        requested_asset_types = list(sub_plan.requested_asset_types)
        resolved_entities = list(sub_plan.resolved_entities)
        parser_warnings = list(sub_plan.parser_warnings)
        abstain_reason = sub_plan.abstain_reason
        for flag in sub_plan.ambiguity_flags:
            if flag == "conflicting_interview_years" and "interview_year" in modal_fields:
                # The modal's own year selection is authoritative; free-text year noise the user
                # never addressed there must not veto it.
                continue
            ambiguity_flags.append(flag)
        if (
            abstain_reason == "conflicting_constraints"
            and "interview_year" in modal_fields
            and "conflicting_interview_years" in sub_plan.ambiguity_flags
        ):
            abstain_reason = None
        if modal_constraints and abstain_reason in {
            "unresolved_structured_lookup",
            "ambiguous_entity_type",
        }:
            # These two reasons mean "the free text alone did not resolve to anything sensible" --
            # a fair refusal when free text is the *only* thing driving the search, but not when the
            # modal already supplies real hard constraints elsewhere. The free text here is a
            # supplementary ranking goal, not the sole basis for the search, so it must not veto a
            # search that already has structure.
            abstain_reason = None

    return TypedQueryPlan(
        raw_query=request.free_text,
        normalized_query=normalized_query,
        query_mode="structured_lookup",
        parsed_terms=[],
        resolved_entities=resolved_entities,
        constraints=modal_constraints + free_constraints,
        operator="AND",
        free_text_terms=free_text_terms,
        requested_asset_types=requested_asset_types,
        sort=["relevance"],
        group_by=["entity"],
        fallback_policy="abstain",
        ambiguity_flags=ambiguity_flags,
        parser_warnings=parser_warnings,
        abstain_reason=abstain_reason,
    )


def execute_structured_search(
    request: StructuredSearchRequest,
    db_path: Path,
    taxonomy: SearchTaxonomy,
    restricted_customers_path: Optional[Path] = DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    audit_log_path: Path = DEFAULT_QUERY_AUDIT_LOG,
    parent_cap: int = DEFAULT_PARENT_CAP,
    asset_cap: int = DEFAULT_ASSET_CAP,
) -> GeneratedAnswer:
    """Execute a validated structured-search request and return a governed answer.

    Retrieval order is: hard structured filters first (inside ``metadata_matches_query_plan``),
    then lexical/semantic scoring on whatever survives -- never the reverse. When ``free_text`` is
    blank this is a pure structured browse and there is nothing to rank, so results are ordered
    deterministically by interview year (newest first) and then by a stable record id, instead of
    depending on undefined SQLite row order.
    """
    chunks = SQLiteIndex(Path(db_path)).load_chunks()
    query_catalog = QueryCatalog.from_metadata(item.chunk.metadata for item in chunks)
    query_plan = build_structured_query_plan(request, query_catalog, taxonomy)

    free_text = request.free_text.strip()
    if free_text:
        return ask_index(
            free_text,
            db_path,
            filters=SearchFilters(intent="external"),
            restricted_customers_path=restricted_customers_path,
            audit_log_path=audit_log_path,
            query_plan=query_plan,
            parent_cap=parent_cap,
            asset_cap=asset_cap,
        )

    return _execute_structured_browse(
        query_plan,
        db_path=db_path,
        restricted_customers_path=restricted_customers_path,
        parent_cap=parent_cap,
        asset_cap=asset_cap,
    )


def _execute_structured_browse(
    query_plan: TypedQueryPlan,
    db_path: Path,
    restricted_customers_path: Optional[Path],
    parent_cap: int,
    asset_cap: int,
) -> GeneratedAnswer:
    governance_index = None
    load_warning = None
    if restricted_customers_path is not None:
        governance_index, load_warning = load_restricted_customers_governance_index(
            Path(restricted_customers_path)
        )

    filters = apply_intent_gating(SearchFilters(intent="external"))
    by_document: "OrderedDict[str, object]" = OrderedDict()
    for indexed_chunk in SQLiteIndex(Path(db_path)).load_chunks():
        chunk = indexed_chunk.chunk
        if chunk.document_id in by_document:
            continue
        metadata = chunk.metadata
        if not matches_filters(metadata, filters):
            continue
        if not metadata_matches_query_plan(metadata, query_plan):
            continue
        by_document[chunk.document_id] = chunk

    results = [SearchResult(chunk=chunk, score=0.0) for chunk in by_document.values()]
    results.sort(key=_structured_browse_sort_key)
    results, removed_count = filter_restricted_results(results, governance_index)

    answer = generate_structured_answer(
        "",
        results,
        query_plan,
        governance_index=governance_index,
        parent_cap=parent_cap,
        asset_cap=asset_cap,
        retrieval_truncated=False,
    )
    answer = apply_governance_to_answer(answer, governance_index)
    if removed_count:
        warning = RESTRICTED_RESULT_REMOVAL_WARNING.format(count=removed_count)
        if warning not in answer.warnings:
            answer.warnings.append(warning)
    if load_warning and load_warning not in answer.warnings:
        answer.warnings.append(load_warning)
    enforce_external_citations(answer, SearchFilters(intent="external"))
    return answer


def _structured_browse_sort_key(result: SearchResult) -> Tuple[int, str]:
    metadata = result.chunk.metadata
    year_rank = -(metadata.interview_year if metadata.interview_year is not None else -1)
    stable_id = (
        f"{metadata.source_sheet}:r{metadata.source_row}"
        if metadata.source_sheet and metadata.source_row is not None
        else result.chunk.document_id
    )
    return (year_rank, stable_id)
