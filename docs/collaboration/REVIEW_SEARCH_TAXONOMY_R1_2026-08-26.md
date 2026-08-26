# Independent delta review — Consolidated Blocker Remediation R1

- Date: 2026-08-26
- Reviewer: independent delta reviewer (Claude Code), read-only worktree
  `/private/tmp/mka-search-r1-delta-review`
- Original blocked candidate: `8af73821a237253af6617c5fbf81605b76349b10`
- R1 candidate under review: `472f5c389d57f91d35b50db8bdd0d96aa64ddf63`
- Reviewer edited candidate: `NO`
- Verdict: `PASS_WITH_NONBLOCKING_FINDINGS`
- Blocking findings: `0`

Scope was the delta `8af7382..472f5c3` only, plus the direct blast radius of the shared
primitives it touched. The previous milestone review was not repeated.

## Method note that changed the result

`pyproject.toml` sets `pythonpath = ["src"]`, which **overrides** the `PYTHONPATH` environment
variable. A first attempt to run R1's tests against the frozen candidate's source therefore
silently ran them against R1's own source and reported everything green. Every A/B measurement in
this record was redone either in a hermetic tree (frozen-candidate `src` + R1 `tests` + the same
`pyproject.toml`) or through non-pytest scripts that print the resolved module path. Anyone
repeating this review must do the same or the comparison is meaningless.

## B1 — short CJK alias false positive: CLOSED

All ten named probes bound a wrong taxonomy constraint on the frozen candidate and bind none on
R1, including the semantic inversion `停業後重新開店的品牌` → `sales_category_lv2=已關閉`.

The implementation is a genuine boundary rule, not a hardcoded query list or an alias special
case: an alias of at most two characters that is not pure ASCII binds only when at least one of
its occurrences sits outside a longer run of CJK characters.

Verified alongside it:

- standalone short aliases still resolve (`狗`, `冰箱`);
- an explicitly typed field never reaches the rule;
- suppression raises **no** `ambiguity_flags` entry — this matters because
  `search_aliases.resolve_exact_alias_parent_ids` reads that list as a reason to disable exact
  merchant-alias expansion, so inventing a flag would have narrowed an unrelated retrieval path;
- merchant identity precedence is intact, including `金魚藝術展`, whose name contains the
  one-character alias `魚`;
- ASCII short terms (`ux`, `tv`, `3c`) and long aliases are untouched.

The new tests discriminate: in the hermetic tree, `test_search_taxonomy.py` gives 6 failed / 74
passed against the frozen candidate's source and 80 passed against R1's.

## B2 — `stable_record_id: null` in governed Vault Markdown: CLOSED

Verified by calling the **real** writers, because the tests do not.

`store_data_sync_plan_v2_execution._create_parent_markdown`, unresolved identity:

- frozen candidate → key present, `stable_record_id: null` emitted;
- R1 → key absent entirely;
- R1 with `MKA-MC-00001` → value preserved verbatim.

Exactly one key is filtered: the frozen candidate renders 37 null-valued frontmatter keys, R1
renders 36. `metadata_dict()` and the SQLite `metadata_json` round-trip are unchanged, so the
shadow contract survives.

Writer inventory is complete. Both originating writers pass through
`models.governed_markdown_frontmatter()`; `obsidian_sync._synced_content` was tested and only
propagates already-parsed frontmatter, so it cannot originate the key; `asset_apply_plan` never
touches `DocumentMetadata`. All 114 governed Vault Markdown files were checked and none carries
the key, so there is no pre-existing contamination to clean up.

## N1 — explicit field fall-through: CLOSED

Measured independently rather than accepting the reported figures.

| Sweep | Frozen candidate | R1 |
| --- | --- | --- |
| 74 indexed taxonomy values, `taxonomy=None` | 6/74 leak | **0/74** |
| 74 indexed taxonomy values, taxonomy on | 1/74 leak | **0/74** |
| 96 Authority canonicals, true fragment fall-through | 0 | **0** |
| 96 Authority canonicals, whitespace-truncation artifacts | 3 | **2** |

The residual was classified mechanically by re-running `EXPLICIT_CONSTRAINT_PATTERN` and comparing
the claimed span against the normalized query. It is entirely whitespace truncation — a separate
pre-existing root cause that R1 in fact reduced by one — and not fragment fall-through.

## N2 / N3 — evaluation hardening: CLOSED

`expect_blocked` now requires `execution_blocked == true` **and** `result_count == 0`, asserted
directly on `result_count` rather than inferred from `forbid_semantic_fallback` (which only fires
when there is no hard constraint at all). The covering test monkeypatches retrieval to return
results against an already-blocked plan.

The exit gate was demonstrated live rather than read. Injecting one Negative case that must block
but does not:

- R1 → `golden_fail=0 negative_fail=1 unexpected_failures=1`, **exit 1**;
- frozen candidate, same case set → `negative_fail=7`, **exit 0**.

`expected_failure_reason` cannot swallow a different failure class: a case declaring
`ingestion_quality` but producing another class is still counted unexpected and still exits 1. And
because `status = "FAIL" if failure_reason else "PASS"`, a failing case always carries a non-null
reason, so there is no `None == None` amnesty hole. The dataset declares zero known expected
failures, which was confirmed against the file.

## Formal evaluation

Run against the pinned Authority and a scratch copy of the production index:
**Golden 21/21, Negative 23/23, 0 unexpected failures, exit 0.**

All six SHORT cases were spot-checked against their real observations rather than the summary.

Case-set integrity: 39 → 44 cases, 5 added, 0 removed, **0 weakened**. The only guard removed from
a pre-existing case is `N-SHORT-01`'s `expected_failure_reason`, which removes an *amnesty* — the
case simultaneously gained four tighter assertions. `N-SHORT-02` gained
`forbid_taxonomy_constraint`.

## N5 — documented, and factually correct

All twelve existing sync manifests were inspected: none carries any of the five lineage fields the
gate requires. The documented prerequisite is therefore real, and the wording correctly separates
`production_reindex_ready=true` from authorization.

## Nonblocking findings

1. **The B2 regression guard is tautological.** The two writer tests apply
   `governed_markdown_frontmatter()` themselves and then assert on their own output; neither calls
   `_markdown_file_for_record` or `_create_parent_markdown`. Proven by mutation: removing the fix
   from **both** writers leaves all 20 tests in the module passing. The fix is correct; nothing
   guards it.
2. **B1's root cause is half-remediated.** The boundary rule covers the Authority scan only.
   `_contains_exact_phrase` still matches non-ASCII catalog values by bare substring, so the six
   short indexed values (`寵物 美食 女裝 生鮮 男裝 玩具`) still bind from inside longer words in
   both modes — `少女裝扮風格` → `sales_category_lv2=女裝`, `男裝潢設計` → `sales_category_lv2=男裝`.
   Retrieval is byte-identical to the frozen candidate, so this is not a regression, but the
   `source` attribution moved from `search_taxonomy_authority` to `field_resolver`, which means
   `forbid_taxonomy_constraint` alone no longer catches the class.
3. **Fragment removal can manufacture a boundary.** `會員回購率狗` binds `sales_category_lv2=寵物`
   once the longer alias is claimed and removed. The frozen candidate bound it too.
4. **Explicit value whitespace truncation** returns an empty result set silently rather than
   failing closed with an explanation.
5. **Spec wording slightly overbroad.** `STABLE_RECORD_V2_SHADOW_INTEGRATION.md` says every
   governed Markdown writer renders through `governed_markdown_frontmatter()`; `obsidian_sync`
   does not, because it propagates rather than originates. Correct in substance, imprecise as
   written.
6. **No CI coverage for the Managed Parent writer's B2 fix in an isolated worktree.** The test
   that would exercise it is among the ten blocked by absent gitignored runtime state.

## Regression status

- 19 related test files on R1: **457 passed, 1 skipped, 0 failed**.
- `test_store_data_sync_plan_v2_execution.py` fails the **same 10** tests on both the frozen
  candidate and R1, caused by gitignored runtime state absent from isolated worktrees.
- `NEW_CORRECTNESS_REGRESSION=NO`, `NEW_PRODUCTION_SAFETY_REGRESSION=NO`.

## Read-only discipline

The production index and the Authority workbook were byte-identical before and after every read
(`74b6038e…`, `7e6ecffc…`), no journal/WAL/SHM sidecar appeared, and every measurement ran against
a scratch copy. Both review worktrees ended clean.

## Not verified

Full application suite was not re-run in full (19 files sampled at 0 failures, plus a matched
failure-set comparison for the environment-blocked file). Lint and type tools are not configured
in this repository. No production sync, re-index, deploy or UAT was performed — those are
forbidden at this stage.
