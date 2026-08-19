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


DEFAULT_SLACK_CONFIG_PATH = Path(".mka/slack_config.json")
DEFAULT_SLACK_AUDIT_LOG = Path("reports/audit_log.csv")
# Payload-free audit code recorded when approved URL authority cannot be verified. It carries no
# path, hash, exception text or CSV content, and is never shown to the Slack user.
APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE = "APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE"
DENIED_CHANNEL_MESSAGE = "此頻道未啟用行銷知識查詢"
ANSWER_TRUNCATION_NOTICE = "(內容過長已截斷,完整結果請用內部工具查詢)"
SLACK_NO_RESULTS_MESSAGE = "找不到相關內容。請換個關鍵字,或聯繫管理者確認資料是否已收錄。"
PAGINATION_EXPIRED_MESSAGE = "此搜尋工作階段已失效，請重新執行原搜尋。"
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
    return SlackConfig(
        allowed_channel_ids=[value.strip() for value in allowed_channel_ids],
        notify_owner_on_denylist=notify_owner,
        max_answer_chars=max_answer_chars,
        enable_approved_asset_urls=enable_approved_asset_urls,
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
    client.chat_postMessage(**reply)


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
        )
        post_slack_reply(client, reply)

    socket_mode_handler_factory(app, app_token).start()


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
