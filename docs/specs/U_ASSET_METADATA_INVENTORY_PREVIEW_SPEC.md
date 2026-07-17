# U. Asset-Level Metadata Inventory & Enrichment Preview

## 1. Scope

`mka asset-metadata-preview` inventories candidate asset metadata without changing Excel preview JSON, managed Obsidian content, `DocumentMetadata`, or the formal SQLite index. Generated reports live under `reports/` and remain outside version control because they may contain company-derived data.

This stage does not enable any query constraint. It establishes stable identity, evidence provenance, coverage, conflicts, missing fields, and an empty human decision surface for a future Apply Sprint.

## 2. Stable Identity

For merchant case assets:

```text
record_id = {source_sheet}:r{source_row}
asset_id  = {record_id}:{asset_type}
```

`asset_type` is one of article, video, podcast, or news. Valid titles and invalid asset markers are both inventoried: invalid markers preserve governance evidence but do not become asset titles or publication statuses.

## 3. Candidate Sources

| Source | Role | May establish asset value automatically? |
| --- | --- | --- |
| Excel preview JSON | record identity, title slots, governance markers | no asset date/status inference |
| Original workbook hyperlink | exact `source_url` evidence | asset URL candidate only; human approval required |
| Managed Vault frontmatter | cross-check and internal file path | only explicit asset-level fields count |
| Formal SQLite metadata | read-only cross-check | no; mirrors reviewed Vault state |
| Review decisions CSV | record governance context | no mapping to asset `review_status` |
| Record `status` / `publish_date` | diagnostic context | never copied to asset status/date |

Public metrics are records/claims, not merchant content assets, and are not converted into synthetic assets by this inventory.

## 4. Field Registry

Canonical runtime definitions are in `asset_metadata_preview.ASSET_METADATA_FIELD_REGISTRY`. Each definition records type, valid values, empty behavior, record/asset level, authoritative and secondary sources, derivation policy, conflict policy, provenance fields, confidence, index eligibility, and operators.

Publication status values are:

```text
published | scheduled | draft | unpublished | archived | unknown
```

No evidence means `unknown`. URL presence, parent record status, capture date, interview year, filename, or directory cannot establish a status or publication date.

## 5. URL and Date Semantics

- `source_url`: exact observed hyperlink.
- `asset_url`: reviewed direct asset URL.
- `canonical_url`: publisher-confirmed canonical target.
- `internal_file_path`: managed Vault path, never a public URL.

Search URLs, redirects, and short URLs are noncanonical evidence. Known tracking parameters may be removed only to create a review candidate; the result is not automatically approved.

`interview_date`, `created_at`, `published_at`, and `updated_at` are independent. Only complete valid dates are accepted. Record capture/publish dates and year-only fields do not substitute for asset dates.

## 6. Outputs

```text
asset_metadata_inventory.csv
asset_metadata_enrichment_preview.csv
asset_metadata_conflicts.csv
asset_metadata_missing.csv
asset_metadata_summary.md
proposed_asset_metadata_schema.md
proposed_query_support_matrix.md
human_review_template.csv
```

Every enrichment row preserves record/asset identity, current and proposed values, source location, provenance, confidence, conflict status, review requirement, reason, and conservative proposed decision. Human `review_decision`, reviewer, and reviewed_at remain blank.

## 7. Safety and Apply Preconditions

The preview never imports indexing code or writes SQLite. SQLite is opened with `mode=ro`. Output may not equal the Excel preview source directory or be placed inside the Obsidian Vault.

A future Apply Sprint may proceed only after:

1. stable asset IDs pass conservation checks;
2. URL candidates and conflicts are reviewed;
3. authoritative date/status/partner/workflow sources are supplied;
4. all accepted rows include reviewer and reviewed_at;
5. a backward-compatible schema migration and rollback plan is approved;
6. a new index is built in a temporary path and passes governance/citation regression tests;
7. formal index replacement and query support activation are separate explicit actions.

Until all gates pass, `asset_url`, `published_at`, `publication_status`, `interview_date`, `interview_status`, `review_status`, and `partner_name` remain fail closed.
