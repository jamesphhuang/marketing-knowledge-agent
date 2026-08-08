# Google Sheets → Obsidian／Search Sprint 0 Planning Summary

## Planning baseline

- Frozen Audit commit：`2d9a795e611fa390e7c0d066dd855fbf615887b4`
- Planning branch：`codex/plan/google-sheets-obsidian-sync-sprint0`
- Frozen design：`docs/reviews/google_sheets_obsidian_sync_audit/00`–`11`
- Decisions 1–11：全部 Confirmed；Remaining Decisions：None
- 本目錄只規劃 implementation；本輪不修改 production code、tests 或 frozen Audit，也不執行 tests。

## Assumptions

1. 本輪明列的 Sprint 0 target scope 是 frozen Audit Sprint 0 的受控子集合；BRD／MREC／MET 納入本計畫，Manual Enrichment runtime／whitelist validator 留待後續，不視為重開 Decision 3。
2. 新 canonical contracts 與現行 `DocumentMetadata`／Markdown-derived index 並存；Sprint 0 不以大規模修改 `models.py` 取代 legacy schema。
3. Google reader 只定義 injectable protocol 與 synthetic implementation；production Service Account、Google client dependency及 live API adapter均不建立。
4. URL safety是純字串／literal-IP、無 DNS、無 redirect、無 HTTP 的離線 contract。Redirect revalidation只在 DTO／policy 中可表達，不在 Sprint 0 執行。
5. HTML normalization只接受 synthetic HTML bytes/text；fixture不得含正式公司內容、第三方文章正文或真實 oral-only claim。
6. Release manifest只定義不可變 composition DTO 與 validation contract；不建立 `.mka/releases`、active pointer、journal或 production migration。

## Definition of done

Sprint 0 implementation完成時，應同時滿足：

- Google `CellData` contract可表達 formatted/effective/user-entered value、hyperlink、rich-text run links、data validation及merge ranges。
- synthetic reader可注入且不含Google credential、client或network path。
- formula正文只取effective／formatted value；Public Metric F只依merge metadata繼承，不blind fill-down。
- BRD／MREC／MET與`<MREC>:<asset_type>` identity不依row、path、URL、run position或array index。
- Metric source cells只存在於transient、repr-redacted normalization input；流程固定為`SourceCell → early minimization → PersistenceEligibleMetricInput → PublicMetric`。Oral-only不得先形成含正文的raw normalized PublicMetric。
- oral-only在任何 persistence-ready canonical object前不可逆最小化，且敏感正文不出現在repr、exception、log、preview、snapshot或serialized bytes。
- link extraction、URL safety／canonicalization與Content Asset 0／1／2+ cardinality規則完整離線可測。
- `CapturedContent`、`CapturePolicy`、HTML normalization、content hash、chunk metadata及Release manifest可表達`success`、`stale`、`unavailable`、`blocked`、`metadata_only`、`needs_review`，但不執行HTTP或publish。
- `source_fingerprint`與`capture_content_hash`分離；Official index input contract明確禁止由Markdown reparse產生。
- redacted preview只含允許的IDs、lineage、counts、hashes與stable reason codes。
- 所有新增tests皆使用synthetic／temporary資料，且既有legacy path仍可保留。

## Scope

本計畫拆成17個可獨立review的Work Packages：

| WP | 名稱 | 主要結果 |
| --- | --- | --- |
| WP0 | Offline Test Harness | synthetic fixture與network/persistence guard基座 |
| WP1 | CellData DTO + Reader Protocol | Google-shaped typed input與injectable reader contract |
| WP2 | Snapshot Serialization + Fingerprint | deterministic Google source fingerprint |
| WP3 | Merge-aware Cell Normalization + Lineage | value/provenance輸出與no-fill-down契約 |
| WP4 | Permanent Identity + Canonical Entity Schemas | BRD／MREC／MET與Content Asset key identity/schema；payload construction gate留在WP5 |
| WP5 | Oral-only Early Minimization | `ExcludedSourceRef`安全邊界 |
| WP6 | Embedded Link Extraction | 四層priority候選與provenance |
| WP7 | URL Safety + Canonicalization | 離線public URL gate與canonical URL |
| WP8 | Content Asset Resolution | `<MREC>:<asset_type>`及0／1／2+結果 |
| WP9 | CapturedContent DTO | capture lineage、authority與status contract |
| WP10 | CapturePolicy + Fetch Result Contract | fail-closed domain policy／fetch outcome classification DTO |
| WP11 | Synthetic HTML Normalization | deterministic clean body／sections |
| WP12 | Content Hash + Revision Contract | parser-versioned body hash與LKG revision semantics |
| WP13 | Captured Chunk Metadata + Identity Contract | 對injected synthetic chunk spans建立stable metadata；不定production splitting algorithm |
| WP14 | Complete Release Manifest Contract | metadata＋capture＋sibling output composition DTO |
| WP15 | Redacted Preview Contract | audit-safe validation／diff view model |
| WP16 | Sprint 0 Integration Contract Tests | end-to-end synthetic, zero-persistence contract |

## Explicitly out of scope

- Google credential、Service Account setup、live Sheets API、Spreadsheet write或standalone Apps Script。
- permanent ID production allocation、BRD自動建立／merge、正式registry mutation。
- HTTP、DNS、redirect follow、crawler、scraper、production article capture或真實網站fixture。
- production Obsidian write、SQLite／FTS migration、vector build或active Release activation。
- Slack Search／Slack Ops、scheduler、notification、pagination、cursor、TTL或rendering caps。
- external LLM、API key、production dataset、`data/`、`reports/`、`obsidian_vault/`或`.mka/`。
- 移除legacy `excel-preview → Markdown → content-index` path；只建立未來替換它的compatibility boundary。

## Cross-cutting invariants

1. Google Sheets是Official metadata／identity／governance authority；linked content只提供body。
2. Normalized canonical objects是未來Obsidian、FTS與vector的共同輸入；Markdown不是authority。
3. Formula string只能作non-body provenance，不能成為canonical正文。
4. Merge inheritance必須可證明anchor/range；非merge空白永不繼承。
5. Oral-only資料最小化早於serialization、preview、capture candidate與任何persistence-ready object。
6. 每個MREC／asset type最多一個Content Asset；多distinct canonical URLs只回`needs_review`，不挑winner。
7. URL、row、path、run position、candidate ordinal、capture time及chunk ordinal都不是permanent parent identity。
8. Evidence永不升格為approved metric；Primary／Evidence parent欄位互斥。
9. `source_fingerprint`與`capture_content_hash`分離，但Release manifest可固定兩者的完整composition。
10. Capture revision可表達history／stale，但Sprint 0不建立independent activation或refresh。
11. Sprint 0只定chunk metadata/identity contract；production chunk-size、overlap與section splitting algorithm留待canonical index Sprint。

## Planning conclusion

沒有發現`BLOCKING_SPEC_ISSUE`。建議從低風險、無legacy接線的DTO與test harness開始，先建立governance boundary，再做link resolution、captured-content deterministic transformations及Release contract，最後才跑Sprint 0 integration contract；不要先改`content_index.py`或`obsidian_sync.py`。
