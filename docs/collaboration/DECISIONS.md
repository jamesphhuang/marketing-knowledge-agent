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
