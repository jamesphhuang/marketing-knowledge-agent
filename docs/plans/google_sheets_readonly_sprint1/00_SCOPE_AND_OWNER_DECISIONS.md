# Sprint 1 Scope and Owner Decisions

## Document status

- Planning artifact only; this document does not authorize implementation, credential creation, Google API access, production data access, or a production smoke.
- Sprint 0 frozen base: `abc323c05e21e3886b648878b35915bf72553c22`.
- Planning branch: `codex/plan/google-sheets-readonly-sprint1`.
- Sprint 0 Frozen Audit and Frozen Planning remain unchanged.
- Current gate: `READY_FOR_SPRINT1_IMPLEMENTATION = NO — PENDING PLANNING EXIT REVIEW`.

## Authority and source hierarchy

This plan preserves three different kinds of authority instead of merging them:

1. The tracked repository at the frozen base supplies the Sprint 0 contracts, Frozen Audit, Frozen Planning, and Final Authorization. In particular:
   - `docs/reviews/google_sheets_obsidian_sync_audit/00_EXECUTIVE_SUMMARY.md` through `11_FINAL_CONSISTENCY_REVIEW.md` are the Frozen Audit;
   - `docs/plans/google_sheets_obsidian_sync_sprint0/00_SPRINT0_PLAN_SUMMARY.md` through `07_SPRINT0_READINESS_CHECKLIST.md` are the Frozen Sprint 0 plan;
   - the Sprint 0 implementation contracts are the tracked modules and tests introduced by WP0–WP16;
   - `docs/reviews/sprint0_exit/02_SPRINT0_FINAL_AUTHORIZATION_2026-08-12.md` is the final Sprint 0 authorization.
2. The owner-reviewed Sprint 1 planning brief supplied for this independent session is the authority for the new Sprint 1 Option A decision, Owner Decisions 1–5, Owner Locks 1–4, and the required S1-WP0–S1-WP5 structure. Those labels are new Sprint 1 planning inputs; they are not retroactively attributed to a tracked Sprint 0 file.
3. A detail not uniquely supported by either source is retained as `OPEN_POLICY` or `OPEN_IMPLEMENTATION_DECISION`. A recommendation is not promoted to a frozen fact.

The tracked tree at the frozen base contains no separately named “Sprint 1 Planning Review” file and no `Option F` label. That absence does not negate the new owner brief, but it requires this plan to keep the source distinction explicit.

## Sprint 0 frozen base

The final Sprint 0 checkpoint records:

- `SPRINT0_TECHNICAL_READINESS = YES`;
- `SPRINT0_HUMAN_AUTHORIZATION_COMPLETE = YES`;
- `SPRINT0_EXIT_READY = YES`;
- `READY_FOR_SPRINT1_PLANNING = YES`;
- `READY_FOR_SPRINT1_IMPLEMENTATION = NO`.

`READY_FOR_SPRINT1_PLANNING = YES` authorizes this independent planning gate only. It does not authorize S1-WP0, any other implementation work, or a production read.

Incident `S0-REVIEW-INCIDENT-001` remains `CLOSED — ACCEPTED WITH EXCEPTION`. Its historical checklist result remains `INCIDENT / EXCEPTION`, not `VERIFIED`. Nothing in Sprint 1 planning rewrites that disposition or the frozen Sprint 0 checkpoint.

## Sprint 1 objective

Plan a fail-closed, read-only path that can obtain a **complete configured-range snapshot** from one pinned production Google Spreadsheet, prove configured-range coverage and source integrity, normalize the result in memory, apply governance and early data minimization, produce brand/ID review candidates and redacted evidence, and then stop without creating or activating any production knowledge artifact.

## Business capability

Sprint 1 is intended to demonstrate that the canonical production metadata source can be observed safely enough for human review. A successful run establishes only that a controlled body of production source evidence was read and transformed into reviewable, redacted evidence under the approved boundaries. It does not establish that the evidence is approved content, release material, or active knowledge.

## Primary vertical slice

Owner-approved formal name:

**Option A — Read-only Google Production Evidence Dry-run**

```text
Configured Google Sheets
  → dedicated read-only adapter
  → complete configured-range snapshot
  → source fingerprint F1
  → source-health validation
  → in-memory canonical normalization
  → early governance / oral-only minimization
  → brand / ID review candidates
  → redacted preview evidence
  → STOP
```

“Complete configured-range snapshot” means every configured sheet and range is retrieved completely and included in a coverage proof. It does not mean an unrestricted read of the entire Spreadsheet.

The diagram defines the complete Option A implementation slice and its synthetic/mocked integration target. Owner Decision 5 separately constrains the **first live** read to stop after coverage plus counts/source health for human baseline review. That first-live checkpoint does not replace Option A as the business slice and does not authorize production data to continue through the later stages before the baseline decision.

## In scope

- A dedicated Google Sheets read-only transport that preserves the existing `SheetsReader` contract for compatibility while exposing a new typed configured-read result that binds the mapped snapshot and coverage proof without a side channel.
- A response mapper that preserves the required Google `CellData` shapes, sheet properties, hidden state, merges, coordinates, data validation, hyperlinks, and rich-text links.
- An explicit, pinned, allowlisted production Spreadsheet target.
- Exact configured ranges and fields once they pass the implementation decision gate; no implicit workbook-wide target.
- Fail-closed proof that every configured range is present, complete, non-truncated, and attributable to the requested target.
- Deterministic F1/source fingerprint computation only after coverage succeeds.
- A pure, mocked F1/F2 second-result comparison helper retained from the Frozen Audit Sprint 1 test plan; this is not the Sprint 5 release-time source-F2/commit gate and grants no automatic second live read.
- Source-health facts and counts suitable for first-live human baseline review.
- In-memory canonical normalization using the frozen Sprint 0 contracts and identities.
- Early oral-only minimization and governance separation for restricted, pending, handle-mapping, and denylist inputs.
- Brand/ID review candidates that do not allocate, merge, overwrite, backfill, or approve IDs.
- Deterministic redacted preview evidence and minimum-necessary observability.
- A read-only dry-run application service that composes the approved stages and enforces `STOP`.
- Synthetic, mocked-adapter, safe local regression, contract, negative, and security testing.
- A production smoke *candidate* package and, only after all later gates and explicit human authorization, one controlled read-only smoke.

## Out of scope

- An unrestricted entire-Spreadsheet or entire-workbook read.
- Arbitrary, caller-supplied, discovered, active, fallback, or secondary Spreadsheet targets.
- Google write scope, writer methods, mutation calls, or Apps Script execute authority.
- Permanent ID allocation, registry mutation, BRD backfill, or Google Sheet correction.
- Linked webpage fetch, DNS lookup for capture, redirect following, crawler, scraper, or any generic HTTP authority.
- Slack API, scheduler-triggered network, external LLM, or arbitrary external API.
- Raw production snapshot persistence, debug dumps, or production-data fixtures.
- Official content, Release Candidate, Official Release, activation, or any active pointer.
- Vault, CapturedContent, Markdown, Official SQLite, FTS, vector, chunk corpus, release journal, or `last_success` mutation.
- Automatic source-health PASS or automatic threshold derivation from the first live read.
- Automatic continuation of first-live production data beyond the baseline `STOP`.
- Default production preview output paths, including `reports/`, `data/`, `obsidian_vault/`, and `.mka/`.
- Changes to the legacy local Excel flow or migration of existing production paths.

## Explicitly deferred

The following are not Sprint 1 blockers and must not be promoted into Sprint 1 implementation:

- Apps Script writer;
- ID allocator or registry write;
- BRD backfill;
- immutable normalized publish candidate;
- canonical Obsidian renderer;
- Official SQLite, FTS, or vector build;
- production content capture;
- production chunk splitting;
- Release journal;
- global active pointer;
- activation or recovery;
- scheduler;
- Slack canonical cutover;
- Slack Ops;
- pagination, cursor, or TTL;
- capture stale/LKG/404 operational policies;
- JavaScript-rendered, non-HTML, video, or podcast support.

## Owner Decision 1 — Primary scope

**Status: `APPROVED`**

- Sprint 1 adopts Option A, **Read-only Google Production Evidence Dry-run**, as its primary vertical slice.
- Option F may exist only as an internal WP checkpoint or fallback. It is not the complete Sprint 1 business slice and cannot satisfy Sprint 1 exit on its own.

## Owner Decision 2 — Production target

**Status: `APPROVED`**

- Canonical production Spreadsheet: `15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM`.
- The reader must pin and allowlist this exact target.
- Arbitrary caller targets, fallback targets, active-spreadsheet identity fallback, and target discovery are forbidden.
- Exact ranges and fields should be derived from frozen sheet schema and Sprint 0 contracts. The tracked contracts establish the required data categories but do not uniquely determine the final range list or exact Google fields-mask string; those details remain `OPEN_IMPLEMENTATION_DECISION`.
- The open range/field decision cannot be resolved by broadening the request to the entire workbook.

## Owner Decision 3 — Credential policy

**Status: `POLICY_APPROVED`**

- Architecture: dedicated read-only Service Account.
- Spreadsheet permission: read-only only.
- OAuth/API scope: read-only only.
- No writer scope and no Apps Script execute authority.
- No credential in Git, logs, exceptions, previews, or other artifacts.
- No credential object, token, auth header, or secret value may cross into mapper, normalizer, governance, brand-review, preview, or observability layers.
- Preferred supply is a company-approved managed identity or secret-management mechanism. Workload Identity, Secret Manager, or an approved equivalent are examples, not frozen product requirements.
- A long-lived JSON credential is **not automatically authorized**. If it is the only available runtime mechanism, implementation must stop and obtain a separate Security Owner decision.
- The concrete runtime credential mechanism remains `OPEN_IMPLEMENTATION_DECISION`.

## Owner Decision 4 — Preview persistence

**Status: `APPROVED_WITH_OUTPUT_GATE`**

- Durable redacted preview evidence is permitted only behind an explicit output gate.
- Every preview is `Evidence only`, `Non-authoritative`, `Non-apply`, `Non-release`, and `Non-activation`.
- A preview can never become an Official source, Release Candidate, Official Release, Vault authority, or index authority.
- Before any production durable write, the destination, ACL, retention, cleanup policy, and allowed fields must all be frozen.
- Until those policies are frozen, there is no default production output path. An in-memory result may be returned to the controlled caller; durable output must fail closed without an explicitly approved target.

## Owner Decision 5 — First live baseline

**Status: `APPROVED`**

- The first controlled production read requires human review.
- Required sequence:

```text
Google read
  → coverage validation
  → counts / source health
  → STOP
  → human baseline review
  → explicit approval
```

- The first live run cannot derive a threshold and then use that self-derived threshold to mark itself `PASS`.
- First-live counts remain evidence pending human review.
- A future source-health threshold may be frozen only after actual evidence exists; it remains `OPEN_POLICY` in this plan.

## Owner Lock 1 — Snapshot semantics

**Status: `LOCKED`**

- “Complete snapshot” always means **complete configured-range snapshot**.
- Every configured range must be retrieved completely.
- Truncation, missing pages, missing ranges, silent partial responses, and unprovable coverage fail closed.
- The phrase must not be used to imply an unrestricted entire-Spreadsheet read.

## Owner Lock 2 — Network authority

**Status: `LOCKED`**

- Sprint 1 production network authority is limited to Google authentication plus the Google Sheets read-only API for the exact configured target.
- No linked webpage fetch, generic HTTP, DNS content lookup, redirect following, crawler, Slack API, external LLM, arbitrary API, or scheduler-triggered network is authorized.
- An Evidence URL is a reference candidate only: `Evidence URL ≠ capture authority`.

## Owner Lock 3 — Dry-run does not create a release

**Status: `LOCKED`**

A successful dry-run must not create or update:

- Release Candidate or Official Release;
- active pointer, release journal, or `last_success`;
- Official SQLite, FTS, or vector data;
- Vault or Markdown state;
- CapturedContent state;
- production chunks or any production corpus.

Success means only that production source evidence is available for human review.

## Owner Lock 4 — Live smoke authority

**Status: `LOCKED`**

A production smoke is outside ordinary implementation and test execution. It is eligible only after all of the following are complete:

- Sprint 1 code review;
- Sprint 1 security review;
- approved test evidence;
- credential-boundary review;
- network-boundary review;
- preview-policy review;
- repository review;
- explicit human authorization for one controlled read-only smoke.

Green tests never authorize Codex to run a production smoke.

## Open policies and implementation decisions

The following remain deliberately unresolved:

| Item | Classification | Current constraint |
| --- | --- | --- |
| Exact configured ranges and fields | `OPEN_IMPLEMENTATION_DECISION` | Must be explicit and schema-minimal; cannot become an entire-workbook read. |
| Credential runtime implementation detail | `OPEN_IMPLEMENTATION_DECISION` | Must meet the approved policy; long-lived JSON requires separate Security Owner approval. |
| Timeout | `OPEN_IMPLEMENTATION_DECISION` | No invented duration. |
| Bounded retry | `OPEN_IMPLEMENTATION_DECISION` | No invented attempt count or backoff. Frozen future scheduler Decision 7 does not automatically define this interactive dry-run transport policy. |
| HTTP 429 / 5xx handling | `OPEN_IMPLEMENTATION_DECISION` | Must remain bounded, sanitized, and fail closed; no final policy invented here. |
| Request deadline | `OPEN_IMPLEMENTATION_DECISION` | No invented duration. |
| Multi-request batching necessity | `OPEN_IMPLEMENTATION_DECISION` | If used, all request parts must participate in one coverage proof. |
| Post-baseline production execution of later Option A stages | `OPEN_POLICY` | First live must stop at counts/source health. Planning Exit Review must decide whether later production normalization/brand-review/preview is separately authorized, deferred beyond Sprint 1, or handled by another explicitly reviewed one-read continuation design. It is never implied by the first smoke. |
| Production brand-candidate human disposition | `OPEN_POLICY` | Frozen Audit retains approve/split/merge/exclude as a human approval point. If no production candidates are generated because the first-live gate stops earlier, Product/Governance Owner must explicitly defer this approval point rather than silently treating it as complete. |
| Source-health threshold draft disposition | `OPEN_POLICY` | Human review must explicitly freeze or defer the draft after first-live evidence; no automatic threshold or PASS. |
| First-live baseline evidence wire schema | `OPEN_IMPLEMENTATION_DECISION` | Must be versioned, redacted, bind target/config/coverage/F1 and safe counts, carry `HUMAN_REVIEW_REQUIRED`, and have a deterministic hash excluding telemetry. It cannot contain or authorize full preview/brand output. |
| Raw snapshot repr containment mechanism | `OPEN_IMPLEMENTATION_DECISION` | Raw cells may exist only in the transient in-memory DTO. WP0 must either harden payload-bearing repr surfaces or encapsulate them so they cannot reach logs/errors/artifacts; direct DTO repr is never safe evidence. |
| Controlled-smoke invocation and one-run replay prevention | `OPEN_IMPLEMENTATION_DECISION` | Must be frozen in the smoke candidate, bind commit/config/credential/approval, use an approved minimal non-content atomic claim/used/closed record that survives process restart, and disable or revoke the one-run execution authority after success or failure. If no reviewed replay-safe mechanism exists, live smoke remains unauthorized. |
| Production preview destination | `OPEN_POLICY` | No default path. |
| Preview ACL | `OPEN_POLICY` | Durable output prohibited until frozen. |
| Preview retention | `OPEN_POLICY` | Durable output prohibited until frozen. |
| Preview cleanup | `OPEN_POLICY` | Durable output prohibited until frozen. |
| First-live actual baseline counts | `OPEN_POLICY` | Must come from one separately authorized controlled read and human review. |
| Future source-health threshold | `OPEN_POLICY` | Cannot be derived and self-approved by the first live run. |

## Readiness definitions

### `READY_FOR_SPRINT1_IMPLEMENTATION`

This may become `YES` only when a Planning Exit Review confirms all of the following:

- these five planning documents are reviewed and frozen;
- Owner Decisions 1–5 and Owner Locks 1–4 are recorded without contradiction;
- no architecture blocker prevents S1-WP0 or S1-WP1 design;
- WP ordering and DAG are frozen;
- test strategy and security gates are frozen;
- credential policy boundary, network authority, production target, and snapshot semantics are frozen;
- open items that can safely remain deferred are labeled, and no unresolved blocker prevents the first implementation packages;
- Product/Governance, Security, and Repository reviewers provide the required planning exit disposition.

`READY_FOR_SPRINT1_IMPLEMENTATION = YES` would authorize only entry into packages whose own dependencies and freeze gates are satisfied. It must not be read as authority for S1-WP1, credentials, network, a production read, or any later package with an open entry decision. This plan therefore also tracks package-level readiness; S1-WP1 remains closed until its configuration, credential, transport-policy, dependency, and security prerequisites are complete.

Current value:

`READY_FOR_SPRINT1_IMPLEMENTATION = NO — PENDING PLANNING EXIT REVIEW`

Package-level current values:

- `READY_FOR_S1_WP0_IMPLEMENTATION = NO — PENDING PLANNING APPROVAL AND WP0 ENTRY FREEZES`;
- `READY_FOR_S1_WP1_IMPLEMENTATION = NO — PENDING CONFIGURATION/SELECTION, CREDENTIAL, TRANSPORT, AND SECURITY FREEZES`.

### `READY_FOR_PRODUCTION_SMOKE`

This may become `YES` only after implementation is complete and the code, security, test, credential, network, preview, persistence, rollback, and repository gates in this plan have passed, the exact production configuration is reviewed, and a human explicitly authorizes one controlled read-only smoke. Planning approval or implementation readiness alone is insufficient.

Current value:

`READY_FOR_PRODUCTION_SMOKE = NO`

### `SPRINT1_EXIT_READY`

This may become `YES` only after:

- the implementation exit criteria and approved non-live checks pass;
- one explicitly authorized controlled read-only smoke completes within the locked authority;
- configured-range completeness and F1 integrity are proven;
- first-live counts and source-health evidence stop for human baseline review;
- the human explicitly approves the baseline evidence;
- Product/Governance Owner resolves the open post-baseline execution policy and the Frozen Audit brand-candidate approval point, either by reviewing all production candidates created under separately granted authority or by explicitly deferring that production approval point;
- Product/Governance Owner explicitly freezes or defers the source-health threshold draft after reviewing actual evidence;
- review confirms no Google write, forbidden egress, credential leakage, raw production persistence, downstream production mutation, or release/activation occurred;
- final Product/Governance, Security, and Repository exit review is complete.

Sprint 1 exit does not create a Release or authorize the next production stage.

Current value:

`SPRINT1_EXIT_READY = NO`

## Current planning verdict

- `READY_FOR_SPRINT1_PLAN_EXIT_REVIEW = YES`.
- `PLANNING_APPROVED = NO — PENDING HUMAN PLANNING EXIT REVIEW`.
- `READY_FOR_SPRINT1_IMPLEMENTATION = NO`.
- `READY_FOR_PRODUCTION_SMOKE = NO`.
- `SPRINT1_EXIT_READY = NO`.
