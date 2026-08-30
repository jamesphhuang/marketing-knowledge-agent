"""Block Kit surface for the Slack faceted-search MVP.

This module only builds and parses Slack payloads -- the button message, the modal view, and the
``StructuredSearchRequest`` a submission decodes into. It holds no retrieval, governance or
taxonomy logic of its own; that lives in :mod:`search_facets` and :mod:`structured_search`, and this
module's job is to translate between their typed objects and the JSON shapes Slack's Block Kit and
``slack_bolt`` actually speak. It holds no state either: the "調整條件" button carries an opaque
token, and the request it stands for lives in :mod:`slack_request_tokens`.

Nothing here trusts Slack payload display text as meaning: every selected option's *value* (not its
*text*) is what round-trips, and the caller that receives a parsed ``StructuredSearchRequest`` is
still expected to re-validate it against a live ``FacetCatalog`` before executing anything.

Block Kit's own limits are asserted rather than assumed, and every one of them fails closed. Slack
enforces these server-side: exceeding a button ``value`` or ``private_metadata`` budget makes the
API call fail with an opaque error, and an over-long option list is the kind of thing that would
otherwise be "fixed" by dropping the overflow -- hiding eligible values from every user with no
visible symptom. Raising is the only outcome that reaches a human.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .search_facets import FacetCatalog
from .structured_search import (
    FREE_TEXT_MAX_LENGTH,
    StructuredSearchRequest,
    StructuredSearchValidationError,
)


class SlackFacetModalError(ValueError):
    """Raised when a facet catalog cannot be rendered as a Slack modal Slack would accept.

    This is an operator-facing fault, not a user error: it means the underlying data has outgrown a
    Block Kit limit and the MVP's "generate static options at modal-open time" approach no longer
    holds. Failing here is deliberate -- Slack would otherwise reject the ``views_open`` call with an
    opaque error, or (worse, depending on the element) silently render a truncated option list that
    hides eligible values from every user without anyone noticing.
    """


# Slack Block Kit limits, asserted rather than assumed. Each is the documented maximum for the
# element this module actually builds.
MAX_STATIC_SELECT_OPTIONS = 100
MAX_BUTTON_VALUE_CHARS = 2000
MAX_PRIVATE_METADATA_CHARS = 3000
OPEN_SEARCH_MODAL_ACTION_ID = "open_faceted_search_modal"
FACETED_SEARCH_MODAL_CALLBACK_ID = "faceted_search_modal"
INTERVIEW_YEARS_BLOCK_ID = "interview_years_block"
INTERVIEW_YEARS_ACTION_ID = "interview_years_select"
SALES_CATEGORY_LV2_BLOCK_ID = "sales_category_lv2_block"
SALES_CATEGORY_LV2_ACTION_ID = "sales_category_lv2_select"
CONTENT_TAGS_BLOCK_ID = "content_tags_block"
CONTENT_TAGS_ACTION_ID = "content_tags_select"
FREE_TEXT_BLOCK_ID = "free_text_block"
FREE_TEXT_ACTION_ID = "free_text_input"
SHOW_MORE_ACTION_ID = "show_more_search_results"
MAX_SELECTED_OPTIONS = 3
MODAL_TITLE = "案例條件搜尋"
# Exact, case- and whitespace-insensitive commands that open the modal. Anything else -- including
# a sentence that merely contains the word -- flows into the ordinary free-text path unchanged.
TRIGGER_PHRASES = ("搜尋", "條件搜尋")

# The slash command that is the whole search entry point under ``slash_faceted_only``. Declared
# here, beside the Block Kit surface it opens, so the handler registration and the guidance text
# that names it to users cannot drift apart.
SLASH_COMMAND_NAME = "/mka"
# What an ``app_mention`` gets once direct search has moved to the slash command. It is guidance,
# not a search: it triggers no retrieval, records no query, and echoes nothing the user typed.
APP_MENTION_GUIDANCE_MESSAGE = "搜尋功能請使用 `/mka`，即可直接開啟搜尋條件。"

# Which entry point a modal was opened from. Carried in ``private_metadata`` so a submission is
# answered the way its own entry point requires -- in-channel for a mention, ephemerally for a
# slash command -- rather than by guessing from whatever routing fields happen to be populated.
ENTRYPOINT_APP_MENTION = "app_mention"
ENTRYPOINT_SLASH_COMMAND = "slash_command"

# 「全部年份」 is a UI affordance, not data. Its value is a sentinel that no real year can collide
# with (every real option's value is a decimal year), and it decodes to *no* ``interview_year``
# constraint at all -- never to a constraint whose value is the sentinel string, which would be a
# literal that matches nothing in the index while looking like a filter in the audit trail.
ALL_YEARS_OPTION_VALUE = "__all_years__"
ALL_YEARS_OPTION_LABEL = "全部年份"

# What the modal calls each field. These are display strings only: the block ids, action ids,
# ``StructuredSearchRequest`` fields, taxonomy field names, query-plan fields and audit columns all
# keep their existing technical names, so renaming here cannot reach the index, the Authority or
# anything a CLI user sees. Human UAT asked for wording a marketer reads without translating from
# the data model.
INTERVIEW_YEARS_LABEL = "採訪年份"
SALES_CATEGORY_LV2_LABEL = "品牌產業別"
CONTENT_TAGS_LABEL = "你在找什麼功能？"
FREE_TEXT_LABEL = "你想找什麼內容或成果，請輸入關鍵字"
# Shown under the year field, before the user submits. 「全部年份」 deliberately does not narrow a
# search (see ``NARROWING_CONSTRAINT_REQUIRED_MESSAGE``), and UAT found the rule correct but late:
# a user who reopened 調整條件, set the year back to 「全部年份」 and cleared the other fields only
# learned it after submitting. The rule is unchanged; this says so up front.
ALL_YEARS_HINT = f"選擇「{ALL_YEARS_OPTION_LABEL}」時，請再選擇{SALES_CATEGORY_LV2_LABEL}或功能選項。"

# Fallback text for the follow-up messages, defined once so the notification preview and the block
# a user actually reads cannot drift apart.
ADJUST_FILTERS_TEXT = "可調整搜尋條件並重新搜尋。"
RESTART_SEARCH_TEXT = "可重新輸入搜尋條件。"
SHOW_MORE_TEXT = "尚有更多搜尋結果可顯示。"
SHOW_MORE_BUTTON_LABEL = "顯示更多"


def is_faceted_search_trigger(question: str) -> bool:
    """Whether a stripped app-mention question is the "open the modal" command, and nothing else."""
    return question.strip() in TRIGGER_PHRASES


def build_open_search_reply(channel_id: str, thread_ts: str) -> dict:
    """The message posted in reply to the trigger command: one button, no retrieval.

    The button's ``value`` deliberately carries no channel or thread. Those are read from the
    interaction payload Slack sends on the click instead -- see ``_interaction_context`` in
    ``slack_interface``. A button posted in a channel is clickable by everyone who can see it, so
    its ``value`` describes only *which* action to take, never *whose* context to take it in.
    """
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": "點擊下方按鈕以條件搜尋案例。",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"點擊下方按鈕，以年份、{SALES_CATEGORY_LV2_LABEL}、功能選項與關鍵字搜尋案例。"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": "open_faceted_search_actions",
                "elements": [_open_modal_button("開啟條件搜尋", _button_value({}))],
            },
        ],
    }


def build_adjust_filters_message(channel_id: str, thread_ts: str, request_token: str) -> dict:
    """A short follow-up message carrying the "調整條件" button.

    The button carries an opaque ``request_token``, never the request itself. Slack caps a button's
    ``value`` at 2000 characters and a free-text goal alone can exceed that, so embedding the
    request would make the whole message fail once a user typed enough -- and truncating it to fit
    would reopen the modal with a quietly different search. The request stays server-side in
    ``slack_request_tokens``; see that module for what it does and does not hold.

    The token is not a capability. This message is posted into a channel, so anyone who can see the
    thread can click it; the token resolves only for the user, channel and thread it was minted in,
    and that check reads the interaction payload rather than anything carried here.

    Sent as its own message rather than attached to the result text: a Block Kit section's mrkdwn
    text is capped at 3000 characters, far below the result page's own budget, so the two must stay
    separate messages rather than one.
    """
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": ADJUST_FILTERS_TEXT,
        "blocks": adjust_filters_blocks(request_token),
    }


def adjust_filters_blocks(request_token: str, session_id: str = "") -> List[dict]:
    """The "調整條件" action block, without any routing envelope.

    Split out from ``build_adjust_filters_message`` because the slash flow answers ephemerally --
    ``channel`` plus ``user``, never ``thread_ts`` -- so the two entry points share the block and
    differ only in how the message is addressed. ``session_id`` is an opaque per-invocation lane
    id, not search content: it says *which* continuation this button belongs to, and resolves to a
    request only when the clicker is also the owner (see :mod:`slack_request_tokens`).
    """
    return [
        {
            "type": "actions",
            "block_id": "adjust_faceted_search_actions",
            "elements": [
                _open_modal_button("調整條件", _button_value(_action_payload(request_token, session_id)))
            ],
        }
    ]


def show_more_blocks(request_token: str, session_id: str, generation: str = "") -> List[dict]:
    """The 「顯示更多」 action block, without any routing envelope.

    Replaces the mention-based continuation reply for the slash flow, which has no thread to reply
    into -- and where a thread reply would never reach this bot anyway, since it subscribes to
    ``app_mention`` and nothing else. Clicking it replays a page this search already rendered: see
    the handler in ``slack_interface``, which performs no retrieval, no ranking and no query
    planning, and writes no new search audit row.

    The button carries the same ``request_token`` the "調整條件" button does, and for the same
    reason -- the token store is what already proves the clicker owns this search. It is not a
    second capability: resolving it here decides only whether to serve the next page, and the
    request it stands for is never read.
    """
    return [
        {
            "type": "actions",
            "block_id": "show_more_search_actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": SHOW_MORE_ACTION_ID,
                    "text": {"type": "plain_text", "text": SHOW_MORE_BUTTON_LABEL},
                    "value": _button_value(
                        _action_payload(request_token, session_id, generation)
                    ),
                }
            ],
        }
    ]


def _action_payload(
    request_token: Optional[str], session_id: str, generation: str = ""
) -> Dict[str, Any]:
    """The button ``value`` for an action that continues or reopens an existing search.

    Only present keys are emitted, so a fresh open and a refused search both produce ``{}`` rather
    than a payload with empty fields that could compare equal to a real one.

    ``generation`` says *which search* a 「顯示更多」 button belongs to, so a button left over from a
    superseded search can be recognised as stale. It is an opaque server-minted id and authorizes
    nothing on its own: the clicker's user, channel and session still come from the interaction
    payload, and the request token still has to resolve for them. No query, no conditions, no
    response_url and nothing the user typed goes in here.
    """
    payload: Dict[str, Any] = {}
    if request_token:
        payload["request_token"] = request_token
    if session_id:
        payload["session_id"] = session_id
    if generation:
        payload["generation"] = generation
    return payload


def build_restart_search_message(channel_id: str, thread_ts: str) -> dict:
    """The follow-up posted after a refused query: a way back in, carrying nothing.

    A refused query's text must not survive anywhere shared, so there is no token to reopen and
    nothing to prefill. This button opens a blank modal. It is a separate builder rather than
    ``build_adjust_filters_message(token=None)`` so that "no token" is a property of the call site
    that decided it, and cannot be reached by an optional argument defaulting its way in.
    """
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": RESTART_SEARCH_TEXT,
        "blocks": restart_search_blocks(),
    }


def restart_search_blocks(session_id: str = "") -> List[dict]:
    """The "重新搜尋" action block, without any routing envelope.

    Carries no ``request_token`` in either flow -- that is the whole point of the refusal path. The
    slash flow additionally carries its opaque session id so the blank search that follows lands in
    the same continuation lane; a session id is a routing coordinate, never the refused text.
    """
    return [
        {
            "type": "actions",
            "block_id": "restart_faceted_search_actions",
            "elements": [_open_modal_button("重新搜尋", _button_value(_action_payload(None, session_id)))],
        }
    ]


def _open_modal_button(label: str, value: str) -> dict:
    return {
        "type": "button",
        "action_id": OPEN_SEARCH_MODAL_ACTION_ID,
        "text": {"type": "plain_text", "text": label},
        "value": value,
    }


def _button_value(payload: Mapping[str, Any]) -> str:
    """Serialize a button payload, asserting it fits Slack's 2000-character ``value`` budget.

    Every payload this module builds is bounded by construction -- routing coordinates plus a
    fixed-width token -- so this assertion should never fire. It is here because the failure it
    guards against is silent from this side: Slack rejects the ``chat.postMessage`` call, the user
    sees no button at all, and nothing in this process would otherwise know why.
    """
    value = json.dumps(payload, ensure_ascii=False)
    if len(value) > MAX_BUTTON_VALUE_CHARS:
        raise SlackFacetModalError(
            f"button value 長度 {len(value)} 超過 Slack 上限 {MAX_BUTTON_VALUE_CHARS}；"
            "不得靜默截斷，請改用 server-side request token。"
        )
    return value


def parse_open_modal_button_value(raw_value: Optional[str]) -> Dict[str, Any]:
    """Decode a button's ``value`` field, or treat anything untrusted/malformed as empty."""
    try:
        payload = json.loads(raw_value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def request_token_from_button_payload(payload: Mapping[str, Any]) -> Optional[str]:
    """The opaque request token an "調整條件" button carries, or ``None`` for a fresh open."""
    token = payload.get("request_token")
    return str(token) if isinstance(token, str) and token else None


def generation_from_button_payload(payload: Mapping[str, Any]) -> str:
    """The pagination generation a 「顯示更多」 button belongs to, or ``""``.

    A lookup coordinate like the session id, not a claim of authority: it selects which search the
    click refers to, and a forged or stale value simply matches no live continuation.
    """
    generation = payload.get("generation")
    return str(generation) if isinstance(generation, str) and generation else ""


def session_id_from_button_payload(payload: Mapping[str, Any]) -> str:
    """The slash flow's continuation lane id, or ``""`` when this button is not from that flow.

    Read from the button's own ``value``, which is deliberately different from how *identity* is
    obtained: who is clicking and in which conversation always comes from the interaction payload
    Slack builds at click time. A session id is only a lookup coordinate -- it selects a lane, it
    does not grant access to one. A forged or copied value simply fails to match the context the
    request token was minted under, and resolves to nothing.
    """
    session_id = payload.get("session_id")
    return str(session_id) if isinstance(session_id, str) and session_id else ""


def build_facet_modal_view(
    facet_catalog: FacetCatalog,
    channel_id: str,
    thread_ts: str = "",
    prefill: Optional[StructuredSearchRequest] = None,
    *,
    entrypoint: str = ENTRYPOINT_APP_MENTION,
    session_id: str = "",
) -> dict:
    """The full modal view: one ``input`` block per facet, plus the free-text goal.

    A facet with zero eligible options is omitted entirely rather than shown as an empty dropdown --
    the LV1 field is never offered at all, by construction, because ``FacetCatalog`` never carries
    it. The year field is the exception: it is always rendered, because 「全部年份」 is always a
    valid choice and is the default one.

    ``private_metadata`` carries only routing coordinates, the entry point this view was opened
    from, and the catalog version it was built under; no workbook path, hash, or search content
    passes through it. The submitting user is deliberately *not* carried here -- a submission's own
    payload states who sent it, and that is the only source worth trusting for identity.

    ``entrypoint`` defaults to the mention flow, which is the behaviour that predates the slash
    command: a caller that forgets to state one gets exactly today's semantics rather than an
    ephemeral answer addressed to nobody.
    """
    private_metadata = json.dumps(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "catalog_version": facet_catalog.catalog_version,
            "entrypoint": entrypoint,
            "session_id": session_id,
        },
        ensure_ascii=False,
    )
    if len(private_metadata) > MAX_PRIVATE_METADATA_CHARS:
        raise SlackFacetModalError(
            f"private_metadata 長度 {len(private_metadata)} 超過 Slack 上限 "
            f"{MAX_PRIVATE_METADATA_CHARS}。"
        )

    blocks: List[dict] = []
    # 「全部年份」 leads, and is what a modal opens on unless a prior request named one specific
    # year. The field is single-select on purpose: "全部年份 plus 2025" and "2025 plus 2024" are
    # both meaningless as a scope, and a multi-select is the only way for a user to express them.
    year_options = [_option(ALL_YEARS_OPTION_LABEL, ALL_YEARS_OPTION_VALUE)] + [
        _option(str(option.year), str(option.year)) for option in facet_catalog.interview_years
    ]
    prefilled_years = prefill.interview_years if prefill else ()
    blocks.append(
        _single_select_block(
            INTERVIEW_YEARS_BLOCK_ID,
            INTERVIEW_YEARS_ACTION_ID,
            INTERVIEW_YEARS_LABEL,
            year_options,
            hint=ALL_YEARS_HINT,
            # An empty prior selection is 「全部年份」, not "nothing chosen": those are the same
            # state, and reopening on the sentinel is what makes 調整條件 round-trip faithfully.
            initial_value=str(prefilled_years[0]) if prefilled_years else ALL_YEARS_OPTION_VALUE,
        )
    )

    lv2_options = [
        _option(option.canonical_value, option.canonical_value)
        for option in facet_catalog.sales_category_lv2
    ]
    if lv2_options:
        blocks.append(
            _multi_select_block(
                SALES_CATEGORY_LV2_BLOCK_ID,
                SALES_CATEGORY_LV2_ACTION_ID,
                SALES_CATEGORY_LV2_LABEL,
                lv2_options,
                initial_values=list(prefill.sales_category_lv2) if prefill else [],
            )
        )

    tag_options = [
        _option(option.canonical_value, option.canonical_value)
        for option in facet_catalog.content_tags
    ]
    if tag_options:
        blocks.append(
            _multi_select_block(
                CONTENT_TAGS_BLOCK_ID,
                CONTENT_TAGS_ACTION_ID,
                CONTENT_TAGS_LABEL,
                tag_options,
                initial_values=list(prefill.content_tags) if prefill else [],
            )
        )

    blocks.append(_free_text_block(prefill.free_text if prefill else ""))

    return {
        "type": "modal",
        "callback_id": FACETED_SEARCH_MODAL_CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": MODAL_TITLE},
        "submit": {"type": "plain_text", "text": "搜尋"},
        "close": {"type": "plain_text", "text": "取消"},
        "blocks": blocks,
    }


def _option(text: str, value: str) -> dict:
    return {"text": {"type": "plain_text", "text": text[:75]}, "value": value[:75]}


def _multi_select_block(
    block_id: str,
    action_id: str,
    label: str,
    options: Sequence[dict],
    initial_values: Sequence[str],
) -> dict:
    _assert_option_count(label, options, "multi_static_select")
    element: Dict[str, Any] = {
        "type": "multi_static_select",
        "action_id": action_id,
        "options": list(options),
        "max_selected_items": MAX_SELECTED_OPTIONS,
        "placeholder": {"type": "plain_text", "text": f"最多選擇 {MAX_SELECTED_OPTIONS} 個，可留空"},
    }
    selected = set(initial_values)
    initial_options = [option for option in options if option["value"] in selected]
    if initial_options:
        element["initial_options"] = initial_options
    return {
        "type": "input",
        "block_id": block_id,
        "optional": True,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


def _single_select_block(
    block_id: str,
    action_id: str,
    label: str,
    options: Sequence[dict],
    initial_value: str,
    hint: str = "",
) -> dict:
    """One ``static_select`` input, guarded by the same option ceiling as the multi-selects.

    ``initial_option`` must be one of ``options``: Slack rejects a view whose initial option is not
    in the list, and a silently dropped initial option would open the modal on whatever Slack
    chooses to show first -- which for the year field would mean a default nobody selected.
    """
    _assert_option_count(label, options, "static_select")
    element: Dict[str, Any] = {
        "type": "static_select",
        "action_id": action_id,
        "options": list(options),
    }
    initial_option = next((option for option in options if option["value"] == initial_value), None)
    if initial_option is None:
        raise SlackFacetModalError(
            f"facet「{label}」的預設值「{initial_value}」不在選項清單中；"
            "Slack 會拒絕這個 view，不得靜默改用其他預設值。"
        )
    element["initial_option"] = initial_option
    block: Dict[str, Any] = {
        "type": "input",
        "block_id": block_id,
        "optional": True,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }
    if hint:
        # Block Kit's own hint surface, so the guidance sits under the field it is about rather
        # than becoming another line of body text the user has to connect back to a control.
        block["hint"] = {"type": "plain_text", "text": hint}
    return block


def _assert_option_count(label: str, options: Sequence[dict], element_type: str) -> None:
    if len(options) > MAX_STATIC_SELECT_OPTIONS:
        # This MVP generates static options at modal-open time precisely because today's counts sit
        # far below this limit. Crossing it means that premise no longer holds and the field needs
        # an external_select data source -- a design change, not something to paper over by
        # dropping the overflow, which would hide eligible values from every user with no visible
        # symptom.
        raise SlackFacetModalError(
            f"facet「{label}」有 {len(options)} 個選項，超過 Slack {element_type} 上限 "
            f"{MAX_STATIC_SELECT_OPTIONS}；請改用 external_select 或縮小可選集合，"
            "不得靜默截斷選項。"
        )


def _free_text_block(initial_value: str) -> dict:
    element: Dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": FREE_TEXT_ACTION_ID,
        "multiline": True,
        # Slack enforces this client-side and rejects a longer submission with a field error, so the
        # user is told before submitting. It is defence in depth, not the boundary: the same limit
        # is re-checked server-side in ``validate_structured_search_request``, because a Block Kit
        # constraint is a property of the payload Slack sent, not a fact this process may assume.
        "max_length": FREE_TEXT_MAX_LENGTH,
    }
    if initial_value:
        element["initial_value"] = initial_value
    return {
        "type": "input",
        "block_id": FREE_TEXT_BLOCK_ID,
        "optional": True,
        "label": {"type": "plain_text", "text": FREE_TEXT_LABEL},
        "element": element,
    }


def parse_structured_search_request(
    state_values: Mapping[str, Any], catalog_version: str
) -> StructuredSearchRequest:
    """Decode a ``view_submission`` payload's ``state.values`` into a request.

    Only option *values* are read, never their displayed *text*; a caller must still run
    ``structured_search.validate_structured_search_request`` before trusting any of it.

    Raises ``StructuredSearchValidationError`` for a year value this modal could not have offered.
    The caller already reports validation errors back into the modal as a field error, so this fails
    closed at the same place the rest of the submission does.
    """
    return StructuredSearchRequest(
        interview_years=_parse_year_selection(state_values),
        sales_category_lv2=tuple(
            _selected_values(state_values, SALES_CATEGORY_LV2_BLOCK_ID, SALES_CATEGORY_LV2_ACTION_ID)
        ),
        content_tags=tuple(
            _selected_values(state_values, CONTENT_TAGS_BLOCK_ID, CONTENT_TAGS_ACTION_ID)
        ),
        free_text=_text_value(state_values, FREE_TEXT_BLOCK_ID, FREE_TEXT_ACTION_ID),
        catalog_version=catalog_version,
    )


def _parse_year_selection(state_values: Mapping[str, Any]) -> tuple:
    """Decode the single year selection into zero or one ``interview_year``.

    Three cases, and the difference between them matters:

    - no selection at all -- an absent block, or a null ``selected_option``. This is 「全部年份」:
      it is the modal's own default, so an absent value and the sentinel are the same state and
      must decode identically. Returning ``()`` here is not a silently dropped filter.
    - the 「全部年份」 sentinel. Returns ``()`` -- *no* year constraint, rather than a constraint
      whose value is the sentinel. A sentinel-valued constraint would match nothing in the index
      while appearing in the plan and the audit row as though a year had been chosen.
    - a decimal year, which is the only other thing this modal ever renders.

    Anything else can only come from a payload this modal did not produce, so it is refused rather
    than coerced to 「全部年份」 -- coercion would turn a forged year into a whole-corpus search.
    """
    block = state_values.get(INTERVIEW_YEARS_BLOCK_ID) or {}
    element = block.get(INTERVIEW_YEARS_ACTION_ID) or {}
    option = element.get("selected_option") or {}
    raw = option.get("value") if hasattr(option, "get") else None
    if raw is None or str(raw) == ALL_YEARS_OPTION_VALUE:
        return ()
    try:
        return (int(str(raw)),)
    except (TypeError, ValueError):
        raise StructuredSearchValidationError(
            f"採訪年份的選項「{raw}」不是有效年份，請重新開啟搜尋視窗再試一次。"
        )


def _selected_values(state_values: Mapping[str, Any], block_id: str, action_id: str) -> List[str]:
    block = state_values.get(block_id) or {}
    element = block.get(action_id) or {}
    options = element.get("selected_options") or []
    return [str(option.get("value")) for option in options if option.get("value") is not None]


def _text_value(state_values: Mapping[str, Any], block_id: str, action_id: str) -> str:
    block = state_values.get(block_id) or {}
    element = block.get(action_id) or {}
    value = element.get("value")
    return str(value).strip() if value else ""
