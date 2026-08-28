# Slack `/mka` Faceted-Only Search Entry

Status: implemented on branch `claude/impl/slack-mka-command`, **not reviewed, not merged, not
activated**. The new behaviour is behind an explicit entry mode whose default is today's behaviour,
so merging this code changes nothing until an operator selects the mode. No Slack App Console
change, no bot start or restart, no production config change. This document is the read side of the
work package; task-lock and verification records live in `docs/collaboration/CURRENT_WORK.md`.

Supersedes the entry-point half of `docs/specs/SLACK_FACETED_SEARCH_MVP.md` when, and only when,
`slack_search_entry_mode` is set to `slash_faceted_only`. Everything that spec says about facet
eligibility, governance, ranking and citations is unchanged and still governs.

## 1. Problem

Human UAT of the Faceted Search MVP surfaced the entry point itself as the weak part:

- `@Bot <自由文字>` is a natural-language search, so the ambiguity the Search Taxonomy Authority
  exists to refuse is reachable from the primary entry point. A user who types a sentence gets
  either a fail-closed abstain they cannot act on, or a broad semantic sweep.
- Reaching the modal takes two hops: mention the bot with an exact trigger phrase, then click a
  button posted into the channel.
- Both the result and the button are posted into the channel, so one person's search is read by
  everyone in it, and the button is clickable by everyone in it.
- 「顯示更多」 requires replying `@Marketing Knowledge Agent 顯示更多` in-thread, because the bot
  subscribes to `app_mention` and nothing else.
- The year field was a multi-select with no default, so "I don't care which year" had to be
  expressed by leaving a field blank, and 「2025 + 2024」 was expressible but meaningless as a scope.

## 2. Product contract

```text
SLACK_PRIMARY_ENTRY=/mka
FACETED_ONLY_SLACK_SEARCH=YES
APP_MENTION_DIRECT_SEARCH=DISABLED
APP_MENTION_GUIDANCE_ONLY=YES
SLASH_COMMAND_TEXT_IGNORED=YES
FREE_TEXT_ONLY_SEARCH=DISABLED
FREE_TEXT_SUPPLEMENTARY_ONLY=YES
RESULT_VISIBILITY=INVOKER_ONLY
PAGINATION_BUTTON=YES
PAGINATION_RESEARCH=NO
```

## 3. Entry mode

One configuration key, `slack_search_entry_mode`, with two canonical values. It is a mode rather
than a set of booleans because the alternatives are mutually exclusive readings of the same
question -- what does an app mention mean? -- and independent booleans would let them contradict.

| value | app mention | `/mka` | result visibility | continuation |
| --- | --- | --- | --- | --- |
| `mention_mixed` (default) | natural-language search; trigger phrase opens the modal button | not registered | in-channel thread reply | `@Bot 顯示更多` |
| `slash_faceted_only` | short guidance only, never a search | opens the modal directly | ephemeral to the invoker | 「顯示更多」 button |

Fails closed at config load: an unrecognised value is refused rather than defaulted, because a typo
would otherwise leave app-mention search alive on a deployment whose operator believed they had
switched it off. `slash_faceted_only` additionally requires `enable_faceted_search=true` (and hence
the pinned taxonomy workbook/sha pair), since the modal is that mode's only search entry.

## 4. Authorization

`allowed_channel_ids` is unchanged and still governs every channel-visible message, including the
app-mention guidance reply and the whole of `mention_mixed`.

`/mka` gets its own key, `slash_command_allowed_channel_ids`, because the two authorize different
things. `allowed_channel_ids` governs channel-*visible* disclosure -- a message posted into a
channel is read by everyone in it, so the channel is the audience. A `/mka` result is ephemeral and
addressed to exactly one user, so the conversation is a routing coordinate, not an audience.
Reusing the channel allowlist would block every DM and every unlisted channel from a flow that
discloses nothing to them.

- key absent → no conversation restriction. This is the product goal, and is safe because the
  result is invoker-only.
- key present as a non-empty list of conversation IDs → restricted to those.
- key present as `[]` → **refused at load**. "Everywhere" and "nowhere" must not be the same value.

`allowed_exposure_channels` and every other data-governance policy are untouched. Nothing about
what may be disclosed changed; only who is asked and where the answer is addressed.

## 5. `/mka`

Acknowledged first and unconditionally -- Slack allows three seconds, and everything after is
in-memory work against a catalog built once at startup. Then `views_open` directly: no intermediate
button message, no retrieval, no query planning, no audit row. Opening a modal is not a query.

`command["text"]` -- whatever the user typed after the command -- is never read. `/mka`,
`/mka 搜尋`, `/mka SHOPLINE` and `/mka <restricted name>` all produce the identical blank modal.
Trailing text never becomes a query, a prefill, a `free_text`, an audit row, a stored request, or an
echo. Treating it as input would reintroduce free-text search through the one entry point that
exists to replace it, with text that was never validated, never checked against the denylist before
being echoed, and never chosen from the catalog.

Works from public channels, private channels and DMs; the conversation id is used as sent, with no
assumption that it begins with `C`.

## 6. App-mention migration

Every mention -- the old trigger phrases, the old 「顯示更多」 continuation, and any other text --
returns the same short guidance naming `/mka`, and returns it **before** the pagination store,
`ask_fn` and every audit call. The text a user typed is read only to be discarded: not retrieved on,
not written to an audit row, not stored against a token, not prefilled, not echoed.

`agent_ask` itself is untouched. CLI and internal natural-language search are unchanged; this is a
Slack routing policy change only.

The pre-existing denied-channel path is unchanged, including its `slack_denied_channel` audit row.

## 7. Year field and 「全部年份」

Single `static_select`, not a multi-select: 「全部年份」+2025 and 2025+2024 are both meaningless as
a scope, and a multi-select is the only way a user could express either.

「全部年份」 leads the option list and is selected by default. It is a **UI sentinel**, not data:
its value (`__all_years__`) decodes to *no* `interview_year` constraint, never to a constraint whose
value is the sentinel. A sentinel-valued constraint would match nothing in the index while
appearing in the query plan and the audit row as though a year had been chosen.

Decoding, and why each case is what it is:

| submission | request | reason |
| --- | --- | --- |
| 「全部年份」 | `interview_years=()` | the sentinel means no constraint |
| field absent / null selection | `interview_years=()` | identical to the default state, so it must decode identically |
| `"2025"` | `interview_years=(2025,)` | the only other thing the modal renders |
| anything else | **refused** | can only come from a forged payload; coercing it to 「全部年份」 would turn a forged field into a whole-corpus search |

Reopening round-trips faithfully: an empty prior year selection reopens on 「全部年份」, a specific
year reopens on that year, and 「重新搜尋」 opens blank on 「全部年份」.

`StructuredSearchRequest.interview_years` keeps its tuple type. The structured layer was not
rewritten for a single-select UI; a tuple of zero or one is the honest encoding of what the field
can now express.

## 8. Narrowing requirement

At least one of: a specific interview year, a Sales Category LV2, or a content tag.

Free text is deliberately absent from that test. A search scoped only by a relevance goal is an
open-ended sweep of the whole corpus dressed as a targeted query: free text is scored against
candidates, it does not bound them. 「全部年份」 leaves `interview_years` empty precisely so that
choosing it cannot smuggle one in.

The refusal names the fields that would satisfy it -- 「請至少選擇一個搜尋範圍，例如特定年份、
Sales Category LV2 或內容相關標籤。」 -- because a user told only 「請至少填寫一個搜尋條件」 would
reasonably retype into the free-text box, which is exactly what cannot work.

Free text remains a supplementary relevance goal alongside a structured scope, unchanged.

## 9. Session context and continuation

A slash command is not a message, so it carries no `thread_ts`. Each `/mka` mints an unguessable
session id, carried in `private_metadata` and in button values, and always combined with the
invoking user id before use:

```text
session_key = f"{user_id}:{session_id}"
```

The user id always comes from the Slack interaction payload; only the opaque lane id travels in a
button value. A copied or guessed session id therefore lands in the copier's own lane, where it
finds nothing.

`SlackRequestTokenStore` and `pagination_key` had their third coordinate renamed `thread_ts` →
`session_key`. It is the same check with an honest name: `thread_ts` for the mention flow, a session
key for the slash flow. Renaming rather than reusing the old name follows the R2 precedent
(`get()` → `resolve()`), so no call site can keep the old meaning by accident.

Cross-user access is blocked twice, independently, and each half is pinned by its own test:

1. the token store's owner check (`owner_user_id`, `channel_id`, `session_key` must all match);
2. the lane key itself being per-user.

Removing either alone leaves the other carrying the guarantee; removing both is caught.

「顯示更多」 replays a page this search already rendered. No retrieval, no reranking, no query
planning, no new search audit row. The button is offered only while a page is actually waiting --
one that answered 「已失效」 would be worse than none. Wrong user, unknown token and expired
continuation are indistinguishable to the clicker: all three get 「請重新執行原搜尋」.

The page footer's continuation instruction is chosen by entry point, because an instruction the
reader cannot follow is worse than none: a thread reply would never reach a bot that subscribes to
`app_mention` only, and an ephemeral message has no thread to reply into.

## 10. Ephemeral posting boundary

`chat.postEphemeral` is a second executable posting API on this surface, so it gets a boundary of
its own -- `post_slack_ephemeral` -- with the same properties as `post_slack_reply`: one call site,
unfurl flags written after the reply is unpacked, no way for a caller to re-enable unfurling. A
source-level inventory test now covers both APIs and rejects `chat_update`, `files_upload`, `say(`
and `respond(` anywhere in the package.

**Verified against the installed SDK rather than assumed:** `slack_sdk` 3.43.0's
`chat.postEphemeral` binding does **not** declare `unfurl_links`/`unfurl_media` as named parameters,
unlike its `chat.postMessage` one. It accepts `**kwargs` and forwards them verbatim into the request
body, so the flags are transmitted. Whether Slack's `chat.postEphemeral` acts on them is **not**
established by this code and has not been exercised against live Slack -- it is a UAT check.
Setting them costs nothing and cannot make unfurling more likely.

Clickable approved asset titles (`<approved-url|title>`) are unchanged on both paths.

## 11. Schema versioning

The modal's wire schema changed, so an in-flight modal must be refused rather than decoded under the
new rules: a v1 payload decoded by v2 finds no `selected_option`, which reads as 「全部年份」 --
silently widening a year-restricted search to every year.

`STRUCTURED_REQUEST_SCHEMA_VERSION` (v2) lives with the request contract in `structured_search.py`
and is folded into `FacetCatalog.catalog_version` alongside `CATALOG_BUILDER_SCHEMA_VERSION`. The
two are separate constants because they change for different reasons -- eligibility rules versus
wire shape -- and bumping the builder version for a modal change would misdescribe why.

A stale submission is refused with the existing staleness message and no execution.

## 12. Audit

`/mka` itself writes nothing: opening a modal is not a search, and the trailing text is never
recorded. A submitted search still writes one `slack_faceted_search` row recording the facet
selection, the catalog version and the free-text goal.

The audit reflects the query actually executed. 「全部年份」 produces no `years=` component at all,
because there is no year constraint to record; a specific year produces `years=2025`. Denylist and
refusal privacy semantics are unchanged: a refused query's text reaches no audit row, no token
store, and no echo.

## 13. Not in scope

No Slack App Console change (`/mka` is not created in any workspace by this work package), no bot
start or restart, no deployment, no production config change, no `main` merge. Stable Record V2,
row_v1 retirement, production re-index, the Search Taxonomy Authority workbook, taxonomy canonical
renames, the cross-level ambiguity backlog and the content-tag ingestion mismatch are all untouched.

Activating `/mka` requires, separately and with its own authorization: creating the slash command in
the Slack App Console, setting `slack_search_entry_mode` to `slash_faceted_only`, restarting the
bot, and Human UAT.
