# Sprint 1 WP1 Production Read-Selection Registry Human Freeze Authorization — 2026-08-13

## Freeze Checkpoint

- Repository: `marketing-knowledge-agent`
- Branch reviewed: `codex/impl/sprint1-wp0-google-response-mapper`
- Authorization base: `1db2b426f63c7938c9f03d51ffe19947c8712f43`
- Registry review result: `READY_TO_FINALIZE_SELECTION_REGISTRY = YES`
- New production schema conflicts: `NONE`

This record freezes only the S1-WP1 Production Read-Selection Registry. It does not authorize S1-WP1 implementation, Google authentication, Google API access, production data access, or a production smoke.

## Product / Governance Owner Approval

- Role: Product / Governance Owner
- Date: 2026-08-13
- Approval: `APPROVED`
- Decision: `SELECTION_REGISTRY_OWNER_APPROVAL = APPROVED`

The human user explicitly approved the finalized five-tab registry, its exact target and bounds, the pending positional schema, the deferred governance disposition, and the fail-closed schema-change policy.

Codex did not independently verify organizational authority.

## Frozen Registry Identity

```text
config_version = s1-wp1-prod-read-selection-v1
configuration_identity = sha256:e4dbf5e50b393729eabd6187590a9419a9a0f8741f97a36bfc2d48994ceac48e
canonical_spreadsheet_id = 15KVEGSpcdbMuFg1SO1aa099iQ_Kzo9my6pjWdP5O1BM
observed_spreadsheet_title = MKT 內容產出資料庫_店家/夥伴案例/對外數據
method = spreadsheets.get
configured_request_count = 1
configured_sheet_count = 5
configured_range_count = 5
includeGridData = true
```

Whole-workbook reads, whole-column ranges, open-ended ranges, runtime range discovery, fallback targets, and automatic range expansion are forbidden.

## Frozen Range 1 — Merchant Case

```text
range_id = merchant_case
exact_tab = 商家/夥伴案例資料庫
sheetId = 0
hidden = false
grid = 1018 rows × 35 columns
header_row = 6
first_data_row = 7
required_columns = A:L
MAX_ROW = 1018
exact_A1 = '商家/夥伴案例資料庫'!A6:L1018
ConfiguredRange = (sheetId=0,rowStart=5,rowEnd=1018,colStart=0,colEnd=12)
Expected_GridData_key = (sheetId=0,startRow=5,startColumn=0)
```

The exact header semantics for A:L are:

1. A — 採訪年份
2. B — 狀態
3. C — 商家 / 夥伴名稱
4. D — Handle
5. E — Sales Category LV1
6. F — Sales Category LV2
7. G — 內容相關標籤
8. H — 文章
9. I — 影片
10. J — Podcast
11. K — 新聞
12. L — 備註

Planned MREC, BRD, and ID Review Status fields are not part of this WP1 read selection.

## Frozen Range 2 — Restricted Customer

```text
range_id = restricted_customer
exact_tab = 「不可公開」客戶名單
sheetId = 1456785208
hidden = false
grid = 994 rows × 28 columns
header_row = 4
first_data_row = 5
required_columns = A:H
MAX_ROW = 994
exact_A1 = '「不可公開」客戶名單'!A4:H994
ConfiguredRange = (sheetId=1456785208,rowStart=3,rowEnd=994,colStart=0,colEnd=8)
Expected_GridData_key = (sheetId=1456785208,startRow=3,startColumn=0)
```

The exact production header is the production binding. The A:H semantics are:

1. A — 更新年份
2. B — 客戶品牌
3. C — 網站
4. D — Sales Category LV1
5. E — 是否有簽保密 NDA
6. F — NDA是否已上傳Salesforce
7. G — 店家狀況（例如：店家對中資事件敏感...）
8. H — 填表人（部門/名字）

G and H map to their existing canonical restricted-customer semantic fields without changing their governance meaning. This range is denylist/governance only. General retrieval and citation are forbidden.

## Frozen Range 3 — Public Metric

```text
range_id = public_metric
exact_tab = 「可公開」對外數據
sheetId = 918878896
hidden = false
grid = 999 rows × 30 columns
header_row = 6
first_data_row = 7
required_columns = A:M
MAX_ROW = 999
exact_A1 = '「可公開」對外數據'!A6:M999
ConfiguredRange = (sheetId=918878896,rowStart=5,rowEnd=999,colStart=0,colEnd=13)
Expected_GridData_key = (sheetId=918878896,startRow=5,startColumn=0)
```

The exact header semantics for A:M are:

1. A — 類型
2. B — 指標
3. C — 論述
4. D — 備註
5. E — 更新時間
6. F — 參考新聞連結
7. G — 新聞稿
8. H — 自媒體
9. I — Saleskits
10. J — 口頭說明
11. K — 演講簡報
12. L — 官網/ 招募網站
13. M — 廣告

The planned MET field is not part of this WP1 read selection.

## Frozen Range 4 — Pending Metric

```text
range_id = pending_metric
exact_tab = 待確認數據
sheetId = 956677822
hidden = true
grid = 999 rows × 26 columns
header = NONE
first_data_row = 3
required_columns = A:D
MAX_ROW = 999
exact_A1 = '待確認數據'!A3:D999
ConfiguredRange = (sheetId=956677822,rowStart=2,rowEnd=999,colStart=0,colEnd=4)
Expected_GridData_key = (sheetId=956677822,startRow=2,startColumn=0)
```

The fixed positional schema is:

1. A — 類型
2. B — 指標
3. C — 論述
4. D — 備註

Rows 1–2 are ignored. Row 3 is the first formal data row and must not be interpreted as a header. Runtime header inference, cell-content schema inference, and caller positional override are forbidden.

This source is pending/internal-review only. Official output, general citation, and external quotation are forbidden.

## Frozen Range 5 — Handle Mapping

```text
range_id = handle_mapping
exact_tab = handle 比對
sheetId = 737692182
hidden = true
grid = 998 rows × 26 columns
header_row = 1
first_data_row = 2
required_columns = A:D
MAX_ROW = 998
exact_A1 = 'handle 比對'!A1:D998
ConfiguredRange = (sheetId=737692182,rowStart=0,rowEnd=998,colStart=0,colEnd=4)
Expected_GridData_key = (sheetId=737692182,startRow=0,startColumn=0)
```

The exact header semantics for A:D are:

1. A — Handle
2. B — Name (with Link)
3. C — Lv1 Sales Category
4. D — Lv2 Sales Category 1st

This range is normalization/brand-candidate evidence only. It is not approved BRD authority and must not be treated as a formal brand master.

## Deferred Permanent-ID and Brand Governance

The following remain planned governance schema and are explicitly deferred:

- Merchant MREC
- Merchant BRD
- Merchant ID Review Status
- Public Metric MET
- `品牌 ID 對照`
- `品牌 ID 初始化審核`

They are `DEFERRED GOVERNANCE SURFACES`, not unnecessary, removed, completed, or implicitly replaced by the five-tab registry.

WP1 must not:

- invent MREC, MET, or BRD values;
- derive them from row number, brand name, or Handle;
- create replacement tabs;
- read empty placeholder columns merely because they were planned;
- treat `handle 比對` as approved BRD authority.

Their absence does not block WP1 read-only transport. Later WP3/migration must emit explicit missing-ID and missing-authority diagnostics. Missing approved permanent IDs or brand authority blocks any claim that canonical production governance is complete.

## Fail-Closed Target, Grid, and Schema Policy

The exact production binding includes:

- canonical Spreadsheet ID;
- exact tab title;
- sheetId;
- hidden state;
- rowCount;
- columnCount;
- configured range bounds;
- header or approved positional semantics.

Any unreviewed mismatch must fail closed. This includes increased or decreased grid bounds, renamed tabs, changed sheet IDs, changed hidden states, changed headers or positional semantics, and changed configured ranges.

No mismatch may trigger automatic expansion, last-row discovery, target/range fallback, alias acceptance, schema inference, or self-healing. A new schema/registry review and human freeze are required before accepting a changed binding.

## Frozen Fields Relationship

The selection registry constrains **where** WP1 may read. The frozen WP0 semantic field set constrains **what response semantics** the mapper must preserve.

`REQUIRED_GOOGLE_RESPONSE_FIELDS` is not an already-approved serialized Google REST `fields` selector. The exact REST fields selector remains subject to a separate WP1 entry review. This freeze does not authorize changing the frozen WP0 semantic field set.

## Authorization State

```text
OWNER_SCHEMA_RECONCILIATION_DECISIONS = APPROVED
READY_TO_FINALIZE_SELECTION_REGISTRY = YES
SELECTION_REGISTRY_OWNER_APPROVAL = APPROVED
SELECTION_REGISTRY_FROZEN = YES
READY_FOR_S1_WP1_IMPLEMENTATION = NO
PRODUCTION_SMOKE_AUTHORIZED = NO
```

`SELECTION_REGISTRY_FROZEN = YES` freezes only the target, tab, grid, range, schema, and deferred-governance selection contract recorded above. It does not authorize transport/runtime implementation, dependency changes, credential construction or use, Google authentication, API calls, production data persistence, CLI, scheduler, Slack, Vault, index, capture, release, or production smoke.

## Repository Disposition

This authorization record does not modify frozen Sprint 0 or Sprint 1 planning documents, WP0 code/tests, prior authorization records, runtime configuration, dependencies, credentials, or production state.

The only intended repository change for this authorization action is this new record:

`docs/reviews/sprint1_wp1_selection_registry_freeze/01_S1_WP1_PRODUCTION_READ_SELECTION_REGISTRY_FREEZE_AUTHORIZATION_2026-08-13.md`

## Remaining Gates

S1-WP1 implementation remains blocked pending separate review and explicit human authorization for at least the exact serialized Google REST fields selector, versioned runtime configuration binding, credential construction, transport/security controls, and implementation scope.

Production smoke remains unauthorized and requires a separate future explicit human authorization.
