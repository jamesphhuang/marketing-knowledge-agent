# Marketing Knowledge Agent

Marketing Knowledge Agent 是一個離線 Python RAG prototype，用來讀取 Obsidian Markdown Vault，讓使用者以自然語言搜尋公司 marketing 資源，並取得 citations、metadata 與資料新鮮度提醒。

第一階段只使用 mock data，不接正式公司資料、不呼叫外部 LLM，也不建立 Web UI。

## 功能

- 讀取 Markdown 檔案與 YAML frontmatter。
- 驗證 marketing metadata schema。
- 支援 `source_type`、`product`、`industry`、`topic`、`funnel_stage`、`status`、`publish_date` 等欄位。
- 使用 SQLite FTS5 做全文搜尋。
- 使用 deterministic local embedding 做 prototype 向量搜尋。
- 支援 metadata filtering。
- mock RAG answer 必須附 citations。
- 若來源 status 是 `archived`、`deprecated`、`draft`，回答會提醒不可直接對外引用。
- `agent-ask` 提供離線 Agentic-lite 流程：query analysis、plan、internal tool calls、reflection 與 trace。
- Metadata schema v0.2 支援 Excel 來源的 merchant case、public metric、pending metric、restricted customer denylist 與 handle mapping。
- Restricted customer 與 handle mapping 是治理/正規化資料，不進一般向量檢索 citation。
- 內建 mock vault 與 evaluation cases。

## 安裝

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## 使用方式

建立 index：

```bash
.venv/bin/mka ingest --vault data/mock_vault --db .mka/index.sqlite
```

全文與向量混合搜尋：

```bash
.venv/bin/mka search "pricing case studies" --source-type showcase --product product-a
```

產生 mock RAG 回答：

```bash
.venv/bin/mka ask "有哪些適合製造業漏斗中段的素材？" --industry manufacturing --funnel-stage consideration
```

使用 Agentic-lite 問答並顯示 trace：

```bash
.venv/bin/mka agent-ask "比較 Product A 製造業 pricing case study 與 ROI blog" --product product-a --show-trace
```

依曝光渠道篩選 public metric：

```bash
.venv/bin/mka search "導入成效" --record-type public_metric --exposure-channel saleskits --can-quote-externally
```

從 Excel 產生人工審核 preview：

```bash
.venv/bin/mka excel-preview \
  --workbook "MKT 內容產出資料庫_店家_夥伴案例_對外數據.xlsx" \
  --output reports/excel_preview \
  --captured-date 2026-07-01
```

執行內建 evaluation：

```bash
.venv/bin/mka evaluate
```

## 使用真實官網 Sample Data

`data/sample_vault/` 目前放入 SHOPLINE TW 主站公開官網內容摘要，來源包含：

- `https://shopline.tw/`
- `https://shopline.tw/about/sitemap`
- `https://shopline.tw/about`
- `https://shopline.tw/about/pricing`

這批資料只涵蓋第一階段的 `website` 來源類型。YouTube 逐字稿與設計素材說明保留到第二階段。

```bash
.venv/bin/mka ingest --vault data/sample_vault --db .mka/sample.sqlite
.venv/bin/mka ask "SHOPLINE 有哪些全通路開店工具？" --source-type website --db .mka/sample.sqlite
.venv/bin/mka ask "SHOPLINE 方案費用有哪些？" --topic pricing --db .mka/sample.sqlite
.venv/bin/mka agent-ask "比較 SHOPLINE 主站首頁與方案頁的內容重點" --source-type website --db .mka/sample.sqlite --show-trace
```

官網頁面未顯示原始發布日，因此 sample vault 的 `publish_date` 與 `updated_date` 使用本次擷取日期。正式導入時建議新增獨立的 `captured_date` 欄位，避免與內容原始發布日期混淆。

執行測試：

```bash
.venv/bin/pytest
```

## Metadata Schema

每個 Markdown 檔案需包含 YAML frontmatter：

```markdown
---
title: "Product A 製造業 ROI 定價指南"
source_type: blog
product: [product-a]
industry: [manufacturing]
topic: [pricing, roi]
funnel_stage: [consideration]
status: published
publish_date: 2026-01-15
updated_date: 2026-02-01
canonical_url: "https://example.com/blog/product-a-roi-pricing"
author: "Marketing Team"
---

Markdown body...
```

支援的 `source_type`：

- `blog`
- `showcase`
- `social`
- `podcast`
- `website`
- `youtube`
- `design`
- `database`

支援的 `funnel_stage`：

- `awareness`
- `consideration`
- `decision`
- `retention`
- `advocacy`

支援的 `status`：

- `published`
- `draft`
- `archived`
- `deprecated`

`archived`、`deprecated`、`draft` 會被視為不可直接對外引用。

### Metadata Schema v0.2

Excel 來源新增共用治理欄位：

- `record_type`
- `brand_name`
- `merchant_handle`
- `merchant_status`
- `interview_year`
- `sales_category_lv1`
- `sales_category_lv2`
- `industry_subcategory`
- `content_tags`
- `metric_type`
- `metric_name`
- `claim_statement`
- `claim_status`
- `data_classification`
- `can_quote_externally`
- `allowed_exposure_channels`
- `nda_signed`
- `nda_uploaded_salesforce`
- `restricted_reason`
- `submitted_by`
- `captured_date`
- `last_reviewed`
- `review_due_date`
- `owner`
- `source_sheet`
- `source_row`

支援的 `record_type`：

- `content_asset`：既有 Markdown / Obsidian 內容。
- `merchant_case`：商家 / 夥伴案例索引，可進 RAG。
- `public_metric`：已核准對外數據，可進 RAG，但需依 `allowed_exposure_channels` 控管用途。
- `pending_metric`：待確認數據，不可直接對外引用。
- `restricted_customer`：不可公開客戶名單，只能做 denylist / policy check，不進一般 RAG citation。
- `handle_mapping`：商家 identity mapping，只能做 normalization / enrichment，不進一般 RAG citation。

支援的 `data_classification`：

- `public`
- `internal`
- `restricted`

支援的 `claim_status`：

- `approved`
- `pending_review`
- `draft`
- `deprecated`

支援的 `allowed_exposure_channels`：

- `press_release`
- `owned_media`
- `saleskits`
- `verbal_briefing`
- `speaking_deck`
- `website_recruiting`
- `ads`

Citation v0.2 必須包含：

- `record_type`
- `data_classification`
- `can_quote_externally`
- `source_sheet`
- `source_row`
- `last_reviewed` 或 `captured_date`

### Blog 與 Showcase 的資料關係

若 showcase 是 blog 底下的內容分類，建議用兩層 metadata 表達：

```yaml
source_type: showcase
content_category: showcase
parent_source_type: blog
```

第一階段仍保留 `source_type: showcase`，避免破壞既有資料與 filter。後續若要正式統一 taxonomy，可以逐步遷移成：

```yaml
source_type: blog
content_category: showcase
```

重點是不要只靠資料夾路徑推論關係，應在 frontmatter 中明確保留這個連結，方便搜尋、filter 與後續 migration。

## 驗證 Vault Metadata

匯入真實資料前，先跑 validate：

```bash
.venv/bin/mka validate --vault data/sample_vault
.venv/bin/mka validate --vault data/mock_vault
```

`validate` 會列出：

- 缺少 YAML frontmatter 的 Markdown。
- 缺少必填欄位或 enum / 日期格式不合法的 metadata。
- 被略過的系統檔、隱藏檔與非 Markdown 檔。
- `source_type=showcase` 但未補 `parent_source_type=blog` / `content_category=showcase` 的提醒。

## 產生 Metadata Backfill 候選報告

對於缺少 frontmatter 的真實 Markdown，先產生審核報告，不要直接寫回原檔：

```bash
.venv/bin/mka backfill-report --vault data/mock_vault --output reports/metadata_backfill_candidates.md
```

報告會為缺 frontmatter 的 `.md` 產生候選欄位：

- `title`：取第一個 H1，沒有 H1 則取檔名。
- `source_type`：由第一層資料夾推斷。
- `content_category` / `parent_source_type`：showcase 會補 `showcase` / `blog`。
- `product`、`industry`、`topic`、`funnel_stage`：用關鍵字做保守候選。
- `status`：預設 `draft`，避免未審核資料被對外引用。
- `publish_date`、`updated_date`、`canonical_url`：保留 `TODO`，必須人工確認。

這個命令只寫報告，不會修改來源 Markdown。

## Excel 來源與治理規劃

Excel ingestion 不直接覆蓋 Obsidian Markdown Vault。第一步只產生 normalized JSON / Markdown preview，保留每列的 `source_sheet` 與 `source_row`，由人審核後才進正式 Vault 或 SQLite index。

目前規劃五張 sheet：

| Sheet | record_type | index role | 說明 |
| --- | --- | --- | --- |
| `商家夥伴案例資料庫` | `merchant_case` | `content_index` | 可進 RAG；商家狀態或備註若含停止營運、暫時下架、已關店、轉走、結束合作等詞，會產生治理 warning。 |
| `「不可公開」客戶名單` | `restricted_customer` | `governance_table` | 不進向量檢索，只做 exact / normalized denylist check。 |
| `「可公開」對外數據` | `public_metric` | `content_index` | 可進 RAG；新聞稿、自媒體、Saleskits、口頭說明、演講簡報、官網 / 招募網站、廣告會轉成 `allowed_exposure_channels`。 |
| `待確認數據` | `pending_metric` | `internal_inventory` | 不可對外引用，只能作內部盤點或缺口提醒。 |
| `handle 比對` | `handle_mapping` | `normalization_table` | 不進一般問答 citation，只用於補齊 `brand_name`、`sales_category_lv1`、`sales_category_lv2`。 |

治理規則：

1. `restricted_customer` 不進向量檢索，只作 denylist / governance check。
2. `pending_metric` 不可對外引用。
3. `public_metric` 必須依 `allowed_exposure_channels` 控管使用場景。
4. `merchant_case` 若商家狀態為已關店、轉走、結束合作、停止營運、暫時下架等，回答要加 warning。
5. `can_quote_externally=false` 時，任何對外文案、新聞稿、官網、廣告、Saleskit 都不得直接引用。
6. 命中 restricted denylist 時，回答需避免輸出敏感細節，並提示需要人工確認。
7. 所有 Excel-derived citation 必須保留 `source_sheet` 與 `source_row`，方便追溯。

Excel normalization 行為：

- `-`、空白、`null` 不會被當成有效素材。
- `審核中`、`暫時下架`、`已下架` 不會被當成有效文章 / 影片 / Podcast / 新聞標題。
- 若 merchant case 的文章、影片、Podcast、新聞全部沒有有效素材，preview 會標示 `no_valid_content_asset=true` 與 `can_enter_content_index=false`；資料仍會保留供人工審核。
- 同一 `brand_name` 或 `merchant_handle` 可以有多筆 merchant case，代表不同年份、主題或素材形式的訪談紀錄。這會列為 `same_brand_multiple_records` / `multi_interview_record`，不是 duplicate error。
- 只有 `brand_name`、`merchant_handle`、`interview_year`、`article_title`、`video_title`、`podcast_title`、`news_title`、`source_sheet` 全部相同時，才會列為 suspected duplicate review item，且不得自動刪除、合併或覆蓋。
- Public metric 的 boolean channel 欄位會轉成 `allowed_exposure_channels` list。
- Public metric 若所有曝光渠道都是 false，會標示 `missing_allowed_exposure_channels=true` 並設為不可對外引用；若備註含 `不可公開`、`僅用於口頭說明`、`不留文字紀錄`，會保留 `restricted_note`。
- Handle mapping 可用來補齊 merchant case 的 `brand_name`、`sales_category_lv1`、`sales_category_lv2`。
- Restricted customer sheet 只建立 governance table，不進 content index；只要有有效資料列就會進 denylist preview，不以 NDA 欄位作為匯入條件。

### Excel Preview Workflow

`mka excel-preview` 只做安全預處理，不會寫入 Obsidian Vault，也不會建立正式 RAG index。輸出目錄固定為人工審核用 preview：

```text
reports/excel_preview/
├── merchant_cases.json
├── public_metrics.json
├── pending_metrics.json
├── restricted_customers.json
├── handle_mappings.json
├── preview_summary.md
└── validation_errors.md
```

每筆 Excel-derived preview record 都會保留：

- `record_type`
- `source_sheet`
- `source_row`
- `data_classification`
- `can_quote_externally`
- `captured_date`
- `normalized_at`

`restricted_customers.json` 是 denylist preview，後續可替換成 SQLite governance table。它應支援 brand name exact match、normalized brand match、merchant handle match、website URL match，以及 restricted warning / blocking。`handle_mappings.json` 則只作 normalization / enrichment，例如補齊 merchant case 的品牌名稱與產業分類。

Public metric 的使用情境必須透過 `allowed_exposure_channels` 控管。例如資料只允許 `saleskits` 與 `verbal_briefing` 時，不應用於 `press_release`、`website_recruiting` 或 `ads`。Pending metric 與 `can_quote_externally=false` 的資料不得用於新聞稿、官網、廣告、Saleskit 等對外用途。

`captured_date` 表示本次擷取 workbook 的日期；`normalized_at` 表示此 preview record 被系統正規化的時間。兩者用於追溯，不等同於原始內容發布日。

正式 Excel preview 在人工審核前必須通過 baseline count check。若 parser count 與人工基準不一致，不要硬改數字，需先輸出 discrepancy 並修正 parser。當前正式 workbook baseline：

- `商家夥伴案例資料庫`: 120
- `「不可公開」客戶名單`: 11
- `「可公開」對外數據`: 33
- `待確認數據`: 7
- `handle 比對`: 91

### Review Template Workflow

Excel 資料進正式 Vault / governance table 前的建議流程：

```text
excel-preview
→ review-template
→ 人工填寫 review_decision
→ validate-review-decisions
→ apply-review-decisions
→ approved Vault preview / governance table preview
→ 人工確認
→ sync to Obsidian / build formal content index
```

目前已實作 `excel-preview`、`review-template`、`validate-review-decisions`、`apply-review-decisions` 與 `sync-obsidian`。`sync-obsidian` 的 execute 只接受已人工確認的 plan，且仍不建立正式 content index。

產生人工審核 CSV：

```bash
.venv/bin/mka review-template \
  --preview-dir reports/excel_preview \
  --output reports/excel_preview/review_decisions_template.csv \
  --summary-output reports/excel_preview/review_summary.md
```

`review-template` 的範圍限制：

- 只讀取 `reports/excel_preview/*.json`，不從 `preview_summary.md` 反推資料。
- 不修改 Obsidian Vault。
- 不建立正式 content index。
- 不套用任何人工審核決策。
- `review_decision`、`reviewer`、`reviewed_at` 預設留空，必須由人工填寫。

### Obsidian Sync Workflow

`sync-obsidian` 是 apply preview 經人工確認後的最後一步，預設只產生 plan；它只會讀寫 `obsidian_vault/MKA/` namespace，不會修改 `.obsidian/` 或 namespace 外檔案。

```bash
.venv/bin/mka sync-obsidian plan \
  --apply-dir reports/excel_preview/apply_preview \
  --vault obsidian_vault

.venv/bin/mka sync-obsidian execute \
  --plan reports/obsidian_sync/sync_plan_<timestamp>.json \
  --vault obsidian_vault \
  --confirm

.venv/bin/mka sync-obsidian rollback \
  --batch <batch_id> \
  --vault obsidian_vault
```

執行前會重新驗證 `plan_state_hash`、apply preview 安全斷言與 restricted denylist；managed 檔案更新前會備份，移除只會 archive，不會 delete。人工編輯過的 managed 檔案與未管理檔案會列為 conflict，預設不覆蓋。

審核原則：

- `restricted_customer` 預設只能進 governance table，不得進一般 RAG citation 或對外引用。
- `pending_metric` 預設只能內部盤點，不可對外引用。
- `same_brand_multiple_records` / `same_handle_multiple_records` 是 informational review，不是 duplicate error。
- 一筆 preview record 原則上只產生一列 review row；多個 issue 會合併在同一列。

驗證人工填寫後的 CSV：

```bash
.venv/bin/mka validate-review-decisions \
  --decisions reports/excel_preview/review_decisions_template.csv \
  --preview-dir reports/excel_preview \
  --output reports/excel_preview/review_decisions_validation.md
```

`validate-review-decisions` 只做讀取與檢查：

- 不套用 `review_decision`。
- 不修改 Obsidian Vault。
- 不建立正式 content index。
- 會檢查 CSV 欄位、每筆 preview record 是否對齊目前 preview JSON、`review_decision` 是否為允許值、布林欄位是否有效。
- 會檢查治理規則：`restricted_customer` 只能進 governance table、`pending_metric` 不可對外引用、`no_valid_content_asset` 不可進 content index 或對外引用、缺少 exposure channel 的 public metric 不可對外引用。
- `reviewer` 與 `reviewed_at` 若仍空白會在 summary 中計數，但目前不視為 blocking error。

## 架構

模組分層固定在 `src/marketing_knowledge_agent/`：

- `ingestion`：讀取 Markdown 與 frontmatter，建立 validated document。
- `chunking`：切分 document 並保留來源 metadata。
- `indexing`：建立 SQLite documents/chunks/FTS tables 與 local embeddings。
- `retrieval`：執行 keyword/vector/hybrid search 與 metadata filters。
- `reranking`：依 metadata match、keyword match、freshness 做簡單重排。
- `generation`：mock generator，輸出 citations、freshness note、status warnings。
- `agentic`：離線 Agentic-lite orchestration，負責 query analysis、plan、工具執行與 reflection；不直接取代 retrieval/generation。
- `evaluation`：內建 prototype evaluation cases。

### 傳統 RAG 與 Agentic-lite

`mka ask` 保留原本的單步 hybrid retrieval + mock generation，適合簡單查詢與 smoke test。

`mka agent-ask` 會先判斷問題是否需要多步流程。若是 simple lookup，會走 fast path 並重用 `ask`；若偵測到比較、跨來源整理、資料新鮮度或 status governance 意圖，會建立最多 4 步的 deterministic plan，呼叫內部 search / index stats 工具，再用 reflection 記錄引用數、來源多樣性與不可引用狀態。

第一版 Agentic-lite 不呼叫外部 LLM、不使用 API key，也不自行改寫公司資料；它的主要用途是驗證 Agentic RAG 的系統骨架與可觀測 trace。

## 後續方向

- 接入正式 Obsidian Vault 前，先補齊 metadata migration 與資料品質檢查。
- 可新增 OpenAI embeddings provider，但 deterministic local embedding 應保留作為測試 fallback。
- 可新增 OpenAI generation provider，但 citations/status warning 必須由本地邏輯保證。
- 若資料量變大，再評估 ChromaDB、FAISS 或其他向量資料庫。
