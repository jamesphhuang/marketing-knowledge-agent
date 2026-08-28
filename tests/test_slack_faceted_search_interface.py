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
from datetime import date

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata
from marketing_knowledge_agent.search_taxonomy import SearchTaxonomyError
from marketing_knowledge_agent.slack_faceted_search import (
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
    FACETED_SEARCH_STALE_CATALOG_MESSAGE,
    PAGINATION_EXPIRED_MESSAGE,
    SlackConfig,
    SlackInterfaceError,
    handle_slack_event,
    load_slack_config,
    run_slack_bot,
)
from marketing_knowledge_agent.slack_pagination import SlackPaginationStore
from marketing_knowledge_agent.slack_request_tokens import SlackRequestTokenStore
from marketing_knowledge_agent.structured_search import StructuredSearchGovernanceError

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

    def event(self, name):
        def register(fn):
            self.events[name] = fn
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


class FakeSlackClient:
    def __init__(self):
        self.opened_views = []
        self.messages = []

    def views_open(self, trigger_id, view):
        self.opened_views.append({"trigger_id": trigger_id, "view": view})

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)


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
):
    config = {"allowed_channel_ids": ["C123"], "enable_faceted_search": enable}
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
    store.start(("C123", "1"), ["page one", "page two"])

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
    request_token_store=None,
):
    db_path = db_path or _build_index(tmp_path)
    workbook_path, sha256 = _write_taxonomy(tmp_path)
    config_path = _write_slack_config(
        tmp_path,
        workbook_path=workbook_path,
        sha256=sha256,
        enable_approved_asset_urls=enable_approved_asset_urls,
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
            "state": {"values": _state_values(years=["2024"], free_text="會員回購")},
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
    assert {opt["value"] for opt in year_block["element"]["initial_options"]} == {"2024"}
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
    assert "initial_options" not in year_block["element"]
    free_text_block = next(b for b in view["blocks"] if b["block_id"] == FREE_TEXT_BLOCK_ID)
    assert "initial_value" not in free_text_block["element"]


# --------------------------------------------------------------------------------------
# view submission handler: faceted_search_modal
# --------------------------------------------------------------------------------------


def _state_values(years=None, lv2=None, tags=None, free_text=None):
    def _options(values):
        return {"selected_options": [{"value": v} for v in values]} if values else {"selected_options": []}

    return {
        INTERVIEW_YEARS_BLOCK_ID: {INTERVIEW_YEARS_ACTION_ID: _options(years)},
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
        "state": {"values": _state_values(years=["2024"])},
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
        "state": {"values": _state_values(years=["2024"])},
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
        "state": {"values": _state_values(years=["2024"])},
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
        app, state_values=_state_values(years=["2024"], free_text=f"{secret} 的成長案例")
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

    _submit(app, state_values=_state_values(years=["2024"], free_text="會員回購"))

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

    _submit(app, state_values=_state_values(years=["2024"]))

    assert len(applied) == 1


def test_approved_asset_urls_are_not_applied_when_disabled(tmp_path, monkeypatch):
    applied = []
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface._apply_approved_asset_urls",
        lambda answer, db_path: applied.append(db_path) or None,
    )
    app, _tmp_path = _run_bot_and_get_app(tmp_path, enable_approved_asset_urls=False)

    _submit(app, state_values=_state_values(years=["2024"]))

    assert applied == []


def test_approved_asset_url_overlay_unavailable_is_audited_without_aborting_the_search(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface._apply_approved_asset_urls",
        lambda answer, db_path: APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE,
    )
    app, tmp_path_ = _run_bot_and_get_app(tmp_path, enable_approved_asset_urls=True)

    _ack, client = _submit(app, state_values=_state_values(years=["2024"]))

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

    _submit(app, state_values=_state_values(years=["2024"], free_text=f"{secret} 案例"))

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
    first_continuation = sentinel_store.next_page(("C123", "1"))
    assert first_continuation is not None

    # A second search in the same thread must start its own continuation, not extend the first.
    _submit(app, state_values=_state_values(tags=["會員經營"]), thread_ts="1")
    resumed = sentinel_store.next_page(("C123", "1"))
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
    assert sentinel_store.next_page(("C123", "1")) is None

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
    """Everything the reopened modal would show the clicker, as one comparable structure."""
    blocks = {b.get("block_id"): b for b in view["blocks"]}
    selected = {}
    for block_id in (INTERVIEW_YEARS_BLOCK_ID, SALES_CATEGORY_LV2_BLOCK_ID, CONTENT_TAGS_BLOCK_ID):
        element = blocks[block_id]["element"]
        selected[block_id] = [o["value"] for o in element.get("initial_options", [])]
    selected[FREE_TEXT_BLOCK_ID] = blocks[FREE_TEXT_BLOCK_ID]["element"].get("initial_value", "")
    return selected


SECRET_GOAL = "U1 私人搜尋目標 competitor-churn"


def test_the_owner_can_reopen_their_own_prefilled_search(tmp_path):
    """Case A. The feature must still work for the person it belongs to."""
    app, _tmp = _run_bot_and_get_app(tmp_path)
    _client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(years=["2024"], free_text=SECRET_GOAL), user_id="U1"
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
        app, state_values=_state_values(years=["2024"], free_text=SECRET_GOAL), user_id="U1"
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
    assert "2024" not in json.dumps(
        [b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID][0]
        .get("element", {})
        .get("initial_options", []),
        ensure_ascii=False,
    )


def test_the_owner_in_a_different_channel_cannot_retrieve_the_request(tmp_path):
    """Case C. Same person, different conversation, different audience."""
    app, _tmp = _run_bot_and_get_app(tmp_path)
    _client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(years=["2024"], free_text=SECRET_GOAL), user_id="U1"
    )

    # C_OTHER is not allowlisted, so nothing opens at all -- the stronger of the two failures.
    reopened = _reopen(app, button["value"], user_id="U1", channel_id="C_OTHER")
    assert reopened.opened_views == []


def test_the_owner_in_a_different_thread_cannot_retrieve_the_request(tmp_path):
    """Case D. Same person, same channel, a thread the search was never run in."""
    app, _tmp = _run_bot_and_get_app(tmp_path)
    _client, button = _submit_and_get_adjust_button(
        app, state_values=_state_values(years=["2024"], free_text=SECRET_GOAL), user_id="U1"
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
        app, state_values=_state_values(years=["2024"], free_text=f"{secret} 的成長案例")
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

    _ack, client = _submit(app, state_values=_state_values(years=["2024"]))

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

    _ack, client = _submit(app, state_values=_state_values(years=["2024"]))

    result = client.messages[0]
    _assert_no_unfurl(result)
    # The clickable mrkdwn link survives the boundary intact: URL present, title still the label.
    assert f"<{url}|" in result["text"]


def test_restart_search_message_after_a_refusal_is_posted_without_unfurling(tmp_path):
    """E, plus the unstructured-reply branch: a refusal posts a body and a 「重新搜尋」 button."""
    secret = "SECRET_CUSTOMER_NAME"
    app, _tmp_path = _run_bot_and_get_app(tmp_path, denylist_brands=[secret])

    _ack, client = _submit(
        app, state_values=_state_values(years=["2024"], free_text=f"{secret} 案例")
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
            "state": {"values": _state_values(years=["2024"])},
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
