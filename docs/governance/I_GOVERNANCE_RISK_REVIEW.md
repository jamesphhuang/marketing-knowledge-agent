# I. Governance Risk Review

> 活文件：risk register 隨 mitigation 進度更新。
> Severity：critical（資料外洩/不可回復）> high（錯誤決策被套用）> medium（品質/信任損傷）> low。
> Likelihood：以目前離線 prototype 與未來弱模型維護風險評估。

## Governance Risk Register

| ID | 風險 | Severity | Likelihood | Mitigation | Required validation rule | Required test | 狀態 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GR-1 | restricted 品牌名經 content 內文進入答案/citation；denylist 未接線或只移除 citation，仍留下答案本文 snippet | critical | high | 已接上 ask / agent-ask denylist；新增 `governance_checked`；身分欄位命中（title/source_path/brand_name/merchant_handle）→ 生成前丟棄；正文命中 → 事後遮名；另保留 citation title/source_path 二次掃描，形成「身分欄位命中丟棄 + 正文命中遮名」雙機制 | runtime：`governance_checked` 旗標；missing denylist warning；restricted result removal warning | `test_cli_ask_blocks_restricted_brand_in_content`、`test_citation_removed_when_title_hits_denylist`、`test_ask_warns_when_denylist_missing`、`test_answer_has_governance_checked_flag`、`test_answer_body_scrubbed_when_source_hits_denylist`、`test_agent_ask_answer_body_scrubbed_when_source_hits_denylist` | 已修（local patch；PR 未建立，workspace 非 git repo） |
| GR-5 | 短 alias 子字串誤傷（例如短英文 alias） | medium | high | 另案處理；本次不改 alias matching 語意 | alias 長度 / word-boundary 規則待設計 | 待補 | 未修 |
| GR-9 | pending_metric / can_quote=false 內容在外部用途被引用（無 intent gating） | high | low（現階段離線）→ high（接 LLM 後） | 接外部 LLM 前需導入 intent + channel gating | SearchFilters / answer mode 強制規則 | 待補 | 路線圖 |

## 維護規則

- 新風險：發現即加列，ID 遞增，不重用。
- 修復後：狀態改「已修（PR/commit）」；若沒有 PR/commit，需明確標示 local patch 與原因。
- 已修風險列不刪，保留作為 onboarding 與 regression 測試索引。
