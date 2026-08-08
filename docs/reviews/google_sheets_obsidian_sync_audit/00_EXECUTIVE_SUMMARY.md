# Google Sheets → Obsidian／Search Audit：Executive Summary

## 結論

目前系統已具備成熟的離線 RAG、人工 review、Obsidian managed namespace、SQLite FTS／deterministic vector、query gating、Slack 外部查詢，以及多組 plan／confirm／execute／rollback 安全元件；但它尚未具備本次目標所需的「Google Sheets 為Official metadata／identity／governance authority、linked webpage正文經CapturedContent canonical model、相容canonical inputs直接產出Obsidian與所有正式索引」通用同步架構。

本Audit列出的Decision 1–11現已全部由使用者正式確認，沒有Remaining Decision。尚未指定的cap數值、pagination UX、credential、deployment與operational parameters仍是implementation／operational questions，不得誤標為原Decision尚未裁決。

不得直接延伸現有 `excel-preview → Markdown → content-index` 作為目標架構，原因有四項：

1. 現行 importer 解析本機 `.xlsx` XML，不是 Google Sheets API `CellData`；缺 Rich Text links、data validation、公式／effective value完整契約與完整 URL 安全策略。
2. 現行 permanent identity 主要是 `source_sheet + source_row`，排序或插列會改變 `record_id`、`asset_id`、Markdown 對應及 alias owner，與 MREC／BRD／MET 永久 ID 規則衝突。
3. 正式 SQLite 目前從 synced Markdown 重新解析，Obsidian 與 index 不是由同一標準化模型直接生成；`sync_batch_id` 也只屬於 Vault sync，沒有跨輸出的 release manifest。
4. oral-only metric 目前可進 Vault、SQLite、FTS 與 embeddings，只在 external retrieval／Slack renderer 之前被濾除。這是本目標最優先必須修正的資料落地風險。

## 現況判定

| 能力 | 判定 | 摘要 |
| --- | --- | --- |
| 本機 Excel preview | partial | 有五 sheet normalize、header preflight、部分 merge、checkbox cached boolean；沒有 Google `CellData`。 |
| 人工 review／apply preview | implemented | validation、守恆、preview/apply 分離、restricted/pending 防線已有良好測試。 |
| Obsidian sync | implemented | managed namespace、plan state hash、conflict、archive、backup、rollback 已實作；identity 仍依 source row。 |
| SQLite／FTS／vector | implemented | 三表 schema、FTS5、local embeddings、typed filtering 已實作；正式 index 來源仍是 Markdown。 |
| Slack Official Search | partial | Bot、external intent、denylist、structured renderer已實作；沒有Official／Enrichment雙索引、Decision 2 intent分流，或Decision 6獨立metric cap＋overall rendered budget。 |
| Google Sheets 連接 | missing | 無 Google client、auth、snapshot、hidden sheet grid 擷取或 source revision fingerprint。 |
| BRD／MREC／MET／ENR | missing | canonical models 與永久 ID validator 尚未存在。 |
| 同批次多輸出發布 | missing | 沒有通用 release candidate、active pointer、跨 Vault／indexes commit journal。 |
| mass-deletion gate | missing | 空 apply preview 可規劃 archive 全部 managed files；沒有來源健康／下降比例 gate。 |

## 五個最高風險

1. **Oral-only 落地**：`normalize_public_metric_row` 會把僅 `verbal_briefing` 的資料設為可引用且可進 content index；`content_index` 只要求 channels 非空。正式測試亦把 oral-only record 已存在 SQLite 視為現況，只驗 Slack 不顯示。
2. **Row identity 漂移**：`record_id = <sheet>:r<row>`、`asset_id = <record_id>:<asset_type>`；插列、排序或移動會創造新身分並可能將舊檔誤判為 archive。
3. **Archive blast radius**：Obsidian plan 對「managed 有、preview 無」一律 `will_archive`；沒有 API failure、range shrink、missing sheet、ID loss 或大量下降的前置安全閘。
4. **平行資料流程**：prototype ingest、Excel preview/apply、Obsidian sync、content-index rebuild、asset preview、one-off store sync、alias projection 各有不同 schema 與 authority；一次性 executor 綁死特定 plan、commit、row count，不能當通用同步器。
5. **Source-of-truth 反轉**：現行正式index從Obsidian Markdown解析；目標明定Google Sheets是Official metadata authority、linked webpage只提供CapturedContent body，Obsidian與index必須由相容的canonical metadata／captured revisions作siblings生成。

## 建議最小路徑

第一個Sprint只建立「Google snapshot contract + normalized canonical metadata + CapturedContent／CapturePolicy contract + validation」，不碰正式Vault、SQLite、Slack、Apps Script或HTTP：

1. 建立 injected Google Sheets reader protocol 與 synthetic `CellData` fixtures；production auth adapter 保留未啟用。
2. 定義BRD／MREC／MET、content asset composite identity、CapturedContent、source lineage、lifecycle、publish eligibility與雙freshness hash邊界。
3. 實作 oral-only 在 normalize 後立即資料最小化，只保留 source row 與 exclusion reason；禁止 claim 進任何 persistent artifact。
4. 建立 fingerprint、ID uniqueness／immutability、required sheet、merge、URL safety、mass-deletion input health validators。
5. 只輸出 redacted validation／diff preview；不寫 Obsidian、不建 index。

完成此 Sprint 並持續遵守 `08_DECISIONS_REQUIRED.md` 已確認的Decision 1–11後，才進renderer／index release candidate。

## 驗證結果

- 原始Audit審查階段曾執行兩批互不重疊的安全測試，共 `431 passed`、`0 failed`；Final Consistency Review未重跑正式測試。
- 兩批各出現相同 6 個 Pydantic V1-style `@validator` deprecation warnings。
- 完整 pytest 未執行，因 historical fixtures 會讀取／複製本輪明確禁止的 `data/`、`reports/`、`obsidian_vault/`、`.mka/`。
- 未連 Google Sheets、Slack API 或外部 LLM；未啟動 Bot；未執行 migration。
