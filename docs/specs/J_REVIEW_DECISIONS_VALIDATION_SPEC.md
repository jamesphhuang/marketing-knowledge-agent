# J. Review Decisions Validation Spec（下一個 sprint 規格）

> 重要前提：**read-only validator 已存在**（`review_decision_validation.py`，`mka validate-review-decisions`，測試綠）。
> 本 spec 是「補強規格」：明確標注哪些已實作（✅ 保留）、哪些是本 sprint 要新增（🆕）。
> 執行者等級：standard-coding。所有規則都有明確判定條件，不需要強推理。
> 不套用任何決策；全部輸出到 `reports/excel_preview/`。

---

## 0. Error / Warning / Info 定義

| 級別 | 定義 | 對下游的意義 |
| --- | --- | --- |
| **error** | 違反 governance 硬規則、資料不完整、或決策內部矛盾 | `error_count > 0` → 禁止進入 apply-preview（exit code 1） |
| **warning** | 可疑但人有權堅持的決策；需要人工看過但不阻擋 | 不阻擋 apply-preview，但 apply summary 必須逐條轉載 |
| **info** 🆕 | 統計性訊號，不指向特定 row 的錯誤 | 僅供人工參考（例：橡皮圖章訊號） |

## 1. 必填欄位檢查

對每一 row 檢查以下欄位非空。標注為現狀 ✅ 的已實作，保持不變：

| 欄位 | 級別 | 現狀 |
| --- | --- | --- |
| source_sheet / source_row / record_type | error（`missing_identity`） | ✅ |
| review_decision | error（`missing_review_decision`） | ✅ |
| can_enter_vault / can_enter_content_index / can_enter_governance_table / can_quote_externally | error（`invalid_boolean`，含空值） | ✅ |
| suggested_action | 🆕 error（`missing_suggested_action`）——template 一定會填，空代表 CSV 被手動刪改 | 🆕 |
| final_status | 🆕 error（`missing_final_status`） | 🆕 |
| reviewer | 🆕 **warning**（`blank_reviewer`），逐 row 發（現只有總計數） | 🆕 |
| reviewed_at | 🆕 **warning**（`blank_reviewed_at`）；非空時必須是 ISO 日期（YYYY-MM-DD），否則 error（`invalid_reviewed_at`） | 🆕 |

> reviewer/reviewed_at 用 warning 而非 error 的理由：validation 可能在人工審核**過程中**反覆執行（審一半先驗格式）。升級為 error 的位置在 **apply-preview 的前置檢查**（K spec §1），不在這裡。

## 2. Enum 檢查

- ✅ 已實作：`review_decision ∉ ALLOWED_REVIEW_DECISIONS` → error（`invalid_review_decision`）。
- Canonical enum 為 code 中的 `ALLOWED_REVIEW_DECISIONS`（13 值，含 `review_identity_mapping`）。使用者 brief 中列的 12 值清單缺 `review_identity_mapping`——**不要按 brief 刪 code 的值**；`review_identity_mapping` 的語意待使用者裁決（GR-11），裁決前維持允許。
- 🆕 `final_status` 加 enum 檢查：允許值 `{review_required, restricted, pending_review, approved, excluded, internal_only}`——**注意**：前三者是 template 產生的現值；後三者為 review 後人工可改的目標值。若實作時發現現有資料使用其他值，先回報再擴 enum，不要擅自加值。
- 🆕 `allowed_exposure_channels`：非空時，逐一檢查 pipe 分隔值 ∈ `AllowedExposureChannel` 七值 → 否則 error（`invalid_exposure_channel`）。

## 3. Conflict validation 規則矩陣

現有 per-type 政策 ✅ 全部保留（restricted_customer 五鎖、pending_metric 五鎖、missing_allowed_exposure_channels / no_valid_content_asset 觸發規則、suspected_duplicate warning、multi-record removal warning）。

🆕 新增以下規則（ID 供測試與報告引用）：

| ID | 條件 | 級別 | 理由 |
| --- | --- | --- | --- |
| CR-1 | record_type=restricted_customer 且 can_enter_content_index=true | error | ✅ 已由 per-type 鎖覆蓋，保留 |
| CR-2 | record_type=restricted_customer 且 can_quote_externally=true | error | ✅ 同上 |
| CR-3 | record_type=restricted_customer 且 can_enter_governance_table=false | error | ✅ 同上 |
| CR-4 | record_type=pending_metric 且 (can_quote_externally=true 或 can_enter_content_index=true) | error | ✅ 同上 |
| CR-5 🆕 | review_decision=exclude 且 (can_enter_content_index=true 或 can_quote_externally=true) | error | exclude 定義＝排除於 index 與公開用途 |
| CR-6 🆕 | review_decision=exclude_from_content_index 且 can_enter_content_index=true | error | 決策與欄位直接矛盾 |
| CR-7 🆕 | review_decision=enter_governance_table_only 且 can_enter_governance_table=false | error | 決策與欄位直接矛盾 |
| CR-8 🆕 | review_decision=enter_governance_table_only 且 (can_enter_content_index=true 或 can_quote_externally=true) | error | "only" 語意 |
| CR-9 🆕 | review_decision ∈ {keep_internal_only, approve_internal_only} 且 can_quote_externally=true | error | internal-only 語意；兩個 decision 同規則 |
| CR-10 🆕 | review_decision=restricted_use_only 且 allowed_exposure_channels 為空 | **warning**（`restricted_use_without_channels`） | 用 warning 不用 error：restricted_use 的限制可能寫在 notes（如僅口頭）而非 channel 欄。但 can_quote_externally=true 且 channels 空 → 升為 **error**（對外引用卻無任何允許渠道，矛盾） |
| CR-11 🆕 | review_decision=approve 且 can_quote_externally=false | **warning**（`approve_without_external_quote`） | 用 warning 不用 error：approve＝可進 index，內部使用完全合法（等同 approve_internal_only 的效果）。warning 目的為讓人確認不是漏勾。若團隊之後決定 approve 必須可外引，再升 error（層級二裁決） |
| CR-12 🆕 | review_decision=approve 且 issue_type 含 no_valid_content_asset | error | 無有效素材不可核准進 index（GOVERNANCE_RULES 既有規則的決策面鏡像） |
| CR-13 🆕 | review_decision=needs_update 或 enrich_metadata 或 manual_review，且 final_status=approved | error | 未完成的決策不能標記為已核准 |
| CR-14 🆕 | review_decision=deprecated 且 can_quote_externally=true | error | GOVERNANCE_RULES：deprecated 非明確核准不得外引 |
| CR-15 🆕 | record_type ≠ restricted_customer 且 review_decision=enter_governance_table_only | error | governance table 只收 restricted_customer（index role 表）。注意：merchant_case 使用 keep_internal_only 是**允許**的，不在本規則範圍 |
| CR-16 | same_brand / same_handle 僅資訊性標記卻被 exclude/deprecated/exclude_from_content_index | warning | ✅ 已實作（`multi_record_marked_for_removal`） |
| CR-17 | suspected_duplicate_review 且 decision ≠ manual_review | warning | ✅ 已實作 |
| CR-18 🆕 | review_decision=review_identity_mapping 且 record_type ≠ merchant_case | error（暫行） | 該值語意未定（GR-11），暫按「handle 身分待確認」最窄解釋；使用者裁決後修訂 |

> **設計原則（給實作者）**：error 保留給「邏輯上不可能同時為真」與「governance 硬規則」；「人可能有理由」的一律 warning。拿不準 → warning + 回報，不要擅自升 error。

## 4. Info 級統計訊號 🆕

| ID | 條件 | 訊息 |
| --- | --- | --- |
| IN-1 | 所有 row 的 review_decision == suggested_action 且 total_rows > 10 | 「全部決策與機器建議相同，請確認確實逐筆人工審核（A 風險 #2）」 |
| IN-2 | blank_reviewer_count > 0 或 blank_reviewed_at_count > 0 | 「apply-preview 前置檢查將要求全部填寫」 |
| IN-3 | preview JSON 的總紀錄數 vs review rows 數 | 「N 筆紀錄無 review row，apply 時將進入 not_reviewed 桶（K spec）」 |

## 5. Row coverage 檢查

✅ 已實作（missing_expected_review_row / unexpected_review_row / duplicate_review_row，皆 error）。保留。
🆕 順手重構：把 validation 對 `review_template` 私有函式的依賴改為公開的 `build_expected_review_rows()`（H-4）。

## 6. 輸出設計

現狀：單一 markdown 報告。🆕 改為三檔輸出（markdown 報告保留並更名）：

| 檔案 | 內容 |
| --- | --- |
| `reports/excel_preview/review_decisions_validation_summary.md` | 現有報告全部內容 + info 節 + 各 CR 規則觸發計數表 |
| `reports/excel_preview/review_decisions_validation_errors.csv` | 欄：severity, rule_id, row_number, source_sheet, source_row, record_type, review_decision, message。只含 error |
| `reports/excel_preview/review_decisions_validation_warnings.csv` | 同欄位。只含 warning |

CSV 的用途：管理者可用 Excel 開啟、排序、逐筆銷帳（M 文件痛點）。info 只進 summary 不進 CSV。
CLI：`--output` 語意改為 summary 路徑，errors/warnings CSV 放同目錄自動命名；exit code 規則不變（error>0 → 1）。舊路徑 `review_decisions_validation.md` 若有下游引用，保留一個 sprint 的相容輸出或在 summary 開頭註明新檔名。

## 7. 驗收條件（sprint DoD）

- [ ] 上表全部 🆕 規則有實作 + 每條至少一個 should-fail 測試與一個 should-pass 測試
- [ ] 現有 43 個測試全綠（不得為通過而改既有測試的斷言）
- [ ] 三檔輸出對 sample_data/review_decisions_sample.sanitized.csv 實跑產生
- [ ] sample CSV 目前應觸發：IN-1（10 rows 全同建議）注意 sample 只有 10 rows，若 >10 門檻使 IN-1 不觸發，此為預期——用門檻測試單獨驗
- [ ] `test_enum_in_governance_rules_matches_code` 上線（F §7；GOVERNANCE_RULES.md 補 `review_identity_mapping` 列——標注「語意待定」）
- [ ] 對真實 decision CSV（46 rows）重跑：預期新增 warnings（blank reviewer×46、blank reviewed_at×46）與 info IN-1、IN-3——**這是功能正確的證明，不是回歸**

## 8. 明確不做（本 sprint）

- 不自動修任何 CSV 內容（validator 永遠唯讀）
- 不實作 apply（那是 K）
- 不動 alias matching / excel preflight（A 風險 #3 是另一個 sprint）
- 不拆 DocumentMetadata
