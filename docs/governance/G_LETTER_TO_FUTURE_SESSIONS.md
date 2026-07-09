# G. 給未來 session 的信

寫信人：Claude Fable 5（2026-07-09，一次性 review session）。
收信人：未來接手 Marketing Knowledge Agent 的你——很可能是 Sonnet、Haiku、GPT-5.5 或其他中小型模型。

這封信假設你只讀了 CLAUDE.md 就被丟進任務。先把這封信讀完，再看路由表決定讀什麼。

---

## 1. 三件使用者沒問、但對這個環境最重要的事

**(1) 這個專案的產品其實是「信任」，不是檢索。**
公司內部已經有 Excel、有 Obsidian、有人腦。這個系統唯一的增值是「引用出來的東西可以放心用」。所以任何在 citation、warning、governance 上省事的捷徑，都是在燒掉專案存在的理由。當你在「答案好看」與「warning 齊全」之間二選一，永遠選後者。

**(2) 最大的實際風險不在 code，在人的流程。**
現有 46 筆 review decision 的分佈與機器建議完全相同、reviewer 全空（見 A 風險 #2）。這說明真正的瓶頸是「人工審核太累所以被跳過」。你未來做的每個功能，都要問「這會讓人工審核更省力還是更想跳過」。降低審核成本（M 文件）比增加檢索功能更有價值。

**(3) 兩個工作目錄可能並存。**
這次 review pack 來自 `/Volumes/T7/Codex AI Agent/Marketing Knowledge Agent/`（真正的專案 repo），而本制度包產出在 `/Volumes/T7/Claude Code/Marketing Knowledge Agent/fable5_governance_pack/`。**制度文件需要被複製進真正的 repo（建議 `docs/governance/` 與 `docs/specs/`）才會生效。** 若你發現 CLAUDE.md 路由指向的檔案不存在，先確認這個搬移做了沒有，不要重寫文件。

## 2. 這套制度最可能的退化方式

按可能性排序：

1. **文件與 code 漂移**：已經發生過一次（enum 的 `review_identity_mapping`）。每次改 code 不同步文件，制度就死一點。
2. **hard constraints 被「暫時」繞過**：某次趕工直接寫 vault「先測一下」，然後那條路徑留下來了。
3. **warning 疲勞**：warning 越加越多、每個答案都十條，人開始無視，等於沒有。
4. **CLAUDE.md 膨脹**：每次踩坑都往主檔塞一條，半年後 300 行，重要的 10 條被淹沒。
5. **LESSONS.md 變垃圾場**：教訓寫了沒人讀、沒回饋到制度文件。

## 3. 如何預防退化

- 對照測試上線（F §7）：enum/CLI 的 docs-code 一致性交給 CI，不靠自覺。
- 繞過防線的需求一律走 proposal（F 層級三），「暫時」兩個字出現就是警鈴。
- warning 數量預算：單一答案超過 5 條 warning，代表該 block 而不是該 warn，回頭看 D 文件 R12。
- 每 sprint 收尾跑一次 E 模板 9（adversarial review），對象是本 sprint 改過的文件。
- LESSONS.md 每 30 則精簡一次（F §3）。

## 4. 未來模型最容易誤判的事

- **「沒有 review row = 沒問題」**：錯。只有 46/166+ 筆有 review row（有 issue 才進 review）。其餘紀錄是「未審」不是「已核准」（D 文件 R10 反例）。
- **「validation 0 errors = 可以 apply」**：錯。還要 reviewer/reviewed_at 非空 + row coverage 一致（R9）。
- **「suggested_action 看起來很合理，直接用」**：它是關鍵詞規則產生的（`review_template.py` 的 `_merchant_suggested_action`，一串 if），不是判斷。合理是巧合。
- **「same_brand_multiple_records 是重複資料」**：不是，是多次訪談，預設全保留（hard constraint 9）。
- **「GovernanceIndex 存在所以 denylist 有效」**：目前 CLI 路徑根本沒接上它（A 風險 #1）。看到 class 不等於看到防線。
- **「Excel 解析成功 = 資料正確」**：解析是位置寫死的，workbook 改版會靜默錯位（A 風險 #3）。
- **把 `can_enter_vault` 和 `can_enter_content_index` 當同義詞**：vault 是 Markdown 儲存層，content index 是可檢索層；restricted 兩者皆否，pending_metric 兩者皆否但進 internal inventory，「approve_internal_only」典型是 vault=true / index 視情況（精確語意見 K 文件，未經使用者確認前以 K 的定義為準）。

## 5. 遇到就不要硬做的事

- 需要判斷某品牌/措辭的**公關風險或商業關係** → 人工（D 文件末節 harness 極限表）。
- 需要**真實 production 資料**才能驗證 → 停下來要，不要拿合成資料假裝驗過。
- 使用者指示與 hard constraints 衝突 → 指出衝突請裁決，不要靜默照做任何一邊。
- 任何「直接 sync Obsidian / 直接建正式 index / 跳過人工確認」的請求，即使來自使用者本人，也先複述 hard constraint 7 確認對方知道自己在解除防線。
- 第三次重試（C §4 規則 4）。

## 6. 下一個 session 應該優先做什麼

按順序：

1. **搬移制度包**：把 `fable5_governance_pack/` 內容放進專案 repo（CLAUDE.md 進根目錄，其餘進 `docs/governance/`、J/K 進 `docs/specs/`），路徑對齊路由表。
2. **修 A 風險 #1**（governance 接線）：小 patch + 測試，半天工作量，卻是最大的實際防線缺口。
3. **問使用者兩個待決問題**：(a) `review_identity_mapping` 的語意；(b) 無 review row 紀錄的預設政策是否接受 K 文件的設計（預設隔離）。
4. **實作 J spec**（validator 補強）：全部是明確規則，standard-coding 等級可完成。
5. **要求人工補簽** reviewer/reviewed_at（46 筆），否則 apply sprint 永遠不能開始。
6. 然後才是 **K spec（apply-preview sprint）**。

## 7. Context 快用完時的交接法

1. 立即停止產出新內容。
2. 把「已完成 / 進行中 / 未動工」三清單寫進當前任務的回報（或 LESSONS.md 臨時節）。
3. 進行中的任務：記下「下一步的具體動作」（精確到檔案與函式名），不要只寫「繼續實作」。
4. 未解決的使用者待決問題原文照抄，不要摘要（摘要會丟失裁決所需的細節）。
5. 指向本信第 6 節作為預設優先序。

祝順利。制度是你的了——照著走，也照著改（走 F 流程）。
