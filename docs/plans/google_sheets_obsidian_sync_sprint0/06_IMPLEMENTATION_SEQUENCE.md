# Recommended Implementation Sequence

## Sequencing principle

先固定低風險contracts與offline guard，再建立治理安全邊界，接著完成link/capture deterministic transformations，最後才定Release composition與integration。任何Phase exit未通過，不進下一Phase。

## Phase 1 — Safety base and leaf contracts

**Work Packages**：WP0、WP1、WP4、WP9、WP10、WP11。

**Entry criteria**：

- Branch仍為`codex/plan/google-sheets-obsidian-sync-sprint0`或後續明確implementation branch，base可追到frozen Audit commit。
- Decisions 1–11沒有被重開；Sprint 0 exclusions仍有效。
- 既有untracked/runtime/sensitive items不在工作範圍。

**Exit criteria**：

- Offline harness能攔network/production persistence。
- CellData完整表達required Google fields且reader沒有write surface。
- BRD/MREC/MET與Content Asset key identity validator不依row/path/URL。
- CapturedContent/CapturePolicy可表達所有required statuses與Primary/Evidence authority；HTML normalizer是獨立pure transformation。
- 所有Phase 1 per-WP tests通過；legacy models/CLI無變更。

**Stop on**：需要Google SDK/credential、修改`DocumentMetadata`大量callers、HTTP implementation或AST ID。

## Phase 2 — Google source semantics and governance boundary

**Work Packages**：WP2、WP3、WP5。

**Entry criteria**：Phase 1 exit通過；CellData與canonical entity schemas已review stable。

**Exit criteria**：

- source fingerprint deterministic且不含web/capture freshness。
- formula/merge/no-fill-down與lineage contract通過。
- oral-only只能產`ExcludedSourceRef`，所有debug/serialization/sentinel scans為零。
- Legacy Excel flow仍可執行原tests，不被new contract接線。

**Stop on**：formula string成正文、非merge fill-down、oral-only payload進任何persistence-ready object、hash domain混合。

## Phase 3 — Link extraction, safety, and asset cardinality

**Work Packages**：WP6、WP7、WP8。

**Entry criteria**：WP1/3/4 stable；URL policy只允許offline syntax/literal-IP判斷。

**Exit criteria**：

- 四層link priority完整收集且保留provenance。
- URL validator涵蓋attack corpus、secret redaction及canonicalization idempotency。
- 0/1/2+ distinct URL結果符合Decision 8；asset key只為`<MREC>:<asset_type>`。
- 不發HTTP、不做DNS、不挑winner、不拆asset。

**Stop on**：unsafe URL完整值進log/error、resolver自動選URL、row/URL成identity或新增AST。

## Phase 4 — Captured-content versioning and chunk metadata contracts

**Work Packages**：WP12、WP13。

**Entry criteria**：WP7、WP9、WP10、WP11完成；synthetic HTML fixture已通過內容審查。

**Exit criteria**：

- HTML normalization穩定保留meaningful structure並移除boilerplate/script。
- body hash與source fingerprint完全分離；parser version/revision/LKG規則通過。
- 注入synthetic spans所建立的chunk metadata/identity具有stable parent/authority/freshness lineage；blocked/metadata-only不產chunk records。
- 無Markdown、HTTP、SQLite/vector接線。
- Production content splitting algorithm明確延後，不在本Phase決定chunk-size、overlap或section heuristics。

**Stop on**：raw HTML長期輸出、hash含timestamp/URL、stale假revision、chunk ID只靠ordinal或跨parent。

## Phase 5 — Release and review contracts

**Work Packages**：WP14、WP15。

**Entry criteria**：WP14的WP2/WP4/WP8/WP9/WP10/WP12/WP13依賴完成；WP15的WP5/WP8依賴完成。

**Exit criteria**：

- Release manifest固定metadata batch、source fingerprint、capture revisions/stale lineage及sibling artifact refs。
- manifest沒有body/raw HTML/secret且無activate/write method。
- redacted preview可追查但無oral/unsafe/raw payload；JSON/Markdown deterministic。
- Negative contract拒絕Markdown-derived Official input與partial composition。
- WP14與WP15可平行，兩者只在WP16匯合。

**Stop on**：component可獨立active、preview需raw content、manifest混合batch/parser/policy或含敏感欄位。

## Phase 6 — Sprint 0 integration gate

**Work Packages**：WP16。

**Entry criteria**：WP0–WP15全部acceptance criteria與per-WP tests通過；沒有open blocking issue。

**Exit criteria**：

- Synthetic end-to-end contract兩次replay deterministic。
- 必要positive/negative cases全覆蓋；oral sentinel/network/production write掃描為零。
- Safe existing regression suite通過；未執行項目明確列出。
- Readiness checklist逐項簽核，才允許進Sprint 1 planning/implementation。

**Stop on**：任何skip/bypass、live dependency、partial manifest、Markdown reparse或legacy production mutation。

## Recommended review/PR order

即使多人平行，建議merge/review順序固定為：

1. WP0
2. WP1
3. WP4、WP9、WP10、WP11（可獨立review）
4. WP2、WP3
5. WP5
6. WP6
7. WP7
8. WP8
9. WP12
10. WP13
11. WP14、WP15（可獨立review）
12. WP16

不建議第一包碰`content_index.py`、`obsidian_sync.py`、`cli.py`或任何migration；它們不是Sprint 0 exit的必要條件。
