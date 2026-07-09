from datetime import date

from marketing_knowledge_agent.generation import generate_answer
from marketing_knowledge_agent.models import SearchFilters
from marketing_knowledge_agent.pipeline import ingest_vault, search_index

from fixtures import write_regression_vault


def test_generation_warns_for_deprecated_sources(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)
    results = search_index(
        "product-c demo transcript",
        db_path=db_path,
        filters=SearchFilters(status=["deprecated"]),
        limit=1,
    )

    answer = generate_answer("product-c demo transcript", results, today=date(2026, 6, 24))

    assert answer.citations
    assert answer.warnings
    assert "status=deprecated" in answer.warnings[0]
    assert answer.citations[0].freshness_note
