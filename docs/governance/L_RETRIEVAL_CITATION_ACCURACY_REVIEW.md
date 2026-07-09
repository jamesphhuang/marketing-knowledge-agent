# L. Retrieval / Citation Accuracy Review

> 目標：提升未來使用者自然語言查詢的精準度與安全性。
> 分兩層：**現在就能做**（離線 prototype 內）與 **接外部 LLM 前必須做**（blocker 標記）。
> 相關現狀依據：`retrieval.py` / `reranking.py` / `generation.py` / `agentic.py`。

---

## 1. 改善項目清單

### 1.1 Metadata filtering 改善

- 現狀：AND across fields / OR within list，`matches_filters` 在 Python 層全量過濾。功能正確，缺「用途宣告」。
- 🔧 新增 `SearchFilters.intent: Literal["internal", "external"]`，預設 `internal`：
  - `external` 時自動疊加：`can_quote_externally=true`、`status=["published"]`、`data_classification=["public"]`、排除 record_type=pending_metric。
  - 這是**組合規則寫死在 code**，不靠呼叫者記得帶三個 filter——與 A 風險 #1 同一設計哲學。
- 🔧 filter 值正規化：使用者輸入 brand 名時先過 `normalize_identity` 再比對（目前大小寫敏感度靠 `_normalize_list` 的 lower，中文品牌帶空格會 miss）。

### 1.2 Query routing

- 現狀：`agentic.analyze_question` 關鍵詞路由，脆弱但無害（只影響檢索計畫，不影響 governance）。
- 🔧 低成本改善：把「新聞稿 / 廣告 / saleskit / 對外 / 公開」等詞加入路由 → 觸發 `external` intent + channel 推斷（見 §2 Q1）。**推斷結果必須顯示給使用者**（「已按新聞稿用途過濾」），推錯時使用者能糾正。
- 接 LLM 後：query 理解交給 LLM，但**產出必須是結構化 SearchFilters**，由本地 code 執行過濾（H-8 原則 3）。

### 1.3 record_type gating

- 🔧 依問題型態預設 record_type：問「數據/成效/指標」→ public_metric 優先；問「案例」→ merchant_case + content_asset；未指定 → 全部可檢索型別。作為 rerank bonus 而非硬 filter（避免路由錯誤變成漏檢）。

### 1.4 public_metric 的 channel-aware retrieval【接 LLM 前 blocker】

- external intent + 已知目標渠道 → `exposure_channel=[目標渠道]` 硬 filter。
- 渠道未知 → 不硬過濾，但每筆 public_metric 的 citation 顯示 channels，答案加「使用前確認渠道」提示。

### 1.5 restricted_customer pre-check【接 LLM 前 blocker】

- 查詢文字先過 `GovernanceIndex.check_text`：命中 → 走 §2 Q2 的拒答模板，**不執行檢索**（避免「查不到但系統行為洩漏該品牌存在於名單」——統一模板讓「在名單」與「不存在」外觀一致）。
- 依 A 風險 #1：答案與 citations 出口再各過一次 denylist（三道：query / answer / citation）。

### 1.6 pending_metric suppression

- internal intent：可返回，帶現有 warning（已實作 ✅）。
- external intent：檢索階段直接排除（1.1 的組合規則）。

### 1.7 Freshness warning

- 現狀：540 天門檻 + 未來日期偵測（`freshness_note_for` ✅）。
- 🔧 問題（GR-12）：Excel 紀錄 `publish_date=captured_date`，新抓的舊資料看起來很新。改法：freshness 計算對 public_metric 優先用 `metric_updated_date`（現已透過 last_reviewed 生效，但 serial 日期解析 bug（GR-8）會讓它靜默失效——先修 GR-8）。merchant_case 用 `interview_year` 推估（`date(interview_year, 12, 31)` 作保守下限），比 captured_date 誠實。
- 🔧 分級（與 D 文件 R11 對齊）：**內部**用途 >540 天 → warning（現有行為）；**對外**用途 >540 天 → 需人工重新確認才可用（R11 判準 f）；>900 天或完全無日期 → 一律需人工確認，不分用途。

### 1.8 Citation contract（citation 的消費契約）

寫成規則供未來所有出口遵守：

1. 答案中每個事實句必須可對應至少一個 citation label。
2. citation 欄位由本地 code 從 chunk metadata 組裝；LLM 不得產生任何 citation 欄位值（H-8）。
3. citation 數 = 0 時不得輸出任何事實性陳述（見 1.10）。
4. v0.3 增補：`allowed_exposure_channels`、citation 級 `warnings`（H-6）。

### 1.9 Answer abstention rules（拒答規則）

任一成立 → 不回答事實內容：

| 條件 | 行為 |
| --- | --- |
| 檢索 0 筆 | 「找不到符合資料」+ 建議放寬條件（現有 ✅）+ 列出用了哪些 filter（🔧 新增，讓使用者知道是 filter 太緊還是真沒有） |
| top 結果分數低於門檻（建議 rerank_score < 0.1，需以 eval 校準） | 「相關度不足」+ 顯示最接近的 title 供人判斷 |
| external intent 且過濾後 0 筆但 internal 有 N 筆 | 「有 N 筆內部資料但無可對外引用資料，需人工核准」——這個訊息本身高價值 |
| 查詢命中 denylist | §2 Q2 模板 |

### 1.10 No-result behavior

- 絕不用「常識」補答。mock generator 天然不會；**接 LLM 後這變成 prompt + 後檢查的雙重要求**：無 citation → 強制輸出拒答模板（本地 code 檢查 citations 為空時直接覆蓋 LLM 輸出）。

### 1.11 High-risk query handling

高風險 = 涉及 restricted 品牌、對外用途、或要求「可以直接複製貼上的對外文案」。處理：前兩者見上；第三類在 prototype 階段一律回「本系統提供來源與限制，不產出最終對外文案」——直到 channel gating + 人工複核流程上線。

### 1.12 Evaluation cases（擴充 `evaluation.py`）

現有 eval 覆蓋 citation coverage / filter correctness / warning coverage。🆕 新增 governance eval 集（每條都是 assert 型，弱模型可跑可判）：

| case | 斷言 |
| --- | --- |
| EV-G1 | 內文含 restricted 品牌的 content_asset，ask 後答案與 citations 不含品牌字串 |
| EV-G2 | external intent 查詢，結果 0 筆 pending_metric、0 筆 can_quote=false |
| EV-G3 | 指定 exposure_channel=press_release，返回的 public_metric 全部含該 channel |
| EV-G4 | 查詢 denylist 品牌名，輸出等於統一拒答模板 |
| EV-G5 | 0 檢索結果時，答案不含任何數字型事實 |
| EV-G6 | merchant_status 含「已關店」的紀錄出現時，warnings 非空 |

## 2. 五個指定問題的回答

### Q1：使用者問「可以用在新聞稿的數據」→ 如何 filter？

`record_type=["public_metric"]` + `exposure_channel=["press_release"]` + `can_quote_externally=true` + `status=["published"]`（即 external intent 組合 + 渠道）。回答需逐筆顯示：claim_statement、metric_updated_date（過期警告）、restricted_note（若有 → 標注需先人工確認）、citation 含 source_sheet/row。**missing channels 的 metric 絕不出現**。若結果 0 筆 → 1.9 第三列行為。

### Q2：使用者問「某不可公開品牌」→ 如何回應？

統一模板（不論品牌在不在名單都長一樣的「查不到」外觀是不夠的——內部工具允許提示治理狀態，但不得輸出名單細節）：

> 「此查詢涉及受限制的客戶資訊，無法提供相關內容。若為內部業務需要，請聯繫 <governance owner> 人工確認。」

不輸出：該品牌的 restricted_reason、NDA 狀態、名單其他內容。不執行檢索（1.5）。內部 audit log（M 文件）記錄此次命中供 owner 追蹤。

### Q3：「找一個成功案例」→ 如何排除已關店 / 下架 / 轉競品？

三層：
1. 檢索層：merchant_case 已在 ingestion 附 `governance_risk_reasons`；rerank 對 `governance_risk_reasons` 非空者加**負分**（新規則，建議 -0.15），健康案例自然排前。
2. 呈現層：若風險案例仍入選，warning 已有（✅ `_contains_risk_term`），保留。
3. 語意層：「成功案例」隱含「目前狀態良好」——這個推斷寫進 query routing（案例類查詢預設排除 merchant_status 含風險詞者，除非使用者明說要含）。排除時回報「已排除 N 筆狀態異常案例，可要求包含」。

### Q4：「團購成效」→ 如何區分 public metric / merchant case / pending metric？

不替使用者猜單一型別，**分節返回**：
- 「可對外數據（public_metric）」節：含 channels 與更新日期
- 「商家案例（merchant_case）」節：含素材連結與狀態
- 「待確認數據（pending_metric）」節：只在 internal intent 下出現，且整節掛「不可對外」頭部警語
每節 citation 帶 record_type，使用者一眼可辨。這是呈現規則，generation 層實作（依 record_type 分組 citations）。

### Q5：什麼情況下拒答或要求人工確認？

拒答：denylist 命中（Q2）、0 結果、低相關（1.9）。
要求人工確認：external intent 命中 restricted_note 資料、freshness >900 天、merchant_case 帶 governance risk 卻被指定要對外使用、要求最終對外文案（1.11）。
判準口訣（給弱模型）：**「查不到 → 拒答；查到但有限制 → 給資料+給限制；限制模糊 → 給人工」**。

## 3. 實施順序

1. GR-8（日期）與 GR-1（denylist 接線）——精準度與安全的共同地基
2. intent 組合規則 + 拒答規則（1.1 / 1.9 / 1.10）
3. eval 集 EV-G1–G6（先有測量，後面每步可驗證）
4. rerank 負分 + record_type 分節呈現（Q3 / Q4）
5. channel-aware（Q1）
6. citation v0.3
