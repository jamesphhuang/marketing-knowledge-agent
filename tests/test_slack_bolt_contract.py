"""Contract tests against the *real* slack_bolt dispatcher, offline.

Every other Slack test in this WP drives a hand-built fake ``App``, which proves our handlers do
the right thing but proves nothing about whether ``slack_bolt`` will ever call them. That gap
matters more than it looks:

``build_required_kwargs`` does **not** raise when a listener declares an argument bolt cannot
inject. It logs ``"<name> is not a valid argument"`` and omits the kwarg, so the failure surfaces
as a ``TypeError`` at the first real button click -- in UAT, in front of a user, not in CI. A
renamed or mistyped handler parameter is therefore invisible to every fake-``App`` test we have.

So these tests register the real handlers on a real ``slack_bolt.App`` and push synthetic
``block_actions`` / ``view_submission`` payloads through bolt's own dispatcher. They stay hermetic
by stubbing ``WebClient.api_call`` -- the single funnel every Slack API method goes through. It is
patched at class level rather than on an instance because bolt clones a fresh ``WebClient`` per
request (since 1.15), so an instance stub would simply be bypassed; ``monkeypatch`` restores it.
Any Slack API call this WP does not expect raises rather than escaping to the network.

What these tests still do NOT prove: that Slack itself accepts these payloads. The Block Kit views
are validated against ``slack_sdk``'s own view model, which is the closest offline proxy available,
but the live ``views_open`` / ``view_submission`` round trip is only exercised in UAT.
"""

import inspect
import json
from datetime import date

import pytest
from slack_bolt import App
from slack_bolt.request import BoltRequest
from slack_sdk.models.views import View
from slack_sdk.web.client import WebClient
from slack_sdk.web.slack_response import SlackResponse

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import Document, DocumentMetadata
from marketing_knowledge_agent.search_facets import build_facet_catalog
from marketing_knowledge_agent.search_taxonomy import load_search_taxonomy
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
    SlackConfig,
    _register_faceted_search_handlers,
)
from marketing_knowledge_agent.slack_pagination import SlackPaginationStore
from marketing_knowledge_agent.slack_request_tokens import SlackRequestTokenStore

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


def _metadata(brand, lv2, tags, year, row):
    return DocumentMetadata(
        title=brand,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 1, 1),
        source_path=f"商家夥伴案例資料庫:{row}",
        source_sheet="商家夥伴案例資料庫",
        source_row=row,
        brand_name=brand,
        merchant_handle=f"handle{row}",
        merchant_status="現有商家",
        interview_year=year,
        sales_category_lv2=lv2,
        content_tags=list(tags),
        article_title=brand,
        data_classification="public",
        can_quote_externally=True,
    )


class _Recorder:
    def __init__(self):
        self.views_open = []
        self.post_message = []


@pytest.fixture
def slack_api(monkeypatch):
    """Intercept every Slack API call at the WebClient funnel; nothing reaches the network."""
    recorder = _Recorder()

    def _response(client, api_method, body):
        # A real SlackResponse: bolt's authorization middleware reads ``.headers`` off it.
        return SlackResponse(
            client=client,
            http_verb="POST",
            api_url=f"https://slack.com/api/{api_method}",
            req_args={},
            data=body,
            headers={"x-oauth-scopes": "chat:write,commands"},
            status_code=200,
        )

    def _stub(self, api_method, *, http_verb="POST", files=None, data=None, params=None,
              json=None, headers=None, auth=None):
        payload = json or data or {}
        if api_method == "auth.test":
            # bolt's single_team_authorization middleware calls this before any listener runs.
            return _response(self, api_method, {
                "ok": True, "url": "https://example.slack.com/", "team": "T", "user": "bot",
                "team_id": "T1", "user_id": "UBOT", "bot_id": "BBOT",
            })
        if api_method == "views.open":
            recorder.views_open.append(payload)
        elif api_method == "chat.postMessage":
            recorder.post_message.append(payload)
        else:
            # An unexpected call is a contract change, not something to swallow.
            raise AssertionError(f"unexpected Slack API call: {api_method}")
        return _response(self, api_method, {"ok": True})

    monkeypatch.setattr(WebClient, "api_call", _stub)
    return recorder


@pytest.fixture
def bolt_app(tmp_path, slack_api):
    documents = [
        Document(id=f"doc-{i}", metadata=_metadata(b, lv2, t, y, i), content=b)
        for i, (b, lv2, t, y) in enumerate(
            [("莉朵花藝", "居家生活相關", ["會員經營"], 2025),
             ("大春煉皂", "食品/飲料", ["會員經營"], 2024)],
            start=1,
        )
    ]
    db_path = tmp_path / "content_index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))

    workbook = tmp_path / "taxonomy.xlsx"
    sha256 = write_taxonomy_workbook(
        workbook, sales_rows=SALES_CATEGORY_ROWS, tag_rows=CONTENT_TAG_ROWS
    )
    taxonomy = load_search_taxonomy(workbook_path=workbook, expected_sha256=sha256)

    denylist = tmp_path / "restricted_customers.json"
    denylist.write_text(json.dumps([]), encoding="utf-8")
    catalog = build_facet_catalog(db_path, taxonomy, restricted_customers_path=denylist)

    app = App(
        token="xoxb-fake-not-a-real-token",
        signing_secret="fake-signing-secret",
        token_verification_enabled=False,
        request_verification_enabled=False,
        ssl_check_enabled=False,
        url_verification_enabled=False,
    )
    _register_faceted_search_handlers(
        app,
        config=SlackConfig(allowed_channel_ids=["C123"], enable_faceted_search=True),
        taxonomy=taxonomy,
        facet_catalog=catalog,
        db_path=db_path,
        restricted_customers_path=denylist,
        audit_log_path=tmp_path / "audit.csv",
        pagination_store=SlackPaginationStore(),
        request_token_store=SlackRequestTokenStore(),
    )
    return app


def _dispatch(app, body):
    # Socket Mode delivers the already-parsed interactivity payload, so the dict is the body.
    return app.dispatch(BoltRequest(body=body, mode="socket_mode"))


def _action_payload(*, user_id="U1", channel_id="C123", thread_ts="100.1", value=None):
    """A block_actions payload shaped the way Slack actually sends one.

    ``container`` and ``channel`` are what the handler reads its interaction context from; the
    button's ``value`` carries only the action's own data.
    """
    return {
        "type": "block_actions",
        "trigger_id": "T-1",
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
            "block_id": "open_faceted_search_actions",
            "value": value if value is not None else json.dumps({}),
        }],
    }


def _open_modal(app, slack_api, channel_id="C123", thread_ts="100.1", **kwargs):
    response = _dispatch(
        app, _action_payload(channel_id=channel_id, thread_ts=thread_ts, **kwargs)
    )
    return response, slack_api.views_open[-1]["view"] if slack_api.views_open else None


def _submission_body(view_payload, *, years=(), lv2=(), tags=(), free_text=None):
    def _options(values):
        return {"selected_options": [{"value": value} for value in values]}

    return {
        "type": "view_submission",
        "user": {"id": "U1"},
        "view": {
            # A real payload always carries this, and bolt reads it while matching listeners.
            "type": "modal",
            "callback_id": FACETED_SEARCH_MODAL_CALLBACK_ID,
            "private_metadata": view_payload["private_metadata"],
            "state": {"values": {
                INTERVIEW_YEARS_BLOCK_ID: {INTERVIEW_YEARS_ACTION_ID: _options(years)},
                SALES_CATEGORY_LV2_BLOCK_ID: {SALES_CATEGORY_LV2_ACTION_ID: _options(lv2)},
                CONTENT_TAGS_BLOCK_ID: {CONTENT_TAGS_ACTION_ID: _options(tags)},
                FREE_TEXT_BLOCK_ID: {FREE_TEXT_ACTION_ID: {"value": free_text}},
            }},
        },
    }


def test_every_handler_argument_name_is_injectable_by_bolt():
    """Guards the failure mode bolt reports only as a log line.

    ``build_required_kwargs`` warns and omits an argument it cannot inject rather than raising, so
    a renamed parameter would pass every fake-``App`` test and then ``TypeError`` on the first real
    interaction. This compares our declared parameters against bolt's own injectable set.
    """
    from slack_bolt.kwargs_injection import build_required_kwargs

    source = inspect.getsource(build_required_kwargs)
    start = source.index("all_available_args: Dict[str, Any] = {")
    block = source[start:source.index("}", start)]
    injectable = {line.split('"')[1] for line in block.splitlines() if line.strip().startswith('"')}
    assert "ack" in injectable and "view" in injectable, "failed to parse bolt's injectable args"

    registered = {}

    class _ProbeApp:
        def event(self, name):
            return lambda fn: registered.setdefault(("event", name), fn) or fn

        def action(self, name):
            return lambda fn: registered.setdefault(("action", name), fn) or fn

        def view(self, name):
            return lambda fn: registered.setdefault(("view", name), fn) or fn

    _register_faceted_search_handlers(
        _ProbeApp(),
        config=SlackConfig(allowed_channel_ids=["C1"]),
        taxonomy=None,
        facet_catalog=None,
        db_path="unused",
        restricted_customers_path="unused",
        audit_log_path="unused",
        pagination_store=None,
        request_token_store=None,
    )
    assert registered, "no handlers registered"
    for (kind, name), handler in registered.items():
        declared = inspect.getfullargspec(handler).args
        unsupported = [arg for arg in declared if arg not in injectable]
        assert not unsupported, f"{kind} {name} declares un-injectable args {unsupported}"


def test_real_bolt_routes_a_button_click_to_views_open(bolt_app, slack_api):
    response, view_payload = _open_modal(bolt_app, slack_api)

    assert response.status == 200
    assert len(slack_api.views_open) == 1
    assert view_payload["callback_id"] == FACETED_SEARCH_MODAL_CALLBACK_ID


def test_the_modal_is_accepted_by_slack_sdks_own_view_model(bolt_app, slack_api):
    """The closest offline proxy for "Slack would accept this Block Kit payload"."""
    _response, view_payload = _open_modal(bolt_app, slack_api)

    View(**view_payload).validate_json()  # raises if the view is malformed
    assert "LV1" not in json.dumps(view_payload, ensure_ascii=False)


def test_real_bolt_routes_a_view_submission_to_a_governed_search(bolt_app, slack_api):
    _response, view_payload = _open_modal(bolt_app, slack_api)
    slack_api.post_message.clear()

    response = _dispatch(bolt_app, _submission_body(view_payload, years=["2024"]))

    assert response.status == 200
    # One result message plus the "調整條件" follow-up, and never a second search.
    assert len(slack_api.post_message) == 2
    assert "大春煉皂" in slack_api.post_message[0]["text"]
    button = slack_api.post_message[-1]["blocks"][-1]["elements"][0]
    assert "request_token" in json.loads(button["value"])


def test_real_bolt_returns_the_errors_response_action_for_an_empty_submission(bolt_app, slack_api):
    """``ack(response_action="errors")`` has to survive bolt's own response serialization."""
    _response, view_payload = _open_modal(bolt_app, slack_api)
    slack_api.post_message.clear()

    response = _dispatch(bolt_app, _submission_body(view_payload))

    body = json.loads(response.body)
    assert body["response_action"] == "errors"
    assert FREE_TEXT_BLOCK_ID in body["errors"]
    assert slack_api.post_message == []


def test_real_bolt_routes_distinct_user_contexts_to_the_right_prefill(bolt_app, slack_api):
    """Case G: the user identity the handler acts on comes from bolt's own routed payload.

    Both clicks carry the *same* button value -- that is the point, since the button is posted once
    into a shared channel. Only ``body.user.id`` differs, and it is bolt that populates it, so this
    exercises the real path rather than a hand-built kwarg.
    """
    _response, view_payload = _open_modal(bolt_app, slack_api)
    slack_api.post_message.clear()

    secret = "U1 私人搜尋 competitor-churn"
    _dispatch(bolt_app, _submission_body(view_payload, years=["2024"], free_text=secret))
    adjust_button = slack_api.post_message[-1]["blocks"][-1]["elements"][0]
    assert "request_token" in json.loads(adjust_button["value"])

    # The owner clicks: prefill comes back.
    slack_api.views_open.clear()
    _dispatch(bolt_app, _action_payload(user_id="U1", value=adjust_button["value"]))
    owner_view = slack_api.views_open[-1]["view"]
    free_text_block = next(
        b for b in owner_view["blocks"] if b.get("block_id") == FREE_TEXT_BLOCK_ID
    )
    assert free_text_block["element"].get("initial_value") == secret

    # A different member of the same channel clicks the same button: nothing of U1's search.
    slack_api.views_open.clear()
    _dispatch(bolt_app, _action_payload(user_id="U2", value=adjust_button["value"]))
    other_view = slack_api.views_open[-1]["view"]
    assert secret not in json.dumps(other_view, ensure_ascii=False)
    other_free_text = next(
        b for b in other_view["blocks"] if b.get("block_id") == FREE_TEXT_BLOCK_ID
    )
    assert "initial_value" not in other_free_text["element"]
    years_block = next(
        b for b in other_view["blocks"] if b.get("block_id") == INTERVIEW_YEARS_BLOCK_ID
    )
    assert "initial_options" not in years_block["element"]


def test_real_slack_sdk_serialization_carries_the_unfurl_suppression(bolt_app, slack_api):
    """The unfurl flags must survive slack_sdk's own ``chat_postMessage`` serialization.

    Every other unfurl test asserts on a fake client's kwargs, which proves the boundary sets the
    flags and proves nothing about what leaves ``WebClient``. This one reads the payload
    ``slack_sdk`` would actually have put on the wire, for both messages a search posts.
    """
    _response, view_payload = _open_modal(bolt_app, slack_api)
    slack_api.post_message.clear()

    _dispatch(bolt_app, _submission_body(view_payload, years=["2024"]))

    assert len(slack_api.post_message) == 2
    for payload in slack_api.post_message:
        assert payload["unfurl_links"] is False
        assert payload["unfurl_media"] is False
