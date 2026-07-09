# K. Apply Review Decisions Preview Spec

> 執行順序：J sprint 完成並且真實 decision CSV 通過（含 reviewer/reviewed_at 補齊）後才開工。
> 鐵律（hard constraint 7）：本命令**只產生 preview**。不寫 Obsidian vault、不建正式 content index、不修改 decision CSV 與 preview JSON。全部輸出在 `reports/excel_preview/apply_preview/` 下。
> 執行者等級：standard-coding（規格已完整）；「無 review row 預設政策」若使用者未確認，先實作預設隔離版。

---

## 0. 名詞定義（本 spec 的 canonical 定義，寫進 GOVERNANCE_RULES.md 前需使用者確認）

- **vault**：Markdown 知識庫儲存層（未來 = Obsidian）。進 vault ≠ 可被檢索。
- **content index**：可檢索、可產生 citation 的層。進 index 必先進 vault（content 類）。
- `can_enter_vault=true, can_enter_content_index=false` 的含義：保留 Markdown 檔供人工瀏覽/追溯，但不建進檢索 index（例：exclude_from_content_index 的紀錄）。

## 1. CLI 與前置檢查

```text
mka apply-review-decisions --decisions <csv> --preview-dir <dir> --output reports/excel_preview/apply_preview
```

執行前依序檢查，任一失敗 → exit 2，不產生任何輸出檔：

1. 內部先跑一次 validation（重用 J 的函式）：`error_count > 0` → 拒絕。
2. `blank_reviewer_count > 0` 或 `blank_reviewed_at_count > 0` → 拒絕（訊息引導：「請完成人工簽核後重試」）。
3. row coverage：decision CSV 與 preview JSON 的 expected rows 完全一致（J §5）→ 否則拒絕。
4. 輸出目錄已存在時：整目錄覆蓋重建（冪等），但先在 summary 記錄前次產出時間。

## 2. 輸出結構

```text
reports/excel_preview/apply_preview/
├── approved_vault_preview/          # 將來會 sync 進 Obsidian 的 Markdown
│   ├── merchant_cases/<slug>.md
│   ├── public_metrics/<slug>.md
│   └── _vault_only/                 # can_enter_vault=true 但 index=false 的紀錄
├── governance_table_preview/
│   ├── restricted_customers.json    # denylist preview（結構見 §5）
│   └── governance_table_summary.md
├── internal_inventory_preview/
│   └── pending_metrics.md           # 內部盤點清單
├── excluded_records.md
├── not_reviewed_records.md          # 無 review row 的紀錄（見 §7）
└── apply_decisions_summary.md
```

## 3. review_decision → 輸出位置對應表

| review_decision | 輸出桶 | Markdown？ | 附帶行為 |
| --- | --- | --- | --- |
| approve | approved_vault_preview/（依 record_type 子目錄） | ✅ | frontmatter 完整 metadata |
| approve_internal_only | approved_vault_preview/，frontmatter 強制 `can_quote_externally: false`、`data_classification: internal` | ✅ | 檔內頂部加 internal-only 警語 |
| keep_all_records | 同 approve（多筆各自成檔，檔名含 source_row 去重） | ✅ | frontmatter 保留 multi_interview_record=true |
| restricted_use_only | approved_vault_preview/，frontmatter 保留 restricted_note 與 channels | ✅ | 檔內頂部加使用限制警語 |
| exclude | excluded_records.md（列表項） | ❌ | 記錄 decision 理由欄（notes） |
| exclude_from_content_index | approved_vault_preview/_vault_only/ | ✅ | frontmatter `can_enter_content_index: false` |
| enter_governance_table_only | governance_table_preview/ | ❌（JSON） | 見 §5 |
| keep_internal_only | record_type=pending_metric → internal_inventory_preview/；其他 → _vault_only/ 且 internal 標記 | 視情況 | |
| needs_update / enrich_metadata / manual_review | excluded_records.md 的「未完成審核」小節 | ❌ | **不進任何 preview 桶**——未完成的決策不產出資產 |
| review_identity_mapping | 同上「未完成審核」小節（語意待定，GR-11） | ❌ | |
| deprecated | excluded_records.md 的「deprecated」小節 | ❌ | 若未來需要歷史檔，走 proposal 加 `_deprecated/` 桶 |

**歸桶守恆規則**：preview JSON 中每筆紀錄（handle_mapping 除外，不參與 apply，見 §8）必須恰好落在一個桶。任何一筆無法歸桶（欄位矛盾且 validation 漏擋）→ **整批失敗**，不產生部分輸出，回報矛盾清單。

**decision 優先規則**：歸桶依 review_decision（本表），不依 can_enter_vault 欄位。已知現有 template 對 exclude 建議列仍給 `can_enter_vault=true`（如 sample row 4）——此類矛盾以 decision 為準（exclude → 不產 vault 檔），並在 summary 的「decision 覆蓋欄位」小節逐筆列出，供人工複核。

## 4. Markdown 輸出格式

- 檔名：`<source_sheet_slug>-r<source_row>-<brand_or_metric_slug>.md`（ASCII slug；row 保證唯一性）。
- frontmatter：`DocumentMetadata` 的 metadata_dict 全欄位 + 追加 review 溯源欄：
  ```yaml
  review_decision: approve
  reviewer: <from CSV>
  reviewed_at: <from CSV>
  review_notes: <from CSV notes>
  applied_at: <ISO timestamp>
  decision_source: reports/excel_preview/review_decisions_template.csv
  source_sheet: 商家夥伴案例資料庫
  source_row: 68
  ```
- merchant_case 內文：標題 + 表格化的素材欄（article/video/podcast/news titles）+ notes。**merchant case 必須保留 source_sheet / source_row / review metadata**——這是 citation 溯源鏈。
- public_metric 內文：claim_statement + metric_note + **allowed_exposure_channels 顯式列表**（同時在 frontmatter 與內文，讓人工瀏覽 vault 時渠道限制可見）。

## 5. Governance JSON（denylist preview）

`governance_table_preview/restricted_customers.json`：

```json
[
  {
    "brand_name": "...",
    "match_terms": ["...normalized 後實際用於匹配的清單..."],
    "restricted_aliases": ["..."],
    "website_url": "...",
    "merchant_handle": null,
    "restricted_reason": "...",
    "nda_signed": true,
    "source_sheet": "「不可公開」客戶名單",
    "source_row": 5,
    "reviewer": "...",
    "reviewed_at": "...",
    "denylist_status": "active"
  }
]
```

要點：`match_terms` 是**展開後**的實際匹配清單（含 normalize 與 alias split 結果）——讓人工在 sync 前看得到「哪些字串會觸發 block」，直接對 GR-5/GR-6 誤傷做最後把關。summary md 列出全部 match_terms 與長度 < 4 的可疑短別名。

## 6. Pending metric 處理

進 `internal_inventory_preview/pending_metrics.md`：表格（metric_name | claim_statement | metric_note | source row | 建議補件事項）。**不產生 vault Markdown、不進任何 index 輸出**。它的價值是「缺口清單」：哪些數據等核准後可轉 public_metric。

## 7. 無 review row 紀錄（本 spec 最重要的新政策）

- preview JSON 中存在、但 expected review rows 之外的紀錄（= 無 issue 的乾淨紀錄，目前約 96 筆 merchant_cases + 29 筆 public_metrics）→ 預設**全部進 `not_reviewed_records.md`**，不進 approved vault preview。
- 提供 `--include-clean-records` 旗標：加上後，乾淨紀錄以「default 政策核准」進 approved_vault_preview，每筆 frontmatter 標 `reviewer: default_policy`、`review_decision: approve(default)`，summary 醒目統計。
- **旗標預設關閉**。是否常態開啟屬使用者裁決（G 信第 6 節待決問題 b）。

## 8. handle_mapping

不參與 apply。它是 normalization table，留在 preview JSON 供 enrichment 使用。summary 記一行計數即可。

## 9. apply_decisions_summary.md 內容

- 前置檢查逐項結果（validation 摘要、reviewer 完整性）
- 各桶計數表 + **守恆等式**（各桶合計 == preview 紀錄總數，明寫等式；此處數字為格式示意，實際值以當次執行為準：`120 = 18 + 1 + 76(not_reviewed) + ...`）
- 白名單斷言結果（見 §10 檢查 3、4）
- validation warnings 全文轉載（人工最終確認要看的就是這些）
- multi-record 被 exclude 的 row 單獨列出（I 文件建議）
- 明確聲明：「本輸出為 preview。Obsidian 未同步、正式 index 未建立。下一步需人工確認後另行執行 sync（尚未實作）。」

## 10. 如何保證不產生正式 index、不寫 production vault

1. **程式層**：apply 模組整體不 import `indexing` / `SQLiteIndex`；輸出路徑全部由 `--output` 派生，任何寫檔前斷言路徑在 output 目錄下（`Path.resolve().is_relative_to(output_dir)`）。
2. **測試層**：`test_apply_preview_writes_only_under_output_dir`（monkeypatch 記錄所有 open/write 路徑）；`test_apply_preview_does_not_import_indexing`（module 靜態檢查）。
3. **白名單斷言（restricted）**：輸出完成後自檢——approved_vault_preview 與 internal_inventory 全部檔案內容跑 `GovernanceIndex.check_text`，restricted 品牌命中數必須為 0；restricted 紀錄只允許出現在 governance_table_preview。命中 ≠ 0 → 整批失敗。
4. **白名單斷言（pending）**：approved_vault_preview 中 record_type=pending_metric 的檔案數必須為 0。
5. **制度層**：hard constraint 7 + 本 spec；E 模板 8 的派工 Non-goals。

## 11. 驗收條件（sprint DoD）

- [ ] §3 對應表全部 decision 有實作與測試（每 decision 至少一筆 fixture 走到對應桶）
- [ ] 守恆檢查 + 兩個白名單斷言實作並有測試（含故意注入 restricted 內容的 should-fail 測試）
- [ ] 前置檢查四項有測試（含 blank reviewer 拒絕）
- [ ] not_reviewed 預設隔離 + `--include-clean-records` 行為有測試
- [ ] 對 sample_data 實跑產出完整目錄樹
- [ ] ARCHITECTURE.md CLI 表新增 apply-review-decisions（Safety scope：writes preview only）
- [ ] pytest 全綠；fresh-context review（E 模板 5）通過
