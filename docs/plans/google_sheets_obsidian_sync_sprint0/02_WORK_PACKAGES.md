# Sprint 0 Work Packages

## Shared implementation rules

- 每包只在其contract邊界內變更；不得順手接CLI、Google、HTTP、Vault、DB、vector或Slack。
- Likely files是planning預測，不是授權擴張。開始每包前仍須重讀實際callers/exports/tests。
- 所有Pydantic model須同時考慮目前`pydantic>=1.10,<3`；不得為新contract順手遷移整個`models.py`。
- 新fixture一律虛構；`tests/conftest.py`的historical/runtime fixtures不作Sprint 0 dependency。
- 每包實作後只執行該包列出的安全tests；本planning round不執行任何test。

## WP0 — Offline Test Harness

1. **Goal**：建立Sprint 0專用synthetic fixture layout、builders及禁止network／production persistence的測試guard。
2. **Why**：現有pytest成熟，但`tests/conftest.py`含會讀`data/`、`reports/`、`obsidian_vault/`、`.mka/`的historical fixtures；新contract需可獨立安全執行。
3. **Audit / Decisions**：`05 §11`、`07 Sprint 0`、`10 §18`；支援Decision 1、9、10的offline/non-production限制。
4. **Current tracked code affected**：只新增Sprint 0 tests/fixtures；不改現有fixture loader。
5. **Likely files**：`tests/sprint0_fixtures.py`、`tests/fixtures/google_sheets_sprint0/*.json`、`tests/fixtures/google_sheets_sprint0/*.html`、`tests/test_sprint0_test_harness.py`。
6. **Files explicitly not to modify**：`tests/conftest.py`、`tests/fixtures.py`、`src/`、`pyproject.toml`、所有runtime/sensitive directories。
7. **New modules / DTOs expected**：fixture builders、synthetic sentinel constants、network-call trap、persistence-root assertion helper；僅test support。
8. **Input contract**：純Python literals或tracked synthetic JSON/HTML；沒有真實Spreadsheet ID、URL secret或公司正文。
9. **Output contract**：in-memory objects或`tmp_path`下檔案；不得寫workspace runtime paths。
10. **Invariants**：fixture deterministic；不依賴locale/timezone/current time；oral-only sentinel為虛構字串。
11. **Failure semantics**：任何socket/HTTP attempt、workspace runtime write或fixture含禁用marker時test立即fail。
12. **Tests to add**：fixture load、deterministic bytes、guard會攔截network、輸出只能位於`tmp_path`、敏感marker corpus檢查。
13. **Existing tests to preserve**：全部現有tests；尤其不改`tests/conftest.py`的historical hash契約。
14. **Acceptance criteria**：test harness可被後續WP單獨import；零production data依賴；無global monkeypatch殘留。
15. **Stop conditions**：必須讀runtime directories、需要live website／Google snapshot、或guard會影響既有test collection。
16. **Dependencies**：無。
17. **Blocks**：所有後續WP的synthetic tests。
18. **Risk**：low。

## WP1 — Google CellData DTO + Reader Protocol

1. **Goal**：定義Google-shaped read-only DTO與injectable reader protocol，完整表達CellData、sheet properties及merge ranges。
2. **Why**：現有local XLSX parser只有flattened值，無formatted/effective/user-entered、rich links或validation。
3. **Audit / Decisions**：`02 GS-01–07`、`03 §3.1`、`05 §2/4`；Decision 1只讀架構。
4. **Current tracked code affected**：新增獨立contract；`excel_preview.py`保留不接線。
5. **Likely files**：`src/marketing_knowledge_agent/sheets_contracts.py`、`tests/test_sheets_contracts.py`、WP0 synthetic CellData JSON。
6. **Files explicitly not to modify**：`excel_preview.py`、`excel_ingestion.py`、`cli.py`、`pyproject.toml`、credential/config files。
7. **New modules / DTOs expected**：`GoogleValue`、`TextFormatRunLink`、`DataValidationDTO`、`CellDataDTO`、`GridRangeDTO`、`MergeRangeDTO`、`SheetSnapshotDTO`、`SpreadsheetSnapshotDTO`、`SheetsReader` protocol、`SyntheticSheetsReader`。
8. **Input contract**：reader接Spreadsheet reference、explicit ranges/field selection；synthetic reader接immutable fixture。
9. **Output contract**：完整typed snapshot；不得回傳Google client/raw response object。
10. **Invariants**：user-entered formula與effective/formatted value分欄；hyperlink/run links/data validation/merge metadata不丟失；reader interface無write method。
11. **Failure semantics**：missing required structural field、invalid coordinate、overlapping malformed range或unsupported typed value回stable validation error；不得dump raw payload。
12. **Tests to add**：formula/effective/formatted、boolean+checkbox validation、rich-text links、hidden sheet、unicode、merge DTO、synthetic reader injection及protocol無write surface。
13. **Existing tests to preserve**：`tests/test_excel_preview.py`、`tests/test_asset_metadata_preview.py`全部不改。
14. **Acceptance criteria**：所有cross-cutting CellData欄位可無損表達；完全離線；DTO deterministic serialization-ready。
15. **Stop conditions**：需引入Google SDK、raw response必須持久化、公式與display value無法分離、或DTO需要任意mutation API。
16. **Dependencies**：WP0。
17. **Blocks**：WP2、WP3、WP6、WP16。
18. **Risk**：medium（schema若錯會向下游擴散）。

## WP2 — Snapshot Canonical Serialization + Source Fingerprint

1. **Goal**：把WP1 snapshot轉為固定順序、type-safe canonical bytes並計算`source_fingerprint`。
2. **Why**：現行hash只保護local plans；Google source需要排除response envelope與web freshness。
3. **Audit / Decisions**：`03 §4`、`05 §9`、`06 §9`、`10 §16/17`；Decision 10雙freshness domain。
4. **Current tracked code affected**：新增domain-specific serializer/hash；不匯入one-off private helpers。
5. **Likely files**：`src/marketing_knowledge_agent/canonical_serialization.py`、`tests/test_source_fingerprint.py`。
6. **Files explicitly not to modify**：`obsidian_sync.py`、governance/store one-off plan modules、`content_index.py`。
7. **New modules / DTOs expected**：`canonical_json_bytes`（受控public helper或module-local）、`snapshot_fingerprint_payload`、`compute_source_fingerprint`。
8. **Input contract**：已validate的`SpreadsheetSnapshotDTO`及explicit selected ranges。
9. **Output contract**：`sha256:<hex>`與可供test檢查、不含敏感正文報告的canonical payload boundary。
10. **Invariants**：sheet/grid/cell順序固定；typed values不以locale string混淆；包含links/validation/merges；排除HTTP、capture body/time及response envelope。
11. **Failure semantics**：未知unserializable type、duplicate cell coordinate或非canonical range直接blocking error，不fallback到`str(object)`。
12. **Tests to add**：key/order變化不改hash、cell內容/merge/link/validation變化會改hash、capture body/time不在輸入、golden vector、unicode normalization。
13. **Existing tests to preserve**：`tests/test_obsidian_sync.py` plan hash tests、所有one-off manifest hash tests。
14. **Acceptance criteria**：相同logical snapshot產生相同bytes/hash；與`capture_content_hash`API及type命名分離。
15. **Stop conditions**：hash依Google JSON原順序、capture欄位混入、使用unstable`repr`或需讀網路/runtime state。
16. **Dependencies**：WP1。
17. **Blocks**：WP14、WP16。
18. **Risk**：medium。

## WP3 — Merge-aware Cell Normalization + Source Lineage

1. **Goal**：依field contract解析cell value與merge anchor，輸出normalized value及精確lineage。
2. **Why**：現行local parser把merge值展開且對Public Metric類型/指標blind fill-down，無法證明每個繼承來源。
3. **Audit / Decisions**：`03 §3.2`、`04 §3`、`05 §3/4`；cross-cutting D/E。
4. **Current tracked code affected**：新增normalizer；legacy `_expand_merged_cells`與`_normalize_sheet_records`保持原樣。
5. **Likely files**：`src/marketing_knowledge_agent/cell_normalization.py`、`tests/test_cell_normalization.py`。
6. **Files explicitly not to modify**：`excel_preview.py`、`excel_ingestion.py`、existing XLSX fixture builders。
7. **New modules / DTOs expected**：`FieldContract`、`ResolvedCellValue`、`SourceLineage`、`SourceFieldLineage`、merge ownership lookup、formula provenance marker。
8. **Input contract**：WP1 sheet/cell/merge DTO＋field type、source column及`merge_inheritance_allowed`。
9. **Output contract**：typed normalized value、human display value、source cell、optional merge anchor/range、`source_was_formula`；不含formula string作body。
10. **Invariants**：只從明確merge anchor繼承；非merge空白保持空白；Public Metric F可merge inherit；formula content取effective/formatted；row不是identity。
11. **Failure semantics**：overlap/invalid merge、covered cell自帶衝突值、effective error、checkbox validation/type mismatch回stable blocking/needs-review issue。
12. **Tests to add**：formula effective value、F7:F9 inheritance、非mergeF8不fill、橫向header merge不污染、merge越界/重疊、boolean validation、lineage coordinates。
13. **Existing tests to preserve**：`test_excel_preview_expands_merged_reference_source`與`does_not_expand_horizontal_merged_header`作legacy regression，不改其期望。
14. **Acceptance criteria**：每個非local value都可指出merge anchor；無blind forward-fill；normalized output deterministic。
15. **Stop conditions**：需要以last-seen value補空白、formula string進正文、或lineage無法指出anchor/range。
16. **Dependencies**：WP0、WP1。
17. **Blocks**：WP5、WP8、WP16；WP4可平行。
18. **Risk**：high（Google sheet語義與治理交界）。

## WP4 — Permanent Identity Validators + BRD/MREC/MET Canonical Entity Schemas

1. **Goal**：定義永久ID value objects、Brand／SourceRecord／PublicMetric schema及共用lifecycle/eligibility contract；不從source cells建立payload-bearing PublicMetric。
2. **Why**：`DocumentMetadata`混合多record types且identity依row；直接擴充會影響大量retrieval callers。
3. **Audit / Decisions**：`04 §1–5`、`05 §8`；Decision 4的ID格式/immutability contract、Decision 8 parent identity。
4. **Current tracked code affected**：新增獨立canonical models；不改legacy `DocumentMetadata`。
5. **Likely files**：`src/marketing_knowledge_agent/canonical_models.py`、`tests/test_canonical_models.py`。
6. **Files explicitly not to modify**：`models.py`、`retrieval.py`、`query_planning.py`、`indexing.py`、Apps Script／registry code。
7. **New modules / DTOs expected**：`BrandId`、`SourceRecordId`、`MetricId`、`AssetType`、`ContentAssetKey`或validators；`Brand`、`SourceRecord`、final `PublicMetric` schema、`LifecycleStatus`、`PublishEligibility`、`ReviewStatus`。Source-facing PublicMetric factory不屬本WP。
8. **Input contract**：ID validators與非敏感結構欄位可直接測；payload-bearing PublicMetric只接受WP5產生的`PersistenceEligibleMetricInput`，不得直接接受WP3 transient source cells。WP4不配置ID。
9. **Output contract**：validated identity/entity schemas；Brand／SourceRecord可獨立建構，PublicMetric的source-to-canonical construction留在WP5 gate之後。
10. **Invariants**：regex最低四位不限制最大值；公式ID拒絕；MREC/BRD/MET namespace不可混；row/name/URL變動不改identity；BRD uncertain只能needs_review；`MetricSourceCells`不是canonical DTO。
11. **Failure semantics**：missing/malformed/duplicate/context-conflicting ID輸出blocking issue；blank BRD不自動create/assign；model validation不得猜值。
12. **Tests to add**：valid/invalid MREC/MET/BRD、`<MREC>:<asset_type>` key、>9999、namespace mismatch、MREC/MET uniqueness set validator、row reorder、BRD uncertain needs_review、lifecycle enum、MET formula-ID rejection，以及raw source-cell DTO不能走PublicMetric source factory的negative contract。
13. **Existing tests to preserve**：所有`DocumentMetadata`／typed retrieval tests；asset legacy row-ID tests不修改。
14. **Acceptance criteria**：canonical schemas可獨立import；不影響legacy model parsing；identity digest不含row/path/name/URL；PublicMetric payload construction必須持有WP5的persistence-eligible result。
15. **Stop conditions**：需要修改廣泛retrieval schema、以row產生永久ID、blank BRD自動歸戶、預先新增AST ID，或raw/transient metric fields可直接建立persistence-ready PublicMetric。
16. **Dependencies**：WP0；WP3 lineage只在WP5／WP16 composition時接入，不是本WP implementation dependency。
17. **Blocks**：WP5、WP8、WP14、WP16。
18. **Risk**：medium。

## WP5 — Oral-only Early Minimization

1. **Goal**：固定`Transient Metric Source Cells → early minimization → PersistenceEligibleMetricInput → PublicMetric`邊界；oral-only只轉成不可逆`ExcludedSourceRef`。
2. **Why**：現行`normalize_public_metric_row`保留claim/note/evidence並只在Slack/written-use gate後擋，是最高資料落地風險。
3. **Audit / Decisions**：`03 §3.3`、`05 §6/10`、`11 §7`；Decision 2 oral-only persistence rule。
4. **Current tracked code affected**：新增canonical normalization boundary；legacy Excel path不改，late-stage governance仍保留defense in depth。
5. **Likely files**：`src/marketing_knowledge_agent/google_normalization.py`、`tests/test_oral_only_minimization.py`。
6. **Files explicitly not to modify**：`excel_ingestion.py`、`apply_review_decisions.py`、`content_index.py`、`slack_presentation.py`、historical Slack fixtures。
7. **New modules / DTOs expected**：ephemeral `MetricSourceCells`（repr-redacted、不可serialize）、`PersistenceEligibleMetricInput`、`ExcludedSourceRef`、`ExclusionReason.ORAL_ONLY`、`minimize_public_metric_source`及唯一source-to-`PublicMetric` factory。
8. **Input contract**：WP3 typed cells/lineage＋channel booleans/note policy＋optional MET；`MetricSourceCells`只在短生命週期記憶體存在，不能寫preview/model dump。
9. **Output contract**：互斥union：不含oral-only payload的`PersistenceEligibleMetricInput`或只含safe lineage/MET/reason/non-reversible digest的`ExcludedSourceRef`；只有前者可交給factory建立WP4 `PublicMetric`。
10. **Invariants**：不存在「含oral claim的raw normalized PublicMetric」中間狀態；oral-only path不保留claim、note、reference URL、raw display或可逆片段；不產生capture candidate；repr/exception不回顯input。
11. **Failure semantics**：channel/validation不確定時fail closed為blocking/needs_review，不建立PublicMetric；error只含stable code與safe coordinate。
12. **Tests to add**：oral-only sentinel不出現在model dump、repr、exception、caplog、JSON、preview input、capture candidates；raw source cells無法直接建立PublicMetric；mixed written+verbal非oral-only時保留normalized channels；note明示不留文字優先排除。
13. **Existing tests to preserve**：`tests/test_excel_governance.py`與`tests/test_slack_structured_governance.py`保留legacy evidence，不改成新canonical assertion。
14. **Acceptance criteria**：任何persistence-ready type constructor/factory都無法接收oral-only或未經gate的source payload；byte/sentinel scan為零。
15. **Stop conditions**：敏感字串出現在任何persistable/debug object、排除發生在Markdown/DB之後、或需把真實claim放fixture。
16. **Dependencies**：WP3、WP4。
17. **Blocks**：WP15、WP16。
18. **Risk**：high（資料洩漏邊界）。

## WP6 — Embedded Link Extraction

1. **Goal**：從WP3已分類為Content Asset來源的eligible CellData依既定priority收集URL candidates並保留provenance，不做安全判定或選winner。
2. **Why**：現有XLSX asset reader只支援whole-cell hyperlink，無rich-text/formula/literal fallback統一contract。
3. **Audit / Decisions**：`05 §5`、`10 §2/5`；Decision 8 link priority。
4. **Current tracked code affected**：新增extractor；legacy hyperlink reader保留。
5. **Likely files**：`src/marketing_knowledge_agent/link_resolution.py`、`tests/test_embedded_link_extraction.py`。
6. **Files explicitly not to modify**：`asset_metadata_preview.py`、`asset_metadata.py`、任何HTTP/fetch module。
7. **New modules / DTOs expected**：`EligibleAssetLinkCell`、`LinkSource` enum、`LinkCandidate`、`extract_link_candidates`、安全的`HYPERLINK` first-argument parser。
8. **Input contract**：WP3 field contract明確分類的單一Content Asset link cell與lineage；raw `MetricSourceCells`、oral-only欄位或未經WP5 eligibility的metric evidence link不得輸入。Formula parser只讀user-entered formula作candidate provenance。
9. **Output contract**：按rich text → cell hyperlink → formula → literal順序的Content Asset candidate list；保留run/cell provenance但不作identity。
10. **Invariants**：較低priority不同URL不得丟棄；formula不eval；literal fallback只接受整格單一HTTP(S)形狀；不發HTTP；本WP不建立Public Metric evidence capture candidate。
11. **Failure semantics**：malformed formula/run link產validation issue；不猜URL、不外搜、不從title生成URL。
12. **Tests to add**：四來源、rich-text多run、whole-cell fallback、HYPERLINK quoting/escaping/malformed、literal URL、title-only零candidate、來源順序deterministic，以及raw metric/oral-only cell輸入被拒絕。
13. **Existing tests to preserve**：`tests/test_asset_metadata_preview.py` whole-cell XLSX cases。
14. **Acceptance criteria**：所有來源候選完整保留；extractor pure/offline；候選position只在provenance。
15. **Stop conditions**：用regex eval公式、priority自動pick winner、candidate extractor包含URL safety/HTTP side effect，或未經field eligibility就接受metric/oral-only來源。
16. **Dependencies**：WP0、WP1、WP3。
17. **Blocks**：WP7、WP8、WP16。
18. **Risk**：medium。

## WP7 — URL Safety + Canonicalization

1. **Goal**：建立離線、fail-closed的public HTTP(S) URL validator與canonicalizer。
2. **Why**：legacy helper缺private/reserved IP、userinfo/sensitive query/admin path等完整防線。
3. **Audit / Decisions**：`02 URL-01–06`、`05 §5`、`10 §6/7`；Decision 9安全前置gate。
4. **Current tracked code affected**：新增URL policy module；可複用legacy tracking規則意圖，但不改其public API。
5. **Likely files**：`src/marketing_knowledge_agent/url_safety.py`、`tests/test_url_safety.py`。
6. **Files explicitly not to modify**：`asset_metadata.py`、network libraries、DNS resolver、capture implementation。
7. **New modules / DTOs expected**：`URLPolicy`、`URLValidationResult`、`CanonicalURL`、stable rejection codes、`validate_and_canonicalize_url`。
8. **Input contract**：WP6 raw candidate字串＋versioned local policy；不接受caller headers/cookies。
9. **Output contract**：safe canonical URL或redacted rejection；完整unsafe URL不進message/repr。
10. **Invariants**：只HTTP/HTTPS；scheme/host lower、IDNA、default port/fragment處理；先檢查原始URL再移tracking；literal IP以`ipaddress`分類；不做DNS/redirect。
11. **Failure semantics**：userinfo、localhost/.local、private/link-local/multicast/unspecified/reserved literal IP、admin/internal pattern、sensitive query、shortener/search redirect、control/ambiguous/overlong URL均rejected並回stable code。
12. **Tests to add**：table-driven attack corpus、default port、IDNA、percent encoding、fragment、tracking、sensitive query、IPv4/IPv6各class、credentials、relative/mailto/tel/file、determinism。
13. **Existing tests to preserve**：`test_multiple_urls_with_same_canonical_target...`、`test_search_and_redirect_urls...`。
14. **Acceptance criteria**：相同safe resource canonicalize一致；unsafe result不回顯secret；零network。
15. **Stop conditions**：需要DNS才判safe、移query後把原危險URL變safe、完整token URL進exception、或自動follow redirect。
16. **Dependencies**：WP6（candidate contract）、WP0。
17. **Blocks**：WP8、WP12、WP16。
18. **Risk**：high（安全邊界）。

## WP8 — Content Asset v1 Resolution + Identity

1. **Goal**：將title與經WP7處理的candidates解析為至多一個logical Content Asset resolution；不決定production persistence或publish eligibility。
2. **Why**：現行一row/asset type雖只有一筆，但ID為`<sheet>:r<row>:<asset_type>`且不同URL conflict語義未與Google四來源統一。
3. **Audit / Decisions**：`04 §2.3`、`05 §5`、Decision 8。
4. **Current tracked code affected**：新增resolver/model extension；legacy asset inventory/apply path保持。
5. **Likely files**：`src/marketing_knowledge_agent/canonical_models.py`、`src/marketing_knowledge_agent/link_resolution.py`、`tests/test_content_asset_resolution.py`。
6. **Files explicitly not to modify**：`asset_metadata_preview.py`、`asset_apply_preview.py`、`asset_apply_plan.py`及其row-ID tests。
7. **New modules / DTOs expected**：`ContentAssetCandidate`、`AssetResolution`、`AssetResolutionStatus`；`AssetType`與`ContentAssetKey`共用WP4 identity contract，不在本WP重複定義。
8. **Input contract**：WP4 valid MREC/BRD/asset key、normalized title、WP6 candidates與WP7 validation results、WP3 lineage。
9. **Output contract**：empty/no asset、`incomplete`、single `resolved_candidate`或`needs_review`；永遠最多一個logical asset object/key，不輸出`active`、`publishable`或production persistence decision。
10. **Invariants**：key=`<MREC>:<asset_type>`；0 URL+title→incomplete；1 distinct canonical URL→resolved candidate；2+→needs_review；canonical-equal candidates dedupe；不挑winner、不拆asset、無AST。
11. **Failure semantics**：missing MREC、invalid asset type、BRD unresolved或2+ URL不得標active；issues只含safe canonical/provenance summary。
12. **Tests to add**：title/no URL、missing video URL、1 URL、multi-source same canonical、2 distinct、row reorder/key stable、URL/run/order改變key stable、空cell不建asset。
13. **Existing tests to preserve**：legacy row-ID asset tests全部保留，標示為compatibility path而非new contract。
14. **Acceptance criteria**：Decision 8全部case可pure function驗證；resolver不發HTTP、不配置ID、不決定Official persistence。
15. **Stop conditions**：自動選URL、自動拆成兩asset、新增AST allocator、identity含URL/row/position，或resolver直接輸出production active/publishable狀態。
16. **Dependencies**：WP4、WP6、WP7、WP3。
17. **Blocks**：WP14、WP15、WP16；WP9 primary parent binding只在WP16整合。
18. **Risk**：medium。

## WP9 — CapturedContent DTO + Authority Contract

1. **Goal**：定義CapturedContent canonical DTO、capture statuses、Primary/Evidence parent互斥與lineage欄位。
2. **Why**：tracked source沒有CapturedContent；legacy `Document`以Markdown/path為中心，不適合body authority與revision lineage。
3. **Audit / Decisions**：`10 §3/4/9`、`06 §4/7`；Decision 9、10、11。
4. **Current tracked code affected**：新增獨立captured-content model；不接`DocumentMetadata`或index。
5. **Likely files**：`src/marketing_knowledge_agent/captured_content.py`、`tests/test_captured_content.py`。
6. **Files explicitly not to modify**：`models.py`、`ingestion.py`、`content_index.py`、`indexing.py`、`obsidian_sync.py`。
7. **New modules / DTOs expected**：`CapturedContentId`、`AuthorityRole`、`CaptureStatus`、`CapturedContent`、`SafeHttpMetadata`、`Section`。
8. **Input contract**：stable internal capture ID、WP4 `ContentAssetKey`或stable evidence relationship、safe canonical URL、clean body/sections、timestamps/hash/parser version；WP8 resolution binding留到WP16整合。
9. **Output contract**：validated canonical capture object，可表達`success/stale/unavailable/blocked/metadata_only/needs_review`。
10. **Invariants**：Primary有asset key且無metric/evidence ID；Evidence有MET+evidence relationship且無asset key；URL/time/hash不作logical identity；Evidence不成approved metric。
11. **Failure semantics**：parent none/both、status與body不相容、unsafe HTTP metadata key、missing stable evidence relationship均validation error/needs_review；不得回顯body。
12. **Tests to add**：parent互斥、all statuses、success/stale body rules、blocked/metadata-only不產fake body、safe metadata allowlist、repr不洩漏clean body、Evidence authority固定。
13. **Existing tests to preserve**：`Document`/`Chunk`/retrieval tests不改。
14. **Acceptance criteria**：DTO能支援未來sibling outputs及Decision 11 timestamps；不暗示獨立activation。
15. **Stop conditions**：capture ID由URL/time產生、Evidence無stable relationship仍標Official、或model自動建立HTTP metadata/body。
16. **Dependencies**：WP0、WP4；Primary parent binding在WP16整合時再接WP8。可與WP1、WP10、WP11平行。
17. **Blocks**：WP12、WP13、WP14、WP16。
18. **Risk**：medium。

## WP10 — CapturePolicy + Fetch Result Contract

1. **Goal**：定義versioned fail-closed domain policy、fetch protocol DTO及temporary/permanent failure classification contract，零HTTP；不判定LKG reuse。
2. **Why**：Audit要求未來可表達metadata-only、blocked與stale LKG，但Sprint 0不能實作fetcher。
3. **Audit / Decisions**：`10 §6/7/9`；Decision 9、10、11。
4. **Current tracked code affected**：只新增policy/protocol models與pure classification；無network implementation。
5. **Likely files**：`src/marketing_knowledge_agent/capture_policy.py`、`tests/test_capture_policy.py`。
6. **Files explicitly not to modify**：`pyproject.toml` network deps、CLI、Slack、scheduler、任何credential/config。
7. **New modules / DTOs expected**：`CaptureMode`、`DomainClass`、`CapturePolicy`、opaque `ValidatedCaptureTargetRef` contract、`CaptureRequest`、`FetchResult`、`FetchFailureCategory`、`FetchClient` protocol。LKG DTO/decision不屬本WP。
8. **Input contract**：explicit versioned local policy、domain class與synthetic fetch outcome；不解析raw URL。WP7的safe canonical URL只在WP16透過adapter形成`ValidatedCaptureTargetRef`。
9. **Output contract**：full_text/metadata_only/unsupported/blocked policy decision，或temporary/non-temporary failure classification；不得直接輸出LKG reuse decision。
10. **Invariants**：unknown third-party fail closed；auth/paywall/unsafe/governance不是temporary；failure classifier只分類本次outcome，不讀previous capture/freshness；無capture-only activation概念。
11. **Failure semantics**：policy missing/malformed/unknown mode為blocked/validation error；不得自行設定freshness天數、headers或retry。
12. **Tests to add**：domain matrix、unknown default、auth/paywall/unsafe、timeout/DNS/network/5xx/429 temporary分類、4xx/permanent/blocked分類，以及raw URL不能直接成CaptureRequest的negative contract。
13. **Existing tests to preserve**：無production capture tests；legacy URL tests不改。
14. **Acceptance criteria**：capture modes與fetch failure categories可完全離線判定；protocol沒有concrete HTTP side effect；LKG reuse明確留給WP12。
15. **Stop conditions**：發HTTP/DNS、解析未驗證raw URL、內建任意freshness天數、unknown domain預設full_text、blocked failure可偽裝temporary，或本WP開始決定LKG reuse。
16. **Dependencies**：WP0；與WP1/WP7/WP9/WP11可平行，safe URL binding留到WP16。
17. **Blocks**：WP12、WP14、WP16。
18. **Risk**：high（policy語義影響未來安全）。

## WP11 — Deterministic Synthetic HTML Normalization

1. **Goal**：把synthetic HTML正規化為deterministic clean body與section structure，移除boilerplate及active content。
2. **Why**：CapturedContent需要可搜尋正文與穩定hash；tracked source沒有HTML normalizer。
3. **Audit / Decisions**：`10 §8/18`；Decision 9 full-body contract。
4. **Current tracked code affected**：新增pure parser/normalizer；不抓網頁、不寫Markdown。
5. **Likely files**：`src/marketing_knowledge_agent/html_normalization.py`、`tests/test_html_normalization.py`、`tests/fixtures/google_sheets_sprint0/*.html`。
6. **Files explicitly not to modify**：`ingestion.py`、`frontmatter.py`、`content_index.py`、`pyproject.toml`（除非implementation另提dependency review）。
7. **New modules / DTOs expected**：`HtmlNormalizationResult`、`NormalizedSection`、`normalize_html`、parser version constant。
8. **Input contract**：synthetic UTF-8 HTML text＋explicit parser version；不接受URL、network response object或script execution。
9. **Output contract**：title、clean_body、stable sections、safe diagnostic codes；不輸出raw HTML。
10. **Invariants**：保留headings/paragraphs/meaningful lists/reliable tables/anchor text順序；移除nav/footer/cookie/ads/script/style/social/hidden/duplicate boilerplate；Unicode/whitespace/newline固定。
11. **Failure semantics**：invalid encoding/oversized synthetic input/無meaningful body回unavailable/needs_review-style result；exception不含raw body。
12. **Tests to add**：boilerplate removal、section/order preservation、lists/tables、script/style/hidden、duplicate header/footer、semantic whitespace equivalence、malformed-but-safe HTML、empty body。
13. **Existing tests to preserve**：Markdown `ingestion`/`frontmatter` tests；不得改成HTML parser依賴。
14. **Acceptance criteria**：同semantic HTML+parser version產相同body/sections；輸出無script/nav/footer/cookie sentinel。
15. **Stop conditions**：需live website/browser、raw HTML持久化、任意執行script、或為了parser重寫Markdown ingestion。
16. **Dependencies**：WP0。HTML→clean body/sections是獨立pure transformation；與CellData、CapturedContent及CapturePolicy contracts可平行。
17. **Blocks**：WP12、WP16。
18. **Risk**：medium。

## WP12 — Deterministic Content Hash + Revision/LKG Semantics

1. **Goal**：計算parser-versioned `capture_content_hash`，並以pure functions判定same-body、新revision或stale LKG composition。
2. **Why**：generic file hashes存在，但沒有capture body與source fingerprint分離的domain contract。
3. **Audit / Decisions**：`10 §9/16/17`；Decision 10、11。
4. **Current tracked code affected**：新增capture-specific hashing/revision functions；不建立store/pointer。
5. **Likely files**：`src/marketing_knowledge_agent/captured_content.py`或`content_hashing.py`、`tests/test_content_hashing.py`。
6. **Files explicitly not to modify**：one-off manifest helpers、`obsidian_sync.py`、DB schema、scheduler。
7. **New modules / DTOs expected**：`compute_capture_content_hash`、`CaptureRevisionRef`、`RevisionDecision`、`LkgEligibilityInput/Result`、`evaluate_lkg_reuse`、`compose_stale_lkg`。
8. **Input contract**：WP11 clean body/sections＋parser version；LKG path另接WP7 same-canonical-URL comparison、WP9 previous success、WP10 failure classification、explicit approved freshness policy與current attempt time。
9. **Output contract**：stable hash、same/new revision decision，或保留previous hash/body timestamps且status=stale的candidate reference。
10. **Invariants**：body/parser version決定hash；captured_at/HTTP metadata不參與；stale不建立假revision、不改原captured_at/last_successful；只更新last_attempt。
11. **Failure semantics**：missing parser version/body、policy不允許或URL changed不得compose LKG；回stable reason，無body dump。
12. **Tests to add**：same body same hash、body/parser change、capture time無影響、source fingerprint隔離、timeout/DNS/network/5xx/429 eligibility、stale timestamp/hash preservation、URL changed/never success/blocked/auth/paywall/governance/freshness missing拒絕。
13. **Existing tests to preserve**：所有generic plan/file hash golden tests完全不改。
14. **Acceptance criteria**：hash namespace/type清楚，不可能把source fingerprint誤作content hash；revision decision deterministic。
15. **Stop conditions**：hash含timestamp/URL、stale製造新body revision、或LKG在policy未核准時可通過。
16. **Dependencies**：WP7、WP9、WP10、WP11。
17. **Blocks**：WP13、WP14、WP16。
18. **Risk**：medium。

## WP13 — Captured Chunk Metadata + Identity Contract

1. **Goal**：對injected synthetic chunk spans建立deterministic chunk identity與完整authority/freshness metadata；不設計production splitting algorithm。
2. **Why**：Sprint 0 target是chunk metadata contract。Legacy chunker輸入Markdown-derived Document且ID依document path+ordinal，但重做chunk-size/overlap/section splitting屬後續canonical index工作。
3. **Audit / Decisions**：`06 §7/12`、`10 §11/12`；Decision 9全文RAG、Decision 11 stale metadata。
4. **Current tracked code affected**：新增CapturedContent chunk metadata/identity builder；legacy `chunking.py`完全不改。
5. **Likely files**：`src/marketing_knowledge_agent/captured_chunks.py`、`tests/test_captured_chunks.py`。
6. **Files explicitly not to modify**：`indexing.py`、`retrieval.py`、`content_index.py`、SQLite schema、vector builder。
7. **New modules / DTOs expected**：`SyntheticChunkSpan`／`ChunkSpan` contract、`CapturedChunk`、`CapturedChunkMetadata`、stable section anchor、`build_captured_chunk`/identity function。Production splitter不屬本WP。
8. **Input contract**：WP9 CapturedContent＋WP12 revision hash＋injected deterministic synthetic span（text/start/end/section anchor/ordinal）；只允許success或符合LKG的stale full body。
9. **Output contract**：chunk DTO，含capture/content/asset或metric/BRD/MREC/authority/title/section/source URL/status/timestamps/hash/batch lineage；本WP不決定如何切出spans。
10. **Invariants**：chunk ID由capture ID+revision/parser/section anchor+chunk text digest；ordinal只作order；Evidence/Primary不混；未來FTS/vector消費同一metadata contract；不從Markdown/fixed summary產生，不定chunk-size/overlap heuristics。
11. **Failure semantics**：blocked/metadata-only/needs-review/empty body不產chunks；parent attribution不完整直接validation error。
12. **Tests to add**：given identical synthetic span時stable IDs、section/text change、section reorder不跨parent、Primary/Evidence metadata、stale flags/timestamps、blocked/metadata-only不能建立chunk DTO、invalid/out-of-range span拒絕。
13. **Existing tests to preserve**：`tests/test_chunking.py`與typed retrieval legacy tests。
14. **Acceptance criteria**：每chunk可追到content hash、canonical parent及lineage；相同CapturedContent+synthetic span輸出bytes/identity穩定；production splitting algorithm明確MOVE_LATER。
15. **Stop conditions**：chunk identity只用ordinal/random、跨文章attribution、fixed summary取代全文、直接寫index，或開始定義production chunk-size/overlap/section splitting heuristics。
16. **Dependencies**：WP9、WP12；WP11只透過WP12 normalized body/revision contract間接關聯。
17. **Blocks**：WP14、WP16。
18. **Risk**：medium。

## WP14 — Complete Release Manifest DTO / Contract

1. **Goal**：定義完整Release composition manifest與cross-field validator，但不建立candidate artifacts或activation。
2. **Why**：現有Vault batch/one-off manifests不固定metadata snapshot、CapturedContent revisions與全部sibling outputs。
3. **Audit / Decisions**：`03 §3.5/6`、`06 §10`、`10 §17`；Decision 10、11。
4. **Current tracked code affected**：新增generic release contract；不替換任何executor。
5. **Likely files**：`src/marketing_knowledge_agent/release_contracts.py`、`tests/test_release_contracts.py`。
6. **Files explicitly not to modify**：`obsidian_sync.py`、`store_data_sync_plan_v2*`、`content_index.py`、`.mka/`、migration files。
7. **New modules / DTOs expected**：`ReleaseManifest`、`CapturedRevisionManifestEntry`、`ArtifactRef`、`ReleasePublishState`、`CanonicalReleaseInputs`、composition validator。
8. **Input contract**：`release_id`、`metadata_sync_batch_id`、schema version、source fingerprint、source row/entity counts、capture policy/parser versions、WP9/12 release-pinned captured revision IDs/content hashes與stale status/timestamps、WP13 chunk-set hashes、future sibling artifact refs/checksums、validator versions、excluded counts、previous release、created time及publish state。
9. **Output contract**：deterministic manifest serialization/hash，逐項固定metadata/capture/sibling composition並能表達stale entries與previous release；不包含body、raw HTML、credential或secret。
10. **Invariants**：單一release固定一個metadata batch+capture set+Obsidian/DB/vector refs；source/capture hashes分欄；no partial publish；revision無independent active pointer。
11. **Failure semantics**：missing sibling、mixed batch/parser/policy、duplicate capture parent、stale timestamp/hash不一致、body/secret field出現時blocking validation。
12. **Tests to add**：minimum valid composition、all required fields、stale entry、mixed release/batch rejection、artifact checksum order determinism、no-body schema、no Markdown reparse input type。
13. **Existing tests to preserve**：Obsidian manifest與one-off store manifest golden/rollback tests；不共用private schema。
14. **Acceptance criteria**：DTO不阻止未來all statuses/LKG；明確沒有activate/write methods；manifest hash deterministic。
15. **Stop conditions**：允許component pointer各自切換、manifest含oral/raw HTML、或需要正式SQLite/Vault artifact才能測。
16. **Dependencies**：WP2、WP4、WP8、WP9、WP10、WP12、WP13。
17. **Blocks**：WP16。
18. **Risk**：medium-high（跨領域composition）。

## WP15 — Redacted Validation / Diff Preview Contract

1. **Goal**：建立只含安全lineage、IDs、counts、hashes、status與stable reason codes的JSON/Markdown preview view model。
2. **Why**：legacy preview/report可能包含claim/notes/URL；new oral-only boundary需要可追查但不可洩漏。
3. **Audit / Decisions**：`03 §5`、`05 §10`、`07 Sprint 0`；Decision 2/5的資料最小化原則（不實作Slack）。
4. **Current tracked code affected**：新增preview builder/renderer；不接現有CLI或寫正式reports。
5. **Likely files**：`src/marketing_knowledge_agent/sync_preview.py`、`tests/test_sync_preview.py`。
6. **Files explicitly not to modify**：`review_template.py`、`apply_review_decisions.py`、legacy report modules、CLI、`reports/`。
7. **New modules / DTOs expected**：`ValidationIssue`、`PreviewItem`、`PreviewSummary`、severity/status/reason enums、safe JSON/Markdown renderer。
8. **Input contract**：WP5 exclusions、WP8 asset resolutions、source fingerprint/hash references及其他already-redacted issues；renderer不接受raw CellData/body。Release manifest validation summary在WP16才接入。
9. **Output contract**：create/update/archive/restore/incomplete/excluded/needs_review/unchanged counts/items，source fingerprint/schema/policy/normalized hashes；oral-only只顯示lineage/MET/reason。
10. **Invariants**：deterministic sort/key order；無claim/note/raw URL/raw HTML/credential；unknown issue payload fail closed；preview不修改source。
11. **Failure semantics**：傳入unsafe field/value時拒絕render並回generic stable code，不sanitize後繼續猜；Markdown與JSON同一view model。
12. **Tests to add**：oral sentinel byte scan、unsafe URL/secret/body field rejection、deterministic JSON/Markdown、0/1/2+asset statuses、counts conservation、stable errors。
13. **Existing tests to preserve**：Excel/asset/apply preview outputs與tests保持legacy behavior。
14. **Acceptance criteria**：preview足以定位sheet/row/field/ID且不含敏感正文；無Vault/index mutation。
15. **Stop conditions**：需要raw payload才能render、oral claim出現在任何format、或preview直接成production apply input未經未來validation。
16. **Dependencies**：WP5、WP8；可與WP14平行，兩者只在WP16匯合。
17. **Blocks**：WP16。
18. **Risk**：high（report資料外洩）。

## WP16 — Sprint 0 Integration Contract Tests

1. **Goal**：用單一完全synthetic scenario串接reader→fingerprint→merge normalization→early minimization→link/URL/asset→capture/HTML/hash/chunk→manifest/preview contract。
2. **Why**：單元contract可能各自正確但composition仍違反oral-only、identity或Markdown authority邊界。
3. **Audit / Decisions**：`07 Sprint 0`、`10 §18`、`11 §12`；Decision 1、8、9、10、11。
4. **Current tracked code affected**：只新增integration tests；不得加production orchestrator/CLI。
5. **Likely files**：`tests/test_sprint0_contract_integration.py`及WP0 fixtures。
6. **Files explicitly not to modify**：`src/.../pipeline.py`、CLI、Vault/index executors、Slack、runtime directories。
7. **New modules / DTOs expected**：無新production DTO；可新增test-only composition helper。
8. **Input contract**：虛構Spreadsheet snapshot，含formula、merge、oral-only metric、rich/whole/formula/literal links、same/different URLs及synthetic HTML。
9. **Output contract**：in-memory canonical batch、capture/chunks、release manifest與redacted preview；全部可序列化到`tmp_path`作assertion。
10. **Invariants**：oral sentinel零輸出；row reorder identity/hash語義符合contract；Markdown不是任何index/capture input；source/capture hashes分離；零network/production writes。
11. **Failure semantics**：任一needs_review/blocking使publishable composition invalid；不以skip flag繞過、不產partial manifest。
12. **Tests to add**：happy path、oral-only exclusion、same canonical dedupe、2+ URL whole-batch needs_review、title/no URL incomplete、formula+merge、deterministic replay、row reorder identity、blocked URL、stale DTO/manifest representation、no Markdown reparse API。
13. **Existing tests to preserve**：全部legacy regression；final safe suite另見`04_TEST_STRATEGY.md`。
14. **Acceptance criteria**：同fixture兩次結果除明確logical clock外完全相同；所有cross-cutting invariants有至少一個negative assertion。
15. **Stop conditions**：需要live integration、production file/database、legacy Markdown reparse、外部LLM，或任何partial publish語義。
16. **Dependencies**：WP0–WP15全部完成。
17. **Blocks**：Sprint 0 readiness exit與後續Sprint 1 implementation planning。
18. **Risk**：medium。

## Work package sizing note

WP1/2/3、WP6/7/8及WP9/10/11/12/13刻意拆開，使schema、governance、安全與deterministic transformation可以各自review及停下。它們可共用少量module，但不得以「同檔案」為理由合併成大型implementation PR。若單一WP實作diff擴及兩個以上legacy runtime modules，應視為scope drift並停止重新拆包。
