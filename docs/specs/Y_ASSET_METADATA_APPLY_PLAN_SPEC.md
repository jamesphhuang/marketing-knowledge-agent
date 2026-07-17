# Y. Asset Metadata Apply Plan

## 1. Scope

`mka apply-asset-metadata --plan` creates a deterministic, read-only plan for reviewed asset URL metadata. It does not confirm or execute the plan, write managed Obsidian files, alter SQLite, rebuild retrieval, change review decisions, activate queries, or modify the production Slack renderer.

The CLI reserves `--confirm PLAN_ID` and `--execute PLAN_ID`, but both fail closed in this sprint. They document the future three-stage contract and cannot be used to bypass human confirmation.

## 2. Formal Asset Record

The proposed source-of-truth path is:

```text
MKA/managed/assets/{sha256(asset_id)}.md
```

The filename is derived only from the stable `asset_id`; brand and title never determine identity. One parent record may own many assets without overwriting a record-level URL.

Each proposed flat record contains:

```text
asset_id
record_id
brand_name
merchant_handle
asset_type
asset_title
asset_url
canonical_url
source
source_location
provenance
reviewed_by
reviewed_at
review_decision
governance_eligibility
```

`content_tags` is deliberately absent. Future rendering must resolve tags through `asset.record_id -> formal source record`, and must omit tags when the parent is missing, not externally quoteable, not indexable, or has an empty tag list. Titles and body text are never tag sources.

## 3. Parent Join Contract

Planning distinguishes three sources:

1. Excel preview parent: validates the reviewed source identity.
2. Managed Vault parent: authoritative future tag and governance lookup.
3. Formal SQLite parent: required normalized retrieval join.

Every eligible asset must have one unique parent in all three. Missing or duplicate parents, identity disagreement, or any unexecutable join blocks the complete plan. The executor may not apply only the supported subset.

The candidate SQLite migration adds a unique `documents.source_record_id`, derived only from existing `source_sheet` and `source_row`, and creates `content_assets.record_id -> documents.source_record_id`. `content_tags` remains in the parent document metadata.

## 4. Plan, Confirm, Execute

### Plan (implemented)

- hashes decisions, previews, validation reports, restricted governance data, formal Vault and SQLite;
- binds all inputs and target checksums into `plan_state_hash` and deterministic `PLAN_ID`;
- expires 30 days after the latest reviewed URL decision;
- validates 222 asset identities, 206 eligible assets, 412 approved URL fields and 16 governance exclusions;
- emits candidate Vault paths, SQLite migration, backup, atomic swap and rollback instructions;
- performs no formal write.

### Confirm (contract only; disabled)

Must persist the exact PLAN_ID/state hash, confirmer, ISO timestamp, Vault/SQLite/backup paths, expected counts, reader shutdown acknowledgement and query-activation boundary. Confirm modifies no formal data.

### Execute (contract only; disabled)

Must require a matching confirmation artifact, reject expired or drifted plans, create checksum-backed backups, build temporary Vault and SQLite candidates, run join/governance/citation/conservation checks, and atomically swap under an exclusive lock. There is no skip-confirm option.

## 5. Safety And Rollback

- Formal inputs are hashed before and after planning.
- Governance-blocked assets never enter the URL apply manifest.
- The Vault candidate must reparse to the exact planned metadata.
- The SQLite candidate must pass foreign-key and read-only reopen checks.
- Candidate Vault files use `obsidian_vault/MKA/.asset_apply_staging/<PLAN_ID>/managed/assets/`; candidate SQLite uses `.mka/asset_apply_staging/<PLAN_ID>/content_index.candidate.sqlite`.
- Live targets are moved to manifest-backed backups before same-filesystem replacement.
- Any write, checksum, parsing, governance, join or post-swap failure restores both Vault and SQLite and verifies their original hashes.

## 6. Current Formal Readiness

The current plan conserves 206 eligible assets, 412 URL fields and 16 blocked assets. All 206 join uniquely to Excel preview parents. However, 9 assets belonging to 5 parent records do not currently have a managed Vault or formal SQLite parent. Therefore the current plan is **C. Not ready for Apply** and `confirm` must remain blocked.

Before a new plan can become ready, a human must choose one of these governed paths:

1. approve and sync the five missing parent records through the existing record review workflow, then rebuild and verify the formal content index; or
2. explicitly exclude/reclassify the nine orphan asset identities through human review.

The asset Apply process must not invent parent records or silently omit those assets.

## 7. Outputs

```text
asset_metadata_apply_plan_summary.md
asset_metadata_apply_manifest.csv
asset_vault_write_plan.csv
asset_sqlite_migration_plan.md
asset_source_record_join_validation.csv
asset_tag_resolution_plan.md
asset_governance_blocked.csv
asset_pre_apply_checksums.json
asset_rollback_execution_plan.md
asset_apply_confirmation_checklist.md
```

Reports can contain company-derived metadata and remain under the ignored `reports/` tree.
