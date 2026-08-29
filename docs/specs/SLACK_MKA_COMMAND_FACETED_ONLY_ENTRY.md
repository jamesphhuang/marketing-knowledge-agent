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

## 9a. Mode transition and stale Slack artifacts

Slack artifacts outlive the configuration that produced them. A 「開啟條件搜尋」 button posted into
a channel last week is still there and still clickable after an operator switches the entry mode,
and a modal opened seconds before the switch can be submitted seconds after it. So an interaction's
own provenance is necessary but never sufficient: **the mode in force at execution time must
authorize it too.**

`private_metadata["entrypoint"]` is not trusted merely because this app wrote it. It states how a
view was *opened*, which is exactly the fact that goes stale.

One rule, `entrypoint_allowed_for_mode(mode, entrypoint)`, is checked at every executable entry
point rather than re-derived per handler:

| current mode | accepts | refuses |
| --- | --- | --- |
| `slash_faceted_only` | slash-session interactions | mention-mode buttons and modals, and anything without slash provenance |
| `mention_mixed` | mention interactions | slash-session artifacts (none can legitimately exist — `/mka` is not registered) |

The gate sits at the open-modal action, the show-more action, **and independently at the view
submission**. The last is not redundant: a modal opened before a switch and submitted after it never
passes through today's action handler at all, so a button-only fix would leave the whole chain —
legacy button → modal recording `entrypoint=app_mention` → submission trusting it → public
`chat.postMessage` — reachable through its second half.

A refused submission executes nothing: no `execute_structured_search`, no retrieval, no audit row,
no message of either kind. It is answered through `ack(response_action="errors")` alone, so no
posting API is involved, and the modal explains itself rather than closing silently on a result
that will never arrive. A refused button click gets a fixed ephemeral pointer through the existing
boundary — no public post, no echo of what was clicked, no prior query.

The guidance names the entry point the **current** mode has, chosen by `stale_entry_mode_message`:

| current mode | guidance |
| --- | --- |
| `slash_faceted_only` | 「搜尋入口已更新，請輸入 `/mka` 重新開啟搜尋。」 |
| `mention_mixed` | 「此搜尋操作已失效，請重新標記 @Marketing Knowledge Agent 開始搜尋。」 |

One shared sentence would not do. Telling a `mention_mixed` user to type `/mka` points them at a
command that mode never registers, leaving the advice as stale as the artifact that triggered it.
Both are fixed literals selected by mode — never assembled from anything the interaction carried —
and the mention wording reuses `SHOW_MORE_MENTION`, the single definition of how this bot is
addressed.

**Invariant:** under `slash_faceted_only` there is no executable route from an old mention-mode
Slack artifact to a structured search, and therefore none to `chat.postMessage`.

## 10. Ephemeral posting boundary

### Destination and transport

Validation is an exact allowlist, checked before a capability is stored rather than before it is
sent — refusing at the boundary where the value enters, not after it has been carried around.
Required: HTTPS; an exact approved host; **no username, no password**; port absent or exactly 443,
with an unparseable port refused rather than treated as absent; no fragment; no query string; and a
**structurally validated** path. An independent review found the looser first version accepted
`user:pass@hooks.slack.com`, `hooks.slack.com:444` and `hooks.slack.com:8443`.

The path check is not a prefix test. A second review found `startswith("/commands/")` authorized
`/commands/a/../../services/x`, `/commands/%2e%2e/x` and `/commands/a%2f..%2fservices/x` on the
strength of the characters they begin with. Validation now runs on the **raw** path, exactly as it
will be sent — nothing is decoded and re-checked, because a validator that normalises differently
from the HTTP client is one that can be walked past. Four gates: every `%` escape well-formed and
none decoding to `/`, `\`, `.` or a control byte; no raw control characters or backslashes; no
empty, `.` or `..` segments; and a first segment naming an approved family with at least one
segment after it.

**The approved families are `commands` and `actions`, and nothing else** — the two payload kinds
this surface actually receives, a slash command and an interactive action. `services` (incoming
webhooks) was in an earlier version of the list and is removed: this app never receives one.

### Exception and frame secrecy

`urllib`'s exceptions carry the full URL in `HTTPError.url` and usually in `str(exc)`. A review
found that `raise ... from None` was not enough: it clears `__cause__` and suppresses traceback
rendering, but the original stays reachable through `__context__`, where any structured reporter
walking an exception tree will find it.

The sanitized error is therefore raised **outside** every `except` block, so nothing is being
handled and `__context__` is `None` too. Only a fixed string and an integer status cross the
boundary.

A later review showed that was still not enough. `__cause__` and `__context__` were clean, and the
capability was *still* reachable — through a **traceback frame local**: a spent reservation kept its
URL, and handler frames hold the reservation. The test that should have caught it compared
`repr(f_locals)`, which this class's own `__repr__` masks, so it was structurally incapable of
seeing the leak.

Two changes close it. Spending is destructive, so a spent reservation holds nothing. And the HTTP
attempt lives in a private helper that **returns** a non-secret status instead of raising, so the
one frame that ever holds the URL and the `Request` never appears in a traceback at all; the
boundary passes `reservation.spend()` straight into it, keeping the capability on the value stack
rather than in `f_locals`.

The detector that guards this walks objects rather than reprs, and is itself checked against a
deliberately leaky control case.

### Proxy, recorded rather than solved

`urllib`'s `ProxyHandler` honours `HTTPS_PROXY`/`https_proxy`. Verified: it is absent in a process
with no proxy variables and present in one that has them, so whether a response_url request
traverses a proxy is a property of the deployment rather than of this code. A review classified it
non-blocking and it is **left unchanged** — pinning it would change behaviour in a proxied network,
which is an operator's decision. Stated here so it is not mistaken for handled.

**Redirects are refused.** Validating the first hop authorizes nothing about the second, and a
`Location` header is a destination chosen by the responder — following one would send a bearer
capability wherever it pointed, with the host allowlist having checked the hop that did not matter.

The transport is a few lines of standard library rather than the Slack SDK's webhook client, for
three reasons established against the installed 3.43.0: it accepts a `logger` and its request path
can emit `req.full_url`, which is the capability itself; it retries by default, so one locally
accounted use could become several requests against Slack's own budget; and it offers no way to
refuse redirects.

There are two outbound boundaries, one per mechanism, each with a single call site:

- `post_slack_reply` → `chat.postMessage`, for channel-visible messages (`mention_mixed`);
- `post_slack_response_url` → the response_url transport, for everything the slash flow says.

The response_url boundary forces four properties, written *after* the caller's message is unpacked
so no call site can override them: `response_type="ephemeral"` (never `in_channel` — that would
publish one user's search to the conversation), `replace_original=False` (each message is its own
reply), and `unfurl_links`/`unfurl_media` false (the unchanged no-unfurl contract).

The source-level inventory test covers both mechanisms, and additionally rejects
`chat_postEphemeral`, `chat_update`, `files_upload`, `say(`, `respond(`, and raw HTTP
(`requests.post`, `urlopen`, `http.client`, `httpx`, `aiohttp`) anywhere in the Slack modules.
`slack_response_urls.py` is the single exception, because it owns the capability and is therefore
the only place allowed to transmit one — and it is separately asserted to hold exactly one outbound
call site and to contain no logging call at all.

`chat.postEphemeral` is on the *forbidden* list rather than merely unused: an unused posting helper
is exactly what a future handler reaches for.

Clickable approved asset titles (`<approved-url|title>`) are unchanged on both paths.

### Resolved: the ephemeral-posting constraint, and what replaced it

The constraint this section used to describe as an open risk was confirmed by Human UAT and has
been fixed. It is kept here because the reasoning is the reason the current design looks as it does.

**What was predicted.** `chat.postEphemeral` requires the app to be able to post into the target
conversation, so an invocation from a channel the bot was never added to can return
`channel_not_found` even though the command was delivered.

**What UAT observed.** Exactly that, on the path that was least expected to matter: a `/mka` from a
conversation outside the operator's allowlist entered the *denial* branch correctly, and then could
not deliver the denial. The user saw nothing at all -- no modal, no refusal, no error. Only the
operator saw it, in the bot's log.

That is worse than the success-path version of the same fault, and it was made *more* likely by
restricting the allowlist for the first UAT round: the denial branch fires precisely in the
conversations the bot is least likely to be a member of. Optimising the success path moved the
failure rather than removing it.

**What replaced it.** Every slash-originated message now leaves through the `response_url` Slack
attaches to the command or interaction that produced it -- see §10a. `chat.postEphemeral` is no
longer used anywhere in the package and the helper that wrapped it has been removed, so a future
handler cannot reach for it.

## 10a. Response URL capability

A `response_url` answers the interaction it came from, ephemerally by default, **without depending
on channel membership**. That is the property the slash flow needs and `chat.postEphemeral` does not
have.

It is also a **bearer secret**: anyone holding it can post into that conversation as this app, with
no token. So it is handled as a credential, not as a routing coordinate:

| rule | why |
| --- | --- |
| memory only, never written to disk | a capability in a file outlives the interaction it belongs to |
| never in `private_metadata`, a button value, a request token, a pagination key or an audit row | all of those travel to Slack and back, or persist |
| excluded from `repr`, on the stored record *and* on the reservation | a debugger, a crash dump or a stray `print` would otherwise disclose it |
| a reservation cannot be copied, deep-copied or pickled | each would mint a second authorization from one — the clones carry the same URL with a fresh unspent flag, and pickling writes the capability into bytes that outlive the process |
| spending is **destructive** — a spent reservation no longer holds its URL | handler frames hold the reservation, so a spent-but-still-loaded one is readable from traceback frame locals by any error reporter that walks objects |
| the one frame that holds the URL returns rather than raises | a frame that raises appears in the traceback; a frame that returns does not |
| never logged, and never in an exception message | the transport logs nothing at all, and every failure is re-raised as a fixed-text error with the chained original suppressed — `urllib` puts the full URL in `HTTPError.url` and usually in `str(exc)` |
| strict validation before storing | see below; this value arrives in a payload and is then POSTed to |
| TTL below Slack's documented ~30 minutes | expiry then fails in our code, with our message, rather than as a Slack error after a search has already run |
| hard budget of 5 sends, matching Slack | the sixth is refused here rather than silently at Slack |
| bound to user + channel + session | a capability is spent only by the interaction it was minted for |

Unknown, expired, wrong-user, wrong-channel, wrong-session and exhausted all resolve to the same
`None`. Distinguishing them would tell a caller which half of a guess was right.

### Atomicity: reserve, don't check

An independent review reproduced two related defects in the first version of this design, and both
shaped what is here now.

**Uses were spendable twice.** Verification and decrement were separate steps, so two threads
holding a one-use capability could both pass the check. `slack_bolt` dispatches listeners on a
thread pool, so that is a real interleaving, not a theoretical one — reproduced as 2 successes on a
budget of 1. Every mutation of store state is now inside one `threading.RLock`, and `reserve()`
verifies and decrements in a single critical section.

**And the reservation itself was spendable twice.** A later review found the same class of defect
one level down: the store's lock protects the *budget*, but the send-once flag on the reservation
was its own unsynchronised check-then-act, so two threads handed the same object both sent —
reproduced as two outbound requests from one authorization. A reservation now owns a `Lock` of its
own, and duplication is refused outright rather than allowed and hoped about.

**The pre-retrieval check was observational.** `can_reply()` answered a question that was already
stale by the time the answer was used: another handler could consume the last use in the window
before the send, leaving a search that had run with nowhere to go.

Both are closed by the same change. `can_reply()` and `take()` no longer exist. The only way
executable code obtains a capability is `reserve()`, which returns a **send-once reservation** whose
use has already been consumed atomically. Holding one *is* the authorization to send.

The ordering is therefore:

```text
validate mode → validate request → reserve one use → retrieval → send with that reservation
```

A validation error reserves nothing, because it answers through `ack` alone; spending a use there
would let a handful of ordinary mistakes exhaust a session that never ran a search. A `/mka` with
no usable `response_url` opens no modal at all.

Three semantics worth stating because they are judgement calls, not defaults:

- **a failed send is not refunded** — Slack may have received and acted on the request even when
  the client saw a transport error, so re-spending could exceed the server-side budget and deliver
  the message twice;
- **one reservation is at most one HTTP attempt** — no retry, by construction;
- **a refresh does not revoke an outstanding reservation** — its use was consumed when it was
  issued, so it is a send already paid for; revoking it would drop a reply the user is owed.

**Refresh.** A button click is a new interaction with its own capability, separately budgeted and
later-expiring. 「調整條件」 and 「重新搜尋」 refresh the stored capability from the click before
opening the next modal, and 「顯示更多」 answers through the click's own. Ownership is *not*
refreshed alongside it: user and channel still come from the interaction payload.

Denial included: when an operator sets `slash_command_allowed_channel_ids`, a refused `/mka` is
answered through its own command capability, which is what makes the refusal reach the very
conversations the allowlist exists to turn away.

## 10b. Modal and result presentation

Human UAT asked for wording a marketer reads without translating from the data model, and for a
result card that leads with the content rather than with identity fields.

| surface | before | after |
| --- | --- | --- |
| modal field | `Sales Category LV2` | 品牌產業別 |
| modal field | 內容相關標籤 | 你在找什麼功能？ |
| modal field | 你想找什麼內容或成果 | 你想找什麼內容或成果，請輸入關鍵字 |
| applied conditions | `Sales Category LV2` | 品牌產業別 |
| applied conditions | 內容相關標籤 | 功能 |
| result card | brand + Handle + LV1 + LV2 + assets | brand + assets |

**These are display strings only.** Block ids, action ids, `StructuredSearchRequest` fields,
taxonomy field names, query-plan fields and audit columns are all unchanged, and the applied-
condition labels are a Slack-scoped mapping rather than a `FIELD_REGISTRY` rename -- renaming there
would change CLI and `explain-query` output this work package has no business touching.

`merchant_handle`, `sales_category_lv1` and `sales_category_lv2` are still carried on every entity
and are still what grouping, conflicting-handle removal, data-conflict marking, identity and
governance read. Only the three rendered lines are gone. That distinction is load-bearing: a group
whose records disagree on the handle is still dropped outright rather than merged under one of
them, which is what stops two different merchants appearing as one brand. The tests that used to
read those properties off the rendered text now read them off the model, so hiding the lines could
not quietly stop testing the behaviour.

### ALL YEARS: same rule, earlier feedback

The narrowing rule is unchanged -- 「全部年份」 still narrows nothing, and a search scoped only by
it is still refused. UAT confirmed the rule is right and the *timing* was wrong: a user who
reopened 調整條件, set the year back to 「全部年份」 and cleared the other fields only found out
after submitting.

The year field now carries a Block Kit hint saying so before submission. It is a hint, not a
validation change: 「全部年份」 still does not count as a narrowing constraint, and the refusal
still fires if the user proceeds anyway.

## 10c. Pagination generations

A new search supersedes the old one. That was true sequentially and, an independent review showed,
**not** true under concurrency: a 「顯示更多」 worker that had already read an entry kept operating
on it while a new search installed a replacement, then delivered a page from the superseded result
and — on a last page — ran an unconditional `pop` that deleted the *new* continuation. Same user
and same session, so no ownership check applied.

Every continuation now carries an opaque server-minted **generation**, and every operation names the
generation it believes it is working on. A stale generation reads nothing, advances nothing and
removes nothing; a last page removes the entry only when it is still that generation's.
`has_more` is generation-aware for the same reason — a key-only question let a stale worker observe
a newer search and render a button that advanced someone else's result.

Generation checks alone are insufficient, because "read a valid page, a new search installs, then
send" passes its check when it is made. A **per-lane guard** serialises consume-and-deliver against
`start` for the same lane, leaving exactly two orderings:

| ordering | allowed |
| --- | --- |
| old page delivered, then new search becomes authoritative | yes |
| new search becomes authoritative, then stale click refuses | yes |
| new search becomes authoritative, then old page delivered | **unreachable** |

The guard is per lane, so unrelated conversations never wait on each other, and the network send
happens in the caller's guarded block rather than inside the store — which owns continuation state,
not Slack transport.

The generation is 32 hex characters and **authorizes nothing on its own**: the clicker's user,
channel and session still come from the interaction payload, and the request token must still
resolve for them. It carries no query, no conditions and nothing the user typed.

A dedicated generation was chosen over reusing the request token: the token's TTL and the
continuation's TTL are independent, and coupling them would let a token refresh silently change
pagination identity.

The `app_mention` flow has no button to carry one, so it consumes whichever generation the lane
currently holds, resolving and consuming in a single critical section. That is a narrower guarantee
than the slash flow's, and still enough that a worker cannot advance or delete a continuation
installed after it read the lane.

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

In `slash_faceted_only`, an app mention writes no query-bearing row at all. Migration routing
precedes every audit path, including the channel-authorization check that predates the mode: the
same text that is not a query in an allowed channel is not a query in a DM or an unlisted channel
either. A denied conversation is still recorded — reaching this bot from an unauthorized
conversation is operational signal — but with an empty query column, dropped by construction rather
than by matching anything in the text. `mention_mixed` keeps its existing row and query column
unchanged, since there the text really is an attempted search.

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
