# Implementation Roadmap and Test Plan

## 原則

- 每個Sprint保留現行active path，先dry-run/dual-run，再切換；不得一次重寫import、Vault、index與Slack。
- production adapter的認證架構已確認為read-only Service Account；Decision 4另確認permanent ID governance writer採external standalone Apps Script。兩條權限路徑必須隔離；建立憑證、正式連線、writer實作／部署與通知仍須各自另案授權。
- Linked capture另受Decision 9與`10_LINKED_CONTENT_CAPTURE_AND_RAG_SPEC.md`約束；Sprint 0只做DTO／policy／synthetic HTML，禁止HTTP。
- Decision 10規定第一版linked capture隨Google metadata sync建立完整Release；不得建立capture-only scheduler、active pointer、單篇production refresh或partial content release。
- Decision 11允許符合same canonical URL、previous success、temporary failure、policy與freshness gates的LKG以`stale`進入新完整Release；具體freshness threshold與ranking penalty仍待operational policy，不得猜定數值。
- Decision 2規定Slack／internal search是Official retrieval surface，不是exposure channel；generic internal retrieval不要求任一G-M為true，只有明確usage intent才套對應channel，且第一版不新增Slack checkbox／N欄。
- Decision 3規定Manual Enrichment的canonical `approved_by`必須精確命中note外部受控authorized approver whitelist；任何identity／whitelist failure及substantive approval hash mismatch都fail closed，且不影響Google Official pipeline。
- Decision 4規定standalone writer只處理MREC／MET與已核准BRD mapping，固定explicit allowlisted Spreadsheet ID與欄位白名單；不得擴張成generic Google writer或sync／capture元件。
- Decision 5規定Private Slack Ops是第一版final sync／release operational failure的primary alert surface；release結果先durable、通知後執行且狀態分離，與Decision 2 Slack internal search不同。
- Decision 6規定Content Asset與Public Metric使用獨立item caps，再套overall rendered message budget；所有caps位於post-governance／post-intent／post-rerank response layer，不縮小index corpus，也不在Sprint 0實作pagination。
- 每個Sprint遇到stop condition即停止，不以skip flag繞過。
- 測試一律使用synthetic CellData、temporary Vault/DB與抽象資料；不得複製正式oral/restricted/pending內容。

## Sprint 0：契約與安全測試基座

### Scope

- 建立Google reader protocol、CellData DTO、canonical serialization與synthetic fixtures。
- 定義BRD/MREC/MET/ENR、lineage、lifecycle、publish eligibility。
- 定義CapturedContent、CapturePolicy、fetch protocol、content revision與chunk metadata contract。
- 建立早期oral-only redaction與URL safety validator。
- 建立synthetic HTML normalization與content hash fixture；不建立production auth／fetch adapter，不發HTTP，不寫Vault/index。

### Inputs

- 本審查文件、tracked現行models/governance/tests、人工合成CellData。

### Outputs

- versioned canonical schema；
- redacted normalization result；
- source fingerprint function；
- CapturedContent／CapturePolicy DTO、deterministic normalized body／content hash與chunk metadata schema；
- validation report schema與error codes。

### Tests

- formula/effective/formatted、rich text、merge、checkbox/data validation；
- 同一URL由rich-text run、whole-cell hyperlink、`HYPERLINK` formula或cell text等多來源取得時，canonicalization後dedupe成一個distinct safe canonical URL；
- 同一asset cell有兩個distinct safe canonical URLs時產生needs_review，不任選也不進Official publish set；
- 多個URL candidates不得產生兩個相同asset type的Content Assets，每個MREC／asset type仍最多一筆；
- Content Asset identity固定為 `<MREC>:<asset_type>`，URL、Rich Text run position與candidate array index變動不得改變identity；
- URL attack table；
- row reorder/insert後ID不變；
- oral-only sentinel不出現在任何serialized result/log/exception；
- deterministic fingerprint golden tests；
- synthetic HTML保留heading／paragraph／list／可靠table並移除nav／footer／cookie／ads／script／boilerplate；
- Primary／Evidence parent互斥、authority label與evidence不升格metric；
- domain policy fail-closed、auth／paywall／unsafe target拒絕contract；
- captured body hash、revision、LKG與source fingerprint分離；
- synthetic failure classifier將timeout、temporary DNS／network、HTTP 5xx與temporary 429列為可進LKG eligibility evaluation，並將unsafe／blocked／auth／paywall／governance rejection排除；
- freshness policy缺失或LKG超過核准threshold時reuse gate fail closed，不自行設定天數；
- full-text chunks可供FTS／vector，fixed summary不是唯一retrieval內容；

### Acceptance criteria

- 全部離線測試通過；
- parser無raw response dump；
- 零HTTP／network call，synthetic fixture不含正式或第三方文章正文；
- permanent ID與lineage完全分離；
- Query明確要求但無法映射既有G-M的usage channel時fail closed；generic internal retrieval不要求channel permission。

### Rollback criteria

- 只新增未接線模組；移除feature flag/entry即可，不影響現行CLI。

### Stop conditions

- 無法以CellData表達既定merge/link/checkbox契約；
- oral-only正文進任何persistable object；
- identity仍依row/path；
- CapturedContent以URL／capture time／chunk position作identity，或source fingerprint混入web content hash。

### 人工核准點

- canonical schema與redaction report是否足以追查但不洩漏。

## Sprint 1：Read-only snapshot dry-run與品牌初始化

### Scope

- 依已確認的read-only Service Account架構實作production Google adapter；Spreadsheet授權與Google API scope均限唯讀，adapter不得取得write scope。
- 擷取必要hidden sheets/ranges與F1，不寫回。
- 產生品牌candidate grouping、ID缺漏/衝突及redacted diff preview。
- Standalone Apps Script writer仍不在本Sprint實作。

### Inputs

- 已確認的read-only Service Account認證架構、另案核准且不進Git／Audit／Obsidian／log的credential供應方式、Spreadsheet ID、Sprint 0 schema。

### Outputs

- snapshot manifest/hash；
- 品牌ID初始化審核artifact；
- baseline/count/source-health report；
- 不含正文的exclusion report。

### Tests

- mocked API pagination/partial response/permission error；
- hidden sheet與fields mask contract；
- F1/F2 helper；
- handle/website unique/ambiguous/conflicting grouping；
- formal workbook只跑read-only dry-run並由人確認counts。

### Acceptance criteria

- 零Google write scope；
- 不產生Vault/index；
- 所有critical sheet/header/count驗證可重現；
- brand suggestions不自動配置BRD。

### Rollback criteria

- 撤銷read credential與adapter設定；現行local Excel flow不受影響。

### Stop conditions

- API response無法保證完整CellData；
- baseline不符、ID重複或品牌ambiguity未進review；
- raw snapshot被寫入report/debug。

### 人工核准點

- 品牌candidate approve/split/merge/exclude；
- 正式baseline與source-health threshold草案。

## Sprint 2：Standalone ID配置治理與normalized batch

### Scope

- 依已確認的Decision 4另案實作external standalone Apps Script MREC／MET allocator及已核准BRD mapping的controlled backfill；不建立Spreadsheet-bound script。
- Production target只接受explicit configured且allowlisted的Spreadsheet ID `15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM`，不得使用active spreadsheet identity／fallback。
- 寫入面只開放商家／夥伴M／N／O與Public Metric N；不暴露任意Spreadsheet／sheet／range或generic mutation API，且不承擔sync、capture、Markdown／index、Slack／RAG或exposure決策。
- importer驗證純文字、immutability、registry與brand master。
- 建立完整normalized batch與archive diff，但仍不發布。

### Inputs

- 核准品牌decision、固定target與write allowlist、ID欄位及Decision 4 contract；`clasp`／CI/CD／deployment identity等open implementation details須另案確認。

### Outputs

- ID allocation audit；
- brand master/readback validation；
- immutable normalized candidate；
- create/update/incomplete/archive/restore preview。

### Tests

- duplicate/reused/mutated/format/formula ID；
- MREC空白且allocator／registry state有效時，該列可取得下一個safe MREC；
- 正確configured＋allowlisted canonical Spreadsheet ID可進validation／allocation，錯誤、缺失、不符或unreadable ID一律fail closed；
- production target resolution不得呼叫或fallback到 `SpreadsheetApp.getActiveSpreadsheet()`；
- existing valid MREC／MET與BRD絕不覆寫；
- duplicate ID使整次allocation fail closed，不得挑新號掩蓋；
- malformed／unknown namespace／unsafe next sequence產生needs_review／blocking；
- active、archived與reserved registry共同參與next-ID validation，retired ID不得重用；
- concurrent allocator executions以exclusive guard＋guard後重讀＋同一critical section allocation／reservation／write，property test證明不產生duplicate；
- row reorder、rename、Handle/URL change不改ID；
- archive/restore同ID；
- MET allocation使用獨立MET namespace與sequence，不得與MREC prefix／registry混用；
- BRD空白且identity uncertain時不得自動建立、merge、split或歸屬；already-approved mapping存在且format／duplicate／conflict validation通過時才可受控回填；
- 商家／夥伴M／N／O及Public Metric N以外的write被拒絕，任意Spreadsheet／sheet／range generic mutation request亦被拒絕；
- Decision 2 schema regression：G-M不被writer修改、不新增Slack欄位且MET仍在N；
- Marketing Knowledge Agent read path仍使用獨立read-only adapter／credential contract，無法透過Apps Script writer取得一般write能力；
- write後readback／verification failure回傳audit-safe failure並fail closed；
- mass deletion/API truncation fail closed。

### Acceptance criteria

- 每個publishable MREC/MET/BRD均有效；
- registry conservation通過；
- 未核准BRD不進publishable set；
- writer只能命中固定Spreadsheet與欄位白名單，read-only Service Account／Marketing Knowledge Agent仍無write capability；
- archive仍只預覽。

### Rollback criteria

- ID不可回收或重配；錯誤配置須凍結並人工修復，不以rollback重用號碼。

### Stop conditions

- allocator可能覆寫現有ID；
- target不是canonical Spreadsheet、以active spreadsheet作fallback、write超出欄位白名單或存在generic mutation surface；
- concurrency guard／current-state re-read／reservation／write verification任一無法證明fail closed；
- 品牌自動merge；
- archive候選無完整snapshot證明。

### 人工核准點

- 首批ID與brand master；
- 首批所有archive candidates。

## Sprint 3：Obsidian sibling renderer

### Scope

- 從normalized batch產生新entity tree、Wiki Links、incomplete與manifest。
- 從synthetic／candidate CapturedContent產生Primary／Evidence Markdown，保留clean body與authority label；不發production HTTP。
- 在temporary candidate Vault dual-run；不碰正式Vault。
- 明確sync-managed/manual boundary。

### Inputs

- Sprint 2 immutable batch、CapturedContent synthetic／approved dry-run artifacts。

### Outputs

- candidate Obsidian tree與checksums；
- legacy vs new projection comparison；
- filename/path migration map；
- captured Markdown parity report與Primary／Evidence authority report。

### Tests

- 每entity一檔、frontmatter schema、referential links；
- unicode/case/path collision；
- deterministic bytes、idempotent rebuild；
- incomplete移轉active、archive/restore；
- namespace escape與manual note preservation；
- captured frontmatter/body、Evidence label、無fixed AI summary與Markdown不回讀index。

### Acceptance criteria

- 不解析Markdown作authority；
- managed projection conservation=canonical publish set；
- 99 manual root零mutation；
- sensitive sentinel掃描零命中；
- captured Markdown與canonical body/hash一致；Evidence不冒充Public Metric。

### Rollback criteria

- candidate tree獨立，可直接棄置；未切換active Vault。

### Stop conditions

- filename被當identity；
- dangling/ambiguous Wiki Link；
- manual note被修改。

### 人工核准點

- Vault資訊架構、命名與首批render preview。

## Sprint 4：Official indexes與Enrichment index

### Scope

- Official SQLite/FTS/vector直接消費normalized metadata與CapturedContent revisions，全文chunks不經Markdown。
- Enrichment獨立parser/index、semantic approval hash與authorized approver exact-membership gate。
- 保留legacy Markdown-derived index做dual-run比較，不再擴充。
- 另案授權後，先以SHOPLINE-owned domains做controlled fetch dry-run；第三方domain未有versioned policy前維持metadata-only／needs_policy。

### Inputs

- canonical metadata batch、synthetic或另案核准的CapturedContent revisions、Manual Enrichment synthetic/temporary fixtures與injected synthetic approver authority contract。

### Outputs

- candidate Official DB/vector；
- candidate Enrichment DB/vector；
- parity與governance report；
- Enrichment approval eligibility report（不複製note正文或whitelist敏感內容）；
- full-text retrieval／reranking／query-focused summary parity report。

### Tests

- schema/foreign key/FTS/vector conservation；
- Official-only authority；
- incomplete/archived/oral/restricted/pending全排除；
- enrichment substantive edit失效、format-only edit不失效；
- `review_status=approved`、canonical `approved_by`精確命中whitelist且其他條件有效時eligible；
- `review_status=approved`但`approved_by`為任意非空未授權值時ineligible；missing／empty亦ineligible；
- `searchable=true`不得讓invalid `approved_by`繞過approval gate；
- whitelist無法載入或malformed時整個Enrichment eligibility fail closed，不fallback成非空字串檢查；
- substantive change後即使保留舊`approved_by`也不得沿用approval；formatting-only change依既有semantic fingerprint contract；
- enrichment note自行宣告whitelist／authorized approver時不得取得authority；
- Google Sheets Official record不受Manual Enrichment approver whitelist影響；
- Official/Enrichment collision不覆蓋；
-現行 typed retrieval/citation regression suite；
- Primary與Evidence全文可由FTS／vector搜尋、rerank與citation；
- query-focused summary只使用retrieved passages，Evidence數字不升格approved metric；
- Markdown變更不影響canonical full-text index candidate。

### Acceptance criteria

- 所有index rows可追到permanent ID與batch；
- Markdown修改不改Official candidate；
- Enrichment必須明確opt-in；
- 每筆indexed Enrichment都能證明review status、canonical approver exact membership、approval timestamp、semantic hash、searchable與allowed channel gates通過；
- byte/sentinel scan通過；
- title／tags／fixed summary不是唯一retrieval corpus，所有captured chunks可追到content hash與authority role。

### Rollback criteria

- candidate index獨立；active path仍指向legacy DB。

### Stop conditions

- 任何index從Markdown重建Official；
- authority layer靠可省略filter才安全；
- substantive enrichment變更仍被索引；
- invalid／unknown approver、whitelist unavailable／malformed或self-declared authority仍可進Enrichment index；
- Manual Enrichment approver gate被錯套到Google Official record；
- index仍只搜尋precomputed summary，或Evidence與approved metric authority混淆。

### 人工核准點

- canonical reviewer ID scheme、whitelist authority interface／storage design與首批Enrichment validation結果；historical approval revocation semantics另案決定。

## Sprint 5：Release coordinator與rollback rehearsal

### Scope

- source F2、single-writer lock、commit journal、candidate checks、active pointer與last_success。
- Vault/Official DB/vector整批activation與recovery。
- release-pinned capture revision set、Decision 11 stale LKG candidate reuse與Google／capture雙hash manifest；所有內容隨完整Release build。
- scheduler依已確認政策執行：Asia/Taipei 09:00、09:30、10:00，總共最多3次attempt，不存在10:30第四次執行。

### Inputs

- 所有candidate builders、已確認的排程attempt政策，以及Decision 5 Private Slack Ops notification contract；實際workspace／channel／App／credential仍須另案授權。

### Outputs

- generic release coordinator；
- crash recovery/rollback command；
- batch manifest（含stale capture與attempt lineage）、獨立`release_status`／`notification_status` operation record與sanitized Slack Ops notification contract；
- 單一完整Release active pointer與journal，固定metadata batch、capture revisions、Vault、DB／FTS及vector composition。

### Tests

- 每個commit step故障注入；
- process kill/restart recovery；
- 三次attempt各自重新取得／驗證source fingerprint，並覆蓋F1/F2 mismatch持續至第3次的情境；
- Attempt 1失敗時durably記錄retry state，不宣告final batch failure，也不要求primary Slack Ops failure alert；
- Attempt 2失敗時同樣維持retry state，不宣告final failure；Attempt 2提前warning仍是future policy；
- Attempt 3仍失敗時先durably記錄`release_status=failed`，之後才eligible嘗試Private Slack Ops alert；
- Release failed且Slack send成功時保持`release_status=failed`, `notification_status=sent`；
- Release failed且Slack send失敗時保持`release_status=failed`, `notification_status=failed`，原始failure evidence不得被覆蓋；
- Release成功後若未來選擇發optional success notification而send失敗，active Release不rollback，`release_status=success`保持不變；本測試不表示第一版強制success alert；
- Oral-only governance failure的Ops payload只含sanitized count／reason，不含claim／body；restricted failure同樣不得含restricted raw content；
- Exception含credential／API key／OAuth／Service Account／Slack token、signed URL、Authorization header或environment secret時，sanitizer必須在alert前移除；
- Slack target缺失、invalid或未命中private Ops allowlist時不得任意送出，`notification_status=failed`且release結果不變；
- Slack internal search request／response不套用Ops alert schema、target或authorization，兩者不得被視為同一surface；
- Linked capture failure alert可含stage、sanitized category與affected count，不含HTML／CapturedContent／Evidence body；
- disk full/permission/checksum/SQLite corruption；
- mass deletion blocked時active全部不變；
- rollback rehearsal恢復上一成功batch；
- same canonical URL＋previous success＋temporary timeout／DNS／network／5xx／429時，符合policy與freshness gates的LKG以`stale`進candidate並可隨完整Release啟用；
- stale reuse保持原`content_hash`、`captured_at`與`last_successful_capture_at`，更新`last_capture_attempt_at`，且不建立新正文revision；
- URL changed＋failure、never-successful URL＋failure或identity無法reconcile時不得沿用舊body；
- unsafe／blocked／auth／paywall／governance failure不得偽裝成temporary failure；
- LKG超過freshness threshold時reuse gate fail closed並產生明確policy result；未配置核准threshold時同樣fail closed；
- manifest、Vault frontmatter、Official DB／FTS／vector與citation metadata一致標記同一stale capture；
- content hash change建立真正revision並重建chunks；
- captured body變化不改Google F1／F2，Google metadata變化不被HTTP response誤判。
- 單篇revision不得獨立更新production Vault／FTS／vector，且不存在capture-only schedule／pointer。

### Acceptance criteria

- 任一blocking error零partial active state；
- last_success永遠指向完整可讀release；
- startup可自動辨識並安全恢復prepared journal；
- notification不含敏感正文；
- final release／batch result在任何Slack call前durable；`release_status`與`notification_status`可獨立重建且notification failure不改active pointer；
- production notification target只能是configured＋allowlisted private operational destination，不接受任意channel或public／search-thread fallback；
- fingerprint不一致的attempt不得publish、archive或更新active release；第3次仍失敗時batch狀態為`failed`。
- 完整Release activation保證Obsidian／FTS／vector引用同一content revision set；符合Decision 11的temporary failure不清空LKG，且stale不得被標成success。
- active release恰好對應一個metadata batch與其release-pinned CapturedContent revisions；沒有partial release composition。

### Rollback criteria

- active pointer、Vault backup與journal可還原previous batch；rollback後再跑smoke tests。

### Stop conditions

- 任一故障可能讓Vault與index指向不同batch；
- rollback未實際演練；
- F2未重驗仍可commit；
- metadata batch與capture revision不相容仍可active，或任何partial capture projection可見。
- capture revision可繞過完整Release validation獨立activate，或存在第一版capture-only scheduler／pointer。
- Slack send位於release transaction內、notification success成為durable failure前提，或notification failure可rollback／改寫release結果。
- Ops payload可能包含oral-only／restricted／captured body、claim、credential／token／secret、signed URL、raw response或unredacted stack trace。
- Ops target可由任意channel ID指定、fallback到public／general channel或與Slack internal search thread混用。

### 人工核准點

- rollback rehearsal evidence、mass-deletion thresholds、正式schedule enable，以及Private Slack Ops target／authorization／message-schema security review。Notification retry／backoff、dead-letter、Email fallback與success alert policy另案處理。

## Sprint 6：Slack Official／Enrichment切換

### Scope

- Slack repository依active release讀Official；明確include時才讀Enrichment。
- 分離Official persistence／search eligibility與query-intent usage／exposure permission，保留authority label、allowed channels、pagination/caps與safe audit。
- 依Decision 6在eligible reranked results上分別套`content_item_cap`／`metric_item_cap`，再套overall `rendered_message_budget`並只render完整item。
- 不改寫retrieval/generation的citation/governance核心。
- 回答層對captured全文做query-focused summarization，derived answer不寫回Official body。

### Inputs

- 已確認Decision 2兩層治理、Decision 6 output budget contract，以及active release APIs；具體cap值、budget單位與pagination UX留待可配置的Slack rendering validation。

### Outputs

- Official-default Slack search；
- optional Enrichment results；
- record-type item caps、overall rendered budget與atomic result／eligible more-results metadata contract；
- migration/rollback feature flag與operational runbook。

### Tests

- default query永不回Enrichment；
- explicit include才顯示且標內部補充；
- oral-only與pending不進Official Index；restricted不進一般Official Search並保留既有restricted governance；
- eligible Public Metric在Saleskits=true、website=false時，generic internal query仍可retrieval，且result保留allowed channels；
- 同一metric的website usage intent被website=false擋下，Saleskit usage intent則因Saleskits=true可使用；
- oral-only即使J「口頭說明」為true，也完全不出現在Markdown、Official DB／FTS／vector或Slack result；
- Primary Article通過authority／governance／`searchable`／capture release policy後，不需Slack-specific checkbox即可internal retrieval；
- Evidence Article可搜尋仍維持`authority_role=evidence`，不得升格`approved_metric`或擴張G-M；
- generic query不套G-M persistence gate，channel-specific query才套requested channel、status、`can_quote_externally`與citation authority；
- generic Public Metric query先完成governance filtering與rerank，再套獨立`metric_item_cap`與overall rendered budget；
- channel-specific metric query先移除channel=false results，再rerank／cap，排除項不得占metric quota；
- oral-only、restricted、pending、non-searchable與其他governance-ineligible results不得占任何display quota；
- Content Asset與Public Metric使用不同item cap，不能只共用單一total-results count；
- metric item count尚未達cap但overall rendered budget已滿時，只停在完整item boundary；
- 下一筆完整item放不下時不截斷claim，回傳`more_results=true`或等價signal；citation、identity、authority與allowed-channel metadata不得與item分離；
- Public Metric cap不改Official Metric index corpus size，Content cap不改全文Article indexing／searchable chunks；
- cap只選reranking後的top eligible results，不按source row／permanent ID numerical order截取，tie-breaker除外；
- shown／remaining count由post-governance、post-intent eligible set計算，不計被治理排除的records，且沿用更嚴格sensitive aggregate policy；
- Private Slack Ops notification不套knowledge-result item caps、rendered budget或pagination semantics；
- no-result與more-results提示；cursor／page token／TTL／button／message splitting只作future implementation，不在本Sprint預先固定；
- audit只存hash/IDs/decision codes；
-既有Slack external-intent/denylist/citation regression suite；
- captured全文問題能命中相關passages而非只回generic summary；
- Primary／Evidence citations分流，Evidence內容不得冒充approved metric。

### Acceptance criteria

- Slack不讀legacy Markdown-derived index；
- Official/Enrichment citations不混淆；
- generic internal result保留`allowed_exposure_channels`且不暗示全渠道可用；
- output caps只消耗post-governance eligible results，Public Metric與Content quota獨立且所有rendered items在budget內保持claim／citation／authority完整；
- display cap不改Official corpus、FTS／vector或retrieval candidate counts；
- cap／budget數值與計量方式保持configurable，不把示例數字當production policy；
- feature flag可即時回上一active search path；
-不啟用外部LLM也可完整離線測試；
- query-focused answer保留source URL、permanent IDs、chunk lineage與capture freshness warning。

### Rollback criteria

- 切回previous active release/repository flag，不需重建資料。

### Stop conditions

- H「自媒體」或任一G-M被默認當Slack permission，或新增Slack-specific persistence gate；
- generic internal query要求任一G-M為true，或channel-specific query未套requested channel；
- 在governance／intent／dedupe／rerank前先cap、以單一shared item count限制Content與Public Metric，或讓被排除result占quota；
- renderer在item中間截斷claim／citation／identity／authority／channel語義，或remaining count包含治理排除資料；
- display cap縮小ingestion／Official Index／full-text corpus，或Decision 6被擴張成Sprint 0 cursor／TTL／interactive pagination實作；
- knowledge-result caps被套到Decision 5 Private Slack Ops alert；
- raw query或敏感內容進persistent audit；
-新renderer繞過現行citation/status/freshness warning；
- query summary補入retrieved evidence未支持內容或覆寫captured official body。

### 人工核准點

- staged Slack output review；cap值、rendered budget計量與more-results UX須依實際Slack payload調校，正式Bot切換需另行明確授權。

## 跨Sprint必跑回歸

- `models`, `governance`, `query_gating`, `retrieval`, `reranking`, `generation`, `structured_results`；
- Excel preview/apply/asset metadata既有測試，直到legacy flow正式退場；
- Obsidian plan/execute/rollback；
- content index與typed query；
- CapturedContent／HTML normalization／capture policy／LKG／full-text chunk與query-focused summary；
- 完整Release composition與「revision保留lineage但不可獨立activate」contract；
- Slack renderer/interface/external governance；
- destructive migration、production Vault/DB、Google/Slack API、HTTP capture與external LLM一律需獨立明確授權。
