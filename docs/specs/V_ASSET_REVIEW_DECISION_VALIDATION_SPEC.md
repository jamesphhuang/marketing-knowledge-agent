# V. Asset URL & Identity Review Decision Validation

## 1. Scope

`mka validate-asset-review-decisions` is a read-only gate between the asset metadata inventory and a future Apply Preview. It validates asset identity plus `asset_url` and `canonical_url` decisions. It does not apply decisions, write Obsidian, change SQLite, or enable retrieval constraints.

The following fields remain outside this decision scope and fail closed in retrieval: `published_at`, `publication_status`, `interview_date`, `interview_status`, `review_status`, and `partner_name`.

## 2. Decision Schema

The source template remains row-per-field. `(asset_id, field)` is the join key; `record_id`, brand, asset type, proposal, source location, provenance, confidence and conflict fields must exactly match the enrichment preview.

Allowed decisions:

| Decision | Meaning | Apply Preview eligibility |
| --- | --- | --- |
| `approve` | Accept this exact non-empty proposal | eligible only after all gates pass |
| `reject` | Reject this field proposal | field is not applied; note required |
| `needs_update` | Proposal or evidence needs correction | blocked; note required |
| `exclude_asset` | Asset must not be applied | excluded; note required |
| `manual_review` | Human investigation remains open | blocked; note required |

Blank and unknown values are errors. `reviewer` and ISO `reviewed_at` are required for both URL fields. The current schema does not permit replacement values: changing `proposed_value` or provenance columns is an error.

## 3. Conservation And Identity

- Every inventory `asset_id` is unique and maps to one `record_id`, brand and asset type.
- Review coverage is compared with the complete enrichment preview; missing, extra and duplicate `(asset_id, field)` rows are errors.
- Only `asset_url` and `canonical_url` decisions are required in this sprint. Other field rows remain conserved but are not treated as approvals.
- Sixteen invalid/missing asset slots remain governance evidence. They cannot become content assets even when a URL decision says approve.

## 4. URL Rules

`asset_url` and `canonical_url` are independent decisions. Approval of either does not approve the other. Approved values must be the exact source proposal and absolute HTTP(S) URLs.

Canonical URLs cannot be search, redirect, short, internal path or unsupported-scheme values. Tracking parameters produce a warning and manual-review eligibility. A canonical URL shared by different assets also requires duplicate review. This validator does not use the network or rewrite URLs.

## 5. Eligibility

Validation-only states are:

```text
ready_for_apply_preview
incomplete_review
invalid_decision
conflicting_decision
missing_evidence
governance_blocked
manual_review_required
excluded
```

`ready_for_apply_preview` never means applied. Errors, unresolved manual review, identity mismatch, governance evidence, missing proposals or conflicting decisions prevent eligibility. URL approval never derives publication date/status or any interview, review or partner field.

## 6. Security And Outputs

All human cells are untrusted. Formula prefixes, control characters, traversal, credential-like input, malformed URLs and restricted denylist matches are blocked without echoing sensitive values. Reports are written under `reports/asset_metadata_review_validation/`; the source template is never modified.

```text
review_validation_summary.md
review_validation_errors.csv
review_validation_warnings.csv
review_decision_status.csv
apply_preview_eligibility.csv
unresolved_manual_review.csv
```

Any validation error requires review fixes before a separate Apply Preview sprint.
