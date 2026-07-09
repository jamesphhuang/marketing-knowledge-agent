# H. 架構 Review

> 快照性質文件（基於 2026-07-08 review pack）。code 演進後以 code 為準；過期段落依 F 層級一修正。

---

## 總評

對「離線 prototype、驗證 schema 與 workflow」這個階段目標而言，架構**合理且紀律良好**。模組邊界清楚、無外部依賴、read-only 安全邊界有意識地維持。主要問題不是過度設計，而是：(1) governance 是可選參數而非管線必經層（A 風險 #1）；(2) `DocumentMetadata` god-model 開始發臭；(3) 部分「防線」只存在於文件，code 未強制。

## 優點（值得保留的設計決策）

- **excel 流與 markdown RAG 流分離**，excel 側全程只寫 `reports/`，安全邊界正確。
- **governance risk 欄位在 normalize 時就附著在紀錄上**（`excel_ingestion.analyze_merchant_case_governance`），而非事後從 summary 推斷——這是對的方向，弱模型不用重算。
- **無外部依賴的 xlsx 解析**（zipfile + ElementTree）：可控、可測，prototype 階段比引入 openpyxl 更好debug。
- **`NON_RETRIEVABLE_RECORD_TYPES` 在 `matches_filters` 最前面硬擋**——record 層防線放在檢索最底層，位置正確。
- **agentic-lite 只包裝既有 search/generate**，不自己生內容、不繞過 citation——「Agentic 會不會繞過 governance」的答案目前是不會，因為它沒有自己的資料通道。
- 測試覆蓋 governance 關鍵路徑（restricted 不進 citation、channel filter、pending 不可外引），43 passed。

## 風險與問題

### H-1. 模組分層：governance 是「裝飾」不是「層」（高）

`apply_governance_to_answer` 需要呼叫者記得傳 index；`ask_index` 完全沒接。詳見 A 風險 #1。
**改法**：新增 `answer_gateway`（或在 pipeline 內）作為唯一答案出口：載入 denylist → 檢查 citations → 檢查答案文字 → 附 `governance_checked` 旗標。所有 ask 類入口只能經過它。

### H-2. `models.py` god-model（中）

`DocumentMetadata` 60+ 欄位服務 6 種 record_type：restricted 欄位（nda_signed）、metric 欄位（claim_statement）、merchant 欄位（interview_year）全部混在一起。後果：(a) 每種 record 都攜帶 50 個 None 欄位；(b) 弱模型無法從 schema 看出「哪些欄位對這個 record_type 有意義」；(c) validator 的 per-type 規則只能寫成 if record_type ==... 散在各處。
**改法（apply sprint 前不必做，之後建議）**：拆成 `BaseRecordMetadata` + 各 record_type 子模型（Pydantic discriminated union，`record_type` 為 discriminator）。遷移時機：升 Pydantic v2 的同一個 sprint（反正要碰所有 validator）。
**不建議現在做**：目前 46 筆 review 流程跑得動，拆模型是大 diff，會拖慢 J/K sprint。

### H-3. CLI 可維護性（低-中）

`main()` 是 if-chain，尚可。但 subcommand 已有 10 個，選項重複（`_add_retrieval_args` 好），excel 系列與 rag 系列混在同一層。
**改法**：等 apply 命令加入時，把 command dispatch 改成 `{name: handler}` dict；不必引入 click/typer（多依賴不值得）。
**退出碼紀律**已建立（0/1/2），apply 命令要延續：0=成功、1=有 error 級 issue、2=輸入無效。

### H-4. excel_preview / review_template / governance 耦合（中）

具體耦合點：`review_decision_validation.py` import 了 `review_template` 的**私有函式**（`_merchant_review_rows` 等 4 個）來重建 expected rows。這表示「哪些紀錄需要 review」的規則只活在 template 生成邏輯裡，validation 被迫逆向依賴。
**改法**：把「records → review rows」的規則抽成公開函式 `build_expected_review_rows(preview) -> List[dict]`，template 與 validation 都呼叫它。小 patch，建議與 J sprint 一起做。
另外 `excel_preview.py`（703 行）同時做 xlsx 解析、normalize 調度、品質標注、summary 渲染——xlsx 解析（~190 行）可抽成 `xlsx_reader.py`，其餘暫留。

### H-5. SearchFilters 是否足以支援未來場景（中）

夠用於目前 filter 語意（AND across fields, OR within list），但缺三件未來必要的東西：
1. **用途宣告**：查詢無法表達「這次查詢是為了對外用途」。建議未來加 `intent: Literal["internal", "external"]`，external 時自動強制 `can_quote_externally=true` + channel gating（見 L）。
2. **日期範圍 filter**（freshness gating 只能事後 warning，不能事前過濾）。
3. **排除語意**（exclude merchant_status in {...}），目前只有 include。
不建議現在加，記入 L 的路線圖即可。

### H-6. Citation v0.2 是否足夠（中）

欄位足以支撐人工複核（record_type / classification / can_quote / 四種日期 / sheet+row 溯源都在）。缺的是：
1. `allowed_exposure_channels` **不在 citation 上**——對外場景下，複核者看 citation 無法知道渠道限制。建議 v0.3 加入。
2. `governance_warnings` 是 answer 級不是 citation 級——warning 與 citation 的對應靠 label 前綴文字（`"[1] ..."`），程式化消費者（未來 LLM）解析脆弱。建議 citation 加 `warnings: List[str]`。
3. 無 `retrieval_reason`（為何入選）——排查誤引用時需要。低優先。

### H-7. retrieval 的規模天花板（低，需標記）

`search` 每次全量 `load_chunks()` 進記憶體過濾。百級文件沒問題；未來接真資料（千級 chunks）前必須改為 SQL 端過濾。同時 `if score > 0 or not filters.is_empty()` 讓帶 filter 的零分 chunk 也入列——這是刻意（讓純 filter 瀏覽可用）還是意外，建議加註解或拆成 explicit `browse` mode。

### H-8. 未來接外部 LLM 時，哪些 guardrail 必須留在本地邏輯

**原則：LLM 只能出現在「已過濾資料 → 文字」這一段。以下永遠在本地 code、絕不交給 prompt：**

1. record_type 檢索資格（`NON_RETRIEVABLE_RECORD_TYPES`）——在 SQL/檢索層擋，LLM 根本看不到 restricted 資料。
2. denylist 檢查與 citation 阻擋——對 LLM 的**輸出**再跑一次 `check_text`（生成後檢查），因為 LLM 可能從訓練記憶吐出品牌名。
3. `can_quote_externally` / channel gating——檢索前過濾（不是 prompt 裡拜託 LLM 別引用）。
4. citation 的 metadata 組裝——由本地 code 從 chunk metadata 組裝，LLM 只產生答案文字，不產生 citation 欄位值（防幻覺 citation）。
5. warning 生成——規則式，不交給 LLM 改寫或省略。

## 不建議現在做的事

- 拆 `DocumentMetadata`（等 Pydantic v2 遷移一起）
- 引入 openpyxl / pandas / click 等依賴
- SQL 端過濾重構（資料量未到）
- 向量 embedding 換真 provider（先把 governance 接線修好）
- 任何 MCP / Web UI 準備工作

## 下一步 priority（架構面）

1. governance 接線修復（H-1，= A 風險 #1 修法）＋測試
2. `build_expected_review_rows` 抽取（H-4，J sprint 順手做）
3. J spec 實作 → K spec 實作
4. Pydantic v2 遷移 + models 拆分（一個獨立 sprint）
5. Citation v0.3（加 channels）與 L 路線圖第一步
