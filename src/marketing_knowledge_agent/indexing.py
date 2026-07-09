from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .embeddings import embed_text
from .models import Chunk, Document, DocumentMetadata


@dataclass(frozen=True)
class IndexedChunk:
    chunk: Chunk
    embedding: List[float]


class SQLiteIndex:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def rebuild(self, documents: Iterable[Document], chunks: Iterable[Chunk]) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            self._drop_schema(connection)
            self._create_schema(connection)
            self._insert_documents(connection, list(documents))
            self._insert_chunks(connection, list(chunks))

    def load_chunks(self) -> List[IndexedChunk]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    chunks.id AS chunk_id,
                    chunks.document_id,
                    chunks.chunk_index,
                    chunks.text,
                    chunks.embedding_json,
                    chunks.start_char,
                    chunks.end_char,
                    documents.metadata_json,
                    documents.title,
                    documents.source_type,
                    documents.content_category,
                    documents.parent_source_type,
                    documents.product_json,
                    documents.industry_json,
                    documents.topic_json,
                    documents.funnel_stage_json,
                    documents.status,
                    documents.publish_date,
                    documents.updated_date,
                    documents.source_path,
                    documents.canonical_url,
                    documents.language,
                    documents.author,
                    documents.asset_type
                FROM chunks
                JOIN documents ON documents.id = chunks.document_id
                ORDER BY documents.source_path, chunks.chunk_index
                """
            ).fetchall()
        return [_row_to_indexed_chunk(row) for row in rows]

    def keyword_scores(self, query: str, limit: int = 50) -> dict:
        fts_query = _to_fts_query(query)
        if not fts_query:
            return {}

        with self.connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT chunk_id, bm25(chunks_fts) AS rank
                    FROM chunks_fts
                    WHERE chunks_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}

        raw_scores = {row["chunk_id"]: 1.0 / (1.0 + abs(float(row["rank"]))) for row in rows}
        if not raw_scores:
            return {}
        max_score = max(raw_scores.values())
        return {chunk_id: score / max_score for chunk_id, score in raw_scores.items()}

    def counts(self) -> dict:
        with self.connect() as connection:
            document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": document_count, "chunks": chunk_count}

    def _drop_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute("DROP TABLE IF EXISTS chunks_fts")
        connection.execute("DROP TABLE IF EXISTS chunks")
        connection.execute("DROP TABLE IF EXISTS documents")

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                content_category TEXT,
                parent_source_type TEXT,
                product_json TEXT NOT NULL,
                industry_json TEXT NOT NULL,
                topic_json TEXT NOT NULL,
                funnel_stage_json TEXT NOT NULL,
                status TEXT NOT NULL,
                publish_date TEXT NOT NULL,
                updated_date TEXT,
                source_path TEXT NOT NULL,
                canonical_url TEXT,
                language TEXT NOT NULL,
                author TEXT,
                asset_type TEXT,
                metadata_json TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                start_char INTEGER NOT NULL,
                end_char INTEGER NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE chunks_fts
            USING fts5(chunk_id UNINDEXED, title, body)
            """
        )

    def _insert_documents(self, connection: sqlite3.Connection, documents: List[Document]) -> None:
        rows = []
        for document in documents:
            metadata = document.metadata
            rows.append(
                (
                    document.id,
                    metadata.title,
                    metadata.source_type,
                    metadata.content_category,
                    metadata.parent_source_type,
                    json.dumps(metadata.product, ensure_ascii=False),
                    json.dumps(metadata.industry, ensure_ascii=False),
                    json.dumps(metadata.topic, ensure_ascii=False),
                    json.dumps(metadata.funnel_stage, ensure_ascii=False),
                    metadata.status,
                    metadata.publish_date.isoformat(),
                    metadata.updated_date.isoformat() if metadata.updated_date else None,
                    metadata.source_path,
                    metadata.canonical_url,
                    metadata.language,
                    metadata.author,
                    metadata.asset_type,
                    json.dumps(metadata.metadata_dict(), ensure_ascii=False),
                    document.content,
                )
            )
        connection.executemany(
            """
            INSERT INTO documents (
                id, title, source_type, content_category, parent_source_type,
                product_json, industry_json, topic_json,
                funnel_stage_json, status, publish_date, updated_date, source_path,
                canonical_url, language, author, asset_type, metadata_json, content
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _insert_chunks(self, connection: sqlite3.Connection, chunks: List[Chunk]) -> None:
        chunk_rows = []
        fts_rows = []
        for chunk in chunks:
            embedding = embed_text(chunk.text)
            chunk_rows.append(
                (
                    chunk.id,
                    chunk.document_id,
                    chunk.chunk_index,
                    chunk.text,
                    json.dumps(embedding),
                    chunk.start_char,
                    chunk.end_char,
                )
            )
            fts_rows.append((chunk.id, chunk.metadata.title, chunk.text))

        connection.executemany(
            """
            INSERT INTO chunks (
                id, document_id, chunk_index, text, embedding_json, start_char, end_char
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            chunk_rows,
        )
        connection.executemany(
            "INSERT INTO chunks_fts (chunk_id, title, body) VALUES (?, ?, ?)",
            fts_rows,
        )


def _row_to_indexed_chunk(row: sqlite3.Row) -> IndexedChunk:
    metadata = DocumentMetadata(**json.loads(row["metadata_json"]))
    chunk = Chunk(
        id=row["chunk_id"],
        document_id=row["document_id"],
        chunk_index=row["chunk_index"],
        text=row["text"],
        metadata=metadata,
        start_char=row["start_char"],
        end_char=row["end_char"],
    )
    return IndexedChunk(chunk=chunk, embedding=json.loads(row["embedding_json"]))


def _to_fts_query(query: str) -> str:
    terms = []
    for raw_term in query.replace("/", " ").split():
        term = raw_term.strip().strip(".,:;!?()[]{}'\"")
        if not term:
            continue
        terms.append(f'"{term.replace(chr(34), chr(34) + chr(34))}"')
    return " OR ".join(terms)
