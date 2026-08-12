# Sprint 1 Blocking Readiness Checklist

## How to use this checklist

This checklist separates planning review, implementation entry, production-smoke authorization, and Sprint exit. A checked planning item proves only the statement written on that line. It does not satisfy a later gate by implication.

Current-state notation:

- `[x]` — satisfied by the frozen base or by this planning artifact, subject to the exact planning commit verification.
- `[ ]` — not yet satisfied; blocking for the named later gate.

This checklist is evidence-oriented. A future reviewer must cite the relevant commit, test result, review disposition, configuration version/hash, or human authorization rather than checking a box from memory.

## A. Frozen baseline

- [x] Base checkpoint is exactly `abc323c05e21e3886b648878b35915bf72553c22`.
- [x] Sprint 0 Final Authorization records `SPRINT0_TECHNICAL_READINESS = YES`.
- [x] Sprint 0 Final Authorization records `SPRINT0_HUMAN_AUTHORIZATION_COMPLETE = YES`.
- [x] Sprint 0 Final Authorization records `SPRINT0_EXIT_READY = YES`.
- [x] Sprint 0 Final Authorization records `READY_FOR_SPRINT1_PLANNING = YES`.
- [x] Sprint 0 Final Authorization records `READY_FOR_SPRINT1_IMPLEMENTATION = NO`.
- [x] Frozen Audit, Frozen Planning, Sprint 0 contracts, Final Authorization, and incident record are treated as authoritative tracked inputs and were not modified by this planning package.
- [x] `S0-REVIEW-INCIDENT-001` remains `CLOSED — ACCEPTED WITH EXCEPTION`.
- [x] The incident’s historical checklist remains `INCIDENT / EXCEPTION`, not `VERIFIED`.
- [x] Scope-outside untracked entries remain under `NO FURTHER CONTENT READS`; only pathname/status observation is permitted.
- [x] `.env`, `reports/`, `data/`, `obsidian_vault/`, and `.mka/` remain outside the planning read/write boundary.

## B. Planning integrity

- [x] This is a new independent Sprint 1 planning package, not a modification of Sprint 0.
- [x] The new owner brief is distinguished from tracked Sprint 0 sources; Option A/F labels are not falsely attributed to a tracked frozen file.
- [x] Primary slice is named **Read-only Google Production Evidence Dry-run**.
- [x] “Complete snapshot” is consistently defined as **complete configured-range snapshot**, never an unrestricted entire-Spreadsheet read.
- [x] In-scope, out-of-scope, and explicitly deferred work are separated.
- [x] Architecture ends at redacted preview evidence and `STOP` with no downstream production activation arrow.
- [x] S1-WP0 through S1-WP5 each define goal, inputs, outputs, likely files, forbidden files, dependencies, test classes, stop conditions, Definition of Done, and rollback boundary.
- [x] Normative text DAG and informative Mermaid DAG express all required dependencies and both security gates.
- [x] Numbered security gates and package-specific freeze decisions are mapped to the composite DAG checkpoints.
- [x] Testing tiers, security gates, minimal observability, and code/data/credential/config/preview rollback are defined.
- [x] Release rollback is explicitly `N/A in Sprint 1`.
- [x] Open policy/implementation items are labeled without invented numbers or product choices.
- [x] Deferred future scope is not promoted into a Sprint 1 blocker or deliverable.
- [x] Content self-review covers Sprint 0 contradictions, Owner Decisions/Locks, scope creep, accidental write/activation/smoke authority, preview authority, and open-policy invention.
- [x] Planning diff is designed to contain exactly the five new files in this directory and no existing tracked-file modification.
- [x] Planning whitespace and exact Git file-set checks are required before commit.
- [ ] Human Planning Exit Review confirms the five documents are accurate and freezes them.

## C. Owner decisions and locks

- [x] Owner Decision 1 recorded: Option A approved; Option F is internal checkpoint/fallback only.
- [x] Owner Decision 2 recorded: exact canonical production Spreadsheet is pinned/allowlisted; arbitrary/fallback/discovered targets forbidden.
- [x] Owner Decision 3 recorded: dedicated read-only Service Account policy; no writer/Apps Script authority; long-lived JSON not automatically authorized.
- [x] Owner Decision 4 recorded: durable redacted preview only behind explicit destination/ACL/retention/cleanup/allowed-fields gate.
- [x] Owner Decision 5 recorded: first-live counts/source health stop for human baseline review; no self-derived automatic PASS.
- [x] Owner Lock 1 recorded: complete configured-range semantics and fail-closed coverage.
- [x] Owner Lock 2 recorded: only Google auth + Sheets read-only API for the exact target; all other production network authority forbidden.
- [x] Owner Lock 3 recorded: dry-run creates/updates no Release, pointer, journal, `last_success`, Vault, index, CapturedContent, or chunk corpus.
- [x] Owner Lock 4 recorded: live smoke requires all reviews and explicit human authorization.
- [ ] Planning Exit Review confirms no owner decision or lock is misstated.

## D. Production target

- [x] Canonical Spreadsheet ID is recorded exactly.
- [x] Target must be pinned and allowlisted outside caller control.
- [x] Arbitrary target, target discovery, active-spreadsheet identity, and fallback target are forbidden.
- [x] Exact ranges/fields are retained as `OPEN_IMPLEMENTATION_DECISION`, not expanded to an entire workbook.
- [ ] Exact configured ranges are frozen against the source schema, including required hidden/brand mapping/review sheets.
- [ ] Exact fields mask is frozen at the minimum complete semantic set, including `GridData.startRow`/`startColumn` and `textFormatRuns.startIndex` where needed.
- [x] Core sparse semantics are frozen: all-fields-absent `{}`/omitted cells are absent; present empty string/false/zero and other semantic branches are preserved; overlaps/ambiguity fail closed.
- [ ] Range-specific expected block identity, offset/extent reconciliation, and exact selection registry are frozen against the production schema.
- [ ] Runtime config schema/version and target/range/field binding are reviewed.
- [ ] Tests prove caller override, target mismatch, fallback, discovery, and unapproved range/field injection fail closed.

## E. Credential policy

- [x] Dedicated read-only Service Account architecture is frozen.
- [x] Spreadsheet permission and OAuth/API scope must both be read-only.
- [x] No writer scope and no Apps Script execute authority.
- [x] Credential cannot enter Git, logs, preview, mapper, normalizer, governance, brand review, or observability.
- [x] Managed identity/approved secret mechanism is preferred; product names are examples, not implementation requirements.
- [x] Long-lived JSON-only environment triggers a separate Security Owner decision and stop.
- [ ] Runtime credential mechanism is approved and documented without secret disclosure.
- [ ] Read-only permission/scope and no-write authority are independently reviewed.
- [ ] Credential revocation/rotation owner and procedure are confirmed.
- [ ] Credential-boundary unit/contract/security tests pass.

## F. Network boundary

- [x] Only Google authentication plus Google Sheets read-only API for the exact configured target is in Sprint 1 production authority.
- [x] Linked webpage fetch, generic HTTP, DNS content lookup, redirects, crawler, Slack, external LLM, arbitrary API, and scheduler network are forbidden.
- [x] Evidence URL remains a reference candidate and grants no capture authority.
- [x] Ordinary tests are zero-network; live controlled smoke is separate from pytest.
- [ ] Adapter endpoint/method/target allowlist is implemented and reviewed.
- [ ] Mocked adapter proves no write call or extra egress.
- [ ] Timeout, deadline, bounded retry, 429/5xx, and batching policy decisions are resolved before live eligibility.
- [ ] Network/egress Security Gate passes.

## G. Configured-range completeness

- [x] Every configured range must be complete, non-truncated, non-duplicated, and represented in a coverage proof.
- [x] Missing page/range/request part, hidden required sheet, grid block, merge/property data, or silent partial fails closed.
- [x] No F1 or downstream result is permitted before coverage succeeds.
- [x] Best effort cannot be labeled complete.
- [x] Snapshot and coverage proof must travel together in a typed configured-read result; a mutable side channel is forbidden.
- [x] WP2 must preserve that binding in an opaque batch context; WP3/WP5 cannot accept independently replaceable snapshot/envelope inputs.
- [ ] Raw response mapper is lossless for every required value/link/validation/merge/property branch.
- [ ] Absolute coordinates and multi-grid/request coverage are proven with non-zero `GridData` offsets.
- [ ] Semantically equivalent omitted/interior/trailing-empty encodings map to the same canonical snapshot/F1 while present empty string/false/zero remain semantic.
- [ ] Request/response target and range binding are proven.
- [ ] Partial/malformed/ambiguous response negative tests pass.
- [ ] Completeness Security Gate passes.

## H. Source health and F1

- [x] F1 uses the frozen deterministic source-snapshot contract only after coverage.
- [x] Run evidence separately binds config, coverage, schema, and policy versions because F1 alone does not attest to request selection.
- [x] Safe counts include configured ranges, sheets, entities, exclusions, and issues as needed for review.
- [x] First-live evidence must end in human review required, not automatic PASS.
- [x] Future threshold remains `OPEN_POLICY` pending real evidence and separate freeze.
- [x] Frozen Audit Sprint 1 F1/F2 test helper is retained as a pure synthetic/mocked same-selection second-result comparison; it is distinct from Sprint 5 release-time F2 and grants no second live read.
- [ ] Batch/F1/source-health envelope is implemented and deterministic.
- [ ] Versioned first-live baseline-evidence schema/hash is reviewed and implemented with only safe config/coverage references or hashes, F1, counts/structural codes, and `HUMAN_REVIEW_REQUIRED`.
- [ ] Baseline evidence/hash excludes telemetry and cannot carry raw cells, canonical/brand/full-preview data, human approval, or downstream instructions.
- [ ] F1/F2 helper equal/different and target/config/coverage/mapper-version mismatch tests pass without helper-owned I/O or a live F2 path.
- [ ] Critical sheet/header/bounds/ID coverage and count reconciliation tests pass.
- [ ] First-live self-approval and caller-supplied PASS negative tests pass.
- [ ] First controlled live counts are obtained under explicit smoke authorization.
- [ ] Human approves the first-live baseline.
- [ ] A future threshold, if needed, is separately reviewed/frozen; the first run does not self-approve it.

## I. Governance and minimization

- [x] Google remains metadata/identity/governance authority.
- [x] Governance/minimization is a mandatory stage, not a caller option.
- [x] Oral-only body/notes/evidence/URL are irreversibly excluded before preview/persistence-ready output.
- [x] Pending stays non-official/non-quotable; restricted and handle mapping remain outside general retrieval/citation.
- [x] Every valid non-public customer row enters denylist/governance preview regardless of NDA-field value.
- [x] Same-brand/handle merchant rows remain separate interviews; no automatic dedupe/merge/overwrite.
- [x] Evidence cannot become an approved metric or expand exposure channels.
- [x] Brand/ID output is candidate-only; no allocation, approval, merge, split, overwrite, or backfill.
- [ ] Production sheet/field registry, date/row-base semantics, and brand-candidate schema/algorithm are reviewed.
- [ ] Oral/restricted/pending/credential/URL sentinel scans pass across all output surfaces.
- [ ] Ambiguous/conflicting brand and ID cases reliably stop for review.
- [ ] Product/Governance Owner records the production brand-candidate `approve`/`split`/`merge`/`exclude` disposition, or explicitly defers that Frozen Audit approval point when the baseline-only live gate creates no production candidates.
- [ ] Minimization / Preview Security Gate passes.

## J. Preview policy and observability

- [x] Preview is Evidence only, Non-authoritative, Non-apply, Non-release, and Non-activation.
- [x] No default production output path exists.
- [x] `reports/`, `data/`, `obsidian_vault/`, and `.mka/` cannot be convenience defaults.
- [x] Minimal observability allowlist and prohibited payload list are frozen in this plan.
- [x] Raw credential/cell/body/full URL/unredacted exception data is forbidden.
- [ ] Versioned Sprint 1 evidence schema is implemented and reviewed.
- [ ] Counts and artifact hash reconcile deterministically; the artifact hash covers canonical redacted evidence bytes and excludes correlation ID, latency, retry count, timestamps, and other telemetry.
- [ ] In-memory-only mode is reviewed for the controlled smoke, **or** destination, ACL, allowed fields, retention, cleanup, and exact artifact rollback are all frozen.
- [ ] Durable output fails closed when any output policy component is absent.
- [ ] Preview/observability sentinel and negative tests pass.
- [ ] Preview Security Gate passes.

## K. Testing

- [x] Unit, contract, synthetic integration, mocked network adapter, safe local regression, production smoke candidate, and live controlled smoke tiers are distinguished.
- [x] Live controlled smoke is explicitly not ordinary pytest.
- [x] Synthetic fixtures and production-data exclusions are defined.
- [x] Cross-package negative/security matrix is defined.
- [x] `tests/sprint1/conftest.py` and a deliberate guard self-test are required because `tests/sprint0/conftest.py` does not govern the sibling Sprint 1 subtree.
- [ ] Every WP’s targeted unit/contract/negative/security tests pass with actual command/result evidence.
- [ ] Relevant frozen Sprint 0 regression passes.
- [ ] Relevant safe legacy/local Excel regression passes or each prohibited/unsafe omission is explicitly recorded.
- [ ] Lint/type checks pass if configured; otherwise omission is stated.
- [ ] Integration tests prove no production write/egress/persistence/downstream mutation.
- [ ] Production smoke candidate evidence package is complete.

## L. Security review

- [ ] Credential Gate passes.
- [ ] Target Gate passes.
- [ ] Egress Gate passes.
- [ ] Completeness Gate passes.
- [ ] Snapshot Integrity Gate passes.
- [ ] Production Data Gate passes.
- [ ] Oral-only Minimization Gate passes.
- [ ] Logs / Exceptions Gate passes.
- [ ] Preview Gate passes.
- [ ] Persistence Gate passes or reviewed in-memory-only mode is fixed.
- [ ] Rollback Gate passes.
- [ ] Audit Evidence Gate passes.
- [ ] Security reviewer approves the Sprint 1 implementation and proposed one-run smoke boundary.

## M. Repository review

- [x] Planning package is scoped to five new documentation files only.
- [x] No production code/test/config implementation is part of this planning package.
- [x] Planning exact five-file staged diff and whitespace are independently verified before commit.
- [ ] Planning parent, commit message/file set, normal push, and local/remote SHA are independently verified as the repository handoff receipt.
- [ ] Each implementation WP actual diff matches its reviewed likely-file boundary or receives an explicit scope amendment.
- [ ] No unrelated/staged/user/other-agent change is included in any commit.
- [ ] No frozen Audit/Planning/Final Authorization/incident file is modified.
- [ ] Implementation repository reviewer approves exact scope, compatibility, test evidence, and rollback boundary.

## N. Production-smoke authorization

- [ ] Sprint 1 implementation has completed its Integration Checkpoint.
- [ ] Code review is approved.
- [ ] Security review is approved.
- [ ] Approved test evidence is complete.
- [ ] Credential-boundary review is approved.
- [ ] Network-boundary review is approved.
- [ ] Preview/persistence policy review is approved.
- [ ] Repository review is approved.
- [ ] Exact commit/config/target binding and operator procedure are recorded safely.
- [ ] Reviewed invocation mechanism is unreachable from ordinary CLI/scheduler/pytest paths and binds the one authorization to the exact commit/config/credential.
- [ ] Approved minimal non-content control-plane store/ACL/retention/audit is frozen; before Google read it atomically claims `unused → claimed`, rejects concurrent/replayed claims, and has an explicit crash/lease disposition.
- [ ] Terminal used/closed disposition is proven and invocation authority is disabled after success or failure.
- [ ] Human explicitly authorizes **one** controlled read-only smoke.
- [ ] No automatic repeat, scheduler, or live pytest path exists.
- [ ] First-live operator procedure stops after coverage plus F1/source-health counts; it does not continue production data into later Option A stages before the human baseline decision.

## O. Sprint exit

- [ ] One explicitly authorized controlled read-only smoke has run.
- [ ] Exact configured-range completeness was proven.
- [ ] F1 and safe source-health/count evidence were produced.
- [ ] Run stopped before any downstream production mutation.
- [ ] First-live run did not process production data beyond the Owner Decision 5 baseline checkpoint.
- [ ] Human reviewed and explicitly approved the first-live baseline evidence.
- [ ] Product/Governance Owner separately authorizes or explicitly defers post-baseline production execution of the later Option A stages.
- [ ] Any production brand candidates created under separate authority receive human `approve`/`split`/`merge`/`exclude` disposition, or the approval point is explicitly deferred when none were created.
- [ ] Product/Governance Owner freezes or explicitly defers the source-health threshold draft after reviewing first-live evidence.
- [ ] No Google write, forbidden egress, credential/raw-data leak, or unapproved durable output occurred.
- [ ] No Release Candidate, Official Release, active pointer, journal, `last_success`, Vault, Markdown, Official DB/FTS/vector, CapturedContent, or production chunk corpus was created/updated.
- [ ] Preview evidence remains non-authoritative.
- [ ] Product/Governance Owner completes Sprint 1 exit review.
- [ ] Security Reviewer completes Sprint 1 exit review.
- [ ] Repository Reviewer completes Sprint 1 exit review.
- [ ] Open follow-ups are recorded without being silently treated as completed.

## Gate calculations

### `READY_FOR_SPRINT1_PLAN_EXIT_REVIEW`

Required now:

- frozen baseline preserved;
- five-document planning package complete;
- owner decisions/locks, authority boundaries, WPs/DAG, tests/security/rollback, and blocking checklist recorded;
- open policies retained without invented values;
- exact documentation diff/whitespace/repository checks complete before handoff.

Current value:

`READY_FOR_SPRINT1_PLAN_EXIT_REVIEW = YES`

This means only that the package may be submitted to human Planning Exit Review. It is not approval.

### `PLANNING_APPROVED`

Required:

- `READY_FOR_SPRINT1_PLAN_EXIT_REVIEW = YES`;
- human Planning Exit Review confirms and freezes the documents;
- no blocking architecture contradiction remains.

Current value:

`PLANNING_APPROVED = NO — PENDING HUMAN PLANNING EXIT REVIEW`

### `IMPLEMENTATION_READY`

Equivalent gate name in this plan: `READY_FOR_SPRINT1_IMPLEMENTATION`.

Required:

- `PLANNING_APPROVED = YES`;
- WP ordering/test/security/credential/network/target boundaries frozen;
- no unresolved blocker prevents S1-WP0/S1-WP1 design and controlled package entry;
- Planning Exit Review explicitly sets the value to `YES`.

Current value:

`IMPLEMENTATION_READY = NO`

`READY_FOR_SPRINT1_IMPLEMENTATION = NO — PENDING PLANNING EXIT REVIEW`

Package-level current values:

- `READY_FOR_S1_WP0_IMPLEMENTATION = NO — PENDING PLANNING APPROVAL AND WP0 ENTRY FREEZES`;
- `READY_FOR_S1_WP1_IMPLEMENTATION = NO — PENDING CONFIGURATION/SELECTION, CREDENTIAL, TRANSPORT, AND SECURITY FREEZES`.

A future program-level `YES` authorizes only a package whose own entry gates pass. It does not authorize WP1, credential creation/use, network access, or a production read by implication.

### `READY_FOR_PRODUCTION_SMOKE`

Required:

- implementation complete through Integration Checkpoint;
- all test, security, credential, network, preview, persistence/rollback, and repository gates pass;
- exact one-run candidate reviewed;
- explicit human production-smoke authorization recorded.

Current value:

`READY_FOR_PRODUCTION_SMOKE = NO`

### `SPRINT1_EXIT_READY`

Required:

- one authorized controlled smoke completed;
- complete configured-range/F1/source-health evidence reviewed;
- human baseline approval completed;
- Product/Governance Owner records the post-baseline Option A/brand-candidate approval-point disposition and freezes or explicitly defers the source-health threshold draft;
- no forbidden side effect or authority promotion;
- all three Sprint 1 exit reviews complete.

Current value:

`SPRINT1_EXIT_READY = NO`

## Current blockers and open items

- Planning blockers preventing submission to Planning Exit Review: `NONE`.
- Human Planning Exit Review: pending and intentionally blocks planning approval/implementation readiness.
- Exact ranges/fields and range-specific sparse/coverage mapping, raw DTO containment, credential runtime, transport timing/retry/batching, correlation-ID contract, baseline/full-preview evidence schemas, controlled-smoke invocation/atomic replay control, preview output policies, post-baseline Option A/brand disposition, first-live counts, and future threshold remain explicit downstream entry/open decisions.
- Production smoke remains unauthorized.
