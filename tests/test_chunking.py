from marketing_knowledge_agent.chunking import chunk_documents
from marketing_knowledge_agent.ingestion import load_documents

from fixtures import write_regression_vault


def test_chunking_preserves_document_context(tmp_path):
    vault = write_regression_vault(tmp_path)
    documents = load_documents(vault)
    chunks = chunk_documents(documents, chunk_size=240, overlap=40)

    assert chunks
    first = chunks[0]
    assert first.document_id
    assert first.id.startswith("chunk_")
    assert first.source_path.endswith(".md")
    assert first.metadata.title
    assert first.start_char <= first.end_char
