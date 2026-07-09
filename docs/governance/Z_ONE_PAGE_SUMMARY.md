# Z. One-Page Summary

## 我做了什麼

沒有改任何 code。把一次性的強模型判斷轉成 16 份可長期沿用的制度文件（`fable5_governance_pack/`）：**制度層** A–G（風險診斷、CLAUDE.md 建議版含 10 條 hard constraints、模型調度、12 條判斷 rubric、9 個派工模板、維護協議、交接信）＋**專項層** H–M（架構、governance 風險登記簿、下兩個 sprint 的完整規格、檢索精準度路線圖、管理者體驗）＋**收尾** Z×3（對抗審查、read-back、本頁）。

## 為什麼這些最重要（三個核心發現）

1. **Denylist 防線在 CLI 路徑實際是關的**：`ask` 無 governance 檢查、`agent-ask` 沒接 governance_index、citation 不過濾（GR-1，critical）。
2. **人工審核已被橡皮圖章**：46 筆 review_decision 分佈與機器建議完全相同、reviewer/reviewed_at 全空；且 validator 對「decision 與 can_enter_* 矛盾」幾乎不檢查（GR-2/GR-3）。
3. **120 筆 merchant case 只有 24 筆進 review**——「沒被審」與「已核准」的界線沒有任何文件定義，apply sprint 最容易在這裡出大錯（GR-4）。

## 明天開始怎麼用

1. 把本包複製進專案 repo：`B` 內的 CLAUDE.md 區塊 → repo 根目錄；其餘 → `docs/governance/` 與 `docs/specs/`（J、K）。**路由表路徑對齊後制度才生效。**
2. 之後每個 session：CLAUDE.md 自動載入 → 按路由表按需讀文件 → 派工用 E 模板 → 判斷用 D rubric → 踩坑寫 LESSONS.md（F 格式）。

## 下一個 sprint 做什麼（依序）

1. 修 GR-1（governance 接線 + citation 過濾 + 測試）——半天，最大安全缺口。
2. 問使用者兩題：`review_identity_mapping` 語意？無 review row 紀錄接受「預設隔離」政策嗎？
3. 實作 **J spec**（validator 補強：18 條規則、三檔輸出、enum 對照測試）。
4. 請人工補簽 46 筆 reviewer/reviewed_at——這是 apply sprint 的硬前置。
5. 然後才是 **K spec**（apply-preview）。

## 仍然禁止做的事

直接 sync Obsidian、建正式 content index、套用 review decision（apply 未實作且簽核未完成）、把 restricted_customer / handle_mapping 放進任何檢索、把 pending_metric 對外引用、替人填 review_decision、引入外部 LLM / MCP / Web UI、處理 raw production data 到會離機的輸出。完整清單見 CLAUDE.md hard constraints 10 條。

## 已知極限

本 review 基於 snapshot 靜態閱讀（未實跑）；真實 workbook 與 denylist 未見過（alias 誤傷率未知）；對抗審查是自審非真 fresh context（下 session 應重跑一次 E 模板 9）；商業/公關語境判斷永遠需要人——清單見 D 文件末節。
