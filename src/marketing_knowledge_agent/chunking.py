from __future__ import annotations

import re
from typing import Iterable, List

from .ingestion import stable_id
from .models import Chunk, Document


def chunk_documents(
    documents: Iterable[Document],
    chunk_size: int = 900,
    overlap: int = 120,
) -> List[Chunk]:
    chunks: List[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document, chunk_size=chunk_size, overlap=overlap))
    return chunks


def chunk_document(document: Document, chunk_size: int = 900, overlap: int = 120) -> List[Chunk]:
    text = document.content.strip()
    if not text:
        return []

    raw_chunks = _split_text(text, chunk_size=chunk_size)
    chunks: List[Chunk] = []
    cursor = 0

    for index, raw_chunk in enumerate(raw_chunks):
        if overlap > 0 and index > 0:
            raw_chunk = f"{raw_chunks[index - 1][-overlap:]}\n\n{raw_chunk}"

        start_char = text.find(raw_chunks[index], cursor)
        if start_char == -1:
            start_char = cursor
        end_char = min(len(text), start_char + len(raw_chunks[index]))
        cursor = end_char

        chunk_id = stable_id("chunk", f"{document.id}:{index}")
        chunks.append(
            Chunk(
                id=chunk_id,
                document_id=document.id,
                chunk_index=index,
                text=raw_chunk.strip(),
                metadata=document.metadata,
                start_char=start_char,
                end_char=end_char,
            )
        )

    return chunks


def _split_text(text: str, chunk_size: int) -> List[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    chunks: List[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_paragraph(paragraph, chunk_size=chunk_size))
            continue

        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            chunks.append(current.strip())
            current = paragraph

    if current:
        chunks.append(current.strip())

    return chunks


def _split_long_paragraph(paragraph: str, chunk_size: int) -> List[str]:
    words = paragraph.split()
    if len(words) <= 1:
        return [paragraph[index : index + chunk_size] for index in range(0, len(paragraph), chunk_size)]

    chunks: List[str] = []
    current_words: List[str] = []
    current_length = 0
    for word in words:
        next_length = current_length + len(word) + (1 if current_words else 0)
        if current_words and next_length > chunk_size:
            chunks.append(" ".join(current_words))
            current_words = [word]
            current_length = len(word)
        else:
            current_words.append(word)
            current_length = next_length
    if current_words:
        chunks.append(" ".join(current_words))
    return chunks
