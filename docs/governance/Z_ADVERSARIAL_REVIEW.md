# Z. Fresh-Context Adversarial Review（對本包全部產出）

> 方法：以「不知情讀者 + 假設撰寫者會犯錯」立場，對 A–M 全部文件執行 E 模板 9 的五項必查。
> 撰寫者與審查者同為本 session（誠實聲明：這不是真正的 fresh context——見文末殘餘限制）。
> 標注 `已修` 的項目：發現後已直接修正原文件；標注 `留存` 的：需未來 session 或使用者處理。

---

## 檢查 1：規則是否互相打架

| # | 發現 | 處置 |
| --- | --- | --- |
| F-1 | D 文件 R11(f) 說對外引用 >540 天需人工確認；L 1.7 原稿說 >540 只 warning、>900 才人工——兩文件會被弱模型分別引用，判準不一致 | **已修**：L 1.7 改為按用途分級並明確引用 R11 |
| F-2 | J 原稿 CR-15 一格內同時寫「允許」與「error」兩件事，弱模型可能把 keep_internal_only 誤讀進 error 條件 | **已修**：CR-15 重寫為單一條件（非 restricted_customer 使用 enter_governance_table_only → error），允許事項移到理由欄 |
| F-3 | K 的歸桶按 decision，但現有 template 對 exclude 列給 can_enter_vault=true（sample row 4 實證）——spec 未說明矛盾時誰優先，實作者可能兩種都寫 | **已修**：K §3 新增「decision 優先規則」，矛盾逐筆列入 summary |
| F-4 | B 的 hard constraint 8 說「任何答案路徑必須經過 apply_governance_to_answer」，但現有 code 不符（GR-1 未修）。弱模型可能誤以為已生效，或在無關任務中被此條卡住 | **留存**：constraint 是對「你寫的新 code」的要求，保持原文；GR-1 已在 I register 標 critical、G 信列為第 2 優先。接手者修完 GR-1 前，此條的執行狀態=「新增路徑適用，存量路徑欠帳」 |
| F-5 | J CR-11（approve + can_quote=false → warning）與 GOVERNANCE_RULES.md 的 approve 定義（「May enter Vault / content index」未提對外）不衝突，但使用者 brief 問「warning 或 error，請說明」——已在規則行內給出理由（內部使用合法），符合要求 | 無需處置 |

## 檢查 2：路徑是否錯

- **系統性風險（最重要）**：B 的 CLAUDE.md 路由表指向 `docs/governance/` 與 `docs/specs/`，但本包實際位置是 `/Volumes/T7/Claude Code/Marketing Knowledge Agent/fable5_governance_pack/`，而專案 repo 在 `/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent/`。**在搬移完成前，路由表全部路徑都是 404。** 已在 B §1 標注假設、G 信第 1 節與第 6 節第 1 項把「搬移」列為下個 session 第一件事。`留存`：搬移時若目錄名不同，必須同步改路由表。
- 文件引用的 code 路徑（`src/marketing_knowledge_agent/*.py`、`reports/excel_preview/*`）與 review pack 的 manifest 一致，逐一核對過。`通過`
- J §6 輸出檔名與使用者 brief 要求的三個檔名逐字一致。`通過`

## 檢查 3：工具名 / 命令是否錯

- `mka` 各 subcommand 名稱與 `cli.py` 的 `build_parser()` 逐一核對：ingest / validate / backfill-report / search / ask / agent-ask / evaluate / excel-preview / review-template / validate-review-decisions 全部真實存在。`apply-review-decisions` 在 K 中明確標注「尚未實作」。`通過`
- E 模板 1 的「Explore 型 agent」與模板 4 的「WebSearch」是**本次 harness 的名稱**，未來環境未必存在。`留存`：C 文件 §0 已規定「依環境查得後填入」，但 E 應理解為「用你環境中對應能力的工具」；接手者填 C §0 抽換表時順手核對 E。
- pytest 命令寫法（`.venv/bin/pytest`）沿用 pack manifest 的實際用法。`通過`

## 檢查 4：弱模型可能誤讀處

| # | 位置 | 可能誤讀 → 後果 | 處置 |
| --- | --- | --- | --- |
| M-1 | K §9 守恆等式範例數字（120 = 18 + 1 + 76…） | 當作真實預期值寫死在測試裡 | **已修**：標注「格式示意」 |
| M-2 | J §2 final_status enum「後三者為人工可改的目標值」 | 弱模型把 template 產生的 review_required 判為非法 | 原文已列 review_required 在允許值內；`通過` |
| M-3 | D R10 條件 (b) 列了 4 個可進 index 的 decision | 誤以為其他 decision 進 index 是 validator 的事、apply 不用再擋 | K §3 的對應表是第二道；兩處一致；`通過` |
| M-4 | 「preview」一詞在兩個語境使用：excel-preview 的輸出 JSON vs apply-preview 的輸出目錄 | 混淆導致讀錯輸入 | `留存`（低風險）：兩者路徑不同且 K §2 有完整目錄樹；未來如有混淆案例，改名 `apply_preview` 為 `apply_output_preview` 屬層級一修正 |
| M-5 | IN-1 門檻 total_rows > 10 | 對 10 行的 sample 不觸發被當 bug | J §7 已預先說明；`通過` |

## 檢查 5：危險自動化審查

- **是否有內容暗示可跳過 manual review？** 逐文件檢查：無。唯一接近的是 K §7 `--include-clean-records`——它允許「無 issue 紀錄」不經逐筆審核進 vault preview。風險評估：(a) 預設關閉；(b) 只影響本來就沒有任何 governance issue 的紀錄；(c) 輸出仍是 preview，後面還有人工確認關卡；(d) 已標注為待使用者裁決。**結論：可接受，但接手者不得在未經使用者同意下把預設改為開啟**（此句即為約束，J/K 已含）。
- **是否有內容建議直接 sync Obsidian？** 無。K 鐵律、B constraint 7、M 長期節反覆禁止；M 並把「sync 前 diff preview」定為未來 sync sprint 的 hard requirement。
- **是否有把 restricted customer 放進 RAG 的風險？** 文件層無。反向檢查：K §10 白名單斷言、L 1.5 三道 denylist 檢查、I GR-1——本包對此風險是收緊而非放鬆。
- **模板類文件（E）是否可能被注入濫用？** E 模板 8 的 Non-goals 把 hard constraints 內嵌在模板裡，即使派工者忘了附 B 文件，subagent 仍看得到禁令。`通過`

## 太抽象 / 缺例子的地方（自查）

- C §1「大量讀取（~5 個檔案 / ~1000 行）」的門檻是經驗值不是硬規則——刻意保留模糊（強模型留白原則），弱模型直接照數字執行即可，兩者都能用。`通過`
- L 1.9 的分數門檻 0.1 標注了「需以 eval 校準」——誠實但弱模型無法自行校準。`留存`：eval 集（L 1.12）上線前，門檻暫用 0.1 並記入 LESSONS。
- D 各 rubric 均有正反例與失敗處理，抽查 R4、R9、R12 可獨立執行。`通過`

## 殘餘限制（誠實條款）

1. 本審查非真正 fresh context——撰寫者的盲點可能共享。**建議：下個 session 開場即用 E 模板 9 對本包再跑一次真 fresh-context 審查**（一小時內工作量），把新 findings 補進本檔。
2. 所有 code 行為結論基於 src_snapshot 靜態閱讀，未實跑（pack 無完整可執行環境）。標注為「靜態確認」等級：GR-1（denylist 未接線）從 `cli.py` 呼叫鏈可直接證明，信心高；GR-8（日期 serial）是從 `_cell_value` 回傳字串 + `normalize_date` 格式清單推導，信心中高，**接手者修復前先寫一個重現測試確認**。
3. 真實 workbook 與真實 denylist 未見過，alias 誤傷率（GR-5/6）的實際嚴重度未知。
