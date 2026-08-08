# Commands and Evidence

## 1. Git baseline與盤點命令

實際執行的核心唯讀命令：

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git rev-parse main
git merge-base HEAD main
git remote -v
git status --short --untracked-files=all
git ls-files
git ls-files 'tests/test_*.py'
git ls-files 'src/marketing_knowledge_agent/*.py'
git ls-files 'docs/**/*.md'
git diff --name-only
git diff --cached --name-only
```

Baseline結果（原始Audit盤點時點）：

- root: `/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent`
- branch: `codex/audit/google-sheets-obsidian-sync`
- HEAD/local main/merge-base: `11c99c86ccbbab06f2bf583f8918560d0ce4e985`
- remote repository: `origin` → `jamesphhuang/marketing-knowledge-agent`
- tracked inventory: 171 files；67 source、59 tests、32 docs、8 `.claude`、5 root。

原始Audit驗證另執行 `find docs/reviews/google_sheets_obsidian_sync_audit -maxdepth 1 -type f -print`、`wc -l`、`rg`一致性搜尋及再次執行Git status/diff命令。外接磁碟曾自動為當時10份文件建立`._*.md` AppleDouble sidecars；確認精確路徑後已移除該10個伴生metadata檔，沒有操作任何原有未追蹤項目。Final Consistency Review當下baseline與驗證另見`11_FINAL_CONSISTENCY_REVIEW.md`。

## 2. 讀取與搜尋方式

使用 `sed -n` 讀取指定檔案、`rg`/`git grep` 搜尋symbols與policy字串、`git ls-files`限定主要盤點範圍。代表性搜尋：

```bash
rg -n 'sync_batch_id|record_id|asset_id|source_row|source_sheet' src tests docs
rg -n 'verbal_briefing|oral|can_quote_externally|allowed_exposure_channels' src tests docs
rg -n 'SQLiteIndex|chunks_fts|embedding|build_content_index' src tests docs
rg -n 'archive|rollback|atomic|fingerprint|last_success|mass' src tests docs
rg -n 'Slack|include:enrichment|enrichment|Official' src tests docs
git grep -n 'spreadsheets.get\|includeGridData\|formattedValue\|effectiveValue\|textFormatRuns'
```

一次較廣的文字搜尋意外顯示既有未追蹤Fable review檔案的少量match行；這些輸出立即棄用，未作任何結論依據，也未讀取該組文件。後續以`git grep`或明確tracked paths限制範圍。

## 3. 實際深入讀取的tracked files

### Root與規範

- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `setup.py`
- `.gitignore`

### 正式文件

- `docs/governance/GOVERNANCE_RULES.md`
- `docs/governance/ROADMAP.md`
- `docs/governance/LESSONS.md`
- `docs/governance/L_RETRIEVAL_CITATION_ACCURACY_REVIEW.md`
- `docs/governance/RETRIEVAL_FAIL_CLOSED_ACCEPTANCE.md`
- `docs/governance/RETRIEVAL_QUALITY_ROOT_CAUSE.md`
- `docs/specs/N_OBSIDIAN_SYNC_SPEC.md`
- `docs/specs/O_CONTENT_INDEX_SPEC.md`
- `docs/specs/P_QUERY_GATING_SPEC.md`
- `docs/specs/Q_LLM_INTEGRATION_SPEC.md`
- `docs/specs/S_SLACK_INTERFACE_SPEC.md`
- `docs/specs/T_RETRIEVAL_QUALITY_TYPED_QUERY_SPEC.md`
- `docs/specs/U_ASSET_METADATA_INVENTORY_PREVIEW_SPEC.md`
- `docs/specs/V_ASSET_REVIEW_DECISION_VALIDATION_SPEC.md`
- `docs/specs/W_ASSET_APPLY_PREVIEW_SPEC.md`
- `docs/specs/X_SLACK_OUTPUT_RENDERER_PREVIEW_SPEC.md`
- `docs/specs/Y_ASSET_METADATA_APPLY_PLAN_SPEC.md`

其餘tracked docs透過inventory與targeted symbol/search盤點是否存在相關entry、重複規格或衝突敘述；未將文件聲稱當作implementation evidence。

### Source modules

- CLI/model/core: `cli.py`, `models.py`, `frontmatter.py`, `pipeline.py`, `ingestion.py`, `chunking.py`, `validation.py`
- Import/review: `excel_ingestion.py`, `excel_preview.py`, `review_template.py`, `review_decision_validation.py`, `apply_review_decisions.py`
- Asset: `asset_metadata.py`, `asset_metadata_preview.py`, `asset_review_validation.py`, `asset_apply_preview.py`, `asset_apply_plan.py`
- Governance/identity: `governance.py`, `missing_parent_diagnostic.py`, `missing_parent_resolution_preview.py`, `resolution_storage_schema_preview.py`, `parent_sync_plan.py`, `parent_authority_review.py`, `parent_authority_import_bundle.py`
- Store/alias: governance decision store plan/confirmation/execution/schema-v2 modules、`store_data_sync_plan_v2.py`, `store_data_sync_plan_v2_execution.py`, `store_data_sync_existing_validation.py`, production search alias plan/confirmation/execution modules、`search_aliases.py`
- Vault/index/search: `obsidian_sync.py`, `content_index.py`, `indexing.py`, `embeddings.py`, `retrieval.py`, `query_planning.py`, `reranking.py`, `generation.py`, `structured_results.py`, `query_gating.py`
- Slack/LLM: `slack_interface.py`, `slack_presentation.py`, `slack_output_preview.py`, `llm.py`, `llm_generation.py`

### Tests

逐檔執行的28個安全測試模組列於下一節兩個pytest命令。另以tracked inventory與targeted search檢查其他tests的fixtures、入口及production-bound assertions；未把未執行test名稱視為通過。

## 4. 測試命令與結果

### Batch 1

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider \
  tests/test_apply_review_decisions.py \
  tests/test_asset_apply_plan.py \
  tests/test_asset_apply_preview.py \
  tests/test_asset_metadata_preview.py \
  tests/test_asset_review_validation.py \
  tests/test_excel_governance.py \
  tests/test_excel_preview.py \
  tests/test_governance_evals.py \
  tests/test_missing_parent_diagnostic.py \
  tests/test_missing_parent_resolution_apply_preview.py \
  tests/test_missing_parent_resolution_preview.py \
  tests/test_obsidian_sync.py \
  tests/test_parent_authority_import_bundle.py \
  tests/test_parent_authority_review.py \
  tests/test_parent_sync_confirmation.py \
  tests/test_parent_sync_plan.py \
  tests/test_production_search_alias_runtime.py \
  tests/test_review_decision_validation.py \
  tests/test_review_template.py \
  tests/test_slack_exact_alias_query.py \
  tests/test_slack_interface.py \
  tests/test_slack_output_preview.py \
  tests/test_slack_search_presentation_v1.py \
  tests/test_slack_structured_governance.py \
  tests/test_typed_query_retrieval.py
```

結果：`396 passed, 6 warnings in 21.74s`。

### Batch 2

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider \
  tests/test_backfill.py \
  tests/test_content_index.py \
  tests/test_query_gating.py
```

結果：`35 passed, 6 warnings in 0.37s`。

兩批互不重疊，合計`431 passed`, `0 failed`。每批的6個warnings是同一組`models.py` Pydantic V1-style `@validator` deprecation，並非12個不同問題。停用bytecode與pytest cache避免在repo建立cache檔。

## 5. 未執行項目

- 未執行完整 `.venv/bin/pytest`：`tests/conftest.py` 的historical/production-bound fixtures會讀取或複製本輪明確禁止的 `data/`, `reports/`, `obsidian_vault/`, `.mka/`。這些測試不是已知fail，而是本輪未驗證。
- 未執行Google Sheets API、Google auth或Apps Script：本階段禁止外連與建立憑證。
- 未啟動Slack Bot、未呼叫Slack API。
- 未啟用外部LLM。
- 未執行migration、formal index rebuild、production Vault sync或rollback。
- repository無tracked CI workflow可執行；pytest設定位於`pyproject.toml`。

## 6. Evidence paths與核心判定

| 判定 | Primary code evidence | Test evidence |
| --- | --- | --- |
| local xlsx而非Google CellData | `excel_preview.py`, `asset_metadata_preview.py` | `test_excel_preview.py`, `test_asset_metadata_preview.py` |
| row-derived identity | `excel_preview.py`, `obsidian_sync.py`, `asset_metadata.py` | `test_obsidian_sync.py`, `test_asset_apply_preview.py` |
| Markdown-derived Official index | `content_index.py` | `test_content_index.py` |
| SQLite/FTS/vector存在 | `indexing.py`, `retrieval.py`, `embeddings.py` | `test_typed_query_retrieval.py`, `test_content_index.py` |
| oral-only可先落地、Slack後擋 | `excel_preview.py`, `apply_review_decisions.py`, `content_index.py`, `governance.py`, `slack_presentation.py` | `test_slack_structured_governance.py` |
| Obsidian plan/backup/rollback | `obsidian_sync.py` | `test_obsidian_sync.py` |
| 無mass-deletion source gate | `obsidian_sync.py` | 現有`test_obsidian_sync.py`只驗archive/rollback，無range-collapse/source-health case |
| one-off atomic store executor | `store_data_sync_plan_v2_execution.py` | `test_store_data_sync_plan_v2_execution.py`（未在安全批次執行，僅讀取/搜尋） |
| Slack Bot確已實作 | `slack_interface.py`, `slack_presentation.py` | 五個已執行Slack test modules |
| 無Official/Enrichment分層 | `content_index.py`, `slack_interface.py` | 現有Slack/content-index tests無雙repository contract |
| 無standalone Apps Script／ID writer實作 | tracked inventory無 `.gs`、`appsscript.json`或`clasp` project；`excel_preview.py`仍是local read path | 現有tests無MREC／MET allocator、BRD controlled backfill、write allowlist或concurrent allocation contract |
| 無sync／release Ops notifier | tracked source只有user-query Slack handler與denylist owner flag；無Release coordinator、Private Slack Ops sender或`release_status`／`notification_status` operation record | 現有tests沒有Attempt 3 final-failure alert、target allowlist、payload sanitizer或notification/release state separation contract |
| Slack renderer仍用固定shared caps | `slack_presentation.py`以`entities[:5]`及跨內容共用10筆asset slice截取presentation；沒有Public Metric獨立cap、rendered-size budget或eligible remaining metadata | 現有Slack tests未覆蓋Decision 6 post-governance metric cap、atomic claim／citation、rendered budget或corpus conservation |

## 7. 不確定事項

- 認證已確認為專用read-only Service Account；scheduler已確認為Asia/Taipei 09:00、09:30、10:00共最多3次attempt。Decision 2已確認Slack／internal search是Official retrieval surface而非G-M exposure channel，第一版不新增Slack欄位；tracked code尚未實作generic與usage-specific intent分流。Decision 3已確認Manual Enrichment `approved_by`須exact-match外部受控authorized whitelist；tracked code尚無ENR parser或approver validator，且reviewer ID scheme、whitelist storage與historical revocation仍是open design／governance問題。Decision 4已確認permanent ID writer採external standalone Apps Script、固定canonical Spreadsheet ID與最小欄位白名單；tracked repository仍無Apps Script／allocator實作，且`clasp`、CI/CD、deployment identity／owner、secret供應與API deployment仍是open implementation details。Decision 5已確認Private Slack Ops為final sync／release operational failure的primary alert surface，且release結果須先durable並與notification狀態分離；tracked source仍無Ops notifier、target allowlist或payload sanitizer，notification retry／Email fallback／成功通知與實際Slack identity仍是open implementation details。Decision 6已確認Public Metric獨立`metric_item_cap`、Content cap與overall rendered budget；tracked renderer仍是固定shared caps，沒有atomic Public Metric budget或eligible remaining metadata。Decision 1–11現均已確認，沒有Remaining Decision；cap數值、budget計量與pagination UX等繼續作open implementation questions。見`08_DECISIONS_REQUIRED.md`。
- mass-deletion數值threshold需先以不含正文的歷史counts/dry-run校準，本審查不猜定。
- Google API回傳的實際merge/rich-text/formula組合尚未連線驗證；本輪只有使用者提供的schema事實與tracked local xlsx behavior可比對。
