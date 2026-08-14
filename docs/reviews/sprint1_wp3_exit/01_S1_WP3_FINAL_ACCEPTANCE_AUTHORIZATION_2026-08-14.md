# S1-WP3 Final Acceptance & Human Freeze Authorization

## 1. Purpose

This record documents final human acceptance of S1-WP3 and freezes the accepted S1-WP3 code state before S1-WP4 entry review. It is a docs-only governance authorization and does not change the frozen code identity.

## 2. Frozen code identity

- Branch: `codex/impl/sprint1-wp3-canonical-normalization`
- Frozen WP3 code SHA: `e04feeca7e1a4d0afb1e6a391c15ea8a9fd3133f`
- Immediate parent: `a504cd7772f5b2ae91491a023e477bcd0f8c32a1`
- Original WP3 implementation: `1bd05f378a7b8906682f92ee8db6c5c16fc98cbc`
- Frozen WP2 checkpoint: `2d7903a9e2c8a8e6b1577c1f8ab4515df6468aec`
- Frozen WP2 code: `05d539bcefa604d8297b79504b30c26cc7dc9e43`

The later documentation authorization commit is not the frozen WP3 code SHA. The frozen code SHA remains `e04feeca7e1a4d0afb1e6a391c15ea8a9fd3133f`.

## 3. WP3 purpose and authority boundary

WP3 produces only:

- non-authoritative in-memory canonical staging;
- safe governance/exclusion facts;
- redacted brand-review candidates; and
- schema-deferred ID diagnostics.

WP3 does not allocate or authorize:

- MREC;
- BRD;
- MET;
- final Brand;
- final SourceRecord;
- final PublicMetric;
- Brand approval;
- persistence;
- preview application;
- release;
- activation; or
- Google write.

`BrandReviewCandidate` remains `NON_AUTHORITATIVE` and `HUMAN_REVIEW_REQUIRED`. `UNIQUE_EVIDENCE` is not approval.

## 4. Input and run-mode boundary

WP3 accepts only the validated `CoverageProvenBatchContext`. The current WP3 implementation remains `SYNTHETIC`-only. `FIRST_LIVE` is still rejected at WP3. This freeze does not authorize production execution.

## 5. Owner Decision #1 — safe brand-candidate exposure

The approved rule permits a redacted `BrandReviewCandidate` to expose only approved safe review information such as:

- validated normalized handle;
- safe canonical hostname;
- sheet ID;
- one-based source row;
- safe deterministic references; and
- allowlisted reason/classification codes.

It must not expose:

- merchant/brand name;
- full URL;
- URL path, query, or fragment;
- restricted payload;
- pending body;
- oral-only body; or
- merchant notes.

## 6. Owner Decision #2 — `WP3_SAFE_HANDLE_V1`

Approved normalization is `NFKC` → trim → casefold. The maximum is 128 Unicode code points.

Allowed syntax is:

- an optional single leading `@`;
- Unicode letters;
- Unicode numbers;
- period;
- underscore; and
- hyphen.

Identity evidence is rejected if it contains:

- whitespace;
- newline or carriage return;
- control/separator payload;
- a second `@`;
- URL/body-shaped syntax;
- colon;
- slash or backslash;
- `?`;
- `#`;
- `&`;
- `=`;
- `%`; or
- oversized content.

An invalid raw handle is not reflected into a safe candidate, error, or `repr`.

## 7. Python guard threat model

The accepted T1/T2/T3 distinction is documented in `docs/governance/PYTHON_GUARD_THREAT_MODEL.md`:

- **T1 — IN SCOPE:** untrusted source data.
- **T2 — IN SCOPE:** supported API or ordinary application misuse.
- **T3 — OUT OF SCOPE as a standalone WP3 freeze blocker:** hostile arbitrary same-process Python execution using deliberate runtime manipulation such as `__closure__`, `object.__new__`, `object.__setattr__`, monkeypatch, `ctypes`, or debugger/runtime mutation.

Pure-Python guards are application correctness/provenance boundaries. They are not cryptographic authenticity, an interpreter sandbox, or hostile-process isolation. If future architecture permits untrusted Python code in-process, a real process/service isolation decision must be revisited.

## 8. B1–B8 pre-freeze hardening

Frozen code SHA `e04feeca7e1a4d0afb1e6a391c15ea8a9fd3133f` includes, and final independent verification confirms, closure of:

- **B1:** pytest collection/package identity baseline.
- **B2:** nested Google DTO payload-safe `repr`/`str`.
- **B3:** brand candidate name-divergence downgrade.
- **B4:** removal of decorative zero-effect WP3 contract metadata.
- **B5:** authority/security terminology clarification.
- **B6:** canonical Python guard threat-model documentation.
- **B7:** `PublicMetric` governance-state invariant.
- **B8:** multi-letter A1 lineage conversion.

## 9. PublicMetric governance invariant

The frozen rules require:

`can_quote_externally == True` only when both `review_status == APPROVED` and `publish_eligibility == ELIGIBLE`.

Additionally, `review_status == EXCLUDED` requires both `can_quote_externally == False` and `publish_eligibility == INELIGIBLE`.

The stable semantic error code is `PUBLIC_METRIC_GOVERNANCE_STATE_INVALID`.

Oral-only/written-retention exclusion remains before persistence eligibility/missing-MET enforcement.

## 10. Brand candidate divergence

`NAME_DIVERGENCE_WITHIN_CANDIDATE` is a review reason. Multiple distinct non-empty normalized names inside one candidate component prevent it from remaining `UNIQUE_EVIDENCE`. If no stronger conflict already applies, the classification is downgraded from `UNIQUE_EVIDENCE` to `AMBIGUOUS`.

Name remains review evidence only. It is not an identity graph edge.

## 11. Frozen acceptance evidence

The following is **FROZEN ACCEPTANCE EVIDENCE** from the Claude Opus 5 Effort Max final independent verification against frozen code SHA `e04feeca7e1a4d0afb1e6a391c15ea8a9fd3133f`. These checks were not rerun for this docs-only freeze authorization:

- Focused B1–B8 tests: **255 passed**.
- Both offline harnesses: **17 passed**.
- Full Sprint1: **323 passed, 3 warnings**.
- Full Sprint0: **1431 passed, 8 warnings**.
- Repository-wide collection: **2446 tests collected, 0 collection errors, 0 duplicate module loads**.
- Safe legacy subset: **30 passed, 6 warnings**.
- Tracked Python compile: **183 tracked Python files, PASS**.
- `pip check`: **No broken requirements found**.
- `git diff --check`: **PASS**.
- Cross-layer regression: **12/12 PASS**.
- New P0/P1/P2 blocking findings: **none**.

This record does not claim that default pytest is fully green.

## 12. Default pytest legacy environment exception

The repository-wide collection baseline is fixed, and default pytest now successfully collects the full suite. Current local execution still has nondeterministic failures/errors confined to pre-existing legacy tests under the classification `LEGACY_ENVIRONMENT_REPRODUCIBILITY`.

Root causes include:

- gitignored historical fixture drift under `data/`;
- macOS AppleDouble sidecar files; and
- bytecode/`__pycache__` interaction with historical filesystem hashing.

These conditions pre-date `e04feeca`, are outside the B1–B8 tracked changes, are not caused by Sprint0/Sprint1 package markers, and are nonblocking for WP3 freeze. Exact default-run passed/error counts are intentionally not recorded as a stable baseline because independent runs were nondeterministic.

```text
COLLECTION_BASELINE = PASS
TRACKED_TEST_REGRESSION = NONE
LEGACY_ENVIRONMENT_REPRODUCIBILITY = CONFIRMED
```

## 13. Final independent verification

- Claude Opus 5 Effort Max final verdict: `APPROVE`.
- All B1–B8: `CLOSED`.
- New P0/P1/P2 findings: `NONE`.
- Files changed by independent review: `NONE`.
- `READY_TO_FREEZE_WP3`: `YES`.

## 14. Nonblocking P3 observations

Each observation below is explicitly **NONBLOCKING** and **NOT_REQUIRED_BEFORE_WP3_FREEZE**:

- **P3-1:** `GoogleError.error_type` and `DataValidationCondition.condition_type` are still rendered in safe `repr` based on the Google API enum contract rather than a local allowlist.
- **P3-2:** The `PublicMetric` final invariant currently inspects raw pre-Pydantic kwargs in one private-token path; no supported/public construction bypass was found. Future hardening could move the check to validated model state.
- **P3-3:** WP3 `_column_index` remains single-letter-only because current registry columns are A–M. A future registry expansion beyond Z must update the reverse conversion.
- **P3-4:** The `PublicMetric` docstring should eventually preserve both facts: final MET schema requires gated construction, and governance fields require explicit downstream enforcement.
- **P3-5:** New `_safe_repr_fields` behavior has been verified under Pydantic 2.13.4; the Pydantic 1.x compatibility path was not independently executed.

## 15. Deferred Opus findings

The following findings remain intentionally deferred. They were not silently accepted as resolved, are not part of this WP3 freeze acceptance, and remain future hardening or separate work-package candidates. None is marked closed:

- F-01 restricted cross-source overlap enforcement.
- F-03 legacy RAG redact mismatch.
- F-04 CLI search result denylist filtering.
- F-05 denylist fail-open.
- F-07 candidate conflict reviewability expansion.
- F-08 URL identity-edge redesign.
- F-10 URL query-secret policy.
- F-11 oral heuristic expansion.
- F-12 digest privacy/HMAC question.
- F-15 `MetricPendingIdentity` redesign.
- F-16 normalization performance.
- F-17 source-ref cache.
- F-20 oral-policy deduplication.
- F-21 identity normalizer unification.
- F-22 AppleDouble legacy reproducibility.

## 16. Owner decisions still deferred

The following Claude Opus owner-decision candidates remain unresolved for separate future governance work; this freeze record does not resolve them:

- **OD-1:** denylist missing/corrupt fail-closed policy.
- **OD-2:** oral-only heuristic review policy.
- **OD-3:** cross-platform handle namespace.
- **OD-4:** aggregator/marketplace URL identity role.
- **OD-5:** `ExcludedSourceRef` digest/HMAC/privacy policy.

## 17. Freeze states

```text
S1_WP3_TECHNICAL_READINESS = YES
HUMAN_WP3_ACCEPTANCE = APPROVED
WP3_FROZEN = YES
FROZEN_WP3_CODE_SHA = e04feeca7e1a4d0afb1e6a391c15ea8a9fd3133f
READY_TO_FREEZE_WP3 = YES
READY_FOR_S1_WP4_ENTRY_REVIEW = YES
READY_FOR_S1_WP4_IMPLEMENTATION = NO
PRODUCTION_SMOKE_AUTHORIZED = NO
```
