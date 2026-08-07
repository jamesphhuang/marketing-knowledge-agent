# Proposed Sync Architecture

## 1. 設計原則

1. Google Sheets snapshot是Official authority；Obsidian與indexes是可重建projection。
2. 第一次完整API response只存在記憶體或受控短生命週期staging；oral-only正文在normalize後立即不可逆地移除，不得進debug dump或failure artifact。
3. 所有可發布輸出從同一個immutable normalized batch產生，不得以同步後Markdown作為index輸入。
4. publish是整批狀態轉移。任何blocking error使candidate失效，active release不變，也不得archive。
5. Enrichment與Official分離；人工筆記不能改寫Official entity或metric。

## 2. 邏輯流程

```text
Scheduled trigger / manual dry-run
  → Read-only Google Sheets snapshot
  → Canonical source fingerprint F1
  → In-memory CellData extraction
  → Early exclusion/redaction gate
  → Normalized staging batch
  → Schema + ID + governance + lineage validation
  → Diff / brand-review / redacted exception preview
  → Human approval gates where required
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
  → Notify
```

Enrichment另走獨立流程：

```text
99_Manual_Notes/Approved_Enrichment
  → frontmatter/schema validation
  → semantic content hash approval check
  → Enrichment candidate index
  → atomic activation of Enrichment release only
```

## 3. 元件邊界

### 3.1 `sheets_snapshot`

- 只讀reader protocol，輸入Spreadsheet ID、明確ranges與fields mask。
- production adapter使用專用read-only Service Account與最小唯讀Google API scope，並呼叫 `spreadsheets.get(includeGridData=true, ranges=...)`；測試以synthetic `CellData` objects注入。
- 保存sheet id/title/hidden state、grid range、merge metadata，以及每個cell的formatted/effective/user-entered value、hyperlink、text runs、validation。
- Spreadsheet只授予該Service Account讀取權限，adapter不得具備寫入Spreadsheet的能力；credential取得方式另案處理，secret不得進Git、Audit文件、Obsidian或log。

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

### 3.4 `governance`

至少包含：

- critical sheet/header/merge/source count health；
- MREC/MET/BRD uniqueness、format、immutability、retired registry；
- brand mapping的unique handle/website suggestion與needs_review；
- exposure channel、written-use、status、publish eligibility；
- public URL與credential/token檢查；
- asset title+URL完整性；
- archive候選與mass-deletion gate；
- 跨output conservation與no-sensitive-payload assertions。

Validation結果分為：

- `blocking_error`：整批不可build/publish；
- `needs_review`：只有明定可隔離的candidate不發布，其餘是否可繼續須由批次政策明確定義；本案採整批停止發布，避免partial publish；
- `excluded`：依既定政策安全移除，可在redacted report記錄來源列與原因；
- `warning`：不影響資料意義或authority的診斷訊息。

### 3.5 `batch_store`

每批使用不可變目錄，例如：

```text
.mka/releases/<sync_batch_id>/
  manifest.json
  normalized/              # 只含允許持久化的標準化資料
  official.sqlite
  official.vector/
  obsidian_tree/
  reports/                 # redacted machine-readable evidence
```

`manifest.json` 至少包含schema version、source fingerprint、source row counts、artifact checksums、validator版本、excluded counts、parent batch、created time與publish state。它不得包含oral-only正文或憑證。

## 4. Source fingerprint

Fingerprint輸入應是canonical serialization，而不是Google response JSON原始順序：

- Spreadsheet ID與selected sheet IDs/titles；
- selected grid coordinates；
- effective/formatted/user-entered value的type-safe representation；
- hyperlinks/text format runs/data validation；
- merge ranges；
- critical sheet properties與last row/column bounds。

排除response envelope與不影響內容的暫態欄位。每次attempt開始取得F1；候選建置與smoke tests後，以相同fields/ranges重新取得F2。`F1 != F2` 時刪棄candidate activation資格、保留active release、不archive，也不更新active release。

Scheduler固定採Asia/Taipei 09:00、09:30、10:00，總共最多3次attempt；每次都重新取得並驗證source fingerprint，不存在10:30第四次執行。第3次仍失敗後batch結束為`failed`；通知管道仍待Decision 5確認。

## 5. Preview與apply

### Preview

- 產生deterministic diff：create/update/archive/restore/incomplete/excluded/needs_review/unchanged。
- brand initialization另產生candidate group review artifact。
- oral-only只顯示來源列、MET（若已配置）與排除原因。
- preview帶source fingerprint、schema version、policy version與content hash。

### Apply/build

- 只接受經驗證且hash未變的preview/decision artifact。
- build只寫新的release candidate，不碰active artifacts。
- 所有renderer必須消費同一份normalized batch object或其immutable serialization。
- apply期間不得回讀產出的Markdown來決定index內容。

## 6. Atomic publish與rollback

檔案系統、SQLite與vector目錄無法形成真正跨資源ACID transaction，因此採「不可變candidate + commit journal + active pointer最後切換」：

1. 取得single-writer lock。
2. 驗證active release與last_success一致。
3. 寫candidate到新目錄並fsync/close。
4. 重驗F2與所有artifact checksums。
5. 寫入狀態為`prepared`的commit journal。
6. 將Vault managed projection切至candidate tree；若不能用同filesystem rename，先保留完整backup並記錄每步。
7. 將Official DB/vector active pointer切到candidate。
8. 執行active smoke tests。
9. 最後原子更新`90_Sync/last_success.json`與global active release pointer，journal標記`committed`。

任何步驟失敗均依journal反向還原到上一個release。啟動時若見未完成journal，必須先recovery，不得開始新sync。至少保留上一個成功release；保留週期另由維運政策決定。

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
- Slack預設只呼叫Official；只有明確parser標記 `include:enrichment` 才執行第二次查詢。
- 結果保留authority label；Enrichment永不與同ID Official欄位merge，也不能覆寫citation。
- Official不足時可以回傳「另有已核准內部補充」的布林提示，不傳內容；使用者明確要求後才顯示。
- 所有Slack結果仍通過現有external/written-safe gate；新channel mapping必須等政策裁決。

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
