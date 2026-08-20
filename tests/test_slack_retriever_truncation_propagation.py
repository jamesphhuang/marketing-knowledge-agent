"""The organic retrieval window cuts candidates before the alias merge can count them.

``pipeline.search_index`` asks ``SQLiteRetriever.search`` for three times the caller's limit and
merges what comes back with the alias branch's own records. The merge decides truncation by
comparing what it admitted with what it was offered -- but a document the window already refused
was never offered, so the merge reads a cut result as one that fit, and Slack introduces it with
「共找到 N 個品牌／夥伴」. On the Slack path the caller's limit is 5, so that window is fifteen
chunks: a handful of long documents fills it on their own.

These tests pin the second, independent signal: the window reports which *documents* it refused,
the alias branch subtracts the ones it re-supplies by parent id, and whatever is left means this
query matched something the user will never be offered. Chunks are not documents and an exactly
full window refuses nothing -- both are pinned here, because either mistake turns a complete
result into a false disclosure.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata, SearchFilters
from marketing_knowledge_agent.pipeline import (
    RetrievalTruncation,
    agent_ask,
    build_index_query_plan,
    search_index,
)
from marketing_knowledge_agent.query_gating import apply_intent_gating
from marketing_knowledge_agent.retrieval import RetrievalWindow, SQLiteRetriever
from marketing_knowledge_agent.search_aliases import (
    EXPECTED_ALIAS_AUTHORITY,
    EXPECTED_ALIAS_BINDING,
)
from marketing_knowledge_agent.slack_interface import SlackConfig, handle_slack_event


ALIAS_QUERY = "SHOPLINE Payments"
ALIAS_OWNER_ID = "商家夥伴案例資料庫:r32"
ALIAS_OWNER_BRAND = "聊心茶室"
# A query that resolves to no alias and to no typed constraint: the organic branch on its own.
ORGANIC_QUERY = "SHOPLINE Payments 導入經驗"
# The Slack path never passes a limit, so agent_ask's default decides the window: 5 * 3.
SLACK_RETRIEVAL_LIMIT = 5
SLACK_WINDOW = SLACK_RETRIEVAL_LIMIT * 3
# The alias merge's frozen caps, restated so widening either one fails a test on the way past.
ALIAS_PARENT_CAP = 5
ALIAS_ASSET_CAP = 10
COMPLETE_TOTAL_PREFIX = "共找到"
CAPPED_PREFIX = "目前顯示最多"

MATCHING_TEXT = f"{ALIAS_QUERY} 合作案例 payments 導入經驗 "
UNRELATED_TEXT = "毫無關聯的內容 filler"
# Enough repetitions of MATCHING_TEXT to split into LONG_DOCUMENT_CHUNKS chunks.
LONG_DOCUMENT_REPEATS = 240
LONG_DOCUMENT_CHUNKS = 10
# A content tag the query planner resolves into a hard constraint, so the query is a structured
# lookup rather than a semantic question.
STRUCTURED_TAG = "支付導入"


# --- fixtures --------------------------------------------------------------------------------------


def _merchant(row, brand, *, body, tags=None):
    metadata = DocumentMetadata(
        title=brand,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 7, 1),
        source_path=f"record-r{row}.md",
        source_sheet="商家夥伴案例資料庫",
        source_row=row,
        brand_name=brand,
        article_title=f"{brand} Article",
        data_classification="public",
        can_quote_externally=True,
        allowed_exposure_channels=[],
        content_tags=list(tags or []),
        can_enter_content_index=True,
    )
    return Document(id=f"parent-r{row}", metadata=metadata, content=body)


def _matching(repeats=1):
    """Content the query scores against; more repeats means both more chunks and a higher score."""
    return (MATCHING_TEXT * repeats).strip()


def _owner(*, body=None, tags=None):
    return _merchant(32, ALIAS_OWNER_BRAND, body=body or _matching(), tags=tags)


def _organic(count, *, start=1, repeats=1, tags=None):
    return [
        _merchant(row, f"Organic Parent {row}", body=_matching(repeats), tags=tags)
        for row in range(start, start + count)
    ]


def _runtime(tmp_path, monkeypatch, documents):
    monkeypatch.chdir(tmp_path)
    projection_path = tmp_path / ".mka/search_alias_projection.json"
    projection_path.parent.mkdir(parents=True)
    _write_projection(projection_path)
    restricted_path = tmp_path / "restricted.json"
    restricted_path.write_text("[]\n", encoding="utf-8")

    db_path = tmp_path / "content_index.sqlite"
    chunks = chunk_documents(documents)
    SQLiteIndex(db_path).rebuild(documents, chunks)
    return {
        "db_path": db_path,
        "restricted_path": restricted_path,
        "audit_path": tmp_path / "slack_audit.csv",
        "chunk_count": len(chunks),
        "document_count": len(documents),
    }


def _plan(runtime, query):
    return build_index_query_plan(query, runtime["db_path"], SearchFilters(intent="external"))


def _window_shape(runtime, query, limit):
    """What the retriever's own window does, read off the retriever rather than assumed.

    Every test below depends on a particular fixture landing on a particular side of the window,
    and content length and hashed embeddings decide that only indirectly. Asserting the shape here
    means a fixture that drifts fails as a wrong precondition instead of as a wrong verdict.
    """
    plan = _plan(runtime, query)
    filters = apply_intent_gating(SearchFilters(intent="external"))
    retriever = SQLiteRetriever(runtime["db_path"])
    everything = retriever.search(query, filters=filters, limit=10**6, query_plan=plan)
    window = RetrievalWindow()
    admitted = retriever.search(
        query, filters=filters, limit=max(limit * 3, limit), query_plan=plan, window=window
    )
    return {
        "query_mode": plan.query_mode,
        "capacity": max(limit * 3, limit),
        "eligible_chunks": len(everything),
        "admitted_chunks": len(admitted),
        "eligible_documents": {result.chunk.document_id for result in everything},
        "admitted_documents": {result.chunk.document_id for result in admitted},
        "refused_documents": set(window.refused_document_ids),
    }


def _truncation(runtime, query, limit):
    truncation = RetrievalTruncation()
    results = search_index(
        query,
        runtime["db_path"],
        filters=SearchFilters(intent="external"),
        limit=limit,
        truncation=truncation,
    )
    return truncation, results


def _slack(runtime, query=ALIAS_QUERY):
    captured = []

    def recording_ask(*args, **kwargs):
        answer = agent_ask(*args, **kwargs)
        captured.append(answer)
        return answer

    reply = handle_slack_event(
        {"text": query, "channel": "C-WINDOW-TRUNC", "user": "U1", "ts": "410.1"},
        config=SlackConfig(allowed_channel_ids=["C-WINDOW-TRUNC"]),
        ask_fn=recording_ask,
        db_path=runtime["db_path"],
        restricted_customers_path=runtime["restricted_path"],
        llm_config_path=Path("missing-llm-config.json"),
        audit_log_path=runtime["audit_path"],
    )
    return reply["text"], captured[0]


def _summary_line(text):
    return next(
        line
        for line in text.splitlines()
        if line.startswith(COMPLETE_TOTAL_PREFIX) or line.startswith(CAPPED_PREFIX)
    )


def _brands(text):
    return [line.strip("`") for line in text.splitlines() if line.startswith("`")]


# --- 1-3. the window's own boundary: below, exactly at, and one past ---------------------------------


@pytest.mark.parametrize("query", [ALIAS_QUERY, ORGANIC_QUERY])
def test_a_result_that_fits_inside_the_window_refuses_nothing(tmp_path, monkeypatch, query):
    """Case A. Fewer candidates than capacity: there is no tail, so there is nothing to report."""
    runtime = _runtime(tmp_path, monkeypatch, [_owner()] + _organic(4))
    shape = _window_shape(runtime, query, limit=4)

    assert shape["query_mode"] == "semantic_question"
    assert shape["eligible_chunks"] == 5 < shape["capacity"] == 12
    assert shape["refused_documents"] == set()

    truncation, _results = _truncation(runtime, query, limit=4)

    assert truncation.retrieval_window_capped is False
    assert truncation.any_stage_capped is False


@pytest.mark.parametrize("query", [ALIAS_QUERY, ORGANIC_QUERY])
def test_a_result_exactly_filling_the_window_is_not_a_truncation(tmp_path, monkeypatch, query):
    """Case B. The boundary a ``len(results) >= limit`` test would get wrong.

    Twelve candidates and a capacity of twelve: every one of them is returned, so declaring this
    query truncated would tell the user their complete result might be missing something.
    """
    runtime = _runtime(tmp_path, monkeypatch, [_owner()] + _organic(11))
    shape = _window_shape(runtime, query, limit=4)

    assert shape["eligible_chunks"] == shape["capacity"] == 12
    assert shape["admitted_chunks"] == 12
    assert shape["refused_documents"] == set()

    truncation, _results = _truncation(runtime, query, limit=4)

    assert truncation.retrieval_window_capped is False


@pytest.mark.parametrize("query", [ALIAS_QUERY, ORGANIC_QUERY])
def test_one_eligible_candidate_past_the_window_is_a_truncation(tmp_path, monkeypatch, query):
    """Case C. The previous fixture plus one more eligible document, and the verdict flips."""
    documents = [_owner()] + _organic(11)
    documents.append(_merchant(90, "Hidden Parent 90", body=UNRELATED_TEXT))
    runtime = _runtime(tmp_path, monkeypatch, documents)
    shape = _window_shape(runtime, query, limit=4)

    assert shape["eligible_chunks"] == 13 > shape["capacity"] == 12
    # The extra document is the lowest-ranked candidate, so it is the one left outside.
    assert shape["refused_documents"] == {"parent-r90"}

    truncation, _results = _truncation(runtime, query, limit=4)

    assert truncation.retrieval_window_capped is True
    assert truncation.any_stage_capped is True


# --- 4. chunks are not documents ----------------------------------------------------------------------


def test_extra_chunks_of_documents_already_in_the_window_are_not_a_truncation(tmp_path, monkeypatch):
    """Case D. The window refuses nine chunks and costs the user nothing.

    Every stage after the window -- the alias merge, ``_unique_document_results`` -- keeps one
    entry per ``document_id``, so a chunk whose document is already inside the window would have
    been collapsed away even if it had been returned. Two twelve-chunk documents cannot both be
    squeezed out of a fifteen-chunk window however they rank, so the document universe is intact
    and counting chunks here would report a truncation on a complete result.
    """
    runtime = _runtime(
        tmp_path,
        monkeypatch,
        [_owner(body=_matching(LONG_DOCUMENT_REPEATS))]
        + _organic(1, repeats=LONG_DOCUMENT_REPEATS),
    )
    shape = _window_shape(runtime, ALIAS_QUERY, limit=SLACK_RETRIEVAL_LIMIT)

    assert runtime["chunk_count"] == 2 * LONG_DOCUMENT_CHUNKS
    assert shape["eligible_chunks"] > shape["capacity"] == SLACK_WINDOW
    assert shape["admitted_chunks"] == SLACK_WINDOW  # the window is full to the brim
    assert shape["admitted_documents"] == shape["eligible_documents"]
    assert shape["refused_documents"] == set()

    text, answer = _slack(runtime)

    assert answer.generated.structured_result.retrieval_truncated is False
    assert _summary_line(text) == "共找到 2 個品牌／夥伴、2 筆內容。"
    assert sorted(_brands(text)) == sorted([ALIAS_OWNER_BRAND, "Organic Parent 1"])
    assert CAPPED_PREFIX not in text


# --- 5. a document the merge never got the chance to refuse -------------------------------------------


def _hidden_document_runtime(tmp_path, monkeypatch):
    """Case D's fixture plus one more parent, which the full window has no room left for."""
    documents = [_owner(body=_matching(LONG_DOCUMENT_REPEATS))]
    documents += _organic(1, repeats=LONG_DOCUMENT_REPEATS)
    documents.append(_merchant(9, "Hidden Parent 9", body=UNRELATED_TEXT))
    return _runtime(tmp_path, monkeypatch, documents)


def test_a_document_hidden_behind_the_window_reaches_the_slack_wording(tmp_path, monkeypatch):
    """Case E. The merge admits everything it sees and the result is still not the universe."""
    runtime = _hidden_document_runtime(tmp_path, monkeypatch)
    shape = _window_shape(runtime, ALIAS_QUERY, limit=SLACK_RETRIEVAL_LIMIT)

    assert shape["query_mode"] == "semantic_question"
    assert shape["refused_documents"] == {"parent-r9"}

    truncation, _results = _truncation(runtime, ALIAS_QUERY, limit=SLACK_RETRIEVAL_LIMIT)
    text, answer = _slack(runtime)

    # The merge was offered two candidates and admitted two: by its own measure nothing was
    # refused, which is exactly why it cannot be the only signal.
    assert truncation.exact_alias_capped is False
    assert truncation.retrieval_window_capped is True
    assert answer.generated.structured_result.retrieval_truncated is True
    assert "Hidden Parent 9" not in text
    assert _summary_line(text) == "目前顯示最多 2 個品牌／夥伴，共 2 筆內容。"
    assert COMPLETE_TOTAL_PREFIX not in text
    # The alias caps did not bind and did not move: this is the window's signal alone.
    assert len(answer.generated.structured_result.matched_entities) < ALIAS_PARENT_CAP


# --- the alias branch re-supplies its owner's records, so refusing them loses nothing ------------------


def test_a_window_that_only_refuses_alias_supplied_documents_is_not_a_truncation(
    tmp_path, monkeypatch
):
    """The refinement that keeps Case C from over-reporting on the alias path.

    ``alias_results_for_parent_ids`` fetches the alias owner's records by parent id, outside this
    window entirely. A window that refuses only those has cost the merge nothing: it is offered
    every candidate either way, and the result really is the whole universe.
    """
    documents = [_owner(body=UNRELATED_TEXT)] + _organic(3, repeats=4)
    runtime = _runtime(tmp_path, monkeypatch, documents)
    shape = _window_shape(runtime, ALIAS_QUERY, limit=1)

    # The owner's own document is the one the window drops, and it is the only one.
    assert shape["refused_documents"] == {"parent-r32"}

    truncation, results = _truncation(runtime, ALIAS_QUERY, limit=1)

    assert truncation.retrieval_window_capped is False
    assert truncation.exact_alias_capped is False
    assert truncation.any_stage_capped is False
    # And the owner is in the result regardless, which is why nothing was lost.
    assert "parent-r32" in {result.chunk.document_id for result in results}


# --- 6. both stages cut, and neither signal erases the other ------------------------------------------


def test_window_and_merge_truncation_together_stay_truncated(tmp_path, monkeypatch):
    """Case G. Sixteen matching parents overflow the window; the five the merge admits overflow it too."""
    documents = [_owner()] + _organic(16)
    documents.append(_merchant(99, "Hidden Parent 99", body=UNRELATED_TEXT))
    runtime = _runtime(tmp_path, monkeypatch, documents)
    shape = _window_shape(runtime, ALIAS_QUERY, limit=SLACK_RETRIEVAL_LIMIT)

    assert shape["eligible_chunks"] == 18 > shape["capacity"] == SLACK_WINDOW
    assert "parent-r99" in shape["refused_documents"]

    truncation, _results = _truncation(runtime, ALIAS_QUERY, limit=SLACK_RETRIEVAL_LIMIT)
    text, answer = _slack(runtime)

    assert truncation.exact_alias_capped is True
    assert truncation.retrieval_window_capped is True
    assert truncation.any_stage_capped is True
    assert answer.generated.structured_result.retrieval_truncated is True
    assert _summary_line(text).startswith(CAPPED_PREFIX)
    assert COMPLETE_TOTAL_PREFIX not in text


def test_neither_signal_can_overwrite_the_other():
    """The two stages write different fields, so the order they run in cannot matter."""
    assert RetrievalTruncation().any_stage_capped is False
    assert RetrievalTruncation(exact_alias_capped=True).any_stage_capped is True
    assert RetrievalTruncation(retrieval_window_capped=True).any_stage_capped is True
    assert RetrievalTruncation(
        exact_alias_capped=True, retrieval_window_capped=True
    ).any_stage_capped is True


# --- 7. the exact-alias merge signal is exactly what 144aa30 made it ----------------------------------


@pytest.mark.parametrize(
    "organic_count, expected_entities, expected_merge_capped",
    [(2, 3, False), (4, ALIAS_PARENT_CAP, False), (8, ALIAS_PARENT_CAP, True)],
)
def test_the_exact_alias_merge_signal_below_at_and_over_its_cap(
    tmp_path, monkeypatch, organic_count, expected_entities, expected_merge_capped
):
    """Below the cap, exactly at it, and past it -- with a window far too wide to interfere."""
    runtime = _runtime(tmp_path, monkeypatch, [_owner()] + _organic(organic_count))
    shape = _window_shape(runtime, ALIAS_QUERY, limit=60)

    assert shape["refused_documents"] == set()

    truncation, results = _truncation(runtime, ALIAS_QUERY, limit=60)

    assert truncation.exact_alias_capped is expected_merge_capped
    assert truncation.retrieval_window_capped is False
    assert len({result.chunk.document_id for result in results}) == expected_entities
    assert len(results) <= ALIAS_ASSET_CAP


# --- 8. the structured_lookup path cannot reach the window at all -------------------------------------


def test_structured_lookup_widens_the_window_past_every_candidate(tmp_path, monkeypatch):
    """Case H. ask_index raises the limit to the whole chunk count, so the window never binds.

    The retriever is then asked for three times every chunk in the index, and no query can produce
    more candidates than there are chunks. The fixture is deliberately the long-document shape that
    truncates the semantic path, plus the low-ranked parent that shape hides.
    """
    documents = [_owner(body=_matching(LONG_DOCUMENT_REPEATS), tags=[STRUCTURED_TAG])]
    documents += _organic(1, repeats=LONG_DOCUMENT_REPEATS, tags=[STRUCTURED_TAG])
    documents.append(_merchant(9, "Hidden Parent 9", body=UNRELATED_TEXT, tags=[STRUCTURED_TAG]))
    runtime = _runtime(tmp_path, monkeypatch, documents)

    assert runtime["chunk_count"] > SLACK_WINDOW
    assert _plan(runtime, STRUCTURED_TAG).query_mode == "structured_lookup"

    truncation = RetrievalTruncation()
    search_index(
        STRUCTURED_TAG,
        runtime["db_path"],
        filters=SearchFilters(intent="external"),
        limit=max(SLACK_RETRIEVAL_LIMIT, runtime["chunk_count"]),
        truncation=truncation,
    )
    text, answer = _slack(runtime, query=STRUCTURED_TAG)

    assert truncation.retrieval_window_capped is False
    assert answer.generated.structured_result.retrieval_truncated is False
    assert _summary_line(text) == "共找到 3 個品牌／夥伴、3 筆內容。"
    # The parent the semantic path hides behind its window is right there in the structured one.
    assert "Hidden Parent 9" in text
    assert CAPPED_PREFIX not in text


# --- 9. the non-alias path is untouched ---------------------------------------------------------------


def test_a_non_alias_query_never_reports_the_alias_signal(tmp_path, monkeypatch):
    """Case I. The organic branch sets only the window field; the merge field stays where it was."""
    runtime = _runtime(tmp_path, monkeypatch, [_owner()] + _organic(12))
    truncation, _results = _truncation(runtime, ORGANIC_QUERY, limit=4)

    assert _plan(runtime, ORGANIC_QUERY).query_mode == "semantic_question"
    assert truncation.exact_alias_capped is False
    assert truncation.retrieval_window_capped is True


def test_a_non_alias_structured_search_still_reads_as_a_complete_total(tmp_path, monkeypatch):
    """The Slack wording for a query with no alias behind it, end to end and unchanged."""
    runtime = _runtime(tmp_path, monkeypatch, _organic(6, tags=[STRUCTURED_TAG]))

    assert _plan(runtime, STRUCTURED_TAG).query_mode == "structured_lookup"

    text, answer = _slack(runtime, query=STRUCTURED_TAG)

    assert answer.generated.structured_result.retrieval_truncated is False
    assert _summary_line(text) == "共找到 6 個品牌／夥伴、6 筆內容。"
    assert CAPPED_PREFIX not in text


# --- 10. the returned result set is byte-for-byte what it was -----------------------------------------


@pytest.mark.parametrize("query", [ALIAS_QUERY, ORGANIC_QUERY])
@pytest.mark.parametrize("limit", [1, 4, 60])
def test_the_diagnostic_never_changes_what_retrieval_returns(tmp_path, monkeypatch, query, limit):
    """Membership and ordering, with and without every channel this change added."""
    runtime = _runtime(tmp_path, monkeypatch, [_owner()] + _organic(12, repeats=30))

    without = search_index(
        query, runtime["db_path"], filters=SearchFilters(intent="external"), limit=limit
    )
    with_channel = search_index(
        query,
        runtime["db_path"],
        filters=SearchFilters(intent="external"),
        limit=limit,
        truncation=RetrievalTruncation(),
    )

    assert [result.chunk.id for result in without] == [
        result.chunk.id for result in with_channel
    ]
    assert [result.chunk.document_id for result in without] == [
        result.chunk.document_id for result in with_channel
    ]


@pytest.mark.parametrize("limit", [1, 4, 12, 60])
def test_the_window_slice_is_still_the_head_of_the_full_ranking(tmp_path, monkeypatch, limit):
    """The window is a prefix of the complete ranking, and asking about it does not move it."""
    runtime = _runtime(tmp_path, monkeypatch, [_owner()] + _organic(12, repeats=30))
    plan = _plan(runtime, ALIAS_QUERY)
    filters = apply_intent_gating(SearchFilters(intent="external"))
    retriever = SQLiteRetriever(runtime["db_path"])

    everything = retriever.search(ALIAS_QUERY, filters=filters, limit=10**6, query_plan=plan)
    plain = retriever.search(ALIAS_QUERY, filters=filters, limit=limit, query_plan=plan)
    window = RetrievalWindow()
    observed = retriever.search(
        ALIAS_QUERY, filters=filters, limit=limit, query_plan=plan, window=window
    )

    assert [result.chunk.id for result in plain] == [result.chunk.id for result in observed]
    assert [result.chunk.id for result in observed] == [
        result.chunk.id for result in everything[:limit]
    ]
    assert RetrievalWindow().refused_document_ids == set()
    assert window.refused_document_ids <= {
        result.chunk.document_id for result in everything[limit:]
    }


def test_the_window_channel_is_optional(tmp_path, monkeypatch):
    """Omitted, the retriever behaves exactly as it did before the channel existed."""
    runtime = _runtime(tmp_path, monkeypatch, [_owner()] + _organic(12, repeats=30))
    filters = apply_intent_gating(SearchFilters(intent="external"))
    retriever = SQLiteRetriever(runtime["db_path"])

    assert [
        result.chunk.id for result in retriever.search(ALIAS_QUERY, filters=filters, limit=7)
    ] == [
        result.chunk.id
        for result in retriever.search(
            ALIAS_QUERY, filters=filters, limit=7, query_plan=None, window=None
        )
    ]


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
