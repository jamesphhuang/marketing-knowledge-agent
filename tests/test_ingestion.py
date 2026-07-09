import pytest

from marketing_knowledge_agent.ingestion import IngestionError, load_documents

from fixtures import write_regression_vault


def test_load_documents_reads_frontmatter_and_body(tmp_path):
    vault = write_regression_vault(tmp_path)
    documents = load_documents(vault)

    assert len(documents) == 4
    titles = {document.metadata.title for document in documents}
    assert "Product A 製造業 ROI 定價指南" in titles

    blog = next(document for document in documents if document.metadata.source_type == "blog")
    assert blog.metadata.product == ["product-a"]
    assert blog.metadata.industry == ["manufacturing"]
    assert blog.metadata.publish_date.isoformat() == "2026-01-15"
    assert blog.metadata.source_path == "blog/product-a-roi-pricing.md"
    assert "ROI" in blog.content


def test_invalid_metadata_raises_clear_error(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "bad.md").write_text(
        "---\nsource_type: blog\nproduct: [product-a]\npublish_date: 2026-01-01\n---\nBody",
        encoding="utf-8",
    )

    with pytest.raises(IngestionError, match="invalid metadata"):
        load_documents(vault)
