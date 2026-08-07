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
| Brand | BRD | `^BRD-[0-9]{4,}$` | 人工核准後的品牌主檔 |
| Source Record | MREC | `^MREC-[0-9]{4,}$` | Apps Script配置後的案例來源列 |
| Public Metric | MET | `^MET-[0-9]{4,}$` | Apps Script配置後的正式論述列 |
| Manual Enrichment | ENR | `^ENR-[0-9]{4,}$` | 人工建立與核准的Obsidian筆記 |

四位數是最低寬度，不應把最大值限制在9999；配置器需以整數sequence管理並輸出zero-padded純文字。

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

第一版可採穩定composite identity：`<MREC>:<asset_type>`，其中asset type為 `article|video|podcast|news`。這適用於來源每個H-K cell最多代表一個邏輯asset的既定結構。

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
- 有title無safe URL建立incomplete asset。
- 同cell抽出多個互不相同URL時不得任選，標記needs_review。
- 未來若一個cell確定要表達多個assets，需先新增獨立永久Asset ID；不得以陣列位置或URL作identity。

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
enrichment_id: ENR-0001
related_entity_id: BRD-0003
review_status: approved
searchable: true
approved_by: james
approved_at: 2026-08-06
allowed_channels:
  - internal_search
approval_content_hash: sha256:...
```

- 只接受 `99_Manual_Notes/Approved_Enrichment/` 下的managed-boundary外人工筆記。
- `related_entity_id`必須在當前或archived Official registry存在。
- approval hash由semantic-normalized body與會影響治理的frontmatter共同計算。
- normalization忽略行尾空白、空白行數與無語義Markdown格式差異；標題、文字、link、relation或治理欄位改變即失效。
- 失效筆記留在Vault，但不進Enrichment index，並產生needs_review。

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
