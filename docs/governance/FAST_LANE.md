# Development Governance Fast Lane v1

Fast Lane v1 automates deterministic Git and development-governance checks. It reduces repeated
manual `status` / `diff` / `grep` / reviewer-packet work without reducing independent review,
external authorization, or HIGH-risk safety boundaries.

It is a development-governance workflow only. It does not activate Stable Record V2, retire
`row_v1`, mutate Authority/Vault/content-index/alias/asset/payload state, re-index production,
change production configuration, restart a production process, or authorize a main update.

## Components

- `tools/governance_policy.py`: canonical Fast Lane risk rules, Git inspection, and evidence schema.
- `tools/governance_gate.py`: candidate verification and promotion preflight.
- `tools/review_packet.py`: deterministic, read-only reviewer packet on stdout.
- `tools/promote_main.py`: dry-run-by-default `origin/main` promotion preflight and explicit execute
  path.

The three CLIs must use `governance_policy.py`; they must not maintain separate risk lists.

## Risk tiers

| Tier | Examples | Fast Lane promotion |
| --- | --- | --- |
| `FAST` | Explicitly allowlisted docs-only changes outside protected governance records | Allowed after candidate checks and acceptance evidence |
| `STANDARD` | Application code, tests, tools, protected governance docs, unknown paths | Allowed only after candidate checks, independent review evidence, and acceptance evidence |
| `HIGH` | Authority/formal data/Vault/content-index/stable identity/security-boundary/secret/migration/production/deployment paths; formal authorization transitions | Never allowed |

Unknown or unclassified paths become `STANDARD` and produce
`NON_GOVERNANCE_DRIFT=PRESENT`; they never default to `FAST`. A rename is classified with rename
detection disabled so both its old and new paths are evaluated. Tests are `STANDARD` even when they
exercise a HIGH-risk module, because test code does not itself mutate that production boundary.

`HIGH` always produces `FAST_LANE_ALLOWED=NO`. Review or authorization flags cannot override it.
The path and authorization-transition rules in `governance_policy.py` are the canonical executable
policy; this document explains that policy but is not a second implementation.

## Candidate verification

Run from the candidate repository:

```bash
python3 tools/governance_gate.py verify \
  --base <baseline-sha-or-ref> \
  --candidate <candidate-sha-or-ref>
```

The gate resolves both inputs to immutable commit SHAs, requires the base to be an ancestor,
requires a clean worktree, runs `git diff --check`, enumerates changed paths, rejects added conflict
markers, records candidate commit identity, and classifies risk. Default output is deterministic
`KEY=VALUE` evidence; `--format json` emits one JSON object. Success exits `0`; every safety failure
exits non-zero.

`verify` may report `GOVERNANCE_GATE=PASS` with `RISK_TIER=HIGH`: this means the deterministic Git
checks passed, not that Fast Lane promotion is allowed. `promote-preflight` converts HIGH risk into
a blocking result:

```bash
python3 tools/governance_gate.py promote-preflight \
  --expected-main <sha> \
  --candidate <sha>
```

No command stages, commits, rebases, amends, merges, pushes, or changes main.

## Reviewer packet

```bash
python3 tools/review_packet.py \
  --base <baseline-sha-or-ref> \
  --candidate <candidate-sha-or-ref> \
  --test "pytest tests/test_example.py: PASS"
```

The Markdown packet is written to stdout and contains the resolved identities, merge base, changed
files, commits, diff stat, risk tier, gate result, operator-provided tests (or `Not provided`), the
relevant boundary, and reviewer instructions. Redirect it only to a reviewer-artifact location;
the tool itself has no output-file option and cannot update `CURRENT_WORK`, Authority, or other
formal governance state.

The reviewer must remain read-only, record the candidate SHA before and after review, verify the
worktree/candidate were not modified, and classify findings as `BLOCKING`, `NONBLOCKING`, or
`INFORMATIONAL`. `REVIEWER_MODIFIED_CANDIDATE=NO` is a reviewer-verified assertion, not a value the
packet generator can prove in advance.

## Evidence files

Evidence is JSON outside the candidate commit, because embedding a file that names its own final
commit SHA would be circular. Every evidence file is bound to the exact baseline and candidate.
Stale or mismatched evidence fails closed.

FAST acceptance evidence:

```json
{
  "accepted": true,
  "baseline_sha": "<full-sha>",
  "candidate_sha": "<full-sha>",
  "reviewer_modified_candidate": false
}
```

STANDARD additionally requires independent review evidence:

```json
{
  "baseline_sha": "<full-sha>",
  "candidate_sha": "<full-sha>",
  "reviewer_modified_candidate": false,
  "verdict": "PASS"
}
```

The accepted review verdicts are `PASS` and `PASS_WITH_NONBLOCKING_FINDINGS`. STANDARD promotion
requires `--review-evidence` and `--acceptance-evidence`; FAST requires
`--acceptance-evidence`. HIGH accepts neither as a bypass.

## Main promotion

Dry-run is the default and never pushes:

```bash
python3 tools/promote_main.py \
  --expected-main <exact-full-sha> \
  --candidate <sha-or-ref> \
  --acceptance-evidence /path/to/acceptance.json
```

For STANDARD, also pass `--review-evidence /path/to/review.json`. Dry-run fetches
`origin`'s `refs/heads/main` into `FETCH_HEAD`, verifies it exactly equals `--expected-main`, checks
fast-forward ancestry, runs the governance gate, rejects HIGH, and validates tier-appropriate
evidence. A passing dry-run emits `PROMOTION_PREFLIGHT=PASS`, `MODE=DRY_RUN`, and
`SAFE_TO_PROMOTE=YES`. Fetch updates Git's transient `FETCH_HEAD`; it does not update local or remote
main.

A real update requires both explicit switches:

```bash
python3 tools/promote_main.py \
  --expected-main <exact-full-sha> \
  --candidate <sha-or-ref> \
  --review-evidence /path/to/review.json \
  --acceptance-evidence /path/to/acceptance.json \
  --execute \
  --confirm-main-update-authorized
```

`--execute` does not grant authorization. `--confirm-main-update-authorized` means only that the
operator declares an external human governance decision has already authorized this exact main
update. The script cannot create, infer, or substitute for that authorization.

Execution uses the exact resolved refspec `<candidate-sha>:refs/heads/main`; no force option is
available. It fetches again after push and requires the observed remote main to equal the candidate.
Remote drift is reported and never auto-resolved. A successful receipt includes old/new SHA,
fast-forward status, `FORCE_USED=NO`, mode, and refspec. A post-push verification failure is a hard
failure requiring human investigation; the script does not attempt a destructive rollback.

## CURRENT_WORK lifecycle

For future tasks, keep the durable collaboration record at three primary lifecycle points:

1. `START`: acquire the implementation lock and record scope/baseline/done definition.
2. `REVIEW_READY`: record implementation summary, changed files, actual checks/results, candidate
   risks, limitations, independent-review status, and confirmation that no production/main write
   occurred. Keep the implementation lock active.
3. `CLOSED`: record review/adjudication, integration state, remaining risks, and release/transfer the
   lock.

Do not permanently copy each deterministic `stage`, `status`, `diff`, `grep`, commit, or push command
into `CURRENT_WORK`; preserve their machine-readable artifacts where review requires them. Major
governance decisions still belong in append-only `DECISIONS.md`. This lifecycle applies going
forward and does not rewrite historical records.

## Known limits

- Risk classification is conservative path/contract policy, not semantic proof of arbitrary code.
- Evidence files prove schema and exact SHA binding; they do not prove the human identity behind a
  file. Human governance remains external.
- A normal non-force push and post-push fetch detect ordinary drift, but external coordination is
  still required around concurrent main updates.
- Fast Lane v1 has no Web UI, database, GitHub App, approval service, daemon, or production control.
