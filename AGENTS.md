# AGENTS.md instructions

Use Traditional Chinese for explanations unless the repository or task requires English.

For non-trivial engineering work, prefer caution over speed.

Before changing code:
- State assumptions.
- Ask when requirements are ambiguous.
- Read relevant callers, exports, shared utilities, and tests.
- Define what “done” means.

When changing code:
- Make the smallest safe change.
- Do not refactor unrelated code.
- Do not add speculative features.
- Match the existing codebase style.

Git discipline (multi-agent repo — 2026-07-11 教訓,詳見 docs/governance/LESSONS.md):
- 本 repo 可能有多個 agent 先後操作。commit 前必看 `git status` 與
  `git diff --cached --name-only`;staged 清單含自己任務以外的檔案 → 停下確認,不要提交。
- `git add` 永遠明確列檔/目錄;禁止 `git add -A` 或 `git add .`。
- 不要對已存在的 commit 做歷史重寫(rebase/amend 他人 commit);
  發現 git 狀態與你的認知不符(commit 消失、分支被 merge)→ 先回報,通常是
  另一端已處理,你的視圖過時了——不要「修復」它。

Project-specific rules:
- 第一階段不得接正式公司資料，只能使用 `data/mock_vault/` 或測試臨時資料。
- 第一階段不得要求外部 LLM 或 API key；`generation` 必須維持可離線測試。
- 新增 ingestion 或 metadata 行為前，先讀 `models.py`、`ingestion.py` 與相關 tests。
- 回答輸出必須保留 citations、metadata 與資料新鮮度提醒。
- `archived`、`deprecated`、`draft` 來源必須提醒不可直接對外引用。
- 模組邊界維持為 ingestion、chunking、indexing、retrieval、reranking、generation、evaluation。
- Agentic RAG 功能只能作為 orchestration 層包裝既有 retrieval / generation，不得繞過 citations、metadata、freshness note 或 status warning。
- Excel 來源中 `restricted_customer` 與 `handle_mapping` 只能作 governance / normalization，不得進一般向量檢索或一般 RAG citation。
- Excel preview 只能輸出人工審核用 JSON / Markdown，不得直接覆蓋 Obsidian Vault 或建立正式 content index。
- 對外用途必須檢查 `can_quote_externally` 與 `allowed_exposure_channels`；`pending_metric` 不得直接用於新聞稿、官網、廣告或 Saleskit。
- `restricted_customer` 只要在不可公開客戶 sheet 有有效資料列就應進 denylist preview，不以 NDA 欄位作為匯入條件。
- 同一品牌或 handle 的多筆 `merchant_case` 預設是多次訪談紀錄，不得自動 dedupe、合併或覆蓋；只有指定欄位全部相同時才列為人工 review item。
- Excel preview count 必須先通過正式 workbook baseline check，才能進入人工審核或後續 Vault 匯入規劃。

When verifying:
- Run relevant tests, lint, or type checks when available.
- Do not claim tests passed unless they were actually run.
- If checks were skipped, say what was skipped and why.

Final response format:
- Summary
- Files changed
- Verification
- Not verified
- Risks or follow-ups

延伸制度文件（按需查閱，不必每次全讀）：
- 上面的 project-specific rules 是不可違反的底線；更完整的 hard constraints 與收納原則見 `docs/governance/B_AGENT_RULES_REWRITE.md`。
- governance / review 判斷：`docs/governance/D_JUDGMENT_RUBRICS.md`、`docs/governance/I_GOVERNANCE_RISK_REVIEW.md`。
- 派工 / 選模型 / 升降級：`docs/governance/C_MODEL_ROUTING_PLAYBOOK.md`、`docs/governance/E_DELEGATION_PROMPTS.md`。
- 補強 validator（已完成 2026-07-10）：`docs/specs/J_REVIEW_DECISIONS_VALIDATION_SPEC.md`。
- 實作 apply-review-decisions（已完成 2026-07-10）：`docs/specs/K_APPLY_REVIEW_DECISIONS_PREVIEW_SPEC.md`。
- Obsidian sync（已完成 2026-07-10,首批 13 篇已同步）：`docs/specs/N_OBSIDIAN_SYNC_SPEC.md`。
- Content index 建置（已完成 2026-07-10,ROADMAP Stage 1）：`docs/specs/O_CONTENT_INDEX_SPEC.md`。
- Query gating（已完成 2026-07-10,ROADMAP Stage 2）：`docs/specs/P_QUERY_GATING_SPEC.md`。
- 外部 LLM 接入（下一個 sprint,ROADMAP Stage 3,雙鑰閘門設計）：`docs/specs/Q_LLM_INTEGRATION_SPEC.md`。
- retrieval / citation 精準度：`docs/governance/L_RETRIEVAL_CITATION_ACCURACY_REVIEW.md`。
- 修改制度文件本身、踩坑教訓寫回：`docs/governance/F_MAINTENANCE_PROTOCOL.md`、`docs/governance/LESSONS.md`。
- 接手新 session、不知從何開始：`docs/governance/G_LETTER_TO_FUTURE_SESSIONS.md`、`docs/governance/Z_ONE_PAGE_SUMMARY.md`。
- 專案階段路線圖(終點=Slack 對話取用;各階段依賴與跳關禁令)：`docs/governance/ROADMAP.md`。
- enum / governance 規則的 canonical source 是 code；文件與 code 不一致時以 code 為準並回報。
