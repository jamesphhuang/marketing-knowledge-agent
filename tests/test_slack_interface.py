import csv
import json
from datetime import date
from pathlib import Path

import pytest

from marketing_knowledge_agent.agentic import (
    AgenticAnswer,
    AgentReflection,
    AgentTrace,
    QueryAnalysis,
)
from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.llm_generation import append_llm_audit
from marketing_knowledge_agent.models import (
    Citation,
    Document,
    DocumentMetadata,
    GeneratedAnswer,
    StructuredAsset,
    StructuredEntity,
    StructuredRetrievalResult,
)
from marketing_knowledge_agent import slack_output_preview
from marketing_knowledge_agent.slack_output_preview import (
    APPROVED_ASSET_URL_INPUTS,
    AUTHORITY_MANIFEST_FILENAME,
    AssetUrlOverlay,
    AssetUrlRecord,
)
from marketing_knowledge_agent.query_gating import RESTRICTED_QUERY_REFUSAL
from marketing_knowledge_agent.query_gating import append_denylist_query_audit
from marketing_knowledge_agent.slack_pagination import SlackPaginationStore, pagination_key
from marketing_knowledge_agent.slack_interface import (
    ANSWER_TRUNCATION_NOTICE,
    APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE,
    DENIED_CHANNEL_MESSAGE,
    SLACK_AUDIT_HEADER,
    SlackConfig,
    SlackInterfaceError,
    format_slack_reply,
    handle_slack_event,
    load_slack_config,
    post_slack_reply,
    run_slack_bot,
)


def test_handler_is_pure_and_returns_thread_reply_with_external_intent(tmp_path):
    calls = []

    def fake_ask(question, **kwargs):
        calls.append((question, kwargs))
        return _agentic_answer(citations=[_citation("Public source")])

    event = {"text": "<@BOT> campaign result", "channel": "C123", "user": "U123", "ts": "100.1"}
    reply = handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=fake_ask,
        audit_log_path=tmp_path / "audit.csv",
    )

    assert reply["channel"] == "C123"
    assert reply["thread_ts"] == "100.1"
    assert "Public source" in reply["text"]
    assert calls[0][0] == "campaign result"
    assert calls[0][1]["filters"].intent == "external"


@pytest.mark.parametrize(
    ("event", "allowed_channels"),
    [
        ({"text": "campaign", "channel": "C999", "user": "U1", "ts": "1"}, ["C123"]),
        (
            {"text": "campaign", "channel": "D123", "channel_type": "im", "user": "U1", "ts": "2"},
            ["D123"],
        ),
    ],
)
def test_non_allowlisted_channel_and_dm_are_denied_without_agent_call(tmp_path, event, allowed_channels):
    calls = []

    reply = handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=allowed_channels),
        ask_fn=lambda *args, **kwargs: calls.append((args, kwargs)),
        audit_log_path=tmp_path / "audit.csv",
    )

    assert reply["text"] == DENIED_CHANNEL_MESSAGE
    assert calls == []
    audit = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert "slack_denied_channel" in audit


def test_message_cannot_override_external_intent_and_internal_content_stays_out(tmp_path):
    db_path = _build_index(tmp_path)
    event = {
        "text": "<@BOT> campaign --intent internal",
        "channel": "C123",
        "user": "U123",
        "ts": "3",
    }

    reply = handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=["C123"]),
        db_path=db_path,
        restricted_customers_path=tmp_path / "restricted.json",
        audit_log_path=tmp_path / "audit.csv",
    )

    assert "INTERNAL_ONLY_MARK" not in reply["text"]
    assert "PUBLIC_MARK" in reply["text"]
    assert "可對外引用" in reply["text"]


def test_denylist_query_refuses_audits_without_query_and_sends_no_notification(tmp_path):
    restricted_query = "Restricted Query Phrase"
    restricted_path = tmp_path / "restricted.json"
    restricted_path.write_text(json.dumps([{"brand_name": restricted_query}]), encoding="utf-8")
    event = {"text": restricted_query, "channel": "C123", "user": "U123", "ts": "4"}

    reply = handle_slack_event(
        event,
        config=SlackConfig(allowed_channel_ids=["C123"], notify_owner_on_denylist=False),
        db_path=tmp_path / "unused.sqlite",
        restricted_customers_path=restricted_path,
        audit_log_path=tmp_path / "audit.csv",
    )

    assert RESTRICTED_QUERY_REFUSAL in reply["text"]
    audit = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert "denylist_query_hit" in audit
    assert restricted_query not in audit
    assert "notification" not in reply


def test_long_answer_only_truncates_body_and_preserves_citations_and_warnings():
    citations = [_citation("Source A", label="[1]"), _citation("Source B", label="[2]")]
    warnings = ["warning-one-complete", "warning-two-complete"]
    answer = _agentic_answer(body="A" * 200, citations=citations, warnings=warnings)

    text = format_slack_reply(answer, max_answer_chars=40)

    assert ANSWER_TRUNCATION_NOTICE in text
    assert "A" * 100 not in text
    assert "Source A" in text and "Source B" in text
    assert all(warning in text for warning in warnings)


def test_slack_abstention_reply_is_single_line_without_titles():
    answer = _agentic_answer(
        body="相關度不足，未產生事實性回答。最接近的內容：\n- Unrelated Brand A\n- Unrelated Brand B",
        citations=[],
    )

    text = format_slack_reply(answer, max_answer_chars=2500)

    assert text == "找不到相關內容。請換個關鍵字,或聯繫管理者確認資料是否已收錄。"
    assert "Unrelated Brand" not in text
    assert "\n" not in text


def test_slack_denylist_refusal_unchanged():
    answer = _agentic_answer(
        body=RESTRICTED_QUERY_REFUSAL,
        citations=[],
        trace_mode="refused",
    )

    text = format_slack_reply(answer, max_answer_chars=2500)

    assert text == RESTRICTED_QUERY_REFUSAL


def test_slack_unsupported_constraint_explains_condition_without_citations():
    unsupported = {
        "field": "publication_status",
        "value": "published",
        "operator": "exact",
        "hard_filter": True,
        "support_status": "unsupported",
        "reason": "asset-level publication status is not available",
    }
    structured = StructuredRetrievalResult(
        query_plan={
            "hard_filters": [unsupported],
            "unsupported_constraints": [unsupported],
            "abstain_reason": "unsupported_hard_constraint",
        },
        unsupported_constraints=[unsupported],
        execution_blocked=True,
        abstained=True,
        abstain_reason="unsupported_hard_constraint",
    )
    answer = _agentic_answer(
        body=(
            "目前資料尚不支援以下搜尋條件：\n"
            "- 上線狀態\n\n"
            "目前可使用品牌名稱、Handle、Sales Category、採訪年份、內容標籤與內容類型進行搜尋。"
        ),
        citations=[],
        structured_result=structured,
    )

    text = format_slack_reply(answer, max_answer_chars=2500)

    assert "目前資料尚不支援以下搜尋條件" in text
    assert "上線狀態" in text
    assert "📚 來源" not in text


def test_slack_body_strips_markdown_tables():
    answer = _agentic_answer(
        body=(
            "[1] # Example heading ## Content Assets | Asset | Title | "
            "| --- | --- | | Article | Example case study |"
        ),
        citations=[_citation("Public source")],
    )

    text = format_slack_reply(answer, max_answer_chars=2500)

    assert "Example heading" in text
    assert "Content Assets" in text
    assert "Article" in text
    assert "Example case study" in text
    assert "| --- |" not in text
    assert "# Example" not in text
    assert "## Content Assets" not in text
    assert not any(line.lstrip().startswith("#") for line in text.splitlines())


def test_startup_rejects_empty_channel_allowlist_before_slack_import(tmp_path):
    config_path = tmp_path / "slack.json"
    config_path.write_text(json.dumps({"allowed_channel_ids": []}), encoding="utf-8")

    with pytest.raises(SlackInterfaceError, match="allowed_channel_ids"):
        run_slack_bot(config_path=config_path, environ={})


def test_startup_rejects_missing_tokens_without_leaking_values_or_writing_log(tmp_path):
    config_path = tmp_path / "slack.json"
    config_path.write_text(json.dumps({"allowed_channel_ids": ["C123"]}), encoding="utf-8")
    audit_path = tmp_path / "audit.csv"

    with pytest.raises(SlackInterfaceError) as exc_info:
        run_slack_bot(
            config_path=config_path,
            environ={"SLACK_BOT_TOKEN": "xoxb-do-not-leak"},
            audit_log_path=audit_path,
        )

    assert "xoxb-do-not-leak" not in str(exc_info.value)
    assert not audit_path.exists()


def test_slack_dependencies_do_not_leak_into_pipeline_or_governance():
    for path in (
        "src/marketing_knowledge_agent/pipeline.py",
        "src/marketing_knowledge_agent/governance.py",
    ):
        source = open(path, encoding="utf-8").read()
        assert "slack_bolt" not in source
        assert "slack_sdk" not in source


def test_fake_client_receives_reply_dict():
    client = FakeSlackClient()
    reply = {"channel": "C123", "thread_ts": "10.1", "text": "reply"}

    post_slack_reply(client, reply)

    # The reply is forwarded unchanged apart from the two unfurl flags the boundary always adds.
    assert client.messages == [{**reply, "unfurl_links": False, "unfurl_media": False}]


def test_slack_audit_preserves_existing_sync_audit_schema(tmp_path):
    audit_path = tmp_path / "audit.csv"
    audit_path.write_text(
        "timestamp,batch_id,action,add,update,archive,operator,plan_path\n",
        encoding="utf-8",
    )

    handle_slack_event(
        {"text": "campaign", "channel": "C123", "user": "U123", "ts": "5"},
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=lambda *args, **kwargs: _agentic_answer(citations=[_citation("Public source")]),
        audit_log_path=audit_path,
    )

    with audit_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert all(len(row) == len(rows[0]) for row in rows)
    assert rows[-1][2] == "slack_qa"
    assert "campaign" in rows[-1]


def test_llm_audit_can_append_to_slack_audit_schema(tmp_path):
    audit_path = tmp_path / "audit.csv"
    audit_path.write_text(
        "timestamp,event,channel_id,user_id,citation_count,warning_count,query\n",
        encoding="utf-8",
    )

    append_llm_audit(audit_path, "agent-ask", "anthropic", "test-model", 2, 1)

    with audit_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert all(len(row) == len(rows[0]) for row in rows)
    assert rows[-1][1] == "llm_call"


def test_denylist_audit_can_append_to_llm_audit_schema_without_query(tmp_path):
    audit_path = tmp_path / "audit.csv"
    audit_path.write_text(
        "timestamp,command,provider,model,payload_chunk_count,internal_removed_count\n",
        encoding="utf-8",
    )

    append_denylist_query_audit(
        audit_path,
        command="agent-ask",
        match_count=1,
        audit_metadata={"channel_id": "C123", "user_id": "U123"},
        warning_count=1,
    )

    with audit_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert all(len(row) == len(rows[0]) for row in rows)
    assert "denylist_query_hit" in rows[-1]


def test_slack_bot_cli_is_registered_and_uses_runner(tmp_path, monkeypatch):
    calls = []
    config_path = tmp_path / "slack.json"
    monkeypatch.setattr("marketing_knowledge_agent.cli.run_slack_bot", lambda **kwargs: calls.append(kwargs))

    exit_code = main(["slack-bot", "--config", str(config_path)])

    assert exit_code == 0
    assert calls == [{"config_path": config_path}]


def test_load_slack_config_uses_documented_defaults(tmp_path):
    path = tmp_path / "slack.json"
    path.write_text(json.dumps({"allowed_channel_ids": ["C123"]}), encoding="utf-8")

    config = load_slack_config(path)

    assert config.allowed_channel_ids == ["C123"]
    assert config.notify_owner_on_denylist is False
    assert config.max_answer_chars == 2500
    assert config.enable_approved_asset_urls is False


def test_opted_in_slack_reply_uses_approved_asset_links_only(monkeypatch, tmp_path):
    assets = [
        StructuredAsset(
            asset_type="article",
            title="Article",
            external_usage_status="可對外引用",
            source_record_id="Sheet:r8",
            source_sheet="Sheet",
            source_row=8,
            citation_label="[1]",
        ),
        StructuredAsset(
            asset_type="video",
            title="Video",
            external_usage_status="可對外引用",
            source_record_id="Sheet:r8",
            source_sheet="Sheet",
            source_row=8,
            citation_label="[2]",
        ),
    ]
    citations = [
        _citation("Article", "[1]"),
        _citation("Video", "[2]"),
    ]
    for citation, asset_type in zip(citations, ("article", "video")):
        citation.source_sheet = "Sheet"
        citation.source_row = 8
        citation.record_type = "merchant_case"
        citation.chunk_id = f"chunk-r8:{asset_type}"
    structured = StructuredRetrievalResult(
        query_plan={"raw_query": "Merchant A", "supported_constraints": []},
        matched_entities=[StructuredEntity(entity_type="merchant", entity_name="Merchant A", assets=assets)],
        total_entities=1,
        total_assets=2,
    )
    answer = GeneratedAnswer(
        question="Merchant A",
        answer="unused",
        citations=citations,
        warnings=[],
        governance_checked=True,
        structured_result=structured,
    )
    record_id = "Sheet:r8"
    overlay = AssetUrlOverlay(
        values={
            (record_id, f"{record_id}:article", "canonical_url"): AssetUrlRecord(
                record_id, f"{record_id}:article", "canonical_url", "https://example.com/article", "Reviewer", "2026-07-17"
            ),
            (record_id, f"{record_id}:video", "asset_url"): AssetUrlRecord(
                record_id, f"{record_id}:video", "asset_url", "https://example.com/video", "Reviewer", "2026-07-17"
            ),
        }
    )
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface.load_index_bound_approved_asset_url_overlay",
        lambda _db_path: overlay,
    )
    audit_path = tmp_path / "audit.csv"

    reply = handle_slack_event(
        {"text": "Merchant A", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=audit_path,
    )

    # OLD: "連結：<url|開啟連結>".  NEW: the same approved url, on that asset's own title.
    assert "> <https://example.com/article|Article>" in reply["text"].splitlines()
    assert "> <https://example.com/video|Video>" in reply["text"].splitlines()
    assert [asset.url for asset in assets] == ["https://example.com/article", "https://example.com/video"]
    assert [citation.canonical_url for citation in citations] == ["https://example.com/article", "https://example.com/video"]
    assert [row[1] for row in _audit_rows(audit_path)] == ["slack_qa"]


def test_overlay_authority_failure_fails_closed_without_aborting_the_slack_query(monkeypatch, tmp_path):
    """F-03: feature ON with unavailable authority must not raise past the Slack response path."""
    assets, citations, answer = _structured_answer()
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_output_preview._authority_root",
        lambda: tmp_path / "no-such-repository-root",
    )
    audit_path = tmp_path / "audit.csv"

    reply = handle_slack_event(
        {"text": "Merchant A", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=audit_path,
    )

    assert "Merchant A" in reply["text"]
    assert ["> Article", "> Video"] == [
        line for line in reply["text"].splitlines() if line in ("> Article", "> Video")
    ]
    assert "<http" not in reply["text"]
    assert [asset.url for asset in assets] == [None, None]
    assert [citation.canonical_url for citation in citations] == [None, None]


@pytest.mark.parametrize("damage", ["missing_artifacts", "missing_manifest", "hash_mismatch", "malformed_artifact"])
def test_unavailable_overlay_authority_still_runs_the_normal_slack_audit(monkeypatch, tmp_path, damage):
    _assets, _citations, answer = _structured_answer()
    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_output_preview._authority_root",
        lambda: _damaged_authority_root(tmp_path, damage),
    )
    audit_path = tmp_path / "audit.csv"

    reply = handle_slack_event(
        {"text": "Merchant A", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=audit_path,
    )

    rows = _audit_rows(audit_path)
    assert [row[1] for row in rows] == [APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE, "slack_qa"]
    assert all(len(row) == len(SLACK_AUDIT_HEADER) for row in rows)
    assert rows[0][-1] == ""
    assert "<http" not in reply["text"]


@pytest.mark.parametrize("damage", ["missing_artifacts", "missing_manifest", "hash_mismatch", "malformed_artifact"])
def test_overlay_failure_never_leaks_paths_hashes_or_internals_to_slack(monkeypatch, tmp_path, damage):
    _assets, _citations, answer = _structured_answer()
    root = _damaged_authority_root(tmp_path, damage)
    monkeypatch.setattr("marketing_knowledge_agent.slack_output_preview._authority_root", lambda: root)

    reply = handle_slack_event(
        {"text": "Merchant A", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"], enable_approved_asset_urls=True),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=tmp_path / "audit.csv",
    )

    text = reply["text"]
    for leak in (str(root), "sha256", "manifest", "asset_apply_preview", "Traceback", "attacker.example"):
        assert leak not in text


def test_feature_off_never_touches_the_approved_url_authority(monkeypatch, tmp_path):
    _assets, _citations, answer = _structured_answer()

    def fail(*args, **kwargs):
        raise AssertionError("approved URL authority must not be consulted while the flag is OFF")

    monkeypatch.setattr(
        "marketing_knowledge_agent.slack_interface.load_index_bound_approved_asset_url_overlay", fail
    )
    audit_path = tmp_path / "audit.csv"

    reply = handle_slack_event(
        {"text": "Merchant A", "channel": "C123", "user": "U123", "ts": "10"},
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=lambda *args, **kwargs: answer,
        audit_log_path=audit_path,
    )

    assert ["> Article", "> Video"] == [
        line for line in reply["text"].splitlines() if line in ("> Article", "> Video")
    ]
    assert "<http" not in reply["text"]
    assert [row[1] for row in _audit_rows(audit_path)] == ["slack_qa"]


@pytest.mark.parametrize("value", ["true", "false", "1", "0", 1, 0, "yes", "on", [], {}, None])
def test_non_boolean_feature_flag_is_rejected_instead_of_coerced(tmp_path, value):
    """Mutation M4: replacing the strict isinstance check with bool(value) must be caught."""
    path = tmp_path / "slack.json"
    path.write_text(
        json.dumps({"allowed_channel_ids": ["C123"], "enable_approved_asset_urls": value}),
        encoding="utf-8",
    )

    with pytest.raises(SlackInterfaceError, match="enable_approved_asset_urls"):
        load_slack_config(path)


@pytest.mark.parametrize(("payload", "expected"), [({}, False), ({"enable_approved_asset_urls": False}, False), ({"enable_approved_asset_urls": True}, True)])
def test_feature_flag_defaults_off_and_only_real_booleans_switch_it(tmp_path, payload, expected):
    path = tmp_path / "slack.json"
    path.write_text(json.dumps(dict({"allowed_channel_ids": ["C123"]}, **payload)), encoding="utf-8")

    assert load_slack_config(path).enable_approved_asset_urls is expected


def _structured_answer():
    assets = [
        StructuredAsset(
            asset_type=asset_type,
            title=title,
            external_usage_status="可對外引用",
            source_record_id="Sheet:r8",
            source_sheet="Sheet",
            source_row=8,
            citation_label=label,
        )
        for asset_type, title, label in (("article", "Article", "[1]"), ("video", "Video", "[2]"))
    ]
    citations = [_citation("Article", "[1]"), _citation("Video", "[2]")]
    for citation, asset_type in zip(citations, ("article", "video")):
        citation.source_sheet = "Sheet"
        citation.source_row = 8
        citation.record_type = "merchant_case"
        citation.chunk_id = f"chunk-r8:{asset_type}"
    structured = StructuredRetrievalResult(
        query_plan={"raw_query": "Merchant A", "supported_constraints": []},
        matched_entities=[StructuredEntity(entity_type="merchant", entity_name="Merchant A", assets=assets)],
        total_entities=1,
        total_assets=2,
    )
    answer = GeneratedAnswer(
        question="Merchant A",
        answer="unused",
        citations=citations,
        warnings=[],
        governance_checked=True,
        structured_result=structured,
    )
    return assets, citations, answer


def _damaged_authority_root(tmp_path, damage):
    """Build an authority directory whose approved URL authority is unusable in one specific way."""
    root = tmp_path / f"root-{damage}"
    manifest_source = (
        Path(slack_output_preview.__file__).resolve().parent
        / slack_output_preview.AUTHORITY_PACKAGE_RELATIVE_DIR
        / AUTHORITY_MANIFEST_FILENAME
    )
    manifest_path = root / AUTHORITY_MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(manifest_source.read_bytes())
    if damage == "missing_manifest":
        manifest_path.unlink()
        return root
    if damage == "missing_artifacts":
        return root
    for relative in APPROVED_ASSET_URL_INPUTS:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "asset_identity,field,url\n" if damage == "malformed_artifact" else "tampered",
            encoding="utf-8",
        )
    return root


def _audit_rows(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == SLACK_AUDIT_HEADER
    return rows[1:]


class FakeSlackClient:
    def __init__(self):
        self.messages = []

    def chat_postMessage(self, **reply):
        self.messages.append(reply)


def _agentic_answer(
    body="answer",
    citations=None,
    warnings=None,
    trace_mode="fast_path",
    structured_result=None,
):
    generated = GeneratedAnswer(
        question="question",
        answer=body,
        citations=citations or [],
        warnings=warnings or [],
        governance_checked=True,
        structured_result=structured_result,
    )
    return AgenticAnswer(
        generated=generated,
        trace=AgentTrace(
            mode=trace_mode,
            analysis=QueryAnalysis(question_type="simple_lookup", needs_agent=False, reasons=[]),
            plan=[],
            observations=[],
            reflection=AgentReflection(sufficient=bool(generated.citations), notes=[]),
        ),
    )


def _citation(title, label="[1]"):
    return Citation(
        label=label,
        title=title,
        source_path=f"synthetic:{title}",
        chunk_id=f"chunk-{title}",
        status="published",
        source_type="database",
        record_type="public_metric",
        data_classification="public",
        can_quote_externally=True,
        publish_date="2026-07-01",
        source_sheet="Synthetic",
        source_row=1,
        freshness_note="最新日期 2026-07-01",
    )


def _build_index(tmp_path):
    documents = []
    for index, (title, content, classification, can_quote) in enumerate(
        [
            ("Public source", "campaign PUBLIC_MARK", "public", True),
            ("Internal source", "campaign INTERNAL_ONLY_MARK", "internal", False),
        ],
        start=1,
    ):
        metadata = DocumentMetadata(
            title=title,
            source_type="database",
            record_type="merchant_case",
            status="published",
            publish_date=date(2026, 7, 1),
            source_path=f"synthetic:{index}",
            source_sheet="Synthetic",
            source_row=index,
            data_classification=classification,
            can_quote_externally=can_quote,
        )
        documents.append(Document(id=f"doc-{index}", metadata=metadata, content=content))
    db_path = tmp_path / "index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return db_path


# --- Slack link/media unfurl suppression -------------------------------------------------------
#
# Human UAT found search threads unreadable: every clickable approved asset title made Slack expand
# a preview card (article summary, "Written by" metadata, a full-width image, a YouTube thumbnail),
# and one search posts several assets. The remedy is a single posting boundary that forces
# unfurling off for every message this bot sends, without touching the URLs themselves.


def _posted(client):
    assert client.messages, "nothing was posted"
    return client.messages[-1]


def test_post_slack_reply_disables_link_and_media_unfurling():
    client = FakeSlackClient()

    post_slack_reply(client, {"channel": "C123", "thread_ts": "10.1", "text": "reply"})

    sent = _posted(client)
    assert sent["unfurl_links"] is False
    assert sent["unfurl_media"] is False


def test_a_call_site_cannot_re_enable_unfurling_through_the_reply_dict():
    """The flags are written after the reply is unpacked, so no caller can turn previews back on."""
    client = FakeSlackClient()

    post_slack_reply(
        client,
        {
            "channel": "C123",
            "thread_ts": "10.1",
            "text": "reply",
            "unfurl_links": True,
            "unfurl_media": True,
        },
    )

    sent = _posted(client)
    assert sent["unfurl_links"] is False
    assert sent["unfurl_media"] is False


def test_the_boundary_leaves_a_clickable_approved_asset_link_untouched():
    """Suppressing the preview must not touch the link: the title stays clickable, the URL stays."""
    client = FakeSlackClient()
    text = "> • *文章 [1]*\n> <https://shopline.tw/blog/case|傳統製麵廠的數位轉型之路！>"

    post_slack_reply(client, {"channel": "C123", "thread_ts": "10.1", "text": text})

    sent = _posted(client)
    assert sent["text"] == text
    assert "https://shopline.tw/blog/case" in sent["text"]
    assert "<https://shopline.tw/blog/case|傳統製麵廠的數位轉型之路！>" in sent["text"]
    assert sent["unfurl_links"] is False and sent["unfurl_media"] is False


def test_natural_language_reply_is_posted_with_unfurling_disabled(tmp_path):
    """A: the app_mention path -- the reply handle_slack_event built, posted through the boundary."""
    client = FakeSlackClient()

    reply = handle_slack_event(
        {"text": "<@BOT> campaign result", "channel": "C123", "user": "U1", "ts": "100.1"},
        config=SlackConfig(allowed_channel_ids=["C123"]),
        ask_fn=lambda question, **kwargs: _agentic_answer(citations=[_citation("Public source")]),
        audit_log_path=tmp_path / "audit.csv",
    )
    post_slack_reply(client, reply)

    sent = _posted(client)
    assert "Public source" in sent["text"]
    assert sent["unfurl_links"] is False and sent["unfurl_media"] is False


def test_pagination_continuation_is_posted_with_unfurling_disabled(tmp_path):
    """C: 「顯示更多」 replays a stored page, which must be posted through the same boundary."""
    store = SlackPaginationStore()
    store.start(pagination_key("C123", "100.1"), ["page one", "page two"])
    client = FakeSlackClient()

    reply = handle_slack_event(
        {"text": "<@BOT> 顯示更多", "channel": "C123", "user": "U1", "ts": "100.1"},
        config=SlackConfig(allowed_channel_ids=["C123"]),
        audit_log_path=tmp_path / "audit.csv",
        pagination_store=store,
    )
    post_slack_reply(client, reply)

    sent = _posted(client)
    assert sent["text"] == "page two"
    assert sent["unfurl_links"] is False and sent["unfurl_media"] is False


def test_no_slack_message_is_posted_outside_the_boundary():
    """The guarantee is centralization: one ``chat_postMessage`` call, inside ``post_slack_reply``.

    A second call site anywhere would post with Slack's default unfurling and reopen the finding,
    so this is asserted over the source rather than left to each new handler to remember.
    """
    source = Path("src/marketing_knowledge_agent/slack_interface.py").read_text(encoding="utf-8")
    assert source.count("chat_postMessage") == 1
    boundary = source.split("def post_slack_reply(", 1)[1].split("\ndef ", 1)[0]
    assert "chat_postMessage" in boundary

    for module in Path("src/marketing_knowledge_agent").glob("*.py"):
        if module.name == "slack_interface.py":
            continue
        assert "chat_postMessage" not in module.read_text(encoding="utf-8"), module.name
