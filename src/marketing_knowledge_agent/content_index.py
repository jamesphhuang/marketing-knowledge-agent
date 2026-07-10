from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .chunking import chunk_documents
from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter
from .governance import GovernanceIndex, RestrictedCustomerRecord, split_restricted_aliases
from .indexing import SQLiteIndex
from .ingestion import stable_id
from .models import Document, DocumentMetadata
from .pipeline import DEFAULT_RESTRICTED_CUSTOMERS_PATH, search_index


MANAGED_BY = "marketing-knowledge-agent"
DEFAULT_CONTENT_INDEX_DB = Path(".mka/content_index.sqlite")
DEFAULT_CONTENT_INDEX_REPORT_DIR = Path("reports/content_index")
INDEXABLE_RECORD_TYPES = {"content_asset", "merchant_case", "public_metric"}
FORBIDDEN_RECORD_TYPES = {"restricted_customer", "handle_mapping", "pending_metric"}
SYNC_ONLY_KEYS = {"managed_by", "sync_batch_id", "synced_at", "content_checksum"}
ANOMALOUS_EXCLUSION_REASONS = {"forbidden_record_type", "metadata_parse_error"}


class ContentIndexError(ValueError):
    """Raised when a formal content index cannot be built safely."""


@dataclass(frozen=True)
class IncludedRecord:
    path: str
    document: Document


@dataclass(frozen=True)
class ExcludedRecord:
    path: str
    reason: str
    record_type: Optional[str] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class ContentIndexPlan:
    vault_path: Path
    namespace: str
    scanned_count: int
    managed_count: int
    latest_sync_batch_id: Optional[str]
    included: List[IncludedRecord]
    excluded: List[ExcludedRecord]

    @property
    def exclusion_counts(self) -> Dict[str, int]:
        return dict(sorted(Counter(record.reason for record in self.excluded).items()))

    @property
    def anomaly_count(self) -> int:
        return sum(record.reason in ANOMALOUS_EXCLUSION_REASONS for record in self.excluded)


def normalize_vault_frontmatter(meta: dict) -> dict:
    normalized = dict(meta)
    value = normalized.get("invalid_asset_values")
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ContentIndexError(f"invalid_asset_values contains invalid JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ContentIndexError("invalid_asset_values JSON must decode to an object")
        normalized["invalid_asset_values"] = parsed

    for key in SYNC_ONLY_KEYS:
        normalized.pop(key, None)
    return normalized


def create_content_index_plan(vault_path: Path, namespace: str = "MKA") -> ContentIndexPlan:
    vault_path = Path(vault_path)
    namespace_path = vault_path / namespace
    if not namespace_path.is_dir():
        raise ContentIndexError(f"vault namespace does not exist: {namespace_path}")

    markdown_files = sorted(
        path
        for path in namespace_path.rglob("*.md")
        if path.is_file() and not path.name.startswith("._")
    )
    included: List[IncludedRecord] = []
    excluded: List[ExcludedRecord] = []
    managed_count = 0
    sync_batch_ids = []

    for path in markdown_files:
        relative_path = path.relative_to(namespace_path).as_posix()
        try:
            raw_text = path.read_text(encoding="utf-8")
            frontmatter, body = parse_markdown_with_frontmatter(raw_text)
        except (UnicodeDecodeError, FrontmatterError) as exc:
            excluded.append(
                ExcludedRecord(relative_path, "metadata_parse_error", detail=str(exc))
            )
            continue

        if frontmatter.get("managed_by") != MANAGED_BY:
            excluded.append(
                ExcludedRecord(relative_path, "not_managed", record_type=_string_or_none(frontmatter.get("record_type")))
            )
            continue

        managed_count += 1
        if frontmatter.get("sync_batch_id"):
            sync_batch_ids.append(str(frontmatter["sync_batch_id"]))

        path_parts = Path(relative_path).parts
        if "_archived" in path_parts:
            excluded.append(
                ExcludedRecord(relative_path, "archived", record_type=_string_or_none(frontmatter.get("record_type")))
            )
            continue
        if "_vault_only" in path_parts:
            excluded.append(
                ExcludedRecord(relative_path, "vault_only", record_type=_string_or_none(frontmatter.get("record_type")))
            )
            continue
        if not _is_true(frontmatter.get("can_enter_content_index", True)):
            excluded.append(
                ExcludedRecord(relative_path, "index_flag_false", record_type=_string_or_none(frontmatter.get("record_type")))
            )
            continue

        record_type = _string_or_none(frontmatter.get("record_type"))
        if record_type not in INDEXABLE_RECORD_TYPES:
            excluded.append(ExcludedRecord(relative_path, "forbidden_record_type", record_type=record_type))
            continue
        if record_type == "public_metric" and not frontmatter.get("allowed_exposure_channels"):
            excluded.append(ExcludedRecord(relative_path, "metric_missing_channels", record_type=record_type))
            continue

        try:
            metadata_payload = normalize_vault_frontmatter(frontmatter)
            metadata_payload.setdefault("source_path", f"{namespace}/{relative_path}")
            metadata = DocumentMetadata(**metadata_payload)
        except (ContentIndexError, ValidationError, TypeError, ValueError) as exc:
            excluded.append(
                ExcludedRecord(relative_path, "metadata_parse_error", record_type=record_type, detail=str(exc))
            )
            continue

        document = Document(
            id=stable_id("doc", f"{namespace}/{relative_path}"),
            metadata=metadata,
            content=body.strip(),
        )
        included.append(IncludedRecord(relative_path, document))

    return ContentIndexPlan(
        vault_path=vault_path,
        namespace=namespace,
        scanned_count=len(markdown_files),
        managed_count=managed_count,
        latest_sync_batch_id=max(sync_batch_ids) if sync_batch_ids else None,
        included=included,
        excluded=excluded,
    )


def build_content_index(
    vault_path: Path,
    db_path: Path = DEFAULT_CONTENT_INDEX_DB,
    namespace: str = "MKA",
    report_dir: Path = DEFAULT_CONTENT_INDEX_REPORT_DIR,
    confirm: bool = False,
    restricted_customers_path: Path = DEFAULT_RESTRICTED_CUSTOMERS_PATH,
) -> dict:
    db_path = Path(db_path)
    report_dir = Path(report_dir)
    plan = create_content_index_plan(vault_path, namespace=namespace)
    assertions = _not_run_assertions()
    summary = _summary(plan, db_path, report_dir, confirm, assertions)
    _write_build_report(plan, summary, report_dir / "build_report.md")

    if not confirm:
        summary["exit_code"] = 1 if plan.anomaly_count else 0
        _append_audit_log(report_dir.parent / "audit_log.csv", "build-content-index plan", len(plan.included), db_path)
        return summary

    if plan.anomaly_count:
        _remove_database(db_path)
        reasons = sorted(
            reason for reason in plan.exclusion_counts if reason in ANOMALOUS_EXCLUSION_REASONS
        )
        raise ContentIndexError("content index preflight failed: " + ", ".join(reasons))

    documents = [record.document for record in plan.included]
    chunks = chunk_documents(documents)
    try:
        SQLiteIndex(db_path).rebuild(documents, chunks)
        governance_index = _load_governance_index(restricted_customers_path)
        _assert_forbidden_record_types(db_path)
        assertions["forbidden_record_types"] = "passed"
        _assert_restricted_denylist(db_path, governance_index)
        assertions["restricted_denylist"] = "passed"
        _assert_conservation(db_path, expected_documents=len(documents))
        assertions["conservation"] = "passed"
        _assert_retrieval_trace(db_path)
        assertions["retrieval_trace"] = "passed"
    except Exception as exc:
        _remove_database(db_path)
        failed_key = next((key for key, value in assertions.items() if value == "not_run"), None)
        if failed_key:
            assertions[failed_key] = f"failed: {exc}"
        summary = _summary(plan, db_path, report_dir, confirm, assertions)
        _write_build_report(plan, summary, report_dir / "build_report.md")
        if isinstance(exc, ContentIndexError):
            raise
        raise ContentIndexError(f"content index build failed: {exc}") from exc

    summary = _summary(plan, db_path, report_dir, confirm, assertions)
    summary["exit_code"] = 0
    _write_build_report(plan, summary, report_dir / "build_report.md")
    _append_audit_log(report_dir.parent / "audit_log.csv", "build-content-index --confirm", len(documents), db_path)
    return summary


def _assert_forbidden_record_types(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as connection:
        rows = connection.execute("SELECT metadata_json FROM documents").fetchall()
    forbidden_count = sum(
        json.loads(row[0]).get("record_type") in FORBIDDEN_RECORD_TYPES
        for row in rows
    )
    if forbidden_count:
        raise ContentIndexError(f"forbidden record types found in database: {forbidden_count}")


def _assert_restricted_denylist(db_path: Path, governance_index: GovernanceIndex) -> None:
    with sqlite3.connect(str(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT documents.title, chunks.text
            FROM chunks
            JOIN documents ON documents.id = chunks.document_id
            """
        ).fetchall()
    blocked_count = sum(
        governance_index.check_text(f"{title}\n{text}").blocked
        for title, text in rows
    )
    if blocked_count:
        raise ContentIndexError(f"restricted denylist assertion failed: {blocked_count} chunks matched")


def _assert_conservation(db_path: Path, expected_documents: int) -> None:
    counts = SQLiteIndex(db_path).counts()
    if counts["documents"] != expected_documents:
        raise ContentIndexError(
            f"conservation assertion failed: expected {expected_documents} documents, found {counts['documents']}"
        )
    if counts["chunks"] <= 0:
        raise ContentIndexError("conservation assertion failed: chunk count must be greater than zero")


def _assert_retrieval_trace(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as connection:
        row = connection.execute("SELECT title FROM documents ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise ContentIndexError("retrieval trace assertion failed: no indexed document")

    results = search_index(str(row[0]), db_path=db_path, limit=5, mode="hybrid")
    if not results:
        raise ContentIndexError("retrieval trace assertion failed: smoke query returned no results")
    top_metadata = results[0].chunk.metadata
    if not top_metadata.source_sheet or top_metadata.source_row is None:
        raise ContentIndexError("retrieval trace assertion failed: citation lacks source_sheet/source_row")


def _load_governance_index(path: Path) -> GovernanceIndex:
    path = Path(path)
    if not path.is_file():
        raise ContentIndexError(f"restricted denylist is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentIndexError(f"restricted denylist is invalid: {path}") from exc
    if not isinstance(payload, list):
        raise ContentIndexError("restricted denylist must be a JSON list")

    records = [
        RestrictedCustomerRecord(
            brand_name=item.get("brand_name") or "",
            website_url=item.get("website_url"),
            merchant_handle=item.get("merchant_handle"),
            restricted_aliases=item.get("restricted_aliases")
            or split_restricted_aliases(item.get("brand_name")),
            source_sheet=item.get("source_sheet"),
            source_row=item.get("source_row"),
        )
        for item in payload
        if isinstance(item, dict)
    ]
    return GovernanceIndex(records)


def _summary(
    plan: ContentIndexPlan,
    db_path: Path,
    report_dir: Path,
    confirm: bool,
    assertions: Dict[str, str],
) -> dict:
    return {
        "mode": "confirm" if confirm else "plan",
        "vault_path": str(plan.vault_path),
        "namespace": plan.namespace,
        "db_path": str(db_path),
        "report_path": str(report_dir / "build_report.md"),
        "scanned_count": plan.scanned_count,
        "managed_count": plan.managed_count,
        "indexable_count": len(plan.included),
        "excluded_count": len(plan.excluded),
        "exclusion_counts": plan.exclusion_counts,
        "anomaly_count": plan.anomaly_count,
        "latest_sync_batch_id": plan.latest_sync_batch_id,
        "assertions": dict(assertions),
    }


def _write_build_report(plan: ContentIndexPlan, summary: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Formal Content Index Build Report",
        "",
        "## Summary",
        "",
        f"- Mode: `{summary['mode']}`",
        f"- Vault: `{summary['vault_path']}`",
        f"- Namespace: `{summary['namespace']}`",
        f"- Database: `{summary['db_path']}`",
        f"- Scanned files: {summary['scanned_count']}",
        f"- Managed files: {summary['managed_count']}",
        f"- Indexable files: {summary['indexable_count']}",
        f"- Excluded files: {summary['excluded_count']}",
        f"- Latest sync_batch_id: `{summary['latest_sync_batch_id'] or ''}`",
        "",
        "## Exclusion Counts",
        "",
    ]
    if plan.exclusion_counts:
        lines.extend(f"- `{reason}`: {count}" for reason, count in plan.exclusion_counts.items())
    else:
        lines.append("- None")

    lines.extend(
        ["", "## Included Files", "", "| file | record_type | can_quote_externally |", "| --- | --- | --- |"]
    )
    for record in plan.included:
        metadata = record.document.metadata
        lines.append(f"| {_md(record.path)} | {_md(metadata.record_type)} | {str(metadata.can_quote_externally).lower()} |")
    if not plan.included:
        lines.append("| - | - | - |")

    lines.extend(["", "## Excluded Files", "", "| file | reason | record_type |", "| --- | --- | --- |"])
    for record in plan.excluded:
        lines.append(f"| {_md(record.path)} | {_md(record.reason)} | {_md(record.record_type)} |")
    if not plan.excluded:
        lines.append("| - | - | - |")

    lines.extend(["", "## Safety Assertions", ""])
    lines.extend(f"- `{name}`: {status}" for name, status in summary["assertions"].items())
    lines.extend(
        [
            "",
            "## Attention",
            "",
            f"- Anomalous exclusions requiring human review: {summary['anomaly_count']}",
            "- `forbidden_record_type` and `metadata_parse_error` indicate an upstream governance or metadata problem.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_audit_log(path: Path, command: str, index_count: int, db_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    header = []
    if exists:
        with path.open("r", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not exists:
            header = ["timestamp", "command", "index_count", "db_path"]
            writer.writerow(header)
        if header == ["timestamp", "command", "index_count", "db_path"]:
            writer.writerow([_utc_now(), command, index_count, str(db_path)])
            return
        if header == ["timestamp", "batch_id", "action", "add", "update", "archive", "operator", "plan_path"]:
            writer.writerow([_utc_now(), "", command, index_count, 0, 0, "", str(db_path)])
            return
        raise ContentIndexError(f"unsupported audit log header: {header}")


def _remove_database(db_path: Path) -> None:
    for path in [Path(db_path), Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]:
        if path.exists():
            path.unlink()


def _not_run_assertions() -> Dict[str, str]:
    return {
        "forbidden_record_types": "not_run",
        "restricted_denylist": "not_run",
        "conservation": "not_run",
        "retrieval_trace": "not_run",
    }


def _is_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "是", "有"}
    return False


def _string_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
