from pathlib import Path

from marketing_knowledge_agent.models import SearchFilters
from marketing_knowledge_agent.pipeline import ask_index, ingest_vault, search_index


SAMPLE_VAULT = Path(__file__).resolve().parents[1] / "data" / "sample_vault"


def test_sample_vault_ingests_real_website_content(tmp_path):
    db_path = tmp_path / "sample.sqlite"
    summary = ingest_vault(SAMPLE_VAULT, db_path)

    assert summary["documents"] == 4
    assert summary["chunks"] >= 4

    results = search_index(
        "SHOPLINE 方案費用 全通路領航員 Open API",
        db_path=db_path,
        filters=SearchFilters(source_type=["website"], topic=["pricing"]),
        limit=3,
    )

    assert results
    assert results[0].chunk.metadata.canonical_url == "https://shopline.tw/about/pricing"


def test_sample_vault_ask_returns_citations_without_status_warning(tmp_path):
    db_path = tmp_path / "sample.sqlite"
    ingest_vault(SAMPLE_VAULT, db_path)

    answer = ask_index(
        "SHOPLINE 有哪些全通路開店工具？",
        db_path=db_path,
        filters=SearchFilters(source_type=["website"], funnel_stage=["awareness"]),
        limit=3,
    )

    assert answer.citations
    assert not answer.warnings
    assert all(citation.status == "published" for citation in answer.citations)
