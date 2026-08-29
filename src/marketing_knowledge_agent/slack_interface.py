from __future__ import annotations

import csv
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Mapping, Optional

from .content_index import DEFAULT_CONTENT_INDEX_DB
from .governance import metadata_allows_written_external_use
from .llm import DEFAULT_LLM_CONFIG_PATH, load_llm_config
from .models import SearchFilters
from .pipeline import DEFAULT_RESTRICTED_CUSTOMERS_PATH, agent_ask
from .search_facets import FacetCatalog, build_facet_catalog
from .search_taxonomy import SearchTaxonomy, load_search_taxonomy
from .slack_faceted_search import (
    APP_MENTION_GUIDANCE_MESSAGE,
    ADJUST_FILTERS_TEXT,
    ENTRYPOINT_APP_MENTION,
    ENTRYPOINT_SLASH_COMMAND,
    FREE_TEXT_BLOCK_ID,
    OPEN_SEARCH_MODAL_ACTION_ID,
    FACETED_SEARCH_MODAL_CALLBACK_ID,
    RESTART_SEARCH_TEXT,
    SHOW_MORE_ACTION_ID,
    SLASH_COMMAND_NAME,
    adjust_filters_blocks,
    build_adjust_filters_message,
    build_facet_modal_view,
    build_open_search_reply,
    build_restart_search_message,
    is_faceted_search_trigger,
    parse_open_modal_button_value,
    parse_structured_search_request,
    request_token_from_button_payload,
    restart_search_blocks,
    session_id_from_button_payload,
    show_more_blocks,
    generation_from_button_payload,
)
from .slack_request_tokens import SlackRequestTokenStore, default_request_token_store
from .slack_response_urls import (
    ResponseReservation,
    SlackResponseUrlStore,
    default_response_url_store,
    is_valid_response_url,
    send_response_url_message,
    single_use_reservation,
)
from .slack_output_preview import (
    apply_approved_asset_url_overlay,
    load_index_bound_approved_asset_url_overlay,
)
from .slack_pagination import (
    SlackPaginationStore,
    default_pagination_store,
    pagination_key,
)
from .slack_presentation import (
    SHOW_MORE_BUTTON_HINT,
    SHOW_MORE_COMMAND,
    SHOW_MORE_MENTION,
    SHOW_MORE_THREAD_REPLY_HINT,
    SLACK_SEARCH_PARENT_CAP,
    build_structured_slack_pages,
    format_structured_slack_reply,
)
from .structured_search import (
    StaleFacetCatalogError,
    StructuredSearchRequest,
    StructuredSearchValidationError,
    execute_structured_search,
    is_restricted_refusal,
    load_required_governance_index,
    validate_structured_search_request,
)


# How this Slack surface is entered, and therefore what an ``app_mention`` means.
#
# ``mention_mixed`` is the pre-existing product: an app mention is a natural-language search, and
# the faceted modal (when enabled) is reachable from a trigger phrase. It is the default, so a
# deployment that merges this code without changing its config behaves exactly as it did before.
#
# ``slash_faceted_only`` is the ``/mka`` product: the modal is the only way to search, an app
# mention answers with guidance and never retrieves, and results are ephemeral to the invoker.
# Selecting it is an explicit configuration act, never a side effect of shipping the code.
ENTRY_MODE_MENTION_MIXED = "mention_mixed"
ENTRY_MODE_SLASH_FACETED_ONLY = "slash_faceted_only"
SLACK_SEARCH_ENTRY_MODES = (ENTRY_MODE_MENTION_MIXED, ENTRY_MODE_SLASH_FACETED_ONLY)
# 32 hex characters identifying one ``/mka`` invocation. A slash command is not a message, so it
# has no ``thread_ts`` to separate one search from the next; this is what takes that role. It is
# always combined with the invoking user id before use (see ``_slash_session_key``), so two people
# can never share a continuation lane even if one of them learned the other's id.
SLASH_SESSION_ID_BYTES = 16

DEFAULT_SLACK_CONFIG_PATH = Path(".mka/slack_config.json")
DEFAULT_SLACK_AUDIT_LOG = Path("reports/audit_log.csv")
# Payload-free audit code recorded when approved URL authority cannot be verified. It carries no
# path, hash, exception text or CSV content, and is never shown to the Slack user.
APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE = "APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE"
DENIED_CHANNEL_MESSAGE = "此頻道未啟用行銷知識查詢"
ANSWER_TRUNCATION_NOTICE = "(內容過長已截斷,完整結果請用內部工具查詢)"
SLACK_NO_RESULTS_MESSAGE = "找不到相關內容。請換個關鍵字,或聯繫管理者確認資料是否已收錄。"
PAGINATION_EXPIRED_MESSAGE = "此搜尋工作階段已失效，請重新執行原搜尋。"
FACETED_SEARCH_STALE_CATALOG_MESSAGE = "搜尋條件已過期，請重新點擊「開啟條件搜尋」再試一次。"
# Shown when a Slack artifact from a superseded entry mode is used. Each names the entry that
# exists under the mode running *now*, and nothing else: no echo of what was clicked, no query, no
# token, no hint about the previous search. They are fixed literals selected by mode, never
# assembled from anything the interaction carried.
#
# There are two because there is no single correct sentence. Telling a `mention_mixed` user to type
# `/mka` sends them to a command that mode never registers, which would leave the guidance as stale
# as the button that produced it -- the user is told to do something that also does nothing.
STALE_ENTRY_MODE_MESSAGE_SLASH = "搜尋入口已更新，請輸入 `/mka` 重新開啟搜尋。"
# Shown when a modal is submitted but its reply capability is gone -- expired, spent, or never
# stored. Deliberately says nothing about which: the user's action is the same either way.
SLASH_SESSION_EXPIRED_MESSAGE = "此搜尋階段已逾時，請重新輸入 `/mka` 開啟搜尋。"
STALE_ENTRY_MODE_MESSAGE_MENTION = (
    f"此搜尋操作已失效，請重新標記 {SHOW_MORE_MENTION} 開始搜尋。"
)
# How much of a structured result the Slack surface materialises before paging over it. These are
# display capacity, not ranking: the ordered candidate set and every governance gate in front of
# it are unchanged, and raising the ceiling only lets Slack show more of the same ranked result.
# Four pages of BRAND_PAGE_SIZE brands bounds the work and the continuation held in memory. The
# parent ceiling is defined with the renderer, which has to describe it to the user when a result
# reaches it; re-exported here because this is the module that spends it.
# One merchant record contributes at most one asset per supported type, so a parent-proportional
# asset budget can never exhaust mid-brand -- which is what keeps a brand group whole on its page.
SLACK_SEARCH_ASSET_CAP = SLACK_SEARCH_PARENT_CAP * 4
# Trailing punctuation a person may add to the continuation reply; stripped before matching so
# "顯示更多。" and "顯示更多!" continue the thread, while anything else stays an ordinary query.
SHOW_MORE_TRAILING = " \t\u3000.。!！~～"
SLACK_AUDIT_HEADER = [
    "timestamp",
    "event",
    "channel_id",
    "user_id",
    "citation_count",
    "warning_count",
    "query",
]


class SlackInterfaceError(ValueError):
    """Raised when the Slack interface cannot start safely."""


@dataclass(frozen=True)
class SlackConfig:
    allowed_channel_ids: List[str] = field(default_factory=list)
    notify_owner_on_denylist: bool = False
    max_answer_chars: int = 2500
    enable_approved_asset_urls: bool = False
    enable_faceted_search: bool = False
    search_taxonomy_workbook: Optional[str] = None
    search_taxonomy_sha256: Optional[str] = None
    search_entry_mode: str = ENTRY_MODE_MENTION_MIXED
    # Which conversations may open the ``/mka`` modal. ``None`` means "no restriction", which is
    # deliberately *not* the same thing as an empty list -- see ``load_slack_config``, which refuses
    # an explicit ``[]``.
    #
    # This is a separate field from ``allowed_channel_ids`` because the two authorize different
    # things. ``allowed_channel_ids`` governs channel-*visible* disclosure: a message posted into a
    # channel is read by everyone in it, so the channel is the audience. A ``/mka`` result is
    # ephemeral and addressed to exactly one user, so the conversation it was invoked from is a
    # routing coordinate, not an audience. Reusing the channel allowlist here would block every DM
    # and every unlisted channel from a flow that discloses nothing to them.
    slash_command_allowed_channel_ids: Optional[List[str]] = None


def load_slack_config(path: Path = DEFAULT_SLACK_CONFIG_PATH) -> SlackConfig:
    path = Path(path)
    if not path.is_file():
        raise SlackInterfaceError(f"Slack 設定檔不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SlackInterfaceError(f"Slack 設定檔無法解析：{path}") from exc
    if not isinstance(payload, dict):
        raise SlackInterfaceError("Slack 設定檔必須是 JSON object。")

    allowed_channel_ids = payload.get("allowed_channel_ids", [])
    if not isinstance(allowed_channel_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in allowed_channel_ids
    ):
        raise SlackInterfaceError("allowed_channel_ids 必須是字串陣列。")
    notify_owner = payload.get("notify_owner_on_denylist", False)
    if not isinstance(notify_owner, bool):
        raise SlackInterfaceError("notify_owner_on_denylist 必須是 boolean。")
    max_answer_chars = payload.get("max_answer_chars", 2500)
    if not isinstance(max_answer_chars, int) or isinstance(max_answer_chars, bool) or max_answer_chars <= 0:
        raise SlackInterfaceError("max_answer_chars 必須是正整數。")
    enable_approved_asset_urls = payload.get("enable_approved_asset_urls", False)
    if not isinstance(enable_approved_asset_urls, bool):
        raise SlackInterfaceError("enable_approved_asset_urls 必須是 boolean。")
    enable_faceted_search = payload.get("enable_faceted_search", False)
    if not isinstance(enable_faceted_search, bool):
        raise SlackInterfaceError("enable_faceted_search 必須是 boolean。")
    search_taxonomy_workbook = payload.get("search_taxonomy_workbook")
    if search_taxonomy_workbook is not None and (
        not isinstance(search_taxonomy_workbook, str) or not search_taxonomy_workbook.strip()
    ):
        raise SlackInterfaceError("search_taxonomy_workbook 必須是非空字串。")
    search_taxonomy_sha256 = payload.get("search_taxonomy_sha256")
    if search_taxonomy_sha256 is not None and (
        not isinstance(search_taxonomy_sha256, str) or not search_taxonomy_sha256.strip()
    ):
        raise SlackInterfaceError("search_taxonomy_sha256 必須是非空字串。")
    if bool(search_taxonomy_workbook) != bool(search_taxonomy_sha256):
        raise SlackInterfaceError(
            "search_taxonomy_workbook 與 search_taxonomy_sha256 必須同時提供或同時省略，"
            "不允許只設定其中一個。"
        )
    if enable_faceted_search and not (search_taxonomy_workbook and search_taxonomy_sha256):
        raise SlackInterfaceError(
            "enable_faceted_search 為 true 時必須同時提供 search_taxonomy_workbook 與 "
            "search_taxonomy_sha256。"
        )

    # An unrecognised mode is refused rather than defaulted. Falling back to the default would mean
    # a typo in the mode name silently keeps natural-language app-mention search alive on a
    # deployment whose operator believed they had switched it off.
    search_entry_mode = payload.get("slack_search_entry_mode", ENTRY_MODE_MENTION_MIXED)
    if search_entry_mode not in SLACK_SEARCH_ENTRY_MODES:
        raise SlackInterfaceError(
            f"slack_search_entry_mode 必須是 {' 或 '.join(SLACK_SEARCH_ENTRY_MODES)} 之一；"
            f"實際為 {search_entry_mode!r}。"
        )
    if search_entry_mode == ENTRY_MODE_SLASH_FACETED_ONLY and not enable_faceted_search:
        raise SlackInterfaceError(
            f"slack_search_entry_mode 為 {ENTRY_MODE_SLASH_FACETED_ONLY} 時必須同時設定 "
            "enable_faceted_search 為 true；條件搜尋是這個模式唯一的搜尋入口，"
            "關閉它會讓 Slack 完全無法搜尋。"
        )

    slash_command_allowed_channel_ids = _load_slash_command_allowed_channel_ids(payload)

    return SlackConfig(
        allowed_channel_ids=[value.strip() for value in allowed_channel_ids],
        notify_owner_on_denylist=notify_owner,
        max_answer_chars=max_answer_chars,
        enable_approved_asset_urls=enable_approved_asset_urls,
        enable_faceted_search=enable_faceted_search,
        search_taxonomy_workbook=search_taxonomy_workbook.strip() if search_taxonomy_workbook else None,
        search_taxonomy_sha256=search_taxonomy_sha256.strip() if search_taxonomy_sha256 else None,
        search_entry_mode=search_entry_mode,
        slash_command_allowed_channel_ids=slash_command_allowed_channel_ids,
    )


def _load_slash_command_allowed_channel_ids(payload: dict) -> Optional[List[str]]:
    """Which conversations may open the ``/mka`` modal, or ``None`` for no restriction.

    An **absent** key means unrestricted, which is the product goal: a workspace member can run
    ``/mka`` from a channel or a DM, and what comes back is ephemeral to them alone. An **explicit
    empty list** is refused, because the two readings of ``[]`` -- "I meant everywhere" and "I meant
    nowhere" -- would otherwise be the same value, and picking either one silently is how an
    operator ends up with a feature that is off when they think it is on, or open when they think
    they closed it.
    """
    raw = payload.get("slash_command_allowed_channel_ids")
    if raw is None:
        return None
    if not isinstance(raw, list) or any(
        not isinstance(value, str) or not value.strip() for value in raw
    ):
        raise SlackInterfaceError("slash_command_allowed_channel_ids 必須是非空字串的陣列。")
    if not raw:
        raise SlackInterfaceError(
            "slash_command_allowed_channel_ids 不得為空陣列；"
            "要開放全部對話請直接省略這個欄位，要限制範圍請列出 conversation ID。"
        )
    return [value.strip() for value in raw]


def handle_slack_event(
    event: dict,
    config: SlackConfig,
    ask_fn: Callable = agent_ask,
    db_path: Path = DEFAULT_CONTENT_INDEX_DB,
    restricted_customers_path: Path = DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    llm_config_path: Path = DEFAULT_LLM_CONFIG_PATH,
    audit_log_path: Path = DEFAULT_SLACK_AUDIT_LOG,
    pagination_store: Optional[SlackPaginationStore] = None,
    faceted_search_enabled: bool = False,
) -> dict:
    channel_id = str(event.get("channel") or "")
    user_id = str(event.get("user") or "")
    thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
    raw_question = str(event.get("text") or "").strip()

    denied_conversation = _is_direct_message(event) or channel_id not in config.allowed_channel_ids

    if config.search_entry_mode == ENTRY_MODE_SLASH_FACETED_ONLY:
        # Direct search has moved to ``/mka``, so in this mode the text after a mention is not a
        # query and must not be written down anywhere.
        #
        # This branch deliberately sits *above* the channel-authorization check, not below it. The
        # authorization path predates the mode and records ``raw_question`` so an operator can see
        # what was asked from an unauthorized conversation -- which is the right trade for a
        # natural-language search surface, and the wrong one here: the same text that is not a
        # query in an allowed channel is not a query in a denied one either, and a DM or an
        # unlisted channel is exactly where someone types a customer name without thinking. Below
        # the check, "never persisted" would have held only for conversations that happened to be
        # allowed.
        #
        # The denial itself is still recorded, because "someone reached this bot from an
        # unauthorized conversation" is operational signal worth keeping. Only the query column is
        # dropped, and it is dropped by construction rather than by matching anything in the text:
        # nothing here inspects what the user typed, so there is no pattern to get wrong.
        if denied_conversation:
            _append_slack_audit(
                audit_log_path,
                event="slack_denied_channel",
                channel_id=channel_id,
                user_id=user_id,
                citation_count=0,
                warning_count=0,
                query="",
            )
            return _reply_dict(channel_id, thread_ts, DENIED_CHANNEL_MESSAGE)
        # An allowed-channel mention writes no row at all: guidance is neither a query nor a
        # denial, so there is nothing to record.
        return _reply_dict(channel_id, thread_ts, APP_MENTION_GUIDANCE_MESSAGE)

    if denied_conversation:
        # ``mention_mixed`` is unchanged, including this row and its query column. Here the text
        # really is an attempted search, and the pre-existing audit contract for it stands.
        _append_slack_audit(
            audit_log_path,
            event="slack_denied_channel",
            channel_id=channel_id,
            user_id=user_id,
            citation_count=0,
            warning_count=0,
            query=raw_question,
        )
        return _reply_dict(channel_id, thread_ts, DENIED_CHANNEL_MESSAGE)

    question = _strip_app_mention(raw_question)
    store = pagination_store if pagination_store is not None else default_pagination_store()
    thread_key = pagination_key(channel_id, thread_ts)
    if _is_show_more_request(question):
        # A continuation replays text this thread's own search already produced: no retrieval, no
        # governance decision and no audit row, because nothing new is being queried or disclosed.
        #
        # The mention flow has no button to carry a generation, so it asks for whichever generation
        # the lane currently holds -- which is the newest search, exactly as before. The guarantee
        # the generation adds here is narrower than in the slash flow but still real: a worker
        # cannot advance or delete a continuation that was installed after it read the lane.
        page = store.consume_current_generation(thread_key)
        return _reply_dict(channel_id, thread_ts, page or PAGINATION_EXPIRED_MESSAGE)

    if faceted_search_enabled and is_faceted_search_trigger(question):
        # Opening the button costs no retrieval, no governance decision and discloses nothing that
        # is not already visible in this channel, so no audit row is written for it either.
        return build_open_search_reply(channel_id, thread_ts)

    llm_config = load_llm_config(llm_config_path)
    answer = ask_fn(
        question,
        db_path=Path(db_path),
        filters=SearchFilters(intent="external"),
        parent_cap=SLACK_SEARCH_PARENT_CAP,
        asset_cap=SLACK_SEARCH_ASSET_CAP,
        restricted_customers_path=Path(restricted_customers_path),
        audit_log_path=Path(audit_log_path),
        provider_name=llm_config.provider,
        llm_config=llm_config,
        llm_audit_log_path=Path(audit_log_path),
        query_audit_metadata={"channel_id": channel_id, "user_id": user_id},
    )
    is_denylist_refusal = getattr(getattr(answer, "trace", None), "mode", None) == "refused"
    overlay_issue = (
        _apply_approved_asset_urls(answer, db_path)
        if config.enable_approved_asset_urls and not is_denylist_refusal
        else None
    )
    if not is_denylist_refusal:
        if overlay_issue is not None:
            _append_slack_audit(
                audit_log_path,
                event=overlay_issue,
                channel_id=channel_id,
                user_id=user_id,
                citation_count=0,
                warning_count=0,
                query="",
            )
        _append_slack_audit(
            audit_log_path,
            event="slack_qa",
            channel_id=channel_id,
            user_id=user_id,
            citation_count=len(answer.citations),
            warning_count=len(answer.warnings),
            query=question,
        )
    pages = build_structured_slack_pages(answer)
    if pages is None:
        # Any earlier continuation in this thread is superseded even when the new query is not a
        # structured search: 「顯示更多」 must never resume a result the user has moved on from.
        store.discard(thread_key)
        return _reply_dict(
            channel_id, thread_ts, _format_unstructured_slack_reply(answer, config.max_answer_chars)
        )
    store.start(thread_key, pages.pages)
    return _reply_dict(channel_id, thread_ts, pages.pages[0])


def _is_show_more_request(question: str) -> bool:
    return question.strip().rstrip(SHOW_MORE_TRAILING).strip() == SHOW_MORE_COMMAND


def _apply_approved_asset_urls(answer, db_path) -> Optional[str]:
    """Enrich an already-governed answer with approved asset URLs, or fail closed.

    Missing, malformed, unpinned or hash-mismatched authority never aborts the Slack query, and
    neither does an authority that no longer binds to the content index it was built against: the
    overlay is simply not applied and one payload-free audit code is returned. The causes are
    deliberately not distinguished here -- the distinction is a diagnostic detail, not something a
    Slack channel should learn. ValueError covers SlackOutputPreviewError (including
    ApprovedAssetUrlIndexBindingError), json.JSONDecodeError and UnicodeDecodeError.
    """
    try:
        overlay = load_index_bound_approved_asset_url_overlay(Path(db_path))
        apply_approved_asset_url_overlay(answer, overlay)
    except (OSError, csv.Error, ValueError, sqlite3.Error):
        return APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE
    return None


def format_slack_reply(answer, max_answer_chars: int) -> str:
    """The single message for this answer -- the first page when the result is paginated."""
    structured_body = format_structured_slack_reply(answer)
    if structured_body is not None:
        return structured_body
    return _format_unstructured_slack_reply(answer, max_answer_chars)


def _format_unstructured_slack_reply(answer, max_answer_chars: int) -> str:
    body = SLACK_NO_RESULTS_MESSAGE if _is_slack_abstention(answer) else _slackify_markdown(answer.answer)
    if len(body) > max_answer_chars:
        body = f"{body[:max_answer_chars].rstrip()}\n{ANSWER_TRUNCATION_NOTICE}"
    parts = [body]
    if answer.citations:
        parts.extend(["", "📚 來源:"])
        for citation in answer.citations:
            effective_date = (
                citation.last_reviewed
                or citation.updated_date
                or citation.captured_date
                or citation.publish_date
            )
            source_sheet = citation.source_sheet or "未知來源"
            source_row = citation.source_row if citation.source_row is not None else "?"
            external_usage = (
                "可對外引用"
                if metadata_allows_written_external_use(citation)
                else "不可對外引用"
            )
            parts.append(
                f"{citation.label} {citation.title} — {source_sheet} r{source_row} · "
                f"{effective_date} · {external_usage}"
            )
    if answer.warnings:
        parts.extend(["", "⚠️ 提醒:"])
        parts.extend(f"- {warning}" for warning in answer.warnings)
    return "\n".join(parts)


def post_slack_reply(client, reply: dict) -> None:
    """The single boundary every message this bot posts to Slack goes through.

    Link and media unfurling is forced off here, for every message, rather than set at each call
    site. Search results render an asset title as ``<approved-url|title>``, and Slack answers each
    such link by expanding a preview card -- article summary, "Written by" metadata, a full-width
    image, a YouTube thumbnail. A single search returns several assets, so a thread becomes
    unreadable long before the results run out. The link itself is what the user needs; the preview
    is what buries it.

    The two flags are written after the reply is unpacked, so a call site cannot re-enable
    unfurling by accident, and no caller has to remember to disable it. Nothing else about the
    message is touched: the approved asset URL, the clickable title, the blocks and the pagination
    text are passed through exactly as their builders produced them.
    """
    client.chat_postMessage(**{**reply, "unfurl_links": False, "unfurl_media": False})


def post_slack_response_url(reservation: ResponseReservation, message: dict) -> None:
    """The single boundary every slash-originated message this bot sends goes through.

    Takes a **reservation**, not a URL. The use it represents was already decremented atomically in
    the store, so possessing one is the authorization to send exactly once; there is no path here
    that could send without having paid for it first, and none that could send twice.

    Four properties are forced and cannot be overridden, because they are written *after* the
    caller's message is unpacked:

    - ``response_type="ephemeral"`` -- a slash result is addressed to the person who ran the
      command and to nobody else. ``"in_channel"`` would publish one user's search to the whole
      conversation, and no call site is allowed to ask for it;
    - ``replace_original=False`` -- each message is its own reply. Replacing would silently destroy
      the page a user is still reading;
    - ``unfurl_links`` / ``unfurl_media`` false -- the unchanged no-unfurl contract.

    The HTTP itself lives in :mod:`slack_response_urls`, which owns the capability and therefore
    owns how it is transmitted: one attempt, no retry, no redirect, no logger.
    """
    send_response_url_message(
        reservation,
        {
            **message,
            "response_type": "ephemeral",
            "replace_original": False,
            "unfurl_links": False,
            "unfurl_media": False,
        },
    )


def _response_message(text: str, blocks: Optional[List[dict]] = None) -> dict:
    """One message for the response_url boundary.

    Carries no ``channel`` and no ``user``: a response_url already addresses the interaction it
    came from, so there is nothing to route here and nothing that could be pointed elsewhere.
    """
    message: dict = {"text": text}
    if blocks:
        message["blocks"] = blocks
    return message


def _action_response_url(body: dict) -> str:
    """The response_url carried by this interaction payload, or ``""`` when there is none.

    A button click is a *new* interaction and Slack gives it its own capability, separately
    budgeted and later-expiring than the command that started the session. Preferring it keeps a
    long browsing session from exhausting the original ``/mka`` capability.
    """
    url = body.get("response_url")
    return url.strip() if isinstance(url, str) and is_valid_response_url(url) else ""


def _slash_session_key(user_id: str, session_id: str) -> str:
    """The continuation lane for one ``/mka`` invocation by one user.

    The user id is folded in rather than trusted alongside, so that two people can never share a
    lane. A session id travels in a button ``value``; a user id is derived from the interaction
    payload at click time. Combining them means a copied or guessed session id lands in the copier's
    own lane, where it finds nothing, instead of in the lane it was copied from.
    """
    return f"{user_id}:{session_id}"


def new_slash_session_id() -> str:
    """A fresh, unguessable id for one ``/mka`` invocation."""
    return secrets.token_hex(SLASH_SESSION_ID_BYTES)


def stale_entry_mode_message(search_entry_mode: str) -> str:
    """The fixed guidance for an interaction ``entrypoint_allowed_for_mode`` refused.

    Selected by the mode in force now, for the same reason that rule is: what the user should do
    next is a fact about the current configuration, not about the artifact they clicked. The
    refusal itself is decided elsewhere and is not affected by which sentence comes back.
    """
    if search_entry_mode == ENTRY_MODE_SLASH_FACETED_ONLY:
        return STALE_ENTRY_MODE_MESSAGE_SLASH
    return STALE_ENTRY_MODE_MESSAGE_MENTION


def _tell_stale_clicker_to_use_the_new_entry(body: dict, config: SlackConfig) -> None:
    """Tell whoever clicked a superseded button where the search entry went, or say nothing.

    Best-effort and deliberately inert: fixed text, no query, no token, no prefill, and nothing
    from the button that was clicked. It answers through this interaction's own response_url, so it
    reaches the clicker wherever they are and can never become a public post. With no usable
    response_url the click is simply a no-op -- a stale button doing nothing is an acceptable
    outcome and is what the remediation actually requires; the message is a courtesy on top.
    """
    reservation = single_use_reservation(_action_response_url(body))
    if reservation is None:
        return
    post_slack_response_url(
        reservation, _response_message(stale_entry_mode_message(config.search_entry_mode))
    )


def entrypoint_allowed_for_mode(search_entry_mode: str, entrypoint: str) -> bool:
    """Whether an interaction from this entry point may execute under the mode running *now*.

    Slack artifacts outlive the configuration that produced them. A "開啟條件搜尋" button posted
    into a channel last week is still sitting there, still clickable, after an operator switches
    the entry mode -- and a modal opened seconds before the switch can be submitted seconds after
    it. So an interaction's own provenance is necessary but never sufficient: the mode in force at
    execution time has to authorize it too.

    ``private_metadata["entrypoint"]`` is not trusted merely because this app wrote it. It is a
    statement about how a view was opened, which is exactly the fact that goes stale; it says what
    the interaction *is*, and this function decides whether that is currently allowed.

    The rule is symmetric, and deliberately so. Under ``slash_faceted_only`` only slash-session
    interactions execute, because the whole point of the mode is that ``/mka`` is the only search
    entry and its results are invoker-only -- a legacy mention artifact would otherwise route a
    real search back into a public channel. Under ``mention_mixed`` only mention interactions
    execute: no slash session can legitimately exist there, since ``/mka`` is not even registered,
    so anything claiming one is stale or forged. Both directions fail closed for free.
    """
    if search_entry_mode == ENTRY_MODE_SLASH_FACETED_ONLY:
        return entrypoint == ENTRYPOINT_SLASH_COMMAND
    return entrypoint == ENTRYPOINT_APP_MENTION


def _slash_entry_allowed(config: SlackConfig, channel_id: str) -> bool:
    """Whether ``/mka`` may be used from this conversation.

    Distinct from ``allowed_channel_ids`` on purpose -- see ``SlackConfig``. An unrestricted
    configuration still discloses nothing to the conversation, because everything this flow posts
    is ephemeral to the invoker. A missing conversation id fails closed rather than matching an
    empty entry.
    """
    if not channel_id:
        return False
    allowed = config.slash_command_allowed_channel_ids
    return allowed is None or channel_id in allowed


def run_slack_bot(
    config_path: Path = DEFAULT_SLACK_CONFIG_PATH,
    db_path: Path = DEFAULT_CONTENT_INDEX_DB,
    restricted_customers_path: Path = DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    llm_config_path: Path = DEFAULT_LLM_CONFIG_PATH,
    audit_log_path: Path = DEFAULT_SLACK_AUDIT_LOG,
    environ: Optional[Mapping[str, str]] = None,
    app_factory=None,
    socket_mode_handler_factory=None,
) -> None:
    config = load_slack_config(config_path)
    if not config.allowed_channel_ids:
        raise SlackInterfaceError(
            "allowed_channel_ids 為空；請先在 .mka/slack_config.json 設定啟用頻道。"
        )

    environment = environ if environ is not None else os.environ
    bot_token = environment.get("SLACK_BOT_TOKEN")
    app_token = environment.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        raise SlackInterfaceError(
            "Slack tokens 未設定；請設定 SLACK_BOT_TOKEN 與 SLACK_APP_TOKEN。"
        )

    taxonomy: Optional[SearchTaxonomy] = None
    facet_catalog: Optional[FacetCatalog] = None
    if config.enable_faceted_search:
        # Loaded exactly once, before the App or Socket Mode handler is constructed, and closed
        # over for the whole process lifetime -- never re-read per query, never re-built per Slack
        # interaction. Any failure here (missing workbook, hash mismatch, unreadable index,
        # missing or unparseable denylist) stops startup before Socket Mode opens; there is no
        # fallback that disables the feature silently or serves results without governance.
        taxonomy = load_search_taxonomy(
            workbook_path=Path(config.search_taxonomy_workbook),
            expected_sha256=config.search_taxonomy_sha256,
        )
        # Loaded here purely to fail fast. Every query path reloads it for itself, so this call's
        # value is discarded -- what matters is that a bot which cannot read its denylist never
        # reaches the point of accepting a query at all, rather than discovering the fault on the
        # first search and answering it anyway.
        load_required_governance_index(restricted_customers_path)
        facet_catalog = build_facet_catalog(
            db_path, taxonomy, restricted_customers_path=restricted_customers_path
        )

    if app_factory is None:
        from slack_bolt import App

        app_factory = App
    if socket_mode_handler_factory is None:
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        socket_mode_handler_factory = SocketModeHandler

    app = app_factory(token=bot_token)
    pagination_store = default_pagination_store()

    @app.event("app_mention")
    def receive_app_mention(event, client):
        reply = handle_slack_event(
            event,
            config=config,
            db_path=db_path,
            restricted_customers_path=restricted_customers_path,
            llm_config_path=llm_config_path,
            audit_log_path=audit_log_path,
            pagination_store=pagination_store,
            faceted_search_enabled=config.enable_faceted_search,
        )
        post_slack_reply(client, reply)

    if config.enable_faceted_search:
        _register_faceted_search_handlers(
            app,
            config=config,
            taxonomy=taxonomy,
            facet_catalog=facet_catalog,
            db_path=db_path,
            restricted_customers_path=restricted_customers_path,
            audit_log_path=audit_log_path,
            pagination_store=pagination_store,
            request_token_store=default_request_token_store(),
            response_url_store=default_response_url_store(),
        )

    socket_mode_handler_factory(app, app_token).start()


def _register_faceted_search_handlers(
    app,
    config: SlackConfig,
    taxonomy: SearchTaxonomy,
    facet_catalog: FacetCatalog,
    db_path: Path,
    restricted_customers_path: Path,
    audit_log_path: Path,
    pagination_store: SlackPaginationStore,
    request_token_store: SlackRequestTokenStore,
    response_url_store: Optional[SlackResponseUrlStore] = None,
) -> None:
    """Register the button-click and modal-submission handlers behind the faceted-search flag.

    Both entry points re-validate ``allowed_channel_ids`` independently of the original
    ``app_mention`` check: a button or a modal is a separate Slack interaction, carrying its own
    payload, and this Slack surface has exactly one channel allowlist, checked at every entry.
    """

    slash_only = config.search_entry_mode == ENTRY_MODE_SLASH_FACETED_ONLY
    if response_url_store is None:
        response_url_store = default_response_url_store()

    @app.action(OPEN_SEARCH_MODAL_ACTION_ID)
    def handle_open_faceted_search_modal(ack, body, client):
        ack()
        payload = parse_open_modal_button_value((body.get("actions") or [{}])[0].get("value"))
        # A session id in the button value is what distinguishes a slash-flow button from a
        # mention-flow one. It is a lane coordinate, not a claim about identity: who is clicking
        # and where still come from the interaction payload below, exactly as before.
        session_id = session_id_from_button_payload(payload)
        button_entrypoint = ENTRYPOINT_SLASH_COMMAND if session_id else ENTRYPOINT_APP_MENTION
        if not entrypoint_allowed_for_mode(config.search_entry_mode, button_entrypoint):
            # A button left over from a different entry mode -- still in the channel, still
            # clickable, now unusable. Refused before the modal is built, so no legacy modal can
            # come into existence under this mode at all.
            _tell_stale_clicker_to_use_the_new_entry(body, config)
            return
        if session_id:
            context = _slash_interaction_context(body)
            if context is None:
                return
            user_id, channel_id = context
            if not _slash_entry_allowed(config, channel_id):
                return
            session_key = _slash_session_key(user_id, session_id)
            thread_ts = ""
            entrypoint = ENTRYPOINT_SLASH_COMMAND
            # 調整條件 and 重新搜尋 both lead to another submission, so the session is handed the
            # capability from *this* click before the modal opens. Without the refresh the next
            # result would have to be delivered through the ageing command capability, which is
            # what runs out first in a long session. Ownership is not refreshed along with it:
            # user and channel still come from the interaction payload, checked above.
            refreshed = response_url_store.store(
                _action_response_url(body),
                owner_user_id=user_id,
                channel_id=channel_id,
                session_key=session_key,
            )
            if not refreshed and not response_url_store.remaining_uses(
                user_id=user_id, channel_id=channel_id, session_key=session_key
            ):
                # Neither a fresh capability nor a live stored one, so the submission this modal
                # would produce could not be answered and the modal is not opened.
                #
                # This is a UX check, not the security decision: the authoritative one is the
                # reservation the submission itself takes before any retrieval runs. Reserving here
                # would spend a use on a modal that may never be submitted.
                return
        else:
            # Who clicked, and where, is read from the interaction payload -- never from the
            # button's own value. The button sits in a channel where everyone who can see the
            # thread can click it, so its value states which action to take, not whose context to
            # take it in.
            context = _interaction_context(body)
            if context is None:
                return
            user_id, channel_id, thread_ts = context
            if channel_id not in config.allowed_channel_ids:
                return
            session_key = thread_ts
            entrypoint = ENTRYPOINT_APP_MENTION

        # Resolves only for the user, channel and session the token was minted in. Unknown, expired
        # and "not yours" are deliberately indistinguishable here: all three reopen an empty modal,
        # which tells a clicker nothing and is never worse than prefilling a search nobody chose.
        prefill = request_token_store.resolve(
            request_token_from_button_payload(payload),
            user_id=user_id,
            channel_id=channel_id,
            session_key=session_key,
        )
        view = build_facet_modal_view(
            facet_catalog,
            channel_id=channel_id,
            thread_ts=thread_ts,
            prefill=prefill,
            entrypoint=entrypoint,
            session_id=session_id,
        )
        client.views_open(trigger_id=body["trigger_id"], view=view)

    if slash_only:

        @app.command(SLASH_COMMAND_NAME)
        def handle_faceted_search_command(ack, body, client):
            # Acknowledged first and unconditionally: Slack gives a slash command three seconds,
            # and everything below is in-memory work against a catalog built at startup.
            ack()
            user_id = str(body.get("user_id") or "").strip()
            channel_id = str(body.get("channel_id") or "").strip()
            trigger_id = str(body.get("trigger_id") or "").strip()
            response_url = str(body.get("response_url") or "").strip()
            # ``body["text"]`` -- whatever the user typed after the command -- is deliberately never
            # read. ``/mka`` has exactly one meaning: open the modal. Treating trailing text as a
            # query would reintroduce free-text search through the one entry point that exists to
            # replace it, and would do so with text that was never shown a validation error, never
            # checked against the denylist before being echoed, and never chosen from the catalog.
            if not user_id or not channel_id or not trigger_id:
                return
            if not is_valid_response_url(response_url):
                # No reply path, so no session. Opening the modal here would let a user run a real
                # search whose result could never be delivered -- work done, governance spent, and
                # silence at the end of it. Refusing before the modal is the honest failure.
                return
            if not _slash_entry_allowed(config, channel_id):
                # Answered through this command's own capability rather than
                # ``chat.postEphemeral``: the conversations this branch exists to turn away are
                # exactly the ones the bot is least likely to be a member of, which is how the
                # denial went undelivered in UAT.
                denial = single_use_reservation(response_url)
                if denial is not None:
                    post_slack_response_url(denial, _response_message(DENIED_CHANNEL_MESSAGE))
                return
            session_id = new_slash_session_id()
            if not response_url_store.store(
                response_url,
                owner_user_id=user_id,
                channel_id=channel_id,
                session_key=_slash_session_key(user_id, session_id),
            ):
                return
            view = build_facet_modal_view(
                facet_catalog,
                channel_id=channel_id,
                prefill=None,
                entrypoint=ENTRYPOINT_SLASH_COMMAND,
                session_id=session_id,
            )
            client.views_open(trigger_id=trigger_id, view=view)

        @app.action(SHOW_MORE_ACTION_ID)
        def handle_show_more(ack, body, client):
            ack()
            context = _slash_interaction_context(body)
            if context is None:
                return
            user_id, channel_id = context
            if not _slash_entry_allowed(config, channel_id):
                return
            payload = parse_open_modal_button_value((body.get("actions") or [{}])[0].get("value"))
            session_id = session_id_from_button_payload(payload)
            if not session_id or not entrypoint_allowed_for_mode(
                config.search_entry_mode, ENTRYPOINT_SLASH_COMMAND
            ):
                return
            session_key = _slash_session_key(user_id, session_id)
            request_token = request_token_from_button_payload(payload)
            # Ownership is decided by the token store, which already binds a request to its user,
            # channel and session -- the resolved request itself is never read, and no retrieval,
            # ranking, query planning or audit row follows from this click. A non-owner (and an
            # expired session) gets the same "run the search again" answer, so the button discloses
            # nothing about whose search it belonged to.
            owns_session = (
                request_token_store.resolve(
                    request_token,
                    user_id=user_id,
                    channel_id=channel_id,
                    session_key=session_key,
                )
                is not None
            )
            generation = generation_from_button_payload(payload)
            if not generation:
                return
            # This click is its own interaction and carries its own capability. Refreshing the lane
            # with it keeps a long browsing session from spending down the original ``/mka``
            # capability, and keeps paging working after that one has expired. The reservation is
            # then taken from the store like every other send, so two concurrent clicks racing for
            # a final use produce exactly one message.
            response_url_store.store(
                _action_response_url(body),
                owner_user_id=user_id,
                channel_id=channel_id,
                session_key=session_key,
            )
            reservation = response_url_store.reserve(
                user_id=user_id, channel_id=channel_id, session_key=session_key
            )
            if reservation is None:
                return
            lane = pagination_key(channel_id, session_key)
            # Consume *and deliver* inside the lane guard. The generation check alone would still
            # allow "read a valid page, a new search installs, then send the old page" -- the check
            # passed when it was made. Holding the lane across the send means a supersede either
            # happens before this click, in which case the generation no longer matches and nothing
            # is sent, or waits until the page is out. A new search followed by an old page is not
            # reachable.
            with pagination_store.lane_operation(lane):
                page = (
                    pagination_store.consume_next_page(lane, generation) if owns_session else None
                )
                if page is None:
                    post_slack_response_url(
                        reservation, _response_message(PAGINATION_EXPIRED_MESSAGE)
                    )
                    return
                blocks = (
                    show_more_blocks(request_token, session_id, generation)
                    if request_token and pagination_store.has_more(lane, generation)
                    else None
                )
                post_slack_response_url(reservation, _response_message(page, blocks))

    @app.view(FACETED_SEARCH_MODAL_CALLBACK_ID)
    def handle_faceted_search_submission(ack, body, client, view):
        metadata = parse_open_modal_button_value(view.get("private_metadata"))
        channel_id = str(metadata.get("channel_id", ""))
        thread_ts = str(metadata.get("thread_ts", ""))
        catalog_version = str(metadata.get("catalog_version", ""))
        entrypoint = str(metadata.get("entrypoint", "") or ENTRYPOINT_APP_MENTION)
        session_id = str(metadata.get("session_id", ""))
        # The submitter comes from the payload Slack built, never from ``private_metadata``: the
        # view states which search this is, the payload states who sent it.
        user_id = str((body.get("user") or {}).get("id", ""))
        is_slash = entrypoint == ENTRYPOINT_SLASH_COMMAND

        if not entrypoint_allowed_for_mode(config.search_entry_mode, entrypoint):
            # Checked here as well as at the button, and not because the button check might be
            # skipped: a modal opened *before* an entry-mode switch is submitted *after* it, so
            # this submission never passed through today's action handler at all. Nothing below
            # runs -- no retrieval, no audit row, no message of either kind. The modal stays open
            # with a fixed explanation rather than closing silently on a search that will never
            # arrive; ``ack`` carries it, so no posting API is involved.
            ack(
                response_action="errors",
                errors={
                    FREE_TEXT_BLOCK_ID: stale_entry_mode_message(config.search_entry_mode)
                },
            )
            return

        if is_slash:
            # A slash submission is answered ephemerally, so it needs both halves of its lane. A
            # missing one fails closed rather than collapsing to an empty session key, which would
            # compare equal to every other empty one.
            if not user_id or not session_id or not _slash_entry_allowed(config, channel_id):
                ack()
                return
            session_key = _slash_session_key(user_id, session_id)
        else:
            if channel_id not in config.allowed_channel_ids:
                ack()
                return
            session_key = thread_ts

        state_values = ((view.get("state") or {}).get("values")) or {}
        thread_key = pagination_key(channel_id, session_key)

        result_reservation: Optional[ResponseReservation] = None
        try:
            request = parse_structured_search_request(state_values, catalog_version)
            validate_structured_search_request(request, facet_catalog)
        except StaleFacetCatalogError:
            ack()
            _post_search_reply(
                client,
                is_slash=is_slash,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                text=FACETED_SEARCH_STALE_CATALOG_MESSAGE,
                reservation=(
                    response_url_store.reserve(
                        user_id=user_id, channel_id=channel_id, session_key=session_key
                    )
                    if is_slash
                    else None
                ),
            )
            return
        except StructuredSearchValidationError as exc:
            # No reservation has been taken yet, deliberately. A validation error is answered
            # through ``ack`` alone and sends nothing, so spending a use here would let a handful
            # of ordinary mistakes exhaust a session that never ran a search.
            ack(response_action="errors", errors={FREE_TEXT_BLOCK_ID: str(exc)})
            return

        if is_slash:
            # Reserved, not checked, and reserved *here* -- after the request is known to be valid
            # and before any retrieval runs.
            #
            # An observational "may I reply?" is stale the moment it returns: another handler on
            # bolt's thread pool can consume the last use in the window before the send, leaving a
            # search that has already executed with nowhere to go. Taking the use now means the
            # reply is paid for before the work starts, and this same reservation is what
            # authorizes the message at the end.
            #
            # Unknown, expired, exhausted and not-yours are one outcome on purpose.
            result_reservation = response_url_store.reserve(
                user_id=user_id, channel_id=channel_id, session_key=session_key
            )
            if result_reservation is None:
                ack(
                    response_action="errors",
                    errors={FREE_TEXT_BLOCK_ID: SLASH_SESSION_EXPIRED_MESSAGE},
                )
                return

        ack()
        answer = execute_structured_search(
            request,
            db_path=db_path,
            taxonomy=taxonomy,
            restricted_customers_path=restricted_customers_path,
            audit_log_path=audit_log_path,
            parent_cap=SLACK_SEARCH_PARENT_CAP,
            asset_cap=SLACK_SEARCH_ASSET_CAP,
            # Without this a denylist hit is recorded under the bare command schema, losing the
            # channel and user the refusal must be attributable to. The query column stays empty
            # for that event by construction, so this adds attribution, not content.
            query_audit_metadata={"channel_id": channel_id, "user_id": user_id},
        )

        # This search supersedes whatever this thread was previously paging through -- including
        # when it produced no pages at all. Done before the reply is sent and on every branch
        # below, so 「顯示更多」 can never resume a result the user has already moved on from.
        pagination_store.discard(thread_key)

        refused = is_restricted_refusal(answer)
        overlay_issue = (
            _apply_approved_asset_urls(answer, db_path)
            if config.enable_approved_asset_urls and not refused
            else None
        )
        if not refused:
            if overlay_issue is not None:
                _append_slack_audit(
                    audit_log_path,
                    event=overlay_issue,
                    channel_id=channel_id,
                    user_id=user_id,
                    citation_count=0,
                    warning_count=0,
                    query="",
                )
            # Skipped entirely on a refusal, exactly as the natural-language path skips ``slack_qa``:
            # the facet selection is safe to record, but the free-text goal that hit the denylist is
            # the very text that must not be written down. ``precheck_restricted_query`` has already
            # recorded the hit itself, with an empty query column.
            _append_slack_audit(
                audit_log_path,
                event="slack_faceted_search",
                channel_id=channel_id,
                user_id=user_id,
                citation_count=len(answer.citations),
                warning_count=len(answer.warnings),
                query=_structured_audit_query(request, catalog_version),
            )

        # The instruction a page ends with has to match the entry point that produced it: the
        # slash flow answers ephemerally, where a thread reply would never reach this bot.
        pages = build_structured_slack_pages(
            answer, SHOW_MORE_BUTTON_HINT if is_slash else SHOW_MORE_THREAD_REPLY_HINT
        )
        # ``start`` installs this search as the lane's newest generation and returns its id --
        # including when the result fits one page, where it installs no continuation but still
        # supersedes whatever was there. A button from the previous search is stale either way.
        generation = ""
        if pages is None:
            body_text = _format_unstructured_slack_reply(answer, config.max_answer_chars)
        else:
            generation = pagination_store.start(thread_key, pages.pages)
            body_text = pages.pages[0]
        _post_search_reply(
            client,
            is_slash=is_slash,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            text=body_text,
            reservation=result_reservation,
        )

        if refused:
            # A refused query's text must not survive anywhere shared, and the token store is shared
            # across every viewer of this channel. So nothing is stored and nothing is offered to
            # reopen -- only a way back to a blank modal. Storing it "just for the owner" would
            # still be storing it.
            if is_slash:
                _post_search_reply(
                    client,
                    is_slash=True,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    user_id=user_id,
                    text=RESTART_SEARCH_TEXT,
                    blocks=restart_search_blocks(session_id),
                    reservation=response_url_store.reserve(
                        user_id=user_id, channel_id=channel_id, session_key=session_key
                    ),
                )
            else:
                post_slack_reply(client, build_restart_search_message(channel_id, thread_ts))
            return

        request_token = request_token_store.store(
            request, owner_user_id=user_id, channel_id=channel_id, session_key=session_key
        )
        if is_slash:
            # One follow-up message rather than two: 「顯示更多」 only when a page is actually
            # waiting, then 「調整條件」, which is always available.
            follow_up: List[dict] = []
            if pages is not None and len(pages.pages) > 1:
                follow_up.extend(show_more_blocks(request_token, session_id, generation))
            follow_up.extend(adjust_filters_blocks(request_token, session_id))
            _post_search_reply(
                client,
                is_slash=True,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                text=ADJUST_FILTERS_TEXT,
                blocks=follow_up,
                # A second reservation, taken after the result is out. The result is the message
                # that had to be guaranteed before retrieval ran; this one is an affordance, and if
                # the budget cannot cover it the search still reached the user.
                reservation=response_url_store.reserve(
                    user_id=user_id, channel_id=channel_id, session_key=session_key
                ),
            )
            return
        post_slack_reply(
            client, build_adjust_filters_message(channel_id, thread_ts, request_token)
        )


def _post_search_reply(
    client,
    *,
    is_slash: bool,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    text: str,
    blocks: Optional[List[dict]] = None,
    reservation: Optional[ResponseReservation] = None,
) -> bool:
    """Send one search-flow message through the boundary its entry point requires.

    A slash-initiated search is visible to the person who ran it and to nobody else, so it never
    takes the in-channel path. Routing is decided from the entry point recorded when the modal was
    opened, not from which fields happen to be populated, so a message cannot become public because
    a thread timestamp was missing.

    Returns whether the message was sent. The slash path sends only against a reservation the
    caller already holds -- it never reaches into the store itself, so there is no second place
    where a use could be spent without being accounted for.
    """
    if is_slash:
        if reservation is None:
            return False
        post_slack_response_url(reservation, _response_message(text, blocks))
        return True
    post_slack_reply(client, _reply_dict(channel_id, thread_ts, text))
    return True


def _slash_interaction_context(body: dict) -> Optional[tuple]:
    """Who clicked and in which conversation, for a button in an ephemeral slash-flow message.

    Returns ``(user_id, channel_id)``, or ``None`` when either is missing.

    Deliberately does not require a ``thread_ts``: an ephemeral message is not a threaded reply and
    Slack sends no thread timestamp for one, so ``_interaction_context`` -- which fails closed
    without it, correctly, for the mention flow -- would reject every slash-flow click. The lane is
    carried by the session id instead, and is always combined with this user id before use.
    """
    container = body.get("container") or {}
    channel = body.get("channel") or {}
    user_id = str((body.get("user") or {}).get("id") or "").strip()
    channel_id = str(container.get("channel_id") or channel.get("id") or "").strip()
    if not user_id or not channel_id:
        return None
    return user_id, channel_id


def _interaction_context(body: dict) -> Optional[tuple]:
    """Who clicked and where, read from the Slack interaction payload itself.

    Returns ``(user_id, channel_id, thread_ts)``, or ``None`` when any of the three is missing.

    Deliberately does not read the button's ``value``: that is content this bot posted into a
    channel, echoed back by whoever clicked it, so it describes the button rather than the person
    now pressing it. Every channel member sees the same value. Only the payload Slack constructs at
    click time says who is acting and in which conversation.

    ``container`` is preferred over the top-level ``channel`` because it is the message the button
    actually lives in. ``thread_ts`` falls back to ``message_ts`` for a button that is not itself a
    threaded reply -- in that case the message's own timestamp is the thread root any reply would
    use. A missing piece fails closed rather than defaulting to an empty string, because an empty
    value would compare equal to an empty stored value and quietly turn the context check off.
    """
    container = body.get("container") or {}
    channel = body.get("channel") or {}
    user_id = str((body.get("user") or {}).get("id") or "").strip()
    channel_id = str(container.get("channel_id") or channel.get("id") or "").strip()
    thread_ts = str(container.get("thread_ts") or container.get("message_ts") or "").strip()
    if not user_id or not channel_id or not thread_ts:
        return None
    return user_id, channel_id, thread_ts


def _structured_audit_query(request: StructuredSearchRequest, catalog_version: str) -> str:
    parts = [f"catalog_version={catalog_version}"]
    if request.interview_years:
        parts.append("years=" + ",".join(str(year) for year in request.interview_years))
    if request.sales_category_lv2:
        parts.append("lv2=" + "|".join(request.sales_category_lv2))
    if request.content_tags:
        parts.append("tags=" + "|".join(request.content_tags))
    if request.free_text:
        parts.append(f"text={request.free_text}")
    return " ".join(parts)


def _append_slack_audit(
    path: Path,
    event: str,
    channel_id: str,
    user_id: str,
    citation_count: int,
    warning_count: int,
    query: str,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    header = []
    if exists:
        with path.open("r", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not exists:
            header = SLACK_AUDIT_HEADER
            writer.writerow(header)
        writer.writerow(
            _slack_audit_row(
                header,
                event=event,
                channel_id=channel_id,
                user_id=user_id,
                citation_count=citation_count,
                warning_count=warning_count,
                query=query,
            )
        )


def _slack_audit_row(
    header: List[str],
    event: str,
    channel_id: str,
    user_id: str,
    citation_count: int,
    warning_count: int,
    query: str,
) -> List[object]:
    safe_query = query if event != "denylist_query_hit" else ""
    timestamp = _utc_now()
    if header == SLACK_AUDIT_HEADER:
        return [timestamp, event, channel_id, user_id, citation_count, warning_count, safe_query]
    if header == [
        "timestamp",
        "batch_id",
        "action",
        "add",
        "update",
        "archive",
        "operator",
        "plan_path",
    ]:
        return [
            timestamp,
            channel_id,
            event,
            citation_count,
            warning_count,
            0,
            user_id,
            safe_query,
        ]
    if header == ["timestamp", "command", "event", "match_count"]:
        details = json.dumps(
            {
                "channel_id": channel_id,
                "user_id": user_id,
                "citation_count": citation_count,
                "warning_count": warning_count,
                "query": safe_query,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return [timestamp, "slack-bot", event, details]
    if header == ["timestamp", "command", "index_count", "db_path"]:
        details = json.dumps(
            {
                "channel_id": channel_id,
                "user_id": user_id,
                "warning_count": warning_count,
                "query": safe_query,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return [timestamp, f"slack-bot:{event}", citation_count, details]
    if header == [
        "timestamp",
        "command",
        "provider",
        "model",
        "payload_chunk_count",
        "internal_removed_count",
    ]:
        context = json.dumps(
            {"channel_id": channel_id, "user_id": user_id, "query": safe_query},
            ensure_ascii=False,
            sort_keys=True,
        )
        return [timestamp, f"slack-bot:{event}", "slack", context, citation_count, warning_count]
    raise SlackInterfaceError(f"不支援的 audit log header：{header}")


def _reply_dict(channel_id: str, thread_ts: str, text: str) -> dict:
    return {"channel": channel_id, "thread_ts": thread_ts, "text": text}


def _strip_app_mention(text: str) -> str:
    return re.sub(r"^\s*<@[A-Za-z0-9]+>\s*", "", text).strip()


def _is_slack_abstention(answer) -> bool:
    trace_mode = getattr(getattr(answer, "trace", None), "mode", None)
    generated = getattr(answer, "generated", answer)
    structured = getattr(generated, "structured_result", None)
    if structured is not None and structured.execution_blocked:
        return False
    return not answer.citations and trace_mode != "refused"


def _slackify_markdown(text: str) -> str:
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"(?<!\S)#{1,6}\s+", "", raw_line.strip())
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            cells = [cell for cell in cells if not re.fullmatch(r":?-{3,}:?", cell)]
            line = " · ".join(cells)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _is_direct_message(event: dict) -> bool:
    return event.get("channel_type") in {"im", "mpim"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
