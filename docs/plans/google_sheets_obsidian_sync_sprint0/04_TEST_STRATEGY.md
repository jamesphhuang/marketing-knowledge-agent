# Sprint 0 Test Strategy

## Current test architecture

- Test runner：pytest；`pyproject.toml`設定`testpaths = ["tests"]`、`pythonpath = ["src"]`。
- 常用模式：`tmp_path`隔離檔案、table-driven parametrization、直接import pure functions、CLI以`main([...])`測試。
- `tests/fixtures.py`提供一般mock Markdown vault。
- `tests/conftest.py`含大量historical fixture，部分會讀／複製`data/`、`reports/`、`obsidian_vault/`、`.mka/`；Sprint 0 tests不得引用這些fixtures。
- 現有Excel tests自行建立synthetic XLSX ZIP/XML；asset tests有URL conflict/canonicalization；Obsidian tests有plan binding、denylist、backup/rollback；content-index tests固定Markdown-derived legacy flow。
- Audit原始安全回歸曾有431 passed，但本planning round依指示不重跑，不能把歷史結果當本輪驗證。

## Test categories

### Existing regression suite

在implementation期間，按實際affected boundary保留：

- `tests/test_excel_preview.py`
- `tests/test_excel_governance.py`
- `tests/test_asset_metadata_preview.py`
- `tests/test_asset_apply_preview.py`
- `tests/test_asset_apply_plan.py`
- `tests/test_ingestion.py`
- `tests/test_chunking.py`
- `tests/test_content_index.py`
- `tests/test_obsidian_sync.py`
- `tests/test_query_gating.py`
- `tests/test_typed_query_retrieval.py`
- `tests/test_governance_evals.py`

這些tests證明legacy compatibility，不代表target architecture完成。`tests/test_slack_structured_governance.py`讀取`.mka`及`reports` historical artifacts，且明確固定oral-only已存在formal DB的舊現況；Sprint 0不得依賴或改寫它，新early-minimization tests需另建。

### Sprint 0 unit tests

- DTO field/enum/validator與parent互斥。
- formula value resolution、merge ownership、lineage。
- ID regex/namespace/row-independent identity。
- link extraction、URL validator、asset resolver。
- oral-only minimizer、capture policy、HTML normalizer、hash、chunk metadata／identity builder、manifest、preview pure functions。

### Contract tests

- Reader protocol無write method且synthetic implementation可替換。
- Canonical serialization與hash type/domain分離。
- `CanonicalReleaseInputs`只接受canonical metadata/CapturedContent，不接受Markdown path/body parser。
- CapturedContent status/body/timestamp組合合法性。
- Release manifest完整composition、不允許partial pointer。

### Synthetic fixture tests

建議tracked fixture layout：

```text
tests/fixtures/google_sheets_sprint0/
  cell_data_formula_and_merge.json
  cell_data_links.json
  cell_data_oral_only.json
  article_clean.html
  article_boilerplate.html
  article_table.html
  article_empty.html
```

所有名稱、claims、URLs使用`example.com`或保留測試domain；不複製真實Spreadsheet snapshot、公司claim或第三方文章。

### Deterministic / property-style tests

不必新增Hypothesis dependency即可先做table/generated loops：

- input dict/key/sheet iteration order permutations產同一canonical bytes/hash。
- merge ranges不可越界、重疊或跨未允許field；非merge空白永不fill。
- URL canonicalization idempotent，safe canonical URL再canonicalize不改值。
- row reorder/insert只改lineage，不改MREC/MET/asset identity。
- HTML whitespace/attribute order等無語義差異不改clean body/hash。
- 相同CapturedContent與注入synthetic span重跑輸出同metadata／identity；不同parent不得共享chunk identity。

### Negative governance tests

- oral-only sentinel掃描repr、exception、caplog、JSON、Markdown preview bytes、capture candidates、manifest/chunks均為零。
- restricted/pending/oral payload不可進canonical Official set。
- unsafe/secret URL完整值不可出現在exception/report。
- 2+ canonical URLs不得挑winner或產多asset。
- BRD uncertain、missing stable evidence relationship、policy missing、freshness threshold missing均fail closed。
- Markdown input不得被Official canonical builder contract接受。

## Required coverage matrix

| Required scenario | Primary test file | Expected result |
| --- | --- | --- |
| rich-text embedded hyperlink | `test_embedded_link_extraction.py` | eligible Content Asset cell產priority 1 candidate＋run provenance |
| whole-cell hyperlink | same | priority 2 candidate |
| HYPERLINK formula fallback | same | safe parse first argument；formula不作body |
| literal URL fallback | same | only complete single HTTP(S) text |
| multiple sources canonicalize same URL | `test_content_asset_resolution.py` | one distinct URL／one asset |
| multiple distinct canonical URLs | same | `needs_review`，no winner |
| title but no URL | same | `incomplete`, `searchable=false` |
| missing video URL | same | video incomplete；不猜URL |
| formula effective value | `test_cell_normalization.py` | effective/formatted正文；formula only provenance |
| merge-aware Public Metric inheritance | same | 只依anchor/range繼承F |
| no blind fill-down | same | nonmerge空白保持空白 |
| oral-only before persistence DTO | `test_oral_only_minimization.py` | only `ExcludedSourceRef` |
| no oral-only in debug | same + `test_sync_preview.py` | sentinel scan zero |
| metric/oral cell cannot enter asset link extractor | `test_embedded_link_extraction.py` | input contract rejection；no candidate |
| MREC/MET validation | `test_canonical_models.py` | format/namespace/duplicate fail closed |
| BRD uncertain | same | `needs_review`, no auto assign |
| row reorder identity | same + integration | identity stable, lineage changes |
| deterministic HTML normalization | `test_html_normalization.py` | same semantic input→same body |
| nav/footer/script removal | same | removed from clean body |
| deterministic content hash | `test_content_hashing.py` | body+parser-version based |
| stable chunk metadata | `test_captured_chunks.py` | 注入相同synthetic span時parent/hash/section lineage與identity stable |
| no Markdown reparse | `test_release_contracts.py` + integration | Markdown-shaped input rejected |
| release manifest fields | `test_release_contracts.py` | metadata/capture/siblings完整 |

## Testing tiers during implementation

### Tier A — WP-local targeted tests

每個WP完成時只要求該WP直接相關的新增targeted tests；不要求每包重跑全部legacy regression。

| WP | WP-local targeted test |
| --- | --- |
| WP0 | `test_sprint0_test_harness.py` |
| WP1 | `test_sheets_contracts.py` |
| WP2 | `test_source_fingerprint.py` |
| WP3 | `test_cell_normalization.py` |
| WP4 | `test_canonical_models.py` |
| WP5 | `test_oral_only_minimization.py` |
| WP6 | `test_embedded_link_extraction.py` |
| WP7 | `test_url_safety.py` |
| WP8 | `test_content_asset_resolution.py` |
| WP9 | `test_captured_content.py` |
| WP10 | `test_capture_policy.py` |
| WP11 | `test_html_normalization.py` |
| WP12 | `test_content_hashing.py` |
| WP13 | `test_captured_chunks.py`（只驗注入span的metadata／identity，不驗production splitter） |
| WP14 | `test_release_contracts.py` |
| WP15 | `test_sync_preview.py`（含sentinel scan） |
| WP16 | `test_sprint0_contract_integration.py` |

### Tier B — Contract checkpoint regressions

- **Governance checkpoint（WP5後）**：WP0–WP5全部new tests，加`test_excel_preview.py`、`test_excel_governance.py`。
- **Link/asset checkpoint（WP8後）**：WP6–WP8全部new tests，加既有asset metadata／preview／apply tests。
- **Capture checkpoint（WP13後）**：WP9–WP13全部new tests；不跑legacy `test_chunking.py`來替WP13背書，因兩者contract不同。
- **Release/preview checkpoint（WP14與WP15完成後）**：`test_release_contracts.py`、`test_sync_preview.py`及相關manifest compatibility tests。
- **Integration checkpoint（WP16）**：全部新增Sprint 0 tests與cross-boundary negative cases。

### Tier C — Broad legacy regression

只在適當里程碑執行，不綁每一個WP：

- Phase 2後：Excel preview/governance regression。
- Phase 3後：asset metadata/preview/apply regression。
- WP16 final gate：ingestion、legacy chunking、content index、Obsidian sync、query gating、typed retrieval、governance eval等明確列出的safe suites。
- Historical Slack suite未另獲授權時不執行，因其會讀runtime artifacts且固定舊oral-only現況。

## Sprint 0 final integration-only runs

在WP16完成後才跑：

1. 全部新增Sprint 0 test modules。
2. Tier C中明確列出且不依賴runtime sensitive fixtures的existing regression modules。
3. lint/type check（僅repository已有工具時；目前未見tracked linter/type checker設定，不得臨時新增工具後宣稱必過）。
4. `pytest`完整suite只有在確認不會讀取禁止目錄，或另獲明確授權後才執行；否則沿用明確列檔的safe suite。

## Failure policy

- 任一network guard、sentinel scan、identity、merge、URL safety、manifest composition或no-Markdown-reparse negative test失敗，即停止該WP及所有downstream WPs。
- 不以`xfail`、skip、fixture filtering或刪除legacy test掩蓋target conflict。
- Legacy historical oral-only test可以繼續描述current state；new canonical tests必須描述target state，兩者透過未接線compatibility boundary共存。
- Production content splitting algorithm及其chunk-size／overlap／section heuristic tests明確移至後續Sprint；WP13不得以legacy chunker regression取代契約測試。
