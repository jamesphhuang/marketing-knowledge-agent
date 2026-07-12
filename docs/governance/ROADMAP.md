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

## Stage 2:查詢閘門(GR-9 關閉) ✅(2026-07-10 完成)

- **目標**:系統能區分 internal / external 用途並據此過濾;拒答規則上線。
- **Spec**:`docs/specs/P_QUERY_GATING_SPEC.md`(2026-07-10 已實作)。依據:L 文件 §1.1/§1.5/§1.9/§1.12。
- **前置**:Stage 1。
- **結果**:EV-G1~G6 全綠；internal / external 查詢共用 `pipeline.search_index` gating 收口；真實 index smoke 為 internal 12 / external 7；denylist 查詢在檢索前統一拒答且 audit 不記查詢原文；無結果會顯示實際 filters。
- **殘留**:Stage 3 接 LLM 後，LLM 輸出仍須再過 denylist（H §8），並重新驗證 GR-9。

## Stage 3:外部 LLM 接入 ✅(2026-07-11 程式完成，政策鑰匙維持關閉)

- **目標**:自然語言理解與回答生成交給外部 LLM,governance 全部留在本地。
- **Spec**:`docs/specs/Q_LLM_INTEGRATION_SPEC.md`(2026-07-11 已實作)。依據:H 文件 §8 五原則。
- **前置**:Stage 2 ✅。
- **政策狀態**:公司 AI 規範尚在確認中(2026-07-10)→ spec 以「雙鑰設定閘門」處理:預設 mock provider 完全離線;`data_policy_confirmed` + `allow_internal_data_to_llm` 兩鑰皆需人工開啟,未開啟前程式強制不外送任何資料。開發與政策確認可平行進行,政策落地後改設定即啟用(手續見 spec §7)。
- **結果**:provider abstraction、Anthropic injectable transport、payload 最小化、dry-run、生成後 denylist、幻覺 citation label 檢查皆完成；全部測試與 smoke 均未呼叫真實外部 API。
- **殘留**:公司政策確認、model 選定、API key 設定與首次真實呼叫驗收仍未執行；完成前外部 LLM 保持停用。

## Stage 4:Slack 介面(終點) ✅ 程式完成(2026-07-11;部署驗收待使用者,見 S spec §6)

- **目標**:Slack bot 對話取用。
- **Spec**:`docs/specs/S_SLACK_INTERFACE_SPEC.md`(2026-07-11 已寫)。
- **前置**:Stage 3 ✅(不依賴 LLM 政策鑰匙——mock provider 可先上,政策確認後改設定即升級)。
- **政策裁決(使用者 2026-07-11 全數確定)**:(a) Slack 一律只回可對外內容(強制 external intent,Slack 端不可改);(b) 指定頻道白名單 only(含 DM 拒絕);(c) denylist 命中拒答+audit、不通知(留設定欄位);(d) 回覆進 thread,citations/warnings 永不截斷。
- **硬性要求**:Slack 層是薄介面,全部 governance 在既有本地模組;tokens 只從環境變數;governance/pipeline 模組不得 import slack 套件(import 邊界測試)。

## 平行小任務(不佔階段,隨時可插隊)

- GR-6:括號別名污染 denylist(alias 拆分規則)
- ~~GR-11~~ ✅ 已關閉(2026-07-11 使用者裁決:採納暫行定義)
- Pydantic v2 遷移 + models.py 拆分(獨立 sprint,機械工作)
- M 文件的 dashboard / data quality report(管理者體驗)

## 月度例行(使用,非開發)

新 Excel → `/excel-check` → review-template → 人工填寫+簽名(填完備份!)→ validate → apply → sync plan → 人工確認 → execute。
