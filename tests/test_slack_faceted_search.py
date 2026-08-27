"""Contract tests for the Block Kit surface (slack_faceted_search.py).

This module only builds/parses payloads, so these tests never touch retrieval, governance or the
taxonomy Authority -- they use a small hand-built ``FacetCatalog`` and assert on the JSON shapes
Slack's Block Kit and ``slack_bolt`` actually exchange.
"""

import json

import pytest

from marketing_knowledge_agent.search_facets import FacetCatalog, FacetValueOption, FacetYearOption
from marketing_knowledge_agent.slack_faceted_search import (
    CONTENT_TAGS_ACTION_ID,
    CONTENT_TAGS_BLOCK_ID,
    FACETED_SEARCH_MODAL_CALLBACK_ID,
    FREE_TEXT_ACTION_ID,
    FREE_TEXT_BLOCK_ID,
    INTERVIEW_YEARS_ACTION_ID,
    INTERVIEW_YEARS_BLOCK_ID,
    MAX_BUTTON_VALUE_CHARS,
    MAX_STATIC_SELECT_OPTIONS,
    OPEN_SEARCH_MODAL_ACTION_ID,
    SALES_CATEGORY_LV2_ACTION_ID,
    SALES_CATEGORY_LV2_BLOCK_ID,
    SlackFacetModalError,
    build_adjust_filters_message,
    build_facet_modal_view,
    build_open_search_reply,
    is_faceted_search_trigger,
    parse_open_modal_button_value,
    parse_structured_search_request,
    request_token_from_button_payload,
)
from marketing_knowledge_agent.slack_request_tokens import SlackRequestTokenStore
from marketing_knowledge_agent.structured_search import (
    FREE_TEXT_MAX_LENGTH,
    StructuredSearchRequest,
)


OWNER = {"owner_user_id": "U1", "channel_id": "C1", "thread_ts": "1"}
CLICK = {"user_id": "U1", "channel_id": "C1", "thread_ts": "1"}


def _catalog():
    return FacetCatalog(
        catalog_version="v1",
        generated_at="2026-08-27T00:00:00+00:00",
        taxonomy_workbook_sha256="a" * 64,
        content_index_generation_id="b" * 64,
        interview_years=(FacetYearOption(2025, 3), FacetYearOption(2024, 5)),
        sales_category_lv2=(FacetValueOption("食品/飲料", 4), FacetValueOption("居家生活相關", 2)),
        content_tags=(FacetValueOption("會員經營", 3),),
    )


# --------------------------------------------------------------------------------------
# trigger detection
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["搜尋", "條件搜尋", "  搜尋  ", "\t條件搜尋\n"])
def test_trigger_phrases_are_recognised(question):
    assert is_faceted_search_trigger(question) is True


@pytest.mark.parametrize("question", ["搜尋案例", "請幫我搜尋", "顯示更多", "", "大春煉皂"])
def test_non_trigger_text_is_not_recognised(question):
    assert is_faceted_search_trigger(question) is False


# --------------------------------------------------------------------------------------
# open-search reply and adjust-filters message
# --------------------------------------------------------------------------------------


def test_open_search_reply_carries_a_button_with_no_context_of_its_own():
    """Routing lives on the message; the button value states only which action to take.

    Channel and thread are deliberately absent: the button sits in a channel where every member
    sees the same copy, so its value must not be the thing that decides whose context applies.
    """
    reply = build_open_search_reply("C123", "100.1")

    assert reply["channel"] == "C123"
    assert reply["thread_ts"] == "100.1"
    button = reply["blocks"][-1]["elements"][0]
    assert button["action_id"] == OPEN_SEARCH_MODAL_ACTION_ID
    assert json.loads(button["value"]) == {}


def test_adjust_filters_message_carries_a_token_not_the_request_itself():
    message = build_adjust_filters_message("C123", "100.1", "deadbeef" * 4)

    button = message["blocks"][-1]["elements"][0]
    payload = json.loads(button["value"])
    # The token, and nothing else. No request content (that blew the 2000-char budget) and no
    # channel/thread (that would be the button deciding whose context applies).
    assert payload == {"request_token": "deadbeef" * 4}


def test_adjust_button_value_stays_within_slacks_2000_character_budget():
    """The regression this token indirection exists for.

    Embedding the request put a maximal one at 3206 characters -- 1206 over Slack's limit, which
    makes ``chat.postMessage`` reject the whole message, so the user gets no button at all.
    """
    store = SlackRequestTokenStore()
    maximal = StructuredSearchRequest(
        interview_years=(2025, 2024, 2023),
        sales_category_lv2=("食品/飲料", "居家生活相關", "男裝"),
        content_tags=("會員經營", "數位轉型", "團購解決方案"),
        free_text="會" * FREE_TEXT_MAX_LENGTH,
    )
    token = store.store(maximal, **OWNER)

    message = build_adjust_filters_message("C123", "100.1", token)
    value = message["blocks"][-1]["elements"][0]["value"]

    assert len(value) <= MAX_BUTTON_VALUE_CHARS
    # Bounded by construction, not merely "small enough today": the payload is routing coordinates
    # plus a fixed-width token, so the free-text length cannot move this number at all.
    shorter = build_adjust_filters_message("C123", "100.1", store.store(
        StructuredSearchRequest(free_text="x"), **OWNER
    ))
    assert len(shorter["blocks"][-1]["elements"][0]["value"]) == len(value)


def test_an_oversized_button_payload_is_refused_rather_than_truncated():
    with pytest.raises(SlackFacetModalError, match="2000"):
        build_adjust_filters_message("C123", "100.1", "t" * (MAX_BUTTON_VALUE_CHARS + 1))


# --------------------------------------------------------------------------------------
# button value parsing
# --------------------------------------------------------------------------------------


def test_malformed_button_value_parses_to_empty_dict():
    assert parse_open_modal_button_value("not json") == {}
    assert parse_open_modal_button_value(None) == {}
    assert parse_open_modal_button_value("[1, 2, 3]") == {}


def test_request_token_is_none_for_a_fresh_open():
    assert request_token_from_button_payload({}) is None


def test_request_round_trips_through_the_token_store_not_the_button():
    store = SlackRequestTokenStore()
    request = StructuredSearchRequest(
        interview_years=(2024, 2023),
        sales_category_lv2=("食品/飲料",),
        content_tags=(),
        free_text="測試",
    )
    token = store.store(request, **OWNER)
    message = build_adjust_filters_message("C1", "1", token)
    payload = parse_open_modal_button_value(message["blocks"][-1]["elements"][0]["value"])

    restored = store.resolve(request_token_from_button_payload(payload), **CLICK)

    assert restored.interview_years == (2024, 2023)
    assert restored.sales_category_lv2 == ("食品/飲料",)
    assert restored.content_tags == ()
    assert restored.free_text == "測試"


def test_an_expired_token_resolves_to_none_rather_than_a_reconstructed_request():
    clock = [1000.0]
    store = SlackRequestTokenStore(ttl_seconds=60, clock=lambda: clock[0])
    token = store.store(StructuredSearchRequest(free_text="測試"), **OWNER)

    assert store.resolve(token, **CLICK) is not None
    clock[0] += 61
    assert store.resolve(token, **CLICK) is None


def test_an_unknown_token_resolves_to_none():
    store = SlackRequestTokenStore()
    assert store.resolve("never-issued", **CLICK) is None
    assert store.resolve(None, **CLICK) is None


def test_a_different_user_cannot_resolve_another_persons_token():
    """The blocker: the button is public, so presentation alone must not be enough."""
    store = SlackRequestTokenStore()
    token = store.store(StructuredSearchRequest(free_text="U1 的私人搜尋"), **OWNER)

    assert store.resolve(token, user_id="U2", channel_id="C1", thread_ts="1") is None


def test_the_same_user_in_a_different_channel_cannot_resolve_the_token():
    store = SlackRequestTokenStore()
    token = store.store(StructuredSearchRequest(free_text="U1 的私人搜尋"), **OWNER)

    assert store.resolve(token, user_id="U1", channel_id="C_OTHER", thread_ts="1") is None


def test_the_same_user_in_a_different_thread_cannot_resolve_the_token():
    store = SlackRequestTokenStore()
    token = store.store(StructuredSearchRequest(free_text="U1 的私人搜尋"), **OWNER)

    assert store.resolve(token, user_id="U1", channel_id="C1", thread_ts="999") is None


@pytest.mark.parametrize(
    "context",
    [
        {"owner_user_id": "", "channel_id": "C1", "thread_ts": "1"},
        {"owner_user_id": "U1", "channel_id": "", "thread_ts": "1"},
        {"owner_user_id": "U1", "channel_id": "C1", "thread_ts": ""},
        {"owner_user_id": "  ", "channel_id": "C1", "thread_ts": "1"},
    ],
)
def test_storing_without_complete_context_is_refused(context):
    """An empty stored value would compare equal to an empty derived one, disabling the check."""
    store = SlackRequestTokenStore()
    with pytest.raises(ValueError, match="context"):
        store.store(StructuredSearchRequest(free_text="x"), **context)


# --------------------------------------------------------------------------------------
# modal view construction
# --------------------------------------------------------------------------------------


def test_modal_never_offers_sales_category_lv1():
    view = build_facet_modal_view(_catalog(), "C1", "1")
    serialized = json.dumps(view, ensure_ascii=False)

    assert "sales_category_lv1" not in serialized
    assert "LV1" not in serialized


def test_modal_carries_routing_and_catalog_version_in_private_metadata():
    view = build_facet_modal_view(_catalog(), "C1", "100.1")
    metadata = json.loads(view["private_metadata"])

    assert metadata == {"channel_id": "C1", "thread_ts": "100.1", "catalog_version": "v1"}


def test_modal_omits_an_empty_facet_entirely():
    empty_tags_catalog = FacetCatalog(
        catalog_version="v1",
        generated_at="2026-08-27T00:00:00+00:00",
        taxonomy_workbook_sha256="a" * 64,
        content_index_generation_id="b" * 64,
        interview_years=(FacetYearOption(2024, 1),),
        sales_category_lv2=(),
        content_tags=(),
    )
    view = build_facet_modal_view(empty_tags_catalog, "C1", "1")
    block_ids = [block.get("block_id") for block in view["blocks"]]

    assert INTERVIEW_YEARS_BLOCK_ID in block_ids
    assert SALES_CATEGORY_LV2_BLOCK_ID not in block_ids
    assert CONTENT_TAGS_BLOCK_ID not in block_ids
    assert FREE_TEXT_BLOCK_ID in block_ids  # the free-text goal is always offered


def test_modal_every_field_is_optional_and_capped_at_three_options():
    view = build_facet_modal_view(_catalog(), "C1", "1")
    for block in view["blocks"]:
        if block["type"] != "input":
            continue
        assert block["optional"] is True
        element = block["element"]
        if element["type"] == "multi_static_select":
            assert element["max_selected_items"] == 3


def test_modal_prefill_preselects_matching_options():
    prefill = StructuredSearchRequest(
        interview_years=(2024,), sales_category_lv2=("食品/飲料",), content_tags=("會員經營",)
    )
    view = build_facet_modal_view(_catalog(), "C1", "1", prefill=prefill)

    year_block = next(b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID)
    selected_years = {opt["value"] for opt in year_block["element"]["initial_options"]}
    assert selected_years == {"2024"}

    lv2_block = next(b for b in view["blocks"] if b.get("block_id") == SALES_CATEGORY_LV2_BLOCK_ID)
    selected_lv2 = {opt["value"] for opt in lv2_block["element"]["initial_options"]}
    assert selected_lv2 == {"食品/飲料"}


def test_modal_prefill_value_no_longer_in_the_catalog_is_silently_dropped():
    """A stale prefill (from an older catalog) must not crash and must not be force-selected."""
    prefill = StructuredSearchRequest(sales_category_lv2=("已下架的類別",))
    view = build_facet_modal_view(_catalog(), "C1", "1", prefill=prefill)

    lv2_block = next(b for b in view["blocks"] if b.get("block_id") == SALES_CATEGORY_LV2_BLOCK_ID)
    assert "initial_options" not in lv2_block["element"]


def test_modal_free_text_prefill_sets_initial_value():
    prefill = StructuredSearchRequest(free_text="會員回購率")
    view = build_facet_modal_view(_catalog(), "C1", "1", prefill=prefill)

    free_text_block = next(b for b in view["blocks"] if b["block_id"] == FREE_TEXT_BLOCK_ID)
    assert free_text_block["element"]["initial_value"] == "會員回購率"


def test_modal_free_text_declares_the_same_max_length_the_server_enforces():
    """Client-side and server-side bounds must be the same number, or one of them is a lie."""
    view = build_facet_modal_view(_catalog(), "C1", "1")

    free_text_block = next(b for b in view["blocks"] if b["block_id"] == FREE_TEXT_BLOCK_ID)
    assert free_text_block["element"]["max_length"] == FREE_TEXT_MAX_LENGTH


def test_a_facet_at_the_100_option_limit_still_renders():
    at_limit = FacetCatalog(
        catalog_version="v1",
        generated_at="2026-08-27T00:00:00+00:00",
        taxonomy_workbook_sha256="a" * 64,
        content_index_generation_id="b" * 64,
        interview_years=(),
        sales_category_lv2=tuple(
            FacetValueOption(f"類別{i:03d}", 1) for i in range(MAX_STATIC_SELECT_OPTIONS)
        ),
        content_tags=(),
    )

    view = build_facet_modal_view(at_limit, "C1", "1")

    lv2_block = next(b for b in view["blocks"] if b.get("block_id") == SALES_CATEGORY_LV2_BLOCK_ID)
    assert len(lv2_block["element"]["options"]) == MAX_STATIC_SELECT_OPTIONS


def test_a_facet_over_the_100_option_limit_fails_closed_with_an_operator_error():
    """Silently dropping the overflow would hide eligible values from every user, invisibly."""
    over_limit = FacetCatalog(
        catalog_version="v1",
        generated_at="2026-08-27T00:00:00+00:00",
        taxonomy_workbook_sha256="a" * 64,
        content_index_generation_id="b" * 64,
        interview_years=(),
        sales_category_lv2=(),
        content_tags=tuple(
            FacetValueOption(f"標籤{i:03d}", 1) for i in range(MAX_STATIC_SELECT_OPTIONS + 1)
        ),
    )

    with pytest.raises(SlackFacetModalError) as exc_info:
        build_facet_modal_view(over_limit, "C1", "1")

    message = str(exc_info.value)
    assert "內容相關標籤" in message  # names the offending facet
    assert str(MAX_STATIC_SELECT_OPTIONS) in message  # and the limit it crossed
    assert "external_select" in message  # and what to do about it


def test_modal_callback_id_is_the_faceted_search_modal():
    view = build_facet_modal_view(_catalog(), "C1", "1")
    assert view["callback_id"] == FACETED_SEARCH_MODAL_CALLBACK_ID
    assert view["type"] == "modal"


# --------------------------------------------------------------------------------------
# submission parsing
# --------------------------------------------------------------------------------------


def _state_values(years=None, lv2=None, tags=None, free_text=None):
    def _options(values):
        return {"selected_options": [{"value": value} for value in values]} if values else {
            "selected_options": []
        }

    return {
        INTERVIEW_YEARS_BLOCK_ID: {INTERVIEW_YEARS_ACTION_ID: _options(years)},
        SALES_CATEGORY_LV2_BLOCK_ID: {SALES_CATEGORY_LV2_ACTION_ID: _options(lv2)},
        CONTENT_TAGS_BLOCK_ID: {CONTENT_TAGS_ACTION_ID: _options(tags)},
        FREE_TEXT_BLOCK_ID: {FREE_TEXT_ACTION_ID: {"type": "plain_text_input", "value": free_text}},
    }


def test_parse_structured_search_request_reads_selected_values_not_display_text():
    state_values = _state_values(years=["2024", "2023"], lv2=["食品/飲料"], tags=["會員經營"], free_text="  測試  ")

    request = parse_structured_search_request(state_values, "v1")

    assert request.interview_years == (2024, 2023)
    assert request.sales_category_lv2 == ("食品/飲料",)
    assert request.content_tags == ("會員經營",)
    assert request.free_text == "測試"
    assert request.catalog_version == "v1"


def test_parse_structured_search_request_handles_all_blank_submission():
    state_values = _state_values()

    request = parse_structured_search_request(state_values, "v1")

    assert request.interview_years == ()
    assert request.sales_category_lv2 == ()
    assert request.content_tags == ()
    assert request.free_text == ""


def test_parse_structured_search_request_ignores_a_malformed_year_value():
    state_values = _state_values(years=["not-a-year", "2024"])

    request = parse_structured_search_request(state_values, "v1")

    assert request.interview_years == (2024,)
