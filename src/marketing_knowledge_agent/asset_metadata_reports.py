from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Sequence

from .asset_metadata import ASSET_METADATA_FIELD_REGISTRY


def build_preview_summary(
    inventory: Sequence[dict],
    enrichment: Sequence[dict],
    conflicts: Sequence[dict],
    missing: Sequence[dict],
    *,
    workbook_path: Path,
    vault_path: Path,
    db_path: Path,
) -> dict:
    coverage = {}
    for field_name in ASSET_METADATA_FIELD_REGISTRY:
        rows = [row for row in enrichment if row["field"] == field_name]
        coverage[field_name] = {
            "total": len(rows),
            "existing": sum(_text(row["existing_value"]) not in {"", "unknown"} for row in rows),
            "proposed": sum(_text(row["proposed_value"]) not in {"", "unknown"} for row in rows),
            "missing": sum(row["conflict_status"] == "missing_evidence" for row in rows),
            "conflicts": sum(row["conflict_status"] not in {"none", "missing_evidence"} for row in rows),
        }
    evidence_fields = {"asset_url", "canonical_url", "published_at", "publication_status"}
    assets_with_evidence = {
        row["asset_id"]
        for row in enrichment
        if row["field"] in evidence_fields and _text(row["proposed_value"]) not in {"", "unknown"}
    }
    deterministic_candidates = sum(
        _text(row["proposed_value"]) not in {"", "unknown"} and row["conflict_status"] == "none"
        for row in enrichment
    )
    return {
        "asset_count": len(inventory),
        "asset_type_counts": dict(sorted(Counter(row["asset_type"] for row in inventory).items())),
        "coverage": coverage,
        "deterministic_candidate_count": deterministic_candidates,
        "human_review_count": sum(bool(row["review_required"]) for row in enrichment),
        "conflict_count": len(conflicts),
        "missing_field_count": len(missing),
        "completely_missing_asset_count": len(inventory) - len(assets_with_evidence),
        "vault_matched_asset_count": sum(bool(row["vault_present"]) for row in inventory),
        "sqlite_matched_asset_count": sum(bool(row["sqlite_present"]) for row in inventory),
        "workbook_path": str(workbook_path) if workbook_path else None,
        "vault_path": str(vault_path) if vault_path else None,
        "db_path": str(db_path) if db_path else None,
        "formal_index_modified": False,
        "vault_modified": False,
        "ready_constraints": [],
        "blocked_constraints": list(ASSET_METADATA_FIELD_REGISTRY),
    }


def render_summary(summary: dict) -> str:
    lines = [
        "# Asset Metadata Inventory Summary",
        "",
        "> Read-only preview. Formal SQLite index and managed Obsidian content were not modified.",
        "",
        f"- Assets inventoried: {summary['asset_count']}",
        f"- Deterministic enrichment candidates (not approved/applied): {summary['deterministic_candidate_count']}",
        f"- Human review rows: {summary['human_review_count']}",
        f"- Conflict rows: {summary['conflict_count']}",
        f"- Missing field rows: {summary['missing_field_count']}",
        f"- Assets with no URL/date/status evidence: {summary['completely_missing_asset_count']}",
        f"- Assets matched to managed Vault records: {summary['vault_matched_asset_count']}",
        f"- Assets matched to formal SQLite records: {summary['sqlite_matched_asset_count']}",
        "",
        "## Asset Types",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in summary["asset_type_counts"].items())
    lines.extend(["", "## Field Coverage", "", "| Field | Existing | Proposed | Missing | Conflicts |", "| --- | ---: | ---: | ---: | ---: |"])
    for field_name, values in summary["coverage"].items():
        lines.append(
            f"| `{field_name}` | {values['existing']} | {values['proposed']} | "
            f"{values['missing']} | {values['conflicts']} |"
        )
    lines.extend(
        [
            "",
            "## Constraint Readiness",
            "",
            "No currently blocked constraint is enabled by this preview. Candidates require human review, "
            "an apply-preview sprint, schema migration, index rebuild plan, and retrieval/governance regression checks.",
            "",
            "## Next Human Review",
            "",
            "1. Confirm each direct asset URL and canonical target.",
            "2. Supply publisher-backed published_at and publication status evidence.",
            "3. Define partner identity and interview/review workflow sources.",
            "4. Keep review_decision, reviewer, and reviewed_at explicit; suggested values are not approvals.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_proposed_schema() -> str:
    lines = [
        "# Proposed Asset Metadata Schema",
        "",
        "> Proposal only. This file does not change DocumentMetadata, Vault frontmatter, or SQLite.",
        "",
        "Stable identity: `asset_id = source_sheet:r{source_row}:{asset_type}` for Excel merchant assets.",
        "",
        "| Field | Type | Level | Values / empty rule | Authoritative source | Auto derive | Conflict policy | Operators |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for definition in ASSET_METADATA_FIELD_REGISTRY.values():
        values = ", ".join(definition.valid_values) or definition.empty_rule
        lines.append(
            f"| `{definition.canonical_name}` | {definition.data_type} | {definition.value_scope} | "
            f"{values} | {definition.authoritative_source or 'not established'} | "
            f"{str(definition.auto_derivation_allowed).lower()} | {definition.conflict_policy} | "
            f"{', '.join(definition.allowed_operators)} |"
        )
    lines.extend(
        [
            "",
            "## URL Separation",
            "",
            "- `source_url`: exact URL observed in a source cell; never assumed canonical.",
            "- `asset_url`: reviewed direct link to the asset.",
            "- `canonical_url`: publisher-confirmed canonical target; search, redirect and tracking URLs are not direct evidence.",
            "- `internal_file_path`: managed Vault location; never exposed as a public URL.",
            "",
            "## Date Separation",
            "",
            "`interview_date`, `created_at`, `published_at`, and `updated_at` are independent. "
            "Capture date, record publish_date, interview_year, and nearby dates cannot substitute for an asset published_at.",
            "",
            "## Apply Preconditions",
            "",
            "Only human-approved, conflict-free values with source provenance may enter a future migration preview. "
            "No row in this Sprint is approved for the formal index.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_query_support_matrix(summary: dict) -> str:
    lines = [
        "# Proposed Query Support Matrix",
        "",
        "| Constraint | Parser / Plan | Preview evidence | Human-approved schema | Formal index | Slack |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for field_name in ASSET_METADATA_FIELD_REGISTRY:
        proposed = summary["coverage"][field_name]["proposed"]
        lines.append(
            f"| `{field_name}` | expressible | {proposed} candidates | no | no | fail closed |"
        )
    lines.extend(
        [
            "",
            "No constraint becomes executable in this Sprint. Enabling requires approved values, schema migration, "
            "backward-compatible index rebuild, rollback procedure, and end-to-end governance tests.",
        ]
    )
    return "\n".join(lines) + "\n"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
