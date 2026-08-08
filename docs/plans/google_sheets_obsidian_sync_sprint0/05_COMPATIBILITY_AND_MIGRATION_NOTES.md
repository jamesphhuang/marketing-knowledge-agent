# Compatibility and Migration Notes

## Compatibility strategy

Sprint 0採「new contracts + explicit compatibility boundary + targeted tests」。不把新Google canonical flow直接接到現行CLI，也不刪除legacy Markdown pipeline。

```text
Legacy (保持)
local XLSX → excel preview → review/apply → managed Markdown → content_index reparse

Sprint 0 (新增、未接production)
synthetic CellData → typed extraction → early minimization → canonical entities
                  └→ link/URL/asset → CapturedContent contracts
canonical metadata + CapturedContent → Release contract (no render/activation)
```

## 加入 CellData contract而不破壞legacy

- 新增`sheets_contracts.py`，不修改`read_xlsx_workbook()`回傳型別。
- Google-shaped DTO不可冒充現有row matrix；未來adapter應明確把snapshot轉進new normalizer。
- 現有XLSX tests繼續保證legacy behavior；new tests驗Google fields/merges/rich links。
- Sprint 1前不在`cli.py`新增Google command，不在`pyproject.toml`加Google SDK。

## 加入 normalized models而不擴張 DocumentMetadata

- `DocumentMetadata`目前被ingestion、retrieval、query planning、Slack及one-off executors廣泛使用；Sprint 0直接加入BRD/MREC/MET/CapturedContent欄位會造成大範圍相容風險。
- 新增`canonical_models.py`保存BRD/MREC/MET及lineage/lifecycle/eligibility。
- 未來建立單向adapter：canonical record → legacy `DocumentMetadata` projection，僅供dual-run/parity；禁止反向把Markdown/DocumentMetadata當canonical authority。
- Adapter不得丟失permanent ID、citations、metadata、freshness/status warning；若legacy schema無法表達，應在parity report列gap，不塞進notes字串。

## 提前 oral-only minimization而保留late defenses

- New path固定為`SourceCell（僅暫態） → WP5 early minimization → PersistenceEligibleMetricInput → PublicMetric`；`SourceCell`與未經gate的normalized intermediary不可序列化、持久化或直接建立`PublicMetric`。
- 被排除的輸入在canonical `PublicMetric`建構前回`ExcludedSourceRef`，因此沒有claim可交給renderer/index。
- 現有`metadata_allows_written_external_use`、Slack filters與denylist仍保留作defense in depth，不作主要保護。
- `excel_ingestion.normalize_public_metric_row`暫不修改，因其輸出與review/apply/historical fixtures綁定；後續migration先讓new importer dual-run，再逐步退役legacy oral-only payload。
- 過渡期tests必須清楚區分：legacy test證明舊資料在presentation被擋；new test證明新canonical path從未持久化。

## 加入 CapturedContent而不碰production capture/index

- `CapturedContent`不繼承`Document`，避免source path與Markdown body成為identity/authority。
- Sprint 0 fetch protocol只有interface/DTO；沒有requests client、HTTP library、redirect或credential。
- WP13只從注入的synthetic spans輸出新chunk metadata／identity，不定義production chunk-size、overlap或section splitting algorithm，也不直接呼叫`SQLiteIndex.rebuild`。
- 後續Sprint 4可建立canonical chunk → candidate Official index adapter；在parity完成前legacy `chunking.py`／`indexing.py`保持。

## 避免Markdown成為未來authority

- `CanonicalReleaseInputs`只接受canonical metadata與CapturedContent/revision references。
- Release contract及integration negative test拒絕Markdown path/parser型輸入。
- `content_index.py`保持legacy builder並標示migration-only；Sprint 0不增加新record types或capture parsing。
- Sprint 3/4應從同一immutable inputs分別呼叫Obsidian renderer與Official index builder；Markdown只做checksum/parity，不被回讀。

## Existing module migration classification

| Module | Sprint 0 treatment | Migration note |
| --- | --- | --- |
| `models.py` | 保持不動 | future one-way projection adapter，非canonical home |
| `ingestion.py` | 保持不動 | future legacy Markdown input only |
| `excel_preview.py` | 保持legacy | new CellData path另模組；Sprint 1再接Google adapter |
| `excel_ingestion.py` | 保持legacy | normalization helpers可小心adapter；oral-only行為不複用 |
| `asset_metadata.py` | 保持legacy | tracking/multi-candidate語義可參考；new URL safety獨立 |
| `asset_metadata_preview.py` | 保持legacy | whole-cell hyperlink與row IDs不當new authority |
| `governance.py` | 保持並作defense | early minimization在new normalization layer |
| `chunking.py` | 保持legacy | WP13不重用既有identity，也不承諾重用splitter；production splitting algorithm延後 |
| `indexing.py` | 保持legacy | Sprint 4建立candidate canonical index builder |
| `content_index.py` | 保持legacy，不擴充 | future dual-run後退役Markdown reparse |
| `obsidian_sync.py` | 保持legacy | plan/hash/rollback概念可參考；Sprint 3 sibling renderer另建 |
| `cli.py` | 不修改 | dry-run/live commands需後續明確授權 |
| one-off store/governance executors | 不作generic core | 只參考manifest/rollback patterns，不抽取私有helper造成耦合 |

## Migration checkpoints after Sprint 0

1. Sprint 1：synthetic reader替換為另案授權的read-only adapter，只做dry-run。
2. Sprint 2：ID writer/registry及normalized batch另案實作；保留legacy row alias mapping。
3. Sprint 3：canonical Obsidian candidate renderer，temporary Vault dual-run。
4. Sprint 4：canonical Official candidate index，與legacy Markdown-derived DB做citation/metadata/governance parity。
5. Sprint 5：完整Release coordinator、F2、journal/rollback/active pointer。
6. 全部parity、sentinel、conservation與人工核准通過後，才逐步停用legacy Markdown authority。

## Rollback boundary

Sprint 0新增模組未接任何entry point；回滾只需移除new imports/tests/feature wiring，不改現行runtime。若implementation發現必須修改legacy module才能完成單包，應先停止，確認是否能以adapter完成；不能時重新規劃，不在同WP擴張migration。
