"""Contract tests for wiring the faceted-search MVP into slack_interface.py.

Covers: the new ``SlackConfig`` fields and their validation, the default-OFF behaviour being a
strict no-op, the "@Bot 搜尋" trigger reply, ``run_slack_bot`` loading the taxonomy/facet catalog
exactly once and registering the button/modal handlers only behind the flag, and the two new
handlers' own channel re-validation, staleness handling, empty-submission errors, and successful
search + "調整條件" follow-up.

A hand-built ``FakeApp``/``FakeSocketModeHandler`` stand in for ``slack_bolt`` -- the same
dependency-injection seam ``run_slack_bot`` already exposes for its pre-existing ``app_mention``
tests -- so none of this touches a real Slack connection.
"""

import csv
import json
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata
from marketing_knowledge_agent.search_taxonomy import SearchTaxonomyError
from marketing_knowledge_agent.slack_faceted_search import (
    ALL_YEARS_OPTION_VALUE,
    APP_MENTION_GUIDANCE_MESSAGE,
    ENTRYPOINT_APP_MENTION,
    ENTRYPOINT_SLASH_COMMAND,
    SHOW_MORE_ACTION_ID,
    SLASH_COMMAND_NAME,
    CONTENT_TAGS_ACTION_ID,
    CONTENT_TAGS_BLOCK_ID,
    FACETED_SEARCH_MODAL_CALLBACK_ID,
    FREE_TEXT_ACTION_ID,
    FREE_TEXT_BLOCK_ID,
    INTERVIEW_YEARS_ACTION_ID,
    INTERVIEW_YEARS_BLOCK_ID,
    OPEN_SEARCH_MODAL_ACTION_ID,
    SALES_CATEGORY_LV2_ACTION_ID,
    SALES_CATEGORY_LV2_BLOCK_ID,
)
from marketing_knowledge_agent.slack_interface import (
    APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE,
    DENIED_CHANNEL_MESSAGE,
    ENTRY_MODE_MENTION_MIXED,
    ENTRY_MODE_SLASH_FACETED_ONLY,
    FACETED_SEARCH_STALE_CATALOG_MESSAGE,
    PAGINATION_EXPIRED_MESSAGE,
    SLASH_SESSION_EXPIRED_MESSAGE,
    STALE_ENTRY_MODE_MESSAGE_MENTION,
    STALE_ENTRY_MODE_MESSAGE_SLASH,
    SlackConfig,
    SlackInterfaceError,
    _slash_session_key,
    entrypoint_allowed_for_mode,
    stale_entry_mode_message,
    handle_slack_event,
    load_slack_config,
    run_slack_bot,
)
from marketing_knowledge_agent.slack_pagination import SlackPaginationStore
from marketing_knowledge_agent.slack_request_tokens import SlackRequestTokenStore
from marketing_knowledge_agent.slack_response_urls import MAX_USES as MAX_RESPONSE_USES
from marketing_knowledge_agent.structured_search import (
    StructuredSearchGovernanceError,
    StructuredSearchRequest,
)

from test_search_taxonomy import write_taxonomy_workbook


SALES_CATEGORY_ROWS = [
    ["Sales Category LV1", "Sales Category LV1 擴充詞", "Sales Category LV2", "Sales Category LV2 擴充詞"],
    ["居家生活", None, "居家生活相關", None],
    [None, None, "食品/飲料", "美食, 餐飲"],
]
CONTENT_TAG_ROWS = [
    ["內容相關標籤", "內容相關標籤 擴充詞", None],
    ["會員經營", "會員回購", None],
]


# --------------------------------------------------------------------------------------
# Fakes standing in for slack_bolt
# --------------------------------------------------------------------------------------


class FakeApp:
    def __init__(self, token=None):
        self.token = token
        self.events = {}
        self.actions = {}
        self.views = {}
        self.commands = {}

    def event(self, name):
        def register(fn):
            self.events[name] = fn
            return fn

        return register

    def command(self, name):
        def register(fn):
            self.commands[name] = fn
            return fn

        return register

    def action(self, action_id):
        def register(fn):
            self.actions[action_id] = fn
            return fn

        return register

    def view(self, callback_id):
        def register(fn):
            self.views[callback_id] = fn
            return fn

        return register


class FakeSocketModeHandler:
    def __init__(self, app, app_token):
        self.app = app
        self.app_token = app_token
        self.started = False

    def start(self):
        self.started = True


class FakeAck:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


# A reserved fake capability. Never a real URL: the point of these tests is that this string does
# not escape into metadata, buttons, audit rows or anything a user can see, so it has to be
# recognisable and worthless.
FAKE_RESPONSE_URL = "https://hooks.slack.com/commands/TEST/SECRET_CAPABILITY"
CAPABILITY_SECRET = "SECRET_CAPABILITY"


class FakeSlackClient:
    def __init__(self):
        self.opened_views = []
        self.messages = []

    def views_open(self, trigger_id, view):
        self.opened_views.append({"trigger_id": trigger_id, "view": view})

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)


class ResponseRecorder:
    """Captures what the slash flow sends, in place of the response_url boundary.

    Patched over ``slack_interface.post_slack_response_url`` so a test sees the URL that was spent
    and the message that went with it, without any HTTP.
    """

    def __init__(self):
        self.sent = []

    def __call__(self, reservation, message):
        # Spending here mirrors the boundary: a reservation authorizes exactly one send, and a
        # recorder that did not spend it would let a test pass where production would raise.
        self.sent.append({"url": reservation.spend(), **message})

    @property
    def texts(self):
        return [m.get("text") for m in self.sent]


def _capturing_app_factory(container):
    def factory(token=None):
        app = FakeApp(token)
        container["app"] = app
        return app

    return factory


def _capturing_socket_mode_handler_factory(container):
    def factory(app, app_token):
        handler = FakeSocketModeHandler(app, app_token)
        container["handler"] = handler
        return handler

    return factory


def _never_called_factory(label):
    def factory(*args, **kwargs):
        raise AssertionError(f"{label} must not be constructed when startup should fail first")

    return factory


# --------------------------------------------------------------------------------------
# Content index + taxonomy fixtures
# --------------------------------------------------------------------------------------


def _metadata(brand_name, handle, lv2, tags, year, source_row=1):
    metadata = DocumentMetadata(
        title=brand_name,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 1, 1),
        source_path=f"商家夥伴案例資料庫:{source_row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=source_row,
        brand_name=brand_name,
        merchant_handle=handle,
        merchant_status="現有商家",
        interview_year=year,
        sales_category_lv2=lv2,
        content_tags=list(tags),
        article_title=brand_name,
        data_classification="public",
        can_quote_externally=True,
    )
    return metadata, brand_name


def _build_index(tmp_path):
    records = [
        _metadata("莉朵花藝", "lido", "居家生活相關", ["會員經營"], 2025, source_row=1),
        _metadata("大春煉皂", "dachun", "食品/飲料", ["會員經營"], 2024, source_row=2),
    ]
    documents = [
        Document(id=f"doc-{index}", metadata=metadata, content=content)
        for index, (metadata, content) in enumerate(records, start=1)
    ]
    db_path = tmp_path / "content_index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return db_path


def _write_taxonomy(tmp_path):
    path = tmp_path / "taxonomy.xlsx"
    sha256 = write_taxonomy_workbook(path, sales_rows=SALES_CATEGORY_ROWS, tag_rows=CONTENT_TAG_ROWS)
    return path, sha256


def _write_slack_config(
    tmp_path,
    db_path=None,
    workbook_path=None,
    sha256=None,
    enable=True,
    enable_approved_asset_urls=None,
    entry_mode=None,
    slash_allowed_channel_ids=None,
):
    config = {"allowed_channel_ids": ["C123"], "enable_faceted_search": enable}
    if entry_mode is not None:
        config["slack_search_entry_mode"] = entry_mode
    if slash_allowed_channel_ids is not None:
        config["slash_command_allowed_channel_ids"] = slash_allowed_channel_ids
    if workbook_path is not None:
        config["search_taxonomy_workbook"] = str(workbook_path)
    if sha256 is not None:
        config["search_taxonomy_sha256"] = sha256
    if enable_approved_asset_urls is not None:
        config["enable_approved_asset_urls"] = enable_approved_asset_urls
    path = tmp_path / "slack_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _action_body(
    *, user_id="U1", channel_id="C123", thread_ts="1", value=None, trigger_id="T1"
):
    """A block_actions payload shaped the way Slack actually sends one.

    Real payloads always carry ``user``, ``container`` and ``channel``; the handler reads the
    interaction context from those rather than from the button's own ``value``, because the value
    is content the bot posted into a channel and every member sees the same copy of it.
    """
    return {
        "type": "block_actions",
        "trigger_id": trigger_id,
        "user": {"id": user_id},
        "container": {
            "type": "message",
            "channel_id": channel_id,
            "message_ts": "999.1",
            "thread_ts": thread_ts,
            "is_ephemeral": False,
        },
        "channel": {"id": channel_id},
        "response_url": "https://hooks.slack.com/actions/TEST/SECRET_CAPABILITY",
        "actions": [{
            "type": "button",
            "action_id": OPEN_SEARCH_MODAL_ACTION_ID,
            "value": value if value is not None else json.dumps({}),
        }],
    }


def _denylist(tmp_path, brand_names=()):
    """A loadable restricted-customer denylist. Required by every faceted-search entry point."""
    path = tmp_path / "restricted_customers.json"
    path.write_text(json.dumps([{"brand_name": n} for n in brand_names]), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# SlackConfig / load_slack_config
# --------------------------------------------------------------------------------------


def test_legacy_config_defaults_faceted_search_to_disabled(tmp_path):
    path = tmp_path / "slack_config.json"
    path.write_text(json.dumps({"allowed_channel_ids": ["C1"]}), encoding="utf-8")

    config = load_slack_config(path)

    assert config.enable_faceted_search is False
    assert config.search_taxonomy_workbook is None
    assert config.search_taxonomy_sha256 is None


def test_a_lone_workbook_path_without_a_hash_is_refused(tmp_path):
    path = tmp_path / "slack_config.json"
    path.write_text(
        json.dumps({"allowed_channel_ids": ["C1"], "search_taxonomy_workbook": "/tmp/x.xlsx"}),
        encoding="utf-8",
    )
    with pytest.raises(SlackInterfaceError, match="同時提供或同時省略"):
        load_slack_config(path)


def test_a_lone_hash_without_a_workbook_path_is_refused(tmp_path):
    path = tmp_path / "slack_config.json"
    path.write_text(
        json.dumps({"allowed_channel_ids": ["C1"], "search_taxonomy_sha256": "a" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(SlackInterfaceError, match="同時提供或同時省略"):
        load_slack_config(path)


def test_enabling_the_flag_without_the_workbook_pair_is_refused(tmp_path):
    path = tmp_path / "slack_config.json"
    path.write_text(
        json.dumps({"allowed_channel_ids": ["C1"], "enable_faceted_search": True}), encoding="utf-8"
    )
    with pytest.raises(SlackInterfaceError, match="enable_faceted_search"):
        load_slack_config(path)


def test_enabling_the_flag_with_the_workbook_pair_succeeds(tmp_path):
    path = _write_slack_config(tmp_path, workbook_path="/tmp/taxonomy.xlsx", sha256="a" * 64)

    config = load_slack_config(path)

    assert config.enable_faceted_search is True
    assert config.search_taxonomy_workbook == "/tmp/taxonomy.xlsx"
    assert config.search_taxonomy_sha256 == "a" * 64


# --------------------------------------------------------------------------------------
# handle_slack_event trigger behaviour
# --------------------------------------------------------------------------------------


def test_flag_disabled_leaves_existing_behaviour_completely_unchanged(tmp_path):
    calls = []

    def fake_ask(question, **kwargs):
        calls.append(question)
        return _minimal_answer()

    event = {"text": "<@BOT> 搜尋", "channel": "C123", "user": "U1", "ts": "1"}
    reply = handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=fake_ask,
        audit_log_path=tmp_path / "audit.csv",
        faceted_search_enabled=False,
    )

    assert calls == ["搜尋"]
    assert "blocks" not in reply


def test_flag_enabled_and_trigger_phrase_opens_the_modal_button_without_calling_ask_fn(tmp_path):
    calls = []

    def fake_ask(question, **kwargs):
        calls.append(question)
        return _minimal_answer()

    for phrase in ("搜尋", "條件搜尋"):
        event = {"text": f"<@BOT> {phrase}", "channel": "C123", "user": "U1", "ts": "1"}
        reply = handle_slack_event(
            event,
            config=SlackConfig(allowed_channel_ids=["C123"]),
            ask_fn=fake_ask,
            audit_log_path=tmp_path / "audit.csv",
            faceted_search_enabled=True,
        )
        assert "blocks" in reply
        assert any(
            element.get("action_id") == OPEN_SEARCH_MODAL_ACTION_ID
            for block in reply["blocks"]
            if block.get("type") == "actions"
            for element in block["elements"]
        )
    assert calls == []


def test_flag_enabled_but_ordinary_question_still_calls_ask_fn(tmp_path):
    calls = []

    def fake_ask(question, **kwargs):
        calls.append(question)
        return _minimal_answer()

    event = {"text": "<@BOT> 大春煉皂", "channel": "C123", "user": "U1", "ts": "1"}
    reply = handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=fake_ask,
        audit_log_path=tmp_path / "audit.csv",
        faceted_search_enabled=True,
    )

    assert calls == ["大春煉皂"]
    assert "blocks" not in reply


def test_show_more_still_takes_priority_when_flag_is_enabled(tmp_path):
    store = SlackPaginationStore()
    generation = store.start(("C123", "1"), ["page one", "page two"])

    def fake_ask(question, **kwargs):
        raise AssertionError("顯示更多 must never trigger a new search")

    event = {"text": "<@BOT> 顯示更多", "channel": "C123", "user": "U1", "ts": "1"}
    reply = handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=fake_ask,
        audit_log_path=tmp_path / "audit.csv",
        pagination_store=store,
        faceted_search_enabled=True,
    )

    assert reply["text"] == "page two"


def _minimal_answer():
    from marketing_knowledge_agent.models import GeneratedAnswer

    return GeneratedAnswer(question="q", answer="a", citations=[])


# --------------------------------------------------------------------------------------
# run_slack_bot wiring
# --------------------------------------------------------------------------------------


def test_flag_disabled_registers_no_faceted_search_handlers(tmp_path, monkeypatch):
    config_path = tmp_path / "slack_config.json"
    config_path.write_text(json.dumps({"allowed_channel_ids": ["C123"]}), encoding="utf-8")
    container = {}

    run_slack_bot(
        config_path=config_path,
        environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
        app_factory=_capturing_app_factory(container),
        socket_mode_handler_factory=_capturing_socket_mode_handler_factory(container),
    )

    app = container["app"]
    assert app.actions == {}
    assert app.views == {}
    assert "app_mention" in app.events


def test_flag_enabled_loads_taxonomy_and_builds_catalog_and_registers_handlers(tmp_path):
    db_path = _build_index(tmp_path)
    workbook_path, sha256 = _write_taxonomy(tmp_path)
    config_path = _write_slack_config(tmp_path, workbook_path=workbook_path, sha256=sha256)
    container = {}

    run_slack_bot(
        config_path=config_path,
        db_path=db_path,
        restricted_customers_path=_denylist(tmp_path),
        audit_log_path=tmp_path / "audit.csv",
        environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
        app_factory=_capturing_app_factory(container),
        socket_mode_handler_factory=_capturing_socket_mode_handler_factory(container),
    )

    app = container["app"]
    assert OPEN_SEARCH_MODAL_ACTION_ID in app.actions
    assert FACETED_SEARCH_MODAL_CALLBACK_ID in app.views
    assert container["handler"].started is True


def test_flag_enabled_fails_closed_on_sha_mismatch_before_socket_mode(tmp_path):
    db_path = _build_index(tmp_path)
    workbook_path, _sha256 = _write_taxonomy(tmp_path)
    config_path = _write_slack_config(tmp_path, workbook_path=workbook_path, sha256="0" * 64)

    with pytest.raises(SearchTaxonomyError):
        run_slack_bot(
            config_path=config_path,
            db_path=db_path,
            restricted_customers_path=_denylist(tmp_path),
            environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
            app_factory=_never_called_factory("app_factory"),
            socket_mode_handler_factory=_never_called_factory("socket_mode_handler_factory"),
        )


def test_flag_enabled_fails_closed_on_missing_workbook_before_socket_mode(tmp_path):
    db_path = _build_index(tmp_path)
    config_path = _write_slack_config(
        tmp_path, workbook_path=tmp_path / "absent.xlsx", sha256="a" * 64
    )

    with pytest.raises(SearchTaxonomyError):
        run_slack_bot(
            config_path=config_path,
            db_path=db_path,
            restricted_customers_path=_denylist(tmp_path),
            environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
            app_factory=_never_called_factory("app_factory"),
            socket_mode_handler_factory=_never_called_factory("socket_mode_handler_factory"),
        )


def test_flag_enabled_fails_closed_on_missing_denylist_before_socket_mode(tmp_path):
    """A bot that cannot read its denylist must never reach the point of accepting a query."""
    db_path = _build_index(tmp_path)
    workbook_path, sha256 = _write_taxonomy(tmp_path)
    config_path = _write_slack_config(tmp_path, workbook_path=workbook_path, sha256=sha256)

    with pytest.raises(StructuredSearchGovernanceError, match="denylist"):
        run_slack_bot(
            config_path=config_path,
            db_path=db_path,
            restricted_customers_path=tmp_path / "absent_restricted.json",
            environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
            app_factory=_never_called_factory("app_factory"),
            socket_mode_handler_factory=_never_called_factory("socket_mode_handler_factory"),
        )


def test_flag_enabled_fails_closed_on_malformed_denylist_before_socket_mode(tmp_path):
    db_path = _build_index(tmp_path)
    workbook_path, sha256 = _write_taxonomy(tmp_path)
    config_path = _write_slack_config(tmp_path, workbook_path=workbook_path, sha256=sha256)
    broken = tmp_path / "broken_restricted.json"
    broken.write_text("{not json at all", encoding="utf-8")

    with pytest.raises(StructuredSearchGovernanceError, match="denylist"):
        run_slack_bot(
            config_path=config_path,
            db_path=db_path,
            restricted_customers_path=broken,
            environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
            app_factory=_never_called_factory("app_factory"),
            socket_mode_handler_factory=_never_called_factory("socket_mode_handler_factory"),
        )


def test_flag_disabled_still_starts_without_a_denylist_file(tmp_path):
    """The fail-closed requirement is scoped to the faceted surface, not to the whole bot.

    The natural-language path's existing "denylist missing -> warn on the answer" behaviour is a
    frozen contract this WP must not change; only the new surface refuses to start without one.
    """
    config_path = tmp_path / "slack_config.json"
    config_path.write_text(json.dumps({"allowed_channel_ids": ["C123"]}), encoding="utf-8")
    container = {}

    run_slack_bot(
        config_path=config_path,
        db_path=_build_index(tmp_path),
        restricted_customers_path=tmp_path / "absent_restricted.json",
        environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
        app_factory=_capturing_app_factory(container),
        socket_mode_handler_factory=_capturing_socket_mode_handler_factory(container),
    )

    assert container["handler"].started is True
    assert container["app"].actions == {}


# --------------------------------------------------------------------------------------
# action handler: open_faceted_search_modal
# --------------------------------------------------------------------------------------


def _run_bot_and_get_app(
    tmp_path, db_path=None, denylist_brands=(), enable_approved_asset_urls=None,
    request_token_store=None, entry_mode=None, slash_allowed_channel_ids=None,
):
    db_path = db_path or _build_index(tmp_path)
    workbook_path, sha256 = _write_taxonomy(tmp_path)
    config_path = _write_slack_config(
        tmp_path,
        workbook_path=workbook_path,
        sha256=sha256,
        enable_approved_asset_urls=enable_approved_asset_urls,
        entry_mode=entry_mode,
        slash_allowed_channel_ids=slash_allowed_channel_ids,
    )
    container = {}
    if request_token_store is not None:
        # run_slack_bot reaches for the process-wide default; swap it so a test can inspect it.
        import marketing_knowledge_agent.slack_interface as _si

        _original = _si.default_request_token_store
        _si.default_request_token_store = lambda: request_token_store
    try:
        _run_bot(config_path, db_path, tmp_path, denylist_brands, container)
    finally:
        if request_token_store is not None:
            _si.default_request_token_store = _original
    return container["app"], tmp_path


def _run_bot(config_path, db_path, tmp_path, denylist_brands, container):
    run_slack_bot(
        config_path=config_path,
        db_path=db_path,
        restricted_customers_path=_denylist(tmp_path, denylist_brands),
        audit_log_path=tmp_path / "audit.csv",
        environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
        app_factory=_capturing_app_factory(container),
        socket_mode_handler_factory=_capturing_socket_mode_handler_factory(container),
    )


def test_open_modal_action_opens_the_view_for_an_allowed_channel(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.actions[OPEN_SEARCH_MODAL_ACTION_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    body = _action_body()

    handler(ack=ack, body=body, client=client)

    assert ack.calls == [{}]
    assert len(client.opened_views) == 1
    assert client.opened_views[0]["trigger_id"] == "T1"
    assert client.opened_views[0]["view"]["callback_id"] == FACETED_SEARCH_MODAL_CALLBACK_ID


def test_open_modal_action_refuses_a_disallowed_channel(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.actions[OPEN_SEARCH_MODAL_ACTION_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    body = _action_body(channel_id="C_NOT_ALLOWED")

    handler(ack=ack, body=body, client=client)

    assert client.opened_views == []


def test_adjust_button_prefills_the_reopened_modal_via_its_request_token(tmp_path):
    """Full round trip: submit -> adjust button -> reopened modal carries the prior selection.

    Driven end to end rather than by hand-building a button payload, because the prefill now
    travels through the server-side token store rather than through the button's ``value``, and a
    hand-built payload would not exercise the store at all.
    """
    app, _tmp_path = _run_bot_and_get_app(tmp_path)

    view_handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    submit_client = FakeSlackClient()
    view_handler(
        ack=FakeAck(),
        body={"user": {"id": "U1"}},
        client=submit_client,
        view={
            "private_metadata": _private_metadata(app),
            "state": {"values": _state_values(year="2024", free_text="會員回購")},
        },
    )

    adjust_button = submit_client.messages[-1]["blocks"][-1]["elements"][0]
    assert json.loads(adjust_button["value"])["request_token"]

    open_handler = app.actions[OPEN_SEARCH_MODAL_ACTION_ID]
    reopen_client = FakeSlackClient()
    open_handler(
        ack=FakeAck(),
        body=_action_body(trigger_id="T2", value=adjust_button["value"]),
        client=reopen_client,
    )

    view = reopen_client.opened_views[0]["view"]
    year_block = next(b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID)
    assert year_block["element"]["initial_option"]["value"] == "2024"
    free_text_block = next(b for b in view["blocks"] if b["block_id"] == FREE_TEXT_BLOCK_ID)
    assert free_text_block["element"]["initial_value"] == "會員回購"


def test_an_expired_request_token_reopens_an_empty_modal_rather_than_guessing(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    open_handler = app.actions[OPEN_SEARCH_MODAL_ACTION_ID]
    client = FakeSlackClient()

    open_handler(
        ack=FakeAck(),
        body=_action_body(
            value=json.dumps({"request_token": "expired-or-never-issued"})
        ),
        client=client,
    )

    view = client.opened_views[0]["view"]
    year_block = next(b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID)
    assert year_block["element"]["initial_option"]["value"] == ALL_YEARS_OPTION_VALUE
    free_text_block = next(b for b in view["blocks"] if b["block_id"] == FREE_TEXT_BLOCK_ID)
    assert "initial_value" not in free_text_block["element"]


# --------------------------------------------------------------------------------------
# view submission handler: faceted_search_modal
# --------------------------------------------------------------------------------------


def _state_values(year=ALL_YEARS_OPTION_VALUE, lv2=None, tags=None, free_text=None):
    """A submission payload in the v2 wire shape: the year field is a single ``static_select``."""

    def _options(values):
        return {"selected_options": [{"value": v} for v in values]} if values else {"selected_options": []}

    return {
        INTERVIEW_YEARS_BLOCK_ID: {
            INTERVIEW_YEARS_ACTION_ID: {"selected_option": {"value": year} if year is not None else None}
        },
        SALES_CATEGORY_LV2_BLOCK_ID: {SALES_CATEGORY_LV2_ACTION_ID: _options(lv2)},
        CONTENT_TAGS_BLOCK_ID: {CONTENT_TAGS_ACTION_ID: _options(tags)},
        FREE_TEXT_BLOCK_ID: {FREE_TEXT_ACTION_ID: {"value": free_text}},
    }


def _private_metadata(app, channel_id="C123", thread_ts="1"):
    # Pull the live catalog version straight from a freshly opened view, rather than hardcoding one.
    open_handler = app.actions[OPEN_SEARCH_MODAL_ACTION_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    open_handler(
        ack=ack,
        body=_action_body(channel_id=channel_id, thread_ts=thread_ts),
        client=client,
    )
    return client.opened_views[0]["view"]["private_metadata"]


def test_submission_from_a_disallowed_channel_is_refused(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    view = {
        "private_metadata": json.dumps(
            {"channel_id": "C_NOT_ALLOWED", "thread_ts": "1", "catalog_version": "whatever"}
        ),
        "state": {"values": _state_values(year="2024")},
    }

    handler(ack=ack, body={"user": {"id": "U1"}}, client=client, view=view)

    assert client.messages == []


def test_submission_with_a_stale_catalog_version_is_refused(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    view = {
        "private_metadata": json.dumps(
            {"channel_id": "C123", "thread_ts": "1", "catalog_version": "stale"}
        ),
        "state": {"values": _state_values(year="2024")},
    }

    handler(ack=ack, body={"user": {"id": "U1"}}, client=client, view=view)

    assert ack.calls == [{}]
    assert len(client.messages) == 1
    assert client.messages[0]["text"] == FACETED_SEARCH_STALE_CATALOG_MESSAGE


def test_empty_submission_is_refused_with_a_view_error(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    metadata = _private_metadata(app)
    view = {"private_metadata": metadata, "state": {"values": _state_values()}}

    handler(ack=ack, body={"user": {"id": "U1"}}, client=client, view=view)

    assert len(ack.calls) == 1
    assert ack.calls[0]["response_action"] == "errors"
    assert FREE_TEXT_BLOCK_ID in ack.calls[0]["errors"]
    assert client.messages == []


def test_valid_submission_runs_exactly_one_search_and_posts_result_plus_adjust_button(tmp_path):
    app, tmp_path_ = _run_bot_and_get_app(tmp_path)
    handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    metadata = _private_metadata(app)
    view = {
        "private_metadata": metadata,
        "state": {"values": _state_values(year="2024")},
    }

    handler(ack=ack, body={"user": {"id": "U1"}}, client=client, view=view)

    assert ack.calls == [{}]
    # One message for the result, one for the "調整條件" follow-up -- never a second search.
    assert len(client.messages) == 2
    assert "大春煉皂" in client.messages[0]["text"]
    adjust_button = client.messages[1]["blocks"][-1]["elements"][0]
    assert adjust_button["action_id"] == OPEN_SEARCH_MODAL_ACTION_ID
    assert json.loads(adjust_button["value"])["request_token"]

    audit_rows = (tmp_path_ / "audit.csv").read_text(encoding="utf-8").splitlines()
    assert any("slack_faceted_search" in row for row in audit_rows)


def test_app_mention_and_view_submission_share_the_same_pagination_store(tmp_path, monkeypatch):
    """A facet search that pages continues correctly through the ordinary "顯示更多" reply.

    Both handlers are wired from the one ``default_pagination_store()`` call inside
    ``run_slack_bot``, so a continuation the view-submission handler starts must be resumable by
    the pre-existing ``app_mention`` "顯示更多" path -- not a second, parallel store.
    """
    sentinel_store = SlackPaginationStore()
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface.default_pagination_store", lambda: sentinel_store
    )

    # BRAND_PAGE_SIZE is 15; sixteen distinct, eligible merchants sharing one content tag forces
    # the structured result onto a second page.
    records = [
        _metadata(f"品牌{i:02d}", f"handle{i:02d}", "食品/飲料", ["會員經營"], 2024, source_row=i)
        for i in range(1, 17)
    ]
    documents = [
        Document(id=f"doc-{index}", metadata=metadata, content=content)
        for index, (metadata, content) in enumerate(records, start=1)
    ]
    db_path = tmp_path / "content_index_bulk.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))

    app, _tmp_path = _run_bot_and_get_app(tmp_path, db_path=db_path)

    view_handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    metadata = _private_metadata(app, channel_id="C123", thread_ts="1")
    view = {"private_metadata": metadata, "state": {"values": _state_values(tags=["會員經營"])}}
    view_handler(ack=ack, body={"user": {"id": "U1"}}, client=client, view=view)

    # The search paged, so the continuation now lives in sentinel_store under this thread.
    assert len(sentinel_store) == 1

    app_mention_handler = app.events["app_mention"]
    mention_client = FakeSlackClient()
    event = {"text": "<@BOT> 顯示更多", "channel": "C123", "user": "U1", "ts": "1"}
    app_mention_handler(event=event, client=mention_client)

    assert len(mention_client.messages) == 1
    assert mention_client.messages[0]["text"] not in ("", None)
    assert mention_client.messages[0]["text"] != client.messages[0]["text"]


# --------------------------------------------------------------------------------------
# Codex review remediation: audit leak, approved-URL parity, pagination lifecycle
# --------------------------------------------------------------------------------------


def _multi_page_records(count=16):
    """Enough distinct eligible merchants under one tag to force a second result page."""
    return [
        _metadata(f"品牌{i:02d}", f"handle{i:02d}", "食品/飲料", ["會員經營"], 2024, source_row=i)
        for i in range(1, count + 1)
    ]


def _submit(app, *, state_values, thread_ts="1", user_id="U1"):
    handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    handler(
        ack=ack,
        body={"user": {"id": user_id}},
        client=client,
        view={
            "private_metadata": _private_metadata(app, thread_ts=thread_ts),
            "state": {"values": state_values},
        },
    )
    return ack, client


def test_restricted_free_text_never_reaches_the_faceted_search_audit_row(tmp_path):
    """Finding 1, at the Slack handler layer.

    The refused free text must appear nowhere in the audit file -- not in the ``slack_faceted_search``
    row, and not in the ``denylist_query_hit`` row that records the refusal itself.
    """
    secret = "SECRET_CUSTOMER_NAME"
    app, tmp_path_ = _run_bot_and_get_app(tmp_path, denylist_brands=[secret])

    _ack, client = _submit(
        app, state_values=_state_values(year="2024", free_text=f"{secret} 的成長案例")
    )

    audit_text = (tmp_path_ / "audit.csv").read_text(encoding="utf-8")
    assert secret not in audit_text
    rows = list(csv.reader((tmp_path_ / "audit.csv").open(encoding="utf-8", newline="")))
    events = [row[1] for row in rows[1:]]
    # The hit is recorded; the search row is skipped entirely, exactly as the NL path skips slack_qa.
    assert "denylist_query_hit" in events
    assert "slack_faceted_search" not in events
    hit = next(row for row in rows[1:] if row[1] == "denylist_query_hit")
    assert hit[2] == "C123" and hit[3] == "U1"
    assert hit[-1] == ""
    # The user still gets the refusal, and it does not echo the restricted term back either.
    assert client.messages and secret not in client.messages[0]["text"]


def test_a_non_restricted_search_still_records_its_facets_in_the_audit_row(tmp_path):
    """The audit row is skipped only on refusal -- an ordinary search is still attributable."""
    app, tmp_path_ = _run_bot_and_get_app(tmp_path)

    _submit(app, state_values=_state_values(year="2024", free_text="會員回購"))

    rows = list(csv.reader((tmp_path_ / "audit.csv").open(encoding="utf-8", newline="")))
    row = next(row for row in rows[1:] if row[1] == "slack_faceted_search")
    assert row[2] == "C123" and row[3] == "U1"
    assert "years=2024" in row[-1]
    assert "會員回購" in row[-1]


def test_approved_asset_urls_are_applied_when_enabled(tmp_path, monkeypatch):
    """Finding 4: the faceted result must go through the same overlay the NL path does."""
    applied = []
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface._apply_approved_asset_urls",
        lambda answer, db_path: applied.append(db_path) or None,
    )
    app, _tmp_path = _run_bot_and_get_app(tmp_path, enable_approved_asset_urls=True)

    _submit(app, state_values=_state_values(year="2024"))

    assert len(applied) == 1


def test_approved_asset_urls_are_not_applied_when_disabled(tmp_path, monkeypatch):
    applied = []
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface._apply_approved_asset_urls",
        lambda answer, db_path: applied.append(db_path) or None,
    )
    app, _tmp_path = _run_bot_and_get_app(tmp_path, enable_approved_asset_urls=False)

    _submit(app, state_values=_state_values(year="2024"))

    assert applied == []


def test_approved_asset_url_overlay_unavailable_is_audited_without_aborting_the_search(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface._apply_approved_asset_urls",
        lambda answer, db_path: APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE,
    )
    app, tmp_path_ = _run_bot_and_get_app(tmp_path, enable_approved_asset_urls=True)

    _ack, client = _submit(app, state_values=_state_values(year="2024"))

    rows = list(csv.reader((tmp_path_ / "audit.csv").open(encoding="utf-8", newline="")))
    events = [row[1] for row in rows[1:]]
    assert APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE in events
    # The audit code is payload-free and the search still answered.
    issue_row = next(row for row in rows[1:] if row[1] == APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE)
    assert issue_row[-1] == ""
    assert client.messages and "大春煉皂" in client.messages[0]["text"]


def test_approved_asset_urls_are_skipped_on_a_denylist_refusal(tmp_path, monkeypatch):
    """The refusal guard: a refused answer has nothing to enrich and must not be enriched."""
    applied = []
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface._apply_approved_asset_urls",
        lambda answer, db_path: applied.append(db_path) or None,
    )
    secret = "SECRET_CUSTOMER_NAME"
    app, _tmp_path = _run_bot_and_get_app(
        tmp_path, denylist_brands=[secret], enable_approved_asset_urls=True
    )

    _submit(app, state_values=_state_values(year="2024", free_text=f"{secret} 案例"))

    assert applied == []


def test_a_new_submission_supersedes_the_previous_pagination_in_the_same_thread(tmp_path, monkeypatch):
    """Finding 5, the ordinary case: a second paged search replaces the first thread's pages."""
    sentinel_store = SlackPaginationStore()
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface.default_pagination_store", lambda: sentinel_store
    )
    db_path = tmp_path / "bulk.sqlite"
    documents = [
        Document(id=f"doc-{i}", metadata=m, content=c)
        for i, (m, c) in enumerate(_multi_page_records(), start=1)
    ]
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    app, _tmp_path = _run_bot_and_get_app(tmp_path, db_path=db_path)

    _submit(app, state_values=_state_values(tags=["會員經營"]), thread_ts="1")
    first_continuation = sentinel_store.consume_current_generation(("C123", "1"))
    assert first_continuation is not None

    # A second search in the same thread must start its own continuation, not extend the first.
    _submit(app, state_values=_state_values(tags=["會員經營"]), thread_ts="1")
    resumed = sentinel_store.consume_current_generation(("C123", "1"))
    assert resumed == first_continuation  # page 2 of the *new* search, from the start


def test_a_refusal_discards_the_previous_pagination_rather_than_leaving_it_resumable(
    tmp_path, monkeypatch
):
    """Finding 5, the case that was actually broken.

    A refused or unstructured submission produces no pages, so the old ``start()`` call never ran
    and the previous search's continuation stayed live -- 「顯示更多」 would then resume a result the
    user had already moved on from, in a thread whose latest search was refused outright.
    """
    sentinel_store = SlackPaginationStore()
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface.default_pagination_store", lambda: sentinel_store
    )
    secret = "SECRET_CUSTOMER_NAME"
    db_path = tmp_path / "bulk.sqlite"
    documents = [
        Document(id=f"doc-{i}", metadata=m, content=c)
        for i, (m, c) in enumerate(_multi_page_records(), start=1)
    ]
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    app, _tmp_path = _run_bot_and_get_app(tmp_path, db_path=db_path, denylist_brands=[secret])

    # A first search that pages, leaving a live continuation.
    _submit(app, state_values=_state_values(tags=["會員經營"]), thread_ts="1")
    assert len(sentinel_store) == 1

    # Then a refused submission in the same thread.
    _submit(
        app,
        state_values=_state_values(tags=["會員經營"], free_text=f"{secret} 案例"),
        thread_ts="1",
    )

    assert len(sentinel_store) == 0
    assert sentinel_store.consume_current_generation(("C123", "1")) is None

    # And 「顯示更多」 says the session expired rather than replaying the superseded result.
    app_mention_handler = app.events["app_mention"]
    mention_client = FakeSlackClient()
    app_mention_handler(
        event={"text": "<@BOT> 顯示更多", "channel": "C123", "user": "U1", "ts": "1"},
        client=mention_client,
    )
    assert mention_client.messages[0]["text"] == PAGINATION_EXPIRED_MESSAGE


# --------------------------------------------------------------------------------------
# Codex R2 blocker: cross-user prefill disclosure via the public "調整條件" button
# --------------------------------------------------------------------------------------


def _submit_and_get_adjust_button(app, *, state_values, user_id="U1", thread_ts="1"):
    """Run one submission and return (client, the adjust/restart button that followed it)."""
    handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    client = FakeSlackClient()
    handler(
        ack=FakeAck(),
        body={"user": {"id": user_id}},
        client=client,
        view={
            "private_metadata": _private_metadata(app, thread_ts=thread_ts),
            "state": {"values": state_values},
        },
    )
    return client, client.messages[-1]["blocks"][-1]["elements"][0]


def _reopen(app, button_value, *, user_id, channel_id="C123", thread_ts="1"):
    client = FakeSlackClient()
    app.actions[OPEN_SEARCH_MODAL_ACTION_ID](
        ack=FakeAck(),
        body=_action_body(
            user_id=user_id, channel_id=channel_id, thread_ts=thread_ts, value=button_value
        ),
        client=client,
    )
    return client


def _modal_prefill(view):
    """Everything the reopened modal would show the clicker, as one comparable structure.

    The year field is single-select and always carries an ``initial_option``, so 「全部年份」 --
    which is "no year chosen" -- is normalised to the same empty list the multi-selects use when
    nothing is selected. That keeps "this clicker sees none of the owner's filters" a single
    comparison across all three fields.
    """
    blocks = {b.get("block_id"): b for b in view["blocks"]}
    selected = {}
    for block_id in (SALES_CATEGORY_LV2_BLOCK_ID, CONTENT_TAGS_BLOCK_ID):
        element = blocks[block_id]["element"]
        selected[block_id] = [o["value"] for o in element.get("initial_options", [])]
    year_value = blocks[INTERVIEW_YEARS_BLOCK_ID]["element"]["initial_option"]["value"]
    selected[INTERVIEW_YEARS_BLOCK_ID] = (
        [] if year_value == ALL_YEARS_OPTION_VALUE else [year_value]
    )
    selected[FREE_TEXT_BLOCK_ID] = blocks[FREE_TEXT_BLOCK_ID]["element"].get("initial_value", "")
    return selected


SECRET_GOAL = "U1 私人搜尋目標 competitor-churn"


def test_the_owner_can_reopen_their_own_prefilled_search(tmp_path):
    """Case A. The feature must still work for the person it belongs to."""
    app, _tmp = _run_bot_and_get_app(tmp_path)
    _client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(year="2024", free_text=SECRET_GOAL), user_id="U1"
    )

    reopened = _reopen(app, button["value"], user_id="U1")

    prefill = _modal_prefill(reopened.opened_views[0]["view"])
    assert prefill[INTERVIEW_YEARS_BLOCK_ID] == ["2024"]
    assert prefill[FREE_TEXT_BLOCK_ID] == SECRET_GOAL


def test_a_different_user_clicking_the_same_button_sees_nothing_of_the_owners_search(tmp_path):
    """Case B, the blocker itself.

    The button is posted into the channel, so U2 can and will be able to click it. What U2 must not
    get is U1's filters or U1's free-text goal -- search intent typed into what looks like a private
    dialog. Failing closed to an empty modal is the required outcome, not an error message that
    would confirm someone else's search exists.
    """
    app, _tmp = _run_bot_and_get_app(tmp_path)
    _client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(year="2024", free_text=SECRET_GOAL), user_id="U1"
    )

    reopened = _reopen(app, button["value"], user_id="U2")

    view = reopened.opened_views[0]["view"]
    prefill = _modal_prefill(view)
    assert prefill[INTERVIEW_YEARS_BLOCK_ID] == []
    assert prefill[SALES_CATEGORY_LV2_BLOCK_ID] == []
    assert prefill[CONTENT_TAGS_BLOCK_ID] == []
    assert prefill[FREE_TEXT_BLOCK_ID] == ""
    # Nothing of U1's search may appear anywhere in the payload U2 receives.
    assert SECRET_GOAL not in json.dumps(view, ensure_ascii=False)
    assert (
        [b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID][0]["element"][
            "initial_option"
        ]["value"]
        == ALL_YEARS_OPTION_VALUE
    )


def test_the_owner_in_a_different_channel_cannot_retrieve_the_request(tmp_path):
    """Case C. Same person, different conversation, different audience."""
    app, _tmp = _run_bot_and_get_app(tmp_path)
    _client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(year="2024", free_text=SECRET_GOAL), user_id="U1"
    )

    # C_OTHER is not allowlisted, so nothing opens at all -- the stronger of the two failures.
    reopened = _reopen(app, button["value"], user_id="U1", channel_id="C_OTHER")
    assert reopened.opened_views == []


def test_the_owner_in_a_different_thread_cannot_retrieve_the_request(tmp_path):
    """Case D. Same person, same channel, a thread the search was never run in."""
    app, _tmp = _run_bot_and_get_app(tmp_path)
    _client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(year="2024", free_text=SECRET_GOAL), user_id="U1"
    )

    reopened = _reopen(app, button["value"], user_id="U1", thread_ts="999.9")

    view = reopened.opened_views[0]["view"]
    assert _modal_prefill(view)[FREE_TEXT_BLOCK_ID] == ""
    assert SECRET_GOAL not in json.dumps(view, ensure_ascii=False)


def test_an_interaction_payload_without_context_fails_closed(tmp_path):
    """Case F's sibling: a malformed payload must not fall through to an empty-string match."""
    app, _tmp = _run_bot_and_get_app(tmp_path)
    client = FakeSlackClient()

    app.actions[OPEN_SEARCH_MODAL_ACTION_ID](
        ack=FakeAck(),
        body={"trigger_id": "T1", "actions": [{"value": json.dumps({})}]},  # no user/container
        client=client,
    )

    assert client.opened_views == []


def test_a_denylist_refusal_stores_no_request_and_offers_no_prefill_button(tmp_path):
    """Case E. Restricted text must not survive in the token store or in a button.

    A refused query is exactly the text that must not be retained anywhere shared, and the token
    store is shared across every viewer of the channel. Storing it "only for the owner" would still
    be storing it, so the refusal path stores nothing and offers only a blank restart.
    """
    secret = "SECRET_CUSTOMER_NAME"
    token_store = SlackRequestTokenStore()
    app, tmp_path_ = _run_bot_and_get_app(
        tmp_path, denylist_brands=[secret], request_token_store=token_store
    )

    client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(year="2024", free_text=f"{secret} 的成長案例")
    )

    # Nothing retained, and no token to reopen with.
    assert len(token_store) == 0
    assert token_store.stored_requests() == ()
    assert "request_token" not in json.loads(button["value"])
    assert button["text"]["text"] == "重新搜尋"

    # The restricted term appears in no observable surface.
    observable = json.dumps(client.messages, ensure_ascii=False)
    assert secret not in observable
    assert secret not in (tmp_path_ / "audit.csv").read_text(encoding="utf-8")
    assert secret not in json.dumps(
        [r.free_text for r in token_store.stored_requests()], ensure_ascii=False
    )

    # And that restart button opens a genuinely blank modal.
    reopened = _reopen(app, button["value"], user_id="U1")
    assert _modal_prefill(reopened.opened_views[0]["view"])[FREE_TEXT_BLOCK_ID] == ""


# --------------------------------------------------------------------------------------
# Slack link/media unfurl suppression (UAT UX remediation)
#
# Every message this handler posts goes through ``post_slack_reply``, which forces
# ``unfurl_links``/``unfurl_media`` off. Human UAT found that a result carrying several clickable
# approved asset titles made Slack expand a preview card per link -- article summaries, "Written
# by" metadata, full-width images, YouTube thumbnails -- burying the results in the thread. The
# links themselves are untouched; only Slack's automatic preview is suppressed.
# --------------------------------------------------------------------------------------


def _assert_no_unfurl(message):
    assert message["unfurl_links"] is False
    assert message["unfurl_media"] is False


def test_faceted_result_and_adjust_button_are_posted_without_unfurling(tmp_path):
    """B and D: the structured result page, and the "調整條件" follow-up that accompanies it."""
    app, _tmp_path = _run_bot_and_get_app(tmp_path)

    _ack, client = _submit(app, state_values=_state_values(year="2024"))

    assert len(client.messages) == 2
    for message in client.messages:
        _assert_no_unfurl(message)
    # The follow-up is still the adjust-filters message, with its button intact.
    assert client.messages[1]["blocks"][-1]["block_id"] == "adjust_faceted_search_actions"


def test_faceted_result_still_carries_its_clickable_asset_titles(tmp_path, monkeypatch):
    """G: suppressing the preview must not strip or rewrite the approved asset link itself."""
    url = "https://shopline.tw/blog/case"

    def _attach_url(answer, db_path):
        structured = getattr(answer, "generated", answer).structured_result
        for entity in structured.matched_entities:
            for asset in entity.assets:
                asset.url = url
        return None

    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface._apply_approved_asset_urls", _attach_url
    )
    app, _tmp_path = _run_bot_and_get_app(tmp_path, enable_approved_asset_urls=True)

    _ack, client = _submit(app, state_values=_state_values(year="2024"))

    result = client.messages[0]
    _assert_no_unfurl(result)
    # The clickable mrkdwn link survives the boundary intact: URL present, title still the label.
    assert f"<{url}|" in result["text"]


def test_restart_search_message_after_a_refusal_is_posted_without_unfurling(tmp_path):
    """E, plus the unstructured-reply branch: a refusal posts a body and a 「重新搜尋」 button."""
    secret = "SECRET_CUSTOMER_NAME"
    app, _tmp_path = _run_bot_and_get_app(tmp_path, denylist_brands=[secret])

    _ack, client = _submit(
        app, state_values=_state_values(year="2024", free_text=f"{secret} 案例")
    )

    assert len(client.messages) == 2
    for message in client.messages:
        _assert_no_unfurl(message)
    assert client.messages[1]["blocks"][-1]["block_id"] == "restart_faceted_search_actions"


def test_stale_catalog_message_is_posted_without_unfurling(tmp_path):
    """F: the staleness refusal is a message too, and takes the same boundary."""
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.views[FACETED_SEARCH_MODAL_CALLBACK_ID]
    client = FakeSlackClient()

    handler(
        ack=FakeAck(),
        body={"user": {"id": "U1"}},
        client=client,
        view={
            "private_metadata": json.dumps(
                {"channel_id": "C123", "thread_ts": "1", "catalog_version": "stale"}
            ),
            "state": {"values": _state_values(year="2024")},
        },
    )

    assert client.messages[0]["text"] == FACETED_SEARCH_STALE_CATALOG_MESSAGE
    _assert_no_unfurl(client.messages[0])


def test_the_faceted_trigger_reply_is_posted_without_unfurling(tmp_path):
    """The "@Bot 搜尋" button message travels the registered app_mention posting path."""
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    client = FakeSlackClient()

    app.events["app_mention"](
        event={"text": "<@BOT> 搜尋", "channel": "C123", "user": "U1", "ts": "1"},
        client=client,
    )

    assert client.messages
    _assert_no_unfurl(client.messages[0])
    assert client.messages[0]["blocks"][-1]["block_id"] == "open_faceted_search_actions"


# ======================================================================================
# /mka slash-command entry mode
# ======================================================================================

SLASH_MODE = "slash_faceted_only"
SECRET_CUSTOMER = "SECRET_CUSTOMER_NAME"


def _slash_app(tmp_path, **kwargs):
    return _run_bot_and_get_app(tmp_path, entry_mode=SLASH_MODE, **kwargs)[0]


def _command_body(*, user_id="U1", channel_id="C123", text="", trigger_id="TRIG1",
                  response_url=FAKE_RESPONSE_URL):
    """A slash-command payload in the flat shape Slack actually sends one."""
    return {
        "token": "verification",
        "team_id": "T1",
        "team_domain": "acme",
        "channel_id": channel_id,
        "channel_name": "general",
        "user_id": user_id,
        "user_name": "someone",
        "command": SLASH_COMMAND_NAME,
        "text": text,
        "api_app_id": "A1",
        "is_enterprise_install": "false",
        "response_url": response_url,
        "trigger_id": trigger_id,
    }


@contextmanager
def _capture_responses():
    """Record what the slash flow sends, in place of the response_url boundary."""
    import marketing_knowledge_agent.slack_interface as _si

    recorder = ResponseRecorder()
    original = _si.post_slack_response_url
    _si.post_slack_response_url = recorder
    try:
        yield recorder
    finally:
        _si.post_slack_response_url = original


def _run_command(app, **kwargs):
    ack, client = FakeAck(), FakeSlackClient()
    with _capture_responses() as recorder:
        app.commands[SLASH_COMMAND_NAME](ack=ack, body=_command_body(**kwargs), client=client)
    client.responses = recorder
    return ack, client


def _ephemeral_action_body(*, user_id="U1", channel_id="C123", value=None, trigger_id="T2"):
    """A block_actions payload for a button inside an *ephemeral* message.

    Slack sends no ``thread_ts`` for one -- an ephemeral message is not a threaded reply -- which
    is exactly why the slash flow cannot reuse the mention flow's context derivation.
    """
    return {
        "type": "block_actions",
        "trigger_id": trigger_id,
        "user": {"id": user_id},
        "container": {
            "type": "message",
            "channel_id": channel_id,
            "message_ts": "999.1",
            "is_ephemeral": True,
        },
        "channel": {"id": channel_id},
        # A real interaction payload carries its own response_url, separately budgeted from the
        # command that started the session.
        "response_url": "https://hooks.slack.com/actions/TEST/SECRET_CAPABILITY",
        "actions": [{"type": "button", "value": value if value is not None else json.dumps({})}],
    }


def _slash_private_metadata(app, *, user_id="U1", channel_id="C123"):
    """Open a real modal through the command handler and take its metadata verbatim."""
    _ack, client = _run_command(app, user_id=user_id, channel_id=channel_id)
    return client.opened_views[-1]["view"]["private_metadata"]


def _slash_submit(app, *, state_values, user_id="U1", channel_id="C123", metadata=None):
    resolved = metadata or _slash_private_metadata(app, user_id=user_id, channel_id=channel_id)
    ack, client = FakeAck(), FakeSlackClient()
    with _capture_responses() as recorder:
        app.views[FACETED_SEARCH_MODAL_CALLBACK_ID](
            ack=ack,
            body={"user": {"id": user_id}},
            client=client,
            view={"private_metadata": resolved, "state": {"values": state_values}},
        )
    client.responses = recorder
    return ack, client


# --------------------------------------------------------------------------------------
# A. entry mode configuration
# --------------------------------------------------------------------------------------


def test_config_without_an_entry_mode_defaults_to_todays_behaviour(tmp_path):
    """Merging this code cannot activate anything: the new mode has to be asked for by name."""
    path = tmp_path / "slack_config.json"
    path.write_text(json.dumps({"allowed_channel_ids": ["C1"]}), encoding="utf-8")

    config = load_slack_config(path)

    assert config.search_entry_mode == ENTRY_MODE_MENTION_MIXED
    assert config.slash_command_allowed_channel_ids is None


def test_an_unrecognised_entry_mode_is_refused_rather_than_defaulted(tmp_path):
    """A typo must not silently leave app-mention search alive on a deployment that switched it off."""
    path = tmp_path / "slack_config.json"
    path.write_text(
        json.dumps({"allowed_channel_ids": ["C1"], "slack_search_entry_mode": "slash_only"}),
        encoding="utf-8",
    )
    with pytest.raises(SlackInterfaceError, match="slack_search_entry_mode"):
        load_slack_config(path)


def test_slash_mode_without_faceted_search_enabled_is_refused(tmp_path):
    """The modal is the only search entry in this mode, so disabling it means no search at all."""
    path = tmp_path / "slack_config.json"
    path.write_text(
        json.dumps(
            {
                "allowed_channel_ids": ["C1"],
                "slack_search_entry_mode": ENTRY_MODE_SLASH_FACETED_ONLY,
                "enable_faceted_search": False,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SlackInterfaceError, match="enable_faceted_search"):
        load_slack_config(path)


def test_an_explicitly_empty_slash_allowlist_is_refused(tmp_path):
    """``[]`` reads as both "everywhere" and "nowhere"; neither may be chosen silently."""
    path = tmp_path / "slack_config.json"
    path.write_text(
        json.dumps({"allowed_channel_ids": ["C1"], "slash_command_allowed_channel_ids": []}),
        encoding="utf-8",
    )
    with pytest.raises(SlackInterfaceError, match="slash_command_allowed_channel_ids"):
        load_slack_config(path)


def test_a_populated_slash_allowlist_is_kept(tmp_path):
    path = tmp_path / "slack_config.json"
    path.write_text(
        json.dumps(
            {"allowed_channel_ids": ["C1"], "slash_command_allowed_channel_ids": [" D9 ", "C2"]}
        ),
        encoding="utf-8",
    )
    assert load_slack_config(path).slash_command_allowed_channel_ids == ["D9", "C2"]


def test_the_slash_command_is_registered_only_in_slash_mode(tmp_path):
    default_app, _tmp = _run_bot_and_get_app(tmp_path)
    assert SLASH_COMMAND_NAME not in default_app.commands
    assert SHOW_MORE_ACTION_ID not in default_app.actions

    slash_app = _slash_app(tmp_path / "slash")
    assert SLASH_COMMAND_NAME in slash_app.commands
    assert SHOW_MORE_ACTION_ID in slash_app.actions


# --------------------------------------------------------------------------------------
# B. /mka opens the modal directly, and its trailing text is not input
# --------------------------------------------------------------------------------------


def test_the_command_acks_and_opens_the_modal_without_an_intermediate_button(tmp_path):
    app = _slash_app(tmp_path)

    ack, client = _run_command(app)

    assert ack.calls == [{}]
    assert len(client.opened_views) == 1
    assert client.opened_views[0]["trigger_id"] == "TRIG1"
    assert client.opened_views[0]["view"]["callback_id"] == FACETED_SEARCH_MODAL_CALLBACK_ID
    # No "點擊下方按鈕" hop, and nothing posted at all.
    assert client.messages == [] and client.responses.sent == []


def test_the_command_runs_no_search(tmp_path):
    """Opening a modal is not a query: no retrieval, and no audit row of any kind."""
    app = _slash_app(tmp_path)
    audit = tmp_path / "audit.csv"

    _run_command(app, text="幫我找寵物案例")

    assert not audit.exists()


@pytest.mark.parametrize(
    "text", ["", "搜尋", "SHOPLINE", "幫我找寵物案例", SECRET_CUSTOMER, "   "]
)
def test_command_trailing_text_is_ignored_entirely(tmp_path, text):
    """Every one of these must produce the *same* blank, default modal.

    Stated as an equality against the no-text modal rather than as "the text is absent": the
    modal's own chrome legitimately contains words like 「搜尋」, so a substring check would either
    miss a real prefill or fail on the submit button. Two views that are identical apart from their
    session id cannot differ in what they carry over from the command.
    """
    app = _slash_app(tmp_path)

    _ack, baseline_client = _run_command(app, text="")
    _ack2, client = _run_command(app, text=text)

    def _normalised(view):
        view = json.loads(json.dumps(view, ensure_ascii=False))
        metadata = json.loads(view["private_metadata"])
        assert metadata["session_id"]  # present, and the only thing allowed to differ
        metadata["session_id"] = "<session>"
        view["private_metadata"] = json.dumps(metadata, ensure_ascii=False)
        return view

    view = client.opened_views[0]["view"]
    assert _normalised(view) == _normalised(baseline_client.opened_views[0]["view"])
    year_block = next(b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID)
    assert year_block["element"]["initial_option"]["value"] == ALL_YEARS_OPTION_VALUE
    free_text_block = next(b for b in view["blocks"] if b["block_id"] == FREE_TEXT_BLOCK_ID)
    assert "initial_value" not in free_text_block["element"]


def test_a_restricted_name_typed_after_the_command_is_never_retained(tmp_path):
    """Nothing retrieves on it, nothing stores it, nothing echoes it back."""
    store = SlackRequestTokenStore()
    app = _slash_app(tmp_path, request_token_store=store, denylist_brands=[SECRET_CUSTOMER])
    audit = tmp_path / "audit.csv"

    _ack, client = _run_command(app, text=SECRET_CUSTOMER)

    assert len(store) == 0
    assert not audit.exists()
    assert SECRET_CUSTOMER not in json.dumps(client.opened_views, ensure_ascii=False)
    assert client.messages == [] and client.responses.sent == []


def test_the_command_works_from_a_dm_conversation_id(tmp_path):
    """The product goal: a workspace member can run /mka anywhere, including a DM."""
    app = _slash_app(tmp_path)

    _ack, client = _run_command(app, channel_id="D0PRIVATE")

    metadata = json.loads(client.opened_views[0]["view"]["private_metadata"])
    assert metadata["channel_id"] == "D0PRIVATE"
    assert metadata["entrypoint"] == ENTRYPOINT_SLASH_COMMAND
    assert metadata["session_id"]


def test_each_invocation_gets_its_own_session(tmp_path):
    """Two searches by the same person must not share a continuation lane."""
    app = _slash_app(tmp_path)

    first = json.loads(_slash_private_metadata(app))["session_id"]
    second = json.loads(_slash_private_metadata(app))["session_id"]

    assert first and second and first != second


def test_a_conversation_outside_an_explicit_slash_allowlist_is_refused_ephemerally(tmp_path):
    app = _slash_app(tmp_path, slash_allowed_channel_ids=["C123"])

    _ack, client = _run_command(app, channel_id="C_OTHER")

    assert client.opened_views == []
    # Answered through the command's own response_url, which is what makes the denial reachable in
    # a conversation the bot is not a member of -- the case that went unanswered in Human UAT.
    assert client.responses.texts == [DENIED_CHANNEL_MESSAGE]
    assert client.responses.sent[-1]["url"] == FAKE_RESPONSE_URL
    assert client.messages == []


def test_an_incomplete_command_payload_fails_closed(tmp_path):
    app = _slash_app(tmp_path)
    for missing in ("user_id", "channel_id", "trigger_id"):
        body = _command_body()
        body[missing] = ""
        ack, client = FakeAck(), FakeSlackClient()
        app.commands[SLASH_COMMAND_NAME](ack=ack, body=body, client=client)
        assert ack.calls == [{}], missing
        assert client.opened_views == [], missing


# --------------------------------------------------------------------------------------
# C. app-mention migration
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "搜尋", "條件搜尋", "顯示更多", "大春煉皂的成長案例", SECRET_CUSTOMER],
)
def test_every_app_mention_gets_guidance_and_never_a_search(tmp_path, text):
    def fake_ask(question, **kwargs):
        raise AssertionError("app_mention must not retrieve in slash_faceted_only mode")

    audit = tmp_path / "audit.csv"
    reply = handle_slack_event(
        {"text": f"<@BOT> {text}", "channel": "C123", "user": "U1", "ts": "100.1"},
        config=SlackConfig(
            allowed_channel_ids=["C123"], search_entry_mode=ENTRY_MODE_SLASH_FACETED_ONLY
        ),
        ask_fn=fake_ask,
        audit_log_path=audit,
        faceted_search_enabled=True,
    )

    assert reply["text"] == APP_MENTION_GUIDANCE_MESSAGE
    assert SLASH_COMMAND_NAME in reply["text"]
    # Guidance, not a search: no button to open, and nothing written down.
    assert "blocks" not in reply
    assert not audit.exists()


def test_a_restricted_name_in_a_mention_is_not_retrieved_audited_stored_or_echoed(tmp_path):
    """The migration regression, stated as the disclosure it prevents."""
    store = SlackRequestTokenStore()
    audit = tmp_path / "audit.csv"

    reply = handle_slack_event(
        {"text": f"<@BOT> {SECRET_CUSTOMER}", "channel": "C123", "user": "U1", "ts": "100.1"},
        config=SlackConfig(
            allowed_channel_ids=["C123"], search_entry_mode=ENTRY_MODE_SLASH_FACETED_ONLY
        ),
        ask_fn=lambda *a, **k: pytest.fail("no retrieval may happen"),
        audit_log_path=audit,
        faceted_search_enabled=True,
    )

    assert SECRET_CUSTOMER not in json.dumps(reply, ensure_ascii=False)
    assert not audit.exists()
    assert len(store) == 0


def test_mention_pagination_is_retired_in_slash_mode(tmp_path):
    """「顯示更多」 as a thread reply must no longer resume anything."""
    store = SlackPaginationStore()
    generation = store.start(("C123", "100.1"), ["page one", "page two"])

    reply = handle_slack_event(
        {"text": "<@BOT> 顯示更多", "channel": "C123", "user": "U1", "ts": "100.1"},
        config=SlackConfig(
            allowed_channel_ids=["C123"], search_entry_mode=ENTRY_MODE_SLASH_FACETED_ONLY
        ),
        audit_log_path=tmp_path / "audit.csv",
        pagination_store=store,
        faceted_search_enabled=True,
    )

    assert reply["text"] == APP_MENTION_GUIDANCE_MESSAGE
    assert "page two" not in reply["text"]
    # The stored continuation is left untouched rather than consumed by a mention.
    assert store.consume_next_page(("C123", "100.1"), generation) == "page two"


def test_the_default_mode_still_searches_on_a_mention(tmp_path):
    """The other half of the contract: nothing changes unless the mode is selected."""
    calls = []
    reply = handle_slack_event(
        {"text": "<@BOT> 大春煉皂", "channel": "C123", "user": "U1", "ts": "1"},
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=lambda question, **kwargs: calls.append(question) or _minimal_answer(),
        audit_log_path=tmp_path / "audit.csv",
    )

    assert calls == ["大春煉皂"]
    assert reply["text"] != APP_MENTION_GUIDANCE_MESSAGE


# --------------------------------------------------------------------------------------
# F. result visibility: invoker only
# --------------------------------------------------------------------------------------


def test_a_slash_search_result_is_ephemeral_to_the_invoker(tmp_path):
    app = _slash_app(tmp_path)

    _ack, client = _slash_submit(app, state_values=_state_values(year="2024"))

    # Nothing at all goes to the channel.
    assert client.messages == []
    assert client.responses.sent
    result = client.responses.sent[0]
    # Delivery is the invoker's own response_url. There is no channel or user field to get wrong,
    # and no thread to attach to -- the capability already addresses the interaction it came from.
    assert result["url"] == FAKE_RESPONSE_URL
    assert "大春煉皂" in result["text"]
    assert "channel" not in result and "user" not in result and "thread_ts" not in result


@pytest.mark.parametrize("channel_id", ["C0PUBLIC", "G0PRIVATE", "D0DIRECT"])
def test_ephemeral_routing_works_for_every_conversation_shape(tmp_path, channel_id):
    app = _slash_app(tmp_path / channel_id)

    _ack, client = _slash_submit(
        app, state_values=_state_values(year="2024"), channel_id=channel_id
    )

    assert client.messages == []
    # The conversation shape is irrelevant to delivery now: the response_url does the addressing,
    # so a channel the bot was never added to is answered exactly like one it lives in.
    assert client.responses.sent[0]["url"] == FAKE_RESPONSE_URL
    assert client.responses.sent[0]["text"]


def test_a_slash_submission_from_an_unauthorized_conversation_posts_nothing(tmp_path):
    app = _slash_app(tmp_path, slash_allowed_channel_ids=["C123"])
    metadata = _slash_private_metadata(app)
    tampered = json.loads(metadata)
    tampered["channel_id"] = "C_OTHER"

    ack, client = _slash_submit(
        app, state_values=_state_values(year="2024"), metadata=json.dumps(tampered)
    )

    assert ack.calls == [{}]
    assert client.messages == [] and client.responses.sent == []


def test_a_slash_submission_without_a_session_fails_closed(tmp_path):
    """An empty session key would compare equal to every other empty one."""
    app = _slash_app(tmp_path)
    metadata = json.loads(_slash_private_metadata(app))
    metadata["session_id"] = ""

    ack, client = _slash_submit(
        app, state_values=_state_values(year="2024"), metadata=json.dumps(metadata)
    )

    assert ack.calls == [{}]
    assert client.messages == [] and client.responses.sent == []


def test_free_text_only_submission_is_refused_in_the_modal(tmp_path):
    app = _slash_app(tmp_path)

    ack, client = _slash_submit(app, state_values=_state_values(free_text="會員經營"))

    assert ack.calls[0]["response_action"] == "errors"
    assert "搜尋範圍" in ack.calls[0]["errors"][FREE_TEXT_BLOCK_ID]
    assert client.messages == [] and client.responses.sent == []


def test_all_years_is_never_recorded_as_a_year_in_the_audit_row(tmp_path):
    app = _slash_app(tmp_path)

    _slash_submit(app, state_values=_state_values(lv2=["食品/飲料"]))

    rows = list(csv.DictReader((tmp_path / "audit.csv").open(encoding="utf-8")))
    search_rows = [r for r in rows if r["event"] == "slack_faceted_search"]
    assert len(search_rows) == 1
    query = search_rows[0]["query"]
    assert "lv2=食品/飲料" in query
    assert "years=" not in query
    assert ALL_YEARS_OPTION_VALUE not in query
    assert "全部年份" not in query


def test_a_specific_year_is_recorded_in_the_audit_row(tmp_path):
    app = _slash_app(tmp_path)

    _slash_submit(app, state_values=_state_values(year="2024", lv2=["食品/飲料"]))

    rows = list(csv.DictReader((tmp_path / "audit.csv").open(encoding="utf-8")))
    query = [r for r in rows if r["event"] == "slack_faceted_search"][0]["query"]
    assert "years=2024" in query
    assert "lv2=食品/飲料" in query


# --------------------------------------------------------------------------------------
# G. pagination button
# --------------------------------------------------------------------------------------


def _multi_page_slash_app(tmp_path, **kwargs):
    records = _multi_page_records()
    documents = [
        Document(id=f"doc-{index}", metadata=metadata, content=content)
        for index, (metadata, content) in enumerate(records, start=1)
    ]
    db_path = tmp_path / "content_index_bulk.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return _slash_app(tmp_path, db_path=db_path, **kwargs)


def _show_more_button(client):
    for message in reversed(client.responses.sent):
        for block in message.get("blocks") or []:
            for element in block.get("elements", []):
                if element.get("action_id") == SHOW_MORE_ACTION_ID:
                    return element
    return None


def _click_show_more(app, value, *, user_id="U1", channel_id="C123"):
    ack, client = FakeAck(), FakeSlackClient()
    with _capture_responses() as recorder:
        app.actions[SHOW_MORE_ACTION_ID](
            ack=ack,
            body=_ephemeral_action_body(user_id=user_id, channel_id=channel_id, value=value),
            client=client,
        )
    client.responses = recorder
    return ack, client


def test_page_one_is_ephemeral_and_offers_a_show_more_button(tmp_path):
    app = _multi_page_slash_app(tmp_path)

    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))

    assert client.messages == []
    first_page = client.responses.sent[0]
    # The page must invite the button, not a thread reply that could never reach this bot.
    assert "顯示更多" in first_page["text"]
    assert "@Marketing Knowledge Agent" not in first_page["text"]
    assert _show_more_button(client) is not None


def test_the_show_more_button_serves_the_next_page_without_re_searching(tmp_path):
    app = _multi_page_slash_app(tmp_path)
    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))
    audit_rows_before = len(
        list(csv.DictReader((tmp_path / "audit.csv").open(encoding="utf-8")))
    )
    button = _show_more_button(client)

    _ack2, next_client = _click_show_more(app, button["value"])

    page_two = next_client.responses.sent[-1]
    # Served through the button's own fresh capability, not the ageing command one.
    assert page_two["url"] == "https://hooks.slack.com/actions/TEST/SECRET_CAPABILITY"
    assert next_client.messages == []
    assert "繼續顯示搜尋結果" in page_two["text"]
    # A continuation replays rendered text: no new search, so no new audit row.
    audit_rows_after = list(csv.DictReader((tmp_path / "audit.csv").open(encoding="utf-8")))
    assert len(audit_rows_after) == audit_rows_before


def test_the_last_page_carries_no_further_show_more_button(tmp_path):
    """Offering a button that answers 「已失效」 would be worse than offering none."""
    app = _multi_page_slash_app(tmp_path)
    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))

    _ack2, last = _click_show_more(app, _show_more_button(client)["value"])

    assert _show_more_button(last) is None


def test_another_user_cannot_advance_someone_elses_pagination(tmp_path):
    """Even holding the button value verbatim, which only its owner ever receives."""
    app = _multi_page_slash_app(tmp_path)
    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))
    button_value = _show_more_button(client)["value"]

    _ack2, other = _click_show_more(app, button_value, user_id="U2")

    assert other.responses.texts == [PAGINATION_EXPIRED_MESSAGE]
    assert other.messages == []
    # And the owner's own continuation was not consumed by the other user's click.
    _ack3, owner = _click_show_more(app, button_value, user_id="U1")
    assert "繼續顯示搜尋結果" in owner.responses.sent[-1]["text"]


def test_an_expired_continuation_answers_safely(tmp_path):
    app = _multi_page_slash_app(tmp_path)
    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))
    button_value = _show_more_button(client)["value"]
    _click_show_more(app, button_value)  # consume the only remaining page

    _ack2, expired = _click_show_more(app, button_value)

    assert expired.responses.sent[-1]["text"] == PAGINATION_EXPIRED_MESSAGE
    assert expired.messages == []


def test_a_show_more_click_without_a_valid_request_token_serves_nothing(tmp_path):
    """Pins the ownership gate on its own.

    Two independent things stop one user reading another's continuation: the token store's owner
    check, and the fact that a lane is keyed per user. Either alone is sufficient, which is why the
    cross-user test still passes when one is removed -- so each is pinned separately, or a refactor
    could delete one and leave the other silently carrying the whole guarantee. Here the lane and
    the clicker are correct and only the token is not.
    """
    app = _multi_page_slash_app(tmp_path)
    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))
    value = json.loads(_show_more_button(client)["value"])
    value["request_token"] = "f" * 32

    _ack2, forged = _click_show_more(app, json.dumps(value))

    assert forged.responses.sent[-1]["text"] == PAGINATION_EXPIRED_MESSAGE
    assert forged.messages == []


def test_the_slash_continuation_lane_is_scoped_to_the_invoking_user(tmp_path):
    """Pins the other guard: a lane id that two people could share is not a lane."""
    assert _slash_session_key("U1", "sess") != _slash_session_key("U2", "sess")
    assert _slash_session_key("U1", "a") != _slash_session_key("U1", "b")
    assert "U1" in _slash_session_key("U1", "sess")


def test_a_show_more_click_without_a_session_does_nothing(tmp_path):
    app = _multi_page_slash_app(tmp_path)
    _slash_submit(app, state_values=_state_values(tags=["會員經營"]))

    _ack, client = _click_show_more(app, json.dumps({"request_token": "x" * 32}))

    assert client.responses.sent == [] and client.messages == []


# --------------------------------------------------------------------------------------
# H/I. adjust filters and restart, in the slash flow
# --------------------------------------------------------------------------------------


def _adjust_button(client):
    for message in reversed(client.responses.sent):
        for block in message.get("blocks") or []:
            for element in block.get("elements", []):
                if element.get("action_id") == OPEN_SEARCH_MODAL_ACTION_ID:
                    return element
    return None


def _reopen_slash(app, value, *, user_id="U1", channel_id="C123"):
    client = FakeSlackClient()
    with _capture_responses() as recorder:
        app.actions[OPEN_SEARCH_MODAL_ACTION_ID](
            ack=FakeAck(),
            body=_ephemeral_action_body(user_id=user_id, channel_id=channel_id, value=value),
            client=client,
        )
    client.responses = recorder
    return client


SLASH_SECRET_GOAL = "U1 私人搜尋 competitor-churn"


def test_the_owner_reopens_their_slash_search_prefilled(tmp_path):
    app = _slash_app(tmp_path)
    _ack, client = _slash_submit(
        app, state_values=_state_values(year="2024", free_text=SLASH_SECRET_GOAL)
    )

    view = _reopen_slash(app, _adjust_button(client)["value"]).opened_views[0]["view"]

    prefill = _modal_prefill(view)
    assert prefill[INTERVIEW_YEARS_BLOCK_ID] == ["2024"]
    assert prefill[FREE_TEXT_BLOCK_ID] == SLASH_SECRET_GOAL


def test_a_different_user_reopening_a_slash_button_sees_nothing_of_it(tmp_path):
    """The session id travels in the button value, so it must not be what grants access."""
    app = _slash_app(tmp_path)
    _ack, client = _slash_submit(
        app, state_values=_state_values(year="2024", free_text=SLASH_SECRET_GOAL)
    )

    view = _reopen_slash(app, _adjust_button(client)["value"], user_id="U2").opened_views[0]["view"]

    assert SLASH_SECRET_GOAL not in json.dumps(view, ensure_ascii=False)
    assert _modal_prefill(view) == {
        INTERVIEW_YEARS_BLOCK_ID: [],
        SALES_CATEGORY_LV2_BLOCK_ID: [],
        CONTENT_TAGS_BLOCK_ID: [],
        FREE_TEXT_BLOCK_ID: "",
    }


def test_reopening_a_slash_button_from_another_conversation_discloses_nothing(tmp_path):
    app = _slash_app(tmp_path)
    _ack, client = _slash_submit(
        app, state_values=_state_values(year="2024", free_text=SLASH_SECRET_GOAL)
    )

    view = _reopen_slash(
        app, _adjust_button(client)["value"], channel_id="C_OTHER"
    ).opened_views[0]["view"]

    assert SLASH_SECRET_GOAL not in json.dumps(view, ensure_ascii=False)


def test_a_refused_slash_search_offers_a_blank_restart_and_stores_nothing(tmp_path):
    store = SlackRequestTokenStore()
    app = _slash_app(tmp_path, denylist_brands=[SECRET_CUSTOMER], request_token_store=store)

    _ack, client = _slash_submit(
        app,
        state_values=_state_values(year="2024", free_text=f"{SECRET_CUSTOMER} 的成長案例"),
    )

    assert len(store) == 0
    assert client.messages == []
    follow_up = client.responses.sent[-1]
    assert SECRET_CUSTOMER not in json.dumps(follow_up, ensure_ascii=False)
    button = follow_up["blocks"][-1]["elements"][0]
    value = json.loads(button["value"])
    # A lane id to return to, and nothing that could reopen the refused search.
    assert set(value) == {"session_id"}
    assert value["session_id"]

    # Restarting opens a blank modal, defaulted to 「全部年份」.
    view = _reopen_slash(app, button["value"]).opened_views[0]["view"]
    assert _modal_prefill(view) == {
        INTERVIEW_YEARS_BLOCK_ID: [],
        SALES_CATEGORY_LV2_BLOCK_ID: [],
        CONTENT_TAGS_BLOCK_ID: [],
        FREE_TEXT_BLOCK_ID: "",
    }
    assert SECRET_CUSTOMER not in json.dumps(view, ensure_ascii=False)


def test_a_slash_result_keeps_its_clickable_approved_asset_title(tmp_path):
    """The ephemeral boundary must not disturb what the renderer produced."""
    app = _slash_app(tmp_path)

    _ack, client = _slash_submit(app, state_values=_state_values(year="2024"))

    assert "大春煉皂" in client.responses.sent[0]["text"]


def test_every_slash_message_goes_through_the_response_url_boundary(tmp_path):
    """Unfurl suppression is now a property of the boundary, so this proves nothing bypasses it.

    ``post_slack_response_url`` forces ``unfurl_links``/``unfurl_media`` false along with
    ``response_type`` and ``replace_original``; that forcing is asserted directly in
    ``test_slack_interface.py``. What matters here is that every message a search produces --
    result page, action message, continuation -- actually leaves through that one function, and
    that none of them reaches the channel.
    """
    app = _multi_page_slash_app(tmp_path)

    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))
    _ack2, next_client = _click_show_more(app, _show_more_button(client)["value"])

    sent = client.responses.sent + next_client.responses.sent
    assert len(sent) >= 3
    for message in sent:
        assert message["url"].startswith("https://hooks.slack.com/")
        assert "channel" not in message
    assert client.messages == [] and next_client.messages == []


# ======================================================================================
# Codex Independent Delta Review R1 -- blocking finding remediation
# ======================================================================================

# --------------------------------------------------------------------------------------
# R1 Finding 1: mention trailing text must never be persisted in slash_faceted_only
# --------------------------------------------------------------------------------------


def _audit_text(path):
    """Everything this Slack path wrote, as raw bytes-on-disk text.

    Read as one string on purpose. Asserting that a specific event is absent only proves that one
    row shape is clean; the finding was a *different* row shape carrying the same text, written by
    a path nobody was looking at. The question worth asking is whether the secret is anywhere in
    the file at all.
    """
    return Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""


def _mention_in_slash_mode(tmp_path, event, ask_fn=None):
    audit = tmp_path / "audit.csv"

    def _must_not_retrieve(*_args, **_kwargs):
        raise AssertionError("app_mention must not retrieve in slash_faceted_only mode")

    reply = handle_slack_event(
        event,
        config=SlackConfig(
            allowed_channel_ids=["C123"], search_entry_mode=ENTRY_MODE_SLASH_FACETED_ONLY
        ),
        ask_fn=ask_fn or _must_not_retrieve,
        audit_log_path=audit,
        faceted_search_enabled=True,
    )
    return reply, _audit_text(audit)


def test_slash_mode_allowed_channel_mention_persists_no_trailing_text(tmp_path):
    """R1 finding 1, case 1."""
    reply, audit = _mention_in_slash_mode(
        tmp_path,
        {"text": f"<@BOT> {SECRET_CUSTOMER}", "channel": "C123", "user": "U1", "ts": "100.1"},
    )

    assert reply["text"] == APP_MENTION_GUIDANCE_MESSAGE
    assert SECRET_CUSTOMER not in audit
    assert SECRET_CUSTOMER not in json.dumps(reply, ensure_ascii=False)


def test_slash_mode_denied_channel_mention_persists_no_trailing_text(tmp_path):
    """R1 finding 1, case 2 -- the reproduced blocker.

    The authorization path predates the entry mode and records the raw question, which is the
    right trade for a natural-language search surface and the wrong one here: the same text that
    is not a query in an allowed channel is not a query in a denied one either.
    """
    reply, audit = _mention_in_slash_mode(
        tmp_path,
        {"text": f"<@BOT> {SECRET_CUSTOMER}", "channel": "C_OTHER", "user": "U1", "ts": "100.1"},
    )

    assert reply["text"] == DENIED_CHANNEL_MESSAGE
    assert SECRET_CUSTOMER not in audit
    assert SECRET_CUSTOMER not in json.dumps(reply, ensure_ascii=False)
    # The denial itself is still recorded -- only its query column is dropped.
    rows = list(csv.DictReader((tmp_path / "audit.csv").open(encoding="utf-8")))
    assert [r["event"] for r in rows] == ["slack_denied_channel"]
    assert rows[0]["query"] == ""
    assert rows[0]["channel_id"] == "C_OTHER" and rows[0]["user_id"] == "U1"


@pytest.mark.parametrize("channel_type", ["im", "mpim"])
def test_slash_mode_direct_message_mention_persists_no_trailing_text(tmp_path, channel_type):
    """R1 finding 1, case 3. A DM is exactly where a customer name gets typed without thinking."""
    reply, audit = _mention_in_slash_mode(
        tmp_path,
        {
            "text": f"<@BOT> {SECRET_CUSTOMER}",
            "channel": "D1",
            "user": "U1",
            "ts": "100.1",
            "channel_type": channel_type,
        },
    )

    assert SECRET_CUSTOMER not in audit
    assert SECRET_CUSTOMER not in json.dumps(reply, ensure_ascii=False)
    rows = list(csv.DictReader((tmp_path / "audit.csv").open(encoding="utf-8")))
    assert all(row["query"] == "" for row in rows)


def test_mention_mixed_denied_channel_audit_is_unchanged(tmp_path):
    """R1 finding 1, case 4: backward compatibility.

    In the default mode the text really is an attempted search, so the pre-existing audit contract
    stands. Pinned so the slash-only fix cannot quietly become a global removal of legacy audit.
    """
    audit = tmp_path / "audit.csv"
    reply = handle_slack_event(
        {"text": "<@BOT> 大春煉皂的成長案例", "channel": "C_OTHER", "user": "U1", "ts": "100.1"},
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=lambda *a, **k: pytest.fail("a denied channel must not retrieve"),
        audit_log_path=audit,
    )

    assert reply["text"] == DENIED_CHANNEL_MESSAGE
    rows = list(csv.DictReader(audit.open(encoding="utf-8")))
    assert [r["event"] for r in rows] == ["slack_denied_channel"]
    assert rows[0]["query"] == "<@BOT> 大春煉皂的成長案例"


# --------------------------------------------------------------------------------------
# R1 Finding 2: a legacy mention artifact must not stay executable after a mode switch
# --------------------------------------------------------------------------------------


def _legacy_private_metadata(catalog_version, channel_id="C123", thread_ts="1"):
    """``private_metadata`` exactly as the mention flow wrote it before the mode switch.

    Built literally rather than by calling the mention handler, because after the fix that handler
    will not produce one under this mode -- and the case being tested is precisely a modal that
    already existed when the mode changed.
    """
    return json.dumps(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "catalog_version": catalog_version,
            "entrypoint": ENTRYPOINT_APP_MENTION,
            "session_id": "",
        },
        ensure_ascii=False,
    )


def _click_open_modal(app, value, body=None):
    client = FakeSlackClient()
    with _capture_responses() as recorder:
        app.actions[OPEN_SEARCH_MODAL_ACTION_ID](
            ack=FakeAck(), body=body or _action_body(value=value), client=client
        )
    client.responses = recorder
    return client


@pytest.mark.parametrize(
    "value",
    [
        json.dumps({}),                              # legacy 開啟條件搜尋 / 重新搜尋
        json.dumps({"request_token": "a" * 32}),     # legacy 調整條件
    ],
    ids=["legacy_open_or_restart_button", "legacy_adjust_button"],
)
def test_a_legacy_mention_button_cannot_open_a_modal_in_slash_mode(tmp_path, value):
    """R1 finding 2, cases A/B/C.

    These buttons are still sitting in channel history after the switch, so somebody will click
    one. None may open a modal, disclose a prior request, or put anything in the channel.
    """
    app = _slash_app(tmp_path)

    client = _click_open_modal(app, value)

    assert client.opened_views == []
    assert client.messages == []
    # A courtesy pointer to the entry that exists now, visible only to the clicker and carrying
    # nothing else.
    assert [m["text"] for m in client.responses.sent] == [STALE_ENTRY_MODE_MESSAGE_SLASH]
    assert "/mka" in client.responses.sent[0]["text"]
    # Delivered through this click's own response_url, so it reaches the clicker wherever
    # they are -- including a conversation the bot is not a member of.
    assert client.responses.sent[0]["url"].startswith("https://hooks.slack.com/")


def test_a_legacy_adjust_button_discloses_nothing_of_the_prior_request(tmp_path):
    """R1 finding 2, case B, stated as the disclosure it prevents."""
    store = SlackRequestTokenStore()
    app = _slash_app(tmp_path, request_token_store=store)
    token = store.store(
        StructuredSearchRequest(interview_years=(2024,), free_text=SLASH_SECRET_GOAL),
        owner_user_id="U1",
        channel_id="C123",
        session_key="1",
    )

    client = _click_open_modal(app, json.dumps({"request_token": token}))

    assert client.opened_views == []
    assert SLASH_SECRET_GOAL not in json.dumps(
        client.responses.sent + client.messages, ensure_ascii=False
    )


def test_a_legacy_modal_submitted_after_the_mode_switch_executes_nothing(tmp_path):
    """R1 finding 2, case D -- the half a button-only fix would miss.

    This submission never passed through today's action handler: the modal was opened before the
    switch. So the view handler has to re-decide, rather than trusting the entry point its own
    ``private_metadata`` records.
    """
    app = _slash_app(tmp_path)
    catalog_version = json.loads(_slash_private_metadata(app))["catalog_version"]
    audit_before = _audit_text(tmp_path / "audit.csv")

    ack, client = FakeAck(), FakeSlackClient()
    with _capture_responses() as recorder:
        app.views[FACETED_SEARCH_MODAL_CALLBACK_ID](
            ack=ack,
            body={"user": {"id": "U1"}},
            client=client,
            view={
                "private_metadata": _legacy_private_metadata(catalog_version),
                "state": {"values": _state_values(year="2024")},
            },
        )

    assert client.messages == []
    assert recorder.sent == []
    # No search ran, so no search audit row was added.
    assert _audit_text(tmp_path / "audit.csv") == audit_before
    assert "slack_faceted_search" not in _audit_text(tmp_path / "audit.csv")
    # The modal explains itself rather than closing silently on a result that never arrives.
    assert ack.calls == [
        {
            "response_action": "errors",
            "errors": {FREE_TEXT_BLOCK_ID: STALE_ENTRY_MODE_MESSAGE_SLASH},
        }
    ]


def test_no_public_message_is_reachable_from_a_legacy_artifact_in_slash_mode(tmp_path):
    """R1 finding 2, the invariant behind it, asserted end to end at handler level.

    The reproduced blocker was a chain: legacy button opens a modal, the modal records
    ``entrypoint=app_mention``, the submission trusts that, and the result goes to the channel. So
    the whole chain is walked here, not just its first link.
    """
    app = _slash_app(tmp_path)
    catalog_version = json.loads(_slash_private_metadata(app))["catalog_version"]

    opened = _click_open_modal(app, json.dumps({}))
    assert opened.opened_views == []

    ack, submitted = FakeAck(), FakeSlackClient()
    app.views[FACETED_SEARCH_MODAL_CALLBACK_ID](
        ack=ack,
        body={"user": {"id": "U1"}},
        client=submitted,
        view={
            "private_metadata": _legacy_private_metadata(catalog_version),
            "state": {"values": _state_values(year="2024")},
        },
    )

    assert opened.messages == [] and submitted.messages == []


def test_a_valid_slash_submission_still_works_after_the_gate(tmp_path):
    """R1 finding 2, case E. The gate must refuse stale artifacts, not the live flow."""
    app = _slash_app(tmp_path)

    _ack, client = _slash_submit(app, state_values=_state_values(year="2024"))

    assert client.messages == []
    assert client.responses.sent
    assert "大春煉皂" in client.responses.sent[0]["text"]


def test_mention_mixed_interactions_are_unaffected_by_the_gate(tmp_path):
    """R1 finding 2, case F. The default mode's button and modal must still work end to end."""
    app, _tmp = _run_bot_and_get_app(tmp_path)

    opened = _click_open_modal(app, json.dumps({}))
    assert len(opened.opened_views) == 1

    ack, submitted = FakeAck(), FakeSlackClient()
    with _capture_responses() as recorder:
        app.views[FACETED_SEARCH_MODAL_CALLBACK_ID](
            ack=ack,
            body={"user": {"id": "U1"}},
            client=submitted,
            view={
                "private_metadata": opened.opened_views[0]["view"]["private_metadata"],
                "state": {"values": _state_values(year="2024")},
            },
        )

    # The legacy flow answers in-channel, as it always has -- and never through a response_url.
    assert len(submitted.messages) == 2
    assert recorder.sent == []


def test_a_slash_artifact_is_refused_in_mention_mixed_too(tmp_path):
    """The symmetric direction, which the shared rule gives for free.

    ``/mka`` is not registered in ``mention_mixed``, so no slash session can legitimately exist
    there; anything claiming one is stale or forged.
    """
    app, _tmp = _run_bot_and_get_app(tmp_path)

    client = _click_open_modal(app, json.dumps({"session_id": "sess-1"}))

    assert client.opened_views == []
    assert client.messages == []
    # Codex R2 P3-1: the guidance has to name the entry this mode actually has. Telling the user
    # to type ``/mka`` here would send them to a command ``mention_mixed`` never registers, leaving
    # the advice as stale as the button that produced it.
    assert [m["text"] for m in client.responses.sent] == [STALE_ENTRY_MODE_MESSAGE_MENTION]
    assert "/mka" not in client.responses.sent[0]["text"]
    assert "@Marketing Knowledge Agent" in client.responses.sent[0]["text"]
    # Delivered through this click's own response_url, so it reaches the clicker wherever
    # they are -- including a conversation the bot is not a member of.
    assert client.responses.sent[0]["url"].startswith("https://hooks.slack.com/")


def test_a_stale_slash_modal_submitted_in_mention_mixed_is_refused_with_mode_correct_guidance(
    tmp_path,
):
    """Codex R2 P3-1, at the other gate: a submission is refused the same way a click is.

    Both refusal paths must speak for the mode running now, or one of them quietly keeps telling
    users about an entry point that is not there.
    """
    app, _tmp = _run_bot_and_get_app(tmp_path)
    catalog_version = json.loads(
        _click_open_modal(app, json.dumps({})).opened_views[0]["view"]["private_metadata"]
    )["catalog_version"]
    stale_slash_metadata = json.dumps(
        {
            "channel_id": "C123",
            "thread_ts": "",
            "catalog_version": catalog_version,
            "entrypoint": ENTRYPOINT_SLASH_COMMAND,
            "session_id": "sess-1",
        },
        ensure_ascii=False,
    )
    audit_before = _audit_text(tmp_path / "audit.csv")

    ack, client = FakeAck(), FakeSlackClient()
    with _capture_responses() as recorder:
        app.views[FACETED_SEARCH_MODAL_CALLBACK_ID](
            ack=ack,
            body={"user": {"id": "U1"}},
            client=client,
            view={
                "private_metadata": stale_slash_metadata,
                "state": {"values": _state_values(year="2024")},
            },
        )

    # Refused, and nothing executed -- the security half is unchanged by this cleanup.
    assert client.messages == [] and recorder.sent == []
    assert _audit_text(tmp_path / "audit.csv") == audit_before
    assert ack.calls == [
        {
            "response_action": "errors",
            "errors": {FREE_TEXT_BLOCK_ID: STALE_ENTRY_MODE_MESSAGE_MENTION},
        }
    ]
    assert "/mka" not in ack.calls[0]["errors"][FREE_TEXT_BLOCK_ID]


def test_stale_guidance_names_the_entry_the_current_mode_actually_has():
    """Pinned directly, and asserted as text rather than as constant identity.

    Comparing the resolver's output to the same constant the resolver returns would pass however
    the sentences were swapped; what matters is which entry point each one names.
    """
    slash = stale_entry_mode_message(ENTRY_MODE_SLASH_FACETED_ONLY)
    mention = stale_entry_mode_message(ENTRY_MODE_MENTION_MIXED)

    assert "/mka" in slash
    assert "@Marketing Knowledge Agent" not in slash
    assert "/mka" not in mention
    assert "@Marketing Knowledge Agent" in mention
    # Neither may carry anything from the interaction that was refused.
    for message in (slash, mention):
        assert "request_token" not in message and "session_id" not in message


def test_entrypoint_allowed_for_mode_is_the_single_rule_both_gates_share():
    """Pinned directly so the two call sites cannot drift into different rules."""
    assert entrypoint_allowed_for_mode(ENTRY_MODE_SLASH_FACETED_ONLY, ENTRYPOINT_SLASH_COMMAND)
    assert not entrypoint_allowed_for_mode(ENTRY_MODE_SLASH_FACETED_ONLY, ENTRYPOINT_APP_MENTION)
    assert entrypoint_allowed_for_mode(ENTRY_MODE_MENTION_MIXED, ENTRYPOINT_APP_MENTION)
    assert not entrypoint_allowed_for_mode(ENTRY_MODE_MENTION_MIXED, ENTRYPOINT_SLASH_COMMAND)
    # An unrecognised entry point is not authorized by either mode.
    for mode in (ENTRY_MODE_SLASH_FACETED_ONLY, ENTRY_MODE_MENTION_MIXED):
        assert not entrypoint_allowed_for_mode(mode, "")
        assert not entrypoint_allowed_for_mode(mode, "something_else")


def test_a_stale_show_more_button_is_refused_in_mention_mixed(tmp_path):
    """The continuation button is only registered in slash mode, and gated there as well."""
    slash = _slash_app(tmp_path)
    assert SHOW_MORE_ACTION_ID in slash.actions

    mention, _tmp = _run_bot_and_get_app(tmp_path / "mention")
    assert SHOW_MORE_ACTION_ID not in mention.actions


# ======================================================================================
# Human UAT R1 -- slash delivery must not depend on bot membership
# ======================================================================================


def _capability_store(app_tmp_path=None):
    from marketing_knowledge_agent.slack_response_urls import SlackResponseUrlStore

    return SlackResponseUrlStore()


def test_the_command_captures_its_capability_and_never_writes_it_into_the_modal(tmp_path):
    """Routing test A. The capability is server-side; the modal carries only the lane id."""
    app = _slash_app(tmp_path)

    _ack, client = _run_command(app)

    view = client.opened_views[0]["view"]
    serialized = json.dumps(view, ensure_ascii=False)
    assert CAPABILITY_SECRET not in serialized
    assert "response_url" not in json.loads(view["private_metadata"])
    assert "hooks.slack.com" not in serialized


def test_a_command_without_a_usable_capability_opens_no_session(tmp_path):
    """No reply path means no search. Opening the modal would let a user do real work for nothing.

    Human UAT is the reason this is a refusal rather than a hope: the failure mode being prevented
    is retrieval succeeding and the result having nowhere to go.
    """
    app = _slash_app(tmp_path)

    for bad in ("", "https://evil.test/commands/T/1/x", "http://hooks.slack.com/x"):
        _ack, client = _run_command(app, response_url=bad)
        assert client.opened_views == [], bad
        assert client.responses.sent == [], bad
        assert client.messages == [], bad


def test_command_trailing_secret_text_is_never_stored_or_sent(tmp_path):
    """Routing test A, second half: the capability is captured, the text still is not."""
    app = _slash_app(tmp_path)
    audit = tmp_path / "audit.csv"

    _ack, client = _run_command(app, text=SECRET_CUSTOMER)

    assert SECRET_CUSTOMER not in json.dumps(client.opened_views, ensure_ascii=False)
    assert SECRET_CUSTOMER not in json.dumps(client.responses.sent, ensure_ascii=False)
    assert not audit.exists()


def test_an_unrestricted_conversation_is_answered_without_chat_post_ephemeral(tmp_path):
    """Routing test B -- the blocking Human UAT finding, stated as the fix.

    ``slash_command_allowed_channel_ids`` is absent, so ``/mka`` is usable from any conversation.
    The fake Slack client has no ``chat_postEphemeral`` at all any more, so a handler that reached
    for it would raise rather than quietly regress.
    """
    app = _slash_app(tmp_path)

    _ack, client = _slash_submit(
        app, state_values=_state_values(year="2024"), channel_id="C_BOT_IS_NOT_A_MEMBER"
    )

    assert client.messages == []
    assert client.responses.sent
    assert all(m["url"] == FAKE_RESPONSE_URL for m in client.responses.sent)
    assert not hasattr(client, "chat_postEphemeral")


def test_a_denied_conversation_is_told_so_through_its_own_capability(tmp_path):
    """Routing test C. This is precisely the path that went unanswered in live UAT."""
    app = _slash_app(tmp_path, slash_allowed_channel_ids=["C123"])
    audit = tmp_path / "audit.csv"

    _ack, client = _run_command(app, channel_id="C_OUTSIDE")

    assert client.responses.texts == [DENIED_CHANNEL_MESSAGE]
    assert client.responses.sent[0]["url"] == FAKE_RESPONSE_URL
    assert client.opened_views == []
    assert client.messages == []
    assert not audit.exists()


def test_a_submission_whose_capability_is_gone_runs_no_search(tmp_path):
    """Routing test D/E. The check happens before retrieval, not after it."""
    app = _slash_app(tmp_path)
    metadata = _slash_private_metadata(app)
    session_id = json.loads(metadata)["session_id"]
    audit_before = _audit_text(tmp_path / "audit.csv")

    # Spend the capability down to nothing, the way five replies would.
    store = _slash_response_url_store(app)
    for _ in range(10):
        store.reserve(user_id="U1", channel_id="C123", session_key=f"U1:{session_id}")

    ack, client = _slash_submit(app, state_values=_state_values(year="2024"), metadata=metadata)

    assert client.messages == [] and client.responses.sent == []
    assert _audit_text(tmp_path / "audit.csv") == audit_before
    assert ack.calls == [
        {
            "response_action": "errors",
            "errors": {FREE_TEXT_BLOCK_ID: SLASH_SESSION_EXPIRED_MESSAGE},
        }
    ]


@pytest.mark.parametrize(
    "wrong",
    [
        {"user_id": "U2"},
        {"channel_id": "C_OTHER"},
    ],
    ids=["wrong_user", "wrong_channel"],
)
def test_a_submission_from_the_wrong_context_cannot_borrow_the_capability(tmp_path, wrong):
    """Routing test D. The capability is bound to who and where, not just to the session id."""
    app = _slash_app(tmp_path)
    metadata = json.loads(_slash_private_metadata(app))
    channel_id = wrong.get("channel_id", "C123")
    metadata["channel_id"] = channel_id
    audit_before = _audit_text(tmp_path / "audit.csv")

    ack, client = _slash_submit(
        app,
        state_values=_state_values(year="2024"),
        user_id=wrong.get("user_id", "U1"),
        metadata=json.dumps(metadata),
    )

    assert client.messages == [] and client.responses.sent == []
    assert _audit_text(tmp_path / "audit.csv") == audit_before


def test_a_search_spends_exactly_two_of_its_five_sends(tmp_path):
    """Routing test E. Result page plus action message, inside Slack's documented budget."""
    from marketing_knowledge_agent.slack_response_urls import MAX_USES

    app = _slash_app(tmp_path)
    metadata = _slash_private_metadata(app)
    session_id = json.loads(metadata)["session_id"]
    store = _slash_response_url_store(app)

    _ack, client = _slash_submit(app, state_values=_state_values(year="2024"), metadata=metadata)

    assert len(client.responses.sent) == 2
    assert store.remaining_uses(
        user_id="U1", channel_id="C123", session_key=f"U1:{session_id}"
    ) == MAX_USES - 2


def test_adjust_refreshes_the_capability_from_the_click(tmp_path):
    """Routing test G. The next submission must not depend on the ageing command capability."""
    app = _slash_app(tmp_path)
    _ack, client = _slash_submit(app, state_values=_state_values(year="2024"))
    button = _adjust_button(client)
    session_id = json.loads(button["value"])["session_id"]
    store = _slash_response_url_store(app)
    assert store.remaining_uses(
        user_id="U1", channel_id="C123", session_key=f"U1:{session_id}"
    ) == 3

    _reopen_slash(app, button["value"])

    # Refreshed from the action payload: a full budget again, and the newer URL.
    assert store.remaining_uses(
        user_id="U1", channel_id="C123", session_key=f"U1:{session_id}"
    ) == 5
    assert store.reserve(
        user_id="U1", channel_id="C123", session_key=f"U1:{session_id}"
    ).spend() == "https://hooks.slack.com/actions/TEST/SECRET_CAPABILITY"


def test_restart_after_a_refusal_also_refreshes_the_capability(tmp_path):
    """Routing test H, and the refused text still is not retained anywhere."""
    store_tokens = SlackRequestTokenStore()
    app = _slash_app(tmp_path, denylist_brands=[SECRET_CUSTOMER], request_token_store=store_tokens)
    _ack, client = _slash_submit(
        app, state_values=_state_values(year="2024", free_text=f"{SECRET_CUSTOMER} 案例")
    )
    button = client.responses.sent[-1]["blocks"][-1]["elements"][0]
    session_id = json.loads(button["value"])["session_id"]
    store = _slash_response_url_store(app)

    reopened = _reopen_slash(app, button["value"])

    assert store.remaining_uses(
        user_id="U1", channel_id="C123", session_key=f"U1:{session_id}"
    ) == 5
    assert len(store_tokens) == 0
    assert SECRET_CUSTOMER not in json.dumps(reopened.opened_views, ensure_ascii=False)


def test_no_capability_ever_reaches_a_button_an_audit_row_or_the_user(tmp_path):
    """Routing test J. The one thing a bearer capability must never do is travel."""
    app = _multi_page_slash_app(tmp_path)

    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))
    _ack2, more = _click_show_more(app, _show_more_button(client)["value"])
    reopened = _reopen_slash(app, _adjust_button(client)["value"])

    audit = _audit_text(tmp_path / "audit.csv")
    assert CAPABILITY_SECRET not in audit
    assert "hooks.slack.com" not in audit
    for surface in (
        [m.get("text", "") for m in client.responses.sent + more.responses.sent],
        [m.get("blocks") for m in client.responses.sent + more.responses.sent],
        reopened.opened_views,
    ):
        assert CAPABILITY_SECRET not in json.dumps(surface, ensure_ascii=False)


def _slash_response_url_store(app):
    """The store the registered handlers closed over."""
    from marketing_knowledge_agent.slack_response_urls import default_response_url_store

    return default_response_url_store()


# ======================================================================================
# Human UAT R1 -- result presentation
# ======================================================================================


def test_a_result_card_shows_the_brand_and_its_assets_and_nothing_else(tmp_path):
    """Presentation matrix. Handle and the two category lines are gone from the card.

    They were three lines of data-model detail between the brand name and the content the user
    came for. What replaces them is nothing -- the assets simply start immediately.
    """
    app = _slash_app(tmp_path)

    _ack, client = _slash_submit(app, state_values=_state_values(year="2024"))

    result = client.responses.sent[0]["text"]
    assert "Handle：" not in result
    assert "Sales Category LV1：" not in result
    assert "Sales Category LV2：" not in result
    # Still a result: the brand, its asset type, and a clickable approved title.
    assert "大春煉皂" in result
    assert "文章" in result or "影片" in result or "Podcast" in result


def test_the_applied_conditions_echo_the_new_wording(tmp_path):
    """The surface answers in the words it asked the question in."""
    app = _slash_app(tmp_path)

    _ack, client = _slash_submit(
        app, state_values=_state_values(lv2=["食品/飲料"], tags=["會員經營"])
    )

    conditions = client.responses.sent[0]["text"].splitlines()[0]
    assert "已套用搜尋條件" in conditions
    assert "品牌產業別" in conditions
    assert "功能" in conditions
    assert "Sales Category LV2" not in conditions
    assert "內容相關標籤" not in conditions


def test_the_slack_label_change_did_not_reach_the_field_registry():
    """The CLI, ``explain-query`` and every non-Slack caller keep their own labels.

    Renaming in ``FIELD_REGISTRY`` would have changed output this work package has no business
    touching, so the Slack mapping is scoped to the Slack renderer.
    """
    from marketing_knowledge_agent.query_planning import FIELD_REGISTRY

    assert FIELD_REGISTRY["sales_category_lv2"].output_label != "品牌產業別"
    assert FIELD_REGISTRY["content_tags"].output_label != "功能"


def test_hiding_the_handle_did_not_disable_conflicting_handle_protection(tmp_path):
    """§18's actual risk, guarded directly.

    Grouping drops a brand group outright when its records disagree on the handle -- that is what
    stops two different merchants being merged under one name. Removing the three lines from the
    card must not remove the data those rules read.
    """
    from marketing_knowledge_agent.slack_presentation import _presentation_entities

    records = [
        _metadata("同名品牌", "handle-a", "食品/飲料", ["會員經營"], 2024, source_row=1),
        _metadata("同名品牌", "handle-b", "食品/飲料", ["會員經營"], 2024, source_row=2),
    ]
    documents = [
        Document(id=f"doc-{i}", metadata=m, content=c)
        for i, (m, c) in enumerate(records, start=1)
    ]
    db_path = tmp_path / "conflict_index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    app = _slash_app(tmp_path, db_path=db_path)

    _ack, client = _slash_submit(app, state_values=_state_values(year="2024"))

    # The conflicting group is withheld rather than merged under one of the two handles.
    assert "同名品牌" not in client.responses.sent[0]["text"]


# ======================================================================================
# Independent review R1 — reservation before retrieval, at handler level
# ======================================================================================


def _retrieval_spy(monkeypatch=None):
    """Count calls to ``execute_structured_search`` as the handler sees it."""
    import marketing_knowledge_agent.slack_interface as _si

    calls = []
    original = _si.execute_structured_search

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    _si.execute_structured_search = counting
    return calls, original


def _restore_retrieval(original):
    import marketing_knowledge_agent.slack_interface as _si

    _si.execute_structured_search = original


def test_no_retrieval_runs_when_the_last_use_was_taken_first(tmp_path):
    """§19. The reservation is the gate, and it sits in front of the search.

    A capability with one use left; something else consumes it; then the modal is submitted. The
    submission must fail *before* any retrieval, not after -- a search that runs and then discovers
    it cannot reply has already spent the work and the governance.
    """
    app = _slash_app(tmp_path)
    metadata = _slash_private_metadata(app)
    session_id = json.loads(metadata)["session_id"]
    session_key = f"U1:{session_id}"
    store = _slash_response_url_store(app)

    # Reduce the lane to exactly one remaining use, then let another handler take it. Bounded by
    # the budget rather than by the counter reaching a value: an unbounded "drain until" loop spins
    # forever the moment the store stops decrementing, which is precisely the mutation the probes
    # apply -- a test that hangs reports nothing.
    for _ in range(MAX_RESPONSE_USES):
        if store.remaining_uses(user_id="U1", channel_id="C123", session_key=session_key) <= 1:
            break
        store.reserve(user_id="U1", channel_id="C123", session_key=session_key)
    stolen = store.reserve(user_id="U1", channel_id="C123", session_key=session_key)
    assert stolen is not None
    assert store.remaining_uses(user_id="U1", channel_id="C123", session_key=session_key) == 0

    audit_before = _audit_text(tmp_path / "audit.csv")
    calls, original = _retrieval_spy()
    try:
        ack, client = _slash_submit(
            app, state_values=_state_values(year="2024"), metadata=metadata
        )
    finally:
        _restore_retrieval(original)

    assert calls == []                                   # retrieval never ran
    assert client.responses.sent == [] and client.messages == []
    assert _audit_text(tmp_path / "audit.csv") == audit_before
    assert ack.calls == [
        {
            "response_action": "errors",
            "errors": {FREE_TEXT_BLOCK_ID: SLASH_SESSION_EXPIRED_MESSAGE},
        }
    ]


def test_the_submission_that_reserves_first_may_retrieve_and_reply(tmp_path):
    """§19, the inverse. The gate must refuse a spent capability, not a live one."""
    app = _slash_app(tmp_path)
    metadata = _slash_private_metadata(app)
    session_id = json.loads(metadata)["session_id"]
    session_key = f"U1:{session_id}"
    store = _slash_response_url_store(app)

    calls, original = _retrieval_spy()
    try:
        _ack, client = _slash_submit(
            app, state_values=_state_values(year="2024"), metadata=metadata
        )
    finally:
        _restore_retrieval(original)

    assert calls == [1]
    assert client.responses.sent
    # And the second handler now finds nothing left to take beyond the budget already spent.
    remaining = store.remaining_uses(user_id="U1", channel_id="C123", session_key=session_key)
    assert remaining == MAX_RESPONSE_USES - 2          # result + action message


def test_a_validation_error_spends_no_use(tmp_path):
    """A handful of ordinary mistakes must not exhaust a session that never ran a search."""
    app = _slash_app(tmp_path)
    metadata = _slash_private_metadata(app)
    session_id = json.loads(metadata)["session_id"]
    session_key = f"U1:{session_id}"
    store = _slash_response_url_store(app)
    before = store.remaining_uses(user_id="U1", channel_id="C123", session_key=session_key)

    for _ in range(3):
        ack, client = _slash_submit(
            app, state_values=_state_values(free_text="關鍵字"), metadata=metadata
        )
        assert ack.calls[0]["response_action"] == "errors"
        assert client.responses.sent == []

    assert store.remaining_uses(
        user_id="U1", channel_id="C123", session_key=session_key
    ) == before


def test_two_concurrent_show_more_clicks_on_a_final_use_send_once(tmp_path):
    """§14. Pagination is on the same atomic mechanism as everything else."""
    import marketing_knowledge_agent.slack_interface as _si

    app = _multi_page_slash_app(tmp_path)
    _ack, client = _slash_submit(app, state_values=_state_values(tags=["會員經營"]))
    button_value = _show_more_button(client)["value"]
    session_id = json.loads(button_value)["session_id"]
    session_key = f"U1:{session_id}"
    store = _slash_response_url_store(app)

    # The click refreshes the lane, so force the shared capability down to its final use by
    # pinning the refresh out and leaving exactly one.
    sent = []
    original_send = _si.post_slack_response_url
    original_store = store.store
    _si.post_slack_response_url = lambda reservation, message: sent.append(reservation.spend())
    store.store = lambda *a, **k: False          # refresh disabled: both clicks share one budget
    for _ in range(MAX_RESPONSE_USES):
        if store.remaining_uses(user_id="U1", channel_id="C123", session_key=session_key) <= 1:
            break
        store.reserve(user_id="U1", channel_id="C123", session_key=session_key)

    barrier = threading.Barrier(2, timeout=5)

    def click():
        barrier.wait()
        app.actions[SHOW_MORE_ACTION_ID](
            ack=FakeAck(),
            body=_ephemeral_action_body(value=button_value),
            client=FakeSlackClient(),
        )

    threads = [threading.Thread(target=click) for _ in range(2)]
    try:
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)
    finally:
        _si.post_slack_response_url = original_send
        store.store = original_store

    assert len(sent) == 1
