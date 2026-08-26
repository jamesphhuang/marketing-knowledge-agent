# Search Taxonomy Authority v1

## Scope

An externally pinned, read-only controlled vocabulary for exactly three search fields:

```text
sales_category_lv1
sales_category_lv2
content_tags
```

Nothing else. The Authority is never a production default, is never discovered, and is never
mutated.

```text
Pinned
!= Loaded by default
!= Indexed
!= Executable
```

`TAXONOMY_RESOLVED != INDEX_CONTAINS_IT`. The Authority answers *what does this term formally
mean?*; the runtime `QueryCatalog` answers *does the formal index actually carry that value?*
Both must say yes before a constraint is emitted.

## Trust and lineage contract

`load_search_taxonomy()` requires two explicit caller inputs and has no defaults:

1. a taxonomy workbook path;
2. an external expected sha256 pin.

The loader refuses, before indexing a single alias, when the path is a symlink, is missing, is not
a regular file, when the pin is not 64 hex characters, when the file's sha256 differs from the pin,
when the workbook is not readable as xlsx, when either sheet is absent, or when either sheet's
header shape differs from the contract. Neither the filename nor any workbook-internal metadata is
trusted; only the bytes are.

## Workbook schema (asserted, never inferred)

Sheet `Sales Category LV1 LV2`, header row 1, exactly four headers:

```text
Sales Category LV1 | Sales Category LV1 擴充詞 | Sales Category LV2 | Sales Category LV2 擴充詞
```

Sheet `內容相關標籤`, header row 1, exactly two headers after trailing blank header cells are
trimmed:

```text
內容相關標籤 | 內容相關標籤 擴充詞
```

The content-tag sheet carries a third column with a blank header holding reference URLs. It is
outside this contract and is excluded by construction: trailing blank headers are trimmed, so the
column is never read. A *named* third header is a schema change and fails the load.

The two Sales Category column pairs share one sheet but are independent vocabularies. LV2 rows
continue past the end of the LV1 column, so LV2 parentage is never inferred from row adjacency.

Expansion cells are comma-separated on `,` and `，` only. The ideographic comma `、` is a term
character, not a separator: the canonical content tag `直播串接（LINE、FB 等）` contains one.

The loader additionally refuses expansion terms with no canonical owner, a canonical value repeated
inside one field, and a field with no canonical value at all.

## Normalization

Reuses `normalize_query_text()` unchanged: Unicode NFKC, whitespace collapse, trim, casefold.
No simplified/traditional conversion, no translation, no stemming, no LLM synonym generation, no
substring inference beyond the parser's existing exact-phrase rule.

Canonical *display* values are preserved verbatim, including trailing whitespace (`"居家生活 "`,
`"Grocery "`). The emitted constraint carries the value the formal index holds, not the Authority's
display value, because ingestion strips what the workbook keeps.

## Resolution contract

```text
normalized alias -> taxonomy field -> canonical value
```

A canonical value resolves to itself. Within one field a canonical name outranks another row's
expansion list, because a canonical name is the Authority's own primary statement of that value.
That is the only precedence rule, and it is structural — never workbook row order, never Excel
order, never similarity.

Resolution refuses rather than chooses when:

- one normalized alias names two canonical values inside one field;
- one normalized alias names values in two or more fields.

Passing an explicit `field` restricts resolution to that domain, which answers a cross-level
collision. It does not rescue a two-canonicals-in-one-field collision, which stays ambiguous.

## Query planner integration

`build_query_plan(raw_query, catalog, taxonomy=None)`.

`taxonomy=None` is the existing contract, byte-for-byte: the runtime catalog and the curated
`CATEGORY_ALIASES` map remain the only alias sources. A supplied Authority becomes the alias source
for its three fields and `CATEGORY_ALIASES` is skipped, so the two never answer the same question at
once. The catalog remains the existence authority: an indexed value the Authority does not state
still resolves through the existing `field_resolver` path.

Order of operations:

1. Explicitly typed `sales_category_lv1=…` / `sales_category_lv2=…` / `content_tags=…` resolve
   through the Authority inside the named domain. The matched span is claimed **whatever the
   Authority goes on to say about the value**, including when it says nothing. Naming the field is
   the user stating which field that text belongs to, so no later stage may read it again; without
   this an explicitly scoped `sales_category_lv1=女裝` also acquired an LV2 filter on the same
   text. This holds with or without an Authority supplied.
2. Free text is scanned only after identity resolution has claimed its fragments, so a merchant
   brand or handle that happens to equal a taxonomy term is never re-read as vocabulary.
3. The scan takes the longest matching alias, removes it, and repeats up to `TAXONOMY_SCAN_LIMIT`
   times. Length is a property of the terms, so `美食相關` does not also register the `美食`
   inside it. Claimed fragments are removed before the catalog pass, so the catalog does not
   re-read a shorter value inside a term the Authority already spent.

## Short CJK alias boundary rule

`_contains_exact_phrase` asserts a boundary for ASCII terms, which is why `tv` does not match
inside `tvbs`. CJK writes without spaces, so no such boundary exists, and the Authority holds nine
one-character aliases (`狗 貓 魚 鳥 蛇 硒 鉀 鋅 鎂`) plus 386 two-character ones.

An alias whose normalized form is at most `SHORT_CJK_ALIAS_MAX_LENGTH` (2) characters and is not
pure ASCII therefore binds a constraint only where the script itself supplies a boundary: at least
one occurrence must sit outside a longer run of CJK characters. Anything longer, and anything
ASCII, is untouched.

| Query | Alias | Bound |
| --- | --- | --- |
| `狗` | `狗` | `sales_category_lv2=寵物` |
| `熱狗堡品牌的案例` | `狗` | nothing |
| `停業後重新開店的品牌` | `停業` | nothing |
| `冰箱` | `冰箱` | `sales_category_lv2=家電/相機/影音硬體設備` |
| `tv` | `tv` | unchanged; ASCII keeps its own boundary |

Two properties matter as much as the rule:

- An explicitly typed field never reaches it. Naming the field is the user supplying the boundary,
  so `sales_category_lv2=寵物` still resolves.
- Suppression is **not** a new refusal. The query is handed back to the planner's own non-taxonomy
  semantics — usually `unresolved_structured_lookup`, exactly what the same query did before any
  Authority existed. No taxonomy ambiguity is invented, and in particular no `ambiguity_flags`
  entry is added: an ambiguity flag is read downstream as a reason to disable exact merchant-alias
  expansion, so raising one here would narrow an unrelated retrieval path.

The cost is recall inside natural sentences: `我想找寵物案例` no longer binds a category, because
`寵物` there is embedded in a longer CJK run. That is deliberate. Recovering it needs tokenization
this parser does not have, and the alternative — answering confidently from an accidental substring
— is the failure this system exists to avoid. Recorded as a UX trade-off for UAT.

Emitted operators are unchanged from `FIELD_REGISTRY`:

```text
sales_category_lv1 -> canonical_exact
sales_category_lv2 -> canonical_exact
content_tags       -> contains_exact_tag
```

An expansion term is never used as a filter value. Only the canonical value is.

## Fail closed

Two new abstain reasons, both of which block execution:

```text
ambiguous_taxonomy_term
taxonomy_known_but_not_indexed
```

Both outrank `unresolved_structured_lookup` in the abstain chain, and the query mode is forced to
`structured_lookup`. This is deliberate: `allow_semantic_fallback()` may clear
`unresolved_structured_lookup` when a caller supplies filters, and clearing a recognised-but-unusable
taxonomy term would turn a refusal into the broad semantic search this parser must never run.

A term the Authority does not know keeps existing behaviour; it is not a taxonomy refusal.

## Typo handling (v1)

Suggestion only. Deterministic edit-distance-one matches against the alias index, offered as a
`parser_warning` and a `taxonomy_typo_suggestion:` flag. It creates no constraint, decides no field,
sets no abstain reason, and cannot pick a side of an ambiguity — a term one edit from two canonical
values yields two suggestions. Suggestions are withheld for a query carrying semantic markers, so an
ordinary question is not given typo noise.

## Merchant identity precedence

Taxonomy alias authority is not merchant alias authority. `search_aliases.py` semantics, the
merchant alias projection and the MKA-MC-00045 alias decision are untouched. Identity resolution
runs first and removes its fragments before any taxonomy scan, so a brand whose name equals a
taxonomy term resolves as a merchant.

## Opt-in surface

Pipeline: `search_index`, `ask_index`, `agent_ask`, `explain_query` and `build_index_query_plan` all
accept `taxonomy=None`. The object is loaded once and passed down; the workbook is never re-read per
query. `agent_ask` builds one plan and hands it to every sub-query, so the agent answers to the same
Authority the outer question did.

CLI: `--search-taxonomy-workbook` and `--search-taxonomy-sha256` on `search`, `ask`, `agent-ask` and
`explain-query`. Both or neither; supplying one alone fails. There is no production default.
`explain-query` reports which Authority answered, or that none was pinned.

## Out of scope for v1

Production defaults, Authority mutation, production re-index or deploy, Stable Record V2 activation,
row_v1 retirement, merchant-alias rebinding, and any auto-correction of user input.
