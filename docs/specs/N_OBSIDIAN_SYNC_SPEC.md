# N. Obsidian Sync Spec(pipeline 最後一哩)

> 定位:`apply-review-decisions` 產出 approved vault preview 之後、經人工確認、把內容**首次寫入真實 Obsidian vault** 的步驟。
> 這是整條 pipeline 第一個「寫到 reports/ 以外」的操作,也是最危險的一步——vault 是使用者的筆記庫,寫錯、蓋錯、刪錯都直接傷害信任。
> 本 spec 與 hard constraint 7 的關係:constraint 7 禁止 apply「直接」sync;本 spec 就是被要求的「人工確認後另行執行」路徑的正式設計,不是繞過。
> 實作等級:standard-coding(規格完整);**但實作前必須先取得 §12 三個使用者裁決,缺答案不開工**。

---

## 0. 範圍

**只 sync 這些**(來自 apply preview 輸出):
- `approved_vault_preview/merchant_cases/*.md`
- `approved_vault_preview/public_metrics/*.md`
- `approved_vault_preview/_vault_only/*.md`(進 vault 但標記不進 content index)

**永不 sync**:
- `governance_table_preview/`(denylist 是檢索系統的設定,不是筆記)
- `internal_inventory_preview/`(pending metrics 盤點表留在 reports/)
- `excluded_records.md`、`not_reviewed_records.md`(報告,不是內容)
- 任何 `--include-clean-records` 產物,除非該旗標政策已由使用者正式核准

**明確不做**(另立 spec):formal SQLite content index build。sync 完成 ≠ 可檢索;index build 是下一個獨立步驟,有自己的 review。

## 1. 核心安全模型(五道防線)

1. **Namespace 隔離**:sync 只能讀寫 `<vault>/<namespace>/` 這一個子目錄(namespace 名稱見 §12)。vault 其他任何路徑(含 `.obsidian/`)一律唯讀不可碰。
2. **Managed-file marker**:每個 synced 檔的 frontmatter 帶 `managed_by: marketing-knowledge-agent`。namespace 內**沒有這個 marker 的檔案 = 使用者手動放的**,永不更新、永不搬移,列入 conflict 報告。
3. **Plan → Confirm → Execute 三階段**:預設只產 plan(唯讀 diff);execute 需要明確旗標 + plan 綁定(見 §3)。
4. **Archive 而非 delete**:任何「移除」都是搬到 `<namespace>/_archived/<batch_id>/`,vault 內永遠不發生 `rm`。
5. **備份 + rollback**:execute 寫入前,整個 namespace 先備份;任一步失敗即自動還原;事後可用 batch_id 手動 rollback。

## 2. CLI 設計

```text
mka sync-obsidian plan --apply-dir <dir> --vault <path> [--namespace <name>]
    唯讀。輸出 sync plan(md + json)到 reports/obsidian_sync/。

mka sync-obsidian execute --plan <plan.json> --vault <path> --confirm
    依已確認的 plan 執行。--confirm 缺席 → 只印 plan 摘要並退出(exit 1)。

mka sync-obsidian rollback --batch <batch_id> --vault <path>
    從備份還原該批次前的 namespace 狀態。
```

exit codes:0=成功;1=需要確認/plan 有 conflict 待裁決;2=前置檢查失敗或執行中止(已自動還原)。

## 3. Plan 階段(唯讀)

### 3.1 產出內容

對 apply preview 與 vault namespace 做 diff,分類每一個檔:

| 分類 | 條件 | execute 時的動作 |
| --- | --- | --- |
| `will_add` | preview 有、namespace 無 | 新增 |
| `will_update` | 兩邊都有(以 source_sheet+source_row 對應)、內容 checksum 不同、vault 版有 managed marker 且其 `content_checksum` 與 frontmatter 記錄一致(=上次 sync 後沒被人動過) | 覆蓋(先備份) |
| `will_archive` | namespace 有(managed)、本批 preview 無 | 搬到 `_archived/<batch_id>/` |
| `unchanged` | checksum 相同 | 不動 |
| `conflict_user_edited` | vault 版有 marker 但實際內容 checksum ≠ frontmatter 記錄的 checksum(=上次 sync 後被人手動改過) | **預設不動、列報告**(§12 裁決 c) |
| `conflict_unmanaged` | namespace 內同名檔但無 marker | 永不動、列報告 |

### 3.2 Plan 檔案

- `reports/obsidian_sync/sync_plan_<timestamp>.md`:人讀版——各分類計數 + 逐檔清單(檔名、對應 source row、動作)+ conflict 明細 + 醒目的「執行方式」說明。
- `reports/obsidian_sync/sync_plan_<timestamp>.json`:機讀版——含每檔 checksum、`plan_state_hash`(見 3.3)、`batch_id`。

### 3.3 Plan 綁定(TOCTOU 防護)

`plan_state_hash` = hash(apply preview 全部檔案 checksums + vault namespace 全部檔案 checksums)。execute 開頭**重算一次**:不一致 → 中止(exit 2),訊息「狀態已改變,請重新產生 plan 並重新確認」。這保證**人確認的就是實際執行的**——apply 重跑過、或有人動過 vault,舊確認即失效。

## 4. Execute 階段

### 4.1 前置檢查(任一失敗 → exit 2,不寫任何東西)

1. plan_state_hash 重算一致(§3.3)。
2. plan 中 `conflict_*` 數量 > 0 且未帶 `--allow-conflicts-skip`(該旗標=承認 conflicts 將被跳過並繼續其餘)→ 拒絕,要求先人工處理 conflicts。
3. apply preview 的 `apply_decisions_summary.md` 存在且其安全斷言記錄為通過。
4. **Denylist final gate**:對 plan 中每個 will_add / will_update 檔重跑 `GovernanceIndex.check_text`(用當前 restricted preview 自建 index),命中數必須 0——這是 K 白名單斷言之後的第二次獨立把關,因為 sync 可能發生在 apply 很久之後、denylist 可能已更新。
5. `<vault>/.obsidian/` 存在(確認目標真的是 Obsidian vault,防打錯路徑)。
6. namespace 路徑 resolve 後必須在 vault 下(`is_relative_to`,同 K 的 `_safe_output_path` 手法)。

### 4.2 執行順序(原子性)

1. 產生 `batch_id`(timestamp + 短隨機碼)。
2. 備份:`<namespace>/` 整目錄複製到 `reports/obsidian_sync/backup_<batch_id>/`(vault 外,免得備份本身污染 vault)。
3. 逐檔執行 will_add / will_update / will_archive;每個寫入都先寫 temp 檔再原子 rename;每檔 frontmatter 追加/更新:
   ```yaml
   managed_by: marketing-knowledge-agent
   sync_batch_id: <batch_id>
   synced_at: <ISO timestamp>
   content_checksum: <本檔內容 hash,供下次偵測人工編輯>
   ```
4. 寫 manifest:`reports/obsidian_sync/manifest_<batch_id>.json`——每檔的動作、路徑、前後 checksum。
5. **執行後自檢**(任一失敗 → 用備份整體還原,exit 2):
   - namespace 外零寫入:對照執行前記錄的「vault 非 namespace 區檔案清單+mtime」,必須無變化。
   - denylist 再掃一次 namespace 全部 managed 檔,零命中。
   - manifest 守恆:plan 的 will_* 計數 == manifest 實際動作計數。
6. Audit log:`reports/audit_log.csv` 追加一行(timestamp、batch_id、add/update/archive 計數、operator、plan 檔路徑)。

### 4.3 失敗處理

執行中任何 exception → 停止後續檔案、從 `backup_<batch_id>` 還原整個 namespace、manifest 標 `status: aborted_and_restored`、exit 2。**不留半完成狀態。**

## 5. Rollback

`rollback --batch <batch_id>`:用 `backup_<batch_id>` 還原 namespace 至該批執行前狀態,寫一筆 rollback manifest 與 audit log。備份目錄保留策略:至少保留最近 5 批或 90 天(先到者為準),清理是人工操作不自動做。

## 6. 測試 DoD(實作 sprint 的驗收)

- [ ] fixture:tmp 假 vault(含 `.obsidian/` 空殼 + namespace 外的「使用者筆記」檔)
- [ ] add / update / archive / unchanged 各至少一測試
- [ ] `conflict_user_edited`:synced 檔被改 checksum 後,plan 列 conflict、execute 預設不覆蓋
- [ ] `conflict_unmanaged`:namespace 內無 marker 檔,永不被動(內容與 mtime 不變)
- [ ] **namespace 外不可寫**:執行後「使用者筆記」檔完全未變(內容+mtime)
- [ ] plan 綁定:產 plan 後改動 apply preview 任一檔 → execute 拒絕
- [ ] denylist final gate:注入含 restricted 品牌的檔 → execute 整批拒絕(should-fail 測試)
- [ ] 中途失敗還原:monkeypatch 讓第 N 個檔寫入拋錯 → namespace 恢復原狀、manifest=aborted
- [ ] rollback 測試:execute 成功後 rollback,namespace 回到執行前
- [ ] `--confirm` 缺席 → 不寫任何東西
- [ ] 全套 pytest 全綠;既有測試斷言不得修改

## 7. 明確不做(本 spec)

- 不建 formal content index(另一個 spec)
- 不做雙向 sync(vault → 系統的反向流不存在;人工編輯用 conflict 機制處理)
- 不動 `.obsidian/` 任何設定檔
- 不自動清理備份
- 不 sync 到 namespace 以外的任何 vault 位置

## 8. 待使用者裁決(實作前必答,答案回填本節)

| # | 問題 | 觀察/建議 | 裁決 |
| --- | --- | --- | --- |
| a | **真實 vault 路徑是哪裡?** | 觀察:repo 根目錄本身有 `.obsidian/`——若 repo 就是 vault,namespace 會落在 repo 內(且必須加進 .gitignore);若正式公司 vault 在別處,請給路徑。這兩種情況的風險面不同,不能猜 | ＿＿＿ |
| b | **namespace 目錄名?** | 建議 `MKA/`(短、明確、不易與現有筆記撞名) | ＿＿＿ |
| c | **synced 檔被人工編輯後,下次 sync 的預設行為?** | 建議「保留人工版 + conflict 報告」(本 spec 預設);另一選項是「覆蓋,人工編輯應發生在 Excel 源頭」——更一致但會吃掉筆記 | ＿＿＿ |

---

*規格作者:Fable 5(2026-07-10)。依 F 協議,本 spec 的安全模型(§1 五道防線)屬層級二——修改前必須問使用者。*
