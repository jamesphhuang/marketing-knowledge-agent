import csv
import json
from datetime import date

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
from marketing_knowledge_agent.models import Citation, Document, DocumentMetadata, GeneratedAnswer
from marketing_knowledge_agent.query_gating import RESTRICTED_QUERY_REFUSAL
from marketing_knowledge_agent.query_gating import append_denylist_query_audit
from marketing_knowledge_agent.slack_interface import (
    ANSWER_TRUNCATION_NOTICE,
    DENIED_CHANNEL_MESSAGE,
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

    assert client.messages == [reply]


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


class FakeSlackClient:
    def __init__(self):
        self.messages = []

    def chat_postMessage(self, **reply):
        self.messages.append(reply)


def _agentic_answer(body="answer", citations=None, warnings=None, trace_mode="fast_path"):
    generated = GeneratedAnswer(
        question="question",
        answer=body,
        citations=citations or [],
        warnings=warnings or [],
        governance_checked=True,
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
