# Slack Approved Asset Link — Final Acceptance Freeze Authorization

Date: 2026-08-16

This record is administrative only. It freezes an already-completed code identity and
records the evidence behind its acceptance. It changes no application code, enables no
feature, sends no Slack message, and authorizes no merge or rollout.

## 1. Scope

Feature: Slack approved asset-level links for structured merchant search results.

| Item | Value |
| --- | --- |
| Feature flag | `enable_approved_asset_urls` |
| Default state | OFF |
| Frozen code branch | `codex/test/slack-enable-asset-links` |
| Frozen code SHA | `ebaea0c85c291e6cc5c2309e91fec5f3ca9cdd12` |

The flag defaults to `False` in `SlackConfig`, and the config loader treats an absent key
as `False`. Enabling the feature is an explicit, separate decision that this freeze does
not grant.

## 2. Implementation History

| Stage | SHA |
| --- | --- |
| Base | `11c99c86ccbbab06f2bf583f8918560d0ce4e985` |
| Initial asset-link implementation | `5374786ed4fcb0bb0b23aa225e4fbad44af593dd` |
| Security remediation | `5a4325f5a40bade585584e2da888a0fff64af68c` |
| Final raw-control-character remediation | `ebaea0c85c291e6cc5c2309e91fec5f3ca9cdd12` |

Chain:

```text
11c99c8 → 5374786 → 5a4325f → ebaea0c
```

The frozen code identity is exactly `ebaea0c85c291e6cc5c2309e91fec5f3ca9cdd12`. It must not
be modified, amended, rebased, squashed, or cherry-picked.

## 3. Closed Blockers

### F-01 / P1 — Approval authority binding

Independent review originally found that approved asset URLs were not sufficiently bound to
tracked approval authority.

Final status: **CLOSED**

Controls include tracked historical-input manifest integrity, required artifact SHA-256
binding, parsing of the same verified bytes, and layered row-level governance.

### F-02 / P1 — Slack mrkdwn entity/link-boundary ambiguity

Final status: **CLOSED**

Controls include raw and canonical URL validation together with safe Slack link rendering.

### F-03 / P2 — Audit continuity on missing/malformed authority artifacts

Enabling the feature with missing or malformed authority artifacts could abort the Slack
request and skip normal audit continuity.

Final status: **CLOSED**

Current behavior fails closed for URL enrichment while preserving the governed Slack
response and the audit path.

### Raw control characters / P2

TAB, LF, CR and related raw control characters could be removed during URL canonicalization
before presentation safety validation ran.

Final status: **CLOSED**

Final code validates raw link candidates before normalization and retains defense-in-depth
validation at the Slack presentation boundary.

## 4. Independent Acceptance

Final targeted independent P2 re-review verdict at frozen code
`ebaea0c85c291e6cc5c2309e91fec5f3ca9cdd12`:

```text
APPROVE_FOR_REAL_CONTROLLED_SLACK_TEST
```

| Result | Value |
| --- | --- |
| New P0 | NONE |
| New P1 | NONE |
| New P2 | NONE |
| READY_FOR_REAL_CONTROLLED_SLACK_TEST | YES |

This verdict covers P0/P1/P2 only. Deferred P3 findings were **not** fixed and are **not**
closed by this verdict — see section 10.

## 5. Controlled Slack Test Evidence

A real controlled Slack test was performed on 2026-08-16.

| Item | Value |
| --- | --- |
| Target branch | `codex/test/slack-enable-asset-links` |
| Target SHA | `ebaea0c85c291e6cc5c2309e91fec5f3ca9cdd12` |
| Target worktree | `/private/tmp/mka-slack-enable-asset-links` |
| Controlled query | 我要尋找三風製麵的案例 |

Expected and observed destinations:

| Asset | Destination |
| --- | --- |
| Article | `https://blog.shopline.tw/merchant-showcase-shanfeng/` |
| Video | `https://www.youtube.com/watch?v=WIMy_AFA0pE` |

Observed Slack result:

- Article displayed a clickable 開啟連結.
- Video displayed a clickable 開啟連結.
- Article and video resolved to their correct distinct destinations.
- No parent merchant URL contamination observed.
- No 資料未提供 for these approved URLs.

## 6. Runtime Attribution Evidence

| Log | Lines |
| --- | ---: |
| Primary audit log (before / controlled-test evidence) | 120 |
| Target audit log after test | 121 |

The controlled message was therefore attributed to the target runtime, not the primary
runtime.

Approved overlay failure audit check:

```text
APPROVED_ASSET_URL_OVERLAY_UNAVAILABLE count = 0
```

No approved-URL authority failure or fail-closed event occurred during the successful
controlled message.

## 7. Runtime Rollback

After the controlled test:

| Runtime | State |
| --- | --- |
| Target Slack runtime | STOPPED |
| Primary Slack runtime | RESTORED |

Restored primary runtime cwd:

```text
/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent
```

Exactly one primary Slack bot process was verified after rollback. The controlled-test
feature-enabled runtime was not left active.

## 8. Feature / Release State

```text
CONTROLLED_SLACK_TEST            = PASS
FEATURE_DEFAULT                  = OFF
PERMANENT_FEATURE_ENABLEMENT     = NOT AUTHORIZED
MERGE_TO_MAIN                    = NOT AUTHORIZED
PRODUCTION_BROAD_ROLLOUT         = NOT AUTHORIZED
```

No production rollout decision is implied by this freeze.

## 9. Data / Governance State

The accepted path preserves:

- Asset-level article/video URL identity
- No sibling URL inheritance
- No merchant-parent canonical URL fallback
- `StructuredAsset` / `Citation` URL consistency
- Restricted governance
- Pending governance
- `can_quote_externally` controls
- Exposure-channel controls
- No URL-driven governance bypass
- No re-index requirement
- No Excel mutation

## 10. Deferred Non-Blocking Items

The following P3 and later-rollout items remain **deferred**. They are **not** implicitly
closed, fixed, or accepted by this freeze:

- Runtime dependency on `tests/fixtures` for packaged / non-editable deployment
- Future callers must use the guarded link-target path rather than bare `canonicalize_url`
  for link production
- URL canonicalization byte-rewrite nuances
- Generic 開啟連結 label / hostname visibility consideration
- Residual regression-test hardening items
- macOS AppleDouble historical-input drift / environment issue
- Offline CLI unpinned preview loader remains outside the Slack runtime threat model

These do not block the frozen controlled-test acceptance.

## 11. Environmental Test Errors

The full repository suite was **not** completely green. The failures were caused by
pre-existing environment / immutable-artifact drift involving a macOS AppleDouble sidecar
under gitignored `data/governance/backups`.

This was independently classified as:

```text
ENVIRONMENT / IMMUTABLE-ARTIFACT_DRIFT
TRACKED_REGRESSION: NONE
```

The repository-wide suite is not represented as fully green.

## 12. Governance Decision

```text
SLACK_ASSET_LINK_CODE_FROZEN            = YES
FROZEN_CODE_SHA                         = ebaea0c85c291e6cc5c2309e91fec5f3ca9cdd12
CONTROLLED_SLACK_TEST                   = PASS
READY_FOR_BRANCH_PUSH                   = YES
READY_TO_MERGE_MAIN                     = NO
PERMANENT_FEATURE_ENABLEMENT_AUTHORIZED = NO
PRODUCTION_BROAD_ROLLOUT_AUTHORIZED     = NO
```
