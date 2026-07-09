# Governance Rules

## `record_type` Rules

| record_type | Meaning | Default role |
| --- | --- | --- |
| `content_asset` | Markdown-based marketing content such as blog, website, podcast, social, YouTube, design notes. | content index |
| `merchant_case` | Merchant / partner case index rows from Excel. | review before content index |
| `public_metric` | Approved metric / claim library with exposure channel constraints. | review before content index |
| `pending_metric` | Unapproved metric / claim candidate. | internal inventory only |
| `restricted_customer` | Non-public customer denylist record. | governance table only |
| `handle_mapping` | Merchant identity mapping / enrichment table. | normalization table only |

## Index Role Rules

| index role | Purpose | Examples |
| --- | --- | --- |
| `content_index` | User-searchable content chunks with citation eligibility. | approved content assets, approved merchant cases, approved public metrics |
| `governance_table` | Policy / denylist checks and blocking. | restricted customers |
| `internal_inventory` | Internal-only data audit or gap tracking. | pending metrics |
| `normalization_table` | Metadata enrichment and identity matching. | handle mappings |

## Review Decision Definitions

| review_decision | Definition | Expected effect |
| --- | --- | --- |
| `approve` | Approved for normal use after review. | May enter Vault / content index when record type allows. |
| `approve_internal_only` | Approved only for internal reference. | Do not quote externally. |
| `exclude` | Exclude from content index and future public use. | Do not sync as searchable content. |
| `exclude_from_content_index` | Keep record for traceability but exclude searchable content. | May stay in preview / internal inventory. |
| `enter_governance_table_only` | Store only as governance rule. | Used for blocking / warnings, not citations. |
| `keep_internal_only` | Keep only for internal tracking. | No external quote, no public citation. |
| `restricted_use_only` | Use only under explicit restrictions. | Preserve warnings and channel restrictions. |
| `needs_update` | Metadata or content must be corrected before use. | Do not treat as approved. |
| `enrich_metadata` | Add missing handle, category, or other metadata. | Re-review after enrichment if needed. |
| `keep_all_records` | Preserve multi-interview records. | Do not dedupe solely by brand or handle. |
| `manual_review` | Requires human investigation. | No automatic action. |
| `deprecated` | Record is retained for history but should not be used as current source. | Warning required; no external quote unless explicitly approved. |

## Prohibited Behavior

- `restricted_customer` must never enter general RAG citation.
- `restricted_customer` must never enter general vector / content index.
- `pending_metric` must not be quoted externally.
- `public_metric` with `missing_allowed_exposure_channels=true` must not be quoted externally.
- `restricted_note` must be preserved and visible to downstream checks.
- `same_brand_multiple_records` must not be automatically deduped.
- `same_handle_multiple_records` must not be automatically deduped.
- `suspected_duplicate_review` must not be automatically deleted, merged, or overwritten.
- `suggested_action` must never be treated as final human decision.
- `handle_mapping` must only enrich / normalize identity, not become a citation source.
- Any future external-copy generation must check `can_quote_externally` and `allowed_exposure_channels`.

## Governance Warnings

Merchant case records should trigger warning or review when:

- Business status includes closed, stopped operations, migrated away, or ended partnership.
- Notes mention content takedown, shutdown, competitor migration, or feature deactivation.
- Asset fields contain invalid values such as under review, temporarily offline, or removed.
- All article / video / podcast / news fields are invalid or blank.

Public metrics should trigger warning or review when:

- Allowed exposure channels are missing.
- Notes include non-public restrictions.
- Notes restrict usage to verbal briefing.
- Notes restrict market-level details.

Restricted denylist matches should avoid outputting sensitive details and should route to manual confirmation.
