from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .governance import GovernanceIndex, RestrictedCustomerRecord, split_restricted_aliases
from .models import DocumentMetadata
from .record_identity_lineage import (
    APPLY_LINEAGE_FILENAME,
    apply_row_identity_surface_entries,
    assert_row_v1_lineage,
    build_apply_lineage_binding,
    resolve_preview_lineage,
)
from .review_decision_validation import validate_review_decisions
from .review_template import REVIEW_COLUMNS, build_expected_review_rows, load_preview_records


UNFINISHED_DECISIONS = {"needs_update", "enrich_metadata", "manual_review", "review_identity_mapping"}
CONTENT_RECORD_KEYS = ("merchant_cases", "public_metrics")
APPLY_RECORD_KEYS = ("merchant_cases", "public_metrics", "pending_metrics", "restricted_customers")
MARKDOWN_RECORD_DIRS = {
    "content_asset": "content_assets",
    "merchant_case": "merchant_cases",
    "public_metric": "public_metrics",
}


class ApplyReviewDecisionsError(ValueError):
    """Raised when review decisions cannot be applied into a preview output."""


def apply_review_decisions(
    decisions_path: Path,
    preview_dir: Path,
    output_dir: Path,
    include_clean_records: bool = False,
    include_clean_merchant_cases: bool = False,
) -> dict:
    decisions_path = Path(decisions_path)
    preview_dir = Path(preview_dir)
    output_dir = Path(output_dir)
    applied_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    # First formal boundary at which existing row-coordinate review decisions are joined to the
    # current workbook's records. Runs before every read and every write below, so a workbook the
    # decisions were not reviewed against fails closed with nothing mutated.
    preview_lineage = resolve_preview_lineage(preview_dir)
    assert_row_v1_lineage(preview_lineage, operation="apply-review-decisions")

    preview = load_preview_records(preview_dir)
    rows = _read_decision_rows(decisions_path)
    validation_summary, validation_warnings = _run_preflight_validation(decisions_path, preview_dir)
    if validation_summary["error_count"] > 0:
        rule_counts = validation_summary.get("rule_counts", {})
        if rule_counts.get("missing_expected_review_row") or rule_counts.get("unexpected_review_row"):
            raise ApplyReviewDecisionsError("row coverage mismatch; decision CSV must match preview expected review rows.")
        raise ApplyReviewDecisionsError("review decision validation has errors; refusing to apply preview.")
    if validation_summary["blank_reviewer_count"] > 0 or validation_summary["blank_reviewed_at_count"] > 0:
        raise ApplyReviewDecisionsError("請完成人工簽核後重試：reviewer / reviewed_at must be filled.")

    expected_keys = {_record_key(row) for row in build_expected_review_rows(preview)}
    actual_keys = {_record_key(row) for row in rows if all(_record_key(row))}
    if expected_keys != actual_keys:
        raise ApplyReviewDecisionsError("row coverage mismatch; decision CSV must match preview expected review rows.")

    previous_output_exists = output_dir.exists()
    previous_output_mtime = (
        datetime.fromtimestamp(output_dir.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()
        if previous_output_exists
        else None
    )

    preview_by_key = _preview_records_by_key(preview)
    rows_by_key = {_record_key(row): row for row in rows}
    plan = _build_apply_plan(
        preview=preview,
        preview_by_key=preview_by_key,
        rows=rows,
        rows_by_key=rows_by_key,
        decisions_path=decisions_path,
        applied_at=applied_at,
        include_clean_records=include_clean_records,
        include_clean_merchant_cases=include_clean_merchant_cases,
        validation_summary=validation_summary,
        validation_warnings=validation_warnings,
        previous_output_exists=previous_output_exists,
        previous_output_mtime=previous_output_mtime,
    )

    restricted_customers = plan["restricted_customer_records"]
    _assert_planned_safety(plan, restricted_customers)

    _rebuild_output_dir(output_dir)
    for relative_path, content in plan["text_files"].items():
        _write_text(output_dir, relative_path, content)
    for relative_path, payload in plan["json_files"].items():
        _write_json(output_dir, relative_path, payload)

    _write_json(
        output_dir,
        Path(APPLY_LINEAGE_FILENAME),
        build_apply_lineage_binding(
            preview_status=preview_lineage,
            surface_entries=apply_row_identity_surface_entries(output_dir),
            applied_at=applied_at,
            decisions_path=decisions_path,
        ),
    )

    assert_apply_preview_safety(output_dir, restricted_customers)
    return plan["summary"]


def assert_apply_preview_safety(output_dir: Path, restricted_customers: List[dict]) -> None:
    output_dir = Path(output_dir)
    governance_index = GovernanceIndex(
        [
            RestrictedCustomerRecord(
                brand_name=record.get("brand_name") or "",
                website_url=record.get("website_url"),
                merchant_handle=record.get("merchant_handle"),
                restricted_aliases=record.get("restricted_aliases") or split_restricted_aliases(record.get("brand_name")),
                source_sheet=record.get("source_sheet"),
                source_row=record.get("source_row"),
            )
            for record in restricted_customers
        ]
    )

    checked_paths = []
    for root in [output_dir / "approved_vault_preview", output_dir / "internal_inventory_preview"]:
        if not root.exists():
            continue
        for path in _preview_text_files(root):
            checked_paths.append(path)
            if governance_index.check_text(path.read_text(encoding="utf-8")).blocked:
                raise ApplyReviewDecisionsError(f"restricted whitelist assertion failed for {path}")

    approved_vault = output_dir / "approved_vault_preview"
    pending_count = 0
    if approved_vault.exists():
        for path in _preview_text_files(approved_vault):
            if path.suffix != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"^record_type:\s*[\"']?pending_metric[\"']?\s*$", text, flags=re.MULTILINE):
                pending_count += 1
    if pending_count:
        raise ApplyReviewDecisionsError("pending_metric must not appear in approved_vault_preview.")


def _run_preflight_validation(decisions_path: Path, preview_dir: Path) -> Tuple[dict, List[dict]]:
    with tempfile.TemporaryDirectory() as temp_dir:
        summary_path = Path(temp_dir) / "review_decisions_validation_summary.md"
        summary = validate_review_decisions(decisions_path, summary_path, preview_dir=preview_dir)
        warnings_path = Path(summary["warnings_output_path"])
        warnings = _read_issue_csv(warnings_path)
        return summary, warnings


def _build_apply_plan(
    preview: Dict[str, List[dict]],
    preview_by_key: Dict[Tuple[str, str, str], dict],
    rows: List[dict],
    rows_by_key: Dict[Tuple[str, str, str], dict],
    decisions_path: Path,
    applied_at: str,
    include_clean_records: bool,
    include_clean_merchant_cases: bool,
    validation_summary: dict,
    validation_warnings: List[dict],
    previous_output_exists: bool,
    previous_output_mtime: Optional[str],
) -> dict:
    text_files: Dict[Path, str] = {}
    json_files: Dict[Path, object] = {}
    assignments: List[dict] = []
    decision_overrides: List[dict] = []
    excluded_records: List[dict] = []
    unfinished_records: List[dict] = []
    deprecated_records: List[dict] = []
    not_reviewed_records: List[dict] = []
    default_policy_records: List[dict] = []
    clean_merchant_policy_records: List[dict] = []
    internal_pending_records: List[dict] = []
    governance_records: List[dict] = []
    markdown_records: List[dict] = []
    vault_only_records: List[dict] = []

    for row in rows:
        key = _record_key(row)
        record = preview_by_key.get(key)
        if record is None:
            raise ApplyReviewDecisionsError(f"unable to find preview record for decision row: {key}")
        decision = _text(row.get("review_decision"))
        override = _decision_override(row)
        if override:
            decision_overrides.append(override)

        if decision in {"approve", "approve_internal_only", "keep_all_records", "restricted_use_only"}:
            relative_path, content = _markdown_file_for_record(record, row, decisions_path, applied_at, decision)
            text_files[relative_path] = content
            markdown_records.append(record)
            assignments.append(_assignment(record, "vault"))
        elif decision == "exclude_from_content_index":
            relative_path, content = _markdown_file_for_record(record, row, decisions_path, applied_at, decision, vault_only=True)
            text_files[relative_path] = content
            vault_only_records.append(record)
            assignments.append(_assignment(record, "vault_only"))
        elif decision == "enter_governance_table_only":
            governance_record = _governance_record(record, row)
            governance_records.append(governance_record)
            assignments.append(_assignment(record, "governance"))
        elif decision == "keep_internal_only":
            if record.get("record_type") == "pending_metric":
                internal_pending_records.append({**record, "_review_row": row})
                assignments.append(_assignment(record, "internal"))
            else:
                relative_path, content = _markdown_file_for_record(
                    record,
                    row,
                    decisions_path,
                    applied_at,
                    decision,
                    vault_only=True,
                    internal_only=True,
                )
                text_files[relative_path] = content
                vault_only_records.append(record)
                assignments.append(_assignment(record, "vault_only"))
        elif decision == "exclude":
            excluded_records.append({**record, "_review_row": row})
            assignments.append(_assignment(record, "excluded"))
        elif decision in UNFINISHED_DECISIONS:
            unfinished_records.append({**record, "_review_row": row})
            assignments.append(_assignment(record, "unfinished"))
        elif decision == "deprecated":
            deprecated_records.append({**record, "_review_row": row})
            assignments.append(_assignment(record, "deprecated"))
        else:
            raise ApplyReviewDecisionsError(f"unsupported review_decision: {decision}")

    for record in _not_reviewed_records(preview, rows_by_key):
        broad_policy_match = include_clean_records and record.get("record_type") in {
            "merchant_case",
            "public_metric",
            "content_asset",
        }
        clean_merchant_policy_match = include_clean_merchant_cases and is_clean_merchant_case_policy_eligible(record)
        if broad_policy_match or clean_merchant_policy_match:
            policy_flag = "--include-clean-records" if broad_policy_match else "--include-clean-merchant-cases"
            row = _default_policy_row(record, policy_flag)
            relative_path, content = _markdown_file_for_record(record, row, decisions_path, applied_at, "approve(default)")
            text_files[relative_path] = content
            default_policy_records.append(record)
            if clean_merchant_policy_match and not broad_policy_match:
                clean_merchant_policy_records.append(record)
            assignments.append(_assignment(record, "vault"))
        else:
            not_reviewed_records.append(record)
            assignments.append(_assignment(record, "not_reviewed"))

    json_files[Path("governance_table_preview/restricted_customers.json")] = governance_records
    text_files[Path("governance_table_preview/governance_table_summary.md")] = _render_governance_summary(governance_records)
    text_files[Path("internal_inventory_preview/pending_metrics.md")] = _render_pending_metrics(internal_pending_records)
    text_files[Path("excluded_records.md")] = _render_excluded_records(
        excluded_records,
        unfinished_records,
        deprecated_records,
    )
    text_files[Path("not_reviewed_records.md")] = _render_not_reviewed_records(not_reviewed_records)

    summary = _build_apply_summary(
        preview=preview,
        assignments=assignments,
        validation_summary=validation_summary,
        validation_warnings=validation_warnings,
        previous_output_exists=previous_output_exists,
        previous_output_mtime=previous_output_mtime,
        include_clean_records=include_clean_records,
        include_clean_merchant_cases=include_clean_merchant_cases,
        decision_overrides=decision_overrides,
        governance_records=governance_records,
        internal_pending_records=internal_pending_records,
        excluded_records=excluded_records,
        unfinished_records=unfinished_records,
        deprecated_records=deprecated_records,
        not_reviewed_records=not_reviewed_records,
        markdown_records=markdown_records,
        vault_only_records=vault_only_records,
        default_policy_records=default_policy_records,
        clean_merchant_policy_records=clean_merchant_policy_records,
    )
    text_files[Path("apply_decisions_summary.md")] = _render_apply_summary(summary)

    return {
        "text_files": text_files,
        "json_files": json_files,
        "summary": summary,
        "restricted_customer_records": governance_records,
    }


def _read_decision_rows(path: Path) -> List[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def _read_issue_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _preview_records_by_key(preview: Dict[str, List[dict]]) -> Dict[Tuple[str, str, str], dict]:
    records = {}
    for key in APPLY_RECORD_KEYS:
        for record in preview[key]:
            records[_record_key(record)] = record
    return records


def _record_key(row: dict) -> Tuple[str, str, str]:
    return (_text(row.get("source_sheet")), _text(row.get("source_row")), _text(row.get("record_type")))


def _not_reviewed_records(preview: Dict[str, List[dict]], rows_by_key: Dict[Tuple[str, str, str], dict]) -> List[dict]:
    records = []
    for key in CONTENT_RECORD_KEYS:
        for record in preview[key]:
            if _record_key(record) not in rows_by_key:
                records.append(record)
    return records


def is_clean_merchant_case_policy_eligible(record: dict) -> bool:
    has_valid_asset = any(
        _text(record.get(field))
        for field in ("article_title", "video_title", "podcast_title", "news_title")
    )
    blocked_markers = (
        record.get("governance_issue_types"),
        record.get("governance_risk_reasons"),
        record.get("governance_risk_fields"),
        record.get("invalid_asset_fields"),
        record.get("invalid_asset_values"),
    )
    return all(
        [
            record.get("record_type") == "merchant_case",
            _text(record.get("status")) == "published",
            _text(record.get("merchant_status")) == "現有商家",
            bool(_text(record.get("brand_name"))),
            bool(_text(record.get("merchant_handle"))),
            has_valid_asset,
            record.get("data_classification") == "public",
            record.get("can_quote_externally") is True,
            record.get("can_enter_content_index") is True,
            record.get("no_valid_content_asset") is not True,
            not any(blocked_markers),
            record.get("same_brand_multiple_records") is not True,
            record.get("same_handle_multiple_records") is not True,
            record.get("multi_interview_record") is not True,
            record.get("suspected_duplicate_review") is not True,
        ]
    )


def _markdown_file_for_record(
    record: dict,
    row: dict,
    decisions_path: Path,
    applied_at: str,
    decision: str,
    vault_only: bool = False,
    internal_only: bool = False,
) -> Tuple[Path, str]:
    payload = dict(record)
    if decision == "approve_internal_only" or internal_only:
        payload["can_quote_externally"] = False
        payload["data_classification"] = "internal"
    if vault_only or decision == "exclude_from_content_index":
        payload["can_enter_content_index"] = False

    metadata = DocumentMetadata(**payload)
    frontmatter = metadata.metadata_dict()
    frontmatter.update(
        {
            "review_decision": decision,
            "reviewer": _text(row.get("reviewer")),
            "reviewed_at": _text(row.get("reviewed_at")),
            "review_notes": _text(row.get("notes")) or None,
            "applied_at": applied_at,
            "decision_source": str(decisions_path),
            "source_sheet": record.get("source_sheet"),
            "source_row": record.get("source_row"),
        }
    )
    content = _record_body(metadata, decision)
    directory = Path("approved_vault_preview")
    if vault_only:
        directory = directory / "_vault_only"
    else:
        directory = directory / MARKDOWN_RECORD_DIRS.get(metadata.record_type, f"{metadata.record_type}s")
    filename = _record_filename(metadata, record)
    return directory / filename, _render_markdown(frontmatter, content)


def _record_body(metadata: DocumentMetadata, decision: str) -> str:
    lines = [f"# {metadata.title}", ""]
    if decision == "approve_internal_only":
        lines.extend(["> Internal-only. Do not quote externally.", ""])
    if decision == "restricted_use_only":
        lines.extend(["> Restricted use. Check allowed exposure channels and notes before reuse.", ""])

    if metadata.record_type == "merchant_case":
        lines.extend(
            [
                "## Content Assets",
                "",
                "| Asset | Title |",
                "| --- | --- |",
                f"| Article | {metadata.article_title or ''} |",
                f"| Video | {metadata.video_title or ''} |",
                f"| Podcast | {metadata.podcast_title or ''} |",
                f"| News | {metadata.news_title or ''} |",
                "",
                "## Notes",
                "",
                metadata.notes or "",
            ]
        )
    elif metadata.record_type == "public_metric":
        channels = ", ".join(metadata.allowed_exposure_channels) or "None"
        lines.extend(
            [
                "## Claim",
                "",
                metadata.claim_statement or "",
                "",
                "## Metric Note",
                "",
                metadata.metric_note or "",
                "",
                "## Allowed Exposure Channels",
                "",
                channels,
            ]
        )
    else:
        lines.extend(["## Record", "", metadata.notes or metadata.claim_statement or ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_markdown(frontmatter: dict, body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.extend(_yaml_lines(key, value))
    lines.extend(["---", "", body])
    return "\n".join(lines)


def _yaml_lines(key: str, value) -> List[str]:
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        return [f"{key}:"] + [f"  - {json.dumps(item, ensure_ascii=False)}" for item in value]
    if isinstance(value, dict):
        if not value:
            return [f"{key}: {{}}"]
        return [f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"]
    return [f"{key}: {json.dumps(value, ensure_ascii=False)}"]


def _record_filename(metadata: DocumentMetadata, record: dict) -> str:
    label = metadata.brand_name or metadata.metric_name or metadata.title or metadata.record_type
    sheet_slug = _slug(record.get("source_sheet") or "sheet")
    label_slug = _slug(label)
    return f"{sheet_slug}-r{record.get('source_row')}-{label_slug}.md"


def _slug(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return normalized or "record"


def _governance_record(record: dict, row: dict) -> dict:
    restricted = RestrictedCustomerRecord(
        brand_name=record.get("brand_name") or "",
        website_url=record.get("website_url"),
        merchant_handle=record.get("merchant_handle"),
        restricted_aliases=record.get("restricted_aliases") or split_restricted_aliases(record.get("brand_name")),
        source_sheet=record.get("source_sheet"),
        source_row=record.get("source_row"),
    )
    return {
        "brand_name": record.get("brand_name"),
        "match_terms": restricted.normalized_terms,
        "restricted_aliases": record.get("restricted_aliases") or split_restricted_aliases(record.get("brand_name")),
        "website_url": record.get("website_url"),
        "merchant_handle": record.get("merchant_handle"),
        "restricted_reason": record.get("restricted_reason"),
        "nda_signed": record.get("nda_signed"),
        "source_sheet": record.get("source_sheet"),
        "source_row": record.get("source_row"),
        "reviewer": _text(row.get("reviewer")),
        "reviewed_at": _text(row.get("reviewed_at")),
        "denylist_status": "active",
    }


def _render_governance_summary(records: List[dict]) -> str:
    lines = ["# Governance Table Preview", "", f"- Restricted customers: {len(records)}", ""]
    lines.extend(["## Match Terms", ""])
    if records:
        for record in records:
            short_terms = [term for term in record["match_terms"] if len(term) < 4]
            lines.append(
                f"- row {record.get('source_row')}: match_terms={', '.join(record['match_terms']) or 'None'}; "
                f"short_terms={', '.join(short_terms) or 'None'}"
            )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _render_pending_metrics(records: List[dict]) -> str:
    lines = [
        "# Pending Metrics Internal Inventory",
        "",
        "| metric_name | claim_statement | metric_note | source row | next step |",
        "| --- | --- | --- | ---: | --- |",
    ]
    if records:
        for record in records:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(record.get("metric_name")),
                        _md_cell(record.get("claim_statement")),
                        _md_cell(record.get("metric_note")),
                        _md_cell(record.get("source_row")),
                        "review and approve before external use",
                    ]
                )
                + " |"
            )
    else:
        lines.append("| None |  |  |  |  |")
    return "\n".join(lines) + "\n"


def _render_excluded_records(excluded: List[dict], unfinished: List[dict], deprecated: List[dict]) -> str:
    lines = ["# Excluded Records", "", "## Excluded", ""]
    lines.extend(_record_list(excluded))
    lines.extend(["", "## 未完成審核", ""])
    lines.extend(_record_list(unfinished))
    lines.extend(["", "## deprecated", ""])
    lines.extend(_record_list(deprecated))
    return "\n".join(lines) + "\n"


def _render_not_reviewed_records(records: List[dict]) -> str:
    lines = ["# Not Reviewed Records", "", "These records have no review row and are excluded from vault preview by default.", ""]
    lines.extend(_record_list(records))
    return "\n".join(lines) + "\n"


def _record_list(records: List[dict]) -> List[str]:
    if not records:
        return ["- None"]
    return [
        f"- {record.get('record_type')} row {record.get('source_row')}: {_record_label(record)}"
        for record in records
    ]


def _record_label(record: dict) -> str:
    return _text(record.get("brand_name") or record.get("metric_name") or record.get("title") or "(untitled)")


def _build_apply_summary(
    preview: Dict[str, List[dict]],
    assignments: List[dict],
    validation_summary: dict,
    validation_warnings: List[dict],
    previous_output_exists: bool,
    previous_output_mtime: Optional[str],
    include_clean_records: bool,
    include_clean_merchant_cases: bool,
    decision_overrides: List[dict],
    governance_records: List[dict],
    internal_pending_records: List[dict],
    excluded_records: List[dict],
    unfinished_records: List[dict],
    deprecated_records: List[dict],
    not_reviewed_records: List[dict],
    markdown_records: List[dict],
    vault_only_records: List[dict],
    default_policy_records: List[dict],
    clean_merchant_policy_records: List[dict],
) -> dict:
    bucket_counts = {
        "approved_vault_preview_md": len(markdown_records) + len(default_policy_records),
        "vault_only_md": len(vault_only_records),
        "governance_restricted_customers": len(governance_records),
        "internal_pending_metrics": len(internal_pending_records),
        "excluded": len(excluded_records),
        "unfinished_review": len(unfinished_records),
        "deprecated": len(deprecated_records),
        "not_reviewed": len(not_reviewed_records),
        "default_policy_approved": len(default_policy_records),
        "clean_merchant_policy_approved": len(clean_merchant_policy_records),
    }
    conservation = _conservation(preview, assignments)
    return {
        "preflight": {
            "validation_error_count": validation_summary["error_count"],
            "validation_warning_count": validation_summary["warning_count"],
            "reviewer_complete": validation_summary["blank_reviewer_count"] == 0
            and validation_summary["blank_reviewed_at_count"] == 0,
            "row_coverage_ok": validation_summary["error_count"] == 0,
            "previous_output_exists": previous_output_exists,
            "previous_output_mtime": previous_output_mtime,
        },
        "bucket_counts": bucket_counts,
        "handle_mapping_count": len(preview["handle_mappings"]),
        "conservation": conservation,
        "validation_warnings": validation_warnings,
        "decision_overrides": decision_overrides,
        "multi_record_excluded": _multi_record_excluded(excluded_records + deprecated_records + vault_only_records),
        "include_clean_records": include_clean_records,
        "include_clean_merchant_cases": include_clean_merchant_cases,
        "preview_notice": "本輸出為 preview。Obsidian 未同步、正式 index 未建立。下一步需人工確認後另行執行 sync（尚未實作）。",
    }


def _conservation(preview: Dict[str, List[dict]], assignments: List[dict]) -> dict:
    expected = {record_type: len(records) for record_type, records in _records_by_type(preview).items()}
    assigned_by_type: Dict[str, Counter] = defaultdict(Counter)
    for item in assignments:
        assigned_by_type[item["record_type"]][item["bucket"]] += 1
    equations = {}
    ok = True
    for record_type, total in sorted(expected.items()):
        buckets = dict(sorted(assigned_by_type[record_type].items()))
        assigned_total = sum(buckets.values())
        equations[record_type] = {
            "total": total,
            "buckets": buckets,
            "assigned_total": assigned_total,
            "ok": total == assigned_total,
        }
        ok = ok and total == assigned_total
    return {"ok": ok, "equations": equations}


def _records_by_type(preview: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    records: Dict[str, List[dict]] = defaultdict(list)
    for key in APPLY_RECORD_KEYS:
        for record in preview[key]:
            records[record.get("record_type")].append(record)
    return records


def _assignment(record: dict, bucket: str) -> dict:
    return {"record_type": record.get("record_type"), "source_row": record.get("source_row"), "bucket": bucket}


def _decision_override(row: dict) -> Optional[dict]:
    decision = _text(row.get("review_decision"))
    overridden = []
    if decision in {"exclude", "deprecated", *UNFINISHED_DECISIONS}:
        for field in ("can_enter_vault", "can_enter_content_index", "can_quote_externally"):
            if _bool_value(row.get(field)) is True:
                overridden.append(field)
    elif decision == "exclude_from_content_index" and _bool_value(row.get("can_enter_content_index")) is True:
        overridden.append("can_enter_content_index")
    elif decision == "enter_governance_table_only":
        for field in ("can_enter_vault", "can_enter_content_index", "can_quote_externally"):
            if _bool_value(row.get(field)) is True:
                overridden.append(field)
    elif decision in {"approve_internal_only", "keep_internal_only"} and _bool_value(row.get("can_quote_externally")) is True:
        overridden.append("can_quote_externally")
    if not overridden:
        return None
    return {
        "source_sheet": row.get("source_sheet"),
        "source_row": row.get("source_row"),
        "record_type": row.get("record_type"),
        "review_decision": decision,
        "overridden_fields": overridden,
    }


def _multi_record_excluded(records: List[dict]) -> List[dict]:
    flagged = []
    for record in records:
        if record.get("same_brand_multiple_records") or record.get("same_handle_multiple_records"):
            flagged.append(
                {
                    "source_sheet": record.get("source_sheet"),
                    "source_row": record.get("source_row"),
                    "record_type": record.get("record_type"),
                }
            )
    return flagged


def _render_apply_summary(summary: dict) -> str:
    lines = [
        "# Apply Review Decisions Preview Summary",
        "",
        summary["preview_notice"],
        "",
        "## Preflight",
        "",
        f"- Validation errors: {summary['preflight']['validation_error_count']}",
        f"- Validation warnings: {summary['preflight']['validation_warning_count']}",
        f"- Reviewer complete: {_yes_no(summary['preflight']['reviewer_complete'])}",
        f"- Row coverage ok: {_yes_no(summary['preflight']['row_coverage_ok'])}",
        f"- Previous output existed: {_yes_no(summary['preflight']['previous_output_exists'])}",
        f"- Previous output mtime: {summary['preflight']['previous_output_mtime'] or 'None'}",
        "",
        "## Bucket Counts",
        "",
    ]
    for key, value in summary["bucket_counts"].items():
        lines.append(f"- `{key}`: {value}")
    lines.append(f"- `handle_mapping_count`: {summary['handle_mapping_count']}")
    lines.extend(["", "## Conservation", ""])
    for record_type, equation in summary["conservation"]["equations"].items():
        bucket_text = " + ".join(f"{count}({bucket})" for bucket, count in equation["buckets"].items()) or "0"
        lines.append(f"- `{record_type}`: {equation['total']} = {bucket_text}; ok={_yes_no(equation['ok'])}")
    lines.extend(["", "## Whitelist Assertions", "", f"- Conservation ok: {_yes_no(summary['conservation']['ok'])}", "- Restricted whitelist assertion: passed", "- Pending metric vault assertion: passed"])
    lines.extend(["", "## Validation Warnings", ""])
    if summary["validation_warnings"]:
        for warning in summary["validation_warnings"]:
            lines.append(f"- row {warning.get('row_number')}: {warning.get('rule_id')} {warning.get('message')}")
    else:
        lines.append("- None")
    lines.extend(["", "## Decision Overrides", ""])
    if summary["decision_overrides"]:
        for override in summary["decision_overrides"]:
            lines.append(
                f"- row {override['source_row']} {override['record_type']} decision={override['review_decision']} "
                f"overrides={', '.join(override['overridden_fields'])}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Multi-record Excluded", ""])
    if summary["multi_record_excluded"]:
        for item in summary["multi_record_excluded"]:
            lines.append(f"- row {item['source_row']} {item['record_type']}")
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _default_policy_row(record: dict, policy_flag: str = "--include-clean-records") -> dict:
    notes = "Included by --include-clean-records default policy."
    if policy_flag == "--include-clean-merchant-cases":
        notes = "Included by --include-clean-merchant-cases policy."
    return {
        "review_decision": "approve(default)",
        "reviewer": "default_policy",
        "reviewed_at": datetime.now(timezone.utc).date().isoformat(),
        "notes": notes,
    }


def _assert_planned_safety(plan: dict, restricted_customers: List[dict]) -> None:
    governance_index = GovernanceIndex(
        [
            RestrictedCustomerRecord(
                brand_name=record.get("brand_name") or "",
                website_url=record.get("website_url"),
                merchant_handle=record.get("merchant_handle"),
                restricted_aliases=record.get("restricted_aliases") or split_restricted_aliases(record.get("brand_name")),
                source_sheet=record.get("source_sheet"),
                source_row=record.get("source_row"),
            )
            for record in restricted_customers
        ]
    )
    for relative_path, content in plan["text_files"].items():
        if str(relative_path).startswith(("approved_vault_preview/", "internal_inventory_preview/")):
            if governance_index.check_text(content).blocked:
                raise ApplyReviewDecisionsError(f"restricted whitelist assertion failed for {relative_path}")
    for relative_path, content in plan["text_files"].items():
        if str(relative_path).startswith("approved_vault_preview/") and re.search(
            r"^record_type:\s*[\"']?pending_metric[\"']?\s*$",
            content,
            flags=re.MULTILINE,
        ):
            raise ApplyReviewDecisionsError("pending_metric must not appear in approved_vault_preview.")
    if not plan["summary"]["conservation"]["ok"]:
        raise ApplyReviewDecisionsError("conservation check failed; refusing to write partial preview.")


def _write_text(output_root: Path, relative_path: Path, content: str) -> Path:
    path = _safe_output_path(output_root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(output_root: Path, relative_path: Path, payload: object) -> Path:
    return _write_text(output_root, relative_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _safe_output_path(output_root: Path, relative_path: Path) -> Path:
    root = Path(output_root).resolve()
    path = (root / relative_path).resolve()
    if not _is_relative_to(path, root):
        raise ApplyReviewDecisionsError(f"refusing to write outside output directory: {path}")
    return path


def _rebuild_output_dir(output_dir: Path) -> None:
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
        if output_dir.exists():
            _remove_remaining_output_files(output_dir)
        if output_dir.exists():
            remaining = [path for path in output_dir.rglob("*") if not path.name.startswith("._")]
            if remaining:
                raise ApplyReviewDecisionsError(f"unable to rebuild non-empty output directory: {output_dir}")
            shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)


def _remove_remaining_output_files(output_dir: Path) -> None:
    for path in sorted(output_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except FileNotFoundError:
            continue


def _preview_text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json"} and not path.name.startswith("._"):
            yield path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _bool_value(value) -> Optional[bool]:
    text = _text(value).lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _md_cell(value) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
