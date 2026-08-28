from __future__ import annotations

import csv
import json
import os
import re
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
    FREE_TEXT_BLOCK_ID,
    OPEN_SEARCH_MODAL_ACTION_ID,
    FACETED_SEARCH_MODAL_CALLBACK_ID,
    build_adjust_filters_message,
    build_facet_modal_view,
    build_open_search_reply,
    build_restart_search_message,
    is_faceted_search_trigger,
    parse_open_modal_button_value,
    parse_structured_search_request,
    request_token_from_button_payload,
)
from .slack_request_tokens import SlackRequestTokenStore, default_request_token_store
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
    SHOW_MORE_COMMAND,
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
    return SlackConfig(
        allowed_channel_ids=[value.strip() for value in allowed_channel_ids],
        notify_owner_on_denylist=notify_owner,
        max_answer_chars=max_answer_chars,
        enable_approved_asset_urls=enable_approved_asset_urls,
        enable_faceted_search=enable_faceted_search,
        search_taxonomy_workbook=search_taxonomy_workbook.strip() if search_taxonomy_workbook else None,
        search_taxonomy_sha256=search_taxonomy_sha256.strip() if search_taxonomy_sha256 else None,
    )


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

    if _is_direct_message(event) or channel_id not in config.allowed_channel_ids:
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
        page = store.next_page(thread_key)
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
) -> None:
    """Register the button-click and modal-submission handlers behind the faceted-search flag.

    Both entry points re-validate ``allowed_channel_ids`` independently of the original
    ``app_mention`` check: a button or a modal is a separate Slack interaction, carrying its own
    payload, and this Slack surface has exactly one channel allowlist, checked at every entry.
    """

    @app.action(OPEN_SEARCH_MODAL_ACTION_ID)
    def handle_open_faceted_search_modal(ack, body, client):
        ack()
        # Who clicked, and where, is read from the interaction payload -- never from the button's
        # own value. The button sits in a channel where everyone who can see the thread can click
        # it, so its value states which action to take, not whose context to take it in.
        context = _interaction_context(body)
        if context is None:
            return
        user_id, channel_id, thread_ts = context
        if channel_id not in config.allowed_channel_ids:
            return
        payload = parse_open_modal_button_value((body.get("actions") or [{}])[0].get("value"))
        # Resolves only for the user, channel and thread the token was minted in. Unknown, expired
        # and "not yours" are deliberately indistinguishable here: all three reopen an empty modal,
        # which tells a clicker nothing and is never worse than prefilling a search nobody chose.
        prefill = request_token_store.resolve(
            request_token_from_button_payload(payload),
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        view = build_facet_modal_view(
            facet_catalog, channel_id=channel_id, thread_ts=thread_ts, prefill=prefill
        )
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view(FACETED_SEARCH_MODAL_CALLBACK_ID)
    def handle_faceted_search_submission(ack, body, client, view):
        metadata = parse_open_modal_button_value(view.get("private_metadata"))
        channel_id = str(metadata.get("channel_id", ""))
        thread_ts = str(metadata.get("thread_ts", ""))
        catalog_version = str(metadata.get("catalog_version", ""))

        if channel_id not in config.allowed_channel_ids:
            ack()
            return

        state_values = ((view.get("state") or {}).get("values")) or {}
        request = parse_structured_search_request(state_values, catalog_version)
        thread_key = pagination_key(channel_id, thread_ts)

        try:
            validate_structured_search_request(request, facet_catalog)
        except StaleFacetCatalogError:
            ack()
            post_slack_reply(
                client, _reply_dict(channel_id, thread_ts, FACETED_SEARCH_STALE_CATALOG_MESSAGE)
            )
            return
        except StructuredSearchValidationError as exc:
            ack(response_action="errors", errors={FREE_TEXT_BLOCK_ID: str(exc)})
            return

        ack()
        user_id = str((body.get("user") or {}).get("id", ""))
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

        pages = build_structured_slack_pages(answer)
        if pages is None:
            post_slack_reply(
                client,
                _reply_dict(
                    channel_id,
                    thread_ts,
                    _format_unstructured_slack_reply(answer, config.max_answer_chars),
                ),
            )
        else:
            pagination_store.start(thread_key, pages.pages)
            post_slack_reply(client, _reply_dict(channel_id, thread_ts, pages.pages[0]))

        if refused:
            # A refused query's text must not survive anywhere shared, and the token store is shared
            # across every viewer of this channel. So nothing is stored and nothing is offered to
            # reopen -- only a way back to a blank modal. Storing it "just for the owner" would
            # still be storing it.
            post_slack_reply(client, build_restart_search_message(channel_id, thread_ts))
            return

        request_token = request_token_store.store(
            request, owner_user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
        )
        post_slack_reply(
            client, build_adjust_filters_message(channel_id, thread_ts, request_token)
        )


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
