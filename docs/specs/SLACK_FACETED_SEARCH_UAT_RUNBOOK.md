# Slack Faceted Search MVP — controlled UAT activation runbook

**Status: NOT AUTHORIZED. This document describes steps; it does not authorize them.**

```text
UAT_ACTIVATION_AUTHORIZED=NO
PRODUCTION_ACTIVATION_AUTHORIZED=NO
MAIN_UPDATE_AUTHORIZED=NO
```

Written to satisfy the implementation work package's closing requirement ("完成後只提供受控 UAT
啟用步驟，由使用者另行授權"). Nothing here has been executed. Every path, hash and precondition
below was verified read-only on 2026-08-27; anything that could not be verified is marked as such.

Prerequisite that is **not** satisfied yet: Codex re-review of `3a7648f..b33218d`. Do not run this
runbook before that review passes. See `CURRENT_WORK.md` (`CODEX_RE_REVIEW=PENDING`).

---

## 0. The thing most likely to go wrong

`run_slack_bot` resolves everything except `--config` from **relative** defaults:

| What | Default | Resolves against |
| --- | --- | --- |
| content index | `.mka/content_index.sqlite` | the process's CWD |
| restricted-customer denylist | `reports/excel_preview/restricted_customers.json` | the process's CWD |
| audit log | `reports/audit_log.csv` | the process's CWD |

Both of the first two are **gitignored runtime state**, and both are now **hard startup
preconditions** when `enable_faceted_search=true` — previously a missing denylist only attached a
warning to an answer. So the CWD the bot starts from decides whether it starts at all.

Verified 2026-08-27:

- The currently running UAT bot (PID 42332) has CWD `/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent`,
  where both files exist (index 843,776 bytes; denylist a valid JSON array of 11 records — so it
  passes the new fail-closed loader).
- The feature worktree `/private/tmp/mka-slack-faceted-search-mvp` has **neither**, plus no
  `.mka/search_alias_projection.json` and no `.mka/llm_config.json`.

**Therefore: starting the bot from the feature worktree without first providing that state will fail
closed at startup.** That is the intended behaviour, not a bug — but it will look like the feature is
broken if you are not expecting it. Choose one of the two options in §3.

---

## 1. Preconditions

1. Codex re-review of `3a7648f..b33218d` has passed. **(Currently: PENDING.)**
2. You have decided the `SlackConfig` field collision with the separate Search Taxonomy Slack
   wiring WP (both branches independently add `search_taxonomy_workbook` /
   `search_taxonomy_sha256`; see `CURRENT_WORK.md`). UAT of this branch alone does not require the
   decision, but shipping both to `main` does.
3. You have the pinned Search Taxonomy workbook on disk and know its path. There is deliberately
   **no production default** — the Authority must be named explicitly.
   Expected sha256, on record in `SEARCH_TAXONOMY_AUTHORITY_V1_SPEC.md` and re-verified in this WP:

   ```text
   7e6ecffc2d4ad9b931c8abc2d75345305c0a93026ecb1cb10841aa5e8c6597a3
   ```

4. `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are set in the environment. Do not put them in the
   config file; `run_slack_bot` reads them from the environment only.

Verify the pin before anything else — if this does not match, stop:

```bash
shasum -a 256 "<path-to-taxonomy-workbook.xlsx>"
```

---

## 2. Config change

Add three keys to the Slack config the UAT bot uses. **Leave every existing key as it is.**

```json
{
  "enable_faceted_search": true,
  "search_taxonomy_workbook": "<absolute path to the pinned .xlsx>",
  "search_taxonomy_sha256": "7e6ecffc2d4ad9b931c8abc2d75345305c0a93026ecb1cb10841aa5e8c6597a3"
}
```

Validation rules that will reject the config outright:

- the workbook path and the sha256 must be given **together** — a lone one of either is refused
  regardless of the flag;
- `enable_faceted_search: true` requires both;
- with the flag absent or `false`, behaviour is unchanged bit-for-bit and the taxonomy is never
  loaded.

Back up the existing config first. Keep the backup: §5 rollback is "restore it".

---

## 3. Where to run it from

**Option A — run from the main worktree (simplest, but requires merging first).**
The runtime state is already there and the CWD is already correct. Requires this branch to be on
whatever `main` worktree checks out, i.e. it is a post-merge option, and merging to `main` is not
authorized. Not available yet.

**Option B — run from the feature worktree (available now, needs runtime state).**
Copy the gitignored runtime state in. This is an established pattern in this project (see the
"linked worktree needs untracked state staged" note in prior WP records). **Copy, never move** — the
running bot at PID 42332 is reading the originals.

```bash
cd /private/tmp/mka-slack-faceted-search-mvp
mkdir -p .mka reports/excel_preview
rsync -a "/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent/.mka/" .mka/
rsync -a "/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent/reports/excel_preview/" reports/excel_preview/
```

Then confirm the two hard preconditions are satisfied:

```bash
python3 -c "import json,pathlib; p=pathlib.Path('reports/excel_preview/restricted_customers.json'); d=json.loads(p.read_text()); print('denylist:', type(d).__name__, len(d), 'records')"
ls -l .mka/content_index.sqlite
```

The denylist must print `list`. A `dict` will be refused at startup — deliberately, because a
non-array denylist is silently read as *empty* by the upstream loader.

---

## 4. Start, and what to check

Only one bot may hold the Socket Mode connection. **Stopping PID 42332 is a separate decision and is
not authorized by this document.** Do not start a second bot against the same app while the first is
running — both will receive events.

```bash
python3 -m marketing_knowledge_agent.cli slack-bot --config <your-config.json>
```

Startup is fail-closed and ordered: taxonomy pin → denylist → facet catalog → **then** the Slack
connection. If any of the three fails, the process exits before Socket Mode ever opens. A successful
start therefore already proves the Authority hash matched, the denylist loaded, and the catalog
built.

Smoke checks in an allowlisted channel, in order:

1. `@Bot 搜尋` → a message with one 「開啟條件搜尋」 button. No search runs.
2. Click it → the modal opens, titled 案例條件搜尋, with three multi-selects and one text box.
   **Confirm no Sales Category LV1 field appears** — it must not exist.
3. Submit with nothing selected → an inline field error, no message posted.
4. Select one 採訪年份 → results post in-thread, followed by a 「調整條件」 button.
5. Click 調整條件 → the modal reopens **prefilled** with that year.
6. If the result paged: `@Bot 顯示更多` → continues correctly.
7. Confirm the existing free-text `@Bot <question>` path still behaves exactly as before.

Everything above except the live Slack round trip is already covered by automated tests. Steps 1–6
are specifically what only UAT can verify: that Slack accepts these payloads, renders the modal as
intended, and meets the 3-second `ack` deadline under real latency.

---

## 5. Rollback

No migration, no schema change, no index write — rollback is entirely a config revert.

1. Stop the bot you started (the one you started, not PID 42332).
2. Restore the backed-up config, or set `enable_faceted_search` to `false`.
3. Restart.

With the flag off, the taxonomy is never loaded, no handler is registered, the trigger phrase is
not recognised, and the surface is bit-for-bit the pre-existing one.

If you used Option B, the copied `.mka/` and `reports/` in the feature worktree are disposable
copies; deleting them affects nothing. The originals were never written to — the faceted surface
opens the content index read-only and never creates one.

---

## 6. Explicitly out of scope

None of the following is authorized by this document, and none is required by it:

- stopping, restarting or reconfiguring the currently running UAT bot (PID 42332);
- merging to `main`;
- production activation (this is UAT only);
- production sync, re-index or deploy;
- Stable Record V2 activation or row_v1 retirement;
- any change to the Search Taxonomy Authority workbook;
- any change to `allowed_exposure_channels` policy.
