# F. 維護協議（Maintenance Protocol）

> 對象：未來維護制度文件的弱模型。
> 核心原則：制度文件分三個信任層級，層級決定你能不能改。

---

## 1. 文件修改權限分層

### 層級一：可自行修改（改完在 PR / 回報中說明即可）

- `LESSONS.md` —— 新增教訓（只增不刪；精簡需走層級二）
- `I_GOVERNANCE_RISK_REVIEW.md` 的 risk register —— 新增風險、更新 mitigation 狀態
- `H_ARCHITECTURE_REVIEW.md` / `M_ADMIN_USABILITY_REVIEW.md` —— 更新「已完成」標記、修正過期的檔案路徑
- `ARCHITECTURE.md` 的 CLI 表、模組表 —— 隨 code 同步（這是義務不是選項）
- 各文件中**明顯的事實錯誤**（路徑不存在、行號失效）—— 修正並在 LESSONS.md 記一筆

### 層級二：修改前必須問使用者

- `CLAUDE.md` / `AGENTS.md` 的 hard constraints（10 條）—— 任何增刪改
- `GOVERNANCE_RULES.md` 的規則語意（禁止行為、record_type 角色、decision 定義）
- `ALLOWED_REVIEW_DECISIONS` enum（code 與文件兩側）
- `D_JUDGMENT_RUBRICS.md` 的判準本身（例子可自行補充，判準不可自行改）
- `J` / `K` spec 中標注「需使用者確認」的項目

### 層級三：只能新增 proposal，不得直接覆蓋

- 整份制度文件的重寫或合併
- 新增 hard constraint
- 廢除任何既有防線（即使你認為它多餘）

做法：新建 `docs/governance/proposals/YYYY-MM-DD_<slug>.md`，內容 = 現狀 / 問題 / 提案 / 影響面 / 回滾方式。由使用者核准後才動原文件。

**判斷不確定屬於哪層 → 當作高一層處理。**

## 2. 踩坑教訓的回寫

位置：`docs/governance/LESSONS.md`（若不存在，用下方格式新建）。

寫入時機：(a) 一個 bug 的根因是「文件沒寫或寫錯」；(b) 你或 subagent 誤讀了規則；(c) 重試兩輪才成功的任務（記下最終有效的方法）；(d) 使用者糾正了你的做法。

### 教訓格式（每則）

```markdown
## YYYY-MM-DD <一句話標題>
- 情境：做什麼任務時發生
- 錯誤：具體做錯了什麼（含錯誤輸出或 diff 摘要）
- 根因：為什麼會錯（文件缺陷 / 誤讀 / 假設錯誤）
- 修正：當時怎麼解的
- 制度回饋：哪份文件因此要改（已改 → 標 PR/commit；屬層級二三 → 標「待使用者裁決」）
```

## 3. 精簡週期

- `LESSONS.md` 超過 **30 則**或 **600 行**：做一次合併——同根因的教訓合為一則通則，把已寫回制度文件的教訓移到文末「已制度化」區（保留一行索引）。合併屬層級一，但合併後要請 fresh-context reviewer 確認沒有丟失規則。
- `CLAUDE.md` 超過 **120 行**：強制瘦身。瘦身方向永遠是「往引用檔搬」，不是刪規則。hard constraints 永遠留在主檔。
- 每完成一個 sprint：檢查 H / J / K 是否有「已完成」項目需標記，spec 全部完成後把該 spec 移到 `docs/archive/` 並在路由表移除。

## 4. 避免 CLAUDE.md / AGENTS.md 膨脹的機制

1. **入檔門檻**：新規則想進主檔，必須同時滿足「任何任務都可能踩到」+「違反代價不可回復」（B 文件 §3）。不滿足 → 進對應引用檔。
2. **一進一出審視**：每次想加一條，先檢查現有條目有沒有可以合併或下放的。
3. **禁止貼上長內容**：主檔任何一節超過 15 行就是訊號，抽出去。
4. **路由表是主檔的主體**：主檔的價值在「指路」不在「載內容」。

## 5. review_decision enum 的維護

- Canonical source：`src/marketing_knowledge_agent/review_decision_validation.py` 的 `ALLOWED_REVIEW_DECISIONS`。
- 增刪值的流程（層級二）：使用者核准 → 同一個 PR 內完成：(a) code enum；(b) GOVERNANCE_RULES.md 的定義表（含 Definition + Expected effect）；(c) J spec 的 conflict rules 是否需要新規則；(d) 對照測試（enum 集合 vs 文件表格）更新。
- **已知欠帳**：`review_identity_mapping` 在 code 中存在但 GOVERNANCE_RULES.md 無定義——接手者應優先向使用者確認語意後補齊（見 A 附註、G 信）。

## 6. governance rules 的維護

- 規則變更一律層級二 + 「驗證不自驗」（C §5）：變更者以外的 fresh-context reviewer 或人工二審。
- 每條新禁止行為必須配一個「違反它會被什麼擋住」的答案：validator 規則、測試、或 runtime 檢查，三者至少其一。只有文字沒有執行機制的規則，要在規則旁標注 `(enforcement: none — 文件約束)`，讓讀者知道它靠自覺。
- 新增 record_type：必須同時定義 index role、citation 資格、review template 行為、validator 政策，四者缺一不合併。

## 7. tests 與 docs 的一致性維護

- **對照測試**（建議下個 sprint 加入）：
  - `test_enum_in_governance_rules_matches_code`：parse GOVERNANCE_RULES.md 的 decision 表 ↔ `ALLOWED_REVIEW_DECISIONS` 集合相等。
  - `test_cli_commands_documented`：`build_parser()` 的 subcommands ↔ ARCHITECTURE.md CLI 表。
- **改動綁定規則**：改 `REVIEW_COLUMNS` → 同 PR 更新 J spec 的必填欄位節；改 `AllowedExposureChannel` → 同 PR 更新 GOVERNANCE_RULES.md 與 L 文件的 channel 說明。
- 每次 sprint 收尾跑一次 E 文件模板 9（adversarial review）對本 sprint 改過的文件集。
