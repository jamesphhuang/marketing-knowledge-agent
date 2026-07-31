from __future__ import annotations

import csv
import json
import re
import sqlite3
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from xml.etree import ElementTree

from .asset_metadata import (
    ASSET_METADATA_FIELD_REGISTRY,
    PUBLICATION_STATUS_VALUES,
    canonical_url_candidate,
    direct_asset_url_candidate,
    is_enrichment_index_eligible,
    parse_asset_date,
)
from .asset_metadata_reports import (
    build_preview_summary,
    render_proposed_schema,
    render_query_support_matrix,
    render_summary,
)
from .excel_ingestion import SHEET_MERCHANT_CASES
from .excel_preview import PACKAGE_REL_NS, REL_NS, WORKBOOK_NS, read_xlsx_workbook
from .frontmatter import FrontmatterError, parse_markdown_with_frontmatter


ASSET_FIELDS = {
    "article": ("article_title", "文章"),
    "video": ("video_title", "影片"),
    "podcast": ("podcast_title", "Podcast"),
    "news": ("news_title", "新聞"),
}
OUTPUT_FILENAMES = (
    "asset_metadata_inventory.csv",
    "asset_metadata_enrichment_preview.csv",
    "asset_metadata_conflicts.csv",
    "asset_metadata_missing.csv",
    "asset_metadata_summary.md",
    "proposed_asset_metadata_schema.md",
    "proposed_query_support_matrix.md",
    "human_review_template.csv",
)
INVENTORY_COLUMNS = (
    "record_id",
    "asset_id",
    "record_type",
    "brand_name",
    "asset_type",
    "asset_title",
    "source_sheet",
    "source_row",
    "asset_source_field",
    "excel_cell",
    "source_urls",
    "asset_url",
    "canonical_url",
    "asset_published_at",
    "asset_publication_status",
    "interview_date",
    "interview_status",
    "review_status",
    "partner_name",
    "internal_file_path",
    "vault_present",
    "sqlite_present",
    "record_publish_date",
    "record_status",
    "record_review_decision",
    "invalid_asset_value",
    "evidence_sources",
)
ENRICHMENT_COLUMNS = (
    "record_id",
    "asset_id",
    "brand_name",
    "asset_type",
    "field",
    "existing_value",
    "proposed_value",
    "source",
    "source_location",
    "provenance",
    "confidence",
    "conflict_status",
    "review_required",
    "reason",
    "proposed_decision",
    "approved_for_index",
)
HUMAN_REVIEW_COLUMNS = ENRICHMENT_COLUMNS + (
    "review_decision",
    "reviewer",
    "reviewed_at",
    "notes",
)


class AssetMetadataPreviewError(ValueError):
    """Raised when a read-only asset metadata preview cannot be built safely."""


def generate_asset_metadata_preview(
    preview_dir: Path,
    output_dir: Path,
    workbook_path: Optional[Path] = None,
    vault_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    decisions_path: Optional[Path] = None,
) -> dict:
    preview_dir = Path(preview_dir)
    output_dir = Path(output_dir)
    _assert_safe_output(output_dir, preview_dir, vault_path, db_path)

    merchant_path = preview_dir / "merchant_cases.json"
    if not merchant_path.is_file():
        raise AssetMetadataPreviewError(f"merchant preview does not exist: {merchant_path}")
    merchant_records = _read_json_list(merchant_path)

    if workbook_path is None:
        workbook_path = _discover_workbook(preview_dir)
    workbook_hyperlinks = (
        read_workbook_asset_hyperlinks(Path(workbook_path))
        if workbook_path is not None and Path(workbook_path).is_file()
        else {}
    )
    vault_records = read_vault_metadata(vault_path) if vault_path is not None else {}
    sqlite_records = read_sqlite_metadata(db_path) if db_path is not None else {}
    decisions = read_review_decisions(decisions_path) if decisions_path is not None else {}

    inventory = build_asset_inventory(
        merchant_records,
        workbook_hyperlinks=workbook_hyperlinks,
        vault_records=vault_records,
        sqlite_records=sqlite_records,
        decisions=decisions,
    )
    enrichment = build_enrichment_preview(inventory)
    conflicts = [row for row in enrichment if row["conflict_status"] not in {"none", "missing_evidence"}]
    missing = [row for row in enrichment if row["conflict_status"] == "missing_evidence"]
    review_rows = [
        {**row, "review_decision": "", "reviewer": "", "reviewed_at": "", "notes": ""}
        for row in enrichment
    ]
    summary = build_preview_summary(
        inventory,
        enrichment,
        conflicts,
        missing,
        workbook_path=workbook_path,
        vault_path=vault_path,
        db_path=db_path,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "asset_metadata_inventory.csv", inventory, INVENTORY_COLUMNS)
    _write_csv(output_dir / "asset_metadata_enrichment_preview.csv", enrichment, ENRICHMENT_COLUMNS)
    _write_csv(output_dir / "asset_metadata_conflicts.csv", conflicts, ENRICHMENT_COLUMNS)
    _write_csv(output_dir / "asset_metadata_missing.csv", missing, ENRICHMENT_COLUMNS)
    _write_csv(output_dir / "human_review_template.csv", review_rows, HUMAN_REVIEW_COLUMNS)
    (output_dir / "asset_metadata_summary.md").write_text(
        render_summary(summary), encoding="utf-8"
    )
    (output_dir / "proposed_asset_metadata_schema.md").write_text(
        render_proposed_schema(), encoding="utf-8"
    )
    (output_dir / "proposed_query_support_matrix.md").write_text(
        render_query_support_matrix(summary), encoding="utf-8"
    )
    return summary


def build_asset_inventory(
    merchant_records: Sequence[dict],
    *,
    workbook_hyperlinks: Optional[Mapping[Tuple[str, int, str], Sequence[str]]] = None,
    vault_records: Optional[Mapping[Tuple[str, int], Sequence[dict]]] = None,
    sqlite_records: Optional[Mapping[Tuple[str, int], Sequence[dict]]] = None,
    decisions: Optional[Mapping[Tuple[str, int, str], dict]] = None,
) -> List[dict]:
    workbook_hyperlinks = workbook_hyperlinks or {}
    vault_records = vault_records or {}
    sqlite_records = sqlite_records or {}
    decisions = decisions or {}
    inventory: List[dict] = []

    for record in merchant_records:
        if record.get("record_type") != "merchant_case":
            continue
        sheet = str(record.get("source_sheet") or SHEET_MERCHANT_CASES)
        row = _integer_or_none(record.get("source_row"))
        if row is None:
            continue
        record_id = f"{sheet}:r{row}"
        record_key = (sheet, row)
        invalid_values = record.get("invalid_asset_values") or {}
        if not isinstance(invalid_values, dict):
            invalid_values = {}
        decision = decisions.get((sheet, row, "merchant_case"), {})
        vault_matches = list(vault_records.get(record_key, ()))
        sqlite_matches = list(sqlite_records.get(record_key, ()))

        for asset_type, (title_field, source_label) in ASSET_FIELDS.items():
            title = _text(record.get(title_field))
            invalid_value = _text(invalid_values.get(source_label))
            if not title and not invalid_value:
                continue
            urls = _unique_urls(workbook_hyperlinks.get((sheet, row, asset_type), ()))
            vault_match = _matching_record(vault_matches, title_field, title)
            sqlite_match = _matching_record(sqlite_matches, title_field, title)
            asset_id = f"{record_id}:{asset_type}"
            evidence = ["excel_preview"]
            if urls:
                evidence.append("excel_hyperlink")
            if vault_match:
                evidence.append("managed_vault")
            if sqlite_match:
                evidence.append("formal_sqlite")
            if decision:
                evidence.append("record_review_decision")
            inventory.append(
                {
                    "record_id": record_id,
                    "asset_id": asset_id,
                    "record_type": "merchant_case",
                    "brand_name": _text(record.get("brand_name")),
                    "asset_type": asset_type,
                    "asset_title": title,
                    "source_sheet": sheet,
                    "source_row": row,
                    "asset_source_field": title_field,
                    "excel_cell": _excel_cell_for_asset(workbook_hyperlinks, sheet, row, asset_type),
                    "source_urls": urls,
                    "asset_url": _text(record.get(f"{asset_type}_url")),
                    "canonical_url": _explicit_asset_value(record, vault_match, f"{asset_type}_canonical_url"),
                    "asset_published_at": _explicit_asset_value(record, vault_match, f"{asset_type}_published_at"),
                    "asset_publication_status": _explicit_asset_value(
                        record, vault_match, f"{asset_type}_publication_status"
                    ),
                    "interview_date": _text(record.get("interview_date")),
                    "interview_status": _text(record.get("interview_status")),
                    "review_status": _text(record.get("asset_review_status")),
                    "partner_name": _text(record.get("partner_name")),
                    "internal_file_path": _text(vault_match.get("_internal_file_path")) if vault_match else "",
                    "vault_present": bool(vault_match),
                    "sqlite_present": bool(sqlite_match),
                    "record_publish_date": _text(record.get("publish_date")),
                    "record_status": _text(record.get("status")),
                    "record_review_decision": _text(decision.get("review_decision")),
                    "invalid_asset_value": invalid_value,
                    "evidence_sources": evidence,
                }
            )
    return sorted(inventory, key=lambda row: (row["source_sheet"], row["source_row"], row["asset_type"]))


def build_enrichment_preview(inventory: Sequence[dict]) -> List[dict]:
    rows: List[dict] = []
    for asset in inventory:
        for field_name in ASSET_METADATA_FIELD_REGISTRY:
            resolution = _resolve_field(asset, field_name)
            rows.append(
                {
                    "record_id": asset["record_id"],
                    "asset_id": asset["asset_id"],
                    "brand_name": asset.get("brand_name") or "",
                    "asset_type": asset["asset_type"],
                    "field": field_name,
                    "existing_value": resolution["existing_value"],
                    "proposed_value": resolution["proposed_value"],
                    "source": resolution["source"],
                    "source_location": resolution["source_location"],
                    "provenance": resolution["provenance"],
                    "confidence": resolution["confidence"],
                    "conflict_status": resolution["conflict_status"],
                    "review_required": resolution["review_required"],
                    "reason": resolution["reason"],
                    "proposed_decision": resolution["proposed_decision"],
                    "approved_for_index": False,
                }
            )
    return rows


def _resolve_field(asset: dict, field_name: str) -> dict:
    if field_name == "asset_url":
        existing = _text(asset.get("asset_url"))
        candidates = _unique_urls([existing, *(asset.get("source_urls") or [])])
        result = direct_asset_url_candidate(candidates)
        return _resolution(existing, result, asset, field_name)
    if field_name == "canonical_url":
        existing = _text(asset.get("canonical_url"))
        candidates = _unique_urls(
            [existing, asset.get("asset_url"), *(asset.get("source_urls") or [])]
        )
        result = canonical_url_candidate(candidates)
        return _resolution(existing, result, asset, field_name)
    if field_name == "published_at":
        existing = _text(asset.get("asset_published_at"))
        parsed = parse_asset_date(existing) if existing else None
        if existing and parsed is None:
            return _invalid_resolution(existing, "invalid asset-level published_at")
        if parsed:
            return _explicit_resolution(existing, parsed, asset, "explicit_asset_metadata", "high")
        return _missing_resolution("no exact asset-level publication date; record dates are not substitutes")
    if field_name == "publication_status":
        existing = _text(asset.get("asset_publication_status")) or "unknown"
        if existing not in PUBLICATION_STATUS_VALUES:
            return _invalid_resolution(existing, "asset-level publication status is outside the allowed enum")
        if existing != "unknown":
            return _explicit_resolution(existing, existing, asset, "explicit_asset_metadata", "high")
        marker = _text(asset.get("invalid_asset_value"))
        reason = "asset-level evidence is absent; URL presence and parent record status are not evidence"
        if marker:
            reason += f"; asset field marker '{marker}' is insufficient to choose a canonical status"
        return _missing_resolution(reason, existing_value="unknown", proposed_value="unknown")
    if field_name == "interview_date":
        return _resolve_explicit_date(asset, "interview_date", "no exact interview date; interview_year is not a date")
    if field_name == "interview_status":
        return _resolve_explicit_enum(
            asset,
            "interview_status",
            ASSET_METADATA_FIELD_REGISTRY[field_name].valid_values,
            "no interview workflow status; merchant status is not a substitute",
        )
    if field_name == "review_status":
        value = _text(asset.get("review_status"))
        if value:
            return _resolve_explicit_enum(
                asset,
                "review_status",
                ASSET_METADATA_FIELD_REGISTRY[field_name].valid_values,
                "no explicit asset review status",
            )
        reason = "record review_decision is governance evidence, not an asset review status"
        return _missing_resolution(reason, proposed_value="unknown")
    if field_name == "partner_name":
        value = _text(asset.get("partner_name"))
        if value:
            return _explicit_resolution(value, value, asset, "explicit_partner_identity", "high")
        return _missing_resolution("shared merchant / partner name cannot establish partner identity")
    raise AssetMetadataPreviewError(f"unsupported asset preview field: {field_name}")


def read_workbook_asset_hyperlinks(workbook_path: Path) -> Dict[Tuple[str, int, str], List[str]]:
    workbook_path = Path(workbook_path)
    sheets = read_xlsx_workbook(workbook_path)
    if SHEET_MERCHANT_CASES not in sheets:
        raise AssetMetadataPreviewError(f"workbook is missing sheet: {SHEET_MERCHANT_CASES}")
    merchant_rows = sheets[SHEET_MERCHANT_CASES]
    if len(merchant_rows) < 6:
        raise AssetMetadataPreviewError("merchant sheet header row 6 is missing")
    headers = [str(value or "").strip() for value in merchant_rows[5]]
    asset_columns = {
        index: asset_type
        for index, header in enumerate(headers)
        for asset_type, (_, source_label) in ASSET_FIELDS.items()
        if header == source_label
    }

    with zipfile.ZipFile(workbook_path) as archive:
        sheet_paths = _xlsx_sheet_paths(archive)
        sheet_path = sheet_paths.get(SHEET_MERCHANT_CASES)
        if not sheet_path:
            raise AssetMetadataPreviewError("merchant sheet XML path is missing")
        relationships = _sheet_relationship_targets(archive, sheet_path)
        root = ElementTree.fromstring(archive.read(sheet_path))
        result: Dict[Tuple[str, int, str], List[str]] = defaultdict(list)
        for element in root.findall(f".//{{{WORKBOOK_NS}}}hyperlink"):
            cell_ref = element.attrib.get("ref", "").split(":", 1)[0]
            cell = _parse_cell_reference(cell_ref)
            if cell is None:
                continue
            column, row = cell
            asset_type = asset_columns.get(column)
            rel_id = element.attrib.get(f"{{{REL_NS}}}id")
            target = relationships.get(rel_id or "")
            if asset_type and row >= 7 and target and target.startswith(("http://", "https://")):
                key = (SHEET_MERCHANT_CASES, row, asset_type)
                if target not in result[key]:
                    result[key].append(target)
        return dict(result)


def read_vault_metadata(vault_path: Path) -> Dict[Tuple[str, int], List[dict]]:
    vault_path = Path(vault_path)
    if not vault_path.is_dir():
        return {}
    result: Dict[Tuple[str, int], List[dict]] = defaultdict(list)
    for path in sorted(vault_path.rglob("*.md")):
        if not path.is_file() or path.name.startswith("._"):
            continue
        try:
            metadata, _ = parse_markdown_with_frontmatter(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, FrontmatterError):
            continue
        key = _source_key(metadata)
        if key is None:
            continue
        result[key].append({**metadata, "_internal_file_path": path.relative_to(vault_path).as_posix()})
    return dict(result)


def read_sqlite_metadata(db_path: Path) -> Dict[Tuple[str, int], List[dict]]:
    db_path = Path(db_path)
    if not db_path.is_file():
        return {}
    uri = f"file:{db_path.resolve()}?mode=ro"
    result: Dict[Tuple[str, int], List[dict]] = defaultdict(list)
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute("SELECT metadata_json FROM documents").fetchall()
    except sqlite3.Error as exc:
        raise AssetMetadataPreviewError(f"unable to read SQLite metadata: {exc}") from exc
    for (payload,) in rows:
        try:
            metadata = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        key = _source_key(metadata)
        if key is not None:
            result[key].append(metadata)
    return dict(result)


def read_review_decisions(path: Path) -> Dict[Tuple[str, int, str], dict]:
    path = Path(path)
    if not path.is_file():
        return {}
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_row = _integer_or_none(row.get("source_row"))
            if source_row is None:
                continue
            key = (_text(row.get("source_sheet")), source_row, _text(row.get("record_type")))
            result[key] = row
    return result


def _resolution(existing: str, candidate: dict, asset: dict, field_name: str) -> dict:
    locations = [asset.get("excel_cell") or ""] if asset.get("source_urls") else []
    return {
        "existing_value": existing,
        "proposed_value": candidate["proposed_value"],
        "source": "excel_hyperlink" if asset.get("source_urls") else "none",
        "source_location": "|".join(value for value in locations if value),
        "provenance": "exact source URL; not yet human-approved" if asset.get("source_urls") else "no evidence",
        "confidence": candidate["confidence"],
        "conflict_status": candidate["conflict_status"],
        "review_required": candidate["review_required"],
        "reason": candidate["reason"],
        "proposed_decision": candidate["proposed_decision"],
    }


def _resolve_explicit_date(asset: dict, key: str, missing_reason: str) -> dict:
    value = _text(asset.get(key))
    if not value:
        return _missing_resolution(missing_reason)
    parsed = parse_asset_date(value)
    if parsed is None:
        return _invalid_resolution(value, f"invalid {key}")
    return _explicit_resolution(value, parsed, asset, "explicit_record_metadata", "high")


def _resolve_explicit_enum(asset: dict, key: str, allowed: Sequence[str], missing_reason: str) -> dict:
    value = _text(asset.get(key))
    if not value:
        return _missing_resolution(missing_reason, proposed_value="unknown")
    if value not in allowed:
        return _invalid_resolution(value, f"{key} is outside the allowed enum")
    return _explicit_resolution(value, value, asset, "explicit_metadata", "high")


def _explicit_resolution(existing: str, proposed: str, asset: dict, source: str, confidence: str) -> dict:
    return {
        "existing_value": existing,
        "proposed_value": proposed,
        "source": source,
        "source_location": f"{asset.get('source_sheet')}:r{asset.get('source_row')}",
        "provenance": "explicit field value; human approval still required",
        "confidence": confidence,
        "conflict_status": "none",
        "review_required": True,
        "reason": "explicit value available for review",
        "proposed_decision": "approve_candidate",
    }


def _missing_resolution(reason: str, *, existing_value: str = "", proposed_value: str = "") -> dict:
    return {
        "existing_value": existing_value,
        "proposed_value": proposed_value,
        "source": "none",
        "source_location": "",
        "provenance": "no authoritative evidence",
        "confidence": "none",
        "conflict_status": "missing_evidence",
        "review_required": False,
        "reason": reason,
        "proposed_decision": "needs_source",
    }


def _invalid_resolution(existing: str, reason: str) -> dict:
    return {
        "existing_value": existing,
        "proposed_value": "",
        "source": "explicit_metadata",
        "source_location": "",
        "provenance": "invalid source value preserved for review",
        "confidence": "none",
        "conflict_status": "invalid_value",
        "review_required": True,
        "reason": reason,
        "proposed_decision": "reject_candidate",
    }


def _xlsx_sheet_paths(archive: zipfile.ZipFile) -> Dict[str, str]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib["Id"]: _resolve_xlsx_target(item.attrib.get("Target", ""), "xl")
        for item in rels.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }
    result = {}
    for sheet in workbook.findall(f".//{{{WORKBOOK_NS}}}sheet"):
        rel_id = sheet.attrib.get(f"{{{REL_NS}}}id")
        if rel_id in targets:
            result[sheet.attrib["name"]] = targets[rel_id]
    return result


def _sheet_relationship_targets(archive: zipfile.ZipFile, sheet_path: str) -> Dict[str, str]:
    sheet = Path(sheet_path)
    rel_path = (sheet.parent / "_rels" / f"{sheet.name}.rels").as_posix()
    if rel_path not in archive.namelist():
        return {}
    root = ElementTree.fromstring(archive.read(rel_path))
    return {
        item.attrib["Id"]: item.attrib.get("Target", "")
        for item in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        if item.attrib.get("TargetMode") == "External"
    }


def _resolve_xlsx_target(target: str, base: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"{base}/{target}"


def _parse_cell_reference(value: str) -> Optional[Tuple[int, int]]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", value)
    if not match:
        return None
    column = 0
    for character in match.group(1):
        column = column * 26 + ord(character) - ord("A") + 1
    return column - 1, int(match.group(2))


def _excel_cell_for_asset(
    links: Mapping[Tuple[str, int, str], Sequence[str]], sheet: str, row: int, asset_type: str
) -> str:
    if (sheet, row, asset_type) not in links:
        return ""
    column = {"article": "H", "video": "I", "podcast": "J", "news": "K"}.get(asset_type, "")
    return f"{sheet}!{column}{row}" if column else f"{sheet}!r{row}:{asset_type}"


def _matching_record(records: Sequence[dict], title_field: str, title: str) -> dict:
    if not records:
        return {}
    exact = [record for record in records if _text(record.get(title_field)) == title and title]
    return exact[0] if len(exact) == 1 else (records[0] if len(records) == 1 else {})


def _explicit_asset_value(record: dict, vault_record: dict, key: str) -> str:
    values = _unique_text([record.get(key), vault_record.get(key) if vault_record else None])
    return values[0] if len(values) == 1 else ""


def _source_key(metadata: Mapping[str, object]) -> Optional[Tuple[str, int]]:
    sheet = _text(metadata.get("source_sheet"))
    row = _integer_or_none(metadata.get("source_row"))
    return (sheet, row) if sheet and row is not None else None


def _discover_workbook(preview_dir: Path) -> Optional[Path]:
    workbooks = sorted(
        path for path in preview_dir.glob("*.xlsx") if path.is_file() and not path.name.startswith("._")
    )
    if len(workbooks) > 1:
        raise AssetMetadataPreviewError("multiple workbooks found; pass --workbook explicitly")
    return workbooks[0] if workbooks else None


def _assert_safe_output(
    output_dir: Path,
    preview_dir: Path,
    vault_path: Optional[Path],
    db_path: Optional[Path],
) -> None:
    output = output_dir.resolve()
    if output == preview_dir.resolve():
        raise AssetMetadataPreviewError("output directory must not overwrite the Excel preview source")
    if vault_path is not None and _is_relative_to(output, Path(vault_path).resolve()):
        raise AssetMetadataPreviewError("output directory must not be inside the Obsidian Vault")
    if db_path is not None and output == Path(db_path).resolve():
        raise AssetMetadataPreviewError("output path must not overwrite the formal SQLite index")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _read_json_list(path: Path) -> List[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetMetadataPreviewError(f"unable to parse JSON preview: {path}") from exc
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise AssetMetadataPreviewError(f"preview must be a JSON array of objects: {path}")
    return payload


def _write_csv(path: Path, rows: Sequence[dict], columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in columns})


def _csv_value(value: object) -> object:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=isinstance(value, dict))
    return "" if value is None else value


def _unique_urls(values: Iterable[object]) -> List[str]:
    return _unique_text(values)


def _unique_text(values: Iterable[object]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _integer_or_none(value: object) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
