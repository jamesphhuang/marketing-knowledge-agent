---
name: delegate
description: 把一份 sprint spec 轉成可直接貼給執行型 coding agent(Codex 等)的完整派工訊息。每當要「派工 / 委派一個 sprint / 把某份 spec 交出去實作 / 產生給 Codex 的工單」,或手上有一份 docs/specs/ 下的 spec 準備開工時,務必用這個 skill。它保證派工訊息不漏掉關鍵欄位——分支紀律、Non-goals、Required checks、smoke 錨點、Acceptance、Stop conditions——即使主持者是較弱的模型。這是本專案「委派—驗收」循環的前半;後半是 accept-sprint skill。
---

# delegate — spec → 派工訊息產生器

## 為什麼要有這個 skill

本專案由主模型定義任務、執行型 agent(Codex)實作、再由主模型驗收(見 `docs/governance/C_MODEL_ROUTING_PLAYBOOK.md`)。派工訊息的品質直接決定 Codex 做出來的東西對不對。這個 session 已經證明:漏掉「Non-goals」會讓 agent 順手改到不該碰的東西;漏掉「smoke 錨點」會讓錯誤的實作也回報成功;漏掉「不得改既有測試斷言」會讓 agent 靠改斷言騙綠燈。

這個 skill 把派工的**固定骨架**固化,讓即使較弱的主模型也不會漏欄位——同時把**需要判斷的部分**(哪些是最容易做錯的點、smoke 的預期數字)明確交還給模型去讀 spec 得出,不假裝能自動生成。

## 什麼時候用

- 手上有一份 `docs/specs/` 的 spec,要開一個新 sprint
- 使用者說「派工 / 產生給 Codex 的訊息 / 把這個交出去做」

## 怎麼做

### 步驟 1:抽 spec 結構(機械部分)

```bash
.venv/bin/python .claude/skills/delegate/scripts/extract_spec.py docs/specs/<X>_*.md
```

它會列出:標題、建議分支名、執行等級、使用者裁決狀態、Non-goals 原文、DoD 原文、安全防線節、可能的 smoke 錨點。

**先看「使用者裁決」那行**:若 spec 還有未回填的裁決(顯示 `＿＿＿`),**停下來先問使用者**,不要派一個前提未定的工單。

### 步驟 2:讀 spec 判斷(判斷部分,不可略)

extractor 給不了的兩件事,你必須自己讀 spec 得出:

1. **「最容易做錯的點」**:挑 3~6 個。判準是「spec 有寫、但弱模型最可能誤解或抄近路的地方」——例如單一收口點、不可改的共用模組、雙鑰無繞過、payload 剔除。每點一句話,指向 spec 章節。
2. **smoke 錨點的預期數字**:從 extractor 列的候選裡挑真正的驗收錨點,若 spec 沒給具體數字而你能從真實資料算出(像 external=7/12),**先自己算再寫進派工**——這是讓「錯誤實作也無法蒙混」的關鍵。

### 步驟 3:用固定模板組裝

ALWAYS 用這個骨架,不可省任何一節(填入 `{...}`):

```text
# 任務:{X} Sprint — {一句話標題}

## 開工前
git checkout -b {建議分支名}
全程在分支作業,完成後 commit(前綴 "{type}({scope}): ..."),不要 merge、不要動 main。

## Task goal
照 {spec 路徑} 實作。spec 是唯一需求來源;{若有已回填的使用者裁決,一句話帶過}。

## 最容易做錯的點(spec 都有,點名強調)
{步驟 2 挑出的 3~6 點,各一句話 + 指向 spec 章節}

## Non-goals
{extractor 的 Non-goals 原文濃縮;務必含「不動 reports/ 人工檔、不寫 obsidian_vault/」若相關}

## Required checks
- 基線 pytest 全綠(目前 {N} passed)後才動工;結束再全套跑一次
- {spec DoD} 逐項先紅後綠;不改既有測試斷言
- Real-data smoke:{步驟 2 的錨點,含預期數字;明示「數字不合即停」}
- 回報只貼計數,不貼品牌名 / 客戶名 / payload 原文

## Acceptance criteria
- [ ] {DoD 逐條}
- [ ] {安全防線有 should-fail 測試(若 spec 有安全節)}
- [ ] 全套 pytest 全綠
- [ ] 分支 commit,回報 branch + hash + pytest 尾行

## Report format
Summary / Files changed / Acceptance 逐條+證據 / smoke 結果 / Not verified / Open questions

## Stop conditions
- 需要改共用模組 / 改 governance 語意才能完成 → 停止回報(層級二,要問使用者)
- smoke 數字不合預期 → 停,回報推導
- 同一測試修兩次仍紅 → 停,附失敗軌跡
- {該 sprint 特有的紅線,例如「任何真實 API 呼叫」「任何寫入真實 vault」}
```

### 步驟 4:輸出

把組好的派工訊息用程式碼區塊呈現給使用者,讓他可直接複製貼給 Codex。**不要自己去執行 sprint**——這個 skill 只產生工單。

## 不變的紅線(每張工單都要有,不論 spec 怎麼寫)

這幾條是專案 hard constraints 的投影,即使 spec 沒明寫也要放進派工:
- 不動 `reports/` 下人工填寫的檔(decision CSV 唯讀)
- 不寫入 `obsidian_vault/`(真實 vault)
- 不改既有測試斷言(要改既有測試 → 只准改輸入/測試名,見 `docs/governance/LESSONS.md` 2026-07-10)
- 回報不含真實品牌/客戶名

## 邊界

- 這個 skill 產生「文字工單」,不執行、不 commit、不派 subagent。
- 它不取代讀 spec——步驟 2 的判斷是它品質的來源。extractor 只是讓你不漏機械欄位。
- 產生工單後的下一步是:Codex 執行 → 用 `accept-sprint` skill 驗收。
