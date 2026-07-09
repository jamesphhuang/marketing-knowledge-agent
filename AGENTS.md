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
