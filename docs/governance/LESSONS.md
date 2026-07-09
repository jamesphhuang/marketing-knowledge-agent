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
