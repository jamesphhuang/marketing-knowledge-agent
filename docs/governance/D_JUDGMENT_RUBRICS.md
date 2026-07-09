# D. 判斷力外化 Rubric

> 用法：遇到標題對應的問題時，逐條套判準。每條 rubric 都是「判準 → 正例 → 反例 → 驗收方式 → 失敗時怎麼辦」。
> 所有例子基於本專案真實情境。record_type / 欄位名稱以 `models.py` 為準。

---

## R1. 何時該升級模型

**判準**：滿足任一即升級——(a) 同一子任務已失敗（低階一次 / 中階兩次，見 C §4）；(b) 任務需要設計「之前不存在的規則或抽象」而非套用既有規則；(c) 任務的錯誤代價不可回復（會動到人工填寫的 CSV、會改 governance 語意）。

- **正例**：設計「無 review row 的 merchant case 在 apply 時的預設政策」→ 這是新規則設計，升級。
- **反例**：照 J 文件已寫好的 conflict rule 加一條 validator 檢查 → 規則已存在，standard-coding 做，不升級。
- **驗收**：升級時附上失敗軌跡或「為什麼這是新規則設計」的一句話理由。
- **失敗處理**：無強模型可用 → 拆小任務或標註「需人工」擱置，不硬做。

## R2. 何時算真的完成

**判準**：全部成立才算完成——(a) 驗收條件逐條打勾；(b) pytest 全綠（有改 code）；(c) 相關文件已同步（改了 enum → GOVERNANCE_RULES.md 同步；加了 CLI command → ARCHITECTURE.md 同步）；(d) 產出被非作者驗證過（C §5）。

- **正例**：加了 `reviewer_blank` warning 規則 + 測試先紅後綠 + J 文件勾選該規則已實作 + fresh reviewer 確認報告輸出含新欄位 → 完成。
- **反例**：「code 寫完了，測試應該會過」→ 沒跑測試不算完成。「測試過了」但 GOVERNANCE_RULES.md 還寫著舊 enum → 不算完成。
- **驗收**：完成宣告必須逐條列驗收證據（測試輸出、檔案路徑）。
- **失敗處理**：任一條不成立 → 狀態是「未完成」，如實回報卡在哪，不要宣稱完成。

## R3. 何時該停下來問使用者

**判準**：滿足任一即問——(a) 兩個合理選項會導致**不同的資料外洩面或不同的人工負擔**，且文件沒有裁決依據；(b) 需要修改 hard constraints 或 enum 語意；(c) 涉及商業/公關判斷（某資料「適不適合」對外，而非「能不能」——「能不能」查欄位，「適不適合」問人）。

- **正例**：`review_identity_mapping` 在 code 允許但無文件定義 → 語意裁決屬使用者，問。
- **反例**：validator 的 warning 訊息措辭要用哪個 → 選一個合理的，不問。CSV 欄位順序 → 照現有 REVIEW_COLUMNS，不問。
- **驗收**：問題必須附上你的建議選項與理由（「A 或 B，我建議 A 因為…」），不丟開放題。
- **失敗處理**：使用者不在 → 記入待決清單，先做不依賴此決定的部分。

## R4. 什麼訊號代表方向錯了，該換路而不是重試

**判準**：出現任一訊號就換方法，不要換措辭重試——(a) 第二次嘗試的錯誤與第一次**相同**；(b) 為了讓方案成立，需要越來越多特例（3 個以上 if）；(c) 方案開始要求繞過既有防線（跳過 validation、直接寫正式路徑）。

- **正例**：為了讓某 merchant case 通過 validation，發現要同時改 3 條規則 → 停，問題可能在資料或規則設計，回報而非硬改。
- **反例**：測試失敗因為 fixture 路徑打錯 → 這是同方法內的小修，不算換路訊號。
- **驗收**：換路時寫一句「原方法為何不通」。
- **失敗處理**：找不到替代路 → 走 R1 升級或 R3 問人。

## R5. 品質底線怎麼驗

**判準**：每類產出有最低驗法——code：測試 + 至少一次真實輸入實跑；validator 規則：一個 should-pass 和一個 should-fail 案例；文件：fresh-context read-back；資料輸出：計數 sanity check + 抽樣 3 筆對照來源。

- **正例**：改了 `normalize_date` → 加 Excel serial 測試 + 拿 sample workbook 實跑 excel-preview 確認 `metric_updated_date` 有值。
- **反例**：「diff 看起來正確」、「邏輯上應該沒問題」→ 不是驗證。
- **驗收**：回報中能指出「用了哪個輸入驗的、看到什麼輸出」。
- **失敗處理**：沒有可用的真實輸入 → 用 tests/fixtures.py 的合成資料，並標註「未經真實資料驗證」。

## R6. 何時可以自動處理（不經人工）

**判準**：全部成立才可自動——(a) 操作是唯讀或只寫入 `reports/` preview 區；(b) 規則在文件中有明確判定條件；(c) 錯了可以整批重跑覆蓋（冪等）。

- **正例**：重新產生 excel-preview、review-template、跑 validation、產生報告 → 自動可。
- **反例**：填 review_decision、判定 suspected_duplicate 是否真重複、決定某品牌是否 restricted → 永遠人工。
- **驗收**：自動操作前自問「這一步寫到哪裡？」，答案不在 preview/報告區就停。
- **失敗處理**：不確定是否冪等 → 當作不可自動，問人。

## R7. 何時必須人工 review

**判準**：滿足任一即必須人工——(a) 決定任何紀錄的 review_decision；(b) restricted denylist 的增刪（含 alias）；(c) 判定 suspected_duplicate 的去留；(d) 任何要進入「apply 之後階段」的批次（人工確認是 apply → sync 之間的必經步驟）；(e) governance 規則或 enum 的語意變更。

- **正例**：alias 誤傷清單整理好了，要把某 alias 從 denylist 移除 → 人工簽核後才改。
- **反例**：把 validation 報告從 1 個 md 拆成 md+csv → 純輸出格式，不需人工審核內容（但走正常 code review）。
- **驗收**：人工 review 的證據 = decision CSV 的 `reviewer` + `reviewed_at` 有值，或使用者在對話中明確說「核准」。
- **失敗處理**：拿不到人工確認 → 該批次停在 preview 狀態，這是正常狀態不是失敗。

## R8. 何時只能產生 preview

**判準**：只要操作物件是「將來會進 vault / index / 對外」的資料，且尚未經過「validation 0 errors + 人工確認」兩關，就只能 preview。目前階段（Apply Sprint 未完成）：**一律只能 preview**。

- **正例**：apply-review-decisions 產出 `approved_vault_preview/` → 對。
- **反例**：「validation 過了，直接把 approved 記錄寫進 Obsidian vault 省一步」→ 違反 hard constraint 7。
- **驗收**：檢查輸出路徑，全部在 `reports/` 下。
- **失敗處理**：發現自己寫了 preview 區以外的路徑 → 撤銷改動，回報。

## R9. 何時可以 apply decision

**判準**：全部成立——(a) `mka validate-review-decisions` 0 errors；(b) `reviewer` 與 `reviewed_at` 非空（目前 46 筆全空 = **不可 apply**）；(c) decision CSV 與當前 preview JSON 的 row coverage 一致（validator 的 missing/unexpected row 檢查通過）；(d) apply 的產出仍是 preview（見 R8）。

- **正例**：validation 報告顯示 0 errors、reviewer 欄有名字、reviewed_at 有日期 → 可跑 apply-preview。
- **反例**：0 errors 但 reviewer 全空 → 不可。有 3 個 warnings 未看過 → 先逐條看 warnings 再決定。
- **驗收**：apply-preview 的 summary 必須記錄它驗過這些前置條件。
- **失敗處理**：條件不齊 → 回報缺哪項，等人補。

## R10. 何時可以進 content index

**判準**（對單筆紀錄）：(a) record_type ∈ {content_asset, merchant_case, public_metric}；(b) 人工 review_decision ∈ {approve, approve_internal_only, keep_all_records, restricted_use_only}（後兩者仍受各自限制）；(c) `can_enter_content_index=true`；(d) 非 `no_valid_content_asset`；(e) public_metric 另需 `allowed_exposure_channels` 非空。restricted_customer / handle_mapping / pending_metric **永遠不進**。

- **正例**：merchant_case、decision=approve、有有效文章素材 → 可進。
- **反例**：public_metric、decision=approve、但 channels 為空 → 不可進（缺 e）。merchant_case 沒有 review row → 不可進（缺 b，沒被審過 = 沒過）。
- **驗收**：進 index 的每筆都能回溯到一列 decision CSV。
- **失敗處理**：條件衝突（如 decision=approve 但 can_enter_content_index=false）→ 這是 validation 該擋的，回頭報 validator 缺規則。

## R11. 何時可以對外引用

**判準**（對單筆已在 index 的紀錄，逐條檢查，任一不過即不可）：(a) `can_quote_externally=true`；(b) `data_classification=public`；(c) status=published；(d) 若是 public_metric：目標渠道 ∈ `allowed_exposure_channels`；(e) 無未解決的 governance warning（merchant risk terms、restricted_note）；(f) freshness：`effective_date` 距今 > 540 天 → 需人工重新確認後才可用。欄位缺失（如 last_reviewed 為空且其他日期也缺）→ 降級為 internal-only 或要求人工確認。

- **正例**：public_metric、channels 含 press_release、metric_updated_date 三個月前 → 可用於新聞稿。
- **反例**：同一筆但使用者要放廣告、channels 不含 ads → 不可，即使 can_quote_externally=true。merchant_case 備註含「已關店」→ 不可，需人工。
- **驗收**：對外引用的回答必須逐條列出它檢查了哪些欄位（citation 附 metadata）。
- **失敗處理**：任一欄位缺失或矛盾 → 回答降級為 internal-only 並說明缺哪個欄位，不要猜。

## R12. 何時必須阻擋 citation

**判準**：任一成立即整筆 citation 移除（不是加 warning 而已）——(a) record_type ∈ {restricted_customer, handle_mapping}；(b) citation 的 title / 內文命中 restricted denylist；(c) 外部用途的回答中出現 pending_metric 或 `can_quote_externally=false` 的紀錄。內部用途（明確標示 internal）時 (c) 可改為保留 + 醒目 warning。

- **正例**：答案引用的 blog 標題含 restricted 品牌名 → 該 citation 移除，答案加「已依 denylist 移除一筆來源」。
- **反例**：merchant_case 有 governance risk（已關店）但使用者做內部盤點 → 保留 + warning，不移除（這是 warning 場景不是 block 場景）。
- **驗收**：test_excel_governance 類測試覆蓋 block 與 warning 兩種路徑。
- **失敗處理**：不確定該 block 還是 warning → 選 block（保守方向永遠是少給），並回報該案例讓人裁決。

---

## Harness 極限聲明（誠實條款）

以下判斷**不能**靠拆解、驗證、多樣本評審補足，遇到就按指示處理：

| 判斷類型 | 為什麼補不了 | 處理 |
| --- | --- | --- |
| 某措辭對外是否有公關風險 | 需要公司當下的公關語境，不在任何檔案裡 | 人工審核 |
| restricted_note 的模糊限制怎麼解讀（如「盡量不要提市場細節」） | 語意邊界是商業判斷 | 保守解讀（當作禁止）+ 標註給人工放寬 |
| 兩筆相似 merchant case 是否真的同一次訪談 | 需要當事人記憶 | 標記 suspected_duplicate，永遠人工 |
| 品牌名是否該進 denylist | 商業關係判斷 | 人工 |
| 「這個回答品質好不好」的品味層 | 可用 rubric 驗底線，但上限是品味 | 驗底線即可，不要追求品味層自動化 |
