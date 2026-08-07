# Obsidian and Indexing Specification

## 1. Authority boundary

- Official Obsidian Markdown、Official SQLite/FTS與Official vector皆是同一normalized batch的projection。
- Index builder不得解析剛產生的Markdown來取得Official正文或metadata。
- Markdown可做輸出parity/smoke test，但不是authority。
- `sync_managed: true` 的檔案由sync完全管理；人工不得在managed區直接加入會被視為Official的內容。
- 人工補充只位於指定Enrichment namespace，且永遠不回寫Google Sheets或覆蓋Official。

## 2. Vault結構

```text
01_Entities/
  Merchants/
  Partners/
02_Content/
  Articles/
  Videos/
  Podcasts/
  News/
03_Public_Metrics/
04_Taxonomy/
  Categories/
  Features/
  Tags/
90_Sync/
  manifests/
  reports/
  last_success.json
98_Incomplete/
  Missing_Content_Links/
99_Manual_Notes/
  Approved_Enrichment/
```

Managed roots為01、02、03、04、90、98；99是人工root，sync只能讀取/驗證，不得重寫正文。若需寫validation sidecar，放90_Sync且不得在99內新增檔案。

## 3. 檔名與路徑

- 可讀slug加永久ID：`canonical-name--BRD-0001.md`、`article-title--MREC-0001-article.md`、`metric-label--MET-0001.md`。
- identity只讀frontmatter永久ID，不從filename反推。
- slug normalization需固定unicode normalization、case與非法字元規則；相同slug由ID自然區分。
- rename只代表projection path update；manifest記錄previous path，link renderer在同批次重建全部managed Wiki Links。
- path traversal、reserved names、過長path或case-fold collision為blocking。

## 4. Frontmatter schemas

### Brand

```yaml
record_type: brand
brand_id: BRD-0001
canonical_name: Example Brand
entity_type: merchant
related_source_record_ids: [MREC-0001]
sync_status: active
governance_status: approved
searchable: true
source_sheet: 品牌 ID 對照
source_rows: [7]
sync_batch_id: SYNC-...
last_synced_at: 2026-08-06T09:00:00+08:00
sync_managed: true
```

### Source Record

```yaml
record_type: source_record
source_record_id: MREC-0001
brand_id: BRD-0001
related_asset_keys: [MREC-0001:article]
sync_status: active
governance_status: approved
searchable: true
source_sheet: 商家/夥伴案例資料庫
source_rows: [7]
sync_batch_id: SYNC-...
last_synced_at: timestamp
sync_managed: true
```

### Content Asset

```yaml
record_type: content_asset
asset_key: MREC-0001:article
source_record_id: MREC-0001
brand_id: BRD-0001
asset_type: article
canonical_name: Approved title
url: https://example.com/story
sync_status: active
governance_status: approved
searchable: true
source_sheet: 商家/夥伴案例資料庫
source_rows: [7]
sync_batch_id: SYNC-...
last_synced_at: timestamp
sync_managed: true
```

### Public Metric

```yaml
record_type: public_metric
metric_id: MET-0001
canonical_name: Metric label
allowed_exposure_channels: [website]
can_quote_externally: true
sync_status: active
governance_status: approved
searchable: true
source_sheet: 「可公開」對外數據
source_rows: [7]
sync_batch_id: SYNC-...
last_synced_at: timestamp
sync_managed: true
```

### Taxonomy

```yaml
record_type: taxonomy
taxonomy_id: category:lv1:example
canonical_name: Example
taxonomy_type: category_lv1
related_entity_ids: [BRD-0001]
sync_status: active
governance_status: approved
searchable: true
source_sheet: 商家/夥伴案例資料庫
source_rows: [7]
sync_batch_id: SYNC-...
last_synced_at: timestamp
sync_managed: true
```

### Manual Enrichment

```yaml
record_type: manual_enrichment
enrichment_id: ENR-0001
related_entity_id: BRD-0001
review_status: approved
searchable: true
approved_by: james
approved_at: 2026-08-06
allowed_channels: [internal_search]
approval_content_hash: sha256:...
sync_managed: false
```

Manual enrichment不得宣告`source_sheet`或冒充Official permanent ID。

## 5. Markdown body與Wiki Links

- 每個檔案只表示一個entity；Source Record不把四種asset正文混成單一表格。
- Brand頁連到MREC、categories/features/tags；MREC連到BRD及assets；asset反向連到MREC/BRD；metric可連到明確related entity，沒有來源relation時不猜。
- Link target採renderer已知的current projection path，顯示文字可讀；不可用名稱搜尋猜target。
- 每個relation都先通過ID referential integrity；dangling link為blocking或將該record隔離為incomplete，不能產生模糊link。
- body欄位順序、frontmatter key order、list order與換行固定，確保deterministic bytes。
- 任何timestamp來自batch clock，不在單檔render時各自取now。

## 6. Incomplete與archived

### Incomplete

- title存在、URL不存在或不安全的asset渲染到 `98_Incomplete/Missing_Content_Links/`。
- `sync_status: incomplete`, `searchable: false`。
- body可顯示title、BRD/MREC relation與缺漏原因，不可猜URL。
- 不進Official documents/FTS/vector，也不計正式案例數。
- 下批取得safe URL後，同asset key移到02_Content並轉active；manifest記錄move。

### Archived

- canonical record保留ID、`archived_at`、`archived_reason`與last active batch。
- 歷史Markdown保存在managed archive area或immutable release history；不留在active search roots。
- active SQLite/FTS/vector完全不含archived record/chunks。
- restore使用相同ID重新生成active projection。
- 沒有通過source-health/deletion gate時不得建立任何archive artifact。

## 7. Official index schema

建議保留現行FTS/retrieval優點，但將authority與batch設為一級欄位：

```text
release_manifest(
  sync_batch_id PK, source_fingerprint, schema_version,
  built_at, normalized_hash, artifact_hash
)

entities(
  entity_id PK, record_type, canonical_name, lifecycle,
  governance_status, searchable, source_lineage_json,
  sync_batch_id FK, metadata_json, body
)

relations(
  from_entity_id, relation_type, to_entity_id,
  sync_batch_id, PRIMARY KEY(...)
)

content_assets(
  asset_key PK, source_record_id, brand_id, asset_type,
  title, safe_url, lifecycle, sync_batch_id
)

chunks(
  chunk_id PK, entity_id FK, ordinal, text,
  embedding_json, sync_batch_id
)

chunks_fts(content=chunks or external-content FTS5)
```

- active candidate DB只含該batch可搜尋Official records；archive history由batch/registry保存，不混入query DB。
- `authority_layer`可固定為Official或由repository boundary保證；不得讓Enrichment寫入同一table後靠弱filter隔離。
- FTS與vector chunks由相同canonical text/chunker輸入；chunk IDs deterministic。
- citation metadata直接引用permanent ID與lineage，不從Markdown path派生。

## 8. Enrichment index

建議獨立SQLite/FTS/vector release：

- parser只接受指定root、完整frontmatter、approved、searchable、allowed `internal_search`與有效semantic hash。
- unknown key可warning；會改變authority/permissions的衝突key blocking。
- related Official entity可以archived，但結果需顯示status warning；不存在則不索引。
- substantive hash mismatch立即移除下一個candidate index；active Enrichment release更新必須原子切換。
- result metadata標記 `authority_layer=enrichment`、ENR與related entity；citation顯示「內部補充」，不能冒充Official source。

## 9. Determinism與idempotency

- canonical JSON採UTF-8、固定key order、stable enum、stable list sort與明確null policy。
- renderer不依filesystem traversal順序、locale或當前時區。
- 同snapshot、policy/schema版本與batch logical time應產生相同normalized/artifact content hashes；batch ID可不同，但可變manifest欄位與content hash分離。
- apply相同release ID不得產生第二次mutation；若active已相同則no-op。
- smoke tests比較entity counts、relation integrity、Markdown/checksum manifest、SQLite rows、FTS/chunk conservation與vector dimensions。

## 10. Publish contract

發布candidate前全部成立：

- normalized batch validation零blocking/needs_review；
- oral/restricted/pending sentinel掃描為零；
- Obsidian managed tree checksum與manifest一致；
- Official DB quick_check、foreign_key_check、FTS/content counts與sample query通過；
- vector count/dimension/finite values通過；
- 每個active entity在要求的projection恰有一次；
- incomplete不在任何Official index；archived不在active index；
- F1等於F2；
- previous active release可讀且rollback rehearsal已在相同schema版本通過。

`last_success.json`最後更新，至少包含active batch、source fingerprint、artifact checksums、previous batch與commit time；不含敏感內容。

## 11. Slack consumption

- Slack handler依active pointer開Official repository，不接受任意DB path作production default。
- 預設query只打Official；明確 `include:enrichment` 才加查Enrichment。
- merge只在presentation層並保持兩組結果/authority label，不合併metadata或citation。
- 所有結果套現有typed query、denylist、external/written-safe與citation post-filter；新channel permission另加fail-closed gate。
- audit log記query hash/length、intent、result IDs、authority layer、decision codes；敏感/blocked query不記原文。
- Public Metric pagination/cap依 `08_DECISIONS_REQUIRED.md` 裁決後實作。
