# Search Quality Evaluation v1

## Purpose

The question this answers is not *did the parser return PASS?* but *what did the system actually do
when a user searched that way?* Every case is therefore observed at two levels at once — the typed
query plan and the retrieval it produced — because a plan that abstains correctly while the pipeline
still returns unrelated records is a failure the plan alone cannot show.

```text
Plan correct
!= Retrieval correct
!= Result set pure
!= Refusal honoured downstream
```

## Golden

A Golden case asserts that a query **should** produce a specific controlled constraint and results
that all satisfy it, or should resolve as a merchant identity.

Golden covers: LV1 canonical, LV1 expansion, LV2 canonical, LV2 expansion, content-tag canonical,
content-tag expansion, merchant name, merchant handle, a mixed identity-plus-taxonomy query, and an
explicitly scoped `sales_category_lv1=` / `sales_category_lv2=` query.

A Golden case with a hard taxonomy constraint passes only when:

- the expected field, canonical value and operator are all bound;
- the constraint is `supported` and is a hard filter;
- **every** returned record satisfies it. A correct top hit does not excuse unrelated records
  behind it;
- the result set is non-empty **when the index actually holds matching records**. Zero results
  against zero matching records is a coverage gap, not a search failure, and is classified as such.

## Negative

A Negative case does not assert "zero results". It asserts that the system **did not guess, did not
mis-bind, and did not quietly widen**.

Negative covers: cross-level ambiguity, intra-field collision, taxonomy-known-but-not-indexed
(free-text and explicitly scoped), an ingestion-damaged tag, a typo, an ambiguous typo, an unknown
term, a short generic alias inside an ordinary sentence, and a plain semantic question.

A Negative ambiguity or known-but-not-indexed case passes only when execution is blocked with the
expected abstain reason **and** retrieval returned nothing. A planner that reports ambiguity while
the pipeline still runs a similarity search is a failure, not a partial pass.

`known_but_not_indexed` and `unresolved_structured_lookup` are never conflated: the first means the
Authority knows the term and the index does not carry it, the second means nothing recognised it.

## Sampling

Deterministic, no randomness, no seed. Cases are drawn from the pinned Authority and the current
index by fixed rules:

- **Canonical**: sort a field's canonical values by normalized form; take the first that is both
  unambiguous and indexed, and separately the one carrying the most indexed records.
- **Expansion**: among expansion terms resolving to an unambiguous indexed canonical, sort by
  `(length, term)`; take the shortest ASCII, the longest ASCII and a short CJK term.
- **Ambiguity**: sort ambiguous aliases by normalized form, preferring those whose canonical is
  indexed.
- **Merchant**: sort indexed brand names by normalized form and take the first.

The Authority itself is not copied into the repository. The case set holds only the representative
terms a reviewer needs to read.

## Blocked cases are asserted at retrieval

`expect_blocked` asserts two things, not one:

```text
execution_blocked == true   AND   result_count == 0
```

Observing the plan alone cannot tell you a refusal actually refused. A plan can abstain while some
later stage still hands results to the caller, and that is precisely the failure a plan-only
assertion is structurally unable to see. `forbid_semantic_fallback` does not cover it either: it
fires only when results come back with no hard constraint at all, so a blocked plan returning
results *with* one would pass silently. A violation is reported as
`blocked_query_returned_results`.

## Failure classes

A pass rate alone hides which of these is moving, and they have different owners.

| Class | Owner | Meaning |
|---|---|---|
| `taxonomy_resolution` | this candidate | the Authority resolved a term wrongly, or not at all |
| `wrong_constraint` | this candidate | a constraint was bound with the wrong field, operator or scope |
| `merchant_precedence` | this candidate | identity lost to taxonomy, or failed to resolve |
| `unexpected_ambiguity` | this candidate / Authority | refused where it should have resolved |
| `unexpected_semantic_fallback` | this candidate | results returned with no hard filter where that is forbidden |
| `unexpected_result` | this candidate | impure result set, or empty despite matching records |
| `runtime_catalog_gap` | re-index decision | the Authority states the value; the index does not carry it |
| `data_quality` | upstream | the indexed value itself is wrong |
| `ingestion_quality` | upstream | ingestion damaged the value on the way in |

An unexpected refusal is classified by the plan's own abstain reason, so a block caused by an index
gap is not read as an ambiguity.

## What is a taxonomy bug, and what is not

**Taxonomy bug** — the Authority resolves a term to the wrong canonical; a constraint carries the
wrong operator; an explicitly scoped query acquires a constraint on the other level; identity is
rebound by vocabulary.

**Not a taxonomy bug, and must not be patched here**:

- an Authority value the index does not carry (`runtime_catalog_gap`);
- a value the index carries in a damaged form. Two are known: `直播串接（LINE、FB 等）` is stored as
  two fragments because ingestion splits on the ideographic comma, and `商店設計` appears alongside
  the full `商店設計（SHOP Builder 等）`. Both are `ingestion_quality`. Refusing these queries is the
  correct behaviour; no taxonomy special case may be added to make them appear to pass.

## Out of scope for v1

NDCG, MRR and any other graded ranking metric. For a pure semantic or free-text query the harness
records the ranking as a baseline and does not treat subjective relevance as a blocker. v1 scores
only constraint correctness, result purity, absence of false broad fallback, and identity
precedence.

## Recorded known gaps

A case the system currently fails carries `expected_failure_reason`, so it cannot be silently
re-read as a pass and its expectation is never bent to match current behaviour.

**As of Consolidated Blocker Remediation R1 (2026-08-26) there are none.** The one gap v1 recorded,
`N-SHORT-01`, was the short-CJK false positive: `狗` matched inside `熱狗堡` and bound
`sales_category_lv2=寵物`, returning pet brands for a hot-dog query. The short-alias boundary rule
closed it, and the case is now an ordinary passing Negative alongside `N-SHORT-03` (`狗屋設計`),
`N-SHORT-04` (`停業後重新開店的品牌`, semantically inverted), `N-SHORT-05` (`硒鼓耗材`, mineral
character) and `N-SHORT-06` (`倉鼠般忙碌的雙11`, two-character alias inside a simile).

`expected_failure_reason` remains a narrow instrument and is deliberately empty:

- It never changes a status. A declared case still reports `FAIL`.
- It excuses a case from the exit gate **only when the observed failure class equals the declared
  one exactly**. A case that starts failing for a different reason is a new regression wearing an
  old label, and is counted in `unexpected_failures`.
- A gap left declared after its defect is fixed is a standing exemption, so the case set is
  asserted to carry none.

## Exit gate

`mka evaluate-search` exits non-zero when either

```text
golden_fail > 0
unexpected_failures > 0
```

A Negative case is what stands between a refusal and a confident wrong answer, so a new Negative
failure fails the command exactly as loudly as a Golden one. `unexpected_failure_ids` and
`known_expected_failure_ids` are printed in the summary and the Markdown report so the difference
is visible without reading every case.

## Running it

```text
mka evaluate-search --db <index copy> --cases tests/fixtures/search_quality_cases.json \
    --search-taxonomy-workbook <workbook> --search-taxonomy-sha256 <sha256> \
    --output reports/search_evaluation
```

`--db` has no default: an evaluation must never fall through to whichever index happens to sit at
the conventional path. The index reader opens read-write, so a production index must be handed in as
a **copy**. Exit code 1 signals a Golden regression; report output lands under the untracked
`reports/` tree.
