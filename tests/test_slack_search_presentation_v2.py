"""Slack Search Result Presentation v2 -- the human UAT contract of 2026-08-19.

Four changes are pinned here:

1. the five asset metadata lines (上線日期 / 採訪年份 / 狀態 / 對外引用 / 資料來源) and the
   連結 line left the Slack surface, while every one of those values stays in the result payload;
2. an asset title is itself the approved link, and is plain text whenever no approved URL resolves;
3. one Slack message carries at most fifteen brand groups, whole;
4. the rest continue in the same thread on 「顯示更多」, from an ephemeral in-memory snapshot.
"""

from __future__ import annotations

import threading
import time

import pytest

from marketing_knowledge_agent.models import (
    Citation,
    GeneratedAnswer,
    StructuredAsset,
    StructuredEntity,
    StructuredRetrievalResult,
)
from marketing_knowledge_agent.slack_interface import (
    PAGINATION_EXPIRED_MESSAGE,
    SlackConfig,
    format_slack_reply,
    handle_slack_event,
    run_slack_bot,
)
from marketing_knowledge_agent.slack_pagination import (
    SlackPaginationStore,
    pagination_key,
)
from marketing_knowledge_agent.slack_presentation import (
    BRAND_PAGE_SIZE,
    GOVERNANCE_SILENT_WORDS,
    PAGE_CHAR_BUDGET,
    SHOW_MORE_COMMAND,
    SHOW_MORE_MENTION,
    SHOW_MORE_REPLY,
    SLACK_SEARCH_PARENT_CAP,
    build_structured_slack_pages,
)


HIDDEN_LABELS = ("上線日期", "採訪年份", "狀態", "對外引用", "資料來源", "連結", "標題")
SHANFENG_ARTICLE_URL = "https://blog.shopline.tw/merchant-showcase-shanfeng/"
SHANFENG_VIDEO_URL = "https://www.youtube.com/watch?v=WIMy_AFA0pE"
YIHE_ARTICLE_URL = "https://blog.shopline.tw/merchant-showcase-yh/"
YIHE_VIDEO_URL = "https://youtu.be/7nVLtH5iW20"


# --- fixtures ------------------------------------------------------------------------------------


def _asset(asset_type, title, *, row, url=None):
    return StructuredAsset(
        asset_type=asset_type,
        title=title,
        url=url,
        published_at="2026-07-10",
        publication_status=None,
        external_usage_status="可對外引用",
        source_record_id=f"商家夥伴案例資料庫:r{row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=row,
        citation_label="",
    )


def _entity(name, assets, *, handle="handle", year=2026):
    return StructuredEntity(
        entity_type="merchant",
        entity_name=name,
        merchant_handle=handle,
        sales_category_lv1="美食",
        sales_category_lv2="食品/飲料",
        interview_year=year,
        assets=assets,
    )


def _answer(entities, question="query"):
    citations = []
    for entity in entities:
        for asset in entity.assets:
            label = f"[{len(citations) + 1}]"
            citations.append(
                Citation(
                    label=label,
                    title=asset.title,
                    source_path=f"{asset.source_sheet}:{asset.source_row}",
                    chunk_id=f"chunk-{asset.source_row}:{asset.asset_type}",
                    status="published",
                    source_type="database",
                    record_type="merchant_case",
                    data_classification="public",
                    can_quote_externally=True,
                    publish_date="2026-07-10",
                    source_sheet=asset.source_sheet,
                    source_row=asset.source_row,
                    canonical_url=asset.url,
                    allowed_exposure_channels=[],
                    freshness_note="fresh",
                )
            )
    structured = StructuredRetrievalResult(
        query_plan={
            "raw_query": question,
            "normalized_query": question,
            "hard_filters": [],
            "supported_constraints": [],
            "unsupported_constraints": [],
        },
        matched_entities=entities,
        total_entities=len(entities),
        total_assets=sum(len(entity.assets) for entity in entities),
    )
    return GeneratedAnswer(
        question=question,
        answer="unused",
        citations=citations,
        structured_result=structured,
        governance_checked=True,
    )


def _brands(count, assets_per_brand=1):
    """`count` brands in a fixed rank order, each with its own numbered assets."""
    entities = []
    for index in range(1, count + 1):
        row = index * 10
        entities.append(
            _entity(
                f"品牌{index:02d}",
                [
                    _asset(asset_type, f"品牌{index:02d} {asset_type} {slot}", row=row + slot)
                    for slot, asset_type in enumerate(
                        ("article", "video", "podcast", "news")[:assets_per_brand]
                    )
                ],
            )
        )
    return entities


def _shanfeng():
    return _entity(
        "三風製麵",
        [
            _asset("article", "傳統製麵廠的數位轉型之路！", row=8, url=SHANFENG_ARTICLE_URL),
            _asset("video", "三風製麵品牌故事", row=8, url=SHANFENG_VIDEO_URL),
        ],
    )


def _yihe():
    return _entity(
        "怡和家電",
        [
            _asset("article", "老牌家電行的電商突圍", row=12, url=YIHE_ARTICLE_URL),
            _asset("video", "怡和家電專訪", row=12, url=YIHE_VIDEO_URL),
        ],
    )


def _pages(entities):
    return build_structured_slack_pages(_answer(entities)).pages


def _brand_headings(text):
    return [line for line in text.splitlines() if line.startswith("`品牌")]


def _event(text, *, channel="C123", ts="10", thread_ts=None):
    event = {"text": text, "channel": channel, "user": "U123", "ts": ts}
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return event


def _handle(event, answer, store, audit_path):
    return handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=["C123", "C999"]),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=audit_path,
        pagination_store=store,
    )


# --- change A: the five metadata fields left the Slack surface ------------------------------------


def test_hidden_metadata_fields_never_reach_the_slack_output():
    text = format_slack_reply(_answer([_shanfeng()]), max_answer_chars=20_000)

    for label in HIDDEN_LABELS:
        assert f"{label}：" not in text
    assert "2026-07-10" not in text
    assert "商家夥伴案例資料庫" not in text
    assert "可對外引用" not in text
    assert "已上線" not in text


def test_hidden_metadata_stays_in_the_result_payload():
    """UI hidden is not governance removed: the answer still carries every hidden value."""
    answer = _answer([_shanfeng()])

    asset = answer.structured_result.matched_entities[0].assets[0]
    citation = answer.citations[0]

    assert asset.published_at == "2026-07-10"
    assert asset.external_usage_status == "可對外引用"
    assert asset.source_sheet == "商家夥伴案例資料庫" and asset.source_row == 8
    assert answer.structured_result.matched_entities[0].interview_year == 2026
    assert citation.can_quote_externally is True
    assert citation.status == "published"


# --- change B: the title is the link ---------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_factory,expected",
    [
        (
            _shanfeng,
            [
                f"> <{SHANFENG_ARTICLE_URL}|傳統製麵廠的數位轉型之路！>",
                f"> <{SHANFENG_VIDEO_URL}|三風製麵品牌故事>",
            ],
        ),
        (
            _yihe,
            [
                f"> <{YIHE_ARTICLE_URL}|老牌家電行的電商突圍>",
                f"> <{YIHE_VIDEO_URL}|怡和家電專訪>",
            ],
        ),
    ],
)
def test_approved_urls_render_as_clickable_titles(entity_factory, expected):
    lines = format_slack_reply(_answer([entity_factory()]), max_answer_chars=20_000).splitlines()

    for line in expected:
        assert line in lines


@pytest.mark.parametrize("entity_factory", [_shanfeng, _yihe])
def test_article_and_video_urls_never_cross_over(entity_factory):
    """The article link may only ever carry the article title, and the video link the video."""
    lines = format_slack_reply(_answer([entity_factory()]), max_answer_chars=20_000).splitlines()
    article_line = lines[lines.index("> • *文章 [1]*") + 1]
    video_line = lines[lines.index("> • *影片 [2]*") + 1]

    for url in (SHANFENG_VIDEO_URL, YIHE_VIDEO_URL):
        assert url not in article_line
    for url in (SHANFENG_ARTICLE_URL, YIHE_ARTICLE_URL):
        assert url not in video_line


def test_asset_without_an_approved_url_keeps_a_plain_text_title():
    entity = _entity("Brand", [_asset("article", "沒有連結的文章", row=3)])

    lines = format_slack_reply(_answer([entity]), max_answer_chars=20_000).splitlines()

    assert "> 沒有連結的文章" in lines
    assert "<" not in "\n".join(lines[lines.index("> • *文章 [1]*") :])


def test_no_url_is_invented_from_a_sibling_asset_or_a_title():
    entity = _entity(
        "Brand",
        [
            _asset("article", "https://example.com/looks-like-a-url", row=4),
            _asset("video", "有連結的影片", row=4, url="https://example.com/video"),
        ],
    )

    text = format_slack_reply(_answer([entity]), max_answer_chars=20_000)
    lines = text.splitlines()

    # The article has no approved URL of its own: it borrows neither its sibling's nor its title's.
    assert lines[lines.index("> • *文章 [1]*") + 1] == "> 資料未提供"
    assert lines[lines.index("> • *影片 [2]*") + 1] == "> <https://example.com/video|有連結的影片>"
    assert text.count("<https://") == 1
    assert "example.com/looks-like-a-url" not in text


# --- change B safety: hostile titles inside the link label ------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "標題 & 副標",
        "標題 <b> 粗體",
        "標題 > 引用",
        "<https://evil.example/pwn|點我>",
        "標題*粗體*結束",
        "標題_斜體_結束",
        "標題~刪除線~結束",
        "標題`程式碼`結束",
        "第一行\n第二行",
        "第一行\r\n> • *文章 [99]*",
        "欄位\t值",
        "控制\x00字元\x1f結束",
        "標題 分段 結束",
    ],
)
def test_hostile_titles_cannot_break_out_of_the_approved_link(hostile):
    entity = _entity("Brand", [_asset("article", hostile, row=5, url="https://example.com/ok")])

    text = format_slack_reply(_answer([entity]), max_answer_chars=20_000)
    lines = text.splitlines()
    link_lines = [line for line in lines if line.startswith("> <")]

    # Exactly one link construct exists, it is the approved one, and it opens and closes once.
    assert len(link_lines) == 1
    link = link_lines[0][2:]
    assert link.startswith("<https://example.com/ok|")
    assert link.endswith(">")
    assert link.count("<") == 1 and link.count(">") == 1
    assert "<https://evil" not in text
    assert "example.com/ok" not in link.split("|", 1)[1]
    # No forged structure: one asset header, one brand heading, nothing on a line of its own.
    assert len([line for line in lines if line.startswith("> • *")]) == 1
    assert len([line for line in lines if line.startswith("`")]) == 1
    assert "第二行" not in [line.strip() for line in lines]


def test_a_url_looking_title_that_disagrees_with_the_approved_url_produces_no_link():
    """A title that is itself a URL is never trusted: disagreement fails closed to no link."""
    entity = _entity(
        "Brand", [_asset("article", "https://evil.example/pwn", row=6, url="https://example.com/ok")]
    )

    text = format_slack_reply(_answer([entity]), max_answer_chars=20_000)

    assert "> 資料不一致" in text.splitlines()
    assert "<http" not in text
    assert "evil.example" not in text


def test_normal_chinese_titles_are_not_over_sanitised():
    title = "傳統製麵廠的數位轉型之路！《三風製麵》如何透過 SHOPLINE 提升客單價超過兩成"
    entity = _entity("三風製麵", [_asset("article", title, row=8, url=SHANFENG_ARTICLE_URL)])

    lines = format_slack_reply(_answer([entity]), max_answer_chars=20_000).splitlines()

    assert f"> <{SHANFENG_ARTICLE_URL}|{title}>" in lines


# --- change C: fifteen brands per page ---------------------------------------------------------------


def test_exactly_fifteen_brands_fit_one_page_without_a_notice():
    pages = _pages(_brands(BRAND_PAGE_SIZE))

    assert len(pages) == 1
    assert len(_brand_headings(pages[0])) == BRAND_PAGE_SIZE
    assert "未顯示" not in pages[0]
    assert SHOW_MORE_COMMAND not in pages[0]


def test_fourteen_brands_also_fit_one_page():
    pages = _pages(_brands(BRAND_PAGE_SIZE - 1))

    assert len(pages) == 1
    assert "未顯示" not in pages[0]


def test_sixteen_brands_split_fifteen_then_one():
    pages = _pages(_brands(16))

    assert len(pages) == 2
    assert len(_brand_headings(pages[0])) == 15
    assert len(_brand_headings(pages[1])) == 1
    assert "尚有 1 個品牌／夥伴未顯示。" in pages[0]
    # The notice quotes the mention form: a bare 「顯示更多」 reply raises no app_mention event.
    assert f"若要繼續查看，請在此討論串回覆「{SHOW_MORE_REPLY}」。" in pages[0]
    assert "未顯示" not in pages[1]


def test_seventeen_brands_split_fifteen_then_two():
    pages = _pages(_brands(17))

    assert len(pages) == 2
    assert len(_brand_headings(pages[0])) == 15
    assert len(_brand_headings(pages[1])) == 2
    assert "尚有 2 個品牌／夥伴未顯示。" in pages[0]
    assert "繼續顯示搜尋結果（第 16–17 個品牌／夥伴）" in pages[1]
    assert "未顯示" not in pages[1]


def test_more_than_thirty_brands_paginate_across_every_page():
    pages = _pages(_brands(38))

    assert [len(_brand_headings(page)) for page in pages] == [15, 15, 8]
    assert "尚有 23 個品牌／夥伴未顯示。" in pages[0]
    assert "尚有 8 個品牌／夥伴未顯示。" in pages[1]
    assert "未顯示" not in pages[2]
    assert "繼續顯示搜尋結果（第 16–30 個品牌／夥伴）" in pages[1]
    assert "繼續顯示搜尋結果（第 31–38 個品牌／夥伴）" in pages[2]


def test_rank_order_is_preserved_across_pages():
    entities = _brands(38)
    pages = _pages(entities)

    rendered = [heading for page in pages for heading in _brand_headings(page)]

    assert rendered == [f"`{entity.entity_name}`" for entity in entities]


def test_asset_numbering_continues_across_pages_without_repeating():
    pages = _pages(_brands(17, assets_per_brand=2))
    numbers = [
        int(line.split("[", 1)[1].split("]", 1)[0])
        for page in pages
        for line in page.splitlines()
        if line.startswith("> • *")
    ]

    assert numbers == list(range(1, 35))


def test_a_brand_group_is_never_split_across_two_pages():
    entities = _brands(20, assets_per_brand=4)
    pages = _pages(entities)

    for page in pages:
        for heading in _brand_headings(page):
            name = heading.strip("`")
            assert page.count(f"{name} article") == 1
            assert page.count(f"{name} news") == 1
    assert sum(page.count("> • *") for page in pages) == 20 * 4


def test_an_oversized_brand_group_still_gets_a_whole_page_of_its_own():
    """Atomicity outranks the character budget: a huge brand is paged alone, never truncated."""
    huge = _entity(
        "巨大品牌",
        [
            _asset("article", "長" * (PAGE_CHAR_BUDGET // 2), row=1),
            _asset("video", "長" * (PAGE_CHAR_BUDGET // 2), row=2),
        ],
    )
    pages = _pages([huge] + _brands(2))

    assert len(pages) == 2
    assert pages[0].count("> • *") == 2
    assert len(pages[0]) > PAGE_CHAR_BUDGET
    assert "尚有 2 個品牌／夥伴未顯示。" in pages[0]


def test_the_character_budget_closes_a_page_before_fifteen_brands():
    long_title = "長" * 1200
    entities = [
        _entity(f"品牌{index:02d}", [_asset("article", f"{long_title}{index}", row=index)])
        for index in range(1, 16)
    ]
    pages = _pages(entities)

    assert len(pages) > 1
    assert all(len(_brand_headings(page)) < BRAND_PAGE_SIZE for page in pages)
    assert all(len(page) <= PAGE_CHAR_BUDGET + len(long_title) for page in pages)
    assert sum(len(_brand_headings(page)) for page in pages) == 15


# --- change D: the totals and the notice -------------------------------------------------------------


def test_summary_counts_describe_the_whole_result_not_the_first_page():
    entities = _brands(23, assets_per_brand=2)
    pages = _pages(entities)

    assert "共找到 23 個品牌／夥伴、46 筆內容。" in pages[0]
    assert len(_brand_headings(pages[0])) == 15
    assert "尚有 8 個品牌／夥伴未顯示。" in pages[0]


def test_only_the_first_page_repeats_the_query_condition_header():
    pages = _pages(_brands(17))

    assert pages[0].startswith("已套用搜尋條件：")
    assert "已套用搜尋條件：" not in pages[1]
    assert "共找到" not in pages[1]


# --- change E: 「顯示更多」 in the same thread ---------------------------------------------------------


def test_show_more_continues_the_same_thread_to_the_end(tmp_path):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    answer = _answer(_brands(38))

    first = _handle(_event("品牌", ts="100.1"), answer, store, audit)
    second = _handle(
        _event(SHOW_MORE_COMMAND, ts="100.2", thread_ts="100.1"), answer, store, audit
    )
    third = _handle(
        _event(SHOW_MORE_COMMAND, ts="100.3", thread_ts="100.1"), answer, store, audit
    )
    fourth = _handle(
        _event(SHOW_MORE_COMMAND, ts="100.4", thread_ts="100.1"), answer, store, audit
    )

    assert [reply["thread_ts"] for reply in (first, second, third, fourth)] == ["100.1"] * 4
    assert [len(_brand_headings(reply["text"])) for reply in (first, second, third)] == [15, 15, 8]
    assert "未顯示" not in third["text"]
    # The result is exhausted: a further 「顯示更多」 fails safely instead of looping or guessing.
    assert fourth["text"] == PAGINATION_EXPIRED_MESSAGE
    assert len(store) == 0


def test_two_threads_never_share_a_continuation(tmp_path):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    answer_a = _answer(_brands(17))
    answer_b = _answer(
        [_entity(f"B品牌{index:02d}", [_asset("article", f"B{index}", row=index)]) for index in range(1, 18)]
    )

    _handle(_event("A", channel="C123", ts="1.1"), answer_a, store, audit)
    _handle(_event("B", channel="C999", ts="2.1"), answer_b, store, audit)
    more_a = _handle(
        _event(SHOW_MORE_COMMAND, channel="C123", ts="1.2", thread_ts="1.1"), answer_a, store, audit
    )
    more_b = _handle(
        _event(SHOW_MORE_COMMAND, channel="C999", ts="2.2", thread_ts="2.1"), answer_b, store, audit
    )

    assert "`品牌16`" in more_a["text"] and "B品牌" not in more_a["text"]
    assert "`B品牌16`" in more_b["text"] and "`品牌16`" not in more_b["text"]


def test_the_same_thread_ts_in_two_channels_is_two_continuations(tmp_path):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    answer = _answer(_brands(17))

    _handle(_event("A", channel="C123", ts="9.9"), answer, store, audit)
    other = _handle(
        _event(SHOW_MORE_COMMAND, channel="C999", ts="9.9", thread_ts="9.9"), answer, store, audit
    )

    assert other["text"] == PAGINATION_EXPIRED_MESSAGE


def test_show_more_without_a_context_fails_safely(tmp_path):
    store = SlackPaginationStore()
    reply = _handle(_event(SHOW_MORE_COMMAND, ts="5.1"), _answer(_brands(2)), store, tmp_path / "a.csv")

    assert reply["text"] == PAGINATION_EXPIRED_MESSAGE
    assert reply["thread_ts"] == "5.1"


def test_an_expired_context_fails_safely_instead_of_re_querying(tmp_path):
    now = [0.0]
    store = SlackPaginationStore(ttl_seconds=60, clock=lambda: now[0])
    audit = tmp_path / "audit.csv"
    answer = _answer(_brands(17))

    _handle(_event("品牌", ts="7.1"), answer, store, audit)
    now[0] = 61.0
    reply = _handle(
        _event(SHOW_MORE_COMMAND, ts="7.2", thread_ts="7.1"), answer, store, audit
    )

    assert reply["text"] == PAGINATION_EXPIRED_MESSAGE
    assert len(store) == 0


def test_a_result_that_fits_one_page_leaves_no_continuation_behind(tmp_path):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"

    _handle(_event("多", ts="8.1"), _answer(_brands(17)), store, audit)
    _handle(_event("少", ts="8.1"), _answer(_brands(2)), store, audit)
    reply = _handle(
        _event(SHOW_MORE_COMMAND, ts="8.2", thread_ts="8.1"), _answer(_brands(2)), store, audit
    )

    # The newer, single-page search cleared the thread: 「顯示更多」 cannot resume the older one.
    assert reply["text"] == PAGINATION_EXPIRED_MESSAGE


def test_a_new_search_in_a_thread_replaces_its_continuation(tmp_path):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    first = _answer(_brands(17))
    second = _answer(
        [_entity(f"新品牌{index:02d}", [_asset("article", f"N{index}", row=index)]) for index in range(1, 18)]
    )

    _handle(_event("舊", ts="11.1"), first, store, audit)
    _handle(_event("新", ts="11.2", thread_ts="11.1"), second, store, audit)
    reply = _handle(
        _event(SHOW_MORE_COMMAND, ts="11.3", thread_ts="11.1"), second, store, audit
    )

    assert "`新品牌16`" in reply["text"]
    assert "`品牌16`" not in reply["text"]


def test_a_non_structured_reply_in_the_thread_clears_the_continuation(tmp_path):
    """A refusal or free-text answer supersedes the thread's continuation just as a search does."""
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    plain = GeneratedAnswer(question="q", answer="一般回答", citations=[], governance_checked=True)

    _handle(_event("品牌", ts="19.1"), _answer(_brands(17)), store, audit)
    _handle(_event("其他問題", ts="19.2", thread_ts="19.1"), plain, store, audit)
    reply = _handle(_event(SHOW_MORE_COMMAND, ts="19.3", thread_ts="19.1"), plain, store, audit)

    assert reply["text"] == PAGINATION_EXPIRED_MESSAGE


def test_show_more_neither_re_queries_nor_writes_an_audit_row(tmp_path):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    calls = []

    def counting_ask(*args, **kwargs):
        calls.append(kwargs)
        return _answer(_brands(17))

    handle_slack_event(
        _event("品牌", ts="12.1"),
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=counting_ask,
        audit_log_path=audit,
        pagination_store=store,
    )
    after_search = audit.read_text(encoding="utf-8")
    files_after_search = sorted(path.name for path in tmp_path.iterdir())

    handle_slack_event(
        _event(SHOW_MORE_COMMAND, ts="12.2", thread_ts="12.1"),
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=counting_ask,
        audit_log_path=audit,
        pagination_store=store,
    )

    assert len(calls) == 1
    assert audit.read_text(encoding="utf-8") == after_search
    assert sorted(path.name for path in tmp_path.iterdir()) == files_after_search


def test_a_denied_channel_never_reaches_the_pagination_store(tmp_path):
    store = SlackPaginationStore()
    store.start(pagination_key("C-DENIED", "13.1"), ("page one", "page two"))

    reply = handle_slack_event(
        _event(SHOW_MORE_COMMAND, channel="C-DENIED", ts="13.2", thread_ts="13.1"),
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=lambda *args, **kwargs: _answer(_brands(2)),
        audit_log_path=tmp_path / "audit.csv",
        pagination_store=store,
    )

    assert reply["text"] != "page two"
    assert "未啟用" in reply["text"]


@pytest.mark.parametrize("text", ["顯示更多", " 顯示更多 ", "顯示更多。", "<@U0BOT> 顯示更多"])
def test_the_continuation_reply_is_recognised(tmp_path, text):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    answer = _answer(_brands(17))

    _handle(_event("品牌", ts="14.1"), answer, store, audit)
    reply = _handle(_event(text, ts="14.2", thread_ts="14.1"), answer, store, audit)

    assert "`品牌16`" in reply["text"]


@pytest.mark.parametrize("text", ["顯示更多品牌", "更多", "show more", "顯示"])
def test_a_look_alike_message_is_still_an_ordinary_search(tmp_path, text):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    answer = _answer(_brands(17))

    _handle(_event("品牌", ts="15.1"), answer, store, audit)
    reply = _handle(_event(text, ts="15.2", thread_ts="15.1"), answer, store, audit)

    assert reply["text"].startswith("已套用搜尋條件：")


# --- pagination state safety --------------------------------------------------------------------------


def test_the_store_holds_only_rendered_pages_under_a_routing_key():
    store = SlackPaginationStore()
    key = pagination_key("C123", "16.1")
    generation = store.start(key, ("page one", "page two", "page three"))

    assert key == ("C123", "16.1")
    assert store.consume_next_page(key, generation) == "page two"
    assert store.consume_next_page(key, generation) == "page three"
    assert store.consume_next_page(key, generation) is None


def test_the_store_is_bounded_by_entry_count():
    store = SlackPaginationStore(max_entries=3)
    generations = {}
    for index in range(5):
        key = pagination_key("C123", f"{index}.0")
        generations[key] = store.start(key, ("first", "second"))

    assert len(store) == 3
    evicted = pagination_key("C123", "0.0")
    newest = pagination_key("C123", "4.0")
    assert store.consume_next_page(evicted, generations[evicted]) is None
    assert store.consume_next_page(newest, generations[newest]) == "second"


def test_the_store_expires_by_age():
    now = [0.0]
    store = SlackPaginationStore(ttl_seconds=10, clock=lambda: now[0])
    key = pagination_key("C123", "17.1")
    generation = store.start(key, ("first", "second", "third"))

    now[0] = 5.0
    assert store.consume_next_page(key, generation) == "second"
    now[0] = 14.0  # the read above renewed the entry, so it is still live
    assert store.consume_next_page(key, generation) == "third"


def test_a_restart_simply_loses_every_continuation():
    key = pagination_key("C123", "18.1")
    first = SlackPaginationStore()
    generation = first.start(key, ("first", "second"))

    assert SlackPaginationStore().consume_next_page(key, generation) is None


# --- remediation R1: the instruction on screen is the one that actually works --------------------


def test_the_continuation_notice_quotes_the_mention_form():
    pages = _pages(_brands(16))

    assert f"若要繼續查看，請在此討論串回覆「{SHOW_MORE_REPLY}」。" in pages[0]
    assert SHOW_MORE_MENTION in pages[0]


@pytest.mark.parametrize("count", [16, 17, 31])
def test_no_notice_ever_quotes_the_bare_command_as_the_whole_action(count):
    """「顯示更多」 on its own is not a followable instruction.

    production subscribes to app_mention only, so a thread reply without the mention never
    reaches the handler -- it is dropped with no reply, no error and no expiry message. Quoting
    the bare command would send every reader of this notice down exactly that path.
    """
    for page in _pages(_brands(count)):
        for line in page.splitlines():
            if "請在此討論串回覆" in line:
                assert f"「{SHOW_MORE_REPLY}」" in line
                assert f"回覆「{SHOW_MORE_COMMAND}」" not in line


def test_the_quoted_reply_is_exactly_what_the_handler_continues_on(tmp_path):
    """Round trip: type what the notice says, get the next page.

    Slack delivers a typed mention as a user id token rather than the display name, so the event
    text carries `<@U0BOT>` where the notice showed `@Marketing Knowledge Agent`. What has to
    match is the rest of the reply -- and that is what the handler matches on.
    """
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    answer = _answer(_brands(17))

    first = _handle(_event("品牌", ts="30.1"), answer, store, audit)
    quoted = first["text"].split("請在此討論串回覆「", 1)[1].split("」", 1)[0]

    assert quoted == SHOW_MORE_REPLY
    # rsplit, not split: the display name itself contains spaces; the command never does.
    mention, command = quoted.rsplit(" ", 1)
    assert mention == SHOW_MORE_MENTION and command == SHOW_MORE_COMMAND

    reply = _handle(_event(f"<@U0BOT> {command}", ts="30.2", thread_ts="30.1"), answer, store, audit)

    assert "`品牌16`" in reply["text"]


def test_the_bot_still_subscribes_to_app_mention_and_nothing_else(tmp_path):
    """R1 changed wording, not the event surface. Pagination must not widen what the bot hears."""
    registered = []
    config_path = tmp_path / "slack.json"
    config_path.write_text('{"allowed_channel_ids": ["C123"]}', encoding="utf-8")

    class _App:
        def __init__(self, token):
            self.token = token

        def event(self, name):
            registered.append(name)
            return lambda handler: handler

    class _Handler:
        def __init__(self, app, token):
            self.app = app

        def start(self):
            return None

    run_slack_bot(
        config_path=config_path,
        environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
        app_factory=_App,
        socket_mode_handler_factory=_Handler,
    )

    assert registered == ["app_mention"]


def test_no_message_history_subscription_or_scope_is_referenced():
    source = open("src/marketing_knowledge_agent/slack_interface.py", encoding="utf-8").read()

    for forbidden in (
        "message.channels",
        "message.groups",
        "message.im",
        "message.mpim",
        "channels:history",
        "groups:history",
        "im:history",
        "mpim:history",
    ):
        assert forbidden not in source
    assert source.count("@app.event(") == 1
    assert '@app.event("app_mention")' in source


def test_pagination_writes_to_no_search_analytics_surface():
    for path in (
        "src/marketing_knowledge_agent/slack_pagination.py",
        "src/marketing_knowledge_agent/slack_presentation.py",
    ):
        source = open(path, encoding="utf-8").read()
        for forbidden in ("search_analytics", "SearchAnalytics", "analytics_event", "sqlite3"):
            assert forbidden not in source


# --- remediation R2: a capped result is disclosed as a ceiling, never as a complete total --------


def test_below_the_ceiling_the_total_reads_as_a_complete_result():
    pages = _pages(_brands(SLACK_SEARCH_PARENT_CAP - 1))

    assert "共找到 59 個品牌／夥伴、59 筆內容。" in pages[0]
    assert "目前顯示最多" not in "\n".join(pages)
    assert "已顯示目前最多可提供的" not in "\n".join(pages)


def test_at_the_ceiling_the_total_reads_as_a_display_maximum():
    """60 is where the Slack ceiling stopped admitting brands, so it cannot be called a total.

    The count is taken after the ceiling has already bound and no pre-cap total is kept, so
    「共找到 60」 would state something the system does not know.
    """
    pages = _pages(_brands(SLACK_SEARCH_PARENT_CAP))

    assert "目前顯示最多 60 個品牌／夥伴，共 60 筆內容。" in pages[0]
    assert "共找到 60" not in "\n".join(pages)


def test_the_ceiling_notice_closes_the_last_page_of_a_capped_result():
    pages = _pages(_brands(SLACK_SEARCH_PARENT_CAP))

    assert "已顯示目前最多可提供的 60 個品牌／夥伴。" in pages[-1]
    assert "若想查看更多可能結果，請縮小或調整搜尋條件後重新搜尋。" in pages[-1]
    assert "未顯示" not in pages[-1]
    assert sum("已顯示目前最多可提供的" in page for page in pages) == 1


def test_a_capped_result_still_paginates_normally_in_between():
    pages = _pages(_brands(SLACK_SEARCH_PARENT_CAP))
    headings = [heading for page in pages for heading in _brand_headings(page)]

    assert [len(_brand_headings(page)) for page in pages] == [15, 15, 15, 15]
    assert "尚有 45 個品牌／夥伴未顯示。" in pages[0]
    assert "尚有 30 個品牌／夥伴未顯示。" in pages[1]
    assert "尚有 15 個品牌／夥伴未顯示。" in pages[2]
    assert "繼續顯示搜尋結果（第 16–30 個品牌／夥伴）" in pages[1]
    assert "繼續顯示搜尋結果（第 46–60 個品牌／夥伴）" in pages[3]
    assert len(headings) == 60 and len(set(headings)) == 60


@pytest.mark.parametrize("count", [59, SLACK_SEARCH_PARENT_CAP])
def test_no_page_claims_that_results_exist_beyond_what_was_retrieved(count):
    """Without a pre-cap total, any 「還有更多」 would be a claim the system cannot support."""
    text = "\n".join(_pages(_brands(count)))

    for claim in ("還有更多", "尚有更多", "共有超過", "超過 60", "至少還有"):
        assert claim not in text


def test_the_ceiling_page_offers_an_action_rather_than_a_promise():
    last = _pages(_brands(SLACK_SEARCH_PARENT_CAP))[-1]

    assert "若想查看更多可能結果" in last  # "可能" -- an invitation, not an assertion
    assert "還有更多" not in last


@pytest.mark.parametrize("count", [0, 1, 15, 16, 30, 45, 59])
def test_the_ceiling_wording_never_fires_below_the_ceiling(count):
    text = "\n".join(_pages(_brands(count)))

    assert "目前顯示最多" not in text
    assert "已顯示目前最多可提供的" not in text


def test_a_capped_result_reaches_its_ceiling_notice_through_the_real_handler(tmp_path):
    store = SlackPaginationStore()
    audit = tmp_path / "audit.csv"
    answer = _answer(_brands(SLACK_SEARCH_PARENT_CAP))

    first = _handle(_event("品牌", ts="31.1"), answer, store, audit)
    assert "目前顯示最多 60 個品牌／夥伴，共 60 筆內容。" in first["text"]

    seen = list(_brand_headings(first["text"]))
    pages = [first["text"]]
    for index in range(3):
        reply = _handle(
            _event(f"<@U0BOT> {SHOW_MORE_COMMAND}", ts=f"31.{index + 2}", thread_ts="31.1"),
            answer,
            store,
            audit,
        )
        pages.append(reply["text"])
        seen.extend(_brand_headings(reply["text"]))

    assert len(seen) == 60 and len(set(seen)) == 60
    assert "已顯示目前最多可提供的 60 個品牌／夥伴。" in pages[-1]
    assert _handle(
        _event(f"<@U0BOT> {SHOW_MORE_COMMAND}", ts="31.9", thread_ts="31.1"), answer, store, audit
    )["text"] == PAGINATION_EXPIRED_MESSAGE


# --- R-F6: the ceiling is a fact about retrieved records, not about surviving brand groups -------


def _capped_records(collapse=None):
    """SLACK_SEARCH_PARENT_CAP source records -- exactly where the structured layer stopped.

    Every entity here is one record the parent cap admitted. ``collapse`` swaps the last record
    for one the presentation layer folds into an existing brand or drops outright, so the brand
    count falls below the cap while the retrieved record count stays on it.
    """
    entities = _brands(SLACK_SEARCH_PARENT_CAP)
    if collapse == "same_brand_twice":
        # 品牌01 interviewed a second year: two source records, one brand group.
        entities[-1] = _entity("品牌01", [_asset("video", "品牌01 的第二次採訪", row=901)], year=2025)
    elif collapse == "conflicting_handles":
        # A brand whose records disagree on the handle is withheld entirely, not merged.
        entities[-1] = _entity("品牌01", [_asset("video", "品牌01 v", row=902)], handle="其他-handle")
    return entities


def test_a_full_record_set_that_merges_into_fewer_brands_still_discloses_the_ceiling():
    """60 records presented as 59 brands is a capped result, not a complete 59.

    The cap is spent on source records, so the brand count left after grouping says nothing
    about whether it bound. Reading the ceiling off the brand count would let this result
    introduce itself as the whole universe.
    """
    entities = _capped_records(collapse="same_brand_twice")
    pages = build_structured_slack_pages(_answer(entities))

    assert len(entities) == SLACK_SEARCH_PARENT_CAP
    assert pages.total_entities == SLACK_SEARCH_PARENT_CAP - 1
    assert "目前顯示最多 59 個品牌／夥伴，共 60 筆內容。" in pages.pages[0]
    assert "共找到 59" not in "\n".join(pages.pages)
    assert "已顯示目前最多可提供的 59 個品牌／夥伴。" in pages.pages[-1]


def test_a_full_record_set_whose_brand_is_withheld_still_discloses_the_ceiling():
    """A group dropped for conflicting handles takes two records off the brand count."""
    entities = _capped_records(collapse="conflicting_handles")
    pages = build_structured_slack_pages(_answer(entities))

    assert len(entities) == SLACK_SEARCH_PARENT_CAP
    assert pages.total_entities == SLACK_SEARCH_PARENT_CAP - 2
    assert "目前顯示最多 58 個品牌／夥伴，共 58 筆內容。" in pages.pages[0]
    assert "共找到 58" not in "\n".join(pages.pages)
    assert "已顯示目前最多可提供的 58 個品牌／夥伴。" in pages.pages[-1]


def test_a_brand_lost_to_governance_filtering_does_not_hide_the_ceiling():
    """The record was retrieved and counted against the cap; only its assets were withheld."""
    entities = _capped_records()
    answer = _answer(entities)
    answer.citations = [citation for citation in answer.citations if "品牌60" not in citation.title]
    pages = build_structured_slack_pages(answer)

    assert len(answer.structured_result.matched_entities) == SLACK_SEARCH_PARENT_CAP
    assert pages.total_entities == SLACK_SEARCH_PARENT_CAP - 1
    assert "目前顯示最多 59 個品牌／夥伴，共 59 筆內容。" in pages.pages[0]
    assert "共找到 59" not in "\n".join(pages.pages)


def test_one_record_below_the_cap_reads_as_a_complete_result():
    """The control for the three above: 59 records never reached the ceiling."""
    entities = _brands(SLACK_SEARCH_PARENT_CAP - 1)
    pages = build_structured_slack_pages(_answer(entities))

    assert len(entities) == SLACK_SEARCH_PARENT_CAP - 1
    assert pages.total_entities == SLACK_SEARCH_PARENT_CAP - 1
    assert "共找到 59 個品牌／夥伴、59 筆內容。" in pages.pages[0]
    assert "目前顯示最多" not in "\n".join(pages.pages)


def test_brands_falling_below_the_cap_is_not_by_itself_a_ceiling():
    """59 records that merge into 58 brands must still read as a complete result.

    The negative control that stops the predicate from being satisfied by any drop in the brand
    count -- disclosing a ceiling that never bound would be its own false statement.
    """
    entities = _brands(SLACK_SEARCH_PARENT_CAP - 1)
    entities[-1] = _entity("品牌01", [_asset("video", "品牌01 的第二次採訪", row=903)], year=2025)
    pages = build_structured_slack_pages(_answer(entities))

    assert len(entities) == SLACK_SEARCH_PARENT_CAP - 1
    assert pages.total_entities == SLACK_SEARCH_PARENT_CAP - 2
    assert "共找到 58 個品牌／夥伴、59 筆內容。" in pages.pages[0]
    assert "目前顯示最多" not in "\n".join(pages.pages)
    assert "已顯示目前最多可提供的" not in "\n".join(pages.pages)


def test_the_ceiling_predicate_reads_records_not_the_rendered_brand_count():
    """Pinned directly: the same 59 brands disclose or not according to the record count."""
    merged = build_structured_slack_pages(_answer(_capped_records(collapse="same_brand_twice")))
    short = build_structured_slack_pages(_answer(_brands(SLACK_SEARCH_PARENT_CAP - 1)))

    assert merged.total_entities == short.total_entities == SLACK_SEARCH_PARENT_CAP - 1
    assert "目前顯示最多 59" in merged.pages[0]
    assert "共找到 59" in short.pages[0]


def test_a_collapsed_ceiling_result_keeps_every_other_v2_guarantee():
    """R-F6 moved the ceiling predicate only; the rest of the surface is untouched."""
    entities = _brands(SLACK_SEARCH_PARENT_CAP - 2) + [_shanfeng(), _shanfeng()]
    pages = build_structured_slack_pages(_answer(entities)).pages
    joined = "\n".join(pages)

    assert len(entities) == SLACK_SEARCH_PARENT_CAP
    assert "目前顯示最多 59 個品牌／夥伴" in pages[0]
    # the continuation notice still quotes the mention form
    assert f"若要繼續查看，請在此討論串回覆「{SHOW_MORE_REPLY}」。" in pages[0]
    assert f"回覆「{SHOW_MORE_COMMAND}」" not in joined
    # fifteen brands to a page, groups whole
    assert [len(_brand_headings(page)) for page in pages] == [15, 15, 15, 13]
    # titles still carry their own approved link
    assert f"<{SHANFENG_ARTICLE_URL}|傳統製麵廠的數位轉型之路！>" in joined
    assert f"<{SHANFENG_VIDEO_URL}|三風製麵品牌故事>" in joined
    # hidden metadata and governance vocabulary stay off the surface
    for label in HIDDEN_LABELS:
        assert f"{label}：" not in joined
    for word in GOVERNANCE_SILENT_WORDS:
        assert word not in joined


# ======================================================================================
# Independent Security Review R3 — pagination generation and supersession ordering
# ======================================================================================


def _release_after(event, action):
    """Run ``action`` on a thread once ``event`` is set, and hand back the thread and its result."""
    result = []

    def worker():
        event.wait(timeout=5)
        result.append(action())

    thread = threading.Thread(target=worker)
    thread.start()
    return thread, result


def test_a_stale_generation_consumes_nothing():
    """The core of R3 finding 2, stated at its simplest."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2", "old-3"])
    new = store.start(key, ["new-1", "new-2"])

    assert store.consume_next_page(key, old) is None
    assert store.consume_next_page(key, new) == "new-2"


def test_a_stale_last_page_cleanup_cannot_delete_the_new_continuation():
    """R3 finding 2's second half: the unconditional ``pop``.

    An old worker finishing its final page used to remove the lane's entry outright, which after a
    supersede meant deleting a *newer* search's continuation. Same user, so no ownership check ever
    applied.
    """
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["a", "LAST"])          # one page remaining on the old generation
    new = store.start(key, ["new-1", "new-2", "new-3"])
    assert len(store) == 1

    assert store.consume_next_page(key, old) is None

    assert len(store) == 1
    assert store.consume_next_page(key, new) == "new-2"


def test_cleanup_removes_only_the_generation_that_asked():
    """Pins the removal guard directly, because the store lock currently hides it.

    ``consume_next_page`` holds the store lock across its whole body, so a stale caller is turned
    away by the generation check long before it could reach the removal -- which means a mutation
    that restores an unconditional ``pop`` there changes no observable behaviour today. That is a
    reason to test the helper on its own terms, not a reason to drop the guard: the guard is what
    keeps the removal correct if that critical section is ever narrowed, and an untested guard is
    one a refactor deletes.
    """
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old_generation = store.start(key, ["a", "b"])
    new_generation = store.start(key, ["x", "y"])

    with store._lock:
        store._remove_if_current(key, old_generation)
    assert store.has_more(key, new_generation) is True, "a foreign generation removed the entry"

    with store._lock:
        store._remove_if_current(key, new_generation)
    assert store.has_more(key, new_generation) is False


def test_has_more_cannot_observe_another_generation():
    """A key-only ``has_more`` let a stale worker render a button for someone else's search."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["a", "b"])
    new = store.start(key, ["x", "y", "z"])

    assert store.has_more(key, old) is False
    assert store.has_more(key, new) is True


def test_a_one_page_new_search_still_supersedes_the_old_continuation():
    """R3 B16. A result that needs no continuation must still invalidate the previous one."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["p1", "p2", "p3"])

    store.start(key, ["only-one-page"])

    assert store.consume_next_page(key, old) is None
    assert len(store) == 0


def test_a_new_search_never_precedes_an_old_page_under_concurrency():
    """R3 B12, the exact reproduced race.

    Worker A begins a 「顯示更多」 on the old generation and pauses inside the lane guard. Worker B
    starts a new search on the same lane. Only two serialisations are acceptable -- A delivers and
    then B supersedes, or B supersedes and A finds its generation stale. What must never happen is
    B becoming authoritative and A delivering an old page afterwards.
    """
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2", "old-3"])

    inside = threading.Event()
    release = threading.Event()
    delivered = []
    order = []

    def worker_a():
        with store.lane_operation(key):
            inside.set()
            release.wait(timeout=5)
            page = store.consume_next_page(key, old)
            if page is not None:
                delivered.append(page)
                order.append("old-page-delivered")

    thread_a = threading.Thread(target=worker_a)
    thread_a.start()
    assert inside.wait(timeout=5)

    def worker_b():
        store.start(key, ["new-1", "new-2"])
        order.append("new-search-authoritative")

    thread_b = threading.Thread(target=worker_b)
    thread_b.start()
    release.set()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    # The lane guard held B until A finished, so this run takes the first acceptable ordering.
    assert order == ["old-page-delivered", "new-search-authoritative"]
    # And the forbidden ordering is the one that cannot appear.
    assert order != ["new-search-authoritative", "old-page-delivered"]


def test_a_stale_worker_cannot_delete_a_new_entry_under_concurrency():
    """R3 B13, as a race rather than a sequence."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["a", "LAST"])

    superseded = threading.Event()
    thread, result = _release_after(superseded, lambda: store.consume_next_page(key, old))

    new = store.start(key, ["new-1", "new-2"])
    superseded.set()
    thread.join(timeout=10)

    assert result == [None]                       # the stale worker got nothing
    assert store.has_more(key, new) is True       # and removed nothing
    assert store.consume_next_page(key, new) == "new-2"


def test_two_concurrent_show_more_clicks_deliver_each_page_once():
    """R3 B14. Consume-next is serialized, so no page is duplicated and none is skipped."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    generation = store.start(key, ["p0", "p1", "p2"])

    barrier = threading.Barrier(2, timeout=5)
    pages = []
    lock = threading.Lock()

    def click():
        barrier.wait()
        with store.lane_operation(key):
            page = store.consume_next_page(key, generation)
        if page is not None:
            with lock:
                pages.append(page)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(pages) == ["p1", "p2"]          # each exactly once, none skipped


def test_the_lane_guard_does_not_serialize_unrelated_lanes():
    """Per-lane, not global: two conversations must not wait on each other."""
    store = SlackPaginationStore()
    first = pagination_key("C1", "U1:s")
    second = pagination_key("C2", "U2:s")
    store.start(first, ["a", "b"])
    store.start(second, ["c", "d"])

    entered_second = threading.Event()

    with store.lane_operation(first):
        thread = threading.Thread(
            target=lambda: (store.lane_operation(second).__enter__(), entered_second.set()),
        )
        thread.start()
        assert entered_second.wait(timeout=5), "an unrelated lane was blocked"
        thread.join(timeout=5)


def test_a_generation_is_opaque_and_carries_no_search_content():
    store = SlackPaginationStore()
    generation = store.start(pagination_key("C1", "U1:s"), ["page one", "page two"])

    assert isinstance(generation, str) and len(generation) == 32
    assert all(character in "0123456789abcdef" for character in generation)
    assert "page" not in generation


def test_the_store_serializes_its_mutable_state():
    """The mechanism the probe removes."""
    store = SlackPaginationStore()
    assert isinstance(store._lock, type(threading.RLock()))


# ======================================================================================
# Final Security Review R4 — every new-search outcome supersedes under one lane contract
# ======================================================================================


def _race_show_more_against(store, key, old_generation, supersede, b_first):
    """Run a paused 「顯示更多」 against a superseding new search, and record the observable order.

    Worker A holds the lane across consume *and* delivery, exactly as the handler does. Worker B is
    whatever the new search does -- refusal, one-page, multi-page. What the test reads is the order
    the two responses would reach the user.
    """
    order = []
    inside = threading.Event()
    release = threading.Event()

    def worker_a():
        with store.lane_operation(key):
            page = store.consume_next_page(key, old_generation)
            inside.set()
            release.wait(timeout=5)
            if page is not None:
                order.append("OLD page")

    def worker_b():
        supersede()
        order.append("NEW response")

    if b_first:
        thread_b = threading.Thread(target=worker_b)
        thread_b.start()
        thread_b.join(timeout=10)
        thread_a = threading.Thread(target=worker_a)
        thread_a.start()
        assert inside.wait(timeout=5)
        release.set()
        thread_a.join(timeout=10)
    else:
        thread_a = threading.Thread(target=worker_a)
        thread_a.start()
        assert inside.wait(timeout=5)
        thread_b = threading.Thread(target=worker_b)
        thread_b.start()
        release.set()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)
    return order


def _refusal_supersession(store, key):
    """What a denylist refusal does: take the lane, install nothing, deliver inside it."""

    def supersede():
        with store.supersede_lane(key):
            pass

    return supersede


def test_a_refusal_cannot_be_followed_by_a_page_from_the_search_it_replaced():
    """The exact R4 blocker.

    The refusal path used to supersede through a bare ``discard`` outside any lane guard, so a
    「顯示更多」 that had already consumed its page could deliver it *after* the refusal was out --
    a new response followed by an old result. Locking ``discard`` alone would not have helped: the
    window is between the consume and the send.
    """
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2", "old-3"])

    order = _race_show_more_against(store, key, old, _refusal_supersession(store, key), b_first=False)

    # The 「顯示更多」 owned the lane first, so it completes and the refusal waits behind it.
    assert order == ["OLD page", "NEW response"]
    assert order != ["NEW response", "OLD page"]


def test_a_refusal_that_supersedes_first_leaves_the_stale_click_with_nothing():
    """R4's inverse ordering. Both orders are acceptable; only one interleaving is not."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2", "old-3"])

    order = _race_show_more_against(store, key, old, _refusal_supersession(store, key), b_first=True)

    assert order == ["NEW response"]


@pytest.mark.parametrize(
    ("label", "new_pages"),
    [
        ("refusal_or_empty", None),
        ("one_page", ["only-one"]),
        ("multi_page", ["new-1", "new-2", "new-3"]),
    ],
)
def test_every_new_search_outcome_supersedes_the_previous_continuation(label, new_pages):
    """One contract, not three.

    A refusal, a result that fits one message and a paginated result are all *new searches*, so a
    button from the previous one is stale after any of them. The one-page case is the one that used
    to slip through: it installs no continuation, and the old one was left alive.
    """
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2", "old-3"])

    with store.supersede_lane(key) as lane:
        new_generation = lane.install(new_pages) if new_pages is not None else ""

    assert store.consume_next_page(key, old) is None
    assert store.has_more(key, old) is False
    if new_pages is not None and len(new_pages) > 1:
        assert store.consume_next_page(key, new_generation) == new_pages[1]
    else:
        assert len(store) == 0


def test_a_one_page_search_cannot_be_followed_by_an_old_page():
    """The same ordering guarantee for a result that installs no continuation of its own."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2"])

    def supersede():
        with store.supersede_lane(key) as lane:
            lane.install(["only-one"])

    assert _race_show_more_against(store, key, old, supersede, b_first=True) == ["NEW response"]


def test_a_failed_delivery_does_not_resurrect_the_superseded_search():
    """The lane is cleared on entry, so an exception during delivery leaves it invalidated.

    Restoring the old continuation would make a search the user has already moved past current
    again, which is the state this whole model exists to prevent.
    """
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2"])

    with pytest.raises(RuntimeError):
        with store.supersede_lane(key):
            raise RuntimeError("delivery failed")

    assert store.consume_next_page(key, old) is None
    assert len(store) == 0


def test_a_page_cannot_overtake_the_result_it_belongs_to():
    """The lane must stay held until the *whole* new response is out, not just until it installs.

    Releasing at install time is not enough, and the reason is worth stating precisely: a stale
    click cannot produce an old page either way, because the old generation is already gone. What
    an early release does allow is a 「顯示更多」 on the **new** generation consuming and delivering
    page two while the new search is still delivering page one -- the second page overtaking the
    first.
    """
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    store.start(key, ["stale-1", "stale-2"])

    order = []
    installed = threading.Event()
    finish_delivery = threading.Event()
    new_generation = {}

    def new_search():
        with store.supersede_lane(key) as lane:
            new_generation["id"] = lane.install(["new-page-1", "new-page-2"])
            installed.set()
            finish_delivery.wait(timeout=5)
            order.append("new result page 1")

    def show_more_on_the_new_generation():
        assert installed.wait(timeout=5)
        with store.lane_operation(key):
            page = store.consume_next_page(key, new_generation["id"])
            if page is not None:
                order.append(f"show more page ({page})")

    searcher = threading.Thread(target=new_search)
    clicker = threading.Thread(target=show_more_on_the_new_generation)
    searcher.start()
    clicker.start()
    # Give the clicker every chance to overtake before the result is released.
    installed.wait(timeout=5)
    time.sleep(0.2)
    finish_delivery.set()
    searcher.join(timeout=10)
    clicker.join(timeout=10)

    assert order[0] == "new result page 1", (
        "page two overtook the result it continues -- the lane was released before delivery"
    )


def test_start_and_supersede_share_one_ordering_contract():
    """``start`` is the convenience form, not a second set of semantics."""
    store = SlackPaginationStore()
    key = pagination_key("C1", "U1:s")
    old = store.start(key, ["old-1", "old-2"])

    entered = threading.Event()

    def hold_the_lane():
        with store.lane_operation(key):
            entered.set()
            time.sleep(0.2)

    holder = threading.Thread(target=hold_the_lane)
    holder.start()
    assert entered.wait(timeout=5)

    # ``start`` must wait for the lane exactly as ``supersede_lane`` does.
    began = time.monotonic()
    store.start(key, ["new-1", "new-2"])
    waited = time.monotonic() - began
    holder.join(timeout=5)

    assert waited >= 0.1, "start() did not serialize against a held lane"
    assert store.consume_next_page(key, old) is None
