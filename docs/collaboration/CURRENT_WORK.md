# Current work

> 這是 Codex 與 Claude Code 的任務鎖定頁。開始改檔前必須更新；同一時間只能有一個 active implementer。

## Lock

- State: active
- Milestone state: REVIEW_READY_DEVELOPMENT_FIRST
- Task: Stable Record V2 Shadow Integration
- Implementer: Codex
- Reviewer: comprehensive review deferred (development-first)
- Branch: codex/impl/stable-record-v2-shadow-integration
- Baseline commit: 6287862964fc9a03e00e9dc8f8fc99077b2eacdf
- Intended scope: add a read-only, externally pinned Stable Record V2 shadow resolver; carry optional stable_record_id metadata through temporary/test content-index planning and SQLite metadata_json; add targeted tests and a minimal spec. No activation, mutation-authority change, Vault mutation, or production re-index.
- Started at: 2026-08-26
- Last updated: 2026-08-26

## Objective and done definition

- Objective: allow qualified row_v1 workbook lineage to resolve Stable Record V2 IDs as additive read-side metadata while row_v1 remains mutation authority.
- Done when: the shadow resolver validates an externally pinned materialized-not-activated Authority, bare rows cannot resolve, optional stable IDs round-trip through metadata_json without changing Document/Chunk IDs, opt-in content-index enrichment is covered by targeted tests, the minimal spec records all governance boundaries, and the worktree remains uncommitted for review.

## Progress

### Completed

- Isolated worktree and branch verified at the requested baseline.
- Implementation lock accepted by Codex.
- Added a read-only Stable Record V2 shadow resolver that requires an explicit Authority path, external expected manifest hash, and row_v1 workbook sha256.
- Reused the canonical Authority loader, `validate_authority()`, `STABLE_ID_RE`, and `qualify_legacy_record_id()`; added shadow-specific manifest, row, unique-binding, and row-classification count gates.
- Added `DocumentMetadata.stable_record_id` as optional validated additive metadata and preserved it through `metadata_dict()` / SQLite `metadata_json` round-trip.
- Added explicit API-only opt-in shadow enrichment to content-index planning/building, including calculated read-only coverage summary.
- Preserved default no-shadow behavior and existing Document/Chunk ID derivation.
- Added the minimal shadow integration spec and targeted synthetic hostile tests.
- Ran a read-only formal Authority pinned coverage smoke: 120 merchant continuations seen, 120 resolved, 0 unresolved, 1 authority-only.
- Confirmed no Authority bytes, Vault files, production SQLite index, activation state, row_v1 state, main, commit, or remote were mutated.

### In progress

- Development-first implementation candidate is ready for the next milestone review; implementation lock remains active as requested.

### Not started

- Comprehensive/adversarial review.
- Full application suite.
- Search Taxonomy or stable-ID search filtering.
- Production activation, row_v1 retirement, Vault migration, or production re-index.

## Verification

- Run: preflight branch/HEAD/worktree/staged-file checks; `pytest -q tests/test_stable_record_authority.py tests/test_stable_record_shadow.py tests/test_content_index.py tests/test_chunking.py`; `git diff --check`; read-only formal Authority pinned coverage smoke.
- Result: `207 passed, 7 skipped`; `git diff --check` passed; formal pin `f7e6c278b0b503791d8f679d4ac19b7f856a517978c34e7015da7b4980c5cd7c` verified; formal coverage 120 resolved / 0 unresolved / 1 authority-only.
- Skips: seven pre-existing conditional Authority tests whose separate external evidence paths are absent from this isolated worktree; formal three-file Authority was verified separately by the read-only smoke.
- Warnings: seven Pydantic V1-validator deprecation warnings, including existing validators and the new validator written in repository style.
- Not run: full application suite and comprehensive/adversarial review (explicitly deferred); lint/type checks (not requested in this development-first targeted pass); production smoke/re-index/sync (forbidden).

## Next exact action

- Next milestone: independent review of the uncommitted implementation candidate, with special attention to external pin trust, row invariant coverage, and content-index opt-in boundaries.

## Blockers and unresolved user questions

- Existing build-content-index lineage finding remains `BLOCKING_FOR_PRODUCTION_REINDEX`; this work package does not fix it.
- Stable Record V2 activation, row_v1 retirement, and production re-index remain unauthorized.
- Unresolved user questions: none.

## Release or transfer

- Lock remains active through the development-first implementation candidate.
- Release/transfer: not requested in this round.
