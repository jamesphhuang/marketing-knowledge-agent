# B. 長期載入規則：CLAUDE.md / AGENTS.md 建議版

> 原則：主檔只放「每個 session 都必須知道」的 hard constraints 與路由。
> 其餘長規則全部抽成獨立文件，用路由表指過去。
> 寫法標準：弱模型需要明確（可逐字執行），強模型需要留白（不寫過度具體的實作細節綁死判斷）。

---

## 1. 最短可用版 CLAUDE.md / AGENTS.md

以下整段可直接複製為專案根目錄的 `CLAUDE.md`（或 `AGENTS.md`，內容相同）。

```markdown
# Marketing Knowledge Agent — Agent Rules

內部 Marketing knowledge / RAG / governance prototype（離線 Python，不接外部 LLM / MCP / Web UI）。
CLI 入口：`mka`（見 `src/marketing_knowledge_agent/cli.py`）。
測試：`.venv/bin/pytest`。改動 governance / review / excel 相關程式後必須全綠才算完成。

## Hard Constraints（不可違反；違反 = 任務失敗，立即停止並回報）

1. `restricted_customer` 紀錄不得進入一般 content index、向量檢索或 RAG citation。只能進 governance table（denylist）。
2. `pending_metric` 不得對外引用（`can_quote_externally` 永遠 false）。
3. `public_metric` 若 `missing_allowed_exposure_channels=true`，不得對外引用；任何對外用途必須檢查 `allowed_exposure_channels` 包含目標渠道。
4. `handle_mapping` 只能用於身分 normalization / enrichment，不得成為 citation 來源。
5. `suggested_action` 是機器建議，不是人工決定。不得把 suggested_action 複製為 review_decision，不得替使用者填 `review_decision` / `reviewer` / `reviewed_at`。
6. review_decision 未通過 `mka validate-review-decisions`（0 errors）前，不得進入任何 apply 步驟。
7. apply-review-decisions 只能產生 preview 輸出（`reports/` 底下），不得寫入正式 Obsidian vault、不得建立正式 content index、不得刪改原始 Excel。
8. 任何回傳答案（GeneratedAnswer）的程式路徑，必須經過 `apply_governance_to_answer`，並保留 citation / freshness / governance warning。不得為了輸出乾淨而移除 warning。
9. `same_brand_multiple_records` / `same_handle_multiple_records` 是資訊性標記，不是 duplicate。`suspected_duplicate_review` 不得自動刪除 / 合併 / 覆蓋——只能標記給人工。
10. 不得處理 raw production data（真實 workbook、真實 restricted 名單）到任何會離開本機的輸出（報告、commit、對外文件）。引用時一律用 sanitized 樣本或計數。

## 需要人類確認才能做（先停下來問）

- 修改 review_decision enum、governance 規則、hard constraints 本身
- 任何寫入 Obsidian vault、正式 index 的操作
- 修改/刪除 `reports/excel_preview/` 下已由人工填寫的 decision CSV
- 對外發布任何內容

## 文件路由（按需讀取，不要全部載入）

| 你要做的事 | 先讀 |
| --- | --- |
| 任何 governance / review 判斷 | `docs/governance/GOVERNANCE_RULES.md` + `docs/governance/D_JUDGMENT_RUBRICS.md` |
| 修 validator / 加驗證規則 | `docs/governance/J_REVIEW_DECISIONS_VALIDATION_SPEC.md` |
| 實作或修改 apply-review-decisions | `docs/governance/K_APPLY_REVIEW_DECISIONS_PREVIEW_SPEC.md` |
| retrieval / citation / 回答精準度 | `docs/governance/L_RETRIEVAL_CITATION_ACCURACY_REVIEW.md` |
| 派工 / 選模型 / 升降級 | `docs/governance/C_MODEL_ROUTING_PLAYBOOK.md` + `E_DELEGATION_PROMPTS.md` |
| 修改制度文件本身 | `docs/governance/F_MAINTENANCE_PROTOCOL.md` |
| 架構層改動（拆模組、動 models.py） | `docs/governance/H_ARCHITECTURE_REVIEW.md` |
| 已知風險與踩坑紀錄 | `docs/governance/LESSONS.md` |
| 接手新 session / 不知從何開始 | `docs/governance/G_LETTER_TO_FUTURE_SESSIONS.md` |

## 工作紀律

- 動手前先跑 `pytest` 確認基線是綠的；結束前再跑一次。
- Enum 的 canonical source 是 code（`ALLOWED_REVIEW_DECISIONS`、`models.py` 的 Literal）；文件與 code 不一致時，以 code 為準並回報不一致，不要兩邊亂改。
- 修完 bug 必須加對應測試；governance 相關改動的驗收不得只靠自己讀自己的 diff（見 C 文件「驗證不自驗」）。
- 每次踩坑，把教訓按格式寫進 `docs/governance/LESSONS.md`（格式見 F 文件）。
```

（假設制度文件放在 `docs/governance/`；若放其他位置，路由表路徑同步改。）

---

## 2. 文件路由表（完整版，含本包全部文件）

| 文件 | 放置建議 | 角色 |
| --- | --- | --- |
| `CLAUDE.md` / `AGENTS.md` | repo 根目錄 | 每 session 自動載入；只放上面第 1 節內容 |
| `GOVERNANCE_RULES.md` | `docs/governance/` | governance 規則正文（現有檔案，維持） |
| `A_QUICK_DIAGNOSIS.md` | `docs/governance/` | 風險診斷基礎，其他文件引用 |
| `C_MODEL_ROUTING_PLAYBOOK.md` | `docs/governance/` | 派工與模型調度 |
| `D_JUDGMENT_RUBRICS.md` | `docs/governance/` | 判斷力 rubric |
| `E_DELEGATION_PROMPTS.md` | `docs/governance/` | 派工模板 |
| `F_MAINTENANCE_PROTOCOL.md` | `docs/governance/` | 制度文件維護協議 |
| `G_LETTER_TO_FUTURE_SESSIONS.md` | `docs/governance/` | 交接信 |
| `H_ARCHITECTURE_REVIEW.md` | `docs/governance/` | 架構 review（快照性質，會過期，見 F） |
| `I_GOVERNANCE_RISK_REVIEW.md` | `docs/governance/` | 風險登記簿（活文件，持續更新） |
| `J_REVIEW_DECISIONS_VALIDATION_SPEC.md` | `docs/specs/` | 下個 sprint 規格 |
| `K_APPLY_REVIEW_DECISIONS_PREVIEW_SPEC.md` | `docs/specs/` | 下下個 sprint 規格 |
| `L_RETRIEVAL_CITATION_ACCURACY_REVIEW.md` | `docs/governance/` | 檢索精準度路線圖 |
| `M_ADMIN_USABILITY_REVIEW.md` | `docs/governance/` | 管理者體驗改善清單 |
| `LESSONS.md` | `docs/governance/` | 踩坑紀錄（新建空檔，格式見 F） |

## 3. 哪些規則放主檔（CLAUDE.md）

判準：**「任何任務都可能踩到」+「違反的代價不可回復」**。符合兩者才進主檔。

- 10 條 hard constraints（全部與資料外洩 / 危險自動化有關）
- 「需要人類確認」清單（4 項）
- 文件路由表
- 工作紀律 4 條（測試基線、enum canonical source、驗證不自驗、教訓回寫）

## 4. 哪些規則放引用檔

判準：**「只有特定任務才需要」或「內容超過 10 行」**。

- review_decision 每個值的完整定義與衝突規則 → GOVERNANCE_RULES.md + J
- apply 的輸出 mapping → K
- 模型升降級路徑、派工模板 → C、E
- rubric 正反例 → D
- 檢索 filter 策略、拒答規則 → L

反例（不要做的事）：把 J 的 20+ 條 conflict rules 塞進 CLAUDE.md。弱模型每次載入都花 token 讀它，但 95% 的任務用不到；且主檔越長，hard constraints 越容易被淹沒。

## 5. 哪些舊規則應移除 / 修正

| 現況 | 處置 |
| --- | --- |
| 使用者 prompt / 舊筆記中「Review Decisions Validation Sprint 尚未完成」 | 已完成（read-only validator 已上線，43 tests passed）。任何地方仍寫「未完成」都要更新，避免弱模型重做已完成的 sprint |
| GOVERNANCE_RULES.md enum 表缺 `review_identity_mapping` | 補上（需使用者先確認語意），或從 code 移除該值。二選一，不能維持現狀 |
| ARCHITECTURE.md 的 CLI 表 | 每加一個 CLI command 同步更新（列入 F 的一致性檢查） |
| PROMPT_FOR_FABLE5.md、FABLE5_REVIEW_QUESTIONS.md | 一次性文件，review 完成後歸檔到 `docs/archive/`，不進路由表 |

## 6. 不可違反的 hard constraints（最終版清單）

即第 1 節的 10 條。設計說明：

- 條 1–4 直接對應四種特殊 record_type 的資料外洩防線。
- 條 5–6 保護 human-in-the-loop（對應 A 風險 #2）。
- 條 7 是「preview 與正式資料的邊界」——這是整個專案的安全模型核心。
- 條 8 對應 A 風險 #1（governance 必過、warning 必留）。
- 條 9 保護多次訪談資料不被自動去重（GOVERNANCE_RULES.md 禁止行為的濃縮）。
- 條 10 是資料衛生：raw production data 不離機。

每條都寫成「可判定違反與否」的形式：弱模型檢查自己的輸出時，逐條問「我有沒有做這件事」，答案只有是/否。
