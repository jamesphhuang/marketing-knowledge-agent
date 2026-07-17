# T. Retrieval Quality and Typed Query Constraint Architecture

## 1. Design Goal

欄位精確查詢先建立合法候選集合，語意搜尋只在候選集合內排序。CLI、Slack、agentic 與未來 Web UI 共用同一個 query plan 與 structured result contract。

## 2. Field Registry

Canonical source 位於 `query_planning.FIELD_REGISTRY`。文件表只摘要；code 與文件不一致時以 code 為準並回報。

| field | current source | type | operators | behavior | availability |
| --- | --- | --- | --- | --- | --- |
| `interview_year` | `interview_year` | integer | eq/in/range/gte/lte | hard | yes |
| `interview_date` | - | date | eq/range/before/after | hard | migration required |
| `entity_name` | `brand_name` | string | exact | canonical hard | yes, entity type limited |
| `merchant_name` | `brand_name` | string | exact | canonical hard | compatible alias |
| `partner_name` | - | string | exact | canonical hard | migration required |
| `merchant_handle` | `merchant_handle` | normalized string | exact | tier-0 hard | yes |
| `sales_category_lv1` | same | canonical enum | canonical_exact/in | hard | yes |
| `sales_category_lv2` | same | canonical enum | canonical_exact/in | hard | yes |
| `content_tags` | same | controlled collection | exact/all/any | hard | yes |
| `asset_type` | asset title fields | enum | exact/in | hard | safely derived |
| `asset_title` | article/video/podcast/news title | string | exact/lexical | exact can be hard | yes |
| `asset_url` | - | URL | exact | output/filter | migration required |
| `published_at` | - | date | eq/range/before/after | hard | migration required |
| `publication_status` | - | enum | exact/in | hard | recognized, fail closed |
| `content_status` | - | enum | exact/in | hard | migration required |
| `interview_status` | - | enum | exact/in | hard | migration required |
| `merchant_status` | same | string | exact/in | hard | yes |
| `review_status` | - | enum | exact/in | hard | migration required |
| `review_decision` | sync-only field | enum | exact/in | non-searchable | migration required |
| `governance_status` | - | enum | exact/in | hard | migration required |
| `claim_status` | same | enum | exact/in | hard | public metric only |
| `external_usage_status` | `can_quote_externally` | boolean | eq | governance hard | safely mapped |
| `citation_status` | `can_quote_externally` | boolean | eq | governance hard | compatible alias |
| `title` | `title` | string | exact/lexical/semantic | exact can be hard | yes |
| `notes` | `notes` | string | lexical/semantic | ranking only | yes |
| `metric_name` | `metric_name` | string | exact/lexical | canonical hard | yes |
| `source_record_id` | `source_sheet + source_row` | string | exact | hard | safely derived |
| `allowed_exposure_channels` | same | enum collection | exact/any | governance hard | yes |
| `can_enter_content_index` | same | boolean | eq | index hard | yes |

Normalization 使用 Unicode NFKC、casefold、trim 與重複空白壓縮。Handle 額外移除前導 `@`。Category、Tag、asset type 與 status alias 只接受 code 中明列 mapping。

## 3. Typed Query Plan

`TypedQueryPlan` 包含：

- raw / normalized query
- `structured_lookup` 或 `semantic_question`
- parsed terms and resolved entities
- typed constraints
- AND/OR operator
- hard filters and free-text terms
- requested asset types
- sort/grouping/fallback
- ambiguity flags, parser warnings, abstain reason
- supported / unsupported / ambiguous / invalid constraint lists
- `execution_blocked`

每個 constraint 包含 `support_status` 與不含敏感資料的 `reason`。Field Registry 驗證欄位、operator、searchable/executable 狀態與 metadata source；任何 unsupported / ambiguous / invalid hard constraint 都使整個 AND query fail closed，不得只執行可支援子集合。Executor 對未知欄位與未知 operator 明確 non-match。

```json
{
  "raw_query": "提供我三風製麵的內容",
  "normalized_query": "提供我三風製麵的內容",
  "query_mode": "structured_lookup",
  "constraints": [
    {
      "field": "entity_name",
      "value": "三風製麵",
      "operator": "exact",
      "match_type": "canonical_exact",
      "hard_filter": true,
      "source": "entity_resolver",
      "confidence": 1.0
    }
  ],
  "operator": "AND",
  "free_text_terms": [],
  "requested_asset_types": [],
  "fallback_policy": "abstain"
}
```

Plan 可透過 `to_dict()` / `from_dict()` round-trip，供 agentic plan、CLI debug 與未來 API 使用。

## 4. Entity Resolution

Resolution order：

1. merchant handle exact
2. merchant canonical name exact
3. partner canonical name exact（欄位上線後）
4. curated alias exact
5. year/range parser
6. explicit status alias
7. Category LV1 exact
8. Category LV2 exact
9. content tag exact
10. asset type exact
11. title/notes lexical
12. semantic fallback

完整 canonical name 可以出現在自然語句中，但 query 的部分字串不得反向匹配多個品牌。商家／夥伴同名時 plan 必須標示 ambiguity；現有 schema 沒有 partner 欄位，所以不做猜測。

## 5. Status Semantics

- `已上線` / `已發布` / `已公開` / `published` → 可辨識為 asset-level `publication_status`，但目前 formal data 無此欄位，因此 fail closed。
- `可對外引用` → `external_usage_status=true` → `can_quote_externally`。
- `已採訪` → unsupported，因 schema 無 `interview_status`；回 ambiguity，不映射到 published。
- `merchant_status` 只描述商家關係／營運狀態。
- `claim_status` 只描述 metric claim review。
- `review_status` / `review_decision` 目前不在 formal index，不可 runtime 猜測。
- record-level `status` 只供既有內容治理，不得套用成每個 asset 的 publication status。

完整日期先於年份區間與單一年份解析。`2025-07-01`、`2025/07/01`、`2025.07.01` 會保留為 unsupported date constraint，不得降級成 `interview_year=2025`。

## 6. Filtering and Ranking

Execution order：

1. query normalization
2. parser and resolver
3. TypedQueryPlan
4. runtime support validation；blocked plan 在 retrieval 前停止
5. `SearchFilters` intent gating
6. typed hard constraints
7. non-retrievable record type guard
8. FTS/vector scoring inside candidates
9. reranking inside candidates
10. restricted source filtering
11. structured aggregation or semantic generation
12. answer/citation governance gate

Across fields 預設 AND；明確「或／任一／其中之一」才使用 OR。多個互斥年份沒有 OR 或 range 時標示 ambiguity。Hard constraint 為 0 筆時不得移除條件或補 Top K。

## 7. Structured Result Contract

`StructuredRetrievalResult` 包含 query plan、matched entities、asset list、counts、warnings 與 abstain state。每個 `StructuredAsset` 包含：

- asset type and title
- URL / published_at（資料存在才填）
- publication status
- external usage status
- source record id / sheet / row
- citation label

Merchant case 的 article/video/podcast/news 只從對應非空 metadata 欄位建立；空欄位不渲染。每個資產建立獨立 citation label，即使同一 source row 有多個資產。
Structured contract 建立前也會掃描資產標題；denylist 命中的資產不會進 answer、citation 或 structured result，並留下不含敏感名稱的 removal warning。

Blocked result 另包含 supported / unsupported / ambiguous / invalid constraints 與 `execution_blocked=true`。asset-level publication status 缺失時保留 `null`，renderer 顯示「資料未提供」，不得使用 parent record status。

## 8. Renderer Contract

Retrieval 不組 Slack 文案。`structured_results` 先產生 channel-neutral contract 與文字；Slack 只做既有長度處理、citation/warning 區塊與 Markdown cleanup。

Structured output 顯示已套用條件、品牌／夥伴與資產數、實際資產、治理資格及 source row。缺 URL 或上線日期時顯示「資料未提供」，不推測。

## 9. Explain and Observability

```bash
mka explain-query "2025 居家生活 已上線 影片" \
  --db .mka/content_index.sqlite --intent external
```

輸出 query plan、constraint support lists、execution blocked、filter 前後 document counts、governance removed count、final entity/asset counts 與 abstain reason。禁止輸出 chunk text、source path、token、restricted record 或完整敏感 metadata。

## 10. Backward Compatibility and Migration

本 sprint 不改 SQLite schema。原因是現有 108 documents 的 metadata 已完整存於 `metadata_json`，可先以 Python deterministic filtering 驗證語意；既有 FTS/chunk/index rebuild 保持相容。

下一版 schema migration 建議：

1. 新增 `entity_lookup`：entity_type、canonical_name、normalized_name、handle、approved aliases。
2. 新增 `content_assets`：record id、asset type、title、URL、published_at、publication status。
3. 新增 typed columns/indexes：interview_year、category LV1/LV2、merchant handle、publication status。
4. 分開 merchant_name / partner_name，保留 entity_type。
5. 將 interview/review/governance status 分欄。

Rebuild procedure：備份 DB → 從 managed Vault 全量重建新 DB → 跑 eligibility/denylist/conservation/retrieval assertions → 原子替換。Rollback：保留上一版 DB，失敗時不替換。測試資料以 synthetic managed Vault 重建，不複製 restricted records。

第 2 項應由後續 Asset-Level Metadata Enrichment Sprint 執行；本 sprint 不修改 index schema、不重建正式 index，也不從 record status 推導 asset status。

## 11. Test Matrix

`tests/test_typed_query_retrieval.py` 覆蓋：名稱、Handle、Category、年份/range、完整日期、unsupported/invalid fail closed、狀態分流、asset type、AND、零交集、不存在名稱、空 asset、agentic 共用 hard constraints、plan round-trip、ambiguity 與 explain-query 安全輸出。既有 governance、LLM、Slack、index 與 citation tests 必須全綠。

## 12. Next Multi-Constraint Sprint

1. 將 curated aliases 移到可審核、版本化的 identity/taxonomy table。
2. 建 asset-level metadata migration，補 URL/published_at/status。
3. 補 partner entity 與同名 disambiguation UX。
4. 擴充 parser 的 explicit OR、date ranges、gte/lte 與 channel aliases。
5. 將 Python full-scan hard filtering 下推至 SQLite typed indexes。
6. 建 query-plan gold set 與 precision/recall evaluation，校準 semantic fallback。
