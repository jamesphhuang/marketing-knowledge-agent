# ROADMAP — 從離線 prototype 到 Slack 對話取用

> 終點目標(使用者 2026-07-10 確認):**使用者透過 Slack 對話,以自然語言取用行銷知識庫**,並獲得 citation、freshness、governance warning。
> 本文件是階段路線圖,不是 spec。每個階段開工前先寫該階段的 spec(照 J/K/N 模式),再派工、再用 accept-sprint 驗收。
> **鐵律:階段有依賴順序,不得跳關。** 特別是:不得在 Stage 2 的 gating 完成前接外部 LLM;不得在 Stage 3 完成前接 Slack。

---

## 已完成(2026-07-10 前)

Excel 匯入(對正式檔驗證)→ 人工審核簽核流程 → J validation → K apply preview → N Obsidian sync(首批 13 篇已同步)。制度包、git、驗收儀式(accept-sprint)、excel-check skill 皆就緒。

## Stage 1:Formal Content Index ✅(2026-07-10 完成)

- **目標**:approved 內容(vault 中 `can_enter_content_index=true` 的 managed 檔)建進正式 SQLite content index,可用 `mka search / ask` 檢索。
- **Spec**:`docs/specs/O_CONTENT_INDEX_SPEC.md`(已實作,2026-07-10 merge f6b8e9c)。
- **結果**:真實 vault 13 掃描 / 12 索引 / 1 vault_only 排除;四項安全斷言 + 毒針測試通過;`mka ask` 對 `.mka/content_index.sqlite` 可回答且 citation 含完整溯源。
- **殘留**:L 文件 EV-G1~G6 governance eval 尚未自動化,歸入 Stage 2 一併做(gating 上線時本來就要跑)。

## Stage 2:查詢閘門(GR-9 關閉)

- **目標**:系統能區分 internal / external 用途並據此過濾;拒答規則上線。
- **Spec**:`docs/specs/P_QUERY_GATING_SPEC.md`(2026-07-10 已寫)。依據:L 文件 §1.1/§1.5/§1.9/§1.12。
- **前置**:Stage 1。
- **完成判準**:EV-G1~G6 全綠;external intent 下 pending_metric / can_quote=false / 缺 channel 的資料檢索不到;denylist 品牌查詢走統一拒答模板。

## Stage 3:外部 LLM 接入

- **目標**:自然語言理解與回答生成交給外部 LLM,governance 全部留在本地。
- **依據**:H 文件 §8 五原則——record_type 資格在檢索層擋、LLM 輸出再過 denylist、can_quote/channel 在檢索前過濾、citation 由本地程式組裝(LLM 不產 citation 欄位)、warning 規則式不交 LLM。
- **前置**:Stage 2(gating 是 blocker,GR-9 在此階段 likelihood 升為 high)。
- **需使用者裁決**:LLM 供應商與資料處理政策(內部資料送外部 API 的公司政策確認)。

## Stage 4:Slack 介面(終點)

- **目標**:Slack bot 對話取用。
- **Spec 檔名**:`docs/specs/S_SLACK_INTERFACE_SPEC.md`(未寫;寫 spec 前先取得下列裁決;原編號 P 讓給 Stage 2)。
- **前置**:Stage 3。
- **需使用者裁決(政策,非技術)**:
  | # | 問題 | 說明 |
  | --- | --- | --- |
  | a | internal-only 資料能否出現在 Slack 回覆? | Slack 訊息存雲端、頻道成員可見可搜尋——比 CLI 的暴露面大得多。建議:公開頻道只回 `can_quote_externally=true`;internal 內容僅限指定私有頻道或拒答引導走內部工具 |
  | b | 誰能問? | 全 workspace / 指定頻道 / 指定成員 |
  | c | denylist 命中的處理 | 拒答模板會留在頻道;是否同時通知 governance owner + 記 audit log(建議:是) |
  | d | 回覆的 citation 呈現 | Slack 格式下 citation/warning 如何顯示不被裁切 |
- **硬性要求(寫 spec 時不可省)**:所有 governance 檢查在本地服務端完成後才發訊;audit log 記錄每次問答(誰、問什麼、命中什麼防線);Slack 端零快取敏感內容。

## 平行小任務(不佔階段,隨時可插隊)

- GR-6:括號別名污染 denylist(alias 拆分規則)
- GR-11:`review_identity_mapping` 語意(等使用者一句話裁決)
- Pydantic v2 遷移 + models.py 拆分(獨立 sprint,機械工作)
- M 文件的 dashboard / data quality report(管理者體驗)

## 月度例行(使用,非開發)

新 Excel → `/excel-check` → review-template → 人工填寫+簽名(填完備份!)→ validate → apply → sync plan → 人工確認 → execute。
