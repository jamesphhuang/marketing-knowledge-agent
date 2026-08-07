# Google Sheets Import and Governance Specification

## 1. Scope與非目標

本規格定義read-only snapshot、CellData extraction、normalization與發布前governance。正式API認證已確認使用專用read-only Service Account與最小唯讀Google API scope；本規格不建立憑證、不實作Apps Script，也不允許寫回Google Sheets。ID配置與人工回填是獨立、受治理流程。

## 2. Snapshot request

使用 `spreadsheets.get`：

- Spreadsheet只授予專用Service Account讀取權限，Service Account不得具備write scope或寫入Spreadsheet的能力；
- `includeGridData=true`；
- `ranges`明確列出五個來源sheet及新增的品牌sheet/review sheet所需範圍；
- fields mask包含spreadsheet/sheet properties、merges、rowData.values的 `formattedValue`, `effectiveValue`, `userEnteredValue`, `hyperlink`, `textFormatRuns.format.link`, `dataValidation`；
- 保留sheet ID與hidden property，不能因hidden而略過必要sheet；
- 不使用 `spreadsheets.values.get` 作為正式擷取來源。

原始response不得寫debug dump。Credential、token與service account secret不得commit至Git、寫入Audit文件或Obsidian，也不得輸出到log；若公司政策禁止長期JSON key，credential供應應採公司核准的Secret Manager、Workload Identity或等價無長期金鑰方案。測試只能使用人工合成、不含正式內容的CellData fixture。本決策不授權本輪建立憑證或連接正式API。

## 3. Sheet contracts

### 商家/夥伴案例資料庫

- header row固定第6列，A-O欄需按名稱與位置驗證。
- M為MREC、N為BRD、O為ID Review Status。
- C的整格/Rich Text hyperlink是official website候選。
- H-K各自代表article/video/podcast/news asset cell。
- 公式只取effective/formatted display；公式本身不作正式正文。
- 同品牌多列是多筆MREC，不自動dedupe。

### 「不可公開」客戶名單

- 每個有效資料列均產生denylist/governance candidate，不以NDA欄為條件。
- 不得進Official normalized content、Markdown、FTS、vector或citation。

### 「可公開」對外數據

- header row固定第6列，A-N欄驗證；N為MET。
- 每個非空C為一筆metric。
- G-M保留effective boolean及validation type；非checkbox/非boolean但含值時blocking或needs_review，不猜值。
- A/B/F只依實際merge metadata向covered cells繼承；不對任意空白row forward fill。
- E標記為maintenance update，不轉成measurement period。

### 待確認數據

- 只進restricted governance/internal review projection；不得進Official或對外answer。
- 報告若無必要不得重複claim正文。

### handle 比對

- 只作brand initialization evidence；不作正式brand master，不直接觸發merge。

### 品牌 ID 對照／品牌 ID 初始化審核

- 前者是核准後BRD authority，後者是decision surface。
- importer讀取兩者以驗證mapping；此read-only sync不回填。

## 4. Cell value resolution

每個field輸出value與provenance：

1. boolean/date/number等typed field優先讀 `effectiveValue`，以 `formattedValue`作人類顯示。
2. text field使用 `formattedValue`；若不存在，才按明確type-safe規則將effective value轉換。
3. `userEnteredValue.formulaValue`只保存為非正文provenance marker，例如 `source_was_formula=true`；不得將公式字串存為content。
4. effective error、stale/missing formula cache、validation/type衝突為validation issue。
5. merged covered cell只向merge anchor取值；每個繼承值標記anchor range。
6. 非merge空白不繼承，除非field-specific contract另有明確規則；本案不設通用forward fill。

## 5. URL extraction

候選依序：

1. `textFormatRuns[].format.link.uri`；若不同run有多個不同URL，保留全部候選並needs_review，不任選。
2. CellData `hyperlink`。
3. 經安全parser辨識的 `HYPERLINK` formula第一個URL argument；不得用字串eval。
4. cell display text本身完整匹配單一HTTP/HTTPS URL。

同一normalized URL重複只留一個；不同URL不得因priority較低就靜默丟棄，需在review顯示來源類型。

### URL normalization

- scheme/host lowercase、移除default port、正規化IDNA、移除fragment；
- path只做不改變resource語義的percent-encoding normalization；
- 不自動跟redirect，不發network request；
- query parameter移除與否不得讓危險URL變安全；先檢查原始URL。

### Blocking/rejection policy

拒絕並不得在log回顯完整URL：

- 非HTTP/HTTPS、relative、fragment-only、mailto、tel、file；
- username/password userinfo；
- localhost、`.local`、loopback、private、link-local、multicast、unspecified、reserved literal IP；
- 明確內部/admin路徑與host模式；
- token/api key/auth/signature/session/credential等敏感query key；
- 可疑signed/tokenized URL、短網址、搜尋redirect或已知tracking redirect；
- control characters、解析歧義、超長或無host URL。

DNS解析可能造成網路與TOCTOU，不應在純snapshot parser內進行。對hostname採allow/deny policy；發布前若需network classification，另設隔離validator且不抓內容。

## 6. Early data minimization

處理順序必須先於任何staging serialize：

1. 讀cell到短生命週期記憶體object。
2. 判定public metric channel booleans與note policy。
3. 若只有口頭權限或備註明確禁止文字留存：立即建立redacted exclusion reference，清除statement、notes、evidence與raw cell display值。
4. restricted/pending套用各自最小化policy。
5. 只有通過此gate的records可進normalized staging。

oral-only assertion必須掃描所有candidate artifacts：Markdown、SQLite documents/chunks/FTS、embedding inputs、vector metadata、reports、logs與test snapshots，確保沒有正文或可逆衍生內容。不得為測試建立正式敏感fixture；只用抽象假資料。

## 7. Exposure governance

- 原始G-M checkbox與normalized `allowed_exposure_channels`都保留於可發布MET。
- `can_quote_externally`不能由「channels非空」推導；必須由明確written-use policy計算。
- oral-only永遠 `can_persist=false`, `searchable=false`, `publishable=false`。
- pending永遠 `official=false`, `can_quote_externally=false`。
- restricted與handle mapping永遠 `searchable=false`。
- Slack/internal search的channel mapping待決；未決前fail closed，不把「自媒體」默認等同Slack。
- 對外結果仍檢查status、can_quote與allowed channel三者。

## 8. Validation classes

### Structural blocking

- API/permission/response incomplete；
- critical sheet missing/renamed unexpectedly；
- header或required column不符；
- merge range非法/重疊/超出contract；
- dataValidation或boolean type不符；
- source bounds異常縮小。

### Identity blocking

- MREC/MET/BRD格式錯誤、空白（在要求已配置的stage）、重複；
- 已知ID mutation、reuse或entity type collision；
- BRD reference不存在/未核准；
- ID由公式產生而非純文字。

### Governance blocking

- oral-only正文進可持久化candidate；
- restricted/pending進Official publish set；
- unsafe URL被標為active；
- channel policy未知卻試圖發布；
- denylist input缺失；
- planned archive超過gate或無完整current snapshot證明。

### Needs review

- BRD無唯一mapping、Handle/website各指向不同BRD；
- 多個URL candidates；
- title存在但URL缺失（可依既定規則轉incomplete，但整批正式發布策略仍需報告）；
- exact duplicate merchant fields；
- entity type不明。

### Warning

- 允許但需維護的空optional field；
- maintenance date過舊；
- alias或taxonomy normalization建議；
- 不影響authority的display格式差異。

## 9. Source consistency與deletion gate

- F1/F2使用相同ranges、fields與canonicalization。
- 任何read error、sheet count/critical sheet變化、header drift、last row大幅縮小、ID coverage下降都禁止archive與publish。
- 與last-known-good比較每sheet physical rows、valid entity IDs、publishable entities、excluded/incomplete counts。
- threshold必須同時考慮absolute與percentage；上線前以dry-run歷史數據校準。
- archive list需逐ID列出previous lineage與current absence evidence，不含敏感正文。
- 第一次live batch建議所有archive人工核准；來源不健康時即使人工核准也不得繞過blocking gate。

## 10. Reports與audit

允許記錄：batch ID、sheet/row/field、record ID、validator code、severity、排除原因、counts、hashes、timestamps。

禁止記錄：oral-only論述/備註/URL、credentials/token、完整unsafe URL、正式API response、restricted customer正文、pending claim正文（除非在明確受控人工review artifact且政策允許；本流程預設不允許）。

例外訊息使用穩定error code，例如 `METRIC_ORAL_ONLY_EXCLUDED`, `URL_PRIVATE_HOST_REJECTED`, `SOURCE_RANGE_SHRINK_BLOCKED`，避免把cell value插入message。

## 11. 測試要求

- synthetic CellData matrices覆蓋formula/effective value、Rich Text多連結、merge、hidden sheet、checkbox validation與unicode。
- table-driven URL attack corpus；全部離線，不做DNS/HTTP。
- property tests驗證merge不越界、空白不forward fill、serialization deterministic。
- mutation tests將oral-only正文標記為sentinel，掃描所有產物bytes與logs皆不得出現。
- duplicate/mutated/reused ID與row reorder/insert測試。
- F1/F2變動、API截斷、critical sheet missing、mass decline必須blocking且active release完全不變。
