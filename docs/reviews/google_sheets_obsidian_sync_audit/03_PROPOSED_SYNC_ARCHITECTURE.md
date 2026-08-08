# Proposed Sync Architecture

## 1. 設計原則

1. Google Sheets snapshot是Official metadata／identity／governance authority；linked webpage只提供經政策核准的CapturedContent body，Obsidian與indexes是可重建projection。
2. 第一次完整API response只存在記憶體或受控短生命週期staging；oral-only正文在normalize後立即不可逆地移除，不得進debug dump或failure artifact。
3. 所有可發布輸出從相容的immutable normalized metadata batch與CapturedContent revision set產生，不得以同步後Markdown作為index輸入。
4. Decision 10已確認第一版metadata sync、linked capture、Obsidian、SQLite／FTS與vector共用一個完整Release publish boundary；不得建立capture-only scheduler、active pointer或partial content activation。
5. publish是整批狀態轉移。任何blocking error使candidate失效，active release不變，也不得archive。
6. Enrichment與Official分離；人工筆記不能改寫Official entity或metric。
7. Decision 4已確認永久ID allocator／governance writer採external standalone Apps Script，並與Marketing Knowledge Agent的read-only snapshot／release pipeline形成獨立最小權限控制平面。

## 2. 邏輯流程

```text
Scheduled trigger / manual dry-run
  → Read-only Google Sheets snapshot
  → Canonical source fingerprint F1
  → In-memory CellData extraction
  → Early exclusion/redaction gate
  → Normalized staging batch
  → Schema + ID + governance + lineage validation
  → Link Resolver + versioned Capture Policy
  → Human approval gates where required
  → HTTP Fetch（Sprint 0禁用）
  → HTML extraction / normalization
  → CapturedContent revisions + content hashes
  → Full-text chunking
  → Diff / brand-review / redacted exception preview
  → Build immutable release candidate
       ├─ Obsidian managed tree
       ├─ Official SQLite + FTS
       ├─ Official vector index
       └─ manifests / smoke-test evidence
  → Re-read canonical source fingerprint F2
  → Require F1 == F2
  → Cross-artifact smoke tests
  → Commit journal + backups
  → Atomic release activation
  → Update last_success last
  → Persist final release / batch operation state
  → End release transaction
  → Conditional Private Slack Ops notification side effect
```

Permanent ID治理另走受控寫入流程，不是上述metadata sync／release pipeline的寫入能力：

```text
GitHub tracked source + specification
  → code review
  → controlled deployment
  → external standalone Apps Script execution target
  → explicit configured + allowlisted canonical Spreadsheet ID
  → exclusive concurrency guard + current-state re-read
  → validate active / archived / reserved ID registry
  → allocate MREC / MET or backfill already-approved BRD mapping
  → field allowlist enforcement + write verification
  → redacted audit-safe result / status
```

Google Sheets是business metadata與reviewed governance authority；GitHub是Apps Script source code與specification authority；standalone deployment是受控execution target。Script editor內的漂移版本不是authority。此writer不得執行snapshot sync、link extraction、HTTP capture、HTML normalization、CapturedContent、Markdown／index／Slack／RAG、query answering或exposure決策。

Enrichment另走獨立流程：

```text
99_Manual_Notes/Approved_Enrichment
  → frontmatter/schema validation
  → authorized approver whitelist exact-membership validation
  → semantic content hash approval check
  → Enrichment candidate index
  → atomic activation of Enrichment release only
```

Decision 3規定`approved_by`必須是單一非空stable canonical reviewer ID，且精確命中note外部的受控authorized approver whitelist。Whitelist無法載入／驗證、ID缺失／無法exact match、`review_status`不是`approved`或substantive hash已失效時，該note即使`searchable=true`也不得進Enrichment candidate。Note frontmatter不得自我宣告或擴張approver authority；Official pipeline不套此Manual Enrichment gate。Reviewer ID scheme、whitelist實體位置與historical approval revocation另列open design／governance問題。

Linked content有獨立hash語義，但第一版不具獨立publish boundary，完整contract見`10_LINKED_CONTENT_CAPTURE_AND_RAG_SPEC.md`：

```text
Complete Release
  ├─ pinned metadata_sync_batch_id + source_fingerprint
  ├─ release-pinned CapturedContent revisions + content hashes
  ├─ Obsidian captured body projection
  ├─ Official full-text SQLite + FTS
  └─ Official full-text vector chunks
  → validate and activate as one unit
```

Google F1／F2與captured content hash分開計算；外部網站內容變動不得被誤判成Google source fingerprint變動。但兩種freshness domain分離不代表可分開publish；CapturedContent revision只能隨完整Release啟用。

Decision 11允許同一canonical URL在temporary fetch failure時，於存在先前成功capture、沒有security／governance／capture policy阻擋且LKG未超過核准freshness policy的前提下，讓上一成功正文以`capture_status=stale`進入本次完整Release candidate。Candidate沿用原`content_hash`、`captured_at`與`last_successful_capture_at`，只更新`last_capture_attempt_at`；不得建立假revision或把blocked／paywalled／URL changed failure偽裝成temporary failure。若freshness policy尚未配置或已超限，LKG reuse gate fail closed。

## 3. 元件邊界

### 3.1 `sheets_snapshot`

- 只讀reader protocol，輸入Spreadsheet ID、明確ranges與fields mask。
- production adapter使用專用read-only Service Account與最小唯讀Google API scope，並呼叫 `spreadsheets.get(includeGridData=true, ranges=...)`；測試以synthetic `CellData` objects注入。
- 保存sheet id/title/hidden state、grid range、merge metadata，以及每個cell的formatted/effective/user-entered value、hyperlink、text runs、validation。
- Spreadsheet只授予該Service Account讀取權限，adapter不得具備寫入Spreadsheet的能力；credential取得方式另案處理，secret不得進Git、Audit文件、Obsidian或log。

### 3.1.1 `id_governance_writer`（獨立控制平面）

- Production target只接受明確設定且allowlist命中的canonical Spreadsheet ID `15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM`；缺失、格式錯誤、不相符或無法讀取時fail closed。不得以 `SpreadsheetApp.getActiveSpreadsheet()`作identity、fallback或production target discovery。
- 寫入白名單只有「商家／夥伴案例資料庫」M（MREC）、N（BRD）、O（ID Review Status），以及「可公開」對外數據N（MET）。不得寫其他business columns、G-M exposure channels或任何Slack欄位，也不得提供任意Spreadsheet ID、sheet、range或generic mutation API。
- MREC／MET只在既有值空白且全域validation安全時配置；既有合法ID永不覆寫，archived／retired ID永不重用。Duplicate不得以配置新號掩蓋；malformed、unknown namespace、registry conflict或無法證明safe next ID時block／needs_review。
- BRD只可回填已由人工核准的mapping。Blank不得自動建立新BRD；名稱／Handle／網站只能作review evidence，不得自動merge、split或不確定歸屬。
- 每次execution須取得exclusive lock或等價guard後重讀current state，並在同一critical section完成allocation、reservation／registry更新與write；concurrent executions不得產生duplicate。寫後readback／verification失敗即fail closed並回報audit-safe狀態。
- Read path仍由專用read-only Service Account執行，Marketing Knowledge Agent不得取得一般Google write能力；write path只屬standalone ID governance writer。`clasp`、CI/CD、branch／tagging、secret manager、deployment owner、execute-as、service identity與API deployment方式仍是open implementation details。

### 3.2 `cell_extraction`

- 只處理表格結構、header映射、merge ownership與field provenance。
- merged value只在Google merge range明確涵蓋且欄位規格允許時繼承。
- 空白列不得通用forward fill。
- formula cell以effective/formatted值作內容；user-entered formula只作provenance，不進正文。

### 3.3 `normalization`

- 輸出typed canonical records，不輸出Markdown。
- 以source permanent ID連接，不以row number當identity；row/range只作lineage。
- oral-only metric在此層轉成 `ExcludedSourceRef`，只保留sheet/row/MET（若存在）、`oral_only`原因與不可逆摘要hash；不保留論述、備註、URL或可重建正文的片段。
- restricted/pending分流至governance-only records，禁止流入Official publish set。
- 通過URL與governance gate的linked metadata只產生capture candidates；normalization層不發HTTP，也不把webpage當identity authority。

### 3.4 `governance`

至少包含：

- critical sheet/header/merge/source count health；
- MREC/MET/BRD uniqueness、format、immutability、retired registry；
- brand mapping的unique handle/website suggestion與needs_review；
- exposure channel、written-use、status、publish eligibility；
- Decision 2兩層治理：先以authority、persistence eligibility、governance status與`searchable`決定Official Index membership；只有query明確要求特定usage channel時，才以G-M `allowed_exposure_channels`限制回答用途；
- public URL與credential/token檢查；
- asset title+URL完整性；
- Primary／Evidence authority role、domain capture policy與capture eligibility；
- archive候選與mass-deletion gate；
- 跨output conservation與no-sensitive-payload assertions。

Validation結果分為：

- `blocking_error`：整批不可build/publish；
- `needs_review`：只有明定可隔離的candidate不發布，其餘是否可繼續須由批次政策明確定義；本案採整批停止發布，避免partial publish；
- `excluded`：依既定政策安全移除，可在redacted report記錄來源列與原因；
- `warning`：不影響資料意義或authority的診斷訊息。

### 3.5 `batch_store`

每個candidate Release使用不可變目錄，例如：

```text
.mka/releases/<release_id>/
  manifest.json
  normalized/              # 只含允許持久化的標準化資料
  captured/                # 相容、允許持久化的CapturedContent revisions
  official.sqlite
  official.vector/
  obsidian_tree/
  reports/                 # redacted machine-readable evidence
```

`manifest.json`至少包含`release_id`、`metadata_sync_batch_id`、schema version、source fingerprint、source row counts、capture policy／parser versions、release-pinned captured revision IDs／content hashes、stale capture IDs／status／capture timestamps、artifact checksums、validator版本、excluded counts、previous release、created time與publish state。它不得包含oral-only正文、raw HTML或憑證。

## 4. Source fingerprint

Fingerprint輸入應是canonical serialization，而不是Google response JSON原始順序：

- Spreadsheet ID與selected sheet IDs/titles；
- selected grid coordinates；
- effective/formatted/user-entered value的type-safe representation；
- hyperlinks/text format runs/data validation；
- merge ranges；
- critical sheet properties與last row/column bounds。

排除response envelope、不影響Google內容的暫態欄位、HTTP metadata、captured body、capture timestamp與capture content hash。每次attempt開始取得F1；候選建置與smoke tests後，以相同fields/ranges重新取得F2。`F1 != F2`時刪棄candidate activation資格、保留active release、不archive，也不更新active release。Linked body另以parser-versioned content hash追蹤，不改寫F1／F2。

Scheduler固定採Asia/Taipei 09:00、09:30、10:00，總共最多3次attempt；每次都重新取得並驗證source fingerprint，不存在10:30第四次執行。Attempt 1／2失敗只durably記錄internal attempt journal並進入既有retry flow，不宣告final batch failure，也不要求主要failure alert。第3次仍失敗後先將batch durably記為`failed`，才可嘗試Decision 5的Private Slack Ops primary failure alert；warning-level retry通知與Attempt 2提前warning仍是future operational policy。

## 5. Preview與apply

### Preview

- 產生deterministic diff：create/update/archive/restore/incomplete/excluded/needs_review/unchanged。
- brand initialization另產生candidate group review artifact。
- oral-only只顯示來源列、MET（若已配置）與排除原因。
- Google preview帶source fingerprint、schema version、policy version與normalized hash；capture preview另帶pinned metadata batch、capture policy／parser versions與content hash，不混用兩種hash。

### Apply/build

- 只接受經驗證且hash未變的preview/decision artifact。
- build只寫新的release candidate，不碰active artifacts。
- 所有renderer必須消費相容的normalized metadata batch與CapturedContent canonical objects或其immutable serialization。
- apply期間不得回讀產出的Markdown來決定index內容。

## 6. Atomic publish與rollback

檔案系統、SQLite與vector目錄無法形成真正跨資源ACID transaction，因此採「不可變candidate + commit journal + active pointer最後切換」：

1. 取得single-writer lock。
2. 驗證active release與last_success一致。
3. 寫candidate到新目錄並fsync/close。
4. 重驗F2與所有artifact checksums。
5. 寫入狀態為`prepared`的commit journal。
6. 將Vault managed projection、Official DB／FTS與vector都保持在同一immutable candidate目錄，不切任何component production pointer。
7. 直接以candidate paths執行跨artifact smoke tests與composition validation。
8. 寫入完整`release_id`、metadata batch、capture revisions與artifact paths/checksums。
9. 最後只原子切換global active release pointer並更新`90_Sync/last_success.json`，journal標記`committed`；所有production consumer都從global pointer解析component paths。

任何步驟失敗均依journal反向還原到上一個release。啟動時若見未完成journal，必須先recovery，不得開始新sync。至少保留上一個成功release；保留週期另由維運政策決定。

Global active pointer只指向完整Release；同一`release_id`固定metadata batch、CapturedContent revisions、Vault projection、Official DB／FTS與vector。第一版不得單獨切換capture revision、單篇更新production Vault／index，或建立capture-only active pointer／scheduler。

Stale LKG即使符合Decision 11，也只能作完整Release composition的一部分通過同一組validation後一起啟用；不得單篇publish。URL已改、從未成功capture、unsafe／blocked／policy-prohibited、identity無法reconcile或超過freshness policy時，舊正文不得進新parent projection。

### Operational notification side effect

Decision 5確認Private Slack Ops Channel是第一版Google sync final failure、Release build／validation／activation failure及重大governance blocking failure的主要operational alert surface。通知必須發往configured＋allowlisted private operational destination；workspace、channel ID／名稱、App／bot identity、OAuth scope、token與secret storage仍是open implementation details。不得接受任意user-provided channel、fallback到public／general channel，或把Ops alert送進Slack internal search response thread。

Release／batch final state必須先寫入durable journal、manifest或operation record，之後才嘗試Slack side effect。至少分開保存 `release_status`與`notification_status`：

- `release_status=failed`, `notification_status=sent`：原始failure已durable，告警成功；
- `release_status=failed`, `notification_status=failed`：原始failure仍成立且不得被通知錯誤掩蓋；
- `release_status=success`, `notification_status=failed`：已啟用Release不得rollback或改成failed。

Slack API call不屬於release commit transaction，也不得成為判定sync／release成功或失敗的前置條件。通知target缺失／無法驗證或send失敗時，只將`notification_status` fail closed並保存machine-readable evidence，不改寫`release_status`、active pointer或last successful release。通知retry次數／interval／backoff、dead-letter、Email fallback及成功Release通知均保留future operational design。

Ops message只使用sanitized structured summary，例如batch／release／metadata batch ID、failure stage、error category、attempt count、affected count、timestamp、last successful release與activation status。Category應與既有stable error codes對齊，可表達source read、fingerprint、schema、governance、capture、release validation／activation及ID integrity等概念，但本Audit不另定完整enum。禁止放入oral-only claim／body、restricted raw content、Public Metric claim text、raw source／HTTP／HTML／captured body、credential／token／secret、signed URL／secret query、Authorization header、environment secret、debug dump或完整unredacted stack trace；詳細diagnostic只留在同樣受redaction治理的受控journal／log。

## 7. Archive與mass-deletion safety

Archive eligibility必須同時滿足：

- F1/F2一致；
- 所有critical sheets存在且header/merge validation通過；
- API response完整、range bounds合理；
- permanent ID coverage沒有異常；
- row count、entity count與前次成功批次差異低於政策threshold；
- 每個archive candidate可由previous active ID與current complete snapshot作集合差證明。

首次live sync建議任何archive都需人工批准。穩定後再採雙threshold，例如absolute count與percentage任一超限即blocking；實際數值由歷史baseline決定，不在本盤點猜定。

## 8. Official與Enrichment consumption

- `OfficialSearchRepository`只開啟目前active Official release。
- `EnrichmentSearchRepository`只開啟獨立active Enrichment release。
- Slack／internal search是Official retrieval surface，不是exposure channel；第一版不新增Slack-specific checkbox、N欄或persistence gate。Slack預設只呼叫Official；只有明確parser標記 `include:enrichment` 才執行第二次查詢。
- Slack internal search是user query／knowledge retrieval surface；Private Slack Ops是maintainer-only system failure notification surface。即使未來共用同一Slack App，authorization、message schema、data exposure、purpose與audit semantics仍須分離。
- 結果保留authority label；Enrichment永不與同ID Official欄位merge，也不能覆寫citation。
- Official不足時可以回傳「另有已核准內部補充」的布林提示，不傳內容；使用者明確要求後才顯示。
- Generic internal query按Official persistence／search eligibility檢索，不要求任一G-M為true；結果保留`allowed_exposure_channels`，不得暗示可用於所有對外渠道。
- Query明確要求Saleskit、官網、廣告等用途時，才在answer eligibility套用對應G-M permission，以及status、`can_quote_externally`、authority與citation gates。Exposure checkbox為false只阻擋該用途，不把eligible record從internal search corpus移除。
- Oral-only在normalization後即排除，pending不進Official Index，restricted不進一般Official Search；Primary／Evidence則依各自authority、governance、`searchable`與capture release policy決定search eligibility。

Decision 6只約束Slack internal search／internal answer的response rendering layer，不改ingestion、Official Index membership、全文searchable corpus或retrieval candidate pool。正式answer pipeline順序為：

```text
Retrieval
  → authority filtering
  → persistence / governance filtering
  → query-intent filtering
  → requested exposure-channel filtering（query有指定時）
  → dedupe
  → reranking
  → content_item_cap + metric_item_cap
  → overall rendered_message_budget
  → atomic whole-item rendering + eligible more-results metadata
```

Content Asset與Public Metric使用不同item quota；Public Metric必須有獨立`metric_item_cap`，兩類結果再共同受整體`rendered_message_budget`限制。具體cap數值與budget單位不得在Audit中猜定，需依Slack實際payload、中文密度、claim／citation／allowed-channel長度與UX測試配置。Oral-only、restricted general-search blocked、pending、non-searchable、wrong requested channel、governance-ineligible或authority錯誤的result在cap前即移除，不占item或rendered quota。

Rendered budget以接近實際輸出的完整representation評估，包含labels、序號、title／claim、allowed channels、citation、authority與formatting overhead。下一筆完整item放不下時整筆不加入，保留前一筆完整內容並輸出`more_results`或等價metadata；不得將claim截成半句、移除citation／authority／channel語義，或用Evidence補寫approved metric。Shown／remaining count只基於post-governance、post-intent eligible set；敏感aggregate若受更嚴治理則沿用較嚴規則。

Caps在rerank後選最相關eligible items，不依source row或permanent ID順序截取，除非只作deterministic tie-breaker。Cursor、page token、TTL、Slack button、message splitting、各Content subtype cap／ratio與單筆超大型result摘要仍是future UX／implementation design；不得因此進Sprint 0。Decision 5 Private Slack Ops使用獨立sanitized operational schema，不套`content_item_cap`、`metric_item_cap`或knowledge-result pagination。

## 9. 最小遷移路徑

1. 新增canonical models、snapshot protocol與synthetic fixtures；不接正式API。
2. 建立redacted dry-run validator及永久ID/brand review contract。
3. 將現行Obsidian renderer包成canonical model consumer；保留舊CLI但標成legacy path，不立刻刪除。
4. 新建candidate Official SQLite/FTS/vector builder，直接吃canonical batch；現行Markdown index builder暫時保留做比較測試。
5. 建立dual-run parity report，確認citation/metadata與governance守恆，不發布。
6. 實作release coordinator、source recheck與rollback rehearsal。
7. 依已確認的read-only Service Account架構，另案取得正式連線授權與安全credential供應後才小範圍dry-run；人工確認後再切active pointer。
8. 最後才讓Slack選擇Official/Enrichment repositories，並逐步停用legacy Markdown-derived index。

此路徑重用既有review/apply、Obsidian backup、typed retrieval、Slack renderer與governance gate，不以一次性store executor作為通用核心，也避免大爆炸式重構。
