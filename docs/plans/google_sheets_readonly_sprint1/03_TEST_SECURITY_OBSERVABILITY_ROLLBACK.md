# Sprint 1 Test, Security, Observability, and Rollback Strategy

## Status and governing rule

This is a planning specification. No test result, security approval, credential review, or production smoke is claimed by this document. All ordinary implementation verification is synthetic, mocked, local, and zero-production-network. A live controlled smoke is a separate human-authorized operation, not a pytest tier that turns on when tests are green.

## Testing principles

1. Test the authority boundaries, not only the happy-path transformation.
2. Prove complete configured-range coverage before exercising F1 or downstream stages through the application boundary.
3. Keep fixtures artificial; do not copy production cells, credentials, private bodies, or third-party article text into tests.
4. Preserve the frozen Sprint 0 contracts and their regression evidence.
5. Fail closed on missing policy, ambiguous response, partial data, unknown error, or unreviewed configuration.
6. Scan bytes, structured output, logs, exceptions, and repr surfaces for secret/sensitive sentinels.
7. Do not use `skip`, `xfail`, a hardcoded fixture result, or a hand-built downstream object to bypass a blocking upstream failure.
8. Do not call live Google, HTTP, Slack, external LLM, or another external API from the ordinary test suite.
9. Do not treat historical test counts as current verification. Each implementation package records the commands it actually ran.

## Testing tiers

### Tier 1 — Unit

Purpose: prove small pure behaviors in mapper, coverage, target/config validation, F1 envelope, source-health facts, canonical normalization, brand/ID review, preview, observability, and application-stage control.

Requirements:

- synthetic inputs only;
- no network or production filesystem;
- table-driven success and failure cases;
- stable error codes without reflected payloads;
- deterministic ordering/serialization where applicable.

Representative coverage:

- Google value/link/validation/merge branches;
- absolute coordinate calculation across non-A1 and multi-range grid blocks using `GridData.startRow`/`startColumn`;
- canonical sparse mapping for omitted rows/cells, empty `{}`, present empty string, false, zero, formulas, errors, links, runs, and validation;
- range coverage reconciliation;
- pinned target rejection;
- F1/config/coverage binding;
- pure F1/F2 second-result comparison under the exact same frozen selection, with no helper-owned read or downstream side effect;
- critical sheet/header/bounds/ID checks;
- oral-only minimization and restricted/pending separation;
- unique/ambiguous/conflicting brand evidence;
- preview schema and minimal telemetry allowlist.

### Tier 2 — Contract

Purpose: prove compatibility across versioned boundaries and with frozen Sprint 0 contracts.

Required contracts:

- runtime config → exact `SheetsReadRequest` selection;
- raw response mapper → `SpreadsheetSnapshot`;
- request/response → configured-range coverage proof;
- mapper + proof → typed `ConfiguredReadResult(snapshot, coverage_proof, config binding)`, never a side channel;
- coverage proof + snapshot → F1/source-health envelope;
- two independently coverage-proven configured results under the same selection → pure Sprint 1 F1/F2 test comparison result, distinct from Sprint 5 release-time F2;
- normalized metric path → early minimization → eligible metric or safe exclusion;
- brand/ID facts → review candidate, never approval;
- minimized facts → redacted preview;
- application service → redacted result + `STOP`;
- no layer accepts an unauthorized credential, target, writer, persistence, or downstream callback.

Compatibility checks must retain existing Sprint 0 test coverage for:

- `sheets_contracts`;
- canonical source serialization/F1;
- merge-aware cell normalization;
- canonical models and identities;
- oral-only early minimization;
- URL safety/link resolution;
- redacted sync preview;
- Sprint 0 synthetic integration.

### Tier 3 — Synthetic integration

Purpose: compose S1-WP0, WP2, WP3, WP4, and WP5 using a fully synthetic configured response and in-memory reader. WP1 is intentionally replaced only at this synthetic tier; this does not satisfy mocked-adapter end-to-end coverage.

Required scenarios:

- complete multi-sheet configured-range happy path ending at `STOP`;
- the full `NON_LIVE_FULL_SLICE` ending at redacted evidence and `STOP`;
- required hidden sheet;
- formulas with effective/formatted values;
- omitted-zero, interior-empty, trailing-empty, and reordered multi-grid encodings mapping to the same snapshot/F1 where semantically equivalent;
- pure F1/F2 equal/different and selection-mismatch scenarios using synthetic configured results;
- allowed merges and non-merge blanks;
- public, oral-only, pending, restricted, handle-mapping, and multi-interview examples using abstract sentinels;
- ID missing/duplicate/conflict and brand ambiguity;
- first-live mode producing `HUMAN_REVIEW_REQUIRED`, never automatic PASS;
- zero writes to every production authority surface.

### Tier 4 — Mocked network adapter

Purpose: verify the production adapter’s call shape and failure handling without external network, then prove the full mocked runtime data flow `WP1 transport → WP0 mapper/coverage → WP2 → WP3 → WP4 → WP5 STOP`.

The fake client must record and assert:

- exact `spreadsheets.get` method;
- exact pinned target;
- exact configured ranges and fields mask;
- `includeGridData=true`;
- no write or Apps Script call;
- no linked URL or other host call;
- bounded calls consistent with the later-frozen retry/batching policy.

The mocked response set must also cover non-zero grid offsets, multiple grid blocks, reordered response blocks, omitted trailing rows/cells, explicit empty objects, and present false/zero/empty-string values. Equivalent sparse encodings must produce equivalent canonical snapshots/F1; overlapping or ambiguous blocks must fail closed.

At least one successful mocked-adapter scenario must exercise `NON_LIVE_FULL_SLICE` end to end and reconcile the adapter selection, coverage proof, F1/source health, governance/minimization, brand/ID candidates, redacted evidence, and final `STOP`. Returning a hand-built `ConfiguredReadResult` at this tier would bypass WP1/WP0 and does not pass.

Failure cases include permission/auth denial, timeout, quota/429, 5xx, malformed/partial response, missing request part, target mismatch, and sanitization failure. No case may return a partial success.

### Tier 5 — Safe local regression

Purpose: demonstrate that the additive Sprint 1 path does not regress frozen Sprint 0 or relevant legacy behavior.

Scope:

- `tests/sprint1/conftest.py` installs the shared offline network/persistence guards for the new subtree; the existing `tests/sprint0/conftest.py` does not apply to sibling Sprint 1 tests;
- a Sprint 1 harness self-test deliberately attempts forbidden network and production-persistence operations and proves the guard blocks them;
- all Sprint 1 unit/contract/synthetic/mocked tests;
- relevant Sprint 0 safe tests listed above;
- existing safe legacy tests for local Excel path and shared public contracts, when they can run without prohibited data/runtime directories;
- lint/type checks only if configured in the repository;
- `git diff --check` and exact diff review.

If a historical test needs `.env`, `reports/`, `data/`, `obsidian_vault/`, `.mka/`, a production credential, or private runtime data, it is not run in this gate. The omission must be listed under “Not verified”; it must not be silently replaced with a passing claim.

### Tier 6 — Production smoke candidate

This is an eligibility package, not a network execution. It must contain:

- reviewed commit SHA and exact file set;
- approved unit/contract/synthetic/mocked/regression evidence;
- code review disposition;
- security review disposition;
- credential-boundary and scope evidence without secret disclosure;
- target/config hash and reviewed exact configured ranges/fields;
- network/egress review;
- completeness/snapshot-integrity review;
- preview schema and, if applicable, destination/ACL/retention/cleanup review;
- rollback/revocation procedure;
- proposed single-run operator procedure and stop conditions;
- a reviewed invocation mechanism that cannot be reached from ordinary CLI, scheduler, or pytest paths;
- commit/config/credential/approval binding, one-run replay prevention, and an exact used/closed disposition;
- an approved minimal non-content control-plane store with owner, ACL, retention, audit fields, atomic pre-read claim, concurrent/replay rejection, and crash/lease disposition;
- the procedure that disables the invocation binding and revokes the one-run execution authority after either success or failure.

Producing this package does not set `READY_FOR_PRODUCTION_SMOKE = YES`; the human authorization gate still controls that value.

### Tier 7 — Live controlled smoke

This is one explicitly authorized production operation. It is not ordinary pytest, not a developer convenience check, and not automatically repeatable.

Preconditions:

- Tier 6 package approved;
- exact commit/config/credential binding reviewed;
- all security and repository gates passed;
- a human explicitly authorizes one controlled read-only smoke;
- an operator and abort/rollback owner are identified;
- the reviewed one-run invocation binding is unused, non-replayable, tied to the authorized commit/config/credential, and backed by an approved atomic claim mechanism;
- no unresolved blocker affects target, credential, completeness, preview, or production-data handling.

Execution:

```text
Authenticate with approved read-only identity
  → read exact pinned target/configured ranges
  → map and prove configured-range coverage
  → compute F1 and safe source-health counts
  → STOP
  → human baseline review
  → explicit human baseline decision
```

Before authentication/read, the operator mechanism must atomically transition the exact binding from `unused` to `claimed/in_progress`. A second/concurrent claim fails closed. The reviewed lease or non-replayable-token policy determines the terminal audit disposition after process crash; a crash never silently restores the authorization to unused.

Because this is the first live read, the smoke stops at the source-health checkpoint. It does not run canonical normalization, brand/ID review, or the full redacted preview against production data before the human baseline decision. The complete Option A application service is verified through approved synthetic and mocked integration evidence; using the narrower first-live checkpoint does not redefine that checkpoint/Option F as the complete business slice. Any later production execution of the remaining Option A stages requires new, explicit human authority after the baseline decision and is not implied by Sprint 1 tests or this one smoke authorization.

Rules:

- no Google write, linked fetch, Slack, external LLM, scheduler, or other egress;
- no raw snapshot/debug persistence;
- no automatic retry beyond an explicitly frozen bounded policy;
- no automatic second smoke after failure; return to human review;
- after success or failure, mark the one-run binding used/closed and immediately disable the invocation authority according to the reviewed procedure;
- no threshold derivation followed by self-PASS;
- no production-data continuation into normalization/brand-review/full-preview stages before the explicit baseline decision;
- no downstream normalization output activation, release, Vault, index, archive, or `last_success` change;
- the first-live counts remain pending until explicit human baseline approval.

## Cross-package test matrix

| Risk | Required proof | Primary package | Blocking outcome |
| --- | --- | --- | --- |
| Wrong/arbitrary target | Exact allowlist and caller override rejection | WP1/WP5 | Stop before auth/read |
| Partial configured range | Request-to-response coverage reconciliation | WP0 | No F1/downstream |
| Response shape loss | Lossless DTO mapping branches | WP0 | Mapping block |
| Sparse/offset ambiguity | Offset-aware canonicalization and equivalence/overlap tests | WP0 | Mapping/coverage block |
| Coverage side channel | Typed configured-read result required end-to-end | WP0/WP1/WP2 | Construction block |
| F1 on unproved data | Coverage proof required by envelope | WP2/WP5 | Construction block |
| F1/F2 test helper expands live authority | Pure/mock second-result helper plus target/config/coverage mismatch rejection | WP2/WP5 | No live F2 path |
| Baseline evidence is unverifiable/unsafe | Versioned narrow schema, config/coverage/F1 binding, canonical hash, repr/sentinel tests | WP2/WP5 | Baseline/Live Smoke Gate fail |
| First-live self-approval | Human-review-only baseline state | WP2/WP5 | No PASS |
| Credential leakage | Boundary types + sentinel scans | WP1/WP5 | Security gate fail |
| Forbidden egress/write | Mock-call allowlist + missing write surface | WP1/WP5 | Security gate fail |
| Oral-only persistence | Early minimization + artifact byte scans | WP3/WP4/WP5 | Security gate fail |
| Restricted/pending promotion | Typed separation and negative construction | WP3 | Normalization block |
| Brand auto-approval | Candidate-only types + ambiguity tests | WP3 | Review required |
| Preview authority escalation | Structural labels/no apply-release fields | WP4 | Preview gate fail |
| Default/unsafe persistence | Explicit policy object and path rejection | WP4 | In-memory only or fail |
| Legacy regression | Relevant Sprint 0 and local Excel safe tests | WP5 | Integration checkpoint fail |
| Offline guard absent | Sprint 1 conftest plus deliberate guard self-test | Shared harness/WP5 | Test gate fail |
| Smoke replay | Commit/config/approval binding plus used/closed state | WP5/Tier 6/7 | Live Smoke Gate fail |
| Downstream production mutation | Persistence spies and forbidden-callback tests | WP5 | Integration checkpoint fail |

## Security gates

Every gate below is blocking. “Evidence” means sanitized, reviewable proof; it does not mean exposing the protected material.

### 1. Credential Gate

Pass requirements:

- dedicated read-only Service Account architecture;
- approved runtime credential mechanism;
- no long-lived JSON key unless separately approved by the Security Owner;
- no credential in Git, fixture, logs, exceptions, preview, or downstream signature;
- no writer or Apps Script execute authority;
- credential revocation/rotation owner and procedure identified.

Block on missing provider approval, secret-bearing config, broader scope, credential leakage, or unreviewed long-lived key.

### 2. Target Gate

Pass requirements:

- exact canonical Spreadsheet allowlisted;
- arbitrary/caller-supplied/discovered/active/fallback targets mechanically impossible;
- target/config binding reviewed;
- observability uses only hashed target identity.

Block on target mismatch, target ambiguity, dynamic discovery, or unreviewed configuration.

### 3. Egress Gate

Pass requirements:

- only Google authentication and Google Sheets read-only API for the exact target are available in a live-authorized context;
- ordinary tests are zero-network;
- no generic HTTP, linked URL, DNS capture, redirects, crawler, Slack, LLM, arbitrary API, or scheduler network;
- mock evidence proves no write call or extra endpoint.

Block on any broader network capability or inability to enforce the endpoint/method allowlist.

### 4. Completeness Gate

Pass requirements:

- every configured range/request part and returned grid block is fully represented in a deterministic proof;
- no missing page/range/block, truncation, duplicate ambiguity, or silent partial;
- hidden required sheets and merge/grid metadata are present;
- configured A1 extent and Google sparse omission semantics are reconciled without synthesizing payload-bearing blank cells;
- failure blocks F1 and downstream processing.

Block whenever completeness cannot be proven; best effort is not a pass.

### 5. Snapshot Integrity Gate

Pass requirements:

- target identity matches;
- response maps losslessly to validated DTOs;
- `GridData.startRow`/`startColumn`, coordinates, bounds, merges, sparse-empty canonicalization, and value unions are valid;
- F1 is computed only from the coverage-proven snapshot;
- run envelope separately binds config/coverage/schema versions;
- deterministic repeat evidence exists for synthetic/mocked inputs.

Semantically equivalent omitted/interior/trailing-empty encodings must yield the same snapshot/F1. Present empty string, false, zero, formula, error, link, run, and validation values must remain distinguishable. Block overlap or identity ambiguity is a hard failure.

Block on malformed or inconsistent snapshot, unbound selection, or fingerprint ambiguity.

### 6. Production Data Gate

Pass requirements:

- raw production response/snapshot remains transient in memory;
- no raw debug dump/cache/fixture/report;
- no private raw body is copied into test evidence;
- source counts and hashes are minimized to the approved evidence schema;
- first-live baseline evidence is versioned/redacted, binds safe config/coverage references or hashes plus F1/counts, and excludes raw/canonical/brand/full-preview payload;
- process-memory and incident response expectations are reviewed.

Block on any unapproved durable raw production data or unclear data lifetime.

### 7. Oral-only Minimization Gate

Pass requirements:

- governance is mandatory in the application flow;
- oral-only body, notes, evidence, URL, and raw display value are irreversibly removed before any preview/persistence-ready object;
- sentinel scans cover repr, exceptions, logs, preview, telemetry, and serialized bytes;
- restricted/pending/handle-mapping boundaries also pass typed separation tests.

Block on a single prohibited sentinel occurrence or bypass path.

### 8. Logs / Exceptions Gate

Pass requirements:

- stable allowlisted stage/outcome/error codes;
- sanitization of nested causes and client exceptions;
- no credential, token, auth header, raw cell, body, full URL, secret query, or unredacted stack dump;
- operational telemetry follows the minimum allowlist below.

Block on reflected payload, free-text raw exception, or unknown logging surface.

### 9. Preview Gate

Pass requirements:

- versioned, deterministic redacted schema;
- counts reconcile with coverage, entities, exclusions, reviews, and issues;
- explicit `Evidence only / Non-authoritative / Non-apply / Non-release / Non-activation` semantics;
- no raw or sensitive body;
- artifact hash can identify reviewed evidence without promoting authority.

The gate covers both evidence shapes: the full WP4 preview for non-live Option A and the narrower WP2/WP5 first-live baseline evidence. Each has a distinct versioned schema and deterministic hash boundary; the baseline schema never implies WP3/WP4 execution.

Block if raw data is required, schema overload is misleading, or the artifact could drive apply/release/activation.

### 10. Persistence Gate

Pass requirements for durable production preview:

- explicit approved destination and allowlist binding;
- ACL, allowed fields, retention, cleanup, and rollback frozen;
- no forbidden default path;
- write occurs only after redaction and only to the approved destination.

If this gate is not complete, the only allowed mode is in-memory evidence. Attempted durable output must fail closed.

The minimal atomic smoke-invocation claim/used/closed record is security control-plane state, not knowledge/preview persistence. It is allowed only after its store, owner, ACL, safe fields, retention, crash/lease semantics, and audit policy pass the Controlled Smoke Invocation Gate; it may contain no Google cell/body/brand content, F1, credential, token, or secret. This exception does not authorize any business-data store.

### 11. Rollback Gate

Pass requirements:

- code disable/revert procedure reviewed without history rewrite;
- adapter/config removal and credential revocation procedure reviewed;
- approved preview artifact cleanup is exact and bounded;
- legacy local Excel flow verified unaffected;
- no release/business-data rollback is claimed because no knowledge authority store is mutated; the reviewed invocation control record has its own exact terminal/cleanup procedure.

Block if rollback targets are broad/ambiguous or depend on deleting unrelated/user data.

### 12. Audit Evidence Gate

Pass requirements:

- commit/config/schema/policy version identifiers;
- test commands and actual results;
- code/security/repository review dispositions;
- exact configured-range coverage evidence;
- F1, safe counts, stage/outcome, artifact hash, and human decisions;
- no protected payload or secret in the audit packet;
- clear distinction among planning approval, implementation readiness, smoke readiness, baseline approval, and Sprint exit.

Block on incomplete provenance, contradictory status, or unverifiable/unsafe evidence.

### 13. Live Smoke Gate

Pass requirements:

- every prior gate passed;
- code/security/credential/network/preview/repository reviews complete;
- exact one-run procedure reviewed;
- replay prevention, commit/config/approval binding, and post-run disable/revoke disposition reviewed;
- the pre-read atomic claim, concurrent/replay rejection, approved control-store boundary, and crash/lease semantics are proven;
- explicit human authorization recorded for one controlled read-only smoke.

Tests green, planning approved, or implementation ready are never substitutes. Without explicit authorization, the gate remains closed.

## Minimum-necessary observability

### Allowed fields

Only the following may be emitted, subject to the reviewed schema and sensitivity classification:

- run/batch correlation ID;
- current stage;
- hashed target identity;
- application, mapper, schema, config, policy, and preview version identifiers;
- configured/observed range counts;
- safe sheet, entity, exclusion, and issue counts;
- F1/source fingerprint;
- stage and total latency;
- retry count under the frozen bounded policy;
- safe structured error code/category;
- redacted artifact hash/reference under the approved output policy;
- outcome such as blocked, review-required, redacted-evidence-ready, or failed.

Observability must not imply `healthy`, `approved`, or `pass` for the first-live baseline before human review.

### Prohibited fields

Never log or emit:

- credential, token, key, auth header, cookie, client secret, or secret path;
- raw Google response or raw cell contents;
- oral-only claim/body/notes/evidence/URL;
- restricted or pending claim/body;
- private raw body or third-party article body;
- unsafe or full URL, including signed/secret query strings;
- arbitrary target identity in cleartext telemetry;
- unredacted exception dump, raw client request/response, or full stack containing payload;
- preview destination secret, ACL principal list, or other security configuration beyond approved non-secret identifiers.

### Correlation and audit behavior

- Correlation IDs must not encode target, user, credential, source body, or business meaning.
- F1 identifies source state but is not proof of configured-range selection by itself; evidence also binds config and coverage identity.
- Artifact hash identifies the exact redacted evidence bytes reviewed; it is not an authority upgrade.
- The deterministic artifact hash excludes correlation ID, latency, retry count, timestamps, and all other per-run telemetry.
- Retry telemetry reports count and safe category, never request/response bodies.
- Blocked stages may emit only the minimum safe evidence needed to locate the stage and policy reason.

## Failure handling

- Unknown failure classification fails closed.
- Sanitization failure replaces the original error with a generic stable code and suppresses the unsafe detail.
- Partial reads never return a success-shaped snapshot.
- A failed run does not update a baseline, threshold, previous-success marker, archive list, release state, or `last_success`.
- A live-smoke failure ends the authorized run; no automatic follow-up smoke occurs.
- Any suspected credential or raw-data leak triggers the credential/config and production-data incident procedures before further runs.

## Rollback strategy

### Code rollback

- Keep each WP isolated and additive behind explicit construction/wiring.
- Disable or revert the new WP commits through normal forward/revert workflow; do not amend/rebase frozen or other-agent history.
- Remove adapter/application wiring without changing legacy Excel, Markdown, Vault, index, retrieval, or Slack paths.
- Re-run the relevant safe regression set after rollback.

### Data rollback

- Official data rollback is `N/A`: Sprint 1 must not write Google, Vault, Official DB, FTS, vector, CapturedContent, chunks, release journal, pointer, archive, or `last_success`.
- In-memory source and staging die with the process.
- The minimal non-content invocation control record follows its reviewed terminal/retention/cleanup policy and is not erased as a substitute for audit evidence.
- Any accidental raw durable persistence is a security incident, not a normal rollback success; contain the exact artifact under an approved incident procedure and do not silently delete evidence.

### Credential / configuration rollback

- Disable the adapter configuration and revoke the exact read identity’s access to the Spreadsheet.
- Disable/delete the approved runtime secret binding through the owning identity/secret system.
- Rotate/revoke if exposure is suspected; do not copy the credential into a debug path for diagnosis.
- Restore the last reviewed config version; never fall back to an arbitrary or active Spreadsheet.
- Confirm the legacy local Excel flow does not depend on this credential/config.

### Preview artifact rollback

- In-memory preview requires no rollback.
- For a durable approved preview, target the exact artifact ID/path only and follow the frozen retention/cleanup policy.
- Record only the allowed cleanup outcome/evidence; do not broaden deletion to a directory or unrelated artifacts.
- If policy requires retaining an audit hash after cleanup, retain only the approved safe fields.

### Release rollback

`N/A in Sprint 1.` Sprint 1 neither builds nor activates a Release, so it cannot invoke a release rollback, pointer recovery, or `last_success` restoration procedure.

## Exit evidence expectations

Sprint 1 Exit Review must receive, without protected payloads:

- implementation commit(s) and exact file sets;
- actual verification commands/results and omissions;
- all security-gate dispositions;
- exact target/config review evidence using hashed/non-secret identifiers where appropriate;
- completeness proof summary;
- F1 and safe source-health counts;
- preview artifact hash/reference if durable output was approved;
- explicit first-live human baseline decision;
- Product/Governance Owner disposition of post-baseline production Option A stages: separately authorized or explicitly deferred;
- brand-candidate `approve`/`split`/`merge`/`exclude` disposition for any production candidates created under separate authority, or explicit deferral of that production approval point when none were created;
- source-health threshold draft freeze or explicit deferral after first-live evidence review;
- proof of no write/forbidden egress/downstream mutation;
- rollback/revocation result or readiness;
- explicit statement that no Release, activation, Vault/index, CapturedContent, chunk corpus, scheduler, Slack, or external LLM was created or changed.
