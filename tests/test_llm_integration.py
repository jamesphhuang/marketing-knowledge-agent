import json
from datetime import date

import pytest

from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.governance import GovernanceIndex, RestrictedCustomerRecord
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.llm import AnthropicProvider, LLMConfig, LLMPolicyError
from marketing_knowledge_agent.models import Document, DocumentMetadata, SearchFilters
from marketing_knowledge_agent.pipeline import agent_ask, ask_index


def test_policy_closed_rejects_anthropic_before_transport_is_called():
    transport = FakeTransport()
    config = _config(data_policy_confirmed=False)

    with pytest.raises(LLMPolicyError, match="公司 AI 資料政策未確認"):
        AnthropicProvider(config=config, api_key="test-only-key", transport=transport)

    assert transport.calls == 0


def test_pipeline_has_no_policy_bypass_for_injected_provider(tmp_path):
    db_path = _build_index(tmp_path, [_record("Public source", "PUBLIC_MARK", "public")])
    provider = FakeProvider("Grounded answer [1]")

    with pytest.raises(LLMPolicyError, match="公司 AI 資料政策未確認"):
        ask_index(
            "campaign",
            db_path,
            provider_name="anthropic",
            llm_config=_config(data_policy_confirmed=False),
            llm_provider=provider,
        )

    assert provider.calls == 0


def test_internal_chunks_are_removed_locally_when_second_key_is_closed(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            _record("Public source", "PUBLIC_MARK", "public"),
            _record("Internal source", "INTERNAL_MARK", "internal", can_quote=False),
        ],
    )
    provider = FakeProvider("Grounded answer [1]")

    answer = ask_index(
        "campaign",
        db_path,
        filters=SearchFilters(intent="internal"),
        limit=10,
        provider_name="anthropic",
        llm_config=_config(allow_internal_data_to_llm=False),
        llm_provider=provider,
        llm_audit_log_path=tmp_path / "audit.csv",
    )

    assert provider.calls == 1
    assert "PUBLIC_MARK" in provider.prompts[0]
    assert "INTERNAL_MARK" not in provider.prompts[0]
    assert any("1 筆內部資料未送外部模型" in warning for warning in answer.warnings)
    assert all(citation.data_classification == "public" for citation in answer.citations)


def test_ev_l1_llm_output_restricted_brand_is_redacted(tmp_path):
    restricted_brand = "Restricted Output Brand"
    db_path = _build_index(tmp_path, [_record("Public source", "PUBLIC_MARK", "public")])
    provider = FakeProvider(f"{restricted_brand} claim [1]")
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_brand)])

    answer = ask_index(
        "campaign",
        db_path,
        governance_index=governance_index,
        provider_name="anthropic",
        llm_config=_config(),
        llm_provider=provider,
        llm_audit_log_path=tmp_path / "audit.csv",
    )

    assert restricted_brand not in answer.answer
    assert "[restricted customer]" in answer.answer
    assert answer.warnings
    assert answer.governance_checked is True


def test_ev_l2_empty_retrieval_never_calls_provider(tmp_path):
    db_path = _build_index(tmp_path, [_record("Public source", "PUBLIC_MARK", "public")])
    provider = FakeProvider("must not be used")

    answer = ask_index(
        "campaign",
        db_path,
        filters=SearchFilters(topic=["missing-topic"]),
        provider_name="anthropic",
        llm_config=_config(),
        llm_provider=provider,
    )

    assert provider.calls == 0
    assert answer.citations == []
    assert "找不到符合條件" in answer.answer


def test_ev_l3_dry_run_payload_is_minimal_and_never_calls_provider(tmp_path):
    source_path = "internal/system/path.md"
    db_path = _build_index(
        tmp_path,
        [_record("Public source", "PUBLIC_MARK", "public", source_path=source_path)],
    )
    provider = FakeProvider("must not be used")

    answer = ask_index(
        "campaign",
        db_path,
        provider_name="anthropic",
        llm_config=_config(data_policy_confirmed=False),
        llm_provider=provider,
        dry_run_llm=True,
    )

    payload = json.loads(answer.answer)
    assert provider.calls == 0
    assert payload["payload_chunk_count"] == 1
    assert payload["internal_removed_count"] == 0
    assert "PUBLIC_MARK" in payload["prompt"]
    assert source_path not in payload["prompt"]
    assert "reviewer" not in payload["prompt"].lower()


def test_hallucinated_citation_label_is_replaced_and_warned(tmp_path):
    db_path = _build_index(tmp_path, [_record("Public source", "PUBLIC_MARK", "public")])
    provider = FakeProvider("Unsupported claim [99]")

    answer = ask_index(
        "campaign",
        db_path,
        provider_name="anthropic",
        llm_config=_config(),
        llm_provider=provider,
        llm_audit_log_path=tmp_path / "audit.csv",
    )

    assert "[99]" not in answer.answer
    assert "(無對應來源)" in answer.answer
    assert any("不存在的引用標籤" in warning for warning in answer.warnings)


def test_anthropic_provider_uses_injected_transport_and_configured_model():
    transport = FakeTransport(response={"content": [{"type": "text", "text": "answer [1]"}]})
    config = _config(model="configured-test-model")
    provider = AnthropicProvider(config=config, api_key="test-only-key", transport=transport)

    answer = provider.generate("prompt")

    assert answer == "answer [1]"
    assert transport.calls == 1
    assert transport.requests[0]["body"]["model"] == "configured-test-model"


def test_mock_provider_explicit_flag_matches_default_behavior(tmp_path):
    db_path = _build_index(tmp_path, [_record("Public source", "PUBLIC_MARK", "public")])

    default_answer = ask_index("campaign", db_path)
    explicit_mock_answer = ask_index("campaign", db_path, provider_name="mock")

    assert explicit_mock_answer == default_answer


def test_non_mock_audit_contains_counts_but_not_payload(tmp_path):
    db_path = _build_index(tmp_path, [_record("Public source", "PAYLOAD_SECRET_MARK", "public")])
    audit_path = tmp_path / "audit.csv"

    ask_index(
        "campaign",
        db_path,
        provider_name="anthropic",
        llm_config=_config(),
        llm_provider=FakeProvider("answer [1]"),
        llm_audit_log_path=audit_path,
    )

    audit = audit_path.read_text(encoding="utf-8")
    assert "anthropic" in audit
    assert "configured-test-model" in audit
    assert "PAYLOAD_SECRET_MARK" not in audit


def test_agentic_multi_step_uses_policy_controlled_provider(tmp_path):
    db_path = _build_index(tmp_path, [_record("Public source", "PUBLIC_MARK", "public")])
    provider = FakeProvider("Agentic grounded answer [1]")

    answer = agent_ask(
        "請整理 campaign 素材",
        db_path,
        provider_name="anthropic",
        llm_config=_config(),
        llm_provider=provider,
        llm_audit_log_path=tmp_path / "audit.csv",
    )

    assert answer.trace.mode == "agentic_lite"
    assert provider.calls == 1
    assert answer.citations


def test_cli_anthropic_is_blocked_without_policy_config(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_path = _build_index(tmp_path, [_record("Public source", "PUBLIC_MARK", "public")])

    exit_code = main(["ask", "campaign", "--db", str(db_path), "--provider", "anthropic"])
    error = capsys.readouterr().err

    assert exit_code == 2
    assert "公司 AI 資料政策未確認" in error


class FakeProvider:
    name = "anthropic"

    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.prompts = []

    def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self.response


class FakeTransport:
    def __init__(self, response=None):
        self.response = response or {"content": [{"type": "text", "text": "ok"}]}
        self.calls = 0
        self.requests = []

    def __call__(self, url, headers, body):
        self.calls += 1
        self.requests.append({"url": url, "headers": headers, "body": body})
        return self.response


def _config(
    data_policy_confirmed=True,
    allow_internal_data_to_llm=False,
    model="configured-test-model",
):
    return LLMConfig(
        provider="anthropic",
        model=model,
        data_policy_confirmed=data_policy_confirmed,
        allow_internal_data_to_llm=allow_internal_data_to_llm,
    )


def _build_index(tmp_path, records):
    documents = []
    for index, record in enumerate(records, start=1):
        content = record.pop("content")
        metadata = DocumentMetadata(
            title=record.pop("title"),
            source_type="database",
            record_type="merchant_case",
            status="published",
            publish_date=date(2026, 7, 1),
            source_sheet="Synthetic",
            source_row=index,
            **record,
        )
        documents.append(Document(id=f"doc-{index}", metadata=metadata, content=content))
    db_path = tmp_path / "index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunk_documents(documents))
    return db_path


def _record(title, marker, classification, can_quote=True, source_path=None):
    return {
        "title": title,
        "content": f"campaign content {marker}",
        "source_path": source_path or f"synthetic:{title}",
        "data_classification": classification,
        "can_quote_externally": can_quote,
    }
