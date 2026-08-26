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
re-read as a pass and its expectation is never bent to match current behaviour. v1 records one:

`N-SHORT-01` — the Authority holds nine one-character aliases (`狗 貓 魚 鳥 蛇 硒 鉀 鋅 鎂`). Because
CJK has no word boundary, `狗` matches inside `熱狗堡` and binds `sales_category_lv2=寵物`, returning
pet brands for a hot-dog query. Whether short aliases need a minimum length, a boundary rule, or an
Authority-side change is a decision for review; it is not relaxed here.

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
