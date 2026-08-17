# Slack mrkdwn Rendering Cleanup — Production Activation Acceptance

Date: 2026-08-17

This record is a governance and operational acceptance document. It records a completed
production activation of an already-integrated presentation fix. It changes no application
code, no tests, no runtime config, and no index, and it authorizes no further change beyond
the state described here.

## 0. Evidence Classes

Every claim in this document carries one of three evidence classes. They are not
interchangeable, and no claim is promoted from a weaker class to a stronger one.

| Class | Meaning | How it was established |
| --- | --- | --- |
| **A — Repository-verified** | Confirmed in this document task by read-only inspection of the frozen worktree | `git show`, `git diff`, `git rev-list`, `grep`, file reads |
| **B — Independent-review recorded** | Carried forward from the completed independent review of the candidate | Prior review disposition, not re-adjudicated here |
| **C — Operator-observed production** | Reported by the operator from the live production runtime | Operator's own observation; **not** re-executed by this document task |

This document task performed **no** production operation. No Slack Bot was started or
stopped, no Slack message was sent, no Slack API was called, no production query was issued,
no index was read or rebuilt, and no network side effect was produced. Every production
statement below is class C.

## 1. Executive Acceptance Decision

```text
SPRINT_ACCEPTANCE = PASS
PRODUCTION_ACTIVATION_ACCEPTED = YES
```

The Slack mrkdwn rendering cleanup sprint has completed implementation, independent review,
fast-forward integration into `main`, and controlled production activation. Two production
smoke queries confirm that the seven asset field labels no longer emit formatter-owned raw
mrkdwn markers to the user, and that approved asset URL behaviour is unchanged for the
merchants observed.

This acceptance covers the activation described below. It does **not** authorize schema
changes, re-indexing, authority rebuilds, approved-URL feature changes, or remediation of
any residual listed in section 12.

## 2. Scope and Non-Scope

### In scope (class A)

The accepted candidate touches exactly four files:

| File | Role |
| --- | --- |
| `src/marketing_knowledge_agent/slack_presentation.py` | The only application-code change |
| `tests/test_slack_search_presentation_v1.py` | New regression coverage |
| `tests/test_slack_interface.py` | Existing label expectations realigned |
| `tests/test_typed_query_retrieval.py` | Existing label expectations realigned |

Verified with `git show --stat --name-only 5277060`.

Behavioural scope is the presentation of the seven asset field labels plus two adjacent
formatter-owned escaping boundaries (dynamic line breaks, dynamic backticks in code-span
context), and the source field's escaping boundary.

### Explicitly not in scope (class A)

| Surface | State |
| --- | --- |
| Approved asset URL selection | UNCHANGED |
| Approved URL authority binding | UNCHANGED |
| Governance / denylist / refusal logic | UNCHANGED |
| Article / video separation logic | UNCHANGED |
| Runtime config (`.mka/slack_config.json`) | UNCHANGED — and untracked; not reachable from a repository commit |
| Content index (`.mka/content_index.sqlite`) | UNCHANGED |
| Excel source data | UNCHANGED |
| `& < >` entity escaping | UNCHANGED |

The approved-URL feature flag `enable_approved_asset_urls` lives in
`src/marketing_knowledge_agent/slack_interface.py` and is not among the four candidate
files (class A).

The single URL-adjacent line in the diff is the `連結` label prefix only; the
`_slack_link(asset['url'])` call itself is byte-identical to the predecessor (class A).

## 3. Authoritative Code State

| Item | Value |
| --- | --- |
| Repository | `jamesphhuang/marketing-knowledge-agent` |
| Authoritative `main` | `527706036d69378c32f57ce29283a83e916494ef` |
| Accepted predecessor (parent) | `c2cf13a90a7b04bfef7b6cab07cb1a2025681c21` |
| Candidate subject | `fix(slack): stop emitting mrkdwn markers Slack renders verbatim` |
| Documentation worktree | `/private/tmp/mka-slack-mrkdwn-prod-freeze` |
| Documentation branch | `codex/docs/slack-mrkdwn-production-acceptance-freeze` |
| Frozen base of this document | `527706036d69378c32f57ce29283a83e916494ef` |

This document was authored on a clean worktree at the frozen base. `git status --short` was
empty before authoring (class A).

## 4. Root Cause and Accepted Fix

### Root cause (class A / class B)

Slack closes a bold run only when the character following the trailing `*` sits on a
delimiter boundary. Every asset field label was emitted as `` `*標題：*` `` immediately in
front of its dynamic value, so the closing `*` landed on the first character of that value.
For all seven labels that first character is a CJK character or a digit — a word character,
not a delimiter — so Slack never closed the run and displayed the markers to the user
verbatim.

This is consistent with what production reported: only the asset header
(`` `*文章 [1]*` ``, whose closing `*` ends the line) and `連結` when a rendered link
followed it ever bolded correctly.

### Accepted fix (class A)

1. The seven asset field labels — 標題, 連結, 上線日期, 採訪年份, 狀態, 對外引用, 資料來源 —
   no longer carry bold mrkdwn, so their rendering can no longer depend on the dynamic value
   that follows them.
2. The asset header retains its bold, because its closing `*` ends the line and therefore
   always sits on a valid boundary.
3. Dynamic line breaks and control characters (C0/C1, DEL, U+2028, U+2029) collapse to a
   single space via `MRKDWN_LINE_BREAKS`, so a dynamic value can no longer escape the
   blockquote and have its remainder read as a field the formatter never wrote.
4. In code-span context, a dynamic backtick is replaced with U+02CB (`ˋ`) instead of a
   backslash escape. Slack has no backslash escape, so the previous `` \` `` reached the user
   as a literal backslash while still closing the code span early.
5. The `資料來源` value now passes through `_normal_text` — the normal-text escaping boundary
   used by the free-form dynamic asset fields. It was previously rendered unescaped. `對外引用`
   is deliberately not in that group: it renders a fixed string produced by
   `_external_usage_label`, not free-form dynamic text, so it is not described here as an
   escaped dynamic value.
6. `& < >` entity escaping is unchanged, so dynamic text still cannot construct a Slack
   hyperlink.

### Regression coverage (class A)

Nine new test functions were added to `tests/test_slack_search_presentation_v1.py`. Two are
parametrized (6 cases and 4 cases respectively), giving **17 collected regression cases**.
This count was re-derived here by reading the added parametrize lists, and matches the count
recorded by the independent review.

The four parametrized URL cases pin exactly the four approved asset URLs later exercised by
the production smokes in sections 9 and 10, with article and video pinned separately for each
merchant.

**All three** test files carry label-expectation realignments matching the new formatter
output — `tests/test_slack_search_presentation_v1.py` as well as
`tests/test_slack_interface.py` and `tests/test_typed_query_retrieval.py`. The realignments
are of the same kind in every file (`> *連結：*` → `> 連結：`, and the code-span backtick
expectation from the previous backslash form to U+02CB).
`tests/test_slack_search_presentation_v1.py` is the only one that *also* carries the new
regression coverage described above; its pre-existing assertions were realigned alongside.

Confirmed by reading all three diffs (class A):

| Property | State |
| --- | --- |
| Existing assertion weakened or removed | NONE |
| Test deleted | NONE |
| `xfail` added | NONE |
| `skipif` added | NONE — the two in `tests/test_typed_query_retrieval.py` are pre-existing and untouched |
| Surrounding assertions — occurrence counts, absence of `開啟連結`, resolved URL lists, citation URLs, audit rows | UNCHANGED |
| Article / video URL separation assertions | PRESERVED |

One assertion was strengthened rather than weakened: alongside the realigned backtick
expectation, a guard was added that no literal backslash-backtick sequence survives in the
rendered output.

## 5. Independent Review Disposition

The independent review is complete. It is recorded here, not re-adjudicated (class B).

```text
FINAL_REVIEWER_DISPOSITION = APPROVE_FOR_PUSH_GATE
```

Recorded findings:

| Finding | Disposition |
| --- | --- |
| Candidate scope is 4 files | CONFIRMED |
| No approved URL authority change | CONFIRMED |
| No runtime config change | CONFIRMED |
| No governance change | CONFIRMED |
| No index / data / report authority change | CONFIRMED |
| URL functions consistent with predecessor | CONFIRMED |
| Hostile dynamic multiline value contained in candidate | CONFIRMED |
| `& < >` escaping still blocks dynamic Slack link injection | CONFIRMED |
| Dynamic `*` `_` `~` bounded to the value's own inline styling | ACCEPTED RESIDUAL |
| `merchant_handle` underscore risk | REACHABLE BUT SAFE |
| Backtick normalization | ACCEPTED BOUNDARY HARDENING |
| 17 new regression cases fail on the real predecessor worktree, pass on candidate | CONFIRMED |

The reviewer recorded that dynamic `*`, `_`, `~` cannot forge a new field, cannot construct
an attacker-controlled Slack hyperlink, and cannot alter subsequent formatter-owned content.
The reviewer also recorded that no `merchant_handle` delimiter of that kind is observed in
current production data convention.

### Test-suite characterization — read precisely

The full suite in the reviewer's detached environment carried pre-existing failures and
environment drift unrelated to this candidate. The predecessor and the candidate produced
**identical** failure and error sets; the candidate added **17 passed** relative to the
predecessor.

```text
FULL_SUITE_STATUS = NOT_FULLY_GREEN — PRE-EXISTING FAILURES UNCHANGED BY CANDIDATE
CANDIDATE_DELTA   = +17 PASSED, NO NEW FAILURES, NO NEW ERRORS
```

This document does **not** claim a fully green suite. No test suite was executed by this
documentation task.

## 6. Main Integration State

```text
MAIN_INTEGRATION = PASS
```

Verified read-only in this task (class A):

| Ref | SHA |
| --- | --- |
| `main` | `527706036d69378c32f57ce29283a83e916494ef` |
| `origin/main` | `527706036d69378c32f57ce29283a83e916494ef` |
| `codex/impl/slack-mrkdwn-rendering-cleanup` | `527706036d69378c32f57ce29283a83e916494ef` |

```text
main ... codex/impl/slack-mrkdwn-rendering-cleanup   ahead = 0   behind = 0
main ... origin/main                                 ahead = 0   behind = 0
```

The feature branch, local `main`, and the authoritative remote `main` are identical. This
history is frozen and must not be rewritten.

## 7. Production Activation Sequence

All of section 7 is class C — operator-observed production evidence, reported by the
operator and recorded verbatim here. This documentation task did not re-execute, re-verify,
or reproduce any step below.

### 7.1 Production worktree state after switch

| Item | Value |
| --- | --- |
| Branch | `main` |
| HEAD | `527706036d69378c32f57ce29283a83e916494ef` |
| Tracked modifications | NONE |
| Pre-existing untracked files | PRESERVED |
| `.mka/slack_config.json` load via real config loader | SUCCESS |
| `enable_approved_asset_urls` | `True` |

Activation of this sprint required **no** config edit. The approved-URL feature flag was
already `True` from the preceding sprint and was carried forward unchanged.

### 7.2 Restart sequence

1. The previous production Slack Bot process, **PID 99531**, was stopped. The operator
   reports this stop was unintentional — an accidental `Ctrl-C` — rather than a planned
   step of the activation procedure. It is recorded as it occurred.
2. The same Terminal was confirmed to still hold both credentials in its environment.
3. The absence of any remaining `mka slack-bot` process was confirmed.
4. The operator launched `.venv/bin/mka slack-bot` from the original token-loaded Terminal.
5. Slack Bolt reported:

   ```text
   ⚡️ Bolt app is running!
   ```

6. Exactly one Slack Bot process was confirmed after restart: **PID 12800**.

```text
PRODUCTION_ACTIVATION = PASS
PRODUCTION_BOT_SINGLE_PROCESS_AFTER_RESTART = PASS
```

**PID scope note.** PID 99531 and PID 12800 are evidence of this specific operation only.
Neither is a durable runtime identifier, and neither may be relied on by any future
procedure.

No other runtime metadata — start timestamp, host, uptime, resource usage — was reported,
and none is invented here.

## 8. Credential Handling / Secret Safety

```text
SECRETS_EXPOSED = NO
```

Credentials are supplied through the environment variables `SLACK_BOT_TOKEN` and
`SLACK_APP_TOKEN`.

The operator confirmed **presence only** (class C):

```text
SLACK_BOT_TOKEN=SET
SLACK_APP_TOKEN=SET
```

No token value was displayed, read, transcribed, or logged at any point. This document
contains no token value, no `.env` content, no credential fragment, and no private
credential reference of any kind. The `.mka/` runtime directory is untracked in this
repository (class A), so no credential-adjacent runtime file is reachable from this commit.

## 9. Production Smoke — 三風製麵

Class C — operator-observed production evidence.

Query:

```text
我要尋找三風製麵的案例
```

Production response header:

```text
共找到 1 個品牌／夥伴、2 筆內容。
```

Article:

- Title: 傳統製麵廠的數位轉型之路！《三風製麵》如何透過 SHOPLINE 提升客單價超過兩成
- URL: `https://blog.shopline.tw/merchant-showcase-shanfeng/`

Video:

- Title: 傳統製麵廠的數位轉型之路！《三風製麵》如何透過 SHOPLINE 提升客單價超過兩成｜SHOPLINE TALKS 聊品牌 EP 89
- URL: `https://www.youtube.com/watch?v=WIMy_AFA0pE`

All seven asset field labels — 標題, 連結, 上線日期, 採訪年份, 狀態, 對外引用, 資料來源 —
rendered as plain labels. The formerly visible formatter-owned forms `` `*標題：*` ``,
`` `*上線日期：*` `` and their siblings were **not** present in the production output.

| Acceptance criterion | Result |
| --- | --- |
| `MRKDWN_RENDERING_FIX` | PASS |
| `APPROVED_URL_ARTICLE` | PASS |
| `APPROVED_URL_VIDEO` | PASS |
| `ARTICLE_VIDEO_SEPARATION` | PASS |
| `CROSS_TYPE_LEAKAGE` | NONE OBSERVED |

## 10. Production Smoke — 怡和家電

Class C — operator-observed production evidence.

Query:

```text
我要尋找怡和家電的案例
```

Production response header:

```text
共找到 1 個品牌／夥伴、2 筆內容。
```

| Asset | URL |
| --- | --- |
| Article | `https://blog.shopline.tw/merchant-showcase-yh/` |
| Video | `https://youtu.be/7nVLtH5iW20` |

The same seven asset field labels rendered without formatter-owned raw asterisk markers.

| Acceptance criterion | Result |
| --- | --- |
| `MRKDWN_RENDERING` | PASS |
| `ARTICLE_URL` | PASS |
| `VIDEO_URL` | PASS |
| `ARTICLE_VIDEO_SEPARATION` | PASS |
| `PRODUCTION_RESPONSE` | HEALTHY |

## 11. Approved Asset URL Regression Check

```text
APPROVED_ASSET_URL_REGRESSION = NONE_OBSERVED
```

Basis (class C, sections 9 and 10):

| Check | Result |
| --- | --- |
| 三風製麵 article URL correct | YES |
| 三風製麵 video URL correct | YES |
| 怡和家電 article URL correct | YES |
| 怡和家電 video URL correct | YES |
| Article / video cross-type leakage | NONE OBSERVED |
| `enable_approved_asset_urls` still enabled | YES |

### Scope limit of this finding — binding

This result covers **two merchants and four asset URLs**. It is not a full-population
verification.

This document does **not** claim that every merchant, every asset, or every approved URL was
verified in production. `NONE_OBSERVED` means exactly what it says: no regression was
observed within the two smoke queries above. The prior sprint's binding metrics (205 bound
assets, 412 approved URL values, 107 distinct merchants) were established in the preceding
acceptance and were **not** re-verified here.

This is a targeted production smoke, not a production certification.

## 12. Known Non-Blocking Residuals

None of the following was fixed in this sprint, and none is closed by this acceptance. They
are recorded so that they are not later mistaken for a regression introduced by this
candidate.

### 12.1 Dynamic inline mrkdwn styling

```text
DYNAMIC_INLINE_MRKDWN_STYLING = KNOWN_NON_BLOCKING_RESIDUAL
```

Dynamic mrkdwn-sensitive characters such as `*`, `_`, `~`, and the backtick may still affect
the presentation of their own dynamic value in contexts where they are not structurally
neutralized. `_mrkdwn_escape` neutralizes `& < >` and collapses line breaks and control
characters, but it does not neutralize `*`, `_`, `~`, or a backtick; `_inline` additionally
normalizes a backtick to U+02CB, so that one character is neutralized only in code-span
context.

Per the independent review (class B), a dynamic `*`, `_` or `~` cannot forge a new field,
cannot create an attacker-controlled Slack hyperlink, and cannot alter subsequent
formatter-owned content.

The backtick is enumerated here for completeness of the residual list. It is **not** a
security defect introduced by this candidate: the predecessor emitted a backslash-escaped
form that left the backtick mrkdwn-active and merely added a visible backslash, so the
candidate changes the visible artifact and not the character's reach. Like the rest of this
residual, it is pre-existing and non-blocking, and it is not fixed here.

### 12.2 `merchant_handle` schema has no delimiter constraint

The `merchant_handle` schema carries no delimiter regex constraint. The independent review
classified this as reachable but safe, and recorded that no such delimiter is present in the
current production data convention (class B).

### 12.3 Merchant metadata (Handle / Sales Category) formatting

```text
HANDLE_SALES_CATEGORY_FORMATTING = KNOWN_NON_BLOCKING_RESIDUAL
```

The operator observed emphasis markers still visible around the merchant metadata lines in
production output (class C). The operator's report represented those lines as:

```text
*Handle：shanfeng*
*Sales Category LV1：美食*
*Sales Category LV2：食品/飲料*
```

and the corresponding 怡和家電 lines.

Repository inspection (class A) shows these three lines are emitted at
`src/marketing_knowledge_agent/slack_presentation.py:177-179` via `_label_value(...)`, wrapped
in `_ … _` italic markers rather than the `*` bold markers this sprint removed:

```text
_Handle：…_
_Sales Category LV1：…_
_Sales Category LV2：…_
```

These are two different kinds of evidence. They are recorded side by side, and neither is
rewritten into the other:

| Evidence | Marker as recorded | Class |
| --- | --- | --- |
| Operator transcript representation | `* … *` | C — operator-observed production |
| Repository formatter source | `_ … _` | A — repository-verified |

The marker character in the operator's transcript and the marker character the formatter
emits therefore differ.

```text
HANDLE_METADATA_MARKER_DISCREPANCY = NON_BLOCKING_DOCUMENTED_DISCREPANCY
```

This record does **not** resolve that difference, and it does **not** claim which marker Slack
finally renders to the user — neither `*` nor `_` is asserted here as the runtime-rendered
form. The transcript may have normalized the emphasis it displayed, or the representation may
have changed anywhere between the formatter and the operator's report; no evidence in this
record settles which. The discrepancy is disclosed and left open, not adjudicated, and the
disposition below is unaffected either way.

This is a **separate, pre-existing merchant-metadata presentation boundary**. It is not one
of the seven asset field labels in this sprint's scope, and it is **not** a regression
introduced by this candidate. It must not be recorded as one. It is not fixed here.

### 12.4 Full mrkdwn-inert dynamic text

If a future requirement demands that all dynamic text be entirely immune to mrkdwn parsing,
the correct path is a separate evaluation of Block Kit `plain_text`. That evaluation is
deliberately **not** expanded into this freeze.

## 13. Rollback Strategy

```text
ROLLBACK_READY = YES
```

**This sprint has no feature flag.** The mrkdwn fix is a code change, so the preceding
sprint's config-only rollback procedure does **not** apply and must not be copied.

### 13.1 Prohibited as rollback

| Action | Status |
| --- | --- |
| Force-push `main` | PROHIBITED |
| Rewrite history | PROHIBITED |
| Disable `enable_approved_asset_urls` as an mrkdwn rollback | PROHIBITED — wrong feature, would suppress approved URLs without addressing rendering |
| Re-index | PROHIBITED |

### 13.2 Conceptual emergency runtime rollback

If a genuine production regression attributable to this candidate is later found:

1. Stop the current Slack Bot process.
2. Temporarily run the production runtime worktree at the accepted predecessor code state
   `c2cf13a90a7b04bfef7b6cab07cb1a2025681c21`.
3. Start **exactly one** Slack Bot using the existing runtime config and existing
   environment credentials.
4. Leave `main` unchanged. Repair afterwards through a normal reviewed remediation or revert
   commit — never a force push.

Reverting to the predecessor reinstates the original defect: the seven asset field labels
would again show raw markers to the user. Rollback is therefore an emergency measure, not a
preferred state.

### 13.3 Authorization boundary

```text
THIS DOCUMENT RECORDS THE ROLLBACK STRATEGY.
IT DOES NOT AUTHORIZE EXECUTING A ROLLBACK NOW.
```

## 14. Relationship to the Previous Approved Asset URL Acceptance

The preceding acceptance record is:

`docs/reviews/slack_asset_link_exit/02_SLACK_APPROVED_ASSET_URL_PRODUCTION_ACTIVATION_ACCEPTANCE_2026-08-17.md`

That record is **immutable and unmodified by this task** (class A). It was read only.

Its section 11 listed as non-blocking follow-up item 5:

> Slack mrkdwn display shows a cosmetic issue where some `*label:*` markers appear visibly in
> production output. This is **not** part of the URL enablement failure criteria and must be
> handled as a separate sprint.

This sprint is that separate sprint.

```text
PREVIOUS_P3_FOLLOW_UP_ITEM_5 = CLOSED
```

Closed within the bounds established here: the seven asset field labels no longer emit
formatter-owned raw mrkdwn markers, verified by 17 repository regression cases (class A) and
by the two production smokes in sections 9 and 10 (class C).

The other follow-up items in that record — the binding canonical-surface newline edge case,
the duplicate `manifest.json` read, historical AppleDouble / data drift, and the deliberate
global fail-closed tradeoff — are **not** addressed by this sprint and remain open.

### Operational invariant carried forward, unchanged

The preceding record's operational invariant remains fully in force and is **not** modified
by this sprint:

> **APPROVED URL AUTHORITY IS BOUND TO THE CONTENT INDEX.**

A re-index that changes the approved identity surface still causes the runtime to fail
closed and suppress all approved URL enrichment until authority is rebuilt against the new
index. Re-index, authority rebuild, and binding verification remain one controlled
maintenance sequence. This presentation sprint neither relaxes nor revalidates that
invariant.

## 15. Final State Matrix

```text
MAIN_INTEGRATION       = PASS
PRODUCTION_ACTIVATION  = PASS

MAIN_SHA               = 527706036d69378c32f57ce29283a83e916494ef
PREDECESSOR_SHA        = c2cf13a90a7b04bfef7b6cab07cb1a2025681c21

CANDIDATE_SCOPE        = 4 FILES (1 SOURCE, 3 TEST)
REVIEW_DISPOSITION     = APPROVE_FOR_PUSH_GATE

NEW_REGRESSION_CASES   = 17
FULL_SUITE_STATUS      = NOT_FULLY_GREEN — PRE-EXISTING FAILURES UNCHANGED BY CANDIDATE

PRODUCTION_SMOKE_SHANFENG = PASS
PRODUCTION_SMOKE_YH       = PASS

MRKDWN_ASSET_FIELD_LABEL_FIX = PASS

APPROVED_ASSET_URL_REGRESSION = NONE_OBSERVED
APPROVED_ASSET_URL_SMOKE_BREADTH = 2 MERCHANTS / 4 URLS — NOT FULL POPULATION
ARTICLE_VIDEO_SEPARATION = PASS

PRODUCTION_BOT_SINGLE_PROCESS_AFTER_RESTART = PASS

SECRETS_EXPOSED = NO

HANDLE_SALES_CATEGORY_FORMATTING   = KNOWN_NON_BLOCKING_RESIDUAL
HANDLE_METADATA_MARKER_DISCREPANCY = NON_BLOCKING_DOCUMENTED_DISCREPANCY
DYNAMIC_INLINE_MRKDWN_STYLING      = KNOWN_NON_BLOCKING_RESIDUAL
MERCHANT_HANDLE_DELIMITER_SCHEMA   = KNOWN_NON_BLOCKING_RESIDUAL

PREVIOUS_P3_FOLLOW_UP_ITEM_5 = CLOSED

ROLLBACK_READY = YES
ROLLBACK_MECHANISM = CODE_STATE (NO FEATURE FLAG)
ROLLBACK_AUTHORIZED_NOW = NO

SPRINT_ACCEPTANCE = PASS
```

### Authorization boundary of this record

```text
DOC_ONLY_CHANGE = YES

NO_CODE_CHANGE       = TRUE
NO_TEST_CHANGE       = TRUE
NO_CONFIG_CHANGE     = TRUE
NO_INDEX_CHANGE      = TRUE
NO_PRODUCTION_ACTION = TRUE
NO_SECRET_EXPOSURE   = TRUE
NOT_PUSHED           = TRUE
```

This record accepts the state described above and nothing further. It does not authorize a
re-index, an authority rebuild, an approved-URL scope expansion, a residual remediation, a
rollback, a push, or a merge.
