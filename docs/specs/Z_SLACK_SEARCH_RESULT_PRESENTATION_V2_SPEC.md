# Z. Slack Search Result Presentation v2(Human UAT Remediation Sprint)

> 來源:2026-08-19 真實 Human UAT 回饋。
> 目標:讓 Slack 搜尋結果更精簡、更容易點擊,並提高單次可瀏覽的品牌數量。
> 性質:**presentation / pagination UX change**,不是 retrieval semantics change。
> 這份規格只描述 production Slack renderer(`slack_presentation.py`),
> 不涵蓋離線 preview renderer(`docs/specs/X_SLACK_OUTPUT_RENDERER_PREVIEW_SPEC.md`),兩者互不引用。

---

## 1. Human UAT 需求(逐條)

| # | 使用者要求 |
| --- | --- |
| A | Slack 使用者可見輸出不再顯示:上線日期、採訪年份、狀態、對外引用、資料來源 |
| B | 不再另外顯示「連結：開啟連結」,改成標題本身直接可點擊 |
| C | 一次最多展示的品牌／夥伴數由 5 提高到 15 |
| D | 超過 15 個時顯示明確提示,並支援在同一 thread 回覆「@Marketing Knowledge Agent 顯示更多」繼續查看 |

## 2. Old vs New Output Contract

舊(每個 asset 八行):

```text
> • *文章 [1]*
> 標題：{title}
> 連結：<{url}|開啟連結>
> 上線日期：{published_at}
> 採訪年份：{interview_year}
> 狀態：{status}
> 對外引用：{external_usage}
> 資料來源：{source_sheet} r{source_row}
```

新(每個 asset 兩行):

```text
> • *文章 [1]*
> <{approved_url}|{title}>
```

無 approved URL 時:

```text
> • *文章 [1]*
> {title}
```

品牌 heading、Handle / Sales Category 三行、blockquote 樣式、asset 型別排序與全域編號
`[1] [2] [3]…` 一律沿用既有 presentation style,本 sprint 不動。

## 3. Hidden ≠ Removed

被隱藏的五個欄位**只離開 Slack 使用者可見輸出**。以下全部維持原狀:

- `StructuredAsset` / `StructuredEntity` / `Citation` 的欄位與型別;
- `can_quote_externally`、`allowed_exposure_channels`、`data_classification`、
  `status`、`source_sheet` / `source_row` 等 governance 與 provenance 欄位;
- `slack_presentation._asset_candidate` 仍解析 `published_at`、`interview_year`、
  `status`、`external_usage`、`source`,並仍以它們做 `_completeness` 排序與
  `資料不一致` 衝突判定;
- 資料庫 schema、content index、asset metadata authority、CLI
  `structured_results.render_structured_result` 全部未改。

`external_usage` 的計算仍是 asset 是否進入輸出的**前提**:
`_presentation_entities` 對每個 asset 呼叫 `_citation_is_written_safe`,不通過就不顯示。
UI 不再印出這個標籤,不代表這道閘門消失。

## 4. Clickable Title Contract

- URL 來源只有一個:既有 approved URL authority
  (`slack_output_preview.apply_approved_asset_url_overlay`,join identity 含 asset_type)。
  本 sprint **未修改** authority、overlay、identity 或 index binding。
- Article URL 只會落在 article asset,Video URL 只會落在 video asset —— 這是 authority
  的 identity 保證,renderer 只是把 asset 已經持有的 URL 放到自己的 title 上。
- Renderer 端的採用條件與舊的「連結」行完全相同:先對 raw value 跑
  `url_is_mrkdwn_safe`,再 canonicalize,再對 canonical form 複驗。任何一關失敗就是純文字 title。
- 以下情形一律**純文字 title,不產生任何 link construct**:
  無 approved URL、URL 與 title-URL 衝突(`資料不一致`)、多列來源 URL 不一致、
  URL 含控制字元 / entity reference / 反斜線、非 http(s)、含帳密、port 非法。
- 永不:猜 URL、用 title 推 URL、web 搜尋補 URL、fallback 到 merchant homepage / parent
  canonical URL、article↔video 互換。
- title 為空時輸出 `資料未提供` 純文字,不產生 label 為空的 link。

### Slack mrkdwn safety

title 進入 `<url|label>` 前一律走既有 `_mrkdwn_escape`,沒有第二套 escape:

- `&` `<` `>` → `&amp;` `&lt;` `&gt;`,因此 title 無法關掉這個 link construct、
  也無法開一個新的;
- C0/C1 控制字元、DEL、` ` / ` ` → 空白,因此 title 無法跳行去偽造下一個
  asset header、品牌 heading 或 more-results 提示;
- label 內的 `|` 無害:Slack 以第一個 `|` 分界;
- `*` `_` `~` `` ` `` 只在 label 內生效,formatter 自己的粗體只有 asset header 一處,
  且其收尾 `*` 落在行尾邊界,不受動態值影響;
- URL 端沿用既有 `escape_mrkdwn_url`,只處理 `&`,不改寫、不 decode approved URL。

## 5. Pagination Contract

- **Page size**:15 個品牌／夥伴(`slack_presentation.BRAND_PAGE_SIZE`)。
  15 計的是**品牌 group**,不是 asset 數。
- **Brand atomicity**:同一品牌的所有 asset 必定在同一頁。既有的
  「全域 10 筆 asset 上限截斷」已移除,不再有品牌被切一半的情形。
- **Ranking**:snapshot 的順序就是 `_presentation_entities` 的原始 rank order。
  第一頁 1–15、第二頁 16–30、依此類推;不重排、不 randomize、不二次查詢拼接。
- **Char budget**:`PAGE_CHAR_BUDGET = 12000`。Slack `chat.postMessage` 的 text 上限是
  40,000 字元,遠高於此。頁面逐品牌填充,下一個品牌會超過預算時提前收頁。
  單一品牌本身就超過預算時仍獨佔一頁完整輸出 —— **atomicity 優先於預算,絕不靜默截斷**。
- **Totals**:第一頁的總數行描述的是**這次搜尋取得的整份結果**,不是本頁數量 —— 但它是否
  等於「符合條件的全部結果」,取決於是否觸及 Slack 的顯示上限
  (`SLACK_SEARCH_PARENT_CAP`,見下節)。因此有兩種措辭:

  | 情況 | 措辭 |
  | --- | --- |
  | 品牌數 < 60 | `共找到 {n} 個品牌／夥伴、{m} 筆內容。` |
  | 品牌數 = 60(觸及上限) | `目前顯示最多 60 個品牌／夥伴，共 {m} 筆內容。` |

  **為什麼上限情況不能說「共找到 60」**:品牌數是在上限已經停止收錄新品牌**之後**才計算的,
  而系統不保留 pre-cap 總數,因此無從得知 60 究竟是完整結果還是被上限切齊的結果。
  「共找到 60」會把後者說成前者。措辭因此只陳述可證明的事:**目前顯示了多少**。

  同理,系統**不會**說「還有更多結果」——— 沒有 pre-cap 總數就無法確定這件事。
  這是 display-ceiling disclosure,不是 more-results claim。

- **More-results notice**(僅在還有剩餘品牌時出現,最後一頁不出現):

  ```text
  尚有 {n} 個品牌／夥伴未顯示。
  若要繼續查看，請在此討論串回覆「@Marketing Knowledge Agent 顯示更多」。
  ```

  提示中一定帶 mention。Bot 只訂閱 `app_mention`,thread 內沒有 mention 的一般訊息
  **不會進入 handler**;若提示只寫「顯示更多」,使用者照做會得到完全沒有回應的靜默失敗。

- **Ceiling notice**(僅在觸及顯示上限的結果的**最後一頁**出現,取代 more-results notice):

  ```text
  已顯示目前最多可提供的 60 個品牌／夥伴。
  若想查看更多可能結果，請縮小或調整搜尋條件後重新搜尋。
  ```

  它揭露上限,但不主張上限之外一定還有結果,並指向唯一能真正取得更多結果的動作。

- **Page 2+ header**:不重複 query condition 與總數,改為
  `繼續顯示搜尋結果（第 16–30 個品牌／夥伴）`。

### Display capacity vs ranking

Slack 端以 `SLACK_SEARCH_PARENT_CAP = 60`、`SLACK_SEARCH_ASSET_CAP = 240`
呼叫既有 `pipeline.agent_ask`,讓 renderer 拿得到分頁所需的完整 candidate set(4 頁 × 15)。

`SLACK_SEARCH_PARENT_CAP` 定義在 `slack_presentation`,因為 renderer 必須知道這個數字才能在
結果觸及上限時如實描述它;`slack_interface` 匯入後用來向 `agent_ask` 要這麼多資料。

這是 **display capacity,不是 ranking**:

- `structured_results.DEFAULT_PARENT_CAP` / `DEFAULT_ASSET_CAP` 維持 5 / 10,CLI 與其他
  呼叫端行為不變;
- `pipeline.search_index` 的 alias merge
  `merge_rank_and_cap_alias_results(..., parent_cap=5, asset_cap=10)` 是既有 frozen
  ranking contract,**本 sprint 未修改**。因此 exact-alias 查詢在 retrieval 層仍然是
  5 parents,即使 Slack 顯示容量提高;
- ranking algorithm、query parser、typed query semantics、governance gating、
  external intent gating、merchant filtering、candidate selection 全部未動。

asset cap 取 parent cap × 4(每筆 merchant record 最多 article / video / podcast / news
各一)是為了讓 asset 預算永遠不會在某個品牌中途用盡 —— 這正是 brand atomicity 的前提。

## 6. Pagination State Design

`slack_pagination.SlackPaginationStore`:process-local、in-memory。

| 項目 | 值 |
| --- | --- |
| Key | `(channel_id, thread_ts)` —— 只有回覆所需的技術 routing 座標 |
| 存的內容 | 第一頁之後、**已渲染完成**的頁面字串 + 下一頁 index + 到期時間 |
| TTL | 900 秒(15 分鐘);每次讀取續期 |
| Bound | 最多 200 筆,超過時淘汰最舊 |
| Clock | `time.monotonic()`,不受系統時間調整影響 |

**不存**:Slack user id / profile / email / display name、token、raw Slack event payload、
query 原文、query plan、citation、provenance、restricted source text、頻道歷史。

**不寫**:SQLite、JSON 檔、`.mka`、content index、Search Analytics、audit log、任何 telemetry。

### 行為定義

- 結果只有一頁 → 不存 state,並清掉該 thread 既有 continuation。
- 同一 thread 重新搜尋(含非結構化回答與拒答)→ **新的搜尋覆蓋舊的 continuation**。
  「顯示更多」永遠只會接續該 thread 最新一次搜尋。
- 最後一頁送出後 continuation 立刻移除,再回「顯示更多」即為失效。
- 找不到有效 context(過期、被覆蓋、bot 重啟、跨 channel、非同一 thread)→ 回覆
  `此搜尋工作階段已失效，請重新執行原搜尋。`
  不猜上一個 query、不掃 Slack 歷史重建、不重跑不確定的查詢、不讀其他 thread 的 state。
- Bot 重啟後所有 continuation 失效,這是可接受且預期的行為。

TTL 15 分鐘的理由:使用者瀏覽搜尋結果通常在數十秒到數分鐘內完成;超過此區間仍保留
「使用者可見結果文字」在記憶體並無收益,而重新搜尋的成本很低(離線、無外部 API)。
數值屬 engineering default,調整不需要 governance 決議。

### Snapshot 而非重新查詢

「顯示更多」**不重新執行搜尋**:第一次搜尋完成後即完成全部分頁渲染,後續頁面是同一份
ordered snapshot 的文字。理由是重新查詢可能因 index 更新、ranking 變化或資料變動造成
重複、漏項與頁序漂移。副作用是 state 只需存已治理完成的使用者可見文字,是最小資料量的選擇。

### 「顯示更多」的辨識

續頁請求**必須 mention bot**:正式指令是「@Marketing Knowledge Agent 顯示更多」。
production 只訂閱 `app_mention`,thread 內未 mention 的一般訊息根本不會送到 handler,
因此沒有 mention 的「顯示更多」不是「未被辨識」,而是**從未抵達**。使用者可見的提示
(上節 More-results notice)因此一律引用含 mention 的完整寫法。

抵達 handler 之後:訊息去除 app mention 與前後空白、去除結尾標點(`. 。 ! ！ ~ ～`)後,
**完全等於**「顯示更多」才視為續頁請求;其餘一律當普通查詢(例如「顯示更多品牌」「更多」仍是搜尋)。
續頁請求不呼叫 retrieval、不做治理判斷、不寫 audit —— 因為沒有任何新的查詢或揭露發生。
頻道白名單檢查仍在最前面,非白名單頻道連 continuation 都讀不到。

## 7. 與既有規格的關係

`docs/specs/S_SLACK_INTERFACE_SPEC.md` §7 明確不做「多輪對話記憶」。
本 sprint 的 thread-scoped 分頁**不是**對話記憶:它不保存 query、不保存使用者身分、
不跨 thread、不落檔、重啟即失效,只是把一次搜尋的輸出分成多則訊息送出。
這是 2026-08-19 Human UAT 的明確裁決,範圍僅限「同一次搜尋結果的續頁」。

## 8. Explicit Non-goals

本 sprint 不處理,發現問題只記 follow-up:

Search Analytics Foundation、query logging、usage statistics、UAT database、user tracking、
Content Gap、Asset Metadata Apply、Approved URL authority rebuild / redesign、
content index rebuild、Slack Block Kit redesign、retrieval ranking、query parser accuracy、
LLM、external API、embeddings、SEO、automatic content production、dashboard。

`/private/tmp/mka-search-analytics-foundation`(`codex/impl/search-analytics-foundation`)
是完全獨立的工作線,本 sprint 未讀取、未修改、未 cherry-pick,分頁 state 也不是
Search Analytics event。

## 9. Verification

全部離線 / fixture / unit / integration。未連 Slack、未啟動 production bot、未讀 token、
未改 `.mka/slack_config.json`、未改 primary worktree。

**Production activation 不包含在本 sprint,尚未進行,不得視為 production PASS。**
