# Implementation Roadmap and Test Plan

## 原則

- 每個Sprint保留現行active path，先dry-run/dual-run，再切換；不得一次重寫import、Vault、index與Slack。
- production adapter的認證架構已確認為read-only Service Account；建立憑證、正式連線、Apps Script與通知仍須各自依授權與未決政策後續處理。
- 每個Sprint遇到stop condition即停止，不以skip flag繞過。
- 測試一律使用synthetic CellData、temporary Vault/DB與抽象資料；不得複製正式oral/restricted/pending內容。

## Sprint 0：契約與安全測試基座

### Scope

- 建立Google reader protocol、CellData DTO、canonical serialization與synthetic fixtures。
- 定義BRD/MREC/MET/ENR、lineage、lifecycle、publish eligibility。
- 建立早期oral-only redaction與URL safety validator。
- 不建立production auth adapter，不寫Vault/index。

### Inputs

- 本審查文件、tracked現行models/governance/tests、人工合成CellData。

### Outputs

- versioned canonical schema；
- redacted normalization result；
- source fingerprint function；
- validation report schema與error codes。

### Tests

- formula/effective/formatted、rich text、merge、checkbox/data validation；
- URL attack table；
- row reorder/insert後ID不變；
- oral-only sentinel不出現在任何serialized result/log/exception；
- deterministic fingerprint golden tests。

### Acceptance criteria

- 全部離線測試通過；
- parser無raw response dump；
- permanent ID與lineage完全分離；
- 未決channel policy時fail closed。

### Rollback criteria

- 只新增未接線模組；移除feature flag/entry即可，不影響現行CLI。

### Stop conditions

- 無法以CellData表達既定merge/link/checkbox契約；
- oral-only正文進任何persistable object；
- identity仍依row/path。

### 人工核准點

- canonical schema與redaction report是否足以追查但不洩漏。

## Sprint 1：Read-only snapshot dry-run與品牌初始化

### Scope

- 依已確認的read-only Service Account架構實作production Google adapter；Spreadsheet授權與Google API scope均限唯讀，adapter不得取得write scope。
- 擷取必要hidden sheets/ranges與F1，不寫回。
- 產生品牌candidate grouping、ID缺漏/衝突及redacted diff preview。
- Apps Script仍不實作。

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

## Sprint 2：ID配置治理與normalized batch

### Scope

- 在Apps Script部署方式決策後另案實作MREC/MET allocator及BRD人工回填流程。
- importer驗證純文字、immutability、registry與brand master。
- 建立完整normalized batch與archive diff，但仍不發布。

### Inputs

- 核准品牌decision、ID欄位與Apps Script政策。

### Outputs

- ID allocation audit；
- brand master/readback validation；
- immutable normalized candidate；
- create/update/incomplete/archive/restore preview。

### Tests

- duplicate/reused/mutated/format/formula ID；
- concurrent allocator/lock property；
- row reorder、rename、Handle/URL change不改ID；
- archive/restore同ID；
- mass deletion/API truncation fail closed。

### Acceptance criteria

- 每個publishable MREC/MET/BRD均有效；
- registry conservation通過；
- 未核准BRD不進publishable set；
- archive仍只預覽。

### Rollback criteria

- ID不可回收或重配；錯誤配置須凍結並人工修復，不以rollback重用號碼。

### Stop conditions

- allocator可能覆寫現有ID；
- 品牌自動merge；
- archive候選無完整snapshot證明。

### 人工核准點

- 首批ID與brand master；
- 首批所有archive candidates。

## Sprint 3：Obsidian sibling renderer

### Scope

- 從normalized batch產生新entity tree、Wiki Links、incomplete與manifest。
- 在temporary candidate Vault dual-run；不碰正式Vault。
- 明確sync-managed/manual boundary。

### Inputs

- Sprint 2 immutable batch。

### Outputs

- candidate Obsidian tree與checksums；
- legacy vs new projection comparison；
- filename/path migration map。

### Tests

- 每entity一檔、frontmatter schema、referential links；
- unicode/case/path collision；
- deterministic bytes、idempotent rebuild；
- incomplete移轉active、archive/restore；
- namespace escape與manual note preservation。

### Acceptance criteria

- 不解析Markdown作authority；
- managed projection conservation=canonical publish set；
- 99 manual root零mutation；
- sensitive sentinel掃描零命中。

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

- Official SQLite/FTS/vector直接消費normalized batch。
- Enrichment獨立parser/index與semantic approval hash。
- 保留legacy Markdown-derived index做dual-run比較，不再擴充。

### Inputs

- canonical batch、approved Enrichment synthetic/temporary fixtures。

### Outputs

- candidate Official DB/vector；
- candidate Enrichment DB/vector；
- parity與governance report。

### Tests

- schema/foreign key/FTS/vector conservation；
- Official-only authority；
- incomplete/archived/oral/restricted/pending全排除；
- enrichment substantive edit失效、format-only edit不失效；
- Official/Enrichment collision不覆蓋；
-現行 typed retrieval/citation regression suite。

### Acceptance criteria

- 所有index rows可追到permanent ID與batch；
- Markdown修改不改Official candidate；
- Enrichment必須明確opt-in；
- byte/sentinel scan通過。

### Rollback criteria

- candidate index獨立；active path仍指向legacy DB。

### Stop conditions

- 任何index從Markdown重建Official；
- authority layer靠可省略filter才安全；
- substantive enrichment變更仍被索引。

### 人工核准點

- approved_by政策與首批Enrichment validation結果。

## Sprint 5：Release coordinator與rollback rehearsal

### Scope

- source F2、single-writer lock、commit journal、candidate checks、active pointer與last_success。
- Vault/Official DB/vector整批activation與recovery。
- scheduler依已確認政策執行：Asia/Taipei 09:00、09:30、10:00，總共最多3次attempt，不存在10:30第四次執行。

### Inputs

- 所有candidate builders、已確認的排程attempt政策，以及仍待決定的通知管道。

### Outputs

- generic release coordinator；
- crash recovery/rollback command；
- batch manifest與redacted notifications。

### Tests

- 每個commit step故障注入；
- process kill/restart recovery；
- 三次attempt各自重新取得／驗證source fingerprint，並覆蓋F1/F2 mismatch持續至第3次的情境；
- disk full/permission/checksum/SQLite corruption；
- mass deletion blocked時active全部不變；
- rollback rehearsal恢復上一成功batch。

### Acceptance criteria

- 任一blocking error零partial active state；
- last_success永遠指向完整可讀release；
- startup可自動辨識並安全恢復prepared journal；
- notification不含敏感正文。
- fingerprint不一致的attempt不得publish、archive或更新active release；第3次仍失敗時batch狀態為`failed`。

### Rollback criteria

- active pointer、Vault backup與journal可還原previous batch；rollback後再跑smoke tests。

### Stop conditions

- 任一故障可能讓Vault與index指向不同batch；
- rollback未實際演練；
- F2未重驗仍可commit。

### 人工核准點

- rollback rehearsal evidence、mass-deletion thresholds、正式schedule enable。

## Sprint 6：Slack Official／Enrichment切換

### Scope

- Slack repository依active release讀Official；明確include時才讀Enrichment。
- 套用channel policy、authority label、pagination/caps與safe audit。
- 不改寫retrieval/generation的citation/governance核心。

### Inputs

- channel permission與metric cap決策、active release APIs。

### Outputs

- Official-default Slack search；
- optional Enrichment results；
- migration/rollback feature flag與operational runbook。

### Tests

- default query永不回Enrichment；
- explicit include才顯示且標內部補充；
- oral/restricted/pending從DB層即不存在；
- channel/status/can_quote三重gate；
- no-result提示、pagination與metric cap；
- audit只存hash/IDs/decision codes；
-既有Slack external-intent/denylist/citation regression suite。

### Acceptance criteria

- Slack不讀legacy Markdown-derived index；
- Official/Enrichment citations不混淆；
- feature flag可即時回上一active search path；
-不啟用外部LLM也可完整離線測試。

### Rollback criteria

- 切回previous active release/repository flag，不需重建資料。

### Stop conditions

- 未決channel被默認映射；
- raw query或敏感內容進persistent audit；
-新renderer繞過現行citation/status/freshness warning。

### 人工核准點

- staged Slack output review；正式Bot切換需另行明確授權。

## 跨Sprint必跑回歸

- `models`, `governance`, `query_gating`, `retrieval`, `reranking`, `generation`, `structured_results`；
- Excel preview/apply/asset metadata既有測試，直到legacy flow正式退場；
- Obsidian plan/execute/rollback；
- content index與typed query；
- Slack renderer/interface/external governance；
- destructive migration、production Vault/DB、Google/Slack API與external LLM一律需獨立明確授權。
