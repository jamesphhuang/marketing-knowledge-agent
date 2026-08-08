# Repository Implementation Gap Map

## Evidence boundary

本盤點只使用frozen Audit及tracked repository。未讀取`.env`、`data/`、`reports/`、`obsidian_vault/`、`.mka/`或Audit外untracked review資料。

狀態只使用`implemented`、`partial`、`missing`、`conflicting`、`unknown`。

| Requirement / Contract | Audit source | Current code location | Current status | Reusable? | Conflict? | Sprint 0 action | Future action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Google reader protocol／synthetic injection | `03 §3.1`, `05 §2`, `07 Sprint 0` | 無Google reader；`excel_preview.py:read_xlsx_workbook`只讀本機XML | missing | `.xlsx` fixtures的builder概念可參考 | 否 | 新增read-only protocol、DTO及synthetic reader，不接CLI | Sprint 1另加Service Account adapter |
| CellData formatted/effective/user-entered values | `02 GS-03/04`, `05 §4` | `excel_preview.py:_cell_value`只回單一cached/inline值 | missing | typed normalization helpers可參考 | 是；formula provenance遺失 | 新增獨立CellData DTO，不改local parser | 後續以adapter把Google response轉DTO |
| hyperlink／textFormatRuns／dataValidation | `02 GS-05/06`, `05 §2/5` | `asset_metadata_preview.py`只讀whole-cell XLSX hyperlink；checkbox只見cached boolean | partial | whole-cell URL tests可沿用意圖 | 是；缺rich text與validation | DTO與embedded-link extractor補齊 | live adapter驗fields mask與實際response |
| merge-aware inheritance | `05 §4`, `11 §7` | `excel_preview.py:_expand_merged_cells`支援受限垂直merge；`_normalize_sheet_records`另對類型／指標blind fill-down | conflicting | merge range parser及既有merge regression可參考 | 是；target只允許merge metadata | 新增anchor/range aware normalizer；legacy維持 | 後續canonical importer取代fill-down path |
| source lineage | `04 §3`, `05 §4` | `models.py`只有`source_sheet/source_row` | partial | source coordinates可沿用 | 否 | 新增field/cell/range/fingerprint/batch lineage DTO | renderer/index保留lineage |
| deterministic source fingerprint | `03 §4`, `07 Sprint 0` | `obsidian_sync.py:_plan_state_hash`只hash local plan；多個one-off module有canonical JSON helper | missing | canonical JSON／SHA-256模式可adapter包裝 | 否 | 新增domain-specific canonical serialization；不共用private one-off helper | Sprint 5做F1/F2與activation gate |
| BRD／MREC／MET validators | `04 §1–2`, `05 §8` | `models.py`無永久ID欄位；legacy IDs由sheet/row產生 | conflicting | Pydantic validation style可沿用 | 是；row identity | 新增獨立canonical models與ID value validators | Sprint 2接registry／writer結果 |
| BRD uncertain mapping | `04 §2.1/6`, `05 §8` | `search_aliases.py`與handle mapping是retrieval/normalization evidence，不是BRD authority | missing | `governance.normalize_identity`可作候選evidence參考 | 否 | DTO只允許approved／needs_review，禁止自動BRD | Sprint 1/2實作human review與writer |
| row reorder不改identity | `04 §1/5`, Decision 8 | `asset_metadata_preview.py`建立`<sheet>:r<row>:<asset_type>` | conflicting | asset type registry可沿用 | 是 | canonical identity只取MREC／MET／BRD；row只進lineage | migration保留legacy alias mapping |
| oral-only early minimization | `05 §6`, `11 §7` | `excel_ingestion.py:normalize_public_metric_row`保留claim/note且標verbal；`governance.py`在written output才擋 | conflicting | restricted-note markers可adapter包裝 | 是；敏感payload可先落地 | 新增pre-persistence minimizer與redacted exclusion DTO | 後續切canonical importer；再退役late-only defense |
| embedded link extraction priority | `05 §5`, `10 §2/5` | `asset_metadata_preview.py:read_workbook_asset_hyperlinks`只支援whole-cell XLSX link | partial | ASSET_FIELDS與whole-cell case可沿用 | 否 | 新增四來源candidate extractor及provenance | Sprint 1接Google CellData |
| URL syntax/canonicalization | `05 §5`, `10 §6/7` | `asset_metadata.py`限制HTTP(S)、去tracking、擋shortener/search redirect | partial | tracking canonicalization與multi-candidate tests可adapter包裝 | 是；無public-host/sensitive-query完整gate | 新增獨立URL safety module；保留legacy helper | capture階段另做redirect/DNS revalidation |
| Content Asset v1 identity/cardinality | `04 §2.3`, Decision 8 | 每row/asset type一筆，但`asset_id`仍row-derived | conflicting | 一cell一asset iteration可參考 | 是 | 新resolver只輸出`empty`／`incomplete`／`resolved_candidate`／`needs_review`及`<MREC>:<asset_type>`；`resolved_candidate`只表示link/content-asset resolution成功，不代表persistence eligibility、searchable、release inclusion或production activation；WP8無`active` state且不決定production persistence | Sprint 3 renderer消費canonical asset |
| CapturedContent runtime DTO | `10 §4/9` | 無model/runtime | missing | `Document`不適合作canonical capture parent | 否 | 新增獨立DTO與parent互斥validation | 後續production fetch與renderer接線 |
| CapturePolicy／failure contract | `10 §6/7/9` | 無domain capture policy或fetch result DTO | missing | 無 | 否 | 定義versioned fail-closed modes/status；零HTTP | production policy owner與allowlist另案 |
| deterministic HTML normalization | `10 §8/18` | 無HTML parser/normalizer | missing | 無 | 否 | synthetic-only normalizer及fixtures | production parser/domain parity review |
| content hash／revision semantics | `10 §9/16/17` | generic file/JSON hashes存在；無parser-versioned body hash | missing | SHA-256/canonical JSON模式可參考 | 否 | 新增capture hash與revision/LKG pure functions | Sprint 5接完整Release/LKG store |
| captured full-text chunk metadata | `10 §11` | `chunking.py`以Document path＋ordinal產生chunk；`indexing.py`支援FTS/local embedding | partial | paragraph splitting與offline embedding可adapter參考 | 是；identity/metadata不足且輸入來自Markdown | 新增CapturedContent-specific chunk contract；不改legacy chunker | Sprint 4建立canonical index adapter |
| Complete Release manifest DTO | `03 §3.5/6`, `06 §10` | `obsidian_sync.py`有Vault batch manifest；one-off stores有hash/manifest，無generic full composition | partial | manifest/hash/plan binding概念可沿用 | 是；沒有metadata＋capture＋siblings單一identity | 新增DTO/validator，禁止實際activation | Sprint 5實作journal/active pointer/recovery |
| redacted validation／diff preview | `03 §5`, `05 §10` | Excel/asset preview輸出含多種正文；部分report已有stable reason概念 | partial | preview/apply分離、counts、reason codes可沿用 | 是；oral-only正文可能進preview | 新增safe view model與serializer | Sprint 1/2接真實dry-run |
| Official sibling-output input | `03 §1`, `06 §1/7` | `content_index.py`從managed Markdown reparse；`obsidian_sync.py`先寫Markdown | conflicting | legacy regression suite與parity用途可保留 | 是 | Sprint 0只定義`CanonicalReleaseInputs`／manifest欄位與negative contract | Sprint 3/4新增sibling renderer/index builder |
| Offline test architecture | `07 原則`, `10 §18` | pytest＋`tmp_path`成熟；`tests/conftest.py`也含會讀runtime dirs的historical fixtures | partial | local synthetic builders可沿用 | 否 | 新增隔離fixture目錄與network/persistence guard，不依賴historical fixtures | CI分出safe Sprint 0 test group |
| CLI integration | Explicit Sprint 0 exclusions | `cli.py`已有多個legacy commands | implemented | 不需要 | 否 | 不修改CLI、不新增command；現況只代表legacy CLI存在 | 後續dry-run獲明確授權才接CLI |
| Production packaging/dependencies | `01 §10`, Sprint 0 exclusions | `pyproject.toml`只有Pydantic／slack-bolt | partial | 現有Python/Pydantic baseline可沿用 | 否 | 優先stdlib＋現有dependency；若實作需新增parser dependency須單獨review | production Google client只在Sprint 1評估 |

## Module disposition

### A. 可以直接沿用

- `pyproject.toml`的pytest設定、Python版本與package discovery。
- `tests`中`tmp_path`、table-driven assertions及synthetic XLSX builder的測試風格。
- `governance.py`的identity normalization、restricted denylist與written-use defense-in-depth概念；不得把它當oral-only primary gate。
- 既有Pydantic schema與literal enum的驗證風格。

### B. 可以adapter包裝

- `asset_metadata.py`的tracking parameter removal、多candidate不選winner語義。
- `excel_preview.py`的header/merge測試案例與source coordinate概念。
- `chunking.py`的段落分割策略及`indexing.py`的離線FTS／deterministic embedding能力；只能由新canonical chunk adapter呼叫，不沿用path-based identity。
- `obsidian_sync.py`與one-off governance/store modules的canonical JSON、checksum、plan binding、backup/rollback概念；不得直接匯入其private helpers形成耦合。

### C. Sprint 0 必須新增或修改

- 新增獨立Google snapshot、canonical model、normalization、link/URL、captured content、HTML、hash/chunk、release及preview contracts。
- 新增完全synthetic／offline tests與fixtures。
- 原則上不修改`models.py`、`excel_preview.py`、`excel_ingestion.py`或CLI；若implementation發現跨模組共用enum確實必要，須以最小export修改並先確認所有callers/tests。

### D. 應暫時保留legacy behavior

- `excel-preview`本機XLSX flow與row-derived preview IDs。
- review/apply、Obsidian sync、Markdown-derived `build-content-index`及現行Slack retrieval。
- 既有late-stage written-use／Slack governance filters作defense in depth。
- 現行tests對legacy output、row identity、Markdown index與historical formal DB的斷言。

### E. 未來Sprint才移除

- row-derived ID作正式identity。
- blind fill-down與local XLSX schema作Google canonical adapter替代品。
- Official index從Markdown reparse。
- oral-only先落地再由Slack擋的legacy路徑與其historical fixture依賴。
- direct target DB rebuild與分散的Vault／index release lifecycle。

## Blocking spec review

未發現`BLOCKING_SPEC_ISSUE`。Audit較廣的Sprint 0提及ENR，而本輪明列scope只要求BRD／MREC／MET；本計畫將ENR runtime留待後續，並保留Decision 3為frozen constraint，不重新裁決。
