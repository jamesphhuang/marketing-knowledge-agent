# Requirements Traceability

狀態欄只使用 `implemented`、`partial`、`missing`、`conflicting`、`unknown`。`implemented` 表示現行 tracked code 與測試已直接覆蓋該要求，不表示未來 Google Sheets 流程已整體完成。

## 1. Authority、批次與輸出

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| AUTH-01 | Google Sheets 是 Official 唯一 Source of Truth | conflicting | `src/marketing_knowledge_agent/content_index.py` 以 Obsidian Markdown 為正式 index 輸入；`tests/test_content_index.py` 驗證此流程。 |
| AUTH-02 | 不得有 Obsidian → Google Sheets 反向同步 | implemented | tracked source 無 Google Sheets writer 或反向同步 entry；`src/marketing_knowledge_agent/cli.py` 的 Obsidian 命令只操作本機 Vault。 |
| AUTH-03 | Obsidian、SQLite／FTS、vector、Slack data 由同一 normalized batch 產生 | missing | `src/marketing_knowledge_agent/obsidian_sync.py` 與 `src/marketing_knowledge_agent/content_index.py` 是前後串接而非 sibling renderers。 |
| AUTH-04 | 所有 sibling artifacts 共用 `sync_batch_id` | partial | `src/marketing_knowledge_agent/obsidian_sync.py` 有 batch ID；`src/marketing_knowledge_agent/indexing.py` schema 沒有 batch manifest／active release 欄位。 |
| AUTH-05 | 不允許 partial publish | partial | `src/marketing_knowledge_agent/obsidian_sync.py` 可在失敗時還原 Vault；`src/marketing_knowledge_agent/content_index.py` 未與 Vault／vector 組成單一發布交易。 |

## 2. Google Sheets snapshot 與 CellData

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| GS-01 | 使用 `spreadsheets.get(includeGridData=true, ranges=...)` | missing | `src/marketing_knowledge_agent/excel_preview.py` 只解析本機 `.xlsx` ZIP/XML；`pyproject.toml` 無 Google client dependency。 |
| GS-02 | 擷取 hidden sheets 與指定 sheet/header row | partial | `excel_preview.py` 有固定 sheet/header preflight，但沒有 Google sheet properties／hidden metadata；`tests/test_excel_preview.py` 只驗本機 workbook。 |
| GS-03 | 保留 formatted/effective/user-entered value | missing | `excel_preview.py` 只保存 XML cached/inline/shared values，沒有三層 CellData model。 |
| GS-04 | 公式使用 effective/formatted value，不把公式字串當內容 | partial | 本機 parser 使用 cached `<v>`，但不保存 formula/effective provenance；`tests/test_excel_preview.py` 未覆蓋 Google formula CellData。 |
| GS-05 | checkbox 保留 boolean 與 data validation | partial | `excel_preview.py` 可讀 cached boolean；沒有 data validation；`tests/test_excel_preview.py` 有 boolean fixture。 |
| GS-06 | Rich Text run hyperlinks | missing | `src/marketing_knowledge_agent/asset_metadata_preview.py` 只讀 worksheet hyperlink relationship，沒有 textFormatRuns。 |
| GS-07 | merge metadata 精確繼承，不全面 forward fill | partial | `excel_preview.py` 僅處理受限垂直 merge與特定欄 fill-down；`tests/test_excel_preview.py` 有 merge案例，沒有 Google merge CellData lineage。 |
| GS-08 | source fingerprint 於開始及發布前重驗 | missing | tracked source無 spreadsheet snapshot fingerprint；`src/marketing_knowledge_agent/obsidian_sync.py` 的 state hash只保護 local plan。 |
| GS-09 | 每次attempt重新取得／驗證source fingerprint；最多3次attempt，時間為09:00、09:30、10:00 | missing | retry語義與時間已確認；tracked source無 scheduler/retry orchestrator，`src/marketing_knowledge_agent/cli.py` 無 schedule command。 |
| GS-10 | 排程每月1、15日，Timezone為Asia/Taipei；第3次仍失敗時batch為failed | missing | repository無 tracked scheduler或CI workflow。 |

## 3. URL extraction 與安全

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| URL-01 | 依 rich text、cell hyperlink、HYPERLINK formula、文字 URL 排序擷取 | partial | `asset_metadata_preview.py` 只覆蓋整格 hyperlink；`tests/test_asset_metadata_preview.py` 驗其既有能力。 |
| URL-02 | 只允許公開 HTTP/HTTPS | partial | `src/marketing_knowledge_agent/asset_metadata.py` 限制 scheme；沒有完整 public-host判定。 |
| URL-03 | 拒絕 mailto/tel/file/relative/fragment-only | implemented | `asset_metadata.py` 的 URL validation 會拒絕非 HTTP(S) 與無 host 值；相關 asset tests通過。 |
| URL-04 | 拒絕 localhost/private/link-local/reserved IP | missing | `asset_metadata.py` 沒有 `ipaddress`/DNS-independent host class 檢查；現有 tests未覆蓋。 |
| URL-05 | 拒絕 internal admin path、credentials、tokenized/sensitive query | partial | `asset_metadata.py` 有 userinfo、部分 credential/redirect/tracking檢查；缺通用 admin path與敏感 query key policy。 |
| URL-06 | 缺 URL 不得猜測或外部搜尋 | implemented | `asset_metadata_preview.py` 只使用 workbook metadata；`tests/test_asset_metadata_preview.py` 無網路依賴。 |

## 4. Identity、品牌與 lineage

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| ID-01 | MREC 永久、唯一、不可變、純文字 | conflicting | `excel_preview.py` 以 `<sheet>:r<row>` 產生 record ID；排序／插列會變。 |
| ID-02 | MET 永久、唯一、不可變、純文字 | conflicting | public metric沿用 row-derived record ID；`tests/test_apply_review_decisions.py` 的 identity以 sheet/row為主。 |
| ID-03 | BRD 半自動且只有人工確認後正式配置 | missing | `src/marketing_knowledge_agent/models.py` 無 BRD欄位或 brand master model。 |
| ID-04 | ENR identity與 approval metadata | missing | tracked models與 `content_index.py` 無 manual enrichment schema。 |
| ID-05 | ID 不因 row/name/handle/URL改變 | conflicting | `src/marketing_knowledge_agent/obsidian_sync.py` 以 source_sheet/source_row匹配，路徑也含 row。 |
| ID-06 | 封存 ID 不重配、同 ID 可 restore | partial | Obsidian保留 archived檔案，但無 canonical ID registry；`tests/test_obsidian_sync.py` 驗檔案 archive/rollback。 |
| ID-07 | 品牌 ID 對照 hidden sheet | missing | 無 Google writer/Apps Script/brand master importer model。 |
| ID-08 | 首次 BRD 候選分組與 approve/split/merge/exclude | missing | `src/marketing_knowledge_agent/production_search_alias_plan_v2.py` 是固定歷史 search-alias plan，不是品牌初始化流程。 |
| ID-09 | Handle唯一與網站唯一僅能建議；名稱近似不可自動 merge | missing | 現有 `search_aliases.py` 是檢索 alias，不是 entity resolution治理。 |
| ID-10 | source sheet、row、cell/field與batch lineage | partial | `models.py` 保留 source_sheet/source_row；沒有 cell range、snapshot fingerprint與跨輸出 batch lineage。 |

## 5. Canonical entities、lifecycle 與 Obsidian

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| DM-01 | Brand/Merchant/Partner entity | partial | merchant metadata存在，但與 interview row混合；`models.py`、`apply_review_decisions.py`。 |
| DM-02 | Source Record entity | partial | row-derived record存在；不是永久 MREC模型。 |
| DM-03 | Content Asset entity | partial | `asset_id`與asset metadata存在；identity依parent row；`asset_metadata.py`。 |
| DM-04 | Public Metric entity | partial | `public_metric` record type存在；缺MET與oral-only persistence boundary。 |
| DM-05 | Category、Feature、Tag entities | missing | 現況只把字串/tuple放metadata，無獨立 taxonomy IDs或 Wiki-link entity renderer。 |
| DM-06 | Manual Enrichment entity | missing | 無 ENR model、approved namespace parser或semantic approval hash。 |
| OBS-01 | 指定 Vault資料夾結構 | missing | `obsidian_sync.py` 使用既有 managed namespace/approved preview layout，未實作目標 entity folders。 |
| OBS-02 | Frontmatter permanent IDs、relations、status、lineage、batch、managed marker | partial | 現有 frontmatter有record/source/status/batch/managed欄位的一部分；缺BRD/MREC/MET/ENR與canonical relations。 |
| OBS-03 | 檔名不作 identity、碰撞可決定性處理 | conflicting | `obsidian_sync.py` 用source row作fallback matching與現行filename成分。 |
| OBS-04 | Wiki Links基於 permanent ID relations | missing | `apply_review_decisions.py` 渲染平面Markdown表格，沒有entity graph renderer。 |
| OBS-05 | 有標題+有效URL為active並進Official | partial | asset review與content index有publishable gate；尚非Google canonical flow。 |
| OBS-06 | 有標題無URL為incomplete，只進98_Incomplete | missing | 現行asset review標記missing link，但無指定資料夾/lifecycle及全索引排除契約。 |
| OBS-07 | source消失採archive，保留ID/歷史且從index移除 | partial | Obsidian可移到archive；canonical store、index release與restore未串起。 |
| OBS-08 | `archived_at`、`archived_reason`與restore | missing | 現行 archive manifest沒有完整canonical lifecycle欄位。 |
| OBS-09 | mass-deletion/source-health gate | missing | `obsidian_sync.py` 對preview消失項目直接規劃archive；`tests/test_obsidian_sync.py` 沒有range-collapse gate。 |

## 6. Governance 與資料外洩

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| GOV-01 | restricted_customer與handle_mapping不進一般RAG | implemented | `src/marketing_knowledge_agent/governance.py`、`query_gating.py`、`tests/test_excel_governance.py`、`tests/test_query_gating.py`。 |
| GOV-02 | pending_metric不進正式對外內容 | implemented | `content_index.py`與external-intent gates排除pending；相關 apply/query/slack tests通過。 |
| GOV-03 | oral-only不得進Markdown/DB/FTS/vector/Slack/log/fixture/report正文 | conflicting | normalize/apply/index可持久化oral-only，Slack才濾除；`tests/test_slack_structured_governance.py` 固定了此現況。 |
| GOV-04 | restricted sheet有效列一律進denylist preview，不以NDA為條件 | implemented | `excel_preview.py` normalize與 `tests/test_excel_preview.py` baseline/denylist cases。 |
| GOV-05 | 同品牌多筆merchant case不自動dedupe/merge | partial | 現行每列獨立；沒有BRD grouping，也沒有「指定欄位全同才review」的新規則。 |
| GOV-06 | Excel count通過正式baseline才進人工review | partial | baseline tests存在；runtime preview未將所有production baseline固定為blocking policy。 |
| GOV-07 | blocking error停止整批publish並保留last-known-good | partial | Obsidian單一namespace有rollback；沒有跨Vault/index batch coordinator。 |
| GOV-08 | audit/report不得複製敏感內容 | partial | denylist Slack audit會去query；一般audit與部分internal inventory仍可含正文；`slack_interface.py`、`apply_review_decisions.py`。 |

## 7. Official／Enrichment、index 與 Slack

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| IDX-01 | Official只來自Google normalized model | conflicting | `content_index.py` 重解析Vault Markdown。 |
| IDX-02 | SQLite/FTS index | implemented | `src/marketing_knowledge_agent/indexing.py` 建 documents/chunks/FTS5；`tests/test_content_index.py`、`tests/test_typed_query_retrieval.py`。 |
| IDX-03 | vector index | implemented | `indexing.py` 保存 deterministic embeddings並支援vector retrieval；`tests/test_typed_query_retrieval.py`。 |
| IDX-04 | Official與Enrichment物理或邏輯隔離 | missing | 單一SQLite index，無authority layer欄位/DB selection。 |
| IDX-05 | Slack預設Official，明確要求才含Enrichment | missing | `slack_interface.py` 只讀單一index，無 `include:enrichment` parser。 |
| IDX-06 | Enrichment不得覆蓋Official | missing | 無ENR ingestion、precedence或collision validator。 |
| IDX-07 | approved enrichment frontmatter完整驗證 | missing | 無指定namespace/frontmatter parser。 |
| IDX-08 | 實質變更使approval失效，格式變更不失效 | missing | 無semantic content hash。 |
| IDX-09 | Slack Bot/renderer已存在 | implemented | `slack_interface.py`、`slack_presentation.py` 與多個 `tests/test_slack_*.py`。 |
| IDX-10 | Slack套用G-M渠道權限 | unknown | 產品政策未決；現行只有通用written-safe gate，見 `governance.py`。 |

## 8. 可重現發布、安全與維運

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| OPS-01 | deterministic conversion | partial | Obsidian path/checksum與local embedding可決定；缺Google snapshot canonicalization與新entity renderer。 |
| OPS-02 | idempotency | partial | Obsidian plan/execute重跑可辨unchanged；無通用multi-artifact release idempotency。 |
| OPS-03 | preview/apply separation | implemented | review、Obsidian、governance/store executors均有plan/confirm/execute patterns與tests。 |
| OPS-04 | blocking validation classes | partial | 多個fail-closed validator存在；缺source health、permanent ID、CellData、oral-only persistence與跨artifact validators。 |
| OPS-05 | atomic publish | partial | `obsidian_sync.py` 支援Vault rollback；`store_data_sync_plan_v2_execution.py` 有特定批次candidate/atomic replace；沒有generic release transaction。 |
| OPS-06 | last-known-good rollback | partial | Obsidian與特定store executor可rollback；沒有跨Official artifacts active pointer與recovery journal。 |
| OPS-07 | source consistency | missing | 無Google start/end fingerprint。 |
| OPS-08 | deletion safety threshold | missing | 無來源下降比例或critical-sheet gate。 |
| OPS-09 | audit trail | partial | Obsidian、governance store、Slack各自有audit；無統一sync batch manifest。 |

## 9. 決策與實作狀態

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| DEC-01 | 專用read-only Service Account | missing | 認證架構已確認；`pyproject.toml`與`src/`仍無Google auth adapter，且本輪不實作或建立credential。 |
| DEC-02 | Slack權限沿用自媒體或新增獨立權限 | unknown | `governance.py`沒有internal_search mapping。 |
| DEC-03 | approved_by白名單 | unknown | 無ENR validator；需求明確保留決策。 |
| DEC-04 | bound或external Apps Script | unknown | repository無Apps Script。 |
| DEC-05 | 成功／失敗／needs_review通知管道 | unknown | tracked source無sync notifier。 |
| DEC-06 | Public Metric是否計入Slack每頁上限 | unknown | `slack_presentation.py`目前使用通用result caps。 |
| DEC-07 | Scheduler最多3次attempt：09:00、09:30、10:00 | missing | retry語義已確認；tracked source仍無scheduler實作。 |
