import json
import re
from datetime import date

import marketing_knowledge_agent.cli as cli_module
import marketing_knowledge_agent.pipeline as pipeline_module
from marketing_knowledge_agent.agentic import analyze_question, build_plan
from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.generation import generate_answer
from marketing_knowledge_agent.governance import GovernanceIndex, RestrictedCustomerRecord
from marketing_knowledge_agent.indexing import SQLiteIndex
from marketing_knowledge_agent.models import (
    Chunk,
    Citation,
    Document,
    DocumentMetadata,
    GeneratedAnswer,
    SearchFilters,
    SearchResult,
)
from marketing_knowledge_agent.pipeline import agent_ask, ask_index, search_index
from marketing_knowledge_agent.query_gating import (
    EXTERNAL_CITATION_REMOVAL_WARNING,
    MIN_RELEVANCE_SCORE,
    RESTRICTED_QUERY_REFUSAL,
    apply_intent_gating,
    enforce_external_citations,
)


def test_search_filters_intent_normalizes_and_round_trips_through_agent_plan():
    filters = SearchFilters(intent="External", product=["product-a"])

    round_tripped = SearchFilters(**filters.as_dict())
    plan = build_plan("整理 product-a 素材", analyze_question("整理 product-a 素材"), filters, 3)

    assert filters.intent == "external"
    assert round_tripped == filters
    assert all(step.filters["intent"] == "external" for step in plan if step.tool_name == "search")


def test_external_gating_forces_quote_status_and_classification():
    gated = apply_intent_gating(
        SearchFilters(
            intent="external",
            can_quote_externally=False,
            status=["published", "archived"],
            data_classification=["public", "internal"],
        )
    )

    assert gated.can_quote_externally is True
    assert gated.status == ["published"]
    assert gated.data_classification == ["public"]


def test_external_gating_excludes_pending_metric_and_empty_intersections(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            _payload("Pending", "pending_metric", can_quote_externally=False, data_classification="internal"),
            _payload("Public", "public_metric", allowed_exposure_channels=["press_release"]),
        ],
    )

    pending_results = search_index(
        "campaign",
        db_path,
        filters=SearchFilters(intent="external", record_type=["pending_metric"]),
        limit=10,
    )
    archived_results = search_index(
        "campaign",
        db_path,
        filters=SearchFilters(intent="external", status=["archived"]),
        limit=10,
    )

    assert pending_results == []
    assert archived_results == []


def test_internal_intent_preserves_existing_result_count(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            _payload("Public", "public_metric", allowed_exposure_channels=["press_release"]),
            _payload("Pending", "pending_metric", can_quote_externally=False, data_classification="internal"),
            _payload("Internal case", "merchant_case", can_quote_externally=False, data_classification="internal"),
        ],
    )

    default_results = search_index("campaign", db_path, limit=10)
    internal_results = search_index("campaign", db_path, filters=SearchFilters(intent="internal"), limit=10)

    assert len(default_results) == 3
    assert [result.chunk.id for result in internal_results] == [result.chunk.id for result in default_results]


def test_external_exit_assertion_removes_invalid_citation_and_warns():
    answer = GeneratedAnswer(
        question="external copy",
        answer="draft",
        citations=[
            _citation("safe", "public_metric", True),
            _citation("unsafe", "pending_metric", False),
        ],
    )

    enforce_external_citations(answer, SearchFilters(intent="external"))

    assert [citation.title for citation in answer.citations] == ["safe"]
    assert EXTERNAL_CITATION_REMOVAL_WARNING.format(count=1) in answer.warnings


def test_ask_precheck_refuses_before_search_and_audits_without_query(tmp_path, monkeypatch):
    restricted_query = "Restricted Brand Direct"
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_query)])
    audit_path = tmp_path / "audit.csv"

    def fail_search(*args, **kwargs):
        raise AssertionError("search must not run")

    monkeypatch.setattr(pipeline_module, "search_index", fail_search)
    answer = ask_index(
        restricted_query,
        tmp_path / "unused.sqlite",
        governance_index=governance_index,
        audit_log_path=audit_path,
    )

    assert answer.answer == RESTRICTED_QUERY_REFUSAL
    assert answer.citations == []
    assert answer.governance_checked is True
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "denylist_query_hit" in audit_text
    assert restricted_query not in audit_text


def test_agent_ask_precheck_refuses_before_agent_execution(tmp_path, monkeypatch):
    restricted_query = "Restricted Brand Agent"
    governance_index = GovernanceIndex([RestrictedCustomerRecord(brand_name=restricted_query)])

    def fail_agent(*args, **kwargs):
        raise AssertionError("agent must not run")

    monkeypatch.setattr(pipeline_module, "agentic_ask", fail_agent)
    answer = agent_ask(
        restricted_query,
        tmp_path / "unused.sqlite",
        governance_index=governance_index,
        audit_log_path=tmp_path / "audit.csv",
    )

    assert answer.answer == RESTRICTED_QUERY_REFUSAL
    assert answer.citations == []
    assert answer.trace.mode == "refused"


def test_search_cli_precheck_refuses_before_search(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    restricted_query = "Restricted Brand Search"
    restricted_path = tmp_path / "restricted.json"
    restricted_path.write_text(json.dumps([{"brand_name": restricted_query}]), encoding="utf-8")

    def fail_search(*args, **kwargs):
        raise AssertionError("search must not run")

    monkeypatch.setattr(cli_module, "search_index", fail_search)
    exit_code = main(
        [
            "search",
            restricted_query,
            "--db",
            str(tmp_path / "unused.sqlite"),
            "--restricted-customers",
            str(restricted_path),
        ]
    )
    output = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert output == RESTRICTED_QUERY_REFUSAL
    audit_text = (tmp_path / "reports" / "audit_log.csv").read_text(encoding="utf-8")
    assert restricted_query not in audit_text


def test_zero_results_lists_effective_filters(tmp_path):
    db_path = _build_index(
        tmp_path,
        [_payload("Public", "public_metric", allowed_exposure_channels=["press_release"])],
    )

    answer = ask_index(
        "campaign",
        db_path,
        filters=SearchFilters(intent="external", status=["archived"]),
    )

    assert "找不到符合條件" in answer.answer
    assert "intent=external" in answer.answer
    assert "status=" in answer.answer


def test_low_relevance_abstains_and_lists_closest_titles():
    result = _search_result("Closest title", score=MIN_RELEVANCE_SCORE - 0.01)

    answer = generate_answer("unrelated", [result], filters=SearchFilters(intent="internal"))

    assert "相關度不足" in answer.answer
    assert "Closest title" in answer.answer
    assert answer.citations == []


def test_external_zero_results_reports_internal_only_count(tmp_path):
    db_path = _build_index(
        tmp_path,
        [
            _payload(
                "Internal case",
                "merchant_case",
                can_quote_externally=False,
                data_classification="internal",
            )
        ],
    )

    answer = ask_index(
        "campaign",
        db_path,
        filters=SearchFilters(intent="external"),
        limit=10,
    )

    assert "有 1 筆內部資料但無可對外引用版本" in answer.answer
    assert answer.citations == []


def test_no_result_answer_contains_no_percentage_fact(tmp_path):
    db_path = _build_index(
        tmp_path,
        [_payload("Public", "public_metric", allowed_exposure_channels=["press_release"])],
    )

    answer = ask_index(
        "not found",
        db_path,
        filters=SearchFilters(topic=["missing-topic"]),
    )

    assert answer.citations == []
    assert re.search(r"\d+%", answer.answer) is None


def _build_index(tmp_path, payloads):
    documents = []
    for index, payload in enumerate(payloads, start=1):
        content = payload.pop("content", "campaign knowledge")
        metadata = DocumentMetadata(
            source_type="database",
            status="published",
            publish_date=date(2026, 7, 1),
            source_path=f"synthetic:{index}",
            source_sheet="Synthetic",
            source_row=index,
            **payload,
        )
        documents.append(Document(id=f"doc-{index}", metadata=metadata, content=content))
    chunks = chunk_documents(documents)
    db_path = tmp_path / "index.sqlite"
    SQLiteIndex(db_path).rebuild(documents, chunks)
    return db_path


def _payload(title, record_type, **overrides):
    payload = {
        "title": title,
        "record_type": record_type,
        "data_classification": "public",
        "can_quote_externally": True,
        "allowed_exposure_channels": [],
        "content": "campaign knowledge",
    }
    payload.update(overrides)
    return payload


def _citation(title, record_type, can_quote):
    return Citation(
        label="[1]",
        title=title,
        source_path=f"synthetic:{title}",
        chunk_id=f"chunk-{title}",
        status="published",
        source_type="database",
        record_type=record_type,
        data_classification="public" if can_quote else "internal",
        can_quote_externally=can_quote,
        publish_date="2026-07-01",
        source_sheet="Synthetic",
        source_row=1,
        freshness_note="fresh",
    )


def _search_result(title, score):
    metadata = DocumentMetadata(
        title=title,
        source_type="database",
        record_type="merchant_case",
        status="published",
        publish_date=date(2026, 7, 1),
        source_path=f"synthetic:{title}",
        source_sheet="Synthetic",
        source_row=1,
    )
    chunk = Chunk(
        id=f"chunk-{title}",
        document_id=f"doc-{title}",
        chunk_index=0,
        text="closest but weak content",
        metadata=metadata,
    )
    return SearchResult(chunk=chunk, score=score, rerank_score=score)
