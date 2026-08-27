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
    FACETED_SEARCH_STALE_CATALOG_MESSAGE,
    SlackConfig,
    SlackInterfaceError,
    handle_slack_event,
    load_slack_config,
    run_slack_bot,
)
from marketing_knowledge_agent.slack_pagination import SlackPaginationStore

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


def _write_slack_config(tmp_path, db_path=None, workbook_path=None, sha256=None, enable=True):
    config = {"allowed_channel_ids": ["C123"], "enable_faceted_search": enable}
    if workbook_path is not None:
        config["search_taxonomy_workbook"] = str(workbook_path)
    if sha256 is not None:
        config["search_taxonomy_sha256"] = sha256
    path = tmp_path / "slack_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
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
        restricted_customers_path=tmp_path / "absent_restricted.json",
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
            environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
            app_factory=_never_called_factory("app_factory"),
            socket_mode_handler_factory=_never_called_factory("socket_mode_handler_factory"),
        )


# --------------------------------------------------------------------------------------
# action handler: open_faceted_search_modal
# --------------------------------------------------------------------------------------


def _run_bot_and_get_app(tmp_path, db_path=None):
    db_path = db_path or _build_index(tmp_path)
    workbook_path, sha256 = _write_taxonomy(tmp_path)
    config_path = _write_slack_config(tmp_path, workbook_path=workbook_path, sha256=sha256)
    container = {}
    run_slack_bot(
        config_path=config_path,
        db_path=db_path,
        restricted_customers_path=tmp_path / "absent_restricted.json",
        audit_log_path=tmp_path / "audit.csv",
        environ={"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test"},
        app_factory=_capturing_app_factory(container),
        socket_mode_handler_factory=_capturing_socket_mode_handler_factory(container),
    )
    return container["app"], tmp_path


def test_open_modal_action_opens_the_view_for_an_allowed_channel(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.actions[OPEN_SEARCH_MODAL_ACTION_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    body = {
        "trigger_id": "T1",
        "actions": [{"value": json.dumps({"channel_id": "C123", "thread_ts": "1"})}],
    }

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
    body = {
        "trigger_id": "T1",
        "actions": [{"value": json.dumps({"channel_id": "C_NOT_ALLOWED", "thread_ts": "1"})}],
    }

    handler(ack=ack, body=body, client=client)

    assert client.opened_views == []


def test_adjust_button_prefills_the_reopened_modal(tmp_path):
    app, _tmp_path = _run_bot_and_get_app(tmp_path)
    handler = app.actions[OPEN_SEARCH_MODAL_ACTION_ID]
    ack = FakeAck()
    client = FakeSlackClient()
    body = {
        "trigger_id": "T1",
        "actions": [
            {
                "value": json.dumps(
                    {
                        "channel_id": "C123",
                        "thread_ts": "1",
                        "prefill": {
                            "interview_years": [2024],
                            "sales_category_lv2": ["食品/飲料"],
                            "content_tags": [],
                            "free_text": "會員回購",
                        },
                    }
                )
            }
        ],
    }

    handler(ack=ack, body=body, client=client)

    view = client.opened_views[0]["view"]
    year_block = next(b for b in view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID)
    selected = {opt["value"] for opt in year_block["element"]["initial_options"]}
    assert selected == {"2024"}
    free_text_block = next(b for b in view["blocks"] if b["block_id"] == FREE_TEXT_BLOCK_ID)
    assert free_text_block["element"]["initial_value"] == "會員回購"


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
        body={
            "trigger_id": "T1",
            "actions": [{"value": json.dumps({"channel_id": channel_id, "thread_ts": thread_ts})}],
        },
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
    prefill = json.loads(adjust_button["value"])["prefill"]
    assert prefill["interview_years"] == [2024]

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
