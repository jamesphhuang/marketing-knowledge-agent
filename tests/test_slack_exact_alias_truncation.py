"""Exact-alias retrieval truncation must reach the Slack wording contract.

The Slack surface materialises up to SLACK_SEARCH_PARENT_CAP brand records and describes a result
that reaches that ceiling as a ceiling rather than as a complete total. The exact-alias path has
its own, much lower retrieval caps in pipeline.search_index, and they bind long before the Slack
ceiling does: the structured layer then never sees the candidates they refused, and the result
arrives looking like a small complete one. These tests pin the upstream fact travelling to the
renderer, surviving grouping and governance filtering on the way, and changing nothing about the
alias caps themselves.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import (
    Citation,
    Document,
    DocumentMetadata,
    GeneratedAnswer,
    SearchFilters,
    StructuredAsset,
    StructuredEntity,
    StructuredRetrievalResult,
)
from marketing_knowledge_agent.pipeline import RetrievalTruncation, agent_ask, search_index
from marketing_knowledge_agent.search_aliases import (
    EXPECTED_ALIAS_AUTHORITY,
    EXPECTED_ALIAS_BINDING,
    alias_merge_candidate_count,
    merge_rank_and_cap_alias_results,
)
from marketing_knowledge_agent.slack_interface import (
    SLACK_SEARCH_ASSET_CAP,
    SLACK_SEARCH_PARENT_CAP,
    SlackConfig,
    handle_slack_event,
)
from marketing_knowledge_agent.slack_presentation import build_structured_slack_pages


ALIAS_QUERY = "SHOPLINE Payments"
ALIAS_OWNER_ID = "商家夥伴案例資料庫:r32"
# The alias merge's own caps, restated here so a change to either one fails a test on the way past.
ALIAS_PARENT_CAP = 5
ALIAS_ASSET_CAP = 10
COMPLETE_TOTAL_PREFIX = "共找到"
CAPPED_PREFIX = "目前顯示最多"


# --- fixtures --------------------------------------------------------------------------------------


def _merchant(row, brand_name, *, article_title, handle=None, can_quote_externally=True, allowed=None):
    metadata = DocumentMetadata(
        title=brand_name,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 7, 1),
        source_path=f"record-r{row}.md",
        source_sheet="商家夥伴案例資料庫",
        source_row=row,
        brand_name=brand_name,
        merchant_handle=handle,
        article_title=article_title,
        data_classification="public",
        can_quote_externally=can_quote_externally,
        allowed_exposure_channels=allowed or [],
        content_tags=[ALIAS_QUERY],
        can_enter_content_index=True,
    )
    return Document(
        id=f"parent-r{row}", metadata=metadata, content=f"{ALIAS_QUERY} candidate {row}"
    )


def _alias_index(tmp_path, monkeypatch, extras):
    """An index whose alias owner is r32 and whose other records all match the alias query.

    ``extras`` is the list of non-owner records, so a test picks exactly how many distinct parents
    the merge is offered and therefore whether its caps have anything to refuse.
    """
    monkeypatch.chdir(tmp_path)
    projection_path = tmp_path / ".mka/search_alias_projection.json"
    projection_path.parent.mkdir(parents=True)
    _write_projection(projection_path)
    restricted_path = tmp_path / "restricted.json"
    restricted_path.write_text("[]\n", encoding="utf-8")

    documents = [_merchant(32, "聊心茶室", article_title="聊心茶室 Article")] + extras
    db_path = tmp_path / "content_index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return {
        "db_path": db_path,
        "restricted_path": restricted_path,
        "audit_path": tmp_path / "slack_audit.csv",
    }


def _organic(count, *, start=1, brand=None, handles=False, can_quote_externally=True, allowed=None):
    return [
        _merchant(
            row,
            brand or f"Organic Parent {row}",
            article_title=f"Organic Article {row}",
            handle=f"handle-{row}" if handles else None,
            can_quote_externally=can_quote_externally,
            allowed=allowed,
        )
        for row in range(start, start + count)
    ]


def _slack_query(runtime, query=ALIAS_QUERY):
    captured = []

    def recording_ask(*args, **kwargs):
        answer = agent_ask(*args, **kwargs)
        captured.append(answer)
        return answer

    reply = handle_slack_event(
        {"text": query, "channel": "C-ALIAS-TRUNC", "user": "U1", "ts": "300.1"},
        config=SlackConfig(allowed_channel_ids=["C-ALIAS-TRUNC"]),
        ask_fn=recording_ask,
        db_path=runtime["db_path"],
        restricted_customers_path=runtime["restricted_path"],
        llm_config_path=Path("missing-llm-config.json"),
        audit_log_path=runtime["audit_path"],
    )
    return reply, captured[0]


def _summary_line(text):
    return next(
        line
        for line in text.splitlines()
        if line.startswith(COMPLETE_TOTAL_PREFIX) or line.startswith(CAPPED_PREFIX)
    )


def _parent_ids(answer):
    identities = []
    for citation in answer.citations:
        identity = f"{citation.source_sheet}:r{citation.source_row}"
        if identity not in identities:
            identities.append(identity)
    return identities


# --- A. below the alias cap: nothing was refused, so the total is a real total ------------------------


def test_exact_alias_below_its_cap_is_not_truncated(tmp_path, monkeypatch):
    runtime = _alias_index(tmp_path, monkeypatch, _organic(2))

    reply, answer = _slack_query(runtime)
    structured = answer.generated.structured_result

    assert ALIAS_OWNER_ID in _parent_ids(answer)
    assert len(structured.matched_entities) == 3
    assert structured.retrieval_truncated is False
    assert _summary_line(reply["text"]) == "共找到 3 個品牌／夥伴、3 筆內容。"
    assert CAPPED_PREFIX not in reply["text"]


# --- B. at the alias cap with more underlying candidates ---------------------------------------------


def test_exact_alias_at_its_cap_reports_a_capped_result(tmp_path, monkeypatch):
    runtime = _alias_index(tmp_path, monkeypatch, _organic(8))

    reply, answer = _slack_query(runtime)
    structured = answer.generated.structured_result

    # Nine parents exist and match; the alias merge admits five and refuses the rest.
    assert len(structured.matched_entities) == ALIAS_PARENT_CAP
    assert structured.retrieval_truncated is True
    # The Slack ceiling is nowhere near: this result would read as complete without the signal.
    assert len(structured.matched_entities) < SLACK_SEARCH_PARENT_CAP
    assert _summary_line(reply["text"]).startswith(CAPPED_PREFIX)
    assert COMPLETE_TOTAL_PREFIX not in reply["text"]
    # Nothing claims a number the system does not have.
    assert "9" not in _summary_line(reply["text"])


def test_the_capped_wording_is_the_existing_v2_wording(tmp_path, monkeypatch):
    runtime = _alias_index(tmp_path, monkeypatch, _organic(8))

    reply, _answer = _slack_query(runtime)

    assert _summary_line(reply["text"]) == "目前顯示最多 5 個品牌／夥伴，共 5 筆內容。"
    assert "已顯示目前最多可提供的 5 個品牌／夥伴。" in reply["text"]
    assert "若想查看更多可能結果，請縮小或調整搜尋條件後重新搜尋。" in reply["text"]


# --- C. grouping collapses brands after the cap bound -------------------------------------------------


def test_grouping_that_shrinks_the_brand_count_keeps_the_truncation_signal(tmp_path, monkeypatch):
    # Every non-owner record carries one shared brand name, so whichever four the merge admits
    # alongside the owner, _presentation_entities collapses them into a single brand. The upstream
    # fact must not shrink with them.
    runtime = _alias_index(tmp_path, monkeypatch, _organic(8, brand="Shared Brand"))

    reply, answer = _slack_query(runtime)
    structured = answer.generated.structured_result
    pages = build_structured_slack_pages(answer)

    assert len(structured.matched_entities) == ALIAS_PARENT_CAP
    assert structured.retrieval_truncated is True
    assert pages.total_entities < ALIAS_PARENT_CAP
    assert _summary_line(reply["text"]).startswith(CAPPED_PREFIX)
    assert COMPLETE_TOTAL_PREFIX not in reply["text"]


def test_grouping_below_the_cap_still_reads_as_a_complete_total(tmp_path, monkeypatch):
    """The mirror case: collapsing brands alone must never be enough to claim a capped result."""
    extras = _organic(2, start=1, brand="Shared Brand")
    runtime = _alias_index(tmp_path, monkeypatch, extras)

    reply, answer = _slack_query(runtime)
    pages = build_structured_slack_pages(answer)

    assert answer.generated.structured_result.retrieval_truncated is False
    assert pages.total_entities == 2 < len(answer.generated.structured_result.matched_entities)
    assert _summary_line(reply["text"]).startswith(COMPLETE_TOTAL_PREFIX)


# --- D. governance filtering drops a group after the cap bound -----------------------------------------


def test_conflict_withheld_brands_keep_the_truncation_signal(tmp_path, monkeypatch):
    # Every non-owner record shares one brand name under a different handle, so the grouped brand
    # has conflicting handles and _presentation_entities withholds it outright. Whichever four the
    # merge admits, only the owner stays visible; the upstream fact does not move with them.
    runtime = _alias_index(
        tmp_path, monkeypatch, _organic(8, brand="Shared Brand", handles=True)
    )

    reply, answer = _slack_query(runtime)
    structured = answer.generated.structured_result
    pages = build_structured_slack_pages(answer)

    assert len(structured.matched_entities) == ALIAS_PARENT_CAP
    assert structured.retrieval_truncated is True
    assert pages.total_entities < ALIAS_PARENT_CAP
    assert pages.total_entities == 1
    assert _summary_line(reply["text"]).startswith(CAPPED_PREFIX)
    assert COMPLETE_TOTAL_PREFIX not in reply["text"]
    assert "Shared Brand" not in reply["text"]


# --- E / F. the non-alias paths are untouched by this remediation ---------------------------------------


def _presentation_answer(brand_count, *, retrieval_truncated=False, withhold_last=False):
    entities = [
        StructuredEntity(
            entity_type="merchant",
            entity_name=f"品牌{index:02d}",
            merchant_handle=f"h{index}",
            assets=[
                StructuredAsset(
                    asset_type="article",
                    title=f"文章 {index}",
                    external_usage_status="可對外引用",
                    source_record_id=f"商家夥伴案例資料庫:r{index}",
                    source_sheet="商家夥伴案例資料庫",
                    source_row=index,
                    citation_label=f"[{index}]",
                )
            ],
        )
        for index in range(1, brand_count + 1)
    ]
    citations = [
        Citation(
            label=f"[{index}]",
            title=f"文章 {index}",
            source_path=f"p{index}",
            chunk_id=f"chunk-{index}:article",
            status="published",
            source_type="database",
            record_type="merchant_case",
            data_classification="public",
            can_quote_externally=not (withhold_last and index == brand_count),
            publish_date="2026-07-01",
            source_sheet="商家夥伴案例資料庫",
            source_row=index,
            allowed_exposure_channels=[],
            freshness_note="fresh",
        )
        for index in range(1, brand_count + 1)
    ]
    structured = StructuredRetrievalResult(
        query_plan={"raw_query": "q", "normalized_query": "q", "hard_filters": []},
        matched_entities=entities,
        total_entities=brand_count,
        total_assets=brand_count,
        retrieval_truncated=retrieval_truncated,
    )
    return GeneratedAnswer(
        question="q",
        answer="unused",
        citations=citations,
        structured_result=structured,
        governance_checked=True,
    )


def test_governance_withheld_brands_keep_the_truncation_signal():
    """The other way the visible count drops: an entity whose only asset fails written external use.

    Retrieval gating normally removes such a record before it is ever a candidate, so this is
    pinned at the presentation layer, where the withholding actually happens.
    """
    answer = _presentation_answer(4, retrieval_truncated=True, withhold_last=True)

    pages = build_structured_slack_pages(answer)

    assert len(answer.structured_result.matched_entities) == 4
    assert pages.total_entities == 3
    assert pages.pages[0].splitlines()[2].startswith(CAPPED_PREFIX)
    assert COMPLETE_TOTAL_PREFIX not in "\n".join(pages.pages)


def test_non_alias_result_below_the_slack_ceiling_is_unchanged():
    pages = build_structured_slack_pages(_presentation_answer(20))

    assert f"{COMPLETE_TOTAL_PREFIX} 20 個品牌／夥伴、20 筆內容。" in pages.pages[0]
    assert CAPPED_PREFIX not in "\n".join(pages.pages)


def test_non_alias_result_at_the_slack_ceiling_keeps_the_bcdb162_behaviour():
    pages = build_structured_slack_pages(_presentation_answer(SLACK_SEARCH_PARENT_CAP))

    assert pages.pages[0].splitlines()[2].startswith(CAPPED_PREFIX)
    assert COMPLETE_TOTAL_PREFIX not in "\n".join(pages.pages)
    assert f"已顯示目前最多可提供的 {SLACK_SEARCH_PARENT_CAP} 個品牌／夥伴。" in pages.pages[-1]


def test_the_two_truncation_signals_are_independent():
    """Either signal alone is enough; neither is required for the other to work."""
    upstream_only = build_structured_slack_pages(_presentation_answer(4, retrieval_truncated=True))
    ceiling_only = build_structured_slack_pages(_presentation_answer(SLACK_SEARCH_PARENT_CAP))
    neither = build_structured_slack_pages(_presentation_answer(4))

    assert upstream_only.pages[0].splitlines()[2].startswith(CAPPED_PREFIX)
    assert ceiling_only.pages[0].splitlines()[2].startswith(CAPPED_PREFIX)
    assert neither.pages[0].splitlines()[2].startswith(COMPLETE_TOTAL_PREFIX)


# --- G. the alias caps themselves are unchanged ------------------------------------------------------


def test_the_alias_retrieval_caps_are_not_widened(tmp_path, monkeypatch):
    runtime = _alias_index(tmp_path, monkeypatch, _organic(20))

    _reply, answer = _slack_query(runtime)
    structured = answer.generated.structured_result

    # Still five parents and ten assets out of the alias merge, not the Slack display capacity.
    assert len(structured.matched_entities) == ALIAS_PARENT_CAP
    assert len(structured.matched_entities) != SLACK_SEARCH_PARENT_CAP
    assert len(answer.citations) <= ALIAS_ASSET_CAP
    assert len(answer.citations) != SLACK_SEARCH_ASSET_CAP


def test_search_index_default_caps_are_five_and_ten(tmp_path, monkeypatch):
    """Read the caps off the merge the pipeline actually calls, not off a restated constant."""
    runtime = _alias_index(tmp_path, monkeypatch, _organic(20))
    seen = {}
    real = merge_rank_and_cap_alias_results

    def spy(alias_results, organic_results, parent_cap=5, asset_cap=10):
        seen["parent_cap"] = parent_cap
        seen["asset_cap"] = asset_cap
        seen["candidates"] = alias_merge_candidate_count(alias_results, organic_results)
        return real(alias_results, organic_results, parent_cap=parent_cap, asset_cap=asset_cap)

    monkeypatch.setattr("marketing_knowledge_agent.pipeline.merge_rank_and_cap_alias_results", spy)
    truncation = RetrievalTruncation()
    results = search_index(
        ALIAS_QUERY,
        runtime["db_path"],
        filters=SearchFilters(intent="external"),
        limit=SLACK_SEARCH_PARENT_CAP,
        truncation=truncation,
    )

    assert seen["parent_cap"] == ALIAS_PARENT_CAP
    assert seen["asset_cap"] == ALIAS_ASSET_CAP
    assert len(results) <= ALIAS_ASSET_CAP
    assert seen["candidates"] > len(results)
    assert truncation.exact_alias_capped is True


# --- the diagnostic channel itself ----------------------------------------------------------------


def test_the_diagnostic_channel_is_optional_and_defaults_to_untruncated(tmp_path, monkeypatch):
    runtime = _alias_index(tmp_path, monkeypatch, _organic(8))

    without = search_index(
        ALIAS_QUERY, runtime["db_path"], filters=SearchFilters(intent="external"), limit=60
    )
    truncation = RetrievalTruncation()
    with_channel = search_index(
        ALIAS_QUERY,
        runtime["db_path"],
        filters=SearchFilters(intent="external"),
        limit=60,
        truncation=truncation,
    )

    # Passing the channel cannot change what retrieval returns.
    assert [result.chunk.id for result in without] == [
        result.chunk.id for result in with_channel
    ]
    assert RetrievalTruncation().exact_alias_capped is False
    assert truncation.exact_alias_capped is True


def test_a_non_alias_query_leaves_the_channel_untouched(tmp_path, monkeypatch):
    runtime = _alias_index(tmp_path, monkeypatch, _organic(8))
    truncation = RetrievalTruncation()

    search_index(
        "聊心茶室",
        runtime["db_path"],
        filters=SearchFilters(intent="external"),
        limit=60,
        truncation=truncation,
    )

    assert truncation.exact_alias_capped is False


def test_candidate_count_dedupes_exactly_as_the_merge_does(tmp_path, monkeypatch):
    """The count and the merge must agree on what one candidate is, or the comparison lies."""
    runtime = _alias_index(tmp_path, monkeypatch, _organic(8))
    results = search_index(
        ALIAS_QUERY, runtime["db_path"], filters=SearchFilters(intent="external"), limit=60
    )

    # Feeding the same list in twice must not look like twice as many candidates.
    assert alias_merge_candidate_count(results, results) == len(
        {result.chunk.document_id for result in results}
    )
    assert alias_merge_candidate_count([], []) == 0


def _write_projection(path):
    payload = {
        "schema_version": 1,
        "projection_type": "production_search_aliases",
        "authority": dict(EXPECTED_ALIAS_AUTHORITY),
        "normalization_contract": {
            "version": "alias-normalization-v1",
            "hash": "b4f05430b26bde6be675ca6d9647044048c752d724ef7c4688afb50d34941bc6",
        },
        "query_semantics_contract": {
            "version": "alias-query-semantics-v1",
            "hash": "b52429126c031079a0034eb125573bc5252d2514eb075237af82d8f79e7bfecc",
        },
        "aliases": [
            {
                "raw_alias": ALIAS_QUERY,
                "normalized_alias": "shopline payments",
                "parent_record_id": ALIAS_OWNER_ID,
                "active": True,
                "reviewer": "Admin",
                "reviewed_at": "2026-07-18T00:33:08+08:00",
                "provenance": "admin_resolution",
                "authority_reference": "event-shopline-payments",
            },
        ],
        **EXPECTED_ALIAS_BINDING,
        "generated_at": "2026-07-27T15:00:00+08:00",
        "runtime_compatibility_version": "production-search-alias-runtime-v1",
        "projection_hash_algorithm": "sha256",
        "projection_hash_scope": (
            "canonical_json_utf8_sorted_keys_compact_without_projection_hash_no_trailing_newline"
        ),
        "projection_hash": "",
    }
    scope = {key: value for key, value in payload.items() if key != "projection_hash"}
    payload["projection_hash"] = hashlib.sha256(
        json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
