# W. Asset URL & Identity Apply Preview

## 1. Scope

`mka apply-asset-review-decisions --dry-run` creates proposed URL changes and migration documents only. It is a separate contract from merchant/governance `apply-review-decisions` and has no formal Apply mode.

The command never changes Excel, human decisions, inventory/enrichment sources, managed Obsidian, or the formal SQLite index. It enables no query constraint.

## 2. Inputs And Fresh Validation

The join key is `(record_id, asset_id, field)`. Only `asset_url` and `canonical_url` are in scope.

Before building a diff, the command:

1. hashes all protected sources;
2. reruns `validate_asset_review_decisions` in a temporary directory;
3. compares fresh status and eligibility rows with persisted validation reports;
4. requires zero validation errors, no unresolved manual review, stable identities, valid reviewer/date and exact proposals;
5. rechecks URL format, tracking, duplicate canonical targets and governance eligibility;
6. verifies source hashes again after report generation.

Any failed gate produces diagnostics and an empty `asset_apply_preview.csv`.

## 3. Preview Contract

Only rows satisfying all conditions enter the proposed Apply CSV:

- asset eligibility is `ready_for_apply_preview`;
- field decision is `approve`;
- reviewer and ISO reviewed_at are present;
- proposal is non-empty and unchanged;
- identity joins exactly;
- no conflict, governance blocker, manual review or validation issue exists.

Actions are `add`, `update`, `no_change`, `blocked`, `excluded`, or `invalid`. Governance-blocked rows are written only to `asset_apply_preview_blocked.csv` and never to the proposed Apply CSV.

URL decisions remain independent. Approval never creates or modifies source URL, internal path, publication date/status, interview fields, review status, partner identity, or parent record status.

## 4. Storage Recommendation

The current frontmatter parser supports flat scalars/lists but not nested mappings. Therefore the recommended formal target is:

1. one dedicated flat managed asset record per `asset_id` as the reviewed source of truth;
2. a normalized SQLite `content_assets` table derived from those records.

This preserves one-to-many record/asset relationships, per-asset citations, deterministic rollback and Vault readability. Nested parent frontmatter is deferred until parser/model round-trip support exists. SQLite must not be the sole review source of truth, and parent record-level `canonical_url` must not hold multiple asset URLs.

## 5. Outputs

```text
asset_apply_preview_summary.md
asset_apply_preview.csv
asset_apply_preview_errors.csv
asset_apply_preview_warnings.csv
asset_apply_preview_blocked.csv
asset_schema_migration_preview.md
asset_vault_diff_preview.md
asset_index_diff_preview.md
asset_apply_rollback_plan.md
proposed_asset_query_support_matrix.md
```

## 6. Query Boundary

`asset_url` exact lookup becomes only a proposed future capability. It still requires an approved schema Apply, temporary index rebuild, governance/citation tests and explicit activation. `canonical_url` is recommended primarily as citation/output metadata.

The following remain fail closed: `published_at`, `publication_status`, `interview_date`, `interview_status`, `review_status`, and `partner_name`.

## 7. Formal Apply Preconditions

- approve the flat asset record schema and deterministic filename mapping;
- add parser/model round-trip tests without changing existing record semantics;
- define a plan/confirm/execute workflow with a pre-apply manifest;
- back up every target record and database before mutation;
- write to temporary Vault/index targets and verify hashes, governance, identity, citation and conservation;
- use an atomic swap and automatic rollback;
- keep query activation as a separate sprint after formal data and index acceptance.
