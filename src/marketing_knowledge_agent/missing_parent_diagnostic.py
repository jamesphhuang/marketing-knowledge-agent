from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .asset_metadata_preview import read_sqlite_metadata, read_vault_metadata
from .governance import (
    GovernanceIndex,
    RestrictedCustomerRecord,
    split_restricted_aliases,
)


DIAGNOSTIC_OUTPUT_FILENAMES = (
    "missing_parent_diagnostic_summary.md",
    "missing_parent_records.csv",
    "orphan_assets.csv",
    "missing_parent_classification.csv",
    "missing_parent_recommended_actions.csv",
    "missing_parent_governance_evidence.md",
    "missing_parent_tag_validation.csv",
)
APPROVED_PARENT_DECISIONS = {"approve", "keep_all_records"}
EXCLUDED_PARENT_DECISIONS = {
    "exclude",
    "deprecated",
    "exclude_from_content_index",
}
INTERNAL_PARENT_DECISIONS = {
    "approve_internal_only",
    "keep_internal_only",
    "restricted_use_only",
    "enter_governance_table_only",
}
UNFINISHED_PARENT_DECISIONS = {
    "needs_update",
    "enrich_metadata",
    "manual_review",
    "review_identity_mapping",
}
CLASSIFICATION_CODES = {
    "approved_but_not_synced": "A",
    "intentionally_excluded": "B",
    "restricted_or_internal_only": "C",
    "parent_mapping_error": "D",
    "requires_human_review": "E",
}


class MissingParentDiagnosticError(ValueError):
    """Raised when missing formal parents cannot be diagnosed read-only."""


def diagnose_missing_formal_parents(
    *,
    join_validation_path: Path,
    apply_preview_path: Path,
    parent_records_path: Path,
    review_decisions_path: Path,
    restricted_customers_path: Path,
    vault_path: Path,
    db_path: Path,
    output_dir: Path,
) -> dict:
    paths = {
        "join_validation": Path(join_validation_path),
        "apply_preview": Path(apply_preview_path),
        "parent_records": Path(parent_records_path),
        "review_decisions": Path(review_decisions_path),
        "restricted_customers": Path(restricted_customers_path),
        "formal_vault": Path(vault_path),
        "formal_sqlite": Path(db_path),
    }
    output_dir = Path(output_dir)
    _assert_safe_output(output_dir, list(paths.values()))
    for name, path in paths.items():
        if not path.exists():
            raise MissingParentDiagnosticError(
                f"required diagnostic input does not exist: {name}"
            )
    before_hashes = {name: _hash_path(path) for name, path in paths.items()}

    join_rows = _read_csv(paths["join_validation"])
    apply_rows = _read_csv(paths["apply_preview"])
    parent_rows = _read_json_list(paths["parent_records"])
    decision_rows = _read_csv(paths["review_decisions"])
    restricted_index = _restricted_index(paths["restricted_customers"])

    missing_record_ids = sorted(
        {
            _text(row.get("record_id"))
            for row in join_rows
            if _text(row.get("formal_vault_parent_status")) != "unique_match"
            or _text(row.get("formal_sqlite_parent_status")) != "unique_match"
        }
        - {""}
    )
    join_assets = _missing_join_assets(join_rows, set(missing_record_ids))
    apply_assets = _asset_rows(apply_rows, set(missing_record_ids))
    preview_parents, duplicate_preview_ids = _parent_index(parent_rows)
    decisions, duplicate_decision_ids = _decision_index(decision_rows)
    vault_parents = _formal_parent_index(read_vault_metadata(paths["formal_vault"]))
    sqlite_parents = _formal_parent_index(read_sqlite_metadata(paths["formal_sqlite"]))

    parent_reports = []
    classifications = []
    recommendations = []
    tag_rows = []
    orphan_rows = []
    for record_id in missing_record_ids:
        parent = preview_parents.get(record_id)
        decision = decisions.get(record_id)
        assets = apply_assets.get(record_id, {})
        mapping_error = (
            parent is None
            or record_id in duplicate_preview_ids
            or record_id in duplicate_decision_ids
            or set(assets) != join_assets.get(record_id, set())
        )
        restricted_match = _restricted_match(parent, restricted_index)
        evidence = _classification_evidence(
            record_id,
            parent,
            decision,
            restricted_match=restricted_match,
            mapping_error=mapping_error,
        )
        classification, action = _classify(evidence)
        classification_code = CLASSIFICATION_CODES[classification]
        governance_status = _governance_status(evidence, classification)
        vault_exists = record_id in vault_parents
        sqlite_exists = record_id in sqlite_parents
        missing_reason = _missing_reason(
            vault_exists,
            sqlite_exists,
            mapping_error,
            decision,
        )
        tag = _tag_validation(record_id, parent, classification, evidence)
        tag_rows.append(tag)
        asset_review_status = _asset_review_status(assets)

        source_sheet, source_row = _split_record_id(record_id)
        parent_report = {
            "record_id": record_id,
            "brand_name": _text(parent.get("brand_name")) if parent else "",
            "merchant_handle": _text(parent.get("merchant_handle")) if parent else "",
            "source_sheet": source_sheet,
            "source_row": source_row,
            "classification": classification,
            "classification_code": classification_code,
            "review_decision": _text(decision.get("review_decision")) if decision else "",
            "can_enter_content_index": _display_bool(evidence["can_enter_content_index"]),
            "can_enter_vault": _display_bool(evidence["can_enter_vault"]),
            "can_quote_externally": _display_bool(evidence["can_quote_externally"]),
            "governance_status": governance_status,
            "data_classification": evidence["data_classification"],
            "restricted_status": str(evidence["restricted"]).lower(),
            "pending_status": str(evidence["pending"]).lower(),
            "internal_status": str(evidence["internal"]).lower(),
            "managed_vault_exists": str(vault_exists).lower(),
            "formal_sqlite_exists": str(sqlite_exists).lower(),
            "missing_reason": missing_reason,
            "orphan_asset_count": len(assets),
            "asset_url_review_status": asset_review_status,
        }
        parent_reports.append(parent_report)
        classifications.append(
            {
                "record_id": record_id,
                "brand_name": parent_report["brand_name"],
                "classification": classification,
                "classification_code": classification_code,
                "evidence": _classification_reason(evidence, classification),
                "review_decision": parent_report["review_decision"],
                "governance_status": governance_status,
            }
        )
        recommendations.append(
            {
                "record_id": record_id,
                "brand_name": parent_report["brand_name"],
                "recommended_action": action,
                "sync_gate_passed": str(action == "sync_parent").lower(),
                "reason": _recommendation_reason(classification, evidence),
                "risk": _recommendation_risk(classification),
            }
        )
        for asset_id, asset in sorted(assets.items()):
            orphan_rows.append(
                {
                    "record_id": record_id,
                    "brand_name": parent_report["brand_name"],
                    "asset_id": asset_id,
                    "asset_type": asset["asset_type"],
                    "asset_title": asset["asset_title"],
                    "asset_url": asset["asset_url"],
                    "canonical_url": asset["canonical_url"],
                    "asset_url_review_status": asset["review_status"],
                    "parent_classification": classification,
                    "parent_classification_code": classification_code,
                    "recommended_action": action,
                }
            )

    if set(missing_record_ids) != {row["record_id"] for row in parent_reports}:
        raise MissingParentDiagnosticError(
            "missing parent conservation failed during diagnosis"
        )
    if sum(row["orphan_asset_count"] for row in parent_reports) != len(orphan_rows):
        raise MissingParentDiagnosticError(
            "orphan asset conservation failed during diagnosis"
        )

    after_hashes = {name: _hash_path(path) for name, path in paths.items()}
    source_files_modified = before_hashes != after_hashes
    if source_files_modified:
        raise MissingParentDiagnosticError(
            "a protected source changed during read-only diagnosis"
        )

    classification_counts = Counter(row["classification"] for row in classifications)
    action_counts = Counter(row["recommended_action"] for row in recommendations)
    summary = {
        "diagnostic_only": True,
        "missing_parent_count": len(parent_reports),
        "orphan_asset_count": len(orphan_rows),
        "classification_counts": dict(sorted(classification_counts.items())),
        "recommended_action_counts": dict(sorted(action_counts.items())),
        "mapping_error_count": classification_counts["parent_mapping_error"],
        "restricted_or_internal_count": classification_counts[
            "restricted_or_internal_only"
        ],
        "sync_parent_count": action_counts["sync_parent"],
        "safe_tag_parent_count": sum(
            row["safe_for_external_use"] == "true" for row in tag_rows
        ),
        "formal_vault_modified": False,
        "formal_sqlite_modified": False,
        "source_files_modified": source_files_modified,
        "repairs_applied": False,
        "sync_executed": False,
        "confirm_executed": False,
        "execute_executed": False,
        "apply_plan_generated": False,
    }
    _write_reports(
        output_dir,
        summary,
        parent_reports,
        orphan_rows,
        classifications,
        recommendations,
        tag_rows,
    )
    final_hashes = {name: _hash_path(path) for name, path in paths.items()}
    if final_hashes != before_hashes:
        raise MissingParentDiagnosticError(
            "a protected source changed while writing diagnostic reports"
        )
    return summary


def _classification_evidence(
    record_id: str,
    parent: Optional[Mapping[str, object]],
    decision: Optional[Mapping[str, object]],
    *,
    restricted_match: bool,
    mapping_error: bool,
) -> dict:
    review_decision = _text(decision.get("review_decision")) if decision else ""
    data_classification = (
        _text(parent.get("data_classification")) if parent else "unknown"
    ).casefold()
    can_vault = _optional_bool(decision.get("can_enter_vault")) if decision else None
    can_index = (
        _optional_bool(decision.get("can_enter_content_index"))
        if decision
        else _optional_bool(parent.get("can_enter_content_index")) if parent else None
    )
    can_quote = (
        _optional_bool(decision.get("can_quote_externally"))
        if decision
        else _optional_bool(parent.get("can_quote_externally")) if parent else None
    )
    record_type = _text(parent.get("record_type")) if parent else ""
    restricted = restricted_match or data_classification == "restricted" or record_type == "restricted_customer"
    pending = record_type == "pending_metric" or "pending" in data_classification
    internal = (
        data_classification == "internal"
        or review_decision in INTERNAL_PARENT_DECISIONS
    )
    governance_issues = _string_list(parent.get("governance_issue_types")) if parent else []
    governance_reasons = _string_list(parent.get("governance_risk_reasons")) if parent else []
    return {
        "record_id": record_id,
        "review_decision": review_decision,
        "data_classification": data_classification or "unknown",
        "can_enter_vault": can_vault,
        "can_enter_content_index": can_index,
        "can_quote_externally": can_quote,
        "restricted": restricted,
        "pending": pending,
        "internal": internal,
        "mapping_error": mapping_error,
        "governance_issues": governance_issues,
        "governance_reasons": governance_reasons,
    }


def _classify(evidence: Mapping[str, object]) -> Tuple[str, str]:
    if evidence["mapping_error"]:
        return "parent_mapping_error", "remap_child_assets"
    if evidence["restricted"] or evidence["pending"] or evidence["internal"]:
        return "restricted_or_internal_only", "keep_blocked"
    decision = _text(evidence["review_decision"])
    if decision in EXCLUDED_PARENT_DECISIONS or evidence["can_enter_vault"] is False or evidence["can_enter_content_index"] is False:
        return "intentionally_excluded", "exclude_child_assets"
    sync_gate = (
        decision in APPROVED_PARENT_DECISIONS
        and evidence["can_enter_vault"] is True
        and evidence["can_enter_content_index"] is True
        and evidence["can_quote_externally"] is True
        and not evidence["governance_issues"]
        and not evidence["governance_reasons"]
    )
    if sync_gate:
        return "approved_but_not_synced", "sync_parent"
    return "requires_human_review", "manual_review"


def _governance_status(
    evidence: Mapping[str, object], classification: str
) -> str:
    if evidence["restricted"]:
        return "restricted"
    if evidence["pending"]:
        return "pending"
    if evidence["internal"]:
        return "internal_only"
    if classification == "intentionally_excluded":
        return "excluded"
    if classification == "approved_but_not_synced":
        return "approved"
    if classification == "parent_mapping_error":
        return "mapping_invalid"
    return "review_incomplete"


def _classification_reason(evidence: Mapping[str, object], classification: str) -> str:
    if classification == "approved_but_not_synced":
        return "Parent review is approved; Vault/index gates are true; no restricted/internal/governance blocker was found."
    if classification == "intentionally_excluded":
        return "Parent review or can_enter_content_index explicitly excludes the record."
    if classification == "restricted_or_internal_only":
        return "Parent is explicitly restricted, pending, or internal-only."
    if classification == "parent_mapping_error":
        return "Preview parent identity is missing, duplicated, or inconsistent with child asset mapping."
    return "Parent decision is unfinished, missing, or does not pass every sync gate."


def _recommendation_reason(classification: str, evidence: Mapping[str, object]) -> str:
    reasons = {
        "approved_but_not_synced": "Run the existing reviewed parent sync/index workflow only after a new explicit approval.",
        "intentionally_excluded": "Keep the parent out of formal content and remove child assets from future Apply eligibility through human review.",
        "restricted_or_internal_only": "Preserve governance isolation; do not place parent or child assets in general retrieval.",
        "parent_mapping_error": "Correct record_id mapping through human-reviewed source identity before any Apply planning.",
        "requires_human_review": "Complete the parent review decision or metadata enrichment before deciding sync versus exclusion.",
    }
    return reasons[classification]


def _recommendation_risk(classification: str) -> str:
    risks = {
        "approved_but_not_synced": "Syncing without checksum and duplicate-parent checks could create stale or duplicate formal records.",
        "intentionally_excluded": "Keeping child assets eligible would bypass the parent exclusion decision.",
        "restricted_or_internal_only": "Syncing to general retrieval could expose non-public governance data.",
        "parent_mapping_error": "Applying before remapping could attach URLs and citations to the wrong parent.",
        "requires_human_review": "Treating an unfinished decision as approval would bypass manual governance review.",
    }
    return risks[classification]


def _tag_validation(
    record_id: str,
    parent: Optional[Mapping[str, object]],
    classification: str,
    evidence: Mapping[str, object],
) -> dict:
    tags = _string_list(parent.get("content_tags")) if parent else []
    safe = (
        classification == "approved_but_not_synced"
        and evidence["can_quote_externally"] is True
        and not evidence["restricted"]
        and not evidence["pending"]
        and not evidence["internal"]
    )
    return {
        "record_id": record_id,
        "content_tags_exists": str(bool(tags)).lower(),
        "content_tags_source": "excel_preview_parent" if parent else "missing_parent",
        "content_tags_json": json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
        "original_source_data": str(bool(tags)).lower(),
        "safe_for_external_use": str(safe).lower(),
        "resolved_tags_json": json.dumps(tags if safe else [], ensure_ascii=False, separators=(",", ":")),
        "excluded_parent_child_tags_blocked": str(classification != "approved_but_not_synced").lower(),
        "title_or_body_inference_used": "false",
        "reason": (
            "Use exact parent content_tags only after parent sync and governance checks."
            if safe
            else "Do not expose tags while parent is excluded, internal, mapping-invalid, or unfinished."
        ),
    }


def _missing_join_assets(
    rows: Sequence[dict], missing_record_ids: set
) -> Dict[str, set]:
    result: Dict[str, set] = defaultdict(set)
    for row in rows:
        record_id = _text(row.get("record_id"))
        asset_id = _text(row.get("asset_id"))
        if record_id in missing_record_ids and asset_id:
            result[record_id].add(asset_id)
    return dict(result)


def _asset_rows(rows: Sequence[dict], missing_record_ids: set) -> Dict[str, Dict[str, dict]]:
    result: Dict[str, Dict[str, dict]] = defaultdict(dict)
    field_seen = set()
    for row in rows:
        record_id = _text(row.get("record_id"))
        asset_id = _text(row.get("asset_id"))
        field = _text(row.get("field"))
        if record_id not in missing_record_ids or not asset_id or field not in {"asset_url", "canonical_url"}:
            continue
        key = (asset_id, field)
        if key in field_seen:
            raise MissingParentDiagnosticError(
                f"duplicate asset URL field in Apply Preview: {asset_id}"
            )
        field_seen.add(key)
        asset = result[record_id].setdefault(
            asset_id,
            {
                "asset_type": _text(row.get("asset_type")),
                "asset_title": _text(row.get("asset_title")),
                "asset_url": "",
                "canonical_url": "",
                "decisions": set(),
            },
        )
        asset[field] = _text(row.get("proposed_value"))
        asset["decisions"].add(_text(row.get("review_decision")))
    for assets in result.values():
        for asset in assets.values():
            asset["review_status"] = (
                "approved"
                if asset["decisions"] == {"approve"}
                and asset["asset_url"]
                and asset["canonical_url"]
                else "incomplete_or_conflicting"
            )
    return dict(result)


def _asset_review_status(assets: Mapping[str, dict]) -> str:
    if assets and all(asset["review_status"] == "approved" for asset in assets.values()):
        return "approved"
    return "incomplete_or_conflicting"


def _parent_index(rows: Sequence[dict]) -> Tuple[Dict[str, dict], set]:
    result = {}
    duplicates = set()
    for row in rows:
        if _text(row.get("record_type")) != "merchant_case":
            continue
        record_id = _record_id(row)
        if not record_id:
            continue
        if record_id in result:
            duplicates.add(record_id)
        else:
            result[record_id] = row
    return result, duplicates


def _decision_index(rows: Sequence[dict]) -> Tuple[Dict[str, dict], set]:
    result = {}
    duplicates = set()
    for row in rows:
        if _text(row.get("record_type")) != "merchant_case":
            continue
        record_id = _record_id(row)
        if not record_id:
            continue
        if record_id in result:
            duplicates.add(record_id)
        else:
            result[record_id] = row
    return result, duplicates


def _formal_parent_index(rows: Mapping[Tuple[str, int], Sequence[dict]]) -> Dict[str, dict]:
    result = {}
    for (sheet, row), records in rows.items():
        merchant_records = [
            record
            for record in records
            if _text(record.get("record_type")) == "merchant_case"
        ]
        if len(merchant_records) == 1:
            result[f"{sheet}:r{row}"] = merchant_records[0]
    return result


def _restricted_index(path: Path) -> GovernanceIndex:
    records = _read_json_list(path)
    return GovernanceIndex(
        RestrictedCustomerRecord(
            brand_name=_text(row.get("brand_name")),
            website_url=_text(row.get("website_url")) or None,
            merchant_handle=_text(row.get("merchant_handle")) or None,
            restricted_aliases=(
                row.get("restricted_aliases")
                if isinstance(row.get("restricted_aliases"), list)
                else split_restricted_aliases(_text(row.get("brand_name")))
            ),
            source_sheet=_text(row.get("source_sheet")) or None,
            source_row=_integer(row.get("source_row")),
        )
        for row in records
        if _text(row.get("brand_name"))
    )


def _restricted_match(
    parent: Optional[Mapping[str, object]], governance_index: GovernanceIndex
) -> bool:
    if parent is None:
        return False
    identity = "\n".join(
        value
        for value in (
            _text(parent.get("brand_name")),
            _text(parent.get("merchant_handle")),
        )
        if value
    )
    return bool(identity and governance_index.check_text(identity).blocked)


def _missing_reason(
    vault_exists: bool,
    sqlite_exists: bool,
    mapping_error: bool,
    decision: Optional[Mapping[str, object]],
) -> str:
    if mapping_error:
        return "asset-to-parent mapping is missing or inconsistent in preview evidence"
    locations = []
    if not vault_exists:
        locations.append("managed Vault")
    if not sqlite_exists:
        locations.append("formal SQLite")
    decision_value = _text(decision.get("review_decision")) if decision else "missing"
    return f"parent absent from {' and '.join(locations)}; parent review_decision={decision_value}"


def _write_reports(
    output_dir: Path,
    summary: Mapping[str, object],
    parent_rows: Sequence[dict],
    orphan_rows: Sequence[dict],
    classification_rows: Sequence[dict],
    recommendation_rows: Sequence[dict],
    tag_rows: Sequence[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / DIAGNOSTIC_OUTPUT_FILENAMES[1], parent_rows)
    _write_csv(output_dir / DIAGNOSTIC_OUTPUT_FILENAMES[2], orphan_rows)
    _write_csv(output_dir / DIAGNOSTIC_OUTPUT_FILENAMES[3], classification_rows)
    _write_csv(output_dir / DIAGNOSTIC_OUTPUT_FILENAMES[4], recommendation_rows)
    _write_csv(output_dir / DIAGNOSTIC_OUTPUT_FILENAMES[6], tag_rows)
    (output_dir / DIAGNOSTIC_OUTPUT_FILENAMES[0]).write_text(
        _render_summary(summary, parent_rows, recommendation_rows), encoding="utf-8"
    )
    (output_dir / DIAGNOSTIC_OUTPUT_FILENAMES[5]).write_text(
        _render_governance_evidence(parent_rows, classification_rows), encoding="utf-8"
    )


def _render_summary(
    summary: Mapping[str, object],
    parent_rows: Sequence[dict],
    recommendations: Sequence[dict],
) -> str:
    actions = {row["record_id"]: row["recommended_action"] for row in recommendations}
    lines = [
        "# Missing Formal Parent Diagnostic",
        "",
        "> Read-only diagnosis. No parent, asset, Vault, SQLite, decision, mapping or Apply Plan was changed.",
        "",
        f"- Missing formal parents: {summary['missing_parent_count']}",
        f"- Orphan assets: {summary['orphan_asset_count']}",
        f"- Mapping errors: {summary['mapping_error_count']}",
        f"- Restricted/internal parents: {summary['restricted_or_internal_count']}",
        f"- Safe `sync_parent` recommendations: {summary['sync_parent_count']}",
        "",
        "## Parent Results",
        "",
    ]
    for row in parent_rows:
        lines.append(
            f"- `{row['record_id']}` {row['brand_name']}: {row['classification']} -> "
            f"{row['classification_code']} -> `{actions[row['record_id']]}`; "
            f"assets={row['orphan_asset_count']}; "
            f"review={row['review_decision'] or 'missing'}"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Formal Vault writes: 0",
            "- Formal SQLite writes: 0",
            "- Sync/confirm/execute: not run",
            "- New Apply Plan: not generated",
            "- Repairs applied: no",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_governance_evidence(
    parent_rows: Sequence[dict], classifications: Sequence[dict]
) -> str:
    by_id = {row["record_id"]: row for row in classifications}
    lines = [
        "# Missing Parent Governance Evidence",
        "",
        "> Parent review governs formal inclusion. Approved asset URLs do not override parent exclusion or unfinished review.",
        "",
    ]
    for row in parent_rows:
        classification = by_id[row["record_id"]]
        lines.extend(
            [
                f"## {row['record_id']} - {row['brand_name']}",
                "",
                f"- Classification: `{row['classification']}`",
                f"- Parent review decision: `{row['review_decision'] or 'missing'}`",
                f"- Enter Vault / index / quote: {row['can_enter_vault']} / {row['can_enter_content_index']} / {row['can_quote_externally']}",
                f"- Governance status: `{row['governance_status']}`",
                f"- Restricted / pending / internal: {row['restricted_status']} / {row['pending_status']} / {row['internal_status']}",
                f"- Evidence: {classification['evidence']}",
                "",
            ]
        )
    return "\n".join(lines)


def _write_csv(path: Path, rows: Sequence[dict]) -> None:
    rows = list(rows)
    fieldnames = list(rows[0]) if rows else ["record_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: _safe_csv_value(row.get(field)) for field in fieldnames}
            )


def _safe_csv_value(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "[unsafe input redacted]"
    return text


def _read_csv(path: Path) -> List[dict]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise MissingParentDiagnosticError(f"CSV has no header: {path}")
            return list(reader)
    except UnicodeDecodeError as exc:
        raise MissingParentDiagnosticError(f"CSV is not valid UTF-8: {path}") from exc


def _read_json_list(path: Path) -> List[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MissingParentDiagnosticError(f"JSON input is invalid: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise MissingParentDiagnosticError(f"JSON must be an array of objects: {path}")
    return payload


def _assert_safe_output(output_dir: Path, protected_paths: Sequence[Path]) -> None:
    output = output_dir.resolve()
    for path in protected_paths:
        protected = Path(path).resolve()
        if output == protected or output in protected.parents or protected in output.parents:
            raise MissingParentDiagnosticError(
                "diagnostic output must be separate from protected sources"
            )
    lowered = {part.casefold() for part in output.parts}
    if ".mka" in lowered or "obsidian_vault" in lowered:
        raise MissingParentDiagnosticError(
            "diagnostic output cannot be inside formal Vault or .mka"
        )


def _record_id(row: Mapping[str, object]) -> str:
    sheet = _text(row.get("source_sheet"))
    source_row = _text(row.get("source_row"))
    if source_row.endswith(".0"):
        source_row = source_row[:-2]
    return f"{sheet}:r{source_row}" if sheet and source_row else ""


def _split_record_id(record_id: str) -> Tuple[str, str]:
    if ":r" not in record_id:
        return "", ""
    return tuple(record_id.rsplit(":r", 1))  # type: ignore[return-value]


def _optional_bool(value: object) -> Optional[bool]:
    text = _text(value).casefold()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _display_bool(value: Optional[bool]) -> str:
    if value is None:
        return "unknown"
    return str(value).lower()


def _string_list(value: object) -> List[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [item.strip() for item in value.split("|")]
        raw = decoded if isinstance(decoded, list) else []
    else:
        raw = []
    result = []
    for item in raw:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result


def _integer(value: object) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _hash_path(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return "missing"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if not child.is_file() or child.name.startswith("._"):
            continue
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
