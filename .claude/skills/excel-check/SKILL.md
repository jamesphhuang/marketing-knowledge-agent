---
name: excel-check
description: 對一份新的行銷資料庫 Excel(.xlsx)做唯讀結構檢查,確認能安全匯入。每當使用者提供或提到一份新的「MKT 內容產出資料庫 / 店家夥伴案例 / 對外數據」Excel 檔、要「檢查 / 匯入 / 對帳 / 比對」Excel,或準備跑 excel-preview / review-template 前,務必先用這個 skill。它會把 preview 跑到暫存目錄、比對基準計數、檢查 header preflight 與幻影列,並給出「可以進 review-template」或「停下」的判斷。這是每次拿到新月份 Excel 的第一個動作。
---

# excel-check — 新 Excel 匯入前的結構檢查

## 為什麼要有這個 skill

這個專案的核心資料源是一份特定結構的 Excel(五張表:商家夥伴案例、可公開對外數據、待確認數據、不可公開客戶名單、handle 比對)。匯入程式對 sheet 名稱、header 位置、欄名做了精確假設。**行銷團隊改一個欄名或插一列,解析可能靜默錯位**——所以每次拿到新檔,要先確認結構沒變,再進人工審核流程。

還有一個安全理由:直接跑 `mka excel-preview` + `mka review-template` 會**覆蓋** `reports/excel_preview/` 下已經人工填好的 decision CSV(見 `docs/governance/LESSONS.md` 2026-07-10 的教訓)。這個 skill 把檢查跑到**暫存目錄**,不碰 `reports/`,所以檢查再多次都安全。

## 什麼時候用

- 使用者給你一個新的 .xlsx 路徑,說要匯入 / 檢查 / 對帳
- 準備開始新一輪 Excel → preview → review 流程之前
- 想確認「這個月的檔跟上個月結構一不一樣」

## 怎麼做

1. 確認 workbook 路徑。若使用者沒給,問它在哪(常見:Downloads 或 reports/excel_preview/)。
2. 跑檢查腳本(它會自己把 preview 產到暫存目錄、不動 reports/):

   ```bash
   .venv/bin/python .claude/skills/excel-check/scripts/check_excel.py "<workbook.xlsx>"
   ```

   腳本的 exit code:`0` = 結構通過、`1` = 有結構問題該停、`2` = 跑不起來(路徑或環境問題)。

3. 把腳本輸出**如實**轉述給使用者,重點放在:
   - 結構性硬檢查有沒有全過(preflight、0 validation errors、無幻影列)
   - 計數與基準的差異(這是**參考**不是對錯——新月份本來就會有更多列;但差異很大時要提醒「是不是拿錯檔」)
   - 有沒有出現未知 channel 欄(可能代表新渠道,需人工決定要不要擴 enum)

4. 給出明確下一步:
   - **通過** → 可以對 `reports/excel_preview/` 跑正式 `mka review-template`。但**先檢查** `reports/excel_preview/review_decisions_template.csv` 的 `reviewer` 欄:非空代表裡面有人工決策,覆蓋前必須先備份(日期後綴)。
   - **停下** → 不要跑 review-template。判斷是 workbook 改版(要更新 code 的欄名假設或 baseline)還是拿錯檔。**這是人工決策,不要自己猜著改 code 遷就資料。**

## 基準怎麼維護

基準計數存在 `references/baseline.json`(目前是 2026-07-08 正式檔驗證過的 120/33/7/11/91、governance_risk 17)。這些是**參考快照**,不是硬性通過標準。當某個月的新檔成為新基準時,人工把 baseline.json 的數字更新掉——skill 不會自動改,因為「哪一版算基準」是人的決定。

## 邊界

- 這個 skill 只讀不寫(除了自己的暫存目錄)。它不改 code、不動 reports/、不跑 review-template。
- 它不判斷資料「內容」對不對(某商家是不是真的關店)——那是 Excel 那端的人工審核。它只管「結構能不能被程式正確吃進來」。
