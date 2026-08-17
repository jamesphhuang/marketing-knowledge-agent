from __future__ import annotations

import re
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

    # Slack has no backslash escape, so a "\`" would reach the user as a visible backslash while
    # still closing the code span the formatter opened. The backtick is replaced instead.
    assert "已套用搜尋條件：`SLP ˋ&lt;&gt; &amp;`" in text
    assert "`Brand ˋ&lt;&gt; &amp;`" in text
    assert "\\`" not in text
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
    assert "> 標題：完整標題" in text
    assert "> 連結：<https://example.com/a|開啟連結>" in text
    assert "> 採訪年份：2024" in text
    assert "> 資料來源：商家夥伴案例資料庫 r32" in text
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

    assert "> 連結：資料未提供" in text
    assert "開啟連結" not in text
    assert "evil.example" not in text


@pytest.mark.parametrize(
    "url",
    [
        # TAB, LF and CR are the three bytes urlsplit() deletes before parsing, so a URL checked
        # only in its canonical form would come back "clean" and be rendered as a rewritten link.
        "https://example.com/a\tb",
        "https://example.com/a\nb",
        "https://example.com/a\rb",
        "https://example.com/p?x=a\tb",
        "https://example.com/p?x=a\nb",
        "https://example.com/p?x=a\rb",
        "https://example.com/p#a\tb",
        "https://example.com/p#a\nb",
        "https://example.com/p#a\rb",
        # The remaining C0 controls and DEL must stay rejected alongside them.
        "https://example.com/a\x00b",
        "https://example.com/a\x1fb",
        "https://example.com/a\x7fb",
        "https://example.com/p?x=a\x00b",
        "https://example.com/p#a\x7fb",
    ],
)
def test_raw_control_character_url_is_never_rendered_as_a_clickable_link(url):
    answer = _answer("query", [_entity("Brand", [_asset("article", "Story", row=32, url=url)])])

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "> 連結：資料未提供" in text
    assert "開啟連結" not in text


@pytest.mark.parametrize("control", ["\t", "\n", "\r"])
def test_canonicalization_erasing_a_control_character_is_not_acceptance(control):
    """The raw value decides safety; normalization must never launder it into a link."""
    raw = f"https://example.com/a{control}b"
    erased = "https://example.com/ab"

    # The erased twin is a perfectly renderable URL, so the rejection below is the raw check
    # doing the work rather than an incidental parse failure.
    assert canonicalize_url(raw).display == erased
    control_free = format_slack_reply(
        _answer("query", [_asset_entity("Brand", erased)]), max_answer_chars=20_000
    )
    assert f"> 連結：<{erased}|開啟連結>" in control_free

    text = format_slack_reply(
        _answer("query", [_asset_entity("Brand", raw)]), max_answer_chars=20_000
    )

    assert "> 連結：資料未提供" in text
    assert erased not in text


@pytest.mark.parametrize(
    "url",
    [
        "https://blog.shopline.tw/merchant-showcase-shanfeng/",
        "https://www.youtube.com/watch?v=WIMy_AFA0pE",
    ],
)
def test_approved_asset_urls_still_render_as_one_clickable_link(url):
    answer = _answer("query", [_asset_entity("三風製麵", url)])

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert f"> 連結：<{url}|開啟連結>" in text
    assert text.count("|開啟連結>") == 1


def test_surrounding_whitespace_still_resolves_to_one_clickable_link():
    """Padding is stripped as before; only characters inside the URL fail closed."""
    answer = _answer(
        "query",
        [_asset_entity("Brand", "  https://blog.shopline.tw/merchant-showcase-shanfeng/  ")],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert (
        "> 連結：<https://blog.shopline.tw/merchant-showcase-shanfeng/|開啟連結>" in text
    )
    assert text.count("|開啟連結>") == 1


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

    assert "> 連結：<https://www.youtube.com/watch?index=1&amp;list=Y&amp;v=X|開啟連結>" in text
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
    link = text.split("> 連結：", 1)[1].splitlines()[0]

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

    assert "標題：資料不一致" in text
    assert "連結：資料未提供" in text


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

    assert "> 標題：資料未提供" in text
    assert "> 連結：資料未提供" in text
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


# --- Slack mrkdwn label rendering ---------------------------------------------------------------
#
# Slack closes a bold run only when the character after the trailing "*" is a delimiter boundary.
# A label written as "*標題：*" immediately in front of its value therefore reached the user
# verbatim whenever the value started with a word character -- which CJK text and digits both are.
# Only the asset header, whose closing "*" ends the line, ever rendered. These tests pin the shape
# of every standard label and the separation between formatter-owned markup and dynamic text.

ASSET_FIELD_LABELS = ("標題", "連結", "上線日期", "採訪年份", "狀態", "對外引用", "資料來源")

# A bold run whose closing "*" is followed by something other than whitespace or end of line.
# Slack's exact boundary rule is undocumented, so those two are the only ones treated as certain.
UNCLOSED_BOLD_RUN = re.compile(r"\*[^*\n]+\*(?!\s|$)", re.MULTILINE)


def _shanfeng_answer():
    return _answer(
        "三風製麵",
        [
            _entity(
                "三風製麵",
                [
                    _asset(
                        "article",
                        "傳統製麵廠的數位轉型之路！",
                        row=8,
                        url="https://blog.shopline.tw/merchant-showcase-shanfeng/",
                        publish_date="2026-07-10",
                    ),
                    _asset(
                        "video",
                        "三風製麵品牌故事",
                        row=8,
                        url="https://www.youtube.com/watch?v=WIMy_AFA0pE",
                        publish_date="2026-07-10",
                    ),
                ],
                interview_year=2026,
            )
        ],
    )


def _yihe_answer():
    return _answer(
        "怡和家電",
        [
            _entity(
                "怡和家電",
                [
                    _asset(
                        "article",
                        "老牌家電行的電商突圍",
                        row=12,
                        url="https://blog.shopline.tw/merchant-showcase-yh/",
                        publish_date="2026-06-02",
                    ),
                    _asset(
                        "video",
                        "怡和家電專訪",
                        row=12,
                        url="https://youtu.be/7nVLtH5iW20",
                        publish_date="2026-06-02",
                    ),
                ],
                interview_year=2026,
            )
        ],
    )


def test_standard_asset_labels_show_no_raw_formatting_markers():
    text = format_slack_reply(_shanfeng_answer(), max_answer_chars=20_000)
    label_lines = [
        line
        for line in text.splitlines()
        if any(line.startswith(f"> {label}：") for label in ASSET_FIELD_LABELS)
    ]

    assert len(label_lines) == 2 * len(ASSET_FIELD_LABELS)
    for line in label_lines:
        assert "*" not in line
        assert "\\" not in line
        assert "`" not in line


def test_no_bold_run_depends_on_the_value_that_follows_it():
    """The root-cause guard: every "*" the formatter emits must close on a boundary."""
    for text in (
        format_slack_reply(_shanfeng_answer(), max_answer_chars=20_000),
        format_slack_reply(_yihe_answer(), max_answer_chars=20_000),
    ):
        assert UNCLOSED_BOLD_RUN.search(text) is None
        assert "> • *文章 [1]*" in text.splitlines()


@pytest.mark.parametrize(
    "expected",
    [
        "> 標題：傳統製麵廠的數位轉型之路！",
        "> 上線日期：2026-07-10",
        "> 採訪年份：2026",
        "> 狀態：已上線",
        "> 對外引用：可對外引用",
        "> 資料來源：商家夥伴案例資料庫 r8",
    ],
)
def test_each_standard_label_renders_as_plain_text_next_to_its_value(expected):
    text = format_slack_reply(_shanfeng_answer(), max_answer_chars=20_000)

    assert expected in text.splitlines()


@pytest.mark.parametrize(
    "answer_factory,url",
    [
        ("_shanfeng_answer", "https://blog.shopline.tw/merchant-showcase-shanfeng/"),
        ("_shanfeng_answer", "https://www.youtube.com/watch?v=WIMy_AFA0pE"),
        ("_yihe_answer", "https://blog.shopline.tw/merchant-showcase-yh/"),
        ("_yihe_answer", "https://youtu.be/7nVLtH5iW20"),
    ],
)
def test_accepted_asset_urls_remain_clickable_after_the_label_change(answer_factory, url):
    text = format_slack_reply(globals()[answer_factory](), max_answer_chars=20_000)

    assert f"> 連結：<{url}|開啟連結>" in text.splitlines()


def test_missing_url_still_renders_the_missing_marker():
    answer = _answer("query", [_entity("Brand", [_asset("article", "沒有連結", row=3, url=None)])])

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "> 連結：資料未提供" in text.splitlines()
    assert "|開啟連結>" not in text


def test_asterisks_inside_a_title_cannot_break_the_surrounding_labels():
    answer = _answer(
        "query",
        [_entity("Brand", [_asset("article", "測試 *星號* 標題", row=4, url="https://example.com/a")])],
    )

    lines = format_slack_reply(answer, max_answer_chars=20_000).splitlines()

    # The asterisks stay inside the title's own line; no label around it is consumed by them.
    assert "> 標題：測試 *星號* 標題" in lines
    assert "> 連結：<https://example.com/a|開啟連結>" in lines
    assert "> 狀態：已上線" in lines
    assert "> 資料來源：商家夥伴案例資料庫 r4" in lines


def test_angle_brackets_and_ampersands_in_dynamic_text_stay_escaped():
    answer = _answer(
        "query",
        [_entity("A <b> & c", [_asset("article", "標題 <x> & <y>", row=5, url=None)])],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "> 標題：標題 &lt;x&gt; &amp; &lt;y&gt;" in text.splitlines()
    assert "`A &lt;b&gt; &amp; c`" in text
    assert "<b>" not in text
    assert "<x>" not in text


def test_dynamic_text_cannot_inject_a_slack_hyperlink():
    answer = _answer(
        "query",
        [
            _entity(
                "Brand",
                [_asset("article", "<https://evil.example/pwn|點我>", row=6, url=None)],
            )
        ],
    )

    text = format_slack_reply(answer, max_answer_chars=20_000)

    assert "> 標題：&lt;https://evil.example/pwn|點我&gt;" in text.splitlines()
    # The hostile text is displayed inert: no link construct is produced anywhere in the message.
    assert "evil.example" in text
    assert "<https://" not in text
    assert "|開啟連結>" not in text


def test_multiline_dynamic_text_cannot_forge_a_following_label():
    answer = _answer(
        "query",
        [
            _entity(
                "Brand",
                [
                    _asset(
                        "article",
                        "第一行\n> 連結：<https://evil.example/pwn|點我>\n第二行",
                        row=7,
                        url=None,
                    )
                ],
            )
        ],
    )

    lines = format_slack_reply(answer, max_answer_chars=20_000).splitlines()

    assert (
        "> 標題：第一行 &gt; 連結：&lt;https://evil.example/pwn|點我&gt; 第二行" in lines
    )
    # Exactly one 連結 line exists and it is the one the formatter wrote.
    assert [line for line in lines if line.startswith("> 連結：")] == ["> 連結：資料未提供"]
    assert "第二行" not in [line.strip() for line in lines]
    assert "<https://" not in "\n".join(lines)
