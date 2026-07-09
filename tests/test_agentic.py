from marketing_knowledge_agent.cli import main
from marketing_knowledge_agent.models import SearchFilters
from marketing_knowledge_agent.pipeline import agent_ask, ingest_vault

from fixtures import write_regression_vault


def test_agent_ask_uses_fast_path_for_simple_lookup(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)

    answer = agent_ask(
        "Product A ROI",
        db_path=db_path,
        filters=SearchFilters(product=["product-a"]),
        limit=3,
    )

    assert answer.trace.mode == "fast_path"
    assert answer.trace.analysis.question_type == "simple_lookup"
    assert answer.citations


def test_agent_ask_runs_multi_step_plan_for_comparison(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)

    answer = agent_ask(
        "比較 Product A 製造業 pricing case study 與 ROI blog",
        db_path=db_path,
        filters=SearchFilters(product=["product-a"], industry=["manufacturing"]),
        limit=4,
    )

    assert answer.trace.mode == "agentic_lite"
    assert answer.trace.analysis.question_type == "comparison"
    assert len(answer.trace.plan) >= 2
    assert len(answer.trace.observations) >= 2
    assert answer.citations
    assert any("showcase" in citation.source_path for citation in answer.citations)


def test_agent_ask_preserves_status_warning_for_non_public_sources(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)

    answer = agent_ask(
        "哪些內容不可直接對外引用？",
        db_path=db_path,
        filters=SearchFilters(status=["archived"]),
        limit=3,
    )

    assert answer.trace.mode == "agentic_lite"
    assert answer.citations
    assert answer.warnings
    assert "不可直接對外引用" in answer.answer
    assert all(citation.status == "archived" for citation in answer.citations)


def test_agent_ask_cli_can_print_trace(tmp_path, capsys):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)

    exit_code = main(
        [
            "agent-ask",
            "比較 Product A 製造業 pricing case study 與 ROI blog",
            "--db",
            str(db_path),
            "--product",
            "product-a",
            "--show-trace",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Agent Trace:" in output
    assert '"mode": "agentic_lite"' in output
