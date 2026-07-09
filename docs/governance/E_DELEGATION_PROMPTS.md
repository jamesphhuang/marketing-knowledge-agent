# E. 派工 Prompt 模板

> 用法：主模型派工時複製對應模板，填入 `{...}` 佔位。每個模板已內建該任務類型的 Required checks 與 Stop conditions，不要刪。
> 通用規則見 C 文件 §2（派工三件套）。模型等級選擇見 C §3。

---

## 模板 1：搜尋 / repo 掃描任務（等級：low-cost 或 Explore 型 agent）

```text
Task goal: 在 repo 中找出 {目標，例如：所有建構 GeneratedAnswer 並回傳的位置}。
Context: {為什麼要找，例如：要確認每個答案出口都接了 apply_governance_to_answer}。
Non-goals: 不修改任何檔案；不評論程式碼品質；不展開與目標無關的檔案內容。
Inputs: repo 根目錄 {path}；起點提示：{已知相關檔案}。
Required checks:
- 對每個命中點記錄 file:line 與一行摘要
- 用至少兩種搜尋詞交叉（符號名 + 字串特徵），避免漏抓
Acceptance criteria:
- 回報涵蓋 src/ 全部 .py 檔
- 每個命中點可點擊定位
Output format: 表格（file:line | 一行摘要 | 是否符合 {判定條件}）+ 「可能漏抓的角落」一節。
Stop conditions: 搜尋超過 15 分鐘或命中超過 50 處 → 停止並回報目前清單與縮小範圍建議。
```

## 模板 2：實作任務（等級：standard-coding）

```text
Task goal: 實作 {功能，引用 spec 章節，例如：J 文件 §conflict-rules 的 CR-3}。
Context: {上游動機一句話}。Spec 是唯一需求來源；spec 沒寫的行為不要自行發明，記入回報的 open questions。
Non-goals: 不重構無關程式碼；不改 spec；不動 reports/ 下人工填寫的檔案。
Inputs: spec: {path}；主要檔案: {paths}；現有測試: {test paths}。
Required checks:
- 先跑 pytest 確認基線全綠，結束再跑一次
- 新行為必須有新測試（先紅後綠）
- 若改動 enum / 規則語意 → 停止，這超出實作任務範圍
Acceptance criteria:
- [ ] spec 中 {清單} 逐項實作
- [ ] pytest 全綠，新增測試 {n} 個以上
- [ ] 無 B 文件 hard constraints 違反
Output format: 結論 3 句 / 改動檔案清單 / 驗收逐條打勾 / open questions。
Stop conditions: 同一測試失敗修兩次仍紅 → 停止，回報完整失敗軌跡（prompt、diff、錯誤訊息）。
```

## 模板 3：重構任務（等級：standard-coding）

```text
Task goal: 重構 {目標，例如：把 DocumentMetadata 按 record_type 拆成子模型}，行為不變。
Context: {引用 H 文件對應建議}。
Non-goals: 不改任何外部可見行為；不順手修 bug（發現 bug 記入回報，另開任務）；不改公開 API 簽名除非 spec 明列。
Inputs: {paths}；行為基準 = 現有全部測試 + {額外基準，如：對 sample workbook 跑 excel-preview 的輸出 diff 為空}。
Required checks:
- 重構前先確認測試覆蓋足夠：列出重構範圍內無測試覆蓋的函式，覆蓋不足先補「行為快照測試」再動手
- 每個階段保持測試綠（小步提交）
Acceptance criteria:
- [ ] pytest 全綠
- [ ] {行為基準} diff 為空
- [ ] 無新增公開 API
Output format: 結論 / 拆分結構圖（縮排列表）/ 驗收打勾 / 發現但未修的 bug 清單。
Stop conditions: 發現行為不可能完全保持（隱藏耦合）→ 停止回報，不要「順便」改行為。
```

## 模板 4：研究任務（等級：standard-coding；需要外部資訊時允許 WebSearch）

```text
Task goal: 研究 {問題，例如：Pydantic v1 validator → v2 field_validator 的遷移地雷}。
Context: {為什麼現在需要}。
Non-goals: 不改 code；不下最終決策（產出的是選項與建議，決策在主對話）。
Inputs: {相關檔案}；{已知限制，例如：本專案 Python 版本、離線限制}。
Required checks:
- 每個結論標注來源（官方文件 / code 實測 / 推測），推測必須明標
- 給出「不做」的成本，不只給「做」的好處
Acceptance criteria:
- [ ] 至少 2 個可行方案 + 各自風險
- [ ] 明確建議一個，附理由
Output format: 問題重述 / 方案表（方案 | 工作量 | 風險 | 適用等級）/ 建議 / 來源清單。
Stop conditions: 需要存取不可得資源（生產資料、付費文件）→ 停止並回報缺口。
```

## 模板 5：審查任務（等級：fresh-context-reviewer；不得由原作者執行）

```text
Task goal: 審查 {diff / PR / 文件}，找 correctness 與 governance 違規。
Context: 你沒有參與撰寫，這是刻意的。不要向作者要背景，用文件與 code 自行判斷——你讀不懂的地方本身就是 finding。
Non-goals: 不改 code；不評風格；不重新設計。
Inputs: {diff 或檔案}；判準: B 文件 hard constraints + D 文件相關 rubric + {spec}。
Required checks:
- 逐條檢查 hard constraints 10 條，每條寫「通過/違反/不適用」
- 對每個 finding 給出具體失敗場景（什麼輸入 → 什麼錯誤結果）
- 檢查測試是否真的驗證了宣稱的行為（測試斷言 assert 什麼）
Acceptance criteria:
- [ ] hard constraints 逐條有結論
- [ ] 每個 finding 有 file:line 與失敗場景
Output format: findings 按嚴重度排序（blocker / major / minor）/ hard constraints 檢查表 / 「我讀不懂的地方」清單。
Stop conditions: 發現 blocker 級 governance 違規 → 立即回報，不必等審完全部。
```

## 模板 6：測試補強任務（等級：standard-coding）

```text
Task goal: 為 {模組 / 行為清單} 補測試。
Context: {引用 A/H/J 文件的缺測試清單}。
Non-goals: 不改被測程式（發現 bug → 寫一個 xfail 或 skip 測試標記它 + 記入回報，不要修）。
Inputs: {模組 paths}；現有測試風格參考: {test path}；fixtures: tests/fixtures.py。
Required checks:
- 每個測試名描述行為不描述實作（test_short_alias_requires_word_boundary ✓；test_check_text_2 ✗）
- 每個測試至少一個 should-pass 與一個 should-fail 方向
- governance 類測試必須斷言「錯誤方向被擋」，不能只斷言「正確方向通過」
Acceptance criteria:
- [ ] {行為清單} 每項至少一個測試
- [ ] pytest 全綠（xfail 除外）
Output format: 測試清單（測試名 | 覆蓋的行為 | pass/xfail）/ 發現的 bug 清單。
Stop conditions: 被測行為的預期結果在文件中找不到定義 → 停止，列出未定義行為清單。
```

## 模板 7：governance validation 任務（等級：standard-coding；驗收必須 fresh-context）

```text
Task goal: 對 {decision CSV path} 執行完整 validation 並解讀結果。
Context: {為什麼跑，例如：apply-preview 前置檢查}。
Non-goals: 不修改 decision CSV（它是人工產物，唯讀）；不「修正」資料讓 validation 通過。
Inputs: decision CSV: {path}；preview dir: {path}；命令: mka validate-review-decisions --decisions {path} --preview-dir {path} --output {report path}。
Required checks:
- error_count / warning_count / blank_reviewer_count / blank_reviewed_at_count 逐項報告
- 每個 error 附 row 定位與規則名
- 檢查 review_decision 分佈是否與 suggested_action 完全一致（橡皮圖章訊號，見 A 風險 #2）
Acceptance criteria:
- [ ] 報告檔案已產生
- [ ] 「可否進入 apply-preview」有明確 yes/no 結論與依據（判準：D 文件 R9）
Output format: 結論（可/不可 apply + 理由）/ issue 摘要表 / 需要人工處理的清單。
Stop conditions: CSV 或 preview 檔缺失 → 停止回報，不要自行生成替代檔案。
```

## 模板 8：apply-review-decisions preview 任務（等級：standard-coding；本任務僅在 K spec 實作完成後可用）

```text
Task goal: 執行 apply-review-decisions 產生 preview 輸出。
Context: validation 已通過（附報告 path），人工確認 reviewer/reviewed_at 已齊。
Non-goals: 【hard constraints】不寫入 Obsidian vault；不建正式 content index；不刪改 decision CSV 與 preview JSON；輸出只能在 reports/ 下。
Inputs: decision CSV: {path}；preview dir: {path}；輸出目錄: reports/excel_preview/apply_preview/。
Required checks:
- 前置：validation 0 errors、blank_reviewer_count=0、blank_reviewed_at_count=0——任一不符立即停止
- 輸出後做守恆檢查：每筆 preview 紀錄必須恰好出現在一個輸出桶（approved / governance / excluded / internal / not_reviewed），總數守恆
- restricted_customer 只出現在 governance_table_preview 與 denylist，不出現在任何 vault preview
Acceptance criteria:
- [ ] apply_decisions_summary.md 產生且含守恆檢查結果
- [ ] 0 筆紀錄寫入 reports/ 以外
Output format: summary 摘要 / 各桶計數表 / 異常清單。
Stop conditions: 任何一筆紀錄無法歸桶（decision 與欄位矛盾）→ 停止，整批不產出，回報矛盾清單。
```

## 模板 9：fresh-context adversarial review 任務（等級：fresh-context-reviewer）

```text
Task goal: 對 {文件集 / 制度變更} 做對抗式審查：假設撰寫者會犯錯，主動找漏洞。
Context: 你是本次變更的第一個「不知情讀者」。你的困惑就是產出。
Non-goals: 不修文件；不補寫缺的內容（指出缺即可）。
Inputs: {文件 paths}；對照基準: B 文件 hard constraints、GOVERNANCE_RULES.md。
Required checks（逐項回答）:
- 規則之間是否互相矛盾？（引用兩處原文對照）
- 檔案路徑 / 工具名 / 命令是否真實存在？（逐一實際檢查，不憑印象）
- 哪些指令弱模型會誤讀？（給出誤讀後的錯誤行為）
- 是否有任何內容暗示可以：跳過 manual review / 直接 sync Obsidian / 把 restricted customer 放進 RAG？
- 哪些規則太抽象、缺例子、缺失敗處理？
Acceptance criteria:
- [ ] 上述五問逐一有答案（沒有問題也要寫「未發現」+ 檢查方法）
Output format: findings 表（位置 | 問題 | 誤讀後果 | 修正建議），按危險度排序。
Stop conditions: 無。此任務必須跑完全部檢查項。
```
