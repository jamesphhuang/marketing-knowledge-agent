from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from pydantic import ValidationError

from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .ingestion import discover_markdown_files
from .models import DocumentMetadata


SHOWCASE_PARENT_WARNING = (
    "source_type=showcase 建議補 parent_source_type=blog 與 "
    "content_category=showcase，以保留它隸屬 blog 內容分類的資料關係。"
)


def validate_vault(vault_path: Path) -> Dict[str, object]:
    vault_path = Path(vault_path)
    if not vault_path.exists():
        raise FileNotFoundError(f"vault path does not exist: {vault_path}")
    if not vault_path.is_dir():
        raise FileNotFoundError(f"vault path is not a directory: {vault_path}")

    markdown_files = set(discover_markdown_files(vault_path))
    results: List[Dict[str, object]] = []

    for path in sorted(file_path for file_path in vault_path.rglob("*") if file_path.is_file()):
        relative_path = path.relative_to(vault_path).as_posix()
        if _is_hidden_or_system_path(path, vault_path):
            results.append(_skipped(relative_path, "hidden_or_system_file"))
            continue
        if path.suffix.lower() != ".md":
            results.append(_skipped(relative_path, "unsupported_extension"))
            continue
        if path not in markdown_files:
            results.append(_skipped(relative_path, "ignored_markdown_file"))
            continue
        results.append(validate_markdown_file(path, vault_path))

    return {
        "vault_path": str(vault_path),
        "summary": {
            "valid": sum(1 for result in results if result["status"] == "valid"),
            "invalid": sum(1 for result in results if result["status"] == "invalid"),
            "skipped": sum(1 for result in results if result["status"] == "skipped"),
            "warnings": sum(len(result["warnings"]) for result in results),
            "total_files": len(results),
        },
        "files": results,
    }


def validate_markdown_file(path: Path, vault_path: Path) -> Dict[str, object]:
    relative_path = path.relative_to(vault_path).as_posix()
    errors: List[Dict[str, str]] = []
    warnings: List[str] = []

    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _invalid(relative_path, [{"code": "invalid_utf8", "message": "file is not valid UTF-8"}])

    try:
        frontmatter, body = parse_markdown_with_frontmatter(raw_text)
    except FrontmatterError as exc:
        return _invalid(relative_path, [{"code": "malformed_frontmatter", "message": str(exc)}])

    if not frontmatter:
        return _invalid(
            relative_path,
            [{"code": "missing_frontmatter", "message": "file has no YAML frontmatter"}],
        )

    metadata_payload = dict(frontmatter)
    metadata_payload["source_path"] = relative_path
    try:
        metadata = DocumentMetadata(**metadata_payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = ".".join(str(part) for part in error.get("loc", []))
            errors.append(
                {
                    "code": "invalid_metadata",
                    "field": field,
                    "message": error.get("msg", "invalid metadata"),
                }
            )
        return _invalid(relative_path, errors)

    if not body.strip():
        warnings.append("document body is empty")
    if metadata.source_type == "showcase":
        if metadata.parent_source_type != "blog" or metadata.content_category != "showcase":
            warnings.append(SHOWCASE_PARENT_WARNING)

    return {
        "path": relative_path,
        "status": "valid",
        "errors": [],
        "warnings": warnings,
        "metadata": {
            "title": metadata.title,
            "source_type": metadata.source_type,
            "content_category": metadata.content_category,
            "parent_source_type": metadata.parent_source_type,
            "status": metadata.status,
            "publish_date": metadata.publish_date.isoformat(),
            "canonical_url": metadata.canonical_url,
        },
    }


def _invalid(path: str, errors: List[Dict[str, str]]) -> Dict[str, object]:
    return {
        "path": path,
        "status": "invalid",
        "errors": errors,
        "warnings": [],
        "metadata": {},
    }


def _skipped(path: str, reason: str) -> Dict[str, object]:
    return {
        "path": path,
        "status": "skipped",
        "errors": [],
        "warnings": [],
        "skip_reason": reason,
        "metadata": {},
    }


def _is_hidden_or_system_path(path: Path, vault_path: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(vault_path).parts)
