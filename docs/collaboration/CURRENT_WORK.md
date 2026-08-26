# Current work

> 這是 Codex 與 Claude Code 的任務鎖定頁。開始改檔前必須更新；同一時間只能有一個 active implementer。

## Lock

- State: inactive
- Task: none
- Implementer: none
- Reviewer: none
- Branch: none
- Baseline commit: none
- Intended scope: none
- Started at: none
- Last updated: 2026-08-26T15:44:49+08:00

## Objective and done definition

- Objective: replace repetitive Git/governance checks with deterministic, fail-closed candidate verification, reviewer-packet generation, and an explicitly authorized, dry-run-by-default main-promotion tool.
- Done when: the three CLIs share one risk policy, reject HIGH-risk Fast Lane promotion, emit machine-readable evidence, pass targeted and relevant tests, document the external authorization boundary, and reach `REVIEW_READY` without staging, committing, pushing, changing `main`, or mutating production/Authority/Vault/content-index state.

## Progress

### Completed

- START lifecycle point recorded for Development Governance Fast Lane v1.
- Isolated worktree, branch, baseline, clean state, same-task active lock, and prior integration-task release verified.
- Added centralized fail-closed policy in `tools/governance_policy.py` for Git inspection, FAST / STANDARD / HIGH classification, rename-safe path evaluation, formal authorization-transition detection, and exact-SHA evidence validation.
- Added `governance_gate.py` candidate verification / promotion preflight with deterministic env or JSON evidence and non-zero safety failures.
- Added deterministic read-only `review_packet.py` Markdown output and explicit reviewer mutation-verification instructions.
- Added dry-run-by-default `promote_main.py`; execution requires both explicit switches, uses only `<candidate-sha>:refs/heads/main`, never force, and verifies remote main after push.
- Added 25 targeted temporary-repository / local-bare-remote tests covering clean/failure/risk/evidence/dry-run/execute/post-push/injection boundaries.
- Added `docs/governance/FAST_LANE.md`, the proposed decision record, and the future START / REVIEW_READY / CLOSED lifecycle rule.
- Stable Record V2 activation, row_v1 retirement, Authority/Vault/content-index mutation, production re-index, production changes, and main update remain out of scope and unauthorized.

### In progress

- Independent review pending; reviewer remains read-only and does not acquire the implementation lock.

### Not started

- Candidate commit, reviewer packet for that immutable candidate SHA, independent review, acceptance adjudication, push, main promotion, and CLOSED lifecycle update.

## Verification

- Initial: `git status --short --branch`, branch/HEAD/baseline verification, required collaboration/governance document readback, and repository/tool/test inventory. Initial worktree was clean at `668e401131128933975cac1523c6165f3ece2dd7`; branch has no upstream, so `git pull --ff-only` was not possible without changing tracking configuration.
- Targeted: `pytest -q tests/test_governance_gate.py tests/test_review_packet.py tests/test_promote_main.py` — 25 passed.
- Relevant existing: `pytest -q tests/test_git_provenance.py` — 8 passed.
- Combined final run: 33 passed in 10.39s using the primary checkout's existing `.venv` against this isolated worktree; no dependency download.
- Python compatibility: Python 3.9 `py_compile` passed for all four tool modules.
- Static safety readback: no `shell=True`; no stage/commit/merge/rebase/amend/reset/switch path; execute-path Git trace proved explicit non-force refspec; hostile ref input did not execute shell content.
- CLI surface: all three `--help` paths executed successfully.
- `git diff --check`: passed after final implementation/docs changes before this REVIEW_READY record update.
- Independent review: pending.
- Candidate risk: `STANDARD` because the scope contains tools, tests, protected governance docs, and lifecycle records; STANDARD requires independent-review plus acceptance evidence before any Fast Lane promotion.
- Known limitations: path/contract classification is conservative rather than semantic proof; evidence schema cannot authenticate a human identity; concurrent main coordination remains external; reviewer packet requires an immutable committed candidate SHA.
- `MAIN_UPDATE_AUTHORIZED=NO`.
- `MAIN_UPDATED=NO`; `PRODUCTION_CHANGED=NO`; no stage, commit, push, Authority/Vault/content-index mutation, production call, or production process action performed.
- Not run: full application suite (standalone development-governance tool surface; targeted plus existing Git-provenance coverage run), lint/type checks (no configured ruff/black/mypy/flake8 command), or independent review.

## Next exact action

- After explicit file-scope review, an authorized operator may create one narrow candidate commit; then an independent reviewer runs the generated reviewer packet and records exact-SHA evidence. Do not update main under this task's current authorization state.

## Blockers and unresolved user questions

- Stable Record V2 activation remains unauthorized.
- `row_v1` retirement remains unauthorized.
- Production re-index remains unauthorized.
- Independent review and acceptance evidence are required before promotion eligibility can be assessed.
- Unresolved user questions: none.

## Release or transfer

- Lock remains active; independent reviewer does not acquire the implementation lock.
- Handoff reference: this REVIEW_READY record plus `docs/governance/FAST_LANE.md`; implementation remains uncommitted by explicit user instruction.
- Frozen implementation candidate: `95e7909af512399be01f6b640e59936068c2ef17`.

## Fast Lane v1 pause record

- Status: `REVIEW_DEFERRED`
- Frozen candidate: `95e7909af512399be01f6b640e59936068c2ef17`
- Automated candidate gate: `PASS`
- Risk tier: `STANDARD`
- Independent adversarial review: deferred
- Reason: development-first workflow; comprehensive review will run at a later milestone gate.
- Candidate is not accepted and has not been promoted to `main`.
- `MAIN_UPDATED=NO`
- `PRODUCTION_CHANGED=NO`
- Paused at: 2026-08-26T15:44:49+08:00
