# Slack Faceted Search MVP

Status: implemented, uncommitted-to-main, default OFF. Branch
`codex/impl/slack-faceted-search-mvp`. No production activation, no UAT activation, no production
re-index. This document is the read side of the milestone; task-lock and verification records live
in `docs/collaboration/CURRENT_WORK.md`.

## 1. Problem and goal

The Search Taxonomy Authority (`docs/specs/SEARCH_TAXONOMY_AUTHORITY_V1_SPEC.md`) makes natural
-language search fail closed rather than guess, which is correct but costs recall: a query has to
name a value in a way the Authority resolves unambiguously, and the formal source data leaves
little slack to search against in the first place --

- 121 rows total; 106 non-closed, minimally-usable candidates;
- 8 distinct interview years (2019-2026);
- 40 canonical Sales Category LV2 values, of which only 22 are ever carried by an eligible record;
- 44 canonical content tags, of which only 37 are ever carried;
- the median (year, LV2) combination has exactly 1 matching record, and 53 of 75 observed
  combinations have exactly 1.

That last fact is the design constraint: with candidate sets this thin, *neither year nor LV2 can
be mandatory* -- a UI that forces both would empty-result far more often than it would help, and
LV1 is dropped from the surface entirely (see `L_RETRIEVAL_CITATION_ACCURACY_REVIEW.md` and
`CURRENT_WORK.md`'s blockers section on LV1 cross-level ambiguity; this MVP sidesteps that decision
by never exposing LV1 to Slack at all).

The goal is a structured entry point that removes the ambiguity problem for the three fields a user
is most likely to already know precisely -- interview year, Sales Category LV2, content tags --
while keeping natural language for everything else (brand name, metric goals, free-form intent).

## 2. UI contract

- `@Bot 搜尋` or `@Bot 條件搜尋` (exact match after trim) replies with one Block Kit button,
  "開啟條件搜尋". No retrieval, no governance decision, no audit row: opening the button discloses
  nothing not already visible in the channel.
- The button opens a modal ("案例條件搜尋") via `views_open`, built fresh from the live
  `FacetCatalog` every time.
- The modal has four optional fields: 採訪年份 (`multi_static_select`, max 3), Sales Category LV2
  (`multi_static_select`, max 3, LV1 never offered), 內容相關標籤 (`multi_static_select`, max 3),
  and 你想找什麼內容或成果 (`plain_text_input`, multiline, `max_length` = `FREE_TEXT_MAX_LENGTH`).
  At least one must be non-empty, enforced by a `view_submission` block-level error, not by making
  any one field required. A facet with zero eligible options is omitted from the modal entirely
  rather than shown empty.
- Submitting posts the result to the original channel/thread as one message (reusing the existing
  structured-result renderer and the existing `SlackPaginationStore`, so 「顯示更多」 continues to
  work exactly as it does for natural-language search), followed by a second, short message
  carrying a "調整條件" button. That button reopens the same modal prefilled with the prior
  selection.
- **Block Kit limits are asserted, never assumed, and every one of them fails closed.** The
  "調整條件" button carries an opaque token, not the request: Slack caps a button `value` at 2000
  characters and an embedded maximal request measured 3206, which makes `chat.postMessage` reject
  the whole message so the user gets no button at all. The request itself lives in
  `slack_request_tokens` (bounded by TTL and entry count, in-memory, process-local, mirroring
  `slack_pagination`); an expired token reopens an *empty* modal rather than a reconstructed one,
  because prefilling filters the user never chose is worse than prefilling nothing. A
  `multi_static_select` carrying more than 100 options raises rather than truncating — today's
  counts (8 / 22 / 37) sit far below that, and crossing it means the "static options at
  modal-open time" premise no longer holds and the field needs an `external_select` data source.
  Truncating would hide eligible values from every user with no visible symptom.
- The original `@mention` free-text flow, its pagination, and approved-asset-URL behaviour are
  unchanged; none of this is reachable unless `enable_faceted_search` is on, and none of it changes
  how the plain NL path behaves when it is on.

## 3. `FacetCatalog` (`search_facets.py`)

A read-only snapshot of what the modal may offer, built once (at bot startup in this MVP) from two
inputs this process already trusts read-only: the pinned Search Taxonomy Authority and the live
content index.

- `interview_years`: every year carried by at least one eligible document, newest first.
- `sales_category_lv2` / `content_tags`: every value that is *both* an Authority canonical value
  *and* actually carried by at least one eligible document -- the intersection, not either side
  alone. LV1 has no field on this type at all; it is structurally impossible for the modal to offer
  it.
- Eligibility mirrors exactly what an external Slack query is allowed to retrieve:
  `SearchFilters(intent="external")` gating (published status, public classification, quotable,
  non-`pending_metric`), the `NON_RETRIEVABLE_RECORD_TYPES` exclusion, and the restricted-customer
  denylist (`filter_restricted_results`). A value whose only carriers fail any of these never
  appears as an option.
- **Both inputs are required and both fail closed** (`load_required_governance_index`,
  `assert_readable_content_index`). `pipeline.load_restricted_customers_governance_index` is
  deliberately forgiving — a missing file yields `(None, warning)` — which is the right default for
  an offline developer query and the wrong one here: a catalog computed without a denylist can
  offer a facet value whose only carrier is a restricted customer, disclosing that the customer
  exists through the option list alone, before any search runs. Three shapes are refused: the file
  is absent/unreadable; it does not parse; or it parses as JSON but is **not a list**. The last is
  the dangerous one — upstream's record comprehension then iterates something yielding no dicts and
  returns an empty denylist *with no warning at all*, indistinguishable downstream from a genuinely
  empty one. A missing content index is likewise refused before opening, because `sqlite3.connect`
  would otherwise create an empty database at that exact path — a write, by a read-only surface, at
  the moment it is least expected.
- Counted by distinct `document_id`, never by chunk count, so a long case does not outweigh a short
  one.
- `catalog_version` is a pure function of the Authority's pinned sha256, a hash of the content index
  file's own bytes (`content_index_generation_id`), and this builder's own schema version. Any of
  the three changing changes the version. Eligible-record counts are computed to decide inclusion
  and are available on the catalog for tests/operators, but the modal itself never displays them --
  showing a count would be a small but real information disclosure this MVP does not need.
- Never writes to the workbook, the Vault, or the content index.

## 4. `StructuredSearchRequest` and plan building (`structured_search.py`)

```
StructuredSearchRequest
- interview_years: Tuple[int, ...]
- sales_category_lv2: Tuple[str, ...]
- content_tags: Tuple[str, ...]
- free_text: str
- catalog_version: str
```

`validate_structured_search_request(request, catalog)` re-validates every field server-side against
the live `FacetCatalog` -- never trusting Slack's displayed option text, only the option *value*,
and never trusting that a value valid under an old catalog is still valid under the current one. A
`catalog_version` mismatch is `StaleFacetCatalogError`, refused before anything else; a stale
submission is never "upgraded" by re-running against the new catalog silently. `free_text` is
bounded at `FREE_TEXT_MAX_LENGTH` here as well as in the Block Kit element, and an over-long one is
**refused, never truncated**: silently shortening a stated goal would run a different search than
the user asked for without saying so, and a Block Kit constraint is a property of the payload Slack
sent rather than a fact this process may assume about it.

`build_structured_query_plan(request, query_catalog, taxonomy)` builds a `TypedQueryPlan` directly:

- each modal-selected field becomes exactly one hard `QueryConstraint`, using an operator the field
  registry already declared but nothing previously executed -- `"in"` for `interview_year` /
  `sales_category_lv2`, `"contains_any"` for `content_tags` (see §6). Multiple selections in one
  field are therefore OR'd by construction;
- the plan's constraints for different fields are AND'd, per the existing `TypedQueryPlan.operator`
  contract (fixed at `"AND"` here);
- the request is never serialised back into a natural-language string and handed to the free-text
  parser -- that would reopen every ambiguity the parser exists to refuse, for a value the user
  already stated unambiguously by selecting it;
- `free_text`, if present, is parsed once by the existing `build_query_plan`, but only for fields
  the modal left untouched. `build_query_plan` gained an additive `preresolved_fields` parameter for
  this: a taxonomy field named there is never reopened by the Authority, whether through an explicit
  `field=value` mention or the ordinary catalog scan, and any residual constraint the parser still
  produces for a modal-selected field is dropped besides. A free-text signal that only concerns
  fields the modal already decided (an ambiguity fully covered by `preresolved_fields`, or a "this
  whole free text alone did not resolve to anything" refusal) is not allowed to veto a search that
  already has real modal-selected structure; a signal about a field the modal left undecided still
  blocks.

## 5. Retrieval order and zero-result behaviour (`structured_search.execute_structured_search`)

Order is always: channel/governance validation (in the Slack handlers, §7) → structured hard
filters → lexical/semantic scoring on the survivors → dedupe/presentation. Never the reverse.

- When `free_text` is non-blank, execution reuses `pipeline.ask_index` with the pre-built plan
  passed in as `query_plan`: `SQLiteRetriever.search` already filters by the hard-constrained plan
  *before* it scores anything (this was already the retrieval contract; nothing here changes it),
  so the free text only ranks candidates the modal's facets already admitted.
- When `free_text` is blank, this is a pure structured browse: there is nothing to rank, so results
  are ordered deterministically -- newest interview year first, then a stable per-record id
  (`source_sheet:r{row}`, falling back to `document_id`) -- instead of depending on the content
  index's own row order, which is unspecified for this purpose and must not be treated as meaningful
  by a caller.
- A zero-result search reports the filters that were actually applied (`structured_result.query_plan
  .hard_filters`) and never relaxes them automatically. There is no complex auto-relaxation ranking
  in this MVP; the "調整條件" button is the relaxation mechanism, and it is manual.
- Restricted-customer, non-retrievable-record-type and pending-metric records are excluded the same
  way for both execution paths (`filter_restricted_results`, `matches_filters`,
  `apply_intent_gating`), and `enforce_external_citations`/`apply_governance_to_answer` are applied
  in both, not only the `ask_index` path.

## 6. New operator semantics in `query_planning.py`

The field registry already declared `"in"` as an allowed operator for `sales_category_lv1`/
`sales_category_lv2` and `"contains_any"` for `content_tags`; nothing previously executed them.
`_metadata_matches_constraint` now does, additively -- the pre-existing scalar operators
(`"canonical_exact"`, `"contains_exact_tag"`, `"eq"`, ...) are completely unchanged, and every
existing single-value constraint continues to behave exactly as before.

## 7. Governance boundary

- Every entry point re-validates `allowed_channel_ids` independently: the original `app_mention`
  handler, the button-click action handler (`open_faceted_search_modal`), and the view-submission
  handler (`faceted_search_modal`) each check the channel carried in their own payload. A DM/MPIM
  is refused the same way it always was; nothing about a button or a modal bypasses that.
  channel/thread routing is carried explicitly in the button's `value` and the view's
  `private_metadata`, never inferred from a Slack field this MVP does not otherwise depend on.
- `private_metadata` carries only `channel_id`, `thread_ts` and `catalog_version` -- no workbook
  path, no hash, no taxonomy content, no query result.
- `execute_structured_search` reuses `pipeline.ask_index`'s existing restricted-query precheck (for
  the free-text half) and the same restricted-customer/denylist filtering described in §5 for both
  paths; there is no second, parallel retrieval pipeline that governance could be bypassed through.
- The denylist is also loaded once at startup purely to fail fast, so a bot that cannot read it
  never reaches the point of accepting a query — rather than discovering the fault on the first
  search and answering it anyway. This is scoped to the faceted surface: the natural-language
  path's existing "denylist missing → warn on the answer" behaviour is a frozen contract this WP
  does not change, and `enable_faceted_search=false` still starts without a denylist file.
- **A refused query's text is never written down.** When the free-text goal hits the denylist,
  `ask_index` returns before any retrieval; `is_restricted_refusal` detects that and the
  `slack_faceted_search` row is skipped **entirely**, exactly as the natural-language path already
  skips `slack_qa` on a refusal. The refusal is still recorded and still attributable:
  `execute_structured_search` forwards `query_audit_metadata`, so `precheck_restricted_query`
  writes `denylist_query_hit` under the Slack schema with `channel_id`/`user_id` and an empty
  query column, instead of falling back to the bare `command,event,match_count` schema that
  records neither.
- A `slack_faceted_search` audit event is appended to the existing Slack audit CSV schema (same
  header, same file) recording only the structured facet selection, the catalog version, and the
  free-text goal — the same class of content the pre-existing `slack_qa` event already records for
  natural-language queries. The audit CSV's broader schema-risk is a pre-existing, out-of-scope
  follow-up (see the "Non-goals" section below).
- Approved asset URLs behave identically to the natural-language path: the same
  `enable_approved_asset_urls` flag, the same refusal guard (a refused answer has nothing to
  enrich and is not enriched), and the same payload-free
  `APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE` audit code when the authority cannot be verified —
  which never aborts the search.
- Every submission supersedes whatever its thread was previously paging through, on **every**
  branch — including a refusal or an unstructured reply that produces no pages at all. The discard
  happens before the reply is sent, so 「顯示更多」 can never resume a result the user has already
  moved on from in a thread whose latest search was refused outright.

## 8. Feature flag and activation

`SlackConfig` gains three fields, all optional and default-OFF:

```
enable_faceted_search: bool = False
search_taxonomy_workbook: Optional[str] = None
search_taxonomy_sha256: Optional[str] = None
```

`load_slack_config` refuses a lone workbook path or a lone hash regardless of the flag (an
inconsistent pair is never silently accepted), and refuses `enable_faceted_search=true` without
both. When the flag is off, `run_slack_bot` never loads a taxonomy, never builds a facet catalog,
never registers the `open_faceted_search_modal` action handler or the `faceted_search_modal` view
handler, and `handle_slack_event` never recognises the trigger phrase -- the existing behaviour is
unchanged bit-for-bit, not merely "close enough."

When the flag is on, `run_slack_bot` calls `load_search_taxonomy` and `build_facet_catalog` exactly
once, before the `slack_bolt` `App` or `SocketModeHandler` is constructed. Any failure (missing
workbook, hash mismatch, unreadable content index) propagates unchanged and stops startup before
Socket Mode opens; there is no fallback that silently disables the feature and continues.

This work package implements and unit-tests the feature. It does not:

- modify the production `.mka/slack_config.json`,
- start, stop, or restart the running Slack UAT bot,
- run a production sync or re-index,
- activate Stable Record V2, retire row_v1, or authorize a production re-index,
- decide the LV1 cross-level-ambiguity question left open elsewhere in `CURRENT_WORK.md`.

A controlled UAT enablement is a separate, explicitly authorized follow-up step.

## 9. Non-goals and known follow-ups

- Complex automatic filter-relaxation ranking when a search returns zero results (deliberately out
  of scope; "調整條件" is the manual relaxation path for this MVP).
- Periodic facet-catalog refresh while the bot process is running; this MVP builds it once at
  startup. `catalog_version` staleness checking is already wired for when that changes, but nothing
  currently changes it mid-process.
- Reconciling the audit CSV's broader schema risk (tracked separately; unchanged by this WP beyond
  adding one more event name inside the existing schema).
- Merging this WP's `search_taxonomy_workbook`/`search_taxonomy_sha256` `SlackConfig` fields with
  the separate, independently-developed Search Taxonomy Slack-wiring work-in-progress (different
  branch, uncommitted there): if both reach `main`, their `SlackConfig` additions need to be
  reconciled into one taxonomy-loading contract rather than two.
- The `EXPLICIT_CONSTRAINT_PATTERN` whitespace-truncation and the LV1 cross-level-ambiguity
  decision, both already tracked in `CURRENT_WORK.md`, are unaffected by this WP.
