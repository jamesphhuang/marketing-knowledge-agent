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
    ALL_YEARS_HINT,
    ALL_YEARS_OPTION_LABEL,
    ALL_YEARS_OPTION_VALUE,
    CONTENT_TAGS_LABEL,
    FREE_TEXT_LABEL,
    SALES_CATEGORY_LV2_LABEL,
    MAX_BUTTON_VALUE_CHARS,
    MAX_STATIC_SELECT_OPTIONS,
    OPEN_SEARCH_MODAL_ACTION_ID,
    SALES_CATEGORY_LV2_ACTION_ID,
    SALES_CATEGORY_LV2_BLOCK_ID,
    SHOW_MORE_ACTION_ID,
    SlackFacetModalError,
    build_adjust_filters_message,
    build_facet_modal_view,
    build_open_search_reply,
    is_faceted_search_trigger,
    parse_open_modal_button_value,
    parse_structured_search_request,
    request_token_from_button_payload,
    restart_search_blocks,
    session_id_from_button_payload,
    show_more_blocks,
)
from marketing_knowledge_agent.slack_request_tokens import SlackRequestTokenStore
from marketing_knowledge_agent.structured_search import (
    FREE_TEXT_MAX_LENGTH,
    StructuredSearchRequest,
    StructuredSearchValidationError,
)


OWNER = {"owner_user_id": "U1", "channel_id": "C1", "session_key": "1"}
CLICK = {"user_id": "U1", "channel_id": "C1", "session_key": "1"}


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

    assert store.resolve(token, user_id="U2", channel_id="C1", session_key="1") is None


def test_the_same_user_in_a_different_channel_cannot_resolve_the_token():
    store = SlackRequestTokenStore()
    token = store.store(StructuredSearchRequest(free_text="U1 的私人搜尋"), **OWNER)

    assert store.resolve(token, user_id="U1", channel_id="C_OTHER", session_key="1") is None


def test_the_same_user_in_a_different_session_cannot_resolve_the_token():
    store = SlackRequestTokenStore()
    token = store.store(StructuredSearchRequest(free_text="U1 的私人搜尋"), **OWNER)

    assert store.resolve(token, user_id="U1", channel_id="C1", session_key="999") is None


@pytest.mark.parametrize(
    "context",
    [
        {"owner_user_id": "", "channel_id": "C1", "session_key": "1"},
        {"owner_user_id": "U1", "channel_id": "", "session_key": "1"},
        {"owner_user_id": "U1", "channel_id": "C1", "session_key": ""},
        {"owner_user_id": "  ", "channel_id": "C1", "session_key": "1"},
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

    assert metadata == {
        "channel_id": "C1",
        "thread_ts": "100.1",
        "catalog_version": "v1",
        # Defaults to the mention flow, so a caller that states no entry point gets exactly the
        # behaviour that predates the slash command.
        "entrypoint": "app_mention",
        "session_id": "",
    }


def test_slash_modal_carries_its_entrypoint_and_session_but_no_thread():
    view = build_facet_modal_view(
        _catalog(), "D999", entrypoint="slash_command", session_id="sess-1"
    )
    metadata = json.loads(view["private_metadata"])

    assert metadata == {
        "channel_id": "D999",
        "thread_ts": "",
        "catalog_version": "v1",
        "entrypoint": "slash_command",
        "session_id": "sess-1",
    }


def test_private_metadata_never_carries_the_submitting_user():
    """Identity comes from the submission payload; the view says which search, not whose."""
    view = build_facet_modal_view(
        _catalog(), "D999", entrypoint="slash_command", session_id="sess-1"
    )
    assert "user_id" not in json.loads(view["private_metadata"])


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
    assert year_block["element"]["initial_option"]["value"] == "2024"

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
    assert CONTENT_TAGS_LABEL in message  # names the offending facet, in the wording users see
    assert str(MAX_STATIC_SELECT_OPTIONS) in message  # and the limit it crossed
    assert "external_select" in message  # and what to do about it


# --------------------------------------------------------------------------------------
# year selector: single select, 「全部年份」 default
# --------------------------------------------------------------------------------------


def _year_block(view):
    return next(b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID)


def test_year_selector_is_single_select_not_multi():
    """「全部年份」+2025 and 2025+2024 are both meaningless as a scope.

    A multi-select is the only way a user could express either, so the element type is the fix
    rather than a validation rule that has to catch every combination after the fact.
    """
    element = _year_block(build_facet_modal_view(_catalog(), "C1", "1"))["element"]

    assert element["type"] == "static_select"
    assert "max_selected_items" not in element


def test_year_selector_offers_all_years_first_and_selects_it_by_default():
    element = _year_block(build_facet_modal_view(_catalog(), "C1", "1"))["element"]

    assert element["options"][0] == {
        "text": {"type": "plain_text", "text": ALL_YEARS_OPTION_LABEL},
        "value": ALL_YEARS_OPTION_VALUE,
    }
    assert element["initial_option"]["value"] == ALL_YEARS_OPTION_VALUE
    assert [option["value"] for option in element["options"][1:]] == ["2025", "2024"]


def test_a_prefill_with_no_year_reopens_on_all_years():
    """An empty prior year selection *is* 「全部年份」; reopening on it round-trips faithfully."""
    prefill = StructuredSearchRequest(sales_category_lv2=("食品/飲料",))
    element = _year_block(build_facet_modal_view(_catalog(), "C1", "1", prefill=prefill))["element"]

    assert element["initial_option"]["value"] == ALL_YEARS_OPTION_VALUE


def test_the_year_field_is_rendered_even_when_the_catalog_has_no_years():
    """「全部年份」 is always a valid choice, so the field never disappears the way a facet does."""
    no_years = FacetCatalog(
        catalog_version="v1",
        generated_at="2026-08-27T00:00:00+00:00",
        taxonomy_workbook_sha256="a" * 64,
        content_index_generation_id="b" * 64,
        interview_years=(),
        sales_category_lv2=(FacetValueOption("食品/飲料", 4),),
        content_tags=(FacetValueOption("會員經營", 3),),
    )
    element = _year_block(build_facet_modal_view(no_years, "C1", "1"))["element"]

    assert [option["value"] for option in element["options"]] == [ALL_YEARS_OPTION_VALUE]


def test_an_initial_year_slack_would_reject_is_refused_rather_than_quietly_dropped():
    """Slack rejects a view whose ``initial_option`` is not in ``options``.

    Dropping it instead would open the modal on whatever Slack shows first -- a year nobody chose,
    presented as though they had.
    """
    prefill = StructuredSearchRequest(interview_years=(1999,))
    with pytest.raises(SlackFacetModalError, match="1999"):
        build_facet_modal_view(_catalog(), "C1", "1", prefill=prefill)


# --------------------------------------------------------------------------------------
# slash-flow action blocks
# --------------------------------------------------------------------------------------


def test_show_more_block_carries_the_token_and_session_but_no_search_content():
    request_token = "a" * 32
    blocks = show_more_blocks(request_token, "sess-1")
    element = blocks[0]["elements"][0]

    assert element["action_id"] == SHOW_MORE_ACTION_ID
    assert element["text"]["text"] == "顯示更多"
    assert json.loads(element["value"]) == {"request_token": request_token, "session_id": "sess-1"}


def test_restart_block_after_a_refusal_carries_a_session_but_never_a_token():
    """The refusal path has nothing to reopen; the lane id is routing, not the refused text."""
    value = json.loads(restart_search_blocks("sess-1")[0]["elements"][0]["value"])

    assert value == {"session_id": "sess-1"}
    assert "request_token" not in value


def test_a_mention_flow_button_carries_no_session_id():
    """Absence is what tells the handler to read its context from the interaction payload."""
    message = build_adjust_filters_message("C1", "100.1", "b" * 32)
    payload = parse_open_modal_button_value(message["blocks"][-1]["elements"][0]["value"])

    assert session_id_from_button_payload(payload) == ""
    assert request_token_from_button_payload(payload) == "b" * 32


def test_session_id_is_ignored_unless_it_is_a_non_empty_string():
    for payload in ({}, {"session_id": ""}, {"session_id": 5}, {"session_id": None}):
        assert session_id_from_button_payload(payload) == ""


def test_modal_callback_id_is_the_faceted_search_modal():
    view = build_facet_modal_view(_catalog(), "C1", "1")
    assert view["callback_id"] == FACETED_SEARCH_MODAL_CALLBACK_ID
    assert view["type"] == "modal"


# --------------------------------------------------------------------------------------
# submission parsing
# --------------------------------------------------------------------------------------


def _state_values(year=ALL_YEARS_OPTION_VALUE, lv2=None, tags=None, free_text=None):
    """A ``view_submission`` state payload in the shape Slack actually sends one.

    The year field is a single ``static_select``, so it reports ``selected_option`` -- singular,
    an object, not a list. ``year=None`` models the field being absent from the payload entirely.
    """

    def _options(values):
        return {"selected_options": [{"value": value} for value in values]} if values else {
            "selected_options": []
        }

    year_element = {"selected_option": {"value": year} if year is not None else None}
    return {
        INTERVIEW_YEARS_BLOCK_ID: {INTERVIEW_YEARS_ACTION_ID: year_element},
        SALES_CATEGORY_LV2_BLOCK_ID: {SALES_CATEGORY_LV2_ACTION_ID: _options(lv2)},
        CONTENT_TAGS_BLOCK_ID: {CONTENT_TAGS_ACTION_ID: _options(tags)},
        FREE_TEXT_BLOCK_ID: {FREE_TEXT_ACTION_ID: {"type": "plain_text_input", "value": free_text}},
    }


def test_parse_structured_search_request_reads_selected_values_not_display_text():
    state_values = _state_values(year="2024", lv2=["食品/飲料"], tags=["會員經營"], free_text="  測試  ")

    request = parse_structured_search_request(state_values, "v1")

    assert request.interview_years == (2024,)
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


def test_all_years_sentinel_parses_to_no_year_constraint_at_all():
    """「全部年份」 is a UI affordance: it must leave the field empty, not carry the sentinel."""
    request = parse_structured_search_request(_state_values(year=ALL_YEARS_OPTION_VALUE), "v1")

    assert request.interview_years == ()
    assert ALL_YEARS_OPTION_VALUE not in json.dumps(request.__dict__, ensure_ascii=False)


def test_a_specific_year_parses_to_exactly_that_year():
    assert parse_structured_search_request(_state_values(year="2025"), "v1").interview_years == (2025,)


def test_an_absent_year_field_reads_as_all_years():
    """An absent selection and the sentinel are the same state, so they must decode identically."""
    assert parse_structured_search_request(_state_values(year=None), "v1").interview_years == ()


def test_parse_structured_search_request_refuses_a_malformed_year_value():
    """A year this modal never rendered can only come from a forged payload.

    Coercing it to 「全部年份」 would turn a forged field into an unrestricted whole-corpus search,
    so it is refused instead -- the caller reports it back into the modal as a field error.
    """
    with pytest.raises(StructuredSearchValidationError, match="not-a-year"):
        parse_structured_search_request(_state_values(year="not-a-year"), "v1")


# --------------------------------------------------------------------------------------
# Human UAT R1 -- modal wording
# --------------------------------------------------------------------------------------


def _labels(view):
    return {
        block["block_id"]: block["label"]["text"]
        for block in view["blocks"]
        if block.get("type") == "input"
    }


def test_the_modal_uses_the_wording_human_uat_asked_for():
    """Display strings only. The block ids beneath them are deliberately unchanged.

    A marketer reading 「Sales Category LV2」 has to translate from the data model to answer it;
    「品牌產業別」 is the question they were already asking.
    """
    labels = _labels(build_facet_modal_view(_catalog(), "C1", "1"))

    assert labels[SALES_CATEGORY_LV2_BLOCK_ID] == "品牌產業別"
    assert labels[CONTENT_TAGS_BLOCK_ID] == "你在找什麼功能？"
    assert labels[FREE_TEXT_BLOCK_ID] == "你想找什麼內容或成果，請輸入關鍵字"
    assert labels[INTERVIEW_YEARS_BLOCK_ID] == "採訪年份"


def test_renaming_the_labels_did_not_rename_anything_underneath():
    """The rename must not reach the payload keys, the request, or the index."""
    view = build_facet_modal_view(_catalog(), "C1", "1")
    block_ids = {b.get("block_id") for b in view["blocks"]}

    assert block_ids == {
        INTERVIEW_YEARS_BLOCK_ID,
        SALES_CATEGORY_LV2_BLOCK_ID,
        CONTENT_TAGS_BLOCK_ID,
        FREE_TEXT_BLOCK_ID,
    }
    action_ids = {b["element"]["action_id"] for b in view["blocks"] if b.get("type") == "input"}
    assert action_ids == {
        INTERVIEW_YEARS_ACTION_ID,
        SALES_CATEGORY_LV2_ACTION_ID,
        CONTENT_TAGS_ACTION_ID,
        FREE_TEXT_ACTION_ID,
    }
    # And a submission still decodes into the same typed fields.
    request = parse_structured_search_request(
        _state_values(year="2024", lv2=["食品/飲料"], tags=["會員經營"]), "v1"
    )
    assert request.sales_category_lv2 == ("食品/飲料",)
    assert request.content_tags == ("會員經營",)


def test_the_year_field_warns_before_submission_that_all_years_narrows_nothing():
    """UAT found the rule right but the feedback late.

    A user who reopened 調整條件, set the year back to 「全部年份」 and cleared the other fields
    only learned the search was invalid after submitting it. The rule is unchanged; this puts the
    condition on the field itself, through Block Kit's own hint surface.
    """
    year_block = _year_block(build_facet_modal_view(_catalog(), "C1", "1"))

    assert year_block["hint"]["type"] == "plain_text"
    hint = year_block["hint"]["text"]
    assert hint == ALL_YEARS_HINT
    assert ALL_YEARS_OPTION_LABEL in hint
    assert "品牌產業別" in hint


def test_only_the_year_field_carries_a_hint():
    """The other fields are unconditional, so a hint there would be noise."""
    view = build_facet_modal_view(_catalog(), "C1", "1")
    hinted = [b["block_id"] for b in view["blocks"] if "hint" in b]

    assert hinted == [INTERVIEW_YEARS_BLOCK_ID]


def test_the_modal_still_validates_against_slacks_own_view_model_with_the_hint():
    from slack_sdk.models.views import View

    View(**build_facet_modal_view(_catalog(), "C1", "1")).validate_json()
