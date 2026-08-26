# Current work

> 這是 Codex 與 Claude Code 的任務鎖定頁。開始改檔前必須更新；同一時間只能有一個 active implementer。

## Lock

- State: active
- Milestone state: B2_REAL_WRITER_REGRESSION_TESTS_ADDED_UNCOMMITTED
- Task: B2 Real Writer Regression Test Hardening
- Implementer: Claude Code
- Reviewer: not yet reviewed for this WP; it closes accepted nonblocking backlog item 6 raised by
  the independent delta review of `472f5c3`
- Branch: codex/test/b2-governed-writer-regression
- Baseline commit: 6002f0c4888d3f88fcb1cbcfe5db1a6f7e872457
- Product behavior changed: NO
- Test-only hardening: YES
- Intended scope: add regression tests that call the two governed Markdown writers themselves.
  No product source change, no Search Taxonomy / query planner / Authority workbook /
  `stable_record_id` schema / `governed_markdown_frontmatter()` semantics change, no refactor,
  no production sync, no production re-index, no Stable Record V2 activation, no row_v1
  retirement, no Slack activation, no UAT.
- Started at: 2026-08-26
- Last updated: 2026-08-26

### B2 Real Writer Regression Test Hardening (this WP)

Accepted nonblocking backlog item 6 said the B2 fix was correct but unguarded: the two writer
tests in `test_stable_record_shadow.py` applied `governed_markdown_frontmatter()` themselves and
then asserted on their own output, so removing the boundary call from both writers left all 20
tests passing. That mutation was reproduced in this worktree before any test was written, and it
reproduced exactly: `20 passed`.

Five tests were added that call the writers directly —
`apply_review_decisions._markdown_file_for_record` and
`store_data_sync_plan_v2_execution._create_parent_markdown` — and assert on the frontmatter those
writers actually return, parsed rather than string-matched. `src/` is byte-identical to the
baseline.

```text
PRODUCT_SOURCE_CHANGED=NO
TEST_ONLY_CHANGE=YES
WRITER_INVENTORY_UNCHANGED=YES
MUTATION_PROBE_CAUGHT=YES
STABLE_RECORD_V2_ACTIVATED=NO
ROW_V1_RETIRED=NO
PRODUCTION_REINDEX_AUTHORIZED=NO
PRODUCTION_REINDEX_RUN=NO
SLACK_TAXONOMY_ACTIVATED=NO
MAIN_UPDATED=NO
```

#### Mutation-strength evidence

Each probe is a copy of `src/` and `tests/` under `/private/tmp`, mutated there so the repository
source is never touched, and confirmed by the imported module path in the pytest output.

| Probe | Boundary call removed from | Result |
| --- | --- | --- |
| Baseline, pre-existing 20 tests | both writers | `20 passed` — the reported hole, reproduced |
| Both writers, new test set | both writers | `3 failed, 22 passed` |
| Apply writer only | `_markdown_file_for_record` | `1 failed, 24 passed` |
| Managed Parent writer only | `_create_parent_markdown` | `2 failed, 23 passed` |

Each writer is therefore guarded independently, not only in combination.

#### Writer inventory

Re-verified rather than assumed. `DocumentMetadata.metadata_dict()` still has five callers —
`indexing.py` (SQLite `metadata_json`), `store_data_sync_plan_v2_execution.py` (one SQLite write,
one governed Markdown writer), `retrieval.py` (search-result payload), and
`apply_review_decisions.py` (governed Markdown writer). `obsidian_sync._synced_content` re-renders
frontmatter it parsed from an existing file and calls `metadata_dict()` nowhere, so it propagates
the key rather than originating it. Two governed Markdown originators, both now covered by a real
-writer test. No new originator found.

### Superseded milestone lock record

The lock below describes the preceding milestone — Stable Shadow + Content Index Lineage + Search
Taxonomy acceptance and main integration preparation. It is retained unchanged as the record of
that work; nothing in this WP alters its state, and `main` is still not updated.

- Milestone state: MAIN_INTEGRATION_CANDIDATE_PREPARED_AWAITING_AUTHORIZATION
- Task: Stable Shadow + Content Index Lineage + Search Taxonomy — acceptance and main integration preparation
- Implementer: Claude Code
- Reviewer: independent delta review completed 2026-08-26 — verdict PASS_WITH_NONBLOCKING_FINDINGS, 0 blocking findings; the reviewer did not modify the candidate
- Branch: codex/integrate/stable-shadow-search-taxonomy
- Integration baseline (GitHub main at preparation time): dd215c6b4199c221288720d6d702eff0c15ed0a9
- Reviewed candidate (frozen, unchanged, merged exactly): 472f5c389d57f91d35b50db8bdd0d96aa64ddf63
- Merged behind that anchor: Stable Record Shadow, Content Index Lineage Gate, Search Taxonomy v1, Golden/Negative Search Evaluation v1, Consolidated Blocker Remediation R1.
- Intended scope: prepare an integration candidate only — merge the exact reviewed commit, record acceptance, run focused integration verification. No product source or test change, no rebase/squash/amend of reviewed lineage, no main push, no production sync/re-index/deploy, no Stable Record V2 activation, no row_v1 retirement, no Slack taxonomy activation.
- Started at: 2026-08-26
- Last updated: 2026-08-26

### Superseded lock record

The previous lock described Consolidated Blocker Remediation R1 as
`REMEDIATION_R1_COMPLETE_UNCOMMITTED`. That statement was already stale inside its own
commit: R1 was committed as `472f5c3` and pushed to
`origin/codex/impl/stable-record-v2-shadow-integration`. The stale line is corrected here
rather than by amending the reviewed commit, which must stay byte-identical.

### Independent review verdict and remediation status

```text
INDEPENDENT_REVIEW=PASS_WITH_NONBLOCKING_FINDINGS
BLOCKING_FINDINGS=0
REVIEWER_EDITED_CANDIDATE=NO
REVIEWED_CANDIDATE=472f5c389d57f91d35b50db8bdd0d96aa64ddf63
B1_SHORT_CJK_FALSE_POSITIVE=CLOSED
B2_STABLE_ID_NULL_LEAK=CLOSED
N1_EXPLICIT_SCOPE_FALLTHROUGH=CLOSED
N2_BLOCKED_RETRIEVAL_ASSERTED=CLOSED
N3_NEGATIVE_FAILURE_EXIT_GATE=CLOSED
N5_REINDEX_PREREQUISITE=DOCUMENTED
FORMAL_GOLDEN=21/21
FORMAL_NEGATIVE=23/23
ACCEPTANCE_RECORDED=YES
MAIN_READY=YES
MAIN_UPDATE_AUTHORIZED=NO
STABLE_RECORD_V2_ACTIVATED=NO
ROW_V1_RETIRED=NO
PRODUCTION_REINDEX_AUTHORIZED=NO
PRODUCTION_REINDEX_RUN=NO
SLACK_TAXONOMY_ACTIVATED=NO
MAIN_UPDATED=NO
```

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

#### Consolidated Blocker Remediation R1 (this round)

- **B1 — short CJK alias false positive: FIXED.** An alias of at most two characters that is not
  pure ASCII now binds a constraint only where the script itself supplies a boundary: at least one
  occurrence must sit outside a longer run of CJK characters. Deliberately not a minimum-length
  rule — the review proved two-character aliases fail the same way (`停業` inside
  `停業後重新開店`, `倉鼠` inside `倉鼠般`, `冰箱` inside `冰箱裡`).
- Suppression is not a new refusal: the query falls back to the planner's own non-taxonomy
  semantics, and no `ambiguity_flags` entry is raised — an ambiguity flag is read downstream by
  `search_aliases.resolve_exact_alias_parent_ids` as a reason to disable exact merchant-alias
  expansion, so inventing one would have narrowed an unrelated retrieval path.
- An explicitly typed field never reaches the rule; naming the field is the user supplying the
  boundary, so `sales_category_lv2=寵物` still resolves.
- **B2 — `stable_record_id: null` in governed Vault Markdown: FIXED at the shared boundary.** Added
  `models.governed_markdown_frontmatter()`, applied immediately before rendering in both
  originating writers (`apply_review_decisions._markdown_file_for_record` and
  `store_data_sync_plan_v2_execution._create_parent_markdown`). It omits the key only when the
  value is `None`; a real `MKA-MC-#####` is preserved.
- Writer inventory established and guarded by a test: five `metadata_dict()` callers, of which two
  write SQLite `metadata_json` (`indexing.py`, `store_data_sync_plan_v2_execution.py`), one builds
  a search-result payload (`retrieval.py`), and two render governed Markdown. `obsidian_sync`
  re-renders already-parsed frontmatter, so it propagates rather than originates the key.
- **N1 — explicit-field fall-through: FIXED, and in both modes.** The explicit parser now claims
  its matched span whatever the Authority says about the value, including `not_found`. Formal
  sweep: default (`taxonomy=None`) leaks 142/148 → 0/148; taxonomy-on 138/148 → 6/148.
- The residual 6 share one unrelated root cause: `EXPLICIT_CONSTRAINT_PATTERN` stops a value at
  whitespace, so `sales_category_lv2=電子 3C` claims only `sales_category_lv2=電子` and the ` 3C`
  left over is genuinely unclaimed text. Pre-existing at base, not fragment leakage, not fixed here.
- **N2 — blocked retrieval asserted: FIXED.** `expect_blocked` now requires
  `execution_blocked == true` AND `result_count == 0`; a violation reports
  `blocked_query_returned_results`. Covered by a must-fail test that makes retrieval return
  results despite a blocked plan.
- **N3 — Negative regression exit gate: FIXED.** `mka evaluate-search` exits non-zero when
  `golden_fail > 0` **or** `unexpected_failures > 0`. `expected_failure_reason` excuses a case only
  when the observed failure class equals the declared one exactly; it never changes a status.
- **N5 — re-index prerequisite: DOCUMENTED ONLY.** `O_CONTENT_INDEX_SPEC.md` §6b records that no
  sync receipt existing today carries the five binding fields the lineage gate requires, so a fresh
  reviewed `sync-obsidian execute` under candidate code is a precondition for any confirmed
  production re-index. No production sync was run.
- Case set: 39 → 44 cases (21 Golden, 23 Negative). `N-SHORT-01` is now an ordinary passing
  Negative; added `N-SHORT-03` (`狗屋設計`, 1-char), `N-SHORT-04` (`停業後重新開店的品牌`, 2-char
  inverted meaning), `N-SHORT-05` (`硒鼓耗材`, mineral character), `N-SHORT-06`
  (`倉鼠般忙碌的雙11`, simile) and `G-SHORT-01` (`冰箱`, standalone short alias still binds).
- The dataset now declares **no** known expected failure, and a test asserts that.

### In progress

- None; the R1 remediation is complete and uncommitted.

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

### B2 Real Writer Regression Test Hardening (Claude Code, 2026-08-26)

- Scope: `tests/test_stable_record_shadow.py` only. `src/` verified byte-identical to baseline
  `6002f0c` by `git diff --stat 6002f0c -- src/` returning empty.
- Targeted run: `tests/test_stable_record_shadow.py`, `tests/test_apply_review_decisions.py`,
  `tests/test_obsidian_sync.py`, `tests/test_content_index.py`,
  `tests/test_content_index_lineage.py`, `tests/test_row_v1_workbook_lineage_guard.py` —
  `151 passed, 3 skipped`. `test_stable_record_shadow.py` alone: 20 → `25 passed`.
- Skips: three pre-existing row_v1 lineage tests whose local-only workbook
  (`reports/excel_preview/…-20260708.xlsx`) is absent from this isolated worktree. Unchanged by
  this work.
- `tests/test_store_data_sync_plan_v2_execution.py`: `10 failed`, root cause
  `FileNotFoundError: …/obsidian_vault/MKA` — gitignored runtime state absent from this isolated
  worktree. The failure-name set is the recorded frozen-candidate set, and both `src/` and that
  test file are byte-identical to the baseline, so the set is definitionally unchanged by this WP.
  No production runtime state was created to make them pass; the two writers are covered by the
  new synthetic real-writer tests instead.
- Mutation probes: four runs against mutated copies under `/private/tmp`, never against the
  repository source. Baseline (pre-existing tests, both writers mutated) `20 passed` — the hole,
  reproduced. New set: both writers mutated `3 failed, 22 passed`; apply writer only
  `1 failed, 24 passed`; Managed Parent writer only `2 failed, 23 passed`. Each probe's pytest
  output was checked to confirm it imported the mutated copy, not the worktree.
- Import-origin guard: pytest resolves `marketing_knowledge_agent` to this worktree's `src`,
  confirmed from the module paths in the warning output.
- `compileall` on the changed test file and `git diff --check` both pass. No duplicate top-level
  definition was introduced into the test module.
- Not run: full application suite; standalone lint/type tools (not configured in this repo);
  production sync, re-index, deploy or UAT; independent review of this WP.

### Consolidated Blocker Remediation R1 (Claude Code, 2026-08-26)

- Targeted run (§12 set): `test_search_quality_evaluation.py`, `test_search_taxonomy.py`,
  `test_typed_query_retrieval.py`, `test_query_gating.py`, `test_pipeline.py`, `test_agentic.py`,
  `test_apply_review_decisions.py`, `test_store_data_sync_plan_v2_execution.py`,
  `test_stable_record_shadow.py`, `test_content_index.py`, `test_content_index_lineage.py`,
  `test_row_v1_workbook_lineage_guard.py`, `test_obsidian_sync.py`.
- Result: `10 failed, 341 passed, 4 skipped`. All 10 failures are in
  `test_store_data_sync_plan_v2_execution.py`, the failure-name set is **byte-identical** to the
  frozen candidate's, and the cause is
  `FileNotFoundError: .../obsidian_vault/MKA` — gitignored runtime state absent from this isolated
  worktree. Per instruction, no production ignored state was created; the Markdown serialization
  helper and both writers are covered by synthetic unit tests instead.
- Full suite: `137 failed, 1303 passed, 65 skipped, 72 errors`. FAILED/ERROR name set diffed
  against the frozen candidate `8af7382`: **0 new, 0 fixed** (209 pre-existing environment
  failures both sides). Passing count 1267 → 1303, i.e. +36 new tests.
- New tests: 15 short-CJK boundary tests in `test_search_taxonomy.py` (65 → 80), 6 governed-writer
  and inventory tests in `test_stable_record_shadow.py` (14 → 20), 15 explicit-isolation /
  blocked-retrieval / exit-gate tests in `test_search_quality_evaluation.py` (26 → 40 + 1 skip).
- Formal evaluation baseline, pinned Authority + scratch copy of the production index, run both
  through the harness and through `mka evaluate-search`: **44 cases, 21/21 Golden, 23/23 Negative,
  0 unexpected failures, 0 known expected failures, exit 0.**
- Live exit-gate demonstration: injecting one Negative case that must block but does not gives
  `golden_fail=0, negative_fail=1, unexpected_failures=1` and **exit 1**. The frozen candidate
  returned exit 0 for the same shape.
- B2 before/after through the real Managed Parent writer `_create_parent_markdown`: frozen
  candidate emits `stable_record_id: null`; R1 emits no key at all, and still preserves a resolved
  `MKA-MC-00001`.
- Adversarial §13 probes against the formal Authority and a scratch index copy: all 10
  false-positive queries bind no taxonomy constraint; all 14 must-still-work queries unchanged.
- `compileall` on every changed source and test file, `git diff --check`, and a JSON parse of the
  case set all pass.
- Read-only discipline: the production index and the Authority workbook are byte-identical before
  and after every run (`74b6038e…`, `7e6ecffc…`); no journal sidecar appeared; every measurement
  ran against a copy in `/private/tmp`.
- Not run: production sync, production re-index, deploy, UAT; standalone lint/type tools (not
  configured in this repo); the store-data-sync formal-runtime tests (blocked by absent gitignored
  state, unchanged from the frozen candidate).

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

## Acceptance and main integration preparation (2026-08-26)

The independent delta review of `472f5c3` returned **PASS_WITH_NONBLOCKING_FINDINGS** with
**0 blocking findings**, and the reviewer did not modify the candidate. The candidate is accepted
and prepared as a main integration candidate. Full record:
`docs/collaboration/REVIEW_SEARCH_TAXONOMY_R1_2026-08-26.md`; formal decision: `DEC-20260826-04`.

### Closed by this milestone

- **B1** — short-CJK taxonomy alias substring false positive, including the semantic inversion
  `停業後重新開店的品牌` → `sales_category_lv2=已關閉`.
- **B2** — `stable_record_id: null` leaking into the second governed Vault Markdown writer.
- **N1** — explicit-field fragment fall-through.
- **N2** — blocked evaluation now asserts `result_count == 0` directly, not by inference.
- **N3** — a Negative regression now fails `evaluate-search` with a non-zero exit code.
- **N5** — the re-index lineage prerequisite is documented (documentation only; no authorization).

### Accepted nonblocking backlog

Recorded, not fixed here. None of these is a regression introduced by this milestone; each was
verified to behave identically on the frozen candidate `8af7382`.

1. **Runtime catalog-path CJK substring matching.** The boundary rule applies to the Authority
   scan only. `_contains_exact_phrase` still matches non-ASCII catalog values by bare substring, so
   the six short indexed values (`寵物 美食 女裝 生鮮 男裝 玩具`) still bind from inside longer
   words in both modes — `少女裝扮風格` → `sales_category_lv2=女裝`. Side effect to remember when
   writing cases: those constraints are now attributed to `field_resolver`, not
   `search_taxonomy_authority`, so `forbid_taxonomy_constraint` alone no longer catches the class.
2. **Fragment-removal artificial boundary.** Removing a longer claimed alias can leave a short one
   free-standing: `會員回購率狗` binds `sales_category_lv2=寵物` after `會員回購率` is claimed.
3. **Explicit constraint whitespace truncation.** `EXPLICIT_CONSTRAINT_PATTERN` stops a value at
   whitespace, so `sales_category_lv1=電子 3C` claims only `sales_category_lv1=電子`. The query
   returns an empty result set silently (`execution_blocked=false`, no warning) rather than failing
   closed with an explanation — that quietness is the part worth fixing.
4. **LV1 canonical ambiguity** — a product/Authority semantics decision, split into two kinds in
   the blockers section below.
5. **Ingestion data-quality WP** — the two content tags the index and Authority disagree on.
6. **Real-writer B2 regression test hardening — CLOSED** by the B2 Real Writer Regression Test
   Hardening WP on branch `codex/test/b2-governed-writer-regression`, test-only. The finding was
   that the two writer tests in `test_stable_record_shadow.py` applied
   `governed_markdown_frontmatter()` themselves and asserted on their own output, so removing the
   fix from both writers left all 20 tests passing. Five tests that call `_markdown_file_for_record`
   and `_create_parent_markdown` directly now guard it, and each writer fails independently when
   its boundary call is removed.
7. **Slack taxonomy activation** — the Authority is still not wired to the Slack surface, so none
   of this fail-closed behaviour reaches end users yet.
8. **Golden/Negative dataset expansion** — v1 remains a 44-case smoke set.

### Production boundaries held by this milestone

```text
STABLE_RECORD_V2_ACTIVATED=NO
ROW_V1_RETIRED=NO
PRODUCTION_REINDEX_AUTHORIZED=NO
PRODUCTION_REINDEX_RUN=NO
SLACK_TAXONOMY_ACTIVATED=NO
MAIN_UPDATED=NO
```

## Next exact action

- Review the B2 real-writer regression tests on `codex/test/b2-governed-writer-regression`. They
  are uncommitted; nothing has been staged, committed or pushed.
- Then, separately, await explicit user authorization to promote
  `codex/integrate/stable-shadow-search-taxonomy` to `main`. The integration candidate is prepared and verified; nothing has been pushed to
  `main`. Do not activate Stable Record V2, retire row_v1, authorize a production re-index, or
  wire the taxonomy to Slack as part of that promotion — each is a separate decision.

### Nonblocking items R1 deliberately did not touch

- `EXPLICIT_CONSTRAINT_PATTERN` stops a value at whitespace, so `sales_category_lv2=電子 3C` claims
  only `sales_category_lv2=電子`. Pre-existing; changing it alters the explicit parser for every
  field and needs its own work package.
- An explicit field naming a value the Authority places in a *different* field (for example
  `sales_category_lv1=女裝`, where 女裝 is an LV2 canonical) produces a supported constraint that
  matches nothing rather than a refusal. Whether that should fail closed as
  `taxonomy_known_but_not_indexed` is a policy decision, not a defect R1 was asked to settle.
- Recall inside natural sentences: `我想找寵物案例` no longer binds a category, because `寵物` is
  embedded in a longer CJK run. Deliberate, documented in the taxonomy spec, and recorded here as
  a UX trade-off for UAT.
- The Search Taxonomy Authority is still not wired to the Slack surface, so none of this
  fail-closed behaviour is in effect for end users yet.

## Blockers and unresolved user questions

- The build-content-index lineage finding is closed by this development candidate; production re-index remains unauthorized until review and a separate explicit production decision.
- The `stable_record_id: null` row_v1 apply-output compatibility blocker is closed by the narrow serializer-boundary patch.
- Stable Record V2 activation, row_v1 retirement, and production re-index remain unauthorized.
- CLOSED by R1: the short-CJK alias false positive (`狗` inside `熱狗堡` binding `sales_category_lv2=寵物`). The independent review widened it beyond one-character aliases, and the boundary rule covers both lengths. `N-SHORT-01` now passes, and four more regression cases were added. The chosen fix was a boundary rule, not a minimum length and not an Authority edit.
- Related usability finding, NONBLOCKING per independent review, deliberately not changed by R1: five of the eleven indexed LV1 categories (`居家生活`, `寵物`, `家庭婦幼`, `已關閉`, `其他（非 SL 用戶）`) are cross-level ambiguous by their own canonical name and abstain unless the level is named or an expansion term is used. The review split them into two kinds, which need different decisions:
  - `寵物`, `已關閉`, `其他（非 SL 用戶）` are **canonical at both levels**. The Authority itself states the same name twice, so refusing is unambiguously correct; only an explicit field or an Authority-side rename resolves them. Do not touch the resolver for these.
  - `居家生活` and `家庭婦幼` are **LV1 canonical versus an LV2 expansion term** (of `居家生活相關` and `家庭婦幼相關`). `_preferred_entries` already encodes "a canonical outranks another row's expansion", scoped deliberately to within one field. Extending that same rule across fields would resolve these two deterministically. That is a product/Authority semantics decision and R1 did not make it.
- Existing finding, not a blocker for this candidate and not fixed here: the formal content index disagrees with the Authority on two content tags. Merchant sheet row 38 carries `直播串接（line` and `fb 等）` as two tags because Vault ingestion splits on `、`, which the Authority treats as a term character; row 106 carries a truncated `商店設計` where row 13 carries `商店設計（shop builder 等）`. Querying those Authority terms therefore fails closed as `taxonomy_known_but_not_indexed` rather than returning a partial match. Reconciling this is upstream Vault/ingestion work and needs its own decision.
- Unresolved user questions: none.

## Release or transfer

- Lock remains active through the development-first implementation candidate.
- Implementer transferred Codex → Claude Code on 2026-08-26 after Codex stopped at its usage limit; Codex had completed read-only discovery only, with no functional Search Taxonomy commit. Milestone anchor `5d4a21a327cf2dbb128e9ce21b07224d1e57bf84` unchanged.
- Search Taxonomy v1 was committed as `ef970ee57f9ce91b29a0604ff3b1b540e88110c1` and is the anchor this evaluation round builds on. Stable Record Shadow, Content Index Lineage Gate and Search Taxonomy v1 all remain frozen.
- Release/transfer: not requested in this round.
