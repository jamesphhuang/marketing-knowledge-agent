# S. Slack Interface Spec(ROADMAP Stage 4 — 終點)

> 目標:使用者在 Slack 指定頻道 @bot 提問,獲得帶 citation、freshness、governance warning 的回答。
> 核心架構原則:**Slack 層是薄介面**——它只做「收訊息 → 呼叫既有 pipeline → 排版回覆」。所有 governance(gating、denylist、abstention、LLM 政策鑰匙)都發生在既有本地模組,Slack 層不得新增、複製或繞過任何治理邏輯。
> 使用者政策裁決(2026-07-11,全數確定):見 §1。
> 執行等級:standard-coding。

---

## 0. 前置與依賴

- Stage 1~3 已完成 ✅。**不依賴 LLM 政策鑰匙開啟**:provider 沿用 `llm_config`(mock = 規則式回答,照樣有 citation;政策確認後改設定即自動升級為 LLM 回答,Slack 層零改動)。
- **新依賴裁決**:Slack 連線允許引入 `slack-bolt`(+`slack_sdk`)——這是專案第一個核准的外部執行依賴,理由:Socket Mode 需 websocket,stdlib 不可行。**依賴只准出現在 Slack 介面模組**,不得滲入 governance / pipeline 模組(測試驗證 import 邊界)。
- 使用者側前置(部署時才需要,開發不需要):在 Slack workspace 建 App、取得 bot token + app token(Socket Mode)、決定啟用頻道 ID。

## 1. 政策(使用者已裁決,寫死為預設,改變屬 F 層級二)

| # | 政策 | 實作 |
| --- | --- | --- |
| a | **Slack 一律只回可對外內容** | 每個 Slack 查詢強制 `intent="external"`(寫死,Slack 端無任何參數可改)。P gating 保證 internal-only / pending / 非 public 內容在檢索層就被濾除——內部資料**物理上到不了** Slack。若查詢在 external 下無結果但 internal 有,回覆 P 既有的「有 N 筆內部資料,需人工核准」訊息並引導走內部工具 |
| b | **指定頻道 only** | 設定檔維護頻道 ID 白名單;非白名單頻道(含 DM)的訊息 → 簡短回覆「此頻道未啟用行銷知識查詢」+ audit 一行,不執行檢索 |
| c | **denylist 命中:拒答 + audit,不通知** | 沿用 P 的統一拒答模板與 audit 規則(不記查詢原文);不做 DM 通知(未來要加,改設定即可,先留欄位 `notify_owner_on_denylist: false`) |
| d | **citation 呈現(設計裁決)** | 回覆一律進 **thread**(不刷頻道);格式見 §3;**warnings 與 citations 永不因長度被裁切**——超長時裁切答案本文並註明,治理資訊完整保留 |

## 2. 架構

```
Slack(Socket Mode)→ slack_interface.py(薄層)
  ├─ 頻道白名單檢查(§1b)
  ├─ 呼叫 pipeline.agent_ask(question, intent="external",
  │     restricted_customers=預設路徑, provider=llm_config)
  │     ↑ 全部治理在這裡面發生(P gating、denylist pre-check、
  │       LLM 雙鑰、輸出 denylist、citation 本地組裝)
  ├─ 排版(§3)
  └─ audit(§4)
```

新模組:`src/marketing_knowledge_agent/slack_interface.py` + 設定 `.mka/slack_config.json`(**加 .gitignore**;tokens 只從環境變數 `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` 讀,永不落檔、不出現在 log/錯誤訊息):

```json
{
  "allowed_channel_ids": [],
  "notify_owner_on_denylist": false,
  "max_answer_chars": 2500
}
```

CLI:`mka slack-bot`(啟動;`allowed_channel_ids` 為空 → 拒啟,訊息引導先設定頻道)。

## 3. 回覆格式(Slack thread 內)

```
{答案本文,超過 max_answer_chars 則截斷並附「(內容過長已截斷,完整結果請用內部工具查詢)」}

📚 來源:
[1] {title} — {source_sheet} r{source_row} · {effective_date} · 可對外引用
[2] ...

⚠️ 提醒:
- {每條 warning 原样保留}
```

規則:citations / warnings 區塊**永不截斷**(§1d);無結果與拒答直接用 pipeline 回傳的訊息文字;所有文字來自 `GeneratedAnswer`,Slack 層不改寫、不省略、不加料。

## 4. Audit

每次互動在 `reports/audit_log.csv` 追加:timestamp、`slack_qa` 或 `slack_denied_channel` 或 `denylist_query_hit`(沿用 P)、channel_id、user_id、citation 數、warning 數。一般問答**記查詢原文**(內部營運紀錄,log 在 gitignored 的 reports/);denylist 命中**不記原文**(P 既有規則)。

## 5. 測試 DoD(全部離線;Slack client 注入假物件,不連真 Slack)

- [ ] handler 純函式化:輸入 event dict → 輸出回覆 dict,單元可測
- [ ] 白名單:非白名單頻道 / DM → 拒絕訊息 + audit,`agent_ask` 未被呼叫
- [ ] **external 強制**:fixture 含 internal-only 內容,Slack 查詢永遠拿不到(斷言 citations 全部 can_quote_externally=true)——此為 §1a 的直接驗證
- [ ] Slack 端無任何輸入可改變 intent(嘗試在訊息文字夾帶「--intent internal」→ 視為普通文字,不解析)
- [ ] denylist 查詢 → 拒答模板 + audit(不含原文)+ 無通知
- [ ] 截斷:超長答案 → 本文截斷 + citations/warnings 完整保留(斷言兩區塊逐字齊全)
- [ ] tokens 缺失 → 啟動即失敗,錯誤訊息不含 token 值;log 掃描無 token
- [ ] import 邊界:governance / pipeline 模組不 import slack 相關套件(靜態測試)
- [ ] 全套 pytest 全綠;不改既有測試斷言
- [ ] Real smoke(離線):以假 client 餵三個事件(正常查詢 / denylist / 非白名單頻道),貼出三個回覆 dict 的結構(不貼品牌名)

## 6. 部署驗收(實作 merge 後、由使用者與主模型一起做,不屬 Codex sprint)

1. 使用者建 Slack App(Socket Mode)、拿 tokens、建測試頻道並將頻道 ID 填入設定。
2. `mka slack-bot` 啟動,在測試頻道實測:正常查詢(mock provider)、denylist 查詢、非白名單頻道。
3. 檢查 audit log 三種事件齊全。
4. 通過後才邀請其他成員進頻道。LLM 政策確認後改 `llm_config` 即升級回答品質,Slack 層不動。

## 7. 明確不做

多輪對話記憶、slash commands、DM 支援、訊息編輯/刪除同步、denylist DM 通知(留設定欄位)、Web UI、任何 governance 邏輯的複製(必須呼叫既有模組)。

---

*規格作者:Fable 5(2026-07-11)。§1 政策表為使用者裁決,修改屬 F 層級二。*
