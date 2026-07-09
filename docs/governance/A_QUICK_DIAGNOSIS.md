# A. 快速診斷：Top 3 系統風險

> 依據：fable5_review_pack（2026-07-08 版）完整閱讀，含全部 src_snapshot、tests_snapshot、sample_data、reports_snapshot。
> 本文件是後續所有制度文件（B–M、Z）的引用基礎。
> 讀者：未來維護此專案的中小型模型（Sonnet / Haiku / GPT-5.5 等級）。

---

## 風險 #1：Governance 防線只存在於「呼叫者記得開啟」的路徑上（誤引用資料｜弱模型做錯決策）

### 問題描述

restricted customer 的答案層防護（denylist 檢查 + redaction）實際上在所有 CLI 路徑都是關閉的：

- `pipeline.agent_ask()` 的 `governance_index` 參數預設 `None`，而 `cli.py` 呼叫 `agent_ask(...)` 時**沒有傳入 governance_index**（見 `cli.py` 的 `agent-ask` 分支）。`apply_governance_to_answer(answer, None)` 直接原樣返回。
- `ask_index()`（`mka ask`）**根本沒有** governance 檢查——連參數都沒有。
- `apply_governance_to_answer` 只 redact `answer.answer` 文字，**不 redact `answer.citations`**——citation 的 `title`、`source_path` 仍可能含 restricted 品牌名。
- `generate_answer` 對 `can_quote_externally=false`、`pending_metric`、非 public status 只產生 **warning 文字**，不阻擋、不降級。外部引用保護完全依賴呼叫者自己記得帶 `--can-quote-externally` filter。

record_type 層的防護（`NON_RETRIEVABLE_RECORD_TYPES` 過濾 `restricted_customer` / `handle_mapping`）是有效的，但它只擋「restricted_customer 這種 record 本身進 index」，擋不住 **restricted 品牌名出現在 content_asset / merchant_case 的內文裡**被引用出來。

### 為什麼會發生

Governance 被設計成「可選的裝飾層」（optional 參數、事後檢查），而不是「預設開啟、必須明確關閉」的管線層。每個新入口（未來的 MCP、Web UI、外部 LLM）都要重新記得接上它——弱模型一定會忘。

### 會造成什麼後果

- 未來任何人執行 `mka ask` / `mka agent-ask`，若 vault 內文提到 restricted 品牌，答案與 citation 會原樣輸出。
- 接上真 LLM 後，這變成對外公關風險：不可公開客戶名稱進入生成內容。
- 弱模型看到「有 GovernanceIndex 這個 class」會誤以為防線已生效。

### 具體修法（依序，皆為小 patch）

1. `ask_index` / `agent_ask` / 未來所有 answer 出口，**一律**在回傳前經過 `apply_governance_to_answer`。governance_index 改為在 pipeline 內部載入（例如從固定路徑 `reports/excel_preview/restricted_customers.json` 或未來 governance table），找不到檔案時輸出明確 warning：「restricted denylist 未載入，答案未經 denylist 檢查」。
2. `apply_governance_to_answer` 擴充：對每一筆 citation 的 `title` / `source_path` 跑同樣的 `check_text`；命中則整筆 citation 移除並加 warning（不是 redact title——citation 指向的來源本身就不該出現）。
3. 在 `GeneratedAnswer` 增加 `governance_checked: bool` 欄位，出口強制檢查；`False` 時 CLI 印出醒目警告。
4. 外部引用場景（未來）：generation 前先依 `can_quote_externally=true` + `exposure_channel` 過濾，而不是生成後警告。詳見 L 文件。

### 應新增的測試

- `test_cli_ask_blocks_restricted_brand_in_content`: vault 放一篇提到 restricted 品牌的 content_asset，跑 CLI `ask`，斷言答案被 redact 且有 warning。
- `test_citation_removed_when_title_hits_denylist`: citation title 含 restricted 品牌 → citation 被移除。
- `test_answer_has_governance_checked_flag`: 所有出口的 answer `governance_checked=True`。

### 應新增的文件規則（進 B 文件 hard constraints）

- 「任何回傳 `GeneratedAnswer` 的新程式路徑，必須呼叫 `apply_governance_to_answer`，且不得以 `governance_index=None` 靜默跳過；缺 denylist 必須產生可見 warning。」

### 弱模型判斷口訣

> **「答案出門前，必過 denylist；citation 命中就整筆刪；沒載入 denylist 要說出來。」**

---

## 風險 #2：suggested_action 橡皮圖章 + review enum 無單一事實來源（出錯｜讓管理者難以維護）

### 問題描述

三件事互相疊加：

1. **橡皮圖章已實際發生**：現有 46 筆 review rows 中，`review_decision` 的分佈與 `suggested_action` 的分佈**完全一致**（逐類別數字相同），且 `reviewer`、`reviewed_at` 46 筆全空。這強烈顯示 review_decision 是整欄複製 suggested_action，並非逐筆人工判斷。GOVERNANCE_RULES.md 明定「suggested_action 不得視為 final decision」，但 validator 對此**不擋不警告**（reviewer 空白只被計數，不產生 issue）。
2. **enum 已漂移**：`review_decision_validation.py` 的 `ALLOWED_REVIEW_DECISIONS` 有 13 個值（含 `review_identity_mapping`），GOVERNANCE_RULES.md 的表只有 12 個（沒有 `review_identity_mapping`）。docs 和 code 已經不同步，未來弱模型不知道該信哪邊。
3. **沒有 review row 的紀錄命運未定義**：`_merchant_review_rows` 只為「有 issue」的紀錄產生 review row。120 筆 merchant_cases 只有 24 筆進 review CSV。剩下 96 筆在 apply 階段的預設命運（自動進 vault preview？還是需要另一輪確認？）目前**沒有任何文件定義**。弱模型實作 apply 時最可能犯的錯就是把「沒被 review」當成「已核准」。

### 為什麼會發生

- review workflow 對人的成本很高（46 列 × 25 欄 CSV），人自然會整欄複製。
- enum 同時活在 code、GOVERNANCE_RULES.md、review CSV 三個地方，沒有指定 canonical source。
- 「有 issue 才進 review」是合理的降噪設計，但沒有補上「無 issue 紀錄的預設政策」這半邊。

### 會造成什麼後果

- Apply sprint 一旦實作，未經真人審核的決策會被當成已審核批次套用——整個 human-in-the-loop 設計被架空。
- enum 漂移導致：弱模型照文件驗證會誤報 `review_identity_mapping` 為非法；照 code 寫文件又會複製漂移。
- 96 筆無 review row 的 merchant case 可能被靜默全量放進 vault。

### 具體修法

1. **Validator 加規則**（見 J 文件完整規格）：
   - `reviewer` 或 `reviewed_at` 空白 → **warning**（validation 階段）；但 apply-preview 的前置檢查中升級為 **error**（沒有具名審核人不得 apply）。
   - 新增 summary 欄位 `review_decision_equals_suggested_action_count`；若 = total_rows 且 total_rows > 10 → **warning**：「所有決策與建議相同，請確認確實逐筆審核」。
2. **Enum 單一事實來源**：以 code 的 `ALLOWED_REVIEW_DECISIONS` 為 canonical，新增測試把 GOVERNANCE_RULES.md 的表與 code 對照（parse markdown 表格比對集合），不一致即測試失敗。`review_identity_mapping` 需補進 GOVERNANCE_RULES.md 並補定義（目前 code 允許但無語意定義——這是待使用者確認事項）。
3. **定義無 review row 紀錄的政策**（進 K 文件）：apply-preview 必須把「preview 中存在但 review CSV 中不存在」的紀錄輸出到獨立清單 `not_reviewed_records.md`，預設**不進** approved vault preview；只有 `--include-clean-records` 明確旗標 + 摘要中醒目標示，才以「clean 預設核准」進入，且逐筆標注 `approved_by=default_policy`。

### 應新增的測試

- `test_validation_warns_when_all_decisions_equal_suggestions`
- `test_enum_in_governance_rules_matches_code`（docs/code 對照）
- `test_apply_preview_excludes_unreviewed_records_by_default`（apply sprint 時）
- `test_apply_precheck_errors_on_blank_reviewer`

### 應新增的文件規則

- 「review_decision enum 的 canonical source 是 `review_decision_validation.ALLOWED_REVIEW_DECISIONS`；改 enum 必須同一 PR 內同步 GOVERNANCE_RULES.md 並通過對照測試。」
- 「沒有 review row ≠ 已核准。任何 apply 邏輯遇到無決策紀錄，一律隔離到 not_reviewed 清單。」

### 弱模型判斷口訣

> **「建議不是決定；沒人簽名不能 apply；沒被審過的資料當作沒過。」**

---

## 風險 #3：Excel 解析與 alias 匹配建立在脆弱假設上，且會靜默失敗（出錯｜誤引用｜浪費 token）

### 問題描述

1. **Hardcoded 結構假設**：sheet 名稱（`商家夥伴案例資料庫` 等 5 個中文名）、header row 位置（6/4/6/3/1）、欄名（`商家 / 夥伴名稱`、`官網/ 招募網站` 含特定空格）全部寫死。行銷團隊改一個 sheet 名或在上方插一列，解析就錯位——而且**可能不報錯，只是資料錯位或整批消失**。
2. **Excel 日期 serial 未處理**：`_cell_value` 對數值型 cell 回傳原始字串（如 `45123`），`normalize_date` 只認 `%Y-%m-%d` 等文字格式 → Excel 原生日期格式的 `更新時間` 會**靜默變成 None** → public metric 失去 `last_reviewed` → freshness 判斷失真。
3. **Alias 子字串匹配誤傷**：`GovernanceIndex.check_text` 用 `term in normalized_text` 子字串匹配。normalize 後全小寫去空白，短別名（CURRENT_STATUS 已知風險提到 `HR`）會命中無關內容（`hrtech`、`shrimp`…），造成誤 redact / 誤 block。反向風險：`split_restricted_aliases` 從括號拆別名，若品牌名含說明性括號（如「XX（已結束）」），「已結束」會變成 denylist 別名，大量誤傷。
4. **Merged cells**：xlsx 中合併儲存格只有左上格有值，目前僅 `類型`/`指標` 兩欄有 fill-down；merchant sheet 若有合併格會靜默缺值。
5. （token 面）`retrieval.search` 每次查詢把**全部 chunks 載入記憶體**逐一過濾；未來資料量上去後，任何用 subagent 實跑檢索的 session 都會慢且貴。現階段可接受，需標記天花板。

### 為什麼會發生

Prototype 對「一份特定的 workbook」做了精準逆向工程，這是正確的第一步，但假設沒有被固化成「開頭先驗證、不符就大聲失敗」的 preflight 檢查。

### 會造成什麼後果

- Workbook 改版後 preview 靜默產出錯誤資料 → 人工 review 的對象本身就是錯的 → 下游全錯。
- 日期靜默丟失 → freshness warning 失效 → 過期數據被當新資料引用。
- Denylist 誤傷 → 正常答案被 redact，管理者失去信任後傾向關掉 governance（最危險的二階效應）。

### 具體修法

1. **Preflight 驗證**：excel-preview 開頭比對「每個 sheet 的 header row 實際內容」與預期欄名清單，不符即 fail-fast 並列出 diff（預期 vs 實際）。欄名清單抽成常數表，成為唯一要維護的點。
2. **日期 serial 支援**：`normalize_date` 增加純數字分支（Excel epoch 1899-12-30 + serial days），並加測試。同時在 preview summary 增加 `metric_updated_date_missing_count`，讓日期解析失敗變成可見訊號。
3. **Alias 匹配收緊**：
   - 別名長度 < 4 個字元（或純英文 ≤ 3 字母）→ 不做子字串匹配，只做整詞匹配（前後為非字母數字）。
   - `split_restricted_aliases` 產出的別名進入 denylist 前，輸出到 preview 的 `restricted_customers.json` 讓人工看得到（目前已輸出 aliases，保持），並在 review template 的 restricted 列顯示「將被用於匹配的 alias 清單」。
   - 新增誤傷測試集：一組「不應命中」的句子清單（含 hrtech 類案例）跑 `check_text` 斷言不 block。
4. **Merged cell**：至少在 preflight 偵測 sheet XML 中的 `<mergeCells>`，若 merchant sheet 有合併格 → warning 列入 preview summary。

### 應新增的測試

- `test_excel_preview_fails_fast_on_header_mismatch`
- `test_normalize_date_parses_excel_serial`
- `test_short_alias_requires_word_boundary`（誤傷保護）
- `test_parenthetical_status_words_not_treated_as_alias`
- `test_preview_warns_on_merged_cells`

### 應新增的文件規則

- 「excel-preview 遇到 header 不符必須 fail-fast，禁止猜測欄位對應。修 workbook 結構問題屬於人工決策，模型只能回報 diff。」

### 弱模型判斷口訣

> **「解析不到就大聲死，不要猜；日期解不出來要計數；短別名不做子字串匹配。」**

---

## 附註：其他已確認但未進 Top 3 的問題（供 H/I/J/L 引用）

| # | 問題 | 嚴重度 | 詳見 |
| --- | --- | --- | --- |
| a | `models.DocumentMetadata` 是 6 種 record_type 共用的 god-model（445 行，60+ 欄位） | 中 | H |
| b | Excel 記錄 `publish_date=captured_date`，freshness note 反映的是「抓取日」不是「資料日」 | 中 | L |
| c | `retrieval.search` 中 `if score > 0 or not filters.is_empty()`：帶 filter 時零分 chunks 也回傳 | 低 | H |
| d | `tests/test_retrieval.py` 不存在（pack manifest 已註明）；freshness、reranking 無測試 | 中 | H/J |
| e | Pydantic v1 `@validator` deprecation（升 v2 是機械工作，適合弱模型批次做） | 低 | H |
| f | `agentic.py` 關鍵詞路由（`比較`、`最新`…）脆弱，但因它只包裝既有 retrieval/generation、不繞過 governance，現階段風險可接受 | 低 | H |
| g | review CSV 25 欄對人工太寬（管理者易填錯欄） | 中 | M |
| h | `can_enter_vault` 與 `can_enter_content_index` 的語意差異沒有文件定義 | 中 | K（本包給出定義，需使用者確認） |

## 缺料聲明

以下資料不在 pack 內，影響判斷之處已在對應文件標註保守結論：

- **原始 workbook 與真實 restricted 名單**：無法驗證 alias 誤傷的實際發生率，只能給規則性防護（風險 #3）。
- **`review_identity_mapping` 的語意定義**：code 允許此值但任何文件皆無定義。J 文件暫按「handle mapping 身分待確認」處理，**需使用者確認**。
- **未來對外引用的實際使用場景**（誰查詢、產出投放到哪）：L 文件的 channel gating 以 `AllowedExposureChannel` 七值為準，若實際渠道更多需擴充 enum。
- **Obsidian vault 的實際結構**：K 文件的 vault preview 目錄結構是建議值，正式 sync 前需對照真實 vault。
