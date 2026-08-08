# Obsidian and Indexing Specification

## 1. Authority boundary

- Google normalized metadata與相容的CapturedContent revision set是Official canonical inputs；Official Obsidian Markdown、SQLite／FTS與vector皆是其sibling projections。
- Index builder不得解析剛產生的Markdown來取得Official正文或metadata。
- Markdown可做輸出parity/smoke test，但不是authority。
- Linked webpage只提供captured body；不得覆寫Google parent identity、relations或governance。Decision 10規定第一版capture必須隨Google metadata sync建立同一完整Release，不得獨立refresh或activate。
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
  Evidence/
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
title: Original article title
source_url: https://example.com/story
canonical_url: https://example.com/story
captured_content_id: CAP-immutable-id
capture_status: success
captured_at: timestamp
last_successful_capture_at: timestamp
last_capture_attempt_at: timestamp
content_hash: sha256:...
parser_version: html-normalizer-v1
sync_status: active
governance_status: approved
searchable: true
source_sheet: 商家/夥伴案例資料庫
source_rows: [7]
sync_batch_id: SYNC-...
last_synced_at: timestamp
sync_managed: true
```

Primary article body包含原始標題、原始來源連結與`CapturedContent.clean_body`，保持合理heading結構。不得把fixed／precomputed AI summary寫成Official body；未來machine-generated abstract須為獨立derivative或明確`derived_summary: true`。

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

Public Metric的`searchable`／Official Index membership由persistence eligibility決定，不由`allowed_exposure_channels`是否含某一值決定。Generic internal query可檢索eligible metric，但result metadata必須保留G-M normalized channels；只有明確usage intent才以對應channel限制answer eligibility。

### Evidence Article

```yaml
record_type: evidence_article
authority_role: evidence
related_metric_id: MET-0001
evidence_relationship_id: EVID-immutable-id
captured_content_id: CAP-immutable-id
title: Evidence article title
source_url: https://news.example/story
canonical_url: https://news.example/story
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

Evidence Article必須投影到與Public Metric可區分的path／record type，body明示「Evidence／背景來源」；不得宣告或看似`approved_metric`。

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
approved_by: REVIEWER-CANONICAL-ID
approved_at: 2026-08-06
allowed_channels: [internal_search]
approval_content_hash: sha256:...
sync_managed: false
```

Manual enrichment不得宣告`source_sheet`、自行定義authorized approver whitelist或冒充Official permanent ID。`approved_by`必須是單一stable canonical reviewer ID，並由note外部受控authority作deterministic exact membership validation；identifier scheme與authority實體位置尚未指定。

## 5. Markdown body與Wiki Links

- 每個檔案只表示一個entity；Source Record不把四種asset正文混成單一表格。
- Brand頁連到MREC、categories/features/tags；MREC連到BRD及assets；asset反向連到MREC/BRD；metric可連到明確related entity，沒有來源relation時不猜。
- Link target採renderer已知的current projection path，顯示文字可讀；不可用名稱搜尋猜target。
- 每個relation都先通過ID referential integrity；dangling link為blocking或將該record隔離為incomplete，不能產生模糊link。
- Primary／Evidence captured body都由canonical `CapturedContent` renderer輸出；Markdown內容不得成為Official index的回讀輸入。
- Public Metric頁可連到Evidence Article，但連結不代表evidence正文已成為核准claim，authority label不可省略。
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
  release_id PK, metadata_sync_batch_id, source_fingerprint, schema_version,
  capture_policy_version, parser_version, capture_revision_set_hash,
  stale_capture_set_hash, stale_capture_count,
  built_at, normalized_hash, artifact_hash
)

entities(
  entity_id PK, record_type, canonical_name, lifecycle,
  governance_status, searchable, allowed_exposure_channels_json,
  can_quote_externally, source_lineage_json,
  release_id FK, sync_batch_id, metadata_json, body
)

relations(
  from_entity_id, relation_type, to_entity_id,
  release_id FK, sync_batch_id, PRIMARY KEY(...)
)

content_assets(
  asset_key PK, source_record_id, brand_id, asset_type,
  title, source_url, canonical_url, lifecycle, release_id FK, sync_batch_id
)

captured_contents(
  captured_content_id PK, asset_key, metric_id, evidence_relationship_id,
  authority_role, canonical_url, capture_status, captured_at, content_hash,
  parser_version, last_successful_capture_at, last_capture_attempt_at,
  searchable, release_id FK, sync_batch_id
)

chunks(
  chunk_id PK, captured_content_id FK, asset_key, metric_id,
  brand_id, source_record_id, authority_role, title, section_heading,
  ordinal, source_url, capture_status, captured_at, last_successful_capture_at,
  content_hash, text, embedding_json, release_id FK, sync_batch_id
)

chunks_fts(content=chunks or external-content FTS5)
```

- active candidate DB只含該batch可搜尋Official records；archive history由batch/registry保存，不混入query DB。
- Official Index membership先套persistence／search eligibility；G-M exposure booleans不得作generic internal retrieval的persistence gate。Search results必須保留`allowed_exposure_channels`，供requested usage intent的第二層治理使用。
- `authority_layer`可固定為Official或由repository boundary保證；不得讓Enrichment寫入同一table後靠弱filter隔離。
- FTS與vector chunks由相同`CapturedContent.clean_body`／section-aware chunker輸入；chunk IDs deterministic，全文不得被fixed summary取代。
- citation metadata直接引用permanent ID與lineage，不從Markdown path派生。
- stale captured body仍保留原`authority_role`；index result與citation metadata必須帶`capture_status=stale`、原`captured_at`、`last_successful_capture_at`與本輪`last_capture_attempt_at`，不得標成`success`。
- Evidence chunks固定保留`authority_role=evidence`與MET relationship；Primary chunks保留`asset_key`／MREC／BRD，兩者不得在index時合併authority。
- `release_id`是完整Release identity，不是capture-only ID；manifest雖記錄每個CapturedContent revision／hash，但revision沒有獨立active pointer。

## 8. Enrichment index

建議獨立SQLite/FTS/vector release：

- Parser只接受指定root、`record_type=manual_enrichment`、完整frontmatter、`review_status=approved`、`searchable=true`、allowed `internal_search`、符合既有contract的`approved_at`、有效semantic approval hash，以及精確命中authorized approver whitelist的canonical `approved_by`。
- 任意非空`approved_by`不構成核准；不得以display name、暱稱、substring、fuzzy、大小寫或格式猜測identity。Missing／empty／unauthorized ID、identity無法exact match、whitelist load／schema failure全部fail closed；note可留在Vault，但不得進Enrichment index。
- Authorized approver whitelist必須是note外部的單一受控configuration／governance authority；note frontmatter不得新增、覆寫或自我授權approver，parser／renderer也不得各自hardcode散落名單。
- unknown key可warning；會改變authority/permissions的衝突key blocking。
- related Official entity可以archived，但結果需顯示status warning；不存在則不索引。
- substantive hash mismatch使舊approval失效，即使舊`approved_by`仍存在也立即移除下一個candidate index；formatting-only change沿用既有semantic normalization contract。Active Enrichment release更新必須原子切換。
- result metadata標記 `authority_layer=enrichment`、ENR與related entity；citation顯示「內部補充」，不能冒充Official source。
- Decision 3只適用Manual Enrichment；Google Sheets Official records不經此approver whitelist。Reviewer ID scheme、whitelist storage location，以及approver移出whitelist後historical approval是否retroactively revoke，均保留為open design／governance問題。

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
- active manifest引用的CapturedContent parent IDs、content hashes與parser version全部相容，Obsidian body與FTS／vector chunk conservation一致；
- Decision 11 stale LKG只在canonical URL相同、先前成功capture、temporary failure、無policy阻擋且freshness gate通過時沿用；manifest明列stale capture與attempt lineage；
- stale沿用不得清空last-known-good body、不得更新原`captured_at`／`content_hash`、不得製造假revision，也不得將URL changed／never-successful／unsafe／blocked／paywalled／governance failure誤分類為temporary；
- metadata batch、release-pinned capture revisions、Obsidian、Official DB／FTS與vector只能由同一global active release pointer一起切換；
- previous active release可讀且rollback rehearsal已在相同schema版本通過。

`last_success.json`最後更新，至少包含active `release_id`、metadata sync batch、source fingerprint、release-pinned capture revisions／hashes、stale capture IDs／status／`captured_at`／`last_successful_capture_at`／`last_capture_attempt_at`、capture policy／parser versions、artifact checksums、previous release與commit time；不含敏感內容或raw HTML。這些capture revision欄位只記錄完整Release composition，不表示可獨立activate。

Release／batch operation record必須把`release_status`與`notification_status`分欄保存，並在嘗試Decision 5 Private Slack Ops通知前durably寫入final release result。Slack send不是publish contract或active pointer transaction的一部分；通知失敗只更新machine-readable `notification_status`，不得rollback已成功Release、改寫`release_status`、清除原始failure evidence或變更active composition。Manifest／journal只保存sanitized notification correlation、target policy result與error category，不保存Slack token、raw message-sensitive body或unredacted exception。

## 11. Slack consumption

- Slack handler依active pointer開Official repository，不接受任意DB path作production default。
- Slack／internal search是Official retrieval surface，不是exposure channel；第一版沒有Slack-specific checkbox、N欄、source permission或persistence gate，也不得把H「自媒體」當成Slack permission。
- Private Slack Ops另屬maintainer-only operational notification surface，不是Official／Enrichment repository consumer，也不得把failure alert送入search response thread；即使共用Slack App，authorization、schema、exposure與audit仍須獨立。
- 預設query只打Official；明確 `include:enrichment` 才加查Enrichment。
- merge只在presentation層並保持兩組結果/authority label，不合併metadata或citation。
- Generic internal query依authority、persistence eligibility、governance status與`searchable`取回Official結果，不要求任一G-M為true；結果保留allowed channels且不得暗示全渠道可用。
- Query intent明確要求Saleskit、官網、廣告等用途時，generation／presentation才套對應G-M、`can_quote_externally`、status與citation authority；該channel為false時只擋此用途，不從generic corpus移除record。
- Oral-only與pending不進Official Index；restricted依既有governance不得進一般Official Search。Primary／Evidence依其authority、governance、`searchable`與capture release policy，沒有Slack-specific gate。
- audit log記query hash/length、intent、result IDs、authority layer、decision codes；敏感/blocked query不記原文。

### Decision 6 response budget

- Official Index與retrieval corpus保存全部符合eligibility的Content Assets、captured full-text chunks與Public Metrics；`content_item_cap`、`metric_item_cap`及`rendered_message_budget`只在response presentation套用，不得減少ingestion、index rows、FTS／vector corpus或upstream retrieval candidates。
- Pipeline必須先完成authority、persistence／governance、query intent、requested G-M channel、dedupe與rerank，再分別套Content與Public Metric item caps，最後以接近實際Slack output的representation套overall rendered budget。被oral-only、restricted、pending、non-searchable、wrong channel或其他fail-closed gate排除的record不占quota。
- Result metadata須攜帶record type、permanent ID、authority role、allowed channels、citation、freshness與rendering eligibility，讓renderer能把Public Metric完整claim、identity、citation與channel語義作一個atomic item。完整item放不下時不加入，不得任意string truncate或把citation／authority拆離。
- `shown_count`、eligible `remaining_count`與`more_results`或等價signal只由post-governance／post-intent eligible set計算；不得把被治理排除的records算入remaining count而洩漏或誤導。更嚴格的sensitive aggregate policy優先。
- Cap值、budget單位（characters／bytes／blocks／serialized payload／hybrid）、Content subtype cap／ratio、cursor、next-page command、page token、TTL、interactive button與message splitting均保留configuration／UX implementation，不在本Audit寫死。
- Private Slack Ops不使用knowledge-result cap／pagination；其failure alert依Decision 5獨立sanitized operational message schema。

## 12. Full-text retrieval與query-focused summary

- eligible corpus先依active manifest、`searchable`、authority與governance建立；FTS與vector都必須搜尋captured全文chunks，不得只搜Google title、tags或precomputed summary。
- keyword與semantic candidates合併後，先套status、authority與敏感資料filter；只有query明確要求usage channel時才加入對應G-M／`can_quote_externally` filter。完成dedupe後再rerank，最後才依Decision 6套output caps並組裝atomic citations／rendered items。
- 回答層只接收通過gate且與query相關的passages，產生query-focused summary；不得用generic整篇summary替代retrieval。
- citation回到原始source URL、captured content／chunk IDs、Content Asset或MET relationship、source lineage與capture freshness；stale結果明示`capture_status`、原`captured_at`、`last_successful_capture_at`與本輪attempt時間。
- `authority_role=evidence`的passage可說明背景，但不得被表述成`approved_metric`；AI summary永遠是derived answer，不得覆寫captured body。
- Primary Article可在通過既有governance／`searchable`／capture release policy後進internal retrieval，不需要Slack checkbox；Evidence可搜尋仍不構成Public Metric claim或任何G-M授權。
- Full-text Article與eligible Public Metric的searchable corpus不受display caps影響；Decision 6只在完成retrieval、filtering、dedupe與rerank後選擇可完整render的top eligible results。
