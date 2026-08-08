# Data Model and ID Specification

## 1. 共通規則

- 永久 ID 是identity；檔名、row number、名稱、Handle、URL與路徑都不是identity。
- 所有永久 ID必須為Google Sheet中的純文字，不得使用 `ROW()` 或其他公式。
- ID一經配置不可修改、回收或重新配發；archive後仍保留。
- 正式批次遇到空白、格式錯誤、重複或與registry衝突的ID即blocking。
- lineage欄位可隨來源位置變更；identity欄位不可隨來源位置變更。
- 所有relation只引用永久ID；顯示名稱只是projection。

格式：

| Entity | ID | Regex | Authority |
| --- | --- | --- | --- |
| Brand | BRD | `^BRD-[0-9]{4,}$` | 人工核准後，由standalone governance writer受控回填的品牌主檔 |
| Source Record | MREC | `^MREC-[0-9]{4,}$` | standalone Apps Script配置後的案例來源列 |
| Public Metric | MET | `^MET-[0-9]{4,}$` | standalone Apps Script配置後的正式論述列 |
| Manual Enrichment | ENR | `^ENR-[0-9]{4,}$` | 人工建立與核准的Obsidian筆記 |

四位數是最低寬度，不應把最大值限制在9999；配置器需以整數sequence管理並輸出zero-padded純文字。

### 1.1 Decision 4 — Permanent ID governance writer

Decision 4已確認選擇B：MREC／MET permanent ID allocator與BRD governance writer採external standalone Apps Script project，不採Spreadsheet-bound script。Google Sheets是business metadata／reviewed governance authority；GitHub是source code／specification authority；standalone deployment只是受控execution target，Script editor drift不構成authority。

Production writer的canonical target固定為Spreadsheet ID `15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM`，且必須同時通過explicit configuration與allowlist。不得以 `SpreadsheetApp.getActiveSpreadsheet()`作identity、fallback或target discovery；設定缺失、ID無效／不符、target無法讀取或sheet contract不符時一律fail closed。

Writer只負責：

- MREC allocation與validation；
- MET allocation與validation；
- 已完成人工審核且mapping明確的BRD controlled backfill；
- duplicate、malformed、missing、namespace與registry validation；
- write guard、write verification及不含敏感正文的audit-safe result／status。

寫入白名單固定為「商家／夥伴案例資料庫」M（MREC）、N（BRD）、O（ID Review Status），以及「可公開」對外數據N（MET）。其他business columns、G-M exposure channels與任何Slack欄位都不可寫；writer不得接受任意Spreadsheet ID、sheet、range或generic mutation指令。

MREC／MET allocation contract：

1. 既有合法ID永不覆寫；archived／retired ID永不回收或重配。
2. 配置前掃描active、archived及reserved registry；duplicate時fail closed，不配置新ID掩蓋collision。
3. Malformed、unknown namespace、mutation、registry conflict或無法證明safe next sequence時標記needs_review／blocking。
4. ID只由deterministic namespace與受治理sequence產生，不用row、`ROW()`、名稱、URL或位置作identity；row reorder／insert／move後仍保留原ID。

BRD backfill contract：

1. 只回填already-approved mapping，並驗證format、duplicate與conflict。
2. Blank BRD不得被writer解讀成「建立新品牌」；不得依名稱 alone自動merge／split，也不得自動歸屬ambiguous mapping。
3. 不確定或互相衝突的evidence只產生needs_review／blocking結果，不寫BRD。

每次allocation須使用exclusive lock或等價concurrency guard，在取得guard後重讀current values與registry，並在同一critical section內完成allocation、reservation／registry update與write。Concurrent executions不得配出duplicate；lock／readback／write verification失敗時fail closed。採用`LockService`與否屬實作選項，不是本次產品決策。

此standalone writer不得負責Google metadata sync／extraction、linked capture、HTTP／HTML處理、CapturedContent、Markdown／Obsidian、FTS／vector、Slack／RAG、query answering、exposure決策或Manual Enrichment approval。Read-only Service Account path維持隔離，Marketing Knowledge Agent不得因此取得一般Google write能力。`clasp`、CI/CD、branch／tagging、secret manager、deployment owner、execute-as、service identity與API deployment仍是open implementation details，須後續另案確認與授權。

## 2. Canonical records

### 2.1 Brand

```yaml
brand_id: BRD-0001
canonical_name: Example Brand
entity_type: merchant | partner | other
handle: example
official_website: https://example.com/
aliases: []
mapping_status: approved | needs_review | excluded | archived
notes: null
created_at: timestamp
updated_at: timestamp
```

- `brand_id`唯一且不可變。
- canonical name、handle、website可更新，變更不產生新BRD。
- handle與normalized website是候選matching key，不是identity。
- 任一key命中多個BRD時blocking/needs_review，不自動合併。
- 名稱相似只能作review evidence。
- `entity_type`變更需人工review，因它會改變Vault位置與presentation。

### 2.2 Source Record

```yaml
source_record_id: MREC-0001
brand_id: BRD-0001
interview_year: 2025
record_status: active | incomplete | archived
source_name: original display value
sales_category_lv1: value
sales_category_lv2: value
tags: []
notes: null
source_lineage: {...}
archived_at: null
archived_reason: null
```

- 一列採訪紀錄是一個MREC；同品牌多列保留多個MREC。
- 不因相同BRD、Handle、名稱或URL而dedupe。
- 只有已指定的比較欄位完全相同才產生duplicate review item；review前不merge/overwrite。
- BRD未核准時，MREC可存在staging，但不得進Official publish set。

### 2.3 Content Asset

Decision 8已確認選擇A：第一版一個H-K source cell最多代表一個logical Content Asset。H、I、J、K依序代表 `article`、`video`、`podcast`、`news`；每個MREC的每個asset type最多建立一個logical Content Asset。

第一版穩定composite identity為 `<MREC>:<asset_type>`。不新增AST permanent ID，也不建立Asset ID allocator或registry。

```yaml
asset_key: MREC-0001:article
source_record_id: MREC-0001
brand_id: BRD-0001
asset_type: article
title: Approved title
url: https://example.com/story
sync_status: active | incomplete | archived
url_source: rich_text_run | cell_hyperlink | hyperlink_formula | cell_text
source_lineage: {...}
```

- 欄位空白表示沒有asset，不建立空record。
- URL候選完成安全檢查、canonicalization與dedupe後，0個safe URL但有有效title時建立incomplete asset；`searchable=false`，不得進Official SQLite／FTS／vector／Slack，不猜測URL，也不得外部搜尋補URL。
- 只有1個distinct safe canonical URL時，才可再依其他governance與identity條件判定active。
- 2個以上distinct safe canonical URLs時標記needs_review；不得依priority任選，不得自動拆成多個Content Assets，人工整理前不得進Official publish set。
- rich-text run、whole-cell hyperlink、`HYPERLINK` formula或cell text取得的候選，若canonicalization後為同一URL，先dedupe為一個，不構成multi-link conflict。
- URL、Rich Text run position與array index只可作candidate provenance，不得作permanent identity。
- 人工修正應在Google Sheets canonical source整理成一個cell對應一個logical asset；read-only sync不自動寫回。
- 未來若業務確認同一cell需要多個正式Content Assets，必須另開architecture／migration decision後才能評估AST permanent ID；第一版不得預先實作AST。
- 本決策不改變既有needs_review／whole-batch publish policy。
- Decision 9下，Google metadata仍決定Content Asset identity／title／URL／governance；核准linked webpage只以`CapturedContent(authority_role=primary_content)`提供body，URL與captured hash均不改變`asset_key`。Canonical capture model與revision規則見`10_LINKED_CONTENT_CAPTURE_AND_RAG_SPEC.md`。

### 2.4 Public Metric

```yaml
metric_id: MET-0001
metric_type: value
indicator: value
approved_statement: value
maintenance_updated_at: date
evidence_urls: []
allowed_exposure_channels: []
sync_status: active | archived
can_quote_externally: true
source_lineage: {...}
```

- 每個非空C欄正式論述是一筆MET；merged A/B/F只提供context/evidence，不增加count。
- E欄是maintenance time，不可推論為measurement period。
- G-M checkbox映射須保存原始欄位與policy-normalized channels。
- oral-only不是可持久化PublicMetric。normalize後轉成redacted exclusion reference；即使MET已配置也不得保存statement。
- pending metric是governance-only record，不可偽裝成MET Official record。
- F「參考新聞連結」只建立MET evidence relationship；其CapturedContent必須標示`authority_role=evidence`，正文不得自動升格為Public Metric claim，也不得擴張G-M核准渠道。

### 2.5 Taxonomy

```yaml
taxonomy_id: category:lv1:<canonical-slug>
taxonomy_type: category_lv1 | category_lv2 | feature | tag
canonical_label: value
aliases: []
```

若初期Google Sheet沒有永久taxonomy IDs，可使用由受治理canonical label產生的namespaced deterministic key；label rename需alias/migration review，不能默默建立第二個taxonomy。這是projection key，不得與BRD/MREC/MET sequence混用。

### 2.6 Manual Enrichment

```yaml
record_type: manual_enrichment
enrichment_id: ENR-0001
related_entity_id: BRD-0003
review_status: approved
searchable: true
approved_by: REVIEWER-CANONICAL-ID
approved_at: 2026-08-06
allowed_channels:
  - internal_search
approval_content_hash: sha256:...
```

- 只接受 `99_Manual_Notes/Approved_Enrichment/` 下的managed-boundary外人工筆記。
- `related_entity_id`必須在當前或archived Official registry存在。
- Approved eligibility要求`record_type=manual_enrichment`、`review_status=approved`、`searchable=true`、既有contract有效的`approved_at`，以及允許`internal_search`的`allowed_channels`；任一條件缺失均不得進Enrichment index。
- `approved_by`是單一非空stable canonical reviewer ID，必須以deterministic exact membership命中authorized approver whitelist；不得接受任意非空字串、display name／暱稱／自由文字推定、substring／fuzzy match或大小寫／格式猜測。
- Whitelist authority必須位於enrichment note之外的單一受控configuration／governance source；note frontmatter不得宣告、覆寫或增加authorized approver。Whitelist實體位置與canonical reviewer ID scheme仍是implementation/open design item。
- `approved_by`缺失／空白／未授權／identity無法exact match，或whitelist無法載入／設定無效時fail closed；`searchable=true`不得繞過approval eligibility。Note可留在Vault，但不進Enrichment index。
- approval hash由semantic-normalized body與會影響治理的frontmatter共同計算。
- normalization忽略行尾空白、空白行數與無語義Markdown格式差異；標題、文字、link、relation或治理欄位改變即失效。
- 失效筆記留在Vault，但不進Enrichment index，並產生needs_review。
- Approver日後移出whitelist時，既有approval採retroactive revocation或approval-time authorization snapshot尚未裁決；不得在本決策中預設。

## 3. Source lineage

每個可持久化canonical record至少保存：

```yaml
spreadsheet_id_hash: sha256:...   # manifest可存明文ID；一般record不必重複
sheet_id: 123
sheet_title: 商家/夥伴案例資料庫
source_row: 7
source_columns:
  source_record_id: M
  brand_id: N
source_ranges:
  title: H7
source_fingerprint: sha256:...
sync_batch_id: SYNC-...
```

lineage只用於追查與diff，不參與identity。錯誤報告只引用sheet/row/field；涉及oral-only時不得附cell value。

## 4. Lifecycle

```text
candidate → needs_review → active → incomplete → active
                              │          │
                              └──────────┴→ archived → active (restore)
```

- `candidate`/`needs_review`只在staging，不進Official。
- `active`滿足identity、governance與publish eligibility。
- `incomplete`只適用可保留但不可搜尋的asset/source projection；目標資料夾為98_Incomplete。
- `archived`是soft delete，保留identity與history，不進active indexes。
- restore沿用相同ID，清除active view中的archive reason但保留lifecycle audit event。
- identity collision、ID mutation、retired ID被另一entity使用均是blocking。

## 5. Registry與immutability驗證

每次批次將current snapshot與last-known-good registry比較：

1. 同一ID只能對應同一entity type。
2. 一個source row在重新排序後以欄內ID找回，不以舊row定位。
3. 已知ID消失形成archive candidate，不立即archive。
4. 新列有MREC/MET但ID已屬於另一record，blocking。
5. 舊record在新列出現相同ID，視為move；lineage更新，不視為delete+create。
6. ID字串被改成另一合法ID仍是mutation，須與registry/diff判斷並blocking。
7. BRD mapping改變需人工核准；不能只因Handle/網站變動自動重掛。

Registry至少保存ID、entity type、first seen batch、last seen batch、current lifecycle、retired flag與不可變identity digest。

## 6. Brand initialization review

候選分組只產生建議，不回填BRD：

- normalized unique Handle命中；
- normalized public official website命中；
- 所有來源名稱與aliases；
- source rows/MREC；
- entity type evidence；
- ambiguous keys、conflicting websites、名稱註記等risk flags。

人工decision必須是 `approve_new`、`assign_existing`、`split`、`merge_candidates`、`exclude`之一，並明確填Assigned BRD。`merge_candidates`只是合併candidate groups，不得在未另行批准時合併兩個既有正式BRD。

## 7. Migration

現行row-derived IDs不能直接宣稱為permanent IDs。遷移順序：

1. freeze last-known-good與現行lineage snapshot；
2. 配置/驗證MREC與MET純文字欄；
3. 建立品牌candidate review與正式brand master；
4. 回填經核准BRD；
5. 產生legacy-row-ID → permanent-ID mapping artifact；
6. dual-build新舊Obsidian/index並做conservation/citation parity；
7. 人工核准release後才切換；
8. legacy IDs保留為aliases/audit evidence，不作新identity。

不得以檔名重命名成功作為migration成功判準；必須驗證relations、citations、archive state與search behavior。

## 8. Collision handling

- sequence collision：停止ID配置與sync，人工修復，不自動挑下一個掩蓋問題。
- duplicate BRD key：needs_review，不自動merge。
- filename collision：檔名使用可讀slug加永久ID，例如 `example-brand--BRD-0001.md`；永久ID保證path disambiguation。
- URL collision：同URL可被不同訪談引用，不代表同MREC或BRD；列review訊息但不dedupe。
- ENR collision：同ENR出現在兩個檔案或一檔宣告兩個ENR均blocking。
- taxonomy slug collision：以namespace/type與canonical registry解決，不以覆蓋檔案解決。
