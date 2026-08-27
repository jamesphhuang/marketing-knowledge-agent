"""Block Kit surface for the Slack faceted-search MVP.

This module only builds and parses Slack payloads -- the button message, the modal view, and the
``StructuredSearchRequest`` a submission decodes into. It holds no retrieval, governance or
taxonomy logic of its own; that lives in :mod:`search_facets` and :mod:`structured_search`, and this
module's job is to translate between their typed objects and the JSON shapes Slack's Block Kit and
``slack_bolt`` actually speak.

Nothing here trusts Slack payload display text as meaning: every selected option's *value* (not its
*text*) is what round-trips, and the caller that receives a parsed ``StructuredSearchRequest`` is
still expected to re-validate it against a live ``FacetCatalog`` before executing anything.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .search_facets import FacetCatalog
from .structured_search import StructuredSearchRequest


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
MAX_SELECTED_OPTIONS = 3
MODAL_TITLE = "案例條件搜尋"
# Exact, case- and whitespace-insensitive commands that open the modal. Anything else -- including
# a sentence that merely contains the word -- flows into the ordinary free-text path unchanged.
TRIGGER_PHRASES = ("搜尋", "條件搜尋")


def is_faceted_search_trigger(question: str) -> bool:
    """Whether a stripped app-mention question is the "open the modal" command, and nothing else."""
    return question.strip() in TRIGGER_PHRASES


def build_open_search_reply(channel_id: str, thread_ts: str) -> dict:
    """The message posted in reply to the trigger command: one button, no retrieval."""
    button_value = json.dumps({"channel_id": channel_id, "thread_ts": thread_ts}, ensure_ascii=False)
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": "點擊下方按鈕以條件搜尋案例。",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "點擊下方按鈕，以年份、Sales Category LV2、內容相關標籤與關鍵字搜尋案例。",
                },
            },
            {
                "type": "actions",
                "block_id": "open_faceted_search_actions",
                "elements": [_open_modal_button("開啟條件搜尋", button_value)],
            },
        ],
    }


def build_adjust_filters_message(
    channel_id: str, thread_ts: str, request: StructuredSearchRequest
) -> dict:
    """A short follow-up message carrying the "調整條件" button, prefilled with ``request``.

    Sent as its own message rather than attached to the result text: a Block Kit section's mrkdwn
    text is capped at 3000 characters, far below the result page's own budget, so the two must stay
    separate messages rather than one.
    """
    button_value = json.dumps(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "prefill": {
                "interview_years": list(request.interview_years),
                "sales_category_lv2": list(request.sales_category_lv2),
                "content_tags": list(request.content_tags),
                "free_text": request.free_text,
            },
        },
        ensure_ascii=False,
    )
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": "可調整搜尋條件並重新搜尋。",
        "blocks": [
            {
                "type": "actions",
                "block_id": "adjust_faceted_search_actions",
                "elements": [_open_modal_button("調整條件", button_value)],
            }
        ],
    }


def _open_modal_button(label: str, value: str) -> dict:
    return {
        "type": "button",
        "action_id": OPEN_SEARCH_MODAL_ACTION_ID,
        "text": {"type": "plain_text", "text": label},
        "value": value,
    }


def parse_open_modal_button_value(raw_value: Optional[str]) -> Dict[str, Any]:
    """Decode a button's ``value`` field, or treat anything untrusted/malformed as empty."""
    try:
        payload = json.loads(raw_value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def prefill_from_button_payload(payload: Mapping[str, Any]) -> Optional[StructuredSearchRequest]:
    """The prior selection carried by an "調整條件" button, or ``None`` for a fresh open."""
    prefill = payload.get("prefill")
    if not isinstance(prefill, dict):
        return None
    return StructuredSearchRequest(
        interview_years=_prefill_int_tuple(prefill.get("interview_years")),
        sales_category_lv2=_prefill_str_tuple(prefill.get("sales_category_lv2")),
        content_tags=_prefill_str_tuple(prefill.get("content_tags")),
        free_text=str(prefill.get("free_text") or ""),
        # The live catalog version is stamped into private_metadata when the view is actually built,
        # never trusted from a button a user could otherwise have held onto for a long time.
        catalog_version="",
    )


def build_facet_modal_view(
    facet_catalog: FacetCatalog,
    channel_id: str,
    thread_ts: str,
    prefill: Optional[StructuredSearchRequest] = None,
) -> dict:
    """The full modal view: one ``input`` block per non-empty facet, plus the free-text goal.

    A facet with zero eligible options is omitted entirely rather than shown as an empty dropdown --
    the LV1 field is never offered at all, by construction, because ``FacetCatalog`` never carries
    it. ``private_metadata`` carries only routing coordinates and the catalog version this view was
    built under; no workbook path, hash, or content passes through it.
    """
    private_metadata = json.dumps(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "catalog_version": facet_catalog.catalog_version,
        },
        ensure_ascii=False,
    )

    blocks: List[dict] = []
    year_options = [_option(str(option.year), str(option.year)) for option in facet_catalog.interview_years]
    if year_options:
        blocks.append(
            _multi_select_block(
                INTERVIEW_YEARS_BLOCK_ID,
                INTERVIEW_YEARS_ACTION_ID,
                "採訪年份",
                year_options,
                initial_values=[str(year) for year in (prefill.interview_years if prefill else ())],
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
                "Sales Category LV2",
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
                "內容相關標籤",
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


def _free_text_block(initial_value: str) -> dict:
    element: Dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": FREE_TEXT_ACTION_ID,
        "multiline": True,
    }
    if initial_value:
        element["initial_value"] = initial_value
    return {
        "type": "input",
        "block_id": FREE_TEXT_BLOCK_ID,
        "optional": True,
        "label": {"type": "plain_text", "text": "你想找什麼內容或成果"},
        "element": element,
    }


def parse_structured_search_request(
    state_values: Mapping[str, Any], catalog_version: str
) -> StructuredSearchRequest:
    """Decode a ``view_submission`` payload's ``state.values`` into a request.

    Only option *values* are read, never their displayed *text*; a caller must still run
    ``structured_search.validate_structured_search_request`` before trusting any of it.
    """
    years: List[int] = []
    for raw in _selected_values(state_values, INTERVIEW_YEARS_BLOCK_ID, INTERVIEW_YEARS_ACTION_ID):
        try:
            years.append(int(raw))
        except (TypeError, ValueError):
            continue

    return StructuredSearchRequest(
        interview_years=tuple(years),
        sales_category_lv2=tuple(
            _selected_values(state_values, SALES_CATEGORY_LV2_BLOCK_ID, SALES_CATEGORY_LV2_ACTION_ID)
        ),
        content_tags=tuple(
            _selected_values(state_values, CONTENT_TAGS_BLOCK_ID, CONTENT_TAGS_ACTION_ID)
        ),
        free_text=_text_value(state_values, FREE_TEXT_BLOCK_ID, FREE_TEXT_ACTION_ID),
        catalog_version=catalog_version,
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


def _prefill_int_tuple(values: Any) -> tuple:
    result: List[int] = []
    for value in values or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(result)


def _prefill_str_tuple(values: Any) -> tuple:
    return tuple(str(value) for value in values or [] if isinstance(value, (str, int, float)))
