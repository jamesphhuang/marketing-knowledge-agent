from __future__ import annotations

from datetime import date

import pytest

from marketing_knowledge_agent.models import (
    Citation,
    GeneratedAnswer,
    StructuredAsset,
    StructuredEntity,
    StructuredRetrievalResult,
)
from marketing_knowledge_agent.slack_interface import format_slack_reply
from marketing_knowledge_agent.slack_presentation import (
    UrlCanonicalizationPolicy,
    canonicalize_url,
)


def test_plain_query_conditions_use_one_inline_code_tag_and_escape_mrkdwn():
    answer = _answer(
        "SLP `<> &",
        [_entity("Brand `<> &", [_asset("article", "Story `<> &", row=32)])],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "已套用搜尋條件：`SLP \\`&lt;&gt; &amp;`" in text
    assert "`Brand \\`&lt;&gt; &amp;`" in text
    assert "📚 來源" not in text


def test_structured_conditions_render_as_separate_inline_code_tags():
    answer = _answer(
        "SLP 文章 Podcast 2024",
        [_entity("Brand", [_asset("article", "Story", row=32)])],
        hard_filters=[
            {"field": "entity_name", "value": "SLP", "operator": "exact", "output_label": "關鍵字"},
            {"field": "asset_type", "value": "article", "operator": "exact"},
            {"field": "asset_type", "value": "podcast", "operator": "exact"},
            {"field": "interview_year", "value": 2024, "operator": "eq"},
        ],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "已套用搜尋條件：`關鍵字：SLP`｜`內容類型：文章、Podcast`｜`採訪年份：2024`" in text


def test_brand_metadata_and_assets_use_requested_mrkdwn_shape():
    answer = _answer(
        "SLP",
        [
            _entity(
                "聊心茶室（SLP 用戶）",
                [
                    _asset("article", "完整標題", row=32, url="https://example.com/a"),
                    _asset("video", "影片標題", row=32),
                ],
                handle=None,
                lv1="其他",
                lv2="其他",
                interview_year=2024,
            )
        ],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "`聊心茶室（SLP 用戶）`" in text
    assert "_Handle：資料未提供_" in text
    assert "_Sales Category LV1：其他_" in text
    assert "> • *文章 [1]*" in text
    assert "> *標題：*完整標題" in text
    assert "> *連結：*<https://example.com/a|開啟連結>" in text
    assert "> *採訪年份：*2024" in text
    assert "> *資料來源：*商家夥伴案例資料庫 r32" in text
    assert "📚 來源" not in text


def test_brand_blockquote_and_global_numbering_are_stable():
    answer = _answer(
        "query",
        [
            _entity("Brand A", [_asset("article", "A1", row=1), _asset("video", "A2", row=1)]),
            _entity("Brand B", [_asset("podcast", "B1", row=2)]),
        ],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)
    lines = text.splitlines()

    assert "> • *文章 [1]*" in lines
    assert ">" in lines
    assert "> • *影片 [2]*" in lines
    assert "> • *Podcast [3]*" in lines
    assert text.index("`Brand A`") < text.index("`Brand B`")
    assert "\n\n`Brand B`" in text


def test_content_type_order_is_fixed_without_changing_brand_order():
    answer = _answer(
        "query",
        [
            _entity("Brand A", [_asset("podcast", "A Podcast", row=1), _asset("article", "A Article", row=1)]),
            _entity("Brand B", [_asset("news", "B News", row=2)]),
        ],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert text.index("文章 [1]") < text.index("Podcast [2]")
    assert text.index("`Brand A`") < text.index("`Brand B`")


def test_no_result_message_shows_applied_conditions_and_no_governance_details():
    answer = _answer(
        "SLP 2022",
        [],
        hard_filters=[
            {"field": "entity_name", "value": "SLP", "operator": "exact", "output_label": "關鍵字"},
            {"field": "interview_year", "value": 2022, "operator": "eq"},
        ],
        abstained=True,
        abstain_reason="no_exact_match",
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "已套用搜尋條件：`關鍵字：SLP`｜`採訪年份：2022`" in text
    assert "找不到符合條件的品牌或內容。" in text
    assert "你可以嘗試：" in text
    assert "restricted" not in text.lower()
    assert "pending" not in text.lower()
    assert "verbal_briefing" not in text


def test_deduplication_does_not_merge_different_asset_types_and_fills_missing_fields():
    first = _entity(
        "Brand A",
        [_asset("article", "Story", row=1, url=None, publish_date=None)],
        handle=None,
        lv1=None,
    )
    second = _entity(
        "Brand A",
        [
            _asset("article", "Story", row=1, url="https://example.com/story", publish_date="2024-01-01"),
            _asset("video", "Story", row=1, url="https://example.com/story"),
        ],
        handle="brand-a",
        lv1="居家生活",
    )

    text = format_slack_reply(
        _answer("query", [first, second]),
        max_answer_chars=20_000,
    )

    assert text.count("文章 [1]") == 1
    assert "影片 [2]" in text
    assert "_Handle：brand-a_" in text
    assert "_Sales Category LV1：居家生活_" in text
    assert "https://example.com/story" in text


def test_key_conflict_silently_removes_content_and_description_conflict_is_visible():
    conflict = _answer(
        "query",
        [
            _entity("Brand A", [_asset("article", "Title A", row=1)]),
            _entity("Brand A", [_asset("article", "Title B", row=1)]),
        ],
    )
    description_conflict = _answer(
        "query",
        [
            _entity("Brand B", [_asset("article", "Title", row=2, publish_date="2024-01-01")], lv1="A"),
            _entity("Brand B", [_asset("article", "Title", row=2, publish_date="2024-01-01")], lv1="B"),
        ],
    )

    conflict_text = format_slack_reply(conflict, max_answer_chars=20_000)
    description_text = format_slack_reply(description_conflict, max_answer_chars=20_000)

    assert "Title A" not in conflict_text and "Title B" not in conflict_text
    assert "資料不一致" in description_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://EXAMPLE.com/article/?utm_source=x&b=2&a=1#part", "https://example.com/article/?a=1&b=2#part"),
        ("https://example.com/article", "https://example.com/article"),
        ("https://example.com/article/", "https://example.com/article/"),
    ],
)
def test_url_display_canonicalization_removes_tracking_and_preserves_fragment(raw, expected):
    assert canonicalize_url(raw).display == expected


def test_url_identity_ignores_fragment_and_trailing_slash_but_keeps_unknown_parameters():
    first = canonicalize_url("https://example.com/article/?b=2&utm_source=x&a=1#one")
    second = canonicalize_url("https://example.com/article?a=1&b=2#two")

    assert first.identity == second.identity
    assert "b=2" in first.display and "a=1" in first.display


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "file:///internal/content.md",
        "data:text/plain,unsafe",
        "https://user:password@example.com/path",
        "https://example.com:invalid/path",
        "http://[invalid",
    ],
)
def test_unsafe_or_malformed_urls_are_not_renderable(value):
    assert canonicalize_url(value) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a&gt;&lt;https://evil.example&gt;",
        "https://example.com/a&#62;&#60;https://evil.example",
        "https://example.com/a&#x3e;&#x3c;https://evil.example",
        "https://example.com/a&amp;foo=bar",
        "https://example.com/a\\b",
        "https://example.com/a&LT;b",
        "https://example.com/a&#X3E;b",
        "https://example.com/a&verbar;b",
    ],
)
def test_entity_or_escape_breakout_url_is_never_rendered_as_a_clickable_link(url):
    answer = _answer("query", [_entity("Brand", [_asset("article", "Story", row=32, url=url)])])

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "> *連結：*資料未提供" in text
    assert "開啟連結" not in text
    assert "evil.example" not in text


def test_legitimate_query_separators_render_as_one_escaped_clickable_link():
    answer = _answer(
        "query",
        [
            _asset_entity(
                "Brand",
                "https://www.youtube.com/watch?v=X&list=Y&index=1",
            )
        ],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "> *連結：*<https://www.youtube.com/watch?index=1&amp;list=Y&amp;v=X|開啟連結>" in text
    assert text.count("|開啟連結>") == 1
    assert text.count("<https://") == 1
    # Nothing outside the single link construct may carry a raw mrkdwn delimiter.
    assert "&list=" not in text
    assert "&index=" not in text


def test_rendered_link_construct_never_contains_a_bare_ampersand():
    answer = _answer(
        "query",
        [_asset_entity("Brand", "https://example.com/a?x=1&y=2")],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)
    link = text.split("> *連結：*", 1)[1].splitlines()[0]

    assert link == "<https://example.com/a?x=1&amp;y=2|開啟連結>"
    assert link.count("<") == 1 and link.count(">") == 1


def test_same_canonical_url_from_multiple_allowed_rows_lists_sorted_sources_once():
    answer = _answer(
        "query",
        [
            _entity("Brand", [_asset("article", "Story", row=87, url="https://example.com/story")]),
            _entity("Brand", [_asset("article", "Story", row=32, url="https://example.com/story#fragment")]),
        ],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert text.count("文章 [1]") == 1
    assert "商家夥伴案例資料庫 r32、商家夥伴案例資料庫 r87" in text


def test_title_url_and_link_url_conflict_does_not_guess_a_link():
    answer = _answer(
        "query",
        [_entity("Brand", [_asset("article", "https://example.com/title", row=1, url="https://example.com/link")])],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "標題：*資料不一致" in text
    assert "連結：*資料未提供" in text


def test_explicit_domain_policy_controls_www_https_and_path_case_merges():
    policy = UrlCanonicalizationPolicy(
        www_equivalent_hosts=frozenset({"example.com"}),
        https_equivalent_hosts=frozenset({"example.com"}),
        path_case_insensitive_hosts=frozenset({"example.com"}),
    )
    approved_http = canonicalize_url("http://www.EXAMPLE.com/Article/", policy)
    approved_https = canonicalize_url("https://example.com/article", policy)
    unknown_http = canonicalize_url("http://www.other.example/Article")

    assert approved_http.identity == approved_https.identity
    assert approved_http.display.startswith("https://example.com/article")
    assert unknown_http.identity != canonicalize_url("https://other.example/article").identity


def test_title_url_never_becomes_an_asset_link_without_approved_url_evidence():
    answer = _answer(
        "query",
        [
            _entity("Brand A", [_asset("article", "https://example.com/a", row=1, url=None)]),
            _entity("Brand B", [_asset("article", "Normal title", row=2, url="https://example.com/b")]),
        ],
    )
    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "> *標題：*資料未提供" in text
    assert "> *連結：*資料未提供" in text
    assert "<https://example.com/a|開啟連結>" not in text
    assert "Brand B" in text


def test_deterministic_output_and_missing_values_are_preserved():
    answer = _answer(
        "query",
        [_entity("Brand", [_asset("article", "Title", row=1, url=None, publish_date=None)], handle=None, lv1=None, lv2=None, interview_year=None)],
    )

    first = format_slack_reply(answer, max_answer_chars=20_000)
    second = format_slack_reply(answer, max_answer_chars=20_000)

    assert first == second
    assert "資料未提供" in first


def _answer(
    question,
    entities,
    *,
    hard_filters=None,
    abstained=False,
    abstain_reason=None,
):
    plan = {
        "raw_query": question,
        "normalized_query": question.strip().casefold(),
        "hard_filters": hard_filters or [],
        "supported_constraints": hard_filters or [],
        "unsupported_constraints": [],
        "operator": "AND",
        "abstain_reason": abstain_reason if abstained else None,
    }
    citations = []
    for entity in entities:
        for index, asset in enumerate(entity.assets, start=len(citations) + 1):
            citations.append(
                Citation(
                    label=f"[{index}]",
                    title=asset.title,
                    source_path=f"{asset.source_sheet}:{asset.source_row}",
                    chunk_id=f"chunk-{asset.source_row}:{asset.asset_type}",
                    status="published",
                    source_type="database",
                    record_type="merchant_case",
                    data_classification="public",
                    can_quote_externally=True,
                    publish_date=asset.published_at or "2024-01-01",
                    source_sheet=asset.source_sheet,
                    source_row=asset.source_row,
                    canonical_url=asset.url,
                    allowed_exposure_channels=[],
                    freshness_note="fresh",
                )
            )
    structured = StructuredRetrievalResult(
        query_plan=plan,
        matched_entities=entities,
        total_entities=len(entities),
        total_assets=sum(len(entity.assets) for entity in entities),
        abstained=abstained,
        abstain_reason=plan["abstain_reason"],
    )
    return GeneratedAnswer(
        question=question,
        answer="unused",
        citations=citations,
        warnings=["restricted denylist removed 1"],
        structured_result=structured,
        governance_checked=True,
    )


def _entity(
    name,
    assets,
    *,
    handle="handle",
    lv1="Category 1",
    lv2="Category 2",
    interview_year=2024,
):
    return StructuredEntity(
        entity_type="merchant",
        entity_name=name,
        merchant_handle=handle,
        sales_category_lv1=lv1,
        sales_category_lv2=lv2,
        interview_year=interview_year,
        assets=assets,
    )


def _asset_entity(name, url):
    return _entity(name, [_asset("article", "Story", row=32, url=url)])


def _asset(asset_type, title, *, row, url="https://example.com/content", publish_date="2024-01-01"):
    return StructuredAsset(
        asset_type=asset_type,
        title=title,
        url=url,
        published_at=publish_date,
        publication_status=None,
        external_usage_status="可對外引用",
        source_record_id=f"商家夥伴案例資料庫:r{row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=row,
        citation_label="",
    )
