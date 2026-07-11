# I. Governance Risk Review

> 活文件：risk register 隨 mitigation 進度更新（F 層級一）。
> Severity：critical（資料外洩/不可回復）> high（錯誤決策被套用）> medium（品質/信任損傷）> low。
> Likelihood：以「未來 12 個月、由中小型模型維護」為前提評估。

---

## 逐題檢查（對應 review pack 的問題）

### restricted_customer 是否可能誤進 RAG citation？

**record 層：目前安全。** `matches_filters` 開頭硬擋 `NON_RETRIEVABLE_RECORD_TYPES`，且 restricted 紀錄只存在於 preview JSON，從未被 ingest 進 SQLite。測試 `test_restricted_customer_is_governance_table_not_search_citation` 覆蓋。
**內容層：不安全。** 三個殘餘路徑：
1. content_asset / merchant_case 的**內文或標題提到** restricted 品牌 → 檢索照常返回，而答案層 denylist 在 CLI 路徑未接線（A 風險 #1）。
2. `apply_governance_to_answer` 即使接上，也**不檢查 citations**——title 含 restricted 品牌會原樣輸出。
3. 未來 apply 若把 restricted 紀錄誤歸桶（實作 bug），沒有第二道守恆檢查會抓到它 → K spec 已規定守恆檢查。

### pending_metric 是否可能被誤對外引用？

會。pending_metric **可被檢索**（不在 NON_RETRIEVABLE 內，這是刻意的——內部盤點需要），保護僅靠：status=draft warning + `metadata_governance_warnings` 的文字警告。「對外」與「內部」用途目前**無法在查詢中區分**（H-5），所以系統無從阻擋。現階段可接受（使用者是內部人+mock generator），接外部 LLM 前必須實作 L 文件的 intent gating。

### public_metric missing_allowed_exposure_channels 是否會被阻擋？

部分。ingestion 時正確設定 `can_quote_externally=false` + `missing_allowed_exposure_channels=true`，review template 建議 needs_update，validator 強制 `can_enter_content_index=false`。但同 pending_metric——runtime 檢索側只有 warning 沒有 block。閉環要等 L 的 channel gating。

### restricted_note 是否需要結構化？

需要，但不是現在。目前 `normalize_public_metric_restricted_note` 只認 3 個關鍵詞（不可公開/僅用於口頭說明/不留文字紀錄），其中「僅用於口頭說明」有結構化效果（強制 channels=[verbal_briefing]）。其他措辭（如「不要提市場細節」）只作為文字保留。
**建議**：維持自由文字 + 新增一個結構化欄位 `restricted_note_type: Literal["verbal_only", "no_written_record", "no_market_detail", "other"]`，由關鍵詞規則初填、人工在 review 時修正。**"other" 一律當作最嚴格處理（不可對外）**——弱模型解讀模糊限制的規則是保守優先（D 文件 harness 極限表）。

### allowed_exposure_channels 是否足夠？

七值 enum 對應 workbook 的七個欄位，目前足夠。缺口：(a) 無「社群貼文」與「合作夥伴轉載」這類常見渠道——若業務出現，走 F 層級二加值；(b) `_unknown_exposure_channel_columns` 已能偵測 workbook 新增欄位並列入 summary——這個設計好，保留；(c) channel 與 citation 的連動缺失（H-6）。

### same_brand / same_handle multiple records 是否容易被誤當 duplicate？

規則層防護已足：issue_type 標記為資訊性、validator 對「僅有多筆標記卻被 exclude」發 warning、summary 明文「not duplicate errors」。殘餘風險是**人**在 CSV 裡看到 4 筆同品牌手滑填 exclude——validator 只給 warning 不給 error，這是對的（人有權真的排除），但 apply summary 應把這類 row 單獨列出讓人再看一眼（已寫入 K）。

### review_decision 是否容易和 can_enter_* 欄位衝突？

容易，這是目前 validator 最大缺口。現有 per-type 規則只覆蓋 restricted_customer 與 pending_metric（全欄位鎖死），merchant_case / public_metric 的 decision 與布林欄位間**幾乎沒有交叉檢查**（只有 issue_type 觸發的兩條）。例如 `exclude` + `can_enter_content_index=true` 目前**不會報錯**。完整規則矩陣見 J spec——這就是下個 sprint。

### apply-review-decisions 應如何避免危險套用？

五道防線（詳見 K）：前置檢查（validation 0 error + reviewer 非空）→ 輸出僅 preview 區 → 每筆單桶歸屬+總數守恆 → restricted/pending 桶的白名單斷言 → summary 供人工最終確認。加上 hard constraint 7 的制度層。

---

## Governance Risk Register

| ID | 風險 | Severity | Likelihood | Mitigation | Required validation rule | Required test | 狀態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GR-1 | restricted 品牌名經 content 內文進入答案/citation（denylist 未接線或只移除 citation、仍留下答案本文 snippet） | critical | high | 已接上 ask / agent-ask denylist；新增 `governance_checked`；身分欄位命中（title/source_path/brand_name/merchant_handle）→ 生成前丟棄；正文命中 → 事後遮名；citation title/source_path 二次掃描——「身分丟棄 + 正文遮名」雙機制 | runtime：`governance_checked` 旗標、missing denylist warning、restricted result removal warning | `test_cli_ask_drops_identity_hit_source_when_query_is_clean`、`test_agent_ask_redacts_body_mention_when_query_is_clean`、`test_citation_removed_when_title_hits_denylist`、`test_ask_warns_when_denylist_missing`、`test_answer_has_governance_checked_flag`、`test_answer_body_scrubbed_when_source_hits_denylist`、`test_agent_ask_answer_body_scrubbed_when_source_hits_denylist` | 已修（local patch；本 session 由 Fable 5 獨立驗證兩個洩漏路徑皆關閉） |
| GR-2 | 橡皮圖章 review（decision=建議、reviewer 空）被 apply | high | high（已發生前半） | J：reviewer/reviewed_at 空 → warning + IN-1 info；K：apply 前置檢查對 blank reviewer/reviewed_at 硬擋(拒絕整批) | J warning + IN-1 + K 前置檢查 | J + K sprint 測試（含 blank reviewer 拒絕） | 已修（J 偵測 + K 硬擋；Fable 5 獨立驗證） |
| GR-3 | decision 與 can_enter_* 矛盾未被驗出（如 exclude + index=true） | high | high | J 已實作 CR-5~CR-15、CR-18 全矩陣 | J conflict rules | J sprint 測試（本 session 自造 CR-5/CR-9 衝突資料實測會抓） | 已修（J sprint；Fable 5 獨立驗證） |
| GR-4 | 無 review row 紀錄在 apply 時被默認核准 | high | medium | K：無 review row 紀錄預設進 not_reviewed 桶(本 session smoke 實測 125 筆隔離);--include-clean-records 預設關閉 | K 守恆檢查 + not_reviewed 隔離測試 | K sprint 測試（Fable 5 獨立 smoke 驗證 125 筆隔離） | 已修（K sprint；Fable 5 獨立驗證） |
| GR-5 | 短 alias 子字串誤傷（誤 block/redact 正常內容）→ 管理者關掉 governance | medium | high | 已加短 term 詞界匹配（normalized 長度 <4 / 純 ASCII ≤3 不做子字串）；完整 alias 治理（括號別名等）仍待做 | alias 長度規則已進 code | `test_short_alias_requires_word_boundary` | 部分修復（本 session Excel 輪次；風險降但未全解） |
| GR-6 | 括號別名污染 denylist（「XX（已結束）」→「已結束」成 alias） | medium | medium | split 規則排除狀態詞 / review 時人工看 alias 清單 | preview 顯示 alias 已做，保持 | `test_parenthetical_status_words_not_treated_as_alias` | 未修 |
| GR-7 | workbook 改版 → 解析錯位靜默產出錯資料 | high | medium | 已加 excel-preview header preflight fail-fast + merge 展開改縱向-only（本 session 對正式檔驗證 120/33/7/11/91） | excel-preview preflight | `test_excel_preview_fails_fast_on_header_mismatch` 等 | 已修（本 session Excel 輪次） |
| GR-8 | Excel 日期 serial 靜默變 None → freshness 失真 | medium | high | 正式檔為文字日期（如 `2025.07`），33/33 解析成功、當前不觸發；code 仍未支援 native serial，未來若有原生日期格式需補 | summary 加 missing 計數 | `test_normalize_date_parses_excel_serial` | 部分緩解（當前格式不觸發，非全解） |
| GR-9 | pending_metric / can_quote=false 內容在外部用途被引用（無 intent gating） | high | low（程式防線已關閉；政策未開）→ 首次真實 LLM 啟用時需重驗 | P sprint 的 intent gating + Q sprint 的雙鑰政策、public-only payload、LLM 輸出 denylist、local citation/warning 組裝；政策確認前非 mock provider 一律拒啟 | `SearchFilters.intent` + `data_policy_confirmed` + `allow_internal_data_to_llm` | EV-G1~G6、EV-L1~L3、transport zero-call tests | 已修（P/Q 離線程式層完成；首次真實 provider 呼叫仍須依 Q §7 驗收） |
| GR-10 | enum 漂移（code vs GOVERNANCE_RULES.md）誤導維護者 | medium | 已發生 | J 已上線 code↔doc 對照測試；GOVERNANCE_RULES.md 補齊 review_identity_mapping | `test_enum_in_governance_rules_matches_code` | 同左（本 session 獨立實測 13 值全對） | 已修（J sprint） |
| GR-11 | `review_identity_mapping` 無語意定義即被使用 | medium | medium | 使用者裁決後補文件（G 信第 6 節） | J 暫按 manual-review 類處理 | — | 待使用者 |
| GR-12 | Excel 匯出的 publish_date=captured_date 造成「假新鮮」 | medium | medium | citation 顯示四種日期已可辨識；L 建議 freshness 以 metric_updated_date 優先 | — | freshness 測試 | 部分緩解 |

## 維護規則

- 新風險：發現即加列，ID 遞增，不重用。
- 修復後：狀態改「已修（PR/commit）」，**列不刪**——歷史風險是最好的 onboarding 教材。
- 每次 sprint 收尾掃一遍 likelihood 是否因環境變化改變（特別是 GR-9：接外部 LLM 時從 low 跳 high）。
