# Q. External LLM Integration Spec(ROADMAP Stage 3)

> 目標:自然語言回答生成交給 LLM,**governance 全部留在本地**(H 文件 §8 五原則的實作)。
> 政策前提:公司 AI 使用規範**尚在確認中**(2026-07-10)。因此本 spec 的核心設計是「**預設完全離線,外部供應商由雙鑰設定閘門控制**」——全部開發與測試不需要、也不得呼叫外部 API;政策確認後由使用者改設定啟用,零程式變更。
> 執行等級:standard-coding。無阻塞性待決事項(政策問題已轉化為設定閘門)。

---

## 0. 角色邊界(H §8,LLM 只做一件事)

```
使用者問題 → [本地] gating 檢索(P sprint,已完成)→ 過濾後的 chunks
→ [LLM] 依 chunks 生成回答文字 ← LLM 的唯一職責
→ [本地] 輸出 denylist 掃描 → [本地] citation 組裝 → [本地] warnings → 答案
```

LLM **永遠不做**:產生 citation 欄位值、決定哪些資料可用、改寫或省略 warning、在檢索為空時回答。這些全在本地 code,測試可完全離線驗證。

## 1. Provider 抽象

```python
class LLMProvider(Protocol):
    name: str
    def generate(self, prompt: str) -> str: ...
```

| Provider | 行為 | 預設 |
| --- | --- | --- |
| `mock` | 現有規則式 generator 的行為(重用 `generate_answer` 的組稿邏輯),零網路 | ✅ 預設 |
| `anthropic` | 呼叫 Anthropic Messages API(用 stdlib urllib,不新增依賴;model id 由 config 指定,實作時查當前官方文件確認可用 model,不要憑記憶寫死) | 需雙鑰啟用 |

擴充其他供應商 = 新增一個 class,不改管線。

## 2. 雙鑰設定閘門(政策未確認前的硬保險)

設定檔:`.mka/llm_config.json`(**加入 .gitignore**;API key 只從環境變數讀,永不落檔):

```json
{
  "provider": "mock",
  "model": null,
  "data_policy_confirmed": false,
  "allow_internal_data_to_llm": false
}
```

- **鑰匙 1 `data_policy_confirmed`**:`false`(預設)時,任何非 mock provider 一律拒絕啟用,錯誤訊息明示「公司 AI 資料政策未確認,外部 LLM 停用」。這個值只能由人工編輯設定檔改變。
- **鑰匙 2 `allow_internal_data_to_llm`**:`false`(預設)時,即使外部 provider 已啟用,**送出的 payload 只含 `data_classification == "public"` 的 chunks**——internal 分類內容(如 approve_internal_only 案例)在本地就被剔除,並在答案 warnings 註明「N 筆內部資料未送外部模型」。政策確認允許後才可改 true。
- 程式碼中不得有任何繞過這兩鑰的路徑(測試驗證)。

## 3. Payload 最小化與透明

- 送 LLM 的內容 = 問題 + 過濾後 chunks 的文字與 title,**不含** source_path、reviewer、內部欄位。
- `--show-llm-payload` 旗標(或 `mka ask --dry-run-llm`):不呼叫任何 provider,把「將送出的完整 payload」印出——政策審查時你可以拿這個給法務/資安看「到底送了什麼」。
- Prompt 模板常數化(版本註記),指示 LLM:只依提供來源回答、用 `[n]` 標註依據、資訊不足就說不足。**但 prompt 不是防線**——防線是 §4 的本地後檢查。

## 4. 生成後本地檢查(依序,全部在本地)

1. **檢索為空 → 不呼叫 LLM**:直接走 P 的 abstention 訊息(省 token 也防幻覺)。
2. **輸出 denylist 掃描**:LLM 回答文字過 `GovernanceIndex.check_text`(LLM 可能從訓練記憶吐出 restricted 品牌,即使 payload 乾淨)→ 命中即 redact + warning(重用 GR-1 機制)。
3. **Citation 本地組裝**:沿用現有 Citation 建構(從 chunks 的 metadata),LLM 文字中的 `[n]` 標籤僅作對照;LLM 提到不存在的 `[n]` → 該標籤替換為「(無對應來源)」+ warning。
4. **無 grounding 拒答**:LLM 輸出若在 citations 為空的情況下仍含事實性內容(理論上不會發生,因為空檢索不呼叫)→ 保險絲:覆蓋為 abstention 訊息。
5. 既有 warnings / freshness / `governance_checked` 機制原樣保留在最終答案上。

## 5. CLI

- `mka ask` / `mka agent-ask` 加 `--provider {mock,anthropic}`(預設 mock,= 現狀零回歸)與 `--dry-run-llm`。
- audit log:每次非 mock 呼叫追加一行(timestamp、provider、model、payload chunk 數、internal 剔除數;**不記 payload 內容**)。

## 6. 測試 DoD(全部離線;外部 provider 用注入的假 transport 測,不打真 API)

- [ ] 雙鑰:`data_policy_confirmed=false` + provider=anthropic → 啟動即拒絕(訊息含政策字樣);`allow_internal_data_to_llm=false` → payload 無任何 `data_classification!=public` 內容(fixture 含 internal chunk,斷言被剔除+warning)
- [ ] 假 transport 驗證:anthropic provider 的 HTTP 層可注入(constructor 注入 callable),測試斷言「政策鑰匙關閉時 transport 從未被呼叫」
- [ ] LLM 輸出含 restricted 品牌(mock provider 故意吐出)→ redact + warning(EV-L1)
- [ ] 空檢索 → provider.generate 從未被呼叫(EV-L2)
- [ ] payload 不含 source_path / reviewer 欄位、只含 gated chunks(EV-L3)
- [ ] 幻覺標籤 `[99]` → 替換 + warning
- [ ] `--dry-run-llm` 不觸發任何 provider 呼叫
- [ ] mock provider 預設下,全部既有測試零回歸(137 個不得紅)
- [ ] Real-data smoke(離線):`mka ask --provider mock` 對真實 index 行為與現狀一致;`--dry-run-llm --intent external` 印出的 payload 經人工檢視只含可外引內容,internal intent + `allow_internal_data_to_llm=false` 時 payload 顯示 internal 剔除計數
- [ ] 全套 pytest 全綠;不改既有測試斷言

## 7. 政策確認後的啟用手續(寫給未來,不屬本 sprint)

1. 使用者確認公司規範 → 決定供應商與允許的資料分類。
2. 編輯 `.mka/llm_config.json`:`data_policy_confirmed: true`、`provider`、`model`(查當時官方文件的現行 model id);視政策決定 `allow_internal_data_to_llm`。
3. 設環境變數 API key。
4. 跑一次 `--dry-run-llm` 人工檢視 payload → 再跑真呼叫 → 對照 EV-G/EV-L 全綠 → GR-9 的「LLM 輸出檢查」項在 register 標記驗收。

## 8. 明確不做

不做 Slack(Stage 4)、不做串流輸出、不做多輪對話記憶、不做 embedding 供應商替換、不快取 LLM 回應、不動 gating/index/sync 既有邏輯(只在其後串接)。

---

*規格作者:Fable 5(2026-07-10)。§2 雙鑰與 §4 後檢查屬 governance 防線(F 層級二),修改前問使用者。*
