# O. Formal Content Index Spec(ROADMAP Stage 1)

> 定位:把 Obsidian vault 中**已核准且可檢索**的 managed 內容,建進正式 SQLite content index,讓 `mka search / ask` 可以對真實內容運作。
> 這是 pipeline「內容在筆記庫裡」→「內容可被檢索」的一步。完成後 Stage 2(intent gating)才有作用對象。
> 執行等級:standard-coding。無使用者待決事項——本 spec 所有設計已定。

---

## 0. 範圍與名詞

- **來源**:`obsidian_vault/MKA/` 下的 managed 檔(frontmatter `managed_by: marketing-knowledge-agent`)。
- **目的地**:`.mka/content_index.sqlite`(新的正式 index 路徑;與 prototype 的 `.mka/index.sqlite` 分開,互不影響)。`*.sqlite` 已在 .gitignore。
- **重用**:索引結構、chunking、檢索完全重用既有 `SQLiteIndex` / `chunk_documents` / `SQLiteRetriever`——本 sprint 不改它們。新的只有「來源篩選 + 建置命令 + 安全斷言」。
- **明確不做**:intent/channel gating(Stage 2)、外部 LLM(Stage 3)、增量索引(全量重建即可)、改 retrieval/generation/reranking 邏輯、改 `mka search/ask` 的預設 `--db`(維持現狀,避免影響 prototype 流程)。

## 1. CLI

```text
mka build-content-index [--vault obsidian_vault] [--namespace MKA]
                        [--db .mka/content_index.sqlite]
                        [--report-dir reports/content_index] [--confirm]
```

- **無 `--confirm`(預設)**:唯讀 plan 模式——只產出資格報告(哪些檔會被索引、哪些被排除與原因),不建 DB。exit 0。
- **有 `--confirm`**:全量重建 DB + 產報告 + 跑建置後安全斷言。
- exit codes:0=成功;1=plan 模式正常結束但有需人工注意的排除異常(見 §4);2=前置/斷言失敗(DB 已刪除,不留半成品)。

## 2. 資格規則(逐檔判定,任一不符即排除並記錄原因碼)

| # | 條件 | 排除原因碼 |
| --- | --- | --- |
| 1 | frontmatter `managed_by == marketing-knowledge-agent`(用 parser 判定,不用字串比對——引號風格會騙過 grep,見 LESSONS 2026-07-10) | `not_managed` |
| 2 | 不在 `_archived/` 下 | `archived` |
| 3 | 不在 `_vault_only/` 下 **且** `can_enter_content_index == true`(雙重條件,兩者皆查) | `vault_only` / `index_flag_false` |
| 4 | `record_type ∈ {content_asset, merchant_case, public_metric}`;restricted_customer / handle_mapping / pending_metric 即使出現也拒絕(縱深防禦,不信任上游) | `forbidden_record_type` |
| 5 | public_metric 另需 `allowed_exposure_channels` 非空 | `metric_missing_channels` |
| 6 | frontmatter 可成功解析為 `DocumentMetadata`(見 §3 已知障礙) | `metadata_parse_error` |

註:`approve_internal_only` 的內容(can_quote_externally=false)**允許進 index**——內部可檢索,對外引用由 answer 層的既有 warning 與未來 Stage 2 阻擋。這是 D 文件 R10 的既定判準。

## 3. 已知障礙與指定解法(已實測確認,不要重新診斷)

**問題**:synced 檔的 frontmatter 經 `parse_markdown_with_frontmatter` 解析後,dict 型欄位是 JSON 字串而非 dict(實測:`invalid_asset_values` 得到 `'{}'` 字串,`DocumentMetadata(**meta)` 拋 `dict_type` validation error)。

**指定解法**:在新模組(建議 `content_index.py`)內寫一個 `normalize_vault_frontmatter(meta: dict) -> dict`:
- 對 `DocumentMetadata` 中型別為 dict 的欄位(目前僅 `invalid_asset_values`),若值是字串且以 `{` 開頭 → `json.loads` 還原;解析失敗 → 該檔記 `metadata_parse_error` 排除,不要猜。
- 移除 sync 專用鍵(`managed_by` / `sync_batch_id` / `synced_at` / `content_checksum`)後再餵給 `DocumentMetadata`(Pydantic 對 extra 欄位的行為不可依賴,顯式移除)。
- **不要修改 `frontmatter.py` 共用 parser 或 `models.py`**——那影響 ingest/validate/sync 全部既有流程,屬架構決策(N sprint 的 follow-up 已記錄同類議題)。normalizer 放在 content_index 自己的模組內。

## 4. 建置報告(`reports/content_index/build_report.md`)

- 資格統計:掃描檔數 / 索引數 / 各排除原因碼計數
- 逐檔清單:索引的(檔名+record_type+can_quote_externally);排除的(檔名+原因碼)
- **異常警示**(plan 模式 exit 1 的觸發條件):出現 `forbidden_record_type` 或 `metadata_parse_error`——這兩種代表上游有問題,人要看
- 安全斷言結果(§5)
- vault 狀態摘要(managed 檔總數、最新 sync_batch_id)——供日後判斷 index 是否過期
- audit log:`reports/audit_log.csv` 追加一行(timestamp、command、索引數、DB path)

## 5. 建置後安全斷言(--confirm 模式;任一失敗 → 刪除 DB、exit 2)

1. **禁入型別**:SQL 查詢 DB 中 `record_type ∈ {restricted_customer, handle_mapping, pending_metric}` 的 documents 數,必須為 0。
2. **Denylist 掃描**:用當前 `reports/excel_preview/restricted_customers.json` 自建 `GovernanceIndex`,對 DB 中每個 chunk 的 title+text 跑 `check_text`,命中必須為 0。
3. **守恆**:資格報告的「索引數」== DB 的 document 數;chunk 數 > 0。
4. **檢索煙霧測試**:`search_index` 對 DB 查一個已知詞,回傳的 citation 必須含 `source_sheet` / `source_row`(溯源鏈完整)。

## 6. 測試 DoD

- [ ] 資格規則 §2 每個排除原因碼各至少一個測試(fixture 造對應檔)
- [ ] `normalize_vault_frontmatter`:JSON 字串 dict 還原、sync 鍵移除、壞 JSON → 排除
- [ ] 對「真實 synced 檔的複本」做整合測試:13 檔 → 12 索引 + 1 `vault_only` 排除(用 tmp 複本,不依賴真實 vault 狀態)
- [ ] 斷言 should-fail:fixture 注入一個 record_type=restricted_customer 的 managed 檔 → build 拒絕且 DB 被刪
- [ ] plan 模式不建 DB(檔案不存在)
- [ ] 全套 pytest 全綠;不改既有測試斷言
- [ ] Real-data smoke:對真實 vault 跑 plan → 預期 13 掃描 / 12 可索引 / 1 `vault_only`;再跑 `--confirm` → 斷言全過;然後 `mka ask "<某已知內容詞>" --db .mka/content_index.sqlite --restricted-customers reports/excel_preview/restricted_customers.json` 能回答且 citation 帶溯源。回報只貼計數與斷言結果,不貼品牌名

## 7. 完成判準(= ROADMAP Stage 1 的關卡)

`mka ask` 能對真實 12 篇內容回答、citation 完整、internal-only 內容帶 warning、denylist 查詢被既有 GR-1 防線攔截。達成後 Stage 2(gating)開工。

---

*規格作者:Fable 5(2026-07-10)。§3 的障礙與解法經實測確認;§2 資格規則引用 D 文件 R10,修改屬層級二。*
