from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, List

from pydantic import ValidationError

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .models import Document, DocumentMetadata


class IngestionError(ValueError):
    """Raised when a Markdown document cannot be ingested."""


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def discover_markdown_files(vault_path: Path) -> List[Path]:
    return sorted(
        path
        for path in vault_path.rglob("*.md")
        if path.is_file() and not path.name.startswith("._")
    )


def load_markdown_file(path: Path, vault_path: Path) -> Document:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise IngestionError(f"{path}: file is not valid UTF-8") from exc

    try:
        frontmatter, body = parse_markdown_with_frontmatter(raw_text)
    except FrontmatterError as exc:
        raise IngestionError(f"{path}: {exc}") from exc

    relative_path = path.relative_to(vault_path).as_posix()
    metadata_payload = dict(frontmatter)
    metadata_payload["source_path"] = relative_path

    try:
        metadata = DocumentMetadata(**metadata_payload)
    except ValidationError as exc:
        raise IngestionError(f"{relative_path}: invalid metadata: {exc}") from exc

    return Document(
        id=stable_id("doc", relative_path),
        metadata=metadata,
        content=body.strip(),
    )


def load_documents(vault_path: Path) -> List[Document]:
    if not vault_path.exists():
        raise IngestionError(f"vault path does not exist: {vault_path}")
    if not vault_path.is_dir():
        raise IngestionError(f"vault path is not a directory: {vault_path}")

    documents = [load_markdown_file(path, vault_path) for path in discover_markdown_files(vault_path)]
    return documents


def iter_documents(vault_path: Path) -> Iterable[Document]:
    for path in discover_markdown_files(vault_path):
        yield load_markdown_file(path, vault_path)
