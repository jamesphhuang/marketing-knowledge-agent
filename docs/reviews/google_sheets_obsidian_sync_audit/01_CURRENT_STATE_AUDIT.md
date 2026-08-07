# Current State Audit

## 1. Baseline

- Repository: `jamesphhuang/marketing-knowledge-agent`
- Branch: `codex/audit/google-sheets-obsidian-sync`
- HEAD／local `main`／merge-base: `11c99c86ccbbab06f2bf583f8918560d0ce4e985`
- Python package: `src/marketing_knowledge_agent/`
- Console entry: `mka = marketing_knowledge_agent.cli:main`
- Tracked inventory: 171 files；67 source modules、59 test modules、32 docs、8 `.claude` support files、5 root files。
- No tracked CI workflow was found.

## 2. 真實 entry points

`cli.py` 有下列 active command families：

- Prototype RAG: `ingest`, `validate`, `backfill-report`, `search`, `ask`, `agent-ask`, `explain-query`, `evaluate`。
- Excel/review: `excel-preview`, `review-template`, `validate-review-decisions`, `apply-review-decisions`。
- Asset metadata: `asset-metadata-preview`, `validate-asset-review-decisions`, `apply-asset-review-decisions`, `apply-asset-metadata`。
- Parent／identity governance: missing-parent diagnosis／preview／validation、resolution storage schema preview、parent sync plan／validate／confirm、parent authority review／bundle。
- Governance store: plan／regenerate／validate／confirm／execute、schema v2 plan／validate／confirm／execute、existing store validation。
- Store materialization: store-data-sync-v2 plan／validate／confirm／execute／existing validation。
- Search alias: v1/v2 plan／validate／confirm；runtime loader 已存在於 `search_aliases.py`。
- Output surfaces: `preview-slack-output`, `sync-obsidian plan|execute|rollback`, `build-content-index`, `slack-bot`。

CLI 命令數量多不等於通用 workflow 已完成。多個後期 executor 內含 exact plan ID、commit hash、row count、特定 row set 與 formal artifact checksum，是歷史批次的受治理執行器，不是可接受任意 Google snapshot 的一般同步服務。

## 3. 現行資料流

### 3.1 Prototype flow

```text
Markdown Vault
→ ingestion.load_documents
→ chunk_documents
→ SQLiteIndex.rebuild
→ SQLiteRetriever / reranking
→ generation / structured_results
→ CLI or Slack
```

`SQLiteIndex.rebuild` 直接對指定 DB drop／create `documents`, `chunks`, `chunks_fts`。Embedding 由 deterministic local `embed_text` 產生並以 JSON 存在 `chunks.embedding_json`。

### 3.2 Excel review flow

```text
local .xlsx
→ excel_preview.read_xlsx_workbook
→ five record normalizers
→ preview JSON / Markdown
→ review-template
→ validate-review-decisions
→ apply-review-decisions
→ approved_vault_preview / governance_table_preview / internal_inventory_preview
```

這個 flow 的 canonical join key 是 `source_sheet + source_row + record_type`。`apply-review-decisions` 會將一個 merchant row 渲染為一個 Markdown，四類 content title 混在同檔表格中；public metric 亦是一列一檔。

### 3.3 Current Obsidian／formal index flow

```text
approved_vault_preview Markdown
→ sync-obsidian
→ obsidian_vault/MKA managed Markdown
→ build-content-index 重新解析 Markdown
→ .mka/content_index.sqlite
→ Slack / CLI search
```

這與目標的 sibling generation 不同。`content_index.py` 明確把 Obsidian managed Markdown 當來源，剝除 sync-only fields 後轉成 `DocumentMetadata`。

### 3.4 Later governed materialization flow

Repo 另有 append-only governance decision store、resolution schema preview、parent authority、store-data-sync-v2 與 production alias projection。這些模組證明現有 codebase 已具備：

- plan identity／manifest hash／confirmation artifact；
- immutable historical evidence／hash chain；
- candidate DB、backup bundle、atomic file replace、rollback rehearsal；
- managed Vault 與 formal SQLite 的 post-apply validation；
- exact alias projection fail-closed loader。

但 `store_data_sync_plan_v2_execution.py` 綁定四個 create rows、十個 governance-only rows、特定 expected counts 與 plan authority；它不能替代新的 generic batch engine。

## 4. Excel importer 真實能力

### 已實作

- 固定五個 sheet 與 header row preflight。
- 從 `.xlsx` ZIP/XML 讀 shared string、inline string、cached value、boolean。
- 讀 merge ranges，僅對同欄的垂直 merge，且只對已有其他 physical value 的 row 展開。
- Public metric `類型`／`指標`與 pending metric做受限 fill-down。
- Merchant governance risk、restricted denylist、public channels、pending/internal、handle mapping normalize。
- Synthetic workbook tests 與 120／11 baseline tests。
- Asset preview 可從 worksheet hyperlink relationships 讀整格 external hyperlink。

### 部分或缺失

- 無 Google Sheets client、Spreadsheet ID、hidden-sheet fetch、OAuth／Service Account adapter。
- 無 `formattedValue`／`effectiveValue`／`userEnteredValue` 分層模型。
- `.xlsx` parser只取 `<v>` cached value；不保留 formula provenance，也沒有 HYPERLINK fallback。
- 無 Rich Text run-level hyperlinks、data validation contract。
- Asset hyperlink reader只處理 worksheet hyperlink relationship；沒有 rich-text links、公式 link、文字 URL fallback。
- Merge inheritance沒有通用 cell provenance，也沒有針對 Google merge metadata的 exact inheritance model。
- URL validator只擋非 HTTP(S)、credentials pattern、部分 redirect／shortener／tracking；沒有 localhost、private/link-local/reserved IP、internal admin path或廣泛 sensitive query key防線。
- baseline counts主要存在測試／文件，runtime並未把所有正式 workbook基準寫成 blocking validation。

## 5. 現行資料模型與 identity

`DocumentMetadata` 是大型共用 schema，涵蓋 content asset、merchant case、restricted customer、public/pending metric與handle mapping。優點是 citation與filters共用；缺點是不同 entity與lifecycle塞在同一 record模型。

目前沒有 BRD、MREC、MET、ENR欄位。實際 identity為：

- parent record: `商家夥伴案例資料庫:r<source_row>`；
- asset: `<parent record_id>:<asset_type>`；
- document: 多數由 source path或 Markdown path `stable_id`；
- alias owner: row-derived parent record ID；
- Obsidian matching: `(source_sheet, source_row)`，再 fallback path。

因此 identity會隨插列／排序改變，不符合永久 ID需求。

## 6. Governance 現況

### 已有防線

- `restricted_customer`／`handle_mapping` 不進一般 retrieval；formal index另擋 pending。
- query denylist precheck、result filtering、answer redaction、citation post-filter。
- External intent強制 public／published／can_quote、排除 pending。
- `metadata_allows_written_external_use` 對 verbal-only／restricted note fail closed。
- Obsidian execute前與寫入後 denylist scan。
- Review validation、row coverage、reviewer、conservation與preview/apply分離。
- Slack永遠用 external intent，denylist audit不記 query原文。

### 關鍵漏洞／不一致

- `normalize_public_metric_row` 遇到「僅用於口頭」時將 channels改成 `['verbal_briefing']`，但 `can_quote_externally`仍為 true。
- `apply-review-decisions` 可將此 metric渲染到 approved Vault preview。
- `content_index` 對 public metric只檢查 channels非空，所以 oral-only claim可進 documents、chunks、FTS與embedding。
- 現有正式 Slack governance test明確驗證 oral-only record已存在 formal SQLite，再確認 Slack body/citations不顯示；防線位置太晚，不符合本次「不得持久化」要求。
- 一般 Slack問答 audit會保存原始 query；新規格需確保 oral-only正文或查詢內容不被 log。
- Denylist缺檔時系統只 warning並繼續，對 future Official publish應改為 blocking input failure。

## 7. Obsidian 現況

### 已實作

- `MKA` namespace隔離、managed marker、checksum、plan state hash。
- add/update/archive/unchanged/user-edited/unmanaged分類。
- conflict預設擋、可明確 skip。
- execute前備份整個namespace、逐檔atomic write、失敗整體還原、手動rollback。
- namespace外快照檢查、manifest守恆、audit log。

### 與目標差距

- 一列 merchant case仍是一份混合 Markdown，沒有 Brand／Source Record／Content Asset／Taxonomy拆分與Wiki links。
- 檔名含source row並作 fallback定位；不是永久 ID。
- archive只移動檔案，沒有 canonical model上的 `archived_at`／`archived_reason`／restore state。
- 空或縮小preview會直接產生大量 `will_archive`；沒有mass-deletion safety gate。
- 沒有 `90_Sync/manifests`、`last_success.json`的正式跨輸出契約。
- Manual enrichment namespace與approval content hash尚未實作。

## 8. SQLite／retrieval 現況

正式 index schema只有：

- `documents`: flattened base欄位 + `metadata_json` + content；
- `chunks`: chunk text + deterministic embedding JSON；
- `chunks_fts`: FTS5 title/body。

Typed query planning、AND constraints、unsupported fail-closed、alias exact lookup、structured result、retrieval/citation traceability均已有良好測試。現有 schema preview另提出 `source_records`, `source_record_aliases`, `content_assets`，但它仍使用row-derived `record_id`且只是preview／特定遷移設計，未成為通用formal schema。

`build_content_index`不是目標所需的atomic release：它先寫report，再在目標DB直接rebuild；失敗會刪除DB，而不是保留舊active DB並atomic swap candidate。後期one-off store sync有candidate/backup/rollback，但不是generic pipeline。

## 9. Slack 現況

Slack Bot不是只有文件：`slack_interface.py`已實作Socket Mode啟動、channel allowlist、token env讀取、thread reply、audit與external-intent查詢；`slack_presentation.py`有structured result、URL canonicalization、dedupe、caps與written-safe citation filter。

缺失：

- 沒有Official／Enrichment index selection；只能讀單一`content_index.sqlite`。
- 沒有 `include:enrichment` parser或提示。
- Slack channel對G～M exposure permissions沒有明確mapping；目前只用「written external safe」總閘。
- Public metric是否計入display cap未決。
- Structured renderer不套`max_answer_chars`，程式註解把pagination留給後續。
- 一般query原文會進audit log。

## 10. Configuration／CI／tests

- `pyproject.toml`: Python >=3.9，runtime依賴Pydantic與slack-bolt，dev依賴pytest。
- `setup.py`與`pyproject.toml`重複package metadata；`setup.py`漏列slack-bolt，兩者有依賴漂移風險。
- `.gitignore`正確忽略env、credentials、reports、data、Vault、SQLite、`.mka`與Slack／LLM config。
- 無Google API dependency或adapter。
- 無tracked CI workflow；test setup只在`pyproject.toml`。
- 本輪最終安全執行431個互不重疊的tests，全部通過；每批均顯示同一組6項Pydantic validator deprecation。

## 11. 文件與程式不一致

1. README仍把Obsidian描述為content index來源；新目標要求Google normalized model直接產生index。
2. ROADMAP稱Slack程式已完成，這對現行單一Official-like index成立，但不代表新Official／Enrichment分層完成。
3. `apply_review_decisions` summary仍含「sync尚未實作」字樣，但`sync-obsidian`已實作。
4. Spec T建議candidate DB atomic replace；`build_content_index.py`本身仍直接rebuild target。
5. `AGENTS.md`第一階段限制mock/test資料，README有sample public website flow；本次沒有使用任何資料目錄。
