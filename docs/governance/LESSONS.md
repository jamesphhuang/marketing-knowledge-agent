# Lessons

## 2026-07-09 GR-1b 裁決：丟棄與遮字要依命中位置區分
- 情境：實作 GR-1b 生成前濾除後，既有測試 `test_agent_ask_warns_and_redacts_restricted_customer_match` 變紅，因合法來源的正文提到 restricted 品牌時被整筆丟棄。
- 錯誤：丟棄依據誤含 `chunk.text`，導致正文順帶提及 restricted 品牌的合法來源被誤殺，違反「合法來源保留、品牌遮名」的治理邊界。
- 根因：沒有區分「身分欄位命中」與「正文命中」；前者代表來源本身是 restricted 對象，應生成前丟棄，後者代表內容提及 restricted 對象，應事後 redact。
- 修正：`_restricted_result_check_text` 移除 `chunk.text`，只保留 title/source_path/brand_name/merchant_handle；正文命中交給 `apply_governance_to_answer` 事後遮名。
- 制度回饋：已更新 `docs/governance/I_GOVERNANCE_RISK_REVIEW.md` 的 GR-1 mitigation；PR 未建立，因目前 workspace 非 git repo。

## 2026-07-09 GR-1b：答案本文與 citation 必須同源治理
- 情境：修復 GR-1 後，fresh-context review 發現 citation 被移除時，`generate_answer` 產生的 `[N] {snippet}` 仍可能留在答案本文。
- 錯誤：上一輪只在 `apply_governance_to_answer` 事後移除 citation title/source_path 命中的引用，沒有在生成前濾除整個 restricted SearchResult，導致 `LEAK_MARK` 這類不含品牌名的正文片段仍可能出現在 answer body。
- 根因：答案本文與 citation 分開治理；citation 移除沒有同步移除已生成的答案段落，事後 `redact_text` 也只遮 brand_name / aliases，不能清掉不含品牌名的 restricted snippet。
- 修正：新增生成前 `filter_restricted_results`，讓 ask 與 agent-ask 在 `generate_answer` 前剔除命中 denylist 的來源；保留事後 answer redact 與 citation scan 作第二道防線。
- 制度回饋：已更新 `docs/governance/I_GOVERNANCE_RISK_REVIEW.md` 的 GR-1 mitigation；PR 未建立，因目前 workspace 非 git repo。

## 2026-07-09 mock_vault 不是穩定 smoke fixture
- 情境：GR-1 CLI smoke test 嘗試用 `data/mock_vault` 建立臨時 index。
- 錯誤：`data/mock_vault` 內有 showcase 檔缺少必要 frontmatter metadata，`mka ingest --vault data/mock_vault` 會失敗，不能當作穩定 smoke fixture。
- 根因：mock vault 混有尚未完成 metadata backfill 的 showcase 檔；sample smoke 應使用 metadata 完整的 `data/sample_vault` 或測試臨時 vault。
- 修正：本次 CLI smoke 改用 `data/sample_vault` 與 synthetic denylist。
- 制度回饋：後續 smoke 指令若需要穩定 ingestion，優先使用 `data/sample_vault`；若要使用 `data/mock_vault`，需先完成 metadata backfill。

## 2026-07-10 已填寫的 review CSV 曾整份遺失,且 re-run review-template 會覆蓋主檔
- 情境:準備 apply 前置簽核時,發現 07-08 曾填好並通過驗證的 46 筆 decision CSV 在全機都找不到,磁碟上只剩空白 template。
- 錯誤:人工填寫成果只存在單一工作檔;`mka review-template` 重跑會直接覆蓋同路徑檔案。
- 根因:人工產物與機器產物共用同一個檔案路徑,且無備份紀律。
- 修正:重新以群組批次審核完成 46 筆(reviewer=Admin, 2026-07-10);填寫後立即產生 `review_decisions_FILLED-YYYYMMDD.backup.csv` 副本。
- 制度回饋:填寫完成的 decision CSV 必須立刻建立日期後綴備份;任何人重跑 review-template 前先確認主檔是否含人工內容(reviewer 欄非空 = 人工檔,禁止覆蓋)。建議未來 K sprint 或 template 工具加「偵測到 reviewer 非空即拒絕覆蓋」保險。

## 2026-07-10 K sprint 驗收:兩個流程教訓(非 code 錯誤)
- 情境:K sprint(apply-review-decisions)驗收時發現兩個流程問題。
- 問題 1:Codex force-add 了 `reports/reports/fable5_review_pack/ARCHITECTURE.md` 進版控,
  繞過 gitignore 的 /reports/ 規則。該檔非敏感(架構 doc、無客戶資料),
  已 git rm --cached 停止追蹤,不需 history purge。但顯示 agent 可能用 git add -f 繞過忽略規則。
- 問題 2:K 分支在 skills commit(e178223)之前開,所以分支上沒有 .claude/skills/,
  導致無法在分支上跑 accept-sprint;改為手動執行同等檢查。
- 根因:(1) DoD「更新 ARCHITECTURE.md」被誤解——repo 無正規 ARCHITECTURE.md,
  只有 review pack 巢狀副本,agent 就改+force-add 了那份;(2) 功能分支落後於 main 的工具。
- 制度回饋:
  1. 驗收時務必 `git ls-files reports/`(或掃 merge 的 create mode)確認沒有 reports/ 檔被 force-add
     進版控——這次非敏感,但同樣手法可能塞進真實客戶資料。
  2. 派工時若 DoD 涉及某文件,先確認該文件在 repo 正規位置存在;不存在就別讓 agent 亂找副本改。
  3. 開功能分支前,先確認 main 的工具(.claude/skills/、docs)已在分支基底;
     或驗收時從 main 的 worktree 跑 accept-sprint。
  4. ARCHITECTURE.md 後續:repo 缺正規架構文件;要嘛建 docs/ARCHITECTURE.md,要嘛以
     docs/governance/H_ARCHITECTURE_REVIEW.md 為準並移除 DoD 對 ARCHITECTURE.md 的要求。

## 2026-07-10 N sprint:frontmatter round-trip bug——合成測試全綠、真實資料 2/13 出錯
- 情境:Obsidian sync 驗收彩排(真實資料、vault 副本)發現 execute 剛寫完的檔,
  零編輯 re-plan 即有 2 檔被誤判 conflict_user_edited。
- 錯誤:frontmatter 寫入端對反斜線/引號轉義,parser 讀回不做反轉義——非互逆,
  每輪 sync 轉義翻倍、checksum 永不匹配,該檔永久卡死不再更新。
- 根因:97 個測試的 fixture 全是乾淨字元;真實資料的 metric_note 含字面 \n、
  invalid_asset_values 含 JSON 引號,合成測試永遠碰不到。
- 修正:序列化改為與 parser 嚴格互逆 + execute 新增逐檔「寫入後重讀重算 checksum」自檢
  (mismatch → 整批失敗還原)。修復後同一彩排 13 unchanged / 0 conflict。
- 制度回饋:(1) 任何「寫入後會再被讀回比對」的功能,測試 fixture 必須含
  反斜線、引號、換行、巢狀 JSON 等髒字元;(2) 驗收彩排必須用真實資料跑完整
  write→read 循環,不能只跑一次寫入;(3) 字串比對驗證 frontmatter 會因引號風格
  誤報,驗 marker/欄位一律用 parser 解析後比對。

## 2026-07-10 P sprint:防線演進時,舊測試改「輸入」不改「斷言」
- 情境:P 的查詢級拒答(檢索前攔截)使兩個守護「來源丟棄層/答案遮字層」的舊測試失敗
  ——舊測試的查詢字串本身含 restricted 品牌,新防線在更外層就攔截了。
- 裁決模式(第四次同類裁決,固化為通則):行為變嚴時,舊測試改「輸入」
  (查詢改中性詞,讓它繼續走到並驗證原防線層),絕不改「斷言」遷就新行為
  ——改斷言會讓深層防線失去測試覆蓋。新行為由新測試覆蓋,不與舊測試混用。
- 驗收要求:授權修改既有測試時,明列准動的部分,收回時逐字 diff 核對
  (本輪實核:僅 2 測試名 + 2 查詢字串,零斷言變動)。
