# Requirements Traceability

狀態欄只使用 `implemented`、`partial`、`missing`、`conflicting`、`unknown`。`implemented` 表示現行 tracked code 與測試已直接覆蓋該要求，不表示未來 Google Sheets 流程已整體完成。

## 1. Authority、批次與輸出

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| AUTH-01 | Google Sheets是Official metadata／identity／governance authority；linked webpage只提供CapturedContent body | conflicting | `src/marketing_knowledge_agent/content_index.py`以Obsidian Markdown為正式index輸入，沒有Google canonical metadata＋CapturedContent authority split；`tests/test_content_index.py`驗證現行Markdown flow。 |
| AUTH-02 | 不得有 Obsidian → Google Sheets 反向同步 | implemented | tracked source 無 Google Sheets writer 或反向同步 entry；`src/marketing_knowledge_agent/cli.py` 的 Obsidian 命令只操作本機 Vault。 |
| AUTH-03 | Google metadata、CapturedContent、Obsidian、SQLite／FTS與vector作同一完整Release的sibling outputs | missing | `src/marketing_knowledge_agent/obsidian_sync.py`與`src/marketing_knowledge_agent/content_index.py`是前後串接，且tracked code沒有CapturedContent model、capture revision set或完整Release coordinator。 |
| AUTH-04 | 所有sibling artifacts共用完整`release_id`，並由manifest固定`metadata_sync_batch_id`、source fingerprint與CapturedContent revision／hash composition | partial | `src/marketing_knowledge_agent/obsidian_sync.py`有Vault batch ID；`src/marketing_knowledge_agent/indexing.py` schema沒有完整Release manifest、capture composition或global active release欄位。 |
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
| ID-11 | 第一版每個MREC的每個asset type最多一個Content Asset；identity為 `<MREC>:<asset_type>`；多個distinct safe canonical URLs只進needs_review，不拆asset，也不用URL／run position／array index作identity | conflicting | `asset_metadata_preview.py` 每個row/asset type只建立一筆，`asset_metadata.py` 可dedupe URL並將多個canonical candidates標為conflict；但`asset_id`仍是 `<sheet>:r<row>:<asset_type>`，不是MREC composite identity，且沒有Google `textFormatRuns`／整格link／formula／cell text的統一cardinality gate。 |
| ID-12 | Permanent ID governance writer採external standalone Apps Script，固定explicit configured＋allowlisted canonical Spreadsheet；不得以active Spreadsheet作identity／fallback | missing | Decision 4規格已確認；tracked repository沒有Apps Script project、controlled deployment source或target allowlist validator。 |
| ID-13 | Writer只可寫商家／夥伴M／N／O與Public Metric N；不得形成任意Spreadsheet／sheet／range writer，read-only ingestion保持權限隔離 | missing | tracked source沒有Google writer或write allowlist；現行Google read adapter本身也尚未實作，無法證明read／write deployment isolation。 |
| ID-14 | MREC／MET allocator不得覆寫合法ID或重用archived／retired ID；配置前驗證active＋archived＋reserved registry、namespace與safe next ID | missing | tracked repository沒有MREC／MET allocator、permanent ID registry或相關tests；現行identity仍是row-derived。 |
| ID-15 | BRD只可依already-approved mapping受控回填；blank／ambiguous identity不得自動create、merge、split或歸戶 | missing | tracked models沒有BRD master／approved mapping writer，現行search aliases亦不是brand identity governance。 |
| ID-16 | Allocation須以exclusive concurrency guard、guard後重讀及同一critical section reservation／write避免collision；target／schema／ID／BRD／write verification異常一律fail closed | missing | tracked repository沒有Apps Script allocator、concurrency guard、reservation、readback verification或failure-semantics tests。 |

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
| GOV-09 | Official persistence／search eligibility與usage／exposure permission分層；generic internal retrieval不要求任一G-M為true | conflicting | 現行oral-only可先進index再由Slack擋，且`slack_interface.py`固定使用external intent；尚無generic research與requested usage channel的intent-aware分流。 |

## 7. Official／Enrichment、index 與 Slack

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| IDX-01 | Official metadata與captured body只來自canonical models，不重解析Vault Markdown | conflicting | `content_index.py`重解析Vault Markdown；tracked code沒有canonical metadata＋CapturedContent direct builder。 |
| IDX-02 | SQLite/FTS index | implemented | `src/marketing_knowledge_agent/indexing.py` 建 documents/chunks/FTS5；`tests/test_content_index.py`、`tests/test_typed_query_retrieval.py`。 |
| IDX-03 | vector index | implemented | `indexing.py` 保存 deterministic embeddings並支援vector retrieval；`tests/test_typed_query_retrieval.py`。 |
| IDX-04 | Official與Enrichment物理或邏輯隔離 | missing | 單一SQLite index，無authority layer欄位/DB selection。 |
| IDX-05 | Slack預設Official，明確要求才含Enrichment | missing | `slack_interface.py` 只讀單一index，無 `include:enrichment` parser。 |
| IDX-06 | Enrichment不得覆蓋Official | missing | 無ENR ingestion、precedence或collision validator。 |
| IDX-07 | Manual Enrichment須通過完整frontmatter、canonical `approved_by` exact whitelist membership、semantic approval hash與allowed channel驗證 | missing | tracked source沒有ENR model、指定namespace parser、approver whitelist authority或membership validator。 |
| IDX-08 | 實質變更使approval失效，格式變更不失效 | missing | 無semantic content hash。 |
| IDX-09 | Slack Bot/renderer已存在 | implemented | `slack_interface.py`、`slack_presentation.py` 與多個 `tests/test_slack_*.py`。 |
| IDX-10 | Slack／internal search是Official retrieval surface；generic query按Official eligibility搜尋，只有明確usage intent才套對應G-M | conflicting | 決策已確認；現行Slack固定使用external intent與通用written-safe gate，沒有generic internal research與Saleskit／website／advertising intent的分流。 |
| IDX-11 | Search result保留authority、governance、searchable與`allowed_exposure_channels` metadata，不因可搜尋就推定全渠道可用 | partial | 現行public metric metadata有channel資訊與structured result，但Official canonical index／intent-aware generation contract尚未完成。 |
| IDX-12 | Missing／empty／unauthorized `approved_by`、whitelist load／schema failure或substantive approval hash mismatch一律使Enrichment search eligibility fail closed | missing | tracked source沒有Manual Enrichment ingestion、whitelist loader或approval eligibility validator；`searchable=true`目前沒有此類authority gate。 |
| IDX-13 | Response rendering分別套`content_item_cap`、`metric_item_cap`與跨record type的`rendered_message_budget`；cap必須在authority／governance／intent／requested-channel filtering、dedupe及rerank後 | conflicting | `slack_presentation.py`固定以`entities[:5]`與跨內容共用10筆asset slice限制輸出，沒有Public Metric獨立cap、rendered-size budget或Decision 2 intent-aware post-governance cap ordering。 |
| IDX-14 | Budget只加入可完整render的whole item；不得截斷approved claim、citation、identity、allowed channels或authority，超限時回傳item-boundary more-results／eligible remaining metadata | missing | 現行renderer沒有Public Metric whole-item budget contract、rendered-size計量、more-results indicator或post-governance remaining count。 |
| IDX-15 | Display cap只屬response layer，不得限制Content Asset全文／Public Metric的ingestion、Official Index corpus或retrieval candidates | partial | 現行固定slice在`slack_presentation.py`，未改`content_index.py` corpus；但tracked flow尚無Decision 6 formal contract、獨立metric path或完整Google canonical index。 |

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
| OPS-10 | Attempt 3仍失敗並durably記錄batch／release為failed後，才以configured＋allowlisted Private Slack Ops作第一版主要failure alert surface | missing | tracked source沒有scheduler、sync/release notifier、Private Slack Ops target validator或final-failure notification orchestration。 |
| OPS-11 | `release_status`與`notification_status`分離；先持久化release結果再嘗試通知，Slack失敗不得改寫結果、rollback成功release或掩蓋原始failure | missing | tracked source沒有通用Release coordinator、operation record或notification state machine；現行Slack handler不是release transaction元件。 |
| OPS-12 | Ops alert只含sanitized structured metadata，禁止oral-only／restricted／captured body、claim、secret、token、signed URL、raw response或unredacted stack trace；Slack Search與Slack Ops為不同surface | missing | 現有governance有部分Slack query／result redaction，但無operational alert schema、sanitizer、private target allowlist或Search／Ops authorization boundary。 |

## 9. 決策與實作狀態

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| DEC-01 | 專用read-only Service Account | missing | 認證架構已確認；`pyproject.toml`與`src/`仍無Google auth adapter，且本輪不實作或建立credential。 |
| DEC-02 | Slack／internal search是Official retrieval surface而非exposure channel；第一版不新增Slack checkbox／N欄／source permission | conflicting | 決策已確認；tracked source尚未實作兩層eligibility／usage intent模型，現行Slack固定external intent，Google import亦仍有oral-only先落地的衝突。 |
| DEC-03 | Manual Enrichment `approved_by`必須是stable canonical reviewer ID並精確命中外部受控authorized approver whitelist | missing | 決策已確認；tracked source與tests沒有Manual Enrichment parser、whitelist authority／loader、deterministic exact membership或fail-closed validation。 |
| DEC-04 | Permanent ID allocator／governance writer採external standalone Apps Script；固定Spreadsheet ID、最小寫入白名單且與read-only sync隔離 | missing | 決策已確認為B；tracked repository無Apps Script project、controlled deployment source或ID writer實作，不能因文件確認標成implemented。 |
| DEC-05 | 第一版final sync／release failure主要通知面為configured＋allowlisted Private Slack Ops；Attempt 1／2只記retry state，notification是durable result之後的非交易side effect | missing | 決策已確認為A；tracked source無sync/release notifier、`release_status`／`notification_status`分離、Ops target allowlist或sanitized alert tests，不能因文件確認標成implemented。 |
| DEC-06 | Public Metric採獨立`metric_item_cap`，Content Asset採`content_item_cap`，另套overall `rendered_message_budget`；只對post-governance reranked eligible results作whole-item rendering | conflicting | 決策已確認為B；`slack_presentation.py`仍使用固定共用entity／asset caps，沒有metric-specific cap、rendered budget、atomic Public Metric claim／citation或eligible remaining count。 |
| DEC-07 | Scheduler最多3次attempt：09:00、09:30、10:00 | missing | retry語義已確認；tracked source仍無scheduler實作。 |
| DEC-08 | 第一版一個H-K source cell最多一個logical Content Asset，identity為 `<MREC>:<asset_type>`，多distinct safe canonical URLs不拆asset | conflicting | 決策已確認；現行`asset_metadata_preview.py`雖每row/asset type只建一筆，但仍使用row-derived `asset_id`，且未實作Google CellData多來源URL的統一canonicalization／cardinality gate。 |
| DEC-09 | embedded article link建立CapturedContent全文，Obsidian／index sibling render並以全文RAG產生query-focused summary | missing | 決策已確認；tracked source無HTTP capture、HTML normalization、CapturedContent或capture LKG，現行Official index仍由Markdown重建。 |
| DEC-10 | 第一版linked capture與Google metadata sync共同建立單一完整Release，不得獨立refresh／activate | missing | 決策已確認；tracked source無linked capture或完整Release coordinator，現行Vault與index亦未形成metadata＋capture＋sibling artifacts的單一activation boundary。 |
| DEC-11 | 同一canonical URL的temporary capture failure在通過previous-success、policy與freshness gates後，可讓LKG以`stale`進新完整Release | missing | 決策已確認；tracked source沒有HTTP capture、CapturedContent status／attempt lineage、temporary failure classifier、freshness policy或stale-aware Release manifest。 |

## 10. Linked Content Capture與RAG

| ID | 需求 | 狀態 | 現況與證據 |
| --- | --- | --- | --- |
| LCAP-01 | 從`textFormatRuns`、whole-cell hyperlink、`HYPERLINK` formula與cell text擷取embedded links | partial | `asset_metadata_preview.py`可讀本機worksheet whole-cell hyperlink；沒有Google `textFormatRuns`、formula與cell text統一extractor。 |
| LCAP-02 | Primary Article建立clean full-body `CapturedContent`並掛在Content Asset | missing | tracked source沒有web fetcher、HTML extractor／normalizer或CapturedContent model。 |
| LCAP-03 | Evidence Article掛在MET evidence relationship且不得升格為approved metric | missing | `models.py`沒有`authority_role=evidence`、evidence relationship ID或Evidence Article schema。 |
| LCAP-04 | captured全文切成可供FTS與vector搜尋的deterministic chunks | partial | `chunking.py`、`indexing.py`已有通用Markdown-derived chunks／FTS／embedding；沒有CapturedContent全文輸入、section metadata或capture hash lineage。 |
| LCAP-05 | retrieval／reranking後依query相關passages產生query-focused summary | partial | `retrieval.py`、`reranking.py`與`generation.py`已有檢索、rerank及引用片段回答；沒有captured article corpus、authority role或明確derived summary contract。 |
| LCAP-06 | Obsidian Markdown與Official index由canonical inputs sibling render，不得Markdown reparse | conflicting | `ingestion.py`解析Markdown，`content_index.py`以Vault Markdown建立Official index；現行tests固定此流程。 |
| LCAP-07 | same canonical URL＋previous success＋temporary failure可沿用LKG；URL changed／never-successful／blocked／policy failure不得錯掛舊body | missing | tracked source沒有capture status、revision、previous content hash、capture attempt classifier或LKG store。 |
| LCAP-08 | Google `source_fingerprint`與web `capture_content_hash`分離計算，但同一完整Release manifest固定兩者 | missing | tracked source沒有Google fingerprint，也沒有captured body hash／完整Release composition manifest。 |
| LCAP-09 | SHOPLINE／approved third-party／unknown third-party採可配置、fail-closed capture policy | missing | tracked source沒有domain capture policy或capture mode DTO。 |
| LCAP-10 | 不繞過登入、付費牆、robots或技術限制，unsafe／private URL直接拒絕 | missing | tracked source沒有HTTP capture boundary、redirect revalidation或paywall／auth policy enforcement。 |
| LCAP-11 | Captured revisions保留lineage但不可單篇或獨立activate；第一版無capture-only scheduler／pointer／partial release | missing | tracked source尚無CapturedContent revision或capture scheduler；亦無可固定metadata、Vault、DB／FTS與vector composition的generic Release coordinator。 |
| LCAP-12 | stale LKG沿用原`content_hash`／`captured_at`／`last_successful_capture_at`，更新`last_capture_attempt_at`，manifest與search／citation揭露stale；freshness gate未配置或超限時fail closed | missing | tracked source沒有CapturedContent timestamp／status contract、stale manifest、capture freshness metadata或ranking／warning policy。 |
