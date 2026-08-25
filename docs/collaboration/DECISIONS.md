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
