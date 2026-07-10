# P. Query Gating Spec(ROADMAP Stage 2 — 關閉 GR-9)

> 目標:系統能區分查詢的**用途**(internal / external),external 用途自動收緊過濾;拒答規則上線;denylist 品牌查詢在檢索前被攔截。
> 依據:L 文件 §1.1(intent)、§1.5(pre-check)、§1.9(abstention)、§1.12(EV eval);D 文件 R11/R12。
> 這是接外部 LLM(Stage 3)前的硬性 blocker——gating 必須存在於本地 code,之後 LLM 只是把自然語言翻成 SearchFilters,規則不交給 prompt。
> 執行等級:standard-coding。無使用者待決事項。

---

## 0. 範圍

**做**:intent 欄位與組合規則、查詢級 denylist pre-check、拒答(abstention)規則、external 出口斷言、EV-G1~G6 自動化測試、CLI `--intent`。
**不做**(明確排除):rerank 的 governance 負分與 record_type 分節呈現(L §2 Q3/Q4,列為後續小任務)、外部 LLM、改 SQLiteIndex schema、改 excel/review/apply/sync 流程。

## 1. `SearchFilters.intent`

- 新欄位:`intent: Literal["internal", "external"] = "internal"`。
- 必須進 `as_dict()` 並能經 `SearchFilters(**dict)` round-trip(agentic 的 plan 用 dict 傳遞 filters,漏掉就會在 agent 路徑靜默失效——加 round-trip 測試)。
- normalize:字串大小寫不敏感(`"External"` → `"external"`)。

## 2. External intent 組合規則(寫死在 code,單一收口點)

新增 `apply_intent_gating(filters: SearchFilters) -> SearchFilters`,在 **`pipeline.search_index` 內**呼叫(唯一收口,search / ask / agent-ask 全部繼承;不要分散到各入口)。

`intent == "external"` 時強制疊加(不覆蓋使用者已給的更嚴條件):

| 欄位 | 強制值 | 理由 |
| --- | --- | --- |
| `can_quote_externally` | `True` | 對外引用底線 |
| `status` | `["published"]` | 非 published 不可對外(若使用者已指定 status,取交集;交集為空 → 视同無結果) |
| `data_classification` | `["public"]` | internal/restricted 分類不可對外 |
| record_type 排除 | `pending_metric` 不得出現 | GR-9 主體。實作:external 時把 pending_metric 加入不可檢索(在 `matches_filters` 的 record_type 檢查處理,或 gating 函式內把它從允許集合剔除) |

`intent == "internal"`:行為與現狀完全相同(零回歸)。

**縱深防禦(出口斷言)**:`ask_index` / `agent_ask` 在回傳前,若 `intent=="external"`,斷言所有 citations 皆 `can_quote_externally==true` 且 record_type ≠ pending_metric;違反 → 移除該 citation + 加 warning「已移除 N 筆不符對外資格的來源」(不拋例外——斷言是保險絲,不是主防線)。

## 3. 查詢級 denylist pre-check(L §1.5)

- 位置:`ask_index` / `agent_ask` / CLI `search`,在**執行任何檢索之前**,對查詢字串跑 `governance_index.check_text`。
- 命中 → 不執行檢索,回傳統一拒答(`GeneratedAnswer`,citations 空、`governance_checked=True`):

  > 「此查詢涉及受限制的客戶資訊,無法提供相關內容。若為內部業務需要,請聯繫管理者人工確認。」

  文案定為常數 `RESTRICTED_QUERY_REFUSAL`,不隨查詢內容變化(不回顯品牌名、不透露名單細節)。
- `search` CLI 需補 `--restricted-customers` 選項(與 ask 相同預設路徑);命中時印拒答文案並 exit 0(不是錯誤,是正確行為)。
- **Audit**:命中即在 `reports/audit_log.csv` 追加一行(timestamp、command、`denylist_query_hit`、match 計數;**不記查詢原文**——原文可能就是品牌名,log 不該複製它)。
- denylist 未載入(檔案不存在):pre-check 跳過,沿用既有 missing-denylist warning 機制(GR-1 已實作),不新增行為。

## 4. Abstention(拒答/降級)規則(L §1.9)

在 `generate_answer` 或其呼叫端實作,規則常數化:

| 條件 | 行為 |
| --- | --- |
| 檢索 0 筆 | 既有「找不到」訊息 + 🆕 列出本次生效的 filters(含 intent 與 gating 疊加後的實際條件)——讓使用者知道是條件太緊還是真沒有 |
| top rerank_score < `MIN_RELEVANCE_SCORE`(常數,暫定 0.1,註明「待 eval 校準」) | 回「相關度不足」+ 列出最接近的前 3 個 title 供人判斷,不產生事實性回答內容 |
| `intent=external` 且結果 0 筆,但同查詢 internal 有 N 筆 | 訊息:「有 N 筆內部資料但無可對外引用版本,對外使用需人工核准」(需做第二次 internal 查詢取得 N;只在 external 0 筆時執行,成本可接受) |

## 5. EV governance eval(L §1.12 → pytest 自動化)

新增 `tests/test_governance_evals.py`,fixture 用合成 vault(自建,含:提到 restricted 品牌的 content_asset、pending_metric、缺 channel 的 public_metric、含 channel 的 public_metric、merchant_status 含「已關店」的 merchant_case)。每條獨立測試:

| 測試 | 斷言 |
| --- | --- |
| `test_ev_g1_restricted_brand_scrubbed` | 內文含 restricted 品牌的來源,ask 後答案與 citations 不含品牌字串(GR-1 既有防線的回歸錨點) |
| `test_ev_g2_external_intent_excludes_unquotable` | external intent:結果 0 筆 pending_metric、0 筆 can_quote=false |
| `test_ev_g3_channel_filter_returns_only_matching` | exposure_channel=press_release:回傳的 public_metric 全含該 channel |
| `test_ev_g4_denylist_query_refused` | 查 denylist 品牌 → 輸出 == RESTRICTED_QUERY_REFUSAL、citations 空、audit log 有記錄 |
| `test_ev_g5_no_result_no_fabrication` | 0 結果時答案不含任何數字型事實(斷言不含 `\d+%` 等 pattern) |
| `test_ev_g6_merchant_risk_warns` | 「已關店」紀錄入選時 warnings 非空(既有行為錨點) |

## 6. CLI

- `search` / `ask` / `agent-ask` 加 `--intent {internal,external}`,預設 internal。
- `search` 加 `--restricted-customers`(§3)。
- 說明文字明示:「external = 只回可對外引用內容」。

## 7. 測試 DoD

- [ ] §1 round-trip 測試(含經 agentic plan dict 往返)
- [ ] §2 每條疊加規則各有測試;internal intent 零回歸測試(同查詢 internal 結果數不因本 sprint 改變)
- [ ] §2 出口斷言測試(手工構造違規 citation → 被移除+warning)
- [ ] §3 pre-check:ask / agent-ask / search 三路徑各一測試;拒答文案常數;audit log 行存在且不含查詢原文
- [ ] §4 三條 abstention 各有測試
- [ ] §5 六條 EV 全綠
- [ ] 全套 pytest 全綠;不改既有測試斷言
- [ ] Real-data smoke(對 `.mka/content_index.sqlite`,帶 --restricted-customers):
  1. internal intent 廣查詢 → 可命中 12 篇範圍(現狀)
  2. **external intent 同查詢 → 只見 7/12**(4 merchant + 3 public;5 篇 internal-only 隱形)——此數字已預先從真實 index 算出,不合即 bug
  3. 查一個 denylist 品牌 → 拒答文案 + audit log 新行
  4. 一個無結果查詢 → 訊息列出生效 filters
  回報貼計數與訊息,不貼品牌名

## 8. 完成後

- risk register GR-9:狀態改「已修(P sprint)——離線層 gating 完成;Stage 3 接 LLM 時需對 LLM 輸出再過 denylist(H-8),該項留在 Stage 3 驗收」。
- ROADMAP Stage 2 標記完成。

---

*規格作者:Fable 5(2026-07-10)。external 疊加規則屬 governance 語意(F 層級二),修改前問使用者。*
