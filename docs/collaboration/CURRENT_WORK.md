# Current work

> 這是 Codex 與 Claude Code 的任務鎖定頁。開始改檔前必須更新；同一時間只能有一個 active implementer。

## Lock

- State: active — controlled UAT preparation
- Milestone state: SLACK_MKA_COMMAND_UAT_R1_SECURITY_REMEDIATED_AWAITING_REVIEW_3
- Task: Slack `/mka` Faceted-Only Search Entry
- Implementer: Claude Code
- Reviewer: Codex — R1 against `5954e10` CHANGES_REQUESTED (2 blocking, both remediated); R2
  against `0da023c` PASS_WITH_NONBLOCKING_FINDINGS (0 blocking, 2 nonblocking, both closed by the
  authorized narrow cleanup); Final Spot Review against `eb128b8` **PASS**, 0 blocking, 0
  nonblocking. Every review round's evidence is preserved in its own dated section below.
- Branch: `claude/impl/slack-mka-command`, published to `origin`
- PR: [#5](https://github.com/jamesphhuang/marketing-knowledge-agent/pull/5) — **OPEN**, base
  `main`, not a draft
- Final reviewed code candidate: `eb128b8080a072917c042de80705521fd2c0a734`
- Clean Integration Verification: PASS
- Remote Publication: PASS
- Remote CI: NOT_CONFIGURED_OR_NO_RUNS (no checks are reported on this branch)
- Worktree: `/private/tmp/mka-slack-mka-command` (isolated; does not touch the main checkout at
  `/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent`)
- Baseline commit: `0669fbb325e2cf2aebb390a3a90ce7686d12c139` (= `origin/main` at start, verified
  equal before any file was changed)
- Product behavior changed: YES (Slack surface only, behind a new explicit entry mode whose default
  is today's behaviour)
- Product runtime: NOT ACTIVATED
- Production activation: NO
- Slack App Console changed: NO
- Bot started/restarted: NO
- Main updated: NO — `origin/main` is still the baseline `0669fbb`; the PR is open, not merged
- Commit provenance: R1 implementation `5ccf31516084f3e30dd34c3dbfcf862f6f121d08`; R1 reviewed
  candidate `5954e10bd31b308c67e349b4d76f207c5558eb03`; R1 remediation
  `12f4c81e070cd04f494d44386a8e95843294f998`; R2 reviewed candidate
  `0da023c2f2f606b0a0287334537168a8a24d93f2`; R2 nonblocking cleanup
  `a09b89c06b6a18220e5781e032d37a1d616bfd94`; **final reviewed code candidate**
  `eb128b8080a072917c042de80705521fd2c0a734`.
- Started at: 2026-08-28
- Human UAT Phase 1: **PASS_WITH_BLOCKING_FINDING** — the search flow passed live; slash delivery
  depended on bot membership and had to be re-routed. See the dated section below.
- Next exact action: **third independent security review** of the capability model, then a second
  controlled UAT round. Not authorized by this record.

### Reviewed-code provenance versus PR head

`FINAL_REVIEWED_CODE_SHA` is `eb128b8080a072917c042de80705521fd2c0a734` and stays that way. This
ledger reconciliation is documentation only and creates a newer commit, so the PR head moves ahead
of the reviewed candidate — but `src/` and `tests/` remain byte-identical to `eb128b8`, verified by
`git diff eb128b8..HEAD -- src tests` returning empty output.

The distinction matters because the two SHAs answer different questions. `eb128b8` is the code that
was actually reviewed and integration-verified; the newer head is only the current tip of the PR
branch. A later reader must not read the docs-only SHA as independently product-reviewed, because
it was not: nothing executable changed in it, and no review round examined it.

### Evidence basis for the gate results recorded here

Recorded so a reader knows which claims rest on which evidence, rather than having to assume:

- **Verified directly in this worktree during this reconciliation:** `HEAD` = `eb128b8`; the branch
  exists on `origin` at that same SHA; PR #5 is OPEN, base `main`, head `eb128b8`, not a draft;
  `gh pr checks 5` reports no checks on this branch; `origin/main` is still `0669fbb`;
  `src/`/`tests/` byte-identity against `eb128b8`.
- **Recorded as authoritative from the reviewing party, not re-performed here:** Final Spot Review
  PASS (0 blocking, 0 nonblocking) and Clean Integration Verification PASS. This reconciliation is
  a ledger update, not a review, and does not re-derive those outcomes.

```text
ACTIVE_IMPLEMENTER=CLAUDE_CODE
TASK_LOCK=HELD
IMPLEMENTATION_AUTHORIZED=YES
PRODUCTION_ACTIVATION_AUTHORIZED=NO
SLACK_APP_CONSOLE_CHANGE_AUTHORIZED=NO
BOT_START_AUTHORIZED=NO
DEPLOYMENT_AUTHORIZED=NO
MAIN_MERGE_AUTHORIZED=NO

IMPLEMENTATION=PASS

CODEX_DELTA_REVIEW_R1=CHANGES_REQUESTED
R1_BLOCKING_FINDINGS=2
R1_BLOCKERS_REMEDIATED=2

CODEX_R2_REVIEW=PASS_WITH_NONBLOCKING_FINDINGS
R2_BLOCKING_FINDINGS=0
R2_NONBLOCKING_FINDINGS=2
R2_NONBLOCKING_CLOSED=2

FINAL_SPOT_REVIEW=PASS
FINAL_SPOT_BLOCKING_FINDINGS=0
FINAL_SPOT_NONBLOCKING_FINDINGS=0

FINAL_REVIEWED_CODE_SHA=eb128b8080a072917c042de80705521fd2c0a734
INTEGRATION_VERIFICATION=PASS
REMOTE_PUBLICATION=PASS

PR_CREATION=PASS
PR_NUMBER=5
PR_STATE=OPEN
REMOTE_CI_STATUS=NOT_CONFIGURED_OR_NO_RUNS

READY_FOR_CONTROLLED_UAT=YES
CONTROLLED_UAT_STARTED=YES
HUMAN_UAT_PHASE_1=PASS_WITH_BLOCKING_FINDING
UAT_BOT_STOPPED_BY_HUMAN=YES
UAT_BOT_STOP_METHOD=SIGINT
UAT_BOT_EXIT_130_EXPECTED=YES
UAT_BOT_RESTARTED=NO
UAT_R1_BLOCKING_FINDING=SLASH_DELIVERY_DEPENDS_ON_BOT_MEMBERSHIP
UAT_R1_REMEDIATED=YES
UAT_R1_DELTA_REVIEW=CHANGES_REQUESTED
UAT_R1_REVIEW_BLOCKING_FINDINGS=4
UAT_R1_SECURITY_BLOCKERS_REMEDIATED=4
UAT_R1_SECURITY_REVIEW_2=CHANGES_REQUESTED
UAT_R1_SECURITY_REVIEW_2_BLOCKING_FINDINGS=4
UAT_R1_SECURITY_REVIEW_2_REMEDIATED=4
UAT_R1_SECURITY_REVIEW_3=PENDING
READY_FOR_UAT_R1_SECURITY_REVIEW_3=YES

SLACK_APP_CONSOLE_CHANGED=NO
PRODUCTION_CONFIG_CHANGED=NO
PRODUCTION_ACTIVATED=NO
BOT_STARTED=NO
MAIN_UPDATED=NO
```

### Assumptions and done definition (recorded before any code change)

1. `slack_search_entry_mode` is a new `.mka/slack_config.json` key. Absent → `mention_mixed`,
   which is today's behaviour bit-for-bit. Only an explicit `slash_faceted_only` selects the new
   `/mka` product contract, so merging this branch cannot activate anything by itself. An
   unrecognised value fails closed at config load.
2. `slash_faceted_only` requires `enable_faceted_search=true` (and therefore the pinned taxonomy
   workbook/sha pair). An inconsistent pair is refused at load rather than silently degraded.
3. The modal changes (single-select year, 「全部年份」 sentinel, narrowing requirement) apply to the
   faceted modal in **both** modes. The faceted modal has never been production-activated
   (`PRODUCTION_ACTIVATED=NO` throughout this file), so it has no production behaviour to preserve;
   the "default unchanged" guarantee is about the `app_mention` natural-language path, which
   `mention_mixed` preserves exactly.
4. Slash entry authorization is its own typed field, `slash_command_allowed_channel_ids`, because
   `allowed_channel_ids` governs *channel-visible* disclosure while a `/mka` result is ephemeral and
   addressed to exactly one user. Absent → no conversation restriction (the stated product goal,
   safe because the result is invoker-only). An explicit `[]` is refused: "unrestricted" and
   "nothing allowed" must not be the same value.
5. `chat_postEphemeral` is a new executable posting API on this surface and is brought inside a
   posting boundary alongside `chat_postMessage`, with the source-level inventory test widened to
   both.

Done when every flag in the WP's DONE DEFINITION holds, targeted Slack tests pass, `compileall` and
`git diff --check` pass, mutation probes demonstrate each new guard actually guards, and a narrow
feature commit exists on this branch with `main` untouched.

### Slack `/mka` Faceted-Only Search Entry (this WP)

Full design record: `docs/specs/SLACK_MKA_COMMAND_FACETED_ONLY_ENTRY.md`.

**Entry mode.** One new key, `slack_search_entry_mode`, with two canonical values. Absent →
`mention_mixed`, which is today's behaviour bit-for-bit; only an explicit `slash_faceted_only`
selects the `/mka` product. An unrecognised value is refused at config load rather than defaulted,
because a typo would otherwise leave app-mention search alive on a deployment whose operator
believed they had switched it off. `slash_faceted_only` additionally requires
`enable_faceted_search=true`, since the modal is that mode's only search entry. A mode was chosen
over booleans because the alternatives are mutually exclusive readings of one question -- what does
an app mention mean? -- and independent booleans could contradict each other.

**`/mka`.** Registered only in slash mode. Acks first and unconditionally (Slack allows three
seconds), then calls `views_open` directly -- no intermediate button, no retrieval, no query
planning, no audit row. `command["text"]` is never read: `/mka`, `/mka 搜尋`, `/mka SHOPLINE` and
`/mka <restricted name>` produce an identical blank modal, asserted as view equality rather than as
a substring check, because the modal's own chrome legitimately contains words like 「搜尋」.

**App-mention migration.** In slash mode every mention returns the same short guidance naming
`/mka`, and returns it *before* the pagination store, `ask_fn` and every audit call. `agent_ask` is
untouched; CLI and internal natural-language search are unchanged.

**Year field.** Single `static_select` with 「全部年份」 leading and selected by default.
「全部年份」 is a UI sentinel that decodes to *no* `interview_year` constraint -- never to a
constraint carrying the sentinel, which would match nothing in the index while appearing in the
plan and audit row as though a year had been chosen. An unrecognised year value is refused rather
than coerced to 「全部年份」: coercion would turn a forged field into a whole-corpus search.
`interview_years` keeps its tuple type; the structured layer was not rewritten for a single-select
UI.

**Narrowing.** At least one of specific year / LV2 / content tag. Free text is deliberately absent
from that test, and 「全部年份」 leaves `interview_years` empty precisely so choosing it cannot
smuggle a whole-corpus search through. The refusal names the fields that would satisfy it, because
a user told only 「請至少填寫一個搜尋條件」 would reasonably retype into the free-text box.

**Result visibility.** Slash results are ephemeral to the invoker, routed from the entry point
recorded in `private_metadata` rather than from which fields happen to be populated, so a message
cannot become public because a thread timestamp was missing. Verified for public, private and DM
conversation id shapes.

**Authorization.** `allowed_channel_ids` is unchanged and still governs every channel-visible
message. `/mka` gets `slash_command_allowed_channel_ids`, absent → unrestricted, explicit `[]` →
refused. `allowed_exposure_channels` and every other data-governance policy are untouched.

**Session context.** A slash command carries no `thread_ts`, so each `/mka` mints an unguessable
session id, always combined with the payload-derived user id (`f"{user_id}:{session_id}"`) before
use. `SlackRequestTokenStore` and `pagination_key` had their third coordinate renamed
`thread_ts` → `session_key` -- the same check under an honest name, renamed rather than reused so
no call site could keep the old meaning by accident (the R2 `get()` → `resolve()` precedent).
Sensitive text stays out of button values and `private_metadata`; only opaque lane ids travel there.

**Pagination.** 「顯示更多」 is a button in slash mode, offered only while a page is actually
waiting. It replays already-rendered text: no retrieval, no reranking, no query planning, no new
audit row. Wrong user, unknown token and expired continuation are indistinguishable to the clicker.

**Posting boundary.** `chat.postEphemeral` is brought inside `post_slack_ephemeral`, with the same
properties as `post_slack_reply`. The source-level inventory test now covers both APIs and rejects
`chat_update`, `files_upload`, `say(` and `respond(` anywhere in the package. This is the bounded
NB-1 hardening the WP authorized, and nothing beyond it.

**Schema version.** `STRUCTURED_REQUEST_SCHEMA_VERSION` (v2) is added to `structured_search.py` and
folded into `catalog_version`, so a modal opened under the v1 multi-select schema is refused as
stale rather than decoded under v2 -- where its absent `selected_option` would read as 「全部年份」
and silently widen a year-restricted search.

#### Findings established by this WP, verified rather than assumed

1. **`chat_postEphemeral` does not declare the unfurl flags.** `slack_sdk` 3.43.0's binding has no
   `unfurl_links`/`unfurl_media` named parameters, unlike `chat_postMessage`. It accepts `**kwargs`
   and forwards them verbatim into the request body, so they are transmitted. Whether Slack's
   `chat.postEphemeral` acts on them is **not** established here and is a UAT check. The boundary
   forces them regardless; that cannot make unfurling more likely.
2. **`chat.postEphemeral` is not available in every conversation.** Slack requires the app to be
   able to post into the target conversation, so an invocation from a channel the bot was never
   added to can fail with `channel_not_found` even though the command was delivered. Not worked
   around here, deliberately: the alternatives are a `response_url` outbound path (outside this
   WP's scope and outside the posting boundary) or posting in-channel, which would break
   invoker-only visibility. The failure discloses nothing -- the result simply does not arrive --
   and it is the first thing UAT should probe. See the spec's "Known platform constraint on
   ephemeral posting".
3. **`slack_bolt` runs a slash-command listener asynchronously.** `dispatch` returns as soon as
   `ack()` fires and the rest of the handler continues on a worker thread. That is correct
   production behaviour for Slack's three-second deadline, but it makes a naive
   assert-immediately-after-dispatch test a race. Reproduced directly: 0 `views.open` calls
   immediately after dispatch, 1 after a 0.5s sleep. The slash contract fixture therefore uses
   bolt's synchronous `process_before_response=True`, which runs the same registered listener
   through the same dispatcher without the timing dependency.

#### Mutation-strength evidence

Every probe copies `src/` and `tests/` to `/private/tmp/mka-mka-probe`, mutates **there** so the
repository source is never touched, runs, and is deleted afterwards. The unmutated copy passes
(`178 passed`), so a failure below is the mutation and not the harness.

| Probe | Mutation | Result |
| --- | --- | --- |
| 0 | none (harness baseline) | `178 passed` |
| 1 | delete the guidance early return; app mentions search again | `8 failed` |
| 2 | restore `or request.free_text.strip()`; free-text-only search allowed | `4 failed` |
| 3 | stop treating the 「全部年份」 sentinel as "no constraint" | `13 failed` |
| 4 | use `/mka` trailing text as a modal prefill | `6 failed` |
| 5 | pagination button re-searches and writes a search audit row | `1 failed` |
| 6a | drop the token-store ownership gate only | `1 failed` |
| 6b | drop the user from the continuation lane key only | `1 failed` |
| 6c | drop both ownership guards | `1 failed` |
| 7 | post the slash result into the channel instead of ephemerally | `8 failed` |
| 8 | remove the 「全部年份」 default selection | `20 failed` |

Probes 6a and 6b initially passed, which was itself the finding: the two cross-user guards are each
independently sufficient, so a single-guard removal was invisible to every test. Two tests were
added to pin each guard on its own -- a show-more click with the correct lane and clicker but an
invalid token, and a unit assertion that the lane key is user-scoped -- after which 6a, 6b and 6c
all fail. Without those, a future refactor could delete one guard and leave the other silently
carrying the whole guarantee.

#### Files changed

Source: `slack_interface.py` (entry mode, config validation, `/mka` handler, show-more handler,
app-mention migration, ephemeral boundary, session helpers, entrypoint-aware submission),
`slack_faceted_search.py` (single-select year, 「全部年份」 sentinel, entrypoint/session in
`private_metadata`, show-more and restart blocks, v2 submission parsing),
`structured_search.py` (`STRUCTURED_REQUEST_SCHEMA_VERSION`, narrowing rule),
`search_facets.py` (schema version folded into `catalog_version`),
`slack_pagination.py` (`session_key` coordinate, additive `has_more`),
`slack_request_tokens.py` (`thread_ts` → `session_key`),
`slack_presentation.py` (entry-point-dependent continuation hint).

Tests: `test_slack_faceted_search.py`, `test_slack_faceted_search_interface.py`,
`test_slack_bolt_contract.py`, `test_slack_interface.py`, `test_structured_search.py`,
`test_search_facets.py`.

Docs: `docs/specs/SLACK_MKA_COMMAND_FACETED_ONLY_ENTRY.md`, this file.

#### Pre-existing assertions updated, and why each is not a weakening

- token-store context keyword `thread_ts=` → `session_key=` (mechanical; a wrong keyword raises
  immediately);
- year prefill `initial_options` (list) → `initial_option` (single), following the element type;
- `_modal_prefill` normalises 「全部年份」 to `[]`, so "this clicker sees none of the owner's
  filters" stays one comparison across all three fields;
- `private_metadata` equality now includes `entrypoint` and `session_id`, and a new test asserts it
  never carries the submitting user;
- `test_free_text_at_the_limit_passes_and_over_the_limit_is_refused` gained a narrowing facet so it
  still tests the length bound rather than the new narrowing rule;
- `test_no_slack_message_is_posted_outside_the_boundary` became
  `test_no_slack_message_is_posted_outside_a_boundary`, covering both posting APIs, plus a new test
  rejecting alternative posting APIs entirely.

```text
SLACK_ENTRY_MODE_IMPLEMENTED=YES
SLASH_COMMAND_REGISTERED_IN_CODE=YES
SLASH_COMMAND_DIRECT_MODAL=YES
SLASH_COMMAND_TEXT_IGNORED=YES
APP_MENTION_DIRECT_SEARCH_DISABLED=YES
APP_MENTION_GUIDANCE_ONLY=YES
YEAR_SELECTOR_SINGLE_SELECT=YES
ALL_YEARS_OPTION_IMPLEMENTED=YES
ALL_YEARS_DEFAULT_SELECTED=YES
ALL_YEARS_MEANS_NO_YEAR_CONSTRAINT=YES
ALL_YEARS_COUNTS_AS_NARROWING_CONSTRAINT=NO
ALL_YEARS_FREE_TEXT_ONLY_REFUSED=YES
SPECIFIC_YEAR_ONLY_SEARCH_VALID=YES
ALL_YEARS_AUDIT_SEMANTICS_CORRECT=YES
FREE_TEXT_ONLY_SEARCH_DISABLED=YES
RESULT_VISIBILITY_INVOKER_ONLY=YES
PAGINATION_BUTTON=YES
PAGINATION_RESEARCH=NO
REQUEST_TOKEN_CONTEXT_SAFE=YES
SENSITIVE_TEXT_RETENTION_REGRESSION=NO
CLICKABLE_ASSET_TITLE_PRESERVED=YES
NO_UNFURL_REGRESSION=YES
GOVERNANCE_REGRESSION=NO
PRODUCTION_CONFIG_CHANGED=NO
SLACK_APP_CONSOLE_CHANGED=NO
PRODUCTION_ACTIVATED=NO
BOT_STARTED=NO
MAIN_UPDATED=NO
```

#### Verification (Claude Code, 2026-08-28)

- Interpreter: the project venv at
  `/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent/.venv/bin/python` (Python 3.9.6,
  `slack_bolt` 1.29.0, `slack_sdk` 3.43.0). Import origin confirmed as **this** worktree's `src`
  before every run via an explicit `PYTHONPATH`, not the venv's editable install pointing at the
  main checkout.
- Targeted run (**23 files**: every Slack module plus structured search, facets, query gating,
  taxonomy, typed retrieval, pipeline, agentic, generation, governance evals, validation, content
  index, search-quality evaluation): **762 passed, 1 skipped, 0 failed.**
  *(Corrected 2026-08-28 per Codex R2 finding P3-2: this set was originally labelled "24 files".
  It is 23. See the R2 nonblocking cleanup section for the counted set and both figures.)*
- Test counts: `test_slack_faceted_search.py` 41 → 53, `test_slack_faceted_search_interface.py`
  44 → 95, `test_slack_bolt_contract.py` 7 → 10, `test_slack_interface.py` 50 → 52,
  `test_structured_search.py` 22 → 32, `test_search_facets.py` 16 → 17.
- `python -m compileall` on `src/marketing_knowledge_agent` and every changed test file: pass.
  `git diff --check`: pass.
- Read-only discipline: no `.mka/` directory and no stray `.sqlite` file exists in this worktree
  after the full run; `git status --short` lists exactly the files this WP touched and nothing else.
- Not run: the full application suite (the pre-existing gitignored-fixture blocker recorded
  throughout this file is unchanged, and this WP touched none of those subsystems);
  `tests/test_slack_structured_governance.py` (same pre-existing blocker); standalone lint/type
  tools (not configured in this repo); production sync, re-index, deploy, Slack Bot start/restart,
  or UAT.
- **Not verified: Slack itself.** No payload has been sent to Slack and the bot was not started.
  The live `views_open` / `view_submission` / `chat.postEphemeral` round trip, whether Slack honours
  the unfurl flags on an ephemeral message, whether an ephemeral message can be posted in every
  conversation shape the bot may be invoked from, and the real three-second `ack` deadline under
  production latency are all first exercised in UAT.

### Codex Independent Delta Review R1 and remediation (2026-08-28)

Reviewed candidate: `5954e10bd31b308c67e349b4d76f207c5558eb03`. Verdict **CHANGES_REQUESTED**,
**2 blocking findings**. Both were accepted, both **reproduced against the reviewed candidate
before any fix**, and both are now guarded by tests proven to fail without the fix. The R1
implementation record above is retained unchanged as that round's evidence.

The block below is **this round's state as of 2026-08-28**, not current state. `CODEX_R2_REVIEW`
was genuinely pending when it was written; R2 has since returned
PASS_WITH_NONBLOCKING_FINDINGS. Current state is the top-level Lock.

```text
CODEX_DELTA_REVIEW_R1=CHANGES_REQUESTED
R1_REVIEWED_CANDIDATE=5954e10bd31b308c67e349b4d76f207c5558eb03
R1_BLOCKING_FINDINGS=2
R1_BLOCKERS_REMEDIATED=2
CODEX_R2_REVIEW=PENDING
```

#### Finding 1 -- slash-only mention trailing text was persisted

**Reproduced first.** In `slash_faceted_only`, `handle_slack_event` ran the channel-authorization
check *before* the mode migration, so a mention from a DM or an unlisted channel wrote:

```text
slack_denied_channel,C_OTHER,U1,0,0,<@BOT> SECRET_CUSTOMER_NAME
slack_denied_channel,D1,U1,0,0,<@BOT> SECRET_CUSTOMER_NAME
```

My R1 test asserted only the allowed-channel case, so "never persisted" held exactly where it was
least at risk and failed where it mattered most: a DM is precisely where somebody types a customer
name without thinking.

**Fix.** Migration routing now precedes every audit path. The reasoning is that the same text which
is not a query in an allowed channel is not a query in a denied one either -- the authorization
path predates the mode and records `raw_question` because it was written for a natural-language
search surface. The denial itself is still recorded, because reaching this bot from an unauthorized
conversation is operational signal worth keeping; only the query column is dropped, and it is
dropped **by construction** rather than by matching anything in the text, so there is no redaction
pattern that could be wrong. An allowed-channel mention writes no row at all: guidance is neither a
query nor a denial.

`mention_mixed` is untouched, including its `slack_denied_channel` row and its query column, and a
test pins that so the slash-only fix cannot drift into a global removal of legacy audit.

#### Finding 2 -- legacy mention artifacts stayed executable after a mode switch

**Reproduced first**, as the full chain rather than its first link:

```text
legacy_button_opened=True
entrypoint='app_mention'  session_id=''
public_messages=2  ephemerals=0
```

A button posted before the switch carries no slash session, so the handler inferred
`entrypoint=app_mention`, wrote it into `private_metadata`, and the submission trusted it -- routing
a real governed search back into a public channel and breaking `RESULT_VISIBILITY=INVOKER_ONLY`.

**Fix.** `entrypoint_allowed_for_mode(mode, entrypoint)` is now the single rule, checked against the
mode in force **at execution time**, at three executable entry points: the open-modal action, the
show-more action, and -- independently -- the view submission. `private_metadata["entrypoint"]` is
not trusted merely because this app wrote it: it states how a view was opened, which is exactly the
fact that goes stale.

The view-handler gate is not redundant with the action gate, and that is the half a button-only fix
would miss: a modal opened *before* the switch and submitted *after* it never passes through today's
action handler at all. The mutation probes below show each layer failing a test the other does not.

The rule is symmetric and that is deliberate: under `mention_mixed` a slash-provenance interaction
also fails closed, because `/mka` is not registered there so no slash session can legitimately
exist.

Stale clickers get a fixed ephemeral pointer to `/mka` through the existing ephemeral boundary --
no public post, no echo of what was clicked, no prior query. A stale submission is refused through
`ack(response_action="errors")` alone, so no posting API is involved at all; the modal explains
itself instead of closing silently on a result that will never arrive.

#### Discovered while probing: a pre-existing test race, fixed

`tests/test_slack_bolt_contract.py`'s `bolt_app` fixture did not set `process_before_response=True`,
so bolt returned from `dispatch` as soon as the listener called `ack()` and finished the work on a
pool thread, racing every assertion after it. Measured: **3 failures in 15 whole-file runs under
CPU load** before, **0 in 15** after. Not one of the two blockers, pre-existing since the
no-unfurl WP, and fixed here rather than reported-and-left because a flaky harness would corrupt
the evidence this review round rests on. `slash_bolt_app` already used the same setting for the
same documented reason.

#### Mutation-strength evidence (R1 remediation)

Probes run against a copy under `/private/tmp/mka-mka-probe`, deleted afterwards; the repository
source was never mutated. Unmutated baseline: `121 passed`.

| Probe | Mutation | Result |
| --- | --- | --- |
| R1-A | move slash-only migration routing back below the raw-question audit | `3 failed` (denied channel, `im`, `mpim`) |
| R1-B1 | remove the **action** gate only | `6 failed` |
| R1-B2 | remove the **view-submission** gate only | `3 failed` |
| R1-B3 | remove both gates | `7 failed` |
| R1-B4 | make the rule asymmetric (`mention_mixed` accepts anything) | `2 failed` |

R1-B1 and R1-B2 each fail at least one test the other does not --
`test_a_legacy_mention_button_cannot_open_a_modal_in_slash_mode` for the action layer,
`test_a_legacy_modal_submitted_after_the_mode_switch_executes_nothing` for the view layer -- which
is what demonstrates the two layers protect independently rather than one covering for the other.

#### Verification (R1 remediation)

- Same suite as the R1 candidate, for comparable evidence: **778 passed, 1 skipped, 0 failed**
  (R1 candidate: 762 passed, 1 skipped; +16 from the tests added this round).
  *(Corrected 2026-08-28 per Codex R2 finding P3-2: that set is **23 files**, not 24. The figure
  itself — 778 passed, 1 skipped — was and is correct for it.)*
- Test counts: `test_slack_faceted_search_interface.py` 95 → 110,
  `test_slack_bolt_contract.py` 10 → 11.
- `compileall` on `src/marketing_knowledge_agent` and both changed test files: pass.
  `git diff --check`: pass. Import origin re-confirmed as this worktree's `src`.
- `git status --short` listed exactly the three files this remediation touched.
- Not run, unchanged from R1: full application suite;
  `tests/test_slack_structured_governance.py` (pre-existing gitignored `.mka/content_index.sqlite`
  fixture blocker -- **not** claimed as passing, and not a regression from this remediation);
  standalone lint/type tools (not configured); production sync, re-index, deploy, bot
  start/restart, or UAT. Slack itself is still unexercised.

### Codex Independent Delta Review R2 and nonblocking cleanup (2026-08-28)

Reviewed candidate: `0da023c2f2f606b0a0287334537168a8a24d93f2`. Verdict
**PASS_WITH_NONBLOCKING_FINDINGS**, **0 blocking**, **2 nonblocking**. Both nonblocking findings
were authorized for a narrow cleanup and are closed below. The R1 and R1-remediation records above
are retained unchanged as those rounds' evidence; the two suite-count lines carry an inline
correction rather than a rewrite, so what was originally claimed remains visible.

The block below is **this round's state as of 2026-08-28**, not current state.
`FINAL_SPOT_REVIEW` was genuinely pending when it was written; it has since returned PASS with 0
blocking and 0 nonblocking findings against `eb128b8`. Current state is the top-level Lock.

```text
CODEX_DELTA_REVIEW_R2=PASS_WITH_NONBLOCKING_FINDINGS
R2_REVIEWED_CANDIDATE=0da023c2f2f606b0a0287334537168a8a24d93f2
R2_BLOCKING_FINDINGS=0
R2_NONBLOCKING_FINDINGS=2
P3_1_MODE_AWARE_STALE_GUIDANCE=CLOSED
P3_2_SUITE_COUNT_CORRECTION=CLOSED
R1_FINDING_1_STILL_CLOSED=YES
R1_FINDING_2_STILL_CLOSED=YES
FINAL_SPOT_REVIEW=PENDING
```

#### P3-1 -- stale-artifact guidance now names the entry the current mode has

**Reproduced first**, on the reviewed candidate, at both refusal paths:

```text
mention_mixed + stale slash button   -> refused; guidance: 「請輸入 `/mka`…」
mention_mixed + stale slash modal    -> refused; ack errors: 「請輸入 `/mka`…」
/mka registered in this mode: False
```

The refusal was correct in both cases -- nothing opened, nothing executed, nothing posted -- so this
was never a security defect. It was advice that was itself stale: a user under `mention_mixed` was
told to type a command that mode never registers, so the remedy failed the same way the button did.

The message is now selected by the mode in force at execution time, by
`stale_entry_mode_message(mode)`, for the same reason `entrypoint_allowed_for_mode` is: what a user
should do next is a fact about the current configuration, not about the artifact they clicked.

| current mode | guidance |
| --- | --- |
| `slash_faceted_only` | 「搜尋入口已更新，請輸入 `/mka` 重新開啟搜尋。」 |
| `mention_mixed` | 「此搜尋操作已失效，請重新標記 @Marketing Knowledge Agent 開始搜尋。」 |

Both are fixed literals chosen by mode, never assembled from anything the interaction carried: no
old query, no button value, no request token, no free text, no `private_metadata` content. The
mention wording reuses `SHOW_MORE_MENTION`, which is already the single definition of how this bot
is addressed, so a rename cannot leave the two instructions disagreeing.

Nothing about refusal changed. `entrypoint_allowed_for_mode`, the action-time gate, the
view-submission gate and the show-more provenance gate are all untouched, and the R1 security
probes were re-run against this cleanup to confirm they still bite (below).

#### P3-2 -- comparable-suite figures, counted rather than asserted

The reviewer was right and the correction is confirmed independently here by counting the set and
running both:

| set | files | result |
| --- | --- | --- |
| comparable set used across R1, R1-remediation and R2 | **23** | R2 candidate: `778 passed, 1 skipped` |
| superset, adding `tests/test_content_index_lineage.py` | **24** | R2 candidate: `799 passed, 1 skipped` |

`tests/test_content_index_lineage.py` alone is `21 passed`, which is exactly the difference. The
records above said "24-file / 778", conflating the two; the file count was wrong, the figure was
right for the set actually run.

`tests/test_slack_structured_governance.py` remains **NOT_RUN / SETUP_BLOCKED_BY_EXISTING_FIXTURE**
— its gitignored `.mka/content_index.sqlite` dependency is absent from this isolated worktree. It
is not claimed as passing, and it is not a regression from any round of this WP.

#### Mutation-strength evidence (R2 cleanup)

Probes run against a copy under `/private/tmp/mka-mka-probe`, deleted afterwards; the repository
source was never mutated. Unmutated baseline: `123 passed`.

| Probe | Mutation | Result |
| --- | --- | --- |
| P31-shared | route both modes back to the single `/mka` message | `3 failed` |
| P31-swapped | return each mode's message for the other mode | `7 failed` |
| R1-A (re-run) | move slash-only migration routing back below the raw-question audit | `3 failed` |
| R1-B1 (re-run) | remove the **action** gate only | `6 failed` |
| R1-B2 (re-run) | remove the **view-submission** gate only | `4 failed` |

The swapped probe matters more than the shared one: it proves the tests assert *which entry point
each message names* rather than comparing the resolver's output to the same constant it returns,
which would have passed however the two sentences were exchanged. The three R1 re-runs are included
because this cleanup edits code inside both gate paths, so "the security remediations still hold"
is checked rather than assumed — and R1-B1/R1-B2 still each fail a test the other does not.

#### Verification (R2 cleanup)

- Comparable 23-file suite: **780 passed, 1 skipped, 0 failed** (R2 candidate: 778; +2 from the
  tests added by this cleanup).
- 24-file superset: **801 passed, 1 skipped, 0 failed** (R2 candidate: 799).
- `test_slack_faceted_search_interface.py` 110 → 112. No other test file changed.
- `compileall` and `git diff --check`: pass. Import origin re-confirmed as this worktree's `src`.
- `git status --short` listed exactly the two files this cleanup touched; no `.mka/`, no stray
  `.sqlite`.
- Not run, unchanged: full application suite; `tests/test_slack_structured_governance.py` (see
  above); standalone lint/type tools (not configured); production sync, re-index, deploy, bot
  start/restart, or UAT. Slack itself is still unexercised.

### Human UAT Phase 1 and the R1 routing remediation (2026-08-28)

Controlled UAT ran against an isolated runtime built from the reviewed code candidate `eb128b8`,
with the operational config, content index and checkout untouched throughout (hashes verified
before and after).

```text
HUMAN_UAT_PHASE_1=PASS_WITH_BLOCKING_FINDING
UAT_BOT_STOPPED_BY_HUMAN=YES
UAT_BOT_STOP_METHOD=SIGINT
UAT_BOT_EXIT_130_EXPECTED=YES
UAT_BOT_RESTARTED=NO
BLOCKING_FINDING=SLASH_DELIVERY_DEPENDS_ON_BOT_MEMBERSHIP
OBSERVED_SLACK_ERROR=channel_not_found
```

The bot was stopped deliberately by the operator with `kill -INT`; exit code 130 is that SIGINT,
not a crash. It has not been restarted, and no code below was written while it was running.

#### What passed, live, against real data

Preserved as recorded. These are live-Slack results, not test outcomes:

| behaviour | result |
| --- | --- |
| `/mka` opens the modal directly | PASS |
| `/mka` trailing text ignored | PASS |
| 「全部年份」 default, year single-select | PASS |
| free-text-only refused; 「全部年份」 alone refused | PASS |
| specific-year-only search; 「全部年份」 + LV2 search | PASS |
| 調整條件; 重新搜尋 blank | PASS |
| 顯示更多 pagination | PASS |
| app mention returns guidance only | PASS |
| ephemeral delivery in a known member channel | PASS |
| clickable approved URLs; unfurl suppression | PASS |

Observed examples: a 2024-only search returned 6 brands / 15 assets; 「全部年份」 + LV2 女裝
returned 5 brands / 7 assets. The audit log recorded 11 searches over ~22 minutes, one channel, one
user, 10 of 11 with citations, 0 warnings, 0 denylist refusals.

#### The blocking finding

A `/mka` from a conversation outside `slash_command_allowed_channel_ids` entered the denial branch
correctly, then failed to deliver the denial:

```text
handle_faceted_search_command → post_slack_ephemeral(... DENIED_CHANNEL_MESSAGE ...)
  → chat.postEphemeral: {'ok': False, 'error': 'channel_not_found'}
```

The user saw nothing at all. The spec had predicted the constraint for the *result* path and
flagged it as the first thing UAT should probe; what it did not anticipate is that the *refusal*
path shares it, and fires precisely in the conversations the bot is least likely to belong to. The
restrictive first-round allowlist — recommended here to protect the success path — therefore made
this more likely to be hit, not less.

#### Remediation: response_url replaces membership-dependent delivery

Every slash-originated message now leaves through the `response_url` Slack attaches to the command
or interaction that produced it. `chat.postEphemeral` is gone from the package entirely, and its
helper was removed rather than left unused, because an unused posting helper is what a future
handler reaches for. `views.open` is unchanged: it uses `trigger_id` and never depended on
membership.

A `response_url` is a bearer capability, so `slack_response_urls.py` holds it as a credential:
memory only; excluded from `repr`; never in `private_metadata`, a button value, a request token, a
pagination key or an audit row; exact-host HTTPS allowlist checked before storing (an arbitrary host
would make this a request-forgery primitive); TTL below Slack's documented ~30 minutes; hard budget
of 5 sends; bound to user + channel + session, with unknown/expired/wrong/exhausted all resolving
to the same `None`.

The reply path is checked **before** retrieval. The failure worth preventing is a search that
succeeds and then has nowhere to go.

Buttons refresh the capability from their own interaction, so a long session does not spend down
the ageing command capability; ownership is not refreshed with it — user and channel still come
from the payload.

#### Presentation changes (nonblocking UX, same round)

Modal: `Sales Category LV2` → 品牌產業別; 內容相關標籤 → 你在找什麼功能？; free-text label extended.
Applied conditions: 品牌產業別 / 功能, mapped in the Slack renderer rather than in `FIELD_REGISTRY`.
Result card: Handle / LV1 / LV2 lines removed. A Block Kit hint now warns about 「全部年份」 before
submission.

**Display only.** Block ids, action ids, request fields, taxonomy and audit names are unchanged, and
`merchant_handle` / `sales_category_lv1` / `sales_category_lv2` are still carried and still drive
grouping, conflicting-handle removal and data-conflict marking. The narrowing rule is unchanged:
「全部年份」 still narrows nothing and is still refused alone.

#### Mutation-strength evidence (UAT R1)

Probes run against a copy under `/private/tmp/mka-mka-probe`, deleted afterwards; the repository
source was never mutated. Unmutated baseline: `271 passed`.

| Probe | Mutation | Result |
| --- | --- | --- |
| R-1 | slash result routed back to the channel-posting path | `25 failed` |
| R-2 | persist the capability into `private_metadata` | `2 failed` |
| R-3 | drop the user binding from the capability store | `35 failed` |
| R-4 | drop the channel/session binding | `34 failed` |
| R-5 | allow more than five sends | `5 failed` |
| R-6 | remove the adjust/restart capability refresh | `4 failed` |
| P-1 | restore the old modal labels | `2 failed` |
| P-2 | restore Handle/LV1/LV2 on the result card | `4 failed` |
| P-3 | remove the conflicting-handle protection | `1 failed` |

P-3 matters most of the nine: it is the one that proves hiding the three lines did not disable the
rule that reads them.

#### Verification (UAT R1 remediation)

- Comparable 23-file suite: **802 passed, 1 skipped, 0 failed** (previous round: 780).
- Plus the new `tests/test_slack_response_urls.py` (24 files): **834 passed, 1 skipped**.
- 25-file superset incl. `test_content_index_lineage.py`: **855 passed, 1 skipped**.
- `compileall` over `src/` and `tests/`, and `git diff --check`: pass. Import origin re-confirmed
  as this worktree's `src`.
- No live Slack call was made and the UAT bot was not restarted.
- `tests/test_slack_structured_governance.py` remains NOT_RUN / SETUP_BLOCKED_BY_EXISTING_FIXTURE.

#### Observed but deliberately not changed

`slack_output_preview._render_detailed` still prints `Sales Category LV1/LV2` and `Handle`. It is a
separate offline preview surface, not the modal and not the live result card, and UAT did not
examine it. Renaming there was out of this round's scope; recorded so the divergence is a decision
rather than an oversight.

### Independent Delta Review of the UAT R1 remediation, and the security remediation (2026-08-28)

Reviewed candidate: `76b0b3fc2c376546cd5aaf03f880ff3b6578ec8d` (code+tests
`7055905f1354f184467fb233dd1ab8f5751f942d`). Verdict **CHANGES_REQUESTED**, **4 blocking findings**,
all HIGH. All four were accepted, all four **reproduced against the reviewed candidate before any
fix**, and each is now guarded by a test proven to fail without its fix.

The Human UAT R1 record above is retained unchanged as that round's evidence.

```text
UAT_R1_DELTA_REVIEW=CHANGES_REQUESTED
UAT_R1_REVIEWED_CANDIDATE=76b0b3fc2c376546cd5aaf03f880ff3b6578ec8d
UAT_R1_REVIEW_BLOCKING_FINDINGS=4
UAT_R1_SECURITY_BLOCKERS_REMEDIATED=4
UAT_R1_SECURITY_REVIEW_2=PENDING
```

The block above is **this round's state**, not a claim of acceptance. No review has passed this
candidate, and no live UAT has been run against it.

#### Finding 1 (HIGH) — the use budget could be double-spent concurrently

**Reproduced first.** `take()` checked liveness and decremented as two separate steps, so two
threads holding a one-use capability could both pass the check before either decremented. A plain
barrier at the call site did not surface it — the window is between two operations *inside*
`take()`, so a probabilistic test can run for a long time without landing in it. Holding the window
open explicitly showed it immediately:

```text
capability had remaining_uses = 1
concurrent successful takes  = 2
```

**Fix.** All mutable store state is now behind one `threading.RLock`, and verification plus
decrement happen in a single critical section. The GIL is not a substitute: the race is between
bytecode operations, not inside one.

#### Finding 2 (HIGH) — URL validation was incomplete, and redirects were followed

**Reproduced first:** `https://user:pass@hooks.slack.com/…`, `https://hooks.slack.com:444/…` and
`https://hooks.slack.com:8443/…` were all accepted.

**Fix.** Validation now requires HTTPS, an exact approved host, no username, no password, port
absent or exactly 443 (with an unparseable port refused rather than treated as absent), no
fragment, and a path under a Slack response_url root. The GovSlack host was dropped: commercial
Slack is the deployment target, and an approved host that is never used is an allowance with no
benefit.

Redirects are refused outright, because validating the first hop authorizes nothing about the
second. A probe found the first version of this test was **vacuous** — it pointed the redirect at a
dead port, where a followed redirect fails too, so it passed with the guard removed. It now uses a
live capture server and asserts that server received nothing. It is also parametrised over
301/302/303/307: `urllib` already refuses 307 on a POST, so 307 alone would have proved nothing;
301/302/303 are the codes the standard library *does* follow, and where the guard actually bears.

#### Finding 3 (HIGH) — the SDK could log the bearer capability

**Reproduced first:** `slack_sdk` 3.43.0's webhook client accepts a `logger`, ships
`ConnectionErrorRetryHandler` by default, and its request path can emit `req.full_url` — which *is*
the capability.

**Fix.** The SDK client was dropped for a few lines of standard library inside the same reviewed
boundary. It logs nothing at all, and every failure is re-raised as `SlackResponseUrlError` whose
message is fixed text; `from None` suppresses the chained original, because `urllib`'s own
exceptions carry the full URL in `HTTPError.url` and often in `str(exc)`. Logs are captured at
DEBUG in tests, so a lower application level is not what is hiding the secret.

#### Finding 4 (HIGH) — the pre-retrieval check was observational, not a reservation

**Reproduced first:** `can_reply()` returned `True`, another handler consumed the final use, and the
later `take()` returned `None` — a search would have run with nowhere to send its result.

**Fix.** `can_reply()` and `take()` are gone from the store's API. The only way executable code
obtains a capability is `reserve()`, which verifies and consumes one use atomically and returns a
send-once reservation. The submission reserves **after** the request is validated and **before** any
retrieval; the same reservation is what authorizes the outbound message at the end.

Validation errors deliberately reserve nothing — they answer through `ack` alone, and spending a use
there would let a handful of ordinary mistakes exhaust a session that never ran a search.

#### Send and refresh semantics, recorded because they are judgement calls

- **A failed send is not refunded.** Slack may have received and acted on the request even when the
  client saw a transport error, so re-spending the use could exceed the server-side budget and
  deliver the message twice.
- **One reservation is at most one HTTP attempt.** No retry, by construction.
- **A refresh does not revoke an outstanding reservation.** Its use was consumed atomically when it
  was issued, so it is a send already paid for; revoking it would drop a reply the user is owed
  rather than prevent one they are not.

#### Mutation-strength evidence (security remediation)

Probes run against a copy under `/private/tmp/mka-mka-probe`, deleted afterwards; the repository
source was never mutated. Unmutated baseline: `259 passed`.

| Probe | Mutation | Result |
| --- | --- | --- |
| R7 | remove the store lock | `2 failed` |
| R8 | reserve *after* retrieval instead of before | `2 failed` |
| R9 | accept userinfo in a response_url | `3 failed` |
| R10 | accept an arbitrary port | `2 failed` |
| R11 | follow redirects | `3 failed` (301/302/303) |
| R12 | log the request URL, as the SDK client would | `4 failed` |
| R13 | retry automatically | `7 failed` |

R11 is worth reading twice: it **did not fail** on the first attempt, and that was the finding — the
test was asserting the wrong thing. Both the test and this table reflect the version that bites.

Retained probes were re-run against this candidate and still bite: R-2 capability-in-metadata
(`2 failed`), R-3 drop user binding (`14 failed`), R-5 unbounded uses (`14 failed`), R-6 no refresh
(`4 failed`), P-2 restore card metadata (`3 failed`), P-3 remove the conflicting-handle guard
(`1 failed`).

R-5 initially **hung** rather than failing, and that was a defect in the tests rather than in the
product: two helpers drained a lane with `while remaining_uses > 1`, which never terminates once
the store stops decrementing. Both are now bounded by the budget. A test that hangs reports
nothing, and under a mutation harness it reports nothing precisely when something is wrong.

#### Verification (security remediation)

- Comparable 23-file suite: **807 passed, 1 skipped, 0 failed** (previous round: 802).
- Plus `tests/test_slack_response_urls.py` (24 files): **884 passed, 1 skipped**.
- 25-file superset incl. `test_content_index_lineage.py`: **905 passed, 1 skipped**.
- `tests/test_slack_structured_governance.py`: **NOT_RUN / SETUP_BLOCKED_BY_EXISTING_FIXTURE** —
  2 passed, 20 errors, all the pre-existing gitignored `.mka/content_index.sqlite` dependency this
  isolated worktree has never had. Not claimed as passing, and not a regression from this round.
- `compileall` over `src/` and `tests/`, and `git diff --check`: pass. Import origin re-confirmed
  as this worktree's `src`.
- No live Slack call was made; the UAT bot was not restarted; nothing was pushed.

### Independent Security Review R2 and the third remediation (2026-08-28)

Reviewed candidate: `0470246dee7327546687b7ec23ab62783c0910d9` (code+tests
`6e742fca494f3ee8e9925699fd6fb292187ea0b6`). Verdict **CHANGES_REQUESTED**, **4 blocking findings**,
all HIGH/P1. All four accepted, all four **reproduced against the reviewed candidate before any
fix**, each now guarded by a test proven to fail without its fix.

The previous rounds' records are retained above unchanged. What R2 confirmed as *fixed* by the
prior round — store-level atomic reserve, reserve-before-retrieval, and the host / userinfo / port
/ redirect base checks — is unchanged by this round and re-verified below.

```text
UAT_R1_SECURITY_REVIEW_2=CHANGES_REQUESTED
UAT_R1_SECURITY_REVIEW_2_REVIEWED_CANDIDATE=0470246dee7327546687b7ec23ab62783c0910d9
UAT_R1_SECURITY_REVIEW_2_BLOCKING_FINDINGS=4
UAT_R1_SECURITY_REVIEW_2_REMEDIATED=4
UAT_R1_SECURITY_REVIEW_3=PENDING
```

This block is **this round's state**, not acceptance. No review has passed this candidate and no
live UAT has been run against it.

#### R2-1 (HIGH) — one reservation could be spent twice concurrently

**Reproduced first:** two threads handed the same reservation object both passed
`if not self._spent` before either set it — 2 successful spends, and 2 outbound requests.

The store's lock did not cover this: it protects the *budget*, and this is the single authorization
that budget already bought. The reservation now owns a `threading.Lock` and the unspent→spent
transition happens inside it; the loser gets an exception rather than the URL, so a second send
cannot be attempted.

#### R2-2 (HIGH) — a reservation could be duplicated and replayed

**Reproduced first:** `copy.copy` and `copy.deepcopy` produced a clone carrying the same bearer URL
with its own fresh `_spent = False`, so the original could send and the clone could send again.
`pickle.dumps` was worse — it serialised the capability itself into bytes (`SECRET_CAPABILITY`
present in the output) that a revived object could spend.

`ResponseReservation` is no longer a dataclass. It uses `__slots__` (no `__dict__` to copy or
pickle) and refuses `__copy__`, `__deepcopy__`, `__reduce__` and `__getstate__` with a `TypeError`
carrying no URL.

A test-strength note found while probing: `deepcopy` and `pickle` also fail *incidentally*, because
the owned `Lock` cannot be copied. A test accepting any `TypeError` would therefore have kept
passing with the explicit guards deleted. The test now asserts the refusal is ours.

#### R2-3 (HIGH) — the sanitized error still carried the secret

**Reproduced first:** the escaping `SlackResponseUrlError` had `__cause__ = None` but
`__context__` = the original `HTTPError`, whose `.url` is the full capability.

`raise ... from None` clears `__cause__` and suppresses traceback *rendering*; it does not remove
the context object, which any structured reporter walking an exception tree will find.

The sanitized error is now raised **outside** every `except` block — nothing is being handled at
that point, so `__context__` is `None` as well — and the locals holding the URL and the `Request`
are deleted first, for reporters that serialise frame locals. Only a fixed string and an integer
status cross the boundary.

Tests walk the whole tree (`str`, `repr`, `args`, `__cause__`, `__context__`, URL-bearing
attributes, and traceback frame locals, recursively) across HTTP 400, HTTP 500, refused redirect
and connection refusal. A companion test feeds the walker a deliberately unsanitized error and
asserts it *does* find the secret, so the other assertions cannot pass vacuously.

#### R2-4 (HIGH) — the path allowlist was a prefix test

**Reproduced first:** every one of these was accepted —
`/commands/a/../../services/x`, `/commands/%2e%2e/x`, `/commands/%2E%2E/x`,
`/commands/a%2f..%2fservices/x`, `/commands/a%5c..%5cx`, `/commands/../admin`, `/commands/%zz`,
and `?a=1` query strings.

Validation is now structural, applied to the **raw** path exactly as it will be sent — nothing is
decoded and re-checked, because a validator that normalises differently from the HTTP client is one
that can be walked past. Four gates: well-formed `%` escapes, none decoding to `/`, `\`, `.` or a
control byte; no raw control characters or backslashes; no empty, `.` or `..` segments; and a first
segment naming an approved family with at least one segment after it. Query strings are refused.

**Endpoint families, established from the repository rather than guessed:** this surface receives
`response_url` from exactly two payload kinds — slash commands (`/commands/…`) and interactive
actions (`/actions/…`). The allowlist is those two. `/services/` (incoming webhooks) was in the
previous list and is removed: this app never receives one.

#### Proxy behaviour — recorded, not changed

R2 noted stdlib `ProxyHandler` may honour `HTTPS_PROXY`. Verified directly: it is **absent** in a
process with no proxy variables (the handler registers no methods and is dropped) and **present**
in one that has them. So whether a response_url request traverses a proxy is a property of the
deployment, not of this code. Classified non-blocking by the review and **left unchanged**;
pinning it would change behaviour in a proxied network, which is an operator's decision. It is
documented in the transport and recorded here so it is not mistaken for solved.

#### Mutation-strength evidence (third remediation)

Probes run against a copy under `/private/tmp/mka-mka-probe`, deleted afterwards; the repository
source was never mutated.

| Probe | Mutation | Result |
| --- | --- | --- |
| R14 | remove the reservation's own lock | `2 failed` |
| R15 | allow copy / deepcopy / pickle | `7 failed` |
| R16 | raise the sanitized error inside the handler again | `13 failed` |
| R17 | restore prefix-only path validation | `21 failed` |
| R18 | permit encoded structural bytes | `11 failed` |
| R19 | accept any `hooks.slack.com` path family | `4 failed` |

Earlier probes on boundaries this round touched were re-run and still bite: R7 store lock
(`2 failed`), R8 reserve-before-retrieval (`2 failed`), R9 userinfo (`3 failed`), R10 non-443 port
(`2 failed`), R11 redirect (`3 failed`), R13 retry (`8 failed`).

#### Verification (third remediation)

- Comparable 23-file suite: **807 passed, 1 skipped, 0 failed** (unchanged from the previous round;
  this round's additions are all in the capability suite).
- Plus `tests/test_slack_response_urls.py` (24 files): **931 passed, 1 skipped**
  (previous round: 884).
- 25-file superset incl. `test_content_index_lineage.py`: **952 passed, 1 skipped**.
- `tests/test_slack_structured_governance.py`: **NOT_RUN / SETUP_BLOCKED_BY_EXISTING_FIXTURE** —
  2 passed, 20 errors, the same pre-existing gitignored `.mka/content_index.sqlite` dependency.
  Not claimed as passing.
- `compileall` over `src/` and `tests/`, and `git diff --check`: pass. Import origin confirmed as
  this worktree's `src`, with `pytest`, `pydantic` and `slack_bolt` all importable — the reviewer's
  inability to run these suites was an environment limitation on their side, not a code fault.
- No live Slack call; UAT bot not restarted; nothing pushed.

### Superseded lock record: closed Slack Faceted Search MVP / no-unfurl milestone

The lock below is the previous milestone's closure record. It is retained unchanged as that
milestone's evidence; this WP does not alter its state, and `main` is still not updated.

- State: released — no active implementer
- Milestone state: CLOSED
- Task: Slack Faceted Search MVP and Slack no-unfurl Human UAT remediation
- Implementer: none
- Review provenance:
  - Slack Faceted Search MVP Codex R3: `PASS_WITH_NONBLOCKING_FOLLOWUPS`; reviewed SHA
    `313fbf7ac2745f2397369db3e2129f1978e03bef`
  - Slack no-unfurl independent Delta Review: `PASS_WITH_NONBLOCKING_FINDINGS`; reviewed candidate
    `0ae710f822fe797df79337a07ed18435c8cf8d88`; 0 blocking findings; NB-1 nonblocking and deferred
- Integration: Slack Faceted Search MVP reviewed and integrated; Slack no-unfurl remediation
  merged via PR #3
- Merge commit on `main`: `d580f8335ea8f08be7045f30460bdc95fa3b3567`
- Closure reconciliation branch: `codex/docs/close-slack-unfurl-remediation` (documentation only)
- Product state: merged, post-merge verified and closed; production activation remains off
- Started at: 2026-08-27
- Closed at: 2026-08-28

```text
ACTIVE_IMPLEMENTER=NONE
TASK_LOCK=RELEASED
SLACK_FACETED_SEARCH_ACCEPTED=YES
SLACK_FACETED_SEARCH_INTEGRATED=YES
HUMAN_UAT=PASS
SLACK_FACETED_SEARCH_R3_REVIEW=PASS_WITH_NONBLOCKING_FOLLOWUPS
SLACK_FACETED_SEARCH_R3_REVIEWED_SHA=313fbf7ac2745f2397369db3e2129f1978e03bef
SLACK_UNFURL_DELTA_REVIEW=PASS_WITH_NONBLOCKING_FINDINGS
SLACK_UNFURL_DELTA_BLOCKING_FINDINGS=0
SLACK_UNFURL_REVIEWED_CANDIDATE=0ae710f822fe797df79337a07ed18435c8cf8d88
MERGED_PR=3
MERGE_SHA=d580f8335ea8f08be7045f30460bdc95fa3b3567
MAIN_UPDATED=YES
POST_MERGE_VERIFICATION=PASS
REMEDIATION_CLOSURE=CLOSED
PRODUCTION_ACTIVATED=NO
BOT_STARTED=NO
NB1_STATUS=NONBLOCKING_DEFERRED
```

The detailed R1/R2 review, mutation, UAT and remediation records below are preserved as historical
evidence. Statements inside those dated sections describe what was true at that point and do not
override the current released lock and closed milestone above.

### Historical implementation lock snapshot (2026-08-27)

At this point in the implementation history, the lock state was:

- State: active
- Milestone state: SLACK_FACETED_SEARCH_MVP_CODEX_REVIEW_R2_REMEDIATED_AWAITING_R3_REVIEW
- Task: Slack Faceted Search MVP
- Implementer: Claude Code
- Reviewer: Codex — R1 CHANGES_REQUESTED (6 findings) against `3a7648f`, all remediated; R2
  CHANGES_REQUESTED (1 blocking finding, cross-user prefill disclosure) against `934d719`,
  remediated below. **This WP is NOT reviewed and NOT accepted.** Codex R3 review pending.
- Branch: codex/impl/slack-faceted-search-mvp
- Worktree: `/private/tmp/mka-slack-faceted-search-mvp` (isolated; does not touch the running UAT
  bot's worktree/process, and does not touch the main worktree at
  `/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent`, whose `main` was left exactly as found —
  behind `origin/main` by 20 commits (a clean fast-forward gap, not a divergence) and dirty with
  unrelated pre-existing local files that this WP did not read, touch, stash, or commit)
- Baseline commit: 5e1f73ce34d6c5b6791d9d0ff126c4dc0c784c40 (= `origin/main` at start; local `main`
  was behind by 20 commits and dirty, so the new worktree was created directly from
  `origin/main`/this worktree's own prior HEAD rather than from the stale local `main`)
- Product behavior changed: YES (Slack surface only, default OFF; existing behavior bit-for-bit
  unchanged when `enable_faceted_search=false`, the default)
- Intended scope: a structured, Block-Kit-driven Slack search entry point (year / Sales Category
  LV2 / content-tag multi-select + optional free text) that builds `TypedQueryPlan` hard constraints
  directly rather than routing selections back through the natural-language parser, reducing
  dependence on Search Taxonomy Authority resolution for the three fields a user can now pick
  explicitly. New `search_facets.py` (`FacetCatalog`), `structured_search.py`
  (`StructuredSearchRequest` + plan builder + execution), `slack_faceted_search.py` (Block Kit view
  and payload parsing); additive changes to `query_planning.py` (`"in"`/`"contains_any"` operator
  execution, `preresolved_fields` parameter), `slack_presentation.py` (multi-value constraint
  rendering) and `slack_interface.py` (new `SlackConfig` fields, trigger detection, action/view
  handler registration). No production `.mka/slack_config.json` change, no Slack Bot start/restart,
  no production sync, no production re-index, no Stable Record V2 activation, no row_v1 retirement,
  no Search Taxonomy Authority workbook modification, no `allowed_exposure_channels` policy change,
  no unrelated refactor.
- Started at: 2026-08-27
- Last updated: 2026-08-27

```text
IMPLEMENTATION_AUTHORIZED=YES
UAT_ACTIVATION_AUTHORIZED=NO
PRODUCTION_ACTIVATION_AUTHORIZED=NO
MAIN_UPDATE_AUTHORIZED=NO
PRODUCTION_REINDEX_AUTHORIZED=NO
UAT_RUNTIME_UNCHANGED=YES
UAT_ACTIVATED=NO
PRODUCTION_ACTIVATED=NO
MAIN_UPDATED=NO
CODEX_REVIEW_R1=CHANGES_REQUESTED
CODEX_REVIEW_R1_FINDINGS=6
CODEX_REVIEW_R1_REMEDIATED=6
CODEX_REVIEW_R2=CHANGES_REQUESTED
CODEX_REVIEW_R2_BLOCKING_FINDINGS=1
CODEX_REVIEW_R2_REMEDIATED=1
CODEX_R3_REVIEW=PENDING
INTEGRATION_ORDER_DECIDED=YES
```

Integration order settled by `DEC-20260827-01`: this branch merges to `main` first and the Search
Taxonomy Slack wiring WP adapts to it. That decision settles **order only** — `main` promotion still
requires Codex re-review to pass and a separate explicit authorization.

### Codex review R2 — cross-user prefill disclosure (2026-08-27)

Reviewed snapshot: `934d719dae44e1b031f57a508333b1fa5a369709`. R1's six findings passed. One new
**blocking** finding, reproduced before being fixed.

**The blocker.** The "調整條件" button is posted into a Slack thread, so every member who can see it
can click it. The request token resolved on presentation alone — it was bound to a
`StructuredSearchRequest` and to nothing else — so U2 clicking U1's button reopened the modal
prefilled with U1's filters *and* U1's free-text goal. Search intent typed into what looks like a
private dialog was therefore readable by the whole channel. Reproduced directly: `store.get(token)`
returned U1's `free_text` verbatim to a caller that supplied no identity at all.

A second half was reproduced in the same pass and is arguably worse: `request_token_store.store(...)`
ran **unconditionally**, including after a denylist refusal. A restricted customer name typed into
the free-text box was retained in the shared token store and a prefill button offering it was
published — the exact disclosure the refusal exists to prevent.

**The fix**, scoped to the blocker and nothing else:

- The token store now holds a `RequestContext` envelope — request, `owner_user_id`, `channel_id`,
  `thread_ts`, `expires_at` — instead of a bare request. `store()` requires all three context
  values and **refuses empty ones**, because an empty stored value would compare equal to an empty
  derived value and silently turn the check off for exactly the requests whose provenance is least
  clear. `get()` is replaced by `resolve(token, *, user_id, channel_id, thread_ts)`, which returns
  `None` unless all three match; the rename is deliberate so no call site could keep the old
  unchecked behaviour by accident.
- Unknown, expired and "not yours" are deliberately indistinguishable to the clicker: all three
  open an empty modal. Reporting "not yours" would confirm that someone else's search exists.
- The handler derives `(user_id, channel_id, thread_ts)` from the **interaction payload**
  (`body.user.id`, `container.channel_id`/`channel.id`, `container.thread_ts`/`message_ts`), never
  from the button's `value`. The value is content this bot posted into a channel and every member
  sees the same copy, so it describes the button rather than the person pressing it. An incomplete
  payload fails closed rather than defaulting to empty strings. `channel_id`/`thread_ts` were
  removed from the button value entirely rather than left as untrusted duplicates.
- The refusal path stores nothing and offers no prefill button. It posts a token-free 「重新搜尋」
  button (`build_restart_search_message`) that opens a blank modal. Kept as its own builder so
  "no token" is a property of the call site that decided it, not an argument that could default
  its way in.
- The owner reopening in the same channel and thread still works unchanged.

Test count 116 → 130 (+14). Full suite failed/skipped/errors unchanged at 137/65/72; passed
1424 → 1438, matching the added tests exactly.

#### R2 mutation-strength evidence

Probes run on a copy under `/private/tmp/mka-r2-probe`, deleted afterwards; the repository source
was never mutated.

| Probe | Mutation | Result |
| --- | --- | --- |
| Owner check | drop `owner_user_id` from `RequestContext.matches` | `3 failed` — at all three layers: store unit test, handler test, real-bolt routing test |
| Refusal path | `if refused:` → `if False:` (store and offer prefill again) | `1 failed` — the denylist-refusal retention test |
| Context source | trust the button value's `channel_id`/`thread_ts` instead of the payload | `1 failed` — the different-thread test |

#### Accepted nonblocking backlog, recorded not fixed (R2)

Explicitly out of scope for this remediation, per instruction:

1. `catalog_version` does not incorporate a denylist hash, so a denylist change alone does not
   invalidate an in-flight catalog version.
2. `SQLiteIndex` opens the content index read-write; `assert_readable_content_index` prevents
   *creating* one but the connection is not yet `mode=ro`.
3. A facet option value longer than 75 characters is still truncated by the Block Kit builder.

### Codex review R1 findings and remediation (2026-08-27)

Reviewed commit: `3a7648f576f091d120c17ea22db8762456156f99`. Six findings, all accepted, all
remediated. Every one was **reproduced first** against the reviewed commit, then fixed, then guarded
by a test proven to fail without the fix (mutation probes below). None was taken on faith.

| # | Finding | Reproduced as | Remediation |
| --- | --- | --- | --- |
| 1 | Denylist audit leak | `slack_faceted_search` row contained `text=SECRET_CUSTOMER_NAME 的成長案例`; and `denylist_query_hit` landed under the bare `command,event,match_count` schema, recording **neither** channel nor user | `is_restricted_refusal()` added; the search audit row is skipped entirely on refusal (parity with the NL path's `slack_qa` skip); `query_audit_metadata` threaded through `ask_index` → `precheck_restricted_query`, so the hit is written under the Slack schema with channel/user and an empty query column |
| 2 | Button `value` 2000-char limit | A maximal request serialized to **3206 chars, 1206 over** — Slack rejects the whole message, so the user gets no button | New `slack_request_tokens.py` (TTL + entry bounded, in-memory, mirrors `slack_pagination`); the button carries a fixed-width opaque token, so payload size is now independent of free-text length. `_button_value()` asserts the 2000 budget and raises rather than truncating; `max_length` set on the Block Kit input **and** re-validated server-side (refuse, never truncate) |
| 3 | Denylist must fail closed | `{"brand_name": "..."}` (valid JSON, not a list) returned an **empty denylist with no warning at all** — indistinguishable downstream from a genuinely empty one | `load_required_governance_index()` refuses missing / unparseable / non-array denylists. Called at startup (before App/SocketModeHandler), in `build_facet_catalog`, and in `execute_structured_search`. Pure browse can no longer run without a loaded governance index — the parameter lost its `None` default entirely |
| 4 | Approved asset URL parity | The faceted result bypassed the overlay, the refusal guard and the unavailable-audit code | Same `enable_approved_asset_urls` flag, same `not refused` guard, same payload-free `APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE` audit code, in the view-submission handler |
| 5 | Pagination lifecycle | `start()` self-supersedes, but a refusal/unstructured reply produced no pages, so the **previous** search's continuation stayed live and 「顯示更多」 could resume it | `pagination_store.discard(thread_key)` before the reply, on every branch |
| 6 | Read-only + Slack platform guards | `SQLiteIndex.load_chunks()` on a missing path **created a 0-byte `.sqlite` file** before failing; no `multi_static_select` option-count guard existed | `assert_readable_content_index()` checks before opening, so nothing is created; `_multi_select_block` raises `SlackFacetModalError` above 100 options, naming the facet, the limit, and the `external_select` remedy |

`allowed_exposure_channels=[]` semantics are untouched by this remediation, as instructed.

#### Mutation-strength evidence

Each probe copies `src/` and `tests/` to `/private/tmp/mka-faceted-probes`, mutates **there** so the
repository source is never touched, runs, and is then deleted. A test that does not fail under its
probe is not guarding anything.

| Probe | Mutation | Result |
| --- | --- | --- |
| 1a | `refused = False` (write the search row despite refusal) | `2 failed` — audit-leak test + refusal overlay-guard test |
| 1b | drop `query_audit_metadata` from the `execute_structured_search` call | `1 failed` — the hit loses channel/user attribution |
| 2 | remove the 2000-char assertion from `_button_value` | `1 failed` |
| 3 | make `load_required_governance_index` tolerant again | `7 failed` across all three layers (facets, execution, startup) |
| 4 | `overlay_issue = None` (remove approved-URL parity) | `2 failed` |
| 5 | remove `pagination_store.discard(thread_key)` | `1 failed` — the refusal case, precisely the one that was broken |
| 6a | remove the content-index existence guard | `2 failed` — both "does not create an empty database" tests |
| 6b | remove the 100-option guard | `1 failed` |

### Slack Faceted Search MVP (this WP)

Full design record: `docs/specs/SLACK_FACETED_SEARCH_MVP.md`.

Implemented behind `SlackConfig.enable_faceted_search` (default `false`). `load_slack_config`
requires `search_taxonomy_workbook`/`search_taxonomy_sha256` together whenever either is present
(regardless of the flag) and requires both when the flag is on; `run_slack_bot` loads the pinned
Authority and builds a `FacetCatalog` exactly once at startup, before the `slack_bolt` `App` or
Socket Mode handler is constructed, and any load failure propagates unchanged (fail closed, no
fallback). The `open_faceted_search_modal` action handler and the `faceted_search_modal` view
handler are registered only when the flag is on; `handle_slack_event` only recognises the "搜尋"／
"條件搜尋" trigger phrase when told the flag is on, and otherwise behaves exactly as before.

`FacetCatalog` offers a year/LV2/tag only when it is both an Authority canonical value and actually
carried by at least one document that would survive the existing external-governance,
non-retrievable-record-type, pending-metric and restricted-customer-denylist filters — counted by
distinct `document_id`, never by chunk. LV1 has no field on the type at all. `catalog_version` is a
pure function of the Authority's pinned sha256, a hash of the content-index file's own bytes, and
the catalog builder's own schema version; a stale submission (version mismatch) is refused before
execution and the user is told to reopen the modal.

`StructuredSearchRequest` selections become `QueryConstraint`s directly — `"in"` for
`interview_year`/`sales_category_lv2`, `"contains_any"` for `content_tags` (operators the field
registry already declared but nothing previously executed) — never serialized back into natural
language for the free-text parser to re-resolve. `build_query_plan` gained an additive
`preresolved_fields` parameter so a free-text goal is only parsed for taxonomy fields the modal left
untouched; any residual constraint or taxonomy ambiguity concerning an already-modal-decided field
is dropped, while an ambiguity spanning an undecided field still blocks. Retrieval order is always
hard structured filters before lexical/semantic scoring (reusing the pre-existing
`SQLiteRetriever.search` contract, which already filtered before scoring); a pure structured browse
(blank free text) is ordered deterministically by interview year (newest first) then a stable
per-record id, rather than depending on the content index's unspecified row order. Restricted
-customer, non-retrievable and pending records are excluded identically on both the free-text
(`pipeline.ask_index`-reused) and pure-browse execution paths, and `apply_governance_to_answer`/
`enforce_external_citations` run on both. A submitted search never widens or overrides a
modal-selected field with free text, and a zero-result search reports the filters actually applied
without auto-relaxing them; "調整條件" (reopening the modal prefilled from the prior selection) is
the only relaxation path in this MVP.

Every entry point (the original `app_mention`, the button-click action, and the view submission)
re-validates `allowed_channel_ids` independently from its own payload; `private_metadata` carries
only `channel_id`, `thread_ts` and `catalog_version`. A new `slack_faceted_search` audit event
reuses the existing Slack audit CSV schema and records only the structured facet selection, the
catalog version, and the free-text goal — the same class of content the pre-existing `slack_qa`
event already records for natural-language queries.

Tests after remediation: `tests/test_search_facets.py` (16), `tests/test_structured_search.py` (22),
`tests/test_slack_faceted_search.py` (32, Block Kit view/payload only),
`tests/test_slack_faceted_search_interface.py` (33, `run_slack_bot`/handler wiring via a hand-built
fake `App`/`SocketModeHandler`/client — no real Slack connection),
`tests/test_structured_query_operators.py` (8, the additive `query_planning.py` operator/
`preresolved_fields` behaviour), and `tests/test_slack_bolt_contract.py` (5, real `slack_bolt`
dispatcher, offline — see the contract-verification section above) — **116 tests** (83 at `3a7648f`, +28 from this remediation, +5 from bolt contract verification), all
synthetic/hermetic fixtures, no gitignored production DB, Vault, token or real Slack. `compileall`
and `git diff --check` both pass, and no `.mka/` directory or stray `.sqlite` file exists in this
worktree after the full run — the read-only claim in finding 6, checked rather than asserted.

Targeted run: the 5 new files plus every existing test file that touches `query_planning`,
`slack_interface`, `slack_presentation`, `search_taxonomy`, `pipeline`, `retrieval`, or structured
-result rendering (`test_query_gating.py`, `test_search_taxonomy.py`, `test_typed_query_retrieval.py`,
`test_pipeline.py`, `test_slack_interface.py`, `test_slack_search_presentation_v1.py`,
`test_slack_search_presentation_v2.py`, `test_slack_exact_alias_query.py`,
`test_slack_exact_alias_truncation.py`, `test_slack_retriever_truncation_propagation.py`,
`test_slack_output_preview.py`, `test_content_index.py`, `test_content_index_lineage.py`,
`test_search_quality_evaluation.py`, `test_production_search_alias_runtime.py`,
`test_slack_structured_governance.py`, plus `test_agentic.py`, `test_generation.py`,
`test_governance_evals.py`, `test_validation.py`): **676 passed, 1 skipped**, plus 20 pre-existing
errors, all in `test_slack_structured_governance.py`, all `FileNotFoundError` on the same gitignored
`.mka/content_index.sqlite` fixture dependency this isolated worktree never had — unrelated to any
file this WP touched.

Full suite after remediation: **137 failed, 1424 passed, 65 skipped, 72 errors**. The
failed/skipped/errors counts (137/65/72) are unchanged from both the pre-remediation run of this WP
and the counts already on record in this file for the preceding milestones; the passed count moved
1391 → 1419 (+28 remediation) → 1424 (+5 bolt contract), matching exactly the tests added, and the set of failing/erroring
*files* is identical to the pre-remediation set. Every one belongs to an unrelated subsystem
(governance decision store, production search-alias plan/confirmation/execution, parent sync,
historical fixture immutability, sample vault, store-data-sync plan v2) whose gitignored production
fixtures are absent from this isolated worktree — the same pre-existing environment blocker recorded
repeatedly elsewhere in this file, not a regression this WP introduced.

Not run / not verified: comprehensive adversarial review; standalone lint/type tools (not configured
in this repo); production sync, re-index, deploy, Slack Bot start/restart, or UAT activation. The 26
pre-existing failing/erroring test files were identified by name and subsystem but not individually
re-run against a clean baseline in this round. Codex re-review of this remediation has not happened.

**Still unverified after the bolt contract tests below: Slack itself.** The Block Kit views are
validated against `slack_sdk`'s own view model, which is the closest offline proxy available, but no
payload has ever been sent to Slack. The live `views_open` / `view_submission` round trip, the real
3-second `ack` deadline under production latency, and whether Slack renders the modal as intended
are all first exercised in UAT.

### Real-`slack_bolt` contract verification (Claude Code, 2026-08-27)

Every other Slack test in this WP drives a hand-built fake `App`, which proves the handlers do the
right thing and proves nothing about whether `slack_bolt` will ever call them. That gap was worth
closing on its own evidence: `build_required_kwargs` **does not raise** when a listener declares an
argument bolt cannot inject — it logs `"<name> is not a valid argument"`, omits the kwarg, and the
failure surfaces as a `TypeError` at the first real button click, in UAT, in front of a user.

`tests/test_slack_bolt_contract.py` (5 tests) registers the real handlers on a real `slack_bolt.App`
and dispatches synthetic `block_actions` / `view_submission` payloads through bolt's own dispatcher.
It stays hermetic by stubbing `WebClient.api_call` — the single funnel every Slack API method goes
through, patched at class level because bolt clones a fresh `WebClient` per request since 1.15, so
an instance stub is simply bypassed. Any unexpected Slack API call raises rather than escaping to
the network. Verified in both orders that `monkeypatch` restores it and the patch does not leak into
the other Slack suites (`220 passed`, `82 passed`).

Confirmed against real bolt: both handlers' argument names are injectable; a button click routes to
`views_open`; `slack_sdk`'s `View.validate_json()` accepts the modal and it contains no LV1; a
submission routes to a governed search posting exactly two messages with a `request_token` button;
and `ack(response_action="errors")` survives bolt's response serialization with the error bound to a
real `block_id`.

Two fixture defects in the harness were found and fixed rather than worked around — a real
`view_submission` payload carries `view.type`, and Socket Mode delivers the already-parsed payload
dict as the body. Both were harness bugs; neither indicated a product defect.

Mutation probe: renaming the view handler's `view` parameter to `submitted_view` (a name bolt cannot
inject) fails 3 of the 5 contract tests, including the dedicated arg-injectability test, which
compares against bolt's own injectable set and therefore catches any wrong name regardless of what
the fake-`App` tests happen to call it.

### Superseded lock record: B2 Real Writer Regression Test Hardening

The lock below describes a separate, unrelated WP on a different branch
(`codex/test/b2-governed-writer-regression`) that this worktree's copy of this file happened to
carry forward from its baseline commit. It is retained unchanged as that WP's record; nothing in
this Slack Faceted Search MVP WP alters its state, its branch is untouched by this worktree, and
`main` is still not updated by either.

- State: active (on its own branch, not this one)
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

### B2 Real Writer Regression Test Hardening

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

### Slack unfurl suppression — narrow UAT UX remediation (Claude Code, 2026-08-28)

- Branch: `codex/fix/slack-disable-unfurl`, worktree `/private/tmp/mka-slack-disable-unfurl`,
  baseline `23c697fed33a9f311f08c1baf8787e5030fd90ae`. Nothing here alters any other WP's state.
- Finding, from Human UAT of the Faceted Search controlled run: search results render an asset
  title as `<approved-url|title>`, and Slack answers each link with a preview card — SHOPLINE
  article summary, "Written by" / "Time to read" metadata, a full-width image, a YouTube
  thumbnail. One search posts several assets, so a thread becomes unreadable.
- Fix: `post_slack_reply` — the helper the `app_mention` path already used — is now the single
  posting boundary for this module, and forces `unfurl_links=False` / `unfurl_media=False` on
  every message. The five direct `client.chat_postMessage` calls in the faceted view-submission
  handler now route through it. The flags are written *after* the reply dict is unpacked, so no
  call site can re-enable previews. `views_open`, the modal payload, the renderers, the approved
  URL authority, ranking, taxonomy, pagination semantics and governance are untouched.
- Inventory: six `chat_postMessage` call sites existed, all in `slack_interface.py`; one remains,
  inside the boundary. No `say()` / `respond()` / `chat_update` / `chat_postEphemeral` /
  `files_upload` exists anywhere in `src/`. A test asserts both facts over the source, so a future
  handler cannot quietly reopen the finding.
- Tests: **+12**, by collected count — `test_slack_interface.py` 44 → 50,
  `test_slack_faceted_search_interface.py` 39 → 44, `test_slack_bolt_contract.py` 6 → 7.
  Coverage: natural-language reply, pagination page 2,
  faceted structured result, adjust-filters follow-up, restart-search after a refusal (which is
  also the unstructured-reply branch), stale-catalog refusal, the "@Bot 搜尋" trigger reply, the
  clickable approved link surviving the boundary, and — through real `slack_bolt` plus a stubbed
  `WebClient.api_call` — the flags surviving `slack_sdk`'s own serialization.
- One pre-existing assertion was updated rather than added to: `test_fake_client_receives_reply_dict`
  compared the whole posted dict for equality, so it now includes the two flags the boundary adds.
  Every other existing assertion indexes by key and was unaffected.

#### Mutation-strength evidence

Probes ran against a copy under `/private/tmp/mka-unfurl-probe`, deleted afterwards; the
repository source was never mutated.

| Probe | Mutation | Result |
| --- | --- | --- |
| 1 | drop `unfurl_links=False` from the boundary | `12 failed` |
| 2 | drop `unfurl_media=False` from the boundary | `12 failed` |
| 3 | let the adjust-filters call site call `chat_postMessage` directly | `3 failed` — the centralization test, the faceted-result test, and the real-bolt serialization test |
| 4 | make `_asset_title` return a plain title (no link) | `1 failed` — proves the clickable-link assertion is not vacuous |

#### Targeted verification

- `tests/test_slack_interface.py`, `test_slack_faceted_search.py`,
  `test_slack_faceted_search_interface.py`, `test_slack_bolt_contract.py`,
  `test_slack_search_presentation_v1.py`, `test_slack_search_presentation_v2.py`,
  `test_slack_exact_alias_query.py`, `test_slack_exact_alias_truncation.py`,
  `test_slack_retriever_truncation_propagation.py`, `test_slack_output_preview.py` —
  **410 passed**, 0 failed. `python -m compileall src/marketing_knowledge_agent` and
  `git diff --check` both pass.
- Import origin confirmed as this worktree's `src` before every run, not the venv's editable
  install pointing at the main checkout.
- Not run: full suite; `tests/test_slack_structured_governance.py` (pre-existing gitignored-fixture
  blocker in this worktree, unrelated); standalone lint/type tools (not configured); Slack itself —
  no message has been sent and the bot was not started.

```text
SLACK_UNFURL_DISABLED=YES
UNFURL_LINKS_FALSE=YES
UNFURL_MEDIA_FALSE=YES
NL_SEARCH_COVERED=YES
FACETED_SEARCH_COVERED=YES
PAGINATION_COVERED=YES
CLICKABLE_ASSET_TITLE_PRESERVED=YES
APPROVED_URL_AUTHORITY_CHANGED=NO
SEARCH_SEMANTICS_CHANGED=NO
PAGINATION_SEMANTICS_CHANGED=NO
PRODUCTION_CONFIG_CHANGED=NO
BOT_RESTARTED=NO
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

### UAT activation runbook (documentation only, 2026-08-27)

`docs/specs/SLACK_FACETED_SEARCH_UAT_RUNBOOK.md` closes the implementation WP's final deliverable
("完成後只提供受控 UAT 啟用步驟，由使用者另行授權"). Nothing in it has been executed and it grants
no authorization; `UAT_ACTIVATION_AUTHORIZED=NO` still holds.

One operational finding, verified read-only, is worth surfacing here because it is a behaviour
change an operator would otherwise meet as a mysterious startup failure: `run_slack_bot` resolves
the content index and the restricted-customer denylist from **relative** defaults against the
process CWD, and both are now **hard startup preconditions** when `enable_faceted_search=true` —
where previously a missing denylist merely attached a warning to an answer. Verified: the running
UAT bot's CWD is the main worktree, where both exist and the denylist is a valid JSON array of 11
records, so it would satisfy the new loader; this feature worktree has neither, so starting there
without first copying the gitignored runtime state fails closed at startup by design. The runbook
gives both options and prefers copying over moving, since PID 42332 is reading the originals.

## Next exact action

### Current: Slack `/mka` Faceted-Only Search Entry

**Controlled `/mka` UAT Activation.** Implementation, R1 remediation, R2 cleanup, Final Spot Review
and Clean Integration Verification are all complete, and PR #5 is open against `main`. Nothing
below is authorized by this record; each is its own gate and needs explicit authorization:

1. create the `/mka` slash command in the Slack App Console;
2. set `slack_search_entry_mode` to `slash_faceted_only` in `.mka/slack_config.json` (which also
   requires `enable_faceted_search=true` and the pinned taxonomy workbook/sha pair);
3. restart the Slack Bot;
4. run Human UAT.

Until then: the product is **not activated**, the Slack App Console is **unchanged**, the bot is
**not started**, and `main` is **not updated**. Merging PR #5 by itself would still activate
nothing, because the default entry mode is `mention_mixed` — today's behaviour bit-for-bit.

The first things UAT should probe are the two live-Slack facts this code cannot establish on its
own, both recorded in the spec: whether `chat.postEphemeral` succeeds in every conversation shape
`/mka` can be invoked from (a bot that was never added to a channel can get `channel_not_found`),
and whether Slack honours the unfurl flags on an ephemeral message.

### Historical: Slack Faceted Search MVP (preceding milestone, closed)

- No further action is outstanding. The MVP is reviewed and integrated, the no-unfurl remediation
  is merged through PR #3, post-merge verification passed, and the remediation is closed.
- NB-1 remains a separate nonblocking deferred finding; no hardening is authorized by this closure.
- Production activation remains off, and no bot start or restart is authorized by this record.

### Separate, still-open tracks (unchanged by this WP)

- Review the B2 real-writer regression tests on `codex/test/b2-governed-writer-regression`. They
  are uncommitted; nothing has been staged, committed or pushed.
- Separately, await explicit user authorization to promote
  `codex/integrate/stable-shadow-search-taxonomy` to `main`. The integration candidate is prepared
  and verified; nothing has been pushed to `main`. Do not activate Stable Record V2, retire row_v1,
  authorize a production re-index, or wire the taxonomy to Slack as part of that promotion — each
  is a separate decision.

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

- The Slack Faceted Search / no-unfurl task lock was released on 2026-08-28 after successful
  post-merge verification and closure adjudication.
- Active implementer: none. No next task or implementer is assigned by this reconciliation.
- Historical transfer record: implementer transferred Codex → Claude Code on 2026-08-26 after
  Codex completed read-only discovery only; milestone anchor
  `5d4a21a327cf2dbb128e9ce21b07224d1e57bf84` remained unchanged.
- Search Taxonomy v1 was committed as `ef970ee57f9ce91b29a0604ff3b1b540e88110c1`;
  Stable Record Shadow, Content Index Lineage Gate and Search Taxonomy v1 remain frozen.
- Release/transfer: complete; no transfer required.
