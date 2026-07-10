---
name: accept-sprint
description: 驗收一個已完成的 sprint 分支(通常是 Codex 或其他 coding agent 交回的成果),決定能不能 merge。每當有一個 feature/fix 分支做完、收到 sprint 完成報告、要「驗收 / review / 確認能不能合併 / accept」一輪委派工作時,務必用這個 skill。它會做「不能採信報告」的那半檢查:重跑完整測試、掃有沒有偷改既有測試斷言、確認人工檔沒被動過,再列出需要人工判斷的項目(spec DoD、risk register)。這是「驗證不自驗」原則的固化——原作者(含 AI 自己)不能是唯一驗收者。
---

# accept-sprint — sprint 驗收儀式

## 為什麼要有這個 skill

這個專案由較弱的 coding agent(Codex 等)執行、由主模型驗收。實務上已經發生過兩次「報告全綠但成果其實有問題」——一次是新功能製造了假象再寫進綠色測試,一次是改了既有測試斷言來讓自己過關。所以驗收有一條鐵律(見 `docs/governance/C_MODEL_ROUTING_PLAYBOOK.md` §5「驗證不自驗」):**原作者不能是唯一驗收者,而且不能只看報告。**

這個 skill 把「必須自己重做、不能相信報告」的那半機械檢查固化,讓它不會被跳過。

## 什麼時候用

- Codex(或任何 agent)回報一個 sprint 做完了,分支在 `feat/*` 或 `fix/*`
- 你要決定「這個分支能不能 merge 進 main」
- 拿到一份「Summary / Files changed / Verification …」格式的完成報告

## 怎麼做

### 步驟 1:機械檢查(跑腳本,不採信報告)

先確定你在要驗收的分支上(`git branch --show-current`),然後:

```bash
.venv/bin/python .claude/skills/accept-sprint/scripts/accept_checks.py --base main
```

腳本會做四件人不該用眼睛草率確認的事:
1. **重跑完整 pytest** —— 不看報告說幾個 passed,自己跑一次。
2. **掃測試斷言完整性** —— diff 出被**修改的既有測試檔**(新增測試檔是正常的,改動既有的才可疑),並列出被移除/改動的 `assert` 行。這是抓「改斷言放水」的關鍵。
3. **原始碼改動摘要** —— 讓你知道實際動了什麼。
4. **人工檔完整性** —— 用 checksum 確認 `review_decisions_template.csv` 這類人工填寫的檔沒被 sprint 動過(它們 gitignore、git 看不到,所以自己 checksum)。

exit code:`0` = 機械檢查乾淨、`1` = 有旗標該停、`2` = 跑不起來。

### 步驟 2:人工/fresh-context 判斷(腳本會列出來)

機械檢查乾淨**不等於**可以 merge。還要:
- 對照該 sprint 的 **spec DoD** 逐條確認(在 `docs/specs/` 或對應文件)。
- **抽驗新規則不是空殼**:自己造一筆「應該被抓」的資料丟進去,確認真的被抓(例如 J sprint 時造一筆 `exclude` + `can_enter_content_index=true`,確認觸發 CR-5 error)。空殼測試會過但沒有保護力。
- 更新 **risk register**(`docs/governance/I_GOVERNANCE_RISK_REVIEW.md`)相關條目的狀態,標注是誰驗證的。
- governance / restricted / apply 相關的變更,**不得只靠原作者自驗**——需要 fresh-context 或人工二審。

### 步驟 3:裁決

- **全部通過** → merge(建議 `--no-ff` 保留 sprint 紀錄),然後更新 risk register 並 commit。
- **有旗標** → 不要 merge。把具體問題(哪個 assert 被動、哪個 DoD 沒達成、哪筆規則是空殼)寫成給原作者的退回訊息,附失敗軌跡。

## 人工檔快照怎麼維護

第一次用、或每次**人工合法編輯**了 decision CSV 之後(例如填完 review_decisions),跑一次記錄新的已知良好基準:

```bash
.venv/bin/python .claude/skills/accept-sprint/scripts/accept_checks.py --snapshot
```

然後把 `references/human_file_checksums.json` commit 起來。這樣之後任何 sprint 若動到人工檔,checksum 對不上就會被抓出來。

## 邊界

- 這個 skill 只讀不改(除了 `--snapshot` 寫自己的快照檔)。它不 merge、不改 code、不改 risk register——那些是你看完結果後的決定。
- 它不取代人的判斷,只保證「該自己重做的機械檢查」不被跳過,並把需要人判斷的項目攤在你面前。
