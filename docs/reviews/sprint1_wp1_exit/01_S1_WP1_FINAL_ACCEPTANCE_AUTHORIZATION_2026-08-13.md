# Sprint 1 WP1 Final Acceptance / Human Freeze Authorization — 2026-08-13

## Final Acceptance Checkpoint

- Repository: `marketing-knowledge-agent`
- Branch reviewed: `codex/impl/sprint1-wp1-google-sheets-transport`
- Frozen WP1 implementation code SHA: `4c8b85f9d928d4abdcf8b0bda3daa82216de81eb`
- Original implementation commit: `c1c21e39eb4588342894e086bd5c1d486df2a0b5`
- Frozen selection authorization base: `c60a99ef5fcbb2c4d65ce84bd0444fd718af6447`
- Independent Final Acceptance Re-review: `APPROVE`
- New blocking findings: `NONE`

The human user formally accepted the independent implementation/security review and the independent remediation re-review. The accepted remediation closes all three findings required before WP1 freeze and introduces no new P0, P1, or P2 blocker.

This record binds the WP1 freeze to the implementation code SHA above. The later documentation-only authorization commit is not the frozen WP1 code SHA.

## Original Finding Disposition

All three findings required by the previous independent review are closed:

1. P1 — Environment-controlled proxy authority: `CLOSED`
2. P1 — Logical deadline excluded JSON decode and mapping: `CLOSED`
3. P2 — Oversized valid `Retry-After` fallback behavior: `CLOSED`

## Product / Governance Owner Approval

- Role: Product / Governance Owner
- Reviewer: Admin
- Date: 2026-08-13
- Approval: `APPROVED`

The human user explicitly accepted the final re-review verdict and authorized S1-WP1 closure and freeze. Codex did not independently verify organizational authority.

## Frozen Identities

```text
selection_configuration_version = s1-wp1-prod-read-selection-v1
frozen_selection_configuration_identity = sha256:e4dbf5e50b393729eabd6187590a9419a9a0f8741f97a36bfc2d48994ceac48e
frozen_transport_policy_identity = sha256:a1cc1c8c2d549d4a6b41981fc5285158ad8bd495bea1a8d3e2a483523d859e5b
```

The final independent reviewer recomputed the transport policy identity and confirmed that it matched the frozen implementation exactly. Neither frozen identity is recomputed or redefined by this authorization record.

## Frozen WP1 Transport Authority

WP1 is frozen as a mocked/offline read-only Google Sheets transport boundary:

```text
canonical_spreadsheet_id = 15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM
semantic_method = spreadsheets.get
http_method = GET
canonical_endpoint = https://sheets.googleapis.com/v4/spreadsheets/15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM
configured_request_count = 1
includeGridData = true
```

The single configured logical request contains exactly these five bounded ranges in this order:

1. `'商家/夥伴案例資料庫'!A6:L1018`
2. `'「不可公開」客戶名單'!A4:H994`
3. `'「可公開」對外數據'!A6:M999`
4. `'待確認數據'!A3:D999`
5. `'handle 比對'!A1:D998`

Whole-workbook reads, whole-column reads, open-ended ranges, runtime target/range discovery, fallback targets, automatic range expansion, and caller overrides of target, range, fields, or endpoint are forbidden.

## Frozen Network and Security Policy

The accepted WP1 transport policy is frozen with:

- HTTPS and the exact canonical Sheets endpoint only;
- `GET` only, with an empty request body;
- the exact serialized REST fields selector;
- `includeGridData=true`;
- exact OAuth scope `https://www.googleapis.com/auth/spreadsheets.readonly`;
- redirects disabled and TLS verification enabled;
- `trust_env=false`, disabling environment-derived proxy and netrc authority;
- no generic HTTP surface or linked-URL fetch;
- no Drive, Apps Script, write, or `batchUpdate` authority.

## Frozen Retry and Deadline Policy

```text
connect_timeout_seconds = 5
read_timeout_seconds_per_attempt = 30
overall_logical_deadline_seconds = 90
maximum_sheets_data_calls = 2
maximum_retry_count = 1
invalid_retry_after_fallback_seconds = 1.0
```

Retries are allowed only for connect timeout, read timeout, and HTTP `408`, `429`, `500`, `502`, `503`, or `504`. A valid `Retry-After` delay that meets or exceeds the remaining deadline causes a stable deadline failure with no sleep and no retry.

The overall logical deadline includes request execution, response handling, JSON decode, frozen WP0 mapping, coverage verification, and the final success-return boundary. Late success is forbidden.

## Frozen Credential and Success Boundaries

WP1 remains authorized only for mocked/offline implementation. Credential, provider, and session authority is confined to transport construction. Credentials, tokens, Authorization headers, and credential/session/provider representations must not enter the mapper, `ConfiguredReadResult`, logs, reprs, persisted artifacts, or exception chains.

Automatic HTTP adapter retry and automatic `AuthorizedSession` 401 refresh/resend are both frozen at zero.

The only accepted WP1 success boundary is `ConfiguredReadResult`. A bare `SpreadsheetSnapshot` and HTTP 200 alone are not production-facing success. Success requires safe JSON decoding, frozen WP0 mapping, configured coverage verification, deadline verification, and a `ConfiguredReadResult`.

## Fresh Independent Acceptance Evidence

The final independent re-review executed safe synthetic/offline verification against frozen code SHA `4c8b85f9d928d4abdcf8b0bda3daa82216de81eb`:

| Verification | Result | Warnings |
| --- | --- | --- |
| Focused WP1 | 65 passed, 0 failed, 0 skipped, 0 xfail | 3 |
| Full `tests/sprint1` | 96 passed, 0 failed, 0 skipped, 0 xfail | 3 |
| Sprint 0 Sheets contracts | 10 passed, 0 failed, 0 skipped, 0 xfail | 0 |
| Sprint 0 source fingerprint | 27 passed, 0 failed, 0 skipped, 0 xfail | 0 |
| Sprint 0 contract integration | 29 passed, 0 failed, 0 skipped, 0 xfail | 0 |
| Full safe `tests/sprint0` | 1405 passed, 0 failed, 0 skipped, 0 xfail | 8 |
| Safe legacy | 30 passed, 0 failed, 0 skipped, 0 xfail | 6 |
| `pip check` | PASS — No broken requirements found | 0 |
| `git diff --check` | PASS | 0 |

Independent adversarial verification covered hostile `HTTPS_PROXY`, `HTTP_PROXY`, and `ALL_PROXY` isolation; fail-closed `trust_env` construction; decode and mapper deadline crossings; exact-deadline rejection; bounded, 400-digit, 5000-digit, and 100,000-digit `Retry-After` values; invalid, mixed, and HTTP-date `Retry-After` values; hidden 401 resend prevention; retry budget; credential and secret containment; malformed responses; the frozen selection identity; and independent transport-policy identity recomputation.

## Nonblocking and Deferred Items

The Python 3.9 EOL warning, LibreSSL/urllib3 compatibility warning, and existing Pydantic deprecation warnings remain nonblocking and deferred. This authorization does not remediate them.

## Authorization State

```text
S1_WP1_TECHNICAL_READINESS = YES
HUMAN_WP1_ACCEPTANCE = APPROVED
WP1_FROZEN = YES
READY_TO_FREEZE_WP1 = YES
READY_FOR_S1_WP2_IMPLEMENTATION = NO
PRODUCTION_SMOKE_AUTHORIZED = NO
```

## Explicitly Unauthorized Follow-on Work

This authorization does not permit real ADC login, Service Account use or keys, impersonation, token refresh, Spreadsheet permission changes, production auth egress, Google API requests, production Sheet reads, production persistence, production smoke, production F1/count baselines, WP2 or WP3 implementation, preview, release, activation, Slack, Vault, index, or capture.

## Repository Disposition

This authorization record does not modify WP1 source or tests, frozen WP0, the selection-registry authorization, Sprint 1 planning, dependencies, `setup.py`, `pyproject.toml`, CLI, Slack, Vault, index, ingestion, preview, governance, credentials, or production artifacts.

The only intended repository change for this authorization action is this new record:

`docs/reviews/sprint1_wp1_exit/01_S1_WP1_FINAL_ACCEPTANCE_AUTHORIZATION_2026-08-13.md`

## Remaining Gates

S1-WP2 implementation remains unauthorized and requires a separate future review and explicit human authorization. Production smoke also remains unauthorized and requires a separate explicit human authorization.
