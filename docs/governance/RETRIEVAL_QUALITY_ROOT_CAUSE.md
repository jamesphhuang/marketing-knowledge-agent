# Retrieval Quality Root Cause Analysis

## Scope

本文件記錄 Retrieval Quality and Typed Query Constraint Architecture Sprint 的實際呼叫鏈、錯誤根因與安全邊界。分析基準為 2026-07-17 的正式 content index 與 181-test baseline。

## Actual Call Chain

```text
Slack app_mention
→ slack_interface._strip_app_mention
→ pipeline.agent_ask
→ query denylist pre-check
→ agentic.analyze_question / plan
→ pipeline.search_index
→ SQLiteRetriever.search
→ SearchFilters metadata gating
→ FTS5 + deterministic embedding
→ rerank_results
→ restricted source filtering
→ generation / local citation assembly
→ answer and citation governance scan
→ slack_interface.format_slack_reply
```

`_strip_app_mention` 只移除訊息開頭的 `<@BOT_ID>` 與鄰近空白。例如 `<@BOT> 提供我三風製麵的內容` 實際送入 pipeline 的字串是 `提供我三風製麵的內容`。Slack 不解析搜尋欄位，這個責任屬於共用 pipeline。

## Confirmed Root Causes

### 1. Natural language was never converted into field constraints

舊流程把整句問題直接交給 FTS 與 embedding。雖然 `SearchFilters` 已能過濾部分 metadata，但只有 CLI 明確傳入 filter 時才生效；自然語言中的名稱、Handle、Category 與年份不會自動變成 filter。

因此：

- `dachun` 只是一般文字，不是 `merchant_handle == dachun`。
- `三風製麵` 即使找到正確文件，也沒有把候選集合限制在該品牌。
- `居家生活` 會在 title/body/notes 產生文字或向量相似度，不是只查 Sales Category 欄位。

### 2. Formal documents are indexed as Markdown bodies

Merchant case 的 managed Markdown body 包含標題、Content Assets 表格與 Notes。FTS title/body 與 embedding 都會看到這個 Markdown blob；空資產欄位也曾以表格空列存在。Metadata 同時保留在 `metadata_json`，但舊檢索沒有先使用這些欄位建立候選集合。

### 3. Top K was applied before entity correctness was established

`pipeline.search_index` 先取 `limit * 3`，rerank 後再切成 `limit`；generator 再取前 3 筆。這個流程適合一般語意問題，但不適合 exact lookup。當指定品牌只有 1 筆時，舊流程會把另外 2 筆相似文件一起交給 generator，造成「補滿」效果。

### 4. Reranking could reorder but could not enforce identity

舊 reranker 只有 keyword、metadata match 與 freshness bonus。它能改善排序，不能保證非指定品牌完全離開候選集合。提高權重或門檻無法修復這個問題。

### 5. Generation faithfully used the wrong candidate set

Generator 沒有自行重新檢索，但會依序使用收到的前幾個 chunks。因此主要錯誤發生在 query planning 與 candidate selection，不在 Slack renderer 或回答提示詞。

## Schema and Index Findings

正式 SQLite 目前有 108 documents：105 `merchant_case`、3 `public_metric`。`documents` 表只有部分常用欄位，其餘 metadata 存在 `metadata_json`；FTS5 只索引 title/body。

目前 formal index 可安全使用：

- `brand_name`
- `merchant_handle`
- `merchant_status`
- `interview_year`
- `sales_category_lv1` / `sales_category_lv2`
- `content_tags`
- `article_title` / `video_title` / `podcast_title` / `news_title`
- record-level `status`（只供既有治理，不代表 asset 已上線）
- `claim_status`
- `allowed_exposure_channels`
- `can_quote_externally`
- citation trace fields

目前不存在或不可可靠推導：

- `interview_date`
- 分開的 `merchant_name` 與 `partner_name`
- `interview_status`
- asset-level `published_at`
- asset-level URL
- asset-level publication status
- `review_status` / searchable `review_decision`

缺少的欄位不得從正文、擷取日期或檔名推測。

## Acceptance Follow-up: Fail-Closed Constraints

`98700af` 的 acceptance review 發現三個 blocking root causes：

1. Field Registry 只描述欄位，沒有驅動 executor；未知欄位最後會回傳 match。
2. 完整日期先被單一年份 regex 部分命中，造成 date query 降級成 `interview_year`。
3. asset-level `publication_status` 被錯誤映射到 record-level `status`。

修正後，每個 `QueryConstraint` 都有 `support_status` 與 `reason`，Field Registry 同時聲明 `searchable`、`executable`、`metadata_source`、value scope 與 unsupported reason。Plan 會衍生 supported / unsupported / ambiguous / invalid constraint 清單；任何不可執行的 hard constraint 都令 `execution_blocked=true`，retrieval 在 FTS/vector 前直接停止。Executor 對未知欄位與未知 operator 明確 non-match，作為第二道防線。

解析順序改為完整日期 → 年份區間 → 單一年份。完整日期與 `partner_name`、`review_status`、`interview_status`、asset URL/date/status 等未具正式資料的條件會保留在 plan 並 fail closed，不會刪除後只執行 AND 查詢的其他條件。

## Corrective Architecture

新流程在共用 pipeline 內加入：

```text
normalize → resolve canonical fields → TypedQueryPlan
→ support validation / fail closed → hard constraint filtering → governance filtering
→ lexical/vector ranking inside legal candidates
→ structured entity/asset aggregation
→ channel renderer
```

精確條件只決定合法候選集合；FTS、embedding 與 rerank 只在集合內排序。0 筆不改 OR、不拿掉條件、不補相似文件。

## Security Notes

- Denylist pre-check、source filtering、answer/citation scan 均保留。
- Query debug 不輸出 chunk text、source path 或 restricted record。
- Restricted customer 與 handle mapping 仍不進 content index 或 citation。
- 本 sprint 未新增外部 API、LLM 呼叫或 token handling。

## Follow-up Migration

後續應建立 Asset-Level Metadata Enrichment Sprint，新增經人工審核的 `content_assets` 資料：record id、asset type、title、URL、published_at 與 publication status。完成 schema migration、Vault preview、formal index rebuild 與 governance assertions 前，asset URL、published date 與 publication status 必須維持 fail closed。
