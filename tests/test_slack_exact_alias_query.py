from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata
from marketing_knowledge_agent.pipeline import agent_ask
from marketing_knowledge_agent.search_aliases import (
    EXPECTED_ALIAS_AUTHORITY,
    EXPECTED_ALIAS_BINDING,
)
from marketing_knowledge_agent.slack_interface import (
    SLACK_NO_RESULTS_MESSAGE,
    SlackConfig,
    handle_slack_event,
)


R32_ID = "商家夥伴案例資料庫:r32"


@pytest.fixture
def slack_alias_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    projection_path = tmp_path / ".mka/search_alias_projection.json"
    projection_path.parent.mkdir(parents=True)
    _write_projection(projection_path)

    restricted_path = tmp_path / "restricted.json"
    restricted_path.write_text("[]\n", encoding="utf-8")
    documents = [
        _merchant(
            32,
            "聊心茶室",
            "r32 governed Parent content",
            article_title="聊心茶室 Article",
            video_title="聊心茶室 Video",
            podcast_title="聊心茶室 Podcast",
        ),
        *[
            _merchant(
                row,
                f"Organic Parent {row}",
                f"SHOPLINE Payments organic result {row}",
                article_title=f"Organic Article {row}",
                content_tags=["SHOPLINE Payments"],
            )
            for row in range(1, 7)
        ],
        _merchant(
            80,
            "Restricted Fixture Brand",
            "generic governance fixture match restricted marker",
            article_title="Restricted Fixture Article",
        ),
        _merchant(
            81,
            "Verbal Briefing Fixture",
            "累計總 GMV verbal-only marker",
            article_title="累計總 GMV Verbal Briefing",
            can_quote_externally=False,
            allowed_exposure_channels=["verbal_briefing"],
        ),
        _merchant(
            82,
            "Public Governance Fixture",
            "generic governance fixture match public marker",
            article_title="Public Fixture Article",
        ),
    ]
    db_path = tmp_path / "content_index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return {
        "db_path": db_path,
        "restricted_path": restricted_path,
        "audit_path": tmp_path / "slack_audit.csv",
    }


def test_app_mention_plus_pure_slp_resolves_r32(slack_alias_runtime):
    reply, answer = _slack_query(slack_alias_runtime, "<@U0BOT123> SLP")

    assert reply["text"] != SLACK_NO_RESULTS_MESSAGE
    _assert_r32_assets(reply["text"])
    assert _parent_ids(answer)[0] == R32_ID


@pytest.mark.parametrize("query", ["SLP", "slp", "SlP", "  SLP  "])
def test_pure_exact_slp_variants_resolve_r32(slack_alias_runtime, query):
    reply, answer = _slack_query(slack_alias_runtime, query)

    assert reply["text"] != SLACK_NO_RESULTS_MESSAGE
    _assert_r32_assets(reply["text"])
    assert _parent_ids(answer)[0] == R32_ID


def test_app_mention_markup_is_fully_removed_before_alias_routing(slack_alias_runtime):
    _slack_query(slack_alias_runtime, "  <@U0BOT123>   SLP  ")

    rows = slack_alias_runtime["audit_path"].read_text(encoding="utf-8").splitlines()
    assert rows[-1].endswith(",SLP")
    assert "<@U0BOT123>" not in rows[-1]


def test_alias_only_candidate_survives_empty_organic_results(
    slack_alias_runtime, monkeypatch
):
    monkeypatch.setattr(
        "marketing_knowledge_agent.retrieval.SQLiteRetriever.search",
        lambda *args, **kwargs: [],
    )
    _, answer = _slack_query(slack_alias_runtime, "SLP")

    assert answer.generated.structured_result is not None
    assert answer.generated.structured_result.total_entities == 1
    assert answer.generated.structured_result.total_assets == 3
    assert len(answer.citations) == 3


def test_alias_only_candidate_reaches_structured_slack_renderer(slack_alias_runtime):
    reply, answer = _slack_query(slack_alias_runtime, "SLP")

    assert answer.generated.structured_result is not None
    assert "聊心茶室" in reply["text"]
    # OLD: the r32 source line proved the alias owner reached the renderer. That line left the
    # Slack surface in v2, so the same fact is read where it still lives -- the result payload.
    assert "商家夥伴案例資料庫" not in reply["text"]
    assert R32_ID in _parent_ids(answer)


def test_shopline_payments_merge_ranking_and_caps_remain_stable(slack_alias_runtime):
    first_reply, first_answer = _slack_query(slack_alias_runtime, "SHOPLINE Payments")
    second_reply, second_answer = _slack_query(slack_alias_runtime, "SHOPLINE Payments")

    first_ids = _parent_ids(first_answer)
    assert first_ids == _parent_ids(second_answer)
    assert first_ids[0] == R32_ID
    assert len(first_ids) == len(set(first_ids)) == 5
    assert first_answer.generated.structured_result.total_assets <= 10
    assert "Organic Parent" in first_reply["text"]
    assert first_reply["text"] == second_reply["text"]


@pytest.mark.parametrize("query", ["SL", "SLPP", "SLP123"])
def test_negative_aliases_do_not_resolve_r32(slack_alias_runtime, query):
    _, answer = _slack_query(slack_alias_runtime, query)

    assert R32_ID not in _parent_ids(answer)


def test_verbal_briefing_only_record_is_not_exposed_by_slack(slack_alias_runtime):
    reply, answer = _slack_query(slack_alias_runtime, "累計總 GMV")

    assert "Verbal Briefing Fixture" not in reply["text"]
    assert "累計總 GMV Verbal Briefing" not in reply["text"]
    assert all("Verbal Briefing" not in citation.title for citation in answer.citations)


def test_generic_fallback_filters_restricted_body_and_citations(slack_alias_runtime):
    slack_alias_runtime["restricted_path"].write_text(
        json.dumps([{"brand_name": "Restricted Fixture Brand"}], ensure_ascii=False),
        encoding="utf-8",
    )

    reply, answer = _slack_query(slack_alias_runtime, "generic governance fixture match")

    assert "Restricted Fixture" not in reply["text"]
    assert "Restricted Fixture" not in answer.answer
    assert all("Restricted Fixture" not in citation.title for citation in answer.citations)
    assert "public marker" in answer.answer


def test_agentic_fallback_filters_restricted_body_and_citations(slack_alias_runtime):
    slack_alias_runtime["restricted_path"].write_text(
        json.dumps([{"brand_name": "Restricted Fixture Brand"}], ensure_ascii=False),
        encoding="utf-8",
    )

    reply, answer = _slack_query(
        slack_alias_runtime, "整理 generic governance fixture match"
    )

    assert answer.trace.mode == "agentic_lite"
    assert "Restricted Fixture" not in reply["text"]
    assert "Restricted Fixture" not in answer.answer
    assert all("Restricted Fixture" not in citation.title for citation in answer.citations)


def test_alias_only_query_still_applies_governance_filter(slack_alias_runtime):
    slack_alias_runtime["restricted_path"].write_text(
        json.dumps([{"brand_name": "聊心茶室"}], ensure_ascii=False),
        encoding="utf-8",
    )

    reply, answer = _slack_query(slack_alias_runtime, "SLP")

    assert "聊心茶室" not in reply["text"]
    assert R32_ID not in _parent_ids(answer)
    assert all(citation.source_row != 32 for citation in answer.citations)


@pytest.mark.parametrize("projection_state", ["missing", "malformed"])
def test_invalid_or_missing_alias_projection_preserves_organic_search(
    slack_alias_runtime, projection_state
):
    projection_path = Path(".mka/search_alias_projection.json")
    if projection_state == "missing":
        projection_path.unlink()
    else:
        projection_path.write_text("{", encoding="utf-8")

    reply, answer = _slack_query(
        slack_alias_runtime, "generic governance fixture match"
    )

    assert reply["text"] != SLACK_NO_RESULTS_MESSAGE
    assert answer.citations
    assert answer.generated.structured_result is None


def _slack_query(runtime, query):
    captured = []

    def recording_ask(*args, **kwargs):
        answer = agent_ask(*args, **kwargs)
        captured.append(answer)
        return answer

    reply = handle_slack_event(
        {
            "text": query,
            "channel": "C-ALIAS-TEST",
            "user": "U-ALIAS-TEST",
            "ts": "100.1",
        },
        config=SlackConfig(allowed_channel_ids=["C-ALIAS-TEST"]),
        ask_fn=recording_ask,
        db_path=runtime["db_path"],
        restricted_customers_path=runtime["restricted_path"],
        llm_config_path=Path("missing-llm-config.json"),
        audit_log_path=runtime["audit_path"],
    )
    return reply, captured[0]


def _parent_ids(answer):
    identities = []
    for citation in answer.citations:
        if citation.source_sheet != "商家夥伴案例資料庫" or citation.source_row is None:
            continue
        identity = f"商家夥伴案例資料庫:r{citation.source_row}"
        if identity not in identities:
            identities.append(identity)
    return identities


def _assert_r32_assets(text):
    assert "聊心茶室" in text
    assert "聊心茶室 Article" in text
    assert "聊心茶室 Video" in text
    assert "聊心茶室 Podcast" in text


def _merchant(
    row,
    brand_name,
    content,
    *,
    article_title,
    video_title=None,
    podcast_title=None,
    can_quote_externally=True,
    allowed_exposure_channels=None,
    content_tags=None,
):
    metadata = DocumentMetadata(
        title=brand_name,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 7, 1),
        source_path=f"record-r{row}-{brand_name}.md",
        source_sheet="商家夥伴案例資料庫",
        source_row=row,
        brand_name=brand_name,
        article_title=article_title,
        video_title=video_title,
        podcast_title=podcast_title,
        data_classification="public",
        can_quote_externally=can_quote_externally,
        allowed_exposure_channels=allowed_exposure_channels or [],
        content_tags=content_tags or [],
        can_enter_content_index=True,
    )
    return Document(id=f"parent-r{row}", metadata=metadata, content=content)


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
                "raw_alias": "SHOPLINE Payments",
                "normalized_alias": "shopline payments",
                "parent_record_id": R32_ID,
                "active": True,
                "reviewer": "Admin",
                "reviewed_at": "2026-07-18T00:33:08+08:00",
                "provenance": "admin_resolution",
                "authority_reference": "event-shopline-payments",
            },
            {
                "raw_alias": "SLP",
                "normalized_alias": "slp",
                "parent_record_id": R32_ID,
                "active": True,
                "reviewer": "Admin",
                "reviewed_at": "2026-07-18T00:33:08+08:00",
                "provenance": "admin_resolution",
                "authority_reference": "event-slp",
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
        json.dumps(
            scope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
