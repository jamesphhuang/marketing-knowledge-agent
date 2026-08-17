# Slack Approved Asset URL — Production Activation Acceptance

Date: 2026-08-17

This record is a governance and operational acceptance document. It records a completed
controlled production enablement. It changes no application code, no runtime config, and no
index, and it authorizes no further rollout beyond the state described here.

## 1. Executive Acceptance Decision

```text
PRODUCTION_ACTIVATION_ACCEPTED = YES
```

The Slack approved asset-level URL feature has completed main integration and controlled
production enablement. Production smoke evidence (two positive merchants and one negative
case), binding verification, and the post-activation audit check all passed. The feature is
live in the production Slack runtime with a verified, non-destructive rollback path.

This acceptance covers the activation described below. It does not authorize schema
changes, re-indexing, authority rebuilds, or expansion of exposure scope.

## 2. Authoritative Code State

| Item | Value |
| --- | --- |
| Repository | `jamesphhuang/marketing-knowledge-agent` |
| Authoritative remote main | `ae7fd6c05fe832c6da2bd77f5ea46dd20bda753f` |

This SHA has already completed independent runtime-authority acceptance, WAL/cache P1
closure, the push gate, merge-readiness review, and fast-forward integration into main.

## 3. Accepted Security / Governance Gates

| Gate | Status |
| --- | --- |
| DG-01 | CLOSED |
| F-01 | PRESERVED |
| STALE_AUTHORITY_P2 | CLOSED |
| CACHE_P1 | CLOSED |

## 4. Production Activation Mechanism

| Item | Value |
| --- | --- |
| Runtime config source | `.mka/slack_config.json` |
| Feature | `enable_approved_asset_urls = True` |
| Activation mechanism | CONFIG_ONLY |
| Restart requirement | PROCESS_RESTART |

Activation changed configuration only. No application code was modified to enable the
feature.

### Activation sequence

Before activation:

- The previous Slack bot process (PID 64518) was stopped. That process predated the
  accepted runtime-authority code, so a restart was required for the accepted code to take
  effect.
- A config backup was created at
  `.mka/slack_config.json.before-approved-url-enable-20260817-1050.bak`.
- The backup was verified byte-identical before the config edit.

After activation:

- A new Slack bot was launched via `.venv/bin/mka slack-bot`.
- Slack Bolt reported `Bolt app is running`.
- Exactly one `mka slack-bot` process was confirmed running.

Credentials are supplied through the environment variables `SLACK_BOT_TOKEN` and
`SLACK_APP_TOKEN`. No secret values were inspected, recorded, or reproduced in this record.

## 5. Runtime / Index / Binding State

Production content index: `.mka/content_index.sqlite`

Readiness immediately before activation:

| Check | Value |
| --- | --- |
| Index exists | YES |
| `journal_mode` | `delete` |
| WAL | absent |
| SHM | absent |

Binding result:

| Metric | Value |
| --- | ---: |
| Binding result | MATCH |
| Bound asset count | 205 |
| Approved URL value count | 412 |
| Authority errors | 0 |
| Mapping mismatches | 0 |
| Distinct merchants with approved URLs | 107 |

Coverage: 205 / 205 indexed assets in the binding surface resolve approved URLs.

## 6. Production Smoke Evidence

### Smoke test 1 — 三風製麵

Query: 我要尋找三風製麵的案例

Result: **PASS** — returned 1 merchant, 2 assets.

Article:

- Title: 傳統製麵廠的數位轉型之路！《三風製麵》如何透過 SHOPLINE 提升客單價超過兩成
- URL: `https://blog.shopline.tw/merchant-showcase-shanfeng/`

Video:

- Title: 傳統製麵廠的數位轉型之路！《三風製麵》如何透過 SHOPLINE 提升客單價超過兩成｜SHOPLINE TALKS 聊品牌 EP 89
- URL: `https://www.youtube.com/watch?v=WIMy_AFA0pE`

| Acceptance criterion | Result |
| --- | --- |
| Article link clickable | YES |
| Video link clickable | YES |
| Article / video separation | PASS |
| Cross-type URL | NONE |
| 資料未提供 regression | CLOSED |

### Smoke test 2 — 怡和家電

Query: 怡和家電

Result: **PASS** — returned 1 merchant, 2 assets.

| Asset | URL |
| --- | --- |
| Article | `https://blog.shopline.tw/merchant-showcase-yh/` |
| Video | `https://youtu.be/7nVLtH5iW20` |

| Acceptance criterion | Result |
| --- | --- |
| Other-merchant URL support | CONFIRMED |
| Article / video separation | PASS |
| Feature hard-coded to 三風製麵 | NO |

## 7. Negative-Case Evidence

Query: 鮮乳坊

Result:

```text
找不到相關內容。請換個關鍵字,或聯繫管理者確認資料是否已收錄。
```

| Acceptance criterion | Result |
| --- | --- |
| Fabricated URL | NONE |
| Unrelated merchant URL | NONE |
| Fallback URL | NONE |
| Unexpected exposure | NONE |

This validates the no-result path only. The separate offline readiness review already
validated that a missing approved URL remains 資料未提供, that restricted / pending / internal
content does not gain URLs, and that a binding mismatch fails closed.

## 8. Post-Activation Audit Result

The production audit was checked for `APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE`.

```text
Result: NO MATCHES
```

No binding or authority-unavailable event was observed during the production smoke
sequence.

## 9. Rollback Procedure

Rollback is config-only:

1. Set `enable_approved_asset_urls = False` in `.mka/slack_config.json`, or remove the key
   entirely — an absent key defaults to `False`.
2. Stop the current Slack bot process.
3. Relaunch exactly one `.venv/bin/mka slack-bot`.

Rollback does **not** require:

- Code rollback
- Re-index
- Excel change
- Governance-decision change
- Authority rebuild for the current matching index

```text
ROLLBACK_READY = YES
```

## 10. Operational Invariant — Re-Index and Authority Rebuild

> **APPROVED URL AUTHORITY IS BOUND TO THE CONTENT INDEX.**

If a future re-index changes the approved identity surface — `source_record_id`,
`entity_name`, `title`, or `asset_type` — the runtime **intentionally fails closed** and
suppresses **all** approved URL enrichment until authority is rebuilt against the new index.

Future operational procedure must therefore treat the following as **one controlled
maintenance sequence**:

```text
RE-INDEX
  + REBUILD APPROVED URL AUTHORITY
  + VERIFY BINDING MATCH
```

Do **not** silently rebuild authority during runtime. A rebuild is a controlled maintenance
action, not a runtime recovery step. Suppression of URL enrichment after a re-index is
correct fail-closed behavior, not a defect to be worked around.

## 11. Known Non-Blocking Follow-Up (P3)

These are recorded as non-blocking follow-up. None was fixed in this task, and none is
closed by this acceptance:

1. Unescaped newline edge case in the binding canonical surface.
2. Duplicate `manifest.json` read per load.
3. Historical AppleDouble / data drift in the full test environment.
4. Global fail-closed availability tradeoff — deliberate by design.
5. Slack mrkdwn display shows a cosmetic issue where some `*label:*` markers appear visibly
   in production output. This is **not** part of the URL enablement failure criteria and
   must be handled as a separate sprint.

## 12. Final State Matrix

```text
MAIN_INTEGRATION       = PASS
PRODUCTION_ENABLEMENT  = PASS

MAIN_SHA               = ae7fd6c05fe832c6da2bd77f5ea46dd20bda753f

FEATURE_STATE          = ON

BINDING                = MATCH

BOUND_ASSETS           = 205
APPROVED_URL_VALUES    = 412
DISTINCT_MERCHANTS     = 107

PRODUCTION_SMOKE_SHANFENG = PASS
PRODUCTION_SMOKE_YH       = PASS
PRODUCTION_NEGATIVE_SMOKE = PASS

ARTICLE_VIDEO_SEPARATION  = PASS

FABRICATED_URL            = NONE

APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE = NOT OBSERVED IN POST-ACTIVATION CHECK

DG-01                  = CLOSED
F-01                   = PRESERVED
STALE_AUTHORITY_P2     = CLOSED
CACHE_P1               = CLOSED

ROLLBACK_READY         = YES
```
