from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping, Sequence


OUTPUT_FILENAMES = (
    "asset_apply_preview_summary.md",
    "asset_apply_preview.csv",
    "asset_apply_preview_errors.csv",
    "asset_apply_preview_warnings.csv",
    "asset_apply_preview_blocked.csv",
    "asset_schema_migration_preview.md",
    "asset_vault_diff_preview.md",
    "asset_index_diff_preview.md",
    "asset_apply_rollback_plan.md",
    "proposed_asset_query_support_matrix.md",
)
PREVIEW_COLUMNS = (
    "record_id",
    "asset_id",
    "brand_name",
    "asset_type",
    "asset_title",
    "field",
    "current_value",
    "proposed_value",
    "review_decision",
    "reviewer",
    "reviewed_at",
    "provenance",
    "source_location",
    "eligibility",
    "governance_status",
    "action",
    "reason",
)
ISSUE_COLUMNS = ("severity", "code", "asset_id", "field", "message")


def write_asset_apply_preview_reports(
    output_dir: Path,
    summary: Mapping[str, object],
    preview_rows: Sequence[dict],
    blocked_rows: Sequence[dict],
    errors: Sequence[dict],
    warnings: Sequence[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "asset_apply_preview.csv", preview_rows, PREVIEW_COLUMNS)
    _write_csv(output_dir / "asset_apply_preview_blocked.csv", blocked_rows, PREVIEW_COLUMNS)
    _write_csv(output_dir / "asset_apply_preview_errors.csv", errors, ISSUE_COLUMNS)
    _write_csv(output_dir / "asset_apply_preview_warnings.csv", warnings, ISSUE_COLUMNS)
    (output_dir / "asset_apply_preview_summary.md").write_text(
        render_summary(summary), encoding="utf-8"
    )
    (output_dir / "asset_schema_migration_preview.md").write_text(
        render_schema_migration(summary), encoding="utf-8"
    )
    (output_dir / "asset_vault_diff_preview.md").write_text(
        render_vault_diff(summary), encoding="utf-8"
    )
    (output_dir / "asset_index_diff_preview.md").write_text(
        render_index_diff(summary), encoding="utf-8"
    )
    (output_dir / "asset_apply_rollback_plan.md").write_text(
        render_rollback_plan(summary), encoding="utf-8"
    )
    (output_dir / "proposed_asset_query_support_matrix.md").write_text(
        render_query_support_matrix(), encoding="utf-8"
    )


def render_summary(summary: Mapping[str, object]) -> str:
    lines = [
        "# Asset URL & Identity Apply Preview",
        "",
        "> Preview only. No decision was applied; Excel, source CSV, Obsidian and the formal SQLite index were not modified.",
        "",
        f"- Conclusion: **{summary['conclusion']}**",
        f"- Dry run: {str(summary['dry_run']).lower()}",
        f"- Validation errors: {summary['validation_error_count']}",
        f"- Apply preview errors: {summary['error_count']}",
        f"- Apply preview warnings: {summary['warning_count']}",
        f"- Inventory assets: {summary['inventory_asset_count']}",
        f"- Eligible assets: {summary['eligible_asset_count']}",
        f"- Governance-blocked assets: {summary['governance_blocked_asset_count']}",
        f"- Approved URL field decisions: {summary['approved_field_count']}",
        f"- Proposed field changes: {summary['preview_row_count']}",
        f"- Blocked field rows: {summary['blocked_row_count']}",
        "",
        "## Action Counts",
        "",
    ]
    for action in ("add", "update", "no_change", "blocked", "excluded", "invalid"):
        lines.append(f"- `{action}`: {summary['action_counts'].get(action, 0)}")
    lines.extend(
        [
            "",
            "## Conservation",
            "",
            f"- Asset identity stable: {_yes_no(summary['asset_identity_stable'])}",
            f"- Record identity stable: {_yes_no(summary['record_identity_stable'])}",
            f"- Eligible asset conservation: {_yes_no(summary['eligible_asset_conservation'])}",
            f"- Approved field conservation: {_yes_no(summary['approved_field_conservation'])}",
            f"- Governance exclusion conservation: {_yes_no(summary['governance_exclusion_conservation'])}",
            f"- Source files unchanged: {_yes_no(not summary['source_files_modified'])}",
            "",
            "## Storage Recommendation",
            "",
            "Use dedicated flat managed asset records as the reviewed source of truth, then derive a normalized SQLite `content_assets` table. Do not write asset URLs into the parent record-level `canonical_url`.",
            "",
            "## Safety Boundary",
            "",
            "- `asset_url` and `canonical_url` remain independent approved fields even when values are equal.",
            "- URL approval does not establish publication status, publication date, interview fields, review status or partner identity.",
            "- No query constraint is enabled by this preview.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_schema_migration(summary: Mapping[str, object]) -> str:
    return f"""# Asset Schema Migration Preview

> Proposal only. No schema or formal data was changed.

## Recommended Target

Use a two-layer design:

1. **Dedicated flat managed asset records** under a future managed namespace such as `MKA/_assets/`. One asset per record avoids nested YAML, preserves the existing simple frontmatter parser, supports one parent record with many assets, and permits per-asset rollback.
2. **Normalized SQLite `content_assets` table** derived from reviewed asset records. SQLite is a retrieval projection, not the review source of truth.

Do not place multiple asset URLs in the parent record-level `canonical_url`. Do not add a nested `assets` object until the shared frontmatter parser and round-trip tests explicitly support it.

## Proposed Flat Asset Contract

```text
asset_id
record_id
asset_type
asset_title
asset_url
canonical_url
source_url
source_location
provenance
reviewed_by
reviewed_at
review_decision
governance_eligibility
```

No `published_at`, `publication_status`, interview fields, `review_status`, partner identity, or parent record status is added by this preview.

## Options

| Option | Identity / multi-asset | Reviewability | Retrieval | Rollback | Recommendation |
| --- | --- | --- | --- | --- | --- |
| Independent JSON | strong | moderate | requires loader | file-level | acceptable interchange format |
| Nested Obsidian frontmatter | strong | high | requires parser/model migration | record-level | defer; current parser lacks nested mapping support |
| SQLite asset table only | strong | low | excellent | DB backup | reject as sole source of truth |
| Flat managed asset records + SQLite projection | strong | high | excellent after migration | per-asset + DB swap | **recommended** |

## Current Preview

- Eligible assets: {summary['eligible_asset_count']}
- Proposed URL field changes: {summary['preview_row_count']}
- Governance-blocked assets excluded: {summary['governance_blocked_asset_count']}
"""


def render_vault_diff(summary: Mapping[str, object]) -> str:
    return f"""# Asset Vault Diff Preview

> No Vault file was created, changed, archived or deleted.

## Future Proposed Shape

- Add one flat managed asset record per eligible asset under a dedicated namespace.
- Preserve `asset_id` and `record_id`; do not merge assets into their parent record.
- Add only approved `asset_url` and `canonical_url` plus review provenance.
- Keep all {summary['governance_blocked_asset_count']} governance-blocked assets out of the managed asset namespace.
- Do not copy parent `status`, `publish_date` or record review decision into asset publication/review fields.

## Preview Counts

- Future asset records considered: {summary['eligible_asset_count']}
- URL fields proposed: {summary['preview_row_count']}
- Existing Vault writes in this sprint: 0

The formal Apply Sprint must first add a flat asset model, a deterministic filename mapping for `asset_id`, frontmatter round-trip tests, a plan/confirm/execute gate, backup, and automatic rollback.
"""


def render_index_diff(summary: Mapping[str, object]) -> str:
    return f"""# Asset Index Diff Preview

> The formal SQLite index was not opened for writing or rebuilt.

## Proposed Future Table

```sql
CREATE TABLE content_assets (
    asset_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_title TEXT NOT NULL,
    asset_url TEXT,
    canonical_url TEXT,
    source_url TEXT,
    source_location TEXT,
    provenance_json TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    review_decision TEXT NOT NULL,
    governance_eligibility TEXT NOT NULL
);
CREATE INDEX idx_content_assets_record_id ON content_assets(record_id);
CREATE INDEX idx_content_assets_type ON content_assets(asset_type);
```

The table should be built in a temporary database from reviewed asset records, checked for identity/governance/citation conservation, and atomically swapped only in a later approved sprint.

## Expected Future Diff

- Eligible asset rows: {summary['eligible_asset_count']}
- URL field values represented: {summary['preview_row_count']}
- Governance-blocked asset rows: 0
- Formal index writes in this sprint: 0
"""


def render_rollback_plan(summary: Mapping[str, object]) -> str:
    return f"""# Asset Apply Rollback Plan

> Design only. No rollback action is required for this preview because nothing was applied.

- Proposed field changes: {summary['preview_row_count']}

## Required Formal Apply Procedure

1. Create a **pre-apply manifest** containing every `asset_id`, target path, prior file hash/value, decision hash, and expected new hash.
2. Copy every affected asset record and the formal index to a batch-scoped backup outside the live namespace.
3. Write candidate asset records to a temporary namespace; reparse and checksum every record.
4. Build the candidate `content_assets` table in a temporary database and run governance, identity and citation assertions.
5. Perform an **atomic** namespace/index swap only after all assertions pass.
6. On any failure, restore prior files and the **formal index** from the manifest-backed backup, then verify hashes and conservation.
7. Retain the batch manifest and backup until a human confirms the post-apply diff.

Rollback coverage must equal all {summary['preview_row_count']} proposed field changes; governance-blocked rows are never written and therefore require no mutation rollback.
"""


def render_query_support_matrix() -> str:
    return """# Proposed Asset Query Support Matrix

| Constraint / output | After this preview | After approved schema Apply | After index rebuild + tests | Recommendation |
| --- | --- | --- | --- | --- |
| `asset_url` lookup | fail closed | data may exist | potentially support exact lookup | proposed only; do not enable now |
| `canonical_url` output/citation | unavailable | data may exist | output metadata available | recommended as output metadata |
| `canonical_url` natural-language search | fail closed | data may exist | optional exact lookup | low priority; do not enable by default |
| `published_at` | fail closed | unsupported | unsupported | needs authoritative source |
| `publication_status` | fail closed | unsupported | unsupported | needs authoritative source |
| `interview_date` | fail closed | unsupported | unsupported | separate enrichment sprint |
| `interview_status` | fail closed | unsupported | unsupported | separate workflow source |
| `review_status` | fail closed | unsupported | unsupported | do not infer from URL review |
| `partner_name` | fail closed | unsupported | unsupported | needs reviewed identity source |

This sprint enables no query constraint.
"""


def _write_csv(path: Path, rows: Sequence[dict], columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _safe_value(row.get(column)) for column in columns})


def _safe_value(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "[unsafe input redacted]"
    return text


def _yes_no(value: object) -> str:
    return "yes" if value else "no"
