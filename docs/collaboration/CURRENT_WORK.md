# Current work

> 這是 Codex 與 Claude Code 的任務鎖定頁。開始改檔前必須更新；同一時間只能有一個 active implementer。

## Lock

- State: active
- Milestone state: REVIEW_READY_DEVELOPMENT_FIRST
- Task: Golden / Negative Search Evaluation v1
- Implementer: Claude Code
- Reviewer: comprehensive review deferred (development-first)
- Branch: codex/impl/stable-record-v2-shadow-integration
- Baseline commit / candidate anchor: ef970ee57f9ce91b29a0604ff3b1b540e88110c1 (Search Taxonomy v1)
- Frozen behind this anchor: Stable Record Shadow, Content Index Lineage Gate, Search Taxonomy v1 (milestone anchor 5d4a21a327cf2dbb128e9ce21b07224d1e57bf84).
- Intended scope: add a deterministic Golden/Negative search-quality case set, a read-only evaluation harness, and a baseline run against the pinned Authority and a scratch copy of the current index. Minimal fixes only for implementation bugs the baseline exposes, each with its failing case saved first. No relaxation of fail-closed behaviour, no Authority or production-index mutation, no production re-index/deploy, no identity activation/retirement, no merchant-alias rebinding.
- Started at: 2026-08-26
- Last updated: 2026-08-26

## Objective and done definition

- Objective: answer what the search system actually does for real user queries, not merely whether the parser returns PASS; separate taxonomy defects from index coverage gaps and from upstream data-quality defects.
- Done when: Golden and Negative cases are sampled deterministically from the formal Authority; the harness observes plan and retrieval together; the baseline runs read-only against the formal Authority and a scratch index copy; every failure is classified; any implementation bug found is reproduced by a saved case before the fix; targeted tests pass; the Authority workbook and production index are byte-identical afterwards.

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
- Added an explicit-only Content Index lineage evidence bundle: canonical apply directory, exact sync plan, and completed sync manifest; no latest-artifact discovery or production default path exists.
- Strengthened sync execution receipts with exact plan SHA-256, plan state hash, apply path, row_v1 scheme/workbook lineage, and action/batch bindings.
- Added a confirm-mode prewrite gate before build-report writes, anomaly cleanup, chunking, or SQLite rebuild. Missing, malformed, unsupported, wrong-target, wrong-workbook, or Vault-mismatched evidence fails closed while preserving an existing DB byte-for-byte.
- Bound the verified canonical apply/plan merchant surface to the current managed Vault merchant surface by qualified row_v1 identity, target path, semantic checksum, and execution batch for mutating actions; bare sheet/row, filename, stable ID, or self-declared Vault checksum cannot substitute.
- Kept plan mode available without evidence and added `record_identity_scheme`, `lineage_gate`, `lineage_evidence`, and `production_reindex_ready` to summaries/reports.
- Verified valid temporary merchant and non-merchant rebuilds, stable-record-ID present/absent cases, unchanged Document/Chunk IDs, missing/extra/moved/modified/different merchant failures, and new/existing DB write safety.
- Reproduced the row_v1 apply-output guard regression and confirmed its cause: the Shadow candidate added `stable_record_id=None` to `DocumentMetadata.metadata_dict()`, while the unchanged apply Markdown serializer rendered every dict entry and therefore materialized the new absent key as `stable_record_id: null`.
- Restored the pre-Shadow apply-output contract at the narrow serializer boundary: omit only `stable_record_id` when its value is `None`. The in-memory optional field, validation, SQLite metadata round-trip, explicit non-empty value, Shadow gates, row_v1 authority, and Content Index Lineage Gate remain unchanged.
- Eliminated the original failing row_v1 guard test and passed the targeted row_v1/apply, Shadow, Lineage Gate, and Content Index verification set.

- Accepted the Codex → Claude Code implementer handoff without altering the frozen milestone anchor or the uncommitted lock record.
- Independently re-verified the formal taxonomy workbook read-only: sha256 `7e6ecffc2d4ad9b931c8abc2d75345305c0a93026ecb1cb10841aa5e8c6597a3` matches, and the file is byte-identical before and after every read.
- Established that the pinned file is the full seven-sheet MKT workbook, and that Search Taxonomy occupies two of its sheets; the loader reads only those two.
- Added `search_taxonomy.py`: an explicitly pinned, read-only Authority loader/resolver with no production default, no workbook discovery, no filename or workbook-metadata trust, and no mutation API.
- Asserted both sheet header shapes rather than inferring them; excluded the content-tag sheet's blank-header reference-URL column by trimming trailing blank headers, so it is never read.
- Established that `、` is a term character, not a separator (the canonical tag `直播串接（LINE、FB 等）` contains one); expansion cells split on `,` and `，` only.
- Read the two Sales Category column pairs as independent vocabularies, so LV2 parentage is never inferred from row adjacency where LV2 continues past the end of LV1.
- Reused `normalize_query_text()` unchanged and preserved canonical display values verbatim, including trailing whitespace; emitted constraints carry the value the formal index holds.
- Implemented the resolution contract: canonical resolves to itself; within one field a canonical outranks another row's expansion; two canonicals in one field, or any cross-field collision, is `AMBIGUOUS`; an explicit field resolves inside that domain only.
- Integrated into `build_query_plan()` behind `taxonomy=None`, superseding `CATEGORY_ALIASES` only when an Authority is supplied, and keeping the runtime catalog as the existence authority.
- Added two fail-closed abstain reasons, `ambiguous_taxonomy_term` and `taxonomy_known_but_not_indexed`, both outranking `unresolved_structured_lookup` so caller filters cannot reopen a recognised term as a broad semantic search.
- Fixed an integration gap found by the tests: a term the Authority had claimed was still re-read by the catalog pass, adding a second unasked-for constraint; claimed fragments are now removed first.
- Kept merchant identity precedence intact by scanning only what identity resolution left behind; `search_aliases.py` semantics and the merchant alias projection are untouched.
- Threaded an opt-in `taxonomy` object through `build_index_query_plan`, `search_index`, `ask_index`, `agent_ask` and `explain_query`, loaded once per invocation rather than per query.
- Added `--search-taxonomy-workbook` / `--search-taxonomy-sha256` to `search`, `ask`, `agent-ask` and `explain-query` as a both-or-neither pair with no production default.
- Implemented typo handling as suggestion only: deterministic edit-distance-one warnings that create no constraint, decide no field, set no abstain reason, and never pick a side of an ambiguity.
- Added `docs/specs/SEARCH_TAXONOMY_AUTHORITY_V1_SPEC.md` and `tests/test_search_taxonomy.py` (65 targeted tests over a synthetic workbook that reproduces the formal workbook's hostile shapes).

#### Golden / Negative Search Evaluation v1 (this round)

- Re-verified the Authority pin and independently recomputed index coverage rather than trusting the previous round: LV1 11/12, LV2 23/40, content tags 37/44 — identical to the prior diagnostic.
- Added `search_evaluation.py`: a read-only harness that observes the typed plan and the retrieval it produced together, and classifies every failure into an owner-bearing class rather than reporting only a pass rate.
- Added `tests/fixtures/search_quality_cases.json`: 39 cases (20 Golden, 19 Negative) sampled from the formal Authority by fixed, reproducible rules — no randomness and no seed. The Authority itself is not copied into the repository.
- Added `evaluate-search` to the CLI with a required `--db` and no default, so an evaluation can never fall through to whichever index sits at the conventional path; report output goes to the untracked `reports/` tree and exit code 1 signals a Golden regression.
- Ran the baseline against the pinned Authority and a scratch copy of the production index. Initial result: 39 cases, 36 PASS / 3 FAIL, all three `wrong_constraint`.
- Reproduced and fixed one real planner bug found by that baseline: an explicitly scoped `sales_category_lv2=<value>` also acquired a `sales_category_lv1` constraint on the same text (and the mirror for LV1), because the explicit parser's matched text was never removed from the input the catalog pass reads. Both failing cases were saved before the fix. Post-fix baseline: 38 PASS / 1 FAIL.
- Fixed a harness classification defect its own test exposed: an unexpected refusal was reported as `unexpected_ambiguity` regardless of cause, hiding index-coverage refusals. Unexpected blocks are now classified by the plan's own abstain reason.
- Confirmed the two upstream tag defects are correctly refused rather than papered over, and recorded them as `ingestion_quality`, not taxonomy failures.
- Added `docs/specs/SEARCH_QUALITY_EVALUATION_V1.md` and `tests/test_search_quality_evaluation.py` (26 tests: case-set integrity, harness must-fail behaviour, the explicit-scope regression, restricted-denylist safety, CLI, and a conditional formal baseline).
- Confirmed the Authority workbook and the production index are byte-identical after every read; the temporary index copy placed in the worktree to exercise the conditional test was removed.

### In progress

- None; the development-first Golden/Negative Search Evaluation v1 candidate is complete and uncommitted.

### Not started

- Comprehensive/adversarial review.
- Full application suite.
- Search Result Content Preview and UAT.
- A decision on the one-character-alias matching rule (see blockers).
- Production activation, row_v1 retirement, Vault migration, or production re-index.
- Reconciling the formal content index's `content_tags` with the Authority (see blockers).

## Verification

- Run before this extension: preflight branch/HEAD/worktree/staged-file checks; `pytest -q tests/test_stable_record_authority.py tests/test_stable_record_shadow.py tests/test_content_index.py tests/test_chunking.py`; read-only formal Authority pinned coverage smoke.
- Prior frozen-candidate result: `207 passed, 7 skipped`; formal pin `f7e6c278b0b503791d8f679d4ac19b7f856a517978c34e7015da7b4980c5cd7c` verified; formal coverage 120 resolved / 0 unresolved / 1 authority-only.
- Run for this extension: `pytest -q tests/test_content_index_lineage.py tests/test_content_index.py tests/test_obsidian_sync.py tests/test_stable_record_shadow.py`; Python `compileall` on changed source/tests; `git diff --check`.
- Result for this extension: `69 passed`; compileall and `git diff --check` passed.
- Compatibility reproduction before fix: `1 failed, 114 passed, 3 skipped`; the failing case was `test_apply_output_assigns_no_successor_identity_key`.
- Compatibility targeted verification after fix: `126 passed, 3 skipped`; the original failure is eliminated with no new failure in row_v1/apply, Shadow, Lineage Gate, or Content Index tests.
- Skips: seven pre-existing conditional Authority tests whose separate external evidence paths are absent from this isolated worktree; formal three-file Authority was verified separately by the read-only smoke.
- Warnings: seven Pydantic V1-validator deprecation warnings, including existing validators and the new validator written in repository style.
- Not run: full application suite and comprehensive/adversarial review (explicitly deferred); standalone lint/type tools (not configured/requested in this development-first targeted pass); production smoke/re-index/sync (forbidden).

### Search Taxonomy v1 (Claude Code, 2026-08-26)

- Run: `pytest -q` over `test_search_taxonomy.py`, `test_typed_query_retrieval.py`, `test_query_gating.py`, `test_pipeline.py`, `test_agentic.py`, `test_slack_interface.py`, `test_slack_search_presentation_v2.py`, `test_slack_exact_alias_query.py`, `test_production_search_alias_runtime.py`, `test_stable_record_shadow.py`, `test_content_index.py`, `test_content_index_lineage.py`, `test_excel_preview.py`, `test_stable_record_crosswalk.py`, `test_row_v1_workbook_lineage_guard.py`, `test_obsidian_sync.py`, `test_apply_review_decisions.py`, `test_slack_output_preview.py`, `test_llm_integration.py`, `test_chunking.py`, `test_ingestion.py`, `test_generation.py`, `test_validation.py`, `test_governance_evals.py`, `test_stable_record_authority.py`; Python `compileall` on changed source and tests; `git diff --check`.
- Result: `823 passed, 24 skipped`, no failures. `test_search_taxonomy.py` alone: `65 passed`.
- Import-origin guard: pytest resolves `marketing_knowledge_agent` to this worktree's `src`, not the main checkout's editable install; ad-hoc scripts were run with an explicit `PYTHONPATH`.
- Read-only formal Authority smoke: pin `7e6ecffc2d4ad9b931c8abc2d75345305c0a93026ecb1cb10841aa5e8c6597a3` verified; workbook byte-identical before and after every read. Sheets `Sales Category LV1 LV2` (4 headers) and `內容相關標籤` (2 headers plus one blank-header column). LV1 12 canonical / 216 expansion; LV2 40 canonical / 2210 expansion; content tags 44 canonical / 910 expansion. 3224 distinct aliases, 3056 unambiguous, 41 intra-field collisions, 127 cross-field ambiguities, 22 blank terms, 0 duplicate canonicals, 0 unowned expansion cells.
- Read-only Authority-versus-formal-index coverage, run against a scratchpad copy of the production SQLite so the production file was never opened for write: LV1 11/12 indexed, LV2 23/40, content tags 37/44; 0 LV1 and 0 LV2 index values are absent from the Authority.
- Skips: pre-existing conditional tests whose external evidence paths (formal M1/M2/backup identity evidence, the local-only row_v1 lineage workbook) are absent from this isolated worktree. Unchanged by this work.
- Not run for this extension: full application suite; comprehensive/adversarial review; lint/type tools; production re-index, sync or deploy.

### Golden / Negative Search Evaluation v1 (Claude Code, 2026-08-26)

- Run: `pytest -q` over `test_search_quality_evaluation.py`, `test_search_taxonomy.py`, `test_typed_query_retrieval.py`, `test_query_gating.py`, `test_pipeline.py`, `test_agentic.py`, `test_slack_interface.py`, `test_slack_search_presentation_v1.py`, `test_slack_search_presentation_v2.py`, `test_slack_exact_alias_query.py`, `test_slack_exact_alias_truncation.py`, `test_slack_retriever_truncation_propagation.py`, `test_production_search_alias_runtime.py`, `test_slack_output_preview.py`, `test_generation.py`, `test_governance_evals.py`, `test_stable_record_shadow.py`, `test_content_index.py`, `test_content_index_lineage.py`, `test_chunking.py`, `test_ingestion.py`, `test_excel_governance.py`, `test_row_v1_workbook_lineage_guard.py`, `test_obsidian_sync.py`, `test_apply_review_decisions.py`; `compileall` on changed source and tests; `git diff --check`.
- Result: `652 passed, 4 skipped`, no failures.
- Formal baseline against the pinned Authority and a scratch copy of the production index: 39 cases, 20/20 Golden, 18/19 Negative, one recorded known gap (`N-SHORT-01`). Run both through the harness directly and through `mka evaluate-search`.
- The conditional `test_formal_baseline_has_no_golden_regression` was executed for real by temporarily placing the index copy at `.mka/content_index.sqlite`: PASSED. That copy was then removed, and the production index remained `74b6038ef5e0ae9077fb97f355b6b50ad8f7e80bb4281fe78199002b2db3effe` throughout.
- Index handling: the index reader opens read-write, so every measurement ran against a scratch copy. The production file was never opened by this work; no journal sidecar appeared beside it.
- Skips: the conditional formal baseline (skips where the formal Authority or index is absent) and three pre-existing row_v1 lineage tests whose local-only workbook is absent from this isolated worktree.
- Pre-existing environment blocker, not caused by this work and not fixed here: `tests/test_slack_structured_governance.py` errors at fixture setup in this worktree because gitignored runtime state (`.mka/content_index.sqlite`, `.mka/search_alias_projection.json`, and further trees) is not present. Excluded from the targeted set and reported rather than worked around.
- Not run for this extension: full application suite; comprehensive/adversarial review; lint/type tools; production re-index, sync or deploy.

## Next exact action

- Run the deferred comprehensive/adversarial review of the Search Taxonomy v1 candidate plus this evaluation candidate, together with the frozen Shadow and Lineage Gate candidates. Do not activate Stable Record V2, retire row_v1, or authorize a production re-index.

## Blockers and unresolved user questions

- The build-content-index lineage finding is closed by this development candidate; production re-index remains unauthorized until review and a separate explicit production decision.
- The `stable_record_id: null` row_v1 apply-output compatibility blocker is closed by the narrow serializer-boundary patch.
- Stable Record V2 activation, row_v1 retirement, and production re-index remain unauthorized.
- New finding from the search evaluation, reported and deliberately not fixed: the Authority holds nine one-character aliases (`狗 貓 魚 鳥 蛇 硒 鉀 鋅 鎂`). CJK has no word boundary, so `狗` matches inside `熱狗堡` and binds `sales_category_lv2=寵物`, returning four pet brands for a hot-dog query. Case `N-SHORT-01` records the correct behaviour and fails. Whether the fix is a minimum alias length, a boundary rule, or an Authority-side change is a review decision; loosening or special-casing the matcher here was out of bounds.
- Related usability finding, no code change proposed: five of the eleven indexed LV1 categories (`居家生活`, `寵物`, `家庭婦幼`, `已關閉`, `其他（非 SL 用戶）`) are cross-level ambiguous by their own canonical name and abstain unless the level is named or an expansion term is used. Safe, but it is roughly half the top-level taxonomy and needs a product decision before UAT.
- Existing finding, not a blocker for this candidate and not fixed here: the formal content index disagrees with the Authority on two content tags. Merchant sheet row 38 carries `直播串接（line` and `fb 等）` as two tags because Vault ingestion splits on `、`, which the Authority treats as a term character; row 106 carries a truncated `商店設計` where row 13 carries `商店設計（shop builder 等）`. Querying those Authority terms therefore fails closed as `taxonomy_known_but_not_indexed` rather than returning a partial match. Reconciling this is upstream Vault/ingestion work and needs its own decision.
- Unresolved user questions: none.

## Release or transfer

- Lock remains active through the development-first implementation candidate.
- Implementer transferred Codex → Claude Code on 2026-08-26 after Codex stopped at its usage limit; Codex had completed read-only discovery only, with no functional Search Taxonomy commit. Milestone anchor `5d4a21a327cf2dbb128e9ce21b07224d1e57bf84` unchanged.
- Search Taxonomy v1 was committed as `ef970ee57f9ce91b29a0604ff3b1b540e88110c1` and is the anchor this evaluation round builds on. Stable Record Shadow, Content Index Lineage Gate and Search Taxonomy v1 all remain frozen.
- Release/transfer: not requested in this round.
