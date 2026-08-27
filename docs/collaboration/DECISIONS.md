# Collaboration decisions

正式協作決策採 append-only 紀錄。若決策變更，新增一筆 superseding decision，不覆寫舊決策的歷史。

## DEC-20260824-01 — Git is the source of truth

- Status: accepted
- Decision: Git repo 是專案規格、程式碼、正式決策、驗證與交接狀態的唯一正式來源。
- Consequence: Codex 與 Claude Code 不依賴彼此的聊天紀錄；重要結論整理後寫回 repo。

## DEC-20260824-02 — Separate Obsidian collaboration vault

- Status: accepted
- Decision: 另建協作 Vault，不使用既有 `obsidian_vault/` 記錄工作對話。
- Sync: Obsidian Sync，標準加密，不與其他雲端同步服務並用。
- Content boundary: 只存去敏摘要；不存客戶資料、原始 Excel、API key、憑證或完整 AI 對話。
- Retention: 每日摘要滿 90 天後移入可查閱的 `Archive/`；正式決策與交接永久保留在 Git。

## DEC-20260824-03 — Dynamic roles with one implementer

- Status: accepted
- Decision: Codex 與 Claude Code 不固定分工；每個任務指定唯一實作者，其他 agent 僅閱讀、研究或審查。
- Consequence: 任何責任轉移都必須有明確 handoff 與 baseline commit，不得默認接管 active task。

## DEC-20260824-04 — Three required update points

- Status: accepted
- Decision: 任務開始、重大決策、任務完成或中斷時，都要更新正式協作紀錄。
- Consequence: AI 可草擬摘要，但只有確認並寫入 Git 的內容才是正式狀態。

## DEC-20260826-01 — Stable Record Authority M3E materialized, not activated

- Status: accepted
- Task: WP0.4b-M3E Stable Record Authority Materialization
- Decision: The reviewed 121-record Stable Record Authority package has been formally materialized and passed implementer-side post-materialization integrity verification.
- Governance state:
  - `AUTHORITY_MATERIALIZATION_AUTHORIZED=YES`
  - `AUTHORITY_MATERIALIZED=YES`
  - `STABLE_RECORD_V2_ACTIVATED=NO`
  - `ROW_V1_RETIRED=NO`
  - `PRODUCTION_REINDEX_AUTHORIZED=NO`
- Record set: 121 unique Stable Record IDs; 120 approved identity continuations and 1 approved new identity (`MKA-MC-00121`).
- Formal Authority package path: `data/identity/authority/stable_record_v2` (Git-ignored governed data; package bytes are not committed to Git).
- Formal Authority package identity:
  - `stable_record_registry.csv` SHA256: `307a3e7f00d14bd3c2a96c66cca11e3a893518157d144c23742ad61afd964a78`
  - `manifest.json` file SHA256: `82a4e0e9dd1364cc9e385f661610a0d781ae3a9df6ad5e29c4c0931491a52aba`
  - `materialization_receipt.json` SHA256: `00c0199e6f3e57149ae68ac99be680149be5c35a40c294a86a256df62d95ce84`
  - `manifest_hash`: `f7e6c278b0b503791d8f679d4ac19b7f856a517978c34e7015da7b4980c5cd7c`
  - `content_digest`: `568d7d3c64c78b973889dc27e49ffc3d57100f8cb4e78410792c585c6e628b04`
  - `stable_id_set_digest`: `f1c0095e01eb2fe286823ceb7da6deeb0159e81d9f7329ad095297be689b8f8b`
- Deferred boundaries remain unresolved and unchanged:
  - Asset review required separately: `MKA-MC-00014`, `MKA-MC-00121`
  - Alias rebinding requires separate decision: `MKA-MC-00045`
  - Payload/content approval remains separate: `MKA-MC-00014`
- Activation trust rule: any future Stable Record V2 activation must externally pin the reviewed Authority `manifest_hash` / complete package identity. `content_digest` alone is not sufficient activation trust.
- Independent acceptance: pending. This decision records successful materialization and implementer-side verification only; it does not substitute for independent review.
- Consequence: Stable Record V2 must not be activated, row_v1 must not be retired, and production re-index must not occur under this decision.
- Handoff: `docs/collaboration/HANDOFF_WP0-4b-M3E_2026-08-26.md`

## DEC-20260826-02 — M3E independently accepted

- Status: accepted
- Date: 2026-08-26
- Scope: WP0.4b-M3E Stable Record Authority Materialization independent acceptance
- Review baseline: `731fdcef3713a9b00ccca943e68ef46165d4c39d`
- Independent review verdict: `PASS_WITH_NONBLOCKING_FINDINGS`
- Governance adjudication: `M3E_INDEPENDENT_ACCEPTANCE=APPROVED`
- Review record: `docs/collaboration/REVIEW_WP0-4b-M3E_2026-08-26.md`

### Accepted Authority identity

- `stable_record_registry.csv` SHA256:
  `307a3e7f00d14bd3c2a96c66cca11e3a893518157d144c23742ad61afd964a78`
- `manifest.json` SHA256:
  `82a4e0e9dd1364cc9e385f661610a0d781ae3a9df6ad5e29c4c0931491a52aba`
- `materialization_receipt.json` SHA256:
  `00c0199e6f3e57149ae68ac99be680149be5c35a40c294a86a256df62d95ce84`
- `manifest_hash`:
  `f7e6c278b0b503791d8f679d4ac19b7f856a517978c34e7015da7b4980c5cd7c`
- `content_digest`:
  `568d7d3c64c78b973889dc27e49ffc3d57100f8cb4e78410792c585c6e628b04`
- `stable_id_set_digest`:
  `f1c0095e01eb2fe286823ceb7da6deeb0159e81d9f7329ad095297be689b8f8b`

### Accepted semantics

- 121 unique Stable Record IDs
- 120 approved identity continuations
- 1 approved new identity: `MKA-MC-00121`
- Confidence distribution: HIGH 105 / MEDIUM 15 / NEW 1
- row_v1 remains retained and not retired
- Stable Record V2 remains not activated

### Deferred boundaries retained

- Asset review: `MKA-MC-00014`, `MKA-MC-00121`
- Alias decision: `MKA-MC-00045`
- Payload/content change not approved: `MKA-MC-00014`

Identity acceptance does not approve asset, URL, alias, payload, or content changes.

### Nonblocking findings carried forward

- F1: `validate_authority` does not independently re-derive `payload_change_status` from `special_flags`; hardening backlog.
- F2: `load_authority_package` does not re-run row-level authority validation; must be addressed by the future Activation WP or an equivalent fail-closed activation gate.
- F3: unexpected fourth files are outside the current manifest-driven loader contract; future directory-enumerating consumers must enforce an exact-file-set gate.
- F4: M3D-R1 independent-review verdict is not Git-tracked; preserve as a governance-record gap and do not rewrite history.
- F5: M3E reviewer shares Claude Code lineage with the earlier M3D-R1 implementer; this review must not be used as an independent M3D-R1 review. M3E independence remains accepted.

### Governance boundary

- `AUTHORITY_MATERIALIZED=YES`
- `M3E_INDEPENDENT_ACCEPTANCE=APPROVED`
- `STABLE_RECORD_V2_ACTIVATED=NO`
- `ROW_V1_RETIRED=NO`
- `PRODUCTION_REINDEX_AUTHORIZED=NO`

This decision does not authorize Stable Record V2 activation, row_v1 retirement, Vault/content-index mutation, alias rebinding, asset approval, payload/content approval, or production re-index.

Any future Stable Record V2 activation must externally pin the reviewed Authority `manifest_hash` / complete package identity. `content_digest` alone is not sufficient activation trust.

## DEC-20260826-03 — M3E integration independently accepted

### Decision

The M3E governance integration candidate has completed independent integration review and is accepted.

- Candidate: `49a2b038f7ac58f34d5af1cf731d911b4630909e`
- Integration baseline: `f4988e346bb1dc5c9534feafbea81c45a2a958b0`
- Accepted M3E source: `5673cf766027454efc98be3ae19fac5ba2742f31`
- Integration merge commit: `eebf5344e0fd0a0aff86e6bd5596df1e53ecd6c5`
- Independent review record: `docs/collaboration/REVIEW_WP0-4b-M3E-INTEGRATION_2026-08-26.md`
- Independent review verdict: `PASS_WITH_NONBLOCKING_FINDINGS`
- Governance adjudication: `M3E_INTEGRATION_ACCEPTANCE=APPROVED`

### Integration verification

Independent review confirmed:

- baseline-to-candidate changes are limited to the expected governance files;
- `NON_GOVERNANCE_DRIFT=NONE`;
- accepted-source `DECISIONS.md`, M3E handoff, and M3E independent-review record remain identical;
- merge commit structure and both parents are correct;
- integration conflict resolution correctly preserved the active integration task lock;
- formal Authority package identity and accepted SHA256 pins remain unchanged;
- no application code, test, production configuration, Authority, Vault/content-index, alias, asset, or payload mutation occurred.

### Review findings

#### IR1 — CURRENT_WORK workflow state stale

Classification: `NONBLOCKING`

The independent reviewer found that `CURRENT_WORK.md` still described the integration-verification commit/push/review steps as pending even though they had completed.

Disposition: correct the collaboration state in the same acceptance-governance update.

#### IR2 — AppleDouble metadata under shared Git refs

Classification: `INFORMATIONAL`

Shared `.git/refs/**/._*` macOS metadata can cause `git fsck` to report `badRefName` / `badRefContent`.

The metadata is outside the candidate tree and did not affect ancestry, object resolution, integration diff, or worktree cleanliness.

Disposition: separate repository-hygiene backlog; do not repair inside this integration WP.

### Existing findings carried forward

- F1 remains a nonblocking Authority-validation hardening backlog item.
- F2 remains open for the future Activation WP and requires row-level invariant validation or an equivalent fail-closed activation gate.
- F3 remains open for activation design where directory-enumerating consumers must enforce an exact-file-set gate.
- F4 remains a historical governance-record gap and must not be repaired by rewriting history.
- F5 reviewer-lineage disclosure remains preserved; M3E review must not be reused as an independent M3D-R1 review.
- Existing build-content-index lineage finding remains a hard blocker for production re-index.

### Governance boundary

- `M3E_INTEGRATION_INDEPENDENT_REVIEW=PASS_WITH_NONBLOCKING_FINDINGS`
- `M3E_INTEGRATION_ACCEPTANCE=APPROVED`
- `MAIN_UPDATE_AUTHORIZED=NO`
- `STABLE_RECORD_V2_ACTIVATED=NO`
- `ROW_V1_RETIRED=NO`
- `PRODUCTION_REINDEX_AUTHORIZED=NO`

Future Stable Record V2 activation must continue to externally bind the reviewed Authority:

`manifest_hash=f7e6c278b0b503791d8f679d4ac19b7f856a517978c34e7015da7b4980c5cd7c`

`content_digest` alone is not sufficient as an activation trust anchor.

This decision does not authorize updating `main`, Stable Record V2 activation, row_v1 retirement, Authority mutation, Vault/content-index mutation, alias/asset/payload mutation, or production re-index.

## DEC-20260826-04 — Stable Shadow + Content Index Lineage + Search Taxonomy accepted

### Decision

The Stable Record Shadow, Content Index Lineage Gate, Search Taxonomy v1, Golden/Negative Search
Evaluation v1 and Consolidated Blocker Remediation R1 candidate has completed independent delta
review and is accepted as a main integration candidate.

- Reviewed candidate: `472f5c389d57f91d35b50db8bdd0d96aa64ddf63`
- Previously blocked candidate: `8af73821a237253af6617c5fbf81605b76349b10`
- Integration baseline (GitHub `main` at preparation time): `dd215c6b4199c221288720d6d702eff0c15ed0a9`
- Integration branch: `codex/integrate/stable-shadow-search-taxonomy`
- Independent review record: `docs/collaboration/REVIEW_SEARCH_TAXONOMY_R1_2026-08-26.md`
- Independent review verdict: `PASS_WITH_NONBLOCKING_FINDINGS`
- Blocking findings: `0`
- Reviewer edited candidate: `NO`

### Why this candidate could be merged without conflict

`origin/main` is a direct ancestor of the reviewed candidate, so the merge could have
fast-forwarded. `--no-ff` was used deliberately so the integration event records both parents. The
merge result tree is byte-identical to the reviewed candidate's tree, which is the proof that
integration introduced no content change of its own.

### Closed by this milestone

- `B1` short-CJK taxonomy alias substring false positive, including the semantic inversion
  `停業後重新開店的品牌` → `sales_category_lv2=已關閉`.
- `B2` `stable_record_id: null` leaking into the second governed Vault Markdown writer.
- `N1` explicit-field fragment fall-through.
- `N2` blocked evaluation asserts `result_count == 0` directly rather than by inference.
- `N3` a Negative regression fails `evaluate-search` with a non-zero exit code.
- `N5` re-index lineage prerequisite documented — documentation only, not an authorization.

### Accepted nonblocking backlog

Recorded, not fixed. Each was verified to behave identically on the frozen candidate `8af7382`, so
none is a regression introduced by this milestone.

1. Runtime catalog-path CJK substring matching — the boundary rule covers the Authority scan only.
2. Fragment-removal artificial boundary edge case.
3. Explicit constraint whitespace truncation — fails silently empty rather than fail-closed.
4. LV1 canonical ambiguity — product/Authority semantics decision.
5. Ingestion data-quality WP for the two disputed content tags.
6. Real-writer `B2` regression test hardening — the fix is correct but unguarded; removing it from
   both writers leaves the whole test module passing.
7. Slack taxonomy activation.
8. Golden/Negative dataset expansion beyond the 44-case v1 smoke set.

### Governance boundary

- `SEARCH_TAXONOMY_R1_INDEPENDENT_REVIEW=PASS_WITH_NONBLOCKING_FINDINGS`
- `SEARCH_TAXONOMY_R1_ACCEPTANCE=APPROVED`
- `MAIN_READY=YES`
- `MAIN_UPDATE_AUTHORIZED=NO`
- `STABLE_RECORD_V2_ACTIVATED=NO`
- `ROW_V1_RETIRED=NO`
- `PRODUCTION_REINDEX_AUTHORIZED=NO`
- `PRODUCTION_REINDEX_RUN=NO`
- `SLACK_TAXONOMY_ACTIVATED=NO`

`MAIN_READY=YES` states that the integration candidate is prepared and verified. It is not an
authorization to update `main`; that remains an explicit, separate human decision.

This decision does not authorize updating `main`, Stable Record V2 activation, row_v1 retirement,
Authority mutation, Vault/content-index mutation, production sync, production re-index, or Slack
taxonomy activation.

## DEC-20260827-01 — Faceted Search MVP merges to main first; taxonomy wiring adapts to it

Two work packages independently added the same two `SlackConfig` fields, each gated by its own
flag:

| Branch | Flag | Shared fields |
| --- | --- | --- |
| `codex/impl/slack-faceted-search-mvp` (pushed, remediated) | `enable_faceted_search` | `search_taxonomy_workbook` + `search_taxonomy_sha256` |
| `codex/impl/slack-search-taxonomy-uat` (uncommitted, separate worktree) | `enable_search_taxonomy` | the same two fields |

This is not only a textual merge conflict. Two flags would each gate loading the *same* pinned
Search Taxonomy Authority, which is an incoherent contract: there is one Authority, and whether it
is loaded should not depend on which of two unrelated features happens to be on.

### Decision

**Faceted Search MVP is the integration order's first branch.** The Search Taxonomy Slack wiring WP
adapts to whatever `SlackConfig` shape lands with it, rather than the reverse.

Rationale: the faceted-search branch has completed a full review cycle (Codex R1
CHANGES_REQUESTED → six findings remediated → re-review pending) and is committed and pushed. The
taxonomy wiring WP is still uncommitted on its own branch, so adapting it costs no completed review
work. Merging in the other order would force already-reviewed code to be rewritten, discarding a
review.

### Consequences for the taxonomy wiring WP

Recorded here so whoever resumes that WP is not surprised. Its worktree was **not** modified by this
decision — its uncommitted changes are untouched.

- `search_taxonomy_workbook` and `search_taxonomy_sha256` will already exist in `SlackConfig`, along
  with their both-or-neither validation. That WP must consume the existing fields, not redeclare
  them.
- `enable_search_taxonomy` remains its own flag and stays independent; the intended end state is
  **one taxonomy pin consumed by two independent feature flags**, so neither feature's flag gates
  the other's.
- The taxonomy load itself should happen once at startup regardless of which flag or flags are on.

### Governance boundary

```text
INTEGRATION_ORDER_DECIDED=YES
CODEX_RE_REVIEW=PENDING
MAIN_UPDATE_AUTHORIZED=NO
MAIN_UPDATED=NO
UAT_ACTIVATION_AUTHORIZED=NO
UAT_ACTIVATED=NO
PRODUCTION_ACTIVATED=NO
```

This decision settles **order only**. It is not an authorization to update `main`, and it does not
bypass the outstanding gate: Codex re-review of `3a7648f..1afa27a` must pass first. Promotion to
`main` remains an explicit, separate human decision, as does UAT activation.
