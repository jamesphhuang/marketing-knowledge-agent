from marketing_knowledge_agent.models import SearchFilters
from marketing_knowledge_agent.pipeline import ask_index, ingest_vault, search_index

from fixtures import write_regression_vault


def test_ingest_and_search_with_metadata_filter(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    summary = ingest_vault(vault, db_path)

    assert summary["documents"] == 4
    assert summary["chunks"] >= 4

    results = search_index(
        "pricing case studies product-a manufacturing",
        db_path=db_path,
        filters=SearchFilters(source_type=["showcase"], product=["product-a"]),
        limit=3,
    )

    assert results
    assert all(result.chunk.metadata.source_type == "showcase" for result in results)
    assert all("product-a" in result.chunk.metadata.product for result in results)


def test_vector_search_returns_relevant_mock_document(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)

    results = search_index(
        "製造業 ROI 定價",
        db_path=db_path,
        filters=SearchFilters(product=["product-a"], industry=["manufacturing"]),
        mode="vector",
        limit=2,
    )

    assert results
    assert any("roi" in result.chunk.metadata.topic for result in results)


def test_search_supports_showcase_blog_relationship_filters(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)

    results = search_index(
        "pricing case studies product-a manufacturing",
        db_path=db_path,
        filters=SearchFilters(content_category=["showcase"], parent_source_type=["blog"]),
        limit=3,
    )

    assert results
    assert all(result.chunk.metadata.content_category == "showcase" for result in results)
    assert all(result.chunk.metadata.parent_source_type == "blog" for result in results)


def test_ask_includes_citations_and_status_warning(tmp_path):
    vault = write_regression_vault(tmp_path)
    db_path = tmp_path / "index.sqlite"
    ingest_vault(vault, db_path)

    answer = ask_index(
        "launch social post",
        db_path=db_path,
        filters=SearchFilters(status=["archived"]),
        limit=3,
    )

    assert answer.citations
    assert answer.warnings
    assert "不可直接對外引用" in answer.answer
    assert answer.citations[0].status == "archived"
