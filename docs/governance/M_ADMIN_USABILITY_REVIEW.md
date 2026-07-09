# M. Admin Usability Review

> 視角：實際要填 review CSV、看報告、決定資料能不能對外的行銷/內容管理者（非工程師）。
> 核心發現：目前流程的單點故障是「人工審核成本太高 → 被跳過」（46 筆決策疑似整欄複製、reviewer 全空，見 A 風險 #2）。所有改善優先服務這一點。

---

## 現況痛點

1. **CSV 有 25 欄，其中人要動的只有 5 欄**（review_decision / can_* 覆寫 / reviewer / reviewed_at / notes），但它們埋在第 16–24 欄，前面 15 欄是機器產生的唯讀資訊。Excel 開啟後要橫向捲動，易改錯欄。
2. **review_decision 沒有 dropdown**——13 個合法值靠記憶或另開文件抄，打錯字要等 validation 才發現。
3. **「這筆為什麼需要我審」的資訊在 current_issue 一欄長文字裡**，中文說明混 pipe 分隔代碼（issue_type），對人不友善。
4. **審完不知道自己審得對不對**：validation 報告是工程師取向的 markdown 表（rule code、row number），管理者難以自查銷帳。
5. **不知道整體進度**：多少筆審了、多少筆卡著、哪些欄位缺資料要回 Excel 補——散在三份報告（preview_summary / review_summary / validation）。
6. **「可不可以對外引用」查詢無捷徑**：管理者要人肉對照 record_type + channels + can_quote + restricted_note 四個地方。

## 快速改善（低工作量，J sprint 可順手）

- **produce `.xlsx` review template**（保留 CSV 相容）：用 openpyxl 加 Data Validation dropdown（review_decision 13 值、can_* true/false、final_status enum）+ 凍結前 3 欄 + 人工欄底色标黄。若不想加依賴：退而求其次，在 CSV 首列下加一列「填寫說明行」（validator 跳過 row 2），並提供一份《review CSV 填寫指南》一頁文件，正例反例各一。
- **validation 輸出 errors/warnings CSV**（J §6 已規格化）：管理者可排序銷帳。
- **review_summary 加「待辦視角」節**：按 suggested_action 分組列 row 清單（「這 15 筆缺 handle，去 Excel 補」），而不是只有計數。
- **欄序調整**：人工欄移到最前（source 定位 3 欄 → 人工 5 欄 → 機器資訊欄）。改 `REVIEW_COLUMNS` 順序即可，validator 讀 DictReader 不受欄序影響；需同步 J spec 與既有 CSV 的相容說明。

## 中期改善（apply sprint 前後）

- **status dashboard（單頁 markdown，`reports/excel_preview/dashboard.md`）**：一個命令彙總——各 record_type 總數 / 已審 / 未審 / error / warning / 可進 index / 可對外，加上「下一步該做什麼」一行建議。資料全部來自既有 summary，純彙整，standard-coding 一天工作量。
- **data quality report**：缺 handle、缺 brand、日期解析失敗（GR-8 計數）、channels 全空的清單，按「該回 Excel 補的欄位」分組——直接回答痛點 5。
- **「可對外引用」速查表**：apply-preview 後自動產生 `quotable_records.md`：只列 can_quote_externally=true 的紀錄 + 各自 channels + 更新日期。管理者被業務問「這數字能不能用」時查這張表。
- **audit log（輕量版）**：每次 excel-preview / validate / apply 在 `reports/audit_log.csv` 追加一行（timestamp、command、輸入檔 hash、error/warning counts、操作者）。價值：出事時回溯「哪一版 workbook 產生的決策」。不需要資料庫。

## 長期改善（有真實使用者後）

- **Obsidian preview before sync**：sync 工具實作時，先產生「將新增 / 將修改 / 將刪除」三清單 diff 供人確認——vault 是人家的筆記庫，靜默改動最傷信任。此為 sync sprint 的 hard requirement，先寫在這裡。
- denylist 命中記錄（誰查了 restricted 品牌）進 audit log——L §2 Q2 的配套。
- Web UI / dashboard 自動化：**不建議現在做**（見下）。

## 不建議現在做的功能

| 功能 | 理由 |
| --- | --- |
| Web UI / Streamlit dashboard | 多一整套依賴與攻擊面；markdown dashboard 已滿足單管理者場景 |
| Google Sheets 整合審核 | 資料離機（restricted 名單上雲），違反資料衛生原則 |
| 自動填 review_decision（「AI 建議一鍵接受」） | 正面衝撞 hard constraint 5；suggested_action 已是建議，再降低摩擦只會讓橡皮圖章更嚴重 |
| 即時同步 Obsidian | apply 還沒上線，跳兩步 |
| 通知/提醒系統 | 單人流程用不上 |

## 逐題回答

- **Excel preview 好懂嗎？** 結構好（計數 + 風險清單 + 建議），但混中英文欄位名與 rule codes；快速改善：風險節加一句白話結論（「以下 N 筆建議排除，因為備註提到轉用競品」）。
- **review_template CSV 好填嗎？** 不好填（痛點 1–3），dropdown + 欄序是最高 CP 值修法。
- **review_summary 足夠嗎？** 作為統計夠，作為待辦不夠（快速改善第 3 項）。
- **如何知道哪些資料要補？** 現在要人肉掃 issue_type；中期 data quality report 解決。
- **如何知道可否對外引用？** 現在四處對照；quotable_records.md 解決。
- **需要 dropdown enum 嗎？** 需要，最高優先。
- **需要 status dashboard 嗎？** 需要，markdown 版即可。
- **需要 data quality report 嗎？** 需要（中期）。
- **需要 Obsidian preview before sync 嗎？** 需要，且是未來 sync sprint 的硬性要求。
- **需要 audit log 嗎？** 需要輕量 CSV 版（中期）；完整版等有多使用者再說。
