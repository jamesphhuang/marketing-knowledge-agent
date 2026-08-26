# Cross-tool collaboration start here

本文件定義 Codex、Claude Code、兩台電腦與手機之間的共同工作流程。

## Source of truth

- Git repo 是程式碼、規格、正式決策、驗證結果與交接狀態的唯一正式來源。
- AI 對話紀錄不是正式來源；重要結論必須整理後寫回 repo。
- 新建的 Obsidian 協作 Vault 只提供手機與電腦的人讀摘要，不取代 Git。
- 專案既有的 `obsidian_vault/` 是受治理的知識內容，不得放入協作日誌。

## Start-of-session checklist

每個新 session 依序執行：

1. 執行 `git status --short --branch`，確認所在分支與既有變更。
2. 工作樹乾淨時執行 `git pull --ff-only`；若不乾淨，先釐清變更所有權，不得直接 pull、stash、discard 或覆寫。
3. 讀取 `AGENTS.md`、本文件與 `CURRENT_WORK.md`。
4. 確認是否已有 active task lock；若鎖定者不是自己，保持唯讀並向使用者確認。
5. 依任務按需讀取 callers、exports、shared utilities、tests、specs 與 governance 文件。
6. 在改檔前寫明假設、完成標準，並更新 `CURRENT_WORK.md` 取得任務鎖定。

## Task roles and lock

- Codex 與 Claude Code 可依任務互換角色，不設固定分工。
- 同一時間只能有一個實作者；審查者不得在同一工作樹同時修改檔案。
- 任務鎖定至少記錄：任務、實作者、角色、分支、baseline commit、預計範圍與開始時間。
- 責任轉移前，原實作者必須留下 handoff；新實作者確認 Git 狀態與 baseline 後才能接手。
- 不得自行判定 active lock 已失效。若鎖定狀態與實際 Git 狀態不符，停止並請使用者裁決。

## Required update points

正式紀錄在三個時點更新：

1. 開始實作前：登記任務鎖定、目標與範圍。
2. 發生重大或不可逆決策時：立即追加至 `DECISIONS.md`。
3. 任務完成或中斷時：記錄完成、進行中、未開始、驗證、下一步與待決問題，然後釋放或轉移任務鎖定。

Fast Lane v1 起，未來任務的主要 lifecycle 紀錄收斂為 `START`、`REVIEW_READY`、
`CLOSED`：開始時取得 lock；implementation candidate 完成時記錄實際變更、驗證、風險與
independent review pending，並保留 lock；review / adjudication / integration 完成後才關閉或
移交。逐次 `stage`、`status`、`diff`、`grep`、commit、push 的 deterministic 細節不再逐項
永久搬進 `CURRENT_WORK.md`，但重大決策仍須即時追加 `DECISIONS.md`，需要審查的
machine-readable evidence 仍須保存。此規則不重寫既有歷史。

## Obsidian collaboration vault

另建獨立 Vault，例如 `MKA Collaboration`，以 Obsidian Sync 的標準加密同步兩台電腦與手機；不要同時交由其他雲端服務同步。

建議結構：

```text
MKA Collaboration/
├── Now.md
├── Daily/
├── Decisions/
├── Inbox/
└── Archive/
```

- `Now.md`：Git `CURRENT_WORK.md` 的人讀摘要；有衝突時以 Git 為準。
- `Daily/`：去敏工作摘要，不複製完整 AI 對話。
- `Decisions/`：方便手機閱讀的正式決策摘要；正式版本仍在 Git。
- `Inbox/`：手機臨時想法，整理前不得視為需求或決策。
- `Archive/`：`Daily/` 筆記滿 90 天後移入；仍可搜尋、開啟與還原，不是刪除。

協作 Vault 不得含客戶名稱或資料、原始 Excel、API key、token、憑證、完整 AI 對話，或任何不應同步到三台裝置的內容。

## Finish safely

1. 執行相關測試、lint 或 type checks；未執行項目必須說明。
2. 檢查 `git status`、`git diff` 與 `git diff --cached --name-only`。
3. 只 stage 本任務明確涉及的檔案；禁止 `git add .` 或 `git add -A`。
4. 更新 `CURRENT_WORK.md`；需要交接時使用 `HANDOFF_TEMPLATE.md`。
5. 重要決策追加到 `DECISIONS.md`，並在 Obsidian 留下去敏摘要。
