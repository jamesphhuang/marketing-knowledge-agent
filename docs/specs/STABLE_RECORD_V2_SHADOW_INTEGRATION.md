# Stable Record V2 Shadow Integration

## Scope

Stable Record V2 may be observed by internal read-side code only as additive metadata.

```text
Materialized
!= Shadow-readable
!= Activated
!= Mutation authority
!= row_v1 retired
```

`SHADOW_VALIDATED != ACTIVATION_AUTHORIZED`.

The current mutation authority remains `row_v1`. A shadow `stable_record_id` never replaces
`Document.id`, `Chunk.id`, `source_path`, Vault path, filename, update/delete/archive selection,
or any row_v1 binding.

## Trust and lineage contract

`load_stable_record_shadow()` requires three explicit caller inputs:

1. an Authority package path;
2. an external expected `manifest_hash` pin;
3. the row_v1 workbook sha256 for the records being read.

There is no production Authority path or hash default and no directory auto-discovery. The
consumer first reuses `load_authority_package()` for package self-validation, then compares the
loaded `manifest_hash` with the external pin, reuses `validate_authority()`, and checks the
additional row invariants used by shadow resolution. It accepts only:

- `authority_status=materialized_not_activated`;
- `activation_status=not_activated`;
- `stable_record_v2_activated=false`;
- `row_v1_retired=false`;
- `row_v1_status=retained_not_retired` on every row;
- merchant-case authority rows with unique valid stable IDs and unique qualified legacy keys.

The resolver reuses canonical `qualify_legacy_record_id()`:

```text
row_v1:<workbook_sha256>:<sheet>:r<row>
```

Bare `(source_sheet, source_row)` is audit metadata and is never a lookup key. Missing or wrong
workbook lineage returns unresolved; there is no sheet/row fallback. Authority-only records such
as a new record without a row_v1 predecessor are reported separately and never receive fabricated
legacy lineage.

The shadow loader does not enumerate the Authority directory. It reads only the files selected by
the canonical loader and therefore does not claim to implement the future Activation Gate's F3
exact-file-set rule. Exact-file-set validation remains an activation concern, not a shadow
authorization.

## Read-side integration

`DocumentMetadata.stable_record_id` is optional and validates against canonical
`STABLE_ID_RE`. `metadata_dict()` retains it, so the existing SQLite `metadata_json` path preserves
it through `SQLiteIndex.load_chunks()` without a schema change.

`create_content_index_plan(..., stable_record_shadow=resolver)` and
`build_content_index(..., stable_record_shadow=resolver)` are explicit opt-in APIs. With no
resolver, existing behavior and output remain unchanged. With a resolver, only `merchant_case`
records can be enriched. The read-only plan exposes calculated coverage counts; no count is
hard-coded.

No CLI option is added in this work package. This keeps production `build-content-index` from
acquiring an implicit or casually selected Authority path while the existing lineage finding is
open. Temporary/test callers may construct the resolver explicitly and use temporary Vault/SQLite
paths.

## Mutation boundaries

This integration does not modify `obsidian_sync`. Its matching and execute semantics continue to
use the existing row_v1 contract. The shadow resolver has no write, update, delete, archive,
activation, retirement, or re-index API.

The existing build-content-index lineage finding remains:

```text
BLOCKING_FOR_PRODUCTION_REINDEX
```

Shadow metadata resolution does not close that blocker and does not authorize production sync,
production re-index, Stable Record V2 activation, or row_v1 retirement.
