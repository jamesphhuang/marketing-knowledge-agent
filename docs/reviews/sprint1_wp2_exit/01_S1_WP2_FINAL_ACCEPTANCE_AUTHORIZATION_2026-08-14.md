# Sprint 1 WP2 Final Acceptance / Human Freeze Authorization — 2026-08-14

## Final Acceptance Checkpoint

- Repository: `marketing-knowledge-agent`
- Branch reviewed: `codex/impl/sprint1-wp2-source-health-envelope`
- Frozen WP2 implementation code SHA: `05d539bcefa604d8297b79504b30c26cc7dc9e43`
- Previous remediation commit: `992e2400112c8693171b01c7aac5d8b054a69319`
- Original WP2 implementation commit: `7eb4a455d64f04bd7a72cc9d7e5b7bb1681d2216`
- Frozen WP1 base: `228dd14db45997aa258d6c36e32ff77a00100571`
- Independent Final Acceptance Re-review: `APPROVE`
- New blocking findings: `NONE`

The human user formally accepts the completed S1-WP2 implementation, targeted remediations, and final independent implementation/security re-review. All original eight blockers are closed, including the final run-mode provenance blocker, and the reviewer reported no new P0, P1, or P2 blocking finding.

This record binds the WP2 freeze exactly to implementation code SHA `05d539bcefa604d8297b79504b30c26cc7dc9e43`. The later documentation-only authorization commit containing this record is not the frozen WP2 code SHA.

## Product / Governance Owner Approval

- Role: Product / Governance Owner
- Reviewer: Admin
- Date: 2026-08-14
- Approval: `APPROVED`

The human user explicitly accepted the final independent re-review verdict and authorized S1-WP2 closure and freeze. Codex did not independently verify organizational authority.

## Frozen WP2 Purpose and Authority Boundary

S1-WP2 is frozen as the mocked/offline Batch / F1 / Source-Health Envelope boundary. It accepts only a frozen WP0/WP1 `ConfiguredReadResult` and produces deterministic in-memory source-health and redacted baseline-evidence boundaries.

WP2 owns no Google authentication, Google read, transport, credential, filesystem persistence, preview persistence, release, activation, or WP3 governance/normalization authority. It has zero production network authority and zero filesystem persistence authority.

The only trusted WP2 source input is the exact concrete `ConfiguredReadResult` from the frozen WP0/WP1 construction boundary. Subclasses, duck-typed inputs, bare snapshots, separately supplied snapshots/proofs, unsupported configuration identities, and non-frozen selection identities are rejected.

The frozen selection identity is:

```text
sha256:e4dbf5e50b393729eabd6187590a9419a9a0f8741f97a36bfc2d48994ceac48e
```

## Frozen F1 and Structural Profile

WP2 reuses `compute_source_fingerprint()` from `canonical_serialization.py` without redefining F1.

```text
fingerprint_semantics_version = canonical-source-snapshot-v1
F1_wire_form = sha256:<64 lowercase hex>
```

F1 is computed only after configured-range validation and coverage succeed. F1 alone does not prove source selection. Evidence separately binds configuration identity, coverage identity, mapper version, snapshot schema version, fingerprint semantics version, and F1.

The frozen structural profile contains exactly five configured sheets and ranges:

1. `merchant_case`
2. `restricted_customer`
3. `public_metric`
4. `pending_metric`
5. `handle_mapping`

Four sources use header-based bindings. `pending_metric` uses the one frozen positional binding, range A:D, with first data row 3 and no pending-header inference.

The following remain deferred and must not be treated as healthy zero diagnostics: MREC, MET, BRD, ID Review Status, 品牌 ID 對照, and 品牌 ID 初始化審核. WP2 does not invent or allocate these IDs.

## Frozen Source-Health Envelope and Run-Mode Provenance

The accepted `SourceHealthEnvelope` binds schema version, run mode, correlation ID, target identity hash, configuration identity and version, coverage identity, mapper version, snapshot schema version, fingerprint semantics version, source-health rules version, source fingerprint, safe structural counts, sensitive in-memory source counts, structural reason codes, deferred check codes, and disposition.

The envelope remains immutable at the supported boundary. Nested semantic containers are defensively canonicalized or immutable, and caller-retained mutable aliases cannot change trusted envelope semantics.

Trusted run-mode authority comes from an internal builder-issued opaque provenance mechanism, not from `envelope.run_mode`, disposition, caller input, or a reconstructed envelope. First-live and synthetic builders use distinct internal authority. Trusted context validation binds provenance to builder-issued mode authority, the exact `ConfiguredReadResult` identity, and the exact `SourceHealthEnvelope` identity, and requires provenance mode to agree with envelope mode and disposition semantics.

The previously demonstrated synthetic-context → replaced mode/disposition → rebound context → `FirstLiveBaselineEvidence` exploit is frozen as blocked. Replacement, reconstruction, and provenance transplant cannot upgrade synthetic authority to first-live authority. Private underscore internals remain unsupported implementation details and are not public authority surfaces.

## Frozen Disposition and Baseline-Evidence Authority

WP2 v1 has exactly these dispositions:

- `STRUCTURAL_BLOCK`
- `HUMAN_REVIEW_REQUIRED`
- `SYNTHETIC_CHECKS_COMPLETE`

The frozen rules are:

```text
FIRST_LIVE + structural success = HUMAN_REVIEW_REQUIRED
FIRST_LIVE + structural failure = STRUCTURAL_BLOCK
SYNTHETIC + structural success = SYNTHETIC_CHECKS_COMPLETE
```

WP2 v1 has no PASS, HEALTHY, APPROVED, threshold-generated PASS, or self-approval disposition. A caller cannot choose disposition.

Authoritative `FirstLiveBaselineEvidence` may arise only from an exact trusted first-live `CoverageProvenBatchContext`. A direct public constructor, arbitrary mapping, `from_mapping` reconstruction, caller-recomputed hash, synthetic context, or forged/rebound context cannot authoritatively manufacture it.

The current 18-field `FirstLiveBaselineEvidence` schema is frozen exactly as implemented. Run-mode provenance is not a wire/evidence field. Evidence remains redacted and non-authoritative for content or release purposes.

The evidence must not contain raw snapshots or cells, claims, notes, customer or merchant names, URLs, sensitive occupied-row counts, correlation ID, runtime telemetry, credentials, authorization headers, human approval, numeric thresholds, WP3 objects, WP4 preview content, or release/activation instructions.

## Frozen Evidence Hash and Count Policy

The evidence hash remains SHA-256 in `sha256:<64 lowercase hex>` form, with domain separator `first-live-baseline-evidence:v1`. Its canonical representation is UTF-8 JSON with sorted keys, compact separators, and `allow_nan=False`.

The deterministic hash includes semantic evidence fields and excludes `evidence_hash`, correlation ID, timestamps, latency, retry count, PID, and other runtime telemetry. It is distinct from F1, the selection configuration identity, the transport policy identity, and any future WP4 artifact hash. The hash provides integrity, not provenance; provenance comes from the trusted WP2 construction boundary.

Per-source occupied-row counts remain `SENSITIVE_IN_MEMORY_ONLY`. They must not enter baseline evidence, canonical evidence JSON, logs, telemetry, safe repr, errors, or durable artifacts. Approved baseline safe counts remain structural-only. Later exposure of occupied-row, canonical-entity, governance-exclusion, or deferred-diagnostic counts requires separate Owner authorization.

Structural counts use exact Python integer semantics; reject bool, negatives, float/string coercion, and impossible large values; respect frozen maxima; and reconcile observed/valid counts with the expected profile. Structural issue count reconciles with structural reason codes. Sensitive occupied-row counts remain within frozen configured-range capacities.

Reason and deferred codes are fixed, stable, category-separated allowlists rather than free-text payload channels. Unknown or duplicate codes, control characters, newlines, and arbitrary payload interpolation are rejected, and ordering is deterministic.

## Frozen Opaque Context and F1/F2 Helper

`CoverageProvenBatchContext` remains sensitive, opaque, non-evidence, non-persistable, and immutable at the supported boundary. It binds the exact `ConfiguredReadResult`, exact `SourceHealthEnvelope`, and trusted run-mode provenance.

Ordinary public construction and subclass forgery are forbidden. Result/envelope swaps and metadata, F1, coverage, or version mismatches are rejected. Its repr is payload-free; generic serialization and pickle are unavailable; copy/deepcopy do not mint authority; and the context cannot serve as a durable evidence object.

The F1/F2 comparison helper remains pure and zero-I/O. Both contexts must pass full authenticity, provenance, and binding validation. F1 equality cannot override incompatible target, configuration, coverage, mapper version, snapshot schema version, fingerprint semantics, or context/provenance authenticity. The helper grants no second live read.

## Frozen Correlation and Payload-Safety Boundaries

The service generates canonical lowercase RFC 4122 UUID4 correlation IDs of exactly 36 characters. Callers cannot provide them. They are payload-independent, not authorization material, and excluded from the deterministic evidence hash.

Safe outputs and application errors must not expose raw cells; merchant/customer names; restricted/pending payloads; claims; notes; URLs; handle payload; cleartext Spreadsheet ID; credentials; authorization headers; payload-bearing exception causes or contexts; or opaque provenance internals. Safe application errors remain stable-code based, and payload-bearing failures must not escape through `__cause__` or `__context__`.

## Fresh Independent Acceptance Evidence

The final independent re-review executed safe synthetic/offline verification against frozen WP2 code SHA `05d539bcefa604d8297b79504b30c26cc7dc9e43`:

| Verification | Result | Warnings |
| --- | --- | --- |
| Focused WP2 | 113 passed, 0 failed, 0 skipped, 0 xfail | 0 |
| Full `tests/sprint1` | 209 passed, 0 failed, 0 skipped, 0 xfail | 3 existing warnings |
| Relevant fingerprint/contracts | 65 passed, 0 failed, 0 skipped, 0 xfail | 0 |
| Sprint 0 integration | 29 passed, 0 failed, 0 skipped, 0 xfail | 0 |
| Full `tests/sprint0` | 1405 passed, 0 failed, 0 skipped, 0 xfail | 8 existing warnings |
| Safe legacy | 30 passed, 0 failed, 0 skipped, 0 xfail | 6 existing warnings |
| Tracked-only Python compile | 177 files compiled, 0 failures | 0 |
| `pip check` | PASS | 0 |
| `git diff --check` | PASS | 0 |

Adversarial verification confirmed that the original manual synthetic-to-first-live upgrade, bidirectional run-mode forgery, provenance transplant, missing/wrong provenance, context subclassing, result/envelope swaps, forged-context F1/F2 comparison, and unauthorized evidence construction are blocked. Payload sentinel and cleartext-target scans passed, with zero network activity and zero filesystem output.

## Nonblocking and Deferred Items

The existing Python 3.9 EOL warning, LibreSSL/urllib3 warning, Pydantic deprecation warnings, pip cache-permission warning, deliberate underscore import of private module internals, and the previously noted fail-closed private-slot deletion P3 do not block WP2 freeze and are not remediated by this authorization.

## Authorization State

```text
S1_WP2_TECHNICAL_READINESS = YES
HUMAN_WP2_ACCEPTANCE = APPROVED
WP2_FROZEN = YES
READY_TO_FREEZE_WP2 = YES
READY_FOR_S1_WP3_ENTRY_REVIEW = YES
READY_FOR_S1_WP3_IMPLEMENTATION = NO
PRODUCTION_SMOKE_AUTHORIZED = NO
```

`READY_FOR_S1_WP3_ENTRY_REVIEW = YES` authorizes only a read-only WP3 entry review. It does not authorize WP3 implementation.

## Explicitly Unauthorized Follow-on Work

This authorization does not permit WP3 implementation, WP4, WP5, Google authentication, ADC, Service Account use, production Google API requests or Sheet reads, production F1 or counts, production first-live evidence, human baseline approval, a second live read, production thresholds, preview persistence, production smoke, release, or activation.

## Repository Disposition

This authorization record does not modify WP2 source or tests, historical review or planning records, dependencies, credentials, runtime configuration, or production state.

The only intended repository change for this authorization action is this new record:

`docs/reviews/sprint1_wp2_exit/01_S1_WP2_FINAL_ACCEPTANCE_AUTHORIZATION_2026-08-14.md`

## Remaining Gates

A read-only S1-WP3 entry review may begin. S1-WP3 implementation remains unauthorized and requires a separate future human authorization. Production smoke remains unauthorized and requires a separate explicit human authorization.
