# Decisions Required

本文件分為已正式確認與仍待決定的產品／治理選擇。Confirmed Decisions共11題；Remaining Decisions為None。

## Confirmed Decisions

### Decision 1 — Google Sheets API認證

**已確認：A — Read-only Service Account。**

正式規則：

1. Google Sheets正式同步使用專用Service Account。
2. Spreadsheet只授予該Service Account讀取權限。
3. Google API scope必須採最小唯讀權限。
4. Service Account不得擁有寫入Spreadsheet的能力。
5. 正式排程必須可無人值守執行。
6. Credential、token或service account secret不得commit至Git、寫入Audit文件、寫入Obsidian或輸出到log。
7. 若公司政策禁止長期JSON key，優先使用公司核准的Secret Manager、Workload Identity或等價無長期金鑰方案。
8. 本決策只確認認證架構，不授權本輪建立憑證或連接正式API。

### Decision 2 — Slack／Internal Search Governance

**已確認：C — Slack／internal search是Official Knowledge的內部retrieval surface，不是新的exposure channel。**

正式規則：

1. 第一版不新增Slack／internal_search checkbox、不新增N欄Slack permission、不移動MET ID，也不建立Slack-specific source permission或persistence gate。
2. 「可公開」對外數據G-M維持既有usage／exposure語義：G新聞稿、H自媒體、I Saleskits、J口頭說明、K演講簡報、L官網／招募網站、M廣告；不得把H「自媒體」映射成Slack permission。
3. 第一層persistence／search eligibility決定record能否存在Official Knowledge／Index。Oral-only永遠不可持久化或進Markdown／SQLite／FTS／vector／logs／fixtures／report body；pending不進Official Index；restricted不進一般Official Search；eligible written Public Metric及Primary／CapturedContent依既有authority、governance與`searchable`規則判定。
4. 第二層usage／exposure permission只在query明確要求特定用途時套用對應G-M。Generic internal research不要求任一G-M為true；某channel為false只阻擋該用途，不使eligible record從internal retrieval消失。
5. Generic Public Metric query可搜尋eligible Official Metrics，result metadata必須保留`allowed_exposure_channels`且不得暗示全渠道可用。Saleskit、官網、廣告等usage intent則必須分別要求對應permission為true。
6. Answer eligibility至少考慮authority role、persistence eligibility、governance status、`searchable`、query intent、requested usage channel、G-M、linked capture freshness與citation authority。
7. Public Metric claim authority仍只來自Google Sheet C「論述」。Evidence Article可搜尋不等於`approved_metric`，不得建立新claim或擴張G-M；channel-specific generation只能使用符合該channel的approved metrics。
8. Primary Article與Evidence Article共用internal retrieval surface，但各自保留authority與governance語義；其search eligibility依authority、governance、`searchable`及capture status／release policy，不依Public Metric G-M或Slack checkbox。
9. 本決策不授權本輪修改Google Sheet、實作Slack／retrieval、執行migration或新增實際欄位。

### Decision 3 — Manual Enrichment Approver Validation

**已確認：A — Manual Enrichment的`approved_by`必須精確命中明確的authorized approver whitelist。**

正式規則：

1. Approved eligibility至少同時要求`record_type=manual_enrichment`、`review_status=approved`、`searchable=true`、符合既有approval contract的`approved_at`、允許`internal_search`的`allowed_channels`，以及其他既有governance條件。
2. `approved_by`必須是單一非空stable canonical reviewer ID，並以deterministic exact membership命中authorized approver whitelist。任意非空字串、display name／暱稱／自由文字、substring／fuzzy matching及大小寫／格式猜測都無效；無法精確確認時fail closed。
3. Whitelist authority必須位於enrichment note之外的單一受控configuration／governance source；note不得透過frontmatter宣告、覆寫或新增approver，parser／renderer亦不得散落hardcode名單。具體storage location仍是implementation/open design item。
4. `approved_by`缺失／空白／未授權／identity無法exact match、whitelist無法載入／設定無效，或`review_status`不是approved時，不得進Enrichment Index；`searchable=true`不能繞過approval，也不得fallback成只檢查非空字串。Markdown可留在Vault但search eligibility fail closed。
5. Substantive content／governance change使既有approval失效；即使舊`approved_by`仍在frontmatter，也必須重新核准後才能回Enrichment Index。Formatting-only change沿用既有semantic fingerprint contract，本決策不重新裁決。
6. Approval仍由人工frontmatter表達，不新增approval CLI。Decision 3只適用Manual Enrichment，Official Index與Google Sheets Official records不受此whitelist gate影響；Enrichment不得覆寫或冒充Official。
7. Canonical reviewer ID必須可作exact membership validation，但其scheme尚未指定為email、GitHub handle、Slack ID、username或employee ID，保留為open design item。
8. Reviewer後續移出whitelist時，歷史approval採retroactive revocation或approval-time authorization snapshot尚未裁決，明列為future governance decision；這不影響新approval必須命中當前authorized whitelist的已確認規則。
9. 本決策只定義contract，不授權本輪建立whitelist設定檔、實作validator／CLI、修改Vault或建Enrichment index。

### Decision 4 — Apps Script Deployment Model

**已確認：B — Permanent ID allocator／governance writer採external standalone Apps Script project。**

正式規則：

1. 不採Spreadsheet-bound script。Google Sheets是business metadata與reviewed governance authority；GitHub是Apps Script source code與specification authority；standalone deployment是受控execution target。Script editor drift不是authority。
2. Apps Script只負責MREC allocation／validation、MET allocation／validation、already-approved BRD mapping controlled backfill、duplicate／malformed／missing validation、write guard、write verification及audit-safe result／status。
3. Apps Script不得負責Google metadata sync／extraction、link resolution、HTTP／HTML capture、CapturedContent、Markdown／Obsidian、SQLite／FTS／vector、Slack／RAG、query answering、exposure decisions或Manual Enrichment approval。
4. Production canonical Spreadsheet ID固定為 `15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM`，必須explicitly configured且allowlist命中。缺失、無效、不符或unreadable時fail closed；不得以 `SpreadsheetApp.getActiveSpreadsheet()`作identity、fallback或target discovery。
5. Write allowlist只有「商家／夥伴案例資料庫」M（MREC）、N（BRD）、O（ID Review Status），以及「可公開」對外數據N（MET）。不得寫其他business columns或G-M，不新增Slack欄位、不移動MET，也不得提供任意Spreadsheet ID、sheet、range或generic mutation API。
6. MREC／MET既有合法值永不覆寫；archived／retired ID永不重用。Allocation前須掃描active、archived與reserved registry；duplicate不得用新ID掩蓋，malformed／unknown namespace／mutation／registry conflict／無safe next ID時needs_review或blocking。
7. Permanent ID由deterministic namespace與受治理sequence產生，不以row、`ROW()`、名稱、Handle、URL或position作identity；row reorder／insert／move後保留原ID。
8. BRD維持semi-auto＋human review，只可回填已核准且無format／duplicate／conflict的mapping。Blank不得自動建立BRD；不得依名稱alone自動merge／split，也不得自動歸屬ambiguous mapping。
9. 每次execution須取得exclusive lock或等價guard，取得後重讀current state，並在同一critical section完成allocation、reservation／registry update與write；concurrent executions不得產生duplicate。`LockService`只是可選implementation，不是產品決策。
10. Target Spreadsheet／Sheet／column mismatch、duplicate／malformed ID、unknown namespace、BRD ambiguity、concurrency guard失敗、無法證明safe next ID或write verification失敗時一律fail closed，僅輸出redacted audit-safe結果；不得自動清除錯誤ID、覆寫合法ID或寫到其他欄位補救。
11. Read path仍由專用read-only Service Account執行，Marketing Knowledge Agent不得取得一般write capability；write path只屬standalone ID governance writer及其欄位白名單。
12. `clasp`、CI/CD、branch／tagging、secret manager、deployment owner、execute-as、service identity與API deployment方式仍是open implementation details，須另開implementation／security design，不得由本決策暗定。本決策也不授權本輪建立Apps Script project、credential、trigger或實際寫入Google Sheet。

### Decision 5 — Sync／Release Failure Notification

**已確認：A — 第一版使用Private Slack Ops Channel作為主要operational failure notification surface。**

正式規則：

1. Private Slack Ops用於Google Sheets sync final failure、Release build final failure、release validation／activation failure、重大governance blocking及其他需要人工介入的sync／release operational failure。
2. Decision 2 Slack internal search是Official／Enrichment knowledge retrieval與user query surface；Decision 5 Private Slack Ops是maintainer-only operations surface。即使共用Slack App，authorization、message schema、data exposure、purpose與audit semantics仍須分離。
3. Decision 7的Attempt 1（09:00）與Attempt 2（09:30）暫時失敗只寫internal attempt journal／structured retry state，不宣告final batch failure，也不要求主要failure alert。只有Attempt 3（10:00）仍失敗並將batch durably判為`failed`後，才嘗試primary Slack Ops alert。Retry warning／Attempt 2 early warning尚未裁決。
4. Sync／Release final result必須先寫入durable journal、manifest或operation record，再嘗試notification。Slack API call不是release transaction component，也不是決定release結果的前置條件。
5. `release_status`與`notification_status`必須分離。`failed/sent`與`failed/failed`都保留原始release failure；`success/failed`不得rollback或改寫已成功activated Release。Notification failure另留machine-readable status，不能掩蓋原始failure。
6. Production target必須是configured＋allowlisted private operational destination。Target缺失、invalid或無法驗證時notification fail closed；不得接受任意user-provided channel、fallback到public／general channel，或把alert送到Slack search response thread。Release result保持不變。
7. Ops alert可含batch／release／metadata batch ID、failure stage、sanitized error category、attempt count、affected count、timestamp、last successful release、activation status與未來的correlation ID。
8. Ops alert禁止oral-only claim／body／備註／URL、restricted raw content、raw sensitive source、Public Metric claim text、raw HTTP／HTML／CapturedContent／Evidence body、credential／API key／OAuth／Service Account／Slack token、signed URL／secret query、Authorization header、environment secret、debug dump或完整unredacted stack trace。Governance event只傳sanitized count／reason code。
9. Failure category使用structured sanitized summary並與既有stable error codes對齊，可表達source read、fingerprint、schema、governance、linked capture、release validation／activation、ID integrity與unknown operational failure；本決策不強制定案完整enum。詳細diagnostic留在同樣受redaction治理的受控journal／log。
10. Slack notification retry次數／interval／backoff、dead-letter、Email fallback／escalation、workspace、實際channel ID／名稱、App／bot identity、token／OAuth scope、secret storage、membership與execute-as identity仍是implementation／future operational design，不在本決策裁決。
11. 本決策只確認failure／operational alert的primary surface；是否發成功Release通知、成功格式、digest或health message仍是optional／future policy，不是第一版必要條件，也不要求Slack＋Email雙通道。
12. Ops notification不得改變Decision 1 read-only ingestion、Decision 2 retrieval／exposure governance、Public Metric／Evidence authority、Decision 9–11 capture policy或Official Index eligibility。本決策不授權本輪建立Slack App／channel／webhook／credential、寄送通知或實作retry／Email fallback。

### Decision 6 — Public Metric Output Cap／Pagination Budget

**已確認：B — Public Metric使用獨立`metric_item_cap`，並另設跨record type的overall `rendered_message_budget`。**

正式規則：

1. 第一版response budget分為Content Asset `content_item_cap`、Public Metric `metric_item_cap`及跨record type的overall `rendered_message_budget`；不得只用單一`total_results=N`限制所有record types。
2. 具體cap值與rendered budget數字保持configurable／UX tuning，不在本Decision寫死。正式值須依Slack payload／Block Kit、citation長度、中文密度、metric claim與allowed-channel metadata、pagination UX驗證。
3. 順序固定為retrieval → authority filtering → persistence／governance filtering → query-intent filtering → requested exposure-channel filtering（若有）→ dedupe → reranking → record-type item caps → rendered budget。不得先取前N筆再做governance。
4. Oral-only、restricted general-search blocked、pending、governance-ineligible、wrong requested channel、Evidence被誤當approved metric、non-searchable及其他fail-closed results不占content、metric或rendered quota。Generic internal query不要求G-M；channel-specific query先套對應G-M，再rerank／cap。
5. `content_item_cap`涵蓋Article／Video／Podcast／News及其他approved content types；`metric_item_cap`只涵蓋approved Public Metrics。各Content subtype是否另設cap／pagination或ratio仍是future UX／ranking design。
6. Overall budget針對接近實際輸出的rendered representation，至少考慮labels、sequence、title／approved claim、allowed-channel presentation、citation／source、authority metadata、formatting overhead與more-results indicator。計量採characters、bytes、blocks、serialized payload或hybrid尚未裁決。
7. Result必須atomic whole-item render。下一筆完整item放不下時整筆不加入，保留上一筆完整內容並回傳`more_results`或等價metadata；不得截斷claim、citation、metric identity、authority或allowed-channel語義。單筆超大型result若需摘要，必須另有explicit policy，不得任意string truncation。
8. Public Metric claim authority仍是Google Sheet C「論述」。Budget不得改寫claim語義、刪除關鍵限定、拼接新claim、以Evidence補滿截斷claim或擴張G-M；完整item無法安全放入budget時不輸出該item。
9. `shown_count`、eligible `remaining_count`與more-results signal只以post-governance／post-intent eligible set計算，不計被治理排除的records。Oral-only／restricted aggregate若受更嚴政策限制，沿用較嚴規則。
10. Cap在rerank後選最相關eligible items，不依source row或permanent ID numerical order截前N筆，除非只作deterministic tie-breaker。Decision 6不重新裁決retrieval／reranking algorithm。
11. Display caps只屬response rendering layer，不限制Content Asset全文／Public Metric ingestion、Official Index corpus、FTS／vector chunks或upstream retrieval。Cursor、page token、TTL、next-page command、Slack button、server-side state與message splitting仍是future implementation，不進Sprint 0。
12. Decision 6適用Slack internal search／internal answer，不適用Decision 5 Private Slack Ops。Ops alert維持獨立sanitized operational schema，不得因content／metric caps丟失重要failure metadata。本決策不授權本輪實作renderer、pagination、cursor、TTL、splitting或configuration。

### Decision 7 — Scheduler retry語義

**已確認：A — 總共最多3次attempt。**

正式時間：

- Attempt 1：09:00
- Attempt 2：09:30
- Attempt 3：10:00
- Timezone：Asia/Taipei

正式規則：

1. 規格統一使用「最多3次attempt」，不得寫成「初次＋retry 3次」。
2. 不存在10:30第四次執行。
3. 每次attempt都必須重新取得並驗證source fingerprint。
4. fingerprint不一致時，不publish、不archive，也不更新active release。
5. 第3次仍失敗後，本批次結束為`failed`。
6. 第3次仍失敗並durably判定batch為`failed`後，主要failure alert依Decision 5嘗試Private Slack Ops；Attempt 1／2 warning、notification retry與Email fallback仍未裁決，不得自行實作。

### Decision 8 — Content Asset cell cardinality

**已確認：A — 第一版一個H-K source cell最多代表一個logical Content Asset。**

正式規則：

1. 商家／夥伴案例資料庫H-K依序代表article、video、podcast、news。
2. 每個MREC的每個asset type，第一版最多建立一個logical Content Asset。
3. 第一版Content Asset identity固定為 `<MREC>:<asset_type>`，例如 `MREC-0001:article`；不新增AST permanent ID，也不建立Asset ID allocator或registry。
4. URL candidates先完成安全檢查、canonicalization與dedupe。rich-text run、whole-cell hyperlink、`HYPERLINK` formula或cell text取得的候選若canonicalization後為同一URL，只算一個，不構成multi-link conflict。
5. 0個safe URL但cell有有效title時建立incomplete asset；`searchable=false`，不得進Official SQLite／FTS／vector／Slack。不猜測URL，也不得外部搜尋補URL。
6. 1個distinct safe canonical URL時，可再依其他governance與identity條件判定active。
7. 2個以上distinct safe canonical URLs時標記needs_review；不得依priority任選，不得自動拆成多個Content Assets，不得以URL、Rich Text run position或candidate array index作permanent identity，人工整理前不得進Official publish set。
8. 未來若確認同一cell需要多個正式Content Assets，必須另開architecture／migration decision，屆時才能評估AST permanent ID；第一版不得預先實作AST。
9. 人工修正應在Google Sheets canonical source整理成一個cell對應一個logical asset；目前read-only sync不自動寫回。本決策不改變既有needs_review／whole-batch publish policy，也不重新裁決其他治理政策。

### Decision 9 — Linked Article Content Capture

**已確認：A — 符合條件的embedded article links建立CapturedContent全文，不只保存URL。**

正式規則：

1. Google Sheets仍是Official metadata／identity／governance authority；linked webpage只提供Content Asset body，不建立或改寫parent identity。
2. 完整canonical representation由Google canonical metadata與相容的CapturedContent revision組成；Obsidian Markdown與Official SQLite／FTS／vector必須作sibling outputs，禁止`URL → Markdown → parse Markdown → Official Index`。
3. MREC Content Asset的captured body標示`authority_role=primary_content`；Public Metric F參考新聞標示`authority_role=evidence`，Evidence正文不得自動升格為approved metric或擴張G-M渠道。
4. CapturedContent保存清洗後deterministic正文、section structure、parser version、content hash、capture status、source URL與lineage；raw HTML不得長期作Official Search content。
5. FTS與vector必須可搜尋captured全文chunks；retrieval／reranking後才產生query-focused summary，不得只搜尋title、tags或fixed／precomputed summary。
6. AI query summary是derived answer，不得覆寫或冒充captured official body，也不得補入retrieved evidence未支持的內容。
7. Capture policy必須versioned、可配置、fail closed；unknown third-party預設metadata-only／needs_policy，不得繞過登入、付費牆、robots或安全限制。
8. Temporary fetch failure保留相同URL的last-known-good正文並標stale；URL改變且新fetch失敗時不得錯掛舊body。Google source fingerprint與captured content hash必須分離。
9. 詳細canonical model、fetch boundary、HTML normalization、release／freshness與RAG contract以`10_LINKED_CONTENT_CAPTURE_AND_RAG_SPEC.md`為準。本決策不授權本輪實作HTTP capture、寫Vault、建index或執行migration。

### Decision 10 — Linked Content Refresh Mode

**已確認：A — 第一版Linked Content Capture與Google metadata sync共同建立同一個完整Release。**

正式規則：

1. 第一版不得建立獨立Linked Content refresh scheduler、capture-only active pointer或partial content release。
2. 同一次Google metadata sync／release build必須依序涵蓋snapshot、metadata normalization、link resolution、capture、CapturedContent、chunking、Obsidian／FTS／vector sibling render、release validation與完整active release activation。
3. 每個active Official Release以單一`release_id`固定`metadata_sync_batch_id`／source fingerprint、release-pinned CapturedContent revisions、Obsidian projection、SQLite／FTS與vector index；任一部分不得自行切換成不同composition。
4. CapturedContent保留`captured_at`、`content_hash`、`previous_content_hash`、`parser_version`、`last_successful_capture_at`與`capture_status`，供lineage、diff與future migration使用。
5. Captured revision可保留歷史與candidate概念，但第一版不得獨立activate、不得單篇更新production Obsidian／index，也不建立capture scheduler或capture-only pointer。
6. `source_fingerprint`只代表Google canonical metadata；`capture_content_hash`只代表單一normalized webpage body。兩者是不同freshness domain，但第一版只能隨完整Release一起publish。
7. 同URL temporary fetch failure的LKG eligibility與新Release行為依Decision 11；符合其全部條件時可標`stale`進入新完整Release，但不得假裝本次成功。
8. URL改變且新URLfetch失敗時不得錯掛舊body，沿用Decision 9。未來若需要更高網頁freshness，必須另開architecture／migration decision後才能設計capture batch、scheduler、independent activation、partial release、composition rollback與stale replacement policy。
9. 本決策不授權本輪實作scheduler、HTTP fetch、Vault／index寫入或migration。

### Decision 11 — Last Known Good on Temporary Capture Failure

**已確認：A — 同一canonical URL在本輪temporary fetch failure時，符合條件的Last Known Good可用`stale`狀態進入新的完整Official Release。**

正式規則：

1. LKG reuse必須同時滿足：canonical URL與上一成功capture完全相同、存在先前`capture_status=success`的CapturedContent、本次是temporary fetch failure、沒有security／governance／capture policy阻擋，且LKG未超過核准freshness policy。
2. Temporary failure至少可涵蓋timeout、temporary DNS／network failure、HTTP 5xx與temporary 429；具體retry classification由後續versioned policy細化。
3. 符合時candidate沿用上一成功正文與`content_hash`，標`capture_status=stale`；原`captured_at`與`last_successful_capture_at`保持不變，只將`last_capture_attempt_at`記成本次attempt。Manifest必須明列stale capture，不得製造假revision、假hash或假成功時間。
4. URL／canonical URL改變、從未成功capture、unsafe、`capture_mode=blocked`、不允許的authenticated／paywalled來源、governance禁止、identity無法可靠reconcile或LKG超過freshness policy時，禁止沿用舊body。URL changed failure的舊capture只保留歷史lineage。
5. LKG不得無限期沿用；具體freshness threshold仍是Open Question／future operational policy。未配置核准threshold或已超限時reuse gate fail closed，不自行設定30／60／90天等數值。
6. `stale`不改變authority role：Primary仍是`primary_content`，Evidence仍是`evidence`，且Evidence無論fresh／stale都不得升格為`approved_metric`。
7. Search result與citation metadata必須帶`capture_status=stale`、原`captured_at`、`last_successful_capture_at`與本輪`last_capture_attempt_at`。回答層未來可依freshness sensitivity降權或顯示warning，但ranking penalty數值尚未裁決。
8. Decision 10的完整Release boundary不變：stale CapturedContent必須由manifest固定並隨metadata、Obsidian、SQLite／FTS及vector一起驗證／啟用，不得單篇publish或建立capture-only release。
9. 本決策不授權本輪實作HTTP fetch、scheduler、Vault／index寫入、ranking policy或migration。

## Remaining Decisions

None。Decision 1–11均已由使用者正式確認；open implementation／operational questions仍可保留，但不得視為原Decision未完成，也不得新增替代Decision 6的項目。
