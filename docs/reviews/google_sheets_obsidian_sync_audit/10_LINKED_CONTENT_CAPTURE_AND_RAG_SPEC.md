# Linked Content Capture and RAG Specification

## 1. Scope and authority

Decision 9已確認選擇A：Google Sheet中符合條件的embedded article links不只保存URL；系統應建立Captured Content，使清洗後文章正文可進Obsidian與Search/RAG。

Decision 10已確認選擇A：第一版Linked Content Capture屬於同一次Google metadata sync／release build，metadata batch、CapturedContent revisions、Obsidian、SQLite／FTS與vector必須作為一個完整Release驗證並啟用；不得獨立refresh或publish capture revision。

Decision 11已確認選擇A：同一canonical URL發生temporary fetch failure時，符合previous success、policy／security／governance與freshness gates的Last Known Good可標為`stale`，隨本次metadata進入新的完整Official Release。

本規格只定義contract、DTO、policy、離線fixture、renderer與retrieval邊界；不授權本輪實作crawler／scraper、發HTTP request、連Google Sheets API、寫Vault、建index、啟動Slack或執行migration。

Authority分工如下：

- Google Sheets是Official metadata authority：決定資產是否存在、BRD／MREC／MET關係、asset type、title、source URL與governance metadata。
- linked webpage是Content Asset body的內容來源，不是identity、parent relation或governance authority。
- 完整canonical content representation由Google canonical metadata與相容的`CapturedContent` revision組成。
- Official Obsidian Markdown、SQLite／FTS與vector是canonical metadata與`CapturedContent`的sibling projections。
- 禁止 `URL → Markdown → parse Markdown → Official Index`；Markdown不是Official index authority。
- AI產生的query-focused summary是回答層derived content，不得覆寫、混入或冒充captured official body。

## 2. Actual Google Sheet link sources

正式來源Spreadsheet為「MKT 內容產出資料庫_店家/夥伴案例/對外數據」。

### 商家／夥伴案例資料庫

- C「商家／夥伴名稱」可能帶official website embedded hyperlink；它是Brand metadata／matching evidence，不自動視為article body capture target。
- H「文章」對應`article` Content Asset，標題可能帶embedded hyperlink，是Decision 9首要的Primary Content capture來源。
- I「影片」可能是標題、純URL、embedded hyperlink或缺URL；沒有另行核准的transcript／media extractor時只依capture policy標為metadata-only或unsupported。
- J「Podcast」可能含SoundOn等URL；不得把音訊頁面假裝成article正文。
- K「新聞」仍是MREC下的`news` Content Asset；若是HTML article且domain policy允許，可作Primary Content capture candidate。
- E／F Sales Category若來自公式，正文只使用effective／formatted value；formula string只作provenance。

### 「可公開」對外數據

- C「論述」是核准後Public Metric claim authority。
- F「參考新聞連結」可能是新聞標題加embedded hyperlink，只建立MET的evidence relationship，不建立新Public Metric claim。
- G-M checkbox仍決定既有governance channels；capture不得擴張其授權。
- oral-only繼續套用early minimization；被排除的claim、evidence與raw cell value不得進capture queue或任何artifact。

所有embedded link候選沿用既有擷取來源與Decision 8 URL safety順序：`textFormatRuns`、whole-cell hyperlink、`HYPERLINK` formula、cell text URL。候選先安全驗證、canonicalization與dedupe，再進link resolution。

## 3. Linked content roles

### 3.1 Primary Content Asset

典型關係：

```text
BRD → MREC → Content Asset → CapturedContent(authority_role=primary_content)
```

- Primary captured body可進Official Obsidian、全文chunks、FTS與vector，並可供query-focused summarization。
- citation必須回到原始article URL、`captured_content_id`、`asset_key`、MREC／BRD與source lineage。
- linked body不得改變`asset_key`或Google governance metadata。

### 3.2 Evidence Article

典型關係：

```text
MET → evidence relationship → CapturedContent(authority_role=evidence)
```

- Evidence body可供搜尋、證據查找與背景理解，但authority label必須是`evidence`。
- Evidence正文的數字或陳述不得自動升格為Public Metric claim。
- `approved_metric`只指Google Sheet C「論述」中通過governance的內容；F的新聞正文不得擴張可公開範圍。
- citation與query summary必須明確區分`approved_metric`與`evidence`，不得把兩者合併成單一authority。

## 4. CapturedContent model

Canonical DTO至少包含：

```yaml
captured_content_id: CAP-immutable-id
asset_key: MREC-0001:article
metric_id: null
evidence_relationship_id: null
authority_role: primary_content
source_url: https://example.com/story
canonical_url: https://example.com/story
source_domain: example.com
content_type: text/html
title: Original article title
clean_body: deterministic normalized article body
section_structure: []
capture_status: success
captured_at: timestamp
last_successful_capture_at: timestamp
last_capture_attempt_at: timestamp
content_hash: sha256:...
parser_version: html-normalizer-v1
source_http_metadata: {}
previous_content_hash: null
searchable: true
source_lineage: {...}
sync_batch_id: SYNC-...
```

Parent欄位採互斥規則：

- `primary_content`必須有`asset_key`，不得有`metric_id`或`evidence_relationship_id`。
- `evidence`必須有`metric_id`與穩定`evidence_relationship_id`，不得有`asset_key`。
- `source_http_metadata`只允許安全allowlist，例如status code、content type、ETag、Last-Modified與已驗證final URL；不得保存cookie、authorization header、token或credential。

Identity規則：

- `captured_content_id`是內部不可變logical capture ID，不得由抓取時間、URL、chunk position、run position或array index推導。
- Primary Content以`asset_key`作permanent parent identity；同一`asset_key`／role最多一條active capture lineage。
- Evidence Content以`metric_id + evidence_relationship_id`作permanent parent relationship；`evidence_relationship_id`須由受治理relationship registry穩定配置，不得直接等於URL或candidate ordinal。
- `canonical_url`只作reconciliation key；redirect或URL change須經驗證後更新relationship lifecycle，不得取代parent identity。
- 正文變更產生同一logical capture下的新revision；revision由`captured_content_id + content_hash + parser_version`辨識，舊revision保留lineage。
- 若Evidence relationship尚無穩定ID，候選只能留在staging／needs_review，不得宣稱為Official captured evidence。

## 5. Link resolution

Link resolver輸入Google canonical metadata與帶provenance的URL candidates，不直接抓取網頁：

1. 收集rich-text run、whole-cell hyperlink、`HYPERLINK` formula與cell text URL。
2. 套用URL syntax、public-host、credential／token與internal-path安全規則。
3. canonicalize safe candidates並以canonical URL dedupe。
4. Content Asset依Decision 8處理0／1／2+ distinct safe canonical URLs；2+時needs_review，不任選、不拆asset。
5. Public Metric F的每個approved evidence relationship獨立掛在同一MET下；不得將evidence URL轉成第二筆MET。
6. HTTP redirect只可在capture階段處理；每一跳重新套用SSRF／domain policy，最終URL再進reconciliation。

## 6. Capture policy

`capture_mode`只允許：

- `full_text`：允許擷取並清洗正文的candidate。
- `metadata_only`：只保存安全HTTP metadata與link metadata，不保存正文。
- `unsupported`：content type或來源能力未支援。
- `blocked`：安全、權限、政策或governance禁止。

Domain policy必須versioned、可配置、可audit且fail closed：

| Domain class | Default | 規則 |
| --- | --- | --- |
| SHOPLINE-owned | `full_text` candidate | 仍須通過實際HTTP、robots、法律／公司政策與content-type驗證；不能只因網域所有權就保證可抓。 |
| Approved third-party | policy-defined | allowlist逐domain指定`full_text`或`metadata_only`，並保存policy version。 |
| Unknown third-party | `metadata_only`／`needs_policy` | 不得自行假設可長期保存全文。 |
| Authenticated／paywalled／blocked | `blocked`或`metadata_only` | 不得登入、帶使用者cookie、繞過付費牆或規避技術限制。 |
| Unsafe／private／internal URL | `blocked` | 沿用URL safety policy，直接拒絕且不得回顯敏感完整URL。 |

本Audit不作法律結論；實際full-text domain allowlist啟用前仍需指定policy owner與核准紀錄。

## 7. Fetch boundaries

- Sprint 0不得發任何HTTP request，只定義fetch protocol、DTO、policy與synthetic fixtures。
- 後續fetcher只接受已驗證的public HTTP／HTTPS target，不接受任意URL或caller-provided headers。
- 不使用登入session、cookie、Authorization header、瀏覽器個人狀態、CAPTCHA bypass或paywall circumvention。
- 每次redirect重新驗證scheme、host、IP class、domain policy與redirect上限；禁止redirect到private／internal target。
- 設定response size、timeout、redirect count與允許content-type上限；超限fail closed。
- 不執行來源script，不把raw response寫入log／report；raw HTML若為短生命週期解析輸入，完成normalize後即丟棄。
- status、content type、ETag與Last-Modified等metadata只保存allowlisted、去敏後值。

## 8. HTML normalization

Captured body是清洗後的deterministic searchable text，不是raw HTML dump。

保留：

- title、headings、paragraphs、meaningful lists；
- 可可靠解析且不造成欄列錯置的meaningful tables；
- inline anchor text與section order；
- 必要的語言、結構與來源provenance。

移除：

- nav、footer、cookie banner、related posts、ads；
- scripts、styles、tracking text、social widgets；
- duplicated header／footer／boilerplate與不可見內容。

Normalization需固定Unicode、空白、換行、heading與list表示；同一HTML語義與同一parser version應產生相同`clean_body`與`content_hash`。Parser version改變時須可重跑parity review，不得把parser drift誤判成來源作者更新。

## 9. Content lifecycle and Last Known Good

`capture_status`至少包含：`success`、`stale`、`unavailable`、`blocked`、`metadata_only`、`needs_review`。

- Decision 11的LKG reuse gate必須同時成立：canonical URL與上一成功capture完全相同、存在先前`capture_status=success`的CapturedContent、本輪屬temporary fetch failure、沒有security／governance／capture policy阻擋，且LKG未超過核准freshness policy。
- Temporary failure可包含timeout、temporary DNS／network failure、HTTP 5xx與temporary 429；具體retry／error classification由後續versioned policy細化。Unsafe URL、`capture_mode=blocked`、不允許的authenticated／paywalled來源、governance rejection與identity reconciliation failure不是temporary failure。
- Gate通過時，candidate引用上一成功body，標`capture_status=stale`並沿用原`content_hash`、`captured_at`與`last_successful_capture_at`；`last_capture_attempt_at`記錄本輪attempt。不得清空正文、建立假hash／假revision、或把本輪記成成功capture。
- Source URL／canonical URL改變且新URLfetch失敗：新metadata依既有policy標`unavailable`／`incomplete`／`needs_review`；不得把舊URL正文掛到新URL，舊capture只作歷史lineage。
- 從未成功capture、identity無法可靠reconcile、LKG超過freshness policy，或freshness threshold尚未由核准policy配置時，LKG reuse gate fail closed；後續candidate狀態依既有capture／blocking policy，不得猜正文。
- normalized body hash改變：在本次完整Release candidate建立新revision、保存`previous_content_hash`並重建相關chunks／index；revision不得繞過完整Release validation獨立更新active projection。
- 本輪成功fetch且hash未變：可更新安全capture metadata與`last_successful_capture_at`，但不得製造正文revision；stale reuse不適用此條。
- permanent 404／removed：不可把Google parent當作deleted；capture lifecycle與Google source lifecycle分開。LKG是否繼續供search屬尚待明確化的retention policy，不在本輪猜定。
- `blocked`、`metadata_only`、`needs_review`或從未成功capture的record不得產生假的full-text chunks；符合Decision 11的stale LKG可沿用既有全文chunks，但所有projection與citation都必須標stale。

## 10. Obsidian rendering

Primary Article Markdown至少包含：

```yaml
record_type: content_asset
asset_key: MREC-0001:article
source_record_id: MREC-0001
brand_id: BRD-0001
asset_type: article
title: Original article title
source_url: https://example.com/story
canonical_url: https://example.com/story
capture_status: success
captured_at: timestamp
last_successful_capture_at: timestamp
last_capture_attempt_at: timestamp
content_hash: sha256:...
parser_version: html-normalizer-v1
sync_status: active
searchable: true
sync_batch_id: SYNC-...
sync_managed: true
```

Body固定包含原始文章標題、原始來源連結與保持合理heading結構的`clean_body`。Renderer只消費canonical metadata與`CapturedContent`，不得再從Markdown反推Official index。

不得把固定AI summary寫成Official正文。未來若需要machine-generated abstract，必須放在獨立derivative artifact或明確標示`derived_summary: true`，並保留其model／prompt／source chunk lineage；它不得取代captured body。

Evidence Article若投影到Markdown，必須使用不同record type或明確frontmatter：

```yaml
record_type: evidence_article
authority_role: evidence
related_metric_id: MET-0001
evidence_relationship_id: EVID-immutable-id
captured_content_id: CAP-immutable-id
capture_status: success
captured_at: timestamp
last_successful_capture_at: timestamp
last_capture_attempt_at: timestamp
searchable: true
sync_managed: true
```

Evidence檔案不得長得像Public Metric本體，不得宣告`approved_metric`，且body必須顯示「Evidence／背景來源」標籤。

## 11. Chunking and indexing

每個full-text chunk至少包含：

```yaml
chunk_id: deterministic-id
captured_content_id: CAP-immutable-id
asset_key: MREC-0001:article
metric_id: null
brand_id: BRD-0001
source_record_id: MREC-0001
authority_role: primary_content
title: Original article title
section_heading: Section title
chunk_ordinal: 0
source_url: https://example.com/story
capture_status: success
captured_at: timestamp
last_successful_capture_at: timestamp
last_capture_attempt_at: timestamp
content_hash: sha256:...
sync_batch_id: SYNC-...
```

- Chunker輸入`clean_body + section_structure`，不是Markdown檔案，也不是fixed summary。
- `chunk_id`由`captured_content_id`、content revision、stable section anchor與chunk text digest決定；不得使用runtime random ID。
- body與parser version不變時chunk identity應穩定；section重排仍保留parent ID，絕不能跨文章attribution。
- FTS與vector必須消費同一組全文chunks與metadata；title、tags或summary只能作補充欄位，不能是唯一retrieval corpus。
- Evidence chunks必須保留`authority_role=evidence`與`metric_id`；Primary chunks保留`asset_key`／MREC／BRD。
- Stale chunks沿用上一成功body與`content_hash`，不得生成假revision；index metadata必須標`capture_status=stale`並保留原`captured_at`／`last_successful_capture_at`，本輪attempt由parent CapturedContent的`last_capture_attempt_at`追蹤。

## 12. Retrieval and reranking

正式搜尋順序：

1. 先以active release、`searchable`、authority／governance metadata建立eligible corpus。
2. FTS keyword retrieval搜尋全文chunks。
3. vector semantic retrieval搜尋相同全文chunks。
4. 合併候選後先套status、authority與敏感資料filter；只有query明確要求特定usage channel時才套對應G-M與`can_quote_externally`。
5. 對通過gate的candidates dedupe並rerank。
6. 組裝含permanent IDs、source URL、lineage、capture freshness與authority role的citations。
7. 只將通過gate且與query相關的passages交給query-focused summarization。
8. Decision 6的`content_item_cap`／`metric_item_cap`與overall rendered budget只在response layer對reranked eligible results套用，不得縮小CapturedContent全文index／retrieval corpus；超限時只在完整item boundary停止，不得切斷citation或authority metadata。

禁止只搜尋Google Sheet title、預先summary或tags。任何orchestration層都不得繞過retrieval、reranking、citation、metadata、freshness note或status warning。

依Decision 2，Slack／internal search只是Official retrieval surface。Primary／Evidence通過authority、governance、`searchable`與capture release policy後即可被internal retrieval，不需要Slack-specific checkbox，也不套Public Metric G-M作persistence gate。Evidence可搜尋仍不等於`approved_metric`或任何exposure permission。

Stale不改變authority：Primary仍是`primary_content`，Evidence仍是`evidence`。Stale search result與citation必須揭露`capture_status=stale`、原`captured_at`、`last_successful_capture_at`與本輪`last_capture_attempt_at`；未來可依query freshness sensitivity降權或顯示warning，但本規格不設定ranking penalty數值。

## 13. Query-focused summarization

例如使用者詢問「有哪些品牌透過產品研發做數位轉型？」時：

1. 對全文articles／chunks執行metadata、FTS與vector retrieval。
2. 找到正文內涉及產品研發、製程、數位轉型等passages。
3. rerank並只保留與query相關且通過governance的chunks。
4. 回答層依這些passages產生query-specific synthesis。
5. 每項事實回鏈原始article URL、Content Asset與chunk lineage。

不得只輸出generic article summary、不得補入retrieved evidence未支持的內容，也不得將Evidence Article中的未核准數據表述為approved Public Metric。Query summary是ephemeral／derived answer；除非未來另有明確derivative policy，不寫回Google、captured body或Official正文。

## 14. Evidence authority and Public Metric boundary

- Public Metric claim text只來自Google Sheet C。Generic internal research可搜尋eligible Official Metrics並保留G-M metadata；只有特定對外usage intent的回答才受對應G-M、`can_quote_externally`與既有governance限制。
- F連結與captured Evidence Article只提供evidence／背景，不新增或改寫MET claim。
- Evidence內容可獨立回答一般背景問題，但citation label必須為`evidence`；若問題要求核准數據，只能以`approved_metric` record作claim authority。
- Evidence內容的公開性不得從MET channels推導；需同時通過其domain／capture policy與回答渠道policy。
- oral-only、pending、restricted或未核准MET不得藉Evidence capture繞過early minimization與publish gates。

## 15. Security, privacy, and sensitive content

- URL validation、SSRF防護與每次redirect revalidation為capture前置gate。
- 不保存credential、cookies、auth headers、signed tokens、session IDs或完整敏感query URL。
- domain allowlist不等於內容可公開；captured body仍須套資料分類、exposure與citation政策。
- capture reports只保留IDs、domain class、status、counts、hashes與redacted reason，不複製敏感正文。
- restricted customer、handle mapping、pending metric與oral-only payload不得進一般capture、Markdown、chunks、FTS、vector或Slack。
- synthetic HTML fixtures只使用抽象假資料，不複製正式公司或第三方文章正文。

## 16. Determinism and hashes

- `source_fingerprint`只代表Google Sheets canonical source state；不得包含HTTP response、captured body或capture timestamp。
- `capture_content_hash`／`content_hash`只代表parser-versioned normalized webpage body；不得替代Google source fingerprint。
- release manifest分別保存metadata batch、source fingerprint、capture policy version、parser version、captured revision IDs／hashes與artifact checksums。
- 同一body與parser version必須產生相同hash；不同body不得碰撞成同一active revision。
- `captured_at`與HTTP cache metadata不參與logical identity或body hash。

## 17. Release and freshness interaction

Google source snapshot consistency與linked web capture是兩個不同freshness domains：

```text
source_fingerprint = Google canonical metadata state
capture_content_hash = normalized linked webpage body state
```

兩種hash分離只代表lineage與diff語義不同，不代表第一版可以分開publish。Decision 10規定第一版使用單一完整Release boundary：

```text
Google Sheets snapshot
  → metadata normalization
  → link resolution / capture policy
  → capture / CapturedContent revisions
  → full-text chunking
  → sibling render: Obsidian + SQLite/FTS + vector
  → complete release validation
  → one active release activation
```

1. 每次release build取得並驗證Google F1／F2，capture只消費該build的明確`metadata_sync_batch_id`與approved safe links。
2. CapturedContent revisions、Obsidian body projection與全文index chunks全部寫入同一immutable release candidate。
3. `release_id`的manifest固定metadata batch、source fingerprint、每個captured revision／hash、Vault checksums、DB／FTS checksum與vector checksum。
4. Global active pointer只切換完整Release；不存在capture-only active pointer、單篇production activation、partial content release或獨立capture scheduler。
5. 符合Decision 11全部eligibility gates的same-canonical-URL temporary failure，可在candidate引用LKG body並隨本輪metadata進入新的完整Official Release；標`capture_status=stale`，保留原`captured_at`、`content_hash`與`last_successful_capture_at`，更新`last_capture_attempt_at`，不得記成本次成功fetch或建立新revision。
6. Release manifest必須固定stale CapturedContent revision／hash、status與capture／attempt timestamps；Vault、DB／FTS、vector及citation metadata必須對同一stale composition一致。這項允許不建立單篇或capture-only activation boundary。
7. URL改變且新fetch失敗時，舊URL revision只留歷史，絕不得掛到新URL或進新parent projection。

Captured revision模型仍保留，以支援history、diff、LKG與future migration；但第一版revision只能隨完整Release啟用。若未來需要高於Google sync頻率的網頁freshness，必須另開architecture／migration decision後才能引入`capture_batch_id`、capture scheduler、independent revision activation、partial content release、composition manifest、cross-revision rollback或stale replacement policy。

## 18. Tests

Sprint 0只使用synthetic CellData與synthetic HTML：

- embedded link四來源擷取、canonical dedupe與Decision 8 multi-link cases；
- Primary／Evidence role與parent互斥validation；
- domain class到capture mode的fail-closed matrix；
- auth／paywall／private／redirect-to-private拒絕contract；
- boilerplate removal、heading／paragraph／list／table preservation；
- deterministic normalization、parser version與content hash golden cases；
- same canonical URL＋previous success＋timeout／temporary DNS／network／5xx／429，通過policy與freshness gates後以stale LKG進candidate；
- stale reuse保持`content_hash`、`captured_at`與`last_successful_capture_at`，更新`last_capture_attempt_at`且不產生新revision；
- URL changed、never-successful、identity reconciliation failure不得沿用舊body；
- unsafe／blocked／auth／paywall／governance failure不得偽裝temporary；
- freshness threshold超限或尚未配置時reuse gate fail closed，不設定任意天數；
- stale manifest、Vault、FTS／vector與citation metadata一致，且只能隨完整Release啟用；
- content hash改變產生revision並重建chunks；
- chunk metadata／identity穩定與跨文章attribution guard；
- FTS／vector都能檢索全文而非只檢索summary；
- query-focused answer只使用retrieved passages並保留citation；
- Evidence數字不得成為approved metric assertion；
- Markdown bytes與index rows由同一canonical inputs產生，修改Markdown不改Official index candidate；
- `source_fingerprint`不受captured body變化影響，capture hash不受Google response envelope影響；
- sensitive sentinel不出現在HTTP metadata、logs、reports、Markdown或index。

## 19. Rollout

- Sprint 0：只新增`CapturedContent`／`CapturePolicy` DTO、fetch protocol、synthetic HTML fixtures、HTML normalization、chunk metadata與hash contracts；零HTTP。
- 後續既有Sprint 1-2保留Google metadata dry-run與ID治理，不接capture network。
- Sprint 3在temporary candidate Vault驗證captured Markdown renderer與Evidence authority標示。
- Sprint 4加入由canonical metadata＋`CapturedContent`直接產生的Official full-text FTS／vector與RAG parity。
- Sprint 5把release-pinned capture revision set、LKG與雙hash manifest納入單一完整Release／rollback rehearsal；不建立capture-only scheduler或pointer。
- 正式HTTP另案先做SHOPLINE-owned domain controlled dry-run，再依核准policy擴及第三方；任何production capture與migration都需明確授權。
- Future independent capture refresh、partial release與cross-revision rollback明確排除於第一版；若需要，須另開architecture／migration decision。

## 20. Open questions

以下不改變Confirmed／Remaining Decisions清單，實作前仍需政策owner留下明確紀錄：

- Approved third-party domain allowlist、review cadence與法律／robots核准責任人。
- Evidence relationship ID的配置、registry保存與URL reconciliation操作流程。
- 首次capture unavailable時，metadata release是否可active但不搜尋全文。
- LKG stale freshness threshold的具體天數、按content／domain分類方式、policy owner與review cadence；LKG不得無限期沿用。
- Query freshness sensitivity如何觸發stale warning或ranking penalty，以及penalty數值；未裁決前不得自行設定。
- permanent 404／removed後LKG全文的保留期限與search eligibility。
- JavaScript-rendered頁面、非HTML文件、影片／Podcast transcript是否支援；未核准前一律metadata-only或unsupported。
- Capture failure／stale若構成需要人工介入的final sync／release operational failure，主要alert surface依Confirmed Decision 5使用Private Slack Ops，且只能傳sanitized stage／category／affected count，不得包含CapturedContent或Evidence body。Retry warning、notification retry／backoff與Email fallback仍是future operational policy。
