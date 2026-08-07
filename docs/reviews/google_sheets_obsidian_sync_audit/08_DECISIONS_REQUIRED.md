# Decisions Required

本文件分為已正式確認與仍待決定的產品／治理選擇。Confirmed Decisions共2題；Remaining Decisions共5題。

## Confirmed Decisions

### Decision 1 — Google Sheets API認證

**已確認：A — Read-only Service Account。**

正式規則：

1. Google Sheets正式同步使用專用Service Account。
2. Spreadsheet只授予該Service Account讀取權限。
3. Google API scope必須採最小唯讀權限。
4. Service Account不得擁有寫入Spreadsheet的能力。
5. 正式排程必須可無人值守執行。
6. Credential、token或service account secret不得commit至Git、寫入Audit文件、寫入Obsidian或輸出到log。
7. 若公司政策禁止長期JSON key，優先使用公司核准的Secret Manager、Workload Identity或等價無長期金鑰方案。
8. 本決策只確認認證架構，不授權本輪建立憑證或連接正式API。

### Decision 7 — Scheduler retry語義

**已確認：A — 總共最多3次attempt。**

正式時間：

- Attempt 1：09:00
- Attempt 2：09:30
- Attempt 3：10:00
- Timezone：Asia/Taipei

正式規則：

1. 規格統一使用「最多3次attempt」，不得寫成「初次＋retry 3次」。
2. 不存在10:30第四次執行。
3. 每次attempt都必須重新取得並驗證source fingerprint。
4. fingerprint不一致時，不publish、不archive，也不更新active release。
5. 第3次仍失敗後，本批次結束為`failed`。
6. 後續通知方式仍屬未決Decision 5，不得自行選擇Slack或Email。

## Remaining Decisions

以下5題仍須在進入相關Sprint前作成明確decision record，不得由實作者自行選擇。

## 2. Slack／內部搜尋渠道權限

**A. 沿用G-M中的「自媒體」權限**

- 不需改Sheet schema。
- 風險：「可在自媒體發表」不必然等於「可在內部Slack持久化與搜尋」，語義混用可能過度授權。

**B. 新增獨立Slack／internal_search權限**

- 權限語義清楚，可fail closed。
- 風險：需變更Sheet與人工重審既有MET。

**建議：B。** 不應把不同曝光面默認等同；新欄位未核准前Public Metric在Slack中fail closed。

## 3. `approved_by`是否白名單

**A. 限制核准者白名單**

- 能把frontmatter文字與實際授權身份連結。
- 風險：需維護名單、代理與離職流程。

**B. 接受任意非空字串**

- 操作簡單。
- 風險：任何人可自行填入名稱，approval幾乎沒有治理效力。

**建議：A。** 白名單應由versioned config/authority store管理；僅比對顯示名稱仍不夠，後續可加入commit identity或review artifact。

## 4. Apps Script部署方式

**A. Spreadsheet-bound script**

- 編輯者容易在Sheet內操作，部署門檻低。
- 風險：版本治理、跨環境review與權限可見性較弱，容易把商業邏輯藏在Sheet。

**B. 外部standalone project**

- 較適合版本控制、code review、least privilege與多Spreadsheet治理。
- 風險：部署與使用者操作較多一步。

**建議：B。** ID allocator是永久identity治理元件，應可獨立review/deploy；Sheet內可只保留清楚的操作入口。實作仍須另案授權。

## 5. 同步通知管道

**A. 專用Slack ops channel**

- failure/needs_review可集中處理並連結redacted report。
- 風險：Slack outage時通知也可能失敗；訊息內容需嚴格redact。

**B. Email distribution list**

- 與Bot runtime分離，較適合長期留存。
- 風險：threading/值班處理較弱，也需管收件人與敏感資訊。

**建議：A。** 先採專用私有ops channel，只發batch ID、狀態、counts與安全report位置；未來再加B作failure fallback，不在首版同時擴張。

## 6. Public Metric是否計入Slack每頁內容上限

**A. 與案例／內容資產共用同一result cap**

- renderer簡單、總長易控制。
- 風險：metric可能擠掉案例或反之，結果組成不穩定。

**B. 設獨立metric cap，再套總字元上限**

- 結果類型可預測，也能避免大量短metric淹沒案例。
- 風險：presentation與pagination稍複雜。

**建議：B。** 保留總字元上限，同時設type-specific cap；數值由Slack output測試與人工preview決定。
